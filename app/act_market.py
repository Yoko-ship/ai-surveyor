"""
Оценка стоимости по объявлениям через снимки экрана сотрудника (30.09.2026).

Почему так: olx.uz отдаёт 403 (CloudFront) на любые автоматические запросы, включая /robots.txt; обходить
защиту запрещено правилами проекта. Поэтому сервер к olx.uz не обращается вообще: он только составляет
адреса поиска (search_links), сотрудник открывает их в своём браузере, делает снимки экрана списка
объявлений, а языковая модель читает снимки как картинки (эндпоинты — в app/act.py).

Здесь — без сети и без HTTP: адреса поиска, инструкция модели, разбор её ответа, проверка объявлений
от сотрудника (POST /act/make, optional.market), текст блока для раздела 3 акта. Расчёт — чистая функция
act_engine.market_estimate. Имена и телефоны продавцов не возвращаются и не хранятся: значения, похожие
на данные людей, вырезаются (clean_text) до ответа и до записи в базу.
"""
import json
import re
from datetime import date
from typing import Optional
from urllib.parse import quote, urlsplit

from . import act_engine as ae
from . import act_texts as tx
from . import llm
from . import valuation_sources as vs
from .act_texts import money, pct, t

SITES = ("olx", "avtoelon", "uybor", "joymee", "other")
CURRENCIES = ("UZS", "USD", "у.е.")
MAX_SHOTS = 5
MAX_LISTINGS = 40
USD_RATE_BOUNDS = (100, 1_000_000)        # сумов за доллар: защита от опечатки в разрядах, а не норма

OLX_ROOT = "https://www.olx.uz"
# разделы OLX, адрес которых известен из app/valuation_sources.py (OLX_SECTIONS)
OLX_SECTION_BY_KIND = {"car": ("авто", "mk_sec_cars"), "electric_car": ("авто", "mk_sec_cars")}
OLX_SECTION_BY_GROUP = {"property": ("недвижимость", "mk_sec_property")}
AVTOELON_CARS = "https://avtoelon.uz/avto/"
JOYMEE_LIST = "https://joymee.uz/ru/announcements"
UYBOR_ROOT = "https://uybor.uz/"

# вид объекта акта → подтип раздела спецтехники avtoelon.uz (valuation.SPEC_SECTIONS)
AVTOELON_SUBTYPE = {"truck_crane": "автокран", "excavator": "экскаватор", "backhoe_loader": "экскаватор-погрузчик",
                    "bulldozer": "бульдозер", "wheel_loader": "погрузчик фронтальный",
                    "forklift": "погрузчик вилочный", "aerial_platform": "автовышка",
                    "concrete_pump": "бетононасос", "concrete_mixer": "бетоносмеситель",
                    "drilling_rig": "буровая установка", "tractor": "трактор", "combine": "комбайн",
                    "trailer": "прицеп"}
CAR_KINDS = ("car", "electric_car")
PROPERTY_KINDS = ("warehouse", "shop", "production", "office", "dwelling", "hotel")
# вид объекта → подсказка класса для act_engine.object_group, если класс не указан
KIND_HINT = {**{k: "special_machinery" for k in AVTOELON_SUBTYPE if k != "trailer"},
             "crawler_crane": "special_machinery", "telehandler": "special_machinery",
             "road_machinery": "special_machinery", "special_other": "special_machinery",
             "trailer": "vehicle", "car": "vehicle", "truck": "vehicle", "electric_car": "vehicle",
             **{k: "building" for k in PROPERTY_KINDS}, "equipment": "equipment", "cargo": "cargo"}

# отчество или «... oʻgʻli / qizi» — это человек, а не название изделия (то же правило, что в app/act.py)
PATRONYMIC = re.compile(r"\b[А-ЯЁ][а-яё]+(?:ович|евич|ьич|овна|евна|ична|инична)\b|\b(?:o[ʻ'`‘]?g[ʻ'`‘]?li|qizi)\b",
                        re.I)
_EMPTY = {"", "null", "none", "n/a", "-", "—", "нет", "не видно", "unknown", "not visible", "yoʻq"}
# хвост названия после вырезанного телефона: «, звоните», «тел.», «звонить», «qoʻngʻiroq qiling», «call»
CALL_TAIL = re.compile(r"[\s,;:\-—(]*\b(?:звоните|звонить|звонок|позвоните|тел(?:ефон)?\.?|моб\.?|пишите|"
                       r"qo[ʻ'`‘]?ng[ʻ'`‘]?iroq(?:\s+qiling)?|tel\.?|call(?:\s+me)?|phone)[\s:.)]*$", re.I)
# ссылка на объявление: только https и только эти площадки (и их поддомены)
URL_HOSTS = ("olx.uz", "avtoelon.uz", "uybor.uz", "joymee.uz")

_CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳ",
                ["a", "b", "v", "g", "d", "e", "yo", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                 "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya", "o", "q", "g", "h"]))


# --------------------------------------------------------------------------- #
#  Адреса поиска (сервер по ним не ходит)
# --------------------------------------------------------------------------- #

def translit(s: str) -> str:
    """Кириллица → латиница для слагов avtoelon.uz: «Шакман» → «shakman»."""
    return "".join(_CYR.get(ch, ch) for ch in str(s or "").lower())


def olx_query(words: list) -> tuple:
    """(текст запроса, часть адреса q-...): слова через дефис, не-ASCII — процентной кодировкой."""
    text = " ".join(str(w).strip() for w in words if str(w or "").strip())
    slug = re.sub(r"[^\w]+", "-", text.lower().replace("ʻ", "").replace("'", ""), flags=re.U).strip("-_")
    return text, quote(slug, safe="-")


def group_of(object_kind: Optional[str], class_code: Optional[str] = None) -> str:
    kind_type = (tx.OBJECT_KINDS.get(object_kind or "") or (None,))[0] or ""
    return ae.object_group(class_code or None, kind_type, KIND_HINT.get(object_kind or "", ""))


