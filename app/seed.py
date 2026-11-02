"""Первый запуск: пустой профиль с шаблоном письма и настройки бота.

Резюме загружаются во вкладке «Резюме», профиль заполняется во вкладке «Профиль и бот».
Если рядом лежит data/private/seed_personal.py (в git не попадает) — используется он.
"""
import importlib.util

from . import db

PROFILE = {
    "last_name": "", "first_name": "", "middle_name": "",
    "email": "", "phone": "", "telegram": "",
    "city": "", "relocation": "", "birth_date": "", "citizenship": "",
    "university": "", "position": "", "salary": "",
    "hh_url": "", "linkedin": "", "github": "", "portfolio": "", "english": "",
    "cover_template": (
        "Здравствуйте!\n\n"
        "Откликаюсь на вакансию «{role}» в {company}. <Пара предложений о том, кто вы и почему эта роль.>\n\n"
        "<Два факта из опыта с цифрами, которые закрывают требования вакансии.>\n\n"
        "Буду рад обсудить, чем могу быть полезен команде.\n\n"
        "{name}"
    ),
}


def _personal():
    path = db.DATA / "private" / "seed_personal.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("seed_personal", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seed():
    db.init()
    personal = _personal()
    if personal and hasattr(personal, "seed"):
        personal.seed()
        return
    if not db.kv_get("profile"):
        db.kv_set("profile", PROFILE)
    if db.kv_get("auto_crawl") is None:
        db.kv_set("auto_crawl", {"enabled": True, "hours": 6})


if __name__ == "__main__":
    seed()
    print("ok")
