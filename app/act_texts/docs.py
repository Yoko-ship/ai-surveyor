"""Документы: запрос филиала, договор и сверка с расчётом, источник условий и правки, отчёт кредитного бюро.
Часть словаря текстов акта (app/act_texts): склеивается в TX в __init__.py в прежнем порядке."""
from . import LANGS

DOC_KIND_LABELS = {
    "договор": {"ru": "договор", "uz": "shartnoma", "en": "contract"},
    "техпаспорт": {"ru": "техпаспорт", "uz": "texnik pasport", "en": "vehicle registration document"},
    "кадастр": {"ru": "кадастровый документ", "uz": "kadastr hujjati", "en": "cadastral document"},
    "отчёт оценщика": {"ru": "отчёт оценщика", "uz": "baholovchi hisoboti", "en": "valuation report"},
    "выписка": {"ru": "выписка", "uz": "koʻchirma", "en": "statement"},
    "штатное расписание": {"ru": "штатное расписание", "uz": "shtat jadvali", "en": "staff list"},
    "прочее": {"ru": "документ", "uz": "hujjat", "en": "document"},
}

DOC_KIND_LABELS["branch_request"] = {"ru": "запрос филиала", "uz": "filial soʻrovi", "en": "branch request"}

# 16 строк бланка запроса филиала
BR_ROW_LABELS = {
    "product_code": {"ru": "Вид страхования (код по приказу)", "uz": "Sugʻurta turi (buyruq boʻyicha kod)",
                     "en": "Insurance product (code per order)"},
    "policyholder": {"ru": "Страхователь", "uz": "Sugʻurta qildiruvchi", "en": "Policyholder"},
    "beneficiary": {"ru": "Выгодоприобретатель", "uz": "Naf oluvchi", "en": "Beneficiary"},
    "pledger": {"ru": "Залогодатель", "uz": "Garovga qoʻyuvchi", "en": "Pledgor"},
    "object": {"ru": "Объект страхования", "uz": "Sugʻurta obyekti", "en": "Insured object"},
    "object_value": {"ru": "Страховая стоимость", "uz": "Sugʻurta qiymati", "en": "Insured value"},
    "sum_insured": {"ru": "Страховая сумма", "uz": "Sugʻurta summasi", "en": "Sum insured"},
    "franchise": {"ru": "Франшиза", "uz": "Franshiza", "en": "Deductible"},
    "tariff": {"ru": "Страховой тариф", "uz": "Sugʻurta tarifi", "en": "Insurance rate"},
    "premium": {"ru": "Страховая премия", "uz": "Sugʻurta mukofoti", "en": "Insurance premium"},
    "term": {"ru": "Срок страхования", "uz": "Sugʻurta muddati", "en": "Insurance term"},
    "contract_terms": {"ru": "Изменение/дополнение стандартных условий", "uz": "Standart shartlarni oʻzgartirish/qoʻshish",
                       "en": "Changes/additions to standard terms"},
    "counterparty": {"ru": "Контрагент", "uz": "Kontragent", "en": "Counterparty"},
    "contracts_count": {"ru": "Число договоров", "uz": "Shartnoma miqdori", "en": "Number of contracts"},
    "osgor_class": {"ru": "Класс (по ОСГОР)", "uz": "Klass (IFJMS boʻyicha)", "en": "Class (per Empl. liab.)"},
    "additional_info": {"ru": "Дополнительные сведения", "uz": "Qoʻshimcha maʼlumot", "en": "Additional information"},
}

RQ_SOURCE_LABELS = {
    "document": {"ru": "из файла запроса (разбор текста)", "uz": "soʻrov faylidan (matn tahlili)",
                 "en": "from the request file (text parsing)"},
    "photo": {"ru": "со скана запроса (распознано моделью)", "uz": "soʻrov skanidan (model tanidi)",
              "en": "from the request scan (read by the model)"},
    "input": {"ru": "введено сотрудником", "uz": "xodim kiritgan", "en": "entered by staff"},
    "session": {"ru": "из загрузки запроса (без правок)", "uz": "soʻrov yuklamasidan (tahrirsiz)",
                "en": "from the request upload (unedited)"},
}

# ================================================================================================
#  Договор страхования: чтение и сверка с расчётом акта (30.09.2026, app/contract_read.py)
# ================================================================================================
DOC_KIND_LABELS["contract"] = {"ru": "договор страхования", "uz": "sugʻurta shartnomasi", "en": "insurance contract"}

CT_SOURCE_LABELS = {
    "document": {"ru": "из файла договора (разбор текста)", "uz": "shartnoma faylidan (matn tahlili)",
                 "en": "from the contract file (text parsing)"},
    "document_ai": {"ru": "из текста договора (часть полей прочитана моделью)",
                    "uz": "shartnoma matnidan (baʼzi maydonlarni model oʻqigan)",
                    "en": "from the contract text (some fields read by the model)"},
    "photo": {"ru": "со скана договора (распознано моделью)", "uz": "shartnoma skanidan (model tanidi)",
              "en": "from the contract scan (read by the model)"},
    "input": {"ru": "введено сотрудником", "uz": "xodim kiritgan", "en": "entered by staff"},
    "session": {"ru": "из загрузки договора (без правок)", "uz": "shartnoma yuklamasidan (tahrirsiz)",
                "en": "from the contract upload (unedited)"},
}

# ключевые поля договора (found / missing в блоке contract)
CT_FIELD_LABELS = {
    "contract_no": {"ru": "Номер договора", "uz": "Shartnoma raqami", "en": "Contract number"},
    "contract_date": {"ru": "Дата договора", "uz": "Shartnoma sanasi", "en": "Contract date"},
    "policyholder": {"ru": "Страхователь", "uz": "Sugʻurta qildiruvchi", "en": "Policyholder"},
    "object": {"ru": "Объект страхования", "uz": "Sugʻurta obyekti", "en": "Insured object"},
    "sum_insured": {"ru": "Страховая сумма", "uz": "Sugʻurta summasi", "en": "Sum insured"},
    "tariff_pct": {"ru": "Тариф", "uz": "Tarif", "en": "Rate"},
    "premium": {"ru": "Страховая премия", "uz": "Sugʻurta mukofoti", "en": "Premium"},
    "term": {"ru": "Срок страхования", "uz": "Sugʻurta muddati", "en": "Insurance term"},
    "franchise": {"ru": "Франшиза", "uz": "Franshiza", "en": "Deductible"},
    "covered_risks": {"ru": "Застрахованные риски", "uz": "Sugʻurtalangan xavflar", "en": "Insured risks"},
}

# существенные условия (ГК РУз, ст. 929) — короткие названия
CT_ESSENTIAL_LABELS = {
    "object": {"ru": "объект страхования", "uz": "sugʻurta obyekti", "en": "insured object"},
    "insured_event": {"ru": "страховой случай (застрахованные риски)", "uz": "sugʻurta hodisasi (xavflar)",
                      "en": "insured event (risks)"},
    "sum_insured": {"ru": "размер страховой суммы", "uz": "sugʻurta summasi miqdori", "en": "sum insured"},
    "premium": {"ru": "размер страховой премии", "uz": "sugʻurta mukofoti miqdori", "en": "premium amount"},
    "term": {"ru": "срок действия договора", "uz": "shartnomaning amal qilish muddati", "en": "contract term"},
}

