"""
Временной ряд рыночной статистики из отчётов НАПП.

Берёт лист 1.4 (классы страхования) из каждого разобранного отчёта в data/parsed,
раскладывает по датам среза и пишет в таблицу market_stats. Повторный запуск безопасен:
уже загруженные точки перезаписываются теми же значениями.

С 01.10.2026 тем же проходом загружаются:
  * лист 1.4, блок «Ikki va undan ortiq klasslar bo'yicha sug'urta» (комплексное страхование): пакеты классов —
    ключи cls3_8_9, cls8_9, cls3_14 …; итог блока — multi_general (общее), multi_life (жизнь); «boshqalar»
    блока — multi_other; сверка «сумма пакетов = итог» (multi_mismatch): если итог отчёта не равен сумме пакетов
    (IV кв. 2025 — строка итога не обновлена, совпадает с III кв.), multi_general заменяется суммой пакетов и
    «boshqalar», пометка — в таблице market_stats_notes (видна в /stats и в CSV-выгрузках);
  * отчёты загружаются по дате среза (не по имени папки): общий срез двух отчётов берётся из более позднего;
  * претензии и договоры → napp_claims: регионы — 3.5 (da'volar) + 3.4 (shartnomalar) + 3.2 (to'lovlar, общее),
    страховщики — 2.10 + 2.7 + 2.5;
  * обособленные подразделения (alohida bo'linmalar) → napp_branches: 2.12 премии, 2.13 выплаты, 2.14 договоры.
Нет листа в отчёте — строки этого разреза не пишутся, причина печатается («ВНИМАНИЕ: …»).

Запуск:  python tools/market_stats.py
Полный цикл (скачать новое → разобрать → загрузить):  python tools/market_stats.py --refresh
Файлы для заказчика (docs/НАПП — …csv):  python tools/market_stats.py --napp
"""
import csv
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app import db as appdb  # noqa: E402

# База — та же, что у сервера (app/db.py): с STORAGE_DIR это постоянный диск, без него — data/.
# Раньше путь был жёстко data/surveyor.db, и на Railway рыночная статистика писалась не в ту базу.
# DB = None — «как у сервера»; присвоить путь можно явно (tools/perf_check.py пишет в копию).
DB = None
PARSED = ROOT / "data" / "parsed"
STEP_TIMEOUT_SEC = 900            # скачивание и разбор отчётов НАПП: дольше — процесс останавливаем


def db_path() -> Path:
    return Path(DB) if DB else appdb.DB_PATH


def _connect():
    """Соединение с настройками сервера (WAL, ожидание блокировки 5 с), строки — кортежи."""
    return appdb.connect_path(db_path(), row_factory=False)

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

# Комплексное страхование (01.10.2026): блок «Ikki va undan ortiq klasslar bo'yicha sug'urta» листа 1.4.
# Строка-итог блока встречается дважды — в добровольном общем страховании и в страховании жизни, поэтому
# ключ зависит от блока, в котором стоит строка (load_sheet следит за блоком). «boshqalar» после итога
# комплексного — прочие пакеты, которые НАПП отдельной строкой не выделил.
MULTI_KEYS = {
    "general": ("multi_general", "Комплексное страхование — два и более класса (итог, добровольное общее)"),
    "life": ("multi_life", "Комплексное страхование жизни — два и более класса (итог)"),
}
MULTI_OTHER = ("multi_other", "Комплексное страхование — прочие пакеты классов (boshqalar)")

# Русские названия классов — из справочника classes в базе (Закон о страховой деятельности)
def _class_names():
    try:
        con = sqlite3.connect(str(db_path()))
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


def _block_of(name: str, block):
    """Блок листа 1.4, в котором стоит строка: mandatory | general | life (по строкам-итогам блоков)."""
    n = name.strip().strip('"')
    if re.match(r"^Majburiy", n, re.I):
        return "mandatory"
    if re.match(r"^Klasslar bo.yicha ixtiyoriy.*umumiy", n, re.I):
        return "general"
    if re.match(r"^Klasslar bo.yicha ixtiyoriy.*hayot", n, re.I):
        return "life"
    return block


