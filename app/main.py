"""JobHunt: трекер откликов + резюме + бот вакансий. Запуск: ./run.sh → http://localhost:8765"""
import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fastapi.responses import HTMLResponse

from . import ai, crawler, db, resume_model as rm, resumes, seed

logging.getLogger("pypdf").setLevel(logging.ERROR)
WEB = db.ROOT / "web"
STATUSES = ["ready", "applied", "screening", "interview", "test", "waiting", "offer", "rejected", "skipped"]
APP_FIELDS = ["company", "role", "track", "status", "link", "resume", "resume_id", "cover", "cover_text", "test",
              "prep_date", "applied_date", "next", "next_at", "notes", "vacancy_id"]

app = FastAPI(title="JobHunt")
# расширение браузера ходит с любых сайтов вакансий — API только на localhost
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

crawl_state = {"running": False, "last": None, "error": None}


def today():
    return date.today().isoformat()


def app_row(a):
    a = dict(a)
    a["history"] = json.loads(a.get("history") or "[]")
    return a


def get_app(aid):
    a = db.one("SELECT * FROM applications WHERE id=?", (aid,))
    if not a:
        raise HTTPException(404, "Отклик не найден")
    return app_row(a)


# ---------- отклики ----------
@app.get("/api/apps")
def list_apps():
    return [app_row(a) for a in db.q("SELECT * FROM applications ORDER BY COALESCE(applied_date, prep_date) DESC, id DESC")]


@app.post("/api/apps")
async def create_app(req: Request):
    d = await req.json()
    if not d.get("company") or not d.get("role"):
        raise HTTPException(400, "Нужны компания и вакансия")
    st = d.get("status") if d.get("status") in STATUSES else "ready"
    hist = [{"s": st, "at": today()}]
    vals = {k: d.get(k) for k in APP_FIELDS if k in d}
    vals.update(status=st, history=json.dumps(hist), created_at=db.now(), updated_at=db.now())
    if st not in ("ready", "skipped") and not vals.get("applied_date"):
        vals["applied_date"] = today()
    cols = ",".join(vals)
    aid = db.run(f"INSERT INTO applications({cols}) VALUES({','.join('?' * len(vals))})", tuple(vals.values()))
    return get_app(aid)


@app.patch("/api/apps/{aid}")
async def patch_app(aid: int, req: Request):
    d = await req.json()
    a = get_app(aid)
    vals = {k: d[k] for k in APP_FIELDS if k in d}
    if "status" in vals and vals["status"] != a["status"]:
        if vals["status"] not in STATUSES:
            raise HTTPException(400, "Неизвестный статус")
        a["history"].append({"s": vals["status"], "at": today()})
        vals["history"] = json.dumps(a["history"], ensure_ascii=False)
        if vals["status"] not in ("ready", "skipped") and not a["applied_date"] and "applied_date" not in d:
            vals["applied_date"] = today()
        if vals["status"] != "ready":
            vals["queue_pos"] = 0
        if a.get("vacancy_id") and vals["status"] != "ready":
            db.run("UPDATE vacancies SET state='applied' WHERE id=?", (a["vacancy_id"],))
    if not vals:
        return a
    vals["updated_at"] = db.now()
    db.run(f"UPDATE applications SET {','.join(k + '=?' for k in vals)} WHERE id=?", (*vals.values(), aid))
    return get_app(aid)


@app.delete("/api/apps/{aid}")
def delete_app(aid: int):
    db.run("DELETE FROM applications WHERE id=?", (aid,))
    return {"ok": True}


# ---------- вакансии от бота ----------
@app.get("/api/vacancies")
def list_vacancies(state: str = "new", q: str = "", watch: int = 0, source: str = "", company: str = "", days: int = 0):
    sql = "SELECT * FROM vacancies WHERE 1=1"
    args = []
    if state != "all":
        sql += " AND state=?"
        args.append(state)
    if watch:
        sql += " AND watch=1"
    if source:
        sql += " AND source=?"
        args.append(source)
    if company:
        sql += " AND brand=?"
        args.append(company)
    if days:
        sql += " AND found_at>=?"
        args.append((datetime.now() - timedelta(days=days)).isoformat())
    if q:
        sql += " AND (ulower(title) LIKE ? OR ulower(company) LIKE ? OR ulower(brand) LIKE ? OR ulower(meta) LIKE ?)"
        args += [f"%{q.lower()}%"] * 4
    sql += " ORDER BY substr(found_at,1,10) DESC, score DESC, id DESC LIMIT 600"
    return db.q(sql, args)


