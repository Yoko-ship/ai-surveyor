"""
Сервер ИИ-сюрвейера. Запуск: run.bat  (или  uvicorn app.main:app --port 8000)

Точки подключения (все отдают JSON, документация — /docs):
  GET  /health                          — жив ли сервер
  GET  /reference/{products|classes|perils|coefficients|checklists|rules}
  POST /calculate                       — расчёт без сохранения (для форм и проверок «на лету»)
  POST /requests                        — запрос от филиала: сохранить, посчитать, вернуть карточку
  GET  /requests, GET /requests/{id}    — список и карточка
  POST /requests/{id}/documents         — загрузить документ
  POST /requests/{id}/photos            — фото объекта; GET /requests/{id}/photos, GET/DELETE /photos/{id}
  POST /requests/{id}/documents/upload  — техпаспорт или кадастр (PDF/фото), сразу разбирается
  POST /documents/parse                 — разбор загруженного документа; GET /requests/{id}/documents/fields
  GET  /requests/{id}/checklist         — чек-лист документов: что получено
  GET  /valuation/norms, /valuation/settings — нормы износа и настройки оценки (POST — правка)
  POST /requests/{id}/decision          — решение андеррайтера
  GET  /analytics/summary               — аналитика запросов
  POST /admin/tariff-versions, /admin/min-rates, /admin/coefficients, /admin/products, PUT /admin/financials
  GET  /requests/{id}/explain           — объяснение расчёта клиенту (ИИ, без него — шаблон)
  GET  /llm/status, POST /llm/ping, GET /llm/calls — состояние и журнал обращений к ИИ
  GET  /deploy/status, /deploy/settings, /deploy/checklist, /deploy/schema-check, POST /deploy/backup
  POST /tg/auth                         — вход мини-приложения Telegram
  GET  /ui — экран агента, GET /admin — админка, GET /admin/deploy — запуск, GET /tg — мини-апп
"""
import json
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import db
from .engine import Input, calculate

ROOT = Path(__file__).resolve().parent.parent
UPLOADS = ROOT / "data" / "uploads"

app = FastAPI(title="ИИ-сюрвейер INSON", version="0.1",
              description="Расчёт ставки, проверки по законодательству и тарифной политике, документы, аналитика.")


sys.path.insert(0, str(ROOT / "tools"))
import market_stats  # noqa: E402  (tools/market_stats.py)

REFRESH_EVERY_SEC = 24 * 3600
_refresh_state = {"last": None, "log": [], "running": False}


def _refresh_job():
    """Агент-статистик: сам проверяет сайт НАПП, забирает новые отчёты и обновляет ряд."""
    if _refresh_state["running"]:
        return
    _refresh_state["running"] = True
    try:
        _refresh_state["log"] = market_stats.refresh()
        _refresh_state["last"] = db.now()
        with db.tx() as con:
            db.audit(con, "агент-статистик", "обновление рыночной статистики", "market_stats",
                     _refresh_state["log"][-1])
    except Exception as e:  # ошибка сети не должна ронять сервер
        _refresh_state["log"] = [f"ошибка: {e}"]
    finally:
        _refresh_state["running"] = False


def _scheduler():
    time.sleep(60)               # даём серверу подняться
    while True:
        _refresh_job()
        time.sleep(REFRESH_EVERY_SEC)


def _inbox_watcher():
    """Агент-статистик: раз в 10 минут смотрит папку data/inbox/portfolio — новые выгрузки договоров
    импортируются сами и связываются с прошлыми загрузками по номеру договора."""
    time.sleep(90)
    while True:
        try:
            from . import history
            res = history.auto_import()
            if res:
                with db.tx() as con:
                    db.audit(con, "агент-статистик", "автоимпорт выгрузок", "portfolio", res)
        except Exception as e:
            print("автоимпорт:", e)
        time.sleep(600)


