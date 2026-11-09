import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("JOBHUNT_DATA") or ROOT / "data").expanduser()
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "resumes").mkdir(exist_ok=True)
DB_PATH = DATA / "jobhunt.db"

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company TEXT NOT NULL,
  role TEXT NOT NULL,
  track TEXT DEFAULT '',
  status TEXT DEFAULT 'ready',
  link TEXT DEFAULT '',
  resume TEXT DEFAULT '',
  resume_id INTEGER,
  cover INTEGER DEFAULT 0,
  cover_text TEXT DEFAULT '',
  test TEXT DEFAULT '',
  prep_date TEXT DEFAULT '',
  applied_date TEXT DEFAULT '',
  next TEXT DEFAULT '',
  next_at TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  history TEXT DEFAULT '[]',
  vacancy_id INTEGER,
  created_at TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS vacancies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT,
  company TEXT,
  title TEXT,
  url TEXT UNIQUE,
  meta TEXT DEFAULT '',
  score INTEGER DEFAULT 0,
  watch INTEGER DEFAULT 0,
  state TEXT DEFAULT 'new',
  description TEXT DEFAULT '',
  found_at TEXT,
  last_seen TEXT
);
CREATE TABLE IF NOT EXISTS resumes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT,
  kind TEXT DEFAULT 'base',
  file TEXT DEFAULT '',
  text TEXT DEFAULT '',
  html TEXT DEFAULT '',
  vacancy_id INTEGER,
  is_main INTEGER DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT, finished_at TEXT, found INTEGER DEFAULT 0, new INTEGER DEFAULT 0, log TEXT DEFAULT ''
);
"""


def now():
    return datetime.now().isoformat(timespec="seconds")


def conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, conn() as c:
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(applications)")}
        if "queue_pos" not in cols:  # очередь массового отклика: 0 = не в очереди
            c.execute("ALTER TABLE applications ADD COLUMN queue_pos INTEGER DEFAULT 0")


def q(sql, args=()):
    with _lock, conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    r = q(sql, args)
    return r[0] if r else None


def run(sql, args=()):
    with _lock, conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def kv_get(k, default=None):
    r = one("SELECT v FROM kv WHERE k=?", (k,))
    return json.loads(r["v"]) if r else default


def kv_set(k, v):
    run("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v, ensure_ascii=False)))
