"""
Вход через Google (OAuth 2.0, authorization code flow + PKCE). Только стандартная библиотека
Python: urllib, hashlib, hmac, secrets, json — сторонних пакетов не требуется.

Подключение (app/main.py): app.include_router(google_auth.router). Инструкция для администратора —
docs/Вход через Google.md, ключи задаются на странице /admin/deploy или в файле .secrets.env.

Как идёт вход:
  1. GET  /auth/google/status   — настроен ли вход вообще (кнопку рисовать или прятать).
  2. GET  /auth/google?next=…   — 302 на страницу выбора аккаунта Google. Перед уходом сервер
     запоминает state и секрет PKCE (таблица google_oauth_states, 10 минут) и кладёт тот же state
     в httpOnly-cookie «gstate». Возврат принимается, только если state есть в таблице, не истёк
     и совпадает с cookie; строка гасится при первом же использовании.
  3. GET  /auth/google/callback — обмен кода на токены (oauth2.googleapis.com/token), проверка
     id_token у самого Google (tokeninfo: подпись проверяет Google, мы сверяем aud, iss, exp,
     email_verified, наличие email и sub) и ограничение по доменам почты.
  4. 302 на next с одним параметром ?auth=<одноразовый код>.
  5. POST /auth/google/exchange — код меняется на сессию («done») или на ключ анкеты («register»).
  6. POST /auth/google/register — анкета (ФИО, департамент, должность, согласие): создаётся
     активный «сотрудник» с привязкой к аккаунту Google. Телефон необязателен, но если он указан,
     нужно отдельное согласие на его обработку — в pd_consents ложится вторая строка (scope «телефон»).

Почему в адресе возврата одноразовый код, а не токен сессии:
  адрес страницы попадает в историю браузера, в журналы прокси и в заголовок Referer при загрузке
  внешних картинок и шрифтов; токен сессии живёт 12 часов, а одноразовый код — 2 минуты и гаснет
  после первого обмена, поэтому утечка адреса входа не даёт.

Персональные данные (правило проекта № 8): в журнал (db.audit) почта целиком не пишется — только
домен и маска вида «i***v@insuranceon.uz». Сам адрес хранится один раз, в users.email.

Известное ограничение (решение заказчика 21.09.2026): учётные записи Google и Telegram
автоматически НЕ связываются — ни по почте, ни по телефону. Два входа одного человека пока
считаются разными учётными записями. Кнопки «Объединить» нет: объединение — отдельная задача
(перенос запросов, сессий, согласий и журнала), делать его молча нельзя.

Тесты — tests/test_google_auth.py: обращения к Google подменяются заглушкой, сеть не трогается.
"""
import base64
import hashlib
import hmac
import json
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import auth, db, llm, registration, tgbot

router = APIRouter()

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")
SCOPE = "openid email profile"

STATE_MINUTES = 10        # сколько живёт начатый вход
EXCHANGE_SECONDS = 120    # одноразовый код из адреса возврата
REG_MINUTES = 30          # ключ анкеты для нового человека
STATE_COOKIE = "gstate"
COOKIE_PATH = "/auth/google"
DEFAULT_NEXT = "/tg"
TIMEOUT_SEC = 15

NOT_CONFIGURED = "Вход через Google не настроен"
HINT = ("Задайте GOOGLE_CLIENT_ID и GOOGLE_CLIENT_SECRET на странице /admin/deploy или в файле "
        ".secrets.env — как их получить, написано в docs/Вход через Google.md")
EXPIRED = "Ссылка входа устарела — нажмите «Продолжить с Google» ещё раз"
NOT_CONFIRMED = "Вход не подтверждён — начните заново"
APPROVED_BY = "вход через Google"


# --------------------------------------------------------------------------- #
#  Настройки
# --------------------------------------------------------------------------- #

def client_id() -> str:
    return (llm.get("GOOGLE_CLIENT_ID") or "").strip()


def client_secret() -> str:
    return (llm.get("GOOGLE_CLIENT_SECRET") or "").strip()


