"""
Узбекская республиканская товарно-сырьевая биржа (УзРТСБ, uzex.uz) — слой загрузки биржевых цен.

Зачем это сюрвейеру. Стоимость запасов, грузов и материалов в акте (классы 7, 8, 9, 16) заявляет страхователь.
Биржевые цены реальных сделок — независимая сверка: сколько на самом деле стоили тонна дизельного топлива,
арматуры или цемента в последние недели. Стоимость объекта по ним не меняется — это справка.

Модуль — ЧИСТЫЕ функции: реестр страниц, robots.txt, загрузка, разбор, группировка, медиана, запись
в таблицу exchange_quotes и чтение из неё. Роутера и расписания здесь нет (это app/uzex.py).

Жёсткие правила модуля (те же, что в app/valuation_sources.py и app/stat_sources.py):
  * только стандартная библиотека Python (urllib, html.parser, re, sqlite3, statistics);
  * robots.txt читается и соблюдается (см. robots_allowed: urllib.robotparser + построчная проверка по
    RFC 9309 — разрешено, только если согласны ОБЕ); robots.txt недоступен — сайт считается закрытым;
  * пауза не менее 2 секунд между обращениями к uzex.uz; честный User-Agent с адресом для связи;
  * никаких обходов: страницы входа, кабинеты, карточки лотов (/Trade/Offer) и всё, что закрыто robots.txt,
    не открываются;
  * ничего не выдумывается: нет даты сделки — trade_date = NULL и пометка «на дату загрузки».

Что отдаёт uzex.uz (проверено 02.10.2026 обращением к сайту, User-Agent ниже):

  1. /Trade/List — «Tuzilgan bitimlar reyestri», реестр ЗАКЛЮЧЁННЫХ СДЕЛОК. Есть дата сделки
     (Shartnoma sanasi «30/09/2026 00:00:00»), номер контракта, товар, количество (Miqdori), единица,
     сумма сделки (Mahsulot narxi, сум), вид контракта (внутренний / экспортный / импортный).
     Сумма — за ВСЁ количество сделки: медь катодная 8 т = 1 471 782 535,92 сум и 23 т = 4 231 374 791 сум —
     одна и та же цена за тонну ≈ 183,97 млн. На сайте около недели сделок (~12,5 тыс. строк, страницы по 1 000),
     сортировка — от новых к старым. Это ОСНОВНОЙ источник для медиан.
  2. /Trade/ContractsSumNew — «Котировочный лист биржи, сум». Даты НЕТ — котировка «на дату загрузки».
     «Bazis narxi» — цена ЛОТА, а не единицы: сверено с реестром сделок по тем же номерам контрактов —
     стекло листовое, лот 250 кв.м, 9 100 000 → 36 400 за кв.м (в реестре 36 822); медная катанка, лот 3 300 кг,
     614 964 293,75 → 186 353 за кг (в реестре 186 402); у лотов в 1 тонну цены совпадают с реестром дословно.
     Поэтому price_lot = Bazis narxi, price_unit = Bazis narxi / Lot. Часть котировок старые (лист — все когда-либо
     зарегистрированные контракты, ~148 тыс. строк), поэтому в медианы они не идут, только в выдачу «как есть».
  3. /Trade/ContractsCurrencyNew — тот же лист в валюте; валюта на странице не подписана (рядом отдельные
     страницы рубля, евро, юаня), у части строк цена-заглушка «1,0000». В ежедневную загрузку НЕ входит.
  4. /Trade/NewSpotTable — заявки на продажу на сегодня (не сделки), ночью таблица пустая. Не грузим.
  5. /Trade/OffersSumNew — ЗАКРЫТ robots.txt (Disallow: /Trade/OffersSum — правило по началу пути).
     Карточки лотов /trade/offer/<n> — закрыты (Disallow: /Trade/Offer). Не открываем.

Особенность robots.txt uzex.uz: в файле две группы «User-agent: *» — первая «Allow: /», вторая с запретами,
а первая строка начинается с невидимого символа BOM. urllib.robotparser берёт только первую группу «*»
(и случайно пропускает её из-за BOM), поэтому полагаться на него одного нельзя: здесь группы для «*»
объединяются, путь сравнивается без учёта регистра (сайт на ASP.NET регистр не различает, а в ссылках
пишет /trade/offer/…), побеждает самое длинное совпавшее правило. Запрещено хоть одной проверкой — не грузим.
"""
import gzip
import html as htmlmod
import re
import sqlite3
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

