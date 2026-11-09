"""Демо-данные с вымышленным кандидатом: ./run.sh --demo  (база в папке demo-data/)."""
import asyncio
import json
from datetime import date, timedelta

from . import db, resume_model as rm, resumes

PROFILE = {
    "last_name": "Петрова", "first_name": "Анна", "middle_name": "Сергеевна",
    "email": "anna.petrova@example.com", "phone": "+7 900 000-00-00", "telegram": "@anna_pm_demo",
    "city": "Москва", "relocation": "Не планирую", "birth_date": "", "citizenship": "Россия",
    "university": "НИУ «Пример», прикладная информатика (2021–2025)", "position": "Junior Product Manager",
    "salary": "от 120 000 ₽", "hh_url": "", "linkedin": "", "github": "", "portfolio": "", "english": "B2",
    "cover_template": (
        "Здравствуйте!\n\nОткликаюсь на вакансию «{role}» в {company}. Я начинающий продакт-менеджер "
        "с опытом аналитика: умею считать воронки, формулировать гипотезы и доводить их до A/B-теста.\n\n"
        "На стажировке запустила онбординг, который поднял конверсию в первую покупку на 12 %, "
        "и собрала дашборд retention, которым пользуется вся команда.\n\n"
        "Буду рада обсудить, чем могу быть полезна.\n\n{name}"
    ),
}

RESUME_HTML = """<h1>Петрова Анна Сергеевна</h1>
<div class="contacts">Москва • anna.petrova@example.com • +7 900 000-00-00 • Tg: @anna_pm_demo</div>
<div class="headline">Junior Product Manager · продуктовая аналитика, эксперименты, AI-инструменты</div>
<h2>О себе</h2><p>Начинающий продакт-менеджер с бэкграундом продуктового аналитика. Считаю воронки, формулирую гипотезы и довожу их до A/B-тестов.</p>
<h2>Опыт работы</h2>
<div class="job"><div class="job-head"><b>ООО «Пример Маркет»</b><span>2024 – наст. время</span></div><div class="job-role">Стажёр продакт-менеджера</div>
<ul><li>Запустила новый онбординг: +12 % конверсии в первую покупку.</li><li>Собрала дашборд retention в SQL и Metabase.</li><li>Провела 15 интервью с пользователями, сформировала бэклог гипотез.</li></ul></div>
<h2>Образование</h2><p><b>НИУ «Пример»</b>, прикладная информатика, 2021–2025</p>
<h2>Навыки</h2><ul><li>SQL, Python (pandas), A/B-тесты, Amplitude, Figma, Jira</li><li>Английский — B2</li></ul>"""

APPS = [  # дней назад, компания, вакансия, тип, статус, следующий шаг, через сколько дней
    (1, "Яндекс", "Стажёр-менеджер продукта в Яндекс Маркет", "Стажировка", "ready", "", None),
    (2, "Авито", "Junior продакт-менеджер (Авито Работа)", "Работа", "ready", "", None),
    (3, "Т-Банк", "Младший продакт-менеджер B2C", "Работа", "screening", "Созвон с HR", 1),
    (5, "VK", "Менеджер продукта (ИИ-ассистенты)", "Работа", "interview", "Продуктовое интервью", 2),
    (6, "X5 Tech", "Product Analyst (AI-first)", "Работа", "test", "Сдать тестовое", 0),
    (8, "МТС", "Стажёр аналитик данных", "Стажировка", "applied", "", None),
    (9, "2ГИС", "Менеджер B2B-проектов", "Работа", "waiting", "Написать рекрутеру", -1),
    (11, "Ozon", "Младший менеджер продукта", "Работа", "applied", "", None),
    (13, "МегаФон", "Стажёр по ИИ и аналитическим продуктам", "Стажировка", "rejected", "", None),
    (15, "Kaspersky", "Junior Product Manager", "Работа", "offer", "Ответить на оффер", 3),
    (17, "Lamoda", "Менеджер продукта (лояльность)", "Работа", "rejected", "", None),
    (20, "Winline", "Младший продуктовый аналитик", "Работа", "skipped", "", None),
]

