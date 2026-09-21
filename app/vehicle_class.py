"""
Граница между спецтехникой и транспортными средствами: классификатор объекта.

Зачем: ставка по спецтехнике (продукты 0302/0304/0313/0318, минимум 0,35%) и ставка
по имуществу (0801/0807/0808, 0,03–0,05%) отличаются в разы, а класс страхования
определяет и резервы, и проверки. Ошибка «спецтехника → имущество» занижает премию,
ошибка «оборудование → спецтехника» её завышает. Поэтому объект разбирается по
регистрационному документу, а не по названию в заявке.

Правовая рамка — docs/Спецтехника и транспорт — граница.md (заметка юриста проекта,
21.09.2026; все нормы проверены по lex.uz):
  * ПКМ № 683 от 31.08.2017, п. 2–5 — регистрация в СБДД МВД, свидетельство + ГРНЗ;
  * ПКМ № 170 от 29.04.2023, прил. 1 п. 2, 3, 14, 15 — Узагроинспекция: технический
    паспорт + ДРБ (самоходные машины) либо техническое свидетельство без номера (агрегаты);
  * ПДД (ПКМ № 172 от 12.04.2022) — «механическое транспортное средство ... этот термин
    также относится ко всем тракторам и самоходным машинам»;
  * ЗРУ-155 ст. 3, 6, 8 и ПКМ № 141 прил. № 5 п. 1 (в ред. ПКМ № 458 от 23.07.2025) — ОСГО;
  * Положение о страховых резервах рег. № 1882 п. 10 — нумерация классов (3, 7, 8, 9, 10, 13).

Модуль — чистые функции без обращений к базе и сети: вход → словарь с решением.
Все числа, кроме тарифов ОСГО из ПКМ № 141, — экспертные (calibrated = 0).
"""
from dataclasses import dataclass
from typing import List, Optional

CALIBRATED = 0                      # правило проекта 7: экспертное значение, не на своей статистике

# ---------------------------------------------------------------------------
# Группы объектов
# ---------------------------------------------------------------------------

G_CAR = "легковой"
G_TRUCK = "грузовой"
G_BUS = "автобус"
G_TRAILER = "прицеп"
G_SPEC = "спецтехника самоходная"
G_SPEC_CHASSIS = "спецтехника на шасси грузовика"
G_EQUIP = "прицепное (навесное) оборудование"

GROUPS = (G_CAR, G_TRUCK, G_BUS, G_TRAILER, G_SPEC, G_SPEC_CHASSIS, G_EQUIP)

# ---------------------------------------------------------------------------
# Регистрационные документы
# ---------------------------------------------------------------------------

DOC_SBDD = "свидетельство СБДД"                      # + ГРНЗ «01 A 123 AA»
DOC_AGRO = "техпаспорт Узагроинспекции"              # + ДРБ
DOC_TECH = "техническое свидетельство"               # без госномера
DOC_OTHER = "регистрация иного органа"               # горный надзор, Минтранс
DOC_NONE = "документа нет"

DOC_SYNONYMS = {
    DOC_SBDD: ("сбдд", "гаи", "гувд", "свидетельство о регистрации", "техпаспорт авто",
               "автомототранспорт", "грнз", "госномер авто"),
    DOC_AGRO: ("узагроинспекция", "агроинспекц", "технический паспорт", "техпаспорт",
               "трактор паспорт", "дрб", "уздавтехназорат"),
    DOC_TECH: ("техническое свидетельство", "техник гувохнома", "техник гувоҳнома",
               "свидетельство без номера"),
    DOC_OTHER: ("горный надзор", "горнотехнический", "министерство транспорта", "минтранс",
                "промышленный надзор"),
    DOC_NONE: ("нет", "отсутствует", "не предъявлен", "нет документа", "без документов"),
}

# ---------------------------------------------------------------------------
# Классы страхования (Положение 1882, п. 10)
# ---------------------------------------------------------------------------

CLASS_NAMES = {
    "3": "страхование наземных транспортных средств",
    "8": "страхование имущества от огня и стихийных бедствий",
    "9": "страхование имущества от ущерба",
    "13": "страхование общей гражданской ответственности",
    "10": "страхование автогражданской ответственности",
}

# ---------------------------------------------------------------------------
# Продукты компании (docs/Спецтехника и транспорт — граница.md, раздел 6)
# ---------------------------------------------------------------------------

PRODUCTS_SPEC = [
    ("0302", "Спецтехника в залоге", 0.35),
    ("0304", "Спецтехника в лизинг", 0.35),
    ("0313", "Спецтехника в рассрочку", 0.35),
    ("0318", "Добровольное страхование спецтехники", 0.35),
]
PRODUCTS_SPEC_TAKAFUL = [
    ("0330", "Такафул: спецтехника в лизинг", 0.40),
    ("0331", "Такафул: спецтехника в залоге", 0.40),
]
PRODUCTS_VEHICLE = [
    ("0301", "Транспорт в залоге", None),
    ("0303", "Транспорт в лизинг", None),
    ("0306", "Транспорт в залоге (программа банка)", None),
    ("0308", "Добровольное страхование транспортных средств", None),
    ("0309", "КАСКО Standart", None),
    ("0310", "КАСКО Comfort", None),
    ("0311", "КАСКО Premium", None),
    ("0315", "КАСКО Standart (онлайн)", None),
    ("0316", "КАСКО Comfort (онлайн)", None),
    ("0317", "КАСКО Premium (онлайн)", None),
]
PRODUCTS_EQUIP = [
    ("0801", "Имущество в залоге", 0.03),
    ("0804", "Объект лизинга", 0.03),
    ("0807", "Имущество от огня и стихийных бедствий", 0.05),
    ("0808", "Имущество от ущерба", 0.05),
    ("0816", "Комплексное страхование имущества", 0.05),
    ("0824", "Все риски: ущерб, поломка, простой (по согласованию с ЦО)", None),
]
PRODUCT_OSGO = ("1002", "ОСГО владельцев транспортных средств", None)
PRODUCT_GL = ("13", "Общая гражданская ответственность (в составе 0806, 0812, 0825, 0833)", None)

