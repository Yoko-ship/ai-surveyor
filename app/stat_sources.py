"""
Открытые данные органа государственной статистики Узбекистана — слой загрузки.

Зачем это сюрвейеру. Ставка должна опираться не только на отчёты НАПП (премии, выплаты,
обязательства рынка), но и на «знаменатель» — сколько людей, домохозяйств, жилья и денег
в регионе. Отсюда берутся: проникновение страхования по регионам, оценка стоимости
недвижимости там, где рынка объявлений нет, и индексация страховых сумм и базовых ставок.

Модуль — ЧИСТЫЕ функции: реестр наборов, загрузка, разбор, сохранение в таблицу stat_series.
Ни FastAPI, ни роутера, ни расписания здесь нет (это делает app/statagency.py).

Жёсткие правила модуля (те же, что в app/valuation_sources.py):
  * только стандартная библиотека Python (urllib, json, re, sqlite3);
  * robots.txt каждого домена читается и соблюдается (используется robots_check из
    app.valuation_sources); robots недоступен — источник считается закрытым;
  * пауза не менее 2 секунд между обращениями к одному домену; честный User-Agent;
  * никакого обхода защит, капч и приватных ключей. Не отдаёт — статус
    «источник недоступен» с причиной. Это нормальный результат, а не ошибка;
  * ничего не выдумывается: нет значения у источника — в базу идёт NULL, а не оценка.

Что найдено на 20.09.2026 (проверено обращением к сайтам):

  1. stat.uz — сайт Национального комитета Республики Узбекистан по статистике.
     robots.txt открыт (Joomla, закрыты только служебные папки). Разделы официальной
     статистики отдаются готовым HTML, и у КАЖДОГО набора рядом лежат ссылки на
     машинные форматы: xlsx / csv / json / xml. Файлы лежат на api.siat.stat.uz
     (SIAT — система интерактивного анализа статистики), формат один и тот же:
         https://api.siat.stat.uz/media/uploads/sdmx/sdmx_data_<N>.json
     Внутри: блок metadata (имя показателя, периодичность, единица, методика, даты
     публикации и последнего изменения) и блок data — строки «код классификатора →
     значения по периодам». Регионы кодируются СОАТО (1700 республика, 1703 Андижан …).
     Каталог собран со страниц раздела «Официальная статистика»: 1 189 наборов.

  2. api.stat.uz — отдельного API по этому адресу НЕТ: домен отдаёт обычную страницу
     сайта stat.uz («Перечень интерактивных услуг»). Реальный машинный адрес —
     api.siat.stat.uz (см. выше).

  3. data.egov.uz — портал открытых данных. robots.txt открыт. Сайт — SPA (Nuxt),
     но его клиентский API отвечает без ключа и без авторизации:
         каталог:  https://data.egov.uz/apiClient/main/gettable?limit=20&offset=0&text=<поиск>
         данные:   https://data.egov.uz/apiData/MainData/GetByFile?id=<structId>&fileType=1&tableType=2
     (fileType: 1 = json, 2 = xml, 3 = xls, 4 = csv). Всего в каталоге 13 098 наборов.

Чего в открытых данных НЕТ (искали, не нашли — не выдумываем):
  * парка автотранспорта по регионам (единиц на учёте). У МВД на data.egov.uz есть
    набор «Данные об автомототранспортных средствах (без личной идентификации)»,
    но это сервис проверки одной машины по госномеру и техпаспорту, а не сводка
    по регионам. Ближайшая замена — «Количество автомобилей на 100 домохозяйств»
    (stat.uz), но она даётся ТОЛЬКО по республике, без регионов;
  * пожаров и ущерба от них по регионам. Единственный набор про пожары
    (Мининфоком, «пожары в лесных хозяйствах, на складах нефтегазовой и химической
    продукции и пунктах хранения хлопка») состоит из одного поля-ссылки и данных
    не содержит. У МЧС есть «Статистика произошедших чрезвычайных ситуаций», но там
    только два квартала (II кв. 2025 и II кв. 2026), три строки и нет ни регионов,
    ни числа пострадавших объектов, ни сумм ущерба;
  * числа домохозяйств по регионам как отдельного показателя. Есть средний состав
    домохозяйства по регионам (человек) — число домохозяйств из него можно получить
    делением населения на средний состав, но это уже РАСЧЁТ, а не данные источника,
    поэтому здесь он не делается (производные показатели — задача app/statagency.py).
"""
import json
import re
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app import db
from app.valuation_sources import _http_get, robots_check

