"""
Коллективное согласование запросов: 1–3 человека на один запрос.

С 21.09.2026 мини-апп только для аналитики: серверное согласование работает, но в Telegram
ничего не уходит. Проверяем подменой отправки: у всех участников есть telegram_id, бот «подключён»,
а исходящих — 0.

Запуск из корня проекта (pytest и httpx в sandbox\\.venv не установлены, поэтому обычные assert
и свой крошечный ASGI-клиент — живой сервер не трогаем и не перезапускаем):
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_approvals.py

Тестовые записи (запрос с branch='тест', пользователи 'тест-*', соглашение «Тест-Банк»)
удаляются в конце — рабочая база остаётся чистой.
"""
import asyncio
import json as _json
import os
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Тест ходит в приложение напрямую с адреса 127.0.0.1 — единый вход (app/guard.py)
# пропускает локальные соединения только в режиме разработчика.
os.environ.setdefault("SURVEYOR_DEV", "1")

from tmpdb import temp_db  # noqa: E402  (tests/tmpdb.py)
from app import db            # noqa: E402
from app import approvals     # noqa: E402
from app import auth          # noqa: E402
from app import team          # noqa: E402
from app import tgbot         # noqa: E402
from app.main import app      # noqa: E402

BRANCH = "тест"
# логин -> (роль, ID агента в ЕАИС); обхода входа в коде нет, тест заводит настоящие сессии
LOGINS = ["тест-согл-1", "тест-согл-2", "тест-согл-3", "тест-чужой", "тест-админ", "тест-агент", "тест-иниц",
          "тест-агент2"]
ROLES = ["андеррайтер", "андеррайтер", "андеррайтер", "андеррайтер", "админ", "агент", "андеррайтер", "агент"]
EAIS, EAIS2 = "ТЕСТ-EAIS-1", "ТЕСТ-EAIS-2"
TOKENS = {}
TG_BASE = 980100            # telegram_id участников: base + номер (заведомо не настоящие)
OUT = []                    # перехваченные исходящие в Telegram: (метод, данные)


# ---------- минимальный ASGI-клиент (httpx в окружении нет) ----------

def call(method: str, path: str, body=None, params=None, who=None):
    """Вызывает роут приложения напрямую по ASGI от имени вошедшего who. Возвращает (статус, тело)."""
    query = urlencode(params or {}, encoding="utf-8")
    payload = _json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(), "headers": [(b"host", b"test"), (b"content-type", b"application/json"),
                                                         (b"content-length", str(len(payload)).encode())]
                        + ([(b"cookie", f"sid={TOKENS[who]}".encode())] if who else []),
             "client": ("127.0.0.1", 0), "server": ("test", 80)}
    out = {"status": None, "chunks": [], "ctype": ""}

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["ctype"] = dict(msg.get("headers") or {}).get(b"content-type", b"").decode()
        elif msg["type"] == "http.response.body":
            out["chunks"].append(msg.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    raw = b"".join(out["chunks"]).decode("utf-8")
    try:
        return out["status"], _json.loads(raw)
    except ValueError:
        return out["status"], raw


def setup():
    db.ensure_schema()
    with db.tx() as con:
        ts = db.now()
        uids = []
        for i, (login, role) in enumerate(zip(LOGINS, ROLES), 1):
            con.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE login=?)", (login,))
            con.execute("DELETE FROM users WHERE login=?", (login,))
            cur = con.execute("INSERT INTO users (login, full_name, role, branch, agent_eais_id, password_hash, salt,"
                              " status, telegram_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (login, f"Тестовый Согласующий {i}", role, BRANCH,
                               {"тест-агент": EAIS, "тест-иниц": EAIS, "тест-агент2": EAIS2}.get(login),
                               "x", "y", "активен", str(TG_BASE + i), ts))
            uids.append(cur.lastrowid)
        con.execute("UPDATE users SET status='заблокирован' WHERE id=?", (uids[2],))   # на нём проверяем валидацию
        # «тест-иниц» — тот, кто подал запрос: связь через ID агента в ЕАИС
        con.execute("DELETE FROM agents WHERE eais_id IN (?,?)", (EAIS, EAIS2))
        con.execute("INSERT INTO agents (eais_id, name, kind, status) VALUES (?,?,?,?)",
                    (EAIS2, "Тестовый агент 2", "физическое лицо", "активен"))
        cur = con.execute("INSERT INTO agents (eais_id, name, kind, status) VALUES (?,?,?,?)",
                          (EAIS, "Тестовый агент", "физическое лицо", "активен"))
        agent_id = cur.lastrowid
        cur = con.execute("INSERT INTO requests (external_no, branch, policyholder, agent_id, created_at)"
                          " VALUES (?,?,?,?,?)", ("ТЕСТ-СОГЛ-1", BRANCH, "ООО «Тест»", agent_id, ts))
        rid = cur.lastrowid
        # настоящие сессии вместо обхода входа: активные пользователи получают cookie «sid»
        for login, uid in zip(LOGINS, uids):
            u = db.rows(con, "SELECT * FROM users WHERE id=?", uid)[0]
            if u["status"] == "активен":
                TOKENS[login], _ = auth.create_session(con, u, "127.0.0.1", "test")
        return rid, uids


