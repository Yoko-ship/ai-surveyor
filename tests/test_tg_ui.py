"""
Мини-приложение Telegram как единое приложение трёх ролей: app/tg.html.

Проверяем без живого сервера — своим ASGI-клиентом (pytest и httpx в sandbox\\.venv не стоят,
поэтому обычные assert). Живой сервер не трогаем и не перезапускаем.

Запуск из корня проекта:
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_tg_ui.py

Тестовые записи (пользователи «тест-ui-*», агент ТЕСТ-UI-EAIS, запрос с branch='тест-ui')
удаляются в конце — рабочая база остаётся чистой.
"""
import asyncio
import json as _json
import re
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Тест ходит в приложение напрямую с адреса 127.0.0.1 — единый вход (app/guard.py)
# пропускает локальные соединения только в режиме разработчика.
os.environ.setdefault("SURVEYOR_DEV", "1")

from app import approvals     # noqa: E402
from app import auth          # noqa: E402
from app import db            # noqa: E402
from app.main import app      # noqa: E402

BRANCH = "тест-ui"
EAIS = "ТЕСТ-UI-EAIS"
# логин -> роль; агент подаёт запрос, андеррайтер и админ его согласуют
PEOPLE = [("тест-ui-агент", "агент", EAIS), ("тест-ui-андер", "андеррайтер", None),
          ("тест-ui-админ", "админ", None)]
TOKENS = {}

# разделы, которые сервер раздаёт по ролям (app/tgbot.py: NAV_BASE / NAV_REVIEWER / NAV_ADMIN)
NAV_KEYS = ["calc", "my-requests", "photos", "inbox", "applications", "users", "agreements", "settings"]
# «Пользователи» открыты всем зарегистрированным (решение заказчика 21.09.2026)
NAV_BY_ROLE = {
    "агент": {"calc", "my-requests", "photos", "users"},
    "андеррайтер": {"calc", "my-requests", "photos", "users", "inbox"},
    "админ": set(NAV_KEYS),
}


# ---------- минимальный ASGI-клиент ----------

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


# ---------- подготовка и уборка ----------

def setup():
    db.ensure_schema()
    with db.tx() as con:
        ts = db.now()
        con.execute("DELETE FROM agents WHERE eais_id=?", (EAIS,))
        cur = con.execute("INSERT INTO agents (eais_id, name, kind, status) VALUES (?,?,?,?)",
                          (EAIS, "Тестовый агент интерфейса", "физическое лицо", "активен"))
        agent_id = cur.lastrowid
        uids = {}
        for login, role, eais in PEOPLE:
            con.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE login=?)", (login,))
            con.execute("DELETE FROM users WHERE login=?", (login,))
            cur = con.execute("INSERT INTO users (login, full_name, role, branch, agent_eais_id, password_hash,"
                              " salt, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                              (login, "Тестовый " + role.capitalize(), role, BRANCH, eais, "x", "y", "активен", ts))
            uids[login] = cur.lastrowid
            u = db.rows(con, "SELECT * FROM users WHERE id=?", uids[login])[0]
            TOKENS[login], _ = auth.create_session(con, u, "127.0.0.1", "test")
        # продукт для расчёта берём из справочника, а не выдумываем
        prod = db.rows(con, "SELECT product_code, class_code FROM product_classes WHERE class_code='8' LIMIT 1")
        if not prod:
            prod = db.rows(con, "SELECT product_code, class_code FROM product_classes LIMIT 1")
    return agent_id, uids, prod[0]


def teardown(rid):
    with db.tx() as con:
        if rid:
            con.execute("DELETE FROM request_reviewers WHERE request_id=?", (rid,))
            con.execute("DELETE FROM check_results WHERE calculation_id IN"
                        " (SELECT id FROM calculations WHERE request_id=?)", (rid,))
            con.execute("DELETE FROM recommendations WHERE calculation_id IN"
                        " (SELECT id FROM calculations WHERE request_id=?)", (rid,))
            con.execute("DELETE FROM object_perils WHERE object_id IN"
                        " (SELECT id FROM objects WHERE request_id=?)", (rid,))
            # прогноз вероятности ссылается на расчёт — убираем его первым (таблицы может не быть)
            try:
                con.execute("DELETE FROM decision_outcomes WHERE request_id=?", (rid,))
            except Exception:
                pass
            con.execute("DELETE FROM calculations WHERE request_id=?", (rid,))
            con.execute("DELETE FROM objects WHERE request_id=?", (rid,))
            con.execute("DELETE FROM requests WHERE id=?", (rid,))
            con.execute("DELETE FROM audit WHERE entity=?", (f"request:{rid}",))
        for login, _role, _eais in PEOPLE:
            con.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE login=?)", (login,))
            con.execute("DELETE FROM audit WHERE who=?", (login,))
            con.execute("DELETE FROM users WHERE login=?", (login,))
        con.execute("DELETE FROM audit WHERE who=?", (BRANCH,))
        con.execute("DELETE FROM agents WHERE eais_id=?", (EAIS,))
        # страховка: запрос без состава не должен висеть «на согласовании»
        for r in db.rows(con, "SELECT id FROM requests WHERE approval_status=?", "на согласовании"):
            if not db.rows(con, "SELECT 1 FROM request_reviewers WHERE request_id=?", r["id"]):
                approvals.recalc(con, r["id"])
        left = con.execute("SELECT COUNT(*) FROM requests WHERE branch=?", (BRANCH,)).fetchone()[0]
        users_left = con.execute("SELECT COUNT(*) FROM users WHERE login LIKE 'тест-ui-%'").fetchone()[0]
    print(f"  очищено; запросов с branch='{BRANCH}' осталось: {left}; тестовых пользователей осталось: {users_left}")


