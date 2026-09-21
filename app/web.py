"""
Общие мелочи веб-слоя (задача 123, оптимизация):

  * read_text(path) / build(key, paths, fn) — файлы страниц и склейки из них держатся в памяти,
    пока файл на диске не изменился (сверка по времени изменения и размеру): правки HTML видны
    сразу, без перезапуска сервера, а диск не читается на каждый запрос;
  * asset_response() — /theme.js, /i18n.js: ETag + Last-Modified + «Cache-Control: no-cache»
    (браузер переспрашивает и получает 304 без тела, если файл не менялся);
  * CacheControlMiddleware — ответам без своего Cache-Control ставит «no-store»: данные людей
    (запросы, выгрузки, списки) не оседают в кэше браузера и прокси;
  * ErrorMiddleware — необработанная ошибка: человеку — понятный текст с кодом, в журнал —
    трассировка с тем же кодом (без тела запроса и без строки запроса);
  * setup_logging() — журнал сервера для логгеров «surveyor.*».
"""
import hashlib
import logging
import os
import secrets
import sys
import threading
from email.utils import formatdate
from pathlib import Path

from starlette.responses import HTMLResponse, JSONResponse, Response

log = logging.getLogger("surveyor.web")

_lock = threading.Lock()
_texts = {}               # путь -> (mtime_ns, размер, текст)
_built = {}               # ключ -> (подпись файлов, результат)


def _sig(path) -> tuple:
    st = os.stat(path)
    return (str(path), st.st_mtime_ns, st.st_size)


def read_text(path) -> str:
    """Текст файла из памяти; файл изменился на диске — перечитываем."""
    sig = _sig(path)
    hit = _texts.get(sig[0])
    if hit is not None and hit[0] == sig:
        return hit[1]
    text = Path(path).read_text(encoding="utf-8")
    with _lock:
        _texts[sig[0]] = (sig, text)
    return text


def build(key, paths, fn):
    """Результат fn() (например, склейка страницы) живёт, пока не изменился ни один из файлов paths."""
    sig = tuple(_sig(p) for p in paths)
    hit = _built.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    value = fn()
    with _lock:
        _built[key] = (sig, value)
    return value


def etag_of_file(path) -> str:
    st = os.stat(path)
    return 'W/"%x-%x"' % (st.st_mtime_ns, st.st_size)


def _not_modified(request, etag: str) -> bool:
    got = request.headers.get("if-none-match") or ""
    return etag in [x.strip() for x in got.split(",")] if got else False


def asset_response(request, path, media_type: str) -> Response:
    """Статический файл с проверкой свежести: 304 без тела, если у браузера та же версия."""
    st = os.stat(path)
    etag = etag_of_file(path)
    headers = {"ETag": etag, "Last-Modified": formatdate(st.st_mtime, usegmt=True),
               "Cache-Control": "no-cache"}
    if request is not None and _not_modified(request, etag):
        return Response(status_code=304, headers=headers)
    return Response(read_text(path), media_type=media_type, headers=headers)


def json_with_etag(request, payload: Response, cache_control: str) -> Response:
    """Готовый JSON-ответ + ETag по содержимому; совпал — 304 без тела."""
    etag = 'W/"%s"' % hashlib.md5(payload.body).hexdigest()[:16]
    payload.headers["ETag"] = etag
    payload.headers["Cache-Control"] = cache_control
    if request is not None and _not_modified(request, etag):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache_control})
    return payload


