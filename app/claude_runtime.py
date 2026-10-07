"""Исполнитель шлюза Claude: `claude -p` по подписке Claude, без инструментов хоста.

Модель видит только текст запроса и вложения. Инструменты, MCP, настройки, навыки и
история выключены; при разрешённом сервером поиске доступны только WebSearch и
WebFetch с одним разрешённым доменом lex.uz. ANTHROPIC_API_KEY в процесс не попадает:
CLI предпочёл бы его и списывал бы оплату по API вместо подписки.
"""
import base64
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from . import ai_policy


class RuntimeFailure(Exception):
    pass


EFFORTS = {"low", "medium", "high", "xhigh", "max"}
SEARCH_TOOLS = {"WebSearch", "WebFetch"}
FETCH_RULE = "WebFetch(domain:lex.uz)"  # прочие адреса CLI отклоняет сам: разрешения не выдаются
SAFE_ENV = {"HOME", "PATH", "LANG", "LC_ALL", "TZ", "TMPDIR", "USER", "LOGNAME",
            "SSL_CERT_FILE", "SSL_CERT_DIR", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN"}
FIXED_ENV = {"DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
_rate = {"last": None}
_rate_lock = threading.Lock()


def environment():
    """Только то, что нужно CLI: ни ключ шлюза, ни переменные Railway не наследуются."""
    env = {k: v for k, v in os.environ.items() if k in SAFE_ENV}
    env.update(FIXED_ENV)
    return env


def command(binary, model, fallback, effort, instructions, web_search=False):
    args = [binary, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--model", model, "--effort", effort, "--strict-mcp-config", "--setting-sources", "",
            "--disable-slash-commands", "--no-session-persistence", "--system-prompt-file", str(instructions)]
    if fallback and fallback != model:
        args += ["--fallback-model", fallback]
    if web_search:
        return args + ["--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch", FETCH_RULE]
    return args + ["--tools", ""]


def content(parts):
    """Вложения как блоки Claude. PDF целиком, до 12 страниц и изображений суммарно."""
    blocks, total, pages = [], 0, 0
    if not isinstance(parts, list) or len(parts) > 10:
        raise ValueError("files")
    for part in parts:
        inline = part["inline_data"]
        data = base64.b64decode(inline["data"], validate=True)
        total += len(data)
        if not data or len(data) > 10 * 1024 * 1024 or total > 15 * 1024 * 1024:
            raise ValueError("file size")
        mime = inline["mime_type"]
        import pymupdf as fitz
        if mime == "application/pdf":
            with fitz.open(stream=data, filetype="pdf") as doc:
                if doc.needs_pass or not 1 <= len(doc) <= 12 - pages:
                    raise ValueError("PDF pages or password")
                pages += len(doc)
            blocks.append({"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf", "data": base64.b64encode(data).decode()}})
        elif mime in ("image/png", "image/jpeg"):
            from .act_pkg.files import image_size
            size = image_size(data, "png" if mime == "image/png" else "jpg")
            if not size or size[0] * size[1] > 50_000_000:
                raise ValueError("image dimensions")
            # Пересжатие: снимок телефона не превышает предел Claude 5 МБ на изображение.
            with fitz.open(stream=data) as doc:
                page = doc[0]
                scale = min(1, 2000 / max(page.rect.width, page.rect.height, 1))
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                jpeg = pix.tobytes("jpeg", jpg_quality=85)
            pages += 1
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(jpeg).decode()}})
        else:
            raise ValueError("mime")
        if pages > 12:
            raise ValueError("too many pages")
    return blocks


def _rate_limit(info):
    """Окна подписки (5 часов и неделя) из rate_limit_event — без отдельного запроса."""
    windows = info.get("unifiedWindows") if isinstance(info, dict) else None
    window = (windows or {}).get("five_hour") if isinstance(windows, dict) else None
    if not isinstance(window, dict) or window.get("utilization") is None:
        return None
    week = windows.get("seven_day") if isinstance(windows.get("seven_day"), dict) else {}
    return {"five_hour_used_percent": round(float(window["utilization"]) * 100, 1),
            "five_hour_resets_at": window.get("resetsAt"),
            "seven_day_used_percent": round(float(week["utilization"]) * 100, 1)
            if week.get("utilization") is not None else None,
            "status": info.get("status")}


def rate_limit():
    with _rate_lock:
        return _rate["last"]


