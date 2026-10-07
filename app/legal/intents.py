"""
Словари распознавания вопросов специалиста (ru | uz | en) — только данные, без логики.

Основы слов сравниваются после нормализации (нижний регистр, апострофы сняты: app/textnorm),
поэтому узбекская латиница записана без апострофов («sugurta», «tolov»).
Читатели: app/legal/search_core.py, faq.py, competitors.py (право и документы конкурентов),
app/legal/market.py (вопросы о рынке и финансах страховщиков), app/legal/parse.py (язык вопроса).
"""
import re

# =========================================================================== #
#  Право: поиск по индексу и FAQ
# =========================================================================== #

# Вопрос о конкурентах, рынке, условиях и ставках других страховщиков: только тогда документы
# конкурентов участвуют в ответе наравне с остальными; иначе — дополнением после нормы
COMPETITOR_WORDS = ("конкурент", "рынк", "рынок", "услови", "франшиз", "ставк", "тариф", "других страховщ",
                    "другие страховщ", "другой страхов", "других компан", "продукт", "оферт", "raqobat", "bozor", "shart", "boshqa",
                    "franshiza", "stavka", "competitor", "market", "terms", "deductible", "other insurer", "offer")

# документ компании (тарифная политика — распознанный скан с перечнем продуктов) отвечает только на вопрос
# о компании: иначе перечень продуктов («страхование лиц, выезжающих за рубеж») выдавался за ответ
# на правовой вопрос «нужно ли страховать туристов» и живой поиск закона на lex.uz не запускался
COMPANY_WORDS = ("inson", "инсон", "тарифн", "политик", "компани", "приказ", "54-п", "агентск", "вознагражд",
                 "минимальн", "tarif siyosat", "kompaniya", "tariff policy", "company", "minimum",
                 "актуар", "aktuar", "actuar")

# служебные слова: в поисковый запрос не идут, иначе «что если» перетягивает выдачу
STOP = {
    "ru": {"что", "если", "как", "при", "для", "это", "или", "the", "или", "но", "на", "по", "из",
           "за", "от", "до", "не", "ли", "же", "бы", "быть", "есть", "его", "их", "мы", "вы",
           "можно", "нужно", "такое", "чем", "кто", "где", "когда", "почему", "какой", "какая"},
    "uz": {"nima", "qanday", "agar", "uchun", "bilan", "boʻlsa", "bolsa", "boladi", "boʻladi",
           "yoki", "va", "emas", "kerak", "qachon", "qaysi", "kim", "qayerda"},
    "en": {"what", "if", "how", "the", "a", "an", "is", "are", "of", "for", "to", "in", "on", "and",
           "or", "not", "be", "can", "should", "when", "which", "who", "where", "why", "does", "do"},
}
ALL_STOP = set().union(*STOP.values())

# Синонимы естественных формулировок: юрист пишет в FAQ «максимум комиссии», а спрашивают
# «какой максимальный размер», «сколько можно платить». Без этого вопрос уходил в поиск по актам
# и получал нерелевантную норму (дефект контролёра: комиссия агента → Положение 1882 о резервах).
SYNONYMS = [
    {"максимум", "максимальный", "максимально", "предел", "предельный", "лимит", "сколько",
     "размер", "величина", "ограничение", "потолок", "eng", "kop", "qancha", "miqdor", "chegara",
     "cap", "maximum", "max", "limit", "size", "much"},
    {"комиссия", "комиссионный", "вознаграждение", "агентский", "vositachilik", "haq", "haqi",
     "commission", "fee", "remuneration"},
    {"платить", "выплата", "выплачивать", "уплата", "оплата", "tolash", "tolanadi", "pay",
     "payment", "paid"},
    {"расторжение", "расторгнут", "прекращение", "досрочный", "bekor", "muddatidan",
     "termination", "terminated", "early"},
    {"возврат", "вернуть", "возвращает", "qaytarish", "qaytaradi", "return", "refund"},
    {"стоимость", "оценка", "qiymat", "value", "valuation"},
    {"сумма", "summa", "summasi", "sum"},
]

