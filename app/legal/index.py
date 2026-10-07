"""
Индекс знаний специалиста (SQLite FTS5, таблицы legal_chunks / legal_files): источники, обход каталогов,
сборка по mtime файлов и отпечаток состава.

Источники: законы library/01_Законодательство (law), акты живого поиска на постоянном диске (law),
документы INSON library/02_Компания_INSON (company), документы конкурентов library/03_…/Конкуренты и
STORAGE_DIR/library_live/Конкуренты (competitor), заметки docs/*.md и docs/Знания/*.md (note),
обзоры рынка docs/Знания/Рынок и STORAGE_DIR/knowledge/Рынок (market или competitor).
"""
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from .. import db
from . import cache
from .parse import _act_name, _clean_url, _slug, fold, lang_of_name, norm, split_units, strip_gaps
from .texts import DEFAULT_LANG

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "library" / "01_Законодательство"
NOTES = ROOT / "docs"
# Папки знаний считаются от ROOT/NOTES в момент обращения (тесты подменяют ROOT и NOTES):
#   NOTES/Знания/*.md          — разборы команды (app/knowledge.py), тип note;
#   NOTES/Знания/Рынок/*.md    — обзоры рынка (строит агент рынка; может не быть), тип market;
#   ROOT/library/02_Компания_INSON/*.txt — тарифная политика и документы компании, тип company;
#   на сервере — STORAGE_DIR/library_live/Компания/*.txt: документы компании не идут ни в Git
#   (репозиторий публичный), ни в образ. Выдаются только вошедшим сотрудникам (search_core.staff).


def knowledge_dir() -> Path:
    return NOTES / "Знания"


def market_notes_dir() -> Path:
    return NOTES / "Знания" / "Рынок"


def company_dir() -> Path:
    return ROOT / "library" / "02_Компания_INSON"


def competitors_dir() -> Path:
    """Публичные документы других страховщиков РУз (правила, оферты, паспорта продуктов) — тип competitor.
    Это не норма права: в ответе помечаются «документ страховщика», official = 0."""
    return ROOT / "library" / "03_Рынок_НАПП" / "Конкуренты"
# Акты, найденные живым поиском на lex.uz (app/legal_live.py). На сервере с постоянным диском
# (STORAGE_DIR) — STORAGE_DIR/library_live: library/ в образе Docker пустая и живёт до перезапуска.
# Без STORAGE_DIR — None: акты кладутся в LIB, как при ручной загрузке tools/lex_fetch.py.
LIVE_LIB = (db.DATA_DIR / "library_live") if os.environ.get("STORAGE_DIR") else None
HISTORY_DIR = "_history"         # прежние версии документов конкурентов (tools/competitors_fetch.py) — не в индекс


def company_live_dir() -> Optional[Path]:
    """Документы компании на постоянном диске сервера (STORAGE_DIR/library_live/Компания)."""
    return (LIVE_LIB / "Компания") if LIVE_LIB is not None else None


def company_files() -> list:
    """Документы компании: library/02_Компания_INSON и постоянный диск; одноимённый файл — свежий."""
    found = {}
    for base in (company_dir(), company_live_dir()):
        if base is None or not base.exists():
            continue
        for p in base.glob("*.txt"):
            _fresher(found, p.name, p)
    return [found[k][1] for k in sorted(found)]


def competitors_live_dir() -> Optional[Path]:
    """Новые версии документов конкурентов на постоянном диске (STORAGE_DIR/library_live/Конкуренты)."""
    return (LIVE_LIB / "Конкуренты") if LIVE_LIB is not None else None


def _fresher(found: dict, key: str, p: Path) -> None:
    """Из двух копий одного файла (образ и постоянный диск) берём более свежую по mtime."""
    try:
        mt = p.stat().st_mtime
    except OSError:
        return
    if key not in found or mt > found[key][0]:
        found[key] = (mt, p)


