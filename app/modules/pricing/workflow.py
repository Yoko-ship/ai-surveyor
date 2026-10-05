"""Сбор входа расчёта и пояснений; ядро расчёта не знает о хранении и HTTP."""
from app import db
from app.errors import NotFoundError
from app.modules.pricing.engine import Input, calculate
from app.modules.pricing.schemas import CalcIn

def to_input(con, c: CalcIn) -> Input:
    pcs = db.rows(con, "SELECT class_code FROM product_classes WHERE product_code=? ORDER BY part_no", c.product_code)
    if not pcs:
        raise NotFoundError(f"Продукт {c.product_code} не найден")
    cls = c.class_code or pcs[0]["class_code"]
    val = None
    if c.valuation_id:
        v = db.rows(con, "SELECT * FROM valuations WHERE id=?", c.valuation_id)
        if v:
            val = {"id": v[0]["id"], "value": v[0]["ai_value"], "method": v[0]["method"],
                   "method_version": v[0]["method_version"], "as_of": v[0]["created_at"],
                   "confirmed_by": v[0]["confirmed_by_underwriter"]}
    elif c.object_key:
        from app.valuation import valuation_for_engine
        val = valuation_for_engine(con, key=c.object_key)
    return Input(product_code=c.product_code, class_code=cls, object_type=c.object_type,
                 value_amount=c.value_amount, sum_insured=c.sum_insured, term_days=c.term_days,
                 factors=c.factors, perils_included=c.perils_included, docs_received=c.docs_received,
                 applied_rate_pct=c.applied_rate_pct, manual_reason=c.manual_reason,
                 premium_paid=c.premium_paid, disclosure_done=c.disclosure_done,
                 credit=c.credit.model_dump() if c.credit else None, takaful=c.takaful, payer_type=c.payer_type, valuation=val)


def _probability(con, inp, result: dict, body) -> dict:
    """
    Вероятность подтверждения к итогу расчёта (app/analysis.py). Ничего не сохраняет:
    запись появляется, когда запрос уходит на согласование (app/approvals.assign).
    Ошибка модуля расчёт не отменяет — она идёт в журнал.
    """
    from app import analysis, outcomes
    try:
        res = analysis.probability(
            con, calc=result, valuation=inp.valuation, documents=None, history=None,
            context={"product_code": body.product_code, "branch": getattr(body, "branch", None),
                     "class_code": inp.class_code, "sum_insured": body.sum_insured,
                     "factors": body.factors or {},
                     "franchise": (body.factors or {}).get("franchise")})
        return outcomes.view(res)
    except Exception as e:
        db.audit(con, "api", "вероятность не рассчитана", None, {"ошибка": str(e)})
        return outcomes.empty("модуль вероятности вернул ошибку")


def quote(con, body):
    """Один расчёт для веб-запроса, сохранённого запроса и фонового сценария."""
    inp = to_input(con, body)
    result = calculate(db.load_reference(con), inp)
    return inp, result

def calculate_quote(body):
    with db.tx() as con:
        inp, result = quote(con, body)
        return {**result, "probability": _probability(con, inp, result, body)}