# ---------------------------------------------------------------------------
# ОСГО: базовые ставки, ПКМ № 141, прил. № 5 к Правилам, п. 1
# (в ред. ПКМ № 458 от 23.07.2025, действует с 01.01.2026). Это норма, не экспертиза.
# ---------------------------------------------------------------------------

OSGO_BASE = {
    G_CAR: (0.2, "строка 1 — легковые автомобили"),
    G_TRUCK: (0.35, "строка 2 — грузовые автомобили"),
    G_BUS: (0.4, "строка 3 — автобусы и микроавтобусы"),
    G_SPEC: (0.075, "строка 4 — тракторы, самоходные дорожно-строительные и иные машины"),
    G_SPEC_CHASSIS: (0.35, "строка 2 — грузовые автомобили (базой является шасси)"),
}
OSGO_KT_TASHKENT = 1.2          # п. 2 того же приложения
OSGO_KT_OTHER = 1.0
OSGO_SPEED_LIMIT = 20.0         # ЗРУ-155 ст. 6: до 20 км/ч включительно — ОСГО не обязательно

SPEED_HIGHWAY = 40.0            # ПДД п. 121 — допуск на автомагистрали
SPEED_SBDD = 50.0               # ПКМ № 683 п. 4 — регистрация в СБДД как автомобиля

MINE_PLACES = ("карьер", "шахта", "рудник", "разрез", "горн")

# ---------------------------------------------------------------------------
# Словарь подтипов: ключевое слово → подтип, группа, самоходность
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SubType:
    code: str
    name: str
    group: str
    self_propelled: Optional[bool]
    keywords: tuple
    note: str = ""


SUBTYPES: List[SubType] = [
    # --- техника на шасси грузовика: двойная природа (шасси + надстройка)
    SubType("avtokran", "автокран", G_SPEC_CHASSIS, True,
            ("автокран", "avtokran", "кран на шасси", "кран автомобильный"),
            "стрела и крановая установка — оборудование на шасси грузовика"),
    SubType("manipulator", "кран-манипулятор", G_SPEC_CHASSIS, True,
            ("манипулятор", "кран-манипулятор", "воровайка")),
    SubType("avtovyshka", "автовышка (подъёмник)", G_SPEC_CHASSIS, True,
            ("автовышка", "автогидроподъемник", "автогидроподъёмник", "подъемник на шасси")),
    SubType("betononasos", "бетононасос", G_SPEC_CHASSIS, True,
            ("бетононасос", "автобетононасос")),
    SubType("mikser", "автобетоносмеситель (миксер)", G_SPEC_CHASSIS, True,
            ("бетоносмеситель", "автобетоносмеситель", "миксер", "бетономешалка на шасси")),
    SubType("burovaya_shassi", "буровая установка на шасси", G_SPEC_CHASSIS, True,
            ("буровая на шасси", "буровая установка на шасси", "автобуровая")),
    SubType("evakuator", "эвакуатор", G_SPEC_CHASSIS, True, ("эвакуатор",)),
    SubType("musorovoz", "мусоровоз", G_SPEC_CHASSIS, True, ("мусоровоз",)),
    SubType("cisterna", "автоцистерна", G_SPEC_CHASSIS, True,
            ("автоцистерна", "цементовоз", "ассенизатор", "топливозаправщик")),
    # --- самоходная спецтехника
    SubType("ekskavator", "экскаватор", G_SPEC, True,
            ("экскаватор", "ekskavator", "excavator", "мини-экскаватор")),
    SubType("ekskavator_pogruzchik", "экскаватор-погрузчик", G_SPEC, True,
            ("экскаватор-погрузчик", "экскаватор погрузчик", "backhoe")),
    SubType("buldozer", "бульдозер", G_SPEC, True, ("бульдозер", "buldozer", "dozer")),
    SubType("greider", "автогрейдер", G_SPEC, True, ("грейдер", "greider", "grader")),
    SubType("katok", "дорожный каток", G_SPEC, True, ("каток", "виброкаток", "roller")),
    SubType("asfalt", "асфальтоукладчик", G_SPEC, True,
            ("асфальтоукладчик", "укладчик асфальта", "paver")),
    SubType("kran_gus", "кран гусеничный", G_SPEC, True,
            ("гусеничный кран", "кран гусеничный")),
    SubType("pogruzchik_front", "погрузчик фронтальный", G_SPEC, True,
            ("фронтальный погрузчик", "ковшовый погрузчик", "погрузчик фронтальный",
             "wheel loader")),
    SubType("pogruzchik_vil", "погрузчик вилочный", G_SPEC, True,
            ("вилочный погрузчик", "погрузчик вилочный", "электропогрузчик", "автопогрузчик",
             "штабелер", "штабелёр", "ричтрак", "forklift")),
    SubType("teleskop", "телескопический погрузчик", G_SPEC, True,
            ("телескопический погрузчик", "телехендлер", "telehandler")),
    SubType("traktor", "трактор", G_SPEC, True,
            ("трактор", "traktor", "tractor", "мини-трактор")),
    SubType("kombain", "комбайн", G_SPEC, True,
            ("комбайн", "kombain", "combine", "зерноуборочный", "хлопкоуборочн")),
    SubType("burovaya", "буровая установка", G_SPEC, True,
            ("буровая", "буровой станок", "гидробур", "ямобур")),
    SubType("truboukladchik", "трубоукладчик", G_SPEC, True, ("трубоукладчик",)),
    # --- обычные транспортные средства
    SubType("samosval", "самосвал", G_TRUCK, True, ("самосвал", "samosval", "tipper")),
    SubType("tyagach", "седельный тягач", G_TRUCK, True, ("тягач", "tyagach", "седельный")),
    SubType("gruzovoi", "грузовой автомобиль", G_TRUCK, True,
            ("грузовик", "грузовой", "бортовой", "фургон", "рефрижератор", "термофургон",
             "изотерм", "тент")),
    SubType("avtobus", "автобус", G_BUS, True,
            ("автобус", "микроавтобус", "avtobus", "bus", "маршрутка")),
    SubType("legkovoi", "легковой автомобиль", G_CAR, True,
            ("легковой", "седан", "хэтчбек", "универсал", "кроссовер", "внедорожник",
             "минивэн", "пикап")),
    SubType("pritsep", "прицеп (полуприцеп)", G_TRAILER, False,
            ("полуприцеп", "прицеп", "тракторный прицеп", "pritsep")),
    # --- навесное и прицепное оборудование
    SubType("navesnoe", "навесное (прицепное) оборудование", G_EQUIP, False,
            ("сеялка", "плуг", "борона", "культиватор", "косилка", "опрыскиватель",
             "жатка", "пресс-подборщик", "разбрасыватель", "каток прицепной")),
    SubType("agro_oborud", "машина или оборудование фермы", G_EQUIP, False,
            ("зерноочистительн", "доильн", "кормораздатчик", "инкубатор",
             "насос водяной", "водяной насос", "зернодробилка", "мельница")),
    SubType("bashenny_kran", "башенный кран", G_EQUIP, False,
            ("башенный кран", "кран башенный", "мостовой кран", "козловой кран")),
    SubType("stancia", "стационарная установка", G_EQUIP, False,
            ("компрессор стационарн", "дробильн", "бетонный завод", "асфальтобетонный завод",
             "генератор стационарн", "линия производств")),
]

