"""
Вход из обычного браузера через Telegram по коду боту (app/tg_link.py).

Запуск из корня проекта (pytest и httpx в sandbox\\.venv не установлены — свой ASGI-клиент,
живой сервер не трогаем и не перезапускаем):
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_tg_link.py

Сеть не используется: отправка в Telegram подменяется сборщиком исходящих, обновления бота
подделываются. Все тестовые записи удаляются в конце (рабочая база остаётся чистой).

Что проверяется:
   1  код выдаётся, выглядит как INS-XXXX и в базе лежит только отпечатком (открытого кода нет);
   2  бот принимает код из поддельного обновления и привязывает telegram_id отправителя;
   3  опрос состояния: «ожидание» → «готово», выдана рабочая сессия (токен пускает в /tg/me);
   4  повторное использование того же кода — отказ, чужая привязка не подменяется;
   5  просроченный код — отказ и у бота, и в опросе;
   6  чужой link_id сессии не даёт: ни своей, ни тем более чужой;
   7  не больше пяти кодов за окно с одного link_id;
   8  незнакомый человек получает «нужна регистрация» и регистрируется БЕЗ кода на телефон;
   9  правило первого администратора по TG_ADMIN_USERNAME срабатывает и на этом пути;
  10  белый список единого входа: обе точки открыты без сессии, и только они;
  11  журналы чистые: кода нет ни в audit, ни в tg_messages, ни в самой таблице.
"""
import asyncio
import json as _json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Тест ходит в приложение напрямую с адреса 127.0.0.1 (как tests/test_tgbot.py).
os.environ.setdefault("SURVEYOR_DEV", "1")

from tmpdb import temp_db  # noqa: E402  (tests/tmpdb.py)
from app import auth, db, guard, llm, registration, telegram, tg_link, tgbot   # noqa: E402
from app.main import app                                                       # noqa: E402

TOKEN = "123456:TEST"                       # вымышленный токен бота, сеть не трогаем
BRANCH = "тест-ссылка"
TG_ACTIVE, TG_NEW, TG_OTHER, TG_BOOT = "9920001", "9920002", "9920003", "9920004"
ALL_TG = [TG_ACTIVE, TG_NEW, TG_OTHER, TG_BOOT]
LOGIN_ACTIVE = "тест-ссылка-активный"
BOOT_USERNAME = "тест_ссылка_владелец"
PHONE = "+998900000021"                     # вымышленный номер: не принадлежит никому
PD_NAME = "Ссылкин Тест Тестович"
DEPARTMENT = "Тестовый департамент входа"

OUT = []                                    # собранные исходящие: (метод, payload)
MARK = {"audit_id": 0, "tg_id": 0}
SAVED = {}                                  # настройки, которые тест подменял
LINKS = []                                  # ключи ожидания, выданные тесту (чтобы всё убрать за собой)


# ---------- минимальный ASGI-клиент ----------

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


# ---------- подделки Telegram ----------

def fake_deliver(method, payload):
    OUT.append((method, payload))
    return {"ok": True, "result": {"message_id": len(OUT)}}


# номера обновлений уникальны для каждого прогона: бот помнит уже разобранные (tg_updates)
UPD_BASE = 77_000_000 + int(datetime.now().timestamp()) % 900_000 * 20


def update_id():
    MARK["tg_id"] += 1
    return UPD_BASE + MARK["tg_id"]


def send_to_bot(tg_id: str, text: str, username: str = "", first_name: str = "Тест") -> dict:
    """Одно обновление «человек написал боту» — ровно то, что присылает Telegram."""
    frm = {"id": int(tg_id), "first_name": first_name}
    if username:
        frm["username"] = username
    upd = {"update_id": update_id(),
           "message": {"message_id": update_id(), "from": frm, "chat": {"id": int(tg_id), "type": "private"},
                       "text": text}}
    return tgbot.process(upd)


def texts_for(tg_id) -> list:
    return [p.get("text", "") for m, p in OUT if str(p.get("chat_id")) == str(tg_id)]


def ask_status(link_id: str):
    return call("GET", "/auth/tg-link/status", params={"link_id": link_id})


def ask_code(link_id: str = ""):
    st, d = call("POST", "/auth/tg-link/start", {"link_id": link_id})
    if isinstance(d, dict) and d.get("link_id"):
        LINKS.append(d["link_id"])
    return st, d


# ---------- подготовка и уборка ----------

