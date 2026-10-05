"""Состояние базы и источников без секретов и клиентских данных."""
from fastapi import APIRouter
from app import db, background
from app.modules.market.refresh import _refresh_state
router = APIRouter()

@router.get("/health")
def health():
    """Проверка живости (Railway, Docker). status и products — как раньше: на них опирается проверка
    площадки. Дальше — состояние базы, фоновых потоков и даты обновлений; без секретов и данных людей."""
    with db.tx() as con:
        n = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        base = {"opens": True, "journal_mode": con.execute("PRAGMA journal_mode").fetchone()[0]}
        updates = {}
        for key, sql in (("market_stats", "SELECT MAX(loaded_at) FROM market_stats"),
                         ("stat_series", "SELECT MAX(fetched_at) FROM stat_series"),
                         ("lawwatch", "SELECT MAX(last_checked_at) FROM watched_acts"),
                         ("exchange_quotes", "SELECT MAX(fetched_at) FROM exchange_quotes")):
            try:
                updates[key] = con.execute(sql).fetchone()[0]
            except Exception:
                updates[key] = None
    sources = data_sources(updates)
    updates["market_knowledge"] = sources["market_knowledge"]["last"]
    updates["competitors"] = sources["competitors"]["last"]
    return {"status": "ok", "products": n, "db": base, "background": background.status(),
            "updated": updates, "data_sources": sources}


def _next_daily(hour: int, minute: int) -> str:
    from datetime import datetime, timedelta
    now = datetime.now()
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=1)
    return at.isoformat(timespec="seconds")


def data_sources(updated: dict = None) -> dict:
    """Блок «Источники данных» для /health и админки (Система → Источники данных): когда обновлялось,
    когда следующий запуск, последняя ошибка (только тип, без текста). Сбой одного модуля не роняет блок."""
    updated = updated or {}

    def thread(name):
        st = background.state(name)
        return {"next": st.get("next_at"), "error": st.get("last_error"), "error_at": st.get("last_error_at"),
                "alive": st.get("alive")}

    out = {}
    t = thread("stats-refresh")
    out["napp"] = {"title": "Отчёты НАПП", "last": _refresh_state["last"] or updated.get("market_stats"),
                   "next": t["next"], "error": t["error"] or (
                       "ошибка обновления" if any(str(l).startswith("ошибка") for l in _refresh_state["log"]) else None),
                   "error_at": t["error_at"], "schedule": "раз в сутки"}
    for key, title, mod, name in (("stat_uz", "stat.uz (агентство статистики)", "statagency", "stat-agency-refresh"),
                                  ("exchange", "Биржа (uzex.uz)", "uzex", "exchange-refresh")):
        t = thread(name)
        try:
            m = __import__(f"app.{mod}", fromlist=["_state"])
            last, err = m._state.get("last"), m._state.get("error")
        except Exception:
            last, err = None, None
        db_key = "stat_series" if key == "stat_uz" else "exchange_quotes"
        out[key] = {"title": title, "last": last or updated.get(db_key), "next": t["next"],
                    "error": t["error"] or ("ошибка обновления" if err else None), "error_at": t["error_at"],
                    "schedule": "раз в сутки"}
    t = thread("lawwatch")
    try:
        from app import lawwatch
        law = lawwatch.last_status()
        nxt = _next_daily(lawwatch.CHECK_HOUR, lawwatch.CHECK_MINUTE)
    except Exception:
        law, nxt = {}, None
    out["laws"] = {"title": "Законодательство (lex.uz)", "last": law.get("last") or updated.get("lawwatch"),
                   "next": nxt, "error": t["error"] or ("ошибка прохода" if law.get("error") else None),
                   "error_at": t["error_at"], "schedule": law.get("schedule") or "раз в сутки"}
    t = thread("market-knowledge")
    try:
        from app import market_knowledge
        mk = market_knowledge.status()
    except Exception:
        mk = {}
    out["market_knowledge"] = {"title": "Знания о рынке (заметки и факты)", "last": mk.get("last_built"),
                               "next": "после обновления НАПП, если данные изменились",
                               "slice": mk.get("slice"), "where": mk.get("where"),
                               "error": t["error"] or mk.get("last_error"),
                               "error_at": t["error_at"] or mk.get("last_error_at"),
                               "schedule": mk.get("schedule")}
    t = thread("competitors-refresh")
    try:
        from app import competitors
        cs = competitors.status()
    except Exception:
        cs = {}
    out["competitors"] = {"title": "Документы конкурентов", "last": cs.get("last_run"), "next": cs.get("next_run"),
                          "checked": cs.get("checked"), "changed": cs.get("changed"),
                          "robots_closed": cs.get("robots_closed"), "errors": cs.get("errors"),
                          "error": t["error"], "error_at": t["error_at"], "schedule": cs.get("schedule")}
    return out
