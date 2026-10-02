"""
Автозаполнение акта по транспортному средству (02.10.2026): техпаспорт и фото → поля шага 2 и объекта.

Два источника:
  * техпаспорт с текстовым слоем (app/docparse.py, вид «техпаспорт»): марка и модель → подпись объекта, год → год,
    тип ТС / категория → подгруппа veh_group шаблона класса 3, вид топлива → fuel, масса, мощность, объём двигателя
    и число мест → характеристики;
  * фото (ответ модели, блок vehicle_category в app/act.py): категория ТС и топливо (если видно — зарядный порт,
    баллоны газа) с уверенностью и коротким «почему».

Подгруппы и виды топлива — те же коды, что у групп факторов шаблона класса 3 (factor_groups veh_group и fuel,
docs/act_class_templates.json); тест сверяет их с шаблоном. Персональные данные владельца сюда не попадают:
на вход идут только поля объекта (docparse уже пропустил строки с ФИО, адресом и паспортом).
Модуль — чистые функции, без базы и сети.
"""
import re
from typing import Optional

VEH_GROUPS = ("car", "truck", "bus", "trailer", "special_wheeled", "special_tracked", "agro", "moto")
FUELS = ("petrol", "diesel", "lpg", "cng", "hybrid", "electric", "other")
SOURCE_LABELS = {"techpassport": {"ru": "техпаспорт", "uz": "texpasport", "en": "vehicle registration"},
                 "photo": {"ru": "фото", "uz": "surat", "en": "photo"}}
CONF_PASSPORT = 0.9          # графа техпаспорта переписана как есть, тип сопоставлен со списком подгрупп
CONF_PASSPORT_DIRECT = 0.95  # марка, модель, год — прямо из графы

# слово в типе ТС → подгруппа; порядок важен: «автобус» раньше «авто», «гусеничный кран» — гусеничная техника
_GROUP_WORDS = (
    ("moto", ("мотоцикл", "мопед", "мотороллер", "скутер", "квадроцикл", "mototsikl", "moped", "motorcycle",
              "scooter")),
    ("bus", ("автобус", "микроавтобус", "avtobus", "mikroavtobus", "bus", "minibus")),
    ("trailer", ("полуприцеп", "прицеп", "tirkama", "yarim tirkama", "trailer", "semi-trailer")),
    ("agro", ("трактор", "комбайн", "сельскохоз", "сельхоз", "traktor", "kombayn", "qishloq xo", "tractor",
              "combine", "agricultural")),
    ("special_tracked", ("гусенич", "экскаватор", "бульдозер", "gusenit", "ekskavator", "buldozer", "crawler",
                         "tracked", "excavator", "bulldozer")),
    ("special_wheeled", ("кран", "погрузчик", "автовышк", "бетононасос", "бетоносмес", "грейдер", "каток",
                         "спецтехн", "специальн", "kran", "yuklagich", "maxsus", "crane", "loader", "grader",
                         "special")),
    ("truck", ("грузов", "самосвал", "тягач", "фургон", "рефрижератор", "yuk ", "yuk-", "yuk avtomobil",
               "samosval", "tyagach", "truck", "lorry", "tipper", "van")),
    ("car", ("легков", "седан", "хэтчбек", "хетчбек", "универсал", "внедорожник", "кроссовер", "минивэн",
             "yengil", "sedan", "hatchback", "passenger", "car", "suv")),
)
# категория водительских прав / ТС (ПДД, Венская конвенция) — если типа словами нет
_CATEGORY = {"A": "moto", "A1": "moto", "L": "moto", "B": "car", "M1": "car", "C": "truck", "C1": "truck",
             "N1": "truck", "N2": "truck", "N3": "truck", "D": "bus", "D1": "bus", "M2": "bus", "M3": "bus",
             "E": "trailer", "O": "trailer", "O1": "trailer", "O2": "trailer", "O3": "trailer", "O4": "trailer"}
# газ сильнее бензина: «бензин/метан» — машина с газовым баллоном (риск оборудования газа)
_FUEL_WORDS = (
    ("electric", ("электр", "elektr", "electric", "battery ev", "bev")),
    ("hybrid", ("гибрид", "gibrid", "hybrid")),
    ("cng", ("метан", "сжатый газ", "кпг", "cng", "metan", "siqilgan gaz")),
    ("lpg", ("пропан", "бутан", "сжиженный газ", "суг", "lpg", "propan", "suyultirilgan gaz")),
    ("diesel", ("дизел", "дт", "dizel", "diesel")),
    ("petrol", ("бензин", "benzin", "petrol", "gasoline", "gasolin")),
)


def _low(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("ʻ", "'").replace("ʼ", "'").replace("‘", "'")).strip().lower()


