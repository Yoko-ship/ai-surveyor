"""
Сюрвейерский акт предстрахового осмотра — лёгкая версия (ТЗ 2.0 от 29.09.2026).

Сотрудник загружает фото объекта и снимки документов, вводит четыре поля и получает акт из пяти разделов
с советами. Парсинга нет: снимки читает языковая модель как картинку (одним запросом), тариф считает
простое правило (app/act_engine.py), текст акта собирает сервер по шаблонам (app/act_texts.py) — цифры
в тексте всегда совпадают с расчётом. Модель нужна только для распознавания фото; без неё акт
формируется целиком (литературная связка разделов моделью убрана 29.09.2026 — решение и списки
всегда из шаблонов).

Адреса (гостям открыты, как /chat/*; лимит актов — app/guest.py через app/guard.py, лимит фото гостя
по числу файлов и общий лимит распознаваний на сервер — здесь, настройки limits):
  POST /act/photos            — фото и снимки документов (multipart: files[], lang, class_code, product_code);
                                Content-Length проверяется до чтения тела (413/411)
  POST /act/make              — акт по четырём полям + необязательным + распознанному (в сеть не ходит)
  GET  /act/{id}              — акт (JSON), ?lang=ru|uz|en — тот же акт на другом языке
  GET  /act/{id}.docx, .pdf   — выгрузка
  POST /act/{id}/send         — отправить акт файлом в чат с ботом (Telegram), только владельцу
  GET  /act/settings          — пороги лёгкого движка; PUT /act/settings — только администратор

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

from . import act_engine as ae
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

FMT_MIME = {"jpg": "image/jpeg", "png": "image/png", "pdf": "application/pdf"}
MODEL_SOURCES = ("photo", "plate", "document", "marking")
CONDITIONS = ("new", "good", "worn", "damaged")
CLASS_HINTS = ("special_machinery", "vehicle", "building", "equipment", "cargo", "other")
HINT_CLASSES = {"special_machinery": ["3"], "vehicle": ["3"], "building": ["8", "9"], "equipment": ["8", "9"],
                "cargo": ["7"], "other": []}
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
        try:
            doc = pymupdf.open(stream=blob, filetype="pdf")
            try:
                if doc.needs_pass:
                    return t("ph_pdf_bad", lang)
                pages = doc.page_count
            finally:
                doc.close()
        except Exception:
            return t("ph_pdf_bad", lang)
        if pages < 1:
            return t("ph_pdf_bad", lang)
        if pages > int(limits["pdf_max_pages"]):
            return t("ph_pdf_pages", lang, n=int(limits["pdf_max_pages"]))
        return None
    return t("ph_format", lang)


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
    "значения с таблички и из документа переписывай в точности, со всеми знаками и единицами.")

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
    '"damages": [{"what": "что повреждено", "where": "где", "file": 1}]}')

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
    "document — документ, marking — надпись или маркировка на кузове, стреле, двери, photo — сам вид объекта.")

LANG_NAME = {"ru": "русском", "uz": "узбекском (латиница)", "en": "английском"}


def model_prompt(n: int, lang: str) -> str:
    return (f"приложено файлов: {n}, они пронумерованы по порядку от 1 до {n}. "
            f"для каждого файла укажи ракурс (view). {FIELD_HINTS} "
            f"описания (object_type, location, damages, note, document_kind) пиши на {LANG_NAME[lang]} языке "
            f"строчными буквами; марки, модели, номера и единицы — как написано на объекте. "
            f"видимые повреждения перечисли в damages; если повреждений не видно — пустой список. "
            f"схема ответа: {SCHEMA_HINT}")


def _placeholders(s: str) -> set:
    return set(re.findall(r"\[[А-ЯЁ\- ]+\]", s or ""))


# отчество или «Фамилия И. О.» — внутри названия компании это уже человек
_PATRONYMIC = re.compile(r"\b[А-ЯЁ][а-яё]+(?:ович|евич|ьич|овна|евна|ична|инична)\b|\b(?:o[ʻ'`‘]?g[ʻ'`‘]?li|qizi)\b",
                         re.I)


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


def pd_like(key: str, value: str) -> bool:
    """
    Похоже ли значение на персональные данные (llm.has_pd). Два известных ложных срабатывания снимаются:
    изготовитель/марка, если вся строка — название компании (_company_only), и девятизначное число
    в номере или модели агрегата, если в значении есть буквы (чистые 9 цифр — возможный ИНН или телефон).
    """
    value = str(value or "")
    if not llm.has_pd(value):
        return False
    found = _placeholders(llm.mask_pd(value)) - _placeholders(value)
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


def parse_model(text: str, n: int) -> Optional[dict]:
    """Ответ модели → проверенная структура. Не JSON или не та схема — None (честный отказ, не догадка)."""
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
        if dk and not pd_like("document_kind", dk):
            kinds[i] = dk
    dropped = 0
    fields, seen = [], set()
    for f in (data.get("fields") or [])[:120]:
        if not isinstance(f, dict):
            continue
        key = str(f.get("key") or "").strip()
        val = _s(f.get("value"), 120)
        if key not in ae.FIELD_KEYS or not val:
            continue
        if pd_like(key, val):
            dropped += 1
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
    return {"views": views, "document_kinds": kinds, "fields": fields, "damages": damages,
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


def recognize(saved: list, lang: str, limits: Optional[dict] = None) -> dict:
    """
    Одно обращение к модели со всеми снимками. saved — [{"blob", "fmt"}] в порядке загрузки.
    Одна попытка, таймаут запроса limits.ai_timeout_sec, общий срок limits.ai_deadline_sec: не уложились —
    ok=False с честной причиной (фото остаются, акт формируется).
    Возвращает {"ok", "reason", "sent": [индексы saved], "not_sent": [...], ...поля parse_model}.
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
    if not AI_CALLS.take("server", 1, int(lim["ai_calls_per_hour"]))["ok"]:
        return {"ok": False, "reason": t("ai_busy", lang, n=int(lim["ai_calls_per_hour"])), "sent": [],
                "not_sent": []}
    deadline = float(lim["ai_deadline_sec"])
    fut = _AI_POOL.submit(llm.chat_raw, "акт: распознавание фото",
                          [{"role": "system", "content": SYSTEM_PROMPT},
                           {"role": "user", "content": model_prompt(len(payload), lang)}],
                          max_tokens=4096, temperature=0.1, files=payload,
                          timeout=float(lim["ai_timeout_sec"]), retries=0)
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
    parsed = parse_model(res["text"], len(payload))
    if parsed is None:
        return {"ok": False, "reason": t("ph_bad_json", lang), "sent": sent, "not_sent": not_sent}
    return {"ok": True, "reason": None, "sent": sent, "not_sent": not_sent, **parsed}


