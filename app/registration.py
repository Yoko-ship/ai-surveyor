"""
Самостоятельная регистрация в мини-приложении по номеру телефона и коду из чата бота
и раздел «Пользователи» (кто есть в системе, кто админ).

Задача заказчика от 21.09.2026. Как это работает:

  1. Мини-приложение открывает незнакомый telegram_id → /tg/auth отвечает «нужна регистрация»
     (app/telegram.py), человек видит форму.
  2. POST /tg/register/send-code — человек вводит номер +998XXXXXXXXX, сервер кладёт в чат ЭТОГО ЖЕ
     telegram_id шестизначный код. Бот не может написать (человек не нажимал Start) — честная ошибка,
     а не «код отправлен».
  3. POST /tg/register/verify-code — проверка кода: 10 минут, 5 попыток, повторная отправка не чаще
     раза в минуту, не больше 5 кодов в сутки, после второй исчерпанной серии — пауза 15 минут
     (меры из раздела 7 docs/Регистрация и роли.md).
  3а. Вход из обычного браузера (app/tg_link.py) приходит сюда же, но с link_id вместо initData:
     шаги 2 и 3 пропускаются — Telegram подтверждён тем, что человек отправил код боту,
     и второй код, теперь на телефон, спрашивать не за чем.
  4. POST /tg/register/submit — анкета: ФИО, департамент, должность, согласия на обработку ПД.
     Пользователь становится активным СРАЗУ, роль «сотрудник»: расчёт, свои запросы, фото и документы,
     отправка на согласование. Подтверждение администратором по этому пути не требуется.

Персональные данные (правило проекта № 8):
  * номер телефона хранится ровно в одном месте — users.phone; в reg_codes лежит только его отпечаток,
    в audit и tg_messages номер не попадает, во внешнюю модель тексты с номером уходят замаскированными
    (app/llm.py mask_pd);
  * код хранится только отпечатком sha256(код + своя соль + telegram_id) — из базы его не прочитать;
  * текст согласия НЕ зашит в код: он берётся из docs/Регистрация и роли.md (app/tgbot.consent_text),
    отдаётся точкой GET /tg/consent, а версия и хэш показанного текста пишутся в pd_consents.

Тесты — tests/test_registration.py (поддельные initData и обновления Telegram, сеть не трогается).
"""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import auth, db, telegram, tgbot

router = APIRouter()

CODE_MINUTES = 10                 # сколько живёт код
CODE_MAX_ATTEMPTS = 5             # неверных попыток ввода
RESEND_SECONDS = 60               # не чаще одного раза в минуту
FORM_MINUTES = 30                 # столько есть на заполнение анкеты после верного кода
# Меры против перебора — раздел 7 docs/Регистрация и роли.md (оценка юриста от 21.09.2026):
DAILY_MAX_CODES = 5               # не больше пяти кодов в сутки на один telegram_id
BLOCK_MINUTES = 15                # пауза после второй подряд исчерпанной серии попыток
BLOCK_AFTER_SERIES = 2

ROLE = auth.ROLE_EMPLOYEE         # «сотрудник»
ADMIN = "админ"

# Должности — список заказчика (21.09.2026). «другое» открывает поле свободного текста.
POSITIONS = [
    "директор",
    "заместитель директора",
    "первый заместитель генерального директора",
    "член Совета директоров",
    "менеджер",
    "ассистент менеджера",
    "другое",
]
POSITION_OTHER = "другое"

PHONE_RE = re.compile(r"^\+998\d{9}$")

# текст — дословно из раздела 5.4 docs/Регистрация и роли.md
NEED_START = ("Бот не может отправить вам код: откройте чат с ботом и нажмите «Запустить» (Start), "
              "затем вернитесь сюда и нажмите «Получить код».")


# --------------------------------------------------------------------------- #
#  Вспомогательное
# --------------------------------------------------------------------------- #

