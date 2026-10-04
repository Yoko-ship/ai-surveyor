"""Память диалога специалиста: последние 8 реплик на сессию, 2 часа, только в памяти процесса.
В базу текст реплик не пишется (там только отпечаток вопроса — cache.log_question)."""
import hashlib
import re
import threading
import time
from collections import deque
from typing import Optional

MEMORY_TURNS = 8
MEMORY_TTL_SEC = 2 * 3600
MEMORY_MAX_SESSIONS = 2000
SESSION_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")


class DialogMemory:
    """session → последние 8 реплик и контекст. Ключ — отпечаток (кто + session_id): чужой session_id
    не даёт доступа к контексту другого пользователя."""

    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(session_id: Optional[str], who: Optional[str]) -> Optional[str]:
        if not session_id:
            return None
        return hashlib.sha256(("%s|%s" % (who or "-", session_id)).encode("utf-8")).hexdigest()[:40]

    def _gc(self, now: float):
        dead = [k for k, v in self._data.items() if now - v["t"] > MEMORY_TTL_SEC]
        for k in dead:
            self._data.pop(k, None)
        if len(self._data) > MEMORY_MAX_SESSIONS:
            for k, _ in sorted(self._data.items(), key=lambda kv: kv[1]["t"])[:len(self._data) - MEMORY_MAX_SESSIONS]:
                self._data.pop(k, None)

    def get(self, key: Optional[str]) -> dict:
        if not key:
            return {"turns": [], "ctx": None}
        now = time.time()
        with self._lock:
            self._gc(now)
            s = self._data.get(key)
            if not s:
                return {"turns": [], "ctx": None}
            return {"turns": list(s["turns"]), "ctx": dict(s["ctx"]) if s["ctx"] else None}

    def add(self, key: Optional[str], question: str, reply: str, ctx: Optional[dict]):
        if not key:
            return
        now = time.time()
        with self._lock:
            s = self._data.setdefault(key, {"turns": deque(maxlen=MEMORY_TURNS), "ctx": None, "t": now})
            s["turns"].append({"role": "user", "text": (question or "")[:1000], "t": now})
            s["turns"].append({"role": "assistant", "text": (reply or "")[:1000], "t": now})
            s["ctx"] = ctx
            s["t"] = now
            self._gc(now)

    def clear(self):
        with self._lock:
            self._data.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._data)


memory = DialogMemory()