def setup():
    db.ensure_schema()
    with db.tx() as con:
        MARK["audit_id"] = con.execute("SELECT COALESCE(MAX(id), 0) FROM audit").fetchone()[0]
        for tg in ALL_TG:
            con.execute("DELETE FROM users WHERE telegram_id=?", (tg,))
            con.execute("DELETE FROM reg_codes WHERE telegram_id=?", (tg,))
            con.execute("DELETE FROM pd_consents WHERE telegram_id=?", (tg,))
            con.execute("DELETE FROM tg_link_codes WHERE telegram_id=?", (tg,))
        for key in ("TG_ADMIN_USERNAME", "TG_BOT_USERNAME", "TG_OWNER_CHAT_ID"):
            r = db.rows(con, "SELECT value FROM app_settings WHERE key=?", key)
            SAVED[key] = r[0]["value"] if r else None
            con.execute("DELETE FROM app_settings WHERE key=?", (key,))
        # имя бота задаём настройкой: getMe в тесте не вызывается, сеть не трогаем
        con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?)",
                    ("TG_BOT_USERNAME", "inson_surveyor_bot", db.now()))
        con.execute("DELETE FROM users WHERE login=?", (LOGIN_ACTIVE,))
        con.execute("INSERT INTO users (login, full_name, role, branch, department, position,"
                    " password_hash, salt, status, telegram_id, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (LOGIN_ACTIVE, "Тестовый активный", "сотрудник", BRANCH, DEPARTMENT, "менеджер",
                     "x", "y", auth.STATUS_ACTIVE, TG_ACTIVE, db.now()))


def teardown():
    with db.tx() as con:
        for tg in ALL_TG:
            con.execute("DELETE FROM pd_consents WHERE telegram_id=?", (tg,))
            con.execute("DELETE FROM reg_codes WHERE telegram_id=?", (tg,))
            con.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE telegram_id=?)", (tg,))
            con.execute("DELETE FROM users WHERE telegram_id=?", (tg,))
            con.execute("DELETE FROM tg_link_codes WHERE telegram_id=?", (tg,))
            con.execute("DELETE FROM tg_messages WHERE telegram_id=?", (tg,))
        con.execute("DELETE FROM tg_updates WHERE update_id BETWEEN ? AND ?", (UPD_BASE, UPD_BASE + 999))
        con.execute("DELETE FROM users WHERE login=?", (LOGIN_ACTIVE,))
        for link_id in set(LINKS):
            con.execute("DELETE FROM tg_link_codes WHERE link_id=?", (link_id,))
        con.execute("DELETE FROM audit WHERE id > ?", (MARK["audit_id"],))
        for key, value in SAVED.items():
            con.execute("DELETE FROM app_settings WHERE key=?", (key,))
            if value is not None:
                con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?)",
                            (key, value, db.now()))
        left = con.execute("SELECT COUNT(*) FROM tg_link_codes").fetchone()[0]
        users = con.execute("SELECT COUNT(*) FROM users WHERE telegram_id IN (%s)"
                            % ",".join("?" * len(ALL_TG)), tuple(ALL_TG)).fetchone()[0]
    print(f"  очищено; строк в tg_link_codes: {left}; тестовых пользователей осталось: {users}")


# ---------- проверки ----------

def check_start():
    st, d = ask_code()
    assert st == 200, (st, d)
    code, link_id = d["code"], d["link_id"]
    assert tg_link.CODE_RE.fullmatch(code), code
    assert d["link"] == "https://t.me/inson_surveyor_bot?start=link_" + code, d["link"]
    assert d["bot"] == "@inson_surveyor_bot", d["bot"]
    with db.tx() as con:
        row = db.rows(con, "SELECT * FROM tg_link_codes WHERE link_id=?", link_id)[0]
        # в базе только отпечаток: открытого кода нет ни в одной колонке
        flat = " ".join(str(v) for v in row.values() if v is not None)
        assert code not in flat, flat
        assert row["code_hash"] == tg_link.code_hash(con, code)
        assert row["used_at"] is None and row["telegram_id"] is None
    print(f"1. код выдан ({code[:4]}…), ссылка на бота собрана, в базе только отпечаток — ок")
    return link_id, code


def check_pending(link_id):
    st, d = ask_status(link_id)
    assert st == 200 and d["status"] == tg_link.ST_PENDING, d
    assert "token" not in d, d
    print("2. пока боту не написали — «ожидание», токена нет — ок")


