"""
Лёгкий движок сюрвейерского акта (ТЗ «Мини-приложение ИИ-сюрвейер. Лёгкая версия», 2.0 от 29.09.2026).

Чистые функции: без сети, без модели и без обращения к базе. Всё нужное приходит аргументами:
справочник движка (engine.Reference из db.load_reference), настройки акта (act_settings, см. DEFAULT_SETTINGS),
пороги франшизы (risk_analytics.load_thresholds — только чтение). Тексты по-человечески собирает app/act.py;
здесь — коды, числа и параметры.

1. risk_level — три уровня по четырём признакам ТЗ 8.1 (состояние, место, убытки за 3 года, документы).
   Каждый признак даёт +1 (повышает), −1 (снижает), 0 (не влияет) или «неизвестно».
   net = сумма; net ≤ level_rule.low_max_net → низкий; net ≥ level_rule.high_min_net → высокий;
   иначе умеренный. Если известно меньше level_rule.min_known признаков — умеренный.
   Пороги — в настройках (таблица act_settings), calibrated = 0.
2. rate — базовая ставка из того же справочника, что у калькулятора (engine.rate_for без
   коэффициентов: базовая ставка класса и типа объекта → рисковая надбавка → нагрузка), либо ставка продукта
   по тарифной политике (настройка base_source = product_rate). Поправка по уровню: adj_pct (0/20/50 %).
   Не ниже минимальной ставки продукта (engine.min_rate). Премия = сумма × ставка × дни / 365.
   Продукты «по программе», «по согласованию», «генеральный договор» — ставка не определена.
   Обязательные виды — только ставка акта (минимум регулятора или число из текста тарифа), без поправок.
3. value_check — отношение суммы к стоимости (ТЗ 8.3) и ориентир «цена минус износ».
4. franchise — только при основании (ТЗ 8.4); размер — вилка из порогов franchise_by_level.
5. clauses, required_views, discrepancies, decision — по ТЗ 5, 8.5, 8.6, 7.
"""
import re
from datetime import date
from typing import Optional

from .engine import NEGOTIATED_MODES, STATUTORY_MODE, Input, min_rate, premium_of, rate_for

CALIBRATED = 0
LEVELS = ("low", "moderate", "high")
# уровни лёгкой версии → названия уровней в порогах франшизы (risk_analytics.DEFAULT_THRESHOLDS)
RA_LEVEL = {"low": "Низкий", "moderate": "Умеренный", "high": "Высокий"}

DEFAULT_SETTINGS = {
    "calibrated": 0,
    "source": "экспертные значения до калибровки (ТЗ лёгкой версии 2.0 от 29.09.2026)",
    "level_rule": {"low_max_net": -2, "high_min_net": 2, "min_known": 2},
    "adj_pct": {"low": 0, "moderate": 20, "high": 50},
    # technical — техническая ставка калькулятора; product_rate — ставка продукта по тарифной политике
    # По умолчанию — ставка тарифной политики: так считает заказчик (образец акта 29.09.2026:
    # спецтехника 0,35 % × 1,2 за умеренный риск = 0,42 %).
    "base_source": "product_rate",
    "new_object_years": 1,          # «объект новый»: не старше стольких лет
    "losses_high_count": 2,         # убытков за 3 года, с которых признак «повышает»
    "decline_min_up": 4,            # отказ — только когда сработали все четыре повышающих признака
    "value_ok_pct": [90, 100],      # сумма к стоимости «в норме»
    "wear_pct_per_year": {"building": 3, "vehicle": 20, "computer": 20, "equipment": 15, "other": 15},
    "insurer_name": "",
    # пределы загрузки и распознавания (app/act.py): защита сервера, а не тариф
    "limits": {
        "max_image_mp": 50,             # картинка больше стольких мегапикселей отклоняется до раскрытия
        "pdf_max_pages": 10,            # PDF с большим числом страниц не принимается
        "guest_photos_per_hour": 60,    # фото в час на одного гостя (по числу файлов)
        "ai_calls_per_hour": 120,       # распознаваний в час на весь сервер
        "ai_timeout_sec": 20,           # ожидание ответа модели на один запрос, одна попытка
        "ai_deadline_sec": 25,          # общий срок распознавания
        "ai_max_mb": 10,                # суммарный объём вложений в одном запросе к модели
        "send_per_hour": 10,            # отправок акта ботом в час на пользователя
    },
}

