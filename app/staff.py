"""
Сотрудник, заведённый администратором вручную (записка заказчика, 02.10.2026).

Обычный путь — человек входит сам через Telegram или Google (app/registration.py, app/google_auth.py).
Этот модуль — для тех, кого удобнее завести из админ-панели: логин и временный пароль выдаёт администратор,
при первом входе человек обязан сменить пароль (users.must_change_password = 1; пока флаг стоит, app/guard.py
пускает сессию только на PUT /auth/password, GET /auth/me и POST /auth/logout).

Маршруты (только администратор — app/guard.py и require):
  POST /tg/users/manual                 — завести сотрудника (активен сразу, роль «сотрудник»)
  PUT  /tg/users/{uid}                  — правка ФИО, должности, департамента, отдела, филиала, телефона
  POST /tg/users/{uid}/reset-password   — новый временный пароль (показывается один раз)

Персональные данные (правило проекта № 8): в журнал audit не пишем ФИО, телефон — только маской
(+998*******67), временный пароль — никогда.
"""
import re
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import auth, db, registration

router = APIRouter()

ADMIN = "админ"
LOGIN_RE = re.compile(r"[a-z0-9_.\-@]{3,40}")
FIELD_MAX = 120
# без похожих символов (0/O, 1/l/I): временный пароль диктуют и переписывают руками
PW_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PW_LEN = 10


def mask_phone(phone: Optional[str]) -> str:
    """+998901234567 → +998*******67: в журнал номер целиком не попадает."""
    p = str(phone or "")
    if len(p) < 6:
        return "***"
    return p[:4] + "*" * (len(p) - 6) + p[-2:]


def temp_password() -> str:
    return "".join(secrets.choice(PW_ALPHABET) for _ in range(PW_LEN))


def _text(raw, what: str, required: bool = False, limit: int = FIELD_MAX) -> str:
    v = " ".join(str(raw or "").split())
    if required and not v:
        raise HTTPException(422, f"Укажите {what}")
    if len(v) > limit:
        raise HTTPException(422, f"Поле «{what}» длиннее {limit} символов — сократите")
    return v


def _login(raw: str) -> str:
    login = (raw or "").strip().lower()
    if not LOGIN_RE.fullmatch(login):
        raise HTTPException(422, "Логин — от 3 до 40 знаков: латинские буквы, цифры, точка, дефис, "
                                 "подчёркивание или @")
    return login


def _password(raw: str) -> str:
    pw = raw or ""
    if len(pw) < auth.PASSWORD_MIN:
        raise HTTPException(422, f"Временный пароль — не короче {auth.PASSWORD_MIN} символов")
    if len(pw) > 128:
        raise HTTPException(422, "Временный пароль слишком длинный")
    return pw


def _phone_free(con, phone: str, except_uid: Optional[int] = None):
    rows = db.rows(con, "SELECT id FROM users WHERE phone=?", phone)
    if any(r["id"] != except_uid for r in rows):
        raise HTTPException(409, "Этот номер телефона уже есть у другого пользователя")


def _card(u: dict) -> dict:
    """Карточка сотрудника для админ-панели (без хэша пароля и соли)."""
    return {"id": u["id"], "login": u["login"], "full_name": u["full_name"], "phone": u.get("phone") or "",
            "position": u.get("position") or "", "department": u.get("department") or "",
            "unit": u.get("unit") or "", "branch": u.get("branch") or "", "role": u["role"],
            "status": u["status"], "must_change_password": bool(u.get("must_change_password")),
            "login_method": registration.login_method(u)}


def _user(con, uid: int) -> dict:
    r = db.rows(con, "SELECT * FROM users WHERE id=?", uid)
    if not r:
        raise HTTPException(404, "Пользователь не найден")
    return r[0]


# --------------------------------------------------------------------------- #
#  Модели
# --------------------------------------------------------------------------- #

class ManualIn(BaseModel):
    full_name: str = ""
    position: str = ""
    department: str = ""
    unit: str = ""                 # отдел
    branch: str = ""
    phone: str = ""
    login: str = ""
    temp_password: str = ""        # пусто — сервер придумает сам и вернёт один раз


class EditIn(BaseModel):
    # None — поле не меняется; пустая строка — очистить (кроме обязательных)
    full_name: Optional[str] = None
    position: Optional[str] = None
    department: Optional[str] = None
    unit: Optional[str] = None
    branch: Optional[str] = None
    phone: Optional[str] = None


# --------------------------------------------------------------------------- #
#  Логика
# --------------------------------------------------------------------------- #

