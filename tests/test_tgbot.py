"""
Бот Telegram: регистрация, подтверждение админом, согласование кнопками, журнал без ПД.

Запуск из корня проекта (pytest и httpx в sandbox\\.venv не установлены — свой ASGI-клиент,
живой сервер не трогаем и не перезапускаем):
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_tgbot.py

Сеть не используется: transport-функция tgbot._deliver подменяется сборщиком исходящих.
Все тестовые записи (пользователи 'tg99…', запрос с branch='тест', настройки теста) удаляются в конце.
"""
import asyncio
import json as _json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Тест ходит в приложение напрямую с адреса 127.0.0.1 — единый вход (app/guard.py)
# пропускает локальные соединения только в режиме разработчика.
os.environ.setdefault("SURVEYOR_DEV", "1")

from app import approvals, auth, db, tgbot        # noqa: E402
from app.main import app                          # noqa: E402

BRANCH = "тест"
# telegram_id тестовых людей (заведомо не пересекаются с настоящими)
TG_NEW, TG_ADMIN, TG_R1, TG_R2, TG_STRANGER, TG_BOOT = "990001", "990002", "990003", "990004", "990005", "990006"
ALL_TG = [TG_NEW, TG_ADMIN, TG_R1, TG_R2, TG_STRANGER, TG_BOOT]
LOGINS = ["тест-бот-админ", "тест-бот-согл1", "тест-бот-согл2", "тест-бот-чужой", "тест-бот-иниц"]
EAIS, EAIS_NEW = "ТЕСТ-БОТ-EAIS", "ТЕСТ-БОТ-EAIS-2"
# ФИО и телефон, которых НЕ должно быть ни в одном журнале (проверка правила № 8)
PD_NAME, PD_PHONE = "Ботов Бот Ботович", "+998901234567"
SECRET, BOOT_CODE = "тест-секрет-вебхука-0001", "ТЕСТ-КОД-АДМИНА-42"

OUT = []                    # собранные исходящие: (метод, payload)
MARK = {"audit_id": 0}      # с какой записи журнала audit начался тест


# ---------- минимальный ASGI-клиент ----------

TOKENS = {}                 # логин -> токен сессии (вход настоящий, обхода нет)


def call(method: str, path: str, body=None, params=None, who=None):
    query = urlencode(params or {}, encoding="utf-8")
    payload = _json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(),
             "headers": [(b"host", b"test"), (b"content-type", b"application/json"),
                         (b"content-length", str(len(payload)).encode())]
                        + ([(b"cookie", f"sid={TOKENS[who]}".encode())] if who else []),
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
    raw = b"".join(out["chunks"]).decode("utf-8")
    try:
        return out["status"], _json.loads(raw)
    except ValueError:
        return out["status"], raw


# ---------- поддельные обновления Telegram ----------

_uid = {"n": 900000}


def next_update_id() -> int:
    _uid["n"] += 1
    return _uid["n"]


def upd_text(tg_id, text, update_id=None) -> dict:
    return {"update_id": update_id or next_update_id(),
            "message": {"message_id": next_update_id(), "text": text,
                        "from": {"id": int(tg_id), "first_name": "Тест"},
                        "chat": {"id": int(tg_id), "type": "private"}}}


def upd_cb(tg_id, data, update_id=None, message_id=777) -> dict:
    return {"update_id": update_id or next_update_id(),
            "callback_query": {"id": str(next_update_id()), "data": data,
                               "from": {"id": int(tg_id), "first_name": "Тест"},
                               "message": {"message_id": message_id, "chat": {"id": int(tg_id)}}}}


def fake_deliver(method, payload):
    OUT.append((method, payload))
    return {"ok": True, "result": {"message_id": len(OUT)}}


def texts_for(tg_id) -> list:
    return [p.get("text", "") for m, p in OUT if str(p.get("chat_id")) == str(tg_id)]


# ---------- подготовка и уборка ----------