def teardown(rid):
    with db.tx() as con:
        con.execute("DELETE FROM request_reviewers WHERE request_id=?", (rid,))
        con.execute("DELETE FROM decision_outcomes WHERE request_id=?", (rid,))
        approvals.recalc(con, rid)          # состава больше нет — статус обязан стать «не требуется»
        con.execute("DELETE FROM requests WHERE id=?", (rid,))
        for login in LOGINS:
            con.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE login=?)", (login,))
            con.execute("DELETE FROM audit WHERE who=?", (login,))
            con.execute("DELETE FROM users WHERE login=?", (login,))
        con.execute("DELETE FROM agents WHERE eais_id IN (?,?)", (EAIS, EAIS2))
        con.execute("DELETE FROM audit WHERE entity=?", (f"request:{rid}",))
        con.execute("DELETE FROM general_agreements WHERE partner=?", ("Тест-Банк",))
        # страховка: запрос без согласующих не должен висеть «на согласовании»
        for r in db.rows(con, "SELECT id FROM requests WHERE approval_status=?", "на согласовании"):
            if not db.rows(con, "SELECT 1 FROM request_reviewers WHERE request_id=?", r["id"]):
                approvals.recalc(con, r["id"])
        left = con.execute("SELECT COUNT(*) FROM requests WHERE branch=?", (BRANCH,)).fetchone()[0]
        hang = con.execute("SELECT COUNT(*) FROM requests WHERE approval_status=? AND id NOT IN"
                           " (SELECT request_id FROM request_reviewers)", ("на согласовании",)).fetchone()[0]
    print(f"  очищено; запросов с branch='{BRANCH}' осталось: {left}; висит без состава: {hang}")


