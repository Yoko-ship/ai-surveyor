"""
Портфельный аудит: выгрузка договоров из учётной системы (xlsx) → массовая проверка движком.

Что делает:
  1. читает лист, сам находит строку заголовков и сопоставляет колонки по синонимам (рус/узб/лат);
  2. по каждой строке строит engine.Input и вызывает engine.calculate;
  3. складывает результат в portfolio_batches / portfolio_reviews и отдаёт сводку.

Точки подключения:
  POST /portfolio/import                 — загрузить xlsx (параметр sheet — имя листа, необязателен)
  GET  /portfolio/batches                — список загрузок
  GET  /portfolio/batches/{id}?verdict=  — строки загрузки
  GET  /portfolio/batches/{id}/summary   — сводка по загрузке
  GET  /portfolio                        — страница
"""
import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import openpyxl
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from . import db
from .engine import Input, calculate, premium_of

ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = db.DATA_DIR / "uploads" / "portfolio"

router = APIRouter()

# Порядок важен: поле, которое идёт раньше, забирает колонку первым
# (например, «страховая стоимость» — до «страховой суммы», «дата начала» — до «срока»).
SYNONYMS = [
    ("value_amount", ["страховая стоимость", "страх. стоимость", "стоимость", "qiymat", "haqiqiy qiymat"]),
    ("sum_insured", ["страховая сумма", "страх. сумма", "страховая сум", "ответственность", "sug'urta summasi",
                     "sug‘urta summasi", "sugurta summasi", "summa", "javobgarlik"]),
    ("franchise", ["франшиза", "franshiza", "franchise"]),
    ("rate", ["ставка", "тариф", "tarif", "stavka", "%"]),
    ("premium", ["премия", "страховой взнос", "взнос", "mukofot", "premiya", "badal"]),
    ("product", ["вид страхования", "вид страх", "продукт", "вид", "класс", "sug'urta turi", "sug‘urta turi",
                 "turi", "mahsulot", "product"]),
    ("external_no", ["договор", "полис", "shartnoma", "polis", "contract", "№"]),
    ("policyholder", ["страхователь", "клиент", "sug'urtalovchi", "sug‘urtalovchi", "sugurtalovchi", "mijoz"]),
    ("branch", ["филиал", "подразделение", "отделение", "filial", "bo'lim"]),
    ("region", ["регион", "область", "город", "viloyat", "hudud", "shahar"]),
    ("date_from", ["дата начала", "начало", "срок с", "действует с", "boshlanish", "boshlan"]),
    ("date_to", ["дата окончания", "окончание", "срок по", "действует по", "tugash", "tugallan"]),
    ("term_days", ["срок", "дней", "muddat", "kun"]),
]
# заголовки, которые никогда не считаем полезными
IGNORE = ["п/п", "порядк", "t/r", "№ п"]

LABELS = {"external_no": "Договор №", "product": "Вид страхования / продукт", "policyholder": "Страхователь",
          "branch": "Филиал", "sum_insured": "Страховая сумма", "value_amount": "Страховая стоимость",
          "premium": "Премия", "rate": "Ставка", "franchise": "Франшиза", "region": "Регион",
          "date_from": "Дата начала", "date_to": "Дата окончания", "term_days": "Срок, дней"}

REQUIRED = ["sum_insured"]          # без этого проверять нечего
OBJECT_BY_CLASS = {"8": "Склад", "9": "Склад", "3": "Легковой", "14": "Кредит"}
DEFAULT_FACTORS = {"construction": "mixed", "activity": "warehouse", "protection": "alarm",
                   "seismic": "z8", "wear": "mid", "loss_history": "clean"}


# ---------- чтение файла ----------

def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s).replace("\n", " ")).strip().lower()