def search_links(q: dict, lang: str) -> list:
    """
    Ссылки поиска на площадках для браузера сотрудника. q: brand, model, year, object_kind, class_code.
    OLX — всегда (раздел, если его адрес известен из valuation_sources.OLX_SECTIONS, иначе общий поиск /list/);
    avtoelon.uz — транспорт и спецтехника; uybor.uz и joymee.uz — недвижимость. Пустой список — искать не по чему.
    """
    from . import valuation                  # разделы спецтехники avtoelon.uz — справочник модуля оценки
    kind = q.get("object_kind") or None
    group = q.get("group") or group_of(kind, q.get("class_code"))
    brand, model, year = (str(q.get(k) or "").strip() for k in ("brand", "model", "year"))
    words = [w for w in (brand, model) if w]
    if not words and kind in tx.OBJECT_KINDS:
        words = [tx.OBJECT_KINDS[kind][1]["ru"]]           # сайты — на русском: ищем по-русски
    links = []
    if not words:
        return links
    sec = OLX_SECTION_BY_KIND.get(kind or "") or OLX_SECTION_BY_GROUP.get(group)
    base = vs.OLX_SECTIONS[sec[0]].rstrip("/") if sec else OLX_ROOT + "/list"
    hint = t("mk_h_olx_section", lang, section=t(sec[1], lang)) if sec else t("mk_h_olx_all", lang)
    text, slug = olx_query(words)
    links.append({"site": "olx", "label": t("mk_l_olx", lang, q=text), "url": f"{base}/q-{slug}/", "hint": hint})
    if year and (brand or model):
        text_y, slug_y = olx_query(words + [year])
        links.append({"site": "olx", "label": t("mk_l_olx_year", lang, q=text_y), "url": f"{base}/q-{slug_y}/",
                      "hint": t("mk_h_olx_year", lang)})
    kind_label = tx.label({k: v[1] for k, v in tx.OBJECT_KINDS.items()}, kind, lang) if kind else ""
    if group in ("special", "vehicle"):
        if kind in CAR_KINDS or (group == "vehicle" and kind is None):
            parts = [AVTOELON_CARS.rstrip("/")] + [vs._slug(translit(w)) for w in (brand, model) if w]
            links.append({"site": "avtoelon", "label": t("mk_l_avtoelon", lang, what=" ".join(words)),
                          "url": "/".join(p for p in parts if p) + "/", "hint": t("mk_h_avtoelon_car", lang)})
        else:
            path = valuation.spec_section(AVTOELON_SUBTYPE.get(kind or "", ""))
            if path:
                parts = [valuation.SPEC_BASE_URL.rstrip("/"), path] + ([vs._slug(translit(brand))] if brand else [])
                url, hint = "/".join(p for p in parts if p) + "/", t("mk_h_avtoelon_section", lang)
            else:
                url, hint = valuation.SPEC_BASE_URL, t("mk_h_avtoelon_all", lang)
            links.append({"site": "avtoelon", "label": t("mk_l_avtoelon", lang, what=kind_label or " ".join(words)),
                          "url": url, "hint": hint})
    if group == "property":
        uy = vs.UYBOR_PAGES[("жилое", "продажа")] if kind == "dwelling" else UYBOR_ROOT
        links.append({"site": "uybor", "label": t("mk_l_uybor", lang), "url": uy, "hint": t("mk_h_uybor", lang)})
        links.append({"site": "joymee", "label": t("mk_l_joymee", lang), "url": JOYMEE_LIST,
                      "hint": t("mk_h_joymee", lang)})
    return links


def shot_tips(lang: str) -> list:
    return [t(k, lang) for k in ("mk_tip_price", "mk_tip_year", "mk_tip_date", "mk_tip_pd")]


# --------------------------------------------------------------------------- #
#  Данные людей в тексте объявления
# --------------------------------------------------------------------------- #

def _s(v, limit: int) -> Optional[str]:
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    if s.lower() in _EMPTY:
        return None
    return s[:limit] or None


def clean_text(v, limit: int, strict: bool = False) -> tuple:
    """
    (значение или None, сколько убрано). Название объявления: телефоны, e-mail, номера документов вырезаются,
    а «Фамилия И. О.» и отчество — признак человека: такое название не возвращается целиком (заглавные слова
    в названии — это марка и модель, общим правилом ФИО их не режем). strict — регион и причина: любое
    похожее на ПД (llm.has_pd) значение не возвращается.
    """
    s = _s(v, limit)
    if not s:
        return None, 0
    if llm.NAME_INITIALS.search(s) or PATRONYMIC.search(s) or (strict and llm.has_pd(s)):
        return None, 1
    out = s
    for rx, _ph in llm.PD_RULES:
        out = rx.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip(" ,;:-—")
    if out != s:
        # номер вырезан — «звоните», «тел.» без номера остаются мусором в названии
        prev = None
        while prev != out:
            prev = out
            out = CALL_TAIL.sub("", out).strip(" ,;:-—.")
    return (out or None), int(out != s)


# --------------------------------------------------------------------------- #
#  Языковая модель: инструкция и разбор ответа
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = (
    "ты читаешь снимки экрана со списком объявлений о продаже с сайтов объявлений. отвечай только объектом json "
    "строго по схеме, без пояснений и без markdown. не выдумывай: чего не видно или не читается, того нет — "
    "ставь null. не извлекай и не возвращай имена, фамилии, телефоны, адреса электронной почты, ники и "
    "фотографии продавцов, даже если они видны. цену переписывай в точности как на снимке, с валютой.")

SCHEMA_HINT = (
    '{"listings": [{"file": 1, "title": "название как в объявлении", "price": 1250000, '
    '"currency": "UZS|USD|у.е.", "year": 2019, "mileage_km": null, "hours": null, "region": "город или null", '
    '"posted": "дата публикации как на снимке или null", "posted_date": "ГГГГ-ММ-ДД или null", '
    '"site": "olx|avtoelon|uybor|joymee|other", "relevant": true, "why_excluded": "строка или null"}]}')

