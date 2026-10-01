"""
Показатели риска по классу страхования и региону из открытых данных органа статистики
(stat.uz / api.siat.stat.uz) и портала data.egov.uz. Задача заказчика от 22.09.2026:
статистика агентства по рискам всех классов должна учитываться в анализе риска.

Что здесь:
    INDICATORS                              — какие показатели считаются и для каких классов;
    NOT_FOUND                               — чего по классу в открытых данных нет (честно);
    risk_indicators(con, class_code, region) -> список показателей
        {id, name, measures, value, unit, period, trend_pct, trend_basis, per_1000, per_1000_unit,
         level_vs_country, scope, used_in_score, src, notes};
    external_block(con, classes, region, th) -> блок для app/risk_analytics.py: показатели всех
        классов договора + балл составляющей «Внешняя статистика региона» (0–100) или причина,
        почему составляющая не учитывается.

Ряды берутся только из таблицы stat_series (её наполняет app/stat_sources.py, раз в сутки —
app/statagency.py). Модуль в базу не пишет и в сеть не ходит.

Жёсткие правила:
  * ничего не выдумываем: нет значения — показатель без значения и с причиной;
  * где у набора нет разреза по регионам — показывается республика с пометкой в scope,
    сравнения «регион против республики» нет, в балл такой показатель не идёт;
  * у каждого показателя — источник: набор, id, ссылка на страницу, период, дата загрузки;
  * балл — экспертный (calibrated = 0): шкала сравнения с республикой задаётся порогом
    external_ratio в risk_thresholds, админ может его менять.

Формулы (словами):
  * на 1 000 жителей = число случаев за год / численность населения региона на начало того же года
    (stat.uz, тыс. человек) — население в тысячах, поэтому просто деление;
  * изменение к прошлому периоду = (значение / значение предыдущего периода − 1) × 100;
  * регион против республики = показатель региона / тот же показатель республики за тот же период;
    для счётных показателей сравниваются значения на 1 000 жителей, для коэффициентов — сами значения;
  * изменчивость урожая = стандартное отклонение годовых изменений валового сбора (%) за последние
    N лет (N = external_volatility_years, по умолчанию 10);
  * уязвимый жилой фонд = (глинобитные + сырцовый кирпич, тыс. кв. м × 1 000) / общая площадь
    жилищного фонда (кв. м) × 100;
  * балл показателя = 0 при отношении «регион / республика» не выше нижней границы external_ratio,
    100 — не ниже верхней, между — по прямой; балл составляющей = среднее по показателям.
"""
from __future__ import annotations

import math
import re
from typing import Optional

from . import stat_sources as ss

CALIBRATED = 0

# виды показателей
COUNT_PC = "count_pc"        # число случаев — сравниваем на 1 000 жителей
RATE = "rate"                # коэффициент (промилле, на 10 000) — сравниваем как есть
EXPOSURE = "exposure"        # объём (экспозиция) — доля региона в республике, в балл не идёт
SHARE = "share"              # доля одной величины в другой (уязвимый жилой фонд)
VOLATILITY = "volatility"    # изменчивость ряда (урожай)
REPUBLIC = "republic"        # набор без регионов — только республика

ANNUAL = re.compile(r"^\d{4}$")


def _ind(id, name, classes, datasets, kind, unit, measures, score=(), key=None, note="",
         periods="annual", sum_keys=None, num=None, den=None):
    return {"id": id, "name": name, "classes": set(classes), "datasets": list(datasets), "kind": kind,
            "unit": unit, "measures": measures, "score": set(score), "key": key, "note": note,
            "periods": periods, "sum_keys": sum_keys, "num": num, "den": den}