def load_sheet(path: Path):
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    hdr = rows[3]
    dates = [hdr[1][:10], hdr[2][:10]]
    out = []
    block, in_multi = None, False
    for r in rows[5:]:
        if len(r) < 9 or not r[0].strip():
            continue
        nb = _block_of(r[0], block)
        if nb != block:
            block, in_multi = nb, False
        n = r[0].strip().strip('"')
        if re.match(r"^Ikki va undan ortiq", n, re.I) and block in MULTI_KEYS:
            key, ru = MULTI_KEYS[block]
            in_multi = block == "general"
        elif in_multi and re.match(r"^boshqalar", n, re.I):
            key, ru = MULTI_OTHER
        else:
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


# ---------------------------------------------------------------------------
# Претензии и договоры (листы 3.5/3.4/3.2 — регионы; 2.10/2.7/2.5 — страховщики), 01.10.2026
# ---------------------------------------------------------------------------
# Лист претензий: над датами — по семь колонок на срез; подписи колонок — во второй строке под датами:
#   «Kelib tushgan sug'urta da'volarning umumiy soni» — поступило претензий (заявлено);
#   «To'langan» — оплачено; «Rad etilgan» — отказано; «Qoniqtirilmagan sug'urta da'volari soni» —
#   не удовлетворено (не урегулировано на дату); «umumiyga nisbatan % da» — доля от поступивших (не берём:
#   считаем сами). Заявлено = оплачено + отказано + не урегулировано (проверено по итогу «Umumiy»).
CLAIM_COLS = ((r"^KELIB TUSHGAN", "received"), (r"^TO'LANGAN", "paid"), (r"^RAD ETILGAN", "refused"),
              (r"^QONIQTIRILMAGAN", "unsettled"))
CLAIM_SHEETS = {"region": ("3.5.csv", "3.4.csv", ("3.2.csv", r"UMUMIY SUG'URTA SOHASI")),
                "company": ("2.10.csv", "2.7.csv", ("2.5.csv", None))}
BRANCH_SHEETS = (("2.12.csv", "premiums_mln"), ("2.13.csv", "payouts_mln"), ("2.14.csv", "contracts"))


def _row_key_of(r, total_ok=True):
    """(нормализованный ключ, название) строки разреза: номер в первой колонке или итог «Umumiy»."""
    if len(r) < 3:
        return None, None
    title = clean_title(r[1])
    if not title:
        return None, None
    if norm_name(title) == "UMUMIY":
        return ("__TOTAL__", "Umumiy") if total_ok else (None, None)
    if re.match(r"^\d+$", (r[0] or "").strip()):
        return norm_name(title), title
    return None, None


def read_claims(path: Path):
    """Лист претензий → {ключ: (название, {дата: {received, paid, refused, unsettled}})}. Нет листа — None."""
    if not path.exists():
        return None
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    hdr_i, cols = find_header(rows)
    if hdr_i is None:
        return None
    labels = rows[hdr_i + 2] if hdr_i + 2 < len(rows) else []
    fields = {}
    for j in cols:
        lab = norm_name(labels[j]) if j < len(labels) else ""
        for pat, f in CLAIM_COLS:
            if re.match(pat, lab):
                fields[j] = (DATE_RE.match(rows[hdr_i][j]).group(1), f)
                break
    out = {}
    for r in rows[hdr_i + 3:]:
        key, title = _row_key_of(r)
        if not key:
            continue
        rec = out.setdefault(key, (title, {}))[1]
        for j, (d, f) in fields.items():
            v = num(r[j]) if j < len(r) else None
            if v is not None:
                rec.setdefault(d, {})[f] = v
    return out


