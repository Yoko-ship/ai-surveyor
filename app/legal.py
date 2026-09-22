"""
Мгновенные юридические ответы на трёх языках (ru | uz | en) — без внешнего ИИ.

Зачем: юрист и андеррайтер задают один и тот же вопрос десятки раз («что если страховая сумма
больше стоимости?»), а ответ у модели занимает секунды и стоит денег. Здесь ответ собирается из
того, что уже лежит в проекте, за десятки миллисекунд:

  1. FAQ юриста  — docs/Юрист — FAQ.json (готовые короткие ответы и точные цитаты на ru/uz/en);
  2. индекс      — SQLite FTS5 по library/01_Законодательство/**/*.txt, заметкам docs/*.md
                   и правилам движка (таблица rules);
  3. ИИ          — НЕОБЯЗАТЕЛЬНОЕ улучшение: если ключ настроен (app/llm), к мгновенному ответу
                   добавляется пересказ строго по найденным пассажам. Нет ключа — ai.status="off",
                   ответ всё равно выдаётся.

Три языка. Файл считается узбекским/английским по пометке в имени («… (uz).txt», «… (узб).txt»,
«… (en).txt»), иначе язык определяется по тексту (app/ingest.detect_language), по умолчанию ru.
Узбекская латиница пишется с разными апострофами (gʻ ‘ ’ ʼ ` '), поэтому и при индексации, и в
запросе апострофы снимаются: «sugʻurta», «sug'urta» и «sugurta» — одно и то же слово.
Если на языке вопроса текста акта в базе нет — отвечаем по-русски и честно пишем об этом в note.

Персональные данные. Текст вопроса НЕ сохраняется: в legal_questions идёт только отпечаток
(sha256), язык, источник ответа, уверенность и время. Этого хватает, чтобы увидеть частые вопросы
без ответа и отдать их юристу на пополнение FAQ.

Подключение (app/main.py): app.include_router(legal.router) — индекс собирается сам при первом
обращении и дособирается по mtime файлов; принудительно — POST /legal/reindex (администратор).
"""
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import db, llm

router = APIRouter()

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library" / "01_Законодательство"
NOTES = ROOT / "docs"
FAQ_FILE = ROOT / "docs" / "Юрист — FAQ.json"
ACTS_REGISTRY = ROOT / "docs" / "Отслеживаемые акты.json"

LANGS = ("ru", "uz", "en")
DEFAULT_LANG = "ru"
CACHE_TTL_SEC = 3600
CACHE_MAX = 500
MAX_PASSAGES = 5
QUOTE_MAX = 300
AI_TIMEOUT_SEC = 8

# апострофы во всех начертаниях: узбекская латиница пишется то ʻ, то ‘, то обычным '
APOSTROPHES = "'‘’ʻʼʽ′`´"
_APO_RE = re.compile("[" + re.escape(APOSTROPHES) + "]")
_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[0-9a-zA-Zа-яёА-ЯЁўқғҳЎҚҒҲ]+")

# мусор постраничной выгрузки lex.uz — в индекс не берём
NOISE = ("Предложения по документу", "Прослушать аудио", "Получить ссылку из элемента документа",
         "Комментарий LexUz", "См. предыдущую редакцию", "См. судебную практику",
         "Hujjat elementidan havola olish", "Hujjat bo'yicha takliflar")

# служебные слова: в поисковый запрос не идут, иначе «что если» перетягивает выдачу
STOP = {
    "ru": {"что", "если", "как", "при", "для", "это", "или", "the", "или", "но", "на", "по", "из",
           "за", "от", "до", "не", "ли", "же", "бы", "быть", "есть", "его", "их", "мы", "вы",
           "можно", "нужно", "такое", "чем", "кто", "где", "когда", "почему", "какой", "какая"},
    "uz": {"nima", "qanday", "agar", "uchun", "bilan", "boʻlsa", "bolsa", "boladi", "boʻladi",
           "yoki", "va", "emas", "kerak", "qachon", "qaysi", "kim", "qayerda"},
    "en": {"what", "if", "how", "the", "a", "an", "is", "are", "of", "for", "to", "in", "on", "and",
           "or", "not", "be", "can", "should", "when", "which", "who", "where", "why", "does", "do"},
}
ALL_STOP = set().union(*STOP.values())

# порог уверенности: ниже — считаем, что нормы по вопросу нет, и так и говорим
MIN_CONFIDENCE = 0.55

NO_NORM = {
    "ru": "В законодательстве это прямо не установлено — вопрос решается правилами страхования "
          "и условиями договора. Уточните у юриста компании.",
    "uz": "Qonunchilikda bu bevosita belgilanmagan — masala sugʻurta qoidalari va shartnoma "
          "shartlari bilan hal qilinadi. Kompaniya yuristidan aniqlashtiring.",
    "en": "The law does not regulate this directly — it is governed by the insurance rules and "
          "the contract terms. Please check with the company lawyer.",
}
NO_NORM_NOTE = {
    "ru": "нормы по этому вопросу в базе не найдено — смотрите правила страхования",
    "uz": "bu savol boʻyicha bazada norma topilmadi — sugʻurta qoidalariga qarang",
    "en": "no provision found in the database — see the insurance rules",
}

# норма существует только на другом языке (Положение 3845 — только узбекский текст)
NOTE_ONLY_LANG = {
    "ru": "текст нормы есть только на языке: %s — показан оригинал и ссылка на него",
    "uz": "normaning matni faqat %s tilida mavjud — asl matn va unga havola koʻrsatilgan",
    "en": "the provision exists only in %s — the original text and its link are shown",
}

NOTE_NO_LANG = {
    "uz":"Bu hujjatning oʻzbekcha matni bazada yoʻq — javob rus tilidagi matn asosida.",
    "en": "The Uzbek/English text of this act is not in the database — the answer is based on the Russian text.",
    "ru": "узбекский/английский текст этого акта в базе отсутствует",
}


# --------------------------------------------------------------------------- #
#  Нормализация и язык
# --------------------------------------------------------------------------- #

def fold(s: str) -> str:
    """Для индекса: NFC, апострофы сняты, пробелы схлопнуты. Регистр сохраняем — его складывает сам
    токенайзер unicode61, а вот апостроф он считает разделителем, и «sugʻurta» распалось бы на
    «sug» и «urta». Поэтому апострофы именно снимаются, а не заменяются."""
    s = unicodedata.normalize("NFC", s or "")
    s = _APO_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()