def setup():
    db.ensure_schema()
    with db.tx() as con:
        MARK["audit_id"] = con.execute("SELECT COALESCE(MAX(id), 0) FROM audit").fetchone()[0]
        for tg in ALL_TG:
            con.execute("DELETE FROM users WHERE telegram_id=?", (tg,))
            con.execute("DELETE FROM tg_dialogs WHERE telegram_id=?", (tg,))
            con.execute("DELETE FROM pd_consents WHERE telegram_id=?", (tg,))
        ts = db.now()
        ids = {}
        for login, role, tg, eais in [(LOGINS[0], "админ", TG_ADMIN, None),
                                      (LOGINS[1], "андеррайтер", TG_R1, None),
                                      (LOGINS[2], "андеррайтер", TG_R2, None),
                                      (LOGINS[3], "андеррайтер", TG_STRANGER, None),
                                      (LOGINS[4], "агент", None, EAIS)]:
            con.execute("DELETE FROM users WHERE login=?", (login,))
            cur = con.execute("INSERT INTO users (login, full_name, role, branch, agent_eais_id, password_hash,"
                              " salt, status, telegram_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (login, f"Тестовый {login}", role, BRANCH, eais, "x", "y", "активен", tg, ts))
            ids[login] = cur.lastrowid
        # инициатор запроса — агент из реестра; ему уходит уведомление об итоге
        con.execute("UPDATE users SET telegram_id=? WHERE login=?", (TG_NEW + "9", LOGINS[4]))
        con.execute("DELETE FROM agents WHERE eais_id IN (?,?)", (EAIS, EAIS_NEW))
        con.execute("INSERT INTO agents (eais_id, name, kind, status) VALUES (?,?,?,?)",
                    (EAIS_NEW, "Тестовый агент бот 2", "физическое лицо", "активен"))
        cur = con.execute("INSERT INTO agents (eais_id, name, kind, status) VALUES (?,?,?,?)",
                          (EAIS, "Тестовый агент бот", "физическое лицо", "активен"))
        agent_id = cur.lastrowid
        cur = con.execute("INSERT INTO requests (external_no, branch, policyholder, product_code, agent_id,"
                          " created_at) VALUES (?,?,?,?,?,?)",
                          ("ТЕСТ-БОТ-1", BRANCH, "ООО «Тест»", "0101", agent_id, ts))
        rid = cur.lastrowid
        con.execute("INSERT INTO objects (request_id, object_type, sum_insured, value_amount)"
                    " VALUES (?,?,?,?)", (rid, "Склад", 500_000_000, 500_000_000))
        # настройки теста: секрет вебхука и код первого администратора
        for k, v in (("TG_WEBHOOK_SECRET", SECRET), ("ADMIN_BOOTSTRAP_CODE", BOOT_CODE)):
            con.execute("DELETE FROM app_settings WHERE key=?", (k,))
            con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?)", (k, v, db.now()))
        con.execute("DELETE FROM app_settings WHERE key='ADMIN_BOOTSTRAP_USED'")
    return rid, ids


def teardown(rid):
    with db.tx() as con:
        con.execute("DELETE FROM request_reviewers WHERE request_id=?", (rid,))
        approvals.recalc(con, rid)
        con.execute("DELETE FROM objects WHERE request_id=?", (rid,))
        con.execute("DELETE FROM requests WHERE id=?", (rid,))
        for tg in ALL_TG + [TG_NEW + "9"]:
            con.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE telegram_id=?)", (tg,))
            con.execute("DELETE FROM audit WHERE who IN (SELECT login FROM users WHERE telegram_id=?)", (tg,))
            con.execute("DELETE FROM audit WHERE who=?", (f"tg{tg}",))
            con.execute("DELETE FROM users WHERE telegram_id=?", (tg,))
            con.execute("DELETE FROM tg_dialogs WHERE telegram_id=?", (tg,))
            con.execute("DELETE FROM pd_consents WHERE telegram_id=?", (tg,))
            con.execute("DELETE FROM tg_messages WHERE telegram_id=?", (tg,))
        con.execute("DELETE FROM tg_messages WHERE error=?", ("неверный секрет вебхука",))
        for login in LOGINS:
            con.execute("DELETE FROM audit WHERE who=?", (login,))
            con.execute("DELETE FROM users WHERE login=?", (login,))
        con.execute("DELETE FROM agents WHERE eais_id IN (?,?)", (EAIS, EAIS_NEW))
        con.execute("DELETE FROM audit WHERE entity=?", (f"request:{rid}",))
        con.execute("DELETE FROM tg_updates WHERE update_id > 900000")
        for k in ("TG_WEBHOOK_SECRET", "ADMIN_BOOTSTRAP_CODE", "ADMIN_BOOTSTRAP_USED"):
            con.execute("DELETE FROM app_settings WHERE key=?", (k,))
        left_u = con.execute("SELECT COUNT(*) FROM users WHERE telegram_id IN (%s)"
                             % ",".join("?" * len(ALL_TG)), ALL_TG).fetchone()[0]
        left_r = con.execute("SELECT COUNT(*) FROM requests WHERE branch=?", (BRANCH,)).fetchone()[0]
        left_m = con.execute("SELECT COUNT(*) FROM tg_messages").fetchone()[0]
    print(f"  очищено; тестовых пользователей осталось: {left_u}; запросов branch='{BRANCH}': {left_r}; "
          f"строк в tg_messages: {left_m}")


