"""
Тайна страхования: посторонний не видит чужой запрос и закрытые разделы.

Повторяет сценарий контролёра от 20.09.2026: человек регистрируется в мини-аппе (роль «сотрудник»),
не является автором запроса и не назначен согласующим — и до исправления получал список запросов
со страхователями, карточку, состав согласующих, генеральные соглашения и мог сменить статус
чужого запроса через POST /requests/{id}/decision.

Основания: ЗРУ-730 ст. 62 (тайна страхования), ГК ст. 933, ЗРУ-547 ст. 12/27/31,
docs/Регистрация и роли.md — разделы 6.1 и 8.

Запуск из корня проекта (pytest и httpx в sandbox\\.venv не стоят — свой ASGI-клиент,
живой сервер не трогаем и не перезапускаем):
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_access.py

Вся работа идёт во ВРЕМЕННОЙ копии базы (tests/tmpdb.py): рабочая data/surveyor.db не меняется.
"""
import asyncio
import json as _json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# guard проверяем целиком: режим разработчика открыл бы всё подряд
os.environ.pop("SURVEYOR_DEV", None)

from tmpdb import temp_db            # noqa: E402  (tests/tmpdb.py)
from app import approvals, auth, db  # noqa: E402
from app.main import app             # noqa: E402

PREFIX = "тест-доступ-"
# логин -> роль
PEOPLE = {PREFIX + "автор": "сотрудник", PREFIX + "чужой": "сотрудник",
          PREFIX + "согласующий": "сотрудник", PREFIX + "андеррайтер": "андеррайтер",
          PREFIX + "актуарий": "актуарий", PREFIX + "админ": "админ"}
AUTHOR, STRANGER, REVIEWER = PREFIX + "автор", PREFIX + "чужой", PREFIX + "согласующий"
UNDER, ACTUARY, ADMIN = PREFIX + "андеррайтер", PREFIX + "актуарий", PREFIX + "админ"

TOKENS = {}
UIDS = {}
POLICYHOLDER = "ООО «Тайна страхования»"

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