def configured() -> bool:
    return bool(client_id() and client_secret())


def allowed_domains() -> list:
    """Домены почты, которым разрешён вход. Пусто — разрешён любой аккаунт Google."""
    raw = (llm.get("GOOGLE_ALLOWED_DOMAINS") or "").replace(";", ",")
    return [d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()]


def redirect_uri() -> str:
    return (llm.get("SERVER_URL") or "").rstrip("/") + "/auth/google/callback"


# --------------------------------------------------------------------------- #
#  Вспомогательное
# --------------------------------------------------------------------------- #

def _digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _challenge(verifier: str) -> str:
    """Отпечаток секрета PKCE: base64url(sha256(verifier)) без знаков «=» (метод S256)."""
    d = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(d).decode("ascii").rstrip("=")


def mask_email(email: str) -> str:
    """Маска для журнала: «ivanov@insuranceon.uz» → «i***v@insuranceon.uz»."""
    email = (email or "").strip()
    if "@" not in email:
        return "***"
    name, _, domain = email.partition("@")
    head = name[:1] if name else ""
    tail = name[-1:] if len(name) > 2 else ""
    return f"{head}***{tail}@{domain}"


def safe_next(raw: str) -> str:
    """Возвращаем человека только на свой же путь: «//чужой.сайт» и внешние адреса отбрасываем."""
    nxt = (raw or "").strip()
    if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt:
        return DEFAULT_NEXT
    return nxt.split("#")[0][:300]


def _ip(request: Request) -> str:
    c = request.scope.get("client")
    return (c[0] if c else "") or ""


# --------------------------------------------------------------------------- #
#  Обращения к Google (в тестах подменяются целиком)
# --------------------------------------------------------------------------- #

def _post(url: str, payload: dict) -> dict:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(url: str) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
        return json.loads(r.read().decode("utf-8"))


def exchange_code_for_tokens(code: str, code_verifier: str) -> dict:
    """Код авторизации → токены. Ошибку сети наружу не выносим, но и не глотаем: пишем в журнал."""
    return _post(TOKEN_URL, {"code": code, "client_id": client_id(), "client_secret": client_secret(),
                             "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
                             "code_verifier": code_verifier})


def fetch_tokeninfo(id_token: str) -> dict:
    """Разбор и проверка подписи id_token — на стороне Google (endpoint tokeninfo)."""
    return _get(TOKENINFO_URL + "?" + urllib.parse.urlencode({"id_token": id_token}))


# --------------------------------------------------------------------------- #
#  Проверка ответа Google
# --------------------------------------------------------------------------- #

def check_tokeninfo(info: dict) -> dict:
    """
    Сверяет то, что вернул Google, с нашим приложением. Любое несовпадение — отказ:
    aud (кому выдан токен), iss (кто выдал), exp (не просрочен), подтверждена ли почта,
    есть ли сам адрес и вечный идентификатор аккаунта sub.
    Возвращает {"sub","email","given_name","family_name","name"} или бросает 403 с причиной.
    """
    info = info or {}
    if str(info.get("aud") or "") != client_id():
        raise HTTPException(403, "Ответ Google выдан другому приложению — вход отклонён")
    if str(info.get("iss") or "") not in ISSUERS:
        raise HTTPException(403, "Ответ пришёл не от Google — вход отклонён")
    try:
        exp = int(info.get("exp") or 0)
    except (TypeError, ValueError):
        exp = 0
    if exp <= int(datetime.now().timestamp()):
        raise HTTPException(403, "Ответ Google просрочен — начните вход заново")
    verified = info.get("email_verified")
    if str(verified).strip().lower() != "true":      # Google отдаёт строкой "true"
        raise HTTPException(403, "Google не подтвердил этот адрес почты — вход отклонён")
    email = str(info.get("email") or "").strip().lower()
    sub = str(info.get("sub") or "").strip()
    if not email or not sub:
        raise HTTPException(403, "Google не вернул адрес почты — вход отклонён")
    return {"sub": sub, "email": email, "given_name": str(info.get("given_name") or "").strip(),
            "family_name": str(info.get("family_name") or "").strip(),
            "name": str(info.get("name") or "").strip()}


