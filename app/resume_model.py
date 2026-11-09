"""Резюме как структура данных + единая вёрстка.

ИИ меняет только содержание (JSON), вёрстка всегда одна и та же: так не разъезжаются
разделы, интересы не прилипают к достижениям, а образование идёт от нового к старому.
"""
import html
import json
import re

SCHEMA_HINT = """{
  "name": "Фамилия Имя Отчество",
  "contacts": ["Москва", "email", "телефон", "Tg: @nick"],
  "headline": "позиционирование одной строкой",
  "about": "абзац «О себе»",
  "experience": [
    {"company": "АО «Компания», Город (формат)", "period": "май 2025 – наст. время",
     "subtitle": "строка под компанией: чем занимается компания и ваши должности",
     "projects": [{"name": "Проект – команда N чел.", "bullets": ["**жирный результат**: пояснение"]}],
     "bullets": ["пункты без проекта, если нужны"]}
  ],
  "initiatives": {"title": "Продуктовые инициативы", "intro": "строка-вступление (необязательно)", "bullets": ["**жирный результат**: пояснение"]},
  "education": [{"org": "Вуз, Город", "period": "2026 – 2028", "degree": "Степень. Программа."}],
  "skills": [{"group": "Управление", "items": "через запятую"}],
  "extra": ["**жирное начало** продолжение — по одному на пункт"],
  "interests": "интересы одной строкой (выводится последним пунктом «Дополнительно»)"
}"""


def _years(period):
    """(начало, конец) периода; «наст. время» — текущий год."""
    from datetime import date
    p = (period or "").lower()
    ys = [int(y) for y in re.findall(r"(19\d\d|20\d\d)", p)]
    now = date.today().year
    start = ys[0] if ys else 0
    end = now if re.search(r"наст|н\.в|present|now|в процессе|по сей", p) else (ys[-1] if ys else 0)
    return start, end


def normalize(d):
    d = dict(d or {})
    for k in ("contacts", "experience", "education", "skills", "extra"):
        if not isinstance(d.get(k), list):
            d[k] = [] if not d.get(k) else [d[k]]
    # образование и опыт: сверху текущее, ниже — более раннее
    d["education"] = sorted(d["education"], key=lambda e: _years(e.get("period")), reverse=True)
    d["experience"] = sorted(d["experience"], key=lambda e: _years(e.get("period")), reverse=True)
    for j in d["experience"]:  # старая схема: context + role → одна строка под компанией
        if not j.get("subtitle") and (j.get("context") or j.get("role")):
            j["subtitle"] = " ".join(x for x in [j.get("context"), j.get("role")] if x)
        if j.get("location") and j["location"] not in (j.get("company") or ""):
            j["company"] = f'{j.get("company")}, {j["location"]}'
            j["location"] = ""
    if isinstance(d.get("initiatives"), list):
        d["initiatives"] = {"title": "Проекты и инициативы", "bullets": d["initiatives"]}
    if isinstance(d.get("interests"), list):
        d["interests"] = ", ".join(d["interests"])
    return d


