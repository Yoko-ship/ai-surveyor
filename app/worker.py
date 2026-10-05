"""Отдельный исполнитель: python -m app.worker (то же STORAGE_DIR, что у HTTP)."""
import signal
import threading

from .config import Settings, load_environment


def main():
    load_environment()
    settings = Settings.from_env()
    if settings.no_background:
        raise SystemExit("SURVEYOR_NO_BACKGROUND=1: фоновый исполнитель отключён")
    from . import web
    from .infrastructure.lifecycle import initialize_storage
    from .infrastructure.scheduling import start_schedulers, stop_schedulers

    web.setup_logging()
    web.install_log_filters()
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    initialize_storage(settings)
    try:
        start_schedulers()
        while not stopped.wait(1):
            pass
    finally:
        stop_schedulers()
        from . import db
        db.close_pool()


if __name__ == "__main__":
    main()
