"""Упорядоченные миграции SQLite. Следующее изменение схемы — новая версия."""
from datetime import datetime
import sqlite3

CURRENT_VERSION = 1


def version(con) -> int:
    exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    return con.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0] if exists else 0


def require_current(con):
    if version(con) != CURRENT_VERSION:
        raise RuntimeError("Версия базы не соответствует приложению; выполните python -m app.migrate")


def apply(con, schema_path):
    current = version(con)
    if current > CURRENT_VERSION:
        raise RuntimeError("База создана более новой версией приложения; откат кода запрещён")
    if current < 1:
        from .legacy_schema import migrate, _carry_decision_outcomes, backfill_login_links
        # Старые миграции изменяют NOT NULL через пересоздание таблицы и управление FK.
        # Они идемпотентны; запись версии появляется только после всех шагов.
        migrate(con)
        con.executescript(schema_path.read_text(encoding="utf-8"))
        _carry_decision_outcomes(con)
        backfill_login_links(con)
        bad = con.execute("PRAGMA foreign_key_check").fetchone()
        if bad:
            raise sqlite3.IntegrityError("Нарушены внешние ключи; миграция не отмечена завершённой")
        con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        con.execute("INSERT INTO schema_migrations VALUES (?, ?)",
                    (1, datetime.now().isoformat(timespec="seconds")))