def _digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def normalize_phone(raw: str) -> str:
    """+998 XX XXX-XX-XX в любом виде → +998XXXXXXXXX. Не тот формат — 422 с понятным текстом."""
    s = re.sub(r"[\s\-()]", "", (raw or "").strip())
    if s.startswith("998"):
        s = "+" + s
    if s.startswith("8") and len(s) == 13:        # 8998XXXXXXXXX — частая опечатка
        s = "+" + s[1:]
    if not PHONE_RE.fullmatch(s):
        raise HTTPException(422, "Номер телефона вводится в формате +998XXXXXXXXX")
    return s


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def tg_user(init_data: str) -> dict:
    """Кто открыл мини-приложение. Подпись Telegram проверяется целиком (app/telegram.py)."""
    token = telegram.bot_token()
    if not token:
        raise HTTPException(503, "Бот Telegram не подключён — регистрация по коду недоступна. "
                                 "Обратитесь к администратору системы")
    res = telegram.check_init_data(init_data, token)
    if not res["ok"]:
        raise HTTPException(401, res["reason"])
    u = res["user"] or {}
    tg_id = str(u.get("id") or "").strip()
    if not tg_id.isdigit():
        raise HTTPException(401, "Telegram не передал идентификатор пользователя")
    return {"telegram_id": tg_id, "user": u}


def who_registers(con, body) -> dict:
    """
    Кто заполняет анкету. Два равноправных пути подтверждения Telegram:
      * мини-приложение — подписанный initData (подпись проверяет app/telegram.py);
      * обычный браузер — код, отправленный боту (app/tg_link.py): Telegram подтверждён тем,
        что сообщение пришло от этого telegram_id, поэтому код на телефон здесь не нужен.
    """
    link_id = (getattr(body, "link_id", "") or "").strip()
    if link_id and not (getattr(body, "initData", "") or "").strip():
        from . import tg_link
        row = tg_link.linked(con, link_id)
        if not row:
            raise HTTPException(401, "Вход по коду бота не подтверждён — получите новый код "
                                     "на странице входа")
        return {"telegram_id": row["telegram_id"], "user": tg_link.tg_user_of(row), "via_link": True}
    return tg_user(getattr(body, "initData", "") or "") | {"via_link": False}


def _existing(con, tg_id: str) -> Optional[dict]:
    r = db.rows(con, "SELECT * FROM users WHERE telegram_id=?", tg_id)
    return r[0] if r else None


def _active_admins(con) -> list:
    return db.rows(con, "SELECT id, login FROM users WHERE role=? AND status=?", ADMIN, auth.STATUS_ACTIVE)


# --------------------------------------------------------------------------- #
#  Модели
# --------------------------------------------------------------------------- #

class PhoneIn(BaseModel):
    initData: str = ""
    phone: str = ""


class CodeIn(BaseModel):
    initData: str = ""
    phone: str = ""
    code: str = ""


class SubmitIn(BaseModel):
    initData: str = ""
    link_id: str = ""                    # вход из браузера по коду боту (app/tg_link.py)
    phone: str = ""
    code: str = ""                       # можно прислать код ещё раз — проверка та же
    full_name: str = ""
    department: str = ""
    position: str = ""
    position_other: str = ""             # если выбрано «другое»
    consent: bool = False                # согласие на обработку персональных данных
    consent_phone: bool = False          # отдельное согласие на номер телефона
    branch: str = ""


# --------------------------------------------------------------------------- #
#  Код подтверждения
# --------------------------------------------------------------------------- #

def _row(con, tg_id: str) -> Optional[dict]:
    r = db.rows(con, "SELECT * FROM reg_codes WHERE telegram_id=?", tg_id)
    return r[0] if r else None


