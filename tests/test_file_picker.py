"""Выбор документов Android: MIME может отсутствовать, ограничения загрузки сохраняются."""
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FilePickerTests(unittest.TestCase):
    def test_client_accepts_docx_with_missing_or_legacy_mime_and_explains_doc(self):
        source = Path("frontend/tg/js/wizard.js").read_text()
        constants = source[source.index("const CH_MAX_FILES"):source.index("const FR_OFF")]
        convert = source[source.index("const WZ_CONVERT"):source.index("function wzToJpeg")]
        add = source[source.index("async function wzAddFiles("):source.index("function cbKindable(")]
        script = """
const assert = require('node:assert/strict');
const CH = {busy:false, queue:[], seq:0, wz:1};
const window = {}, haptic=()=>{}, wzSave=()=>{}, wzPaint=()=>{};
const T = (key,fallback,values={}) => Object.keys(values).reduce((s,k)=>s.replaceAll('{'+k+'}',values[k]),fallback);
""" + constants + convert + add + """
(async()=>{
 await wzAddFiles([{name:'Договор.DOCX',type:'',size:1024},
  {name:'Документ.docx',type:'application/msword',size:2048},
  {name:'Таблица.xlsx',type:'application/octet-stream',size:2048}]);
 assert.equal(CH.queue.length,3);
 assert.equal(CH.err,'');
 await wzAddFiles([{name:'Договор.doc',type:'application/msword',size:1024}]);
 assert.equal(CH.queue.length,3);
 assert.match(CH.err,/DOCX/); assert.match(CH.err,/PDF/); assert.match(CH.err,/переименование/);
 await wzAddFiles([{name:'program.exe',type:'application/octet-stream',size:1024}]);
 assert.equal(CH.queue.length,3); assert.ok(CH.err);
 await wzAddFiles([{name:'large.docx',type:'',size:16*1048576}]);
 assert.equal(CH.queue.length,3); assert.match(CH.err,/15/);
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_server_checks_docx_content_instead_of_browser_mime(self):
        from app.act_pkg.photos import _save_files
        from app.act_engine import DEFAULT_SETTINGS
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as doc:
            doc.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Test document</w:t></w:r></w:p></w:body></w:document>')
        with tempfile.TemporaryDirectory() as folder:
            files = [SimpleNamespace(filename="contract.docx", content_type="application/msword",
                                     file=io.BytesIO(content.getvalue())),
                     SimpleNamespace(filename="spoof.docx", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                     file=io.BytesIO(b"This is not a Word file"))]
            saved, rejected = _save_files(files, Path(folder), DEFAULT_SETTINGS["limits"], "ru")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["fmt"], "docx")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["index"], 2)


if __name__ == "__main__":
    unittest.main()