RISK_LABELS = {
    "all_risks": {"ru": "все риски", "uz": "barcha xavflar", "en": "all risks"},
    "fire": {"ru": "пожар", "uz": "yongʻin", "en": "fire"},
    "lightning": {"ru": "удар молнии", "uz": "chaqmoq urishi", "en": "lightning"},
    "explosion": {"ru": "взрыв", "uz": "portlash", "en": "explosion"},
    "water": {"ru": "залив, повреждение водой", "uz": "suv bosishi", "en": "water damage"},
    "natural": {"ru": "стихийные бедствия", "uz": "tabiiy ofatlar", "en": "natural disasters"},
    "earthquake": {"ru": "землетрясение", "uz": "zilzila", "en": "earthquake"},
    "flood": {"ru": "наводнение", "uz": "toshqin", "en": "flood"},
    "storm": {"ru": "буря, ураган", "uz": "boʻron", "en": "storm"},
    "hail": {"ru": "град", "uz": "doʻl", "en": "hail"},
    "landslide": {"ru": "оползень, сель", "uz": "koʻchki, sel", "en": "landslide, mudflow"},
    "theft": {"ru": "кража, грабёж, разбой", "uz": "oʻgʻirlik, talonchilik", "en": "theft, robbery"},
    "third_party": {"ru": "противоправные действия третьих лиц", "uz": "uchinchi shaxslarning gʻayriqonuniy harakatlari",
                    "en": "malicious acts of third parties"},
    "vehicle_impact": {"ru": "наезд транспорта", "uz": "transport urilishi", "en": "vehicle impact"},
    "aircraft": {"ru": "падение летательных аппаратов", "uz": "uchish apparatlari qulashi", "en": "aircraft"},
    "glass": {"ru": "бой стёкол", "uz": "oynalar sinishi", "en": "glass breakage"},
    "breakdown": {"ru": "поломка, авария оборудования", "uz": "uskunaning buzilishi", "en": "machinery breakdown"},
    "electrical": {"ru": "короткое замыкание", "uz": "qisqa tutashuv", "en": "electrical damage"},
    "collision": {"ru": "ДТП, столкновение", "uz": "yoʻl-transport hodisasi", "en": "collision"},
    # личное страхование (классы 1 и 2)
    "death": {"ru": "смерть", "uz": "vafot etish", "en": "death"},
    "temp_disability": {"ru": "временная утрата трудоспособности", "uz": "mehnat qobiliyatini vaqtincha yoʻqotish",
                        "en": "temporary disability"},
    "disability": {"ru": "инвалидность", "uz": "nogironlik", "en": "permanent disability"},
    "injury": {"ru": "травма", "uz": "jarohat", "en": "injury"},
    "illness": {"ru": "заболевание", "uz": "kasallik", "en": "illness"},
    "other": {"ru": "другое", "uz": "boshqa", "en": "other"},
}

EXCLUSION_LABELS = {
    "war": {"ru": "военные действия", "uz": "urush harakatlari", "en": "war"},
    "terrorism": {"ru": "терроризм", "uz": "terrorizm", "en": "terrorism"},
    "nuclear": {"ru": "ядерный взрыв, радиация", "uz": "yadro portlashi, radiatsiya", "en": "nuclear risks"},
    "riots": {"ru": "забастовки, беспорядки", "uz": "ish tashlash, gʻalayonlar", "en": "strikes, riots"},
    "intent": {"ru": "умысел страхователя", "uz": "sugʻurta qildiruvchining qasdi", "en": "wilful acts"},
    "gross_negligence": {"ru": "грубая неосторожность", "uz": "qoʻpol ehtiyotsizlik", "en": "gross negligence"},
    "wear": {"ru": "износ, коррозия", "uz": "eskirish, zanglash", "en": "wear and tear, corrosion"},
    "confiscation": {"ru": "конфискация, арест", "uz": "musodara, hibsga olish", "en": "confiscation"},
    "defects": {"ru": "дефекты, брак", "uz": "nuqsonlar", "en": "defects"},
    "consequential": {"ru": "косвенные убытки, упущенная выгода", "uz": "bilvosita zararlar", "en": "consequential loss"},
    "cyber": {"ru": "кибер-риски", "uz": "kiber xavflar", "en": "cyber risks"},
    "mould": {"ru": "плесень, грибок", "uz": "mogʻor", "en": "mould"},
    "pollution": {"ru": "загрязнение", "uz": "ifloslanish", "en": "pollution"},
    "intoxication": {"ru": "алкогольное или наркотическое опьянение", "uz": "mast holat", "en": "intoxication"},
    # личное страхование
    "doping": {"ru": "применение допинга", "uz": "doping qoʻllash", "en": "use of doping"},
    "suicide": {"ru": "самоубийство или покушение на него", "uz": "oʻz joniga qasd qilish", "en": "suicide"},
    "self_harm": {"ru": "умышленное причинение вреда своему здоровью", "uz": "oʻziga qasddan shikast yetkazish",
                  "en": "self-inflicted injury"},
    "crime": {"ru": "совершение преступления", "uz": "jinoyat sodir etish", "en": "committing a crime"},
    "other": {"ru": "другое", "uz": "boshqa", "en": "other"},
}

# незаполненные поля бланка договора (contract.blank)
CT_BLANK_LABELS = dict(CT_FIELD_LABELS, place={"ru": "Место заключения", "uz": "Tuzilgan joyi",
                                               "en": "Place of signing"})

# колонки таблицы застрахованных по профессиям (приложение к договору личного страхования)
CT_SCHED_LABELS = {
    "profession": {"ru": "Профессия (род занятий)", "uz": "Kasbi (faoliyat turi)", "en": "Occupation"},
    "count": {"ru": "Количество застрахованных", "uz": "Sugʻurtalanganlar soni", "en": "Number of insured persons"},
    "personal_sum": {"ru": "Персональная страховая сумма", "uz": "Shaxsiy sugʻurta summasi",
                     "en": "Sum insured per person"},
    "rate": {"ru": "Ставка, %", "uz": "Stavka, %", "en": "Rate, %"},
    "premium_one": {"ru": "Премия за одного застрахованного", "uz": "Bir kishi uchun sugʻurta mukofoti",
                    "en": "Premium per person"},
    "sum_total": {"ru": "Страховая сумма всего", "uz": "Jami sugʻurta summasi", "en": "Total sum insured"},
    "premium_total": {"ru": "Страховая премия всего", "uz": "Jami sugʻurta mukofoti", "en": "Total premium"},
}

PAYMENT_MODE_LABELS = {"single": {"ru": "единовременно", "uz": "bir martalik", "en": "in one payment"},
                       "installments": {"ru": "в рассрочку", "uz": "boʻlib-boʻlib", "en": "in instalments"}}

# запрос филиала против договора
X_LABELS = {
    "product_code": {"ru": "Код вида страхования", "uz": "Sugʻurta turi kodi", "en": "Product code"},
    "sum_insured": {"ru": "Страховая сумма", "uz": "Sugʻurta summasi", "en": "Sum insured"},
    "object_value": {"ru": "Страховая стоимость", "uz": "Sugʻurta qiymati", "en": "Insured value"},
    "tariff_pct": {"ru": "Тариф", "uz": "Tarif", "en": "Rate"},
    "premium": {"ru": "Страховая премия", "uz": "Sugʻurta mukofoti", "en": "Premium"},
    "term": {"ru": "Срок", "uz": "Muddat", "en": "Term"},
    "franchise": {"ru": "Франшиза", "uz": "Franshiza", "en": "Deductible"},
    "object": {"ru": "Объект", "uz": "Obyekt", "en": "Object"},
}

# «запрос» → «договор» в текстах сверки: одни и те же проверки (act_engine.request_check), другой документ
_CT_REPL = {
    "ru": [("Запрос филиала сходится", "Договор сходится"), ("Запрос филиала расходится", "Договор расходится"),
           ("Тариф в запросе филиала", "Тариф в договоре"), ("В запросе филиала нет", "В договоре нет"),
           ("Премия в запросе филиала", "Премия в договоре"), ("Франшиза в запросе филиала", "Франшиза в договоре"),
           ("Срок в запросе филиала", "Срок в договоре"),
           ("по тарифу запроса", "по тарифу договора"), ("Премия по тарифу запроса", "Премия по тарифу договора"),
           ("в запросе", "в договоре"), ("В запросе", "В договоре"), ("из запроса", "из договора"),
           ("запроса", "договора"), ("запрос", "договор")],
    "uz": [("Filial soʻrovi dalolatnoma", "Shartnoma dalolatnoma"), ("Filial soʻrovi", "Shartnoma"),
           ("Filial soʻrovidagi", "Shartnomadagi"), ("Filial soʻrovida", "Shartnomada"),
           ("soʻrov tarifi", "shartnoma tarifi"), ("Soʻrov tarifi", "Shartnoma tarifi"),
           ("soʻrovdagi", "shartnomadagi"), ("soʻrovda", "shartnomada"), ("soʻrovdan", "shartnomadan"),
           ("Soʻrovdagi", "Shartnomadagi")],
    "en": [("The branch request", "The contract"), ("the branch request", "the contract"),
           ("branch request", "contract"), ("the request rate", "the contract rate"),
           ("the request premium", "the contract premium"), ("The request", "The contract"),
           ("the request", "the contract"), ("request premium", "contract premium"),
           ("in the request", "in the contract"), ("request", "contract")],
}

