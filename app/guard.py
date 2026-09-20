"""
Единый вход в систему: без сессии сервер наружу ничего не отдаёт.

Подключение (app/main.py):

    from . import guard
    guard.install(app)            # middleware + маршруты первого администратора

Что происходит с каждым запросом:
  1. Путь в белом списке (WHITE_EXACT / WHITE_PREFIX) — пропускаем. Это то, без чего нельзя войти:
     /health, /theme.js, страница /login, заявка и вход /auth/*, страница мини-аппа /tg и её вход,
     вебхук бота /tg/webhook/{секрет} (секрет проверяет сам обработчик в app/tgbot.py).
  2. /docs, /redoc, /openapi.json — только в режиме разработчика (SURVEYOR_DEV=1 с локального адреса).
  3. Режим разработчика: SURVEYOR_DEV=1 И соединение пришло прямо с 127.0.0.1 (::1) И в запросе нет
     заголовков прокси (X-Forwarded-For и родня). Заголовок подделывается кем угодно, поэтому он не
     разрешает доступ, а наоборот — запрещает обход: на Railway запрос всегда идёт через прокси.
     Проверяем адрес из scope["client"] — это реальный собеседник сокета, а не то, что он о себе пишет.
  4. Иначе нужна сессия: cookie «sid» или заголовок Authorization: Bearer <токен>.
     Нет сессии → API отвечает 401 {"detail":"нужен вход"}, страница — редирект на /login?next=…
  5. Роли: ADMIN_PREFIX — только «админ»; остальное — любая подтверждённая роль.
     Точечные проверки внутри модулей (require(...), «решение принимает назначенный») остаются как были:
     guard — нижняя граница, а не замена.

Первый администратор на пустом сервере: GET /auth/bootstrap-needed и POST /auth/bootstrap
(код + ФИО + логин + пароль). Код — тот же ADMIN_BOOTSTRAP_CODE, что у бота (app/tgbot.py):
одноразовый, после использования гасится отметкой ADMIN_BOOTSTRAP_USED и удаляется из настроек.
Если код не задан, а пользователей ноль — при старте генерируем и печатаем его в журнал сервера
(только в журнал: страницы админки закрыты guard'ом, показать там некому).
"""
import hashlib
import hmac
import os
import secrets
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import auth, db, llm

router = APIRouter()

ADMIN = "админ"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
# заголовки, которые ставит прокси: если хоть один есть — запрос пришёл не с локального компьютера
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "forwarded")

NEED_LOGIN = "нужен вход"

# --- белый список: без сессии ---
WHITE_EXACT = {
    "/health",                       # проверка живости площадки
    "/theme.js", "/favicon.ico",     # статика страниц входа
    "/login",
    "/auth/register", "/auth/login", "/auth/verify-code", "/auth/logout",
    "/auth/bootstrap", "/auth/bootstrap-needed",
    "/tg",                           # страница мини-аппа: сама делает вход через /tg/auth
    "/tg/auth", "/tg/status", "/tg/me",
}
WHITE_PREFIX = ("/tg/webhook/",)     # секрет проверяет app/tgbot.py

# --- только в режиме разработчика ---
DEV_ONLY = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}

# --- только администратор ---
ADMIN_PREFIX = ("/admin", "/deploy/", "/tasks", "/reports", "/audit", "/users", "/approvals/admin")
# точечно: правка норм износа — админ, а чтение норм открыто любой роли
ADMIN_METHOD_PATH = {("POST", "/valuation/norms"), ("DELETE", "/valuation/norms"),
                     ("POST", "/valuation/settings")}


# --------------------------------------------------------------------------- #
#  Режим разработчика
# --------------------------------------------------------------------------- #

def client_host(request: Request) -> str:
    """Реальный адрес собеседника из ASGI-scope. Заголовкам не верим."""
    c = request.scope.get("client")
    return (c[0] if c else "") or ""


def dev_bypass(request: Request) -> bool:
    """Локальная разработка: guard отключён только для соединения с самого компьютера."""
    if os.environ.get("SURVEYOR_DEV") != "1":
        return False
    if any(request.headers.get(h) for h in PROXY_HEADERS):
        return False                                   # пришли через прокси — это не «свой компьютер»
    return client_host(request) in LOCAL_HOSTS


# --------------------------------------------------------------------------- #
#  Кто пришёл
# --------------------------------------------------------------------------- #

def user_of(request: Request) -> Optional[dict]:
    """Пользователь по сессии (cookie или Bearer). Ошибка базы не должна открывать доступ."""
    token = auth.request_token(request)
    if not token:
        return None
    try:
        with db.tx() as con:
            return auth.session_user(con, token)
    except Exception as e:
        print("guard: не удалось проверить сессию:", e)
        return None


# --------------------------------------------------------------------------- #
#  Решение по пути
# --------------------------------------------------------------------------- #

def is_open(path: str, dev: bool) -> bool:
    if path in WHITE_EXACT or path.startswith(WHITE_PREFIX):
        return True
    return dev and path in DEV_ONLY


def needs_admin(method: str, path: str) -> bool:
    return path.startswith(ADMIN_PREFIX) or (method.upper(), path) in ADMIN_METHOD_PATH


def wants_html(request: Request) -> bool:
    """Страница это или вызов API: браузер в навигации просит text/html."""
    if request.method.upper() not in ("GET", "HEAD"):
        return False
    return "text/html" in (request.headers.get("accept") or "")


