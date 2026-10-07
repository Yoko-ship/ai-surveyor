"""Выключенный по умолчанию адаптер ChatGPT plan для закрытого тестирования.

Только официальный SIWC OAuth + Responses API. Не читает сессии Codex.
Импорт не создаёт файлы, не открывает базу и не делает сетевых запросов.
"""
from collections import defaultdict, deque
from contextvars import ContextVar
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.openai.com/v1"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
REQUIRED_SCOPES = {"resource.invoke", "chatgpt.tokens.use.direct"}
actor = ContextVar("chatgpt_plan_tester", default="")
_refresh_lock = threading.Lock()
_quota_lock = threading.Lock()
_slots = threading.BoundedSemaphore(2)
_hits = defaultdict(deque)


class PlanError(RuntimeError):
    """Сообщение без данных провайдера, токенов и текста запроса."""


def selected():
    return os.environ.get("CHATGPT_PLAN_ENABLED", "").strip().lower() in {"1", "true"}


def allowed_ids():
    values = os.environ.get("CHATGPT_PLAN_TESTER_IDS", "").split(",")
    return {v.strip() for v in values if v.strip().isascii() and v.strip().isdigit()
            and int(v.strip()) > 0}


def authorized():
    return bool(actor.get()) and actor.get() in allowed_ids()


def auth_path():
    from .config import PROJECT_ROOT
    root = Path(os.environ.get("STORAGE_DIR") or PROJECT_ROOT / "data").expanduser()
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root / ".chatgpt-plan" / "auth.json"


def _validate(record):
    if not isinstance(record, dict):
        raise PlanError("Некорректное подключение ChatGPT: требуется повторный вход")
    scopes = record.get("scopes")
    if not isinstance(scopes, list) or not REQUIRED_SCOPES.issubset(set(scopes)):
        raise PlanError("При входе не разрешено использование плана ChatGPT")
    for name in ("client_id", "access_token", "refresh_token", "ext_agent_host_id"):
        if not isinstance(record.get(name), str) or not 1 <= len(record[name]) <= 20000:
            raise PlanError("Подключение ChatGPT неполное: требуется повторный вход")
    if record["client_id"] == "dynamic_agent_client" or record.get("token_type") != "Bearer":
        raise PlanError("Некорректная регистрация ChatGPT: требуется повторный вход")
    if (type(record.get("expires_at")) not in (int, float)
            or not math.isfinite(record["expires_at"]) or record["expires_at"] <= 0):
        raise PlanError("Не задан срок действия подключения ChatGPT")
    return record


def _read():
    path = auth_path()
    try:
        if path.is_symlink() or path.parent.is_symlink():
            raise PlanError("Хранилище подключения ChatGPT не должно быть ссылкой")
        if path.exists():
            if path.stat().st_mode & 0o077:
                raise PlanError("Закройте общий доступ к файлу подключения ChatGPT")
            if path.stat().st_size > 100000:
                raise PlanError("Некорректный размер файла подключения ChatGPT")
            raw = path.read_text(encoding="utf-8")
        else:
            raw = os.environ.get("CHATGPT_PLAN_AUTH_JSON", "")
        if not raw:
            raise PlanError("ChatGPT ещё не подключён: требуется вход владельца подписки")
        if len(raw) > 100000:
            raise PlanError("Некорректный размер подключения ChatGPT")
        return _validate(json.loads(raw))
    except (OSError, ValueError, TypeError):
        raise PlanError("Подключение ChatGPT не прочитано: требуется повторная настройка") from None


def _save(record):
    path = auth_path()
    if path.is_symlink() or path.parent.is_symlink():
        raise PlanError("Хранилище подключения ChatGPT не должно быть ссылкой")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".auth-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_validate(record), f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def model():
    return os.environ.get("CHATGPT_PLAN_MODEL", "").strip()


def reason():
    if not authorized():
        return "ИИ по подписке доступен только допущенным тестировщикам внутри Telegram"
    if not model():
        return "Для ChatGPT ещё не выбрана доступная аккаунту модель"
    try:
        _read()
    except PlanError as exc:
        return str(exc)
    return "Подключение ChatGPT задано; доступ модели требует проверки"


def ready():
    if not selected() or not authorized() or not model():
        return False
    try:
        _read()
        return True
    except PlanError:
        return False


