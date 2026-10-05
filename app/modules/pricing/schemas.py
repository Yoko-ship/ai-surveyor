"""Входные данные расчёта HTTP; независимы от регистрации приложения."""
from typing import Optional
from pydantic import BaseModel, Field

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
    factors: dict = Field(default_factory=dict)
    perils_included: Optional[list] = None
    docs_received: list = Field(default_factory=list)
    applied_rate_pct: Optional[float] = None
    manual_reason: str = ""
    premium_paid: bool = False
    disclosure_done: bool = False
    credit: Optional[Credit] = None
    takaful: bool = False
    payer_type: Optional[str] = None
    object_key: Optional[str] = None          # ключ объекта: подтянуть последнюю оценку стоимости
    valuation_id: Optional[int] = None        # или конкретная оценка