INDICATORS = [
    # --- наземный транспорт и ответственность владельцев ТС ---
    _ind("road_accidents", "Дорожно-транспортные происшествия", ("3", "10", "7", "1"),
         ["road_accidents", "road_accidents_2024"], COUNT_PC, "ДТП", "частота", score=("3", "10", "7"),
         note="все ДТП по учёту органа статистики, а не страховые случаи"),
    _ind("road_injured", "Пострадавшие в ДТП", ("1", "3", "10"),
         ["road_injured", "road_injured_2024"], COUNT_PC, "человек", "тяжесть", score=("1", "3", "10"),
         note="погибшие и раненые вместе"),
    _ind("road_death_rate", "Смертность в ДТП на 100 000 жителей", ("1", "3", "10"),
         ["road_death_rate"], REPUBLIC, "на 100 000 человек", "тяжесть"),
    _ind("cars_per_100_households", "Автомобилей на 100 домохозяйств", ("3", "10"),
         ["cars_per_100_households"], REPUBLIC, "штук", "экспозиция", key="1700"),
    _ind("passengers_road", "Перевезено пассажиров автотранспортом", ("1", "3", "10"),
         ["passengers_road"], EXPOSURE, "млн человек", "экспозиция"),
    _ind("freight_road", "Перевезено грузов автотранспортом", ("7", "3", "10"),
         ["freight_road"], EXPOSURE, "млн т", "экспозиция"),
    # --- ж/д, авиация, грузы ---
    _ind("freight_rail", "Перевезено грузов железной дорогой", ("4", "7"), ["freight_rail"], REPUBLIC,
         "млн т", "экспозиция", key="1700"),
    _ind("passengers_rail", "Отправлено пассажиров железной дорогой", ("4",), ["passengers_rail"], REPUBLIC,
         "млн человек", "экспозиция", key="1700"),
    _ind("freight_air", "Перевезено грузов воздушным транспортом", ("5", "11", "7"), ["freight_air"], REPUBLIC,
         "тыс. т", "экспозиция", key="1700"),
    _ind("passengers_air", "Отправлено пассажиров воздушным транспортом", ("5", "11"), ["passengers_air"],
         REPUBLIC, "млн человек", "экспозиция", key="1700"),
    _ind("freight_total", "Перевезено грузов всеми видами транспорта", ("7",), ["freight_total"], REPUBLIC,
         "млн т", "экспозиция", key="1700"),
    # --- имущество ---
    _ind("vulnerable_housing", "Доля глинобитного жилья и жилья из сырцового кирпича", ("8",),
         ["housing_walls_adobe", "housing_walls_raw_brick", "housing_fund_area"], SHARE,
         "% жилищного фонда", "уязвимость", score=("8",),
         num=["housing_walls_adobe", "housing_walls_raw_brick"], den="housing_fund_area",
         note="уязвимость застройки региона к землетрясению и воде; это весь жилищный фонд, "
              "а не конкретный объект"),
    _ind("emergencies", "Чрезвычайные ситуации (всего)", ("8", "9"), ["emergencies"], REPUBLIC,
         "случаев", "частота", key="Jami", periods="any",
         note="в наборе МЧС только II квартал 2025 и 2026 годов"),
    _ind("construction_price_index_y", "Индекс цен на строительство (к декабрю прошлого года)", ("8", "9"),
         ["construction_price_index_y"], REPUBLIC, "%", "тяжесть", key="1700",
         note="рост стоимости восстановления — тяжесть убытка при той же страховой сумме"),
    _ind("crimes_total", "Зарегистрированные преступления", ("9",), ["crimes_total"], COUNT_PC,
         "преступлений", "частота", score=("9",)),
    _ind("thefts", "Зарегистрированные кражи", ("9", "3", "7"), ["thefts"], COUNT_PC, "краж", "частота",
         score=("9",), note="все кражи вместе: угоны и кражи грузов отдельно не публикуются; "
                            "с 2024 года ряд вырос втрое — вероятно, изменение учёта"),
    _ind("robberies", "Грабежи и разбои", ("9", "7"), ["robberies"], REPUBLIC, "случаев", "частота", key="1700"),
    # --- личное страхование ---
    _ind("mortality_rate", "Коэффициент смертности", ("1", "2"), ["mortality_rate"], RATE,
         "на 1 000 жителей (промилле)", "частота", score=("1", "2"),
         note="общий коэффициент без поправки на возраст населения"),
    _ind("respiratory_morbidity", "Заболеваемость острыми инфекциями дыхательных путей", ("2",),
         ["respiratory_morbidity"], COUNT_PC, "случаев", "частота",
         note="одна группа болезней — общей заболеваемости по регионам по годам нет; в балл не входит: "
              "город Ташкент даёт около 73% всех случаев республики (2024), разброс регионов отражает "
              "учёт обращений, а не частоту болезни"),
    _ind("doctors_per_10k", "Врачей на 10 000 жителей", ("2",), ["doctors_per_10k"], RATE,
         "на 10 000 жителей", "условия лечения", note="не риск, а доступность медицины — в балл не входит"),
    _ind("hospital_beds_per_10k", "Больничных коек на 10 000 жителей", ("2",), ["hospital_beds_per_10k"], RATE,
         "на 10 000 жителей", "условия лечения", note="в балл не входит"),
    _ind("disability_primary", "Впервые признаны лицами с инвалидностью (всего)", ("1", "2"),
         ["disability_primary"], REPUBLIC, "человек", "тяжесть", key="1700"),
    _ind("disability_work_injury", "Инвалидность из-за трудового увечья (мужчины + женщины)", ("1", "13"),
         ["disability_work_injury_m", "disability_work_injury_f"], REPUBLIC, "человек", "тяжесть",
         key="1700", sum_keys=True),
    # --- общая ответственность ---
    _ind("work_injury_fatal_rate", "Смертельные травмы на производстве на 100 000 работников", ("13", "1"),
         ["work_injury_fatal_rate"], REPUBLIC, "на 100 000 работников", "частота", key="Jami",
         note="2020 год; разрез — виды деятельности (в by_activity)"),
    _ind("work_injury_nonfatal_rate", "Несмертельные травмы на производстве на 100 000 работников",
         ("13", "1"), ["work_injury_nonfatal_rate"], REPUBLIC, "на 100 000 работников", "частота", key="Jami",
         note="2020 год; разрез — виды деятельности (в by_activity)"),
    _ind("work_accidents", "Несчастные случаи на производстве", ("13",), ["work_accidents"], REPUBLIC,
         "случаев", "частота", key="Jami", note="2020 год"),
    # --- урожай ---
    _ind("grain_volatility", "Изменчивость сбора зерна (год к году)", ("16у",), ["grain_production"],
         VOLATILITY, "п. п. (стандартное отклонение годового изменения, %)", "изменчивость", score=("16у",),
         note="валовой сбор, а не урожайность: колебания могут быть и от изменения посевов"),
    _ind("wheat_volatility", "Изменчивость сбора пшеницы (год к году)", ("16у",), ["wheat_production"],
         VOLATILITY, "п. п. (стандартное отклонение годового изменения, %)", "изменчивость",
         note="показывается рядом с зерном, в балл не входит (ряды почти совпадают)"),
    _ind("grain_production", "Валовой сбор зерновых", ("16у",), ["grain_production"], EXPOSURE,
         "тыс. т", "экспозиция"),
    _ind("crop_output", "Продукция растениеводства", ("16у",), ["crop_output"], EXPOSURE,
         "млрд сум (текущие цены)", "экспозиция", note="в текущих ценах — рост включает инфляцию"),
    # --- финансовые риски ---
    _ind("enterprises_liquidated", "Ликвидированные субъекты предпринимательства", ("13з", "14", "15", "16"),
         ["enterprises_liquidated"], COUNT_PC, "субъектов", "частота", score=("13з", "14", "15", "16"),
         note="ликвидации по любой причине, не только банкротства; один полный год — 2024"),
    _ind("frauds", "Зарегистрированные мошенничества", ("13з", "14", "15", "16"), ["frauds"], REPUBLIC,
         "случаев", "частота", key="1700"),
    _ind("bank_loans_total", "Остаток кредитов банков (всего)", ("13з", "14", "15"), ["bank_loans_by_type"],
         REPUBLIC, "млрд сум", "экспозиция", key="Jami kredit qoldig`i", periods="any"),
    _ind("financial_services", "Объём финансовых услуг", ("14", "15", "16"), ["financial_services"], EXPOSURE,
         "млрд сум", "экспозиция"),
    _ind("cpi_annual", "Инфляция: индекс потребительских цен (к декабрю прошлого года)", ("16",),
         ["cpi_annual"], REPUBLIC, "%", "финансовый фон", key="1700"),
    _ind("fx_usd", "Курс доллара США (ЦБ)", ("16",), ["fx_rates"], REPUBLIC, "сум за 1 доллар",
         "финансовый фон", key="USD", periods="any",
         note="источник отдаёт курс на один день — история копится с каждым обновлением"),
]
BY_ID = {i["id"]: i for i in INDICATORS}

