"""
Вход по сети для вкладки «Аналитика» мини-аппа (задача 144, этап 2).

    GET  /analytics/risk/fields?class_code=  — поля формы для класса, регионы, классы, продукты, типы объектов
    POST /analytics/risk                     — анализ риска + рынок + документы (doc_ids) + разбор ИИ; не сохраняет
    GET  /analytics/risk/thresholds          — действующие пороги уровня риска (любой вошедший)
    PUT  /analytics/risk/thresholds          — правка порогов (только админ: guard + require)

Считает не роутер: app/risk_analytics.py (модель) и app/market_picture.py (рынок, stat.uz).
Доступ: guard.ANY_ROLE_EXACT открывает эти пути любой подтверждённой роли, PUT — ADMIN_METHOD_PATH.
"""
from typing import List

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import analysis_docs as adocs
from . import auth, db, ingest, llm
from . import market_picture as mp
from . import risk_analytics as ra

router = APIRouter()

ADMIN = "админ"
LABELS = {f["key"]: f["label"] for f in ra.FIELDS["must"] + ra.FIELDS["optional"]}
BODY_MAX_KEYS = 60                     # недоверенный ввод: форма не бывает больше


def _group_of(code: str) -> str:
    return ra._group([code])


def _catalog(con, classes: list) -> dict:
    """Классы, продукты и типы объектов для выпадающих списков."""
    all_classes = db.rows(con, "SELECT code, name FROM classes ORDER BY CAST(code AS INTEGER), code")
    prods = db.rows(con, "SELECT p.code, p.name, pc.class_code FROM products p "
                         "JOIN product_classes pc ON pc.product_code = p.code "
                         "WHERE p.status <> 'тест' ORDER BY p.code, pc.part_no")
    by_code = {}
    for r in prods:
        by_code.setdefault(r["code"], {"code": r["code"], "name": r["name"], "classes": []})["classes"].append(
            r["class_code"])
    products = [p for p in by_code.values() if not classes or any(c in p["classes"] for c in classes)]
    ref = db.load_reference(con)
    types = sorted({ot for (c, ot) in ref.base_rates if not classes or c in classes})
    return {
        "classes": [{"code": c["code"], "name": c["name"], "group": _group_of(c["code"]),
                     "default_product": ra.DEFAULT_PRODUCT.get(c["code"])} for c in all_classes],
        "products": products,
        "object_types": types,
    }


@router.get("/analytics/risk/fields")
def risk_fields(class_code: str = "") -> dict:
    """Поля must/optional для класса («8», «9», «8/9», «3»), регионы, классы и продукты."""
    classes = ra.parse_classes(class_code)
    with db.tx() as con:
        cat = _catalog(con, classes)
        regions = mp.regions(con)
    known = {c["code"] for c in cat["classes"]}
    unknown = [c for c in classes if c not in known]
    return {"class_code": "/".join(classes), "classes_parsed": classes,
            "unknown_classes": unknown, "group": ra._group(classes) if classes else None,
            **ra.fields_for(class_code), "regions": regions, **cat,
            "default_product_special": ra.DEFAULT_PRODUCT_SPECIAL}


class RiskIn(BaseModel):
    must: dict = {}
    optional: dict = {}
    doc_ids: List[str] = []          # договор и документы из POST /analytics/risk/document (задача 150)


def _user_opt(request: Request):
    """Вошедший, если он есть: единый вход кладёт его в scope, иначе — по сессии."""
    u = request.scope.get("surveyor_user")
    if u is None:
        token = auth.request_token(request)
        if token:
            with db.tx() as con:
                u = auth.session_user(con, token)
    return u


def _invalid(v: dict, message: str) -> JSONResponse:
    """422: что не заполнено и что неверно — по-русски, с подписями полей для подсветки на фронте."""
    missing = list(v.get("missing") or [])
    errors = dict(v.get("errors") or {})
    return JSONResponse(status_code=422, content={
        "ok": False, "detail": message, "missing": missing, "errors": errors,
        "labels": {k: LABELS.get(k, k) for k in missing + list(errors)},
        "missing_text": ["Не заполнено: " + LABELS.get(k, k) for k in missing],
        "errors_text": [f"{LABELS.get(k, k)}: {e}" for k, e in errors.items()]})


