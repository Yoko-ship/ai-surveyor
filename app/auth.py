"""
Верификация пользователей и роли. Подключение в main.py:

    from .auth import router as auth_router, current_user, require
    app.include_router(auth_router)

    @app.get("/requests")
    def list_requests(..., user: dict = Depends(require("андеррайтер", "актуарий", "админ"))): ...

Процесс (описание для руководителя — docs/Верификация пользователей.md):
  1. POST /auth/register  — ЗАКРЫТО с 21.09.2026 (ответ 410): заявок на доступ больше нет. Человек входит
                            сам — Telegram (app/tg_link.py, app/telegram.py) или рабочая почта Google
                            (app/google_auth.py) — и после короткой анкеты сразу активен, роль «сотрудник».
  2. GET  /auth/pending    — админ видит оставшиеся старые заявки и сверку с реестром компании (таблица agents).
     POST /auth/approve/{id} | /auth/reject/{id} | /auth/block/{id} — решение админа, всё пишется в audit.
  3. POST /auth/login      — логин + пароль. Если у пользователя есть telegram_id — второй шаг: 6-значный код
                            (5 минут, 5 попыток), POST /auth/verify-code. Отправка кода — заглушка send_code().
  4. Сессия — случайный токен в httpOnly-cookie «sid» на 12 часов, продлевается при каждом обращении.
     POST /auth/logout, GET /auth/me.
  Первый администратор: пока таблица users пуста, заявка с role='админ' сразу активна (bootstrap) — в audit.

Матрица прав (PERMISSIONS): что какая роль может делать. Проверка — require(*roles) или can(user, right).

    право                     агент  андеррайтер  актуарий  админ
    расчёт (/calculate)         +        +           +        +
    свои запросы (создать,      +        +           +        +
      смотреть свой филиал)
    все запросы                 -        +           +        +
    решение по запросу          -        +           -        +
    портфельный аудит           -        +           +        +
    калибровка                  -        -           +        +
    ёмкость и удержание         -        -           +        +
    справочники и тарифы        -        -           -        +
      (админка), пользователи
    журнал (audit)              -        -           -        +

Пароли: PBKDF2-HMAC-SHA256, 200 000 итераций, соль 16 байт на пользователя (hashlib + secrets, без сторонних пакетов).
Логика вынесена в функции с параметром con (register_user, login_user, verify_login_code, approve_user, ...),
маршруты — тонкие обёртки; тесты вызывают функции напрямую (tests/test_auth.py).
"""
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import db, web

ROOT = Path(__file__).resolve().parent.parent
router = APIRouter()

# «сотрудник» — роль по умолчанию при самостоятельной регистрации в мини-приложении
# (задача заказчика от 21.09.2026): расчёт, свои запросы, фото и документы, отправка на согласование.
# Чужих запросов, портфеля, журнала, справочников и копий базы не видит.
ROLES = ("сотрудник", "агент", "андеррайтер", "актуарий", "админ")
ROLE_EMPLOYEE = "сотрудник"
STATUS_PENDING, STATUS_ACTIVE, STATUS_BLOCKED = "ожидает подтверждения", "активен", "заблокирован"

PBKDF2_ITERATIONS = 200_000
SESSION_HOURS = 12
SESSION_EXTEND_EVERY_SEC = 60        # продление сессии пишется в базу не чаще раза в минуту
CODE_MINUTES = 5
CODE_MAX_ATTEMPTS = 5
COOKIE = "sid"

# право -> роли, которым оно дано (админ имеет всё)
PERMISSIONS = {
    "расчёт": {"сотрудник", "агент", "андеррайтер", "актуарий", "админ"},
    "свои запросы": {"сотрудник", "агент", "андеррайтер", "актуарий", "админ"},
    "все запросы": {"андеррайтер", "актуарий", "админ"},
    "решение": {"андеррайтер", "админ"},
    "портфель": {"андеррайтер", "актуарий", "админ"},
    "калибровка": {"актуарий", "админ"},
    "ёмкость": {"актуарий", "админ"},
    "справочники": {"админ"},
    "пользователи": {"админ"},
    "журнал": {"админ"},
}


def dev_mode() -> bool:
    from .config import boolean
    return boolean(os.environ, "SURVEYOR_DEV")


# ---------- пароли ----------