def create_manual(con, admin: dict, data: ManualIn) -> dict:
    full_name = registration.check_full_name(data.full_name)
    position = _text(data.position, "должность", required=True, limit=80)
    department = _text(data.department, "департамент", required=True)
    unit = _text(data.unit, "отдел")
    branch = _text(data.branch, "филиал")
    phone = registration.normalize_phone(data.phone)
    login = _login(data.login)
    generated = not (data.temp_password or "")
    pw = temp_password() if generated else _password(data.temp_password)

    if db.rows(con, "SELECT 1 FROM users WHERE login=?", login):
        raise HTTPException(409, "Такой логин уже занят")
    _phone_free(con, phone)

    pw_hash, salt = auth.hash_password(pw)
    ts = db.now()
    cur = con.execute(
        "INSERT INTO users (login, full_name, phone, role, branch, department, position, unit, agent_eais_id,"
        " password_hash, salt, status, created_at, approved_by, approved_at, must_change_password)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (login, full_name, phone, auth.ROLE_EMPLOYEE, branch, department, position, unit, None,
         pw_hash, salt, auth.STATUS_ACTIVE, ts, admin["login"], ts, 1))
    uid = cur.lastrowid
    db.audit(con, admin["login"], "сотрудник заведён вручную", f"user:{uid}",
             {"login": login, "role": auth.ROLE_EMPLOYEE, "телефон": mask_phone(phone),
              "временный пароль": "задан администратором" if not generated else "создан сервером"})
    out = {"ok": True, "user": _card(_user(con, uid)),
           "message": "Сотрудник заведён. При первом входе он сменит временный пароль"}
    if generated:
        out["temp_password"] = pw          # показывается один раз, в базе только хэш
    return out


EDIT_FIELDS = ("full_name", "position", "department", "unit", "branch", "phone")


def edit_user(con, admin: dict, uid: int, data: EditIn) -> dict:
    u = _user(con, uid)
    new = {}
    if data.full_name is not None:
        new["full_name"] = registration.check_full_name(data.full_name)
    if data.position is not None:
        new["position"] = _text(data.position, "должность", required=True, limit=80)
    if data.department is not None:
        new["department"] = _text(data.department, "департамент", required=True)
    if data.unit is not None:
        new["unit"] = _text(data.unit, "отдел")
    if data.branch is not None:
        new["branch"] = _text(data.branch, "филиал")
    if data.phone is not None:
        new["phone"] = registration.normalize_phone(data.phone)
        _phone_free(con, new["phone"], except_uid=uid)
    changed = [k for k in EDIT_FIELDS if k in new and (u.get(k) or "") != new[k]]
    if not changed:
        return {"ok": True, "changed": [], "user": _card(u)}
    con.execute("UPDATE users SET " + ", ".join(f"{k}=?" for k in changed) + " WHERE id=?",
                tuple(new[k] for k in changed) + (uid,))
    # в журнал — только названия полей; телефон маской, ФИО и прочие значения не пишем
    detail = {"login": u["login"], "поля": changed}
    if "phone" in changed:
        detail["телефон"] = f"{mask_phone(u.get('phone'))} → {mask_phone(new['phone'])}"
    db.audit(con, admin["login"], "карточка сотрудника изменена", f"user:{uid}", detail)
    return {"ok": True, "changed": changed, "user": _card(_user(con, uid))}


def reset_password(con, admin: dict, uid: int) -> dict:
    u = _user(con, uid)
    if u["status"] == auth.STATUS_BLOCKED:
        raise HTTPException(422, "Учётная запись заблокирована — сначала снимите блокировку")
    pw = temp_password()
    pw_hash, salt = auth.hash_password(pw)
    con.execute("UPDATE users SET password_hash=?, salt=?, must_change_password=1 WHERE id=?",
                (pw_hash, salt, uid))
    con.execute("DELETE FROM sessions WHERE user_id=?", (uid,))     # старые входы больше не действуют
    db.audit(con, admin["login"], "сброшен пароль", f"user:{uid}", {"login": u["login"]})
    return {"ok": True, "user_id": uid, "login": u["login"], "temp_password": pw,
            "must_change_password": True,
            "message": "Временный пароль показан один раз. При входе сотрудник сменит его"}


# --------------------------------------------------------------------------- #
#  Маршруты
# --------------------------------------------------------------------------- #

@router.post("/tg/users/manual")
def post_manual(body: ManualIn, admin: dict = Depends(auth.require(ADMIN))):
    with db.tx() as con:
        return create_manual(con, admin, body)


@router.put("/tg/users/{uid}")
def put_user(uid: int, body: EditIn, admin: dict = Depends(auth.require(ADMIN))):
    with db.tx() as con:
        return edit_user(con, admin, uid, body)


@router.post("/tg/users/{uid}/reset-password")
def post_reset(uid: int, admin: dict = Depends(auth.require(ADMIN))):
    with db.tx() as con:
        return reset_password(con, admin, uid)