def read_contracts(path: Path):
    """Договоры по общему страхованию → {ключ: (название, {дата: (действующие, новые)})}. Нет листа — None.
    Лист 3.4 — три группы колонок («Umumiy shartnomalar soni» всего, «Umumiy sug'urta sohasi» общее,
    «Hayotni sug'urta qilish sohasi» жизнь) — берём общее; лист 2.7 — только общее страхование
    (подписи «Amalda bo'lgan» / «Yangi tuzilgan» стоят под датами, а не над ними)."""
    if not path.exists():
        return None
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    hdr_i, cols = find_header(rows)
    if hdr_i is None:
        return None
    kinds = rows[hdr_i - 1] if hdr_i > 0 else []
    below = rows[hdr_i + 1] if hdr_i + 1 < len(rows) else []
    if any(norm_name(below[j]).startswith("AMALDA") for j in cols if j < len(below)):
        kinds = below
    sphere = rows[hdr_i - 2] if hdr_i > 1 else []
    # строка сфер есть только в 3.4; у 2.7 двумя строками выше — заголовок листа (в нём тоже «sohasi»)
    has_sphere = any(norm_name(c).startswith(("UMUMIY SUG'URTA SOHASI", "HAYOTNI")) for c in sphere)
    want = {}
    for j in cols:
        if has_sphere and not norm_name(sphere[j] if j < len(sphere) else "").startswith("UMUMIY SUG'URTA SOHASI"):
            continue
        k = norm_name(kinds[j] if j < len(kinds) else "")
        what = "active" if k.startswith("AMALDA") else ("new" if k.startswith("YANGI") else None)
        if what:
            want[j] = (DATE_RE.match(rows[hdr_i][j]).group(1), what)
    out = {}
    for r in rows[hdr_i + 1:]:
        key, title = _row_key_of(r)
        if not key:
            continue
        rec = out.setdefault(key, (title, {}))[1]
        for j, (d, what) in want.items():
            v = num(r[j]) if j < len(r) else None
            if v is None:
                continue
            a, n = rec.get(d, (None, None))
            rec[d] = (v, n) if what == "active" else (a, v)
    return out


def read_payouts(path: Path, caption):
    """Выплаты (млн сум) с итогом «Umumiy» → {ключ: {дата: значение}}. Нет листа — None."""
    if not path.exists():
        return None
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    hdr_i, cols = find_header(rows)
    if hdr_i is None:
        return None
    block = pick_block(rows, hdr_i, cols, caption)
    dates = [DATE_RE.match(rows[hdr_i][j]).group(1) for j in block]
    out = {}
    for r in rows[hdr_i + 1:]:
        key, _t = _row_key_of(r)
        if not key:
            continue
        for j, d in zip(block, dates):
            v = num(r[j]) if j < len(r) else None
            if v is not None:
                out.setdefault(key, {})[d] = v
    return out


def _out_key(scope, key, ru_map=None, renames=None):
    if key == "__TOTAL__":
        return "total", ("Весь рынок" if scope == "company" else "Республика Узбекистан")
    new, marked = (renames or {}).get(key, (key, None))
    return f"{scope}:{new}", marked or (ru_map or {}).get(key)


def load_claims(folder: Path):
    """Претензии + договоры + выплаты одного отчёта → строки napp_claims и список недостающих листов."""
    out, missing = [], []
    for scope, (cs, ks, (ps, cap)) in CLAIM_SHEETS.items():
        claims = read_claims(folder / cs)
        if claims is None:
            missing.append(cs)
            continue                                   # без претензий строки разреза не пишем
        contracts = read_contracts(folder / ks)
        if contracts is None:
            missing.append(ks)
        pays = read_payouts(folder / ps, cap)
        if pays is None:
            missing.append(ps)
        ru_map = RU_REGIONS if scope == "region" else None
        renames = COMPANY_RENAMES if scope == "company" else None
        for k, (title, by_date) in claims.items():
            key, name = _out_key(scope, k, ru_map, renames)
            name = name or title
            for d, c in by_date.items():
                act, new = ((contracts or {}).get(k, (None, {}))[1]).get(d, (None, None))
                pay = ((pays or {}).get(k) or {}).get(d)
                out.append((d, scope, key, name, c.get("received"), c.get("paid"), c.get("refused"),
                            c.get("unsettled"), act, new, pay, folder.name))
    return out, missing