def norm(s: str) -> str:
    """То же самое плюс нижний регистр — для сравнения строк в Python."""
    return fold(s).lower()


_UZ_MARKERS = {"sugurta", "modda", "boyicha", "qiymat", "summasi", "shartnoma", "tashkiloti",
               "qonun", "band", "oshsa", "toldirish", "nima", "qanday", "uchun", "bilan"}
_EN_MARKERS = {"insurance", "sum", "value", "article", "law", "contract", "policy", "claim",
               "insured", "agent", "premium"}


def detect_lang(text: str) -> str:
    """Язык короткого вопроса. Сначала штатный определитель, потом простая эвристика.

    app/ingest.detect_language рассчитан на документы и на вопросе из пяти слов честно
    возвращает None — поэтому ниже добор по алфавиту и характерным словам.
    """
    t = norm(text)
    if not t:
        return DEFAULT_LANG
    try:
        from .ingest import detect_language
        lang = (detect_language(text) or {}).get("language")
    except Exception:
        lang = None
    if lang in ("uz-latn", "uz-cyrl"):
        return "uz"
    if lang in ("ru", "en"):
        return lang
    tokens = set(_TOKEN_RE.findall(t))
    if re.search(r"[а-яёўқғҳ]", t):
        return DEFAULT_LANG                      # кириллица: ru (узбекская кириллица в вопросах не ждём)
    if tokens & _UZ_MARKERS:
        return "uz"
    if tokens & _EN_MARKERS:
        return "en"
    # латиница без опознавательных слов: узбекские буквы q/x/oʻ встречаются чаще, чем в английском
    if re.search(r"[qx]", t) and not tokens & _EN_MARKERS:
        return "uz"
    return "en"


def lang_of_name(name: str) -> Optional[str]:
    """Язык по пометке в имени файла: «… (uz).txt», «… (узб).txt», «… (en).txt», «… (рус).txt»."""
    low = name.lower()
    for mark in ("(uz)", "(узб)", "(узб.)", "(uz.)", "(o'zb)", "(oʻzb)"):
        if mark in low:
            return "uz"
    for mark in ("(en)", "(eng)", "(англ)"):
        if mark in low:
            return "en"
    for mark in ("(ru)", "(рус)", "(рус.)"):
        if mark in low:
            return "ru"
    return None


# --------------------------------------------------------------------------- #
#  Разбор файла на юниты
# --------------------------------------------------------------------------- #

# «Статья 938.», «938-modda», «Article 938», «4.» в начале строки
UNIT_RE = re.compile(
    r"^\s*(?:(?P<ru>Стать[яи]\s+(?P<ru_n>\d+[\d¹²³¹²³\-]*))"
    r"|(?P<uz>(?P<uz_n>\d+[\d\-]*)\s*-?\s*modda)"
    r"|(?P<en>Article\s+(?P<en_n>\d+[\d\-]*))"
    r")\s*[\.\)]?\s*(?P<title>.*)$", re.IGNORECASE)
POINT_RE = re.compile(r"^\s*(?P<n>\d+(?:\.\d+)*)\.\s+(?P<rest>\S.*)$")

CHUNK_CHARS = 1800          # если структуры нет — режем на куски примерно по абзацу-полтора
TOC_MIN_CHARS = 100         # короче — это строка оглавления, а не норма
RUBRIC_MAX_LINES = 40       # рубрикатор lex.uz длиннее сорока строк не бывает


def _clean_lines(text: str) -> list:
    """Строки файла без служебного мусора выгрузки lex.uz.

    Рубрикаторы («[ ОКОЗ: 03.00.00.00 Гражданское законодательство / … ]», «[ OKOZ: … ]»,
    «[ СПиТ: … ]», «[ TSZ: … ]») почти всегда разорваны на несколько строк: «[» стоит в первой,
    «]» — через две-три строки. Раньше отбрасывалась только первая строка, а продолжение
    («03.11.22.02 Majburiy sugʻurta]») попадало в индекс и цитировалось как текст закона.
    Поэтому идём с состоянием: открылась скобка — пропускаем до закрывающей.
    """
    out = []
    skip_left = 0                          # сколько ещё строк рубрикатора пропускаем
    for raw in text.splitlines():
        ln = raw.replace("﻿", "").strip()
        if not ln:
            continue
        if skip_left:
            skip_left -= 1
            if "]" in ln:
                skip_left = 0
            continue
        if any(n in ln for n in NOISE):
            continue
        if ln.startswith("["):
            if "]" not in ln:
                skip_left = RUBRIC_MAX_LINES      # предел, чтобы незакрытая скобка не съела акт
            continue
        if ln.startswith("=== стр."):
            continue
        out.append(ln)
    return out


def _unit_label(m: re.Match, lang: str) -> str:
    if m.group("ru"):
        return "ст. " + m.group("ru_n")
    if m.group("uz"):
        return m.group("uz_n").rstrip("-") + "-modda"
    return "Article " + m.group("en_n")


def split_units(text: str, lang: str) -> list:
    """Файл → список юнитов [{unit, title, text}]. Статьи, затем пункты, затем куски по размеру."""
    lines = _clean_lines(text)
    units, cur = [], None
    for ln in lines:
        m = UNIT_RE.match(ln)
        if m and len(ln) < 300:
            if cur:
                units.append(cur)
            cur = {"unit": _unit_label(m, lang), "title": (m.group("title") or "").strip(), "body": []}
            continue
        if cur is None:
            cur = {"unit": "", "title": "", "body": []}
        cur["body"].append(ln)
    if cur:
        units.append(cur)

    out = []
    for u in units:
        body = "\n".join(u["body"]).strip()
        if not body and not u["title"]:
            continue
        if len(body) <= CHUNK_CHARS * 2 or u["unit"]:
            # статья целиком: дробить её вредно — вопрос обычно про статью, а не про абзац
            for part in _split_long(body):
                out.append({"unit": u["unit"], "title": u["title"], "text": part})
        else:
            for sub in _split_points(body):
                out.append({"unit": sub["unit"], "title": u["title"], "text": sub["text"]})
    # отбрасываем оглавление: в выгрузке lex.uz перед текстом идёт список статей, где под
    # «Статья 816. Последствия неоплаты чека» стоит одна строка «Глава 46. Поручение».
    # Такие куски имеют настоящий заголовок и пустое содержание — они забивали выдачу.
    return [u for u in out if len((u["text"] or "").strip()) >= TOC_MIN_CHARS]