# марки, которые встречаются только у спецтехники
SPECIAL_BRANDS = (
    "caterpillar", "катерпиллер", "komatsu", "комацу", "hitachi", "хитачи", "jcb", "джисиби",
    "xcmg", "сксмг", "sany", "сани", "zoomlion", "liugong", "лиугонг", "sdlg", "lonking",
    "shantui", "shehwa", "doosan", "дусан", "develon", "bobcat", "manitou", "wirtgen",
    "dynapac", "hamm", "bomag", "terex", "grove", "kato", "tadano", "liebherr", "либхер",
    "new holland", "case", "claas", "john deere", "deere", "kubota", "yto", "lovol",
    "мтз", "mtz", "беларус", "belarus", "амкодор", "четра", "чтз", "лтз", "ктз", "уралец",
    "т-28", "т-40", "дт-75", "мдт", "халк", "agrotech",
)
# марки легковых и грузовых
CAR_BRANDS = ("chevrolet", "шевроле", "daewoo", "ravon", "равон", "lada", "ваз", "nexia",
              "cobalt", "кобальт", "spark", "malibu", "captiva", "tracker", "damas", "labo",
              "byd", "chery", "haval", "skoda", "audi", "bmw", "opel", "peugeot", "renault")
TRUCK_BRANDS = ("камаз", "kamaz", "маз", "maz", "краз", "зил", "газель", "gazelle", "газ",
                "howo", "shacman", "shaanxi", "faw", "foton", "sitrak", "jac", "dongfeng",
                "isuzu", "hino", "daf", "man", "scania", "iveco", "урал", "уаз")
BUS_BRANDS = ("yutong", "ютонг", "higer", "king long", "mercedes sprinter", "setra", "neoplan")
# марки, которые есть и там, и там: решает тип из документа
AMBIGUOUS_BRANDS = ("toyota", "тойота", "hyundai", "хендай", "хёндэ", "volvo", "вольво",
                    "mercedes", "мерседес", "mitsubishi", "мицубиси", "nissan", "ниссан",
                    "kia", "киа", "ford", "форд")

CHASSIS_HINTS = ("на шасси", "на базе", "шасси камаз", "шасси маз", "шасси howo", "на камазе",
                 "на базе камаз", "на базе маз")


# ---------------------------------------------------------------------------
# Вход
# ---------------------------------------------------------------------------

@dataclass
class VehicleInput:
    """Карточка объекта со слов агента и из регистрационного документа."""
    type_text: str = ""                     # тип/категория из регистрационного документа
    doc_kind: str = ""                      # вид документа (см. DOC_*)
    plate: str = ""                         # госномер (ГРНЗ или ДРБ), если есть
    brand: str = ""
    model: str = ""
    self_propelled: Optional[bool] = None   # едет сама?
    max_speed_kmh: Optional[float] = None   # конструктивная скорость
    place: str = ""                         # место эксплуатации
    purpose: str = ""                       # назначение
    region: str = ""                        # для коэффициента территории ОСГО
    on_truck_chassis: Optional[bool] = None # смонтирована на шасси грузовика
    takaful: bool = False

    def text(self) -> str:
        return " ".join(str(x or "") for x in
                        (self.type_text, self.brand, self.model, self.purpose)).lower()


# ---------------------------------------------------------------------------
# Разбор входа
# ---------------------------------------------------------------------------

def normalize_doc(value: str) -> str:
    """Свободная формулировка агента → один из DOC_*. Непонятное — «документа нет»."""
    v = (value or "").strip().lower()
    if not v:
        return DOC_NONE
    for doc, words in DOC_SYNONYMS.items():
        for w in words:
            if w in v:
                return doc
    return DOC_NONE


def has_plate(inp: VehicleInput) -> bool:
    """Госномер считается указанным, если в поле есть хотя бы две цифры или буква серии."""
    p = (inp.plate or "").strip()
    if not p or p.lower() in ("нет", "отсутствует", "-"):
        return False
    return sum(ch.isdigit() for ch in p) >= 2


def detect_subtype(inp: VehicleInput) -> Optional[SubType]:
    """
    Подтип по ключевому слову. Сначала — тип из документа (он главнее), затем марка и модель.
    Самое длинное совпадение выигрывает: «экскаватор-погрузчик» важнее «погрузчика».
    """
    fields = [(inp.type_text or "").lower(),
              " ".join([(inp.model or ""), (inp.brand or "")]).lower(),
              (inp.purpose or "").lower()]
    for text in fields:
        if not text.strip():
            continue
        best, best_len = None, 0
        for st in SUBTYPES:
            for kw in st.keywords:
                if kw in text and len(kw) > best_len:
                    best, best_len = st, len(kw)
        if best:
            return best
    return None


