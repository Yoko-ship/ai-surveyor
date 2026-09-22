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

from tmpdb import temp_db  # noqa: E402  (tests/tmpdb.py)
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

# разделы, которые сервер раздаёт по ролям (app/tgbot.py: NAV_BASE / NAV_ADMIN)
# задача 144: «Аналитика» и «ОСГОР» добавлены, «Мои запросы» из меню убраны (точка /tg/my-requests осталась)
# задача 150: «Ждут меня», «Заявки», «Генеральные соглашения» убраны из меню для всех ролей
# 22.09.2026: «Юрист» для всех, «Админка» — только админу
# задача 223: «Аналитика» и «Фото» сведены в одну вкладку «ИИ-сюрвейер» (chat);
# сервер по-прежнему отдаёт ключи analytics/photos, интерфейс их объединяет (NAV_MERGE)
NAV_KEYS = ["analytics", "calc", "osgor", "legal", "photos", "users", "settings"]
SECTIONS = ["chat", "calc", "osgor", "legal", "users", "settings"]      # разделы в разметке
GONE_SECTIONS = ["analytics", "photos"]
REMOVED_KEYS = ["inbox", "applications", "agreements"]
# «Пользователи» открыты всем зарегистрированным (решение заказчика 21.09.2026)
NAV_BY_ROLE = {
    "агент": {"analytics", "calc", "osgor", "legal", "photos", "users"},
    "андеррайтер": {"analytics", "calc", "osgor", "legal", "photos", "users"},
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

    missing = [k for k in SECTIONS if f'id="tab-{k}"' not in html]
    assert not missing, "в разметке нет разделов: " + ", ".join(missing)
    no_attr = [k for k in SECTIONS if f'data-section="{k}"' not in html]
    assert not no_attr, "у разделов нет data-section: " + ", ".join(no_attr)
    left = [k for k in GONE_SECTIONS if f'id="tab-{k}"' in html or f'data-section="{k}"' in html]
    assert not left, "«Аналитика» и «Фото» должны быть внутри вкладки chat: " + ", ".join(left)
    assert 'const NAV_MERGE = {analytics: "chat", photos: "chat"}' in html, "меню сервера не сводится к вкладке chat"
    print(f"1. GET /tg отдаёт 200, все {len(SECTIONS)} разделов есть в разметке, analytics и photos — внутри chat — ок")

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
    # 22.09.2026: без входа — гостевое меню без «Пользователей», а не пустое
    assert st == 200 and me["status"] == "гость" and me["mode"] == "guest", me
    assert [n["key"] for n in me["nav"]] == ["analytics", "calc", "osgor", "legal", "photos"], me
    print("4. /tg/me отдаёт разный nav по ролям, без входа — гостевое меню — ок")
    for login, _role, _eais in PEOPLE:
        _st, me = call("GET", "/tg/me", who=login)
        assert not {n["key"] for n in me["nav"]} & set(REMOVED_KEYS), me["nav"]
    print("4a. «Ждут меня», «Заявки», «Соглашения» сервер не отдаёт ни одной роли — ок")


def check_guest_screen(uids, html):
    """Регистрации нет: неподтверждённая запись видит приложение как гость, экранов входа на странице нет."""
    login = "тест-ui-андер"
    with db.tx() as con:
        con.execute("UPDATE users SET status=? WHERE id=?", ("ожидает подтверждения", uids[login]))
    st, me = call("GET", "/tg/me", who=login)
    with db.tx() as con:
        con.execute("UPDATE users SET status=? WHERE id=?", ("активен", uids[login]))
    assert st == 200 and me["user"] is None and me["status"] == "гость", me

    # ни экранов входа и ожидания, ни кода регистрации на странице
    gone = ['id="screen-login"', 'id="screen-wait"', 'id="screen-register"', "function showWait(",
            "function showRegister(", "/tg/register/send-code", "/tg/register/verify-code",
            "/tg/register/submit", "/tg/consent?scope=", "/tg/register/positions",
            "/auth/tg-link/start", "/auth/google/exchange", "Получить код", "Зарегистрироваться",
            "Продолжить с Google", "Войти через Telegram", "нужна регистрация", "tg.wait_title"]
    left = [k for k in gone if k in html]
    assert not left, "в мини-аппе осталась регистрация или вход: " + ", ".join(left)

    # гостю — имя «Гость» и кнопка «Запросить доступ в админку» (вместо ссылки на вход)
    must = {'id="askAdm"': "нет кнопки «Запросить доступ в админку»",
            "Запросить доступ в админку": "нет подписи кнопки запроса доступа",
            'T("tg.guest", "Гость")': "в левой панели не написано «Гость»",
            "ME.login_url": "вход не берётся из /tg/me (login_url)",
            "const IS_GUEST = () => !ME.user": "страница не отличает гостя от вошедшего"}
    miss = [why for key, why in must.items() if key not in html]
    assert not miss, "гостевой режим: " + "; ".join(miss)
    print("5. регистрации и экранов входа нет, гостю — «Гость» и запрос доступа в админку — ок")


def check_guest_photos(html):
    """Файлы гостя теперь грузятся прямо в диалог: скрепка, перетаскивание, буфер обмена."""
    must = {'"/chat/upload"': "файлы не уходят в диалог",
            'id="chatFile"': "нет поля выбора файлов",
            'accept=".pdf,.docx,.xlsx,.jpg,.jpeg,.png"': "форматы файлов не ограничены",
            'errHtml(CH.err)': "отказ сервера (413, 415, 429) показывается не его словами"}
    miss = [why for key, why in must.items() if key not in html]
    assert not miss, "файлы в диалоге: " + "; ".join(miss)
    # гостевое меню сервера рисуется целиком
    st, me = call("GET", "/tg/me")
    keys = [n["key"] for n in me["nav"]]
    assert keys == ["analytics", "calc", "osgor", "legal", "photos"], me
    merged = {"analytics": "chat", "photos": "chat"}
    assert all(f'data-section="{merged.get(k, k)}"' in html for k in keys), "не все разделы гостя есть в разметке"
    assert "const GUEST_NAV = [" in html, "нет запасного меню гостя, если сервер не ответил"
    print("5a. файлы гостя уходят в диалог: скрепка, форматы, ответ сервера своими словами — ок")


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

    # 22.09.2026 (заказчик): калькулятор упрощён — вероятность подтверждения, сохранение запроса
    # и выгрузки PDF/XLSX с экрана расчёта убраны; серверные точки остались.
    gone_calc = ["function probHtml(", "function downloadAnalysis(", "Прислать в Telegram",
                 'id="saveBtn"', "/analysis.pdf"]
    left = [k for k in gone_calc if k in html]
    assert not left, "в упрощённом калькуляторе осталось лишнее: " + ", ".join(left)

    # 21.09.2026: мини-апп только для аналитики — ни отправки на согласование из расчёта,
    # ни карточки решения по ссылке /tg?request=<№>; серверные точки согласования остаются
    gone = ["reviewer-candidates", "/reviewers", "function revCount(", "paintApprovalBox", "sendReviewers",
            'id="approvalBox"', "general_agreement", "Отправить на согласование", "от 2 до 3",
            'id="reviewBox"', "openReviewFromLink", "reviewCard(", "function decide(", "/decide",
            "/tg/inbox", 'get("request")', "get('request')", "data-dec=", "Примечание (необязательно)",
            ">Отклонить<", "Задать вопрос"]
    left = [k for k in gone if k in html]
    assert not left, "в мини-аппе осталось согласование: " + ", ".join(left)
    # параметр ?request= просто игнорируется: страница та же, что и без него
    st, with_req = call("GET", "/tg", params={"request": "123"})
    assert st == 200 and with_req == html, "GET /tg?request=123 отдаёт не ту же страницу"
    print("16. «Пользователи» в разметке; согласования, вероятности и выгрузок в мини-аппе нет,"
          " ?request= игнорируется — ок")


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


def check_ui_kit(html):
    """Кнопки (21.09.2026): один ui-kit на tg.html, admin_hub.html, login.html; тема Telegram кнопки не перекрашивает."""
    app_dir = Path(__file__).resolve().parent.parent / "app"
    pat = re.compile(r"/\* ===== ui-kit кнопок INSON v1.*?/\* ===== конец ui-kit ===== \*/", re.S)
    kits = {n: pat.search((app_dir / n).read_text(encoding="utf-8")) for n in ("tg.html", "admin_hub.html", "login.html")}
    miss = [n for n, m in kits.items() if not m]
    assert not miss, "нет блока ui-kit в " + ", ".join(miss)
    assert len({m.group(0) for m in kits.values()}) == 1, "блок ui-kit на страницах различается"
    kit = kits["tg.html"].group(0)
    for cls in (".btn-primary", ".btn-secondary", ".btn-danger", ".btn-success", ".btn-link"):
        assert cls + "," in kit or cls + "{" in kit, "в ui-kit нет " + cls
    assert "min-height:44px" in kit and "-webkit-text-fill-color:#FFFFFF" in kit, "кнопки ниже 44px или без явного цвета текста"
    # старые классы кнопок в мини-аппе больше не используются
    old = re.findall(r'<button[^>]*class="(?:go|ghost|no)"', html)
    assert not old, "остались старые классы кнопок: " + str(old[:5])
    # тема Telegram: только фон и основной текст, с проверкой контраста; button_color не применяется
    assert "button_color" not in html and "link_color" not in html, "цвета кнопок Telegram снова накладываются на страницу"
    assert "function contrast(" in html and ">= 4.5" in html, "нет проверки контраста перед применением цветов Telegram"
    print("20. ui-kit кнопок один на три страницы, тема Telegram кнопки не трогает, контраст проверяется — ок")


def check_removed_tabs(html):
    """Задача 150 (разметка — дизайнер): убранных разделов в tg.html нет."""
    left = [k for k in REMOVED_KEYS if f'id="tab-{k}"' in html or f'data-section="{k}"' in html]
    assert not left, "в разметке остались убранные разделы: " + ", ".join(left)
    print("21. в разметке нет разделов inbox, applications, agreements — ок")


def check_chat_tab(html):
    """Задача 223: «Аналитика», «Расчёт» и «Фото» сведены в диалог ИИ-сюрвейера."""
    must = {
        'id="tab-chat"': "нет раздела «ИИ-сюрвейер»",
        'data-section="chat"': "раздел не подключён к меню",
        '"/chat/start"': "диалог не начинается",
        '"/chat/answer"': "ответы на чипы и поля не уходят",
        '"/chat/upload"': "файлы не уходят одним действием",
        '"/chat/analyze"': "анализ не запускается",
        '"/chat/message"': "свободный вопрос к ИИ не уходит",
        '"/chat/lang"': "смена языка не переводит свободный текст",
        '"/chat/state?session_id="': "диалог не восстанавливается после перезагрузки",
        '"dragenter"': "файл нельзя перетащить на экран",
        '"drop"': "нет обработчика отпускания файла",
        '"paste"': "файл из буфера обмена не вставляется",
        'id="chatDrop"': "нет подсветки «Отпустите, чтобы загрузить»",
        'capture': "на телефоне скрепка не открывает камеру",
        "function chatRelang(": "лента не перерисовывается при смене языка",
        "CH.tr[I18N_LANG]": "переводы не кэшируются на клиенте",
        'sessionStorage.setItem(CH_KEY': "номер диалога не переживает перезагрузку",
        'case "checklist"': "карточка чек-листа не рисуется",
        'case "fields"': "карточка полей не рисуется",
        'case "chips"': "быстрые ответы не рисуются",
        'case "result"': "итог не рисуется",
        "gaugeSvg(lv.score": "в итоге нет спидометра уровня риска",
        "chatOptionsHtml": "нет трёх вариантов тарифа",
        "narrative_source": "не показано, ИИ это или правила",
        "anScenariosCard(r)": "«Подробнее» не переиспользует карточки дашборда",
        'inputmode="numeric"': "суммы без цифровой клавиатуры",
        "keepDoc(": "«Заявление-анкета» не убрано из чек-листов",
        'HIDE_CHECKS = ["disclosure", "premium_unpaid"]': "скрытые проверки не отфильтрованы",
    }
    miss = [why for key, why in must.items() if key not in html]
    assert not miss, "диалог ИИ-сюрвейера: " + "; ".join(miss)
    gone = ["function wzQuick(", "function wzStep1(", "function loadPhotosTab(", 'id="anDash"']
    left = [k for k in gone if k in html]
    assert not left, "остался старый мастер аналитики: " + ", ".join(left)
    print("22. диалог ИИ-сюрвейера: карточки, файлы, анализ, язык, восстановление сессии — ок")


def check_calc_tab(html):
    """Заказчик 22.09.2026: «Калькулятор» — отдельная простая вкладка без диалога."""
    must = {
        'id="tab-calc"': "нет раздела «Калькулятор»",
        '"/reference/coefficients"': "факторы класса не запрашиваются",
        '"/calculate"': "премия не считается",
        "function calcRun(": "нет кнопки расчёта",
        "CALC_TERMS = [3, 6, 12]": "срок не выбирается сегментами",
        "CALC_FACTORS = 3": "показано больше трёх главных факторов",
        'T("calc.breakdown"': "нет раскрывашки «Из чего сложилась ставка»",
        'T("tg.calc_min"': "не показан минимум по продукту",
        'T("tg.nav.calc", "Калькулятор")': "вкладка называется не «Калькулятор»",
        'T("tg.nav.legal", "Специалист")': "«Юрист» не переименован в специалиста",
    }
    miss = [why for key, why in must.items() if key not in html]
    assert not miss, "вкладка «Калькулятор»: " + "; ".join(miss)
    print("22a. «Калькулятор»: продукт, сумма, срок, три фактора, «как сложилась» — ок")


def check_compact_and_view(html):
    """Задача 170: компактная раскладка, режим пользователя для админа, нативные кнопки Telegram."""
    must = {
        ".seg{": "нет блока сегментов для связанных значений",
        ".fg>div.half": "короткие поля не стоят по два в ряд",
        ".kpi.hero{grid-column:span 2}": "главная цифра KPI не на две колонки",
        "@media (max-width:599px){" + chr(10) + "  .tblwrap": "таблицы на телефоне не становятся карточками строк",
        "function anExtStatsCard(": "нет карточки «Статистика по рискам региона» (external_stats)",
        '"surveyor_view"': "режим пользователя не помнится в sessionStorage",
        'q.get("mode") === "user"': "адрес /tg?mode=user не включает режим пользователя",
        'id="viewToggle"': "нет переключателя «Режим пользователя» в левой панели",
        'id="viewBack"': "нет плашки «Режим пользователя · Вернуться»",
        '"/tg/me" + (VIEW_USER ? "?view=user" : "")': "режим пользователя не передаётся серверу (view=user)",
        "const IS_ADMIN = () => REAL_ADMIN() && !VIEW_USER": "в режиме пользователя админские части не прячутся",
        "CAN_MANAGE = !!USERS.can_manage && !VIEW_USER": "в режиме пользователя остались кнопки управления людьми",
        "TG.BackButton": "нет нативной кнопки «Назад»",
        "TG.SettingsButton": "нет нативной кнопки настроек",
        "showProgress": "нижняя кнопка Telegram без прогресса",
        "selectionChanged": "нет отклика HapticFeedback на выбор",
        "section_bg_color": "тема Telegram не использует section_bg_color",
    }
    miss = [why for key, why in must.items() if key not in html]
    assert not miss, "компактная раскладка и режимы: " + "; ".join(miss)
    # в режиме пользователя меню «Админка» не рисуется: условие — IS_ADMIN(), а не наличие раздела «Настройки»
    assert "if (IS_ADMIN()) {" + chr(10) + "    const hub" in html, "кнопка «Админка» видна в режиме пользователя"
    print("23. компактная раскладка, режим пользователя (/tg?mode=user), нативные кнопки Telegram — ок")


def check_legal_tab(html):
    """Вкладка «Юрист» (заказчик 22.09.2026): мгновенный ответ по закону на трёх языках."""
    must = {
        'id="tab-legal"': "нет раздела «Юрист»",
        'data-section="legal"': "раздел «Юрист» не подключён к меню",
        '"/legal/faq?lang="': "частые вопросы не запрашиваются на языке интерфейса",
        '"/legal/ask"': "вопрос не отправляется в POST /legal/ask",
        '{q: text, lang: I18N_LANG, ai: false}': "в запросе нет языка вопроса или выключенного ИИ",
        "function lgRepaint(": "при смене языка раздел «Юрист» не перерисовывается",
        "legal: lgRepaint": "раздел «Юрист» не в списке перерисовки языков",
        "legal: loadLegal": "раздел «Юрист» не в списке загрузчиков",
        'T("tg.nav.legal"': "название вкладки не берётся из словаря",
        'data-i18n="tg.lg.title">ИИ специалист по страхованию<': "вкладка не переименована в специалиста",
        "lgskel": "ответ появляется без скелетона",
        "tg.lg.read_source": "нет кнопки «Читать в источнике lex.uz»",
        "c.official": "не показано, официальный текст или перевод",
        "took_ms": "не показано время ответа",
        "tg.lg.related": "похожие вопросы не показываются",
        '"surveyor_legal"': "история вопросов сессии не сохраняется",
    }
    miss = [why for key, why in must.items() if key not in html]
    assert not miss, "вкладка «Юрист»: " + "; ".join(miss)
    # ИИ не изображаем: текст ИИ показываем только при ai.status === "ok"
    assert 'ai === "ok"' in html, "ответ ИИ показывается без проверки ai.status"
    print("24. вкладка «Юрист»: /legal/faq, /legal/ask, цитаты с источником, история сессии — ок")


def check_cover(html):
    """Обложка (задача 234): полноэкранные секции, картинки, бережный режим и карточка «Способы входа»."""
    must = {
        'id="cover"': "нет слоя обложки",
        "scroll-snap-type:y mandatory": "прокрутка не прилипает к секции",
        'class="cv-sec': "нет полноэкранных секций",
        "backdrop-filter": "карточки обложки не стеклянные",
        "perspective(900px)": "нет лёгкого наклона карточки",
        "IntersectionObserver": "текст не появляется при прокрутке",
        "prefers-reduced-motion": "не выключаются анимации при системной настройке",
        "saveData": "не учитывается экономия трафика",
        'srcset="/static/bg/': "картинки без srcset на две ширины",
        'loading="lazy"': "картинки секций грузятся сразу",
        "surveyor_cover": "выбор «обложка или приложение» не помнится",
        'id="appbg"': "нет фона раздела внутри приложения",
    }
    miss = [why for key, why in must.items() if key not in html]
    assert not miss, "обложка: " + "; ".join(miss)
    # первая картинка — не lazy: она и есть первый экран
    hero = html.split('data-cv="hero"')[1].split("</section>")[0]
    assert 'loading="lazy"' not in hero, "первая картинка обложки помечена lazy — первый экран будет пустым"
    # все картинки обложки есть на диске и вместе с телефонной версией не тяжелее 2 МБ
    bg = Path(__file__).resolve().parent.parent / "app" / "static" / "bg"
    names = sorted(set(re.findall(r'/static/bg/([a-z0-9-]+)\.jpg', html)))
    lost = [n for n in names if not (bg / f"{n}.jpg").exists()]
    assert names and not lost, "нет файлов фонов: " + ", ".join(lost)
    total = sum(f.stat().st_size for f in bg.glob("*.jpg"))
    assert total <= 2 * 1024 * 1024, f"фоны весят {total // 1024} КБ — больше 2 МБ"
    small = sum(f.stat().st_size for f in bg.glob("*-640.jpg"))
    # мини-апп отдаётся сервером вместе с картинками: адрес /static открыт до входа
    from app import guard
    assert any(x == "/static/" for x in guard.WHITE_PREFIX), "/static/ закрыт — картинки не загрузятся гостю"
    print(f"25. обложка: {len(names)} адресов картинок, все фоны {total // 1024} КБ"
          f" (телефонные {small // 1024} КБ),"
          f" lazy, бережный режим — ок")


def check_login_links(html):
    """Карточка «Способы входа» (задача 236): живые данные, привязка, отвязка, подпись зачем."""
    must = {
        'id="links"': "нет карточки способов входа",
        '"/auth/links"': "состав способов входа не запрашивается",
        "/auth/link/google/start": "нет запроса на привязку Google",
        "/auth/google?link=1": "нет запасного пути привязки Google",
        "/auth/link/telegram/start": "нет привязки Telegram кодом боту",
        "/auth/tg-link/status?link_id=": "код боту не опрашивается",
        'id="lnBindTg"': "нет кнопки «Привязать Telegram»",
        'id="lnTgOff"': "нет кнопки «Отвязать» у Telegram",
        'id="lnGoOff"': "нет кнопки «Отвязать» у Google",
        '"/auth/link/" + p': "отвязка не ходит в DELETE /auth/link/{provider}",
        "canUnlink": "кнопка «Отвязать» показывается без разрешения сервера (can_unlink)",
        'q.get("link")': "ответ Google (?link=ok / ?link=error) не разбирается",
        'T("tg.links.google_err"': "ошибка привязки Google не показывается текстом",
        'data-i18n="tg.links.why"': "нет подписи «второй способ входа — тот же профиль»",
        "const show = !!ME.user;": "карточка видна не всем вошедшим, а только админу",
    }
    miss = [why for key, why in must.items() if key not in html]
    assert not miss, "способы входа: " + "; ".join(miss)
    # разрешение на отвязку берётся у сервера, а не выдумывается страницей
    assert "LINKS.can_unlink" in html, "can_unlink не читается из ответа сервера"
    print("26. способы входа: Telegram и Google, привязка, отвязка по can_unlink, ошибки словами — ок")


def check_admin_request(html):
    """Запрос доступа в админку (задача 236): кнопка гостю, статус, блок владельца."""
    must = {
        'id="askAdm"': "нет кнопки «Запросить доступ в админку»",
        'data-i18n="tg.adminreq.ask"': "у кнопки запроса нет подписи из словаря",
        '"/auth/admin-request"': "запрос не уходит на сервер",
        "initData: (TG && TG.initData)": "внутри Telegram запрос идёт без подписанных данных",
        "if (d.token) setToken(d.token)": "токен нового профиля не сохраняется",
        'location.href = (ME.login_url || "/login?next=/tg")': "в браузере гость не отправляется на вход",
        'T("tg.adminreq.wait"': "статус «ждите подтверждения» не показывается",
        'st === "отклонён"': "отказ владельца не показывается",
        'id="admReqs"': "нет блока «Доступ в админку» в «Пользователях»",
        "USERS.is_owner": "блок запросов показывается не только владельцу",
        "USERS.admin_requests": "список запросов не берётся из /tg/users",
        '"/auth/admin-request/" + rid': "решение владельца не уходит по адресу запроса",
        '(yes ? "approve" : "reject")': "нет кнопок «Подтвердить» и «Отклонить»",
    }
    miss = [why for key, why in must.items() if key not in html]
    assert not miss, "запрос доступа в админку: " + "; ".join(miss)
    assert 'id="adminLogin"' not in html, "осталась старая ссылка «Вход для администратора»"
    print("27. запрос доступа в админку: кнопка гостю, статус, блок владельца — ок")


if __name__ == "__main__":
    with temp_db("surveyor-tg-ui.db"):  # рабочая data/surveyor.db не меняется
        agent_id, uids, prod = setup()
        rid = None
        try:
            html = check_page()
            check_nav_by_role()
            check_guest_screen(uids, html)
            check_guest_photos(html)
            rid = check_flow(uids, prod)
            check_ui_blocks(html)
            check_users_api(uids)
            check_candidates_and_exports(rid, uids)
            check_ui_kit(html)
            # ниже — ожидания к разметке после дизайнера (задача 150)
            check_removed_tabs(html)
            check_chat_tab(html)
            check_calc_tab(html)
            check_compact_and_view(html)
            check_legal_tab(html)
            check_cover(html)
            check_login_links(html)
            check_admin_request(html)
            print("\nВсе проверки мини-приложения пройдены.")
        finally:
            teardown(rid)
