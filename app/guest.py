"""
Гостевой режим мини-приложения (решение заказчика 22.09.2026).

Приложение открыто для всех: аналитика риска, расчёт, ОСГОР и юридические ответы работают
без входа. Вход остаётся только администратору — для админки и правки справочников.

Что здесь:
  * guest_id — анонимный токен в cookie «gid» на 24 часа. Он не персональные данные:
    случайные байты, ни имени, ни телефона, ни привязки к человеку. Нужен ровно для двух вещей —
    «мои загруженные файлы» (чужой guest_id к чужому файлу не пускает) и счётчик ограничений.
  * owner_key — единый ключ владельца файла: «u:<id>» для вошедшего, «g:<guest_id>» для гостя.
    Один формат — чтобы в таблице был один столбец, а не два взаимоисключающих.
  * ограничение злоупотреблений: окно в час на ключ (guest_id, а если его нет — IP).
    Счётчики в памяти процесса: перезапуск их обнуляет, и это осознанно — защита от перебора,
    а не учёт. В журнал уходит только хэш ключа и счётчик, без IP и без ПД.
"""
import hashlib
import os
import secrets
import threading
import time
from typing import Optional

COOKIE = "gid"
TTL_HOURS = 24
TTL_SEC = TTL_HOURS * 3600
TOKEN_RE_LEN = (24, 64)

WINDOW_SEC = 3600
# бакет -> сколько можно за час одному гостю (guest_id или IP)
LIMITS = {
    "analysis": 60,     # анализы риска, расчёты, ОСГОР
    "upload": 30,       # загрузка договора для анализа
    "legal": 200,       # юридические вопросы
}
LIMIT_TEXT = {
    "analysis": "Слишком много расчётов подряд: не больше %d в час без входа. Подождите немного и повторите.",
    "upload": "Слишком много загрузок подряд: не больше %d файлов в час без входа. Подождите немного и повторите.",
    "legal": "Слишком много вопросов подряд: не больше %d в час без входа. Подождите немного и повторите.",
}

_lock = threading.Lock()
_hits: dict = {}          # (бакет, ключ) -> [метки времени]


# --------------------------------------------------------------------------- #
#  Анонимный идентификатор гостя
# --------------------------------------------------------------------------- #

def new_id() -> str:
    """Случайный токен гостя. Ни от чего не зависит — угадать чужой нельзя."""
    return secrets.token_urlsafe(18)


def valid(value: str) -> bool:
    lo, hi = TOKEN_RE_LEN
    return bool(value) and lo <= len(value) <= hi and all(
        c.isalnum() or c in "-_" for c in value)


def from_request(request) -> Optional[str]:
    """guest_id из cookie запроса (или из scope, если его туда уже положил guard)."""
    gid = request.scope.get("surveyor_guest") if hasattr(request, "scope") else None
    if gid:
        return gid
    gid = (request.cookies.get(COOKIE) or "").strip()
    return gid if valid(gid) else None


def cookie_header(gid: str, secure: bool = True) -> bytes:
    """Set-Cookie для гостя: только HTTP, SameSite=Lax (мини-апп открывается переходом), 24 часа."""
    parts = [f"{COOKIE}={gid}", "Path=/", f"Max-Age={TTL_SEC}", "HttpOnly", "SameSite=Lax"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts).encode("latin-1")


def cookie_secure() -> bool:
    """На локальной разработке по http cookie с флагом Secure браузер не сохранит."""
    return os.environ.get("SURVEYOR_DEV") != "1"


# --------------------------------------------------------------------------- #
#  Владелец файла
# --------------------------------------------------------------------------- #

def owner_key(user: Optional[dict] = None, gid: Optional[str] = None) -> Optional[str]:
    """«u:<id>» для вошедшего, «g:<guest_id>» для гостя, None — владельца определить нечем."""
    if user and user.get("id"):
        return "u:%s" % user["id"]
    if gid and valid(gid):
        return "g:" + gid
    return None


def owner_of(request, user: Optional[dict] = None) -> Optional[str]:
    return owner_key(user, from_request(request))


def short(value: str) -> str:
    """Короткий хэш для журналов: ни guest_id, ни IP в открытом виде наружу не уходят."""
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
#  Ограничение злоупотреблений
# --------------------------------------------------------------------------- #

def _prune(marks: list, now: float) -> list:
    return [t for t in marks if now - t < WINDOW_SEC]


def hit(bucket: str, key: str, now: Optional[float] = None) -> dict:
    """
    Считает обращение. {"ok": bool, "count": int, "limit": int, "retry_after": сек}.
    Отказ («ok»: False) обращение не засчитывает — иначе окно никогда не закроется.
    """
    limit = LIMITS.get(bucket)
    if not limit or not key:
        return {"ok": True, "count": 0, "limit": limit or 0, "retry_after": 0}
    now = now if now is not None else time.time()
    k = (bucket, key)
    with _lock:
        marks = _prune(_hits.get(k) or [], now)
        if len(marks) >= limit:
            _hits[k] = marks
            retry = int(WINDOW_SEC - (now - marks[0])) + 1
            return {"ok": False, "count": len(marks), "limit": limit, "retry_after": max(retry, 1)}
        marks.append(now)
        _hits[k] = marks
        return {"ok": True, "count": len(marks), "limit": limit, "retry_after": 0}


def message(bucket: str, limit: int) -> str:
    return LIMIT_TEXT.get(bucket, "Слишком много обращений: не больше %d в час.") % limit


def reset(bucket: Optional[str] = None) -> None:
    """Сброс счётчиков — для тестов и для ручного снятия блокировки."""
    with _lock:
        if bucket is None:
            _hits.clear()
        else:
            for k in [k for k in _hits if k[0] == bucket]:
                _hits.pop(k, None)