def brand_hint(inp: VehicleInput) -> str:
    """
    Что подсказывает марка, когда тип не назван: 'спец' | 'легковой' | 'грузовой' |
    'автобус' | 'спорный' | ''. Марка — только подсказка, документ важнее.
    """
    b = (" ".join([inp.brand or "", inp.model or ""])).lower()
    if not b.strip():
        return ""
    for name in SPECIAL_BRANDS:
        if name in b:
            return "спец"
    for name in BUS_BRANDS:
        if name in b:
            return "автобус"
    for name in TRUCK_BRANDS:
        if name in b:
            return "грузовой"
    for name in CAR_BRANDS:
        if name in b:
            return "легковой"
    for name in AMBIGUOUS_BRANDS:
        if name in b:
            return "спорный"
    return ""


def on_chassis(inp: VehicleInput, st: Optional[SubType]) -> bool:
    """Смонтирована ли машина на шасси грузовика: явный признак, подсказка в тексте или подтип."""
    if inp.on_truck_chassis is not None:
        return bool(inp.on_truck_chassis)
    text = inp.text()
    if any(h in text for h in CHASSIS_HINTS):
        return True
    return bool(st and st.group == G_SPEC_CHASSIS)


def is_mine_site(inp: VehicleInput) -> bool:
    place = (inp.place or "").lower()
    return any(w in place for w in MINE_PLACES)


# ---------------------------------------------------------------------------
# Правила SPEC-01…SPEC-07 (заметка юриста, раздел 7)
# ---------------------------------------------------------------------------

def _pct(x) -> str:
    """0.075 -> «0,075 %» — проценты агенту показываем по-русски, с запятой."""
    if x is None:
        return "—"
    return ("%g" % float(x)).replace(".", ",") + " %"


def _rule(code: str, title: str, level: str, norm: str, text: str) -> dict:
    return {"code": code, "title": title, "level": level, "norm": norm, "text": text}


RULE_TITLES = {
    "SPEC-01": "Класс определяется регистрационным документом",
    "SPEC-02": "Техническое свидетельство без госномера = имущество",
    "SPEC-03": "Несамоходное = имущество",
    "SPEC-04": "ОСГО для самоходной техники",
    "SPEC-05": "ОСГО не покрывает работу оборудования",
    "SPEC-06": "Карьерная и шахтная техника — ручная проверка",
    "SPEC-07": "Конструктивная скорость: три разных порога",
}


# ---------------------------------------------------------------------------
# Основной разбор
# ---------------------------------------------------------------------------

def decide_group(inp: VehicleInput) -> dict:
    """Группа объекта. Возвращает группу, подтип, признаки и причины выбора."""
    st = detect_subtype(inp)
    hint = brand_hint(inp)
    reasons = []
    group = None

    if st:
        group = st.group
        reasons.append("в документах указан тип «%s» — это %s." % (st.name, st.group))
    elif hint == "спец":
        group = G_SPEC
        reasons.append("тип не указан, но марка «%s» встречается только у спецтехники."
                       % (inp.brand or inp.model))
    elif hint in ("легковой", "грузовой", "автобус"):
        group = {"легковой": G_CAR, "грузовой": G_TRUCK, "автобус": G_BUS}[hint]
        reasons.append("тип не указан; марка «%s» — обычный автотранспорт." % (inp.brand or ""))
    else:
        reasons.append("ни тип, ни марка не дали однозначного ответа.")

    # шасси грузовика перекрывает «самоходную спецтехнику»
    chassis = on_chassis(inp, st)
    if chassis and group in (G_SPEC, G_TRUCK, G_SPEC_CHASSIS):
        if group != G_SPEC_CHASSIS:
            reasons.append("машина смонтирована на шасси грузовика — это техника двойной природы.")
        group = G_SPEC_CHASSIS

    # самоходность как запасной признак
    if group is None:
        if inp.self_propelled is True:
            group = G_SPEC
            reasons.append("машина едет сама — считаем её самоходной; уточните тип.")
        elif inp.self_propelled is False:
            group = G_EQUIP
            reasons.append("машина сама не едет — это навесное или прицепное оборудование.")

    # несамоходность прямо противоречит «самоходной спецтехнике»
    if inp.self_propelled is False and group in (G_SPEC, G_SPEC_CHASSIS):
        group = G_EQUIP
        reasons.append("агент указал, что машина не самоходная: она переведена в оборудование "
                       "(если это зарегистрированный прицеп — приложите документ).")

    return {"group": group, "subtype": st, "brand_hint": hint, "chassis": chassis,
            "reasons": reasons}


def decide_class(inp: VehicleInput, group: Optional[str], doc: str, plate: bool) -> dict:
    """
    Класс страхования по регистрационному документу.

    Одна фраза для агента: госномер и паспорт (СБДД или Узагроинспекции) → класс 3;
    только техническое свидетельство или вообще ничего → классы 8/9.
    """
    if doc == DOC_SBDD and plate:
        return {"class_code": "3", "confirmed": True,
                "basis": "свидетельство о регистрации СБДД с ГРНЗ (ПКМ № 683, п. 2–4)"}
    if doc == DOC_AGRO and plate:
        return {"class_code": "3", "confirmed": True,
                "basis": "технический паспорт Узагроинспекции с ДРБ (ПКМ № 170, прил. 1, п. 3)"}
    if doc in (DOC_SBDD, DOC_AGRO) and not plate:
        return {"class_code": "3", "confirmed": False,
                "basis": "документ назван, но госномер не указан — класс 3 не подтверждён "
                         "(ПКМ № 683 п. 2; ПКМ № 170 прил. 1 п. 3)"}
    if doc == DOC_TECH:
        return {"class_code": "8/9", "confirmed": True,
                "basis": "техническое свидетельство выдаётся тому, кому технический паспорт "
                         "не положен (ПКМ № 170, прил. 1, п. 3)"}
    if doc == DOC_OTHER:
        return {"class_code": "8/9", "confirmed": False,
                "basis": "регистрация иного органа: класс зависит от допуска к дорогам общего "
                         "пользования — решает андеррайтер (ПКМ № 170, прил. 1, п. 14)"}
    # документа нет
    if group in (G_EQUIP,):
        return {"class_code": "8/9", "confirmed": True,
                "basis": "оборудование без регистрации — имущество (ПДД, понятие «прицеп»; "
                         "ПКМ № 170, прил. 1, п. 3)"}
    return {"class_code": None, "confirmed": False,
            "basis": "регистрационный документ не предъявлен — класс не определяется "
                     "(ПКМ № 683 п. 2–4; ПКМ № 170 прил. 1 п. 2, 3)"}


