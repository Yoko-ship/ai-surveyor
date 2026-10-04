"""
Мелкое форматирование текста акта: суммы и проценты (в том числе «нет данных»), множители, знаки, даты,
списки разделов.
"""
import re
from datetime import date
from typing import Optional

from .. import act_texts as tx
from ..act_texts import money, pct, t


# строки раздела 1 по виду объекта (30.09.2026): у оборудования — паспорт оборудования, у зданий — адрес, кадастр,
# площади, конструкция, год постройки, этажность; у техники и транспорта — как раньше. «Данные недоступны» — не
# больше трёх строк (сначала ключевые признаки), остальное — одной строкой «Не указано: …».
ROWS_BY_GROUP = {
    "equipment": ["object_type", "manufacturer", "model", "serial_no", "year", "location"],
    "property": ["object_type", "location", "cadastre_no", "total_area", "useful_area", "land_area", "construction",
                 "year", "floors"],
}


def _lower_first(s: str) -> str:
    """Подпись поля внутри фразы — со строчной: «Срок кредита, месяцев» → «срок кредита, месяцев»;
    сокращения (ИНН, PML) не трогаются."""
    s = str(s or "")
    return s[:1].lower() + s[1:] if len(s) > 1 and s[0].isupper() and s[1].islower() else s


def _ddmmyyyy(iso) -> str:
    try:
        return date.fromisoformat(str(iso)).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return str(iso or "")


def money_k(v: float, lang: str) -> str:
    """Сумма с копейками (тийинами), если они есть: 47 397 852 345,04 сум; целая — без дроби."""
    x = round(float(v), 2)
    if x == int(x):
        return money(x, lang)
    cur = {"ru": "сум", "uz": "soʻm", "en": "UZS"}[tx.lang_of(lang)]
    return tx._num(x, tx.lang_of(lang), 2) + tx.NBSP + cur


def _money_na(x, lang: str) -> str:
    """Сумма или «нет данных» (None)."""
    return money(x, lang) if x is not None else t("na", lang)


def _pct_na(x, lang: str) -> str:
    """Процент или «нет данных» (None)."""
    return pct(x, lang) if x is not None else t("na", lang)


def _signed(x, lang: str) -> str:
    if x is None:
        return t("na", lang)
    return ("−" if x < 0 else "+") + money(abs(x), lang)


# --------------------------------------------------------------------------- #
#  Раздел 4: аналитика риска (30.09.2026) — таблицы, строки и JSON analytics
# --------------------------------------------------------------------------- #

def _mult(x, lang: str) -> str:
    """Множитель без лишних нулей: 1,2 / 0,85 / 1."""
    s = f"{float(x):.4f}".rstrip("0").rstrip(".")
    return s if lang == "en" else s.replace(".", ",")


def _spct(x, lang: str, digits: int = 1) -> str:
    """Изменение в процентах со знаком: +20 % / −25 %."""
    if x is None:
        return t("na", lang)
    v = round(float(x), digits)
    sign = "+" if v > 0 else ("−" if v < 0 else "")
    return sign + tx.pct_fixed(abs(v), lang, digits if v != int(v) else 0)


def _spp(x, lang: str) -> str:
    """Процентные пункты со знаком: +0,1092."""
    v = round(float(x or 0), 4)
    return ("+" if v > 0 else ("−" if v < 0 else "")) + _mult(abs(v), lang)


def _smoney(x, lang: str) -> str:
    return _signed(x, lang) if x else money(0, lang)


def _li(title: str, items: list, table: Optional[dict] = None, sources: Optional[list] = None,
        notes: Optional[list] = None) -> dict:
    """Список раздела: строки для экрана и Word; table — та же информация таблицей (Word, PDF);
    sources — строки «Источник: …» (под таблицей, на экране — плашка .srcbar по JSON analytics)."""
    li = {"title": title, "items": list(items) + list(notes or []) + list(sources or [])}
    if table:
        li["table"] = table
        li["notes"] = list(notes or [])
        li["sources"] = list(sources or [])
    return li


def _date(iso) -> str:
    return _ddmmyyyy(str(iso)[:10]) if iso else ""


def _cap(x: str) -> str:
    """Первая буква — заглавная (фраза после точки)."""
    return x[:1].upper() + x[1:] if x else x


_CYR = re.compile(r"[А-Яа-яЁё]")
