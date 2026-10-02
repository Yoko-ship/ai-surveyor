"""
База знаний специалиста о рынке на сервере: заметки «Рынок/*.md» и факты market_facts.json.

Логика сборки — tools/market_knowledge.py (generate). Здесь — когда и куда собирать:
  * после успешного обновления статистики НАПП (app/main.py, _refresh_job) сервер сверяет отпечаток
    рабочих таблиц (число строк, последний срез, суммы премий и выплат, претензии, подразделения);
    изменился — пересборка в фоне (поток «market-knowledge»), запись в журнал audit;
  * вручную — POST /market/knowledge/rebuild (администратор).

Куда пишем: на сервере (STORAGE_DIR) — STORAGE_DIR/knowledge/Рынок/*.md и STORAGE_DIR/knowledge/market_facts.json
(docs/ в образе живёт до перезапуска); локально — docs/Знания/Рынок и docs/market_facts.json, как раньше.
Читатели (app/legal.py, app/market_expert.py) смотрят оба места и берут более свежий файл.

Статистика на сервере не пересобирается из data/parsed (разборов там может не быть): берём рабочие
таблицы как есть (rebuild=False). Листы, которых нет в базе (1.1, 1.5, 1.6, 2.1, 2.9), дочитываются из
data/parsed, если он есть; нет — эти разделы заметок честно пишут «нет в открытых данных».
"""
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request

from . import background, db

ROOT = Path(__file__).resolve().parent.parent
THREAD = "market-knowledge"

# STORAGE_DIR/knowledge — на сервере; None — локально (пишем в docs). Тесты подменяют.
KNOWLEDGE: Optional[Path] = (db.DATA_DIR / "knowledge") if os.environ.get("STORAGE_DIR") else None
DOCS_NOTES = ROOT / "docs" / "Знания" / "Рынок"
DOCS_FACTS = ROOT / "docs" / "market_facts.json"

router = APIRouter()
_lock = threading.Lock()
_state = {"running": False, "last": None, "error": None, "result": None}


def notes_out() -> Path:
    return (KNOWLEDGE / "Рынок") if KNOWLEDGE is not None else DOCS_NOTES


def facts_out() -> Path:
    return (KNOWLEDGE / "market_facts.json") if KNOWLEDGE is not None else DOCS_FACTS


def state_file() -> Path:
    # отпечаток последней сборки — рядом с базой (на сервере — на постоянном диске)
    return Path(db.DB_PATH).parent / "market_knowledge_state.json"


