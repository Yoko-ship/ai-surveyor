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

from . import act_texts as tx
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
# Комплексный продукт → пакет классов НАПП (01.10.2026)
# --------------------------------------------------------------------------- #
# Для продукта из нескольких классов (product_classes) рыночная ставка и убыточность берутся по строке
# пакета листа 1.4 «Ikki va undan ortiq klasslar bo'yicha sug'urta» с тем же набором классов (0312 = 3 + 14 →
# «3,14 klasslar» = cls3_14). Нет точного пакета — ближайший: пакет, в котором есть все классы продукта
# (меньше лишних классов; при равенстве — больший объём премий на последний срез). Нет и такого — строка
# класса (CLASS_ROWS), с пометкой. Продукт из одного класса — как раньше: строка класса (для 8 и 9 — пакет
# «8, 9», одиночная строка класса на порядки меньше и показывается в alternatives).
# Подклассы проекта 13з и 16у в отчёте НАПП входят в свои классы (13 и 16) — для сравнения с пакетами
# берётся номер класса, это пометка в note.

def _cls_num(code: str) -> str:
    return "".join(ch for ch in str(code) if ch.isdigit())


def _pack_ok(con, row_key: str) -> Optional[dict]:
    """Последняя точка строки, если по ней считается ставка (есть премии и обязательства)."""
    r = _rows(con, "SELECT report_date, premiums_ytd, liabilities FROM market_stats WHERE row_key=? "
                   "ORDER BY report_date DESC LIMIT 1", row_key)
    if not r or not r[0]["premiums_ytd"] or not r[0]["liabilities"]:
        return None
    return r[0]


def product_row(con, product_code: Optional[str], class_code: Optional[str] = None) -> Optional[dict]:
    """
    Строка НАПП для продукта из нескольких классов. None — продукт не задан, не найден или из одного класса
    (тогда работает CLASS_ROWS). Иначе {"row_key", "how": exact | nearest | class, "product_code",
    "product_classes", "pack_classes", "missing_pack", "subclass_note", "alternatives": [...]}.
    """
    if not product_code:
        return None
    pcs = [r["class_code"] for r in _rows(
        con, "SELECT class_code FROM product_classes WHERE product_code=? ORDER BY part_no", product_code)]
    nums = sorted({_cls_num(c) for c in pcs if _cls_num(c)}, key=int)
    if len(nums) < 2:
        return None
    sub = [c for c in pcs if c != _cls_num(c)]
    out = {"product_code": product_code, "product_classes": list(dict.fromkeys(pcs)), "classes": nums,
           "subclass_note": ("подклассы %s в отчёте НАПП входят в классы %s" % (
               ", ".join(sub), ", ".join(_cls_num(c) for c in sub))) if sub else None,
           "row_key": None, "how": None, "pack_classes": [], "candidates": []}
    exact = "cls" + "_".join(nums)
    if _pack_ok(con, exact):
        out.update(row_key=exact, how="exact", pack_classes=list(nums))
        return out
    want = set(nums)
    cands = []
    for r in _rows(con, "SELECT DISTINCT row_key FROM market_stats WHERE row_key GLOB 'cls*_*'"):
        k = r["row_key"]
        ks = k[3:].split("_")
        if not want.issubset(ks):
            continue
        last = _pack_ok(con, k)
        if last:
            cands.append((len(ks) - len(want), -(last["premiums_ytd"] or 0), k, ks))
    cands.sort()
    out["candidates"] = [c[2] for c in cands]
    if cands:
        out.update(row_key=cands[0][2], how="nearest", pack_classes=cands[0][3])
        return out
    first = class_code if class_code and _cls_num(class_code) in want else (pcs[0] if pcs else None)
    out.update(row_key=CLASS_ROWS.get(str(first)) if first else None, how="class", class_used=first)
    return out


