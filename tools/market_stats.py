"""
Временной ряд рыночной статистики из отчётов НАПП.

Берёт лист 1.4 (классы страхования) из каждого разобранного отчёта в data/parsed,
раскладывает по датам среза и пишет в таблицу market_stats. Повторный запуск безопасен:
уже загруженные точки перезаписываются теми же значениями.

Запуск:  python tools/market_stats.py
Полный цикл (скачать новое → разобрать → загрузить):  python tools/market_stats.py --refresh
"""
import csv
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "surveyor.db"
PARSED = ROOT / "data" / "parsed"

# строки отчёта, которым даём устойчивые ключи (остальные получают ключ из названия)
KEYS = [
    (r"^Jami", "total", "Весь рынок"),
    (r"^Majburiy", "mandatory", "Обязательное страхование"),
    (r"^Klasslar bo.yicha ixtiyoriy.*umumiy", "voluntary_general", "Добровольное, общее страхование"),
    (r"^Klasslar bo.yicha ixtiyoriy.*hayot", "voluntary_life", "Добровольное, страхование жизни"),
    (r"^qurilish-montaj", "cmr_mandatory", "СМР обязательное"),
    # ОСГОР — обязательное страхование гражданской ответственности работодателя (ЗРУ-616, ПКМ-573).
    # В отчёте НАПП это две отдельные строки внутри блока «Majburiy sug'urta»; аннуитетная идёт первой,
    # поэтому её образец должен стоять выше общего — иначе общий перехватит обе.
    (r"^ish beruvchining fuqarolik javobgarligi \(annuitet\)", "osgor_annuity",
     "ОСГОР — аннуитеты (обязательное страхование ГО работодателя, выплаты рентой)"),
    (r"^ish beruvchining fuqarolik javobgarligi\s*$", "osgor",
     "ОСГОР — ГО работодателя (обязательное)"),
    (r"^transport vositalari egalarining fuqarolik javobgarligi", "osago", "ОСАГО — ГО владельцев транспортных средств (обязательное)"),
    (r"^(\d+)-klass", None, None),                 # cls<N>
    (r"^\"?([\d,]+) klasslar", None, None),        # пакет классов: cls8_9, cls8_9_13
]

# Русские названия классов — из справочника classes в базе (Закон о страховой деятельности)
def _class_names():
    try:
        con = sqlite3.connect(DB)
        d = {r[0]: r[1] for r in con.execute("SELECT code, name FROM classes")}
        con.close()
        return d
    except Exception:
        return {}


CLASS_NAMES = {**{"18": "Медицинское страхование"}, **_class_names()}
SHORT = {"1": "НС", "2": "болезнь", "3": "транспорт", "4": "ж/д", "5": "авиа", "6": "море", "7": "грузы",
         "8": "огонь и стихия", "9": "ущерб", "10": "автогражданка", "11": "авиа-ответств.", "12": "морская ответств.",
         "13": "гражд. ответств.", "14": "кредиты", "15": "гарантии", "16": "финриски", "17": "правовая защита", "18": "медицина"}


def class_row_name(key: str, raw: str) -> str:
    """cls8 → «Класс 8 — Имущество от огня и стихийных бедствий»; cls8_9 → «Классы 8, 9 — огонь и стихия + ущерб»."""
    nums = key[3:].split("_")
    if len(nums) == 1:
        n = nums[0]
        return f"Класс {n} — {CLASS_NAMES.get(n) or raw.split('–', 1)[-1].strip()}"
    return f"Классы {', '.join(nums)} — " + " + ".join(SHORT.get(n, n) for n in nums)


def row_key(name: str):
    n = name.strip().strip('"')
    for pat, key, ru in KEYS:
        m = re.match(pat, n, re.I)
        if not m:
            continue
        if key:
            return key, ru
        if m.re.pattern.startswith("^(\\d+)"):
            k = f"cls{m.group(1)}"
        else:
            k = "cls" + "_".join(x.strip() for x in m.group(1).split(","))
        return k, class_row_name(k, n)
    return None, None


def num(s):
    s = (s or "").strip()
    if not s or s.startswith(">"):
        return None
    try:
        return float(s.replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def load_sheet(path: Path):
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    hdr = rows[3]
    dates = [hdr[1][:10], hdr[2][:10]]
    out = []
    for r in rows[5:]:
        if len(r) < 9 or not r[0].strip():
            continue
        key, ru = row_key(r[0])
        if not key:
            continue
        # колонки: 1,2 премии (прошлый год, текущий); 4,5 выплаты; 7,8 обязательства
        for i, d in enumerate(dates):
            out.append((d, key, ru, num(r[1 + i]), num(r[4 + i]), num(r[7 + i])))
    return out



# ---------------------------------------------------------------------------
# Разрезы «регионы» (листы 3.1/3.2/3.3) и «страховщики» (листы 2.1/2.5/2.3)
# ---------------------------------------------------------------------------

DATE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})")
APOSTROPHES = "‘’ʻʼ`´′'ʹ"
QUOTES = '"«»“”„‟″'

