"""
Один профиль — несколько способов входа (задача заказчика 23.09.2026, «как на daykon.uz»).

Идея заказчика дословно: в профиле видно «Telegram — @username · ID 7230524397» и «Google — не
привязан», рядом кнопка «Привязать Google»; со второго устройства человек входит другим способом
и попадает В ТОТ ЖЕ профиль, а не заводит новый.

Что здесь:
  * таблица login_links (db/schema.sql) — список способов входа одного профиля. Перенос старых
    привязок из users.telegram_id и users.google_sub делает db.backfill_login_links() при запуске.
    Столбцы users остаются зеркалом: на них опирается старый код входа (app/telegram.py и др.).
  * GET  /auth/links                    — что привязано и что можно привязать;
  * POST /auth/link/google/start        — адрес Google для привязки (state помечен «привязка»);
  * POST /auth/link/telegram/start      — код боту для привязки (тот же механизм app/tg_link.py);
  * DELETE /auth/link/{provider}        — отвязать, но не последний способ (иначе вход потерян);
  * «лёгкая регистрация»: гость нажал «Вход для администратора», вошёл Telegram или Google,
    профиля нет — создаётся «сотрудник» БЕЗ анкеты (имя берётся из Telegram или Google,
    департамент и должность остаются пустыми, их можно заполнить позже);
  * запрос доступа в админку: POST/GET /auth/admin-request. Админом никто не становится сам —
    уточнение заказчика 23.09.2026: «только мне придёт запрос, и я подтверждаю». Владельцу
    (TG_ADMIN_USERNAME / TG_OWNER_CHAT_ID) уходит сообщение с кнопками «Подтвердить»/«Отклонить»
    (разбор нажатия — app/tgbot.handle_callback, право проверяется по telegram_id нажавшего).

Персональные данные (правило проекта № 8): в журнал (db.audit) не идут ни почта целиком, ни имя —
только провайдер и маска вида «i***v@insuranceon.uz» либо «•••4397». Сам адрес хранится один раз,
в users.email, и показывается только владельцу профиля.

Тесты — tests/test_login_links.py (сеть не трогается, работа на копии базы).
"""
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import auth, db, llm

router = APIRouter()

TELEGRAM, GOOGLE = "telegram", "google"
PROVIDERS = (TELEGRAM, GOOGLE)
TITLES = {TELEGRAM: "Telegram", GOOGLE: "Google"}

ROLE_EMPLOYEE = auth.ROLE_EMPLOYEE          # «сотрудник» — роль нового профиля, прав админа нет
ADMIN = "админ"

ST_WAIT, ST_OK, ST_NO = "ожидает", "подтверждён", "отклонён"

TAKEN = {GOOGLE: "Эта почта привязана к другому профилю",
         TELEGRAM: "Этот Telegram привязан к другому профилю"}
ALREADY = {GOOGLE: "Google уже привязан к вашему профилю",
           TELEGRAM: "Telegram уже привязан к вашему профилю"}
LAST_ONE = ("Это единственный способ входа — если отвязать его, вы не сможете войти. "
            "Сначала привяжите второй способ")


# --------------------------------------------------------------------------- #
#  Мелочи
# --------------------------------------------------------------------------- #

def check_provider(provider: str) -> str:
    p = (provider or "").strip().lower()
    if p not in PROVIDERS:
        raise HTTPException(404, "Такого способа входа нет: только Telegram и Google")
    return p


def mask_external(provider: str, external_id: str) -> str:
    """Для журнала и для чужих глаз: «•••4397». Целиком идентификатор наружу не отдаём."""
    v = str(external_id or "")
    tail = v[-4:] if len(v) > 4 else v
    return ("ID •••" if provider == TELEGRAM else "•••") + tail


def mask_display(provider: str, display: str, external_id: str = "") -> str:
    """Маска для журнала: почта — «i***v@домен», Telegram — «@username» (это публичный ник)."""
    d = (display or "").strip()
    if provider == GOOGLE:
        from . import google_auth           # локально: google_auth сам обращается сюда
        return google_auth.mask_email(d) if "@" in d else mask_external(provider, external_id)
    return d or mask_external(provider, external_id)