ROOT = Path(__file__).resolve().parent.parent
# База по умолчанию — та, с которой работает сервер СЕЙЧАС (db.DB_PATH читается при вызове,
# а не при импорте: STORAGE_DIR и подмена базы в тестах должны учитываться).
DB = None

STATUS_OK = "ok"
STATUS_EMPTY = "нет данных"
STATUS_DOWN = "источник недоступен"
STATUS_ROBOTS = "запрещено robots.txt"

SOURCE_STAT = "stat.uz"
SOURCE_EGOV = "data.egov.uz"

SIAT_JSON = "https://api.siat.stat.uz/media/uploads/sdmx/sdmx_data_%s.json"
EGOV_DATA = "https://data.egov.uz/apiData/MainData/GetByFile?id=%s&fileType=1&tableType=2"
EGOV_PAGE = "https://data.egov.uz/rus/data/%s"


# --------------------------------------------------------------------------- #
# Регионы: СОАТО у stat.uz → ключи region:* из таблицы market_stats
# --------------------------------------------------------------------------- #
# Слева — код СОАТО и русское название, как их отдаёт stat.uz.
# Справа — ключ, которым тот же регион назван в market_stats (там он собран из
# узбекских названий отчётов НАПП). Совпадение проверено по русским названиям:
# market_stats.row_name и stat.uz «Klassifikator_ru» совпадают дословно.
REGIONS: Dict[str, str] = {
    "1700": "total",                                   # Республика Узбекистан целиком
    "1735": "region:QORAQALPOG'ISTON RESPUBLIKASI",    # Республика Каракалпакстан
    "1703": "region:ANDIJON",                          # Андижанская область
    "1706": "region:BUXORO",                           # Бухарская область
    "1708": "region:JIZZAX",                           # Джизакская область
    "1710": "region:QASHQADARYO",                      # Кашкадарьинская область
    "1712": "region:NAVOIY",                           # Навоийская область
    "1714": "region:NAMANGAN",                         # Наманганская область
    "1718": "region:SAMARQAND",                        # Самаркандская область
    "1722": "region:SURXANDARYO",                      # Сурхандарьинская область
    "1724": "region:SIRDARYO",                         # Сырдарьинская область
    "1727": "region:TOSHKENT",                         # Ташкентская область
    "1730": "region:FARG'ONA",                         # Ферганская область
    "1733": "region:XORAZM",                           # Хорезмская область
    "1726": "region:TOSHKENT SHAHRI",                  # город Ташкент
}

# Названия регионов по-русски (для показа заказчику, без обращения к сети)
REGION_NAMES_RU: Dict[str, str] = {
    "total": "Республика Узбекистан",
    "region:QORAQALPOG'ISTON RESPUBLIKASI": "Республика Каракалпакстан",
    "region:ANDIJON": "Андижанская область",
    "region:BUXORO": "Бухарская область",
    "region:JIZZAX": "Джизакская область",
    "region:QASHQADARYO": "Кашкадарьинская область",
    "region:NAVOIY": "Навоийская область",
    "region:NAMANGAN": "Наманганская область",
    "region:SAMARQAND": "Самаркандская область",
    "region:SURXANDARYO": "Сурхандарьинская область",
    "region:SIRDARYO": "Сырдарьинская область",
    "region:TOSHKENT": "Ташкентская область",
    "region:FARG'ONA": "Ферганская область",
    "region:XORAZM": "Хорезмская область",
    "region:TOSHKENT SHAHRI": "город Ташкент",
}


def region_key(code: str) -> str:
    """
    Код СОАТО → ключ region:* как в market_stats. Районы и города районного
    значения (коды длиннее четырёх знаков) разрезом не являются: '' — разреза нет.
    Неизвестный код — тоже '', ничего не домысливаем.
    """
    return REGIONS.get(str(code).strip(), "")