def _split_long(body: str) -> list:
    """Очень длинная статья → куски по абзацам, чтобы пассаж помещался в ответ."""
    if len(body) <= CHUNK_CHARS * 3:
        return [body]
    parts, buf = [], ""
    for para in body.split("\n"):
        if len(buf) + len(para) > CHUNK_CHARS * 2 and buf:
            parts.append(buf)
            buf = ""
        buf += ("\n" if buf else "") + para
    if buf:
        parts.append(buf)
    return parts


def _split_points(body: str) -> list:
    """Текст без статей — по пунктам «N.»; что не попало в пункт, идёт кусками по размеру."""
    out, cur = [], {"unit": "", "text": ""}
    for ln in body.split("\n"):
        m = POINT_RE.match(ln)
        if m and len(m.group("n")) <= 8:
            if cur["text"].strip():
                out.append(cur)
            cur = {"unit": "п. " + m.group("n"), "text": m.group("rest")}
        else:
            if len(cur["text"]) > CHUNK_CHARS * 2:
                out.append(cur)
                cur = {"unit": cur["unit"], "text": ""}
            cur["text"] += ("\n" if cur["text"] else "") + ln
    if cur["text"].strip():
        out.append(cur)
    return out


# --------------------------------------------------------------------------- #
#  Источники: акты, заметки, правила
# --------------------------------------------------------------------------- #

_SOURCE_RE = re.compile(r"Источник:\s*(https?://\S+)")


def _slug(name: str) -> str:
    """Код акта из имени файла: только буквы/цифры, пробелы → подчёркивание. Для группировки uz/ru."""
    base = norm(name)
    base = re.sub(r"\((?:uz|ru|en|узб|рус|англ)\.?\)", " ", base)
    base = re.sub(r"[^0-9a-zа-яё]+", "_", base).strip("_")
    return base[:60] or "act"


_registry_cache = {"data": None}


def _registry() -> list:
    """docs/Отслеживаемые акты.json — оттуда берём ссылки lex.uz, если их нет в шапке файла."""
    if _registry_cache["data"] is None:
        try:
            data = json.loads(ACTS_REGISTRY.read_text(encoding="utf-8"))
            _registry_cache["data"] = data.get("acts") or []
        except Exception:
            _registry_cache["data"] = []
    return _registry_cache["data"]


def _clean_url(url: str) -> str:
    """Убираем хвостовые знаки препинания, приклеившиеся к адресу при вырезании из текста.

    ВАЖНО: дефис в адресе lex.uz вида https://lex.uz/uz/docs/-180552 НЕ лишний. Проверено
    22.09.2026 обращением к сайту: /uz/docs/-<номер> отдаёт узбекский текст ЛАТИНИЦЕЙ,
    /uz/docs/<номер> — тот же акт КИРИЛЛИЦЕЙ. Обе страницы открываются (HTTP 200).
    В библиотеке лежат латинские выгрузки, поэтому дефис сохраняем — иначе сотрудник
    открывает кириллическую страницу и не находит в ней показанную цитату.
    """
    return (url or "").strip().rstrip(")").rstrip(".")


_LEX_LANG_RE = re.compile(r"(lex\.uz)/(?:(?:ru|uz|en|oz|o'z|uzc)/)?docs/", re.IGNORECASE)


def _lang_url(url: str, language: str) -> str:
    """Ссылка на lex.uz в языке процитированного текста.

    Английские файлы выгружены с адресом вида https://lex.uz/ru/docs/6124730: номер документа
    английский, а раздел сайта русский — по такой ссылке открывается не тот язык, что в цитате.
    Номер документа не трогаем, правим только языковой сегмент пути.
    """
    if not url or "lex.uz" not in url:
        return url
    seg = {"ru": "ru", "uz": "uz", "en": "en"}.get(language, "ru")
    return _LEX_LANG_RE.sub(r"\1/%s/docs/" % seg, url, count=1)


def _url_for(path: Path, head: str, language: str) -> str:
    """Ссылка на источник: сначала шапка файла («Источник: …»), потом реестр отслеживаемых актов."""
    m = _SOURCE_RE.search(head)
    if m:
        return _lang_url(_clean_url(m.group(1)), language)
    fname = norm(path.stem)
    for act in _registry():
        for key in re.findall(r"(?:зру|пкм|уп)[-\s№]*\d+|\b\d{4}\b", norm(act.get("title") or "")):
            if key and key.replace(" ", "") in fname.replace(" ", "").replace("№", ""):
                # из реестра берём готовый адрес нужного языка и язык в пути не правим:
                # у ru и uz версий на lex.uz разные номера документов
                url = (act.get("lex_url_uz") if language == "uz" else None) or act.get("lex_url") or ""
                return _clean_url(url)
    return ""


def _act_name(path: Path) -> str:
    name = path.stem
    return re.sub(r"\s*\((?:uz|ru|en|узб|рус|англ)\.?\)\s*$", "", name, flags=re.IGNORECASE).strip()


SKIP_MARK = "Индексировать: нет"


