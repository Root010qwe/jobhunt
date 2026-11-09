"""Бот: обходит hh.ru и карьерные сайты компаний, отбирает свежие вакансии под профиль."""
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import quote, urljoin

from playwright.async_api import async_playwright

from . import db
from .brands import brand

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


META_TAIL = re.compile(r"\s+(Полная|Частичная|Проектная|Стажировка|Удал[её]нн|Гибрид|Офис|Москва|Санкт-Петербург|Без опыта|От \d|Нет опыта)\b.*$")


def clean_title(title):
    t = META_TAIL.sub("", title).strip()
    return t if len(t) >= 6 else title


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
        db.run("INSERT INTO vacancies(source,company,brand,title,url,meta,score,watch,state,found_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (v["source"], v["company"], brand(v["company"]), v["title"], url, v.get("meta", ""), v["score"], v.get("watch", 0), "new", ts, ts))
        new += 1
    mark_applied()
    return new


def fill_brands():
    """Проставить название компании старым записям (и пересчитать после правок в brands.py)."""
    for v in db.q("SELECT id, company, brand FROM vacancies"):
        b = brand(v["company"])
        if b != v["brand"]:
            db.run("UPDATE vacancies SET brand=? WHERE id=?", (b, v["id"]))


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
            it["title"] = clean_title(it["title"])
            s = score(it["title"], it["meta"], kw)
            if s >= 0:
                res.append({**it, "source": comp["name"], "company": comp["name"], "score": s + 5, "watch": 1})
    log.append(f"{comp['name']}: подходящих {len(res)}")
    return res


async def crawl_hh(ctx, cfg, kw, watch, sem, log):
    res = []

    async def one(qtext, exp, area, pg, employer=False):
        # employer=True: все вакансии работодателя (поиск по названию компании), роль отберёт фильтр
        field = "&search_field=company_name" if employer else ""
        url = (f"https://hh.ru/search/vacancy?text={quote(qtext)}{field}&area={area}&experience={exp}"
               f"&order_by=publication_time&search_period=30&items_on_page=100&page={pg}")
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
            w = int(employer or any(x.lower() in it["company"].lower() for x in watch))
            parts = []
            for x in [exp_label] + re.split(r"\s*·\s*|\n", it["meta"]):
                x = re.sub(r"^Опыт\s+", "", x.strip())
                if x and x.lower() not in (y.lower() for y in parts):
                    parts.append(x)
            res.append({**it, "meta": " · ".join(parts), "source": "hh.ru",
                        "score": s + (8 if w else 0) + (3 if exp == "noExperience" else 0), "watch": w})

    jobs = [one(qt, exp, area, pg) for qt in cfg["queries"] for exp in cfg["experience"]
            for area in cfg["area"] for pg in range(cfg.get("pages", 1))]
    jobs += [one(name, exp, area, 0, employer=True) for name in cfg.get("employers", [])
             for exp in cfg["experience"] for area in cfg["area"]]
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
        sem = asyncio.Semaphore(6)
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


DESC_JS = r"""
() => {
  // основной блок описания: самый «текстовый» из кандидатов, без меню, подвала и форм
  const sel = '[data-qa="vacancy-description"], [class*="vacancy-description" i], [class*="description" i], article, main, [class*="vacancy" i], [class*="content" i]';
  let best = null, bestLen = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (el.closest('header, footer, nav, form')) continue;
    const t = (el.innerText || '').trim();
    const len = t.length;
    if (len > 300 && len < 40000 && (len > bestLen * 1.0) && (!best || !el.contains(best) || len < bestLen * 1.6)) { best = el; bestLen = len; }
  }
  const hh = document.querySelector('[data-qa="vacancy-description"]');
  if (hh) { best = hh; }
  const el = best || document.body;
  // страница одноразовая: прячем служебное прямо в ней, чтобы innerText сохранил переносы строк
  el.querySelectorAll('script, style, nav, footer, header, form, button, svg, iframe, [class*="breadcrumb" i]').forEach(n => { n.style.display = 'none'; });
  const h1 = document.querySelector('h1');
  const text = (el.innerText || '').split('\n').map(s => s.replace(/\s+/g, ' ').trim()).filter((s, i, a) => s || (a[i - 1] && a[i - 1].trim()))
    .join('\n').replace(/\n{3,}/g, '\n\n').trim();
  return { title: h1 ? h1.innerText.trim() : document.title, text };
}
"""


async def fetch_vacancy(url, limit=15000):
    """Заголовок и текст вакансии — для карточки в дашборде и для адаптации резюме."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(locale="ru-RU", user_agent=UA, ignore_https_errors=True)
        page = await ctx.new_page()
        try:
            await page.goto(url, timeout=45000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)
            d = await page.evaluate(DESC_JS)
        finally:
            await browser.close()
    d["text"] = d["text"][:limit]
    return d


async def fetch_text(url, limit=12000):
    return (await fetch_vacancy(url, limit))["text"]


if __name__ == "__main__":
    import sys
    db.init()
    print(json.dumps(asyncio.run(crawl(sys.argv[1:] or None)), ensure_ascii=False, indent=1))