def pack_note(pc: Optional[dict]) -> Optional[str]:
    """Пометка о выборе строки НАПП для продукта (русский, для notes и mapping_note)."""
    if not pc:
        return None
    cl = ",".join(pc["classes"])
    pk = ",".join(pc.get("pack_classes") or [])
    if pc["how"] == "exact":
        t = "пакет НАПП %s — тот же набор классов, что у продукта %s" % (pk, pc["product_code"])
    elif pc["how"] == "nearest":
        t = "пакет НАПП %s — ближайший к составу продукта %s (%s): пакета ровно с этими классами в отчёте нет" % (
            pk, pc["product_code"], cl)
    else:
        t = ("пакета НАПП с классами %s (продукт %s) в отчёте нет — взята строка класса %s"
             % (cl, pc["product_code"], pc.get("class_used") or "—"))
    if pc.get("subclass_note"):
        t += "; " + pc["subclass_note"]
    return t


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
    pc = product_row(con, product_code, class_code) if product_code else None
    out["pack_choice"] = None
    if pc and pc.get("row_key"):
        out["pack_choice"] = {k: pc.get(k) for k in ("how", "product_code", "product_classes", "classes",
                                                     "pack_classes", "class_used", "candidates", "subclass_note")}
        out["pack_choice"]["note"] = pack_note(pc)
        notes.append(pack_note(pc))
        if pc["how"] in ("exact", "nearest"):
            notes[:] = ["в отчётах НАПП нет разреза по продуктам — рынок показан по пакету классов продукта "
                        "(лист 1.4, «Ikki va undan ortiq klasslar bo'yicha sug'urta»)"
                        if n.startswith("в отчётах НАПП нет разреза по продуктам") else n for n in notes]
        row_key = pc["row_key"]
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
    if out.get("pack_choice"):
        out["mapping_note"] = out["pack_choice"]["note"]
    elif "_" in row_key:
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

    # одиночные строки классов продукта рядом с пакетом (правило проекта № 5 — ставка проверяется по каждому
    # классу): ставка и убыточность последнего среза и полного года строки «N-klass» каждого класса продукта
    out["class_rows"] = []
    if (out.get("pack_choice") or {}).get("how") in ("exact", "nearest"):
        for n in out["pack_choice"].get("classes") or []:
            c = _row_block(con, "cls" + n, src)
            if not c:
                out["class_rows"].append({"class_code": n, "row_key": "cls" + n, "available": False})
                continue
            cl, cfy = c["last"], c["full_year"]
            out["class_rows"].append({
                "class_code": n, "row_key": "cls" + n, "available": True, "row_name": c["row_name"],
                "date": cl["report_date"], "period": _label(cl["report_date"]),
                "rate_pct": _r(_annual_rate(cl)), "loss_ratio_pct": _r(_loss_ratio(cl)),
                "premiums": _r(cl["premiums_ytd"]),
                "full_year_period": _label(cfy["report_date"]) if cfy else None,
                "rate_full_year_pct": _r(_annual_rate(cfy)) if cfy else None,
                "loss_ratio_full_year_pct": _r(_loss_ratio(cfy)) if cfy else None,
                "src": _napp_src(src, cl["report_date"], cl["source_file"])})

    # одиночные строки класса и родственные строки
    alts = list(ALTERNATIVES.get(class_code, []))
    if out.get("pack_choice") and CLASS_ROWS.get(class_code) and CLASS_ROWS[class_code] != row_key:
        alts = [CLASS_ROWS[class_code]] + [a for a in alts if a != CLASS_ROWS[class_code]]
    for alt in alts:
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
# Претензии и подразделения (НАПП, таблицы napp_claims / napp_branches), 01.10.2026
# --------------------------------------------------------------------------- #
# Формулы:
#   частота претензий на 1 000 договоров = поступило претензий с начала года / действующих договоров на дату × 1 000
#     (лист 3.5 / лист 3.4 по регионам, 2.10 / 2.7 по страховщикам; только общее страхование);
#   доля отказов, % = отказано / поступило × 100 (как «umumiyga nisbatan % da» в отчёте);
#   средняя выплата, сум = выплаты общего страхования (лист 3.2 или 2.5, млн сум) × 1 000 000 / оплачено претензий;
#   убыточность подразделения, % = выплаты / премии × 100 (листы 2.13 / 2.12); средняя премия = премии / договоры.
# Срез — последний срез рынка (market_stats, строка total). Нет листа за этот срез — показатель пропускается
# с пометкой (последний доступный ранее срез не подставляется: периоды разные).
# Оговорки к претензиям — на трёх языках в act_texts.CLAIMS_CAVEATS (замечание контролёра 01.10.2026); здесь —
# русский список для API (/market/claims, risk_stats). Доля города Ташкента в претензиях страны считается по срезу.
CAPITAL_KEY = "region:TOSHKENT SHAHRI"


def claims_caveats(lang: str = "ru", capital_share_pct: Optional[float] = None) -> list:
    """Оговорки к строкам претензий НАПП на языке lang; capital_share_pct — доля города Ташкента в претензиях
    страны на срез (None — без числа)."""
    out = []
    for code, tr in tx.CLAIMS_CAVEATS.items():
        if code == "capital":
            tr = tx.CLAIMS_CAVEATS["capital"] if capital_share_pct is not None else tx.CLAIMS_CAVEAT_CAPITAL_NA
        text = tr.get(lang) or tr["ru"]
        if "{share}" in text:
            text = text.replace("{share}", tx.pct_fixed(capital_share_pct, lang, 0))
        out.append(text)
    return out