# ---------- проверки ----------

def check_page():
    st, html = call("GET", "/tg")
    assert st == 200, st
    assert isinstance(html, str) and "Сюрвейер INSON" in html, html[:300]

    missing = [k for k in NAV_KEYS if f'id="tab-{k}"' not in html]
    assert not missing, "в разметке нет разделов: " + ", ".join(missing)
    no_attr = [k for k in NAV_KEYS if f'data-section="{k}"' not in html]
    assert not no_attr, "у разделов нет data-section: " + ", ".join(no_attr)
    print(f"1. GET /tg отдаёт 200, все {len(NAV_KEYS)} разделов есть в разметке — ок")

    ext = re.findall(r'src="(https?://[^"]+)"', html)
    assert len(ext) == 1, "внешних скриптов должно быть ровно один, найдено: " + str(ext)
    assert ext[0] == "https://telegram.org/js/telegram-web-app.js", ext
    assert "fonts.googleapis" not in html and "@import" not in html, "подключён внешний шрифт"
    print(f"2. внешний скрипт ровно один: {ext[0]} — ок")

    nav = re.search(r"<nav[^>]*>(.*?)</nav>", html, flags=re.S)
    assert nav, "нет меню разделов"
    assert "<button" not in nav.group(1), "список разделов зашит в разметку nav: " + nav.group(1)[:200]
    assert "function paintNav(" in html, "нет функции отрисовки навигации"
    # меню слева (21.09.2026): узкая рейка со значками, по «гамбургеру» — панель с названиями
    assert '<aside id="side">' in html and "#side{position:fixed;left:0" in html, "меню больше не слева"
    assert 'id="burger"' in html and "function sideOpen(" in html, "рейка не раскрывается в панель"
    assert "(min-width:900px)" in html, "на широком экране панель не раскрыта сразу"
    assert "min-height:52px" in html, "пункты меню ниже 44px — на телефоне в них не попасть"
    print("3. меню слева: рейка пустая в разметке, рисует её paintNav, панель раскрывается — ок")
    return html


