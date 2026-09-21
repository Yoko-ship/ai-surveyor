"""
Вход из обычного браузера через Telegram — «обратный код».

Зачем. В мини-приложении Telegram вход берётся из подписанного initData (app/telegram.py).
В обычном браузере initData нет, а виджет Telegram Login требует настройки домена в BotFather
и обращается к серверам Telegram со страницы. Заказчику (21.09.2026) нужен вход из браузера
без сторонних виджетов и без настроек у бота, поэтому код идёт в обратную сторону:
не Telegram присылает нам подпись, а мы даём человеку одноразовый код, который он отправляет боту.

Как это работает:
  1. Браузер: POST /auth/tg-link/start → сервер выдаёт одноразовый код вида INS-7K3M, срок 10 минут,
     и анонимный link_id (ключ ожидания). В базе лежит только отпечаток кода.
  2. Человек нажимает «Открыть бота» (ссылка t.me/<бот>?start=link_INS-7K3M) или пишет код боту руками.
     app/tgbot.handle_message узнаёт код и вызывает bind(): к заявке привязывается telegram_id
     отправителя. Подделать чужую привязку нельзя: telegram_id берётся из обновления Telegram,
     а не из текста сообщения. Один код срабатывает один раз.
  3. Браузер опрашивает GET /auth/tg-link/status?link_id=… раз в две секунды. Как только код принят,
     сервер делает ровно то же, что /tg/auth после проверки подписи: telegram.link_or_request().
     Активный пользователь получает сессию и токен, новый — «нужна регистрация» и ту же анкету
     (app/registration.py), только код подтверждения телефона там уже не нужен: Telegram подтверждён
     самим фактом сообщения боту.

Чем это защищено:
  * код живёт 10 минут и гасится после первой привязки (used_at);
  * в базе только sha256(код + перец из app_settings) — из выгрузки базы код не восстановить;
  * не больше 5 кодов за 10 минут с одного link_id (и грубый порог по адресу — RATE_MAX_IP);
  * человек, наугад пишущий боту коды, после 5 неверных попыток за 10 минут получает отказ;
  * сам код никуда не пишется: ни в audit, ни в tg_messages, ни в печать сервера (правило № 8);
  * link_id знает только вкладка, которая его получила, — чужой link_id сессии не даёт.

Тесты — tests/test_tg_link.py (поддельные обновления Telegram, сеть не трогается).
"""
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import auth, db, llm, telegram

router = APIRouter()

PREFIX = "INS-"
# Без похожих знаков: нет 0/O, 1/I/L, 5/S. Человек переписывает код с экрана в чат руками.
ALPHABET = "ABCDEFGHJKMNPQRTUVWXYZ2346789"
CODE_LEN = 4                       # вид кода — INS-7K3M (образец заказчика)
CODE_RE = re.compile(r"^INS-[A-Z0-9]{%d}$" % CODE_LEN)

CODE_MINUTES = 10                  # столько живёт код
RATE_MINUTES = 10                  # окно ограничения выдачи
RATE_MAX = 5                       # не больше пяти кодов за окно на один link_id
# Адрес — грубая мера: за прокси (Railway, офисный выход в интернет) у всей компании он один,
# поэтому по адресу порог кратно выше, иначе пятый сотрудник за день остался бы без входа.
RATE_MAX_IP = 30
GUESS_MAX = 5                      # столько неверных кодов подряд принимаем от одного telegram_id
SESSION_MINUTES = 30               # столько после привязки можно доводить регистрацию анкетой

DEFAULT_BOT = "inson_surveyor_bot"  # имя бота, если getMe недоступен (настройка TG_BOT_USERNAME)

ST_PENDING, ST_DONE, ST_REGISTER = "ожидание", "готово", "нужна регистрация"
ST_EXPIRED, ST_NONE = "истёк", "нет кода"


# --------------------------------------------------------------------------- #
#  Код и его отпечаток
# --------------------------------------------------------------------------- #

def _pepper(con) -> str:
    """Общая соль отпечатков. Искать код в базе надо по хэшу, поэтому соль одна на все коды;
    она лежит в app_settings, а не в коде, — из одной выгрузки таблицы коды не перебрать."""
    r = db.rows(con, "SELECT value FROM app_settings WHERE key='TG_LINK_PEPPER'")
    if r and (r[0]["value"] or "").strip():
        return r[0]["value"].strip()
    value = secrets.token_hex(16)
    con.execute("DELETE FROM app_settings WHERE key='TG_LINK_PEPPER'")
    con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?)",
                ("TG_LINK_PEPPER", value, db.now()))
    return value


def code_hash(con, code: str) -> str:
    return hashlib.sha256((_pepper(con) + "|" + normalize_code(code)).encode("utf-8")).hexdigest()


