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
from . import auth, db, guest, ingest, llm
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
    all_classes = db.rows(con, f"SELECT code, name FROM classes WHERE {db.CLASSES_SHOWN} ORDER BY {db.CLASSES_ORDER}")
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
def risk_fields(request: Request, class_code: str = "", mode: str = "full", product_code: str = "") -> dict:
    """
    Поля must/optional для класса («8», «9», «8/9», «3»), регионы, классы и продукты.
    mode=quick — в must остаются только четыре поля (класс/продукт, сумма, стоимость, регион),
    остальное уходит в will_assume: что подставим и почему.
    """
    classes = ra.parse_classes(class_code)
    quick = str(mode or "").lower() == "quick"
    user = _user_opt(request) if quick else None
    with db.tx() as con:
        cat = _catalog(con, classes)
        regions = mp.regions(con)
        plan = ra.quick_plan(con, class_code, product_code, user) if quick else []
    known = {c["code"] for c in cat["classes"]}
    unknown = [c for c in classes if c not in known]
    out = {"class_code": "/".join(classes), "classes_parsed": classes,
           "unknown_classes": unknown, "group": ra._group(classes) if classes else None,
           **ra.fields_for(class_code), "regions": regions, **cat,
           "mode": "quick" if quick else "full",
           "default_product_special": ra.DEFAULT_PRODUCT_SPECIAL}
    if quick:
        assumed = {a["key"] for a in plan}
        quick_must = [f for f in out["must"] if f["key"] in ra.QUICK_MUST]
        out["must_full"] = out["must"]
        out["must"] = quick_must
        out["quick_must_keys"] = list(ra.QUICK_MUST)
        out["will_assume"] = plan
        out["hidden_keys"] = [f["key"] for f in out["must_full"] if f["key"] not in ra.QUICK_MUST]
        out["note"] = ("Быстрый режим: заполните класс (или продукт), страховую сумму, стоимость и регион. "
                       "Остальное подставим по умолчанию — значения консервативные, чтобы не занизить риск; "
                       "каждое помечено «" + ra.ASSUMED_NOTE + "». Уточнение любого поля повышает точность.")
        out["assumed_keys"] = sorted(assumed)
        if user and not any(a["key"] == "region" for a in plan):
            out["region_from_profile"] = ra.region_from_branch(str(user.get("branch") or ""))
    return out


@router.get("/analytics/risk/presets")
def risk_presets() -> dict:
    """Типовые объекты одним нажатием: значения готовы, человек вводит только суммы и регион."""
    with db.tx() as con:
        items = ra.presets(con)
    return {"presets": items,
            "i18n_keys": [p["i18n_key"] for p in items] + ["preset.hint"],
            "languages": ["ru", "uz", "en"],
            "note": "Тексты ru готовы; uz и en подставляются по ключам i18n словарями интерфейса."}


@router.get("/analytics/risk/last")
def get_last(request: Request) -> dict:
    """Последний выбор этого пользователя (класс, продукт, тип объекта, регион…). Без сумм и ПД.
    Гостю (22.09.2026) ничего не запоминаем: ответ пустой, форма открывается с нуля."""
    user = _user_opt(request)
    if not user:
        return {"ok": True, "guest": True, "last": {}, "allowed_keys": list(ra.LAST_ALLOWED)}
    with db.tx() as con:
        data = ra.load_last(con, user["id"])
    return {"ok": True, **data, "allowed_keys": list(ra.LAST_ALLOWED)}


@router.put("/analytics/risk/last")
def put_last(request: Request, body: dict = Body(...)) -> dict:
    """Запоминает выбор формы. Пишутся только справочные ключи из LAST_ALLOWED, ПД не сохраняются."""
    if not isinstance(body, dict) or len(body) > BODY_MAX_KEYS:
        raise HTTPException(422, "Ожидается набор полей формы")
    user = _user_opt(request)
    if not user:
        return {"ok": True, "guest": True, "saved": [],
                "note": "Без входа выбор формы не сохраняется"}
    with db.tx() as con:
        try:
            data = ra.save_last(con, user["id"], body)
        except ValueError as e:
            raise HTTPException(422, f"Не сохранено: {e}")
    return {"ok": True, **data}


class RiskIn(BaseModel):
    must: dict = {}
    optional: dict = {}
    doc_ids: List[str] = []          # договор и документы из POST /analytics/risk/document (задача 150)
    mode: str = "full"               # "quick" — достроить обязательные поля по умолчанию (assumptions)


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
    user = _user_opt(request)
    # владелец файлов: вошедший или гость по анонимному guest_id из cookie (22.09.2026)
    owner = guest.owner_of(request, user)
    if body.doc_ids and not owner:
        raise HTTPException(404, adocs.NOT_FOUND)
    quick = str(body.mode or "").lower() == "quick"
    must, optional, assumptions = dict(body.must), dict(body.optional), []
    if quick:
        v = ra.validate_quick(must, optional)
        if not v["ok"]:
            return _invalid(v, "Быстрый режим: нужны класс (или продукт), страховая сумма, "
                               "стоимость объекта и регион.")
        with db.tx() as con:
            must, optional, assumptions = ra.apply_defaults(con, must, optional, user=user)
    v = ra.validate(must, optional)
    if not v["ok"]:
        return _invalid(v, "Анализ не запущен: заполните обязательные поля и исправьте ошибки.")
    with db.tx() as con:
        # документы проверяем до анализа: чужой или просроченный id — 404, анализ не запускается
        docs = adocs.documents_block(con, owner, list(dict.fromkeys(body.doc_ids)),
                                     str(must.get("product_code") or ""), str(must.get("class_code") or ""),
                                     str(must.get("object_type") or ""))
        # флаг «документы загружены» в полноте данных ставит сервер, если распознан хоть один
        if docs["items"] and not ra._present(optional.get("documents")) and any(
                i["status"] in (ingest.ST_OK, ingest.ST_PARTIAL) for i in docs["items"]):
            optional["documents"] = True
        res = ra.analyze(con, must, optional, assumptions=assumptions)
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
    res["mode"] = "quick" if quick else "full"
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
def get_thresholds(request: Request) -> dict:
    user = _user_opt(request) or {}
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
