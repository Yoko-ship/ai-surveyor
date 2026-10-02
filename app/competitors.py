"""
Документы конкурентов на сервере: раз в 7 дней — проверка новых версий на сайтах страховщиков (tools/competitors_fetch.py).

Поток «competitors-refresh»: первая проверка через 15 минут после старта, дальше раз в 6 часов сверяется,
прошло ли 7 дней с прошлого прохода (перезапуск сервера не сбивает недельный ритм). Новые версии .txt —
в STORAGE_DIR/library_live/Конкуренты/<Компания>/ (локально — library/03_Рынок_НАПП/Конкуренты), прежние — в _history.
Индекс специалиста (app/legal.py) подхватывает их сам по mtime. Отпечатки, найденные ссылки и итог прохода —
в competitors_state.json рядом с базой.

  GET  /market/competitors/status   — дата, проверено, изменилось, новые, закрыто robots, ошибки
  POST /market/competitors/refresh  — внеплановый проход (администратор), в фоне
"""
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request

from . import background, db

ROOT = Path(__file__).resolve().parent.parent
THREAD = "competitors-refresh"
EVERY_DAYS = 7
FIRST_DELAY_SEC = 15 * 60
CHECK_EVERY_SEC = 6 * 3600

router = APIRouter()
_lock = threading.Lock()
_state = {"running": False}
CLIENT = None            # тесты подставляют клиента без сети (tools.competitors_fetch.Client с подменой get)


def _tool():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import tools.competitors_fetch as cf
    return cf


def out_dir() -> Path:
    from . import legal
    return legal.competitors_live_dir() or legal.competitors_dir()


def image_dir() -> Path:
    from . import legal
    return legal.competitors_dir()


def registry_file() -> Path:
    return _tool().REGISTRY


def state_file() -> Path:
    return Path(db.DB_PATH).parent / "competitors_state.json"


def refresh(who: str = "агент-рынка", limit: int = None) -> dict:
    """Один проход по реестру. Итог — в журнал audit (сводка, изменения и новые документы — отдельными строками)."""
    cf = _tool()
    if not _lock.acquire(blocking=False):
        return {"started": False, "note": "проход уже идёт"}
    _state["running"] = True
    try:
        reg = cf.load_json(registry_file(), {"sources": []})
        state = cf.load_json(state_file(), {})
        res = cf.run(reg, state, out_dir(), image_dir(), client=CLIENT or cf.Client(), limit=limit,
                     log=lambda s: None)
        cf.save_json(state_file(), state)
        with db.tx() as con:
            for c in res["changes"]:
                db.audit(con, who, "новый документ конкурента" if c["new"] else "документ конкурента изменился",
                         c["txt"], {"источник": c["url"], "прежняя версия": c["history"],
                                    "в индексе": c["indexed"],
                                    **({"внимание": "была ручная расшифровка скана — проверить"}
                                       if c["manual_text_lost"] else {})})
            for n in res["new_list"]:
                db.audit(con, who, "найдена ссылка на документ конкурента", n["txt"],
                         {"источник": n["url"], "страница": n["found_on"], "пометка": "new — проверить администратору"})
            db.audit(con, who, "проверка документов конкурентов", "competitors",
                     {k: res[k] for k in ("checked", "changed", "unchanged", "baseline", "new_found", "new_saved",
                                          "robots_closed", "errors", "took_s")})
        return res
    finally:
        _state["running"] = False
        _lock.release()


def last_run() -> dict:
    return (_tool().load_json(state_file(), {}) or {}).get("last_run") or {}


def due() -> bool:
    at = last_run().get("finished_at")
    if not at:
        return True
    try:
        return datetime.now() - datetime.fromisoformat(at) >= timedelta(days=EVERY_DAYS)
    except ValueError:
        return True


def _step():
    if due():
        res = refresh()
        if res.get("errors") and not res.get("checked"):
            background.failed(THREAD, kind="ни один источник не ответил")


def _loop():
    background.run_loop(THREAD, _step, first_delay=FIRST_DELAY_SEC, every=CHECK_EVERY_SEC)


def start_scheduler():
    background.start(THREAD, _loop)


def next_run() -> str:
    """Следующий проход: через 7 дней после прошлого, но не раньше ближайшей сверки потока."""
    at = last_run().get("finished_at")
    planned = background.state(THREAD).get("next_at")
    if at:
        try:
            nxt = (datetime.fromisoformat(at) + timedelta(days=EVERY_DAYS)).isoformat(timespec="seconds")
            return max(nxt, planned) if planned else nxt
        except ValueError:
            pass
    return planned


def status() -> dict:
    lr = last_run()
    reg = _tool().load_json(registry_file(), {"sources": []})
    state = _tool().load_json(state_file(), {}) or {}
    return {"last_run": lr.get("finished_at"), "running": _state["running"],
            "sources": len(reg.get("sources") or []), "discovered": len(state.get("discovered") or []),
            "checked": lr.get("checked"), "changed": lr.get("changed"), "unchanged": lr.get("unchanged"),
            "baseline": lr.get("baseline"), "new_found": lr.get("new_found"), "new_saved": lr.get("new_saved"),
            "robots_closed": lr.get("robots_closed"), "errors": lr.get("errors"),
            "robots": (lr.get("robots") or [])[:30], "error_list": (lr.get("error_list") or [])[:30],
            "changes": (lr.get("changes") or [])[:30], "next_run": next_run(),
            "where": db.stored_path(out_dir()),
            "schedule": "раз в 7 дней; первая проверка через 15 минут после старта сервера"}


@router.get("/market/competitors/status")
def get_status():
    return status()


@router.post("/market/competitors/refresh")
def post_refresh(request: Request):
    """Внеплановый проход (администратор). Идёт в фоне: 150+ адресов с паузой 2,5 с на домен."""
    user = request.scope.get("surveyor_user") or {}
    who = user.get("login") or "админ"
    if _state["running"]:
        return {"started": False, "note": "проход уже идёт"}

    def run():
        try:
            refresh(who=who)
            background.ok(THREAD)
        except Exception as e:
            background.failed(THREAD, e)
    threading.Thread(target=run, daemon=True, name=THREAD + "-manual").start()
    return {"started": True, "note": "проверка в фоне; итог — в /market/competitors/status"}