def check_nav_by_role():
    for login, role, _eais in PEOPLE:
        st, me = call("GET", "/tg/me", who=login)
        assert st == 200, (st, me)
        assert me["status"] == "активен", me
        assert me["user"]["role"] == role, me
        keys = {n["key"] for n in me["nav"]}
        assert keys == NAV_BY_ROLE[role], (role, keys)
        assert keys <= set(NAV_KEYS), (role, keys)
        print(f"   {role}: {len(keys)} разделов — {', '.join(n['title'] for n in me['nav'])}")
    st, me = call("GET", "/tg/me")
    assert st == 200 and me["status"] == "не вошёл" and me["nav"] == [], me
    print("4. /tg/me отдаёт разный nav по ролям, без входа — пустой — ок")


def check_wait_screen(uids, html):
    """Статус не «активен» — ни разделов, ни сведений о человеке; на странице только экран ожидания."""
    login = "тест-ui-андер"
    with db.tx() as con:
        con.execute("UPDATE users SET status=? WHERE id=?", ("ожидает подтверждения", uids[login]))
    st, me = call("GET", "/tg/me", who=login)
    # вход по паролю: сервер отказывает понятной фразой, страница показывает её на экране ожидания
    pw_hash, salt = auth.hash_password("проверка-пароля-1")
    with db.tx() as con:
        con.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?", (pw_hash, salt, uids[login]))
    st2, lg = call("POST", "/auth/login", {"login": login, "password": "проверка-пароля-1"})
    with db.tx() as con:
        con.execute("UPDATE users SET status=? WHERE id=?", ("активен", uids[login]))

    assert st == 200 and me["nav"] == [] and me["user"] is None, me
    assert st2 == 403 and "подтверждена" in lg["detail"], (st2, lg)
    assert "screen-wait" in html and "function showWait(" in html, "нет экрана ожидания"
    assert "showWait(r.error" in html, "отказ входа не ведёт на экран ожидания"
    print(f"5. неподтверждённый человек: разделов нет, вход закрыт — «{lg['detail']}» — ок")


