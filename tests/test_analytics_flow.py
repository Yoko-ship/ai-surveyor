"""
Аналитика по шагам (задача 150, 21.09.2026): продукт → документы → договор → суммы → анализ.
app/analysis_docs.py, app/risk_api.py (doc_ids, documents, ai_summary), меню мини-аппа (app/tgbot.py).

Запуск из корня (свой ASGI-клиент, живой сервер не трогаем):
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_analytics_flow.py

Вся работа — во временной копии базы (tests/tmpdb.py); файлы договоров — во временной папке
(analysis_docs.DIR подменяется), рабочая data/analysis не трогается.
"""
import asyncio
import io
import json as _json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.pop("SURVEYOR_DEV", None)     # guard проверяем целиком

from tmpdb import temp_db                 # noqa: E402
from app import analysis_docs, auth, db, llm   # noqa: E402
from app.main import app                  # noqa: E402

PREFIX = "тест-шаги-"
EMP, EMP2, ADM = PREFIX + "сотрудник", PREFIX + "сотрудник-2", PREFIX + "админ"
PEOPLE = {EMP: "сотрудник", EMP2: "сотрудник", ADM: "админ"}
TOKENS = {}
passed, failed = 0, 0

CONTRACT_LINES = [
    "ДОГОВОР СТРАХОВАНИЯ ИМУЩЕСТВА № 15/2026",
    "г. Ташкент, 21 сентября 2026 г.",
    "Страхователь: ООО «Тестовый склад», ИНН 301234567",
    "Объект страхования: склад готовой продукции",
    "Адрес: г. Ташкент, Юнусабадский район, ул. Амира Темура, 1",
    "Материал стен: кирпич",
    "Год постройки: 2012",
    "Страховая сумма: 4 200 000 000 сум",
    "Стоимость имущества: 5 000 000 000 сум",
    "Срок страхования: 12 месяцев",
]


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ок  ", name)
    else:
        failed += 1
        print("  ПЛОХО", name, extra)


