"""
База знаний о внутреннем страховом рынке Узбекистана — только из открытых отчётов НАПП.

Что делает:
  1. Снимает копию базы (рабочая база только читается) и пересобирает на копии рыночную статистику
     из разборов data/parsed (tools/market_stats.build): так в копии появляются претензии (napp_claims),
     подразделения (napp_branches) и пометки (market_stats_notes), даже если рабочая база ещё не обновлена.
  2. Дочитывает из разборов листы, которых нет в базе: 1.1 (участники рынка), 1.4 (обязательные виды
     «перевозчик», «опасные объекты», «прочие»), 1.5 (договоры по классам), 1.6 (перестрахование за рубеж),
     2.1 (нетто-премии и переданное в перестрахование по страховщикам), 2.9 (число подразделений).
  3. Пишет заметки docs/Знания/Рынок/*.md (по-русски, с цифрами и датами; в конце каждой — источники)
     и машиночитаемые факты docs/market_facts.json.

Правила: все числа — из данных; чего нет — «нет в открытых данных». Ничего о компаниях сверх отчёта НАПП
(владельцы, история и т.п.) не пишется. Суммы — млн сум с разделителями тысяч, проценты — с одним знаком,
ставки — с тремя знаками. Рост год к году — только между сопоставимыми периодами (одинаковая дата среза
год назад: I полугодие 2026 к I полугодию 2025, 2025 год к 2024 году): премии и выплаты в отчёте НАПП —
нарастающим итогом с начала года (ytd), поэтому полугодие с годом не сравнивается.

Запуск (из корня проекта):
    sandbox/.venv/Scripts/python.exe tools/market_knowledge.py
    ... --db <путь к базе>      другая база-источник (по умолчанию — база сервера, app/db.DB_PATH)
    ... --no-rebuild            не пересобирать статистику на копии, читать как есть
    ... --out <папка> --json <файл>

На сервере то же делает app/market_knowledge.py (без пересборки статистики, по сигналу нового среза НАПП).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import db as appdb  # noqa: E402
from app import market_picture as mp  # noqa: E402
import tools.market_stats as ms  # noqa: E402

OUT_DIR = ROOT / "docs" / "Знания" / "Рынок"
JSON_PATH = ROOT / "docs" / "market_facts.json"
LIBRARY_NAPP = ROOT / "library" / "03_Рынок_НАПП"
LAW_DIR = "library/01_Законодательство/02_Акты_регуляторов"
NO_DATA = "нет в открытых данных"
DEV = "вывод разработчика"
MAX_CLASS = 18

# --------------------------------------------------------------------------- #
# Форматирование
# --------------------------------------------------------------------------- #


def _grp(s: str) -> str:
    return s.replace(",", " ").replace(".", ",")


def fmt_mln(x) -> str:
    """Сумма, млн сум: целые с разделителями тысяч; меньше 10 — с одним знаком."""
    if x is None:
        return NO_DATA
    if x != 0 and abs(x) < 10:
        return _grp(f"{x:,.1f}")
    return _grp(f"{x:,.0f}")


def fmt_int(x) -> str:
    return NO_DATA if x is None else _grp(f"{x:,.0f}")


def fmt_pct(x, n: int = 1) -> str:
    return NO_DATA if x is None else _grp(f"{x:,.{n}f}") + " %"


def fmt_yoy(x) -> str:
    if x is None:
        return NO_DATA
    return ("+" if x > 0 else "") + fmt_pct(x)


def fmt_rate(x) -> str:
    """Ставка, % — три знака."""
    return fmt_pct(x, 3)


def r1(x):
    return None if x is None else round(x, 1)


def r3(x):
    return None if x is None else round(x, 3)


def date_ru(d: str) -> str:
    return f"{d[8:10]}.{d[5:7]}.{d[:4]}"


def period_label(d: str) -> str:
    """Дата среза → период нарастающего итога: 2026-07-01 → «I полугодие 2026», 2026-01-01 → «2025 год»."""
    md, y = d[5:], int(d[:4])
    if md in ("03-31", "04-01"):
        return f"I квартал {y}"
    if md == "07-01":
        return f"I полугодие {y}"
    if md == "10-01":
        return f"9 месяцев {y}"
    if md == "01-01":
        return f"{y - 1} год"
    return d


def period_to(d: str) -> str:
    """«к …»: 2025-07-01 → «к I полугодию 2025», 2025-01-01 → «к 2024 году»."""
    md, y = d[5:], int(d[:4])
    if md in ("03-31", "04-01"):
        return f"к I кварталу {y}"
    if md == "07-01":
        return f"к I полугодию {y}"
    if md == "10-01":
        return f"к 9 месяцам {y}"
    if md == "01-01":
        return f"к {y - 1} году"
    return f"к {d}"


def lr_cell(prem, pay) -> str:
    """Убыточность для таблицы: премий нет — так и пишем, а не «нет данных»."""
    if prem == 0:
        return "премий нет"
    return fmt_pct(loss_ratio(prem, pay))


def xlsx_name(folder: str | None) -> str | None:
    """Папка разбора → имя файла отчёта в library/03_Рынок_НАПП."""
    if not folder:
        return None
    return folder.replace("_", " ").strip() + ".xlsx"


def year_ago(d: str) -> str:
    """Сопоставимая дата год назад — тот же месяц и день."""
    return f"{int(d[:4]) - 1}{d[4:]}"


def loss_ratio(prem, pay):
    if not prem or pay is None:
        return None
    return pay / prem * 100


def growth(cur, base):
    if cur is None or base is None or base == 0:
        return None
    return (cur / base - 1) * 100


# --------------------------------------------------------------------------- #
# Источники и факты
# --------------------------------------------------------------------------- #


class Sources:
    """Какие отчёты и срезы использованы в заметке — для строки «Источник: …» в конце."""

    def __init__(self):
        self.items: dict[str, set] = {}

    def add(self, folder: str | None, date: str | None):
        f = xlsx_name(folder)
        if f and date:
            self.items.setdefault(f, set()).add(date)

    def footer(self) -> str:
        if not self.items:
            return f"Источник: отчёт НАПП — {NO_DATA}."
        lines = []
        for f in sorted(self.items, key=lambda x: max(self.items[x]), reverse=True):
            ds = ", ".join(date_ru(d) for d in sorted(self.items[f]))
            lines.append(f"Источник: отчёт НАПП «{f}», срез {ds}")
        return "\n".join(lines) + "\n"


class Facts:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, topic, date, metric, value, unit, entity, *, rank=None, share_pct=None, loss_ratio_pct=None,
            yoy_pct=None, yoy_base=None, source_folder=None, note=None, digits=1):
        self.items.append({
            "topic": topic, "period": period_label(date) if date else None, "metric": metric,
            "value": None if value is None else round(value, digits), "unit": unit, "entity": entity,
            "rank": rank, "share_pct": r1(share_pct), "loss_ratio_pct": r1(loss_ratio_pct),
            "yoy_pct": r1(yoy_pct), "yoy_base_date": yoy_base if yoy_pct is not None else None,
            "source_file": xlsx_name(source_folder), "source_date": date, "note": note})


# --------------------------------------------------------------------------- #
# Чтение базы
# --------------------------------------------------------------------------- #


def _rows(con, sql, *args):
    cur = con.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _has_table(con, name):
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


class Market:
    """market_stats в памяти: (дата, ключ) → строка."""

    def __init__(self, con):
        self.rows = {}
        for r in _rows(con, "SELECT report_date, row_key, row_name, premiums_ytd, payouts_ytd, liabilities, "
                            "source_file FROM market_stats"):
            self.rows[(r["report_date"], r["row_key"])] = r
        self.dates = sorted({d for d, _ in self.rows})
        self.last = self.dates[-1] if self.dates else None
        fy = [d for d in self.dates if d[5:] == "01-01"]
        self.last_fy = fy[-1] if fy else None
        self.notes = {}
        if _has_table(con, "market_stats_notes"):
            for r in _rows(con, "SELECT report_date, row_key, note FROM market_stats_notes"):
                self.notes[(r["report_date"], r["row_key"])] = r["note"]

    def get(self, d, k):
        return self.rows.get((d, k))

    def val(self, d, k, f="premiums_ytd"):
        r = self.get(d, k)
        return None if r is None else r[f]

    def keys(self, d, prefix):
        return [k for (dd, k) in self.rows if dd == d and k.startswith(prefix)]

    def base_date(self, d):
        b = year_ago(d)
        return b if b in self.dates else None

    def yoy_cell(self, d, k, f="premiums_ytd", flag=""):
        """Рост г/г для таблицы с причиной, если не считается."""
        b = self.base_date(d)
        if not b or self.get(b, k) is None:
            return NO_DATA
        base = self.val(b, k, f)
        if base == 0:
            return "год назад 0"
        g = growth(self.val(d, k, f), base)
        return fmt_yoy(g) + flag

    def yoy(self, d, k, f="premiums_ytd"):
        """(рост, %, дата базы) — только к той же дате год назад; иначе (None, None)."""
        b = self.base_date(d)
        if not b:
            return None, None
        g = growth(self.val(d, k, f), self.val(b, k, f))
        return (g, b) if g is not None else (None, None)


# --------------------------------------------------------------------------- #
# Дочитывание листов из разборов (того, чего нет в базе)
# --------------------------------------------------------------------------- #


def _csv(path: Path):
    return list(csv.reader(open(path, encoding="utf-8-sig"))) if path.exists() else None


def _num(s):
    return ms.num(s)


def read_structure(folder: Path):
    """Лист 1.1 → {дата: {показатель: значение}}."""
    rows = _csv(folder / "1.1.csv")
    if not rows:
        return {}
    hi, cols = ms.find_header(rows)
    if hi is None:
        return {}
    dates = [ms.DATE_RE.match(rows[hi][j]).group(1) for j in cols[:2]]
    pats = [("^SUG'URTA TASHKILOTLARI SONI", "insurers"), ("^SUG'URTA TASHKILOTLARINING UMUMIY USTAV", "capital"),
            ("^SUG'URTA BROKERLARI", "brokers"), ("^AKTUARIY", "actuaries"), ("^SUG'URTA AGENTLARI", "agents"),
            ("^TO'LOVLARNI KAFOLATLASH", "guarantee_fund")]
    out, prev = {}, None
    for r in rows[hi + 1:]:
        if len(r) < 3 or not r[0].strip():
            continue
        n = ms.norm_name(r[0])
        key = next((k for p, k in pats if re.match(p, n)), None)
        if not key and n.startswith("SHU JUMLADAN"):
            key = {"insurers": "life_insurers", "agents": "agents_legal"}.get(prev)
        if key:
            prev = key
            for j, d in zip(cols[:2], dates):
                v = _num(r[j])
                if v is not None:
                    out.setdefault(d, {})[key] = v
    return out


MAND_EXTRA = [(r"^tashuvchilarning", "carrier", "ОСГО перевозчика (гражданская ответственность перевозчика)"),
              (r"^xavfli ishlab chiqarish", "hazardous", "ГО владельцев опасных производственных объектов"),
              (r"^boshqalar", "mandatory_other", "Прочие обязательные виды (boshqalar)")]


def read_mandatory_extra(folder: Path):
    """Лист 1.4, блок «Majburiy»: строки, которые market_stats не загружает → {(дата, ключ): {...}}."""
    rows = _csv(folder / "1.4.csv")
    if not rows:
        return {}
    dates = [rows[3][1][:10], rows[3][2][:10]]
    out, block = {}, None
    for r in rows[5:]:
        if len(r) < 9 or not r[0].strip():
            continue
        block = ms._block_of(r[0], block)
        if block != "mandatory":
            continue
        n = r[0].strip().strip('"')
        for pat, key, ru in MAND_EXTRA:
            if re.match(pat, n, re.I):
                for i, d in enumerate(dates):
                    out[(d, key)] = {"row_name": ru, "premiums_ytd": _num(r[1 + i]), "payouts_ytd": _num(r[4 + i]),
                                     "liabilities": _num(r[7 + i]), "source_file": folder.name}
    return out


def read_contracts_by_class(folder: Path):
    """Лист 1.5 → {(дата, ключ строки как в market_stats): (действующие, новые)}."""
    rows = _csv(folder / "1.5.csv")
    if not rows:
        return {}
    hi, cols = ms.find_header(rows)
    if hi is None:
        return {}
    kinds = rows[hi + 1]
    want = {}
    for j in cols:
        k = ms.norm_name(kinds[j] if j < len(kinds) else "")
        what = "active" if k.startswith("AMALDA") else ("new" if k.startswith("YANGI") else None)
        if what:
            want[j] = (ms.DATE_RE.match(rows[hi][j]).group(1), what)
    out, block = {}, None
    for r in rows[hi + 2:]:
        if not r or not r[0].strip():
            continue
        nb = ms._block_of(r[0], block)
        if nb != block:
            block = nb
        n = r[0].strip().strip('"')
        key = None
        if block == "life":
            continue
        if re.match(r"^Ikki va undan ortiq", n, re.I) and block == "general":
            key = "multi_general"
        elif block == "mandatory":
            key = next((k for p, k, _ in MAND_EXTRA if re.match(p, n, re.I)), None)
        if not key:
            key, _ = ms.row_key(r[0])
        if not key:
            continue
        for j, (d, what) in want.items():
            v = _num(r[j]) if j < len(r) else None
            if v is None:
                continue
            a, nw = out.get((d, key), (None, None))
            out[(d, key)] = (v, nw) if what == "active" else (a, v)
    return out


def read_reinsurance(folder: Path):
    """Лист 1.6, премии, общее страхование → {дата: {ceded, ceded_pct, accepted, accepted_pct}}."""
    rows = _csv(folder / "1.6.csv")
    if not rows:
        return {}
    hi, cols = ms.find_header(rows)
    if hi is None or len(cols) < 4:
        return {}
    d1, d2 = (ms.DATE_RE.match(rows[hi][cols[0]]).group(1), ms.DATE_RE.match(rows[hi][cols[2]]).group(1))
    out, sphere = {}, None
    for r in rows[hi + 2:]:
        if not r or not r[0].strip():
            continue
        n = ms.norm_name(r[0])
        if "MAJBURIYATLARI HAJMI" in n:
            break                                        # дальше — обязательства, не премии
        if n.startswith("UMUMIY SUG'URTA SOHASI"):
            sphere = "general"
            continue
        if n.startswith("HAYOT"):
            sphere = "life"
            continue
        if sphere != "general":
            continue
        what = "ceded" if "BERILGAN" in n else ("accepted" if "QABUL" in n else None)
        if not what:
            continue
        for d, jv, jp in ((d1, cols[0], cols[1]), (d2, cols[2], cols[3])):
            v, p = _num(r[jv]), _num(r[jp])
            out.setdefault(d, {})[what] = v
            out[d][what + "_pct"] = None if p is None else p * 100
    return out


def read_company_reins(folder: Path):
    """Лист 2.1 → {(дата, ключ компании): {net, ceded, accepted}} (млн сум)."""
    out = {}
    for cap, f in ((r"^SOF", "net"), (r"^QAYTA SUG'URTAGA BERILGAN", "ceded"), (r"^QAYTA SUG'URTA BO'YICHA QABUL", "accepted")):
        m = ms.read_matrix(folder / "2.1.csv", cap)
        if not m:
            continue
        for k, (_title, by_date) in m.items():
            key, _ = ms._out_key("company", k, None, ms.COMPANY_RENAMES)
            for d, v in by_date.items():
                out.setdefault((d, key), {})[f] = v
    return out


def read_divisions(folder: Path):
    """Лист 2.9 → {(дата, ключ компании): (всего, в городах, в сельской местности)}; ключ 'total' — рынок."""
    rows = _csv(folder / "2.9.csv")
    if not rows:
        return {}
    hi, cols = ms.find_header(rows)
    if hi is None:
        return {}
    by_date = {}
    for j in cols:                                   # три колонки на дату: всего, в городах, в сельской местности
        by_date.setdefault(ms.DATE_RE.match(rows[hi][j]).group(1), []).append(j)
    out = {}
    for r in rows[hi + 2:]:
        key, _title = ms._row_key_of(r)
        if not key:
            continue
        ck, _ = ms._out_key("company", key, None, ms.COMPANY_RENAMES)
        for d, b in by_date.items():
            if len(b) < 3:
                continue
            vals = tuple(_num(r[j]) if j < len(r) else None for j in b[:3])
            if vals[0] is not None:
                out[(d, ck)] = vals + (folder.name,)
    return out


def read_extras(parsed: Path):
    """Все дочитываемые листы по всем отчётам; общий срез двух отчётов берётся из более позднего."""
    ms_parsed = ms.PARSED
    ms.PARSED = parsed
    try:
        folders = ms.report_folders()
    finally:
        ms.PARSED = ms_parsed
    ex = {"structure": {}, "mand": {}, "contracts": {}, "reins": {}, "comp_reins": {}, "divisions": {},
          "structure_src": {}, "reins_src": {}, "comp_reins_src": {}, "contracts_src": {}}
    for f in folders:
        for d, v in read_structure(f).items():
            ex["structure"][d] = v
            ex["structure_src"][d] = f.name
        ex["mand"].update(read_mandatory_extra(f))
        for k, v in read_contracts_by_class(f).items():
            ex["contracts"][k] = v
            ex["contracts_src"][k] = f.name
        for d, v in read_reinsurance(f).items():
            ex["reins"][d] = v
            ex["reins_src"][d] = f.name
        for k, v in read_company_reins(f).items():
            ex["comp_reins"][k] = v
            ex["comp_reins_src"][k] = f.name
        ex["divisions"].update(read_divisions(f))
    ex["sheets"] = _sheet_list(folders[-1]) if folders else []
    return ex


def _sheet_list(folder: Path):
    """Перечень таблиц отчёта (лист «Jadvallar ro‘yxati»): [(код, название по-узбекски)]."""
    p = next(iter(folder.glob("Jadvallar*.csv")), None)
    rows = _csv(p) if p else None
    out = []
    for r in rows or []:
        if len(r) > 3 and re.match(r"^\d+\.\d+$", (r[2] or "").strip()):
            out.append((r[2].strip(), r[3].strip()))
    return out


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out) + "\n"


SHORT = ms.SHORT
CAVEATS = [
    "премии и выплаты — нарастающим итогом с начала года на дату среза (ytd); срез «01.01» — итог прошлого года;",
    "страховые обязательства (суммы ответственности) — на дату среза, а не за период;",
    "выплаты — оплаченные за период, а не произошедшие убытки; резервы (РНП, РЗУ, РПНУ) в отчёте не показаны, "
    "поэтому убыточность здесь — брутто и «кассовая»: выплаты / премии × 100;",
    "премии — брутто, до передачи в перестрахование (сумма «за вычетом принятых в перестрахование» — по сноске "
    "листа 1.4);",
    "числа страховых случаев по классам в отчётах НАПП нет — есть только претензии по страховщикам и регионам "
    "(листы 2.10 и 3.5) и число договоров по классам (лист 1.5);",
    "рост год к году считается только между одинаковыми периодами (I полугодие к I полугодию, год к году).",
]


def caveats_md():
    return "## Оговорки\n\n" + "\n".join("- " + c for c in CAVEATS) + "\n"


# --------------------------------------------------------------------------- #
# Сборка
# --------------------------------------------------------------------------- #


class Builder:
    def __init__(self, con, parsed: Path):
        self.con = con
        self.m = Market(con)
        self.ex = read_extras(parsed)
        self.facts = Facts()
        self.class_names = {r["code"]: r["name"] for r in _rows(con, "SELECT code, name FROM classes")}
        self.class_names.setdefault("18", "Медицинское страхование")
        self.class_group = {r["code"]: r["group_code"] for r in _rows(con, "SELECT code, group_code FROM classes")}
        self.gaps = mp._gaps(self.m.dates)

    # ---- общие куски ----
    def src_row(self, S, d, k):
        r = self.m.get(d, k)
        if r:
            S.add(r["source_file"], d)
        return r

    def head(self, title, intro):
        L, F = self.m.last, self.m.last_fy
        s = f"# {title}\n\n"
        s += (f"> Собрано автоматически скриптом `tools/market_knowledge.py` {datetime.now():%d.%m.%Y} из отчётов НАПП. "
              f"Последний срез — {date_ru(L)} ({period_label(L)}), последний полный год — "
              f"{period_label(F) if F else NO_DATA}. Суммы — млн сум.")
        if self.gaps:
            s += f" Нет срезов: {', '.join(self.gaps)} (отчёт не загружен или не опубликован)."
        s += "\n\n" + intro.strip() + "\n\n"
        return s

    # ======================================================================= #
    # 1. Рынок в целом
    # ======================================================================= #
    def market(self):
        m, S, F = self.m, Sources(), self.facts
        L = m.last
        t = self.head("Рынок в целом", "Объём страховых премий и выплат по всем срезам, рост к тому же периоду "
                      "прошлого года, доли добровольного и обязательного страхования, убыточность.")
        tot = self.src_row(S, L, "total")
        g, b = m.yoy(L, "total")
        gp, _ = m.yoy(L, "total", "payouts_ytd")
        lr = loss_ratio(tot["premiums_ytd"], tot["payouts_ytd"])
        t += "## Главное на последний срез\n\n"
        t += (f"- {period_label(L)} (срез {date_ru(L)}): премии всего рынка — **{fmt_mln(tot['premiums_ytd'])} млн сум**"
              f"{', ' + fmt_yoy(g) + ' ' + period_to(b) if b else ''}.\n")
        t += (f"- Выплаты — {fmt_mln(tot['payouts_ytd'])} млн сум"
              f"{', ' + fmt_yoy(gp) + ' ' + period_to(b) if b else ''}; убыточность брутто — {fmt_pct(lr)}.\n")
        for k, ru in (("voluntary_general", "добровольное общее страхование"), ("mandatory", "обязательное страхование"),
                      ("voluntary_life", "добровольное страхование жизни")):
            v = m.val(L, k)
            t += f"- Доля: {ru} — {fmt_pct(v / tot['premiums_ytd'] * 100 if v is not None else None)}.\n"
        t += f"- Страховые обязательства на {date_ru(L)} — {fmt_mln(tot['liabilities'])} млн сум.\n\n"

        # все срезы
        rows = []
        for d in m.dates:
            r = self.src_row(S, d, "total")
            if not r:
                continue
            lr_d = loss_ratio(r["premiums_ytd"], r["payouts_ytd"])
            g, b = m.yoy(d, "total")
            gp, _ = m.yoy(d, "total", "payouts_ytd")
            rows.append([date_ru(d), period_label(d), fmt_mln(r["premiums_ytd"]), fmt_mln(r["payouts_ytd"]),
                         fmt_pct(lr_d), fmt_mln(r["liabilities"])])
            F.add("рынок", d, "premiums", r["premiums_ytd"], "млн сум", "весь рынок", share_pct=100.0,
                  loss_ratio_pct=lr_d, yoy_pct=g, yoy_base=b, source_folder=r["source_file"])
            F.add("рынок", d, "payouts", r["payouts_ytd"], "млн сум", "весь рынок", loss_ratio_pct=lr_d, yoy_pct=gp,
                  yoy_base=b, source_folder=r["source_file"])
            F.add("рынок", d, "liabilities", r["liabilities"], "млн сум", "весь рынок", source_folder=r["source_file"],
                  note="страховые обязательства на дату среза")
        t += "## Все срезы\n\n"
        t += table(["Срез", "Период (с начала года)", "Премии", "Выплаты", "Убыточность", "Обязательства на дату"], rows)
        t += "\nЦифры среза нельзя сравнивать между собой подряд: каждый срез — сумма с 1 января своего года.\n\n"

        # год к году
        rows = []
        for d in m.dates:
            b = m.base_date(d)
            if not b or not m.get(d, "total"):
                continue
            rows.append([period_label(d) + " " + period_to(b), fmt_mln(m.val(d, "total")), fmt_mln(m.val(b, "total")),
                         fmt_yoy(m.yoy(d, "total")[0]), fmt_mln(m.val(d, "total", "payouts_ytd")),
                         fmt_mln(m.val(b, "total", "payouts_ytd")), fmt_yoy(m.yoy(d, "total", "payouts_ytd")[0])])
        t += "## Рост год к году (сопоставимые периоды)\n\n"
        if rows:
            t += table(["Периоды", "Премии", "Премии год назад", "Рост премий", "Выплаты", "Выплаты год назад",
                        "Рост выплат"], rows)
        else:
            t += f"Пар сопоставимых периодов — {NO_DATA}.\n"
        t += ("\nСравниваются только одинаковые периоды. Например, I полугодие 2026 сравнивается с I полугодием 2025, "
              "а не с 2025 годом: в отчёте суммы нарастают с 1 января.\n\n")

        # структура
        rows = []
        for d in m.dates:
            tp = m.val(d, "total")
            if not tp:
                continue
            cells = [date_ru(d), period_label(d)]
            for k, topic in (("voluntary_general", "добровольное общее"), ("mandatory", "обязательное"),
                             ("voluntary_life", "добровольное жизни")):
                r = self.src_row(S, d, k)
                v = r["premiums_ytd"] if r else None
                sh = v / tp * 100 if v is not None else None
                lr_k = loss_ratio(v, r["payouts_ytd"]) if r else None
                g, b = m.yoy(d, k)
                cells += [fmt_mln(v), fmt_pct(sh)]
                if r:
                    F.add("рынок", d, "premiums", v, "млн сум", topic, share_pct=sh, loss_ratio_pct=lr_k, yoy_pct=g,
                          yoy_base=b, source_folder=r["source_file"])
            rows.append(cells)
        t += "## Структура: добровольное и обязательное страхование\n\n"
        t += table(["Срез", "Период", "Добровольное общее", "Доля", "Обязательное", "Доля", "Добровольное жизни",
                    "Доля"], rows)
        t += ("\nДоли — от премий всего рынка. Обязательное страхование включает ОСАГО, ОСГОР (и аннуитеты ОСГОР — "
              "их ведут страховщики жизни), ОСГО перевозчика, опасные объекты и СМР.\n\n")

        # участники рынка (лист 1.1)
        t += "## Участники рынка (лист 1.1)\n\n"
        st = self.ex["structure"]
        if st:
            ds = [x for x in (self.m.base_date(L), L) if x in st] or sorted(st)[-2:]
            names = [("insurers", "Страховых организаций", fmt_int), ("life_insurers", "из них страхование жизни", fmt_int),
                     ("capital", "Уставный капитал всех страховщиков, млн сум", fmt_mln),
                     ("brokers", "Страховых брокеров", fmt_int), ("actuaries", "Актуариев", fmt_int),
                     ("agents", "Страховых агентов", fmt_int), ("agents_legal", "из них юридических лиц", fmt_int),
                     ("guarantee_fund", "Членов Фонда гарантирования выплат", fmt_int)]
            rows = []
            for key, ru, fm in names:
                rows.append([ru] + [fm(st[d].get(key)) for d in ds])
                for d in ds:
                    v = st[d].get(key)
                    if v is not None:
                        S.add(self.ex["structure_src"][d], d)
                        F.add("участники рынка", d, key, v, "млн сум" if key == "capital" else "шт.", "весь рынок",
                              source_folder=self.ex["structure_src"][d], note=ru)
            t += table(["Показатель"] + [f"на {date_ru(d)}" for d in ds], rows)
        else:
            t += f"Лист 1.1 — {NO_DATA}.\n"
        t += "\n"

        # перестрахование за рубеж (1.6)
        t += "## Перестрахование у иностранных страховщиков (лист 1.6, общее страхование)\n\n"
        rs = self.ex["reins"]
        if rs:
            rows = []
            for d in sorted(rs):
                v = rs[d]
                S.add(self.ex["reins_src"][d], d)
                rows.append([date_ru(d), period_label(d), fmt_mln(v.get("ceded")), fmt_pct(v.get("ceded_pct")),
                             fmt_mln(v.get("accepted")), fmt_pct(v.get("accepted_pct"))])
                F.add("перестрахование", d, "ceded_abroad_premiums", v.get("ceded"), "млн сум", "весь рынок",
                      share_pct=v.get("ceded_pct"), source_folder=self.ex["reins_src"][d],
                      note="премии, переданные иностранным перестраховщикам; доля — от брутто-премий общего страхования")
            t += table(["Срез", "Период", "Передано за рубеж", "% от брутто-премий", "Принято из-за рубежа",
                        "% от брутто-премий"], rows)
        else:
            t += f"Лист 1.6 — {NO_DATA}.\n"
        t += "\n"
        notes = [(d, n) for (d, k), n in self.m.notes.items()]
        if notes:
            t += "## Пометки к данным\n\n" + "".join(f"- {date_ru(d)}: {n}.\n" for d, n in sorted(notes)) + "\n"
        t += caveats_md() + "\n" + S.footer()
        return t

    # ======================================================================= #
    # 2. Классы
    # ======================================================================= #
    def _class_table(self, d, S, with_contracts):
        m, F = self.m, self.facts
        tp = m.val(d, "total")
        ranked = sorted([k for k in (f"cls{i}" for i in range(1, MAX_CLASS + 1)) if (m.val(d, k) or 0) > 0],
                        key=lambda k: -m.val(d, k))
        rank = {k: i + 1 for i, k in enumerate(ranked)}
        rows = []
        for i in range(1, MAX_CLASS + 1):
            k, c = f"cls{i}", str(i)
            r = self.src_row(S, d, k)
            name = self.class_names.get(c, "")
            grp = self.class_group.get(c)
            if not r:
                rows.append([c, name, grp or "—", NO_DATA, "", "", "", "", "", ""] + ([""] if with_contracts else []))
                continue
            p, y = r["premiums_ytd"], r["payouts_ytd"]
            sh = p / tp * 100 if p is not None and tp else None
            lr = loss_ratio(p, y)
            g, b = m.yoy(d, k)
            flag = self._swing(g, m.val(b, k) if b else None)
            cells = [c, name, grp or "не задана", fmt_mln(p), fmt_pct(sh), rank.get(k, "—"), fmt_mln(y), lr_cell(p, y),
                     m.yoy_cell(d, k, flag=flag)]
            if with_contracts:
                act = self.ex["contracts"].get((d, k), (None, None))[0]
                if act is not None:
                    S.add(self.ex["contracts_src"].get((d, k)), d)
                cells.append(fmt_int(act))
            rows.append(cells)
            F.add("классы", d, "premiums", p, "млн сум", f"класс {c} — {name}", rank=rank.get(k), share_pct=sh,
                  loss_ratio_pct=lr, yoy_pct=g, yoy_base=b, source_folder=r["source_file"],
                  note="доля — от премий всего рынка; место — среди классов 1–18" + ("; " + flag.strip(" *") if flag else ""))
            F.add("классы", d, "payouts", y, "млн сум", f"класс {c} — {name}", loss_ratio_pct=lr,
                  yoy_pct=m.yoy(d, k, "payouts_ytd")[0], yoy_base=m.yoy(d, k, "payouts_ytd")[1],
                  source_folder=r["source_file"])
        head = ["Класс", "Название", "Учётная группа РНП", "Премии", "Доля рынка", "Место", "Выплаты", "Убыточность",
                "Рост премий г/г"] + (["Действующих договоров (лист 1.5)"] if with_contracts else [])
        return table(head, rows)

    @staticmethod
    def _swing(g, base):
        if g is None or base is None or base < 100:
            return ""
        if g > 300 or g < -80:
            return " *"
        return ""

    def classes(self):
        m, S, F = self.m, Sources(), self.facts
        L, FY = m.last, m.last_fy
        t = self.head("Классы страхования", "Классы 1–18 добровольного общего страхования (лист 1.4 отчёта НАПП): "
                      "премии, выплаты, убыточность, доля рынка, место класса, динамика; отдельно — пакеты "
                      "комплексного страхования (договор сразу по двум и более классам). Классы страхования жизни "
                      "(I–VII) здесь не разбираются — вне рамок проекта. Учётная группа РНП — из справочника "
                      "классов проекта (Положение № 1882, п. 10).")
        t += f"## Классы на последний срез — {period_label(L)}\n\n"
        t += self._class_table(L, S, True)
        t += ("\nДоля рынка — от премий всего рынка (включая обязательное и страхование жизни). Место — среди классов "
              "1–18 по премиям. «*» — резкое изменение год к году (больше +300 % или меньше −80 %): перед выводами "
              "сверить с отчётом, причина в открытых данных не указана.\n\n")
        if FY:
            t += f"## Классы за полный год — {period_label(FY)}\n\n"
            t += self._class_table(FY, S, False) + "\n"
        else:
            t += f"## Классы за полный год\n\nПолного года — {NO_DATA}.\n\n"

        # динамика
        t += "## Премии по классам на каждом срезе\n\n"
        rows = []
        for i in range(1, MAX_CLASS + 1):
            k = f"cls{i}"
            rows.append([str(i)] + [fmt_mln(m.val(d, k)) if self.src_row(S, d, k) else NO_DATA for d in m.dates])
        t += table(["Класс"] + [f"{date_ru(d)} ({period_label(d)})" for d in m.dates], rows)
        t += "\nКаждый столбец — сумма с 1 января своего года; сравнивать можно только одинаковые периоды разных лет.\n\n"

        # пакеты
        t += self._packages(S)

        # вывод разработчика
        t += f"## Что это значит для тарифа ({DEV})\n\n"
        t += self._class_conclusions(S)
        t += caveats_md() + "\n" + S.footer()
        return t

    def _pack_keys(self, d):
        return sorted([k for k in self.m.keys(d, "cls") if re.match(r"^cls\d+(_\d+)+$", k)],
                      key=lambda k: -(self.m.val(d, k) or 0))

    def _packages(self, S):
        m, F = self.m, self.facts
        L, FY = m.last, m.last_fy
        t = f"## Пакеты комплексного страхования — {period_label(L)}\n\n"
        mg = self.src_row(S, L, "multi_general")
        if not mg:
            return t + f"Пакеты — {NO_DATA}.\n\n"
        tot_pack = mg["premiums_ytd"]
        tp = m.val(L, "total")
        big = max((m.val(L, f"cls{i}") or 0, i) for i in range(1, MAX_CLASS + 1))
        t += (f"Комплексное страхование (два и более класса в одном договоре) — **{fmt_mln(tot_pack)} млн сум** премий "
              f"за {period_label(L)}, {fmt_pct(tot_pack / tp * 100)} всего рынка. Для сравнения, крупнейший отдельный "
              f"класс — {big[1]} — {fmt_mln(big[0])} млн сум.\n\n")
        F.add("пакеты", L, "premiums", tot_pack, "млн сум", "комплексное страхование (итог)", share_pct=tot_pack / tp * 100,
              loss_ratio_pct=loss_ratio(tot_pack, mg["payouts_ytd"]), yoy_pct=m.yoy(L, "multi_general")[0],
              yoy_base=m.yoy(L, "multi_general")[1], source_folder=mg["source_file"], note="доля — от премий всего рынка")
        rows = []
        keys = self._pack_keys(L) + (["multi_other"] if m.get(L, "multi_other") else [])
        for i, k in enumerate(keys):
            r = self.src_row(S, L, k)
            p, y = r["premiums_ytd"], r["payouts_ytd"]
            sh = p / tot_pack * 100 if tot_pack else None
            lr = loss_ratio(p, y)
            g, b = m.yoy(L, k)
            flag = self._swing(g, m.val(b, k) if b else None)
            fy = m.get(FY, k) if FY else None
            if fy:
                S.add(fy["source_file"], FY)
            label = "прочие пакеты (boshqalar)" if k == "multi_other" else "Классы " + ", ".join(k[3:].split("_"))
            what = "" if k == "multi_other" else " + ".join(SHORT.get(n, n) for n in k[3:].split("_"))
            rank = None if k == "multi_other" else i + 1
            rows.append([rank or "—", label, what, fmt_mln(p), fmt_pct(sh), fmt_mln(y), lr_cell(p, y),
                         m.yoy_cell(L, k, flag=flag), fmt_mln(fy["premiums_ytd"]) if fy else NO_DATA,
                         lr_cell(fy["premiums_ytd"], fy["payouts_ytd"]) if fy else NO_DATA])
            F.add("пакеты", L, "premiums", p, "млн сум", label, rank=rank, share_pct=sh, loss_ratio_pct=lr, yoy_pct=g,
                  yoy_base=b, source_folder=r["source_file"],
                  note="доля — от итога комплексного страхования" + ("; резкое изменение г/г — сверить с отчётом" if flag else ""))
            if fy:
                F.add("пакеты", FY, "premiums", fy["premiums_ytd"], "млн сум", label,
                      loss_ratio_pct=loss_ratio(fy["premiums_ytd"], fy["payouts_ytd"]),
                      yoy_pct=m.yoy(FY, k)[0], yoy_base=m.yoy(FY, k)[1], source_folder=fy["source_file"])
        t += table(["Место", "Пакет", "Состав", "Премии", "Доля в комплексном", "Выплаты", "Убыточность",
                    "Рост премий г/г", f"Премии, {period_label(FY) if FY else 'год'}",
                    f"Убыточность, {period_label(FY) if FY else 'год'}"], rows)
        t += ("\n«*» — резкое изменение год к году: строки пакетов в отчёте меняются от отчёта к отчёту, перед выводами "
              "сверить с отчётом. Как премия пакета делится между классами, в отчёте не показано.\n\n")
        ns = [(d, n) for (d, k), n in m.notes.items() if k == "multi_general"]
        if ns:
            t += "Пометки к итогу комплексного страхования:\n\n" + "".join(f"- {date_ru(d)}: {n}.\n" for d, n in sorted(ns)) + "\n"
        return t

    def _class_conclusions(self, S):
        m, FY, L = self.m, self.m.last_fy, self.m.last
        out = []
        if FY:
            hi = [(i, loss_ratio(m.val(FY, f"cls{i}"), m.val(FY, f"cls{i}", "payouts_ytd"))) for i in range(1, MAX_CLASS + 1)]
            hi = [(i, lr) for i, lr in hi if lr is not None and lr >= 50 and (m.val(FY, f"cls{i}") or 0) >= 1000]
            if hi:
                out.append(f"Высокая убыточность за {period_label(FY)} (50 % и выше при премиях от 1 000 млн сум): " +
                           "; ".join(f"класс {i} — {fmt_pct(lr)}" for i, lr in sorted(hi, key=lambda x: -x[1])) +
                           ". В этих классах на выплаты уходит половина рыночной премии и больше — скидки к тарифу "
                           "без анализа риска опасны.")
            zero = [str(i) for i in range(1, MAX_CLASS + 1) if m.get(FY, f"cls{i}") and not m.val(FY, f"cls{i}")]
            if zero:
                out.append(f"Без премий за {period_label(FY)}: класс(ы) {', '.join(zero)} — рыночного ориентира по ставке "
                           "нет, тариф только по собственной методике.")
        # имущество 8/9 — одиночно и в пакетах
        single = sum(m.val(L, k) or 0 for k in ("cls8", "cls9"))
        packs = sum(m.val(L, k) or 0 for k in self._pack_keys(L) if set(k[3:].split("_")) & {"8", "9"})
        if packs:
            out.append(f"Имущество (классы 8 и 9) за {period_label(L)}: отдельными договорами — {fmt_mln(single)} млн сум, "
                       f"в пакетах с классом 8 или 9 — {fmt_mln(packs)} млн сум. Поэтому рыночный ориентир по имуществу "
                       "берётся по пакету «8, 9» (так же делает экран расчёта), а не по одиночной строке класса.")
        out.append("Убыточность здесь «кассовая» (оплаченные выплаты к начисленным премиям за тот же период) — для "
                   "длинных и редких рисков (имущество, финансовые риски) она сильно колеблется от периода к периоду; "
                   "для тарифа смотреть несколько лет, а не один срез. Сравнение со ставками INSON — в заметке "
                   "«Рыночные ставки по классам».")
        return "".join(f"- {o} ({DEV})\n" for o in out) + "\n"

    # ======================================================================= #
    # 3. Компании
    # ======================================================================= #
    def _companies(self, d):
        ks = [k for k in self.m.keys(d, "company:") if (self.m.val(d, k) or 0) > 0]
        return sorted(ks, key=lambda k: -self.m.val(d, k))

    def _cname(self, k, d=None):
        for dd in ([d] if d else []) + list(reversed(self.m.dates)):
            r = self.m.get(dd, k)
            if r:
                return r["row_name"]
        return k.split(":", 1)[1]

    def companies(self):
        m, S, F = self.m, Sources(), self.facts
        L, FY = m.last, m.last_fy
        B = m.base_date(L)
        t = self.head("Страховые компании", "Рейтинг страховщиков общего страхования по премиям (листы 2.1, 2.3, "
                      "2.5 отчёта НАПП), доли, убыточность, перестрахование, претензии (лист 2.10), подразделения "
                      "(листы 2.9, 2.12). Страховщики жизни в рейтинг не входят: их листы (2.2, 2.4, 2.6) в проекте не "
                      "разбираются. О компаниях здесь только то, что есть в отчёте НАПП.")
        ks = self._companies(L)
        tot = sum(m.val(L, k) for k in ks)
        rank_prev = {k: i + 1 for i, k in enumerate(self._companies(B))} if B else {}
        tot_prev = sum(m.val(B, k) for k in rank_prev) if B else None
        rows = []
        for i, k in enumerate(ks):
            r = self.src_row(S, L, k)
            p, y = r["premiums_ytd"], r["payouts_ytd"]
            sh = p / tot * 100
            g, b = m.yoy(L, k)
            if b:
                S.add(m.get(b, k)["source_file"] if m.get(b, k) else None, b)
            lr = loss_ratio(p, y)
            cr = self.ex["comp_reins"].get((L, k), {})
            ced = cr.get("ceded")
            ced_sh = ced / p * 100 if ced is not None and p else None
            if cr:
                S.add(self.ex["comp_reins_src"].get((L, k)), L)
            rows.append([i + 1, r["row_name"], fmt_mln(p), fmt_pct(sh), m.yoy_cell(L, k), rank_prev.get(k, "—"), fmt_mln(y),
                         lr_cell(p, y), fmt_mln(cr.get("net")), fmt_pct(ced_sh)])
            F.add("компании", L, "premiums", p, "млн сум", r["row_name"], rank=i + 1, share_pct=sh, loss_ratio_pct=lr,
                  yoy_pct=g, yoy_base=b, source_folder=r["source_file"],
                  note=f"доля — среди страховщиков общего страхования (лист 2.1); место год назад — {rank_prev.get(k, 'нет')}")
            if ced_sh is not None:
                F.add("компании", L, "ceded_share", ced_sh, "%", r["row_name"], source_folder=self.ex["comp_reins_src"].get((L, k)),
                      note="доля брутто-премий, переданная в перестрахование (лист 2.1)")
        t += f"## Рейтинг по премиям — {period_label(L)} (срез {date_ru(L)})\n\n"
        zero = [self._cname(k, L) for k in m.keys(L, "company:") if (m.val(L, k) or 0) == 0]
        t += (f"Страховщиков общего страхования с премиями за период: {len(ks)}; их премии вместе — {fmt_mln(tot)} млн сум."
              + (f" Без премий за период (в рейтинг не включены): {', '.join(zero)}." if zero else "") + "\n\n")
        t += table(["Место", "Компания", "Премии (брутто)", "Доля", "Рост г/г",
                    f"Место за {period_label(B) if B else 'год назад'}", "Выплаты", "Убыточность", "Нетто-премии",
                    "Доля переданного в перестрахование"], rows)
        t += ("\nДоля — среди страховщиков общего страхования. Нетто-премии — после передачи в перестрахование "
              "(лист 2.1, «Sof sug'urta mukofotlari»). «—» в месте год назад — компании не было в отчёте или у неё не было премий.\n\n")

        # концентрация
        t += "## Концентрация рынка\n\n"
        rows = []
        for d in [x for x in (B, L) if x]:
            kk = self._companies(d)
            tt = sum(m.val(d, k) for k in kk)
            shares = [m.val(d, k) / tt * 100 for k in kk]
            hhi = sum(s * s for s in shares)
            rows.append([period_label(d), len(kk), fmt_pct(sum(shares[:3])), fmt_pct(sum(shares[:5])),
                         fmt_pct(sum(shares[:10])), fmt_int(hhi)])
            for n in (3, 5, 10):
                F.add("компании", d, f"top{n}_share", sum(shares[:n]), "%", "весь рынок (общее страхование)",
                      source_folder=m.get(d, kk[0])["source_file"], note=f"доля {n} крупнейших страховщиков по премиям")
            F.add("компании", d, "hhi", hhi, "пункты", "весь рынок (общее страхование)",
                  source_folder=m.get(d, kk[0])["source_file"], digits=0,
                  note="индекс Херфиндаля — Хиршмана: сумма квадратов долей в процентах")
        t += table(["Период", "Страховщиков", "Доля топ-3", "Доля топ-5", "Доля топ-10", "Индекс HHI"], rows)
        t += ("\nИндекс HHI — сумма квадратов долей (в процентах). Принятая в антимонопольной практике шкала: до 1 500 — "
              f"низкая концентрация, 1 500–2 500 — умеренная, выше 2 500 — высокая ({DEV}: шкала не из отчёта НАПП).\n\n")

        # полный год
        if FY:
            ksf = self._companies(FY)
            tf = sum(m.val(FY, k) for k in ksf)
            BF = m.base_date(FY)
            rows = []
            for i, k in enumerate(ksf):
                r = self.src_row(S, FY, k)
                g, b = m.yoy(FY, k)
                lr = loss_ratio(r["premiums_ytd"], r["payouts_ytd"])
                rows.append([i + 1, r["row_name"], fmt_mln(r["premiums_ytd"]), fmt_pct(r["premiums_ytd"] / tf * 100),
                             m.yoy_cell(FY, k), fmt_mln(r["payouts_ytd"]), lr_cell(r["premiums_ytd"], r["payouts_ytd"])])
                F.add("компании", FY, "premiums", r["premiums_ytd"], "млн сум", r["row_name"], rank=i + 1,
                      share_pct=r["premiums_ytd"] / tf * 100, loss_ratio_pct=lr, yoy_pct=g, yoy_base=b,
                      source_folder=r["source_file"], note="доля — среди страховщиков общего страхования")
            t += f"## Рейтинг за {period_label(FY)}\n\n"
            t += table(["Место", "Компания", "Премии", "Доля", f"Рост {period_to(BF) if BF else 'к прошлому году'}",
                        "Выплаты", "Убыточность"], rows) + "\n"

        t += self._claims_companies(S, L)
        t += self._divisions(S, L)
        t += ("## Специализация компаний\n\n"
              f"Разреза «страховщик × класс страхования» в открытых отчётах НАПП нет — {NO_DATA}. В каких классах или "
              "пакетах сильна конкретная компания, по этим данным сказать нельзя. Что можно сказать по отчёту: объём "
              "премий, убыточность, долю переданного в перестрахование, претензии и регионы, где у компании есть "
              "подразделения (таблицы выше).\n\n")
        t += self._ranking()
        t += self._financials()
        t += self._inson(S)
        t += caveats_md() + "\n" + S.footer()
        return t

    def _claims_companies(self, S, L):
        F = self.facts
        t = f"## Претензии по страховщикам — {period_label(L)} (лист 2.10, общее страхование)\n\n"
        if not _has_table(self.con, "napp_claims"):
            return t + f"Претензии — {NO_DATA}.\n\n"
        rs = _rows(self.con, "SELECT * FROM napp_claims WHERE report_date=? AND scope='company' ORDER BY claims_received DESC",
                   L)
        if not rs:
            return t + f"Претензии за {period_label(L)} — {NO_DATA}.\n\n"
        rows = []
        for r in rs:
            S.add(r["source_file"], L)
            rec = r["claims_received"]
            ref = r["claims_refused"] / rec * 100 if rec and r["claims_refused"] is not None else None
            if not rec and not r["contracts_active"]:
                continue                                   # ни претензий, ни договоров — строка без содержания
            per = rec / r["contracts_active"] * 1000 if rec is not None and r["contracts_active"] else None
            rows.append([r["name"], fmt_int(rec), fmt_int(r["claims_paid"]), fmt_int(r["claims_refused"]), fmt_pct(ref),
                         fmt_int(r["claims_unsettled"]), fmt_int(r["contracts_active"]), fmt_pct(per, 1).replace(" %", "")
                         if per is not None else NO_DATA])
            F.add("претензии", L, "claims_received", rec, "шт.", r["name"], share_pct=None, source_folder=r["source_file"],
                  digits=0, note=f"оплачено {fmt_int(r['claims_paid'])}, отказано {fmt_int(r['claims_refused'])} "
                                 f"({fmt_pct(ref)}), не урегулировано {fmt_int(r['claims_unsettled'])}")
        t += table(["Страховщик", "Поступило", "Оплачено", "Отказано", "Доля отказов", "Не урегулировано",
                    "Действующих договоров", "Претензий на 1 000 договоров"], rows)
        t += ("\nПретензии — число с начала года; договоры — действующие на дату (лист 2.7). Частота на 1 000 договоров "
              "грубая: в отчёте нет разбивки по классам, а у ОСАГО и кредитного страхования договоров на порядки "
              "больше, чем у имущества; у страховщиков с малым числом договоров показатель неустойчив.\n\n")
        return t

    def _divisions(self, S, L):
        F = self.facts
        t = "## Подразделения и регионы присутствия (листы 2.9 и 2.12)\n\n"
        dv = {k: v for (d, k), v in self.ex["divisions"].items() if d == L}
        br = {}
        if _has_table(self.con, "napp_branches"):
            for r in _rows(self.con, "SELECT company_key, region_key, premiums_mln, contracts, source_file FROM napp_branches "
                                     "WHERE report_date=? AND region_key<>'total'", L):
                if (r["premiums_mln"] or 0) > 0 or (r["contracts"] or 0) > 0:
                    br.setdefault(r["company_key"], set()).add(r["region_key"])
                    S.add(r["source_file"], L)
        if not dv and not br:
            return t + f"Подразделения — {NO_DATA}.\n\n"
        keys = sorted(set(dv) | set(br), key=lambda k: -(dv.get(k, (0,))[0] or 0))
        rows = []
        for k in keys:
            if k == "total":
                continue
            v = dv.get(k)
            if v:
                S.add(v[3], L)
            name = self._cname(k, L) if self.m.get(L, k) else (self._branch_name(k) or k.split(":", 1)[1])
            rows.append([name, fmt_int(v[0]) if v else NO_DATA, fmt_int(v[1]) if v else NO_DATA,
                         fmt_int(v[2]) if v else NO_DATA, len(br[k]) if k in br else NO_DATA])
            if v:
                F.add("компании", L, "divisions", v[0], "шт.", name, source_folder=v[3], digits=0,
                      note=f"территориальных подразделений: в городах {fmt_int(v[1])}, в сельской местности {fmt_int(v[2])}")
        if "total" in dv:
            v = dv["total"]
            t += f"Всего территориальных подразделений страховщиков на {date_ru(L)}: {fmt_int(v[0])} (в городах {fmt_int(v[1])}, в сельской местности {fmt_int(v[2])}).\n\n"
        t += table(["Компания", "Подразделений", "в городах", "в сельской местности",
                    "Регионов с премиями или договорами (из 14)"], rows)
        t += "\nВ таблице — и страховщики жизни (лист 2.9 их включает).\n\n"
        return t

    def _branch_name(self, key):
        if not _has_table(self.con, "napp_branches"):
            return None
        r = self.con.execute("SELECT company_name FROM napp_branches WHERE company_key=? ORDER BY report_date DESC LIMIT 1",
                             (key,)).fetchone()
        return r[0] if r else None

    def _financials(self):
        t = "## Финансовые показатели\n\n"
        rs = _rows(self.con, "SELECT * FROM company_financials ORDER BY report_date") if _has_table(
            self.con, "company_financials") else []
        t += ("В отчёте НАПП собственных средств, резервов и капитала по компаниям нет (есть только уставный капитал "
              "всех страховщиков вместе, лист 1.1, см. заметку «Рынок в целом»); по компаниям — раздел «Финансы "
              "страховщиков (рэнкинг snsratings)» выше.\n\n")
        if rs:
            t += "Таблица проекта company_financials (только INSON):\n\n"
            rows = []
            for r in rs:
                rows.append([date_ru(r["report_date"]), fmt_mln(r["own_funds"] / 1e6), fmt_mln(r["reserves"] / 1e6),
                             r["source"] or ""])
                self.facts.add("INSON", r["report_date"], "own_funds", r["own_funds"] / 1e6, "млн сум", '"INSON" AJ',
                               note=f"company_financials: {r['source'] or 'источник не указан'}")
                self.facts.add("INSON", r["report_date"], "reserves", r["reserves"] / 1e6, "млн сум", '"INSON" AJ',
                               note=f"company_financials: {r['source'] or 'источник не указан'}")
            t += table(["На дату", "Собственные средства, млн сум", "Страховые резервы, млн сум", "Пометка"], rows)
            t += ("\nЭто не отчёт НАПП: цифры проекта для лимита на один риск (Положение № 1806, п. 15) — до "
                  "подтверждения бухгалтерией (временные или из публичного рэнкинга, см. пометку).\n\n")
        return t

    # ---- рэнкинг snsratings.uz (company_rankings, tools/ranking_parse.py) ----
    RANK_COLS = [("total_assets", "Активы"), ("total_capital", "Совокупный капитал"),
                 ("reserves_net", "Резервы чистые"), ("premiums_total", "Премии всего"),
                 ("net_profit", "Чистая прибыль"), ("roe", "Рентабельность капитала"),
                 ("payouts_to_premiums", "Выплаты / премии"), ("refused_to_claims", "Доля отказов")]
    RANK_FACTS = ["total_assets", "total_capital", "share_capital", "reserves_gross", "reserves_net", "premiums_total",
                  "premiums_net", "claims_paid", "net_profit", "roe", "roa", "payouts_to_premiums", "claims_received",
                  "claims_paid_count", "claims_refused", "refused_to_claims", "contracts_active", "contracts_new"]

    def _rk_rows(self):
        if not _has_table(self.con, "company_rankings"):
            return None, {}
        per = self.con.execute("SELECT MAX(report_period) FROM company_rankings").fetchone()[0]
        if not per:
            return None, {}
        by = {}
        for r in _rows(self.con, "SELECT * FROM company_rankings WHERE report_period=?", per):
            by.setdefault(r["company"], {})[r["indicator_code"]] = r
        return per, by

    @staticmethod
    def _rk_cell(r):
        if not r or r["value_cur"] is None:
            return NO_DATA
        if r["unit"] == "%":
            return fmt_pct(r["value_cur"], 2)
        if r["unit"] == "кол-во":
            return fmt_int(r["value_cur"])
        return _grp(f"{r['value_cur']:,.1f}")

    def _rk_fact(self, r, n):
        """Факт рэнкинга: topic company_finance, источник — текст рэнкинга (а не отчёт НАПП)."""
        unit = {"кол-во": "шт.", "%": "%"}.get(r["unit"], r["unit"])
        self.facts.items.append({
            "topic": "company_finance", "period": "II кв. 2026 (на 30.06.2026)" if r["report_period"] == "2026-Q2"
            else r["report_period"], "metric": r["indicator_code"],
            "value": None if r["value_cur"] is None else round(r["value_cur"], 2), "unit": unit,
            "entity": r["company"], "rank": r["rank_cur"], "share_pct": r1(r["share_cur"]), "loss_ratio_pct": None,
            "yoy_pct": r1(r["change_pct"]) if r["value_prev"] is not None else None,
            "yoy_base_date": "2025-07-01" if r["change_pct"] is not None and r["value_prev"] is not None else None,
            "source_file": r["source_file"], "source_date": "2026-07-01" if r["report_period"] == "2026-Q2" else None,
            "note": (f"{r['indicator_name']}; место {r['rank_cur'] if r['rank_cur'] is not None else '—'} из {n}; "
                     f"год назад {r['value_prev'] if r['value_prev'] is not None else 'нет данных'} "
                     f"(место {r['rank_prev'] if r['rank_prev'] is not None else '—'}); стр. {r['page']}; "
                     "Рэнкинг snsratings.uz, II кв. 2026 (по данным openinfo.uz, НАПП)"),
            "source": "Рэнкинг snsratings.uz, II кв. 2026 (по данным openinfo.uz, НАПП)",
            "source_url": "https://snsratings.uz"})

    def _ranking(self):
        t = "## Финансы страховщиков (рэнкинг snsratings)\n\n"
        per, by = self._rk_rows()
        if not by:
            return t + (f"Рэнкинг страховщиков snsratings.uz в базе не загружен — {NO_DATA} "
                        "(загрузка: `python tools/ranking_parse.py --load`).\n\n")
        comps = [c for c in by if c != "ВСЕГО"]
        n = {code: sum(1 for c in comps if code in by[c]) for code in self.RANK_FACTS}
        src = next(iter(by["ВСЕГО"].values()))["source_file"] if "ВСЕГО" in by else ""
        t += ("Источник: Рэнкинг snsratings.uz, II кв. 2026 (по данным openinfo.uz, НАПП), файл "
              f"«{src}» (library/03_Рынок_НАПП/Рэнкинг snsratings). Это не отчёт НАПП: рэнкинг собран по бухгалтерской "
              "отчётности компаний и данным НАПП, проектом не проверялся. Остатки (активы, капитал, резервы) — на "
              "30.06.2026, потоки (премии, выплаты, прибыль, претензии) — с начала года по 30.06; выплаты — оплаченные. "
              f"Страховщиков в рэнкинге: {len(comps)}, из них страховщиков жизни: "
              f"{sum(1 for c in comps if any(r['is_life'] for r in by[c].values()))}. Место — как в рэнкинге "
              "(по суммам — 1 у наибольшего значения).\n\n")
        order = sorted(comps, key=lambda c: (by[c].get("total_assets") or {}).get("rank_cur") or 99)
        rows = []
        for c in order:
            a = by[c].get("total_assets")
            life = " (жизнь)" if any(r["is_life"] for r in by[c].values()) else ""
            rows.append([a["rank_cur"] if a else "—", c + life] + [self._rk_cell(by[c].get(k)) for k, _ in self.RANK_COLS])
            for k in self.RANK_FACTS:
                if k in by[c]:
                    self._rk_fact(by[c][k], n[k])
        if "ВСЕГО" in by:
            rows.append(["", "Весь рынок (ВСЕГО)"] + [self._rk_cell(by["ВСЕГО"].get(k)) for k, _ in self.RANK_COLS])
            for k in self.RANK_FACTS:
                if k in by["ВСЕГО"]:
                    self._rk_fact(by["ВСЕГО"][k], n[k])
        t += table(["Место по активам", "Компания"] + [f"{h}{', млн сум' if k not in ('roe', 'payouts_to_premiums', 'refused_to_claims') else ''}"
                                                       for k, h in self.RANK_COLS], rows)
        t += ("\nРентабельность капитала — чистая прибыль за полугодие / совокупный капитал (не в годовом выражении). "
              "«Выплаты / премии» — как в рэнкинге (знаменатель — премии по данным НАПП, поэтому на ~1 % отличается "
              "от «Премий всего» из отчётности). Доля отказов — отказано / поступило претензий.\n\n")
        ins = by.get("INSON AJ")
        if ins:
            def line(code, label):
                r = ins.get(code)
                if not r:
                    return f"- {label}: {NO_DATA}."
                s_ = f"- {label}: {self._rk_cell(r)}{' млн сум' if r['unit'] == 'млн сум' else ''}"
                if r["rank_cur"] is not None:
                    s_ += f" — {r['rank_cur']}-е место из {n.get(code) or len(comps)}"
                if r["share_cur"] is not None:
                    s_ += f", доля {fmt_pct(r['share_cur'], 2)}"
                if r["value_prev"] is not None:
                    prev = fmt_pct(r["value_prev"], 2) if r["unit"] == "%" else (
                        fmt_int(r["value_prev"]) if r["unit"] == "кол-во" else _grp(f"{r['value_prev']:,.1f}"))
                    s_ += f"; год назад {prev}" + (f" ({r['rank_prev']}-е место)" if r["rank_prev"] is not None else "")
                if r["change_pct"] is not None and r["value_prev"] is not None:
                    s_ += f", изменение {fmt_yoy(r['change_pct'])}"
                elif r["unit"] == "%" and r["change_pp"] is not None:
                    pp = _grp(f"{r['change_pp']:,.2f}")
                    s_ += f", изменение {'+' if r['change_pp'] > 0 else ''}{pp} п.п."
                t_ = by["ВСЕГО"].get(code) if "ВСЕГО" in by else None
                if r["unit"] == "%" and t_ and t_["value_cur"] is not None:
                    s_ += f"; по рынку {fmt_pct(t_['value_cur'], 2)}"
                return s_ + "."
            t += "### INSON в рэнкинге\n\n"
            t += "\n".join([
                line("total_assets", "Совокупные активы (место INSON на рынке по активам)"),
                line("premiums_total", "Всего собранные премии"),
                line("total_capital", "Совокупный капитал"),
                line("share_capital", "Акционерный капитал"),
                line("reserves_gross", "Страховые резервы брутто"),
                line("reserves_net", "Страховые резервы чистые"),
                line("payouts_to_premiums", "Страховые выплаты / страховые премии"),
                line("claims_received", "Поступило претензий"),
                line("claims_refused", "Отказано по претензиям"),
                line("refused_to_claims", "Доля отказов"),
                line("roe", "Рентабельность собственного капитала"),
                line("roa", "Рентабельность активов"),
            ]) + "\n\n"
            t += ("Совокупный капитал и чистые резервы INSON из рэнкинга записаны в company_financials (лимит на один "
                  "риск, Положение № 1806, п. 15) с пометкой «из публичного рэнкинга, до подтверждения бухгалтерией».\n\n")
        return t

    def _inson(self, S):
        m, F = self.m, self.facts
        k = mp.INSON_ROW
        t = "## Карточка INSON\n\n"
        if not any(m.get(d, k) for d in m.dates):
            return t + f"Строки INSON в отчётах — {NO_DATA}.\n\n"
        rows = []
        for d in m.dates:
            r = self.src_row(S, d, k)
            if not r:
                rows.append([date_ru(d), period_label(d), NO_DATA, "", "", "", "", ""])
                continue
            ks = self._companies(d)
            tot = sum(m.val(d, x) for x in ks)
            rank = ks.index(k) + 1
            g, b = m.yoy(d, k)
            lr = loss_ratio(r["premiums_ytd"], r["payouts_ytd"])
            sh = r["premiums_ytd"] / tot * 100
            rows.append([date_ru(d), period_label(d), fmt_mln(r["premiums_ytd"]), fmt_pct(sh), f"{rank} из {len(ks)}",
                         fmt_yoy(g), fmt_mln(r["payouts_ytd"]), fmt_pct(lr)])
            F.add("INSON", d, "premiums", r["premiums_ytd"], "млн сум", '"INSON" AJ', rank=rank, share_pct=sh,
                  loss_ratio_pct=lr, yoy_pct=g, yoy_base=b, source_folder=r["source_file"],
                  note=f"место среди {len(ks)} страховщиков общего страхования")
        t += table(["Срез", "Период", "Премии", "Доля", "Место", "Рост г/г", "Выплаты", "Убыточность"], rows) + "\n"
        L = m.last
        extra = []
        cr = self.ex["comp_reins"].get((L, k))
        if cr and m.val(L, k):
            extra.append(f"Нетто-премии за {period_label(L)} — {fmt_mln(cr.get('net'))} млн сум; передано в "
                         f"перестрахование — {fmt_mln(cr.get('ceded'))} млн сум "
                         f"({fmt_pct(cr['ceded'] / m.val(L, k) * 100 if cr.get('ceded') is not None else None)} брутто-премий).")
        if _has_table(self.con, "napp_claims"):
            c = _rows(self.con, "SELECT * FROM napp_claims WHERE report_date=? AND key=?", L, k)
            if c:
                c = c[0]
                ref = c["claims_refused"] / c["claims_received"] * 100 if c["claims_received"] else None
                extra.append(f"Претензии за {period_label(L)}: поступило {fmt_int(c['claims_received'])}, оплачено "
                             f"{fmt_int(c['claims_paid'])}, отказано {fmt_int(c['claims_refused'])} ({fmt_pct(ref)}), не "
                             f"урегулировано {fmt_int(c['claims_unsettled'])}; действующих договоров — "
                             f"{fmt_int(c['contracts_active'])}, новых с начала года — {fmt_int(c['contracts_new'])}.")
                F.add("INSON", L, "claims_received", c["claims_received"], "шт.", '"INSON" AJ', source_folder=c["source_file"],
                      digits=0, note=f"отказано {fmt_pct(ref)}")
        dv = self.ex["divisions"].get((L, k))
        if dv:
            extra.append(f"Территориальных подразделений на {date_ru(L)} — {fmt_int(dv[0])} (в городах {fmt_int(dv[1])}, "
                         f"в сельской местности {fmt_int(dv[2])}).")
        if _has_table(self.con, "napp_branches"):
            regs = _rows(self.con, "SELECT region_name, premiums_mln, payouts_mln, contracts FROM napp_branches WHERE "
                                   "report_date=? AND company_key=? AND region_key<>'total' AND "
                                   "(COALESCE(premiums_mln,0)>0 OR COALESCE(contracts,0)>0) ORDER BY premiums_mln DESC", L, k)
            if regs:
                extra.append(f"Регионы с премиями или договорами INSON за {period_label(L)} (лист 2.12): {len(regs)} из 14 — " +
                             "; ".join(f"{r['region_name']} {fmt_mln(r['premiums_mln'])}" for r in regs) + " (млн сум).")
        t += "".join(f"- {e}\n" for e in extra) + "\n"
        return t

    # ======================================================================= #
    # 4. Регионы
    # ======================================================================= #
    def regions(self):
        m, S, F = self.m, Sources(), self.facts
        L, FY = m.last, m.last_fy
        t = self.head("Регионы", "Премии, выплаты и претензии по регионам (листы 3.1, 3.2, 3.3, 3.4, 3.5 отчёта НАПП). "
                      "Разреза «регион × класс» в отчёте нет — только регион в целом.")
        t += ("**Как читать.** Премии и претензии учитываются по месту головных офисов страховщиков и онлайн-продаж, "
              "а не по месту нахождения застрахованного объекта (так отчёт читается в проекте с 01.10.2026: головные "
              "офисы почти всех страховщиков — в городе Ташкенте). Поэтому доля Ташкента завышена, а доли областей "
              "занижены; сравнивать регионы для поправки ставки нельзя.\n\n")
        cl = {}
        if _has_table(self.con, "napp_claims"):
            for r in _rows(self.con, "SELECT * FROM napp_claims WHERE report_date=? AND scope='region'", L):
                cl[r["key"]] = r
        for d, with_claims in ((L, True), (FY, False)):
            if not d:
                continue
            ks = sorted([k for k in m.keys(d, "region:") if m.val(d, k) is not None], key=lambda k: -m.val(d, k))
            tot = m.val(d, "total")
            rows = []
            for i, k in enumerate(ks):
                r = self.src_row(S, d, k)
                p, y = r["premiums_ytd"], r["payouts_ytd"]
                sh = p / tot * 100 if tot else None
                g, b = m.yoy(d, k)
                lr = loss_ratio(p, y)
                cells = [i + 1, r["row_name"], fmt_mln(p), fmt_pct(sh), fmt_yoy(g), fmt_mln(y), fmt_pct(lr)]
                if with_claims:
                    c = cl.get(k)
                    if c:
                        S.add(c["source_file"], d)
                    rec = c["claims_received"] if c else None
                    ref = c["claims_refused"] / rec * 100 if c and rec else None
                    cells += [fmt_int(rec), fmt_pct(ref), fmt_int(c["contracts_active"]) if c else NO_DATA]
                rows.append(cells)
                F.add("регионы", d, "premiums", p, "млн сум", r["row_name"], rank=i + 1, share_pct=sh, loss_ratio_pct=lr,
                      yoy_pct=g, yoy_base=b, source_folder=r["source_file"], note="доля — от премий всего рынка; учёт по месту "
                                                                                  "головного офиса страховщика")
            head = ["Место", "Регион", "Премии", "Доля", "Рост г/г", "Выплаты", "Убыточность"]
            if with_claims:
                head += ["Претензий поступило", "Доля отказов", "Действующих договоров"]
            t += f"## Регионы — {period_label(d)}\n\n" + table(head, rows) + "\n"
        # доля Ташкента
        cap = mp.CAPITAL_KEY
        rows = []
        for d in m.dates:
            v, tot = m.val(d, cap), m.val(d, "total")
            if v is None or not tot:
                continue
            self.src_row(S, d, cap)
            sh = v / tot * 100
            rows.append([date_ru(d), period_label(d), fmt_mln(v), fmt_pct(sh)])
            F.add("регионы", d, "capital_share", sh, "%", "город Ташкент", source_folder=m.get(d, cap)["source_file"],
                  note="доля города Ташкента в премиях всего рынка; учёт по месту головного офиса")
        t += "## Доля города Ташкента по срезам\n\n"
        t += table(["Срез", "Период", "Премии г. Ташкента", "Доля в рынке"], rows) if rows else f"{NO_DATA}.\n"
        t += "\n" + caveats_md() + "\n" + S.footer()
        return t

    # ======================================================================= #
    # 5. Обязательные виды
    # ======================================================================= #
    MAND = [("osago", "ОСАГО — ГО владельцев транспортных средств", "ПКМ № 141 от 24.06.2008"),
            ("osgor", "ОСГОР — ГО работодателя", "ПКМ № 177 от 24.06.2009 (ред. ПКМ № 443 от 15.07.2025)"),
            ("osgor_annuity", "ОСГОР — аннуитеты (выплаты рентой, ведут страховщики жизни)", "ПКМ № 177"),
            ("carrier", "ОСГО перевозчика", "ПКМ № 266 от 15.09.2015"),
            ("hazardous", "ГО владельцев опасных производственных объектов", None),
            ("cmr_mandatory", "Строительно-монтажные риски (обязательное)", None),
            ("mandatory_other", "Прочие обязательные (boshqalar)", None)]

    def _mrow(self, d, k):
        r = self.m.get(d, k)
        if r:
            return r
        return self.ex["mand"].get((d, k))

    def mandatory(self):
        m, S, F = self.m, Sources(), self.facts
        L, FY = m.last, m.last_fy
        t = self.head("Обязательные виды страхования", "ОСАГО, ОСГОР, ОСГО перевозчика и другие обязательные виды "
                      "(лист 1.4 отчёта НАПП, блок «Majburiy sug'urta»; договоры — лист 1.5). Тарифы ОСАГО, ОСГОР и ОСГО "
                      "перевозчика утверждены постановлениями Кабинета Министров — страховщик их не меняет. Класс 10 («автогражданская "
                      "ответственность») — это добровольная автогражданка, не ОСАГО.")
        for d in (L, FY):
            if not d:
                continue
            mt = m.val(d, "mandatory")
            rows = []
            for k, ru, _ in self.MAND:
                r = self._mrow(d, k)
                if not r:
                    rows.append([ru, NO_DATA, "", "", "", "", "", ""])
                    continue
                S.add(r["source_file"], d)
                p, y = r["premiums_ytd"], r["payouts_ytd"]
                b = m.base_date(d)
                rb = self._mrow(b, k) if b else None
                g = growth(p, rb["premiums_ytd"]) if rb else None
                if rb:
                    S.add(rb["source_file"], b)
                lr = loss_ratio(p, y)
                sh = p / mt * 100 if p is not None and mt else None
                act, new = self.ex["contracts"].get((d, k), (None, None))
                if act is not None:
                    S.add(self.ex["contracts_src"].get((d, k)), d)
                rows.append([ru, fmt_mln(p), fmt_pct(sh), fmt_yoy(g), fmt_mln(y), lr_cell(p, y), fmt_int(act),
                             fmt_int(new)])
                F.add("обязательные", d, "premiums", p, "млн сум", ru, share_pct=sh, loss_ratio_pct=lr, yoy_pct=g,
                      yoy_base=b if g is not None else None, source_folder=r["source_file"],
                      note="доля — от премий обязательного страхования")
                if act is not None:
                    F.add("обязательные", d, "contracts_active", act, "шт.", ru, digits=0,
                          source_folder=self.ex["contracts_src"].get((d, k)), note=f"новых с начала года — {fmt_int(new)}")
            mr = m.get(d, "mandatory")
            if mr:
                S.add(mr["source_file"], d)
                g, b = m.yoy(d, "mandatory")
                rows.append(["**Всё обязательное**", fmt_mln(mt), "100,0 %", fmt_yoy(g), fmt_mln(mr["payouts_ytd"]),
                             fmt_pct(loss_ratio(mt, mr["payouts_ytd"])),
                             fmt_int(self.ex["contracts"].get((d, "mandatory"), (None,))[0]),
                             fmt_int(self.ex["contracts"].get((d, "mandatory"), (None, None))[1])])
            t += f"## {period_label(d)} (срез {date_ru(d)})\n\n"
            t += table(["Вид", "Премии", "Доля в обязательном", "Рост премий г/г", "Выплаты", "Убыточность",
                        "Действующих договоров", "Новых договоров с начала года"], rows) + "\n"
        # ряд ОСАГО и ОСГОР
        t += "## ОСАГО и ОСГОР по срезам\n\n"
        rows = []
        for d in m.dates:
            cells = [date_ru(d), period_label(d)]
            for k in ("osago", "osgor"):
                r = self.src_row(S, d, k)
                cells += [fmt_mln(r["premiums_ytd"]) if r else NO_DATA,
                          fmt_pct(loss_ratio(r["premiums_ytd"], r["payouts_ytd"])) if r else NO_DATA,
                          fmt_yoy(m.yoy(d, k)[0])]
            rows.append(cells)
        t += table(["Срез", "Период", "ОСАГО премии", "ОСАГО убыточность", "ОСАГО рост г/г", "ОСГОР премии",
                    "ОСГОР убыточность", "ОСГОР рост г/г"], rows) + "\n"
        # тарифы
        t += "## Нормативные тарифы — где смотреть\n\n"
        t += ("Ставки обязательных видов здесь не переписываются: их устанавливает нормативный акт, действующая редакция "
              "— в библиотеке проекта (тексты взяты с lex.uz).\n\n")
        lib = ROOT / LAW_DIR
        acts = [("ОСАГО", "ПКМ № 141 от 24.06.2008 — меры по реализации Закона об ОСГО владельцев ТС (Правила и тарифы).txt"),
                ("ОСГОР", "ПКМ № 177 от 24.06.2009 — меры по реализации Закона об ОСГО работодателя (Правила и тарифы).txt"),
                ("ОСГОР, коэффициенты по видам деятельности",
                 "ПКМ № 443 от 15.07.2025 — коэффициенты тарифов ОСГОР по видам деятельности (узб.).txt"),
                ("ОСГО перевозчика", "ПКМ № 266 от 15.09.2015 — меры по реализации Закона об ОСГО перевозчика (Правила и тарифы).txt")]
        for what, f in acts:
            ok = (lib / f).exists()
            t += f"- {what}: `{LAW_DIR}/{f}`" + ("" if ok else f" — файла в библиотеке нет ({NO_DATA})") + "\n"
        tv = _rows(self.con, "SELECT name, document_ref, effective_from FROM tariff_versions WHERE level='регулятор'") \
            if _has_table(self.con, "tariff_versions") else []
        for r in tv:
            t += f"- В справочнике проекта: «{r['name']}» — {r['document_ref']}, действует с {date_ru(r['effective_from'])}.\n"
        t += ("- ГО владельцев опасных производственных объектов и обязательное страхование СМР — тарифные акты в "
              f"библиотеке проекта не загружены ({NO_DATA}).\n\n")
        t += caveats_md() + "\n" + S.footer()
        return t

    # ======================================================================= #
    # 6. Рыночные ставки
    # ======================================================================= #
    def _inson_min(self):
        if not _has_table(self.con, "min_rates"):
            return {}
        rs = _rows(self.con, "SELECT m.class_code c, MIN(m.min_rate_pct) lo, MAX(m.min_rate_pct) hi, COUNT(*) n "
                             "FROM min_rates m JOIN tariff_versions v ON v.id=m.tariff_version_id "
                             "WHERE v.level='компания' AND v.document_ref LIKE '%54-П%' GROUP BY m.class_code")
        return {r["c"]: r for r in rs}

    def rates(self):
        m, S, F = self.m, Sources(), self.facts
        L, FY = m.last, m.last_fy
        t = self.head("Рыночные ставки по классам", "Средняя рыночная ставка = премии / страховые обязательства — так же, "
                      "как считает рыночный ориентир экрана расчёта (app/market_picture.py, app/db.market_rates).")
        t += ("## Метод\n\n"
              "- Годовая ставка, % = премии с начала года × 12 / число месяцев среза / страховые обязательства на дату "
              "среза × 100.\n"
              "- Убыточность, % = выплаты с начала года / премии с начала года × 100.\n"
              "- Для классов 8 и 9 берётся пакет «8, 9»: одиночные строки этих классов на порядки меньше пакета.\n"
              "- Минимальный тариф INSON — из тарифной политики (приказ № 54-П от 23.09.2025, таблица min_rates): "
              "наименьшая и наибольшая минимальная ставка продуктов класса.\n\n")
        mins = self._inson_min()
        rows = []
        for c in [str(i) for i in range(1, MAX_CLASS + 1)]:
            rk = mp.CLASS_ROWS.get(c) or f"cls{c}"
            last, fy = m.get(L, rk), m.get(FY, rk) if FY else None
            rl = mp._annual_rate(last) if last else None
            rf = mp._annual_rate(fy) if fy else None
            lrf = loss_ratio(fy["premiums_ytd"], fy["payouts_ytd"]) if fy else None
            if last:
                S.add(last["source_file"], L)
            if fy:
                S.add(fy["source_file"], FY)
            mn = mins.get(c)
            mn_s = (fmt_rate(mn["lo"]) + (f" – {fmt_rate(mn['hi'])}" if mn["hi"] != mn["lo"] else "")) if mn else "нет продуктов в 54-П"
            note = "" if mn else "продуктов INSON в классе нет — сравнивать не с чем"
            if rf and mn:
                lo_x, hi_x = _grp(f"{mn['lo'] / rf:,.1f}"), _grp(f"{mn['hi'] / rf:,.1f}")
                note = f"мин. тариф INSON = {lo_x if lo_x == hi_x else lo_x + '–' + hi_x} × рыночная ставка за {period_label(FY)}"
            if rl is None and rf is None:
                note = ("ставка не считается: премий нет" if last and not last["premiums_ytd"]
                        else "ставка не считается: нет премий или обязательств")
            rows.append([c, self.class_names.get(c, ""), "пакет 8, 9" if "_" in rk else rk.replace("cls", "строка класса "),
                         fmt_rate(rl), fmt_rate(rf), lr_cell(fy["premiums_ytd"], fy["payouts_ytd"]) if fy else NO_DATA, mn_s, note])
            for d, rate, rr in ((L, rl, last), (FY, rf, fy)):
                if rate is not None:
                    F.add("ставки", d, "market_rate_annual", rate, "%", f"класс {c} — {self.class_names.get(c, '')}",
                          loss_ratio_pct=loss_ratio(rr["premiums_ytd"], rr["payouts_ytd"]), source_folder=rr["source_file"],
                          digits=3, note=f"строка НАПП {rk}; премии / обязательства, приведено к году")
        t += f"## Ставки по классам\n\n"
        t += table(["Класс", "Название", "Строка отчёта", f"Ставка, {period_label(L)} (годовая)",
                    f"Ставка, {period_label(FY) if FY else 'год'}", f"Убыточность, {period_label(FY) if FY else 'год'}",
                    "Минимальный тариф INSON (54-П)", f"Сравнение ({DEV})"], rows)
        t += ("\nСравнение минимального тарифа INSON с рыночной ставкой — грубое: рыночная ставка — среднее по всем "
              "договорам класса, а тариф INSON — по продуктам. Разница в разы сама по себе не значит, что тариф завышен "
              "или занижен: состав договоров рынка (крупные и мелкие, типы объектов) по открытым данным не виден "
              f"({DEV}).\n\n")
        # пакеты
        rows = []
        for k in self._pack_keys(L):
            last, fy = m.get(L, k), m.get(FY, k) if FY else None
            rl = mp._annual_rate(last) if last else None
            rf = mp._annual_rate(fy) if fy else None
            S.add(last["source_file"], L)
            rows.append(["Классы " + ", ".join(k[3:].split("_")), fmt_rate(rl), fmt_rate(rf),
                         lr_cell(fy["premiums_ytd"], fy["payouts_ytd"]) if fy else NO_DATA])
            if rl is not None:
                F.add("ставки", L, "market_rate_annual", rl, "%", "пакет " + ", ".join(k[3:].split("_")), digits=3,
                      loss_ratio_pct=loss_ratio(last["premiums_ytd"], last["payouts_ytd"]), source_folder=last["source_file"])
        t += "## Ставки по пакетам комплексного страхования\n\n"
        t += table(["Пакет", f"Ставка, {period_label(L)} (годовая)", f"Ставка, {period_label(FY) if FY else 'год'}",
                    f"Убыточность, {period_label(FY) if FY else 'год'}"], rows) + "\n"
        t += ("## Ограничения\n\n"
              "- Обязательства на дату включают договоры прошлых лет, а премии — только начисленные с начала года: для "
              "неполного года ставка приводится к году линейно (без сезонности), поэтому полугодовая ставка менее надёжна, "
              "чем годовая.\n"
              "- Многолетние договоры (кредиты, СМР, ипотека) занижают ставку: обязательства есть, а премия начислена "
              "раньше.\n"
              "- Ставка — средняя по рынку, без разделения на типы объектов, суммы и регионы; для калибровки базовых "
              "ставок INSON она годится только как ориентир (в базе такие значения не помечаются calibrated = 1).\n"
              "- Числа договоров и страховых случаев по классам для частоты и тяжести в отчёте нет — нетто-ставку "
              "T = q × k × 100 по этим данным посчитать нельзя.\n\n")
        t += caveats_md() + "\n" + S.footer()
        return t

    # ======================================================================= #
    # 7. Словарь
    # ======================================================================= #
    SHEET_RU = {
        "1.1": "структура рынка: страховщики, брокеры, актуарии, агенты, уставный капитал",
        "1.2": "инвестиционная деятельность страховщиков",
        "1.3": "сферы страхования: общее и жизнь, обязательное и добровольное",
        "1.4": "классы страхования: премии, выплаты, обязательства (главный лист для тарифа)",
        "1.5": "договоры по классам: действующие и новые",
        "1.6": "перестрахование у иностранных страховщиков",
        "2.1": "премии страховщиков общего страхования (брутто, нетто, переданные и принятые в перестрахование)",
        "2.2": "премии страховщиков жизни", "2.3": "обязательства страховщиков общего страхования",
        "2.4": "обязательства страховщиков жизни", "2.5": "выплаты страховщиков общего страхования",
        "2.6": "выплаты страховщиков жизни", "2.7": "договоры страховщиков общего страхования",
        "2.8": "договоры страховщиков жизни", "2.9": "территориальные подразделения страховщиков",
        "2.10": "претензии к страховщикам общего страхования", "2.11": "претензии к страховщикам жизни",
        "2.12": "премии по обособленным подразделениям (страховщик × регион)",
        "2.13": "выплаты по обособленным подразделениям", "2.14": "договоры по обособленным подразделениям",
        "3.1": "премии по регионам", "3.2": "выплаты по регионам", "3.3": "обязательства по регионам",
        "3.4": "договоры по регионам", "3.5": "претензии по регионам, общее страхование",
        "3.6": "претензии по регионам, страхование жизни"}

    def glossary(self):
        m, S = self.m, Sources()
        L = m.last
        tot = self.src_row(S, L, "total")
        b = m.base_date(L)
        tb = self.src_row(S, b, "total") if b else None
        t = self.head("Словарь рынка и как читать отчёт НАПП", "Термины, которые встречаются в заметках о рынке, и "
                      "устройство квартального отчёта НАПП.")
        ex = (f"Пример: срез {date_ru(L)} — премии рынка {fmt_mln(tot['premiums_ytd'])} млн сум; это сумма за "
              f"{period_label(L)}, а не за квартал.")
        if tb:
            ex += (f" Сравнивать её можно с {period_label(b)} ({fmt_mln(tb['premiums_ytd'])} млн сум), но не с полным "
                   "годом.")
        terms = [
            ("НАПП", "Национальное агентство перспективных проектов — регулятор страхового рынка Узбекистана; "
                     "публикует квартальный отчёт о рынке (napp.uz, раздел статистики)."),
            ("Срез", "дата, на которую составлен отчёт (01.01, 31.03 или 01.04, 01.07, 01.10). Срез 01.01 — итог "
                     "прошлого года."),
            ("ytd (с начала года)", "нарастающий итог с 1 января до даты среза. Премии и выплаты в отчёте НАПП — ytd. " + ex),
            ("Сопоставимый период", "тот же период прошлого года: I полугодие к I полугодию, 9 месяцев к 9 месяцам, "
                                    "год к году. Рост «год к году» (г/г) считается только так."),
            ("Страховые премии (брутто)", "начисленные премии до передачи в перестрахование. Нетто-премии — после "
                                           "передачи (лист 2.1)."),
            ("Страховые выплаты", "оплаченные за период суммы, в том числе по договорам и убыткам прошлых лет."),
            ("Страховые обязательства", "сумма ответственности по действующим договорам на дату среза (сколько "
                                        "страховщики могут максимально выплатить)."),
            ("Убыточность брутто", "выплаты / премии × 100 за один и тот же период, без учёта резервов и "
                                   "перестрахования. Это «кассовая» убыточность, не равная убыточности по "
                                   "произошедшим убыткам."),
            ("Рыночная ставка", "премии (приведённые к году) / страховые обязательства × 100 — средняя цена 100 сум "
                                "ответственности на рынке."),
            ("Класс страхования", "вид деятельности по классификатору (ПКМ № 80, прил. 6): 18 классов общего "
                                  "страхования и 7 классов (I–VII) страхования жизни."),
            ("Пакет классов (комплексное страхование)", "договор сразу по нескольким классам; в отчёте — строки "
                                                        "«3,8,9 klasslar», «8,9 klasslar» и т.д. Премия пакета между "
                                                        "классами не делится."),
            ("Общая сфера и сфера жизни", "отчёт показывает страховщиков общего страхования и страховщиков жизни "
                                          "раздельно (например, премии — листы 2.1 и 2.2)."),
            ("Обязательное страхование", "виды страхования, обязательные по закону; тарифы ОСАГО, ОСГОР и ОСГО "
                                         "перевозчика утверждены постановлениями Кабинета Министров (ПКМ № 141, 177, 266)."),
            ("РНП", "резерв незаработанной премии — часть начисленной премии, приходящаяся на ещё не истёкший срок "
                    "договора. В отчёте НАПП резервов нет."),
            ("Учётная группа", "группа классов по Положению № 1882 (п. 10), от которой зависит способ расчёта РНП: " +
             "; ".join(f"{r['name']} — {r['rnp_method']}" for r in _rows(self.con, "SELECT name, rnp_method FROM groups ORDER BY code"))
             if _has_table(self.con, "groups") else "группа классов по Положению № 1882 для расчёта РНП."),
            ("Претензия (da'vo)", "заявление о страховой выплате; в отчёте — поступило, оплачено, отказано, "
                                  "не урегулировано (листы 2.10 и 3.5)."),
            ("Обособленное подразделение", "филиал или отделение страховщика в регионе (листы 2.9, 2.12–2.14)."),
            ("Перестрахование", "передача части риска и премии другому страховщику; лист 1.6 — с иностранными "
                                "перестраховщиками."),
        ]
        t += "## Термины\n\n" + "".join(f"- **{a}** — {b_}\n" for a, b_ in terms) + "\n"
        t += "## Как устроен отчёт НАПП\n\n"
        t += ("Отчёт — книга Excel с тремя разделами: 1 — рынок в целом, 2 — по страховщикам, 3 — по регионам. "
              "В каждом листе две даты: текущий срез и тот же срез год назад, плюс изменение в процентах. Суммы — "
              "млн сум, договоры и претензии — штуки.\n\n")
        sh = self.ex.get("sheets") or []
        if sh:
            t += table(["Лист", "Что в нём", "Название в отчёте"],
                       [[c, self.SHEET_RU.get(c, NO_DATA), u] for c, u in sh]) + "\n"
        else:
            t += f"Перечень листов — {NO_DATA}.\n\n"
        t += ("## Как читать — короткие правила\n\n"
              "1. Сравнивать только одинаковые периоды разных лет (ytd).\n"
              "2. Для классов 8 и 9 смотреть пакеты: основная часть имущества продаётся в пакетах.\n"
              "3. Убыточность одного среза по редким рискам — шум; смотреть несколько лет.\n"
              "4. Регионы — по месту головного офиса страховщика, не по месту объекта.\n"
              "5. Разрезов «страховщик × класс» и «регион × класс» в отчёте нет — такие выводы из него не делаются.\n"
              "6. Новый отчёт может уточнить прошлогодний срез — в базе берётся значение из более позднего отчёта.\n\n")
        t += S.footer()
        return t

    # ======================================================================= #
    def build(self):
        docs = {
            "01 Рынок в целом.md": self.market(),
            "02 Классы страхования.md": self.classes(),
            "03 Страховые компании.md": self.companies(),
            "04 Регионы.md": self.regions(),
            "05 Обязательные виды.md": self.mandatory(),
            "06 Рыночные ставки по классам.md": self.rates(),
            "07 Словарь рынка.md": self.glossary(),
        }
        return docs, self.facts.items


# --------------------------------------------------------------------------- #
# Точка входа
# --------------------------------------------------------------------------- #


def snapshot(src: Path, dst: Path):
    """Копия базы средствами SQLite (источник открывается только на чтение)."""
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def _write_atomic(path: Path, text: str):
    """Сервер читает заметки и факты, пока они пересобираются: пишем рядом и подменяем одним шагом."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _ensure_ranking(con):
    """Рэнкинга нет в базе-источнике (сервер ещё не перезапускался) — грузим его в копию из текста в library.
    Рабочая база не трогается: con — соединение с копией."""
    try:
        import tools.ranking_parse as rp
        if rp.source_file() and rp.needs_load(con):
            con.executescript((ROOT / "db" / "schema.sql").read_text(encoding="utf-8")) \
                if not _has_table(con, "company_rankings") else None
            cols = {r[1] for r in con.execute("PRAGMA table_info(company_financials)")}
            for name, decl in appdb.ADDED_COLUMNS.get("company_financials", []):
                if cols and name not in cols:
                    con.execute(f"ALTER TABLE company_financials ADD COLUMN {name} {decl}")
            rp.load(con)
            con.commit()
    except Exception as e:                      # нет файла или не разобрался — заметка честно скажет «нет»
        print("рэнкинг snsratings в копию не загружен:", e)


