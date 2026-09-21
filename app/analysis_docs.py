"""
Вкладка «Аналитика» мини-аппа по шагам (задача 150, 21.09.2026):
продукт → какие документы нужны → загрузка договора → суммы → анализ.

    GET    /analytics/risk/docs?product_code=&class_code=&object_type=  — какие документы нужны
    POST   /analytics/risk/document            — договор (PDF, DOCX) для анализа, без запроса
    GET    /analytics/risk/document/{doc_id}   — результат разбора ещё раз (только владелец)
    DELETE /analytics/risk/document/{doc_id}   — удалить раньше срока (только владелец)

Список документов — справочник checklists, отбор строк — та же функция, что у движка в проверке
docs_missing (engine.checklist_items). Разбор файла — ядро app/ingest.py (parse_path) плюс
условия договора страхования (ingest.extract_contract_terms).

Хранение: файл — DATA_DIR/analysis/<doc_id>/document.<pdf|docx>, 24 часа; в базе (analysis_docs)
только вид, язык, статус и замаскированные поля — без текста документа и без ПД. Просроченное
удаляется при следующей загрузке и фоновой задачей «analysis-cleanup» (app/main.py).
В журнал пишем id, вид, язык, статус и число полей — без имени файла и значений.
"""
import json
import re
import secrets
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from . import auth, db
from . import docparse as D
from . import engine
from . import ingest
from . import market_picture as mp
from . import risk_analytics as ra

router = APIRouter()

DIR = db.DATA_DIR / "analysis"          # тесты подменяют на временную папку
TTL_HOURS = 24
MAX_BYTES = 15 * 1024 * 1024
MAX_DOC_IDS = 10
SOURCE = "договор"

