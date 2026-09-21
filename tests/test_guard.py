"""
Единый вход (app/guard.py): без сессии сервер наружу ничего не отдаёт.

Проверяем без живого сервера — своим ASGI-клиентом (pytest и httpx в sandbox\\.venv не стоят,
поэтому обычные assert). Живой сервер не трогаем и не перезапускаем.

Запуск из корня проекта:
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_guard.py

Что проверяется:
  1. без сессии — 401 (вызов API) или 302 на /login (страница) по шести адресам;
  2. агент на /admin — 403, администратор — 200;
  3. /health открыт всегда, /tg/webhook/<секрет> открыт (секрет проверяет сам обработчик);
  4. SURVEYOR_DEV=1: соединение с 127.0.0.1 проходит, с чужого адреса и с заголовком прокси — нет;
  5. на рабочей базе код первого администратора второго админа не создаёт;
  6. пустой сервер (временная копия базы): код выдаётся в журнал, создаёт админа и гаснет навсегда.

Тестовые записи («тест-guard-*») удаляются в конце.
"""
import asyncio
import json as _json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# сам guard включаем принудительно: режим разработчика проверяем точечно, ниже
os.environ.pop("SURVEYOR_DEV", None)

from app import auth, db, guard, llm   # noqa: E402
from app.main import app          # noqa: E402

PREFIX = "тест-guard-"
PEOPLE = [(PREFIX + "агент", "агент"), (PREFIX + "админ", "админ")]
TOKENS = {}
WEBHOOK_SECRET = "тест-guard-секрет"

passed, failed = 0, 0


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ок  ", name)
    else:
        failed += 1
        print("  ПЛОХО", name, extra)


# ---------- минимальный ASGI-клиент ----------

def call(method: str, path: str, body=None, params=None, who=None, host="127.0.0.1", headers=None, html=False):
    """Вызов приложения по ASGI. host — реальный адрес соединения (scope['client'])."""
    query = urlencode(params or {}, encoding="utf-8")
    payload = _json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
    hdrs = [(b"host", b"test"), (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode())]
    if html:
        hdrs.append((b"accept", b"text/html,application/xhtml+xml"))
    if who:
        hdrs.append((b"cookie", f"sid={TOKENS[who]}".encode()))
    for k, v in (headers or {}).items():
        hdrs.append((k.lower().encode(), v.encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(), "headers": hdrs,
             "client": (host, 0), "server": ("test", 80)}
    out = {"status": None, "chunks": [], "headers": {}}

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = {k.decode().lower(): v.decode() for k, v in msg.get("headers", [])}
        elif msg["type"] == "http.response.body":
            out["chunks"].append(msg.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    raw = b"".join(out["chunks"]).decode("utf-8", "replace")
    try:
        return out["status"], _json.loads(raw), out["headers"]
    except ValueError:
        return out["status"], raw, out["headers"]


# ---------- подготовка и уборка ----------

def setup():
    db.ensure_schema()
    with db.tx() as con:
        ts = db.now()
        for login, role in PEOPLE:
            con.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE login=?)", (login,))
            con.execute("DELETE FROM users WHERE login=?", (login,))
            cur = con.execute("INSERT INTO users (login, full_name, role, branch, password_hash, salt,"
                              " status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                              (login, "Тестовый " + role, role, "тест-guard", "x", "y", "активен", ts))
            u = db.rows(con, "SELECT * FROM users WHERE id=?", cur.lastrowid)[0]
            TOKENS[login], _ = auth.create_session(con, u, "127.0.0.1", "test")
        # секрет вебхука на время теста
        con.execute("DELETE FROM app_settings WHERE key='TG_WEBHOOK_SECRET'")
        con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?)",
                    ("TG_WEBHOOK_SECRET", WEBHOOK_SECRET, ts))


def teardown():
    with db.tx() as con:
        for login, _role in PEOPLE:
            con.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE login=?)", (login,))
            con.execute("DELETE FROM audit WHERE who=?", (login,))
            con.execute("DELETE FROM users WHERE login=?", (login,))
        con.execute("DELETE FROM app_settings WHERE key='TG_WEBHOOK_SECRET'")
        left = con.execute("SELECT COUNT(*) FROM users WHERE login LIKE ?", (PREFIX + "%",)).fetchone()[0]
    print(f"  очищено; тестовых пользователей осталось: {left}")


# ---------- проверки ----------