# Показатели из отчётов НАПП (01.10.2026): в балл риска и в список показателей класса (risk_indicators) не входят —
# только в поправку региона вилки ставки (act_engine.fork_region, настройка rate_fork.region.indicators) и в
# раздел 4 акта отдельной строкой. Читаются из napp_claims (tools/market_stats.py), а не из stat_series.
NAPP_CLAIMS = "napp_claims"
NAPP_INDICATORS = [
    _ind("claims_freq", "Частота страховых претензий (НАПП)", ("3", "4", "5", "6", "7", "8", "9"), [], NAPP_CLAIMS,
         "претензий на 1 000 договоров", "частота",
         note="все претензии по общему страхованию региона (листы 3.5 и 3.4 отчёта НАПП), без разреза по классам"),
]
BY_ID.update({i["id"]: i for i in NAPP_INDICATORS})

# Чего по классу в открытых данных нет (проверено по каталогу stat.uz и поиску data.egov.uz 22.09.2026)
NOT_FOUND = {
    "1": "бытового травматизма населения по регионам нет; производственный травматизм — только 2020 год "
         "и только по республике",
    "2": "общей заболеваемости по регионам по годам нет (набор Минздрава на data.egov.uz без указания года); "
         "есть одна группа болезней (острые инфекции дыхательных путей) и смертность",
    "3": "парка транспортных средств по регионам и угонов отдельно от прочих краж нет",
    "4": "аварий и происшествий на железной дороге нет — только объём перевозок по республике",
    "5": "авиационных происшествий нет — только объём перевозок по республике",
    "6": "морского и речного транспорта в статистике нет (ни перевозок, ни аварий)",
    "7": "краж грузов и аварий с грузами отдельно нет — только объём перевозок (автотранспорт по регионам)",
    "8": "пожаров и ущерба от пожаров по регионам нет (единственный набор про пожары пуст); ЧС МЧС — два "
         "квартала без регионов и без сумм ущерба; перечня землетрясений нет",
    "9": "краж из жилищ и предприятий отдельно нет — только все кражи вместе",
    "10": "парка транспортных средств по регионам нет; числа договоров ОСАГО нет в отчётах НАПП",
    "11": "авиационных происшествий нет — только объём перевозок по республике",
    "12": "морского и речного транспорта в статистике нет",
    "13": "производственный травматизм — только 2020 год и только по республике (по отраслям); "
          "числа исков о возмещении вреда нет",
    "13з": "просроченной и проблемной задолженности банков нет (набор ЦБ «проблемные кредиты» пуст); "
           "банкротств отдельно от ликвидаций нет",
    "14": "просроченной задолженности по кредитам нет (набор ЦБ пуст); просроченная дебиторская и "
          "кредиторская задолженность предприятий по регионам — наборы на портале пустые или без периода",
    "15": "просроченной задолженности и банкротств отдельно нет; есть ликвидации предприятий (2024)",
    "16": "банкротств отдельно нет; курс валют — только на текущий день (история копится)",
    "16у": "урожайности по годам и посевных площадей нет (наборы урожайности без года, посевные площади "
           "хлопчатника пусты); погодных потерь и гибели посевов нет",
    "17": "числа судебных дел нет (набор Верховного суда «Данные о судебных решениях» на портале пуст)",
}


