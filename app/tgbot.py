"""
Бот Telegram: регистрация, команды и согласование запросов прямо в чате.

Как это работает для заказчика — docs/Telegram мини-апп.md, раздел «Бот».
Коротко:
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

Проверка подлинности решения: кто нажал кнопку, определяется по telegram_id отправителя нажатия,
а не по содержимому callback_data. Подделать чужое решение подменой данных кнопки нельзя.

Тесты — tests/test_tgbot.py (поддельные обновления, сеть не трогается).
"""
import hashlib
import hmac
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import approvals, auth, db, llm

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent
CONSENT_DOC = ROOT / "docs" / "Регистрация и роли.md"

API = "https://api.telegram.org"
TIMEOUT_SEC = 25
SEND_RETRIES = 3                      # не больше трёх попыток
POLL_TIMEOUT_SEC = 25                 # long polling: сколько Telegram держит соединение

NOT_CONNECTED = "Бот не подключён"

# шаги диалога регистрации
S_CONSENT, S_NAME, S_BRANCH, S_POSITION, S_ROLE, S_AGENT, S_QUESTION = (
    "согласие", "фио", "филиал", "должность", "роль", "id агента", "вопрос")

ROLE_BUTTONS = [("агент", "Агент"), ("андеррайтер", "Андеррайтер"), ("админ", "Администратор")]

HELP = ("Что умеет бот:\n"
        "/start — регистрация или вход\n"
        "/me — кто я, роль и состояние учётной записи\n"
        "/inbox — запросы, которые ждут моего решения\n"
        "/help — эта подсказка\n"
        "/admin КОД — разовый код первого администратора")

WAIT_MSG = ("Заявка принята и ждёт подтверждения администратора. "
            "Как только вас подтвердят, расчёт и согласование откроются.")


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


CONSENT_VERSION_DEFAULT = "ПД-1"
CONSENT_MAIN, CONSENT_PHONE = "основное", "телефон"
CONSENT_HEADING = "текст согласия"          # заголовок раздела 6 в документе юриста


def _consent_blocks(text: str) -> list:
    """Блоки-цитаты («> ...») после заголовка «## ... Текст согласия». Первый — основное
    согласие, второй — отдельное согласие на номер телефона (раздел 6 документа юриста)."""
    blocks, cur, started = [], [], False
    for line in text.splitlines():
        low = line.strip().lower()
        if low.startswith("#"):
            if CONSENT_HEADING in low.replace("*", ""):
                started, blocks, cur = True, [], []
                continue
            if started:                     # следующий раздел — дальше не смотрим
                break
        if not started:
            continue
        if line.startswith(">"):
            cur.append(line.lstrip(">").strip().replace("**", ""))
        elif cur:
            blocks.append("\n".join(cur).strip())
            cur = []
    if cur:
        blocks.append("\n".join(cur).strip())
    return [b for b in blocks if b]


def _consent_version(text: str) -> str:
    """Версия из заголовка раздела 6: «## 6. Текст согласия — версия ПД-1 от 20.09.2026»."""
    for line in text.splitlines():
        low = line.strip().lower().replace("*", "")
        if low.startswith("#") and CONSENT_HEADING in low:
            m = re.search(r"верси[яи]\s+([^\s,;]+)", line, flags=re.IGNORECASE)
            if m:
                return m.group(1).strip(" *.")
    return ""


def consent_text(scope: str = CONSENT_MAIN) -> dict:
    """
    Текст согласия и его версия из docs/Регистрация и роли.md (раздел 6). Берётся только блок
    согласия, а не весь документ: человеку в боте уходит ровно тот текст, который утвердил юрист.
    scope='телефон' — отдельное согласие на номер (второй блок того же раздела).
    """
    version = (llm.get("CONSENT_VERSION") or CONSENT_VERSION_DEFAULT).strip()
    if CONSENT_DOC.exists():
        try:
            doc = CONSENT_DOC.read_text(encoding="utf-8")
            blocks = _consent_blocks(doc)
            version = _consent_version(doc) or version
            idx = 1 if scope == CONSENT_PHONE else 0
            if len(blocks) > idx:
                body = blocks[idx]
                return {"text": body[:3500], "version": version, "scope": scope,
                        "hash": _digest(body), "source": "docs/Регистрация и роли.md"}
        except Exception:
            pass
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
    """Клавиатура под сообщением: [[(текст, данные), ...], ...]."""
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows_of_buttons]}