LANG_NAME = {"ru": "русском", "uz": "узбекском (латиница)", "en": "английском"}


def target_text(q: dict) -> str:
    """Что ищем — для модели, строчными словами (без данных людей)."""
    parts = []
    kind = q.get("object_kind")
    if kind in tx.OBJECT_KINDS:
        parts.append("вид: " + tx.OBJECT_KINDS[kind][1]["ru"])
    for key, name in (("brand", "марка"), ("model", "модель"), ("year", "год выпуска")):
        if q.get(key):
            parts.append(f"{name}: {q[key]}")
    return "; ".join(parts) or "не указано — relevant ставь по смыслу списка"


def model_prompt(n: int, lang: str, q: dict, shot_date: date) -> str:
    return (f"приложено снимков экрана: {n}, они пронумерованы по порядку от 1 до {n}. "
            f"дата снимков: {shot_date.isoformat()} — по ней переводи «сегодня», «вчера», «n дней назад» "
            f"в posted_date. объект для сравнения — {target_text(q)}. "
            "верни каждое объявление, которое видно на снимках, одной записью; одно и то же объявление на двух "
            "снимках — один раз. price — число без пробелов, currency — UZS для сумов, USD для долларов, у.е. — "
            "если так написано. mileage_km — пробег в километрах, hours — моточасы, если указаны. "
            "relevant = true, если это то же изделие: та же марка и модель (для недвижимости — тот же вид "
            "объекта); год выпуска, пробег, моточасы, регион и цена на relevant не влияют — другой год той же "
            "модели это relevant = true. другая марка или модель, запчасти, аренда и услуги — relevant = false и "
            f"коротко why_excluded на {LANG_NAME[lang]} языке. site — площадка по виду страницы. "
            f"схема ответа: {SCHEMA_HINT}")


def _num(v, lo: float, hi: float) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        x = ae.to_number(v)
    if x is None or x != x or not lo <= x <= hi:
        return None
    return x


def _currency(v, price_text: str = "") -> Optional[str]:
    s = f"{v or ''} {price_text or ''}".strip().lower()
    if not s:
        return None
    if "у.е" in s or "y.e" in s or "у.e" in s:
        return "у.е."
    if "usd" in s or "$" in s or "доллар" in s or "dollar" in s:
        return "USD"
    if "uzs" in s or "сум" in s or "so'm" in s or "soʻm" in s or "som" in s or "sum" in s:
        return "UZS"
    return None


def _bool(v, default: bool = True) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v if v is not None else "").strip().lower()
    if s in ("true", "1", "yes", "да", "ha"):
        return True
    if s in ("false", "0", "no", "нет", "yoʻq", "yoq"):
        return False
    return default


def normalize_listing(r: dict, shot_date: date, default_site: str, strict_year: bool = False) -> tuple:
    """Одно объявление (от модели или от сотрудника) → чистая запись и число убранных значений ПД."""
    this_year = date.today().year
    dropped = 0
    title, d = clean_text(r.get("title"), 160)
    dropped += d
    region, d = clean_text(r.get("region"), 80, strict=True)
    dropped += d
    why, d = clean_text(r.get("why_excluded"), 160, strict=True)
    dropped += d
    posted, d = clean_text(r.get("posted"), 40, strict=True)
    dropped += d
    raw_price = r.get("price")
    price = _num(raw_price, 1, 1e15)
    cur = r.get("currency")
    cur = cur if cur in CURRENCIES else _currency(cur, raw_price if isinstance(raw_price, str) else "")
    if price is not None and cur is None:
        cur = "UZS"                          # на сайтах Узбекистана без знака валюты — сумы
    year = _num(r.get("year"), 1950, this_year + 1)
    pd_, st = ae.parse_posted_ex(r.get("posted_date"), shot_date)
    if pd_ is None:
        pd2, st2 = ae.parse_posted_ex(posted, shot_date)
        if pd2 is not None or st == "none":
            pd_, st = pd2, st2
    if st == "future" and not posted:
        # дата позже снимка не выбрасывается молча: остаётся текстом, расчёт исключит её как некорректную
        posted, _ = clean_text(r.get("posted_date"), 40, strict=True)
    # площадку, указанную сотрудником, модель не переспорит; не указана — берём с вида страницы
    site = default_site if default_site != "other" else str(r.get("site") or "").strip().lower()
    return {"title": title, "price": price, "currency": cur, "year": int(year) if year else None,
            "mileage_km": _int_or_none(_num(r.get("mileage_km", r.get("mileage")), 0, 5_000_000)),
            "hours": _int_or_none(_num(r.get("hours"), 0, 500_000)), "region": region, "posted": posted,
            "posted_date": pd_.isoformat() if pd_ else None, "site": site if site in SITES else default_site,
            "relevant": _bool(r.get("relevant")), "why_excluded": why}, dropped


def _int_or_none(x):
    return int(x) if x is not None else None


