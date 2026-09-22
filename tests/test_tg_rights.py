"""
Права в мини-приложении (задача 144, 21.09.2026): меню, правка справочников админом, БРВ для ОСГОР,
исключение аналитики риска из раздела «/analytics», регистрация по имени.

Запуск из корня проекта (живой сервер не трогаем, рабочая база не меняется — временная копия):
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_tg_rights.py

Что проверяется:
  1. /tg/me сотрудника: «Аналитика» первой, «ОСГОР» после «Расчёта», «Мои запросы» в меню нет,
     is_admin = false, can_edit пуст; у админа is_admin = true и can_edit со всеми справочниками;
     /tg/my-requests по-прежнему отвечает;
  2. сотрудник: 403 на PUT /osgor/brv, PUT /analytics/risk/thresholds, POST /admin/coefficients,
     DELETE /valuation/norms/{код}; админ проходит;
  3. аналитика риска (GET /analytics/risk, /fields, /thresholds) guard'ом открыта любой роли,
     /analytics/summary для сотрудника по-прежнему закрыта;
  4. БРВ: пусто, пока админ не ввёл; версия по дате; неверный ввод — 422; в журнале логин админа;
  5. регистрация: имя «Жасур» проходит, «Ж» — нет; старое поле full_name принимается.
"""
import asyncio
import json as _json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.pop("SURVEYOR_DEV", None)          # guard проверяем по-настоящему

from tmpdb import temp_db  # noqa: E402  (tests/tmpdb.py)
from app import auth, db, guard, registration, tgbot   # noqa: E402
from app.main import app                              # noqa: E402

PREFIX = "тест-права-"
PEOPLE = [(PREFIX + "сотрудник", "сотрудник"), (PREFIX + "админ", "админ")]
EMP, ADM = PEOPLE[0][0], PEOPLE[1][0]
TOKENS = {}
passed, failed = 0, 0


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ок  ", name)
    else:
        failed += 1
        print("  ПЛОХО", name, extra)


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
    raw = b"".join(out["chunks"]).decode("utf-8", "replace")
    try:
        return out["status"], _json.loads(raw)
    except ValueError:
        return out["status"], raw