@app.on_event("startup")
def startup():
    db.ensure_schema()
    UPLOADS.mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "photos").mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_scheduler, daemon=True, name="stats-refresh").start()
    threading.Thread(target=_inbox_watcher, daemon=True, name="inbox-watcher").start()
    from . import team
    team.start_scheduler()
    try:                                   # открытые данные агентства статистики: раз в сутки
        from . import statagency
        statagency.start_scheduler()
    except Exception as e:                 # модуль или сеть не готовы — сервер всё равно поднимается
        print("расписание агентства статистики не запущено:", e)
    try:                                   # слежение за законодательством: раз в сутки, 06:30
        from . import lawwatch
        lawwatch.start_scheduler()
    except Exception as e:
        print("расписание слежения за законодательством не запущено:", e)


# ---------- модели входа ----------

class Credit(BaseModel):
    loan_amount: float
    collateral_value: float = 0
    policyholder_is_bank: bool = True
    payer_is_bank: bool = True


class CalcIn(BaseModel):
    product_code: str
    class_code: Optional[str] = None          # если не задан — первый класс продукта
    object_type: str = "Склад"
    value_amount: float = Field(gt=0)
    sum_insured: float = Field(gt=0)
    term_days: int = 365
    factors: dict = {}
    perils_included: Optional[list] = None
    docs_received: list = []
    applied_rate_pct: Optional[float] = None
    manual_reason: str = ""
    premium_paid: bool = False
    disclosure_done: bool = False
    credit: Optional[Credit] = None
    takaful: bool = False
    payer_type: Optional[str] = None
    object_key: Optional[str] = None          # ключ объекта: подтянуть последнюю оценку стоимости
    valuation_id: Optional[int] = None        # или конкретная оценка


class RequestIn(CalcIn):
    external_no: Optional[str] = None         # номер договора в учётной системе
    branch: Optional[str] = None
    policyholder: Optional[str] = None
    beneficiary: Optional[str] = None
    insured_person: Optional[str] = None
    agent_eais_id: Optional[str] = None
    address: Optional[str] = None
    region: Optional[str] = None
    seismic_zone: Optional[int] = None
    term_from: Optional[str] = None
    term_to: Optional[str] = None


class Decision(BaseModel):
    who: str
    decision: str                              # 'утверждено' | 'отклонено' | 'на доработку'
    comment: str = ""


def to_input(con, c: CalcIn) -> Input:
    pcs = db.rows(con, "SELECT class_code FROM product_classes WHERE product_code=? ORDER BY part_no", c.product_code)
    if not pcs:
        raise HTTPException(404, f"Продукт {c.product_code} не найден")
    cls = c.class_code or pcs[0]["class_code"]
    val = None
    try:                                       # оценка стоимости — необязательный модуль
        from .valuation import valuation_for_engine, latest_valuation
        if c.valuation_id:
            v = db.rows(con, "SELECT * FROM valuations WHERE id=?", c.valuation_id)
            if v:
                val = {"id": v[0]["id"], "value": v[0]["ai_value"], "method": v[0]["method"],
                       "method_version": v[0]["method_version"], "as_of": v[0]["created_at"],
                       "confirmed_by": v[0]["confirmed_by_underwriter"]}
        elif c.object_key:
            val = valuation_for_engine(con, key=c.object_key)
    except Exception:                          # модуль оценки не должен ронять расчёт
        val = None
    return Input(product_code=c.product_code, class_code=cls, object_type=c.object_type,
                 value_amount=c.value_amount, sum_insured=c.sum_insured, term_days=c.term_days,
                 factors=c.factors, perils_included=c.perils_included, docs_received=c.docs_received,
                 applied_rate_pct=c.applied_rate_pct, manual_reason=c.manual_reason,
                 premium_paid=c.premium_paid, disclosure_done=c.disclosure_done,
                 credit=c.credit.model_dump() if c.credit else None, takaful=c.takaful, payer_type=c.payer_type, valuation=val)


# ---------- справочники ----------

@app.get("/health")
def health():
    with db.tx() as con:
        n = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    return {"status": "ok", "products": n}