def parse_model(text: str, n: int, shot_date: date, default_site: str) -> Optional[dict]:
    """Ответ модели → {"listings", "dropped", "duplicates"}; не JSON или не та схема — None (честный отказ)."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("listings"), list):
        return None
    out, seen, dropped, dup = [], set(), 0, 0
    for r in data["listings"][:MAX_LISTINGS * 2]:
        if not isinstance(r, dict):
            continue
        row, d = normalize_listing(r, shot_date, default_site)
        dropped += d
        if not row["title"] and row["price"] is None:
            continue
        sig = (re.sub(r"\W+", "", (row["title"] or "").lower()), row["price"], row["currency"], row["year"])
        if sig in seen:                      # снимки при прокрутке перекрываются
            dup += 1
            continue
        seen.add(sig)
        try:
            fi = int(r.get("file"))
            row["file"] = fi if 1 <= fi <= n else None
        except (TypeError, ValueError):
            row["file"] = None
        row["id"] = f"L{len(out) + 1}"
        out.append(row)
        if len(out) >= MAX_LISTINGS:
            break
    return {"listings": out, "dropped": dropped, "duplicates": dup}


# --------------------------------------------------------------------------- #
#  Курс доллара: только из существующего источника или от сотрудника
# --------------------------------------------------------------------------- #

def fx_label(fx: Optional[dict], lang: str) -> Optional[dict]:
    if not fx or not fx.get("rate"):
        return None
    src = t({"cbu": "fx_src_cbu", "cbu_unverified": "fx_src_cbu_unverified", "manual_setting": "fx_src_manual",
             "employee": "fx_src_employee"}.get(fx.get("by"), "fx_src_employee"), lang)
    d = fx.get("as_of") or ""
    when = f"{d[8:10]}.{d[5:7]}.{d[:4]}" if len(d) >= 10 else d
    return {"rate": fx["rate"], "by": fx.get("by"), "as_of": fx.get("as_of"), "source_label": src,
            "text": t("mk_fx_line", lang, rate=rate_text(fx["rate"], lang), date=when, src=src)}


def usd_rate_in(v) -> tuple:
    """(курс или None, ошибка или None). Пусто — не задан."""
    if v in (None, ""):
        return None, None
    x = _num(v, *USD_RATE_BOUNDS) if not isinstance(v, str) or re.fullmatch(r"[\d\s.,]+", v.strip()) else None
    if x is None:
        return None, f"курс — число от {USD_RATE_BOUNDS[0]} до {USD_RATE_BOUNDS[1]} сумов за доллар"
    return x, None


# --------------------------------------------------------------------------- #
#  POST /act/make: объявления с правками сотрудника
# --------------------------------------------------------------------------- #

def safe_url(u) -> Optional[str]:
    """Адрес объявления: только https, без логина в адресе, хост — olx.uz, avtoelon.uz, uybor.uz, joymee.uz."""
    s = str(u or "").strip()
    if not s or len(s) > 300 or re.search(r"[\s<>\"'\\]", s):
        return None
    try:
        x = urlsplit(s)
        port = x.port
    except ValueError:
        return None
    host = (x.hostname or "").lower().rstrip(".")
    if x.scheme.lower() != "https" or x.username or x.password or port not in (None, 443):
        return None
    if not any(host == d or host.endswith("." + d) for d in URL_HOSTS):
        return None
    return s


FX_BY = ("cbu", "manual_setting", "employee")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def _iso_date(v) -> Optional[str]:
    """ГГГГ-ММ-ДД настоящей датой или None."""
    s = str(v or "").strip()
    if not _ISO.fullmatch(s):
        return None
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def validate_market(raw) -> tuple:
    """(чистый блок или None, текст ошибки или None). Недоверенный ввод: типы, суммы, даты, адрес, курс."""
    if raw in (None, "", {}):
        return None, None
    if not isinstance(raw, dict):
        return None, "объект {listings, usd_rate, fx, shots_session}"
    items = raw.get("listings")
    if items is None:
        items = []
    if not isinstance(items, list):
        return None, "listings — список объявлений"
    if len(items) > MAX_LISTINGS:
        return None, f"не больше {MAX_LISTINGS} объявлений"
    rate, err = usd_rate_in(raw.get("usd_rate"))
    if err:
        return None, err
    fx_claim = None
    fx = raw.get("fx")
    if fx not in (None, "", {}):
        if not isinstance(fx, dict):
            return None, "fx — объект {rate, by, as_of}"
        f_rate, err = usd_rate_in(fx.get("rate"))
        if err or f_rate is None:
            return None, "fx.rate: " + (err or "нужен курс")
        if fx.get("by") not in FX_BY:
            return None, "fx.by — cbu, manual_setting или employee"
        as_of = fx.get("as_of")
        if as_of not in (None, "") and _iso_date(as_of) is None:
            return None, "fx.as_of — дата ГГГГ-ММ-ДД"
        fx_claim = {"rate": f_rate, "by": fx["by"], "as_of": _iso_date(as_of)}
    out, dropped, today = [], 0, date.today()
    this_year = today.year
    seen = set()
    for i, r in enumerate(items, start=1):
        if not isinstance(r, dict):
            return None, f"объявление {i}: объект"
        cur = r.get("currency")
        if cur not in (None, "") and cur not in CURRENCIES:
            return None, f"объявление {i}: currency — UZS, USD или у.е."
        p = r.get("price")
        if p not in (None, "") and (_num(p, 1, 1e15) is None or isinstance(p, bool)):
            return None, f"объявление {i}: цена — число больше нуля"
        y = r.get("year")
        if y not in (None, "") and _num(y, 1950, this_year + 1) is None:
            return None, f"объявление {i}: год от 1950 до {this_year + 1}"
        url = r.get("url")
        if url not in (None, ""):
            url = safe_url(url)
            if url is None:
                return None, (f"объявление {i}: адрес — только https на olx.uz, avtoelon.uz, uybor.uz или "
                              "joymee.uz, до 300 знаков")
        pdate = r.get("posted_date")
        if pdate not in (None, "") and _iso_date(pdate) is None:
            return None, f"объявление {i}: дата публикации — ГГГГ-ММ-ДД"
        row, d = normalize_listing(r, today, "other")
        dropped += d
        row["currency"] = cur or row["currency"] or ("UZS" if row["price"] is not None else None)
        row["url"] = url or None
        # дату экрана не пересчитываем от сегодняшнего дня: её проверит расчёт от даты снимков
        row["posted_date"] = _iso_date(pdate) or row["posted_date"]
        rid = str(r.get("id") or "").strip()[:12]
        rid = rid if re.fullmatch(r"[A-Za-z0-9_\-]{1,12}", rid) and rid not in seen else f"m{i}"
        seen.add(rid)
        row["id"] = rid
        row["posted_input"] = bool(r.get("posted_date") or r.get("posted"))
        # что экран говорит о происхождении объявления; сервер верит этому, только если загрузка снимков недоступна
        site = str(r.get("site") or "").strip().lower()
        row["site"] = site if site in SITES else "other"
        row["claimed_source"] = r.get("source") if r.get("source") in ("shot", "manual") else None
        row["claimed_date_assumed"] = r.get("date_assumed") is True
        out.append(row)
    ss = _s(raw.get("shots_session"), 40)
    return {"listings": out, "usd_rate": rate, "fx_claim": fx_claim, "shots_session": ss,
            "dropped_pd": dropped}, None


# поля, правка которых сотрудником видна в акте (отметка «учитывать» — отдельно)
_COMPARE = ("title", "price", "currency", "year", "posted_date")
EDIT_KEYS = ("unchecked", "checked", "price_changed", "removed", "manual")


def _same(key: str, a, b) -> bool:
    if key == "price":
        try:
            return (a is None and b is None) or (a is not None and b is not None and float(a) == float(b))
        except (TypeError, ValueError):
            return False
    return (a or None) == (b or None)


def trust_listings(items: list, shots: Optional[dict]) -> dict:
    """
    Источник каждого объявления и правки сотрудника (items меняется на месте).
    Загрузка снимков доступна: shot — сервер сам прочитал объявление со снимков этой загрузки (совпал id),
    manual — добавлено сотрудником; правки цены, валюты, года, даты публикации, названия и отметки сверяются
    с загрузкой; объявления загрузки, которых нет во вводе, возвращаются в список как «убрано сотрудником».
    Загрузки нет (истекла, чужая, сменилась сессия): все объявления — «введено сотрудником», а дату публикации
    и площадку берём из ввода экрана.
    Возвращает {"manual", "edited", "edits": {unchecked, checked, price_changed, removed, manual, items}}.
    """
    edits = {k: 0 for k in EDIT_KEYS}
    edits["items"] = []

    def note(r, what, was=None, now=None):
        edits["items"].append({"id": r.get("id"), "title": r.get("title"), "what": what, "was": was, "now": now,
                               "currency": r.get("currency")})

    known = {r["id"]: r for r in (shots or {}).get("listings") or [] if r.get("id")}
    edited = manual = 0
    for r in items:
        r["edits"] = []
        src = known.get(r["id"]) if shots else None
        if src is None:
            r["source"], r["edited"] = "manual", False
            manual += 1
            edits["manual"] += 1
            note(r, "manual", None, r.get("price"))
            continue
        r["source"] = "shot"
        r["site"] = src.get("site") or "other"
        r["file"] = src.get("file")
        r["posted"] = src.get("posted")
        if not r.get("posted_input"):
            r["posted_date"] = src.get("posted_date")
        changes = []
        for k in _COMPARE:
            if not _same(k, src.get(k), r.get(k)):
                changes.append(k)
                note(r, k, src.get(k), r.get(k))
        if any(k in ("price", "currency") for k in changes):
            edits["price_changed"] += 1
        was_rel, now_rel = src.get("relevant") is not False, r.get("relevant") is not False
        if was_rel and not now_rel:
            r["off_by"] = "employee"
            edits["unchecked"] += 1
            changes.append("relevant")
            note(r, "unchecked", True, False)
        elif not was_rel and now_rel:
            r["on_by"] = "employee"
            edits["checked"] += 1
            changes.append("relevant")
            note(r, "checked", src.get("why_excluded"), True)
        if not was_rel:
            r["why_excluded"] = src.get("why_excluded")       # причина модели, а не текст экрана
        r["edits"] = changes
        r["edited"] = bool(changes)
        edited += int(r["edited"])
    given = {r["id"] for r in items}
    for rid, src in known.items():
        if rid in given:
            continue
        row = {k: src.get(k) for k in LISTING_KEYS if k in src}
        row.update(id=rid, source="shot", removed=True, edited=True, edits=["removed"], used=False)
        items.append(row)
        edits["removed"] += 1
        note(row, "removed")
    return {"manual": manual, "edited": edited, "edits": edits}


# --------------------------------------------------------------------------- #
#  Раздел 3 акта и блок market_value
# --------------------------------------------------------------------------- #

def _dmy(iso: Optional[str]) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}" if iso and len(iso) >= 10 else (iso or "")


def _k(x, lang: str) -> str:
    s = f"{float(x):g}"
    return s if lang == "en" else s.replace(".", ",")


def rate_text(rate, lang: str) -> str:
    """Курс «12 650,00 сум» (en — «12,650.00 UZS»)."""
    lg = tx.lang_of(lang)
    return tx._num(float(rate), lg, 2) + tx.NBSP + {"ru": "сум", "uz": "soʻm", "en": "UZS"}[lg]


def _how_text(h: dict, lang: str) -> str:
    p = dict(h.get("params") or {})
    for key in ("median", "low", "high", "med0", "declared"):
        if key in p:
            p[key] = money(p[key], lang)
    if "diff" in p:
        p["diff"] = tx.pct_fixed(p["diff"], lang, 1)
    if "thr" in p:
        p["thr"] = pct(p["thr"], lang)
    for key in ("lo", "hi"):
        if key in p:
            p[key] = _k(p[key], lang)
    if "rate" in p:
        p["rate"] = rate_text(p["rate"], lang)
    return t(h["code"], lang, **p)


def excluded_text(e: dict, lang: str) -> str:
    p = dict(e.get("params") or {})
    if e["code"] in ("mx_outlier_low", "mx_outlier_high"):
        p.update(price=money(p.get("price"), lang), median=money(p.get("median"), lang), k=_k(p.get("k"), lang))
    if e["code"] == "mx_too_old":
        p["date"] = _dmy(p.get("date"))
    if e["code"] == "mx_bad_date":
        p["shot"] = _dmy(p.get("shot"))
    reason = t(e["code"], lang, **{k: v for k, v in p.items() if k != "why"})
    if e["code"] == "mx_not_relevant" and p.get("why"):
        reason += f" ({p['why']})"
    title = e.get("title") or t("mv_untitled", lang, id=e.get("id") or "")
    return t("mv_excl_item", lang, title=title, reason=reason)


def _num_text(v, cur: Optional[str], lang: str) -> str:
    """Цена правки: «2 650 000 000» (сумы — без подписи, как в объявлении), доллары — с валютой."""
    if v is None:
        return "—"
    s = tx._num(float(v), tx.lang_of(lang), 0)
    return s if (cur or "UZS") == "UZS" else f"{s}{tx.NBSP}{cur}"


def edit_text(e: dict, lang: str) -> str:
    """Одна правка сотрудника словами: что было, что стало."""
    title = e.get("title") or t("mv_untitled", lang, id=e.get("id") or "")
    what, was, now = e.get("what"), e.get("was"), e.get("now")
    if what == "price":
        body = t("me_price", lang, was=_num_text(was, e.get("currency"), lang), now=_num_text(now, e.get("currency"), lang))
    elif what == "posted_date":
        body = t("me_posted_date", lang, was=_dmy(was) or "—", now=_dmy(now) or "—")
    elif what in ("currency", "year", "title"):
        body = t("me_" + what, lang, was=was if was not in (None, "") else "—", now=now if now not in (None, "") else "—")
    elif what == "checked":
        body = t("me_checked", lang) + (f" ({was})" if was else "")
    elif what == "manual":
        body = t("me_manual", lang, price=_num_text(now, e.get("currency"), lang))
    else:
        body = t("me_" + str(what), lang)
    return t("mv_excl_item", lang, title=title, reason=body)


def edits_line(M: dict, lang: str) -> Optional[str]:
    """«Правки сотрудника: снято N, включено N, исправлено цен N, убрано N, добавлено вручную N» или «правок нет»."""
    ed = M.get("edits")
    if not isinstance(ed, dict):
        return None
    if not any(ed.get(k) for k in EDIT_KEYS) and not ed.get("items"):
        return t("mv_edits_none", lang)
    return t("mv_edits_line", lang, **{k: int(ed.get(k) or 0) for k in EDIT_KEYS})


def source_lines(M: dict, lang: str) -> list:
    """Строки источника под оценкой: площадка и дата снимков, введённое вручную, недоступные снимки, правки,
    ссылка поиска."""
    alive = [r for r in M.get("listings") or [] if not r.get("removed")]
    used = [r for r in alive if r.get("used")]
    rows = used or alive
    sites = []
    for r in rows:
        if r.get("source") == "shot" and r.get("site") not in sites:
            sites.append(r.get("site") or "other")
    manual = sum(1 for r in rows if r.get("source") == "manual")
    out = []
    if sites:
        out.append(t("mv_src_shots", lang, site=", ".join(tx.label(tx.SITE_LABELS, s, lang) for s in sites),
                     date=_dmy(M.get("shot_date"))))
    if manual or not sites:
        out.append(t("mv_src_manual", lang, n=manual))
    if M.get("shots_missing"):
        out.append(t("mv_shots_missing", lang))
    line = edits_line(M, lang)
    if line:
        out.append(line)
    link = next((ln for ln in M.get("links") or [] if ln["site"] in sites), None) or \
        next(iter(M.get("links") or []), None)
    if link:
        out.append(t("mv_search", lang, url=link["url"]))
    return out


def source_label(M: dict, lang: str) -> Optional[str]:
    lines = source_lines(M, lang)
    return lines[0] if lines else None


def verdict_text(M: dict, lang: str) -> str:
    v = M.get("verdict") or "none"
    rule = M.get("rule") or {}
    if v == "confirmed":
        return t("mvv_confirmed", lang, diff=tx.pct_fixed(abs(M["diff_pct"]), lang, 1),
                 thr=pct(rule.get("diff_pct"), lang))
    if v == "refine":
        return t("mvv_refine", lang, declared=money(M["declared"], lang), median=money(M["median"], lang),
                 diff=tx.pct_fixed(abs(M["diff_pct"]), lang, 1), thr=pct(rule.get("diff_pct"), lang),
                 refined=money(M["refined_value"], lang),
                 ref={"ru": "ГК РУз, ст. 935", "uz": "OʻzR Fuqarolik kodeksi, 935-modda",
                      "en": "Civil Code of Uzbekistan, Art. 935"}[tx.lang_of(lang)])
    if v == "few":
        return t("mvv_few", lang, n=M.get("used") or 0, min=rule.get("min_listings"))
    if v == "ready":
        return t("mvv_ready", lang)
    return t("mvv_none", lang)


def insured_text(ic: Optional[dict], lang: str) -> Optional[str]:
    if not ic:
        return None
    ratio = pct(ic["ratio_pct"], lang, 2)
    ref = tx.label(tx.LEGAL_REFS, ic["legal_ref"], lang) if ic.get("legal_ref") else None
    if ic["verdict"] == "over":
        return t("v_over", lang, diff=money(ic["diff"], lang), ref=ref)
    if ic["verdict"] == "under":
        return t("v_under", lang, ratio=ratio, ref=ref)
    if ref:
        return t("v_normal", lang, ratio=ratio) + " " + t("v_under_small", lang, ratio=ratio, ref=ref)
    return t("v_normal", lang, ratio=ratio)


LISTING_KEYS = ("id", "title", "price", "currency", "price_uzs", "year", "mileage_km", "hours", "region", "posted",
                "posted_date", "date_assumed", "site", "relevant", "why_excluded", "used", "source", "edited", "url",
                "file")
# добавлены 30.09.2026 (правки сотрудника и дата): в загрузке снимков не хранятся, только в ответе и в акте
LISTING_EXTRA = ("date_status", "off_by", "on_by", "removed", "edits")


def listing_view(r: dict, lang: str) -> dict:
    out = {k: r.get(k) for k in LISTING_KEYS + LISTING_EXTRA}
    out["removed"] = bool(r.get("removed"))
    out["edits"] = list(r.get("edits") or [])
    out["site_label"] = tx.label(tx.SITE_LABELS, r.get("site") or "other", lang)
    if r.get("date_status") == "future":
        out["date_note"] = t("mk_date_bad", lang)
    elif r.get("date_assumed"):
        out["date_note"] = t("mk_date_assumed" if r.get("used") else "mk_date_missing", lang)
    else:
        out["date_note"] = None
    notes = []
    if r.get("on_by") == "employee":
        notes.append(t("me_checked", lang))
    if r.get("off_by") == "employee":
        notes.append(t("me_unchecked", lang))
    if r.get("removed"):
        notes.append(t("me_removed", lang))
    out["edit_note"] = "; ".join(notes) or None
    out["check_label"] = t("check_mark", lang)
    return out


def estimate_view(M: dict, lang: str) -> dict:
    """Короткая сводка расчёта для экрана (предпросмотр после чтения снимков)."""
    return {"available": bool(M.get("available")), "verdict": M.get("verdict"),
            "verdict_label": tx.label(tx.MV_VERDICT_LABELS, M.get("verdict") or "none", lang),
            "verdict_text": verdict_text(M, lang), "count": M.get("count", 0), "used": M.get("used", 0),
            "median": M.get("median"), "low": M.get("low"), "high": M.get("high"), "currency": "UZS",
            "excluded": [{"id": e.get("id"), "code": e["code"], "reason": excluded_text(e, lang)}
                         for e in M.get("excluded") or []],
            "how": [_how_text(h, lang) for h in M.get("how") or []] + [t("mh_calibrated", lang)],
            "calibrated": ae.CALIBRATED}


def market_view(M: Optional[dict], lang: str) -> dict:
    """{"json": блок market_value, "rows", "source_lines", "lists"} для раздела 3 (пусто, если оценки нет)."""
    lang = tx.lang_of(lang)
    if not M:
        return {"json": {"available": False, "count": 0, "used": 0, "excluded": [], "median": None, "low": None,
                         "high": None, "currency": "UZS", "declared": None, "diff_pct": None, "verdict": "none",
                         "verdict_label": tx.label(tx.MV_VERDICT_LABELS, "none", lang), "verdict_text": None,
                         "refined_value": None, "insured_check": None, "source_label": None, "source_lines": [],
                         "shot_date": None, "links": [], "how": [], "listings": [], "fx": None,
                         "edits": None, "median_original": None, "shots_missing": False,
                         "shots_missing_text": None, "calibrated": ae.CALIBRATED},
                "rows": [], "source_lines": [], "lists": []}
    ev = estimate_view(M, lang)
    ic = M.get("insured_check")
    lines = source_lines(M, lang)
    ed = M.get("edits") if isinstance(M.get("edits"), dict) else None
    edits_js = ({**{k: int(ed.get(k) or 0) for k in EDIT_KEYS}, "line": edits_line(M, lang),
                 "items": [{"id": e.get("id"), "title": e.get("title"), "what": e.get("what"), "was": e.get("was"),
                            "now": e.get("now"), "text": edit_text(e, lang)} for e in ed.get("items") or []]}
                if ed is not None else None)
    js = {**ev, "declared": M.get("declared"), "diff_pct": M.get("diff_pct"), "refined_value": M.get("refined_value"),
          "insured_check": ({**ic, "legal_ref_text": tx.label(tx.LEGAL_REFS, ic["legal_ref"], lang)
                             if ic.get("legal_ref") else None, "text": insured_text(ic, lang)} if ic else None),
          "source_label": lines[0] if lines else None, "source_lines": lines,
          "source": M.get("source") or {}, "shot_date": M.get("shot_date"),
          "links": M.get("links") or [], "listings": [listing_view(r, lang) for r in M.get("listings") or []],
          "date_assumed": M.get("date_assumed") or [], "usd_rate": M.get("usd_rate"),
          "fx": fx_label(M.get("fx"), lang), "rule": M.get("rule"), "edits": edits_js,
          "median_original": M.get("median_original"),
          "shots_missing": bool(M.get("shots_missing")),
          "shots_missing_text": t("mv_shots_missing", lang) if M.get("shots_missing") else None,
          "calibrated": ae.CALIBRATED}
    edit_list = ([{"title": t("mv_edits_title", lang), "items": [e["text"] for e in edits_js["items"]]}]
                 if edits_js and edits_js["items"] else [])
    if not M.get("available"):
        # оценки нет, но почему — видно: что не вошло в расчёт и какие правки сделал сотрудник
        if not M.get("listings"):
            return {"json": js, "rows": [], "source_lines": [], "lists": []}
        lists = ([{"title": t("mv_excl_title", lang), "items": [e["reason"] for e in ev["excluded"]]}]
                 if ev["excluded"] else []) + edit_list
        return {"json": js, "rows": [], "source_lines": lines, "lists": lists}
    R = lambda label, value, note=None: {"label": label, "value": value, "note": note}  # noqa: E731
    med_note = t("uncalibrated", lang)
    if ed is not None and M.get("median_original") != M.get("median") and "median_original" in M:
        med_note += "; " + (t("mv_median_orig", lang, median=money(M["median_original"], lang))
                            if M.get("median_original") is not None else t("mv_median_orig_none", lang))
    rows = [R(t("mv_title", lang), ev["verdict_label"]),
            R(t("mv_median", lang), money(M["median"], lang), med_note),
            R(t("mv_range", lang), t("mv_range_val", lang, low=money(M["low"], lang), high=money(M["high"], lang))),
            R(t("mv_count", lang), t("mv_count_val", lang, used=M["used"], count=M["count"]))]
    if M.get("diff_pct") is not None:
        rows.append(R(t("mv_diff", lang), tx.pct_fixed(abs(M["diff_pct"]), lang, 1),
                      t("mv_diff_up" if M["diff_pct"] > 0 else "mv_diff_down", lang)))
    rows.append(R(t("mv_verdict", lang), ev["verdict_text"]))
    if M.get("verdict") == "refine":
        rows.append(R(t("mv_refined", lang), money(M["refined_value"], lang)))
        if ic:
            rows.append(R(t("mv_insured", lang), pct(ic["ratio_pct"], lang, 2), insured_text(ic, lang)))
    lists = [{"title": t("mv_how_title", lang), "items": ev["how"]}]
    if ev["excluded"]:
        lists.append({"title": t("mv_excl_title", lang), "items": [e["reason"] for e in ev["excluded"]]})
    lists += edit_list
    return {"json": js, "rows": rows, "source_lines": lines, "lists": lists}


def edits_shift_median(M: Optional[dict]) -> bool:
    """Правки сотрудника есть и медиана с ними не та, что по объявлениям как их прочитала модель."""
    if not M or "median_original" not in M:
        return False
    ed = M.get("edits") or {}
    if not any(ed.get(k) for k in EDIT_KEYS if k != "manual") and not ed.get("items"):
        return False
    return M.get("median_original") != M.get("median")


def decision_checks(M: Optional[dict]) -> list:
    """Что проверить андеррайтеру по оценке: расхождение со стоимостью (refine) и правки, сдвинувшие медиану."""
    out = []
    if M and edits_shift_median(M):
        ed = M.get("edits") or {}
        out.append({"code": "c_market_edits", "params": {"median": M.get("median"),
                                                         "median_original": M.get("median_original"),
                                                         **{k: int(ed.get(k) or 0) for k in EDIT_KEYS}}})
    if not M or M.get("verdict") != "refine":
        return out
    out.append({"code": "c_market_refine", "params": {"n": M["used"], "median": M["median"], "low": M["low"],
                                                      "high": M["high"], "declared": M["declared"],
                                                      "diff": abs(M["diff_pct"])}})
    ic = M.get("insured_check") or {}
    if ic.get("verdict") == "under":
        out.append({"code": "c_market_under", "params": {"ratio": ic["ratio_pct"], "ref": ic["legal_ref"]}})
    elif ic.get("verdict") == "over":
        out.append({"code": "c_market_over", "params": {"diff": ic["diff"], "ref": ic["legal_ref"]}})
    return out


VERDICT_WORDS = {"normal": {"ru": "в норме", "uz": "meʼyorda", "en": "within the norm"},
                 "under": {"ru": "недострахование", "uz": "toʻliq sugʻurtalanmagan", "en": "underinsurance"},
                 "over": {"ru": "превышение", "uz": "sugʻurta summasi oshib ketgan", "en": "overinsurance"}}


def final_verdict(value: dict, M: Optional[dict]) -> str:
    """Итоговый вывод раздела 3 и плитки сводки: refine, если объявления требуют уточнить стоимость."""
    return "refine" if M and M.get("verdict") == "refine" else value.get("verdict")


def final_text(value: dict, M: Optional[dict], lang: str, base: str) -> str:
    """
    Один вывод без противоречий: при refine — сумма к заявленной стоимости, затем к уточнённой и итог.
    Иначе — прежний текст вывода (base).
    """
    if final_verdict(value, M) != "refine":
        return base
    lg = tx.lang_of(lang)
    ic = M.get("insured_check") or {}
    diff = tx.pct_fixed(abs(M.get("diff_pct") or 0), lang, 1)
    direction = t("vf_lower" if (M.get("diff_pct") or 0) > 0 else "vf_higher", lang)
    head = t("vf_declared", lang, ratio=pct(value["ratio_pct"], lang, 2), v=VERDICT_WORDS[value["verdict"]][lg])
    if ic.get("ratio_pct") is not None:
        ref = tx.label(tx.LEGAL_REFS, ic["legal_ref"], lang) if ic.get("legal_ref") else None
        mid = t("vf_market", lang, dir=direction, diff=diff, ratio=pct(ic["ratio_pct"], lang, 2),
                v=VERDICT_WORDS[ic["verdict"]][lg] + (f" ({ref})" if ref else ""))
    else:
        mid = t("vf_market_short", lang, dir=direction, diff=diff)
    return f"{head} {mid} {t('vf_total', lang)}"


def declared_text(orig, value, M: Optional[dict], lang: str) -> Optional[tuple]:
    """(подпись, текст, источник) о замене стоимости объекта: клиент заявил orig, в акте — value."""
    if not orig or not value or float(orig) == float(value):
        return None
    if M and M.get("median") is not None and float(M["median"]) == float(value):
        return (t("mv_declared_orig", lang), t("mv_declared_replaced", lang, orig=money(orig, lang),
                                                value=money(value, lang)), "listings")
    return (t("mv_declared_orig", lang), t("mv_declared_changed", lang, orig=money(orig, lang),
                                            value=money(value, lang)), "employee")


def check_text(c: dict, lang: str) -> Optional[str]:
    p = c.get("params") or {}
    if c["code"] == "c_market_edits":
        orig = p.get("median_original")
        return t("c_market_edits", lang, median=money(p["median"], lang) if p.get("median") is not None
                 else t("mv_orig_none", lang),
                 orig=money(orig, lang) if orig is not None else t("mv_orig_none", lang),
                 **{k: int(p.get(k) or 0) for k in EDIT_KEYS})
    if c["code"] == "c_market_refine":
        return t("c_market_refine", lang, n=p["n"], median=money(p["median"], lang), low=money(p["low"], lang),
                 high=money(p["high"], lang), declared=money(p["declared"], lang),
                 diff=tx.pct_fixed(p["diff"], lang, 1))
    if c["code"] == "c_market_under":
        return t("c_market_under", lang, ratio=pct(p["ratio"], lang, 2), ref=tx.label(tx.LEGAL_REFS, p["ref"], lang))
    if c["code"] == "c_market_over":
        return t("c_market_over", lang, diff=money(p["diff"], lang), ref=tx.label(tx.LEGAL_REFS, p["ref"], lang))
    return None
