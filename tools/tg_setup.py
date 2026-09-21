"""
Разовая настройка бота Telegram: вебхук, кнопка меню и список команд.

Запуск (из корня проекта):
    sandbox\\.venv\\Scripts\\python.exe tools\\tg_setup.py            # настроить
    sandbox\\.venv\\Scripts\\python.exe tools\\tg_setup.py --show     # показать состояние вебхука

Что делает:
  1. getMe             — проверяет, что токен рабочий, и печатает имя бота;
  2. setWebhook        — ставит адрес {SERVER_URL}/tg/webhook/{секрет} и тот же секрет в заголовке;
                         секрет берётся из настройки TG_WEBHOOK_SECRET, а если её нет — создаётся здесь
                         и сохраняется в app_settings (в вывод не печатается);
  3. setChatMenuButton — кнопка «Открыть сюрвейер» на {SERVER_URL}/tg;
  4. setMyCommands     — /start, /me, /help (/inbox убрана 21.09.2026: мини-апп только для аналитики).

Без токена ничего не ломает: печатает инструкцию и выходит с кодом 1.
Ни токен, ни секрет в вывод не попадают — только маски.
"""
import json
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, llm            # noqa: E402
from app import tgbot             # noqa: E402

COMMANDS = [{"command": "start", "description": "Регистрация или вход"},
            {"command": "me", "description": "Кто я и моя роль"},
            {"command": "help", "description": "Подсказка"}]

NO_TOKEN = """Токен бота не задан — настраивать нечего.

Что сделать:
  1. Создайте бота у @BotFather в Telegram (команда /newbot) и скопируйте токен.
  2. Откройте страницу «Деплой и состояние системы»: адрес сервера + /admin/deploy,
     блок «Настройки» → поле «Токен бота Telegram» → вставьте токен → «Сохранить настройки».
     Там же заполните «Адрес сервера» — он должен начинаться с https:// и быть виден из интернета.
  3. Запустите этот скрипт ещё раз.

Подробно — docs/Telegram мини-апп.md."""


def api(method: str, payload: dict = None) -> dict:
    """Вызов Bot API через urllib. Возвращает ответ Telegram как есть."""
    return tgbot._deliver(method, payload or {})


def ensure_secret() -> str:
    """Секрет вебхука: из настройки или новый (сохраняется в app_settings)."""
    s = tgbot.webhook_secret()
    if s:
        return s
    s = secrets.token_urlsafe(24)
    db.ensure_schema()
    with db.tx() as con:
        tgbot._setting_set(con, "TG_WEBHOOK_SECRET", s)
        db.audit(con, "tg_setup", "создан секрет вебхука", "app_settings", {"keys": ["TG_WEBHOOK_SECRET"]})
    return s


def show() -> int:
    if not tgbot.connected():
        print(NO_TOKEN)
        return 1
    info = api("getWebhookInfo")
    res = info.get("result") or {}
    if not info.get("ok"):
        print("Telegram не ответил:", info.get("description"))
        return 1
    url = res.get("url") or ""
    # адрес печатаем без секрета: он часть пути
    safe = url.rsplit("/", 1)[0] + "/…" if url else "не задан"
    print("Вебхук:", safe)
    print("Ожидают доставки обновлений:", res.get("pending_update_count", 0))
    if res.get("last_error_message"):
        print("Последняя ошибка доставки:", res.get("last_error_message"), "—", res.get("last_error_date"))
    print("Режим опроса (TG_POLLING):", "включён" if tgbot.polling_enabled() else "выключен")
    return 0


def setup() -> int:
    if not tgbot.connected():
        print(NO_TOKEN)
        return 1
    me = api("getMe")
    if not me.get("ok"):
        print("Токен не подошёл:", me.get("description"))
        print("Проверьте поле «Токен бота Telegram» на странице /admin/deploy. Маска текущего:",
              llm.mask_key(tgbot.bot_token()))
        return 1
    bot = me.get("result") or {}
    print(f"Бот: {bot.get('first_name')} (@{bot.get('username')})")

    base = tgbot.server_url()
    if not base:
        print("Не задан адрес сервера (SERVER_URL). Впишите его на /admin/deploy и повторите.")
        return 1
    if not base.startswith("https://"):
        print("Внимание: Telegram принимает вебхук и мини-апп только по https. Сейчас задано:", base)
        print("Настройка вебхука пропущена. Для локальной машины используйте запасной режим: TG_POLLING=1.")
        return 1

    secret = ensure_secret()
    hook = api("setWebhook", {"url": f"{base}/tg/webhook/{secret}", "secret_token": secret,
                              "allowed_updates": ["message", "callback_query"],
                              "drop_pending_updates": False})
    print("Вебхук:", "поставлен" if hook.get("ok") else "ошибка — " + str(hook.get("description")))

    btn = api("setChatMenuButton", {"menu_button": {"type": "web_app", "text": "Открыть сюрвейер",
                                                    "web_app": {"url": f"{base}/tg"}}})
    print("Кнопка меню:", "поставлена" if btn.get("ok") else "ошибка — " + str(btn.get("description")))

    cmd = api("setMyCommands", {"commands": COMMANDS})
    print("Список команд:", "записан" if cmd.get("ok") else "ошибка — " + str(cmd.get("description")))

    print("\nГотово. Проверка состояния: tools/tg_setup.py --show")
    return 0 if hook.get("ok") else 1


if __name__ == "__main__":
    sys.exit(show() if "--show" in sys.argv else setup())