# --------------------------------------------------------------------------- #
# Чтение рядов
# --------------------------------------------------------------------------- #

def _rows(con, sql, *args) -> list:
    cur = con.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, tuple(r))) for r in cur.fetchall()]


def _ds_region(ds_id: str, region: str) -> str:
    """Регион в stat_series: у наборов без разреза — ''."""
    d = ss.DATASETS.get(ds_id) or {}
    return region if d.get("regions") else ""


def _series(con, ds_ids: list, region: str, key: Optional[str] = None, periods: str = "annual") -> dict:
    """{период: {value, url, fetched_at, dataset_id}} — наборы сливаются по периоду, более поздний
    в списке перекрывает более ранний (продолжение ряда: road_accidents → road_accidents_2024)."""
    out = {}
    for ds_id in ds_ids:
        sql = ("SELECT period, value, url, fetched_at FROM stat_series WHERE dataset_id=? AND region=? "
               "AND value IS NOT NULL")
        args = [ds_id, _ds_region(ds_id, region)]
        if key is not None:
            sql += " AND key=?"
            args.append(key)
        for r in _rows(con, sql, *args):
            if periods == "annual" and not ANNUAL.match(r["period"]):
                continue
            out[r["period"]] = {"value": r["value"], "url": r["url"], "fetched_at": r["fetched_at"],
                                "dataset_id": ds_id}
    return dict(sorted(out.items()))


