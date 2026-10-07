"""Шлюз Claude: единственное место на сервере, где хранится вход подписки Claude.

На Railway работает отдельным сервисом и слушает только частную сеть проекта
(claude-gateway.railway.internal). Доступ — серверный Bearer-ключ + список Telegram ID.
Вход: CLAUDE_CODE_OAUTH_TOKEN из `claude setup-token` (подписка, около года).
Не публиковать токены, ключ шлюза или тела запросов.
"""
from collections import defaultdict, deque
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import claude_runtime
from app.claude_runtime import infer

MAX_BODY = 22 * 1024 * 1024


def handler(config):
    slots, lock, hits = threading.BoundedSemaphore(config.get("slots", 2)), threading.Lock(), defaultdict(deque)
    auth, auth_lock = {"checked": 0.0, "status": None}, threading.Lock()

    def login():
        """Состояние входа кешируется на минуту; после сбоя проверяется заново."""
        with auth_lock:
            if auth["status"] is None or time.monotonic() - auth["checked"] > 60:
                auth.update(status=claude_runtime.auth_status(config["claude_binary"]),
                            checked=time.monotonic())
            return auth["status"]

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *args):
            pass  # URL, заголовки и документы не попадают в журнал

        def reply(self, code, payload):
            data = json.dumps(payload).encode()
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authenticated(self):
            supplied = self.headers.get("Authorization", "").encode()
            expected = ("Bearer " + config["token"]).encode()
            if not hmac.compare_digest(supplied, expected):
                self.reply(401, {"ok": False})
                return False
            return True

        def do_GET(self):
            if self.path == "/livez":
                return self.reply(200, {"ok": True})
            if not self.authenticated():
                return
            if self.path != "/health":
                return self.reply(404, {"ok": False})
            state = login()
            self.reply(200, {"ok": state["logged_in"], **state, "model": config["model"],
                             "fallback_model": config["fallback_model"], "rate_limit": claude_runtime.rate_limit()})

        def do_POST(self):
            if not self.authenticated():
                return
            if self.path != "/infer":
                return self.reply(404, {"ok": False})
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                return self.reply(400, {"ok": False})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.reply(400, {"ok": False})
            if not 0 < length <= MAX_BODY:
                return self.reply(413, {"ok": False})
            if not slots.acquire(blocking=False):
                return self.reply(429, {"ok": False})
            try:
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    return self.reply(422, {"ok": False})
                uid = payload.get("tester_id")
                if uid not in config["tester_ids"]:
                    return self.reply(403, {"ok": False})
                if payload.get("model") != config["model"]:
                    return self.reply(400, {"ok": False})
                with lock:
                    now, q = time.monotonic(), hits[uid]
                    while q and q[0] <= now - 3600:
                        q.popleft()
                    if len(q) >= 60:
                        return self.reply(429, {"ok": False})
                    q.append(now)
                if not login()["logged_in"]:
                    return self.reply(503, {"ok": False, "login": False})
                result = infer(payload, config["claude_binary"], config["model"],
                               config["fallback_model"], config["effort"])
                self.reply(200, result)
            except TimeoutError:
                self.reply(504, {"ok": False})
            except (ValueError, KeyError, TypeError, AttributeError):
                self.reply(422, {"ok": False})
            except Exception:
                auth["checked"] = 0.0  # сбой может означать истёкший вход
                self.reply(502, {"ok": False})
            finally:
                slots.release()

    return Handler


class DualStackServer(ThreadingHTTPServer):
    """Частная сеть Railway адресует сервисы по IPv6; IPv4 тоже принимается."""
    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def environment_config():
    """Секреты задаются в runtime Railway, а не в образе или репозитории."""
    return {"token": os.environ.get("CLAUDE_GATEWAY_TOKEN", ""),
            "tester_ids": [v.strip() for v in os.environ.get("CHATGPT_PLAN_TESTER_IDS", "").split(",") if v.strip()],
            "model": os.environ.get("CLAUDE_GATEWAY_MODEL", ""),
            "fallback_model": os.environ.get("CLAUDE_GATEWAY_FALLBACK_MODEL", ""),
            "effort": os.environ.get("CLAUDE_GATEWAY_EFFORT", "low").strip().lower(),
            "claude_binary": os.environ.get("CLAUDE_BINARY", "/usr/local/bin/claude"),
            "slots": 2, "port": int(os.environ.get("PORT", "8787"))}


def main():
    os.umask(0o077)
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY must not be set on the Claude gateway: it would bill the API")
    config = environment_config()
    if (len(config["token"]) < 32 or not config["tester_ids"] or not config["model"]
            or config["effort"] not in claude_runtime.EFFORTS
            or any(not v.isascii() or not v.isdecimal() or int(v) <= 0 for v in config["tester_ids"])):
        raise SystemExit("Gateway config is incomplete")
    server = DualStackServer(("::", config["port"]), handler(config))
    print("Authenticated Claude gateway listening", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