def normalize_code(raw: str) -> str:
    """Человек может прислать код строчными буквами, с пробелами или без приставки INS-."""
    s = re.sub(r"\s+", "", (raw or "")).upper().replace("—", "-").replace("–", "-")
    if s.startswith("LINK_"):
        s = s[5:]
    if not s.startswith(PREFIX) and re.fullmatch(r"[A-Z0-9]{%d}" % CODE_LEN, s):
        s = PREFIX + s
    return s


def looks_like_code(raw: str) -> bool:
    return bool(CODE_RE.fullmatch(normalize_code(raw)))


def new_code() -> str:
    return PREFIX + "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def new_link_id() -> str:
    return secrets.token_urlsafe(24)


LINK_RE = re.compile(r"^[A-Za-z0-9_\-]{16,64}$")


def clean_link_id(raw: str) -> str:
    s = (raw or "").strip()
    return s if LINK_RE.fullmatch(s) else ""


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
#  Имя бота для ссылки t.me
# --------------------------------------------------------------------------- #

def bot_username(con=None) -> str:
    """Настройка TG_BOT_USERNAME → ответ getMe (спрашиваем один раз и запоминаем) → имя по умолчанию."""
    name = (llm.get("TG_BOT_USERNAME") or "").strip().lstrip("@")
    if name:
        return name
    if telegram.bot_token():
        try:
            from . import tgbot
            res = tgbot._deliver("getMe", {})
            got = ((res.get("result") or {}).get("username") or "").strip() if res.get("ok") else ""
            if got and con is not None:
                con.execute("DELETE FROM app_settings WHERE key='TG_BOT_USERNAME'")
                con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?)",
                            ("TG_BOT_USERNAME", got, db.now()))
            if got:
                return got
        except Exception:
            pass                    # сеть недоступна — покажем имя по умолчанию, код от этого не зависит
    return DEFAULT_BOT


def bot_link(code: str, con=None) -> str:
    return "https://t.me/%s?start=link_%s" % (bot_username(con), code)


# --------------------------------------------------------------------------- #
#  Выдача кода
# --------------------------------------------------------------------------- #

def _too_many(con, link_id: str, ip: str) -> bool:
    since = _ts(datetime.now() - timedelta(minutes=RATE_MINUTES))
    by_link = con.execute("SELECT COUNT(*) FROM tg_link_codes WHERE created_at >= ? AND link_id=?",
                          (since, link_id)).fetchone()[0]
    if by_link >= RATE_MAX:
        return True
    if not ip:
        return False
    by_ip = con.execute("SELECT COUNT(*) FROM tg_link_codes WHERE created_at >= ? AND ip=?",
                        (since, ip)).fetchone()[0]
    return by_ip >= RATE_MAX_IP


def start(con, link_id: str = "", ip: str = "") -> dict:
    """Новый одноразовый код. Возвращает и сам код — он показывается человеку и больше нигде не хранится."""
    link_id = clean_link_id(link_id) or new_link_id()
    if _too_many(con, link_id, ip):
        raise HTTPException(429, "Слишком много кодов подряд. Подождите %d минут и попробуйте снова"
                            % RATE_MINUTES)
    now = datetime.now()
    code = new_code()
    con.execute("INSERT INTO tg_link_codes (link_id, code_hash, created_at, expires_at, ip)"
                " VALUES (?,?,?,?,?)",
                (link_id, code_hash(con, code), _ts(now), _ts(now + timedelta(minutes=CODE_MINUTES)), ip or ""))
    # в журнал — ни кода, ни его отпечатка
    db.audit(con, "браузер", "запрошен код входа через Telegram", "tg-link", {"ip": ip or ""})
    return {"link_id": link_id, "code": code, "bot": "@" + bot_username(con), "link": bot_link(code, con),
            "expires_in": CODE_MINUTES * 60, "poll_seconds": 2}


# --------------------------------------------------------------------------- #
#  Привязка из чата бота
# --------------------------------------------------------------------------- #

def _guessing(con, tg_id: str) -> bool:
    """Защита от подбора: пять неверных кодов за окно от одного telegram_id — дальше не проверяем."""
    since = _ts(datetime.now() - timedelta(minutes=RATE_MINUTES))
    n = con.execute("SELECT COUNT(*) FROM tg_messages WHERE telegram_id=? AND kind='код входа'"
                    " AND ok=0 AND created_at >= ?", (str(tg_id), since)).fetchone()[0]
    return n >= GUESS_MAX


