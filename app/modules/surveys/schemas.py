"""Данные создания страхового запроса."""
from typing import Optional
from app.modules.pricing.schemas import CalcIn

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
