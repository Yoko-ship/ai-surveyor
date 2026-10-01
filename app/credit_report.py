"""
Отчёт кредитного бюро (КАТМ, «InfoScore») как документ акта — для кредитного страхования (классы 14, 13з, 15).
01.10.2026, заказчик: «нам вот такой infoscoring должен выдавать».

Прямого запроса в КАТМ нет: отчёт загружает сотрудник (PDF с текстом или скан). Для прямого подключения нужен
договор страховщика с бюро и согласие субъекта кредитной информации.

  detect(text)        — текст похож на отчёт бюро (не меньше двух разных признаков из MARKERS);
  parse_text(text)    — PDF с текстом → поля правилами, без модели;
  from_model(raw)     — блок credit_report из ответа модели по скану (строгая схема MODEL_SCHEMA);
  normalize(raw, strict) — общая проверка полей: и для ответа модели (лишнее отбрасывается), и для ввода экрана
                        (strict — ошибка словами, как у запроса филиала).

Что извлекается: дата отчёта, тип субъекта (юрлицо / физлицо); наименование, ИНН — ТОЛЬКО у юридического лица;
ОКЭД; скоринговый балл, класс оценки, версия; общий обзор (заявки, договоры, условные обязательства, запросы,
среднемесячный платёж, просрочки основного долга и процентов); действующие договоры (количество, остаток
задолженности, просроченная часть, среднемесячный платёж, банки-кредиторы — это юрлица).
Что НЕ извлекается и не хранится: у физлица — ФИО, ПИНФЛ, паспорт, адрес, телефон; у любого субъекта — телефон,
e-mail, адрес, учредители. Дополнительно значения проходят проверку на ПД в app/act.py (pd_like).
"""
import re
from datetime import date
from typing import Optional

from . import branch_request as br

KIND = "credit_report"
CREDIT_CLASSES = ("14", "13з", "15")
CALIBRATED = 0
# признаки отчёта: нужно не меньше двух разных (одно «кредитное бюро» в договоре кредита — не отчёт)
MARKERS = ("кредитное бюро", "кредитно-информационный аналитический центр", "кредитно-информационного",
           "katm", "infoscore", "субъект кредитной информации", "субъекта кредитной информации", "scoring",
           "скоринговый балл", "kredit byurosi", "kredit-axborot", "кредит бюроси", "credit bureau",
           "credit-information analytical")
OVERVIEW_KEYS = ("applications", "contracts", "contingent", "inquiries", "avg_monthly_payment",
                 "overdue_principal_count", "max_overdue_principal_days", "max_overdue_principal_amount",
                 "max_overdue_interest_days", "overdue_interest_total")
MONEY_KEYS = ("avg_monthly_payment", "max_overdue_principal_amount", "overdue_interest_total")
ACTIVE_KEYS = ("count", "total_debt", "overdue", "monthly_payment")
ACTIVE_MONEY = ("total_debt", "overdue", "monthly_payment")
MAX_COUNT = 100000
MAX_MONEY = 1e15
MAX_CREDITORS = 10

