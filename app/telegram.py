"""
Вход через мини-приложение Telegram.

Проверка initData — по описанию Telegram Web Apps (https://core.telegram.org/bots/webapps):
  secret_key         = HMAC_SHA256(key="WebAppData", msg=<токен бота>)
  data_check_string  = пары "key=value" из initData, кроме hash (и служебного signature),
                       отсортированные по ключу и склеенные через перевод строки
  ожидаемый hash     = hex(HMAC_SHA256(key=secret_key, msg=data_check_string))
Дополнительно проверяем auth_date: данные старше суток не принимаем.

Токен бота берётся из настроек (TELEGRAM_BOT_TOKEN, таблица app_settings или .env).
Без токена работает «режим браузера»: страница открывается, но пользователь не авторизован —
вход по логину и паролю на /login остаётся рабочим.

Привязка к пользователям — по docs/Верификация пользователей.md: незнакомый telegram_id
не пускается внутрь, а создаёт заявку на верификацию, которую подтверждает администратор.

Файлы (фото, документы) мини-приложение шлёт в уже существующие точки:
  POST /requests/{rid}/photos, POST /requests/{rid}/documents/upload — новых не заводим.
"""
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import auth, db, llm

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent
TG_PAGE = ROOT / "app" / "tg.html"

MAX_AGE_SEC = 24 * 3600          # устаревание initData
MODE_TG, MODE_BROWSER = "telegram", "браузер"
ST_OK, ST_NEED, ST_ERR = "ок", "нужна верификация", "ошибка"


def bot_token() -> str:
    return (llm.get("TELEGRAM_BOT_TOKEN") or "").strip()


def pd_mode() -> str:
    """Режим персональных данных — тот же источник, что у /deploy/status и /deploy/settings."""
    return (llm.get("PD_MODE") or "test").strip().lower()


def data_check_string(pairs: list) -> str:
    """Пары key=value без hash, по алфавиту, через \\n."""
    return "\n".join(f"{k}={v}" for k, v in sorted(pairs, key=lambda kv: kv[0]))


def check_init_data(init_data: str, token: str, max_age_sec: int = MAX_AGE_SEC) -> dict:
    """
    Возвращает {"ok": bool, "reason": str, "data": {...}, "user": {...}|None}.
    Исключений не бросает: недоверенный вход не должен ронять сервер.
    """
    if not token:
        return {"ok": False, "reason": "Токен бота не задан", "data": {}, "user": None}
    if not init_data:
        return {"ok": False, "reason": "Данные Telegram не переданы", "data": {}, "user": None}
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    except Exception:
        return {"ok": False, "reason": "Данные Telegram нечитаемы", "data": {}, "user": None}
    data = dict(pairs)
    got_hash = data.get("hash", "")
    if not got_hash:
        return {"ok": False, "reason": "В данных Telegram нет подписи", "data": data, "user": None}
    # signature — отдельная подпись сторонних приложений, в строку проверки не входит
    checked = [(k, v) for k, v in pairs if k not in ("hash", "signature")]
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check_string(checked).encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got_hash.lower()):
        return {"ok": False, "reason": "Подпись Telegram не совпала", "data": data, "user": None}
    try:
        age = time.time() - int(data.get("auth_date", "0"))
    except ValueError:
        return {"ok": False, "reason": "Неверная дата в данных Telegram", "data": data, "user": None}
    if age > max_age_sec:
        return {"ok": False, "reason": "Данные входа устарели — откройте приложение заново",
                "data": data, "user": None}
    user = None
    if data.get("user"):
        try:
            user = json.loads(data["user"])
        except Exception:
            user = None
    return {"ok": True, "reason": "Подпись верна", "data": data, "user": user}


# --------------------------------------------------------------------------- #
#  Привязка к пользователям системы
# --------------------------------------------------------------------------- #

def _display_name(tg_user: dict) -> str:
    parts = [(tg_user.get("first_name") or "").strip(), (tg_user.get("last_name") or "").strip()]
    return " ".join(p for p in parts if p) or ("Пользователь Telegram " + str(tg_user.get("id")))