# ---------- проверки ----------

def run(rid, ids):
    # 1. регистрация нового человека через /start
    tgbot.process(upd_text(TG_NEW, "/start"))
    with db.tx() as con:
        d = tgbot.dialog(con, TG_NEW)
    assert d and d["step"] == "согласие", d
    assert any("Версия текста" in t for t in texts_for(TG_NEW)), texts_for(TG_NEW)
    tgbot.process(upd_cb(TG_NEW, "consent:ok"))
    tgbot.process(upd_text(TG_NEW, PD_NAME))
    tgbot.process(upd_text(TG_NEW, "Ташкентский городской"))
    tgbot.process(upd_text(TG_NEW, "Главный специалист"))
    tgbot.process(upd_cb(TG_NEW, "role:агент"))
    tgbot.process(upd_text(TG_NEW, "НЕТ-ТАКОГО-ID"))        # чужой ID — заявка не создаётся
    with db.tx() as con:
        assert not db.rows(con, "SELECT 1 FROM users WHERE telegram_id=?", TG_NEW)
    tgbot.process(upd_text(TG_NEW, EAIS_NEW))
    with db.tx() as con:
        u = db.rows(con, "SELECT * FROM users WHERE telegram_id=?", TG_NEW)
        consent = db.rows(con, "SELECT * FROM pd_consents WHERE telegram_id=?", TG_NEW)
        left = db.rows(con, "SELECT 1 FROM tg_dialogs WHERE telegram_id=?", TG_NEW)
    assert u and u[0]["status"] == auth.STATUS_PENDING and u[0]["role"] == "агент", u
    assert u[0]["agent_eais_id"] == EAIS_NEW and u[0]["full_name"] == PD_NAME, u
    assert consent and consent[0]["version"] and consent[0]["created_at"] and consent[0]["user_id"] == u[0]["id"]
    assert consent[0]["channel"] == "telegram" and not left, (consent, left)
    new_uid = u[0]["id"]
    print(f"1. /start: заявка № {new_uid} «{u[0]['status']}», согласие на ПД версии "
          f"«{consent[0]['version']}» от {consent[0]['created_at']} — ок")

    # 2. админ подтверждает заявку кнопкой и меняет роль на «андеррайтер»
    assert any(f"Новая заявка на доступ № {new_uid}" in t for t in texts_for(TG_ADMIN)), texts_for(TG_ADMIN)
    tgbot.process(upd_cb(TG_ADMIN, f"uapprove:{new_uid}:андеррайтер"))
    with db.tx() as con:
        u = db.rows(con, "SELECT * FROM users WHERE id=?", new_uid)[0]
    assert u["status"] == auth.STATUS_ACTIVE and u["role"] == "андеррайтер", u
    print(f"2. админ подтвердил заявку: статус «{u['status']}», роль «{u['role']}» — ок")

    # 3. назначение согласующих: карточки ушли обоим
    OUT.clear()
    with db.tx() as con:
        approvals.assign(con, rid, [ids[LOGINS[1]], ids[LOGINS[2]]], who=LOGINS[0])
        rows = approvals.reviewers(con, rid)
    row1 = [r for r in rows if r["user_id"] == ids[LOGINS[1]]][0]
    row2 = [r for r in rows if r["user_id"] == ids[LOGINS[2]]][0]
    assert any("Страховая сумма" in t for t in texts_for(TG_R1)), texts_for(TG_R1)
    assert any("Страховая сумма" in t for t in texts_for(TG_R2)), texts_for(TG_R2)
    print("3. карточки запроса ушли обоим согласующим — ок")

    # 4. «Одобрить» от чужого telegram_id — отказ, решение не записано
    OUT.clear()
    tgbot.process(upd_cb(TG_STRANGER, f"approve:{rid}:{row1['id']}"))
    with db.tx() as con:
        rows = approvals.reviewers(con, rid)
    assert all(r["status"] == "ожидает" for r in rows), rows
    assert any("не назначены согласующим" in t for t in texts_for(TG_STRANGER)), texts_for(TG_STRANGER)
    print("4. чужой нажал «Одобрить» — отказ, решение не записано — ок")

    # 5. «Одобрить» от назначенного: решение записано, инициатору пока ничего (итог не окончательный)
    OUT.clear()
    tgbot.process(upd_cb(TG_R1, f"approve:{rid}:{row1['id']}"))
    with db.tx() as con:
        rows = approvals.reviewers(con, rid)
        r = db.rows(con, "SELECT approval_status FROM requests WHERE id=?", rid)[0]
    mine = [x for x in rows if x["user_id"] == ids[LOGINS[1]]][0]
    assert mine["status"] == "одобрил" and mine["decided_at"], mine
    assert r["approval_status"] == "на согласовании", r
    assert any(m == "editMessageText" for m, _ in OUT), OUT
    print(f"5. первый согласующий одобрил: статус «{r['approval_status']}», карточка обновлена — ок")

    # 6. второй одобрил — итог «согласован», уведомления ушли инициатору и обоим согласующим
    OUT.clear()
    tgbot.process(upd_cb(TG_R2, f"approve:{rid}:{row2['id']}"))
    with db.tx() as con:
        r = db.rows(con, "SELECT approval_status FROM requests WHERE id=?", rid)[0]
    assert r["approval_status"] == "согласован", r
    for tg in (TG_R1, TG_R2, TG_NEW + "9"):
        assert any("итог «согласован»" in t for t in texts_for(tg)), (tg, texts_for(tg))
    print("6. второй одобрил: итог «согласован», уведомления ушли инициатору и обоим согласующим — ок")

    # 7. /admin с кодом: первый раз — админ, второй раз тот же код — отказ
    OUT.clear()
    tgbot.process(upd_text(TG_BOOT, f"/admin {BOOT_CODE}"))
    with db.tx() as con:
        a = db.rows(con, "SELECT * FROM users WHERE telegram_id=?", TG_BOOT)
    assert a and a[0]["role"] == "админ" and a[0]["status"] == auth.STATUS_ACTIVE, a
    tgbot.process(upd_text(TG_BOOT, f"/admin {BOOT_CODE}"))
    assert any("уже использован" in t for t in texts_for(TG_BOOT)), texts_for(TG_BOOT)
    with db.tx() as con:
        det = " ".join(r["detail"] or "" for r in db.rows(con, "SELECT detail FROM audit WHERE who=?", f"tg{TG_BOOT}"))
    assert BOOT_CODE not in det, det                        # код в журнал не попадает — только маска
    print("7. /admin: администратор создан, повторный тот же код — отказ, код в журнале замаскирован — ок")

    # 8. вебхук: неверный секрет — 403, верный — 200
    st, b = call("POST", "/tg/webhook/не-тот-секрет", upd_text(TG_ADMIN, "/help"))
    assert st == 403, (st, b)
    OUT.clear()
    up = upd_text(TG_ADMIN, "/help")
    st, b = call("POST", f"/tg/webhook/{SECRET}", up)
    assert st == 200 and b.get("ok") is True, (st, b)
    assert len(texts_for(TG_ADMIN)) == 1, texts_for(TG_ADMIN)
    # 9. повтор того же update_id обрабатывается один раз
    st, b = call("POST", f"/tg/webhook/{SECRET}", up)
    assert st == 200 and b.get("skipped") == "повтор", (st, b)
    assert len(texts_for(TG_ADMIN)) == 1, texts_for(TG_ADMIN)
    print("8. вебхук: чужой секрет — 403, верный — 200; повтор update_id обработан один раз — ок")

    # 10. точки мини-аппа (без входа — 401, /tg/me отвечает «не вошёл»)
    st, me = call("GET", "/tg/me")
    assert st == 200 and me["status"] == "не вошёл" and me["mode"] in ("telegram", "браузер"), me
    st, _ = call("GET", "/tg/inbox")
    assert st == 401, st
    st, _ = call("GET", "/tg/my-requests")
    assert st == 401, st
    st, bs = call("GET", "/tg/bot-status")
    assert st == 200 and bs["webhook_set"] is True and "messages" in bs, bs
    assert BOOT_CODE not in _json.dumps(bs, ensure_ascii=False), bs

    # те же точки от имени вошедшего: разделы по роли, «ждут меня», свои запросы
    with db.tx() as con:
        for login in (LOGINS[0], LOGINS[1], LOGINS[4]):
            u = db.rows(con, "SELECT * FROM users WHERE login=?", login)[0]
            TOKENS[login], _ = auth.create_session(con, u, "127.0.0.1", "test")
    st, me = call("GET", "/tg/me", who=LOGINS[0])
    keys = [n["key"] for n in me["nav"]]
    assert st == 200 and me["status"] == "активен" and me["user"]["role"] == "админ", me
    assert {"calc", "inbox", "users", "settings"} <= set(keys), keys
    st, me2 = call("GET", "/tg/me", who=LOGINS[4])
    assert "inbox" not in [n["key"] for n in me2["nav"]] and "расчёт" in me2["rights"], me2
    st, inb = call("GET", "/tg/inbox", who=LOGINS[1])
    assert st == 200 and inb["count"] == 0, inb          # оба уже решили — ждущих нет
    st, my = call("GET", "/tg/my-requests", who=LOGINS[4])
    ours = [x for x in my["items"] if x["id"] == rid]
    assert st == 200 and ours and ours[0]["approval_status"] == "согласован", my
    assert len(ours[0]["reviewers"]) == 2 and ours[0]["sum_insured"] == 500_000_000, ours[0]
    print(f"9. /tg/me, /tg/inbox, /tg/my-requests, /tg/bot-status отвечают; разделы админа: {', '.join(keys)}; "
          f"сообщений в журнале: {bs['messages']} — ок")


