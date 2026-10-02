"""
Рэнкинг страховых компаний snsratings.uz в базе сервера (таблица company_rankings).

ensure_loaded() — при старте сервера (app/main.py): если таблица пуста или текст рэнкинга
(library/03_Рынок_НАПП/Рэнкинг snsratings/*.txt, попадает в образ) изменился — разбирает его
tools/ranking_parse.py и загружает; заодно обновляет строку INSON в company_financials, если там
временные цифры (ручной ввод из админки не перезаписывается).

Чтение для ИИ специалиста (app/market_expert.py): company_rows(), indicator_rows(), latest_period().
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from . import db

ROOT = Path(__file__).resolve().parent.parent
SOURCE_LABEL = "Рэнкинг snsratings.uz, II кв. 2026 (по данным openinfo.uz, НАПП)"
SOURCE_URL = "https://snsratings.uz"


def _tool():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import tools.ranking_parse as rp
    return rp


def ensure_loaded(force: bool = False) -> dict:
    """Загрузка при старте. Возвращает {'loaded': bool, 'reason': …, ...}."""
    rp = _tool()
    path = rp.source_file()
    if not path:
        return {"loaded": False, "reason": "нет текста рэнкинга в образе"}
    with db.tx() as con:
        need = force or rp.needs_load(con, path)
    if not need:
        return {"loaded": False, "reason": "уже загружен"}
    rep = rp.load(path=path)
    msg = (f"рэнкинг страховщиков {rep['period']}: {rep['written']} строк, {rep['indicators']} показателей, "
           f"{rep['companies']} компаний; сверка с ВСЕГО — расхождений {len(rep['sum'])}; {rep.get('financials')}")
    print(msg)
    try:                                   # заметки и факты о рынке: раздел «Финансы страховщиков» — в фоне
        from . import market_knowledge
        market_knowledge.trigger()
    except Exception as e:
        print("знания о рынке после рэнкинга не пересобраны:", e)
    return {"loaded": True, "reason": msg, "report": {k: rep[k] for k in ("period", "written", "indicators",
                                                                            "companies", "sum", "issues")}}


def has_table(con) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='company_rankings'").fetchone())


def latest_period(con) -> Optional[str]:
    if not has_table(con):
        return None
    r = con.execute("SELECT MAX(report_period) FROM company_rankings").fetchone()
    return r[0] if r and r[0] else None


def company_rows(con, company: str, period: Optional[str] = None) -> dict:
    """{indicator_code: строка} одной компании ('INSON AJ') за период (по умолчанию — последний)."""
    period = period or latest_period(con)
    if not period:
        return {}
    return {r["indicator_code"]: dict(r) for r in con.execute(
        "SELECT * FROM company_rankings WHERE report_period=? AND company=?", (period, company))}


def indicator_rows(con, code: str, period: Optional[str] = None) -> list:
    """Все компании по показателю (без строки ВСЕГО), по месту на текущий период."""
    period = period or latest_period(con)
    if not period:
        return []
    return [dict(r) for r in con.execute(
        "SELECT * FROM company_rankings WHERE report_period=? AND indicator_code=? AND company<>'ВСЕГО' "
        "ORDER BY rank_cur IS NULL, rank_cur, company", (period, code))]


def total_row(con, code: str, period: Optional[str] = None) -> Optional[dict]:
    period = period or latest_period(con)
    if not period:
        return None
    r = con.execute("SELECT * FROM company_rankings WHERE report_period=? AND indicator_code=? AND company='ВСЕГО'",
                    (period, code)).fetchone()
    return dict(r) if r else None


def companies_count(con, code: str, period: Optional[str] = None) -> int:
    period = period or latest_period(con)
    r = con.execute("SELECT COUNT(*) FROM company_rankings WHERE report_period=? AND indicator_code=? "
                    "AND company<>'ВСЕГО'", (period, code)).fetchone()
    return r[0] if r else 0