def _population(con, region: str, year: str):
    """Население на начало года (тыс. человек). Нет года — последний более ранний (с пометкой)."""
    rows = _rows(con, "SELECT period, value, url FROM stat_series WHERE dataset_id='population' AND region=? "
                      "AND value IS NOT NULL AND period<=? ORDER BY period DESC LIMIT 1", region, year)
    return rows[0] if rows else None


def _src(ds_id: str, rec: Optional[dict], period: Optional[str]) -> dict:
    d = ss.DATASETS.get(ds_id) or {}
    return {"name": d.get("name", ds_id), "id": ds_id, "source": d.get("source"),
            "source_id": d.get("src_id"), "url": (rec or {}).get("url") or d.get("page"),
            "data_url": d.get("data_url"), "period": period, "fetched_at": (rec or {}).get("fetched_at"),
            "regions": bool(d.get("regions")), "limits": list(d.get("limits") or [])}


def _trend(ser: dict, per: str):
    keys = list(ser)
    i = keys.index(per)
    if i == 0:
        return None, None
    prev = keys[i - 1]
    a, b = ser[per]["value"], ser[prev]["value"]
    if not b:
        return None, None
    return round((a / b - 1) * 100, 1), "%s к %s" % (per, prev)


def _cmp(reg_v, cty_v) -> Optional[dict]:
    if reg_v is None or not cty_v:
        return None
    r = reg_v / cty_v
    d = (r - 1) * 100
    word = "на уровне республики" if abs(d) < 1 else ("выше республики" if d > 0 else "ниже республики")
    return {"region": round(reg_v, 4), "country": round(cty_v, 4), "ratio": round(r, 3),
            "diff_pct": round(d, 1),
            "text": word if abs(d) < 1 else "%s на %s%%" % (word, _fmt(abs(d), 1))}


def _fmt(x, n=1) -> str:
    return format(float(x), ",.%df" % n).replace(",", " ").replace(".", ",")


def _volatility(ser: dict, years: int):
    """Стандартное отклонение годовых изменений (%) за последние years лет ряда."""
    keys = list(ser)[-(years + 1):]
    ch = []
    for a, b in zip(keys, keys[1:]):
        va, vb = ser[a]["value"], ser[b]["value"]
        if va and int(b) == int(a) + 1:
            ch.append((vb / va - 1) * 100)
    if len(ch) < 3:
        return None, keys
    m = sum(ch) / len(ch)
    return math.sqrt(sum((c - m) ** 2 for c in ch) / (len(ch) - 1)), keys


# --------------------------------------------------------------------------- #
# Один показатель
# --------------------------------------------------------------------------- #

def _napp_claims_one(con, ind: dict, cls: str, region: Optional[str]) -> dict:
    """Частота претензий региона к республике (market_picture.region_claims) в форме показателя _one."""
    from . import market_picture as mp
    out = {"id": ind["id"], "name": ind["name"], "class_code": cls, "measures": ind["measures"], "kind": ind["kind"],
           "value": None, "unit": ind["unit"], "period": None, "trend_pct": None, "trend_basis": None,
           "per_1000": None, "per_1000_unit": None, "level_vs_country": None, "used_in_score": False,
           "points": None, "src": [], "notes": [ind["note"]], "calibrated": CALIBRATED}
    rc = mp.region_claims(con, region if region and region != "total" else None)
    out["scope"] = "регион" if rc.get("region") else "республика"
    out["region"] = rc.get("region_name") or "Республика Узбекистан"
    if rc.get("note"):
        out["notes"].append(rc["note"])
    out["notes"] += rc.get("caveats") or []
    s = rc.get("source")
    if s:
        out["src"] = [{"name": "Страховой отчёт, листы %s" % s["sheets"], "id": "napp_claims", "source": "НАПП",
                       "source_id": None, "url": s["url"], "data_url": None, "period": rc.get("date"),
                       "fetched_at": (rc.get("republic") or {}).get("loaded_at"), "regions": True, "limits": []}]
    cur = rc.get("region") or rc.get("republic")
    if not cur or cur.get("per_1000") is None:
        out["status"] = "нет данных"
        out["reason"] = rc.get("note") or "нет данных НАПП о претензиях"
        return out
    d = rc.get("date") or ""
    out.update({"value": cur["per_1000"], "period": "%s.%s.%s" % (d[8:10], d[5:7], d[:4]) if d else None,
                "status": "ок", "per_1000": cur["per_1000"],
                "per_1000_unit": ind["unit"], "date": rc.get("date"),
                "claims": {"region": rc.get("region"), "republic": rc.get("republic")}})
    if rc.get("available"):
        out["level_vs_country"] = _cmp(rc["region"]["per_1000"], rc["republic"]["per_1000"])
    return out


