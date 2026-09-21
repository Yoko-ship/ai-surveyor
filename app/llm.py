"""
Единый клиент к ИИ (OpenAI-совместимый chat endpoint) — только стандартная библиотека.

Главное правило: персональные данные граждан РУз не покидают Узбекистан (правило проекта № 8).
Поэтому ЛЮБОЙ текст, уходящий во внешнюю модель, проходит через mask_pd(): ФИО, ПИНФЛ, паспорт,
ИНН, телефон, e-mail, госномер, номер карты и кадастровый номер заменяются на плейсхолдеры.
Обойти маскировку нельзя: наружу открыт только chat(), внутри которого она вызывается.

Настройки берутся так: таблица app_settings (приоритет) → переменная окружения → файлы .env и .secrets.env → значение по умолчанию.
Ключи наружу отдаются маской вида "sk-...abcd" — целиком их не показывает ни один ответ API.

Если ключа нет, status() честно говорит «ИИ не подключён», а все вызовы возвращают None:
везде, где ИИ используется, обязателен запасной вариант без него.

Провайдеры (OpenAI-совместимый /chat/completions):
  kimi      — https://api.moonshot.ai/v1, модель по умолчанию kimi-k3
              (проверено 20.09.2026 на platform.moonshot.ai/docs/api/chat — в примере официальной
               документации стоит "model": "kimi-k3"; в прайс-листе также kimi-k2.6 и kimi-k2.7-code)
  openai    — https://api.openai.com/v1
  anthropic — другой формат запроса, здесь НЕ поддержан: выбор провайдера 'anthropic' работает
              как 'none' (честная ошибка), чтобы не притворяться рабочим.
  none      — ИИ выключен.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent
# файлы с настройками: .env (основной) и .secrets.env (ключи, в репозиторий не попадает);
# .secrets.env дополняет .env, но не затирает уже найденные там значения
ENV_FILES = (ROOT / ".env", ROOT / ".secrets.env")

TIMEOUT_SEC = 40
RETRIES = 1                       # одна повторная попытка
MAX_PROMPT_CHARS = 12000          # длинные документы режем: и дешевле, и меньше риска

SETTING_KEYS = ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY",
                "TELEGRAM_BOT_TOKEN", "PD_MODE", "SERVER_URL",
                # бот Telegram (app/tgbot.py): секрет вебхука, код первого администратора,
                # запасной режим опроса и версия текста согласия на обработку ПД
                "TG_WEBHOOK_SECRET", "ADMIN_BOOTSTRAP_CODE", "ADMIN_BOOTSTRAP_USED",
                "TG_ADMIN_USERNAME", "TG_OWNER_CHAT_ID",   # username Telegram первого администратора: входит админом без кода
                "TG_POLLING", "CONSENT_VERSION",
                # вход через Google (app/google_auth.py): приложение в Google Cloud Console
                # и необязательное ограничение по доменам почты, через запятую
                "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_ALLOWED_DOMAINS")

PROVIDERS = {
    "kimi":      {"base_url": "https://api.moonshot.ai/v1", "model": "kimi-k3",       "name": "Kimi (Moonshot)"},
    "openai":    {"base_url": "https://api.openai.com/v1",  "model": "gpt-4o-mini",   "name": "OpenAI"},
    "anthropic": {"base_url": "https://api.anthropic.com",  "model": "claude-sonnet", "name": "Anthropic (не подключён)"},
    "none":      {"base_url": "",                           "model": "",              "name": "ИИ выключен"},
}

DEFAULTS = {"LLM_PROVIDER": "none", "PD_MODE": "test", "SERVER_URL": "http://127.0.0.1:8000",
            "LLM_BASE_URL": "", "LLM_MODEL": "", "LLM_API_KEY": "", "TELEGRAM_BOT_TOKEN": "",
            "TG_WEBHOOK_SECRET": "", "ADMIN_BOOTSTRAP_CODE": "", "ADMIN_BOOTSTRAP_USED": "",
            "TG_POLLING": "0", "CONSENT_VERSION": "черновик-1",
            "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "", "GOOGLE_ALLOWED_DOMAINS": ""}

NOT_CONNECTED = "ИИ не подключён: не задан ключ API"

last_error = {"text": None}       # текст последней ошибки — показываем в админке


# --------------------------------------------------------------------------- #
#  Настройки
# --------------------------------------------------------------------------- #

def _env_file() -> dict:
    """Чтение .env и .secrets.env (без пакета python-dotenv): KEY=value, строки с # пропускаем.
    Первый файл главнее: значения из .secrets.env только дополняют.
    encoding utf-8-sig — иначе первый ключ файла с BOM читается как "﻿KEY"."""
    out = {}
    for path in ENV_FILES:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in out:
                out[k] = v
    return out


def _db_settings() -> dict:
    try:
        with db.tx() as con:
            return {r["key"]: r["value"] for r in db.rows(con, "SELECT key, value FROM app_settings")}
    except Exception:          # таблицы ещё нет (первый запуск до ensure_schema)
        return {}


def get(key: str, default: str = None) -> str:
    """Значение настройки: база → окружение → .env и .secrets.env → значение по умолчанию."""
    v = _db_settings().get(key)
    if v is None or v == "":
        v = os.environ.get(key) or _env_file().get(key)
    if v is None or v == "":
        v = DEFAULTS.get(key, "") if default is None else default
    return v


def set_many(values: dict, who: str = "админ") -> list:
    """Сохраняет настройки. Пустая строка = не менять (так UI не стирает ключ пустым полем)."""
    changed = []
    with db.tx() as con:
        for k, v in values.items():
            if k not in SETTING_KEYS or v is None or str(v).strip() == "":
                continue
            v = str(v).strip()
            con.execute("DELETE FROM app_settings WHERE key=?", (k,))
            con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?)", (k, v, db.now()))
            changed.append(k)
        if changed:
            # в журнал пишем только названия изменённых настроек, значения (ключи!) — никогда
            db.audit(con, who, "изменены настройки", "app_settings", {"keys": changed})
    return changed


def mask_key(value: str) -> str:
    """Маска ключа для показа наружу: sk-...abcd."""
    if not value:
        return ""
    v = str(value)
    head = v[:3] if len(v) > 10 else ""
    return f"{head}...{v[-4:]}" if len(v) > 8 else "..."


def provider() -> str:
    p = (get("LLM_PROVIDER") or "none").strip().lower()
    return p if p in PROVIDERS else "none"


def base_url() -> str:
    return (get("LLM_BASE_URL") or PROVIDERS[provider()]["base_url"]).rstrip("/")


def model() -> str:
    return get("LLM_MODEL") or PROVIDERS[provider()]["model"]


def api_key() -> str:
    return get("LLM_API_KEY") or ""


def enabled() -> bool:
    return provider() in ("kimi", "openai") and bool(api_key()) and bool(base_url())


def status() -> dict:
    """Честный ответ о состоянии ИИ. Никогда не показывает ключ целиком."""
    p = provider()
    if p == "none":
        reason = "ИИ выключен в настройках (провайдер «none»)"
    elif p == "anthropic":
        reason = "Провайдер Anthropic в этой версии не поддержан — выберите kimi или openai"
    elif not api_key():
        reason = NOT_CONNECTED
    else:
        reason = "Ключ задан. Нажмите «Проверить», чтобы убедиться в связи"
    return {"provider": p, "provider_name": PROVIDERS[p]["name"], "model": model(),
            "base_url": base_url(), "key_mask": mask_key(api_key()),
            "connected": enabled(), "reason": reason,
            "pd_mode": get("PD_MODE"),
            "note": "Персональные данные маскируются до отправки: ФИО, ПИНФЛ, паспорт, ИНН, телефон, e-mail"}


# --------------------------------------------------------------------------- #
#  Маскировка персональных данных
# --------------------------------------------------------------------------- #

# слова с заглавной буквы, которые ФИО не являются: иначе «Республика Узбекистан» станет [ФИО]
NAME_STOP = {
    "республика", "узбекистан", "ташкент", "ташкентская", "область", "город", "район", "махалля",
    "закон", "положение", "постановление", "кабинет", "министров", "министерство", "агентство",
    "страхование", "страховая", "страховщик", "компания", "общество", "акционерное", "полис",
    "договор", "приложение", "таблица", "сумма", "премия", "ставка", "объект", "здание", "квартира",
    "автомобиль", "марка", "модель", "янв", "инсон", "кадастр", "техпаспорт", "паспорт", "выписка",
    "государственный", "реестр", "недвижимости", "имущества", "кодекс", "гражданский", "статья",
}
LAT_STOP = {"inson", "insurance", "uzbekistan", "tashkent", "republic", "company", "policy",
            "model", "object", "total", "sum", "the", "and", "for", "llc", "jsc"}

_CYR_WORD = r"[А-ЯЁ][а-яё]{1,}"
_LAT_WORD = r"[A-Z][a-z]{1,}"

# Порядок важен: сначала длинные/структурные шаблоны, потом короткие цифровые.
PD_RULES = [
    # e-mail
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[E-MAIL]"),
    # кадастровый номер: 10:09:05:01:0123:0001
    (re.compile(r"\b\d{1,4}(?::\d{1,5}){3,6}\b"), "[КАДАСТР]"),
    # номер карты: 16 цифр группами
    (re.compile(r"(?<!\d)\d{4}[ \-]?\d{4}[ \-]?\d{4}[ \-]?\d{4}(?!\d)"), "[НОМЕР КАРТЫ]"),
    # ПИНФЛ — 14 цифр
    (re.compile(r"(?<!\d)\d{14}(?!\d)"), "[ПИНФЛ]"),
    # серия и номер паспорта: AA 1234567 / АА1234567
    (re.compile(r"\b[A-ZА-ЯЁ]{2}\s?[\-№]?\s?\d{7}\b"), "[ПАСПОРТ]"),
    # госномер ТС Узбекистана: 01 A 123 AA и 01 123 AAA
    (re.compile(r"\b\d{2}\s?[A-ZА-Я]\s?\d{3}\s?[A-ZА-Я]{2}\b"), "[ГОСНОМЕР]"),
    (re.compile(r"\b\d{2}\s?\d{3}\s?[A-ZА-Я]{3}\b"), "[ГОСНОМЕР]"),
    # телефон: +998 90 123-45-67 и варианты с разделителями
    (re.compile(r"(?<![\d\-:])(?:\+?998|8)[\s\-()]*\d{2}[\s\-()]*\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"), "[ТЕЛЕФОН]"),
    (re.compile(r"(?<![\d\-:])\d{2}[\s\-]\d{3}[\s\-]\d{2}[\s\-]\d{2}(?!\d)"), "[ТЕЛЕФОН]"),
    # ИНН юр./физ. лица — 9 цифр
    (re.compile(r"(?<![\d\-:])\d{9}(?!\d)"), "[ИНН]"),
]

# ФИО: 2–4 слова с заглавной подряд (кириллица или латиница) либо «Фамилия И. О.»
# _SP — пробел внутри имени: перевод строки им не считается, иначе маска перескакивает
# на следующую строку и съедает её подпись («Домашний адрес», «Адрес объекта»)
_SP = r"[^\S\r\n]+"
_SP0 = r"[^\S\r\n]*"                 # необязательный пробел — тоже без перевода строки
NAME_INITIALS = re.compile(rf"\b{_CYR_WORD}{_SP}[А-ЯЁ]\.{_SP0}[А-ЯЁ]\.|\b[А-ЯЁ]\.{_SP0}[А-ЯЁ]\.{_SP0}{_CYR_WORD}")
NAME_CYR = re.compile(rf"\b{_CYR_WORD}(?:{_SP}{_CYR_WORD}){{1,3}}\b")
NAME_LAT = re.compile(rf"\b{_LAT_WORD}(?:{_SP}{_LAT_WORD}){{1,3}}\b")


def _mask_names(text: str) -> str:
    def repl(m, stop):
        words = m.group(0).split()
        if any(w.lower().strip(".,") in stop for w in words):
            return m.group(0)
        return "[ФИО]"
    text = NAME_INITIALS.sub("[ФИО]", text)
    text = NAME_CYR.sub(lambda m: repl(m, NAME_STOP), text)
    text = NAME_LAT.sub(lambda m: repl(m, LAT_STOP), text)
    return text


def mask_names(text: str) -> str:
    """Только правило ФИО, без остальных правил PD_RULES — публичное имя для app/ingest.py."""
    return _mask_names(text or "")


def mask_name_initials(text: str) -> str:
    """Только «Фамилия И. О.»: в характеристиках объекта такое написание — точно человек."""
    return NAME_INITIALS.sub("[ФИО]", text or "")


def mask_pd(text) -> str:
    """
    Заменяет персональные данные на плейсхолдеры. Лучше замаскировать лишнее, чем выпустить
    данные человека за пределы страны (правило проекта № 8).
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    out = text
    for rx, placeholder in PD_RULES:
        out = rx.sub(placeholder, out)
    return _mask_names(out)


