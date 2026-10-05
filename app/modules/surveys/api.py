"""HTTP-запросы страхования и аналитика; сценарии создания доступны независимо от HTTP."""
from typing import Optional
from fastapi import APIRouter, Depends, Request
from app import access, db
from app.auth import current_user
from app.modules.surveys.schemas import RequestIn
from app.modules.surveys.repository import _card
from app.modules.surveys.workflow import create_survey
router = APIRouter()

@router.post("/requests")
def create_request(body: RequestIn, request: Request = None):
    # кто подал: берём вошедшего из сессии. Нужен роли «сотрудник» — у неё нет ID агента в ЕАИС,
    # а «свои запросы» и доступ к выгрузкам определяются именно по автору.
    author_id = None
    if request is not None:
        from app import auth
        user = request.scope.get("surveyor_user")
        if user is None:
            with db.tx() as con:
                user = auth.session_user(con, auth.request_token(request))
        author_id = (user or {}).get("id")
    return create_survey(body, author_id)

@router.get("/requests")
def list_requests(status: Optional[str] = None, branch: Optional[str] = None, limit: int = 100,
                  user: dict = Depends(current_user)):
    """Список запросов. Кто не видит всё (сотрудник, агент) — получает только свои: фильтр стоит
    в SQL, чтобы чужая строка не попадала в выборку вообще (docs/Регистрация и роли.md, 6.1 п. 1)."""
    sql = """SELECT r.id, r.external_no, r.branch, r.product_code, r.policyholder, r.created_at, r.status,
                    c.applied_rate_pct, c.gross_rate_pct, c.min_rate_pct, c.premium, c.verdict
             FROM requests r LEFT JOIN calculations c ON c.request_id = r.id WHERE 1=1"""
    args = []
    if not access.sees_all(user):
        sql += access.own_requests_where("r"); args += access.own_requests_args(user)
    if status:
        sql += " AND r.status=?"; args.append(status)
    if branch:
        sql += " AND r.branch=?"; args.append(branch)
    sql += " ORDER BY r.id DESC LIMIT ?"; args.append(limit)
    with db.tx() as con:
        return db.rows(con, sql, *args)


@router.get("/requests/{rid}")
def get_request(rid: int, user: dict = Depends(current_user)):
    """Чужой запрос для сотрудника и агента не существует: 404, а не 403 (6.1 п. 2)."""
    with db.tx() as con:
        access.ensure_can_open(con, user, rid)
        return _card(con, rid)


@router.get("/analytics/summary")
def analytics():
    with db.tx() as con:
        total = con.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        by_verdict = db.rows(con, "SELECT verdict, COUNT(*) n FROM calculations GROUP BY verdict")
        manual = con.execute("SELECT COUNT(*) FROM calculations WHERE explanation LIKE '%\"manual\": true%'").fetchone()[0]
        below = con.execute("SELECT COUNT(*) FROM calculations WHERE applied_rate_pct < gross_rate_pct - 1e-9").fetchone()[0]
        by_product = db.rows(con, """SELECT r.product_code, COUNT(*) n, AVG(c.applied_rate_pct) avg_rate, SUM(c.premium) premium
                                     FROM requests r JOIN calculations c ON c.request_id=r.id GROUP BY r.product_code ORDER BY n DESC""")
        by_branch = db.rows(con, """SELECT r.branch, COUNT(*) n, SUM(CASE WHEN c.applied_rate_pct < c.gross_rate_pct THEN 1 ELSE 0 END) below_tech
                                    FROM requests r JOIN calculations c ON c.request_id=r.id GROUP BY r.branch ORDER BY n DESC""")
        top_rules = db.rows(con, """SELECT rule_code, COUNT(*) n FROM check_results WHERE status='нарушено'
                                    GROUP BY rule_code ORDER BY n DESC LIMIT 10""")
    return {"requests": total, "by_verdict": by_verdict, "manual_rates": manual, "below_technical": below,
            "by_product": by_product, "by_branch": by_branch, "top_violations": top_rules}