def osgo_block(inp: VehicleInput, group: Optional[str], class_code: Optional[str]) -> dict:
    """
    ОСГО: обязанность, базовая ставка и коэффициент территории.
    Числа — из ПКМ № 141 (прил. № 5 к Правилам, п. 1 и 2), это норма, а не экспертная оценка.
    """
    out = {"required": None, "reason": "", "base_rate_pct": None, "row": "",
           "kt": None, "rate_pct": None, "product": PRODUCT_OSGO, "calibrated": 1}
    if group in (G_TRAILER, G_EQUIP):
        out["required"] = False
        out["reason"] = ("прицепы и несамоходное оборудование под обязанность страховать "
                         "не подпадают (ЗРУ-155, ст. 6)")
        return out
    if class_code == "8/9":
        out["required"] = False
        out["reason"] = ("объект не допущен к дорогам общего пользования, ОСГО к нему "
                         "не применяется (ЗРУ-155, ст. 3 — «перевозка по дорогам»)")
        return out
    speed = inp.max_speed_kmh
    base, row = OSGO_BASE.get(group or "", (None, ""))
    region = (inp.region or "").lower()
    kt = OSGO_KT_TASHKENT if ("ташкент" in region) else OSGO_KT_OTHER
    out["base_rate_pct"], out["row"], out["kt"] = base, row, kt
    if base is not None:
        out["rate_pct"] = round(base * kt, 4)
    if speed is None:
        out["required"] = None
        out["reason"] = ("конструктивная скорость не указана: порог обязанности — 20 км/ч "
                         "(ЗРУ-155, ст. 6). Уточните скорость по паспорту машины")
    elif speed <= OSGO_SPEED_LIMIT:
        out["required"] = False
        out["reason"] = ("конструктивная скорость %g км/ч — не более 20 км/ч, обязанность "
                         "страховать не распространяется (ЗРУ-155, ст. 6)" % speed)
        out["rate_pct"] = None
    else:
        out["required"] = True
        out["reason"] = ("конструктивная скорость %g км/ч — выше порога 20 км/ч "
                         "(ЗРУ-155, ст. 3, 6)" % speed)
    return out


def products_for(group: Optional[str], class_code: Optional[str], takaful: bool) -> List[dict]:
    """Продукты компании под группу объекта."""
    def pack(rows, note=""):
        return [{"code": c, "name": n, "rate_pct": r, "note": note} for c, n, r in rows]

    if class_code is None:
        return []                       # класс не подтверждён документом — продукт не предлагаем
    if class_code == "8/9" or group == G_EQUIP:
        return pack(PRODUCTS_EQUIP, "имущество: классы 8 (огонь и стихия) и 9 (ущерб)")
    if group in (G_SPEC, G_SPEC_CHASSIS):
        rows = pack(PRODUCTS_SPEC_TAKAFUL if takaful else PRODUCTS_SPEC,
                    "спецтехника, класс 3")
        if group == G_SPEC_CHASSIS:
            rows += pack(PRODUCTS_EQUIP[:4], "надстройка (крановая, подъёмная, насосная "
                                             "установка) страхуется как оборудование")
        return rows
    if group in (G_CAR, G_TRUCK, G_BUS, G_TRAILER):
        return pack(PRODUCTS_VEHICLE, "обычные транспортные средства, класс 3")
    return []


def dual_nature_block(group: Optional[str], inp: VehicleInput) -> Optional[dict]:
    """Техника на шасси грузовика: шасси — класс 3, надстройка — оборудование."""
    if group != G_SPEC_CHASSIS:
        return None
    return {
        "chassis": {"class_code": "3", "what": "шасси грузового автомобиля",
                    "basis": "зарегистрировано в СБДД, участвует в дорожном движении "
                             "(ПКМ № 683 п. 4; Положение 1882 п. 10)"},
        "superstructure": {"class_code": "3 или 8/9",
                           "what": "установленное оборудование (стрела, люлька, насос, бурстанок)",
                           "basis": "по ЗРУ-155 ст. 3 работа установленного оборудования "
                                    "использованием транспортного средства не является; "
                                    "для КАСКО надстройка либо включается в страховую сумму "
                                    "машины, либо страхуется отдельно как оборудование"},
        "text": ("Это машина двойной природы: грузовое шасси плюс рабочая установка. "
                 "В страховой сумме показывайте их отдельно — шасси и надстройку: при убытке "
                 "выясняется, что именно повреждено, а тарифы у них разные. "
                 "Ответственность за вред от работы установки ОСГО не покрывает — нужен класс 13."),
    }