def _parse(ts: str):
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def send_code(con, tg_id: str, phone: str) -> dict:
    """Кладёт код в чат этого же telegram_id. Ничего не придумывает: не ушло — так и говорит."""
    row = _row(con, tg_id)
    now = datetime.now()
    sent_total, window_start = 0, now
    if row:
        blocked = _parse(row["blocked_until"])
        if blocked and blocked > now:
            raise HTTPException(429, f"Слишком много неудачных попыток. Попробуйте через "
                                     f"{int((blocked - now).total_seconds() // 60) + 1} минут")
        since = (now - (_parse(row["created_at"]) or now)).total_seconds() if row["created_at"] else 999
        if since < RESEND_SECONDS:
            left = int(RESEND_SECONDS - since) + 1
            raise HTTPException(429, "Код можно запрашивать не чаще одного раза в минуту. "
                                     f"Новый код будет доступен через {left // 60:02d}:{left % 60:02d}.")
        window_start = _parse(row["first_sent_at"]) or now
        sent_total = row["sent_total"] or 0
        if (now - window_start).total_seconds() > 24 * 3600:     # сутки прошли — счётчик с нуля
            window_start, sent_total = now, 0
        elif sent_total >= DAILY_MAX_CODES:
            raise HTTPException(429, f"За сутки можно получить не больше {DAILY_MAX_CODES} кодов. "
                                     "Попробуйте завтра или обратитесь к администратору")

    code = f"{secrets.randbelow(10 ** 6):06d}"
    text = (f"Код для регистрации в приложении INSON: {code}\n"
            f"Код действует {CODE_MINUTES} минут. Никому его не сообщайте.")
    res = tgbot.send(tg_id, text, kind="код", con=con)
    if not res.get("ok"):
        reason = (res.get("reason") or "").lower()
        # человек не начинал диалог с ботом — Telegram отвечает «chat not found» / «bot can't initiate»
        if "chat not found" in reason or "can't initiate" in reason or "blocked" in reason \
                or "deactivated" in reason:
            raise HTTPException(409, NEED_START)
        raise HTTPException(502, "Код отправить не удалось: " + (res.get("reason") or "Telegram не ответил"))
    salt = secrets.token_hex(16)            # своя соль на каждый код: иначе 6 цифр перебираются по хэшу
    con.execute("DELETE FROM reg_codes WHERE telegram_id=?", (tg_id,))   # живым остаётся один код
    con.execute("INSERT INTO reg_codes (telegram_id, phone_hash, code_hash, salt, created_at, expires_at,"
                " attempts, verified_at, sent_total, first_sent_at, exhausted, blocked_until)"
                " VALUES (?,?,?,?,?,?,0,NULL,?,?,?,NULL)",
                (tg_id, _digest(phone), _digest(code + salt + tg_id), salt, _ts(now),
                 _ts(now + timedelta(minutes=CODE_MINUTES)), sent_total + 1, _ts(window_start),
                 (row["exhausted"] if row else 0) or 0))
    _warn_phone_owner(con, tg_id, phone)
    # в журнал — только факт и идентификатор чата: ни номера, ни кода
    db.audit(con, "tg" + tg_id, "код регистрации отправлен", "telegram:" + tg_id, None)
    return {"ok": True, "sent": True, "valid_minutes": CODE_MINUTES,
            "resend_after_sec": RESEND_SECONDS, "attempts_left": CODE_MAX_ATTEMPTS,
            "message": f"Код отправлен вам в чат с ботом. Он действует {CODE_MINUTES} минут"}


def _warn_phone_owner(con, tg_id: str, phone: str):
    """На уже зарегистрированный номер кто-то запрашивает код — владельцу уходит предупреждение
    (раздел 7 документа юриста). Номер в журнал не пишется, только факт события."""
    for u in db.rows(con, "SELECT id, telegram_id FROM users WHERE phone=?", phone):
        if u["telegram_id"] and str(u["telegram_id"]) != str(tg_id):
            tgbot.send(u["telegram_id"], "Внимание: кто-то запросил код регистрации на ваш номер телефона. "
                                         "Если это не вы — сообщите администратору системы.",
                       kind="уведомление", con=con, user_id=u["id"])


EXHAUSTED = "Попытки исчерпаны. Этот код больше не действует — запросите новый через минуту."


def check_code(con, tg_id: str, phone: str, code: str) -> dict:
    """Проверка кода. Возвращает строку reg_codes или бросает 4xx с понятной причиной."""
    row = _row(con, tg_id)
    if not row:
        raise HTTPException(400, "Код не запрашивался — нажмите «Получить код»")
    blocked = _parse(row["blocked_until"])
    if blocked and blocked > datetime.now():
        raise HTTPException(429, f"Слишком много неудачных попыток. Попробуйте через "
                                 f"{int((blocked - datetime.now()).total_seconds() // 60) + 1} минут")
    if row["expires_at"] < db.now():
        raise HTTPException(400, "Код истёк — запросите новый")
    if row["attempts"] >= CODE_MAX_ATTEMPTS:
        raise HTTPException(429, EXHAUSTED)
    if not hmac.compare_digest(row["phone_hash"], _digest(phone)):
        raise HTTPException(400, "Код отправлялся на другой номер — запросите код на этот номер заново")
    # сравнение постоянного времени и с индивидуальной солью (раздел 7 документа юриста)
    if not hmac.compare_digest(row["code_hash"], _digest((code or "").strip() + (row["salt"] or "") + tg_id)):
        left = CODE_MAX_ATTEMPTS - row["attempts"] - 1
        series = (row["exhausted"] or 0) + (1 if left <= 0 else 0)
        block = _ts(datetime.now() + timedelta(minutes=BLOCK_MINUTES)) \
            if series >= BLOCK_AFTER_SERIES and left <= 0 else row["blocked_until"]
        con.execute("UPDATE reg_codes SET attempts=attempts+1, exhausted=?, blocked_until=? WHERE id=?",
                    (series, block, row["id"]))
        # фиксируем попытку сразу: дальше мы бросаем ошибку, а незавершённая транзакция
        # откатилась бы вместе со счётчиком — и подбор кода стал бы бесконечным
        con.commit()
        if left <= 0:
            raise HTTPException(429, EXHAUSTED)
        raise HTTPException(400, f"Код не подошёл. Проверьте 6 цифр из последнего сообщения бота. "
                                 f"Осталось попыток: {left}.")
    # код принят: счётчики серии обнуляем, сама строка живёт до конца анкеты и удаляется в register()
    con.execute("UPDATE reg_codes SET verified_at=?, attempts=0, exhausted=0, blocked_until=NULL,"
                " expires_at=? WHERE id=?",
                (db.now(), _ts(datetime.now() + timedelta(minutes=FORM_MINUTES)), row["id"]))
    return {"ok": True, "verified": True, "form_minutes": FORM_MINUTES}