def hash_password(password: str, salt_hex: Optional[str] = None) -> tuple[str, str]:
    """Возвращает (hash_hex, salt_hex). Соль новая, если не передана."""
    salt_hex = salt_hex or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS)
    return digest.hex(), salt_hex


def check_password(password: str, password_hash: str, salt_hex: str) -> bool:
    calc, _ = hash_password(password, salt_hex)
    return hmac.compare_digest(calc, password_hash)


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ---------- модели входа ----------

class RegisterIn(BaseModel):
    full_name: str = Field(min_length=3)
    phone: str = ""
    login: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8)
    role: str = "агент"
    branch: str = ""
    agent_eais_id: str = ""


class LoginIn(BaseModel):
    login: str
    password: str


class CodeIn(BaseModel):
    login: str
    code: str


class PasswordIn(BaseModel):
    old_password: str = ""
    new_password: str = ""


PASSWORD_MIN = 8
# Пока временный пароль не сменён, сессия открывает только эти адреса (app/guard.py)
MUST_CHANGE_ALLOWED = {"/auth/password", "/auth/me", "/auth/logout"}
MUST_CHANGE_DETAIL = "Смените временный пароль: PUT /auth/password"


# ---------- заглушка отправки кода ----------

def send_code(user: dict, code: str) -> Optional[str]:
    """
    Отправка одноразового кода. Бота ещё нет, поэтому ничего не отправляется.
    Здесь подключается Telegram-бот компании: по user['telegram_id'] вызвать
    https://api.telegram.org/bot<TOKEN>/sendMessage с текстом «Код входа в сюрвейер: NNNNNN, действует 5 минут».
    Токен бота — в переменной окружения SURVEYOR_TG_TOKEN (в код и в базу не класть).
    Возвращает описание канала доставки или None, если доставить нечем.
    """
    token = os.environ.get("SURVEYOR_TG_TOKEN")
    if not token:
        return None
    # TODO: urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=...)
    return None


# ---------- логика (без FastAPI-объектов, работает с con) ----------

def _public(u: dict) -> dict:
    return {k: u.get(k) for k in ("id", "login", "full_name", "phone", "role", "branch", "agent_eais_id",
                                  "position", "department", "unit",
                                  "status", "created_at", "approved_by", "approved_at", "last_login")
            } | {"telegram": bool(u.get("telegram_id")),
                 "must_change_password": bool(u.get("must_change_password"))}


def _user_by_login(con, login: str) -> Optional[dict]:
    r = db.rows(con, "SELECT * FROM users WHERE login=?", login.strip().lower())
    return r[0] if r else None


def _user_by_id(con, uid: int) -> dict:
    r = db.rows(con, "SELECT * FROM users WHERE id=?", uid)
    if not r:
        raise HTTPException(404, "Пользователь не найден")
    return r[0]


def user_by_google_sub(con, google_sub: str) -> Optional[dict]:
    """Учётная запись, привязанная к аккаунту Google (app/google_auth.py). sub — вечный
    идентификатор аккаунта у Google: почту человек может сменить, sub — нет."""
    if not google_sub:
        return None
    r = db.rows(con, "SELECT * FROM users WHERE google_sub=?", str(google_sub))
    return r[0] if r else None


def registry_entry(con, eais_id: str) -> Optional[dict]:
    """Запись реестра агентов компании (таблица agents) по ID из ЕАИС — для сверки админом."""
    if not eais_id:
        return None
    r = db.rows(con, "SELECT id, eais_id, name, kind, status, trained_at, monitored_at FROM agents WHERE eais_id=?", eais_id)
    return r[0] if r else None