def check_flow(uids, prod):
    agent, under, admin = "тест-ui-агент", "тест-ui-андер", "тест-ui-админ"

    st, made = call("POST", "/requests", {"product_code": prod["product_code"], "class_code": prod["class_code"],
                                          "object_type": "Склад", "value_amount": 1_000_000_000,
                                          "sum_insured": 800_000_000, "term_days": 365,
                                          "branch": BRANCH, "policyholder": "ООО «Тест интерфейса»",
                                          "agent_eais_id": EAIS}, who=agent)
    assert st == 200, (st, made)
    rid = made["request_id"]
    print(f"6. агент сохранил запрос № {rid}, продукт {prod['product_code']}, вердикт «{made['verdict']}» — ок")

    # агенты в согласующие не попадают, себя выбрать нельзя — сервер это запрещает
    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": [uids[under], uids[agent]]}, who=agent)
    assert st == 400 and "3845" in b["detail"], (st, b)
    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": []}, who=agent)
    assert st == 400 and "от 1 до 3" in b["detail"], (st, b)
    print("7. агент согласующим быть не может, состав — от 1 до 3 человек: ошибки понятные — ок")

    st, b = call("POST", f"/requests/{rid}/reviewers", {"user_ids": [uids[under], uids[admin]],
                                                        "general_agreement_id": None}, who=agent)
    assert st == 200 and b["approval_status"] == "на согласовании", (st, b)
    assert [x["full_name"] for x in b["reviewers"]] == ["Тестовый Андеррайтер", "Тестовый Админ"], b
    print("8. запрос отправлен на согласование двум работникам компании — ок")

    st, inb = call("GET", "/tg/inbox", who=under)
    assert st == 200 and any(i["request_id"] == rid for i in inb["items"]), inb
    print(f"9. у андеррайтера в разделе «Ждут меня» {inb['count']} запрос(ов), наш там есть — ок")

    st, d = call("POST", f"/requests/{rid}/decide", {"decision": "одобрил", "comment": "склад осмотрен"}, who=under)
    assert st == 200 and d["approval_status"] == "на согласовании", (st, d)
    print("10. андеррайтер одобрил; пока решил не каждый — статус «на согласовании» — ок")

    st, my = call("GET", "/tg/my-requests", who=agent)
    assert st == 200, (st, my)
    card = [i for i in my["items"] if i["id"] == rid]
    assert card, my
    card = card[0]
    assert card["approval_status"] == "на согласовании", card
    assert card["sum_insured"] == 800_000_000 and card["premium"] and card["rate_pct"], card
    done = [x for x in card["reviewers"] if x["status"] == "одобрил"]
    assert len(done) == 1, card["reviewers"]
    assert done[0]["full_name"] == "Тестовый Андеррайтер", done
    assert done[0]["position"] == "андеррайтер" and done[0]["decided_at"], done
    assert done[0]["comment"] == "склад осмотрен", done
    waiting = [x for x in card["reviewers"] if x["status"] == "ожидает"]
    assert len(waiting) == 1 and waiting[0]["decided_at"] is None, card["reviewers"]
    print(f"11. «Мои запросы»: одобрил {done[0]['full_name']} ({done[0]['position']}) {done[0]['decided_at']},"
          f" ждём {waiting[0]['full_name']} — ок")

    st, d = call("POST", f"/requests/{rid}/decide", {"decision": "одобрил"}, who=admin)
    assert st == 200 and d["approval_status"] == "согласован", (st, d)
    st, my = call("GET", "/tg/my-requests", who=agent)
    card = [i for i in my["items"] if i["id"] == rid][0]
    assert card["approval_status"] == "согласован", card
    print("12. после второго решения запрос согласован, карточка агента это показывает — ок")

    # админские разделы: заявки, пользователи, соглашения, состояние бота
    st, pend = call("GET", "/auth/pending", who=admin)
    assert st == 200 and isinstance(pend, list), (st, pend)
    st, users = call("GET", "/auth/users", who=admin)
    assert st == 200 and any(u["login"] == agent for u in users), st
    st, agr = call("GET", "/general-agreements", who=admin)
    assert st == 200 and isinstance(agr, list), st
    st, bot = call("GET", "/tg/bot-status")
    assert st == 200 and "connected" in bot, bot
    st, col = call("GET", "/auth/colleagues", who=agent)
    assert st == 200 and all(c["status"] == "активен" for c in col), st
    print(f"13. разделы админа отвечают: заявок {len(pend)}, людей {len(users)}, соглашений {len(agr)};"
          f" бот подключён: {'да' if bot['connected'] else 'нет'} — ок")

    # у агента админских разделов нет и сервер их закрывает
    st, b = call("GET", "/auth/users", who=agent)
    assert st == 403, (st, b)
    print("14. агенту список пользователей закрыт (403) — ок")
    return rid


def check_register_markup(html):
    """Экран регистрации: три шага, текст согласия только с сервера, чужих текстов ошибок нет."""
    must = {
        'id="screen-register"': "нет экрана регистрации",
        "function showRegister(": "нет функции показа регистрации",
        '"нужна регистрация"': "статус «нужна регистрация» от /tg/auth не обрабатывается",
        'id="regStep1"': "нет шага 1 (номер телефона)",
        'id="regStep2"': "нет шага 2 (код)",
        'id="regStep3"': "нет шага 3 (анкета)",
        "/tg/consent?scope=": "текст согласия не берётся с сервера",
        "/tg/register/positions": "список должностей не запрашивается",
        "/tg/register/departments": "подсказки по департаменту не запрашиваются",
        "/tg/register/send-code": "нет запроса кода",
        "/tg/register/verify-code": "нет проверки кода",
        "/tg/register/submit": "нет отправки анкеты",
        "regCountdown(": "нет обратного отсчёта до повторной отправки",
        "Отправить код заново": "нет ссылки повторной отправки",
        "Осталось попыток ввода": "не показан счётчик попыток",
        'id="regPosOtherBox"': "нет поля для должности «другое»",
        "Версия согласия:": "версия согласия не показывается",
    }
    missing = [why for key, why in must.items() if key not in html]
    assert not missing, "экран регистрации: " + "; ".join(missing)
    # согласие ПД-2 в разметку не зашито: показываем ровно то, что пришло с сервера
    assert "consent_text" not in html and "персональных данных в соответствии со статьёй" not in html,         "текст согласия зашит в страницу"
    # ошибки 409/429/422 показываем словами сервера
    assert "regMsg(\"#regSendMsg\", \"err\", r.error)" in html, "текст ошибки отправки кода придуман страницей"
    assert "regMsg(\"#regCodeMsg\", \"err\", r.error)" in html, "текст ошибки проверки кода придуман страницей"
    print("15. экран регистрации: три шага, согласие и должности с сервера, ошибки — словами сервера — ок")