def _one(con, ind: dict, cls: str, region: Optional[str], th: dict) -> dict:
    kind = ind["kind"]
    if kind == NAPP_CLAIMS:
        return _napp_claims_one(con, ind, cls, region)
    ds0 = ind["datasets"][0]
    regional = kind != REPUBLIC and bool((ss.DATASETS.get(ds0) or {}).get("regions"))
    want = region if (region and region != "total" and regional) else "total"
    out = {"id": ind["id"], "name": ind["name"], "class_code": cls, "measures": ind["measures"],
           "kind": kind, "value": None, "unit": ind["unit"], "period": None, "trend_pct": None,
           "trend_basis": None, "per_1000": None, "per_1000_unit": None, "level_vs_country": None,
           "used_in_score": False, "points": None, "src": [], "notes": [], "calibrated": CALIBRATED}
    if ind["note"]:
        out["notes"].append(ind["note"])
    if not regional:
        out["scope"] = "республика — у набора нет разреза по регионам"
        want = ""
    elif want == "total":
        out["scope"] = "республика — регион не задан" if not region else "республика"
    else:
        out["scope"] = "регион"
    out["region"] = ss.REGION_NAMES_RU.get(want, "Республика Узбекистан") if want else "Республика Узбекистан"

    # --- набор без регионов (в т. ч. сумма двух наборов) ---
    if kind == REPUBLIC:
        if ind["sum_keys"]:
            parts = [_series(con, [d], "", ind["key"], ind["periods"]) for d in ind["datasets"]]
            common = sorted(set.intersection(*[set(p) for p in parts])) if parts else []
            ser = {p: {"value": sum(x[p]["value"] for x in parts), "url": parts[0][p]["url"],
                       "fetched_at": parts[0][p]["fetched_at"], "dataset_id": ind["datasets"][0]}
                   for p in common}
        else:
            ser = _series(con, ind["datasets"], "", ind["key"], ind["periods"])
        if not ser:
            out["status"] = "нет данных"
            out["reason"] = "набор ещё не загружен или у источника нет значений"
            out["src"] = [_src(d, None, None) for d in ind["datasets"]]
            return out
        per = list(ser)[-1]
        out.update({"value": round(ser[per]["value"], 4), "period": per, "status": "ок"})
        out["trend_pct"], out["trend_basis"] = _trend(ser, per)
        out["src"] = [_src(d, ser[per], per) for d in ind["datasets"]]
        if ind["id"].startswith("work_injury") or ind["id"] == "work_accidents":
            out["by_activity"] = {r["key"]: r["value"] for r in _rows(
                con, "SELECT key, value FROM stat_series WHERE dataset_id=? AND period=? AND value IS NOT NULL "
                     "ORDER BY value DESC", ds0, per)}
        return out

    # --- доля: уязвимый жилой фонд ---
    if kind == SHARE:
        def share(reg):
            nums = [_series(con, [d], reg) for d in ind["num"]]
            den = _series(con, [ind["den"]], reg)
            common = sorted(set(den).intersection(*[set(n) for n in nums]))
            if not common:
                return None, None, nums, den
            p = common[-1]
            v = sum(n[p]["value"] for n in nums) * 1000 / den[p]["value"] * 100 if den[p]["value"] else None
            return v, p, nums, den
        v, per, nums, den = share(want)
        if v is None:
            out["status"] = "нет данных"
            out["reason"] = "нет одного из трёх рядов (глинобитные, сырцовый кирпич, общая площадь) за общий год"
            out["src"] = [_src(d, None, None) for d in ind["datasets"]]
            return out
        out.update({"value": round(v, 2), "period": per, "status": "ок"})
        prev_years = sorted(set(den).intersection(*[set(n) for n in nums]))
        if len(prev_years) > 1:
            p0 = prev_years[-2]
            v0 = sum(n[p0]["value"] for n in nums) * 1000 / den[p0]["value"] * 100
            out["trend_pct"] = round((v / v0 - 1) * 100, 1) if v0 else None
            out["trend_basis"] = "%s к %s" % (per, p0)
        out["src"] = [_src(d, s[per], per) for d, s in zip(ind["num"] + [ind["den"]], nums + [den])]
        out["formula"] = "(глинобитные + сырцовый кирпич, тыс. кв. м × 1 000) / общая площадь фонда, кв. м × 100"
        if want not in ("", "total"):
            cv, cper, _, _ = share("total")
            if cv is not None and cper == per:
                out["level_vs_country"] = _cmp(v, cv)
        return _score(out, ind, cls, th)

    # --- изменчивость урожая ---
    if kind == VOLATILITY:
        years = int(th.get("external_volatility_years", 10))
        ser = _series(con, ind["datasets"], want)
        vol, used = _volatility(ser, years)
        if vol is None:
            out["status"] = "нет данных"
            out["reason"] = "меньше трёх смежных лет ряда — изменчивость не считается"
            out["src"] = [_src(ds0, None, None)]
            return out
        out.update({"value": round(vol, 2), "period": "%s–%s" % (used[0], used[-1]), "status": "ок"})
        out["formula"] = ("стандартное отклонение годовых изменений валового сбора, %% за %s–%s"
                          % (used[0], used[-1]))
        out["src"] = [_src(ds0, ser[used[-1]], out["period"])]
        if want not in ("", "total"):
            cser = _series(con, ind["datasets"], "total")
            cvol, cused = _volatility(cser, years)
            if cvol and cused == used:
                out["level_vs_country"] = _cmp(vol, cvol)
        return _score(out, ind, cls, th)

    # --- число случаев, коэффициент, экспозиция ---
    ser = _series(con, ind["datasets"], want, None, ind["periods"])
    if not ser:
        out["status"] = "нет данных"
        out["reason"] = "набор ещё не загружен или у источника нет значений по региону"
        out["src"] = [_src(d, None, None) for d in ind["datasets"]]
        return out
    per = list(ser)[-1]
    rec = ser[per]
    out.update({"value": round(rec["value"], 4), "period": per, "status": "ок"})
    out["trend_pct"], out["trend_basis"] = _trend(ser, per)
    out["src"] = [_src(rec["dataset_id"], rec, per)]
    cser = _series(con, ind["datasets"], "total", None, ind["periods"]) if want != "total" else ser
    crec = cser.get(per)

    if kind == COUNT_PC:
        pop = _population(con, want, per[:4])
        if pop and pop["value"]:
            out["per_1000"] = round(rec["value"] / pop["value"], 4)
            out["per_1000_unit"] = "%s на 1 000 жителей" % ind["unit"]
            out["src"].append(_src("population", pop, pop["period"]))
            if pop["period"] != per[:4]:
                out["notes"].append("население взято на начало %s года — за %s у источника ещё нет"
                                    % (pop["period"], per[:4]))
            if want != "total" and crec:
                cpop = _population(con, "total", per[:4])
                if cpop and cpop["value"]:
                    out["level_vs_country"] = _cmp(out["per_1000"], crec["value"] / cpop["value"])
        else:
            out["notes"].append("нет численности населения — на 1 000 жителей не пересчитано")
    elif kind == RATE:
        if want != "total" and crec:
            out["level_vs_country"] = _cmp(rec["value"], crec["value"])
    elif kind == EXPOSURE:
        if want != "total" and crec and crec["value"]:
            out["share_of_country_pct"] = round(rec["value"] / crec["value"] * 100, 2)
    return _score(out, ind, cls, th)