def competitor_files() -> list:
    """Документы конкурентов: library/03_…/Конкуренты и STORAGE_DIR/library_live/Конкуренты.
    Один и тот же «Компания/документ.txt» — один раз (свежий); папки _history не индексируются."""
    found = {}
    for base in (competitors_dir(), competitors_live_dir()):
        if base is None or not base.exists():
            continue
        for p in base.rglob("*.txt"):
            rel = p.relative_to(base)
            if HISTORY_DIR in rel.parts:
                continue
            _fresher(found, rel.as_posix(), p)
    return [found[k][1] for k in sorted(found)]


def market_notes_dirs() -> list:
    """Где лежат обзоры рынка: STORAGE_DIR/knowledge/Рынок (пересборка на сервере) и docs/Знания/Рынок."""
    from .. import market_knowledge as mkn
    out = []
    if mkn.KNOWLEDGE is not None:
        out.append(mkn.KNOWLEDGE / "Рынок")
    out.append(market_notes_dir())
    return [d for d in out if d.exists()]


def market_note_files() -> list:
    """Обзоры рынка по имени файла — более свежий из двух мест."""
    found = {}
    for d in market_notes_dirs():
        for p in d.glob("*.md"):
            _fresher(found, p.name, p)
    return [found[k][1] for k in sorted(found)]
ACTS_REGISTRY = ROOT / "docs" / "Отслеживаемые акты.json"


def source_kind(path: str) -> str:
    """law | company | competitor | market | note по пути файла индекса."""
    p = path or ""
    if p.startswith("library/02_") or "/library_live/Компания/" in p:
        return "company"
    if p.startswith("library/03_") or "/library_live/Конкуренты/" in p:
        return "competitor"      # правила и оферты других страховщиков (library/03_Рынок_НАПП/Конкуренты)
    if "/knowledge/Рынок/" in p:  # обзоры рынка, пересобранные на сервере (STORAGE_DIR/knowledge)
        return "competitor" if is_competitor_note(p) else "market"
    if p.startswith("docs/Знания/Рынок/") and is_competitor_note(p):
        return "competitor"      # обзор продуктов и условий конкурентов — не данные НАПП и не норма
    if p.startswith("docs/Знания/Рынок/"):
        return "market"
    if p.startswith("docs/") or p.startswith("db:"):
        return "note"
    return "law"                 # library/01_… и акты живого поиска на постоянном диске


def is_competitor_note(path) -> bool:
    """Заметка о конкурентах («Конкуренты — продукты и условия.md») — тип competitor, а не market."""
    return "конкурент" in Path(str(path)).name.lower()


# --------------------------------------------------------------------------- #
#  Источники: акты, заметки, правила
# --------------------------------------------------------------------------- #

_SOURCE_RE = re.compile(r"Источник:\s*(https?://\S+)")


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


def live_lib() -> Path:
    """Куда живой поиск сохраняет найденные акты: постоянный диск или сама библиотека."""
    return LIVE_LIB if LIVE_LIB is not None else LIB


def rel_path(p: Path) -> str:
    """Путь файла для индекса и цитат: внутри проекта — относительный, на постоянном диске
    (STORAGE_DIR вне проекта) — абсолютный. Читатели делают ROOT / путь — это работает для обоих
    (тот же подход, что app/db.stored_path)."""
    p = Path(p)
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def source_files() -> list:
    """Что индексируем: акты библиотеки (норма), акты живого поиска на постоянном диске
    и заметки проекта (пометка «заметка, не норма»)."""
    out = []
    dirs = [LIB]
    if LIVE_LIB is not None and LIVE_LIB != LIB:
        dirs.append(LIVE_LIB)
    live_comp, live_company = competitors_live_dir(), company_live_dir()
    for d in dirs:
        if not d.exists():
            continue
        for p in sorted(d.rglob("*.txt")):
            if any(x is not None and x in p.parents for x in (live_comp, live_company)):
                continue                     # документы конкурентов и компании на постоянном диске — ниже, не акты
            if skipped(p):
                continue
            out.append({"path": p, "kind": "act"})
    for p in company_files():
        if not skipped(p):
            out.append({"path": p, "kind": "company"})
    # сканы без расшифровки помечены «Индексировать: нет» — в индекс не идут
    for p in competitor_files():
        if not skipped(p):
            out.append({"path": p, "kind": "competitor"})
    if NOTES.exists():
        for p in sorted(NOTES.glob("*.md")):
            out.append({"path": p, "kind": "note"})
    if knowledge_dir().exists():
        for p in sorted(knowledge_dir().glob("*.md")):
            out.append({"path": p, "kind": "note"})
    # обзоры рынка: пересобираются сервером после нового среза НАПП — подхватываются по mtime, как законы
    for p in market_note_files():
        out.append({"path": p, "kind": "competitor" if is_competitor_note(p) else "market"})
    return out