CLAIMS_CAVEATS = claims_caveats("ru")


def capital_share(con, d: Optional[str]) -> Optional[float]:
    """Доля города Ташкента в претензиях общего страхования страны на срез d, % (лист 3.5)."""
    if not d or not _has_table(con, "napp_claims"):
        return None
    tot = _claims_row(con, d, "region", "total")
    cap = _claims_row(con, d, "region", CAPITAL_KEY)
    if not tot or not cap or not tot.get("claims_received") or cap.get("claims_received") is None:
        return None
    return _r(cap["claims_received"] / tot["claims_received"] * 100, 1)
CLAIM_SHEET = {"region": ("3.5", "3.4", "3.2"), "company": ("2.10", "2.7", "2.5")}


def latest_slice(con) -> Optional[str]:
    """Последний срез рынка (строка total листа 1.4)."""
    r = _rows(con, "SELECT MAX(report_date) d FROM market_stats WHERE row_key='total'")
    return r[0]["d"] if r and r[0]["d"] else None


def _has_table(con, name: str) -> bool:
    return bool(_rows(con, "SELECT name FROM sqlite_master WHERE type='table' AND name=?", name))


def claims_metrics(rec: Optional[dict]) -> Optional[dict]:
    """Показатели одной строки napp_claims."""
    if not rec:
        return None
    rcv, paid, ref = rec.get("claims_received"), rec.get("claims_paid"), rec.get("claims_refused")
    act, pay = rec.get("contracts_active"), rec.get("payouts_mln")
    return {"received": rcv, "paid": paid, "refused": ref, "unsettled": rec.get("claims_unsettled"),
            "contracts_active": act, "contracts_new": rec.get("contracts_new"), "payouts_mln": pay,
            "per_1000": _r(rcv / act * 1000, 4) if rcv is not None and act else None,
            "refused_pct": _r(ref / rcv * 100, 3) if ref is not None and rcv else None,
            "avg_payout": round(pay * 1e6 / paid) if pay is not None and paid else None,
            "source_file": rec.get("source_file"), "loaded_at": rec.get("loaded_at")}


def _claims_row(con, d: str, scope: str, key: str) -> Optional[dict]:
    r = _rows(con, "SELECT * FROM napp_claims WHERE report_date=? AND scope=? AND key=?", d, scope, key)
    return r[0] if r else None


def napp_source(sheets: str, d: Optional[str], source_file: Optional[str] = None) -> dict:
    """Источник для плашки .srcbar: страница НАПП, листы отчёта, срез."""
    title = "%s, листы %s%s" % (NAPP_TITLE, sheets, (" (%s)" % source_file.replace("_", " ").strip())
                                if source_file else "")
    return {"title": title, "url": NAPP_PAGE, "domain": urlsplit(NAPP_PAGE).netloc, "as_of": d, "sheets": sheets}


def region_claims(con, region: Optional[str]) -> dict:
    """Претензии в регионе против республики на последний срез рынка (листы 3.5, 3.4, 3.2)."""
    key, name = resolve_region(region) if region else (None, None)
    d = latest_slice(con)
    out = {"available": False, "reason": None, "region_key": key, "region_name": name, "date": d,
           "period": _label(d) if d else None, "months": MONTHS.get((d or "")[5:]), "region": None,
           "republic": None, "ratio": None, "diff_pct": None, "source": None, "caveats": list(CLAIMS_CAVEATS),
           "capital_share_pct": None,
           "formula": "претензии с начала года / действующие договоры на дату × 1 000 (общее страхование)"}
    if not _has_table(con, "napp_claims") or not d:
        out["reason"] = "no_table"
        return out
    out["capital_share_pct"] = capital_share(con, d)
    out["caveats"] = claims_caveats("ru", out["capital_share_pct"])
    rep = claims_metrics(_claims_row(con, d, "region", "total"))
    reg = claims_metrics(_claims_row(con, d, "region", key)) if key and key != "total" else None
    if rep is None:
        out["reason"] = "no_sheet"             # листа 3.5 за этот срез нет — показатель пропускается
        out["note"] = "в отчёте НАПП за срез %s листа 3.5 (претензии по регионам) нет — показатель пропущен" % d
        return out
    out["republic"] = rep
    out["source"] = napp_source("3.5, 3.4, 3.2", d, rep.get("source_file"))
    if not key or key == "total":
        out["reason"] = "region_unknown" if region else "no_region"
        return out
    if reg is None:
        out["reason"] = "no_region_row"
        out["note"] = "в листе 3.5 за срез %s нет строки региона «%s» — показатель пропущен" % (d, name)
        return out
    out["region"] = reg
    if reg["per_1000"] is None or not rep["per_1000"]:
        out["reason"] = "no_contracts"
        out["note"] = "нет числа действующих договоров (лист 3.4) за срез %s — частота не считается" % d
        return out
    out["ratio"] = _r(reg["per_1000"] / rep["per_1000"], 4)
    out["diff_pct"] = _r((out["ratio"] - 1) * 100, 1)
    out["available"] = True
    return out


