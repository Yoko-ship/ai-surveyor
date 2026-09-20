"""
Единый клиент к ИИ (OpenAI-совместимый chat endpoint) — только стандартная библиотека.

Главное правило: персональные данные граждан РУз не покидают Узбекистан (правило проекта № 8).
Поэтому ЛЮБОЙ текст, уходящий во внешнюю модель, проходит через mask_pd(): ФИО, ПИНФЛ, паспорт,
ИНН, телефон, e-mail, госномер, номер карты и кадастровый номер заменяются на плейсхолдеры.
Обойти маскировку нельзя: наружу открыт только chat(), внутри которого она вызывается.

Настройки берутся так: таблица app_settings (приоритет) → переменная окружения → файл .env → значение по умолчанию.
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
ENV_FILE = ROOT / ".env"

TIMEOUT_SEC = 40
RETRIES = 1                       # одна повторная попытка
MAX_PROMPT_CHARS = 12000          # длинные документы режем: и дешевле, и меньше риска

SETTING_KEYS = ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY",
                "TELEGRAM_BOT_TOKEN", "PD_MODE", "SERVER_URL",
                # бот Telegram (app/tgbot.py): секрет вебхука, код первого администратора,
                # запасной режим опроса и версия текста согласия на обработку ПД
                "TG_WEBHOOK_SECRET", "ADMIN_BOOTSTRAP_CODE", "ADMIN_BOOTSTRAP_USED",
                "TG_POLLING", "CONSENT_VERSION")

PROVIDERS = {
    "kimi":      {"base_url": "https://api.moonshot.ai/v1", "model": "kimi-k3",       "name": "Kimi (Moonshot)"},
    "openai":    {"base_url": "https://api.openai.com/v1",  "model": "gpt-4o-mini",   "name": "OpenAI"},
    "anthropic": {"base_url": "https://api.anthropic.com",  "model": "claude-sonnet", "name": "Anthropic (не подключён)"},
    "none":      {"base_url": "",                           "model": "",              "name": "ИИ выключен"},
}

DEFAULTS = {"LLM_PROVIDER": "none", "PD_MODE": "test", "SERVER_URL": "http://127.0.0.1:8000",
            "LLM_BASE_URL": "", "LLM_MODEL": "", "LLM_API_KEY": "", "TELEGRAM_BOT_TOKEN": "",
            "TG_WEBHOOK_SECRET": "", "ADMIN_BOOTSTRAP_CODE": "", "ADMIN_BOOTSTRAP_USED": "",
            "TG_POLLING": "0", "CONSENT_VERSION": "черновик-1"}

NOT_CONNECTED = "ИИ не подключён: не задан ключ API"

last_error = {"text": None}       # текст последней ошибки — показываем в админке


# --------------------------------------------------------------------------- #
#  Настройки
# --------------------------------------------------------------------------- #

def _env_file() -> dict:
    """Простое чтение .env (без пакета python-dotenv): KEY=value, строки с # пропускаем."""
    out = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def _db_settings() -> dict:
    try:
        with db.tx() as con:
            return {r["key"]: r["value"] for r in db.rows(con, "SELECT key, value FROM app_settings")}
    except Exception:          # таблицы ещё нет (первый запуск до ensure_schema)
        return {}


def get(key: str, default: str = None) -> str:
    """Значение настройки: база → окружение → .env → значение по умолчанию."""
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
NAME_INITIALS = re.compile(rf"\b{_CYR_WORD}\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.|\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?{_CYR_WORD}")
NAME_CYR = re.compile(rf"\b{_CYR_WORD}(?:\s+{_CYR_WORD}){{1,3}}\b")
NAME_LAT = re.compile(rf"\b{_LAT_WORD}(?:\s+{_LAT_WORD}){{1,3}}\b")


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