def _ct_from_rq(key_from: str, tx: dict) -> dict:
    row = tx[key_from]
    out = {}
    for lg in LANGS:
        s = row[lg]
        for a, b in _CT_REPL[lg]:
            s = s.replace(a, b)
        out[lg] = s
    return out


def derive_ct(tx: dict) -> None:
    """Тексты сверки с договором — из текстов сверки с запросом филиала («запрос» → «договор»).
    Вызывается при сборке TX сразу после блока запроса филиала: позже в TX ключи ct_* уточняются."""
    tx.update({"ct" + k[2:]: _ct_from_rq(k, tx) for k in list(tx)
               if k.startswith("rq_") and not k.startswith(("rq_v_", "rq_l_")) and k != "rq_how_title"})
    tx.update({"c_ct_" + k[5:]: _ct_from_rq(k, tx) for k in list(tx) if k.startswith("c_rq_")})

# ================================================================================================
#  Источник условий запроса и договора, правки сотрудника, заявление (30.09.2026, вечер)
# ================================================================================================
DOC_KIND_LABELS["application"] = {"ru": "заявление на страхование", "uz": "sugʻurta arizasi",
                                  "en": "insurance application"}

# поля условий документа — подписи правок «было → стало» и перечня «прочитано моделью»
EDIT_LABELS = {
    "product_code": {"ru": "Код вида страхования", "uz": "Sugʻurta turi kodi", "en": "Product code"},
    "sum_insured": {"ru": "Страховая сумма", "uz": "Sugʻurta summasi", "en": "Sum insured"},
    "object_value": {"ru": "Страховая стоимость", "uz": "Sugʻurta qiymati", "en": "Insured value"},
    "tariff_pct": {"ru": "Тариф", "uz": "Tarif", "en": "Rate"},
    "premium": {"ru": "Страховая премия", "uz": "Sugʻurta mukofoti", "en": "Premium"},
    "franchise": {"ru": "Франшиза", "uz": "Franshiza", "en": "Deductible"},
    "term": {"ru": "Срок страхования", "uz": "Sugʻurta muddati", "en": "Insurance term"},
    "term_from": {"ru": "Начало срока", "uz": "Muddat boshlanishi", "en": "Term start"},
    "term_to": {"ru": "Окончание срока", "uz": "Muddat tugashi", "en": "Term end"},
    "term_days": {"ru": "Срок, дней", "uz": "Muddat, kun", "en": "Term, days"},
    "cadastre_no": {"ru": "Кадастровый номер", "uz": "Kadastr raqami", "en": "Cadastral number"},
    "object_description": {"ru": "Объект страхования", "uz": "Sugʻurta obyekti", "en": "Insured object"},
    "contract_no": {"ru": "Номер договора", "uz": "Shartnoma raqami", "en": "Contract number"},
    "contract_date": {"ru": "Дата договора", "uz": "Shartnoma sanasi", "en": "Contract date"},
    "currency": {"ru": "Валюта", "uz": "Valyuta", "en": "Currency"},
    "covered_risks": {"ru": "Застрахованные риски", "uz": "Sugʻurtalangan xavflar", "en": "Insured risks"},
    "exclusions": {"ru": "Исключения", "uz": "Istisnolar", "en": "Exclusions"},
    "payment_mode": {"ru": "Порядок оплаты", "uz": "Toʻlov tartibi", "en": "Payment terms"},
    "payments": {"ru": "График платежей", "uz": "Toʻlov jadvali", "en": "Payment schedule"},
    "items": {"ru": "Суммы по объектам", "uz": "Obyektlar boʻyicha summalar", "en": "Per-item sums"},
    "areas": {"ru": "Площадь", "uz": "Maydon", "en": "Area"},
    "address": {"ru": "Адрес объекта", "uz": "Obyekt manzili", "en": "Object address"},
    "territory": {"ru": "Территория страхования", "uz": "Sugʻurta hududi", "en": "Territory"},
    "special_terms": {"ru": "Особые условия", "uz": "Maxsus shartlar", "en": "Special terms"},
    "notice": {"ru": "Срок уведомления о событии", "uz": "Hodisa haqida xabar berish muddati",
               "en": "Notice period"},
    "product_name": {"ru": "Название продукта", "uz": "Mahsulot nomi", "en": "Product name"},
    "place": {"ru": "Место заключения", "uz": "Tuzilgan joyi", "en": "Place of signing"},
    "purpose": {"ru": "Назначение объекта", "uz": "Obyekt maqsadi", "en": "Purpose"},
    "year_built": {"ru": "Год постройки", "uz": "Qurilgan yili", "en": "Year built"},
    "vin": {"ru": "VIN или заводской номер", "uz": "VIN yoki zavod raqami", "en": "VIN or serial number"},
}

# вид объекта (подсказка класса) в сверке «запрос ↔ договор»
X_KIND_LABELS = {
    "building": {"ru": "здание, помещение", "uz": "bino, xona", "en": "building, premises"},
    "equipment": {"ru": "оборудование", "uz": "uskuna", "en": "equipment"},
    "vehicle": {"ru": "транспорт", "uz": "transport", "en": "vehicle"},
    "special_machinery": {"ru": "спецтехника", "uz": "maxsus texnika", "en": "special machinery"},
    "cargo": {"ru": "груз", "uz": "yuk", "en": "cargo"},
    "other": {"ru": "другое", "uz": "boshqa", "en": "other"},
}

_ESS_NOT_FOUND = {
    "ru": "в тексте договора не найдено условие: {what} (ГК РУз, ст. 929). Проверьте договор; если условия "
          "действительно нет — договор нужно дополнить",
    "uz": "shartnoma matnida shart topilmadi: {what} (OʻzR FK, 929-modda). Shartnomani tekshiring; agar shart "
          "haqiqatan yoʻq boʻlsa — shartnomani toʻldirish kerak",
    "en": "the contract text does not contain the term: {what} (Civil Code, Art. 929). Check the contract; if "
          "the term is really missing, the contract must be supplemented"}

_ESS_NOT_FOUND_CAP = {lg: s[0].upper() + s[1:] for lg, s in _ESS_NOT_FOUND.items()}

DOC_KIND_LABELS["credit_report"] = {"ru": "отчёт кредитного бюро", "uz": "kredit byurosi hisoboti",
                                    "en": "credit bureau report"}

# откуда значения отчёта бюро
CR_SOURCE_LABELS = {
    "document": {"ru": "из отчёта (файл с текстом)", "uz": "hisobotdan (matnli fayl)", "en": "from the report (text file)"},
    "photo": {"ru": "со скана, прочитано моделью", "uz": "skandan, model oʻqigan", "en": "from the scan, read by the model"},
    "input": {"ru": "введено сотрудником", "uz": "xodim kiritgan", "en": "entered by staff"},
}

CR_SUBJECT_LABELS = {
    "legal": {"ru": "юридическое лицо", "uz": "yuridik shaxs", "en": "legal entity"},
    "individual": {"ru": "физическое лицо", "uz": "jismoniy shaxs", "en": "individual"},
}