def company_claims(con, company_key: str = INSON_ROW) -> dict:
    """Претензии по рынку в целом и по страховщику (листы 2.10, 2.7, 2.5) на последний срез рынка."""
    d = latest_slice(con)
    out = {"available": False, "reason": None, "date": d, "period": _label(d) if d else None,
           "months": MONTHS.get((d or "")[5:]), "market": None, "company": None, "company_key": company_key,
           "company_name": None, "source": None, "caveats": list(CLAIMS_CAVEATS), "capital_share_pct": None}
    if not _has_table(con, "napp_claims") or not d:
        out["reason"] = "no_table"
        return out
    out["capital_share_pct"] = capital_share(con, d)
    out["caveats"] = claims_caveats("ru", out["capital_share_pct"])
    mrec = _claims_row(con, d, "company", "total")
    if mrec is None:
        out["reason"] = "no_sheet"
        out["note"] = "в отчёте НАПП за срез %s листа 2.10 (претензии по страховщикам) нет — показатель пропущен" % d
        return out
    out["market"] = claims_metrics(mrec)
    out["source"] = napp_source("2.10, 2.7, 2.5", d, mrec.get("source_file"))
    crec = _claims_row(con, d, "company", company_key)
    if crec is None:
        out["reason"] = "no_company"
        out["note"] = "в листе 2.10 за срез %s нет строки страховщика %s" % (d, company_key)
        return out
    out["company"] = claims_metrics(crec)
    out["company_name"] = crec.get("name")
    out["available"] = True
    return out


def branch_metrics(rec: Optional[dict]) -> Optional[dict]:
    if not rec:
        return None
    p, y, n = rec.get("premiums_mln"), rec.get("payouts_mln"), rec.get("contracts")
    return {"premiums_mln": p, "payouts_mln": y, "contracts": n,
            "loss_ratio_pct": _r(y / p * 100, 3) if y is not None and p else None,
            "avg_premium": round(p * 1e6 / n) if p is not None and n else None}


def company_label(con, company_key: str) -> str:
    """Название страховщика для пометок: из отчёта (napp_branches / napp_claims / market_stats) по company_key;
    нет строк — ключ без «company:» и формы собственности («INSON AJ» → «INSON»)."""
    for sql in ("SELECT company_name n FROM napp_branches WHERE company_key=? ORDER BY report_date DESC LIMIT 1",
                "SELECT name n FROM napp_claims WHERE key=? ORDER BY report_date DESC LIMIT 1",
                "SELECT row_name n FROM market_stats WHERE row_key=? ORDER BY report_date DESC LIMIT 1"):
        try:
            r = _rows(con, sql, company_key)
        except Exception:                       # таблицы ещё нет (база до первой загрузки)
            r = []
        if r and r[0]["n"]:
            n = str(r[0]["n"]).replace('"', "").replace("«", "").replace("»", "").strip()
            return " ".join(w for w in n.split() if w.upper() not in ("AJ", "QK", "AJ.", "MCHJ")) or n
    k = company_key.split(":", 1)[-1]
    return " ".join(w for w in k.split() if w.upper() not in ("AJ", "QK", "MCHJ")) or k