# язык короткого вопроса (parse.detect_lang): характерные слова узбекской латиницы и английского
_UZ_MARKERS = {"sugurta", "modda", "boyicha", "qiymat", "summasi", "shartnoma", "tashkiloti",
               "qonun", "band", "oshsa", "toldirish", "nima", "qanday", "uchun", "bilan"}
_EN_MARKERS = {"insurance", "sum", "value", "article", "law", "contract", "policy", "claim",
               "insured", "agent", "premium"}

# =========================================================================== #
#  Известные молчания закона (faq.silence_match)
# =========================================================================== #
# Вопросы, на которые закон РУз ответа НЕ даёт: по добровольным видам это отдано правилам
# страхования и договору. Раньше поиск подбирал к ним нормы об ОБЯЗАТЕЛЬНОМ страховании
# (ПКМ 141 об ОСГО, ЗРУ-386 о перевозчике) и выдавал их за ответ. Теперь отвечаем честно
# и показываем ближайшую по смыслу норму.
#   all  — группы слов, из каждой должно встретиться хотя бы одно (сравнение по основам);
#   none — слова, при которых пункт не применяется (вопрос про обязательный вид — там нормы есть);
#   near — запрос, которым ищем ближайшую норму для ссылки.
NOT_COMPULSORY = ["осго", "обязательн", "majburiy", "compulsory", "mandatory", "осгор", "ifjms"]