def load_state() -> dict:
    try:
        return json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> None:
    p = state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def signature(con) -> dict:
    """Отпечаток данных, из которых собираются заметки. Время загрузки не входит: ежедневная
    перезапись тех же отчётов не должна пересобирать заметки."""
    sig = {}
    r = con.execute("SELECT COUNT(*), MAX(report_date), ROUND(COALESCE(SUM(premiums_ytd),0),1),"
                    " ROUND(COALESCE(SUM(payouts_ytd),0),1) FROM market_stats").fetchone()
    sig["market_stats"] = [r[0], r[1], r[2], r[3]]
    for t in ("napp_claims", "napp_branches", "market_stats_notes", "company_financials"):
        try:
            sig[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception:
            sig[t] = None              # таблицы может не быть в старой базе
    sig["hash"] = hashlib.sha256(json.dumps(sig, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return sig


def current_signature() -> dict:
    with db.tx() as con:
        return signature(con)


def _tool():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import tools.market_knowledge as mk
    return mk


def rebuild(who: str = "агент-статистик", reason: str = "", rebuild_stats: bool = False) -> dict:
    """Собирает заметки и факты сейчас (в вызывающем потоке). Возвращает итог; ошибка — исключение."""
    if not _lock.acquire(blocking=False):
        return {"started": False, "note": "сборка уже идёт"}
    _state["running"] = True
    t0 = time.time()
    try:
        sig = current_signature()
        mk = _tool()
        docs, facts, _ = mk.generate(db.DB_PATH, notes_out(), facts_out(), rebuild=rebuild_stats)
        res = {"built_at": db.now(), "signature": sig, "notes": sorted(docs), "facts": len(facts),
               "notes_dir": db.stored_path(notes_out()), "facts_file": db.stored_path(facts_out()), "took_ms": int((time.time() - t0) * 1000), "reason": reason}
        st = load_state()
        st.update({"signature": sig, "built_at": res["built_at"], "notes": res["notes"], "facts": res["facts"],
                   "last_error": None, "last_error_at": None})
        _save_state(st)
        _state.update(last=res["built_at"], error=None, result=res)
        with db.tx() as con:
            db.audit(con, who, "пересобраны знания о рынке", "market_knowledge",
                     {"заметок": len(docs), "фактов": len(facts), "срез": sig["market_stats"][1],
                      "причина": reason, "мс": res["took_ms"]})
        return res
    except Exception as e:
        st = load_state()
        st.update({"last_error": type(e).__name__, "last_error_at": db.now()})
        try:
            _save_state(st)
            with db.tx() as con:
                db.audit(con, who, "знания о рынке не пересобраны", "market_knowledge",
                         {"ошибка": str(e)[:300], "причина": reason})
        except Exception as e2:
            print("market_knowledge: журнал не записан:", e2)
        _state["error"] = type(e).__name__
        raise
    finally:
        _state["running"] = False
        _lock.release()


def needs_rebuild() -> tuple:
    """(нужно ли, причина): данных нет — не нужно; отпечаток сменился или файлов нет — нужно."""
    sig = current_signature()
    if not sig["market_stats"][0]:
        return False, "в market_stats нет строк"
    st = load_state()
    if not facts_out().exists():
        return True, "файла фактов нет"
    if (st.get("signature") or {}).get("hash") != sig["hash"]:
        return True, "данные НАПП изменились (срез %s)" % sig["market_stats"][1]
    return False, "данные не менялись"


def maybe_rebuild(who: str = "агент-статистик") -> dict:
    """Синхронно: сверить и при необходимости собрать. Для фонового потока и тестов."""
    need, why = needs_rebuild()
    if not need:
        return {"rebuilt": False, "reason": why}
    try:
        res = rebuild(who=who, reason=why)
        background.ok(THREAD)
        return {"rebuilt": bool(res.get("built_at")), "reason": why, **res}
    except Exception as e:
        background.failed(THREAD, e)
        return {"rebuilt": False, "reason": why, "error": type(e).__name__}


def trigger(who: str = "агент-статистик", force: bool = False) -> bool:
    """После обновления НАПП: проверка и сборка в фоне, чтобы не держать поток статистики."""
    def run():
        if force:
            try:
                rebuild(who=who, reason="ручной запуск")
                background.ok(THREAD)
            except Exception as e:
                background.failed(THREAD, e)
        else:
            maybe_rebuild(who)
    if background.disabled():
        return False
    return background.start(THREAD, run)


def status() -> dict:
    st = load_state()
    return {"last_built": st.get("built_at"), "running": _state["running"], "slice": (st.get("signature") or {})
            .get("market_stats", [None, None])[1], "notes": st.get("notes") or [], "facts": st.get("facts"),
            "last_error": st.get("last_error"), "last_error_at": st.get("last_error_at"),
            "where": "STORAGE_DIR/knowledge" if KNOWLEDGE is not None else "docs",
            "schedule": "после каждого обновления отчётов НАПП, если данные изменились"}


@router.post("/market/knowledge/rebuild")
def post_rebuild(request: Request, wait: bool = False):
    """Пересборка заметок и фактов о рынке (администратор). wait=1 — дождаться итога."""
    user = request.scope.get("surveyor_user") or {}
    who = user.get("login") or "админ"
    if wait or background.disabled():
        return rebuild(who=who, reason="ручной запуск")
    started = trigger(who=who, force=True)
    return {"started": started, "note": "сборка в фоне; итог — в /market/knowledge/status и /health"}


@router.get("/market/knowledge/status")
def get_status():
    return status()
