"""
Один профиль — несколько способов входа (app/login_links.py, задача заказчика 23.09.2026).

Запуск из корня проекта (pytest и httpx в sandbox\\.venv не установлены — свой ASGI-клиент,
живой сервер не трогаем и не перезапускаем):
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_login_links.py

Сеть не используется: Google и Telegram подменены заглушками. Вся работа идёт на КОПИИ рабочей
базы (tests/tmpdb.py) — рабочая база не открывается вообще.

Что проверяется:
   1  миграция: старые привязки из users.telegram_id и users.google_sub переезжают в login_links,
      повторный запуск ничего не дублирует;
   2  один профиль: вход через Telegram и через Google ведёт в ОДНУ учётную запись;
   3  конфликт: аккаунт, уже привязанный к другому профилю, — 409 и понятный текст;
   4  нельзя отвязать последний способ входа (иначе войти станет нечем);
   5  гость → сотрудник одним кликом: вход со страницы администратора, без анкеты, без прав админа;
   6  привязка Google к уже открытому профилю: два способа входа у одного профиля;
   7  в журнале только маска почты — самой почты и имени там нет;
   8  запрос доступа в админку: создаётся один, чужое нажатие в боте — отказ, владелец — роль «админ»;
   9  контракт для дизайнера: GET /auth/links отдаёт links / can_link / can_unlink;
  10  белый список единого входа: открыт ровно POST /auth/admin-request, привязки — только по сессии.
"""
import asyncio
import hashlib
import hmac
import json as _json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SURVEYOR_DEV", "1")     # тест ходит в приложение с адреса 127.0.0.1

from tmpdb import temp_db                                                      # noqa: E402
from fastapi import HTTPException                                              # noqa: E402
from app import auth, db, google_auth, guard, llm, login_links, tg_link, tgbot  # noqa: E402
from app.main import app                                                       # noqa: E402

BOT_TOKEN = "123456:TEST-LOGIN-LINKS"          # вымышленный токен: сеть не трогаем
CLIENT_ID = "test-client.apps.googleusercontent.com"

TG_OLD, TG_NEW, TG_OWNER, TG_STRANGER = "9930001", "9930002", "9930003", "9930004"
SUB_OLD, SUB_NEW, SUB_FREE = "тест-sub-900001", "тест-sub-900002", "тест-sub-900003"
EMAIL_OLD, EMAIL_NEW, EMAIL_FREE = ("staryi@insuranceon.uz", "novyi@insuranceon.uz",
                                    "svobodnyi@insuranceon.uz")
LOGIN_OLD, LOGIN_SECOND, LOGIN_OWNER = "тест-связка-старый", "тест-связка-второй", "тест-связка-владелец"

SETTINGS = {"SERVER_URL": "http://127.0.0.1:8000", "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
            "TG_BOT_USERNAME": "inson_surveyor_bot", "GOOGLE_CLIENT_ID": CLIENT_ID,
            "GOOGLE_CLIENT_SECRET": "test-secret", "TG_OWNER_CHAT_ID": TG_OWNER}
INFO = {}
OUT = []                                        # исходящие в Telegram
PASSED = []
N = {"upd": 0}


# --------------------------------------------------------------------------- #
#  Заглушки
# --------------------------------------------------------------------------- #

def fake_get(key: str, default: str = None) -> str:
    return SETTINGS.get(key, "" if default is None else default)


def fake_deliver(method, payload):
    OUT.append((method, payload))
    return {"ok": True, "result": {"message_id": len(OUT)}}


def fake_exchange(code: str, code_verifier: str) -> dict:
    assert code_verifier, "PKCE-секрет должен доходить до обмена кода"
    return {"id_token": "id-token-заглушка"}


def fake_tokeninfo(id_token: str) -> dict:
    return dict(INFO)


def tokeninfo(sub: str, email: str, **over) -> dict:
    base = {"aud": CLIENT_ID, "iss": "https://accounts.google.com",
            "exp": int((datetime.now() + timedelta(minutes=5)).timestamp()),
            "email_verified": "true", "email": email, "sub": sub,
            "given_name": "Тест", "family_name": "Связкин", "name": "Тест Связкин"}
    base.update(over)
    return base


def google_return(sub: str, email: str, purpose: str = google_auth.P_LOGIN, link_user_id=None) -> dict:
    """Полный проход «начали вход → вернулись от Google» без сети."""
    INFO.clear()
    INFO.update(tokeninfo(sub, email))
    with db.tx() as con:
        started = google_auth.start_login(con, "/tg", "127.0.0.1", purpose=purpose,
                                          link_user_id=link_user_id)
    with db.tx() as con:
        return google_auth.handle_callback(con, "код-от-google", started["state"], started["state"])