def check_domain(email: str):
    """Ограничение по доменам почты. Пустая настройка — пускаем любой аккаунт Google."""
    domains = allowed_domains()
    if not domains:
        return
    if (email.rpartition("@")[2] or "").lower() not in domains:
        raise HTTPException(403, "Вход разрешён только с адресов: " + ", ".join("@" + d for d in domains))


# --------------------------------------------------------------------------- #
#  Начатые входы (state + PKCE)
# --------------------------------------------------------------------------- #

def start_login(con, next_url: str, ip: str = "") -> dict:
    """Готовит адрес страницы Google. Возвращает {"url","state"}; state кладётся ещё и в cookie."""
    state = secrets.token_urlsafe(32)            # 32 случайных байта
    verifier = secrets.token_urlsafe(64)         # секрет PKCE: 43..128 знаков
    now = datetime.now()
    con.execute("DELETE FROM google_oauth_states WHERE expires_at < ?", (db.now(),))   # уборка старых
    con.execute("INSERT INTO google_oauth_states (state, code_verifier, next_url, created_at, expires_at, ip)"
                " VALUES (?,?,?,?,?,?)",
                (state, verifier, safe_next(next_url), _ts(now),
                 _ts(now + timedelta(minutes=STATE_MINUTES)), ip))
    params = {"client_id": client_id(), "redirect_uri": redirect_uri(), "response_type": "code",
              "scope": SCOPE, "state": state, "code_challenge": _challenge(verifier),
              "code_challenge_method": "S256", "access_type": "online", "prompt": "select_account"}
    return {"url": AUTH_URL + "?" + urllib.parse.urlencode(params), "state": state}


def take_state(con, state: str, cookie_state: str) -> dict:
    """Строка начатого входа. Одноразовая: удаляется при первом же обращении."""
    state = (state or "").strip()
    cookie_state = (cookie_state or "").strip()
    # сравниваем отпечатки: значение cookie присылает браузер, там может оказаться что угодно,
    # в том числе кириллица, а compare_digest принимает только ASCII
    if not state or not cookie_state or not hmac.compare_digest(_digest(state), _digest(cookie_state)):
        raise HTTPException(400, NOT_CONFIRMED)
    rows = db.rows(con, "SELECT * FROM google_oauth_states WHERE state=?", state)
    if not rows:
        raise HTTPException(400, NOT_CONFIRMED)
    row = rows[0]
    con.execute("DELETE FROM google_oauth_states WHERE state=?", (state,))
    con.commit()                                  # гасим сразу: дальше возможен отказ с откатом
    if row["expires_at"] < db.now():
        raise HTTPException(400, NOT_CONFIRMED)
    return row


# --------------------------------------------------------------------------- #
#  Одноразовые коды (обмен и анкета)
# --------------------------------------------------------------------------- #

def issue_code(con, kind: str, profile: dict, minutes_or_seconds: int, seconds: bool = False,
               user_id: Optional[int] = None, session_token: Optional[str] = None, ip: str = "") -> str:
    """Выдаёт одноразовый код. В базе только отпечаток sha256 — сам код живёт в адресе или в ответе."""
    code = secrets.token_urlsafe(32)
    now = datetime.now()
    delta = timedelta(seconds=minutes_or_seconds) if seconds else timedelta(minutes=minutes_or_seconds)
    con.execute("DELETE FROM google_login_codes WHERE expires_at < ?", (db.now(),))
    con.execute("INSERT INTO google_login_codes (kind, code_hash, google_sub, email, profile_json,"
                " user_id, session_token, created_at, expires_at, used_at, ip)"
                " VALUES (?,?,?,?,?,?,?,?,?,NULL,?)",
                (kind, _digest(code), profile["sub"], profile["email"],
                 json.dumps(profile, ensure_ascii=False), user_id, session_token,
                 _ts(now), _ts(now + delta), ip))
    return code