LIMIT_BOUNDS = {"max_image_mp": (1, 200), "pdf_max_pages": (1, 100), "guest_photos_per_hour": (1, 10000),
                "ai_calls_per_hour": (1, 100000), "ai_timeout_sec": (5, 120), "ai_deadline_sec": (5, 180),
                "ai_max_mb": (1, 15), "send_per_hour": (1, 1000)}

VIEWS = ("front", "back", "left", "right", "plate", "odometer", "document", "interior", "facade", "roof",
         "electrical", "fire_safety", "general", "installation", "packaging", "marking", "transport", "other")
SOURCES = ("document", "plate", "marking", "input", "photo")          # порядок = приоритет показа
FIELD_KEYS = ("object_type", "brand", "model", "manufacture_date", "year", "serial_no", "manufacturer",
              "engine_no", "engine_model", "engine_power", "curb_mass", "payload", "dimensions", "color",
              "mileage", "location")
LOCATIONS = ("open_area", "construction", "port", "guarded", "closed_storage", "other")
LOC_UP = ("open_area", "construction", "port")
LOC_DOWN = ("guarded", "closed_storage")

REQUIRED_VIEWS = {
    "special": ["front", "back", "left", "right", "plate", "odometer", "document"],
    "vehicle": ["front", "back", "left", "right", "plate", "odometer", "document"],
    "property": ["facade", "roof", "interior", "electrical", "fire_safety"],
    "equipment": ["general", "plate", "installation"],
    "cargo": ["packaging", "marking", "transport"],
    "liability": [],
    "other": [],
}
# какой снимок закрывает нужный ракурс: общий вид оборудования снимают и «спереди», и «сбоку»
VIEW_COVERS = {"general": {"general", "front", "back", "left", "right"}, "facade": {"facade", "front"},
               "transport": {"transport"}}

# какие поля показывать в разделе 1 по группе объекта
FIELDS_BY_GROUP = {
    "special": ["object_type", "brand", "model", "year", "serial_no", "manufacturer", "engine_model",
                "engine_no", "engine_power", "curb_mass", "payload", "dimensions", "color", "mileage", "location"],
    "vehicle": ["object_type", "brand", "model", "year", "serial_no", "manufacturer", "engine_model",
                "engine_no", "engine_power", "curb_mass", "payload", "color", "mileage", "location"],
    "equipment": ["object_type", "brand", "model", "year", "serial_no", "manufacturer", "engine_power",
                  "dimensions", "location"],
    "property": ["object_type", "year", "dimensions", "location"],
    "cargo": ["object_type", "curb_mass", "dimensions", "location"],
    "liability": ["object_type", "location"],
    "other": ["object_type", "brand", "model", "year", "serial_no", "location"],
}
# признаки с высокой ценой ошибки: их отсутствие попадает в «что проверить»
KEY_FIELDS = {"special": ["serial_no", "year", "model"], "vehicle": ["serial_no", "year", "model"],
              "equipment": ["serial_no", "year", "model"], "property": ["year"], "cargo": [], "liability": [],
              "other": []}

SPECIAL_WORDS = ("спецтех", "кран", "экскават", "бульдоз", "погрузч", "грейдер", "каток", "автовыш", "бетон",
                 "буров", "трактор", "комбайн", "crane", "excavat", "bulldoz", "loader", "grader", "tractor",
                 "special")
EQUIP_WORDS = ("оборуд", "станок", "машины и", "генератор", "equipment", "machine")
COMPUTER_WORDS = ("компьют", "ноутбук", "сервер", "computer", "laptop", "server")
FURNITURE_WORDS = ("мебел", "furniture")


# ================================================================================================
#  Группа объекта, ракурсы, оговорки
# ================================================================================================

def object_group(class_code: Optional[str], object_type: str = "", class_hint: str = "") -> str:
    """Группа для ракурсов и оговорок: special | vehicle | property | equipment | cargo | liability | other."""
    cls = str(class_code or "").strip()
    text = f"{object_type or ''} {class_hint or ''}".lower()
    hint = str(class_hint or "").strip().lower()
    if cls in ("3", "4") or (not cls and hint in ("vehicle", "special_machinery")):
        special = hint == "special_machinery" or any(w in text for w in SPECIAL_WORDS)
        return "special" if special else "vehicle"
    if cls == "7" or (not cls and hint == "cargo"):
        return "cargo"
    if cls in ("8", "9") or (not cls and hint in ("building", "equipment")):
        return "equipment" if hint == "equipment" or any(w in text for w in EQUIP_WORDS) else "property"
    if cls in ("10", "11", "12", "13", "13з"):
        return "liability"
    return "other"


