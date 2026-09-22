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
NAV_KEYS = ["analytics", "calc", "osgor", "legal", "photos", "users", "settings"]
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

    # гостю — имя «Гость» и ссылка на вход для администратора из login_url
    must = {'id="adminLogin"': "нет ссылки «Вход для администратора»",
            "Вход для администратора": "нет подписи ссылки входа для администратора",
            'T("tg.guest", "Гость")': "в левой панели не написано «Гость»",
            "ME.login_url": "ссылка входа не берётся из /tg/me (login_url)",
            "const IS_GUEST = () => !ME.user": "страница не отличает гостя от вошедшего"}
    miss = [why for key, why in must.items() if key not in html]
    assert not miss, "гостевой режим: " + "; ".join(miss)
    print("5. регистрации и экранов входа нет, гостю — «Гость» и вход для администратора — ок")


def check_guest_photos(html):
    """Вкладка «Фото» у гостя: свои файлы анализа, загрузка, удаление, срок хранения 24 часа."""
    must = {'id="gdocCard"': "нет карточки файлов гостя",
            '"/analytics/risk/documents"': "список своих файлов не запрашивается",
            '"/analytics/risk/document", {method: "POST"': "нет загрузки файла гостем",
            '"/analytics/risk/document/" + encodeURIComponent(id), {method: "DELETE"}': "нет удаления файла",
            "tg.gdoc_ttl": "не сказано, что файлы хранятся 24 часа",
            "function loadPhotosTab(": "вкладка «Фото» не различает гостя и вошедшего",
            'errHtml(r.error)': "отказ сервера (429) показывается не его словами"}
    miss = [why for key, why in must.items() if key not in html]
    assert not miss, "вкладка «Фото» у гостя: " + "; ".join(miss)
    # гостевое меню сервера рисуется целиком
    st, me = call("GET", "/tg/me")
    keys = [n["key"] for n in me["nav"]]
    assert keys == ["analytics", "calc", "osgor", "legal", "photos"], me
    assert all(f'data-section="{k}"' in html for k in keys), "не все разделы гостя есть в разметке"
    assert "const GUEST_NAV = [" in html, "нет запасного меню гостя, если сервер не ответил"
    print("5a. вкладка «Фото» гостя: свои файлы, загрузка, удаление, 24 часа — ок")


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
    print("16. «Пользователи», вероятность, выгрузки — в разметке; блока согласования и карточки решения нет,"
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


def check_analytics_steps(html):
    """Задача 150 (разметка — дизайнер): аналитика по шагам продукт → документы → договор → суммы → анализ."""
    must = {
        "/analytics/risk/docs": "шаг 2: список нужных документов не запрашивается",
        "/analytics/risk/document": "шаг 3: нет загрузки договора",
        'accept=".pdf,.docx': "шаг 3: поле файла не ограничено PDF и DOCX",
        "doc_ids": "шаг 5: распознанные документы не передаются в анализ",
        "prefill": "шаг 4: суммы из договора не подставляются",
        "ai_summary": "шаг 5: не показан разбор ИИ",
        "ai_status": "шаг 5: не показано, что ИИ не подключён",
    }
    miss = [why for key, why in must.items() if key not in html]
    assert not miss, "аналитика по шагам: " + "; ".join(miss)
    print("22. аналитика по шагам: документы, загрузка договора, подстановка сумм, разбор ИИ — ок")


def check_compact_and_view(html):
    """Задача 170: компактная раскладка, режим пользователя для админа, нативные кнопки Telegram."""
    must = {
        ".seg{": "нет блока сегментов для связанных значений",
        '<div class="seg">': "ставки не объединены в сегменты",
        ".fg>div.half": "короткие поля не стоят по два в ряд",
        "function isShort(": "шаг 4: короткие поля не отмечаются",
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
        "TG.BackButton": "в мастере нет нативной кнопки «Назад»",
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


def check_quick_mode(html):
    """Быстрый режим аналитики (заказчик 22.09.2026): плитки, четыре поля, подстановки по умолчанию."""
    must = {
        'mode: "quick"': "быстрый режим не включён по умолчанию",
        'body.mode = WZ.mode': "режим не уходит в POST /analytics/risk",
        '"/analytics/risk/presets"': "плитки готовых объектов не запрашиваются",
        '"/analytics/risk/last"': "последние значения формы не восстанавливаются",
        '&mode=quick': "поля не запрашиваются в быстром режиме",
        "will_assume": "не показано, что система подставит по умолчанию",
        "assumptions": "в итоге не показаны подстановки",
        "must_full": "остальные поля не берутся из must_full",
        "function wzQuick(": "нет экрана быстрого анализа",
        'class="tiles"': "нет плиток объектов",
        'class="qadd"': "нет кнопок «+млн», «+млрд», «= стоимости»",
        'inputmode="numeric"': "суммы без цифровой клавиатуры",
        'class="segsel"': "нет сегментных переключателей вместо списков",
        "opts5.length <= 5": "сегменты не ограничены пятью вариантами",
        'T("tg.an.mode_full"': "нет переключателя «Подробный режим»",
        'T("tg.an.mode_quick"': "из подробного режима не вернуться в быстрый",
        'T("tg.an.refine"': "нет раскрытия «Уточнить данные»",
        "anSaveLast": "последние значения не сохраняются после анализа",
    }
    miss = [why for key, why in must.items() if key not in html]
    assert not miss, "быстрый режим аналитики: " + "; ".join(miss)
    print("25. быстрый режим: пресеты, четыре поля, подстановки, подробный режим — ок")


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
            check_analytics_steps(html)
            check_compact_and_view(html)
            check_legal_tab(html)
            check_quick_mode(html)
            print("\nВсе проверки мини-приложения пройдены.")
        finally:
            teardown(rid)
