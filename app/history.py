"""
История и автообновление данных портфеля.

- auto_import(): забирает новые файлы из data/inbox/portfolio (кладите туда выгрузки — или настройте
  учётную систему выгружать туда по расписанию), импортирует через модуль portfolio, пропускает уже загруженные.
- Связь загрузок между собой по номеру договора: история каждого договора и разница между соседними загрузками
  (новые, исчезнувшие, изменившие ставку или сумму).
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException

from . import db

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "data" / "inbox" / "portfolio"
router = APIRouter()


def auto_import() -> dict | None:
    """Импортирует выгрузки, которых ещё нет в portfolio_batches. Возвращает сводку или None, если новых нет."""
    from . import portfolio
    INBOX.mkdir(parents=True, exist_ok=True)
    with db.tx() as con:
        done = {r["file_name"] for r in db.rows(con, "SELECT file_name FROM portfolio_batches")}
    new = [p for p in sorted(INBOX.glob("*.xlsx")) if p.name not in done and not p.name.startswith("~$")
           and p.open("rb").read(2) == b"PK"]
    if not new:
        return None
    out = {"imported": [], "failed": []}
    for p in new:
        try:
            res = portfolio.import_file(p, p.name)
            out["imported"].append({"file": p.name, "batch_id": res.get("batch_id"), "rows": res.get("summary", {}).get("rows_total")})
        except Exception as e:
            out["failed"].append({"file": p.name, "error": str(e)[:200]})
    return out


@router.get("/portfolio/history/{external_no}")
def contract_history(external_no: str):
    """Все загрузки, где встречался договор: как менялись ставка, сумма, премия и вердикт."""
    with db.tx() as con:
        rows = db.rows(con, """SELECT r.batch_id, b.imported_at, b.file_name, r.product_code, r.policyholder, r.branch,
                                      r.sum_insured, r.value_amount, r.premium_file, r.applied_rate_pct, r.technical_rate_pct,
                                      r.min_rate_pct, r.verdict, r.violations
                               FROM portfolio_reviews r JOIN portfolio_batches b ON b.id = r.batch_id
                               WHERE r.external_no = ? ORDER BY b.imported_at""", external_no)
        req = db.rows(con, """SELECT r.id, r.created_at, r.status, c.applied_rate_pct, c.premium, c.verdict
                              FROM requests r LEFT JOIN calculations c ON c.request_id = r.id
                              WHERE r.external_no = ? ORDER BY r.id""", external_no)
    if not rows and not req:
        raise HTTPException(404, "Договор не встречался ни в загрузках, ни в запросах")
    timeline, prev = [], None
    for r in rows:
        changes = []
        if prev:
            for k, label in (("applied_rate_pct", "ставка"), ("sum_insured", "сумма"), ("premium_file", "премия"), ("verdict", "вердикт")):
                if r[k] != prev[k]:
                    changes.append({"what": label, "from": prev[k], "to": r[k]})
        timeline.append({**r, "changes": changes})
        prev = r
    return {"external_no": external_no, "loads": timeline, "requests": req}


@router.get("/portfolio/batches/{bid}/diff")
def batch_diff(bid: int):
    """Что изменилось по сравнению с предыдущей загрузкой (по номеру договора)."""
    with db.tx() as con:
        cur = db.rows(con, "SELECT * FROM portfolio_batches WHERE id=?", bid)
        if not cur:
            raise HTTPException(404, "Загрузка не найдена")
        prev = db.rows(con, "SELECT * FROM portfolio_batches WHERE imported_at < ? ORDER BY imported_at DESC LIMIT 1",
                       cur[0]["imported_at"])
        if not prev:
            return {"batch_id": bid, "previous": None, "note": "Это первая загрузка — сравнивать не с чем"}
        a = {r["external_no"]: r for r in db.rows(con, "SELECT * FROM portfolio_reviews WHERE batch_id=? AND external_no IS NOT NULL", prev[0]["id"])}
        b = {r["external_no"]: r for r in db.rows(con, "SELECT * FROM portfolio_reviews WHERE batch_id=? AND external_no IS NOT NULL", bid)}
    new = [b[k] for k in b if k not in a]
    gone = [a[k] for k in a if k not in b]
    changed = []
    for k in b:
        if k in a:
            x, y = a[k], b[k]
            diff = {}
            for f in ("applied_rate_pct", "sum_insured", "premium_file", "verdict"):
                if x[f] != y[f]:
                    diff[f] = {"from": x[f], "to": y[f]}
            if diff:
                changed.append({"external_no": k, "policyholder": y["policyholder"], "diff": diff})
    worse = [c for c in changed if "verdict" in c["diff"] and c["diff"]["verdict"]["to"] == "отклонено"]
    return {"batch_id": bid, "previous": {"id": prev[0]["id"], "imported_at": prev[0]["imported_at"], "file_name": prev[0]["file_name"]},
            "new": len(new), "gone": len(gone), "changed": len(changed), "became_rejected": len(worse),
            "new_rows": new[:100], "gone_rows": gone[:100], "changed_rows": changed[:200]}


@router.post("/portfolio/auto-import")
def run_auto_import():
    res = auto_import()
    return res or {"imported": [], "failed": [], "note": f"новых файлов в {INBOX} нет"}