class CacheControlMiddleware:
    """Ответ без Cache-Control получает «no-store». Страницы и API, где заголовок задан явно, не трогаем."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message):
            if message["type"] == "http.response.start":
                headers = message.get("headers") or []
                if not any(k.lower() == b"cache-control" for k, _ in headers):
                    message["headers"] = list(headers) + [(b"cache-control", b"no-store")]
            await send(message)

        await self.app(scope, receive, _send)


class PageGzipCache:
    """
    Страницы и скрипты (text/html, application/javascript) одинаковы для всех, пока не изменился файл,
    а сжатие 300 КБ занимает несколько миллисекунд на каждый запрос. Здесь сжатая версия хранится
    в памяти (последние 32 разных тела) и отдаётся готовой; сверка — по полному совпадению тела,
    поэтому чужая страница выдана быть не может. Остальное (JSON, PDF, XLSX) идёт мимо — его сжимает
    или пропускает стандартный GZipMiddleware.
    """
    TYPES = (b"text/html", b"application/javascript")
    MIN_SIZE = 1024
    MAX_ITEMS = 32

    def __init__(self, app, compresslevel: int = 6):
        self.app = app
        self.level = compresslevel
        self.cache = {}                  # hash(тело) -> (тело, сжатое)
        self.order = []

    def _gz(self, body: bytes) -> bytes:
        import gzip
        key = hash(body)
        hit = self.cache.get(key)
        if hit is not None and hit[0] == body:
            return hit[1]
        gz = gzip.compress(body, compresslevel=self.level, mtime=0)
        with _lock:
            self.cache[key] = (body, gz)
            self.order.append(key)
            while len(self.order) > self.MAX_ITEMS:
                self.cache.pop(self.order.pop(0), None)
        return gz

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return
        accept = b""
        for k, v in scope.get("headers") or []:
            if k == b"accept-encoding":
                accept = v
        if b"gzip" not in accept:
            await self.app(scope, receive, send)
            return
        state = {"start": None, "passthrough": False}

        async def _send(message):
            if state["passthrough"]:
                await send(message)
                return
            if message["type"] == "http.response.start":
                headers = dict((k.lower(), v) for k, v in message.get("headers") or [])
                ctype = headers.get(b"content-type", b"").split(b";")[0].strip()
                if message["status"] != 200 or ctype not in self.TYPES or b"content-encoding" in headers:
                    state["passthrough"] = True
                    await send(message)
                    return
                state["start"] = message
                return
            if message["type"] == "http.response.body":
                start = state["start"]
                body = message.get("body", b"")
                if message.get("more_body") or len(body) < self.MIN_SIZE:
                    state["passthrough"] = True          # потоковый или маленький ответ — как есть
                    await send(start)
                    await send(message)
                    return
                gz = self._gz(body)
                headers = [(k, v) for k, v in start.get("headers") or []
                           if k.lower() not in (b"content-length", b"vary")]
                headers += [(b"content-encoding", b"gzip"), (b"content-length", str(len(gz)).encode()),
                            (b"vary", b"Accept-Encoding")]
                await send({**start, "headers": headers})
                await send({"type": "http.response.body", "body": gz if scope["method"] == "GET" else b""})
                return
            await send(message)

        await self.app(scope, receive, _send)


ERROR_TEXT = "Внутренняя ошибка сервера. Код ошибки: {id}. Сообщите администратору."


class AccessLogFilter(logging.Filter):
    """Журнал запросов uvicorn («GET /путь?строка HTTP/1.1» 200): убирает строку запроса (там могут быть
    телефон, почта, коды) и секрет вебхука бота из пути. Сама запись остаётся — метод, путь и код ответа."""

    def filter(self, record):
        try:
            args = list(record.args or ())
            # uvicorn.access: (адрес клиента, метод, путь с строкой запроса, версия HTTP, код)
            if len(args) >= 3 and isinstance(args[2], str):
                p = args[2].split("?", 1)[0]
                args[2] = _safe_path(p)
                record.args = tuple(args)
        except Exception:
            pass
        return True


class ErrorLogFilter(logging.Filter):
    """uvicorn.error: текст сообщений об исключениях проходит через маску ПД."""

    def filter(self, record):
        try:
            if record.exc_info and record.exc_info[1] is not None:
                record.exc_info = _masked_exc_info(record.exc_info[1])
        except Exception:
            pass
        return True


def install_log_filters():
    """Вызывается при старте приложения. Повторный вызов фильтры не дублирует."""
    for name, flt in (("uvicorn.access", AccessLogFilter), ("uvicorn.error", ErrorLogFilter)):
        lg = logging.getLogger(name)
        if not any(isinstance(f, flt) for f in lg.filters):
            lg.addFilter(flt())


class MaskedError(Exception):
    """Исключение для журнала: исходный тип и текст с замаскированными ПД, стек вызовов прежний."""


def _safe_path(path) -> str:
    # секрет вебхука бота — часть пути; в журнал его не пишем
    path = path or ""
    if path.startswith("/tg/webhook/"):
        return "/tg/webhook/***"
    return path


def _masked_exc_info(exc, depth: int = 0):
    """(тип, значение, стек) для log.error: текст исключения — через маску ПД (app/llm.py).
    Стек вызовов (файлы, строки кода) данных людей не содержит и остаётся как есть для отладки."""
    try:
        from .llm import mask_pd
        text = mask_pd(str(exc))
    except Exception:                    # маска недоступна — лучше не писать текст вовсе
        text = "(текст скрыт)"
    masked = MaskedError(f"{type(exc).__name__}: {text}")
    masked.__traceback__ = exc.__traceback__
    cause = exc.__cause__ or exc.__context__
    if cause is not None and depth < 5:
        masked.__cause__ = _masked_exc_info(cause, depth + 1)[1]
    return (MaskedError, masked, exc.__traceback__)


class ErrorMiddleware:
    """Единый ответ на необработанное исключение. HTTPException и ошибки проверки ввода сюда не доходят —
    их, как и раньше, отдаёт FastAPI со своими кодами (4xx)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def _send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception as e:
            eid = secrets.token_hex(4)
            # в журнал — метод и путь (без строки запроса и тела: там могут быть данные людей)
            log.error("необработанная ошибка, код %s: %s %s", eid, scope.get("method"), _safe_path(scope.get("path")),
                      exc_info=_masked_exc_info(e))
            if started:                      # ответ уже начат — дописать нечего, соединение закроется.
                # Исходное исключение не пробрасываем: uvicorn напечатал бы его текст (а в нём могут быть ПД).
                raise RuntimeError(f"ответ прерван, код ошибки {eid}") from None
            text = ERROR_TEXT.format(id=eid)
            accept = ""
            for k, v in scope.get("headers") or []:
                if k == b"accept":
                    accept = v.decode("latin-1")
            if scope.get("method") in ("GET", "HEAD") and "text/html" in accept:
                resp = HTMLResponse("<!doctype html><meta charset='utf-8'><title>Ошибка</title>"
                                    "<body style='font:16px system-ui;padding:32px'><p>%s</p>" % text,
                                    status_code=500)
            else:
                resp = JSONResponse({"detail": text}, status_code=500)
            await resp(scope, receive, send)


def setup_logging() -> None:
    """Журнал для логгеров «surveyor.*» в stderr (его и собирают Railway и run.bat). Идемпотентно."""
    root = logging.getLogger("surveyor")
    if getattr(root, "_surveyor_configured", False):
        return
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(h)
    root.setLevel(logging.INFO)
    root.propagate = False
    root._surveyor_configured = True