def confidence_of(inp: VehicleInput, d: dict, cls: dict, doc: str, plate: bool) -> dict:
    """Уровень уверенности и список того, что надо уточнить."""
    ask = []
    score = 1.0
    if doc == DOC_NONE:
        ask.append("Какой регистрационный документ у объекта: свидетельство СБДД с госномером, "
                   "технический паспорт Узагроинспекции с ДРБ или техническое свидетельство?")
        score -= 0.45
    if doc in (DOC_SBDD, DOC_AGRO) and not plate:
        ask.append("Укажите государственный номер (ГРНЗ или ДРБ) из документа.")
        score -= 0.2
    if d["subtype"] is None:
        ask.append("Укажите тип машины из регистрационного документа (например, «экскаватор», "
                   "«трактор», «грузовой автомобиль»).")
        score -= 0.25
    if d["brand_hint"] == "спорный" and d["subtype"] is None:
        ask.append("Марка встречается и у легковых машин, и у спецтехники — нужен тип из документа.")
        score -= 0.1
    if inp.self_propelled is None and d["group"] in (G_SPEC, G_EQUIP, None):
        ask.append("Машина передвигается сама или её перевозят (буксируют)?")
        score -= 0.1
    if inp.max_speed_kmh is None and d["group"] in (G_SPEC, G_SPEC_CHASSIS, G_TRUCK, G_CAR, G_BUS):
        ask.append("Какая максимальная конструктивная скорость по паспорту (нужна для ОСГО, "
                   "порог 20 км/ч)?")
        score -= 0.1
    if is_mine_site(inp):
        ask.append("Техника работает на руднике, шахте или в карьере — приложите регистрационный "
                   "документ: класс определит андеррайтер.")
        score -= 0.2
    if not inp.place:
        ask.append("Где машина работает: карьер, стройка, дорожные работы, склад, поле?")
        score -= 0.05
    if cls.get("class_code") is None:
        # класс не определён — это всегда низкая уверенность, чем бы ни был заполнен остальной бланк
        score = min(score, 0.4)
    elif not cls.get("confirmed"):
        score = min(score, 0.6)
    score = max(0.0, min(1.0, score))
    level = "высокая" if score >= 0.8 else ("средняя" if score >= 0.5 else "низкая")
    return {"score": round(score, 2), "level": level, "ask": ask}


def classify(inp: VehicleInput) -> dict:
    """
    Главная функция: карточка объекта → группа, класс, продукты, правила, объяснение.

    Ничего не сохраняет и никуда не ходит — чистая функция, её результат можно показать
    агенту и положить в расчёт.
    """
    doc = normalize_doc(inp.doc_kind)
    plate = has_plate(inp)
    d = decide_group(inp)
    group = d["group"]
    st = d["subtype"]
    cls = decide_class(inp, group, doc, plate)
    class_code = cls["class_code"]

    rules = []
    why = []

    # --- объяснение: что за машина
    why.append("Что это за машина: " + " ".join(d["reasons"]))
    if group:
        why.append("Группа: %s%s." % (group, (" (%s)" % st.name) if st else ""))

    # --- документ
    doc_human = {
        DOC_SBDD: "свидетельство о регистрации СБДД (МВД)",
        DOC_AGRO: "технический паспорт Узагроинспекции",
        DOC_TECH: "техническое свидетельство Узагроинспекции (госномер не выдаётся)",
        DOC_OTHER: "регистрация другого органа (горный надзор или Министерство транспорта)",
        DOC_NONE: "регистрационный документ не предъявлен",
    }[doc]
    why.append("Документ: %s; госномер %s." % (doc_human, "указан" if plate else "не указан"))
    why.append("Класс: %s — %s." % (class_code or "не определён", cls["basis"]))

    # SPEC-01
    if class_code == "3" and not cls["confirmed"]:
        rules.append(_rule("SPEC-01", RULE_TITLES["SPEC-01"], "стоп",
                           "ПКМ № 683 п. 2, 3, 4; ПКМ № 170 прил. 1 п. 2, 3",
                           "Для класса 3 нужен документ о регистрации: свидетельство СБДД "
                           "с госномером или технический паспорт Узагроинспекции с ДРБ. "
                           "Приложите документ или переведите объект в класс 8/9."))
    if class_code is None:
        rules.append(_rule("SPEC-01", RULE_TITLES["SPEC-01"], "стоп",
                           "ПКМ № 683 п. 2, 3, 4; ПКМ № 170 прил. 1 п. 2, 3",
                           "Регистрационный документ не предъявлен — расчёт по классу 3 "
                           "не проводится. Приложите документ или оформляйте как имущество "
                           "(класс 8/9)."))
    # SPEC-02
    if doc == DOC_TECH and not plate:
        rules.append(_rule("SPEC-02", RULE_TITLES["SPEC-02"], "стоп",
                           "ПКМ № 170 прил. 1 п. 3",
                           "Техническое свидетельство выдаётся на агрегаты и оборудование, "
                           "которым технический паспорт не положен. Это имущество, класс 8/9, "
                           "а не наземный транспорт."))
    # SPEC-03
    if (inp.self_propelled is False or group == G_EQUIP) and doc == DOC_NONE:
        rules.append(_rule("SPEC-03", RULE_TITLES["SPEC-03"], "предупреждение",
                           "ПДД (ПКМ № 172), понятия «механическое ТС» и «прицеп»; "
                           "ПКМ № 683 п. 4; ПКМ № 170 прил. 1 п. 3",
                           "Навесное и прицепное оборудование без регистрации — имущество. "
                           "Если это зарегистрированный прицеп или полуприцеп, приложите "
                           "свидетельство или техпаспорт."))
    # SPEC-04 и SPEC-07
    osgo = osgo_block(inp, group, class_code)
    if group in (G_SPEC, G_SPEC_CHASSIS) and inp.self_propelled is not False:
        if osgo["required"] is True:
            rules.append(_rule("SPEC-04", RULE_TITLES["SPEC-04"], "предупреждение",
                               "ЗРУ-155 ст. 3, 6; ПКМ № 141 прил. № 5 п. 1 "
                               "(в ред. ПКМ № 458 от 23.07.2025)",
                               "Машина подпадает под ОСГО: %s. Базовая ставка %s (%s), "
                               "коэффициент территории %s. Проверьте наличие полиса (продукт 1002)."
                               % (osgo["reason"], _pct(osgo["base_rate_pct"]), osgo["row"],
                                  ("%g" % osgo["kt"]).replace(".", ","))))
        elif osgo["required"] is None:
            rules.append(_rule("SPEC-04", RULE_TITLES["SPEC-04"], "предупреждение",
                               "ЗРУ-155 ст. 3, 6",
                               "Не указана конструктивная скорость: без неё нельзя сказать, "
                               "нужен ли полис ОСГО (порог — 20 км/ч)."))
    if inp.max_speed_kmh is not None:
        s = float(inp.max_speed_kmh)
        rules.append(_rule("SPEC-07", RULE_TITLES["SPEC-07"], "подсказка",
                           "ЗРУ-155 ст. 6; ПДД (ПКМ № 172) п. 121; ПКМ № 683 п. 4",
                           "Скорость %g км/ч: ОСГО — %s (порог 20 км/ч); автомагистрали — %s "
                           "(порог 40 км/ч); регистрация в СБДД как автомобиля — %s "
                           "(порог 50 км/ч). Пороги разные, подменять их нельзя."
                           % (s, "подпадает" if s > OSGO_SPEED_LIMIT else "не подпадает",
                              "допуск есть" if s >= SPEED_HIGHWAY else "движение запрещено",
                              "подходит" if s >= SPEED_SBDD else "не подходит")))
    # SPEC-05
    if class_code == "3" and group in (G_SPEC, G_SPEC_CHASSIS):
        rules.append(_rule("SPEC-05", RULE_TITLES["SPEC-05"], "подсказка",
                           "ЗРУ-155 ст. 3, 8",
                           "ОСГО не покрывает вред от работы установленного на машине "
                           "оборудования, вред при погрузке-разгрузке и вред на территории "
                           "самого предприятия-владельца. Предложите общую гражданскую "
                           "ответственность (класс 13)."))
    # SPEC-06
    if is_mine_site(inp):
        rules.append(_rule("SPEC-06", RULE_TITLES["SPEC-06"], "предупреждение",
                           "ПКМ № 683 п. 4 против ПКМ № 170 прил. 1 п. 14 — акты расходятся",
                           "Карьерная и шахтная техника регистрируется по-разному. Приложите "
                           "регистрационный документ: класс определит андеррайтер."))

    conf = confidence_of(inp, d, cls, doc, plate)
    dual = dual_nature_block(group, inp)
    if dual:
        why.append(dual["text"])
    if class_code == "3" and group in (G_SPEC, G_SPEC_CHASSIS):
        why.append("Тариф: продукты спецтехники (0302, 0304, 0313, 0318) — минимум 0,35%; "
                   "такафул 0330/0331 — 0,4%. Ставка имущества (0,03–0,05%) здесь не применяется.")
    if class_code == "8/9" and group in (G_SPEC, G_SPEC_CHASSIS):
        why.append("Важно: по типу это самоходная машина, но регистрационный документ относит её "
                   "к имуществу — класс 3 применить нельзя, считаем по классам 8/9.")
    if class_code is None:
        why.append("Продукт подобрать пока нельзя: сначала нужен регистрационный документ. "
                   "Он же решит, класс 3 это или классы 8/9.")
    if class_code == "8/9":
        why.append("Тариф: имущество (0801, 0804, 0807, 0808, 0816) — 0,03–0,05%. "
                   "Ставка спецтехники (0,35%) здесь была бы завышением в разы.")

    return {
        "group": group,
        "subtype": st.name if st else None,
        "subtype_code": st.code if st else None,
        "class_code": class_code,
        "class_name": CLASS_NAMES.get(class_code or "", "классы 8 и 9 — страхование имущества"
                                      if class_code == "8/9" else ""),
        "class_confirmed": cls["confirmed"],
        "class_basis": cls["basis"],
        "document": {"kind": doc, "human": doc_human, "plate": plate},
        "products": products_for(group, class_code, inp.takaful),
        "extra_cover": ({"class_code": "13", "product": PRODUCT_GL,
                         "why": "вред от работы установленного оборудования из ОСГО исключён "
                                "(ЗРУ-155 ст. 3, 8)"}
                        if group in (G_SPEC, G_SPEC_CHASSIS) else None),
        "osgo": osgo,
        "dual_nature": dual,
        "rules": rules,
        "why": why,
        "confidence": conf["level"],
        "confidence_score": conf["score"],
        "ask": conf["ask"],
        "calibrated": CALIBRATED,
        "legal": "docs/Спецтехника и транспорт — граница.md (заметка юриста, 21.09.2026)",
    }