def link_or_request(con, tg_user: dict) -> dict:
    """
    Известный telegram_id — вход; незнакомый — заявка на верификацию (её подтверждает админ,
    сверяя ФИО и ID агента по реестру, см. docs/Верификация пользователей.md).
    """
    tg_id = str(tg_user.get("id") or "").strip()
    if not tg_id.isdigit():
        return {"status": ST_ERR, "user": None, "reason": "Telegram не передал идентификатор пользователя"}
    found = db.rows(con, "SELECT * FROM users WHERE telegram_id=?", tg_id)
    # Первый администратор — по username из настройки TG_ADMIN_USERNAME, пока на сервере нет ни одного
    # администратора: заказчик входит сразу, без кода. Как только админ есть, правило не действует.
    boot = (llm.get("TG_ADMIN_USERNAME") or "").strip().lstrip("@").lower()
    uname = (tg_user.get("username") or "").strip().lower()
    if boot and uname and uname == boot and not db.rows(con, "SELECT 1 FROM users WHERE role='админ'"):
        ts = db.now()
        if found:
            con.execute("UPDATE users SET role='админ', status=?, approved_by='tg-bootstrap', approved_at=?"
                        " WHERE id=?", (auth.STATUS_ACTIVE, ts, found[0]["id"]))
        else:
            pw_hash, salt = auth.hash_password(secrets.token_urlsafe(24))
            con.execute("INSERT INTO users (login, full_name, phone, role, branch, agent_eais_id, password_hash,"
                        " salt, status, telegram_id, created_at, approved_by, approved_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("tg" + tg_id, _display_name(tg_user), "", "админ", "", None, pw_hash, salt,
                         auth.STATUS_ACTIVE, tg_id, ts, "tg-bootstrap", ts))
        found = db.rows(con, "SELECT * FROM users WHERE telegram_id=?", tg_id)
        db.audit(con, found[0]["login"], "первый администратор по username Telegram",
                 "user:%s" % found[0]["id"], {"telegram_id": tg_id})
    if found:
        u = found[0]
        if u["status"] == auth.STATUS_ACTIVE:
            return {"status": ST_OK, "user": auth._public(u), "reason": "", "row": u}
        if u["status"] == auth.STATUS_BLOCKED:
            return {"status": ST_ERR, "user": None, "reason": "Доступ заблокирован администратором"}
        return {"status": ST_NEED, "user": auth._public(u),
                "reason": "Заявка уже подана и ждёт подтверждения администратором"}
    # новая заявка: логин технический, пароль случайный (вход только через Telegram),
    # роль «агент» — администратор при подтверждении сверит ID агента по реестру
    login = "tg" + tg_id
    if db.rows(con, "SELECT 1 FROM users WHERE login=?", login):
        return {"status": ST_NEED, "user": None, "reason": "Заявка уже подана и ждёт подтверждения"}
    pw_hash, salt = auth.hash_password(secrets.token_urlsafe(24))
    con.execute("INSERT INTO users (login, full_name, phone, role, branch, agent_eais_id, password_hash,"
                " salt, status, telegram_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (login, _display_name(tg_user), "", "агент", "", None, pw_hash, salt,
                 auth.STATUS_PENDING, tg_id, db.now()))
    # в журнал — без ФИО: только технический логин и telegram_id (правило проекта № 8)
    db.audit(con, login, "заявка на доступ из Telegram", "user:" + login, {"telegram_id": tg_id})
    return {"status": ST_NEED, "user": None,
            "reason": "Вы впервые вошли из Telegram. Заявка создана — администратор должен подтвердить "
                      "вас по реестру агентов, после этого вход откроется"}


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #

class AuthIn(BaseModel):
    initData: str = ""


@router.post("/tg/auth")
def tg_auth(body: AuthIn, request: Request, response: Response):
    """Вход мини-приложения. Без токена бота — честный режим «браузер»."""
    token = bot_token()
    if not token:
        return {"mode": MODE_BROWSER, "status": ST_ERR, "user": None, "pd_mode": pd_mode(),
                "reason": "Бот Telegram не подключён: страница работает, но вход по Telegram недоступен. "
                          "Войдите по логину и паролю на /login"}
    res = check_init_data(body.initData, token)
    if not res["ok"]:
        return {"mode": MODE_TG, "status": ST_ERR, "user": None, "pd_mode": pd_mode(),
                "reason": res["reason"]}
    if not res["user"]:
        return {"mode": MODE_TG, "status": ST_ERR, "user": None, "pd_mode": pd_mode(),
                "reason": "Telegram не передал сведения о пользователе"}
    with db.tx() as con:
        out = link_or_request(con, res["user"])
        if out["status"] == ST_OK:
            tok, _ = auth.create_session(con, out["row"], ip=(request.client.host if request.client else ""),
                                         user_agent="telegram-mini-app")
            auth._set_cookie(response, tok)
            db.audit(con, out["row"]["login"], "вход через Telegram", "user:%s" % out["row"]["id"], None)
    return {"mode": MODE_TG, "status": out["status"], "user": out.get("user"),
            "pd_mode": pd_mode(), "reason": out.get("reason", "")}


@router.get("/tg/status")
def tg_status():
    """Подключён ли бот. Токен наружу не отдаём — только маску."""
    t = bot_token()
    return {"bot_connected": bool(t), "token_mask": llm.mask_key(t),
            "mode": MODE_TG if t else MODE_BROWSER, "pd_mode": pd_mode(),
            "reason": "" if t else "Не задан TELEGRAM_BOT_TOKEN — вход по Telegram недоступен"}


STUB = """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Мини-приложение INSON</title>
<body style="background:#0F1418;color:#E6ECF0;font:15px/1.5 system-ui;padding:32px">
<h1 style="font-size:20px">Мини-приложение готовится</h1>
<p>Страница <code>app/tg.html</code> ещё не выложена. Сервер уже работает:
проверка входа — <code>POST /tg/auth</code>, состояние бота — <code>GET /tg/status</code>.</p>
</body></html>"""


@router.get("/tg", response_class=HTMLResponse)
def tg_page():
    """Страницу делает интерфейсный поток (app/tg.html); пока её нет — понятная заглушка."""
    if TG_PAGE.exists():
        return TG_PAGE.read_text(encoding="utf-8")
    return STUB