SILENCES = [
    {
        "id": "claim_review_term",
        "q": {"ru": "Какой максимальный срок рассмотрения претензии по добровольному имущественному страхованию?",
              "uz": "Ixtiyoriy mulk sugʻurtasi boʻyicha daʼvoni koʻrib chiqishning eng koʻp muddati qancha?",
              "en": "What is the maximum claim handling period in voluntary property insurance?"},
        "a": {"ru": "Срок рассмотрения претензии по добровольному страхованию законом не установлен — "
                    "он определяется правилами страхования и договором. По обязательным видам сроки "
                    "задаёт отдельный акт по каждому виду.",
              "uz": "Ixtiyoriy sugʻurtada daʼvoni koʻrib chiqish muddati qonun bilan belgilanmagan — "
                    "u sugʻurta qoidalari va shartnoma bilan aniqlanadi. Majburiy turlarda muddatlar "
                    "har bir tur boʻyicha alohida hujjatda belgilanadi.",
              "en": "The claim handling period in voluntary insurance is not set by law — it is "
                    "defined by the insurance rules and the contract. For compulsory classes the "
                    "period is set by the act on each class."},
        "all": [["претенз", "заявлен", "davo", "daʼvo", "ariza", "claim"],
                ["срок", "muddat", "period", "term", "рассмотр", "korib", "handling"]],
        "none": NOT_COMPULSORY,
        "near": {"ru": "обязанности страховщика при наступлении страхового случая выплата",
                 "uz": "sugʻurta hodisasi yuz berganda sugʻurta toʻlovi majburiyati",
                 "en": "obligations of the insurer upon occurrence of the insured event payment"},
    },
    {
        "id": "payout_term",
        "q": {"ru": "В какой срок страховщик обязан выплатить возмещение по добровольному имущественному страхованию?",
              "uz": "Ixtiyoriy mulk sugʻurtasi boʻyicha toʻlov qaysi muddatda amalga oshiriladi?",
              "en": "Within what period must the insurer pay under voluntary property insurance?"},
        "a": {"ru": "Срок выплаты по добровольному имущественному страхованию законом не установлен — "
                    "его задают правила страхования и договор. Закон требует лишь выплатить при "
                    "наступлении страхового случая в порядке, предусмотренном договором.",
              "uz": "Ixtiyoriy mulk sugʻurtasida toʻlov muddati qonun bilan belgilanmagan — uni "
                    "sugʻurta qoidalari va shartnoma belgilaydi. Qonun faqat sugʻurta hodisasi yuz "
                    "berganda shartnomada nazarda tutilgan tartibda toʻlashni talab qiladi.",
              "en": "The payment period in voluntary property insurance is not set by law — it is "
                    "set by the insurance rules and the contract."},
        "all": [["выплат", "возмещ", "tolov", "toʻlov", "payment", "payout", "indemn"],
                ["срок", "muddat", "period", "term", "когда", "qachon", "when"]],
        "none": NOT_COMPULSORY,
        "near": {"ru": "страховая выплата при наступлении страхового случая обязанность страховщика",
                 "uz": "sugʻurta toʻlovi sugʻurta hodisasi sugʻurtalovchi majburiyati",
                 "en": "insurance payment upon the insured event obligation of the insurer"},
    },
    {
        "id": "franchise_size",
        "q": {"ru": "Какой размер франшизы установлен законом?",
              "uz": "Franshiza miqdori qonun bilan belgilanganmi?",
              "en": "What deductible (franchise) size does the law require?"},
        "a": {"ru": "Размер франшизы законом не установлен — он определяется правилами страхования "
                    "и договором. Закон только даёт понятие франшизы и требует указать её в договоре.",
              "uz": "Franshiza miqdori qonun bilan belgilanmagan — u sugʻurta qoidalari va shartnoma "
                    "bilan aniqlanadi. Qonun faqat franshiza tushunchasini beradi.",
              "en": "The size of the deductible is not set by law — it is defined by the insurance "
                    "rules and the contract; the law only defines the term."},
        "all": [["франши", "franshiz", "deduct", "franchis"],
                ["размер", "миqdor", "miqdor", "сколь", "qanch", "size", "amount", "максимал", "минимал"]],
        "none": [],
        "near": {"ru": "франшиза договор страхования",
                 "uz": "franshiza sugʻurta shartnomasi",
                 "en": "deductible franchise insurance contract"},
    },
    {
        "id": "commission_voluntary",
        "q": {"ru": "Какая комиссия агента допустима по добровольным видам страхования?",
              "uz": "Ixtiyoriy sugʻurta turlari boʻyicha agent komissiyasi qancha boʻlishi mumkin?",
              "en": "What agent commission is allowed in voluntary insurance classes?"},
        "a": {"ru": "Предельный размер комиссии установлен только Положением 3845 — не более 25% "
                    "от премии. Иных ограничений по добровольным видам закон не задаёт: размер "
                    "определяется агентским договором и тарифной политикой компании.",
              "uz": "Komissiyaning yuqori chegarasi faqat 3845-sonli Nizomda — mukofotning 25 "
                    "foizidan koʻp emas. Boshqa cheklovlar qonunda yoʻq: miqdor agentlik shartnomasi "
                    "va kompaniyaning tarif siyosati bilan belgilanadi.",
              "en": "The only cap is Regulation 3845 — no more than 25% of the premium. Otherwise "
                    "the commission is set by the agency contract and the company's tariff policy."},
        "all": [["комисс", "вознагражд", "komiss", "vositachilik", "commission"],
                ["добровол", "ixtiyo", "volunt", "размер", "miqdor", "сколь", "qanch", "max"]],
        "none": [],
        "near": {"ru": "страховой агент вознаграждение договор поручения",
                 "uz": "sugʻurta agenti vositachilik haqi shartnoma",
                 "en": "insurance agent remuneration agency contract"},
    },
    {
        "id": "tariff_rate_limit",
        "q": {"ru": "Установлен ли законом максимальный тариф по добровольному страхованию?",
              "uz": "Ixtiyoriy sugʻurta boʻyicha eng yuqori tarif qonun bilan belgilanganmi?",
              "en": "Does the law cap tariffs in voluntary insurance?"},
        "a": {"ru": "Нет. Тарифы по добровольным видам страховщик устанавливает сам — в рамках "
                    "своей тарифной политики; закон задаёт тарифы только по обязательным видам.",
              "uz": "Yoʻq. Ixtiyoriy turlar boʻyicha tariflarni sugʻurtalovchi oʻz tarif siyosati "
                    "doirasida belgilaydi; qonun faqat majburiy turlar tarifini belgilaydi.",
              "en": "No. In voluntary classes the insurer sets tariffs itself within its tariff "
                    "policy; the law sets tariffs only for compulsory classes."},
        "all": [["тариф", "ставк", "tarif", "rate", "premium"],
                ["максимал", "предел", "лимит", "eng", "chegara", "cap", "max", "limit", "установл"]],
        "none": NOT_COMPULSORY,
        "near": {"ru": "страховая премия страховой тариф договор страхования",
                 "uz": "sugʻurta mukofoti sugʻurta tarifi shartnoma",
                 "en": "insurance premium insurance tariff contract"},
    },
    {
        "id": "documents_for_payout",
        "q": {"ru": "Какой перечень документов нужен для выплаты по добровольному страхованию?",
              "uz": "Ixtiyoriy sugʻurta boʻyicha toʻlov uchun qanday hujjatlar kerak?",
              "en": "What documents are required for a payout in voluntary insurance?"},
        "a": {"ru": "Перечень документов по добровольным видам законом не установлен — он приводится "
                    "в правилах страхования и в договоре.",
              "uz": "Ixtiyoriy turlar boʻyicha hujjatlar roʻyxati qonun bilan belgilanmagan — u "
                    "sugʻurta qoidalari va shartnomada keltiriladi.",
              "en": "The list of documents for voluntary classes is not set by law — it is given in "
                    "the insurance rules and the contract."},
        "all": [["документ", "перечен", "hujjat", "royxat", "document", "list"],
                ["выплат", "возмещ", "tolov", "toʻlov", "payout", "payment", "случа", "hodis"]],
        "none": NOT_COMPULSORY,
        "near": {"ru": "страховая выплата документы страхового случая",
                 "uz": "sugʻurta toʻlovi hujjatlar sugʻurta hodisasi",
                 "en": "insurance payment documents insured event"},
    },
    {
        "id": "contract_term_length",
        "q": {"ru": "Установлен ли законом минимальный срок договора добровольного страхования?",
              "uz": "Ixtiyoriy sugʻurta shartnomasining eng kam muddati qonun bilan belgilanganmi?",
              "en": "Is there a statutory minimum term for a voluntary insurance contract?"},
        "a": {"ru": "Нет. Срок договора добровольного страхования законом не ограничен — стороны "
                    "определяют его сами в договоре.",
              "uz": "Yoʻq. Ixtiyoriy sugʻurta shartnomasining muddati qonun bilan cheklanmagan — "
                    "uni tomonlar shartnomada belgilaydi.",
              "en": "No. The term of a voluntary insurance contract is not limited by law — the "
                    "parties set it in the contract."},
        "all": [["срок", "muddat", "term", "period"],
                ["договор", "shartnoma", "contract", "polic", "polis"],
                ["минимал", "максимал", "eng", "чем", "установл", "minimum", "maximum"]],
        "none": NOT_COMPULSORY,
        "near": {"ru": "срок действия договора страхования вступление в силу",
                 "uz": "sugʻurta shartnomasining amal qilish muddati kuchga kirishi",
                 "en": "term of the insurance contract entry into force"},
    },
]