def required_views(group: str) -> list:
    return list(REQUIRED_VIEWS.get(group) or [])


def missing_views(group: str, seen) -> list:
    seen = set(seen or [])
    out = []
    for v in required_views(group):
        if not (VIEW_COVERS.get(v, {v}) & seen):
            out.append(v)
    return out


def clauses(group: str, catalog: dict) -> list:
    """Оговорки из готового списка по группе объекта (docs/act_clauses.json). Все — экспертные."""
    items = ((catalog or {}).get("groups") or {}).get(group) or []
    return [dict(c, expert=True, calibrated=CALIBRATED) for c in items if isinstance(c, dict) and c.get("code")]


# ================================================================================================
#  Разбор значений
# ================================================================================================

def to_year(value) -> Optional[int]:
    """Год из «2026», «2026-03», «03.2026», «март 2026 г.»; неправдоподобное — None."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        y = int(value)
    else:
        m = re.search(r"(?<!\d)(19[5-9]\d|20\d\d)(?!\d)", str(value))
        if not m:
            return None
        y = int(m.group(1))
    return y if 1950 <= y <= date.today().year + 1 else None


_UNIT_MASS = {"kg": 1.0, "кг": 1.0, "t": 1000.0, "т": 1000.0, "тонн": 1000.0, "ton": 1000.0, "tonn": 1000.0}
_UNIT_POWER = {"kw": 1.0, "квт": 1.0, "hp": 0.7355, "л.с": 0.7355, "лс": 0.7355, "ps": 0.7355, "ot kuchi": 0.7355}


def to_number(value) -> Optional[float]:
    """Число из строки «36 170 кг», «38,600 kg», «248 kW», «2.5 t». Разряды пробелом или запятой."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).replace(" ", " ").replace(" ", " ").replace(" ", " ")
    m = re.search(r"\d[\d\s.,]*", s)
    if not m:
        return None
    raw = re.sub(r"\s+", "", m.group(0)).rstrip(".,")
    if "," in raw and "." in raw:
        raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") else raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        parts = raw.split(",")
        raw = raw.replace(",", "") if all(len(p) == 3 for p in parts[1:]) else raw.replace(",", ".")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _unit_factor(value, table: dict) -> Optional[float]:
    s = str(value or "").lower().replace(" ", "")
    for unit in sorted(table, key=len, reverse=True):
        if unit.replace(" ", "") in s:
            return table[unit]
    return None


def _norm_text(value, drop=()) -> str:
    s = str(value or "").upper()
    for d in drop:
        if d:
            s = s.replace(str(d).upper(), " ")
    return re.sub(r"[\s\-_/.,:;·•]+", "", s)


def _same(key: str, a, b, brands=()) -> bool:
    """Одно ли это значение. Числа с единицами сравниваются после приведения; строки — по знакам."""
    if key in ("curb_mass", "payload", "engine_power", "mileage"):
        table = _UNIT_POWER if key == "engine_power" else _UNIT_MASS
        na, nb = to_number(a), to_number(b)
        if na is None or nb is None:
            return _norm_text(a) == _norm_text(b)
        fa, fb = _unit_factor(a, table), _unit_factor(b, table)
        if fa is None or fb is None or fa == fb:
            return abs(na - nb) < 1e-9
        va, vb = na * fa, nb * fb                  # разные единицы: пересчёт даёт округление — допуск 1 %
        return abs(va - vb) <= 0.01 * max(abs(va), abs(vb))
    if key == "year":
        return to_year(a) == to_year(b)
    drop = brands if key in ("model", "engine_model") else ()
    return _norm_text(a, drop) == _norm_text(b, drop)


# ================================================================================================
#  1. Уровень риска
# ================================================================================================