# --------------------------------------------------------------------------- #
# Реестр наборов
# --------------------------------------------------------------------------- #
# Поля:
#   id          — наш устойчивый ключ (попадает в stat_series.dataset_id);
#   name        — название по-русски, как у источника;
#   source      — 'stat.uz' | 'data.egov.uz';
#   src_id      — номер набора у источника;
#   page        — страница раздела/набора: она пишется в stat_series.url;
#   data_url    — адрес машинных данных;
#   period      — периодичность, как её объявил источник;
#   unit        — единица измерения;
#   regions     — True, если строки набора — регионы (СОАТО);
#   purpose     — зачем сюрвейеру;
#   limits      — честные ограничения набора.

_PAGE = "https://stat.uz/ru/ofitsialnaya-statistika/%s"


def _siat(ds_id, name, src_id, topic, period, unit, regions, purpose, limits=()):
    return {
        "id": ds_id, "name": name, "source": SOURCE_STAT, "src_id": str(src_id),
        "page": _PAGE % topic, "data_url": SIAT_JSON % src_id,
        "period": period, "unit": unit, "regions": regions,
        "purpose": purpose, "limits": list(limits),
    }


def _egov(ds_id, name, struct_id, period, unit, regions, purpose, limits=()):
    return {
        "id": ds_id, "name": name, "source": SOURCE_EGOV, "src_id": struct_id,
        "page": EGOV_PAGE % struct_id, "data_url": EGOV_DATA % struct_id,
        "period": period, "unit": unit, "regions": regions,
        "purpose": purpose, "limits": list(limits),
    }


