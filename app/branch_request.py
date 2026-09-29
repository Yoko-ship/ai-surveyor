"""
«Запрос филиала» — таблица из 16 строк (образцы заказчика 30.09.2026, узбекская кириллица).

Два пути, один результат:
  parse_text(text, tables) — файл с текстовым слоем (DOCX, XLSX, PDF с текстом), без модели;
  from_model(raw)          — ответ языковой модели по скану (блок branch_request в /act/photos).
Оба возвращают fields() одной формы; значения чисел, срока и франшизы разбирают одни и те же функции,
поэтому скан и файл дают одинаковые цифры.

Стороны договора (страхователь, выгодоприобретатель, залогодатель): название возвращается, только если
это юридическое лицо (маркер МЧЖ/MCHJ, АЖ/AJ, АТБ/ATB, ООО, АО, банк, филиал …). Физическое лицо —
только признак kind = individual, само значение не извлекается и не хранится. В журналы названия не пишутся.

Модуль без сети, без базы и без HTTP. Подписи строк — docparse.BRANCH_REQUEST_ROWS.
"""
import re
from datetime import date
from typing import Optional

from . import docparse as D

KIND = "branch_request"         # вид документа в акте (подпись — act_texts.DOC_KIND_LABELS)
ROW_CODES = [code for code, _labels in D.BRANCH_REQUEST_ROWS]
PARTY_CODES = ("policyholder", "beneficiary", "pledger")
# сколько разных подписей бланка нужно, чтобы документ считался запросом филиала: у обычного договора
# тоже есть «страхователь», «страховая сумма», «франшиза», поэтому при неполном бланке нужен ещё код продукта
MIN_ROWS = 8
FULL_ROWS = 12
MAX_TEXT = 600                 # длина значения строки (описание объекта бывает длинным)
MAX_TERM_DAYS = 3660

# подписи в свёрнутом написании (fold): длинные раньше — «суғурта тури» не перехватит «суғурта тарифи»
_LABELS = sorted(((D.fold(lab), code) for code, labs in D.BRANCH_REQUEST_ROWS for lab in labs if D.fold(lab)),
                 key=lambda x: -len(x[0]))

# ------------------------------------------------------------------ стороны договора

def _F(xs) -> tuple:
    """Слова и фразы в естественном написании → свёрнутые (как текст документа после D.fold)."""
    return tuple(sorted({D.fold(x) for x in xs if D.fold(x)}))


# маркеры организационно-правовой формы и организаций: отдельные слова (сравнение по слову) и фразы.
# ЧП — частное предприятие (юрлицо), в отличие от ИП/ЯТТ; название с фамилией остаётся как есть (решение заказчика)
LEGAL_WORDS = _F(("МЧЖ", "MCHJ", "АЖ", "AJ", "АТБ", "ATB", "ОАЖ", "ООО", "АО", "ОАО", "ЗАО", "ПАО", "ЧАКБ",
                  "АКБ", "AKB", "ЧП", "JSC", "OJSC", "LLC", "LTD", "PLC", "INC", "GmbH", "ХК", "XK", "ҚК", "QK",
                  "ДУК", "DUK", "УК", "UK", "ОК", "OK", "банк", "банки", "bank", "banki", "банкаси", "bankasi",
                  "БХО", "BXO", "УБХО", "UBXO", "филиал", "филиали",
                  "filial", "filiali", "корхона", "корхонаси", "korxona", "korxonasi", "ташкилот", "ташкилоти",
                  "tashkilot", "tashkiloti", "муассаса", "муассасаси", "muassasa", "muassasasi", "кооператив",
                  "kooperativ", "ҳокимлиги", "hokimligi", "жамияти", "jamiyati", "хўжалиги", "xo'jaligi"))
LEGAL_PHRASES = _F(("масъулияти чекланган", "mas'uliyati cheklangan", "акциядорлик", "aksiyadorlik",
                    "акционерное", "общество", "товарищество", "фермер хўжалиги", "fermer xo'jaligi",
                    "давлат унитар", "davlat unitar", "унитар корхона", "хусусий корхона", "xususiy korxona",
                    "қўшма корхона", "qo'shma korxona", "предприятие", "частное предприятие", "компания",
                    "company", "corporation", "group"))