# подписи полей отчёта бюро (для экрана и правок «было → стало»)
CR_FIELD_LABELS = {
    "report_date": {"ru": "Дата отчёта", "uz": "Hisobot sanasi", "en": "Report date"},
    "subject_type": {"ru": "Тип субъекта", "uz": "Subyekt turi", "en": "Subject type"},
    "name": {"ru": "Наименование", "uz": "Nomi", "en": "Name"},
    "inn": {"ru": "ИНН", "uz": "STIR", "en": "TIN"},
    "oked": {"ru": "ОКЭД", "uz": "IFUT", "en": "Activity code (OKED)"},
    "score": {"ru": "Скоринговый балл", "uz": "Skoring bali", "en": "Credit score"},
    "score_class": {"ru": "Класс оценки", "uz": "Baholash sinfi", "en": "Score class"},
    "score_version": {"ru": "Версия скоринга", "uz": "Skoring versiyasi", "en": "Scoring version"},
    "overview.applications": {"ru": "Заявки", "uz": "Arizalar", "en": "Applications"},
    "overview.contracts": {"ru": "Договоры", "uz": "Shartnomalar", "en": "Contracts"},
    "overview.contingent": {"ru": "Условные обязательства", "uz": "Shartli majburiyatlar",
                            "en": "Contingent liabilities"},
    "overview.inquiries": {"ru": "Запросы и подписки", "uz": "Soʻrovlar va obunalar", "en": "Inquiries and subscriptions"},
    "overview.avg_monthly_payment": {"ru": "Среднемесячный платёж", "uz": "Oʻrtacha oylik toʻlov",
                                     "en": "Average monthly payment"},
    "overview.overdue_principal_count": {"ru": "Просрочек основного долга", "uz": "Asosiy qarz boʻyicha kechikishlar",
                                         "en": "Principal overdue cases"},
    "overview.max_overdue_principal_days": {"ru": "Максимальная просрочка основного долга, дней",
                                            "uz": "Asosiy qarz boʻyicha eng uzoq kechikish, kun",
                                            "en": "Longest principal overdue, days"},
    "overview.max_overdue_principal_amount": {"ru": "Максимальная просрочка основного долга, сумма",
                                              "uz": "Asosiy qarz boʻyicha eng katta kechikish, summa",
                                              "en": "Largest principal overdue, amount"},
    "overview.max_overdue_interest_days": {"ru": "Максимальная непрерывная просрочка процентов, дней",
                                           "uz": "Foizlar boʻyicha eng uzoq uzluksiz kechikish, kun",
                                           "en": "Longest continuous interest overdue, days"},
    "overview.overdue_interest_total": {"ru": "Всего просроченных процентов", "uz": "Jami kechiktirilgan foizlar",
                                        "en": "Total overdue interest"},
    "active.count": {"ru": "Действующих договоров", "uz": "Amaldagi shartnomalar", "en": "Active contracts"},
    "active.total_debt": {"ru": "Остаток задолженности", "uz": "Qarz qoldigʻi", "en": "Outstanding debt"},
    "active.overdue": {"ru": "Просроченная часть", "uz": "Kechiktirilgan qism", "en": "Overdue part"},
    "active.monthly_payment": {"ru": "Среднемесячный платёж по действующим", "uz": "Amaldagilar boʻyicha oylik toʻlov",
                               "en": "Monthly payment on active contracts"},
    "active.creditors": {"ru": "Кредиторы", "uz": "Kreditorlar", "en": "Creditors"},
}