# =========================================================================== #
#  Документы конкурентов (competitors.py): термин вопроса и вид страхования
# =========================================================================== #
# термин вопроса → основа в заголовке колонки таблицы обзора конкурентов
TERM_STEMS = {"франшиз": "франшиз", "износ": "износ", "исключ": "исключ", "тариф": "тариф", "ставк": "тариф",
              "лимит": "лимит", "покрыт": "покрыт", "гибел": "гибел", "справк": "справк"}
# вид страхования в вопросе → основа заголовка раздела «## …» обзора и слова для документов
KIND_STEMS = {"каско": ("каско", ("каско", "kasko", "транспорт")),
              "имуществ": ("имуществ", ("имуществ",)), "груз": ("груз", ("груз",)),
              "ответствен": ("ответствен", ("ответствен",)), "кредит": ("кредит", ("кредит",)),
              "осаго": ("осаго", ("осаго",))}

# =========================================================================== #
#  Рынок и финансы страховщиков (market.detect)
# =========================================================================== #
MARKET_WORDS = ("рынок", "рынк", "рынке", "напп", "napp", "лидер", "рейтинг", "топ", "крупнейш", "крупн",
                "market", "leader", "ranking", "rank", "largest", "biggest", "top",
                "bozor", "yetakchi", "reyting", "eng katta", "компании рынка")
