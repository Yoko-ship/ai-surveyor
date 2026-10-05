"""Атомарное сохранение запроса и его расчёта, чтение карточки."""
import json
from app import db
from app.errors import NotFoundError

def save_calculation(con, body, result, author_id=None):
    agent_id = None
    if body.agent_eais_id:
        a = db.rows(con, "SELECT id FROM agents WHERE eais_id=?", body.agent_eais_id)
        agent_id = a[0]["id"] if a else None
    cur = con.execute(
        "INSERT INTO requests (external_no, branch, product_code, policyholder, beneficiary, agent_id,"
        " created_by_user_id, created_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
        (body.external_no, body.branch, body.product_code, body.policyholder, body.beneficiary, agent_id,
         author_id, db.now(), "посчитан"))
    rid = cur.lastrowid
    cur = con.execute(
        "INSERT INTO objects (request_id, object_type, address, region, seismic_zone, value_amount, sum_insured, franchise, attributes)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (rid, body.object_type, body.address, body.region, body.seismic_zone, body.value_amount, body.sum_insured,
         body.factors.get("franchise"), json.dumps({"factors": body.factors, "term_from": body.term_from,
                                                    "term_to": body.term_to, "insured_person": body.insured_person,
                                                    "credit": body.credit.model_dump() if body.credit else None,
                                                    "premium_paid": body.premium_paid,
                                                    # условия расчёта: без них запрос не пересчитать
                                                    # тем же движком (app/outcomes.py)
                                                    "term_days": body.term_days,
                                                    "payer_type": body.payer_type,
                                                    "takaful": body.takaful,
                                                    "disclosure_done": body.disclosure_done,
                                                    "object_key": body.object_key}, ensure_ascii=False)))
    oid = cur.lastrowid
    for p in result["perils_included"]:
        con.execute("INSERT INTO object_perils VALUES (?,?,1)", (oid, p))
    from app import min_rates as _mrs
    # действующая на сегодня версия уровня «компания», без версий правок по одному продукту
    tv_id = _mrs.current_version(con, "компания")
    r = result["rates"]
    cur = con.execute(
        "INSERT INTO calculations (request_id, object_id, tariff_version_id, net_rate_pct, risk_load_pct, cat_load_pct,"
        " gross_rate_pct, min_rate_pct, applied_rate_pct, premium, verdict, explanation, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, oid, tv_id, r["net_pct"], r["risk_load_pct"], r["cat_load_pct"],
         r["technical_pct"], r["min_pct"], r["applied_pct"], result["premium"], result["verdict"],
         json.dumps({"chain": result["explanation"], "manual": r["manual"], "manual_reason": body.manual_reason},
                    ensure_ascii=False), db.now()))
    cid = cur.lastrowid
    for c in result["checks"]:
        rule = c["rule"] if db.rows(con, "SELECT 1 FROM rules WHERE code=?", c["rule"]) else None
        if rule:
            con.execute("INSERT INTO check_results (calculation_id, rule_code, status, detail) VALUES (?,?,?,?)",
                        (cid, rule, "нарушено" if c["status"] != "ok" else "пройдено", c["title"] + ": " + c["detail"]))
    for t in result["recommendations"]:
        con.execute("INSERT INTO recommendations (calculation_id, kind, text, premium_delta) VALUES (?,?,?,?)",
                    (cid, t["kind"], t["text"], t["premium_delta"]))
    db.audit(con, body.branch or "api", "создан запрос", f"request:{rid}", {"verdict": result["verdict"]})
    return rid, cid

def _card(con, rid: int) -> dict:
    req = db.rows(con, "SELECT * FROM requests WHERE id=?", rid)
    if not req:
        raise NotFoundError("Запрос не найден")
    obj = db.rows(con, "SELECT * FROM objects WHERE request_id=?", rid)
    calc = db.rows(con, "SELECT * FROM calculations WHERE request_id=? ORDER BY id DESC LIMIT 1", rid)
    checks = db.rows(con, "SELECT rule_code, status, detail FROM check_results WHERE calculation_id=?",
                     calc[0]["id"]) if calc else []
    recs = db.rows(con, "SELECT kind, text, premium_delta FROM recommendations WHERE calculation_id=?",
                   calc[0]["id"]) if calc else []
    docs = db.rows(con, "SELECT id, doc_name, received, file_path FROM documents WHERE request_id=?", rid)
    from app import outcomes
    return {"request": req[0], "object": obj[0] if obj else None, "calculation": calc[0] if calc else None,
            "checks": checks, "recommendations": recs, "documents": docs,
            "probability": outcomes.summary(con, rid)}