REF_SQL = {
    "products": "SELECT p.*, (SELECT group_concat(class_code) FROM product_classes pc WHERE pc.product_code=p.code) AS classes FROM products p ORDER BY code",
    "classes": "SELECT * FROM classes ORDER BY CAST(code AS INTEGER), code",
    "perils": "SELECT * FROM perils ORDER BY class_code, base_share DESC",
    "coefficients": "SELECT * FROM coefficients ORDER BY factor_code, id",
    "checklists": "SELECT * FROM checklists ORDER BY scope_type, scope_code, id",
    "rules": "SELECT * FROM rules",
    "min_rates": "SELECT m.*, v.level, v.name AS version, v.effective_from FROM min_rates m JOIN tariff_versions v ON v.id=m.tariff_version_id ORDER BY product_code",
}


@app.get("/reference/{name}")
def reference(name: str):
    if name not in REF_SQL:
        raise HTTPException(404, "Нет такого справочника")
    with db.tx() as con:
        return db.rows(con, REF_SQL[name])


# ---------- расчёт ----------

@app.post("/calculate")
def calc(body: CalcIn):
    with db.tx() as con:
        ref = db.load_reference(con)
        return calculate(ref, to_input(con, body))


@app.post("/requests")
def create_request(body: RequestIn):
    with db.tx() as con:
        ref = db.load_reference(con)
        inp = to_input(con, body)
        result = calculate(ref, inp)
        agent_id = None
        if body.agent_eais_id:
            a = db.rows(con, "SELECT id FROM agents WHERE eais_id=?", body.agent_eais_id)
            agent_id = a[0]["id"] if a else None
        cur = con.execute(
            "INSERT INTO requests (external_no, branch, product_code, policyholder, beneficiary, agent_id, created_at, status)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (body.external_no, body.branch, body.product_code, body.policyholder, body.beneficiary, agent_id,
             db.now(), "посчитан"))
        rid = cur.lastrowid
        cur = con.execute(
            "INSERT INTO objects (request_id, object_type, address, region, seismic_zone, value_amount, sum_insured, franchise, attributes)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, body.object_type, body.address, body.region, body.seismic_zone, body.value_amount, body.sum_insured,
             body.factors.get("franchise"), json.dumps({"factors": body.factors, "term_from": body.term_from,
                                                        "term_to": body.term_to, "insured_person": body.insured_person,
                                                        "credit": body.credit.model_dump() if body.credit else None,
                                                        "premium_paid": body.premium_paid}, ensure_ascii=False)))
        oid = cur.lastrowid
        for p in result["perils_included"]:
            con.execute("INSERT INTO object_perils VALUES (?,?,1)", (oid, p))
        tv = db.rows(con, "SELECT id FROM tariff_versions WHERE level='компания' ORDER BY effective_from DESC LIMIT 1")
        r = result["rates"]
        cur = con.execute(
            "INSERT INTO calculations (request_id, object_id, tariff_version_id, net_rate_pct, risk_load_pct, cat_load_pct,"
            " gross_rate_pct, min_rate_pct, applied_rate_pct, premium, verdict, explanation, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, oid, tv[0]["id"] if tv else None, r["net_pct"], r["risk_load_pct"], r["cat_load_pct"],
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
        return {"request_id": rid, "calculation_id": cid, **result}


@app.get("/requests")
def list_requests(status: Optional[str] = None, branch: Optional[str] = None, limit: int = 100):
    sql = """SELECT r.id, r.external_no, r.branch, r.product_code, r.policyholder, r.created_at, r.status,
                    c.applied_rate_pct, c.gross_rate_pct, c.min_rate_pct, c.premium, c.verdict
             FROM requests r LEFT JOIN calculations c ON c.request_id = r.id WHERE 1=1"""
    args = []
    if status:
        sql += " AND r.status=?"; args.append(status)
    if branch:
        sql += " AND r.branch=?"; args.append(branch)
    sql += " ORDER BY r.id DESC LIMIT ?"; args.append(limit)
    with db.tx() as con:
        return db.rows(con, sql, *args)


def _card(con, rid: int) -> dict:
    req = db.rows(con, "SELECT * FROM requests WHERE id=?", rid)
    if not req:
        raise HTTPException(404, "Запрос не найден")
    obj = db.rows(con, "SELECT * FROM objects WHERE request_id=?", rid)
    calc = db.rows(con, "SELECT * FROM calculations WHERE request_id=? ORDER BY id DESC LIMIT 1", rid)
    checks = db.rows(con, "SELECT rule_code, status, detail FROM check_results WHERE calculation_id=?",
                     calc[0]["id"]) if calc else []
    recs = db.rows(con, "SELECT kind, text, premium_delta FROM recommendations WHERE calculation_id=?",
                   calc[0]["id"]) if calc else []
    docs = db.rows(con, "SELECT id, doc_name, received, file_path FROM documents WHERE request_id=?", rid)
    return {"request": req[0], "object": obj[0] if obj else None, "calculation": calc[0] if calc else None,
            "checks": checks, "recommendations": recs, "documents": docs}


@app.get("/requests/{rid}")
def get_request(rid: int):
    with db.tx() as con:
        return _card(con, rid)


@app.post("/requests/{rid}/documents")
async def upload_document(rid: int, doc_name: str, file: UploadFile = File(...)):
    with db.tx() as con:
        _card(con, rid)
        folder = UPLOADS / str(rid)
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / Path(file.filename).name
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        con.execute("INSERT INTO documents (request_id, doc_name, file_path, received) VALUES (?,?,?,1)",
                    (rid, doc_name, str(dest.relative_to(ROOT))))
        db.audit(con, "api", "загружен документ", f"request:{rid}", {"doc": doc_name, "file": file.filename})
    return {"ok": True, "stored": str(dest.relative_to(ROOT))}


@app.post("/requests/{rid}/decision")
def decide(rid: int, d: Decision):
    with db.tx() as con:
        _card(con, rid)
        con.execute("UPDATE requests SET status=? WHERE id=?", (d.decision, rid))
        db.audit(con, d.who, "решение андеррайтера", f"request:{rid}", {"decision": d.decision, "comment": d.comment})
    return {"ok": True, "status": d.decision}


# ---------- аналитика ----------

@app.get("/analytics/summary")
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


# ---------- рыночная статистика (динамика) ----------

@app.get("/market/rows")
def market_rows():
    with db.tx() as con:
        return db.rows(con, """SELECT row_key, row_name, COUNT(*) points, MAX(report_date) last
                               FROM market_stats GROUP BY row_key ORDER BY row_key""")


@app.get("/market/series")
def market_series(row: str = "cls8_9"):
    """Ряд по строке отчёта: нарастающий итог, квартальные приросты и производные показатели."""
    with db.tx() as con:
        pts = db.rows(con, "SELECT * FROM market_stats WHERE row_key=? ORDER BY report_date", row)
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
                    "annual_rate": ann, "source": p["source_file"]})
        prev = p
    return {"row": row, "name": pts[0]["row_name"] if pts else row, "points": out}