# банк по слитному названию: «Namunabank», «Намунабанк», «…banki», «…банкаси»
_BANK_TAIL = ("bank", "banki", "bankasi")
# индивидуальный предприниматель и гражданин — физическое лицо, даже если рядом слово «предприятие»
INDIVIDUAL_WORDS = _F(("ЯТТ", "YATT", "YaTT", "ИП"))
INDIVIDUAL_PHRASES = _F(("якка тартибдаги", "yakka tartibdagi", "индивидуальный предприниматель", "индивидуальн",
                         "предприниматель", "фуқаро", "fuqaro", "гражданин", "гражданка"))
_PATRONYMIC = re.compile(r"\b[А-ЯЁ][а-яё]+(?:ович|евич|ьич|овна|евна|ична|инична)\b|"
                         r"\b(?:o[ʻ'`‘’]?g[ʻ'`‘’]?li|qizi|ўғли|угли|қизи|кизи)\b", re.I)
_INITIALS = re.compile(r"\b[А-ЯЁA-Z][а-яёa-z]+\s+[А-ЯЁA-Z]\.\s*[А-ЯЁA-Z]\.|\b[А-ЯЁA-Z]\.\s*[А-ЯЁA-Z]\.\s*[А-ЯЁA-Z][а-яёa-z]+")
# представитель и реквизиты после названия («…, в лице директора …») к названию не относятся
_REPR_CUT = re.compile(r"\s*[,;(]?\s*(?:в\s+лице\b|действующ\w*|на\s+основании\b|номидан\b|nomidan\b|"
                       r"represented\s+by\b|acting\s+on\b|инн\b|стир\b|stir\b|inn\b).*$", re.I | re.S)


def _words(folded: str) -> set:
    return set(re.findall(r"[a-z0-9]+", folded))


def legal_name(name: Optional[str]) -> str:
    """Название без части «в лице …», «действующего на основании …», реквизитов."""
    return _REPR_CUT.sub("", str(name or "")).strip(" \t\r\n,;")


def is_legal(name: Optional[str]) -> bool:
    """Название юрлица: есть маркер организации, нет признаков ИП и ФИО (отчество, «Фамилия И. О.»)."""
    s = legal_name(name)
    if not s:
        return False
    f = D.fold(s)
    w = _words(f)
    if w & set(INDIVIDUAL_WORDS) or any(p in f for p in INDIVIDUAL_PHRASES):
        return False
    if _PATRONYMIC.search(s) or _INITIALS.search(s):
        return False
    if any(len(x) > len(t) + 1 and x.endswith(t) for x in w for t in _BANK_TAIL):
        return True
    return bool(w & set(LEGAL_WORDS)) or any(p in f for p in LEGAL_PHRASES)


def party(value: Optional[str]) -> dict:
    """{"kind": legal|individual|None, "name": название юрлица или None}. ФИО не возвращается никогда."""
    s = _clean(value)
    if not s:
        return {"kind": None, "name": None}
    if is_legal(s):
        return {"kind": "legal", "name": (legal_name(s) or s)[:200]}
    return {"kind": "individual", "name": None}


# ------------------------------------------------------------------ значения

_EMPTY = {"", "-", "—", "–", "null", "none", "n/a", "нет данных"}


def _clean(v) -> Optional[str]:
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    s = re.sub(r"[ \t  ]+", " ", str(v)).strip(" \t\r\n:;*|")
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s[:MAX_TEXT] if s and s.lower() not in _EMPTY else None


_AMOUNT = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:[   ]\d{3})+(?:[.,]\d{1,2})?"
                     r"|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
                     r"|\d{1,3}(?:\.\d{3}){2,}(?:,\d{1,2})?"
                     r"|\d+(?:[.,]\d{1,2})?)(?![\d])")