def send(chat_id, text: str, keyboard: Optional[dict] = None, kind: str = "уведомление",
         con=None, user_id: Optional[int] = None, request_id: Optional[int] = None) -> dict:
    """
    Единая отправка сообщения. Повтор при ошибке — не больше трёх попыток, пауза удваивается;
    при 429 ждём retry_after от Telegram. Без токена ничего не отправляет и честно говорит об этом.
    """
    if not connected():
        _journal_out(con, chat_id, user_id, request_id, kind, False, NOT_CONNECTED)
        return {"ok": False, "reason": NOT_CONNECTED}
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


def edit_card(con, chat_id, message_id, text: str) -> dict:
    """Обновляет уже отправленную карточку: кнопки убираются, видно, кто и что решил."""
    if not connected() or not message_id:
        return {"ok": False, "reason": NOT_CONNECTED}
    res = _deliver("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text,
                                       "disable_web_page_preview": True})
    if not res.get("ok"):
        _journal_out(con, chat_id, None, None, "уведомление", False, res.get("description"))
    return res


def answer_callback(callback_id: str, text: str = ""):
    if connected() and callback_id:
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


def set_dialog(con, telegram_id, step: str, draft: dict):
    con.execute("DELETE FROM tg_dialogs WHERE telegram_id=?", (str(telegram_id),))
    con.execute("INSERT INTO tg_dialogs (telegram_id, step, draft, updated_at) VALUES (?,?,?,?)",
                (str(telegram_id), step, json.dumps(draft, ensure_ascii=False), db.now()))


def drop_dialog(con, telegram_id):
    con.execute("DELETE FROM tg_dialogs WHERE telegram_id=?", (str(telegram_id),))


# --------------------------------------------------------------------------- #
#  Карточки и тексты
# --------------------------------------------------------------------------- #

def request_card(con, request_id: int) -> str:
    """Карточка запроса для согласующего: сведений о страхователе в неё не кладём."""
    r = db.rows(con, "SELECT * FROM requests WHERE id=?", request_id)
    if not r:
        return f"Запрос {request_id} не найден"
    r = r[0]
    obj = db.rows(con, "SELECT * FROM objects WHERE request_id=? ORDER BY id LIMIT 1", request_id)
    calc = db.rows(con, "SELECT * FROM calculations WHERE request_id=? ORDER BY id DESC LIMIT 1", request_id)
    prod = db.rows(con, "SELECT name FROM products WHERE code=?", r.get("product_code") or "")
    agent = db.rows(con, "SELECT name FROM agents WHERE id=?", r.get("agent_id")) if r.get("agent_id") else []
    partner = db.rows(con, "SELECT partner FROM general_agreements WHERE id=?",
                      r.get("general_agreement_id")) if r.get("general_agreement_id") else []

    def money(v):
        return f"{v:,.0f}".replace(",", " ") + " сум" if isinstance(v, (int, float)) and v else "не указана"

    lines = [f"Запрос № {r.get('external_no') or r['id']} · филиал: {r.get('branch') or 'не указан'}",
             f"Продукт: {r.get('product_code') or '—'} {prod[0]['name'] if prod else ''}".strip(),
             f"Объект: {obj[0]['object_type'] if obj else 'не указан'}",
             f"Страховая сумма: {money(obj[0]['sum_insured'] if obj else None)}"]
    if calc:
        rate = calc[0].get("applied_rate_pct") or calc[0].get("gross_rate_pct")
        lines.append(f"Ставка: {rate:.3f} %" if rate else "Ставка: не рассчитана")
        lines.append(f"Премия: {money(calc[0].get('premium'))}")
    lines.append(f"Подал: {agent[0]['name'] if agent else 'не указан'}")
    if partner:
        lines.append(f"Генеральное соглашение: {partner[0]['partner']}")
    return "\n".join(lines)


