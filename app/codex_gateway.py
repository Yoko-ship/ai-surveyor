"""Аутентифицированный HTTPS-шлюз к отдельному исполнителю Codex."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from . import chatgpt_plan
from .chatgpt_plan import PlanError


def selected():
    return os.environ.get("CODEX_GATEWAY_ENABLED", "").lower().strip() in {"1", "true"}


def endpoint():
    value = os.environ.get("CODEX_GATEWAY_URL", "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        return ""
    return value


def model():
    return os.environ.get("CODEX_GATEWAY_MODEL", "").strip()


def ready():
    return (selected() and chatgpt_plan.authorized() and bool(endpoint())
            and len(os.environ.get("CODEX_GATEWAY_TOKEN", "")) >= 32 and bool(model()))


def reason():
    if not chatgpt_plan.authorized():
        return "Codex доступен только допущенным тестировщикам внутри Telegram"
    if not ready():
        return "Шлюз Codex ещё не настроен"
    return "Шлюз Codex настроен"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # не передавать ключ и документы на другой адрес


def request(messages, file_parts, timeout=None):
    if not ready():
        raise PlanError(reason())
    limit = max(1, min(float(timeout or 90), 120))
    body = {"tester_id": chatgpt_plan.actor.get(), "messages": messages,
            "files": file_parts, "timeout": limit, "model": model()}
    req = urllib.request.Request(endpoint() + "/infer", data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ["CODEX_GATEWAY_TOKEN"]})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=limit + 5) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise PlanError("Ответ шлюза Codex превышает предел размера")
        result = json.loads(raw)
        if (not isinstance(result, dict) or result.get("ok") is not True
                or not isinstance(result.get("text"), str) or not result["text"].strip()):
            raise PlanError("Codex не завершил ответ")
        return {"text": result["text"], "usage": result.get("usage") or {}}
    except urllib.error.HTTPError as exc:
        messages = {401: "Ключ шлюза Codex не принят", 403: "Нет допуска к шлюзу Codex",
                    413: "Документ слишком большой для шлюза Codex",
                    422: "Шлюз Codex не смог прочитать вложение; уменьшите документ",
                    429: "Шлюз Codex занят или достигнут тестовый лимит",
                    504: "Codex не завершил ответ вовремя"}
        raise PlanError(messages.get(exc.code, "Шлюз Codex недоступен")) from None
    except (OSError, ValueError, TypeError):
        raise PlanError("Нет связи со шлюзом Codex") from None