TX_BRANCH_REQUEST = {
    # ---------- распознавание бланка ----------
    "br_individual": {"ru": "{role}: физическое лицо — данные не извлекаются и не хранятся",
                      "uz": "{role}: jismoniy shaxs — maʼlumotlar olinmaydi va saqlanmaydi",
                      "en": "{role}: an individual — the data is not extracted or stored"},
    "br_term_unread": {"ru": "Срок страхования в запросе не разобран — введите даты вручную",
                       "uz": "Soʻrovdagi sugʻurta muddati tahlil qilinmadi — sanalarni qoʻlda kiriting",
                       "en": "The insurance term in the request could not be read — enter the dates manually"},
    "br_no_tariff": {"ru": "Тариф в запросе не найден — сверка тарифа и премии невозможна",
                     "uz": "Soʻrovda tarif topilmadi — tarif va mukofotni solishtirib boʻlmaydi",
                     "en": "No rate was found in the request — the rate and premium cannot be checked"},

    # ---------- сверка с запросом филиала (раздел 4) ----------
    "rq_title": {"ru": "Сверка с запросом филиала", "uz": "Filial soʻrovi bilan solishtirish",
                 "en": "Check against the branch request"},
    "rq_how_title": {"ru": "Как сверено", "uz": "Qanday solishtirildi", "en": "How it was checked"},
    "rq_l_tariff_min": {"ru": "Тариф и минимальная ставка", "uz": "Tarif va eng kam stavka",
                        "en": "Rate vs minimum rate"},
    "rq_l_tariff_act": {"ru": "Тариф и ставка акта", "uz": "Tarif va dalolatnoma stavkasi",
                        "en": "Rate vs report rate"},
    "rq_l_premium_request": {"ru": "Премия по тарифу запроса", "uz": "Soʻrov tarifi boʻyicha mukofot",
                             "en": "Premium at the request rate"},
    "rq_l_premium_act": {"ru": "Премия акта (справочно)", "uz": "Dalolatnoma mukofoti (maʼlumot uchun)",
                         "en": "Report premium (for reference)"},
    "rq_l_sum_value": {"ru": "Сумма и стоимость (справочно)", "uz": "Summa va qiymat (maʼlumot uchun)",
                       "en": "Sum insured vs value (for reference)"},
    "rq_l_franchise": {"ru": "Франшиза", "uz": "Franshiza", "en": "Deductible"},
    "rq_l_term": {"ru": "Срок", "uz": "Muddat", "en": "Term"},
    "rq_v_ok": {"ru": "сходится", "uz": "mos keladi", "en": "matches"},
    "rq_v_differs": {"ru": "расхождение", "uz": "farq bor", "en": "differs"},
    "rq_v_below_min": {"ru": "ниже минимума", "uz": "eng kam stavkadan past", "en": "below minimum"},
    "rq_v_missing": {"ru": "нет данных", "uz": "maʼlumot yoʻq", "en": "no data"},
    "rq_summary_ok": {"ru": "Запрос филиала сходится с расчётом акта.",
                      "uz": "Filial soʻrovi dalolatnoma hisob-kitobiga mos keladi.",
                      "en": "The branch request matches the report calculation."},
    "rq_summary_differs": {"ru": "Запрос филиала расходится с расчётом акта — расхождения ниже, решение за андеррайтером.",
                           "uz": "Filial soʻrovi dalolatnoma hisob-kitobidan farq qiladi — farqlar quyida, qaror "
                                 "anderrayterda.",
                           "en": "The branch request differs from the report calculation — see the differences "
                                 "below; the underwriter decides."},
    "rq_summary_below_min": {"ru": "Тариф в запросе филиала ниже минимального по тарифной политике — нужно решение "
                                   "андеррайтера (отступление от тарифной политики).",
                             "uz": "Filial soʻrovidagi tarif tarif siyosati boʻyicha eng kam stavkadan past — "
                                   "anderrayter qarori kerak (tarif siyosatidan chetga chiqish).",
                             "en": "The rate in the branch request is below the tariff-policy minimum — an "
                                   "underwriter decision (a deviation from the tariff policy) is required."},
    "rq_summary_missing": {"ru": "В запросе филиала нет данных для сверки.",
                           "uz": "Filial soʻrovida solishtirish uchun maʼlumot yoʻq.",
                           "en": "The branch request has no data to check."},
    "rq_tariff_none": {"ru": "тариф в запросе не указан", "uz": "soʻrovda tarif koʻrsatilmagan",
                       "en": "the request states no rate"},
    "rq_tariff_min_na": {"ru": "тариф в запросе {req}; минимальной ставки продукта в справочнике нет — сравнить не с чем",
                         "uz": "soʻrovdagi tarif {req}; maʼlumotnomada mahsulotning eng kam stavkasi yoʻq — "
                               "solishtirib boʻlmaydi",
                         "en": "the request rate is {req}; the product has no minimum rate in the reference — "
                               "nothing to compare with"},
    "rq_tariff_min_ok": {"ru": "тариф в запросе {req} не ниже минимального {min}",
                         "uz": "soʻrovdagi tarif {req} eng kam stavka {min} dan past emas",
                         "en": "the request rate {req} is not below the minimum {min}"},
    "rq_tariff_min_below_min": {"ru": "тариф в запросе {req} ниже минимального по тарифной политике {min} — требуется "
                                      "решение андеррайтера/отступление",
                                "uz": "soʻrovdagi tarif {req} tarif siyosati boʻyicha eng kam stavka {min} dan past — "
                                      "anderrayter qarori/chetga chiqish kerak",
                                "en": "the request rate {req} is below the tariff-policy minimum {min} — an "
                                      "underwriter decision/deviation is required"},
    "rq_tariff_act_na": {"ru": "тариф в запросе {req}; ставка акта не определена",
                         "uz": "soʻrovdagi tarif {req}; dalolatnoma stavkasi aniqlanmagan",
                         "en": "the request rate is {req}; the report rate is not determined"},
    "rq_tariff_act_ok": {"ru": "тариф в запросе {req} не ниже ставки акта {calc}",
                         "uz": "soʻrovdagi tarif {req} dalolatnoma stavkasi {calc} dan past emas",
                         "en": "the request rate {req} is not below the report rate {calc}"},
    "rq_tariff_act_differs": {"ru": "тариф в запросе {req} ниже ставки акта {calc} (на {diff})",
                              "uz": "soʻrovdagi tarif {req} dalolatnoma stavkasi {calc} dan past ({diff} ga)",
                              "en": "the request rate {req} is below the report rate {calc} (by {diff})"},
    "rq_premium_none": {"ru": "премия в запросе не указана", "uz": "soʻrovda mukofot koʻrsatilmagan",
                        "en": "the request states no premium"},
    "rq_premium_request_na": {"ru": "премия в запросе {req}; без тарифа или срока пересчитать её нельзя",
                              "uz": "soʻrovdagi mukofot {req}; tarif yoki muddatsiz uni qayta hisoblab boʻlmaydi",
                              "en": "the request premium is {req}; it cannot be recalculated without the rate or term"},
    "rq_premium_request_ok": {"ru": "в запросе {req}, по тарифу запроса {calc} — разница {diff} в пределах "
                                    "округления ({tol})",
                              "uz": "soʻrovda {req}, soʻrov tarifi boʻyicha {calc} — farq {diff} yaxlitlash doirasida "
                                    "({tol})",
                              "en": "{req} in the request, {calc} at the request rate — the difference {diff} is within "
                                    "rounding ({tol})"},
    "rq_premium_request_differs": {"ru": "в запросе {req}, по тарифу запроса {rate} за {days} дн. — {calc}: "
                                         "расхождение {diff}",
                                   "uz": "soʻrovda {req}, soʻrov tarifi {rate} boʻyicha {days} kun uchun — {calc}: "
                                         "farq {diff}",
                                   "en": "{req} in the request; at the request rate {rate} for {days} days — {calc}: "
                                         "difference {diff}"},
    "rq_premium_act_ok": {"ru": "премия в запросе {req}, премия акта {calc} — сходятся",
                          "uz": "soʻrovdagi mukofot {req}, dalolatnoma mukofoti {calc} — mos",
                          "en": "request premium {req}, report premium {calc} — they match"},
    "rq_premium_act_differs": {"ru": "премия в запросе {req}, премия акта по ставке акта {rate} — {calc} (разница {diff})",
                               "uz": "soʻrovdagi mukofot {req}, dalolatnoma stavkasi {rate} boʻyicha mukofot — "
                                     "{calc} (farq {diff})",
                               "en": "request premium {req}; report premium at the report rate {rate} — {calc} "
                                     "(difference {diff})"},
    "rq_premium_act_na": {"ru": "сравнить не с чем: нет премии в запросе или премия акта не определена",
                          "uz": "solishtirib boʻlmaydi: soʻrovda mukofot yoʻq yoki dalolatnoma mukofoti aniqlanmagan",
                          "en": "nothing to compare: no premium in the request or the report premium is not "
                                "determined"},
    "rq_sum_value": {"ru": "сумма к стоимости {ratio} — вывод в разделе 3",
                     "uz": "summaning qiymatga nisbati {ratio} — xulosa 3-boʻlimda",
                     "en": "sum insured to value {ratio} — see the conclusion in section 3"},
    "rq_franchise_none": {"ru": "франшиза в запросе не указана", "uz": "soʻrovda franshiza koʻrsatilmagan",
                          "en": "the request states no deductible"},
    "rq_franchise_ok_none": {"ru": "в запросе франшиза не применяется, акт франшизу не требует",
                             "uz": "soʻrovda franshiza qoʻllanilmaydi, dalolatnoma franshizani talab qilmaydi",
                             "en": "no deductible in the request, and the report does not require one"},
    "rq_franchise_act_proposed": {"ru": "в запросе франшиза не применяется, а акт предлагает франшизу {pct}",
                                  "uz": "soʻrovda franshiza qoʻllanilmaydi, dalolatnoma esa {pct} franshizani taklif "
                                        "qiladi",
                                  "en": "no deductible in the request, but the report proposes a {pct} deductible"},
    "rq_franchise_act_applied": {"ru": "в запросе франшиза не применяется, а в акте применена франшиза {pct}",
                                 "uz": "soʻrovda franshiza qoʻllanilmaydi, dalolatnomada esa {pct} franshiza "
                                       "qoʻllanilgan",
                                 "en": "no deductible in the request, but a {pct} deductible is applied in the report"},
    "rq_franchise_req_only": {"ru": "в запросе франшиза «{text}», а акт оснований для франшизы не видит",
                              "uz": "soʻrovda franshiza «{text}», dalolatnoma esa franshiza uchun asos koʻrmaydi",
                              "en": "the request has a deductible «{text}», but the report sees no grounds for one"},
    "rq_franchise_ok_same": {"ru": "франшиза {pct} — одна и та же в запросе и в акте",
                             "uz": "franshiza {pct} — soʻrovda ham, dalolatnomada ham bir xil",
                             "en": "deductible {pct} — the same in the request and in the report"},
    "rq_franchise_size": {"ru": "франшиза в запросе {pct}, в акте {act}",
                          "uz": "soʻrovdagi franshiza {pct}, dalolatnomada {act}",
                          "en": "deductible {pct} in the request, {act} in the report"},
    "rq_term_none": {"ru": "срок в запросе не указан", "uz": "soʻrovda muddat koʻrsatilmagan",
                     "en": "the request states no term"},
    "rq_term_ok": {"ru": "в запросе {req} дн., в акте {calc} дн.", "uz": "soʻrovda {req} kun, dalolatnomada {calc} kun",
                   "en": "{req} days in the request, {calc} days in the report"},
    "rq_term_from_request": {"ru": "{req} дн. — срок акта взят из запроса, премия акта посчитана на весь срок",
                             "uz": "{req} kun — dalolatnoma muddati soʻrovdan olindi, mukofot butun muddatga "
                                   "hisoblandi",
                             "en": "{req} days — the report term is taken from the request; the premium covers the "
                                   "whole term"},
    "rq_term_differs": {"ru": "в запросе {req} дн., в акте {calc} дн. — премии посчитаны на разный срок",
                        "uz": "soʻrovda {req} kun, dalolatnomada {calc} kun — mukofotlar turli muddatga hisoblangan",
                        "en": "{req} days in the request, {calc} days in the report — the premiums cover different "
                              "terms"},
    "rq_how_premium": {"ru": "Премия по тарифу запроса = {sum} × {rate} × {days} / 365 = {premium}",
                       "uz": "Soʻrov tarifi boʻyicha mukofot = {sum} × {rate} × {days} / 365 = {premium}",
                       "en": "Premium at the request rate = {sum} × {rate} × {days} / 365 = {premium}"},
    "rq_how_days": {"ru": "Срок с {date_from} по {date_to} — {days} дн., оба крайних дня включены",
                    "uz": "Muddat {date_from} dan {date_to} gacha — {days} kun, ikkala chekka kun ham kiritilgan",
                    "en": "Term from {date_from} to {date_to} — {days} days, both end days included"},
    "rq_how_days_excl": {"ru": "Срок с {date_from} по {date_to} — {days} дн., последний день не включён",
                         "uz": "Muddat {date_from} dan {date_to} gacha — {days} kun, oxirgi kun kiritilmagan",
                         "en": "Term from {date_from} to {date_to} — {days} days, the last day excluded"},
    "rq_how_tol": {"ru": "Допуск по премии — {tol}: филиал округляет премию", "uz": "Mukofot boʻyicha ruxsat — {tol}: "
                   "filial mukofotni yaxlitlaydi", "en": "Premium tolerance — {tol}: the branch rounds the premium"},
    "rq_how_annual": {"ru": "Тарифы — годовые: многолетний договор считается на весь срок по дням",
                      "uz": "Tariflar — yillik: koʻp yillik shartnoma butun muddatga kunlar boʻyicha hisoblanadi",
                      "en": "Rates are annual: a multi-year contract is priced for the whole term by days"},
    # ---------- что проверить андеррайтеру (раздел 5) ----------
    "c_rq_tariff_min": {"ru": "Тариф в запросе филиала {req} ниже минимального {calc} — решение андеррайтера "
                              "(отступление от тарифной политики)",
                        "uz": "Filial soʻrovidagi tarif {req} eng kam stavka {calc} dan past — anderrayter qarori "
                              "(tarif siyosatidan chetga chiqish)",
                        "en": "The branch request rate {req} is below the minimum {calc} — underwriter decision "
                              "(deviation from the tariff policy)"},
    "c_rq_tariff_act": {"ru": "Тариф в запросе филиала {req} ниже ставки акта {calc} — обосновать или применить "
                              "ставку акта",
                        "uz": "Filial soʻrovidagi tarif {req} dalolatnoma stavkasi {calc} dan past — asoslash yoki "
                              "dalolatnoma stavkasini qoʻllash",
                        "en": "The branch request rate {req} is below the report rate {calc} — justify it or apply "
                              "the report rate"},
    "c_rq_premium_request": {"ru": "Премия в запросе филиала {req} не сходится с расчётом по тарифу запроса {calc}: "
                                   "расхождение {diff}",
                             "uz": "Filial soʻrovidagi mukofot {req} soʻrov tarifi boʻyicha hisob {calc} ga mos emas: "
                                   "farq {diff}",
                             "en": "The branch request premium {req} does not match the calculation at the request "
                                   "rate {calc}: difference {diff}"},
    "c_rq_franchise": {"ru": "Франшиза в запросе филиала расходится с выводом акта — уточнить условие договора",
                       "uz": "Filial soʻrovidagi franshiza dalolatnoma xulosasidan farq qiladi — shartnoma shartini "
                             "aniqlashtirish",
                       "en": "The deductible in the branch request differs from the report — clarify the contract "
                             "condition"},
    "c_rq_term": {"ru": "Срок в запросе филиала {req} дн. не совпадает со сроком акта {calc} дн.",
                  "uz": "Filial soʻrovidagi muddat {req} kun dalolatnoma muddati {calc} kun bilan mos emas",
                  "en": "The branch request term of {req} days does not match the report term of {calc} days"},
}

