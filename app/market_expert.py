"""
«ИИ специалист» — ветка «рынок»: вопросы о страховом рынке Узбекистана отвечаются ИЗ ДАННЫХ.

Что умеет:
  detect()  — распознаёт вопрос о рынке на ru/uz/en: компании (APEX, Узбекинвест, Kafolat, INSON…),
              классы (КАСКО/3, огонь/8, грузы/7, кредиты/14, ОСАГО, ОСГОР…), регионы, показатели
              (премии, выплаты, убыточность, доля, рост, рейтинг, рыночная ставка), период
              («год назад», «за 2025 год»). Уточнение («а по классу 8?», «а у INSON?», «а год назад?»)
              разрешается по контексту диалога (последняя сущность, показатель и срез).
  answer()  — собирает ответ: короткий текст с цифрами, таблица до 10 строк, источники (файл отчёта
              НАПП и дата среза), пометка «ytd — нарастающим итогом», отдельно «вывод специалиста».
              Модель (если есть ключ) только пересказывает переданные факты.
  memory    — память диалога в процессе: последние 8 реплик на сессию, срок 2 часа. В базу текст
              реплик не попадает (там по-прежнему только отпечаток вопроса — legal.log_question).

Финансы страховщиков (с 03.10.2026): активы, капитал, резервы, прибыль, рентабельность, претензии и отказы,
число договоров, карточка компании («расскажи про INSON») — из company_rankings (рэнкинг snsratings.uz,
tools/ranking_parse.py): значение, место, доля, изменение к прошлому году и источник «Рэнкинг snsratings.uz».

Данные (только чтение): market_stats (отчёты НАПП, tools/market_stats.py), company_financials, company_rankings,
docs/market_facts.json (если есть — подхватывается по mtime). Формулы те же, что в app/market_picture.py:
  убыточность, % = выплаты с начала года / премии с начала года × 100;
  годовая ставка, % = премии с начала года × 12 / месяцев среза / обязательства на дату × 100;
  доля компании, % = премии компании / сумма премий всех страховщиков того же среза × 100.
Нет данных — честно «в открытых данных НАПП нет».
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import unicodedata
from collections import deque
from pathlib import Path
from typing import Optional

from . import db, llm
from . import market_picture as mp
from . import rankings as rk

ROOT = Path(__file__).resolve().parent.parent
FACTS_FILE = ROOT / "docs" / "market_facts.json"
TABLE_MAX = 10

# --------------------------------------------------------------------------- #
#  Тексты
# --------------------------------------------------------------------------- #

ROLE = {
    "ru": "специалист по страхованию и рынку Узбекистана",
    "uz": "Oʻzbekiston sugʻurta va sugʻurta bozori boʻyicha mutaxassis",
    "en": "specialist in insurance and the insurance market of Uzbekistan",
}
# база доли: в одном ответе — одна база, и она названа
SHARE_GENERAL = {"ru": "доля среди страховщиков общего страхования",
                 "uz": "umumiy sugʻurta sugʻurtalovchilari orasidagi ulush",
                 "en": "share among general insurers"}
SHARE_MARKET = {"ru": "доля в премиях всего рынка", "uz": "butun bozor mukofotlaridagi ulush",
                "en": "share of total market premiums"}
CLARIFY = {
    "ru": "Уточните, пожалуйста: какой показатель (премии, выплаты, убыточность, доля, рыночная ставка) "
          "и за какой период (последний срез, год назад, конкретный год)?",
    "uz": "Iltimos, aniqlashtiring: qaysi koʻrsatkich (mukofot, toʻlov, zararlilik, ulush, bozor stavkasi) "
          "va qaysi davr (oxirgi hisobot, bir yil oldin, aniq yil)?",
    "en": "Please specify: which indicator (premiums, claims paid, loss ratio, share, market rate) "
          "and which period (latest slice, a year ago, a given year)?",
}
YTD_NOTE = {
    "ru": "ytd — нарастающим итогом с начала года",
    "uz": "ytd — yil boshidan oʻsib boruvchi yakun",
    "en": "ytd — year to date (cumulative since 1 January)",
}
NAPP_LABEL = {"ru": "данные НАПП", "uz": "NAPP maʼlumotlari", "en": "NAPP data"}
FACTS_LABEL = {"ru": "обзор рынка (база знаний)", "uz": "bozor sharhi (bilimlar bazasi)",
               "en": "market review (knowledge base)"}
COMPANY_LABEL = {"ru": "данные компании", "uz": "kompaniya maʼlumotlari", "en": "company data"}
OPINION_LABEL = {
    "ru": "Вывод специалиста — интерпретация цифр, а не данные НАПП",
    "uz": "Mutaxassis xulosasi — raqamlar talqini, NAPP maʼlumoti emas",
    "en": "Specialist's view — an interpretation of the figures, not NAPP data",
}
DATA_LABEL = {"ru": "Данные", "uz": "Maʼlumotlar", "en": "Data"}
NO_DATA = {
    "ru": "В открытых данных НАПП нет %s.",
    "uz": "NAPP ochiq maʼlumotlarida %s yoʻq.",
    "en": "The open NAPP data has no %s.",
}
MLN = {"ru": "млн сум", "uz": "mln soʻm", "en": "UZS m"}
AI_LABEL = {"ru": "пересказ ИИ строго по данным выше", "uz": "SI qayta bayoni — faqat yuqoridagi maʼlumotlar boʻyicha",
            "en": "AI summary strictly based on the data above"}

METRIC_NAME = {
    "premiums": {"ru": "премии", "uz": "mukofotlar", "en": "premiums"},
    "payouts": {"ru": "выплаты", "uz": "toʻlovlar", "en": "claims paid"},
    "loss_ratio": {"ru": "убыточность", "uz": "zararlilik", "en": "loss ratio"},
    "share": {"ru": "доля рынка", "uz": "bozor ulushi", "en": "market share"},
    "rate": {"ru": "рыночная ставка", "uz": "bozor stavkasi", "en": "market rate"},
    "growth": {"ru": "рост год к году", "uz": "yillik oʻsish", "en": "year-on-year growth"},
    "own_funds": {"ru": "собственные средства и резервы", "uz": "oʻz mablagʻlari va zaxiralar",
                  "en": "own funds and reserves"},
}
COLS = {
    "ru": {"rank": "№", "name": "Строка отчёта", "company": "Страховщик", "region": "Регион", "period": "Срез",
           "premiums": "Премии ytd, млн сум", "payouts": "Выплаты ytd, млн сум", "loss_ratio": "Убыточность, %",
           "share": "Доля, %", "yoy": "Рост премий г/г, %", "rate": "Ставка, % годовых"},
    "uz": {"rank": "№", "name": "Hisobot qatori", "company": "Sugʻurtalovchi", "region": "Hudud", "period": "Sana",
           "premiums": "Mukofot ytd, mln soʻm", "payouts": "Toʻlov ytd, mln soʻm", "loss_ratio": "Zararlilik, %",
           "share": "Ulush, %", "yoy": "Mukofot oʻsishi, %", "rate": "Stavka, % yillik"},
    "en": {"rank": "#", "name": "Report line", "company": "Insurer", "region": "Region", "period": "Slice",
           "premiums": "Premiums ytd, UZS m", "payouts": "Claims paid ytd, UZS m", "loss_ratio": "Loss ratio, %",
           "share": "Share, %", "yoy": "Premium growth y/y, %", "rate": "Rate, % p.a."},
}

# --------------------------------------------------------------------------- #
#  Нормализация
# --------------------------------------------------------------------------- #

_APO_RE = re.compile("[" + re.escape("'‘’ʻʼʽ′`´ʹ") + "]")
_TOK_RE = re.compile(r"[0-9a-zа-яёўқғҳ\-]+")


def nrm(s: str) -> str:
    """Нижний регистр, ё→е, апострофы сняты (oʻzbekinvest = ozbekinvest), пробелы схлопнуты."""
    s = unicodedata.normalize("NFC", s or "").lower().replace("ё", "е")
    s = _APO_RE.sub("", s)
    return " ".join(s.split())


def _tokens(s: str) -> list:
    return [t.strip("-") for t in _TOK_RE.findall(nrm(s)) if t.strip("-")]


def _has(q: str, words) -> bool:
    """Есть ли в вопросе слово, начинающееся с одной из основ."""
    toks = _tokens(q)
    for w in words:
        if " " in w:
            if w in q:
                return True
            continue
        if any(t.startswith(w) for t in toks):
            return True
    return False


# --------------------------------------------------------------------------- #
#  Словари распознавания
# --------------------------------------------------------------------------- #

MARKET_WORDS = ("рынок", "рынк", "рынке", "напп", "napp", "лидер", "рейтинг", "топ", "крупнейш", "крупн",
                "market", "leader", "ranking", "rank", "largest", "biggest", "top",
                "bozor", "yetakchi", "reyting", "eng katta", "компании рынка")
# «рыночная стоимость» — понятие оценки, а не вопрос о рынке
NOT_MARKET = ("market value", "bozor qiymat", "рыночн стоимост", "рыночная стоимость", "рыночной стоимост",
              "рыночную стоимость")
# признаки правового вопроса: такой вопрос уточнением рыночного не считается
LEGAL_MARK = ("если", "можно", "обязан", "закон", "стать", "норм", "договор", "agar", "mumkin", "qonun",
              "modda", "shartnoma", "if ", "law", "article", "contract", "must")
METRICS = [
    ("loss_ratio", ("убыточн", "loss ratio", "loss-ratio", "zararlilik", "zarar koeff", "lr")),
    ("share", ("доля", "долю", "доли", "share", "ulush")),
    ("rate", ("рыночн ставк", "ставк", "rate", "stavka", "тариф рынка", "market rate")),
    ("growth", ("рост", "прирост", "динамик", "вырос", "сократ", "growth", "grew", "dynamic", "trend",
                "osish", "osdi", "dinamika", "год к году", "y/y", "yoy")),
    ("payouts", ("выплат", "возмещен", "payout", "claims paid", "paid claims", "tolov", "tolangan")),
    ("premiums", ("преми", "сбор", "взнос", "premium", "gwp", "mukofot", "yigim")),
    ("own_funds", ("собственн средств", "собственные средства", "резерв", "own funds", "reserves",
                   "oz mablag", "zaxira")),
]
RANK_WORDS = ("лидер", "рейтинг", "топ", "крупнейш", "кто больше", "первое место", "самая", "самый",
              "leader", "top", "largest", "biggest", "ranking", "rank", "yetakchi", "reyting", "eng katta",
              "eng kop", "kim")
DEFINITION = ("что такое", "что значит", "что означает", "как рассчитыва", "как считает", "what is a ",
              "what does", "what is the definition", "nima degani", "nima bu", "qanday hisoblan",
              "how is the", "how do you calculate", "how to calculate")
FOLLOW_START = ("а ", "а что", "и ", "a ", "and ", "what about", "how about", "va ", "endi ")
CLASS_RE = [re.compile(r"(?:класс\w*|class(?:es)?|klass\w*|sinf\w*)\s*№?\s*(\d{1,2})\b"),
            re.compile(r"\b(\d{1,2})\s*-?\s*(?:класс\w*|class|klass\w*|sinf\w*)")]
# слово → строка market_stats; сила: True — явное название вида, False — слово могло попасть из бытового вопроса
CLASS_WORDS = [
    (("осаго", "osago", "автогражданк"), "osago", True),
    (("осгор", "osgor", "ifjms", "работодател", "employer"), "osgor", True),
    (("каско", "kasko", "casco", "автотранспорт", "наземн транспорт", "motor hull"), "cls3", True),
    (("груз", "cargo", "yuk "), "cls7", True),
    (("кредит", "credit", "kredit"), "cls14", True),
    (("огонь", "огня", "пожар", "имуществ", "fire", "property", "olov", "yongin", "mol-mulk"), "cls8", False),
    (("несчастн", "accident", "baxtsiz"), "cls1", True),
    (("болезн", "sickness", "kasallik"), "cls2", True),
    (("авиа", "aviation", "aviatsiya"), "cls5", True),
    (("морск", "marine", "dengiz"), "cls6", True),
    (("ответственност", "liability", "javobgarlik"), "cls13", False),
    (("поручительств", "гаранти", "surety", "kafillik"), "cls15", True),
    (("финансов", "financial risk", "moliyaviy"), "cls16", True),
    (("медицин", "medical", "tibbiy"), "cls18", True),
    (("жизн", "life insurance", "hayot"), "voluntary_life", True),
    (("обязательн", "compulsory", "mandatory", "majburiy"), "mandatory", True),
    (("добровольн", "voluntary", "ixtiyoriy"), "voluntary_general", True),
]
# кириллица и обиходные имена страховщиков → различимое слово из названия в отчёте НАПП
COMPANY_ALIASES = {
    "апекс": "apex", "apeks": "apex", "узбекинвест": "ozbekinvest", "узинвест": "ozbekinvest",
    "uzbekinvest": "ozbekinvest", "кафолат": "kafolat", "капитал": "kapital", "гросс": "gross",
    "инсон": "inson", "insonsugurta": "inson", "альфа": "alfa", "евроазия": "euroasia", "кафил": "kafil",
    "азия": "asia", "траст": "trust-insurance", "импекс": "impex-insurance", "алском": "alskom",
    "sqb": "sqb", "нео": "neo", "агросугурта": "ozagrosugurta", "узагро": "ozagrosugurta",
    "темирйул": "temiryol", "мозаик": "mosaic", "инфинити": "infinity", "квантум": "quantum",
    "имкон": "imkon", "ишонч": "ishonch", "ария": "aria", "халк": "xalq", "хамкор": "hamkor",
    "семург": "semurg", "глобал": "global", "май иншуранс": "my-insurance", "узре": "uzre", "uzre": "uzre",
}
LEGAL_FORM = {"aj", "qk", "mqst", "eisk", "sugurta", "insurance", "kompaniyasi", "tashkiloti", "corp",
              "group", "company", "ranee", "ранее", "ozbekiston", "the", "qayta"}
REGION_WORDS = ("регион", "област", "region", "viloyat", "hudud")
CLASS_DIM_WORDS = ("классы", "классам", "класса", "по классам", "classes", "klasslar", "вид страхования",
                   "виды страхования")
QUANTITY = ("сколько", "объем", "объём", "итог", "how much", "volume", "total", "qancha", "hajm", "jami")
AGO_WORDS = ("год назад", "прошл", "предыдущ", "year ago", "last year", "previous year", "prior year",
             "otgan yil", "bir yil oldin", "oldingi yil")
# Финансы страховщиков (company_rankings, рэнкинг snsratings.uz): слово → показатели (первый — главный)
FIN_WORDS = [
    (("уставн капитал", "уставный капитал", "уставного капитал", "акционерн", "share capital", "ustav kapital",
      "ustav fond", "aksiyador"), ["share_capital", "total_capital"]),
    (("капитал", "kapital", "capital", "equity"), ["total_capital", "share_capital"]),
    (("актив", "aktiv", "asset"), ["total_assets"]),
    (("резерв", "zaxira", "reserve"), ["reserves_net", "reserves_gross"]),
    (("рентабельн", "доходность капитал", "roe", "roa", "rentabel", "profitability", "return on"),
     ["roe", "roa", "roe_share_capital"]),
    (("прибыл", "убыток", "убытк", "foyda", "profit", "net income"), ["net_profit", "profit_before_tax"]),
    (("претензи", "davo", "claims received", "number of claims", "complaint"),
     ["claims_received", "claims_paid_count", "claims_refused", "claims_unsettled", "refused_to_claims"]),
    (("отказ", "отказан", "rad etil", "rad qilin", "refus", "rejected", "denied"),
     ["claims_refused", "refused_to_claims", "claims_received"]),
    (("выручк", "tushum", "revenue"), ["net_revenue"]),
    (("инвестиц", "investitsiya", "investment"), ["total_investments", "short_term_investments",
                                                   "long_term_investments"]),
    (("денежн средств", "денежные средства", "pul mablag", "cash"), ["cash"]),
    (("дебитор", "debitor", "receivable"), ["short_term_receivables", "insurance_receivables"]),
    (("подразделени", "филиал", "filial", "bolinma", "branches"), ["branches"]),
    (("административн", "mamuriy", "admin expense", "administrative"), ["admin_expenses", "admin_to_period_expenses"]),
    (("страховые обязательств", "страховых обязательств", "insurance liabilit", "sugurta majburiyat"),
     ["insurance_liabilities"]),
]
# «договоры» — показатель только с «сколько / количество / число»: иначе вопрос о праве
CONTRACT_WORDS = ("договор", "shartnoma", "contract", "polis")
COUNT_WORDS = ("сколько", "количеств", "число", "how many", "number of", "qancha", "nechta", "soni")
# неоднозначные слова («отказ», «претензия»): при правовом обороте вопрос не о статистике
LEGAL_VERB = ("вправе", "может ли", "можно ли", "должен", "обязан", "почему", "как оформ", "как подать",
              "как обжал", "что делать", "как быть", "жалоб", "в суд", "huquq", "mumkinmi", "nima qilish",
              "can the", "may the", "how to", "what to do")
AMBIG_FIN = ("отказ", "претензи", "davo", "rad etil", "rad qilin", "refus", "rejected", "denied", "complaint")
CARD_WORDS = ("расскажи про", "расскажи о", "расскажите про", "расскажите о", "о компании", "про компанию",
              "карточк", "профиль компании", "финансы компании", "финансовое состояние", "финансовые показател",
              "что известно о", "обзор компании", "tell me about", "about the company", "company profile",
              "financials of", "haqida", "moliyaviy holat", "moliyaviy korsatkich")


# --------------------------------------------------------------------------- #
#  Данные
# --------------------------------------------------------------------------- #

def _rows(con, sql, *args) -> list:
    return [dict(r) for r in con.execute(sql, args)]


def _has_table(con, name: str) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone())


def companies(con) -> list:
    """Строки страховщиков: [{row_key, name, words}] — words: различимые слова названия."""
    if not _has_table(con, "market_stats"):
        return []
    out = []
    for r in _rows(con, "SELECT row_key, MAX(row_name) AS row_name FROM market_stats "
                        "WHERE row_key LIKE 'company:%' GROUP BY row_key"):
        base = nrm(r["row_key"][len("company:"):])
        words = {t for t in re.split(r"[^0-9a-z\-]+", base) if t and t not in LEGAL_FORM and len(t) >= 3}
        words |= {t for t in re.split(r"[^0-9a-z]+", base) if t and t not in LEGAL_FORM and len(t) >= 4}
        if "qayta" in base.split():
            words.add("uzre")          # «O'zbekiston qayta sug'urta»: «qayta» — обычное узбекское слово
        out.append({"row_key": r["row_key"], "name": _pretty(r["row_name"]), "words": words, "base": base})
    # страховщики из рэнкинга, которых нет в отчётах НАПП market_stats (жизнь и др.): ключ по тому же правилу
    have = {c["row_key"] for c in out}
    if _has_table(con, "company_rankings"):
        for r in _rows(con, "SELECT company, MAX(company_raw) AS raw FROM company_rankings WHERE company<>'ВСЕГО' "
                            "GROUP BY company"):
            key = "company:" + r["company"]
            if key in have:
                continue
            base = nrm(r["company"])
            words = {t for t in re.split(r"[^0-9a-z\-]+", base) if t and t not in LEGAL_FORM and len(t) >= 3}
            words |= {t for t in re.split(r"[^0-9a-z]+", base) if t and t not in LEGAL_FORM and len(t) >= 3}
            name = re.sub(r"\s+AJ$", "", r["company"]).replace("'", "ʻ")
            out.append({"row_key": key, "name": "«%s» AJ" % name, "words": words, "base": base})
    return out


def _pretty(name: str) -> str:
    """«"APEX INSURANCE" AJ (ранее …)» → «APEX INSURANCE» AJ."""
    s = re.sub(r"\s*\(ранее[^)]*\)", "", name or "").strip()
    s = s.replace('"', "«", 1).replace('"', "»", 1)
    return s


def _ru_date(d: str) -> str:
    return "%s.%s.%s" % (d[8:10], d[5:7], d[:4]) if d and len(d) >= 10 else (d or "")


def period_label(d: str, lang: str) -> str:
    m = mp.MONTHS.get((d or "")[5:])
    if not m:
        return d or ""
    y = int(d[:4])
    if m == 12:
        return {"ru": "%d год" % (y - 1), "uz": "%d-yil" % (y - 1), "en": "FY %d" % (y - 1)}[lang]
    return {"ru": "%d · %d мес." % (y, m), "uz": "%d-yil %d oy" % (y, m), "en": "%d, %d months" % (y, m)}[lang]


def _num(x: Optional[float], lang: str, nd: int = 0) -> str:
    if x is None:
        return "—"
    s = f"{x:,.{nd}f}"
    if lang == "en":
        return s
    return s.replace(",", " ").replace(".", ",")


def _pct(x: Optional[float], lang: str, nd: int = 1, sign: bool = False) -> str:
    if x is None:
        return "—"
    s = _num(abs(x) if sign else x, lang, nd)
    if sign:
        s = ("+" if x >= 0 else "−") + s
    return s + " %"


def _r(x, n=2):
    return None if x is None else round(x, n)


def _dates(con) -> list:
    if not _has_table(con, "market_stats"):
        return []
    return [r["report_date"] for r in _rows(con, "SELECT DISTINCT report_date FROM market_stats "
                                                 "ORDER BY report_date")]


def _row(con, key: str, d: str) -> Optional[dict]:
    r = _rows(con, "SELECT report_date, row_key, row_name, premiums_ytd, payouts_ytd, liabilities, source_file "
                   "FROM market_stats WHERE row_key=? AND report_date=?", key, d)
    return r[0] if r else None


def _company_total(con, d: str) -> Optional[float]:
    r = _rows(con, "SELECT SUM(premiums_ytd) s FROM market_stats WHERE report_date=? AND row_key LIKE 'company:%' "
                   "AND premiums_ytd > 0", d)
    return r[0]["s"] if r and r[0]["s"] else None


def _prev_year(d: str) -> str:
    return "%04d%s" % (int(d[:4]) - 1, d[4:])


def _comparable(con, d: str, key: str) -> Optional[dict]:
    """Тот же срез год назад (сопоставимый период: ytd с тем же числом месяцев)."""
    return _row(con, key, _prev_year(d)) if d else None


def _lr(r) -> Optional[float]:
    return mp._loss_ratio(r) if r else None


def _rate(r) -> Optional[float]:
    return mp._annual_rate(r) if r else None


def _yoy(cur: Optional[float], old: Optional[float]) -> Optional[float]:
    if cur is None or not old:
        return None
    return (cur / old - 1) * 100


class Sources:
    """Плашка источника: файл отчёта НАПП и дата среза; одинаковые склеиваются."""

    def __init__(self, lang: str):
        self.lang, self.items = lang, []

    def napp(self, r: Optional[dict]):
        if not r:
            return
        f = (r.get("source_file") or "").replace("_", " ").strip()
        key = ("market", f, r["report_date"])
        if any((s["kind"], s.get("file"), s.get("date")) == key for s in self.items):
            return
        self.items.append({"kind": "market", "label": NAPP_LABEL[self.lang],
                           "title": mp.NAPP_TITLE + (" (%s)" % f if f else ""), "file": f or None,
                           "date": r["report_date"], "date_text": _ru_date(r["report_date"]),
                           "period": period_label(r["report_date"], self.lang),
                           "url": mp.NAPP_PAGE, "domain": "napp.uz"})

    def add(self, item: dict):
        if item not in self.items:
            self.items.append(item)

    def ensure(self, latest: Optional[str]):
        """Источник есть всегда: даже при «данных нет» показываем, где искали."""
        if not self.items:
            self.items.append({"kind": "market", "label": NAPP_LABEL[self.lang], "title": mp.NAPP_TITLE,
                               "file": None, "date": latest, "date_text": _ru_date(latest or ""),
                               "period": period_label(latest, self.lang) if latest else None,
                               "url": mp.NAPP_PAGE, "domain": "napp.uz"})
        return self.items


# --------------------------------------------------------------------------- #
#  docs/market_facts.json (строит другой агент; может не быть)
# --------------------------------------------------------------------------- #

_facts_cache = {"mtime": None, "items": [], "bad": 0}
_facts_lock = threading.Lock()
FACT_STR = ("topic", "period", "metric", "unit", "entity", "source_file", "source_date", "note")
FACT_NUM = ("value", "rank", "share_pct", "loss_ratio_pct", "yoy_pct")


def _clean_fact(f) -> Optional[dict]:
    """Факт из файла — недоверенные данные: строки обрезаются, числа проверяются."""
    if not isinstance(f, dict):
        return None
    out = {}
    for k in FACT_STR:
        v = f.get(k)
        if v is None:
            out[k] = None
        elif isinstance(v, (str, int, float)) and not isinstance(v, bool):
            out[k] = str(v).strip()[:300]
        else:
            return None
    for k in FACT_NUM:
        v = f.get(k)
        if v is None or v == "":
            out[k] = None
            continue
        if isinstance(v, bool):
            return None
        try:
            x = float(str(v).replace(" ", "").replace(",", "."))
        except ValueError:
            return None
        if x != x or abs(x) > 1e15:
            return None
        out[k] = x
    if not (out.get("topic") or out.get("metric")) or not out.get("source_file"):
        return None              # без источника факт не показываем
    return out


def facts_file() -> Optional[Path]:
    """Файл фактов: пересобранный сервером (STORAGE_DIR/knowledge) или из docs — более свежий по mtime."""
    from . import market_knowledge as mkn
    cands = [FACTS_FILE]
    if mkn.KNOWLEDGE is not None:
        cands.insert(0, mkn.KNOWLEDGE / "market_facts.json")
    best = None
    for p in cands:
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if best is None or mt > best[0]:
            best = (mt, p)
    return best[1] if best else None


def facts() -> list:
    path = facts_file()
    try:
        st = path.stat() if path is not None else None
    except OSError:
        st = None
    if st is None:
        with _facts_lock:
            _facts_cache.update({"mtime": None, "items": [], "bad": 0})
        return []
    stamp = (str(path), st.st_mtime)          # смена файла (docs ↔ постоянный диск) — тоже перечитать
    with _facts_lock:
        if _facts_cache["mtime"] == stamp:
            return _facts_cache["items"]
    items, bad, err = [], 0, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("facts") if isinstance(data, dict) else data
        for f in raw or []:
            c = _clean_fact(f)
            if c:
                items.append(c)
            else:
                bad += 1
    except Exception as e:
        err = str(e)[:300]
    if bad or err:
        # ошибки не глотаем: в журнал (раз на версию файла)
        try:
            with db.tx() as con:
                db.audit(con, "system", "market_facts.json: записи отброшены", db.stored_path(path),
                         {"отброшено": bad, "ошибка": err})
        except Exception as e:
            print("market_expert: журнал не записан:", e)
    with _facts_lock:
        _facts_cache.update({"mtime": stamp, "items": items, "bad": bad})
    return items


# подписи показателей сводки (русские; на uz/en факты отдаются только структурой market.facts)
FACT_METRIC_RU = {
    "ceded_share": "передано в перестрахование", "claims_received": "претензий получено",
    "contracts_active": "действующих договоров", "market_rate_annual": "рыночная ставка, % годовых",
    "top3_share": "доля трёх крупнейших", "top5_share": "доля пяти крупнейших",
    "top10_share": "доля десяти крупнейших", "hhi": "индекс концентрации ХХИ",
    "capital_share": "доля Ташкента в премиях", "insurers": "страховых организаций",
    "life_insurers": "из них по жизни", "brokers": "страховых брокеров", "agents": "страховых агентов",
    "agents_legal": "агентов — юрлиц", "actuaries": "актуариев", "capital": "уставный капитал страховщиков",
    "guarantee_fund": "членов Фонда гарантирования", "ceded_abroad_premiums": "передано иностранным перестраховщикам",
    "own_funds": "собственные средства", "reserves": "страховые резервы",
}
DUP_METRICS = {"premiums", "payouts", "liabilities", "market_rate_annual"}   # уже есть в ответе из market_stats
TOTAL_ENTITIES = ("весь рынок",)


def _fact_entity_ok(f: dict, ent: Optional[dict], intent: dict) -> bool:
    e = nrm(f.get("entity") or "")
    if ent and ent.get("type") == "company":
        return any(w in re.split(r"[^0-9a-z\-]+", e) for w in ent.get("words") or [])
    if ent and ent.get("type") == "class":
        key = ent.get("asked") or ent.get("key") or ""
        if key in ("osago", "osgor"):
            return ("осаго" if key == "osago" else "осгор") in e
        m = re.match(r"cls(\d+)$", key)
        return bool(m) and bool(re.match(r"класс %s\b" % m.group(1), e))
    if ent and ent.get("type") == "region":
        return e == nrm(ent.get("name") or "")
    if ent and ent.get("type") == "total" or not ent:
        if intent.get("rank") and not intent.get("dim"):
            return f.get("metric") in ("top3_share", "top5_share", "top10_share", "hhi")
        if intent.get("dim") == "region":
            return f.get("metric") == "capital_share"
        return any(e.startswith(t) for t in TOTAL_ENTITIES) and f.get("topic") in (
            "рынок", "участники рынка", "претензии", "перестрахование")
    return False


def match_facts(intent: dict, entity: Optional[dict], date: Optional[str], found: bool, limit: int = 5) -> list:
    """Факты сводки docs/market_facts.json к ответу: та же сущность и тот же срез.
    Если market_stats ответил — только дополнительные показатели (перестрахование, претензии, договоры…);
    если нет — всё, что есть по сущности, последние срезы первыми."""
    items = facts()
    if not items:
        return []
    out, seen = [], set()
    for f in items:
        sig = (f.get("metric"), f.get("value"), f.get("source_date"), nrm(f.get("entity") or ""))
        if sig in seen:
            continue                     # один факт лежит в сводке под двумя темами (INSON и компании)
        seen.add(sig)
        if found and f.get("metric") in DUP_METRICS:
            continue
        if f.get("topic") == "company_finance":
            continue                     # рэнкинг snsratings — не «сводка НАПП»: его отвечает answer_fin
        if not _fact_entity_ok(f, entity, intent):
            continue
        if found and date and f.get("source_date") != date and f.get("topic") != "участники рынка":
            continue
        y = intent.get("year")
        if not found and y and not ((f.get("source_date") or "").startswith(str(y))
                                    or f.get("source_date") == "%d-01-01" % (y + 1)):
            continue                     # спросили год, которого нет, — факты других лет не подставляем
        out.append(f)
    out.sort(key=lambda f: (f.get("source_date") or ""), reverse=True)
    if found and date:
        # «участники рынка» бывают только на итог года — берём последний такой срез
        last_part = max([f.get("source_date") or "" for f in out if f.get("topic") == "участники рынка"] or [""])
        out = [f for f in out if f.get("topic") != "участники рынка" or f.get("source_date") == last_part]
    return out[:limit]


def facts_line(fs: list, lang: str) -> str:
    """Короткая строка дополнительных показателей сводки (только ru: подписи показателей русские)."""
    if lang != "ru" or not fs:
        return ""
    parts = []
    for f in fs:
        name = FACT_METRIC_RU.get(f.get("metric") or "") or f.get("note") or f.get("metric")
        unit = f.get("unit") or ""
        nd = 3 if f.get("metric") == "market_rate_annual" else (1 if unit == "%" else 0)
        parts.append(("%s — %s%s%s" % (name, _num(f.get("value"), lang, nd), "" if unit == "%" else " ",
                                       unit if unit != "%" else " %")).rstrip(". "))
    return " Дополнительно по той же сводке НАПП: " + "; ".join(parts) + "."


# --------------------------------------------------------------------------- #
#  Распознавание
# --------------------------------------------------------------------------- #

def _find_class(q: str) -> tuple:
    """(row_key, сила, назван ли номер класса). Сначала явный номер класса, затем название вида."""
    for rx in CLASS_RE:
        m = rx.search(q)
        if m:
            n = m.group(1)
            return ("cls" + n, True, True) if 1 <= int(n) <= 18 else (None, False, False)
    for words, key, strong in CLASS_WORDS:
        if _has(q, words):
            return key, strong, False
    return None, False, False


# «капитал» — и показатель, и название страховщика KAPITAL SUG'URTA: «капитал APEX» — это APEX
CAPITAL_TOKENS = ("капитал", "kapital", "capital")


def _find_company(con, q: str) -> Optional[dict]:
    toks = _tokens(q)
    if any(t.startswith(CAPITAL_TOKENS) for t in toks):
        rest = " ".join(t for t in toks if not t.startswith(CAPITAL_TOKENS))
        other = _find_company_toks(con, rest, _tokens(rest)) if rest else None
        if other and not other["key"].startswith("company:KAPITAL"):
            return other
        # «у кого больше всего капитала», «рейтинг по капиталу» — показатель, а не KAPITAL SUG'URTA
        insurer_word = any(t.startswith(("sugurta", "сугурт", "страхов", "insurance")) for t in toks)
        if not other and not insurer_word and (_has(q, RANK_WORDS) or "у кого" in q or "больше всего" in q
                                               or "eng kop" in q):
            return None
    return _find_company_toks(con, q, toks)


def _find_company_toks(con, q: str, toks: list) -> Optional[dict]:
    alias = set()
    for a, w in COMPANY_ALIASES.items():
        if " " in a:
            if a in q:
                alias.add(w)
            continue
        if any(t == a or (len(a) >= 4 and t.startswith(a) and len(t) - len(a) <= 3) for t in toks):
            alias.add(w)
    best = None
    for c in companies(con):
        hit = 0
        for w in c["words"]:
            if w in alias or any(t == w or (len(w) >= 5 and t.startswith(w) and len(t) - len(w) <= 3)
                                 for t in toks):
                hit += 1
        if alias & {c["base"].split(" ")[0]}:
            hit += 1
        if hit and (not best or hit > best[0]):
            best = (hit, c)
    if not best:
        if "inson" in alias or "inson" in toks:
            # строки INSON в отчёте может не быть — компанию всё равно узнаём, ответ будет «данных нет»
            return {"type": "company", "key": mp.INSON_ROW, "name": "«INSON» AJ", "words": ["inson"]}
        return None
    c = best[1]
    return {"type": "company", "key": c["row_key"], "name": c["name"], "words": sorted(c["words"])}


def _find_region(q: str) -> Optional[dict]:
    if _has(q, ("ташкентск", "toshkent viloyat")):
        return {"type": "region", "key": "region:TOSHKENT", "name": "Ташкентская область"}
    extra = {"самарканд": "region:SAMARQAND", "ферган": "region:FARG'ONA", "бухар": "region:BUXORO",
             "андижан": "region:ANDIJON", "наманган": "region:NAMANGAN", "хорезм": "region:XORAZM",
             "навои": "region:NAVOIY", "джизак": "region:JIZZAX", "кашкадар": "region:QASHQADARYO",
             "сурхандар": "region:SURXANDARYO", "сырдар": "region:SIRDARYO", "каракалпак":
             "region:QORAQALPOG'ISTON RESPUBLIKASI", "ташкент": "region:TOSHKENT SHAHRI",
             "toshkent": "region:TOSHKENT SHAHRI", "tashkent": "region:TOSHKENT SHAHRI"}
    for name, key, _soato, aliases in mp.REGION_TABLE:
        for a in sorted(set(aliases) | {nrm(key.replace("region:", ""))}, key=len, reverse=True):
            a = nrm(a)
            if len(a) >= 5 and _has(q, (a,)):
                return {"type": "region", "key": key, "name": name}
    for stem_, key in extra.items():
        if _has(q, (stem_,)):
            name = next((n for n, k, _s, _a in mp.REGION_TABLE if k == key), key)
            return {"type": "region", "key": key, "name": name}
    return None


def _metric(q: str) -> Optional[str]:
    for key, words in METRICS:
        if key == "rate":
            if _has(q, ("ставк", "stavka", "rate")) and not _has(q, ("loss ratio",)):
                return "rate"
            continue
        if _has(q, words):
            return key
    return None


def _year(q: str) -> Optional[int]:
    m = re.search(r"\b(20[12]\d)\b", q)
    return int(m.group(1)) if m else None


# Условия продуктов других страховщиков — не статистика НАПП, а их документы (intent «competitor»)
PRODUCT_TERMS = ("франшиз", "исключени", "услови", "правил", "оферт", "износ", "лимит", "покрыти", "тариф",
                 "ставк", "franshiza", "istisno", "shart", "qoida", "oferta", "eskirish", "limit", "qoplam", "tarif",
                 "stavka", "deductible", "exclusion", "terms", "conditions", "rules", "wear", "coverage", "tariff",
                 "rate", "offer")
COMPETITOR_MARK = ("конкурент", "других страховщ", "другие страховщ", "другой страхов", "других компан",
                   "у других", "raqobatchi", "boshqa sugurtalovchi", "boshqa kompaniya", "competitor",
                   "other insurer", "other companies")
SELL_WORDS = ("что прода", "что предлага", "какие продукты", "nima sot", "qanday mahsulot", "what does",
              "sells", "sell", "products of")
COMPARE_WORDS = ("сравни", "сравнить", "solishtir", "compare")
# русские слова запроса к индексу (документы конкурентов — на русском)
RU_TERMS = {"franshiza": "франшиза", "istisno": "исключения", "shart": "условия", "qoida": "правила",
            "kasko": "каско", "mulk": "имущество", "yuk": "грузы", "kredit": "кредит", "limit": "лимит",
            "tarif": "тариф", "eskirish": "износ", "qoplam": "покрытие", "deductible": "франшиза",
            "exclusion": "исключения", "terms": "условия", "conditions": "условия", "rules": "правила",
            "casco": "каско", "property": "имущество", "cargo": "грузы", "credit": "кредит", "wear": "износ",
            "coverage": "покрытие", "tariff": "тариф", "rate": "тариф", "sell": "продукты", "nima": "продукты"}


def competitor_intent(q: str, comp: Optional[dict]) -> bool:
    """Вопрос об условиях продуктов других страховщиков (франшиза, исключения, «что продаёт Gross»)."""
    if _has(q, COMPARE_WORDS):
        return True
    if comp and comp.get("key") != mp.INSON_ROW and _has(q, SELL_WORDS):
        return True
    if _has(q, PRODUCT_TERMS) and (_has(q, COMPETITOR_MARK) or (comp and comp.get("key") != mp.INSON_ROW)):
        return True
    return False


def ru_query(question: str, lang: str) -> str:
    """Запрос к документам конкурентов на русском: для uz/en — переводим ключевые слова словарём."""
    if lang == "ru":
        return question
    words = []
    for t in _tokens(question):
        for k, v in RU_TERMS.items():
            if t.startswith(k) and v not in words:
                words.append(v)
    return " ".join(words) or question


def _fin_codes(q: str, comp: Optional[dict]) -> list:
    """Показатели рэнкинга, о которых спрашивают: «активы», «капитал», «резервы», «претензии», «отказы»…"""
    codes = []
    legal_verb = any(v in q for v in LEGAL_VERB) or _has(q, tuple(w for w in LEGAL_MARK
                                                                if w not in ("договор", "shartnoma", "contract")))
    for words, cs in FIN_WORDS:
        if not _has(q, words):
            continue
        if legal_verb and _has(q, AMBIG_FIN) and set(cs) & {"claims_received", "claims_refused"}:
            continue                            # «может ли страховщик отказать…» — вопрос о праве
        if cs[0] == "total_capital" and comp and comp["key"].startswith("company:KAPITAL") \
                and sum(t.startswith(CAPITAL_TOKENS) for t in _tokens(q)) < 2:
            continue                            # «капитал» здесь — название KAPITAL SUG'URTA
        if cs[0] == "total_capital" and "share_capital" in codes:
            continue                            # «уставный капитал» уже распознан
        for c in cs:
            if c not in codes:
                codes.append(c)
    if _has(q, CONTRACT_WORDS) and _has(q, COUNT_WORDS) and not legal_verb:
        for c in ("contracts_active", "contracts_new"):
            if c not in codes:
                codes.append(c)
    return codes


def detect(question: str, lang: str = "ru", last: Optional[dict] = None, con=None) -> dict:
    """Вопрос о рынке? → {is_market, entity, metric, rank, dim, year, ago, follow_up, strong}."""
    q = nrm(question)
    if con is None:
        with db.tx() as c:
            comp = _find_company(c, q)
    else:
        comp = _find_company(con, q)
    cls_key, cls_strong, cls_explicit = _find_class(q)
    region = None if comp else _find_region(q)
    metric = _metric(q)
    rank = _has(q, RANK_WORDS)
    market_word = _has(q, MARKET_WORDS)
    dim = "region" if _has(q, REGION_WORDS) and not region else (
        "class" if _has(q, CLASS_DIM_WORDS) and not cls_key else None)
    year, ago = _year(q), _has(q, AGO_WORDS)
    definition = any(q.startswith(d) or (" " + d) in q for d in DEFINITION)
    fin, card = _fin_codes(q, comp), bool(comp) and _has(q, CARD_WORDS)

    entity = None
    if comp:
        entity = comp
    elif cls_key:
        entity = {"type": "class", "key": cls_key, "strong": cls_strong}
    elif region:
        entity = region

    strong_entity = bool(comp or region or (cls_key and cls_strong))
    # «доля» у класса — чаще «какая доля кредита страхуется», а не доля рынка
    strong_metric = metric in ("loss_ratio", "growth", "rate", "own_funds") or (
        metric == "share" and (comp or market_word))
    not_market = any(x in q for x in NOT_MARKET)
    legal_like = _has(q, LEGAL_MARK)
    quantity = _has(q, QUANTITY)
    is_market = False
    if (definition or not_market) and not strong_entity:
        is_market = False
    elif market_word:
        is_market = True
    elif (comp or region or cls_explicit) and metric:
        is_market = True
    elif entity and strong_metric:
        is_market = True
    elif cls_key and cls_strong and metric and quantity and not legal_like:
        is_market = True
    elif dim and metric and not legal_like:
        is_market = True
    elif comp and metric is None and _has(q, ("сколько", "how much", "qancha", "показател", "итоги", "results",
                                             "natija")):
        is_market = True
    if not definition and (card or (fin and comp)):
        is_market = True                        # финансы компании или её карточка — из рэнкинга
    elif fin and not comp and not definition and (rank or market_word or "у кого" in q or "больше всего" in q
                                                  or "eng kop" in q or "most" in _tokens(q)):
        is_market, rank = True, True            # «у кого больше всего капитала» — рейтинг из рэнкинга

    follow = False
    words_n = len(_tokens(q))
    if not is_market and last and last.get("kind") == "market" and not definition and not legal_like:
        starts = any(q.startswith(s) for s in FOLLOW_START) or bool(re.search(r"chi\W*$", q))
        if (strong_entity or ago or year or metric or dim) and (starts or words_n <= 6):
            is_market = follow = True

    # «а по классу 8?» без контекста: сущность есть, показателя и периода нет — просим уточнить
    clarify = (not is_market and not definition and not legal_like and strong_entity and not metric
               and not rank and (any(q.startswith(s) for s in FOLLOW_START) or words_n <= 4))
    # условия продуктов других страховщиков: цифры рынка (премии, убыточность…) не перекрывают этот смысл
    competitor = competitor_intent(q, comp) and metric not in ("premiums", "payouts", "loss_ratio", "share",
                                                                "growth", "own_funds") and not fin and not card
    if competitor:
        is_market = clarify = follow = False
    return {"is_market": is_market, "clarify": clarify, "competitor": competitor,
            "entity": entity, "metric": metric, "rank": rank, "dim": dim,
            "year": year, "ago": ago, "follow_up": follow, "definition": definition,
            "market_word": market_word, "fin": fin, "card": card}


def resolve(intent: dict, last: Optional[dict]) -> dict:
    """Уточняющий вопрос: недостающее берём из прошлой реплики (сущность, показатель, срез)."""
    if not last or last.get("kind") != "market":
        return dict(intent, context_used=False)
    it = dict(intent)
    it["prev_entity"] = last.get("entity")
    used = []
    if it.get("follow_up") or (not it.get("entity") and not it.get("market_word") and not it.get("rank")):
        if not it.get("entity") and not it.get("rank") and not it.get("dim") and last.get("entity"):
            it["entity"] = last["entity"]
            used.append("entity")
        if not it.get("fin") and not it.get("metric") and not it.get("card") and last.get("fin"):
            it["fin"] = last["fin"]             # «а у APEX?» после «активы INSON» — тот же показатель рэнкинга
            used.append("fin")
        if not it.get("metric") and last.get("metric"):
            # у компании разреза по классам нет: показатель переносим, класс — нет
            it["metric"] = last["metric"]
            used.append("metric")
        if not it.get("rank") and not it.get("entity") and last.get("rank"):
            it["rank"] = True
            used.append("rank")
        if not it.get("dim") and not it.get("entity") and last.get("dim"):
            it["dim"] = last["dim"]
        if it.get("ago") and last.get("date"):
            it["base_date"] = last["date"]
            used.append("date")
        elif not it.get("year") and not it.get("ago") and last.get("date") and it.get("follow_up"):
            it["date"] = last["date"]
            used.append("date")
    it["context_used"] = bool(used)
    it["context_fields"] = used
    return it


# --------------------------------------------------------------------------- #
#  Ответ
# --------------------------------------------------------------------------- #

def _pick_date(con, it: dict, key: Optional[str]) -> Optional[str]:
    """Срез ответа: явный год → итог года (01.01 следующего); «год назад» → тот же срез годом раньше;
    иначе последний срез, где есть строка."""
    dates = _dates(con)
    if not dates:
        return None
    have = dates
    if key:
        have = [r["report_date"] for r in _rows(con, "SELECT report_date FROM market_stats WHERE row_key=? "
                                                     "ORDER BY report_date", key)] or dates
    if it.get("year"):
        y = it["year"]
        want = "%d-01-01" % (y + 1)
        if want in have:
            return want
        inyear = [d for d in have if d[:4] == str(y) and d[5:] != "01-01"]
        return inyear[-1] if inyear else None
    base = it.get("date") or it.get("base_date")
    if it.get("ago"):
        base = base or have[-1]
        want = _prev_year(base)
        return want if want in have else None
    if base and base in have:
        return base
    return have[-1]


def _class_label(con, key: str, lang: str) -> str:
    r = _rows(con, "SELECT row_name FROM market_stats WHERE row_key=? ORDER BY report_date DESC LIMIT 1", key)
    name = r[0]["row_name"] if r else key
    if lang == "ru":
        return name
    m = re.match(r"cls(\d+)$", key)
    if m:
        return ("%s-klass" if lang == "uz" else "Class %s") % m.group(1) + " (" + name.split("—")[-1].strip() + ")"
    return name


def _series_table(con, key: str, lang: str, src: Sources, upto: str) -> dict:
    """Ряд строки по срезам (до 10 последних) — премии, выплаты, убыточность, ставка, рост г/г."""
    pts = _rows(con, "SELECT report_date, row_name, premiums_ytd, payouts_ytd, liabilities, source_file "
                     "FROM market_stats WHERE row_key=? AND report_date<=? ORDER BY report_date", key, upto)
    pts = pts[-TABLE_MAX:]
    by = {p["report_date"]: p for p in _rows(con, "SELECT report_date, premiums_ytd FROM market_stats "
                                                  "WHERE row_key=?", key)}
    c = COLS[lang]
    rows = []
    for p in reversed(pts):
        old = by.get(_prev_year(p["report_date"]))
        rows.append([period_label(p["report_date"], lang) + " (" + _ru_date(p["report_date"]) + ")",
                     _r(p["premiums_ytd"], 1), _r(p["payouts_ytd"], 1), _r(_lr(p), 1),
                     _r(_rate(p), 3), _r(_yoy(p["premiums_ytd"], old["premiums_ytd"] if old else None), 1)])
        src.napp(p)
    return {"columns": [c["period"], c["premiums"], c["payouts"], c["loss_ratio"], c["rate"], c["yoy"]],
            "rows": rows}


def _companies_on(con, d: str) -> list:
    return _rows(con, "SELECT row_key, row_name, premiums_ytd, payouts_ytd, liabilities, source_file, report_date "
                      "FROM market_stats WHERE report_date=? AND row_key LIKE 'company:%' "
                      "AND premiums_ytd > 0", d)


def _rank_sort(rows: list, metric: str) -> list:
    if metric == "payouts":
        key = lambda r: -(r["payouts_ytd"] or 0)
    elif metric == "loss_ratio":
        # убыточность малых строк случайна: в рейтинг берём строки с премиями от 1% лидера
        top = max((r["premiums_ytd"] or 0) for r in rows) if rows else 0
        rows = [r for r in rows if (r["premiums_ytd"] or 0) >= top * 0.01]
        key = lambda r: -(_lr(r) or 0)
    else:
        key = lambda r: -(r["premiums_ytd"] or 0)
    return sorted(rows, key=key)


def _ord(n: int, lang: str) -> str:
    return {"ru": "%d-е место" % n, "uz": "%d-oʻrin" % n, "en": "rank %d" % n}[lang]


def answer_rank_companies(con, it: dict, lang: str, src: Sources) -> dict:
    d = _pick_date(con, it, None)
    rows = _companies_on(con, d) if d else []
    metric = it.get("metric") if it.get("metric") in ("payouts", "loss_ratio") else "premiums"
    if not rows:
        return {"found": False, "what": {"ru": "разреза по страховщикам", "uz": "sugʻurtalovchilar kesimi",
                                         "en": "breakdown by insurer"}[lang], "date": d}
    total = _company_total(con, d)
    ranked = _rank_sort(rows, metric)
    old_d = _prev_year(d)
    old = {r["row_key"]: r for r in _companies_on(con, old_d)}
    c = COLS[lang]
    table_rows = []
    for i, r in enumerate(ranked[:TABLE_MAX], 1):
        o = old.get(r["row_key"])
        table_rows.append([i, _pretty(r["row_name"]), _r(r["premiums_ytd"], 1), _r(r["payouts_ytd"], 1),
                           _r(_lr(r), 1), _r(r["premiums_ytd"] / total * 100 if total else None, 2),
                           _r(_yoy(r["premiums_ytd"], o["premiums_ytd"] if o else None), 1)])
        src.napp(r)
    if old:
        src.napp(next(iter(old.values())))
    lead = ranked[0]
    lo = old.get(lead["row_key"])
    share = lead["premiums_ytd"] / total * 100 if total else None
    yoy = _yoy(lead["premiums_ytd"], lo["premiums_ytd"] if lo else None)
    mname = METRIC_NAME[metric][lang]
    per = period_label(d, lang)
    if lang == "ru":
        if metric == "loss_ratio":
            head = "Самая высокая убыточность среди страховщиков на %s (%s, ytd) — %s: %s (премии %s %s, выплаты %s %s)." % (
                _ru_date(d), per, _pretty(lead["row_name"]), _pct(_lr(lead), lang),
                _num(lead["premiums_ytd"], lang), MLN[lang], _num(lead["payouts_ytd"], lang), MLN[lang])
        else:
            val = lead["payouts_ytd"] if metric == "payouts" else lead["premiums_ytd"]
            head = "Лидер рынка по %s на %s (%s, ytd) — %s: %s %s, %s — %s (страховщиков с премиями: %d)." % (
                "выплатам" if metric == "payouts" else "премиям", _ru_date(d), per, _pretty(lead["row_name"]),
                _num(val, lang), MLN[lang], SHARE_GENERAL[lang], _pct(share, lang), len(rows))
        nxt = ", ".join("%d) %s" % (i, _pretty(r["row_name"])) for i, r in enumerate(ranked[1:3], 2))
        text = head + (" Далее: %s." % nxt if nxt else "")
        if yoy is not None:
            text += " Премии лидера год к году (к %s): %s." % (_ru_date(old_d), _pct(yoy, lang, 1, True))
    elif lang == "uz":
        val = lead["payouts_ytd"] if metric == "payouts" else lead["premiums_ytd"]
        text = "%s (%s, ytd) holatiga koʻra %s boʻyicha bozor yetakchisi — %s: %s %s, %s — %s (mukofoti bor %d ta sugʻurtalovchi)." % (
            _ru_date(d), per, mname, _pretty(lead["row_name"]), _num(val if metric != "loss_ratio" else _lr(lead), lang, 0 if metric != "loss_ratio" else 1),
            MLN[lang] if metric != "loss_ratio" else "%", SHARE_GENERAL[lang], _pct(share, lang), len(rows))
        if yoy is not None:
            text += " Yetakchi mukofotlari yillik oʻsishi (%s ga nisbatan): %s." % (_ru_date(old_d), _pct(yoy, lang, 1, True))
    else:
        val = lead["payouts_ytd"] if metric == "payouts" else lead["premiums_ytd"]
        text = "Market leader by %s as of %s (%s, ytd): %s — %s %s, %s %s (%d insurers with premiums)." % (
            mname, _ru_date(d), per, _pretty(lead["row_name"]),
            _num(val if metric != "loss_ratio" else _lr(lead), lang, 0 if metric != "loss_ratio" else 1),
            MLN[lang] if metric != "loss_ratio" else "%", SHARE_GENERAL[lang], _pct(share, lang), len(rows))
        if yoy is not None:
            text += " Leader's premiums year on year (vs %s): %s." % (_ru_date(old_d), _pct(yoy, lang, 1, True))
    top3 = sum((r["premiums_ytd"] or 0) for r in ranked[:3]) / total * 100 if total and metric == "premiums" else None
    return {"found": True, "date": d, "text": text,
            "table": {"columns": [c["rank"], c["company"], c["premiums"], c["payouts"], c["loss_ratio"],
                                  c["share"], c["yoy"]], "rows": table_rows},
            "numbers": {"leader": lead["row_key"], "leader_name": _pretty(lead["row_name"]),
                        "premiums": lead["premiums_ytd"], "payouts": lead["payouts_ytd"],
                        "share_pct": _r(share, 2), "yoy_pct": _r(yoy, 1), "insurers": len(rows),
                        "top3_share_pct": _r(top3, 1), "metric": metric},
            "entity": None, "rank": True}


def answer_rank_dim(con, it: dict, lang: str, src: Sources, dim: str) -> dict:
    """Рейтинг регионов или классов (одиночные строки clsN + ОСАГО/ОСГОР)."""
    d = _pick_date(con, it, "total")
    if not d:
        return {"found": False, "what": "", "date": None}
    if dim == "region":
        rows = _rows(con, "SELECT * FROM market_stats WHERE report_date=? AND row_key LIKE 'region:%'", d)
    else:
        rows = [r for r in _rows(con, "SELECT * FROM market_stats WHERE report_date=?", d)
                if re.match(r"^cls\d+$", r["row_key"]) or r["row_key"] in ("osago", "osgor")]
    if not rows:
        return {"found": False, "what": {"ru": "разреза по регионам" if dim == "region" else "разреза по классам",
                                         "uz": "hududlar kesimi" if dim == "region" else "klasslar kesimi",
                                         "en": "breakdown by region" if dim == "region" else "breakdown by class"}[lang],
                "date": d}
    metric = it.get("metric") if it.get("metric") in ("payouts", "loss_ratio") else "premiums"
    ranked = _rank_sort(rows, metric)
    tot = _row(con, "total", d)
    totp = tot["premiums_ytd"] if tot else None
    old = {r["row_key"]: r for r in _rows(con, "SELECT * FROM market_stats WHERE report_date=?", _prev_year(d))}
    c = COLS[lang]
    trs = []
    for i, r in enumerate(ranked[:TABLE_MAX], 1):
        o = old.get(r["row_key"])
        trs.append([i, r["row_name"], _r(r["premiums_ytd"], 1), _r(r["payouts_ytd"], 1), _r(_lr(r), 1),
                    _r(r["premiums_ytd"] / totp * 100 if totp else None, 2),
                    _r(_yoy(r["premiums_ytd"], o["premiums_ytd"] if o else None), 1)])
        src.napp(r)
    lead = ranked[0]
    share = lead["premiums_ytd"] / totp * 100 if totp else None
    per = period_label(d, lang)
    label = {"ru": ("регион", "класс (строка отчёта)"), "uz": ("hudud", "klass"), "en": ("region", "class")}[lang]
    lab = label[0] if dim == "region" else label[1]
    if lang == "ru":
        by = {"premiums": "премиям", "payouts": "выплатам", "loss_ratio": "убыточности"}[metric]
        text = "На %s (%s, ytd) первый %s по %s — %s: премии %s %s, %s %s; убыточность %s." % (
            _ru_date(d), per, lab, by, lead["row_name"], _num(lead["premiums_ytd"], lang),
            MLN[lang], SHARE_MARKET[lang], _pct(share, lang), _pct(_lr(lead), lang))
        if dim == "class":
            text += " Пакеты нескольких классов (например «8, 9») в рейтинг не входят — это отдельные строки отчёта."
    elif lang == "uz":
        text = "%s (%s, ytd) holatiga koʻra birinchi %s — %s: mukofot %s %s, bozor ulushi %s; zararlilik %s." % (
            _ru_date(d), per, lab, lead["row_name"], _num(lead["premiums_ytd"], lang), MLN[lang],
            _pct(share, lang), _pct(_lr(lead), lang))
    else:
        text = "As of %s (%s, ytd) the top %s is %s: premiums %s %s, %s of the market; loss ratio %s." % (
            _ru_date(d), per, lab, lead["row_name"], _num(lead["premiums_ytd"], lang), MLN[lang],
            _pct(share, lang), _pct(_lr(lead), lang))
    return {"found": True, "date": d, "text": text,
            "table": {"columns": [c["rank"], c["region"] if dim == "region" else c["name"], c["premiums"],
                                  c["payouts"], c["loss_ratio"], c["share"], c["yoy"]], "rows": trs},
            "numbers": {"leader": lead["row_key"], "premiums": lead["premiums_ytd"], "share_pct": _r(share, 2)},
            "entity": None, "rank": True, "dim": dim}


def answer_company(con, it: dict, lang: str, src: Sources) -> dict:
    ent = it["entity"]
    key = ent["key"]
    d = _pick_date(con, it, key)
    r = _row(con, key, d) if d else None
    if not r and not it.get("year") and not it.get("ago"):
        rr = rk.company_rows(con, _ranking_company(ent))
        if rr and any(x["is_life"] for x in rr.values()):
            # страховщик жизни: в отчётах НАПП по общему страхованию его строки нет — карточка из рэнкинга
            return answer_fin(con, it, lang, src, card=True)
    if not r:
        return {"found": False, "date": d, "entity": ent,
                "what": {"ru": "строки страховщика %s%s" % (ent["name"], " на запрошенный срез" if d is None else
                                                              " на " + _ru_date(d)),
                         "uz": "%s qatori" % ent["name"], "en": "a line for %s" % ent["name"]}[lang]}
    all_ = _companies_on(con, d)
    total = _company_total(con, d)
    ranked = _rank_sort(all_, "premiums")
    place = next((i for i, x in enumerate(ranked, 1) if x["row_key"] == key), None)
    share = r["premiums_ytd"] / total * 100 if total and r["premiums_ytd"] is not None else None
    o = _comparable(con, d, key)
    yoy = _yoy(r["premiums_ytd"], o["premiums_ytd"] if o else None)
    mkt_old = _company_total(con, _prev_year(d))
    mkt_yoy = _yoy(total, mkt_old)
    src.napp(r)
    if o:
        src.napp(o)
    per = period_label(d, lang)
    lr = _lr(r)
    if lang == "ru":
        text = ("%s на %s (%s, ytd): премии %s %s, выплаты %s %s, убыточность %s, %s %s — "
                "%s из %d.") % (ent["name"], _ru_date(d), per, _num(r["premiums_ytd"], lang), MLN[lang],
                                _num(r["payouts_ytd"], lang), MLN[lang], _pct(lr, lang), SHARE_GENERAL[lang],
                                _pct(share, lang, 2),
                                _ord(place, lang) if place else "место не определено (премий нет)", len(all_))
        if yoy is not None:
            text += " Год к году (к %s): премии %s, премии страховщиков общего страхования %s." % (
                _ru_date(o["report_date"]), _pct(yoy, lang, 1, True), _pct(mkt_yoy, lang, 1, True))
        else:
            text += " Сопоставимого среза годом раньше в данных нет."
        if it.get("follow_up") and (it.get("prev_entity") or {}).get("type") == "class":
            text += " Разреза «страховщик × класс» в отчётах НАПП нет — показаны итоги компании по всем классам."
    elif lang == "uz":
        text = ("%s %s (%s, ytd): mukofot %s %s, toʻlov %s %s, zararlilik %s, %s %s — %s (%d tadan).") % (
            ent["name"], _ru_date(d), per, _num(r["premiums_ytd"], lang), MLN[lang], _num(r["payouts_ytd"], lang),
            MLN[lang], _pct(lr, lang), SHARE_GENERAL[lang], _pct(share, lang, 2),
            _ord(place, lang) if place else "—", len(all_))
        if yoy is not None:
            text += " Yillik oʻsish (%s ga nisbatan): mukofot %s, bozor %s." % (
                _ru_date(o["report_date"]), _pct(yoy, lang, 1, True), _pct(mkt_yoy, lang, 1, True))
    else:
        text = ("%s as of %s (%s, ytd): premiums %s %s, claims paid %s %s, loss ratio %s, %s %s — "
                "%s of %d.") % (ent["name"], _ru_date(d), per, _num(r["premiums_ytd"], lang), MLN[lang],
                                _num(r["payouts_ytd"], lang), MLN[lang], _pct(lr, lang), SHARE_GENERAL[lang],
                                _pct(share, lang, 2), _ord(place, lang) if place else "—", len(all_))
        if yoy is not None:
            text += " Year on year (vs %s): premiums %s, market %s." % (
                _ru_date(o["report_date"]), _pct(yoy, lang, 1, True), _pct(mkt_yoy, lang, 1, True))
    tbl = _series_table(con, key, lang, src, d)
    return {"found": True, "date": d, "text": text, "table": tbl, "entity": ent,
            "numbers": {"row_key": key, "premiums": r["premiums_ytd"], "payouts": r["payouts_ytd"],
                        "loss_ratio_pct": _r(lr, 2), "share_pct": _r(share, 2), "place": place,
                        "insurers": len(all_), "yoy_pct": _r(yoy, 1), "market_yoy_pct": _r(mkt_yoy, 1)}}


def answer_row(con, it: dict, lang: str, src: Sources) -> dict:
    """Класс / регион / итог рынка / обязательное страхование."""
    ent = it.get("entity") or {"type": "total", "key": "total"}
    key = ent["key"]
    alt = None
    if ent.get("type") == "class":
        m = re.match(r"cls(\d+)$", key)
        main = mp.CLASS_ROWS.get(m.group(1)) if m else None
        if main and main != key:
            # класс 8/9: основной объём в отчёте НАПП — пакетом «8, 9» (как в рыночном ориентире расчёта)
            alt, key = key, main
    d = _pick_date(con, it, key)
    r = _row(con, key, d) if d else None
    if not r:
        what = {"ru": "строки «%s»%s" % (_class_label(con, key, lang) if ent.get("type") != "region" else ent.get("name"),
                                        (" на " + _ru_date(d)) if d else " на запрошенный период"),
                "uz": "«%s» qatori" % key, "en": "a line «%s»" % key}[lang]
        return {"found": False, "date": d, "entity": ent, "what": what}
    tot = _row(con, "total", d)
    share = r["premiums_ytd"] / tot["premiums_ytd"] * 100 if tot and tot["premiums_ytd"] and key != "total" else None
    o = _comparable(con, d, key)
    yoy = _yoy(r["premiums_ytd"], o["premiums_ytd"] if o else None)
    lr, rate = _lr(r), _rate(r)
    lr_old = _lr(o) if o else None
    src.napp(r)
    if o:
        src.napp(o)
    name = ent.get("name") if ent.get("type") == "region" else _class_label(con, key, lang)
    if key == "total":
        name = {"ru": "Страховой рынок Узбекистана (все страховщики)", "uz": "Oʻzbekiston sugʻurta bozori",
                "en": "Uzbekistan insurance market"}[lang]
    per = period_label(d, lang)
    if lang == "ru":
        text = "%s на %s (%s, ytd): премии %s %s, выплаты %s %s, убыточность %s" % (
            name, _ru_date(d), per, _num(r["premiums_ytd"], lang), MLN[lang], _num(r["payouts_ytd"], lang),
            MLN[lang], _pct(lr, lang))
        if share is not None:
            text += ", %s %s" % (SHARE_MARKET[lang], _pct(share, lang))
        if rate is not None and ent.get("type") == "class":
            text += ", рыночная ставка %s годовых" % _pct(rate, lang, 3)
        text += "."
        if yoy is not None:
            text += " Год к году (к %s): премии %s, убыточность была %s." % (
                _ru_date(o["report_date"]), _pct(yoy, lang, 1, True), _pct(lr_old, lang))
        else:
            text += " Сопоставимого среза годом раньше в данных нет."
    elif lang == "uz":
        text = "%s %s (%s, ytd): mukofot %s %s, toʻlov %s %s, zararlilik %s" % (
            name, _ru_date(d), per, _num(r["premiums_ytd"], lang), MLN[lang], _num(r["payouts_ytd"], lang),
            MLN[lang], _pct(lr, lang))
        if share is not None:
            text += ", %s %s" % (SHARE_MARKET[lang], _pct(share, lang))
        if rate is not None and ent.get("type") == "class":
            text += ", bozor stavkasi yillik %s" % _pct(rate, lang, 3)
        text += "."
        if yoy is not None:
            text += " Yillik oʻsish (%s ga nisbatan): mukofot %s." % (_ru_date(o["report_date"]), _pct(yoy, lang, 1, True))
    else:
        text = "%s as of %s (%s, ytd): premiums %s %s, claims paid %s %s, loss ratio %s" % (
            name, _ru_date(d), per, _num(r["premiums_ytd"], lang), MLN[lang], _num(r["payouts_ytd"], lang),
            MLN[lang], _pct(lr, lang))
        if share is not None:
            text += ", %s %s" % (SHARE_MARKET[lang], _pct(share, lang))
        if rate is not None and ent.get("type") == "class":
            text += ", market rate %s p.a." % _pct(rate, lang, 3)
        text += "."
        if yoy is not None:
            text += " Year on year (vs %s): premiums %s." % (_ru_date(o["report_date"]), _pct(yoy, lang, 1, True))
    alt_nums = None
    if alt:
        a = _row(con, alt, d)
        if a:
            src.napp(a)
            alt_nums = {"row_key": alt, "premiums": a["premiums_ytd"], "payouts": a["payouts_ytd"],
                        "loss_ratio_pct": _r(_lr(a), 2)}
            n = alt[3:]
            text += {"ru": " Класс %s в отчёте НАПП идёт в основном пакетом «%s» (цифры выше); отдельная строка "
                           "класса %s: премии %s %s, выплаты %s %s, убыточность %s.",
                     "uz": " %s-klass NAPP hisobotida asosan «%s» paketi bilan (yuqoridagi raqamlar); alohida %s-klass "
                           "qatori: mukofot %s %s, toʻlov %s %s, zararlilik %s.",
                     "en": " In NAPP reports class %s mostly goes in the package «%s» (figures above); the separate "
                           "class %s line: premiums %s %s, claims paid %s %s, loss ratio %s."}[lang] % (
                n, key[3:].replace("_", ", "), n, _num(a["premiums_ytd"], lang), MLN[lang],
                _num(a["payouts_ytd"], lang), MLN[lang], _pct(_lr(a), lang))
    if it.get("follow_up") and it.get("metric") == "share" and (it.get("prev_entity") or {}).get("type") == "company":
        text += {"ru": " Доли страховщика по классу в отчётах НАПП нет.", "uz": "", "en": ""}[lang]
    tbl = _series_table(con, key, lang, src, d)
    return {"found": True, "date": d, "text": text, "table": tbl, "entity": dict(ent, row_key=key, asked=ent["key"]),
            "numbers": {"row_key": key, "premiums": r["premiums_ytd"], "payouts": r["payouts_ytd"],
                        "loss_ratio_pct": _r(lr, 2), "rate_pct": _r(rate, 3), "share_pct": _r(share, 2),
                        "yoy_pct": _r(yoy, 1), "loss_ratio_prev_pct": _r(lr_old, 2), "alt": alt_nums}}


def answer_own_funds(con, it: dict, lang: str, src: Sources) -> dict:
    if not _has_table(con, "company_financials"):
        return {"found": False, "what": "", "date": None}
    r = _rows(con, "SELECT * FROM company_financials ORDER BY report_date DESC LIMIT 1")
    if not r:
        return {"found": False, "date": None, "what": {"ru": "данных о собственных средствах INSON",
                                                       "uz": "INSON oʻz mablagʻlari", "en": "INSON own funds data"}[lang]}
    r = r[0]
    src.add({"kind": "company", "label": COMPANY_LABEL[lang], "title": "company_financials: " + (r["source"] or ""),
             "file": None, "date": r["report_date"], "date_text": _ru_date(r["report_date"]), "url": None,
             "domain": None})
    text = {"ru": "INSON на %s: собственные средства %s сум, страховые резервы %s сум (источник: %s).",
            "uz": "INSON %s: oʻz mablagʻlari %s soʻm, sugʻurta zaxiralari %s soʻm (manba: %s).",
            "en": "INSON as of %s: own funds UZS %s, insurance reserves UZS %s (source: %s)."}[lang] % (
        _ru_date(r["report_date"]), _num(r["own_funds"], lang), _num(r["reserves"], lang), r["source"] or "—")
    limit = 0.2 * (r["own_funds"] + r["reserves"])
    text += {"ru": " Лимит на один риск по Положению № 1806, п. 15 = 20 %% × (собственные средства + резервы) = %s сум.",
             "uz": " Bitta xavf limiti (1806-son Nizom, 15-band) = 20 %% × (oʻz mablagʻlari + zaxiralar) = %s soʻm.",
             "en": " Per-risk limit (Regulation 1806, para. 15) = 20 %% × (own funds + reserves) = UZS %s."}[lang] % (
        _num(limit, lang))
    extra, parts = _inson_ranking_extra(con, lang, src)
    text += extra
    return {"found": True, "date": r["report_date"], "text": text, "table": None, "entity": None,
            "numbers": {"own_funds": r["own_funds"], "reserves": r["reserves"], "source": r["source"],
                        "limit_per_risk": limit, "ranking": parts or None}}


# --------------------------------------------------------------------------- #
#  Финансы страховщиков: рэнкинг snsratings.uz (company_rankings)
# --------------------------------------------------------------------------- #

RANK_LABEL = {"ru": "рэнкинг страховщиков", "uz": "sugʻurtalovchilar reytingi", "en": "insurer ranking"}
RANK_DATE = "2026-07-01"           # II кв. 2026: остатки — на 30.06.2026, потоки — с начала года по 30.06
RANK_NOTE = {
    "ru": "рэнкинг snsratings.uz по отчётности компаний (openinfo.uz) и НАПП, без проверки проектом: остатки "
          "(активы, капитал, резервы) — на 30.06, потоки (премии, выплаты, прибыль, претензии) — с начала года; "
          "выплаты — оплаченные; место — как в рэнкинге",
    "uz": "snsratings.uz reytingi (openinfo.uz va NAPP maʼlumotlari): qoldiqlar — 30.06 holatiga, oqimlar — yil "
          "boshidan; oʻrin — reytingdagidek",
    "en": "snsratings.uz ranking (company filings on openinfo.uz and NAPP data): balances as of 30 June, flows "
          "year to date; rank as in the ranking",
}
# названия показателей на uz/en (ru — из базы, как в содержании документа)
FIN_NAME = {
    "total_assets": ("Jami aktivlar", "Total assets"),
    "total_capital": ("Jami kapital", "Total equity"),
    "share_capital": ("Aksiyadorlik kapitali", "Share capital"),
    "reserves_gross": ("Sugʻurta zaxiralari, brutto", "Insurance reserves, gross"),
    "reserves_net": ("Sugʻurta zaxiralari, sof", "Insurance reserves, net"),
    "net_profit": ("Sof foyda (zarar)", "Net profit (loss)"),
    "profit_before_tax": ("Soliqqacha foyda", "Profit before tax"),
    "roe": ("Kapital rentabelligi", "Return on equity"),
    "roa": ("Aktivlar rentabelligi", "Return on assets"),
    "roe_share_capital": ("Aksiyadorlik kapitali rentabelligi", "Return on share capital"),
    "claims_received": ("Kelib tushgan daʼvolar", "Claims received"),
    "claims_paid_count": ("Toʻlangan daʼvolar", "Claims paid (number)"),
    "claims_refused": ("Rad etilgan daʼvolar", "Claims refused"),
    "claims_unsettled": ("Hal qilinmagan daʼvolar", "Claims unsettled"),
    "refused_to_claims": ("Rad etilganlar ulushi", "Refusal share"),
    "premiums_total": ("Jami yigʻilgan mukofotlar", "Total premiums written"),
    "claims_paid": ("Sugʻurta toʻlovlari", "Claims paid"),
    "payouts_to_premiums": ("Toʻlovlar / mukofotlar", "Claims paid / premiums"),
    "contracts_active": ("Amaldagi shartnomalar", "Contracts in force"),
    "contracts_new": ("Tuzilgan shartnomalar", "Contracts concluded"),
    "branches": ("Hududiy boʻlinmalar", "Branches"),
}
UNIT = {"млн сум": {"ru": "млн сум", "uz": "mln soʻm", "en": "UZS m"},
        "млрд сум": {"ru": "млрд сум", "uz": "mlrd soʻm", "en": "UZS bn"},
        "кол-во": {"ru": "шт.", "uz": "ta", "en": ""},
        "сум": {"ru": "сум", "uz": "soʻm", "en": "UZS"},
        "%": {"ru": "%", "uz": "%", "en": "%"}}
# карточка компании: что показываем и в каком порядке
CARD_CODES = ["premiums_total", "total_assets", "total_capital", "share_capital", "reserves_gross", "reserves_net",
              "net_profit", "roe", "roa", "claims_paid", "payouts_to_premiums", "claims_received", "claims_paid_count",
              "claims_refused", "refused_to_claims", "contracts_active", "branches"]
# относительные показатели, где больше — хуже (для вывода «лучше / хуже рынка»)
WORSE_IF_HIGHER = {"refused_to_claims", "payouts_to_premiums", "cost_to_premiums"}


def _fin_name(r: dict, lang: str) -> str:
    if lang == "ru":
        return r["indicator_name"]
    pair = FIN_NAME.get(r["indicator_code"])
    return pair[0 if lang == "uz" else 1] if pair else r["indicator_name"]


def _fin_val(v, unit: str, lang: str) -> str:
    if v is None:
        return "—"
    if unit == "%":
        return _pct(v, lang, 2)
    nd = 0 if unit == "кол-во" else 1
    u = UNIT.get(unit, {}).get(lang, unit)
    return (_num(v, lang, nd) + (" " + u if u else "")).strip()


def _fin_place(rank, n: int, lang: str) -> str:
    if rank is None:
        return {"ru": "место не указано", "uz": "oʻrin koʻrsatilmagan", "en": "no rank"}[lang]
    return {"ru": "%d-е место из %d" % (rank, n), "uz": "%d-oʻrin (%d tadan)" % (rank, n),
            "en": "rank %d of %d" % (rank, n)}[lang]


def _fin_src(src: Sources, con, lang: str):
    f = con.execute("SELECT MAX(source_file) FROM company_rankings").fetchone()
    src.add({"kind": "market", "label": RANK_LABEL[lang], "title": rk.SOURCE_LABEL, "file": f[0] if f else None,
             "date": RANK_DATE, "date_text": "30.06.2026", "period": "II кв. 2026" if lang == "ru" else
             ("2026-yil II chorak" if lang == "uz" else "Q2 2026"), "url": rk.SOURCE_URL, "domain": "snsratings.uz"})


def _fin_line(r: dict, n: int, lang: str) -> str:
    """«совокупные активы 272 856,7 млн сум — 18-е место из 36, доля 1,61 %; год назад 207 277,6 млн сум
    (18-е место); изменение +31,6 %»."""
    u = r["unit"]
    s = "%s %s — %s" % (_fin_name(r, lang).lower() if lang == "ru" else _fin_name(r, lang),
                        _fin_val(r["value_cur"], u, lang), _fin_place(r["rank_cur"], n, lang))
    if r["share_cur"] is not None:
        s += {"ru": ", доля %s", "uz": ", ulush %s", "en": ", share %s"}[lang] % _pct(r["share_cur"], lang, 2)
    if r["value_prev"] is not None:
        prev_place = (_ord(r["rank_prev"], lang) if r["rank_prev"] is not None else
                      {"ru": "место не указано", "uz": "oʻrin koʻrsatilmagan", "en": "no rank"}[lang])
        s += {"ru": "; год назад %s (%s)", "uz": "; bir yil oldin %s (%s)", "en": "; a year ago %s (%s)"}[lang] % (
            _fin_val(r["value_prev"], u, lang), prev_place)
    else:
        s += {"ru": "; год назад данных нет", "uz": "; bir yil oldin maʼlumot yoʻq",
              "en": "; no data a year ago"}[lang]
    if r["change_pct"] is not None and r["value_prev"] is not None:
        s += {"ru": ", изменение %s", "uz": ", oʻzgarish %s", "en": ", change %s"}[lang] % _pct(
            r["change_pct"], lang, 1, True)
    elif u == "%" and r["change_pp"] is not None:
        s += {"ru": ", изменение %s п.п.", "uz": ", oʻzgarish %s p.p.", "en": ", change %s pp"}[lang] % (
            _pct(r["change_pp"], lang, 2, True).replace(" %", ""))
    return s


def _fin_table(rows: list, n_by: dict, lang: str) -> dict:
    cols = {"ru": ["Показатель", "II кв. 2025", "Место", "II кв. 2026", "Место", "Доля, %", "Изменение"],
            "uz": ["Koʻrsatkich", "2025 II chorak", "Oʻrin", "2026 II chorak", "Oʻrin", "Ulush, %", "Oʻzgarish"],
            "en": ["Indicator", "Q2 2025", "Rank", "Q2 2026", "Rank", "Share, %", "Change"]}[lang]
    out = []
    for r in rows[:TABLE_MAX * 2]:
        chg = (_pct(r["change_pct"], lang, 1, True) if r["change_pct"] is not None and r["value_prev"] is not None
               else (_pct(r["change_pp"], lang, 2, True).replace(" %", " п.п." if lang == "ru" else " pp")
                     if r["unit"] == "%" and r["change_pp"] is not None else "—"))
        out.append([_fin_name(r, lang), _fin_val(r["value_prev"], r["unit"], lang), r["rank_prev"],
                     _fin_val(r["value_cur"], r["unit"], lang), r["rank_cur"],
                     _r(r["share_cur"], 2), chg])
    return {"columns": cols, "rows": out}


def _vs_market(con, r: dict, lang: str) -> Optional[str]:
    """Относительный показатель против итога рынка — для вывода специалиста (правило, не данные)."""
    if r["unit"] != "%" or r["value_cur"] is None:
        return None
    t = rk.total_row(con, r["indicator_code"], r["report_period"])
    if not t or t["value_cur"] is None:
        return None
    hi = r["value_cur"] > t["value_cur"]
    if lang == "ru":
        word = "выше" if hi else "ниже"
        tail = ""
        if r["indicator_code"] in WORSE_IF_HIGHER:
            tail = " — хуже рынка" if hi else " — лучше рынка"
        elif r["indicator_code"] in ("roe", "roa", "roe_share_capital", "capital_to_assets"):
            tail = " — лучше рынка" if hi else " — хуже рынка"
        return "%s %s среднего по рынку (%s против %s)%s" % (_fin_name(r, lang).lower(), word,
                                                              _pct(r["value_cur"], lang, 2), _pct(t["value_cur"], lang, 2),
                                                              tail)
    return "%s: %s vs %s" % (_fin_name(r, lang), _pct(r["value_cur"], lang, 2), _pct(t["value_cur"], lang, 2))


def _ranking_company(ent: dict) -> str:
    return (ent.get("key") or "").split(":", 1)[-1]


def answer_fin(con, it: dict, lang: str, src: Sources, codes: Optional[list] = None, card: bool = False) -> dict:
    """Показатели компании из рэнкинга: значение, место, доля, изменение к II кв. 2025."""
    ent = it.get("entity") or {"type": "company", "key": mp.INSON_ROW, "name": "«INSON» AJ"}
    comp = _ranking_company(ent)
    period = rk.latest_period(con)
    data = rk.company_rows(con, comp, period) if period else {}
    if not data:
        return {"found": False, "date": None, "entity": ent, "ranking": True,
                "what": {"ru": "строк %s в рэнкинге страховщиков snsratings.uz" % ent.get("name", comp),
                         "uz": "%s reytingda yoʻq" % ent.get("name", comp),
                         "en": "%s in the snsratings.uz ranking" % ent.get("name", comp)}[lang]}
    codes = CARD_CODES if card else (codes or [])
    rows = [data[c] for c in codes if c in data]
    missing = [c for c in codes if c not in data]
    if not rows:
        return {"found": False, "date": RANK_DATE, "entity": ent, "ranking": True,
                "what": {"ru": "этого показателя у %s в рэнкинге" % ent.get("name", comp),
                         "uz": "bu koʻrsatkich reytingda yoʻq", "en": "this indicator in the ranking"}[lang]}
    n_by = {r["indicator_code"]: rk.companies_count(con, r["indicator_code"], period) for r in rows}
    _fin_src(src, con, lang)
    name = ent.get("name") or comp
    life = any(r["is_life"] for r in rows)
    if card:
        g = lambda c: data.get(c)  # noqa: E731
        bits = []
        for c in ("premiums_total", "total_assets", "total_capital", "reserves_net", "net_profit", "roe",
                  "payouts_to_premiums", "claims_received", "claims_refused", "refused_to_claims"):
            if g(c):
                bits.append(_fin_line(g(c), n_by.get(c) or rk.companies_count(con, c, period), lang))
        head = {"ru": "%s по рэнкингу страховщиков snsratings.uz (II кв. 2026, на 30.06.2026): ",
                "uz": "%s — snsratings.uz reytingi (2026-yil II chorak, 30.06.2026): ",
                "en": "%s in the snsratings.uz insurer ranking (Q2 2026, as of 30 June 2026): "}[lang] % name
        text = head + "; ".join(bits).rstrip(".") + "."
    else:
        text = {"ru": "%s по рэнкингу страховщиков snsratings.uz (II кв. 2026, на 30.06.2026): ",
                "uz": "%s — snsratings.uz reytingi (2026-yil II chorak, 30.06.2026): ",
                "en": "%s in the snsratings.uz insurer ranking (Q2 2026, as of 30 June 2026): "}[lang] % name
        text += "; ".join(_fin_line(r, n_by[r["indicator_code"]], lang) for r in rows).rstrip(".") + "."
    if life:
        text += {"ru": " Компания — страховщик жизни: в отчётах НАПП по общему страхованию её строки нет.",
                 "uz": " Hayot sugʻurtasi kompaniyasi.", "en": " A life insurer."}[lang]
    if missing:
        text += {"ru": " В рэнкинге нет: %s.", "uz": " Reytingda yoʻq: %s.", "en": " Not in the ranking: %s."}[lang] % (
            ", ".join(missing))
    vs = [x for x in (_vs_market(con, r, lang) for r in rows) if x]
    nums = {"company": comp, "period": period, "indicators": {
        r["indicator_code"]: {"value": r["value_cur"], "unit": r["unit"], "rank": r["rank_cur"],
                              "share_pct": r["share_cur"], "value_prev": r["value_prev"], "rank_prev": r["rank_prev"],
                              "change_pct": r["change_pct"], "change_pp": r["change_pp"],
                              "insurers": n_by[r["indicator_code"]], "page": r["page"]} for r in rows},
            "is_life": life, "source": rk.SOURCE_LABEL, "metric": "card" if card else "fin"}
    return {"found": True, "date": RANK_DATE, "text": text, "table": _fin_table(rows, n_by, lang),
            "entity": ent, "numbers": nums, "ranking": True, "vs_market": vs}


def answer_rank_fin(con, it: dict, lang: str, src: Sources) -> dict:
    """«У кого больше всего капитала» — первые 10 по главному показателю вопроса."""
    code = (it.get("fin") or ["total_assets"])[0]
    period = rk.latest_period(con)
    rows = rk.indicator_rows(con, code, period) if period else []
    if not rows:
        return {"found": False, "date": None, "ranking": True,
                "what": {"ru": "рэнкинга страховщиков по этому показателю", "uz": "bu koʻrsatkich boʻyicha reyting",
                         "en": "an insurer ranking for this indicator"}[lang]}
    _fin_src(src, con, lang)
    n = len(rows)
    lead = rows[0]
    tot = rk.total_row(con, code, period)
    cols = {"ru": ["Место", "Страховщик", "II кв. 2026", "Доля, %", "Место год назад", "Изменение"],
            "uz": ["Oʻrin", "Sugʻurtalovchi", "2026 II chorak", "Ulush, %", "Oʻtgan yil oʻrni", "Oʻzgarish"],
            "en": ["Rank", "Insurer", "Q2 2026", "Share, %", "Rank a year ago", "Change"]}[lang]
    trs = []
    for r in rows[:TABLE_MAX]:
        chg = (_pct(r["change_pct"], lang, 1, True) if r["change_pct"] is not None and r["value_prev"] is not None
               else "—")
        trs.append([r["rank_cur"], r["company_raw"], _fin_val(r["value_cur"], r["unit"], lang), _r(r["share_cur"], 2),
                    r["rank_prev"], chg])
    nm = _fin_name(lead, lang)
    text = {"ru": "%s — первое место в рэнкинге snsratings.uz (II кв. 2026) по показателю «%s»: %s",
            "uz": "%s — snsratings.uz reytingida (2026-yil II chorak) «%s» boʻyicha birinchi: %s",
            "en": "%s ranks first in the snsratings.uz ranking (Q2 2026) by «%s»: %s"}[lang] % (
        lead["company_raw"], nm, _fin_val(lead["value_cur"], lead["unit"], lang))
    if lead["share_cur"] is not None:
        text += {"ru": ", доля %s", "uz": ", ulush %s", "en": ", share %s"}[lang] % _pct(lead["share_cur"], lang, 2)
    if tot and tot["value_cur"] is not None:
        text += {"ru": "; по рынку %s", "uz": "; bozor boʻyicha %s", "en": "; market %s"}[lang] % _fin_val(
            tot["value_cur"], tot["unit"], lang)
    text += {"ru": ". Компаний в рэнкинге: %d.", "uz": ". Reytingda %d ta kompaniya.",
             "en": ". Insurers in the ranking: %d."}[lang] % n
    ins = next((r for r in rows if r["company"] == "INSON AJ"), None)
    if ins and lead["company"] != "INSON AJ":
        text += {"ru": " INSON — %s (%s).", "uz": " INSON — %s (%s).", "en": " INSON — %s (%s)."}[lang] % (
            _fin_place(ins["rank_cur"], n, lang), _fin_val(ins["value_cur"], ins["unit"], lang))
    return {"found": True, "date": RANK_DATE, "text": text, "table": {"columns": cols, "rows": trs},
            "numbers": {"metric": code, "leader": lead["company"], "leader_value": lead["value_cur"],
                        "unit": lead["unit"], "insurers": n, "inson_rank": ins["rank_cur"] if ins else None},
            "entity": None, "rank": True, "ranking": True}


def _inson_ranking_extra(con, lang: str, src: Sources) -> tuple:
    """Разбивка собственных средств INSON из рэнкинга — к ответу о собственных средствах."""
    data = rk.company_rows(con, "INSON AJ")
    if not data:
        return "", {}
    _fin_src(src, con, lang)
    per = rk.latest_period(con)
    parts = [_fin_line(data[c], rk.companies_count(con, c, per), lang)
             for c in ("total_capital", "share_capital", "reserves_gross", "reserves_net", "total_assets") if c in data]
    t = {"ru": " По рэнкингу snsratings.uz (II кв. 2026): %s.", "uz": " snsratings.uz reytingi boʻyicha: %s.",
         "en": " Per the snsratings.uz ranking (Q2 2026): %s."}[lang] % "; ".join(parts).rstrip(".")
    return t, {c: data[c]["value_cur"] for c in data if c in ("total_capital", "share_capital", "reserves_gross",
                                                                 "reserves_net", "total_assets")}



def opinion(res: dict, it: dict, lang: str) -> Optional[str]:
    """Вывод по цифрам — правилами, без модели. Помечается как мнение, а не данные."""
    n = res.get("numbers") or {}
    out = []
    lr = n.get("loss_ratio_pct")
    if lr is not None:
        if lr >= 70:
            out.append({"ru": "убыточность высокая (≥ 70 %): запаса в ставке почти нет",
                        "uz": "zararlilik yuqori (≥ 70 %)", "en": "loss ratio is high (≥ 70 %)"}[lang])
        elif lr <= 25:
            out.append({"ru": "убыточность низкая (≤ 25 %): по выплатам вид прибыльный, но выплаты НАПП — оплаченные, "
                              "а не произошедшие убытки", "uz": "zararlilik past (≤ 25 %)",
                        "en": "loss ratio is low (≤ 25 %); NAPP shows paid, not incurred, claims"}[lang])
    y, my = n.get("yoy_pct"), n.get("market_yoy_pct")
    if y is not None and my is not None:
        if y > my:
            out.append({"ru": "растёт быстрее рынка (%s против %s)" % (_pct(y, lang, 1, True), _pct(my, lang, 1, True)),
                        "uz": "bozordan tezroq oʻsmoqda", "en": "growing faster than the market"}[lang])
        else:
            out.append({"ru": "растёт медленнее рынка (%s против %s) — доля сокращается" % (
                _pct(y, lang, 1, True), _pct(my, lang, 1, True)),
                        "uz": "bozordan sekinroq oʻsmoqda", "en": "growing slower than the market"}[lang])
    if n.get("top3_share_pct"):
        out.append({"ru": "рынок концентрирован: на трёх крупнейших приходится %s премий" % _pct(n["top3_share_pct"], lang),
                    "uz": "bozor jamlangan: uchta yirik kompaniyaga %s" % _pct(n["top3_share_pct"], lang),
                    "en": "the market is concentrated: the top three hold %s" % _pct(n["top3_share_pct"], lang)}[lang])
    if n.get("rate_pct") is not None and (it.get("entity") or {}).get("type") == "class":
        out.append({"ru": "рыночная ставка — средняя по строке отчёта (премии к обязательствам), а не тариф "
                          "конкретного договора", "uz": "bozor stavkasi — hisobot qatori boʻyicha oʻrtacha",
                    "en": "the market rate is an average for the report line, not a contract tariff"}[lang])
    out.extend(res.get("vs_market") or [])
    if not out:
        return None
    return "; ".join(out) + "."


MARKET_SYSTEM = ("Ты — «ИИ специалист по страхованию INSON», %s: специалист по страховому рынку Узбекистана. "
                 "Отвечай только по данным — по переданным фактам (JSON ниже): не выдумывай компании, цифры, "
                 "даты и источники. Если в фактах ответа нет — так и "
                 "скажи. Цифры приводи как в фактах (млн сум, %%), с датой среза; ytd — нарастающим итогом с "
                 "начала года. Свои выводы помечай словом «Вывод:». 2–5 предложений. Язык ответа строго: %s.")


def ai_retell(question: str, facts_: dict, lang: str, history: list) -> dict:
    if not llm.enabled():
        return {"status": "off", "text": None}
    hist = "\n".join("%s: %s" % ("Пользователь" if h["role"] == "user" else "Специалист", h["text"][:300])
                     for h in (history or [])[-6:])
    user = ("Контекст диалога:\n%s\n\nВопрос: %s\n\nФакты:\n%s" % (hist or "—", question,
                                                                 json.dumps(facts_, ensure_ascii=False)[:6000]))
    try:
        text = llm.chat("рынок: пересказ по данным", MARKET_SYSTEM % (ROLE["ru"], lang), user, max_tokens=400,
                        timeout=8)
    except Exception as e:
        return {"status": "error", "text": None, "reason": str(e)[:200]}
    if not text:
        return {"status": "error", "text": None, "reason": (llm.last_error or {}).get("text")}
    return {"status": "ok", "text": text.strip(), "label": AI_LABEL[lang]}


def answer(question: str, lang: str, it: dict, with_ai: bool = False, history: Optional[list] = None) -> dict:
    """Ответ по рынку из данных. Возвращает блок market + текст, источники и контекст для памяти."""
    lang = lang if lang in ROLE else "ru"
    src = Sources(lang)
    with db.tx() as con:
        dates = _dates(con)
        latest = dates[-1] if dates else None
        ent = it.get("entity") or {}
        fin = it.get("fin") or []
        own_words = it.get("metric") == "own_funds" and not (set(fin) - {"reserves_net", "reserves_gross"})
        if it.get("card") and ent.get("type") == "company":
            res = answer_fin(con, it, lang, src, card=True)
        elif own_words and (not ent or ent.get("key") == mp.INSON_ROW):
            res = answer_own_funds(con, it, lang, src)
        elif fin and ent.get("type") == "company":
            res = answer_fin(con, it, lang, src, codes=fin)
        elif fin and not ent and it.get("rank"):
            res = answer_rank_fin(con, it, lang, src)
        elif ent.get("type") == "company":
            res = answer_company(con, it, lang, src)
        elif it.get("rank") and it.get("dim") in ("region", "class") and not ent:
            res = answer_rank_dim(con, it, lang, src, it["dim"])
        elif it.get("dim") in ("region", "class") and not ent:
            res = answer_rank_dim(con, it, lang, src, it["dim"])
        elif (it.get("rank") or it.get("metric") == "share") and not ent:
            res = answer_rank_companies(con, it, lang, src)
        elif ent.get("type") == "class" and it.get("rank"):
            # рейтинга страховщиков по классу в НАПП нет — честно, и показываем сам класс
            res = answer_row(con, it, lang, src)
            if res.get("found"):
                res["text"] = {"ru": "Рейтинга страховщиков по отдельному классу в отчётах НАПП нет (разреза "
                                     "«страховщик × класс» нет). По классу в целом: ",
                               "uz": "NAPP hisobotlarida klass boʻyicha sugʻurtalovchilar reytingi yoʻq. Klass boʻyicha: ",
                               "en": "NAPP reports have no ranking of insurers within a class. For the class as a whole: "
                               }[lang] + res["text"]
        else:
            res = answer_row(con, it, lang, src)
    ent_f = res.get("entity") or (None if res.get("rank") else it.get("entity"))
    if ent_f is None and not res.get("rank") and not res.get("dim") and res.get("found") \
            and (res.get("numbers") or {}).get("row_key") == "total":
        ent_f = {"type": "total", "key": "total"}
    # собственные средства — из company_financials; прочие показатели компании к ним не относятся
    fs = [] if (it.get("metric") == "own_funds" or res.get("ranking")) and res.get("found") else \
        match_facts(it, ent_f, res.get("date"), bool(res.get("found")))
    have = {((s_.get("file") or "").lower(), s_.get("date")) for s_ in src.items}
    for f in fs:
        fname = (f.get("source_file") or "")
        base = re.sub(r"\.xlsx?$", "", fname, flags=re.I).replace("_", " ").strip().lower()
        if (base, f.get("source_date")) in have:
            continue                     # тот же отчёт НАПП и срез — плашка уже есть
        have.add((base, f.get("source_date")))
        src.add({"kind": "market", "label": FACTS_LABEL[lang], "title": "docs/market_facts.json — " + fname,
                 "file": fname or None, "date": f.get("source_date"), "date_text": _ru_date(f.get("source_date") or ""),
                 "url": mp.NAPP_PAGE, "domain": "napp.uz"})
    found = bool(res.get("found"))
    if found:
        text = res["text"] + facts_line(fs, lang)
    elif fs:
        text = {"ru": "В таблицах отчётов НАПП в базе этого нет; есть в обзорах рынка (база знаний):",
                "uz": "Bazadagi NAPP jadvallarida bu yoʻq; bozor sharhlarida bor:",
                "en": "Not in the NAPP tables in the database; found in market reviews:"}[lang]
        text += " " + "; ".join("%s — %s %s (%s, %s)" % (f.get("topic") or f.get("metric") or "",
                                                         _num(f.get("value"), lang, 1) if f.get("value") is not None else "",
                                                         f.get("unit") or "", f.get("period") or "",
                                                         f.get("source_file")) for f in fs[:3])
        found = True
    else:
        text = NO_DATA[lang] % (res.get("what") or {"ru": "таких данных", "uz": "bunday maʼlumot",
                                                    "en": "such data"}[lang])
    view = opinion(res, it, lang) if res.get("found") else None
    facts_payload = {"text": text, "numbers": res.get("numbers"), "table": res.get("table"),
                     "date": res.get("date"), "facts": fs, "sources": src.items}
    ai = ai_retell(question, facts_payload, lang, history or []) if with_ai and found else {"status": "off",
                                                                                              "text": None}
    sources = src.ensure(latest)
    ctx = {"kind": "market", "entity": res.get("entity") or (it.get("entity") if not res.get("rank") else None),
           "metric": it.get("metric") or (res.get("numbers") or {}).get("metric"), "rank": bool(res.get("rank")),
           "dim": res.get("dim"), "date": res.get("date"), "fin": it.get("fin") or None}
    if res.get("ranking"):
        ctx["metric"] = it.get("metric") if it.get("metric") == "own_funds" else None
    return {"found": found, "text": text, "opinion": view, "table": res.get("table"),
            "numbers": res.get("numbers"), "facts": fs, "sources": sources, "date": res.get("date"),
            "period": period_label(res["date"], lang) if res.get("date") else None,
            "ytd_note": RANK_NOTE[lang] if res.get("ranking") else YTD_NOTE[lang], "ai": ai, "context": ctx}


# --------------------------------------------------------------------------- #
#  Память диалога (в процессе; в базу не пишется)
# --------------------------------------------------------------------------- #

MEMORY_TURNS = 8
MEMORY_TTL_SEC = 2 * 3600
MEMORY_MAX_SESSIONS = 2000
SESSION_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")


class DialogMemory:
    """session → последние 8 реплик и контекст. Ключ — отпечаток (кто + session_id): чужой session_id
    не даёт доступа к контексту другого пользователя."""

    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(session_id: Optional[str], who: Optional[str]) -> Optional[str]:
        if not session_id:
            return None
        return hashlib.sha256(("%s|%s" % (who or "-", session_id)).encode("utf-8")).hexdigest()[:40]

    def _gc(self, now: float):
        dead = [k for k, v in self._data.items() if now - v["t"] > MEMORY_TTL_SEC]
        for k in dead:
            self._data.pop(k, None)
        if len(self._data) > MEMORY_MAX_SESSIONS:
            for k, _ in sorted(self._data.items(), key=lambda kv: kv[1]["t"])[:len(self._data) - MEMORY_MAX_SESSIONS]:
                self._data.pop(k, None)

    def get(self, key: Optional[str]) -> dict:
        if not key:
            return {"turns": [], "ctx": None}
        now = time.time()
        with self._lock:
            self._gc(now)
            s = self._data.get(key)
            if not s:
                return {"turns": [], "ctx": None}
            return {"turns": list(s["turns"]), "ctx": dict(s["ctx"]) if s["ctx"] else None}

    def add(self, key: Optional[str], question: str, reply: str, ctx: Optional[dict]):
        if not key:
            return
        now = time.time()
        with self._lock:
            s = self._data.setdefault(key, {"turns": deque(maxlen=MEMORY_TURNS), "ctx": None, "t": now})
            s["turns"].append({"role": "user", "text": (question or "")[:1000], "t": now})
            s["turns"].append({"role": "assistant", "text": (reply or "")[:1000], "t": now})
            s["ctx"] = ctx
            s["t"] = now
            self._gc(now)

    def clear(self):
        with self._lock:
            self._data.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._data)


memory = DialogMemory()


# --------------------------------------------------------------------------- #
#  Подсказки
# --------------------------------------------------------------------------- #

SUGGEST = {
    "ru": [("market", "Кто лидер рынка по премиям?"), ("market", "Какая убыточность по классу 14 (кредиты)?"),
           ("market", "Доля INSON на рынке и рост год к году"), ("market", "Рыночная ставка по классу 8 (огонь)"),
           ("law", "Что если страховая сумма больше стоимости имущества?"),
           ("law", "Какая максимальная комиссия агента?"),
           ("law", "Можно ли вернуть премию при досрочном расторжении?"),
           ("company", "Какой минимальный тариф по КАСКО в тарифной политике INSON?")],
    "uz": [("market", "Mukofotlar boʻyicha bozor yetakchisi kim?"), ("market", "14-klass boʻyicha zararlilik qancha?"),
           ("market", "INSON bozor ulushi qancha?"), ("market", "Hududlar reytingi mukofotlar boʻyicha"),
           ("law", "Sugʻurta summasi qiymatdan oshsa nima boʻladi?"), ("law", "Agent komissiyasi eng koʻp qancha?"),
           ("law", "Shartnoma muddatidan oldin bekor qilinsa, mukofot qaytariladimi?"),
           ("company", "INSON tarif siyosatida KASKO boʻyicha eng kichik tarif qancha?")],
    "en": [("market", "Who is the market leader by premiums?"), ("market", "What is the loss ratio for class 14?"),
           ("market", "What is INSON's market share?"), ("market", "Market rate for class 3 (motor hull)"),
           ("law", "What if the sum insured exceeds the value of the property?"),
           ("law", "What is the maximum agent commission?"),
           ("law", "Is the premium refunded on early termination?"),
           ("company", "Minimum CASCO tariff in INSON's tariff policy?")],
}


def suggest(lang: str) -> list:
    lang = lang if lang in SUGGEST else "ru"
    return [{"kind": k, "q": q} for k, q in SUGGEST[lang]]
