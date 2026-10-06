"""Шлюз Codex: --config <защищённый JSON> локально или --from-env на Railway.

Локально слушает loopback; в облаке HTTPS завершает Railway. Доступ — серверный
Bearer-ключ + список Telegram ID. Не публиковать config, токены или тела запросов.
"""
import argparse
from collections import defaultdict, deque
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.codex_runtime import infer

MAX_BODY = 22 * 1024 * 1024


def handler(config):
    slots, lock, hits = threading.BoundedSemaphore(1), threading.Lock(), defaultdict(deque)

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
            self.reply(200 if self.path == "/health" else 404,
                       {"ok": self.path == "/health", "model": config["model"]})

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
                result = infer(payload, config["codex_binary"], config["model"])
                self.reply(200, result)
            except TimeoutError:
                self.reply(504, {"ok": False})
            except (ValueError, KeyError, TypeError, AttributeError):
                self.reply(422, {"ok": False})
            except Exception:
                self.reply(502, {"ok": False})
            finally:
                slots.release()

    return Handler


def environment_config():
    """Секреты задаются в runtime Railway, а не в образе или репозитории."""
    return {"token": os.environ.get("CODEX_GATEWAY_TOKEN", ""),
            "tester_ids": [v.strip() for v in os.environ.get("CHATGPT_PLAN_TESTER_IDS", "").split(",") if v.strip()],
            "model": os.environ.get("CODEX_GATEWAY_MODEL", ""),
            "codex_binary": os.environ.get("CODEX_BINARY", "/usr/local/bin/codex"),
            "port": int(os.environ.get("PORT", "8787"))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config")
    source.add_argument("--from-env", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if args.from_env:
        config = environment_config()
        home = Path(os.environ["CODEX_HOME"])
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        home.chmod(0o700)
    else:
        path = Path(args.config)
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise SystemExit("Gateway config must be a regular private file (0600)")
        config = json.loads(path.read_text())
    if (len(config.get("token", "")) < 32 or not config.get("tester_ids") or not config.get("model")
            or any(not isinstance(v, str) or not v.isascii() or not v.isdecimal() or int(v) <= 0
                   for v in config["tester_ids"])):
        raise SystemExit("Gateway config is incomplete")
    host = "0.0.0.0" if args.from_env else "127.0.0.1"
    server = ThreadingHTTPServer((host, int(config.get("port", 8787))), handler(config))
    server.daemon_threads = True
    print("Authenticated Codex gateway listening", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
