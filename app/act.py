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
  GET  /act/{id}.docx, .pdf   — выгрузка (первая страница / секция — страховой скоринг объекта)
  GET  /act/{id}/scoring.pdf, /scoring.png — только страница скоринга / картинка шкалы (права как у акта)
  POST /act/{id}/send         — отправить акт файлом в чат с ботом (Telegram), только владельцу
  GET  /act/settings          — пороги лёгкого движка; PUT /act/settings — только администратор
  GET  /act/templates         — шаблоны анализа кратко (справочник class_templates, приложение А): все классы
                                общего страхования 1–18 и варианты 13з и 16у (20 шаблонов); поле variant
  GET  /act/templates/{class} — шаблон класса на языке (?lang=, ?raw=1 — исходный JSON на трёх языках);
                                /history — все версии; PUT — новая версия (администратор, проверка структуры);
                                pending_file_version — новая версия файла ждёт в истории (действует правка)
                                Акт берёт из шаблона ракурсы, оговорки, мероприятия, риски (классы без perils),
                                простое правило сценария (классы без правила в risk_analytics), виды объекта и
                                поля класса (optional.class_fields); у классов 3, 8, 9 выбор прежний
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

Комплексный продукт по частям (30.09.2026, ТЗ универсального шаблона 4.1в): продукт из нескольких классов
(product_classes) или явные optional.parts[] {class_code, product_code?, sum_insured | share_pct, object_value?,
object_kind?, object_description?, same_object?, fields{…}, deductible?} — несколько условных договоров
(Положение 1882, п. 11). Части: сотрудника (сумма частей = страховой сумме ± parts.sum_tolerance, иначе 422) →
перечень объектов договора (contract.items) → доли тарифной политики (настройка parts.shares) → поровну с пометкой
«подтвердите» (suggested_parts). Каждая часть — по шаблону своего класса (_part_calc): уровень, ставка по своему
минимуму (act_engine.part_rate; обязательная часть — только нормативный акт), премия, франшиза, сценарии, аналитика.
Договор: премия — сумма, уровень — самый высокий, сценарии — большее (один объект) или сумма (разные объекты),
удержание — против EML договора, средняя ставка — только справочно. Блок parts в ответе, разделы 1, 3, 4, 5 акта.

Страховой скоринг объекта (01.10.2026, app/act_scoring.py): первая страница акта в PDF и первая секция Word —
балл 0–500 (act_engine.insurance_score: 500 − 5 × балл риска 0–100 аналитики; без аналитики — по уровню риска),
класс A–E с подклассом, шкала, общий обзор, риски, сценарии, проверки. Блок scoring в ответе /act/make и GET /act/{id};
GET /act/{id}/scoring.pdf — только страница скоринга, GET /act/{id}/scoring.png — картинка шкалы (права как у акта).
Отчёт кредитного бюро КАТМ (app/credit_report.py): документ credit_report — файл с текстом правилами, скан моделью;
в /act/photos — блок credit_report, в /act/make — optional.credit_report (источник решает сервер, как у запроса) →
блок borrower и проверки андеррайтеру для классов 14, 13з, 15 (в уровень риска и ставку не входит).

Вилка ставки (01.10.2026): блок rate_fork в /act/make и GET /act/{id}, раздел 4 «Вилка ставки» (Word, PDF), строка
«вилка ставки» в общем обзоре скоринга. Отметки: минимум тарифной политики → ставка акта (прежнее правило) → ставка с
учётом региона (stat.uz / data.egov.uz, risk_stats: отношение региона к республике) и рынка (НАПП, market_picture:
убыточность и рыночная ставка класса) → рыночная ставка; плюс ставки запроса филиала и договора и справочная техническая.
Чистые функции — act_engine (раздел 12), данные из базы — act_analytics.fork_data. Настройка act settings rate_fork:
mode reference (по умолчанию: премия по ставке акта, поправленная — рядом) или apply (поправленная ставка — ставка акта
до франшизы, мероприятий и сверок). Обязательные виды — одна ставка по акту; «по программе» — только рынок; части —
вилка у каждой части, у договора справочная. Все пороги экспертные (calibrated = 0).

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
from . import act_scoring as asc
from . import branch_request as br
from . import class_templates as ctpl
from . import contract_read as cr
from . import credit_report as crr
from . import act_market as am
from . import market_picture as mpic
from . import act_texts as tx
from . import auth, db, guest, i18n, llm
from . import min_rates as mrs
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


def current_custom(con) -> dict:
    """Правки администратора действующей версии act_settings (без значений по умолчанию); нет версии — {}."""
    try:
        r = con.execute("SELECT settings_json FROM act_settings ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(r[0]) if r else {}
    except Exception:
        return {}


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
    """Действующая версия тарифов уровня (без версий правок по одному продукту — минимальной ставки, её id
    в D["min_rate"], и базовой ставки из импорта продуктов)."""
    return mrs.current_version(con, level)


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
    '"contract": null или ' + CONTRACT_SCHEMA + ', "credit_report": null или ' + crr.MODEL_SCHEMA + '}')
# сканы отчёта бюро модели не читаются (credit_report.allow_scan = false): в схеме блока credit_report нет,
# вид credit_report модель только называет — чтобы сервер отбросил всё, что пришло с такого снимка
SCHEMA_HINT_NO_CR = SCHEMA_HINT.replace(', "credit_report": null или ' + crr.MODEL_SCHEMA, "")

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