DATASETS: Dict[str, dict] = {d["id"]: d for d in [
    # --- знаменатель проникновения страхования по регионам ---
    _siat("population", "Численность постоянного населения (всего)", 246, "demography",
          "на начало года", "тысяч человек", True,
          "Знаменатель проникновения страхования по регионам: премии на душу населения.",
          ["значения на начало года, а премии НАПП — за период; сопоставлять надо "
           "премии года N с населением на начало года N"]),
    _siat("household_size", "Информация о среднем составе домашних хозяйств", 1325, "living-standards",
          "ежегодный", "человек", True,
          "Средний размер домохозяйства: из него и населения получается число домохозяйств "
          "— знаменатель для страхования жилья.",
          ["само число домохозяйств по регионам орган статистики открыто не публикует; "
           "деление населения на средний состав — это уже расчёт, а не данные источника",
           "показатель выборочный (обследование домохозяйств)"]),

    # --- оценка стоимости недвижимости (классы 8 и 9) ---
    _siat("housing_fund_area", "Общая площадь жилищного фонда", 1244, "environment",
          "ежегодный", "на конец года, кв. м", True,
          "Жилой фонд региона: экспозиция по классам 8 и 9 и база для средней площади жилья.",
          ["на конец года; свежесть — год назад"]),
    _siat("dwellings_count", "Количество квартир (домов)", 1245, "environment",
          "ежегодный", "на конец года, единиц", True,
          "Число жилых единиц в регионе: вместе с площадью фонда даёт среднюю площадь квартиры.",
          ["на конец года"]),
    _siat("housing_fund_by_walls_brick", "Распределение жилищного фонда по материалу стен: жжёный кирпич",
          1256, "environment", "ежегодный", "на конец года, тысяч кв. м", True,
          "Доля огнестойких конструкций в регионе — фон для коэффициента «конструкция» класса 8.",
          ["это структура всего фонда, а не конкретного объекта; "
           "коэффициент объекта по ней калибровать нельзя"]),
    _siat("housing_commissioned", "Общая площадь введённого в эксплуатацию жилья", 1905, "investments",
          "годовая и квартальная", "тысяча квадратных метров", True,
          "Ввод жилья: новый фонд, который приходит на страхование, и признак активности рынка.",
          ["в одном наборе смешаны годовые и квартальные столбцы — они разведены по полю period"]),
    _siat("house_price_index", "Динамика индекса цен на рынке жилья (к предыдущему кварталу)",
          1305, "prices-and-indexes", "ежеквартально", "процент", True,
          "Индексация страховых стоимостей жилья по регионам там, где площадок объявлений нет.",
          ["это ИНДЕКС (к предыдущему кварталу), а не цена квадратного метра; "
           "уровня цен орган статистики открыто не даёт",
           "ряд начинается с 2022-Q1"]),
    _siat("house_price_index_primary", "Динамика индекса цен на первичном рынке жилья (к предыдущему кварталу)",
          1306, "prices-and-indexes", "ежеквартально", "процент", True,
          "То же для новостроек.", ["индекс, не уровень цен"]),
    _siat("house_price_index_secondary", "Динамика индекса цен на вторичном рынке жилья (к предыдущему кварталу)",
          1307, "prices-and-indexes", "ежеквартально", "процент", True,
          "То же для вторичного жилья.", ["индекс, не уровень цен"]),

    # --- индексация страховых сумм и базовых ставок ---
    _siat("cpi_annual", "Годовая динамика индекса потребительских цен (к декабрю предыдущего года)",
          3003, "prices-and-indexes", "ежегодно", "процент", False,
          "Инфляция: индексация лимитов, франшиз и базовых ставок между версиями тарифа.",
          ["только по республике, разреза регионов нет"]),
    _siat("construction_price_index_m", "Динамика индекса цен на инвестиции в основной капитал "
          "и строительные работы (к предыдущему месяцу)", 1301, "prices-and-indexes",
          "ежемесячный", "процент", False,
          "Приведение страховой стоимости строений и СМР к дате договора.",
          ["строки — не регионы, а виды затрат (сводный индекс, СМР, материалы …); "
           "разреза регионов нет",
           "месяцы до 2021 года подписаны кириллической «М», позже латинской «M» — "
           "период нормализуется к виду ГГГГ-MNN"]),
    _siat("construction_price_index_y", "Годовая динамика индекса цен на инвестиции в основной "
          "капитал и строительные работы (к декабрю предыдущего года)", 1302, "prices-and-indexes",
          "ежегодно", "процент", False,
          "Годовая индексация страховых стоимостей строений.",
          ["только по республике"]),
    _siat("grp", "Объём валового внутреннего (регионального) продукта", 1299, "national-accounts",
          "ежегодный", "в текущих ценах, млрд сум", True,
          "ВРП региона: масштаб экономики — база для оценки потенциала сборов и "
          "для сравнения регионов между собой.", ["годовой, публикуется с задержкой"]),
    _siat("grp_per_capita", "Объём валового внутреннего (регионального) продукта на душу населения",
          660, "national-accounts", "ежегодный", "в текущих ценах, тыс. сум", True,
          "Платёжеспособность региона.", ["годовой"]),

    # --- экспозиция по классу 3 / ОСАГО ---
    _siat("cars_per_100_households", "Количество автомобилей на 100 домохозяйств", 579,
          "living-standards", "ежегодный", "штук", False,
          "Косвенная экспозиция по классу 3 и ОСАГО: парка машин по регионам в открытых "
          "данных нет, это единственный открытый измеритель автомобилизации.",
          ["ТОЛЬКО по республике, разреза регионов нет",
           "это обеспеченность домохозяйств, а не число машин на учёте; "
           "подменять ею парк региона нельзя"]),

    # --- классы 8 и 9: чрезвычайные ситуации ---
    _egov("emergencies", "Статистика произошедших чрезвычайных ситуаций (МЧС)",
          "674026018f55e7be2f46f1f0", "квартальная", "случаев", False,
          "Частота чрезвычайных ситуаций — фон для рисков классов 8 и 9.",
          ["в наборе всего два периода: II квартал 2025 и II квартал 2026",
           "нет разреза регионов, нет сумм ущерба и числа пострадавших объектов",
           "для расчёта убыточности классов 8 и 9 этого недостаточно"]),
]}


# --------------------------------------------------------------------------- #
# Периоды
# --------------------------------------------------------------------------- #

_CYR = {"М": "M", "К": "Q", "м": "M", "к": "Q"}


def norm_period(raw: str) -> Optional[str]:
    """
    Столбец источника → период вида '2025', '2025-Q2', '2026-M08'.
    Не период (Code, Klassifikator*) — None.
    """
    s = (raw or "").strip()
    if not s or s.startswith("Klassifikator") or s == "Code":
        return None
    for c, r in _CYR.items():
        s = s.replace(c, r)
    s = s.upper()
    if re.fullmatch(r"\d{4}", s):
        return s
    m = re.fullmatch(r"(\d{4})-?([MQ])(\d{1,2})", s)
    if m:
        return "%s-%s%s" % (m.group(1), m.group(2), m.group(3).zfill(2) if m.group(2) == "M" else m.group(3))
    return None


# --------------------------------------------------------------------------- #
# Разбор ответов
# --------------------------------------------------------------------------- #