def explain_text(result: dict) -> str:
    """Человеческое объяснение одним куском — для экрана агента и для PDF-предложения."""
    out = list(result["why"])
    if result["rules"]:
        out.append("Проверки:")
        for r in result["rules"]:
            out.append("  [%s, %s] %s" % (r["code"], r["level"], r["text"]))
    if result["products"]:
        out.append("Подходящие продукты: " + ", ".join(
            "%s %s%s" % (p["code"], p["name"],
                         (" — " + _pct(p["rate_pct"])) if p["rate_pct"] else "")
            for p in result["products"]))
    o = result["osgo"]
    if o["required"] is True:
        out.append("ОСГО: нужен (продукт 1002), ставка %s × %s = %s — %s."
                   % (_pct(o["base_rate_pct"]), ("%g" % o["kt"]).replace(".", ","),
                      _pct(o["rate_pct"]), o["row"]))
    elif o["required"] is False:
        out.append("ОСГО: не требуется — " + o["reason"] + ".")
    else:
        out.append("ОСГО: не определено — " + o["reason"] + ".")
    if result["ask"]:
        out.append("Что уточнить у клиента:")
        out += ["  — " + a for a in result["ask"]]
    out.append("Уверенность: %s (%.2f)." % (result["confidence"], result["confidence_score"]))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Вход по сети: экран агента (/ui) спрашивает классификатор здесь.
# Роутер ничего не считает сам — он только принимает карточку объекта, переводит
# ручной выбор группы в признаки, которые читает разбор выше, и отдаёт результат.
# ---------------------------------------------------------------------------
from fastapi import APIRouter          # noqa: E402
from pydantic import BaseModel         # noqa: E402

router = APIRouter()

