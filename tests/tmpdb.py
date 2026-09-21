"""
Временная копия базы для тестов, которые пишут данные.

Зачем: тест не должен оставлять строки в рабочей data/surveyor.db — иначе тестовые договоры
попадают в аналитику, портфель и доклад заказчику.

Как пользоваться:

    from tmpdb import temp_db
    with temp_db():
        ...                 # всё, что делает модуль, идёт в копию

Копия удаляется и при падении теста (finally), рабочая база не открывается вообще.
"""
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402


@contextmanager
def temp_db(name: str = "surveyor-test.db"):
    """Подменяет db.DB_PATH копией рабочей базы. Возвращает путь к копии."""
    folder = Path(tempfile.mkdtemp(prefix="surveyor-test-"))
    copy = folder / name
    db.snapshot(db.DB_PATH, copy)       # штатная копия SQLite: учитывает журнал WAL рабочей базы
    original = db.DB_PATH
    db.DB_PATH = copy
    try:
        yield copy
    finally:
        db.DB_PATH = original
        db.close_pool()                 # соединения пула держат файл копии открытым
        shutil.rmtree(folder, ignore_errors=True)