def register_user(con, data: RegisterIn) -> dict:
    """Заведение учётной записи логином и паролем. С 21.09.2026 снаружи недоступно
    (POST /auth/register отвечает 410): остался один живой вызов — первый администратор
    по коду ADMIN_BOOTSTRAP_CODE (app/guard.py), а он сразу активен. Статус «ожидает
    подтверждения» поэтому больше никому не выдаётся; в коде он остался ради старых записей."""
    login = data.login.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.\-@]+", login):
        raise HTTPException(422, "Логин — латинские буквы, цифры, точка, дефис или подчёркивание")
    if data.role not in ROLES:
        raise HTTPException(422, "Роль должна быть одной из: " + ", ".join(ROLES))
    eais = data.agent_eais_id.strip()
    if data.role == "агент" and not eais:
        raise HTTPException(422, "Для агента обязателен ID из ЕАИС: по Положению № 3845 (п. 4) агент работает только "
                                 "после включения в реестр страховщика и регистрации в Единой системе")
    if _user_by_login(con, login):
        raise HTTPException(409, "Такой логин уже занят")
    if eais and db.rows(con, "SELECT 1 FROM users WHERE agent_eais_id=?", eais):
        raise HTTPException(409, "Пользователь с таким ID агента уже зарегистрирован")

    bootstrap = data.role == "админ" and con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    status = STATUS_ACTIVE if bootstrap else STATUS_PENDING
    pw_hash, salt = hash_password(data.password)
    ts = db.now()
    cur = con.execute(
        "INSERT INTO users (login, full_name, phone, role, branch, agent_eais_id, password_hash, salt, status,"
        " created_at, approved_by, approved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (login, data.full_name.strip(), data.phone.strip(), data.role, data.branch.strip(), eais or None,
         pw_hash, salt, status, ts, "bootstrap" if bootstrap else None, ts if bootstrap else None))
    uid = cur.lastrowid
    if bootstrap:
        db.audit(con, login, "создан первый администратор", f"user:{uid}", {"full_name": data.full_name})
    else:
        db.audit(con, login, "заявка на доступ", f"user:{uid}",
                 {"role": data.role, "branch": data.branch, "agent_eais_id": eais or None,
                  "in_registry": bool(registry_entry(con, eais))})
    u = _user_by_id(con, uid)
    return _public(u) | {"message": "Администратор создан, можно входить" if bootstrap else
                         "Заявка принята. После проверки администратором вы сможете войти"}


def pending_users(con) -> list:
    out = []
    for u in db.rows(con, "SELECT * FROM users WHERE status=? ORDER BY created_at", STATUS_PENDING):
        out.append(_public(u) | {"registry": registry_entry(con, u["agent_eais_id"])})
    return out


def approve_user(con, admin: dict, uid: int) -> dict:
    u = _user_by_id(con, uid)
    if u["status"] == STATUS_ACTIVE:
        return _public(u)
    con.execute("UPDATE users SET status=?, approved_by=?, approved_at=? WHERE id=?",
                (STATUS_ACTIVE, admin["login"], db.now(), uid))
    reg = registry_entry(con, u["agent_eais_id"])
    db.audit(con, admin["login"], "подтверждён пользователь", f"user:{uid}",
             {"login": u["login"], "role": u["role"], "agent_eais_id": u["agent_eais_id"],
              "registry": reg["name"] if reg else "не найден в реестре компании"})
    return _public(_user_by_id(con, uid))


def reject_user(con, admin: dict, uid: int, reason: str = "") -> dict:
    u = _user_by_id(con, uid)
    con.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    con.execute("DELETE FROM login_codes WHERE user_id=?", (uid,))
    con.execute("DELETE FROM users WHERE id=?", (uid,))
    db.audit(con, admin["login"], "отклонена заявка", f"user:{uid}",
             {"login": u["login"], "full_name": u["full_name"], "role": u["role"],
              "agent_eais_id": u["agent_eais_id"], "reason": reason})
    return {"ok": True, "login": u["login"]}


def block_user(con, admin: dict, uid: int, reason: str = "") -> dict:
    """Увольнение или исключение из реестра: доступ закрывается сразу, все сессии удаляются."""
    u = _user_by_id(con, uid)
    if u["id"] == admin["id"]:
        raise HTTPException(422, "Нельзя заблокировать самого себя")
    con.execute("UPDATE users SET status=? WHERE id=?", (STATUS_BLOCKED, uid))
    con.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    db.audit(con, admin["login"], "заблокирован пользователь", f"user:{uid}", {"login": u["login"], "reason": reason})
    return _public(_user_by_id(con, uid))


def _check_can_login(con, u: dict):
    if u["status"] == STATUS_PENDING:
        raise HTTPException(403, "Заявка ещё не подтверждена администратором")
    if u["status"] == STATUS_BLOCKED:
        raise HTTPException(403, "Учётная запись заблокирована. Обратитесь к администратору")
    if u["role"] == "агент" and u["agent_eais_id"]:
        reg = registry_entry(con, u["agent_eais_id"])
        if reg and reg["status"] != "активен":
            raise HTTPException(403, "Агент исключён из реестра компании — вход закрыт")