def decision_keyboard(request_id: int, reviewer_row_id: int) -> dict:
    return kb([[("Одобрить", f"approve:{request_id}:{reviewer_row_id}"),
                ("Отклонить", f"reject:{request_id}:{reviewer_row_id}")],
               [("Вопрос", f"ask:{request_id}:{reviewer_row_id}")]])


# --------------------------------------------------------------------------- #
#  Хуки из app/approvals.py (вызываются мягко, через try/except)
# --------------------------------------------------------------------------- #

def on_assigned(con, request_id: int, user_ids: list):
    """Назначили согласующих — каждому, у кого есть Telegram, уходит карточка с кнопками."""
    card = request_card(con, request_id)
    for row in approvals.reviewers(con, request_id):
        u = db.rows(con, "SELECT * FROM users WHERE id=?", row["user_id"])
        if not u or not u[0].get("telegram_id"):
            continue
        send(u[0]["telegram_id"], "Вам на согласование:\n\n" + card,
             decision_keyboard(request_id, row["id"]), kind="уведомление",
             con=con, user_id=u[0]["id"], request_id=request_id)


def on_decided(con, request_id: int, user: dict, decision: str, comment: Optional[str], status: str):
    """Итог согласования — инициатору и всем согласующим (только когда итог окончательный)."""
    if status not in (approvals.APPROVED, approvals.REJECTED):
        return
    who = user.get("full_name") or user.get("login")
    text = (f"Запрос № {request_id}: итог «{status}».\n"
            f"Последнее решение: {decision} — {who}." + (f"\nКомментарий: {comment}" if comment else ""))
    targets = {}
    for row in approvals.reviewers(con, request_id):
        u = db.rows(con, "SELECT * FROM users WHERE id=?", row["user_id"])
        if u and u[0].get("telegram_id"):
            targets[u[0]["id"]] = u[0]
    for uid in approvals.initiator_user_ids(con, request_id):
        u = db.rows(con, "SELECT * FROM users WHERE id=?", uid)
        if u and u[0].get("telegram_id"):
            targets[uid] = u[0]
    for uid, u in targets.items():
        send(u["telegram_id"], text, kind="уведомление", con=con, user_id=uid, request_id=request_id)


# --------------------------------------------------------------------------- #
#  Команды
# --------------------------------------------------------------------------- #

def cmd_start(con, tg_id, update_id=None) -> dict:
    u = user_by_tg(con, tg_id)
    if u and u["status"] == auth.STATUS_ACTIVE:
        drop_dialog(con, tg_id)
        send(tg_id, f"Здравствуйте! Вы вошли как {u['role']}. " + HELP, kind="start", con=con, user_id=u["id"])
        return {"action": "вход", "user_id": u["id"]}
    if u and u["status"] == auth.STATUS_BLOCKED:
        send(tg_id, "Доступ закрыт администратором.", kind="отказ", con=con, user_id=u["id"])
        return {"action": "заблокирован", "user_id": u["id"]}
    if u:
        send(tg_id, WAIT_MSG, kind="start", con=con, user_id=u["id"])
        return {"action": "ожидает", "user_id": u["id"]}
    c = consent_text()
    set_dialog(con, tg_id, S_CONSENT, {"consent_version": c["version"], "consent_hash": c["hash"]})
    send(tg_id, c["text"] + "\n\nВерсия текста: " + c["version"],
         kb([[("Согласен", "consent:ok"), ("Не согласен", "consent:no")]]), kind="start", con=con)
    return {"action": "согласие", "step": S_CONSENT}