TX_CONTRACT = {
    # ---------- заголовки ----------
    "ct_title": {"ru": "Сверка с договором", "uz": "Shartnoma bilan solishtirish", "en": "Check against the contract"},
    "ct_how_title": {"ru": "Как сверен договор", "uz": "Shartnoma qanday solishtirildi",
                     "en": "How the contract was checked"},
    "ct_how_tol": {"ru": "Допуск по премии — {tol}: премию в договоре округляют",
                   "uz": "Mukofot boʻyicha ruxsat — {tol}: shartnomada mukofot yaxlitlanadi",
                   "en": "Premium tolerance — {tol}: the contract premium is rounded"},
    "ct_row": {"ru": "Договор страхования", "uz": "Sugʻurta shartnomasi", "en": "Insurance contract"},
    "ct_row_value": {"ru": "№ {no} от {date}", "uz": "{date} dagi {no}-son", "en": "No. {no} of {date}"},
    "ct_row_no": {"ru": "№ {no}", "uz": "{no}-son", "en": "No. {no}"},
    "ct_row_date": {"ru": "от {date}", "uz": "{date} dagi", "en": "of {date}"},
    # ---------- строки сверки ----------
    "ct_l_tariff_min": {"ru": "Тариф и минимальная ставка", "uz": "Tarif va eng kam stavka", "en": "Rate vs minimum rate"},
    "ct_l_tariff_act": {"ru": "Тариф и ставка акта", "uz": "Tarif va dalolatnoma stavkasi", "en": "Rate vs report rate"},
    "ct_l_premium_request": {"ru": "Премия по тарифу договора", "uz": "Shartnoma tarifi boʻyicha mukofot",
                             "en": "Premium at the contract rate"},
    "ct_l_premium_act": {"ru": "Премия акта (справочно)", "uz": "Dalolatnoma mukofoti (maʼlumot uchun)",
                         "en": "Report premium (for reference)"},
    "ct_l_sum_value": {"ru": "Сумма и стоимость (справочно)", "uz": "Summa va qiymat (maʼlumot uchun)",
                       "en": "Sum insured vs value (for reference)"},
    "ct_l_franchise": {"ru": "Франшиза", "uz": "Franshiza", "en": "Deductible"},
    "ct_l_term": {"ru": "Срок", "uz": "Muddat", "en": "Term"},
    "ct_l_payments": {"ru": "График платежей и премия", "uz": "Toʻlov jadvali va mukofot",
                      "en": "Payment schedule vs premium"},
    "ct_l_items_sum": {"ru": "Суммы по объектам и общая сумма", "uz": "Obyektlar boʻyicha summalar va umumiy summa",
                       "en": "Per-item sums vs total sum insured"},
    "ct_l_essentials": {"ru": "Существенные условия (ГК РУз, ст. 929)",
                        "uz": "Muhim shartlar (OʻzR FK, 929-modda)",
                        "en": "Essential terms (Civil Code, Art. 929)"},
    "rq_v_no_essential": {"ru": "условие не найдено", "uz": "shart topilmadi", "en": "term not found"},
    "ct_summary_no_essential": {
        "ru": "В тексте договора не найдено существенное условие (ГК РУз, ст. 929). Проверьте договор; если условия "
              "действительно нет — договор нужно дополнить. Решение за андеррайтером.",
        "uz": "Shartnoma matnida muhim shart topilmadi (OʻzR FK, 929-modda). Shartnomani tekshiring; agar shart "
              "haqiqatan yoʻq boʻlsa — shartnomani toʻldirish kerak. Qaror anderrayterda.",
        "en": "An essential term was not found in the contract text (Civil Code, Art. 929). Check the contract; if "
              "the term is really missing, the contract must be supplemented. The underwriter decides."},
    "ct_payments_ok": {"ru": "график: {n} плат., всего {calc} — равно премии {req}",
                       "uz": "jadval: {n} ta toʻlov, jami {calc} — mukofot {req} ga teng",
                       "en": "schedule: {n} payment(s), {calc} in total — equals the premium {req}"},
    "ct_payments_differs": {"ru": "график: {n} плат., всего {calc}, а премия {req} — разница {diff}",
                            "uz": "jadval: {n} ta toʻlov, jami {calc}, mukofot esa {req} — farq {diff}",
                            "en": "schedule: {n} payment(s), {calc} in total, but the premium is {req} — "
                                  "difference {diff}"},
    "ct_payments_none": {"ru": "график платежей в договоре не указан", "uz": "shartnomada toʻlov jadvali koʻrsatilmagan",
                         "en": "the contract states no payment schedule"},
    "ct_payments_single": {"ru": "премия уплачивается единовременно; сумма платежа отдельно не указана",
                           "uz": "mukofot bir martalik toʻlanadi; toʻlov summasi alohida koʻrsatilmagan",
                           "en": "the premium is paid in one payment; the payment amount is not stated separately"},
    "ct_payments_na": {"ru": "график: {n} плат., всего {calc}; премии в договоре нет — сравнить не с чем",
                       "uz": "jadval: {n} ta toʻlov, jami {calc}; shartnomada mukofot yoʻq — solishtirib boʻlmaydi",
                       "en": "schedule: {n} payment(s), {calc} in total; no premium in the contract — nothing to "
                             "compare"},
    "ct_items_sum_ok": {"ru": "по {n} объектам всего {calc} — равно общей страховой сумме {req}",
                        "uz": "{n} ta obyekt boʻyicha jami {calc} — umumiy sugʻurta summasi {req} ga teng",
                        "en": "{n} item(s), {calc} in total — equals the total sum insured {req}"},
    "ct_items_sum_differs": {"ru": "по {n} объектам всего {calc}, а общая страховая сумма {req} — разница {diff}",
                             "uz": "{n} ta obyekt boʻyicha jami {calc}, umumiy sugʻurta summasi esa {req} — farq {diff}",
                             "en": "{n} item(s), {calc} in total, but the total sum insured is {req} — difference {diff}"},
    "ct_items_sum_na": {"ru": "по {n} объектам всего {calc}; общей страховой суммы в договоре нет",
                        "uz": "{n} ta obyekt boʻyicha jami {calc}; shartnomada umumiy sugʻurta summasi yoʻq",
                        "en": "{n} item(s), {calc} in total; the contract states no total sum insured"},
    "ct_essentials_ok": {"ru": "есть все: {what}", "uz": "hammasi bor: {what}", "en": "all present: {what}"},
    "ct_essentials_no_essential": _ESS_NOT_FOUND,
    "ct_how_essentials": {
        "ru": "ГК РУз, ст. 929: «При заключении договора имущественного страхования между страхователем и страховщиком "
              "должно быть достигнуто соглашение: об определенном имуществе либо ином имущественном интересе, являющемся "
              "объектом страхования; о характере события, на случай наступления которого производится страхование "
              "(страхового случая); о размере страховой суммы; о порядке определения размера страхового возмещения, "
              "если договором предусмотрена возможность его выплаты в размере меньшем, чем страховая сумма; о размере "
              "страховой премии и сроке (сроках) ее уплаты; о сроке действия договора.» Условие о порядке определения "
              "возмещения и срок уплаты премии сотрудник проверяет по тексту договора.",
        "uz": "OʻzR FK, 929-modda: «Mulkiy sugʻurta shartnomasi tuzishda sugʻurta qildiruvchi bilan sugʻurtalovchi "
              "oʻrtasida quyidagilar toʻgʻrisida kelishuvga erishilishi lozim: sugʻurta obyekti boʻlgan muayyan mol-mulk "
              "yoxud boshqa mulkiy manfaat toʻgʻrisida; yuz berishi ehtimol tutilib sugʻurta amalga oshirilayotgan voqea "
              "(sugʻurta hodisasi)ning xususiyati toʻgʻrisida; sugʻurta summasi miqdori toʻgʻrisida; sugʻurta tovoni "
              "miqdorini aniqlash tartibi toʻgʻrisida, agar shartnomada uni sugʻurta summasidan oz miqdorda toʻlash "
              "mumkinligi nazarda tutilgan boʻlsa; sugʻurta mukofotining miqdori va uni toʻlash muddati (muddatlari) "
              "toʻgʻrisida; shartnomaning amal qilish muddati toʻgʻrisida.» Tovonni aniqlash tartibi va mukofotni toʻlash "
              "muddatini xodim shartnoma matni boʻyicha tekshiradi.",
        "en": "Civil Code of Uzbekistan, Art. 929 (translation): when a property insurance contract is concluded, the "
              "policyholder and the insurer must agree on the specific property or other property interest that is the "
              "object of insurance; the nature of the event insured against (the insured event); the sum insured; the "
              "procedure for determining the indemnity if the contract allows paying less than the sum insured; the "
              "premium and its payment term(s); the term of the contract. The indemnity procedure and the premium "
              "payment term are checked by staff against the contract text."},
    "ct_risks_line": {"ru": "Застрахованные риски по договору: {what}", "uz": "Shartnoma boʻyicha xavflar: {what}",
                      "en": "Risks insured under the contract: {what}"},
    "ct_excl_line": {"ru": "Исключения по договору: {what}", "uz": "Shartnoma boʻyicha istisnolar: {what}",
                     "en": "Exclusions under the contract: {what}"},
    "c_ct_payments": {"ru": "График платежей по договору ({calc}) не сходится с премией {req}: разница {diff}",
                      "uz": "Shartnoma boʻyicha toʻlov jadvali ({calc}) mukofot {req} ga mos emas: farq {diff}",
                      "en": "The contract payment schedule ({calc}) does not match the premium {req}: difference {diff}"},
    "c_ct_items_sum": {"ru": "Суммы по объектам в договоре ({calc}) не сходятся с общей страховой суммой {req}",
                       "uz": "Shartnomadagi obyektlar boʻyicha summalar ({calc}) umumiy sugʻurta summasi {req} ga mos emas",
                       "en": "The per-item sums in the contract ({calc}) do not add up to the total sum insured {req}"},
    "c_ct_essentials": _ESS_NOT_FOUND_CAP,
    # ---------- блок contract в /act/photos ----------
    "ct_term_unread": {"ru": "Срок страхования в договоре не разобран — введите даты вручную",
                       "uz": "Shartnomadagi sugʻurta muddati tahlil qilinmadi — sanalarni qoʻlda kiriting",
                       "en": "The insurance term in the contract could not be read — enter the dates manually"},
    "ct_no_tariff": {"ru": "Тариф в договоре не найден — премию по тарифу договора пересчитать нельзя",
                     "uz": "Shartnomada tarif topilmadi — mukofotni shartnoma tarifi boʻyicha qayta hisoblab boʻlmaydi",
                     "en": "No rate was found in the contract — the premium cannot be recalculated at the contract rate"},
    "ct_currency": {"ru": "Суммы в договоре указаны в {cur} — в шаг 2 не подставлены, пересчитайте в сумы",
                    "uz": "Shartnomadagi summalar {cur} da — 2-qadamga qoʻyilmadi, soʻmga qayta hisoblang",
                    "en": "The contract amounts are in {cur} — not filled into step 2; convert them to UZS"},
    "ct_missing": {"ru": "В договоре не найдено: {what}", "uz": "Shartnomada topilmadi: {what}",
                   "en": "Not found in the contract: {what}"},
    "ct_missing_essential": _ESS_NOT_FOUND_CAP,
    "ct_ai_filled": {"ru": "Часть полей прочитана моделью из текста договора ({what}) — проверьте",
                     "uz": "Baʼzi maydonlarni model shartnoma matnidan oʻqidi ({what}) — tekshiring",
                     "en": "Some fields were read by the model from the contract text ({what}) — please check"},
    "ct_ai_note": {"ru": "прочитано моделью из текста, проверьте", "uz": "model matndan oʻqidi, tekshiring",
                   "en": "read by the model from the text, please check"},
    "ct_ai_failed": {"ru": "Дочитать договор моделью не удалось: {reason}",
                     "uz": "Shartnomani model bilan oʻqib boʻlmadi: {reason}",
                     "en": "The model could not complete reading the contract: {reason}"},
    "ct_truncated": {"ru": "Договор длиннее предела разбора — прочитана только часть текста; проверьте условия по "
                           "оригиналу",
                     "uz": "Shartnoma tahlil chegarasidan uzun — matnning faqat bir qismi oʻqildi; shartlarni asl nusxa "
                           "boʻyicha tekshiring",
                     "en": "The contract exceeds the parsing limit — only part of the text was read; check the terms "
                           "against the original"},
    # ---------- бланк договора (поля не заполнены) ----------
    "ct_template": {"ru": "Это бланк договора: поля {what} не заполнены. Существенные условия проверяются по "
                          "заполненному договору",
                    "uz": "Bu shartnoma blankasi: {what} maydonlari toʻldirilmagan. Muhim shartlar toʻldirilgan "
                          "shartnoma boʻyicha tekshiriladi",
                    "en": "This is a blank contract form: {what} are not filled in. Essential terms are checked "
                          "against the completed contract"},
    "ct_template_form": {"ru": "Форма договора из приложения к приказу компании",
                         "uz": "Kompaniya buyrugʻi ilovasidagi shartnoma shakli",
                         "en": "Contract form from an annex to a company order"},
    "ct_blank": {"ru": "не заполнено", "uz": "toʻldirilmagan", "en": "not filled in"},
    "doc_scan_pages": {"ru": "В длинном PDF нет текстового слоя — модели он не отправлен: сканы читаются не длиннее "
                             "предела страниц, загрузите нужные страницы отдельно",
                       "uz": "Uzun PDFda matn qatlami yoʻq — modelga yuborilmadi: skanlar betlar chegarasigacha "
                             "oʻqiladi, kerakli betlarni alohida yuklang",
                       "en": "The long PDF has no text layer — it was not sent to the model: scans are read up to the "
                             "page limit; upload the pages you need separately"},
    # ---------- запрос филиала против договора ----------
    "x_title": {"ru": "Запрос филиала и договор: расхождения", "uz": "Filial soʻrovi va shartnoma: farqlar",
                "en": "Branch request vs contract: differences"},
    "x_v_same": {"ru": "совпадает", "uz": "mos", "en": "same"},
    "x_v_differs": {"ru": "расходится", "uz": "farq bor", "en": "differs"},
    "x_v_missing": {"ru": "нет в одном из документов", "uz": "hujjatlardan birida yoʻq",
                    "en": "missing in one of the documents"},
    "x_line": {"ru": "{label} — {verdict}: в запросе {req}, в договоре {ct}",
               "uz": "{label} — {verdict}: soʻrovda {req}, shartnomada {ct}",
               "en": "{label} — {verdict}: request {req}, contract {ct}"},
    "x_summary_ok": {"ru": "Запрос филиала и договор совпадают по сверяемым условиям.",
                     "uz": "Filial soʻrovi va shartnoma solishtirilgan shartlar boʻyicha mos.",
                     "en": "The branch request and the contract match on the compared terms."},
    "x_summary_differs": {"ru": "Запрос филиала и договор расходятся ({n}) — уточните, какой документ верный.",
                          "uz": "Filial soʻrovi va shartnoma farq qiladi ({n}) — qaysi hujjat toʻgʻri ekanini aniqlang.",
                          "en": "The branch request and the contract differ ({n}) — clarify which document is correct."},
    "x_summary_missing": {"ru": "Сверить запрос филиала и договор не по чему: нужных условий нет в обоих документах.",
                          "uz": "Filial soʻrovi va shartnomani solishtirib boʻlmaydi: shartlar ikkala hujjatda yoʻq.",
                          "en": "The branch request and the contract cannot be compared: the terms are not in both."},
    "c_x": {"ru": "Запрос филиала и договор расходятся: {what}", "uz": "Filial soʻrovi va shartnoma farq qiladi: {what}",
            "en": "The branch request and the contract differ: {what}"},
    "x_term": {"ru": "{days} дн. ({date_from}–{date_to})", "uz": "{days} kun ({date_from}–{date_to})",
               "en": "{days} days ({date_from}–{date_to})"},
    "x_fr_none": {"ru": "не применяется", "uz": "qoʻllanilmaydi", "en": "not applied"},
}

