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

from . import ai, crawler, db, resumes, seed

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
def list_vacancies(state: str = "new", q: str = "", watch: int = 0, source: str = "", days: int = 0):
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
    if days:
        sql += " AND found_at>=?"
        args.append((datetime.now() - timedelta(days=days)).isoformat())
    if q:
        sql += " AND (lower(title) LIKE ? OR lower(company) LIKE ? OR lower(meta) LIKE ?)"
        args += [f"%{q.lower()}%"] * 3
    sql += " ORDER BY substr(found_at,1,10) DESC, score DESC, id DESC LIMIT 600"
    return db.q(sql, args)


@app.get("/api/vacancies/stats")
def vacancy_stats():
    return {
        "states": {r["state"]: r["n"] for r in db.q("SELECT state, count(*) n FROM vacancies GROUP BY state")},
        "sources": db.q("SELECT source, count(*) n FROM vacancies WHERE state='new' GROUP BY source ORDER BY n DESC"),
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
        v = db.one("SELECT * FROM vacancies WHERE id=?", (a["vacancy_id"],))
        if v and v["description"]:
            return v["description"]
    if not a.get("link"):
        return f"{a['role']} в {a['company']}"
    text = await crawler.fetch_text(a["link"])
    if a.get("vacancy_id"):
        db.run("UPDATE vacancies SET description=? WHERE id=?", (text, a["vacancy_id"]))
    return text


@app.post("/api/apps/{aid}/tailor")
async def tailor(aid: int, req: Request):
    """Адаптирует резюме (и по желанию письмо) под вакансию из отклика."""
    d = await req.json() if (await req.body()) else {}
    a = get_app(aid)
    base = get_resume(d["base_id"]) if d.get("base_id") else resumes.main_resume()
    if not base:
        raise HTTPException(400, "Сначала загрузите основное резюме")
    try:
        vt = await vacancy_text(a)
        prof = db.kv_get("profile", {})
        out = {}
        if d.get("resume", True):
            html = await asyncio.to_thread(ai.tailor_resume, base["text"], vt, a["company"], a["role"])
            fname = f"Резюме_{resumes.safe_name(prof.get('last_name', ''))}_{resumes.safe_name(prof.get('first_name', ''))}_{resumes.safe_name(a['company'])}_{resumes.safe_name(a['role'])[:30]}.pdf"
            await resumes.html_to_pdf(html, fname)
            rid = db.run("INSERT INTO resumes(name,kind,file,text,html,vacancy_id,created_at) VALUES(?,?,?,?,?,?,?)",
                         (Path(fname).stem, "tailored", fname, re.sub(r"<[^>]+>", " ", html), html, a.get("vacancy_id"), db.now()))
            db.run("UPDATE applications SET resume=?, resume_id=?, prep_date=COALESCE(NULLIF(prep_date,''),?) WHERE id=?",
                   (Path(fname).stem, rid, today(), aid))
            out["resume_id"] = rid
        if d.get("cover", True):
            letter = await asyncio.to_thread(ai.cover_letter, base["text"], vt, a["company"], a["role"], prof)
            db.run("UPDATE applications SET cover=1, cover_text=? WHERE id=?", (letter, aid))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return get_app(aid)


@app.post("/api/resumes/{rid}/rebuild")
async def rebuild_pdf(rid: int, req: Request):
    """Пересобрать PDF после ручной правки HTML адаптированного резюме."""
    d = await req.json()
    r = get_resume(rid)
    html = d.get("html", r["html"])
    fname = r["file"] or f"{resumes.safe_name(r['name'])}.pdf"
    await resumes.html_to_pdf(html, fname)
    db.run("UPDATE resumes SET html=?, text=?, file=? WHERE id=?", (html, re.sub(r"<[^>]+>", " ", html), fname, rid))
    return get_resume(rid)


# ---------- профиль и автозаполнение ----------
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
    return {"auto_crawl": db.kv_get("auto_crawl", {"enabled": True, "hours": 6})}


@app.put("/api/settings")
async def put_settings(req: Request):
    d = await req.json()
    if "auto_crawl" in d:
        db.kv_set("auto_crawl", d["auto_crawl"])
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
    return {
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
            db.run("UPDATE applications SET status='applied', applied_date=?, history=?, updated_at=? WHERE id=?",
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
    crawler.mark_applied()
    asyncio.create_task(scheduler())


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