def own_display(provider: str, row: dict) -> str:
    """Что видит сам владелец профиля: «@username · ID 7230524397» или почта целиком."""
    d = (row.get("display") or "").strip()
    if provider == TELEGRAM:
        ident = "ID " + str(row.get("external_id") or "")
        return (d + " · " + ident) if d else ident
    return d


def tg_display(tg_user: dict) -> str:
    """«@username», если Telegram его прислал. Имени и фамилии в display не держим — это ПД."""
    u = ((tg_user or {}).get("username") or "").strip()
    return ("@" + u) if u else ""


def tg_name(tg_user: dict) -> str:
    parts = [((tg_user or {}).get("first_name") or "").strip(),
             ((tg_user or {}).get("last_name") or "").strip()]
    return " ".join(p for p in parts if p) or ("Пользователь Telegram " + str((tg_user or {}).get("id") or ""))


# --------------------------------------------------------------------------- #
#  Чтение
# --------------------------------------------------------------------------- #

def rows_of(con, user_id: int) -> list:
    return db.rows(con, "SELECT * FROM login_links WHERE user_id=? ORDER BY linked_at, id", user_id)


def by_external(con, provider: str, external_id: str) -> Optional[dict]:
    if not external_id:
        return None
    r = db.rows(con, "SELECT * FROM login_links WHERE provider=? AND external_id=?",
                provider, str(external_id))
    return r[0] if r else None


def user_by_link(con, provider: str, external_id: str) -> Optional[dict]:
    """
    Профиль по способу входа. Сначала login_links, потом — зеркало в users: база, в которую
    ещё не доехал перенос (db.backfill_login_links), не должна терять вход.
    """
    link = by_external(con, provider, external_id)
    if link:
        u = db.rows(con, "SELECT * FROM users WHERE id=?", link["user_id"])
        if u:
            return u[0]
    column = "telegram_id" if provider == TELEGRAM else "google_sub"
    u = db.rows(con, f"SELECT * FROM users WHERE {column}=?", str(external_id))
    return u[0] if u else None


def public(row: dict) -> dict:
    p = row["provider"]
    return {"provider": p, "title": TITLES.get(p, p), "display": own_display(p, row),
            "external_id_masked": mask_external(p, row.get("external_id")),
            "linked_at": row.get("linked_at"), "last_login_at": row.get("last_login_at")}


def links_payload(con, user: dict) -> dict:
    items = rows_of(con, user["id"])
    have = {r["provider"] for r in items}
    from . import google_auth
    ready = google_auth.configured()
    return {"links": [public(r) for r in items],
            "can_link": [p for p in PROVIDERS if p not in have],
            # отвязать можно, только если останется хотя бы один способ войти
            "can_unlink": [r["provider"] for r in items] if len(items) > 1 else [],
            "google": {"configured": ready,
                       "message": "" if ready else google_auth.NOT_CONFIGURED,
                       "hint": "" if ready else google_auth.HINT},
            "profile": {"id": user["id"], "name": user.get("full_name") or "",
                        "department": user.get("department") or "",
                        "position": user.get("position") or "",
                        "role": user.get("role"), "is_admin": user.get("role") == ADMIN}}


# --------------------------------------------------------------------------- #
#  Привязка и отвязка
# --------------------------------------------------------------------------- #

def _mirror(con, user_id: int, provider: str, external_id: str, display: str):
    """users.telegram_id / users.google_sub — зеркало для старого кода входа."""
    if provider == TELEGRAM:
        con.execute("UPDATE users SET telegram_id=? WHERE id=?", (str(external_id), user_id))
    else:
        email = display if "@" in (display or "") else None
        con.execute("UPDATE users SET google_sub=?, email=COALESCE(?, email) WHERE id=?",
                    (str(external_id), email, user_id))


