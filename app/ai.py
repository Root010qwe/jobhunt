"""ИИ через локальный Claude Code (`claude -p`): мастер-резюме, адаптация под вакансию, письма."""
import json
import shutil
import subprocess

from . import db, resume_model as rm

CLAUDE = shutil.which("claude") or str(db.Path.home() / ".local/bin/claude")


def model():
    return (db.kv_get("ai", {}) or {}).get("model") or "sonnet"


def ask(prompt, timeout=420, mdl=None):
    try:
        r = subprocess.run([CLAUDE, "-p", "--model", mdl or model(), "--output-format", "text"], input=prompt,
                           capture_output=True, text=True, timeout=timeout, cwd=str(db.DATA))
    except FileNotFoundError:
        raise RuntimeError("Не найден Claude Code (команда claude). Установите его и войдите в аккаунт.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Модель не ответила за отведённое время, попробуйте ещё раз.")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "ошибка claude").strip()[:400])
    return r.stdout.strip()


def ask_json(prompt, mdl=None):
    out = ask(prompt, mdl=mdl)
    try:
        return rm.parse_json(out)
    except Exception:  # noqa: BLE001 — одна попытка починить ответ
        fixed = ask("Верни только корректный JSON из этого ответа, без пояснений:\n\n" + out[:20000], mdl="haiku")
        return rm.parse_json(fixed)


# ---------- мастер-резюме ----------
def master_from_text(text):
    return ask_json(f"""Перенеси резюме в структуру JSON. Это мастер-резюме: из него потом собираются версии под вакансии,
и оно должно выглядеть так же, как исходное.

Правила:
- Переноси текст ДОСЛОВНО: те же формулировки, порядок, цифры, даты. Ничего не добавляй и не сокращай.
- Жирные фрагменты в исходнике размечены **так** — сохрани эту разметку там же внутри пунктов.
- Строка под названием компании (чем занимается компания, должности) — в subtitle.
- Вступительная строка раздела инициатив — в initiatives.intro.
- Интересы — только в поле interests, одной строкой (без слова «Интересы»). Из других пунктов их убери.
- Образование — каждое место отдельно, с периодом.

Схема:
{rm.SCHEMA_HINT}

Верни только JSON.

Резюме (**жирное** выделено):
<<<
{text}
>>>""")


def edit_resume(data, request):
    return ask_json(f"""Вот резюме в JSON. Внеси правку по просьбе кандидата, остальное оставь как есть.
Не выдумывай факты, которых нет в резюме. Верни только JSON той же схемы.

Просьба: {request}

Резюме:
{json.dumps(data, ensure_ascii=False)}""")


# ---------- адаптация под вакансию ----------
TAILOR_RULES = """Как адаптировать (это главное):
1. Выпиши для себя обязательные и желательные требования вакансии и её ключевые слова.
2. headline и «О себе» — под эту роль, языком вакансии: кто кандидат для них и чем закрывает их задачу.
3. Для каждого требования найди в опыте самый близкий факт и переформулируй пункт так, чтобы связь была очевидна,
   словами вакансии. Пример: вакансия про продукт Wi-Fi для бизнеса — подсвети интеграции, инфраструктуру,
   B2B-заказчиков, метрики использования; про AI — LLM, RAG, автоматизацию.
4. Переставь проекты и пункты: самое релевантное — первым. Нерелевантное сократи или убери.
5. Покажи переносимый опыт: проектное управление → работа с бэклогом, приоритизация, запуск фич; внутренние сервисы → продуктовый подход.
6. Навыки: сначала те, что просит вакансия и подтверждает опыт.
7. Можно обобщать, переименовывать формулировки, переводить результаты в продуктовый язык.
   Нельзя выдумывать работодателей, должности, сроки, цифры и технологии — это вскроется на собеседовании.
8. Одна страница A4: не больше ~3300 знаков текста, 2–4 пункта на проект.
9. Сохраняй стиль мастер-резюме: пункт начинается с **жирного результата**, дальше пояснение после двоеточия. Интересы — в поле interests.
10. Пробелы (чего нет в опыте) не упоминай ни в резюме, ни в письме: ни «изучаю», ни «пока нет». Пробелы — только в match."""