@app.get("/api/vacancies/stats")
def vacancy_stats():
    return {
        "states": {r["state"]: r["n"] for r in db.q("SELECT state, count(*) n FROM vacancies GROUP BY state")},
        "sources": db.q("SELECT source, count(*) n FROM vacancies WHERE state='new' GROUP BY source ORDER BY n DESC"),
        "companies": db.q("SELECT brand, count(*) n FROM vacancies WHERE state IN ('new','saved') AND brand!='' GROUP BY brand ORDER BY n DESC, brand"),
        "today": db.one("SELECT count(*) n FROM vacancies WHERE found_at>=?", (today(),))["n"],
        "last_run": db.one("SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"),
        "running": crawl_state["running"],
    }


@app.patch("/api/vacancies/{vid}")
async def patch_vacancy(vid: int, req: Request):
    d = await req.json()
    if d.get("state") in ("new", "seen", "hidden", "saved", "applied"):
        db.run("UPDATE vacancies SET state=? WHERE id=?", (d["state"], vid))
    return db.one("SELECT * FROM vacancies WHERE id=?", (vid,))


@app.post("/api/vacancies/{vid}/apply")
async def apply_vacancy(vid: int, req: Request):
    """Одна кнопка: заводит отклик «Готово к подаче» с основным резюме и письмом по шаблону."""
    v = db.one("SELECT * FROM vacancies WHERE id=?", (vid,))
    if not v:
        raise HTTPException(404, "Вакансия не найдена")
    ex = db.one("SELECT id FROM applications WHERE vacancy_id=? OR link=?", (vid, v["url"]))
    if ex:
        return get_app(ex["id"])
    prof = db.kv_get("profile", {})
    main = resumes.main_resume()
    track = "Стажировка" if re.search(r"стаж|intern|trainee", v["title"], re.I) else "Работа"
    aid = db.run("""INSERT INTO applications(company,role,track,status,link,resume,resume_id,cover,cover_text,prep_date,history,vacancy_id,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (v["company"], v["title"], track, "ready", v["url"], main["name"] if main else "", main["id"] if main else None,
                  1, ai.template_letter(prof, v["company"], v["title"]), today(),
                  json.dumps([{"s": "ready", "at": today()}]), vid, db.now(), db.now()))
    db.run("UPDATE vacancies SET state='saved' WHERE id=?", (vid,))
    return get_app(aid)


def _new_app_from_vacancy(v, prof, main):
    ex = db.one("SELECT id FROM applications WHERE vacancy_id=? OR link=?", (v["id"], v["url"]))
    if ex:
        return ex["id"]
    track = "Стажировка" if re.search(r"стаж|intern|trainee", v["title"], re.I) else "Работа"
    aid = db.run("""INSERT INTO applications(company,role,track,status,link,resume,resume_id,cover,cover_text,prep_date,history,vacancy_id,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (v["company"], v["title"], track, "ready", v["url"], main["name"] if main else "", main["id"] if main else None,
                  1, ai.template_letter(prof, v["company"], v["title"]), today(),
                  json.dumps([{"s": "ready", "at": today()}]), v["id"], db.now(), db.now()))
    db.run("UPDATE vacancies SET state='saved' WHERE id=?", (v["id"],))
    return aid


bulk_state = {"running": False, "done": 0, "total": 0, "error": None}


async def tailor_many(ids):
    """Массовый отклик с ИИ: под каждую вакансию — своё резюме и письмо, одобряются автоматически."""
    bulk_state.update(running=True, done=0, total=len(ids), error=None)
    for aid in ids:
        try:
            a = get_app(aid)
            if a["status"] == "ready" and a.get("vacancy_id"):
                dr = await make_draft(a["vacancy_id"])
                await approve_draft_core(dr["id"])
        except Exception as e:  # noqa: BLE001
            bulk_state["error"] = str(e)[:200]
        bulk_state["done"] += 1
    bulk_state["running"] = False


@app.post("/api/vacancies/bulk-apply")
async def bulk_apply(req: Request):
    """Массовый отклик: заводит отклики по выбранным вакансиям и ставит их в очередь."""
    d = await req.json()
    ids = [int(x) for x in d.get("ids", [])][:200]
    if not ids:
        raise HTTPException(400, "Не выбрано ни одной вакансии")
    prof = db.kv_get("profile", {})
    main = resumes.main_resume()
    last = db.one("SELECT COALESCE(MAX(queue_pos),0) m FROM applications")["m"]
    created = []
    for vid in ids:
        v = db.one("SELECT * FROM vacancies WHERE id=?", (vid,))
        if not v or v["state"] == "applied":
            continue
        aid = _new_app_from_vacancy(v, prof, main)
        a = get_app(aid)
        if a["status"] != "ready":
            continue
        last += 1
        db.run("UPDATE applications SET queue_pos=? WHERE id=?", (last, aid))
        created.append(aid)
    if d.get("tailor") and created and not bulk_state["running"]:
        asyncio.create_task(tailor_many(created))
    return {"queued": len(created), "first": queue_next(0)}


def queue_list():
    return db.q("SELECT id, company, role, link, queue_pos FROM applications WHERE queue_pos>0 AND status='ready' AND link!='' ORDER BY queue_pos")


def queue_next(after_id):
    rows = queue_list()
    if after_id:
        cur = db.one("SELECT queue_pos FROM applications WHERE id=?", (after_id,))
        pos = cur["queue_pos"] if cur else 0
        rows = [r for r in rows if r["queue_pos"] > pos and r["id"] != after_id]
    return rows[0] if rows else None


@app.get("/api/queue")
def get_queue():
    return {"items": queue_list(), "tailoring": bulk_state}


@app.get("/api/queue/next")
def get_queue_next(after: int = 0):
    return {"next": queue_next(after), "left": len(queue_list())}


@app.post("/api/queue/{aid}/skip")
def queue_skip(aid: int):
    db.run("UPDATE applications SET queue_pos=0 WHERE id=?", (aid,))
    return {"next": queue_next(aid), "left": len(queue_list())}


@app.delete("/api/queue")
def queue_clear():
    db.run("UPDATE applications SET queue_pos=0 WHERE queue_pos>0")
    return {"ok": True}


async def run_crawl(only=None):
    if crawl_state["running"]:
        return
    crawl_state.update(running=True, error=None)
    try:
        crawl_state["last"] = await crawler.crawl(only)
    except Exception as e:  # noqa: BLE001
        crawl_state["error"] = str(e)
    finally:
        crawl_state["running"] = False


@app.post("/api/crawl")
async def start_crawl(req: Request):
    d = await req.json() if (await req.body()) else {}
    if crawl_state["running"]:
        return {"running": True}
    asyncio.create_task(run_crawl(d.get("only")))
    return {"running": True}


@app.get("/api/crawl")
def crawl_status():
    return crawl_state


@app.get("/api/sources")
def get_sources():
    return crawler.load_sources()


@app.put("/api/sources")
async def put_sources(req: Request):
    d = await req.json()
    for c in d.get("companies", []):
        re.compile(c["pattern"])  # проверка регулярок до записи
    crawler.SOURCES.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True}


