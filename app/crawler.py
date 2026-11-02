"""Бот: обходит hh.ru и карьерные сайты компаний, отбирает свежие вакансии под профиль."""
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import quote, urljoin

from playwright.async_api import async_playwright

from . import db

SOURCES = Path(__file__).with_name("sources.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")

# Ссылки на вакансии + заголовок из карточки
EXTRACT_JS = r"""
(pattern) => {
  const re = new RegExp(pattern, 'i');
  const BTN = /^(откликнуться|подробнее|узнать больше|смотреть|перейти|apply|more|details)$/i;
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.href.split('#')[0];
    if (!re.test(href) || seen.has(href)) continue;
    // самый длинный «заголовочный» элемент внутри карточки (город тоже бывает в [class*=name])
    let pick = null, best = '';
    for (const el of a.querySelectorAll('h1,h2,h3,h4,h5,[class*="title" i],[class*="name" i],[class*="heading" i]')) {
      const tx = (el.innerText || '').trim();
      if (tx.length > best.length && tx.length < 160) { best = tx; pick = el; }
    }
    let title = (pick ? best : a.innerText || a.getAttribute('title') || '').trim().replace(/\s+/g, ' ');
    let meta = (a.innerText || '').trim().replace(/\s+/g, ' ');
    if (!pick) {
      const card = a.closest('li,article,[class*="card" i],[class*="item" i]');
      if (card && card !== a) meta = card.innerText.trim().replace(/\s+/g, ' ');
    }
    if (title.length < 4 || BTN.test(title)) {  // кнопка или пустая обёртка: ищем заголовок выше по дереву
      let el = a.parentElement;
      for (let i = 0; el && i < 6; i++, el = el.parentElement) {
        const tx = [...el.querySelectorAll('h1,h2,h3,h4,h5,[class*="title" i],[class*="name" i],[class*="heading" i]')]
          .map(h => (h.innerText || '').trim()).find(s => s.length > 4 && s.length < 160 && !BTN.test(s) && !/^(г\.|\d)/.test(s));
        if (tx) { title = tx.replace(/\s+/g, ' '); meta = el.innerText.trim().replace(/\s+/g, ' '); break; }
      }
    }
    title = title.replace(/\s*\(opens in a new tab\).*$/i, '');
    if (title.length < 4 || BTN.test(title) || /^(все вакансии|вакансии)$/i.test(title)) continue;
    seen.add(href);
    out.push({url: href, title: title.slice(0, 200), meta: meta.slice(0, 400)});
  }
  return out;
}
"""

HH_JS = r"""
() => [...document.querySelectorAll('[data-qa="vacancy-serp__vacancy"], [data-qa^="vacancy-serp__vacancy "]')].map(card => {
  const t = card.querySelector('[data-qa="serp-item__title"]');
  const e = card.querySelector('[data-qa="vacancy-serp__vacancy-employer"], [data-qa="vacancy-serp__vacancy-employer-text"]');
  const addr = card.querySelector('[data-qa="vacancy-serp__vacancy-address"]');
  const exp = card.querySelector('[data-qa^="vacancy-serp__vacancy-work-experience"]');
  const pay = card.querySelector('[class*="compensation"]');
  return t ? {url: t.href.split('?')[0], title: t.innerText.trim(), company: e ? e.innerText.trim().replace(/\s+/g,' ') : '',
             meta: [exp && exp.innerText, pay && pay.innerText, addr && addr.innerText].filter(Boolean).join(' · ')} : null;
}).filter(Boolean)
"""


def load_sources():
    return json.loads(SOURCES.read_text(encoding="utf-8"))


def _has(text, word):
    w = word.lower()
    if len(w) <= 3:  # короткие токены (ai, ии, pm, qa) — только целым словом
        return re.search(rf"(?<![a-zа-яё]){re.escape(w)}(?![a-zа-яё])", text) is not None
    return w in text


def score(title, meta, kw):
    t = title.lower()
    m = (meta or "").lower()
    if any(_has(t, w) for w in kw["exclude"]):
        return -1
    if not any(_has(t, w) for w in kw["include"]):
        return -1
    if re.search(r"([3-9] ?[–-] ?\d+ ?(лет|года)|более 6|от 3 (до 6 )?(лет|года)|от 5|от 6|3\+ ?(лет|years)|5\+ ?(лет|years)|[3-9]\+ years)", t + " " + m):
        return -1
    s = 10 + sum(5 for w in kw["boost"] if _has(t, w))
    if re.search(r"без опыта|не требуется|1[–-]3 ?год|стаж", m):
        s += 5
    return s


def norm_url(url):
    return url.split("#")[0].rstrip("/")


