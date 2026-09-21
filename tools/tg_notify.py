"""
Служебное уведомление владельцу системы в Telegram.
Запуск:  python tools/tg_notify.py "текст"
Нужны TELEGRAM_BOT_TOKEN и TG_OWNER_CHAT_ID (переменные окружения или .secrets.env).
Чат владельца сервер запоминает сам, когда владелец (TG_ADMIN_USERNAME) пишет боту или открывает мини-апп.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(key: str) -> str:
    v = os.environ.get(key, "").strip()
    if v:
        return v
    f = ROOT / ".secrets.env"
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    return ""


def main():
    text = " ".join(sys.argv[1:]).strip()
    token, chat = _env("TELEGRAM_BOT_TOKEN"), _env("TG_OWNER_CHAT_ID")
    if not text or not token or not chat:
        print("Нужны текст, TELEGRAM_BOT_TOKEN и TG_OWNER_CHAT_ID"); sys.exit(1)
    body = json.dumps({"chat_id": chat, "text": text[:4000]}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print("отправлено" if json.load(r).get("ok") else "не отправлено")


if __name__ == "__main__":
    main()