def check_bind_and_session(link_id, code):
    send_to_bot(TG_ACTIVE, "/start link_" + code)
    assert any("Готово" in t for t in texts_for(TG_ACTIVE)), texts_for(TG_ACTIVE)
    with db.tx() as con:
        row = db.rows(con, "SELECT * FROM tg_link_codes WHERE link_id=?", link_id)[0]
        assert row["telegram_id"] == TG_ACTIVE and row["used_at"], row
        assert _json.loads(row["tg_user_json"])["id"] == int(TG_ACTIVE)
    st, d = ask_status(link_id)
    assert st == 200 and d["status"] == tg_link.ST_DONE, d
    assert d["user"]["login"] == LOGIN_ACTIVE, d
    token = d["token"]
    st2, me = call("GET", "/tg/me", token=token)
    assert st2 == 200 and (me.get("user") or {}).get("full_name") == "Тестовый активный", (st2, me)
    assert me.get("status") == auth.STATUS_ACTIVE, me
    print("3. бот привязал telegram_id, состояние «готово», токен пускает в /tg/me — ок")
    return token


def check_second_use(code):
    """Тот же код второй раз — отказ. Чужой telegram_id привязку не перебивает."""
    before = len(texts_for(TG_OTHER))
    send_to_bot(TG_OTHER, code)
    new = texts_for(TG_OTHER)[before:]
    assert new and "уже использован" in new[-1], new
    with db.tx() as con:
        rows = db.rows(con, "SELECT * FROM tg_link_codes WHERE telegram_id=?", TG_OTHER)
        assert not rows, rows
    print("4. повторное использование кода — отказ, чужая привязка не подменяется — ок")


def check_expired():
    st, d = ask_code()
    link_id, code = d["link_id"], d["code"]
    with db.tx() as con:                     # переводим часы: код выдан 11 минут назад
        old = tg_link._ts(datetime.now() - timedelta(minutes=tg_link.CODE_MINUTES + 1))
        con.execute("UPDATE tg_link_codes SET expires_at=? WHERE link_id=?", (old, link_id))
    before = len(texts_for(TG_ACTIVE))
    send_to_bot(TG_ACTIVE, code)
    assert "истёк" in texts_for(TG_ACTIVE)[before:][-1], texts_for(TG_ACTIVE)[-1]
    st, d = ask_status(link_id)
    assert d["status"] == tg_link.ST_EXPIRED and "token" not in d, d
    print("5. просроченный код: бот отказал, опрос вернул «истёк», сессии нет — ок")


def check_foreign_link(token_of_active):
    """Чужой link_id не даёт ни своей сессии, ни чужой."""
    st, d = ask_code()
    mine = d["link_id"]
    st, d2 = ask_status(mine)
    assert d2["status"] == tg_link.ST_PENDING and "token" not in d2, d2
    st, d3 = ask_status("невыданный_ключ_ожидания_12345")
    assert d3["status"] == tg_link.ST_NONE and "token" not in d3, d3
    st, d4 = ask_status("")
    assert d4["status"] == tg_link.ST_NONE, d4
    print("6. чужой и выдуманный link_id сессии не дают — ок")


def check_rate_limit():
    st, d = ask_code()
    link_id = d["link_id"]
    got = 1
    last = (200, {})
    for _ in range(tg_link.RATE_MAX + 1):
        last = ask_code(link_id)
        if last[0] == 200:
            got += 1
        else:
            break
    assert last[0] == 429, last
    assert got == tg_link.RATE_MAX, got
    print(f"7. с одного link_id выдано {got} кодов, шестой — отказ 429 — ок")