def read_sheet(path, sheet: Optional[str] = None) -> dict:
    """Лист → {'headers': [...], 'rows': [[...]], 'header_row': n, 'sheet': имя, 'formats': [...]}.
    Строка заголовков — первая, где не меньше трёх непустых текстовых ячеек."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    if sheet:
        if sheet not in wb.sheetnames:
            raise ValueError(f"Листа «{sheet}» нет в файле. Есть: {', '.join(wb.sheetnames)}")
        ws = wb[sheet]
    else:
        ws = wb[wb.sheetnames[0]]
        # первый лист может быть пустым — берём первый, где есть данные
        for name in wb.sheetnames:
            if wb[name].max_row and wb[name].max_row > 1:
                ws = wb[name]
                break
    header_row = None
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=min(ws.max_row, 50)), start=1):
        texts = [c.value for c in row if isinstance(c.value, str) and c.value.strip()]
        if len(texts) >= 3:
            header_row = i
            break
    if header_row is None:
        raise ValueError("Не нашёл строку заголовков: нужна строка, где не меньше трёх текстовых ячеек.")
    headers = [str(c.value).strip() if c.value is not None else "" for c in ws[header_row]]
    rows, formats = [], None
    for row in ws.iter_rows(min_row=header_row + 1):
        vals = [c.value for c in row]
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in vals):
            continue
        if formats is None:   # форматы ячеек первой строки данных: чтобы понять, процент ли «ставка»
            formats = [c.number_format or "" for c in row]
        rows.append(vals)
    wb.close()
    return {"headers": headers, "rows": rows, "header_row": header_row, "sheet": ws.title, "formats": formats or []}


def map_columns(headers: list) -> dict:
    """Сопоставление поле -> индекс колонки. Возвращает {'index': {...}, 'names': {...}, 'missing': [...]}."""
    norm = [_norm(h) for h in headers]
    taken, index = set(), {}
    for field, pats in SYNONYMS:
        for i, h in enumerate(norm):
            if not h or i in taken or any(x in h for x in IGNORE):
                continue
            if any(p in h for p in pats):
                index[field] = i
                taken.add(i)
                break
    names = {f: headers[i] for f, i in index.items()}
    missing = [f for f in REQUIRED if f not in index]
    return {"index": index, "names": names, "missing": missing}


def explain_missing(missing: list) -> str:
    return "Не найдены колонки: " + "; ".join(f"«{LABELS[m]}»" for m in missing) + \
        ". Проверьте заголовки в файле или переименуйте колонки."


# ---------- разбор значений ----------

def to_number(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(" ", "").replace("%", "")
    if not s or s in ("-", "—"):
        return None
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def to_date(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y"):
            try:
                return datetime.strptime(v.strip()[:10], fmt).date()
            except ValueError:
                pass
    return None


def product_code_of(product_cell, external_no, known: dict) -> Optional[str]:
    """Код продукта: из колонки вида («0807.Страхование…», 807, «0807»), иначе из номера договора 0275/0311/26/0030."""
    if product_cell is not None:
        if isinstance(product_cell, (int, float)) and not isinstance(product_cell, bool):
            code = f"{int(product_cell):04d}"
            if code in known:
                return code
        s = str(product_cell).strip()
        m = re.match(r"^\s*(\d{4})\b", s) or re.search(r"\b(\d{4})\b", s)
        if m and m.group(1) in known:
            return m.group(1)
        if m and not known:
            return m.group(1)
    if external_no:
        parts = re.split(r"[/\-\s]+", str(external_no).strip())
        if len(parts) >= 2 and re.fullmatch(r"\d{4}", parts[1]) and (not known or parts[1] in known):
            return parts[1]
        for p in parts:
            if re.fullmatch(r"\d{4}", p) and p in known:
                return p
    return None


def _cell(row, idx: dict, field: str):
    i = idx.get(field)
    return row[i] if i is not None and i < len(row) else None


def parse_row(row: list, mapping: dict, formats: list, known: dict) -> dict:
    """Строка выгрузки → плоский словарь полей договора."""
    idx = mapping["index"]
    g = lambda f: _cell(row, idx, f)
    external_no = g("external_no")
    external_no = str(external_no).strip() if external_no is not None else None
    rate = to_number(g("rate"))
    # ставка в процентном формате Excel хранится долей: 0,0005 → 0,05 %
    if rate is not None and "rate" in idx and idx["rate"] < len(formats) and "%" in formats[idx["rate"]]:
        rate *= 100
    d_from, d_to = to_date(g("date_from")), to_date(g("date_to"))
    term = to_number(g("term_days"))
    if term is None and d_from and d_to and d_to > d_from:
        term = (d_to - d_from).days
    fr = to_number(g("franchise"))
    return {
        "external_no": external_no,
        "product_code": product_code_of(g("product"), external_no, known),
        "product_text": str(g("product")).strip() if g("product") is not None else None,
        "policyholder": str(g("policyholder")).strip() if g("policyholder") is not None else None,
        "branch": str(g("branch")).strip() if g("branch") is not None else None,
        "region": str(g("region")).strip() if g("region") is not None else None,
        "sum_insured": to_number(g("sum_insured")),
        "value_amount": to_number(g("value_amount")),
        "premium": to_number(g("premium")),
        "rate": rate,
        "franchise": fr or 0.0,
        "term_days": int(term) if term and term > 0 else 365,
        "date_from": d_from.isoformat() if d_from else None,
        "date_to": d_to.isoformat() if d_to else None,
    }


# ---------- проверка движком ----------

def review_row(ref, p: dict) -> dict:
    """Один договор → результат проверки. Если данных не хватает — вердикт «не проверен» с причиной."""
    out = {k: p.get(k) for k in ("external_no", "product_code", "policyholder", "branch", "sum_insured",
                                 "value_amount", "region", "term_days")}
    out.update({"premium_file": p.get("premium"), "premium_calc": None, "applied_rate_pct": None,
                "technical_rate_pct": None, "min_rate_pct": None, "verdict": "не проверен",
                "violations": [], "note": None})
    if not p.get("sum_insured"):
        out["note"] = "нет страховой суммы"
        return out
    if not p.get("product_code"):
        out["note"] = "не распознан код продукта" + (f" («{p['product_text']}»)" if p.get("product_text") else "")
        return out
    classes = ref.product_classes.get(p["product_code"])
    if not classes:
        out["note"] = f"продукт {p['product_code']} не найден в справочнике"
        return out
    cls = classes[0]
    value = p["value_amount"] if p.get("value_amount") else p["sum_insured"]   # нет стоимости — считаем равной сумме
    if not p.get("value_amount"):
        out["note"] = "страховая стоимость не указана, принята равной сумме"
    rate = p.get("rate")
    if rate is None and p.get("premium") and p["sum_insured"]:
        rate = p["premium"] / p["sum_insured"] * 100 * 365 / p["term_days"]
    factors = {**DEFAULT_FACTORS, "franchise": "f1" if (p.get("franchise") or 0) > 0 else "f0"}
    inp = Input(product_code=p["product_code"], class_code=cls,
                object_type=OBJECT_BY_CLASS.get(cls, "Прочее"),
                value_amount=value, sum_insured=p["sum_insured"], term_days=p["term_days"],
                factors=factors, applied_rate_pct=rate if rate else None,
                manual_reason="из выгрузки" if rate else "",
                # в портфельном режиме проверяем цену и закон, а не документооборот: эти три
                # предупреждения (документы, оплата, раскрытие) к выгрузке неприменимы
                docs_received=[c["doc_name"] for c in ref.checklists], premium_paid=True, disclosure_done=True)
    res = calculate(ref, inp)
    r = res["rates"]
    prem_calc = round(premium_of(r["auto_pct"], p["sum_insured"], p["term_days"]))
    prem_file = p.get("premium") if p.get("premium") is not None else res["premium"]
    out.update({
        "class_code": cls, "object_type": inp.object_type,
        "applied_rate_pct": round(r["applied_pct"], 4), "technical_rate_pct": round(r["technical_pct"], 4),
        "min_rate_pct": r["min_pct"], "premium_file": prem_file, "premium_calc": prem_calc,
        "premium_diff": round(prem_calc - prem_file), "verdict": res["verdict"],
        # ручная ставка «из выгрузки» — не нарушение, а способ передачи ставки; в список не включаем
        "violations": [{"rule": c["rule"], "status": c["status"], "title": c["title"]}
                       for c in res["checks"] if c["status"] != "ok" and c["rule"] != "manual_rate"],
    })
    return out


def review_rows(ref, sheet: dict, mapping: dict) -> list:
    """Все строки листа → список результатов. Строки без номера и без суммы (итоги, подписи) пропускаются."""
    known = ref.product_classes
    out = []
    for i, row in enumerate(sheet["rows"], start=sheet["header_row"] + 1):
        p = parse_row(row, mapping, sheet["formats"], known)
        if p["external_no"] is None and p["sum_insured"] is None:
            continue
        if p["external_no"] and _norm(p["external_no"]).startswith(("итого", "всего", "jami")):
            continue
        r = review_row(ref, p)
        r["row_no"] = i
        r["raw"] = {h: _json_safe(v) for h, v in zip(sheet["headers"], row) if h}
        out.append(r)
    return out


def _json_safe(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


# ---------- сохранение и сводка ----------

def save_batch(con, file_name: str, mapping: dict, reviews: list) -> int:
    ts = db.now()
    cnt = lambda v: sum(1 for r in reviews if r["verdict"] == v)
    cur = con.execute(
        "INSERT INTO portfolio_batches (imported_at, file_name, rows_total, rows_ok, rows_warn, rows_stop, mapping)"
        " VALUES (?,?,?,?,?,?,?)",
        (ts, file_name, len(reviews), cnt("ок"), cnt("на утверждение"), cnt("отклонено"),
         json.dumps(mapping["names"], ensure_ascii=False)))
    bid = cur.lastrowid
    for r in reviews:
        con.execute(
            "INSERT INTO portfolio_reviews (batch_id, imported_at, file_name, external_no, product_code, policyholder,"
            " branch, sum_insured, value_amount, premium_file, premium_calc, applied_rate_pct, technical_rate_pct,"
            " min_rate_pct, verdict, violations, row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (bid, ts, file_name, r["external_no"], r["product_code"], r["policyholder"], r["branch"],
             r["sum_insured"], r["value_amount"], r["premium_file"], r["premium_calc"], r["applied_rate_pct"],
             r["technical_rate_pct"], r["min_rate_pct"], r["verdict"],
             json.dumps(r["violations"], ensure_ascii=False),
             json.dumps({"raw": r.get("raw"), "row_no": r.get("row_no"), "note": r.get("note"),
                         "premium_diff": r.get("premium_diff")}, ensure_ascii=False)))
    db.audit(con, "backend", "портфельный аудит: загрузка", f"portfolio_batch:{bid}",
             {"file": file_name, "rows": len(reviews), "stop": cnt("отклонено")})
    return bid


def _review_from_db(r: dict) -> dict:
    extra = json.loads(r.pop("row_json") or "{}")
    r["violations"] = json.loads(r["violations"] or "[]")
    r["note"] = extra.get("note")
    r["row_no"] = extra.get("row_no")
    r["premium_diff"] = extra.get("premium_diff")
    r["raw"] = extra.get("raw")
    return r


def batch_rows(con, bid: int, verdict: Optional[str] = None) -> list:
    sql, args = "SELECT * FROM portfolio_reviews WHERE batch_id=?", [bid]
    if verdict:
        sql += " AND verdict=?"; args.append(verdict)
    sql += " ORDER BY id"
    return [_review_from_db(r) for r in db.rows(con, sql, *args)]


def summarize(reviews: list) -> dict:
    """Сводка по списку результатов: счётчики нарушений, недополученная премия, топы по филиалам и продуктам."""
    has = lambda r, code: any(v["rule"] == code for v in r["violations"])
    verdicts = {}
    for r in reviews:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
    lost = sum(max(r.get("premium_diff") or 0, 0) for r in reviews)
    tops = {"branch": {}, "product_code": {}}
    for r in reviews:
        if r["verdict"] in ("ок", "не проверен"):
            continue
        for key in tops:
            k = r.get(key) or "—"
            t = tops[key].setdefault(k, {"name": k, "rows": 0, "stop": 0, "lost": 0})
            t["rows"] += 1
            t["stop"] += r["verdict"] == "отклонено"
            t["lost"] += max(r.get("premium_diff") or 0, 0)
    top = lambda d: sorted(d.values(), key=lambda t: (-t["stop"], -t["rows"], -t["lost"]))[:10]
    return {
        "rows_total": len(reviews),
        "by_verdict": verdicts,
        "below_min": sum(has(r, "min_rate") for r in reviews),
        "below_technical": sum(has(r, "below_technical") for r in reviews),
        "sum_over_value": sum(has(r, "sum_over_value") for r in reviews),
        "no_franchise": sum(has(r, "no_franchise") for r in reviews),
        "risk_limit_20": sum(any(v["rule"] == "risk_limit_20" and v["status"] == "stop" for v in r["violations"])
                             for r in reviews),
        "unchecked": sum(r["verdict"] == "не проверен" for r in reviews),
        "sum_insured_total": sum(r["sum_insured"] or 0 for r in reviews),
        "premium_file_total": sum(r["premium_file"] or 0 for r in reviews),
        "premium_calc_total": sum(r["premium_calc"] or 0 for r in reviews if r["premium_calc"] is not None),
        "lost_premium": round(lost),
        "top_branches": top(tops["branch"]),
        "top_products": top(tops["product_code"]),
    }


def import_file(path, file_name: str, sheet: Optional[str] = None) -> dict:
    """Полный цикл без сервера: файл → сопоставление → проверка → база. Ошибки — ValueError по-русски."""
    sh = read_sheet(path, sheet)
    mapping = map_columns(sh["headers"])
    if mapping["missing"]:
        raise ValueError(explain_missing(mapping["missing"]) + f" Заголовки на листе «{sh['sheet']}» (строка {sh['header_row']}): "
                         + ", ".join(h for h in sh["headers"] if h))
    if "product" not in mapping["index"] and "external_no" not in mapping["index"]:
        raise ValueError("Не найдены ни колонка «Вид страхования», ни «Договор №» — код продукта взять негде.")
    with db.tx() as con:
        ref = db.load_reference(con)
        reviews = review_rows(ref, sh, mapping)
        if not reviews:
            raise ValueError("В файле нет строк с договорами после строки заголовков.")
        bid = save_batch(con, file_name, mapping, reviews)
    return {"batch_id": bid, "file_name": file_name, "sheet": sh["sheet"], "header_row": sh["header_row"],
            "mapping": mapping["names"],
            "unmapped": [h for i, h in enumerate(sh["headers"]) if h and i not in mapping["index"].values()],
            "summary": summarize(reviews), "rows": reviews}


# ---------- API ----------

@router.post("/portfolio/import")
async def portfolio_import(file: UploadFile = File(...), sheet: Optional[str] = None):
    name = Path(file.filename or "выгрузка.xlsx").name
    if not name.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Нужен файл Excel (.xlsx). Старый формат .xls сохраните как .xlsx.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    if dest.read_bytes()[:2] != b"PK":
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Файл не похож на xlsx (нет сигнатуры PK) — возможно, это .xls или html.")
    try:
        out = import_file(dest, name, sheet)
    except ValueError as e:
        raise HTTPException(400, str(e))
    out["stored"] = str(dest.relative_to(ROOT))
    return out


@router.get("/portfolio/batches")
def portfolio_batches(limit: int = 50):
    with db.tx() as con:
        out = db.rows(con, "SELECT * FROM portfolio_batches ORDER BY id DESC LIMIT ?", limit)
    for b in out:
        b["mapping"] = json.loads(b["mapping"] or "{}")
    return out


def _batch(con, bid: int) -> dict:
    b = db.rows(con, "SELECT * FROM portfolio_batches WHERE id=?", bid)
    if not b:
        raise HTTPException(404, "Загрузка не найдена")
    b[0]["mapping"] = json.loads(b[0]["mapping"] or "{}")
    return b[0]


@router.get("/portfolio/batches/{bid}")
def portfolio_batch(bid: int, verdict: Optional[str] = None):
    with db.tx() as con:
        b = _batch(con, bid)
        return {"batch": b, "rows": batch_rows(con, bid, verdict)}


@router.get("/portfolio/batches/{bid}/summary")
def portfolio_summary(bid: int):
    with db.tx() as con:
        b = _batch(con, bid)
        return {"batch": b, "summary": summarize(batch_rows(con, bid))}


@router.get("/portfolio", response_class=HTMLResponse)
def portfolio_page():
    return (ROOT / "app" / "portfolio.html").read_text(encoding="utf-8")
