"""Настоящий синтетический DOC: текст, лимиты, неверный OLE, отсутствие отправки ИИ."""
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import act_extras as ax, ingest, legacy_doc as reader
from app.act_engine import DEFAULT_SETTINGS
from app.act_pkg.photos import _save_files, _model_pass

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_contract.doc"


class LegacyDocTests(unittest.TestCase):
    def test_real_word_cyrillic_and_uzbek(self):
        result = ingest.read_file(FIXTURE)
        self.assertIsNone(result["status"], result)
        self.assertEqual(result["format"], "doc")
        self.assertIn("ДОГОВОР СТРАХОВАНИЯ", result["text"])
        self.assertIn("Sugʻurta shartnomasi", result["text"])
        self.assertIn("100 000 000", result["text"])

    def test_upload_validates_contents_and_preserves_model_exclusion(self):
        limits = DEFAULT_SETTINGS["limits"]
        with tempfile.TemporaryDirectory() as folder:
            files = [SimpleNamespace(filename="contract.DOC", content_type="",
                                     file=io.BytesIO(FIXTURE.read_bytes())),
                     SimpleNamespace(filename="spoof.doc", content_type="application/msword",
                                     file=io.BytesIO(reader.OLE_SIGNATURE + b"invalid" * 100))]
            saved, rejected = _save_files(files, Path(folder), limits, "ru")
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["fmt"], "doc")
            self.assertEqual(rejected[0]["index"], 2)
            # Настоящий разбор условий, не только принятие расширения.
            from app import db
            with db.tx() as con:
                parsed = {"f1": ax.parse_document_limited(con, saved[0]["full"], limits=limits)}
            self.assertTrue(parsed["f1"]["text_layer"], parsed)
            self.assertTrue(parsed["f1"]["items"], parsed)
            with patch("app.act_pkg.photos.recognize", side_effect=AssertionError("DOC sent to model")):
                _model_pass(saved, parsed, limits, "ru", True)

    def test_text_budget_marks_partial(self):
        result = ax.read_limited(FIXTURE, {"doc_max_text_chars": 50})
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["text"]), 50)

    def test_failure_modes_do_not_return_partial_text(self):
        blob = FIXTURE.read_bytes()
        with patch.object(reader.shutil, "which", return_value=None):
            with self.assertRaisesRegex(reader.DocReadError, "reader_unavailable"):
                reader.read_doc_bytes(blob)
        with patch.object(reader.subprocess, "run", side_effect=subprocess.TimeoutExpired("reader", 5)):
            with self.assertRaisesRegex(reader.DocReadError, "reader_timeout"):
                reader.read_doc_bytes(blob)
        with patch.object(reader.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(reader.DocReadError, "unreadable_doc"):
                reader.read_doc_bytes(blob)
        with self.assertRaisesRegex(reader.DocReadError, "invalid_doc"):
            reader.read_doc_bytes(b"not a word document")


if __name__ == "__main__":
    unittest.main()