# подписи общего обзора → поле (свёрнутый текст: строчные, ё → е); длинные раньше коротких
_OVERVIEW_LABELS = (
    ("максимальная непрерывная просрочка", "max_overdue_interest_days"),
    ("всего просроченных", "overdue_interest_total"),
    ("количество просрочек основного долга", "overdue_principal_count"),
    ("количество просрочек", "overdue_principal_count"),
    ("максимальная просрочка од (сумма)", "max_overdue_principal_amount"),
    ("максимальная просрочка од (дни)", "max_overdue_principal_days"),
    ("максимальная просрочка основного долга (сумма)", "max_overdue_principal_amount"),
    ("максимальная просрочка основного долга (дни)", "max_overdue_principal_days"),
    ("среднемесячный платеж", "avg_monthly_payment"),
    ("условные обязательства", "contingent"),
    ("запросы и подписки", "inquiries"),
    ("запросы", "inquiries"),
    ("заявки", "applications"),
    ("договора", "contracts"),
    ("договоры", "contracts"),
)
_AMOUNT = r"\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
# \u0447\u0438\u0441\u043b\u043e \u0432 \u044f\u0447\u0435\u0439\u043a\u0435 \u0442\u0430\u0431\u043b\u0438\u0446\u044b: \u0433\u0440\u0443\u043f\u043f\u044b \u043f\u043e 3 \u0446\u0438\u0444\u0440\u044b \u043f\u043e\u0441\u043b\u0435 \u043f\u0435\u0440\u0432\u043e\u0439 \u0433\u0440\u0443\u043f\u043f\u044b 1\u20133 \u0446\u0438\u0444\u0440 (\u043f\u0435\u0440\u0432\u0430\u044f \u2014 \u043d\u0435 \u0441 \u043d\u0443\u043b\u044f: \u00ab0 788 963 272.68\u00bb \u2014
# \u044d\u0442\u043e \u00ab0\u00bb \u0438 \u00ab788 963 272.68\u00bb), \u043d\u0435\u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u0430\u044f \u0434\u0440\u043e\u0431\u043d\u0430\u044f \u0447\u0430\u0441\u0442\u044c; \u0431\u0435\u0437 \u0433\u0440\u0443\u043f\u043f \u2014 \u043f\u0440\u043e\u0441\u0442\u043e \u0446\u0438\u0444\u0440\u044b
_CELL_NUM = r"(?<![\d.,])(?:[1-9]\d{0,2}(?:[ \u00a0]\d{3})+|\d+)(?:[.,]\d{1,2})?(?![\d])"
# \u043f\u043e\u0434\u043f\u0438\u0441\u0438 \u0434\u0430\u0442\u044b \u043e\u0442\u0447\u0451\u0442\u0430 \u2014 \u0432 \u043f\u043e\u0440\u044f\u0434\u043a\u0435 \u043f\u0440\u0438\u043e\u0440\u0438\u0442\u0435\u0442\u0430
DATE_LABELS = ("\u0432\u0440\u0435\u043c\u044f \u0437\u0430\u043f\u0440\u043e\u0441\u0430", "\u0434\u0430\u0442\u0430 \u0437\u0430\u043f\u0440\u043e\u0441\u0430", "\u0434\u0430\u0442\u0430 \u0437\u0430\u044f\u0432\u043a\u0438", "\u0434\u0430\u0442\u0430 \u0441\u043e\u0433\u043b\u0430\u0441\u0438\u044f")
_CUR = r"(?:UZS|USD|EUR|RUB)"


# дефисы, которые даёт извлечение текста PDF (мягкий перенос U+00AD, неразрывный дефис, минус) → «-»
_DASHES = str.maketrans({0xAD: "-", 0x2010: "-", 0x2011: "-", 0x2012: "-", 0x2212: "-"})


