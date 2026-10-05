"""Инициализация хранения и жизненный цикл сервера, без работы при импорте."""
from contextlib import asynccontextmanager
import os
import subprocess
import sys

from app import background, db
from app.config import PROJECT_ROOT, Settings

def initialize_storage(settings: Settings):
    with background.suspend():
        _initialize_storage(settings)


def _initialize_storage(settings: Settings):
    db.init_storage()
    if settings.auto_migrate:
        db.ensure_schema()
    else:
        from .migrations import require_current
        with db.tx() as con:
            require_current(con)
    from app import knowledge, lawwatch, rankings, refsync
    refsync.sync_on_start()
    knowledge._ensure_seed()
    lawwatch._ensure_seed()
    rankings.ensure_loaded()
    for name in ("uploads", "photos"):
        (db.DATA_DIR / name).mkdir(parents=True, exist_ok=True)


def _demo_seed_then_bootstrap():
    try:
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "tools" / "demo_seed.py"), "--yes"],
            capture_output=True, text=True, timeout=600,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        # Вывод скрипта может содержать учётные данные: в общем журнале только статус.
        if result.returncode:
            background.failed("demo-seed", kind="ненулевой код завершения")
    except subprocess.TimeoutExpired:
        background.failed("demo-seed", kind="превышено время")
    finally:
        db.invalidate_reference()
        from app.guard import ensure_bootstrap_code
        ensure_bootstrap_code()


@asynccontextmanager
async def lifespan(app):
    from app import guard, tgpage, web
    from .scheduling import start_schedulers, stop_schedulers

    settings = app.state.settings
    web.install_log_filters()
    background.reset()
    if settings.dev_mode:
        tgpage.ensure_fresh()
    initialize_storage(settings)
    try:
        if not settings.no_background and settings.background_mode == "embedded":
            start_schedulers()
        if not (settings.demo_seed and background.start("demo-seed", _demo_seed_then_bootstrap)):
            guard.ensure_bootstrap_code()
        yield
    finally:
        stop_schedulers()
        db.close_pool()