def init_data(tg_id: str, username: str = "") -> str:
    """Подписанный initData мини-приложения — ровно так, как его собирает Telegram."""
    user = {"id": int(tg_id), "first_name": "Гость", "username": username} if username \
        else {"id": int(tg_id), "first_name": "Гость"}
    pairs = {"auth_date": str(int(datetime.now().timestamp())),
             "user": _json.dumps(user, ensure_ascii=False, separators=(",", ":"))}
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


# --------------------------------------------------------------------------- #
#  Мелочи
# --------------------------------------------------------------------------- #

def ok(name, cond):
    assert cond, f"ПРОВАЛ: {name}"
    PASSED.append(name)
    print("  ✓", name)


def expect(status, fn, *args, **kw):
    try:
        fn(*args, **kw)
    except HTTPException as e:
        assert e.status_code == status, f"ожидали {status}, получили {e.status_code}: {e.detail}"
        return e.detail
    raise AssertionError(f"ожидали ошибку {status}, а вызов прошёл")


def call(method: str, path: str, body=None, params=None, token=None):
    query = urlencode(params or {}, encoding="utf-8")
    payload = _json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
    headers = [(b"host", b"test"), (b"content-type", b"application/json"),
               (b"content-length", str(len(payload)).encode())]
    if token:
        headers.append((b"authorization", ("Bearer " + token).encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(), "headers": headers,
             "client": ("127.0.0.1", 0), "server": ("test", 80)}
    out = {"status": None, "chunks": []}

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["chunks"].append(msg.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    blob = b"".join(out["chunks"])
    try:
        return out["status"], _json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return out["status"], blob


def upd_id() -> int:
    N["upd"] += 1
    return 88_000_000 + N["upd"]


def send_to_bot(tg_id: str, text: str, username: str = "") -> dict:
    frm = {"id": int(tg_id), "first_name": "Тест"}
    if username:
        frm["username"] = username
    return tgbot.process({"update_id": upd_id(),
                          "message": {"message_id": upd_id(), "from": frm,
                                      "chat": {"id": int(tg_id), "type": "private"}, "text": text}})


def press_button(tg_id: str, data: str) -> dict:
    """Нажатие кнопки под сообщением: Telegram присылает callback_query."""
    return tgbot.process({"update_id": upd_id(),
                          "callback_query": {"id": "cb-" + str(upd_id()),
                                             "from": {"id": int(tg_id), "first_name": "Тест"},
                                             "message": {"message_id": upd_id(),
                                                         "chat": {"id": int(tg_id), "type": "private"}},
                                             "data": data}})


def new_user(con, login: str, role: str = "сотрудник", tg=None, sub=None, email=None) -> dict:
    con.execute("INSERT INTO users (login, full_name, phone, role, branch, password_hash, salt, status,"
                " telegram_id, google_sub, email, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (login, "Тест " + login, "", role, "", "x", "y", auth.STATUS_ACTIVE, tg, sub, email,
                 db.now()))
    return db.rows(con, "SELECT * FROM users WHERE login=?", login)[0]


def audit_text(con, since: int) -> str:
    rows = db.rows(con, "SELECT who, action, detail FROM audit WHERE id > ?", since)
    return _json.dumps(rows, ensure_ascii=False)


# --------------------------------------------------------------------------- #
#  Сценарий
# --------------------------------------------------------------------------- #

def run():
    db.ensure_schema()
    with db.tx() as con:
        mark = con.execute("SELECT COALESCE(MAX(id),0) FROM audit").fetchone()[0]

    # --- 1. миграция старых привязок ---
    with db.tx() as con:
        u_old = new_user(con, LOGIN_OLD, tg=TG_OLD, sub=SUB_OLD, email=EMAIL_OLD)
        con.execute("DELETE FROM login_links WHERE user_id=?", (u_old["id"],))
        made = db.backfill_login_links(con)
        again = db.backfill_login_links(con)
        links = login_links.rows_of(con, u_old["id"])
    ok("миграция: из users завелись оба способа входа", made >= 2 and len(links) == 2)
    ok("миграция идемпотентна: второй прогон ничего не добавил", again == 0)
    ok("в login_links лежат те же идентификаторы",
       {l["external_id"] for l in links} == {TG_OLD, SUB_OLD})
    ok("почта из users попала в display Google",
       [l["display"] for l in links if l["provider"] == "google"] == [EMAIL_OLD])

    # --- 2. один профиль: два способа входа ведут в одну учётную запись ---
    with db.tx() as con:
        by_tg = login_links.user_by_link(con, "telegram", TG_OLD)
        by_google = login_links.user_by_link(con, "google", SUB_OLD)
    ok("Telegram и Google ведут в один и тот же профиль",
       by_tg and by_google and by_tg["id"] == by_google["id"] == u_old["id"])

    # вход через Google по второму устройству — та же учётная запись, нового профиля нет
    with db.tx() as con:
        before = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    out = google_return(SUB_OLD, EMAIL_OLD, purpose=google_auth.P_ADMIN)
    with db.tx() as con:
        after = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        session = auth.session_user(con, out["token"])
    ok("вход через Google с нового устройства: тот же профиль, новый не заводится",
       after == before and session and session["id"] == u_old["id"])

    # --- 3. чужой аккаунт: 409 ---
    with db.tx() as con:
        u2 = new_user(con, LOGIN_SECOND, tg=TG_STRANGER)
        db.backfill_login_links(con)
        detail = expect(409, login_links.attach, con, u2, "google", SUB_OLD, EMAIL_OLD)
    ok("чужая почта: 409 «привязана к другому профилю»", "другому профилю" in detail)

    # --- 4. последний способ входа не отвязывается ---
    with db.tx() as con:
        detail = expect(422, login_links.detach, con, u2, "telegram")
        ok("единственный способ входа отвязать нельзя", "единственный способ" in detail)
        login_links.attach(con, u2, "google", SUB_FREE, EMAIL_FREE)
        res = login_links.detach(con, u2, "telegram")
        ok("когда способов два — отвязать можно", res["ok"] and res["left"] == ["google"])
        expect(422, login_links.detach, con, u2, "google")
        ok("последний оставшийся снова не отвязать", True)

    # --- 5. гость → сотрудник одним кликом (страница «Вход для администратора») ---
    st, started = call("POST", "/auth/tg-link/start", {"link_id": "", "mode": "admin"})
    ok("код для входа администратора выдан", st == 200 and started["code"].startswith("INS-"))
    send_to_bot(TG_NEW, started["code"], username="новичок_тест")
    st, done = call("GET", "/auth/tg-link/status", params={"link_id": started["link_id"]})
    ok("незнакомый человек вошёл без анкеты", st == 200 and done["status"] == tg_link.ST_DONE)
    ok("новый профиль — «сотрудник», прав админа нет",
       done["user"]["role"] == "сотрудник" and done.get("created") is True)
    employee_token = done["token"]
    st, me = call("GET", "/auth/me", token=employee_token)
    ok("выданная сессия работает", st == 200 and me["role"] == "сотрудник")
    with db.tx() as con:
        emp = auth.session_user(con, employee_token)
        emp_links = login_links.rows_of(con, emp["id"])
    ok("способ входа записан с @username",
       len(emp_links) == 1 and emp_links[0]["display"] == "@новичок_тест")

    # --- 6. привязка Google к уже открытому профилю ---
    out = google_return(SUB_NEW, EMAIL_NEW, purpose=google_auth.P_LINK, link_user_id=emp["id"])
    ok("возврат от Google при привязке: «ок», сессия не выдаётся",
       out.get("link") == "ок" and not out.get("token"))
    st, links = call("GET", "/auth/links", token=employee_token)
    ok("в профиле два способа входа", st == 200 and len(links["links"]) == 2)
    ok("привязывать больше нечего", links["can_link"] == [])
    ok("отвязать можно любой из двух", sorted(links["can_unlink"]) == ["google", "telegram"])
    ok("контракт формы: у способа есть показ и маска",
       all({"provider", "title", "display", "external_id_masked", "linked_at"} <= set(l)
           for l in links["links"]))
    ok("Google показан почтой, Telegram — ником и ID",
       any(l["display"] == EMAIL_NEW for l in links["links"])
       and any(l["display"] == "@новичок_тест · ID " + TG_NEW for l in links["links"]))

    # тем же аккаунтом Google теперь входит тот же профиль (второй способ входа)
    out = google_return(SUB_NEW, EMAIL_NEW, purpose=google_auth.P_ADMIN)
    with db.tx() as con:
        same = auth.session_user(con, out["token"])
    ok("вход через Google ведёт в тот же профиль, что и Telegram", same["id"] == emp["id"])

    # занятый аккаунт при привязке к другому профилю — 409 без падения
    out = google_return(SUB_NEW, EMAIL_NEW, purpose=google_auth.P_LINK, link_user_id=u_old["id"])
    ok("привязать занятый Google к другому профилю нельзя",
       out.get("link") == "ошибка" and "другому профилю" in (out.get("reason") or ""))

    # --- 7. журнал: только маска почты ---
    with db.tx() as con:
        text = audit_text(con, mark)
    ok("почты целиком в журнале нет", EMAIL_NEW not in text and EMAIL_OLD not in text)
    ok("маска почты в журнале есть", google_auth.mask_email(EMAIL_NEW) in text)
    ok("имени человека в журнале нет", "Связкин" not in text)

    # --- 8. запрос доступа в админку ---
    with db.tx() as con:
        owner = new_user(con, LOGIN_OWNER, role="админ", tg=TG_OWNER)
        db.backfill_login_links(con)
        ok("владелец найден по TG_OWNER_CHAT_ID", login_links.is_owner(con, owner))
        ok("сотрудник владельцем не считается", not login_links.is_owner(con, emp))

    OUT.clear()
    st, first = call("POST", "/auth/admin-request", {}, token=employee_token)
    ok("запрос доступа создан", st == 200 and first["status"] == "ожидает" and first["created"])
    st, second = call("POST", "/auth/admin-request", {}, token=employee_token)
    ok("повторный запрос не дублируется",
       second["request_id"] == first["request_id"] and second.get("created") is False)
    with db.tx() as con:
        n = con.execute("SELECT COUNT(*) FROM admin_requests WHERE user_id=?", (emp["id"],)).fetchone()[0]
    ok("в базе ровно один запрос", n == 1)
    ok("владельцу ушло сообщение с кнопками",
       any(str(p.get("chat_id")) == TG_OWNER and "админку" in p.get("text", "") for _, p in OUT))

    rid = first["request_id"]
    press_button(TG_STRANGER, f"adminreq:{rid}:ok")
    with db.tx() as con:
        role = db.rows(con, "SELECT role FROM users WHERE id=?", emp["id"])[0]["role"]
    ok("чужое нажатие роли не меняет", role == "сотрудник")

    press_button(TG_OWNER, f"adminreq:{rid}:ok")
    with db.tx() as con:
        role = db.rows(con, "SELECT role FROM users WHERE id=?", emp["id"])[0]["role"]
        status = db.rows(con, "SELECT status FROM admin_requests WHERE id=?", rid)[0]["status"]
    ok("владелец подтвердил — роль «админ»", role == "админ" and status == "подтверждён")
    ok("человеку ушло уведомление",
       any(str(p.get("chat_id")) == TG_NEW and "админку" in p.get("text", "") for _, p in OUT))

    st, mine = call("GET", "/auth/admin-request", token=employee_token)
    ok("свой статус виден", st == 200 and mine["status"] == "подтверждён" and mine["is_admin"])

    # --- 9. гость мини-приложения: один клик по подписанному initData ---
    st, guest = call("POST", "/auth/admin-request", {"initData": init_data(TG_STRANGER, "гость_тест")})
    ok("гость мини-аппа получил профиль и подал запрос",
       st == 200 and guest["status"] == "ожидает" and guest["user"]["role"] == "сотрудник")
    st, bad = call("POST", "/auth/admin-request", {"initData": "user=%7B%22id%22%3A1%7D&hash=нет"})
    ok("поддельный initData не пускает (и не роняет сервер)",
       st == 401 and "Подпись" in (bad.get("detail") or ""))

    # --- 10. белый список единого входа ---
    ok("POST /auth/admin-request открыт без сессии", guard.is_open("/auth/admin-request", False))
    ok("привязки закрыты сессией",
       not guard.is_open("/auth/links", False)
       and not guard.is_open("/auth/link/google/start", False)
       and not guard.is_open("/auth/link/telegram/start", False))


def main():
    real = (llm.get, google_auth.exchange_code_for_tokens, google_auth.fetch_tokeninfo, tgbot._deliver)
    llm.get = fake_get
    google_auth.exchange_code_for_tokens = fake_exchange
    google_auth.fetch_tokeninfo = fake_tokeninfo
    tgbot._deliver = fake_deliver
    try:
        with temp_db("surveyor-test-login-links.db"):
            run()
    finally:
        (llm.get, google_auth.exchange_code_for_tokens,
         google_auth.fetch_tokeninfo, tgbot._deliver) = real
    print(f"\nВсе проверки пройдены: {len(PASSED)}")


if __name__ == "__main__":
    main()