def run_no_token(rid):
    """Без токена: ни одного исключения, отправка честно говорит «Бот не подключён»."""
    res = tgbot.send(TG_R1, "проверка", kind="уведомление")
    assert res == {"ok": False, "reason": "Бот не подключён"}, res
    out = tgbot.process(upd_text(TG_R1, "/me"))              # обработка идёт, просто без доставки
    assert out["ok"] is True, out
    st, bs = call("GET", "/tg/bot-status")
    assert st == 200 and bs["connected"] is False and bs["reason"] == "Бот не подключён", bs
    assert tgbot.start_polling() is False                    # без токена поток опроса не поднимается
    print("10. без TELEGRAM_BOT_TOKEN: отправка возвращает «Бот не подключён», исключений нет — ок")


def check_no_pd():
    """Правило № 8: ни ФИО, ни телефона, ни текстов сообщений в журналах tg_messages и audit."""
    needles = [PD_NAME, "Ботов", "Ботович", PD_PHONE, "Ташкентский городской", "Главный специалист",
               "ООО «Тест»", "Страховая сумма"]
    bad = []
    with db.tx() as con:
        # tg_messages смотрим целиком, audit — записи, сделанные этим тестом
        for table, where in (("tg_messages", ""), ("audit", f" WHERE id > {MARK['audit_id']}")):
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            for row in db.rows(con, f"SELECT * FROM {table}{where}"):
                for c in cols:
                    v = row.get(c)
                    if isinstance(v, str) and any(n in v for n in needles):
                        bad.append((table, c, v[:80]))
    assert not bad, bad
    print(f"11. журналы tg_messages и audit проверены на {len(needles)} образцов ПД — ни одного совпадения — ок")