# шесть адресов, каждый из которых до 20.09.2026 отвечал кому угодно
CLOSED_API = ["/requests", "/audit", "/tasks", "/deploy/status", "/deploy/backup/download", "/approvals/inbox"]
CLOSED_PAGES = ["/", "/admin", "/admin/deploy", "/tasks-page", "/approvals", "/portfolio"]


def check_no_session():
    print("1. Без сессии")
    for p in CLOSED_API:
        st, body, _ = call("GET", p)
        ok(f"{p} → 401", st == 401 and isinstance(body, dict) and body.get("detail") == "нужен вход",
           f"получено {st} {body}")
    for p in CLOSED_PAGES:
        st, _body, h = call("GET", p, html=True)
        ok(f"страница {p} → редирект на /login",
           st == 302 and h.get("location", "").startswith("/login?next="), f"получено {st} {h}")


def check_open():
    print("2. Белый список")
    for p in ("/health", "/login", "/theme.js", "/tg", "/auth/bootstrap-needed"):
        st, _b, _h = call("GET", p)
        ok(f"{p} открыт", st == 200, f"получено {st}")
    st, _b, _h = call("POST", "/auth/login", {"login": "нет-такого", "password": "неверный"})
    ok("/auth/login открыт (отвечает 401 «неверный логин», а не «нужен вход»)", st == 401)
    # вебхук бота: guard пропускает, секрет проверяет сам обработчик
    st, body, _h = call("POST", f"/tg/webhook/{WEBHOOK_SECRET}", {"update_id": 1})
    ok("/tg/webhook/<верный секрет> открыт", st == 200, f"получено {st} {body}")
    st, _b, _h = call("POST", "/tg/webhook/чужой-секрет", {"update_id": 1})
    ok("/tg/webhook/<чужой секрет> не пускает (403/404 от обработчика)", st in (403, 404), f"получено {st}")
    st, _b, _h = call("GET", "/docs")
    ok("/docs закрыт вне режима разработчика", st == 404, f"получено {st}")


def check_roles():
    print("3. Роли")
    agent, admin = PEOPLE[0][0], PEOPLE[1][0]
    st, body, _h = call("GET", "/admin", who=agent)
    ok("агент на /admin → 403", st == 403, f"получено {st} {body}")
    for p in ("/audit", "/tasks", "/deploy/status"):
        st, _b, _h = call("GET", p, who=agent)
        ok(f"агент на {p} → 403", st == 403, f"получено {st}")
    st, body, _h = call("GET", "/admin", who=admin)
    ok("админ на /admin → 200", st == 200, f"получено {st}")
    st, body, _h = call("GET", "/audit", who=admin)
    ok("админ на /audit → 200", st == 200, f"получено {st}")
    st, body, _h = call("GET", "/approvals/inbox", who=agent)
    ok("агент на /approvals/inbox → пускает (не 401/403)", st not in (401, 403), f"получено {st}")
    st, body, _h = call("GET", "/auth/me", who=agent)
    ok("агент на /auth/me → 200", st == 200, f"получено {st}")


def check_bearer():
    print("4. Bearer вместо cookie")
    agent = PEOPLE[0][0]
    st, _b, _h = call("GET", "/auth/me", headers={"authorization": "Bearer " + TOKENS[agent]})
    ok("Authorization: Bearer <токен> — вход есть", st == 200, f"получено {st}")
    st, _b, _h = call("GET", "/auth/me", headers={"authorization": "Bearer подделка"})
    ok("чужой Bearer не пускает", st == 401, f"получено {st}")


def check_dev_mode():
    print("5. Режим разработчика")
    os.environ["SURVEYOR_DEV"] = "1"
    try:
        st, _b, _h = call("GET", "/audit", host="127.0.0.1")
        ok("SURVEYOR_DEV=1 и 127.0.0.1 — пропускает", st == 200, f"получено {st}")
        st, _b, _h = call("GET", "/docs", host="127.0.0.1")
        ok("SURVEYOR_DEV=1: /docs открыт локально", st == 200, f"получено {st}")
        st, _b, _h = call("GET", "/audit", host="203.0.113.7")
        ok("чужой адрес не пропускает даже в режиме разработчика", st == 401, f"получено {st}")
        st, _b, _h = call("GET", "/audit", host="127.0.0.1",
                          headers={"x-forwarded-for": "127.0.0.1"})
        ok("подделка X-Forwarded-For не открывает лазейку", st == 401, f"получено {st}")
    finally:
        os.environ.pop("SURVEYOR_DEV", None)
    st, _b, _h = call("GET", "/audit", host="127.0.0.1")
    ok("без SURVEYOR_DEV локальный адрес тоже закрыт", st == 401, f"получено {st}")