def _score(out: dict, ind: dict, cls: str, th: dict) -> dict:
    """Балл показателя 0–100 по отношению «регион / республика» (только для показателей из ind['score'])."""
    if cls not in ind["score"]:
        return out
    lv = out.get("level_vs_country")
    if not lv:
        out["notes"].append("в балл не вошёл: нет сравнения региона с республикой за один период")
        return out
    lo, hi = th.get("external_ratio", [0.5, 1.5])
    r = lv["ratio"]
    pts = 0.0 if r <= lo else (100.0 if r >= hi else (r - lo) / (hi - lo) * 100)
    out["used_in_score"] = True
    out["points"] = round(pts, 1)
    return out


# --------------------------------------------------------------------------- #
# Публичные функции
# --------------------------------------------------------------------------- #

def _region_key(region) -> Optional[str]:
    if not region:
        return None
    from . import market_picture as mp          # справочник регионов проекта (русские названия, псевдонимы)
    key, _ = mp.resolve_region(str(region))
    return key


def risk_indicators(con, class_code: str, region: Optional[str] = None, th: Optional[dict] = None) -> list:
    """
    Показатели риска класса по региону. Регион — название, псевдоним, ключ region:* или СОАТО.
    Не опознан или не задан — республика (scope говорит об этом). Возвращает список словарей.
    """
    cls = str(class_code or "").strip()
    th = th or {}
    key = _region_key(region)
    res = []
    for ind in INDICATORS:
        if cls in ind["classes"]:
            res.append(_one(con, ind, cls, key, th))
    return res