TX_TERMS_SOURCE = {
    # ---------- источник условий (решает сервер по своей загрузке) ----------
    "tr_src_doc": {"ru": "из документа", "uz": "hujjatdan", "en": "from the document"},
    "tr_src_edited": {"ru": "из документа с правками сотрудника ({n})", "uz": "hujjatdan, xodim tuzatishlari bilan ({n})",
                      "en": "from the document with staff edits ({n})"},
    "tr_src_input": {"ru": "введено сотрудником", "uz": "xodim kiritgan", "en": "entered by staff"},
    "tr_doc_missing": {"ru": "документ недоступен (прошло больше 24 часов или сменилась сессия)",
                       "uz": "hujjat mavjud emas (24 soatdan koʻp vaqt oʻtdi yoki sessiya almashdi)",
                       "en": "the document is unavailable (more than 24 hours have passed or the session changed)"},
    "tr_src_line": {"ru": "Источник условий: {v}", "uz": "Shartlar manbai: {v}", "en": "Source of the terms: {v}"},
    "tr_edit": {"ru": "{label}: было {was} → стало {now}", "uz": "{label}: edi {was} → boʻldi {now}",
                "en": "{label}: was {was} → now {now}"},
    "tr_no_edits": {"ru": "правок нет", "uz": "tuzatishlar yoʻq", "en": "no edits"},
    "tr_none": {"ru": "не указано", "uz": "koʻrsatilmagan", "en": "not stated"},
    "tr_payments": {"ru": "{n} плат., всего {total}", "uz": "{n} ta toʻlov, jami {total}",
                    "en": "{n} payment(s), {total} in total"},
    "tr_items": {"ru": "{n} объект(ов), всего {total}", "uz": "{n} ta obyekt, jami {total}",
                 "en": "{n} item(s), {total} in total"},
    "x_days": {"ru": "{n} дн.", "uz": "{n} kun", "en": "{n} days"},
    "rq_edits_line": {"ru": "Правки сотрудника в условиях запроса: {what}",
                      "uz": "Soʻrov shartlaridagi xodim tuzatishlari: {what}",
                      "en": "Staff edits to the request terms: {what}"},
    "ct_edits_line": {"ru": "Правки сотрудника в условиях договора: {what}",
                      "uz": "Shartnoma shartlaridagi xodim tuzatishlari: {what}",
                      "en": "Staff edits to the contract terms: {what}"},
    "c_rq_edits": {"ru": "Проверить правки сотрудника в условиях запроса ({n})",
                   "uz": "Soʻrov shartlaridagi xodim tuzatishlarini tekshirish ({n})",
                   "en": "Check the staff edits to the request terms ({n})"},
    "c_ct_edits": {"ru": "Проверить правки сотрудника в условиях договора ({n})",
                   "uz": "Shartnoma shartlaridagi xodim tuzatishlarini tekshirish ({n})",
                   "en": "Check the staff edits to the contract terms ({n})"},
    "by_model_mark": {"ru": "прочитано моделью", "uz": "model oʻqigan", "en": "read by the model"},
    "br_negative": {"ru": "В запросе отрицательное число ({what}) — значение не взято, проверьте документ",
                    "uz": "Soʻrovda manfiy son ({what}) — qiymat olinmadi, hujjatni tekshiring",
                    "en": "The request has a negative number ({what}) — the value was not taken; check the document"},
    "doc_application": {"ru": "Это заявление, а не договор: данные подставлены для проверки, сверка договора и "
                              "существенных условий не выполняется",
                        "uz": "Bu ariza, shartnoma emas: maʼlumotlar tekshirish uchun qoʻyildi, shartnoma va muhim "
                              "shartlar solishtirilmaydi",
                        "en": "This is an application, not a contract: the data is filled in for checking; the "
                              "contract and its essential terms are not checked"},
    # ---------- запрос ↔ договор: объект ----------
    "x_object_na": {"ru": "{label} — сравнить нечем: в запросе {req}, в договоре {ct}",
                    "uz": "{label} — solishtirib boʻlmaydi: soʻrovda {req}, shartnomada {ct}",
                    "en": "{label} — nothing to compare: request {req}, contract {ct}"},
    # ---------- существенные условия: поиск по словарю, не утверждение, что условия нет ----------
}