def check_ui_blocks(html):
    """Пользователи, вероятность подтверждения, выгрузки и выбор согласующих — в разметке."""
    users = ["/tg/users", "can_manage", "Сделать админом", "Снять админа", "make-admin", "revoke-admin"]
    miss = [k for k in users if k not in html]
    assert not miss, "раздел «Пользователи»: нет " + ", ".join(miss)
    assert "/auth/users" not in html, "раздел «Пользователи» всё ещё ходит в админский /auth/users"
    # почта и способ входа (21.09.2026): поля email и login_method из /tg/users, у старого сервера — прочерк
    cols = ['T("tg.user_email", "Почта")', 'T("tg.user_login_method", "Вход")', "u.email || \"—\"",
            "loginMethod(u.login_method)", '"Telegram и Google"', '"Служебный"']
    miss = [k for k in cols if k not in html]
    assert not miss, "в карточке человека нет почты или способа входа: " + ", ".join(miss)
    hub = (Path(__file__).resolve().parent.parent / "app" / "admin_hub.html").read_text(encoding="utf-8")
    cols = ['data-i18n="tg.user_email">Почта<', 'data-i18n="tg.user_login_method">Вход<',
            "login_method(u.login_method)", '"Telegram и Google"', '"Служебный"']
    miss = [k for k in cols if k not in hub]
    assert not miss, "в админке, раздел «Пользователи», нет колонок почты и входа: " + ", ".join(miss)
    assert "№ 159" not in hub, "в админке осталось пояснение про закрытый вопрос № 159"

    prob = ["Вероятность подтверждения", "Что снижает", "Что повысит", "жёсткое нарушение",
            "Вероятность ещё не рассчитана", "не калибрована", "how_to_raise", "function probHtml("]
    miss = [k for k in prob if k not in html]
    assert not miss, "блок вероятности: нет " + ", ".join(miss)

    exp = ["Скачать PDF", "Скачать XLSX", "Прислать в Telegram", '/analysis." + fmt',
           'data-fmt="pdf"', 'data-fmt="xlsx"',
           "/analysis/send-telegram", "скачивание файла часто не срабатывает"]
    miss = [k for k in exp if k not in html]
    assert not miss, "кнопки выгрузок: нет " + ", ".join(miss)

    # Выгрузки закрыты единым входом (app/guard.py), а сессия мини-аппа держится на токене:
    # по простой ссылке <a href> заголовок Authorization не уходит и файл вернёт 401.
    link = re.compile(r"<a[^>]+href=[\"'][^\"']*/analysis\.(?:pdf|xlsx)")
    assert not link.search(html), "«Скачать PDF/XLSX» снова простые ссылки — токен по ним не уйдёт"
    dl = ["function downloadAnalysis(", 'data-dl="', "Authorization: \"Bearer \" + TOKEN",
          "Content-Disposition", "Анализ запроса ", "function saveBlob(",
          "вы не вошли или сессия закончилась", "у вас нет доступа к этому запросу",
          "Встроенный браузер Telegram не дал сохранить файл"]
    miss = [k for k in dl if k not in html]
    assert not miss, "скачивание выгрузок запросом с токеном: нет " + ", ".join(miss)
    # запрос за файлом уходит с заголовком Authorization
    got = re.search(r"async function downloadAnalysis\(rid, fmt\)\{(.+?)\n\}", html, re.S)
    assert got and "Authorization" in got.group(1) and "/analysis.\" + fmt" in got.group(1),         "downloadAnalysis не забирает файл запросом с заголовком Authorization"

    rev = ["reviewer-candidates", "Выбрано ", "function revCount(", "Примечание (необязательно)",
           "Подтвердить", "Отклонить"]
    miss = [k for k in rev if k not in html]
    assert not miss, "отправка на согласование и карточка ответственного: нет " + ", ".join(miss)
    assert "от 2 до 3" not in html, "на странице осталось старое правило «от 2 до 3 согласующих»"
    print("16. «Пользователи», вероятность, выгрузки, выбор согласующих и карточка решения — в разметке — ок")