def skipped(path: Path) -> bool:
    """Файл помечен в шапке «Индексировать: нет» — в индекс не берём.

    Так исключаются полные кодексы, из которых для ответов вырезана нужная глава: иначе на
    вопрос по страхованию поиск выдавал ст. 540 (наём имущества) из файла, названного «глава 52».
    Сам файл остаётся в библиотеке — он нужен для проверки цитат и для других глав.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return SKIP_MARK in f.read(400)
    except OSError:
        return False


def source_files() -> list:
    """Что индексируем: акты библиотеки (норма) и заметки проекта (пометка «заметка, не норма»)."""
    out = []
    if LIB.exists():
        for p in sorted(LIB.rglob("*.txt")):
            if skipped(p):
                continue
            out.append({"path": p, "kind": "act"})
    if NOTES.exists():
        for p in sorted(NOTES.glob("*.md")):
            out.append({"path": p, "kind": "note"})
    return out


def _file_language(path: Path, text: str) -> str:
    lang = lang_of_name(path.name)
    if lang:
        return lang
    try:
        from .ingest import detect_language
        got = (detect_language(text[:20000]) or {}).get("language")
    except Exception:
        got = None
    if got in ("uz-latn", "uz-cyrl"):
        return "uz"
    if got == "en":
        return "en"
    return DEFAULT_LANG


# --------------------------------------------------------------------------- #
#  Сборка индекса
# --------------------------------------------------------------------------- #

_index_lock = threading.Lock()
_index_ready = {"stamp": None, "checked": 0.0}


def _insert(con, rows_):
    con.executemany(
        "INSERT INTO legal_chunks (act, act_code, language, unit, title, text, raw, url, path, official)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)", rows_)


def _index_file(con, path: Path, kind: str) -> dict:
    raw = path.read_text(encoding="utf-8", errors="replace")
    rel = path.relative_to(ROOT).as_posix()
    language = _file_language(path, raw)
    act = _act_name(path)
    code = _slug(path.stem)
    url = _url_for(path, raw[:400], language) if kind == "act" else ""
    # официальным считаем только узбекский текст акта: русские версии на lex.uz помечены
    # «Неофициальный перевод» (см. ГК, ст. 938), английского официального текста нет вовсе
    official = 1 if (kind == "act" and language == "uz") else 0
    if kind == "note":
        act = "Заметка проекта: " + act
    units = split_units(raw, language)
    rows_ = []
    for u in units:
        body = fold(u["text"])
        if not body:
            continue
        rows_.append((fold(act), code, language, fold(u["unit"]), fold(u["title"]),
                      body, u["text"].strip(), url, rel, official))
    con.execute("DELETE FROM legal_chunks WHERE path=?", (rel,))
    _insert(con, rows_)
    st = path.stat()
    con.execute("DELETE FROM legal_files WHERE path=?", (rel,))
    con.execute("INSERT INTO legal_files (path, mtime, size, language, act, act_code, chunks, indexed_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (rel, st.st_mtime, st.st_size, language, act, code, len(rows_), db.now()))
    return {"path": rel, "chunks": len(rows_), "language": language, "act": act}


def _index_rules(con) -> int:
    """Правила проверок движка: у них есть legal_ref — вопрос «почему система не даёт» тоже юридический."""
    con.execute("DELETE FROM legal_chunks WHERE path=?", ("db:rules",))
    try:
        rs = db.rows(con, "SELECT code, name, severity, legal_ref, description FROM rules")
    except Exception:
        return 0
    rows_ = []
    for r in rs:
        text = f"{r['name']}. {r['description'] or ''} Основание: {r['legal_ref'] or '—'}."
        rows_.append(("Правило проверки INSON", "rule_" + (r["code"] or ""), DEFAULT_LANG,
                      fold(r["code"] or ""), fold(r["name"] or ""), fold(text), text, "",
                      "db:rules", 0))
    _insert(con, rows_)
    return len(rows_)


def index_stamp() -> str:
    """Отпечаток состава источников: пути + mtime + размеры. Изменился — индекс пересобирается."""
    parts = []
    for s in source_files():
        try:
            st = s["path"].stat()
        except OSError:
            continue
        parts.append(f"{s['path'].as_posix()}|{int(st.st_mtime)}|{st.st_size}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _ensure_table(con) -> bool:
    """FTS5-таблицу нельзя доработать ALTER'ом: если состав колонок старый (база собрана прежней
    версией schema.sql) — пересоздаём её целиком и просим полную пересборку."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(legal_chunks)")}
    if cols and "raw" in cols:
        return False
    con.execute("DROP TABLE IF EXISTS legal_chunks")
    con.execute("DELETE FROM legal_files")
    sql = db.SCHEMA.read_text(encoding="utf-8")
    start = sql.index("CREATE VIRTUAL TABLE IF NOT EXISTS legal_chunks")
    con.execute(sql[start:sql.index(");", start) + 2])       # определение берём из schema.sql, не копируем
    return True


def reindex(force: bool = False) -> dict:
    """Пересобирает индекс по изменившимся файлам. Идемпотентно: без изменений — ничего не пишет."""
    t0 = time.time()
    with _index_lock:
        with db.tx() as con:
            try:
                force = _ensure_table(con) or force
            except Exception as e:
                print("legal: таблица индекса не проверена:", e)
            have = {r["path"]: r for r in db.rows(con, "SELECT * FROM legal_files")}
            seen, changed = set(), []
            for s in source_files():
                p = s["path"]
                rel = p.relative_to(ROOT).as_posix()
                seen.add(rel)
                try:
                    st = p.stat()
                except OSError:
                    continue
                old = have.get(rel)
                if not force and old and abs(old["mtime"] - st.st_mtime) < 1e-6 and old["size"] == st.st_size:
                    continue
                try:
                    changed.append(_index_file(con, p, s["kind"]))
                except Exception as e:
                    # ошибку не глотаем: файл пропускаем, но в журнал она попадает
                    db.audit(con, "system", "индекс закона: файл не разобран", rel, {"ошибка": str(e)[:300]})
            for gone in set(have) - seen:                 # файл удалили — убираем и из индекса
                con.execute("DELETE FROM legal_chunks WHERE path=?", (gone,))
                con.execute("DELETE FROM legal_files WHERE path=?", (gone,))
            rules_n = _index_rules(con)
            total = con.execute("SELECT COUNT(*) FROM legal_chunks").fetchone()[0]
        _index_ready["stamp"] = index_stamp()
        _cache.clear()
        _df_cache["n"].clear()
        _df_cache["df"].clear()            # веса слов считались по старому составу индекса
    return {"files_changed": len(changed), "files_total": len(seen), "rules": rules_n,
            "chunks": total, "took_ms": int((time.time() - t0) * 1000), "changed": changed[:20]}


STAMP_EVERY_SEC = 10          # чаще сверять отпечаток смысла нет: это сотня обращений к диску


def ensure_index() -> None:
    """Ленивая сборка: первый вопрос собирает индекс, дальше не чаще раза в 10 с сверяется отпечаток."""
    if _index_ready["stamp"] and time.time() - _index_ready.get("checked", 0) < STAMP_EVERY_SEC:
        return
    _index_ready["checked"] = time.time()
    if _index_ready["stamp"] == index_stamp():
        return
    reindex()


# --------------------------------------------------------------------------- #
#  FAQ юриста
# --------------------------------------------------------------------------- #

_faq_cache = {"mtime": None, "items": [], "version": None}