def external_block(con, classes: list, region: Optional[str], th: dict) -> dict:
    """
    Блок external_stats для analyze и данные составляющей «Внешняя статистика региона».
    Показатели считаются по каждому классу договора; один показатель, нужный двум классам,
    показывается один раз. Балл = среднее баллов показателей, вошедших в балл.
    """
    key = _region_key(region)
    inds, seen = [], set()
    for cls in classes:
        for i in risk_indicators(con, cls, region, th):
            if i["id"] in seen:
                # тот же показатель у второго класса: если там он в балле — отмечаем
                prev = next(x for x in inds if x["id"] == i["id"])
                if i["used_in_score"] and not prev["used_in_score"]:
                    inds[inds.index(prev)] = i
                continue
            seen.add(i["id"])
            inds.append(i)
    scored = [i for i in inds if i["used_in_score"]]
    lo, hi = th.get("external_ratio", [0.5, 1.5])
    block = {"region": region, "region_key": key,
             "region_name": ss.REGION_NAMES_RU.get(key) if key else None,
             "classes": list(classes), "indicators": inds,
             "not_found": {c: NOT_FOUND[c] for c in classes if c in NOT_FOUND},
             "scale": ("отношение «регион / республика» %s и ниже → 0 баллов, %s и выше → 100, между — по прямой; "
                       "балл составляющей — среднее по показателям" % (_fmt(lo, 2), _fmt(hi, 2))),
             "calibrated": CALIBRATED,
             "source_note": "stat.uz (api.siat.stat.uz) и data.egov.uz — ссылка на набор у каждого показателя"}
    if not inds:
        block.update({"applicable": False, "points": None,
                      "why": "для класса нет показателей в открытых данных: " +
                             "; ".join(NOT_FOUND.get(c, "") for c in classes if NOT_FOUND.get(c))})
        return block
    if not key or key == "total":
        block.update({"applicable": False, "points": None,
                      "why": ("регион не опознан" if region and not key else "регион не задан или указана "
                              "вся республика") + " — сравнивать регион с республикой не с чем, "
                              "показатели даны по республике"})
        return block
    if not scored:
        block.update({"applicable": False, "points": None,
                      "why": "по классу нет показателей с разрезом по регионам (или нет данных за общий "
                             "период) — показатели даны по республике для справки, в уровень риска не входят"})
        return block
    pts = sum(i["points"] for i in scored) / len(scored)
    avg_ratio = sum(i["level_vs_country"]["ratio"] for i in scored) / len(scored)
    block.update({
        "applicable": True, "points": round(pts, 1), "avg_ratio": round(avg_ratio, 3),
        "why": "; ".join("%s (%s, %s): %s → %s баллов" % (
            i["name"], i["region"], i["period"], i["level_vs_country"]["text"], _fmt(i["points"], 0))
            for i in scored) + "; среднее %s баллов" % _fmt(pts, 1)})
    return block