# «рыночная стоимость» — понятие оценки, а не вопрос о рынке
NOT_MARKET = ("market value", "bozor qiymat", "рыночн стоимост", "рыночная стоимость", "рыночной стоимост",
              "рыночную стоимость")
# признаки правового вопроса: такой вопрос уточнением рыночного не считается
LEGAL_MARK = ("если", "можно", "обязан", "закон", "стать", "норм", "договор", "agar", "mumkin", "qonun",
              "modda", "shartnoma", "if ", "law", "article", "contract", "must")
METRICS = [
    ("loss_ratio", ("убыточн", "loss ratio", "loss-ratio", "zararlilik", "zarar koeff", "lr")),
    ("share", ("доля", "долю", "доли", "share", "ulush")),
    ("rate", ("рыночн ставк", "ставк", "rate", "stavka", "тариф рынка", "market rate")),
    ("growth", ("рост", "прирост", "динамик", "вырос", "сократ", "growth", "grew", "dynamic", "trend",
                "osish", "osdi", "dinamika", "год к году", "y/y", "yoy")),
    ("payouts", ("выплат", "возмещен", "payout", "claims paid", "paid claims", "tolov", "tolangan")),
    ("premiums", ("преми", "сбор", "взнос", "premium", "gwp", "mukofot", "yigim")),
    ("own_funds", ("собственн средств", "собственные средства", "резерв", "own funds", "reserves",
                   "oz mablag", "zaxira")),
]
RANK_WORDS = ("лидер", "рейтинг", "топ", "крупнейш", "кто больше", "первое место", "самая", "самый",
              "leader", "top", "largest", "biggest", "ranking", "rank", "yetakchi", "reyting", "eng katta",
              "eng kop", "kim")
DEFINITION = ("что такое", "что значит", "что означает", "как рассчитыва", "как считает", "what is a ",
              "what does", "what is the definition", "nima degani", "nima bu", "qanday hisoblan",
              "how is the", "how do you calculate", "how to calculate")
FOLLOW_START = ("а ", "а что", "и ", "a ", "and ", "what about", "how about", "va ", "endi ")
CLASS_RE = [re.compile(r"(?:класс\w*|class(?:es)?|klass\w*|sinf\w*)\s*№?\s*(\d{1,2})\b"),
            re.compile(r"\b(\d{1,2})\s*-?\s*(?:класс\w*|class|klass\w*|sinf\w*)")]