def faq_items() -> list:
    """docs/Юрист — FAQ.json. Файла ещё нет — работаем без него (юрист пишет параллельно)."""
    try:
        st = FAQ_FILE.stat()
    except OSError:
        _faq_cache.update({"mtime": None, "items": [], "version": None})
        return []
    if _faq_cache["mtime"] == st.st_mtime:
        return _faq_cache["items"]
    try:
        data = json.loads(FAQ_FILE.read_text(encoding="utf-8"))
        items = [i for i in (data.get("items") or []) if isinstance(i, dict) and i.get("id")]
        _faq_cache.update({"mtime": st.st_mtime, "items": items, "version": data.get("version")})
    except Exception as e:
        print("legal: FAQ не прочитан:", e)
        _faq_cache.update({"mtime": st.st_mtime, "items": [], "version": None})
    return _faq_cache["items"]


# Синонимы естественных формулировок: юрист пишет в FAQ «максимум комиссии», а спрашивают
# «какой максимальный размер», «сколько можно платить». Без этого вопрос уходил в поиск по актам
# и получал нерелевантную норму (дефект контролёра: комиссия агента → Положение 1882 о резервах).
SYNONYMS = [
    {"максимум", "максимальный", "максимально", "предел", "предельный", "лимит", "сколько",
     "размер", "величина", "ограничение", "потолок", "eng", "kop", "qancha", "miqdor", "chegara",
     "cap", "maximum", "max", "limit", "size", "much"},
    {"комиссия", "комиссионный", "вознаграждение", "агентский", "vositachilik", "haq", "haqi",
     "commission", "fee", "remuneration"},
    {"платить", "выплата", "выплачивать", "уплата", "оплата", "tolash", "tolanadi", "pay",
     "payment", "paid"},
    {"расторжение", "расторгнут", "прекращение", "досрочный", "bekor", "muddatidan",
     "termination", "terminated", "early"},
    {"возврат", "вернуть", "возвращает", "qaytarish", "qaytaradi", "return", "refund"},
    {"стоимость", "оценка", "qiymat", "value", "valuation"},
    {"сумма", "summa", "summasi", "sum"},
]
SYN_OF = {}
for _i, _grp in enumerate(SYNONYMS):
    for _w in _grp:
        SYN_OF.setdefault(_w, set()).add(_i)

PREFIX_MIN = 5          # общая основа в пять знаков: «максимальный» и «максимум» — одно слово
FAQ_MIN_SCORE = 0.6


def _groups(word: str) -> set:
    """Номера синонимических групп слова (по самому слову и по его основе)."""
    out = set(SYN_OF.get(word, ()))
    for w, g in SYN_OF.items():
        if _common_prefix(w, word) >= PREFIX_MIN:
            out |= g
    return out


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _hay_tokens(text: str) -> list:
    return _TOKEN_RE.findall(text)


def _term_hit(term: str, tokens: list, groups: set) -> bool:
    """Слово вопроса найдено в вопросе FAQ: тот же корень или синоним.

    Короткие токены («ст», «ли», «в») в сравнении не участвуют: раньше «ст» из тега «ст. 934»
    совпадало с любым словом на «ст-» («ставка», «страхование»), и вопрос про ставку получал
    ответ про дату определения стоимости.
    """
    for tk in tokens:
        if len(tk) < 4:
            continue
        n = _common_prefix(tk, term)
        if n >= PREFIX_MIN or (n >= 4 and n >= min(len(tk), len(term)) - 1):
            return True
        if groups and groups & SYN_OF.get(tk, set()):
            return True
    return False


def _faq_haystack(item: dict, lang: str) -> str:
    q = item.get("q") or {}
    tags = " ".join(str(t) for t in (item.get("tags") or []))
    return norm(" ".join([str(q.get(lang) or ""), str(q.get(DEFAULT_LANG) or ""), tags,
                          str(item.get("id") or "")]))


def _terms(text: str, lang: str) -> list:
    stop = STOP.get(lang, set()) | ALL_STOP
    out = []
    for tk in _TOKEN_RE.findall(norm(text)):
        if len(tk) < 3 or tk in stop:
            continue
        if tk not in out:
            out.append(tk)
    return out[:12]


def faq_match(question: str, lang: str) -> tuple:
    """Лучший пункт FAQ и уверенность 0..1. Считаем долю слов вопроса, найденных в q + tags."""
    items = faq_items()
    if not items:
        return None, 0.0
    terms = _terms(question, lang)
    if not terms:
        return None, 0.0
    groups = [_groups(t) for t in terms]
    best, best_score = None, 0.0
    for item in items:
        tokens = _hay_tokens(_faq_haystack(item, lang))
        hit = sum(1 for t, g in zip(terms, groups) if _term_hit(t, tokens, g))
        score = hit / len(terms)
        if score > best_score:
            best, best_score = item, score
    return (best, round(best_score, 3)) if best_score >= FAQ_MIN_SCORE else (None, round(best_score, 3))


# --------------------------------------------------------------------------- #
#  Поиск по индексу
# --------------------------------------------------------------------------- #

# предел длины основы: русский склоняется окончаниями (основа длинная), узбекский лепит суффиксы
# один за другим (qiymat → qiymatidan → qiymatdan), поэтому основу берём короче
STEM_CAP = {"ru": 8, "uz": 6, "en": 8}


def stem(t: str, lang: str = DEFAULT_LANG) -> str:
    """Грубая основа слова вместо морфологии: «страховая» → «страхов», «qiymatdan» → «qiymat».

    Словарей склонения в стандартной библиотеке нет, а «сумма»* не нашло бы «суммы».
    Отрезаем два последних знака и ограничиваем длину по языку.
    """
    return t[:max(4, min(len(t) - 2, STEM_CAP.get(lang, 8)))]


def stems_of(question: str, lang: str) -> list:
    return [stem(t, lang) for t in _terms(question, lang)]


def match_query(question: str, lang: str) -> str:
    """Запрос FTS5: основы слов через OR (одного совпадения достаточно, точность даёт пересчёт ниже)."""
    return " OR ".join('"%s"*' % s for s in stems_of(question, lang))


# веса bm25 по колонкам таблицы: act, act_code, language, unit, title, text, raw, url, path, official
BM25 = "bm25(legal_chunks, 1.0, 0.0, 0.0, 0.5, 4.0, 1.0, 0.0, 0.0, 0.0, 0.0)"