def take_code(con, kind: str, code: str) -> dict:
    """Проверяет и гасит одноразовый код. Просрочен, использован или чужой — 400."""
    rows = db.rows(con, "SELECT * FROM google_login_codes WHERE code_hash=? AND kind=?",
                   _digest((code or "").strip()), kind)
    row = rows[0] if rows else None
    if not row or row["used_at"] or row["expires_at"] < db.now():
        raise HTTPException(400, EXPIRED)
    con.execute("UPDATE google_login_codes SET used_at=? WHERE id=?", (db.now(), row["id"]))
    con.commit()                                  # код гаснет даже если дальше будет отказ
    return row


# --------------------------------------------------------------------------- #
#  Возврат от Google
# --------------------------------------------------------------------------- #

def handle_callback(con, code: str, state: str, cookie_state: str, ip: str = "") -> dict:
    """
    Весь разбор возврата: state → токены → проверка id_token → домен → сессия или анкета.
    Возвращает {"next": путь, "auth": одноразовый код, "token": токен сессии или None}.
    """
    row = take_state(con, state, cookie_state)
    if not (code or "").strip():
        raise HTTPException(400, NOT_CONFIRMED)
    try:
        tokens = exchange_code_for_tokens(code, row["code_verifier"])
    except HTTPException:
        raise
    except Exception as e:
        db.audit(con, APPROVED_BY, "вход через Google не удался", None, {"шаг": "обмен кода", "ошибка": str(e)[:200]})
        raise HTTPException(502, "Google не ответил на запрос токена — попробуйте ещё раз")
    id_token = (tokens or {}).get("id_token")
    if not id_token:
        db.audit(con, APPROVED_BY, "вход через Google не удался", None,
                 {"шаг": "обмен кода", "ошибка": str((tokens or {}).get("error") or "нет id_token")[:200]})
        raise HTTPException(403, "Google не вернул подтверждение личности — вход отклонён")
    try:
        info = fetch_tokeninfo(id_token)
    except HTTPException:
        raise
    except Exception as e:
        db.audit(con, APPROVED_BY, "вход через Google не удался", None, {"шаг": "проверка id_token", "ошибка": str(e)[:200]})
        raise HTTPException(502, "Google не ответил на проверку подтверждения — попробуйте ещё раз")

    try:
        profile = check_tokeninfo(info)
    except HTTPException as e:
        # в журнал — только факт и причина: адреса почты целиком здесь ещё нет и быть не должно
        db.audit(con, APPROVED_BY, "вход через Google отклонён", None, {"причина": e.detail})
        con.commit()
        raise
    try:
        check_domain(profile["email"])
    except HTTPException as e:
        db.audit(con, APPROVED_BY, "вход через Google отклонён", None,
                 {"причина": "домен вне списка", "домен": profile["email"].rpartition("@")[2]})
        con.commit()
        raise e

    user = auth.user_by_google_sub(con, profile["sub"])
    if user:
        token, _ = auth.create_session(con, user, ip, "google")
        auth_code = issue_code(con, "обмен", profile, EXCHANGE_SECONDS, seconds=True,
                               user_id=user["id"], session_token=token, ip=ip)
        db.audit(con, user["login"], "вход через Google", f"user:{user['id']}",
                 {"почта": mask_email(profile["email"])})
        return {"next": row["next_url"] or DEFAULT_NEXT, "auth": auth_code, "token": token}

    # незнакомый аккаунт: пользователя не создаём, ждём анкету (POST /auth/google/register)
    auth_code = issue_code(con, "обмен", profile, EXCHANGE_SECONDS, seconds=True, ip=ip)
    db.audit(con, APPROVED_BY, "вход через Google: нужна анкета", None,
             {"почта": mask_email(profile["email"])})
    return {"next": row["next_url"] or DEFAULT_NEXT, "auth": auth_code, "token": None}


