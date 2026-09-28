"""
Гостевой режим: приложение открыто для всех, вход нужен только администратору
(решение заказчика 22.09.2026).

Проверяем без живого сервера — своим ASGI-клиентом (pytest и httpx в sandbox\\.venv не стоят,
поэтому обычные assert и печать «ок / ПЛОХО»). Живой сервер не трогаем и не перезапускаем,
рабочую базу не меняем: всё идёт во временную копию (tests/tmpdb.py).

Запуск из корня проекта:
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_guest.py

Что проверяется:
  1. без сессии 200: /tg/me (гость), /analytics/risk (быстрый режим), /osgor/quick, /calculate,
     /legal/ask, /reference/*, GET /osgor/brv, /analytics/risk/fields;
  2. контракт /tg/me для гостя: mode=guest, user=None, nav без «Пользователей», can_edit пуст,
     admin_available=false, login_url;
  3. закрыто без входа: PUT /osgor/brv, PUT /analytics/risk/thresholds, /admin/hub, /tasks,
     /audit, /users, /portfolio, /deploy/status, POST /market/refresh;
  4. guest_id: свой файл виден, чужой — 404, без cookie — 404;
  5. лимит обращений гостя: после исчерпания — 429 с понятным текстом;
  6. POST /tg/register/submit — 410 «Регистрация не требуется…».
"""
import asyncio
import json as _json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.pop("SURVEYOR_DEV", None)     # гостя проверяем при включённом guard
os.environ["LEX_LIVE"] = "0"              # 200 вопросов подряд не должны идти на lex.uz (сеть в тестах не нужна)

from tmpdb import temp_db                                  # noqa: E402
from app import analysis_docs as adocs                     # noqa: E402
from app import db, guard, guest                           # noqa: E402
from app.main import app                                   # noqa: E402

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