# --------------------------------------------------------------------------- #
#  Анкета
# --------------------------------------------------------------------------- #

# Проверки анкеты вынесены в отдельные функции: та же анкета заполняется и после входа
# через Google (app/google_auth.py). Требования к ФИО, департаменту, должности и согласию
# должны совпадать дословно — поэтому один источник правды, а не две копии.

def check_full_name(raw: str) -> str:
    """ФИО одной строкой: минимум фамилия и имя. Возвращает нормализованное значение."""
    full_name = " ".join((raw or "").split())
    if len(full_name) < 5 or " " not in full_name:
        raise HTTPException(422, "Укажите фамилию, имя и отчество полностью")
    return full_name


def check_department(raw: str) -> str:
    """Департамент — свободный текст с подсказками, но пустым быть не может."""
    department = " ".join((raw or "").split())[:120]
    if not department:
        raise HTTPException(422, "Укажите департамент")
    return department


def check_position(position: str, position_other: str = "") -> str:
    """Должность из списка; «другое» — свободный текст до 80 знаков."""
    pos = (position or "").strip()
    if pos == POSITION_OTHER:
        other = (position_other or "").strip()
        if not other:
            raise HTTPException(422, "Выбрано «другое» — напишите должность")
        return other[:80]
    if pos not in POSITIONS:
        raise HTTPException(422, "Должность выбирается из списка: " + ", ".join(POSITIONS))
    return pos


def check_consent(given: bool):
    """Без согласия на обработку персональных данных учётная запись не создаётся (ЗРУ-547)."""
    if not given:
        raise HTTPException(422, "Без согласия на обработку персональных данных регистрация невозможна")


def _position(data: SubmitIn) -> str:
    return check_position(data.position, data.position_other)


def _save_consents(con, uid: Optional[int], tg_id: str, data: SubmitIn):
    """Две отдельные записи: основное согласие и согласие на номер телефона (раздел 6 документа юриста)."""
    for scope, given in ((tgbot.CONSENT_MAIN, data.consent), (tgbot.CONSENT_PHONE, data.consent_phone)):
        if not given:
            continue
        c = tgbot.consent_text(scope)
        con.execute("INSERT INTO pd_consents (user_id, telegram_id, version, channel, created_at,"
                    " consent_text_hash, scope) VALUES (?,?,?,?,?,?,?)",
                    (uid, tg_id, c["version"], "мини-приложение", db.now(), c["hash"], scope))