DOC_KINDS = (DOC_SBDD, DOC_AGRO, DOC_TECH, DOC_OTHER, DOC_NONE)

# слова, которыми ручной выбор группы объясняется классификатору
GROUP_TYPE_WORD = {
    G_CAR: "легковой автомобиль",
    G_TRUCK: "грузовой автомобиль",
    G_BUS: "автобус",
    G_TRAILER: "прицеп",
}
# что спросить у агента, если его выбор не сошёлся с разбором карточки
GROUP_CONFLICT_FIX = {
    G_SPEC: "напишите тип машины из регистрационного документа (экскаватор, трактор, "
            "погрузчик, каток) — классификатор идёт от документа, а не от марки",
    G_SPEC_CHASSIS: "напишите тип надстройки (автокран, автовышка, бетононасос, миксер) — "
                    "по нему видно, что машина стоит на шасси грузовика",
    G_EQUIP: "уберите из типа слово, которым назван транспорт, и укажите, что это за "
             "оборудование (сеялка, башенный кран, дробильная установка)",
}


class VehicleIn(BaseModel):
    """Карточка объекта с экрана агента. Пустые поля допустимы: чего нет — то и не решает."""
    type_text: str = ""
    doc_kind: str = ""
    plate: str = ""
    brand: str = ""
    model: str = ""
    self_propelled: Optional[bool] = None
    max_speed_kmh: Optional[float] = None
    place: str = ""
    purpose: str = ""
    region: str = ""
    on_truck_chassis: Optional[bool] = None
    takaful: bool = False
    group: Optional[str] = None        # группа, выбранная агентом вручную


def _input_of(body: "VehicleIn") -> VehicleInput:
    return VehicleInput(type_text=body.type_text, doc_kind=body.doc_kind, plate=body.plate,
                        brand=body.brand, model=body.model, self_propelled=body.self_propelled,
                        max_speed_kmh=body.max_speed_kmh, place=body.place, purpose=body.purpose,
                        region=body.region, on_truck_chassis=body.on_truck_chassis,
                        takaful=body.takaful)


def apply_group(inp: VehicleInput, group: str) -> VehicleInput:
    """
    Ручной переключатель группы. Выбор агента не подменяет решение сверху: он переводится
    в те самые признаки, которые читает разбор (тип из документа, самоходность, шасси),
    и дальше работает обычная цепочка. Если признаки не сходятся — расхождение видно.
    """
    out = VehicleInput(type_text=inp.type_text, doc_kind=inp.doc_kind, plate=inp.plate,
                       brand=inp.brand, model=inp.model, self_propelled=inp.self_propelled,
                       max_speed_kmh=inp.max_speed_kmh, place=inp.place, purpose=inp.purpose,
                       region=inp.region, on_truck_chassis=inp.on_truck_chassis,
                       takaful=inp.takaful)
    if group in GROUP_TYPE_WORD:
        out.type_text = GROUP_TYPE_WORD[group]
        out.on_truck_chassis = False
        out.self_propelled = group != G_TRAILER
    elif group == G_SPEC:
        out.self_propelled = True
        out.on_truck_chassis = False
    elif group == G_SPEC_CHASSIS:
        out.self_propelled = True
        out.on_truck_chassis = True
    elif group == G_EQUIP:
        out.self_propelled = False
        out.on_truck_chassis = False
    return out


@router.get("/vehicle/reference")
def vehicle_reference() -> dict:
    """Справочник для экрана: группы, виды документов, ставки ОСГО и названия правил."""
    return {
        "groups": list(GROUPS),
        "doc_kinds": list(DOC_KINDS),
        "osgo_base": {g: {"rate_pct": r, "row": row} for g, (r, row) in OSGO_BASE.items()},
        "osgo_kt": {"г. Ташкент": OSGO_KT_TASHKENT, "остальные регионы": OSGO_KT_OTHER},
        "osgo_speed_limit": OSGO_SPEED_LIMIT,
        "rules": RULE_TITLES,
        "products": {
            "спецтехника": [{"code": c, "name": n, "rate_pct": r} for c, n, r in PRODUCTS_SPEC],
            "спецтехника такафул": [{"code": c, "name": n, "rate_pct": r} for c, n, r in PRODUCTS_SPEC_TAKAFUL],
            "транспорт": [{"code": c, "name": n, "rate_pct": r} for c, n, r in PRODUCTS_VEHICLE],
            "оборудование": [{"code": c, "name": n, "rate_pct": r} for c, n, r in PRODUCTS_EQUIP],
        },
        "calibrated": CALIBRATED,
        "legal": "docs/Спецтехника и транспорт — граница.md (заметка юриста, 21.09.2026)",
    }


@router.post("/vehicle/classify")
def vehicle_classify(body: VehicleIn) -> dict:
    """
    Что это за объект: группа, класс страхования, продукты, ОСГО, правила SPEC-01…07.

    Всегда возвращается автоматический разбор (`auto`). Если агент выбрал группу сам и
    карточка это подтверждает — основной результат считается по его выбору; если не
    подтверждает, основным остаётся автоматический разбор, а в `conflict` написано,
    что поправить.
    """
    inp = _input_of(body)
    auto = classify(inp)
    out = {"result": auto, "explain": explain_text(auto), "auto": auto,
           "manual_group": None, "conflict": None}
    group = (body.group or "").strip()
    if not group or group not in GROUPS:
        return out
    out["manual_group"] = group
    if group == auto["group"]:
        return out
    forced = classify(apply_group(inp, group))
    if forced["group"] == group:
        out["result"] = forced
        out["explain"] = explain_text(forced)
        return out
    fix = GROUP_CONFLICT_FIX.get(group, "уточните тип машины из регистрационного документа")
    out["conflict"] = ("Вы выбрали группу «%s», а карточка объекта читается как «%s». "
                       "Класс страхования определяет регистрационный документ, поэтому "
                       "показан автоматический разбор. Чтобы выбор сработал, %s."
                       % (group, auto["group"] or "не определено", fix))
    return out