def call(method, path, body=None, params=None, who=None, raw=None, ctype="application/json"):
    query = urlencode(params or {}, encoding="utf-8")
    payload = raw if raw is not None else (
        _json.dumps(body, ensure_ascii=False).encode() if body is not None else b"")
    hdrs = [(b"host", b"test"), (b"content-type", ctype.encode()),
            (b"content-length", str(len(payload)).encode())]
    if who:
        hdrs.append((b"cookie", f"sid={TOKENS[who]}".encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(), "headers": hdrs,
             "client": ("203.0.113.9", 0), "server": ("test", 80)}
    out = {"status": None, "chunks": []}

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["chunks"].append(msg.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    text = b"".join(out["chunks"]).decode("utf-8", "replace")
    try:
        return out["status"], _json.loads(text)
    except ValueError:
        return out["status"], text


def upload(blob: bytes, filename: str, who=EMP, params=None):
    b = "----analytics-flow-boundary"
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + blob + f"\r\n--{b}--\r\n".encode()
    return call("POST", "/analytics/risk/document", raw=body, who=who, params=params,
                ctype=f"multipart/form-data; boundary={b}")


# ---------- файлы договоров ----------

def make_docx(lines) -> bytes:
    """Минимальный DOCX: только то, что читает app/ingest.py (word/document.xml)."""
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    paras = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{ln}</w:t></w:r></w:p>" for ln in lines)
    doc = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{ns}"><w:body>{paras}</w:body></w:document>'
    types = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
             'content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
             'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override '
             'PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
             'wordprocessingml.document.main+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def _font() -> str:
    for p in (r"C:\Windows\Fonts\arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(p).exists():
            return p
    return ""


def make_pdf(lines) -> bytes:
    import pymupdf as fitz
    d = fitz.open()
    page = d.new_page()
    font = _font()
    if font:
        page.insert_font(fontname="ru", fontfile=font)
    y = 72
    for ln in lines:
        page.insert_text((56, y), ln, fontname="ru" if font else "helv", fontsize=11)
        y += 18
    out = d.tobytes()
    d.close()
    return out


def make_scan_pdf() -> bytes:
    """PDF без текстового слоя — как скан: только рисунок."""
    import pymupdf as fitz
    d = fitz.open()
    page = d.new_page()
    page.draw_rect(fitz.Rect(50, 50, 300, 200), color=(0, 0, 0), fill=(0.8, 0.8, 0.8))
    out = d.tobytes()
    d.close()
    return out


# ---------- подготовка ----------

def setup():
    db.ensure_schema()
    with db.tx() as con:
        ts = db.now()
        for login, role in PEOPLE.items():
            cur = con.execute("INSERT INTO users (login, full_name, role, branch, password_hash, salt,"
                              " status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                              (login, "Тест", role, "тест-шаги", "x", "y", "активен", ts))
            u = db.rows(con, "SELECT * FROM users WHERE id=?", cur.lastrowid)[0]
            TOKENS[login], _ = auth.create_session(con, u, "203.0.113.9", "test")


# ---------- 1. меню ----------

def check_menu():
    print("1. Меню мини-аппа без «Ждут меня», «Заявок», «Соглашений»")
    for who, want in ((EMP, ["analytics", "calc", "osgor", "photos", "users"]),
                      (ADM, ["analytics", "calc", "osgor", "photos", "users", "settings"])):
        st, me = call("GET", "/tg/me", who=who)
        keys = [n["key"] for n in me.get("nav", [])] if st == 200 else []
        ok(f"{PEOPLE[who]}: меню {', '.join(want)}", keys == want, keys)
        ok(f"{PEOPLE[who]}: нет inbox, applications, agreements",
           not {"inbox", "applications", "agreements"} & set(keys), keys)
    st, _ = call("GET", "/tg/inbox", who=ADM)
    ok("бэкенд согласований остался: /tg/inbox → 200", st == 200, st)


# ---------- 2. какие документы нужны ----------

def check_docs():
    print("2. GET /analytics/risk/docs")
    st, b = call("GET", "/analytics/risk/docs", params={"product_code": "0807"}, who=EMP)
    ok("0807 → 200", st == 200, (st, b))
    ok("0807: продукт, название и классы", b["product"]["code"] == "0807" and b["product"]["classes"] == ["8"],
       b.get("product"))
    names = [d["doc_name"] for d in b["required"]]
    ok("0807: своего чек-листа нет — основание «класс»", b["basis"] == "класс", b["basis"])
    ok("0807: общий пункт и пункт класса 8 среди обязательных",
       "Заявление-анкета на страхование" in names and "Фотографии объекта" in names, names)
    ok("0807: источники помечены (общий / класс)",
       {d["source"] for d in b["required"]} == {"общий", "класс"}, {d["source"] for d in b["required"]})
    ok("0807: необязательные с условием «когда»",
       b["optional"] and all("when" in d for d in b["optional"]), b["optional"][:2])
    ok("count = обязательные + необязательные", b["count"] == len(b["required"]) + len(b["optional"]))

    st, c = call("GET", "/analytics/risk/docs", params={"class_code": "3"}, who=EMP)
    names3 = [d["doc_name"] for d in c["required"]]
    ok("класс 3 → 200, продукта нет", st == 200 and c["product"] is None, (st, c.get("product")))
    ok("класс 3: техпаспорт и список водителей",
       "Технический паспорт и свидетельство о регистрации" in names3
       and "Список допущенных водителей: стаж и возраст" in names3, names3)
    ok("класс 3: пунктов класса 8 нет", "Фотографии объекта" not in names3)

    st, s = call("GET", "/analytics/risk/docs", params={"class_code": "3", "object_type": "Спецтехника — экскаватор"},
                 who=EMP)
    ok("класс 3 + спецтехника: добавлены пункты типа объекта",
       st == 200 and any(d["source"] == "тип объекта" for d in s["required"]), s.get("basis_text"))

    st, o = call("GET", "/analytics/risk/docs", params={"product_code": "1323"}, who=EMP)
    ok("1323 (ОСГОР): свой чек-лист — основание «продукт», ссылки на норму",
       st == 200 and o["basis"] == "продукт" and any(d.get("legal_ref") for d in o["required"]),
       (st, o.get("basis")))

    st, _ = call("GET", "/analytics/risk/docs", who=EMP)
    ok("без продукта и класса → 422", st == 422, st)
    st, _ = call("GET", "/analytics/risk/docs", params={"product_code": "9999999"}, who=EMP)
    ok("неизвестный продукт → 404", st == 404, st)
    st, _ = call("GET", "/analytics/risk/docs", params={"product_code": "0807"})
    ok("без входа → 401", st == 401, st)


# ---------- 3. загрузка договора ----------

def check_upload():
    print("3. POST /analytics/risk/document")
    st, d = upload(make_docx(CONTRACT_LINES), "Договор склада.docx", params={"class_code": "8"})
    ok("DOCX → 200", st == 200, (st, d))
    must = d.get("prefill", {}).get("must", {}) if st == 200 else {}
    ok("DOCX: страховая сумма 4 200 000 000", must.get("sum_insured", {}).get("value") == 4.2e9, must)
    ok("DOCX: стоимость 5 000 000 000", must.get("object_value", {}).get("value") == 5e9, must)
    ok("DOCX: регион — город Ташкент", must.get("region", {}).get("value") == "region:TOSHKENT SHAHRI",
       must.get("region"))
    ok("DOCX: тип объекта «Склад», конструкция reinforced, год 2012",
       must.get("object_type", {}).get("value") == "Склад"
       and must.get("construction", {}).get("value") == "reinforced"
       and must.get("year", {}).get("value") == 2012, must)
    ok("DOCX: всё помечено source = договор", must and all(v["source"] == "договор" for v in must.values()))
    ok("DOCX: вид, язык, статус", d.get("kind") and d.get("language") == "ru" and d.get("status"),
       (d.get("kind"), d.get("language"), d.get("status")))
    ok("DOCX: признан договором страхования", d.get("insurance_contract") is True)
    doc_docx = d.get("doc_id")

    # в базе нет ни текста, ни ИНН, ни страхователя
    with db.tx() as con:
        row = db.rows(con, "SELECT * FROM analysis_docs WHERE id=?", doc_docx)
    raw = _json.dumps(row, ensure_ascii=False)
    ok("в базе строка есть, срок хранения 24 часа", row and row[0]["expires_at"] > row[0]["created_at"])
    ok("в базе нет ИНН и страхователя", "301234567" not in raw and "Тестовый склад" not in raw)
    ok("файл лежит во временной папке", (analysis_docs.DIR / doc_docx).exists())

    st, p = upload(make_pdf([ln.replace("4 200 000 000 сум", "4,2 млрд сум") for ln in CONTRACT_LINES]),
                   "contract.pdf")
    pm = p.get("prefill", {}).get("must", {}) if st == 200 else {}
    ok("PDF → 200", st == 200, (st, p))
    ok("PDF: «4,2 млрд сум» → 4 200 000 000", pm.get("sum_insured", {}).get("value") == 4.2e9, pm)
    ok("PDF: стоимость 5 000 000 000", pm.get("object_value", {}).get("value") == 5e9, pm)

    st, s = upload(make_scan_pdf(), "scan.pdf")
    ok("скан без текста → 200, «нужно распознавание», поля пустые",
       st == 200 and s["status"] == "нужно распознавание" and s["fields"] == {} and s["prefill"]["must"] == {},
       (st, s))

    st, e = upload(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600, "old.doc")
    ok(".doc → 415 с подсказкой «сохраните как .docx»", st == 415 and "сохраните как .docx" in e["detail"],
       (st, e))
    st, e = upload(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600, "old.xls")
    ok(".xls → 415", st == 415 and ".docx" in e["detail"], (st, e))
    st, e = upload(b"\x89PNG\r\n\x1a\n" + b"\0" * 100, "photo.png")
    ok("картинка → 415", st == 415, st)
    st, e = upload(b"%PDF-" + b"0" * (analysis_docs.MAX_BYTES + 10), "big.pdf")
    ok("больше 15 МБ → 413", st == 413, st)

    st, g = call("GET", f"/analytics/risk/document/{doc_docx}", who=EMP)
    ok("владелец читает свой документ", st == 200 and g["doc_id"] == doc_docx, st)
    st, _ = call("GET", f"/analytics/risk/document/{doc_docx}", who=EMP2)
    ok("чужой doc_id → 404", st == 404, st)
    st, _ = call("GET", "/analytics/risk/document/../../etc", who=EMP)
    ok("мусор вместо id → 404", st == 404, st)
    return doc_docx, p.get("doc_id")


# ---------- 4. анализ с документами ----------

WAREHOUSE = {"class_code": "8", "product_code": "0807", "object_type": "Склад", "sum_insured": 4.2e9,
             "object_value": 5e9, "region": "region:TOSHKENT SHAHRI", "term_months": 12,
             "construction": "reinforced", "activity": "warehouse"}


def check_analyze(doc_docx, doc_pdf):
    print("4. POST /analytics/risk с doc_ids")
    st, r = call("POST", "/analytics/risk", {"must": WAREHOUSE, "optional": {}, "doc_ids": [doc_docx]}, who=EMP)
    ok("анализ с договором → 200", st == 200 and r.get("ok"), (st, str(r)[:300]))
    docs = r.get("documents") or {}
    ok("documents: received — вид договора", docs.get("received") == [r["documents"]["items"][0]["kind"]], docs)
    ok("documents: missing — пункты чек-листа 0807",
       "Фотографии объекта" in docs.get("missing", []) and "Заявление-анкета на страхование" in docs.get("missing", []),
       docs.get("missing"))
    ok("documents: договор страхования пунктов не закрывает, полнота документов 0%",
       docs.get("completeness_docs_pct") == 0 and docs.get("note"), docs)
    comp = r.get("completeness") or {}
    ok("completeness: docs_pct и docs_missing", comp.get("docs_pct") == 0 and comp.get("docs_missing") > 0, comp)
    ok("completeness: флаг «документы загружены» учтён", comp.get("optional_filled", 0) >= 1, comp)

    st0, r0 = call("POST", "/analytics/risk", {"must": WAREHOUSE, "optional": {}}, who=EMP)
    ok("без документов полнота ниже", st0 == 200 and r0["completeness"]["pct"] < comp.get("pct", 0),
       (r0.get("completeness", {}).get("pct"), comp.get("pct")))
    ok("без документов блок documents тоже есть, received пуст",
       r0.get("documents", {}).get("received") == [] and r0["documents"]["missing"])

    ai_on = llm.enabled()
    if ai_on:
        ok("ИИ подключён: ai_summary — текст", isinstance(r.get("ai_summary"), str) or r.get("ai_summary") is None)
    else:
        ok("ИИ не подключён: ai_summary = null, ai_status честный",
           r.get("ai_summary") is None and r.get("ai_status") == llm.AI_OFF, (r.get("ai_summary"), r.get("ai_status")))

    st, e = call("POST", "/analytics/risk", {"must": WAREHOUSE, "doc_ids": [doc_docx]}, who=EMP2)
    ok("чужой doc_id в анализе → 404", st == 404, (st, e))
    st, e = call("POST", "/analytics/risk", {"must": WAREHOUSE, "doc_ids": ["0" * 24]}, who=EMP)
    ok("несуществующий doc_id → 404", st == 404, st)
    st, e = call("POST", "/analytics/risk", {"must": WAREHOUSE, "doc_ids": [doc_docx] * 11}, who=EMP)
    ok("больше 10 документов → 422", st == 422, st)

    # просрочка: срок вышел — документа нет, файл удаляется очисткой
    with db.tx() as con:
        con.execute("UPDATE analysis_docs SET expires_at='2000-01-01T00:00:00' WHERE id=?", (doc_pdf,))
        n = analysis_docs.cleanup(con)
    ok("очистка удалила просроченный", n == 1 and not (analysis_docs.DIR / doc_pdf).exists(), n)
    st, _ = call("GET", f"/analytics/risk/document/{doc_pdf}", who=EMP)
    ok("просроченный → 404", st == 404, st)
    st, _ = call("DELETE", f"/analytics/risk/document/{doc_docx}", who=EMP)
    ok("владелец удаляет свой документ", st == 200 and not (analysis_docs.DIR / doc_docx).exists(), st)


def main():
    folder = Path(tempfile.mkdtemp(prefix="surveyor-analysis-"))
    original = analysis_docs.DIR
    analysis_docs.DIR = folder / "analysis"
    try:
        with temp_db("surveyor-analytics-flow.db"):
            setup()
            check_menu()
            check_docs()
            doc_docx, doc_pdf = check_upload()
            check_analyze(doc_docx, doc_pdf)
    finally:
        analysis_docs.DIR = original
        shutil.rmtree(folder, ignore_errors=True)
    print(f"\nИтого: {passed} ок, {failed} плохо")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