RU_REGIONS = {
    "QORAQALPOG'ISTON RESPUBLIKASI": "Республика Каракалпакстан",
    "ANDIJON": "Андижанская область", "BUXORO": "Бухарская область",
    "JIZZAX": "Джизакская область", "QASHQADARYO": "Кашкадарьинская область",
    "NAVOIY": "Навоийская область", "NAMANGAN": "Наманганская область",
    "SAMARQAND": "Самаркандская область", "SURXANDARYO": "Сурхандарьинская область",
    "SIRDARYO": "Сырдарьинская область", "TOSHKENT": "Ташкентская область",
    "FARG'ONA": "Ферганская область", "XORAZM": "Хорезмская область",
    "TOSHKENT SHAHRI": "город Ташкент",
}


def norm_name(s: str) -> str:
    """Нормализованный ключ названия: без кавычек, сносок и разнобоя апострофов, в верхнем регистре."""
    n = (s or "").strip()
    n = re.sub(r"^\*+\s*", "", n)                     # сноски вида **"OMAD SUG'URTA" AJ
    for q in QUOTES:
        n = n.replace(q, " ")
    for a in APOSTROPHES:
        n = n.replace(a, "'")
    n = re.sub(r"\s+", " ", n).strip(" '")
    return n.upper()


def clean_title(s: str) -> str:
    """Человекочитаемое название — как в отчёте, но без сносок и обрамляющих кавычек."""
    n = re.sub(r"^\*+\s*", "", (s or "").strip())
    n = re.sub(r"\s+", " ", n)
    return n.strip()


def find_header(rows):
    """Находит строку с датами (не по фиксированному индексу) и позиции дат в ней."""
    for i, r in enumerate(rows):
        cols = [j for j, c in enumerate(r) if DATE_RE.match(c or "")]
        if len(cols) >= 2:
            return i, cols
    return None, []


def date_blocks(cols):
    """Группирует колонки дат в блоки подряд идущих (каждый блок — один показатель)."""
    out, cur = [], [cols[0]]
    for c in cols[1:]:
        if c == cur[-1] + 1:
            cur.append(c)
        else:
            out.append(cur)
            cur = [c]
    out.append(cur)
    return out


def pick_block(rows, hdr_i, cols, caption=None):
    """Выбирает блок колонок: по надписи над датами (если задана) или первый блок."""
    blocks = date_blocks(cols)
    if caption:
        above = rows[hdr_i - 1] if hdr_i > 0 else []
        for b in blocks:
            cap = norm_name(above[b[0]]) if b[0] < len(above) else ""
            if re.search(caption, cap, re.I):
                return b
    return blocks[0]


def read_matrix(path, caption=None):
    """Читает лист «строки × даты» → {ключ: (название, {дата: значение})}. Нет листа — None."""
    if not path.exists():
        return None
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    hdr_i, cols = find_header(rows)
    if hdr_i is None:
        return None
    block = pick_block(rows, hdr_i, cols, caption)
    dates = [DATE_RE.match(rows[hdr_i][j]).group(1) for j in block]
    data = {}
    for r in rows[hdr_i + 1:]:
        if len(r) < 2:
            continue
        title = clean_title(r[1])
        if not title or not re.match(r"^\d+$", (r[0] or "").strip()):
            continue                                   # итог «Umumiy», шапки и сноски — без номера
        key = norm_name(title)
        if not key or key == "UMUMIY":
            continue
        data.setdefault(key, (title, {}))
        for j, d in zip(block, dates):
            v = num(r[j]) if j < len(r) else None
            if v is not None:
                data[key][1][d] = v
    return data


def load_cut(folder: Path, prefix: str, sheets, ru_map=None, renames=None):
    """Собирает один разрез (регионы или компании) из трёх листов одного отчёта."""
    prem = read_matrix(folder / sheets[0][0], sheets[0][1])
    pays = read_matrix(folder / sheets[1][0], sheets[1][1])
    liab = read_matrix(folder / sheets[2][0], sheets[2][1])
    missing = [s[0] for s, m in zip(sheets, (prem, pays, liab)) if m is None]
    out = []
    keys = set()
    for m in (prem, pays, liab):
        if m:
            keys |= set(m)
    for k in sorted(keys):
        title = next((m[k][0] for m in (prem, pays, liab) if m and k in m), k)
        name = (ru_map or {}).get(k) or title
        dates = set()
        for m in (prem, pays, liab):
            if m and k in m:
                dates |= set(m[k][1])
        out_key, marked = (renames or {}).get(k, (k, None))
        if marked:
            name = marked
        for d in sorted(dates):
            g = lambda m: (m[k][1].get(d) if (m and k in m) else None)
            out.append((d, f"{prefix}:{out_key}", name, g(prem), g(pays), g(liab)))
    return out, missing