def risk_level(inputs: dict, settings: Optional[dict] = None) -> dict:
    """
    inputs: {"inspected": bool (фото распознаны), "damages": [..], "condition": "new|good|worn|damaged"|None,
             "year": int|None, "location": код LOCATIONS|None, "guard": bool|None,
             "losses_count": int|None, "documents": bool, "today": date}
    Возвращает {"level", "net", "up", "down", "known", "few", "factors": [{"code", "sign", "params"}],
                "rule", "calibrated"}. sign: up | down | neutral | unknown.
    """
    st = merge_settings(settings)
    rule = st["level_rule"]
    today = inputs.get("today") or date.today()
    factors = []

    # состояние объекта
    damages = [d for d in (inputs.get("damages") or []) if d]
    cond = str(inputs.get("condition") or "").lower() or None
    year = inputs.get("year")
    new = year is not None and today.year - int(year) <= int(st["new_object_years"])
    if damages or cond == "damaged":
        factors.append({"code": "f_cond_damage", "sign": "up", "params": {"n": len(damages) or 1}})
    elif cond == "worn":
        factors.append({"code": "f_cond_worn", "sign": "up", "params": {}})
    elif new and (inputs.get("inspected") or cond in ("new", "good")):
        factors.append({"code": "f_cond_new", "sign": "down", "params": {"year": year}})
    elif inputs.get("inspected") or cond in ("new", "good"):
        # год неизвестен — «не новый» утверждать нельзя
        code = "f_cond_neutral" if year is not None else "f_cond_no_year"
        factors.append({"code": code, "sign": "neutral", "params": {}})
    else:
        factors.append({"code": "f_cond_unknown", "sign": "unknown", "params": {}})

    # место эксплуатации
    loc = inputs.get("location")
    guard = inputs.get("guard")
    if loc in LOC_UP and guard:
        factors.append({"code": "f_loc_guarded_open", "sign": "neutral", "params": {"place": loc}})
    elif loc in LOC_UP:
        factors.append({"code": "f_loc_up", "sign": "up", "params": {"place": loc}})
    elif loc in LOC_DOWN:
        factors.append({"code": "f_loc_down", "sign": "down", "params": {"place": loc}})
    elif guard:
        factors.append({"code": "f_loc_down", "sign": "down", "params": {"place": "guarded"}})
    elif loc:
        factors.append({"code": "f_loc_neutral", "sign": "neutral", "params": {"place": loc}})
    else:
        factors.append({"code": "f_loc_unknown", "sign": "unknown", "params": {}})

    # история убытков
    n = inputs.get("losses_count")
    if n is None:
        factors.append({"code": "f_loss_unknown", "sign": "unknown", "params": {}})
    elif n >= int(st["losses_high_count"]):
        factors.append({"code": "f_loss_up", "sign": "up", "params": {"n": n}})
    elif n == 0:
        factors.append({"code": "f_loss_down", "sign": "down", "params": {}})
    else:
        factors.append({"code": "f_loss_neutral", "sign": "neutral", "params": {"n": n}})

    # документы: не представлены — это тоже известный факт
    if inputs.get("documents"):
        factors.append({"code": "f_docs_down", "sign": "down", "params": {}})
    else:
        factors.append({"code": "f_docs_up", "sign": "up", "params": {}})

    up = sum(1 for f in factors if f["sign"] == "up")
    down = sum(1 for f in factors if f["sign"] == "down")
    known = sum(1 for f in factors if f["sign"] != "unknown")
    net = up - down
    few = known < int(rule["min_known"])
    if few:
        level = "moderate"
    elif net <= int(rule["low_max_net"]):
        level = "low"
    elif net >= int(rule["high_min_net"]):
        level = "high"
    else:
        level = "moderate"
    return {"level": level, "net": net, "up": up, "down": down, "known": known, "few": few,
            "factors": factors, "rule": dict(rule), "calibrated": CALIBRATED}


# ================================================================================================
#  2. Ставка и премия
# ================================================================================================

def match_object_type(ref, class_code: str, object_kind_type: Optional[str] = None,
                      object_type: Optional[str] = None) -> Optional[str]:
    """Тип объекта справочника базовых ставок: явный ввод → подсказка модели → None (средняя по классу)."""
    types = [ot for (c, ot) in ref.base_rates if c == class_code]
    low = {ot.lower(): ot for ot in types}
    for cand in (object_type, object_kind_type):
        if cand and str(cand).strip().lower() in low:
            return low[str(cand).strip().lower()]
    return None


def _percent_in(text: str) -> Optional[float]:
    """Одна ставка в тексте тарифа: «0,4% (ПКМ №532)» → 0.4. Несколько чисел или ни одного — None."""
    found = re.findall(r"(\d+(?:[.,]\d+)?)\s*%", str(text or ""))
    if len(found) != 1:
        return None
    return float(found[0].replace(",", "."))