def group_from_text(text) -> Optional[str]:
    """Тип ТС или категория словами («легковой», «грузовой самосвал», «N3», «yengil avtomobil») → код подгруппы."""
    low = _low(text)
    if not low:
        return None
    for code, words in _GROUP_WORDS:
        if any(w in low + " " for w in words):
            return code
    return _CATEGORY.get(re.sub(r"[^A-Z0-9]", "", low.upper()))


def fuel_from_text(text) -> Optional[str]:
    """Вид топлива словами («бензин», «дизельное топливо», «бензин/метан», «elektr») → код. Газ сильнее бензина."""
    low = " " + _low(text) + " "
    if not low.strip():
        return None
    for code, words in _FUEL_WORDS:
        for w in words:
            if (w == "дт" and re.search(r"(^|[^а-яa-z])дт([^а-яa-z]|$)", low)) or (w != "дт" and w in low):
                return code
    return None


def _year(v) -> Optional[int]:
    m = re.search(r"(19[5-9]\d|20\d\d)", str(v or ""))
    return int(m.group(1)) if m else None


def from_passport(by: dict) -> Optional[dict]:
    """
    Поля техпаспорта {ключ docparse: значение} → блок автозаполнения: label (марка + модель), year, veh_group,
    fuel, characteristics. Нет ни марки, ни модели, ни типа — None.
    """
    brand, model = str(by.get("brand") or "").strip(), str(by.get("model") or "").strip()
    vtype = str(by.get("vehicle_type") or "").strip()
    if not (brand or model or vtype):
        return None
    if brand and model and model.lower().startswith(brand.lower()):
        label = model                                   # «Chevrolet Cobalt» в графе модели — марку не повторяем
    elif brand and model:
        label = f"{brand} {model}"
    else:
        label = brand or model
    chars = {}
    for key in ("engine_power", "max_mass", "engine_cc", "seats"):
        if by.get(key) not in (None, ""):
            chars[key] = str(by[key]).strip()[:40]
    return {"label": label[:120] or None, "year": _year(by.get("year")), "veh_group": group_from_text(vtype),
            "vehicle_type": vtype[:80] or None, "fuel": fuel_from_text(by.get("fuel")),
            "fuel_text": (str(by.get("fuel") or "").strip()[:60] or None), "characteristics": chars}


def _entry(field: str, value, source: str, confidence: float, file=None, why: Optional[str] = None,
           lang: str = "ru") -> dict:
    return {"field": field, "value": value, "source": source,
            "source_label": SOURCE_LABELS[source].get(lang) or SOURCE_LABELS[source]["ru"],
            "confidence": round(float(confidence), 2), "file": file, "why": why}


def prefill(passport: Optional[dict], category: Optional[dict], lang: str = "ru",
            passport_file: Optional[str] = None) -> list:
    """
    Подсказки шага 2 и объекта: [{field, value, source: techpassport | photo, source_label, confidence, file, why}].
    Поля: object_label, year, class_fields.veh_group, class_fields.fuel, characteristics. Техпаспорт сильнее фото
    (графа документа против вида на снимке); фото дополняет то, чего в техпаспорте нет. Пробег — только у сотрудника
    (в техпаспорте его нет — docparse.MANUAL_FIELDS).
    """
    out, have = [], set()
    if passport:
        if passport.get("label"):
            out.append(_entry("object_label", passport["label"], "techpassport", CONF_PASSPORT_DIRECT, passport_file,
                              lang=lang))
        if passport.get("year"):
            out.append(_entry("year", passport["year"], "techpassport", CONF_PASSPORT_DIRECT, passport_file, lang=lang))
        if passport.get("veh_group"):
            out.append(_entry("class_fields.veh_group", passport["veh_group"], "techpassport", CONF_PASSPORT,
                              passport_file, passport.get("vehicle_type"), lang))
        if passport.get("fuel"):
            out.append(_entry("class_fields.fuel", passport["fuel"], "techpassport", CONF_PASSPORT, passport_file,
                              passport.get("fuel_text"), lang))
        if passport.get("characteristics"):
            out.append(_entry("characteristics", dict(passport["characteristics"]), "techpassport",
                              CONF_PASSPORT_DIRECT, passport_file, lang=lang))
        have = {e["field"] for e in out}
    if category:
        src = category.get("source") or "photo"
        if category.get("code") and "class_fields.veh_group" not in have:
            out.append(_entry("class_fields.veh_group", category["code"], src, category.get("confidence") or 0,
                              category.get("file"), category.get("why"), lang))
        if category.get("fuel") and "class_fields.fuel" not in have:
            out.append(_entry("class_fields.fuel", category["fuel"], src,
                              category.get("fuel_confidence") or category.get("confidence") or 0,
                              category.get("file"), category.get("fuel_why") or category.get("why"), lang))
    return out