def cmd_me(con, tg_id) -> dict:
    u = user_by_tg(con, tg_id)
    if not u:
        send(tg_id, "Вы ещё не зарегистрированы. Начните с команды /start.", kind="команда", con=con)
        return {"action": "не зарегистрирован"}
    send(tg_id, f"Роль: {u['role']}\nФилиал: {u.get('branch') or 'не указан'}\nСостояние: {u['status']}",
         kind="команда", con=con, user_id=u["id"])
    return {"action": "me", "user_id": u["id"], "status": u["status"]}


def cmd_inbox(con, tg_id) -> dict:
    u = user_by_tg(con, tg_id)
    if not u or u["status"] != auth.STATUS_ACTIVE:
        send(tg_id, WAIT_MSG if u else "Вы ещё не зарегистрированы. Начните с команды /start.",
             kind="команда", con=con, user_id=(u["id"] if u else None))
        return {"action": "нет доступа"}
    items = approvals.inbox(con, u["login"])
    if not items:
        send(tg_id, "Запросов, ждущих вашего решения, нет.", kind="команда", con=con, user_id=u["id"])
        return {"action": "inbox", "count": 0}
    for it in items[:10]:
        row = db.rows(con, "SELECT id FROM request_reviewers WHERE request_id=? AND user_id=?",
                      it["request_id"], u["id"])
        send(tg_id, request_card(con, it["request_id"]),
             decision_keyboard(it["request_id"], row[0]["id"]) if row else None,
             kind="команда", con=con, user_id=u["id"], request_id=it["request_id"])
    return {"action": "inbox", "count": len(items)}


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
#  Пошаговая регистрация
# --------------------------------------------------------------------------- #

def _ask(con, tg_id, step: str, draft: dict, text: str, keyboard=None):
    set_dialog(con, tg_id, step, draft)
    send(tg_id, text, keyboard, kind="команда", con=con)


def dialog_text(con, tg_id, text: str) -> dict:
    """Очередной ответ человека в пошаговом диалоге (регистрация или комментарий к «Вопросу»)."""
    d = dialog(con, tg_id)
    if not d:
        send(tg_id, "Не понял. " + HELP, kind="команда", con=con)
        return {"action": "не понял"}
    step, draft = d["step"], d["draft"]
    value = (text or "").strip()

    if step == S_QUESTION:                       # комментарий к решению «вопрос»
        return question_comment(con, tg_id, draft, value)

    if step == S_CONSENT:
        send(tg_id, "Сначала подтвердите согласие кнопкой ниже.", kind="команда", con=con)
        return {"action": "ждём согласия"}
    if not value:
        send(tg_id, "Пустой ответ — напишите, пожалуйста, текстом.", kind="команда", con=con)
        return {"action": "пусто"}

    if step == S_NAME:
        draft["full_name"] = value[:120]
        _ask(con, tg_id, S_BRANCH, draft, "Филиал (например: Ташкентский городской):")
        return {"action": "шаг", "step": S_BRANCH}
    if step == S_BRANCH:
        draft["branch"] = value[:80]
        _ask(con, tg_id, S_POSITION, draft, "Должность:")
        return {"action": "шаг", "step": S_POSITION}
    if step == S_POSITION:
        draft["position"] = value[:80]
        _ask(con, tg_id, S_ROLE, draft, "Выберите роль:",
             kb([[(title, f"role:{code}")] for code, title in ROLE_BUTTONS]))
        return {"action": "шаг", "step": S_ROLE}
    if step == S_ROLE:
        send(tg_id, "Выберите роль кнопкой ниже.", kind="команда", con=con)
        return {"action": "ждём роль"}
    if step == S_AGENT:
        found = db.rows(con, "SELECT id, status FROM agents WHERE eais_id=?", value)
        if not found:
            send(tg_id, "Такого ID агента нет в реестре компании. Проверьте и отправьте ещё раз "
                        "(ID выдаётся при включении в реестр, Положение № 3845).", kind="команда", con=con)
            return {"action": "ID не найден"}
        draft["agent_eais_id"] = value
        return finish_registration(con, tg_id, draft)
    send(tg_id, "Не понял. " + HELP, kind="команда", con=con)
    return {"action": "не понял"}