def rate(ref, product: Optional[dict], class_code: str, level: str, sum_insured: float,
         term_days: int = 365, object_type: Optional[str] = None, payer_type: Optional[str] = None,
         settings: Optional[dict] = None) -> dict:
    """
    product: {"code", "name", "pricing_mode", "rate_text"} или None (выбран только класс).
    Возвращает {"mode": tariff|statutory|undefined, "base_pct", "base_source", "adj_pct", "calc_pct",
                "applied_pct", "min_pct", "min_applied", "premium", "term_days", "object_type",
                "class_code", "product_code", "calibrated", "how": [{"code", "params"}]}.
    """
    st = merge_settings(settings)
    code = (product or {}).get("code")
    mode_raw = (product or {}).get("pricing_mode")
    out = {"mode": "tariff", "base_pct": None, "base_source": None, "adj_pct": None, "calc_pct": None,
           "applied_pct": None, "min_pct": None, "min_applied": False, "premium": None,
           "term_days": int(term_days), "object_type": object_type, "class_code": class_code,
           "product_code": code, "pricing_mode": mode_raw, "calibrated": CALIBRATED, "how": [],
           "engine_chain": []}

    if mode_raw in NEGOTIATED_MODES:
        out["mode"] = "undefined"
        out["how"].append({"code": "how_undefined", "params": {"code": code,
                                                               "rate_text": product.get("rate_text") or mode_raw}})
        return out

    if mode_raw == STATUTORY_MODE:
        out["mode"] = "statutory"
        out["adj_pct"] = 0
        mr = min_rate(ref, code, payer_type)
        by_act = mr["regulator"] if mr["regulator"] is not None else _percent_in(product.get("rate_text"))
        ref_txt = product.get("rate_text") or code
        if by_act is None:
            out["mode"] = "statutory_undefined"
            out["how"].append({"code": "how_statutory_na", "params": {"ref": ref_txt}})
            return out
        out.update(base_pct=by_act, calc_pct=by_act, applied_pct=round(by_act, 4), min_pct=by_act,
                   base_source="act")
        out["premium"] = round(premium_of(out["applied_pct"], sum_insured, term_days))
        out["how"].append({"code": "how_statutory", "params": {"rate": by_act, "ref": ref_txt}})
        out["how"].append({"code": "how_premium", "params": {"sum": sum_insured, "rate": out["applied_pct"],
                                                             "days": term_days, "premium": out["premium"]}})
        return out

    # ставка: базовая → поправка по уровню → не ниже минимума продукта
    mr = min_rate(ref, code, payer_type) if code else {"floor": None, "company": None}
    floor = mr["floor"]
    if st["base_source"] == "product_rate" and code and mr.get("company") is not None:
        base = float(mr["company"])
        out["base_source"] = "product_rate"
        out["how"].append({"code": "how_base_product", "params": {"base": base, "code": code}})
    else:
        has_class = any(c == class_code for (c, _) in ref.base_rates)
        if not has_class:
            out["mode"] = "undefined"
            out["how"].append({"code": "how_no_base", "params": {"cls": class_code}})
            return out
        # тот же расчёт, что у калькулятора, но без коэффициентов: одна поправка по уровню заменяет их все
        r = rate_for(ref, Input(product_code=code or "", class_code=class_code,
                                object_type=object_type or "", value_amount=1, sum_insured=1,
                                term_days=term_days, factors={}))
        base = r["gross_pct"]
        out["engine_chain"] = r["chain"]
        out["base_source"] = "technical"
        if object_type:
            out["how"].append({"code": "how_base_tech", "params": {"base": round(base, 4), "cls": class_code,
                                                                   "otype": object_type}})
        else:
            out["how"].append({"code": "how_base_tech_avg", "params": {"base": round(base, 4),
                                                                       "cls": class_code}})
    adj = float((st["adj_pct"] or {}).get(level, 0))
    calc = base * (1 + adj / 100)
    out["base_pct"] = round(base, 4)
    out["adj_pct"] = adj
    out["calc_pct"] = round(calc, 4)
    out["how"].append({"code": "how_adj", "params": {"level": level, "adj": adj}})
    applied = calc
    if floor is not None:
        out["min_pct"] = floor
        if calc + 1e-12 < floor:
            applied = floor
            out["min_applied"] = True
            out["how"].append({"code": "how_min_applied", "params": {"calc": round(calc, 4), "min": floor}})
        else:
            out["how"].append({"code": "how_min_ok", "params": {"min": floor}})
    else:
        out["how"].append({"code": "how_min_none", "params": {}})
    out["applied_pct"] = round(applied, 4)
    # премия считается от уже округлённой ставки — так ручной пересчёт по акту даёт ту же цифру
    out["premium"] = round(premium_of(out["applied_pct"], sum_insured, term_days))
    out["how"].append({"code": "how_premium", "params": {"sum": sum_insured, "rate": out["applied_pct"],
                                                         "days": term_days, "premium": out["premium"]}})
    return out