from app import db
from app.valuation_sources import _SSL_CTX, _throttle

BASE = "https://uzex.uz"
DOMAIN = "uzex.uz"
SOURCE = "uzex.uz"
SOURCE_NAME = "УзРТСБ (Узбекская республиканская товарно-сырьевая биржа)"
CONTACT_EMAIL = "j.mirzaumarov@insuranceon.uz"
# HTTP-заголовки — только латиница. Строка согласована с заказчиком.
USER_AGENT = "ai-surveyor (INSON) contact: %s" % CONTACT_EMAIL
MIN_DELAY_SEC = 2.0
HTTP_TIMEOUT = 60
ROBOTS_TTL_SEC = 6 * 3600           # прочитанные правила живут 6 часов
ROBOTS_FAIL_TTL_SEC = 600           # сбой чтения — повтор через 10 минут

STATUS_OK = "ok"
STATUS_EMPTY = "нет данных"
STATUS_DOWN = "источник недоступен"
STATUS_ROBOTS = "запрещено robots.txt"

DATE_DEAL = "дата сделки"
DATE_FETCH = "на дату загрузки"

# --------------------------------------------------------------------------- #
# Страницы
# --------------------------------------------------------------------------- #
PAGES: Dict[str, dict] = {
    "List": {
        "path": "/Trade/List", "title": "Реестр заключённых сделок (Tuzilgan bitimlar reyestri)",
        "kind": "deals", "dated": True, "currency": "UZS", "length": 1000, "max_pages": 15,
        "price_basis": "сумма сделки за всё количество; цена за единицу = сумма / количество",
    },
    "ContractsSumNew": {
        "path": "/Trade/ContractsSumNew", "title": "Котировочный лист биржи, сум",
        "kind": "quotes", "dated": False, "currency": "UZS", "length": 1000, "max_pages": 1,
        "price_basis": "Bazis narxi — цена лота (сверено с реестром сделок); цена за единицу = цена лота / Lot",
    },
    "ContractsCurrencyNew": {
        "path": "/Trade/ContractsCurrencyNew", "title": "Котировочный лист биржи, валюта",
        "kind": "quotes", "dated": False, "currency": "валюта (на странице не подписана)", "length": 1000,
        "max_pages": 1,
        "price_basis": "Bazis narxi — цена лота в валюте; у части строк цена-заглушка 1,0000",
    },
}
DAILY_PAGES = ("List", "ContractsSumNew")


def page_url(page: str, n: int = 1, length: Optional[int] = None) -> str:
    p = PAGES[page]
    q = urllib.parse.urlencode({"page": n, "status": 1, "length": length or p["length"]})
    return "%s%s?%s" % (BASE, p["path"], q)


# --------------------------------------------------------------------------- #
# robots.txt
# --------------------------------------------------------------------------- #
_robots = {"text": None, "reason": "", "at": 0.0}


def _rfc_rules(text: str, agent: str) -> List[Tuple[str, str]]:
    """Правила (allow|disallow, путь) всех групп, подходящих агенту; если своих групп нет — всех групп «*»."""
    groups, cur, in_agents = [], None, False
    for raw in text.splitlines():
        line = raw.replace("﻿", "").split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            if not in_agents:
                cur = {"agents": [], "rules": []}
                groups.append(cur)
                in_agents = True
            cur["agents"].append(v.lower())
        elif k in ("allow", "disallow"):
            in_agents = False
            if cur is not None:
                cur["rules"].append((k, v))
    own = [g for g in groups if any(a not in ("", "*") and a in agent.lower() for a in g["agents"])]
    use = own or [g for g in groups if "*" in g["agents"]]
    return [r for g in use for r in g["rules"]]


