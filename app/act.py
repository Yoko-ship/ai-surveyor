"""
Сюрвейерский акт предстрахового осмотра — лёгкая версия (ТЗ 2.0 от 29.09.2026).

Сотрудник загружает фото объекта и снимки документов, вводит четыре поля и получает акт из пяти разделов
с советами. Парсинга нет: снимки читает языковая модель как картинку (одним запросом), тариф считает
простое правило (app/act_engine.py), текст акта собирает сервер по шаблонам (app/act_texts.py) — цифры
в тексте всегда совпадают с расчётом. Модель нужна только для распознавания фото; без неё акт
формируется целиком (литературная связка разделов моделью убрана 29.09.2026 — решение и списки
всегда из шаблонов).

Дополнения 29.09.2026 (app/act_extras.py, считают существующие модули): сценарии PML/EML/MFL и лимит
удержания (risk_analytics), разбор DOCX/XLSX/PDF с текстом без модели (ingest, analysis_docs, docparse),
применение франшизы (franchise.what_if — множитель к ставке акта), рекомендации страхователю
(preventive_measures через reducers и docs/act_measures.json).

Адреса (гостям открыты, как /chat/*; лимит актов — app/guest.py через app/guard.py, лимит фото гостя
по числу файлов и общий лимит распознаваний на сервер — здесь, настройки limits):
  POST /act/photos            — фото и снимки документов (multipart: files[], lang, class_code, product_code);
                                JPG/PNG/PDF-сканы — модели; DOCX, XLSX и PDF с текстом — парсерам (prefill);
                                Content-Length проверяется до чтения тела (413/411)
                                разбор документа — в пределах limits.doc_* (строки, колонки, листы, знаки),
                                со сроком на файл и на запрос; одновременно не больше двух на сервер
  POST /act/make              — акт по четырём полям + необязательным + распознанному (в сеть не ходит)
  GET  /act/{id}              — акт (JSON), ?lang=ru|uz|en — тот же акт на другом языке
  GET  /act/{id}.docx, .pdf   — выгрузка
  POST /act/{id}/send         — отправить акт файлом в чат с ботом (Telegram), только владельцу
  GET  /act/settings          — пороги лёгкого движка; PUT /act/settings — только администратор
  GET  /act/market/links      — ссылки поиска на OLX, avtoelon.uz, uybor.uz, joymee.uz для браузера сотрудника
                                (сервер по ним не ходит: olx.uz закрыт CloudFront, обход защиты запрещён)
  POST /act/market/shots      — снимки экрана со списком объявлений (до 5 JPG/PNG) → модель читает их одним
                                запросом → объявления, курс, предварительная медиана (app/act_market.py);
                                в /act/make — optional.market, блок market_value и текст в разделе 3

Запрос филиала (30.09.2026, app/branch_request.py): бланк из 16 строк узнаётся и в файле с текстом (разбор
без модели), и на скане (модель, блок branch_request) — в ответе /act/photos блок branch_request и prefill;
в /act/make — optional.request (тариф, премия, франшиза, срок) и блок request_check: сверка с минимальной
ставкой продукта, ставкой и премией акта, франшизой и сроком; подраздел в разделе 4 и в Word/PDF.

Договор страхования (30.09.2026, app/contract_read.py) — тем же образцом: файл с текстом разбирается
правилами (если найдено меньше половины ключевых полей — текст после маскировки ПД дочитывает модель,
настройка contract.ai_assist), скан читает модель (блок contract). В /act/photos — блок contract и,
если есть и запрос филиала, cross_check; в /act/make — optional.contract и блок contract_check (та же
сверка, что с запросом, плюс график платежей, суммы по объектам и существенные условия ГК РУз, ст. 929),
в разделе 1 — номер и дата договора, в разделе 4 — «Сверка с договором» и «Запрос филиала и договор».

Хранение: фото — 24 часа (DATA_DIR/act/<сессия>/, таблица act_uploads), акт — 7 дней (таблица acts);
очистка — фоновым потоком раз в час и при каждой загрузке. Открыть акт может только тот, кто его создал
(гость — по cookie gid, пользователь — по id), администратор — любой.
Персональные данные: текст в модель уходит через llm.mask_pd (внутри llm.chat_raw), имена файлов в модель
не передаются, в ответе модели значения, похожие на ФИО и паспорт, отбрасываются (llm.has_pd).
В журнал (audit) пишутся только счётчики и коды — без значений, имён и содержимого фото.
"""
import json
import re
import secrets
import shutil
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pymupdf
from fastapi import APIRouter, Body, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from . import act_analytics as aa
from . import act_engine as ae
from . import act_extras as ax
from . import branch_request as br
from . import contract_read as cr
from . import act_market as am
from . import act_texts as tx
from . import auth, db, guest, i18n, llm
from .act_texts import money, pct, t

router = APIRouter()

DIR = db.DATA_DIR / "act"                 # файлы загрузок; тесты подменяют
CLAUSES_FILE = db.ROOT / "docs" / "act_clauses.json"
PHOTO_TTL_SEC = 24 * 3600
ACT_TTL_SEC = 7 * 24 * 3600
ORPHAN_SEC = 3600                         # папка без записи в базе старше часа — сирота, удаляется
MAX_FILES = 10
MAX_BYTES = 15 * 1024 * 1024
# тело multipart: файлы + заголовки частей и поля формы
MAX_BODY = MAX_FILES * MAX_BYTES + 1024 * 1024
AI_MAX_SIDE = 2048                        # длинная сторона снимка для модели: табличка ещё читается
# если вложения не укладываются в предел — пережимаем сильнее (сторона, качество JPEG)
AI_SHRINK_STEPS = ((AI_MAX_SIDE, 85), (1600, 75), (1280, 65))
MAX_SUM = 1e15                            # защита от опечатки в разрядах, а не норма
MAX_RECOGNIZED = 100
ADMIN = "админ"
SEND_FORMATS = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "pdf": "application/pdf"}
# подсказка приоритета до распознавания: такие имена скорее документ или табличка (имя нигде не хранится)
DOC_NAME_HINTS = ("doc", "scan", "sheet", "plate", "passport", "pasport", "скан", "док", "лист", "таблич",
                  "паспорт", "техпас", "hujjat")