def model_prompt(n: int, lang: str, credit_scan: bool = False) -> str:
    """credit_scan — модель читает сканы отчёта бюро (настройка credit_report.allow_scan); иначе только называет вид."""
    return (f"приложено файлов: {n}, они пронумерованы по порядку от 1 до {n}. "
            f"для каждого файла укажи ракурс (view). {FIELD_HINTS} "
            f"описания (object_type, location, damages, note, document_kind) пиши на {LANG_NAME[lang]} языке "
            f"строчными буквами; марки, модели, номера и единицы — как написано на объекте. "
            f"видимые повреждения перечисли в damages; если повреждений не видно — пустой список. "
            f"перевод описания объекта из запроса филиала (object_description_translated) — на {LANG_NAME[lang]} "
            f"языке; если запроса филиала нет — branch_request = null. {CONTRACT_HINT} "
            f"{crr.MODEL_HINT if credit_scan else crr.MODEL_HINT_OFF} "
            f"схема ответа: {SCHEMA_HINT if credit_scan else SCHEMA_HINT_NO_CR}")


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
        elif dk and dk.lower() in ("credit_report", "credit report", "отчёт кредитного бюро", "кредитный отчёт"):
            dk = crr.KIND
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
    cbr = crr.from_model(data.get("credit_report"))
    if cbr:
        try:
            kf = int((data.get("credit_report") or {}).get("file"))
            kf = kf if 1 <= kf <= n else None
        except (TypeError, ValueError, AttributeError):
            kf = None
        cbr = {"fields": cbr, "file": kf or next((i for i, k in sorted(kinds.items()) if k == crr.KIND), None)}
    return {"views": views, "document_kinds": kinds, "fields": fields, "damages": damages, "branch_request": brq,
            "contract": ctr, "credit_report": cbr,
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
    scan = bool((limits or {}).get("_credit_scan"))
    return ask_model(saved, lang, limits, "акт: распознавание фото", SYSTEM_PROMPT,
                     lambda n: model_prompt(n, lang, scan), lambda text, n: parse_model(text, n, inclusive))


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
               class_code: str = Form(""), product_code: str = Form(""), kinds: str = Form("")):
    """
    Фото объекта и снимки документов (до 10 файлов, до 15 МБ; JPG, PNG, PDF). Файлы хранятся 24 часа.
    Одним запросом уходят в языковую модель; ответ проверяется по схеме.
    kinds (необязательно) — JSON {номер файла с 1: "credit_report" | "object" | "document"}: вид, который выбрал
    сотрудник. Файл «отчёт бюро» (картинка или скан без текста) при credit_report.allow_scan = false в модель не уходит.
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
    kind_map, kind_err = parse_kinds(kinds, len(files))
    if kind_err:
        return _fail(request, t("ph_kinds_bad", lang), 422, errors={"kinds": kind_err})
    with db.tx() as con:
        ensure_tables(con)
        st = load_settings(con)
    limits = st["limits"]
    inclusive = bool(st["request_check"]["term_inclusive"])
    limits = dict(limits, _contract=st["contract"], _tolerance=float(st["request_check"]["premium_tolerance"]),
                  _credit_scan=bool((st.get("credit_report") or {}).get("allow_scan")), _kinds=kind_map)
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

FILE_KINDS = ("credit_report", "object", "document")


def parse_kinds(raw, n: int) -> tuple:
    """Поле формы kinds (недоверенный ввод) → ({номер файла: вид}, ошибка | None). Пусто — {}."""
    raw = str(raw or "").strip()
    if not raw:
        return {}, None
    if len(raw) > 2000:
        return {}, "слишком длинно"
    try:
        data = json.loads(raw)
    except ValueError:
        return {}, "не JSON"
    if not isinstance(data, dict):
        return {}, "объект {номер файла: вид}"
    out = {}
    for k, v in data.items():
        try:
            i = int(str(k).strip())
        except ValueError:
            return {}, f"номер файла «{str(k)[:10]}» — целое"
        if not 1 <= i <= n:
            return {}, f"номер файла {i} — от 1 до {n}"
        if v not in FILE_KINDS:
            return {}, f"вид файла {i} — " + ", ".join(FILE_KINDS)
        out[i] = v
    return out, None


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
    # сканы и фото, которые сотрудник пометил «отчёт бюро»: в отчёте кредитная история — модели не отдаём,
    # пока администратор не разрешил (credit_report.allow_scan)
    credit_scan = bool(limits.get("_credit_scan"))
    marked = limits.get("_kinds") or {}
    withheld = [] if credit_scan else [f for f in model_files if marked.get(f["index"]) == crr.KIND]
    model_files = [f for f in model_files if f not in withheld]
    if model_files:
        rec = recognize(model_files, lang, limits, inclusive)
    else:
        rec = {"ok": False, "reason": None, "sent": [], "not_sent": []}
    sent = rec.get("sent") or []
    # номер файла в запросе к модели → id загруженного файла
    model_to_id = {k + 1: model_files[i]["id"] for k, i in enumerate(sent)}
    # модель узнала отчёт бюро на снимке, а сканы не разрешены: значения отчёта и всё, что пришло с этого снимка,
    # отбрасываются (картинка уже ушла вместе с остальными — поэтому экран просит пометить такой файл заранее)
    scan_dropped = bool(withheld)
    if not credit_scan:
        cr_files = {k for k, v in (rec.get("document_kinds") or {}).items() if v == crr.KIND}
        if rec.get("credit_report") and (rec["credit_report"].get("file") or 0) > 0:
            cr_files.add(rec["credit_report"]["file"])
        if cr_files or rec.get("credit_report"):
            scan_dropped = True
            rec = dict(rec, credit_report=None,
                       fields=[x for x in rec.get("fields") or [] if x.get("file") not in cr_files],
                       damages=[x for x in rec.get("damages") or [] if x.get("file") not in cr_files])
    views = {model_to_id[k]: v for k, v in (rec.get("views") or {}).items() if k in model_to_id}
    doc_kinds = {model_to_id[k]: v for k, v in (rec.get("document_kinds") or {}).items() if k in model_to_id}
    fields = []
    for f in rec.get("fields") or []:
        fields.append({**f, "file_id": model_to_id.get(f.get("file"))})
    damages = [{"what": d["what"], "where": d.get("where"), "file": model_to_id.get(d.get("file"))}
               for d in rec.get("damages") or []]
    not_sent = [model_files[i]["index"] for i in rec.get("not_sent") or []]
    doc_kinds = {k: (tx.label(tx.DOC_KIND_LABELS, v, lang) if v in (br.KIND, cr.KIND, crr.KIND) else v)
                 for k, v in doc_kinds.items()}
    for f in withheld:
        doc_kinds[f["id"]] = tx.label(tx.DOC_KIND_LABELS, crr.KIND, lang)

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
    # отчёт кредитного бюро (КАТМ): файл с текстом (разобран правилами) или скан (ответ модели); первый найденный
    cbr = None
    for f in saved:
        got = (parsed.get(f["id"]) or {}).get("credit_report")
        if got:
            cbr = {"source": "document", "file": f["id"], "fields": got["fields"], "notes": list(got["notes"])}
            break
    if not cbr and rec.get("credit_report"):
        mk_ = rec["credit_report"]
        fid = model_to_id.get(mk_.get("file")) or (model_to_id.get(1) if len(model_to_id) == 1 else None)
        cbr = {"source": "photo", "file": fid, "fields": mk_["fields"],
               "notes": ["cr_individual"] if mk_["fields"].get("subject_type") == "individual" else []}
    if cbr:
        cbr["fields"], n = cr_clean(cbr["fields"])
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
        # ракурсы — из шаблона класса (справочник class_templates); класса нет — по группе объекта
        need = ctpl.for_group(((ctpl.current(con, cls) or {}).get("template") or {}).get("required_views"), group) \
            if cls else None
        need = ae.required_views(group) if need is None else need
        missing = ae.missing_views(group, seen, need) if rec.get("ok") else list(need)
        stored = {"ai": bool(rec.get("ok")), "reason": rec.get("reason"), "views": views,
                  "document_kinds": doc_kinds, "fields": all_fields, "damages": damages,
                  "object_kind": kind, "class_hint": hint, "condition": rec.get("condition"),
                  "files": len(saved), "photo_files": len(model_files), "parsed_docs": parsed_ok,
                  "prefill": prefill, "doc_notes": doc_notes, "not_sent": not_sent, "lang": lang,
                  "branch_request": brq, "contract": ctr, "credit_report": cbr}
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
                  "cross_differs": (cross or {}).get("differs", 0),
                  # отчёт бюро — только признак и источник: ни названия, ни ИНН, ни сумм
                  "credit_report": bool(cbr), "credit_report_source": (cbr or {}).get("source"),
                  "credit_scan_withheld": len(withheld), "credit_scan_dropped": scan_dropped})
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
    if cbr:
        notes.append(t("cr_found", lang))
        if "cr_individual" in cbr["notes"] and t("cr_individual", lang) not in notes:
            notes.append(t("cr_individual", lang))
    if scan_dropped:
        notes.append(t("cr_scan_off", lang))
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
                   "format": f["fmt"], "kind_marked": marked.get(f["index"]),
                   "credit_scan_withheld": f in withheld} for f in saved],
        "rejected": rejected,
        "recognized": recognized_view(all_fields, lang, group=group),
        "damages": damages,
        "object_kind": ({"code": kind, "label": tx.label({k: v[1] for k, v in tx.OBJECT_KINDS.items()}, kind, lang)}
                        if kind else None),
        "class_hint": hint,
        "suggest_classes": HINT_CLASSES.get(hint or "", []),
        "condition": rec.get("condition"),
        "group": group,
        "required_views": [{"code": v, "label": tx.label(tx.VIEW_LABELS, v, lang)} for v in need],
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
        "credit_report": credit_report_view(cbr, lang),
        "warning": t("warn_pd", lang),
        "expires_in_hours": PHOTO_TTL_SEC // 3600,
    })


# --------------------------------------------------------------------------- #
#  Отчёт кредитного бюро (КАТМ, 01.10.2026): очистка, вид для экрана, ввод, источник
# --------------------------------------------------------------------------- #

def cr_clean(f: Optional[dict]) -> tuple:
    """Поля отчёта бюро без значений, похожих на ПД: наименование и кредиторы — только юрлица (pd_like как у
    стороны договора); у физлица наименования и ИНН нет вовсе. (поля, сколько убрано)."""
    if not f:
        return f, 0
    f = {**f, "overview": dict(f.get("overview") or {}), "active": dict(f.get("active") or {})}
    n = 0
    if f.get("subject_type") != "legal":
        n += int(bool(f.get("name"))) + int(bool(f.get("inn")))
        f["name"], f["inn"] = None, None
    if f.get("name") and pd_like("policyholder", f["name"]):
        f["name"] = None
        n += 1
    keep = []
    for c in f["active"].get("creditors") or []:
        if pd_like("policyholder", c):
            n += 1
            continue
        keep.append(c)
    f["active"]["creditors"] = keep
    return f, n


def _cr_value(code: str, v, lang: str) -> str:
    """Значение поля отчёта бюро словами: суммы — сумами, даты — ДД.ММ.ГГГГ, тип субъекта — словом."""
    if v in (None, "", []):
        return t("na", lang)
    if code == "report_date":
        return _ddmmyyyy(v)
    if code == "subject_type":
        return tx.label(tx.CR_SUBJECT_LABELS, v, lang)
    if code.split(".")[-1] in crr.MONEY_KEYS + crr.ACTIVE_MONEY:
        return money(v, lang)
    if isinstance(v, list):
        return "; ".join(str(x) for x in v)
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def credit_report_view(cbr: Optional[dict], lang: str) -> dict:
    """Блок credit_report ответа /act/photos: detected, source, fields{…}, rows (подпись — значение), notes."""
    if not cbr:
        return {"detected": False, "source": None, "source_label": None, "file": None, "fields": {}, "rows": [],
                "notes": [], "calibrated": ae.CALIBRATED}
    rows = [{"code": k, "label": tx.label(tx.CR_FIELD_LABELS, k, lang), "value": _cr_value(k, v, lang), "raw": v}
            for k, v in crr.flat(cbr["fields"]).items() if v not in (None, "", [])]
    notes = [t(c, lang) for c in cbr.get("notes") or []] + [t("cb_no_direct", lang), t("cb_not_in_rate", lang)]
    return {"detected": True, "source": cbr["source"], "source_label": tx.label(tx.CR_SOURCE_LABELS, cbr["source"], lang),
            "file": cbr.get("file"), "kind_label": tx.label(tx.DOC_KIND_LABELS, crr.KIND, lang),
            "fields": cbr["fields"], "rows": rows, "notes": notes, "calibrated": ae.CALIBRATED}


CR_SOURCES = ("document", "photo", "input", "session")


def validate_credit_report(raw) -> tuple:
    """
    optional.credit_report (недоверенный ввод экрана; поля как в блоке credit_report.fields ответа /act/photos) →
    (чистый отчёт | None, ошибка | None). Поле source не доверяется: источник решает сервер по своей загрузке.
    """
    if raw in (None, "", {}):
        return None, None
    if not isinstance(raw, dict):
        return None, "объект {report_date, subject_type, score, score_class, overview{…}, active{…}}"
    body = raw.get("fields") if isinstance(raw.get("fields"), dict) else raw
    got, err = crr.normalize(body, strict=True)
    if err:
        return None, err
    got, _n = cr_clean(got)
    src = str(raw.get("source") or "input").strip()
    return {"fields": got, "claimed_source": src if src in CR_SOURCES else "input"}, None


def _trust_credit(sent: Optional[dict], block: Optional[dict], upload_missing: bool) -> tuple:
    """
    Отчёт бюро для акта и источник (как _trust_doc у запроса и договора): своя живая загрузка с отчётом есть —
    присланное сравнивается с сохранённым по полям: совпало — источник загрузки (document / photo), отличается —
    input и правка «было → стало». Загрузки нет — всё input, пометка «отчёт недоступен».
    Возвращает (поля | None, {source, source_kind, edits, field_sources, doc_missing}).
    """
    if sent is None:
        if not block or not block.get("fields"):
            return None, None
        fs = {k: block["source"] for k, v in crr.flat(block["fields"]).items() if v not in (None, "", [])}
        return dict(block["fields"]), {"source": block["source"], "source_kind": "document", "edits": [],
                                       "field_sources": fs, "doc_missing": False}
    fields = sent["fields"]
    now = crr.flat(fields)
    if not block or not block.get("fields"):
        fs = {k: "input" for k, v in now.items() if v not in (None, "", [])}
        return fields, {"source": "input", "source_kind": "input", "edits": [], "field_sources": fs,
                        "doc_missing": bool(upload_missing or sent.get("claimed_source") in ("document", "photo",
                                                                                              "session"))}
    was = crr.flat(block["fields"])
    edits, fs = [], {}
    for k in sorted(set(was) | set(now)):
        a, b = was.get(k), now.get(k)
        if _empty_v(a) and _empty_v(b):
            continue
        same = a == b or (isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= 0.005)
        if same:
            fs[k] = block["source"]
            continue
        fs[k] = "input"
        edits.append({"code": k, "was": a, "now": b})
    return fields, {"source": block["source"], "source_kind": "document_edited" if edits else "document",
                    "edits": edits, "field_sources": fs, "doc_missing": False}


def _borrower_block(con_settings: dict, fields: dict, trust: dict, classes: set) -> dict:
    """Блок borrower акта: поля отчёта бюро, источник, возраст отчёта и проверки андеррайтеру (только кредитные
    классы 14, 13з, 15). В уровень риска и ставку не входит — пока заказчик не утвердит правило."""
    credit = any(c in ae.CREDIT_REPORT_CLASSES for c in classes)
    checks = ae.borrower_checks(fields, credit, con_settings.get("credit_report"))
    return {"available": True, "fields": fields, "source": trust["source"], "source_kind": trust["source_kind"],
            "edits": trust["edits"], "field_sources": trust["field_sources"], "doc_missing": trust["doc_missing"],
            "credit_product": credit, "age_days": crr.age_days(fields), "checks": checks,
            "in_risk_level": False, "in_rate": False, "settings": dict(con_settings.get("credit_report") or {}),
            "calibrated": ae.CALIBRATED}


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
        try:
            ctpl.ensure(con)              # класс 18 в справочнике classes (шаблоны 1.2.0)
        except Exception as e:
            print("акт: шаблоны классов не доведены:", type(e).__name__)
        # классов страхования жизни в шаблонах нет: строка L*, если она вдруг есть в classes, не принимается
        if ctpl.is_life_code(ccode) or not db.rows(con, "SELECT code FROM classes WHERE code=?", ccode):
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
    # запрошенная ставка, введённая сотрудником (01.10.2026): если ниже минимальной ставки страховщика — акт отвечает,
    # можно ли застраховать по ней (оценка below_min_assessment); в типе ставки продукта (годовая или на весь срок)
    rr = opt.get("requested_rate_pct")
    if rr not in (None, ""):
        x = _money_in(rr)
        if x is None or not 0 < x <= 100:
            errs["requested_rate_pct"] = "процент больше 0 и не больше 100"
        else:
            o["requested_rate_pct"] = x
    # стоимость, которую клиент заявил до того, как сотрудник заменил её медианой объявлений
    dvo = opt.get("declared_value_original")
    if dvo not in (None, ""):
        v = _money_in(dvo)
        if v is None or v <= 0 or v > MAX_SUM:
            errs["declared_value_original"] = "нужно число больше нуля"
        else:
            o["declared_value_original"] = v
    # шаблон класса (app/class_templates.py): свои виды объекта и поля класса (optional.class_fields)
    tpl_row = ctpl.current(con, ccode) if ccode and "class_code" not in errs else None
    tpl = (tpl_row or {}).get("template")
    kind = opt.get("object_kind")
    if kind not in (None, ""):
        if kind not in tx.OBJECT_KINDS and kind not in ctpl.kind_labels(tpl):
            errs["object_kind"] = "неизвестный вид объекта"
        else:
            o["object_kind"] = kind
    cf, cf_err = validate_class_fields(opt.get("class_fields"), tpl)
    if cf_err:
        errs["class_fields"] = cf_err
    if cf:
        o["class_fields"] = cf
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
    o["deductible"], ded_err = deductible_in(opt.get("deductible"), m.get("sum_insured"))
    if ded_err:
        errs["deductible"] = ded_err

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
    # отчёт кредитного бюро (01.10.2026): поля отчёта для проверок андеррайтеру по кредитным классам
    cbr, cbr_err = validate_credit_report(opt.get("credit_report"))
    if cbr_err:
        errs["credit_report"] = cbr_err
    clean["credit_report"] = cbr

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

    # комплексный продукт по частям (30.09.2026): части сотрудника и переключатель «один объект / разные объекты»
    try:
        clean["same_object"] = _bool_in(opt.get("same_object"))
    except ValueError:
        errs["same_object"] = "да или нет"
    try:
        clean["parts_confirmed"] = _bool_in(opt.get("parts_confirmed"))
    except ValueError:
        errs["parts_confirmed"] = "да или нет"
    clean["parts"] = None
    if opt.get("parts") not in (None, "", []) and not any(k in errs for k in ("product_code", "class_code",
                                                                             "sum_insured")):
        tol = float(load_settings(con)["parts"]["sum_tolerance"])
        plan, p_err = validate_parts(con, opt.get("parts"), m, tol)
        if p_err:
            errs["parts"] = p_err
        clean["parts"] = plan
    return clean, errs


def deductible_in(ded, S: Optional[float]) -> tuple:
    """Франшиза сотрудника {pct | amount, type} → (франшиза | None, ошибка | None). amount — не больше половины S."""
    if ded in (None, "", {}):
        return None, None
    if not isinstance(ded, dict):
        return None, "объект {pct | amount, type}"
    p, a = ded.get("pct"), ded.get("amount")
    ftype = str(ded.get("type") or "unconditional").strip()
    pv = _money_in(p) if p not in (None, "") else None
    av = _money_in(a) if a not in (None, "") else None
    if ftype not in ax.FR_TYPES:
        return None, "type: " + ", ".join(ax.FR_TYPES)
    if (p not in (None, "") and pv is None) or (a not in (None, "") and av is None):
        return None, "pct и amount — числа"
    if pv is None and av is None:
        return None, "нужен pct (% страховой суммы) или amount (сумы)"
    if pv is not None and av is not None:
        return None, "укажите что-то одно: pct или amount"
    if pv is not None and not 0 < pv <= 50:
        return None, "pct больше 0 и не больше 50 % страховой суммы"
    if av is not None and not (0 < av <= (S or MAX_SUM) * 0.5):
        return None, "amount больше 0 и не больше половины страховой суммы"
    return {"pct": pv, "amount": av, "type": ftype}, None


def validate_part_fields(raw, cls: str, tpl: Optional[dict]) -> tuple:
    """
    Признаки части (optional.parts[].fields): те же поля, что у договора, но свои у части — год, состояние, место,
    охрана, убытки за 3 года, документы, тип объекта, конструкция, деятельность, защита, сейсмозона, поля класса
    (по шаблону класса части), просьба снизить премию, преобладающий риск. (поля, ошибка).
    """
    if raw in (None, "", {}):
        return {}, None
    if not isinstance(raw, dict):
        return {}, "fields — объект {признак: значение}"
    out = {}
    this_year = date.today().year
    try:
        for key, lo, hi in (("year", 1950, this_year + 1), ("purchase_year", 1950, this_year)):
            v = _int_in(raw.get(key), lo, hi)
            if v is not None:
                out[key] = v
        z = _int_in(raw.get("seismic_zone"), 5, 10)
        if z is not None:
            out["seismic_zone"] = z
        for key in ("guard", "documents_provided", "want_lower_premium"):
            v = _bool_in(raw.get(key))
            if v is not None:
                out[key] = v
    except ValueError:
        return {}, "fields: год — целое 1950–следующий год, сейсмозона 5–10, да/нет — true/false"
    for key, allowed in (("condition", CONDITIONS), ("location", ae.LOCATIONS), ("construction", ax.CONSTRUCTIONS),
                         ("activity", ax.ACTIVITIES)):
        v = raw.get(key)
        if v not in (None, ""):
            if v not in allowed:
                return {}, f"fields.{key}: одно из " + ", ".join(allowed)
            out[key] = v
    prot = raw.get("protection")
    if prot not in (None, ""):
        codes = ax.PROT_CODES.get(cls)
        if codes and prot not in codes:
            return {}, "fields.protection: одно из " + ", ".join(codes)
        if codes:
            out["protection"] = prot
    losses = raw.get("losses_3y")
    if losses not in (None, ""):
        if not isinstance(losses, dict):
            return {}, "fields.losses_3y — объект {count, small_count, amount}"
        try:
            n, small = _int_in(losses.get("count"), 0, 1000), _int_in(losses.get("small_count"), 0, 1000)
        except ValueError:
            return {}, "fields.losses_3y: количество — целое число от 0 до 1000"
        if n is not None and small is not None and small > n:
            return {}, "fields.losses_3y: мелких убытков не может быть больше, чем всех"
        out["losses_count"], out["small_count"] = n, small
        amt = losses.get("amount")
        if amt not in (None, ""):
            a = _money_in(amt)
            if a is None or a > MAX_SUM:
                return {}, "fields.losses_3y: сумма убытков — число не меньше нуля"
            out["losses_amount"] = a
    price = raw.get("price_new")
    if price not in (None, ""):
        p = _money_in(price)
        if p is None or p <= 0 or p > MAX_SUM:
            return {}, "fields.price_new: число больше нуля"
        out["price_new"] = p
    ot = _s(raw.get("object_type"), 120)
    if ot and not llm.has_pd(ot):
        out["object_type"] = ot
    dom = _s(raw.get("dominant_risk"), 80)
    if dom and not llm.has_pd(dom):
        out["dominant_risk"] = dom
    cf, cf_err = validate_class_fields(raw.get("class_fields"), tpl)
    if cf_err:
        return {}, "fields.class_fields: " + cf_err
    if cf:
        out["class_fields"] = cf
    return out, None


def validate_parts(con, raw, must: dict, tol: float) -> tuple:
    """
    optional.parts (недоверенный ввод) → (части | None, ошибка | None). Часть: {class_code, product_code?,
    sum_insured | share_pct, object_value?, object_kind?, object_description?, same_object?, fields{…}, deductible?}.
    Класс — из справочника; не из состава продукта — можно, с пометкой class_outside. Продукт части (например,
    обязательный вид) — из справочника, класс части должен к нему относиться. Сумма частей = страховой сумме договора
    (допуск tol сумов), доли 0–100. У продукта с одним классом частей не меньше двух.
    """
    if not isinstance(raw, list) or not raw or len(raw) > ae.MAX_PARTS:
        return None, f"parts — список от 1 до {ae.MAX_PARTS} частей {{class_code, sum_insured, …}}"
    classes = list(must.get("product_classes") or [])
    if len(classes) <= 1 and len(raw) < 2:
        return None, "у продукта один класс: одна часть — это обычный акт, уберите parts или добавьте часть"
    S = must.get("sum_insured")
    out = []
    for i, p in enumerate(raw, 1):
        if not isinstance(p, dict):
            return None, f"часть {i}: объект {{class_code, sum_insured, …}}"
        cls = str(p.get("class_code") or "").strip()[:10]
        if not cls:
            return None, f"часть {i}: нужен class_code"
        crow = db.rows(con, "SELECT code, name FROM classes WHERE code=?", cls)
        if not crow:
            return None, f"часть {i}: класс {cls} не найден в справочнике"
        pcode = str(p.get("product_code") or "").strip()[:10] or None
        prod = None
        if pcode and pcode != must.get("product_code"):
            rows = db.rows(con, "SELECT code, name, pricing_mode, rate_text FROM products WHERE code=?", pcode)
            if not rows:
                return None, f"часть {i}: продукт {pcode} не найден в справочнике"
            pcls = [r["class_code"] for r in db.rows(con, "SELECT class_code FROM product_classes "
                                                          "WHERE product_code=? ORDER BY part_no", pcode)]
            if cls not in pcls:
                return None, f"часть {i}: класс {cls} не относится к продукту {pcode} ({', '.join(pcls)})"
            prod = rows[0]
        share = None
        if p.get("share_pct") not in (None, ""):
            share = _money_in(p.get("share_pct"))
            if share is None or not 0 <= share <= 100:
                return None, f"часть {i}: share_pct — доля от 0 до 100"
        s = _money_in(p.get("sum_insured")) if p.get("sum_insured") not in (None, "") else None
        if s is None and share is not None and S:
            s = round(float(S) * share / 100, 2)
        if s is None or s <= 0 or s > MAX_SUM:
            return None, f"часть {i}: sum_insured — число больше нуля (или share_pct)"
        v = None
        if p.get("object_value") not in (None, ""):
            v = _money_in(p.get("object_value"))
            if v is None or v <= 0 or v > MAX_SUM:
                return None, f"часть {i}: object_value — число больше нуля"
        tpl = (ctpl.current(con, cls) or {}).get("template")
        kind = p.get("object_kind")
        if kind not in (None, ""):
            if kind not in tx.OBJECT_KINDS and kind not in ctpl.kind_labels(tpl):
                return None, f"часть {i}: неизвестный вид объекта"
        else:
            kind = None
        desc = _s(p.get("object_description"), 200)
        try:
            same = _bool_in(p.get("same_object"))
        except ValueError:
            return None, f"часть {i}: same_object — да или нет"
        fields, f_err = validate_part_fields(p.get("fields"), cls, tpl)
        if f_err:
            return None, f"часть {i}: {f_err}"
        ded, d_err = deductible_in(p.get("deductible"), s)
        if d_err:
            return None, f"часть {i}: deductible — {d_err}"
        out.append({"class_code": cls, "class_name": crow[0]["name"], "product_code": pcode if prod else None,
                    "product": prod, "class_outside": bool(classes) and cls not in classes and not prod,
                    "sum_insured": s, "share_pct": share, "object_value": v, "object_kind": kind,
                    "object_description": desc if desc and not pd_like("object_type", desc) else None,
                    "same_object": same, "fields": fields, "deductible": ded})
    bad = ae.check_parts_sum(out, S, tol) if S else None
    if bad:
        return None, (f"сумма частей {ae._plain_number(bad['total'])} не равна страховой сумме договора "
                      f"{ae._plain_number(bad['sum_insured'])} (разница {ae._plain_number(abs(bad['diff']))}, "
                      f"допуск {ae._plain_number(tol)} сум)")
    return out, None


def validate_class_fields(raw, tpl: Optional[dict]) -> tuple:
    """
    Поля класса из шаблона (optional.class_fields): {код: значение}. Берутся только поля шаблона с вводом
    optional.class_fields.*; числа — в пределах, текст — до 120 знаков и без ПД (отбрасывается). (поля, ошибка).
    """
    if raw in (None, "", {}):
        return {}, None
    if not isinstance(raw, dict):
        return {}, "объект {код поля: значение}"
    known = ctpl.class_fields(tpl)
    out, bad = {}, []
    for code, v in list(raw.items())[:40]:
        f = known.get(str(code))
        if f is None or v in (None, ""):
            continue
        typ = f.get("type")
        try:
            if typ in ("int", "year"):
                x = _int_in(v, 0, 10_000_000)
                if x is not None:
                    out[code] = x
            elif typ in ("number", "money"):
                x = _money_in(v)
                if x is None or x < 0 or x > MAX_SUM:
                    raise ValueError
                out[code] = x
            elif typ == "bool":
                x = _bool_in(v)
                if x is not None:
                    out[code] = x
            elif typ == "choice":
                if str(v) not in (f.get("options") or []):
                    raise ValueError
                out[code] = str(v)
            else:
                s = _s(v, 120)
                if s and not llm.has_pd(s):
                    out[code] = s
        except ValueError:
            bad.append(str(code))
    return out, ("неверные значения: " + ", ".join(bad)) if bad else None


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
    out["policyholder"] = policyholder_in(rq.get("policyholder"))
    return None


def policyholder_in(v) -> Optional[dict]:
    """
    Страхователь из запроса или договора (для проверки «по кредиту страхователь — банк», правило проекта № 6):
    строка названия или {kind: legal | individual, name} / {is_legal, name}. Название хранится только у юрлица
    (гражданин — kind = individual без имени, как в branch_request.party). Пусто — None.
    """
    if v in (None, "", {}):
        return None
    if isinstance(v, dict):
        kind = v.get("kind")
        if kind == "individual" or v.get("is_legal") is False:
            return {"kind": "individual", "name": None}
        name = _s(v.get("name"), 200)
        if not name:
            return {"kind": "legal", "name": None} if kind == "legal" or v.get("is_legal") is True else None
        v = name
    got = br.party(_s(v, 200))
    if got.get("kind") == "legal" and got.get("name") and pd_like("policyholder", got["name"]):
        got = {"kind": "legal", "name": None}
    return got if got.get("kind") else None


def _policyholder(req: Optional[dict], ct: Optional[dict], upload: dict) -> Optional[dict]:
    """Страхователь договора: договор, потом запрос филиала (введённое или своя загрузка документа)."""
    cands = [("contract", (ct or {}).get("policyholder")), ("request", (req or {}).get("policyholder")),
             ("contract", ((upload.get("contract") or {}).get("fields") or {}).get("policyholder")),
             ("request", ((upload.get("branch_request") or {}).get("fields") or {}).get("policyholder"))]
    for src, p in cands:
        if isinstance(p, dict) and p.get("kind") in ("legal", "individual"):
            return {"kind": p["kind"], "name": p.get("name") if p["kind"] == "legal" else None, "source": src}
    return None


def credit_rule(tpl: Optional[dict], cls: str) -> Optional[float]:
    """Доля кредита по правилу credit шаблона (0,5), если класс кредитный (14, 13з); иначе None."""
    sr = (tpl or {}).get("scenario_rule") or {}
    if sr.get("code") != "credit" and ctpl.base_class(cls) != "14":
        return None
    try:
        share = float((sr.get("params") or {}).get("max_share_of_loan", ctpl.CREDIT_MAX_SHARE))
    except (TypeError, ValueError):
        share = ctpl.CREDIT_MAX_SHARE
    return min(share, ctpl.CREDIT_MAX_SHARE)


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
    # шаблон анализа класса (справочник class_templates, приложение А): ракурсы, оговорки, мероприятия, риски,
    # правило сценария; у классов 3, 8 и 9 шаблон повторяет прежний выбор по группе объекта
    tpl_row = ctpl.current(con, cls)
    tpl = (tpl_row or {}).get("template") or {}
    views_req = ctpl.for_group(tpl.get("required_views"), group)
    if views_req is None:
        views_req = ae.required_views(group)

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
    # минимальная ставка страховщика на дату акта (app/min_rates.py): источник (правка администратора или тарифная
    # политика) и тип ставки продукта (annual | fixed); тот же минимум движок берёт из справочника (db.load_reference)
    min_info = _min_info(con, product, o.get("payer_type"))
    rate_type = (min_info or {}).get("rate_type") or "annual"
    rate_res = ae.rate(ref, product, cls, risk["level"], m["sum_insured"], term, otype, o.get("payer_type"), st,
                       rate_type=rate_type)
    _min_how(rate_res, min_info)
    # класс без продуктов страховщика (16у, 18) — акт по шаблону, ставка не определена: тарифной политики по классу
    # нет, экспертная базовая ставка справочника (если есть) ставкой акта не становится
    if not product and ctpl.products_count(con, cls) == 0:
        rate_res = _no_products_rate(rate_res, cls)
    statutory = rate_res["mode"] in ("statutory", "statutory_undefined")
    multi = bool(clean.get("parts")) or len(m.get("product_classes") or []) > 1
    # факторы объекта по подгруппам класса (02.10.2026, factor_groups шаблона): режим reference — ставка акта прежняя,
    # множитель справочно (отметка вилки); apply — ставка акта = тариф × поправка уровня × множитель, не ниже минимума,
    # до вилки, франшизы и сверок. Комплексный продукт — по шаблону класса каждой части (_part_calc)
    fa = ae.factor_adjust(o.get("class_fields"), tpl, st, o)
    # фон региона к факторам со ссылкой на stat.uz (stat_ref, 02.10.2026): только чтение stat_series, ставку не меняет
    aa.factor_stats(con, fa, region_for_modules(m))
    if not multi:
        ae.factor_effect(fa, rate_res, m["sum_insured"])
    # вилка ставки (01.10.2026): поправки региона (stat.uz) и рынка (НАПП) к ставке акта; в режиме apply ставка с
    # поправками становится ставкой акта до франшизы, мероприятий и сверок — всё дальше считается от неё
    fork_errors = []
    fs = ae.fork_settings(st)
    fork_in, fork_adj = _fork_prepare(con, fs, cls=cls, region=region_for_modules(m), group=group_ra,
                                      product=product, rate_res=_annual_view(rate_res), errors=fork_errors,
                                      min_info=min_info)
    if not multi and rate_res.get("rate_type") != "fixed":
        # у фиксированной ставки (на весь срок) вилка — справочно в годовом выражении, ставку акта не меняет
        ae.apply_fork(rate_res, fork_adj, m["sum_insured"])
    value = ae.value_check(m["sum_insured"], m["object_value"], st, o.get("price_new"), o.get("purchase_year"),
                           group, kind_type or obj_text)
    from .risk_analytics import load_thresholds            # пороги франшизы — только чтение
    th = load_thresholds(con)
    fr = ae.franchise({"small_count": o.get("small_count"), "dominant_risk": o.get("dominant_risk"),
                       "want_lower_premium": o.get("want_lower_premium")},
                      risk["level"], th, statutory, cls, m["sum_insured"])
    fr["thresholds_source"] = {k: v for k, v in (th.get("_source") or {}).items() if k in ("id", "what")}
    clause_codes = ctpl.for_group(tpl.get("clauses"), group)
    clauses = ae.clauses_by_codes(clause_codes, clause_catalog()) if clause_codes is not None \
        else ae.clauses(group, clause_catalog())
    disc = ae.discrepancies(recognized, {"year": o.get("year"), "sum_insured": m["sum_insured"],
                                         "object_value": m["object_value"],
                                         "term_days": None if (term_from_request or term_from_contract)
                                         else o.get("term_days")})

    # сценарии, франшиза и мероприятия — существующими модулями на тех же входных данных, что акт
    block_errors = list(fork_errors)
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
    scen = ax.scenarios(ctx, cls, m["sum_insured"], template=tpl, V=m["object_value"], fields=o.get("class_fields"))
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
                           premium=premium_final, codes=ctpl.for_group(tpl.get("measures"), group_ra))
    except Exception as e:
        block_errors.append({"block": "measures", "error": type(e).__name__})
        meas = {"items": [], "total": {"count": 0}, "error": type(e).__name__}
    if fr.get("status") not in ("error",):
        try:
            fr["alternatives"] = ax.alternatives(ctx, rate_res, m["sum_insured"], meas, th)
        except Exception as e:
            block_errors.append({"block": "alternatives", "error": type(e).__name__})
    # аналитика раздела 4 (30.09.2026): детализация и справочная техническая ставка — тариф акта не меняет
    # риски: классы с рисками в справочнике perils (8, 9) и с правилом в модуле аналитики (3) — как раньше;
    # остальные — экспертные доли шаблона класса вместо одной строки «весь класс»
    tpl_risks = None
    if cls not in ax.RULE_CLASSES and not any(p["class_code"] == cls for p in ref.perils.values()):
        tpl_risks = ctpl.template_risks(tpl, cls) or None
    # комплексный продукт (несколько классов) или явные части: аналитика считается по каждой части (_apply_parts)
    if multi:
        analytics = {"available": False, "reason": "by_parts", "calibrated": ae.CALIBRATED}
    else:
        analytics = _analytics_block(con, ctx, cls, m.get("product_code"), region_ra, m["sum_insured"],
                                     m["object_value"], term, rate_res, scen, meas, risk["level"], statutory, th,
                                     group_ra, tpl_risks, block_errors)
        _napp_settings(analytics, fs, st)
    analytics["activity"] = (ctx.get("must") or {}).get("activity") if ctx.get("ok") else None
    analytics["activity_source"] = (ctx.get("sources") or {}).get("activity")
    # тип объекта, на котором посчитана аналитика, и откуда он (default — принят по умолчанию)
    analytics["object_type"] = (ctx.get("must") or {}).get("object_type") if ctx.get("ok") else None
    analytics["object_type_source"] = (ctx.get("sources") or {}).get("object_type")
    # справка биржи УзРТСБ (02.10.2026): классы 7, 8, 9, 16 — медианы сделок uzex.uz из exchange_quotes (только
    # чтение); стоимость объекта и ставку не меняет. Нет данных — available = false, в акте ничего не пишется
    try:
        exchange = aa.exchange_background(
            con, cls, kind, clean.get("parts"), o.get("class_fields"),
            text=" ".join(str(x) for x in (obj_doc.get("original"), obj_doc.get("translated"), o.get("object_type"))
                          if x))
    except Exception as e:               # справка не собрана — акт всё равно формируется
        block_errors.append({"block": "exchange", "error": type(e).__name__})
        exchange = {"available": False, "reason": "error", "items": []}
    analytics["exchange"] = exchange

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
    missing_v = ae.missing_views(group, views_seen, views_req) if ai_ok else (list(views_req) if photos else [])
    inspection = {"photos": photos, "ai": ai_ok, "ai_reason": upload.get("reason") if photos and not ai_ok else None,
                  "views_seen": views_seen, "required_views": list(views_req),
                  "missing_views": missing_v, "damages": damages, "documents": documents,
                  "document_kinds": sorted(set((upload.get("document_kinds") or {}).values())),
                  "recognized": bool(recognized), "session": clean.get("session"),
                  "session_missing": session_missing, "upload_lang": upload.get("lang"),
                  "parsed_docs": int(upload.get("parsed_docs") or 0)}
    dec = ae.decision(risk, rate_res, value, fr, disc, inspection, missing_key, st)
    share = credit_rule(tpl, cls)
    if share is not None:
        # кредит (правило проекта № 6): сумма ≤ min(кредит − обеспечение; 50 % кредита), страхователь — банк;
        # любая из этих проверок — «принять без оговорок» уже нельзя
        cks = ae.credit_check(m["sum_insured"], o.get("class_fields"), share, _policyholder(req, ct, upload))
        dec["checks"] += [{"code": "c_" + ("tpl_credit_over" if c["code"] == "credit_over" else c["code"]),
                           "params": c["params"]} for c in cks]
        if cks and dec["code"] == "d_accept":
            dec["code"] = "d_accept_with_clauses"
    # урожай (16у): страховая сумма выше стоимости урожая (площадь × урожайность × цена) — ГК ст. 938
    crop_over = [c for c in scen.get("checks") or [] if c.get("code") == "tpl_crop_over"]
    if crop_over:
        dec["checks"].append({"code": "c_tpl_crop_over", "params": dict(crop_over[0]["params"])})
        if dec["code"] == "d_accept":
            dec["code"] = "d_accept_with_clauses"
    if fr.get("applied"):
        # франшиза сотрудника — условие договора: андеррайтер подтверждает, «принять без оговорок» уже нельзя
        dec["checks"].append({"code": "c_fr_applied", "params": {}})
        if dec["code"] == "d_accept":
            dec["code"] = "d_accept_with_clauses"
    market = None
    mchecks = []
    if clean.get("market"):
        market = market_block(con, clean["market"], owner, st, m, o, recognized, kind, cls, group, y)
        mchecks = am.decision_checks(market)
        dec["checks"] += mchecks
        if mchecks and dec["code"] == "d_accept":
            dec["code"] = "d_accept_with_clauses"
    docs = {"req": req, "ct": ct, "rq_trust": rq_trust, "ct_trust": ct_trust, "upload": upload,
            "term_from_request": term_from_request, "term_from_contract": term_from_contract}
    rc, cc, xc, rq_checks, ct_checks = _doc_checks(st, m, docs, rate_res, rate_final, premium_final, value, fr)
    if rq_checks:
        # тариф ниже минимума или расхождение с запросом — «принять без оговорок» уже нельзя
        dec["checks"] += rq_checks
        if dec["code"] == "d_accept":
            dec["code"] = "d_accept_with_clauses"
    if ct_checks:
        # расхождение с договором или нет существенного условия — «принять без оговорок» уже нельзя
        dec["checks"] += ct_checks
        if dec["code"] == "d_accept":
            dec["code"] = "d_accept_with_clauses"
    cls_row = db.rows(con, "SELECT name FROM classes WHERE code=?", cls)
    D = {
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
        "exchange": exchange,
        "object_facts": {"address": ((upload.get("contract") or {}).get("fields") or {}).get("address")},
        # шаблон анализа класса (30.09.2026): версия и то, что акт из него взял; подписи — на языке при выдаче
        "template": _template_block(tpl_row, group, group_ra, views_req, clause_codes, tpl_risks, scen,
                                    o.get("class_fields")),
        # комплексный продукт по частям (30.09.2026): для продукта с одним классом — mode single
        "parts": {"mode": "single", "source": None, "confirmed": True, "items": [], "totals": None, "notes": []},
    }
    if D.get("template") is not None:
        # класс без продуктов страховщика (16у, 18): пометка в шаблоне акта
        n_prod = ctpl.products_count(con, cls)
        D["template"]["products_count"] = n_prod
        D["template"]["no_products_note"] = dict(ctpl.NO_PRODUCTS_NOTE) if n_prod == 0 else None
    if not multi:
        rt_ = rate_res.get("rate_type") or "annual"
        D["rate_fork"] = _fork_finish(fork_in, fork_adj, fs, _annual_view(rate_res), m["sum_insured"],
                                      ae.annual_pct((req or {}).get("tariff_pct"), term, rt_),
                                      ae.annual_pct((ct or {}).get("tariff_pct"), term, rt_), analytics,
                                      premium_final, bool(fr.get("applied")), factors=fa)
        D["factor_adjustment"] = fa
        if rt_ == "fixed":
            D["rate_fork"]["rate_type"] = "fixed"         # отметки — годовой эквивалент фиксированной ставки
    # минимальная ставка страховщика и её источник — в данных акта: старый акт показывает минимум своей даты
    D["min_rate"] = _min_block(min_info, rate_res)
    # оценка заниженной ставки (01.10.2026): запрошенная ставка ниже минимальной — можно ли застраховать
    D["below_min"] = _below_min_block(D, o, req, ct, rate_res, analytics, scen, risk, documents, y, st,
                                      statutory, multi, _net_calibrated(con, cls, analytics))
    _below_min_decision(D)
    if multi:
        C = {"ref": ref, "st": st, "th": th, "m": m, "o": o, "product": product, "recognized": recognized,
             "upload": upload, "kind": kind, "obj_text": " ".join(x for x in (obj_doc.get("original"),
                                                                              obj_doc.get("translated")) if x),
             "class_hint": upload.get("class_hint") or "", "y": y, "location": location,
             "region_ra": region_ra, "term": term, "fs": fs,
             "risk_in": {"inspected": ai_ok, "damages": damages,
                         "condition": o.get("condition") or (upload.get("condition") if ai_ok else None),
                         "year": y, "location": location, "guard": o.get("guard"),
                         "losses_count": o.get("losses_count"), "documents": documents, "today": date.today()},
             "docs": docs, "mchecks": mchecks, "disc": disc, "inspection": inspection, "missing_key": missing_key,
             "block_errors": block_errors}
        _apply_parts(con, clean, D, C)
        # факторы объекта комплексного продукта — по каждой части (шаблон класса части)
        D["factor_adjustment"] = {"by_parts": True, "mode": ae.factor_settings(st)["mode"],
                                  "parts": [dict(p.get("factor_adjustment") or {}, index=p["index"],
                                                 class_code=p["class_code"]) for p in D["parts"]["items"]],
                                  "calibrated": ae.CALIBRATED}
    # страховой скоринг (01.10.2026): цвет полос страницы скоринга — из настроек
    D["scoring_style"] = {"brand_color": (st.get("scoring") or {}).get("brand_color") or asc.DEFAULT_BRAND}
    # отчёт кредитного бюро (КАТМ): проверки андеррайтеру для кредитных классов; уровень и ставку не меняет
    cbf, cb_trust = _trust_credit(clean.get("credit_report"), upload.get("credit_report"), session_missing)
    if cbf:
        classes = {cls} | set(m.get("product_classes") or []) | {p["class_code"] for p in D["parts"]["items"]}
        D["borrower"] = _borrower_block(st, cbf, cb_trust, classes)
        cks = [{"code": "c_" + c["code"], "params": c["params"]} for c in D["borrower"]["checks"]]
        if cks:
            D["decision"]["checks"] += cks
            if D["decision"]["code"] == "d_accept":
                D["decision"]["code"] = "d_accept_with_clauses"
    return D


def _min_info(con, product: Optional[dict], payer_type: Optional[str] = None) -> Optional[dict]:
    """Минимальная ставка страховщика по продукту на дату акта (сегодня) с источником и типом ставки; сбой — None
    (акт формируется как раньше, тип ставки — годовая)."""
    code = (product or {}).get("code")
    if not code:
        return None
    try:
        return mrs.on_date(con, code, date.today(), payer_type)
    except Exception as e:
        print("акт: минимальная ставка страховщика не прочитана:", type(e).__name__)
        return None


def _min_applies(min_info: Optional[dict], rate_res: dict) -> bool:
    """Минимум акта — это минимальная ставка страховщика (а не минимум регулятора выше неё)."""
    return bool(min_info) and rate_res.get("min_pct") is not None and rate_res.get("mode") == "tariff" \
        and abs(float(min_info["pct"]) - float(rate_res["min_pct"])) < 1e-9


def _min_src_row(min_info: dict) -> dict:
    return {k: min_info.get(k) for k in ("source", "effective_from", "note", "document_ref", "name")}


def _min_how(rate_res: dict, min_info: Optional[dict]) -> None:
    """Строка «как посчитано»: откуда минимальная ставка (правка администратора с датой и примечанием или тарифная
    политика) — сразу после сравнения с минимумом."""
    if not _min_applies(min_info, rate_res):
        return
    how = rate_res["how"]
    at = next((i for i, h in enumerate(how) if h["code"] in ("how_min_ok", "how_min_applied")), None)
    item = {"code": "how_min_src", "params": {"min_src": _min_src_row(min_info)}}
    if at is None:
        how.append(item)
    else:
        how.insert(at + 1, item)


def _min_block(min_info: Optional[dict], rate_res: dict) -> dict:
    """Минимальная ставка акта и её источник — в данных акта (старый акт показывает минимум своей даты)."""
    applies = _min_applies(min_info, rate_res)
    out = {"min_pct": rate_res.get("min_pct"), "rate_type": rate_res.get("rate_type") or "annual",
           "insurer": applies, "calibrated": ae.CALIBRATED}
    if min_info:
        out.update(insurer_pct=min_info["pct"], source=min_info["source"], version_id=min_info["version_id"],
                   effective_from=min_info["effective_from"], note=min_info.get("note"),
                   document_ref=min_info.get("document_ref"), name=min_info.get("name"),
                   set_at=min_info.get("created_at"))
    return out


def _annual_view(rate_res: dict) -> dict:
    """Ставка акта в годовом выражении для вилки и рынка: у фиксированной (на весь срок) — × 365 / дни; премия та же."""
    if rate_res.get("rate_type") != "fixed" or rate_res.get("applied_pct") is None:
        return rate_res
    term = int(rate_res.get("term_days") or 365)
    out = dict(rate_res)
    for k in ("applied_pct", "min_pct", "calc_pct", "base_pct"):
        if out.get(k) is not None:
            out[k] = round(ae.annual_pct(out[k], term, "fixed"), 4)
    return out


def _net_calibrated(con, cls: str, analytics: Optional[dict]) -> bool:
    """Калибрована ли базовая нетто-ставка, на которой стоит нетто-ставка расчётного модуля (base_rates.calibrated)."""
    tf = (analytics or {}).get("tariff") or {}
    if not tf.get("available"):
        return False
    try:
        rows = db.rows(con, "SELECT object_type, calibrated FROM base_rates WHERE class_code=?", cls)
    except Exception:
        return False
    if tf.get("base_kind") == "object_type":
        return any(r["object_type"] == tf.get("object_type") and r["calibrated"] for r in rows)
    return bool(rows) and all(r["calibrated"] for r in rows)


def _below_min_block(D: dict, o: dict, req: Optional[dict], ct: Optional[dict], rate_res: dict,
                     analytics: Optional[dict], scen: Optional[dict], risk: dict, documents: bool, y, st: dict,
                     statutory: bool, multi: bool, net_cal: bool) -> dict:
    """Оценка заниженной ставки (act_engine.below_min_assess) по данным акта: запрошенная ставка — введённая
    сотрудником, иначе из договора, иначе из запроса филиала."""
    if o.get("requested_rate_pct") is not None:
        rq, src = o["requested_rate_pct"], "employee"
    elif (ct or {}).get("tariff_pct") is not None:
        rq, src = ct["tariff_pct"], "contract"
    elif (req or {}).get("tariff_pct") is not None:
        rq, src = req["tariff_pct"], "request"
    else:
        rq, src = None, None
    base = {"available": False, "requested_pct": rq, "requested_source": src, "min_pct": rate_res.get("min_pct"),
            "calibrated": ae.CALIBRATED}
    if multi:
        return dict(base, reason="by_parts")
    if rate_res.get("mode") not in ("tariff", "statutory"):
        return dict(base, reason="no_rate")
    an = analytics if (analytics or {}).get("available") else {}
    tf = an.get("tariff") or {}
    net = tf.get("net_pct") if tf.get("available") else None
    sc = scen or {}
    ret = sc.get("retention") or {}
    within = ret.get("within") if ret.get("known") else None
    eml = ((sc.get("items") or {}).get("EML") or {}).get("amount")
    mk = an.get("market") or {}
    mrate = mk.get("rate_pct") if mk.get("available") else None
    lr = mk.get("loss_ratio_pct") if mk.get("available") else None
    fork_mk = ((D.get("rate_fork") or {}).get("adjustments") or {}).get("market") or {}
    if fork_mk.get("loss_ratio_pct") is not None:
        lr = fork_mk["loss_ratio_pct"]            # та же база убыточности, что у поправки рынка вилки
    if mrate is None:
        mrate = ((D.get("rate_fork") or {}).get("market_data") or {}).get("rate_pct")
    new_years = int(st.get("new_object_years", 1))
    obj_new = (date.today().year - int(y) <= new_years) if y else None
    out = ae.below_min_assess(
        requested_pct=rq, requested_source=src, min_pct=rate_res.get("min_pct"),
        rate_type=rate_res.get("rate_type") or "annual", term_days=rate_res.get("term_days") or 365,
        sum_insured=D["must"]["sum_insured"], statutory=statutory, level=risk.get("level"),
        losses_count=o.get("losses_count"), documents=bool(documents), object_new=obj_new, net_pct=net,
        net_calibrated=net_cal, retention_within=within, eml=eml, retention_limit=ret.get("limit"),
        market_rate_pct=mrate, loss_ratio_pct=lr, settings=st)
    out["min_insurer"] = bool((D.get("min_rate") or {}).get("insurer"))
    return out


def _below_min_decision(D: dict) -> None:
    """Пункт «Отступление от минимальной ставки» в проверках андеррайтера; «принять без оговорок» уже нельзя."""
    bm = D.get("below_min") or {}
    if not bm.get("available"):
        return
    D["decision"]["checks"].append({"code": "c_below_min", "params": {"verdict": bm["verdict"],
                                                                      "req": bm["requested_pct"],
                                                                      "min": bm["min_pct"]}})
    if D["decision"]["code"] == "d_accept":
        D["decision"]["code"] = "d_accept_with_clauses"


def _no_products_rate(rate_res: dict, cls: str) -> dict:
    """Класс без продуктов страховщика (16у, 18): ставка и премия не определены — тарифной политики по классу нет;
    экспертная базовая ставка справочника (если есть) в «как посчитана ставка» не выдаётся за ставку акта."""
    out = dict(rate_res, mode="undefined", base_pct=None, base_source=None, adj_pct=None, calc_pct=None,
               applied_pct=None, min_pct=None, min_applied=False, premium=None, engine_chain=[])
    out["how"] = [{"code": "how_no_products", "params": {"cls": cls}}]
    return out


def _fork_prepare(con, fs: dict, *, cls: str, region: str, group: Optional[str], product: Optional[dict],
                  rate_res: dict, errors: list, min_source: Optional[str] = None,
                  min_info: Optional[dict] = None) -> tuple:
    """Данные региона и рынка (только чтение базы) и поправки вилки к ставке акта → (данные, поправки) или
    (None, None) при сбое: акт формируется, вилка — с reason = error."""
    try:
        fin = aa.fork_data(con, cls=cls, region=region, group=group, fs=fs, product_code=(product or {}).get("code"),
                           min_pct=rate_res.get("min_pct"), min_source=min_source,
                           statutory_ref=(product or {}).get("rate_text"), min_info=min_info)
    except Exception as e:
        errors.append({"block": "rate_fork", "error": type(e).__name__})
        return None, None
    return fin, ae.fork_adjust(rate_res, fin["region"], fin["market"], fs)


def _fork_finish(fin: Optional[dict], adj: Optional[dict], fs: dict, rate_res: dict, S: float, request_pct, contract_pct,
                 analytics: Optional[dict], premium_final, fr_applied: bool, factors: Optional[dict] = None) -> dict:
    """Вилка ставки одного класса (act_engine.fork_build) с данными региона и рынка для показа; factors — факторы
    объекта (act_engine.factor_adjust с effect): в режиме reference — отметка «с учётом факторов объекта»."""
    if fin is None:
        return {"available": False, "reason": "error", "mode": fs["mode"], "unit": "% годовых", "marks": [],
                "adjustments": None, "recommended": None, "position": {"request": "none", "contract": "none"},
                "calibrated": ae.CALIBRATED}
    tech = ((analytics or {}).get("engine") or {}).get("technical_pct") if (analytics or {}).get("available") else None
    out = ae.fork_build(rate_res=rate_res, adj=adj, sum_insured=S, market=fin["market"], sources=fin["sources"],
                        request_pct=request_pct, contract_pct=contract_pct, technical_pct=tech,
                        premium_final=premium_final, franchise_applied=fr_applied, mode=fs["mode"],
                        factors=factors)
    mk = fin["market"]
    out["market_data"] = {k: mk.get(k) for k in ("rate_pct", "rate_date", "loss_ratio_pct", "loss_ratio_full_year_pct",
                                                 "rate_full_year_pct", "full_year_period", "row_key", "pack",
                                                 "pack_choice", "class_rows", "source")}
    out["region_data"] = fin["region"]
    if out.get("adjustments"):
        out["adjustments"]["market"] = dict(out["adjustments"]["market"], as_of=mk.get("rate_date"),
                                            source=mk.get("source"), row_key=mk.get("row_key"), pack=mk.get("pack"),
                                            pack_choice=mk.get("pack_choice"),
                                            rate_full_year_pct=mk.get("rate_full_year_pct"),
                                            class_rows=mk.get("class_rows") or [])
    return out


def _napp_settings(analytics: dict, fs: dict, st: Optional[dict]) -> None:
    """Блок napp аналитики: вес claims_freq в поправке региона (строка «Претензии в регионе» — справочно или в
    поправке) и порог малой базы подразделений (настройка napp.branch_min_contracts)."""
    NP = analytics.get("napp") if isinstance(analytics, dict) else None
    if not isinstance(NP, dict):
        return
    NP["claims_weight"] = float(((fs or {}).get("region") or {}).get("weights", {}).get("claims_freq", 1.0))
    NP["branch_min_contracts"] = int((ae.merge_settings(st or {}).get("napp") or {}).get("branch_min_contracts", 200))


def _analytics_block(con, ctx, cls, product_code, region_ra, S, V, term, rate_res, scen, meas, level, statutory, th,
                     group_ra, tpl_risks, block_errors) -> dict:
    """Аналитика раздела 4 (act_analytics.build) для одного класса; сбой — блок с reason = error."""
    try:
        analytics = aa.build(con, ctx, cls=cls, product_code=product_code, region=region_ra, S=S, V=V,
                             term_days=term, rate_res=rate_res, scen=scen, meas=meas, act_level=level,
                             statutory=statutory, th=th, group=group_ra, tpl_risks=tpl_risks)
        for e in analytics.get("errors") or []:
            block_errors.append({"block": "analytics:" + e["block"], "error": e["error"]})
    except Exception as e:
        block_errors.append({"block": "analytics", "error": type(e).__name__})
        analytics = {"available": False, "reason": "error", "calibrated": ae.CALIBRATED}
    return analytics


def _doc_checks(st: dict, m: dict, docs: dict, rate_res: dict, rate_final, premium_final, value: dict,
                fr: dict) -> tuple:
    """Сверка запроса филиала, договора и «запрос ↔ договор» с расчётом акта → (rc, cc, xc, rq_checks, ct_checks)."""
    req, ct, upload = docs["req"], docs["ct"], docs["upload"]
    rf = rate_final if rate_res["mode"] not in ("undefined", "multi") else None
    rc = ae.request_check(req, rate_res=rate_res, rate_final=rf, premium_final=premium_final,
                          sum_insured=m["sum_insured"], object_value=m["object_value"], value=value, fr=fr,
                          term_from_request=docs["term_from_request"], settings=st,
                          rate_type=rate_res.get("rate_type") or "annual")
    _attach_trust(rc, docs["rq_trust"])
    rq_checks = ae.request_checks(rc)
    if (docs["rq_trust"] or {}).get("edits"):
        rq_checks.append({"code": "c_rq_edits", "params": {"n": len(docs["rq_trust"]["edits"])}})
    cc = ae.contract_check(ct, rate_res=rate_res, rate_final=rf, premium_final=premium_final,
                           sum_insured=m["sum_insured"], object_value=m["object_value"], value=value, fr=fr,
                           term_from_contract=docs["term_from_contract"], settings=st,
                           rate_type=rate_res.get("rate_type") or "annual")
    _attach_trust(cc, docs["ct_trust"])
    ct_checks = ae.request_checks(cc, "ct")
    if (docs["ct_trust"] or {}).get("edits"):
        ct_checks.append({"code": "c_ct_edits", "params": {"n": len(docs["ct_trust"]["edits"])}})
    # запрос филиала против договора: объект (кадастр, вид) — присланный, а при живой загрузке — сохранённый
    xc = cr.cross_check(_with_object(req, (upload.get("branch_request") or {}).get("fields")),
                        _with_object(ct, (upload.get("contract") or {}).get("fields")),
                        float(st["request_check"]["premium_tolerance"]))
    x_codes = [i["code"] for i in (xc or {}).get("items") or [] if i["verdict"] == "differs"]
    if x_codes:
        ct_checks.append({"code": "c_x", "params": {"codes": x_codes}})
    return rc, cc, xc, rq_checks, ct_checks


# --------------------------------------------------------------------------- #
#  Комплексный продукт по частям (30.09.2026, ТЗ универсального шаблона 4.1в)
# --------------------------------------------------------------------------- #

def _template_words(con, classes: list) -> dict:
    """Слова видов объекта шаблонов классов (ru/uz/en, основы от 5 букв) — для сопоставления объектов договора."""
    out = {}
    for c in classes:
        tpl = (ctpl.current(con, c) or {}).get("template") or {}
        stems = set()
        for k in (tpl.get("object") or {}).get("kinds") or []:
            for lab in (k.get("label") or {}).values():
                for w in re.split(r"[^\wʻ'-]+", str(lab).lower()):
                    if len(w) >= 5:
                        stems.add(w[:5])
        out[str(c)] = sorted(stems)
    return out


def _parts_plan(con, clean: dict, C: dict) -> dict:
    """
    Какие части и с какими суммами: явные части сотрудника (source = employee) → перечень объектов договора
    (contract; сумма объектов должна сойтись со страховой суммой) → доли тарифной политики (policy_shares,
    настройка parts.shares) → поровну (default). Всё, кроме частей сотрудника, — предложение: confirmed = false.
    """
    m, st = C["m"], C["st"]
    classes = list(m.get("product_classes") or [])
    S = float(m["sum_insured"])
    tol = float(st["parts"]["sum_tolerance"])
    notes = []
    if clean.get("parts"):
        conf = clean.get("parts_confirmed")
        return {"source": "employee", "confirmed": True if conf is None else bool(conf),
                "items": [dict(p) for p in clean["parts"]], "notes": notes}
    ct = C["docs"].get("ct") or {}
    items = [x for x in ct.get("items") or [] if x.get("sum")]
    if items:
        rows = ae.parts_from_items(items, classes, _template_words(con, classes))
        if ae.check_parts_sum(rows, S, tol) is None:
            for r in rows:
                if r["class_guess"]:
                    notes.append({"code": "pt_n_class_guess", "params": {"cls": r["class_code"],
                                                                         "names": ", ".join(r["names"])}})
            return {"source": "contract", "confirmed": False, "notes": notes,
                    "items": [{"class_code": r["class_code"], "sum_insured": r["sum_insured"],
                               "share_pct": round(r["sum_insured"] / S * 100, 4),
                               "object_description": "; ".join(r["names"])[:200] or None,
                               "class_guess": r["class_guess"]} for r in rows]}
        notes.append({"code": "pt_n_items_sum", "params": {"sum": round(sum(float(x["sum"]) for x in items), 2),
                                                           "total": S}})
    shares = ((st["parts"].get("shares") or {}).get(m.get("product_code") or "")) or None
    if shares and all(c in shares for c in classes):
        return {"source": "policy_shares", "confirmed": False, "notes": notes,
                "items": ae.split_sum(S, classes, shares)}
    notes.append({"code": "pt_n_default", "params": {"n": len(classes)}})
    return {"source": "default", "confirmed": False, "notes": notes, "items": ae.split_sum(S, classes)}


def _part_missing(tpl: dict, op: dict, kind, main: bool, recognized: list) -> list:
    """Обязательные поля шаблона класса части, которых нет (коды и подписи ru/uz/en)."""
    have_rec = {r["key"] for r in recognized or [] if r.get("value")} if main else set()
    cf = op.get("class_fields") or {}
    out = []
    for f in tpl.get("must") or []:
        code, inp = f.get("code"), str(f.get("input") or "")
        if inp.startswith("optional.class_fields."):
            ok_ = code in cf
        elif code == "object_kind":
            ok_ = bool(kind)
        elif code in ("brand", "model", "year"):
            ok_ = code in have_rec or (code == "year" and op.get("year") is not None)
        else:
            ok_ = op.get(code) not in (None, "")
        if not ok_:
            out.append({"code": code, "label": dict(f.get("label") or {})})
    return out


def _part_calc(con, P: dict, idx: int, C: dict) -> dict:
    """
    Одна часть договора по шаблону своего класса: уровень риска по своим признакам, ставка по своей тарифной
    политике (обязательный вид — только нормативный акт, без поправок и франшизы), премия на весь срок, сумма к
    стоимости, франшиза, сценарии, мероприятия, аналитика. Те же функции, что у однопродуктового акта.
    """
    ref, st, th, m, o = C["ref"], C["st"], C["th"], C["m"], C["o"]
    errs = C["block_errors"]
    cls = P["class_code"]
    main = bool(P["same_object"])
    product = P.get("product") or C["product"]
    pcode = (product or {}).get("code")
    tpl_row = ctpl.current(con, cls)
    tpl = (tpl_row or {}).get("template") or {}
    f = P.get("fields") or {}
    # признаки части: того же объекта — ввод договора и осмотр; другого объекта — история убытков, документы и
    # пожелания страхователя; поля части — поверх
    if main:
        op = {k: v for k, v in o.items() if k not in ("class_fields", "deductible")}
        if cls == m["class_code"] and o.get("class_fields"):
            op["class_fields"] = dict(o["class_fields"])
    else:
        op = {k: o.get(k) for k in ("losses_count", "small_count", "losses_amount", "documents_provided",
                                    "payer_type", "want_lower_premium", "term_days")}
    op.update({k: v for k, v in f.items() if v is not None})
    if op.get("protection") and op["protection"] not in (ax.PROT_CODES.get(cls) or []):
        op.pop("protection")              # защита другого класса (например, у техники) к этой части не относится
    kind = P.get("object_kind") or (C["kind"] if main else None)
    kind_type = tx.OBJECT_KINDS[kind][0] if kind in tx.OBJECT_KINDS else None
    obj_text = P.get("object_description") or (C["obj_text"] if main else "") or ""
    group = ae.object_group(cls, f"{kind_type or ''} {obj_text} {op.get('object_type') or ''}",
                            C["class_hint"] if main else "")
    otype = ae.match_object_type(ref, cls, kind_type, op.get("object_type"))
    kind_ra = kind or _kind_from_text(f"{obj_text} {op.get('object_type') or ''}")
    otype_ra = otype or ae.match_object_type(ref, cls, (tx.OBJECT_KINDS.get(kind_ra) or (None,))[0], None)
    special = "спецтехник" in str((product or {}).get("name") or "").lower()
    group_ra = "special" if group == "vehicle" and special else group
    y = op.get("year") if op.get("year") is not None else (C["y"] if main else None)
    location = op.get("location") or (C["location"] if main else None)
    S, V = float(P["sum_insured"]), float(P["object_value"])
    term = C["term"]

    risk = ae.risk_level(ae.part_risk_inputs(C["risk_in"], f, main), st)
    cm = ae.class_min(ref, product, cls)
    rate_res = ae.part_rate(ref, product, cls, risk["level"], S, term, otype, op.get("payer_type"), st, cm)
    statutory = rate_res["mode"] in ("statutory", "statutory_undefined")
    # факторы объекта части (02.10.2026): по шаблону класса части, до вилки и франшизы
    fa = ae.factor_effect(aa.factor_stats(con, ae.factor_adjust(op.get("class_fields"), tpl, st, op), C["region_ra"]),
                          rate_res, S)
    # вилка ставки части (01.10.2026): поправки региона и рынка по классу и виду объекта части
    fs = C.get("fs") or ae.fork_settings(st)
    ferrs = []
    fork_in, fork_adj = _fork_prepare(con, fs, cls=cls, region=C["region_ra"], group=group_ra, product=product,
                                      rate_res=rate_res, errors=ferrs, min_source=cm.get("source"))
    errs += [dict(e, block=f"part{idx}:" + e["block"]) for e in ferrs]
    ae.apply_fork(rate_res, fork_adj, S)
    applicable = cls in ae.VALUE_CLASSES
    value = ae.value_check(S, V, st, op.get("price_new"), op.get("purchase_year"), group, kind_type or obj_text)
    value["applicable"] = applicable
    fr = ae.franchise({"small_count": op.get("small_count"), "dominant_risk": op.get("dominant_risk"),
                       "want_lower_premium": op.get("want_lower_premium")}, risk["level"], th, statutory, cls, S)
    ctx = ax.ra_context(con, cls=cls, product_code=pcode, otype=otype_ra, group=group_ra, kind=kind_ra, S=S, V=V,
                        region=C["region_ra"], term_days=o.get("term_days"), year=y, o=op,
                        recognized=C["recognized"] if main else [], text=" ".join(x for x in (
                            obj_text, op.get("object_type")) if x))
    if not ctx.get("ok"):
        errs.append({"block": f"part{idx}:risk_analytics", "error": ctx.get("error")})
    scen = ax.scenarios(ctx, cls, S, template=tpl, V=V, fields=op.get("class_fields"))
    requested = P.get("deductible") or o.get("deductible")
    try:
        fr = ax.franchise(con, ctx, fr, rate_res, cls=cls, S=S, level=risk["level"], statutory=statutory,
                          requested=requested, th=th)
    except Exception as e:
        errs.append({"block": f"part{idx}:franchise", "error": type(e).__name__})
        fr.update(status="error", applied=False, how=[], alternatives=[], error=type(e).__name__)
    fr["requested_from"] = "part" if P.get("deductible") else ("contract" if o.get("deductible") else None)
    premium_final = fr["premium_after"] if fr.get("applied") and fr.get("premium_after") is not None \
        else rate_res["premium"]
    rate_final = fr["rate_after"] if fr.get("applied") and fr.get("rate_after") is not None \
        else rate_res["applied_pct"]
    try:
        meas = ax.measures(con, ctx, rate_res, cls=cls, group=group_ra, kind=kind_ra, S=S, V=V, o=op,
                           location=location, statutory=statutory, th=th, premium=premium_final,
                           codes=ctpl.for_group(tpl.get("measures"), group_ra))
    except Exception as e:
        errs.append({"block": f"part{idx}:measures", "error": type(e).__name__})
        meas = {"items": [], "total": {"count": 0}, "error": type(e).__name__}
    if fr.get("status") != "error":
        try:
            fr["alternatives"] = ax.alternatives(ctx, rate_res, S, meas, th)
        except Exception as e:
            errs.append({"block": f"part{idx}:alternatives", "error": type(e).__name__})
    tpl_risks = None
    if cls not in ax.RULE_CLASSES and not any(p["class_code"] == cls for p in ref.perils.values()):
        tpl_risks = ctpl.template_risks(tpl, cls) or None
    perrs = []
    analytics = _analytics_block(con, ctx, cls, pcode, C["region_ra"], S, V, term, rate_res, scen, meas,
                                 risk["level"], statutory, th, group_ra, tpl_risks, perrs)
    _napp_settings(analytics, fs, st)
    errs += [dict(e, block=f"part{idx}:" + e["block"]) for e in perrs]
    analytics["activity"] = (ctx.get("must") or {}).get("activity") if ctx.get("ok") else None
    analytics["activity_source"] = (ctx.get("sources") or {}).get("activity")
    analytics["object_type"] = (ctx.get("must") or {}).get("object_type") if ctx.get("ok") else None
    analytics["object_type_source"] = (ctx.get("sources") or {}).get("object_type")
    clause_codes = ctpl.for_group(tpl.get("clauses"), group)
    clauses = ae.clauses_by_codes(clause_codes, clause_catalog()) if clause_codes is not None \
        else ae.clauses(group, clause_catalog())
    views_req = ctpl.for_group(tpl.get("required_views"), group)
    if views_req is None:
        views_req = ae.required_views(group)
    # кредитная часть (класс 14, 13з): сумма и страхователь-банк — правило проекта № 6
    share = credit_rule(tpl, cls)
    docs = C.get("docs") or {}
    checks = ae.credit_check(S, op.get("class_fields"), share, _policyholder(docs.get("req"), docs.get("ct"),
                                                                            docs.get("upload") or {})) \
        if share is not None else []
    if kind is None and ctpl.single_kind(tpl):
        # единственный вид объекта класса (кредит, груз …) — по умолчанию; в расчёт не идёт, только в описание части
        kind_default = ctpl.single_kind(tpl)
    else:
        kind_default = None
    crow = db.rows(con, "SELECT name FROM classes WHERE code=?", cls)
    return {
        "index": idx, "class_code": cls, "class_name": crow[0]["name"] if crow else P.get("class_name"),
        "product_code": pcode, "product_name": (product or {}).get("name"), "product_own": bool(P.get("product")),
        "pricing_mode": (product or {}).get("pricing_mode"), "class_outside": bool(P.get("class_outside")),
        "class_guess": bool(P.get("class_guess")),
        "template_version": (tpl_row or {}).get("version"), "template_class": (tpl_row or {}).get("class_code"),
        "sum_insured": S, "share_pct": P.get("share_pct"), "object_value": V,
        "object_value_default": bool(P.get("object_value_default")), "object_kind": kind,
        "object_kind_default": kind_default,
        "object_description": P.get("object_description"), "same_object": main, "group": group,
        "object_type_ref": otype, "term_days": term, "level": risk["level"], "risk": risk, "rate": rate_res,
        "class_min": rate_res.get("class_min") or cm, "statutory": statutory, "value": value, "franchise": fr,
        "premium": premium_final, "premium_before_franchise": rate_res.get("premium"),
        "premium_final": {"amount": premium_final, "rate_pct": rate_final,
                          "franchise_applied": bool(fr.get("applied"))},
        "scenarios": scen, "measures": meas, "analytics": analytics, "clauses": clauses, "checks": checks,
        "missing": _part_missing(tpl, op, kind, main, C["recognized"]),
        "rate_fork": _fork_finish(fork_in, fork_adj, fs, rate_res, S, None, None, analytics, premium_final,
                                  bool(fr.get("applied")), factors=fa),
        "factor_adjustment": fa,
        "optional": {k: v for k, v in op.items() if v is not None},
        "template": _template_block(tpl_row, group, group_ra, views_req, clause_codes, tpl_risks, scen,
                                    op.get("class_fields")),
    }


def _apply_parts(con, clean: dict, D: dict, C: dict) -> None:
    """
    Комплексный продукт: части считаются по отдельности (_part_calc), верхние поля акта (премия, ставка, уровень,
    сценарии, франшиза, сумма к стоимости, решение, сверки с запросом и договором) — итоги договора. Средняя ставка
    договора — только справочно (правило проекта № 5).
    """
    m, o, st = C["m"], C["o"], C["st"]
    S, V = float(m["sum_insured"]), float(m["object_value"])
    plan = _parts_plan(con, clean, C)
    classes = list(m.get("product_classes") or [])
    all_cls = [p["class_code"] for p in plan["items"]]
    notes = list(plan["notes"])
    same_default = ae.default_same_object(all_cls)
    items = []
    v_default = False
    # стоимость объекта договора делится между частями со страховой стоимостью (у ответственности, НС, кредита её нет)
    s_val = sum(float(p["sum_insured"]) for p in plan["items"] if p["class_code"] in ae.VALUE_CLASSES)
    for i, p in enumerate(plan["items"], 1):
        P = dict(p)
        P.setdefault("fields", {})
        if i == 1:
            P["same_object"] = True           # часть 1 — объект договора (осмотр, документы)
        elif P.get("same_object") is None:
            P["same_object"] = clean.get("same_object") if clean.get("same_object") is not None else same_default
        if P.get("object_value") is None:
            # стоимость части не введена: доля стоимости договора по доле суммы (у частей без страховой стоимости —
            # сама сумма части, сумма к стоимости у них не проверяется)
            if P["class_code"] in ae.VALUE_CLASSES:
                P["object_value"] = round(V * float(P["sum_insured"]) / s_val, 2) if s_val else \
                    float(P["sum_insured"])
                P["object_value_default"] = True
                v_default = True
            else:
                P["object_value"] = float(P["sum_insured"])
        if P.get("share_pct") is None:
            P["share_pct"] = round(float(P["sum_insured"]) / S * 100, 4) if S else None
        if P.get("class_outside"):
            notes.append({"code": "pt_n_outside", "params": {"n": i, "cls": P["class_code"],
                                                             "classes": ", ".join(classes)}})
        items.append(_part_calc(con, P, i, C))
    if v_default:
        notes.append({"code": "pt_n_value_default", "params": {}})
    if not plan["confirmed"]:
        notes.append({"code": "pt_n_confirm", "params": {}})
    totals = ae.contract_totals(items, S)
    agg = ae.aggregate_scenarios([{"index": p["index"], "class_code": p["class_code"], "main": p["same_object"],
                                   "scenarios": p["scenarios"]} for p in items])
    eml = (agg["items"].get("EML") or {}).get("amount") if agg["available"] else None
    mfl = (agg["items"].get("MFL") or {}).get("amount") if agg["available"] else None
    retention = ae.contract_retention([(p["scenarios"] or {}).get("retention") for p in items
                                       if (p["scenarios"] or {}).get("available")], eml or 0, mfl) \
        if agg["available"] else None
    same_all = all(p["same_object"] for p in items)
    diff_all = all(not p["same_object"] for p in items[1:])
    object_mode = "one" if same_all else ("different" if diff_all else "mixed")
    worst = max(items, key=lambda p: ae.LEVEL_ORDER.get(p["level"], 1))
    D["parts"] = {"mode": "multi", "source": plan["source"], "confirmed": bool(plan["confirmed"]),
                  "object_mode": object_mode, "same_object_default": same_default, "items": items,
                  "totals": dict(totals, scenarios=agg, retention=retention, worst_index=worst["index"],
                                 value=ae.contract_value([{"index": p["index"], "sum_insured": p["sum_insured"],
                                                           "object_value": p["object_value"],
                                                           "value_applicable": p["value"]["applicable"]}
                                                          for p in items], st)),
                  "notes": notes, "calibrated": ae.CALIBRATED,
                  "suggested": [{"class_code": p["class_code"], "product_code": p["product_code"]
                                 if p["product_own"] else None, "sum_insured": p["sum_insured"],
                                 "share_pct": p["share_pct"], "object_value": p["object_value"],
                                 "same_object": p["same_object"], "object_description": p["object_description"],
                                 "class_guess": p["class_guess"]} for p in items]}
    D["multi_class"] = True
    # верхние поля — итоги договора
    D["risk"] = dict(worst["risk"], contract=True, part_index=worst["index"])
    fr_any = [p for p in items if p["franchise"].get("applied")]
    D["premium_final"] = {"amount": totals["premium"], "rate_pct": None, "franchise_applied": bool(fr_any)}
    before = [p["premium_before_franchise"] for p in items]
    D["rate"] = {"mode": "multi", "base_pct": None, "base_source": "parts", "adj_pct": None, "calc_pct": None,
                 "applied_pct": None, "min_pct": None, "min_applied": False,
                 "premium": round(sum(before)) if all(x is not None for x in before) else None,
                 "term_days": C["term"], "object_type": None, "class_code": m["class_code"],
                 "product_code": m.get("product_code"), "pricing_mode": (C["product"] or {}).get("pricing_mode"),
                 "calibrated": ae.CALIBRATED, "engine_chain": [],
                 "reference_pct": totals["reference_rate_pct"], "reference_only": True,
                 "how": [{"code": "how_multi", "params": {"n": len(items)}}]}
    statuses = [p["franchise"].get("status") for p in items]
    D["franchise"] = {"needed": any(p["franchise"].get("needed") for p in items), "code": "fr_parts",
                      "status": "applied" if fr_any else ("proposed" if "proposed" in statuses else "none"),
                      "grounds": [], "size": None, "size_pct": None, "applied": bool(fr_any), "how": [],
                      "alternatives": [], "by_parts": True}
    sc_items = {}
    if agg["available"]:
        for s in ae.SCENARIOS3:
            a = agg["items"][s]["amount"]
            sc_items[s] = {"amount": a, "pct": round(a / S * 100, 1) if S else None, "what": "sc_w_parts_" + agg["rule"],
                           "what_params": {}, "state": None, "formula": None, "level": None, "source_scenario": s}
    D["scenarios"] = {"available": agg["available"], "reason": None if agg["available"] else "pt_sc_na",
                      "class_code": None, "rule": "parts", "items": sc_items, "retention": retention,
                      "assumptions": [], "calibrated": ae.CALIBRATED, "order": ax.SCENARIO_ORDER, "source": "parts",
                      "aggregate": agg, "order_ok": agg.get("order_ok")}
    cv = D["parts"]["totals"]["value"]
    if cv:
        D["value"] = dict(D["value"], **{k: cv[k] for k in ("ratio_pct", "verdict", "legal_ref", "diff")},
                          contract=True)
    # оговорки и мероприятия — все части (без повторов), с номером части
    seen, cl = set(), []
    for p in items:
        for c in p["clauses"]:
            if c["code"] not in seen:
                seen.add(c["code"])
                cl.append(c)
    D["clauses"] = cl
    ms, mseen = [], set()
    for p in items:
        for it in (p["measures"] or {}).get("items") or []:
            if it.get("code") not in mseen:
                mseen.add(it.get("code"))
                ms.append(dict(it, part_index=p["index"]))
    D["measures"] = {"items": ms, "total": {"count": len(ms), "by_parts": True}}
    # решение: осмотр и документы — как у договора, остальное — по частям; признак, введённый в части 1 (объект
    # договора), не считается недостающим
    own = items[0]["optional"]
    D["missing"] = [k for k in D["missing"] if own.get(k) in (None, "")]
    missing_key = [k for k in C["missing_key"] if own.get(k) in (None, "")]
    dec = ae.decision(D["risk"], {"mode": "tariff"}, D["value"], {"needed": False}, C["disc"], C["inspection"],
                      missing_key, st)
    chk = dec["checks"]
    if not plan["confirmed"]:
        chk.append({"code": "c_parts_confirm", "params": {"source": plan["source"]}})
    for p in items:
        n, c_ = p["index"], p["class_code"]
        base = {"n": n, "cls": c_}
        if p["rate"]["mode"] in ("undefined", "statutory_undefined"):
            chk.append({"code": "c_part_rate_undefined", "params": base})
        if p["statutory"]:
            chk.append({"code": "c_part_statutory", "params": base})
        if p["value"]["applicable"] and p["value"]["verdict"] in ("under", "over"):
            chk.append({"code": "c_part_" + p["value"]["verdict"], "params": base})
        if p["franchise"].get("needed"):
            chk.append({"code": "c_part_franchise", "params": base})
        if p["franchise"].get("applied"):
            chk.append({"code": "c_part_fr_applied", "params": base})
        if p["class_outside"] or p["class_guess"]:
            chk.append({"code": "c_part_class_check", "params": base})
        for c in p["checks"]:
            chk.append({"code": "c_part_" + c["code"], "params": dict(c["params"], **base)})
        if n > 1 and p.get("missing"):
            # обязательные поля шаблона класса у частей 2 и далее (у части 1 — в «чего не хватает» акта)
            chk.append({"code": "c_part_missing", "params": dict(base, labels=[dict(x["label"]) for x in p["missing"]],
                                                                  codes=[x["code"] for x in p["missing"]])})
    chk += C["mchecks"]
    fr_c = {"status": D["franchise"]["status"], "needed": D["franchise"]["needed"], "size_pct": None}
    rc, cc, xc, rq_checks, ct_checks = _doc_checks(st, m, C["docs"], D["rate"], None, totals["premium"],
                                                   D["value"], fr_c)
    chk += rq_checks + ct_checks
    if dec["code"] == "d_accept" and [c for c in chk if c["code"] != "c_confirm"]:
        dec["code"] = "d_accept_with_clauses"
    D["decision"] = dec
    D["request_check"], D["contract_check"], D["cross_check"] = rc, cc, xc
    # вилка ставки договора — справочно (сумма премий частей по каждой отметке); у частей — своя вилка
    D["rate_fork"] = ae.fork_contract(items, S, C["term"], (C["docs"].get("req") or {}).get("tariff_pct"),
                                      (C["docs"].get("ct") or {}).get("tariff_pct"), C["fs"]["mode"])


def _template_block(row: Optional[dict], group: str, group_ra: str, views_req: list, clause_codes: Optional[list],
                    tpl_risks: Optional[list], scen: dict, fields: Optional[dict]) -> Optional[dict]:
    """Что акт взял из шаблона класса — сохраняется в акте (подписи ru/uz/en, язык выбирается при выдаче)."""
    if not row:
        return None
    tpl = row["template"]
    sr = tpl.get("scenario_rule") or {}
    return {"class_code": row["class_code"], "requested_class": row.get("requested_class"),
            "alias_of": row.get("alias_of"), "version": row["version"], "source": row["source"],
            "variant": bool(tpl.get("variant")), "variant_of": tpl.get("variant_of"),
            "official_name": tpl.get("official_name"),
            "name": tpl.get("name"), "object": tpl.get("object"),
            "must": tpl.get("must") or [], "optional": tpl.get("optional") or [],
            "valuation_methods": tpl.get("valuation_methods") or [],
            "required_views": list(views_req or []), "clauses": list(clause_codes or []),
            "measures": ctpl.for_group(tpl.get("measures"), group_ra) or [],
            "risks_source": "template" if tpl_risks else ((tpl.get("risks") or {}).get("source")),
            "risks_used": bool(tpl_risks), "risks": tpl.get("risks"),
            "factors": tpl.get("factors") or [],
            "scenario_rule": {"code": sr.get("code"), "engine": sr.get("engine"), "text": sr.get("text"),
                              "simple_rule": sr.get("simple_rule"), "used": scen.get("source") == "template",
                              "params": sr.get("params") or {}},
            "documents": tpl.get("documents") or {}, "stats": tpl.get("stats") or [],
            "notes": tpl.get("notes") or [], "reinsurance_usually": bool(tpl.get("reinsurance_usually")),
            "class_fields": dict(fields or {}), "group": group, "calibrated": ae.CALIBRATED}


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
    if key == "min_src":                    # источник минимальной ставки (app/min_rates.py) на языке акта
        return mrs.source_label(v or {}, lang) or t("na", lang)
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
    if key == "spct":                       # поправка со знаком: +15 % / −3,2 % (вилка ставки)
        return _spct(v, lang, 2)
    if key == "fmult":                      # множитель факторов объекта: 1,6905
        return _mult(v, lang)
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
    if c["code"] in ("c_tpl_credit_over", "c_part_credit_over", "c_credit_need_data", "c_part_credit_need_data"):
        return t(c["code"], lang, n=p.get("n"), cls=p.get("cls"), share=p.get("share_pct", 50),
                 **{k: money(p.get(k), lang) for k in ("sum", "insurable", "excess", "credit", "collateral")})
    if c["code"] == "c_below_min":
        return t("c_below_min", lang, req=pct(p.get("req"), lang), min=pct(p.get("min"), lang),
                 verdict=t("bm_v_" + str(p.get("verdict")), lang))
    if c["code"] == "c_tpl_crop_over":
        return t(c["code"], lang, **{k: money(p.get(k), lang) for k in ("sum", "value")})
    if c["code"] in ("c_credit_holder_not_bank", "c_part_credit_holder_not_bank"):
        who = p.get("holder") or t("credit_holder_individual" if p.get("individual") else "credit_holder_noname",
                                   lang)
        return t(c["code"], lang, n=p.get("n"), cls=p.get("cls"), holder=who,
                 where=t("credit_src_" + str(p.get("source") or "contract"), lang))
    if c["code"] == "c_borrower_overdue":
        return t(c["code"], lang, amount=money(p.get("amount"), lang))
    if c["code"] == "c_borrower_stale":
        if p.get("days") is None:
            return t("c_borrower_stale_nodate", lang)
        return t(c["code"], lang, days=p["days"], max=p.get("max"))
    if c["code"] == "c_borrower_low_class":
        return t(c["code"], lang, cls=p.get("cls"), low=p.get("low"))
    if c["code"] == "c_part_missing":
        what = ", ".join(_lower_first(x.get(lang) or x.get("ru") or "") for x in p.get("labels") or []) \
            or ", ".join(p.get("codes") or [])
        return t(c["code"], lang, n=p.get("n"), cls=p.get("cls"), what=what)
    if c["code"] == "c_parts_confirm":
        return t(c["code"], lang, source=t("pt_src_" + str(p.get("source") or "default"), lang))
    if c["code"].startswith("c_part_"):
        return t(c["code"], lang, n=p.get("n"), cls=p.get("cls"))
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
    tkinds = ctpl.kind_labels((D.get("template") or {}).get("object") and {"object": D["template"]["object"]})
    if kind == "warehouse" and ax.cold_store(" ".join(x for x in (doc.get("original"), doc.get("translated")) if x)):
        base = tx.label(tx.OBJECT_SUBKINDS, "cold_store", lang)
    elif kind not in tx.OBJECT_KINDS and kind in tkinds:
        base = tkinds[kind].get(lang) or tkinds[kind].get("ru")      # вид объекта из шаблона класса
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


def _part_label(p: dict, lang: str) -> str:
    """«Часть 2 — класс 14 Кредиты»."""
    return t("pt_part", lang, n=p["index"], cls=_class_label(p["class_code"], p.get("class_name"), lang))


def _value_text(value: dict, lang: str) -> str:
    """Вывод «сумма к стоимости» (раздел 3) для одного значения value_check."""
    ratio = pct(value["ratio_pct"], lang, 2)
    legal = tx.label(tx.LEGAL_REFS, value["legal_ref"], lang) if value.get("legal_ref") else None
    if value["verdict"] == "over":
        return t("v_over", lang, diff=money(value["diff"], lang), ref=legal)
    if value["verdict"] == "under":
        return t("v_under", lang, ratio=ratio, ref=legal)
    if value.get("legal_ref"):
        return t("v_normal", lang, ratio=ratio) + " " + t("v_under_small", lang, ratio=ratio, ref=legal)
    return t("v_normal", lang, ratio=ratio)


def _fr_short(fr: dict, lang: str) -> str:
    """Франшиза части одной строкой для таблицы частей."""
    st_ = fr.get("status")
    if st_ == "statutory":
        return t("pt_fr_statutory", lang)
    if st_ == "applied":
        return t("pt_fr_applied", lang, pct=pct(fr.get("size_pct"), lang))
    if st_ == "proposed":
        return t("pt_fr_proposed", lang, pct=pct(fr["size_pct"], lang)) if fr.get("size_pct") \
            else t("pt_fr_proposed_nosize", lang)
    return t("pt_fr_none", lang)


def _part_rate_text(r: dict, lang: str) -> str:
    if r["mode"] in ("tariff", "statutory") and r.get("applied_pct") is not None:
        return pct(r["applied_pct"], lang)
    return t("rate_undefined", lang)


def _parts_sc_how(sc: dict, lang: str) -> list:
    """Как сложены сценарии договора: один объект — большее из частей, разные объекты — сумма."""
    agg = sc.get("aggregate") or {}
    out = [t("pt_sc_rule_" + str(agg.get("rule") or "max"), lang)]
    for s in ("PML", "EML", "MFL"):
        it = (agg.get("items") or {}).get(s) or {}
        mv = it.get("main_values") or []
        terms = []
        if len(mv) > 1:
            terms.append(t("pt_sc_max", lang, vals="; ".join(
                t("pt_part_short", lang, n=x["index"]) + " " + money(x["amount"], lang) for x in mv)))
        elif mv:
            terms.append(t("pt_part_short", lang, n=mv[0]["index"]) + " " + money(mv[0]["amount"], lang))
        terms += [t("pt_part_short", lang, n=x["index"]) + " " + money(x["amount"], lang) for x in it.get("added") or []]
        out.append(t("pt_sc_line", lang, s=s, expr=" + ".join(terms), total=money(it.get("amount"), lang)))
    for x in agg.get("excluded") or []:
        out.append(t("pt_sc_excluded", lang, n=x["index"], cls=x["class_code"]))
    out.append(t("pt_sc_ret", lang))
    return out


def _parts_view(D: dict, lang: str) -> dict:
    """
    Комплексный продукт (30.09.2026): строки разделов 1, 3, 4, абзацы, списки и JSON блока parts на языке акта.
    Каждая часть — своим шаблоном класса; аналитика, сценарии и франшиза — те же показы, что у одного класса.
    """
    PT = D["parts"]
    must = D["must"]
    NA = t("na", lang)
    items = PT["items"]
    totals = PT["totals"] or {}
    s1_rows, s3_rows, table, lists, pjs, missing_lines = [], [], [], [], [], []
    for p in items:
        lab = _part_label(p, lang)
        n = p["index"]
        kl = tx.OBJECT_KINDS.get(p.get("object_kind") or "")
        obj = p.get("object_description") or ((kl[1].get(lang) or kl[1].get("ru")) if kl else None)
        where = t("pt_same_object" if p["same_object"] else "pt_other_object", lang)
        s1_rows.append(_row(lab, t("pt_s1_value", lang, sum=money(p["sum_insured"], lang),
                                   share=pct(p["share_pct"], lang, 2) if p.get("share_pct") is not None else NA),
                            "; ".join(x for x in (obj, where, t("pt_outside", lang) if p["class_outside"] else None,
                                                  t("pt_guess", lang) if p["class_guess"] else None) if x)))
        v = p["value"]
        if v.get("applicable"):
            vtext = _value_text(v, lang)
            if p.get("object_value_default"):
                vtext += " " + t("pt_value_default", lang)
            s3_rows.append(_row(lab, pct(v["ratio_pct"], lang, 2),
                                t("pt_s3_note", lang, sum=money(p["sum_insured"], lang),
                                  value=money(p["object_value"], lang)) + ". " + vtext))
        else:
            vtext = t("pt_value_na", lang)
            s3_rows.append(_row(lab, t("pt_value_na_short", lang), vtext))
        r = p["rate"]
        lvl = tx.label(tx.LEVEL_LABELS, p["level"], lang)
        prem = p["premium_final"]["amount"]
        table.append([str(n), _class_label(p["class_code"], p.get("class_name"), lang),
                      money(p["sum_insured"], lang), lvl, _part_rate_text(r, lang),
                      money(prem, lang) if prem is not None else NA, _fr_short(p["franchise"], lang)])
        # подраздел части: уровень, как посчитан тариф, франшиза, сценарии, аналитика
        Dp = {"must": {"class_code": p["class_code"], "class_name": p.get("class_name"),
                       "product_code": p.get("product_code"), "product_name": p.get("product_name"),
                       "sum_insured": p["sum_insured"], "object_value": p["object_value"],
                       "region": must.get("region"), "region_code": must.get("region_code")},
              "rate": r, "risk": p["risk"], "franchise": p["franchise"], "analytics": p.get("analytics"),
              "measures": p.get("measures"), "template": p.get("template"), "contract": D.get("contract")}
        rrule = p["risk"]["rule"]
        minus = lambda x: str(x).replace("-", "−")
        lists.append({"title": t("pt_sub_level", lang, part=lab, level=lvl),
                      "items": [_text(f, lang) for f in p["risk"]["factors"]] +
                      [t("level_rule", lang, net=minus(p["risk"]["net"]), low=minus(rrule["low_max_net"]),
                         high=minus(rrule["high_min_net"]), k=rrule["min_known"])]})
        how = [_text(h, lang) for h in r["how"]]
        if r["mode"] == "statutory":
            how.append(t("pt_statutory_note", lang))
        lists.append({"title": t("pt_sub_rate", lang, part=lab), "items": how})
        fr = p["franchise"]
        fr_text = _fr_text(fr, lang)
        fr_items = [fr_text] + [_text(g, lang) for g in fr.get("grounds") or []] + \
            [_fr_how_text(h, lang) for h in fr.get("how") or []]
        fr_alts = [_alt_view(a, lang) for a in fr.get("alternatives") or []]
        lists.append({"title": t("pt_sub_fr", lang, part=lab), "items": fr_items + [a["text"] for a in fr_alts]})
        scv = _scenarios_view(p["scenarios"], Dp["must"], lang)
        sc_items = [f"{x['label']}: {x['value']}" + (f" ({x['note']})" if x.get("note") else "") for x in scv["rows"]]
        lists.append({"title": t("pt_sub_sc", lang, part=lab), "items": sc_items + scv["json"]["how"]})
        try:
            anv = _analytics_view(Dp, lang)
        except Exception as e:               # сбой показа аналитики части не роняет акт
            print("акт: аналитика части не показана:", type(e).__name__, e)
            anv = {"summary": None, "lists": [], "json": {"available": False, "reason": "render_error",
                                                          "calibrated": ae.CALIBRATED}}
        if anv.get("summary"):
            lists.append({"title": t("pt_sub_summary", lang, part=lab), "items": [anv["summary"]]})
        for li in anv["lists"]:
            lists.append(dict(li, title=f"{lab}. {li['title']}"))
        if p.get("missing"):
            missing_lines.append(lab + ": " + ", ".join((x["label"].get(lang) or x["label"].get("ru") or x["code"])
                                                        for x in p["missing"]))
        msv = _measures_view(p.get("measures"), lang)
        pjs.append({
            "index": n, "class_code": p["class_code"],
            "class_name": _class_label(p["class_code"], p.get("class_name"), lang), "label": lab,
            "product_code": p.get("product_code"), "product_own": p.get("product_own"),
            "pricing_mode": p.get("pricing_mode"), "template_version": p.get("template_version"),
            "template_class": p.get("template_class"), "class_outside": p["class_outside"],
            "class_guess": p["class_guess"], "sum_insured": p["sum_insured"], "share_pct": p.get("share_pct"),
            "object_value": p["object_value"], "object_value_default": p.get("object_value_default"),
            "object_kind": p.get("object_kind"), "object_kind_default": p.get("object_kind_default"),
            "object_description": p.get("object_description"),
            "same_object": p["same_object"], "level": p["level"], "level_label": lvl,
            "risk": {"level": p["level"], "net": p["risk"]["net"], "up": p["risk"]["up"],
                     "down": p["risk"]["down"], "factors": [{"code": f["code"], "sign": f["sign"],
                                                             "text": _text(f, lang)} for f in p["risk"]["factors"]],
                     "calibrated": ae.CALIBRATED},
            "rate": {"mode": r["mode"], "base_pct": r.get("base_pct"), "base_source": r.get("base_source"),
                     "adj_pct": r.get("adj_pct"), "calc_pct": r.get("calc_pct"), "applied_pct": r.get("applied_pct"),
                     "min_pct": r.get("min_pct"), "min_applied": r.get("min_applied"),
                     "min_source": (p.get("class_min") or {}).get("source"),
                     "final_pct": p["premium_final"]["rate_pct"], "statutory": p["statutory"],
                     "how": how, "calibrated": ae.CALIBRATED},
            "premium": prem, "premium_text": money(prem, lang) if prem is not None else NA,
            "premium_before_franchise": p.get("premium_before_franchise"), "term_days": p.get("term_days"),
            "franchise": {"needed": bool(fr.get("needed")), "text": fr_text, "short": _fr_short(fr, lang),
                          "requested_from": fr.get("requested_from"),
                          "grounds": [{"code": g["code"], "text": _text(g, lang)} for g in fr.get("grounds") or []],
                          **({"size": fr["size"]} if fr.get("size") else {}),
                          **_franchise_extra(fr, [_fr_how_text(h, lang) for h in fr.get("how") or []], fr_alts,
                                             lang)},
            "scenarios": scv["json"],
            "value": {"applicable": bool(v.get("applicable")), "ratio_pct": v["ratio_pct"],
                      "verdict": v["verdict"] if v.get("applicable") else "na", "text": vtext,
                      "legal_ref": v.get("legal_ref") if v.get("applicable") else None,
                      "depreciated": v.get("depreciated")},
            "analytics": anv["json"],
            "clauses": [{"code": c["code"], "text": c.get(lang) or c.get("ru"), "expert": True,
                         "calibrated": ae.CALIBRATED} for c in p.get("clauses") or []],
            "measures": [x["json"] for x in msv["items"]],
            "missing": [{"code": x["code"], "label": x["label"].get(lang) or x["label"].get("ru")}
                        for x in p.get("missing") or []],
        })
    cols = [t("pt_col_n", lang), t("pt_col_class", lang), t("pt_col_sum", lang), t("pt_col_level", lang),
            t("pt_col_rate", lang), t("pt_col_premium", lang), t("pt_col_fr", lang)]
    total_prem = totals.get("premium")
    table.append(["", t("pt_total", lang), money(totals.get("sum_insured"), lang),
                  tx.label(tx.LEVEL_LABELS, totals.get("level"), lang), "—",
                  money(total_prem, lang) if total_prem is not None else NA, "—"])
    parts_list = {"title": t("pt_table_title", lang), "items": [" | ".join(r) for r in table],
                  "table": {"columns": cols, "rows": table, "widths": [5, 25, 17, 11, 10, 17, 15]},
                  "notes": [t("pt_table_note", lang)]}
    worst = next(p for p in items if p["index"] == totals.get("worst_index"))
    level_label = tx.label(tx.LEVEL_LABELS, totals.get("level"), lang)
    ref_pct = totals.get("reference_rate_pct")
    s4_rows = [_row(t("level", lang), level_label, t("pt_level_note", lang, part=_part_label(worst, lang)) + "; " +
                    t("uncalibrated", lang)),
               _row(t("premium", lang), money(total_prem, lang) if total_prem is not None else NA,
                    t("pt_premium_note", lang, n=len(items), days=totals.get("term_days"))
                    if total_prem is not None else t("pt_premium_incomplete", lang,
                                                     known=money(totals.get("premium_known"), lang))),
               _row(t("pt_ref_rate", lang), pct(ref_pct, lang) if ref_pct is not None else NA,
                    t("pt_ref_note", lang))]
    fr_parts = "; ".join(f"{t('pt_part_short', lang, n=p['index'])} — {_fr_short(p['franchise'], lang)}"
                         for p in items)
    fr_text = t("pt_fr_by_parts", lang, list=fr_parts)
    s4_rows.append(_row(t("franchise", lang), fr_text))
    src = PT.get("source") or "default"
    notes = [t(x["code"], lang, **{k: (money(v, lang) if k in ("sum", "total") else v)
                                   for k, v in (x.get("params") or {}).items()}) for x in PT.get("notes") or []]
    s5 = t("pt_s5_confirmed", lang) if PT.get("confirmed") else t("pt_s5_default", lang,
                                                                   source=t("pt_src_" + src, lang))
    agg = totals.get("scenarios") or {}
    sc = D.get("scenarios") or {}
    ret = totals.get("retention") or {}
    summary_bits = [t("pt_sum_intro", lang, n=len(items), premium=money(total_prem, lang)
                      if total_prem is not None else NA, level=level_label, part=_part_label(worst, lang))]
    if sc.get("available"):
        summary_bits.append(t("pt_sum_sc", lang, eml=money(sc["items"]["EML"]["amount"], lang),
                              mfl=money(sc["items"]["MFL"]["amount"], lang),
                              rule=t("pt_sc_rule_" + str(agg.get("rule") or "max"), lang)))
    if ret.get("known"):
        summary_bits.append(t("pt_sum_ret_ok" if ret.get("within") else "pt_sum_ret_over", lang,
                              limit=money(ret.get("limit"), lang), x=money(ret.get("eml_excess"), lang)))
    summary = " ".join(summary_bits)
    obj_mode = PT.get("object_mode") or "different"
    js = {"mode": "multi", "source": src, "source_label": t("pt_src_" + src, lang),
          "confirmed": bool(PT.get("confirmed")), "object_mode": obj_mode,
          "object_mode_label": t("pt_mode_" + obj_mode, lang), "same_object_default": PT.get("same_object_default"),
          "items": pjs,
          "totals": {"premium": total_prem, "premium_text": money(total_prem, lang) if total_prem is not None else NA,
                     "premium_complete": totals.get("premium_complete"), "premium_known": totals.get("premium_known"),
                     "sum_insured": totals.get("sum_insured"), "level": totals.get("level"),
                     "level_label": level_label, "worst_index": totals.get("worst_index"),
                     "reference_rate_pct": ref_pct, "reference_note": t("pt_ref_note", lang),
                     "scenarios": {"available": bool(sc.get("available")), "rule": agg.get("rule"),
                                   "rule_text": t("pt_sc_rule_" + str(agg.get("rule") or "max"), lang),
                                   **{s.lower(): (sc.get("items") or {}).get(s, {}).get("amount")
                                      for s in ("PML", "EML", "MFL")},
                                   "excluded": agg.get("excluded") or [], "how": _parts_sc_how(sc, lang)
                                   if sc.get("available") else []},
                     "retention": {"known": bool(ret.get("known")), "limit": ret.get("limit"),
                                   "compared_with": "eml", "eml_excess": ret.get("eml_excess"),
                                   "within": ret.get("within"), "mfl_excess": ret.get("mfl_excess"),
                                   "status": ret.get("status"), "legal_ref": ret.get("legal_ref")},
                     "value": totals.get("value"), "calibrated": ae.CALIBRATED},
          "notes": notes, "summary": summary, "table": {"columns": cols, "rows": table},
          "suggested_parts": [dict(x, class_name=_class_label(x["class_code"], next(
              (p.get("class_name") for p in items if p["class_code"] == x["class_code"]), None), lang))
              for x in PT.get("suggested") or []],
          "calibrated": ae.CALIBRATED}
    rate_extra = {"reference_pct": ref_pct, "reference_only": True, "reference_note": t("pt_ref_note", lang),
                  "by_parts": [{"index": x["index"], "class_code": x["class_code"], "mode": x["rate"]["mode"],
                                "applied_pct": x["rate"]["applied_pct"], "min_pct": x["rate"]["min_pct"],
                                "min_source": x["rate"]["min_source"], "final_pct": x["rate"]["final_pct"]}
                               for x in pjs]}
    return {"s1_rows": s1_rows, "s1_paragraph": t("pt_s1_par", lang, n=len(items),
                                                  mode=t("pt_mode_" + obj_mode, lang)) + (" " + " ".join(notes)
                                                                                          if notes else ""),
            "s3_rows": s3_rows, "s3_paragraphs": [t("pt_s3_par", lang)],
            "s4_rows": s4_rows, "s4_lists": [parts_list] + lists, "fr_text": fr_text,
            "s5_paragraph": s5, "missing_lines": missing_lines, "summary": t("an_summary", lang, text=summary),
            "analytics_json": {"available": False, "reason": "by_parts", "text": t("pt_an_by_parts", lang),
                               "summary": {"text": summary, "sentences": summary_bits}, "calibrated": ae.CALIBRATED},
            "worst": worst["index"], "rate_extra": rate_extra, "json": js}


# --------------------------------------------------------------------------- #
#  Вилка ставки (01.10.2026): показ на языке акта
# --------------------------------------------------------------------------- #

def _sg(x, lang: str) -> str:
    """«+ 15 %» / «− 3,2 %» — слагаемое поправки в формуле «(1 + 15 %)»."""
    v = round(float(x or 0), 2)
    return ("− " if v < 0 else "+ ") + pct(abs(v), lang, 2)


def _fork_src(src: Optional[dict], lang: str) -> Optional[dict]:
    """Источник отметки на языке акта: {title, url, as_of}."""
    if not src:
        return None
    kind = src.get("kind")
    title = src.get("title_ru")
    if kind in ("policy", "regulator", "policy_act") and lang != "ru":
        # название документа в справочнике — по-русски; на других языках — название словами акта и номер приказа
        order = _order_ref_text(title, lang)
        title = t("rf_doc_regulator" if kind == "regulator" else "rf_doc_policy", lang) + (f" — {order}" if order else "")
    if kind == "insurer_min":
        # минимальная ставка страховщика (правка администратора, app/min_rates.py): «… (установлена администратором
        # 02.10.2026, примечание …)»
        title = mrs.source_label(src.get("row") or {}, lang) or title
    elif kind in ("policy", "regulator"):
        title = t("rf_src_policy_part", lang, title=title, code=src.get("product_code") or "") if src.get("part_text") \
            else t("rf_src_policy", lang, title=title, date=_date(src.get("as_of")))
    elif kind == "policy_act":
        title = t("rf_src_policy_act", lang, title=title)
    elif kind == "napp":
        title = t("rf_src_napp", lang, date=_date(src.get("as_of")))
    elif kind == "napp_claims":
        title = t("rf_src_napp_claims", lang, date=_date(src.get("as_of")))
    elif kind == "adjusted":
        title = t("rf_src_adjusted", lang)
    elif kind in ("request", "contract", "technical", "parts", "factors"):
        title = t("rf_src_" + kind, lang)
    elif kind == "statutory":
        title = t("rf_src_statutory", lang, title=_act_ref_text(title, lang))
    elif kind == "stat" and lang != "ru" and src.get("name"):
        title = f"{str(title or '').split(' — ')[0]} — {src['name']}"     # набор — названием показателя на языке акта
    return {"title": title, "url": src.get("url"), "as_of": src.get("as_of"), "kind": kind}


_LAT = {"А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "К": "K", "Л": "L", "М": "M", "Н": "N", "О": "O",
        "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "X", "Ц": "TS", "Ч": "CH", "Ш": "SH"}


def _order_ref_text(ref: Optional[str], lang: str) -> Optional[str]:
    """Номер и дата приказа из названия версии тарифа («… — Приказ №54-П от 23.09.2025») на языке акта:
    uz — «54-P-son buyruq, 23.09.2025», en — «Order No. 54-P of 23.09.2025»; не нашли — None."""
    m = re.search(r"Приказ\s*№\s*([0-9A-Za-zА-Яа-яЁё\-/]+)\s*(?:от\s*(\d{2}\.\d{2}\.\d{4}))?", str(ref or ""))
    if not m:
        return None
    no = "".join(_LAT.get(ch.upper(), ch) if re.match(r"[А-Яа-яЁё]", ch) else ch for ch in m.group(1))
    dt = m.group(2)
    if lang == "uz":
        return f"{no}-son buyruq" + (f", {dt}" if dt else "")
    if lang == "en":
        return f"Order No. {no}" + (f" of {dt}" if dt else "")
    return m.group(0)


def _act_ref_text(ref: Optional[str], lang: str) -> str:
    """«ПКМ №532» на языке акта: uz — «VMQ №532», en — «CM Resolution No. 532»; иначе — «нормативный акт»."""
    if not ref:
        return t("na", lang)
    if lang == "ru":
        return ref
    s = re.sub(r"ПКМ", {"uz": "VMQ", "en": "CM Resolution"}[tx.lang_of(lang)], str(ref))
    if lang == "en":
        s = s.replace("№", "No. ")
    return s if not re.search(r"[А-Яа-яЁё]", s) else t("rf_doc_regulation", lang)


def _fork_note(m: dict, lang: str, F: dict) -> str:
    code, p = m.get("note") or "", m.get("params") or {}
    if code == "act":
        return t("rf_n_act", lang, adj=_spct(p.get("level") or 0, lang, 2))
    if code in ("adjusted", "adjusted_min"):
        return t("rf_n_" + code, lang, reg=_sg(p.get("region"), lang), mkt=_sg(p.get("market"), lang))
    if code in ("market", "market_below_min"):
        return t("rf_n_" + code, lang, date=_date((m.get("source") or {}).get("as_of")))
    if code in ("factors", "factors_min"):
        return t("rf_n_" + code, lang, mult=_mult(p.get("mult") or 1, lang))
    return t("rf_n_" + code, lang) if code else ""


def _fork_num(x, lang: str) -> str:
    """Число процента без знака % (для строки «0,35 – 0,42 – 0,695 %»)."""
    return pct(x, lang).replace("%", "").replace(tx.NBSP, "").strip() if x is not None else "—"


def _stat_name(i: dict, lang: str) -> str:
    return tx.label(tx.STAT_LABELS, i["id"], lang) if i["id"] in tx.STAT_LABELS else (i.get("name_ru") or i["id"])


def _fork_region_lines(R: dict, lang: str, cls: Optional[str]) -> tuple:
    """(строки поправки региона, источники) на языке акта."""
    pct_txt = _spct(R.get("pct") or 0, lang, 2) if R.get("pct") else pct(0, lang)
    lines, srcs = [], []
    head = t("rf_reg_title", lang, pct=pct_txt)
    if R.get("reason"):
        why = {"no_rules": t("rf_reg_r_no_rules", lang, cls=cls or ""), "kind": t("rf_reg_r_kind", lang, cls=cls or ""),
               "region_unknown": t("rf_reg_r_region_unknown", lang, region=R.get("region_requested") or ""),
               "no_regional": t("rf_reg_r_no_regional", lang),
               "zero_weight": t("rf_reg_r_zero_weight", lang)}.get(R["reason"], "")
        lines.append(f"{head} — {t('rf_reg_none', lang)} ({why})")
    else:
        lo, hi = R["bounds"]
        txt = f"{head} — " + t("rf_reg_how", lang, sens=_mult(R["sensitivity"], lang), lo=_spct(lo, lang, 2),
                               hi=_spct(hi, lang, 2))
        if R.get("clamped"):
            txt += "; " + t("rf_reg_raw", lang, raw=_spct(R["raw_pct"], lang, 2), lim=_spct(hi if R["clamped"] == "max"
                                                                                           else lo, lang, 2))
        lines.append(txt)
    per1000 = {"ru": "на 1 000 жителей", "uz": "1 000 aholiga", "en": "per 1,000 people"}[tx.lang_of(lang)]
    for i in R.get("indicators") or []:
        name = tx.label(tx.STAT_LABELS, i["id"], lang) if i["id"] in tx.STAT_LABELS else i.get("name_ru") or i["id"]
        if i.get("used"):
            unit = tx.label(tx.STAT_UNITS, i.get("value_unit_ru"), lang) if i.get("value_unit_ru") in tx.STAT_UNITS \
                else (i.get("value_unit_ru") or "")
            if i.get("kind") == "count_pc":
                unit = f"{unit} {per1000}"
            line = t("rf_reg_ind", lang, name=name, period=i.get("period") or "", reg=_num4(i["region_value"], lang),
                     cty=_num4(i["country_value"], lang), unit=unit, ratio=_mult(i["ratio"], lang),
                     effect=_spct(i["effect_pct"], lang, 2))
            cav = _caveat(i["id"], lang)
            lines.append(line + (f" ({t('rf_caveat', lang, text=cav)})" if cav else ""))
            s = _fork_src(dict(i["source"], name=name) if i.get("source") else None, lang)
            if s:
                srcs.append(s)
        else:
            lines.append(t("rf_reg_ind_off", lang, name=name, why=tx.label(tx.FORK_WHY_LABELS, i.get("why"), lang)))
    return lines, srcs


def _caveat(iid: str, lang: str) -> Optional[str]:
    """Оговорка показателя региона: что он измеряет на самом деле (все ДТП, все кражи, жилой фонд)."""
    return tx.label(tx.STAT_CAVEATS, iid, lang) if iid in tx.STAT_CAVEATS else None


def _pack_choice_text(pc: Optional[dict], lang: str, cls: Optional[str] = None, prefix: str = "rf_mkt_pack_") -> Optional[str]:
    """Какая строка НАПП взята для комплексного продукта и почему (exact | nearest | class) — на языке акта."""
    if not pc or not pc.get("how"):
        return None
    kw = {"pack": ",".join(pc.get("pack_classes") or []), "code": pc.get("product_code") or "",
          "classes": ",".join(pc.get("classes") or []), "cls": pc.get("class_used") or cls or ""}
    txt = t(prefix + pc["how"], lang, **kw)
    if pc.get("subclass_note"):
        txt += t(prefix + "sub", lang, sub=", ".join(x for x in pc.get("product_classes") or []
                                                      if not str(x).isdigit()))
    return txt


def _fork_market_lines(M: dict, lang: str, cls: Optional[str] = None) -> list:
    pct_txt = _spct(M.get("pct") or 0, lang, 2) if M.get("pct") else pct(0, lang)
    head = t("rf_mkt_title", lang, pct=pct_txt)
    r = M.get("reason")
    kw = {"act": pct(M.get("act_pct"), lang), "market": pct(M.get("market_rate_pct"), lang),
          "lr": tx.pct_fixed(M.get("loss_ratio_pct"), lang, 1) if M.get("loss_ratio_pct") is not None else t("na", lang),
          # убыточность взята за полный год — дата его среза (01.01 следующего года), а не последнего среза
          "date": (_date(f"{int(_fy_year(M.get('full_year_period'))) + 1}-01-01")
                   if M.get("basis") == "full_year" and _fy_year(M.get("full_year_period"))
                   else _date(M.get("as_of"))), "pct": pct_txt,
          "thr": tx.pct_fixed(M.get("threshold") if M.get("threshold") is not None
                              else ((M.get("steps") or [[0]])[0][0]), lang, 0)}
    body = t("rf_mkt_" + (r or "no_data") if r in ("applied", "act_not_below", "lr_below", "no_data")
             else "rf_mkt_no_data", lang, **kw)
    lines = [f"{head} — {body}"]
    if M.get("full_year_switch"):
        # скачок убыточности за неполный год: ступень взята по полному году (rate_fork.market.basis)
        lines.append(t("rf_mkt_fy_switch", lang, last=tx.pct_fixed(M.get("loss_ratio_last_pct"), lang, 1),
                       fy=tx.pct_fixed(M.get("loss_ratio_full_year_pct"), lang, 1),
                       year=_fy_year(M.get("full_year_period")) or "—"))
    if M.get("capped"):
        lines.append(t("rf_mkt_capped", lang, market=pct(M.get("market_rate_pct"), lang),
                       pct=_spct(M.get("effective_pct") or 0, lang, 2) if M.get("effective_pct") else pct(0, lang)))
    steps = "; ".join(t("rf_mkt_step", lang, thr=tx.pct_fixed(s[0], lang, 0), pct=_spct(s[1], lang, 2))
                      for s in M.get("steps") or [])
    if steps:
        lines.append(t("rf_mkt_steps", lang, steps=steps, basis=t("rf_mkt_basis_" + (M.get("basis") or "last"), lang)))
    if M.get("cap_at_market"):
        lines.append(t("rf_mkt_cap_on", lang))
    pcx = _pack_choice_text(M.get("pack_choice"), lang, cls)
    if pcx:
        lines.append(pcx)
    elif M.get("pack"):
        lines.append(t("rf_mkt_pack", lang))
    if (M.get("pack_choice") or {}).get("how") in ("exact", "nearest"):
        # полный год пакета и одиночные строки классов продукта (правило проекта № 5 — по каждому классу)
        if M.get("loss_ratio_full_year_pct") is not None and M.get("rate_full_year_pct") is not None:
            lines.append(t("rf_mkt_pack_fy", lang, year=_fy_year(M.get("full_year_period")) or "—",
                           rate=pct(M.get("rate_full_year_pct"), lang),
                           lr=tx.pct_fixed(M.get("loss_ratio_full_year_pct"), lang, 1)))
        cr = _class_rows_text(M.get("class_rows"), lang)
        if cr:
            lines.append(cr)
    return lines


def _fy_year(period: Optional[str]) -> Optional[str]:
    """«2025 год» → «2025» (год полного среза НАПП)."""
    d = "".join(ch for ch in str(period or "") if ch.isdigit())[:4]
    return d or None


def _class_rows_text(rows: Optional[list], lang: str) -> Optional[str]:
    """«по одиночным строкам классов: класс 3 — 14,6 %, класс 14 — 24,2 % (убыточность, срез …; ставка: …)»."""
    rows = [r for r in rows or [] if r.get("class_code")]
    if not rows:
        return None
    lrs, rates = [], []
    for r in rows:
        if r.get("available") and r.get("loss_ratio_pct") is not None:
            lrs.append(t("rf_mkt_class_row", lang, cls=r["class_code"], v=tx.pct_fixed(r["loss_ratio_pct"], lang, 1)))
        else:
            lrs.append(t("rf_mkt_class_row_na", lang, cls=r["class_code"]))
        if r.get("available") and r.get("rate_pct") is not None:
            rates.append(t("rf_mkt_class_row", lang, cls=r["class_code"], v=pct(r["rate_pct"], lang)))
    date = next((r.get("date") for r in rows if r.get("date")), None)
    return t("rf_mkt_class_rows", lang, rows=", ".join(lrs), date=_date(date) or "—",
             rates=", ".join(rates) or t("na", lang))


def _fork_view(F: Optional[dict], lang: str, cls: Optional[str] = None) -> dict:
    """
    Вилка ставки одного класса (или справочная договора) на языке акта:
    {"json": блок rate_fork ответа, "list": список раздела 4 с таблицей, "summary": одна фраза, "row": строка
    раздела 4 рядом с премией или None, "overview": «0,35 – 0,42 – 0,695 %» для страницы скоринга}.
    """
    lang = tx.lang_of(lang)
    NA = t("na", lang)
    if not F:                                # акты до 01.10.2026 — без вилки
        return {"json": {"available": False, "reason": "old_act", "marks": [], "calibrated": ae.CALIBRATED},
                "list": None, "summary": None, "row": None, "overview": None}
    marks = []
    for m in F.get("marks") or []:
        lab = t("rf_m_" + m["code"], lang)
        marks.append({"code": m["code"], "label": lab, "rate_pct": m.get("rate_pct"), "premium": m.get("premium"),
                      "is_recommended": bool(m.get("is_recommended")), "source": _fork_src(m.get("source"), lang),
                      "note": _fork_note(m, lang, F)})
    by = {m["code"]: m for m in marks}
    adj = F.get("adjustments") or {}
    R = adj.get("region") or F.get("region_data")
    MK = adj.get("market")
    how, srcs = [], []
    reason = F.get("reason")
    # одна фраза вывода
    if reason in ("statutory", "statutory_undefined"):
        ref_txt = ((F.get("marks") or [{}])[0].get("source") or {}).get("title_ru") if F.get("marks") else None
        summary = t("rf_sum_statutory", lang, rate=pct(by["act"]["rate_pct"], lang), ref=_act_ref_text(ref_txt, lang)) \
            if "act" in by else t("rf_sum_statutory_na", lang, ref=NA)
    elif reason == "undefined":
        summary = t("rf_sum_undefined", lang, market=pct(by["market"]["rate_pct"], lang)) if "market" in by \
            else t("rf_sum_undefined_nomarket", lang)
    elif reason == "error":
        summary = t("rf_sum_error", lang)
    elif reason == "parts_reference":
        summary = t("rf_sum_parts", lang, min=pct((by.get("min") or {}).get("rate_pct"), lang),
                    act=pct((by.get("act") or {}).get("rate_pct"), lang),
                    adj=pct((by.get("adjusted") or {}).get("rate_pct"), lang))
    else:
        rec = by[F["recommended"]["code"]]
        base_kw = {"min": pct((by.get("min") or {}).get("rate_pct"), lang), "rec": pct(rec["rate_pct"], lang)}
        summary = t("rf_sum", lang, market=pct(by["market"]["rate_pct"], lang), **base_kw) if "market" in by \
            else t("rf_sum_nomarket", lang, **base_kw)
        if F["recommended"]["code"] == "act" and "adjusted" in by and by["adjusted"]["rate_pct"] != rec["rate_pct"]:
            summary += t("rf_sum_adj", lang, adj=pct(by["adjusted"]["rate_pct"], lang))
        if F["recommended"]["code"] == "adjusted" and by["act"]["rate_pct"] != rec["rate_pct"]:
            summary += t("rf_sum_act", lang, act=pct(by["act"]["rate_pct"], lang))
    for code in ("request", "contract"):
        if code in by:
            summary += t("rf_sum_doc", lang, what=by[code]["label"], rate=pct(by[code]["rate_pct"], lang),
                         pos=tx.label(tx.FORK_POS_LABELS, (F.get("position") or {}).get(code) or "none", lang)) \
                if reason not in ("parts_reference",) else ""
    # поправки
    reg_js = mkt_js = None
    if R and F.get("available") and reason != "parts_reference":
        rl, rs_ = _fork_region_lines(R, lang, cls)
        how += rl
        srcs += rs_
        reg_js = {"pct": R.get("pct"), "raw_pct": R.get("raw_pct"),
                  "clamped": R.get("clamped"), "bounds": R.get("bounds"), "sensitivity": R.get("sensitivity"),
                  "reason": R.get("reason"), "region_key": R.get("region_key"),
                  "indicators": [{"id": i["id"], "name": tx.label(tx.STAT_LABELS, i["id"], lang)
                                  if i["id"] in tx.STAT_LABELS else i.get("name_ru"),
                                  "region_value": i.get("region_value"), "country_value": i.get("country_value"),
                                  "ratio": i.get("ratio"), "effect_pct": i.get("effect_pct"), "period": i.get("period"),
                                  "unit": i.get("unit_ru"), "source": _fork_src(dict(i["source"], name=_stat_name(i, lang))
                                                                  if i.get("source") else None, lang),
                                  "used": bool(i.get("used")), "why": i.get("why"), "caveat": _caveat(i["id"], lang),
                                  "why_text": tx.label(tx.FORK_WHY_LABELS, i["why"], lang) if i.get("why") else None}
                                 for i in R.get("indicators") or []],
                  "text": rl[0], "lines": rl, "calibrated": ae.CALIBRATED}
    if MK:
        ml = _fork_market_lines(MK, lang, cls)
        how += ml
        mds = _fork_src(MK.get("source"), lang)
        mkt_js = {"pct": MK.get("pct"), "reason": MK.get("reason"), "loss_ratio_pct": MK.get("loss_ratio_pct"),
                  "capped": bool(MK.get("capped")), "effective_pct": MK.get("effective_pct"),
                  "cap_at_market": bool(MK.get("cap_at_market")),
                  "loss_ratio_basis": MK.get("basis"), "market_rate_pct": MK.get("market_rate_pct"),
                  "threshold": MK.get("threshold"), "steps": MK.get("steps"), "as_of": MK.get("as_of"),
                  "row_key": MK.get("row_key"), "pack_choice": MK.get("pack_choice"),
                  # 01.10.2026: скачок убыточности за неполный год — ступень по полному году (rate_fork.market.basis)
                  "basis": MK.get("basis"), "full_year_switch": bool(MK.get("full_year_switch")),
                  "loss_ratio_last_pct": MK.get("loss_ratio_last_pct"),
                  "loss_ratio_full_year_pct": MK.get("loss_ratio_full_year_pct"),
                  "rate_full_year_pct": MK.get("rate_full_year_pct"), "full_year_period": MK.get("full_year_period"),
                  "class_rows": list(MK.get("class_rows") or []),
                  "pack_note": _pack_choice_text(MK.get("pack_choice"), lang, cls),
                  "source": mds, "text": ml[0], "lines": ml, "calibrated": ae.CALIBRATED}
    if F.get("available") and reason != "parts_reference":
        how.append(t("rf_mode_" + (F.get("mode") or "reference"), lang))
        if F.get("franchise_applied") and F.get("premium_final") is not None:
            how.append(t("rf_fr_note", lang, premium=money(F["premium_final"], lang)))
        if F.get("rate_type") == "fixed":
            how.append(t("rf_fixed_note", lang, days=F.get("term_days")))
        how.append(t("rf_calibrated", lang))
    # источники: отметки и показатели (без повторов)
    seen, src_lines = set(), []
    for s in [m["source"] for m in marks if m.get("source")] + srcs:
        if s.get("kind") in ("adjusted", "policy_act", "request", "contract", "technical", "parts", "factors"):
            continue
        key = (s["title"], s.get("url"))
        if key in seen:
            continue
        seen.add(key)
        src_lines.append(t("rf_src_line", lang, title=s["title"], url=s["url"]) if s.get("url")
                         else t("rf_src_line", lang, title=s["title"], url="").rstrip(" —"))
    rows = []
    for m in marks:
        lab = m["label"] + (f" — {t('rf_recommended', lang)}" if m["is_recommended"] else "")
        rows.append([lab, pct(m["rate_pct"], lang) if m["rate_pct"] is not None else NA,
                     money(m["premium"], lang) if m["premium"] is not None else NA, m["note"],
                     (m["source"] or {}).get("title") or ""])
    table = {"columns": [t("rf_col_mark", lang), t("rf_col_rate", lang), t("rf_col_premium", lang),
                         t("rf_col_why", lang), t("rf_col_src", lang)], "rows": rows, "widths": [18, 10, 15, 32, 25]}
    li = _li(t("rf_title", lang), [], table if rows else None, src_lines, [summary] + how)
    rec = F.get("recommended")
    js = {"available": bool(F.get("available")), "reason": reason, "mode": F.get("mode"), "title": t("rf_title", lang),
          "unit": t("rf_unit", lang), "marks": marks,
          "adjustments": {"region": reg_js, "market": mkt_js} if (reg_js or mkt_js) else None,
          "recommended": dict(rec) if rec else None, "position": dict(F.get("position") or {}),
          "summary": summary, "how": how, "sources": [s for s in [m["source"] for m in marks if m.get("source")] + srcs],
          "reference_only": bool(F.get("reference_only")), "term_days": F.get("term_days"),
          # премия акта с учётом применённой франшизы (та же, что premium.amount)
          "premium_final": F.get("premium_final"), "franchise_applied": bool(F.get("franchise_applied")),
          "rate_type": F.get("rate_type") or "annual",
          "table": table, "calibrated": ae.CALIBRATED}
    row = None
    if F.get("available") and reason != "parts_reference" and "adjusted" in by:
        if F["recommended"]["code"] == "act":
            row = _row(t("rf_row_adjusted", lang), pct(by["adjusted"]["rate_pct"], lang),
                       t("rf_row_adjusted_note", lang, premium=money(by["adjusted"]["premium"], lang)))
        else:
            row = _row(t("rf_row_act", lang), pct(by["act"]["rate_pct"], lang),
                       t("rf_row_act_note", lang, premium=money(by["act"]["premium"], lang)))
    ov_rec = (rec or {}).get("rate_pct")
    ov_min = (by.get("min") or {}).get("rate_pct")
    ov_mkt = (by.get("market") or {}).get("rate_pct")
    overview = None if ov_rec is None and ov_min is None and ov_mkt is None else \
        f"{_fork_num(ov_min, lang)} – {_fork_num(ov_rec, lang)} – {_fork_num(ov_mkt, lang)}" + \
        ("%" if lang == "en" else tx.NBSP + "%")
    return {"json": js, "list": li, "summary": summary, "row": row, "overview": overview}


def _fork_parts_view(D: dict, PV: dict, lang: str) -> dict:
    """Договор из частей: вилка по каждой части (таблица) и справочная вилка договора."""
    items = (D.get("parts") or {}).get("items") or []
    cv = _fork_view(D.get("rate_fork"), lang)
    NA = t("na", lang)
    rows, notes, src_lines = [], [], []
    for p, pj in zip(items, PV["json"]["items"]):
        fv = _fork_view(p.get("rate_fork"), lang, p["class_code"])
        pj["rate_fork"] = fv["json"]
        by = {m["code"]: m for m in fv["json"]["marks"]}
        adj = fv["json"].get("adjustments") or {}
        cell = lambda c: (pct(by[c]["rate_pct"], lang) + (" (" + t("rf_rec_short", lang) + ")" if by[c]["is_recommended"] else "")) if c in by else "—"  # noqa
        rows.append([pj["label"], cell("min"), cell("act"), cell("adjusted"), cell("market"),
                     " / ".join(_spct(x, lang, 2) if x else pct(0, lang)
                                for x in ((adj.get("region") or {}).get("pct"), (adj.get("market") or {}).get("pct")))
                     if fv["json"]["available"] else "—"])
        notes.append(t("rf_part_line", lang, part=pj["label"], text=fv["summary"]))
        skip = (t("rf_calibrated", lang), t("rf_mode_reference", lang), t("rf_mode_apply", lang))
        notes += [t("rf_part_line", lang, part=t("pt_part_short", lang, n=p["index"]), text=x)
                  for x in fv["json"]["how"] if x not in skip]
        for s in fv["list"]["sources"] if fv["list"] and fv["list"].get("sources") else []:
            if s not in src_lines:
                src_lines.append(s)
    cby = {m["code"]: m for m in cv["json"]["marks"]}
    rows.append([t("rf_contract_row", lang)] + [pct(cby[c]["rate_pct"], lang) if c in cby else "—"
                                                 for c in ("min", "act", "adjusted")] + ["—", "—"])
    notes += [t("rf_mode_" + (cv["json"].get("mode") or "reference"), lang), t("rf_calibrated", lang)]
    table = {"columns": [t("rf_col_part", lang), t("rf_m_min", lang), t("rf_m_act", lang), t("rf_m_adjusted", lang),
                         t("rf_m_market", lang), "±"], "rows": rows, "widths": [26, 13, 13, 16, 14, 18]}
    js = dict(cv["json"], parts=[{"index": p["index"], "class_code": p["class_code"], "summary": pj["rate_fork"]["summary"],
                                  "recommended": pj["rate_fork"].get("recommended")}
                                 for p, pj in zip(items, PV["json"]["items"])])
    return {"json": js, "list": _li(t("rf_parts_title", lang), [], table, src_lines, [cv["summary"]] + notes),
            "summary": cv["summary"], "overview": cv["overview"] or NA}


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
    # комплексный продукт (30.09.2026): перечень частей договора
    PV = _parts_view(D, lang) if (D.get("parts") or {}).get("mode") == "multi" else None
    p1 = []
    if PV:
        rows1 += PV["s1_rows"]
        p1.append(PV["s1_paragraph"])
    s1 = {"n": 1, "title": t("s1", lang), "paragraphs": p1, "rows": rows1}

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
    cvp = ((D.get("parts") or {}).get("totals") or {}).get("value") if PV else None
    rows3 += [_row(t("ratio", lang), ratio,
                   t("pt_ratio_note", lang, sum=money(cvp["sum_insured"], lang), value=money(cvp["object_value"], lang))
                   if cvp else None),
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
    if PV:
        rows3 += PV["s3_rows"]
        p3 += PV["s3_paragraphs"]
    EX = _exchange_view(D, lang)
    src3 = list(mv["source_lines"])
    if EX["row"]:
        rows3.append(EX["row"])
        src3.append(EX["source_line"])
    s3 = {"n": 3, "title": t("s3", lang), "paragraphs": p3, "rows": rows3, "source_lines": src3,
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
        if rate_res.get("rate_type") == "fixed":
            # фиксированная ставка (на весь срок): тип и годовой эквивалент для сравнения с рынком — обе ставки
            rows4 += [_row(t("rate_type_row", lang), t("rate_type_fixed", lang)),
                      _row(t("rate_annual_equiv_row", lang), pct(rate_res.get("annual_equiv_pct"), lang),
                           f"{pct(rate_res['applied_pct'], lang)} × 365 / {rate_res['term_days']}")]
        if pf["franchise_applied"]:
            rows4.append(_row(t("rate_with_fr", lang), pct(pf["rate_pct"], lang),
                              t("min_applied", lang) if fr.get("floor_applied") else None))
        prem_note = t("premium_term", lang, days=rate_res["term_days"])
        if pf["franchise_applied"]:
            prem_note += "; " + t("premium_no_fr", lang, before=money(rate_res["premium"], lang))
        rows4 += [_row(t("base_rate", lang), pct(rate_res["base_pct"], lang)),
                  _row(t("adj", lang), "+" + pct(rate_res["adj_pct"], lang), t("uncalibrated", lang)),
                  _row(t("min_rate", lang), pct(rate_res["min_pct"], lang) if rate_res["min_pct"] is not None
                       else NA, _min_src_text(D, lang)),
                  _row(t("premium", lang), money(pf["amount"], lang), prem_note)]
    elif mode == "statutory":
        rows4 += [_row(t("applied_rate", lang), pct(rate_res["applied_pct"], lang), t("rate_by_act", lang)),
                  _row(t("adj", lang), t("adj_none_statutory", lang)),
                  _row(t("premium", lang), money(rate_res["premium"], lang),
                       t("premium_term", lang, days=rate_res["term_days"]))]
    elif mode == "multi":
        rows4 = PV["s4_rows"]
    else:
        rows4 += [_row(t("applied_rate", lang), t("rate_undefined", lang)),
                  _row(t("premium", lang), NA)]
    # вилка ставки (01.10.2026): минимум → ставка акта → с учётом региона и рынка → рынок; у частей — по каждой части
    FV = _fork_parts_view(D, PV, lang) if PV and D.get("rate_fork") else \
        _fork_view(D.get("rate_fork"), lang, must["class_code"])
    if not PV and FV.get("row") and mode == "tariff":
        rows4.append(FV["row"])
    # факторы объекта по подгруппам класса (02.10.2026): строка (у частей — по строке на часть) и перечень ниже
    FAV = _factor_view(D, lang)
    rows4 += FAV["rows"]
    fr_text = PV["fr_text"] if PV else _fr_text(fr, lang)
    if not PV:
        rows4.append(_row(t("franchise", lang), fr_text))
    scv = _scenarios_view(D.get("scenarios"), must, lang)
    rows4 += scv["rows"]
    if PV:
        # комплексный продукт: уровень договора — по самой опасной части; дальше таблица частей и разбор каждой
        lists4 = [{"title": t("pt_factors_title", lang, n=PV["worst"]), "items": factors + [rule_text]},
                  {"title": t("how_title", lang), "items": [_text(h, lang) for h in rate_res["how"]]}]
        anv = {"summary": PV["summary"], "lists": PV["s4_lists"], "json": PV["analytics_json"]}
    else:
        lists4 = [{"title": t("factors", lang), "items": factors + [rule_text]},
                  {"title": t("how_title", lang),
                   "items": [_text(h, lang) for h in rate_res["how"]]}]
        # аналитика риска (30.09.2026): резюме первым абзацем, таблицы — после «Как посчитан тариф»
        try:
            anv = _analytics_view(D, lang)
        except Exception as e:               # сбой показа аналитики не роняет акт: остальные разделы на месте
            print("акт: аналитика раздела 4 не показана:", type(e).__name__, e)
            anv = {"summary": None, "lists": [], "json": {"available": False, "reason": "render_error",
                                                          "calibrated": ae.CALIBRATED}}
    if FV.get("list"):
        lists4.append(FV["list"])            # «Вилка ставки» — сразу после «Как посчитан тариф»
    if FAV["lines"]:
        # «Факторы объекта по подгруппам класса» — после вилки: построчно, вклад каждого фактора в сумах
        lists4.append({"title": t("fa_title", lang), "items": FAV["lines"]})
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
    bv = _borrower_view(D.get("borrower"), lang)
    if bv["available"]:
        # «Заёмщик: данные кредитного бюро» (01.10.2026): в уровень риска и ставку не входит
        lists4.append({"title": t("cb_title", lang), "items": bv["lines"]})
    bmv = _below_min_view(D, lang)
    if bmv["json"]["available"]:
        # «Ставка ниже минимальной: можно ли застраховать» (01.10.2026): ответ, доводы за и против, условия
        lists4.append({"title": t("bm_title", lang), "items": bmv["lines"]})
    s4 = {"n": 4, "title": t("s4", lang), "paragraphs": p4, "rows": rows4, "lists": lists4}

    # ---------- раздел 5 ----------
    dec = D["decision"]
    p5 = [t(dec["code"], lang)]
    if bmv["json"]["available"]:
        # рекомендация акта — по ставке акта; по запрошенной ставке ниже минимума — ответ оценки
        p5.append(bmv["s5"])
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
    p5 += FAV["s5"]
    if FAV["unfilled"]:
        lists5.append({"title": t("fa_unfilled_title", lang), "items": FAV["unfilled"]})
    if PV:
        # распределение суммы по классам: подтверждено сотрудником или принято по умолчанию; поля частей
        p5.append(PV["s5_paragraph"])
        if PV["missing_lines"]:
            lists5.append({"title": t("pt_missing_title", lang), "items": PV["missing_lines"]})
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
    out = {
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
                 "product_classes": (must.get("product_classes") or []),
                 # вилка ставки, режим apply: ставка акта уже с поправками региона и рынка (01.10.2026)
                 "fork_applied": bool(rate_res.get("fork_applied")), "fork_act_pct": rate_res.get("fork_act_pct"),
                 # факторы объекта, режим apply (02.10.2026): ставка акта уже с множителем факторов
                 "factors_applied": bool(rate_res.get("factors_applied")),
                 "factor_act_pct": rate_res.get("factor_act_pct"),
                 # тип ставки продукта (01.10.2026): annual | fixed; у fixed — годовой эквивалент для рынка
                 "rate_type": rate_res.get("rate_type") or "annual",
                 "rate_type_label": t("rate_type_" + (rate_res.get("rate_type") or "annual"), lang),
                 "annual_equiv_pct": rate_res.get("annual_equiv_pct"),
                 "min_source": _min_src_text(D, lang),
                 **(PV["rate_extra"] if PV else {})},
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
        # оценка заниженной ставки (01.10.2026); ставка не ниже минимума или её нет — available = false
        "below_min_assessment": bmv["json"],
        # минимальная ставка страховщика на дату акта и её источник (app/min_rates.py)
        "min_rate": _min_rate_view(D, lang),
        "cross_check": xv or {"available": False},
        "franchise": {"needed": bool(fr.get("needed")), "text": fr_text,
                      "grounds": [{"code": g["code"], "text": _text(g, lang)} for g in fr.get("grounds") or []],
                      **({"size": fr["size"]} if fr.get("size") else {}),
                      **_franchise_extra(fr, fr_how, fr_alts, lang)},
        "scenarios": scv["json"],
        # аналитика раздела 4 (30.09.2026): риски, факторы, чувствительность, состав тарифа, сценарии подробно,
        # удержание, балл, рынок и статистика с источниками, франшиза справочно, мероприятия, резюме
        "analytics": dict(anv["json"] or {}, exchange=EX["json"]),
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
        # шаблон анализа класса, по которому собран акт (справочник class_templates): версия, поля класса,
        # документы и статистика класса — на языке акта; акты до 30.09.2026 — без шаблона
        "template": ctpl.localize(D["template"], lang) if D.get("template") else None,
        # комплексный продукт по частям (30.09.2026); акты до этой даты и однопродуктовые — mode single
        "parts": PV["json"] if PV else {"mode": "single", "source": None, "confirmed": True, "items": [],
                                        "totals": None, "notes": []},
        **({"suggested_parts": PV["json"]["suggested_parts"]} if PV and not PV["json"]["confirmed"] else {}),
        "footer": t("footer", lang),
        "downloads": {"docx": f"/act/{meta['id']}.docx?lang={lang}", "pdf": f"/act/{meta['id']}.pdf?lang={lang}",
                      "scoring_pdf": f"/act/{meta['id']}/scoring.pdf?lang={lang}",
                      "scoring_png": f"/act/{meta['id']}/scoring.png?lang={lang}"},
        # отчёт кредитного бюро по заёмщику (01.10.2026); нет отчёта — available = false
        "borrower": bv["json"],
        # вилка ставки (01.10.2026): отметки, поправки региона и рынка с источниками, вывод одной фразой
        "rate_fork": dict(FV["json"], overview=FV.get("overview")),
        # факторы объекта по подгруппам класса (02.10.2026): множитель, применённые и незаполненные группы, вклад
        # каждого фактора в сумах, строки пояснения на языке акта; экспертно, calibrated = 0
        "factor_adjustment": FAV["json"],
    }
    # страховой скоринг объекта (01.10.2026): представление посчитанного акта; сбой показа не роняет акт
    try:
        out["scoring"] = asc.view(D, out, lang, meta, borrower=bv["scoring"])
    except Exception as e:
        print("акт: скоринг не показан:", type(e).__name__, e)
        out["scoring"] = {"available": False, "reason": "render_error", "calibrated": ae.CALIBRATED}
    return out


def _min_src_text(D: dict, lang: str) -> Optional[str]:
    """Источник минимальной ставки акта словами; акты до 01.10.2026 (без D["min_rate"]) — None."""
    mb = D.get("min_rate") or {}
    if not mb.get("insurer") or not mb.get("source"):
        return None
    return mrs.source_label({k: mb.get(k) for k in ("source", "effective_from", "note", "document_ref", "name")},
                            lang)


def _min_rate_view(D: dict, lang: str) -> dict:
    mb = D.get("min_rate")
    if not mb:
        return {"available": False, "reason": "old_act"}
    rt = mb.get("rate_type") or "annual"
    return {"available": mb.get("min_pct") is not None, "min_pct": mb.get("min_pct"), "rate_type": rt,
            "rate_type_label": t("rate_type_" + rt, lang), "insurer": bool(mb.get("insurer")),
            "source": mb.get("source"), "effective_from": mb.get("effective_from"), "note": mb.get("note"),
            "version_id": mb.get("version_id"), "source_text": _min_src_text(D, lang)}


def _bm_params(code: str, p: dict, lang: str) -> dict:
    out = {}
    for k, v in (p or {}).items():
        if v is None:
            out[k] = t("na", lang)
        elif k in ("req", "net", "market", "gap"):
            out[k] = pct(v, lang)
        elif k in ("share", "lim", "lr", "gap_rel") and code not in ("bm_market_ok", "bm_market_high"):
            v1 = round(float(v), 1)
            out[k] = tx.pct_fixed(v1, lang, 0 if v1 == int(v1) else 1)
        elif k in ("eml", "limit", "shortfall"):
            out[k] = money(v, lang)
        elif k in ("ratio", "lim"):
            out[k] = tx._num(float(v), lang, 2).rstrip("0").rstrip(",.") if float(v) != int(float(v)) \
                else str(int(float(v)))
        else:
            out[k] = v
    if code in ("bm_net_ok", "bm_net_below"):
        out["cal"] = "" if p.get("calibrated") else t("bm_cal_expert", lang)
    return out


def _bm_reason_text(r: dict, lang: str) -> str:
    code = r["code"]
    p = r.get("params") or {}
    if code == "bm_level":
        return t("bm_level_" + str(p.get("level")), lang)
    if code == "bm_gap" and p.get("rate_type") == "fixed":
        code = "bm_gap_fixed"
    return t(code, lang, **_bm_params(r["code"], p, lang))


def _below_min_view(D: dict, lang: str) -> dict:
    """Оценка заниженной ставки на языке акта: строки раздела 4, абзац раздела 5 и JSON below_min_assessment."""
    bm = D.get("below_min") or {}
    js = {"available": bool(bm.get("available")), "reason": bm.get("reason") if bm else "old_act",
          "requested_pct": bm.get("requested_pct"), "requested_source": bm.get("requested_source"),
          "requested_source_label": t("bm_src_" + bm["requested_source"], lang) if bm.get("requested_source") else None,
          "min_pct": bm.get("min_pct"), "calibrated": ae.CALIBRATED, "note": t("bm_note", lang)}
    if not js["available"]:
        return {"lines": [], "s5": None, "json": js}
    verdict = bm["verdict"]
    vlabel = t("bm_v_" + verdict, lang)
    reasons = [{"code": r["code"], "sign": r["sign"], "effect": r["effect"], "value": r.get("value"),
                "text": _bm_reason_text(r, lang)} for r in bm["reasons"]]
    conds = [{"code": c, "text": t(c, lang)} for c in bm.get("conditions") or []]
    min_src = _min_src_text(D, lang) or t("bm_min_insurer" if bm.get("min_insurer") else "bm_min_regulator", lang)
    head = t("bm_head", lang, req=pct(bm["requested_pct"], lang), src=js["requested_source_label"],
             min=pct(bm["min_pct"], lang), min_src=min_src, verdict=vlabel)
    parts = [head]
    if verdict == "not_allowed":
        why = [r["text"] for r in reasons if r["effect"] in ("blocks", "no_conditions")]
        parts.append(t("bm_why_no", lang, why="; ".join(why)))
    elif verdict == "allowed_with_conditions":
        why = [r["text"] for r in reasons if r["effect"] == "no_yes"]
        parts.append(t("bm_why_cond", lang, why="; ".join(why)))
        parts.append(t("bm_conds", lang, what="; ".join(c["text"] for c in conds)))
    else:
        parts.append(t("bm_why_yes", lang))
        parts.append(t("bm_conds", lang, what="; ".join(c["text"] for c in conds)))
    sf = t("bm_shortfall_fixed" if bm.get("rate_type") == "fixed" else "bm_shortfall", lang,
           days=bm.get("term_days"), sum=money(bm.get("shortfall"), lang))
    parts.append(sf)
    text = " ".join(parts)
    js.update(gap_pct=bm.get("gap_pct"), gap_rel_pct=bm.get("gap_rel_pct"), share_pct=bm.get("share_pct"),
              shortfall=bm.get("shortfall"), shortfall_text=money(bm.get("shortfall"), lang),
              term_days=bm.get("term_days"), rate_type=bm.get("rate_type"),
              requested_annual_pct=bm.get("requested_annual_pct"), verdict=verdict, verdict_label=vlabel,
              hard=bool(bm.get("hard")), reasons=reasons, conditions=conds, text=text, min_source_text=min_src,
              rule=bm.get("rule"))
    lines = [text]
    lines += [t("bm_line", lang, sign=t("bm_" + r["sign"], lang), text=r["text"]) for r in reasons]
    lines.append(js["note"])
    rate = ((D.get("premium_final") or {}).get("rate_pct"))
    s5 = t("bm_s5_" + verdict, lang, req=pct(bm["requested_pct"], lang), min=pct(bm["min_pct"], lang),
           rate=pct(rate, lang))
    return {"lines": lines, "s5": s5, "json": js}


def _borrower_view(b: Optional[dict], lang: str) -> dict:
    """Заёмщик по отчёту кредитного бюро: строки раздела 4, блок borrower ответа и строки для страницы скоринга."""
    if not b or not b.get("available"):
        return {"available": False, "lines": [], "json": {"available": False}, "scoring": None}
    f = b["fields"]
    ov, ac = f.get("overview") or {}, f.get("active") or {}
    NA = t("na", lang)
    num = lambda v: NA if v is None else str(int(v)) if float(v) == int(v) else str(v)   # noqa: E731
    mon = lambda v: NA if v is None else money(v, lang)                                     # noqa: E731
    src = tx.label(tx.CR_SOURCE_LABELS, "input" if b["source_kind"] == "input" else b["source"], lang)
    lines = [t("cb_date", lang, date=_ddmmyyyy(f["report_date"]), days=b["age_days"], src=src)
             if f.get("report_date") and b.get("age_days") is not None else t("cb_date_unknown", lang, src=src)]
    if f.get("subject_type") == "legal":
        rest = "".join(x for x in ((f" «{f['name']}»" if f.get("name") else ""),
                                   (", " + t("cb_inn", lang, v=f["inn"]) if f.get("inn") else ""),
                                   (", " + t("cb_oked", lang, v=f["oked"]) if f.get("oked") else "")))
        lines.append(t("cb_subject_legal", lang, rest=rest))
    elif f.get("subject_type") == "individual":
        lines.append(t("cb_subject_individual", lang))
    else:
        lines.append(t("cb_subject_unknown", lang))
    lines.append(t("cb_score", lang, score=num(f.get("score")), cls=f.get("score_class") or NA,
                   ver=f.get("score_version") or NA))
    lines.append(t("cb_overview", lang, a=num(ov.get("applications")), c=num(ov.get("contracts")),
                   u=num(ov.get("contingent")), q=num(ov.get("inquiries")), p=mon(ov.get("avg_monthly_payment"))))
    lines.append(t("cb_overdue", lang, n=num(ov.get("overdue_principal_count")),
                   days=num(ov.get("max_overdue_principal_days")), amount=mon(ov.get("max_overdue_principal_amount")),
                   idays=num(ov.get("max_overdue_interest_days")), itotal=mon(ov.get("overdue_interest_total"))))
    lines.append(t("cb_active", lang, n=num(ac.get("count")), debt=mon(ac.get("total_debt")), od=mon(ac.get("overdue")),
                   pay=mon(ac.get("monthly_payment"))))
    if ac.get("creditors"):
        lines.append(t("cb_creditors", lang, v="; ".join(ac["creditors"])))
    if b.get("edits"):
        lines.append(t("cb_edits", lang, what="; ".join(
            f"{tx.label(tx.CR_FIELD_LABELS, e['code'], lang)}: {_cr_value(e['code'], e['was'], lang)} → "
            f"{_cr_value(e['code'], e['now'], lang)}" for e in b["edits"])))
    if b.get("doc_missing"):
        lines.append(t("cb_doc_missing", lang))
    checks = [_check_text({"code": "c_" + c["code"], "params": c["params"]}, lang) for c in b.get("checks") or []]
    if not b.get("credit_product"):
        lines.append(t("cb_not_credit", lang))
    lines += checks
    lines += [t("cb_not_in_rate", lang), t("cb_keep", lang), t("cb_no_direct", lang)]
    rows = [{"code": "score", "label": t("cb_s_score", lang),
             "value": f"{num(f.get('score'))} / {f.get('score_class') or NA}"},
            {"code": "overdue", "label": t("cb_s_overdue", lang), "value": mon(ac.get("overdue"))},
            {"code": "max_overdue_days", "label": t("cb_s_max_overdue", lang),
             "value": num(ov.get("max_overdue_principal_days"))},
            {"code": "total_debt", "label": t("cb_s_debt", lang), "value": mon(ac.get("total_debt"))},
            {"code": "monthly_payment", "label": t("cb_s_payment", lang),
             "value": mon(ac.get("monthly_payment") if ac.get("monthly_payment") is not None
                          else ov.get("avg_monthly_payment"))},
            {"code": "report_date", "label": t("cb_s_date", lang), "value": _ddmmyyyy(f["report_date"])
             if f.get("report_date") else NA}]
    js = {"available": True, "fields": f, "source": b["source"], "source_kind": b["source_kind"],
          "source_label": src, "field_sources": b.get("field_sources") or {}, "edits": b.get("edits") or [],
          "doc_missing": bool(b.get("doc_missing")), "age_days": b.get("age_days"),
          "credit_product": bool(b.get("credit_product")), "checks": checks,
          "check_codes": [c["code"] for c in b.get("checks") or []], "lines": lines, "rows": rows,
          "in_risk_level": False, "in_rate": False,
          "note": t("cb_not_in_rate", lang) + " " + t("cb_keep", lang), "direct_note": t("cb_no_direct", lang),
          "keep_note": t("cb_keep", lang), "calibrated": ae.CALIBRATED}
    sc = {"available": True, "score": f.get("score"), "score_class": f.get("score_class"),
          "overdue": ac.get("overdue"), "max_overdue_principal_days": ov.get("max_overdue_principal_days"),
          "total_debt": ac.get("total_debt"), "rows": rows, "note": js["note"]} if b.get("credit_product") else None
    return {"available": True, "lines": lines, "json": js, "scoring": sc}


def _lower_first(s: str) -> str:
    """Подпись поля внутри фразы — со строчной: «Срок кредита, месяцев» → «срок кредита, месяцев»;
    сокращения (ИНН, PML) не трогаются."""
    s = str(s or "")
    return s[:1].lower() + s[1:] if len(s) > 1 and s[0].isupper() and s[1].islower() else s


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


def _exchange_view(D: dict, lang: str) -> dict:
    """Справка биржи УзРТСБ для раздела 3: строка, строка источника (ссылка обязательна) и блок для ответа.
    Акты до 02.10.2026 справки не имеют — available = false."""
    ex = D.get("exchange") or (D.get("analytics") or {}).get("exchange") or {"available": False, "items": []}
    items = [i for i in ex.get("items") or [] if i.get("url")] if ex.get("available") else []
    if not items:
        return {"row": None, "source_line": None, "json": dict(ex, available=False, text=None)}
    from . import uzex_sources as us
    parts = []
    for i in items:
        unit = (us.UNIT_LABELS.get(i["unit"]) or {}).get(tx.lang_of(lang)) or i["unit"]
        parts.append(t("ex_item", lang, group=us.group_label(i["group"], tx.lang_of(lang)),
                       price=money(i["median_unit_price"], lang), unit=unit, n=i["deals"],
                       date=_ddmmyyyy(i["last_date"])))
    text = t("ex_text", lang, items="; ".join(parts))
    url = ex.get("url") or items[0]["url"]
    line = t("ex_src", lang, url=url, days=ex.get("days") or 30)
    return {"row": _row(t("ex_label", lang), text, t("ex_note", lang)), "source_line": line,
            "json": dict(ex, text=text, source_line=line)}


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
    if sc.get("source") == "template":
        simple = sc.get("rule_simple") or {}
        how = [t("sc_how_source_tpl", lang, cls=sc.get("class_code"), rule=simple.get(lang) or simple.get("ru") or "")]
        if sc.get("rule_text"):
            how.append(t("sc_tpl_rule_text", lang, text=sc["rule_text"].get(lang) or sc["rule_text"].get("ru")))
        for c in sc.get("checks") or []:
            # площадь, урожайность и процент (урожай, 16у) — числа, а не суммы
            how.append(t(c["code"], lang, **{k: (_num4(v, lang) if k in ("area", "yield", "pct") else money(v, lang))
                                             if isinstance(v, (int, float)) and k != "by" else v
                                             for k, v in (c.get("params") or {}).items()}))
        js.update(source="template", rule_text=(sc.get("rule_text") or {}).get(lang),
                  checks=[{"code": c["code"], "params": c.get("params")} for c in sc.get("checks") or []])
    elif sc.get("source") == "parts":
        how = _parts_sc_how(sc, lang)
    else:
        how = [t("sc_how_source", lang, cls=sc.get("class_code"), rule=t("sc_rule_" + sc["rule"], lang))]
    if classic and sc.get("source") != "template":
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


def _np_src(src: Optional[dict], lang: str) -> tuple:
    """(строка «Источник: …», запись для плашки) для данных НАПП о претензиях и подразделениях."""
    if not src:
        return None, None
    file = ""
    if lang == "ru" and "(" in (src.get("title") or ""):
        file = " (" + src["title"].split("(", 1)[1]
        file = file if file.endswith(")") else file + ")"
    line = t("an_np_src", lang, sheets=src.get("sheets") or "", file=file, date=_date(src.get("as_of")),
             url=src.get("url") or "")
    title = t("an_np_src_title", lang, sheets=src.get("sheets") or "")
    return line, {"title": title, "url": src.get("url"), "source": src.get("domain") or "napp.uz",
                  "period": _date(src.get("as_of")), "fetched_at": None}


def _np_skip(B: dict, lang: str, sheet: str) -> str:
    """Почему показатель НАПП пропущен — на языке акта."""
    r = B.get("reason")
    if r in ("no_sheet", "no_table"):
        return t("an_np_no_sheet", lang, date=_date(B.get("date")) or t("na", lang), sheet=sheet)
    if r == "no_region_row":
        return t("an_np_no_region_row", lang, date=_date(B.get("date")), region=B.get("region_name") or "")
    if r == "no_contracts":
        return t("an_np_no_contracts", lang, date=_date(B.get("date")))
    if r == "no_company":
        return t("an_np_no_company", lang, date=_date(B.get("date")))
    return t("na", lang)


def _cap(x: str) -> str:
    """Первая буква — заглавная (фраза после точки)."""
    return x[:1].upper() + x[1:] if x else x


def _napp_items(NP: dict, lang: str, must: dict) -> list:
    """Строки раздела 4 по претензиям и подразделениям (НАПП) в форме analytics.stats.indicators."""
    if not NP.get("available"):
        return []
    # таблиц ещё нет (база до первой загрузки новых листов) — строк нет вовсе, без ложного «листа нет»
    NP = {k: (v if not (isinstance(v, dict) and v.get("reason") == "no_table") else {}) for k, v in NP.items()}
    lang = tx.lang_of(lang)
    out = []
    reg_name = region_label(must, lang) or ""

    def n2(x):
        return tx._num(float(x), lang, 2) if x is not None else t("na", lang)

    def p1(x):
        return tx.pct_fixed(x, lang, 1) if x is not None else t("na", lang)

    def m0(x):
        return money(x, lang) if x is not None else t("na", lang)

    cw = NP.get("claims_weight", 0.0)
    min_n = NP.get("branch_min_contracts", 200)

    def caveats(B):
        """Оговорки к строкам претензий на языке акта (доля города Ташкента — по срезу)."""
        return t("an_np_caveats", lang, text="; ".join(mpic.claims_caveats(lang, B.get("capital_share_pct"))))

    def item(iid, name, status, value_text, text, scope, B, vs=None, row_note=""):
        line, sx = _np_src(B.get("source"), lang)
        return {"id": iid, "name": name, "scope": scope, "status": status,
                "value": None, "value_text": value_text, "period": _date(B.get("date")) or None,
                "vs_country": vs, "used_in_score": False, "excluded_for_kind": False, "points": None,
                "text": text, "source_lines": [line] if line else [], "sources": [sx] if sx else [],
                "row_note": row_note or (_date(B.get("date")) or "—"), "napp": True, "calibrated": ae.CALIBRATED}

    # 1. претензии в регионе против республики (листы 3.5, 3.4, 3.2)
    RC = NP.get("region_claims") or {}
    reg, rep_ = RC.get("region") or {}, RC.get("republic") or {}
    months = RC.get("months") or "—"
    # строка справочная: в поправку ставки по умолчанию не входит (вес claims_freq = 0, замечание 01.10.2026)
    share = RC.get("capital_share_pct")
    if cw:
        ref = t("an_np_rc_in_fork", lang, w=tx._num(float(cw), lang, 1 if float(cw) != int(cw) else 0))
    else:
        ref = t("an_np_rc_ref", lang, share=tx.pct_fixed(share, lang, 0)) if share is not None \
            else t("an_np_rc_ref_na", lang)
    if RC.get("available"):
        text = t("an_np_rc_line", lang, f=n2(reg["per_1000"]), cf=n2(rep_["per_1000"]), r=p1(reg["refused_pct"]),
                 cr=p1(rep_["refused_pct"]), a=m0(reg["avg_payout"]), ca=m0(rep_["avg_payout"]),
                 n=tx._num(float(reg["received"]), lang, 0), date=_date(RC["date"]), m=months)
        it = item("napp_region_claims", t("an_np_rc_name", lang), "ok",
                  t("an_np_per1000", lang, f=n2(reg["per_1000"])), text + ". " + _cap(ref) + ". " + caveats(RC), "region",
                  RC, {"ratio": RC["ratio"], "diff_pct": RC["diff_pct"]},
                  f"{_date(RC['date'])}; {t('an_st_region', lang)}: {reg_name}; {ref}")
        it.update(reference_only=not cw, in_fork_weight=cw, note=ref,
                  caveats=mpic.claims_caveats(lang, RC.get("capital_share_pct")))
        out.append(it)
    elif rep_ and RC.get("reason") in ("no_region", "region_unknown"):
        text = t("an_np_rc_rep_line", lang, cf=n2(rep_["per_1000"]), cr=p1(rep_["refused_pct"]),
                 ca=m0(rep_["avg_payout"]), date=_date(RC["date"]), m=months)
        it = item("napp_region_claims", t("an_np_rc_name_rep", lang), "ok",
                  t("an_np_per1000", lang, f=n2(rep_["per_1000"])), text + ". " + caveats(RC), "republic", RC)
        it.update(reference_only=True, caveats=mpic.claims_caveats(lang, RC.get("capital_share_pct")))
        out.append(it)
    elif RC.get("reason"):
        why = _np_skip(RC, lang, "3.5")
        out.append(item("napp_region_claims", t("an_np_rc_name", lang), "no_data", why,
                        t("an_np_skip_line", lang, name=t("an_np_rc_name", lang), why=why), "region", RC))
    # 2. претензии: рынок в целом / INSON (листы 2.10, 2.7, 2.5)
    CC = NP.get("company_claims") or {}
    mk_, co = CC.get("market") or {}, CC.get("company") or {}
    if CC.get("available"):
        text = t("an_np_cc_line", lang, mn=tx._num(float(mk_["received"]), lang, 0), mf=n2(mk_["per_1000"]),
                 mr=p1(mk_["refused_pct"]), ma=m0(mk_["avg_payout"]), cn=tx._num(float(co["received"]), lang, 0),
                 cf=n2(co["per_1000"]), cr=p1(co["refused_pct"]), ca=m0(co["avg_payout"]), date=_date(CC["date"]),
                 m=CC.get("months") or "—")
        it = item("napp_company_claims", t("an_np_cc_name", lang), "ok",
                  t("an_np_cc_value", lang, cf=n2(co["per_1000"]), mf=n2(mk_["per_1000"])),
                  text + ". " + caveats(CC), "republic", CC)
        it.update(reference_only=True, caveats=mpic.claims_caveats(lang, CC.get("capital_share_pct")))
        out.append(it)
    elif CC.get("reason"):
        why = _np_skip(CC, lang, "2.10")
        out.append(item("napp_company_claims", t("an_np_cc_name", lang), "no_data", why,
                        t("an_np_skip_line", lang, name=t("an_np_cc_name", lang), why=why), "republic", CC))
    # 3. подразделения INSON в регионе (листы 2.12–2.14)
    BR = NP.get("branches") or {}
    if BR.get("available"):
        r, c = BR["region"], BR["company"]
        text = t("an_np_br_line", lang, lr=p1(r["loss_ratio_pct"]), clr=p1(c["loss_ratio_pct"]),
                 ap=m0(r["avg_premium"]), cap=m0(c["avg_premium"]), p=tx._num(float(r["premiums_mln"]), lang, 1),
                 n=tx._num(float(r["contracts"] or 0), lang, 0), date=_date(BR["date"]))
        small = r.get("contracts") is not None and min_n and float(r["contracts"]) < float(min_n)
        sm = t("an_np_br_small", lang, n=tx._num(float(r["contracts"] or 0), lang, 0),
               min=tx._num(float(min_n), lang, 0)) if small else ""
        it = item("napp_branches", t("an_np_br_name", lang), "ok",
                  t("an_np_br_value", lang, lr=p1(r["loss_ratio_pct"]), clr=p1(c["loss_ratio_pct"])),
                  text + (". " + _cap(sm) if sm else ""), "region", BR, None,
                  f"{_date(BR['date'])}; {t('an_st_region', lang)}: {reg_name}" + (f"; {sm}" if sm else ""))
        it.update(small_base=bool(small), min_contracts=min_n)
        out.append(it)
    elif BR.get("reason") == "not_listed":
        why = t("an_np_br_not_listed_reg" if BR.get("company") else "an_np_br_not_listed", lang, region=reg_name,
                company=BR.get("company_label") or "INSON")
        out.append(item("napp_branches", t("an_np_br_name", lang), "no_data", why,
                        t("an_np_skip_line", lang, name=t("an_np_br_name", lang), why=why), "region", BR))
    elif BR.get("reason") == "no_sheet":
        why = _np_skip(BR, lang, "2.12–2.14")
        out.append(item("napp_branches", t("an_np_br_name", lang), "no_data", why,
                        t("an_np_skip_line", lang, name=t("an_np_br_name", lang), why=why), "region", BR))
    return out


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
        name = (it.get("labels") or {}).get(lang) or _peril_label(it["code"], lang, it.get("name_ru"),
                                                                  it.get("class_code"))
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
    if R.get("source") == "template" and ritems:
        # риски и доли — из шаблона класса (справочник class_templates): в perils класса нет
        tnote = ((((D.get("template") or {}).get("risks") or {}).get("note")) or {})
        rnotes.append(t("an_r_template", lang, cls=cls, total=tx.pct_fixed(R.get("total_pct") or 0, lang, 1),
                        note=tnote.get(lang) or tnote.get("ru") or "").strip())
    elif not R.get("whole_class") and ritems:
        # класс без разбивки на риски — про доли справочника не пишем: там одна строка «весь класс»
        rnotes.append(t("an_r_shares_round" if R.get("rounding") else "an_r_shares", lang,
                        total=tx.pct_fixed(R.get("total_pct") or 0, lang, 1)))
    js["risks"] = {"available": bool(ritems), "items": ritems, "total_pct": R.get("total_pct"),
                   "whole_class": bool(R.get("whole_class")), "covered_by_contract": covered, "notes": rnotes,
                   "source": R.get("source") or ("class" if R.get("whole_class") else "perils"),
                   "calibrated": ae.CALIBRATED}
    if R.get("source") == "template":
        js["risks"]["label"] = t("an_r_tpl_label", lang)
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
        # часть комплексного продукта: минимум класса из текста тарифа продукта — это не «ставка продукта»
        cm_src = ((D.get("rate") or {}).get("class_min") or {}).get("source")
        by_text = cm_src in ("rate_text", "rate_text_common")
        cm_kw = {"cls": (D.get("must") or {}).get("class_code") or T.get("class_code"), "code": T.get("product_code")}
        tr("min", t("an_t_min_class" if by_text else "an_t_min", lang, **cm_kw),
           pct(T["min_pct"], lang) if T.get("min_pct") is not None else NA)
        if T.get("act_mode") == "tariff":
            if T.get("policy_rate_pct") is not None:
                tr("policy", t("an_t_policy_class" if by_text else "an_t_policy", lang, **cm_kw),
                   pct(T["policy_rate_pct"], lang))
            else:
                tr("act_base", t("an_t_base_act", lang), pct(T.get("act_base_pct"), lang))
            act_note = t("an_t_act_min_class" if by_text else "an_t_act_min", lang, **cm_kw) \
                if T.get("min_applied") else ""
            if rate_res.get("fork_applied"):         # вилка ставки, режим apply: ставка акта уже с поправками
                act_note = "; ".join(x for x in (act_note, t("rf_an_applied", lang)) if x)
            tr("act", t("an_t_act", lang, adj="+" + pct(T.get("adj_pct") or 0, lang)), pct(T["act_rate_pct"], lang),
               act_note)
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
                elif p["peril"] == "template":
                    w = p.get("what") or {}
                    how.append(t("an_sc_tpl", lang, what=w.get(lang) or w.get("ru") or it["name"],
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
            if it.get("source") == "template":
                pass                             # простое правило шаблона: защиты в правиле нет
            elif it["rule"] != "vehicle":
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
        if mk.get("class_rows"):
            # рядом с пакетом — одиночные строки классов продукта (правило проекта № 5: ставка по каждому классу)
            for r in mk["class_rows"]:
                if not r.get("available"):
                    mrows.append([t("an_m_class_label", lang, cls=r["class_code"]), NA, "—"])
                    continue
                val = t("an_m_class_value", lang, rate=pct(r.get("rate_pct"), lang),
                        lr=tx.pct_fixed(r["loss_ratio_pct"], lang, 1) if r.get("loss_ratio_pct") is not None else NA)
                if r.get("rate_full_year_pct") is not None:
                    val += t("an_m_class_fy", lang, year=_fy_year(r.get("full_year_period")) or "—",
                             rate=pct(r["rate_full_year_pct"], lang),
                             lr=tx.pct_fixed(r.get("loss_ratio_full_year_pct"), lang, 1)
                             if r.get("loss_ratio_full_year_pct") is not None else NA)
                mrows.append([t("an_m_class_label", lang, cls=r["class_code"]), val, _date(r.get("date"))])
            cr_line = _class_rows_text(mk["class_rows"], lang)
            if cr_line:
                mitems.append(cr_line[:1].upper() + cr_line[1:] + ".")
        if mk.get("pack_choice"):
            # комплексный продукт: пакет НАПП с классами продукта (точный / ближайший) или строка класса
            row_note = _pack_choice_text(mk["pack_choice"], lang, cls, "an_m_prod_")
        elif mk.get("pack"):
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
    # претензии региона, рынок / INSON, подразделения INSON в регионе (НАПП, 01.10.2026): тот же формат строки
    # показателя, что у stat.uz (экран показывает их в analytics.stats.indicators со своей плашкой источника)
    for it in _napp_items(A.get("napp") or {}, lang, must):
        sitems2.append(it)
        mitems.append(it["text"])
        mrows.append([it["name"], it["value_text"], it["row_note"]])
        for line_s in it["source_lines"]:
            if line_s not in msrc:
                msrc.append(line_s)
        for x in it["sources"]:
            src_add(x["title"], x["url"], x["source"], x["period"])
    js["market"] = {"available": bool(mk.get("available")), "rate_pct": mk.get("rate_pct"),
                    "rate_date": mk.get("rate_date"), "months": mk.get("months"),
                    "loss_ratio_pct": mk.get("loss_ratio_pct"), "rate_full_year_pct": mk.get("rate_full_year_pct"),
                    "loss_ratio_full_year_pct": mk.get("loss_ratio_full_year_pct"), "full_year": mk.get("full_year"),
                    "pack": bool(mk.get("pack")), "row_key": mk.get("row_key"), "row_note": row_note,
                    "pack_choice": mk.get("pack_choice"), "pack_classes": list(mk.get("pack_classes") or []),
                    "class_rows": list(mk.get("class_rows") or []),
                    # подпись строки на экране: у комплексного продукта — какой пакет взят и почему
                    "pack_label": (row_note if mk.get("pack_choice") else
                                   (t("an_m_pack_label", lang, pack=_pack_text(mk, lang)) if mk.get("pack") else None)),
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
    if it.get("what_text"):                  # простое правило шаблона класса: подпись из шаблона на языке акта
        return it["what_text"].get(lang) or it["what_text"].get("ru") or ""
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
    if a["code"] in ("as_tpl_limit_case_over", "as_tpl_limit_aggregate_over"):
        p = {k: money(v, lang) for k, v in p.items()}
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
                  "cross_differs": (D.get("cross_check") or {}).get("differs", 0),
                  # скоринг и отчёт бюро — только класс и коды проверок: ни названий, ни ИНН, ни сумм
                  "scoring": (out.get("scoring") or {}).get("class_code"),
                  "borrower": bool(D.get("borrower")),
                  "borrower_checks": [c["code"] for c in (D.get("borrower") or {}).get("checks") or []]})
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
    patch = body.get("settings") if isinstance(body.get("settings"), dict) else body
    patch = {k: v for k, v in (patch or {}).items() if k != "_source"}
    with db.tx() as con:
        ensure_tables(con)
        # частичная правка сливается с действующей версией (глубоко): прежние правки администратора не сбрасываются
        new = ae.deep_merge(current_custom(con), patch)
        errs = ae.check_settings(new)
        if errs:
            return JSONResponse({"ok": False, "errors": errs}, status_code=422)
        cur = con.execute("INSERT INTO act_settings (created_at, created_by, settings_json, calibrated, note) "
                          "VALUES (?,?,?,?,?)", (db.now(), user.get("login"), json.dumps(new, ensure_ascii=False),
                                                 0, str(body.get("note") or "")[:300]))
        db.audit(con, user.get("login") or "админ", "акт: настройки изменены", f"act_settings:{cur.lastrowid}",
                 {"keys": sorted(patch)})
        return {"ok": True, "id": cur.lastrowid, "settings": load_settings(con)}


# --------------------------------------------------------------------------- #
#  Шаблоны анализа по классам (справочник class_templates, приложение А)
#  Пути объявлены раньше /act/{aid}: иначе «templates» принялось бы за номер акта.
# --------------------------------------------------------------------------- #

@router.get("/act/templates")
def act_templates_list(request: Request, lang: str = ""):
    """Все шаблоны кратко (файл 1.3.0: классы общего страхования 1–18, варианты 13з и 16у): версия, признак варианта
    (variant), название, риски с долями, правило сценария, поля; pending_file_version — новая версия файла ждёт."""
    lg = tx.lang_of(_lang(request, lang))
    with db.tx() as con:
        rows = ctpl.all_current(con)
        try:
            meta = ctpl.load_file()
        except (OSError, ValueError):
            meta = {}
        items = [ctpl.view(r, lg, con, full=False) for r in rows]
        return {"ok": True, "lang": lg, "file_version": meta.get("version"), "file_date": meta.get("date"),
                "aliases": meta.get("aliases") or {}, "calibrated": ctpl.CALIBRATED,
                "classification": ctpl.localize(meta.get("classification") or {}, lg),
                "counts": {"всего": len(items), "классов": sum(1 for x in items if not x["variant"]),
                           "вариантов": sum(1 for x in items if x["variant"])},
                "common_must": ctpl.localize(meta.get("common_must") or [], lg),
                "templates": items}


@router.get("/act/templates/{class_code}/history")
def act_templates_history(request: Request, class_code: str):
    """Все версии шаблона класса (старые не удаляются): кто, когда, источник (file | admin)."""
    admin = (_user(request) or {}).get("role") == ADMIN
    with db.tx() as con:
        if not ctpl.current(con, class_code):
            return JSONResponse({"ok": False, "detail": "нет шаблона для класса " + class_code}, status_code=404)
        hist = ctpl.history(con, class_code)
        if not admin:                        # кто правил — видит только администратор
            for h in hist:
                h["updated_by"] = "администратор" if h["source"] == "admin" else h["updated_by"]
        return {"ok": True, "class_code": ctpl.base_class(class_code), "history": hist}


@router.get("/act/templates/{class_code}")
def act_templates_get(request: Request, class_code: str, lang: str = "", raw: int = 0):
    """Шаблон класса на языке lang (подписи ru/uz/en) с документами из checklists и рисками perils (классы 8, 9).
    raw=1 — исходный JSON со всеми тремя языками (для правки администратором)."""
    lg = tx.lang_of(_lang(request, lang))
    with db.tx() as con:
        row = ctpl.current(con, class_code)
        if not row:
            return JSONResponse({"ok": False, "detail": "нет шаблона для класса " + class_code}, status_code=404)
        out = ctpl.view(row, lg, con)
        if raw:
            out["raw"] = row["template"]
        return {"ok": True, **out}


@router.put("/act/templates/{class_code}")
def act_templates_put(request: Request, class_code: str, body: dict = Body(...)):
    """Новая версия шаблона класса (история сохраняется). Только администратор. Проверка структуры: доли рисков
    100 ± 0,5, коды оговорок и мероприятий существуют, обязательных полей не больше четырёх — иначе 422."""
    user = _user(request)
    if (user or {}).get("role") != ADMIN:
        return JSONResponse({"detail": "нужны права администратора"}, status_code=403)
    tpl = body.get("template") if isinstance(body, dict) and isinstance(body.get("template"), dict) else body
    with db.tx() as con:
        if not ctpl.current(con, class_code):
            return JSONResponse({"ok": False, "detail": "нет шаблона для класса " + class_code}, status_code=404)
        errs = ctpl.validate(tpl, class_code, con)
        if errs:
            return JSONResponse({"ok": False, "errors": errs}, status_code=422)
        row = ctpl.save(con, class_code, tpl, user.get("login") or ADMIN, (body or {}).get("note") or "")
        return {"ok": True, **ctpl.view(row, tx.lang_of(_lang(request, (body or {}).get("lang"))), con)}


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


@router.get("/act/{aid}/scoring.pdf")
def act_scoring_pdf(request: Request, aid: str, lang: str = ""):
    """Только страница «Страховой скоринг объекта» (права как у акта: владелец или администратор)."""
    got = _load_act(request, aid)
    if not got:
        return _fail(request, t("not_found", _lang(request, lang)), 404)
    D, meta, row = got
    out = render(D, _lang(request, lang or row["lang"]), meta)
    if not (out.get("scoring") or {}).get("available"):
        return _fail(request, t("not_found", out["lang"]), 404)
    return Response(content=build_pdf(out, scoring_only=True), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="scoring_{meta["number"]}.pdf"'})


@router.get("/act/{aid}/scoring.png")
def act_scoring_png(request: Request, aid: str, lang: str = ""):
    """Картинка шкалы скоринга с плашкой класса (PNG; права как у акта)."""
    got = _load_act(request, aid)
    if not got:
        return _fail(request, t("not_found", _lang(request, lang)), 404)
    D, meta, row = got
    out = render(D, _lang(request, lang or row["lang"]), meta)
    if not (out.get("scoring") or {}).get("available"):
        return _fail(request, t("not_found", out["lang"]), 404)
    return Response(content=scoring_png(out), media_type="image/png",
                    headers={"Content-Disposition": f'inline; filename="scoring_{meta["number"]}.png"'})


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

def scoring_png(act: dict) -> bytes:
    """Картинка шкалы скоринга с плашкой класса (тот же код рисования, что у страницы PDF)."""
    regular, bold = _fonts()
    fit = lambda s, font: "".join(ch if ord(ch) < 128 or font.has_glyph(ord(ch)) else GLYPH_FALLBACK.get(ch, "?")  # noqa
                                  for ch in str(s))
    return asc.gauge_png(act["scoring"], regular, bold, fit)


def build_docx(act: dict) -> bytes:
    from .docx_lite import TEXT_WIDTH, Docx
    lang = act["lang"]
    doc = Docx(lang={"ru": "ru-RU", "uz": "uz-Latn-UZ", "en": "en-GB"}[lang])
    if (act.get("scoring") or {}).get("available"):
        # первая секция — страховой скоринг объекта (01.10.2026), шкала — картинкой PNG; затем прежний акт
        asc.docx_section(doc, act["scoring"], scoring_png(act))
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


def build_pdf(act: dict, scoring_only: bool = False) -> bytes:
    """Акт в PDF: первая страница — страховой скоринг объекта (01.10.2026), затем прежний акт из пяти разделов.
    scoring_only — только страница скоринга (GET /act/{id}/scoring.pdf)."""
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
    if (act.get("scoring") or {}).get("available"):
        asc.draw_page(pdf.page, act["scoring"], pdf.regular, pdf.bold, pdf._fit, MARGIN, A4_H - MARGIN - 4)
        if scoring_only:
            return pdf.finish()
        pdf.new_page()
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


# ================================================================================================
#  Факторы объекта по подгруппам класса (02.10.2026): показ на языке акта
# ================================================================================================

def _fa_one(fa: dict, lang: str) -> dict:
    """
    Факторы объекта одного класса (act_engine.factor_adjust с effect) на языке акта: {"json", "lines", "row",
    "s5", "unfilled"}. Строки — построчно: база, каждый фактор с коэффициентом, ставкой после него и вкладом в премию
    в сумах, границы множителя и минимум (если сработали), итог, режим, что уточнить.
    """
    L = lambda x: ctpl.localize(x, lang) if x is not None else None  # noqa: E731
    eff = fa.get("effect") or {}
    steps = {x["group"]: x for x in eff.get("steps") or [] if x.get("kind") == "factor"}
    applied, lines = [], [t("fa_head", lang, n=len(fa.get("applied") or []), total=fa.get("groups") or 0)]
    stat_rows = []
    if eff.get("available"):
        lines.append(t("fa_base_" + (eff.get("base") or "act"), lang, rate=pct(eff["base_pct"], lang)))
    for a in fa.get("applied") or []:
        stp = steps.get(a["group"]) if eff.get("available") else None
        g, o, note = L(a.get("group_label")) or a["group"], L(a.get("label")) or a["option"], L(a.get("note")) or ""
        kw = {"group": g, "option": o, "coef": _mult(a["coef"], lang), "note": note}
        lines.append(t("fa_line_sum", lang, rate=pct(stp["rate_pct"], lang), delta=_smoney(stp["premium_delta"], lang),
                       **kw) if stp else t("fa_line", lang, **kw))
        item = {"group": a["group"], "group_label": g, "option": a["option"], "label": o, "coef": a["coef"],
                "note": note, "rate_pct": stp["rate_pct"] if stp else None,
                "premium_delta": stp["premium_delta"] if stp else None}
        if a.get("stat_ref"):
            # фон региона (stat.uz) к фактору: блок stat из данных акта (на дату формирования), строка explain и
            # строка раздела 4; акт до 02.10.2026 без блока — available = false, reason = old_act
            sb = a.get("stat") or {"available": False, "reason": "old_act",
                                   "reason_text": "акт сформирован до подключения фона stat.uz",
                                   "note": ae.STAT_NOTE, "calibrated": ae.CALIBRATED}
            item["stat"] = sb
            sl = _fa_stat_line(sb, g, lang)
            lines.append(sl)
            stat_rows.append((t("fa_stat_row", lang, group=g), sl))
        applied.append(item)
    for x in eff.get("steps") or []:
        if x.get("kind") == "bound":
            lo, hi = fa.get("bounds") or [None, None]
            lines.append(t("fa_bound", lang, raw=_mult(fa.get("raw_product") or 0, lang), lo=_mult(lo, lang),
                           hi=_mult(hi, lang), mult=_mult(fa["product"], lang), rate=pct(x["rate_pct"], lang),
                           delta=_smoney(x["premium_delta"], lang)))
        elif x.get("kind") == "min":
            lines.append(t("fa_min", lang, min=pct(x["rate_pct"], lang), delta=_smoney(x["premium_delta"], lang)))
    reason = eff.get("reason")
    if eff.get("available"):
        lines.append(t("fa_total", lang, mult=_mult(fa["product"], lang), base=pct(eff["base_pct"], lang),
                       rate=pct(eff["rate_pct"], lang), p0=money(eff["base_premium"], lang),
                       p1=money(eff["premium"], lang), delta=_smoney(eff["delta_premium"], lang)))
    elif reason in ("statutory", "no_rate"):
        lines.append(t("fa_" + reason, lang))
    elif not fa.get("applied"):
        lines.append(t("fa_none", lang))
    mode = fa.get("mode") or "reference"
    if reason != "statutory":
        lines.append(t("fa_mode_" + mode, lang))
    unfilled = [{"group": u["group"], "label": L(u.get("label")) or u["group"]} for u in fa.get("unfilled") or []]
    if unfilled:
        lines.append(t("fa_unfilled", lang, items=", ".join(u["label"] for u in unfilled)))
    # строка раздела 4: факторы, множитель, режим
    if applied:
        value = t("fa_row_value", lang, items="; ".join(f"{a['label']} × {_mult(a['coef'], lang)}" for a in applied),
                  mult=_mult(fa["product"], lang))
    else:
        value = t("fa_row_none", lang)
    if reason in ("statutory", "no_rate"):
        note = t("fa_row_note_" + reason, lang)
    elif eff.get("available") and eff.get("applied_to_act"):
        note = t("fa_row_note_apply", lang)
    elif eff.get("available"):
        note = t("fa_row_note_reference", lang, rate=pct(eff["rate_pct"], lang), premium=money(eff["premium"], lang))
    else:
        note = None
    s5 = []
    if eff.get("available"):
        s5.append(t("fa_s5_apply", lang, mult=_mult(fa["product"], lang)) if eff.get("applied_to_act")
                  else t("fa_s5_reference", lang, rate=pct(eff["rate_pct"], lang), premium=money(eff["premium"], lang)))
    js = {"available": True, "mode": mode, "product": fa.get("product"), "raw_product": fa.get("raw_product"),
          "clamped": fa.get("clamped"), "bounds": fa.get("bounds"), "groups": fa.get("groups"), "applied": applied,
          "unfilled": unfilled,
          "effect": {k: eff.get(k) for k in ("available", "reason", "applied_to_act", "base", "base_pct",
                                             "base_premium", "rate_pct", "premium", "delta_premium", "floored",
                                             "min_pct", "rate_type", "term_days", "steps")},
          "explain": lines, "calibrated": ae.CALIBRATED}
    return {"json": js, "lines": lines, "row": (value, note), "s5": s5, "unfilled": [u["label"] for u in unfilled],
            "stat_rows": stat_rows}


def _fa_stat_line(sb: dict, group: str, lang: str) -> str:
    """Строка фона региона к фактору: доля материала стен в жилищном фонде или показатель (газ) со ссылкой."""
    L = lambda x: ctpl.localize(x, lang) if x is not None else None  # noqa: E731
    if not sb.get("available"):
        return t("fa_stat_none", lang, group=group, reason=sb.get("reason_text") or sb.get("reason") or "—")
    what = "; ".join(x for x in (L(z) for z in sb.get("labels") or []) if x) or sb.get("name") or ""
    kw = {"region": sb.get("region_name") or sb.get("region") or "", "period": sb.get("period"), "what": what,
          "share": tx.pct_fixed(sb.get("share_pct"), lang, 1), "url": sb.get("url") or ""}
    return t("fa_stat_walls" if sb.get("kind") == "walls_share" else "fa_stat_value", lang, **kw)


def _factor_view(D: dict, lang: str) -> dict:
    """
    Блок factor_adjustment ответа, строки раздела 4, фраза раздела 5 и перечень «уточнить». Акты до 02.10.2026 —
    без блока (available = false, reason = old_act); шаблон без групп факторов — пусто.
    """
    FA = D.get("factor_adjustment")
    empty = {"json": {"available": False, "reason": "old_act", "calibrated": ae.CALIBRATED}, "rows": [], "lines": [],
             "s5": [], "unfilled": []}
    if not FA:
        return empty
    if FA.get("by_parts"):
        parts, rows, lines, s5, unf = [], [], [], [], []
        for pf in FA.get("parts") or []:
            if not pf.get("groups"):
                continue
            one = _fa_one(pf, lang)
            n, cls = pf.get("index"), pf.get("class_code")
            parts.append(dict(one["json"], index=n, class_code=cls))
            rows.append(_row(t("fa_row_part", lang, n=n), one["row"][0], one["row"][1]))
            rows += [_row(t("fa_part", lang, n=n, cls=cls, text=lb), v) for lb, v in one["stat_rows"]]
            lines += [t("fa_part", lang, n=n, cls=cls, text=x) for x in one["lines"]]
            s5 += [t("fa_part", lang, n=n, cls=cls, text=x) for x in one["s5"]]
            unf += [t("fa_part", lang, n=n, cls=cls, text=x) for x in one["unfilled"]]
        return {"json": {"available": bool(parts), "by_parts": True, "mode": FA.get("mode"), "parts": parts,
                         "explain": lines, "calibrated": ae.CALIBRATED},
                "rows": rows, "lines": lines, "s5": s5, "unfilled": unf}
    if not FA.get("groups"):
        return dict(empty, json={"available": False, "reason": "no_groups", "mode": FA.get("mode"),
                                 "calibrated": ae.CALIBRATED})
    one = _fa_one(FA, lang)
    return {"json": one["json"], "rows": [_row(t("fa_row", lang), one["row"][0], one["row"][1])] +
            [_row(lb, v) for lb, v in one["stat_rows"]],
            "lines": one["lines"], "s5": one["s5"], "unfilled": one["unfilled"]}