def exchange(con, code: str) -> dict:
    """Одноразовый код из адреса → сессия («done») или ключ анкеты («register»)."""
    row = take_code(con, "обмен", code)
    profile = json.loads(row["profile_json"] or "{}")
    if row["user_id"] and row["session_token"]:
        users = db.rows(con, "SELECT * FROM users WHERE id=?", row["user_id"])
        if not users:
            raise HTTPException(400, EXPIRED)
        return {"step": "done", "token": row["session_token"], "user": auth._public(users[0])}
    reg_token = issue_code(con, "анкета", profile, REG_MINUTES, ip=row["ip"] or "")
    full_name = " ".join(x for x in (profile.get("family_name"), profile.get("given_name")) if x) \
        or profile.get("name") or ""
    return {"step": "register", "reg_token": reg_token,
            "profile": {"full_name": full_name, "email": profile.get("email", ""),
                        "given_name": profile.get("given_name", ""),
                        "family_name": profile.get("family_name", "")}}


# --------------------------------------------------------------------------- #
#  Анкета нового человека
# --------------------------------------------------------------------------- #

class RegisterIn(BaseModel):
    reg_token: str = ""
    full_name: str = ""
    department: str = ""
    position: str = ""
    position_other: str = ""
    phone: str = ""                     # здесь необязателен: личность подтвердил Google
    consent: bool = False
    consent_phone: bool = False         # отдельное согласие на обработку номера (ЗРУ-547 ст. 18, 21, 31)


def register(con, data: RegisterIn, ip: str = "") -> dict:
    """Создаёт активного «сотрудника», привязанного к аккаунту Google. Проверки анкеты — общие
    с регистрацией в мини-приложении (app/registration.py), чтобы требования не разъехались."""
    row = take_code(con, "анкета", data.reg_token)
    profile = json.loads(row["profile_json"] or "{}")
    sub, email = row["google_sub"], row["email"]

    full_name = registration.check_full_name(data.full_name or profile.get("name") or "")
    department = registration.check_department(data.department)
    position = registration.check_position(data.position, data.position_other)
    registration.check_consent(data.consent)
    # телефон здесь необязателен, но если он указан — нужно отдельное согласие на его обработку
    # (docs/Регистрация и роли.md, раздел 10.1; ЗРУ-547 ст. 18, 21, 31)
    phone = None
    if (data.phone or "").strip():
        if not data.consent_phone:
            raise HTTPException(422, "Номер телефона можно не указывать. "
                                     "Если указываете — отметьте согласие на его обработку")
        phone = registration.normalize_phone(data.phone)

    existing = auth.user_by_google_sub(con, sub)
    if existing:                                  # успели войти в другой вкладке
        token, _ = auth.create_session(con, existing, ip, "google")
        return {"user_id": existing["id"], "login": existing["login"], "role": existing["role"],
                "status": existing["status"], "token": token, "user": auth._public(existing)}

    login = "g" + sub
    if db.rows(con, "SELECT 1 FROM users WHERE login=?", login):
        raise HTTPException(409, "Учётная запись с таким логином уже есть — обратитесь к администратору")
    pw_hash, salt = auth.hash_password(secrets.token_urlsafe(24))    # вход только через Google
    ts = db.now()
    cur = con.execute(
        "INSERT INTO users (login, full_name, phone, role, branch, department, position, agent_eais_id,"
        " password_hash, salt, status, google_sub, email, created_at, approved_by, approved_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (login, full_name, phone, auth.ROLE_EMPLOYEE, "", department, position, None,
         pw_hash, salt, auth.STATUS_ACTIVE, sub, email, ts, APPROVED_BY, ts))
    uid = cur.lastrowid

    # согласие на обработку ПД: текст и версия — из tgbot.consent_text(), в коде текста нет
    c = tgbot.consent_text(tgbot.CONSENT_MAIN)
    con.execute("INSERT INTO pd_consents (user_id, telegram_id, version, channel, created_at,"
                " consent_text_hash, scope) VALUES (?,?,?,?,?,?,?)",
                (uid, "", c["version"], APPROVED_BY, ts, c["hash"], tgbot.CONSENT_MAIN))
    if phone:                                     # отдельная строка на номер телефона
        cp = tgbot.consent_text(tgbot.CONSENT_PHONE)
        con.execute("INSERT INTO pd_consents (user_id, telegram_id, version, channel, created_at,"
                    " consent_text_hash, scope) VALUES (?,?,?,?,?,?,?)",
                    (uid, "", cp["version"], APPROVED_BY, ts, cp["hash"], tgbot.CONSENT_PHONE))
    # в журнал — без ФИО, департамента и телефона (правило проекта № 8)
    db.audit(con, login, "регистрация через Google", f"user:{uid}",
             {"почта": mask_email(email), "роль": auth.ROLE_EMPLOYEE, "согласие": c["version"]})
    token, _ = auth.create_session(con, db.rows(con, "SELECT * FROM users WHERE id=?", uid)[0], ip, "google")
    user = db.rows(con, "SELECT * FROM users WHERE id=?", uid)[0]
    return {"user_id": uid, "login": login, "role": auth.ROLE_EMPLOYEE, "status": auth.STATUS_ACTIVE,
            "token": token, "user": auth._public(user)}


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #

@router.get("/auth/google/status")
def get_status():
    """Настроен ли вход через Google. Интерфейс по этому ответу показывает или прячет кнопку."""
    domains = ", ".join("@" + d for d in allowed_domains())
    if not configured():
        return {"configured": False, "message": NOT_CONFIGURED, "domains": domains, "hint": HINT}
    return {"configured": True,
            "message": "Вход через Google настроен" + (f". Разрешены адреса: {domains}" if domains else ""),
            "domains": domains, "hint": ""}


@router.get("/auth/google")
def go(request: Request, next: str = DEFAULT_NEXT):
    """Начало входа: 302 на страницу выбора аккаунта Google."""
    if not configured():
        raise HTTPException(503, NOT_CONFIGURED + ". " + HINT)
    with db.tx() as con:
        started = start_login(con, next, _ip(request))
    resp = RedirectResponse(started["url"], status_code=302)
    resp.set_cookie(STATE_COOKIE, started["state"], max_age=STATE_MINUTES * 60, httponly=True,
                    samesite="lax", path=COOKIE_PATH)
    return resp


@router.get("/auth/google/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Возврат от Google. В адрес следующей страницы кладём только одноразовый код (см. шапку)."""
    if not configured():
        raise HTTPException(503, NOT_CONFIGURED + ". " + HINT)
    if error:
        raise HTTPException(400, "Google не подтвердил вход: " + error[:80])
    with db.tx() as con:
        out = handle_callback(con, code, state, request.cookies.get(STATE_COOKIE) or "", _ip(request))
    sep = "&" if "?" in out["next"] else "?"
    resp = RedirectResponse(out["next"] + sep + "auth=" + urllib.parse.quote(out["auth"]), status_code=302)
    resp.delete_cookie(STATE_COOKIE, path=COOKIE_PATH)
    if out["token"]:
        auth.set_session_cookie(resp, out["token"])
    return resp


class ExchangeIn(BaseModel):
    code: str = ""


@router.post("/auth/google/exchange")
def post_exchange(body: ExchangeIn, response: Response):
    """Одноразовый код из адреса → сессия или предложение заполнить анкету."""
    with db.tx() as con:
        out = exchange(con, body.code)
    if out.get("token"):
        auth.set_session_cookie(response, out["token"])
    return out


@router.post("/auth/google/register")
def post_register(body: RegisterIn, request: Request, response: Response):
    """Анкета нового человека: роль «сотрудник», активен сразу, подтверждение админом не нужно."""
    with db.tx() as con:
        out = register(con, body, _ip(request))
    auth.set_session_cookie(response, out["token"])
    return out