FMT_MIME = {"jpg": "image/jpeg", "png": "image/png", "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
DOC_FMTS = ("docx", "xlsx")               # документы с текстом: разбираются парсерами, в модель не уходят
# значения документа, которые похожи на ПД по шаблону, но являются данными объекта
PD_KEEP = {"reg_no": "[ГОСНОМЕР]", "cadastre_no": "[КАДАСТР]"}
MODEL_SOURCES = ("photo", "plate", "document", "marking")
CONDITIONS = ("new", "good", "worn", "damaged")
CLASS_HINTS = ("special_machinery", "vehicle", "building", "equipment", "cargo", "other")
HINT_CLASSES = {"special_machinery": ["3"], "vehicle": ["3"], "building": ["8", "9"], "equipment": ["8", "9"],
                "cargo": ["7"], "other": [],
                # личное страхование по договору (app/contract_read.personal_hint): несчастные случаи, болезни
                "accident": ["1"], "health": ["2"]}
EMPTY_VALUES = {"", "null", "none", "n/a", "na", "-", "—", "unknown", "нет", "не видно", "неизвестно",
                "не указано", "yoʻq", "not visible"}
COMPANY_MARKERS = ("co.", "co,", "ltd", "llc", "inc", "gmbh", "group", "machinery", "corporation", "corp",
                   "industr", "heavy", "motor", "company", "завод", "компания", "ооо", "ао ", "холдинг")

SCHEMA_SQL = [
    """CREATE TABLE IF NOT EXISTS act_settings (
        id            INTEGER PRIMARY KEY,
        created_at    TEXT NOT NULL,
        created_by    TEXT,
        settings_json TEXT NOT NULL,
        calibrated    INTEGER NOT NULL DEFAULT 0,
        note          TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS act_uploads (
        id           TEXT PRIMARY KEY,
        owner_key    TEXT NOT NULL,
        user_id      INTEGER NOT NULL DEFAULT 0,
        files_json   TEXT NOT NULL DEFAULT '[]',
        result_json  TEXT NOT NULL DEFAULT '{}',
        created_at   TEXT NOT NULL,
        expires_at   TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS acts (
        id                TEXT PRIMARY KEY,
        owner_key         TEXT NOT NULL,
        user_id           INTEGER NOT NULL DEFAULT 0,
        lang              TEXT NOT NULL DEFAULT 'ru',
        tariff_version_id INTEGER,
        settings_id       INTEGER,
        act_json          TEXT NOT NULL,
        created_at        TEXT NOT NULL,
        expires_at        TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_act_uploads_expires ON act_uploads (expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_acts_expires ON acts (expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_acts_owner ON acts (owner_key, created_at)",
]


def ensure_tables(con) -> None:
    for sql in SCHEMA_SQL:
        con.execute(sql)


def _now() -> datetime:
    return datetime.now()


class _Window:
    """
    Скользящее окно в час (в памяти процесса, как app/guest.py): перезапуск обнуляет — это защита
    от злоупотреблений, а не учёт. take(key, n) засчитывает сразу n единиц (например, n фото).
    """

    def __init__(self, window_sec: int = 3600):
        self.window = window_sec
        self.lock = threading.Lock()
        self.hits = {}

    def take(self, key: str, n: int, limit: int, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        with self.lock:
            marks = [x for x in self.hits.get(key) or [] if now - x < self.window]
            if len(marks) + n > limit:
                self.hits[key] = marks
                retry = int(self.window - (now - marks[0])) + 1 if marks else self.window
                return {"ok": False, "count": len(marks), "limit": limit, "retry_after": max(retry, 1)}
            marks += [now] * n
            self.hits[key] = marks
            return {"ok": True, "count": len(marks), "limit": limit, "left": limit - len(marks),
                    "retry_after": 0}

    def reset(self) -> None:
        with self.lock:
            self.hits.clear()


GUEST_PHOTOS = _Window()                  # фото гостя в час (по числу файлов, а не запросов)
AI_CALLS = _Window()                      # обращения к модели за распознаванием — на весь сервер
SENDS = _Window()                         # отправки акта ботом на одного пользователя Telegram
# распознавание идёт в отдельном потоке: так общий срок ответа соблюдается, даже если сеть «тянет»
_AI_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="act-ai")
# раскрытых картинок в памяти одновременно не больше двух: до 50 Мп × 4 байта каждая
_DECODE = threading.BoundedSemaphore(2)


def reset_limits() -> None:
    """Сброс счётчиков — для тестов и ручного снятия блокировки."""
    for w in (GUEST_PHOTOS, AI_CALLS, SENDS):
        w.reset()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
#  Настройки и справочники
# --------------------------------------------------------------------------- #

def load_settings(con) -> dict:
    """Действующие настройки: последняя версия act_settings поверх ae.DEFAULT_SETTINGS."""
    try:
        r = con.execute("SELECT id, created_at, created_by, settings_json FROM act_settings "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    except Exception:
        r = None
    if not r:
        s = ae.merge_settings({})
        s["_source"] = {"id": None, "what": "по умолчанию (экспертно)"}
        return s
    s = ae.merge_settings(json.loads(r[3]))
    s["_source"] = {"id": r[0], "created_at": r[1], "created_by": r[2], "what": "правка администратора"}
    return s


_clauses_cache = {"mtime": None, "data": {}}


def clause_catalog() -> dict:
    """docs/act_clauses.json; файл перечитывается, только если изменился."""
    try:
        m = CLAUSES_FILE.stat().st_mtime
        if _clauses_cache["mtime"] != m:
            _clauses_cache["data"] = json.loads(CLAUSES_FILE.read_text(encoding="utf-8"))
            _clauses_cache["mtime"] = m
    except (OSError, ValueError) as e:
        print("акт: список оговорок не прочитан:", type(e).__name__)
        return {}
    return _clauses_cache["data"]


def insurer_name(settings: dict) -> Optional[str]:
    """Название страховщика — из настроек (INSURER_NAME или act_settings.insurer_name), в коде его нет."""
    name = (llm.get("INSURER_NAME", "") or "").strip() or str(settings.get("insurer_name") or "").strip()
    return name[:120] or None


def tariff_version(con, level: str) -> Optional[int]:
    today = date.today().isoformat()
    r = con.execute("SELECT id FROM tariff_versions WHERE level=? AND effective_from <= ? AND "
                    "(effective_to IS NULL OR effective_to >= ?) ORDER BY effective_from DESC, id DESC LIMIT 1",
                    (level, today, today)).fetchone()
    return r[0] if r else None


# --------------------------------------------------------------------------- #
#  Кто пришёл, ответы
# --------------------------------------------------------------------------- #

def _user(request: Request):
    u = request.scope.get("surveyor_user")
    if u is None:
        token = auth.request_token(request)
        if token:
            with db.tx() as con:
                u = auth.session_user(con, token)
    return u


def _who(user, owner: str) -> str:
    """Для журнала: пользователь по id, гость — коротким хэшем (ни gid, ни IP в журнал не пишем)."""
    if user and user.get("id"):
        return "u:%s" % user["id"]
    return "гость:" + guest.short(owner or "")


def _cookie_7d(gid: str) -> bytes:
    """cookie гостя на 7 дней: акт живёт неделю и должен открываться тем же гостем."""
    parts = [f"{guest.COOKIE}={gid}", "Path=/", f"Max-Age={ACT_TTL_SEC}", "HttpOnly", "SameSite=Lax"]
    if guest.cookie_secure():
        parts.append("Secure")
    return "; ".join(parts).encode("latin-1")


def _reply(request: Request, payload: dict, status: int = 200) -> JSONResponse:
    res = JSONResponse(status_code=status, content=payload)
    gid = guest.from_request(request)
    if gid:
        res.raw_headers.append((b"set-cookie", _cookie_7d(gid)))
    return res


def _fail(request: Request, message: str, status: int, **extra) -> JSONResponse:
    return _reply(request, {"ok": False, "detail": message, **extra}, status)


def _lang(request: Request, given=None) -> str:
    if given and i18n.normalize_lang(given):
        return i18n.normalize_lang(given)
    return i18n.pick_lang(None, request.cookies.get(i18n.COOKIE))


# --------------------------------------------------------------------------- #
#  Очистка
# --------------------------------------------------------------------------- #

def cleanup(con=None) -> int:
    """Удаляет просроченные загрузки (24 часа, вместе с файлами) и акты (7 дней)."""
    def _run(c):
        ensure_tables(c)
        now = _iso(_now())
        old = db.rows(c, "SELECT id FROM act_uploads WHERE expires_at <= ?", now)
        for r in old:
            shutil.rmtree(DIR / r["id"], ignore_errors=True)
            c.execute("DELETE FROM act_uploads WHERE id=?", (r["id"],))
        n = c.execute("DELETE FROM acts WHERE expires_at <= ?", (now,)).rowcount or 0
        return len(old) + n + _drop_orphans(c)
    if con is not None:
        return _run(con)
    with db.tx() as c:
        return _run(c)


def _drop_orphans(con) -> int:
    """Страховка: папки загрузок без записи в базе (запись упала) старше ORPHAN_SEC удаляются по времени изменения."""
    if not DIR.is_dir():
        return 0
    known = {r["id"] for r in db.rows(con, "SELECT id FROM act_uploads")}
    edge = time.time() - ORPHAN_SEC
    n = 0
    for p in DIR.iterdir():
        try:
            if p.is_dir() and p.name not in known and p.stat().st_mtime < edge:
                shutil.rmtree(p, ignore_errors=True)
                n += 1
        except OSError:
            continue
    return n


# --------------------------------------------------------------------------- #
#  Распознавание фото языковой моделью
# --------------------------------------------------------------------------- #

def _format_of(blob: bytes) -> Optional[str]:
    if blob[:4] == b"%PDF":
        return "pdf"
    if blob[:4] == b"PK\x03\x04":
        return ax.zip_format(blob)
    if blob[:3] == b"\xff\xd8\xff":
        return "jpg"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


# маркеры JPEG с размерами кадра: SOF0–SOF3, SOF5–SOF7, SOF9–SOF11, SOF13–SOF15 (C4, C8, CC — не кадр)
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _png_size(blob: bytes) -> Optional[tuple]:
    """Ширина и высота из заголовка IHDR (первый блок PNG) — без раскрытия картинки."""
    if len(blob) < 24 or blob[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", blob[16:24])
    return (w, h) if w and h else None


def _jpeg_size(blob: bytes) -> Optional[tuple]:
    """Ширина и высота из маркера SOF: идём по сегментам, данные кадра не читаем."""
    i, n = 2, len(blob)
    while i + 4 <= n:
        if blob[i] != 0xFF:
            return None
        m = blob[i + 1]
        if m == 0xFF:                       # заполнитель перед маркером
            i += 1
            continue
        if m in (0x01, 0xD8) or 0xD0 <= m <= 0xD7:
            i += 2                          # маркеры без длины
            continue
        if m in (0xD9, 0xDA):               # конец или начало данных, а кадра так и не было
            return None
        seg = struct.unpack(">H", blob[i + 2:i + 4])[0]
        if seg < 2:
            return None
        if m in _JPEG_SOF:
            if i + 9 > n:
                return None
            h, w = struct.unpack(">HH", blob[i + 5:i + 9])
            return (w, h) if w and h else None
        i += 2 + seg
    return None


def image_size(blob: bytes, fmt: str) -> Optional[tuple]:
    try:
        if fmt == "png":
            return _png_size(blob)
        if fmt == "jpg":
            return _jpeg_size(blob)
    except struct.error:
        return None
    return None


def check_content(blob: bytes, fmt: str, limits: dict, lang: str) -> Optional[str]:
    """
    Проверка до любого раскрытия: картинка — размеры из заголовка (не прочитали — отказ, больше предела
    мегапикселей — отказ); PDF — число страниц (на сервере PDF не растеризуется). None — файл годится.
    """
    if fmt in ("png", "jpg"):
        size = image_size(blob, fmt)
        if not size:
            return t("ph_dims_unknown", lang)
        mp = float(limits["max_image_mp"])
        if size[0] * size[1] > mp * 1_000_000:
            return t("ph_too_many_px", lang, w=size[0], h=size[1], mp=int(mp) if mp == int(mp) else mp)
        return None
    if fmt == "pdf":
        text_max = int(limits.get("pdf_text_max_pages") or limits["pdf_max_pages"])
        try:
            doc = pymupdf.open(stream=blob, filetype="pdf")
            try:
                if doc.needs_pass:
                    return t("ph_pdf_bad", lang)
                pages = doc.page_count
                # длинный PDF принимается, только если у него есть текстовый слой (договор): разбор без модели
                has_text = pages > int(limits["pdf_max_pages"]) and pages <= text_max and _pdf_has_text(doc)
            finally:
                doc.close()
        except Exception:
            return t("ph_pdf_bad", lang)
        if pages < 1:
            return t("ph_pdf_bad", lang)
        if pages > int(limits["pdf_max_pages"]) and not has_text:
            return t("ph_pdf_pages", lang, n=int(limits["pdf_max_pages"]) if pages <= text_max else text_max)
        return None
    return t("ph_format", lang)


def _pdf_has_text(doc, pages: int = 3) -> bool:
    """Есть ли текстовый слой на первых страницах (сканы в модель берутся не длиннее limits.pdf_max_pages)."""
    got = 0
    for k in range(min(pages, doc.page_count)):
        got += len(re.sub(r"\s+", "", doc[k].get_text("text") or ""))
        if got >= 20:
            return True
    return False


def _for_model(blob: bytes, fmt: str, side: int = AI_MAX_SIDE, quality: int = 85) -> tuple:
    """Снимок для модели: длинная сторона до side, JPEG. Не вышло — исходный файл.
    Размеры картинки уже проверены check_content — раскрытие здесь ограничено пределом мегапикселей."""
    if fmt == "pdf":
        return blob, FMT_MIME["pdf"]
    try:
        with _DECODE:
            pix = pymupdf.Pixmap(blob)
            if pix.alpha:
                pix = pymupdf.Pixmap(pix, 0)
            if pix.colorspace is None or pix.colorspace.n not in (1, 3):
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            while max(pix.width, pix.height) > side:
                pix.shrink(1)
            out = pix.tobytes("jpg", jpg_quality=quality)
            pix = None
        if out and (len(out) < len(blob) or fmt == "png"):
            return out, "image/jpeg"
    except Exception as e:                   # битая картинка — модель скажет сама, сервер не падает
        print("акт: снимок не пережат:", type(e).__name__)
    return blob, FMT_MIME[fmt]


SYSTEM_PROMPT = (
    "ты — сюрвейер страховой компании. по фотографиям объекта и снимкам документов ты записываешь, что на них "
    "видно. отвечай только объектом json строго по схеме, без пояснений и без markdown. "
    "не выдумывай: чего не видно или не читается, того нет — ставь null или не добавляй запись. "
    "не извлекай и не возвращай имена, фамилии, лица, паспортные данные, адреса и телефоны людей, "
    "а также номера посторонних машин и государственные номера. "
    "если одно и то же поле видно в нескольких источниках (табличка, документ, надпись на кузове или стреле, "
    "само фото) — верни каждое значение отдельной записью со своим source. "
    "значения с таблички и из документа переписывай в точности, со всеми знаками и единицами. "
    "названия организаций (с формой собственности: мчж, аж, атб, ооо, ао, банк, филиал) в запросе филиала "
    "и в договоре страхования переписывай как есть; если сторона договора — гражданин (фамилия и имя без "
    "формы собственности), её имя не пиши, только признак is_legal = false.")



# договор страхования: те же поля, что у разбора текста (app/contract_read.fields); госномер модель не читает
CONTRACT_SCHEMA = (
    '{"file": 1, "contract_no": "номер договора или null", "contract_date": "ГГГГ-ММ-ДД или null", '
    '"place": "место заключения или null", "product_name": "вид страхования или название продукта", '
    '"product_code": "код вида страхования или null", '
    '"insurer": {"is_legal": true, "name": "название организации или null"}, '
    '"policyholder": {"is_legal": true, "name": "…"}, "beneficiary": {"is_legal": true, "name": "…"}, '
    '"pledger": {"is_legal": true, "name": "…"}, "object": "описание объекта страхования как в документе", '
    '"class_hint": "building|equipment|vehicle|special_machinery|cargo|other или null", '
    '"address": "адрес объекта или null", "cadastre_no": "…", "brand": "…", "model": "…", "year": "…", '
    '"vin": "…", "serial_no": "…", "land_area": "…", "useful_area": "…", "total_area": "…", '
    '"construction": "…", "purpose": "назначение или деятельность", "year_built": "…", '
    '"object_value": "сумма цифрами как в документе", "sum_insured": "…", "currency": "UZS|USD|EUR|RUB или null", '
    '"items": [{"name": "часть объекта", "sum": "страховая сумма части"}], '
    '"tariff": "тариф как в документе, например 0,1 %", "premium": "…", "payment_mode": "single|installments|null", '
    '"payments": [{"date": "ГГГГ-ММ-ДД", "amount": "сумма"}], "term": "срок как в документе", '
    '"term_from": "ГГГГ-ММ-ДД", "term_to": "ГГГГ-ММ-ДД", "liability_from": "ГГГГ-ММ-ДД или null", '
    '"franchise": "франшиза как в документе или null", "franchise_type": "unconditional|conditional|null", '
    '"franchise_risk": "риск, к которому относится франшиза, или null", '
    '"covered_risks": ["короткие названия застрахованных рисков"], '
    '"exclusions": ["короткие названия исключений, не больше 20"], "territory": "территория страхования или null", '
    '"special_terms": ["особые условия и оговорки коротко"], '
    '"notice": "срок уведомления о страховом случае или null"}')
CONTRACT_HINT = (
    "договор страхования или полис (договор страхования, суғурта шартномаси, sugʻurta shartnomasi, полис, "
    "insurance contract, insurance policy; разделы «предмет договора», «страховая сумма», «страховая премия»): "
    "для такого снимка document_kind = contract и заполни contract — значения как в документе, суммы цифрами, "
    "даты в виде ГГГГ-ММ-ДД; чего в документе нет — null, не выдумывай; стороны — только организации, "
    "гражданин — is_legal = false без имени; имена, подписи и паспортные данные людей не пиши; "
    "если договора нет — contract = null.")
CONTRACT_TEXT_SYSTEM = (
    "ты — андеррайтер страховой компании. тебе дан текст договора страхования; персональные данные в нём "
    "заменены метками в квадратных скобках. извлеки условия договора. отвечай только объектом json строго по "
    "схеме, без пояснений и без markdown. не выдумывай: чего нет в тексте — null. стороны договора — только "
    "организации с формой собственности; гражданин — is_legal = false без имени. имена, подписи, паспортные "
    "данные, телефоны и адреса людей не возвращай, метки в квадратных скобках не переписывай.")

SCHEMA_HINT = (
    '{"files": [{"n": 1, "view": "front|back|left|right|plate|odometer|document|interior|facade|roof|'
    'electrical|fire_safety|general|installation|packaging|marking|transport|other", '
    '"document_kind": "строка или null"}], '
    '"object_kind": "truck_crane|crawler_crane|excavator|backhoe_loader|bulldozer|wheel_loader|forklift|'
    'telehandler|aerial_platform|concrete_pump|concrete_mixer|drilling_rig|road_machinery|tractor|combine|'
    'special_other|trailer|car|truck|electric_car|warehouse|shop|production|office|dwelling|hotel|equipment|'
    'cargo|other или null", '
    '"class_hint": "special_machinery|vehicle|building|equipment|cargo|other или null", '
    '"condition": "new|good|worn|damaged или null", '
    '"fields": [{"key": "ключ", "value": "значение", "source": "photo|plate|document|marking", '
    '"file": 1, "note": "строка или null"}], '
    '"damages": [{"what": "что повреждено", "where": "где", "file": 1}], '
    '"branch_request": null или {"file": 1, ' + ", ".join(
        f'"{c}": "строка как в документе или null"' for c in br.ROW_CODES if c not in br.PARTY_CODES) +
    ', "policyholder": {"is_legal": true, "name": "название организации или null"}, '
    '"beneficiary": {"is_legal": true, "name": "…"}, "pledger": {"is_legal": true, "name": "…"}, '
    '"term_from": "ГГГГ-ММ-ДД или null", "term_to": "ГГГГ-ММ-ДД или null", '
    '"object_description_translated": "описание объекта в переводе или null", '
    '"class_hint": "building|equipment|vehicle|special_machinery|cargo|other или null"}, '
    '"contract": null или ' + CONTRACT_SCHEMA + '}')

FIELD_HINTS = (
    "ключи fields: object_type (что за объект, словами), brand (марка), model (модель), "
    "manufacture_date (дата изготовления), year (год выпуска), serial_no (заводской или серийный номер, vin), "
    "manufacturer (изготовитель), engine_no (номер двигателя), engine_model (модель двигателя), "
    "engine_power (мощность с единицами), curb_mass (снаряжённая масса с единицами), "
    "payload (грузоподъёмность с единицами), dimensions (габариты), color (цвет), "
    "mileage (пробег или моточасы), location (место эксплуатации, что видно на фото: открытая площадка, "
    "стройка, порт, охраняемая территория, закрытое помещение). "
    "view: front — спереди, back — сзади, left/right — борта, plate — заводская табличка, "
    "odometer — счётчик пробега или моточасов, document — снимок документа (техпаспорт, паспорт самоходной "
    "машины, лист технических параметров, кадастровый документ). source: plate — заводская табличка, "
    "document — документ, marking — надпись или маркировка на кузове, стреле, двери, photo — сам вид объекта. "
    "запрос филиала — таблица из шестнадцати строк (суғурта тури, суғурта қилдирувчи, наф олувчи, гаровга "
    "қўювчи, суғурта объекти, суғурта қиймати, суғурта суммаси, франшиза, суғурта тарифи, суғурта мукофоти, "
    "суғурта муддати, стандарт шартлар, контрагент, шартнома миқдори, класс, қўшимча маълумот; бывает на "
    "латинице и по-русски): для такого снимка document_kind = branch_request и заполни branch_request — "
    "каждую строку перепиши как в документе, без перевода и без пересчёта, суммы вместе с суммой прописью, "
    "срок целиком (с какого и по какое число); product_code — код вида страхования; object — вся строка "
    "объекта (описание, площади, кадастровый номер); пустая строка бланка — null. term_from и term_to — те же "
    "даты срока в виде ГГГГ-ММ-ДД. object_description_translated — описание объекта в переводе.")

LANG_NAME = {"ru": "русском", "uz": "узбекском (латиница)", "en": "английском"}


def model_prompt(n: int, lang: str) -> str:
    return (f"приложено файлов: {n}, они пронумерованы по порядку от 1 до {n}. "
            f"для каждого файла укажи ракурс (view). {FIELD_HINTS} "
            f"описания (object_type, location, damages, note, document_kind) пиши на {LANG_NAME[lang]} языке "
            f"строчными буквами; марки, модели, номера и единицы — как написано на объекте. "
            f"видимые повреждения перечисли в damages; если повреждений не видно — пустой список. "
            f"перевод описания объекта из запроса филиала (object_description_translated) — на {LANG_NAME[lang]} "
            f"языке; если запроса филиала нет — branch_request = null. {CONTRACT_HINT} "
            f"схема ответа: {SCHEMA_HINT}")


def _placeholders(s: str) -> set:
    return set(re.findall(r"\[[А-ЯЁ\- ]+\]", s or ""))


# отчество или «Фамилия И. О.» — внутри названия компании это уже человек (правило общее с app/act_market.py)
_PATRONYMIC = am.PATRONYMIC


def _has_marker(text: str) -> bool:
    low = text.lower() + " "
    return any(m in low for m in COMPANY_MARKERS)


def _company_only(value: str) -> bool:
    """
    Вся строка — название компании: в каждой её части (через запятую, точку с запятой, скобки, тире)
    есть маркер компании, а похожего на ФИО (2–3 слова с заглавной) вне таких частей нет. В частях
    с маркером человеком считаем только «Фамилия И. О.» и слова с отчеством.
    """
    parts = [p.strip(" .") for p in re.split(r"[,;()\[\]/]|\s[-—–]\s|\n", value) if p.strip(" .")]
    if not parts or not any(_has_marker(p) for p in parts):
        return False
    for p in parts:
        if _has_marker(p):
            if llm.NAME_INITIALS.search(p) or _PATRONYMIC.search(p):
                return False
        elif llm.NAME_INITIALS.search(p) or "[ФИО]" in llm.mask_names(p) or _PATRONYMIC.search(p):
            return False
    return True


# поля запроса филиала: числа, даты и коды — не персональные данные; длинные строки бланка
NUMERIC_KEYS = ae.NUMBER_KEYS + ("premium", "tariff_pct", "contracts_count", "land_area", "useful_area", "total_area",
                                 "product_code", "term_from", "term_to")
LONG_KEYS = ("object_type", "additional_info", "contract_terms", "policyholder", "beneficiary", "pledger",
             "franchise")
PD_KEEP["object_type"] = "[КАДАСТР]"      # описание объекта из запроса филиала содержит кадастровый номер


def value_limit(key: str) -> int:
    return br.MAX_TEXT if key in LONG_KEYS else 120


def pd_like(key: str, value: str) -> bool:
    """
    Похоже ли значение на персональные данные (llm.has_pd). Два известных ложных срабатывания снимаются:
    изготовитель/марка, если вся строка — название компании (_company_only), и девятизначное число
    в номере или модели агрегата, если в значении есть буквы (чистые 9 цифр — возможный ИНН или телефон).
    """
    value = str(value or "")
    if key in ae.NUMBER_KEYS and re.fullmatch(r"[\d\s.,]+", value):
        return False                     # сумма или срок из документа — число, а не телефон или ИНН
    if key in NUMERIC_KEYS and re.fullmatch(r"[\d\s.,:\-м²m2]+", value):
        return False
    if key in br.PARTY_CODES:
        # сторона договора: только название юрлица (маркер МЧЖ/АЖ/банк …); гражданин — всегда ПД
        if not br.is_legal(value):
            return True
        found = _placeholders(llm.mask_pd(value)) - _placeholders(value)
        found.discard("[ФИО]")           # «Namunabank Sinov» — не ФИО, если есть маркер юрлица
        return bool(found)
    if not llm.has_pd(value):
        return False
    found = _placeholders(llm.mask_pd(value)) - _placeholders(value)
    if key in PD_KEEP:
        found.discard(PD_KEEP[key])      # госномер и кадастровый номер — данные объекта
    if key in ("manufacturer", "brand") and _company_only(value):
        found.discard("[ФИО]")
    if key in ("serial_no", "engine_no", "model", "engine_model") \
            and re.fullmatch(r"[\dA-Za-z\-/ ]+", value) and re.search(r"[A-Za-z]", value):
        found.discard("[ИНН]")
    return bool(found)


def _s(v, limit: int) -> Optional[str]:
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    if s.lower() in EMPTY_VALUES:
        return None
    return s[:limit] or None


def parse_model(text: str, n: int, inclusive: bool = True) -> Optional[dict]:
    """Ответ модели → проверенная структура. Не JSON или не та схема — None (честный отказ, не догадка).
    Блок branch_request (запрос филиала) разбирается сервером (app/branch_request.from_model); inclusive —
    считать ли в сроке оба крайних дня (настройка request_check.term_inclusive)."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or not any(k in data for k in ("files", "fields", "damages")):
        return None
    if not isinstance(data.get("files", []), list) or not isinstance(data.get("fields", []), list) \
            or not isinstance(data.get("damages", []), list):
        return None
    views = {}
    kinds = {}
    for f in data.get("files") or []:
        if not isinstance(f, dict):
            continue
        try:
            i = int(f.get("n"))
        except (TypeError, ValueError):
            continue
        if not 1 <= i <= n:
            continue
        v = str(f.get("view") or "other").strip().lower()
        views[i] = v if v in ae.VIEWS else "other"
        dk = _s(f.get("document_kind"), 60)
        if dk and dk.lower() in ("branch_request", "branch request"):
            dk = "branch_request"
        elif dk and dk.lower() in ("contract", "insurance contract", "insurance policy", "договор страхования"):
            dk = cr.KIND
        if dk and not pd_like("document_kind", dk):
            kinds[i] = dk
    dropped = 0
    fields, seen = [], set()
    for f in (data.get("fields") or [])[:120]:
        if not isinstance(f, dict):
            continue
        key = str(f.get("key") or "").strip()
        val = _s(f.get("value"), value_limit(key))
        if key not in ae.FIELD_KEYS or not val:
            continue
        if key == "reg_no" or pd_like(key, val):
            dropped += 1                 # госномер модели не принимаем: ей запрещено его читать
            continue
        src = str(f.get("source") or "photo").strip().lower()
        src = src if src in MODEL_SOURCES else "photo"
        try:
            fi = int(f.get("file"))
            fi = fi if 1 <= fi <= n else None
        except (TypeError, ValueError):
            fi = None
        note = _s(f.get("note"), 200)
        if note and llm.has_pd(note):
            note = None
        sig = (key, src, val.upper())
        if sig in seen:
            continue
        seen.add(sig)
        fields.append({"key": key, "value": val, "source": src, "file": fi, "note": note})
    damages = []
    for d in (data.get("damages") or [])[:20]:
        if isinstance(d, str):
            d = {"what": d}
        if not isinstance(d, dict):
            continue
        what = _s(d.get("what"), 160)
        if not what:
            continue
        where = _s(d.get("where"), 80)
        if llm.has_pd(what) or (where and llm.has_pd(where)):
            dropped += 1
            continue
        try:
            fi = int(d.get("file"))
            fi = fi if 1 <= fi <= n else None
        except (TypeError, ValueError):
            fi = None
        damages.append({"what": what, "where": where, "file": fi})
    kind = str(data.get("object_kind") or "").strip().lower()
    hint = str(data.get("class_hint") or "").strip().lower()
    cond = str(data.get("condition") or "").strip().lower()
    brq = br.from_model(data.get("branch_request"), inclusive)
    if brq:
        try:
            bf = int((data.get("branch_request") or {}).get("file"))
            bf = bf if 1 <= bf <= n else None
        except (TypeError, ValueError, AttributeError):
            bf = None
        brq["file"] = bf or next((i for i, k in sorted(kinds.items()) if k == "branch_request"), None)
    ctr = cr.from_model(data.get("contract"), inclusive)
    if ctr:
        try:
            cf = int((data.get("contract") or {}).get("file"))
            cf = cf if 1 <= cf <= n else None
        except (TypeError, ValueError, AttributeError):
            cf = None
        ctr["file"] = cf or next((i for i, k in sorted(kinds.items()) if k == cr.KIND), None)
    return {"views": views, "document_kinds": kinds, "fields": fields, "damages": damages, "branch_request": brq,
            "contract": ctr,
            "object_kind": kind if kind in tx.OBJECT_KINDS else None,
            "class_hint": hint if hint in CLASS_HINTS else None,
            "condition": cond if cond in CONDITIONS else None, "dropped": dropped}


def _priority(f: dict) -> int:
    """Кого показывать модели в первую очередь, если всё не помещается: документы и таблички."""
    if f["fmt"] == "pdf":
        return 0
    name = str(f.get("orig_name") or "").lower()
    return 1 if any(h in name for h in DOC_NAME_HINTS) else 2


def pick_for_model(saved: list, budget: int) -> tuple:
    """
    Вложения для модели не больше budget байт. Сначала обычное сжатие, не помещается — сильнее
    (AI_SHRINK_STEPS), всё равно не помещается — не все файлы: в приоритете PDF и снимки, похожие на
    документ или табличку, дальше — по порядку загрузки. Возвращает (payload в порядке загрузки,
    индексы saved отправленных, индексы saved не отправленных).
    """
    one = min(budget, llm.INLINE_MAX_ONE)
    prepared = {}
    for side, quality in AI_SHRINK_STEPS:
        prepared = {i: _for_model(f["blob"], f["fmt"], side, quality) for i, f in enumerate(saved)}
        if sum(len(d) for d, _ in prepared.values()) <= budget:
            break
    order = sorted(range(len(saved)), key=lambda i: (_priority(saved[i]), i))
    chosen, total = set(), 0
    for i in order:
        size = len(prepared[i][0])
        # пределы app/llm.py проверяем заранее: иначе модель увидит меньше файлов и нумерация съедет
        if size > one or total + size > budget or len(chosen) >= llm.INLINE_MAX_FILES:
            continue
        chosen.add(i)
        total += size
    sent = sorted(chosen)
    payload = [{"name": f"file {k + 1}", "mime": prepared[i][1], "data": prepared[i][0]}
               for k, i in enumerate(sent)]
    return payload, sent, [i for i in range(len(saved)) if i not in chosen]


def recognize(saved: list, lang: str, limits: Optional[dict] = None, inclusive: bool = True) -> dict:
    """
    Одно обращение к модели со всеми снимками. saved — [{"blob", "fmt"}] в порядке загрузки.
    Одна попытка, таймаут запроса limits.ai_timeout_sec, общий срок limits.ai_deadline_sec: не уложились —
    ok=False с честной причиной (фото остаются, акт формируется).
    Возвращает {"ok", "reason", "sent": [индексы saved], "not_sent": [...], ...поля parse_model}.
    """
    return ask_model(saved, lang, limits, "акт: распознавание фото", SYSTEM_PROMPT,
                     lambda n: model_prompt(n, lang), lambda text, n: parse_model(text, n, inclusive))


def ask_model(saved: list, lang: str, limits: Optional[dict], purpose: str, system: str, prompt_of, parse) -> dict:
    """
    Общий путь к модели для фото объекта и снимков объявлений: пределы вложений, общий лимит обращений
    на сервер (AI_CALLS), один запрос без повторов с таймаутом ai_timeout_sec, общий срок ai_deadline_sec,
    ответ проверяется parse(text, n) по строгой схеме (None — честный отказ).
    """
    lim = {**ae.DEFAULT_SETTINGS["limits"], **(limits or {})}
    t0 = time.monotonic()
    if not llm.enabled():
        return {"ok": False, "reason": t("ai_not_connected", lang), "sent": [], "not_sent": []}
    if not llm.supports_files():
        return {"ok": False, "reason": t("ai_no_files", lang), "sent": [], "not_sent": []}
    payload, sent, not_sent = pick_for_model(saved, int(float(lim["ai_max_mb"]) * 1024 * 1024))
    if not payload:
        return {"ok": False, "reason": t("ph_too_big", lang, mb=lim["ai_max_mb"]), "sent": [],
                "not_sent": not_sent}
    return _model_call(purpose, [{"role": "system", "content": system},
                                 {"role": "user", "content": prompt_of(len(payload))}], payload, lim, t0, lang,
                       lambda text: parse(text, len(payload)), sent, not_sent)


def _model_call(purpose: str, messages: list, files: Optional[list], lim: dict, t0: float, lang: str, parse,
                sent=(), not_sent=(), deadline: Optional[float] = None) -> dict:
    """Одно обращение к модели в отдельном потоке: общий лимит AI_CALLS, таймаут запроса ai_timeout_sec,
    общий срок deadline (по умолчанию ai_deadline_sec от t0), ответ — через parse(text) (None — отказ)."""
    sent, not_sent = list(sent), list(not_sent)
    if not AI_CALLS.take("server", 1, int(lim["ai_calls_per_hour"]))["ok"]:
        return {"ok": False, "reason": t("ai_busy", lang, n=int(lim["ai_calls_per_hour"])), "sent": [],
                "not_sent": []}
    deadline = float(lim["ai_deadline_sec"]) if deadline is None else deadline
    fut = _AI_POOL.submit(llm.chat_raw, purpose, messages, max_tokens=4096, temperature=0.1, files=files,
                          timeout=min(float(lim["ai_timeout_sec"]), max(1.0, deadline)), retries=0)
    try:
        res = fut.result(timeout=max(0.5, deadline - (time.monotonic() - t0)))
    except FutureTimeout:
        # поток сам завершится по таймауту запроса; его ответ уже никому не нужен
        return {"ok": False, "reason": t("ai_timeout", lang, sec=int(deadline)), "sent": sent,
                "not_sent": not_sent}
    except Exception as e:
        print("акт: распознавание упало:", type(e).__name__)
        return {"ok": False, "reason": t("ai_error", lang), "sent": sent, "not_sent": not_sent}
    if not res.get("text"):
        return {"ok": False, "reason": res.get("reason") or t("ph_bad_json", lang), "sent": sent,
                "not_sent": not_sent}
    parsed = parse(res["text"])
    if parsed is None:
        return {"ok": False, "reason": t("ph_bad_json", lang), "sent": sent, "not_sent": not_sent}
    return {"ok": True, "reason": None, "sent": sent, "not_sent": not_sent, **parsed}


# кусок текста договора в одном сообщении: llm.chat_raw обрезает сообщение до MAX_PROMPT_CHARS
CT_CHUNK = 11000


def contract_text_model(text: str, lang: str, limits: Optional[dict], max_chars: int, inclusive: bool,
                        deadline: Optional[float] = None) -> dict:
    """
    Текст договора (не файл) — в модель, когда разбор правилами нашёл меньше половины ключевых полей.
    Текст сокращается до max_chars (начало и строки у подписей суммы, премии, срока, объекта) и
    маскируется llm.mask_pd ДО отправки; ответ — по схеме CONTRACT_SCHEMA, поля — contract_read.fields.
    Срок и лимит обращений — общие с распознаванием фото (limits.ai_*, AI_CALLS).
    """
    lim = {**ae.DEFAULT_SETTINGS["limits"], **(limits or {})}
    t0 = time.monotonic()
    if not llm.enabled():
        return {"ok": False, "reason": t("ai_not_connected", lang)}
    part, cut = cr.excerpt(text, max_chars)
    masked = llm.mask_pd(part)
    chunks = [masked[k:k + CT_CHUNK] for k in range(0, len(masked), CT_CHUNK)] or [""]
    messages = [{"role": "system", "content": CONTRACT_TEXT_SYSTEM},
                {"role": "user", "content": 'схема ответа: {"contract": ' + CONTRACT_SCHEMA + "}"}]
    messages += [{"role": "user", "content": f"текст договора, часть {k + 1} из {len(chunks)}:\n{c}"}
                 for k, c in enumerate(chunks)]

    def parse(reply):
        m = re.search(r"\{.*\}", reply or "", re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except ValueError:
            return None
        raw = data.get("contract") if isinstance(data, dict) and isinstance(data.get("contract"), dict) else data
        got = cr.from_model(raw, inclusive, min_found=0)
        return {"contract": got} if got else None

    res = _model_call("акт: договор по тексту", messages, None, lim, t0, lang, parse, deadline=deadline)
    res["excerpt_cut"] = cut
    return res


# --------------------------------------------------------------------------- #
#  Распознанное: подписи и выбор значения
# --------------------------------------------------------------------------- #

def recognized_view(items: list, lang: str, file_ids: Optional[dict] = None, group: Optional[str] = None) -> list:
    """Распознанное для экрана: подпись поля, источник словами и пометка «проверьте»."""
    out = []
    for r in items:
        out.append({"key": r["key"], "label": tx.field_label(r["key"], lang, group), "value": r["value"],
                    "source": r["source"], "source_label": tx.label(tx.SOURCE_LABELS, r["source"], lang),
                    "note": r.get("note"), "file": (file_ids or {}).get(r.get("file"), r.get("file_id")),
                    "check": True, "check_label": t("check_mark", lang)})
    return out


def preferred(items: list, key: str) -> Optional[dict]:
    """Значение поля для раздела 1: документ → табличка → маркировка → ввод → фото."""
    cand = [r for r in items if r["key"] == key and r.get("value")]
    if key == "year":
        cand += [dict(r, value=str(ae.to_year(r["value"]))) for r in items
                 if r["key"] == "manufacture_date" and ae.to_year(r.get("value"))]
    if not cand:
        return None
    cand.sort(key=lambda r: ae.SOURCES.index(r["source"]) if r["source"] in ae.SOURCES else 9)
    return cand[0]


# --------------------------------------------------------------------------- #
#  POST /act/photos
# --------------------------------------------------------------------------- #

def _class_of(con, class_code: str, product_code: str) -> Optional[str]:
    if class_code:
        return class_code
    if product_code:
        r = con.execute("SELECT class_code FROM product_classes WHERE product_code=? ORDER BY part_no LIMIT 1",
                        (product_code,)).fetchone()
        return r[0] if r else None
    return None


class _BodyLimitRoute(APIRoute):
    """
    Размер тела проверяется по Content-Length ДО чтения: FastAPI разбирает multipart раньше, чем
    вызывает обработчик, поэтому проверка внутри обработчика опоздала бы. Нет Content-Length — 411.
    """
    max_body = MAX_BODY

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def limited(request: Request):
            raw = request.headers.get("content-length")
            lang = _lang(request)
            if raw is None:
                return _fail(request, t("ph_length_required", lang), 411)
            try:
                size = int(raw)
            except ValueError:
                return _fail(request, t("ph_length_required", lang), 400)
            if size > self.max_body:
                return _fail(request, t("ph_body_too_big", lang, mb=self.max_body // (1024 * 1024)), 413)
            return await handler(request)

        return limited


def _limit_reply(request: Request, message: str, res: dict) -> JSONResponse:
    """429 в том же виде, что у гостевого лимита app/guard.py."""
    out = _fail(request, message, 429, limit=res["limit"], window_hours=1,
                retry_after_sec=res["retry_after"], guest=True)
    out.headers["Retry-After"] = str(res["retry_after"])
    return out


def act_photos(request: Request, files: List[UploadFile] = File(...), lang: str = Form(""),
               class_code: str = Form(""), product_code: str = Form("")):
    """
    Фото объекта и снимки документов (до 10 файлов, до 15 МБ; JPG, PNG, PDF). Файлы хранятся 24 часа.
    Одним запросом уходят в языковую модель; ответ проверяется по схеме.
    Обычный def: FastAPI выполняет его в пуле потоков — распознавание (сеть до 25 с) и пережатие
    не останавливают сервер с одним процессом uvicorn.
    """
    user = _user(request)
    owner = guest.owner_of(request, user)
    lang = _lang(request, lang)
    if not owner:
        return _fail(request, "Не удалось опознать сессию — откройте приложение заново", 400)
    if len(files) > MAX_FILES:
        return _fail(request, t("ph_too_many", lang, n=MAX_FILES), 413)
    with db.tx() as con:
        ensure_tables(con)
        st = load_settings(con)
    limits = st["limits"]
    inclusive = bool(st["request_check"]["term_inclusive"])
    limits = dict(limits, _contract=st["contract"], _tolerance=float(st["request_check"]["premium_tolerance"]))
    if owner.startswith("g:"):
        n_max = int(limits["guest_photos_per_hour"])
        res = GUEST_PHOTOS.take(owner, len(files), n_max)
        if not res["ok"]:
            print("акт: лимит фото гостя", guest.short(owner), res["count"], "/", n_max, flush=True)
            return _limit_reply(request, t("ph_guest_limit", lang, n=n_max), res)
    class_code = str(class_code or "").strip()[:10]
    product_code = str(product_code or "").strip()[:10]
    sid = secrets.token_hex(12)
    folder = DIR / sid
    try:
        return _photos(request, user, owner, lang, files, class_code, product_code, limits, sid, folder, inclusive)
    except Exception:
        # запись в базу или распознавание упали — папка с фото не должна остаться сиротой
        shutil.rmtree(folder, ignore_errors=True)
        raise


router.add_api_route("/act/photos", act_photos, methods=["POST"], route_class_override=_BodyLimitRoute)


def _photos(request, user, owner, lang, files, class_code, product_code, limits, sid, folder, inclusive=True):
    saved, rejected = [], []
    for i, up in enumerate(files, start=1):
        orig = Path(up.filename or "").name
        name = llm.mask_pd(orig)[:120] or f"file {i}"        # только для ответа, в базу не пишется
        blob = up.file.read(MAX_BYTES + 1)
        if not blob:
            rejected.append({"index": i, "name": name, "error": t("ph_empty_file", lang)})
            continue
        if len(blob) > MAX_BYTES:
            rejected.append({"index": i, "name": name,
                             "error": t("ph_too_big", lang, mb=MAX_BYTES // (1024 * 1024))})
            continue
        fmt = _format_of(blob)
        info = {}
        if fmt in DOC_FMTS:
            code, info = ax.zip_check(blob, limits)
            err = t(code, lang) if code else None
        else:
            err = check_content(blob, fmt, limits, lang) if fmt else t("ph_format", lang)
        if err:
            rejected.append({"index": i, "name": name, "error": err})
            continue
        folder.mkdir(parents=True, exist_ok=True)
        fid = f"f{len(saved) + 1}"
        path = folder / f"{fid}.{fmt}"
        path.write_bytes(blob)
        pages = None
        if fmt == "pdf":
            with pymupdf.open(stream=blob, filetype="pdf") as pdoc:
                pages = pdoc.page_count
        saved.append({"id": fid, "index": i, "name": name, "orig_name": orig, "fmt": fmt, "mime": FMT_MIME[fmt],
                      "size": len(blob), "path": db.stored_path(path), "blob": blob, "full": path,
                      "macros": bool(info.get("macros")), "pages": pages})
    if not saved:
        shutil.rmtree(folder, ignore_errors=True)
        return _fail(request, t("ph_none", lang), 422, rejected=rejected, ai=False,
                     warning=t("warn_pd", lang))

    with db.tx() as con:
        ensure_tables(con)
        cls = _class_of(con, class_code, product_code)
    # документы с текстовым слоем (DOCX, XLSX, PDF с текстом) — парсерами, без модели; сканы — модели.
    # Пределы текста и сроки (limits.doc_*): файл — doc_parse_sec (PDF с текстом — doc_file_sec_pdf: длинный
    # договор), все документы запроса — doc_parse_total_sec; одновременно на сервере разбирается не больше
    # ax.PARSE_SLOTS документов.
    parsed, parse_errors = {}, []
    dl = ax.doc_limits(limits)
    t_docs = time.monotonic()
    for f in saved:
        if f["fmt"] not in DOC_FMTS and f["fmt"] != "pdf":
            continue
        left = float(dl["doc_parse_total_sec"]) - (time.monotonic() - t_docs)
        res = None
        if left <= 0:
            res = {"text_layer": False, "status": "timeout", "kind": None, "items": [], "prefill": {},
                   "notes": ["doc_timeout"]}
        elif not ax.parse_slot(left):
            res = {"text_layer": False, "status": "busy", "kind": None, "items": [], "prefill": {},
                   "notes": ["doc_busy"]}
        else:
            try:
                left = float(dl["doc_parse_total_sec"]) - (time.monotonic() - t_docs)
                with db.tx() as con:
                    per_file = float(dl["doc_file_sec_pdf"] if f["fmt"] == "pdf" else dl["doc_parse_sec"])
                    res = ax.parse_document_limited(con, f["full"], cls or "", dl,
                                                    min(per_file, max(left, 0.0)), inclusive)
            except Exception as e:       # ошибка разбора не роняет загрузку, но и не глотается
                parse_errors.append({"format": f["fmt"], "error": type(e).__name__})
                res = {"text_layer": False, "kind": None, "items": [], "prefill": {}, "notes": ["doc_unreadable"]}
            finally:
                ax.parse_slot_release()
        if res.get("status") in ("timeout", "busy"):
            parse_errors.append({"format": f["fmt"], "error": res["status"]})
        if f["fmt"] == "pdf" and not res["text_layer"] and not set(res["notes"]) & {
                "doc_unreadable", "doc_timeout", "doc_busy"}:
            if (f.get("pages") or 0) <= int(limits["pdf_max_pages"]):
                continue                 # скан без текста — его читает модель
            res["notes"].append("doc_scan_pages")   # текст есть не на первых страницах: длинный скан модели не отдаём
        if f["macros"]:
            res["notes"].append("doc_macros")
        parsed[f["id"]] = res
    model_files = [f for f in saved if f["id"] not in parsed]
    if model_files:
        rec = recognize(model_files, lang, limits, inclusive)
    else:
        rec = {"ok": False, "reason": None, "sent": [], "not_sent": []}
    sent = rec.get("sent") or []
    # номер файла в запросе к модели → id загруженного файла
    model_to_id = {k + 1: model_files[i]["id"] for k, i in enumerate(sent)}
    views = {model_to_id[k]: v for k, v in (rec.get("views") or {}).items() if k in model_to_id}
    doc_kinds = {model_to_id[k]: v for k, v in (rec.get("document_kinds") or {}).items() if k in model_to_id}
    fields = []
    for f in rec.get("fields") or []:
        fields.append({**f, "file_id": model_to_id.get(f.get("file"))})
    damages = [{"what": d["what"], "where": d.get("where"), "file": model_to_id.get(d.get("file"))}
               for d in rec.get("damages") or []]
    not_sent = [model_files[i]["index"] for i in rec.get("not_sent") or []]
    doc_kinds = {k: (tx.label(tx.DOC_KIND_LABELS, v, lang) if v in (br.KIND, cr.KIND) else v)
                 for k, v in doc_kinds.items()}

    # значения из разобранных документов: источник «документ», пометка «проверьте», ПД отбрасываются
    doc_fields, prefill, doc_notes, dropped_doc, doc_list = [], {}, [], 0, []
    for f in saved:
        res = parsed.get(f["id"])
        if res is None:
            continue
        n_before = len(doc_fields)
        if res.get("text_layer"):
            views[f["id"]] = "document"
            if res.get("kind"):
                doc_kinds[f["id"]] = tx.label(tx.DOC_KIND_LABELS, res["kind"], lang)
        for it in res.get("items") or []:
            if pd_like(it["key"], it["value"]):
                dropped_doc += 1
                continue
            if any(d["key"] == it["key"] and d["value"] == it["value"] for d in doc_fields):
                continue
            doc_fields.append({"key": it["key"], "value": it["value"], "source": "document", "file": None,
                               "file_id": f["id"], "note": t("doc_parsed_note", lang), "parsed": True})
        for k, v in (res.get("prefill") or {}).items():
            if k not in prefill:
                prefill[k] = {"value": v, "source": "document", "file": f["id"]}
        for c in res.get("notes") or []:
            if c not in doc_notes:
                doc_notes.append(c)
        doc_list.append({"file": f["id"], "index": f["index"], "kind": res.get("kind"),
                         "kind_label": tx.label(tx.DOC_KIND_LABELS, res["kind"], lang) if res.get("kind") else None,
                         "text_layer": bool(res.get("text_layer")), "values": len(doc_fields) - n_before,
                         "notes": [t(c, lang) for c in res.get("notes") or []]})
    # запрос филиала: из файла с текстом (разобран выше) или со скана (ответ модели); первый найденный
    brq, dropped_br = None, 0
    for f in saved:
        got = (parsed.get(f["id"]) or {}).get("branch_request")
        if got:
            brq = dict(got, source="document", file=f["id"])
            break
    if not brq and rec.get("branch_request"):
        mb = rec["branch_request"]
        fid = model_to_id.get(mb.get("file")) or (model_to_id.get(1) if len(model_to_id) == 1 else None)
        brq = dict(mb, source="photo", file=fid)
        # строки бланка со скана — в распознанное (источник «документ»), с той же проверкой на ПД
        for it in br.items(mb["fields"]):
            if pd_like(it["key"], it["value"]):
                dropped_br += 1
                continue
            if any(x["key"] == it["key"] and x["value"] == it["value"] for x in fields):
                continue
            fields.append({"key": it["key"], "value": it["value"], "source": "document", "file": mb.get("file"),
                           "note": None, "file_id": fid})
        for k, v in br.prefill(mb["fields"], br.region_in(mb["fields"])).items():
            prefill.setdefault(k, {"value": v, "source": "document", "file": fid})
    if brq:
        brq["fields"], n = br_clean(brq["fields"])
        dropped_br += n
    # договор страхования: из файла с текстом (разобран выше) или со скана (ответ модели); первый найденный
    ctr, texts = None, {}
    for f in saved:
        res = parsed.get(f["id"]) or {}
        if res.get("_text") is not None:
            texts[f["id"]] = res.pop("_text")        # текст договора в базу не пишется: только для модели
        if not ctr and res.get("contract"):
            ctr = dict(res["contract"], source="document", file=f["id"], pages=f.get("pages"))
    if not ctr and rec.get("contract"):
        mc = rec["contract"]
        fid = model_to_id.get(mc.get("file")) or (model_to_id.get(1) if len(model_to_id) == 1 else None)
        ctr = dict(mc, source="photo", file=fid, pages=None, truncated=False)
        # условия договора со скана — в распознанное (источник «документ»), с той же проверкой на ПД
        for it in cr.items(mc["fields"]):
            if pd_like(it["key"], it["value"]):
                dropped_br += 1
                continue
            if any(x["key"] == it["key"] and x["value"] == it["value"] for x in fields):
                continue
            fields.append({"key": it["key"], "value": it["value"], "source": "document", "file": mc.get("file"),
                           "note": None, "file_id": fid})
        for k, v in cr.prefill(mc["fields"]).items():
            prefill.setdefault(k, {"value": v, "source": "document", "file": fid})
    ct_ai = {"asked": False, "ok": None, "reason": None}
    if ctr and ctr["source"] == "document":
        ctr["fields"], n = ct_clean(ctr["fields"])
        dropped_br += n
        opts = limits.get("_contract") or ae.DEFAULT_SETTINGS["contract"]
        text = texts.get(ctr["file"])
        if opts.get("ai_assist") and text and not ctr["fields"].get("is_template") and cr.need_assist(ctr["fields"]) \
                and llm.enabled():
            left = float(limits["ai_deadline_sec"]) - (time.monotonic() - t_docs)
            ct_ai["asked"] = True
            got = contract_text_model(text, lang, limits, int(opts.get("ai_max_chars") or 30000), inclusive,
                                      deadline=max(5.0, left))
            ct_ai.update(ok=bool(got.get("ok")), reason=got.get("reason"))
            if got.get("ok"):
                filled = cr.merge_missing(ctr["fields"], got["contract"]["fields"])
                ctr["fields"], n = ct_clean(ctr["fields"])
                dropped_br += n
                filled = [k for k in filled if ctr["fields"].get(k) not in (None, "", [], {})]
                ctr["field_sources"] = {k: "document_ai" for k in filled}
                if filled:
                    ctr["source"] = "document_ai"
                    have = {(d["key"], d["value"]) for d in doc_fields}
                    for it in cr.items(ctr["fields"]):
                        if cr.item_field(it["key"]) not in filled or (it["key"], it["value"]) in have \
                                or pd_like(it["key"], it["value"]):
                            continue
                        doc_fields.append({"key": it["key"], "value": it["value"], "source": "document_ai",
                                           "file": None, "file_id": ctr["file"], "note": t("ct_ai_note", lang),
                                           "parsed": True})
                    for k, v in cr.prefill(ctr["fields"]).items():
                        if cr.item_field(k) in filled:
                            prefill.setdefault(k, {"value": v, "source": "document_ai", "file": ctr["file"]})
    elif ctr:
        ctr["fields"], n = ct_clean(ctr["fields"])
        dropped_br += n
    if ctr:
        ctr["found"], ctr["missing"] = cr.found_missing(ctr["fields"])
        ctr["essentials"] = cr.essentials(ctr["fields"])
        ctr["ai"] = ct_ai
    # бланк договора (пустые поля) с запросом не сверяется: сверять нечего — как с заявлением
    cross = cr.cross_check(brq["fields"], ctr["fields"], float(limits.get("_tolerance") or 1000)) \
        if brq and ctr and not ctr["fields"].get("is_template") else None
    if "region" in prefill:
        prefill["region"]["code"] = region_code(prefill["region"]["value"])
    all_fields = fields + doc_fields
    parsed_ok = sum(1 for r in parsed.values() if r.get("text_layer"))

    with db.tx() as con:
        ensure_tables(con)
        cleanup(con)
        kind = rec.get("object_kind")
        hint = rec.get("class_hint")
        if brq:
            # скан бланка модель видит как «документ»; вид объекта — из строки «объект страхования»
            bf = brq["fields"]
            if (not kind or kind == "other") and bf.get("object_kind"):
                kind = bf["object_kind"]
            if (not hint or hint == "other") and bf.get("class_hint"):
                hint = bf["class_hint"]
        if ctr and (not hint or hint == "other") and ctr["fields"].get("class_hint") in cr.PERSON_HINTS:
            hint = ctr["fields"]["class_hint"]    # договор личного страхования: подсказка класса 1 или 2
        kind_text = tx.OBJECT_KINDS[kind][0] if kind and tx.OBJECT_KINDS[kind][0] else ""
        group = ae.object_group(cls, kind_text, hint or "")
        seen = sorted(set(views.values()))
        missing = ae.missing_views(group, seen) if rec.get("ok") else ae.required_views(group)
        stored = {"ai": bool(rec.get("ok")), "reason": rec.get("reason"), "views": views,
                  "document_kinds": doc_kinds, "fields": all_fields, "damages": damages,
                  "object_kind": kind, "class_hint": hint, "condition": rec.get("condition"),
                  "files": len(saved), "photo_files": len(model_files), "parsed_docs": parsed_ok,
                  "prefill": prefill, "doc_notes": doc_notes, "not_sent": not_sent, "lang": lang,
                  "branch_request": brq, "contract": ctr}
        now = _now()
        # в базе о файле — только порядковый номер, формат, размер и путь: имени файла нет
        keep = ("id", "index", "fmt", "mime", "size", "path")
        con.execute("INSERT INTO act_uploads (id, owner_key, user_id, files_json, result_json, created_at, "
                    "expires_at) VALUES (?,?,?,?,?,?,?)",
                    (sid, owner, (user or {}).get("id") or 0,
                     json.dumps([{k: f[k] for k in keep} for f in saved], ensure_ascii=False),
                     json.dumps(stored, ensure_ascii=False), _iso(now),
                     _iso(now + timedelta(seconds=PHOTO_TTL_SEC))))
        view_count = {}
        for v in views.values():
            view_count[v] = view_count.get(v, 0) + 1
        # в журнал — только счётчики: ни имён файлов, ни распознанных значений
        db.audit(con, _who(user, owner), "акт: фото загружены", f"act_upload:{sid}",
                 {"files": len(saved), "rejected": len(rejected), "ai": bool(rec.get("ok")),
                  "fields": len(fields), "damages": len(damages), "views": view_count,
                  "not_sent": len(not_sent), "dropped_pd": (rec.get("dropped") or 0) + dropped_doc + dropped_br,
                  "parsed_docs": parsed_ok, "doc_fields": len(doc_fields),
                  # запрос филиала — только признак и число строк: ни названий сторон, ни сумм
                  "branch_request": bool(brq), "branch_rows": (brq or {}).get("rows_found") or 0,
                  # договор — только признак, источник и счётчики: ни сторон, ни сумм, ни номера
                  "contract": bool(ctr), "contract_source": (ctr or {}).get("source"),
                  "contract_found": len((ctr or {}).get("found") or []),
                  "contract_ai": len((ctr or {}).get("field_sources") or {}),
                  "cross_differs": (cross or {}).get("differs", 0)})
        for e in parse_errors:
            db.audit(con, _who(user, owner), "акт: документ не разобран", f"act_upload:{sid}", e)
    notes = []
    if model_files:
        if rec.get("ok"):
            message = t("ph_ok", lang, n=len(fields)) if fields else t("ph_empty", lang)
        else:
            message = t("ph_ai_off", lang, reason=rec.get("reason") or t("ai_not_connected", lang))
        if parsed:
            notes.append(t("ph_docs_ok", lang, n=parsed_ok, k=len(doc_fields)))
    else:
        message = t("ph_docs_ok", lang, n=parsed_ok, k=len(doc_fields))
    if rec.get("ok") and not_sent:
        notes.append(t("ph_not_sent", lang, files=", ".join(str(n) for n in not_sent), mb=limits["ai_max_mb"]))
    notes += [t(c, lang) for c in doc_notes]
    if ct_ai["asked"] and not ct_ai["ok"]:
        notes.append(t("ct_ai_failed", lang, reason=ct_ai["reason"] or t("ai_error", lang)))
    model_ids = {f["id"] for f in model_files}
    return _reply(request, {
        "ok": True, "session": sid, "lang": lang,
        "files": [{"id": f["id"], "index": f["index"], "name": f["name"], "view": views.get(f["id"]),
                   "view_label": tx.label(tx.VIEW_LABELS, views[f["id"]], lang) if f["id"] in views else None,
                   "document_kind": doc_kinds.get(f["id"]),
                   "read_by_ai": bool(rec.get("ok")) and f["id"] in model_ids and f["index"] not in not_sent,
                   "parsed": bool((parsed.get(f["id"]) or {}).get("text_layer")),
                   "format": f["fmt"]} for f in saved],
        "rejected": rejected,
        "recognized": recognized_view(all_fields, lang, group=group),
        "damages": damages,
        "object_kind": ({"code": kind, "label": tx.label({k: v[1] for k, v in tx.OBJECT_KINDS.items()}, kind, lang)}
                        if kind else None),
        "class_hint": hint,
        "suggest_classes": HINT_CLASSES.get(hint or "", []),
        "condition": rec.get("condition"),
        "group": group,
        "required_views": [{"code": v, "label": tx.label(tx.VIEW_LABELS, v, lang)} for v in ae.required_views(group)],
        "missing_views": [{"code": v, "label": tx.label(tx.VIEW_LABELS, v, lang)} for v in missing],
        "ai": bool(rec.get("ok")),
        "message": message,
        "notes": notes,
        "not_sent": not_sent,
        "documents": doc_list,
        "prefill": prefill_view(prefill, lang) if prefill else None,
        "branch_request": branch_view(brq, lang),
        "contract": contract_view(ctr, lang),
        "cross_check": cross_view(cross, lang),
        "warning": t("warn_pd", lang),
        "expires_in_hours": PHOTO_TTL_SEC // 3600,
    })


# текстовые поля бланка → ключ, по правилам которого они проверяются на ПД (pd_like)
BR_TEXT_KEYS = {"object_description": "object_type", "object_description_translated": "object_type",
                "additional_info": "additional_info", "contract_terms": "contract_terms",
                "osgor_class": "additional_info", "counterparty": "policyholder", "term_text": "additional_info"}


def br_clean(f: dict) -> tuple:
    """Поля бланка без значений, похожих на данные людей (как у распознанного): (поля, сколько убрано)."""
    f = dict(f)
    n = 0
    for key, rule in BR_TEXT_KEYS.items():
        if f.get(key) and pd_like(rule, f[key]):
            f[key] = None
            n += 1
    fr = f.get("franchise")
    if fr and fr.get("text") and pd_like("franchise", fr["text"]):
        f["franchise"] = dict(fr, text=None)
        n += 1
    for code in br.PARTY_CODES:
        p = f.get(code) or {}
        if p.get("name") and pd_like(code, p["name"]):
            f[code] = {"kind": "individual", "name": None}
            n += 1
    return f, n


def branch_view(brq: Optional[dict], lang: str) -> Optional[dict]:
    """Блок «запрос филиала» для экрана: 16 строк бланка (найдена, заполнена), разобранные поля и готовый
    optional.request для /act/make. Стороны: название — только у юрлица, у гражданина — kind = individual."""
    if not brq:
        return None
    f = brq["fields"]
    notes = []
    for code in br.PARTY_CODES:
        if (f.get(code) or {}).get("kind") == "individual":
            notes.append(t("br_individual", lang, role=tx.label(tx.BR_ROW_LABELS, code, lang).lower()))
    if f.get("term_error"):
        notes.append(t("br_term_unread", lang))
    if f.get("tariff_pct") is None:
        notes.append(t("br_no_tariff", lang))
    if f.get("amount_errors"):
        notes.append(t("br_negative", lang, what=", ".join(tx.label(tx.BR_ROW_LABELS, k, lang).lower()
                                                           for k in f["amount_errors"])))
    return {"detected": True, "kind_label": tx.label(tx.DOC_KIND_LABELS, br.KIND, lang),
            "source": brq.get("source"), "file": brq.get("file"),
            "rows_found": brq.get("rows_found"), "rows_total": len(br.ROW_CODES),
            "rows": [dict(r, label=tx.label(tx.BR_ROW_LABELS, r["code"], lang)) for r in brq.get("rows") or []],
            "fields": f, "request": br.request_of(f, brq.get("source") or "document"),
            "notes": notes, "check_label": t("prefill_check", lang)}


# текстовые поля договора → ключ, по правилам которого они проверяются на ПД (pd_like)
CT_TEXT_KEYS = {"object_description": "object_type", "address": "additional_info", "construction": "construction",
                "purpose": "additional_info", "territory": "additional_info", "product_name": "additional_info",
                "place": "additional_info", "notice": "additional_info", "term_text": "additional_info",
                "contract_no": "additional_info", "insured_event": "additional_info",
                "cover_period": "additional_info", "payment_text": "additional_info",
                "brand": "brand", "model": "model", "vin": "serial_no",
                "serial_no": "serial_no", "engine_no": "engine_no", "cadastre_no": "cadastre_no"}


def ct_clean(f: dict) -> tuple:
    """Поля договора без значений, похожих на данные людей, и без меток маскировки «[ФИО]»: (поля, сколько убрано)."""
    f = dict(f)
    n = 0

    def bad(rule, v):
        return bool(_placeholders(str(v))) or pd_like(rule, str(v))

    for key, rule in CT_TEXT_KEYS.items():
        if f.get(key) and bad(rule, f[key]):
            f[key] = None
            n += 1
    fr = f.get("franchise")
    if fr:
        for k in ("text", "risk"):
            if fr.get(k) and bad("franchise", fr[k]):
                fr = dict(fr, **{k: None})
                n += 1
        f["franchise"] = fr
    for code in cr.PARTY_CODES:
        p = f.get(code) or {}
        if p.get("name") and (bad("policyholder", p["name"]) or not br.is_legal(p["name"])):
            f[code] = {"kind": "individual", "name": None}
            n += 1
    for key in ("covered_risks", "exclusions"):
        keep = []
        for x in f.get(key) or []:
            if x.get("text") and bad("additional_info", x["text"]):
                n += 1
                continue
            keep.append(x)
        f[key] = keep
    terms = [x for x in f.get("special_terms") or [] if not bad("additional_info", x)]
    n += len(f.get("special_terms") or []) - len(terms)
    f["special_terms"] = terms
    items = [x for x in f.get("items") or [] if not bad("object_type", x.get("name"))]
    n += len(f.get("items") or []) - len(items)
    f["items"] = items
    sch = f.get("schedule")
    if sch:
        rows = []
        for x in sch.get("items") or []:
            if x.get("profession") and bad("additional_info", x["profession"]):
                x = {k: v for k, v in x.items() if k != "profession"}
                n += 1
            rows.append(x)
        f["schedule"] = dict(sch, items=rows)
    f["has_beneficiary"] = (f.get("beneficiary") or {}).get("kind") is not None
    f["has_pledger"] = (f.get("pledger") or {}).get("kind") is not None
    return f, n


# служебные поля разбора: в перечень «прочитано моделью» не входят
_AI_SKIP = ("class_hint", "object_kind", "term_text", "term_inclusive", "term_error", "has_beneficiary",
            "has_pledger", "items_total", "liability_from")


def _ai_field_labels(keys, lang: str) -> list:
    """Поля, дочитанные моделью, — подписями на языке ответа (срок с, по и дни — одной подписью «срок»)."""
    out = []
    for k in keys:
        if k in _AI_SKIP:
            continue
        code = "term" if k in ("term_from", "term_to", "term_days") else k
        lab = None
        for group in (tx.EDIT_LABELS, tx.CT_FIELD_LABELS, tx.FIELD_LABELS):
            if code in group:
                lab = tx.label(group, code, lang)
                break
        if lab and lab.lower() not in out:
            out.append(lab.lower())
    return out


def _labels(group: dict, codes, lang: str) -> list:
    return [{"code": c, "label": tx.label(group, c, lang)} for c in codes]


def _risk_view(xs: list, group: dict, lang: str) -> list:
    return [{"code": x["code"], "label": x.get("text") if x["code"] == "other" else tx.label(group, x["code"], lang)}
            for x in xs or []]


def contract_view(ctr: Optional[dict], lang: str) -> Optional[dict]:
    """Блок «договор страхования» для экрана: поля договора, какие ключевые поля найдены и каких нет,
    существенные условия (ГК РУз, ст. 929), заметки и готовый optional.contract для /act/make.
    Стороны: название — только у юрлица, у гражданина — kind = individual."""
    if not ctr:
        return None
    f = ctr["fields"]
    if f.get("is_template"):
        return _template_view(ctr, lang)
    notes = []
    for code in cr.PARTY_CODES:
        if (f.get(code) or {}).get("kind") == "individual":
            notes.append(t("br_individual", lang, role=tx.label(tx.FIELD_LABELS, code, lang).lower()))
    if ctr.get("truncated"):
        notes.append(t("ct_truncated", lang))
    if not f.get("term_days"):
        notes.append(t("ct_term_unread", lang))
    if f.get("tariff_pct") is None:
        notes.append(t("ct_no_tariff", lang))
    if f.get("currency") not in (None, "UZS"):
        notes.append(t("ct_currency", lang, cur=f["currency"]))
    missing = ctr.get("missing") or []
    if missing:
        notes.append(t("ct_missing", lang, what=", ".join(tx.label(tx.CT_FIELD_LABELS, k, lang).lower()
                                                           for k in missing)))
    ess = ctr.get("essentials") or cr.essentials(f)
    lack = [e["code"] for e in ess if not e["present"]]
    if lack:
        notes.append(t("ct_missing_essential", lang, what=", ".join(tx.label(tx.CT_ESSENTIAL_LABELS, c, lang)
                                                                     for c in lack)))
    srcs = ctr.get("field_sources") or {}
    ai_labels = _ai_field_labels(srcs, lang)
    if ai_labels:
        notes.append(t("ct_ai_filled", lang, what=", ".join(ai_labels)))
    return {"detected": True, "kind_label": tx.label(tx.DOC_KIND_LABELS, cr.KIND, lang),
            "source": ctr.get("source"), "source_label": tx.label(tx.CT_SOURCE_LABELS, ctr.get("source"), lang),
            "file": ctr.get("file"), "pages": ctr.get("pages"), "truncated": bool(ctr.get("truncated")),
            "fields": f, "field_sources": srcs, "ai_fields": sorted(srcs),
            "found": _labels(tx.CT_FIELD_LABELS, ctr.get("found") or [], lang),
            "missing": _labels(tx.CT_FIELD_LABELS, missing, lang),
            "essentials": [{"code": e["code"], "label": tx.label(tx.CT_ESSENTIAL_LABELS, e["code"], lang),
                            "present": e["present"]} for e in ess],
            "legal_ref": tx.label(tx.LEGAL_REFS, ae.ESSENTIAL_REF, lang),
            "covered_risks": _risk_view(f.get("covered_risks"), tx.RISK_LABELS, lang),
            "exclusions": _risk_view(f.get("exclusions"), tx.EXCLUSION_LABELS, lang),
            "payment_mode_label": tx.label(tx.PAYMENT_MODE_LABELS, f["payment_mode"], lang)
            if f.get("payment_mode") else None,
            "request": cr.request_of(f, ctr.get("source") or "document"),
            "is_template": False, "template_hint": bool(f.get("template_hint")),
            "blank": _labels(tx.CT_BLANK_LABELS, f.get("blank") or [], lang), "blank_label": t("ct_blank", lang),
            "schedule": _schedule_view(f.get("schedule"), lang),
            "notes": notes, "check_label": t("prefill_check", lang)}


def _schedule_view(sch: Optional[dict], lang: str) -> Optional[dict]:
    """Таблица застрахованных по профессиям (приложение к договору личного страхования): колонки подписями."""
    if not sch:
        return None
    return {"columns": _labels(tx.CT_SCHED_LABELS, sch.get("columns") or [], lang), "items": sch.get("items") or [],
            "blank": bool(sch.get("blank"))}


def _template_view(ctr: dict, lang: str) -> dict:
    """
    Бланк договора (поля — подчёркивания): что в нём есть (вид страхования, страховщик, страховой случай,
    исключения, порядок оплаты, таблица приложения) и какие поля не заполнены. Существенные условия
    (ГК ст. 929) и сверка с расчётом не выполняются — они проверяются по заполненному договору; готового
    optional.contract нет (request = None).
    """
    f = ctr["fields"]
    blank = f.get("blank") or []
    what = ", ".join(tx.label(tx.CT_BLANK_LABELS, k, lang).lower() for k in blank)
    notes = [t("ct_template", lang, what=what)]
    if f.get("template_hint"):
        notes.append(t("ct_template_form", lang))
    if ctr.get("truncated"):
        notes.append(t("ct_truncated", lang))
    return {"detected": True, "kind_label": tx.label(tx.DOC_KIND_LABELS, cr.KIND, lang),
            "is_template": True, "template_hint": bool(f.get("template_hint")),
            "source": ctr.get("source"), "source_label": tx.label(tx.CT_SOURCE_LABELS, ctr.get("source"), lang),
            "file": ctr.get("file"), "pages": ctr.get("pages"), "truncated": bool(ctr.get("truncated")),
            "fields": f, "field_sources": {}, "ai_fields": [],
            "found": _labels(tx.CT_FIELD_LABELS, ctr.get("found") or [], lang),
            "missing": _labels(tx.CT_FIELD_LABELS, ctr.get("missing") or [], lang),
            "blank": _labels(tx.CT_BLANK_LABELS, blank, lang), "blank_label": t("ct_blank", lang),
            "essentials": [], "legal_ref": tx.label(tx.LEGAL_REFS, ae.ESSENTIAL_REF, lang),
            "covered_risks": _risk_view(f.get("covered_risks"), tx.RISK_LABELS, lang),
            "exclusions": _risk_view(f.get("exclusions"), tx.EXCLUSION_LABELS, lang),
            "payment_mode_label": tx.label(tx.PAYMENT_MODE_LABELS, f["payment_mode"], lang)
            if f.get("payment_mode") else None,
            "schedule": _schedule_view(f.get("schedule"), lang),
            "request": None, "notes": notes, "check_label": t("prefill_check", lang)}


def _x_value(code: str, v, lang: str, by: Optional[str] = None) -> str:
    NA = t("na", lang)
    if v in (None, "", []):
        return NA
    if code in ("sum_insured", "object_value", "premium"):
        return money(v, lang)
    if code == "tariff_pct":
        return pct(v, lang)
    if code == "term":
        return t("x_term", lang, days=v.get("days"), date_from=_ddmmyyyy(v.get("from")) if v.get("from") else NA,
                 date_to=_ddmmyyyy(v.get("to")) if v.get("to") else NA)
    if code == "franchise":
        if not v.get("applied"):
            return t("x_fr_none", lang)
        if v.get("pct") is not None:
            return pct(v["pct"], lang)
        return money(v["amount"], lang) if v.get("amount") else (v.get("text") or NA)
    if code == "object":
        kind = tx.label(tx.X_KIND_LABELS, v["class_hint"], lang) if v.get("class_hint") else None
        if by == "kind":
            return kind or NA
        if by == "cadastre":
            return str(v.get("cadastre_no") or NA)
        parts = [str(v["cadastre_no"])] if v.get("cadastre_no") else []
        return ", ".join(parts + ([kind] if kind else [])) or NA
    return str(v)


def cross_view(xc: Optional[dict], lang: str) -> Optional[dict]:
    """Запрос филиала против договора: строки «совпадает / расходится» и итог (null — нет одного из документов)."""
    if not xc or not xc.get("available"):
        return None
    items, lines = [], []
    for it in xc["items"]:
        label = tx.label(tx.X_LABELS, it["code"], lang)
        by = it.get("compared")
        req_v, ct_v = _x_value(it["code"], it["request"], lang, by), _x_value(it["code"], it["contract"], lang, by)
        # объект: сравнить нечем (кадастр только в одном документе, вида нет) — так и пишем, это не расхождение
        na_obj = it["code"] == "object" and it["verdict"] == "missing" and (it["request"] or it["contract"])
        text = t("x_object_na", lang, label=label, req=req_v, ct=ct_v) if na_obj else             t("x_line", lang, label=label, verdict=t("x_v_" + it["verdict"], lang), req=req_v, ct=ct_v)
        items.append({"code": it["code"], "label": label, "request": it["request"], "contract": it["contract"],
                      "verdict": it["verdict"], "verdict_label": t("x_v_" + it["verdict"], lang), "text": text,
                      "compared": by})
        if it["verdict"] != "missing" or na_obj:
            lines.append(text)
    n = xc.get("differs", 0)
    verdict = "differs" if n else ("ok" if any(i["verdict"] == "same" for i in xc["items"]) else "missing")
    summary = t("x_summary_" + verdict, lang, n=n) if verdict == "differs" else t("x_summary_" + verdict, lang)
    return {"available": True, "items": items, "differs": n, "missing": xc.get("missing", 0),
            "summary": {"verdict": verdict, "text": summary}, "lines": [summary] + lines}


# --------------------------------------------------------------------------- #
#  Оценка по объявлениям: ссылки поиска и снимки экрана сотрудника (30.09.2026)
#  Сервер к площадкам (в том числе olx.uz) не обращается: только адреса и чтение снимков моделью.
# --------------------------------------------------------------------------- #

MAX_SHOT_BODY = am.MAX_SHOTS * MAX_BYTES + 1024 * 1024
_FX_CACHE = {}                            # курс ЦБ на дату: (ответ, срок в time.monotonic() или None — навсегда)
FX_DEADLINE_SEC = 5                       # общий срок ожидания курса при загрузке снимков
FX_FAIL_TTL_SEC = 600                     # неудачу (нет сети, cbu.uz молчит) повторяем не раньше чем через 10 минут
# свой пул для курса: медленный cbu.uz не должен занимать потоки, которые ждут ответа модели
_FX_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="act-fx")
_FX_INFLIGHT = {}
_FX_LOCK = threading.Lock()


def _photo_query(con, sid: Optional[str], owner: str) -> dict:
    """Марка, модель, год и вид объекта из своей живой загрузки фото объекта (если она указана)."""
    if not sid:
        return {}
    rows = db.rows(con, "SELECT result_json FROM act_uploads WHERE id=? AND owner_key=? AND expires_at > ?",
                   str(sid)[:40], owner, _iso(_now()))
    up = json.loads(rows[0]["result_json"] or "{}") if rows else {}
    if not up or up.get("kind") == "market":
        return {}
    fields = up.get("fields") or []
    yr = preferred(fields, "year")
    return {"brand": (preferred(fields, "brand") or {}).get("value"),
            "model": (preferred(fields, "model") or {}).get("value"),
            "year": ae.to_year(yr["value"]) if yr else None, "object_kind": up.get("object_kind")}


def _market_query(request: Request, session: str, brand: str, model: str, year, object_kind: str,
                  class_code: str) -> tuple:
    """(запрос, ошибки): явные параметры сильнее значений из загрузки фото."""
    user = _user(request)
    owner = guest.owner_of(request, user)
    errs = {}
    q = {}
    if session:
        with db.tx() as con:
            ensure_tables(con)
            q = _photo_query(con, session, owner or "")
    for key, v, lim in (("brand", brand, 60), ("model", model, 60)):
        v = am._s(v, lim)
        if v:
            if llm.has_pd(v) and not re.fullmatch(r"[\w\-./ ]+", v, re.A):
                errs[key] = "похоже на данные человека"
            else:
                q[key] = v
    if year not in (None, ""):
        try:
            q["year"] = _int_in(year, 1950, date.today().year + 1)
        except ValueError:
            errs["year"] = f"целое число от 1950 до {date.today().year + 1}"
    if object_kind:
        if object_kind not in tx.OBJECT_KINDS:
            errs["object_kind"] = "неизвестный вид объекта"
        else:
            q["object_kind"] = object_kind
    if class_code:
        q["class_code"] = str(class_code).strip()[:10]
    return q, errs


@router.get("/act/market/links")
def act_market_links(request: Request, session: str = "", lang: str = "", brand: str = "", model: str = "",
                     year: str = "", object_kind: str = "", class_code: str = "", region: str = ""):
    """
    Ссылки поиска на площадках для браузера сотрудника (OLX, avtoelon.uz, uybor.uz, joymee.uz по виду объекта)
    и подсказка, что снять на снимке. Сервер только составляет адреса и сам по ним не ходит.
    """
    lang = _lang(request, lang)
    q, errs = _market_query(request, session, brand, model, year, object_kind, class_code)
    if errs:
        return _fail(request, "Проверьте поля: " + ", ".join(sorted(errs)), 422, errors=errs)
    q["group"] = am.group_of(q.get("object_kind"), q.get("class_code"))
    links = am.search_links(q, lang)
    if not links:
        return _fail(request, t("mk_links_none", lang), 422, errors={"query": "brand, model или object_kind"})
    return _reply(request, {"ok": True, "lang": lang,
                            "query": {k: q.get(k) for k in ("brand", "model", "year", "object_kind", "class_code",
                                                            "group")},
                            "region": am._s(region, 80), "links": links, "hint": t("mk_hint", lang, n=am.MAX_SHOTS),
                            "shot_tips": am.shot_tips(lang), "max_shots": am.MAX_SHOTS,
                            "note": t("mk_server_note", lang), "warning": t("mk_warn", lang)})


class _ShotsBodyLimitRoute(_BodyLimitRoute):
    max_body = MAX_SHOT_BODY


def _fx_cached(key: str) -> Optional[dict]:
    """Курс ЦБ из памяти: удачный — на весь день, неудача — FX_FAIL_TTL_SEC (не долбим cbu.uz на каждой загрузке)."""
    got = _FX_CACHE.get(key)
    if not got:
        return None
    res, until = got
    if until is not None and time.monotonic() > until:
        _FX_CACHE.pop(key, None)
        return None
    return res


def _fx_lookup(today: date) -> dict:
    """Курс из существующего источника проекта: valuation.fx_rate (ручной курс заказчика → ЦБ РУз, cbu.uz)."""
    from . import valuation
    key = today.isoformat()
    hit = _fx_cached(key)
    if hit is not None:
        return hit
    try:
        with db.tx() as con:
            fx = valuation.fx_rate(con, today)
    except Exception as e:                   # сбой курса не роняет загрузку, но запоминается на 10 минут
        fx = {"rate": None, "as_of": key, "reason": type(e).__name__}
    by = "manual_setting" if fx.get("manual") else ("cbu" if fx.get("rate") else None)
    res = {"rate": fx.get("rate"), "by": by, "as_of": fx.get("as_of"), "reason": fx.get("reason")}
    if by == "cbu":
        _FX_CACHE[key] = (res, None)
    elif by is None:
        _FX_CACHE[key] = (res, time.monotonic() + FX_FAIL_TTL_SEC)
    return res


def _fx_submit(today: date):
    """Запрос курса в своём пуле (не в пуле модели); одновременные загрузки ждут один и тот же запрос."""
    key = today.isoformat()
    with _FX_LOCK:
        fut = _FX_INFLIGHT.get(key)
        if fut is None or fut.done():
            fut = _FX_POOL.submit(_fx_lookup, today)
            _FX_INFLIGHT[key] = fut
        return fut


def fx_verify(claim: Optional[dict]) -> Optional[dict]:
    """
    Курс, который экран получил от сервера (fx.by = cbu), сверяется с памятью сервера без сети: совпал — это
    курс ЦБ; не с чем сверить — курс ЦБ по данным экрана, сервером не перепроверен (не «введён сотрудником»).
    """
    if not claim or claim.get("by") != "cbu":
        return None
    key = claim.get("as_of") or date.today().isoformat()
    hit = _fx_cached(key)
    if hit and hit.get("by") == "cbu" and hit.get("rate") and abs(float(hit["rate"]) - float(claim["rate"])) < 0.005:
        return {"rate": float(hit["rate"]), "by": "cbu", "as_of": hit.get("as_of") or key}
    return {"rate": float(claim["rate"]), "by": "cbu_unverified", "as_of": key}


def act_market_shots(request: Request, files: List[UploadFile] = File(...), session: str = Form(""),
                     lang: str = Form(""), site: str = Form(""), brand: str = Form(""), model: str = Form(""),
                     year: str = Form(""), object_kind: str = Form(""), usd_rate: str = Form("")):
    """
    Снимки экрана со списком объявлений (до 5 файлов JPG/PNG). Модель читает их одним запросом и возвращает
    объявления; сервер пересчитывает валюту, считает предварительную медиану и хранит снимки 24 часа.
    Гостевой лимит — по числу файлов (как у /act/photos), лимит обращений к модели — общий с распознаванием фото.
    """
    user = _user(request)
    owner = guest.owner_of(request, user)
    lang = _lang(request, lang)
    if not owner:
        return _fail(request, "Не удалось опознать сессию — откройте приложение заново", 400)
    if len(files) > am.MAX_SHOTS:
        return _fail(request, t("mk_too_many", lang, n=am.MAX_SHOTS), 413)
    site = str(site or "").strip().lower() or "other"
    errs = {}
    if site not in am.SITES:
        errs["site"] = "одно из: " + ", ".join(am.SITES)
    rate, rate_err = am.usd_rate_in(usd_rate)
    if rate_err:
        errs["usd_rate"] = rate_err
    q, qerrs = _market_query(request, session, brand, model, year, object_kind, "")
    errs.update(qerrs)
    if errs:
        return _fail(request, "Проверьте поля: " + ", ".join(sorted(errs)), 422, errors=errs)
    with db.tx() as con:
        ensure_tables(con)
        st = load_settings(con)
    limits = st["limits"]
    if owner.startswith("g:"):
        n_max = int(limits["guest_photos_per_hour"])
        res = GUEST_PHOTOS.take(owner, len(files), n_max)
        if not res["ok"]:
            print("акт: лимит снимков гостя", guest.short(owner), res["count"], "/", n_max, flush=True)
            return _limit_reply(request, t("ph_guest_limit", lang, n=n_max), res)
    sid = secrets.token_hex(12)
    folder = DIR / sid
    try:
        return _shots(request, user, owner, lang, files, site, q, rate, st, sid, folder)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


router.add_api_route("/act/market/shots", act_market_shots, methods=["POST"],
                     route_class_override=_ShotsBodyLimitRoute)


def _shots(request, user, owner, lang, files, site, q, emp_rate, st, sid, folder):
    limits = st["limits"]
    saved, rejected = [], []
    for i, up in enumerate(files, start=1):
        name = llm.mask_pd(Path(up.filename or "").name)[:120] or f"file {i}"   # только для ответа
        blob = up.file.read(MAX_BYTES + 1)
        if not blob:
            rejected.append({"index": i, "name": name, "error": t("ph_empty_file", lang)})
            continue
        if len(blob) > MAX_BYTES:
            rejected.append({"index": i, "name": name, "error": t("ph_too_big", lang, mb=MAX_BYTES // (1024 * 1024))})
            continue
        fmt = _format_of(blob)
        err = check_content(blob, fmt, limits, lang) if fmt in ("jpg", "png") else t("mk_format", lang)
        if err:
            rejected.append({"index": i, "name": name, "error": err})
            continue
        folder.mkdir(parents=True, exist_ok=True)
        fid = f"s{len(saved) + 1}"
        path = folder / f"{fid}.{fmt}"
        path.write_bytes(blob)
        saved.append({"id": fid, "index": i, "name": name, "orig_name": "", "fmt": fmt, "mime": FMT_MIME[fmt],
                      "size": len(blob), "path": db.stored_path(path), "blob": blob})
    if not saved:
        shutil.rmtree(folder, ignore_errors=True)
        return _fail(request, t("ph_none", lang), 422, rejected=rejected, ai=False, warning=t("mk_warn", lang))

    today = date.today()
    # курс ищем параллельно с чтением снимков в своём пуле: к cbu.uz ходит существующий модуль оценки,
    # не к площадкам; общий срок ожидания — FX_DEADLINE_SEC от начала загрузки
    fx_fut = _fx_submit(today)
    t0 = time.monotonic()
    rec = ask_model(saved, lang, limits, "акт: снимки объявлений", am.SYSTEM_PROMPT,
                    lambda n: am.model_prompt(n, lang, q, today),
                    lambda text, n: am.parse_model(text, n, today, site))
    try:
        fx = fx_fut.result(timeout=max(0.0, FX_DEADLINE_SEC - (time.monotonic() - t0)))
    except FutureTimeout:
        fx = {"rate": None, "by": None, "as_of": today.isoformat(),
              "reason": f"курс не получен за {FX_DEADLINE_SEC} с"}
    except Exception as e:                   # курс не должен ронять загрузку, но причина видна
        fx = {"rate": None, "by": None, "as_of": today.isoformat(), "reason": type(e).__name__}
    if not fx.get("rate") and emp_rate:
        fx = {"rate": emp_rate, "by": "employee", "as_of": today.isoformat()}
    fx_used = fx if fx.get("rate") else None
    sent = rec.get("sent") or []
    model_to_id = {k + 1: saved[i]["id"] for k, i in enumerate(sent)}
    listings = []
    for r in rec.get("listings") or []:
        listings.append({**r, "file": model_to_id.get(r.get("file")), "source": "shot", "edited": False})
    est = ae.market_estimate(listings, settings=st, shot_date=today, usd_rate=(fx_used or {}).get("rate"))
    usd_needed = not fx_used and any(r.get("currency") in ae.USD_LIKE for r in listings)
    stored_listings = [{k: r.get(k) for k in am.LISTING_KEYS if k in r} for r in listings]
    with db.tx() as con:
        ensure_tables(con)
        cleanup(con)
        now = _now()
        stored = {"kind": "market", "lang": lang, "ai": bool(rec.get("ok")), "reason": rec.get("reason"),
                  "shot_date": today.isoformat(), "site": site, "query": q, "listings": stored_listings,
                  "fx": fx_used, "files": len(saved)}
        keep = ("id", "index", "fmt", "mime", "size", "path")
        con.execute("INSERT INTO act_uploads (id, owner_key, user_id, files_json, result_json, created_at, "
                    "expires_at) VALUES (?,?,?,?,?,?,?)",
                    (sid, owner, (user or {}).get("id") or 0,
                     json.dumps([{k: f[k] for k in keep} for f in saved], ensure_ascii=False),
                     json.dumps(stored, ensure_ascii=False), _iso(now),
                     _iso(now + timedelta(seconds=PHOTO_TTL_SEC))))
        # в журнал — только счётчики: ни названий, ни цен, ни данных продавцов
        db.audit(con, _who(user, owner), "акт: снимки объявлений", f"act_upload:{sid}",
                 {"files": len(saved), "rejected": len(rejected), "ai": bool(rec.get("ok")),
                  "listings": len(listings), "relevant": sum(1 for r in listings if r.get("relevant")),
                  "dropped_pd": rec.get("dropped") or 0, "duplicates": rec.get("duplicates") or 0,
                  "site": site, "fx": (fx_used or {}).get("by")})
    notes = []
    if rec.get("ok"):
        message = t("mk_ok", lang, n=len(listings), k=sum(1 for r in listings if r.get("relevant"))) \
            if listings else t("mk_empty", lang)
    else:
        message = t("mk_ai_off", lang, reason=rec.get("reason") or t("ai_not_connected", lang))
    if rec.get("dropped"):
        notes.append(t("mk_pd_dropped", lang, n=rec["dropped"]))
    if usd_needed:
        notes.append(t("mk_need_rate", lang))
    fxv = am.fx_label(fx_used, lang)
    if fxv:
        notes.append(fxv["text"])
    if rec.get("ok") and rec.get("not_sent"):
        notes.append(t("ph_not_sent", lang, files=", ".join(str(saved[i]["index"]) for i in rec["not_sent"]),
                       mb=limits["ai_max_mb"]))
    by_id = {r.get("id"): r for r in est["listings"]}
    q_links = dict(q, group=am.group_of(q.get("object_kind")))
    return _reply(request, {
        "ok": True, "shots_session": sid, "lang": lang, "shot_date": today.isoformat(), "site": site,
        "site_label": tx.label(tx.SITE_LABELS, site, lang),
        "files": [{"id": f["id"], "index": f["index"], "name": f["name"], "format": f["fmt"],
                   "read_by_ai": bool(rec.get("ok")) and k in sent} for k, f in enumerate(saved)],
        "rejected": rejected,
        "listings": [am.listing_view(by_id.get(r["id"], r), lang) for r in listings],
        "estimate": am.estimate_view(est, lang),
        "fx": fxv, "usd_rate_needed": usd_needed,
        "query": {k: q.get(k) for k in ("brand", "model", "year", "object_kind")},
        "links": am.search_links(q_links, lang) if (q.get("brand") or q.get("model") or q.get("object_kind")) else [],
        "ai": bool(rec.get("ok")), "message": message, "notes": notes,
        "warning": t("mk_warn", lang), "expires_in_hours": PHOTO_TTL_SEC // 3600,
    })


def prefill_view(prefill: dict, lang: str) -> dict:
    """Подсказка для шага 2: значения из документа с источником и пометкой «из документа, проверьте»
    (прочитанное моделью из текста договора — «прочитано моделью из текста, проверьте»)."""
    out = {}
    for k, v in prefill.items():
        mark = t("ct_ai_note", lang) if v.get("source") == "document_ai" else t("prefill_check", lang)
        out[k] = {**v, "label": tx.label(tx.FIELD_LABELS, k, lang), "check_label": mark}
    return out


# --------------------------------------------------------------------------- #
#  Проверка ввода (недоверенные данные)
# --------------------------------------------------------------------------- #

def _money_in(v) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        s = re.sub(r"[\s\u00a0\u202f_]", "", str(v)).replace(",", ".")
        if not re.fullmatch(r"\d+(\.\d+)?", s):
            return None
        x = float(s)
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def _int_in(v, lo: int, hi: int) -> Optional[int]:
    if v is None or v == "" or isinstance(v, bool):
        return None
    # только целое: 365.5 — ошибка ввода, а не 365 (дробная часть не отбрасывается молча)
    if isinstance(v, float):
        if v != v or not v.is_integer():
            raise ValueError
        x = int(v)
    elif isinstance(v, int):
        x = v
    else:
        s = str(v).strip()
        if not re.fullmatch(r"[+-]?\d{1,9}(?:[.,]0+)?", s):
            raise ValueError
        x = int(re.split(r"[.,]", s)[0])
    if not lo <= x <= hi:
        raise ValueError
    return x


def _bool_in(v) -> Optional[bool]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "да", "ha"):
        return True
    if s in ("0", "false", "no", "нет", "yoʻq", "yoq"):
        return False
    raise ValueError


def validate(con, body: dict) -> tuple:
    """(чистый ввод, ошибки {поле: текст}). Ошибки не глотаются: вызывающий отвечает 422 и пишет в журнал."""
    errs = {}
    must = body.get("must") if isinstance(body.get("must"), dict) else {}
    opt = body.get("optional") if isinstance(body.get("optional"), dict) else {}
    clean = {"must": {}, "optional": {}}
    m = clean["must"]

    pcode = str(must.get("product_code") or "").strip()[:10]
    ccode = str(must.get("class_code") or "").strip()[:10]
    product = None
    classes = []
    if pcode:
        rows = db.rows(con, "SELECT code, name, pricing_mode, rate_text FROM products WHERE code=?", pcode)
        if not rows:
            errs["product_code"] = "продукт не найден в справочнике"
        else:
            product = rows[0]
            classes = [r["class_code"] for r in db.rows(
                con, "SELECT class_code FROM product_classes WHERE product_code=? ORDER BY part_no", pcode)]
            classes = list(dict.fromkeys(classes))
            if ccode and ccode not in classes:
                errs["class_code"] = "класс не относится к продукту: " + ", ".join(classes)
            ccode = ccode or (classes[0] if classes else "")
            if not ccode:
                errs["product_code"] = "у продукта нет класса страхования"
    elif ccode:
        if not db.rows(con, "SELECT code FROM classes WHERE code=?", ccode):
            errs["class_code"] = "класс не найден в справочнике"
    else:
        errs["product_code"] = "нужен продукт или класс"
    m.update(product_code=pcode or None, class_code=ccode or None, product=product, product_classes=classes)

    for key in ("sum_insured", "object_value"):
        x = _money_in(must.get(key))
        if x is None:
            errs[key] = "нужно число больше нуля"
        elif x <= 0 or x > MAX_SUM:
            errs[key] = "сумма должна быть больше нуля и меньше 10^15"
        else:
            m[key] = x
    region = str(must.get("region") or "").strip()
    if not region:
        errs["region"] = "укажите регион"
    elif len(region) > 80:
        errs["region"] = "не длиннее 80 знаков"
    else:
        m["region"] = region
        m["region_code"] = region_code(region)

    o = clean["optional"]
    this_year = date.today().year
    for key, lo, hi in (("year", 1950, this_year + 1), ("purchase_year", 1950, this_year),
                        ("term_days", 1, 3660)):
        try:
            o[key] = _int_in(opt.get(key), lo, hi)
        except ValueError:
            errs[key] = f"целое число от {lo} до {hi}"
    loc = opt.get("location")
    if loc not in (None, ""):
        if str(loc) not in ae.LOCATIONS:
            errs["location"] = "одно из: " + ", ".join(ae.LOCATIONS)
        else:
            o["location"] = str(loc)
    for key in ("guard", "want_lower_premium", "documents_provided"):
        try:
            o[key] = _bool_in(opt.get(key))
        except ValueError:
            errs[key] = "да или нет"
    losses = opt.get("losses_3y")
    o["losses_count"] = o["small_count"] = o["losses_amount"] = None
    if losses not in (None, ""):
        if not isinstance(losses, dict):
            errs["losses_3y"] = "объект {count, small_count, amount}"
        else:
            try:
                o["losses_count"] = _int_in(losses.get("count"), 0, 1000)
                o["small_count"] = _int_in(losses.get("small_count"), 0, 1000)
            except ValueError:
                errs["losses_3y"] = "количество — целое число от 0 до 1000"
            amt = losses.get("amount")
            if amt not in (None, ""):
                a = _money_in(amt)
                if a is None or a > MAX_SUM:
                    errs["losses_3y"] = "сумма убытков — число не меньше нуля"
                else:
                    o["losses_amount"] = a
            if o["small_count"] is not None and o["losses_count"] is not None \
                    and o["small_count"] > o["losses_count"]:
                errs["losses_3y"] = "мелких убытков не может быть больше, чем всех"
    price = opt.get("price_new")
    if price not in (None, ""):
        p = _money_in(price)
        if p is None or p <= 0 or p > MAX_SUM:
            errs["price_new"] = "нужно число больше нуля"
        else:
            o["price_new"] = p
    # стоимость, которую клиент заявил до того, как сотрудник заменил её медианой объявлений
    dvo = opt.get("declared_value_original")
    if dvo not in (None, ""):
        v = _money_in(dvo)
        if v is None or v <= 0 or v > MAX_SUM:
            errs["declared_value_original"] = "нужно число больше нуля"
        else:
            o["declared_value_original"] = v
    kind = opt.get("object_kind")
    if kind not in (None, ""):
        if kind not in tx.OBJECT_KINDS:
            errs["object_kind"] = "неизвестный вид объекта"
        else:
            o["object_kind"] = kind
    otype = opt.get("object_type")
    if otype not in (None, ""):
        o["object_type"] = str(otype).strip()[:120]
    cond = opt.get("condition")
    if cond not in (None, ""):
        if cond not in CONDITIONS:
            errs["condition"] = "одно из: " + ", ".join(CONDITIONS)
        else:
            o["condition"] = cond
    dom = _s(opt.get("dominant_risk"), 80)
    if dom:
        o["dominant_risk"] = None if llm.has_pd(dom) else dom
    payer = opt.get("payer_type")
    if payer not in (None, ""):
        if payer not in ("юр", "физ"):
            errs["payer_type"] = "юр или физ"
        else:
            o["payer_type"] = payer

    # уточнения для сценариев убытка (risk_analytics); чего нет — берётся по умолчанию с пометкой
    prot = opt.get("protection")
    if prot not in (None, ""):
        codes = ax.PROT_CODES.get(ccode)
        if not codes:
            # у класса нет списка защиты (он есть только у 3, 8, 9) — значение не учитывается,
            # в акте об этом пометка в «принято по умолчанию» (as_protection_ignored)
            o["protection_ignored"] = True
        elif prot not in codes:
            errs["protection"] = "одно из: " + ", ".join(codes)
        else:
            o["protection"] = prot
    try:
        o["seismic_zone"] = _int_in(opt.get("seismic_zone"), 5, 10)
    except ValueError:
        errs["seismic_zone"] = "целое число баллов от 5 до 10"
    for key, allowed in (("construction", ax.CONSTRUCTIONS), ("activity", ax.ACTIVITIES)):
        v = opt.get(key)
        if v not in (None, ""):
            if v not in allowed:
                errs[key] = "одно из: " + ", ".join(allowed)
            else:
                o[key] = v
    # франшиза сотрудника: {pct | amount, type}; по обязательным видам не применяется (решает build_data)
    ded = opt.get("deductible")
    o["deductible"] = None
    if ded not in (None, "", {}):
        if not isinstance(ded, dict):
            errs["deductible"] = "объект {pct | amount, type}"
        else:
            p, a = ded.get("pct"), ded.get("amount")
            ftype = str(ded.get("type") or "unconditional").strip()
            pv = _money_in(p) if p not in (None, "") else None
            av = _money_in(a) if a not in (None, "") else None
            if ftype not in ax.FR_TYPES:
                errs["deductible"] = "type: " + ", ".join(ax.FR_TYPES)
            elif (p not in (None, "") and pv is None) or (a not in (None, "") and av is None):
                errs["deductible"] = "pct и amount — числа"
            elif pv is None and av is None:
                errs["deductible"] = "нужен pct (% страховой суммы) или amount (сумы)"
            elif pv is not None and av is not None:
                errs["deductible"] = "укажите что-то одно: pct или amount"
            elif pv is not None and not 0 < pv <= 50:
                errs["deductible"] = "pct больше 0 и не больше 50 % страховой суммы"
            elif av is not None and not (0 < av <= (m.get("sum_insured") or MAX_SUM) * 0.5):
                errs["deductible"] = "amount больше 0 и не больше половины страховой суммы"
            else:
                o["deductible"] = {"pct": pv, "amount": av, "type": ftype}

    # запрос филиала (30.09.2026): тариф, премия, франшиза, срок — для сверки с расчётом акта
    inclusive = bool(load_settings(con)["request_check"]["term_inclusive"])
    rq, rq_err = validate_request(opt.get("request"), inclusive)
    if rq_err:
        errs["request"] = rq_err
    clean["request"] = rq
    # договор страхования (30.09.2026): те же поля и условия договора — для сверки с расчётом акта
    ct, ct_err = validate_contract(opt.get("contract"), inclusive)
    if ct_err:
        errs["contract"] = ct_err
    clean["contract"] = ct

    # объявления со снимков экрана с правками сотрудника (30.09.2026); в расчёт — act_engine.market_estimate
    mk, mk_err = am.validate_market(opt.get("market"))
    if mk_err:
        errs["market"] = mk_err
    clean["market"] = mk

    # распознанное с правками сотрудника: только известные поля, значения без ПД
    rec = body.get("recognized")
    items, dropped = [], 0
    if rec is not None:
        if not isinstance(rec, list):
            errs["recognized"] = "список {key, value, source}"
        else:
            for r in rec[:MAX_RECOGNIZED]:
                if not isinstance(r, dict):
                    continue
                key = str(r.get("key") or "")
                val = _s(r.get("value"), value_limit(key))
                if key not in ae.FIELD_KEYS or not val:
                    continue
                if pd_like(key, val):
                    dropped += 1
                    continue
                src = str(r.get("source") or "input")
                src = src if src in ae.SOURCES else "input"
                note = _s(r.get("note"), 200)
                items.append({"key": key, "value": val, "source": src,
                              "note": None if (note and llm.has_pd(note)) else note,
                              "file_id": _s(r.get("file"), 10)})
    clean["recognized"] = items if rec is not None else None
    clean["dropped_pd"] = dropped
    dmg = body.get("damages")
    if dmg is not None:
        if not isinstance(dmg, list):
            errs["damages"] = "список {what, where}"
        else:
            clean["damages"] = []
            for d in dmg[:20]:
                d = {"what": d} if isinstance(d, str) else d
                if not isinstance(d, dict):
                    continue
                what, where = _s(d.get("what"), 160), _s(d.get("where"), 80)
                if what and not llm.has_pd(what) and not (where and llm.has_pd(where)):
                    clean["damages"].append({"what": what, "where": where, "file": _s(d.get("file"), 10)})
    clean["session"] = _s(body.get("session"), 40)
    return clean, errs


REQUEST_SOURCES = ("document", "photo", "input", "session")
CONTRACT_SOURCES = ("document", "document_ai", "photo", "input", "session")


DATE_MIN, DATE_MAX = date(2000, 1, 1), date(2100, 12, 31)     # даты срока, договора, платежей
DATE_RANGE = "с 01.01.2000 по 31.12.2100"
MAX_TERMS_SUM = 1e14          # суммы запроса и договора: сто триллионов сумов — опечатка в разрядах, а не объект


def _date_in(v) -> Optional[date]:
    """Дата из «ГГГГ-ММ-ДД» или «ДД.ММ.ГГГГ» в пределах 2000–2100; иначе ValueError."""
    s = str(v or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s) or None
    m2 = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", s)
    d = None
    try:
        if m:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        elif m2:
            d = date(int(m2.group(3)), int(m2.group(2)), int(m2.group(1)))
    except ValueError:
        pass
    if d is None or not DATE_MIN <= d <= DATE_MAX:
        raise ValueError
    return d


def validate_request(rq, inclusive: bool = True) -> tuple:
    """
    optional.request (запрос филиала, недоверенный ввод) → (чистый запрос | None, текст ошибки | None).
    {tariff_pct: % годовых (0; 100], premium: сумы > 0, franchise: {applied, text?, pct?, amount?} или текст
    бланка, term_from/term_to: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ (2000–2100), term_days: целое 1–3660, sum_insured?,
    object_value?, cadastre_no?, object_kind?, class_hint?, object_description?}. Поле source не доверяется:
    источник решает сервер по своей загрузке (build_data → _trust_doc).
    Даты сильнее term_days: дни считаются по датам (оба крайних дня — настройка request_check.term_inclusive);
    term_days, не совпавший с датами, — ошибка, а не молчаливая замена.
    """
    if rq in (None, "", {}):
        return None, None
    if not isinstance(rq, dict):
        return None, "объект {tariff_pct, premium, franchise, term_from, term_to, term_days}"
    out, err = _validate_terms(rq, inclusive, REQUEST_SOURCES)
    if err:
        return None, err
    err = _object_terms(rq, out)
    if err:
        return None, err
    if all(out[k] is None for k in ("tariff_pct", "premium", "franchise", "term_days")):
        return None, "в запросе нет ни тарифа, ни премии, ни франшизы, ни срока"
    return out, None


def _money_term(v, key: str) -> tuple:
    """Сумма условия документа: (число | None, ошибка | None). Больше MAX_TERMS_SUM — опечатка в разрядах."""
    if v in (None, ""):
        return None, None
    x = _money_in(v)
    if x is None or not 0 < x <= MAX_TERMS_SUM:
        return None, f"{key} — число больше нуля и не больше 10^14 сумов"
    return x, None


def _object_terms(rq: dict, out: dict) -> Optional[str]:
    """Стоимость и объект документа (для сверки запроса с договором): стоимость, кадастр, вид, описание."""
    out["object_value"], err = _money_term(rq.get("object_value"), "object_value")
    if err:
        return err
    desc = _s(rq.get("object_description"), br.MAX_TEXT)
    out["object_description"] = desc if desc and not pd_like("object_type", desc) else None
    cad = _s(rq.get("cadastre_no"), 40)
    out["cadastre_no"] = cad if cad and br._KADASTR.fullmatch(cad) else None
    kind = str(rq.get("object_kind") or "").strip()
    out["object_kind"] = kind if kind in tx.OBJECT_KINDS else None
    hint = str(rq.get("class_hint") or "").strip()
    out["class_hint"] = hint if hint in HINT_CODES else None
    return None


HINT_CODES = ("building", "equipment", "vehicle", "special_machinery", "cargo", "other")


def _validate_terms(rq: dict, inclusive: bool, sources: tuple) -> tuple:
    """Общие условия запроса филиала и договора: тариф, премия, сумма, франшиза, срок."""
    out = {"tariff_pct": None, "premium": None, "franchise": None, "term_from": None, "term_to": None,
           "term_days": None, "sum_insured": None, "source": "input"}
    tr = rq.get("tariff_pct")
    if tr not in (None, ""):
        x = _money_in(tr)
        if x is None or not 0 < x <= 100:
            return None, "tariff_pct — процент годовых больше 0 и не больше 100"
        out["tariff_pct"] = x
    for key in ("premium", "sum_insured"):
        out[key], err = _money_term(rq.get(key), key)
        if err:
            return None, err
    if out["premium"] is not None and out["sum_insured"] is not None and out["premium"] > out["sum_insured"]:
        return None, "premium больше страховой суммы — проверьте разряды"
    fr = rq.get("franchise")
    if fr not in (None, "", {}):
        if isinstance(fr, str):
            fr = br.franchise(fr)
        if not isinstance(fr, dict) or not isinstance(fr.get("applied"), bool):
            return None, "franchise — {applied: true|false, text, pct, amount}"
        pv = _money_in(fr.get("pct")) if fr.get("pct") not in (None, "") else None
        av = _money_in(fr.get("amount")) if fr.get("amount") not in (None, "") else None
        if (fr.get("pct") not in (None, "") and (pv is None or not 0 < pv <= 100)) or \
                (fr.get("amount") not in (None, "") and (av is None or not 0 < av <= MAX_TERMS_SUM)):
            return None, "franchise.pct — от 0 до 100 %, franchise.amount — сумы больше нуля"
        text = _s(fr.get("text"), 120)
        out["franchise"] = {"applied": fr["applied"], "text": None if (text and llm.has_pd(text)) else text,
                            "pct": pv if fr["applied"] else None, "amount": av if fr["applied"] else None}
    d1, d2 = rq.get("term_from"), rq.get("term_to")
    if d1 not in (None, "") or d2 not in (None, ""):
        try:
            a, b = _date_in(d1), _date_in(d2)
        except ValueError:
            return None, f"term_from и term_to — даты ГГГГ-ММ-ДД или ДД.ММ.ГГГГ {DATE_RANGE}, обе"
        if b < a:
            return None, "term_to раньше term_from"
        days = br.term_days(a, b, inclusive)
        if not 1 <= days <= br.MAX_TERM_DAYS:
            return None, f"срок от 1 до {br.MAX_TERM_DAYS} дней"
        out.update(term_from=a.isoformat(), term_to=b.isoformat(), term_days=days)
    td = rq.get("term_days")
    if td not in (None, ""):
        try:
            x = _int_in(td, 1, br.MAX_TERM_DAYS)
        except ValueError:
            return None, f"term_days — целое число от 1 до {br.MAX_TERM_DAYS}"
        if out["term_days"] is not None and x != out["term_days"]:
            return None, (f"term_days {x} не совпадает со сроком по датам ({out['term_days']} дн., "
                          f"{'оба крайних дня включены' if inclusive else 'без последнего дня'})")
        out["term_days"] = x
    # заявленный экраном источник — только подсказка «был ли документ» (пометка «документ недоступен»)
    src = str(rq.get("source") or "input").strip()
    out["claimed_source"] = src if src in sources else "input"
    pc = re.search(r"(?<!\d)(\d{3,4})(?!\d)", str(rq.get("product_code") or ""))
    out["product_code"] = pc.group(1).zfill(4) if pc else None
    return out, None


def validate_contract(ct, inclusive: bool = True) -> tuple:
    """
    optional.contract (договор страхования, недоверенный ввод) → (чистый договор | None, текст ошибки | None).
    Поля optional.request (тариф, премия, франшиза {applied, text, pct, amount, type}, срок, sum_insured,
    объект) и contract_no, contract_date, currency, covered_risks[], exclusions[] (коды словаря или короткий
    текст), payment_mode, payments[{date, amount}], items[{name, sum}]. Даты — 2000–2100. Поле source не
    доверяется (build_data → _trust_doc). Значения, похожие на ПД, отбрасываются.
    """
    if ct in (None, "", {}):
        return None, None
    if not isinstance(ct, dict):
        return None, "объект {tariff_pct, premium, franchise, term_from, term_to, sum_insured, contract_no, …}"
    out, err = _validate_terms(ct, inclusive, CONTRACT_SOURCES)
    if err:
        return None, err
    err = _object_terms(ct, out)
    if err:
        return None, err
    fr = ct.get("franchise")
    if out["franchise"] is not None and isinstance(fr, dict) and fr.get("type") not in (None, ""):
        if fr["type"] not in ("unconditional", "conditional"):
            return None, "franchise.type — unconditional или conditional"
        out["franchise"]["type"] = fr["type"] if out["franchise"]["applied"] else None
    no = _s(ct.get("contract_no"), 40)
    out["contract_no"] = no if no and not llm.has_pd(no) else None
    out["contract_date"] = None
    if ct.get("contract_date") not in (None, ""):
        try:
            out["contract_date"] = _date_in(ct["contract_date"]).isoformat()
        except ValueError:
            return None, f"contract_date — дата ГГГГ-ММ-ДД или ДД.ММ.ГГГГ {DATE_RANGE}"
    cur = str(ct.get("currency") or "").strip().upper()
    if cur and cur not in ("UZS", "USD", "EUR", "RUB"):
        return None, "currency — UZS, USD, EUR или RUB"
    out["currency"] = cur or None
    for key, table, codes in (("covered_risks", cr._RISK_F, cr.RISK_CODES),
                              ("exclusions", cr._EXCL_F, cr.EXCLUSION_CODES)):
        v = ct.get(key)
        if v in (None, "", []):
            out[key] = []
            continue
        if not isinstance(v, list) or len(v) > 40:
            return None, f"{key} — список (не больше 40) кодов или коротких названий"
        vals = [x for x in (_s(x.get("text") or x.get("code") if isinstance(x, dict) else x, 80) for x in v) if x]
        out[key] = cr._codes([x for x in vals if not llm.has_pd(x)], table, codes)
    mode = ct.get("payment_mode")
    if mode not in (None, "", "single", "installments"):
        return None, "payment_mode — single или installments"
    out["payment_mode"] = mode or None
    pays = ct.get("payments")
    out["payments"] = []
    if pays not in (None, "", []):
        if not isinstance(pays, list) or len(pays) > cr.MAX_PAYMENTS:
            return None, f"payments — список до {cr.MAX_PAYMENTS} платежей {{date, amount}}"
        for x in pays:
            if not isinstance(x, dict):
                return None, "payments — список {date, amount}"
            try:
                d = _date_in(x.get("date"))
            except ValueError:
                return None, f"payments.date — дата ГГГГ-ММ-ДД или ДД.ММ.ГГГГ {DATE_RANGE}"
            a, err = _money_term(x.get("amount"), "payments.amount")
            if err or a is None:
                return None, "payments.amount — число больше нуля и не больше 10^14 сумов"
            out["payments"].append({"date": d.isoformat(), "amount": a})
    items = ct.get("items")
    out["items"] = []
    if items not in (None, "", []):
        if not isinstance(items, list) or len(items) > cr.MAX_ITEMS:
            return None, f"items — список до {cr.MAX_ITEMS} частей {{name, sum}}"
        for x in items:
            if not isinstance(x, dict):
                return None, "items — список {name, sum}"
            a, err = _money_term(x.get("sum"), "items.sum")
            if err or a is None:
                return None, "items.sum — число больше нуля и не больше 10^14 сумов"
            name = _s(x.get("name"), 200)
            out["items"].append({"name": name if name and not pd_like("object_type", name) else None, "sum": a})
    if all(out[k] in (None, []) for k in ("tariff_pct", "premium", "franchise", "term_days", "sum_insured",
                                          "contract_no", "object_description", "covered_risks", "payments")):
        return None, "в договоре нет ни одного условия"
    return out, None


# --------------------------------------------------------------------------- #
#  Источник условий запроса и договора: решает сервер по своей загрузке (30.09.2026)
# --------------------------------------------------------------------------- #

# поля, правка которых сотрудником видна в акте (было → стало)
RQ_COMPARE = ("product_code", "sum_insured", "object_value", "tariff_pct", "premium", "franchise",
              "term_from", "term_to", "term_days", "cadastre_no", "object_description")
CT_COMPARE = RQ_COMPARE + ("contract_no", "contract_date", "currency", "covered_risks", "exclusions",
                           "payment_mode", "payments", "items")
_MONEY_CMP = ("sum_insured", "object_value", "premium")
DOC_SOURCES = ("document", "document_ai", "photo", "session")


def _empty_v(v) -> bool:
    return v in (None, "", [], {})


def _same_term(key: str, a, b) -> bool:
    if _empty_v(a) and _empty_v(b):
        return True
    if key in _MONEY_CMP or key == "tariff_pct":
        try:
            return a is not None and b is not None and abs(float(a) - float(b)) <= 0.005
        except (TypeError, ValueError):
            return False
    return a == b


def _saved_terms(prefix: str, block: dict, inclusive: bool) -> dict:
    """Условия своей загрузки в той же форме, что проверенный ввод экрана (validate_request / validate_contract):
    сравниваются одинаково нормализованные значения."""
    f = block.get("fields") or {}
    if prefix == "rq":
        raw = br.request_of(f, "document")
        clean, err = validate_request(raw, inclusive)
    else:
        raw = cr.request_of(f, "document")
        raw["cadastre_no"] = f.get("cadastre_no")
        clean, err = validate_contract(raw, inclusive)
    return clean if clean and not err else dict(raw)


def _field_source(prefix: str, block: dict, key: str) -> str:
    """Источник поля своей загрузки: photo — скан (модель), document_ai — дочитано моделью, иначе document."""
    base = "photo" if block.get("source") == "photo" else "document"
    if prefix == "ct":
        return (block.get("field_sources") or {}).get(key) or base
    return base


def _trust_doc(prefix: str, sent: Optional[dict], block: Optional[dict], upload_missing: bool,
               inclusive: bool) -> tuple:
    """
    Условия запроса (rq) или договора (ct) для акта и источник каждого поля (образец — act_market.trust_listings).
    Своя живая загрузка с этим документом есть: присланное сравнивается с сохранённым по полям — совпало —
    источник документа (document / photo / document_ai), отличается — input и правка «было → стало».
    Загрузки нет (истекла, чужая, сменилась сессия): все поля — input, пометка «документ недоступен».
    Возвращает (условия | None, {source_kind, origin, edits, field_sources, doc_missing}).
    """
    keys = RQ_COMPARE if prefix == "rq" else CT_COMPARE
    if prefix == "ct" and ((block or {}).get("fields") or {}).get("is_template"):
        # загружен бланк договора (поля не заполнены): сверки договора с расчётом нет — как у заявления
        return None, None
    if sent is None:
        if not block:
            return None, None
        doc = _request_from_upload(block) if prefix == "rq" else _contract_from_upload(block)
        if not doc:
            return None, None
        fs = {k: _field_source(prefix, block, k) for k in keys if not _empty_v(doc.get(k))}
        return doc, {"source_kind": "document", "origin": block.get("source"), "edits": [], "field_sources": fs,
                     "doc_missing": False}
    doc = dict(sent)
    claimed = doc.pop("claimed_source", "input")
    if not block:
        fs = {k: "input" for k in keys if not _empty_v(doc.get(k))}
        doc["source"] = "input"
        return doc, {"source_kind": "input", "origin": None, "edits": [], "field_sources": fs,
                     "doc_missing": bool(upload_missing or claimed in DOC_SOURCES)}
    saved = _saved_terms(prefix, block, inclusive)
    edits, fs = [], {}
    dates_edited = any(not _same_term(k, saved.get(k), doc.get(k)) for k in ("term_from", "term_to"))
    for k in keys:
        a, b = saved.get(k), doc.get(k)
        if _empty_v(a) and _empty_v(b):
            continue
        if _same_term(k, a, b):
            fs[k] = _field_source(prefix, block, k)
            continue
        fs[k] = "input"
        if k == "term_days" and dates_edited:
            continue                      # дни — следствие правки дат, отдельной правкой не считаются
        edits.append({"code": k, "was": a, "now": b})
    doc["source"] = block.get("source") or "document"
    return doc, {"source_kind": "document_edited" if edits else "document", "origin": doc["source"],
                 "edits": edits, "field_sources": fs, "doc_missing": False}


def _region_names() -> dict:
    """Коды регионов из словаря интерфейса (tg.act.reg.*) → их названия на всех языках."""
    out = {}
    try:
        for key in i18n.keys("ru"):
            if key.startswith("tg.act.reg."):
                code = key[len("tg.act.reg."):]
                out[code] = {lg: i18n.t(key, lg) for lg in i18n.LANGS}
    except Exception as e:                   # словарь не прочитан — регион печатается как введён
        print("акт: словарь регионов не прочитан:", type(e).__name__)
    return out


def _fold(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("ʻ", "'").replace("‘", "'").replace("`", "'")).strip().lower()


def region_code(text: str) -> Optional[str]:
    """Регион кодом (tashkent_region) или каноническим названием на любом из трёх языков → код; иначе None."""
    names = _region_names()
    s = _fold(text)
    if s in names:
        return s
    for code, by_lang in names.items():
        if any(_fold(v) == s for v in by_lang.values()):
            return code
    return None


def region_for_modules(must: dict) -> str:
    """
    Регион для risk_analytics, risk_stats и market_picture: они узнают регион по русскому названию
    (market_picture.resolve_region). Код из списка экрана (tashkent_region) → «Ташкентская область»;
    не из списка — как ввёл сотрудник.
    """
    code = must.get("region_code") or region_code(must.get("region") or "")
    if code:
        got = i18n.t(f"tg.act.reg.{code}", "ru")
        if got and not got.startswith("tg.act."):
            return got
    return must.get("region") or ""


def region_label(must: dict, lang: str) -> str:
    """Регион на языке акта, если есть перевод в словаре; иначе — как ввёл сотрудник."""
    code = must.get("region_code")
    if code:
        got = i18n.t(f"tg.act.reg.{code}", lang)
        if got and not got.startswith("tg.act."):
            return got
    return must.get("region") or ""


def trust_sources(items: list, upload: Optional[dict]) -> int:
    """
    Источник photo/plate/document/marking остаётся только у значения, которое сервер сам вернул в этой
    загрузке (совпали key, value и source). Загрузки нет, она чужая или истекла, значение изменено —
    источник input («введено сотрудником»). Возвращает, сколько источников понижено.
    """
    known = {(f.get("key"), f.get("value"), f.get("source")): f.get("file_id")
             for f in (upload or {}).get("fields") or []}
    n = 0
    for r in items:
        if r["source"] == "input":
            continue
        sig = (r["key"], r["value"], r["source"])
        if sig in known:
            r["file_id"] = known[sig]
        else:
            r["source"], r["file_id"] = "input", None
            n += 1
    return n


# --------------------------------------------------------------------------- #
#  Сборка акта: структурированные данные (язык не важен)
# --------------------------------------------------------------------------- #

def build_data(con, clean: dict, owner: str, lang: str) -> dict:
    ref = db.load_reference(con)
    st = load_settings(con)
    m, o = clean["must"], clean["optional"]
    product = m.get("product")
    cls = m["class_code"]

    # сессия фото: только своя и живая
    upload, session_missing = None, False
    if clean.get("session"):
        rows = db.rows(con, "SELECT * FROM act_uploads WHERE id=? AND owner_key=? AND expires_at > ?",
                       clean["session"], owner, _iso(_now()))
        if rows:
            upload = json.loads(rows[0]["result_json"] or "{}")
        if not rows or upload.get("kind") == "market":   # снимки объявлений — не осмотр объекта
            upload, session_missing = None, True
    upload = upload or {}
    downgraded = 0
    if clean.get("recognized") is not None:
        recognized = [dict(r) for r in clean["recognized"]]
        downgraded = trust_sources(recognized, upload)
    else:
        recognized = [{"key": f["key"], "value": f["value"], "source": f["source"], "note": f.get("note"),
                       "file_id": f.get("file_id")} for f in upload.get("fields") or []]
    damages = clean["damages"] if clean.get("damages") is not None else (upload.get("damages") or [])
    # фото и сканы, ушедшие в модель; документы с текстом разобраны отдельно и осмотром не считаются
    photos = int(upload.get("photo_files", upload.get("files")) or 0)
    ai_ok = bool(upload.get("ai")) and photos > 0
    views_seen = sorted(set((upload.get("views") or {}).values()))

    kind = o.get("object_kind") or upload.get("object_kind")
    kind_type = tx.OBJECT_KINDS[kind][0] if kind in tx.OBJECT_KINDS else None
    obj_text = (preferred(recognized, "object_type") or {}).get("value") or ""
    group = ae.object_group(cls, f"{kind_type or ''} {obj_text} {o.get('object_type') or ''}",
                            upload.get("class_hint") or "")
    otype = ae.match_object_type(ref, cls, kind_type, o.get("object_type"))
    # для сценариев и мероприятий вид объекта можно понять и по тексту документа («склад готовой продукции»)
    kind_ra = kind or _kind_from_text(f"{obj_text} {o.get('object_type') or ''}")
    otype_ra = otype or ae.match_object_type(ref, cls, (tx.OBJECT_KINDS.get(kind_ra) or (None,))[0], None)
    # продукт «спецтехника» без фото: для сценариев и мероприятий это спецтехника, а не легковой транспорт
    special_product = "спецтехник" in str((product or {}).get("name") or "").lower()
    group_ra = "special" if group == "vehicle" and special_product else group

    y = o.get("year")
    if y is None:
        pr = preferred(recognized, "year")
        y = ae.to_year(pr["value"]) if pr else None
    documents = "document" in views_seen
    if o.get("documents_provided") is not None:
        documents = bool(o["documents_provided"])
    location = o.get("location")
    if not location:
        loc_rec = (preferred(recognized, "location") or {}).get("value") or ""
        location = _location_from_text(loc_rec)

    risk = ae.risk_level({"inspected": ai_ok, "damages": damages,
                          "condition": o.get("condition") or (upload.get("condition") if ai_ok else None),
                          "year": y, "location": location, "guard": o.get("guard"),
                          "losses_count": o.get("losses_count"), "documents": documents,
                          "today": date.today()}, st)
    # запрос филиала: из ввода (экран подставил распознанное) или из своей загрузки бланка; источник каждого
    # поля и правки сотрудника решает сервер по своей загрузке (_trust_doc), поле source экрана не доверяется
    inclusive = bool(st["request_check"]["term_inclusive"])
    req, rq_trust = _trust_doc("rq", clean.get("request"), upload.get("branch_request"), session_missing, inclusive)
    term_from_request = False
    if o.get("term_days") is None and req and req.get("term_days"):
        # срок сотрудник не ввёл — берём весь срок договора из запроса (многолетний тоже; ставка годовая)
        o["term_days"] = int(req["term_days"])
        o["term_source"] = "request"
        term_from_request = True
    # договор: из ввода (экран подставил распознанное) или из своей загрузки договора
    ct, ct_trust = _trust_doc("ct", clean.get("contract"), upload.get("contract"), session_missing, inclusive)
    term_from_contract = False
    if o.get("term_days") is None and ct and ct.get("term_days"):
        o["term_days"] = int(ct["term_days"])
        o["term_source"] = "contract"
        term_from_contract = True
    term = o.get("term_days") or 365
    rate_res = ae.rate(ref, product, cls, risk["level"], m["sum_insured"], term, otype, o.get("payer_type"), st)
    statutory = rate_res["mode"] in ("statutory", "statutory_undefined")
    value = ae.value_check(m["sum_insured"], m["object_value"], st, o.get("price_new"), o.get("purchase_year"),
                           group, kind_type or obj_text)
    from .risk_analytics import load_thresholds            # пороги франшизы — только чтение
    th = load_thresholds(con)
    fr = ae.franchise({"small_count": o.get("small_count"), "dominant_risk": o.get("dominant_risk"),
                       "want_lower_premium": o.get("want_lower_premium")},
                      risk["level"], th, statutory, cls, m["sum_insured"])
    fr["thresholds_source"] = {k: v for k, v in (th.get("_source") or {}).items() if k in ("id", "what")}
    clauses = ae.clauses(group, clause_catalog())
    disc = ae.discrepancies(recognized, {"year": o.get("year"), "sum_insured": m["sum_insured"],
                                         "object_value": m["object_value"],
                                         "term_days": None if (term_from_request or term_from_contract)
                                         else o.get("term_days")})

    # сценарии, франшиза и мероприятия — существующими модулями на тех же входных данных, что акт
    block_errors = []
    # описание объекта в документе (запрос филиала, договор, распознанное): по нему — деятельность на объекте
    obj_doc = object_text_info(recognized, upload, req, ct)
    # регион для модулей аналитики — названием по-русски: код экрана (tashkent_region) они не знают
    region_ra = region_for_modules(m)
    ctx = ax.ra_context(con, cls=cls, product_code=m.get("product_code"), otype=otype_ra, group=group_ra, kind=kind_ra,
                        S=m["sum_insured"], V=m["object_value"], region=region_ra, term_days=o.get("term_days"),
                        year=y, o=o, recognized=recognized,
                        text=" ".join(x for x in (obj_doc.get("original"), obj_doc.get("translated"),
                                                  o.get("object_type")) if x))
    if not ctx.get("ok"):
        block_errors.append({"block": "risk_analytics", "error": ctx.get("error")})
    scen = ax.scenarios(ctx, cls, m["sum_insured"])
    if o.get("protection_ignored"):
        scen["assumptions"] = list(scen.get("assumptions") or []) + [{"code": "as_protection_ignored", "params": {}}]
    try:
        fr = ax.franchise(con, ctx, fr, rate_res, cls=cls, S=m["sum_insured"], level=risk["level"],
                          statutory=statutory, requested=o.get("deductible"), th=th)
    except Exception as e:               # блок не посчитан — акт всё равно формируется
        block_errors.append({"block": "franchise", "error": type(e).__name__})
        fr.update(status="error", applied=False, how=[], alternatives=[], error=type(e).__name__)
    premium_final = fr["premium_after"] if fr.get("applied") and fr.get("premium_after") is not None \
        else rate_res["premium"]
    rate_final = fr["rate_after"] if fr.get("applied") and fr.get("rate_after") is not None \
        else rate_res["applied_pct"]
    try:
        meas = ax.measures(con, ctx, rate_res, cls=cls, group=group_ra, kind=kind_ra, S=m["sum_insured"],
                           V=m["object_value"], o=o, location=location, statutory=statutory, th=th,
                           premium=premium_final)
    except Exception as e:
        block_errors.append({"block": "measures", "error": type(e).__name__})
        meas = {"items": [], "total": {"count": 0}, "error": type(e).__name__}
    if fr.get("status") not in ("error",):
        try:
            fr["alternatives"] = ax.alternatives(ctx, rate_res, m["sum_insured"], meas, th)
        except Exception as e:
            block_errors.append({"block": "alternatives", "error": type(e).__name__})
    # аналитика раздела 4 (30.09.2026): детализация и справочная техническая ставка — тариф акта не меняет
    try:
        analytics = aa.build(con, ctx, cls=cls, product_code=m.get("product_code"), region=region_ra,
                             S=m["sum_insured"], V=m["object_value"], term_days=term, rate_res=rate_res,
                             scen=scen, meas=meas, act_level=risk["level"], statutory=statutory, th=th,
                             group=group_ra)
        for e in analytics.get("errors") or []:
            block_errors.append({"block": "analytics:" + e["block"], "error": e["error"]})
    except Exception as e:
        block_errors.append({"block": "analytics", "error": type(e).__name__})
        analytics = {"available": False, "reason": "error", "calibrated": ae.CALIBRATED}
    analytics["activity"] = (ctx.get("must") or {}).get("activity") if ctx.get("ok") else None
    analytics["activity_source"] = (ctx.get("sources") or {}).get("activity")
    # тип объекта, на котором посчитана аналитика, и откуда он (default — принят по умолчанию)
    analytics["object_type"] = (ctx.get("must") or {}).get("object_type") if ctx.get("ok") else None
    analytics["object_type_source"] = (ctx.get("sources") or {}).get("object_type")

    present = {r["key"] for r in recognized if r.get("value")}
    if any(r["key"] == "manufacture_date" for r in recognized) or o.get("year"):
        present.add("year")
    if o.get("location"):
        present.add("location")
    if kind:
        present.add("object_type")
    if o.get("construction"):
        present.add("construction")
    if ((upload.get("contract") or {}).get("fields") or {}).get("address"):
        present.add("location")
    # набор полей — тот же, что строки раздела 1 (у оборудования и зданий — свои, 30.09.2026)
    missing = [k for k in (ROWS_BY_GROUP.get(group) or ae.FIELDS_BY_GROUP.get(group, [])) if k not in present]
    missing_key = [k for k in ae.KEY_FIELDS.get(group, []) if k in missing]
    missing_v = ae.missing_views(group, views_seen) if ai_ok else (ae.required_views(group) if photos else [])
    inspection = {"photos": photos, "ai": ai_ok, "ai_reason": upload.get("reason") if photos and not ai_ok else None,
                  "views_seen": views_seen, "required_views": ae.required_views(group),
                  "missing_views": missing_v, "damages": damages, "documents": documents,
                  "document_kinds": sorted(set((upload.get("document_kinds") or {}).values())),
                  "recognized": bool(recognized), "session": clean.get("session"),
                  "session_missing": session_missing, "upload_lang": upload.get("lang"),
                  "parsed_docs": int(upload.get("parsed_docs") or 0)}
    dec = ae.decision(risk, rate_res, value, fr, disc, inspection, missing_key, st)
    if fr.get("applied"):
        # франшиза сотрудника — условие договора: андеррайтер подтверждает, «принять без оговорок» уже нельзя
        dec["checks"].append({"code": "c_fr_applied", "params": {}})
        if dec["code"] == "d_accept":
            dec["code"] = "d_accept_with_clauses"
    market = None
    if clean.get("market"):
        market = market_block(con, clean["market"], owner, st, m, o, recognized, kind, cls, group, y)
        mchecks = am.decision_checks(market)
        dec["checks"] += mchecks
        if mchecks and dec["code"] == "d_accept":
            dec["code"] = "d_accept_with_clauses"
    rc = ae.request_check(req, rate_res=rate_res, rate_final=rate_final if rate_res["mode"] != "undefined" else None,
                          premium_final=premium_final, sum_insured=m["sum_insured"], object_value=m["object_value"],
                          value=value, fr=fr, term_from_request=term_from_request, settings=st)
    _attach_trust(rc, rq_trust)
    rq_checks = ae.request_checks(rc)
    if (rq_trust or {}).get("edits"):
        rq_checks.append({"code": "c_rq_edits", "params": {"n": len(rq_trust["edits"])}})
    if rq_checks:
        # тариф ниже минимума или расхождение с запросом — «принять без оговорок» уже нельзя
        dec["checks"] += rq_checks
        if dec["code"] == "d_accept":
            dec["code"] = "d_accept_with_clauses"
    cc = ae.contract_check(ct, rate_res=rate_res, rate_final=rate_final if rate_res["mode"] != "undefined" else None,
                           premium_final=premium_final, sum_insured=m["sum_insured"], object_value=m["object_value"],
                           value=value, fr=fr, term_from_contract=term_from_contract, settings=st)
    _attach_trust(cc, ct_trust)
    ct_checks = ae.request_checks(cc, "ct")
    if (ct_trust or {}).get("edits"):
        ct_checks.append({"code": "c_ct_edits", "params": {"n": len(ct_trust["edits"])}})
    # запрос филиала против договора: объект (кадастр, вид) — присланный, а при живой загрузке — сохранённый
    xc = cr.cross_check(_with_object(req, (upload.get("branch_request") or {}).get("fields")),
                        _with_object(ct, (upload.get("contract") or {}).get("fields")),
                        float(st["request_check"]["premium_tolerance"]))
    x_codes = [i["code"] for i in (xc or {}).get("items") or [] if i["verdict"] == "differs"]
    if x_codes:
        ct_checks.append({"code": "c_x", "params": {"codes": x_codes}})
    if ct_checks:
        # расхождение с договором или нет существенного условия — «принять без оговорок» уже нельзя
        dec["checks"] += ct_checks
        if dec["code"] == "d_accept":
            dec["code"] = "d_accept_with_clauses"
    cls_row = db.rows(con, "SELECT name FROM classes WHERE code=?", cls)
    return {
        "insurer": insurer_name(st),
        "must": {"product_code": m.get("product_code"), "product_name": (product or {}).get("name"),
                 "class_code": cls, "class_name": cls_row[0]["name"] if cls_row else None,
                 "sum_insured": m["sum_insured"], "object_value": m["object_value"], "region": m["region"],
                 "region_code": m.get("region_code"), "product_classes": m.get("product_classes") or []},
        "multi_class": len(m.get("product_classes") or []) > 1,
        "sources_downgraded": downgraded,
        "optional": {k: v for k, v in o.items() if v is not None},
        "group": group, "object_kind": kind, "object_type_ref": otype,
        "recognized": recognized, "inspection": inspection, "risk": risk, "rate": rate_res,
        "value": value, "franchise": fr, "clauses": clauses, "discrepancies": disc, "decision": dec,
        "missing": missing, "tariff_version_id": tariff_version(con, "регулятор" if statutory else "компания"),
        "settings_id": (st.get("_source") or {}).get("id"), "calibrated": ae.CALIBRATED,
        "dropped_pd": clean.get("dropped_pd") or 0,
        # дополнения 29.09.2026: одна премия на весь акт — с учётом применённой франшизы
        "premium_final": {"amount": premium_final, "rate_pct": rate_final,
                          "franchise_applied": bool(fr.get("applied"))},
        "scenarios": scen, "measures": meas, "block_errors": block_errors,
        "market": market,
        "request": req, "request_check": rc,
        "contract": ct, "contract_check": cc, "cross_check": xc,
        # дополнения 30.09.2026: аналитика раздела 4, описание объекта из документа, адрес объекта
        "analytics": analytics, "object_doc": obj_doc,
        "object_facts": {"address": ((upload.get("contract") or {}).get("fields") or {}).get("address")},
    }


def _text_lang(s: str) -> Optional[str]:
    """Язык короткого описания: узбекские буквы (ў қ ғ ҳ, oʻ gʻ) — uz; кириллица — ru; латиница — en/uz."""
    s = str(s or "")
    if re.search(r"[ўқғҳЎҚҒҲ]", s):
        return "uz-cyrl"                     # узбекская кириллица: для акта на латинице — другой алфавит
    if re.search(r"[А-Яа-яЁё]", s):
        return "ru"
    if re.search(r"(o|g)[ʻ'‘`]|\b(uchun|va|mahsulot\w*|bino\w*|ombor\w*|uskuna\w*|ishlab|chiqarish|yer|maydon\w*)\b",
                 s, re.I):
        return "uz"
    if re.search(r"[A-Za-z]", s):
        return "en"
    return None


def object_text_info(recognized: list, upload: dict, req: Optional[dict], ct: Optional[dict]) -> dict:
    """
    Описание объекта из документа: исходный текст, перевод модели (для скана запроса филиала — если модель
    его вернула; язык перевода = язык загрузки), откуда текст. Перевод правилами не выдумывается.
    """
    best = preferred(recognized, "object_type") or {}
    doc_src = best.get("source") in ("document", "document_ai")
    brf = (upload.get("branch_request") or {}).get("fields") or {}
    ctf = (upload.get("contract") or {}).get("fields") or {}
    original = (best.get("value") if doc_src else None) or brf.get("object_description") \
        or ctf.get("object_description") or (req or {}).get("object_description") \
        or (ct or {}).get("object_description")
    translated = brf.get("object_description_translated") or ctf.get("object_description_translated")
    return {"original": original, "translated": translated,
            "translated_lang": tx.lang_of(upload.get("lang")) if translated else None,
            "original_lang": _text_lang(original) if original else None,
            "from_document": bool(original) and (doc_src or not best),
            "scan": (upload.get("branch_request") or {}).get("source") == "photo"}


def _request_from_upload(b: dict) -> Optional[dict]:
    """Запрос филиала из своей живой загрузки: те же числа, что сервер разобрал сам (источник session)."""
    f = (b or {}).get("fields") or {}
    # те же поля, что optional.request экрана (со стоимостью и объектом — для сверки с договором)
    req = br.request_of(f, "session")
    if all(req[k] is None for k in ("tariff_pct", "premium", "franchise", "term_days")):
        return None
    return req


def _contract_from_upload(b: dict) -> Optional[dict]:
    """Договор из своей живой загрузки: те же поля, что сервер разобрал сам (источник session)."""
    f = (b or {}).get("fields") or {}
    ct = cr.request_of(f, "session")
    ct["covered_risks"] = list(f.get("covered_risks") or [])
    ct["exclusions"] = list(f.get("exclusions") or [])
    ct["cadastre_no"] = f.get("cadastre_no")
    if ct.get("currency") not in (None, "UZS"):
        # суммы не в сумах сверять с расчётом акта нельзя — только условия без денег
        ct.update(sum_insured=None, premium=None, object_value=None, payments=[], items=[])
    if all(ct.get(k) in (None, []) for k in ("tariff_pct", "premium", "franchise", "term_days", "sum_insured",
                                             "contract_no", "object_description", "covered_risks")):
        return None
    return ct


def _with_object(doc: Optional[dict], fields: Optional[dict]) -> Optional[dict]:
    """Условия документа + кадастр, вид и описание объекта (для сверки запроса с договором): при своей живой
    загрузке этого документа — сохранённые при загрузке (как в сверке экрана), иначе — присланные."""
    if not doc:
        return None
    keys = ("cadastre_no", "class_hint", "object_kind", "object_description")
    if fields:
        return {**doc, **{k: fields.get(k) for k in keys}}
    return dict(doc)


def _attach_trust(rc: dict, trust: Optional[dict]) -> None:
    """Источник условий и правки сотрудника — в сверку (request_check / contract_check), для акта и экрана."""
    if rc.get("available") and trust:
        rc.update(source_kind=trust["source_kind"], origin=trust["origin"], edits=trust["edits"],
                  field_sources=trust["field_sources"], doc_missing=trust["doc_missing"])


def _shots_upload(con, sid: Optional[str], owner: str) -> Optional[dict]:
    """Своя живая загрузка снимков объявлений (kind = market) или None."""
    if not sid:
        return None
    rows = db.rows(con, "SELECT result_json FROM act_uploads WHERE id=? AND owner_key=? AND expires_at > ?",
                   sid, owner, _iso(_now()))
    res = json.loads(rows[0]["result_json"] or "{}") if rows else {}
    return res if res.get("kind") == "market" else None


def fx_offline(con, as_of: date) -> Optional[dict]:
    """Курс без сети: только ручной курс заказчика из настроек оценки (valuation_settings.fx_rate_manual)."""
    from . import valuation
    raw = valuation.setting(con, valuation.SETTING_FX_MANUAL)
    try:
        v = float(str(raw).replace(" ", "").replace(",", ".")) if raw not in (None, "") else 0.0
    except ValueError:
        v = 0.0
    return {"rate": v, "by": "manual_setting", "as_of": as_of.isoformat()} if v > 0 else None


def market_block(con, mk: dict, owner: str, st: dict, m: dict, o: dict, recognized: list, kind, cls, group, y) -> dict:
    """
    Оценка по объявлениям для акта (в сеть не ходит). Объявления — из ввода сотрудника; источник каждого
    сверяется со своей загрузкой снимков (am.trust_listings), правки сотрудника считаются по всей загрузке.
    Курс: из загрузки снимков (он получен из источника проекта) → курс ЦБ, который экран получил от сервера
    (fx.by = cbu, сверка с памятью сервера) → ручной курс заказчика в настройках → курс, введённый сотрудником;
    иначе доллары не считаются.
    """
    shots = _shots_upload(con, mk.get("shots_session"), owner)
    items = [dict(r) for r in mk["listings"]]
    src = am.trust_listings(items, shots)
    edits = src.pop("edits")
    shot_date = date.today()
    if shots and shots.get("shot_date"):
        try:
            shot_date = date.fromisoformat(shots["shot_date"])
        except ValueError:
            pass
    claim = mk.get("fx_claim")
    fx = (shots or {}).get("fx") if ((shots or {}).get("fx") or {}).get("by") in ("cbu", "manual_setting") else None
    if not fx and claim and claim.get("by") == "cbu":
        fx = fx_verify(claim)
        fx = fx if fx["by"] == "cbu" else (fx_offline(con, shot_date) or fx)
    # ручной курс настроек сверяется с настройками же: если его там уже нет, курс экрана не берём
    fx = fx or fx_offline(con, shot_date)
    # курс сотрудника — только тот, что он ввёл сам (fx.by = employee или старое поле usd_rate)
    emp_rate = claim["rate"] if claim and claim.get("by") == "employee" else mk.get("usd_rate")
    if not fx and emp_rate:
        fx = {"rate": emp_rate, "by": "employee", "as_of": (claim or {}).get("as_of") or date.today().isoformat()}
    rate = (fx or {}).get("rate")
    est = ae.market_estimate(items, declared=m["object_value"], sum_insured=m["sum_insured"], settings=st,
                             shot_date=shot_date, usd_rate=rate)
    if shots:
        # медиана без правок: объявления, как их прочитала модель, по тем же правилам и курсу
        orig = [dict(r, source="shot") for r in shots.get("listings") or []]
        est0 = ae.market_estimate(orig, settings=st, shot_date=shot_date, usd_rate=rate)
        est["median_original"] = est0["median"]
    q = {"brand": (preferred(recognized, "brand") or {}).get("value"),
         "model": (preferred(recognized, "model") or {}).get("value"),
         "year": y, "object_kind": kind, "class_code": cls, "group": group}
    q.update({k: v for k, v in ((shots or {}).get("query") or {}).items() if v and not q.get(k)})
    # ссылки поиска собираются при показе акта на его языке (по запросу query); сервер по ним не ходит
    est.update(fx=fx, query=q, shots_session=mk.get("shots_session"),
               shots_missing=bool(mk.get("shots_session")) and shots is None,
               source={**src, "shots": sum(1 for r in items if r.get("source") == "shot" and not r.get("removed"))},
               edits=edits, dropped_pd=mk.get("dropped_pd") or 0)
    return est


_KIND_BY_TYPE = {"Склад": "warehouse", "Офис": "office", "Магазин": "shop", "Производство": "production",
                 "Гостиница": "hotel", "Жильё": "dwelling", "Машины и оборудование": "equipment"}


def _kind_from_text(text: str) -> Optional[str]:
    """Вид объекта по словам документа — только однозначное совпадение (analysis_docs._one)."""
    from .analysis_docs import OBJECT_TYPE_WORDS, _one
    return _KIND_BY_TYPE.get(_one(text, OBJECT_TYPE_WORDS) or "")


def _location_from_text(text: str) -> Optional[str]:
    """Место с фото (словами на любом из трёх языков) → код. Не узнали — None, а не догадка."""
    s = str(text or "").lower()
    table = (("guarded", ("охраня", "qoʻriqlan", "qoriqlan", "guarded", "fenced")),
             ("closed_storage", ("закрыт", "помещен", "ангар", "гараж", "yopiq", "indoor", "garage", "hangar")),
             ("construction", ("строй", "стройк", "qurilish", "construction")),
             ("port", ("порт", "port")),
             ("open_area", ("открыт", "площадк", "ochiq", "open", "yard")))
    for code, words in table:
        if any(w in s for w in words):
            return code
    return None


# --------------------------------------------------------------------------- #
#  Сборка акта: текст на нужном языке
# --------------------------------------------------------------------------- #

def _and(lang: str) -> str:
    return {"ru": " и ", "uz": " va ", "en": " and "}[tx.lang_of(lang)]


def _class_label(code: Optional[str], name_ru: Optional[str], lang: str) -> str:
    if not code:
        return t("na", lang)
    name = name_ru if lang == "ru" else i18n.t(f"tg.wz.cls.{code}", lang)
    if not name or name.startswith("tg.wz"):
        name = name_ru or ""
    return f"{code} — {name}" if name else code


def _fmt_param(key: str, v, lang: str):
    if key in ("base", "rate", "min", "calc"):
        return pct(v, lang)
    if key == "adj":
        return pct(v, lang)
    if key in ("sum", "premium", "diff", "price"):
        return money(v, lang)
    if key == "level":
        return tx.label(tx.LEVEL_LABELS, v, lang)
    if key == "place":
        return tx.label(tx.LOCATION_LABELS, v, lang)
    if key == "otype":
        return _otype_label(v, lang)
    return v


def _otype_label(otype: str, lang: str) -> str:
    """Тип объекта справочника (по-русски) → подпись на языке акта через OBJECT_KINDS."""
    for _code, (ref_type, labels) in tx.OBJECT_KINDS.items():
        if ref_type and ref_type == otype:
            return labels.get(lang) or labels["ru"]
    return otype


def _text(item: dict, lang: str) -> str:
    params = {k: _fmt_param(k, v, lang) for k, v in (item.get("params") or {}).items()}
    return t(item["code"], lang, **params)


def _check_text(c: dict, lang: str, group: Optional[str] = None) -> str:
    p = c.get("params") or {}
    if c["code"] == "c_disc":
        return t("c_disc", lang, label=tx.field_label(p["key"], lang, group))
    if c["code"] == "c_views":
        return t("c_views", lang, views=", ".join(tx.label(tx.VIEW_LABELS, v, lang) for v in p["views"]))
    if c["code"] == "c_missing":
        return t("c_missing", lang, what=", ".join(tx.field_label(k, lang, group).lower() for k in p["keys"]))
    if c["code"].startswith("c_market_"):
        return am.check_text(c, lang) or t(c["code"], lang)
    if c["code"].startswith("c_rq_"):
        return t(c["code"], lang, **_rq_params(c["code"][2:], p, lang))
    if c["code"] == "c_ct_essentials":
        return t(c["code"], lang, what=", ".join(tx.label(tx.CT_ESSENTIAL_LABELS, x, lang) for x in p.get("missing") or []))
    if c["code"].startswith("c_ct_"):
        return t(c["code"], lang, **_rq_params(c["code"][2:], p, lang))
    if c["code"] == "c_x":
        return t("c_x", lang, what=", ".join(tx.label(tx.X_LABELS, x, lang).lower() for x in p.get("codes") or []))
    return t(c["code"], lang)


def _disc_text(d: dict, lang: str, group: Optional[str] = None) -> str:
    parts = []
    for g in d["values"]:
        where = _and(lang).join(tx.label(tx.SOURCE_IN, s, lang) for s in g["sources"])
        parts.append(f"{where} {g['value']}")
    return t("disc_line", lang, label=tx.field_label(d["key"], lang, group), values=", ".join(parts))


def _row(label: str, value, note=None) -> dict:
    return {"label": label, "value": value, "note": note}


# строки раздела 1 по виду объекта (30.09.2026): у оборудования — паспорт оборудования, у зданий — адрес, кадастр,
# площади, конструкция, год постройки, этажность; у техники и транспорта — как раньше. «Данные недоступны» — не
# больше трёх строк (сначала ключевые признаки), остальное — одной строкой «Не указано: …».
ROWS_BY_GROUP = {
    "equipment": ["object_type", "manufacturer", "model", "serial_no", "year", "location"],
    "property": ["object_type", "location", "cadastre_no", "total_area", "useful_area", "land_area", "construction",
                 "year", "floors"],
}
EXTRA_IF_PRESENT = {"equipment": ["brand", "engine_power", "dimensions"], "property": ["reg_no"]}
MAX_NA_ROWS = 3
_GROUP_LABELS = {"equipment": {"object_type": "lbl_equipment_name", "location": "lbl_install_place"},
                 "property": {"object_type": "lbl_building_kind", "location": "lbl_address", "floors": "lbl_floors"}}


def _s1_label(key: str, lang: str, group: str) -> str:
    code = (_GROUP_LABELS.get(group) or {}).get(key)
    return t(code, lang) if code else tx.field_label(key, lang, group)


def _kind_label(D: dict, lang: str) -> Optional[str]:
    """Вид объекта на языке акта по словарю: вид (склад, оборудование…) + уточнение (склад-холодильник) +
    деятельность по описанию (пищевое производство). Перевод текста документа это не заменяет."""
    kind = D.get("object_kind")
    doc = D.get("object_doc") or {}
    an = D.get("analytics") or {}
    if not kind:
        return None
    if kind == "warehouse" and ax.cold_store(" ".join(x for x in (doc.get("original"), doc.get("translated")) if x)):
        base = tx.label(tx.OBJECT_SUBKINDS, "cold_store", lang)
    else:
        base = tx.label({k: v[1] for k, v in tx.OBJECT_KINDS.items()}, kind, lang)
    if an.get("activity_source") == "text" and an.get("activity") and kind in ("equipment", "production", "other"):
        base += " — " + tx.label(tx.OPTION_LABELS, "activity:" + an["activity"], lang)
    return base


def _object_type_row(D: dict, lang: str, label: str, best: Optional[dict]) -> Optional[dict]:
    """
    Описание объекта из документа на языке акта: перевод модели (скан) → вид объекта по словарю, исходный текст
    в примечании → исходный текст с пометкой «текст документа». Текст на языке акта показывается как есть.
    """
    doc = D.get("object_doc") or {}
    orig = doc.get("original")
    if not orig or not doc.get("from_document") or (best and best.get("value") != orig):
        return None
    src_lang = doc.get("original_lang")
    if src_lang == lang or (src_lang is None):
        return None
    cut = orig.strip(" «»\"'“”")
    cut = cut if len(cut) <= 200 else cut[:199] + "…"
    if doc.get("translated") and doc.get("translated_lang") == lang:
        return _row(label, doc["translated"], t("s1_by_model", lang) + "; " + t("s1_doc_text", lang, text=cut))
    kl = _kind_label(D, lang)
    if kl:
        return _row(label, kl, t("s1_by_dict", lang) + "; " + t("s1_doc_text", lang, text=cut))
    return _row(label, orig, t("s1_doc_text_mark", lang) + ", " + t("check_mark", lang))


def _doc_kind_label(kind: str, lang: str) -> str:
    """
    Вид документа на языке акта. Загрузка сохраняет подпись на своём языке («запрос филиала», «filial soʻrovi»)
    или код (branch_request) — ищем его в act_texts.DOC_KIND_LABELS по коду и по подписи на любом языке;
    не нашли (вид назвала модель своими словами) — как есть.
    """
    s = str(kind or "").strip()
    low = s.lower()
    for code, by_lang in tx.DOC_KIND_LABELS.items():
        if low == code.lower() or any(low == str(v).lower() for v in by_lang.values()):
            return tx.label(tx.DOC_KIND_LABELS, code, lang)
    return s


def _default_otype(D: dict) -> Optional[str]:
    """Тип объекта, принятый по умолчанию для расчёта аналитики (risk_analytics), или None."""
    an = D.get("analytics") or {}
    if an.get("object_type_source") == "default" and an.get("object_type"):
        return an["object_type"]
    return None


def _object_rows(D: dict, lang: str) -> list:
    group = D["group"]
    opt, rec = D["optional"], D["recognized"]
    kind = D.get("object_kind")
    NA = t("na", lang)
    keys = ROWS_BY_GROUP.get(group) or ae.FIELDS_BY_GROUP.get(group, [])
    keys = list(keys) + [k for k in EXTRA_IF_PRESENT.get(group, []) if preferred(rec, k)]
    key_first = ae.KEY_FIELDS.get(group, [])
    rows, missing = [], []
    for key in keys:
        label = _s1_label(key, lang, group)
        if key == "location" and opt.get("location"):
            rows.append(_row(label, tx.label(tx.LOCATION_LABELS, opt["location"], lang),
                             tx.label(tx.SOURCE_LABELS, "input", lang)))
            continue
        if key == "location" and group == "property" and (D.get("object_facts") or {}).get("address"):
            rows.append(_row(label, D["object_facts"]["address"],
                             tx.label(tx.SOURCE_LABELS, "document", lang) + ", " + t("check_mark", lang)))
            continue
        if key == "year" and opt.get("year"):
            rows.append(_row(label, str(opt["year"]), tx.label(tx.SOURCE_LABELS, "input", lang)))
            continue
        if key == "construction" and opt.get("construction"):
            rows.append(_row(label, tx.label(tx.RA_VALUE_LABELS, opt["construction"], lang),
                             tx.label(tx.SOURCE_LABELS, "input", lang)))
            continue
        best = preferred(rec, key)
        if key == "object_type":
            doc_row = _object_type_row(D, lang, label, best)
            if doc_row:
                rows.append(doc_row)
                continue
        if key == "object_type" and not best and kind:
            rows.append(_row(label, _kind_label(D, lang) or NA,
                             tx.label(tx.SOURCE_LABELS, "input" if opt.get("object_kind") else "photo", lang)
                             + ", " + t("check_mark", lang)))
            continue
        dflt = _default_otype(D)
        if key == "object_type" and not best and dflt:
            # вида объекта нет ни во вводе, ни в документе: аналитика посчитана на типе по умолчанию — так и пишем
            rows.append(_row(label, t("s1_kind_default", lang, v=_otype_label(dflt, lang)),
                             t("s1_kind_default_note", lang)))
            continue
        if not best:
            rows.append(None)
            missing.append((key, label))
            continue
        note = tx.label(tx.SOURCE_LABELS, best["source"], lang) + ", " + t("check_mark", lang)
        others = [r for r in rec if (r["key"] == key or (key == "year" and r["key"] == "manufacture_date"))
                  and r is not best and not ae._same(key, r["value"], best["value"])]
        if others:
            note += "; " + t("also_in", lang, what="; ".join(
                f"{r['value']} ({tx.label(tx.SOURCE_LABELS, r['source'], lang)})" for r in others[:3]))
        rows.append(_row(label, best["value"], note))
    if group not in ROWS_BY_GROUP:
        for key in ae.EXTRA_ROW_KEYS:
            best = preferred(rec, key)
            if best:
                rows.append(_row(tx.field_label(key, lang, group), best["value"],
                                 tx.label(tx.SOURCE_LABELS, best["source"], lang) + ", " + t("check_mark", lang)))
    # «данные недоступны» — не больше трёх строк: сначала ключевые признаки, остальное одной строкой
    order = sorted(missing, key=lambda kl: (kl[0] not in key_first, keys.index(kl[0])))
    keep = {k for k, _l in order[:MAX_NA_ROWS]}
    out, rest = [], []
    mi = 0
    for row in rows:
        if row is not None:
            out.append(row)
            continue
        key, label = missing[mi]
        mi += 1
        if key in keep:
            out.append(_row(label, NA))
        else:
            rest.append(label.lower() if lang != "en" else label[:1].lower() + label[1:])
    if rest:
        out.append(_row(t("s1_not_specified", lang), ", ".join(rest)))
    return out


def render(D: dict, lang: str, meta: dict) -> dict:
    """Акт на языке lang из структурированных данных. Все цифры — из расчёта, слова — из act_texts."""
    lang = tx.lang_of(lang)
    NA = t("na", lang)
    ins, risk, rate_res, value, fr = D["inspection"], D["risk"], D["rate"], D["value"], D["franchise"]
    must, opt = D["must"], D["optional"]
    rec = D["recognized"]

    # ---------- раздел 1 ----------
    rows1 = [_row(t("class", lang), _class_label(must["class_code"], must.get("class_name"), lang)),
             _row(t("product", lang), (f"{must['product_code']} — {must['product_name']}" if lang == "ru"
                                       else must["product_code"]) if must.get("product_code") else NA)]
    ctd = D.get("contract") or {}
    if ctd.get("contract_no") or ctd.get("contract_date"):
        # номер и дата договора страхования — из загруженного договора или ввода сотрудника
        no, dt = ctd.get("contract_no"), _ddmmyyyy(ctd["contract_date"]) if ctd.get("contract_date") else None
        val = t("ct_row_value", lang, no=no, date=dt) if no and dt else \
            t("ct_row_no", lang, no=no) if no else t("ct_row_date", lang, date=dt)
        # источник номера — по полю (номер исправлен сотрудником — «введено сотрудником»)
        fs = (D.get("contract_check") or {}).get("field_sources") or {}
        src = fs.get("contract_no") or fs.get("contract_date") or ctd.get("source") or "input"
        rows1.append(_row(t("ct_row", lang), val, tx.label(tx.CT_SOURCE_LABELS, src, lang)))
    rows1 += _object_rows(D, lang)
    rows1.append(_row(t("region", lang), region_label(must, lang)))
    if opt.get("guard") is not None:
        rows1.append(_row(t("guard", lang), t("yes" if opt["guard"] else "no", lang)))
    s1 = {"n": 1, "title": t("s1", lang), "paragraphs": [], "rows": rows1}

    # ---------- раздел 2 ----------
    p2, rows2 = [], []
    if not ins["photos"]:
        p2.append(t("p_no_photos", lang))
        rows2.append(_row(t("views_seen", lang), t("no_inspection", lang)))
        rows2.append(_row(t("damages", lang), t("no_inspection", lang)))
    elif not ins["ai"]:
        p2.append(t("p_ai_failed", lang, n=ins["photos"], reason=ins.get("ai_reason") or NA))
        rows2.append(_row(t("files_count", lang), str(ins["photos"])))
        rows2.append(_row(t("views_seen", lang), t("no_inspection", lang)))
        rows2.append(_row(t("damages", lang), t("no_inspection", lang)))
    else:
        p2.append(t("p_inspected", lang, n=ins["photos"]))
        seen = [tx.label(tx.VIEW_LABELS, v, lang) for v in ins["views_seen"] if v != "other"]
        rows2.append(_row(t("views_seen", lang), ", ".join(seen) or NA))
        if ins["required_views"]:
            rows2.append(_row(t("views_missing", lang),
                              ", ".join(tx.label(tx.VIEW_LABELS, v, lang) for v in ins["missing_views"])
                              or t("views_all", lang)))
        else:
            rows2.append(_row(t("views_missing", lang), t("views_none_needed", lang)))
        if ins["damages"]:
            rows2.append(_row(t("damages", lang), "; ".join(
                d["what"] + (f" ({d['where']})" if d.get("where") else "") for d in ins["damages"])))
        else:
            rows2.append(_row(t("damages", lang), t("damages_none", lang)))
    if ins.get("parsed_docs"):
        rows2.append(_row(t("docs_parsed", lang), str(ins["parsed_docs"])))
    kinds2 = []
    for k in ins.get("document_kinds") or []:
        lk = _doc_kind_label(k, lang)
        if lk not in kinds2:
            kinds2.append(lk)
    rows2.append(_row(t("documents", lang), (t("docs_given", lang) + (
        " (" + ", ".join(kinds2) + ")" if kinds2 else ""))
        if ins["documents"] else t("docs_not_given", lang)))
    s2 = {"n": 2, "title": t("s2", lang), "paragraphs": p2, "rows": rows2}

    # ---------- раздел 3 ----------
    ratio = pct(value["ratio_pct"], lang, 2)
    legal = tx.label(tx.LEGAL_REFS, value["legal_ref"], lang) if value.get("legal_ref") else None
    if value["verdict"] == "over":
        vtext = t("v_over", lang, diff=money(value["diff"], lang), ref=legal)
    elif value["verdict"] == "under":
        vtext = t("v_under", lang, ratio=ratio, ref=legal)
    elif value.get("legal_ref"):
        vtext = t("v_normal", lang, ratio=ratio) + " " + t("v_under_small", lang, ratio=ratio, ref=legal)
    else:
        vtext = t("v_normal", lang, ratio=ratio)
    M = D.get("market")
    # один итоговый вывод: при «уточнить стоимость» — сумма к заявленной и к уточнённой стоимости и итог
    final = am.final_verdict(value, M)
    vtext = am.final_text(value, M, lang, vtext)
    decl = am.declared_text(opt.get("declared_value_original"), must["object_value"], M, lang)
    rows3 = [_row(t("sum_insured", lang), money(must["sum_insured"], lang)),
             _row(t("object_value", lang), money(must["object_value"], lang))]
    if decl:
        rows3.append(_row(decl[0], money(opt["declared_value_original"], lang), decl[1]))
    rows3 += [_row(t("ratio", lang), ratio),
              _row(t("verdict", lang), vtext)]
    p3 = []
    dep = value.get("depreciated")
    if dep:
        rows3.append(_row(t("depreciated", lang), money(dep["value"], lang),
                          t("depr_how", lang, price=money(dep["price_new"], lang),
                            rate=pct(dep["wear_pct_per_year"], lang), years=dep["years"])))
        p3.append(t("depr_note", lang))
    if M and M.get("query") is not None:
        M = dict(M, links=am.search_links(M["query"], lang))
    mv = am.market_view(M, lang)
    rows3 += mv["rows"]
    s3 = {"n": 3, "title": t("s3", lang), "paragraphs": p3, "rows": rows3, "source_lines": mv["source_lines"],
          "lists": mv["lists"]}

    # ---------- раздел 4 ----------
    level_label = tx.label(tx.LEVEL_LABELS, risk["level"], lang)
    factors = [_text(f, lang) for f in risk["factors"]]
    rule = risk["rule"]
    minus = lambda x: str(x).replace("-", "−")
    rule_text = t("level_rule", lang, net=minus(risk["net"]), low=minus(rule["low_max_net"]),
                  high=minus(rule["high_min_net"]), k=rule["min_known"])
    mode = rate_res["mode"]
    pf = _premium_final(D)
    rows4 = [_row(t("level", lang), level_label, t("uncalibrated", lang))]
    if mode == "tariff":
        rows4 += [_row(t("applied_rate", lang), pct(rate_res["applied_pct"], lang),
                       t("min_applied", lang) if rate_res["min_applied"] else None)]
        if pf["franchise_applied"]:
            rows4.append(_row(t("rate_with_fr", lang), pct(pf["rate_pct"], lang),
                              t("min_applied", lang) if fr.get("floor_applied") else None))
        prem_note = t("premium_term", lang, days=rate_res["term_days"])
        if pf["franchise_applied"]:
            prem_note += "; " + t("premium_no_fr", lang, before=money(rate_res["premium"], lang))
        rows4 += [_row(t("base_rate", lang), pct(rate_res["base_pct"], lang)),
                  _row(t("adj", lang), "+" + pct(rate_res["adj_pct"], lang), t("uncalibrated", lang)),
                  _row(t("min_rate", lang), pct(rate_res["min_pct"], lang) if rate_res["min_pct"] is not None
                       else NA),
                  _row(t("premium", lang), money(pf["amount"], lang), prem_note)]
    elif mode == "statutory":
        rows4 += [_row(t("applied_rate", lang), pct(rate_res["applied_pct"], lang), t("rate_by_act", lang)),
                  _row(t("adj", lang), t("adj_none_statutory", lang)),
                  _row(t("premium", lang), money(rate_res["premium"], lang),
                       t("premium_term", lang, days=rate_res["term_days"]))]
    else:
        rows4 += [_row(t("applied_rate", lang), t("rate_undefined", lang)),
                  _row(t("premium", lang), NA)]
    fr_text = _fr_text(fr, lang)
    rows4.append(_row(t("franchise", lang), fr_text))
    scv = _scenarios_view(D.get("scenarios"), must, lang)
    rows4 += scv["rows"]
    lists4 = [{"title": t("factors", lang), "items": factors + [rule_text]},
              {"title": t("how_title", lang),
               "items": [_text(h, lang) for h in rate_res["how"]]}]
    # аналитика риска (30.09.2026): резюме первым абзацем, таблицы — после «Как посчитан тариф»
    try:
        anv = _analytics_view(D, lang)
    except Exception as e:                   # сбой показа аналитики не роняет акт: остальные разделы на месте
        print("акт: аналитика раздела 4 не показана:", type(e).__name__, e)
        anv = {"summary": None, "lists": [], "json": {"available": False, "reason": "render_error",
                                                      "calibrated": ae.CALIBRATED}}
    lists4 += anv["lists"]
    if fr.get("grounds"):
        lists4.append({"title": t("fr_grounds", lang), "items": [_text(g, lang) for g in fr["grounds"]]})
    fr_how = [_fr_how_text(h, lang) for h in fr.get("how") or []]
    if fr_how:
        lists4.append({"title": t("fr_how_title", lang), "items": fr_how})
    fr_alts = [_alt_view(a, lang) for a in fr.get("alternatives") or []]
    if fr_alts:
        lists4.append({"title": t("fr_alt_title", lang), "items": [a["text"] for a in fr_alts]})
    lists4 += scv["lists"]
    p4 = [anv["summary"]] if anv.get("summary") else []
    if D.get("multi_class"):
        p4.append(t("multi_class_note", lang, classes=", ".join(must.get("product_classes") or [])))
    if not fr.get("needed") and fr.get("code") == "fr_not_needed" and fr.get("status") in (None, "none"):
        p4.append(fr_text + ": " + t("fr_none_grounds", lang) + ". " + t("fr_alt", lang))
    if D["clauses"]:
        lists4.append({"title": t("clauses", lang),
                       "items": [f"{c.get(lang) or c.get('ru')} ({t('expert', lang)})" for c in D["clauses"]]})
    p4.append(scv["paragraph"])
    rcv = _request_check_view(D.get("request_check"), lang)
    if rcv["json"]["available"]:
        # подраздел «Сверка с запросом филиала»: итог, строки сверки, как считали
        lists4.append({"title": t("rq_title", lang), "items": rcv["lines"]})
        lists4.append({"title": t("rq_how_title", lang), "items": rcv["json"]["how"]})
    ccv = _request_check_view(D.get("contract_check"), lang, "ct", D.get("contract"))
    if ccv["json"]["available"]:
        # подраздел «Сверка с договором»: итог, строки сверки, риски и исключения, как сверено
        lists4.append({"title": t("ct_title", lang), "items": ccv["lines"]})
        lists4.append({"title": t("ct_how_title", lang), "items": ccv["json"]["how"]})
    xv = cross_view(D.get("cross_check"), lang)
    if xv:
        lists4.append({"title": t("x_title", lang), "items": xv["lines"]})
    s4 = {"n": 4, "title": t("s4", lang), "paragraphs": p4, "rows": rows4, "lists": lists4}

    # ---------- раздел 5 ----------
    dec = D["decision"]
    p5 = [t(dec["code"], lang)]
    lists5 = []
    if D["discrepancies"]:
        lists5.append({"title": t("disc_title", lang), "items": [_disc_text(d, lang, D["group"]) for d in D["discrepancies"]]})
        p5.append(t("disc_priority", lang))
    else:
        p5.append(t("disc_none", lang))
    checks = [_check_text(c, lang, D["group"]) for c in dec["checks"]]
    if checks:
        lists5.append({"title": t("checks_title", lang), "items": checks})
    missing_labels = [_s1_label(k, lang, D["group"]) for k in D["missing"]]
    if ins.get("session_missing"):
        missing_labels.append(t("session_not_found", lang))
    if missing_labels:
        lists5.append({"title": t("missing_title", lang), "items": missing_labels})
    msv = _measures_view(D.get("measures"), lang)
    lists5.append({"title": t("ms_title", lang), "items": [m["line"] for m in msv["items"]] or [t("ms_none", lang)]})
    if msv["summary"].get("text"):
        p5.append(msv["summary"]["text"])
    s5 = {"n": 5, "title": t("s5", lang), "paragraphs": p5, "rows": [], "lists": lists5}

    sections = [s1, s2, s3, s4, s5]
    for s in sections:
        s.setdefault("lists", [])
        s.setdefault("source_lines", [])      # строки источника сразу под строками раздела
    created = meta["created_at"]
    return {
        "ok": True, "id": meta["id"], "number": meta["number"], "lang": lang,
        "title": t("title", lang), "insurer": D.get("insurer") or NA, "insurer_known": bool(D.get("insurer")),
        "date": datetime.fromisoformat(created).strftime("%d.%m.%Y"),
        "created_at": created, "expires_at": meta["expires_at"],
        "header": [_row(t("number", lang), meta["number"]),
                   _row(t("date", lang), datetime.fromisoformat(created).strftime("%d.%m.%Y")),
                   _row(t("insurer", lang), D.get("insurer") or NA)],
        "sections": sections,
        "risk": {"level": risk["level"], "level_label": level_label, "net": risk["net"], "up": risk["up"],
                 "down": risk["down"], "calibrated": ae.CALIBRATED, "rule": rule_text,
                 "factors": [{"code": f["code"], "sign": f["sign"], "text": _text(f, lang)}
                             for f in risk["factors"]]},
        "rate": {"mode": mode, "base_pct": rate_res["base_pct"], "adj_pct": rate_res["adj_pct"],
                 "calc_pct": rate_res["calc_pct"], "applied_pct": rate_res["applied_pct"],
                 "min_pct": rate_res["min_pct"], "min_applied": rate_res["min_applied"],
                 "base_source": rate_res["base_source"], "object_type": rate_res["object_type"],
                 "product_code": rate_res["product_code"], "class_code": rate_res["class_code"],
                 "calibrated": ae.CALIBRATED, "how": [_text(h, lang) for h in rate_res["how"]],
                 "tariff_version_id": D.get("tariff_version_id"), "final_pct": pf["rate_pct"],
                 "multi_class": bool(D.get("multi_class")),
                 "product_classes": (must.get("product_classes") or [])},
        "premium": {"amount": pf["amount"], "term_days": rate_res["term_days"], "currency": "UZS",
                    "text": money(pf["amount"], lang) if pf["amount"] is not None else NA,
                    "before_franchise": rate_res["premium"], "rate_pct": pf["rate_pct"],
                    "franchise_applied": pf["franchise_applied"]},
        "value": {"ratio_pct": value["ratio_pct"], "verdict": value["verdict"], "text": vtext,
                  "legal_ref": value["legal_ref"], "legal_ref_text": legal,
                  "depreciated": value.get("depreciated"),
                  # итоговый вывод раздела 3 и плитки сводки: normal | under | over | refine
                  "final_verdict": final, "final_text": vtext,
                  "refined_ratio_pct": ((M or {}).get("insured_check") or {}).get("ratio_pct")
                  if final == "refine" else None,
                  "declared_original": opt.get("declared_value_original"),
                  "value_source": decl[2] if decl else None},
        "market_value": mv["json"],
        "request_check": rcv["json"],
        "contract_check": ccv["json"],
        "cross_check": xv or {"available": False},
        "franchise": {"needed": bool(fr.get("needed")), "text": fr_text,
                      "grounds": [{"code": g["code"], "text": _text(g, lang)} for g in fr.get("grounds") or []],
                      **({"size": fr["size"]} if fr.get("size") else {}),
                      **_franchise_extra(fr, fr_how, fr_alts, lang)},
        "scenarios": scv["json"],
        # аналитика раздела 4 (30.09.2026): риски, факторы, чувствительность, состав тарифа, сценарии подробно,
        # удержание, балл, рынок и статистика с источниками, франшиза справочно, мероприятия, резюме
        "analytics": anv["json"],
        "measures": [m["json"] for m in msv["items"]],
        "measures_summary": msv["summary"],
        "clauses": [{"code": c["code"], "text": c.get(lang) or c.get("ru"), "expert": True,
                     "calibrated": ae.CALIBRATED} for c in D["clauses"]],
        "discrepancies": [{"key": d["key"], "label": tx.field_label(d["key"], lang, D["group"]),
                           "values": [{"value": g["value"], "sources": g["sources"],
                                       "source_labels": [tx.label(tx.SOURCE_LABELS, s, lang) for s in g["sources"]]}
                                      for g in d["values"]],
                           "priority": d["priority"], "text": _disc_text(d, lang, D["group"])} for d in D["discrepancies"]],
        "decision": {"code": dec["code"].replace("d_", ""), "text": t(dec["code"], lang), "checks": checks},
        "missing": missing_labels,
        "inspection": {"done": bool(ins["photos"] and ins["ai"]), "photos": ins["photos"],
                       "views_seen": ins["views_seen"], "missing_views": ins["missing_views"],
                       "damages": ins["damages"], "documents": ins["documents"]},
        "recognized": recognized_view(rec, lang, group=D["group"]),
        "footer": t("footer", lang),
        "downloads": {"docx": f"/act/{meta['id']}.docx?lang={lang}", "pdf": f"/act/{meta['id']}.pdf?lang={lang}"},
    }


def _ddmmyyyy(iso) -> str:
    try:
        return date.fromisoformat(str(iso)).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return str(iso or "")


def _rq_params(code: str, p: dict, lang: str, how: bool = False) -> dict:
    """Параметры строки сверки в словах языка акта: тарифы — процентом, премии — сумами, даты — ДД.ММ.ГГГГ.
    how — «как считали»: сумма и премия с копейками, если они есть."""
    NA = t("na", lang)
    out = {}
    # что сравнивается: тарифы и франшиза — проценты, срок — дни, остальное — сумы
    kind = "pct" if ("tariff" in code or "franchise" in code) else "days" if "term" in code else "money"
    for k, v in (p or {}).items():
        if v is None:
            out[k] = NA
        elif k in ("req", "calc", "min", "diff") and kind == "pct":
            out[k] = pct(abs(v) if k == "diff" else v, lang)
        elif k in ("req", "calc", "diff") and kind == "days":
            out[k] = str(int(v))
        elif k == "diff":
            out[k] = _signed(v, lang)
        elif how and k in ("sum", "premium"):
            out[k] = money_k(v, lang)
        elif k in ("req", "calc", "tol", "sum", "premium"):
            out[k] = money(v, lang)
        elif k in ("rate", "pct", "act"):
            out[k] = pct(v, lang)
        elif k == "ratio":
            out[k] = pct(v, lang, 2)
        elif k in ("date_from", "date_to"):
            out[k] = _ddmmyyyy(v)
        elif k == "days":
            out[k] = str(int(v))
        else:
            out[k] = v
    return out


def _request_check_view(rc: Optional[dict], lang: str, prefix: str = "rq", contract: Optional[dict] = None) -> dict:
    """Сверка с запросом филиала (prefix rq) или с договором (prefix ct, общая функция): строки для раздела 4
    и JSON request_check / contract_check. Старые акты — available = false."""
    rc = rc or {}
    sources = tx.CT_SOURCE_LABELS if prefix == "ct" else tx.RQ_SOURCE_LABELS
    js = {"available": bool(rc.get("available")), "source": rc.get("source"),
          "source_label": tx.label(sources, rc["source"], lang) if rc.get("source") else None,
          "items": [], "summary": None, "how": [], "tolerance": rc.get("tolerance"),
          "term_inclusive": rc.get("term_inclusive"), "calibrated": ae.CALIBRATED}
    trusted = "source_kind" in rc                  # акты до 30.09.2026 (вечер) — без источника по полям и правок
    if trusted:
        js.update(_trust_view(rc, prefix, lang, sources))
    if prefix == "ct":
        ess = rc.get("essentials") or []
        js["essentials"] = [{"code": e["code"], "label": tx.label(tx.CT_ESSENTIAL_LABELS, e["code"], lang),
                             "present": e["present"]} for e in ess]
        js["legal_ref"] = tx.label(tx.LEGAL_REFS, rc["legal_ref"], lang) if rc.get("legal_ref") else None
        c = contract or {}
        js["contract_no"], js["contract_date"] = c.get("contract_no"), c.get("contract_date")
        js["covered_risks"] = _risk_view(c.get("covered_risks"), tx.RISK_LABELS, lang)
        js["exclusions"] = _risk_view(c.get("exclusions"), tx.EXCLUSION_LABELS, lang)
        js["payments"] = list(c.get("payments") or [])
        js["payment_mode"] = c.get("payment_mode")
    if not js["available"]:
        return {"lines": [], "json": js}
    lines = []
    for it in rc.get("items") or []:
        params = it.get("params") or {}
        if it["code"] == "essentials":
            codes = params.get("missing") if it["verdict"] == "no_essential" else params.get("present")
            text = t(it["text_code"], lang, what=", ".join(tx.label(tx.CT_ESSENTIAL_LABELS, x, lang)
                                                           for x in codes or []))
        else:
            text = t(it["text_code"], lang, **_rq_params(it["code"], params, lang))
        label = t(prefix + "_l_" + it["code"], lang)
        js["items"].append({"code": it["code"], "label": label, "requested": it["requested"],
                            "calculated": it["calculated"], "diff": it["diff"], "diff_pct": it["diff_pct"],
                            "verdict": it["verdict"], "verdict_label": t("rq_v_" + it["verdict"], lang),
                            "reference": bool(it["reference"]), "text": text})
        lines.append(f"{label} — {t('rq_v_' + it['verdict'], lang)}: {text}")
    sm = rc.get("summary") or {}
    js["summary"] = {"verdict": sm.get("verdict"), "text": t(sm.get("code") or f"{prefix}_summary_missing", lang),
                     "differs": sm.get("differs", 0), "below_min": sm.get("below_min", 0),
                     "missing": sm.get("missing", 0)}
    if prefix == "ct":
        js["summary"]["no_essential"] = sm.get("no_essential", 0)
        fs = rc.get("field_sources") or {}
        for key, line in (("covered_risks", "ct_risks_line"), ("exclusions", "ct_excl_line")):
            if not js[key]:
                continue
            # риски со скана или дочитанные моделью — с пометкой: модель могла ошибиться
            by_model = fs.get(key) in ("photo", "document_ai")
            js[key + "_by_model"] = by_model
            lines.append(t(line, lang, what=", ".join(x["label"] for x in js[key]))
                         + (" (" + t("by_model_mark", lang) + ")" if by_model else ""))
    head = [js["summary"]["text"]]
    if trusted:
        head.append(t("tr_src_line", lang, v=js["source_label"]))
        lines += [js["edits"]["line"]] + [e["text"] for e in js["edits"]["items"]]
    js["how"] = [t(h["code"], lang, **_rq_params(h["code"], h.get("params") or {}, lang, how=True))
                 for h in rc.get("how") or []]
    return {"lines": head + lines, "json": js}


def _trust_view(rc: dict, prefix: str, lang: str, sources: dict) -> dict:
    """Источник условий (из документа / с правками сотрудника / введено сотрудником) и правки «было → стало»."""
    kind, edits = rc.get("source_kind"), rc.get("edits") or []
    detail = tx.label(sources, rc.get("origin"), lang) if rc.get("origin") else None
    if kind == "input":
        label = t("tr_src_input", lang) + (" — " + t("tr_doc_missing", lang) if rc.get("doc_missing") else "")
    elif kind == "document_edited":
        label = t("tr_src_edited", lang, n=len(edits)) + (" — " + detail if detail else "")
    else:
        label = t("tr_src_doc", lang) + (" — " + detail if detail else "")
    items = []
    for e in edits:
        lab = tx.label(tx.EDIT_LABELS, e["code"], lang)
        was, now = _edit_value(e["code"], e.get("was"), lang), _edit_value(e["code"], e.get("now"), lang)
        items.append({"code": e["code"], "label": lab, "was": e.get("was"), "now": e.get("now"),
                      "text": t("tr_edit", lang, label=lab, was=was, now=now)})
    line = t(prefix + "_edits_line", lang, what=str(len(items)) if items else t("tr_no_edits", lang))
    return {"source_kind": kind, "source_label": label, "document_missing": bool(rc.get("doc_missing")),
            "field_sources": dict(rc.get("field_sources") or {}),
            "edits": {"count": len(items), "items": items, "line": line}}


def _edit_value(code: str, v, lang: str) -> str:
    """Значение условия в строке правки: суммы с копейками, тариф процентом, даты ДД.ММ.ГГГГ."""
    NA = t("tr_none", lang)
    if v in (None, "", [], {}):
        return NA
    if code in ("sum_insured", "object_value", "premium"):
        return money_k(v, lang)
    if code == "tariff_pct":
        return pct(v, lang)
    if code in ("term_from", "term_to", "contract_date"):
        return _ddmmyyyy(v)
    if code == "term_days":
        return t("x_days", lang, n=int(v))
    if code == "franchise":
        return _x_value("franchise", v, lang)
    if code in ("covered_risks", "exclusions"):
        group = tx.RISK_LABELS if code == "covered_risks" else tx.EXCLUSION_LABELS
        return ", ".join(x["label"] for x in _risk_view(v, group, lang)) or NA
    if code == "payment_mode":
        return tx.label(tx.PAYMENT_MODE_LABELS, v, lang)
    if code == "payments":
        return t("tr_payments", lang, n=len(v), total=money_k(sum(float(p["amount"]) for p in v), lang))
    if code == "items":
        return t("tr_items", lang, n=len(v), total=money_k(sum(float(x["sum"]) for x in v), lang))
    s = str(v)
    return s if len(s) <= 80 else s[:79] + "…"


def money_k(v, lang: str) -> str:
    """Сумма с копейками (тийинами), если они есть: 47 397 852 345,04 сум; целая — без дроби."""
    x = round(float(v), 2)
    if x == int(x):
        return money(x, lang)
    cur = {"ru": "сум", "uz": "soʻm", "en": "UZS"}[tx.lang_of(lang)]
    return tx._num(x, tx.lang_of(lang), 2) + tx.NBSP + cur


def _premium_final(D: dict) -> dict:
    """Премия акта: с учётом применённой франшизы; в старых актах (до 29.09.2026) — премия ставки."""
    pf = D.get("premium_final")
    if pf:
        return pf
    return {"amount": D["rate"]["premium"], "rate_pct": D["rate"]["applied_pct"], "franchise_applied": False}


def _signed(x, lang: str) -> str:
    if x is None:
        return t("na", lang)
    return ("−" if x < 0 else "+") + money(abs(x), lang)


def _num4(x, lang: str) -> str:
    s = f"{float(x):.4f}".rstrip("0").rstrip(".")
    return s if lang == "en" else s.replace(".", ",")


_PCT_KEYS = ("pct", "from", "to", "rate", "rate_after", "raw", "min", "cap")
_MONEY_KEYS = ("amount", "base", "with", "premium", "before", "after", "sum", "value")


def _fmt_params(params: dict, lang: str) -> dict:
    out = {}
    for k, v in (params or {}).items():
        name = {"from": "from_", "with": "with_"}.get(k, k)
        if v is None:
            out[name] = t("na", lang)
        elif k in _PCT_KEYS:
            out[name] = pct(v, lang)
        elif k == "delta":
            out[name] = _signed(v, lang)
        elif k in _MONEY_KEYS:
            out[name] = money(v, lang)
        elif k == "mult":
            out[name] = _num4(v, lang)
        elif k == "type":
            out[name] = tx.label(tx.FR_TYPE_LABELS, v, lang)
        else:
            out[name] = v
    return out


def _fr_how_text(item: dict, lang: str) -> str:
    p = item.get("params") or {}
    code = item["code"]
    if code == "frh_size" and p.get("from") is not None and p.get("from") == p.get("to"):
        code = "frh_size_one"                     # вилка с равными границами
    return t(code, lang, **_fmt_params(p, lang))


def _alt_view(a: dict, lang: str) -> dict:
    ref = a.get("legal_ref")
    return {"code": a["code"], "text": t(a["code"], lang, **_fmt_params(a.get("params"), lang)),
            "premium": a.get("premium"), "premium_delta": a.get("premium_delta"),
            "base_premium": a.get("base_premium"),
            "legal_ref": tx.label(tx.LEGAL_REFS, ref, lang) if ref else None, "calibrated": ae.CALIBRATED}


def _franchise_extra(fr: dict, how: list, alts: list, lang: str) -> dict:
    """Новые поля блока franchise (29.09.2026). В старых актах их нет — отдаются значения «нет франшизы»."""
    w = fr.get("warning")
    return {"status": fr.get("status") or ("statutory" if fr.get("code") == "fr_statutory" else
                                           "proposed" if fr.get("needed") else "none"),
            "applied": bool(fr.get("applied")), "applied_by": fr.get("applied_by"),
            "type": fr.get("type"),
            "type_label": tx.label(tx.FR_TYPE_LABELS, fr["type"], lang) if fr.get("type") else None,
            "size_pct": fr.get("size_pct", 0.0), "size_amount": fr.get("size_amount", 0), "cap_pct": fr.get("cap_pct"),
            "premium_before": fr.get("premium_before"), "premium_after": fr.get("premium_after"),
            "delta": fr.get("delta"), "delta_pct": fr.get("delta_pct"),
            "rate_before": fr.get("rate_before"), "rate_after": fr.get("rate_after"),
            "multiplier": fr.get("multiplier", 1.0), "floor_applied": bool(fr.get("floor_applied")),
            "how": how, "alternatives": alts,
            "warning": t(w["code"], lang, **_fmt_params(w.get("params"), lang)) if w else None,
            "calibrated": ae.CALIBRATED}


def _scenarios_view(sc: Optional[dict], must: dict, lang: str) -> dict:
    """Строки раздела 4, абзац с определениями, списки «как посчитано» и JSON блока scenarios."""
    NA = t("na", lang)
    sc = sc or {"available": False, "reason": "sc_na_error", "class_code": must.get("class_code")}
    # порядок заказчика PML ≤ EML ≤ MFL (с 29.09.2026); старые сохранённые акты — прежние тексты
    classic = sc.get("order") == ax.SCENARIO_ORDER
    defs = t("sc_defs_classic" if classic else "sc_defs", lang)
    js = {"available": bool(sc.get("available")), "class_code": sc.get("class_code"), "rule": sc.get("rule"),
          "order": sc.get("order") or "legacy", "pml": None, "eml": None, "mfl": None, "tiles": [],
          "retention": None, "how": [], "assumptions": [], "note": None, "definitions": defs,
          "calibrated": ae.CALIBRATED}
    rows, lists = [], []
    js["assumptions"] = [{"code": a["code"], "text": _assumption_text(a, lang),
                          "note": "принято по умолчанию" if lang == "ru" else t("sc_assumptions_title", lang)}
                         for a in sc.get("assumptions") or []]
    if not sc.get("available"):
        note = t(sc.get("reason") or "sc_na_error", lang)
        js["note"] = note
        rows.append(_row("PML / EML / MFL", t("sc_na", lang), note))
        if js["assumptions"]:
            lists.append({"title": t("sc_assumptions_title", lang), "items": [a["text"] for a in js["assumptions"]]})
        return {"rows": rows, "lists": lists, "paragraph": defs, "json": js}
    # проценты трёх сценариев — с одинаковым числом знаков: все целые — без дроби, иначе один знак
    pcts = [sc["items"][s]["pct"] for s in ("PML", "EML", "MFL")]
    digits = 0 if all(p is not None and float(p) == int(p) for p in pcts) else 1
    for s, lab in (("PML", "sc_pml"), ("EML", "sc_eml"), ("MFL", "sc_mfl")):
        it = sc["items"][s]
        what = _scenario_what(it, lang)
        pct_text = tx.pct_fixed(it["pct"], lang, digits)
        js[s.lower()] = {"amount": round(it["amount"]), "pct": it["pct"], "pct_text": pct_text, "what": what,
                         "what_code": it["what"], "state_code": it.get("state"),
                         "source_scenario": it.get("source_scenario"),
                         # формула собрана модулем по-русски — на другом языке акта её не отдаём
                         "formula": it.get("formula") if lang == "ru" else None, "calibrated": ae.CALIBRATED}
        js["tiles"].append({"code": s.lower(), "name": s, "label": t(lab, lang), "amount": round(it["amount"]),
                            "pct": it["pct"], "pct_text": pct_text, "what": what})
        rows.append(_row(t(lab, lang), t("sc_value", lang, amount=money(it["amount"], lang), pct=pct_text), what))
    ret = sc.get("retention") or {}
    tail = None
    if ret.get("known"):
        basis = t("sc_ret_basis", lang, own=money(ret.get("own_funds"), lang), res=money(ret.get("reserves"), lang),
                  limit=money(ret.get("limit_per_risk"), lang), cls=ret.get("line_class") or sc.get("class_code"),
                  line=money(ret.get("line_retention"), lang) if ret.get("line_retention") is not None else NA,
                  ret=money(ret.get("limit"), lang))
        status = t("sc_ret_temporary" if ret.get("status") == "temporary" else "sc_ret_reported", lang)
        ex = ret.get("mfl_excess")
        if ret.get("compared_with") == "eml":
            ee = ret.get("eml_excess")
            tail = (t("sc_ret_eml_excess", lang, x=money(ee, lang)) if ee else t("sc_ret_eml_within", lang)) + "; " + \
                (t("sc_ret_mfl_excess", lang, x=money(ex, lang)) if ex else t("sc_ret_mfl_within", lang))
        else:
            tail = t("sc_ret_excess", lang, x=money(ex, lang)) if ex else t("sc_ret_within", lang)
        rows.append(_row(t("sc_retention", lang), money(ret.get("limit"), lang), status + "; " + tail))
    else:
        basis = t("sc_ret_unknown", lang)
        rows.append(_row(t("sc_retention", lang), t("sc_ret_unknown", lang)))
    legal = ret.get("legal_ref")
    js["retention"] = {"limit": ret.get("limit"), "known": bool(ret.get("known")), "basis": basis,
                       "status": ret.get("status"), "limit_per_risk": ret.get("limit_per_risk"),
                       "line_retention": ret.get("line_retention"), "compared_with": ret.get("compared_with"),
                       "eml_excess": ret.get("eml_excess"), "within": ret.get("within"),
                       "mfl_excess": ret.get("mfl_excess"), "text": tail,
                       "legal_ref": tx.label(tx.LEGAL_REFS, legal, lang) if legal else None}
    how = [t("sc_how_source", lang, cls=sc.get("class_code"), rule=t("sc_rule_" + sc["rule"], lang))]
    if classic:
        how.append(t("sc_how_names", lang))
    if sc["rule"] != "vehicle" and (sc.get("k") or 1) < 1:
        how.append(t("sc_how_k", lang, k=_num4(sc["k"], lang)))
    how.append(basis)
    js["how"] = how
    lists.append({"title": t("sc_how_title", lang), "items": how})
    if js["assumptions"]:
        lists.append({"title": t("sc_assumptions_title", lang), "items": [a["text"] for a in js["assumptions"]]})
    return {"rows": rows, "lists": lists, "paragraph": defs, "json": js}


# --------------------------------------------------------------------------- #
#  Раздел 4: аналитика риска (30.09.2026) — таблицы, строки и JSON analytics
# --------------------------------------------------------------------------- #

def _mult(x, lang: str) -> str:
    """Множитель без лишних нулей: 1,2 / 0,85 / 1."""
    s = f"{float(x):.4f}".rstrip("0").rstrip(".")
    return s if lang == "en" else s.replace(".", ",")


def _spct(x, lang: str, digits: int = 1) -> str:
    """Изменение в процентах со знаком: +20 % / −25 %."""
    if x is None:
        return t("na", lang)
    v = round(float(x), digits)
    sign = "+" if v > 0 else ("−" if v < 0 else "")
    return sign + tx.pct_fixed(abs(v), lang, digits if v != int(v) else 0)


def _spp(x, lang: str) -> str:
    """Процентные пункты со знаком: +0,1092."""
    v = round(float(x or 0), 4)
    s = f"{abs(v):.4f}".rstrip("0").rstrip(".")
    s = s if lang == "en" else s.replace(".", ",")
    return ("+" if v > 0 else ("−" if v < 0 else "")) + s


def _smoney(x, lang: str) -> str:
    return _signed(x, lang) if x else money(0, lang)


def _flabel(f: str, lang: str, ru: Optional[str] = None) -> str:
    if f in tx.FACTOR_LABELS:
        return tx.label(tx.FACTOR_LABELS, f, lang)
    return ru if (lang == "ru" and ru) else f


def _olabel(f: str, o, lang: str, ru: Optional[str] = None) -> str:
    if o is None:
        return t("an_f_not_set", lang)
    key = f"{f}:{o}"
    if key in tx.OPTION_LABELS:
        return tx.label(tx.OPTION_LABELS, key, lang)
    return ru if (lang == "ru" and ru) else str(o)


def _fo(f: str, o, lang: str) -> str:
    """«фактор: значение» строчными — для перечней в тексте."""
    return f"{_flabel(f, lang).lower()}: {_olabel(f, o, lang)}"


def _peril_label(code: str, lang: str, ru: Optional[str], cls: Optional[str]) -> str:
    if code.startswith("class"):
        return _class_label(code[5:], ru, lang)
    if code in tx.PERIL_LABELS:
        return tx.label(tx.PERIL_LABELS, code, lang)
    return ru if lang == "ru" and ru else code


def _li(title: str, items: list, table: Optional[dict] = None, sources: Optional[list] = None,
        notes: Optional[list] = None) -> dict:
    """Список раздела: строки для экрана и Word; table — та же информация таблицей (Word, PDF);
    sources — строки «Источник: …» (под таблицей, на экране — плашка .srcbar по JSON analytics)."""
    li = {"title": title, "items": list(items) + list(notes or []) + list(sources or [])}
    if table:
        li["table"] = table
        li["notes"] = list(notes or [])
        li["sources"] = list(sources or [])
    return li


def _date(iso) -> str:
    return _ddmmyyyy(str(iso)[:10]) if iso else ""


def _pack_text(mk: dict, lang: str) -> str:
    """Классы строки-пакета НАПП: «8 и 9» / «8 va 9» / «8 and 9» (по ключу строки market_stats)."""
    cl = [str(x) for x in mk.get("pack_classes") or []] or ["8", "9"]
    conj = {"ru": " и ", "uz": " va ", "en": " and "}[tx.lang_of(lang)]
    return ", ".join(cl[:-1]) + conj + cl[-1] if len(cl) > 1 else cl[0]


def _dmark(x: dict, lang: str, by_source: bool = False) -> str:
    """Пометка «по умолчанию» у фактора, значение которого не введено, а принято по умолчанию."""
    on = x.get("source") == "default" if by_source else bool(x.get("assumed"))
    return ", " + t("an_default_mark", lang) if on else ""


def _pts(x, lang: str) -> str:
    """Баллы с нужной формой слова: 24 балла / 24 ball / 24 points."""
    return tx.count_text(round(float(x or 0)), "points", lang)


def _analytics_view(D: dict, lang: str) -> dict:
    """Аналитика раздела 4 на языке акта: резюме, списки с таблицами и JSON analytics для экрана."""
    A = D.get("analytics")
    must = D["must"]
    if not A:                               # акты до 30.09.2026 (вечер) — без аналитики
        return {"summary": None, "lists": [], "json": {"available": False, "reason": "old_act",
                                                       "calibrated": ae.CALIBRATED}}
    if not A.get("available"):
        return {"summary": None, "lists": [{"title": t("an_tariff_title", lang), "items": [t("an_na", lang)]}],
                "json": {"available": False, "reason": A.get("reason") or "no_engine", "text": t("an_na", lang),
                         "calibrated": ae.CALIBRATED}}
    rate_res = D["rate"]
    cls = must["class_code"]
    NA = t("na", lang)
    js = {"available": True, "reason": None, "calibrated": ae.CALIBRATED, "sources": []}
    lists = []

    def src_add(title, url, domain, as_of):
        for s in js["sources"]:
            if s["url"] == url and s["title"] == title:
                return
        js["sources"].append({"title": title, "url": url, "domain": domain, "as_of": as_of})

    # ---------- 1. риски ----------
    R = A.get("risks") or {}
    ritems, rrows = [], []
    for it in R.get("items") or []:
        name = _peril_label(it["code"], lang, it.get("name_ru"), it.get("class_code"))
        rs = it.get("reason") or {}
        if rs.get("code") == "by_factor":
            why = t("an_r_by_factor", lang, factor=_flabel(rs["factor"], lang), option=_olabel(rs["factor"], rs["option"],
                                                                                            lang), mult=_mult(rs["multiplier"], lang))
            if rs.get("assumed"):
                why += " — " + t("an_default_mark", lang)
        elif rs.get("code") == "zone_unknown":
            why = t("an_r_zone_unknown", lang)
        else:
            why = t("an_r_all_avg", lang)
        parts = [why]
        if it.get("raises"):
            parts.append(t("an_r_raises", lang, what=", ".join(
                f"{_fo(x['factor'], x['option'], lang)} ×{_mult(x['multiplier'], lang)}{_dmark(x, lang)}" for x in it["raises"])))
        if it.get("lowers"):
            parts.append(t("an_r_lowers", lang, what=", ".join(
                f"{_fo(x['factor'], x['option'], lang)} ×{_mult(x['multiplier'], lang)}{_dmark(x, lang)}" for x in it["lowers"])))
        if it.get("unknown"):
            parts.append(t("an_r_unknown", lang, what=", ".join(_flabel(f, lang).lower() for f in it["unknown"])))
        if it.get("measures"):
            parts.append(t("an_r_helps", lang, what=", ".join(
                f"{_fo(m['factor'], m['option_to'], lang)} {_spct(m['delta_pct'], lang)}" for m in it["measures"])))
        why_all = "; ".join(parts)
        share = tx.pct_fixed(it["share_of_net_pct"], lang, 1)
        level = tx.label(tx.PERIL_LEVEL_LABELS, it["level"], lang)
        label = name + (" (" + t("an_r_cat", lang) + ")" if it.get("catastrophic") else "")
        ritems.append({"code": it["code"], "name": name, "share_of_net_pct": it["share_of_net_pct"],
                       "share_text": share, "level": it["level"], "level_label": level, "why": why_all,
                       "reason": rs, "catastrophic": bool(it.get("catastrophic")), "multiplier": it.get("multiplier"),
                       "raises": [_olabel(x["factor"], x["option"], lang) for x in it.get("raises") or []],
                       "lowers": [_olabel(x["factor"], x["option"], lang) for x in it.get("lowers") or []],
                       "unknown": [_flabel(f, lang) for f in it.get("unknown") or []],
                       "measures": [_olabel(m["factor"], m["option_to"], lang) for m in it.get("measures") or []],
                       "text": t("an_r_line", lang, name=label, share=share, level=level, why=why_all),
                       "calibrated": ae.CALIBRATED})
        rrows.append([label, share, level, why_all])
    rnotes = []
    if R.get("whole_class"):
        rnotes.append(t("an_r_whole", lang, cls=cls))
    covered = [x["label"] for x in _risk_view(((D.get("contract") or {}).get("covered_risks")), tx.RISK_LABELS, lang)]
    if covered:
        rnotes.append(t("an_r_covered", lang, what=", ".join(covered)))
    th = R.get("thresholds") or aa.PERIL_LEVEL
    rnotes.append(t("an_r_rule", lang, low=_mult(th["low_max"], lang), high=_mult(th["high_min"], lang)))
    if not R.get("whole_class") and ritems:
        # класс без разбивки на риски — про доли справочника не пишем: там одна строка «весь класс»
        rnotes.append(t("an_r_shares_round" if R.get("rounding") else "an_r_shares", lang,
                        total=tx.pct_fixed(R.get("total_pct") or 0, lang, 1)))
    js["risks"] = {"available": bool(ritems), "items": ritems, "total_pct": R.get("total_pct"),
                   "whole_class": bool(R.get("whole_class")), "covered_by_contract": covered, "notes": rnotes,
                   "calibrated": ae.CALIBRATED}
    if ritems:
        lists.append(_li(t("an_risks_title", lang), [x["text"] for x in ritems],
                         {"columns": [t("col_risk", lang), t("col_share", lang), t("col_level", lang),
                                      t("col_why", lang)], "rows": rrows, "widths": [22, 12, 11, 55]},
                         notes=rnotes))

    # ---------- 2. факторы ----------
    F = A.get("factors") or {}
    fitems, frows = [], []
    for x in F.get("items") or []:
        name = _flabel(x["factor"], lang, x.get("name_ru"))
        value = _olabel(x["factor"], x.get("option"), lang, x.get("option_ru"))
        src = tx.label(tx.AN_SOURCE_LABELS, x["source"], lang)
        if not x.get("applies", True):
            contrib = t("an_f_seismic_na", lang)
        elif x.get("rate_pp"):
            contrib = t("an_f_contrib", lang, pp=_spp(x["rate_pp"], lang), premium=_smoney(x["premium_effect"], lang))
        else:
            contrib = t("an_f_zero", lang)
        text = t("an_f_line", lang, factor=name, value=value, source=src, mult=_mult(x["multiplier"], lang),
                 contrib=contrib)
        fitems.append({"code": x["factor"], "name": name, "option": x.get("option"), "value": value,
                       "source": x["source"], "source_label": src, "status": x["status"],
                       "multiplier": x["multiplier"], "effect_pct": x.get("effect_pct"), "direction": x["direction"],
                       "rate_pp": x.get("rate_pp"), "premium_effect": x.get("premium_effect"), "contribution": contrib,
                       "text": text, "calibrated": ae.CALIBRATED})
        frows.append([name, value, src, "×" + _mult(x["multiplier"], lang), contrib])
    fnote = t("an_f_base", lang, base=pct(F.get("base_pct"), lang), tech=pct(F.get("technical_pct"), lang))
    js["factors"] = {"items": fitems, "base_pct": F.get("base_pct"), "technical_pct": F.get("technical_pct"),
                     "note": fnote, "calibrated": ae.CALIBRATED}
    if fitems:
        lists.append(_li(t("an_factors_title", lang), [x["text"] for x in fitems],
                         {"columns": [t("col_factor", lang), t("col_value", lang), t("col_source", lang),
                                      t("col_mult", lang), t("col_contrib", lang)], "rows": frows,
                          "widths": [20, 20, 22, 10, 28]}, notes=[fnote]))

    # ---------- чувствительность ----------
    sitems, srows = [], []
    for s in (A.get("sensitivity") or {}).get("items") or []:
        fname = _flabel(s["factor"], lang, s.get("name_ru"))
        to = _olabel(s["factor"], s["option_to"], lang, s.get("option_to_ru"))
        key = "an_s_measure" if s["kind"] == "measure" else "an_s_clarify"
        text = t(key, lang, factor=fname if s["kind"] == "measure" else fname.lower(), to=to,
                 before=pct(s["tech_before"], lang), after=pct(s["tech_after"], lang),
                 delta=_spct(s["delta_pct"], lang))
        act_txt = ""
        if s.get("act_premium_after") is not None:
            d_txt = _smoney(s["act_premium_delta"], lang) if s.get("act_premium_delta") else t("an_ms_zero", lang)
            act_txt = money(s["act_premium_after"], lang) + " (" + d_txt + ")"
            text += t("an_s_act", lang, after=money(s["act_premium_after"], lang), delta=d_txt)
            if s.get("act_floored") and s.get("act_premium_delta"):
                text += t("an_s_act_floor", lang)
        sitems.append({"factor": s["factor"], "name": fname, "from": _olabel(s["factor"], s.get("option_from"), lang),
                       "to": to, "kind": s["kind"], "direction": s["direction"], "delta_pct": s["delta_pct"],
                       "tech_before": s["tech_before"], "tech_after": s["tech_after"],
                       "tech_premium_delta": s.get("tech_premium_delta"),
                       "act_premium_after": s.get("act_premium_after"), "act_premium_delta": s.get("act_premium_delta"),
                       "act_floored": bool(s.get("act_floored")), "text": text, "calibrated": ae.CALIBRATED})
        first = f"{fname}: {to}" if s["kind"] == "measure" else t("an_s_if", lang, factor=fname.lower(), to=to)
        srows.append([first, f"{pct(s['tech_before'], lang)} → {pct(s['tech_after'], lang)}",
                      _spct(s["delta_pct"], lang), act_txt or "—"])
    snote = t("an_s_note", lang)
    js["sensitivity"] = {"items": sitems, "note": snote, "calibrated": ae.CALIBRATED}
    lists.append(_li(t("an_sens_title", lang), [x["text"] for x in sitems] or [t("an_s_none", lang)],
                     {"columns": [t("col_change", lang), t("col_tech", lang), t("col_change", lang),
                                  t("col_act_premium", lang)], "rows": srows,
                      "widths": [38, 24, 12, 26]} if srows else None, notes=[snote]))

    # ---------- 3. состав тарифа ----------
    T = A.get("tariff") or {}
    mk = A.get("market") or {}
    if T.get("available"):
        trows = []

        def tr(code, label, value, note=""):
            trows.append({"code": code, "label": label, "value": value, "note": note})

        otype = _otype_label(T.get("object_type") or "", lang)
        bnotes = []
        if T.get("object_type_source") == "default":
            bnotes.append(t("an_t_base_default", lang, v=otype))
        if T.get("base_kind") == "class_average":
            bnotes.append(t("an_t_base_avg", lang))
        tr("base_net", t("an_t_base", lang, cls=T["class_code"], otype=otype), pct(T["base_net_pct"], lang),
           "; ".join(bnotes))
        excl = [tx.label(tx.PERIL_LABELS, c, lang) for c in T.get("excluded") or []]
        if T.get("perils_share") is not None and (T["perils_share"] != 1 or excl):
            tr("perils", t("an_t_perils", lang), "×" + _mult(T["perils_share"], lang),
               t("an_t_excluded", lang, what=", ".join(excl)) if excl else "")
        tr("factors", t("an_t_factors", lang), "×" + _mult(T.get("factors_mult") or 1, lang))
        tr("net", t("an_t_net", lang), pct(T["net_pct"], lang))
        tr("risk_load", t("an_t_risk", lang, r=tx.pct_fixed(T["risk_load_rate"] * 100, lang, 0)),
           "+" + pct(T["risk_load_pct"], lang))
        if T.get("cat_load_pct"):
            tr("cat_load", t("an_t_cat", lang), "+" + pct(T["cat_load_pct"], lang))
        if T.get("load_share") is not None:
            tr("load", t("an_t_load", lang), tx.pct_fixed(T["load_share"] * 100, lang, 0),
               t("an_t_load_takaful", lang) if T.get("takaful") else "")
        tr("technical", t("an_t_tech", lang), pct(T["technical_pct"], lang),
           t("an_t_tech_note", lang, premium=money(T["technical_premium"], lang)))
        tr("min", t("an_t_min", lang), pct(T["min_pct"], lang) if T.get("min_pct") is not None else NA)
        if T.get("act_mode") == "tariff":
            if T.get("policy_rate_pct") is not None:
                tr("policy", t("an_t_policy", lang, code=T.get("product_code")), pct(T["policy_rate_pct"], lang))
            else:
                tr("act_base", t("an_t_base_act", lang), pct(T.get("act_base_pct"), lang))
            tr("act", t("an_t_act", lang, adj="+" + pct(T.get("adj_pct") or 0, lang)), pct(T["act_rate_pct"], lang),
               t("an_t_act_min", lang) if T.get("min_applied") else "")
        else:
            tr("act", t("an_t_act_other", lang), pct(T["act_rate_pct"], lang) if T.get("act_rate_pct") is not None
               else t("rate_undefined", lang))
        tr("market", t("an_t_market", lang), pct(T["market_rate_pct"], lang) if T.get("market_rate_pct") else
           t("an_t_na_short", lang), (t("an_m_pack", lang, pack=_pack_text(mk, lang)).strip(" ()")
                                      if mk.get("pack") else ""))
        concl = t("an_t_conclusion", lang, act=pct(T["act_rate_pct"], lang) if T.get("act_rate_pct") is not None
                  else t("rate_undefined", lang), tech=pct(T["technical_pct"], lang),
                  market=pct(T["market_rate_pct"], lang) if T.get("market_rate_pct") else t("an_t_na_short", lang))
        js["tariff"] = {"available": True, "rows": trows, "conclusion": concl, "calibrated": ae.CALIBRATED,
                        **{k: T.get(k) for k in ("base_net_pct", "perils_share", "factors_mult", "net_pct",
                                                  "risk_load_pct", "cat_load_pct", "load_share", "takaful",
                                                  "technical_pct", "technical_premium", "min_pct", "policy_rate_pct",
                                                  "adj_pct", "act_rate_pct", "act_premium", "market_rate_pct",
                                                  "act_vs_technical", "act_vs_market_pct", "technical_vs_market_pct")}}
        lists.append(_li(t("an_tariff_title", lang),
                         [f"{r['label']}: {r['value']}" + (f" ({r['note']})" if r["note"] else "") for r in trows],
                         {"columns": [t("col_indicator", lang), t("col_value", lang), t("col_note", lang)],
                          "rows": [[r["label"], r["value"], r["note"]] for r in trows], "widths": [45, 18, 37]},
                         notes=[concl]))
    else:
        txt = t("an_t_na", lang, cls=cls)
        js["tariff"] = {"available": False, "reason": T.get("reason") or "no_base", "text": txt,
                        "conclusion": txt, "calibrated": ae.CALIBRATED}
        lists.append(_li(t("an_tariff_title", lang), [txt]))

    # ---------- 4. сценарии подробно, «что если», удержание ----------
    SC = A.get("scenarios") or {}
    RET = A.get("retention") or {}
    if SC.get("available"):
        scitems, scrows = [], []
        for it in SC.get("items") or []:
            how = []
            for p in it["parts"]:
                share = _mult(p["share"], lang) if p.get("share") is not None else NA
                if p["peril"] == "fire":
                    how.append(t("an_sc_fire", lang, base=money(p["base"], lang),
                                 where=t("an_sc_where_" + p["base_kind"], lang), share=share,
                                 amount=money(p["amount"], lang)))
                elif p["peril"] == "earthquake":
                    if p.get("amount") is None:
                        how.append(t("an_sc_eq_na", lang))
                    else:
                        zone = t("an_sc_zone", lang, z=_pts(p["zone"], lang)) if p.get("zone") is not None \
                            else t("an_sc_zone_na", lang)
                        how.append(t("an_sc_eq", lang, base=money(p["base"], lang), zone=zone, share=share,
                                     amount=money(p["amount"], lang)))
                elif p["peril"] == "damage9":
                    how.append(t("an_sc_c9", lang, base=money(p["base"], lang),
                                 where=t("an_sc_where_" + p["base_kind"], lang), share=share,
                                 amount=money(p["amount"], lang)))
                else:
                    how.append(t("an_sc_veh", lang, base=money(p["base"], lang), share=share,
                                 amount=money(p["amount"], lang)))
            if it["rule"] == "property8" and len([p for p in it["parts"] if p.get("amount") is not None]) > 1:
                how.append(t("an_sc_taken", lang, what=tx.label(tx.PERIL_LABELS, it["chosen"], lang).lower()))
            if it.get("k") is not None and it["k"] < 1 and it["rule"] != "vehicle":
                how.append(t("an_sc_k", lang, k=_mult(it["k"], lang)))
            if it.get("bi_loss"):
                how.append(t("an_sc_bi", lang, x=money(it["bi_loss"], lang)))
            if it["rule"] != "vehicle":
                how.append(t("an_sc_prot_assumed", lang) if it.get("protection_assumed")
                           else t("an_sc_prot", lang, v=_olabel("protection", it.get("protection"), lang)))
            elif it.get("protection"):
                how.append(t("an_sc_prot", lang, v=_olabel("protection", it["protection"], lang)))
            how_txt = "; ".join(how)
            pct_txt = tx.pct_fixed(it["pct"], lang, 1) if it.get("pct") is not None else NA
            scitems.append({"name": it["name"], "amount": it["amount"], "pct": it.get("pct"), "pct_text": pct_txt,
                            "formula": how_txt, "parts": it["parts"], "chosen": it.get("chosen"),
                            "text": t("an_sc_line", lang, name=it["name"], amount=money(it["amount"], lang),
                                      pct=pct_txt, how=how_txt), "calibrated": ae.CALIBRATED})
            scrows.append([it["name"], money(it["amount"], lang) + " (" + pct_txt + ")", how_txt])
        lists.append(_li(t("an_sc_title", lang), [x["text"] for x in scitems],
                         {"columns": [t("col_scenario", lang), t("col_amount", lang), t("col_how", lang)],
                          "rows": scrows, "widths": [10, 25, 65]}))
        # «что если»
        witems, wrows = [], []
        for w in SC.get("whatif") or []:
            if not w.get("ok"):
                continue
            val = _olabel("protection", w["value"], lang) if w["change"] == "protection" else _pts(w["value"], lang)
            what = tx.label(tx.AN_WHATIF_LABELS, w["change"], lang).format(v=val)
            tail = ""
            if w.get("eml_excess") is not None:
                tail = t("an_wi_excess", lang, x=money(w["eml_excess"], lang)) if w["eml_excess"] \
                    else t("an_wi_within", lang)
            dp = _smoney(w["pml_delta"], lang) if w["pml_delta"] else t("an_wi_same", lang)
            de = _smoney(w["eml_delta"], lang) if w["eml_delta"] else t("an_wi_same", lang)
            text = t("an_wi_line", lang, what=what, pml=money(w["pml"], lang), dp=dp, eml=money(w["eml"], lang),
                     de=de, mfl=money(w["mfl"], lang), tail=tail)
            witems.append({"change": w["change"], "value": w["value"], "label": what, "pml": w["pml"], "eml": w["eml"],
                           "mfl": w["mfl"], "pml_delta": w["pml_delta"], "eml_delta": w["eml_delta"],
                           "mfl_delta": w["mfl_delta"], "eml_excess": w.get("eml_excess"), "text": text,
                           "calibrated": ae.CALIBRATED})
            wrows.append([what, money(w["pml"], lang), money(w["eml"], lang), money(w["mfl"], lang)])
        lists.append(_li(t("an_whatif_title", lang), [x["text"] for x in witems] or [t("an_wi_none", lang)],
                         {"columns": [t("col_variant", lang), "PML", "EML", "MFL"], "rows": wrows,
                          "widths": [34, 22, 22, 22]} if wrows else None))
        js["scenarios"] = {"available": True, "items": scitems, "whatif": witems, "calibrated": ae.CALIBRATED}
    else:
        js["scenarios"] = {"available": False, "reason": SC.get("reason"), "items": [], "whatif": [],
                           "calibrated": ae.CALIBRATED}
    # удержание
    # удержание — оценка, а не факт (30.09.2026): лимит по норме на временных цифрах, таблица линий — экспертная
    rlines, est_note = [], None
    if RET.get("known"):
        temp = RET.get("status") == "temporary"
        est = t("an_ret_est" if temp else "an_ret_est_rep", lang)
        rlines.append(t("an_ret_law", lang, own=money(RET.get("own_funds"), lang), res=money(RET.get("reserves"), lang),
                        lpr=money(RET.get("limit_per_risk"), lang),
                        temp=t("an_ret_temp" if temp else "an_ret_reported", lang)))
        S_ret = RET.get("sum_insured") if RET.get("sum_insured") is not None else must.get("sum_insured")
        if RET.get("sum_within_limit_20") is False:
            rlines.append(t("an_ret_over20", lang))
        elif RET.get("limit_per_risk") is not None and S_ret is not None:
            rlines.append(t("an_ret_sum_in", lang, sum=money(S_ret, lang), lpr=money(RET.get("limit_per_risk"), lang)))
        if RET.get("line_retention") is not None:
            rlines.append(t("an_ret_line", lang, cls=RET.get("line_class") or cls,
                            line=money(RET.get("line_retention"), lang)))
            rlines.append(t("an_ret_calc", lang, limit=money(RET.get("limit"), lang)))
        else:
            rlines.append(t("an_ret_calc_one", lang, limit=money(RET.get("limit"), lang)))
        rlines.append(t("an_ret_cmp", lang, eml=money(RET.get("eml"), lang), limit=money(RET.get("limit"), lang),
                        mfl=money(RET.get("mfl"), lang)))
        if RET["verdict"] == "within":
            rlines.append(t("an_ret_within", lang, est=est))
        elif RET["verdict"] == "mfl_excess":
            rlines.append(t("an_ret_mfl", lang, x=money(RET.get("mfl_excess"), lang), est=est))
        else:
            rlines.append(t("an_ret_eml", lang, x=money(RET.get("eml_excess"), lang),
                            y=money(RET.get("mfl_excess"), lang), est=est))
        parts = ([t("an_ret_note_temp", lang)] if temp else []) + \
            ([t("an_ret_note_line", lang)] if RET.get("line_retention") is not None else [])
        if parts:
            est_note = t("an_ret_note", lang, what="; ".join(parts))
            rlines.append(est_note)
    else:
        rlines.append(t("an_ret_unknown", lang))
    js["retention"] = {**{k: RET.get(k) for k in ("known", "limit", "limit_per_risk", "line_retention", "line_class",
                                                   "own_funds", "reserves", "status", "eml", "mfl", "eml_excess",
                                                   "mfl_excess", "sum_within_limit_20", "verdict", "need",
                                                   "sum_insured", "funds_source", "line_rule")},
                       "estimate": bool(RET.get("known")), "estimate_note": est_note,
                       "legal_ref": tx.label(tx.LEGAL_REFS, RET.get("legal_ref") or "Положение № 1806, п. 15", lang),
                       "lines": rlines, "text": " ".join(rlines), "calibrated": ae.CALIBRATED}
    lists.append(_li(t("an_ret_title", lang), rlines))

    # ---------- 5. балл ----------
    S5 = A.get("score") or {}
    if S5.get("available"):
        prm = S5.get("params") or {}
        citems, crows = [], []
        for c in S5.get("components") or []:
            code, v = c["code"], c.get("value")
            if code == "rate":
                why = t("an_why_rate", lang, v=_mult(v, lang), lo=_mult(prm["rate_ratio"][0], lang),
                        hi=_mult(prm["rate_ratio"][1], lang)) if v is not None else t("an_why_rate_na", lang)
            elif code == "mfl_retention":
                why = t("an_why_mfl_ret", lang, v=_mult(v, lang), lo=_mult(prm["mfl_to_retention"][0], lang),
                        hi=_mult(prm["mfl_to_retention"][1], lang)) if c.get("basis") == "retention" \
                    else t("an_why_mfl_sum", lang, v=tx.pct_fixed(v or 0, lang, 0))
            elif code == "losses":
                why = t("an_why_losses", lang, v=v) if v is not None else t("an_why_unknown", lang,
                                                                            v=prm.get("unknown_points"))
            elif code == "insurance_to_value":
                why = t("an_why_itv", lang, v=tx.pct_fixed((v or 0) * 100, lang, 0))
            elif code == "seismic":
                why = (t("an_why_seismic", lang, v=_pts(v, lang)) if v is not None else t("an_why_unknown", lang,
                                                                              v=prm.get("unknown_points"))) \
                    if c["applicable"] else t("an_why_seismic_na", lang)
            elif code == "external_stats":
                why = t("an_why_ext", lang, v=_mult(v, lang)) if c["applicable"] and v is not None \
                    else t({"kind": "an_why_ext_kind", "region_unknown": "an_why_ext_region",
                            "no_regional": "an_why_ext_noreg"}.get(c.get("reason"), "an_why_ext_na"), lang)
            else:
                why = ""
            name = tx.label(tx.SCORE_COMP_LABELS, code, lang)
            pts = tx._num(float(c["points"]), lang, 0)
            if c["applicable"]:
                text = t("an_sc_comp", lang, name=name, points=_pts(c["points"], lang),
                         weight=_mult(c.get("weight") or 0, lang),
                         contrib=tx._num(float(c.get("contribution") or 0), lang, 1), why=why)
            else:
                text = t("an_sc_comp_na", lang, name=name, why=why)
            citems.append({"code": code, "name": name, "points": c["points"], "weight": c.get("weight"),
                           "contribution": c.get("contribution"), "applicable": c["applicable"], "value": v,
                           "why": why, "text": text, "calibrated": ae.CALIBRATED})
            crows.append([name, pts if c["applicable"] else "—",
                          _mult(c.get("weight") or 0, lang) if c["applicable"] else "—",
                          tx._num(float(c.get("contribution") or 0), lang, 1) if c["applicable"] else "—"])
        b = S5.get("bounds") or [20, 40, 60, 80]
        level5 = tx.label(tx.LEVEL5_LABELS, S5.get("level") or "moderate", lang)
        act_lvl = tx.label(tx.LEVEL_LABELS, S5.get("act_level") or "moderate", lang)
        stext = t("an_sc_score", lang, score=tx._num(float(S5.get("score") or 0), lang, 1), level=level5, act=act_lvl,
                  b1=b[0], b2=b[1], b3=b[2], b4=b[3])
        js["score"] = {"available": True, "score": S5.get("score"), "level": S5.get("level"), "level_label": level5,
                       "housing_excluded": list(S5.get("housing_excluded") or []),
                       "act_level": S5.get("act_level"), "act_level_label": act_lvl, "components": citems,
                       "bounds": b, "text": stext, "override": bool(S5.get("override")), "calibrated": ae.CALIBRATED}
        lists.append(_li(t("an_score_title", lang), [stext] + [x["text"] for x in citems],
                         {"columns": [t("col_indicator", lang), t("col_points", lang), t("col_weight", lang),
                                      t("col_contribution", lang)], "rows": crows, "widths": [52, 16, 16, 16]},
                         notes=[stext]))
        lists[-1]["items"] = [stext] + [x["text"] for x in citems]
    else:
        js["score"] = {"available": False, "calibrated": ae.CALIBRATED}

    # ---------- 6. рынок и статистика ----------
    mitems, msrc, mrows = [], [], []
    row_note = None
    if mk.get("available"):
        pack = t("an_m_pack", lang, pack=_pack_text(mk, lang)) if mk.get("pack") else ""
        line = t("an_m_rate", lang, cls=cls, pack=pack, rate=pct(mk["rate_pct"], lang), date=_date(mk["rate_date"]),
                 months=mk.get("months") or "—")
        mitems.append(line)
        mrows.append([t("an_t_market", lang), pct(mk["rate_pct"], lang), _date(mk["rate_date"])])
        if mk.get("loss_ratio_pct") is not None:
            mitems.append(t("an_m_lr", lang, lr=tx.pct_fixed(mk["loss_ratio_pct"], lang, 1), date=_date(mk["rate_date"])))
            mrows.append([t("an_m_lr_label", lang), tx.pct_fixed(mk["loss_ratio_pct"], lang, 1),
                          _date(mk["rate_date"])])
        if mk.get("rate_full_year_pct") is not None and mk.get("full_year"):
            # полный год — отдельной строкой, как на экране (не хвостом строки убыточности среза)
            fy = t("an_m_fy", lang, year=mk["full_year"], rate=pct(mk["rate_full_year_pct"], lang),
                   lr=tx.pct_fixed(mk["loss_ratio_full_year_pct"] or 0, lang, 1))
            mitems.append(fy)
            # в таблице документа — две строки (ставка, убыточность), как две величины на экране
            mrows.append([t("an_m_fy_rate_label", lang, year=mk["full_year"]), pct(mk["rate_full_year_pct"], lang),
                          str(mk["full_year"])])
            if mk.get("loss_ratio_full_year_pct") is not None:
                mrows.append([t("an_m_fy_lr_label", lang, year=mk["full_year"]),
                              tx.pct_fixed(mk["loss_ratio_full_year_pct"], lang, 1), str(mk["full_year"])])
        if mk.get("pack"):
            # какая строка отчёта взята и почему — по объёму премий строк в market_stats
            if mk.get("alt_premiums") is not None and mk.get("pack_premiums"):
                row_note = t("an_m_row", lang, pack=_pack_text(mk, lang), rk=mk.get("row_key"), cls=cls,
                             pp=tx._num(float(mk["pack_premiums"]), lang, 0), date=_date(mk.get("rate_date")),
                             alt=mk.get("alt_row_key"), ap=tx._num(float(mk["alt_premiums"]), lang, 0),
                             share=tx.pct_fixed(mk.get("alt_share_pct") or 0, lang, 1))
            else:
                row_note = t("an_m_row_only", lang, pack=_pack_text(mk, lang), rk=mk.get("row_key"), cls=cls)
        act = mk.get("act_rate_pct")
        if act is not None and mk.get("act_vs_market_pp") is not None:
            key = "an_m_cmp_above" if mk["act_vs_market_pp"] > 0 else "an_m_cmp_below"
            cmp_line = t(key, lang, act=pct(act, lang), pp=_mult(abs(mk["act_vs_market_pp"]), lang),
                         pct=_spct(mk["act_vs_market_pct"], lang))
            if mk.get("tech_vs_market_pct") is not None:
                cmp_line += "; " + t("an_m_cmp_tech", lang, tech=pct(mk.get("technical_pct"), lang),
                                     pct=_spct(mk["tech_vs_market_pct"], lang))
            mitems.append(cmp_line)
            mrows.append([t("an_m_cmp_label", lang), _spct(mk["act_vs_market_pct"], lang),
                          pct(act, lang) + " / " + pct(mk["rate_pct"], lang)])
        if mk.get("missing_quarters") and lang == "ru":
            mitems.append(t("an_m_gap", lang, what=", ".join(mk["missing_quarters"])))
        s = mk.get("source") or {}
        if s:
            file = ""
            if lang == "ru" and "(" in (s.get("title_ru") or ""):
                file = " (" + s["title_ru"].split("(", 1)[1]
                file = file if file.endswith(")") else file + ")"
            msrc.append(t("an_m_src", lang, file=file, date=_date(s.get("slice") or s.get("as_of")), url=s["url"]))
            src_add(t("an_m_src", lang, file="", date=_date(s.get("slice") or s.get("as_of")), url="")
                    .rstrip(" —").split(": ", 1)[-1], s["url"], s.get("domain"), s.get("slice") or s.get("as_of"))
    else:
        mitems.append(t("an_m_na", lang, cls=cls))
    ST = A.get("stats") or {}
    sitems2 = []
    for i in ST.get("indicators") or []:
        name = tx.label(tx.STAT_LABELS, i["id"], lang) if i["id"] in tx.STAT_LABELS else \
            (i.get("name_ru") if lang == "ru" else i["id"])
        where = t("an_st_region", lang) + ": " + (region_label(must, lang) or "") if i["scope"] == "region" \
            else t("an_st_republic", lang)
        if i["status"] != "ok":
            value = t("an_st_nodata", lang)
        else:
            unit = tx.label(tx.STAT_UNITS, i.get("unit_ru"), lang) if i.get("unit_ru") in tx.STAT_UNITS else \
                (i.get("unit_ru") if lang == "ru" else "")
            num = i["value"]
            if lang == "ru" and i.get("unit_ru") in tx.UNIT_FORMS_RU:
                unit = tx.plural_ru(num, tx.UNIT_FORMS_RU[i["unit_ru"]])      # 32 случая, 21 кража
            value = tx._num(float(num), lang, 0 if float(num) == int(num) or abs(num) >= 1000 else 2) + \
                (("" if lang == "en" and unit.startswith("%") else " ") + unit if unit else "")
            if i.get("per_1000") is not None:
                value += t("an_st_per1000", lang, v=tx._num(float(i["per_1000"]), lang, 2))
        cmpx = ""
        if i.get("vs_country"):
            cmpx = t("an_st_cmp", lang, pct=_spct(i["vs_country"]["diff_pct"], lang),
                     pts=t("an_st_pts", lang, p=tx._num(float(i["points"]), lang, 0)) if i.get("used_in_score")
                     and i.get("points") is not None else "")
        if i.get("excluded_for_kind"):
            cmpx += t("an_st_kind", lang, kind=tx.label(tx.GROUP_LABELS, A.get("group") or "other", lang))
        line = t("an_st_line", lang, name=name, where=where, period=i.get("period") or "—", value=value, cmp=cmpx)
        srcs = []
        for s in i.get("sources") or []:
            if not s.get("url"):
                continue
            nm = (s.get("name_ru") if lang == "ru" else name) or name
            line_s = t("an_st_src" if s.get("fetched_at") else "an_st_src_nf", lang, src=s.get("source") or "",
                       name=nm, period=s.get("period") or "—", fetched=_date(s.get("fetched_at")), url=s["url"])
            if line_s not in msrc and line_s not in srcs:
                srcs.append(line_s)
            src_add((s.get("source") or "") + " — " + nm, s["url"], s.get("source"), s.get("period"))
        sitems2.append({"id": i["id"], "name": name, "scope": i["scope"], "status": i["status"],
                        "value": i.get("value"), "value_text": value, "period": i.get("period"),
                        "vs_country": i.get("vs_country"), "used_in_score": i.get("used_in_score"),
                        "excluded_for_kind": bool(i.get("excluded_for_kind")),
                        "points": i.get("points"), "text": line, "source_lines": srcs,
                        "sources": [{"title": x.get("name_ru") if lang == "ru" else name, "url": x.get("url"),
                                     "source": x.get("source"), "period": x.get("period"),
                                     "fetched_at": x.get("fetched_at")} for x in i.get("sources") or []],
                        "calibrated": ae.CALIBRATED})
        mitems.append(line)
        mrows.append([name, value + cmpx, f"{i.get('period') or '—'}; {where}"])
        msrc += srcs
    if not sitems2:
        mitems.append(t("an_st_none", lang, cls=cls))
    nf = tx.label(tx.STAT_NOT_FOUND, cls, lang) if cls in tx.STAT_NOT_FOUND else \
        ((ST.get("not_found") or {}).get(cls) if lang == "ru" else None)
    if nf:
        mitems.append(t("an_st_nf", lang, what=nf))
    js["market"] = {"available": bool(mk.get("available")), "rate_pct": mk.get("rate_pct"),
                    "rate_date": mk.get("rate_date"), "months": mk.get("months"),
                    "loss_ratio_pct": mk.get("loss_ratio_pct"), "rate_full_year_pct": mk.get("rate_full_year_pct"),
                    "loss_ratio_full_year_pct": mk.get("loss_ratio_full_year_pct"), "full_year": mk.get("full_year"),
                    "pack": bool(mk.get("pack")), "row_key": mk.get("row_key"), "row_note": row_note,
                    "pack_premiums": mk.get("pack_premiums"), "alt_row_key": mk.get("alt_row_key"),
                    "alt_premiums": mk.get("alt_premiums"), "alt_share_pct": mk.get("alt_share_pct"),
                    "act_rate_pct": mk.get("act_rate_pct"),
                    "act_vs_market_pp": mk.get("act_vs_market_pp"), "act_vs_market_pct": mk.get("act_vs_market_pct"),
                    "technical_pct": mk.get("technical_pct"), "tech_vs_market_pct": mk.get("tech_vs_market_pct"),
                    "source": ({"title": (mk.get("source") or {}).get("title_ru") if lang == "ru" else "NAPP",
                                "url": (mk.get("source") or {}).get("url"),
                                "domain": (mk.get("source") or {}).get("domain"),
                                "as_of": (mk.get("source") or {}).get("slice")} if mk.get("source") else None),
                    "lines": list(mitems), "source_lines": list(msrc), "calibrated": ae.CALIBRATED}
    js["stats"] = {"available": bool(ST.get("available")), "region": region_label(must, lang),
                   "indicators": sitems2, "not_found": nf, "applicable": bool(ST.get("applicable")),
                   "points": ST.get("points"), "calibrated": ae.CALIBRATED}
    lists.append(_li(t("an_market_title", lang), mitems,
                     {"columns": [t("col_indicator", lang), t("col_value", lang), t("col_period", lang)],
                      "rows": mrows, "widths": [40, 32, 28]} if mrows else None, sources=msrc,
                     notes=[row_note] if row_note else None))

    # ---------- 7. франшиза: варианты ----------
    FT = A.get("franchise") or {}
    fr_verdict = _fr_text(D["franchise"], lang)
    if FT.get("available"):
        fitems2, frows2 = [], []
        for r in FT.get("rows") or []:
            p = pct(r["pct"], lang)
            if not r.get("ok"):
                text = t("an_fr_na_row", lang, pct=p)
                frows2.append([p, money(r["amount"], lang), "—", "—", "—"])
            else:
                note = (t("an_fr_floor", lang) if r.get("floored") else "") + \
                    (t("an_fr_extra", lang) if r.get("extrapolated") else "")
                text = t("an_fr_line", lang, pct=p, amount=money(r["amount"], lang), rate=pct(r["rate_pct"], lang),
                         premium=money(r["premium"], lang), saving=money(r["saving"], lang), note=note)
                frows2.append([p, money(r["amount"], lang), pct(r["rate_pct"], lang) + note, money(r["premium"], lang),
                               money(r["saving"], lang) + (" (" + tx.pct_fixed(r["saving_pct"], lang, 1) + ")"
                                                           if r.get("saving_pct") is not None else "")])
            fitems2.append({**{k: r.get(k) for k in ("pct", "amount", "multiplier", "rate_pct", "premium", "saving",
                                                     "saving_pct", "floored", "extrapolated", "ok")},
                            "text": text, "calibrated": ae.CALIBRATED})
        fnote2 = t("an_fr_note", lang, verdict=fr_verdict.rstrip("."))
        js["franchise"] = {"available": True, "reference_only": True, "base_premium": FT.get("base_premium"),
                           "base_rate_pct": FT.get("base_rate_pct"), "cap_pct": FT.get("cap_pct"), "rows": fitems2,
                           "verdict": fr_verdict, "note": fnote2, "calibrated": ae.CALIBRATED}
        lists.append(_li(t("an_fr_title", lang), [x["text"] for x in fitems2],
                         {"columns": [t("col_franchise", lang), t("col_per_loss", lang), t("col_rate", lang),
                                      t("col_premium", lang), t("col_saving", lang)], "rows": frows2,
                          "widths": [12, 24, 14, 24, 26]}, notes=[fnote2]))
    else:
        txt = t("an_fr_na_" + (FT.get("reason") or "error"), lang)
        js["franchise"] = {"available": False, "reason": FT.get("reason"), "text": txt, "verdict": fr_verdict,
                           "calibrated": ae.CALIBRATED}
        lists.append(_li(t("an_fr_title", lang), [txt]))

    # ---------- 8. мероприятия: эффект ----------
    ME = A.get("measures") or {}
    ms_texts = {m["json"]["code"]: m for m in (_measures_view(D.get("measures"), lang)["items"] or [])}
    mitems3, mrows3 = [], []
    for it in ME.get("items") or []:
        mv = (ms_texts.get(it["code"]) or {}).get("json") or {}
        text0 = (mv.get("text") or it["code"]).rstrip(".")
        if it.get("ratio"):
            delta = _smoney(it["premium_delta"], lang) if it.get("premium_delta") else t("an_ms_zero", lang)
            text = t("an_ms_line", lang, text=text0, before=pct(it["tech_before"], lang),
                     after=pct(it["tech_after"], lang), eff=_spct(it["effect_pct"], lang), delta=delta)
            mrows3.append([text0, f"{pct(it['tech_before'], lang)} → {pct(it['tech_after'], lang)} "
                                  f"({_spct(it['effect_pct'], lang)})", delta])
        else:
            text = t("an_ms_line_na", lang, text=text0)
            mrows3.append([text0, t("an_f_zero", lang), "—"])
        mitems3.append({"code": it["code"], "text": text, "name": text0, "effect_pct": it.get("effect_pct"),
                        "tech_before": it.get("tech_before"), "tech_after": it.get("tech_after"),
                        "tech_delta_pp": it.get("tech_delta_pp"), "premium_delta": it.get("premium_delta"),
                        "calibrated": ae.CALIBRATED})
    total_txt = None
    if ME.get("tech_after_all") is not None and ME.get("premium_after") is not None:
        total_txt = t("an_ms_total", lang, before=pct(ME["tech_before"], lang), after=pct(ME["tech_after_all"], lang),
                      pb=money(ME["premium_before"], lang), pa=money(ME["premium_after"], lang))
        if ME.get("floor_applied") or ME.get("premium_after") == ME.get("premium_before"):
            total_txt += " " + t("ms_total_floor", lang)
    js["measures"] = {"items": mitems3, "total": {"tech_before": ME.get("tech_before"),
                                                  "tech_after": ME.get("tech_after_all"),
                                                  "premium_before": ME.get("premium_before"),
                                                  "premium_after": ME.get("premium_after"),
                                                  "premium_delta": ME.get("premium_delta"),
                                                  "floor_applied": bool(ME.get("floor_applied")), "text": total_txt},
                      "calibrated": ae.CALIBRATED}
    lists.append(_li(t("an_ms_title", lang), [x["text"] for x in mitems3] or [t("an_ms_none", lang)],
                     {"columns": [t("col_measure", lang), t("col_tech", lang), t("col_act_premium", lang)],
                      "rows": mrows3, "widths": [46, 30, 24]} if mrows3 else None,
                     notes=[total_txt] if total_txt else None))

    # ---------- 9. резюме ----------
    sentences = _an_summary(D, A, js, lang)
    js["summary"] = {"text": " ".join(sentences), "sentences": sentences}
    return {"summary": t("an_summary", lang, text=" ".join(sentences)) if sentences else None, "lists": lists,
            "json": js}


def _an_summary(D: dict, A: dict, js: dict, lang: str) -> list:
    """Резюме раздела 4 в 5–7 предложениях — из тех же чисел, что в таблицах."""
    out = []
    must, rate_res = D["must"], D["rate"]
    R = A.get("risks") or {}
    items = R.get("items") or []
    if R.get("whole_class"):
        out.append(t("an_sum_whole", lang, cls=must["class_code"]))
    elif items:
        top = items[:3]
        out.append(t("an_sum_risks", lang, what=", ".join(
            t("an_sum_risk_item", lang, name=_peril_label(x["code"], lang, x.get("name_ru"), x.get("class_code")).lower()
              if lang != "en" else _peril_label(x["code"], lang, x.get("name_ru"), x.get("class_code")).lower(),
              share=tx.pct_fixed(x["share_of_net_pct"], lang, 1)) for x in top)))
    fx = (A.get("factors") or {}).get("items") or []
    ups = [f"{_fo(x['factor'], x['option'], lang)} (×{_mult(x['multiplier'], lang)}{_dmark(x, lang, True)})" for x in fx
           if x["direction"] == "up"]
    downs = [f"{_fo(x['factor'], x['option'], lang)} (×{_mult(x['multiplier'], lang)}{_dmark(x, lang, True)})" for x in fx
             if x["direction"] == "down"]
    if ups:
        out.append(t("an_sum_up", lang, what=", ".join(ups)))
    if downs:
        out.append(t("an_sum_down", lang, what=", ".join(downs)))
    elif not ups:
        out.append(t("an_sum_nodown", lang))
    T = A.get("tariff") or {}
    mrate = (A.get("market") or {}).get("rate_pct")
    if rate_res.get("mode") == "tariff" and rate_res.get("applied_pct") is not None:
        key = "an_sum_tariff" if rate_res.get("base_source") == "product_rate" else "an_sum_tariff_tech"
        out.append(t(key, lang, act=pct(rate_res["applied_pct"], lang), base=pct(rate_res["base_pct"], lang),
                     adj=pct(rate_res.get("adj_pct") or 0, lang),
                     level=tx.label(tx.LEVEL_LABELS, D["risk"]["level"], lang),
                     min=t("an_sum_min", lang, m=pct(rate_res["min_pct"], lang)) if rate_res.get("min_pct") else "",
                     tech=pct(T.get("technical_pct"), lang) if T.get("technical_pct") is not None else
                     t("an_t_na_short", lang),
                     market=t("an_sum_market", lang, m=pct(mrate, lang)) if mrate else ""))
    else:
        what = pct(rate_res["applied_pct"], lang) if rate_res.get("applied_pct") is not None else \
            t("rate_undefined", lang)
        out.append(t("an_sum_tariff_other", lang, what=what))
    RET = A.get("retention") or {}
    if RET.get("eml") is not None:
        est = t("an_ret_est" if RET.get("status") == "temporary" else "an_ret_est_rep", lang)
        if not RET.get("known"):
            ret = t("an_sum_ret_na", lang)
        elif RET.get("verdict") == "within":
            ret = t("an_sum_ret_within", lang, est=est)
        elif RET.get("verdict") == "mfl_excess":
            ret = t("an_sum_ret_mfl", lang, limit=money(RET.get("limit"), lang), est=est)
        else:
            ret = t("an_sum_ret_eml", lang, est=est)
        out.append(t("an_sum_loss", lang, eml=money(RET["eml"], lang), mfl=money(RET["mfl"], lang), ret=ret))
    advice = []
    meas = [s for s in (A.get("sensitivity") or {}).get("items") or [] if s["kind"] == "measure"
            and s["direction"] == "down"]
    if meas:
        s = meas[0]
        advice.append(t("an_sum_advice_measure", lang,
                        text=f"{_flabel(s['factor'], lang).lower()}: {_olabel(s['factor'], s['option_to'], lang)}",
                        eff=_spct(s["delta_pct"], lang)))
    else:
        mi = [m for m in (js.get("measures") or {}).get("items") or [] if m.get("effect_pct")]
        if mi:
            advice.append(t("an_sum_advice_measure", lang, text=mi[0]["name"][:1].lower() + mi[0]["name"][1:],
                            eff=_spct(mi[0]["effect_pct"], lang)))
    fr = D["franchise"]
    if fr.get("status") in (None, "none") and fr.get("code") == "fr_not_needed":
        advice.append(t("an_sum_advice_fr_no", lang))
    elif fr.get("needed"):
        advice.append(t("an_sum_advice_fr", lang))
    if advice:
        out.append(t("an_sum_advice", lang, what="; ".join(advice)))
    clar = [_flabel(x["factor"], lang).lower() for x in fx if x["source"] in ("default", "not_set")][:4]
    if clar:
        out.append(t("an_sum_clarify", lang, what=", ".join(clar)))
    S5 = A.get("score") or {}
    if S5.get("available") and len(out) < 7:
        out.append(t("an_sum_score", lang, score=tx._num(float(S5.get("score") or 0), lang, 1),
                     level=tx.label(tx.LEVEL5_LABELS, S5.get("level") or "moderate", lang)))
    return out[:7]


def _scenario_what(it: dict, lang: str) -> str:
    """Подпись сценария: состояние защиты (у имущества) и причина — ровно то, что посчитано."""
    what = t(it["what"], lang, **(it.get("what_params") or {}))
    if it.get("state"):
        what = t(it["state"], lang) + ": " + what
    return what


def _assumption_text(a: dict, lang: str) -> str:
    p = dict(a.get("params") or {})
    v = p.get("value")
    if a["code"] == "as_object_type":
        p["value"] = _otype_label(v, lang) if v else t("na", lang)
    elif v is not None and a["code"] in ("as_construction", "as_activity", "as_vehicle_type", "as_activity_text",
                                         "as_activity_by_type"):
        p["value"] = tx.label(tx.RA_VALUE_LABELS, v, lang)
    if a["code"] == "as_activity_by_type":
        p["type"] = _otype_label(p.get("type"), lang) if p.get("type") else t("na", lang)
    return t(a["code"], lang, **p)


def _measures_view(ms: Optional[dict], lang: str) -> dict:
    """Рекомендации страхователю: строки для раздела 5 и JSON (measures[], measures_summary)."""
    ms = ms or {"items": [], "total": {"count": 0}}
    out = []
    for it in ms.get("items") or []:
        text = (it.get("text") or {}).get(lang) or (it.get("text") or {}).get("ru") or ""
        why = (it.get("why") or {}).get(lang) or (it.get("why") or {}).get("ru") or ""
        eff = None
        if it.get("effect_pct") is not None:
            eff = ("−" if it["effect_pct"] < 0 else "+") + pct(abs(it["effect_pct"]), lang, 1)
        if it.get("premium_delta"):
            effect = t("ms_effect", lang, effect=eff, delta=_signed(it["premium_delta"], lang))
        elif (ms.get("total") or {}).get("statutory"):
            effect = t("ms_effect_statutory", lang)
        elif it.get("effect_pct") is not None and it.get("premium_delta") == 0:
            effect = t("ms_effect_zero", lang)
        elif it.get("effect_pct") is not None:
            effect = t("ms_effect_pct", lang, effect=eff)
        else:
            effect = t("ms_effect_na", lang)
        deadline = t("ms_deadline_default" if it.get("deadline_default") else "ms_deadline", lang,
                     n=it.get("deadline_days"))
        line = t("ms_item", lang, text=text.rstrip("."), why=why if why.endswith(".") else why + ".",
                 deadline=deadline.rstrip("."), mandatory=t("ms_mandatory", lang) if it.get("mandatory") else "",
                 effect=effect)
        out.append({"line": line, "json": {
            "code": it["code"], "text": text, "why": why, "effect_pct": it.get("effect_pct"),
            "premium_delta": it.get("premium_delta"), "mandatory": bool(it.get("mandatory")),
            "deadline_days": it.get("deadline_days"), "deadline_default": bool(it.get("deadline_default")),
            "legal_ref": it.get("legal_ref"), "source": it.get("source"), "effect_text": effect,
            "calibrated": ae.CALIBRATED}})
    tot = ms.get("total") or {}
    summary = {"count": tot.get("count", 0), "with_effect": tot.get("with_effect", 0),
               "premium_before": tot.get("premium_before"), "premium_after": tot.get("premium_after"),
               "delta": tot.get("delta"), "floor_applied": bool(tot.get("floor_applied")), "text": None,
               "calibrated": ae.CALIBRATED}
    if tot.get("delta"):
        summary["text"] = t("ms_total", lang, after=money(tot["premium_after"], lang),
                            before=money(tot["premium_before"], lang))
        if tot.get("floor_applied"):
            summary["text"] += " " + t("ms_total_floor", lang)
    return {"items": out, "summary": summary}


def _fr_text(fr: dict, lang: str) -> str:
    status = fr.get("status")
    if status == "applied":
        params = dict(type=tx.label(tx.FR_TYPE_LABELS, fr.get("type") or "unconditional", lang),
                      pct=pct(fr.get("size_pct"), lang), amount=money(fr.get("size_amount"), lang))
        if _fr_effect_done(fr):
            text = t("fr_applied", lang, before=money(fr.get("premium_before"), lang),
                     after=money(fr.get("premium_after"), lang), **params)
            text += _fr_effect_notes(fr, lang)
        else:
            text = t("fr_applied_na", lang, **params)
        w = fr.get("warning")
        if w:
            text += " " + t(w["code"], lang, **_fmt_params(w.get("params"), lang))
        return text
    if status == "statutory" and fr.get("warning"):
        return t("fr_statutory", lang) + ". " + t("fr_w_statutory", lang)
    base = _fr_text_base(fr, lang)
    if status == "proposed" and fr.get("size_pct"):
        params = dict(type=tx.label(tx.FR_TYPE_LABELS, fr.get("type") or "unconditional", lang),
                      pct=pct(fr["size_pct"], lang), amount=money(fr.get("size_amount"), lang))
        if _fr_effect_done(fr):
            base += t("fr_proposed_tail", lang, before=money(fr.get("premium_before"), lang),
                      after=money(fr.get("premium_after"), lang), **params)
            base += _fr_effect_notes(fr, lang)
        else:
            base += t("fr_proposed_tail_na", lang, **params)
    return base


def _fr_effect_notes(fr: dict, lang: str) -> str:
    """Пояснения к премии с франшизой (и применённой, и предложенной): упёрлась в минимум, множитель выше 2 %."""
    out = ""
    if fr.get("floor_applied"):
        out += " " + t("fr_floor_note", lang)
    if (fr.get("engine") or {}).get("extrapolated"):
        out += " " + t("fr_extrapolated_note", lang)
    return out


def _fr_effect_done(fr: dict) -> bool:
    """Эффект франшизы на премию акта посчитан (есть шаг «ставка акта × множитель»)."""
    return any(h.get("code") == "frh_apply" for h in fr.get("how") or [])


def _fr_text_base(fr: dict, lang: str) -> str:
    code = fr.get("code") or "fr_not_needed"
    if code == "fr_advise_range":
        s = fr["size"]
        if s["from_pct"] == s["to_pct"]:           # вилка с равными границами — одно число, а не «от 5 % до 5 %»
            rng = pct(s["to_pct"], lang)
            amount = money(s["to_amount"], lang)
        elif s["from_pct"] <= 0:
            rng = t("fr_range_upto", lang, to=pct(s["to_pct"], lang))
            amount = t("fr_range_upto", lang, to=money(s["to_amount"], lang))
        else:
            rng = t("fr_range", lang, from_=pct(s["from_pct"], lang), to=pct(s["to_pct"], lang))
            amount = t("fr_range", lang, from_=money(s["from_amount"], lang), to=money(s["to_amount"], lang))
        return t("fr_advise_range", lang, range=rng, amount=amount)
    return t(code, lang)


# --------------------------------------------------------------------------- #
#  POST /act/make, GET /act/...
# --------------------------------------------------------------------------- #

def _meta(row: dict) -> dict:
    return {"id": row["id"], "number": row.get("number") or _number(row["id"], row["created_at"]),
            "created_at": row["created_at"], "expires_at": row["expires_at"]}


def _number(aid: str, created: str) -> str:
    return f"{created[:10].replace('-', '')}-{aid[:6].upper()}"


@router.post("/act/make")
def act_make(request: Request, body: dict = Body(...)):
    """Акт из пяти разделов. Тело и ответ — см. докстринг модуля и отчёт разработчика."""
    user = _user(request)
    owner = guest.owner_of(request, user)
    if not owner:
        return _fail(request, "Не удалось опознать сессию — откройте приложение заново", 400)
    if not isinstance(body, dict):
        return _fail(request, "Тело запроса — объект JSON", 422)
    lang = _lang(request, body.get("lang"))
    with db.tx() as con:
        ensure_tables(con)
        clean, errs = validate(con, body)
        if errs:
            db.audit(con, _who(user, owner), "акт: ошибка ввода", None, {"fields": sorted(errs)})
            return _fail(request, "Проверьте поля: " + ", ".join(sorted(errs)), 422, errors=errs)
        D = build_data(con, clean, owner, lang)
        aid = secrets.token_hex(8)
        now = _now()
        meta = {"id": aid, "created_at": _iso(now), "expires_at": _iso(now + timedelta(seconds=ACT_TTL_SEC))}
        meta["number"] = _number(aid, meta["created_at"])
        out = render(D, lang, meta)
    # сеть здесь не нужна: акт собирается по шаблонам (литературная связка моделью убрана 29.09.2026)
    with db.tx() as con:
        con.execute("INSERT INTO acts (id, owner_key, user_id, lang, tariff_version_id, settings_id, act_json, "
                    "created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (aid, owner, (user or {}).get("id") or 0, lang, D.get("tariff_version_id"),
                     D.get("settings_id"), json.dumps({"meta": meta, "data": D}, ensure_ascii=False, default=str),
                     meta["created_at"], meta["expires_at"]))
        db.audit(con, _who(user, owner), "акт сформирован", f"act:{aid}",
                 {"product": D["must"]["product_code"], "class": D["must"]["class_code"],
                  "level": D["risk"]["level"], "rate_mode": D["rate"]["mode"],
                  "decision": D["decision"]["code"], "photos": D["inspection"]["photos"],
                  "ai": D["inspection"]["ai"], "discrepancies": len(D["discrepancies"]),
                  "dropped_pd": D.get("dropped_pd") or 0, "sources_downgraded": D.get("sources_downgraded") or 0,
                  "lang": lang, "franchise": D["franchise"].get("status"),
                  "scenarios": bool((D.get("scenarios") or {}).get("available")),
                  "measures": len((D.get("measures") or {}).get("items") or []),
                  "market": (D.get("market") or {}).get("verdict"),
                  "market_used": (D.get("market") or {}).get("used"),
                  "contract_check": ((D.get("contract_check") or {}).get("summary") or {}).get("verdict"),
                  "cross_differs": (D.get("cross_check") or {}).get("differs", 0)})
        for e in D.get("block_errors") or []:
            db.audit(con, _who(user, owner), "акт: блок не посчитан", f"act:{aid}", e)
    return _reply(request, out)


def _load_act(request: Request, aid: str):
    """(данные, meta) своего живого акта или None. Чужой акт не отличается от несуществующего."""
    user = _user(request)
    owner = guest.owner_of(request, user)
    if not re.fullmatch(r"[0-9a-f]{16}", aid or ""):
        return None
    with db.tx() as con:
        ensure_tables(con)
        rows = db.rows(con, "SELECT * FROM acts WHERE id=? AND expires_at > ?", aid, _iso(_now()))
    if not rows:
        return None
    row = rows[0]
    if row["owner_key"] != owner and (user or {}).get("role") != ADMIN:
        return None
    stored = json.loads(row["act_json"])
    return stored["data"], stored["meta"], row


@router.get("/act/settings")
def act_settings_get():
    """Пороги лёгкого движка (уровень риска, поправки, износ) — чтение открыто, правка — администратор."""
    with db.tx() as con:
        ensure_tables(con)
        s = load_settings(con)
    return {"settings": s, "defaults": ae.DEFAULT_SETTINGS, "calibrated": ae.CALIBRATED}


@router.put("/act/settings")
def act_settings_put(request: Request, body: dict = Body(...)):
    """Новая версия настроек (старые остаются — акты хранят settings_id). Только администратор (guard)."""
    user = _user(request)
    if (user or {}).get("role") != ADMIN:
        return JSONResponse({"detail": "нужны права администратора"}, status_code=403)
    new = body.get("settings") if isinstance(body.get("settings"), dict) else body
    errs = ae.check_settings(new or {})
    if errs:
        return JSONResponse({"ok": False, "errors": errs}, status_code=422)
    with db.tx() as con:
        ensure_tables(con)
        cur = con.execute("INSERT INTO act_settings (created_at, created_by, settings_json, calibrated, note) "
                          "VALUES (?,?,?,?,?)", (db.now(), user.get("login"), json.dumps(new, ensure_ascii=False),
                                                 0, str(body.get("note") or "")[:300]))
        db.audit(con, user.get("login") or "админ", "акт: настройки изменены", f"act_settings:{cur.lastrowid}",
                 {"keys": sorted(new)})
        return {"ok": True, "id": cur.lastrowid, "settings": load_settings(con)}


@router.get("/act/{aid}.docx")
def act_docx(request: Request, aid: str, lang: str = ""):
    got = _load_act(request, aid)
    if not got:
        return _fail(request, t("not_found", _lang(request, lang)), 404)
    D, meta, row = got
    out = render(D, _lang(request, lang or row["lang"]), meta)
    data = build_docx(out)
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument."
                                              "wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="act_{meta["number"]}.docx"'})


@router.get("/act/{aid}.pdf")
def act_pdf(request: Request, aid: str, lang: str = ""):
    got = _load_act(request, aid)
    if not got:
        return _fail(request, t("not_found", _lang(request, lang)), 404)
    D, meta, row = got
    out = render(D, _lang(request, lang or row["lang"]), meta)
    return Response(content=build_pdf(out), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="act_{meta["number"]}.pdf"'})


def _telegram_id(request: Request, user: Optional[dict], init_data: str) -> tuple:
    """
    Подтверждённый telegram id: подписанный initData мини-приложения (проверка app/telegram.py)
    или привязка вошедшего пользователя (users.telegram_id). (id или None, код отказа или None).
    """
    from . import telegram
    if init_data:
        res = telegram.check_init_data(init_data, telegram.bot_token())
        tid = str(((res.get("user") or {}).get("id")) or "") if res.get("ok") else ""
        return (tid, None) if tid.isdigit() else (None, "bad_init")
    if user and user.get("id"):
        with db.tx() as con:
            r = db.rows(con, "SELECT telegram_id FROM users WHERE id=?", user["id"])
        tid = str((r[0]["telegram_id"] if r else "") or "").strip()
        if tid.isdigit():
            return tid, None
    return None, "no_telegram"


@router.post("/act/{aid}/send")
def act_send(request: Request, aid: str, body: dict = Body(default={})):
    """
    Акт файлом в чат с ботом (в Telegram на телефоне скачивание из WebView не работает).
    Тело: {"format": "docx"|"pdf", "lang": "ru"|"uz"|"en" (необязательно), "initData": "..." (из мини-приложения)}.
    Только владелец акта; не больше limits.send_per_hour отправок в час на пользователя Telegram.
    """
    body = body if isinstance(body, dict) else {}
    lang = _lang(request, body.get("lang"))
    user = _user(request)
    owner = guest.owner_of(request, user)
    got = _load_act(request, aid)
    # только владелец: администратору чужой акт ботом не отправляется
    if not got or got[2]["owner_key"] != owner:
        return _fail(request, t("not_found", lang), 404)
    fmt = str(body.get("format") or "").strip().lower()
    if fmt not in SEND_FORMATS:
        return _fail(request, t("send_format", lang), 422, errors={"format": "docx или pdf"})
    from . import tgbot
    if not tgbot.connected():
        return _fail(request, t("send_bot_off", lang), 503, code="bot_off")
    tid, why = _telegram_id(request, user, str(body.get("initData") or "").strip()[:4096])
    if why == "bad_init":
        return _fail(request, t("send_bad_init", lang), 403, code="bad_init_data")
    if not tid:
        return _fail(request, t("send_no_tg", lang), 409, code="no_telegram")
    with db.tx() as con:
        ensure_tables(con)
        n_max = int(load_settings(con)["limits"]["send_per_hour"])
    res = SENDS.take("tg:" + tid, 1, n_max)
    if not res["ok"]:
        out = _fail(request, t("send_limit", lang, n=n_max), 429, limit=n_max, window_hours=1,
                    retry_after_sec=res["retry_after"], code="limit")
        out.headers["Retry-After"] = str(res["retry_after"])
        return out
    D, meta, row = got
    act_lang = _lang(request, body.get("lang") or row["lang"])
    act = render(D, act_lang, meta)
    blob = build_docx(act) if fmt == "docx" else build_pdf(act)
    filename = f"act_{meta['number']}.{fmt}"
    sent = tgbot.send_file(tid, filename, blob, SEND_FORMATS[fmt], caption=t("send_caption", act_lang,
                                                                             number=meta["number"]),
                           kind="акт")
    with db.tx() as con:
        # в журнал — без telegram id и без содержимого: формат, язык и итог
        db.audit(con, _who(user, owner), "акт: отправка ботом", f"act:{aid}",
                 {"format": fmt, "lang": act_lang, "ok": bool(sent.get("ok"))})
    if not sent.get("ok"):
        reason = str(sent.get("reason") or "")
        low = reason.lower()
        if "blocked" in low or "initiate" in low or "chat not found" in low:
            return _fail(request, t("send_start_bot", lang), 502, code="start_bot")
        return _fail(request, t("send_failed", lang), 502, code="telegram_error")
    return _reply(request, {"ok": True, "sent": True, "format": fmt, "lang": act_lang, "filename": filename,
                            "message": t("send_ok", lang), "left_this_hour": res.get("left", 0)})


@router.get("/act/{aid}")
def act_get(request: Request, aid: str, lang: str = ""):
    got = _load_act(request, aid)
    if not got:
        return _fail(request, t("not_found", _lang(request, lang)), 404)
    D, meta, row = got
    return _reply(request, render(D, _lang(request, lang or row["lang"]), meta))


# --------------------------------------------------------------------------- #
#  Word и PDF
# --------------------------------------------------------------------------- #

def build_docx(act: dict) -> bytes:
    from .docx_lite import TEXT_WIDTH, Docx
    lang = act["lang"]
    doc = Docx(lang={"ru": "ru-RU", "uz": "uz-Latn-UZ", "en": "en-GB"}[lang])
    if act.get("insurer_known"):
        doc.para(act["insurer"], bold=True, color="555555", size=20, align="center", after=60)
    doc.title(act["title"])
    doc.table([[r["label"], str(r["value"])] for r in act["header"]], widths=[3200, TEXT_WIDTH - 3200],
              header=False)
    for s in act["sections"]:
        doc.heading(f"{s['n']}. {s['title']}")
        for p in s["paragraphs"]:
            doc.para(p)
        if s["rows"]:
            doc.table([[r["label"], str(r["value"]), r.get("note") or ""] for r in s["rows"]],
                      widths=[2900, 3900, TEXT_WIDTH - 6800], header=False)
        for p in s.get("source_lines") or []:
            doc.para(p, italic=True, color="555555", size=18, after=60)
        for li in s.get("lists") or []:
            doc.para(li["title"], bold=True, after=60)
            tb = li.get("table")
            if tb and tb.get("rows"):
                # аналитика раздела 4: таблица, под ней пояснения и строки «Источник: …»
                w = tb.get("widths") or [100 // len(tb["columns"])] * len(tb["columns"])
                widths = [int(TEXT_WIDTH * x / sum(w)) for x in w]
                doc.table([list(tb["columns"])] + [[str(c) for c in r] for r in tb["rows"]], widths=widths,
                          header=True, size=17)
                for p in li.get("notes") or []:
                    doc.para(p, size=18, after=60)
                for p in li.get("sources") or []:
                    doc.para(p, italic=True, color="555555", size=16, after=40)
                continue
            for it in li["items"]:
                doc.bullet(it)
    doc.rule()
    doc.para(act["footer"], bold=True, italic=True)
    return doc.to_bytes()


# Windows — Arial; сервер (Dockerfile ставит fonts-dejavu-core) — DejaVu Sans, в нём есть узбекская ʻ
FONT_CANDIDATES = (
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
)
GLYPH_FALLBACK = {"ʻ": "'", "ʼ": "'", "−": "-", "—": "-", "×": "x", "≤": "<=", "≥": ">=", "→": "->",
                  "«": '"', "»": '"', "…": "...", "\u00a0": " "}


def _fonts():
    for reg, bold in FONT_CANDIDATES:
        if Path(reg).exists() and Path(bold).exists():
            return pymupdf.Font(fontfile=reg), pymupdf.Font(fontfile=bold)
    # на сервере без шрифтов — встроенные Helvetica (кириллица в них есть, узбекской ʻ нет)
    return pymupdf.Font("helv"), pymupdf.Font("hebo")


def build_pdf(act: dict) -> bytes:
    from .proposal import A4_H, A4_W, BLACK, CONTENT_W, GRAY, MARGIN, _Pdf

    class ActPdf(_Pdf):
        def __init__(self):
            self.doc = pymupdf.open()
            self.regular, self.bold = _fonts()
            self.page = None
            self.writers = {}
            self.y = 0.0
            self.footer = act["footer"]
            self.new_page()

        def _fit(self, s, font):
            return "".join(ch if ord(ch) < 128 or font.has_glyph(ord(ch)) else GLYPH_FALLBACK.get(ch, "?")
                           for ch in str(s))

        def _put(self, x, baseline, s, font, size, color):
            super()._put(x, baseline, self._fit(s, font), font, size, color)

        def heading(self, s, size=12.5):
            self.ensure(90)                  # заголовок раздела не остаётся внизу страницы один
            super().heading(s, size)

        def bullet(self, s, size=10, color=BLACK):
            self.ensure(size * 1.35 * 2)     # маркер не отрывается от своего текста
            super().bullet(s, size=size, color=color)

        def finish(self) -> bytes:
            self._flush()
            n = self.doc.page_count
            for i, page in enumerate(self.doc, start=1):
                tw = pymupdf.TextWriter(page.rect)
                s = self._fit(t("page", act["lang"], i=i, n=n), self.regular)
                w = self.regular.text_length(s, fontsize=8)
                tw.append((A4_W - MARGIN - w, A4_H - MARGIN + 6), s, font=self.regular, fontsize=8)
                foot = self._fit(self.footer, self.regular)
                tw.append((MARGIN, A4_H - MARGIN + 6), foot[:95], font=self.regular, fontsize=8)
                tw.write_text(page, color=GRAY)
            self.doc.subset_fonts()
            return self.doc.tobytes(garbage=3, deflate=True)

    pdf = ActPdf()
    if act.get("insurer_known"):
        pdf.para(act["insurer"], size=10, bold=True, color=GRAY, gap=2)
    pdf.para(act["title"], size=15, bold=True, gap=4)
    pdf.kv([(r["label"], str(r["value"])) for r in act["header"]], label_w=150)
    pdf.rule()
    label_w, note_w = 140, 160
    for s in act["sections"]:
        pdf.heading(f"{s['n']}. {s['title']}")
        for p in s["paragraphs"]:
            pdf.para(p, size=10)
        if s["rows"]:
            pdf.table([("", label_w, "l"), ("", CONTENT_W - label_w - note_w, "l"), ("", note_w, "l")],
                      [[r["label"], str(r["value"]), r.get("note") or ""] for r in s["rows"]], header=False)
        for p in s.get("source_lines") or []:
            pdf.para(p, size=9, color=GRAY, gap=2)
        for li in s.get("lists") or []:
            tb = li.get("table")
            if tb and tb.get("rows"):
                pdf.ensure(90)               # заголовок таблицы не остаётся внизу страницы без строк
            else:
                pdf.ensure(45)               # заголовок списка — хотя бы с первой строкой
            pdf.para(li["title"], size=10, bold=True, gap=2)
            if tb and tb.get("rows"):
                w = tb.get("widths") or [100 // len(tb["columns"])] * len(tb["columns"])
                cols = [(c, CONTENT_W * x / sum(w), "l") for c, x in zip(tb["columns"], w)]
                pdf.table(cols, [[str(c) for c in r] for r in tb["rows"]], size=8, header=True)
                for p in li.get("notes") or []:
                    pdf.para(p, size=9, gap=2)
                for p in li.get("sources") or []:
                    pdf.para(p, size=8, color=GRAY, gap=2)
                continue
            for it in li["items"]:
                pdf.bullet(it, size=9.5)
    pdf.space(6)
    pdf.ensure(40)
    pdf.rule()
    pdf.para(act["footer"], size=10, bold=True)
    return pdf.finish()