VACANCIES = [  # компания, вакансия, источник, мета, оценка, из моего списка
    ("Яндекс", "Менеджер продукта ИИ-компаньона", "Яндекс", "Москва · гибрид", 30, 1),
    ("Okkam", "Стажёр / менеджер проектов (IT, B2B)", "hh.ru", "без опыта · Москва", 46, 0),
    ("ОТП Банк", "Стажёр — продакт-менеджер", "hh.ru", "без опыта · Москва", 36, 1),
    ("Авито", "Продакт-менеджер команды Авито Соседи", "Авито", "Москва", 25, 1),
    ("Winline", "Младший продуктовый аналитик", "Winline", "1–3 года · Москва", 33, 1),
    ("VK", "Менеджер продукта B2B ИИ-агенты", "VK", "Москва · гибрид", 30, 1),
    ("МегаФон", "Product Manager на продукт «Лидогенерация»", "МегаФон", "удалённо", 25, 1),
    ("Т-Банк", "ML-продакт-менеджер", "Т-Банк", "Москва", 30, 1),
    ("Playrix", "Junior Project Manager", "Playrix", "удалённо", 30, 1),
    ("Crezu", "Junior+ Product Manager — эксперименты и монетизация", "hh.ru", "1–3 года · удалённо", 33, 0),
    ("Higgsfield", "Product Manager (Product Growth)", "Higgsfield", "Almaty / remote", 25, 1),
    ("МТС", "Стажёр Data Analyst [МТС Web Services]", "МТС", "без опыта · Москва", 30, 1),
]


MASTER = {
    "name": "Петрова Анна Сергеевна",
    "headline": "Junior Product Manager · продуктовая аналитика, эксперименты, AI-инструменты",
    "contacts": ["Москва", "anna.petrova@example.com", "+7 900 000-00-00", "Telegram @anna_pm_demo"],
    "about": "Начинающий продакт-менеджер с бэкграундом продуктового аналитика. Считаю воронки, формулирую гипотезы и довожу их до A/B-тестов; собираю дашборды, которыми пользуется команда.",
    "experience": [
        {"company": "ООО «Пример Маркет»", "location": "Москва", "period": "2024 – наст. время", "role": "Стажёр продакт-менеджера",
         "context": "Маркетплейс товаров для дома, 2 млн покупателей в месяц.",
         "projects": [{"name": "Онбординг новых покупателей", "bullets": ["Запустила новый онбординг: +12 % конверсии в первую покупку за 6 недель.", "Провела 15 интервью с пользователями и собрала бэклог из 30 гипотез."]},
                      {"name": "Аналитика удержания", "bullets": ["Собрала дашборд retention в SQL и Metabase; им пользуется вся продуктовая команда.", "Нашла сегмент с оттоком 40 % на второй неделе, предложила и проверила 3 гипотезы."]}]},
    ],
    "education": [{"org": "НИУ «Пример»", "period": "2021 – 2025", "degree": "Бакалавр, прикладная информатика"}],
    "skills": [{"group": "Продукт", "items": "CustDev, гипотезы, A/B-тесты, бэклог и приоритизация, роадмап"},
               {"group": "Данные", "items": "SQL, Python (pandas), Amplitude, Metabase"},
               {"group": "Инструменты", "items": "Figma, Jira, Notion"}, {"group": "Языки", "items": "русский — родной, английский — B2"}],
    "extra": ["Финалистка кейс-чемпионата по продуктовой аналитике (2024)."],
    "interests": "настольные игры, бег, фотография",
}

DRAFT = {
    "resume": {**MASTER, "headline": "Менеджер продукта · AI-ассистенты, эксперименты, продуктовая аналитика",
               "about": "Продакт с аналитическим бэкграундом: запускаю фичи от интервью до A/B-теста и считаю, что они дали. Для AI-ассистента сильна в гипотезах о сценариях использования и метриках качества ответов."},
    "letter": "Здравствуйте! Откликаюсь на роль менеджера продукта ИИ-компаньона: хочу работать над продуктом, где ценность измеряется тем, вернётся ли человек завтра.\n\nНа стажировке я запустила онбординг, который поднял конверсию в первую покупку на 12 %, и собрала дашборд удержания, по которому команда принимает решения. Сценарии ИИ-компаньона хочу развивать так же: от интервью до эксперимента.\n\nБуду рада обсудить задачи команды.\nАнна Петрова, +7 900 000-00-00",
    "letter_short": "Здравствуйте! Продакт с аналитическим бэкграундом: онбординг +12 % к конверсии, дашборд удержания для команды. Хочу развивать ИИ-компаньона от гипотез до экспериментов. Анна Петрова, +7 900 000-00-00",
    "match": [{"req": "Опыт запуска продуктовых фич", "evidence": "Онбординг: +12 % конверсии за 6 недель", "level": "strong"},
              {"req": "Работа с метриками и экспериментами", "evidence": "A/B-тесты, дашборд retention в SQL и Metabase", "level": "strong"},
              {"req": "Исследования пользователей", "evidence": "15 интервью, бэклог из 30 гипотез", "level": "strong"},
              {"req": "Понимание LLM-продуктов", "evidence": "Пользуется AI-инструментами, коммерческого опыта с LLM нет", "level": "partial"},
              {"req": "Опыт B2C-продукта от 1 года", "evidence": "Стажировка в маркетплейсе — меньше года", "level": "partial"}],
    "changes": "Заголовок и «О себе» — под AI-ассистента; на первом месте эксперименты и метрики удержания",
}


