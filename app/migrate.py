"""Явный запуск миграций: python -m app.migrate. Существующие данные не пересобираются."""
from .config import Settings, load_environment


def main():
    load_environment()
    Settings.from_env()
    from . import db
    db.init_storage()
    db.ensure_schema()
    print("Схема базы обновлена.")


if __name__ == "__main__":
    main()
