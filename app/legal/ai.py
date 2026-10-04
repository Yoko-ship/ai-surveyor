"""ИИ — необязательное улучшение ответа: пересказ строго по найденным пассажам или свободный ответ
с пометкой «ИИ». Нет ключа — status «off», мгновенный ответ всё равно выдаётся."""
from typing import Optional

from .. import llm
from . import index as ix
from .search_core import MAX_PASSAGES, _cap, _unit_of
from .texts import AI_NOTE, DEFAULT_LANG

AI_TIMEOUT_SEC = 8

GUARD_FILE = ix.ROOT / "app" / "llm_prompts" / "legal_guard.ru.txt"
SYSTEM_FILE = ix.ROOT / "app" / "llm_prompts" / "system.json"
_guard_cache = {"mtime": None, "text": ""}

# Роль помощника. Правовой блок (legal_guard.ru.txt) подклеивается к ней целиком: запреты
# «не сочинять нормы», «не обещать выплату», «не толковать договор» действуют и здесь.
AI_ROLE = ("Ты «ИИ специалист по страхованию INSON» — специалист по страхованию и рынку Узбекистана, "
           "помощник сотрудников страховой организации в Узбекистане. Ты отвечаешь и на вопросы практики (андеррайтинг, документы, оценка, "
           "убытки), и на правовые вопросы. Практику объясняй просто и по делу; норму — только со "
           "ссылкой на акт, статью и пункт. Ответ — 2–5 предложений. Язык ответа строго: %s.")

AI_SYSTEM = AI_ROLE + ("\nОтвечай ТОЛЬКО по приведённым ниже пассажам. Ничего не добавляй от себя: "
                       "если в пассажах ответа нет — так и напиши. Цитируй номера статей и пунктов.")

# Нормы в пассажах нет: модель отвечает по общей практике страхования и обязана это пометить
AI_SYSTEM_FREE = AI_ROLE + ("\nНормы по этому вопросу тебе не передали. Отвечай по общей практике "
                            "страхования и учебникам, НЕ ссылайся на конкретные статьи и пункты и не "
                            "называй номера актов. Если вопрос требует нормы — скажи, что нужен юрист.")


def guard_text() -> str:
    """Правовой блок системного промпта (app/llm_prompts/legal_guard.ru.txt). Файла нет — работаем без него."""
    try:
        st = GUARD_FILE.stat()
    except OSError:
        return ""
    if _guard_cache["mtime"] != st.st_mtime:
        try:
            _guard_cache.update({"mtime": st.st_mtime,
                                 "text": GUARD_FILE.read_text(encoding="utf-8")})
        except Exception as e:
            print("legal: правовой блок промпта не прочитан:", e)
            _guard_cache.update({"mtime": st.st_mtime, "text": ""})
    return _guard_cache["text"]


def system_prompt(lang: str, free: bool = False) -> str:
    """Системный промпт «ИИ специалиста»: роль + правовой блок юриста."""
    base = (AI_SYSTEM_FREE if free else AI_SYSTEM) % lang
    guard = guard_text()
    return base + ("\n\n" + guard if guard else "")


def _history_text(history: Optional[list]) -> str:
    """Последние реплики диалога для модели (только в запрос; ПД маскирует app/llm)."""
    if not history:
        return ""
    lines = ["%s: %s" % ("Пользователь" if h.get("role") == "user" else "Специалист", (h.get("text") or "")[:300])
             for h in history[-6:]]
    return "Контекст диалога (предыдущие реплики):\n" + "\n".join(lines) + "\n\n"


def ai_answer(question: str, passages: list, lang: str, history: Optional[list] = None) -> dict:
    """Пересказ по найденным пассажам. Нет ключа — ai.status='off', мгновенный ответ уже отдан."""
    if not llm.enabled():
        return {"status": "off", "text": None}
    body = "\n\n".join(f"[{_cap(r['act'])} {_unit_of(r)}]\n{r['body'][:1200]}" for r in passages[:MAX_PASSAGES])
    if not body:
        return {"status": "off", "text": None}
    try:
        # ответ по норме ждать дольше 8 с нет смысла; таймаут — только этому вызову
        text = llm.chat("вопрос специалисту по страхованию", system_prompt(lang),
                        f"{_history_text(history)}Вопрос: {question}\n\nПассажи:\n{body}", max_tokens=400,
                        timeout=AI_TIMEOUT_SEC)
    except Exception as e:
        return {"status": "error", "text": None, "reason": str(e)[:200]}
    if not text:
        return {"status": "error", "text": None, "reason": (llm.last_error or {}).get("text")}
    return {"status": "ok", "text": text.strip()}


def ai_free_answer(question: str, lang: str, history: Optional[list] = None) -> dict:
    """Ни FAQ, ни закон вопрос не покрыли: отвечает модель, ответ помечается «ИИ»."""
    if not llm.enabled():
        return {"status": "off", "text": None}
    try:
        text = llm.chat("вопрос специалисту по страхованию (без нормы)",
                        system_prompt(lang, free=True), f"{_history_text(history)}Вопрос: {question}",
                        max_tokens=400, timeout=AI_TIMEOUT_SEC)
    except Exception as e:
        return {"status": "error", "text": None, "reason": str(e)[:200]}
    if not text:
        return {"status": "error", "text": None, "reason": (llm.last_error or {}).get("text")}
    return {"status": "ok", "text": text.strip(), "source": "ai",
            "note": AI_NOTE.get(lang) or AI_NOTE[DEFAULT_LANG]}