def check_consent():
    """Текст согласия берётся из раздела 6 docs/Регистрация и роли.md, а не весь документ."""
    c = tgbot.consent_text()
    assert c["version"] == "ПД-1", c["version"]
    assert len(c["text"]) < 2500, len(c["text"])
    assert c["text"].lstrip().startswith("Согласие"), c["text"][:60]
    assert "INSON" in c["text"], c["text"][:200]
    for bad in ("матрица", "PERMISSIONS", "Положение № 3845"):
        assert bad not in c["text"], bad
    assert ">" not in c["text"] and "**" not in c["text"], c["text"][:200]
    phone = tgbot.consent_text("телефон")                 # отдельное согласие на номер — другой текст
    assert "номер телефона" in phone["text"] and phone["text"] != c["text"], phone["text"][:120]
    print(f"12. согласие: версия «{c['version']}», {len(c['text'])} знаков, источник {c['source']} — ок")


if __name__ == "__main__":
    rid, ids = setup()
    real_token, real_deliver = tgbot.bot_token, tgbot._deliver
    try:
        tgbot.bot_token = lambda: "TEST-TOKEN"      # токена у компании ещё нет — подменяем, сеть не трогаем
        tgbot._deliver = fake_deliver
        run(rid, ids)
        tgbot.bot_token = lambda: ""                # проверка режима «бот не подключён»
        run_no_token(rid)
        check_no_pd()
        check_consent()
        print("\nВсе проверки бота Telegram пройдены.")
    finally:
        tgbot.bot_token, tgbot._deliver = real_token, real_deliver
        teardown(rid)