# ---------------------------------------------------------------------------
# Переименования страховщиков
# ---------------------------------------------------------------------------
# Один и тот же страховщик появляется в отчётах НАПП под старым и новым названием:
# в старых отчётах — прежнее имя, в свежих — новое. Без склейки строка задваивается
# (на срезе 01.07.2025 сумма премий компаний давала 5 970 356,454 при итоге «Umumiy»
# листа 2.1 = 5 850 637,412; разница 119 719,041 = ровно две задвоенные компании).
#
# ОСНОВАНИЕ СКЛЕЙКИ — совпадение чисел, а не документ регулятора. На общем срезе
# 01.07.2025, который есть сразу в двух отчётах (II квартал 2025 — старое имя,
# II квартал 2026 — новое), у пары побайтово совпадают все три показателя:
# премии, выплаты и страховые обязательства. Это наша реконструкция; официального
# подтверждения НАПП или регулятора о смене наименования в наших источниках нет.
# Совпадение проверялось только по этим двум парам; прочие выбывшие компании
# (UNIPOLIS, UNIVERSAL SUG'URTA, DD GENERAL INSURANCE, OMAD SUG'URTA) ни с одним
# новым названием по числам не совпали и НЕ склеиваются.
COMPANY_RENAMES = {
    # старый нормализованный ключ: (новый ключ, подпись строки)
    "INGO-UZBEKISTON AJ": (
        "QUANTUM INSURANCE AJ",
        '"QUANTUM INSURANCE" AJ (ранее "INGO-UZBEKISTON")'),
    "MOSAIC INSURANCE COMPANY AJ QK": (
        "MOSAIC INSURANCE GROUP AJ QK",
        '"MOSAIC INSURANCE GROUP" AJ QK (ранее "MOSAIC INSURANCE COMPANY")'),
}
# та же подпись должна стоять и у строк, пришедших уже под новым названием
COMPANY_RENAMES.update({new: (new, name) for new, name in COMPANY_RENAMES.values()})


REGION_SHEETS = (("3.1.csv", None), ("3.2.csv", None), ("3.3.csv", None))
COMPANY_SHEETS = (("2.1.csv", r"UMUMIY SUG'URTA MUKOFOTLARI"), ("2.5.csv", None), ("2.3.csv", None))