def parse_siat(payload: str, ds: dict) -> Tuple[List[dict], dict]:
    """
    Разбирает JSON api.siat.stat.uz. Возвращает (записи, паспорт набора).
    Паспорт — то, что источник сам про себя написал: имя, периодичность,
    единица, дата последнего изменения. Единицу берём у источника, а не из реестра.
    """
    doc = json.loads(payload)
    if isinstance(doc, list):
        doc = doc[0] if doc else {}
    meta_rows = doc.get("metadata") or []
    meta = {m.get("name_ru", ""): m.get("value_ru") for m in meta_rows}
    passport = {
        "name": meta.get("Имя индикатора") or ds["name"],
        "period": meta.get("Периодичность") or ds["period"],
        "unit": meta.get("Единица измерения") or ds["unit"],
        "updated": meta.get("Дата последнего изменения"),
        "code": meta.get("Идентификационный номер индикатора (код)"),
    }
    unit = passport["unit"]
    out: List[dict] = []
    for row in doc.get("data") or []:
        code = str(row.get("Code", "")).strip()
        reg = region_key(code) if ds["regions"] else ""
        if ds["regions"] and not reg:
            continue                       # районы и города — в разрез не берём
        for col, val in row.items():
            per = norm_period(col)
            if per is None:
                continue
            try:
                v = None if val in (None, "", "-") else float(val)
            except (TypeError, ValueError):
                v = None
            out.append({
                "source": ds["source"], "dataset_id": ds["id"], "key": code or ds["id"],
                "region": reg, "period": per, "value": v, "unit": unit, "url": ds["page"],
            })
    return out, passport


def parse_egov(payload: str, ds: dict) -> Tuple[List[dict], dict]:
    """
    Разбирает JSON data.egov.uz: список плоских записей, где имена полей —
    это и есть периоды (например «2026yilIIchorak»). Периоды достаём из имён полей.
    """
    rows = json.loads(payload)
    if isinstance(rows, dict):
        rows = rows.get("result") or rows.get("data") or []
    out: List[dict] = []
    for row in rows or []:
        key = ""
        for f, v in row.items():
            if f.lower() == "id":
                continue
            if _egov_period(f) is None and not key:
                key = str(v)
        for f, v in row.items():
            per = _egov_period(f)
            if per is None:
                continue
            try:
                val = None if v in (None, "", "-") else float(str(v).replace(" ", "").replace(",", "."))
            except ValueError:
                val = None
            out.append({
                "source": ds["source"], "dataset_id": ds["id"], "key": key or f,
                "region": "", "period": per, "value": val, "unit": ds["unit"], "url": ds["page"],
            })
    return out, {"name": ds["name"], "period": ds["period"], "unit": ds["unit"],
                 "updated": None, "code": ds["src_id"]}


_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}


def _egov_period(field: str) -> Optional[str]:
    """«2026yilIIchorak» → '2026-Q2'; «2025» → '2025'; иначе None."""
    m = re.fullmatch(r"(\d{4})yil(I{1,3}|IV)chorak", field or "", re.I)
    if m:
        return "%s-Q%d" % (m.group(1), _ROMAN[m.group(2).upper()])
    return norm_period(field)


# --------------------------------------------------------------------------- #
# Загрузка
# --------------------------------------------------------------------------- #

def fetch(dataset_id: str) -> Tuple[List[dict], dict]:
    """
    Загружает набор по ключу реестра. Возвращает (записи, статус источника).

    Статус — всегда словарь: status, reason, url, fetched_at, rows, passport.
    Источник не отдал данные — это НЕ исключение, а статус «источник недоступен»
    с причиной и пустым списком записей.
    """
    ds = DATASETS.get(dataset_id)
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    if ds is None:
        return [], {"status": STATUS_DOWN, "reason": "набор «%s» не зарегистрирован" % dataset_id,
                    "url": "", "fetched_at": now, "rows": 0, "passport": {}}
    st = {"status": STATUS_DOWN, "reason": "", "dataset_id": dataset_id, "name": ds["name"],
          "url": ds["page"], "data_url": ds["data_url"], "fetched_at": now,
          "rows": 0, "passport": {}}

    allowed, why = robots_check(ds["data_url"])
    if not allowed:
        st["status"], st["reason"] = STATUS_ROBOTS, why
        return [], st
    try:
        payload = _http_get(ds["data_url"], timeout=60)
    except Exception as e:                                   # сеть, HTTP, TLS, таймаут
        st["reason"] = "%s: %s" % (type(e).__name__, e)
        return [], st

    try:
        if ds["source"] == SOURCE_STAT:
            recs, passport = parse_siat(payload, ds)
        else:
            recs, passport = parse_egov(payload, ds)
    except Exception as e:
        st["reason"] = "ответ не разобрался (%s: %s)" % (type(e).__name__, e)
        return [], st

    for r in recs:
        r["fetched_at"] = now
    st["passport"] = passport
    st["rows"] = len(recs)
    st["status"] = STATUS_OK if recs else STATUS_EMPTY
    if not recs:
        st["reason"] = "источник ответил, но строк в наборе нет"
    return recs, st