def _rfc_allowed(text: str, agent: str, url: str) -> bool:
    """RFC 9309: самое длинное совпадение побеждает, при равной длине — allow. Регистр не различаем (строже)."""
    sp = urllib.parse.urlsplit(url)
    path = (sp.path or "/") + ("?" + sp.query if sp.query else "")
    lp = path.lower()
    best, verdict = -1, True
    for kind, rule in _rfc_rules(text, agent):
        if not rule:
            continue                     # «Disallow:» пустой — ничего не запрещает
        pat = re.escape(rule.lower()).replace(r"\*", ".*")
        if pat.endswith(r"\$"):
            pat = pat[:-2] + "$"
        if re.match(pat, lp):
            n = len(rule)
            if n > best or (n == best and kind == "allow"):
                best, verdict = n, kind == "allow"
    return verdict


def robots_allowed(text: str, url: str, agent: str = USER_AGENT) -> Tuple[bool, str]:
    """Чистая проверка по тексту robots.txt: разрешено, только если согласны urllib.robotparser и RFC 9309."""
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.replace("﻿", "").splitlines())
    a = rp.can_fetch(agent, url)
    b = _rfc_allowed(text, agent, url)
    if a and b:
        return True, ""
    return False, "robots.txt %s запрещает загрузку %s" % (DOMAIN, url)


def _robots_text() -> Tuple[Optional[str], str]:
    now = time.monotonic()
    ttl = ROBOTS_TTL_SEC if _robots["text"] is not None else ROBOTS_FAIL_TTL_SEC
    if _robots["at"] and now - _robots["at"] < ttl:
        return _robots["text"], _robots["reason"]
    text, reason = None, ""
    try:
        text = _http_get(BASE + "/robots.txt", timeout=25)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            text = ""                    # файла нет — ограничений нет
        else:
            reason = "robots.txt недоступен (HTTP %d)" % e.code
    except Exception as e:               # сеть, таймаут, TLS
        reason = "robots.txt недоступен (%s)" % type(e).__name__
    _robots.update(text=text, reason=reason, at=now)
    return text, reason


def robots_check(url: str) -> Tuple[bool, str]:
    """(разрешено, причина). Не прочитали robots.txt — не грузим."""
    text, reason = _robots_text()
    if text is None:
        return False, reason or "robots.txt недоступен"
    return robots_allowed(text, url)


# --------------------------------------------------------------------------- #
# Сеть
# --------------------------------------------------------------------------- #

def _http_get(url: str, timeout: int = HTTP_TIMEOUT) -> str:
    """GET с честным User-Agent и паузой не меньше MIN_DELAY_SEC между обращениями к uzex.uz."""
    _throttle(DOMAIN)                    # общий учёт пауз по домену (app/valuation_sources.py, 2 с)
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "From": CONTACT_EMAIL,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,uz;q=0.8", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")


def fetch_html(url: str) -> str:
    ok, reason = robots_check(url)
    if not ok:
        raise PermissionError(reason)
    return _http_get(url)


# --------------------------------------------------------------------------- #
# Разбор
# --------------------------------------------------------------------------- #