@app.post("/market/refresh")
def market_refresh():
    threading.Thread(target=_refresh_job, daemon=True).start()
    return {"started": True, "note": "проверяю сайт НАПП и обновляю ряд; результат — в /market/status"}


@app.get("/market/status")
def market_status():
    with db.tx() as con:
        n = con.execute("SELECT COUNT(*) FROM market_stats").fetchone()[0]
        dates = [r["report_date"] for r in db.rows(con, "SELECT DISTINCT report_date FROM market_stats ORDER BY 1")]
    return {"points": n, "dates": dates, "last_refresh": _refresh_state["last"],
            "running": _refresh_state["running"], "log": _refresh_state["log"],
            "schedule": "каждые 24 часа, первая проверка через минуту после старта"}


@app.get("/stats", response_class=HTMLResponse)
def stats_page():
    return (ROOT / "app" / "stats.html").read_text(encoding="utf-8")


# ---------- офис агентов ----------

AGENTS = [
    ("lead", "Руководитель", "планирует работу, сводит отчёты, ведёт вопросы к заказчику", ["admin"]),
    ("law", "Юрист", "читает законы и акты, извлекает правила проверок, ищет недостающее на lex.uz", ["юрист"]),
    ("data", "Статистик", "забирает отчёты НАПП, ведёт динамику рынка, готовит калибровку", ["агент-статистик", "system"]),
    ("actuary", "Актуарий", "считает ставки, проверки, удержание и ёмкость", ["api", "экран агента"]),
    ("backend", "Разработчик", "база, сервер, интеграция с учётной системой", ["backend"]),
    ("ui", "Дизайнер", "экраны агента и админки, паутина, динамика", ["ui"]),
    ("reviewer", "Контролёр", "проверяет расчёты и отчёты перед сдачей", ["reviewer"]),
]