def parse_events(stdout, web_search=False):
    result, searches = None, 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") in {"tool_use", "server_tool_use"}:
                    if not web_search or block.get("name") not in SEARCH_TOOLS:
                        raise RuntimeFailure("Unexpected tool activity")
                    searches += 1
        elif kind == "rate_limit_event":
            snapshot = _rate_limit(event.get("rate_limit_info"))
            if snapshot:
                with _rate_lock:
                    _rate["last"] = snapshot
        elif kind == "result":
            result = event
    answer = (result or {}).get("result")
    if (not result or result.get("is_error") or result.get("subtype") != "success"
            or not isinstance(answer, str) or not answer.strip()):
        raise RuntimeFailure("Claude did not return a completed answer")
    answer = ai_policy.safe_output(answer, [os.environ.get("CLAUDE_GATEWAY_TOKEN", "")])
    usage = result.get("usage") or {}
    prompt = sum(int(usage.get(k) or 0) for k in
                 ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    completion = int(usage.get("output_tokens") or 0)
    return {"ok": True, "text": answer, "web_search": {"enabled": web_search, "calls": searches},
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                      "total_tokens": prompt + completion}}


def auth_status(binary):
    """`claude auth status` не обращается к модели: бесплатная проверка входа."""
    try:
        done = subprocess.run([binary, "auth", "status"], capture_output=True, text=True, timeout=30,
                              env=environment(), check=False)
        raw = json.loads(done.stdout or "{}")
        return {"logged_in": bool(raw.get("loggedIn")), "auth_method": raw.get("authMethod")}
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return {"logged_in": False, "auth_method": None}


def infer(payload, binary, model, fallback="", effort="low"):
    messages = payload["messages"]
    ai_policy.validate_messages(messages)
    if not isinstance(payload.get("web_search", False), bool):
        raise ValueError("web_search")
    if effort not in EFFORTS:
        raise ValueError("effort")
    # Только сервер явно разрешает поиск; вложения никогда его не получают.
    web_search = payload.get("web_search", False) and not payload.get("files")
    if any(not isinstance(m, dict) or m.get("role") not in {"system", "user", "assistant"}
           or not isinstance(m.get("content"), str) or len(m["content"]) > 12000 for m in messages):
        raise ValueError("message")
    limit = float(payload.get("timeout") or 90)
    if not math.isfinite(limit):
        raise ValueError("timeout")
    limit = max(1, min(limit, 120))
    secret = [os.environ.get("CLAUDE_GATEWAY_TOKEN", "")]
    blocks = content(payload.get("files", []))
    rules = ai_policy.SYSTEM_RULES + "\nTask instructions (subject to the rules above):\n"
    if web_search:
        rules += (ai_policy.WEB_RULES + "\nOnly lex.uz pages can be opened; use search results for other sites."
                  + "\nCurrent UTC date: " + datetime.now(timezone.utc).date().isoformat() + "\n")
    rules += "\n".join(m["content"] for m in messages
                       if m["role"] == "system" and m["content"] != ai_policy.SYSTEM_RULES)
    rules = ai_policy.redact_credentials(rules, secret)
    prompt = json.dumps([m for m in messages if m["role"] != "system"], ensure_ascii=False)
    prompt = ai_policy.redact_credentials(prompt, secret)
    message = {"type": "user", "message": {"role": "user",
                                           "content": blocks + [{"type": "text", "text": prompt}]}}
    with tempfile.TemporaryDirectory(prefix="inson-claude-") as tmp:
        folder = Path(tmp)
        (folder / "instructions.txt").write_text(rules, encoding="utf-8")
        work = folder / "work"  # пустая рабочая папка: инструкции лежат вне неё
        work.mkdir()
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen(command(binary, model, fallback, effort, folder / "instructions.txt",
                                            web_search),
                                    stdin=subprocess.PIPE, stdout=output, stderr=errors, cwd=work,
                                    env=environment(), start_new_session=True)
            try:
                proc.communicate((json.dumps(message, ensure_ascii=False) + "\n").encode(), timeout=limit)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                raise TimeoutError("Claude timeout") from None
            if proc.returncode:
                raise RuntimeFailure("Claude process failed")
            if output.tell() > 4 * 1024 * 1024:
                raise RuntimeFailure("Claude output too large")
            output.seek(0)
            return parse_events(output.read().decode("utf-8", "replace"), web_search)