def create_session(con, user: dict, ip: str = "", user_agent: str = "") -> tuple[str, str]:
    """Возвращает (token, expires_at)."""
    token = secrets.token_urlsafe(32)
    exp = _ts(datetime.now() + timedelta(hours=SESSION_HOURS))
    con.execute("INSERT INTO sessions (token, user_id, created_at, expires_at, ip, user_agent) VALUES (?,?,?,?,?,?)",
                (token, user["id"], db.now(), exp, ip, user_agent[:200] if user_agent else None))
    con.execute("UPDATE users SET last_login=? WHERE id=?", (db.now(), user["id"]))
    db.audit(con, user["login"], "вход", f"user:{user['id']}", {"ip": ip})
    return token, exp


def login_user(con, data: LoginIn, ip: str = "", user_agent: str = "") -> dict:
    """
    Шаг 1. Возвращает {"step":"done","token":...} — сессия создана, или
    {"step":"code", ...} — нужен код из Telegram (verify_login_code).
    """
    u = _user_by_login(con, data.login)
    if not u or not check_password(data.password, u["password_hash"], u["salt"]):
        db.audit(con, data.login.strip().lower(), "неудачный вход", None, {"ip": ip})
        raise HTTPException(401, "Неверный логин или пароль")
    _check_can_login(con, u)
    if not u["telegram_id"]:
        token, exp = create_session(con, u, ip, user_agent)
        out = {"step": "done", "token": token, "expires_at": exp, "user": _public(u)}
        if u.get("must_change_password"):
            # временный пароль от администратора: до смены сессия пускает только в MUST_CHANGE_ALLOWED
            out |= {"must_change_password": True, "next": "PUT /auth/password", "message": MUST_CHANGE_DETAIL}
        return out
    # второй шаг: одноразовый код
    code = f"{secrets.randbelow(10**6):06d}"
    con.execute("DELETE FROM login_codes WHERE user_id=?", (u["id"],))
    con.execute("INSERT INTO login_codes (user_id, code, expires_at, channel) VALUES (?,?,?,?)",
                (u["id"], code, _ts(datetime.now() + timedelta(minutes=CODE_MINUTES)), "telegram"))
    delivery = send_code(u, code) or "недоступно — бот не настроен"
    out = {"step": "code", "delivery": delivery, "valid_minutes": CODE_MINUTES}
    if dev_mode():
        out["dev_code"] = code
    return out


def verify_login_code(con, data: CodeIn, ip: str = "", user_agent: str = "") -> dict:
    u = _user_by_login(con, data.login)
    if not u:
        raise HTTPException(401, "Код не подходит")
    rows = db.rows(con, "SELECT * FROM login_codes WHERE user_id=? ORDER BY id DESC LIMIT 1", u["id"])
    lc = rows[0] if rows else None
    if not lc or lc["expires_at"] < db.now() or lc["attempts"] >= CODE_MAX_ATTEMPTS:
        raise HTTPException(401, "Код истёк или не запрашивался — войдите заново")
    if not hmac.compare_digest(lc["code"], data.code.strip()):
        con.execute("UPDATE login_codes SET attempts=attempts+1 WHERE id=?", (lc["id"],))
        left = CODE_MAX_ATTEMPTS - lc["attempts"] - 1
        raise HTTPException(401, f"Неверный код, осталось попыток: {left}")
    con.execute("DELETE FROM login_codes WHERE user_id=?", (u["id"],))
    _check_can_login(con, u)
    token, exp = create_session(con, u, ip, user_agent)
    out = {"step": "done", "token": token, "expires_at": exp, "user": _public(u)}
    if u.get("must_change_password"):
        out |= {"must_change_password": True, "next": "PUT /auth/password", "message": MUST_CHANGE_DETAIL}
    return out


