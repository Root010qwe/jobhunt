"""Резюме как структура данных + единая вёрстка.

ИИ меняет только содержание (JSON), вёрстка всегда одна и та же: так не разъезжаются
разделы, интересы не прилипают к достижениям, а образование идёт от нового к старому.
"""
import html
import json
import re

SCHEMA_HINT = """{
  "name": "Фамилия Имя Отчество",
  "headline": "позиционирование одной строкой (роль · домен · сильная сторона)",
  "contacts": ["Москва", "email", "телефон", "Telegram @nick", "ссылка"],
  "about": "2–3 предложения: кто вы и чем полезны именно этой роли",
  "experience": [
    {"company": "Компания", "location": "город / формат", "period": "май 2025 – наст. время",
     "role": "Должность", "context": "одна строка о компании или продукте (необязательно)",
     "projects": [{"name": "Проект — команда N чел.", "bullets": ["результат с цифрой", "..."]}],
     "bullets": ["пункты без проекта, если нужны"]}
  ],
  "initiatives": {"title": "Продуктовые инициативы", "bullets": ["..."]},
  "education": [{"org": "Вуз", "period": "2026 – 2028", "degree": "Степень, программа", "note": "необязательно"}],
  "skills": [{"group": "Управление", "items": "через запятую"}],
  "extra": ["достижения, буткемпы, олимпиады — по одному на пункт"],
  "interests": "интересы одной строкой"
}"""


def _year(period):
    """Последний год периода; «наст. время» и «в процессе» — самые свежие."""
    p = (period or "").lower()
    if re.search(r"наст|н\.в|present|now|в процессе|по сей", p):
        return 9999
    years = [int(y) for y in re.findall(r"(19\d\d|20\d\d)", p)]
    return max(years) if years else 0


