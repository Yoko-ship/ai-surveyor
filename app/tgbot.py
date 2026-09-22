"""
Бот Telegram: регистрация, команды и вход в мини-приложение.

С 21.09.2026 мини-приложение только для аналитики (решение заказчика): бот НЕ присылает карточки
запросов на согласование и итоги согласования, команды /inbox нет. Серверное согласование
(app/approvals.py, /requests/*/decide, /approvals) работает как прежде, но в Telegram не пишет.
Кнопки «Подтвердить/Отклонить/Вопрос» из старых сообщений отвечают «Согласование в приложении
отключено» и ничего не записывают.

Как это работает для заказчика — docs/Telegram мини-апп.md, раздел «Бот».
Коротко:
  * Регистрация с 21.09.2026 идёт НЕ в боте, а в мини-приложении: номер телефона + код из чата
    (app/registration.py). Бот на /start незнакомому человеку отвечает одним сообщением с кнопкой
    «Открыть приложение» — один путь регистрации и одно место хранения согласия на ПД.
  * Telegram присылает обновления на POST /tg/webhook/{секрет}. Секрет задаётся настройкой
    TG_WEBHOOK_SECRET и ставится вместе с вебхуком скриптом tools/tg_setup.py.
  * Запасной способ для локальной машины (когда адреса из интернета нет) — опрос getUpdates:
    настройка TG_POLLING=1 плюс токен; поток поднимается из app/main.py при старте сервера.
  * Без TELEGRAM_BOT_TOKEN модуль работает в режиме «браузер»: ничего не отправляет,
    возвращает понятную причину и НЕ бросает исключений. Правка кода при появлении токена не нужна —
    токен берётся через llm.get("TELEGRAM_BOT_TOKEN") (app_settings → окружение → .env).

Правило проекта № 8 (персональные данные): в журнале tg_messages и в db.audit НЕТ ни текстов
сообщений, ни ФИО, ни телефонов, ни сведений о страхователе — только telegram_id, наш user_id,
тип события и техническая причина ошибки.

Кнопки подтверждения заявки администратором (uapprove/ureject): кто нажал, определяется по
telegram_id отправителя нажатия, а не по содержимому callback_data.

Тесты — tests/test_tgbot.py (поддельные обновления, сеть не трогается).
"""
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import approvals, auth, background, db, llm, outcomes

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent
CONSENT_DOC = ROOT / "docs" / "Регистрация и роли.md"

API = "https://api.telegram.org"
TIMEOUT_SEC = 25
SEND_RETRIES = 3                      # не больше трёх попыток
POLL_TIMEOUT_SEC = 25                 # long polling: сколько Telegram держит соединение

NOT_CONNECTED = "Бот не подключён"

HELP = ("Что умеет бот:\n"
        "/start — открыть приложение\n"
        "/me — кто я, роль и состояние учётной записи\n"
        "/help — эта подсказка\n"
        "/admin КОД — разовый код первого администратора")

# 22.09.2026: приложение открыто для всех, регистрация отменена — подсказок про телефон и код нет.
OPEN_APP = ("Приложение открыто для всех — нажмите кнопку меню внизу чата или кнопку "
            "«Открыть приложение» ниже. Внутри: аналитика рисков, расчёт, ОСГОР и юридические ответы. "
            "Регистрация не нужна.")

WAIT_MSG = ("Заявка принята и ждёт подтверждения администратора. "
            "Как только вас подтвердят, откроются аналитика и расчёт.")

# Ответ на /inbox и на кнопки решения из старых сообщений (решение заказчика 21.09.2026).
ANALYTICS_ONLY = "Приложение теперь только для аналитики рисков — откройте его кнопкой меню"
APPROVAL_OFF = "Согласование в приложении отключено"


# --------------------------------------------------------------------------- #
#  Настройки
# --------------------------------------------------------------------------- #

def bot_token() -> str:
    return (llm.get("TELEGRAM_BOT_TOKEN") or "").strip()


def connected() -> bool:
    return bool(bot_token())


def webhook_secret() -> str:
    return (llm.get("TG_WEBHOOK_SECRET") or "").strip()


def polling_enabled() -> bool:
    return str(llm.get("TG_POLLING") or "0").strip().lower() in ("1", "да", "true", "on")


def server_url() -> str:
    return (llm.get("SERVER_URL") or "").strip().rstrip("/")


def _digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _same(a: str, b: str) -> bool:
    """Сравнение секретов за постоянное время. Сравниваем отпечатки: compare_digest
    не работает со строками не из ASCII, а секрет может быть и на кириллице."""
    return bool(a) and bool(b) and hmac.compare_digest(_digest(a), _digest(b))


def _setting_set(con, key: str, value: str):
    """Настройка в app_settings через уже открытое соединение (llm.set_many открывает своё)."""
    con.execute("DELETE FROM app_settings WHERE key=?", (key,))
    con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?)", (key, value, db.now()))


CONSENT_FALLBACK = (
    "Согласие на обработку персональных данных (предварительный текст).\n\n"
    "Нажимая «Согласен», вы разрешаете СО АО «INSON» обрабатывать ваши фамилию, имя, отчество, "
    "должность, филиал и идентификатор Telegram для допуска к рабочей системе сюрвейера. "
    "Данные хранятся на серверах в Республике Узбекистан и не передаются третьим лицам. "
    "Согласие можно отозвать, обратившись к администратору системы.\n\n"
    "Окончательный текст готовит юрист (docs/Регистрация и роли.md).")

CONSENT_PHONE_FALLBACK = (
    "Чтобы связаться с вами по рабочим вопросам и восстановить доступ, нам нужен ваш номер телефона.\n"
    "Передача номера — добровольная, отказ не мешает работе в системе.")


