"""Клиент приватного шлюза Claude (подписка Claude на отдельном сервисе, tools/claude_gateway.py)."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from . import chatgpt_plan
from .chatgpt_plan import PlanError
from .codex_gateway import NoRedirect


def selected():
    return os.environ.get("CLAUDE_GATEWAY_ENABLED", "").lower().strip() in {"1", "true"}


def endpoint():
    """HTTPS или частная сеть Railway (http://<сервис>.railway.internal:<порт>)."""
    value = os.environ.get("CLAUDE_GATEWAY_URL", "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    private = parsed.scheme == "http" and (parsed.hostname or "").endswith(".railway.internal")
    if ((parsed.scheme != "https" and not private) or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        return ""
    return value


def model():
    return os.environ.get("CLAUDE_GATEWAY_MODEL", "").strip()


def ready():
    return (selected() and chatgpt_plan.authorized() and bool(endpoint())
            and len(os.environ.get("CLAUDE_GATEWAY_TOKEN", "")) >= 32 and bool(model()))


def reason():
    if not chatgpt_plan.authorized():
        return "Claude доступен только допущенным тестировщикам внутри Telegram"
    if not ready():
        return "Шлюз Claude ещё не настроен"
    return "Шлюз Claude настроен"


def request(messages, file_parts, timeout=None, web_search=False):
    if not ready():
        raise PlanError(reason())
    limit = max(1, min(float(timeout or 90), 120))
    body = {"tester_id": chatgpt_plan.actor.get(), "messages": messages,
            "files": file_parts, "timeout": limit, "model": model(),
            "web_search": web_search is True and not file_parts}
    req = urllib.request.Request(endpoint() + "/infer", data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ["CLAUDE_GATEWAY_TOKEN"]})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=limit + 5) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise PlanError("Ответ шлюза Claude превышает предел размера")
        result = json.loads(raw)
        if (not isinstance(result, dict) or result.get("ok") is not True
                or not isinstance(result.get("text"), str) or not result["text"].strip()):
            raise PlanError("Claude не завершил ответ")
        return {"text": result["text"], "usage": result.get("usage") or {}}
    except urllib.error.HTTPError as exc:
        messages = {401: "Ключ шлюза Claude не принят", 403: "Нет допуска к шлюзу Claude",
                    413: "Документ слишком большой для шлюза Claude",
                    422: "Шлюз Claude не смог прочитать вложение; уменьшите документ",
                    429: "Шлюз Claude занят или достигнут тестовый лимит",
                    503: "Шлюз Claude не вошёл в подписку: нужен новый токен входа",
                    504: "Claude не завершил ответ вовремя"}
        raise PlanError(messages.get(exc.code, "Шлюз Claude недоступен")) from None
    except (OSError, ValueError, TypeError):
        raise PlanError("Нет связи со шлюзом Claude") from None