def finish_registration(con, tg_id, draft: dict) -> dict:
    """Создаёт заявку в users со статусом «ожидает подтверждения» и записывает согласие на ПД."""
    login = f"tg{tg_id}"
    if db.rows(con, "SELECT 1 FROM users WHERE login=?", login):
        drop_dialog(con, tg_id)
        send(tg_id, WAIT_MSG, kind="команда", con=con)
        return {"action": "уже подана"}
    role = draft.get("role") or "агент"
    pw_hash, salt = auth.hash_password(hashlib.sha256((login + db.now()).encode()).hexdigest())
    cur = con.execute("INSERT INTO users (login, full_name, phone, role, branch, position, agent_eais_id,"
                      " password_hash, salt, status, telegram_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (login, draft.get("full_name") or f"Пользователь {tg_id}", "", role,
                       draft.get("branch") or "", draft.get("position") or "",
                       draft.get("agent_eais_id") or None, pw_hash, salt, auth.STATUS_PENDING,
                       str(tg_id), db.now()))
    uid = cur.lastrowid
    con.execute("UPDATE pd_consents SET user_id=? WHERE telegram_id=? AND user_id IS NULL", (uid, str(tg_id)))
    drop_dialog(con, tg_id)
    # в журнал — без ФИО и должности: только роль и идентификаторы (правило проекта № 8)
    db.audit(con, login, "заявка на доступ из бота", f"user:{uid}",
             {"telegram_id": str(tg_id), "role": role, "согласие": draft.get("consent_version")})
    log(con, "in", "start", tg_id, uid, True, None)
    send(tg_id, WAIT_MSG, kind="команда", con=con, user_id=uid)
    notify_admins_new_user(con, uid, role)
    return {"action": "заявка создана", "user_id": uid, "role": role}


def notify_admins_new_user(con, uid: int, role: str):
    text = (f"Новая заявка на доступ № {uid}.\nРоль по заявке: {role}.\n"
            "Проверьте человека по реестру и решите:")
    keyboard = kb([[("Подтвердить как агент", f"uapprove:{uid}:агент")],
                   [("Подтвердить как андеррайтер", f"uapprove:{uid}:андеррайтер")],
                   [("Отклонить", f"ureject:{uid}:-")]])
    for a in admins(con):
        send(a["telegram_id"], text, keyboard, kind="уведомление", con=con, user_id=a["id"])


# --------------------------------------------------------------------------- #
#  Нажатия кнопок
# --------------------------------------------------------------------------- #

def question_comment(con, tg_id, draft: dict, comment: str) -> dict:
    u = user_by_tg(con, tg_id)
    rid = draft.get("request_id")
    drop_dialog(con, tg_id)
    if not u or not rid:
        send(tg_id, "Запрос не найден — начните заново.", kind="отказ", con=con)
        return {"action": "отказ"}
    return apply_decision(con, u, rid, "вопрос", comment, tg_id)