def folder_date(folder: Path):
    """Дата текущего среза отчёта (поздняя из двух дат листа 1.4) — у листов 2.12–2.14 своей даты нет."""
    for sheet in ("1.4.csv", "2.1.csv", "3.1.csv"):
        p = folder / sheet
        if not p.exists():
            continue
        rows = list(csv.reader(open(p, encoding="utf-8-sig")))
        hdr_i, cols = find_header(rows)
        if hdr_i is not None:
            return max(DATE_RE.match(rows[hdr_i][j]).group(1) for j in cols)
    return None


def region_of_header(s: str):
    """«Toshkent  viloyati» → TOSHKENT, «Toshkent sh.» → TOSHKENT SHAHRI, «Farg`ona viloyati» → FARG'ONA."""
    n = norm_name(s)
    n = re.sub(r"\s+VILOYATI$", "", n)
    n = re.sub(r"\s+SH\.?$", " SHAHRI", n)
    return n if n in RU_REGIONS else None


def read_branches(path: Path):
    """Лист 2.12/2.13/2.14 (компания × регион, один срез) → {(ключ компании, ключ региона): (название, сфера,
    значение)}. Колонки: 2 — итог компании, далее парами «значение, доля» по 14 регионам. Нет листа — None."""
    if not path.exists():
        return None
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    reg_i = next((i for i, r in enumerate(rows[:10]) if len(r) > 4 and region_of_header(r[4])), None)
    if reg_i is None:
        return None
    regcols = {}
    for j in range(4, len(rows[reg_i]), 2):
        rk = region_of_header(rows[reg_i][j])
        if rk and rk not in regcols.values():
            regcols[j] = rk
    out, sphere = {}, None
    for r in rows[reg_i + 2:]:
        if len(r) < 3:
            continue
        a, b = norm_name(r[0]), norm_name(r[1])
        if a == b and "SOHASI" in a:
            sphere = "life" if a.startswith("HAYOT") else "general"
            continue
        key, title = _row_key_of(r)
        if not key:
            continue
        sp = None if key == "__TOTAL__" else sphere
        v = num(r[2])
        if v is not None:
            out[(key, "__TOTAL__")] = (title, sp, v)
        for j, rk in regcols.items():
            v = num(r[j]) if j < len(r) else None
            if v is not None:
                out[(key, rk)] = (title, sp, v)
    return out


def load_branches(folder: Path):
    """Подразделения одного отчёта → строки napp_branches и список недостающих листов."""
    d = folder_date(folder)
    out, missing, data = [], [], {}
    if not d:
        return out, ["1.4.csv (дата среза)"]
    for sheet, field in BRANCH_SHEETS:
        m = read_branches(folder / sheet)
        if m is None:
            missing.append(sheet)
            continue
        for (ck, rk), (title, sp, v) in m.items():
            rec = data.setdefault((ck, rk), {"title": title, "sphere": sp})
            rec[field] = v
    for (ck, rk), rec in sorted(data.items()):
        ckey, cname = _out_key("company", ck, None, COMPANY_RENAMES)
        rkey = "total" if rk == "__TOTAL__" else f"region:{rk}"
        rname = "Все регионы" if rk == "__TOTAL__" else RU_REGIONS.get(rk, rk)
        out.append((d, ckey, cname or rec["title"], rec["sphere"], rkey, rname, rec.get("premiums_mln"),
                    rec.get("payouts_mln"), rec.get("contracts"), folder.name))
    return out, missing


def multi_mismatch(rows14, tol=0.005):
    """Сверка листа 1.4: итог «Ikki va undan ortiq…» (multi_general) против суммы пакетов и «boshqalar».
    Возвращает [(дата, итог, сумма пакетов)] там, где расхождение больше tol (доля от итога)."""
    tot, parts = {}, {}
    for d, key, _ru, prem, _pay, _liab in rows14:
        if key == "multi_general" and prem is not None:
            tot[d] = prem
        elif (key == "multi_other" or re.match(r"^cls\d+(_\d+)+$", key)) and prem is not None:
            parts[d] = parts.get(d, 0.0) + prem
    return [(d, t, parts.get(d, 0.0)) for d, t in sorted(tot.items())
            if t and abs(parts.get(d, 0.0) - t) > tol * abs(t)]