def call(method: str, path: str, body=None, params=None, who=None):
    query = urlencode(params or {}, encoding="utf-8")
    payload = _json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
    hdrs = [(b"host", b"test"), (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode())]
    if who:
        hdrs.append((b"cookie", f"sid={TOKENS[who]}".encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(), "headers": hdrs,
             "client": ("203.0.113.9", 0), "server": ("test", 80)}
    out = {"status": None, "chunks": []}

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["chunks"].append(msg.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    raw = b"".join(out["chunks"]).decode("utf-8", "replace")
    try:
        return out["status"], _json.loads(raw)
    except ValueError:
        return out["status"], raw


# ---------- подготовка ----------

def setup() -> int:
    """Заводит людей, один чужой запрос и состав согласующих. Возвращает id запроса."""
    db.ensure_schema()
    with db.tx() as con:
        ts = db.now()
        for login, role in PEOPLE.items():
            cur = con.execute("INSERT INTO users (login, full_name, role, branch, password_hash, salt,"
                              " status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                              (login, "Тестовый " + login, role, "тест-доступ", "x", "y", "активен", ts))
            UIDS[login] = cur.lastrowid
            u = db.rows(con, "SELECT * FROM users WHERE id=?", cur.lastrowid)[0]
            TOKENS[login], _ = auth.create_session(con, u, "203.0.113.9", "test")
        cur = con.execute("INSERT INTO requests (external_no, branch, product_code, policyholder,"
                          " created_by_user_id, created_at, status) VALUES (?,?,?,?,?,?,?)",
                          ("ТЕСТ-ДОСТУП-1", "тест-доступ", "0807", POLICYHOLDER, UIDS[AUTHOR], ts, "посчитан"))
        rid = cur.lastrowid
        approvals.assign(con, rid, [UIDS[REVIEWER]], who=AUTHOR)
        return rid


# ---------- сценарий контролёра: пять строк ----------

def check_stranger(rid):
    print(f"1. Посторонний «сотрудник» по чужому запросу № {rid}")

    st, body = call("GET", "/requests", who=STRANGER)
    seen = [r["id"] for r in body] if isinstance(body, list) else []
    ok("GET /requests — 200, но чужого запроса в списке нет",
       st == 200 and rid not in seen, f"получено {st}, видно {seen}")
    ok("в списке нет страхователя по чужому запросу",
       POLICYHOLDER not in _json.dumps(body, ensure_ascii=False), str(body)[:200])

    st, body = call("GET", f"/requests/{rid}", who=STRANGER)
    ok("GET /requests/{id} — 404 (не 403: 403 подсказал бы, что запрос существует)",
       st == 404, f"получено {st} {body}")

    st, body = call("GET", f"/requests/{rid}/approvals", who=STRANGER)
    ok("GET /requests/{id}/approvals — 404", st == 404, f"получено {st} {body}")

    st, body = call("GET", "/general-agreements", who=STRANGER)
    ok("GET /general-agreements — 403", st == 403, f"получено {st} {body}")

    st, body = call("POST", f"/requests/{rid}/decision", {"who": STRANGER, "decision": "отклонён"},
                    who=STRANGER)
    ok("POST /requests/{id}/decision — маршрута больше нет (405/404)", st in (404, 405),
       f"получено {st} {body}")
    with db.tx() as con:
        status = db.rows(con, "SELECT status, approval_status FROM requests WHERE id=?", rid)[0]
    ok("статус чужого запроса не изменился", status["status"] == "посчитан", str(status))

    st, body = call("POST", f"/requests/{rid}/decide", {"decision": "отклонил"}, who=STRANGER)
    ok("POST /requests/{id}/decide — 403 «вы не назначены согласующим»", st == 403,
       f"получено {st} {body}")


# ---------- закрытые разделы ----------

CLOSED_FOR_EMPLOYEE = [
    ("GET", "/portfolio/batches"), ("POST", "/portfolio/import"), ("GET", "/portfolio"),
    ("GET", "/calibration/exposure"), ("GET", "/calibration/runs"), ("POST", "/calibration/run"),
    ("POST", "/calibration/runs/1/approve"), ("POST", "/calibration/runs/1/reject"),
    ("GET", "/calibration"), ("GET", "/claims"), ("POST", "/claims"),
    ("GET", "/capacity"), ("GET", "/capacity-page"), ("GET", "/accumulation"),
    ("GET", "/analytics/summary"), ("GET", "/knowledge/topics"), ("GET", "/knowledge/log"),
    ("GET", "/law-events"), ("POST", "/lawwatch/check"), ("POST", "/market/refresh"),
    ("GET", "/office/shelves"), ("GET", "/general-agreements"),
]


def check_sections():
    print("2. Разделы, закрытые для роли «сотрудник»")
    bad = []
    for method, path in CLOSED_FOR_EMPLOYEE:
        st, _b = call(method, path, {} if method == "POST" else None, who=STRANGER)
        if st != 403:
            bad.append(f"{method} {path} → {st}")
    ok(f"все {len(CLOSED_FOR_EMPLOYEE)} закрытых разделов отвечают 403", not bad, "; ".join(bad))

    # утверждение версии калибровки — только админ, даже актуарию нельзя
    st, _b = call("POST", "/calibration/runs/1/approve", {}, who=ACTUARY)
    ok("POST /calibration/runs/{id}/approve — актуарию 403 (запись в справочники у админа)",
       st == 403, f"получено {st}")
    st, _b = call("GET", "/calibration/runs", who=ACTUARY)
    ok("GET /calibration/runs — актуарию открыт", st == 200, f"получено {st}")
    st, _b = call("GET", "/portfolio/batches", who=UNDER)
    ok("GET /portfolio/batches — андеррайтеру открыт", st == 200, f"получено {st}")
    st, _b = call("GET", "/analytics/summary", who=UNDER)
    ok("GET /analytics/summary — андеррайтеру открыт", st == 200, f"получено {st}")
    st, _b = call("GET", "/general-agreements", who=ADMIN)
    ok("GET /general-agreements — админу открыт", st == 200, f"получено {st}")


# ---------- свои видят ----------

def check_owner(rid):
    print("3. Автор, согласующий, андеррайтер и админ видят запрос по-прежнему")
    for who, name in ((AUTHOR, "автор"), (REVIEWER, "назначенный согласующий"),
                      (UNDER, "андеррайтер"), (ADMIN, "админ")):
        st, body = call("GET", f"/requests/{rid}", who=who)
        ok(f"GET /requests/{{id}} — {name}: 200",
           st == 200 and body.get("request", {}).get("id") == rid, f"получено {st} {str(body)[:120]}")
        st, body = call("GET", f"/requests/{rid}/approvals", who=who)
        ok(f"GET /requests/{{id}}/approvals — {name}: 200", st == 200, f"получено {st}")
        st, body = call("GET", "/requests", who=who)
        seen = [r["id"] for r in body] if isinstance(body, list) else []
        ok(f"GET /requests — {name} видит запрос в списке", rid in seen, f"получено {st}, видно {seen}")


def check_no_session(rid):
    print("4. Без входа не отдаём ничего")
    for method, path in (("GET", "/requests"), ("GET", f"/requests/{rid}"),
                         ("GET", f"/requests/{rid}/approvals"), ("GET", "/general-agreements")):
        st, body = call(method, path)
        ok(f"{method} {path} без сессии → 401", st == 401, f"получено {st} {body}")


def main():
    print("Тайна страхования: чужой запрос и закрытые разделы")
    with temp_db("surveyor-access.db"):
        rid = setup()
        check_no_session(rid)
        check_stranger(rid)
        check_sections()
        check_owner(rid)
    print(f"\nитог: успешно {passed}, ошибок {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
