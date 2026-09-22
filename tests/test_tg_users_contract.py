"""
Раздел «Пользователи» (GET /tg/users): поля email и login_method — на ВРЕМЕННОЙ копии базы.

Запуск из корня проекта:
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_tg_users_contract.py

Контракт для экранов app/tg.html и app/admin_hub.html (задача 123):
  * email — строка или null (почта из аккаунта Google, у остальных null);
  * login_method — "telegram" | "google" | "both" | "service":
      telegram — есть users.telegram_id; google — есть users.google_sub; both — оба;
      service — ни того, ни другого: служебная запись, вход только по логину и паролю;
  * почта видна тем же, кому виден телефон (сейчас — всем вошедшим); login_method — всем, кому видна запись.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.pop("SURVEYOR_DEV", None)                  # вход по сессии, как на Railway

from tmpdb import temp_db                             # noqa: E402
from app import auth, db, registration                # noqa: E402
from app.main import app                              # noqa: E402

PEOPLE = [  # login, telegram_id, google_sub, email, phone, role
    ("тест-кв-телеграм", "990100001", None, None, "+998900000011", "сотрудник"),
    ("тест-кв-гугл", None, "тест-sub-1", "google.only@insuranceon.uz", "", "сотрудник"),
    ("тест-кв-оба", "990100002", "тест-sub-2", "both@insuranceon.uz", "+998900000012", "админ"),
    ("тест-кв-служебный", None, None, None, "", "андеррайтер"),
]
PASSED = []


def ok(name, cond, hint=""):
    assert cond, f"ПРОВАЛ: {name} {hint}"
    PASSED.append(name)
    print("  ✓", name)


def get_users(token: str):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": "/tg/users", "raw_path": b"/tg/users", "root_path": "",
             "query_string": b"", "client": ("10.0.0.7", 0), "server": ("test", 80),
             "headers": [(b"host", b"test"), (b"authorization", f"Bearer {token}".encode())]}
    out = {"chunks": []}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["chunks"].append(msg.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    return out["status"], json.loads(b"".join(out["chunks"]).decode("utf-8"))


def main():
    with temp_db("surveyor-test-users.db"):
        db.ensure_schema()              # как при старте сервера: новые таблицы и колонки
        tokens, ids = {}, {}
        with db.tx() as con:
            for login, tg, sub, email, phone, role in PEOPLE:
                cur = con.execute(
                    "INSERT INTO users (login, full_name, phone, role, branch, password_hash, salt, status,"
                    " telegram_id, google_sub, email, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (login, "Тест " + login, phone, role, "тест", "x" * 64, "0" * 32, auth.STATUS_ACTIVE,
                     tg, sub, email, db.now()))
                ids[login] = cur.lastrowid
                u = db.rows(con, "SELECT * FROM users WHERE id=?", cur.lastrowid)[0]
                tokens[login], _ = auth.create_session(con, u, ip="10.0.0.7", user_agent="test")

        expect = {"тест-кв-телеграм": "telegram", "тест-кв-гугл": "google", "тест-кв-оба": "both",
                  "тест-кв-служебный": "service"}
        for viewer in ("тест-кв-телеграм", "тест-кв-оба"):          # обычный сотрудник и администратор
            st, b = get_users(tokens[viewer])
            ok(f"{viewer}: список открыт (200), can_manage={b.get('can_manage')}", st == 200)
            mine = {x["login"]: x for x in b["items"] if x["id"] in ids.values()}
            for login, tg, sub, email, phone, role in PEOPLE:
                x = mine[login]
                ok(f"  {viewer} видит {login}: login_method={x['login_method']}, email={x['email']!r}",
                   x["login_method"] == expect[login] and x["email"] == email)
                # почта — по тому же правилу, что телефон: оба поля либо показаны, либо скрыты
                ok(f"  {login}: телефон и почта видны вместе", ("phone" in x) == ("email" in x)
                   and x["phone"] == (phone or ""))
            allowed = {"telegram", "google", "both", "service"}
            ok(f"{viewer}: у каждой записи login_method из {sorted(allowed)}, email — строка или null",
               all(x["login_method"] in allowed and (x["email"] is None or isinstance(x["email"], str))
                   for x in b["items"]))

        ok("login_method() без полей — service", registration.login_method({}) == "service")
        st, _ = get_users("чужой-токен")
        ok("без входа список закрыт (401)", st == 401)
    print(f"Все проверки пройдены: {len(PASSED)}")


if __name__ == "__main__":
    main()
