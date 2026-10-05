"""Отчётность страховщика, ёмкость и накопление рисков."""
from typing import Optional
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from app import db, web, capacity as cap
from app.auth import require
from app.config import PROJECT_ROOT as ROOT
from app.ui.pages import page
router = APIRouter()
ADMIN = "админ"

@router.get("/capacity")
def capacity_view():
    with db.tx() as con:
        c = cap.capacity(con)
        c["retention"] = cap.retention_table(con, c["limit_per_risk"]) if c["limit_per_risk"] else []
        return c


class ReserveRow(BaseModel):
    report_date: str
    scope_type: str                            # 'группа' | 'класс' | 'вид' | 'итого'
    scope_code: str
    rnp: float = 0
    rzu: float = 0
    rpnu: float = 0
    stab: float = 0
    cat_reserve: float = 0
    other: float = 0
    base_premium_12m: Optional[float] = None
    source: str = ""


class AssetRow(BaseModel):
    report_date: str
    category: str
    amount: float
    is_liquid: int = 1


class SolvencyRow(BaseModel):
    report_date: str
    own_funds: float
    deductions: float = 0
    premiums_12m: Optional[float] = None
    claims_36m: Optional[float] = None
    claims_36m_net: Optional[float] = None
    min_capital: Optional[float] = None
    top5_liabilities: Optional[float] = None
    source: str = ""


@router.post("/admin/reserves")
def add_reserves(rows_in: list[ReserveRow], user: dict = Depends(require(ADMIN))):
    with db.tx() as con:
        for r in rows_in:
            con.execute("INSERT OR REPLACE INTO reserve_reports VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (r.report_date, r.scope_type, r.scope_code, r.rnp, r.rzu, r.rpnu, r.stab, r.cat_reserve,
                         r.other, r.base_premium_12m, r.source))
        db.audit(con, user["login"], "отчёт о резервах", rows_in[0].report_date if rows_in else None, {"rows": len(rows_in)})
    return {"ok": True, "rows": len(rows_in)}


@router.post("/admin/assets")
def add_assets(rows_in: list[AssetRow], user: dict = Depends(require(ADMIN))):
    with db.tx() as con:
        for r in rows_in:
            con.execute("INSERT OR REPLACE INTO allocated_assets VALUES (?,?,?,?)",
                        (r.report_date, r.category, r.amount, r.is_liquid))
        db.audit(con, user["login"], "выделенные активы", rows_in[0].report_date if rows_in else None, {"rows": len(rows_in)})
    return {"ok": True, "rows": len(rows_in)}


@router.put("/admin/solvency")
def set_solvency(s: SolvencyRow, user: dict = Depends(require(ADMIN))):
    with db.tx() as con:
        con.execute("INSERT OR REPLACE INTO solvency_reports VALUES (?,?,?,?,?,?,?,?,?)",
                    (s.report_date, s.own_funds, s.deductions, s.premiums_12m, s.claims_36m, s.claims_36m_net,
                     s.min_capital, s.top5_liabilities, s.source))
        db.audit(con, user["login"], "платёжеспособность", s.report_date, s.model_dump())
    return {"ok": True}


@router.get("/capacity-page", response_class=HTMLResponse)
def capacity_page(embed: int = 0):
    return page(web.read_text(ROOT / "app" / "capacity.html"), "/capacity-page", bool(embed))


class Financials(BaseModel):
    report_date: str
    own_funds: float
    reserves: float
    source: str = ""


@router.put("/admin/financials")
def set_financials(f: Financials, user: dict = Depends(require(ADMIN))):
    with db.tx() as con:
        # колонки по имени: с 03.10.2026 в таблице есть разбивка (капитал, резервы брутто/нетто, активы)
        con.execute("INSERT OR REPLACE INTO company_financials (report_date, own_funds, reserves, source, confirmed)"
                    " VALUES (?,?,?,?,?)", (f.report_date, f.own_funds, f.reserves,
                                             f.source or "введено в админке", None))
        db.audit(con, user["login"], "финансовые показатели", f.report_date, f.model_dump())
        db.reference_changed(con)        # расчёт должен сразу видеть новое значение
    return {"ok": True, "risk_limit": 0.2 * (f.own_funds + f.reserves)}


@router.get("/accumulation")
def accumulation():
    """Накопление страховых сумм по регионам и сейсмозонам против лимита на один риск."""
    with db.tx() as con:
        c = cap.capacity(con)
        by_region = db.rows(con, """SELECT COALESCE(o.region,'не указан') region, COALESCE(o.seismic_zone, 0) zone,
                                    COUNT(*) n, SUM(o.sum_insured) total, MAX(o.sum_insured) largest
                                    FROM objects o GROUP BY region, zone ORDER BY total DESC""")
        by_product = db.rows(con, """SELECT r.product_code, COUNT(*) n, SUM(o.sum_insured) total
                                     FROM objects o JOIN requests r ON r.id=o.request_id GROUP BY r.product_code ORDER BY total DESC""")
    limit = c["limit_per_risk"]
    for r in by_region:
        r["share_of_limit"] = (r["total"] / limit) if limit else None
        r["flag"] = "стоп" if limit and r["largest"] > limit else ("внимание" if limit and r["total"] > limit else "ок")
    return {"limit_per_risk": limit, "top5_limit": c["limit_top5"], "by_region": by_region, "by_product": by_product}