@app.get("/agents/status")
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
        from . import lawwatch
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


@app.get("/office", response_class=HTMLResponse)
def office_page():
    return (ROOT / "app" / "office.html").read_text(encoding="utf-8")


# ---------- ёмкость, резервы, удержание ----------

from . import capacity as cap  # noqa: E402


@app.get("/capacity")
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


@app.post("/admin/reserves")
def add_reserves(rows_in: list[ReserveRow]):
    with db.tx() as con:
        for r in rows_in:
            con.execute("INSERT OR REPLACE INTO reserve_reports VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (r.report_date, r.scope_type, r.scope_code, r.rnp, r.rzu, r.rpnu, r.stab, r.cat_reserve,
                         r.other, r.base_premium_12m, r.source))
        db.audit(con, "admin", "отчёт о резервах", rows_in[0].report_date if rows_in else None, {"rows": len(rows_in)})
    return {"ok": True, "rows": len(rows_in)}


@app.post("/admin/assets")
def add_assets(rows_in: list[AssetRow]):
    with db.tx() as con:
        for r in rows_in:
            con.execute("INSERT OR REPLACE INTO allocated_assets VALUES (?,?,?,?)",
                        (r.report_date, r.category, r.amount, r.is_liquid))
        db.audit(con, "admin", "выделенные активы", rows_in[0].report_date if rows_in else None, {"rows": len(rows_in)})
    return {"ok": True, "rows": len(rows_in)}


@app.put("/admin/solvency")
def set_solvency(s: SolvencyRow):
    with db.tx() as con:
        con.execute("INSERT OR REPLACE INTO solvency_reports VALUES (?,?,?,?,?,?,?,?,?)",
                    (s.report_date, s.own_funds, s.deductions, s.premiums_12m, s.claims_36m, s.claims_36m_net,
                     s.min_capital, s.top5_liabilities, s.source))
        db.audit(con, "admin", "платёжеспособность", s.report_date, s.model_dump())
    return {"ok": True}


@app.get("/capacity-page", response_class=HTMLResponse)
def capacity_page():
    return (ROOT / "app" / "capacity.html").read_text(encoding="utf-8")


# ---------- админка ----------

class TariffVersion(BaseModel):
    level: str                                 # 'компания' | 'регулятор'
    name: str
    document_ref: str = ""
    effective_from: str                        # ГГГГ-ММ-ДД


class MinRate(BaseModel):
    tariff_version_id: int
    product_code: str
    class_code: Optional[str] = None
    payer_type: Optional[str] = None
    min_rate_pct: float


class Coefficient(BaseModel):
    factor_code: str
    factor_name: str
    class_code: Optional[str] = "8"
    option_code: str
    option_name: str
    multiplier: float
    calibrated: int = 0
    source: str = ""


class Product(BaseModel):
    code: str
    name: str
    classes: list
    rate_text: str = ""
    commission_text: str = ""
    commission_pct: Optional[float] = None
    pricing_mode: str = "ставка"
    min_rate_pct: Optional[float] = None
    tariff_version_id: Optional[int] = None


class Financials(BaseModel):
    report_date: str
    own_funds: float
    reserves: float
    source: str = ""


@app.post("/admin/tariff-versions")
def add_version(v: TariffVersion):
    with db.tx() as con:
        cur = con.execute("INSERT INTO tariff_versions (level, name, document_ref, effective_from) VALUES (?,?,?,?)",
                          (v.level, v.name, v.document_ref, v.effective_from))
        db.audit(con, "admin", "новая версия тарифов", f"version:{cur.lastrowid}", v.model_dump())
        return {"id": cur.lastrowid}