VACANCY_TEXT = """Менеджер продукта ИИ-компаньона
Чем предстоит заниматься
Развивать сценарии использования ИИ-компаньона в подписке
Формулировать гипотезы и проверять их в A/B-экспериментах
Работать с метриками удержания и качества ответов
Проводить исследования с пользователями
Мы ждём
Опыт запуска продуктовых фич от 1 года
Умение работать с данными: SQL, продуктовые метрики
Интерес к LLM-продуктам
Мы предлагаем
Гибридный формат, сильную команду, ДМС с первого дня"""


def run():
    db.init()
    if db.one("SELECT id FROM applications LIMIT 1"):
        return
    db.kv_set("profile", PROFILE)
    db.kv_set("auto_crawl", {"enabled": False, "hours": 6})
    db.kv_set("master_resume", rm.normalize(MASTER))
    asyncio.run(resumes.html_to_pdf(RESUME_HTML, "Резюме_Петрова_Анна_общее.pdf"))
    main = db.run("INSERT INTO resumes(name,kind,file,text,html,is_main,created_at) VALUES(?,?,?,?,?,1,?)",
                  ("Общее (RU)", "base", "Резюме_Петрова_Анна_общее.pdf", RESUME_HTML, RESUME_HTML, db.now()))
    today = date.today()
    for ago, co, role, track, st, nxt, due in APPS:
        d = (today - timedelta(days=ago)).isoformat()
        rid, rname = main, "Общее (RU)"
        if st in ("interview", "test", "offer"):
            rname = f"Резюме_Петрова_Анна_{co.replace(' ', '_')}"
            rid = db.run("INSERT INTO resumes(name,kind,file,text,created_at) VALUES(?,?,?,?,?)",
                         (rname, "tailored", "Резюме_Петрова_Анна_общее.pdf", RESUME_HTML, db.now()))
        hist = [{"s": "ready", "at": d}] + ([{"s": "applied", "at": d}] if st != "ready" else []) + \
               ([{"s": st, "at": today.isoformat()}] if st not in ("ready", "applied") else [])
        letter = PROFILE['cover_template'].replace('{company}', co).replace('{role}', role).replace('{name}', 'Анна Петрова')
        db.run("""INSERT INTO applications(company,role,track,status,link,resume,resume_id,cover,cover_text,applied_date,prep_date,next,next_at,history,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (co, role, track, st, "https://example.com/vacancy", rname, rid, 1, letter,
                "" if st == "ready" else d, d, nxt, (today + timedelta(days=due)).isoformat() if due is not None else "",
                json.dumps(hist, ensure_ascii=False), db.now(), db.now()))
    for i, (co, title, src, meta, score, watch) in enumerate(VACANCIES):
        ts = (today - timedelta(days=0 if i < 7 else 1)).isoformat() + "T09:00:00"
        db.run("INSERT INTO vacancies(source,company,title,url,meta,score,watch,state,found_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (src, co, title, f"https://example.com/vacancy/{i}", meta, score, watch, "new", ts, ts))
    vid = db.one("SELECT id FROM vacancies WHERE title LIKE 'Менеджер продукта ИИ-компаньона%'")["id"]
    db.run("UPDATE vacancies SET description=? WHERE id=?", (VACANCY_TEXT, vid))
    db.run("""INSERT INTO drafts(vacancy_id,version,resume,letter,letter_short,match,changes,model,created_at)
              VALUES(?,?,?,?,?,?,?,?,?)""", (vid, 1, json.dumps(rm.normalize(DRAFT["resume"]), ensure_ascii=False), DRAFT["letter"],
              DRAFT["letter_short"], json.dumps(DRAFT["match"], ensure_ascii=False), DRAFT["changes"], "sonnet", db.now()))
    db.run("INSERT INTO runs(started_at,finished_at,found,new,log) VALUES(?,?,?,?,?)",
           (db.now(), db.now(), len(VACANCIES), len(VACANCIES), "demo"))


if __name__ == "__main__":
    run()
    print("demo ok:", db.DATA)
