"""Контракты новой архитектуры: изоляция, транзакции, маршруты и жизненный цикл."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
os.environ["SURVEYOR_NO_BACKGROUND"] = "1"
from tmpdb import temp_db
from app import background, db
from app.application import create_app
from app.config import ConfigurationError, Settings, read_env_files
from app.errors import NotFoundError
from app.infrastructure import migrations
from app.infrastructure.process_lock import ProcessLock
from app.modules.documents import storage
from app.modules.pricing.schemas import CalcIn
from app.modules.pricing.workflow import calculate_quote
from app.modules.surveys.schemas import RequestIn
from app.modules.surveys.workflow import create_survey

PAYLOAD = dict(product_code="0807", value_amount=1e9, sum_insured=8e8)


class ArchitectureTests(unittest.TestCase):
    def test_configuration(self):
        self.assertEqual(Settings.from_env({}).background_mode, "embedded")
        self.assertTrue(Settings.from_env({"SURVEYOR_DEV": "true"}).dev_mode)
        for env in ({"SURVEYOR_DEV": "maybe"}, {"DATABASE_URL": "secret-value"},
                    {"SURVEYOR_BACKGROUND_MODE": "unknown"}):
            with self.assertRaises(ConfigurationError) as error:
                Settings.from_env(env)
            self.assertNotIn("secret-value", str(error.exception))
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder) / "first.env", Path(folder) / "second.env"
            first.write_text('\ufeffA="first"\nB=one=two\n# comment\n', encoding="utf-8")
            second.write_text("A=second\nC='three'\n", encoding="utf-8")
            self.assertEqual(read_env_files((first, second)), {"A": "first", "B": "one=two", "C": "three"})

    def test_import_does_not_create_storage(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "not-created"
            result = subprocess.run([sys.executable, "-c", "from app.main import app"], cwd=ROOT,
                                    env={**os.environ, "STORAGE_DIR": str(target)},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(target.exists(), "Импорт создал хранилище")

    def test_route_contract_and_fail_fast(self):
        app = create_app()
        paths = app.openapi()["paths"]
        expected = json.loads((ROOT / "tests/fixtures/routes.json").read_text(encoding="utf-8"))
        self.assertEqual({p: sorted(methods) for p, methods in paths.items()}, expected)
        with patch("app.application.import_module", side_effect=ImportError("broken module")):
            with self.assertRaises(ImportError):
                create_app()

    def test_engine_has_no_framework_or_storage_import(self):
        import ast
        tree = ast.parse((ROOT / "app/modules/pricing/engine.py").read_text(encoding="utf-8"))
        imports = {n.module.split(".")[0] for n in ast.walk(tree)
                   if isinstance(n, ast.ImportFrom) and n.module}
        imports.update(a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names)
        self.assertFalse(imports & {"app", "fastapi", "sqlite3", "requests", "httpx"}, imports)

    def test_survey_matches_quote_and_rolls_back(self):
        with temp_db():
            quote = calculate_quote(CalcIn(**PAYLOAD))
            result = create_survey(RequestIn(**PAYLOAD))
            self.assertEqual(result["rates"], quote["rates"])
            self.assertEqual(result["premium"], quote["premium"])
            with db.tx() as con:
                count = con.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
                con.execute("CREATE TRIGGER reject_calculation BEFORE INSERT ON calculations BEGIN "
                            "SELECT RAISE(ABORT, 'test failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                create_survey(RequestIn(**PAYLOAD))
            with db.tx() as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM requests").fetchone()[0], count)
                self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            with self.assertRaises(NotFoundError):
                calculate_quote(CalcIn(**{**PAYLOAD, "product_code": "missing"}))

    def test_storage_errors_are_not_converted_to_missing_inputs(self):
        from starlette.requests import Request
        from app.modules.surveys.api import create_request
        with temp_db():
            with patch("app.valuation.valuation_for_engine", side_effect=sqlite3.OperationalError("offline")):
                with self.assertRaises(sqlite3.OperationalError):
                    calculate_quote(CalcIn(**PAYLOAD, object_key="test-object"))
            with patch("app.auth.session_user", side_effect=sqlite3.OperationalError("offline")):
                with self.assertRaises(sqlite3.OperationalError):
                    create_request(RequestIn(**PAYLOAD), Request({"type": "http", "headers": []}))

    def test_documents_access_collision_limit_and_rollback(self):
        from fastapi import HTTPException
        with temp_db(), tempfile.TemporaryDirectory() as folder, patch.object(db, "DATA_DIR", Path(folder)):
            rid = create_survey(RequestIn(**PAYLOAD))["request_id"]
            admin = {"id": 1234, "role": "админ", "login": "test"}
            stranger = {"id": 9876, "role": "сотрудник"}
            with self.assertRaises(HTTPException) as denied:
                storage.store_document(rid, "test", "a.txt", io.BytesIO(b"secret"), stranger)
            self.assertEqual(denied.exception.status_code, 404)
            self.assertFalse((Path(folder) / "uploads").exists())
            first = storage.store_document(rid, "test", "../../a.txt", io.BytesIO(b"one"), admin)
            second = storage.store_document(rid, "test", "../../a.txt", io.BytesIO(b"two"), admin)
            self.assertNotEqual(first["stored"], second["stored"])
            self.assertEqual(Path(first["stored"]).read_bytes(), b"one")
            with patch.object(storage, "MAX_DOCUMENT_BYTES", 128):
                with self.assertRaises(HTTPException) as too_large:
                    storage.store_document(rid, "test", "a.txt", io.BytesIO(b"a" * 129), admin)
                self.assertEqual(too_large.exception.status_code, 413)
            with db.tx() as con:
                con.execute("CREATE TRIGGER reject_document BEFORE INSERT ON documents BEGIN "
                            "SELECT RAISE(ABORT, 'test failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                storage.store_document(rid, "test", "a.txt", io.BytesIO(b"three"), admin)
            self.assertEqual(len(list((Path(folder) / "uploads" / str(rid)).iterdir())), 2)

    def test_migrations_idempotent_and_require_current(self):
        with sqlite3.connect(":memory:") as con:
            with self.assertRaises(RuntimeError):
                migrations.require_current(con)
            migrations.apply(con, db.SCHEMA)
            migrations.apply(con, db.SCHEMA)
            migrations.require_current(con)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 1)
            con.execute("INSERT INTO schema_migrations VALUES (999, 'test')")
            with self.assertRaises(RuntimeError):
                migrations.apply(con, db.SCHEMA)

    def test_initialization_preserves_existing_database_without_products(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "surveyor.db"
            with sqlite3.connect(destination) as con:
                con.execute("CREATE TABLE customer_data (value TEXT)")
                con.execute("INSERT INTO customer_data VALUES ('keep')")
            con.close()
            with patch.object(db, "DATA_DIR", Path(folder)), patch.object(db, "DB_PATH", destination):
                db.init_storage()
            with sqlite3.connect(destination) as con:
                self.assertEqual(con.execute("SELECT value FROM customer_data").fetchone()[0], "keep")
            con.close()

    def test_single_worker_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            one, two = (ProcessLock(Path(folder) / "worker.lock") for _ in range(2))
            one.acquire()
            try:
                with self.assertRaises(RuntimeError):
                    two.acquire()
            finally:
                one.release()
            two.acquire()
            two.release()
            one.acquire()
            release = threading.Timer(.1, one.release)
            release.start()
            try:
                two.acquire(timeout=1)
                two.release()
            finally:
                release.join()

    def test_background_single_start_and_shutdown(self):
        with patch.dict(os.environ, {"SURVEYOR_NO_BACKGROUND": "0"}):
            background.reset()
            try:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(lambda _: background.start("test", lambda: background.wait(86400)), range(30)))
                self.assertEqual(sum(results), 1)
            finally:
                self.assertTrue(background.stop_all(timeout=1))
                background.reset()

    def test_external_mode_does_not_start_web_schedulers(self):
        from app.infrastructure.lifecycle import lifespan
        app = create_app()
        app.state.settings = Settings(db.DATA_DIR, background_mode="external")
        async def exercise():
            async with lifespan(app):
                pass
        with patch("app.infrastructure.lifecycle.initialize_storage"), patch("app.guard.ensure_bootstrap_code"), \
             patch("app.infrastructure.scheduling.start_schedulers") as start:
            asyncio.run(exercise())
            start.assert_not_called()
        background.reset()

    def test_real_scheduler_start_and_stop(self):
        from app import team, market_knowledge, tgbot
        from app.infrastructure import scheduling
        with tempfile.TemporaryDirectory() as folder, patch.object(db, "DATA_DIR", Path(folder)), \
             patch.dict(os.environ, {"SURVEYOR_NO_BACKGROUND": "0"}), \
             patch.object(team, "make_report"), patch.object(market_knowledge, "trigger"), \
             patch.object(tgbot, "start_polling"):
            try:
                scheduling.start_schedulers()
                states = background.status()["threads"]
                self.assertTrue(states["stats-refresh"]["alive"])
                self.assertTrue(states["act-cleanup"]["alive"])
            finally:
                scheduling.stop_schedulers()
            self.assertFalse(any(row["alive"] for row in background.status()["threads"].values()))
            self.assertIsNone(scheduling._owner)
            background.reset()


if __name__ == "__main__":
    unittest.main()