def _access_token():
    # Один Uvicorn-процесс; обновления токена сериализованы между его потоками.
    with _refresh_lock:
        record = _read()
        if record["expires_at"] > time.time() + 90:
            if not auth_path().exists():
                _save(record)  # env используется только для первого защищённого импорта
            return record["access_token"]
        body = urllib.parse.urlencode({"grant_type": "refresh_token", "client_id": record["client_id"],
                                      "refresh_token": record["refresh_token"], "resource": API}).encode()
        request = urllib.request.Request(TOKEN_URL, data=body, headers={
            "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "INSON-surveyor/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.loads(response.read(100001))
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403):
                raise PlanError("Вход ChatGPT истёк или отозван: войдите снова") from None
            raise PlanError("Не удалось обновить вход ChatGPT; попробуйте позже") from None
        except (OSError, ValueError):
            raise PlanError("Не удалось обновить вход ChatGPT; попробуйте позже") from None
        try:
            updated = {**record, "access_token": result["access_token"],
                       "refresh_token": result["refresh_token"], "token_type": result["token_type"],
                       "expires_at": time.time() + int(result["expires_in"]),
                       "scopes": result.get("scope", " ".join(record["scopes"])).split()}
            if result.get("id_token"):
                updated["id_token"] = result["id_token"]
            _save(updated)  # не использовать новое значение, пока вращаемая пара не сохранена
        except (KeyError, TypeError, ValueError, OSError):
            raise PlanError("Не удалось сохранить обновлённый вход ChatGPT") from None
        return updated["access_token"]


def request_body(messages, file_parts):
    instructions = "\n".join(m["content"] for m in messages if m["role"] == "system")
    items = [{"role": m["role"], "content": [{
        "type": "output_text" if m["role"] == "assistant" else "input_text", "text": m["content"]}]}
        for m in messages if m["role"] != "system"]
    if not items or items[-1]["role"] != "user":
        items.append({"role": "user", "content": [{"type": "input_text", "text": ""}]})
    for i, part in enumerate(file_parts):
        inline = part["inline_data"]
        uri = "data:" + inline["mime_type"] + ";base64," + inline["data"]
        if inline["mime_type"] == "application/pdf":
            item = {"type": "input_file", "filename": f"document-{i + 1}.pdf", "file_data": uri}
        else:
            item = {"type": "input_image", "image_url": uri}
        items[-1]["content"].append(item)
    # SIWC не принимает max_output_tokens, temperature и ряд других обычных параметров.
    return {"model": model(), "instructions": instructions, "input": items, "store": False, "stream": True}


def _completed(response, deadline):
    data, total = [], 0
    while True:
        if time.monotonic() > deadline:
            raise PlanError("ChatGPT не завершил ответ вовремя")
        line = response.readline(65537)
        if not line:
            break
        total += len(line)
        if len(line) > 65536 or total > 8 * 1024 * 1024:
            raise PlanError("Ответ ChatGPT превышает предел размера")
        if line.strip():
            if line.startswith(b"data:"):
                data.append(line[5:].strip())
            continue
        if not data:
            continue
        raw, data = b"\n".join(data), []
        if raw == b"[DONE]":
            break
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError):
            raise PlanError("ChatGPT вернул нечитаемый поток ответа") from None
        if event.get("type") in {"error", "response.failed", "response.incomplete"}:
            raise PlanError("ChatGPT не завершил ответ; попробуйте снова")
        if event.get("type") == "response.completed":
            result = event.get("response") or {}
            if result.get("status") != "completed":
                raise PlanError("ChatGPT не завершил ответ")
            text = "\n".join(part.get("text", "") for item in result.get("output", [])
                             if item.get("type") == "message" for part in item.get("content", [])
                             if part.get("type") == "output_text")
            if not text.strip():
                raise PlanError("ChatGPT не вернул текст ответа")
            usage = result.get("usage") or {}
            return {"text": text, "usage": {"prompt_tokens": usage.get("input_tokens"),
                    "completion_tokens": usage.get("output_tokens"), "total_tokens": usage.get("total_tokens")}}
    raise PlanError("Поток ChatGPT оборвался до завершения ответа")


def request(messages, file_parts, timeout=90):
    if not ready():
        raise PlanError(reason())
    if not _slots.acquire(blocking=False):
        raise PlanError("Все тестовые подключения ChatGPT заняты; повторите позже")
    try:
        with _quota_lock:
            now = time.monotonic()
            q = _hits[actor.get()]
            while q and q[0] <= now - 3600:
                q.popleft()
            if len(q) >= 60:
                raise PlanError("Достигнут тестовый предел ChatGPT: 60 обращений в час")
            q.append(now)
        token = _access_token()
        body = json.dumps(request_body(messages, file_parts)).encode()
        req = urllib.request.Request(API + "/responses", data=body, headers={
            "Content-Type": "application/json", "Accept": "text/event-stream",
            "Authorization": "Bearer " + token, "User-Agent": "INSON-surveyor/1.0"})
        limit = max(1, min(float(timeout or 90), 120))
        try:
            with urllib.request.urlopen(req, timeout=limit) as response:
                return _completed(response, time.monotonic() + limit)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise PlanError("Нет доступа к модели ChatGPT: проверьте вход и разрешения") from None
            if exc.code == 429:
                raise PlanError("Достигнут лимит подписки ChatGPT; повторите позже") from None
            raise PlanError(f"ChatGPT отклонил запрос (HTTP {exc.code})") from None
        except OSError:
            raise PlanError("Нет связи с ChatGPT; попробуйте позже") from None
    finally:
        _slots.release()


class TesterContextMiddleware:
    """Разрешение только из HMAC-проверенных initData, никогда из username/ID браузера."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        tester = ""
        from . import claude_gateway, codex_gateway
        if (selected() or codex_gateway.selected() or claude_gateway.selected()) and allowed_ids():
            headers = dict(scope.get("headers", []))
            raw = headers.get(b"x-telegram-init-data", b"")
            if 0 < len(raw) <= 16384:
                from .telegram import bot_token, check_init_data
                result = check_init_data(raw.decode("utf-8", "replace"), bot_token(), max_age_sec=3600)
                try:
                    uid = str((result.get("user") or {}).get("id", ""))
                    issued = int((result.get("data") or {}).get("auth_date", 0))
                    if result["ok"] and issued <= time.time() + 30 and uid in allowed_ids():
                        tester = uid
                except (ValueError, TypeError, AttributeError):
                    pass
        mark = actor.set(tester)
        try:
            await self.app(scope, receive, send)
        finally:
            actor.reset(mark)