SEARCH_SQL = f"""
SELECT act, act_code, language, unit, title, url, path, official,
       snippet(legal_chunks, 5, '[', ']', ' … ', 24) AS snip,
       raw AS body,
       text AS folded,
       {BM25} AS score
  FROM legal_chunks
 WHERE legal_chunks MATCH ? AND language = ?
 ORDER BY score
 LIMIT ?
"""

CANDIDATES = 40


# вес слова: редкое слово вопроса («попугай», «крыша») решает, есть ли вообще норма по теме,
# частое («страховой», «договор») встречается в половине базы и о совпадении темы не говорит
_df_cache = {"n": {}, "df": {}}


def _doc_freq(con, stem_: str, lang: str) -> int:
    key = (lang, stem_)
    if key in _df_cache["df"]:
        return _df_cache["df"][key]
    try:
        n = con.execute("SELECT COUNT(*) FROM legal_chunks WHERE legal_chunks MATCH ?"
                        " AND language = ?", ('"%s"*' % stem_, lang)).fetchone()[0]
    except Exception:
        n = 0
    _df_cache["df"][key] = n
    return n


def _lang_total(con, lang: str) -> int:
    if lang not in _df_cache["n"]:
        try:
            _df_cache["n"][lang] = con.execute(
                "SELECT COUNT(*) FROM legal_chunks WHERE language = ?", (lang,)).fetchone()[0] or 1
        except Exception:
            _df_cache["n"][lang] = 1
    return _df_cache["n"][lang]


def weights_of(con, stems: list, lang: str) -> dict:
    """Вес каждой основы: log(N / df). Считается один раз на слово и кэшируется до пересборки."""
    import math
    total = _lang_total(con, lang)
    out = {}
    top = math.log(max(2, total))
    for s in stems:
        df = _doc_freq(con, s, lang)
        # слова, которого в базе нет вовсе («попугай», «крыша»), — самый тяжёлый вес: без него
        # совпадение по общим словам не должно выглядеть уверенным ответом
        out[s] = top if not df else max(0.15, math.log(total / df))
    return out


def _coverage_w(r: dict, weights: dict) -> float:
    """Доля ВЕСА слов вопроса, найденного в куске. Три общих слова из пяти больше не дают 0,6:
    если редкое слово темы в норме не встретилось, уверенность падает и ответ честно считается
    ненайденным."""
    if not weights:
        return 0.0
    hay = ((r.get("title") or "") + " " + (r.get("folded") or "")).lower()
    hit = sum(w for s, w in weights.items() if s in hay)
    return hit / sum(weights.values())


def _coverage(r: dict, stems: list) -> float:
    """Сколько РАЗНЫХ слов вопроса встретилось в куске. bm25 этого не умеет: он вознаграждает
    многократное повторение одного слова, из-за чего «стоимость, стоимость, стоимость» обгоняло
    статью, где есть и «страховая сумма», и «страховая стоимость»."""
    hay = (r["title"] + " " + (r["folded"] or "")).lower()
    return sum(1 for s in stems if s in hay) / max(1, len(stems))