def apply_decision(con, u: dict, request_id: int, decision: str, comment: Optional[str], tg_id,
                   chat_id=None, message_id=None) -> dict:
    """Решение пишется существующей approvals.decide от имени пользователя по его telegram_id."""
    try:
        res = approvals.decide(con, request_id, u["login"], decision, comment)
    except HTTPException as e:
        log(con, "in", "отказ", tg_id, u["id"], False, "decide", request_id=request_id)
        send(tg_id, e.detail, kind="отказ", con=con, user_id=u["id"], request_id=request_id)
        return {"action": "отказ", "reason": e.detail}
    log(con, "in", "callback", tg_id, u["id"], True, None, request_id=request_id)
    if chat_id and message_id:
        edit_card(con, chat_id, message_id,
                  request_card(con, request_id) + f"\n\nВаше решение: {decision}"
                  + (f"\nКомментарий: {comment}" if comment else "")
                  + f"\nИтог по запросу: {res['approval_status']}")
    else:
        send(tg_id, f"Решение «{decision}» записано. Итог по запросу: {res['approval_status']}",
             kind="уведомление", con=con, user_id=u["id"], request_id=request_id)
    # уведомления об итоге рассылает сам approvals.decide через мягкий хук on_decided — здесь не дублируем
    return {"action": "решение", "decision": decision, "approval_status": res["approval_status"]}


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

    if action == "consent":
        d = dialog(con, tg_id)
        if parts[1:2] == ["ok"]:
            draft = (d or {}).get("draft") or {}
            shown = consent_text()
            version = draft.get("consent_version") or shown["version"]
            # хэш именно того текста, который человек видел (раздел 7 документа юриста)
            text_hash = draft.get("consent_hash") or shown["hash"]
            con.execute("INSERT INTO pd_consents (user_id, telegram_id, version, channel, created_at,"
                        " consent_text_hash, scope) VALUES (?,?,?,?,?,?,?)",
                        (None, str(tg_id), version, "telegram", db.now(), text_hash, CONSENT_MAIN))
            draft["consent_version"], draft["consent_hash"] = version, text_hash
            answer_callback(cq_id, "Согласие записано")
            _ask(con, tg_id, S_NAME, draft, "Фамилия, имя и отчество полностью:")
            return {"action": "согласие", "version": version}
        drop_dialog(con, tg_id)
        answer_callback(cq_id)
        send(tg_id, "Без согласия доступ к системе невозможен.", kind="отказ", con=con)
        return {"action": "отказ от согласия"}

    if action == "role":
        d = dialog(con, tg_id)
        if not d or d["step"] != S_ROLE:
            answer_callback(cq_id, "Начните заново: /start")
            return {"action": "нет диалога"}
        role = parts[1] if len(parts) > 1 else "агент"
        if role not in auth.ROLES:
            answer_callback(cq_id, "Такой роли нет")
            return {"action": "плохая роль"}
        draft = d["draft"]
        draft["role"] = role
        answer_callback(cq_id)
        if role == "агент":
            _ask(con, tg_id, S_AGENT, draft, "Ваш ID агента из реестра компании (ЕАИС):")
            return {"action": "шаг", "step": S_AGENT}
        return finish_registration(con, tg_id, draft)

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
        u = user_by_tg(con, tg_id)
        rid = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        row_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        if not u or u["status"] != auth.STATUS_ACTIVE:
            log(con, "in", "отказ", tg_id, (u or {}).get("id"), False, "not active", update_id, rid)
            answer_callback(cq_id, "Вы не вошли в систему")
            return {"action": "отказ", "reason": "не активен"}
        # строка согласующего обязана принадлежать нажавшему: подменой callback_data чужое решение не записать
        row = db.rows(con, "SELECT * FROM request_reviewers WHERE id=? AND request_id=?", row_id, rid)
        mine = db.rows(con, "SELECT * FROM request_reviewers WHERE request_id=? AND user_id=?", rid, u["id"])
        if not mine or (row and row[0]["user_id"] != u["id"]):
            log(con, "in", "отказ", tg_id, u["id"], False, "not reviewer", update_id, rid)
            answer_callback(cq_id, "Вы не назначены согласующим по этому запросу")
            send(tg_id, "Вы не назначены согласующим по этому запросу.", kind="отказ", con=con,
                 user_id=u["id"], request_id=rid)
            return {"action": "отказ", "reason": "не назначен согласующим"}
        if action == "ask":
            set_dialog(con, tg_id, S_QUESTION, {"request_id": rid, "reviewer_row_id": mine[0]["id"]})
            answer_callback(cq_id, "Напишите вопрос сообщением")
            send(tg_id, "Напишите ваш вопрос одним сообщением — он уйдёт вместе с решением «вопрос».",
                 kind="команда", con=con, user_id=u["id"], request_id=rid)
            return {"action": "ждём вопрос", "request_id": rid}
        answer_callback(cq_id, "Принято")
        return apply_decision(con, u, rid, "одобрил" if action == "approve" else "отклонил",
                              None, tg_id, chat_id, message_id)

    answer_callback(cq_id)
    log(con, "in", "callback", tg_id, None, False, "unknown action", update_id)
    return {"action": "неизвестная кнопка"}


