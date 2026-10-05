"""Офис, журнал и представление состояния команды."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from app import db, web
from app.config import PROJECT_ROOT as ROOT
from app.modules.market.refresh import _refresh_state
from app.ui.pages import page
router = APIRouter()

AGENTS = [
    ("lead", "Руководитель", "планирует работу, сводит отчёты, ведёт вопросы к заказчику", ["admin"]),
    ("law", "Юрист", "читает законы и акты, извлекает правила проверок, ищет недостающее на lex.uz", ["юрист"]),
    ("data", "Статистик", "забирает отчёты НАПП, ведёт динамику рынка, готовит калибровку", ["агент-статистик", "system"]),
    ("actuary", "Актуарий", "считает ставки, проверки, удержание и ёмкость", ["api", "экран агента"]),
    ("backend", "Разработчик", "база, сервер, интеграция с учётной системой", ["backend"]),
    ("ui", "Дизайнер", "экраны агента и админки, паутина, динамика", ["ui"]),
    ("reviewer", "Контролёр", "проверяет расчёты и отчёты перед сдачей", ["reviewer"]),
]


@router.get("/agents/status")
def agents_status():
    with db.tx() as con:
        log = db.rows(con, "SELECT ts, who, action, entity FROM audit ORDER BY id DESC LIMIT 300")
    out = []
    for code, name, role, whos in AGENTS:
        mine = [l for l in log if l["who"] in whos or (code == "actuary" and (l["action"] or "").startswith("создан запрос"))]
        last = mine[0] if mine else None
        state = "работает" if (code == "data" and _refresh_state["running"]) else ("сделал" if last else "ожидает задачи")
        out.append({"code": code, "name": name, "role": role, "state": state,
                    "last_action": last["action"] if last else None, "last_entity": last["entity"] if last else None,
                    "last_ts": last["ts"] if last else None, "count": len(mine)})
    try:                                   # слежение за законодательством: что у юриста на столе
        from app import lawwatch
        law = lawwatch.last_status()
    except Exception:
        law = None
    if law:
        for a in out:
            if a["code"] == "law":
                if law["running"]:
                    a["state"] = "работает"
                elif law["unseen_events"]:
                    a["state"] = "сделал"
                a["law_unseen"] = law["unseen_events"]
                a["law_changed_acts"] = law["changed_acts"]
    return {"agents": out, "refresh": _refresh_state, "lawwatch": law}


@router.get("/office", response_class=HTMLResponse)
def office_page(embed: int = 0):
    return page(web.read_text(ROOT / "app" / "office.html"), "/office", bool(embed))


@router.get("/audit")
def audit_log(limit: int = 200):
    with db.tx() as con:
        return db.rows(con, "SELECT * FROM audit ORDER BY id DESC LIMIT ?", limit)