@app.post("/admin/min-rates")
def add_min_rate(m: MinRate):
    with db.tx() as con:
        con.execute("INSERT INTO min_rates (tariff_version_id, product_code, class_code, payer_type, min_rate_pct) VALUES (?,?,?,?,?)",
                    (m.tariff_version_id, m.product_code, m.class_code, m.payer_type, m.min_rate_pct))
        db.audit(con, "admin", "минимальная ставка", m.product_code, m.model_dump())
    return {"ok": True}


@app.post("/admin/coefficients")
def add_coefficient(c: Coefficient):
    with db.tx() as con:
        con.execute("DELETE FROM coefficients WHERE factor_code=? AND option_code=? AND class_code IS ?",
                    (c.factor_code, c.option_code, c.class_code))
        con.execute("INSERT INTO coefficients (factor_code,factor_name,class_code,option_code,option_name,multiplier,calibrated,source)"
                    " VALUES (?,?,?,?,?,?,?,?)", (c.factor_code, c.factor_name, c.class_code, c.option_code,
                                                 c.option_name, c.multiplier, c.calibrated, c.source))
        db.audit(con, "admin", "коэффициент", f"{c.factor_code}/{c.option_code}", c.model_dump())
    return {"ok": True}


@app.post("/admin/products")
def add_product(p: Product):
    with db.tx() as con:
        con.execute("INSERT OR REPLACE INTO products (code,name,rate_text,commission_text,commission_pct,pricing_mode,is_general,status)"
                    " VALUES (?,?,?,?,?,?,0,'тест')", (p.code, p.name, p.rate_text, p.commission_text, p.commission_pct, p.pricing_mode))
        con.execute("DELETE FROM product_classes WHERE product_code=?", (p.code,))
        for i, cl in enumerate(p.classes, start=1):
            con.execute("INSERT INTO product_classes VALUES (?,?,?)", (p.code, cl, i))
        if p.min_rate_pct is not None and p.tariff_version_id:
            con.execute("INSERT INTO min_rates (tariff_version_id, product_code, class_code, payer_type, min_rate_pct) VALUES (?,?,?,?,?)",
                        (p.tariff_version_id, p.code, p.classes[0], None, p.min_rate_pct))
        db.audit(con, "admin", "продукт", p.code, p.model_dump())
    return {"ok": True, "status": "тест — до утверждения виден только андеррайтеру"}


@app.put("/admin/financials")
def set_financials(f: Financials):
    with db.tx() as con:
        con.execute("INSERT OR REPLACE INTO company_financials VALUES (?,?,?,?)",
                    (f.report_date, f.own_funds, f.reserves, f.source))
        db.audit(con, "admin", "финансовые показатели", f.report_date, f.model_dump())
    return {"ok": True, "risk_limit": 0.2 * (f.own_funds + f.reserves)}


@app.get("/audit")
def audit_log(limit: int = 200):
    with db.tx() as con:
        return db.rows(con, "SELECT * FROM audit ORDER BY id DESC LIMIT ?", limit)


# ---------- экраны ----------

SIDEBAR_ITEMS = [("/", "Главная"), ("/ui", "Новый расчёт"), ("/admin", "Запросы и админка"), ("/portfolio", "Портфель"),
                 ("/approvals", "Согласования"), ("/tasks-page", "Задачи команде"), ("/reports-page", "Ежедневный доклад"),
                 ("/stats", "Динамика рынка"), ("/capacity-page", "Ёмкость и удержание"), ("/calibration", "Калибровка"),
                 ("/graph", "Паутина знаний"), ("/law-feed", "Законодательство"), ("/office", "Офис агентов"),
                 ("/admin/deploy", "Запуск и обслуживание"), ("/docs", "API")]