# --------------------------------------------------------------------------- #
#  Разбор обновления
# --------------------------------------------------------------------------- #

def handle_message(con, msg: dict, update_id=None) -> dict:
    tg_id = str(((msg.get("from") or {}).get("id")) or ((msg.get("chat") or {}).get("id")) or "")
    if not tg_id:
        return {"action": "без отправителя"}
    text = (msg.get("text") or "").strip()
    u = user_by_tg(con, tg_id)
    if text.startswith("/"):
        cmd, _, rest = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        log(con, "in", "start" if cmd == "/start" else "команда", tg_id, (u or {}).get("id"), True, None, update_id)
        if cmd == "/start":
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
    log(con, "in", "команда", tg_id, (u or {}).get("id"), True, None, update_id)
    return dialog_text(con, tg_id, text)


def handle_update(con, update: dict) -> dict:
    """Разбор одного обновления. Идемпотентность проверяется снаружи (seen_update)."""
    update_id = update.get("update_id")
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
        except Exception as e:
            _poll_state["last_error"] = str(e)[:200]
            time.sleep(10)


def start_polling():
    """Вызывается из app/main.py на старте. Без токена или без флага поток не создаётся вовсе."""
    if not connected() or not polling_enabled() or _poll_state["running"]:
        return False
    threading.Thread(target=poll_loop, daemon=True, name="tg-polling").start()
    return True


# --------------------------------------------------------------------------- #
#  Точки для мини-приложения
# --------------------------------------------------------------------------- #

NAV_BASE = [("calc", "Расчёт"), ("my-requests", "Мои запросы"), ("photos", "Фото")]
NAV_REVIEWER = [("inbox", "Ждут меня")]
NAV_ADMIN = [("applications", "Заявки"), ("users", "Пользователи"),
             ("agreements", "Генеральные соглашения"), ("settings", "Настройки")]


def _session_user(request: Request) -> Optional[dict]:
    with db.tx() as con:
        return auth.session_user(con, auth.request_token(request))


def _mode() -> str:
    from .telegram import MODE_BROWSER, MODE_TG
    return MODE_TG if connected() else MODE_BROWSER


@router.get("/tg/me")
def tg_me(request: Request):
    """Кто вошёл, что ему можно и какие разделы показывать. Работает и по cookie-сессии."""
    u = _session_user(request)
    if not u:
        return {"mode": _mode(), "status": "не вошёл", "user": None, "rights": [], "nav": [],
                "reason": "Войдите через Telegram или по логину и паролю"}
    if u["status"] != auth.STATUS_ACTIVE:
        return {"mode": _mode(), "status": u["status"], "user": None, "rights": [], "nav": [],
                "reason": WAIT_MSG}
    nav = list(NAV_BASE)
    if u["role"] in ("андеррайтер", "актуарий", "админ"):
        nav += NAV_REVIEWER
    if u["role"] == "админ":
        nav += NAV_ADMIN
    return {"mode": _mode(), "status": auth.STATUS_ACTIVE,
            "user": {"id": u["id"], "full_name": u["full_name"], "role": u["role"],
                     "branch": u.get("branch"), "status": u["status"]},
            "rights": sorted(r for r in auth.PERMISSIONS if auth.can(u, r)),
            "nav": [{"key": k, "title": t} for k, t in nav], "reason": ""}


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