def register(con, tg_id: str, data: SubmitIn, via_link: bool = False) -> dict:
    """Создаёт (или достраивает) пользователя и сразу делает его активным сотрудником.
    via_link — пришли из браузера по коду боту: Telegram уже подтверждён, второй код не спрашиваем."""
    phone = normalize_phone(data.phone)
    if not via_link:
        row = _row(con, tg_id)
        if not row or not row["verified_at"]:
            if not (data.code or "").strip():
                raise HTTPException(400, "Сначала подтвердите номер кодом из чата бота")
            check_code(con, tg_id, phone, data.code)
            row = _row(con, tg_id)
        if row["expires_at"] < db.now():
            raise HTTPException(400, "Подтверждение номера устарело — получите код заново")
        if not hmac.compare_digest(row["phone_hash"], _digest(phone)):
            raise HTTPException(400, "Номер не совпадает с подтверждённым — получите код на нужный номер")

    full_name = check_full_name(data.full_name)
    department = check_department(data.department)
    position = _position(data)
    check_consent(data.consent)
    if not data.consent_phone:
        raise HTTPException(422, "Номер телефона — способ подтверждения личности при регистрации: "
                                 "без отдельного согласия на его обработку зарегистрировать нельзя")

    existing = _existing(con, tg_id)
    if existing and existing["status"] == auth.STATUS_BLOCKED:
        raise HTTPException(403, "Доступ заблокирован администратором")
    if existing and existing["status"] == auth.STATUS_ACTIVE:
        raise HTTPException(409, "Вы уже зарегистрированы — закройте и откройте приложение заново")

    ts = db.now()
    if existing:
        # заявка, поданная по старому пути, достраивается до полноценной записи (роль не понижаем)
        role = existing["role"] if existing["role"] in (ADMIN, "андеррайтер", "актуарий") else ROLE
        con.execute("UPDATE users SET full_name=?, phone=?, role=?, department=?, position=?, branch=?,"
                    " status=?, approved_by=?, approved_at=? WHERE id=?",
                    (full_name, phone, role, department, position, (data.branch or existing["branch"] or ""),
                     auth.STATUS_ACTIVE, "самостоятельная регистрация", ts, existing["id"]))
        uid = existing["id"]
        login = existing["login"]
    else:
        login = "tg" + tg_id
        if db.rows(con, "SELECT 1 FROM users WHERE login=?", login):
            raise HTTPException(409, "Учётная запись с таким логином уже есть — обратитесь к администратору")
        pw_hash, salt = auth.hash_password(secrets.token_urlsafe(24))   # вход только через Telegram
        cur = con.execute(
            "INSERT INTO users (login, full_name, phone, role, branch, department, position, agent_eais_id,"
            " password_hash, salt, status, telegram_id, created_at, approved_by, approved_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (login, full_name, phone, ROLE, (data.branch or "").strip(), department, position, None,
             pw_hash, salt, auth.STATUS_ACTIVE, tg_id, ts, "самостоятельная регистрация", ts))
        uid = cur.lastrowid
        role = ROLE

    _save_consents(con, uid, tg_id, data)
    con.execute("UPDATE pd_consents SET user_id=? WHERE telegram_id=? AND user_id IS NULL", (uid, tg_id))
    con.execute("DELETE FROM reg_codes WHERE telegram_id=?", (tg_id,))
    tgbot.drop_dialog(con, tg_id)
    # в журнал — без ФИО, департамента и телефона (правило проекта № 8)
    db.audit(con, login, "самостоятельная регистрация", f"user:{uid}",
             {"telegram_id": tg_id, "role": role, "согласие": tgbot.consent_text()["version"]})
    tgbot.log(con, "in", "регистрация", tg_id, uid, True, None)
    tgbot.send(tg_id, f"Регистрация завершена. Вы вошли как {role}: доступны расчёт, свои запросы, "
                      f"загрузка фото и документов и отправка запроса на согласование.",
               kind="уведомление", con=con, user_id=uid)
    tgbot.notify_admins_new_user(con, uid, role)
    return {"user_id": uid, "login": login, "role": role, "status": auth.STATUS_ACTIVE}


# --------------------------------------------------------------------------- #
#  API регистрации
# --------------------------------------------------------------------------- #

@router.get("/tg/consent")
def get_consent(scope: str = tgbot.CONSENT_MAIN):
    """Текст согласия и его версия — ровно тот, что утвердил юрист (docs/Регистрация и роли.md).
    В коде текста нет: версия и хэш показанного текста пишутся в pd_consents."""
    if scope not in (tgbot.CONSENT_MAIN, tgbot.CONSENT_PHONE):
        raise HTTPException(422, "scope: «основное» или «телефон»")
    return tgbot.consent_text(scope)


@router.get("/tg/register/positions")
def get_positions():
    """Список должностей для выпадающего списка. «другое» — открывает поле свободного текста."""
    return {"items": POSITIONS, "other": POSITION_OTHER}