def generate(db_path: Path | None = None, out_dir: Path = OUT_DIR, json_path: Path = JSON_PATH, rebuild: bool = True,
             parsed: Path | None = None, work_db: Path | None = None):
    """Собирает заметки и факты. Возвращает (docs, facts, путь к использованной копии базы).
    work_db — куда положить копию (оставляется); без него копия во временной папке и удаляется."""
    src = Path(db_path) if db_path else appdb.DB_PATH
    parsed = Path(parsed) if parsed else ms.PARSED
    tmpdir = None
    if work_db is None:
        tmpdir = Path(tempfile.mkdtemp(prefix="market_knowledge_"))
        work = tmpdir / "work.db"
    else:
        work = Path(work_db)
    snapshot(src, work)
    try:
        if rebuild:
            old_db, old_parsed = ms.DB, ms.PARSED
            ms.DB, ms.PARSED = str(work), parsed
            try:
                ms.build()
            finally:
                ms.DB, ms.PARSED = old_db, old_parsed
        con = sqlite3.connect(str(work))
        try:
            _ensure_ranking(con)
            docs, facts = Builder(con, parsed).build()
            dates = [r[0] for r in con.execute("SELECT DISTINCT report_date FROM market_stats ORDER BY 1")]
        finally:
            con.close()
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, text in docs.items():
            _write_atomic(out_dir / name, text)
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "generator": "tools/market_knowledge.py",
            "slices": dates,
            "units": {"money": "млн сум", "percent": "%", "rate": "% годовых, три знака"},
            "fields": {"period": "период нарастающего итога (ytd) по дате среза", "share_pct": "доля, %",
                       "yoy_pct": "рост к тому же периоду прошлого года, %", "yoy_base_date": "дата среза базы роста",
                       "rank": "место по премиям", "source_date": "дата среза"},
            "caveats": CAVEATS,
            "facts": facts,
        }
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(Path(json_path), json.dumps(payload, ensure_ascii=False, indent=1))
        return docs, facts, work
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--db", help="база-источник (только чтение)")
    ap.add_argument("--no-rebuild", action="store_true", help="не пересобирать статистику на копии")
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--json", default=str(JSON_PATH))
    a = ap.parse_args(argv)
    docs, facts, _ = generate(a.db, Path(a.out), Path(a.json), rebuild=not a.no_rebuild)
    for n in docs:
        print("записано:", Path(a.out) / n)
    print(f"фактов: {len(facts)} → {a.json}")


if __name__ == "__main__":
    main()