# множители после числа: «1,5 млрд сум», «250 mln so'm» (общие с contract_read.money)
SCALE = (("трлн", 1e12), ("trln", 1e12), ("trillion", 1e12), ("млрд", 1e9), ("миллиард", 1e9), ("mlrd", 1e9),
         ("milliard", 1e9), ("billion", 1e9), ("млн", 1e6), ("миллион", 1e6), ("mln", 1e6), ("million", 1e6),
         ("тыс", 1e3), ("минг", 1e3), ("ming", 1e3), ("thousand", 1e3))
_MINUS = re.compile(r"(?:^|[^\w])[-−]$")


def scale_of(after: str) -> float:
    """Множитель по слову сразу после числа («млн», «mlrd» …); нет слова — 1."""
    s = str(after or "")[:30].lower()
    for word, mult in SCALE:
        if re.match(r"\s*" + word, s):
            return mult
    return 1.0


def amount_ex(text) -> tuple:
    """(сумма | None, пометка | None). Пометка negative — перед числом минус: сумма не берётся, а знак не
    отбрасывается молча (отрицательная премия или сумма — ошибка документа, её видит сотрудник)."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return (None, "negative") if text < 0 else (amount(text), None)
    s = str(text or "").split("(")[0]
    m = _AMOUNT.search(s)
    if m and _MINUS.search(s[:m.start()]):
        return None, "negative"
    return amount(text), None


def amount(text) -> Optional[float]:
    """Сумма: «81 250 000 000,00 (саксон бир миллиард … сўм)» → 81250000000.0. Сумма прописью в скобках
    не читается — берётся число до скобки; «1,5 млрд» — с множителем. Не число или минус перед числом —
    None (пометку даёт amount_ex)."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text) if 0 < text < 1e15 else None
    s = str(text or "").split("(")[0]
    m = _AMOUNT.search(s)
    if not m or _MINUS.search(s[:m.start()]):
        return None
    raw = re.sub(r"[   ]", "", m.group(0))
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", raw):
        raw = raw.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3}){2,}(?:,\d{1,2})?", raw):
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")
    try:
        x = float(raw)
    except ValueError:
        return None
    x = round(x * scale_of(s[m.end():]), 2)
    return x if 0 < x < 1e15 else None


