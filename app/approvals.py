"""
Коллективное согласование запросов: один запрос рассматривают 2–3 человека.

Зачем: по генеральному соглашению (например, с Asia Alliance Bank) запрос уходит не одному
андеррайтеру, а сразу нескольким людям, которых выбирает сам пользователь. Компании нужно
видеть, кто что решил — поэтому все решения попадают в общую ленту и в ежедневный доклад.

Права (обхода входа в модуле нет, тесты заводят настоящие сессии):
    решение              — вошедший пользователь, назначенный согласующим по этому запросу;
    состав согласующих   — вошедший работник компании; агент — только по своему запросу;
    генеральные соглашения — админ.

Итоговый статус запроса (requests.approval_status):
    все 'одобрил'            -> 'согласован'
    хотя бы один 'отклонил'  -> 'отклонён'
    иначе                    -> 'на согласовании'
"""
import json
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import db
from .auth import current_user, require

ROOT = Path(__file__).resolve().parent.parent
router = APIRouter()

MIN_REVIEWERS, MAX_REVIEWERS = 2, 3
DECISIONS = ("одобрил", "отклонил", "вопрос")
WAITING = "ожидает"

NOT_REQUIRED, IN_REVIEW, APPROVED, REJECTED = "не требуется", "на согласовании", "согласован", "отклонён"


# ---------- модели запросов ----------

class ReviewersIn(BaseModel):
    user_ids: List[int]                     # порядок списка = порядок рассмотрения
    general_agreement_id: Optional[int] = None
    # поле who больше не принимается: кто назначил — берём из сессии


class DecisionIn(BaseModel):
    decision: str
    comment: Optional[str] = None
    who: Optional[str] = None               # только сверка: должен совпасть с вошедшим, иначе 403


class AgreementIn(BaseModel):
    partner: str
    product_code: Optional[str] = None
    terms: Optional[str] = None
    default_reviewers: Optional[list] = None
    status: Optional[str] = "черновик"
    note: Optional[str] = None


# ---------- логика ----------

def recalc(con, request_id: int) -> str:
    """Пересчитывает итоговый статус согласования запроса и сохраняет его."""
    rows = db.rows(con, "SELECT status FROM request_reviewers WHERE request_id=?", request_id)
    if not rows:
        status = NOT_REQUIRED
    elif any(r["status"] == "отклонил" for r in rows):
        status = REJECTED
    elif all(r["status"] == "одобрил" for r in rows):
        status = APPROVED
    else:
        status = IN_REVIEW
    con.execute("UPDATE requests SET approval_status=? WHERE id=?", (status, request_id))
    return status


def _request(con, request_id: int) -> dict:
    r = db.rows(con, "SELECT * FROM requests WHERE id=?", request_id)
    if not r:
        raise HTTPException(404, "Запрос не найден")
    return r[0]


def _user_by_who(con, who: str) -> Optional[dict]:
    """Согласующий может прийти логином, id или ФИО — принимаем все три варианта."""
    who = (who or "").strip()
    if not who:
        return None
    if who.isdigit():
        r = db.rows(con, "SELECT * FROM users WHERE id=?", int(who))
        if r:
            return r[0]
    r = db.rows(con, "SELECT * FROM users WHERE login=?", who)
    if r:
        return r[0]
    r = db.rows(con, "SELECT * FROM users WHERE full_name=?", who)
    return r[0] if r else None


def default_reviewers(con, agreement_id: int) -> list:
    """Согласующие по умолчанию из генерального соглашения (user_id или ФИО)."""
    a = db.rows(con, "SELECT * FROM general_agreements WHERE id=?", agreement_id)
    if not a:
        raise HTTPException(404, "Генеральное соглашение не найдено")
    try:
        lst = json.loads(a[0]["default_reviewers"] or "[]")
    except ValueError:
        lst = []
    out = []
    for item in lst if isinstance(lst, list) else []:
        u = _user_by_who(con, str(item))
        if u:
            out.append(u["id"])
    return out


def initiator_user_ids(con, request_id: int) -> set:
    """Кто подал запрос: в requests хранится только agent_id, сверяем с людьми по ID агента в ЕАИС."""
    r = _request(con, request_id)
    aid = r.get("agent_id")
    if not aid:
        return set()
    a = db.rows(con, "SELECT eais_id FROM agents WHERE id=?", aid)
    if not a or not a[0]["eais_id"]:
        return set()
    return {u["id"] for u in db.rows(con, "SELECT id FROM users WHERE agent_eais_id=?", a[0]["eais_id"])}