# ---------- резюме ----------
@app.get("/api/resumes")
def list_resumes():
    rows = db.q("SELECT id,name,kind,file,vacancy_id,is_main,created_at,length(text) chars FROM resumes ORDER BY is_main DESC, kind, id DESC")
    used = {r["resume_id"]: r["n"] for r in db.q("SELECT resume_id, count(*) n FROM applications GROUP BY resume_id")}
    for r in rows:
        r["used"] = used.get(r["id"], 0)
    return rows


@app.get("/api/resumes/{rid}")
def get_resume(rid: int):
    r = db.one("SELECT * FROM resumes WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404)
    return r


@app.patch("/api/resumes/{rid}")
async def patch_resume(rid: int, req: Request):
    d = await req.json()
    if d.get("is_main"):
        db.run("UPDATE resumes SET is_main=0")
        db.run("UPDATE resumes SET is_main=1, kind='base' WHERE id=?", (rid,))
    for k in ("name", "text", "kind"):
        if k in d:
            db.run(f"UPDATE resumes SET {k}=? WHERE id=?", (d[k], rid))
    return get_resume(rid)


@app.delete("/api/resumes/{rid}")
def delete_resume(rid: int):
    db.run("DELETE FROM resumes WHERE id=?", (rid,))
    return {"ok": True}


@app.post("/api/resumes/upload")
async def upload_resume(file: UploadFile = File(...)):
    name = Path(file.filename).name
    if Path(name).suffix.lower() not in (".pdf", ".docx", ".txt", ".md"):
        raise HTTPException(400, "Нужен PDF, DOCX или TXT")
    tmp = db.DATA / "upload_tmp" / name
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_bytes(await file.read())
    rid = resumes.add_file(tmp, kind="base")
    tmp.unlink(missing_ok=True)
    return get_resume(rid)


@app.get("/api/resumes/{rid}/file")
def resume_file(rid: int):
    r = get_resume(rid)
    p = resumes.DIR / r["file"]
    if not r["file"] or not p.exists():
        raise HTTPException(404, "Файла нет")
    return FileResponse(p, filename=p.name)


# ---------- ИИ: адаптация под вакансию ----------
async def vacancy_text(a):
    if a.get("vacancy_id"):
        v = await ensure_description(a["vacancy_id"])
        if v["description"]:
            return v["description"]
    if not a.get("link"):
        return f"{a['role']} в {a['company']}"
    return await crawler.fetch_text(a["link"])


# ---------- карточка вакансии ----------
async def ensure_description(vid, refresh=False):
    v = db.one("SELECT * FROM vacancies WHERE id=?", (vid,))
    if not v:
        raise HTTPException(404, "Вакансия не найдена")
    if (refresh or not v["description"]) and v["url"].startswith("http"):
        try:
            d = await crawler.fetch_vacancy(v["url"])
            db.run("UPDATE vacancies SET description=? WHERE id=?", (d["text"], vid))
            v["description"] = d["text"]
        except Exception as e:  # noqa: BLE001
            v["description"] = ""
            v["fetch_error"] = str(e).splitlines()[0][:200]
    return v


def draft_row(d):
    d = dict(d)
    d["resume"] = json.loads(d["resume"] or "{}")
    d["match"] = json.loads(d["match"] or "[]")
    return d


@app.get("/api/vacancies/{vid}/detail")
async def vacancy_detail(vid: int, refresh: int = 0):
    v = await ensure_description(vid, bool(refresh))
    drafts = [draft_row(d) for d in db.q("SELECT * FROM drafts WHERE vacancy_id=? ORDER BY version DESC", (vid,))]
    app_ = db.one("SELECT * FROM applications WHERE vacancy_id=? OR link=?", (vid, v["url"]))
    v["analysis"] = json.loads(v.get("analysis") or "[]")
    v["answers"] = json.loads(v.get("answers") or "{}")
    return {"vacancy": v, "drafts": drafts, "app": app_row(app_) if app_ else None}


@app.post("/api/apps/{aid}/vacancy")
def app_vacancy(aid: int):
    """Отклик из трекера → карточка вакансии (заводит запись вакансии, если её не было)."""
    a = get_app(aid)
    if a.get("vacancy_id") and db.one("SELECT id FROM vacancies WHERE id=?", (a["vacancy_id"],)):
        return {"vacancy_id": a["vacancy_id"]}
    if not a.get("link"):
        raise HTTPException(400, "У отклика нет ссылки на вакансию")
    v = db.one("SELECT id FROM vacancies WHERE url=?", (crawler.norm_url(a["link"]),))
    vid = v["id"] if v else db.run(
        "INSERT INTO vacancies(source,company,title,url,score,watch,state,found_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?)",
        ("трекер", a["company"], a["role"], crawler.norm_url(a["link"]), 0, 1,
         "applied" if a["status"] != "ready" else "saved", db.now(), db.now()))
    db.run("UPDATE applications SET vacancy_id=? WHERE id=?", (vid, aid))
    return {"vacancy_id": vid}


def master():
    m = db.kv_get("master_resume")
    if not m:
        raise HTTPException(400, "Сначала соберите мастер-резюме во вкладке «Резюме»")
    return m


def known_facts():
    return db.kv_get("facts", []) or []


def remember_facts(new):
    facts = known_facts()
    for f in new:
        if f and f not in facts:
            facts.append(f)
    db.kv_set("facts", facts[-60:])


@app.post("/api/vacancies/{vid}/analyze")
async def analyze_vacancy(vid: int):
    """Шаг 1: требования вакансии и вопросы кандидату по пробелам."""
    v = await ensure_description(vid)
    if not v["description"]:
        raise HTTPException(502, "Не удалось прочитать текст вакансии")
    try:
        out = await asyncio.to_thread(ai.analyze, master(), v["description"], v["company"], v["title"], known_facts())
    except (RuntimeError, ValueError) as e:
        raise HTTPException(502, str(e))
    reqs = out.get("requirements") or []
    db.run("UPDATE vacancies SET analysis=? WHERE id=?", (json.dumps(reqs, ensure_ascii=False), vid))
    return {"requirements": reqs, "answers": json.loads(v.get("answers") or "{}")}


@app.get("/api/facts")
def get_facts():
    return {"facts": known_facts()}


@app.put("/api/facts")
async def put_facts(req: Request):
    d = await req.json()
    db.kv_set("facts", [f.strip() for f in d.get("facts", []) if f.strip()])
    return {"facts": known_facts()}


@app.post("/api/apps/{aid}/reopen")
def reopen_app(aid: int):
    """Отклик отмечен «Подано» по ошибке — вернуть в «Готово к подаче»."""
    a = get_app(aid)
    a["history"].append({"s": "ready", "at": today()})
    db.run("UPDATE applications SET status='ready', applied_date='', history=?, updated_at=? WHERE id=?",
           (json.dumps(a["history"], ensure_ascii=False), db.now(), aid))
    if a.get("vacancy_id"):
        db.run("UPDATE vacancies SET state='saved' WHERE id=?", (a["vacancy_id"],))
    return get_app(aid)


async def make_draft(vid, request="", from_id=None, answers=None):
    v = await ensure_description(vid)
    if not v["description"]:
        raise HTTPException(502, "Не удалось прочитать текст вакансии: " + v.get("fetch_error", "пустая страница"))
    prev = db.one("SELECT * FROM drafts WHERE id=?", (from_id,)) if from_id else None
    prof = db.kv_get("profile", {})
    # ответы на уточнения: новые сохраняем и запоминаем как факты о кандидате
    if answers is not None:
        db.run("UPDATE vacancies SET answers=? WHERE id=?", (json.dumps(answers, ensure_ascii=False), vid))
    else:
        answers = json.loads(v.get("answers") or "{}")
    facts = [f"{a['req']}: {a['text']}" for a in (answers.values() if isinstance(answers, dict) else answers) if a.get("has") and a.get("text")]
    remember_facts(facts)
    try:
        out = await asyncio.to_thread(ai.tailor, master(), v["description"], v["company"], v["title"], prof,
                                      request or None, json.loads(prev["resume"]) if prev else None, facts + [f for f in known_facts() if f not in facts])
    except (RuntimeError, ValueError) as e:
        raise HTTPException(502, str(e))
    res = rm.normalize(out.get("resume") or {})
    ver = (db.one("SELECT COALESCE(MAX(version),0) m FROM drafts WHERE vacancy_id=?", (vid,))["m"]) + 1
    did = db.run("""INSERT INTO drafts(vacancy_id,version,request,resume,letter,letter_short,match,changes,model,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                 (vid, ver, request or "", json.dumps(res, ensure_ascii=False), (out.get("letter") or "").strip(),
                  (out.get("letter_short") or "").strip(), json.dumps(out.get("match") or [], ensure_ascii=False),
                  out.get("changes") or "", ai.model("tailor"), db.now()))
    return draft_row(db.one("SELECT * FROM drafts WHERE id=?", (did,)))


@app.post("/api/vacancies/{vid}/draft")
async def new_draft(vid: int, req: Request):
    """Новая версия резюме и письма под вакансию; с from_id и request — правка предыдущей версии."""
    d = await req.json() if (await req.body()) else {}
    return await make_draft(vid, (d.get("request") or "").strip(), d.get("from_id"), d.get("answers"))


@app.get("/api/drafts/{did}/html", response_class=HTMLResponse)
def draft_html(did: int):
    d = db.one("SELECT resume FROM drafts WHERE id=?", (did,))
    if not d:
        raise HTTPException(404)
    return rm.render(json.loads(d["resume"]))


@app.patch("/api/drafts/{did}")
async def patch_draft(did: int, req: Request):
    d = await req.json()
    for k in ("letter", "letter_short"):
        if k in d:
            db.run(f"UPDATE drafts SET {k}=? WHERE id=?", (d[k], did))
    return draft_row(db.one("SELECT * FROM drafts WHERE id=?", (did,)))


async def approve_draft_core(did):
    dr = db.one("SELECT * FROM drafts WHERE id=?", (did,))
    if not dr:
        raise HTTPException(404)
    v = db.one("SELECT * FROM vacancies WHERE id=?", (dr["vacancy_id"],))
    sent = db.one("SELECT id FROM applications WHERE (vacancy_id=? OR link=?) AND status!='ready'", (v["id"], v["url"]))
    if sent:  # сначала проверка, потом PDF и отметка — чтобы версия не числилась выбранной без отклика
        raise HTTPException(409, "Отклик на эту вакансию отмечен как отправленный. Если это ошибка — нажмите «Ещё не отправлял».")
    prof = db.kv_get("profile", {})
    data = json.loads(dr["resume"])
    fname = "_".join(x for x in ["Резюме", resumes.safe_name(prof.get("last_name", "")), resumes.safe_name(prof.get("first_name", "")),
                                  resumes.safe_name(v["company"])[:25], resumes.safe_name(v["title"])[:35]] if x) + f"_v{dr['version']}.pdf"
    doc = rm.render(data)
    await resumes.html_to_pdf(doc, fname)
    rid = dr["resume_id"] or db.run("INSERT INTO resumes(name,kind,file,text,html,vacancy_id,created_at) VALUES(?,?,?,?,?,?,?)",
                                    (Path(fname).stem, "tailored", fname, rm.plain_text(data), doc, v["id"], db.now()))
    db.run("UPDATE resumes SET file=?, text=?, html=? WHERE id=?", (fname, rm.plain_text(data), doc, rid))
    db.run("UPDATE drafts SET approved=(id=?), resume_id=CASE WHEN id=? THEN ? ELSE resume_id END WHERE vacancy_id=?",
           (did, did, rid, v["id"]))
    a = db.one("SELECT * FROM applications WHERE vacancy_id=? OR link=?", (v["id"], v["url"]))
    track = "Стажировка" if re.search(r"стаж|intern|trainee", v["title"], re.I) else "Работа"
    if a:
        db.run("""UPDATE applications SET resume=?, resume_id=?, cover=1, cover_text=?, cover_short=?, vacancy_id=?,
                  prep_date=COALESCE(NULLIF(prep_date,''),?), updated_at=? WHERE id=?""",
               (Path(fname).stem, rid, dr["letter"], dr["letter_short"], v["id"], today(), db.now(), a["id"]))
        aid = a["id"]
    else:
        aid = db.run("""INSERT INTO applications(company,role,track,status,link,resume,resume_id,cover,cover_text,cover_short,prep_date,history,vacancy_id,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (v["company"], v["title"], track, "ready", v["url"], Path(fname).stem, rid, 1, dr["letter"], dr["letter_short"],
                      today(), json.dumps([{"s": "ready", "at": today()}]), v["id"], db.now(), db.now()))
        db.run("UPDATE vacancies SET state='saved' WHERE id=? AND state!='applied'", (v["id"],))
    return get_app(aid)


@app.post("/api/drafts/{did}/approve")
async def approve_draft(did: int):
    """Эта версия нравится: PDF, отклик «Готово к подаче» с этим резюме и письмом."""
    return await approve_draft_core(did)


# ---------- мастер-резюме ----------
@app.get("/api/master")
def get_master():
    return {"master": db.kv_get("master_resume"), "model": ai.model("tailor")}


@app.get("/api/master/html", response_class=HTMLResponse)
def master_html():
    m = db.kv_get("master_resume")
    return rm.render(m) if m else "<p style='font-family:sans-serif;padding:24px'>Мастер-резюме ещё не собрано.</p>"


@app.post("/api/master/build")
async def master_build(req: Request):
    """Собрать мастер-резюме из загруженного резюме (по умолчанию — основного)."""
    d = await req.json() if (await req.body()) else {}
    r = get_resume(d["resume_id"]) if d.get("resume_id") else resumes.main_resume()
    if not r or not r["text"]:
        raise HTTPException(400, "Загрузите резюме во вкладке «Резюме»")
    try:
        m = rm.normalize(await asyncio.to_thread(ai.master_from_text, r["text"]))
    except (RuntimeError, ValueError) as e:
        raise HTTPException(502, str(e))
    db.kv_set("master_resume", m)
    return {"master": m}


@app.post("/api/master/edit")
async def master_edit(req: Request):
    d = await req.json()
    if not (d.get("request") or "").strip():
        raise HTTPException(400, "Напишите, что поменять")
    try:
        m = rm.normalize(await asyncio.to_thread(ai.edit_resume, master(), d["request"]))
    except (RuntimeError, ValueError) as e:
        raise HTTPException(502, str(e))
    db.kv_set("master_resume", m)
    return {"master": m}


@app.put("/api/master")
async def master_put(req: Request):
    d = await req.json()
    db.kv_set("master_resume", rm.normalize(d))
    return {"master": db.kv_get("master_resume")}


@app.post("/api/master/pdf")
async def master_pdf():
    """PDF из мастер-резюме — становится основным (уходит в отклики без адаптации)."""
    m = master()
    prof = db.kv_get("profile", {})
    fname = "_".join(x for x in ["Резюме", resumes.safe_name(prof.get("last_name", "")), resumes.safe_name(prof.get("first_name", "")), "основное"] if x) + ".pdf"
    doc = rm.render(m)
    await resumes.html_to_pdf(doc, fname)
    ex = db.one("SELECT id FROM resumes WHERE file=?", (fname,))
    rid = ex["id"] if ex else db.run("INSERT INTO resumes(name,kind,file,created_at) VALUES(?,?,?,?)", ("Основное (из мастер-резюме)", "base", fname, db.now()))
    db.run("UPDATE resumes SET text=?, html=?, kind='base' WHERE id=?", (rm.plain_text(m), doc, rid))
    db.run("UPDATE resumes SET is_main=0")
    db.run("UPDATE resumes SET is_main=1 WHERE id=?", (rid,))
    return get_resume(rid)


# ---------- профиль и автозаполнение ----------
@app.get("/api/ai/usage")
def ai_usage(days: int = 30):
    since = (datetime.now() - timedelta(days=days)).isoformat()
    rows = db.q("""SELECT task, model, count(*) calls, sum(input_tokens) tin, sum(output_tokens) tout, sum(cost_usd) cost
                   FROM ai_usage WHERE at>=? GROUP BY task, model ORDER BY cost DESC""", (since,))
    total = db.one("SELECT count(*) calls, coalesce(sum(input_tokens),0) tin, coalesce(sum(output_tokens),0) tout, coalesce(sum(cost_usd),0) cost FROM ai_usage WHERE at>=?", (since,))
    return {"days": days, "total": total, "by_task": rows}


@app.get("/api/profile")
def get_profile():
    return db.kv_get("profile", {})


@app.put("/api/profile")
async def put_profile(req: Request):
    d = await req.json()
    db.kv_set("profile", d)
    return d


@app.get("/api/settings")
def get_settings():
    models = {k: ai.model(k) for k in ai.TASK_MODELS if k != "fix"}
    return {"auto_crawl": db.kv_get("auto_crawl", {"enabled": True, "hours": 6}), "ai": {"models": models}}


@app.put("/api/settings")
async def put_settings(req: Request):
    d = await req.json()
    if "auto_crawl" in d:
        db.kv_set("auto_crawl", d["auto_crawl"])
    if isinstance(d.get("ai", {}).get("models"), dict):
        models = {k: v for k, v in d["ai"]["models"].items() if k in ai.TASK_MODELS and v in ai.MODELS}
        db.kv_set("ai", {"models": models})
    return get_settings()


def _norm(u):
    s = urlsplit(u or "")
    return (s.netloc.replace("www.", "") + s.path).rstrip("/").lower()


ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{4,}", re.I)


def _ids(u):
    return set(ID_RE.findall(u or ""))


def find_app_by_url(url):
    """Ищет отклик по адресу страницы: точное совпадение, вложенный путь
    или тот же сайт + тот же номер вакансии (VK: /vacancy/123 → /feedback/123, hh: vacancyId=123)."""
    n = _norm(url)
    if not n:
        return None
    host = urlsplit(url).netloc.replace("www.", "").lower()
    ids = _ids(url)
    rows = db.q("SELECT * FROM applications WHERE link!='' ORDER BY (status='ready') DESC, id DESC")
    for a in rows:
        la = _norm(a["link"])
        if la and (n == la or n.startswith(la + "/") or (la.startswith(n) and len(n) > 15)):
            return a
    for a in rows:
        lh = urlsplit(a["link"]).netloc.replace("www.", "").lower()
        same_site = lh == host or lh.endswith("." + host) or host.endswith("." + lh)
        if ids and same_site and ids & _ids(urlsplit(a["link"]).path + "?" + urlsplit(a["link"]).query):
            return a
    return None


def _hh_vacancy_id(url):
    m = re.search(r"(?:vacancyId=|hh\.ru/vacancy/)(\d+)", url or "")
    return m.group(1) if m else None


@app.post("/api/questions/answer")
async def answer_questions(req: Request):
    """Расширение прислало вопросы со страницы отклика — отвечаем от лица кандидата."""
    d = await req.json()
    questions = d.get("questions") or []
    if not questions:
        raise HTTPException(400, "На странице не нашлось вопросов")
    # вопросы, на которые ответ лежит в профиле, — без модели
    prof = db.kv_get("profile", {})
    direct, rest = [], []
    for q in questions:
        ans = profile_answer(q, prof)
        (direct if ans is not None else rest).append({**q, "_ans": ans})
    answers = [{"id": q["id"], **q["_ans"]} for q in direct]
    if not rest:
        return {"answers": answers, "notes": "Ответил из профиля, без ИИ — проверьте значения."}
    questions = [{k: v for k, v in q.items() if k != "_ans"} for q in rest]
    url = d.get("url", "")
    # контекст вакансии: из базы, иначе читаем страницу вакансии (для hh — по vacancyId)
    a = find_app_by_url(url) or (find_app_by_url(d.get("ref", "")) if d.get("ref") else None)
    v = None
    if a and a.get("vacancy_id"):
        v = db.one("SELECT * FROM vacancies WHERE id=?", (a["vacancy_id"],))
    hid = _hh_vacancy_id(url) or _hh_vacancy_id(d.get("ref", ""))
    if not v and hid:
        v = db.one("SELECT * FROM vacancies WHERE url LIKE ?", (f"%hh.ru/vacancy/{hid}%",))
    text, company, role = "", d.get("company", ""), d.get("title", "")
    if v:
        v = await ensure_description(v["id"])
        text, company, role = v["description"], v.get("brand") or v["company"], v["title"]
    elif hid:
        try:
            fv = await crawler.fetch_vacancy(f"https://hh.ru/vacancy/{hid}")
            text, role = fv["text"], role or fv["title"]
        except Exception:  # noqa: BLE001
            pass
    text = text or d.get("page_text", "")[:6000]
    try:
        out = await asyncio.to_thread(ai.answer_questions, master(), known_facts(), text, company, role,
                                      questions, prof)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(502, str(e))
    out["answers"] = answers + (out.get("answers") or [])
    return out


PROFILE_Q = [  # (регулярка по тексту вопроса, поле профиля, подпись для пометки)
    (r"зарплат|доход|ожидани.{0,15}(оплат|вознагр|зп|з/п)|salary|compensation", "salary", "ожидания по зарплате"),
    (r"(в каком|ваш|какой) город|где (вы )?(живёте|живете|проживаете)|город проживания|местоположение|location", "city", "город"),
    (r"переезд|релокац|relocat", "relocation", "готовность к переезду"),
    (r"гражданств|citizenship", "citizenship", "гражданство"),
    (r"когда (вы )?(готовы|сможете) (выйти|приступить)|дата выхода|срок выхода|notice period|start date", "start_date", "дата выхода"),
    (r"telegram|телеграм", "telegram", "Telegram"),
    (r"ссылк.{0,15}(резюме|портфолио|github|linkedin)", "portfolio", "ссылка"),
]


def profile_answer(q, prof):
    """Анкетный вопрос (зарплата, город, переезд…) — ответ из профиля без ИИ. None — нужен ИИ."""
    if q.get("type") in ("letter",) or len(q.get("question", "")) > 220:
        return None
    text = q.get("question", "").lower()
    for rx, key, label in PROFILE_Q:
        if re.search(rx, text):
            val = (prof.get(key) or "").strip()
            if q.get("type") in ("radio", "checkbox"):
                if not val:
                    return None
                hit = [o for o in q.get("options", []) if val.lower() in o.lower() or o.lower() in val.lower()]
                return {"choice": hit[:1]} if hit else None
            return {"text": val or f"[уточнить: {label}]"}
    return None


@app.get("/api/autofill")
def autofill(url: str = "", app_id: int = 0):
    """Расширение спрашивает: что подставить на этой странице."""
    a = get_app(app_id) if app_id else find_app_by_url(url)
    prof = db.kv_get("profile", {})
    resume = None
    if a and a.get("resume_id"):
        resume = db.one("SELECT id,name,file FROM resumes WHERE id=?", (a["resume_id"],))
    if not resume:
        m = resumes.main_resume()
        resume = {"id": m["id"], "name": m["name"], "file": m["file"]} if m else None
    letter = (a or {}).get("cover_text") or ""
    if not letter and a:
        letter = ai.template_letter(prof, a["company"], a["role"])
    if not letter:
        letter = ai.template_letter(prof, "вашей компании", "эту позицию").replace("«эту позицию»", "эту позицию")
    full = " ".join(x for x in [prof.get("last_name"), prof.get("first_name"), prof.get("middle_name")] if x)
    queue = None
    if a and a.get("queue_pos"):
        items = queue_list()
        ids = [r["id"] for r in items]
        nxt = queue_next(a["id"])
        queue = {"pos": ids.index(a["id"]) + 1 if a["id"] in ids else 0, "total": len(items),
                 "next_url": nxt["link"] if nxt else "", "next_id": nxt["id"] if nxt else 0}
    short = (a or {}).get("cover_short") or ""
    tailored = bool(a and (a.get("cover_short") or db.one("SELECT id FROM drafts WHERE vacancy_id=? AND approved=1", (a.get("vacancy_id") or 0,))))
    return {
        "letter_is_template": not tailored,
        "queue": queue,
        "cover_short": short,
        "app": {k: a[k] for k in ("id", "company", "role", "status")} if a else None,
        "profile": {**prof, "full_name": full},
        "cover_letter": letter,
        "resume": resume and {**resume, "url": f"/api/resumes/{resume['id']}/file"},
    }


@app.post("/api/autofill/submitted")
async def autofill_submitted(req: Request):
    """Расширение заметило отправку формы — отмечаем отклик поданным (или заводим новый)."""
    d = await req.json()
    a = get_app(d["app_id"]) if d.get("app_id") else find_app_by_url(d.get("url", ""))
    if a:
        if a["status"] == "ready":
            hist = json.loads(a["history"]) if isinstance(a["history"], str) else a["history"]
            hist.append({"s": "applied", "at": today()})
            db.run("UPDATE applications SET status='applied', queue_pos=0, applied_date=?, history=?, updated_at=? WHERE id=?",
                   (today(), json.dumps(hist), db.now(), a["id"]))
            if a.get("vacancy_id"):
                db.run("UPDATE vacancies SET state='applied' WHERE id=?", (a["vacancy_id"],))
        return get_app(a["id"])
    if d.get("company") or d.get("title"):
        return await create_app_from(d)
    return JSONResponse({"ok": False})


async def create_app_from(d):
    main = resumes.main_resume()
    aid = db.run("""INSERT INTO applications(company,role,track,status,link,resume,resume_id,applied_date,prep_date,history,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (d.get("company") or urlsplit(d.get("url", "")).netloc, d.get("title") or "Вакансия", "Работа", "applied",
                  d.get("url", ""), main["name"] if main else "", main["id"] if main else None, today(), today(),
                  json.dumps([{"s": "applied", "at": today()}]), db.now(), db.now()))
    return get_app(aid)


# ---------- планировщик ----------
async def scheduler():
    await asyncio.sleep(20)
    while True:
        cfg = db.kv_get("auto_crawl", {"enabled": True, "hours": 6})
        last = db.one("SELECT started_at FROM runs ORDER BY id DESC LIMIT 1")
        due = not last or datetime.fromisoformat(last["started_at"]) < datetime.now() - timedelta(hours=cfg.get("hours", 6))
        if cfg.get("enabled") and due:
            await run_crawl()
        await asyncio.sleep(600)


@app.on_event("startup")
async def startup():
    seed.seed()
    crawler.fill_brands()
    crawler.mark_applied()
    asyncio.create_task(scheduler())


app.mount("/resume-fonts", StaticFiles(directory=db.ROOT / "app" / "fonts"), name="fonts")
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