MULTI_NOTE = "итог отчёта не равен сумме пакетов — взята сумма пакетов"


def multi_fix(rows14, tol=0.005):
    """Исправляет итог комплексного страхования (multi_general) там, где он не равен сумме пакетов и «boshqalar»:
    премии, выплаты и обязательства — суммы по пакетам. Возвращает (строки, {дата: (итог отчёта, сумма пакетов)})."""
    bad = {d: (t, parts) for d, t, parts in multi_mismatch(rows14, tol)}
    if not bad:
        return rows14, {}
    sums = {}
    for d, key, _ru, prem, pay, liab in rows14:
        if d in bad and (key == "multi_other" or re.match(r"^cls\d+(_\d+)+$", key)):
            acc = sums.setdefault(d, [0.0, 0.0, 0.0])
            for i, v in enumerate((prem, pay, liab)):
                if v is not None:
                    acc[i] += v
    out = []
    for r in rows14:
        d, key = r[0], r[1]
        if key == "multi_general" and d in bad:
            p_, y_, l_ = sums.get(d, [None, None, None])
            r = (d, key, r[2], p_, y_, l_)
        out.append(r)
    return out, bad


def report_folders():
    """Папки разобранных отчётов по дате текущего среза (затем по имени): общий срез двух отчётов (прошлый год в
    новом отчёте) перезаписывается более поздним отчётом, а не тем, чьё имя папки дальше по алфавиту."""
    if not PARSED.exists():
        return []
    folders = [f for f in PARSED.iterdir() if f.is_dir()]
    return sorted(folders, key=lambda f: (folder_date(f) or "", f.name))