TAILOR_OUT = """Верни только JSON:
{
  "resume": <резюме по той же схеме>,
  "letter": "сопроводительное письмо 600–900 знаков, 3 абзаца: почему эта роль; 2 факта, закрывающих главные требования; призыв к разговору и подпись",
  "letter_short": "та же мысль до 250 знаков (с подписью) — для полей с лимитом 255",
  "match": [{"req": "требование вакансии", "evidence": "чем закрываю, коротко", "level": "strong | partial | gap"}],
  "changes": "что поменял в резюме под вакансию — 2–3 коротких пункта через «; »"
}
match — 4–8 главных требований. gap честно, если опыта нет."""


def tailor(master, vacancy_text, company, role, profile, request=None, previous=None, facts=None):
    sign = ", ".join(x for x in [" ".join(x for x in [profile.get("first_name"), profile.get("last_name")] if x),
                                   profile.get("phone"), profile.get("telegram")] if x)
    base = previous or master
    extra = ""
    if previous and request:
        extra = f"\nЭто правка предыдущей версии. Просьба кандидата: {request}\nОстальное в предыдущей версии сохрани.\n"
    elif request:
        extra = f"\nПожелание кандидата: {request}\n"
    if facts:
        extra += ("\nКандидат дополнительно сообщил о себе (это правда — используй в резюме и письме, где закрывает требования; "
                  "формулируй уверенно, без «изучаю» и «немного знаю», но не преувеличивай):\n" + "\n".join(f"- {f}" for f in facts) + "\n")
    return ask_json(f"""Ты карьерный консультант, который адаптирует резюме под конкретную вакансию так, чтобы рекрутер
за 10 секунд увидел совпадение с требованиями.

{TAILOR_RULES}
Подпись в письме: {sign}.
{extra}
Вакансия: {role} — {company}
<<<
{vacancy_text}
>>>

{'Предыдущая версия резюме' if previous else 'Мастер-резюме'} (JSON):
{json.dumps(base, ensure_ascii=False)}
{'' if not previous else chr(10) + 'Мастер-резюме, источник фактов (JSON):' + chr(10) + json.dumps(master, ensure_ascii=False)}

{TAILOR_OUT}""")


def analyze(master, vacancy_text, company, role, facts):
    """Требования вакансии и что по ним есть в резюме; по пробелам — вопрос кандидату."""
    known = "\n".join(f"- {f}" for f in facts) or "—"
    return ask_json(f"""Разбери вакансию для кандидата перед тем, как адаптировать его резюме.

1. Выпиши 5–9 главных требований и задач вакансии (обязательные и желательные), коротко, своими словами.
2. Для каждого найди подтверждение в резюме или в известных фактах о кандидате.
3. level: strong — прямо подтверждено; partial — есть близкий опыт; gap — в резюме нет.
4. Для partial и gap задай кандидату один короткий вопрос, чтобы понять, есть ли у него этот опыт или знания,
   которых нет в резюме (например: «Работали с сетями или Wi-Fi-оборудованием? Что именно знаете?»). Для strong — пустая строка.

Верни только JSON:
{{"requirements": [{{"req": "...", "level": "strong|partial|gap", "evidence": "что есть в резюме, коротко", "question": "..."}}]}}

Вакансия: {role} — {company}
<<<
{vacancy_text}
>>>

Известные факты о кандидате (не из резюме, но правда):
{known}

Резюме (JSON):
{json.dumps(master, ensure_ascii=False)}""", mdl="sonnet")


def cover_letter(base_text, vacancy_text, company, role, profile):
    """Короткий путь для массового отклика: только письмо."""
    sign = " ".join(x for x in [profile.get("first_name"), profile.get("last_name")] if x)
    return ask(f"""Напиши сопроводительное письмо под вакансию: 600–900 знаков, 3 абзаца, без штампов.
Первый абзац — почему эта роль; второй — 2 факта из резюме, которые закрывают главные требования; третий — призыв к разговору.
Ничего не выдумывай. Подпись: {sign}, {profile.get('phone', '')}, {profile.get('telegram', '')}. Верни только текст.

Вакансия: {role} — {company}
<<<
{vacancy_text}
>>>

Резюме:
<<<
{base_text}
>>>""")


def template_letter(profile, company, role):
    t = profile.get("cover_template") or ""
    return (t.replace("{company}", company or "").replace("{role}", role or "")
             .replace("{name}", " ".join(x for x in [profile.get("first_name"), profile.get("last_name")] if x)))