def check_bootstrap():
    print("6. Первый администратор")
    st, body, _h = call("GET", "/auth/bootstrap-needed")
    ok("/auth/bootstrap-needed отвечает без входа", st == 200 and body.get("needed") is False,
       f"получено {st} {body}")
    st, body, _h = call("POST", "/auth/bootstrap",
                        {"code": "любой", "full_name": "Кто-то Кто-то", "login": "guardboot", "password": "12345678"})
    ok("при непустой базе код не создаёт второго админа", st == 409, f"получено {st} {body}")
    with db.tx() as con:
        left = con.execute("SELECT COUNT(*) FROM users WHERE login='guardboot'").fetchone()[0]
    ok("пользователь guardboot не создан", left == 0)
    ok("код сравнивается по отпечатку", guard._same("Секрет", "Секрет") and not guard._same("Секрет", "секрет"))
    ok("пустой код не проходит", not guard._same("", ""))


def check_bootstrap_empty():
    """Пустой сервер — на временной копии базы, рабочую не трогаем."""
    print("7. Пустой сервер: первый администратор по коду")
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "surveyor-guard.db"
    db.snapshot(db.ROOT / "data" / "surveyor.db", tmp)   # штатная копия SQLite: учитывает WAL
    original = db.DB_PATH
    db.DB_PATH = tmp
    # проверяем именно пустой сервер: файлы .env / .secrets.env администратора и окружение
    # на время отключаем, иначе заданный там ADMIN_BOOTSTRAP_CODE подменит сгенерированный
    env_files, env_code = llm.ENV_FILES, os.environ.pop("ADMIN_BOOTSTRAP_CODE", None)
    llm.ENV_FILES = ()
    try:
        with db.tx() as con:
            con.execute("DELETE FROM login_codes")
            con.execute("DELETE FROM sessions")
            con.execute("DELETE FROM users")
            con.execute("DELETE FROM app_settings WHERE key IN ('ADMIN_BOOTSTRAP_CODE','ADMIN_BOOTSTRAP_USED')")
        code = guard.ensure_bootstrap_code()
        ok("на пустой базе код выдан и напечатан", bool(code) and len(code) >= 12, str(code))
        ok("повторный запуск код не меняет", guard.ensure_bootstrap_code() is None)
        with db.tx() as con:
            st = expect_http(403, guard.bootstrap_admin, con,
                             guard.BootstrapIn(code="не тот", full_name="Кто Кто", login="boot1", password="12345678"))
        ok("неверный код — 403", st == 403)
        with db.tx() as con:
            out = guard.bootstrap_admin(con, guard.BootstrapIn(code=code, full_name="Первый Администратор",
                                                               login="boot1", password="пароль-12345"))
        ok("администратор создан и сразу активен", out["role"] == "админ" and out["status"] == "активен", str(out))
        with db.tx() as con:
            st = expect_http(409, guard.bootstrap_admin, con,
                             guard.BootstrapIn(code=code, full_name="Второй Админ", login="boot2", password="12345678"))
        ok("второй раз тем же кодом — отказ (администратор уже есть)", st == 409)
        with db.tx() as con:
            con.execute("DELETE FROM users")          # даже на снова пустой базе код уже погашен
            st = expect_http(403, guard.bootstrap_admin, con,
                             guard.BootstrapIn(code=code, full_name="Третий Админ", login="boot3", password="12345678"))
        ok("код одноразовый: после использования не работает", st == 403)
    finally:
        db.DB_PATH = original
        db.close_pool()                 # соединения пула держат файл копии открытым
        llm.ENV_FILES = env_files
        if env_code is not None:
            os.environ["ADMIN_BOOTSTRAP_CODE"] = env_code
        try:
            tmp.unlink()
            tmp.parent.rmdir()
        except OSError:
            pass


def expect_http(status, fn, *args):
    from fastapi import HTTPException
    try:
        fn(*args)
    except HTTPException as e:
        return e.status_code
    return None


def main():
    print("Единый вход (app/guard.py)")
    setup()
    try:
        check_no_session()
        check_open()
        check_roles()
        check_bearer()
        check_dev_mode()
        check_bootstrap()
        check_bootstrap_empty()
    finally:
        teardown()
    print(f"\nитог: успешно {passed}, ошибок {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