CONSENT_VERSION_DEFAULT = "ПД-3"
CONSENT_MAIN, CONSENT_PHONE = "основное", "телефон"

# Заголовки разделов документа юриста (docs/Регистрация и роли.md, раздел 5):
#   5.1 «Согласие на обработку персональных данных — версия ПД-3 от 21.09.2026»
#   5.2 «Отдельное согласие на номер телефона»
# Ищем по смыслу заголовка, а не по номеру: юрист меняет нумерацию, а название — нет.
CONSENT_HEADINGS = {
    CONSENT_MAIN: ("согласие на обработку персональных данных",),
    CONSENT_PHONE: ("согласие на номер телефона", "номер телефона"),
}


def _first_quote_block(lines, from_index: int) -> str:
    """Первый блок-цитата («> ...») после заголовка. Второй блок («Без согласия доступ невозможен»)
    в текст согласия не входит — это подпись под кнопкой."""
    cur, seen = [], False
    for line in lines[from_index:]:
        if line.strip().startswith("#") and seen:
            break
        if line.startswith(">"):
            cur.append(line.lstrip(">").strip().replace("**", ""))
            seen = True
        elif cur:
            break
    return "\n".join(cur).strip()


def _consent_from_doc(text: str, scope: str) -> tuple:
    """Возвращает (текст, версия) из документа юриста или ('', '')."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        low = line.strip().lower().replace("*", "")
        if not low.startswith("#"):
            continue
        if not any(n in low for n in CONSENT_HEADINGS[scope]):
            continue
        if scope == CONSENT_MAIN and "телефон" in low:
            continue                       # 5.2 — это согласие на номер, а не основное
        body = _first_quote_block(lines, i + 1)
        if not body:
            continue
        m = re.search(r"верси[яи]\s+([^\s,;)]+)", line, flags=re.IGNORECASE) or \
            re.search(r"верси[яи]\s+([^\s,;)]+)", body, flags=re.IGNORECASE)
        return body, (m.group(1).strip(" *.") if m else "")
    return "", ""


def consent_text(scope: str = CONSENT_MAIN) -> dict:
    """
    Текст согласия и его версия из docs/Регистрация и роли.md (раздел 5). Берётся только блок
    согласия, а не весь документ: человек видит ровно тот текст, который утвердил юрист.
    scope='телефон' — отдельное согласие на номер (раздел 5.2), оно пишется в pd_consents отдельной
    строкой. Версия и хэш показанного текста — доказательство согласия (ЗРУ-547, ст. 31).
    """
    version = (llm.get("CONSENT_VERSION") or CONSENT_VERSION_DEFAULT).strip()
    if CONSENT_DOC.exists():
        try:
            doc = CONSENT_DOC.read_text(encoding="utf-8")
            body, found = _consent_from_doc(doc, scope)
            if body:
                # версия общая для обоих согласий: её задаёт раздел 5.1
                if not found and scope == CONSENT_PHONE:
                    _, found = _consent_from_doc(doc, CONSENT_MAIN)
                return {"text": body[:3500], "version": found or version, "scope": scope,
                        "hash": _digest(body), "source": "docs/Регистрация и роли.md"}
        except Exception as e:
            print("текст согласия не разобран, берём запасной:", e)
    body = CONSENT_FALLBACK if scope == CONSENT_MAIN else CONSENT_PHONE_FALLBACK
    return {"text": body, "version": version, "scope": scope, "hash": _digest(body), "source": "заглушка"}


# --------------------------------------------------------------------------- #
#  Журнал (без персональных данных)
# --------------------------------------------------------------------------- #

def log(con, direction: str, kind: str, telegram_id=None, user_id=None, ok: bool = True,
        error: str = None, update_id=None, request_id=None):
    con.execute("INSERT INTO tg_messages (update_id, telegram_id, user_id, direction, kind, ok, error,"
                " request_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (update_id, str(telegram_id) if telegram_id is not None else None, user_id, direction,
                 kind, 1 if ok else 0, (error or None), request_id, db.now()))


# --------------------------------------------------------------------------- #
#  Отправка
# --------------------------------------------------------------------------- #

def _deliver(method: str, payload: dict) -> dict:
    """Один вызов Telegram Bot API (urllib, без сторонних пакетов). Исключений не бросает."""
    token = bot_token()
    if not token:
        return {"ok": False, "description": NOT_CONNECTED}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{API}/bot{token}/{method}", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {}
        return {"ok": False, "error_code": e.code, "description": body.get("description", f"HTTP {e.code}"),
                "parameters": body.get("parameters", {})}
    except Exception as e:
        return {"ok": False, "description": str(e)[:200]}


def kb(rows_of_buttons) -> dict:
    """Клавиатура под сообщением: [[(текст, данные), ...], ...].
    Данные, начинающиеся с http, становятся ссылкой, остальные — callback_data."""
    out = []
    for row in rows_of_buttons:
        line = []
        for text, data in row:
            if _is_app_url(data):
                # своё приложение — кнопкой мини-аппа: откроется внутри Telegram, а не в браузере как сайт
                line.append({"text": text, "web_app": {"url": data}})
            elif str(data).startswith("http"):
                line.append({"text": text, "url": data})
            else:
                line.append({"text": text, "callback_data": data})
        out.append(line)
    return {"inline_keyboard": out}


def _is_app_url(data) -> bool:
    """Ссылка на наш мини-апп (SERVER_URL/tg…) по https — такие кнопки делаем web_app.
    Telegram открывает web_app-кнопки только по https, поэтому http (локальный сервер) остаётся ссылкой."""
    base = server_url()
    d = str(data or "")
    return bool(base) and d.startswith("https://") and (d == base + "/tg" or d.startswith(base + "/tg?")
                                                        or d.startswith(base + "/tg#"))


def app_link() -> str:
    """Ссылка «Открыть приложение». Без настройки SERVER_URL кнопки просто не будет."""
    base = server_url()
    return base + "/tg" if base else ""


def _defer(con, job) -> bool:
    """
    Идёт пишущая транзакция (своя con или внешняя db.tx() этого потока) — отправку откладываем
    до её фиксации. Иначе база заперта на запись всё время сетевого вызова (до 25 с с повторами),
    и параллельные запросы получают «database is locked». Кому и что уходит — не меняется.
    """
    for c in (con, db.current_tx()):
        if c is not None and db.holds_write_lock(c) and db.after_commit(c, job):
            return True
    return False


def _journal_con(con):
    """Журнал отправки пишем своим коротким соединением, если у con нет открытой транзакции:
    иначе запись журнала сама откроет транзакцию в con и следующие отправки уйдут «после фиксации»."""
    if con is not None and hasattr(con, "after_commit_jobs") and not db.holds_write_lock(con):
        return None
    return con


DEFERRED = {"ok": True, "deferred": True, "reason": "отправка после сохранения"}


def send(chat_id, text: str, keyboard: Optional[dict] = None, kind: str = "уведомление",
         con=None, user_id: Optional[int] = None, request_id: Optional[int] = None) -> dict:
    """
    Единая отправка сообщения. Повтор при ошибке — не больше трёх попыток, пауза удваивается;
    при 429 ждём retry_after от Telegram. Без токена ничего не отправляет и честно говорит об этом.
    Внутри пишущей транзакции отправка откладывается до её фиксации (_defer).
    """
    if not connected():
        _journal_out(con, chat_id, user_id, request_id, kind, False, NOT_CONNECTED)
        return {"ok": False, "reason": NOT_CONNECTED}
    if _defer(con, lambda: send(chat_id, text, keyboard, kind, None, user_id, request_id)):
        return dict(DEFERRED)
    con = _journal_con(con)
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = keyboard
    pause, last = 1.0, {}
    for attempt in range(1, SEND_RETRIES + 1):
        last = _deliver("sendMessage", payload)
        if last.get("ok"):
            _journal_out(con, chat_id, user_id, request_id, kind, True, None)
            return {"ok": True, "message_id": (last.get("result") or {}).get("message_id")}
        if attempt == SEND_RETRIES:
            break
        wait = (last.get("parameters") or {}).get("retry_after")
        time.sleep(float(wait) if wait else pause)
        pause *= 2
    reason = last.get("description") or "Telegram не принял сообщение"
    _journal_out(con, chat_id, user_id, request_id, kind, False, reason)
    return {"ok": False, "reason": reason}


def _journal_out(con, chat_id, user_id, request_id, kind, ok, error):
    """Запись об исходящем. Если соединения нет — открываем своё (вне обработки обновления)."""
    try:
        if con is not None:
            log(con, "out", kind, chat_id, user_id, ok, error, None, request_id)
        else:
            with db.tx() as own:
                log(own, "out", kind, chat_id, user_id, ok, error, None, request_id)
    except Exception:
        pass                       # журнал не должен мешать работе бота


def _deliver_file(method: str, fields: dict, field: str, filename: str, blob: bytes, mime: str) -> dict:
    """Отправка файла в Telegram (multipart/form-data, только стандартная библиотека)."""
    token = bot_token()
    if not token:
        return {"ok": False, "description": NOT_CONNECTED}
    boundary = "----inson" + secrets.token_hex(8)
    body = bytearray()
    for k, v in fields.items():
        body += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                 % (boundary, k, v)).encode("utf-8")
    body += ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
             'Content-Type: %s\r\n\r\n' % (boundary, field, filename, mime)).encode("utf-8")
    body += blob + ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    req = urllib.request.Request(f"{API}/bot{token}/{method}", data=bytes(body),
                                 headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC * 2) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            info = json.loads(e.read().decode("utf-8"))
        except Exception:
            info = {}
        return {"ok": False, "error_code": e.code, "description": info.get("description", f"HTTP {e.code}")}
    except Exception as e:
        return {"ok": False, "description": str(e)[:200]}


def send_file(chat_id, filename: str, blob: bytes, mime: str, caption: str = "",
              kind: str = "вложение", con=None, user_id=None, request_id=None) -> dict:
    """Один файл в чат: картинка уходит как фото, остальное — документом."""
    if not connected():
        _journal_out(con, chat_id, user_id, request_id, kind, False, NOT_CONNECTED)
        return {"ok": False, "reason": NOT_CONNECTED}
    if _defer(con, lambda: send_file(chat_id, filename, blob, mime, caption, kind, None, user_id, request_id)):
        return dict(DEFERRED)
    con = _journal_con(con)
    method, field = ("sendPhoto", "photo") if str(mime).startswith("image/") else ("sendDocument", "document")
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption[:1000]
    res = _deliver_file(method, fields, field, filename, blob, mime)
    ok = bool(res.get("ok"))
    _journal_out(con, chat_id, user_id, request_id, kind, ok, None if ok else res.get("description"))
    return {"ok": ok, "reason": "" if ok else (res.get("description") or "Telegram не принял файл")}


def edit_card(con, chat_id, message_id, text: str) -> dict:
    """Обновляет уже отправленное сообщение (заявка администратору): кнопки убираются."""
    if not connected() or not message_id:
        return {"ok": False, "reason": NOT_CONNECTED}
    if _defer(con, lambda: edit_card(None, chat_id, message_id, text)):
        return dict(DEFERRED)
    con = _journal_con(con)
    res = _deliver("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text,
                                       "disable_web_page_preview": True})
    if not res.get("ok"):
        _journal_out(con, chat_id, None, None, "уведомление", False, res.get("description"))
    return res


def answer_callback(callback_id: str, text: str = ""):
    if connected() and callback_id:
        if _defer(None, lambda: answer_callback(callback_id, text)):
            return
        _deliver("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]})


# --------------------------------------------------------------------------- #
#  Пользователи и состояние диалога
# --------------------------------------------------------------------------- #

def user_by_tg(con, telegram_id) -> Optional[dict]:
    r = db.rows(con, "SELECT * FROM users WHERE telegram_id=?", str(telegram_id))
    return r[0] if r else None


def admins(con) -> list:
    return db.rows(con, "SELECT * FROM users WHERE role='админ' AND status=? AND telegram_id IS NOT NULL"
                        " AND telegram_id<>''", auth.STATUS_ACTIVE)


def dialog(con, telegram_id) -> Optional[dict]:
    r = db.rows(con, "SELECT * FROM tg_dialogs WHERE telegram_id=?", str(telegram_id))
    if not r:
        return None
    d = r[0]
    try:
        d["draft"] = json.loads(d["draft"] or "{}")
    except ValueError:
        d["draft"] = {}
    return d


def drop_dialog(con, telegram_id):
    con.execute("DELETE FROM tg_dialogs WHERE telegram_id=?", (str(telegram_id),))


# --------------------------------------------------------------------------- #
#  Команды
# --------------------------------------------------------------------------- #

def start_keyboard():
    link = app_link()
    return kb([[("Открыть приложение", link)]]) if link else None


def cmd_start(con, tg_id, update_id=None) -> dict:
    """
    Приложение открыто для всех (решение заказчика 22.09.2026): /start — одно сообщение с кнопкой
    web_app. Регистрации нет, поэтому подсказывать нечего; вход нужен только администратору,
    он входит по своему username (TG_ADMIN_USERNAME) или логином и паролем на /login.
    """
    u = user_by_tg(con, tg_id)
    drop_dialog(con, tg_id)
    if u and u["status"] == auth.STATUS_BLOCKED:
        send(tg_id, "Доступ закрыт администратором.", kind="отказ", con=con, user_id=u["id"])
        return {"action": "заблокирован", "user_id": u["id"]}
    if u and u["status"] == auth.STATUS_ACTIVE:
        send(tg_id, f"Здравствуйте! Вы вошли как {u['role']}. " + OPEN_APP + "\n\n" + HELP,
             start_keyboard(), kind="start", con=con, user_id=u["id"])
        return {"action": "вход", "user_id": u["id"]}
    send(tg_id, "Здравствуйте! " + OPEN_APP, start_keyboard(), kind="start", con=con,
         user_id=(u or {}).get("id"))
    return {"action": "открытое приложение", "user_id": (u or {}).get("id")}


def cmd_me(con, tg_id) -> dict:
    u = user_by_tg(con, tg_id)
    if not u:
        send(tg_id, "Учётной записи у вас нет — она и не нужна: приложение открыто для всех. "
                    + OPEN_APP, start_keyboard(), kind="команда", con=con)
        return {"action": "гость"}
    send(tg_id, f"Роль: {u['role']}\nФилиал: {u.get('branch') or 'не указан'}\nСостояние: {u['status']}",
         kind="команда", con=con, user_id=u["id"])
    return {"action": "me", "user_id": u["id"], "status": u["status"]}


def cmd_inbox(con, tg_id) -> dict:
    """/inbox убран из команд (21.09.2026): согласования в Telegram больше нет, отправляем в приложение."""
    u = user_by_tg(con, tg_id)
    send(tg_id, ANALYTICS_ONLY, start_keyboard(), kind="команда", con=con, user_id=(u or {}).get("id"))
    return {"action": "inbox отключён"}


def cmd_admin(con, tg_id, code: str) -> dict:
    """Разовый код первого администратора. Код нигде не печатается — только маска."""
    code = (code or "").strip()
    setting = (llm.get("ADMIN_BOOTSTRAP_CODE") or "").strip()
    used = (llm.get("ADMIN_BOOTSTRAP_USED") or "").strip()
    digest = _digest(code) if code else ""
    deny = None
    if not code:
        deny = "Укажите код: /admin КОД"
    elif used and digest and hmac.compare_digest(used, digest):
        # код гасится навсегда: отметка о погашении проверяется раньше самой настройки,
        # иначе после удаления кода ответ был бы «код не задан» вместо «уже использован»
        deny = "Этот код уже использован."
    elif not setting:
        deny = "Код первого администратора не задан. Обратитесь к тому, кто настраивает систему."
    elif not _same(setting, code):
        deny = "Код не подходит."
    if deny:
        log(con, "in", "отказ", tg_id, None, False, "admin bootstrap")
        send(tg_id, deny, kind="отказ", con=con)
        return {"action": "отказ", "reason": deny}

    u = user_by_tg(con, tg_id)
    if u:
        con.execute("UPDATE users SET role='админ', status=?, approved_by=?, approved_at=? WHERE id=?",
                    (auth.STATUS_ACTIVE, "bootstrap", db.now(), u["id"]))
        uid = u["id"]
    else:
        pw_hash, salt = auth.hash_password(hashlib.sha256((code + str(tg_id)).encode()).hexdigest())
        cur = con.execute("INSERT INTO users (login, full_name, phone, role, branch, password_hash, salt,"
                          " status, telegram_id, created_at, approved_by, approved_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (f"tg{tg_id}", f"Администратор {tg_id}", "", "админ", "", pw_hash, salt,
                           auth.STATUS_ACTIVE, str(tg_id), db.now(), "bootstrap", db.now()))
        uid = cur.lastrowid
    _setting_set(con, "ADMIN_BOOTSTRAP_USED", digest)
    con.execute("DELETE FROM app_settings WHERE key='ADMIN_BOOTSTRAP_CODE'")
    drop_dialog(con, tg_id)
    db.audit(con, f"tg{tg_id}", "первый администратор по коду", f"user:{uid}",
             {"telegram_id": str(tg_id), "код": llm.mask_key(code)})
    send(tg_id, "Готово: вы администратор системы. " + HELP, kind="команда", con=con, user_id=uid)
    return {"action": "админ создан", "user_id": uid}


# --------------------------------------------------------------------------- #
#  Свободный текст в чате
# --------------------------------------------------------------------------- #

def dialog_text(con, tg_id, text: str) -> dict:
    """Свободный текст в чате. Анкеты в боте больше нет: регистрация — в мини-приложении."""
    # старый незаконченный шаг «вопрос к решению» просто сбрасываем: решения в боте больше нет
    if dialog(con, tg_id):
        drop_dialog(con, tg_id)
    send(tg_id, "Не понял. " + HELP, start_keyboard(), kind="команда", con=con)
    return {"action": "не понял"}


def notify_admins_new_user(con, uid: int, role: str):
    """Администраторам — сообщение о новом работнике. Подтверждать его больше не надо:
    человек активен сразу после регистрации (решение заказчика 21.09.2026)."""
    u = db.rows(con, "SELECT full_name, department, position FROM users WHERE id=?", uid)
    who = u[0] if u else {}
    text = (f"Зарегистрировался работник № {uid}.\n"
            f"{who.get('full_name') or 'ФИО не указано'}\n"
            f"Департамент: {who.get('department') or 'не указан'}\n"
            f"Должность: {who.get('position') or 'не указана'}\n"
            f"Роль: {role}. Права администратора выдаются в разделе «Пользователи».")
    link = app_link()
    keyboard = kb([[("Открыть приложение", link)]]) if link else None
    for a in admins(con):
        send(a["telegram_id"], text, keyboard, kind="уведомление", con=con, user_id=a["id"])


def handle_callback(con, cq: dict, update_id=None) -> dict:
    """Кто нажал — определяем ТОЛЬКО по telegram_id отправителя, данные кнопки не доверенные."""
    tg_id = str(((cq.get("from") or {}).get("id")) or "")
    data = (cq.get("data") or "").strip()
    msg = cq.get("message") or {}
    chat_id = ((msg.get("chat") or {}).get("id")) or tg_id
    message_id = msg.get("message_id")
    cq_id = cq.get("id") or ""
    parts = data.split(":")
    action = parts[0] if parts else ""

    if action in ("uapprove", "ureject"):
        admin = user_by_tg(con, tg_id)
        if not admin or admin["role"] != "админ" or admin["status"] != auth.STATUS_ACTIVE:
            log(con, "in", "отказ", tg_id, (admin or {}).get("id"), False, "not admin", update_id)
            answer_callback(cq_id, "Заявки подтверждает администратор")
            return {"action": "отказ", "reason": "не администратор"}
        uid = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        target = db.rows(con, "SELECT * FROM users WHERE id=?", uid)
        if not target:
            answer_callback(cq_id, "Заявка не найдена")
            return {"action": "отказ", "reason": "нет заявки"}
        if action == "ureject":
            auth.reject_user(con, admin, uid, "отклонено в боте")
            answer_callback(cq_id, "Отклонено")
            if target[0].get("telegram_id"):
                send(target[0]["telegram_id"], "Заявка отклонена администратором.", kind="уведомление",
                     con=con, user_id=None)
            if message_id:
                edit_card(con, chat_id, message_id, f"Заявка № {uid}: отклонена ({admin['login']}).")
            return {"action": "заявка отклонена", "user_id": uid}
        role = parts[2] if len(parts) > 2 else target[0]["role"]
        if role in auth.ROLES and role != target[0]["role"]:
            con.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
            db.audit(con, admin["login"], "роль изменена при подтверждении", f"user:{uid}",
                     {"было": target[0]["role"], "стало": role})
        auth.approve_user(con, admin, uid)
        log(con, "in", "callback", tg_id, admin["id"], True, None, update_id)
        answer_callback(cq_id, "Подтверждено")
        if target[0].get("telegram_id"):
            send(target[0]["telegram_id"], f"Доступ открыт. Ваша роль: {role}. " + HELP,
                 kind="уведомление", con=con, user_id=uid)
        if message_id:
            edit_card(con, chat_id, message_id, f"Заявка № {uid}: подтверждена как {role} ({admin['login']}).")
        return {"action": "заявка подтверждена", "user_id": uid, "role": role}

    if action in ("approve", "reject", "ask"):
        # кнопки решения из старых сообщений: решение не пишем, только отвечаем на нажатие
        answer_callback(cq_id, APPROVAL_OFF)
        log(con, "in", "callback", tg_id, (user_by_tg(con, tg_id) or {}).get("id"), False,
            "согласование отключено", update_id)
        return {"action": "согласование отключено"}

    answer_callback(cq_id)
    log(con, "in", "callback", tg_id, None, False, "unknown action", update_id)
    return {"action": "неизвестная кнопка"}


# --------------------------------------------------------------------------- #
#  Разбор обновления
# --------------------------------------------------------------------------- #

def link_code_message(con, tg_id, frm: dict, code: str, update_id=None) -> dict:
    """Код входа из браузера (app/tg_link.py). Сам код в журнал не попадает — только исход."""
    from . import tg_link
    res = tg_link.bind(con, code, frm or {"id": tg_id})
    log(con, "in", "код входа", tg_id, None, res["ok"], (None if res["ok"] else res["reason"][:120]), update_id)
    send(tg_id, "Готово, вернитесь в браузер — страница войдёт сама." if res["ok"] else res["reason"],
         kind="код входа", con=con)
    return {"action": "код входа принят" if res["ok"] else "код входа отклонён"}


def handle_message(con, msg: dict, update_id=None) -> dict:
    tg_id = str(((msg.get("from") or {}).get("id")) or ((msg.get("chat") or {}).get("id")) or "")
    if not tg_id:
        return {"action": "без отправителя"}
    text = (msg.get("text") or "").strip()
    frm = msg.get("from") or {}
    u = user_by_tg(con, tg_id)
    if text.startswith("/"):
        cmd, _, rest = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        log(con, "in", "start" if cmd == "/start" else "команда", tg_id, (u or {}).get("id"), True, None, update_id)
        if cmd == "/start":
            # «/start link_INS-7K3M» — человек пришёл по ссылке со страницы входа в браузере
            if rest.strip().lower().startswith("link_"):
                return link_code_message(con, tg_id, frm, rest.strip()[5:], update_id)
            return cmd_start(con, tg_id, update_id)
        if cmd == "/me":
            return cmd_me(con, tg_id)
        if cmd == "/inbox":
            return cmd_inbox(con, tg_id)
        if cmd == "/admin":
            return cmd_admin(con, tg_id, rest)
        if cmd == "/help":
            send(tg_id, HELP, kind="команда", con=con, user_id=(u or {}).get("id"))
            return {"action": "help"}
        send(tg_id, "Такой команды нет. " + HELP, kind="команда", con=con, user_id=(u or {}).get("id"))
        return {"action": "нет команды"}
    # голый код входа в чате: «INS-7K3M» без всяких команд
    from . import tg_link
    if tg_link.looks_like_code(text) and not dialog(con, tg_id):
        return link_code_message(con, tg_id, frm, text, update_id)
    log(con, "in", "команда", tg_id, (u or {}).get("id"), True, None, update_id)
    return dialog_text(con, tg_id, text)


def remember_owner(con, frm: dict):
    """Запоминает чат владельца (username из TG_ADMIN_USERNAME), чтобы система могла писать ему
    служебные уведомления («работа закончена»). Хранится только telegram_id, один раз, в app_settings."""
    boot = (llm.get("TG_ADMIN_USERNAME") or "").strip().lstrip("@").lower()
    uname = ((frm or {}).get("username") or "").strip().lower()
    tg_id = str((frm or {}).get("id") or "")
    if not boot or not uname or uname != boot or not tg_id.isdigit():
        return
    if (llm.get("TG_OWNER_CHAT_ID") or "") == tg_id:
        return
    _setting_set(con, "TG_OWNER_CHAT_ID", tg_id)
    print(f"бот: чат владельца сохранён, TG_OWNER_CHAT_ID={tg_id}")


def handle_update(con, update: dict) -> dict:
    """Разбор одного обновления. Идемпотентность проверяется снаружи (seen_update)."""
    update_id = update.get("update_id")
    src = update.get("callback_query") or update.get("message") or update.get("edited_message") or {}
    remember_owner(con, src.get("from") or {})
    if update.get("callback_query"):
        return handle_callback(con, update["callback_query"], update_id)
    msg = update.get("message") or update.get("edited_message")
    if msg:
        return handle_message(con, msg, update_id)
    return {"action": "нечего разбирать"}


def seen_update(con, update_id) -> bool:
    """True, если это обновление уже обрабатывали. Иначе отмечает его как обработанное."""
    if update_id is None:
        return False
    if db.rows(con, "SELECT 1 FROM tg_updates WHERE update_id=?", int(update_id)):
        return True
    con.execute("INSERT INTO tg_updates (update_id, received_at) VALUES (?,?)", (int(update_id), db.now()))
    return False


def process(update: dict) -> dict:
    """Точка входа и для вебхука, и для опроса: одна транзакция на обновление."""
    with db.tx() as con:
        if seen_update(con, update.get("update_id")):
            return {"ok": True, "skipped": "повтор"}
        try:
            res = handle_update(con, update)
        except Exception as e:                # ни одна ошибка разбора не должна ронять приём
            log(con, "in", "ошибка", ((update.get("message") or {}).get("from") or {}).get("id"),
                None, False, str(e)[:200], update.get("update_id"))
            return {"ok": True, "error": str(e)[:200]}
        return {"ok": True, "result": res}


# --------------------------------------------------------------------------- #
#  Вебхук
# --------------------------------------------------------------------------- #

@router.post("/tg/webhook/{secret}")
async def webhook(secret: str, request: Request):
    """
    Telegram при ошибке повторяет доставку, поэтому на разобранное обновление всегда отвечаем 200:
    внутренние сбои идут в журнал, а не в ответ. Неверный секрет — 403, тело обновления не сохраняем.
    """
    want = webhook_secret()
    header = request.headers.get("x-telegram-bot-api-secret-token", "")
    ok = _same(secret, want) or _same(header, want)
    if not ok:
        try:
            with db.tx() as con:
                log(con, "in", "отказ", None, None, False, "неверный секрет вебхука")
        except Exception:
            pass
        raise HTTPException(403, "Неверный адрес вебхука")
    raw = await request.body()
    try:
        update = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(update, dict):
            raise ValueError("ожидался объект")
    except Exception as e:
        with db.tx() as con:
            log(con, "in", "ошибка", None, None, False, f"нечитаемое обновление: {str(e)[:100]}")
        return JSONResponse({"ok": True})
    out = process(update)
    return JSONResponse({"ok": True} | ({"skipped": out["skipped"]} if out.get("skipped") else {}))


# --------------------------------------------------------------------------- #
#  Запасной режим: опрос getUpdates (для локальной машины без адреса в интернете)
# --------------------------------------------------------------------------- #

_poll_state = {"running": False, "offset": 0, "last_error": None}


def poll_once() -> int:
    res = _deliver("getUpdates", {"timeout": POLL_TIMEOUT_SEC, "offset": _poll_state["offset"] or None})
    if not res.get("ok"):
        _poll_state["last_error"] = res.get("description")
        return 0
    updates = res.get("result") or []
    for up in updates:
        _poll_state["offset"] = int(up.get("update_id", 0)) + 1
        process(up)
    return len(updates)


def poll_loop():
    """Поток опроса. Поднимается только при TG_POLLING=1 и заданном токене."""
    _poll_state["running"] = True
    time.sleep(5)
    while True:
        try:
            if not connected() or not polling_enabled():
                _poll_state["running"] = False
                return
            poll_once()
            background.ok("tg-polling")
        except Exception as e:
            _poll_state["last_error"] = str(e)[:200]
            background.failed("tg-polling", e)
            time.sleep(10)


def start_polling():
    """Вызывается из app/main.py на старте. Без токена или без флага поток не создаётся вовсе."""
    if not connected() or not polling_enabled() or _poll_state["running"]:
        return False
    return background.start("tg-polling", poll_loop)


# --------------------------------------------------------------------------- #
#  Точки для мини-приложения
# --------------------------------------------------------------------------- #

# «Пользователи» видят все зарегистрированные (решение заказчика 21.09.2026): список открыт,
# кнопки «Сделать админом» / «Снять админа» показываются только админу (can_manage в /tg/users).
# Меню 21.09.2026 (задача 144): «Аналитика» первой, «ОСГОР» после «Расчёта»; «Мои запросы» из меню
# убраны, но точка /tg/my-requests и данные запросов остаются.
NAV_BASE = [("analytics", "Аналитика"), ("calc", "Расчёт"), ("osgor", "ОСГОР"), ("legal", "Юрист"),
            ("photos", "Фото"), ("users", "Пользователи")]
# Гостевое меню (22.09.2026): приложение открыто для всех, вход нужен только администратору.
# «Пользователи» гостю не показываем — это раздел админки.
NAV_GUEST = [("analytics", "Аналитика"), ("calc", "Расчёт"), ("osgor", "ОСГОР"), ("legal", "Юрист"),
             ("photos", "Фото")]
# Что гостю можно: только чтение и расчёт, ничего не сохраняя в справочники.
GUEST_RIGHTS = ["расчёт", "аналитика риска", "ОСГОР", "юридические ответы",
                "справочники (чтение)", "договор для анализа (24 часа)"]
GUEST_STATUS = "гость"
GUEST_LOGIN_URL = "/login?next=/tg"

# Справочники, которые админ правит из мини-приложения: ключ → метод, путь чтения и записи.
# Остальным ролям can_edit пуст. Чек-листов здесь нет: API их правки нет.
EDITABLE = [
    {"key": "coefficients", "title": "Коэффициенты", "read": "GET /reference/coefficients",
     "write": "POST /admin/coefficients"},
    {"key": "min_rates", "title": "Минимальные ставки", "read": "GET /reference/min_rates",
     "write": "POST /admin/min-rates"},
    {"key": "depreciation_norms", "title": "Нормы износа", "read": "GET /valuation/norms",
     "write": "POST /valuation/norms"},
    {"key": "osgor_brv", "title": "БРВ для ОСГОР", "read": "GET /osgor/brv", "write": "PUT /osgor/brv"},
    {"key": "risk_thresholds", "title": "Пороги уровня риска", "read": "GET /analytics/risk/thresholds",
     "write": "PUT /analytics/risk/thresholds"},
]


def can_edit(u: dict) -> list:
    return [e["key"] for e in EDITABLE] if u.get("role") == "админ" else []
# 21.09.2026 (задача 150): «Ждут меня», «Заявки», «Генеральные соглашения» из меню мини-аппа убраны
# для всех ролей; точки /tg/inbox, /approvals, /agreements и данные остаются.
# «Админка» отдельной кнопкой в меню не идёт: путь в неё — admin_available + login_url
NAV_ADMIN = [("settings", "Настройки")]


def _session_user(request: Request) -> Optional[dict]:
    with db.tx() as con:
        return auth.session_user(con, auth.request_token(request))


def _mode() -> str:
    from .telegram import MODE_BROWSER, MODE_TG
    return MODE_TG if connected() else MODE_BROWSER


def _wants_user_view(request: Request, view: Optional[str]) -> bool:
    """Режим «как видит сотрудник»: ?view=user или заголовок X-View: user."""
    return (view or request.headers.get("x-view") or "").strip().lower() == "user"


@router.get("/tg/me")
def tg_me(request: Request, view: Optional[str] = None):
    """Кто вошёл, что ему можно и какие разделы показывать. Работает и по cookie-сессии.

    ?view=user (или заголовок X-View: user) — кнопка «Выйти из админки» (22.09.2026): ответ собирается
    так, как его увидел бы «сотрудник» (меню, права, can_edit, role, is_admin). Это только отображение:
    сессия и права на сервере не меняются, админские API админа по-прежнему пускают.
    real_role и admin_available — чтобы мини-апп показал «Вернуться в админку»."""
    u = _session_user(request)
    as_user = _wants_user_view(request, view)
    if not u or u["status"] != auth.STATUS_ACTIVE:
        # гостевой режим (22.09.2026): приложение открыто, вход нужен только администратору.
        # Незавершённая или заблокированная учётная запись тоже видит приложение как гость —
        # ждать подтверждения больше не нужно, регистрация отменена.
        return {"mode": "guest", "tg_mode": _mode(), "status": GUEST_STATUS, "user": None,
                "guest": True,
                "nav": [{"key": k, "title": t} for k, t in NAV_GUEST],
                "rights": list(GUEST_RIGHTS), "can_edit": [],
                "admin_available": False, "login_url": GUEST_LOGIN_URL,
                "view": "user" if as_user else "full", "real_role": None,
                "reason": "Приложение открыто для всех. Вход нужен только администратору"}
    shown = dict(u, role=auth.ROLE_EMPLOYEE) if as_user else u
    nav = list(NAV_BASE)
    if shown["role"] == "админ":
        nav += NAV_ADMIN
    # name — то же, что full_name: с 21.09.2026 в анкете одно поле «Имя», хранится в users.full_name
    return {"mode": _mode(), "tg_mode": _mode(), "guest": False, "login_url": "",
            "status": auth.STATUS_ACTIVE,
            "user": {"id": u["id"], "full_name": u["full_name"], "name": u["full_name"], "role": shown["role"],
                     "branch": u.get("branch"), "status": u["status"], "is_admin": shown["role"] == "админ"},
            "rights": sorted(r for r in auth.PERMISSIONS if auth.can(shown, r)),
            "can_edit": can_edit(shown),
            "nav": [{"key": k, "title": t} for k, t in nav],
            "view": "user" if as_user else "full", "real_role": u["role"],
            "admin_available": u["role"] == "админ",
            "reason": ""}


@router.get("/tg/inbox")
def tg_inbox(request: Request):
    """Что ждёт решения вошедшего — обёртка над approvals.inbox по сессии, без имени в адресе."""
    u = _session_user(request)
    if not u:
        raise HTTPException(401, "Нужно войти в систему")
    with db.tx() as con:
        return {"count": len(approvals.inbox(con, u["login"])), "items": approvals.inbox(con, u["login"])}


@router.get("/tg/my-requests")
def tg_my_requests(request: Request):
    """Свои запросы вошедшего: связь человека с запросом — по ID агента в ЕАИС."""
    u = _session_user(request)
    if not u:
        raise HTTPException(401, "Нужно войти в систему")
    out = []
    with db.tx() as con:
        if not u.get("agent_eais_id"):
            return {"count": 0, "items": [],
                    "reason": "К вашей учётной записи не привязан ID агента — своих запросов нет"}
        rs = db.rows(con, """SELECT r.*, o.object_type, o.sum_insured, g.partner
                             FROM requests r
                             LEFT JOIN objects o ON o.request_id = r.id
                             LEFT JOIN general_agreements g ON g.id = r.general_agreement_id
                             JOIN agents a ON a.id = r.agent_id
                             WHERE a.eais_id=? ORDER BY r.id DESC LIMIT 50""", u["agent_eais_id"])
        seen = set()
        for r in rs:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            calc = db.rows(con, "SELECT premium, applied_rate_pct, gross_rate_pct FROM calculations"
                                " WHERE request_id=? ORDER BY id DESC LIMIT 1", r["id"])
            out.append({"id": r["id"], "external_no": r.get("external_no"), "product_code": r.get("product_code"),
                        "object_type": r.get("object_type"), "sum_insured": r.get("sum_insured"),
                        "premium": calc[0]["premium"] if calc else None,
                        "rate_pct": (calc[0]["applied_rate_pct"] or calc[0]["gross_rate_pct"]) if calc else None,
                        "approval_status": r.get("approval_status"), "partner": r.get("partner"),
                        "probability": outcomes.brief(con, r["id"]),
                        "reviewers": [{"full_name": x["full_name"], "position": x["position"],
                                       "status": x["status"], "comment": x["comment"],
                                       "decided_at": x["decided_at"]}
                                      for x in approvals.reviewers(con, r["id"])]})
    return {"count": len(out), "items": out}


@router.get("/tg/bot-status")
def tg_bot_status():
    """Состояние бота для админки мини-аппа. Ни токен, ни код администратора наружу не уходят."""
    with db.tx() as con:
        n = con.execute("SELECT COUNT(*) FROM tg_messages").fetchone()[0]
        last = db.rows(con, "SELECT created_at FROM tg_messages ORDER BY id DESC LIMIT 1")
    return {"connected": connected(), "token_mask": llm.mask_key(bot_token()),
            "webhook_set": bool(webhook_secret()),
            "webhook_url": (f"{server_url()}/tg/webhook/…" if server_url() and webhook_secret() else ""),
            "polling": polling_enabled(), "polling_running": _poll_state["running"],
            "polling_error": _poll_state["last_error"],
            "bootstrap_code_set": bool((llm.get("ADMIN_BOOTSTRAP_CODE") or "").strip()),
            "messages": n, "last_message_at": last[0]["created_at"] if last else None,
            "consent_version": consent_text()["version"], "consent_source": consent_text()["source"],
            "reason": "" if connected() else NOT_CONNECTED}