# слово → строка market_stats; сила: True — явное название вида, False — слово могло попасть из бытового вопроса
CLASS_WORDS = [
    (("осаго", "osago", "автогражданк"), "osago", True),
    (("осгор", "osgor", "ifjms", "работодател", "employer"), "osgor", True),
    (("каско", "kasko", "casco", "автотранспорт", "наземн транспорт", "motor hull"), "cls3", True),
    (("груз", "cargo", "yuk "), "cls7", True),
    (("кредит", "credit", "kredit"), "cls14", True),
    (("огонь", "огня", "пожар", "имуществ", "fire", "property", "olov", "yongin", "mol-mulk"), "cls8", False),
    (("несчастн", "accident", "baxtsiz"), "cls1", True),
    (("болезн", "sickness", "kasallik"), "cls2", True),
    (("авиа", "aviation", "aviatsiya"), "cls5", True),
    (("морск", "marine", "dengiz"), "cls6", True),
    (("ответственност", "liability", "javobgarlik"), "cls13", False),
    (("поручительств", "гаранти", "surety", "kafillik"), "cls15", True),
    (("финансов", "financial risk", "moliyaviy"), "cls16", True),
    (("медицин", "medical", "tibbiy"), "cls18", True),
    (("жизн", "life insurance", "hayot"), "voluntary_life", True),
    (("обязательн", "compulsory", "mandatory", "majburiy"), "mandatory", True),
    (("добровольн", "voluntary", "ixtiyoriy"), "voluntary_general", True),
]
# кириллица и обиходные имена страховщиков → различимое слово из названия в отчёте НАПП
COMPANY_ALIASES = {
    "апекс": "apex", "apeks": "apex", "узбекинвест": "ozbekinvest", "узинвест": "ozbekinvest",
    "uzbekinvest": "ozbekinvest", "кафолат": "kafolat", "капитал": "kapital", "гросс": "gross",
    "инсон": "inson", "insonsugurta": "inson", "альфа": "alfa", "евроазия": "euroasia", "кафил": "kafil",
    "азия": "asia", "траст": "trust-insurance", "импекс": "impex-insurance", "алском": "alskom",
    "sqb": "sqb", "нео": "neo", "агросугурта": "ozagrosugurta", "узагро": "ozagrosugurta",
    "темирйул": "temiryol", "мозаик": "mosaic", "инфинити": "infinity", "квантум": "quantum",
    "имкон": "imkon", "ишонч": "ishonch", "ария": "aria", "халк": "xalq", "хамкор": "hamkor",
    "семург": "semurg", "глобал": "global", "май иншуранс": "my-insurance", "узре": "uzre", "uzre": "uzre",
}
LEGAL_FORM = {"aj", "qk", "mqst", "eisk", "sugurta", "insurance", "kompaniyasi", "tashkiloti", "corp",
              "group", "company", "ranee", "ранее", "ozbekiston", "the", "qayta"}
REGION_WORDS = ("регион", "област", "region", "viloyat", "hudud")
CLASS_DIM_WORDS = ("классы", "классам", "класса", "по классам", "classes", "klasslar", "вид страхования",
                   "виды страхования")
QUANTITY = ("сколько", "объем", "объём", "итог", "how much", "volume", "total", "qancha", "hajm", "jami")
AGO_WORDS = ("год назад", "прошл", "предыдущ", "year ago", "last year", "previous year", "prior year",
             "otgan yil", "bir yil oldin", "oldingi yil")
