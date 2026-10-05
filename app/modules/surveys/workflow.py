"""Сценарий создания запроса: расчёт и запись в одной транзакции."""
from app import db
from app.modules.pricing.workflow import quote, _probability
from app.modules.surveys.repository import save_calculation

def create_survey(body, author_id=None):
    with db.tx() as con:
        inp, result = quote(con, body)
        rid, cid = save_calculation(con, body, result, author_id)
        return {"request_id": rid, "calculation_id": cid, **result,
                "probability": _probability(con, inp, result, body)}