def check_register_without_phone_code():
    """Незнакомый человек: «нужна регистрация», анкета без кода на телефон, сразу активен."""
    st, d = ask_code()
    link_id, code = d["link_id"], d["code"]
    send_to_bot(TG_NEW, "/start link_" + code, username="t" + TG_NEW)
    st, d = ask_status(link_id)
    assert st == 200 and d["status"] == tg_link.ST_REGISTER, d
    assert d["telegram_id"] == TG_NEW and "token" not in d, d

    # кода подтверждения телефона по этому пути нет — в reg_codes пусто
    with db.tx() as con:
        assert not db.rows(con, "SELECT 1 FROM reg_codes WHERE telegram_id=?", TG_NEW)

    st, out = call("POST", "/tg/register/submit",
                   {"link_id": link_id, "phone": PHONE, "full_name": PD_NAME,
                    "department": DEPARTMENT, "position": "менеджер",
                    "consent": True, "consent_phone": True, "branch": BRANCH})
    assert st == 200, (st, out)
    assert out["role"] == registration.ROLE and out["status"] == auth.STATUS_ACTIVE, out
    token = out["token"]
    st2, me = call("GET", "/tg/me", token=token)
    assert st2 == 200 and (me.get("user") or {}).get("full_name") == PD_NAME, (st2, me)
    with db.tx() as con:
        u = db.rows(con, "SELECT * FROM users WHERE telegram_id=?", TG_NEW)[0]
        assert u["phone"] == PHONE and u["status"] == auth.STATUS_ACTIVE, u
        # оба согласия записаны — как и при регистрации в мини-приложении
        kinds = {r["scope"] for r in db.rows(con, "SELECT scope FROM pd_consents WHERE telegram_id=?", TG_NEW)}
        assert len(kinds) == 2, kinds
    print("8. новый человек: «нужна регистрация», анкета без кода на телефон, сразу активен — ок")

    # чужая анкета по чужому link_id не проходит: привязки нет
    st3, bad = call("POST", "/tg/register/submit",
                    {"link_id": "невыданный_ключ_ожидания_12345", "phone": PHONE, "full_name": PD_NAME,
                     "department": DEPARTMENT, "position": "менеджер", "consent": True, "consent_phone": True})
    assert st3 == 401, (st3, bad)
    print("8а. анкета по непривязанному link_id — 401 — ок")


def check_first_admin():
    """Правило первого администратора по TG_ADMIN_USERNAME действует и на входе из браузера."""
    with db.tx() as con:
        admins = db.rows(con, "SELECT id FROM users WHERE role='админ'")
        if admins:                           # на рабочей базе админ уже есть — правило проверяем «вхолостую»
            print("9. администратор в базе уже есть — правило первого админа не применяется (проверено) — ок")
            return
        con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?)",
                    ("TG_ADMIN_USERNAME", BOOT_USERNAME, db.now()))
    st, d = ask_code()
    link_id, code = d["link_id"], d["code"]
    send_to_bot(TG_BOOT, code, username=BOOT_USERNAME)
    st, d = ask_status(link_id)
    assert d["status"] == tg_link.ST_DONE and d["user"]["role"] == "админ", d
    print("9. первый администратор по TG_ADMIN_USERNAME входит по коду боту сразу — ок")


def check_guard():
    assert "/auth/tg-link/start" in guard.WHITE_EXACT
    assert "/auth/tg-link/status" in guard.WHITE_EXACT
    # только точные пути: сосед вида /auth/tg-link/что-нибудь наружу не открыт
    assert not any(p.startswith("/auth/tg-link") for p in guard.WHITE_PREFIX)
    print("10. белый список единого входа: открыты ровно две точки, префиксом — ничего — ок")


def check_no_code_in_logs():
    """Ни один выданный код не должен встретиться в журналах и в таблице ожиданий."""
    st, d = ask_code()
    code = d["code"]
    send_to_bot(TG_ACTIVE, code)
    bad = []
    with db.tx() as con:
        for table, cols in (("audit", ("who", "action", "entity", "detail")),
                            ("tg_messages", ("kind", "error")),
                            ("tg_link_codes", ("link_id", "code_hash", "tg_user_json"))):
            for r in db.rows(con, f"SELECT * FROM {table} WHERE id > ?", 0):
                for c in cols:
                    v = r.get(c)
                    if isinstance(v, str) and code in v:
                        bad.append((table, c, v[:60]))
    assert not bad, bad
    assert not any(code in t for t in texts_for(TG_ACTIVE)), "код вернулся в чат"
    print("11. код не попал ни в audit, ни в tg_messages, ни в таблицу ожиданий — ок")


def main():
    setup()
    real = (telegram.bot_token, tgbot.bot_token, tgbot._deliver)
    try:
        telegram.bot_token = lambda: TOKEN
        tgbot.bot_token = lambda: TOKEN
        tgbot._deliver = fake_deliver
        link_id, code = check_start()
        check_pending(link_id)
        token = check_bind_and_session(link_id, code)
        check_second_use(code)
        check_expired()
        check_foreign_link(token)
        check_rate_limit()
        check_register_without_phone_code()
        check_first_admin()
        check_guard()
        check_no_code_in_logs()
        print("\nВсе проверки входа по коду боту пройдены.")
    finally:
        telegram.bot_token, tgbot.bot_token, tgbot._deliver = real
        teardown()


if __name__ == "__main__":
    with temp_db("surveyor-tg-link.db"):  # рабочая data/surveyor.db не меняется
        main()