def change_password(con, user: dict, data: PasswordIn, keep_token: Optional[str] = None) -> dict:
    """Смена пароля самим человеком. Старый пароль обязателен — и временный тоже: сессию могли оставить
    открытой на чужом компьютере. Остальные сессии закрываются, текущая остаётся."""
    u = _user_by_id(con, user["id"])
    if not check_password(data.old_password or "", u["password_hash"], u["salt"]):
        db.audit(con, u["login"], "смена пароля: неверный текущий пароль", f"user:{u['id']}", None)
        raise HTTPException(403, "Текущий пароль не подходит")
    new = data.new_password or ""
    if len(new) < PASSWORD_MIN:
        raise HTTPException(422, f"Новый пароль — не короче {PASSWORD_MIN} символов")
    if new == data.old_password:
        raise HTTPException(422, "Новый пароль должен отличаться от временного")
    pw_hash, salt = hash_password(new)
    con.execute("UPDATE users SET password_hash=?, salt=?, must_change_password=0 WHERE id=?",
                (pw_hash, salt, u["id"]))
    if keep_token:
        con.execute("DELETE FROM sessions WHERE user_id=? AND token<>?", (u["id"], keep_token))
    db.audit(con, u["login"], "пароль изменён", f"user:{u['id']}",
             {"был временный": bool(u.get("must_change_password"))})
    return {"ok": True, "must_change_password": False, "message": "Пароль изменён"}


def session_user(con, token: Optional[str], extend: bool = True) -> Optional[dict]:
    """Пользователь по токену сессии; просроченная сессия удаляется. При активности срок продлевается."""
    if not token:
        return None
    s = db.rows(con, "SELECT * FROM sessions WHERE token=?", token)
    if not s:
        return None
    s = s[0]
    if s["expires_at"] < db.now():
        con.execute("DELETE FROM sessions WHERE token=?", (token,))
        return None
    u = db.rows(con, "SELECT * FROM users WHERE id=? AND status=?", s["user_id"], STATUS_ACTIVE)
    if not u:
        return None
    if extend:
        # продлеваем не чаще раза в минуту: запись в базу на каждое обращение выстраивала
        # параллельные запросы в очередь. Срок сессии от этого короче не более чем на минуту.
        new_exp = datetime.now() + timedelta(hours=SESSION_HOURS)
        if s["expires_at"] < _ts(new_exp - timedelta(seconds=SESSION_EXTEND_EVERY_SEC)):
            con.execute("UPDATE sessions SET expires_at=? WHERE token=?", (_ts(new_exp), token))
    return u[0]


def logout_session(con, token: Optional[str]) -> bool:
    if not token:
        return False
    s = db.rows(con, "SELECT s.user_id, u.login FROM sessions s JOIN users u ON u.id=s.user_id WHERE token=?", token)
    con.execute("DELETE FROM sessions WHERE token=?", (token,))
    if s:
        db.audit(con, s[0]["login"], "выход", f"user:{s[0]['user_id']}")
    return bool(s)


def can(user: dict, right: str) -> bool:
    return user["role"] == "админ" or user["role"] in PERMISSIONS.get(right, set())


def check_role(user: dict, roles: tuple) -> dict:
    if roles and user["role"] not in roles and user["role"] != "админ":
        raise HTTPException(403, f"Недостаточно прав: нужна роль {' / '.join(roles)}")
    return user


# ---------- зависимости FastAPI ----------

def _set_cookie(response: Response, token: str):
    # secure=True включить, когда сервер выйдет за https
    response.set_cookie(COOKIE, token, max_age=SESSION_HOURS * 3600, httponly=True, samesite="lax", path="/")


def set_session_cookie(response: Response, token: str):
    """Публичное имя _set_cookie: cookie «sid» ставят и другие модули входа (app/google_auth.py)."""
    _set_cookie(response, token)


def bearer_token(request: Request) -> Optional[str]:
    """Токен сессии из заголовка Authorization: Bearer … — для вызовов без браузера."""
    h = request.headers.get("authorization") or ""
    if h[:7].lower() == "bearer ":
        return h[7:].strip() or None
    return None


def request_token(request: Request) -> Optional[str]:
    """Токен сессии: cookie «sid» или Bearer. Один источник правды для auth, guard и tgbot."""
    return request.cookies.get(COOKIE) or bearer_token(request)


def current_user(request: Request, response: Response) -> dict:
    """Depends: текущий пользователь по cookie «sid» или Bearer. 401, если сессии нет или она истекла."""
    u = request.scope.get("surveyor_user")     # единый вход (app/guard.py) уже проверил сессию
    token = request_token(request)
    if u is None:
        with db.tx() as con:
            u = session_user(con, token)
    if not u:
        raise HTTPException(401, "Нужно войти в систему")
    if request.cookies.get(COOKIE):
        _set_cookie(response, token)          # продлеваем cookie вместе с сессией
    return u