def run(rid, uids):
    boss, admin = LOGINS[0], LOGINS[4]      # от чьего имени назначаем состав и заводим генсоглашения

    # 0. без входа ничего записать нельзя
    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": [uids[0], uids[1]]})
    assert st == 401, (st, b)
    st, b = call("POST", f"/requests/{rid}/decide", {"decision": "одобрил"})
    assert st == 401, (st, b)
    st, b = call("POST", "/general-agreements", {"partner": "Тест-Банк"})
    assert st == 401, (st, b)
    st, b = call("POST", "/general-agreements", {"partner": "Тест-Банк"}, who=boss)
    assert st == 403, (st, b)               # андеррайтер генсоглашения не заводит
    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": [uids[0], uids[1]]}, who=LOGINS[7])
    assert st == 403 and "своему запросу" in b["detail"], (st, b)   # чужой агент состав не трогает
    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": [uids[0], uids[1]]}, who=LOGINS[5])
    assert st == 200, (st, b)               # агент, подавший запрос, состав назначает
    print("0. без входа — 401, чужая роль и чужой запрос — 403 — ок")

    # 1. состав: от 1 до 3 активных человек (с 21.09.2026 одного рассматривающего достаточно)
    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": [uids[0]]}, who=boss)
    assert st == 200 and len(b["reviewers"]) == 1, (st, b)
    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": []}, who=boss)
    assert st == 400 and "от 1 до 3" in b["detail"], (st, b)
    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": [uids[0], uids[1], uids[1], uids[3], 999999]}, who=boss)
    assert st == 400, (st, b)                                  # четверо после снятия дублей
    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": [uids[0], uids[2]]}, who=boss)
    assert st == 400 and "не активен" in b["detail"], (st, b)
    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": [uids[0], 999999]}, who=boss)
    assert st == 400 and "не найден" in b["detail"], (st, b)
    print("1. валидация состава (1–3, активные, существующие) — ок")

    # 1а. правовые ограничения на состав
    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": [uids[0], uids[5]]}, who=boss)
    assert st == 400 and "3845" in b["detail"], (st, b)         # агент — посредник, а не работник компании
    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": [uids[0], uids[6]]}, who=boss)
    assert st == 400 and "сам себя" in b["detail"], (st, b)     # инициатор запроса себя не согласует
    print("1а. агент не согласует (3845 п. 2), инициатор не согласует сам себя — ок")

    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": [uids[0], uids[1]]}, who=boss)
    assert st == 200, (st, b)
    assert b["approval_status"] == "на согласовании"
    assert [x["order_no"] for x in b["reviewers"]] == [1, 2]
    assert b["reviewers"][0]["full_name"] == "Тестовый Согласующий 1"
    assert b["reviewers"][0]["position"] == "андеррайтер"      # должность скопирована на момент назначения
    assert all(x["status"] == "ожидает" for x in b["reviewers"])
    print("2. состав назначен, ФИО и должность скопированы — ок")

    # 2. решать может только назначенный и только от своего имени
    st, b = call("POST", f"/requests/{rid}/decide", {"decision": "одобрил"}, who=LOGINS[3])
    assert st == 403, (st, b)
    st, b = call("POST", f"/requests/{rid}/decide", {"decision": "может быть"}, who=LOGINS[0])
    assert st == 400, (st, b)
    st, b = call("POST", f"/requests/{rid}/decide", {"who": LOGINS[1], "decision": "одобрил"}, who=LOGINS[3])
    assert st == 403 and "вошедшего" in b["detail"], (st, b)   # подписаться чужой фамилией нельзя
    print("3. чужой — 403, кривое решение — 400, чужая фамилия в теле — 403 — ок")

    # 3. итоговый статус
    st, b = call("POST", f"/requests/{rid}/decide", {"decision": "одобрил", "comment": "ок"}, who=LOGINS[0])
    assert st == 200 and b["approval_status"] == "на согласовании", (st, b)
    st, b = call("POST", f"/requests/{rid}/decide", {"decision": "вопрос", "comment": "нужен акт"}, who=LOGINS[1])
    assert b["approval_status"] == "на согласовании", b
    st, b = call("POST", f"/requests/{rid}/decide", {"decision": "одобрил"}, who=LOGINS[1])
    assert b["approval_status"] == "согласован", b             # повторное решение перебивает своё прежнее
    st, b = call("POST", f"/requests/{rid}/decide", {"decision": "отклонил", "comment": "износ"}, who=LOGINS[0])
    assert b["approval_status"] == "отклонён", b               # один «отклонил» перевешивает
    print("4. итоговый статус: на согласовании → согласован → отклонён — ок")

    # 4. журнал: в audit попадает логин вошедшего, а не то, что прислали в теле
    with db.tx() as con:
        n = con.execute("SELECT COUNT(*) FROM audit WHERE entity=? AND action='решение по согласованию'",
                        (f"request:{rid}",)).fetchone()[0]
        actors = {r["who"] for r in db.rows(con, "SELECT who FROM audit WHERE entity=? AND"
                                                   " action='решение по согласованию'", f"request:{rid}")}
    assert n >= 4, n
    assert actors <= set(LOGINS) and LOGINS[3] not in actors, actors
    print(f"5. в audit записано решений: {n}, авторы — {', '.join(sorted(actors))} — ок")

    # 5. просмотр, входящие, лента
    st, v = call("GET", f"/requests/{rid}/approvals", who=boss)
    assert st == 200 and v["approval_status"] == "отклонён" and len(v["reviewers"]) == 2, v
    st, inb = call("GET", "/approvals/inbox", params={"user": LOGINS[0]}, who=LOGINS[0])
    assert st == 200 and all(i["request_id"] != rid for i in inb["items"]), inb
    # чужую очередь смотреть нельзя (ЗРУ-730 ст. 62): только свою или админскую
    st, b = call("GET", "/approvals/inbox", params={"user": LOGINS[1]}, who=LOGINS[0])
    assert st == 403, (st, b)
    st, feed = call("GET", "/approvals/feed", params={"limit": 20}, who=boss)
    mine = [i for i in feed["items"] if i["request_id"] == rid]
    assert st == 200 and len(mine) == 2, feed
    assert {i["decision"] for i in mine} == {"одобрил", "отклонил"}, mine
    assert all(i["position"] == "андеррайтер" and i["full_name"] for i in mine), mine
    print(f"6. лента решений: {len(mine)} записи по тестовому запросу, висит запросов: {feed['pending_requests']} — ок")

    # 6. переназначение состава сбрасывает решения
    call("POST", f"/requests/{rid}/reviewers", {"user_ids": [uids[0], uids[1]]}, who=boss)
    st, inb = call("GET", "/approvals/inbox", params={"user": LOGINS[1]}, who=LOGINS[1])
    assert any(i["request_id"] == rid for i in inb["items"]), inb
    st, v = call("GET", f"/requests/{rid}/approvals", who=boss)
    assert v["approval_status"] == "на согласовании", v
    print("7. переназначение состава сбрасывает решения — ок")

    # 6а. список «все запросы на согласовании» одним запросом
    st, pend = call("GET", "/approvals/pending", who=boss)
    row = [p for p in pend["items"] if p["id"] == rid]
    assert st == 200 and row and row[0]["total"] == 2 and row[0]["waiting"] == 2, (st, pend)
    assert pend["count"] == len(pend["items"]), pend
    print(f"7а. /approvals/pending: ждут решения {pend['count']} запросов, по нашему осталось {row[0]['waiting']} из 2 — ок")

    # 7. генеральные соглашения
    st, lst = call("GET", "/general-agreements", who=admin)
    aab = [a for a in lst if a["partner"] == "Asia Alliance Bank"]
    assert st == 200 and aab, lst
    assert aab[0]["status"] == "черновик" and aab[0]["default_reviewers"] == [] and not aab[0]["terms"], aab[0]
    st, made = call("POST", "/general-agreements", {"partner": "Тест-Банк", "terms": "условия",
                                                    "default_reviewers": [uids[0], uids[1]], "status": "действует"},
                    who=admin)
    assert st == 200, (st, made)
    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": [], "general_agreement_id": made["id"]}, who=boss)
    assert st == 200 and [x["user_id"] for x in b["reviewers"]] == [uids[0], uids[1]], (st, b)
    st, v = call("GET", f"/requests/{rid}/approvals", who=boss)
    assert v["general_agreement_id"] == made["id"], v
    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": [], "general_agreement_id": aab[0]["id"]}, who=boss)
    assert st == 400 and "вручную" in b["detail"], (st, b)     # у Asia Alliance Bank состава нет — не выдумываем
    print("8. генсоглашения: Asia Alliance Bank пустой черновик, подстановка состава работает — ок")

    # 8. страница и доклад
    st, html = call("GET", "/approvals")
    assert st == 200 and "Согласования" in html, st
    with db.tx() as con:
        body, _ = team.build_report(con, date.today())
    assert "## 6а. Согласования" in body, "нет раздела в докладе"
    assert "Ждут решения" in body, body[-2000:]
    print("9. страница /approvals открывается, раздел «Согласования» есть в докладе — ок")

    # 9. Telegram: назначения и решения выше прошли, а в бот не ушло ни одного сообщения и файла
    assert OUT == [], OUT[:3]
    print("10. назначение и решения в Telegram не отправлялись: 0 сообщений (мини-апп только для аналитики) — ок")


if __name__ == "__main__":
    with temp_db("surveyor-approvals.db"):  # рабочая data/surveyor.db не меняется
        rid, uids = setup()
        real = (tgbot.bot_token, tgbot._deliver, tgbot._deliver_file)
        try:
            # бот «подключён», сеть подменена сборщиком: любая отправка попала бы в OUT
            tgbot.bot_token = lambda: "TEST-TOKEN"
            tgbot._deliver = lambda method, payload: OUT.append((method, payload)) or {"ok": True, "result": {}}
            tgbot._deliver_file = lambda method, fields, *a: OUT.append((method, fields)) or {"ok": True, "result": {}}
            run(rid, uids)
            print("\nВсе проверки согласования пройдены.")
        finally:
            tgbot.bot_token, tgbot._deliver, tgbot._deliver_file = real
            teardown(rid)