OLD_HINT = {
    "doc": "Формат .doc не поддерживается. Откройте файл в Word и сохраните как .docx (или PDF).",
    "xls": "Формат .xls не подходит для договора. Откройте файл и сохраните как .docx или PDF.",
    "rtf": "Формат .rtf не поддерживается. Откройте файл в Word и сохраните как .docx (или PDF).",
    "odt": "Формат .odt не поддерживается. Откройте файл в Word и сохраните как .docx (или PDF).",
}
ONLY_PDF_DOCX = "Для анализа принимается договор в PDF или Word (.docx)."
MIME = {"pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
NOT_FOUND = "Документ не найден или срок его хранения (24 часа) истёк — загрузите заново"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS analysis_docs (
    id          TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    filename    TEXT,
    mime        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    kind        TEXT,
    language    TEXT,
    status      TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
)"""

SCOPE_SOURCE = {"всегда": "общий", "класс": "класс", "продукт": "продукт", "тип_объекта": "тип объекта"}
LEGAL_RE = re.compile(r"(ПКМ|ЗРУ|ст\.\s*\d|Правил|Положени|прил\.|Закон|Указ)")


def ensure_table(con) -> None:
    con.execute(SCHEMA_SQL)


# --------------------------------------------------------------------------- #
# Шаг 2: какие документы нужны
# --------------------------------------------------------------------------- #

def _item(c: dict) -> dict:
    cond = (c.get("condition") or "").strip() or None
    legal = cond if cond and LEGAL_RE.search(cond) else None
    out = {"doc_name": c["doc_name"], "source": SCOPE_SOURCE.get(c["scope_type"], c["scope_type"]),
           "scope_code": c.get("scope_code")}
    if c["required"]:
        if legal:
            out["legal_ref"] = legal
        elif cond:
            out["why"] = cond
    else:
        out["when"] = cond                  # у необязательных condition — когда документ запрашивают
        if legal:
            out["legal_ref"] = legal
    return out


def required_docs(con, product_code: str = "", class_code: str = "", object_type: str = "") -> dict:
    """
    Документы по продукту, классу и типу объекта. Если у продукта свой чек-лист (строки «продукт») —
    берём его; иначе — общий и по классам продукта (или по переданному классу). Строки по типу
    объекта добавляются всегда. Источник каждой строки помечен.
    """
    product_code = (product_code or "").strip()
    object_type = (object_type or "").strip()
    product = None
    classes = ra.parse_classes(class_code) if class_code else []
    if product_code:
        p = db.rows(con, "SELECT code, name FROM products WHERE code=?", product_code)
        if not p:
            raise HTTPException(404, f"Продукт {product_code} не найден в справочнике")
        pcl = [r["class_code"] for r in db.rows(
            con, "SELECT class_code FROM product_classes WHERE product_code=? ORDER BY part_no", product_code)]
        product = {"code": p[0]["code"], "name": p[0]["name"], "classes": pcl}
        classes = classes or pcl
    if not product and not classes:
        raise HTTPException(422, "Укажите продукт или класс страхования")
    known = {r["code"] for r in db.rows(con, "SELECT code FROM classes")}
    unknown = [c for c in classes if c not in known]
    if unknown:
        raise HTTPException(422, "Неизвестный класс страхования: " + ", ".join(unknown))

    lists = db.load_reference(con).checklists
    by_product = [c for c in engine.checklist_items(lists, None, object_type, product_code or None)
                  if c["scope_type"] != "всегда"] if product else []
    if any(c["scope_type"] == "продукт" for c in by_product):
        rows, basis = by_product, "продукт"
        basis_text = f"Чек-лист продукта {product_code} из справочника"
    else:
        rows, seen, basis = [], set(), "класс"
        for cls in classes:
            for c in engine.checklist_items(lists, cls, object_type):
                if c["doc_name"] not in seen:
                    seen.add(c["doc_name"])
                    rows.append(c)
        basis_text = ((f"У продукта {product_code} своего чек-листа нет — " if product else "")
                      + "общий список и документы по классу " + ", ".join(classes))
    if object_type and any(c["scope_type"] == "тип_объекта" for c in rows):
        basis_text += f"; плюс документы для типа объекта «{object_type}»"
    req = [_item(c) for c in rows if c["required"]]
    opt = [_item(c) for c in rows if not c["required"]]
    return {"product": product, "class_code": "/".join(classes), "classes": classes,
            "object_type": object_type or None, "basis": basis, "basis_text": basis_text,
            "required": req, "optional": opt, "count": len(req) + len(opt),
            "required_count": len(req), "optional_count": len(opt),
            "source": "справочник checklists"}


@router.get("/analytics/risk/docs")
def risk_docs(product_code: str = "", class_code: str = "", object_type: str = "") -> dict:
    with db.tx() as con:
        return required_docs(con, product_code[:20], class_code[:20], object_type[:120])


# --------------------------------------------------------------------------- #
# Шаг 3: договор для анализа
# --------------------------------------------------------------------------- #

def _expired_before(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def cleanup(con=None) -> int:
    """Удаляет просроченные документы: строки и папки. Возвращает число удалённых."""
    def run(c):
        ensure_table(c)
        now = _expired_before(datetime.now())
        old = [r["id"] for r in db.rows(c, "SELECT id FROM analysis_docs WHERE expires_at <= ?", now)]
        for doc_id in old:
            _remove_dir(doc_id)
        if old:
            c.execute("DELETE FROM analysis_docs WHERE expires_at <= ?", (now,))
            db.audit(c, "система", "удалены просроченные документы аналитики", "analysis_docs",
                     {"count": len(old)})
        # папки без строки в базе (сбой между записью файла и базы) — тоже мусор
        if DIR.exists():
            ids = {r["id"] for r in db.rows(c, "SELECT id FROM analysis_docs")}
            for p in DIR.iterdir():
                if p.is_dir() and p.name not in ids:
                    age = datetime.now().timestamp() - p.stat().st_mtime
                    if age > TTL_HOURS * 3600:
                        shutil.rmtree(p, ignore_errors=True)
        return len(old)
    if con is not None:
        return run(con)
    with db.tx() as c:
        return run(c)


def _remove_dir(doc_id: str) -> None:
    p = (DIR / doc_id).resolve()
    if DIR.resolve() in p.parents:          # путь из базы всё равно проверяем
        shutil.rmtree(p, ignore_errors=True)


def _fmt_of(blob: bytes, filename: str) -> str:
    """Формат по содержимому (как в ingest.sniff_format), расширение — только для OLE (.doc/.xls)."""
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if blob[:5] == b"%PDF-":
        return "pdf"
    if blob[:8] == ingest.OLE_SIGNATURE:
        return "xls" if ext in ("xls", "xlt") else "doc"
    if blob[:5] == b"{\\rtf":
        return "rtf"
    if blob[:4] == b"PK\x03\x04":
        import io
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                names = set(z.namelist())
        except Exception:
            return "unknown"
        if "word/document.xml" in names:
            return "docx"
        if any(n.startswith("xl/") for n in names):
            return "xlsx"
        if "mimetype" in names:
            return "odt"
        return "unknown"
    if ext in OLD_HINT:
        return ext
    return "unknown"


# Тип объекта по словам договора → значение из справочника базовых ставок. Берём, только если
# совпал ровно один тип и он есть у класса.
OBJECT_TYPE_WORDS = [
    ("Склад", ("склад", "ombor", "омбор", "warehouse")),
    ("Офис", ("офис", "административн", "ofis", "office")),
    ("Магазин", ("магазин", "торгов", "do'kon", "дўкон", "shop", "retail")),
    ("Гостиница", ("гостиниц", "отель", "mehmonxona", "hotel")),
    ("Жильё", ("жилой дом", "квартир", "жиль", "turar joy", "турар жой")),
    ("Производство", ("производств", "цех", "завод", "фабрик", "ishlab chiqarish", "factory", "plant")),
    ("Машины и оборудование", ("оборудован", "станок", "станки", "uskuna", "equipment", "machinery")),
    ("Легковой", ("легков",)),
    ("Грузовой", ("грузов",)),
    ("Спецтехника", ("спецтехник", "экскаватор", "бульдозер", "автокран", "погрузчик", "трактор")),
]
CONSTRUCTION_WORDS = [("reinforced", ("железобетон", "бетон", "кирпич", "камен", "g'isht", "ғишт", "brick", "concrete")),
                      ("wood", ("дерев", "сэндвич", "сендвич", "легких металлоконструкц", "yog'och", "wood")),
                      ("mixed", ("смешан", "aralash", "mixed"))]
ACTIVITY_WORDS = [("warehouse", ("склад", "хранени", "ombor")),
                  ("office", ("офис", "торговл", "магазин", "ofis", "savdo")),
                  ("food", ("пищев", "хлебо", "мясо", "молоч", "кондитер", "oziq-ovqat")),
                  ("flammable", ("горюч", "лакокрас", "нефт", "газов", "деревообработ", "химическ"))]
VEHICLE_WORDS = [("car", ("легков",)), ("truck", ("грузов",)),
                 ("special", ("спецтехник", "экскаватор", "бульдозер", "автокран", "погрузчик", "трактор")),
                 ("ev", ("электромобил",))]


def _one(text: str, table: list) -> Optional[str]:
    """Код, если в тексте нашлись слова ровно одной группы; иначе None (не угадываем)."""
    t = (text or "").lower().replace("ё", "е")
    hit = [code for code, words in table if any(w in t for w in words)]
    return hit[0] if len(hit) == 1 else None


def _pf(value, label: str, found_by: str = None, confidence: str = None, **extra) -> dict:
    out = {"value": value, "source": SOURCE, "label": label}
    if found_by:
        out["found_by"] = found_by
    if confidence:
        out["confidence"] = confidence
    out.update(extra)
    return out


def build_prefill(con, fields: dict, class_code: str = "") -> dict:
    """
    Сопоставление полей договора с ключами формы /analytics/risk/fields. Только то, что взято
    из документа однозначно; у каждого значения source = «договор». Возвращает (prefill, notes).
    """
    must, opt, notes = {}, {}, []

    def f(key):
        return fields.get(key) or {}

    # деньги: только сумы (или валюта не указана — в договорах РУз это сумы)
    for key, label, alt in (("sum_insured", "Страховая сумма", ()),
                            ("object_value", "Стоимость объекта", ("market_value", "appraised_value", "book_value"))):
        src = f(key)
        num, cur = src.get("value_num"), src.get("currency")
        if num is None:
            for a in alt:
                if f(a).get("value"):
                    mv = ingest.money_value(str(f(a)["value"]))
                    if mv:
                        src, num, cur = f(a), mv["value"], mv["currency"]
                        break
        if num is None:
            continue
        if cur not in (None, "UZS"):
            notes.append(f"{label} в договоре указана в {cur} — пересчитайте в сумы и введите вручную")
            continue
        if not (0 < num < 1e16):
            notes.append(f"{label}: значение {num:,.0f} вне разумных границ — не подставлено".replace(",", " "))
            continue
        must[key] = _pf(round(num, 2), label, src.get("found_by"), src.get("confidence"),
                        currency="UZS" if cur else None)
    if "sum_insured" in must and "object_value" in must \
            and must["sum_insured"]["value"] > must["object_value"]["value"]:
        notes.append("Страховая сумма в договоре больше стоимости объекта — проверьте (ГК РУз, ст. 936)")

    # регион: из адреса объекта или графы «регион» — тем же разбором, что у кадастра
    addr = f("address").get("value") or f("region").get("value")
    if addr:
        name = D.cast("region", str(addr))
        key, rname = mp.resolve_region(name) if name else (None, None)
        if key:
            must["region"] = _pf(key, "Регион", f("address").get("found_by") or f("region").get("found_by"),
                                 f("address").get("confidence") or f("region").get("confidence"),
                                 name=rname)
        else:
            notes.append("Адрес объекта в договоре есть, но регион по нему не определён — выберите вручную")

    # тип объекта, конструкция, деятельность, транспорт — только однозначные совпадения слов
    obj_text = " ".join(str(f(k).get("value") or "") for k in ("object_kind", "activity", "activity_type"))
    ref = db.load_reference(con)
    classes = ra.parse_classes(class_code) if class_code else []
    types = {ot for (c, ot) in ref.base_rates if not classes or c in classes}
    ot = _one(obj_text, OBJECT_TYPE_WORDS)
    if ot and ot in types:
        must["object_type"] = _pf(ot, "Тип объекта", f("object_kind").get("found_by"))
    cons = _one(str(f("walls").get("value") or ""), CONSTRUCTION_WORDS)
    if cons:
        must["construction"] = _pf(cons, "Конструкция", f("walls").get("found_by"))
    act = _one(obj_text, ACTIVITY_WORDS)
    if act:
        must["activity"] = _pf(act, "Деятельность на объекте",
                               f("activity").get("found_by") or f("object_kind").get("found_by"))
    veh = _one(" ".join(str(f(k).get("value") or "") for k in ("object_kind", "vehicle_type", "vehicle_category")),
               VEHICLE_WORDS)
    if veh:
        must["vehicle_type"] = _pf(veh, "Тип транспорта")
    year = f("year").get("value") or f("year_built").get("value") or f("year_of_manufacture").get("value")
    y = D.cast("year", str(year)) if year else None
    if y:
        must["year"] = _pf(int(y), "Год выпуска или постройки", f("year").get("found_by"))

    # необязательные: площадь и этажность — если их нашёл разбор (кадастр, техпаспорт)
    for key, label, alts in (("area_m2", "Площадь", ("total_area", "area", "usable_area")),
                             ("floors", "Этажность", ("floors",))):
        for a in alts:
            v = f(a).get("value")
            num = D.cast("number", str(v)) if v else None
            if num:
                opt[key] = _pf(float(num), label, f(a).get("found_by"))
                break
    return {"must": must, "optional": opt}, notes


def _fields_dict(core_fields: list, terms: list) -> dict:
    """Поля разбора и условия договора одним словарём key → {name, value, …}; договор важнее."""
    out = {}
    for it in core_fields or []:
        out[it["ключ"]] = {"name": it.get("название"), "value": it.get("значение"),
                           "found_by": it.get("найдено_по"), "confidence": it.get("уверенность"),
                           "method": it.get("метод"), "source": SOURCE}
    for it in terms or []:
        row = {"name": it["название"], "value": it["значение"], "found_by": it.get("найдено_по"),
               "confidence": it.get("уверенность"), "method": it.get("метод"), "source": SOURCE}
        if "value_num" in it:
            row.update({"value_num": it["value_num"], "currency": it.get("currency")})
        out[it["ключ"]] = row
    return out


INSURANCE_MARKERS = ("договор страхования", "страховой полис", "страховая сумма", "страхователь",
                     "sug'urta shartnomasi", "sug'urta polisi", "sug'urta summasi",
                     "суғурта шартномаси", "суғурта суммаси", "insurance contract", "sum insured")


def _is_insurance_contract(text: str) -> bool:
    folded = D.fold(text or "")
    return any(D.fold(m) in folded for m in INSURANCE_MARKERS)


def _public(row: dict) -> dict:
    data = json.loads(row["fields_json"] or "{}")
    return {"doc_id": row["id"], "filename": row["filename"], "size": row["size"], "mime": row["mime"],
            "kind": row["kind"], "language": row["language"], "status": row["status"],
            "insurance_contract": data.get("insurance_contract", False),
            "fields": data.get("fields") or {}, "facts": data.get("facts") or {},
            "prefill": data.get("prefill") or {"must": {}, "optional": {}},
            "notes": data.get("notes") or [], "created_at": row["created_at"],
            "expires_at": row["expires_at"]}


def own_docs(con, user: dict, doc_ids: List[str]) -> List[dict]:
    """Строки документов владельца. Чужой, несуществующий или просроченный id — 404 (одинаково)."""
    ensure_table(con)
    now = _expired_before(datetime.now())
    out = []
    for doc_id in doc_ids:
        if not isinstance(doc_id, str) or not re.fullmatch(r"[0-9a-f]{24}", doc_id):
            raise HTTPException(404, NOT_FOUND)
        r = db.rows(con, "SELECT * FROM analysis_docs WHERE id=? AND user_id=? AND expires_at > ?",
                    doc_id, user["id"], now)
        if not r:
            raise HTTPException(404, NOT_FOUND)
        out.append(r[0])
    return out


@router.post("/analytics/risk/document")
async def upload_document(file: UploadFile = File(...), class_code: str = "",
                          user: dict = Depends(auth.current_user)) -> dict:
    """Один файл до 15 МБ: PDF или DOCX. Запрос не создаётся; через 24 часа файл удаляется."""
    blob = await file.read(MAX_BYTES + 1)
    if not blob:
        raise HTTPException(400, "Файл пустой")
    if len(blob) > MAX_BYTES:
        raise HTTPException(413, "Файл больше 15 МБ — сожмите PDF или уберите из него фотографии")
    fmt = _fmt_of(blob, file.filename)
    if fmt in OLD_HINT:
        raise HTTPException(415, OLD_HINT[fmt])
    if fmt not in MIME:
        raise HTTPException(415, ONLY_PDF_DOCX + " Если договор в .doc — сохраните как .docx.")

    with db.tx() as con:
        cleanup(con)                          # просроченное убираем при каждой новой загрузке
    doc_id = secrets.token_hex(12)
    folder = DIR / doc_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("document." + fmt)
    path.write_bytes(blob)

    notes = []
    try:
        core = ingest.parse_path(path, MIME[fmt], file.filename)
    except Exception as e:                    # разбор упал — файл не держим, ошибку в журнал
        shutil.rmtree(folder, ignore_errors=True)
        with db.tx() as con:
            db.audit(con, user["login"], "ошибка разбора договора для аналитики", "analysis_docs",
                     {"error": type(e).__name__, "format": fmt})
        raise HTTPException(422, "Файл прочитать не удалось: %s" % type(e).__name__)
    text, tables, status = core["read"]["text"], core["read"]["tables"], core["status"]
    readable = status not in (ingest.ST_OCR, ingest.ST_UNSUPPORTED, ingest.ST_ERROR)
    terms = ingest.extract_contract_terms(text, tables) if readable else []
    fields = _fields_dict(core["fields"], terms) if readable else {}
    facts = {f["ключ"]: f["значение"] for f in ingest._mask_deep(core["facts"])}
    insurance = readable and _is_insurance_contract(text)
    # ядро ingest судит о полноте по своим полям; условия договора — тоже поля этого разбора
    if readable and fields and core["kind"]["kind"] != ingest.KIND_OTHER:
        status = ingest.ST_OK
    if core["read"].get("note"):
        notes.append(core["read"]["note"])
    with db.tx() as con:
        ensure_table(con)
        if readable:
            prefill, more = build_prefill(con, fields, class_code[:20])
            notes += more
            if not prefill["must"]:
                notes.append("Из договора не удалось уверенно взять ни одного значения формы — введите вручную")
        else:
            prefill = {"must": {}, "optional": {}}
            if status == ingest.ST_OCR:
                notes.append("Скан без текстового слоя: поля не заполнены, введите суммы вручную")
        if readable and not insurance:
            notes.append("Документ не похож на договор страхования — проверьте, тот ли файл загружен")
        if core["ai"].get("ok") is False and core["ai"].get("reason") not in (None, "не запрашивалось"):
            notes.append("ИИ для дораскрытия полей: " + str(core["ai"]["reason"]))
        now = datetime.now()
        shown = ingest._mask_str(Path(file.filename or "").name)[:200] or ("document." + fmt)
        data = {"fields": fields, "facts": facts, "prefill": prefill, "notes": notes,
                "insurance_contract": bool(insurance), "format": fmt}
        con.execute("INSERT INTO analysis_docs (id, user_id, filename, mime, size, kind, language, status,"
                    " fields_json, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (doc_id, user["id"], shown, MIME[fmt], len(blob), core["kind"]["kind"],
                     core["lang"]["language"], status, json.dumps(data, ensure_ascii=False),
                     now.isoformat(timespec="seconds"),
                     (now + timedelta(hours=TTL_HOURS)).isoformat(timespec="seconds")))
        db.audit(con, user["login"], "договор для аналитики разобран", f"analysis_doc:{doc_id}",
                 {"вид": core["kind"]["kind"], "язык": core["lang"]["language"], "статус": status,
                  "полей": len(fields), "подставлено": sorted(prefill["must"])})
        row = db.rows(con, "SELECT * FROM analysis_docs WHERE id=?", doc_id)[0]
    return _public(row)


@router.get("/analytics/risk/document/{doc_id}")
def get_document(doc_id: str, user: dict = Depends(auth.current_user)) -> dict:
    with db.tx() as con:
        return _public(own_docs(con, user, [doc_id])[0])


@router.delete("/analytics/risk/document/{doc_id}")
def delete_document(doc_id: str, user: dict = Depends(auth.current_user)) -> dict:
    with db.tx() as con:
        own_docs(con, user, [doc_id])
        con.execute("DELETE FROM analysis_docs WHERE id=?", (doc_id,))
        _remove_dir(doc_id)
        db.audit(con, user["login"], "договор для аналитики удалён", f"analysis_doc:{doc_id}", None)
    return {"ok": True, "doc_id": doc_id}


# --------------------------------------------------------------------------- #
# Шаг 5: блок documents в ответе POST /analytics/risk
# --------------------------------------------------------------------------- #

def documents_block(con, user: Optional[dict], doc_ids: List[str], product_code: str,
                    class_code: str, object_type: str) -> dict:
    """
    Что из нужных документов получено. Вид документа закрывает пункт чек-листа по той же таблице,
    что и у документов запроса (docparse.CHECKLIST_MATCH). Договор страхования пунктов не закрывает:
    это предмет анализа, а не подтверждающий документ.
    """
    try:
        need = required_docs(con, product_code or "", class_code or "", object_type or "")
    except HTTPException as e:             # класс или продукт неверны — об этом скажет сам анализ
        need = {"required": [], "basis": None, "basis_text": str(e.detail)}
    names = [d["doc_name"] for d in need["required"]]
    rows = own_docs(con, user, doc_ids) if doc_ids else []
    items, closed = [], set()
    by_norm = {D.norm(n): n for n in names}
    for r in rows:
        data = json.loads(r["fields_json"] or "{}")
        closes = []
        if r["status"] in (ingest.ST_OK, ingest.ST_PARTIAL) and not data.get("insurance_contract"):
            for want in D.CHECKLIST_MATCH.get(r["kind"], []):
                n = by_norm.get(D.norm(want))
                if n and n not in closed:
                    closed.add(n)
                    closes.append(n)
        items.append({"doc_id": r["id"], "kind": r["kind"], "status": r["status"],
                      "insurance_contract": bool(data.get("insurance_contract")), "closes": closes})
    missing = [n for n in names if n not in closed]
    pct = round(len(closed) / len(names) * 100) if names else 100
    return {"received": [i["kind"] for i in items], "items": items, "missing": missing,
            "required_total": len(names), "closed": len(closed), "completeness_docs_pct": pct,
            "basis": need["basis"], "basis_text": need["basis_text"],
            "note": ("Договор страхования — предмет анализа: пунктов чек-листа он не закрывает"
                     if any(i["insurance_contract"] for i in items) else None)}