# ================================================================================================
#  3. Стоимость и страховая сумма
# ================================================================================================

def wear_kind(group: str, object_type: str = "") -> str:
    text = str(object_type or "").lower()
    if any(w in text for w in COMPUTER_WORDS):
        return "computer"
    if group in ("special", "vehicle"):
        return "vehicle"
    if group == "property":
        return "building"
    if group == "equipment" or any(w in text for w in FURNITURE_WORDS):
        return "equipment"
    return "other"


def value_check(sum_insured: float, value: float, settings: Optional[dict] = None,
                price_new: Optional[float] = None, purchase_year: Optional[int] = None,
                group: str = "other", object_type: str = "", today: Optional[date] = None) -> dict:
    """Отношение суммы к стоимости (ТЗ 8.3): 90–100 % в норме; < 90 % недострахование (ГК ст. 936);
    > 100 % превышение (ГК ст. 938). Ниже 100 %, но не ниже 90 % — в норме, но доля выплаты та же."""
    st = merge_settings(settings)
    lo, hi = st["value_ok_pct"]
    ratio = sum_insured / value * 100
    if ratio > hi + 1e-9:
        verdict, ref = "over", "ГК РУз, ст. 938"
    elif ratio < lo - 1e-9:
        verdict, ref = "under", "ГК РУз, ст. 936"
    else:
        verdict, ref = "normal", ("ГК РУз, ст. 936" if ratio < 100 - 1e-9 else None)
    out = {"ratio_pct": round(ratio, 2), "verdict": verdict, "legal_ref": ref,
           "diff": round(sum_insured - value) if verdict == "over" else None, "depreciated": None}
    if price_new and purchase_year:
        today = today or date.today()
        years = max(0, today.year - int(purchase_year))
        kind = wear_kind(group, object_type)
        per_year = float((st["wear_pct_per_year"] or {}).get(kind, st["wear_pct_per_year"].get("other", 15)))
        left = max(0.0, 1 - per_year / 100 * years)
        out["depreciated"] = {"price_new": price_new, "purchase_year": int(purchase_year), "years": years,
                              "kind": kind, "wear_pct_per_year": per_year, "value": round(price_new * left),
                              "calibrated": CALIBRATED}
    return out


# ================================================================================================
#  4. Франшиза
# ================================================================================================

def franchise(inputs: dict, level: str, thresholds: dict, statutory: bool = False,
              class_code: Optional[str] = None, sum_insured: float = 0) -> dict:
    """
    ТЗ 8.4: по умолчанию «не требуется». Основания: ≥ franchise_loss_count_high мелких убытков за 3 года;
    высокий уровень; явно преобладающий риск (inputs.dominant_risk); просьба клиента снизить премию.
    Размер — вилка franchise_by_level (пороги только читаются), с потолком класса franchise_class_caps.
    Вилка пустая — «рассмотреть франшизу, размер определяет андеррайтер».
    """
    if statutory:
        return {"needed": False, "code": "fr_statutory", "grounds": [], "size": None}
    th = thresholds or {}
    grounds = []
    small = inputs.get("small_count")
    need_small = int(th.get("franchise_loss_count_high", 2))
    if small is not None and small >= need_small:
        grounds.append({"code": "fr_g_small_losses", "params": {"n": small}})
    if level == "high":
        grounds.append({"code": "fr_g_level_high", "params": {}})
    if inputs.get("dominant_risk"):
        grounds.append({"code": "fr_g_dominant", "params": {"what": str(inputs["dominant_risk"])[:80]}})
    if inputs.get("want_lower_premium"):
        grounds.append({"code": "fr_g_client", "params": {}})
    if not grounds:
        return {"needed": False, "code": "fr_not_needed", "grounds": [], "size": None}
    band = (th.get("franchise_by_level") or {}).get(RA_LEVEL.get(level)) or []
    size = None
    try:
        lo, hi = float(band[0]), float(band[1])
    except (IndexError, TypeError, ValueError):
        lo = hi = 0.0
    cap = (th.get("franchise_class_caps") or {}).get(str(class_code or ""))
    if cap is not None:
        hi = min(hi, float(cap))
        lo = min(lo, hi)
    if hi > 0:
        size = {"from_pct": lo, "to_pct": hi, "from_amount": round(sum_insured * lo / 100),
                "to_amount": round(sum_insured * hi / 100), "cap_pct": cap,
                "source": "risk_thresholds.franchise_by_level", "calibrated": CALIBRATED}
    return {"needed": True, "code": "fr_advise_range" if size else "fr_advise_nosize",
            "grounds": grounds, "size": size}


