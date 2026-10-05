"""Маршруты расчёта и тарифных справочников."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app import db
from app.auth import require
from app.modules.pricing.schemas import CalcIn
from app.modules.pricing.workflow import calculate_quote
router = APIRouter()
ADMIN = "админ"

CLASSES_SHOWN, CLASSES_ORDER = db.CLASSES_SHOWN, db.CLASSES_ORDER
REF_SQL = {
    "products": "SELECT p.*, (SELECT group_concat(class_code) FROM product_classes pc WHERE pc.product_code=p.code) AS classes FROM products p ORDER BY code",
    "classes": f"SELECT * FROM classes WHERE {CLASSES_SHOWN} ORDER BY {CLASSES_ORDER}",
    "perils": "SELECT * FROM perils ORDER BY class_code, base_share DESC",
    "coefficients": "SELECT * FROM coefficients ORDER BY factor_code, id",
    "checklists": "SELECT * FROM checklists ORDER BY scope_type, scope_code, id",
    "rules": "SELECT * FROM rules",
    "min_rates": "SELECT m.*, v.level, v.name AS version, v.effective_from FROM min_rates m JOIN tariff_versions v ON v.id=m.tariff_version_id ORDER BY product_code",
}


@router.get("/reference/{name}")
def reference(name: str):
    if name == "min-rates":
        # минимальные ставки страховщика с версиями (app/min_rates.py); путь общий с остальными справочниками
        from app import min_rates
        return min_rates.list_min_rates()
    if name not in REF_SQL:
        raise HTTPException(404, "Нет такого справочника")
    with db.tx() as con:
        return db.rows(con, REF_SQL[name])


@router.post("/calculate")
def calc(body: CalcIn):
    return calculate_quote(body)

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


@router.post("/admin/tariff-versions")
def add_version(v: TariffVersion, user: dict = Depends(require(ADMIN))):
    with db.tx() as con:
        cur = con.execute("INSERT INTO tariff_versions (level, name, document_ref, effective_from) VALUES (?,?,?,?)",
                          (v.level, v.name, v.document_ref, v.effective_from))
        db.audit(con, user["login"], "новая версия тарифов", f"version:{cur.lastrowid}", v.model_dump())
        db.reference_changed(con)        # расчёт должен сразу видеть новое значение
        return {"id": cur.lastrowid}


@router.post("/admin/min-rates")
def add_min_rate(m: MinRate, user: dict = Depends(require(ADMIN))):
    with db.tx() as con:
        con.execute("INSERT INTO min_rates (tariff_version_id, product_code, class_code, payer_type, min_rate_pct) VALUES (?,?,?,?,?)",
                    (m.tariff_version_id, m.product_code, m.class_code, m.payer_type, m.min_rate_pct))
        db.audit(con, user["login"], "минимальная ставка", m.product_code, m.model_dump())
        db.reference_changed(con)        # расчёт должен сразу видеть новое значение
    return {"ok": True}


@router.post("/admin/coefficients")
def add_coefficient(c: Coefficient, user: dict = Depends(require(ADMIN))):
    with db.tx() as con:
        con.execute("DELETE FROM coefficients WHERE factor_code=? AND option_code=? AND class_code IS ?",
                    (c.factor_code, c.option_code, c.class_code))
        con.execute("INSERT INTO coefficients (factor_code,factor_name,class_code,option_code,option_name,multiplier,calibrated,source)"
                    " VALUES (?,?,?,?,?,?,?,?)", (c.factor_code, c.factor_name, c.class_code, c.option_code,
                                                 c.option_name, c.multiplier, c.calibrated, c.source))
        db.audit(con, user["login"], "коэффициент", f"{c.factor_code}/{c.option_code}", c.model_dump())
        db.reference_changed(con)        # расчёт должен сразу видеть новое значение
    return {"ok": True}


@router.post("/admin/products")
def add_product(p: Product, user: dict = Depends(require(ADMIN))):
    with db.tx() as con:
        con.execute("INSERT OR REPLACE INTO products (code,name,rate_text,commission_text,commission_pct,pricing_mode,is_general,status)"
                    " VALUES (?,?,?,?,?,?,0,'тест')", (p.code, p.name, p.rate_text, p.commission_text, p.commission_pct, p.pricing_mode))
        con.execute("DELETE FROM product_classes WHERE product_code=?", (p.code,))
        for i, cl in enumerate(p.classes, start=1):
            con.execute("INSERT INTO product_classes VALUES (?,?,?)", (p.code, cl, i))
        if p.min_rate_pct is not None and p.tariff_version_id:
            con.execute("INSERT INTO min_rates (tariff_version_id, product_code, class_code, payer_type, min_rate_pct) VALUES (?,?,?,?,?)",
                        (p.tariff_version_id, p.code, p.classes[0], None, p.min_rate_pct))
        db.audit(con, user["login"], "продукт", p.code, p.model_dump())
        db.reference_changed(con)        # расчёт должен сразу видеть новое значение
    return {"ok": True, "status": "тест — до утверждения виден только андеррайтеру"}