def parse_json(text):
    """Достаёт JSON из ответа модели (даже если вокруг есть текст или ```json)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b == -1:
        raise ValueError("Модель вернула не JSON")
    return json.loads(t[a:b + 1])


CSS = """
@font-face { font-family: "Liberation Serif"; font-weight: 400; src: url("/resume-fonts/LiberationSerif-Regular.ttf"); }
@font-face { font-family: "Liberation Serif"; font-weight: 700; src: url("/resume-fonts/LiberationSerif-Bold.ttf"); }
@font-face { font-family: "Liberation Serif"; font-style: italic; src: url("/resume-fonts/LiberationSerif-Italic.ttf"); }
@page { size: Letter; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { font-family: "Liberation Serif", "Times New Roman", Times, serif; color: #000; font-size: 10pt; line-height: 1.15; }
.page { width: 8.5in; min-height: 11in; padding: 26pt 65pt 14pt; background: #fff; }
.name { text-align: center; font-size: 17pt; font-weight: 700; text-transform: uppercase; line-height: 1.1; }
.contacts { text-align: center; font-size: 9pt; margin-top: 1pt; }
.headline { text-align: center; font-size: 10.5pt; font-weight: 700; margin-top: 1pt; }
h2 { font-size: 14pt; font-weight: 700; margin: 3pt 0 3pt; padding-bottom: 0.5pt; border-bottom: 0.75pt solid #000; line-height: 1.15; }
p { margin: 0; text-align: justify; }
.row { display: flex; justify-content: space-between; align-items: baseline; gap: 12pt; margin-top: 3pt; line-height: 1.15; }
.org { font-size: 12pt; font-weight: 700; }
.period { font-size: 9.5pt; white-space: nowrap; }
.sub { text-align: justify; }
.proj { font-size: 12pt; font-weight: 700; margin: 2pt 0 0 7pt; line-height: 1.15; }
ul { margin: 1pt 0 0; padding-left: 28pt; }
li { text-align: justify; margin: 0; }
.intro { margin-bottom: 1pt; }
"""


def esc(s):
    return html.escape(str(s or ""))


def rich(s):
    """**жирное** → <b>, остальное экранируется."""
    parts = re.split(r"(\*\*.+?\*\*)", str(s or ""))
    return "".join(f"<b>{esc(x[2:-2])}</b>" if x.startswith("**") and x.endswith("**") else esc(x) for x in parts)


def bullets(items):
    return "<ul>" + "".join(f"<li>{rich(b)}</li>" for b in items if b) + "</ul>" if items else ""


def render(d, standalone=True):
    d = normalize(d)
    o = ['<div class="page">', f'<div class="name">{esc(d.get("name"))}</div>']
    if d.get("contacts"):
        o.append('<div class="contacts">' + " • ".join(esc(c) for c in d["contacts"] if c) + "</div>")
    if d.get("headline"):
        o.append(f'<div class="headline">{esc(d["headline"])}</div>')
    if d.get("about"):
        o.append(f'<h2>О себе</h2><p>{rich(d["about"])}</p>')
    if d["experience"]:
        o.append("<h2>Опыт работы</h2>")
        for j in d["experience"]:
            o.append(f'<div class="row"><span class="org">{esc(j.get("company"))}</span><span class="period">{esc(j.get("period"))}</span></div>')
            if j.get("subtitle"):
                o.append(f'<p class="sub">{rich(j["subtitle"])}</p>')
            for pr in j.get("projects") or []:
                o.append(f'<div class="proj">{esc(pr.get("name"))}</div>' + bullets(pr.get("bullets")))
            o.append(bullets(j.get("bullets")))
    ini = d.get("initiatives") or {}
    if ini.get("bullets"):
        o.append(f'<h2>{esc(ini.get("title") or "Продуктовые инициативы")}</h2>')
        if ini.get("intro"):
            o.append(f'<p class="intro">{rich(ini["intro"])}</p>')
        o.append(bullets(ini["bullets"]))
    if d["education"]:
        o.append("<h2>Образование</h2>")
        for e in d["education"]:
            o.append(f'<div class="row"><span class="org">{esc(e.get("org"))}</span><span class="period">{esc(e.get("period"))}</span></div>'
                     f'<p>{rich(e.get("degree"))}</p>')
    if d["skills"]:
        o.append("<h2>Навыки</h2>" + bullets([f'**{s.get("group")}:** {s.get("items")}' for s in d["skills"]]))
    extra = list(d["extra"])
    if d.get("interests"):
        extra.append(f'**Интересы:** {d["interests"]}')   # интересы — отдельным пунктом, не отдельным разделом
    if extra:
        o.append("<h2>Дополнительно</h2>" + bullets(extra))
    o.append("</div>")
    body = "".join(o)
    if not standalone:
        return body
    return f"<!doctype html><html lang='ru'><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"


def plain_text(d):
    """Текст резюме — для писем и поиска."""
    d = normalize(d)
    lines = [d.get("name", ""), " • ".join(d.get("contacts") or []), d.get("headline", ""), "", d.get("about", "")]
    for j in d["experience"]:
        lines += ["", f'{j.get("company")} ({j.get("period")})', j.get("subtitle", "")]
        for pr in j.get("projects") or []:
            lines.append(pr.get("name", ""))
            lines += [f"- {b}" for b in pr.get("bullets") or []]
        lines += [f"- {b}" for b in j.get("bullets") or []]
    for b in (d.get("initiatives") or {}).get("bullets") or []:
        lines.append(f"- {b}")
    for e in d["education"]:
        lines.append(f'{e.get("org")}, {e.get("degree")} ({e.get("period")})')
    for s in d["skills"]:
        lines.append(f'{s.get("group")}: {s.get("items")}')
    lines += d["extra"]
    if d.get("interests"):
        lines.append("Интересы: " + d["interests"])
    return re.sub(r"\*\*", "", "\n".join(x for x in lines if x is not None))