def _file_language(path: Path, text: str) -> str:
    lang = lang_of_name(path.name)
    if lang:
        return lang
    try:
        from ..ingest import detect_language
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
    rel = rel_path(path)
    language = _file_language(path, raw)
    act = _act_name(path)
    code = _slug(path.stem)
    url = _url_for(path, raw[:400], language) if kind == "act" else ""
    # официальным считаем только узбекский текст акта: русские версии на lex.uz помечены
    # «Неофициальный перевод» (см. ГК, ст. 938), английского официального текста нет вовсе
    official = 1 if (kind == "act" and language == "uz") else 0
    if kind == "note":
        act = "Заметка проекта: " + act
    elif kind == "market":
        act = "Обзор рынка: " + act
    elif kind == "company":
        act = "Документ INSON: " + act
    elif kind == "competitor":
        # шапка выгрузки (компания, источник, дата, пометки) — до строки «-----»; в цитаты не идёт
        head, sep, body = raw.partition("\n-----\n")
        if sep:
            m = re.search(r"Источник:\s*(\S+)", head)
            url = m.group(1) if m else ""
            m = re.search(r"Компания:\s*(.+)", head)
            act = f"Документ страховщика {m.group(1).strip() if m else path.parent.name} (не норма): " + act
            raw = body
        else:
            act = "Обзор конкурентов (не норма): " + act
    units = split_units(raw, language)
    rows_ = []
    for u in units:
        body = fold(strip_gaps(u["text"]))      # в поиск разрывы не идут, в raw остаются
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


# версия разбора текста: меняется вместе с правилами очистки и разметки разрывов. Индекс,
# собранный прежней версией, пересобирается сам — иначе в цитатах остаётся старый мусор.
PARSER_VERSION = "2026-10-02.forms"
PARSER_ROW = "db:parser"


def index_stamp() -> str:
    """Отпечаток состава источников: пути + mtime + размеры. Изменился — индекс пересобирается."""
    parts = [PARSER_VERSION]
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
            # индекс собран прежней версией разбора — пересобираем целиком
            if (have.get(PARSER_ROW) or {}).get("act") != PARSER_VERSION:
                force = True
            seen, changed = set(), []
            for s in source_files():
                p = s["path"]
                rel = rel_path(p)
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
            for gone in set(have) - seen - {PARSER_ROW}:  # файл удалили — убираем и из индекса
                con.execute("DELETE FROM legal_chunks WHERE path=?", (gone,))
                con.execute("DELETE FROM legal_files WHERE path=?", (gone,))
            rules_n = _index_rules(con)
            # отметка версии разбора: по ней следующий запуск поймёт, что индекс свежий
            con.execute("DELETE FROM legal_files WHERE path=?", (PARSER_ROW,))
            con.execute("INSERT INTO legal_files (path, mtime, size, language, act, act_code,"
                        " chunks, indexed_at) VALUES (?,?,?,?,?,?,?,?)",
                        (PARSER_ROW, 0.0, 0, "", PARSER_VERSION, "parser", 0, db.now()))
            total = con.execute("SELECT COUNT(*) FROM legal_chunks").fetchone()[0]
        _index_ready["stamp"] = index_stamp()
        cache._cache.clear()
        cache._df_cache["n"].clear()
        cache._df_cache["df"].clear()            # веса слов считались по старому составу индекса
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