@router.post("/analytics/risk")
def risk_analyze(body: RiskIn, request: Request):
    """
    Анализ риска объекта (ra.analyze) + блок market (mp.picture) + блок documents (что из нужных
    документов получено) + ai_summary (разбор ИИ, если он подключён) одним ответом. В базу не пишет.
    """
    if len(body.must) > BODY_MAX_KEYS or len(body.optional) > BODY_MAX_KEYS:
        raise HTTPException(422, "Слишком много полей в запросе")
    if len(body.doc_ids) > adocs.MAX_DOC_IDS:
        raise HTTPException(422, f"Документов в одном анализе — не больше {adocs.MAX_DOC_IDS}")
    user = None
    if body.doc_ids:
        user = _user_opt(request)
        if not user:
            raise HTTPException(401, "Нужно войти в систему, чтобы использовать загруженные документы")
    v = ra.validate(body.must, body.optional)
    if not v["ok"]:
        return _invalid(v, "Анализ не запущен: заполните обязательные поля и исправьте ошибки.")
    must, optional = dict(body.must), dict(body.optional)
    with db.tx() as con:
        # документы проверяем до анализа: чужой или просроченный id — 404, анализ не запускается
        docs = adocs.documents_block(con, user, list(dict.fromkeys(body.doc_ids)),
                                     str(must.get("product_code") or ""), str(must.get("class_code") or ""),
                                     str(must.get("object_type") or ""))
        # флаг «документы загружены» в полноте данных ставит сервер, если распознан хоть один
        if docs["items"] and not ra._present(optional.get("documents")) and any(
                i["status"] in (ingest.ST_OK, ingest.ST_PARTIAL) for i in docs["items"]):
            optional["documents"] = True
        res = ra.analyze(con, must, optional)
        if not res.get("ok"):
            return _invalid(res.get("validation") or {}, res.get("message") or "Анализ не запущен")
        s = res["summary"]
        classes = ra.parse_classes(s["class_code"])
        # рынок НАПП даёт 8 и 9 одной строкой; у договора из нескольких классов берём первый
        cls = classes[0]
        product = s.get("product_code") or next(
            (p["product_code"] for p in s["parts"] if p["class_code"] == cls), None)
        pic = mp.picture(con, cls, product, s.get("region"), our_rate_pct=s["rate_applied_pct"])
    if len(classes) > 1:
        pic["notes"] = pic["notes"] + [f"Договор из классов {', '.join(classes)}: рынок показан по классу {cls}, "
                                       f"наша ставка — по всему договору"]
    res["market_compare"] = res.pop("market")     # сравнение, по которому считался балл уровня риска
    res["market"] = pic
    res["documents"] = docs
    comp = res["completeness"]
    comp["docs_pct"] = docs["completeness_docs_pct"]
    comp["docs_missing"] = len(docs["missing"])
    if docs["missing"] and not any(w.get("key") == "documents" for w in comp.get("what_to_add") or []):
        comp.setdefault("what_to_add", []).append(
            {"key": "documents", "why": f"не хватает документов по чек-листу: {len(docs['missing'])}"})
    ai = llm.risk_summary(res)
    res["ai_summary"], res["ai_status"], res["ai_source"] = ai["text"], ai["status"], ai["source"]
    return res


# ---------- пороги уровня риска ----------

def _history(con, n: int = 10) -> list:
    try:
        return db.rows(con, "SELECT id, created_at, created_by, thresholds_json, note FROM risk_thresholds "
                            "ORDER BY id DESC LIMIT ?", n)
    except Exception:                  # таблицы ещё нет — правок не было
        return []


@router.get("/analytics/risk/thresholds")
def get_thresholds(user: dict = Depends(auth.current_user)) -> dict:
    with db.tx() as con:
        th = ra.load_thresholds(con)
        hist = _history(con)
    return {"thresholds": th, "defaults": ra.DEFAULT_THRESHOLDS, "level_names": ra.LEVEL_NAMES,
            "can_edit": user.get("role") == ADMIN, "history": hist}


@router.put("/analytics/risk/thresholds")
def put_thresholds(body: dict = Body(...), user: dict = Depends(auth.require(ADMIN))) -> dict:
    """
    Тело — изменяемые ключи порогов (например {"level_bounds": [25, 45, 65, 85]}); остальное остаётся
    как было. {"reset": true} — вернуть значения по умолчанию. Старые версии остаются в истории.
    """
    if not isinstance(body, dict):
        raise HTTPException(422, "Ожидается набор порогов")
    with db.tx() as con:
        if body.get("reset") is True and len(body) == 1:
            patched = {}
        else:
            cur = ra.load_thresholds(con)
            cur.pop("_source", None)
            patched = ra._merge(cur, {k: v for k, v in body.items() if k != "_source"})
        try:
            th = ra.save_thresholds(con, patched, user["login"])
        except ValueError as e:
            raise HTTPException(422, f"Пороги не сохранены: {e}")
    return {"ok": True, "thresholds": th}