def fold(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").translate(_DASHES).lower().replace("ё", "е")).strip()


def detect(text: str) -> bool:
    """Похоже на отчёт кредитного бюро: не меньше двух разных признаков (КАТМ, InfoScore, «субъект кредитной
    информации», SCORING …)."""
    f = fold(text)[:200000]
    return sum(1 for m in MARKERS if m in f) >= 2


def _amount(s) -> Optional[float]:
    t = str(s or "").replace(chr(0xA0), " ").strip()
    if not t or not re.fullmatch(r"[\d\s.,]+", t):
        return None
    t = t.replace(" ", "")
    if re.fullmatch(r"\d+[.,]\d{1,2}", t):
        t = t.replace(",", ".")
    elif "," in t or t.count(".") > 1:
        t = t.replace(",", "").replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def _date(s) -> Optional[str]:
    t = str(s or "").strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    m2 = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", t)
    try:
        if m:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        elif m2:
            d = date(int(m2.group(3)), int(m2.group(2)), int(m2.group(1)))
        else:
            return None
    except ValueError:
        return None
    return d.isoformat() if date(2000, 1, 1) <= d <= date(2100, 12, 31) else None


def _section(text: str, start_rx: str, end_rx: str) -> str:
    """Кусок текста от заголовка start_rx до следующего заголовка end_rx (или до конца)."""
    m = re.search(start_rx, text, re.I)
    if not m:
        return ""
    rest = text[m.end():]
    e = re.search(end_rx, rest, re.I)
    return rest[:e.start()] if e else rest


def _subject_type(f: str) -> Optional[str]:
    if re.search(r"юридическ\w* лиц|yuridik shaxs|legal entity|юридик шахс", f):
        return "legal"
    if re.search(r"физическ\w* лиц|jismoniy shaxs|individual|жисмоний шахс", f):
        return "individual"
    return None


def parse_text(text: str) -> Optional[dict]:
    """
    Текст отчёта (PDF с текстовым слоем) → {"fields": {...}, "notes": [коды]} или None, если это не отчёт бюро.
    Числа и подписи общего обзора ищутся парами «число — подпись» (и в одной строке, и через перенос);
    у физического лица наименование и ИНН не извлекаются вовсе.
    """
    if not detect(text):
        return None
    t = str(text or "").replace(chr(0xA0), " ").translate(_DASHES)
    head_end = re.search(r"(?i)(?:^|\n)\s*2\.\s*scoring|скоринговый балл", t)
    subj = _section(t, r"субъект\w*\s+кредитной\s+информации|1\.\s*субъект", r"(?:^|\n)\s*2\.\s|скоринговый балл") \
        or t[:head_end.start() if head_end else 4000]
    fs = fold(subj)
    stype = _subject_type(fs) or _subject_type(fold(t[:6000]))
    if stype is None and re.search(r"(?<!\d)\d{14}(?!\d)", subj):
        stype = "individual"                    # ПИНФЛ (14 цифр) есть только у физлица — сам номер не берём
    fields = {"report_date": None, "subject_type": stype, "name": None, "inn": None, "oked": None,
              "score": None, "score_class": None, "score_version": None,
              "overview": {k: None for k in OVERVIEW_KEYS},
              "active": {"count": None, "total_debt": None, "overdue": None, "monthly_payment": None,
                         "creditors": []}}
    # дата отчёта — только по подписи, в порядке DATE_LABELS; без подписи — null (первая дата в тексте — это может
    # быть дата договора или рождения, по ней «свежесть» отчёта не считается)
    for lab in DATE_LABELS:
        m = re.search(r"(?i)" + lab + r"\s*:?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4})", t)
        if m and _date(m.group(1)):
            fields["report_date"] = _date(m.group(1))
            break
    if stype == "legal":
        m = re.search(r"(?im)^\s*(?:наименование|nomi)\s*:?[ \t]*(.*)$", subj)
        name = m.group(1).strip() if m else None
        if m and not name:                      # подпись и значение — в разных строках
            nxt = [x.strip() for x in subj[m.end():].splitlines() if x.strip()]
            name = nxt[0] if nxt else None
        if name and br.is_legal(name):
            fields["name"] = name[:200]
        m = re.search(r"(?i)(?:инн|stir)\s*:?\s*(\d{9})(?!\d)", subj)
        fields["inn"] = m.group(1) if m else None
    m = re.search(r"(?i)(?:окэд|oked|iftt)\s*:?\s*(\d{4,5})(?!\d)", subj or t)
    fields["oked"] = m.group(1) if m else None
    m = re.search(r"(?i)(?:скоринговый балл|skoring bali|scoring score)\s*:?\s*(\d{1,4})(?!\d)", t)
    fields["score"] = int(m.group(1)) if m else None
    m = re.search(r"(?i)(?:класс оценки|baholash sinfi|score class)\s*:?\s*([A-EАВСЕ])\s*([1-9])?", t)
    if m:
        fields["score_class"] = (m.group(1).translate(str.maketrans("АВСЕ", "ABCE")) + (m.group(2) or ""))
    m = re.search(r"(?i)(?:версия скоринга|skoring versiyasi|scoring version)\s*:?\s*(\d{1,2}(?:\.\d{1,2})?)", t)
    fields["score_version"] = m.group(1) if m else None
    # общий обзор: пары «число — подпись» между «ОБЩИЙ ОБЗОР» и следующим разделом
    ov = _section(t, r"общий обзор|umumiy ko'rinish", r"действующие договор|amaldagi shartnoma|(?:^|\n)\s*4\.\s")
    flat = re.sub(r"\s+", " ", ov)
    for m in re.finditer(r"(?<![\d.,])(" + _AMOUNT + r")\s*[-–—]\s*([^\d]+?)(?=\s+\d|$)", flat):
        label = fold(m.group(2))
        for lab, key in _OVERVIEW_LABELS:
            if label.startswith(lab) and fields["overview"][key] is None:
                v = _amount(m.group(1))
                fields["overview"][key] = v if key in MONEY_KEYS else (int(v) if v is not None else None)
                break
    # действующие договоры: строки до «Итого», в строке «Итого» — остаток, просроченная часть, платёж
    sec = _section(t, r"действующие договор\w*|amaldagi shartnomalar",
                   r"(?:^|\n)\s*5\.\s|заявки без договор|закрытые договор|yopilgan")
    if sec:
        it = re.search(r"(?i)(?:итого|jami)\b(.*)", sec, re.S)
        before = sec[:it.start()] if it else sec
        vals = [_amount(x) for x in _total_numbers(it.group(1))[:3]] if it else []
        if len(vals) == 3 and all(v is not None for v in vals):
            fields["active"].update(total_debt=vals[0], overdue=vals[1], monthly_payment=vals[2])
        n = len(re.findall(r"\b" + _CUR + r"\b", before))
        fields["active"]["count"] = n if n or it else None
        fields["active"]["creditors"] = _creditors(before)[:MAX_CREDITORS]
    clean, _err = normalize(fields, strict=False)
    if not clean or not any(clean.get(k) is not None for k in ("score", "score_class")) \
            and not any(v is not None for v in clean["overview"].values()):
        return None
    notes = []
    if clean.get("subject_type") == "individual":
        notes.append("cr_individual")
    if not clean.get("report_date"):
        notes.append("cr_no_date")
    return {"fields": clean, "notes": notes}


def _total_numbers(rest: str) -> list:
    """Числа строки «Итого» по строкам текста (не склеивая соседние ячейки): и «Итого 200 000 000.00 0 12 500 000.00»
    в одну строку, и раскладка «каждое значение на своей строке». Строка со словами после найденных чисел — конец."""
    out = []
    for ln in str(rest or "").splitlines():
        s = ln.replace(" ", " ").strip()
        if not s:
            continue
        words = re.sub(r"\b" + _CUR + r"\b", "", s)
        if out and re.search(r"[A-Za-zА-Яа-яЁё]{2,}", words):
            break
        out += re.findall(_CELL_NUM, re.sub(r"[ \t]+", " ", s))
        if len(out) >= 3:
            break
    return out


def _creditors(before: str) -> list:
    """Банки-кредиторы из строк таблицы действующих договоров. Название, перенесённое на следующую строку
    («АКБ "NAMUNA» / «BANK"»), склеивается: текст копится до номера договора, валюты или числа."""
    seen, buf = [], []

    def flush():
        s = re.sub(r"\s+", " ", " ".join(buf)).strip()
        buf.clear()
        if s and br.is_legal(s) and re.search(r"(?i)bank|банк", s) and s[:160] not in seen:
            seen.append(s[:160])

    for ln in str(before or "").splitlines():
        s = re.sub(r"\s+", " ", ln.replace(" ", " ")).strip()
        if not s:
            continue
        if re.fullmatch(r"\d{1,3}", s):                 # номер строки таблицы отдельной строкой — новая строка
            flush()
            continue
        if re.fullmatch(r"[\d\s.,]+|" + _CUR, s):       # ячейка с числом или валютой
            flush()
            continue
        s = re.sub(r"^\d{1,3}\s+(?=\D)", "", s)          # номер строки таблицы в начале
        if re.match(r"\d{1,3}\s", ln.strip()):
            flush()
        parts = re.split(r"\s\d{6,}|\s" + _CUR + r"\b|\s\d{1,2}\.\d{1,2}\.\d{4}", " " + s, maxsplit=1)
        name = parts[0].strip()
        if name:
            buf.append(name)
        if len(parts) > 1:
            flush()
    flush()
    return seen


def _int(v, lo: int, hi: int):
    if v in (None, ""):
        return None
    if isinstance(v, bool):
        raise ValueError
    if isinstance(v, str):
        x = _amount(v)
        if x is None:
            raise ValueError
        v = x
    if not isinstance(v, (int, float)) or float(v) != int(v) or not lo <= int(v) <= hi:
        raise ValueError
    return int(v)


def _money(v):
    if v in (None, ""):
        return None
    if isinstance(v, bool):
        raise ValueError
    x = _amount(v) if isinstance(v, str) else (float(v) if isinstance(v, (int, float)) else None)
    if x is None or not 0 <= x <= MAX_MONEY:
        raise ValueError
    return round(x, 2)


def _text(v, n: int) -> Optional[str]:
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s[:n] or None


def normalize(raw, strict: bool = True) -> tuple:
    """
    Поля отчёта бюро → (чистые поля | None, ошибка | None). strict (ввод экрана): неверное значение — ошибка
    словами; иначе (ответ модели, разбор текста) — значение отбрасывается. Наименование и ИНН остаются только у
    юридического лица (наименование — с маркером организации); у физического лица — только признак.
    """
    if raw in (None, "", {}):
        return None, None
    if not isinstance(raw, dict):
        return None, "объект {report_date, subject_type, score, score_class, overview{…}, active{…}}"
    out = {}

    def take(key, fn, err):
        try:
            return fn(), None
        except (ValueError, TypeError):
            if strict:
                return None, err
            return None, None

    stype = raw.get("subject_type")
    if stype in (None, ""):
        out["subject_type"] = None
    elif stype in ("legal", "individual"):
        out["subject_type"] = stype
    elif strict:
        return None, "subject_type — legal или individual"
    else:
        out["subject_type"] = None
    rd = raw.get("report_date")
    out["report_date"] = _date(rd) if rd not in (None, "") else None
    if rd not in (None, "") and out["report_date"] is None and strict:
        return None, "report_date — дата ГГГГ-ММ-ДД или ДД.ММ.ГГГГ с 01.01.2000 по 31.12.2100"
    legal = out["subject_type"] == "legal"
    name = _text(raw.get("name"), 200) if legal else None
    out["name"] = name if name and br.is_legal(name) else None
    inn = re.sub(r"\D", "", str(raw.get("inn") or "")) if legal else ""
    out["inn"] = inn if re.fullmatch(r"\d{9}", inn) else None
    oked = re.sub(r"\D", "", str(raw.get("oked") or ""))
    out["oked"] = oked if re.fullmatch(r"\d{4,5}", oked) else None
    out["score"], e = take("score", lambda: _int(raw.get("score"), 0, 1000), "score — целое от 0 до 1000")
    if e:
        return None, e
    sc = str(raw.get("score_class") or "").strip().upper().translate(str.maketrans("АВСЕ", "ABCE"))
    if sc and not re.fullmatch(r"[A-E][1-9]?", sc):
        if strict:
            return None, "score_class — буква A–E и цифра, например A1"
        sc = ""
    out["score_class"] = sc or None
    ver = _text(raw.get("score_version"), 10)
    out["score_version"] = ver if ver and re.fullmatch(r"\d{1,2}(?:\.\d{1,2})?", ver) else None
    ov = raw.get("overview") if isinstance(raw.get("overview"), dict) else {}
    if raw.get("overview") not in (None, "", {}) and not isinstance(raw.get("overview"), dict) and strict:
        return None, "overview — объект"
    out["overview"] = {}
    for k in OVERVIEW_KEYS:
        if k in MONEY_KEYS:
            out["overview"][k], e = take(k, lambda: _money(ov.get(k)), f"overview.{k} — сумма не меньше нуля")
        else:
            out["overview"][k], e = take(k, lambda: _int(ov.get(k), 0, MAX_COUNT),
                                         f"overview.{k} — целое от 0 до {MAX_COUNT}")
        if e:
            return None, e
    ac = raw.get("active") if isinstance(raw.get("active"), dict) else {}
    if raw.get("active") not in (None, "", {}) and not isinstance(raw.get("active"), dict) and strict:
        return None, "active — объект"
    out["active"] = {}
    for k in ACTIVE_KEYS:
        if k in ACTIVE_MONEY:
            out["active"][k], e = take(k, lambda: _money(ac.get(k)), f"active.{k} — сумма не меньше нуля")
        else:
            out["active"][k], e = take(k, lambda: _int(ac.get(k), 0, MAX_COUNT),
                                       f"active.{k} — целое от 0 до {MAX_COUNT}")
        if e:
            return None, e
    cred = ac.get("creditors")
    if cred not in (None, "", []) and not isinstance(cred, list):
        if strict:
            return None, "active.creditors — список названий банков"
        cred = []
    names = []
    for c in (cred or [])[:MAX_CREDITORS]:
        s = _text(c.get("name") if isinstance(c, dict) else c, 160)
        if s and br.is_legal(s) and s not in names:
            names.append(s)                       # кредитор — организация; гражданин сюда не попадает
    out["active"]["creditors"] = names
    if all(out[k] is None for k in ("score", "score_class", "report_date")) \
            and all(v is None for v in out["overview"].values()) \
            and all(v is None for k, v in out["active"].items() if k != "creditors"):
        return None, ("в отчёте бюро нет ни балла, ни класса, ни даты, ни показателей" if strict else None)
    return out, None


def from_model(raw) -> Optional[dict]:
    """Блок credit_report ответа модели по скану → чистые поля или None (нет отчёта или пусто)."""
    if not isinstance(raw, dict):
        return None
    got, _err = normalize(raw, strict=False)
    return got


def flat(fields: Optional[dict]) -> dict:
    """Поля одним уровнем (overview.applications, active.count …) — для сравнения ввода с загрузкой."""
    out = {}
    for k, v in (fields or {}).items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                out[f"{k}.{k2}"] = list(v2) if isinstance(v2, list) else v2
        else:
            out[k] = v
    return out


def age_days(fields: Optional[dict], today: Optional[date] = None) -> Optional[int]:
    try:
        return ((today or date.today()) - date.fromisoformat(str((fields or {}).get("report_date")))).days
    except (TypeError, ValueError):
        return None


# схема блока для модели (скан отчёта бюро): только эти поля; телефон, e-mail, адрес, учредители — не нужны
MODEL_SCHEMA = (
    '{"file": 1, "report_date": "ГГГГ-ММ-ДД или null", "subject_type": "legal|individual|null", '
    '"name": "наименование организации или null (у физического лица — всегда null)", '
    '"inn": "ИНН организации (9 цифр) или null (у физического лица — всегда null)", "oked": "код ОКЭД или null", '
    '"score": "скоринговый балл числом или null", "score_class": "класс оценки, например A1, или null", '
    '"score_version": "версия скоринга или null", '
    '"overview": {"applications": 0, "contracts": 0, "contingent": 0, "inquiries": 0, "avg_monthly_payment": 0, '
    '"overdue_principal_count": 0, "max_overdue_principal_days": 0, "max_overdue_principal_amount": 0, '
    '"max_overdue_interest_days": 0, "overdue_interest_total": 0}, '
    '"active": {"count": 0, "total_debt": 0, "overdue": 0, "monthly_payment": 0, '
    '"creditors": ["название банка-кредитора"]}}')
MODEL_HINT = (
    "отчёт кредитного бюро (КАТМ, «Кредитно-информационный аналитический центр», InfoScore, «субъект кредитной "
    "информации», SCORING): для такого снимка document_kind = credit_report и заполни credit_report — числа "
    "цифрами без пробелов, даты ГГГГ-ММ-ДД; общий обзор (overview) — числа из блока «общий обзор»; действующие "
    "договоры (active) — количество строк и строка «итого». если субъект — физическое лицо: subject_type = "
    "individual, name и inn = null, ФИО, ПИНФЛ, паспорт, адрес и телефон не пиши. телефон, e-mail, адрес и "
    "учредителей не пиши ни у кого. чего нет — null, не выдумывай; если такого отчёта нет — credit_report = null.")
# сканы отчёта бюро модели не читаются (по умолчанию): вид называется только для того, чтобы сервер всё отбросил
MODEL_HINT_OFF = (
    "отчёт кредитного бюро (КАТМ, «Кредитно-информационный аналитический центр», InfoScore, «субъект кредитной "
    "информации», SCORING): для такого снимка укажи только document_kind = credit_report и ничего из него не "
    "переписывай — ни в fields, ни куда-либо ещё.")
