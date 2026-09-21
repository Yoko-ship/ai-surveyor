"""
Смена имени самим человеком: PUT /tg/me/name (app/registration.py, задача 170).

Запуск из корня проекта:
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_profile.py

Без pytest и без живого сервера: свой ASGI-клиент, база — временная копия (tests/tmpdb.py).
Что проверяется:
  1. без входа — 401;
  2. короче 2 символов и длиннее 80 — 422 с понятной фразой, имя не меняется;
  3. пробелы схлопываются, имя пишется в users.full_name, /tg/me отдаёт новое имя;
  4. вход по заголовку Authorization: Bearer — тоже работает;
  5. в журнал попадает только факт и user_id — самого имени там нет;
  6. закрытая учётная запись имя не меняет;
  7. страница мини-аппа зовёт PUT /tg/me/name из левой панели и из «Настройки → Профиль».
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("SURVEYOR_DEV", "1")

from tmpdb import temp_db  # noqa: E402
from app import auth, db   # noqa: E402
from app.main import app   # noqa: E402

LOGIN = "тест-профиль"
passed = failed = 0


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ок  ", name)
    else:
        failed += 1
        print("  ПЛОХО", name, extra)


def call(method, path, body=None, cookie=None, bearer=None):
    payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
    headers = [(b"host", b"test"), (b"content-type", b"application/json"),
               (b"content-length", str(len(payload)).encode())]
    if cookie:
        headers.append((b"cookie", f"sid={cookie}".encode()))
    if bearer:
        headers.append((b"authorization", f"Bearer {bearer}".encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "", "query_string": b"",
             "headers": headers, "client": ("127.0.0.1", 0), "server": ("test", 80)}
    out = {"status": None, "chunks": []}

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["chunks"].append(msg.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    raw = b"".join(out["chunks"]).decode("utf-8")
    try:
        return out["status"], json.loads(raw)
    except ValueError:
        return out["status"], raw


def setup():
    db.ensure_schema()
    with db.tx() as con:
        con.execute("DELETE FROM users WHERE login=?", (LOGIN,))
        cur = con.execute("INSERT INTO users (login, full_name, role, branch, password_hash, salt, status, created_at)"
                          " VALUES (?,?,?,?,?,?,?,?)", (LOGIN, "Старое Имя", "сотрудник", "тест", "x", "y", "активен", db.now()))
        uid = cur.lastrowid
        u = db.rows(con, "SELECT * FROM users WHERE id=?", uid)[0]
        token, _ = auth.create_session(con, u, "127.0.0.1", "test")
    return uid, token


def full_name(uid):
    with db.tx() as con:
        return db.rows(con, "SELECT full_name FROM users WHERE id=?", uid)[0]["full_name"]


def main():
    uid, token = setup()

    st, b = call("PUT", "/tg/me/name", {"name": "Алишер"})
    ok("без входа — 401", st == 401, (st, b))

    st, b = call("PUT", "/tg/me/name", {"name": " А "}, cookie=token)
    ok("короче 2 символов — 422 с понятной фразой", st == 422 and "не короче 2" in str(b.get("detail")), (st, b))
    st, b = call("PUT", "/tg/me/name", {"name": "Я" * 81}, cookie=token)
    ok("длиннее 80 — 422, имя не обрезается молча", st == 422 and "80" in str(b.get("detail")), (st, b))
    ok("после отказов имя прежнее", full_name(uid) == "Старое Имя", full_name(uid))

    st, b = call("PUT", "/tg/me/name", {"name": "  Алишер   Каримов "}, cookie=token)
    ok("по cookie — 200, пробелы схлопнуты", st == 200 and b.get("name") == "Алишер Каримов", (st, b))
    ok("имя записано в users.full_name", full_name(uid) == "Алишер Каримов", full_name(uid))
    st, me = call("GET", "/tg/me", cookie=token)
    ok("/tg/me отдаёт новое имя", st == 200 and me["user"]["name"] == "Алишер Каримов", me.get("user"))

    st, b = call("PUT", "/tg/me/name", {"name": "Ali"}, bearer=token)
    ok("по Bearer — 200", st == 200 and full_name(uid) == "Ali", (st, b))
    st, b = call("PUT", "/tg/me/name", {"name": "Я" * 80}, bearer=token)
    ok("ровно 80 символов — принимается", st == 200 and len(full_name(uid)) == 80, (st, b))

    with db.tx() as con:
        rows = db.rows(con, "SELECT * FROM audit WHERE entity=? AND action=?", f"user:{uid}", "имя изменено")
    text = json.dumps(rows, ensure_ascii=False)
    ok("в журнале есть факт смены имени с user_id", len(rows) == 3 and all(json.loads(r["detail"]) == {"user_id": uid} for r in rows), rows[:1])
    ok("самого имени в журнале нет", "Алишер" not in text and "Ali\"" not in text and "Я" * 10 not in text, text[:200])

    with db.tx() as con:
        con.execute("UPDATE users SET status=? WHERE id=?", ("заблокирован", uid))
    st, b = call("PUT", "/tg/me/name", {"name": "Новое"}, cookie=token)
    ok("закрытая учётная запись имя не меняет (401/403)", st in (401, 403) and len(full_name(uid)) == 80, (st, b))

    html = (ROOT / "app" / "tg.html").read_text(encoding="utf-8")
    ok("мини-апп зовёт PUT /tg/me/name", '"/tg/me/name", jsonOpts("PUT"' in html)
    ok("имя меняется из левой панели и из «Профиля»", 'id="whoNameBtn"' in html and 'id="whoNameForm"' in html
       and 'id="profileCard"' in html and 'id="pfSave"' in html)


if __name__ == "__main__":
    with temp_db("surveyor-profile.db"):
        main()
    print(f"\nпройдено {passed}, не пройдено {failed}")
    sys.exit(1 if failed else 0)
