"""Адаптация резюме и сопроводительные письма через локальный Claude Code (`claude -p`)."""
import re
import shutil
import subprocess

from . import db

CLAUDE = shutil.which("claude") or str(db.Path.home() / ".local/bin/claude")


def ask(prompt, timeout=300):
    try:
        r = subprocess.run([CLAUDE, "-p", "--output-format", "text"], input=prompt, capture_output=True,
                           text=True, timeout=timeout, cwd=str(db.DATA))
    except FileNotFoundError:
        raise RuntimeError("Не найден Claude Code (команда claude). Установите его или пишите тексты вручную.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude не ответил за отведённое время, попробуйте ещё раз.")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "ошибка claude").strip()[:400])
    return r.stdout.strip()


RULES = """Правила:
- Только факты из базового резюме. Ничего не выдумывай: ни опыта, ни цифр, ни навыков.
- Можно переставлять акценты, переформулировать под язык вакансии, выбрасывать нерелевантное, менять заголовок-позиционирование.
- Цифры и результаты сохраняй точными.
- Язык — как у вакансии (русский или английский)."""


def tailor_resume(base_text, vacancy_text, company, role):
    prompt = f"""Ты помогаешь кандидату адаптировать резюме под конкретную вакансию.

{RULES}
- Объём — строго одна страница A4: не больше ~3200 знаков текста, 3–4 пункта на проект, навыки одной строкой на группу.

Верни ТОЛЬКО HTML-фрагмент (без <html>, <head>, <style>, без markdown-ограждений) такой структуры:
<h1>ФИО</h1>
<div class="contacts">город • email • телефон • telegram</div>
<div class="headline">позиционирование под вакансию</div>
<h2>О себе</h2><p>...</p>
<h2>Опыт работы</h2>
<div class="job"><div class="job-head"><b>Компания</b><span>даты</span></div><div class="job-role">должность</div><ul><li>...</li></ul></div>
<h2>Образование</h2> ... <h2>Навыки</h2><ul>...</ul> <h2>Дополнительно</h2><ul>...</ul>

Вакансия: {role} — {company}
Текст вакансии:
<<<
{vacancy_text}
>>>

Базовое резюме:
<<<
{base_text}
>>>
"""
    html = ask(prompt)
    html = re.sub(r"^```(?:html)?\s*|\s*```$", "", html.strip())
    return html


def cover_letter(base_text, vacancy_text, company, role, profile):
    name = " ".join(x for x in [profile.get("first_name"), profile.get("last_name")] if x)
    prompt = f"""Напиши короткое сопроводительное письмо для отклика.

{RULES}
- 600–900 символов, 3 абзаца, без воды и штампов («я ответственный», «командный игрок»).
- Первый абзац: почему эта роль и компания. Второй: 2 факта из опыта, которые закрывают требования вакансии. Третий: готовность обсудить, контакт.
- Подпись: {name or 'кандидат'}, {profile.get('phone', '')}, {profile.get('telegram', '')}.
- Верни только текст письма, без заголовков и пояснений.

Вакансия: {role} — {company}
<<<
{vacancy_text}
>>>

Резюме:
<<<
{base_text}
>>>
"""
    return ask(prompt)


def template_letter(profile, company, role):
    t = profile.get("cover_template") or ""
    return (t.replace("{company}", company or "").replace("{role}", role or "")
             .replace("{name}", " ".join(x for x in [profile.get("first_name"), profile.get("last_name")] if x)))
