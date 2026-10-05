"""Один владелец периодических заданий на хранилище: HTTP-процесс либо worker."""
from app import background, db
from .process_lock import ProcessLock

_owner = None


def _inbox_step():
    from app import history
    result = history.auto_import()
    if result:
        with db.tx() as con:
            db.audit(con, "агент-статистик", "автоимпорт выгрузок", "portfolio", result)


def start_schedulers():
    global _owner
    if _owner is not None:
        return
    lock = ProcessLock(db.DATA_DIR / "worker.lock")
    lock.acquire()
    _owner = lock
    try:
        background.reset()
        from app import analysis_docs, act, team, statagency, uzex, lawwatch, competitors, tgbot
        from app.modules.market.refresh import _scheduler
        background.start("stats-refresh", _scheduler)
        for name, step, delay, every in (
            ("inbox-watcher", _inbox_step, 90, 600),
            ("analysis-cleanup", analysis_docs.cleanup, 120, 3600),
            ("act-cleanup", act.cleanup, 120, 3600),
        ):
            background.start(name, lambda n=name, s=step, d=delay, e=every:
                             background.run_loop(n, s, first_delay=d, every=e))
        for module in (team, statagency, uzex, lawwatch, competitors):
            module.start_scheduler()
        tgbot.start_polling()
        from app import market_knowledge
        market_knowledge.trigger()
    except BaseException:
        stop_schedulers()
        raise


def stop_schedulers():
    global _owner
    finished = background.stop_all()
    if _owner is not None and finished:
        _owner.release()
        _owner = None