def normalize(d):
    d = dict(d or {})
    for k in ("contacts", "experience", "education", "skills", "extra"):
        if not isinstance(d.get(k), list):
            d[k] = [] if not d.get(k) else [d[k]]
    # образование и опыт: сверху текущее, ниже — более раннее
    d["education"] = sorted(d["education"], key=lambda e: (_year(e.get("period")), e.get("period", "")), reverse=True)
    d["experience"] = sorted(d["experience"], key=lambda e: (_year(e.get("period")), e.get("period", "")), reverse=True)
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
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; color: #1b1f1d; font-size: 9.6pt; line-height: 1.42;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.page { width: 210mm; min-height: 297mm; padding: 13mm 15mm 12mm; background: #fff; }
.hd { border-bottom: 1.5pt solid #1b1f1d; padding-bottom: 7pt; margin-bottom: 9pt; }
.name { font-size: 20pt; font-weight: 700; letter-spacing: -0.01em; line-height: 1.1; }
.headline { margin-top: 3pt; font-size: 10.5pt; font-weight: 600; color: #2f45c5; }
.contacts { margin-top: 4pt; color: #4a524e; font-size: 9pt; }
.contacts span + span::before { content: "·"; margin: 0 6pt; color: #9aa29e; }
.sec { margin-top: 10pt; }
.sec > h2 { margin: 0 0 4pt; font-size: 8.6pt; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; color: #6a726e; }
p { margin: 0; }
.job + .job { margin-top: 8pt; }
.row { display: flex; justify-content: space-between; align-items: baseline; gap: 12pt; }
.org { font-weight: 700; font-size: 10.2pt; }
.loc { font-weight: 400; color: #4a524e; }
.period { white-space: nowrap; color: #4a524e; font-size: 9pt; }
.role { font-weight: 600; margin-top: 1pt; }
.ctx { color: #4a524e; font-size: 9pt; margin-top: 1pt; }
.proj { margin-top: 4pt; }
.proj-name { font-weight: 600; }
ul { margin: 2pt 0 0; padding-left: 12pt; }
li { margin: 0 0 1.6pt; padding-left: 1pt; }
li::marker { color: #8a928e; }
.edu + .edu { margin-top: 4pt; }
.deg { color: #2b302e; }
.skills { display: grid; grid-template-columns: max-content 1fr; gap: 2pt 10pt; }
.skills b { font-weight: 600; }
"""


def esc(s):
    return html.escape(str(s or ""))


def render(d, standalone=True):
    d = normalize(d)
    o = ['<div class="page">']
    o.append('<header class="hd">')
    o.append(f'<div class="name">{esc(d.get("name"))}</div>')
    if d.get("headline"):
        o.append(f'<div class="headline">{esc(d["headline"])}</div>')
    if d.get("contacts"):
        o.append('<div class="contacts">' + "".join(f"<span>{esc(c)}</span>" for c in d["contacts"] if c) + "</div>")
    o.append("</header>")

    if d.get("about"):
        o.append(f'<section class="sec"><h2>О себе</h2><p>{esc(d["about"])}</p></section>')

    if d["experience"]:
        o.append('<section class="sec"><h2>Опыт работы</h2>')
        for j in d["experience"]:
            loc = f' <span class="loc">· {esc(j["location"])}</span>' if j.get("location") else ""
            o.append(f'<div class="job"><div class="row"><span class="org">{esc(j.get("company"))}{loc}</span>'
                     f'<span class="period">{esc(j.get("period"))}</span></div>')
            if j.get("role"):
                o.append(f'<div class="role">{esc(j["role"])}</div>')
            if j.get("context"):
                o.append(f'<div class="ctx">{esc(j["context"])}</div>')
            for pr in j.get("projects") or []:
                o.append(f'<div class="proj"><div class="proj-name">{esc(pr.get("name"))}</div><ul>'
                         + "".join(f"<li>{esc(b)}</li>" for b in pr.get("bullets") or []) + "</ul></div>")
            if j.get("bullets"):
                o.append("<ul>" + "".join(f"<li>{esc(b)}</li>" for b in j["bullets"]) + "</ul>")
            o.append("</div>")
        o.append("</section>")

    ini = d.get("initiatives") or {}
    if ini.get("bullets"):
        o.append(f'<section class="sec"><h2>{esc(ini.get("title") or "Проекты и инициативы")}</h2><ul>'
                 + "".join(f"<li>{esc(b)}</li>" for b in ini["bullets"]) + "</ul></section>")

    if d["education"]:
        o.append('<section class="sec"><h2>Образование</h2>')
        for e in d["education"]:
            note = f' <span class="loc">— {esc(e["note"])}</span>' if e.get("note") else ""
            o.append(f'<div class="edu"><div class="row"><span class="org">{esc(e.get("org"))}</span>'
                     f'<span class="period">{esc(e.get("period"))}</span></div><div class="deg">{esc(e.get("degree"))}{note}</div></div>')
        o.append("</section>")

    if d["skills"]:
        o.append('<section class="sec"><h2>Навыки</h2><div class="skills">'
                 + "".join(f'<b>{esc(s.get("group"))}</b><span>{esc(s.get("items"))}</span>' for s in d["skills"])
                 + "</div></section>")

    if d["extra"]:
        o.append('<section class="sec"><h2>Дополнительно</h2><ul>' + "".join(f"<li>{esc(x)}</li>" for x in d["extra"]) + "</ul></section>")

    if d.get("interests"):
        o.append(f'<section class="sec"><h2>Интересы</h2><p>{esc(d["interests"])}</p></section>')
    o.append("</div>")
    body = "".join(o)
    if not standalone:
        return body
    return f"<!doctype html><html lang='ru'><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"


def plain_text(d):
    """Текст резюме — для писем и поиска."""
    d = normalize(d)
    lines = [d.get("name", ""), d.get("headline", ""), " · ".join(d.get("contacts") or []), "", d.get("about", "")]
    for j in d["experience"]:
        lines += ["", f'{j.get("company")} — {j.get("role")} ({j.get("period")})']
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
    return "\n".join(x for x in lines if x is not None)
