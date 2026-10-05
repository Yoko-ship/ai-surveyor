"""Рыночная статистика и страницы источников."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from app import background, db, web
from app.config import PROJECT_ROOT as ROOT
from app.ui.pages import page
from app.modules.market.refresh import _refresh_job, _refresh_state
router = APIRouter()

@router.get("/market/rows")
def market_rows():
    with db.tx() as con:
        return db.rows(con, """SELECT row_key, row_name, COUNT(*) points, MAX(report_date) last
                               FROM market_stats GROUP BY row_key ORDER BY row_key""")


@router.get("/market/series")
def market_series(row: str = "cls8_9"):
    """Ряд по строке отчёта: нарастающий итог, квартальные приросты и производные показатели."""
    with db.tx() as con:
        pts = db.rows(con, "SELECT * FROM market_stats WHERE row_key=? ORDER BY report_date", row)
        # пометки (01.10.2026): итог комплексного заменён суммой пакетов и т. п. — таблицы может ещё не быть
        try:
            notes = {r["report_date"]: r["note"] for r in db.rows(
                con, "SELECT report_date, note FROM market_stats_notes WHERE row_key=?", row)}
        except Exception:
            notes = {}
    out, prev = [], None
    # срез «01 января» — это итог за ПРЕДЫДУЩИЙ год, поэтому учётный год у него на единицу меньше
    eff_year = lambda d: int(d[:4]) - 1 if d[5:] == "01-01" else int(d[:4])
    for p in pts:
        d = p["report_date"]
        year = d[:4]
        # квартальный прирост — разница нарастающих итогов внутри одного учётного года
        q_prem = q_pay = None
        if prev and eff_year(prev["report_date"]) == eff_year(d) and prev["premiums_ytd"] is not None and p["premiums_ytd"] is not None:
            q_prem = p["premiums_ytd"] - prev["premiums_ytd"]
            q_pay = (p["payouts_ytd"] or 0) - (prev["payouts_ytd"] or 0)
        elif d[5:] in ("03-31", "04-01") and p["premiums_ytd"] is not None:
            q_prem, q_pay = p["premiums_ytd"], p["payouts_ytd"]
        months = {"03-31": 3, "04-01": 3, "07-01": 6, "10-01": 9, "01-01": 12}.get(d[5:], None)
        # 01-01 — это итог за прошлый год: относим к нему
        label = f"{int(year)-1} год" if d[5:] == "01-01" else f"{year} · {months} мес." if months else d
        ann = None
        if p["premiums_ytd"] and p["liabilities"] and months:
            ann = p["premiums_ytd"] * 12 / months / p["liabilities"] * 100    # годовая ставка, %
        out.append({"date": d, "label": label, "premiums_ytd": p["premiums_ytd"], "payouts_ytd": p["payouts_ytd"],
                    "liabilities": p["liabilities"], "q_premiums": q_prem, "q_payouts": q_pay,
                    "loss_ratio": (p["payouts_ytd"] / p["premiums_ytd"] * 100) if p["premiums_ytd"] else None,
                    "annual_rate": ann, "source": p["source_file"], "note": notes.get(d)})
        prev = p
    return {"row": row, "name": pts[0]["row_name"] if pts else row, "points": out}


@router.post("/market/refresh")
def market_refresh():
    started = background.start("stats-refresh-manual", _refresh_job)
    return {"started": started, "note": "проверяю сайт НАПП и обновляю ряд; результат — в /market/status"
            if started else "обновление уже запущено или фоновые задания отключены"}


@router.get("/market/status")
def market_status():
    with db.tx() as con:
        n = con.execute("SELECT COUNT(*) FROM market_stats").fetchone()[0]
        dates = [r["report_date"] for r in db.rows(con, "SELECT DISTINCT report_date FROM market_stats ORDER BY 1")]
    return {"points": n, "dates": dates, "last_refresh": _refresh_state["last"],
            "running": _refresh_state["running"], "log": _refresh_state["log"],
            "schedule": "каждые 24 часа, первая проверка через минуту после старта"}


@router.get("/market/branches")
def market_branches(date: str = "", company: str = "company:INSON AJ"):
    """Обособленные подразделения страховщика по регионам (листы 2.12–2.14 отчёта НАПП) на срез: премии, выплаты,
    договоры, убыточность и средняя премия; рядом — итог компании и рынок региона. Только чтение napp_branches."""
    from app import market_picture as mp
    with db.tx() as con:
        return mp.branches_table(con, date or None, company or mp.INSON_ROW)


@router.get("/market/claims")
def market_claims(region: str = ""):
    """Претензии (НАПП, листы 3.5/3.4/3.2 и 2.10/2.7/2.5) на последний срез: регион против республики и INSON
    против рынка. Только чтение napp_claims."""
    from app import market_picture as mp
    with db.tx() as con:
        return {"region": mp.region_claims(con, region or None), "company": mp.company_claims(con)}


@router.get("/stats", response_class=HTMLResponse)
def stats_page(embed: int = 0):
    return page(web.read_text(ROOT / "app" / "stats.html"), "/stats", bool(embed))