def assign(con, request_id: int, user_ids: List[int], who: Optional[str] = None,
           agreement_id: Optional[int] = None) -> dict:
    """Назначает состав согласующих. Повторный вызов заменяет состав (решения снимаются)."""
    _request(con, request_id)
    ids, seen = [], set()
    for uid in user_ids or []:
        if not isinstance(uid, int) or uid in seen:
            continue                        # дубли и мусор молча отбрасываем — ниже проверим количество
        seen.add(uid)
        ids.append(uid)
    if not (MIN_REVIEWERS <= len(ids) <= MAX_REVIEWERS):
        raise HTTPException(400, f"Согласующих должно быть от {MIN_REVIEWERS} до {MAX_REVIEWERS}, передано {len(ids)}")

    users = {}
    for uid in ids:
        u = db.rows(con, "SELECT * FROM users WHERE id=?", uid)
        if not u:
            raise HTTPException(400, f"Пользователь {uid} не найден")
        if u[0]["status"] != "активен":
            # согласующий — действующий работник компании: сведения о страхователе открыты только им (ЗРУ-730, ст. 62)
            raise HTTPException(400, f"Пользователь {u[0]['full_name']} не активен ({u[0]['status']})")
        if u[0]["role"] == "агент":
            raise HTTPException(400, f"{u[0]['full_name']} — страховой агент: он посредник, а не работник компании, "
                                     f"и согласующим быть не может (Положение № 3845, п. 2)")
        users[uid] = u[0]

    initiators = initiator_user_ids(con, request_id)
    same = [users[uid]["full_name"] for uid in ids if uid in initiators]
    if same:
        raise HTTPException(400, "Инициатор запроса не может согласовывать сам себя: " + ", ".join(same))

    old = db.rows(con, "SELECT user_id, status FROM request_reviewers WHERE request_id=?", request_id)
    con.execute("DELETE FROM request_reviewers WHERE request_id=?", (request_id,))
    ts = db.now()
    for n, uid in enumerate(ids, 1):
        u = users[uid]
        con.execute("INSERT INTO request_reviewers (request_id, user_id, full_name, position, order_no,"
                    " status, comment, assigned_at, assigned_by, decided_at) VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                    (request_id, uid, u["full_name"], u["role"], n, WAITING, None, ts, who))
    if agreement_id is not None:
        con.execute("UPDATE requests SET general_agreement_id=? WHERE id=?", (agreement_id, request_id))
    status = recalc(con, request_id)
    db.audit(con, who or "system", "назначены согласующие", f"request:{request_id}",
             {"было": [o["user_id"] for o in old], "стало": ids, "генсоглашение": agreement_id})
    return {"request_id": request_id, "approval_status": status, "reviewers": reviewers(con, request_id)}


def reviewers(con, request_id: int) -> list:
    return db.rows(con, "SELECT * FROM request_reviewers WHERE request_id=? ORDER BY order_no, id", request_id)


def decide(con, request_id: int, who: str, decision: str, comment: Optional[str] = None) -> dict:
    if decision not in DECISIONS:
        raise HTTPException(400, "Решение должно быть одно из: " + ", ".join(DECISIONS))
    _request(con, request_id)
    u = _user_by_who(con, who)
    if not u:
        raise HTTPException(403, "Пользователь не найден — решение принимать некому")
    row = db.rows(con, "SELECT * FROM request_reviewers WHERE request_id=? AND user_id=?", request_id, u["id"])
    if not row:
        raise HTTPException(403, "Вы не назначены согласующим по этому запросу")
    before = row[0]["status"]
    con.execute("UPDATE request_reviewers SET status=?, comment=?, decided_at=? WHERE id=?",
                (decision, comment, db.now(), row[0]["id"]))
    status = recalc(con, request_id)
    db.audit(con, u["login"], "решение по согласованию", f"request:{request_id}",
             {"было": before, "стало": decision, "комментарий": comment, "итог": status})
    # точка расширения: здесь же отправлять уведомление в Telegram остальным согласующим
    return {"request_id": request_id, "approval_status": status,
            "decision": decision, "reviewers": reviewers(con, request_id)}


def feed(con, limit: int = 50) -> list:
    """Лента решений: кто (ФИО, должность), по какому запросу, что решил, когда, комментарий."""
    return db.rows(con, """SELECT rr.request_id, rr.user_id, rr.full_name, rr.position, rr.status AS decision,
                                  rr.comment, rr.decided_at, r.external_no, r.branch, r.product_code,
                                  r.policyholder, r.approval_status, g.partner
                           FROM request_reviewers rr
                           JOIN requests r ON r.id = rr.request_id
                           LEFT JOIN general_agreements g ON g.id = r.general_agreement_id
                           WHERE rr.decided_at IS NOT NULL
                           ORDER BY rr.decided_at DESC, rr.id DESC LIMIT ?""", limit)


def inbox(con, who: str) -> list:
    u = _user_by_who(con, who)
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    items = db.rows(con, """SELECT rr.request_id, rr.order_no, rr.assigned_at, r.external_no, r.branch,
                                   r.product_code, r.policyholder, r.approval_status, g.partner
                            FROM request_reviewers rr
                            JOIN requests r ON r.id = rr.request_id
                            LEFT JOIN general_agreements g ON g.id = r.general_agreement_id
                            WHERE rr.user_id=? AND rr.status=?
                            ORDER BY rr.assigned_at""", u["id"], WAITING)
    return items


# ---------- API ----------

@router.post("/requests/{request_id}/reviewers")
def post_reviewers(request_id: int, body: ReviewersIn, user: dict = Depends(current_user)):
    """Состав назначает вошедший: работник компании — по любому запросу, агент — только по своему.

    Состав выбирают прямо при сохранении запроса на экране агента, поэтому право шире «всех запросов»;
    чужой запрос агент тронуть не может (связь агента с запросом — по ID в ЕАИС).
    """
    with db.tx() as con:
        if user["role"] == "агент" and user["id"] not in initiator_user_ids(con, request_id):
            raise HTTPException(403, "Агент может назначать согласующих только по своему запросу")
        ids = body.user_ids
        # состав не передан, но указано генеральное соглашение — берём его список по умолчанию
        if not ids and body.general_agreement_id:
            ids = default_reviewers(con, body.general_agreement_id)
            if not ids:
                raise HTTPException(400, "В генеральном соглашении не заданы согласующие по умолчанию — "
                                         "выберите людей вручную")
        return assign(con, request_id, ids, user["login"], body.general_agreement_id)


@router.post("/requests/{request_id}/decide")
def post_decide(request_id: int, body: DecisionIn, user: dict = Depends(current_user)):
    """Решение записывается только от имени вошедшего: в ленту и в доклад идут настоящие фамилии."""
    with db.tx() as con:
        if body.who:                        # если фамилию всё же прислали — она обязана совпасть с сессией
            named = _user_by_who(con, body.who)
            if not named or named["id"] != user["id"]:
                raise HTTPException(403, "Решение записывается от имени вошедшего пользователя, "
                                         "принять его за другого человека нельзя")
        return decide(con, request_id, user["login"], body.decision, body.comment)


@router.get("/requests/{request_id}/approvals")
def get_approvals(request_id: int):
    with db.tx() as con:
        r = _request(con, request_id)
        return {"request_id": request_id, "approval_status": r.get("approval_status") or NOT_REQUIRED,
                "general_agreement_id": r.get("general_agreement_id"),
                "reviewers": reviewers(con, request_id)}


@router.get("/approvals/inbox")
def get_inbox(user: str):
    with db.tx() as con:
        return {"user": user, "items": inbox(con, user)}


@router.get("/approvals/feed")
def get_feed(limit: int = 50):
    with db.tx() as con:
        pending = db.rows(con, "SELECT COUNT(*) n FROM requests WHERE approval_status=?", IN_REVIEW)
        return {"pending_requests": pending[0]["n"] if pending else 0, "items": feed(con, max(1, min(limit, 500)))}


@router.get("/approvals/pending")
def get_pending():
    """Все запросы на согласовании и сколько человек ещё не ответили (для экрана /approvals)."""
    with db.tx() as con:
        items = db.rows(con, """SELECT r.id, r.external_no, r.branch, r.product_code,
                                       COUNT(rr.id) total,
                                       SUM(CASE WHEN rr.status='ожидает' THEN 1 ELSE 0 END) waiting
                                FROM requests r JOIN request_reviewers rr ON rr.request_id = r.id
                                WHERE r.approval_status='на согласовании'
                                GROUP BY r.id ORDER BY r.id DESC""")
    return {"count": len(items), "items": items}


@router.get("/general-agreements")
def get_agreements():
    with db.tx() as con:
        out = db.rows(con, "SELECT * FROM general_agreements ORDER BY partner, id")
    for a in out:
        try:
            a["default_reviewers"] = json.loads(a["default_reviewers"] or "[]")
        except ValueError:
            a["default_reviewers"] = []
    return out


@router.post("/general-agreements")
def post_agreement(body: AgreementIn, user: dict = Depends(require("админ"))):
    partner = (body.partner or "").strip()
    if not partner:
        raise HTTPException(400, "Не указан партнёр по генеральному соглашению")
    with db.tx() as con:
        exists = db.rows(con, "SELECT id FROM general_agreements WHERE partner=? AND COALESCE(product_code,'')=?",
                         partner, body.product_code or "")
        payload = (partner, body.product_code, body.terms,
                   json.dumps(body.default_reviewers or [], ensure_ascii=False),
                   body.status or "черновик", body.note)
        if exists:                          # идемпотентно: повторная отправка обновляет карточку
            con.execute("UPDATE general_agreements SET partner=?, product_code=?, terms=?, default_reviewers=?,"
                        " status=?, note=? WHERE id=?", payload + (exists[0]["id"],))
            aid = exists[0]["id"]
        else:
            cur = con.execute("INSERT INTO general_agreements (partner, product_code, terms, default_reviewers,"
                              " status, note, created_at) VALUES (?,?,?,?,?,?,?)", payload + (db.now(),))
            aid = cur.lastrowid
        db.audit(con, user["login"], "генеральное соглашение", f"general_agreement:{aid}", {"партнёр": partner})
        return db.rows(con, "SELECT * FROM general_agreements WHERE id=?", aid)[0]


@router.get("/approvals", response_class=HTMLResponse)
def approvals_page():
    return (ROOT / "app" / "approvals.html").read_text(encoding="utf-8")