def build():
    con = sqlite3.connect(DB)
    con.executescript((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
    n = 0
    warn = []
    now = datetime.now().isoformat(timespec="seconds")
    for sheet in sorted(PARSED.glob("*/1.4.csv")):
        src = sheet.parent.name
        for d, key, ru, prem, pay, liab in load_sheet(sheet):
            con.execute("INSERT OR REPLACE INTO market_stats VALUES (?,?,?,?,?,?,?,?)",
                        (d, key, ru, prem, pay, liab, src, now))
            n += 1
    # строки, оставшиеся от прежней загрузки под старым названием страховщика
    for k, (new_key, _) in COMPANY_RENAMES.items():
        if k != new_key:
            con.execute("DELETE FROM market_stats WHERE row_key=?", (f"company:{k}",))
    # разрезы «регионы» и «страховщики»
    for folder in sorted(p for p in PARSED.iterdir() if p.is_dir()):
        for prefix, sheets, ru_map, renames in (("region", REGION_SHEETS, RU_REGIONS, None),
                                                ("company", COMPANY_SHEETS, None, COMPANY_RENAMES)):
            rows, missing = load_cut(folder, prefix, sheets, ru_map, renames)
            for m in missing:
                warn.append(f"{folder.name}: нет листа {m} — разрез {prefix} неполный")
            for d, key, name, prem, pay, liab in rows:
                con.execute("INSERT OR REPLACE INTO market_stats VALUES (?,?,?,?,?,?,?,?)",
                            (d, key, name, prem, pay, liab, folder.name, now))
                n += 1
    for w in warn:
        print("ВНИМАНИЕ:", w)
    con.commit()
    dates = [r[0] for r in con.execute("SELECT DISTINCT report_date FROM market_stats ORDER BY 1")]
    # n — число операций INSERT OR REPLACE (срезы пересекаются между отчётами и
    # перезаписываются), поэтому наружу отдаём фактическое число строк таблицы
    total = con.execute("SELECT COUNT(*) FROM market_stats").fetchone()[0]
    con.close()
    return total, dates


def refresh():
    """Скачать новые отчёты, разобрать, загрузить статистику. Возвращает краткий отчёт."""
    py = sys.executable
    log = []
    for script in ("napp_download.py", "inspect_excel.py"):
        r = subprocess.run([py, str(ROOT / "tools" / script)], capture_output=True, text=True,
                           env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
        log.append(f"{script}: {(r.stdout or r.stderr).strip()[-300:]}")
    PARSED.mkdir(parents=True, exist_ok=True)
    n, dates = build()
    if dates:
        log.append(f"market_stats: строк в таблице {n}, даты {dates[0]}…{dates[-1]} ({len(dates)} срезов)")
    else:
        log.append("market_stats: разобранных отчётов нет — проверьте data/inbox и вывод inspect_excel.py выше")
    return log


# ---------------------------------------------------------------------------
# Выгрузка по ОСГОР: деньги (лист 1.4) + число договоров (лист 1.5)
# ---------------------------------------------------------------------------
# Числа договоров в market_stats не хранятся (в таблице только премии, выплаты и
# обязательства), поэтому лист 1.5 читается отдельно и отдаётся файлом для заказчика.
OSGOR_ROWS = {"osgor": "ОСГОР — ГО работодателя (обязательное)",
              "osgor_annuity": "ОСГОР — аннуитеты"}


def contracts_15():
    """Число договоров по строкам ОСГОР из листа 1.5: {(ключ, дата): (действующие, новые)}."""
    out = {}
    for f in sorted(PARSED.glob("*/1.5.csv")):
        rows = list(csv.reader(open(f, encoding="utf-8-sig")))
        hdr_i, cols = find_header(rows)
        if hdr_i is None:
            continue
        sub = rows[hdr_i + 1]                      # подзаголовок: «действующие» / «новые»
        for r in rows[hdr_i + 2:]:
            n = (r[0] or "").strip().lower()
            if not n.startswith("ish beruvchining"):
                continue
            key = "osgor_annuity" if "annuitet" in n else "osgor"
            for j in cols:
                d = DATE_RE.match(rows[hdr_i][j]).group(1)
                v = num(r[j]) if j < len(r) else None
                if v is None:
                    continue
                act, new = out.get((key, d), (None, None))
                if "Amalda" in (sub[j] if j < len(sub) else ""):
                    act = int(v)
                else:
                    new = int(v)
                out[(key, d)] = (act, new)
    return out


def osgor_csv():
    """docs/ОСГОР — статистика рынка (НАПП).csv: по срезам, деньги + договоры + убыточность."""
    con = sqlite3.connect(DB)
    money = {(r[1], r[0]): r[2:] for r in con.execute(
        "SELECT report_date, row_key, premiums_ytd, payouts_ytd, liabilities, source_file "
        "FROM market_stats WHERE row_key IN ('osgor','osgor_annuity')")}
    con.close()
    cnt = contracts_15()
    path = ROOT / "docs" / "ОСГОР — статистика рынка (НАПП).csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["ряд", "строка отчёта", "дата среза", "период (нарастающим итогом)",
                    "премии, млн сум", "выплаты, млн сум", "страховые обязательства, млн сум",
                    "убыточность, % (выплаты/премии)", "договоров действующих на дату, шт.",
                    "договоров заключено за период, шт.", "источник (разобранный отчёт)"])
        period = {"03-31": "3 мес.", "04-01": "3 мес.", "07-01": "6 мес.",
                  "10-01": "9 мес.", "01-01": "12 мес. (год завершён)"}
        for key, name in OSGOR_ROWS.items():
            for (k, d), (prem, pay, liab, src) in sorted(money.items()):
                if k != key:
                    continue
                yr = int(d[:4]) - 1 if d[5:] == "01-01" else int(d[:4])
                act, new = cnt.get((key, d), (None, None))
                lr = (pay / prem * 100) if prem else None
                w.writerow([key, name, d, f"{yr}, {period.get(d[5:], d)}",
                            f"{prem:.3f}" if prem is not None else "",
                            f"{pay:.3f}" if pay is not None else "",
                            f"{liab:.3f}" if liab is not None else "",
                            f"{lr:.3f}" if lr is not None else "",
                            act if act is not None else "",
                            new if new is not None else "", src])
    return path


if __name__ == "__main__":
    if "--osgor" in sys.argv:
        print("Файл по ОСГОР:", osgor_csv())
    elif "--refresh" in sys.argv:
        print("\n".join(refresh()))
    else:
        n, dates = build()
        print(f"Строк в таблице market_stats: {n}; даты срезов: {', '.join(dates)}")
