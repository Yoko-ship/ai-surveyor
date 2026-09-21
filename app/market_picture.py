"""
«Техническая картина рынка» — блок вкладки «Аналитика» мини-аппа.

Что отдаёт picture():
  market  — по классу страхования: средняя годовая ставка рынка НАПП (премии / страховые
            обязательства), её ряд по срезам для sparkline, убыточность (выплаты / премии)
            и её ряд, доля INSON, отклонение нашей ставки от рыночной;
  region  — по данным stat.uz: население, ВРП и ВРП на душу, проникновение страхования
            (премии НАПП к ВРП), индекс цен на жильё и на строительство;
  sources — список {id, title, url, domain, as_of} для плашки «Читать в источнике» (.srcbar);
  notes   — чего в данных нет.

Правило модуля: ничего не выдумывается. Нет данных — значение None и пояснение в notes.
У каждого числа есть поле src — ссылка на id в списке sources.

Данные только читаются: таблицы market_stats (tools/market_stats.py, отчёты НАПП, лист 1.4
«классы», листы 2.x «страховщики», 3.x «регионы») и stat_series (app/stat_sources.py).
Формулы те же, что в /market/series и db.market_rates (рыночный ориентир экрана расчёта):
  годовая ставка, % = премии с начала года × 12 / число месяцев среза / обязательства на дату × 100;
  убыточность, %    = выплаты с начала года / премии с начала года × 100.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

from . import db
from . import stat_sources as ss

# --------------------------------------------------------------------------- #
# Источники
# --------------------------------------------------------------------------- #
# Страница, с которой tools/napp_download.py забирает отчёты (проверено загрузчиком).
NAPP_PAGE = "https://napp.uz/pages/statistics-and-analysis-for-im"
NAPP_TITLE = "НАПП — отчёт о страховом рынке"

# Сколько месяцев накоплено в срезе НАПП: ключ — «месяц-день» отчётной даты.
MONTHS = {"03-31": 3, "04-01": 3, "07-01": 6, "10-01": 9, "01-01": 12}
QUARTER_OF = {"03-31": 1, "04-01": 1, "07-01": 2, "10-01": 3, "01-01": 4}
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV"}

SERIES_MAX = 12            # сколько последних точек ряда отдаём для sparkline stat.uz
INSON_ROW = "company:INSON AJ"

# --------------------------------------------------------------------------- #
# Классы проекта → строки отчёта НАПП (market_stats.row_key)
# --------------------------------------------------------------------------- #
# Основная строка совпадает с db.MARKET_ROW (рыночный ориентир экрана расчёта): для классов
# 8 и 9 взят пакет «8, 9» — одиночные строки в отчёте на порядки меньше пакета
# (2025 год: пакет 2 140 444 млн сум премий, класс 9 отдельно — 3 419 млн сум).
# Одиночные строки показываются как «alternatives».
CLASS_ROWS = {
    "1": "cls1", "2": "cls2", "3": "cls3", "4": "cls4", "5": "cls5", "6": "cls6", "7": "cls7",
    "8": "cls8_9", "9": "cls8_9", "10": "cls10", "11": "cls11", "12": "cls12", "13": "cls13",
    "14": "cls14", "15": "cls15", "16": "cls16", "17": "cls17",
    "13з": None,     # ответственность заёмщиков: отдельной строки в отчёте НАПП нет
    "16у": None,     # урожай с/х культур: отдельной строки в отчёте НАПП нет
}
CLASS_ROWS.update(getattr(db, "MARKET_ROW", {}))      # если ориентир движка поменяют — идём за ним

ALTERNATIVES = {
    "8": ["cls8"], "9": ["cls9"],
    "10": ["osago"],  # класс 10 в отчёте — добровольная автогражданка; ОСАГО — отдельная строка
}

NO_ROW_REASON = {
    "13з": "в отчёте НАПП нет отдельной строки «ответственность заёмщиков за непогашение кредитов»; "
           "в какую строку (класс 13 или 14) НАПП её относит — в отчёте не сказано",
    "16у": "в отчёте НАПП нет отдельной строки «урожай сельскохозяйственных культур»; "
           "входит ли он в класс 16 — в отчёте не сказано",
}

# --------------------------------------------------------------------------- #
# Регионы: справочник проекта (русские названия, как в app/docparse.py REGIONS)
#          → ключ региона в stat_series и market_stats (СОАТО — см. app/stat_sources.py)
# --------------------------------------------------------------------------- #
REGION_TABLE = [
    # (название в справочнике проекта, ключ, СОАТО stat.uz, псевдонимы)
    ("Республика Каракалпакстан", "region:QORAQALPOG'ISTON RESPUBLIKASI", "1735",
     ("каракалпакстан", "qoraqalpog'iston", "нукус")),
    ("Андижанская область", "region:ANDIJON", "1703", ("андижан", "andijon")),
    ("Бухарская область", "region:BUXORO", "1706", ("бухара", "buxoro")),
    ("Джизакская область", "region:JIZZAX", "1708", ("джизак", "jizzax")),
    ("Кашкадарьинская область", "region:QASHQADARYO", "1710", ("кашкадарья", "карши", "qashqadaryo")),
    ("Навоийская область", "region:NAVOIY", "1712", ("навои", "navoiy")),
    ("Наманганская область", "region:NAMANGAN", "1714", ("наманган", "namangan")),
    ("Самаркандская область", "region:SAMARQAND", "1718", ("самарканд", "samarqand")),
    ("Сурхандарьинская область", "region:SURXANDARYO", "1722",
     ("сурхандарья", "термез", "surxondaryo", "surxandaryo")),
    ("Сырдарьинская область", "region:SIRDARYO", "1724", ("сырдарья", "гулистан", "sirdaryo")),
    ("Ташкентская область", "region:TOSHKENT", "1727", ("ташкентская", "toshkent viloyati")),
    ("Ферганская область", "region:FARG'ONA", "1730", ("фергана", "farg'ona")),
    ("Хорезмская область", "region:XORAZM", "1733", ("хорезм", "ургенч", "xorazm")),
    # «Ташкент» без уточнения — город (так же решает app/docparse.py)
    ("город Ташкент", "region:TOSHKENT SHAHRI", "1726",
     ("ташкент", "г. ташкент", "г.ташкент", "toshkent", "toshkent shahri", "тошкент")),
]
REPUBLIC = ("Республика Узбекистан", "total", "1700", ("узбекистан", "республика", "uzbekistan"))

_APOS = "‘’ʻʼ`´′ʹ'"


def _norm(s: str) -> str:
    t = (s or "").strip().lower().replace("ё", "е")
    for a in _APOS:
        t = t.replace(a, "'")
    return " ".join(t.split())


def resolve_region(value: Optional[str]):
    """Название / псевдоним / ключ / СОАТО → (ключ, русское название) или (None, None)."""
    v = _norm(value)
    if not v:
        return None, None
    for name, key, soato, aliases in REGION_TABLE + [REPUBLIC]:
        if v in (_norm(name), _norm(key), soato) or v in aliases or v == _norm(key.replace("region:", "")):
            return key, name
    return None, None


# --------------------------------------------------------------------------- #
# Мелочи
# --------------------------------------------------------------------------- #

def _rows(con, sql, *args) -> list:
    cur = con.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, tuple(r))) for r in cur.fetchall()]


def _label(d: str) -> str:
    """Дата среза → подпись периода: '2026-01-01' → '2025 год', '2026-07-01' → '2026 · 6 мес.'."""
    m = MONTHS.get(d[5:])
    if m == 12:
        return "%d год" % (int(d[:4]) - 1)
    return "%s · %d мес." % (d[:4], m) if m else d


def _quarter(d: str):
    q = QUARTER_OF.get(d[5:])
    if not q:
        return None
    return (int(d[:4]) - 1, 4) if q == 4 else (int(d[:4]), q)


def _annual_rate(r) -> Optional[float]:
    m = MONTHS.get(r["report_date"][5:])
    if not m or not r["premiums_ytd"] or not r["liabilities"]:
        return None
    return r["premiums_ytd"] * 12 / m / r["liabilities"] * 100


def _loss_ratio(r) -> Optional[float]:
    if not r["premiums_ytd"] or r["payouts_ytd"] is None:
        return None
    return r["payouts_ytd"] / r["premiums_ytd"] * 100


def _r(x, n=3):
    return None if x is None else round(x, n)


class _Sources:
    """Реестр источников ответа: одинаковые (title, url) склеиваются, id — порядковые."""

    def __init__(self):
        self.items = []

    def add(self, title: str, url: str, as_of: Optional[str], **extra) -> str:
        for s in self.items:
            if s["title"] == title and s["url"] == url:
                if as_of and (not s["as_of"] or as_of > s["as_of"]):
                    s["as_of"] = as_of
                for d in extra.get("slices") or []:          # один отчёт НАПП несёт два среза
                    if d not in s.setdefault("slices", []):
                        s["slices"].append(d)
                        s["slices"].sort()
                return s["id"]
        sid = "s%d" % (len(self.items) + 1)
        self.items.append({"id": sid, "title": title, "url": url,
                           "domain": urlsplit(url).netloc or url, "as_of": as_of, **extra})
        return sid


# --------------------------------------------------------------------------- #
# Рынок по классу (НАПП)
# --------------------------------------------------------------------------- #

def _napp_src(src: _Sources, report_date: str, source_file: Optional[str]) -> str:
    title = NAPP_TITLE + (" (%s)" % source_file.replace("_", " ").strip() if source_file else "")
    return src.add(title, NAPP_PAGE, report_date, table="market_stats", slices=[report_date])


def _row_block(con, row_key: str, src: _Sources) -> Optional[dict]:
    pts = _rows(con, "SELECT report_date, row_name, premiums_ytd, payouts_ytd, liabilities, source_file "
                     "FROM market_stats WHERE row_key=? ORDER BY report_date", row_key)
    if not pts:
        return None
    rate_s, lr_s = [], []
    for p in pts:
        sid = _napp_src(src, p["report_date"], p["source_file"])
        rate_s.append({"period": _label(p["report_date"]), "date": p["report_date"],
                       "value": _r(_annual_rate(p)), "src": sid})
        lr_s.append({"period": _label(p["report_date"]), "date": p["report_date"],
                     "value": _r(_loss_ratio(p)), "src": sid})
    last = pts[-1]
    full = [p for p in pts if p["report_date"][5:] == "01-01"]
    fy = full[-1] if full else None
    return {"row_key": row_key, "row_name": last["row_name"], "points": pts,
            "rate_series": rate_s, "loss_ratio_series": lr_s, "last": last, "full_year": fy}


def _gaps(dates: list) -> list:
    """Пропущенные кварталы между первым и последним срезом (например, «I квартал 2026»)."""
    qs = sorted({q for q in (_quarter(d) for d in dates) if q})
    if not qs:
        return []
    have, out = set(qs), []
    y, q = qs[0]
    while (y, q) < qs[-1]:
        q += 1
        if q > 4:
            y, q = y + 1, 1
        if (y, q) not in have:
            out.append("%s квартал %d" % (ROMAN[q], y))
    return out


def _market(con, class_code: str, product_code, our_rate_pct, src: _Sources, notes: list) -> dict:
    cls = _rows(con, "SELECT code, name FROM classes WHERE code=?", class_code)
    out = {"class_code": class_code, "class_name": cls[0]["name"] if cls else None,
           "unit_money": "млн сум", "row_key": None, "row_name": None, "row_kind": None,
           "rate_pct": None, "rate_period": None, "rate_date": None,
           "rate_full_year_pct": None, "rate_full_year_period": None,
           "rate_series": [], "loss_ratio_pct": None, "loss_ratio_period": None,
           "loss_ratio_full_year_pct": None, "loss_ratio_series": [],
           "premiums": None, "payouts": None, "liabilities": None,
           "inson_share": None, "our_rate_pct": our_rate_pct, "deviation": None,
           "periods": 0, "first_date": None, "last_date": None, "missing_quarters": [],
           "alternatives": [], "product": None,
           "formula": {"rate": "премии с начала года × 12 / число месяцев среза / страховые обязательства "
                               "на дату среза × 100",
                       "loss_ratio": "выплаты с начала года / премии с начала года × 100",
                       "deviation": "п.п. = наша ставка − рыночная; % = (наша / рыночная − 1) × 100"},
           "caveats": ["премии и выплаты — нарастающим итогом за период, страховые обязательства — "
                       "на дату среза; ставка неполного года приведена к году линейно (без сезонности)",
                       "выплаты — оплаченные за период, а не произошедшие убытки; резервы не учтены",
                       "числа договоров и страховых случаев в отчётах НАПП нет",
                       "срез «01.01» — итог предыдущего года"]}
    if not cls:
        notes.append("класс «%s» не найден в справочнике classes" % class_code)
        return out
    out["inson_share"] = _inson_share(con, src, notes)

    # продукт: только проверка и подпись — в отчётах НАПП разреза по продуктам нет
    if product_code:
        pr = _rows(con, "SELECT code, name FROM products WHERE code=?", product_code)
        pcs = [r["class_code"] for r in _rows(
            con, "SELECT class_code FROM product_classes WHERE product_code=? ORDER BY part_no", product_code)]
        out["product"] = {"code": product_code, "name": pr[0]["name"] if pr else None, "classes": pcs,
                          "class_in_product": class_code in pcs}
        if not pr:
            notes.append("продукт %s не найден в справочнике products" % product_code)
        elif class_code not in pcs:
            notes.append("класс %s не входит в продукт %s (классы продукта: %s)"
                         % (class_code, product_code, ", ".join(pcs) or "нет"))
        notes.append("в отчётах НАПП нет разреза по продуктам — рынок показан по классу %s" % class_code)

    row_key = CLASS_ROWS.get(class_code)
    if not row_key:
        notes.append("рыночных данных по классу %s нет: %s" % (
            class_code, NO_ROW_REASON.get(class_code, "класс не сопоставлен со строкой отчёта НАПП")))
        out["mapping_note"] = NO_ROW_REASON.get(class_code)
        return out
    blk = _row_block(con, row_key, src)
    if not blk:
        notes.append("в market_stats нет строки %s для класса %s" % (row_key, class_code))
        return out

    out.update({"row_key": row_key, "row_name": blk["row_name"],
                "row_kind": "пакет классов" if "_" in row_key else "класс",
                "rate_series": blk["rate_series"], "loss_ratio_series": blk["loss_ratio_series"]})
    if "_" in row_key:
        out["mapping_note"] = ("для класса %s взят пакет %s — тот же ряд, что у рыночного ориентира "
                               "экрана расчёта; одиночная строка класса — в alternatives"
                               % (class_code, row_key))
    dates = [p["report_date"] for p in blk["points"]]
    out["periods"], out["first_date"], out["last_date"] = len(dates), dates[0], dates[-1]
    out["missing_quarters"] = _gaps(dates)
    if out["missing_quarters"]:
        notes.append("в ряду НАПП нет срезов: %s (отчёт не загружен или не опубликован)"
                     % ", ".join(out["missing_quarters"]))

    last = blk["last"]
    sid = _napp_src(src, last["report_date"], last["source_file"])
    out["premiums"] = {"value": _r(last["premiums_ytd"]), "period": _label(last["report_date"]), "src": sid}
    out["payouts"] = {"value": _r(last["payouts_ytd"]), "period": _label(last["report_date"]), "src": sid}
    out["liabilities"] = {"value": _r(last["liabilities"]), "date": last["report_date"], "src": sid}
    rate = _annual_rate(last)
    out["rate_pct"], out["rate_period"], out["rate_date"] = _r(rate), _label(last["report_date"]), last["report_date"]
    out["rate_src"] = sid
    lr = _loss_ratio(last)
    out["loss_ratio_pct"], out["loss_ratio_period"], out["loss_ratio_src"] = _r(lr), _label(last["report_date"]), sid
    if rate is None:
        notes.append("ставка по %s на %s не считается: %s" % (
            row_key, last["report_date"],
            "страховые обязательства равны нулю или не даны" if not last["liabilities"] else "нет премий"))
    if lr is None:
        notes.append("убыточность по %s на %s не считается: премий нет" % (row_key, last["report_date"]))
    fy = blk["full_year"]
    if fy:
        fsid = _napp_src(src, fy["report_date"], fy["source_file"])
        out["rate_full_year_pct"], out["rate_full_year_period"] = _r(_annual_rate(fy)), _label(fy["report_date"])
        out["loss_ratio_full_year_pct"] = _r(_loss_ratio(fy))
        out["full_year_src"] = fsid
    else:
        notes.append("полного года по %s в данных нет" % row_key)

    # отклонение нашей ставки
    if our_rate_pct is not None:
        dev = {"vs": "rate_pct", "market_rate_pct": out["rate_pct"], "src": sid}
        if rate:
            dev["pp"] = _r(our_rate_pct - rate)
            dev["pct"] = _r((our_rate_pct / rate - 1) * 100, 1)
        else:
            dev["pp"] = dev["pct"] = None
            notes.append("отклонение нашей ставки не считается: рыночной ставки нет")
        fyr = _annual_rate(fy) if fy else None
        if fyr:
            dev["full_year"] = {"market_rate_pct": _r(fyr), "period": _label(fy["report_date"]),
                                "pp": _r(our_rate_pct - fyr), "pct": _r((our_rate_pct / fyr - 1) * 100, 1),
                                "src": out["full_year_src"]}
        out["deviation"] = dev

    # одиночные строки класса и родственные строки
    for alt in ALTERNATIVES.get(class_code, []):
        a = _row_block(con, alt, src)
        if not a:
            continue
        al = a["last"]
        out["alternatives"].append({
            "row_key": alt, "row_name": a["row_name"], "period": _label(al["report_date"]),
            "rate_pct": _r(_annual_rate(al)), "loss_ratio_pct": _r(_loss_ratio(al)),
            "premiums": _r(al["premiums_ytd"]), "src": _napp_src(src, al["report_date"], al["source_file"])})

    for key, val in (("loss_ratio_pct", out["loss_ratio_pct"]), ("loss_ratio_full_year_pct",
                                                                   out["loss_ratio_full_year_pct"])):
        if val is not None and val > 200:
            notes.append("убыточность %s по %s = %s %% — выплаты больше премий вдвое и более; "
                         "вероятно, выплаты по договорам прошлых лет при малых премиях — сверить с отчётом"
                         % ("последнего среза" if key == "loss_ratio_pct" else "полного года", row_key, val))
    return out


def _inson_share(con, src: _Sources, notes: list) -> dict:
    """Доля INSON по премиям: по классу — нет разреза; по рынку в целом — листы 2.x НАПП."""
    res = {"by_class_pct": None,
           "by_class_note": "нет выгрузки компании: в отчётах НАПП разреза «страховщик × класс» нет, "
                            "а договоров INSON в базе нет",
           "market_pct": None, "period": None, "series": [],
           "formula": "премии INSON с начала года / сумма премий всех страховщиков в том же срезе "
                      "(лист 2.1 отчёта НАПП) × 100"}
    have = _rows(con, "SELECT COUNT(*) n FROM market_stats WHERE row_key LIKE 'company:%'")[0]["n"]
    if not have:
        res["market_note"] = "нет выгрузки компании: в market_stats нет строк страховщиков"
        notes.append("доля INSON не считается: нет строк company:* в market_stats")
        return res
    pts = _rows(con, "SELECT m.report_date, m.premiums_ytd p, m.source_file, "
                     "(SELECT SUM(premiums_ytd) FROM market_stats c WHERE c.report_date=m.report_date "
                     " AND c.row_key LIKE 'company:%') tot "
                     "FROM market_stats m WHERE m.row_key=? ORDER BY m.report_date", INSON_ROW)
    if not pts:
        res["market_note"] = "в отчётах НАПП нет строки INSON"
        notes.append("доля INSON не считается: строки %s в market_stats нет" % INSON_ROW)
        return res
    for p in pts:
        v = p["p"] / p["tot"] * 100 if p["p"] is not None and p["tot"] else None
        res["series"].append({"period": _label(p["report_date"]), "date": p["report_date"], "value": _r(v),
                              "src": _napp_src(src, p["report_date"], p["source_file"])})
    lastp = res["series"][-1]
    res.update({"market_pct": lastp["value"], "period": lastp["period"], "date": lastp["date"],
                "src": lastp["src"]})
    notes.append("доля INSON по классу неизвестна: нужны выгрузки договоров компании "
                 "(в НАПП доля есть только по рынку в целом)")
    return res


# --------------------------------------------------------------------------- #
# Регион (stat.uz)
# --------------------------------------------------------------------------- #

def _stat_src(src: _Sources, ds_id: str, url: str, as_of: str, fetched: str) -> str:
    d = ss.DATASETS.get(ds_id) or {}
    title = "%s — %s" % (d.get("source", "stat.uz"), d.get("name", ds_id))
    return src.add(title, url, as_of, fetched_at=fetched)


def _stat_indicator(con, ds_id: str, key: str, src: _Sources, notes: list, what: str) -> dict:
    """Последнее значение и ряд набора по региону; нет региона у набора — республика с level."""
    d = ss.DATASETS.get(ds_id) or {}
    ind = {"dataset": ds_id, "name": d.get("name", ds_id), "value": None, "period": None,
           "unit": None, "level": None, "series": [], "src": None}
    regional = bool(d.get("regions"))
    if regional:
        want = key or "total"
        level = "республика" if want == "total" else "регион"
    else:
        want, level = "", "республика"
    if key and key != "total" and not regional:
        ind["note"] = "у набора нет разреза по регионам — показана республика"
    pts = _rows(con, "SELECT period, value, unit, url, fetched_at FROM stat_series "
                     "WHERE dataset_id=? AND region=? AND value IS NOT NULL ORDER BY period", ds_id, want)
    if ds_id == "housing_commissioned":
        pts = [p for p in pts if "-" not in p["period"]]            # только годовые столбцы
    if not pts:
        notes.append("%s: в stat_series нет значений набора %s по «%s»" % (what, ds_id, want or "республика"))
        ind["level"] = level
        return ind
    for p in pts[-SERIES_MAX:]:
        ind["series"].append({"period": p["period"], "value": p["value"],
                              "src": _stat_src(src, ds_id, p["url"], p["period"], p["fetched_at"])})
    last = pts[-1]
    ind.update({"value": last["value"], "period": last["period"], "unit": last["unit"] or d.get("unit"),
                "level": level, "src": ind["series"][-1]["src"], "limits": list(d.get("limits") or [])})
    return ind


def _penetration(con, key: str, src: _Sources, notes: list) -> dict:
    """Премии региона за полный год (НАПП, все классы) / ВРП того же года × 100."""
    want = key or "total"
    ind = {"name": "Проникновение страхования (премии к ВРП)", "unit": "% ВРП", "value": None,
           "period": None, "level": "республика" if want == "total" else "регион", "series": [],
           "src": None,
           "formula": "премии за полный год (НАПП, млн сум) / 1 000 / ВРП того же года (stat.uz, млрд сум) × 100",
           "note": "премии региона — по всем классам вместе: разреза «регион × класс» в отчётах НАПП нет"}
    prem = _rows(con, "SELECT report_date, premiums_ytd, source_file FROM market_stats "
                      "WHERE row_key=? AND substr(report_date,6)='01-01' ORDER BY report_date", want)
    for p in prem:
        year = str(int(p["report_date"][:4]) - 1)
        g = _rows(con, "SELECT value, url, fetched_at FROM stat_series WHERE dataset_id='grp' AND region=? "
                       "AND period=? AND value IS NOT NULL", want, year)
        if not g or not p["premiums_ytd"] or not g[0]["value"]:
            notes.append("проникновение за %s не считается: нет %s" % (
                year, "ВРП" if not g else "премий"))
            continue
        v = p["premiums_ytd"] / 1000.0 / g[0]["value"] * 100
        ind["series"].append({"period": year, "value": round(v, 4),
                              "premiums_mln": _r(p["premiums_ytd"]), "grp_bln": g[0]["value"],
                              "src": [_napp_src(src, p["report_date"], p["source_file"]),
                                      _stat_src(src, "grp", g[0]["url"], year, g[0]["fetched_at"])]})
    if ind["series"]:
        last = ind["series"][-1]
        ind.update({"value": last["value"], "period": last["period"], "src": last["src"]})
    else:
        notes.append("проникновение не считается: нет пары «премии НАПП за полный год + ВРП» по «%s»" % want)
    return ind


def _region(con, region: Optional[str], src: _Sources, notes: list) -> dict:
    key, name = resolve_region(region) if region else (None, None)
    if region and not key:
        notes.append("регион «%s» не опознан — показана республика" % region)
    if not key:
        key, name = "total", REPUBLIC[0]
    out = {"requested": region, "key": key, "name": name,
           "level": "республика" if key == "total" else "регион", "indicators": {}}
    for ind_id, ds_id, what in (("population", "population", "население"),
                                ("grp", "grp", "ВРП"),
                                ("grp_per_capita", "grp_per_capita", "ВРП на душу"),
                                ("house_price_index", "house_price_index", "индекс цен на жильё"),
                                ("construction_price_index", "construction_price_index_y",
                                 "индекс цен на строительство")):
        out["indicators"][ind_id] = _stat_indicator(con, ds_id, key, src, notes, what)
    out["indicators"]["house_price_index"]["base"] = "к предыдущему кварталу, %"
    out["indicators"]["construction_price_index"]["base"] = "к декабрю предыдущего года, %"
    out["indicators"]["population"]["base"] = "на начало года"
    out["indicators"]["penetration"] = _penetration(con, key, src, notes)
    if key != "total":
        notes.append("индекс цен на строительство публикуется только по республике (level='республика')")
    return out


# --------------------------------------------------------------------------- #
# Публичные функции
# --------------------------------------------------------------------------- #

def picture(con, class_code: str, product_code: Optional[str] = None, region: Optional[str] = None,
            our_rate_pct: Optional[float] = None) -> dict:
    """Техническая картина рынка по классу и региону. Только чтение базы."""
    class_code = str(class_code or "").strip()
    if our_rate_pct is not None:
        our_rate_pct = float(our_rate_pct)
    src, notes = _Sources(), []
    market = _market(con, class_code, product_code, our_rate_pct, src, notes)
    reg = _region(con, region, src, notes)
    return {"class_code": class_code, "product_code": product_code, "region": reg, "market": market,
            "sources": src.items, "notes": list(dict.fromkeys(notes))}


def regions(con) -> list:
    """Регионы, по которым есть статистика (stat.uz или разрез регионов НАПП) — для выпадающего списка."""
    stat = {r["region"]: r["n"] for r in _rows(
        con, "SELECT region, COUNT(DISTINCT dataset_id) n FROM stat_series "
             "WHERE value IS NOT NULL AND region<>'' GROUP BY region")}
    napp = {r["row_key"] for r in _rows(
        con, "SELECT DISTINCT row_key FROM market_stats WHERE row_key LIKE 'region:%' OR row_key='total'")}
    out = []
    for name, key, soato, _al in [REPUBLIC] + REGION_TABLE:
        if key in stat or key in napp:
            out.append({"key": key, "name": name, "soato": soato,
                        "level": "республика" if key == "total" else "регион",
                        "stat_datasets": stat.get(key, 0), "napp": key in napp})
    return out
