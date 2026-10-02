"""
Фоновые потоки сервера: единый запуск, защита от двойного старта, журнал ошибок и состояние для /health.

    background.start("stats-refresh", loop_fn)      # loop_fn — бесконечный цикл модуля
    background.run_loop(name, step, first_delay, every)  # готовый цикл: пауза → step() → пауза ...
    background.ok(name) / background.failed(name, exc) — отметки итераций из своих циклов
    background.plan(name, seconds)                  — когда следующий запуск (для /health, «Источники данных»)

SURVEYOR_NO_BACKGROUND=1 — служебный флаг для тестов и замеров: ни один поток не запускается.
Ошибки пишутся в журнал (logging) с трассировкой; наружу (/health) — только время и тип ошибки,
без текста: в тексте исключения может оказаться адрес с ключом или данные человека.
"""
import logging
import os
import threading
import time
from datetime import datetime, timedelta

log = logging.getLogger("surveyor.background")

_lock = threading.Lock()
_threads = {}            # имя -> threading.Thread
_state = {}              # имя -> {"started", "last_ok", "last_error_at", "last_error", "errors"}


def disabled() -> bool:
    return os.environ.get("SURVEYOR_NO_BACKGROUND") == "1"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _st(name: str) -> dict:
    return _state.setdefault(name, {"started": None, "last_ok": None, "last_error_at": None,
                                    "last_error": None, "errors": 0, "next_at": None})


def plan(name: str, seconds: float) -> None:
    """Отметка «следующий запуск через seconds» — перед сном цикла."""
    at = (datetime.now() + timedelta(seconds=max(0, seconds))).isoformat(timespec="seconds")
    with _lock:
        _st(name)["next_at"] = at


def ok(name: str) -> None:
    with _lock:
        _st(name)["last_ok"] = _now()


def failed(name: str, exc: BaseException = None, kind: str = None) -> None:
    """Итерация не удалась. В журнал — трассировка (если есть исключение), в состояние — только тип."""
    if exc is not None:
        log.error("фоновый поток %s: итерация с ошибкой", name, exc_info=(type(exc), exc, exc.__traceback__))
    with _lock:
        s = _st(name)
        s["last_error_at"] = _now()
        s["last_error"] = kind or (type(exc).__name__ if exc is not None else "ошибка")
        s["errors"] += 1


def _guarded(name: str, target):
    def body():
        try:
            target()
        except BaseException as e:            # сам цикл упал — фиксируем, поток завершается
            failed(name, e)
    return body


def start(name: str, target) -> bool:
    """Запускает поток, если он ещё не запущен (или умер). False — отключено или уже работает."""
    if disabled():
        return False
    with _lock:
        t = _threads.get(name)
        if t is not None and t.is_alive():
            return False
        t = threading.Thread(target=_guarded(name, target), daemon=True, name=name)
        _threads[name] = t
        _st(name)["started"] = _now()
    t.start()
    return True


def run_loop(name: str, step, first_delay: float, every: float):
    """Цикл «пауза → step() → пауза». Исключение в step() журналируется, цикл продолжается."""
    plan(name, first_delay)
    time.sleep(first_delay)
    while True:
        try:
            step()
            ok(name)
        except Exception as e:
            failed(name, e)
        plan(name, every)
        time.sleep(every)


def state(name: str) -> dict:
    """Состояние одного потока (копия) — для блока «Источники данных»."""
    with _lock:
        t = _threads.get(name)
        return {"alive": bool(t and t.is_alive()), **dict(_st(name))}


def status() -> dict:
    """Для /health: живы ли потоки и когда последний раз отработали. Без текстов ошибок."""
    with _lock:
        out = {}
        for name in sorted(set(_threads) | set(_state)):
            t = _threads.get(name)
            s = dict(_st(name))
            out[name] = {"alive": bool(t and t.is_alive()), **s}
        return {"disabled": disabled(), "threads": out}