class _Tables(HTMLParser):
    """Таблицы страницы: заголовки (th) и строки (td) как текст."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._t, self._row, self._cell, self._in = [], None, None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._t = {"head": [], "rows": []}
        elif self._t is not None and tag == "tr":
            self._row = []
        elif self._t is not None and tag in ("td", "th"):
            self._cell, self._in = [], tag

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._t is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            if self._in == "th":
                self._t["head"].append(text)
            elif self._row is not None:
                self._row.append(text)
            self._cell, self._in = None, None
        elif tag == "tr" and self._t is not None:
            if self._row:
                self._t["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._t is not None:
            self.tables.append(self._t)
            self._t = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_tables(html: str) -> List[dict]:
    p = _Tables()
    p.feed(html)
    return p.tables


def parse_number(text) -> Optional[float]:
    """«870000000,0000», «315341645.0000», «1 471 782 535,92», «3,5» → число; пусто или мусор → None."""
    if text is None:
        return None
    s = str(text).strip().replace(" ", "").replace(" ", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(text) -> Optional[str]:
    """«30/09/2026 00:00:00» или «30.09.2026» → «2026-09-30»."""
    m = re.search(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", str(text or ""))
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


def total_on_site(html: str) -> Optional[int]:
    """«148654 dan 1 dan 10 gacha yozuvlar aks ettirildi» → 148654."""
    m = re.search(r"(\d+)\s+dan\s+\d+\s+dan\s+\d+\s+gacha", html)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# Группы товаров и единицы
# --------------------------------------------------------------------------- #
# Порядок важен: первое совпадение выигрывает («Масло хлопковое» — не нефтяное масло и не хлопок-сырец).
GROUPS: Dict[str, dict] = {
    "diesel": {"ru": "дизельное топливо", "uz": "dizel yoqilgʻisi", "en": "diesel fuel"},
    "petrol": {"ru": "бензин", "uz": "benzin", "en": "petrol"},
    "heating_oil": {"ru": "печное топливо", "uz": "pech yoqilgʻisi", "en": "heating oil"},
    "fuel_other": {"ru": "прочее топливо (газ, уголь, авиатопливо, мазут)", "uz": "boshqa yoqilgʻi (gaz, koʻmir)",
                   "en": "other fuel (gas, coal, jet fuel, fuel oil)"},
    "oils": {"ru": "масла и смазки", "uz": "moylar", "en": "oils and lubricants"},
    "metal": {"ru": "металл, арматура, прокат", "uz": "metall, armatura, prokat", "en": "steel, rebar, rolled metal"},
    "nonferrous": {"ru": "цветные металлы", "uz": "rangli metallar", "en": "non-ferrous metals"},
    "cement": {"ru": "цемент", "uz": "sement", "en": "cement"},
    "grain": {"ru": "мука и зерно", "uz": "un va don", "en": "flour and grain"},
    "cotton": {"ru": "хлопок", "uz": "paxta", "en": "cotton"},
    "chemicals": {"ru": "химия и удобрения", "uz": "kimyo va oʻgʻitlar", "en": "chemicals and fertilisers"},
    "other": {"ru": "прочее", "uz": "boshqa", "en": "other"},
}
_FOOD_OIL = re.compile(r"масло\s*(растит|подсолн|хлопк|сливоч|пищев|соев|кукуруз|оливк)|хлопковое масло", re.I)
_RULES: List[Tuple[str, re.Pattern]] = [
    ("heating_oil", re.compile(r"печн", re.I)),
    ("diesel", re.compile(r"дизел|дизтоплив|\bдт\b|diesel|dizel", re.I)),
    ("petrol", re.compile(r"бензин|\bаи-?\d\d|\bа-?9[2258]\b|benzin", re.I)),
    ("fuel_other", re.compile(r"сжиженн\w* газ|пропан|бутан|уголь|мазут|авиацион|авиатоплив|керосин|"
                              r"джет|jet\s*a|нафта|газовый конденсат", re.I)),
    ("oils", re.compile(r"\bмасл[оа]\b|масло|смазк|солидол|литол", re.I)),
    ("cement", re.compile(r"цемент|sement", re.I)),
    ("nonferrous", re.compile(r"\bмедь\b|\bмеди\b|медн|катодн|цинк|алюмин|свин[ец]|латун|бронз|никел|\bолово", re.I)),
    ("metal", re.compile(r"арматур|уголок|катанк|швеллер|балк[аи]|сталь|стальн|прокат|лист\w* (горяче|холодно)|"
                         r"труб[аы]|квадрат|круг\s*№|чугун|металлолом|проволок|armatura", re.I)),
    ("grain", re.compile(r"мук[аи]|пшениц|ячмен|зерн|кукуруз|\bрис\b|овёс|овес|отруб|bugʻdoy", re.I)),
    ("cotton", re.compile(r"хлоп|paxta|линт|пряж", re.I)),
    ("chemicals", re.compile(r"удобрен|аммофос|суперфосфат|карбамид|селитр|калий|сульфат|кислот|метанол|\bсер[аы]\b|"
                             r"аммиак|амиак|полипропилен|полиэтилен|сод[аы]|натри|хлор|препарат|инсектицид|гербицид|"
                             r"фунгицид|дефолиант|двуокись углерода|аргон|кислород|азот|химэтефон|регулятор роста|"
                             r"смол[аы]|пвх|каустик", re.I)),
]
_PRECIOUS = re.compile(r"золот|серебр|платин|палладий|au-1|слиток", re.I)


def classify(name: str) -> str:
    """Название товара → код группы. Драгметаллы и пищевые масла — «прочее»: для сверки запасов они не годятся."""
    s = str(name or "")
    if not s.strip() or _PRECIOUS.search(s) or _FOOD_OIL.search(s):
        return "other"
    for code, rx in _RULES:
        if rx.search(s):
            return code
    return "other"


def group_label(code: str, lang: str = "ru") -> str:
    g = GROUPS.get(code) or GROUPS["other"]
    return g.get(lang) or g["ru"]


# единица у источника → (единица, к которой приводим, во сколько раз умножить цену за единицу)
UNITS = {
    "тонна": ("т", 1.0), "т": ("т", 1.0), "tonna": ("т", 1.0),
    "килограмм": ("т", 1000.0), "кг": ("т", 1000.0), "kilogramm": ("т", 1000.0),
    "грамм": ("г", 1.0), "г": ("г", 1.0),
    "литр": ("л", 1.0), "л": ("л", 1.0), "декалитр": ("л", 0.1),
    "куб.метр": ("м³", 1.0), "м3": ("м³", 1.0), "кв.м": ("м²", 1.0), "штука": ("шт", 1.0), "шт": ("шт", 1.0),
}
UNIT_LABELS = {"т": {"ru": "т", "uz": "t", "en": "t"}, "г": {"ru": "г", "uz": "g", "en": "g"},
               "л": {"ru": "л", "uz": "l", "en": "l"}, "м³": {"ru": "м³", "uz": "m³", "en": "m³"},
               "м²": {"ru": "м²", "uz": "m²", "en": "m²"}, "шт": {"ru": "шт", "uz": "dona", "en": "pc"}}


def norm_unit(unit: str, price_unit: Optional[float]) -> Tuple[str, Optional[float]]:
    u = str(unit or "").strip().lower()
    to, k = UNITS.get(u, (u or "?", 1.0))
    return to, (None if price_unit is None else round(price_unit * k, 4))


# --------------------------------------------------------------------------- #
# Строки страниц
# --------------------------------------------------------------------------- #

def _col(head: List[str], *names) -> Optional[int]:
    low = [h.lower() for h in head]
    for n in names:
        for i, h in enumerate(low):
            if n.lower() in h:
                return i
    return None


def _main_table(html: str, must: str) -> Optional[dict]:
    for t in parse_tables(html):
        if any(must.lower() in h.lower() for h in t["head"]):
            return t
    return None


def parse_page(page: str, html: str, fetched_at: str, url: str) -> List[dict]:
    """HTML страницы → строки {page, contract_no, name, lot_qty, unit, price_lot, price_unit, warehouse,
    trade_date, fetched_at, url, …}. Ничего не дописываем: нет значения — None."""
    p = PAGES[page]
    out: List[dict] = []
    if p["kind"] == "deals":
        t = _main_table(html, "Shartnoma sanasi")
        if not t:
            return out
        h = t["head"]
        ci = {"date": _col(h, "Shartnoma sanasi"), "deal": _col(h, "Bitim raqami"),
              "price": _col(h, "Mahsulot narxi"), "no": _col(h, "Shartnoma №", "Shartnoma No"),
              "name": _col(h, "Mahsulot markasi"), "qty": _col(h, "Miqdori"), "unit": _col(h, "O'lchov", "Oʻlchov"),
              "type": _col(h, "Shartnoma turi"), "status": _col(h, "bajarilish")}
        seen: Dict[tuple, int] = {}
        for r in t["rows"]:
            g = lambda k: (r[ci[k]] if ci[k] is not None and ci[k] < len(r) else None)  # noqa: E731
            if len(r) < 5:
                continue
            total = parse_number(g("price"))
            qty = parse_number(g("qty"))
            td = parse_date(g("date"))
            unit = (g("unit") or "").strip()
            pu = round(total / qty, 4) if total is not None and qty else None
            un, pun = norm_unit(unit, pu)
            base = (g("no") or "", td or "", qty, total)
            seq = seen.get(base, 0) + 1
            seen[base] = seq
            out.append({
                "page": page, "contract_no": (g("no") or "").strip() or None,
                "deal_key": "%s|%s|%s|%s|%d" % (g("no") or "", td or "", _plain(qty), _plain(total), seq),
                "name": (g("name") or "").strip() or None, "grp": classify(g("name")),
                "lot_qty": qty, "unit": unit or None, "unit_norm": un,
                "price_raw": total, "price_lot": total, "price_unit": pu, "price_unit_norm": pun,
                "price_basis": p["price_basis"], "currency": p["currency"], "warehouse": None,
                "trade_date": td, "date_basis": DATE_DEAL if td else DATE_FETCH,
                "contract_type": (g("type") or "").strip() or None, "deal_status": (g("status") or "").strip() or None,
                "fetched_at": fetched_at, "fetched_date": fetched_at[:10], "url": url})
        return out
    t = _main_table(html, "Bazis narxi")
    if not t:
        return out
    h = t["head"]
    ci = {"no": _col(h, "Shartnoma raqami"), "name": _col(h, "Nomi"), "lot": _col(h, "Lot"),
          "unit": _col(h, "O'lchov", "Oʻlchov"), "price": _col(h, "Bazis narxi"), "wh": _col(h, "Omborning")}
    for r in t["rows"]:
        g = lambda k: (r[ci[k]] if ci[k] is not None and ci[k] < len(r) else None)  # noqa: E731
        if len(r) < 5:
            continue
        raw = parse_number(g("price"))
        lot = parse_number(g("lot"))
        unit = (g("unit") or "").strip()
        pu = round(raw / lot, 4) if raw is not None and lot else None
        un, pun = norm_unit(unit, pu)
        no = (g("no") or "").strip() or None
        out.append({
            "page": page, "contract_no": no, "deal_key": no or "",
            "name": (g("name") or "").strip() or None, "grp": classify(g("name")),
            "lot_qty": lot, "unit": unit or None, "unit_norm": un,
            "price_raw": raw, "price_lot": raw, "price_unit": pu, "price_unit_norm": pun,
            "price_basis": p["price_basis"], "currency": p["currency"],
            "warehouse": (g("wh") or "").strip() or None,
            "trade_date": None, "date_basis": DATE_FETCH, "contract_type": None, "deal_status": None,
            "fetched_at": fetched_at, "fetched_date": fetched_at[:10], "url": url})
    return out


def _plain(x) -> str:
    if x is None:
        return ""
    return ("%.4f" % float(x)).rstrip("0").rstrip(".")


def fetch_quotes(page: str, max_pages: Optional[int] = None, stop_before: Optional[str] = None) -> List[dict]:
    """
    Загрузка страницы биржи (все её листы по 1 000 строк, не больше max_pages). stop_before — дата ISO:
    реестр сделок идёт от новых к старым, и если на листе уже есть сделки раньше этой даты, дальше не идём
    (они уже в базе). Запрет robots.txt → PermissionError; сеть → исключение urllib.
    """
    if page not in PAGES:
        raise KeyError(page)
    p = PAGES[page]
    limit = max_pages or p["max_pages"]
    rows: List[dict] = []
    for n in range(1, limit + 1):
        url = page_url(page, n)
        html = fetch_html(url)
        got = parse_page(page, html, db.now(), url)
        rows += got
        if len(got) < p["length"]:
            break
        if stop_before and p["dated"]:
            dates = [r["trade_date"] for r in got if r["trade_date"]]
            if dates and min(dates) < stop_before:
                break
    if p["kind"] == "deals":
        rekey(rows)
    return rows


def rekey(rows: List[dict]) -> List[dict]:
    """Порядковый номер одинаковых сделок — по всей загрузке, а не по листу: одинаковые сделки одного дня
    бывают по разные стороны границы листа, и нумерация с единицы на каждом листе склеила бы их."""
    seen: Dict[str, int] = {}
    for r in rows:
        base = r["deal_key"].rsplit("|", 1)[0]
        seen[base] = seen.get(base, 0) + 1
        r["deal_key"] = "%s|%d" % (base, seen[base])
    return rows


# --------------------------------------------------------------------------- #
# База
# --------------------------------------------------------------------------- #
COLS = ("page", "contract_no", "deal_key", "name", "grp", "lot_qty", "unit", "unit_norm", "price_raw", "price_lot",
        "price_unit", "price_unit_norm", "price_basis", "currency", "warehouse", "trade_date", "date_basis",
        "contract_type", "deal_status", "fetched_at", "fetched_date", "url")


def save_rows(con, rows: List[dict]) -> dict:
    """Запись с историей: ключ (page, deal_key, fetched_date). Повтор в тот же день обновляет строку дня,
    строки прошлых дней не трогаются."""
    ins = upd = 0
    for r in rows:
        cur = con.execute("SELECT id FROM exchange_quotes WHERE page=? AND deal_key=? AND fetched_date=?",
                          (r["page"], r["deal_key"], r["fetched_date"])).fetchone()
        if cur:
            con.execute("UPDATE exchange_quotes SET %s WHERE id=?" % ", ".join("%s=?" % c for c in COLS),
                        tuple(r.get(c) for c in COLS) + (cur[0],))
            upd += 1
        else:
            con.execute("INSERT INTO exchange_quotes (%s) VALUES (%s)" % (", ".join(COLS), ", ".join("?" * len(COLS))),
                        tuple(r.get(c) for c in COLS))
            ins += 1
    return {"вставлено": ins, "обновлено": upd, "всего": len(rows)}


def last_trade_date(con, page: str = "List") -> Optional[str]:
    try:
        r = con.execute("SELECT MAX(trade_date) FROM exchange_quotes WHERE page=?", (page,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r else None


def fetch_and_save(page: str, con=None) -> dict:
    """Загрузка и запись одной страницы. Недоступность — статус, а не исключение."""
    p = PAGES[page]
    st = {"page": page, "title": p["title"], "url": page_url(page), "fetched_at": db.now(), "rows": 0,
          "status": STATUS_OK, "reason": "", "dated": p["dated"],
          "date_note": DATE_DEAL if p["dated"] else "даты сделки на странице нет — котировка на дату загрузки"}
    try:
        stop = None
        if p["dated"]:
            if con is not None:
                stop = last_trade_date(con, page)
            else:
                with db.tx() as c:
                    stop = last_trade_date(c, page)
            if stop:                     # день перекрываем: последний день мог пополниться
                stop = (date.fromisoformat(stop) - timedelta(days=1)).isoformat()
        rows = fetch_quotes(page, stop_before=stop)
    except PermissionError as e:
        st.update(status=STATUS_ROBOTS, reason=str(e))
        return st
    except Exception as e:
        st.update(status=STATUS_DOWN, reason="%s: %s" % (type(e).__name__, e))
        return st
    if not rows:
        st.update(status=STATUS_EMPTY, reason="таблица на странице пустая или не распознана")
        return st
    if con is not None:
        st["saved"] = save_rows(con, rows)
    else:
        with db.tx() as c:
            st["saved"] = save_rows(c, rows)
    st["rows"] = len(rows)
    dates = sorted(r["trade_date"] for r in rows if r["trade_date"])
    st["trade_dates"] = [dates[0], dates[-1]] if dates else None
    return st


# Одна сделка реестра попадает в базу при каждой загрузке, пока висит на сайте (около недели).
# Для медиан берём каждую сделку один раз — последнюю загруженную строку по (page, deal_key).
_DEALS_SQL = ("SELECT * FROM exchange_quotes WHERE id IN (SELECT MAX(id) FROM exchange_quotes "
              "WHERE trade_date IS NOT NULL GROUP BY page, deal_key)")


def median(xs: List[float]) -> Optional[float]:
    xs = [float(x) for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def summary(con, days: int = 30, as_of: Optional[str] = None, groups: Optional[List[str]] = None,
            include_other: bool = True) -> List[dict]:
    """
    Медиана цены за единицу по группам за days дней до as_of (по умолчанию — сегодня), только сделки с датой.
    Единицы приведены: кг → т, декалитр → л. Строка на (группа, единица). Таблицы нет — пустой список.
    """
    end = as_of or date.today().isoformat()
    start = (date.fromisoformat(end) - timedelta(days=int(days))).isoformat()
    try:
        rows = db.rows(con, "SELECT grp, unit_norm, price_unit_norm, trade_date, page, url FROM (%s) "
                            "WHERE trade_date > ? AND trade_date <= ? AND price_unit_norm IS NOT NULL "
                            "AND price_unit_norm > 0 AND (currency='UZS')" % _DEALS_SQL, start, end)
    except sqlite3.OperationalError:
        return []
    acc: Dict[tuple, dict] = {}
    for r in rows:
        if groups and r["grp"] not in groups:
            continue
        if not include_other and r["grp"] == "other":
            continue
        k = (r["grp"], r["unit_norm"])
        a = acc.setdefault(k, {"prices": [], "dates": [], "page": r["page"]})
        a["prices"].append(r["price_unit_norm"])
        a["dates"].append(r["trade_date"])
    out = []
    for (g, u), a in acc.items():
        ps = sorted(a["prices"])
        out.append({"group": g, "group_label": group_label(g), "unit": u, "median_unit_price": median(ps),
                    "min": round(ps[0], 2), "max": round(ps[-1], 2), "deals": len(ps),
                    "first_date": min(a["dates"]), "last_date": max(a["dates"]), "currency": "UZS",
                    "period": {"from": start, "to": end, "days": int(days)},
                    "url": "%s%s" % (BASE, PAGES[a["page"]]["path"]), "source": SOURCE})
    out.sort(key=lambda x: (list(GROUPS).index(x["group"]) if x["group"] in GROUPS else 99, -x["deals"]))
    return out


def quotes(con, group: Optional[str] = None, q: Optional[str] = None, days: Optional[int] = None,
           page: Optional[str] = None, limit: int = 200, offset: int = 0) -> dict:
    """Строки как есть (последняя загрузка каждой строки). days — по дате сделки; у котировок без даты — по дате
    загрузки (это и есть их дата)."""
    where, args = [], []
    if group:
        where.append("grp=?")
        args.append(group)
    if page:
        where.append("page=?")
        args.append(page)
    if q:
        # LOWER() в SQLite кириллицу не понижает — ищем в нескольких написаниях
        qs = str(q).strip()
        forms = list(dict.fromkeys([qs, qs.lower(), qs.capitalize(), qs.upper()]))
        where.append("(" + " OR ".join("name LIKE ?" for _ in forms) + ")")
        args += ["%" + f + "%" for f in forms]
    if days:
        since = (date.today() - timedelta(days=int(days))).isoformat()
        where.append("COALESCE(trade_date, fetched_date) > ?")
        args.append(since)
    sql = ("SELECT * FROM exchange_quotes WHERE id IN (SELECT MAX(id) FROM exchange_quotes GROUP BY page, deal_key)"
           + (" AND " + " AND ".join(where) if where else ""))
    try:
        total = con.execute("SELECT COUNT(*) FROM (%s)" % sql, tuple(args)).fetchone()[0]
        rows = db.rows(con, sql + " ORDER BY COALESCE(trade_date, fetched_date) DESC, id DESC LIMIT ? OFFSET ?",
                       *args, int(limit), int(offset))
    except sqlite3.OperationalError:
        return {"total": 0, "rows": []}
    for r in rows:
        r["group_label"] = group_label(r["grp"])
        r["note"] = (None if r["trade_date"] else
                     "даты сделки на странице нет — котировка на дату загрузки %s" % r["fetched_date"])
    return {"total": total, "rows": rows}


def groups_in_text(text: str) -> List[str]:
    """Все группы, названные в свободном тексте («склад ГСМ: дизельное топливо и бензин» → diesel, petrol).
    Порядок — как в GROUPS; «прочее» не возвращается."""
    s = str(text or "")
    if not s.strip():
        return []
    s = _PRECIOUS.sub(" ", _FOOD_OIL.sub(" ", s))
    found = {code for code, rx in _RULES if rx.search(s)}
    if re.search(r"\bгсм\b|нефтепродукт|топлив|yoqilg", s, re.I) and not found & {"diesel", "petrol", "heating_oil"}:
        found |= {"diesel", "petrol"}
    return [c for c in GROUPS if c in found]
