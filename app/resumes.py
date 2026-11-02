"""Хранилище резюме: импорт файлов, извлечение текста, сборка PDF из HTML."""
import re
import shutil
import zipfile
from pathlib import Path

from pypdf import PdfReader

from . import db

DIR = db.DATA / "resumes"

PDF_CSS = """
@page { size: A4; margin: 14mm 15mm; }
body { font-family: "Helvetica Neue", Arial, sans-serif; font-size: 10.2pt; line-height: 1.38; color: #17201B; margin: 0; }
h1 { font-size: 19pt; margin: 0 0 2px; letter-spacing: .01em; }
.contacts { color: #4b5750; font-size: 9.5pt; }
.headline { font-weight: 600; margin: 6px 0 4px; color: #2F45C5; }
h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: .06em; border-bottom: 1px solid #D8DED9; padding-bottom: 2px; margin: 12px 0 5px; }
p { margin: 0 0 4px; }
ul { margin: 2px 0 4px; padding-left: 16px; }
li { margin: 0 0 2px; }
.job { margin-bottom: 6px; }
.job-head { display: flex; justify-content: space-between; }
.job-role { font-style: italic; color: #4b5750; }
"""


def extract_text(path: Path) -> str:
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        try:
            return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages).strip()
        except Exception:  # noqa: BLE001
            return ""
    if p.suffix.lower() == ".docx":
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml).strip()
    if p.suffix.lower() in (".txt", ".md", ".html"):
        return re.sub(r"<[^>]+>", " ", p.read_text(encoding="utf-8", errors="ignore")).strip()
    return ""


def add_file(src: Path, name=None, kind="base", vacancy_id=None, copy=True):
    src = Path(src)
    dest = DIR / src.name
    if copy and src.resolve() != dest.resolve():
        if dest.exists():
            dest = DIR / f"{src.stem}_{db.now().replace(':', '')}{src.suffix}"
        shutil.copy2(src, dest)
    rid = db.run("INSERT INTO resumes(name,kind,file,text,vacancy_id,created_at) VALUES(?,?,?,?,?,?)",
                 (name or src.stem, kind, dest.name, extract_text(dest), vacancy_id, db.now()))
    return rid


def main_resume():
    return db.one("SELECT * FROM resumes WHERE is_main=1 ORDER BY id DESC LIMIT 1") or \
        db.one("SELECT * FROM resumes WHERE kind='base' ORDER BY id DESC LIMIT 1")


def safe_name(s):
    return re.sub(r"[^\w\-]+", "_", s, flags=re.U).strip("_")[:60]


async def html_to_pdf(fragment: str, filename: str) -> Path:
    from playwright.async_api import async_playwright
    out = DIR / filename
    doc = f"<!doctype html><html><head><meta charset='utf-8'><style>{PDF_CSS}</style></head><body>{fragment}</body></html>"
    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page()
        await page.set_viewport_size({"width": 680, "height": 1000})  # ширина A4 минус поля, в CSS-пикселях
        await page.set_content(doc, wait_until="load")
        # подгоняем под одну страницу: уменьшаем масштаб, но не мельче 80 %
        h = await page.evaluate("document.body.scrollHeight")
        limit = 1010  # высота A4 минус поля, в CSS-пикселях
        if h > limit:
            await page.evaluate(f"document.body.style.zoom = {max(0.8, limit / h * 0.98):.3f}")
        await page.pdf(path=str(out), format="A4", print_background=True)
        await b.close()
    return out