# ================================================================================================
#  5. Расхождения
# ================================================================================================

COMPARE_KEYS = ("curb_mass", "model", "serial_no", "year", "engine_power", "engine_model")


def discrepancies(recognized: list, inputs: Optional[dict] = None) -> list:
    """
    Одно поле из разных источников (табличка / документ / маркировка / фото / ввод сотрудника).
    Год берётся и из «дата изготовления». Возвращает [{"key", "values": [{"source", "value"}], "priority"}].
    Приоритет — у документа с печатью производителя (ТЗ 8.6); решение — за андеррайтером.
    """
    inputs = inputs or {}
    brands = [str(r.get("value")) for r in recognized or [] if r.get("key") in ("brand", "manufacturer")
              and r.get("value")]
    by_key = {}
    for r in recognized or []:
        key = r.get("key")
        val = r.get("value")
        if val in (None, ""):
            continue
        if key == "manufacture_date":
            key = "year"
        if key not in COMPARE_KEYS:
            continue
        by_key.setdefault(key, []).append({"source": r.get("source") or "photo", "value": str(val)})
    if inputs.get("year"):
        by_key.setdefault("year", []).append({"source": "input", "value": str(inputs["year"])})
    out = []
    for key in COMPARE_KEYS:
        vals = by_key.get(key) or []
        # одинаковые значения из одного источника схлопываем
        uniq, seen = [], set()
        for v in vals:
            sig = (v["source"], _norm_text(v["value"]))
            if sig not in seen:
                seen.add(sig)
                uniq.append(v)
        if len(uniq) < 2:
            continue
        # группы одинаковых значений: в строке показываем каждое значение один раз с его источниками
        groups = []
        for v in uniq:
            for g in groups:
                if _same(key, g["value"], v["value"], brands):
                    if v["source"] not in g["sources"]:
                        g["sources"].append(v["source"])
                    break
            else:
                groups.append({"value": v["value"], "sources": [v["source"]]})
        if len(groups) < 2:
            continue
        groups.sort(key=lambda g: min(SOURCES.index(s) if s in SOURCES else 9 for s in g["sources"]))
        priority = "document" if any("document" in g["sources"] for g in groups) else None
        out.append({"key": key, "values": groups, "priority": priority})
    return out


# ================================================================================================
#  6. Решение
# ================================================================================================

def decision(risk: dict, rate_res: dict, value: dict, fr: dict, disc: list, inspection: dict,
             missing_key: list, settings: Optional[dict] = None) -> dict:
    """
    accept — низкий уровень и ни одного вопроса; decline — сработали все повышающие признаки
    (decline_min_up); иначе accept_with_clauses. checks — что проверить андеррайтеру до полиса.
    """
    st = merge_settings(settings)
    checks = []
    for d in disc:
        checks.append({"code": "c_disc", "params": {"key": d["key"]}})
    if not inspection.get("photos"):
        checks.append({"code": "c_no_inspection", "params": {}})
    elif not inspection.get("ai"):
        checks.append({"code": "c_ai_failed", "params": {}})
    elif inspection.get("missing_views"):
        checks.append({"code": "c_views", "params": {"views": inspection["missing_views"]}})
    if inspection.get("damages"):
        checks.append({"code": "c_damages", "params": {}})
    if not inspection.get("documents"):
        checks.append({"code": "c_docs", "params": {}})
    if value["verdict"] == "under":
        checks.append({"code": "c_under", "params": {}})
    elif value["verdict"] == "over":
        checks.append({"code": "c_over", "params": {}})
    if rate_res["mode"] in ("undefined", "statutory_undefined"):
        checks.append({"code": "c_rate_undefined", "params": {}})
    if rate_res["mode"] in ("statutory", "statutory_undefined"):
        checks.append({"code": "c_statutory", "params": {}})
    if fr.get("needed"):
        checks.append({"code": "c_franchise", "params": {}})
    if missing_key:
        checks.append({"code": "c_missing", "params": {"keys": list(missing_key)}})
    if inspection.get("ai") and inspection.get("recognized"):
        checks.append({"code": "c_confirm", "params": {}})

    if risk["up"] >= int(st["decline_min_up"]):
        code = "d_decline"
        checks.append({"code": "c_decline", "params": {}})
    elif risk["level"] == "low" and not [c for c in checks if c["code"] != "c_confirm"]:
        code = "d_accept"
    else:
        code = "d_accept_with_clauses"
    return {"code": code, "checks": checks}