def check_register_api():
    """Точки регистрации отвечают без сессии: текст согласия, должности, департаменты."""
    for scope in ("основное", "телефон"):
        st, c = call("GET", "/tg/consent", params={"scope": scope})
        assert st == 200 and c.get("text") and c.get("version") and c.get("hash"), (scope, st, c)
        assert c["scope"] == scope, c
    st, p = call("GET", "/tg/register/positions")
    assert st == 200 and p["items"] and p["other"] in p["items"], (st, p)
    st, d = call("GET", "/tg/register/departments")
    assert st == 200 and isinstance(d["items"], list), (st, d)
    st, bad = call("GET", "/tg/consent", params={"scope": "выдумка"})
    assert st == 422, (st, bad)
    print(f"17. согласие (версия {c['version']}), {len(p['items'])} должностей и подсказки по департаментам"
          " отдаются без входа — ок")


def check_users_api(uids):
    """Список людей открыт всем вошедшим, права администратора — только администратору."""
    st, u = call("GET", "/tg/users", who="тест-ui-агент")
    assert st == 200 and u["can_manage"] is False and u["count"] == len(u["items"]), (st, u)
    keys = {"id", "full_name", "department", "position", "phone", "role", "status", "is_admin", "branch"}
    assert keys <= set(u["items"][0]), u["items"][0]
    st, a = call("GET", "/tg/users", who="тест-ui-админ")
    assert st == 200 and a["can_manage"] is True, (st, a)
    st, b = call("POST", f"/tg/users/{uids['тест-ui-андер']}/make-admin", who="тест-ui-агент")
    assert st == 403, (st, b)
    print(f"18. /tg/users: агент видит {u['count']} человек без кнопок (can_manage=false),"
          " админ — с кнопками, чужому назначение закрыто (403) — ок")


def check_candidates_and_exports(rid, uids):
    """Кандидаты в согласующие (1–3) и выгрузки анализа."""
    st, c = call("GET", f"/requests/{rid}/reviewer-candidates", who="тест-ui-агент")
    assert st == 200 and c["min"] == 1 and c["max"] == 3, (st, c)
    assert uids["тест-ui-агент"] not in [i["id"] for i in c["items"]], "автор запроса попал в кандидаты"
    for i in c["items"]:
        assert {"id", "full_name", "position", "department"} <= set(i), i
    # выгрузки: без входа закрыто, участнику — открыто (Telegram не привязан, поэтому 409, а не 403)
    st, _ = call("GET", f"/requests/{rid}/analysis.pdf")
    assert st in (401, 403), st
    st, t = call("POST", f"/requests/{rid}/analysis/send-telegram", params={"format": "both"},
                 who="тест-ui-агент")
    assert st == 409 and "Telegram" in t["detail"], (st, t)
    print(f"19. кандидатов в согласующие {len(c['items'])} (от 1 до 3, автор исключён);"
          " выгрузка без входа закрыта, участнику открыта — ок")


if __name__ == "__main__":
    agent_id, uids, prod = setup()
    rid = None
    try:
        html = check_page()
        check_nav_by_role()
        check_wait_screen(uids, html)
        rid = check_flow(uids, prod)
        check_register_markup(html)
        check_ui_blocks(html)
        check_register_api()
        check_users_api(uids)
        check_candidates_and_exports(rid, uids)
        print("\nВсе проверки мини-приложения пройдены.")
    finally:
        teardown(rid)