def _deny(request: Request, code: int, detail: str):
    if code == 401 and wants_html(request):
        nxt = request.url.path + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse("/login?next=" + quote(nxt, safe=""), status_code=302)
    return JSONResponse({"detail": detail}, status_code=code)


async def check(request: Request):
    """None — пропустить дальше; иначе готовый ответ-отказ."""
    path = request.url.path.rstrip("/") or "/"
    dev = dev_bypass(request)
    if is_open(path, dev) or dev:
        return None
    if path in DEV_ONLY:                                  # документация API на рабочем сервере закрыта
        return _deny(request, 404, "страница недоступна")
    user = await run_in_threadpool(user_of, request)
    if not user:
        return _deny(request, 401, NEED_LOGIN)
    if needs_admin(request.method, path) and user["role"] != ADMIN:
        return _deny(request, 403, "нужны права администратора")
    request.scope["surveyor_user"] = user                  # чтобы обработчик не ходил в базу второй раз
    return None


def install(app):
    """Подключает middleware и маршруты первого администратора."""
    @app.middleware("http")
    async def guard_middleware(request: Request, call_next):
        denied = await check(request)
        if denied is not None:
            return denied
        return await call_next(request)

    app.include_router(router)


# --------------------------------------------------------------------------- #
#  Первый администратор
# --------------------------------------------------------------------------- #

def _digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _same(a: str, b: str) -> bool:
    """Секрет может быть на кириллице — сравниваем отпечатки, а не строки."""
    return bool(a) and bool(b) and hmac.compare_digest(_digest(a), _digest(b))


def users_count(con) -> int:
    """Сколько АДМИНИСТРАТОРОВ на сервере. Первый вход по коду нужен, пока нет ни одного админа:
    обычные или демо-пользователи могут появиться раньше (демо-данные при старте), это не должно
    закрывать дорогу первому администратору."""
    return con.execute("SELECT COUNT(*) FROM users WHERE role=?", (ADMIN,)).fetchone()[0]


def _setting_set(con, key: str, value: str):
    con.execute("DELETE FROM app_settings WHERE key=?", (key,))
    con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?)", (key, value, db.now()))


def ensure_bootstrap_code() -> Optional[str]:
    """
    Пустой сервер без заданного ADMIN_BOOTSTRAP_CODE: генерируем код и печатаем его в журнал сервера.
    Идемпотентно: если код уже задан (переменной или в настройках) или пользователи есть — ничего.
    Возвращает сгенерированный код или None.
    """
    try:
        with db.tx() as con:
            if users_count(con) > 0:
                return None
            if (llm.get("ADMIN_BOOTSTRAP_CODE") or "").strip():
                return None
            code = secrets.token_urlsafe(12)
            _setting_set(con, "ADMIN_BOOTSTRAP_CODE", code)
            con.execute("DELETE FROM app_settings WHERE key='ADMIN_BOOTSTRAP_USED'")
    except Exception as e:
        print("guard: код первого администратора не создан:", e)
        return None
    print("=" * 72)
    print("ПЕРВЫЙ ЗАПУСК: пользователей нет. Код первого администратора (одноразовый):")
    print("    " + code)
    print("Откройте /login → «Первый вход» и введите этот код. После входа код перестанет работать.")
    print("=" * 72, flush=True)
    return code


class BootstrapIn(BaseModel):
    code: str = ""
    full_name: str = Field(min_length=3)
    login: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8)


def bootstrap_admin(con, data: BootstrapIn) -> dict:
    """Создаёт первого администратора по одноразовому коду. Код в журнал не пишем — только маску."""
    if users_count(con) > 0:
        raise HTTPException(409, "Администратор уже создан. Войдите по логину и паролю или попросите "
                                 "действующего администратора подтвердить вашу заявку")
    code = (data.code or "").strip()
    setting = (llm.get("ADMIN_BOOTSTRAP_CODE") or "").strip()
    used = (llm.get("ADMIN_BOOTSTRAP_USED") or "").strip()
    digest = _digest(code) if code else ""
    if not code:
        raise HTTPException(422, "Укажите код первого администратора")
    if used and digest and hmac.compare_digest(used, digest):
        raise HTTPException(403, "Этот код уже использован")
    if not setting:
        raise HTTPException(403, "Код первого администратора не задан. Он печатается в журнале сервера "
                                 "при первом запуске или задаётся переменной ADMIN_BOOTSTRAP_CODE")
    if not _same(setting, code):
        raise HTTPException(403, "Код не подходит")

    out = auth.register_user(con, auth.RegisterIn(full_name=data.full_name, login=data.login,
                                                  password=data.password, role=ADMIN))
    _setting_set(con, "ADMIN_BOOTSTRAP_USED", digest)
    con.execute("DELETE FROM app_settings WHERE key='ADMIN_BOOTSTRAP_CODE'")
    db.audit(con, out["login"], "первый администратор по коду", f"user:{out['id']}",
             {"код": llm.mask_key(code)})
    return out | {"message": "Администратор создан, можно входить"}


@router.get("/auth/bootstrap-needed")
def bootstrap_needed():
    """Пуст ли сервер. Наружу уходит только «да/нет» — ни кода, ни имён."""
    with db.tx() as con:
        return {"needed": users_count(con) == 0}


@router.post("/auth/bootstrap")
def bootstrap(body: BootstrapIn):
    with db.tx() as con:
        return bootstrap_admin(con, body)