def tariff(text) -> Optional[float]:
    """Тариф в процентах годовых: «0.05», «0,05 %», «0,05% йиллик» → 0.05. Вне (0; 100] — None."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        x = float(text)
    else:
        m = re.search(r"(?<![\d.,])(\d{1,3}(?:[.,]\d+)?)", str(text or "").split("(")[0])
        if not m:
            return None
        x = float(m.group(1).replace(",", "."))
    return x if 0 < x <= 100 else None


def count(text) -> Optional[int]:
    """«1 дона», «1 dona», «1 шт.» → 1."""
    m = re.search(r"(?<!\d)(\d{1,4})(?!\d)", str(text or ""))
    return int(m.group(1)) if m and 0 < int(m.group(1)) < 10000 else None


_NOT_APPLIED = _F(("қўлланилмайди", "қўлланилинмайди", "қўлланмайди", "қўлланилмаган", "qo'llanilmaydi",
                   "qo'llanilinmaydi", "qo'llanmaydi", "qo'llanilmagan", "не применяется", "не применяются",
                   "не предусмотрен", "не устанавливается", "не установлен", "мавжуд эмас", "mavjud emas",
                   "кўзда тутилмаган", "ko'zda tutilmagan", "белгиланмаган", "belgilanmagan", "not applied",
                   "not applicable", "не предусматривается", "не устанавливается", "белгиланмайди",
                   "belgilanmaydi"))
_NO_WORDS = _F(("йўқ", "yo'q", "нет", "no", "none", "nil"))


def franchise(text) -> Optional[dict]:
    """Франшиза: {"applied": bool, "text", "pct", "amount"}. «Қўлланилинмайди» / «не применяется» → applied=false."""
    s = _clean(text)
    if s is None:
        return None
    f = D.fold(s)
    w = _words(f)
    if any(p in f for p in _NOT_APPLIED) or w & set(_NO_WORDS) or s in ("0", "0%", "0 %"):
        return {"applied": False, "text": s, "pct": None, "amount": None}
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", s)
    pct = float(m.group(1).replace(",", ".")) if m else None
    amt = None
    if pct is None:
        amt = amount(s)
    return {"applied": True, "text": s, "pct": pct if pct and 0 < pct <= 100 else None, "amount": amt}


# ------------------------------------------------------------------ срок

_MONTHS = (("сентябр", 9), ("октябр", 10), ("ноябр", 11), ("декабр", 12), ("январ", 1), ("феврал", 2),
           ("апрел", 4), ("август", 8), ("март", 3), ("май", 5), ("мая", 5), ("июн", 6), ("июл", 7),
           ("sentyabr", 9), ("sentabr", 9), ("oktyabr", 10), ("oktabr", 10), ("noyabr", 11), ("dekabr", 12),
           ("yanvar", 1), ("fevral", 2), ("aprel", 4), ("avgust", 8), ("mart", 3), ("may", 5), ("iyun", 6),
           ("iyul", 7), ("january", 1), ("february", 2), ("march", 3), ("april", 4), ("june", 6), ("july", 7),
           ("august", 8), ("september", 9), ("october", 10), ("november", 11), ("december", 12))
_W = r"[a-zа-яёўқғҳ]+"
# «2026 йил 29 сентябрдан», «2026-yil 1-oktyabrdan» (дефис после года — узбекская латиница)
_RX_UZ = re.compile(r"(?<!\d)(\d{4})\s*[-–]?\s*(?:йил|yil)" + _W[:-1] + r"*\s*(\d{1,2})\s*[-–]?\s*(" + _W + ")")
_RX_NUM = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)")
_RX_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_RX_WORD = re.compile(r"(?<!\d)(\d{1,2})\s*[-–]?\s*(" + _W + r")\s+(\d{4})(?!\d)")
# английский порядок: «October 1, 2026»
_RX_EN = re.compile(r"(?<![a-z])(" + _W + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)")


def _month(word: str) -> Optional[int]:
    w = (word or "").lower()
    for stem, n in _MONTHS:
        if w.startswith(stem):
            return n
    return None


def _date(y, m, d) -> Optional[date]:
    try:
        x = date(int(y), int(m), int(d))
    except (TypeError, ValueError):
        return None
    return x if 2000 <= x.year <= 2100 else None


def dates_in(text) -> list:
    """Даты в порядке появления в тексте: «2026 йил «29» сентябрдан», «29.09.2026», «2026-09-29»,
    «29 сентября 2026», «07 sentyabr 2026». Кавычки «» и "" не мешают."""
    s = re.sub(r"[«»\"“”„'‘’ʻʼ`]", " ", str(text or "").lower().replace("ё", "е"))
    found = []
    for rx, kind in ((_RX_UZ, "uz"), (_RX_NUM, "num"), (_RX_ISO, "iso"), (_RX_WORD, "word"), (_RX_EN, "en")):
        for m in rx.finditer(s):
            g = m.groups()
            if kind == "uz":
                d = _date(g[0], _month(g[2]) or 0, g[1])
            elif kind == "num":
                d = _date(g[2], g[1], g[0])
            elif kind == "iso":
                d = _date(g[0], g[1], g[2])
            elif kind == "en":
                d = _date(g[2], _month(g[0]) or 0, g[1])
            else:
                d = _date(g[2], _month(g[1]) or 0, g[0])
            if d:
                found.append((m.start(), m.end(), d))
    found.sort()
    out, edge = [], -1
    for a, b, d in found:
        if a >= edge:                  # пересекающиеся совпадения (разные шаблоны одной даты) — одно
            out.append(d)
            edge = b
    return out


def term_days(d_from: date, d_to: date, inclusive: bool = True) -> int:
    """Дни срока: с обоими крайними днями (inclusive, по умолчанию) или без последнего."""
    return (d_to - d_from).days + (1 if inclusive else 0)


def term(text, inclusive: bool = True) -> dict:
    """«2026 йил «29» сентябрдан 2029 йил «10» октябргача» → {"from", "to", "days", "inclusive", "error"}."""
    ds = dates_in(text)
    out = {"from": None, "to": None, "days": None, "inclusive": bool(inclusive), "error": None}
    if len(ds) < 2:
        out["error"] = "term_not_found" if not ds else "term_one_date"
        return out
    a, b = ds[0], ds[1]
    if b < a:
        out["error"] = "term_reversed"
        return out
    days = term_days(a, b, inclusive)
    if not 1 <= days <= MAX_TERM_DAYS:
        out["error"] = "term_too_long"
        return out
    out.update({"from": a.isoformat(), "to": b.isoformat(), "days": days})
    return out


# ------------------------------------------------------------------ объект

_AREA = re.compile(r"(\d{1,3}(?:[   ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*"
                   r"(?:кв\.?\s*м|kv\.?\s*m|м2|m2|м²|m²|sq\.?\s*m)", re.I)
_LAND = _F(("ер участка", "yer uchastka", "земельн", "участ", "land"))
_USEFUL = _F(("фойдали", "foydali", "полезн", "useful"))
_KADASTR = re.compile(r"(?<![\d:])\d{1,4}(?::\d{1,5}){3,6}(?![\d:])")
# вид объекта по описанию: побеждает слово, стоящее в тексте раньше
_KIND_WORDS = (
    ("equipment", _F(("асбоб ускуна", "asbob-uskuna", "ускуна", "uskuna", "оборудован", "станок", "equipment",
                      "machinery", "технологик", "texnologik"))),
    ("building", _F(("бино", "bino", "здани", "иморат", "imorat", "иншоот", "inshoot", "сооружен", "нежил",
                     "нотурар", "noturar", "омбор", "ombor", "склад", "музлатгич", "muzlatgich", "холодильн",
                     "building", "warehouse", "кўчмас мулк", "ko'chmas mulk", "недвижим"))),
    ("vehicle", _F(("автомобил", "avtomobil", "транспорт воситаси", "transport vositasi", "vehicle"))),
    ("special_machinery", _F(("махсус техника", "maxsus texnika", "спецтехник", "экскаватор", "ekskavator",
                              "автокран", "avtokran", "бульдозер", "buldozer"))),
    ("cargo", _F(("юк", "yuk", "груз", "товар", "tovar", "cargo"))),
)
_STORAGE = _F(("сақлаш", "saqlash", "хранен", "омбор", "ombor", "склад", "музлатгич", "muzlatgich", "холодильн",
               "storage", "warehouse"))
_PRODUCTION = _F(("ишлаб чиқариш", "ishlab chiqarish", "производств", "завод", "фабрик", "production"))


def object_facts(desc: Optional[str]) -> dict:
    """Из описания объекта: площади (участок, полезная, общая), кадастровый номер, подсказка класса и вид."""
    s = str(desc or "")
    out = {"areas": {"land_m2": None, "useful_m2": None, "total_m2": None}, "cadastre_no": None,
           "class_hint": None, "object_kind": None}
    m = _KADASTR.search(s)
    if m:
        out["cadastre_no"] = m.group(0)
    prev = 0
    for m in _AREA.finditer(s):
        before = D.fold(s[prev:m.start()])
        prev = m.end()
        val = amount(m.group(1))
        if val is None:
            continue
        slot = "land_m2" if any(w in before for w in _LAND) else \
            "useful_m2" if any(w in before for w in _USEFUL) else "total_m2"
        if out["areas"][slot] is None:
            out["areas"][slot] = val
    f = " " + D.fold(s) + " "
    best = None
    for hint, words in _KIND_WORDS:
        for w in words:
            # короткое слово («юк») — только целым словом, иначе оно найдётся внутри других слов
            m = re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(w), f) if len(w) < 4 else None
            p = m.start() if m else (f.find(w) if len(w) >= 4 else -1)
            if p >= 0 and (best is None or p < best[0]):
                best = (p, hint)
    if best:
        out["class_hint"] = best[1]
        if best[1] == "equipment":
            out["object_kind"] = "equipment"
        elif best[1] == "building":
            out["object_kind"] = "production" if any(w in f for w in _PRODUCTION) else \
                "warehouse" if any(w in f for w in _STORAGE) else None
    return out


# ------------------------------------------------------------------ разбор строк бланка

def _row_label(cell: str) -> Optional[tuple]:
    """(код строки, остаток после подписи) — если ячейка/строка начинается с подписи бланка."""
    # номер строки «5.» и сноски «**» у подписи бланка к подписи не относятся
    raw = re.sub(r"^\s*\d{1,2}\s*[.)]?\s*", "", str(cell or "")).replace("*", "")
    f, idx = D.fold_map(raw)
    for lab, code in _LABELS:
        if f == lab or f.startswith(lab + " "):
            end = len(lab) - 1
            pos = (idx[end] + 1) if end < len(idx) else len(raw)
            rest = raw[pos:]
            # остаток подписи до двоеточия («(ОСГОР бўйича):») — не значение
            if ":" in rest[:60]:
                rest = rest.split(":", 1)[1]
            elif re.match(r"^\s*[\(\*]", rest) and ")" in rest[:60]:
                rest = rest.split(")", 1)[1]
            # хвост подписи: закрывающая скобка «(ОСГОР бўйича)», двоеточие, сноски
            return code, re.sub(r"^[\s:*)]+", "", rest).strip()
    return None


def rows_from_tables(tables: list) -> dict:
    """Строки бланка из таблиц: ячейка с подписью → значение — остальные непустые ячейки строки."""
    got = {}
    for t in tables or []:
        for row in t.get("rows") or []:
            cells = [str(c or "").strip() for c in row]
            for k, c in enumerate(cells):
                hit = _row_label(c) if c else None
                if not hit:
                    continue
                code, rest = hit
                vals = [rest] + [x for x in cells[k + 1:] if x]
                val = _clean("\n".join(v for v in vals if v))
                if code not in got or (got[code] is None and val):
                    got[code] = val
                break
    return got


def rows_from_lines(text: str) -> dict:
    """Строки бланка из текста (PDF): подпись начинает строку, значение — хвост и следующие строки до
    следующей подписи. Одиночный номер строки («5.») пропускается."""
    got, cur, buf = {}, None, []

    def close():
        if cur is not None and (cur not in got or got[cur] is None):
            got[cur] = _clean("\n".join(buf))

    for ln in str(text or "").splitlines():
        ln = ln.strip()
        if not ln or re.fullmatch(r"\d{1,2}\s*[.)]?", ln):
            continue
        parts = [p.strip() for p in ln.split(" | ")] if " | " in ln else [ln]
        hit = None
        for k, p in enumerate(parts):
            hit = _row_label(p)
            if hit:
                parts = [hit[1]] + parts[k + 1:]
                break
        if hit:
            close()
            cur, buf = hit[0], [p for p in parts if p]
        elif cur is not None:
            buf.append(ln)
    close()
    return got


# строки, которых нет ни в договоре, ни в полисе: «Шартнома миқдори», «Класс (ОСГОР бўйича)», «Контрагент»,
# «Стандарт шартларни ўзгартириш/қўшиш» — по ним бланк узнаётся, когда код продукта не заполнен
OWN_ROWS = ("contracts_count", "osgor_class", "counterparty", "contract_terms")


def detect(rows: dict) -> bool:
    """Бланк запроса филиала: свой набор строк (нумерованная таблица 1–16). Полный бланк — со строками, которых
    нет в договоре; неполный — с кодом продукта «по приказу». Заголовок договора/полиса проверяет вызывающий."""
    n = len([c for c in rows if c in ROW_CODES])
    coded = bool(re.fullmatch(r"\s*\d{3,4}\s*", str(rows.get("product_code") or "")))
    own = any(c in rows for c in OWN_ROWS)
    return (n >= FULL_ROWS and (own or coded)) or (n >= MIN_ROWS and coded)


def fields(rows: dict, inclusive: bool = True) -> dict:
    """Строки бланка (текст как в документе) → поля с разобранными числами, сроком и франшизой."""
    g = lambda c: _clean(rows.get(c))                        # noqa: E731
    desc = g("object")
    facts = object_facts(desc)
    tm = term(g("term"), inclusive) if g("term") else {"from": None, "to": None, "days": None,
                                                         "inclusive": bool(inclusive), "error": None}
    ph, bf, pl = party(rows.get("policyholder")), party(rows.get("beneficiary")), party(rows.get("pledger"))
    code = re.search(r"(?<!\d)(\d{3,4})(?!\d)", g("product_code") or "")
    terms_text = g("contract_terms")
    # отрицательные суммы не берутся — пометка для экрана (branch_view → br_negative)
    negative = [k for k, row in (("object_value", "object_value"), ("sum_insured", "sum_insured"),
                                 ("premium", "premium")) if amount_ex(g(row))[1] == "negative"]
    return {
        "product_code": code.group(1).zfill(4) if code else None,
        "policyholder": ph, "beneficiary": bf, "pledger": pl,
        "has_beneficiary": bf["kind"] is not None, "has_pledger": pl["kind"] is not None,
        "object_description": desc, "object_description_translated": None,
        "object_kind": facts["object_kind"], "class_hint": facts["class_hint"],
        "areas": facts["areas"], "cadastre_no": facts["cadastre_no"],
        "object_value": amount(g("object_value")), "sum_insured": amount(g("sum_insured")),
        "franchise": franchise(g("franchise")),
        "tariff_pct": tariff(g("tariff")), "premium": amount(g("premium")),
        "term_text": g("term"), "term_from": tm["from"], "term_to": tm["to"], "term_days": tm["days"],
        "term_inclusive": tm["inclusive"], "term_error": tm["error"],
        "contract_terms": terms_text,
        "contract_terms_standard": bool(terms_text and D.fold(terms_text).startswith("standart")),
        "contracts_count": count(g("contracts_count")),
        "counterparty": g("counterparty") if is_legal(g("counterparty")) else None,
        "osgor_class": g("osgor_class"),
        "additional_info": g("additional_info"),
        "amount_errors": negative,
    }


def row_view(rows: dict) -> list:
    """16 строк бланка: найдена ли строка и есть ли в ней значение (сами значения — в fields)."""
    return [{"n": i + 1, "code": c, "found": c in rows, "filled": bool(_clean(rows.get(c)))}
            for i, c in enumerate(ROW_CODES)]


def parse_text(text: str, tables: Optional[list] = None, inclusive: bool = True) -> Optional[dict]:
    """Файл с текстом → {"rows", "fields", "rows_found"} или None, если это не запрос филиала."""
    rows = rows_from_tables(tables)
    if not detect(rows):
        rows = rows_from_lines(text)
    if not detect(rows):
        return None
    # стороны договора дальше идут только через fields (название юрлица или признак «физическое лицо»)
    return {"rows": row_view(rows), "rows_found": len(rows), "fields": fields(rows, inclusive)}


def from_model(raw, inclusive: bool = True) -> Optional[dict]:
    """
    Блок branch_request из ответа модели → те же fields, что у текстового разбора. Модель переписывает
    строки как в документе (строки текста); числа, срок и франшизу разбирает сервер.
    Не словарь или почти пусто — None.
    """
    if not isinstance(raw, dict):
        return None
    rows = {}
    for code in ROW_CODES:
        v = raw.get(code)
        if isinstance(v, dict):                    # {"name": ..., "is_legal": ...} для сторон
            # модель сказала «гражданин» — имени не берём, даже если она его написала
            name = v.get("name") or v.get("text") or v.get("value")
            v = "individual" if (v.get("is_legal") is False and name) else name
        v = _clean(v)
        if v is not None:
            rows[code] = v
    if len(rows) < 4:
        return None
    f = fields(rows, inclusive)
    # срок: если текст срока не разобран, берём даты модели (ГГГГ-ММ-ДД), дни считает сервер
    if not f["term_days"]:
        a, b = _iso(raw.get("term_from")), _iso(raw.get("term_to"))
        if a and b and b >= a and 1 <= term_days(a, b, inclusive) <= MAX_TERM_DAYS:
            f.update(term_from=a.isoformat(), term_to=b.isoformat(), term_days=term_days(a, b, inclusive),
                     term_error=None)
    tr = _clean(raw.get("object_description_translated"))
    f["object_description_translated"] = tr[:MAX_TEXT] if tr else None
    hint = str(raw.get("class_hint") or "").strip().lower()
    if not f["class_hint"] and hint in ("building", "equipment", "vehicle", "special_machinery", "cargo", "other"):
        f["class_hint"] = hint
    return {"rows": row_view(rows), "rows_found": len(rows), "fields": f}


def _iso(v) -> Optional[date]:
    m = re.fullmatch(r"\s*(\d{4})-(\d{2})-(\d{2})\s*", str(v or ""))
    return _date(*m.groups()) if m else None


# ------------------------------------------------------------------ для акта

# ключи распознанного (act_engine.FIELD_KEYS), в которые раскладывается бланк
ITEM_KEYS = ("product_code", "policyholder", "beneficiary", "pledger", "object_type", "cadastre_no",
             "land_area", "useful_area", "total_area", "object_value", "sum_insured", "franchise", "tariff_pct",
             "premium", "term_from", "term_to", "term_days", "contract_terms", "contracts_count", "additional_info")


def _num_text(x: float) -> str:
    v = float(x)
    return f"{v:,.0f}".replace(",", " ") if v == int(v) else f"{v:,.2f}".replace(",", " ")


def items(f: dict) -> list:
    """Поля бланка → записи распознанного [{key, value}] (строки, как у остальных документов)."""
    out = []

    def add(key, value):
        if value not in (None, ""):
            out.append({"key": key, "value": str(value)[:MAX_TEXT]})

    add("product_code", f.get("product_code"))
    for k in PARTY_CODES:
        add(k, (f.get(k) or {}).get("name"))
    add("object_type", f.get("object_description"))
    add("cadastre_no", f.get("cadastre_no"))
    ar = f.get("areas") or {}
    for key, slot in (("land_area", "land_m2"), ("useful_area", "useful_m2"), ("total_area", "total_m2")):
        if ar.get(slot):
            add(key, _num_text(ar[slot]) + " м²")
    for key in ("object_value", "sum_insured", "premium"):
        if f.get(key):
            add(key, _num_text(f[key]))
    fr = f.get("franchise")
    if fr:
        add("franchise", fr.get("text"))
    if f.get("tariff_pct") is not None:
        add("tariff_pct", ("%g" % f["tariff_pct"]))
    add("term_from", f.get("term_from"))
    add("term_to", f.get("term_to"))
    if f.get("term_days"):
        add("term_days", f["term_days"])
    add("contract_terms", f.get("contract_terms"))
    if f.get("contracts_count"):
        add("contracts_count", f["contracts_count"])
    add("additional_info", f.get("additional_info"))
    return out


def prefill(f: dict, region: Optional[str] = None) -> dict:
    """Подсказка для шага 2: код продукта, суммы, срок; регион — только если назван в тексте бланка."""
    out = {}
    for k in ("product_code", "sum_insured", "object_value", "term_days", "term_from", "term_to"):
        if f.get(k) not in (None, ""):
            out[k] = f[k]
    if region:
        out["region"] = region
    return out


def region_in(f: dict) -> Optional[str]:
    """Регион по названию в описании объекта (словарь docparse.REGIONS). Кадастровый код региона не
    расшифровывается: справочника кодов в проекте нет — не выдумываем."""
    desc = f.get("object_description")
    return D.cast("region", desc) if desc else None


def request_of(f: dict, source: str) -> dict:
    """Готовый optional.request для /act/make (экран может подставить как есть, сотрудник — исправить)."""
    return {"tariff_pct": f.get("tariff_pct"), "premium": f.get("premium"), "franchise": f.get("franchise"),
            "term_from": f.get("term_from"), "term_to": f.get("term_to"), "term_days": f.get("term_days"),
            "sum_insured": f.get("sum_insured"), "product_code": f.get("product_code"), "source": source,
            # для сверки «запрос ↔ договор» в акте: стоимость и объект — те же, что при загрузке
            "object_value": f.get("object_value"), "cadastre_no": f.get("cadastre_no"),
            "object_kind": f.get("object_kind"), "class_hint": f.get("class_hint"),
            "object_description": f.get("object_description")}