# ================================================================================================
#  Настройки
# ================================================================================================

def merge_settings(custom: Optional[dict]) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else (list(v) if isinstance(v, list) else v))
           for k, v in DEFAULT_SETTINGS.items()}
    for k, v in (custom or {}).items():
        if k not in DEFAULT_SETTINGS:
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def check_settings(s: dict) -> list:
    """Ошибки в настройках (пустой список — всё верно). Проверяются типы и разумные пределы."""
    errs = []
    unknown = [k for k in s if k not in DEFAULT_SETTINGS]
    if unknown:
        errs.append("неизвестные настройки: " + ", ".join(unknown))
    m = merge_settings(s)
    rule = m["level_rule"]
    try:
        lo, hi, k = int(rule["low_max_net"]), int(rule["high_min_net"]), int(rule["min_known"])
        if not (-4 <= lo < hi <= 4):
            errs.append("level_rule: нужно −4 ≤ low_max_net < high_min_net ≤ 4")
        if not (1 <= k <= 4):
            errs.append("level_rule.min_known: от 1 до 4")
    except (KeyError, TypeError, ValueError):
        errs.append("level_rule: нужны целые low_max_net, high_min_net, min_known")
    adj = m["adj_pct"]
    try:
        vals = [float(adj[x]) for x in LEVELS]
        if any(v < 0 or v > 300 for v in vals) or not (vals[0] <= vals[1] <= vals[2]):
            errs.append("adj_pct: от 0 до 300 %, и не убывает от низкого к высокому")
    except (KeyError, TypeError, ValueError):
        errs.append("adj_pct: нужны числа для low, moderate, high")
    if m["base_source"] not in ("technical", "product_rate"):
        errs.append("base_source: technical или product_rate")
    try:
        lo_v, hi_v = [float(x) for x in m["value_ok_pct"]]
        if not (0 < lo_v <= hi_v <= 150):
            errs.append("value_ok_pct: [от, до] в процентах, 0 < от ≤ до ≤ 150")
    except (TypeError, ValueError):
        errs.append("value_ok_pct: пара чисел")
    for key, lo_b, hi_b in (("new_object_years", 0, 10), ("losses_high_count", 1, 20), ("decline_min_up", 1, 5)):
        try:
            if not (lo_b <= int(m[key]) <= hi_b):
                errs.append(f"{key}: от {lo_b} до {hi_b}")
        except (TypeError, ValueError):
            errs.append(f"{key}: целое число")
    try:
        if any(not (0 <= float(v) <= 100) for v in m["wear_pct_per_year"].values()):
            errs.append("wear_pct_per_year: от 0 до 100 % в год")
    except (TypeError, ValueError, AttributeError):
        errs.append("wear_pct_per_year: словарь чисел")
    if not isinstance(m["insurer_name"], str) or len(m["insurer_name"]) > 120:
        errs.append("insurer_name: строка до 120 знаков")
    lim = m["limits"] if isinstance(m["limits"], dict) else {}
    for key, (lo_b, hi_b) in LIMIT_BOUNDS.items():
        v = lim.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (lo_b <= v <= hi_b):
            errs.append(f"limits.{key}: число от {lo_b} до {hi_b}")
    extra = [k for k in lim if k not in LIMIT_BOUNDS]
    if extra:
        errs.append("limits: неизвестные ключи " + ", ".join(extra))
    if not errs and lim.get("ai_timeout_sec", 0) > lim.get("ai_deadline_sec", 0):
        errs.append("limits: ai_timeout_sec не больше ai_deadline_sec")
    return errs
