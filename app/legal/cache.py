"""Кэш ответов (час, до 500 вопросов), кэш весов слов поиска и журнал вопросов — только отпечаток вопроса."""
import hashlib
import threading
import time

from .. import db
from .parse import norm

CACHE_TTL_SEC = 3600
CACHE_MAX = 500

# вес слова: редкое слово вопроса («попугай», «крыша») решает, есть ли вообще норма по теме,
# частое («страховой», «договор») встречается в половине базы и о совпадении темы не говорит
_df_cache = {"n": {}, "df": {}}

_cache = {}
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        if time.time() - hit[0] > CACHE_TTL_SEC:
            _cache.pop(key, None)
            return None
        return hit[1]


def _cache_put(key, value):
    # Временная недоступность источника/модели не должна маскировать восстановление.
    if (value.get("live") or {}).get("status") in ("unavailable", "limit", "error"):
        return
    if (value.get("ai") or {}).get("status") == "error":
        return
    with _cache_lock:
        if len(_cache) >= CACHE_MAX:
            _cache.clear()
        _cache[key] = (time.time(), value)


def q_hash(question: str, lang: str) -> str:
    """Отпечаток вопроса вместо текста: в вопросе может быть ФИО или ИНН — в базу их не кладём."""
    return hashlib.sha256(norm(question).encode("utf-8")).hexdigest()[:32]


def log_question(question: str, lang: str, source: str, confidence: float, took_ms: int, found: bool):
    try:
        with db.tx() as con:
            con.execute("INSERT INTO legal_questions (created_at, lang, q_hash, source, confidence,"
                        " took_ms, found) VALUES (?,?,?,?,?,?,?)",
                        (db.now(), lang, q_hash(question, lang), source, confidence, took_ms, int(found)))
    except Exception as e:
        print("legal: вопрос не записан в журнал:", e)
