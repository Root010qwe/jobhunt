"""Название компании для фильтра: «ОТП Банк. Бизнес», «ПАО МегаФон, Офис» → «ОТП Банк», «МегаФон»."""
import re

# бренд → как он встречается в названиях работодателей (регулярки, регистр не важен)
ALIASES = {
    "Яндекс": r"яндекс|yandex",
    "VK": r"\bvk\b|вконтакте|\bmax\b|дзен|mail\.ru",
    "Т-Банк": r"т-банк|t-bank|тинькофф|tinkoff",
    "Сбер": r"\bсбер|sber|сбербанк",
    "Альфа-Банк": r"альфа[- ]банк|экосистема альфа|alfa",
    "ВТБ": r"\bвтб\b|vtb",
    "Газпромбанк": r"газпромбанк",
    "ОТП Банк": r"\bотп\b|otp bank",
    "Совкомбанк": r"совкомбанк",
    "МТС": r"\bмтс\b|\bmts\b",
    "МегаФон": r"мегафон|megafon|\byota\b",
    "Билайн": r"билайн|beeline|вымпелком",
    "Ростелеком": r"ростелеком|ртк[- ]?ит|ртк[- ]?цт|\bрт лабс",
    "Ozon": r"\bozon\b|\bозон\b",
    "Wildberries": r"wildberries|\brwb\b|вайлдберриз",
    "Авито": r"авито|avito",
    "X5": r"\bx5\b|пятёрочка|пятерочка|перекр[её]сток",
    "Лаборатория Касперского": r"касперск|kaspersky",
    "Т1": r"\bт1\b|\bt1\b",
    "ЛАНИТ": r"ланит|lanit|treolan|онланта",
    "КРОК": r"\bкрок\b|\bcroc\b",
    "2ГИС": r"2гис|2gis",
    "Lamoda": r"lamoda|ламода",
    "Контур": r"контур",
    "Selectel": r"selectel|селектел",
    "Самокат": r"самокат",
    "Купер": r"купер|kuper",
    "Иви": r"\bиви\b|\bivi\b",
    "Positive Technologies": r"positive technologies|позитив",
    "Росатом": r"росатом|гринатом",
    "Газпром": r"газпром(?!банк)",
    "Циан": r"циан|cian",
    "HeadHunter": r"headhunter|\bhh\.ru\b",
    "Спортмастер": r"спортмастер",
    "Магнит": r"магнит",
    "Ашан": r"ашан|auchan",
    "PARI": r"\bpari\b|\bпари\b",
    "Winline": r"winline|винлайн",
    "Лига Ставок": r"лига ставок",
    "Fonbet": r"фонбет|fonbet",
    "BetBoom": r"betboom",
    "Higgsfield": r"higgsfield",
    "Playrix": r"playrix",
    "Azur Games": r"azur games",
    "Nebius": r"nebius",
    "РСХБ": r"рсхб",
    "ПСБ": r"\bпсб\b",
    "Островок": r"островок|ostrovok",
}
_COMPILED = [(b, re.compile(rx, re.I)) for b, rx in ALIASES.items()]
LEGAL = re.compile(r"^(ооо|пао|оао|зао|ао|ип|нко|ано|фгуп|гк|группа компаний|компания)\s+", re.I)


def brand(company):
    c = (company or "").strip()
    for b, rx in _COMPILED:
        if rx.search(c):
            return b
    # неизвестная компания: убираем кавычки, форму собственности и подразделение после точки/запятой
    s = re.sub(r"[«»\"“”]", "", c)
    s = LEGAL.sub("", s)
    s = LEGAL.sub("", s)
    s = re.split(r"\s*(?:[.,:]|::|\s\(|\s-\s|\s—\s)\s*", s, maxsplit=1)[0].strip()
    return s or c