def branches(con, region: Optional[str], company_key: str = INSON_ROW) -> dict:
    """Подразделения страховщика в регионе против среднего по компании (листы 2.12–2.14, последний срез)."""
    key, name = resolve_region(region) if region else (None, None)
    d = latest_slice(con)
    out = {"available": False, "reason": None, "date": d, "period": _label(d) if d else None,
           "region_key": key, "region_name": name, "company_key": company_key, "company_name": None,
           "region": None, "company": None, "market_region": None, "source": None,
           "formula": "убыточность = выплаты / премии × 100 (листы 2.13 / 2.12); средняя премия = премии / договоры "
                      "(лист 2.14)",
           "caveats": ["в отчёте НАПП подразделения даны по регионам (все подразделения страховщика в регионе "
                       "вместе), отдельных филиалов нет",
                       "договоры — действующие на дату, премии и выплаты — с начала года: средняя премия — "
                       "ориентир, а не премия по договору"]}
    if not _has_table(con, "napp_branches") or not d:
        out["reason"] = "no_table"
        return out
    tot = _rows(con, "SELECT * FROM napp_branches WHERE report_date=? AND company_key=? AND region_key='total'",
                d, company_key)
    if not _rows(con, "SELECT 1 FROM napp_branches WHERE report_date=? LIMIT 1", d):
        out["reason"] = "no_sheet"
        out["note"] = "в отчёте НАПП за срез %s листов 2.12–2.14 (подразделения) нет — показатель пропущен" % d
        return out
    if not tot:
        out["reason"] = "not_listed"
        out["company_label"] = company_label(con, company_key)
        out["note"] = "в отчёте НАПП подразделения %s не выделены" % out["company_label"]
        return out
    out["company"] = branch_metrics(tot[0])
    out["company_name"] = tot[0]["company_name"]
    out["company_label"] = company_label(con, company_key)
    out["source"] = napp_source("2.12, 2.13, 2.14", d, tot[0].get("source_file"))
    if not key or key == "total":
        out["reason"] = "region_unknown" if region else "no_region"
        return out
    rr = _rows(con, "SELECT * FROM napp_branches WHERE report_date=? AND company_key=? AND region_key=?",
               d, company_key, key)
    reg = branch_metrics(rr[0]) if rr else None
    mr = _rows(con, "SELECT * FROM napp_branches WHERE report_date=? AND company_key='total' AND region_key=?", d, key)
    out["market_region"] = branch_metrics(mr[0]) if mr else None
    if not reg or not reg["premiums_mln"]:
        out["reason"] = "not_listed"
        out["note"] = "в отчёте НАПП подразделения %s в регионе «%s» не выделены (премий по региону нет)" % (
            out["company_label"], name)
        out["region"] = reg
        return out
    out["region"] = reg
    out["available"] = True
    return out


def branches_table(con, date: Optional[str] = None, company_key: str = INSON_ROW) -> dict:
    """Таблица подразделений страховщика по регионам на срез (для /stats)."""
    if not _has_table(con, "napp_branches"):
        return {"dates": [], "date": None, "companies": [], "company_key": company_key, "rows": [], "total": None,
                "market": None, "source": None}
    dates = [r["report_date"] for r in _rows(con, "SELECT DISTINCT report_date FROM napp_branches ORDER BY 1")]
    # срез без листов 2.12–2.14 — ближайший более ранний (на странице это подписано), без даты — последний
    earlier = [x for x in dates if date and x <= date]
    d = date if date in dates else (earlier[-1] if earlier else (dates[-1] if dates else None))
    comps = _rows(con, "SELECT company_key AS key, company_name AS name, sphere, premiums_mln FROM napp_branches "
                       "WHERE report_date=? AND region_key='total' AND company_key<>'total' "
                       "ORDER BY premiums_mln DESC", d) if d else []
    rows = _rows(con, "SELECT * FROM napp_branches WHERE report_date=? AND company_key=? AND region_key<>'total' "
                      "ORDER BY premiums_mln DESC", d, company_key) if d else []
    tot = _rows(con, "SELECT * FROM napp_branches WHERE report_date=? AND company_key=? AND region_key='total'",
                d, company_key) if d else []
    mkt = {r["region_key"]: r for r in _rows(con, "SELECT * FROM napp_branches WHERE report_date=? AND "
                                                    "company_key='total'", d)} if d else {}
    out_rows = []
    for r in rows:
        m = branch_metrics(r)
        mr = branch_metrics(mkt.get(r["region_key"]))
        m.update(region_key=r["region_key"], region_name=r["region_name"],
                 share_of_region_pct=_r(r["premiums_mln"] / mr["premiums_mln"] * 100, 3)
                 if mr and mr["premiums_mln"] and r["premiums_mln"] is not None else None,
                 market_loss_ratio_pct=mr["loss_ratio_pct"] if mr else None)
        out_rows.append(m)
    src = tot[0]["source_file"] if tot else None
    return {"dates": dates, "date": d, "period": _label(d) if d else None, "companies": comps,
            "company_key": company_key, "company_name": tot[0]["company_name"] if tot else None,
            "rows": out_rows, "total": branch_metrics(tot[0]) if tot else None,
            "market": branch_metrics(mkt.get("total")), "source": napp_source("2.12, 2.13, 2.14", d, src) if d else None,
            "note": None if tot else "в отчёте НАПП подразделения этого страховщика на срез не выделены"}


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