def bind(con, code: str, tg_user: dict) -> dict:
    """Код из чата бота → живая заявка получает telegram_id отправителя. Один код — один раз."""
    tg_id = str((tg_user or {}).get("id") or "").strip()
    if not tg_id.isdigit():
        return {"ok": False, "reason": "Telegram не передал идентификатор пользователя"}
    if _guessing(con, tg_id):
        return {"ok": False, "reason": "Слишком много неверных кодов. Попробуйте позже"}
    code = normalize_code(code)
    if not CODE_RE.fullmatch(code):
        return {"ok": False, "reason": "Это не похоже на код входа"}
    rows = db.rows(con, "SELECT * FROM tg_link_codes WHERE code_hash=? ORDER BY id DESC", code_hash(con, code))
    if not rows:
        return {"ok": False, "reason": "Код не найден. Откройте страницу входа и получите новый"}
    row = rows[0]
    if row["used_at"]:
        return {"ok": False, "reason": "Этот код уже использован. Получите новый на странице входа"}
    if row["expires_at"] < db.now():
        return {"ok": False, "reason": "Срок кода истёк. Получите новый на странице входа"}
    # храним только то, что нужно для входа: id, username и имя (link_or_request и remember_owner)
    keep = {k: (tg_user or {}).get(k) for k in ("id", "username", "first_name", "last_name")
            if (tg_user or {}).get(k) is not None}
    con.execute("UPDATE tg_link_codes SET telegram_id=?, tg_user_json=?, used_at=? WHERE id=? AND used_at IS NULL",
                (tg_id, json.dumps(keep, ensure_ascii=False), db.now(), row["id"]))
    db.audit(con, "tg" + tg_id, "код входа принят", "tg-link:%s" % row["id"], {"telegram_id": tg_id})
    return {"ok": True, "link_code_id": row["id"], "telegram_id": tg_id}


# --------------------------------------------------------------------------- #
#  Состояние ожидания
# --------------------------------------------------------------------------- #

def linked(con, link_id: str) -> Optional[dict]:
    """Привязанная заявка этого link_id, если привязка свежая (для регистрации без кода на телефон)."""
    link_id = clean_link_id(link_id)
    if not link_id:
        return None
    rows = db.rows(con, "SELECT * FROM tg_link_codes WHERE link_id=? AND used_at IS NOT NULL"
                        " ORDER BY id DESC LIMIT 1", link_id)
    if not rows:
        return None
    row = rows[0]
    if row["used_at"] < _ts(datetime.now() - timedelta(minutes=SESSION_MINUTES)):
        return None
    return row


def tg_user_of(row: dict) -> dict:
    try:
        return json.loads(row["tg_user_json"] or "{}")
    except (TypeError, ValueError):
        return {"id": row["telegram_id"]}


def status(con, link_id: str, ip: str = "", user_agent: str = "") -> dict:
    """Что с ожиданием: ждём, вошли (сессия и токен) или нужна регистрация."""
    link_id = clean_link_id(link_id)
    if not link_id:
        return {"status": ST_NONE, "reason": "Код входа не запрашивался"}
    rows = db.rows(con, "SELECT * FROM tg_link_codes WHERE link_id=? ORDER BY id DESC LIMIT 1", link_id)
    if not rows:
        return {"status": ST_NONE, "reason": "Код входа не запрашивался"}
    row = rows[0]
    if not row["used_at"]:
        if row["expires_at"] < db.now():
            return {"status": ST_EXPIRED, "link_id": link_id,
                    "reason": "Срок кода истёк — получите новый"}
        return {"status": ST_PENDING, "link_id": link_id}
    if row["used_at"] < _ts(datetime.now() - timedelta(minutes=SESSION_MINUTES)):
        return {"status": ST_EXPIRED, "link_id": link_id, "reason": "Вход не был завершён — получите новый код"}

    who = tg_user_of(row)
    try:                                   # чат владельца для служебных уведомлений — как в /tg/auth
        from . import tgbot
        tgbot.remember_owner(con, who)
    except Exception:
        pass
    out = telegram.link_or_request(con, who)
    if out["status"] == telegram.ST_OK:
        token, _ = auth.create_session(con, out["row"], ip=ip, user_agent=user_agent or "browser-tg-link")
        db.audit(con, out["row"]["login"], "вход по коду бота", "user:%s" % out["row"]["id"], None)
        return {"status": ST_DONE, "link_id": link_id, "token": token, "user": out["user"]}
    if out["status"] == telegram.ST_REG:
        return {"status": ST_REGISTER, "link_id": link_id, "reason": out.get("reason", ""),
                "telegram_id": out.get("telegram_id")}
    return {"status": telegram.ST_ERR, "link_id": link_id, "reason": out.get("reason", "")}


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #

class StartIn(BaseModel):
    link_id: str = ""


def _ip(request: Request) -> str:
    c = request.scope.get("client")
    return (c[0] if c else "") or ""


@router.post("/auth/tg-link/start")
def post_start(body: StartIn, request: Request):
    with db.tx() as con:
        return start(con, body.link_id, _ip(request))


@router.get("/auth/tg-link/status")
def get_status(request: Request, response: Response, link_id: str = ""):
    with db.tx() as con:
        out = status(con, link_id, _ip(request), "browser-tg-link")
    if out.get("token"):
        auth._set_cookie(response, out["token"])
    return out