# --------------------------------------------------------------------------- #
#  Распознанное: подписи и выбор значения
# --------------------------------------------------------------------------- #

def recognized_view(items: list, lang: str, file_ids: Optional[dict] = None) -> list:
    """Распознанное для экрана: подпись поля, источник словами и пометка «проверьте»."""
    out = []
    for r in items:
        out.append({"key": r["key"], "label": tx.label(tx.FIELD_LABELS, r["key"], lang), "value": r["value"],
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
            if size > MAX_BODY:
                return _fail(request, t("ph_body_too_big", lang, mb=MAX_BODY // (1024 * 1024)), 413)
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
        limits = load_settings(con)["limits"]
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
        return _photos(request, user, owner, lang, files, class_code, product_code, limits, sid, folder)
    except Exception:
        # запись в базу или распознавание упали — папка с фото не должна остаться сиротой
        shutil.rmtree(folder, ignore_errors=True)
        raise


router.add_api_route("/act/photos", act_photos, methods=["POST"], route_class_override=_BodyLimitRoute)


def _photos(request, user, owner, lang, files, class_code, product_code, limits, sid, folder):
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
        err = check_content(blob, fmt, limits, lang) if fmt else t("ph_format", lang)
        if err:
            rejected.append({"index": i, "name": name, "error": err})
            continue
        folder.mkdir(parents=True, exist_ok=True)
        fid = f"f{len(saved) + 1}"
        path = folder / f"{fid}.{fmt}"
        path.write_bytes(blob)
        saved.append({"id": fid, "index": i, "name": name, "orig_name": orig, "fmt": fmt, "mime": FMT_MIME[fmt],
                      "size": len(blob), "path": db.stored_path(path), "blob": blob})
    if not saved:
        shutil.rmtree(folder, ignore_errors=True)
        return _fail(request, t("ph_none", lang), 422, rejected=rejected, ai=False,
                     warning=t("warn_pd", lang))

    rec = recognize(saved, lang, limits)
    sent = rec.get("sent") or []
    # номер файла в запросе к модели → id загруженного файла
    model_to_id = {k + 1: saved[i]["id"] for k, i in enumerate(sent)}
    views = {model_to_id[k]: v for k, v in (rec.get("views") or {}).items() if k in model_to_id}
    doc_kinds = {model_to_id[k]: v for k, v in (rec.get("document_kinds") or {}).items() if k in model_to_id}
    fields = []
    for f in rec.get("fields") or []:
        fields.append({**f, "file_id": model_to_id.get(f.get("file"))})
    damages = [{"what": d["what"], "where": d.get("where"), "file": model_to_id.get(d.get("file"))}
               for d in rec.get("damages") or []]
    not_sent = [saved[i]["index"] for i in rec.get("not_sent") or []]
    with db.tx() as con:
        ensure_tables(con)
        cleanup(con)
        cls = _class_of(con, class_code, product_code)
        kind = rec.get("object_kind")
        kind_text = tx.OBJECT_KINDS[kind][0] if kind and tx.OBJECT_KINDS[kind][0] else ""
        group = ae.object_group(cls, kind_text, rec.get("class_hint") or "")
        seen = sorted(set(views.values()))
        missing = ae.missing_views(group, seen) if rec.get("ok") else ae.required_views(group)
        stored = {"ai": bool(rec.get("ok")), "reason": rec.get("reason"), "views": views,
                  "document_kinds": doc_kinds, "fields": fields, "damages": damages,
                  "object_kind": kind, "class_hint": rec.get("class_hint"), "condition": rec.get("condition"),
                  "files": len(saved), "not_sent": not_sent, "lang": lang}
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
                  "not_sent": len(not_sent), "dropped_pd": rec.get("dropped") or 0})
    if rec.get("ok"):
        message = t("ph_ok", lang, n=len(fields)) if fields else t("ph_empty", lang)
    else:
        message = t("ph_ai_off", lang, reason=rec.get("reason") or t("ai_not_connected", lang))
    notes = []
    if rec.get("ok") and not_sent:
        notes.append(t("ph_not_sent", lang, files=", ".join(str(n) for n in not_sent), mb=limits["ai_max_mb"]))
    return _reply(request, {
        "ok": True, "session": sid, "lang": lang,
        "files": [{"id": f["id"], "index": f["index"], "name": f["name"], "view": views.get(f["id"]),
                   "view_label": tx.label(tx.VIEW_LABELS, views[f["id"]], lang) if f["id"] in views else None,
                   "document_kind": doc_kinds.get(f["id"]),
                   "read_by_ai": bool(rec.get("ok")) and f["index"] not in not_sent} for f in saved],
        "rejected": rejected,
        "recognized": recognized_view(fields, lang),
        "damages": damages,
        "object_kind": ({"code": kind, "label": tx.label({k: v[1] for k, v in tx.OBJECT_KINDS.items()}, kind, lang)}
                        if kind else None),
        "class_hint": rec.get("class_hint"),
        "suggest_classes": HINT_CLASSES.get(rec.get("class_hint") or "", []),
        "condition": rec.get("condition"),
        "group": group,
        "required_views": [{"code": v, "label": tx.label(tx.VIEW_LABELS, v, lang)} for v in ae.required_views(group)],
        "missing_views": [{"code": v, "label": tx.label(tx.VIEW_LABELS, v, lang)} for v in missing],
        "ai": bool(rec.get("ok")),
        "message": message,
        "notes": notes,
        "not_sent": not_sent,
        "warning": t("warn_pd", lang),
        "expires_in_hours": PHOTO_TTL_SEC // 3600,
    })


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
    try:
        x = int(float(str(v).strip()))
    except (TypeError, ValueError):
        raise ValueError
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
                val = _s(r.get("value"), 120)
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
        else:
            session_missing = True
    upload = upload or {}
    downgraded = 0
    if clean.get("recognized") is not None:
        recognized = [dict(r) for r in clean["recognized"]]
        downgraded = trust_sources(recognized, upload)
    else:
        recognized = [{"key": f["key"], "value": f["value"], "source": f["source"], "note": f.get("note"),
                       "file_id": f.get("file_id")} for f in upload.get("fields") or []]
    damages = clean["damages"] if clean.get("damages") is not None else (upload.get("damages") or [])
    photos = int(upload.get("files") or 0)
    ai_ok = bool(upload.get("ai")) and photos > 0
    views_seen = sorted(set((upload.get("views") or {}).values()))

    kind = o.get("object_kind") or upload.get("object_kind")
    kind_type = tx.OBJECT_KINDS[kind][0] if kind in tx.OBJECT_KINDS else None
    obj_text = (preferred(recognized, "object_type") or {}).get("value") or ""
    group = ae.object_group(cls, f"{kind_type or ''} {obj_text} {o.get('object_type') or ''}",
                            upload.get("class_hint") or "")
    otype = ae.match_object_type(ref, cls, kind_type, o.get("object_type"))

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
    disc = ae.discrepancies(recognized, {"year": o.get("year")})

    present = {r["key"] for r in recognized if r.get("value")}
    if any(r["key"] == "manufacture_date" for r in recognized) or o.get("year"):
        present.add("year")
    if o.get("location"):
        present.add("location")
    if kind:
        present.add("object_type")
    missing = [k for k in ae.FIELDS_BY_GROUP.get(group, []) if k not in present]
    missing_key = [k for k in ae.KEY_FIELDS.get(group, []) if k in missing]
    missing_v = ae.missing_views(group, views_seen) if ai_ok else (ae.required_views(group) if photos else [])
    inspection = {"photos": photos, "ai": ai_ok, "ai_reason": upload.get("reason") if photos and not ai_ok else None,
                  "views_seen": views_seen, "required_views": ae.required_views(group),
                  "missing_views": missing_v, "damages": damages, "documents": documents,
                  "document_kinds": sorted(set((upload.get("document_kinds") or {}).values())),
                  "recognized": bool(recognized), "session": clean.get("session"),
                  "session_missing": session_missing, "upload_lang": upload.get("lang")}
    dec = ae.decision(risk, rate_res, value, fr, disc, inspection, missing_key, st)
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
    }


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


def _check_text(c: dict, lang: str) -> str:
    p = c.get("params") or {}
    if c["code"] == "c_disc":
        return t("c_disc", lang, label=tx.label(tx.FIELD_LABELS, p["key"], lang))
    if c["code"] == "c_views":
        return t("c_views", lang, views=", ".join(tx.label(tx.VIEW_LABELS, v, lang) for v in p["views"]))
    if c["code"] == "c_missing":
        return t("c_missing", lang, what=", ".join(tx.label(tx.FIELD_LABELS, k, lang).lower() for k in p["keys"]))
    return t(c["code"], lang)


def _disc_text(d: dict, lang: str) -> str:
    parts = []
    for g in d["values"]:
        where = _and(lang).join(tx.label(tx.SOURCE_IN, s, lang) for s in g["sources"])
        parts.append(f"{where} {g['value']}")
    return t("disc_line", lang, label=tx.label(tx.FIELD_LABELS, d["key"], lang), values=", ".join(parts))


def _row(label: str, value, note=None) -> dict:
    return {"label": label, "value": value, "note": note}


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
    kind = D.get("object_kind")
    for key in ae.FIELDS_BY_GROUP.get(D["group"], []):
        label = tx.label(tx.FIELD_LABELS, key, lang)
        if key == "location" and opt.get("location"):
            rows1.append(_row(label, tx.label(tx.LOCATION_LABELS, opt["location"], lang),
                              tx.label(tx.SOURCE_LABELS, "input", lang)))
            continue
        if key == "year" and opt.get("year"):
            rows1.append(_row(label, str(opt["year"]), tx.label(tx.SOURCE_LABELS, "input", lang)))
            continue
        best = preferred(rec, key)
        if key == "object_type" and not best and kind:
            rows1.append(_row(label, tx.label({k: v[1] for k, v in tx.OBJECT_KINDS.items()}, kind, lang),
                              tx.label(tx.SOURCE_LABELS, "photo", lang) + ", " + t("check_mark", lang)))
            continue
        if not best:
            rows1.append(_row(label, NA))
            continue
        note = tx.label(tx.SOURCE_LABELS, best["source"], lang) + ", " + t("check_mark", lang)
        others = [r for r in rec if (r["key"] == key or (key == "year" and r["key"] == "manufacture_date"))
                  and r is not best and not ae._same(key, r["value"], best["value"])]
        if others:
            note += "; " + t("also_in", lang, what="; ".join(
                f"{r['value']} ({tx.label(tx.SOURCE_LABELS, r['source'], lang)})" for r in others[:3]))
        rows1.append(_row(label, best["value"], note))
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
    rows2.append(_row(t("documents", lang), (t("docs_given", lang) + (
        " (" + ", ".join(ins["document_kinds"]) + ")" if ins.get("document_kinds") else ""))
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
    rows3 = [_row(t("sum_insured", lang), money(must["sum_insured"], lang)),
             _row(t("object_value", lang), money(must["object_value"], lang)),
             _row(t("ratio", lang), ratio),
             _row(t("verdict", lang), vtext)]
    p3 = []
    dep = value.get("depreciated")
    if dep:
        rows3.append(_row(t("depreciated", lang), money(dep["value"], lang),
                          t("depr_how", lang, price=money(dep["price_new"], lang),
                            rate=pct(dep["wear_pct_per_year"], lang), years=dep["years"])))
        p3.append(t("depr_note", lang))
    s3 = {"n": 3, "title": t("s3", lang), "paragraphs": p3, "rows": rows3}

    # ---------- раздел 4 ----------
    level_label = tx.label(tx.LEVEL_LABELS, risk["level"], lang)
    factors = [_text(f, lang) for f in risk["factors"]]
    rule = risk["rule"]
    minus = lambda x: str(x).replace("-", "−")
    rule_text = t("level_rule", lang, net=minus(risk["net"]), low=minus(rule["low_max_net"]),
                  high=minus(rule["high_min_net"]), k=rule["min_known"])
    mode = rate_res["mode"]
    rows4 = [_row(t("level", lang), level_label, t("uncalibrated", lang))]
    if mode == "tariff":
        rows4 += [_row(t("applied_rate", lang), pct(rate_res["applied_pct"], lang),
                       t("min_applied", lang) if rate_res["min_applied"] else None),
                  _row(t("base_rate", lang), pct(rate_res["base_pct"], lang)),
                  _row(t("adj", lang), "+" + pct(rate_res["adj_pct"], lang), t("uncalibrated", lang)),
                  _row(t("min_rate", lang), pct(rate_res["min_pct"], lang) if rate_res["min_pct"] is not None
                       else NA),
                  _row(t("premium", lang), money(rate_res["premium"], lang),
                       t("premium_term", lang, days=rate_res["term_days"]))]
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
    lists4 = [{"title": t("factors", lang), "items": factors + [rule_text]},
              {"title": t("how_title", lang),
               "items": [_text(h, lang) for h in rate_res["how"]]}]
    if fr.get("grounds"):
        lists4.append({"title": t("fr_grounds", lang), "items": [_text(g, lang) for g in fr["grounds"]]})
    p4 = []
    if D.get("multi_class"):
        p4.append(t("multi_class_note", lang, classes=", ".join(must.get("product_classes") or [])))
    if not fr.get("needed") and fr.get("code") == "fr_not_needed":
        p4.append(fr_text + ": " + t("fr_none_grounds", lang) + ". " + t("fr_alt", lang))
    if D["clauses"]:
        lists4.append({"title": t("clauses", lang),
                       "items": [f"{c.get(lang) or c.get('ru')} ({t('expert', lang)})" for c in D["clauses"]]})
    s4 = {"n": 4, "title": t("s4", lang), "paragraphs": p4, "rows": rows4, "lists": lists4}

    # ---------- раздел 5 ----------
    dec = D["decision"]
    p5 = [t(dec["code"], lang)]
    lists5 = []
    if D["discrepancies"]:
        lists5.append({"title": t("disc_title", lang), "items": [_disc_text(d, lang) for d in D["discrepancies"]]})
        p5.append(t("disc_priority", lang))
    else:
        p5.append(t("disc_none", lang))
    checks = [_check_text(c, lang) for c in dec["checks"]]
    if checks:
        lists5.append({"title": t("checks_title", lang), "items": checks})
    missing_labels = [tx.label(tx.FIELD_LABELS, k, lang) for k in D["missing"]]
    if ins.get("session_missing"):
        missing_labels.append(t("session_not_found", lang))
    if missing_labels:
        lists5.append({"title": t("missing_title", lang), "items": missing_labels})
    s5 = {"n": 5, "title": t("s5", lang), "paragraphs": p5, "rows": [], "lists": lists5}

    sections = [s1, s2, s3, s4, s5]
    for s in sections:
        s.setdefault("lists", [])
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
                 "tariff_version_id": D.get("tariff_version_id"),
                 "multi_class": bool(D.get("multi_class")),
                 "product_classes": (must.get("product_classes") or [])},
        "premium": {"amount": rate_res["premium"], "term_days": rate_res["term_days"], "currency": "UZS",
                    "text": money(rate_res["premium"], lang) if rate_res["premium"] is not None else NA},
        "value": {"ratio_pct": value["ratio_pct"], "verdict": value["verdict"], "text": vtext,
                  "legal_ref": value["legal_ref"], "legal_ref_text": legal,
                  "depreciated": value.get("depreciated")},
        "franchise": {"needed": bool(fr.get("needed")), "text": fr_text,
                      "grounds": [{"code": g["code"], "text": _text(g, lang)} for g in fr.get("grounds") or []],
                      **({"size": fr["size"]} if fr.get("size") else {})},
        "clauses": [{"code": c["code"], "text": c.get(lang) or c.get("ru"), "expert": True,
                     "calibrated": ae.CALIBRATED} for c in D["clauses"]],
        "discrepancies": [{"key": d["key"], "label": tx.label(tx.FIELD_LABELS, d["key"], lang),
                           "values": [{"value": g["value"], "sources": g["sources"],
                                       "source_labels": [tx.label(tx.SOURCE_LABELS, s, lang) for s in g["sources"]]}
                                      for g in d["values"]],
                           "priority": d["priority"], "text": _disc_text(d, lang)} for d in D["discrepancies"]],
        "decision": {"code": dec["code"].replace("d_", ""), "text": t(dec["code"], lang), "checks": checks},
        "missing": missing_labels,
        "inspection": {"done": bool(ins["photos"] and ins["ai"]), "photos": ins["photos"],
                       "views_seen": ins["views_seen"], "missing_views": ins["missing_views"],
                       "damages": ins["damages"], "documents": ins["documents"]},
        "recognized": recognized_view(rec, lang),
        "footer": t("footer", lang),
        "downloads": {"docx": f"/act/{meta['id']}.docx?lang={lang}", "pdf": f"/act/{meta['id']}.pdf?lang={lang}"},
    }


def _fr_text(fr: dict, lang: str) -> str:
    code = fr.get("code") or "fr_not_needed"
    if code == "fr_advise_range":
        s = fr["size"]
        if s["from_pct"] <= 0:
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
                  "lang": lang})
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
        for li in s.get("lists") or []:
            doc.para(li["title"], bold=True, after=60)
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
        for li in s.get("lists") or []:
            pdf.para(li["title"], size=10, bold=True, gap=2)
            for it in li["items"]:
                pdf.bullet(it, size=9.5)
    pdf.space(6)
    pdf.ensure(40)
    pdf.rule()
    pdf.para(act["footer"], size=10, bold=True)
    return pdf.finish()