# Финансы страховщиков (company_rankings, рэнкинг snsratings.uz): слово → показатели (первый — главный)
FIN_WORDS = [
    (("уставн капитал", "уставный капитал", "уставного капитал", "акционерн", "share capital", "ustav kapital",
      "ustav fond", "aksiyador"), ["share_capital", "total_capital"]),
    (("капитал", "kapital", "capital", "equity"), ["total_capital", "share_capital"]),
    (("актив", "aktiv", "asset"), ["total_assets"]),
    (("резерв", "zaxira", "reserve"), ["reserves_net", "reserves_gross"]),
    (("рентабельн", "доходность капитал", "roe", "roa", "rentabel", "profitability", "return on"),
     ["roe", "roa", "roe_share_capital"]),
    (("прибыл", "убыток", "убытк", "foyda", "profit", "net income"), ["net_profit", "profit_before_tax"]),
    (("претензи", "davo", "claims received", "number of claims", "complaint"),
     ["claims_received", "claims_paid_count", "claims_refused", "claims_unsettled", "refused_to_claims"]),
    (("отказ", "отказан", "rad etil", "rad qilin", "refus", "rejected", "denied"),
     ["claims_refused", "refused_to_claims", "claims_received"]),
    (("выручк", "tushum", "revenue"), ["net_revenue"]),
    (("инвестиц", "investitsiya", "investment"), ["total_investments", "short_term_investments",
                                                   "long_term_investments"]),
    (("денежн средств", "денежные средства", "pul mablag", "cash"), ["cash"]),
    (("дебитор", "debitor", "receivable"), ["short_term_receivables", "insurance_receivables"]),
    (("подразделени", "филиал", "filial", "bolinma", "branches"), ["branches"]),
    (("административн", "mamuriy", "admin expense", "administrative"), ["admin_expenses", "admin_to_period_expenses"]),
    (("страховые обязательств", "страховых обязательств", "insurance liabilit", "sugurta majburiyat"),
     ["insurance_liabilities"]),
]
# «договоры» — показатель только с «сколько / количество / число»: иначе вопрос о праве
CONTRACT_WORDS = ("договор", "shartnoma", "contract", "polis")
COUNT_WORDS = ("сколько", "количеств", "число", "how many", "number of", "qancha", "nechta", "soni")
# неоднозначные слова («отказ», «претензия»): при правовом обороте вопрос не о статистике
LEGAL_VERB = ("вправе", "может ли", "можно ли", "должен", "обязан", "почему", "как оформ", "как подать",
              "как обжал", "что делать", "как быть", "жалоб", "в суд", "huquq", "mumkinmi", "nima qilish",
              "can the", "may the", "how to", "what to do")
AMBIG_FIN = ("отказ", "претензи", "davo", "rad etil", "rad qilin", "refus", "rejected", "denied", "complaint")
CARD_WORDS = ("расскажи про", "расскажи о", "расскажите про", "расскажите о", "о компании", "про компанию",
              "карточк", "профиль компании", "финансы компании", "финансовое состояние", "финансовые показател",
              "что известно о", "обзор компании", "tell me about", "about the company", "company profile",
              "financials of", "haqida", "moliyaviy holat", "moliyaviy korsatkich")

# «капитал» — и показатель, и название страховщика KAPITAL SUG'URTA: «капитал APEX» — это APEX
CAPITAL_TOKENS = ("капитал", "kapital", "capital")

# Условия продуктов других страховщиков — intent «competitor» (market.competitor_intent)
PRODUCT_TERMS = ("франшиз", "исключени", "услови", "правил", "оферт", "износ", "лимит", "покрыти", "тариф",
                 "ставк", "franshiza", "istisno", "shart", "qoida", "oferta", "eskirish", "limit", "qoplam", "tarif",
                 "stavka", "deductible", "exclusion", "terms", "conditions", "rules", "wear", "coverage", "tariff",
                 "rate", "offer")
COMPETITOR_MARK = ("конкурент", "других страховщ", "другие страховщ", "другой страхов", "других компан",
                   "у других", "raqobatchi", "boshqa sugurtalovchi", "boshqa kompaniya", "competitor",
                   "other insurer", "other companies")
SELL_WORDS = ("что прода", "что предлага", "какие продукты", "nima sot", "qanday mahsulot", "what does",
              "sells", "sell", "products of")
COMPARE_WORDS = ("сравни", "сравнить", "solishtir", "compare")
# русские слова запроса к индексу (документы конкурентов — на русском)
RU_TERMS = {"franshiza": "франшиза", "istisno": "исключения", "shart": "условия", "qoida": "правила",
            "kasko": "каско", "mulk": "имущество", "yuk": "грузы", "kredit": "кредит", "limit": "лимит",
            "tarif": "тариф", "eskirish": "износ", "qoplam": "покрытие", "deductible": "франшиза",
            "exclusion": "исключения", "terms": "условия", "conditions": "условия", "rules": "правила",
            "casco": "каско", "property": "имущество", "cargo": "грузы", "credit": "кредит", "wear": "износ",
            "coverage": "покрытие", "tariff": "тариф", "rate": "тариф", "sell": "продукты", "nima": "продукты"}