def optional_user(request: Request) -> Optional[dict]:
    """Тот же вошедший, что и current_user, но без 401: None — гость (решение заказчика 22.09.2026)."""
    u = request.scope.get("surveyor_user")
    if u is not None:
        return u
    token = request_token(request)
    if not token:
        return None
    try:
        with db.tx() as con:
            return session_user(con, token)
    except Exception as e:
        print("auth: сессию проверить не удалось:", e)
        return None


def require(*roles: str):
    """Фабрика зависимостей: require('андеррайтер', 'актуарий'). Админ проходит всегда."""
    def dep(user: dict = Depends(current_user)) -> dict:
        return check_role(user, roles)
    return dep


# ---------- маршруты ----------

def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


# Решение заказчика 21.09.2026: «заявок на доступ» больше нет. Человек входит сам —
# через Telegram (код боту или мини-приложение) или через рабочую почту Google, и сразу
# становится активным сотрудником. Точка оставлена закрытой, а не удалена: по ней ещё могут
# постучаться старые вкладки и закладки, и им нужен понятный ответ, а не 404.
REGISTER_CLOSED = ("Регистрация теперь через Telegram или Google. Откройте страницу входа "
                   "и выберите «Войти через Telegram» или «Продолжить с Google»")


@router.post("/auth/register")
def register(body: RegisterIn):
    raise HTTPException(410, REGISTER_CLOSED)


@router.get("/auth/pending")
def pending(admin: dict = Depends(require("админ"))):
    with db.tx() as con:
        return pending_users(con)


@router.get("/auth/users")
def users_list(admin: dict = Depends(require("админ"))):
    with db.tx() as con:
        return [_public(u) | {"registry": registry_entry(con, u["agent_eais_id"])}
                for u in db.rows(con, "SELECT * FROM users ORDER BY status, login")]


@router.get("/auth/colleagues")
def colleagues():
    """Справочник действующих работников — из кого выбирают согласующих (экран /approvals и экран агента).

    Отдаём только ФИО, роль и филиал: ни телефонов, ни ID агента, ни сведений о страхователях.
    Список нужен обычному агенту при сохранении запроса, поэтому прав администратора не требует.
    """
    with db.tx() as con:
        return [{k: u[k] for k in ("id", "login", "full_name", "role", "branch", "status")}
                for u in db.rows(con, "SELECT id, login, full_name, role, branch, status FROM users"
                                      " WHERE status=? ORDER BY full_name", STATUS_ACTIVE)]


@router.post("/auth/approve/{uid}")
def approve(uid: int, admin: dict = Depends(require("админ"))):
    with db.tx() as con:
        return approve_user(con, admin, uid)


@router.post("/auth/reject/{uid}")
def reject(uid: int, reason: str = "", admin: dict = Depends(require("админ"))):
    with db.tx() as con:
        return reject_user(con, admin, uid, reason)


@router.post("/auth/block/{uid}")
def block(uid: int, reason: str = "", admin: dict = Depends(require("админ"))):
    with db.tx() as con:
        return block_user(con, admin, uid, reason)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    with db.tx() as con:
        out = login_user(con, body, _ip(request), request.headers.get("user-agent", ""))
    if out["step"] == "done":
        _set_cookie(response, out.pop("token"))
    return out


@router.post("/auth/verify-code")
def verify_code(body: CodeIn, request: Request, response: Response):
    with db.tx() as con:
        out = verify_login_code(con, body, _ip(request), request.headers.get("user-agent", ""))
    _set_cookie(response, out.pop("token"))
    return out


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    with db.tx() as con:
        logout_session(con, request_token(request))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.put("/auth/password")
def put_password(body: PasswordIn, request: Request, user: dict = Depends(current_user)):
    """Смена пароля: {old_password, new_password}. Обязательна после входа с временным паролем."""
    with db.tx() as con:
        return change_password(con, user, body, keep_token=request_token(request))


@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return _public(user) | {"rights": sorted(r for r in PERMISSIONS if can(user, r))}


@router.get("/login", response_class=HTMLResponse)
def login_page():
    return web.read_text(ROOT / "app" / "login.html")