def attach(con, user: dict, provider: str, external_id: str, display: str = "",
           actor: str = "") -> dict:
    """
    Добавляет способ входа к профилю. Чужой аккаунт — 409 (молча перевешивать вход нельзя:
    это увело бы человека в чужой профиль).
    """
    provider = check_provider(provider)
    external_id = str(external_id or "").strip()
    if not external_id:
        raise HTTPException(422, "Провайдер не передал идентификатор аккаунта")
    taken = by_external(con, provider, external_id)
    if taken and taken["user_id"] != user["id"]:
        db.audit(con, actor or user["login"], "привязка отклонена: аккаунт занят", "user:%s" % user["id"],
                 {"способ": provider, "аккаунт": mask_display(provider, display, external_id)})
        raise HTTPException(409, TAKEN[provider])
    if taken:                                   # тот же аккаунт того же профиля — просто обновим показ
        con.execute("UPDATE login_links SET display=COALESCE(NULLIF(?,''), display) WHERE id=?",
                    (display, taken["id"]))
        _mirror(con, user["id"], provider, external_id, display)
        return {"ok": True, "provider": provider, "changed": False}
    mine = [r for r in rows_of(con, user["id"]) if r["provider"] == provider]
    if mine:
        raise HTTPException(409, ALREADY[provider])
    con.execute("INSERT INTO login_links (user_id, provider, external_id, display, linked_at)"
                " VALUES (?,?,?,?,?)", (user["id"], provider, external_id, display or None, db.now()))
    _mirror(con, user["id"], provider, external_id, display)
    db.audit(con, actor or user["login"], "привязан способ входа", "user:%s" % user["id"],
             {"способ": provider, "аккаунт": mask_display(provider, display, external_id)})
    return {"ok": True, "provider": provider, "changed": True}


def ensure(con, user: dict, provider: str, external_id: str, display: str = "") -> dict:
    """
    Тихо доводит список способов входа в момент входа (профиль мог быть заведён до login_links).
    Отказ здесь не должен ломать вход: пишем в печать сервера и идём дальше.
    """
    try:
        return attach(con, user, provider, external_id, display)
    except HTTPException as e:
        print("login_links: способ входа не записан:", e.detail)
        return {"ok": False, "provider": provider, "reason": e.detail}


def detach(con, user: dict, provider: str) -> dict:
    provider = check_provider(provider)
    items = rows_of(con, user["id"])
    mine = [r for r in items if r["provider"] == provider]
    if not mine:
        raise HTTPException(404, "Этот способ входа не привязан")
    if len(items) <= 1:
        raise HTTPException(422, LAST_ONE)
    con.execute("DELETE FROM login_links WHERE id=?", (mine[0]["id"],))
    if provider == TELEGRAM:
        con.execute("UPDATE users SET telegram_id=NULL WHERE id=?", (user["id"],))
    else:
        con.execute("UPDATE users SET google_sub=NULL WHERE id=?", (user["id"],))
    db.audit(con, user["login"], "отвязан способ входа", "user:%s" % user["id"],
             {"способ": provider,
              "аккаунт": mask_display(provider, mine[0].get("display"), mine[0].get("external_id"))})
    return {"ok": True, "provider": provider, "left": [r["provider"] for r in items if r["id"] != mine[0]["id"]]}


def touch(con, user_id: int, provider: str, external_id: str):
    """Отметка «этим способом сейчас вошли» — для показа в профиле."""
    con.execute("UPDATE login_links SET last_login_at=? WHERE provider=? AND external_id=?",
                (db.now(), provider, str(external_id)))


# --------------------------------------------------------------------------- #
#  Лёгкая регистрация: один клик, без анкеты
# --------------------------------------------------------------------------- #

def _free_login(con, base: str) -> str:
    login = base
    n = 1
    while db.rows(con, "SELECT 1 FROM users WHERE login=?", login):
        n += 1
        login = f"{base}-{n}"
    return login


