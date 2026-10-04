"""Регионы: коды словаря интерфейса, особые регионы (вся республика, «Другое»), регион для модулей и подпись."""
import re
from typing import Optional

from .. import i18n

from .common import REGION_ALL, REGION_OTHER


def _region_names() -> dict:
    """Коды регионов из словаря интерфейса (tg.act.reg.*) → их названия на всех языках."""
    out = {}
    try:
        for key in i18n.keys("ru"):
            if key.startswith("tg.act.reg."):
                code = key[len("tg.act.reg."):]
                out[code] = {lg: i18n.t(key, lg) for lg in i18n.LANGS}
    except Exception as e:                   # словарь не прочитан — регион печатается как введён
        print("акт: словарь регионов не прочитан:", type(e).__name__)
    return out


def _fold(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("ʻ", "'").replace("‘", "'").replace("`", "'")).strip().lower()


def region_code(text: str) -> Optional[str]:
    """Регион кодом (tashkent_region) или каноническим названием на любом из трёх языков → код; иначе None."""
    names = _region_names()
    s = _fold(text)
    if s in names:
        return s
    for code, by_lang in names.items():
        if any(_fold(v) == s for v in by_lang.values()):
            return code
    return None


def region_scope(must: dict) -> Optional[str]:
    """Особые регионы (02.10.2026): republic — вся республика (uz_all), outside — территория вне Узбекистана
    (other, текстом); обычный регион — None."""
    code = must.get("region_code")
    return "republic" if code == REGION_ALL else ("outside" if code == REGION_OTHER else None)


def region_for_modules(must: dict) -> str:
    """
    Регион для risk_analytics, risk_stats и market_picture: они узнают регион по русскому названию
    (market_picture.resolve_region). Код из списка экрана (tashkent_region) → «Ташкентская область»;
    не из списка — как ввёл сотрудник. «Другое» (вне Узбекистана) — республика как нейтральное значение для расчёта
    (модулю нужен регион); статистика региона и НАПП по регионам такому акту не показываются (_region_scope_stats),
    поправка региона 0 (_fork_prepare, scope), фон региона к факторам не берётся (region_for_stats).
    """
    code = must.get("region_code") or region_code(must.get("region") or "")
    if code == REGION_OTHER:
        code = REGION_ALL
    if code:
        got = i18n.t(f"tg.act.reg.{code}", "ru")
        if got and not got.startswith("tg.act."):
            return got
    return must.get("region") or ""


def region_for_stats(must: dict) -> str:
    """Регион для фона открытых данных (stat.uz) к факторам объекта: вне Узбекистана — пусто (не применяются)."""
    return "" if region_scope(must) == "outside" else region_for_modules(must)


def region_label(must: dict, lang: str) -> str:
    """Регион на языке акта, если есть перевод в словаре; иначе — как ввёл сотрудник. «Другое» — территория,
    как её ввёл сотрудник (region_text)."""
    code = must.get("region_code")
    if code == REGION_OTHER and must.get("region_text"):
        return must["region_text"]
    if code:
        got = i18n.t(f"tg.act.reg.{code}", lang)
        if got and not got.startswith("tg.act."):
            return got
    return must.get("region") or ""