# --------------------------------------------------------------------------- #
# Сохранение
# --------------------------------------------------------------------------- #

def save(records: List[dict], db_path: Path = None) -> dict:
    """
    Пишет записи в stat_series. История не перезаписывается:
      * новый период — вставляется;
      * тот же период с тем же значением — ничего не делаем (дублей не будет);
      * тот же период с ДРУГИМ значением — прежнее значение уходит в
        stat_series_revisions, действующим становится новое.
    Почему так: орган статистики регулярно пересчитывает ряды задним числом
    (уточнение населения после переписи, пересчёт ВРП). Если хранить оба значения
    в одном ряду, расчёт удвоит показатель; если затирать молча — старый расчёт
    перестанет воспроизводиться. Журнал пересчётов решает и то, и другое.

    Возвращает: {'вставлено', 'изменено', 'без изменений', 'всего'}.
    """
    res = {"вставлено": 0, "изменено": 0, "без изменений": 0, "всего": len(records)}
    if not records:
        return res
    # те же настройки соединения, что у сервера: WAL и ожидание блокировки вместо мгновенной ошибки
    con = db.connect_path(db_path or DB or db.DB_PATH, row_factory=False)
    try:
        cur = con.cursor()
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        for r in records:
            pk = (r["source"], r["dataset_id"], r["key"], r.get("region", ""), r["period"])
            old = cur.execute(
                "SELECT value, fetched_at FROM stat_series "
                "WHERE source=? AND dataset_id=? AND key=? AND region=? AND period=?", pk
            ).fetchone()
            if old is None:
                cur.execute(
                    "INSERT INTO stat_series "
                    "(source, dataset_id, key, region, period, value, unit, fetched_at, url) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    pk + (r.get("value"), r.get("unit"), r.get("fetched_at", now), r["url"]))
                res["вставлено"] += 1
            elif _same(old[0], r.get("value")):
                res["без изменений"] += 1
            else:
                cur.execute(
                    "INSERT INTO stat_series_revisions "
                    "(source, dataset_id, key, region, period, old_value, new_value, "
                    " old_fetched_at, replaced_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    pk + (old[0], r.get("value"), old[1], now))
                cur.execute(
                    "UPDATE stat_series SET value=?, unit=?, fetched_at=?, url=? "
                    "WHERE source=? AND dataset_id=? AND key=? AND region=? AND period=?",
                    (r.get("value"), r.get("unit"), r.get("fetched_at", now), r["url"]) + pk)
                res["изменено"] += 1
        con.commit()
    finally:
        con.close()
    return res


def _same(a, b) -> bool:
    """Значения считаются теми же, если оба пусты или совпадают до 1e-9."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) < 1e-9


def fetch_and_save(dataset_id: str, db_path: Path = None) -> dict:
    """Загрузить набор и сохранить. Статус источника возвращается всегда."""
    recs, st = fetch(dataset_id)
    st["saved"] = save(recs, db_path) if recs else {"вставлено": 0, "изменено": 0,
                                                    "без изменений": 0, "всего": 0}
    return st


def catalog() -> List[dict]:
    """Реестр наборов для показа заказчику (без обращения к сети)."""
    return [{
        "id": d["id"], "Набор": d["name"], "Источник": d["source"],
        "Страница набора": d["page"], "Адрес данных": d["data_url"],
        "Периодичность": d["period"], "Единица": d["unit"],
        "Регионы": "да" if d["regions"] else "нет",
        "Зачем": d["purpose"], "Ограничения": d["limits"],
    } for d in DATASETS.values()]