@router.get("/tg/register/departments")
def get_departments():
    """Подсказки для поля «департамент»: уже введённые значения, по убыванию частоты."""
    with db.tx() as con:
        rows = db.rows(con, "SELECT department, COUNT(*) n FROM users"
                            " WHERE department IS NOT NULL AND department<>''"
                            " GROUP BY department ORDER BY n DESC, department")
    return {"items": [r["department"] for r in rows]}


@router.post("/tg/register/send-code")
def post_send_code(body: PhoneIn):
    who = tg_user(body.initData)
    phone = normalize_phone(body.phone)
    with db.tx() as con:
        return send_code(con, who["telegram_id"], phone)


@router.post("/tg/register/verify-code")
def post_verify_code(body: CodeIn):
    who = tg_user(body.initData)
    phone = normalize_phone(body.phone)
    with db.tx() as con:
        return check_code(con, who["telegram_id"], phone, body.code)


@router.post("/tg/register/submit")
def post_submit(body: SubmitIn, request: Request, response: Response):
    """Анкета. После неё человек СРАЗУ активен: ждать подтверждения администратора не нужно."""
    with db.tx() as con:
        who = who_registers(con, body)
        out = register(con, who["telegram_id"], body, via_link=who["via_link"])
        u = db.rows(con, "SELECT * FROM users WHERE id=?", out["user_id"])[0]
        token, _ = auth.create_session(con, u, ip=(request.client.host if request.client else ""),
                                       user_agent="telegram-mini-app")
        auth._set_cookie(response, token)
    # токен заголовком: встроенный браузер Telegram может не хранить cookie
    return out | {"token": token, "user": auth._public(u)}


# --------------------------------------------------------------------------- #
#  Раздел «Пользователи»
# --------------------------------------------------------------------------- #

def people(con) -> list:
    out = []
    for u in db.rows(con, "SELECT * FROM users ORDER BY full_name, login"):
        out.append({"id": u["id"], "login": u["login"], "full_name": u["full_name"],
                    "department": u.get("department") or "", "position": u.get("position") or "",
                    "phone": u.get("phone") or "", "role": u["role"], "status": u["status"],
                    "is_admin": u["role"] == ADMIN, "branch": u.get("branch") or "",
                    "telegram": bool(u.get("telegram_id"))})
    return out


@router.get("/tg/users")
def get_users(user: dict = Depends(auth.current_user)):
    """Список видят все зарегистрированные (решение заказчика 21.09.2026): ФИО, департамент,
    должность, телефон, отметка «админ». Кнопки управления — только у админа."""
    with db.tx() as con:
        items = people(con)
    return {"can_manage": user["role"] == ADMIN, "count": len(items), "items": items}


def set_admin(con, actor: dict, uid: int, make: bool) -> dict:
    u = db.rows(con, "SELECT * FROM users WHERE id=?", uid)
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    u = u[0]
    if make and u["status"] != auth.STATUS_ACTIVE:
        raise HTTPException(422, "Администратором можно сделать только действующего работника")
    if not make:
        others = [a for a in _active_admins(con) if a["id"] != uid]
        if u["role"] == ADMIN and not others:
            # снять последнего админа нельзя: управлять системой станет некому
            raise HTTPException(422, "Это единственный администратор — сначала назначьте другого")
    was = u["role"]
    role = ADMIN if make else ROLE
    if was == role:
        return {"ok": True, "user_id": uid, "role": role, "changed": False}
    con.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
    # в журнал — только логин и событие: ни ФИО, ни телефона (правило проекта № 8)
    db.audit(con, actor["login"], "назначен администратором" if make else "снят с администраторов",
             f"user:{uid}", {"login": u["login"], "было": was, "стало": role})
    if u.get("telegram_id"):
        tgbot.send(u["telegram_id"],
                   "Вам выданы права администратора: решения по запросам, пользователи и генеральные соглашения."
                   if make else "Права администратора сняты. Расчёт и свои запросы остаются доступны.",
                   kind="уведомление", con=con, user_id=uid)
    return {"ok": True, "user_id": uid, "role": role, "changed": True, "was": was}


@router.post("/tg/users/{uid}/make-admin")
def post_make_admin(uid: int, user: dict = Depends(auth.require(ADMIN))):
    with db.tx() as con:
        return set_admin(con, user, uid, True)


@router.post("/tg/users/{uid}/revoke-admin")
def post_revoke_admin(uid: int, user: dict = Depends(auth.require(ADMIN))):
    with db.tx() as con:
        return set_admin(con, user, uid, False)