def sidebar(active: str) -> str:
    links = "".join(f'<a href="{h}"{" class=on" if h == active else ""}>{t}</a>' for h, t in SIDEBAR_ITEMS)
    return f"""<style>
    :root{{--sb-paper:#0F1418;--sb-line:#26303A;--sb-muted:#8E9BA6;--sb-accent:#2ED3A2;--sb-dim:#1E8F70}}
    #sb{{position:fixed;left:0;top:0;bottom:0;width:220px;background:var(--sb-paper);border-right:1px solid var(--sb-line);
        padding:18px 12px;display:flex;flex-direction:column;gap:4px;font:14px Manrope,system-ui,sans-serif;z-index:50;overflow-y:auto}}
    #sb .brand{{display:flex;align-items:center;gap:10px;padding:6px 8px 18px;color:#E6ECF0}}
    #sb .brand i{{width:34px;height:34px;border-radius:50%;background:var(--sb-dim);display:grid;place-items:center;font-weight:800;color:#0F1418;font-style:normal}}
    #sb .brand b{{display:block;font-size:15px}} #sb .brand span{{font-size:11px;color:var(--sb-muted)}}
    #sb a{{color:var(--sb-muted);text-decoration:none;padding:9px 12px;border-radius:10px;font-weight:600;font-size:13.5px}}
    #sb a.on{{color:var(--sb-accent);border:1px solid var(--sb-dim);background:rgba(46,211,162,.08)}}
    body{{padding-left:220px !important}}
    @media (max-width:760px){{#sb{{position:static;width:auto;flex-direction:row;overflow-x:auto;border-right:0;border-bottom:1px solid var(--sb-line);padding:10px}}
      #sb .brand{{display:none}} body{{padding-left:0 !important}}}}
    </style>
    <nav id="sb"><div class="brand"><i>S</i><div><b>Сюрвейер</b><span>INSON</span></div></div>{links}</nav>"""


# Мост UI_BRIDGE убран 20.09.2026: экран /ui (docs/agent_ui.html) сам показывает рынок,
# предупредительные мероприятия, сохраняет запрос и даёт ссылку на PDF.


@app.get("/theme.js")
def theme_js():
    from fastapi.responses import Response
    return Response((ROOT / "app" / "theme.js").read_text(encoding="utf-8"), media_type="application/javascript")


@app.get("/ui", response_class=HTMLResponse)
def ui():
    html = (ROOT / "docs" / "agent_ui.html").read_text(encoding="utf-8")
    return ("<!doctype html><html><meta charset='utf-8'>"
            "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Manrope:wght@600;800&display=swap'>"
            + sidebar("/ui") + html + "<script src='/theme.js'></script>")


@app.get("/admin", response_class=HTMLResponse)
def admin():
    html = (ROOT / "app" / "admin.html").read_text(encoding="utf-8")
    return html.replace("<div class=\"wrap\">", sidebar("/admin") + "<div class=\"wrap\">", 1)


@app.get("/graph", response_class=HTMLResponse)
def graph():
    """Паутина знаний в стиле Obsidian: продукты → классы → учётные группы → правила РНП."""
    html = (ROOT / "docs" / "tariff_web.html").read_text(encoding="utf-8")
    back = ('<a href="/stats" style="position:fixed;right:16px;bottom:14px;z-index:9;font:600 13px Manrope,system-ui;'
            'color:#2ED3A2;text-decoration:none;background:#161C21;border:1px solid #26303A;border-radius:999px;padding:7px 13px">← к приложению</a>')
    return "<!doctype html><meta charset='utf-8'>" + html + back


# модули, которые делают агенты: портфельный аудит, предложение клиенту, калибровка
for _mod, _name in (("portfolio", "portfolio_router"), ("proposal", "proposal_router"), ("calibration", "calibration_router"),
                    ("history", "history_router"), ("auth", "auth_router"), ("team", "team_router"),
                    ("knowledge", "knowledge_router"), ("office_api", "office_router"),
                    ("photos", "photos_router"), ("valuation", "valuation_router"),
                    ("docparse", "docparse_router"), ("statagency", "statagency_router"),
                    ("approvals", "approvals_router"), ("lawwatch", "lawwatch_router"),
                    ("llm", "llm_router"), ("deploy", "deploy_router"), ("telegram", "telegram_router")):
    try:
        _m = __import__(f"app.{_mod}", fromlist=["router"])
        app.include_router(_m.router)
    except Exception as _e:  # модуль ещё не готов — сервер всё равно поднимается
        print(f"модуль {_mod} не подключён: {_e}")


@app.get("/accumulation")
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


@app.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "app" / "home.html").read_text(encoding="utf-8").replace("<!--SIDEBAR-->", sidebar("/"))