def call(method: str, path: str, body=None, params=None, gid=None, headers=None, html=False):
    """Вызов приложения по ASGI. gid — cookie анонимного гостя (None — без cookie)."""
    query = urlencode(params or {}, encoding="utf-8")
    payload = _json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
    hdrs = [(b"host", b"test"), (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode())]
    if html:
        hdrs.append((b"accept", b"text/html,application/xhtml+xml"))
    if gid:
        hdrs.append((b"cookie", f"{guest.COOKIE}={gid}".encode()))
    for k, v in (headers or {}).items():
        hdrs.append((k.lower().encode(), v.encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(), "headers": hdrs,
             "client": ("203.0.113.7", 0), "server": ("test", 80)}
    out = {"status": None, "chunks": [], "headers": []}

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = [(k.decode().lower(), v.decode()) for k, v in msg.get("headers", [])]
        elif msg["type"] == "http.response.body":
            out["chunks"].append(msg.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    raw = b"".join(out["chunks"]).decode("utf-8", "replace")
    try:
        return out["status"], _json.loads(raw), out["headers"]
    except ValueError:
        return out["status"], raw, out["headers"]


# ---------- 1. открыто без входа ----------

QUICK_RISK = {"mode": "quick",
              "must": {"class_code": "8", "sum_insured": 500_000_000, "object_value": 600_000_000,
                       "region": "Ташкент"},
              "optional": {}}


def check_open():
    print("1. Без входа открыты чтение и расчёт")
    st, me, hdrs = call("GET", "/tg/me")
    ok("/tg/me отвечает 200", st == 200, (st, me))
    ok("выдана cookie гостя", any(k == "set-cookie" and k2.startswith(guest.COOKIE + "=")
                                 for k, k2 in hdrs), hdrs)
    st, b, _ = call("GET", "/analytics/risk/fields", params={"class_code": "8", "mode": "quick"})
    ok("/analytics/risk/fields 200", st == 200, st)
    st, b, _ = call("POST", "/analytics/risk", QUICK_RISK)
    ok("POST /analytics/risk (быстрый режим) 200", st == 200 and b.get("ok") is not False, (st, b))
    st, b, _ = call("POST", "/osgor/quick", {"okved": "41100", "payroll": 1_000_000_000,
                                             "term_days": 365})
    ok("POST /osgor/quick 200", st == 200, (st, b))
    st, b, _ = call("GET", "/osgor/activities", params={"q": "строит"})
    ok("GET /osgor/activities 200", st == 200, st)
    st, b, _ = call("GET", "/osgor/brv")
    ok("GET /osgor/brv 200 и can_edit=false", st == 200 and b.get("can_edit") is False, (st, b))
    st, b, _ = call("POST", "/calculate", {"product_code": "0102", "value_amount": 120_000_000,
                                           "sum_insured": 100_000_000, "term_days": 365,
                                           "factors": {}})
    ok("POST /calculate 200", st == 200, (st, b))
    st, b, _ = call("POST", "/legal/ask", {"q": "Какой срок выплаты по ОСГОР?"})
    ok("POST /legal/ask 200", st == 200, (st, str(b)[:120]))
    st, b, _ = call("GET", "/legal/faq")
    ok("GET /legal/faq 200", st == 200, st)
    st, b, _ = call("GET", "/reference/coefficients")
    ok("GET /reference/* 200", st == 200, st)
    st, b, _ = call("GET", "/valuation/norms")
    ok("GET /valuation/norms 200", st == 200, st)
    st, b, _ = call("GET", "/market/status")
    ok("GET /market/status 200", st == 200, st)
    st, b, _ = call("GET", "/analytics/risk/thresholds")
    ok("GET /analytics/risk/thresholds 200, can_edit=false",
       st == 200 and b.get("can_edit") is False, (st, b))
    st, page, _ = call("GET", "/tg", html=True)
    ok("страница /tg открыта гостю", st == 200, st)


# ---------- 2. контракт /tg/me ----------

def check_me_contract():
    print("2. Контракт /tg/me для гостя")
    st, me, _ = call("GET", "/tg/me")
    keys = [n["key"] for n in me.get("nav", [])]
    ok("mode=guest, status=гость, user=None",
       me.get("mode") == "guest" and me.get("status") == "гость" and me.get("user") is None, me)
    ok("nav = аналитика, расчёт, ОСГОР, юрист, фото",
       keys == ["analytics", "calc", "osgor", "legal", "photos"], keys)
    ok("«Пользователи» гостю не показываются", "users" not in keys, keys)
    ok("can_edit пуст, admin_available=false",
       me.get("can_edit") == [] and me.get("admin_available") is False, me)
    ok("есть login_url на админку", me.get("login_url") == "/login?next=/tg", me.get("login_url"))
    ok("права только на чтение и расчёт", bool(me.get("rights")) and "справочники" not in me["rights"],
       me.get("rights"))


# ---------- 3. закрыто без входа ----------

CLOSED = [("PUT", "/osgor/brv", {"value": 412000, "effective_from": "2026-01-01"}),
          ("PUT", "/analytics/risk/thresholds", {"reset": True}),
          ("GET", "/admin/hub", None),
          ("GET", "/tasks", None),
          ("POST", "/tasks", {"title": "тест"}),
          ("GET", "/audit", None),
          ("GET", "/users", None),
          ("GET", "/portfolio", None),
          ("GET", "/deploy/status", None),
          ("POST", "/market/refresh", None),
          ("POST", "/admin/coefficients", {}),
          ("POST", "/valuation/norms", {}),
          ("GET", "/tg/users", None),
          ("GET", "/analytics/summary", None)]


def check_closed():
    print("3. Изменяющее и админское закрыто без входа")
    for method, path, body in CLOSED:
        st, b, _ = call(method, path, body)
        ok(f"{method} {path} — {st}", st in (401, 403), (st, str(b)[:80]))


# ---------- 4. изоляция файлов по guest_id ----------

def check_isolation():
    print("4. Документы гостя: чужой guest_id не видит файл")
    mine, alien = guest.new_id(), guest.new_id()
    doc_id = "a" * 24
    with db.tx() as con:
        adocs.ensure_table(con)
        con.execute("INSERT INTO analysis_docs (id, user_id, owner_key, filename, mime, size, kind,"
                    " language, status, fields_json, created_at, expires_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (doc_id, 0, guest.owner_key(None, mine), "договор.pdf", adocs.MIME["pdf"], 10,
                     "договор", "ru", "ок", "{}", db.now(), "2999-01-01T00:00:00"))
    st, b, _ = call("GET", "/analytics/risk/document/" + doc_id, gid=mine)
    ok("свой файл виден владельцу guest_id", st == 200 and b.get("doc_id") == doc_id, (st, b))
    st, b, _ = call("GET", "/analytics/risk/documents", gid=mine)
    ok("список «Фото»: свой файл есть",
       st == 200 and [i["doc_id"] for i in b["items"]] == [doc_id], (st, b))
    st, b, _ = call("GET", "/analytics/risk/documents", gid=alien)
    ok("список «Фото»: чужих файлов нет", st == 200 and b["items"] == [], (st, b))
    st, b, _ = call("GET", "/analytics/risk/document/" + doc_id, gid=alien)
    ok("чужой guest_id — 404", st == 404, (st, b))
    st, b, _ = call("GET", "/analytics/risk/document/" + doc_id)
    ok("без cookie — 404", st == 404, (st, b))
    st, b, _ = call("DELETE", "/analytics/risk/document/" + doc_id, gid=alien)
    ok("чужое удаление — 404", st == 404, (st, b))
    st, b, _ = call("DELETE", "/analytics/risk/document/" + doc_id, gid=mine)
    ok("своё удаление — 200", st == 200, (st, b))


# ---------- 5. лимит обращений ----------

def check_limit():
    print("5. Лимит обращений гостя")
    guest.reset()
    gid = guest.new_id()
    limit = guest.LIMITS["legal"]
    codes = set()
    for i in range(limit + 2):
        st, b, _ = call("POST", "/legal/ask", {"q": "Вопрос номер %d о выплате" % i}, gid=gid)
        codes.add(st)
        if st == 429:
            last = b
            break
    ok("после лимита приходит 429", 429 in codes, codes)
    ok("в 429 понятный текст по-русски", isinstance(last, dict) and "час" in last.get("detail", ""),
       last)
    ok("указан лимит и когда повторить",
       last.get("limit") == limit and last.get("retry_after_sec", 0) > 0, last)
    # другой guest_id считается отдельно
    st, b, _ = call("POST", "/legal/ask", {"q": "Другой гость спрашивает"}, gid=guest.new_id())
    ok("другому гостю лимит не мешает", st == 200, (st, str(b)[:80]))
    guest.reset()


# ---------- 6. регистрация отключена ----------

def check_registration_off():
    print("6. Самостоятельная регистрация отключена")
    st, b, _ = call("POST", "/tg/register/submit", {"name": "Тест", "phone": "+998901112233"})
    ok("POST /tg/register/submit — 410", st == 410, (st, b))
    ok("текст про открытое приложение",
       isinstance(b, dict) and "Регистрация не требуется" in b.get("detail", ""), b)


def main():
    print("Гостевой режим (22.09.2026)\n")
    with temp_db("surveyor-guest-test.db"):
        db.ensure_schema()
        check_open()
        check_me_contract()
        check_closed()
        check_isolation()
        check_limit()
        check_registration_off()
    print(f"\nитог: ок {passed}, плохо {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