def has_pd(text: str) -> bool:
    """Проверка «в тексте ещё осталось похожее на ПД» — для тестов и самоконтроля."""
    return mask_pd(text) != (text or "")


# --------------------------------------------------------------------------- #
#  Вызов модели
# --------------------------------------------------------------------------- #

def _log_call(purpose: str, ms: int, ok: bool, usage: dict = None, error: str = None):
    usage = usage or {}
    try:
        with db.tx() as con:
            con.execute("""INSERT INTO llm_calls (ts, purpose, provider, model, prompt_tokens,
                           completion_tokens, total_tokens, ms, ok, error) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (db.now(), purpose, provider(), model(), usage.get("prompt_tokens"),
                         usage.get("completion_tokens"), usage.get("total_tokens"),
                         int(ms), 1 if ok else 0, error))
    except Exception as e:          # журнал не должен ронять основную работу
        print("журнал вызовов ИИ:", e)


def _friendly(err: Exception) -> str:
    """Понятные сообщения по-русски вместо трассировок."""
    if isinstance(err, urllib.error.HTTPError):
        code = err.code
        if code in (401, 403):
            return "Ключ API не принят: неверный, отозван или нет прав. Проверьте ключ в настройках"
        if code == 404:
            return "Адрес сервиса или название модели неверны — проверьте LLM_BASE_URL и LLM_MODEL"
        if code == 429:
            return "Превышен лимит запросов или закончились средства на счёте у поставщика ИИ"
        if code >= 500:
            return f"Сервис ИИ временно недоступен (ошибка {code}) — попробуйте позже"
        return f"Сервис ИИ отклонил запрос (ошибка {code})"
    if isinstance(err, urllib.error.URLError):
        return "Нет связи с сервисом ИИ: проверьте интернет на сервере и адрес LLM_BASE_URL"
    if isinstance(err, TimeoutError):
        return "Сервис ИИ не ответил вовремя"
    return f"Ошибка обращения к ИИ: {type(err).__name__}"


def _request(messages: list, max_tokens: int, temperature: float) -> dict:
    body = json.dumps({"model": model(), "messages": messages,
                       "max_tokens": max_tokens, "temperature": temperature}).encode("utf-8")
    req = urllib.request.Request(
        base_url() + "/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key(),
                 "User-Agent": "INSON-surveyor/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def chat(purpose: str, system: str, user: str, max_tokens: int = 700,
         temperature: float = 0.2) -> Optional[str]:
    """
    Единственная точка обращения к модели. Маскирует персональные данные, логирует метрики.
    Возвращает текст ответа или None — вызывающий код обязан уметь работать без ИИ.
    """
    last_error["text"] = None
    if not enabled():
        last_error["text"] = status()["reason"]
        _log_call(purpose, 0, False, error=last_error["text"])
        return None
    messages = [{"role": "system", "content": mask_pd(system)},
                {"role": "user", "content": mask_pd(user)[:MAX_PROMPT_CHARS]}]
    last_err = None
    for attempt in range(RETRIES + 1):
        t0 = time.time()
        try:
            data = _request(messages, max_tokens, temperature)
            ms = (time.time() - t0) * 1000
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            _log_call(purpose, ms, bool(text), data.get("usage") or {},
                      None if text else "пустой ответ модели")
            return text
        except Exception as e:
            last_err = e
            _log_call(purpose, (time.time() - t0) * 1000, False, error=_friendly(e))
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403, 404):
                break                    # повтор не поможет
            time.sleep(1)
    last_error["text"] = _friendly(last_err) if last_err else None
    return None


def ping() -> dict:
    """Короткий запрос «скажи ОК» — проверка связи и ключа."""
    if not enabled():
        return {"ok": False, "reason": status()["reason"], "provider": provider(), "model": model()}
    t0 = time.time()
    answer = chat("проверка связи", "Отвечай одним словом.", "Ответь словом: ОК", max_tokens=10)
    ms = int((time.time() - t0) * 1000)
    if answer:
        return {"ok": True, "reason": "Связь есть", "answer": answer.strip()[:40],
                "provider": provider(), "model": model(), "ms": ms}
    return {"ok": False, "reason": last_error["text"] or "Ответ не получен",
            "provider": provider(), "model": model(), "ms": ms}


def calls(limit: int = 50) -> list:
    with db.tx() as con:
        return db.rows(con, "SELECT * FROM llm_calls ORDER BY id DESC LIMIT ?", int(limit))


# --------------------------------------------------------------------------- #
#  (а) Объяснение расчёта человеческим языком
# --------------------------------------------------------------------------- #

SYSTEM_EXPLAIN = (
    "Ты объясняешь клиенту страховой компании, из чего сложилась ставка по его объекту. "
    "Пиши по-русски, просто, без жаргона, 5–8 коротких предложений. Не придумывай цифры: "
    "используй только те, что даны. Не упоминай людей и документы, которых нет в данных.")


def _explain_data(card: dict) -> dict:
    """Что отдаём в подсказку: только объект и цифры, ничего о человеке."""
    calc = card.get("calculation") or {}
    obj = card.get("object") or {}
    req = card.get("request") or {}
    try:
        expl = json.loads(calc.get("explanation") or "{}")
    except Exception:
        expl = {}
    chain = expl.get("chain") if isinstance(expl, dict) else expl
    try:
        attrs = json.loads(obj.get("attributes") or "{}")
    except Exception:
        attrs = {}
    term = None
    if attrs.get("term_from") and attrs.get("term_to"):
        term = f"с {attrs['term_from']} по {attrs['term_to']}"
    return {
        "продукт": req.get("product_code"),
        "класс": (attrs.get("factors") or {}).get("class_code") or attrs.get("class_code"),
        "тип_объекта": obj.get("object_type"),
        "страховая_сумма": obj.get("sum_insured"),
        "страховая_стоимость": obj.get("value_amount"),
        "франшиза": obj.get("franchise"),
        "срок": term,
        "ставка_техническая_проц": calc.get("gross_rate_pct"),
        "ставка_минимальная_проц": calc.get("min_rate_pct"),
        "ставка_применённая_проц": calc.get("applied_rate_pct"),
        "премия": calc.get("premium"),
        "вердикт": calc.get("verdict"),
        "из_чего_сложилась": chain if isinstance(chain, list) else [],
        "проверки": [{"правило": c.get("rule_code"), "статус": c.get("status"), "пояснение": c.get("detail")}
                     for c in (card.get("checks") or [])],
        "что_можно_изменить": [{"вид": r.get("kind"), "текст": r.get("text"),
                                "изменение_премии": r.get("premium_delta")}
                               for r in (card.get("recommendations") or [])],
    }


def explain_template(data: dict) -> str:
    """Объяснение без ИИ. Работает всегда — это основной вариант, ИИ лишь причёсывает текст."""
    p = []
    money = lambda v: f"{round(v):,}".replace(",", " ") if isinstance(v, (int, float)) else "—"
    pct = lambda v: f"{v:.3f}%".replace(".", ",") if isinstance(v, (int, float)) else "—"
    cls = f", класс страхования {data['класс']}" if data.get("класс") else ""
    p.append(f"Объект: {data.get('тип_объекта') or 'не указан'}{cls}, "
             f"страховая сумма {money(data.get('страховая_сумма'))} сум.")
    p.append(f"Расчётная (техническая) ставка — {pct(data.get('ставка_техническая_проц'))}, "
             f"применённая — {pct(data.get('ставка_применённая_проц'))}, "
             f"премия — {money(data.get('премия'))} сум"
             + (f" за период {data.get('срок')}." if data.get("срок") else "."))
    if data.get("ставка_минимальная_проц"):
        p.append(f"Ниже {pct(data.get('ставка_минимальная_проц'))} опускаться нельзя: это минимум "
                 f"тарифной политики компании или регулятора.")
    chain = data.get("из_чего_сложилась") or []
    if chain:
        parts = []
        for step in chain:
            if "value_pct" in step:
                parts.append(f"{step['name']} — {pct(step['value_pct'])}")
            elif "mult" in step and step.get("mult") not in (1, 1.0, None):
                parts.append(f"{step['name']} — коэффициент {step['mult']}")
            elif "add_pct" in step and step["add_pct"]:
                parts.append(f"{step['name']} — плюс {pct(step['add_pct'])}")
            elif "divide_by" in step:
                parts.append(f"{step['name']}")
        if parts:
            p.append("Из чего сложилась ставка: " + "; ".join(parts) + ".")
    bad = [c for c in (data.get("проверки") or []) if c.get("статус") in ("stop", "warn", "нарушено")]
    if bad:
        p.append("Что требует внимания: " + "; ".join(str(c.get("пояснение") or c.get("правило")) for c in bad) + ".")
    recs = data.get("что_можно_изменить") or []
    if recs:
        p.append("Как можно уменьшить премию: " + "; ".join(str(r.get("текст")) for r in recs) + ".")
    p.append("Вердикт системы: " + str(data.get("вердикт") or "не рассчитан") + ".")
    return " ".join(p)


def explain_calculation(card: dict, class_code: str = None) -> dict:
    """Объяснение расчёта: шаблон всегда, ИИ — если подключён."""
    data = _explain_data(card)
    data["класс"] = data.get("класс") or class_code
    base = explain_template(data)
    text, source = base, "шаблон"
    if enabled():
        answer = chat("объяснение расчёта", SYSTEM_EXPLAIN,
                      "Данные расчёта (JSON):\n" + json.dumps(data, ensure_ascii=False) +
                      "\n\nЧерновик объяснения:\n" + base +
                      "\n\nПерепиши черновик понятным языком для клиента, сохранив все цифры.")
        if answer and answer.strip():
            text, source = answer.strip(), "ИИ"
    return {"text": text, "source": source, "template": base,
            "ai": status() | {"error": last_error["text"] if source == "шаблон" else None}}


SYSTEM_RISK = (
    "Ты андеррайтер страховой компании в Узбекистане. По результатам анализа риска объекта "
    "напиши связный разбор из 5–7 предложений по-русски: уровень риска и что его определяет, "
    "страховая сумма к стоимости, ставка к минимуму и рынку, главные сценарии убытка, каких данных "
    "и документов не хватает. Используй только цифры и факты из данных, ничего не добавляй. "
    "Без списков и заголовков, без упоминания людей.")
AI_OFF = "ИИ не подключён — анализ выполнен по правилам и справочникам"


def risk_summary_data(res: dict) -> dict:
    """Что отдаём модели из ответа /analytics/risk: только объект и цифры, ничего о человеке."""
    s = res.get("summary") or {}
    lvl = res.get("level") or {}
    docs = res.get("documents") or {}
    comp = res.get("completeness") or {}
    return {
        "класс": s.get("class_code"), "продукт": s.get("product_code"), "тип_объекта": s.get("object_type"),
        "регион": s.get("region"), "страховая_сумма": s.get("sum_insured"),
        "стоимость": s.get("object_value"), "сумма_к_стоимости": s.get("ratio_sum_to_value"),
        "ставка_применённая_проц": s.get("rate_applied_pct"), "ставка_минимальная_проц": s.get("rate_min_pct"),
        "премия": s.get("premium"),
        "уровень_риска": lvl.get("level"), "балл": lvl.get("score"),
        "составляющие": [{"что": c.get("name"), "баллы": c.get("points"), "почему": c.get("why")}
                         for c in (lvl.get("components") or []) if c.get("applicable")],
        "главные_факторы": res.get("top_drivers") or [],
        "сценарии": {k: {"что": v.get("title"), "убыток": v.get("amount"), "доля_суммы_проц": v.get("pct_of_sum"),
                         "главная_причина": v.get("dominant"), "уровень": v.get("level")}
                     for k, v in (res.get("scenarios") or {}).items() if isinstance(v, dict)},
        "риски": [{"риск": r.get("name"), "доля_проц": r.get("share_of_net_pct")}
                  for r in (res.get("risks") or []) if isinstance(r, dict)],
        "полнота_данных_проц": comp.get("pct"), "уверенность": comp.get("confidence"),
        "что_добавить": [w.get("key") for w in comp.get("what_to_add") or []],
        "не_хватает_документов": docs.get("missing") or [],
        "рынок": (res.get("market") or {}).get("notes") or [],
    }


def risk_summary(res: dict) -> dict:
    """
    Короткий разбор результатов анализа риска. Без ключа — {"text": None, "status": AI_OFF}:
    шаблонного текста здесь нет, ответ анализа и так содержит все цифры.
    """
    if not enabled():
        return {"text": None, "status": AI_OFF, "source": None}
    data = risk_summary_data(res)
    answer = chat("разбор анализа риска", SYSTEM_RISK,
                  "Результаты анализа (JSON):\n" + json.dumps(data, ensure_ascii=False, default=str),
                  max_tokens=600)
    if answer and answer.strip():
        return {"text": answer.strip(), "status": "Разбор подготовил ИИ по результатам анализа — цифры "
                                                  "сверяйте с блоками ниже", "source": "ИИ"}
    return {"text": None, "status": "ИИ не ответил (" + str(last_error["text"] or "пустой ответ") +
                                     ") — анализ выполнен по правилам и справочникам", "source": None}


# --------------------------------------------------------------------------- #
#  (б) Помощь разбору документов — только по полям, которые не нашли регэкспы
# --------------------------------------------------------------------------- #

SYSTEM_FIELDS = (
    "Ты извлекаешь значения полей из текста официального документа Узбекистана. "
    "Отвечай ТОЛЬКО объектом JSON вида {\"ключ\": \"значение\"}. Если значения в тексте нет — "
    "не добавляй ключ. Ничего не придумывай. Персональные данные уже заменены на метки "
    "вида [ФИО] — их не восстанавливай.")


def extract_fields(text: str, missing: list, doc_kind: str = "") -> dict:
    """
    Ищет в тексте документа поля, которые не нашлись регэкспами.
    missing — список {"key": ..., "name": ...}. Возвращает {key: value} (может быть пусто).
    Любое значение отсюда — «требует проверки»: источник помечается как «ИИ».
    """
    if not enabled() or not text or not missing:
        return {}
    ask = "\n".join(f"- {m.get('key')}: {m.get('name')}" for m in missing)
    answer = chat("поля документа", SYSTEM_FIELDS,
                  f"Вид документа: {doc_kind or 'не указан'}\nНужные поля:\n{ask}\n\nТекст документа:\n{text}",
                  max_tokens=500)
    if not answer:
        return {}
    m = re.search(r"\{.*\}", answer, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {}
    wanted = {x.get("key") for x in missing}
    return {k: str(v).strip() for k, v in data.items()
            if k in wanted and v not in (None, "", "нет", "не указано")}


# --------------------------------------------------------------------------- #
#  (б2) Разбор документа целиком — для app/ingest.py
# --------------------------------------------------------------------------- #
# Заказчик (21.09.2026): загруженный файл должен переводиться во внутреннее представление,
# одинаковое для русского, узбекского и английского документа. Поэтому ключи ответа всегда
# английские и всегда одни и те же, а промпт берётся по языку документа из app/llm_prompts/.

PROMPTS_DIR = ROOT / "app" / "llm_prompts"

# Файл промпта по языку документа. uz-latn и uz-cyrl — один файл; mixed и неизвестно — ru.
PROMPT_BY_LANG = {"ru": "ru.txt", "uz-latn": "uz.txt", "uz-cyrl": "uz.txt", "uz": "uz.txt",
                  "en": "en.txt", "mixed": "ru.txt"}

# Разрешённые ключи полей. Один и тот же набор для документа на любом языке.
DOC_FIELD_KEYS = (
    # транспорт
    "brand", "model", "year", "vehicle_type", "vin", "body_no", "chassis_no", "engine_no",
    "engine_cc", "seats", "color", "region",
    # недвижимость
    "cadastral_no", "address", "object_kind", "area_m2", "land_area_ha", "right_kind",
    "cadastral_value", "encumbrance", "mortgage", "rooms", "build_year", "floors", "walls",
    # оценка и деньги
    "market_value", "appraised_value", "book_value", "valuation_date", "currency", "amount",
    "sum_insured",
    # договор
    "contract_no", "contract_date", "period_from", "period_to",
    # выписка и организация
    "account_no", "balance_close", "turnover", "inn_org", "org_name",
    # штатное расписание
    "positions_count", "payroll_fund", "headcount",
)

DOC_FACT_KEYS = ("is_policy", "has_mortgage", "has_encumbrance", "is_register_extract",
                 "construction_unfinished")

DOC_SCHEMA = ('{"document_kind": "строка", "language": "ru|uz-latn|uz-cyrl|en", '
              '"fields": {"ключ": "значение"}, "facts": {"ключ": true|false}, '
              '"summary": "одно предложение о документе"}')


def prompt_file(language: str = None) -> Path:
    """Файл промпта по языку документа. Неизвестный язык — русский промпт."""
    return PROMPTS_DIR / PROMPT_BY_LANG.get((language or "").strip().lower(), "ru.txt")


def load_prompt(language: str = None) -> Optional[str]:
    """Читает промпт с диска. Файла нет — None, вызывающий код обязан это пережить."""
    try:
        return prompt_file(language).read_text(encoding="utf-8")
    except Exception as e:
        last_error["text"] = "Промпт не прочитан: %s" % type(e).__name__
        return None


SYSTEM_DOCUMENT = ("Ты извлекаешь данные из официальных документов Узбекистана. "
                   "Отвечаешь только объектом JSON с английскими ключами. "
                   "Ничего не придумываешь. Персональные данные физических лиц не извлекаешь.")


def extract_document(text: str, kind: str = "", language: str = None) -> dict:
    """
    Разбор документа ИИ: один и тот же JSON для русского, узбекского и английского документа.

    Возвращает {"ok", "source", "fields", "facts", "document_kind", "language", "summary",
                "требует_проверки", "reason"}.
    Без ключа ИИ (enabled() == False) честно отдаёт ok=False и пустые поля: вызывающий код
    (app/ingest.py) в этом случае остаётся на регулярках и помечает method = "regex".
    Маскировка персональных данных делается внутри chat() — обойти её нельзя.
    """
    empty = {"ok": False, "source": "нет", "fields": {}, "facts": {}, "document_kind": None,
             "language": language, "summary": None, "требует_проверки": False}
    if not enabled():
        return empty | {"reason": status()["reason"]}
    if not (text or "").strip():
        return empty | {"reason": "пустой текст документа"}
    template = load_prompt(language)
    if not template:
        return empty | {"reason": "нет файла промпта app/llm_prompts/"}
    user = (template
            .replace("{kind}", kind or "не определён")
            .replace("{language}", language or "не определён")
            .replace("{schema}", DOC_SCHEMA)
            .replace("{keys}", ", ".join(DOC_FIELD_KEYS))
            .replace("{text}", text))
    answer = chat("разбор документа", SYSTEM_DOCUMENT, user, max_tokens=900)
    if not answer:
        return empty | {"reason": last_error["text"] or "ответ не получен"}
    m = re.search(r"\{.*\}", answer, re.S)
    if not m:
        return empty | {"reason": "модель ответила не JSON"}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return empty | {"reason": "ответ модели не разобрался как JSON"}
    raw_fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    fields = {k: str(v).strip() for k, v in raw_fields.items()
              if k in DOC_FIELD_KEYS and v not in (None, "", "нет", "не указано", "-")}
    raw_facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
    facts = {k: bool(v) for k, v in raw_facts.items() if k in DOC_FACT_KEYS}
    return {"ok": True, "source": "ИИ", "fields": fields, "facts": facts,
            "document_kind": (data.get("document_kind") or None),
            "language": data.get("language") or language,
            "summary": (str(data.get("summary"))[:500] if data.get("summary") else None),
            "требует_проверки": True,
            "reason": "Значения предложены ИИ — проверьте по оригиналу документа"}


# --------------------------------------------------------------------------- #
#  (в) Разбор новостей и изменений законов — только интерфейс
# --------------------------------------------------------------------------- #

def analyze_news(items: list, purpose: str = "изменения законодательства") -> dict:
    """
    ЗАГОТОВКА (реализация — в другом потоке работ).

    Назначение: по списку новостей и опубликованных актов понять, что меняется для страховщика
    (ставки, обязательные виды, требования к агентам), и предложить темы для разбора.

    Вход:  items — список {"title": ..., "date": ..., "url": ..., "text": ...}.
           Источник нормы — только lex.uz (см. tools/lex_fetch.py); новости — справочно.
    Выход: {"ok": bool, "source": "ИИ"|"нет", "items": [...], "reason": "..."}
           где для каждой новости: {"title", "url", "важность", "что_меняется", "норма"}.

    Сейчас ИИ-разбора нет: функция возвращает список без оценки, чтобы вызывающий код
    (реестр знаний app/knowledge.py) продолжал работать как раньше.
    """
    plain = [{"title": (i.get("title") or "")[:300], "url": i.get("url"), "date": i.get("date"),
              "важность": None, "что_меняется": None, "норма": None} for i in (items or [])]
    return {"ok": False, "source": "нет", "items": plain,
            "reason": "Разбор новостей ИИ ещё не реализован — показан исходный список"}


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #

class MaskIn(BaseModel):
    text: str


@router.get("/llm/status")
def llm_status():
    """Подключён ли ИИ, какой провайдер и модель. Ключ — только маской."""
    return status()


@router.post("/llm/ping")
def llm_ping():
    return ping()


@router.get("/llm/calls")
def llm_calls(limit: int = 50):
    return calls(limit)


@router.post("/llm/mask-preview")
def llm_mask_preview(body: MaskIn):
    """Показать, как выглядит текст после маскировки — чтобы проверить правило № 8 своими глазами."""
    return {"masked": mask_pd(body.text)}


@router.get("/requests/{rid}/explain")
def request_explain(rid: int):
    """Объяснение расчёта для клиента. Без ИИ отдаётся тот же текст, собранный по шаблону."""
    from .main import _card                     # карточка запроса собирается в main.py
    with db.tx() as con:
        card = _card(con, rid)
        cls = db.rows(con, "SELECT class_code FROM product_classes WHERE product_code=? ORDER BY part_no",
                      (card.get("request") or {}).get("product_code"))
    if not card.get("calculation"):
        raise HTTPException(404, "По этому запросу расчёта ещё нет")
    return explain_calculation(card, ", ".join(c["class_code"] for c in cls) or None)
