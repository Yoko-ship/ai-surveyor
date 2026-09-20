"""
Агентство статистики: обновление, выдача и ОБРАБОТКА открытых данных.

Слой загрузки (реестр наборов, сеть, разбор, запись в stat_series) живёт в app/stat_sources.py
и здесь не переписывается — отсюда он только вызывается.

Что делает этот модуль:
  1. точки подключения /stat/refresh, /stat/series, /stat/status — ручное обновление и выдача;
  2. расписание: поток внутри сервера обновляет все наборы раз в сутки; сеть недоступна —
     пишем статус и живём дальше, сервер не падает;
  3. производные показатели: премии на душу населения, проникновение, страхование жилья
     к жилому фонду, ОСАГО к автопарку, индексация страховых стоимостей. У каждого —
     формула словами, период каждого множителя, ссылки на ОБА источника (отчёт НАПП
     в market_stats и набор stat.uz) и ограничения наборов;
  4. источник «Агентство статистики» для оценки недвижимости (см. valuation_source):
     stat.uz даёт не цену квадратного метра, а индекс цен на жильё, поэтому его честная
     роль — приведение ранее известной цены к текущему кварталу, а не замена рынка.

Жёсткое правило: ничего не выдумывать. Нет данных — показатель возвращается со статусом
«нет данных», с причиной и ссылкой на открытый вопрос к заказчику.

Единицы, которые надо помнить при чтении формул:
  * market_stats.premiums_ytd — млн сум, нарастающим итогом с начала года;
    срез на 1 января — это полный предыдущий год (проверено: сумма по регионам = итог);
  * population — тысяч человек, на НАЧАЛО года;
  * grp — млрд сум, за год;
  * housing_fund_area — кв. м, на конец года;
  * индексы (cpi_annual, construction_price_index_y) — проценты к декабрю предыдущего года;
  * house_price_index — процент к ПРЕДЫДУЩЕМУ кварталу.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException

from . import db
from . import stat_sources as ss

router = APIRouter()

MARKET_UNIT = "млн сум"
MARKET_SOURCE = "Отчёт НАПП (таблица market_stats, страница /stats)"
MARKET_URL = "https://napp.uz/uz/pages/statistika"

# Сколько месяцев накоплено в срезе НАПП: ключ — «месяц-день» отчётной даты.
MARKET_MONTHS = {"03-31": 3, "04-01": 3, "07-01": 6, "10-01": 9, "01-01": 12}

QUESTIONS = {
    "opendata": "вопрос 46 в docs/Открытые вопросы.md (условия использования открытых данных)",
    "fleet": "вопросы 47 и 53 в docs/Открытые вопросы.md (парка ТС по регионам в открытых "
             "данных нет, числа договоров ОСАГО нет в отчётах НАПП)",
    "households": "вопрос 49 в docs/Открытые вопросы.md (число домохозяйств — расчётное)",
    "house_price": "вопрос 50 в docs/Открытые вопросы.md (метод приведения цены индексом)",
    "cpi_region": "вопрос 51 в docs/Открытые вопросы.md (ИПЦ по регионам)",
    "region_class": "вопрос 52 в docs/Открытые вопросы.md (разрез «регион × класс» в отчётах НАПП)",
}

STATUS_OK = "ок"
STATUS_NODATA = "нет данных"


# --------------------------------------------------------------------------- #
# Справочные мелочи
# --------------------------------------------------------------------------- #

def region_key(value: Optional[str]) -> str:
    """
    Что угодно от пользователя → ключ region:* как в market_stats.
    Принимаем: сам ключ, код СОАТО, русское название. Неизвестное — пустая строка.
    """
    s = (value or "").strip()
    if not s:
        return ""
    if s in ss.REGION_NAMES_RU:
        return s
    if s.isdigit():
        return ss.region_key(s)
    low = s.lower()
    for key, name in ss.REGION_NAMES_RU.items():
        if name.lower() == low:
            return key
    for key in ss.REGION_NAMES_RU:
        if key.lower() == "region:" + low:
            return key
    return ""


def region_name(key: str) -> str:
    return ss.REGION_NAMES_RU.get(key, key or "разреза нет")


def _dataset_ref(ds_id: str) -> dict:
    """Ссылка на набор данных для показа в объяснении."""
    d = ss.DATASETS.get(ds_id) or {}
    return {"Набор": d.get("name", ds_id), "id": ds_id, "Источник": d.get("source", "stat.uz"),
            "Ссылка": d.get("page", ""), "Ограничения": list(d.get("limits") or [])}


# --------------------------------------------------------------------------- #
# Чтение рядов
# --------------------------------------------------------------------------- #

def series_value(con, dataset_id: str, period: str, region: str = "", key: str = None) -> Optional[dict]:
    """
    Одно значение ряда. Возвращает словарь с значением, единицей и ссылкой либо None.
    Ничего не подставляет вместо отсутствующего значения.
    """
    sql = ("SELECT key, region, period, value, unit, url, fetched_at FROM stat_series "
           "WHERE dataset_id=? AND period=? AND region=?")
    args = [dataset_id, period, region]
    if key is not None:
        sql += " AND key=?"
        args.append(key)
    rows = db.rows(con, sql, *args)
    rows = [r for r in rows if r["value"] is not None]
    return rows[0] if rows else None


def last_period(con, dataset_id: str, region: str = "", upto: str = None) -> Optional[str]:
    """Последний период набора (не позже upto), у которого есть значение."""
    sql = ("SELECT MAX(period) p FROM stat_series WHERE dataset_id=? AND region=? AND value IS NOT NULL")
    args = [dataset_id, region]
    if upto:
        sql += " AND period<=?"
        args.append(upto)
    r = db.rows(con, sql, *args)
    return r[0]["p"] if r and r[0]["p"] else None


def market_snapshot(con, year: int = None) -> Optional[dict]:
    """
    Какой срез НАПП взять. Срез на 1 января года N+1 — это ПОЛНЫЙ год N; остальные — часть года.
    Без указания года берём самый свежий полный год, потому что делить годовой ВРП
    на премии половины года нельзя.
    """
    dates = [r["report_date"] for r in db.rows(
        con, "SELECT DISTINCT report_date FROM market_stats ORDER BY report_date")]
    if not dates:
        return None
    full = [d for d in dates if d[5:] == "01-01"]
    if year is not None:
        want = "%d-01-01" % (year + 1)
        if want in dates:
            chosen, months, y, partial = want, 12, year, False
        else:
            same = [d for d in dates if d[:4] == str(year) and d[5:] != "01-01"]
            if not same:
                return None
            chosen = same[-1]
            months = MARKET_MONTHS.get(chosen[5:], 0)
            y, partial = year, True
    elif full:
        chosen = full[-1]
        months, y, partial = 12, int(chosen[:4]) - 1, False
    else:
        chosen = dates[-1]
        months = MARKET_MONTHS.get(chosen[5:], 0)
        y, partial = int(chosen[:4]), True
    return {"report_date": chosen, "year": y, "months": months, "partial": partial}


def market_value(con, row_key: str, report_date: str, field: str = "premiums_ytd") -> Optional[float]:
    r = db.rows(con, "SELECT %s v FROM market_stats WHERE report_date=? AND row_key=?" % field,
                report_date, row_key)
    return r[0]["v"] if r and r[0]["v"] is not None else None


# --------------------------------------------------------------------------- #
# Производный показатель 1: премии на душу населения по регионам
# --------------------------------------------------------------------------- #

def premiums_per_capita(con, year: int = None, region: str = None) -> dict:
    """
    Премии на душу населения = премии региона (НАПП) / население региона (stat.uz).

    Формула:  премии, млн сум × 1 000 000 / (население, тыс. человек × 1 000)
              = премии × 1 000 / население  → сум на одного жителя.
    """
    snap = market_snapshot(con, year)
    out = {"Показатель": "Премии на душу населения по регионам",
           "Формула": "премии региона за год (отчёт НАПП, млн сум) разделить на численность "
                      "постоянного населения региона на начало того же года (stat.uz, тыс. человек); "
                      "в сумах на человека это премии × 1 000 / население",
           "Единица": "сум на одного жителя",
           "Источники": [{"Название": MARKET_SOURCE, "Ссылка": MARKET_URL},
                         _dataset_ref("population")],
           "Предупреждения": [], "Строки": []}
    if snap is None:
        out["Статус"] = STATUS_NODATA
        out["Причина"] = "в базе нет ни одного среза отчётности НАПП (таблица market_stats пуста)"
        return out
    y = snap["year"]
    out["Год"] = y
    out["Срез НАПП"] = snap["report_date"]
    out["Период премий"] = ("%d год полностью" % y if not snap["partial"]
                            else "%d год, %d мес. нарастающим итогом" % (y, snap["months"]))
    out["Период населения"] = "на начало %d года" % y
    if snap["partial"]:
        out["Предупреждения"].append(
            "премии взяты не за полный год (%d мес.) — показатель неполный, годовым его считать нельзя"
            % snap["months"])
    out["Предупреждения"].append(
        "население даётся на начало года, а премии — за период: сопоставляются премии года N "
        "с населением на начало года N")

    want = region_key(region) if region else None
    if region and not want:
        raise HTTPException(400, "регион «%s» не опознан" % region)
    keys = [want] if want else (["total"] + [k for k in ss.REGION_NAMES_RU if k != "total"])
    for key in keys:
        prem = market_value(con, key, snap["report_date"])
        pop = series_value(con, "population", str(y), key)
        row = {"Регион": region_name(key), "region": key,
               "Премии, млн сум": prem,
               "Население, тыс. человек": pop["value"] if pop else None,
               "Ссылка на набор населения": pop["url"] if pop else _dataset_ref("population")["Ссылка"]}
        if prem is None or not pop or not pop["value"]:
            row["Значение"] = None
            row["Статус"] = STATUS_NODATA
            row["Причина"] = ("нет премий региона в срезе %s" % snap["report_date"] if prem is None
                              else "нет численности населения за %d" % y)
        else:
            row["Значение"] = round(prem * 1000.0 / pop["value"], 2)
            row["Статус"] = STATUS_OK
            row["Расчёт"] = "%.1f млн сум × 1 000 / %.1f тыс. чел = %.2f сум на жителя" % (
                prem, pop["value"], row["Значение"])
        out["Строки"].append(row)
    out["Статус"] = STATUS_OK if any(r["Статус"] == STATUS_OK for r in out["Строки"]) else STATUS_NODATA
    return out


# --------------------------------------------------------------------------- #
# Производный показатель 2: проникновение страхования (премии к ВРП)
# --------------------------------------------------------------------------- #

def penetration(con, year: int = None, region: str = None) -> dict:
    """
    Проникновение = премии региона / валовой региональный продукт.

    Формула:  премии, млн сум / 1 000 = премии, млрд сум;
              премии, млрд сум / ВРП, млрд сум × 100 = проникновение, % ВРП.
    """
    snap = market_snapshot(con, year)
    out = {"Показатель": "Проникновение страхования (премии к ВРП)",
           "Формула": "премии региона за год (отчёт НАПП, млн сум) перевести в млрд сум делением "
                      "на 1 000, разделить на ВРП региона за тот же год (stat.uz, млрд сум) "
                      "и умножить на 100",
           "Единица": "процент ВРП",
           "Источники": [{"Название": MARKET_SOURCE, "Ссылка": MARKET_URL}, _dataset_ref("grp")],
           "Предупреждения": [], "Строки": []}
    if snap is None:
        out["Статус"] = STATUS_NODATA
        out["Причина"] = "в базе нет ни одного среза отчётности НАПП"
        return out
    y = snap["year"]
    out["Год"] = y
    out["Срез НАПП"] = snap["report_date"]
    out["Период премий"] = ("%d год полностью" % y if not snap["partial"]
                            else "%d год, %d мес. нарастающим итогом" % (y, snap["months"]))
    out["Период ВРП"] = "%d год" % y
    if snap["partial"]:
        out["Предупреждения"].append(
            "премии за %d мес., а ВРП годовой — делить их друг на друга нельзя без оговорки" % snap["months"])
    out["Предупреждения"].append("ВРП публикуется с задержкой: за последний год его может ещё не быть")

    want = region_key(region) if region else None
    if region and not want:
        raise HTTPException(400, "регион «%s» не опознан" % region)
    keys = [want] if want else (["total"] + [k for k in ss.REGION_NAMES_RU if k != "total"])
    for key in keys:
        prem = market_value(con, key, snap["report_date"])
        grp = series_value(con, "grp", str(y), key)
        row = {"Регион": region_name(key), "region": key, "Премии, млн сум": prem,
               "ВРП, млрд сум": grp["value"] if grp else None,
               "Ссылка на набор ВРП": grp["url"] if grp else _dataset_ref("grp")["Ссылка"]}
        if prem is None or not grp or not grp["value"]:
            row["Значение"] = None
            row["Статус"] = STATUS_NODATA
            row["Причина"] = ("нет премий региона в срезе %s" % snap["report_date"] if prem is None
                              else "ВРП региона за %d ещё не опубликован" % y)
        else:
            row["Значение"] = round(prem / 1000.0 / grp["value"] * 100, 4)
            row["Статус"] = STATUS_OK
            row["Расчёт"] = "%.1f млн сум / 1 000 / %.1f млрд сум × 100 = %.4f %% ВРП" % (
                prem, grp["value"], row["Значение"])
        out["Строки"].append(row)
    out["Статус"] = STATUS_OK if any(r["Статус"] == STATUS_OK for r in out["Строки"]) else STATUS_NODATA
    return out


# --------------------------------------------------------------------------- #
# Производный показатель 3: страхование жилья к жилому фонду
# --------------------------------------------------------------------------- #

def housing_vs_fund(con, year: int = None) -> dict:
    """
    Премии по имущественным классам 8 и 9 на квадратный метр жилищного фонда.

    Проверено по базе, а не предположено: в market_stats строки — это либо класс (cls8, cls9,
    cls8_9), либо регион (region:*), либо компания. Разреза «регион × класс» в отчётах НАПП нет,
    поэтому показатель считается ТОЛЬКО по республике; по регионам — «нет данных».

    Формула:  премии классов 8 и 9 за год, млн сум × 1 000 000 / площадь жилищного фонда, кв. м
              = сум премии на один квадратный метр жилищного фонда.
    """
    snap = market_snapshot(con, year)
    out = {"Показатель": "Страхование имущества к жилому фонду",
           "Формула": "премии классов 8 и 9 за год (отчёт НАПП, млн сум) умножить на 1 000 000 "
                      "и разделить на общую площадь жилищного фонда (stat.uz, кв. м)",
           "Единица": "сум премии на 1 кв. м жилищного фонда",
           "Источники": [{"Название": MARKET_SOURCE, "Ссылка": MARKET_URL},
                         _dataset_ref("housing_fund_area")],
           "Предупреждения": [
               "классы 8 и 9 — это ВСЁ имущество (предприятия, склады, товары), а не только жильё; "
               "отдельной строки «страхование жилья» в отчётах НАПП нет",
               "площадь жилищного фонда — на конец года, премии — за период",
           ],
           "По регионам": {"Статус": STATUS_NODATA,
                           "Причина": "в отчётах НАПП нет разреза «регион × класс»: строки отчёта — "
                                      "либо класс, либо регион, либо компания. Премии классов 8 и 9 "
                                      "по регионам открыто не публикуются",
                           "Вопрос": QUESTIONS["region_class"]}}
    if snap is None:
        out["Статус"] = STATUS_NODATA
        out["Причина"] = "в базе нет ни одного среза отчётности НАПП"
        return out
    y = snap["year"]
    out["Год"] = y
    out["Срез НАПП"] = snap["report_date"]
    out["Период премий"] = ("%d год полностью" % y if not snap["partial"]
                            else "%d год, %d мес." % (y, snap["months"]))
    fund_period = last_period(con, "housing_fund_area", "total", upto=str(y)) or \
        last_period(con, "housing_fund_area", "total")
    prem = market_value(con, "cls8_9", snap["report_date"])
    if prem is None:
        prem8 = market_value(con, "cls8", snap["report_date"])
        prem9 = market_value(con, "cls9", snap["report_date"])
        prem = None if prem8 is None and prem9 is None else (prem8 or 0) + (prem9 or 0)
        out["Строка отчёта"] = "cls8 + cls9"
    else:
        out["Строка отчёта"] = "cls8_9 (пакет «огонь и стихия + ущерб»)"
    fund = series_value(con, "housing_fund_area", fund_period, "total") if fund_period else None
    out["Период жилого фонда"] = fund_period
    out["Премии классов 8 и 9, млн сум"] = prem
    out["Площадь жилищного фонда, кв. м"] = fund["value"] if fund else None
    if prem is None or not fund or not fund["value"]:
        out["Статус"] = STATUS_NODATA
        out["Значение"] = None
        out["Причина"] = ("нет премий классов 8 и 9 в срезе %s" % snap["report_date"] if prem is None
                          else "нет площади жилищного фонда по республике")
        return out
    if fund_period != str(y):
        out["Предупреждения"].append(
            "жилой фонд взят за %s, а премии за %d: свежее данных у источника нет" % (fund_period, y))
    out["Значение"] = round(prem * 1_000_000 / fund["value"], 2)
    out["Расчёт"] = "%.1f млн сум × 1 000 000 / %.0f кв. м = %.2f сум на кв. м" % (
        prem, fund["value"], out["Значение"])
    out["Статус"] = STATUS_OK
    return out


# --------------------------------------------------------------------------- #
# Производный показатель 4: ОСАГО к автопарку — честное «нет данных»
# --------------------------------------------------------------------------- #

def osago_vs_fleet(con, year: int = None) -> dict:
    """
    Договоры ОСАГО к парку транспортных средств.

    Посчитать НЕЛЬЗЯ: парка ТС по регионам в открытых данных нет (у МВД на data.egov.uz —
    только проверка одной машины по госномеру, у stat.uz — «автомобилей на 100 домохозяйств»
    и только по республике). Числа договоров ОСАГО в отчётах НАПП тоже нет — есть премии
    и обязательства по строке ОСАГО, но не количество полисов.

    Поэтому показатель возвращается как «нет данных» с причиной, а рядом кладётся то, что
    у источника действительно есть: премии ОСАГО по республике и автомобилизация домохозяйств.
    Подменять этим парк региона запрещено.
    """
    snap = market_snapshot(con, year)
    out = {"Показатель": "Договоры ОСАГО к автопарку",
           "Статус": STATUS_NODATA,
           "Причина": "регионального парка транспортных средств в открытых данных нет, а числа "
                      "договоров ОСАГО нет в отчётах НАПП: в них по строке ОСАГО даются премии, "
                      "выплаты и обязательства, но не количество полисов",
           "Вопрос": QUESTIONS["fleet"],
           "Формула": "была бы: число действующих договоров ОСАГО / число транспортных средств "
                      "на учёте в регионе — оба множителя недоступны",
           "Значение": None,
           "Источники": [{"Название": MARKET_SOURCE, "Ссылка": MARKET_URL},
                         _dataset_ref("cars_per_100_households")],
           "Предупреждения": [
               "«Количество автомобилей на 100 домохозяйств» — обеспеченность домохозяйств, "
               "а не число машин на учёте; заменять ею парк региона нельзя",
               "этот набор даётся ТОЛЬКО по республике, разреза регионов нет",
           ],
           "Что есть вместо этого": {}}
    per = last_period(con, "cars_per_100_households", "")
    cars = series_value(con, "cars_per_100_households", per) if per else None
    if cars:
        out["Что есть вместо этого"]["Автомобилей на 100 домохозяйств"] = {
            "Период": per, "Значение": cars["value"], "Единица": cars["unit"], "Ссылка": cars["url"],
            "Разрез": "только республика"}
    if snap is not None:
        prem = market_value(con, "osago", snap["report_date"])
        if prem is not None:
            out["Что есть вместо этого"]["Премии ОСАГО, млн сум"] = {
                "Период": ("%d год полностью" % snap["year"] if not snap["partial"]
                           else "%d год, %d мес." % (snap["year"], snap["months"])),
                "Значение": prem, "Источник": MARKET_SOURCE, "Ссылка": MARKET_URL,
                "Разрез": "только республика (по регионам премии даются суммарно по всем классам)"}
    return out


# --------------------------------------------------------------------------- #
# Производный показатель 5: индексация страховых стоимостей
# --------------------------------------------------------------------------- #

INDEX_SETS = {
    "cpi": "cpi_annual",
    "construction": "construction_price_index_y",
}


def indexation(con, amount: float, year_from: int, year_to: int, index: str = "cpi") -> dict:
    """
    Приведение суммы из года A в год B по годовым индексам «к декабрю предыдущего года».

    Формула:  сумма(B) = сумма(A) × произведение индексов за годы A+1 … B, каждый / 100.
    Обратное направление (B < A) — деление на то же произведение.
    Нет индекса хотя бы за один год цепочки — показатель «нет данных»: ни один год
    не домысливается и не заменяется соседним.
    """
    ds_id = INDEX_SETS.get(index)
    if ds_id is None:
        raise HTTPException(400, "индекс «%s» не поддерживается: допустимы %s"
                            % (index, ", ".join(INDEX_SETS)))
    ref = _dataset_ref(ds_id)
    out = {"Показатель": "Индексация страховой стоимости",
           "Индекс": ref["Набор"],
           "Формула": "сумма года A × произведение годовых индексов за каждый год после A "
                      "и по год B включительно, каждый индекс делится на 100 "
                      "(индексы даны к декабрю предыдущего года, поэтому перемножаются цепочкой)",
           "Сумма на входе": amount, "Год исходный": year_from, "Год целевой": year_to,
           "Источники": [ref], "Множители": [], "Предупреждения": list(ref["Ограничения"])}
    if index == "cpi":
        out["Предупреждения"].append(
            "сводного ИПЦ по регионам орган статистики не публикует — индекс республиканский (%s)"
            % QUESTIONS["cpi_region"])
    if year_from == year_to:
        out.update({"Статус": STATUS_OK, "Коэффициент": 1.0, "Значение": float(amount),
                    "Расчёт": "годы совпадают — сумма не меняется"})
        return out
    lo, hi = min(year_from, year_to), max(year_from, year_to)
    k, missing = 1.0, []
    for y in range(lo + 1, hi + 1):
        v = series_value(con, ds_id, str(y), "")
        if not v or not v["value"]:
            missing.append(y)
            continue
        k *= v["value"] / 100.0
        out["Множители"].append({"Период": str(y), "Индекс, %": v["value"],
                                 "Единица": v["unit"], "Ссылка": v["url"]})
    if missing:
        out.update({"Статус": STATUS_NODATA, "Значение": None, "Коэффициент": None,
                    "Причина": "у источника нет индекса за %s — цепочка разорвана, "
                               "недостающие годы не домысливаются"
                               % ", ".join(str(m) for m in missing)})
        return out
    forward = year_to > year_from
    coef = k if forward else 1.0 / k
    out.update({"Статус": STATUS_OK, "Коэффициент": round(coef, 6),
                "Значение": round(float(amount) * coef, 2),
                "Расчёт": "%s × %s = %s (%s)" % (
                    _n(amount), round(coef, 6), _n(float(amount) * coef),
                    "произведение индексов %s" % " × ".join(
                        "%g/100" % m["Индекс, %"] for m in out["Множители"])
                    + ("" if forward else ", приведение назад — деление на произведение"))})
    return out


def parse_quarter(period: str):
    """«2024-Q3» → (2024, 3); что-то другое → None."""
    s = (period or "").strip().upper().replace(" ", "")
    if len(s) != 7 or s[4] != "-" or s[5] != "Q":
        return None
    if not s[:4].isdigit() or s[6] not in "1234":
        return None
    return int(s[:4]), int(s[6])


def quarters_between(period_from: str, period_to: str) -> list:
    """Кварталы строго после period_from и по period_to включительно, без пропусков."""
    a, b = parse_quarter(period_from), parse_quarter(period_to)
    if a is None or b is None:
        return []
    out, y, q = [], a[0], a[1]
    while True:
        q += 1
        if q > 4:
            q, y = 1, y + 1
        if (y, q) > b:
            break
        out.append("%d-Q%d" % (y, q))
    return out


def house_index_chain(con, region: str, period_from: str, period_to: str = None,
                      dataset_id: str = "house_price_index") -> dict:
    """
    Коэффициент приведения цены жилья из квартала A в квартал B по индексам stat.uz.

    Формула:  цена(B) = цена(A) × произведение квартальных индексов за кварталы после A
              и по B включительно, каждый / 100 (индекс дан к предыдущему кварталу).
    Это ИНДЕКС, а не уровень цены: источник цены квадратного метра не даёт.
    Цепочка должна быть непрерывной: нет индекса хотя бы за один квартал — «нет данных»
    с перечнем недостающих кварталов (как в годовой indexation), без замены соседним кварталом.
    """
    if dataset_id not in ("house_price_index", "house_price_index_primary", "house_price_index_secondary"):
        raise HTTPException(400, "набор «%s» не является индексом цен на жильё" % dataset_id)
    key = region_key(region) or "total"
    ref = _dataset_ref(dataset_id)
    out = {"Показатель": "Приведение цены жилья по индексу цен",
           "Регион": region_name(key), "region": key,
           "Формула": "цена квартала A × произведение квартальных индексов за все кварталы после A "
                      "и по квартал B включительно, каждый индекс делится на 100",
           "Источники": [ref], "Множители": [], "Предупреждения": list(ref["Ограничения"])}
    out["Предупреждения"].append("метод приведения с заказчиком ещё не утверждён — " + QUESTIONS["house_price"])
    to = period_to or last_period(con, dataset_id, key)
    out["Квартал исходный"] = period_from
    out["Квартал целевой"] = to
    if not to:
        out.update({"Статус": STATUS_NODATA, "Коэффициент": None,
                    "Причина": "у набора нет ни одного значения по этому региону"})
        return out
    a, b = parse_quarter(period_from), parse_quarter(to)
    if a is None or b is None:
        raise HTTPException(400, "квартал указывается как 2024-Q3, получено «%s» и «%s»"
                            % (period_from, to))
    if a == b:
        out.update({"Статус": STATUS_OK, "Коэффициент": 1.0, "Множители": [],
                    "Расчёт": "кварталы совпадают — цена не меняется"})
        return out
    if b < a:
        out.update({"Статус": STATUS_NODATA, "Коэффициент": None,
                    "Причина": "целевой квартал %s раньше исходного %s — приведение назад "
                               "по этому набору не считается" % (to, period_from)})
        return out
    # цепочка квартальная и непрерывная: индекс каждого квартала даётся к предыдущему,
    # поэтому пропуск хотя бы одного квартала обесценивает произведение
    need = quarters_between(period_from, to)
    rows = {r["period"]: r for r in db.rows(
        con, "SELECT period, value, unit, url FROM stat_series WHERE dataset_id=? AND region=? "
             "AND period>? AND period<=? AND value IS NOT NULL ORDER BY period",
        dataset_id, key, period_from, to)}
    missing = [p for p in need if p not in rows or not rows[p]["value"]]
    if missing:
        out.update({"Статус": STATUS_NODATA, "Коэффициент": None, "Множители": [],
                    "Кварталов в цепочке": len(need),
                    "Недостающие кварталы": missing,
                    "Причина": "у источника нет индекса за %s — цепочка кварталов разорвана, "
                               "недостающие кварталы не домысливаются и соседними не заменяются"
                               % ", ".join(missing)})
        return out
    k = 1.0
    for p in need:
        r = rows[p]
        k *= r["value"] / 100.0
        out["Множители"].append({"Период": r["period"], "Индекс, %": r["value"],
                                 "Единица": r["unit"], "Ссылка": r["url"]})
    out.update({"Статус": STATUS_OK, "Коэффициент": round(k, 6),
                "Кварталов в цепочке": len(need),
                "Расчёт": "произведение %s = %s" % (
                    " × ".join("%g/100" % m["Индекс, %"] for m in out["Множители"]), round(k, 6))})
    return out


def _n(x) -> str:
    return format(float(x), ",.2f").replace(",", " ")


# --------------------------------------------------------------------------- #
# Источник «Агентство статистики» для оценки недвижимости
# --------------------------------------------------------------------------- #

VALUATION_SOURCE_NAME = "stat.uz — индекс цен на жильё"
VALUATION_STATUS = "Агентство статистики"


def valuation_source(con, params: dict = None, as_of=None) -> dict:
    """
    Источник для app/valuation.py: не рынок, а индекс.

    Площадки недвижимости закрыты (olx — 403, uybor и joymee рисуются в браузере), цены
    квадратного метра у органа статистики нет. Что есть — индекс цен на жильё по регионам,
    к предыдущему кварталу. Значит роль источника честная: привести УЖЕ известную цену
    (отчёт оценщика, прошлый договор) к текущему кварталу.

    Источник возвращается со статусом «Агентство статистики»: он не даёт цен и в медиану
    объявлений не попадает — ни одной цены отсюда в расчёт не уходит.
    Если в params переданы base_price и base_period, коэффициент применяется и показывается;
    метод приведения до утверждения заказчиком (вопрос 50) — справочный, не обязательный.
    """
    params = dict(params or {})
    ref = _dataset_ref("house_price_index")
    key = region_key(params.get("region") or params.get("регион") or params.get("city")
                     or params.get("город")) or "total"
    res = {"source": VALUATION_SOURCE_NAME, "status": VALUATION_STATUS,
           "url": ref["Ссылка"], "ads_count": 0, "prices": [], "median": None, "q1": None, "q3": None,
           "fetched_at": db.now(), "dataset": ref,
           "region": region_name(key),
           "limitations": list(ref["Ограничения"]) + [
               "источник даёт индекс, а не цену квадратного метра: заменить рынок он не может",
               "метод приведения цены индексом с заказчиком не утверждён — " + QUESTIONS["house_price"]],
           "reason": ""}
    base_period = params.get("base_period") or params.get("квартал базовой цены")
    base_price = params.get("base_price") or params.get("базовая цена")
    to = last_period(con, "house_price_index", key)
    res["last_quarter"] = to
    if not to:
        res["reason"] = ("индекса цен на жильё по региону «%s» в базе нет — запустите "
                         "обновление POST /stat/refresh?dataset=house_price_index" % region_name(key))
        return res
    if not base_period:
        res["reason"] = ("цен квадратного метра источник не публикует. Доступен индекс цен на жильё "
                         "по региону «%s», последний квартал %s: им приводится ранее известная цена "
                         "(отчёт оценщика или прошлый договор) к дате договора. Передайте "
                         "base_price и base_period — и приведение будет посчитано" % (region_name(key), to))
        return res
    chain = house_index_chain(con, key, base_period, to)
    res["chain"] = chain
    if chain["Статус"] != STATUS_OK or chain.get("Коэффициент") is None:
        res["reason"] = "приведение не посчитано: " + (chain.get("Причина") or "нет индексов")
        return res
    res["coefficient"] = chain["Коэффициент"]
    if base_price:
        try:
            res["indexed_price"] = round(float(base_price) * chain["Коэффициент"], 2)
        except (TypeError, ValueError):
            res["indexed_price"] = None
    res["reason"] = ("цену квадратного метра источник не даёт; известная цена за %s приведена "
                     "к %s коэффициентом %s (%s)" % (base_period, to, chain["Коэффициент"],
                                                     chain.get("Расчёт", "")))
    return res


# --------------------------------------------------------------------------- #
# Обновление и расписание
# --------------------------------------------------------------------------- #

REFRESH_EVERY_SEC = 24 * 3600
_state = {"last": None, "running": False, "statuses": {}, "error": None}


def refresh(dataset_id: str = None) -> list:
    """
    Обновление наборов. Сеть или сайт недоступны — это статус набора, а не исключение:
    список статусов возвращается всегда, сервер работает дальше.
    """
    ids = [dataset_id] if dataset_id else list(ss.DATASETS)
    out = []
    for ds_id in ids:
        try:
            st = ss.fetch_and_save(ds_id)
        except Exception as e:                       # сеть, файловая система, разбор
            st = {"dataset_id": ds_id, "status": ss.STATUS_DOWN,
                  "reason": "%s: %s" % (type(e).__name__, e),
                  "name": (ss.DATASETS.get(ds_id) or {}).get("name", ds_id),
                  "url": (ss.DATASETS.get(ds_id) or {}).get("page", ""),
                  "fetched_at": db.now(), "rows": 0,
                  "saved": {"вставлено": 0, "изменено": 0, "без изменений": 0, "всего": 0}}
        _state["statuses"][ds_id] = st
        out.append(st)
    _state["last"] = db.now()
    try:
        with db.tx() as con:
            db.audit(con, "агент-статистик", "обновление данных агентства статистики", "stat_series",
                     {s["dataset_id"]: {"статус": s["status"], "сохранено": s.get("saved")} for s in out})
    except Exception:                                # журнал не должен ронять обновление
        pass
    return out


def _refresh_job():
    if _state["running"]:
        return
    _state["running"] = True
    try:
        refresh()
        _state["error"] = None
    except Exception as e:                           # ошибка не должна ронять поток и сервер
        _state["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        _state["running"] = False


def _scheduler():
    time.sleep(180)                                  # даём серверу подняться и не спорить с НАПП за сеть
    while True:
        _refresh_job()
        time.sleep(REFRESH_EVERY_SEC)


def start_scheduler():
    threading.Thread(target=_scheduler, daemon=True, name="stat-agency-refresh").start()


def last_statuses() -> dict:
    """Статусы последнего обновления — для ежедневного доклада."""
    return {"last": _state["last"], "error": _state["error"],
            "statuses": dict(_state["statuses"])}


# --------------------------------------------------------------------------- #
# Точки подключения
# --------------------------------------------------------------------------- #

@router.post("/stat/refresh")
def post_refresh(dataset: str = None):
    """Ручное обновление: все наборы или один по ?dataset=. Полный обход занимает около минуты."""
    if dataset and dataset not in ss.DATASETS:
        raise HTTPException(404, "набор «%s» не зарегистрирован" % dataset)
    res = refresh(dataset)
    return {"обновлено наборов": len(res), "когда": _state["last"], "наборы": res}


@router.get("/stat/series")
def get_series(dataset: str = None, region: str = None, period: str = None,
               key: str = None, limit: int = 500, offset: int = 0):
    """Ряды из stat_series с фильтрами. В каждой строке — ссылка на страницу набора."""
    sql = "SELECT * FROM stat_series WHERE 1=1"
    args = []
    if dataset:
        if dataset not in ss.DATASETS:
            raise HTTPException(404, "набор «%s» не зарегистрирован" % dataset)
        sql += " AND dataset_id=?"
        args.append(dataset)
    if region is not None:
        rk = region_key(region)
        if region and not rk:
            raise HTTPException(400, "регион «%s» не опознан" % region)
        sql += " AND region=?"
        args.append(rk)
    if period:
        sql += " AND period=?"
        args.append(period)
    if key:
        sql += " AND key=?"
        args.append(key)
    sql += " ORDER BY dataset_id, region, period LIMIT ? OFFSET ?"
    args += [max(1, min(int(limit), 5000)), max(0, int(offset))]
    with db.tx() as con:
        rows = db.rows(con, sql, *args)
        # подзапросу нужен псевдоним — иначе PostgreSQL такой запрос не примет
        total = db.rows(con, "SELECT COUNT(*) n FROM (" + sql.split(" ORDER BY")[0] + ") t",
                        *args[:-2])[0]["n"]
    for r in rows:
        d = ss.DATASETS.get(r["dataset_id"]) or {}
        r["Набор"] = d.get("name", r["dataset_id"])
        r["Регион"] = region_name(r["region"]) if r["region"] else "разреза нет"
        r["Ссылка"] = r["url"]
    return {"Всего строк": total, "Показано": len(rows), "Строки": rows}


@router.get("/stat/status")
def get_status():
    """По каждому набору: что это, когда обновляли, статус источника, строки, периоды, ссылка."""
    with db.tx() as con:
        agg = {r["dataset_id"]: r for r in db.rows(
            con, "SELECT dataset_id, COUNT(*) n, MIN(period) p_min, MAX(period) p_max, "
                 "MAX(fetched_at) last_fetch, SUM(CASE WHEN value IS NULL THEN 1 ELSE 0 END) empties "
                 "FROM stat_series GROUP BY dataset_id")}
        revs = {r["dataset_id"]: r["n"] for r in db.rows(
            con, "SELECT dataset_id, COUNT(*) n FROM stat_series_revisions GROUP BY dataset_id")}
    out = []
    for ds_id, d in ss.DATASETS.items():
        a = agg.get(ds_id) or {}
        st = _state["statuses"].get(ds_id)
        out.append({
            "id": ds_id, "Набор": d["name"], "Источник": d["source"],
            "Ссылка на набор": d["page"], "Адрес данных": d["data_url"],
            "Периодичность": d["period"], "Единица": d["unit"],
            "Регионы": "да" if d["regions"] else "нет",
            "Строк в базе": a.get("n", 0),
            "Пустых значений": a.get("empties", 0),
            "Период от": a.get("p_min"), "Период до": a.get("p_max"),
            "Последнее обновление": a.get("last_fetch"),
            "Статус источника": (st or {}).get("status", "с запуска сервера не обновлялся"),
            "Причина": (st or {}).get("reason", ""),
            "Проверен": (st or {}).get("fetched_at"),
            "Сохранено при последнем обновлении": (st or {}).get("saved"),
            "Пересчётов задним числом": revs.get(ds_id, 0),
            "Зачем": d["purpose"], "Ограничения": d["limits"],
        })
    return {"Наборов": len(out), "Последнее массовое обновление": _state["last"],
            "Обновление идёт": _state["running"], "Ошибка расписания": _state["error"],
            "Периодичность обновления": "раз в сутки внутри сервера", "Наборы": out}


@router.get("/stat/indicators")
def get_indicators(year: int = None, region: str = None):
    """Все производные показатели сразу: значение, формула словами, источники, оговорки."""
    with db.tx() as con:
        return {"Показатели": [
            premiums_per_capita(con, year, region),
            penetration(con, year, region),
            housing_vs_fund(con, year),
            osago_vs_fleet(con, year),
        ]}


@router.get("/stat/indicators/premiums-per-capita")
def get_ppc(year: int = None, region: str = None):
    with db.tx() as con:
        return premiums_per_capita(con, year, region)


@router.get("/stat/indicators/penetration")
def get_pen(year: int = None, region: str = None):
    with db.tx() as con:
        return penetration(con, year, region)


@router.get("/stat/indicators/housing")
def get_housing(year: int = None):
    with db.tx() as con:
        return housing_vs_fund(con, year)


@router.get("/stat/indicators/osago")
def get_osago(year: int = None):
    with db.tx() as con:
        return osago_vs_fleet(con, year)


@router.get("/stat/indexation")
def get_indexation(amount: float, year_from: int, year_to: int, index: str = "cpi"):
    """Приведение суммы из года в год: index = cpi (потребительские цены) | construction (строительство)."""
    with db.tx() as con:
        return indexation(con, amount, year_from, year_to, index)


@router.get("/stat/house-index")
def get_house_index(region: str = "total", period_from: str = None, period_to: str = None,
                    dataset: str = "house_price_index", base_price: float = None):
    """Коэффициент приведения цены жилья по индексу stat.uz между кварталами."""
    with db.tx() as con:
        if not period_from:
            raise HTTPException(400, "укажите period_from — квартал, за который известна цена, "
                                     "например 2024-Q3")
        res = house_index_chain(con, region, period_from, period_to, dataset)
        if base_price and res.get("Коэффициент"):
            res["Цена на входе"] = base_price
            res["Приведённая цена"] = round(float(base_price) * res["Коэффициент"], 2)
        return res


@router.get("/stat/catalog")
def get_catalog():
    """Реестр наборов как он есть в app/stat_sources.py (без обращения к сети)."""
    return {"Наборов": len(ss.DATASETS), "Наборы": ss.catalog()}