def save(found, log):
    new = 0
    ts = db.now()
    for v in found:
        url = norm_url(v["url"])
        ex = db.one("SELECT id FROM vacancies WHERE url=? OR (company=? AND title=?)", (url, v["company"], v["title"]))
        if ex:
            db.run("UPDATE vacancies SET last_seen=? WHERE id=?", (ts, ex["id"]))
            continue
        db.run("INSERT INTO vacancies(source,company,title,url,meta,score,watch,state,found_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (v["source"], v["company"], v["title"], url, v.get("meta", ""), v["score"], v.get("watch", 0), "new", ts, ts))
        new += 1
    mark_applied()
    return new


def mark_applied():
    """Вакансии, на которые уже есть отклик в трекере, не показываем как новые."""
    links = {norm_url(a["link"]): a["id"] for a in db.q("SELECT id, link FROM applications WHERE link!=''")}
    hh_ids = {m.group(1) for l in links for m in [re.search(r"hh\.ru/vacancy/(\d+)", l)] if m}
    for v in db.q("SELECT id, url FROM vacancies WHERE state IN ('new','saved')"):
        u = norm_url(v["url"])
        m = re.search(r"hh\.ru/vacancy/(\d+)", u)
        if u in links or (m and m.group(1) in hh_ids):
            db.run("UPDATE vacancies SET state='applied' WHERE id=?", (v["id"],))


async def crawl_company(ctx, comp, kw, sem, log):
    urls = [comp["url"]] + comp.get("extra", [])
    res = []
    for u in urls:
        async with sem:
            page = await ctx.new_page()
            try:
                await page.goto(u, timeout=40000, wait_until="domcontentloaded")
                await page.wait_for_timeout(4000)
                # подгрузить ленивые списки
                for _ in range(3):
                    await page.mouse.wheel(0, 4000)
                    await page.wait_for_timeout(700)
                items = await page.evaluate(EXTRACT_JS, comp["pattern"])
            except Exception as e:  # noqa: BLE001
                log.append(f"{comp['name']}: ошибка {str(e).splitlines()[0][:120]}")
                items = []
            finally:
                await page.close()
        for it in items:
            s = score(it["title"], it["meta"], kw)
            if s >= 0:
                res.append({**it, "source": comp["name"], "company": comp["name"], "score": s + 5, "watch": 1})
    log.append(f"{comp['name']}: подходящих {len(res)}")
    return res


async def crawl_hh(ctx, cfg, kw, watch, sem, log):
    res = []

    async def one(qtext, exp, area, pg):
        url = (f"https://hh.ru/search/vacancy?text={quote(qtext)}&area={area}&experience={exp}"
               f"&order_by=publication_time&search_period=30&items_on_page=50&page={pg}")
        async with sem:
            page = await ctx.new_page()
            try:
                await page.goto(url, timeout=40000, wait_until="domcontentloaded")
                await page.wait_for_timeout(2500)
                items = await page.evaluate(HH_JS)
            except Exception as e:  # noqa: BLE001
                log.append(f"hh «{qtext}»: ошибка {str(e).splitlines()[0][:100]}")
                items = []
            finally:
                await page.close()
        exp_label = "без опыта" if exp == "noExperience" else "1–3 года"
        for it in items:
            s = score(it["title"], it["meta"], kw)
            if s < 0:
                continue
            w = int(any(x.lower() in it["company"].lower() for x in watch))
            parts = []
            for x in [exp_label] + re.split(r"\s*·\s*|\n", it["meta"]):
                x = re.sub(r"^Опыт\s+", "", x.strip())
                if x and x.lower() not in (y.lower() for y in parts):
                    parts.append(x)
            res.append({**it, "meta": " · ".join(parts), "source": "hh.ru",
                        "score": s + (8 if w else 0) + (3 if exp == "noExperience" else 0), "watch": w})

    jobs = [one(qt, exp, area, pg) for qt in cfg["queries"] for exp in cfg["experience"]
            for area in cfg["area"] for pg in range(cfg.get("pages", 1))]
    await asyncio.gather(*jobs)
    uniq = {}
    for r in res:
        uniq.setdefault(norm_url(r["url"]), r)
    log.append(f"hh.ru: подходящих {len(uniq)}")
    return list(uniq.values())


async def crawl(only=None):
    """only: список названий компаний (или 'hh.ru') — обойти только их."""
    src = load_sources()
    kw = src["keywords"]
    log = []
    run_id = db.run("INSERT INTO runs(started_at) VALUES(?)", (db.now(),))
    found = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(locale="ru-RU", user_agent=UA, ignore_https_errors=True,
                                        viewport={"width": 1366, "height": 900})
        sem = asyncio.Semaphore(4)
        tasks = [crawl_company(ctx, c, kw, sem, log) for c in src["companies"] if not only or c["name"] in only]
        if src["hh"]["enabled"] and (not only or "hh.ru" in only):
            tasks.append(crawl_hh(ctx, src["hh"], kw, src.get("watchlist_hh", []), sem, log))
        for r in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(r, Exception):
                log.append(f"сбой: {r}")
            else:
                found.extend(r)
        await browser.close()
    new = save(found, log)
    db.run("UPDATE runs SET finished_at=?, found=?, new=?, log=? WHERE id=?",
           (db.now(), len(found), new, "\n".join(sorted(log)), run_id))
    return {"found": len(found), "new": new, "log": sorted(log)}


async def fetch_text(url, limit=12000):
    """Текст страницы вакансии — для адаптации резюме и письма."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(locale="ru-RU", user_agent=UA, ignore_https_errors=True)
        page = await ctx.new_page()
        try:
            await page.goto(url, timeout=40000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            sel = '[data-qa="vacancy-description"]' if "hh.ru" in url else "main"
            el = await page.query_selector(sel)
            text = await (el.inner_text() if el else page.inner_text("body"))
        finally:
            await browser.close()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:limit]


if __name__ == "__main__":
    import sys
    db.init()
    print(json.dumps(asyncio.run(crawl(sys.argv[1:] or None)), ensure_ascii=False, indent=1))