def search(question: str, lang: str, limit: int = MAX_PASSAGES) -> list:
    stems = stems_of(question, lang)
    q = match_query(question, lang)
    if not q:
        return []
    with db.tx() as con:
        try:
            rs = db.rows(con, SEARCH_SQL, q, lang, CANDIDATES)
        except Exception as e:
            print("legal: поиск не выполнен:", e)
            return []
        weights = weights_of(con, stems, lang)
    rs = [dict(r) for r in rs]
    worst = max([-r["score"] for r in rs] or [1.0]) or 1.0
    scored = []
    for r in rs:
        r["coverage"] = _coverage(r, stems)
        r["coverage_w"] = _coverage_w(r, weights)
        title = (r["title"] or "").lower()
        in_title = sum(weights.get(s, 1.0) for s in stems if s in title) / max(1e-9, sum(weights.values()))
        # норма важнее заметки проекта и правила движка: заметка не источник права
        weight = 1.0 if r["path"].startswith("library/") else 0.8
        # решает вес найденных слов (редкое слово темы важнее общих), bm25 — только уточняет
        # порядок внутри; попадание в заголовок статьи ценится отдельно: он и есть тема вопроса
        r["rank"] = (0.6 * r["coverage_w"] + 0.25 * in_title + 0.15 * (-r["score"] / worst)) * weight
        scored.append(r)
    scored.sort(key=lambda x: -x["rank"])
    out, seen = [], set()
    for r in scored:
        # один и тот же акт лежит в библиотеке дважды (отдельная глава и документ целиком)
        key = (r["unit"], norm(r["body"])[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= limit:
            break
    return out


SENT_RE = re.compile(r"(?<=[.!?;])\s+")


def _first_sentences(text: str, n: int = 2, limit: int = 400) -> str:
    sents = [s.strip() for s in SENT_RE.split((text or "").strip()) if s.strip()]
    out = " ".join(sents[:n]).strip()
    return out[:limit] + ("…" if len(out) > limit else "")


def _best_sentences(text: str, stems: list, n: int = 2, limit: int = QUOTE_MAX) -> str:
    """Цитата — предложение, где действительно встретились слова вопроса, а не первое в куске.

    В акте первым предложением часто идёт служебная строка («Глава 46. Поручение»,
    «Oldingi tahrirga qarang») — цитировать её бессмысленно.
    """
    sents = [s.strip() for s in SENT_RE.split((text or "").strip()) if len(s.strip()) > 15]
    if not sents:
        return _first_sentences(text, n, limit)
    best_i, best = 0, -1
    for i, s in enumerate(sents):
        low = s.lower()
        hit = sum(1 for st in stems if st in low)
        if hit > best:
            best_i, best = i, hit
    if best <= 0:
        return _first_sentences(text, n, limit)
    out = " ".join(sents[best_i:best_i + n]).strip()
    return out[:limit] + ("…" if len(out) > limit else "")


def _cap(s: str) -> str:
    return (s[:1].upper() + s[1:]) if s else s


def _unit_of(r: dict) -> str:
    return r.get("unit") or r.get("title") or ""


def summarize_passages(passages: list, lang: str, stems: list = ()) -> str:
    """Резюме из предложений найденных пассажей. Ничего не сочиняем — только цитируем с указанием статьи."""
    parts = []
    for r in passages[:2]:
        head = ", ".join(x for x in (_cap(r["act"]), _unit_of(r)) if x)
        body = _best_sentences(r["body"], list(stems), 2, 400)
        if body:
            parts.append(f"{head}: {_cap(body)}")
    if not parts:
        return {"ru": "В базе нет нормы, прямо отвечающей на этот вопрос.",
                "uz": "Bazada bu savolga bevosita javob beradigan norma yoʻq.",
                "en": "No provision in the database answers this question directly."}[lang]
    return " ".join(parts)


def _citation(r: dict, lang: str, stems: list = ()) -> dict:
    quote = _best_sentences(r["body"], list(stems), 2, QUOTE_MAX)
    return {"act": _cap(r["act"]), "unit": _unit_of(r), "quote": _cap(quote),
            "url": r["url"] or None, "language": r["language"], "official": bool(r["official"])}


# --------------------------------------------------------------------------- #
#  Ответ из FAQ
# --------------------------------------------------------------------------- #

def _pick(d, lang: str):
    """Значение на языке вопроса; нет — русское. Возвращает (текст, был ли откат на ru)."""
    if not isinstance(d, dict):
        return (d, False) if d else (None, False)
    v = d.get(lang)
    if v:
        return v, False
    return d.get(DEFAULT_LANG), bool(d.get(DEFAULT_LANG)) and lang != DEFAULT_LANG


def _pick_any(d, lang: str) -> tuple:
    """Значение на языке вопроса; нет — по порядку ru, uz, en. Возвращает (значение, язык).

    Нужно там, где норма есть только на одном языке: у Положения 3845 текст только узбекский,
    и без этого отката сотрудник видел ответ вовсе без цитаты и без ссылки — проверить норму
    было нельзя. Лучше показать узбекский оригинал с пометкой, чем пустое место.
    """
    if not isinstance(d, dict):
        return (d, lang) if d else (None, None)
    for cand in (lang, DEFAULT_LANG, "uz", "en"):
        if d.get(cand):
            return d[cand], cand
    return None, None


def faq_answer(item: dict, lang: str, confidence: float) -> dict:
    text, fell_back = _pick(item.get("a") or {}, lang)
    citations = []
    for c in item.get("citations") or []:
        quote, q_lang = _pick_any(c.get("quote") or {}, lang)
        url, u_lang = _pick_any(c.get("url") or {}, lang)
        q_fb = bool(quote) and q_lang != lang
        official = (c.get("official") or {})
        official = bool(official.get(q_lang or lang)) if isinstance(official, dict) else bool(official)
        citations.append({"act": c.get("act") or "", "unit": c.get("article") or "",
                          "quote": (quote or "")[:QUOTE_MAX], "url": _clean_url(url) or None,
                          "language": q_lang or lang, "official": official})
        fell_back = fell_back or q_fb
    note = None
    if fell_back:
        note = NOTE_NO_LANG.get(lang) if lang != DEFAULT_LANG else None
    # цитата показана не на языке вопроса — честно говорим, на каком она
    other = {c["language"] for c in citations if c["quote"]} - {lang}
    if other and not note:
        note = NOTE_ONLY_LANG.get(lang, NOTE_ONLY_LANG[DEFAULT_LANG]) % ", ".join(sorted(other))
    if lang == "en" and not any(c["official"] for c in citations):
        note = (note + " " if note else "") + "unofficial: no official English text of the act exists"
    return {"text": text or "", "citations": citations, "note": note,
            "confidence": confidence}


# --------------------------------------------------------------------------- #
#  ИИ — необязательное улучшение
# --------------------------------------------------------------------------- #

AI_SYSTEM = ("Ты юрист страховой компании в Узбекистане. Отвечай ТОЛЬКО по приведённым ниже пассажам. "
             "Ничего не добавляй от себя: если в пассажах ответа нет — так и напиши. "
             "Цитируй номера статей и пунктов. Ответ — 2–5 предложений. "
             "Язык ответа строго: %s.")


def ai_answer(question: str, passages: list, lang: str) -> dict:
    """Пересказ по найденным пассажам. Нет ключа — ai.status='off', мгновенный ответ уже отдан."""
    if not llm.enabled():
        return {"status": "off", "text": None}
    body = "\n\n".join(f"[{_cap(r['act'])} {_unit_of(r)}]\n{r['body'][:1200]}" for r in passages[:MAX_PASSAGES])
    if not body:
        return {"status": "off", "text": None}
    old = getattr(llm, "TIMEOUT_SEC", None)
    try:
        llm.TIMEOUT_SEC = AI_TIMEOUT_SEC            # юридический ответ ждать дольше 8 с нет смысла
        text = llm.chat("юридический вопрос", AI_SYSTEM % lang,
                        f"Вопрос: {question}\n\nПассажи:\n{body}", max_tokens=400)
    except Exception as e:
        return {"status": "error", "text": None, "reason": str(e)[:200]}
    finally:
        if old is not None:
            llm.TIMEOUT_SEC = old
    if not text:
        return {"status": "error", "text": None, "reason": (llm.last_error or {}).get("text")}
    return {"status": "ok", "text": text.strip()}


# --------------------------------------------------------------------------- #
#  Кэш и журнал
# --------------------------------------------------------------------------- #

_cache = {}
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        if time.time() - hit[0] > CACHE_TTL_SEC:
            _cache.pop(key, None)
            return None
        return hit[1]


def _cache_put(key, value):
    with _cache_lock:
        if len(_cache) >= CACHE_MAX:
            _cache.clear()
        _cache[key] = (time.time(), value)


def q_hash(question: str, lang: str) -> str:
    """Отпечаток вопроса вместо текста: в вопросе может быть ФИО или ИНН — в базу их не кладём."""
    return hashlib.sha256(norm(question).encode("utf-8")).hexdigest()[:32]


def log_question(question: str, lang: str, source: str, confidence: float, took_ms: int, found: bool):
    try:
        with db.tx() as con:
            con.execute("INSERT INTO legal_questions (created_at, lang, q_hash, source, confidence,"
                        " took_ms, found) VALUES (?,?,?,?,?,?,?)",
                        (db.now(), lang, q_hash(question, lang), source, confidence, took_ms, int(found)))
    except Exception as e:
        print("legal: вопрос не записан в журнал:", e)


# --------------------------------------------------------------------------- #
#  Главная логика
# --------------------------------------------------------------------------- #

def related(lang: str, limit: int = 6) -> list:
    out = []
    for item in faq_items()[:50]:
        q, _ = _pick(item.get("q") or {}, lang)
        if q:
            out.append({"id": item["id"], "q": q})
        if len(out) >= limit:
            break
    return out


def ask(question: str, lang: str = None, with_ai: bool = False) -> dict:
    t0 = time.time()
    question = (question or "").strip()
    if not question:
        raise HTTPException(422, "Вопрос пустой")
    lang = lang if lang in LANGS else detect_lang(question)
    ensure_index()

    key = (norm(question), lang, bool(with_ai))
    cached = _cache_get(key)
    if cached:
        out = dict(cached)
        out["took_ms"] = int((time.time() - t0) * 1000)
        out["cached"] = True
        return out

    note = None
    item, conf = faq_match(question, lang)
    if item:
        a = faq_answer(item, lang, conf)
        answer = {"text": a["text"], "source": "faq", "confidence": conf}
        citations, note = a["citations"], a["note"]
        passages = []
    else:
        stems = stems_of(question, lang)
        passages = search(question, lang)
        if not passages and lang != DEFAULT_LANG:
            # узбекского или английского текста этого акта в базе нет — отвечаем по-русски и говорим об этом
            stems = stems_of(question, DEFAULT_LANG)
            passages = search(question, DEFAULT_LANG)
            if passages:
                note = NOTE_NO_LANG.get(lang) or NOTE_NO_LANG["ru"]
        # уверенность — по лучшему пассажу и по весу найденных слов, а не по среднему числу
        # совпавших слов: среднее по трём случайным нормам давало «приемлемые» 0,5–0,7 там,
        # где закон вопроса вообще не касается
        best = max((r.get("coverage_w", r["coverage"]) for r in passages), default=0.0)
        conf = round(best, 2)
        if conf < MIN_CONFIDENCE:
            # закон молчит: не выдаём три произвольных нормы за ответ
            passages, citations = [], []
            answer = {"text": NO_NORM.get(lang) or NO_NORM[DEFAULT_LANG],
                      "source": "none", "confidence": conf}
            note = note or NO_NORM_NOTE.get(lang) or NO_NORM_NOTE[DEFAULT_LANG]
            out = {"lang": lang, "took_ms": int((time.time() - t0) * 1000), "answer": answer,
                   "citations": [], "related": related(lang), "ai": {"status": "off", "text": None},
                   "note": note, "cached": False}
            _cache_put(key, out)
            log_question(question, lang, "none", conf, out["took_ms"], False)
            return out
        answer = {"text": summarize_passages(passages, lang, stems),
                  "source": "passages", "confidence": conf}
        citations = [_citation(r, lang, stems) for r in passages]
        if lang == "en" and citations and not any(c["official"] for c in citations):
            note = (note + " " if note else "") + "unofficial: no official English text of the act exists"

    ai = {"status": "off", "text": None}
    if with_ai and passages:
        ai = ai_answer(question, passages, lang)

    took = int((time.time() - t0) * 1000)
    out = {"lang": lang, "took_ms": took, "answer": answer, "citations": citations,
           "related": related(lang), "ai": ai, "note": note, "cached": False}
    _cache_put(key, out)
    log_question(question, lang, answer["source"], answer["confidence"], took, bool(citations))
    return out


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #

@router.on_event("startup")
def _build_index_on_start():
    """При старте сервера индекс дособирается в фоне: юрист мог докачать узбекские тексты,
    пока сервер был выключен. Отдельным потоком — старт не должен ждать разбор библиотеки."""
    if os.environ.get("SURVEYOR_NO_BACKGROUND") == "1":
        return
    try:
        from . import background
        background.start("legal-index", lambda: reindex())
    except Exception as e:
        print("legal: индекс при старте не собран:", e)


class AskIn(BaseModel):
    q: str = Field(min_length=2, max_length=1000)
    lang: Optional[str] = None
    ai: bool = False


@router.post("/legal/ask")
def legal_ask(body: AskIn):
    """Мгновенный юридический ответ. lang не указан — определяем по тексту вопроса."""
    return ask(body.q, body.lang, with_ai=body.ai)


@router.get("/legal/faq")
def legal_faq(lang: str = DEFAULT_LANG):
    """Список вопросов FAQ — чипы «спросить в один клик»."""
    lang = lang if lang in LANGS else DEFAULT_LANG
    items = []
    for item in faq_items():
        q, fb = _pick(item.get("q") or {}, lang)
        if not q:
            continue
        items.append({"id": item["id"], "q": q, "tags": item.get("tags") or [],
                      "lang": DEFAULT_LANG if fb else lang})
    return {"lang": lang, "version": _faq_cache.get("version"), "count": len(items), "items": items}


@router.get("/legal/acts")
def legal_acts():
    """Какие акты и на каких языках лежат в индексе — чтобы юрист видел, что ещё не докачано."""
    ensure_index()
    with db.tx() as con:
        rs = db.rows(con, "SELECT act, act_code, language, SUM(chunks) AS chunks FROM legal_files"
                          " GROUP BY act_code, language ORDER BY act")
    acts = {}
    for r in rs:
        a = acts.setdefault(r["act_code"], {"act_code": r["act_code"], "act": r["act"],
                                            "languages": [], "chunks": 0})
        a["languages"].append(r["language"])
        a["chunks"] += r["chunks"] or 0
    out = sorted(acts.values(), key=lambda a: a["act"])
    missing = [a["act"] for a in out if "uz" not in a["languages"]]
    return {"count": len(out), "acts": out, "languages": list(LANGS),
            "without_uz": missing, "faq_file": FAQ_FILE.exists()}


@router.post("/legal/reindex")
def legal_reindex(request: Request, force: bool = True):
    """Пересборка индекса (администратор): юрист докачал узбекский текст — нажал и готово."""
    user = request.scope.get("surveyor_user") or {}
    out = reindex(force=force)
    try:
        with db.tx() as con:
            db.audit(con, user.get("login") or "админ", "пересобран индекс законодательства",
                     "legal_chunks", {"кусков": out["chunks"], "файлов": out["files_total"]})
    except Exception as e:
        print("legal: журнал пересборки не записан:", e)
    return out
