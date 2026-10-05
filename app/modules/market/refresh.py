"""Обновление НАПП: общая операция для расписания и ручного запроса."""
import threading
from app import background, db
from tools import market_stats
from app.infrastructure.process_lock import AlreadyLockedError, ProcessLock
REFRESH_EVERY_SEC = 24 * 3600
_refresh_state = {"last": None, "log": [], "running": False}
_refresh_lock = threading.Lock()

def _refresh_job():
    """Агент-статистик: сам проверяет сайт НАПП, забирает новые отчёты и обновляет ряд."""
    if not _refresh_lock.acquire(blocking=False):
        return
    owner = ProcessLock(db.DATA_DIR / "market-refresh.lock")
    try:
        try:
            owner.acquire()
        except AlreadyLockedError:
            return  # кнопка и планировщик могут находиться в разных процессах
        _refresh_state["running"] = True
        _refresh_state["log"] = market_stats.refresh()
        _refresh_state["last"] = db.now()
        with db.tx() as con:
            db.audit(con, "агент-статистик", "обновление рыночной статистики", "market_stats",
                     _refresh_state["log"][-1])
        background.ok("stats-refresh")
        _after_stats()
    except Exception as e:  # ошибка сети не должна ронять сервер
        _refresh_state["log"] = [f"ошибка: {e}"]
        background.failed("stats-refresh", e)
    finally:
        _refresh_state["running"] = False
        owner.release()
        _refresh_lock.release()


def _after_stats():
    """Новый срез НАПП → пересборка знаний о рынке (в фоне, только если данные изменились)."""
    try:
        from app import market_knowledge
        market_knowledge.trigger()
    except Exception as e:                # знания — дополнение: статистика уже обновлена
        background.failed("market-knowledge", e)


def _scheduler():
    background.plan("stats-refresh", 60)
    background.wait(60)               # даём серверу подняться
    while not background.stopping():
        _refresh_job()
        background.plan("stats-refresh", REFRESH_EVERY_SEC)
        background.wait(REFRESH_EVERY_SEC)