def create_employee(con, provider: str, external_id: str, display: str = "", name: str = "",
                    email: str = "") -> dict:
    """
    Новый профиль в один клик: роль «сотрудник», права админа не выдаются.
    Анкеты нет — имя берём у провайдера, департамент и должность остаются пустыми
    (их человек при желании заполнит в профиле).
    """
    provider = check_provider(provider)
    external_id = str(external_id or "").strip()
    base = ("tg" + external_id) if provider == TELEGRAM else ("g" + external_id[-12:])
    login = _free_login(con, base.lower())
    pw_hash, salt = auth.hash_password(secrets.token_urlsafe(24))   # вход только через провайдера
    ts = db.now()
    cur = con.execute(
        "INSERT INTO users (login, full_name, phone, role, branch, agent_eais_id, password_hash, salt,"
        " status, telegram_id, google_sub, email, created_at, approved_by, approved_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (login, (name or "").strip() or login, "", ROLE_EMPLOYEE, "", None, pw_hash, salt,
         auth.STATUS_ACTIVE,
         external_id if provider == TELEGRAM else None,
         external_id if provider == GOOGLE else None,
         (email or (display if "@" in (display or "") else "")) or None,
         ts, "вход " + TITLES[provider], ts))
    uid = cur.lastrowid
    con.execute("INSERT INTO login_links (user_id, provider, external_id, display, linked_at)"
                " VALUES (?,?,?,?,?)", (uid, provider, external_id, display or None, ts))
    # в журнал — ни имени, ни почты целиком (правило проекта № 8)
    db.audit(con, login, "новый профиль одним кликом", "user:%s" % uid,
             {"способ": provider, "аккаунт": mask_display(provider, display, external_id),
              "роль": ROLE_EMPLOYEE})
    return db.rows(con, "SELECT * FROM users WHERE id=?", uid)[0]


def login_or_create(con, provider: str, external_id: str, display: str = "", name: str = "",
                    email: str = "") -> dict:
    """
    Вход «для администратора» из браузера: известный аккаунт — тот же профиль, незнакомый —
    новый «сотрудник» без анкеты. Возвращает {"user": строка users, "created": bool}.
    """
    u = user_by_link(con, provider, external_id)
    if u:
        if u["status"] == auth.STATUS_BLOCKED:
            raise HTTPException(403, "Доступ заблокирован администратором")
        if u["status"] != auth.STATUS_ACTIVE:
            con.execute("UPDATE users SET status=? WHERE id=?", (auth.STATUS_ACTIVE, u["id"]))
            u = db.rows(con, "SELECT * FROM users WHERE id=?", u["id"])[0]
        # заодно доводим список способов входа: профиль мог быть заведён до login_links
        ensure(con, u, provider, external_id, display)
        touch(con, u["id"], provider, external_id)
        return {"user": u, "created": False}
    u = create_employee(con, provider, external_id, display, name, email)
    touch(con, u["id"], provider, external_id)
    return {"user": u, "created": True}


# --------------------------------------------------------------------------- #
#  Владелец и запросы доступа в админку
# --------------------------------------------------------------------------- #

def owner(con) -> Optional[dict]:
    """
    Кто подтверждает доступ в админку. По порядку: чат владельца (TG_OWNER_CHAT_ID),
    username владельца (TG_ADMIN_USERNAME), иначе самый первый действующий администратор.
    """
    chat = (llm.get("TG_OWNER_CHAT_ID") or "").strip()
    if chat:
        r = db.rows(con, "SELECT * FROM users WHERE telegram_id=? AND status=?", chat, auth.STATUS_ACTIVE)
        if r:
            return r[0]
    uname = (llm.get("TG_ADMIN_USERNAME") or "").strip().lstrip("@").lower()
    if uname:
        r = db.rows(con, "SELECT u.* FROM users u JOIN login_links l ON l.user_id=u.id"
                         " WHERE l.provider=? AND LOWER(l.display)=? AND u.status=?",
                    TELEGRAM, "@" + uname, auth.STATUS_ACTIVE)
        if r:
            return r[0]
    r = db.rows(con, "SELECT * FROM users WHERE role=? AND status=? ORDER BY id LIMIT 1",
                ADMIN, auth.STATUS_ACTIVE)
    return r[0] if r else None


def is_owner(con, user: dict) -> bool:
    o = owner(con)
    return bool(o and user and o["id"] == user.get("id"))


def owner_chat(con) -> str:
    """Куда писать: сохранённый чат владельца или его telegram_id из профиля."""
    chat = (llm.get("TG_OWNER_CHAT_ID") or "").strip()
    if chat:
        return chat
    o = owner(con)
    return str((o or {}).get("telegram_id") or "")


def my_request(con, user_id: int) -> Optional[dict]:
    r = db.rows(con, "SELECT * FROM admin_requests WHERE user_id=? ORDER BY id DESC LIMIT 1", user_id)
    return r[0] if r else None