def build():
    # Сначала разбираем все CSV в память (без базы), потом пишем одной короткой транзакцией:
    # раньше блокировка записи держалась всё время разбора, и расчёты в это время ждали.
    # Порядок операций и итог в таблице — те же, что были.
    warn = []
    now = datetime.now().isoformat(timespec="seconds")
    first = []
    notes = {}                          # (дата, ключ) → (пометка, отчёт): только у строки, которая останется в базе
    folders = report_folders()
    for folder in folders:
        sheet = folder / "1.4.csv"
        if not sheet.exists():
            continue
        src = folder.name
        rows14, bad = multi_fix(load_sheet(sheet))
        for d, key, ru, prem, pay, liab in rows14:
            first.append((d, key, ru, prem, pay, liab, src, now))
            if key == "multi_general":
                notes.pop((d, key), None)         # более поздний отчёт перезаписал срез — старая пометка не нужна
        for d, (total, parts) in bad.items():
            fmt = lambda x: f"{x:,.1f}".replace(",", " ").replace(".", ",")
            text = (f"{MULTI_NOTE}: итог «Ikki va undan ortiq…» в отчёте {fmt(total)} млн сум, сумма пакетов и "
                    f"«boshqalar» {fmt(parts)} млн сум (строка итога в отчёте, вероятно, не обновлена)")
            notes[(d, "multi_general")] = (text, src)
            warn.append(f"{src}: на {d} итог комплексного страхования {fmt(total)} млн сум не равен сумме пакетов "
                        f"{fmt(parts)} млн сум — строка «Ikki va undan ortiq…» в отчёте, вероятно, не обновлена; "
                        f"итог заменён суммой пакетов (пометка в market_stats_notes)")
    cuts, claims, branches = [], [], []
    for folder in folders:
        for prefix, sheets, ru_map, renames in (("region", REGION_SHEETS, RU_REGIONS, None),
                                                ("company", COMPANY_SHEETS, None, COMPANY_RENAMES)):
            rows, missing = load_cut(folder, prefix, sheets, ru_map, renames)
            for m in missing:
                warn.append(f"{folder.name}: нет листа {m} — разрез {prefix} неполный")
            for d, key, name, prem, pay, liab in rows:
                cuts.append((d, key, name, prem, pay, liab, folder.name, now))
        # претензии, договоры и подразделения (01.10.2026): нет листа — строки не пишутся, причина в журнал
        rows, missing = load_claims(folder)
        for m in missing:
            warn.append(f"{folder.name}: нет листа {m} — претензии/договоры за срез неполные")
        claims += [r + (now,) for r in rows]
        rows, missing = load_branches(folder)
        for m in missing:
            warn.append(f"{folder.name}: нет листа {m} — подразделения за срез неполные")
        branches += [r + (now,) for r in rows]
    con = _connect()
    try:
        con.executescript((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
        con.executemany("INSERT OR REPLACE INTO market_stats VALUES (?,?,?,?,?,?,?,?)", first)
        # пометки к строкам market_stats (итог комплексного заменён суммой пакетов) — пересобираются целиком
        con.execute("DELETE FROM market_stats_notes")
        con.executemany("INSERT OR REPLACE INTO market_stats_notes (report_date, row_key, note, source_file, loaded_at) "
                        "VALUES (?,?,?,?,?)", [(d, k, n, src, now) for (d, k), (n, src) in sorted(notes.items())])
        # строки, оставшиеся от прежней загрузки под старым названием страховщика
        for k, (new_key, _) in COMPANY_RENAMES.items():
            if k != new_key:
                con.execute("DELETE FROM market_stats WHERE row_key=?", (f"company:{k}",))
        # разрезы «регионы» и «страховщики»
        con.executemany("INSERT OR REPLACE INTO market_stats VALUES (?,?,?,?,?,?,?,?)", cuts)
        # претензии и подразделения: строки старых названий страховщиков — под новым ключом (как выше)
        for k, (new_key, _) in COMPANY_RENAMES.items():
            if k != new_key:
                con.execute("DELETE FROM napp_claims WHERE key=?", (f"company:{k}",))
                con.execute("DELETE FROM napp_branches WHERE company_key=?", (f"company:{k}",))
        con.executemany("INSERT OR REPLACE INTO napp_claims (report_date, scope, key, name, claims_received, "
                        "claims_paid, claims_refused, claims_unsettled, contracts_active, contracts_new, payouts_mln, "
                        "source_file, loaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", claims)
        con.executemany("INSERT OR REPLACE INTO napp_branches (report_date, company_key, company_name, sphere, "
                        "region_key, region_name, premiums_mln, payouts_mln, contracts, source_file, loaded_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", branches)
        con.commit()
        dates = [r[0] for r in con.execute("SELECT DISTINCT report_date FROM market_stats ORDER BY 1")]
        # число операций INSERT OR REPLACE больше числа строк (срезы пересекаются между отчётами
        # и перезаписываются), поэтому наружу отдаём фактическое число строк таблицы
        total = con.execute("SELECT COUNT(*) FROM market_stats").fetchone()[0]
    finally:
        con.close()
    for w in warn:
        print("ВНИМАНИЕ:", w)
    # рыночный ориентир входит в справочники расчёта — кэш сервера сбрасываем
    appdb.invalidate_reference()
    return total, dates


def refresh():
    """Скачать новые отчёты, разобрать, загрузить статистику. Возвращает краткий отчёт."""
    py = sys.executable
    log = []
    for script in ("napp_download.py", "inspect_excel.py"):
        try:
            r = subprocess.run([py, str(ROOT / "tools" / script)], capture_output=True, text=True,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=STEP_TIMEOUT_SEC)
            log.append(f"{script}: {(r.stdout or r.stderr).strip()[-300:]}")
        except subprocess.TimeoutExpired:
            log.append(f"{script}: не уложился в {STEP_TIMEOUT_SEC} с — остановлен, берём то, что уже разобрано")
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
    con = sqlite3.connect(str(db_path()))
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


def napp_csv():
    """Файлы для заказчика (01.10.2026): пакеты классов (комплексное), претензии, подразделения — в docs/."""
    con = sqlite3.connect(str(db_path()))
    months = {"03-31": 3, "04-01": 3, "07-01": 6, "10-01": 9, "01-01": 12}
    f3 = lambda x: "" if x is None else f"{x:.3f}"
    out = []
    # 1. пакеты классов и итог комплексного (лист 1.4)
    path = ROOT / "docs" / "НАПП — комплексное страхование (пакеты классов).csv"
    rows = con.execute("SELECT m.report_date, m.row_key, m.row_name, m.premiums_ytd, m.payouts_ytd, m.liabilities, "
                       "m.source_file, n.note FROM market_stats m LEFT JOIN market_stats_notes n "
                       "ON n.report_date=m.report_date AND n.row_key=m.row_key "
                       "WHERE m.row_key GLOB 'cls*_*' OR m.row_key LIKE 'multi_%' "
                       "ORDER BY m.report_date, m.row_key").fetchall()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["дата среза", "ключ", "строка", "премии с начала года, млн сум", "выплаты с начала года, млн сум",
                    "обязательства на дату, млн сум", "годовая ставка, % (премии × 12 / мес. / обязательства)",
                    "убыточность, % (выплаты / премии)", "источник (разобранный отчёт)", "примечание"])
        for d, k, n, p, y, l, src, note in rows:
            m = months.get(d[5:])
            w.writerow([d, k, n, f3(p), f3(y), f3(l), f3(p * 12 / m / l * 100) if p and l and m else "",
                        f3(y / p * 100) if p and y is not None else "", src, note or ""])
    out.append(path)
    # 2. претензии (регионы и страховщики)
    path = ROOT / "docs" / "НАПП — претензии по регионам и страховщикам.csv"
    rows = con.execute("SELECT report_date, scope, key, name, claims_received, claims_paid, claims_refused, "
                       "claims_unsettled, contracts_active, contracts_new, payouts_mln, source_file FROM napp_claims "
                       "ORDER BY report_date, scope, key").fetchall()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["дата среза", "разрез", "ключ", "название", "претензий поступило", "оплачено", "отказано",
                    "не урегулировано", "договоров действующих на дату", "договоров новых с начала года",
                    "выплаты общего страхования, млн сум", "претензий на 1 000 действующих договоров",
                    "доля отказов, %", "средняя выплата, сум", "источник (разобранный отчёт)"])
        for d, sc, k, n, rc, pd, rf, un, act, new, pay, src in rows:
            w.writerow([d, "регион" if sc == "region" else "страховщик", k, n, rc, pd, rf, un, act, new, f3(pay),
                        f3(rc / act * 1000) if rc is not None and act else "",
                        f3(rf / rc * 100) if rf is not None and rc else "",
                        round(pay * 1e6 / pd) if pay is not None and pd else "", src])
    out.append(path)
    # 3. подразделения
    path = ROOT / "docs" / "НАПП — подразделения страховщиков по регионам.csv"
    rows = con.execute("SELECT report_date, company_key, company_name, sphere, region_key, region_name, premiums_mln, "
                       "payouts_mln, contracts, source_file FROM napp_branches "
                       "ORDER BY report_date, company_key, region_key").fetchall()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["дата среза", "ключ страховщика", "страховщик", "сфера", "ключ региона", "регион",
                    "премии с начала года, млн сум", "выплаты с начала года, млн сум", "договоров на дату",
                    "убыточность, %", "средняя премия на договор, сум", "источник (разобранный отчёт)"])
        for d, ck, cn, sp, rk, rn, p, y, n, src in rows:
            w.writerow([d, ck, cn, {"general": "общее", "life": "жизнь"}.get(sp, ""), rk, rn, f3(p), f3(y),
                        "" if n is None else int(n), f3(y / p * 100) if p and y is not None else "",
                        round(p * 1e6 / n) if p is not None and n else "", src])
    out.append(path)
    con.close()
    return out


if __name__ == "__main__":
    if "--osgor" in sys.argv:
        print("Файл по ОСГОР:", osgor_csv())
    elif "--napp" in sys.argv:
        print("Файлы НАПП:", *napp_csv(), sep="\n  ")
    elif "--refresh" in sys.argv:
        print("\n".join(refresh()))
    else:
        n, dates = build()
        print(f"Строк в таблице market_stats: {n}; даты срезов: {', '.join(dates)}")