def setup():
    db.ensure_schema()
    with db.tx() as con:
        ts = db.now()
        for login, role in PEOPLE:
            cur = con.execute("INSERT INTO users (login, full_name, role, branch, password_hash, salt,"
                              " status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                              (login, "Тест " + role, role, "тест-права", "x", "y", "активен", ts))
            u = db.rows(con, "SELECT * FROM users WHERE id=?", cur.lastrowid)[0]
            TOKENS[login], _ = auth.create_session(con, u, "127.0.0.1", "test")
        con.execute("DELETE FROM osgor_brv")      # копия рабочей базы: начинаем с пустого справочника
        # и с пустого журнала по БРВ: в рабочей базе уже есть правки заказчика, проверка «кто вписал» — о тесте
        con.execute("DELETE FROM audit WHERE entity LIKE 'osgor_brv:%'")


# ---------- 1. меню ----------

def check_menu():
    print("1. /tg/me и меню")
    st, me = call("GET", "/tg/me", who=EMP)
    keys = [n["key"] for n in me.get("nav", [])] if st == 200 else []
    ok("сотрудник: /tg/me 200", st == 200, (st, me))
    ok("«Аналитика» — первый пункт", keys[:1] == ["analytics"], keys)
    ok("«ОСГОР» сразу после «Расчёта»", "calc" in keys and keys[keys.index("calc") + 1:][:1] == ["osgor"], keys)
    ok("«Мои запросы» в меню нет", "my-requests" not in keys, keys)
    ok("«Ждут меня» у сотрудника не появилось", "inbox" not in keys, keys)
    ok("сотрудник: меню ровно analytics, calc, osgor, legal, photos, users",
       keys == ["analytics", "calc", "osgor", "legal", "photos", "users"], keys)
    ok("is_admin = false, can_edit пуст",
       me["user"]["is_admin"] is False and me["can_edit"] == [], (me["user"], me.get("can_edit")))
    ok("user.name = full_name", me["user"]["name"] == me["user"]["full_name"], me["user"])
    st, _ = call("GET", "/tg/my-requests", who=EMP)
    ok("/tg/my-requests остался и отвечает", st == 200, st)

    st, me = call("GET", "/tg/me", who=ADM)
    keys = [n["key"] for n in me["nav"]]
    ok("админ: is_admin = true", me["user"]["is_admin"] is True, me["user"])
    ok("админ: can_edit — все справочники",
       me["can_edit"] == [e["key"] for e in tgbot.EDITABLE] and "osgor_brv" in me["can_edit"]
       and "risk_thresholds" in me["can_edit"], me["can_edit"])
    ok("админ: «Мои запросы» нет", "my-requests" not in keys, keys)
    # задача 150: «Ждут меня», «Заявки», «Соглашения» убраны из меню для всех ролей
    ok("админ: нет «Ждут меня», «Заявок», «Соглашений»",
       not {"inbox", "applications", "agreements"} & set(keys), keys)
    ok("админ: меню = база + «Настройки»",
       keys == ["analytics", "calc", "osgor", "legal", "photos", "users", "settings"], keys)
    st, _ = call("GET", "/tg/inbox", who=ADM)
    ok("/tg/inbox как точка остался", st == 200, st)


# ---------- 2–3. права ----------

COEF = {"factor_code": "тест_права", "factor_name": "Тест прав", "class_code": "8",
        "option_code": "x", "option_name": "Вариант", "multiplier": 1.0, "calibrated": 0, "source": "тест"}
BRV = {"value": 100000, "effective_from": "2026-01-01", "source": "тестовый акт"}


def check_rights():
    print("2. Правка справочников: сотрудник — 403, админ — проходит")
    for method, path, body in (("PUT", "/osgor/brv", BRV),
                               ("PUT", "/analytics/risk/thresholds", {"green": 1}),
                               ("POST", "/admin/coefficients", COEF),
                               ("DELETE", "/valuation/norms/тест-нет-такой", None),
                               ("POST", "/valuation/norms", {"code": "т", "name": "т", "rate_pct": 1})):
        st, b = call(method, path, body, who=EMP)
        ok(f"сотрудник {method} {path} → 403", st == 403, (st, b))

    st, b = call("POST", "/admin/coefficients", COEF, who=ADM)
    ok("админ POST /admin/coefficients → 200", st == 200, (st, b))
    st, b = call("DELETE", "/valuation/norms/тест-нет-такой", who=ADM)
    ok("админ DELETE /valuation/norms/{код} проходит guard (404 — нормы нет)", st == 404, (st, b))
    # обработчика порогов ещё нет (этап 2) — guard админа пропускает, ответ даёт маршрутизатор
    st, b = call("PUT", "/analytics/risk/thresholds", {"green": 1}, who=ADM)
    ok("админ PUT /analytics/risk/thresholds не остановлен guard'ом", st not in (401, 403), (st, b))

    print("3. Аналитика риска открыта любой роли, сводка — нет")
    for path in ("/analytics/risk", "/analytics/risk/fields", "/analytics/risk/thresholds"):
        st, b = call("GET", path, who=EMP)
        ok(f"сотрудник GET {path} не 403", st not in (401, 403), (st, b))
        ok(f"guard: {path} без ограничения роли", guard.allowed_roles(path) is None)
    st, b = call("GET", "/analytics/summary", who=EMP)
    ok("сотрудник GET /analytics/summary → 403", st == 403, (st, b))
    ok("guard: /analytics/risk/что-то-ещё закрыта ролями",
       guard.allowed_roles("/analytics/risk/export") == guard.UNDERWRITING)
    ok("guard: PUT порогов и БРВ — админ", guard.needs_admin("PUT", "/analytics/risk/thresholds")
       and guard.needs_admin("PUT", "/osgor/brv") and not guard.needs_admin("GET", "/osgor/brv"))


# ---------- 4. БРВ ----------

def check_brv():
    print("4. БРВ для ОСГОР")
    st, b = call("GET", "/osgor/brv", who=EMP)
    ok("пока админ не ввёл — value = null, ничего не выдумано",
       st == 200 and b["value"] is None and b["history"] == [] and b["can_edit"] is False, (st, b))
    for bad in ({**BRV, "value": -5}, {**BRV, "value": 0}, {**BRV, "effective_from": "01.01.2026"},
                {**BRV, "source": ""}, {**BRV, "value": "много"}):
        st, b = call("PUT", "/osgor/brv", bad, who=ADM)
        ok(f"неверный ввод отклонён ({st})", st == 422, (bad, st, b))

    st, b = call("PUT", "/osgor/brv", BRV, who=ADM)
    ok("админ PUT /osgor/brv → 200", st == 200 and b["value"] == 100000.0, (st, b))
    st, b = call("PUT", "/osgor/brv", {**BRV, "value": 120000, "effective_from": "2099-01-01"}, who=ADM)
    ok("будущая версия записана", st == 200, (st, b))

    st, b = call("GET", "/osgor/brv", who=EMP)
    ok("сотрудник видит действующее значение и будущее",
       st == 200 and b["value"] == 100000.0 and b["next"]["value"] == 120000.0 and len(b["history"]) == 2, b)
    st, b = call("GET", "/osgor/brv", params={"on": "2025-06-01"}, who=EMP)
    ok("на дату до первой версии — пусто (старый расчёт не получает новое значение)",
       st == 200 and b["value"] is None, b)
    st, b = call("GET", "/osgor/brv", params={"on": "2099-02-01"}, who=EMP)
    ok("на дату после второй версии — вторая", st == 200 and b["value"] == 120000.0, b)
    st, b = call("GET", "/osgor/brv", params={"on": "не дата"}, who=EMP)
    ok("неверная дата запроса — 422", st == 422, (st, b))

    with db.tx() as con:
        who = {r["who"] for r in db.rows(con, "SELECT who FROM audit WHERE entity LIKE 'osgor_brv:%'")}
        by = {r["entered_by"] for r in db.rows(con, "SELECT entered_by FROM osgor_brv")}
    ok("в журнале и в записи — логин реального админа, не «admin»", who == {ADM} and by == {ADM}, (who, by))


# ---------- 5. имя при регистрации ----------

def expect_422(fn, *args):
    from fastapi import HTTPException
    try:
        fn(*args)
    except HTTPException as e:
        return e.status_code == 422
    return False


def check_name():
    print("5. Регистрация по имени")
    ok("«Жасур» проходит", registration.check_full_name("Жасур") == "Жасур")
    ok("пробелы схлопываются", registration.check_full_name("  Жасур   ака ") == "Жасур ака")
    ok("«Ж» — отказ 422", expect_422(registration.check_full_name, "Ж"))
    ok("« Ж » после схлопывания — отказ 422", expect_422(registration.check_full_name, "  Ж  "))
    ok("старое поле full_name принимается как синоним",
       registration.name_of(registration.SubmitIn(full_name="Жасур")) == "Жасур")
    ok("name важнее full_name",
       registration.name_of(registration.SubmitIn(name="Жасур", full_name="Старое")) == "Жасур")

    # целиком через register(): путь «вход из браузера» (via_link) — код на телефон не нужен
    real = (tgbot._deliver, tgbot.notify_admins_new_user)
    tgbot._deliver = lambda method, payload: {"ok": True, "result": {"message_id": 1}}
    tgbot.notify_admins_new_user = lambda *a, **k: None
    try:
        form = dict(phone="+998900000144", department="Тест", position="менеджер",
                    consent=True, consent_phone=True)
        with db.tx() as con:
            ok("register(): «Ж» — отказ 422",
               expect_422(registration.register, con, "99144001", registration.SubmitIn(name="Ж", **form), True))
        with db.tx() as con:
            out = registration.register(con, "99144001", registration.SubmitIn(name="Жасур", **form), True)
            u = db.rows(con, "SELECT full_name, role, status FROM users WHERE id=?", out["user_id"])[0]
        ok("register(): «Жасур» — активный сотрудник, users.full_name = «Жасур»",
           u == {"full_name": "Жасур", "role": "сотрудник", "status": "активен"}, u)
    finally:
        tgbot._deliver, tgbot.notify_admins_new_user = real


# ---------- 6. второй уровень прав: режим разработчика не открывает запись сотруднику ----------

def check_dev_rights():
    print("6. Режим разработчика с 127.0.0.1: сотрудник — 403 на запись в справочники")
    os.environ["SURVEYOR_DEV"] = "1"          # guard пропускает локальный запрос — проверяет сам обработчик
    try:
        for method, path, body in (("POST", "/admin/coefficients", COEF),
                                   ("POST", "/admin/min-rates", {"tariff_version_id": 1, "product_code": "0807",
                                                                 "min_rate_pct": 1}),
                                   ("POST", "/valuation/norms", {"code": "т", "name": "т", "rate_pct": 1}),
                                   ("DELETE", "/valuation/norms/тест-нет-такой", None),
                                   ("POST", "/valuation/settings", {"key": "т", "value": "1"})):
            st, b = call(method, path, body, who=EMP)
            ok(f"dev: сотрудник {method} {path} → 403", st == 403, (st, b))
        st, b = call("POST", "/admin/coefficients", COEF, who=ADM)
        ok("dev: админ POST /admin/coefficients → 200", st == 200, (st, b))
        st, b = call("POST", "/admin/coefficients", COEF)
        ok("dev: без сессии POST /admin/coefficients → 401", st == 401, (st, b))
    finally:
        os.environ.pop("SURVEYOR_DEV", None)


# ---------- 7. ОСГОР без БРВ: одно предупреждение ----------

def check_brv_single_note():
    print("7. ОСГОР без БРВ — предупреждение одно")
    st, b = call("POST", "/osgor/quick", {"okved": "41100", "payroll": 1e9, "contract_date": "2025-06-01"}, who=EMP)
    dup = [n for n in b.get("notes", []) if "БРВ не передан" in n] if st == 200 else None
    ok("/osgor/quick: note про БРВ есть, в notes дубля нет", st == 200 and b["brv"] is None and b["note"]
       and dup == [], (st, b.get("note"), b.get("notes")))
    st, b = call("POST", "/osgor/assess", {"okved": "41100", "headcount": 10, "payroll_12m": 1e9,
                                           "contract_date": "2025-06-01"}, who=EMP)
    notes = b.get("premium", {}).get("notes", []) if st == 200 else None
    ok("/osgor/assess: note про БРВ есть, в premium.notes дубля нет",
       st == 200 and b["note"] and not [n for n in notes if "БРВ не передан" in n], (st, b.get("note"), notes))
    st, b = call("POST", "/osgor/quick", {"okved": "41100", "payroll": 1e9, "brv": 100000}, who=EMP)
    ok("/osgor/quick с БРВ: note пуст", st == 200 and b["note"] == "", (st, b.get("note")))


def main():
    print("Права в мини-приложении (задача 144)")
    setup()
    check_menu()
    check_rights()
    check_brv_single_note()
    check_brv()
    check_name()
    check_dev_rights()
    print(f"\nИтого: пройдено {passed}, не пройдено {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    with temp_db("surveyor-tg-rights.db"):      # рабочая data/surveyor.db не меняется
        code = main()
    sys.exit(code)