def pending_requests(con) -> list:
    out = []
    for r in db.rows(con, "SELECT r.*, u.full_name, u.login, u.email FROM admin_requests r"
                          " JOIN users u ON u.id=r.user_id WHERE r.status=? ORDER BY r.id", ST_WAIT):
        links = rows_of(con, r["user_id"])
        out.append({"id": r["id"], "user_id": r["user_id"], "name": r.get("full_name") or r["login"],
                    "created_at": r["created_at"],
                    "ways": [{"provider": l["provider"], "display": own_display(l["provider"], l)}
                             for l in links]})
    return out


def request_card(con, user: dict) -> str:
    """Текст сообщения владельцу. Имя и способ входа — это сообщение лично владельцу, не журнал."""
    ways = " · ".join(own_display(l["provider"], l) for l in rows_of(con, user["id"])) or "без привязок"
    return ("Запрос доступа в админку\n"
            f"{user.get('full_name') or user['login']}\n"
            f"{ways}\n"
            f"Когда: {db.now().replace('T', ' ')}")


def ask_admin(con, user: dict) -> dict:
    """Человек просит доступ в админку. Повторный запрос не дублируется."""
    if user.get("role") == ADMIN:
        return {"status": ST_OK, "already_admin": True, "request_id": None}
    have = db.rows(con, "SELECT * FROM admin_requests WHERE user_id=? AND status=?", user["id"], ST_WAIT)
    if have:
        return {"status": ST_WAIT, "already_admin": False, "request_id": have[0]["id"], "created": False}
    cur = con.execute("INSERT INTO admin_requests (user_id, status, created_at) VALUES (?,?,?)",
                      (user["id"], ST_WAIT, db.now()))
    rid = cur.lastrowid
    db.audit(con, user["login"], "запрошен доступ в админку", "user:%s" % user["id"], {"запрос": rid})
    notify_owner(con, rid, user)
    return {"status": ST_WAIT, "already_admin": False, "request_id": rid, "created": True}


def notify_owner(con, rid: int, user: dict):
    """Сообщение владельцу с кнопками. Бот не подключён — запрос всё равно ждёт в «Пользователях»."""
    try:
        from . import tgbot
        chat = owner_chat(con)
        if not chat:
            return
        buttons = tgbot.kb([[("Подтвердить", f"adminreq:{rid}:ok"), ("Отклонить", f"adminreq:{rid}:no")]])
        tgbot.send(chat, request_card(con, user), buttons, kind="запрос доступа", con=con,
                   user_id=user["id"])
    except Exception as e:                       # доставка не должна ронять запрос
        print("login_links: владельцу не отправлено:", e)


def decide_admin(con, actor: dict, rid: int, approve: bool, note: str = "") -> dict:
    """Решение владельца. Роль «админ» выдаётся только здесь и только владельцем."""
    r = db.rows(con, "SELECT * FROM admin_requests WHERE id=?", rid)
    if not r:
        raise HTTPException(404, "Запрос не найден")
    r = r[0]
    if r["status"] != ST_WAIT:
        return {"ok": True, "request_id": rid, "status": r["status"], "changed": False}
    target = db.rows(con, "SELECT * FROM users WHERE id=?", r["user_id"])
    if not target:
        raise HTTPException(404, "Человек не найден")
    target = target[0]
    status = ST_OK if approve else ST_NO
    con.execute("UPDATE admin_requests SET status=?, decided_at=?, decided_by=?, note=? WHERE id=?",
                (status, db.now(), actor["login"], note or None, rid))
    if approve:
        con.execute("UPDATE users SET role=? WHERE id=?", (ADMIN, target["id"]))
    db.audit(con, actor["login"], "доступ в админку подтверждён" if approve else "доступ в админку отклонён",
             "user:%s" % target["id"], {"запрос": rid, "было": target["role"]})
    try:
        from . import tgbot
        if target.get("telegram_id"):
            tgbot.send(target["telegram_id"],
                       "Доступ в админку открыт. Откройте приложение — появится раздел «Настройки»."
                       if approve else "Доступ в админку не открыт.",
                       kind="уведомление", con=con, user_id=target["id"])
    except Exception as e:
        print("login_links: уведомление не отправлено:", e)
    return {"ok": True, "request_id": rid, "status": status, "changed": True, "user_id": target["id"]}


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #

def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.get("/auth/links")
def get_links(user: dict = Depends(auth.current_user)):
    """Способы входа своего профиля: что привязано, что можно привязать и что можно отвязать."""
    with db.tx() as con:
        return links_payload(con, user)


@router.post("/auth/link/google/start")
def post_link_google(request: Request, user: dict = Depends(auth.current_user)):
    """Адрес Google для привязки к текущему профилю. state помечен «привязка» — войти по нему нельзя."""
    from . import google_auth
    if not google_auth.configured():
        raise HTTPException(503, google_auth.NOT_CONFIGURED)
    with db.tx() as con:
        out = google_auth.start_login(con, "/tg", _ip(request), purpose=google_auth.P_LINK,
                                      link_user_id=user["id"])
    return {"url": out["url"], "state": out["state"]}


@router.post("/auth/link/telegram/start")
def post_link_telegram(request: Request, user: dict = Depends(auth.current_user)):
    """Код боту для привязки Telegram к текущему профилю (тот же механизм, что при входе)."""
    from . import tg_link
    with db.tx() as con:
        out = tg_link.start(con, "", _ip(request), purpose=tg_link.P_LINK, link_user_id=user["id"])
    return out


@router.delete("/auth/link/{provider}")
def delete_link(provider: str, user: dict = Depends(auth.current_user)):
    with db.tx() as con:
        return detach(con, user, provider)


class AdminRequestIn(BaseModel):
    initData: str = ""         # мини-приложение: подписанные данные Telegram (у гостя сессии ещё нет)
    comment: str = ""


@router.post("/auth/admin-request")
def post_admin_request(body: AdminRequestIn, request: Request, response: Response):
    """
    «Запросить доступ в админку»: создаётся запрос владельцу, роль НЕ меняется.
    Повторное нажатие нового запроса не создаёт.

    Гость мини-приложения сессии ещё не имеет, зато у него есть подписанный initData: подпись
    проверяется здесь же (тот же порядок, что у регистрации), профиль «сотрудник» заводится одним
    кликом, и уже от его имени уходит запрос владельцу.
    """
    user = auth.optional_user(request)
    with db.tx() as con:
        if not user:
            if not (body.initData or "").strip():
                raise HTTPException(401, "Нужно войти: откройте приложение в Telegram или войдите на /login")
            from . import telegram
            res = telegram.check_init_data(body.initData, telegram.bot_token())
            if not res["ok"] or not res["user"]:
                raise HTTPException(401, res.get("reason") or "Telegram не подтвердил, кто вы")
            tg_id = str((res["user"] or {}).get("id") or "")
            made = login_or_create(con, TELEGRAM, tg_id, tg_display(res["user"]), tg_name(res["user"]))
            user = made["user"]
            token, _ = auth.create_session(con, user, _ip(request), "telegram-mini-app")
            auth.set_session_cookie(response, token)
            out = ask_admin(con, user)
            # токен заголовком: встроенный браузер Telegram может не хранить cookie
            return out | {"token": token, "user": auth._public(user)}
        return ask_admin(con, user)


@router.get("/auth/admin-request")
def get_admin_request(user: dict = Depends(auth.current_user)):
    """Свой статус; владельцу дополнительно — список ожидающих запросов."""
    with db.tx() as con:
        mine = my_request(con, user["id"])
        out = {"status": (mine or {}).get("status") or "нет",
               "request_id": (mine or {}).get("id"),
               "is_admin": user.get("role") == ADMIN,
               "is_owner": is_owner(con, user)}
        if out["is_owner"]:
            out["pending"] = pending_requests(con)
        return out


@router.post("/auth/admin-request/{rid}/approve")
def post_approve(rid: int, user: dict = Depends(auth.current_user)):
    with db.tx() as con:
        if not is_owner(con, user):
            raise HTTPException(403, "Доступ в админку открывает владелец")
        return decide_admin(con, user, rid, True)


@router.post("/auth/admin-request/{rid}/reject")
def post_reject(rid: int, user: dict = Depends(auth.current_user)):
    with db.tx() as con:
        if not is_owner(con, user):
            raise HTTPException(403, "Доступ в админку открывает владелец")
        return decide_admin(con, user, rid, False)
