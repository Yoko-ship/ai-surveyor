# -*- coding: utf-8 -*-
"""
Сборка docs/Юрист — FAQ.json: вопросы сотрудников и ответы на трёх языках
с дословными цитатами из текстов в library/01_Законодательство.

Каждая цитата вырезается из файла библиотеки по якорю (регулярное выражение),
обрезается до 300 символов и проверяется на дословное совпадение с файлом
(пробелы приводятся к одному, служебные подписи lex.uz удаляются).

Запуск: PYTHONIOENCODING=utf-8 python tools/faq_build.py
"""
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library" / "01_Законодательство"
OUT = ROOT / "docs" / "Юрист — FAQ.json"

# Кнопки страницы lex.uz. Это не текст акта, а подписи под каждым абзацем; они стоят между
# пунктами перечня, поэтому их удаление НЕ делает цитату недословной — перечень в акте слитный.
UI = r"(Предложения по документу|Прослушать аудио|Получить ссылку из элемента документа|" \
     r"Hujjatga taklif yuborish|Audioni tinglash|Hujjat elementidan havola olish|" \
     r"Hujjat bo\W?yicha takliflar)"

# А это уже чужой текст: комментарий редакции LexUz, отсылки к прежней редакции, рубрикаторы.
# Через них цитата не переносится — иначе комментарий выдавался бы за норму закона
# (так в цитате ст. 5 Закона о ПД оказалось «См. Положение … № 71»).
CUT = r"(Комментарий LexUz|LexUZ sharhi|См\. предыдущую редакцию|См\. судебную практику|" \
      r"Oldingi tahrirga qarang|\[ ОКОЗ:[^\]]*\]|\[ СПиТ:[^\]]*\]|\[ OKOZ:[^\]]*\]|" \
      r"\[ SPiT:[^\]]*\]|=== стр\. \d+ ===)"

# границы, через которые цитата не переносится: заголовки статей, глав и приложений.
# Иначе цитата «доедала» соседнюю норму (ОСГОР заканчивался заголовком «Статья 9.»).
BREAK = r"(?=Статья \d+|Глава \d+|\d+-modda\.|\d+-bob\.|Article \d+|ГЛАВА |ПРИЛОЖЕНИЕ)"

SEP = "\x00"          # метка границы: цитата обязана остановиться на ней

_cache = {}
_raw_cache = {}


def raw(rel: str) -> str:
    """Текст файла без кнопок lex.uz, пробелы схлопнуты. По нему проверяется дословность цитаты."""
    if rel not in _raw_cache:
        t = (LIB / rel).read_text(encoding="utf-8", errors="ignore")
        t = re.sub(UI, " ", t)
        _raw_cache[rel] = re.sub(r"\s+", " ", t)
    return _raw_cache[rel]


def text(rel: str) -> str:
    """Тот же текст, но чужие вставки и заголовки статей заменены меткой SEP.

    Раньше вставки просто удалялись, а остатки склеивались — и вырезанная цитата в самом акте
    не встречалась (к тексту закона прилипал «Комментарий LexUz», к пункту — чужой заголовок).
    Теперь на их месте стоит метка, и цитата обрывается перед ней.
    """
    if rel not in _cache:
        t = raw(rel)
        t = re.sub(CUT, SEP, t)
        t = re.sub(BREAK, SEP, t)
        _cache[rel] = t
    return _cache[rel]


APO = "'‘’ʻʼʽ`´"


def any_apo(anchor: str) -> str:
    """В якоре любой апостроф подходит под любой: в выгрузках lex.uz пишут то ʻ, то ‘, то '."""
    return re.sub("[" + re.escape(APO) + "]", "[" + re.escape(APO) + "]", anchor)


def quote(rel: str, anchor: str, limit: int = 300) -> str:
    """Дословный кусок текста акта, начиная с якоря, не длиннее limit символов."""
    t = text(rel)
    m = re.search(any_apo(anchor), t)
    if not m:
        raise SystemExit(f"НЕ НАЙДЕНО: {anchor} в {rel}")
    s = t[m.start(): m.start() + limit + 120]
    if s.startswith(SEP):               # якорь стоит сразу за границей — саму метку снимаем
        s = s[1:]
    stop = s.find(SEP)
    if stop >= 0:
        s = s[:stop]
    # обрезаем по концу предложения, иначе по последнему пробелу
    cut = max(s.rfind(". ", 0, limit + 1), s.rfind("; ", 0, limit + 1))
    if len(s) > limit:
        s = s[: cut + 1] if cut > 80 else s[:limit].rsplit(" ", 1)[0]
    return s.strip()


def check(rel: str, q: str) -> bool:
    """Цитата обязана встречаться в файле библиотеки дословно (с точностью до пробелов)."""
    return bool(q) and q in raw(rel)


# ---------------------------------------------------------------- источники
F = {
    "gk_ru": "01_Законы/ГК РУз глава 52 Страхование (ст. 914-961).txt",
    "gk_uz": "01_Законы/ГК РУз глава 52 Страхование (ст. 914-961) (uz).txt",
    "z730_ru": "01_Законы/ЗРУ-730 от 23.11.2021 О страховой деятельности.txt",
    "z730_uz": "01_Законы/ЗРУ-730 от 23.11.2021 О страховой деятельности (uz).txt",
    "z730_en": "01_Законы/ЗРУ-730 от 23.11.2021 О страховой деятельности (en).txt",
    "z210_ru": "01_Законы/ЗРУ-210 от 16.04.2009 Об обязательном страховании гражданской ответственности работодателя.txt",
    "z210_uz": "01_Законы/ЗРУ-210 от 16.04.2009 Об обязательном страховании гражданской ответственности работодателя (uz).txt",
    "z155_ru": "01_Законы/ЗРУ-155 от 21.04.2008 Об обязательном страховании гражданской ответственности владельцев транспортных средств.txt",
    "z386_ru": "01_Законы/ЗРУ-386 от 26.05.2015 Об обязательном страховании гражданской ответственности перевозчика.txt",
    "z547_ru": "01_Законы/ЗРУ-547 от 02.07.2019 О персональных данных.txt",
    "z547_en": "01_Законы/ЗРУ-547 от 02.07.2019 О персональных данных (en).txt",
    "p177_ru": "02_Акты_регуляторов/ПКМ № 177 от 24.06.2009 — меры по реализации Закона об ОСГО работодателя (Правила и тарифы).txt",
    "p177_uz": "02_Акты_регуляторов/ПКМ № 177 от 24.06.2009 — меры по реализации Закона об ОСГО работодателя (Правила и тарифы) (uz).txt",
    "p141_ru": "02_Акты_регуляторов/ПКМ № 141 от 24.06.2008 — меры по реализации Закона об ОСГО владельцев ТС (Правила и тарифы).txt",
    "p1806_ru": "02_Акты_регуляторов/Положение 1806 — платёжеспособность страховщиков (рус).txt",
    "p1882_ru": "02_Акты_регуляторов/Положение 1882 — страховые резервы (рус).txt",
    "p3845_uz": "02_Акты_регуляторов/Положение 3845 — страховые агенты (uz).txt",
}
U = {
    # Адреса lex.uz: /ru/docs/<номер> — русский текст, /uz/docs/-<номер> — узбекская ЛАТИНИЦА,
    # /uz/docs/<номер> — та же норма КИРИЛЛИЦЕЙ. Дефис не опечатка: проверено 22.09.2026,
    # все девять узбекских адресов ниже открываются (HTTP 200) и отдают латинский текст,
    # который и лежит в библиотеке. Убирать дефис нельзя — цитата перестанет находиться на странице.
    "gk": {"ru": "https://lex.uz/ru/docs/180550", "uz": "https://lex.uz/uz/docs/-180552", "en": None},
    "z730": {"ru": "https://lex.uz/ru/docs/5739120", "uz": "https://lex.uz/uz/docs/-5739117",
             "en": "https://lex.uz/ru/docs/6124730"},
    "z210": {"ru": "https://lex.uz/ru/docs/1471201", "uz": "https://lex.uz/uz/docs/-1471197", "en": None},
    "z155": {"ru": "https://lex.uz/ru/docs/1342476", "uz": "https://lex.uz/uz/docs/-1342474", "en": None},
    "z386": {"ru": "https://lex.uz/ru/docs/2652781", "uz": "https://lex.uz/uz/docs/-2652779", "en": None},
    "z547": {"ru": "https://lex.uz/ru/docs/4396428", "uz": "https://lex.uz/uz/docs/-4396419",
             "en": "https://lex.uz/ru/docs/4831939"},
    "p177": {"ru": "https://lex.uz/ru/docs/1493387", "uz": "https://lex.uz/uz/docs/-1493389", "en": None},
    "p141": {"ru": "https://lex.uz/ru/docs/1366341", "uz": "https://lex.uz/uz/docs/-1366339", "en": None},
    "p1806": {"ru": "https://lex.uz/ru/docs/1354128", "uz": "https://lex.uz/uz/docs/-1354126", "en": None},
    "p1882": {"ru": "https://lex.uz/ru/docs/1416860", "uz": "https://lex.uz/uz/docs/-1416862", "en": None},
    # Положение 3845: русского текста приложения на lex.uz нет (страница /ru/docs/8244248 — только
    # карточка акта), поэтому и для русского ответа даём узбекский официальный текст
    "p3845": {"ru": None, "uz": "https://lex.uz/uz/docs/-8241971", "en": None},
}
# официальность текста: официальным в РУз является узбекский текст
OFF = {"ru": False, "uz": True, "en": False}


def cite(act, article, doc, ru=None, uz=None, en=None, urls=None, official=None):
    """Собрать цитату: ru/uz/en — (файл, якорь) или None."""
    def one(pair):
        if not pair:
            return None
        rel, anchor = pair
        q = quote(rel, anchor)
        if not check(rel, q):
            raise SystemExit(f"Цитата не подтверждена файлом: {rel} / {anchor}")
        return q
    return {"act": act, "article": article, "doc": doc,
            "quote": {"ru": one(ru), "uz": one(uz), "en": one(en)},
            "url": urls or {"ru": None, "uz": None, "en": None},
            "official": official or OFF}


# ---------------------------------------------------------------- цитаты
CITES = {}


def C(key):
    return CITES[key]


def build_cites():
    g = F["gk_ru"]; gu = F["gk_uz"]
    CITES["gk936"] = cite("ГК РУз", "ст. 936", "Гражданский кодекс, часть вторая",
        ru=(g, r"Если в договоре страхования имущества или предпринимательского риска страховая сумма установлена ниже"),
        uz=(gu, r"Agar mol-mulkni yoki tadbirkorlik xavfini sugʻurtalash shartnomasida sugʻurta summasi sugʻurta qiymatidan kam"),
        urls=U["gk"])
    CITES["gk938"] = cite("ГК РУз", "ст. 938", "Гражданский кодекс, часть вторая",
        ru=(g, r"Если страховая сумма, указанная в договоре страхования имущества или предпринимательского риска, превышает"),
        uz=(gu, r"Agar sugʻurta shartnomasida koʻrsatilgan mol-mulk yoki tadbirkorlik xavfining sugʻurta summasi sugʻurta qiymatidan ortiq"),
        urls=U["gk"])
    CITES["gk939"] = cite("ГК РУз", "ст. 939", "Гражданский кодекс, часть вторая",
        ru=(g, r"При двойном страховании имущества или предпринимательского риска каждый страховщик"), urls=U["gk"])
    CITES["gk935"] = cite("ГК РУз", "ст. 935", "Гражданский кодекс, часть вторая",
        ru=(g, r"Страховой стоимостью \(страховой оценкой\) признается стоимость имущества"),
        uz=(gu, r"Sugʻurta shartnomasini tuzish paytida taraflarning kelishuviga koʻra aniqlanadigan"), urls=U["gk"])
    CITES["gk935_2"] = cite("ГК РУз", "ст. 935 ч. 2", "Гражданский кодекс, часть вторая",
        ru=(g, r"Страховая стоимость имущества, указанная в договоре страхования, не может быть впоследствии оспорена"),
        uz=(gu, r"Mol-mulkning shartnomada koʻrsatilgan sugʻurta qiymati ustida keyinchalik nizolashish mumkin emas"), urls=U["gk"])
    CITES["gk934"] = cite("ГК РУз", "ст. 934", "Гражданский кодекс, часть вторая",
        ru=(g, r"При страховании имущества или предпринимательского риска, если договором страхования не предусмотрено иное"),
        urls=U["gk"])
    CITES["gk932"] = cite("ГК РУз", "ст. 932", "Гражданский кодекс, часть вторая",
        ru=(g, r"При заключении договора страхования имущества страховщик вправе произвести осмотр"),
        uz=(gu, r"Mol-mulkni sugʻurtalash shartnomasi tuzilayotganida sugʻurtalovchi sugʻurta qilinayotgan mol-mulkni koʻzdan kechirishga"),
        urls=U["gk"])
    CITES["gk929"] = cite("ГК РУз", "ст. 929", "Гражданский кодекс, часть вторая",
        ru=(g, r"При заключении договора имущественного страхования между страхователем и страховщиком должно быть достигнуто соглашение"),
        urls=U["gk"])
    CITES["gk927"] = cite("ГК РУз", "ст. 927", "Гражданский кодекс, часть вторая",
        ru=(g, r"Договор страхования должен быть заключен в письменной или электронной форме"), urls=U["gk"])
    CITES["gk947"] = cite("ГК РУз", "ст. 947", "Гражданский кодекс, часть вторая",
        ru=(g, r"Договор страхования, если в нем не предусмотрено иное, вступает в силу в момент уплаты"), urls=U["gk"])
    CITES["gk948"] = cite("ГК РУз", "ст. 948", "Гражданский кодекс, часть вторая",
        ru=(g, r"При досрочном прекращении договора страхования по обстоятельствам, указанным в части первой настоящей статьи"),
        uz=(gu, r"Sugʻurta shartnomasi ushbu moddaning birinchi qismida koʻrsatilgan holatlar boʻyicha muddatidan oldin bekor boʻlganda"),
        urls=U["gk"])
    CITES["gk955"] = cite("ГК РУз", "ст. 955", "Гражданский кодекс, часть вторая",
        ru=(g, r"Страховщик вправе отказать страхователю \(выгодоприобретателю\) в выплате страхового возмещения"), urls=U["gk"])
    CITES["gk942"] = cite("ГК РУз", "ст. 942", "Гражданский кодекс, часть вторая",
        ru=(g, r"Страховщик при определении размера страховой премии, подлежащей уплате по договору страхования, вправе"), urls=U["gk"])
    CITES["gk937"] = cite("ГК РУз", "ст. 937", "Гражданский кодекс, часть вторая",
        ru=(g, r"Если имущество или предпринимательский риск застрахованы лишь в части страховой стоимости"), urls=U["gk"])

    z, zu, ze = F["z730_ru"], F["z730_uz"], F["z730_en"]
    CITES["z730_331"] = cite("Закон о страховой деятельности ЗРУ-730", "ст. 33¹", "Закон РУз о страховой деятельности",
        ru=(z, r"Страховым полисом является документ, подтверждающий уплату страховой премии"),
        uz=(zu, r"Sugʻurta shartnomasiga yoki sugʻurta qoidalariga asosan belgilangan sugʻurta mukofotining toʻlanganligini"),
        urls=U["z730"])
    CITES["z730_34"] = cite("Закон о страховой деятельности ЗРУ-730", "ст. 34", "Закон РУз о страховой деятельности",
        ru=(z, r"Страховщик обязан выплатить страховое возмещение \(страховую выплату\) указанному в договоре"),
        uz=(zu, r"Sugʻurtalovchi sugʻurta shartnomasida koʻrsatilgan shaxsga faqat sugʻurta hodisasi yuz bergan taqdirda"),
        en=(ze, r"The insurer shall be obliged to pay the insurance indemnity"), urls=U["z730"])
    CITES["z730_63"] = cite("Закон о страховой деятельности ЗРУ-730", "ст. 63", "Закон РУз о страховой деятельности",
        ru=(z, r"В процессе оказании страховых услуг страховщик обязан предоставить потребителям страховых услуг"),
        urls=U["z730"])
    CITES["z730_63_1"] = cite("Закон о страховой деятельности ЗРУ-730", "ст. 63 ч. 1", "Закон РУз о страховой деятельности",
        ru=(z, r"Потребители страховых услуг свободны в выборе страховщика"),
        en=(ze, r"Consumers of insurance services shall be free to choose an insurer"), urls=U["z730"])
    CITES["z730_62"] = cite("Закон о страховой деятельности ЗРУ-730", "ст. 62", "Закон РУз о страховой деятельности",
        ru=(z, r"Не подлежат разглашению сведения о страхователях, застрахованных лицах"), urls=U["z730"])
    CITES["z730_30"] = cite("Закон о страховой деятельности ЗРУ-730", "ст. 30", "Закон РУз о страховой деятельности",
        ru=(z, r"Правила страхования \(за исключением правил страхования, принимаемых в рамках международных систем"),
        urls=U["z730"])
    CITES["z730_24"] = cite("Закон о страховой деятельности ЗРУ-730", "ст. 24", "Закон РУз о страховой деятельности",
        ru=(z, r"Страховым сюрвейером является юридическое лицо, имеющее в своем штате"), urls=U["z730"])
    CITES["z730_33"] = cite("Закон о страховой деятельности ЗРУ-730", "ст. 33", "Закон РУз о страховой деятельности",
        en=(ze, r"When determining the amount of the insurance premium payable under the insurance contract"), urls=U["z730"])

    CITES["p1806_15"] = cite("Положение 1806 (платёжеспособность)", "п. 15", "Положение о платёжеспособности страховщиков",
        ru=(F["p1806_ru"], r"Предельно допустимый размер обязательств страховщика \(перестраховщика\) по отдельным рискам, за исключением"),
        urls=U["p1806"])
    CITES["p1806_15b"] = cite("Положение 1806 (платёжеспособность)", "п. 15 абз. 3", "Положение о платёжеспособности страховщиков",
        ru=(F["p1806_ru"], r"Страховщик \(перестраховщик\) вправе принять обязательства по отдельным рискам с превышением норматива"),
        urls=U["p1806"])
    CITES["p1882_rk"] = cite("Положение 1882 (страховые резервы)", "резерв катастроф", "Положение о страховых резервах",
        ru=(F["p1882_ru"], r"Резерв катастроф \(РК\) предназначен для покрытия чрезвычайного ущерба"), urls=U["p1882"])

    CITES["z210_10"] = cite("ЗРУ-210 (ОСГОР)", "ст. 10", "Закон об обязательном страховании гражданской ответственности работодателя",
        ru=(F["z210_ru"], r"Страховая сумма по договору обязательного страхования гражданской ответственности работодателя устанавливается"),
        urls=U["z210"])
    CITES["z210_8"] = cite("ЗРУ-210 (ОСГОР)", "ст. 8", "Закон об обязательном страховании гражданской ответственности работодателя",
        ru=(F["z210_ru"], r"Страховщик не вправе применять базовую ставку и коэффициенты страховых тарифов"), urls=U["z210"])
    CITES["p177_26"] = cite("ПКМ № 177 (Правила ОСГОР)", "п. 26", "Правила обязательного страхования ответственности работодателя",
        ru=(F["p177_ru"], r"Страховая премия по договору обязательного страхования уплачивается работодателем единовременно"),
        urls=U["p177"])
    CITES["p177_43"] = cite("ПКМ № 177 (Правила ОСГОР)", "п. 43", "Правила обязательного страхования ответственности работодателя",
        ru=(F["p177_ru"], r"расходов на погребение, определяемом в договоре обязательного страхования"), urls=U["p177"])
    CITES["p177_29"] = cite("ПКМ № 177 (Правила ОСГОР)", "п. 29", "Правила обязательного страхования ответственности работодателя",
        ru=(F["p177_ru"], r"не позднее пяти рабочих дней с момента изменения размера годовой заработной платы"), urls=U["p177"])

    CITES["z155_9"] = cite("ЗРУ-155 (ОСГО ВТС)", "ст. 9", "Закон об ОСГО владельцев транспортных средств",
        ru=(F["z155_ru"], r"Договор обязательного страхования заключается сроком на один год"), urls=U["z155"])
    CITES["z155_sum"] = cite("ЗРУ-155 (ОСГО ВТС)", "ст. 22", "Закон об ОСГО владельцев транспортных средств",
        ru=(F["z155_ru"], r"Страховая сумма, в пределах которой возмещается вред, причиненный жизни"), urls=U["z155"])
    CITES["z155_23"] = cite("ЗРУ-155 (ОСГО ВТС)", "ст. 23", "Закон об ОСГО владельцев транспортных средств",
        ru=(F["z155_ru"], r"Страховое возмещение выплачивается страховщиком непосредственно потерпевшему"), urls=U["z155"])
    CITES["z155_15d"] = cite("ЗРУ-155 (ОСГО ВТС)", "ст. 21", "Закон об ОСГО владельцев транспортных средств",
        ru=(F["z155_ru"], r"Страховщик принимает решение о выплате или отказе в выплате страхового возмещения в течение"),
        urls=U["z155"])
    CITES["z155_repair"] = cite("ЗРУ-155 (ОСГО ВТС)", "ст. 21", "Закон об ОСГО владельцев транспортных средств",
        ru=(F["z155_ru"], r"Владельцы транспортных средств не вправе производить ремонт или утилизацию"), urls=U["z155"])

    CITES["z386_3"] = cite("ЗРУ-386 (ОСГОП)", "ст. 3", "Закон об обязательном страховании ответственности перевозчика",
        ru=(F["z386_ru"], r"потерпевший — пассажир, здоровью и \(или\) имуществу которого причинен вред"), urls=U["z386"])

    CITES["z547_271"] = cite("ЗРУ-547 (персональные данные)", "ст. 27¹", "Закон о персональных данных",
        ru=(F["z547_ru"], r"Персональные данные граждан Республики Узбекистан должны храниться при обеспечении"),
        urls=U["z547"])
    CITES["z547_5"] = cite("ЗРУ-547 (персональные данные)", "ст. 5", "Закон о персональных данных",
        ru=(F["z547_ru"], r"Основными принципами настоящего Закона являются"), urls=U["z547"])
    CITES["z547_27"] = cite("ЗРУ-547 (персональные данные)", "ст. 27", "Закон о персональных данных",
        ru=(F["z547_ru"], r"Государство гарантирует защиту персональных данных"), urls=U["z547"])

    CITES["p3845_15"] = cite("Положение 3845 (страховые агенты)", "п. 15",
        "Положение о страховых агентах (приказ НАПП № 14 от 20.04.2026, рег. 3845)",
        uz=(F["p3845_uz"], r"Sug‘urta agenti vositachiligida tuzilgan sug‘urta shartnomalari bo‘yicha vositachilik haqi"),
        urls=U["p3845"])
    CITES["p3845_15b"] = cite("Положение 3845 (страховые агенты)", "п. 15 абз. 2", "Положение о страховых агентах",
        uz=(F["p3845_uz"], r"Topshiriq shartnomasi \(agent bitimi\) asosida sugʼurta agentlari ishtirokida"), urls=U["p3845"])
    CITES["p3845_16"] = cite("Положение 3845 (страховые агенты)", "п. 16", "Положение о страховых агентах",
        uz=(F["p3845_uz"], r"Sug‘urta shartnomalari muddatidan oldin bekor qilinganda"), urls=U["p3845"])


# ---------------------------------------------------------------- вопросы и ответы
def I(id, tags, q, a, cites=(), rules=()):
    return {"id": id, "tags": list(tags),
            "q": {"ru": q[0], "uz": q[1], "en": q[2]},
            "a": {"ru": a[0], "uz": a[1], "en": a[2]},
            "citations": [C(k) for k in cites],
            "rule_codes": list(rules)}


def items():
    it = []
    A = it.append

    A(I("underinsurance", ["недострахование", "ст. 936", "пропорция", "ниже стоимости",
                           "меньше стоимости", "qiymatdan kam", "qiymatdan past", "below the value"],
        ("Страховая сумма ниже стоимости — что будет при убытке?",
         "Sugʻurta summasi qiymatdan past boʻlsa, zarar yuz berganda nima boʻladi?",
         "The sum insured is below the value — what happens at a loss?"),
        ("Выплата уменьшается пропорционально: возмещается доля убытка, равная отношению страховой суммы к страховой стоимости. Это не отказ, а пропорциональное возмещение. Клиента нужно предупредить до подписания договора и предложить поднять сумму до стоимости.",
         "Toʻlov mutanosib ravishda kamayadi: zarar sugʻurta summasining sugʻurta qiymatiga nisbati boʻyicha qoplanadi. Bu rad etish emas, mutanosib qoplash. Mijozni shartnoma imzolangunga qadar ogohlantirish va summani qiymatgacha koʻtarishni taklif qilish kerak.",
         "The indemnity is reduced proportionally: the loss is paid in the ratio of the sum insured to the insurable value. This is not a refusal but proportional indemnity. Warn the client before signing and offer to raise the sum to full value. (unofficial translation)"),
        ["gk936"], ["underinsurance"]))

    A(I("sum_over_value", ["превышение стоимости", "ст. 938", "ничтожность", "больше стоимости",
                           "выше стоимости", "qiymatdan oshsa", "qiymatdan ortiq", "exceeds the value"],
        ("Можно ли поставить страховую сумму выше стоимости имущества?",
         "Sugʻurta summasini mol-mulk qiymatidan yuqori qilib belgilash mumkinmi?",
         "May the sum insured exceed the value of the property?"),
        ("Нет. Договор недействителен в той части, где страховая сумма превышает страховую стоимость, а излишне уплаченная премия не возвращается. Превышение надо снять до подписания или подтвердить стоимость документами.",
         "Yoʻq. Shartnoma sugʻurta summasi sugʻurta qiymatidan ortiq boʻlgan qismida haqiqiy emas, ortiqcha toʻlangan mukofot esa qaytarilmaydi. Ortiqchani imzolashdan oldin olib tashlash yoki qiymatni hujjat bilan tasdiqlash kerak.",
         "No. The contract is invalid in the part where the sum insured exceeds the insurable value, and the excess premium is not refunded. Remove the excess before signing or document the value. (unofficial translation)"),
        ["gk938"], ["sum_over_value", "sum_vs_valuation"]))

    A(I("additional_insurance", ["дополнительное страхование", "ст. 937"],
        ("Клиент застрахован не на полную стоимость — можно ли дострахoвать?",
         "Mijoz toʻliq qiymatga sugʻurtalanmagan — qoʻshimcha sugʻurta qilish mumkinmi?",
         "The client is insured below value — may the balance be insured additionally?"),
        ("Да, дострахование допускается, в том числе у другого страховщика, но общая страховая сумма по всем договорам не должна превышать страховую стоимость. Иначе наступают последствия страхования сверх стоимости.",
         "Ha, qoʻshimcha sugʻurta, shu jumladan boshqa sugʻurtalovchida ham mumkin, biroq barcha shartnomalar boʻyicha umumiy summa sugʻurta qiymatidan oshmasligi kerak. Aks holda qiymatdan ortiq sugʻurta oqibatlari kelib chiqadi.",
         "Yes, top-up cover is allowed, including with another insurer, provided the total sum insured under all contracts does not exceed the insurable value. (unofficial translation)"),
        ["gk937"], ["sum_over_value"]))

    A(I("double_insurance", ["двойное страхование", "ст. 939"],
        ("Объект застрахован у двух страховщиков — кто и сколько платит?",
         "Obyekt ikki sugʻurtalovchida sugʻurtalangan — kim va qancha toʻlaydi?",
         "The object is insured with two insurers — who pays and how much?"),
        ("Каждый страховщик отвечает в пределах своего договора, но общая сумма всех возмещений не может превышать реальный ущерб. Страхователь вправе получить возмещение с любого страховщика, а недостающую часть — с другого.",
         "Har bir sugʻurtalovchi oʻz shartnomasi doirasida javob beradi, biroq barcha tovonlarning umumiy summasi haqiqiy zarardan oshmaydi. Sugʻurta qildiruvchi tovonni istalgan sugʻurtalovchidan, yetishmagan qismini esa boshqasidan olishga haqli.",
         "Each insurer pays within its own contract, but the total of all indemnities may not exceed the actual loss. (unofficial translation)"),
        ["gk939"], ["sum_over_value"]))

    A(I("insured_value_agreed", ["страховая стоимость", "ст. 935", "оценка"],
        ("Кто определяет страховую стоимость — система или стороны?",
         "Sugʻurta qiymatini kim belgilaydi — tizimmi yoki taraflarmi?",
         "Who sets the insurable value — the system or the parties?"),
        ("Страховая стоимость определяется соглашением сторон на момент заключения договора. Расчёт системы — предварительная оценка страховщика, окончательная стоимость фиксируется в договоре.",
         "Sugʻurta qiymati shartnoma tuzilayotgan paytda taraflar kelishuviga koʻra aniqlanadi. Tizim hisobi — sugʻurtalovchining dastlabki bahosi, yakuniy qiymat shartnomada qayd etiladi.",
         "The insurable value is set by agreement of the parties at the time of contracting; the system figure is only the insurer's preliminary estimate. (unofficial translation)"),
        ["gk935"], ["value_agreed"]))

    A(I("value_cannot_dispute", ["оспаривание стоимости", "ст. 935", "осмотр"],
        ("Можно ли позже оспорить стоимость, указанную в договоре?",
         "Shartnomada koʻrsatilgan qiymatni keyinchalik nizolashish mumkinmi?",
         "Can the value stated in the contract be challenged later?"),
        ("Нет, кроме одного случая: если страховщик не воспользовался правом на оценку риска до заключения договора и был умышленно введён в заблуждение. Поэтому осмотр и фиксация стоимости до подписания — защита страховщика.",
         "Yoʻq, faqat bitta holat bundan mustasno: sugʻurtalovchi shartnoma tuzilgunga qadar xavfni baholash huquqidan foydalanmagan va bila turib chalgʻitilgan boʻlsa. Shuning uchun imzolashdan oldin koʻzdan kechirish muhim.",
         "No, except where the insurer did not use its right to assess the risk before contracting and was deliberately misled. (unofficial translation)"),
        ["gk935_2"], ["value_no_inspection"]))

    A(I("value_date_place", ["дата и место оценки", "ст. 934"],
        ("На какую дату и в каком месте определяется стоимость имущества?",
         "Mol-mulk qiymati qaysi sana va qaysi joyda aniqlanadi?",
         "As of what date and place is the value of property determined?"),
        ("Действительная стоимость — это стоимость имущества в месте его нахождения в день заключения договора. Оценка полугодовой давности или по другому региону не годится: её нужно обновить.",
         "Haqiqiy qiymat — mol-mulkning shartnoma tuzilgan kuni turgan joyidagi qiymati. Olti oy oldingi yoki boshqa hudud boʻyicha baho yaramaydi, uni yangilash kerak.",
         "The actual value is the value at the location of the property on the day the contract is concluded. (unofficial translation)"),
        ["gk934"], ["value_date_place", "sum_vs_valuation"]))

    A(I("inspection_right", ["осмотр", "ст. 932", "оценка риска"],
        ("Обязан ли страховщик осматривать имущество перед договором?",
         "Sugʻurtalovchi shartnomadan oldin mol-mulkni koʻzdan kechirishi shartmi?",
         "Must the insurer inspect the property before the contract?"),
        ("Это право, а не обязанность: страховщик вправе осмотреть имущество и при необходимости организовать оценку. Но отказ от осмотра лишает страховщика возможности позже оспорить стоимость.",
         "Bu huquq, majburiyat emas: sugʻurtalovchi mol-mulkni koʻzdan kechirishga va zarurat boʻlsa baholashni tashkil etishga haqli. Ammo koʻzdan kechirmaslik keyinchalik qiymatni nizolashish imkonini yoʻqotadi.",
         "It is a right, not a duty: the insurer may inspect the property and arrange a valuation if needed. (unofficial translation)"),
        ["gk932"], ["value_no_inspection"]))

    A(I("essential_terms", ["существенные условия", "ст. 929"],
        ("Какие условия обязательно должны быть в договоре имущественного страхования?",
         "Mulkiy sugʻurta shartnomasida qanday shartlar boʻlishi shart?",
         "What terms must an property insurance contract contain?"),
        ("Объект страхования, характер страхового случая, размер страховой суммы, порядок определения возмещения (если оно может быть меньше суммы), размер и сроки уплаты премии, срок действия договора. Без согласования этих условий договор не считается заключённым.",
         "Sugʻurta obyekti, sugʻurta hodisasi xususiyati, sugʻurta summasi miqdori, tovonni aniqlash tartibi, mukofot miqdori va toʻlash muddati, shartnoma amal qilish muddati. Bu shartlarsiz shartnoma tuzilgan hisoblanmaydi.",
         "The insured object, the nature of the insured event, the sum insured, the method of calculating indemnity, the premium and its due dates, and the term of the contract. (unofficial translation)"),
        ["gk929"], ["docs_missing"]))

    A(I("contract_form", ["форма договора", "ст. 927", "электронная форма"],
        ("Можно ли заключать договор страхования в электронной форме?",
         "Sugʻurta shartnomasini elektron shaklda tuzish mumkinmi?",
         "May the insurance contract be concluded electronically?"),
        ("Да. С редакции ЗРУ-1154 договор страхования заключается в письменной или электронной форме; несоблюдение формы влечёт недействительность договора.",
         "Ha. OʻRQ-1154 tahririda sugʻurta shartnomasi yozma yoki elektron shaklda tuziladi; shakl buzilsa, shartnoma haqiqiy emas.",
         "Yes. Under the 2026 amendment the contract is concluded in written or electronic form; non-compliance renders it invalid. (unofficial translation)"),
        ["gk927"], []))

    A(I("contract_start", ["начало действия", "ст. 947"],
        ("С какого момента действует договор страхования?",
         "Sugʻurta shartnomasi qaysi paytdan amal qiladi?",
         "When does the insurance contract take effect?"),
        ("По общему правилу — с момента уплаты премии или её первого взноса, если в договоре не указано иное. Страхование распространяется на случаи после вступления договора в силу.",
         "Umumiy qoidaga koʻra — mukofot yoki uning birinchi badali toʻlangan paytdan, agar shartnomada boshqacha koʻrsatilmagan boʻlsa. Sugʻurta shartnoma kuchga kirgandan keyingi hodisalarga tatbiq etiladi.",
         "As a rule, upon payment of the premium or its first instalment, unless the contract states otherwise. (unofficial translation)"),
        ["gk947"], ["premium_unpaid"]))

    A(I("policy_without_premium", ["полис", "ст. 33¹", "премия не уплачена"],
        ("Полис выдан, а премия не поступила — обязана ли компания платить?",
         "Polis berilgan, mukofot tushmagan — kompaniya toʻlashi shartmi?",
         "The policy is issued but the premium was not received — must we pay?"),
        ("Да. Если полис выдан без уплаты премии, страховщик должен полностью обеспечить выполнение страховых обязательств. Поэтому полис не выдают до поступления денег.",
         "Ha. Mukofot toʻlanmasdan polis berilgan boʻlsa, sugʻurtalovchi sugʻurta majburiyatlarini toʻliq taʼminlashi lozim. Shuning uchun pul tushmaguncha polis berilmaydi.",
         "Yes. If a policy is issued without payment of the premium, the insurer must fully honour its obligations. (unofficial translation; the English text on lex.uz is the 2021 version and does not contain Article 33-1)"),
        ["z730_331"], ["premium_unpaid"]))

    A(I("early_termination_refund", ["досрочное расторжение", "ст. 948", "возврат премии"],
        ("Клиент расторгает договор досрочно — сколько премии вернуть?",
         "Mijoz shartnomani muddatidan oldin bekor qilmoqda — mukofotning qancha qismi qaytariladi?",
         "The client terminates early — how much premium is refunded?"),
        ("Если возможность страхового случая отпала (например, имущество погибло не по страховому случаю), страховщик оставляет себе часть премии пропорционально времени действия страхования, остальное возвращается. При простом отказе страхователя премия по общему правилу не возвращается, если договором не предусмотрено иное.",
         "Agar sugʻurta hodisasi ehtimoli yoʻqolgan boʻlsa, sugʻurtalovchi mukofotning sugʻurta amal qilgan vaqtga mutanosib qismini oʻzida qoldiradi, qolgani qaytariladi. Sugʻurta qildiruvchi shunchaki voz kechsa, umumiy qoidaga koʻra mukofot qaytarilmaydi.",
         "If the possibility of the insured event has ceased, the insurer keeps the premium in proportion to the time on risk and refunds the rest. (unofficial translation)"),
        ["gk948"], []))

    A(I("refusal_grounds", ["отказ в выплате", "ст. 955"],
        ("На каких основаниях можно отказать в выплате?",
         "Toʻlashni rad etish qanday asoslarda mumkin?",
         "On what grounds may payment be refused?"),
        ("Основания перечислены в ГК: договор прекратился до страхового случая, договор ничтожен, и другие случаи, прямо указанные в законе. Придумывать основания в правилах сверх закона нельзя — отказ будет оспорен.",
         "Asoslar FKda sanab oʻtilgan: shartnoma sugʻurta hodisasigacha bekor boʻlgan, shartnoma oʻz-oʻzidan haqiqiy emas va qonunda koʻrsatilgan boshqa hollar. Qonundan tashqari asos oʻylab topib boʻlmaydi.",
         "The grounds are listed in the Civil Code; grounds invented in the insurer's rules beyond the law will not stand. (unofficial translation)"),
        ["gk955"], []))

    A(I("payment_only_on_event", ["выплата", "ст. 34", "страховой случай"],
        ("Можно ли выплатить возмещение без страхового случая?",
         "Sugʻurta hodisasisiz tovon toʻlash mumkinmi?",
         "May indemnity be paid without an insured event?"),
        ("Нет. Страховщик обязан выплатить возмещение только при наступлении страхового случая и не вправе отказать в срок, если получены доказательства случая.",
         "Yoʻq. Sugʻurtalovchi tovonni faqat sugʻurta hodisasi yuz berganda toʻlaydi va dalillar olingan boʻlsa, belgilangan muddatda rad etishga haqli emas.",
         "No. The insurer pays only upon the occurrence of an insured event."),
        ["z730_34"], []))

    A(I("disclosure", ["раскрытие информации", "ст. 63", "ЗРУ-1154"],
        ("Что обязательно рассказать клиенту до продажи полиса?",
         "Polis sotilgunga qadar mijozga nimalarni aytish shart?",
         "What must be disclosed to the client before selling a policy?"),
        ("О страховщике и его агенте, о самой услуге, её цене и условиях возврата премии, о преимуществах и недостатках, о застрахованных рисках и исключениях, о размере возмещения, обязанностях сторон и порядке урегулирования претензий. Это требование ст. 63 в редакции ЗРУ-1154.",
         "Sugʻurtalovchi va uning agenti, xizmat va uning narxi, mukofotni qaytarish sharti, xizmatning ustunlik va kamchiliklari, sugʻurtalangan xavflar va istisnolar, tovon miqdori, tomonlar majburiyatlari va daʼvolarni hal qilish tartibi haqida.",
         "About the insurer and its agent, the service and its price, refund terms, advantages and drawbacks, covered risks and exclusions, the indemnity, the parties' duties and the claims procedure. (unofficial translation)"),
        ["z730_63"], ["disclosure"]))

    A(I("no_tying", ["навязывание", "ст. 63"],
        ("Можно ли продавать полис только в комплекте с другим продуктом?",
         "Polisni faqat boshqa mahsulot bilan birga sotish mumkinmi?",
         "May a policy be sold only in a bundle with another product?"),
        ("Нет. Страховщик не вправе ставить получение одной услуги условием получения другой — ни своей, ни третьего лица. Потребитель свободен в выборе страховщика и услуги.",
         "Yoʻq. Sugʻurtalovchi bir xizmatni olishni boshqasini olish sharti qilib qoʻya olmaydi. Isteʼmolchi sugʻurtalovchi va xizmatni tanlashda erkin.",
         "No. The insurer may not make one service a condition for another; the consumer is free to choose."),
        ["z730_63_1"], ["disclosure"]))

    A(I("insurance_secret", ["тайна страхования", "ст. 62"],
        ("Какие сведения о клиенте нельзя разглашать?",
         "Mijoz haqidagi qanday maʼlumotlarni oshkor qilish mumkin emas?",
         "What client information may not be disclosed?"),
        ("Сведения о страхователях, застрахованных и выгодоприобретателях, состоянии их здоровья, застрахованном имуществе, страховой сумме, банковских счетах и движении денег. Тарифы, виды и сроки страхования — открытая информация.",
         "Sugʻurta qildiruvchilar, sugʻurtalangan shaxslar va naf oluvchilar, ularning sogʻligʻi, sugʻurtalangan mol-mulk, sugʻurta summasi, bank hisoblari haqidagi maʼlumotlar. Tariflar va sugʻurta turlari esa ochiq maʼlumot.",
         "Data on policyholders, insured persons and beneficiaries, their health, the insured property, the sum insured and bank accounts. (unofficial translation)"),
        ["z730_62"], ["pd_ads_scrape"]))

    A(I("rules_of_insurance", ["правила страхования", "ст. 30", "износ"],
        ("Где должен быть описан порядок расчёта возмещения и учёт износа?",
         "Tovonni hisoblash tartibi va eskirish qayerda yozilishi kerak?",
         "Where must the indemnity calculation and depreciation be described?"),
        ("В правилах страхования, которые принимает и утверждает страховщик; они содержат основные положения страхования. Если износ не описан в правилах и договоре, применять его при выплате рискованно.",
         "Sugʻurtalovchi qabul qiladigan sugʻurta qoidalarida; ular sugʻurtaning asosiy qoidalarini oʻz ichiga oladi. Eskirish qoidalarda va shartnomada yozilmagan boʻlsa, uni toʻlovda qoʻllash xavfli.",
         "In the insurer's rules of insurance, which contain the main terms of cover. (unofficial translation)"),
        ["z730_30"], ["value_depreciation"]))

    A(I("surveyor_role", ["сюрвейер", "ст. 24"],
        ("Кто такой страховой сюрвейер и нужна ли ему лицензия?",
         "Sugʻurta syurveyeri kim va unga litsenziya kerakmi?",
         "Who is an insurance surveyor and is a licence needed?"),
        ("Сюрвейер — юридическое или физическое лицо, которое осматривает объект, анализирует риск и составляет заключение до заключения договора. Он начинает и прекращает деятельность в уведомительном порядке, лицензия не требуется.",
         "Syurveyer — obyektni koʻzdan kechiruvchi, xavfni tahlil qiluvchi va xulosa tuzuvchi yuridik yoki jismoniy shaxs. U faoliyatini xabardor qilish tartibida boshlaydi va tugatadi, litsenziya talab etilmaydi.",
         "A surveyor inspects the object, analyses the risk and issues an opinion before the contract; activity starts by notification. (unofficial translation)"),
        ["z730_24"], []))

    A(I("tariff_freedom", ["тариф", "ст. 942", "ставка"],
        ("Свободен ли страховщик в выборе тарифа?",
         "Sugʻurtalovchi tarifni tanlashda erkinmi?",
         "Is the insurer free to set its tariff?"),
        ("По добровольным видам — да, страховщик применяет свои тарифы с учётом объекта и характера риска. По обязательным видам тарифы устанавливает Кабинет Министров, отступать от них нельзя.",
         "Ixtiyoriy turlarda — ha, sugʻurtalovchi obyekt va xavf xususiyatini hisobga olib oʻz tariflarini qoʻllaydi. Majburiy turlarda tariflarni Vazirlar Mahkamasi belgilaydi, ulardan chetga chiqib boʻlmaydi.",
         "For voluntary classes the insurer applies its own tariffs; for compulsory classes tariffs are set by the Cabinet of Ministers."),
        ["gk942", "z730_33"], ["min_rate"]))

    A(I("min_rate", ["минимальная ставка", "тарифная политика", "54-П"],
        ("Ставка ниже минимальной по тарифной политике — что делать?",
         "Tarif siyosatidagi minimal stavkadan past stavka — nima qilish kerak?",
         "The rate is below the minimum in the tariff policy — what now?"),
        ("Это внутреннее правило компании (приказ 54-П от 23.09.2025), а не норма закона: ставка проверяется по каждому классу отдельно, а не по средней по договору. Ниже минимума ставку ставить нельзя без решения уполномоченного органа компании.",
         "Bu kompaniyaning ichki qoidasi (54-P buyrugʻi, 23.09.2025), qonun normasi emas: stavka har bir klass boʻyicha alohida tekshiriladi. Minimumdan past stavka kompaniyaning vakolatli organi qarorisiz qoʻyilmaydi.",
         "This is an internal company rule (order 54-P), not a statutory norm; the rate is checked per class, not on the contract average."),
        [], ["min_rate"]))

    A(I("below_technical", ["техническая ставка", "убыточность"],
        ("Ставка выше минимальной, но ниже технической — это нарушение?",
         "Stavka minimaldan yuqori, lekin texnik stavkadan past — bu qoidabuzarlikmi?",
         "The rate is above the minimum but below the technical rate — is that a breach?"),
        ("Нет, закон такой ставки не запрещает. Это экономическое предупреждение: премия не покрывает ожидаемые убытки, решение принимает андеррайтер, случай попадает в аналитику.",
         "Yoʻq, qonun buni taqiqlamaydi. Bu iqtisodiy ogohlantirish: mukofot kutilayotgan zararni qoplamaydi, qarorni anderrayter qabul qiladi.",
         "No, the law does not prohibit it; it is an economic warning for the underwriter."),
        [], ["below_technical"]))

    A(I("risk_limit_20", ["лимит 20%", "Положение 1806", "п. 15"],
        ("Какой максимум ответственности допустим по одному риску?",
         "Bitta xavf boʻyicha javobgarlikning eng koʻp miqdori qancha?",
         "What is the maximum liability per single risk?"),
        ("Не более 20% от суммы источников собственных средств и страховых резервов (для страхования экспортных контрактов от политических и коммерческих рисков — 15%). Считается по каждому отдельному риску.",
         "Oʻz mablagʻlari manbalari va sugʻurta zaxiralari summasining 20 foizidan ortiq emas (eksport shartnomalarini siyosiy va tijorat xavflaridan sugʻurtalashda — 15 foiz).",
         "Not more than 20% of the sum of own funds and insurance reserves (15% for export contract risks). (unofficial translation)"),
        ["p1806_15"], ["risk_limit_20"]))

    A(I("limit_exceed_reinsurance", ["перестрахование", "лимит", "Положение 1806"],
        ("Риск больше лимита 20% — можно ли его принять?",
         "Xavf 20 foiz chegaradan katta — uni qabul qilish mumkinmi?",
         "The risk exceeds the 20% limit — may we accept it?"),
        ("Да, но только если часть обязательств сверх норматива перестрахована в порядке, установленном Положением 1806. Без перестрахования превышение недопустимо.",
         "Ha, lekin meʼyordan ortiq qismi 1806-sonli Nizomga muvofiq qayta sugʻurta qilingan boʻlsa. Qayta sugʻurtasiz ortiqcha qabul qilinmaydi.",
         "Yes, but only if the part above the limit is reinsured under Regulation 1806. (unofficial translation)"),
        ["p1806_15b"], ["risk_limit_20"]))

    A(I("cat_reserve", ["резерв катастроф", "накопление", "сейсмозона"],
        ("Зачем следить за накоплением сумм в одной зоне?",
         "Nega bitta hududdagi summalar toʻplanishini kuzatish kerak?",
         "Why watch accumulation of sums in one zone?"),
        ("Потому что по одному событию (землетрясение, крупная авария) выплаты идут сразу по многим договорам — для этого формируется резерв катастроф. Накопление в зоне требует либо резерва, либо перестрахования.",
         "Chunki bitta hodisa (zilzila, yirik avariya) boʻyicha koʻplab shartnomalar boʻyicha toʻlov ketadi — buning uchun katastrofa zaxirasi shakllantiriladi. Toʻplanish zaxira yoki qayta sugʻurtani talab qiladi.",
         "Because one event triggers payouts under many contracts; the catastrophe reserve exists for this. (unofficial translation)"),
        ["p1882_rk"], ["cat_accumulation"]))

    A(I("no_franchise", ["франшиза"],
        ("Обязательна ли франшиза по закону?",
         "Fransiza qonun boʻyicha majburiymi?",
         "Is a deductible required by law?"),
        ("Нет, по добровольным видам закон франшизу не требует и не запрещает — она устанавливается договором и правилами страхования. По ОСГОР франшизы нет: тариф и условия жёстко заданы актом.",
         "Yoʻq, ixtiyoriy turlarda qonun fransizani talab ham, taqiqlamaydi ham — u shartnoma va qoidalarda belgilanadi. IFJMSda fransiza yoʻq.",
         "No. For voluntary classes the law is silent; the deductible is a matter of contract. For employer's liability there is none."),
        ["z730_30"], ["no_franchise"]))

    A(I("docs_missing", ["документы", "чек-лист"],
        ("Клиент не донёс документы — можно ли считать и оформлять?",
         "Mijoz hujjatlarni keltirmadi — hisoblab, rasmiylashtirish mumkinmi?",
         "The client has not provided documents — may we quote and issue?"),
        ("Расчёт считается предварительным, пока нет обязательных документов: без них не проверить объект, стоимость и существенные условия договора. Оформлять договор до получения документов о праве на объект и стоимости не следует.",
         "Majburiy hujjatlarsiz hisob dastlabki hisoblanadi: ularsiz obyekt, qiymat va muhim shartlarni tekshirib boʻlmaydi. Hujjatlar kelmaguncha shartnoma rasmiylashtirilmaydi.",
         "The quote is preliminary until the required documents arrive; without them the object, value and essential terms cannot be verified."),
        ["gk929"], ["docs_missing"]))

    A(I("agent_commission_25", ["агент", "комиссия", "25%", "Положение 3845"],
        ("Какой максимум комиссии страхового агента?",
         "Sugʻurta agenti vositachilik haqining eng koʻp miqdori qancha?",
         "What is the cap on the insurance agent's commission?"),
        ("Не более 25% от страховых премий, фактически поступивших на банковский счёт страховщика по договорам, заключённым при посредничестве агента. Это Положение о страховых агентах (рег. № 3845 от 02.06.2026).",
         "Agent vositachiligida tuzilgan shartnomalar boʻyicha sugʻurtalovchining bank hisobiga kelib tushgan mukofotlarning 25 foizidan oshmasligi kerak (3845-sonli Nizom).",
         "Not more than 25% of premiums actually received in the insurer's bank account under contracts concluded through the agent. (unofficial translation; the act exists in Uzbek only)"),
        ["p3845_15"], []))

    A(I("agent_no_staff_pay", ["агент", "штатные работники", "запрет"],
        ("Можно ли платить агентское вознаграждение штатному сотруднику страховщика?",
         "Sugʻurtalovchining shtatdagi xodimiga agentlik haqi toʻlash mumkinmi?",
         "May agent commission be paid to the insurer's own employee?"),
        ("Нет. Выплата работникам страховщика вознаграждения за посреднические услуги по договорам, заключённым с участием агентов на основании агентского соглашения, запрещена.",
         "Yoʻq. Agent bitimi asosida tuzilgan shartnomalar boʻyicha sugʻurtalovchi xodimlariga vositachilik xizmatlari uchun haq toʻlash taqiqlanadi.",
         "No. Paying the insurer's staff for intermediary services under such contracts is prohibited. (unofficial translation)"),
        ["p3845_15b"], []))

    A(I("agent_commission_return", ["агент", "возврат комиссии", "расторжение"],
        ("Договор расторгнут досрочно — возвращает ли агент комиссию?",
         "Shartnoma muddatidan oldin bekor qilindi — agent haqni qaytaradimi?",
         "The contract is terminated early — does the agent return the commission?"),
        ("Да, при досрочном расторжении договора агент возвращает страховщику комиссионное вознаграждение в порядке, установленном Положением о страховых агентах.",
         "Ha, shartnoma muddatidan oldin bekor qilinganda agent vositachilik haqini sugʻurtalovchiga qaytaradi.",
         "Yes; on early termination the agent returns the commission to the insurer. (unofficial translation)"),
        ["p3845_16"], []))

    A(I("osgor_sum", ["ОСГОР", "страховая сумма", "ФОТ", "ст. 10"],
        ("Как считается страховая сумма по ОСГОР?",
         "IFJMS boʻyicha sugʻurta summasi qanday hisoblanadi?",
         "How is the sum insured calculated for employer's liability?"),
        ("Это годовая заработная плата всех работников за предыдущие 12 месяцев (для работающих более года) либо за последующие 12 месяцев — для вновь созданных. Это не лимит на одного человека.",
         "Bu barcha xodimlarning oldingi 12 oy uchun yillik ish haqi (bir yildan ortiq faoliyat yuritayotganlar uchun) yoki yangi tashkil etilganlar uchun keyingi 12 oy uchun. Bu bir kishiga limit emas.",
         "It is the annual payroll of all employees for the previous 12 months, or for the next 12 months for new employers. (unofficial translation)"),
        ["z210_10"], ["OSGOR-01"]))

    A(I("osgor_tariff_fixed", ["ОСГОР", "тариф", "КСТ", "ст. 8"],
        ("Можно ли по ОСГОР дать скидку или изменить коэффициент?",
         "IFJMS boʻyicha chegirma berish yoki koeffitsientni oʻzgartirish mumkinmi?",
         "May we discount or change the coefficient for employer's liability?"),
        ("Нет. Страховщик не вправе применять базовую ставку и коэффициенты, отличные от установленных законом; коэффициент зависит от степени опасности вида деятельности. Премия = страховая сумма × 0,1 × КСТ / 100.",
         "Yoʻq. Sugʻurtalovchi qonunda belgilangandan boshqa bazaviy stavka va koeffitsientlarni qoʻllashga haqli emas; koeffitsient faoliyat xavflilik darajasiga bogʻliq.",
         "No. The insurer may not apply a base rate or coefficients other than those set by law. (unofficial translation)"),
        ["z210_8"], ["OSGOR-02", "OSGOR-03", "OSGOR-04"]))

    A(I("osgor_premium_5days", ["ОСГОР", "оплата премии", "5 дней"],
        ("В какой срок работодатель платит премию по ОСГОР?",
         "Ish beruvchi IFJMS mukofotini qaysi muddatda toʻlaydi?",
         "When must the employer pay the premium?"),
        ("Единовременно в течение пяти рабочих дней с даты заключения договора. При безналичной оплате датой считается дата поступления денег на расчётный счёт страховщика.",
         "Shartnoma tuzilgan sanadan besh ish kuni ichida bir yoʻla. Pul koʻchirishda toʻlov sanasi — pulning sugʻurtalovchi hisob raqamiga tushgan sanasi.",
         "In a lump sum within five working days from the date of the contract. (unofficial translation)"),
        ["p177_26"], ["OSGOR-07"]))

    A(I("osgor_funeral", ["ОСГОР", "погребение", "3 БРВ"],
        ("Какая сумма на погребение указывается в договоре ОСГОР?",
         "IFJMS shartnomasida dafn xarajatlari uchun qancha summa koʻrsatiladi?",
         "What funeral expense amount is stated in the contract?"),
        ("Отдельной строкой, но не более 3-кратного размера базовой расчётной величины на день смерти работника.",
         "Alohida satr bilan, ammo xodim vafot etgan kundagi bazaviy hisoblash miqdorining 3 baravaridan oshmagan holda.",
         "As a separate line, not exceeding three times the base calculation value on the day of death. (unofficial translation)"),
        ["p177_43"], ["OSGOR-08"]))

    A(I("osgor_fot_change", ["ОСГОР", "изменение ФОТ", "допсоглашение"],
        ("У клиента вырос фонд оплаты труда — что делать с договором ОСГОР?",
         "Mijozning ish haqi fondi oshdi — IFJMS shartnomasi bilan nima qilish kerak?",
         "The payroll has grown — what to do with the contract?"),
        ("Работодатель обязан сообщить страховщику не позднее пяти рабочих дней с момента изменения размера годовой заработной платы; премия пересчитывается допсоглашением пропорционально остатку срока.",
         "Ish beruvchi yillik ish haqi miqdori oʻzgargan paytdan besh ish kunidan kechiktirmay sugʻurtalovchiga xabar berishi shart; mukofot qoʻshimcha bitim bilan qayta hisoblanadi.",
         "The employer must notify the insurer within five working days of the change in annual payroll. (unofficial translation)"),
        ["p177_29"], ["OSGOR-10"]))

    A(I("osgor_term", ["ОСГОР", "срок договора"],
        ("На какой срок заключается договор ОСГОР?",
         "IFJMS shartnomasi qaysi muddatga tuziladi?",
         "For what term is the employer's liability contract concluded?"),
        ("На один год; более короткий срок возможен, только если сама деятельность работодателя короче года. Премия за неполный год считается пропорционально числу дней.",
         "Bir yilga; qisqaroq muddat faqat ish beruvchi faoliyati bir yildan qisqa boʻlsa mumkin. Toʻliq boʻlmagan yil uchun mukofot kunlarga mutanosib hisoblanadi.",
         "For one year; a shorter term only if the employer's activity itself is shorter. (unofficial translation)"),
        ["z210_8"], ["OSGOR-06"]))

    A(I("osgo_term", ["ОСГО", "срок", "ст. 9"],
        ("На какой срок заключается договор ОСГО владельцев транспортных средств?",
         "Transport vositalari egalarining majburiy sugʻurta shartnomasi qaysi muddatga tuziladi?",
         "For what term is motor third-party liability concluded?"),
        ("На один год, кроме случаев, прямо указанных в законе. Для иностранных транспортных средств — на весь срок временного использования, но не менее пятнадцати дней.",
         "Bir yilga, qonunda koʻrsatilgan hollar bundan mustasno. Chet el transport vositalari uchun — vaqtincha foydalanishning butun muddatiga, ammo kamida oʻn besh kun.",
         "For one year, with exceptions set by law; for foreign vehicles, for the period of temporary use but at least fifteen days. (unofficial translation)"),
        ["z155_9"], []))

    A(I("osgo_sum_cm", ["ОСГО", "страховая сумма", "ПКМ 141"],
        ("Кто устанавливает страховую сумму по ОСГО?",
         "Majburiy sugʻurta boʻyicha sugʻurta summasini kim belgilaydi?",
         "Who sets the sum insured for motor third-party liability?"),
        ("Кабинет Министров: размеры страховой суммы установлены постановлением № 141 от 24.06.2008. Страховщик не может изменить их договором.",
         "Vazirlar Mahkamasi: sugʻurta summasi 2008-yil 24-iyundagi 141-sonli qaror bilan belgilangan. Sugʻurtalovchi uni shartnoma bilan oʻzgartira olmaydi.",
         "The Cabinet of Ministers, by Resolution No. 141 of 24.06.2008. (unofficial translation)"),
        ["z155_sum"], []))

    A(I("osgo_payment_to_victim", ["ОСГО", "выплата потерпевшему"],
        ("Кому платится возмещение по ОСГО?",
         "Majburiy sugʻurta boʻyicha tovon kimga toʻlanadi?",
         "To whom is the indemnity paid?"),
        ("Непосредственно потерпевшему (его наследнику или правопреемнику) в размере причинённого вреда, но не более страховой суммы.",
         "Bevosita jabrlanuvchiga (uning merosxoʻri yoki huquqiy vorisiga) yetkazilgan zarar miqdorida, ammo sugʻurta summasidan ortiq emas.",
         "Directly to the victim (or their heir/successor), up to the sum insured. (unofficial translation)"),
        ["z155_23"], []))

    A(I("osgo_decision_15days", ["ОСГО", "срок решения", "15 дней"],
        ("В какой срок принимается решение о выплате по ОСГО?",
         "Majburiy sugʻurta boʻyicha toʻlov toʻgʻrisidagi qaror qaysi muddatda qabul qilinadi?",
         "Within what period is the claims decision taken?"),
        ("В течение пятнадцати календарных дней со дня представления документов. Решение — либо выплата, либо мотивированный отказ.",
         "Hujjatlar taqdim etilgan kundan oʻn besh kalendar kun ichida. Qaror — toʻlov yoki asoslantirilgan rad etish.",
         "Within fifteen calendar days from submission of the documents. (unofficial translation)"),
        ["z155_15d"], []))

    A(I("osgo_no_repair", ["ОСГО", "ремонт до осмотра"],
        ("Можно ли ремонтировать автомобиль до решения страховщика?",
         "Sugʻurtalovchi qarorigacha avtomobilni taʼmirlash mumkinmi?",
         "May the vehicle be repaired before the insurer's decision?"),
        ("Нет. Владельцы транспортных средств не вправе ремонтировать или утилизировать транспортное средство без письменного согласия страховщика или до решения о выплате либо отказе.",
         "Yoʻq. Transport vositasi egalari sugʻurtalovchining yozma roziligisiz yoki qaror qabul qilinmaguncha taʼmirlash yoki utilizatsiya qilishga haqli emas.",
         "No, not without the insurer's written consent or before the decision. (unofficial translation)"),
        ["z155_repair"], []))

    A(I("osgop_victim", ["ОСГОП", "перевозчик", "пассажир"],
        ("Кто считается потерпевшим по ОСГО перевозчика?",
         "Tashuvchining majburiy sugʻurtasida jabrlanuvchi kim hisoblanadi?",
         "Who is the victim under carrier's liability insurance?"),
        ("Пассажир, здоровью и (или) имуществу которого причинён вред и который имеет право на страховое возмещение; имущество пассажира — багаж и ручная кладь.",
         "Sogʻligʻiga va (yoki) mol-mulkiga zarar yetkazilgan hamda tovon olish huquqiga ega yoʻlovchi; yoʻlovchi mol-mulki — bagaj va qoʻl yuki.",
         "A passenger whose health and/or property has been harmed; passenger property means baggage and hand luggage. (unofficial translation)"),
        ["z386_3"], []))

    A(I("pd_storage_uz", ["персональные данные", "ст. 27¹", "хранение в Узбекистане"],
        ("Где должны храниться персональные данные граждан Узбекистана?",
         "Oʻzbekiston fuqarolarining shaxsiy maʼlumotlari qayerda saqlanishi kerak?",
         "Where must personal data of Uzbek citizens be stored?"),
        ("Данные граждан РУз хранятся с соблюдением принципов закона; обязательному хранению на территории Узбекистана подлежат биометрические и генетические данные, а также данные пользователей операторов телекоммуникаций. Проектное правило компании — держать всю базу в Узбекистане.",
         "OʻzR fuqarolari maʼlumotlari qonun tamoyillariga rioya qilgan holda saqlanadi; biometrik va genetik maʼlumotlar hamda telekommunikatsiya operatorlari foydalanuvchilari maʼlumotlari Oʻzbekiston hududida saqlanishi shart.",
         "Data of Uzbek citizens must be stored under the law's principles; biometric, genetic and telecom user data must be stored inside Uzbekistan. (unofficial translation)"),
        ["z547_271"], ["pd_ads_scrape"]))

    A(I("pd_principles", ["персональные данные", "принципы", "ст. 5"],
        ("Какие принципы обработки персональных данных обязательны?",
         "Shaxsiy maʼlumotlarni qayta ishlashning qanday tamoyillari majburiy?",
         "What principles of personal data processing are mandatory?"),
        ("Соблюдение конституционных прав, законность целей и способов обработки, точность и достоверность данных, конфиденциальность и защищённость, равенство прав участников, безопасность личности, общества и государства.",
         "Konstitutsiyaviy huquqlarga rioya qilish, qayta ishlash maqsad va usullarining qonuniyligi, maʼlumotlarning aniqligi, maxfiylik va himoyalanganlik, ishtirokchilar huquqlarining tengligi, xavfsizlik.",
         "Constitutional rights, lawful purposes and means, accuracy, confidentiality and security, equality of participants. (unofficial translation)"),
        ["z547_5"], ["pd_ads_scrape"]))

    A(I("pd_protection_measures", ["персональные данные", "защита", "ст. 27"],
        ("Что компания обязана делать для защиты персональных данных?",
         "Kompaniya shaxsiy maʼlumotlarni himoya qilish uchun nima qilishi shart?",
         "What must the company do to protect personal data?"),
        ("Принимать правовые, организационные и технические меры: защита частной жизни, целостность и сохранность данных, конфиденциальность, предотвращение незаконной обработки.",
         "Huquqiy, tashkiliy va texnik choralar koʻrish: shaxsiy hayotni himoya qilish, maʼlumotlar butunligi va saqlanishi, maxfiylik, noqonuniy qayta ishlashning oldini olish.",
         "Legal, organisational and technical measures: privacy, integrity, confidentiality, prevention of unlawful processing. (unofficial translation)"),
        ["z547_27"], []))

    A(I("pd_ads_scrape", ["объявления", "персональные данные", "оценка"],
        ("Можно ли сохранять данные продавца из объявления при оценке стоимости?",
         "Qiymatni baholashda eʼlondagi sotuvchi maʼlumotlarini saqlash mumkinmi?",
         "May we store the seller's data from an advertisement when valuing?"),
        ("Нет. Хранить можно только характеристики объекта и цену; ФИО, телефон, профиль и фото продавца не сохраняются, данные обезличиваются. Основание — принципы законности целей обработки и защиты данных.",
         "Yoʻq. Faqat obyekt tavsiflari va narxni saqlash mumkin; sotuvchining FISh, telefoni, profili va surati saqlanmaydi, maʼlumotlar shaxssizlantiriladi.",
         "No. Only the object's features and price may be kept; the seller's name, phone, profile and photos must not be stored. (unofficial translation)"),
        ["z547_5"], ["pd_ads_scrape"]))

    A(I("credit_uncovered", ["кредитное страхование", "необеспеченная часть"],
        ("Какую часть кредита можно страховать?",
         "Kreditning qaysi qismini sugʻurtalash mumkin?",
         "Which part of a loan may be insured?"),
        ("В проекте действует правило: страхуется только необеспеченная залогом часть и не более 50% суммы кредита. Полного текста документа регулятора по кредитному страхованию в библиотеке нет — норму нужно подтвердить документом от заказчика.",
         "Loyihada qoida amal qiladi: faqat garov bilan taʼminlanmagan qism va kredit summasining 50 foizidan koʻp boʻlmagan qismi sugʻurtalanadi. Regulyator hujjatining toʻliq matni kutubxonada yoʻq.",
         "Project rule: only the unsecured part and not more than 50% of the loan. The regulator's full text is not in the library — to be confirmed."),
        [], ["credit_uncovered"]))

    A(I("credit_payer", ["кредитное страхование", "плательщик премии"],
        ("Кто платит премию по страхованию кредитного риска?",
         "Kredit xavfini sugʻurtalash boʻyicha mukofotni kim toʻlaydi?",
         "Who pays the premium in credit risk insurance?"),
        ("По правилу проекта страхователь — банк-кредитор, и премия принимается только от банка. Документального подтверждения нормой в библиотеке пока нет; запрошен документ регулятора.",
         "Loyiha qoidasiga koʻra sugʻurta qildiruvchi — kreditor bank, mukofot faqat bankdan qabul qilinadi. Kutubxonada hujjat bilan tasdiq yoʻq.",
         "By project rule the policyholder is the lending bank and only the bank may pay the premium; the regulator's document is not in the library."),
        [], ["credit_payer"]))

    A(I("sum_vs_valuation", ["страховая сумма", "оценка системы"],
        ("Страховая сумма выше оценки системы — это стоп?",
         "Sugʻurta summasi tizim bahosidan yuqori — bu toʻxtatishmi?",
         "The sum insured exceeds the system valuation — is that a stop?"),
        ("Это предупреждение: превышение надо либо снять, либо подтвердить документами о стоимости (отчёт оценщика, счёт, договор купли-продажи). Если превышена реальная страховая стоимость, договор ничтожен в части превышения.",
         "Bu ogohlantirish: ortiqchani olib tashlash yoki qiymat hujjatlari bilan tasdiqlash kerak. Haqiqiy sugʻurta qiymatidan oshsa, shartnoma oshgan qismida haqiqiy emas.",
         "It is a warning: remove the excess or support the value with documents. (unofficial translation)"),
        ["gk938"], ["sum_vs_valuation", "value_ad_source"]))

    A(I("appraiser_optional", ["оценщик", "отчёт об оценке"],
        ("Нужен ли отчёт независимого оценщика для страхования?",
         "Sugʻurta uchun mustaqil baholovchi hisoboti kerakmi?",
         "Is an independent appraiser's report required for insurance?"),
        ("Нет, обязательная оценка предусмотрена не для страхования, а для сделок с госимуществом и спора о стоимости. Для страхования стоимость определяется соглашением сторон, а страховщик вправе организовать оценку сам.",
         "Yoʻq, majburiy baholash sugʻurta uchun emas, davlat mulki bitimlari va qiymat boʻyicha nizolar uchun nazarda tutilgan. Sugʻurtada qiymat taraflar kelishuviga koʻra aniqlanadi.",
         "No; mandatory valuation applies to state property deals and value disputes, not insurance. (unofficial translation)"),
        ["gk935", "gk932"], ["appraiser_optional"]))

    A(I("value_ad_source", ["объявления", "рыночная стоимость"],
        ("Можно ли брать стоимость прямо из объявления на сайте?",
         "Qiymatni toʻgʻridan-toʻgʻri saytdagi eʼlondan olish mumkinmi?",
         "May we take the value straight from an online advertisement?"),
        ("Цена объявления — это цена предложения, а не рыночная стоимость: нужны корректировки на торг, состояние и износ. Документы клиента (счёт, договор, отчёт оценщика) приоритетнее объявлений.",
         "Eʼlondagi narx — taklif narxi, bozor qiymati emas: savdo, holat va eskirishga tuzatish kerak. Mijoz hujjatlari eʼlonlardan ustun.",
         "An advertised price is an offer price, not market value; adjustments are needed and client documents prevail."),
        [], ["value_ad_source"]))

    A(I("depreciation", ["износ", "возмещение"],
        ("Можно ли уменьшать выплату на износ?",
         "Toʻlovni eskirishga kamaytirish mumkinmi?",
         "May the payout be reduced for depreciation?"),
        ("Только если порядок определения возмещения, включая износ, описан в правилах страхования и договоре. Иначе уменьшение выплаты будет оспорено.",
         "Faqat tovonni aniqlash tartibi, shu jumladan eskirish, sugʻurta qoidalari va shartnomada yozilgan boʻlsa. Aks holda kamaytirish nizoga sabab boʻladi.",
         "Only if the indemnity calculation, including depreciation, is set out in the rules and the contract."),
        ["z730_30", "gk929"], ["value_depreciation"]))

    A(I("payment_deadline_property", ["срок выплаты", "имущество"],
        ("Какой срок выплаты по добровольному имущественному страхованию?",
         "Ixtiyoriy mulkiy sugʻurtada toʻlov muddati qanday?",
         "What is the payment deadline in voluntary property insurance?"),
        ("Закон единого срока не устанавливает — срок определяется договором и правилами страхования. При этом страховщик не вправе отказать в выплате в срок, установленный договором, если доказательства случая получены.",
         "Qonun yagona muddat belgilamaydi — muddat shartnoma va qoidalarda belgilanadi. Dalillar olingan boʻlsa, sugʻurtalovchi shartnomadagi muddatda rad etishga haqli emas.",
         "The law sets no single deadline; it is fixed by the contract and rules."),
        ["z730_34"], []))

    A(I("electronic_policy", ["ЕАИС", "электронный полис", "Положение 3571"],
        ("Можно ли выдавать полис в электронном виде?",
         "Polisni elektron shaklda berish mumkinmi?",
         "May the policy be issued electronically?"),
        ("Да: договор заключается в письменной или электронной форме, а порядок оказания страховых услуг в электронной форме определён Положением рег. № 3571 от 11.11.2024. В библиотеке есть узбекский (кириллица) текст Положения 3571.",
         "Ha: shartnoma yozma yoki elektron shaklda tuziladi, elektron shakldagi xizmat tartibi 3571-sonli Nizomda belgilangan.",
         "Yes; the contract may be electronic, and the procedure is set by Regulation No. 3571 of 11.11.2024. (unofficial translation)"),
        ["gk927"], []))

    A(I("personal_vs_property", ["классификация", "личное", "имущественное"],
        ("Кредитное страхование — это личное или имущественное?",
         "Kredit sugʻurtasi shaxsiy sugʻurtami yoki mulkiymi?",
         "Is credit insurance personal or property insurance?"),
        ("Имущественное. По терминологии компании личное страхование — только жизнь и здоровье; всё остальное, включая кредиты, гарантии и финансовые риски, относится к имущественному.",
         "Mulkiy. Kompaniya atamalariga koʻra shaxsiy sugʻurta — faqat hayot va sogʻliq; qolgani, jumladan kreditlar va moliyaviy xavflar, mulkiy sugʻurtaga kiradi.",
         "Property. In company terms personal insurance covers life and health only; everything else is property insurance."),
        [], []))

    A(I("official_language", ["язык", "официальный текст", "перевод"],
        ("Какой текст закона считается официальным?",
         "Qonunning qaysi matni rasmiy hisoblanadi?",
         "Which text of the law is official?"),
        ("Официальным является узбекский текст. Русские редакции норм, изменённых ЗРУ-1154 (2026), на lex.uz помечены как неофициальный перевод, английские тексты есть далеко не по всем актам и обычно отражают старую редакцию.",
         "Rasmiy matn — oʻzbek tilidagi matn. OʻRQ-1154 bilan oʻzgartirilgan normalarning ruscha tahriri lex.uz da norasmiy tarjima deb belgilangan.",
         "The Uzbek text is official. Russian versions of provisions amended in 2026 are marked as unofficial translations; English versions exist only for some acts."),
        [], []))

    A(I("zru1154_changes", ["ЗРУ-1154", "изменения", "2026"],
        ("Что главное изменил ЗРУ-1154 от 22.06.2026?",
         "22.06.2026-dagi OʻRQ-1154 nimani oʻzgartirdi?",
         "What did Law LRU-1154 of 22.06.2026 change?"),
        ("В ГК — статьи 927, 934, 936, 938, 955 (электронная форма договора, страховая сумма, недострахование, превышение стоимости, отказ в выплате); в Законе о страховой деятельности — новая ст. 33¹ о полисе и расширенное раскрытие информации по ст. 63; изменены законы об ОСГО, ОСГОР и ОСГОП.",
         "FKda — 927, 934, 936, 938, 955-moddalar; Sugʻurta faoliyati toʻgʻrisidagi qonunda — polis haqida yangi 33¹-modda va 63-modda boʻyicha kengaytirilgan axborot berish; majburiy sugʻurta qonunlari ham oʻzgartirilgan.",
         "It amended Civil Code articles 927, 934, 936, 938, 955, introduced Article 33-1 on the policy and expanded disclosure under Article 63. (unofficial translation)"),
        ["z730_331", "gk927"], []))

    A(I("reserves_who", ["резервы", "Положение 1882"],
        ("Кто и какие страховые резервы обязан формировать?",
         "Kim va qanday sugʻurta zaxiralarini shakllantirishi shart?",
         "Who must form insurance reserves and which ones?"),
        ("Страховщик формирует резервы по Положению 1882: незаработанной премии, убытков, катастроф, колебаний убыточности и другие. По обязательным видам (ОСГО, ОСГОР, ОСГОП) дополнительно формируется резерв предупредительных мероприятий.",
         "Sugʻurtalovchi 1882-sonli Nizomga koʻra zaxiralarni shakllantiradi: ishlab topilmagan mukofot, zararlar, katastrofa va boshqalar. Majburiy turlarda ogohlantiruvchi tadbirlar zaxirasi ham shakllantiriladi.",
         "The insurer forms reserves under Regulation 1882; for compulsory classes a preventive measures reserve is also required. (unofficial translation)"),
        ["p1882_rk"], []))

    A(I("spec_tech_osgo", ["спецтехника", "ОСГО", "трактор"],
        ("Нужен ли полис ОСГО на трактор или экскаватор?",
         "Traktor yoki ekskavatorga majburiy sugʻurta polisi kerakmi?",
         "Is motor third-party liability needed for a tractor or excavator?"),
        ("Вопрос решается по тому, зарегистрирована ли машина как транспортное средство и допущена ли к дорожному движению: техника на дорогах общего пользования подпадает под ОСГО владельцев транспортных средств. Точный перечень — в Законе ЗРУ-155 и Правилах ПКМ № 141; в спорном случае запросите регистрационные документы.",
         "Masala mashina transport vositasi sifatida roʻyxatdan oʻtgani va yoʻl harakatiga qoʻyilganiga qarab hal qilinadi. Aniq roʻyxat OʻRQ-155 va 141-sonli qaror Qoidalarida; shubhali holatda roʻyxat hujjatlarini soʻrang.",
         "It depends on whether the machine is registered as a vehicle and admitted to road traffic; see LRU-155 and Resolution 141."),
        ["z155_9"], []))

    A(I("subrogation", ["суброгация", "виновник"],
        ("Можно ли взыскать выплаченное с виновника?",
         "Toʻlangan summani aybdordan undirish mumkinmi?",
         "Can we recover the paid indemnity from the party at fault?"),
        ("Да, по имущественному страхованию к страховщику переходит право требования к лицу, ответственному за убытки (суброгация, ст. 957 ГК). Документы по виновнику надо собирать сразу при урегулировании.",
         "Ha, mulkiy sugʻurtada zarar uchun javobgar shaxsga nisbatan talab huquqi sugʻurtalovchiga oʻtadi (subrogatsiya, FK 957-modda).",
         "Yes, subrogation applies in property insurance (Civil Code Article 957)."),
        [], []))

    A(I("osgor_after_payment", ["ОСГОР", "остаток суммы", "доплата премии"],
        ("После выплаты по ОСГОР страховая сумма уменьшилась — что дальше?",
         "IFJMS boʻyicha toʻlovdan keyin sugʻurta summasi kamaydi — keyin nima?",
         "After a payout the sum insured has decreased — what next?"),
        ("Остаток страховой суммы уменьшается на сумму выплаты, и работодатель доплачивает премию пропорционально выплате и остатку срока договора. Это оформляется допсоглашением.",
         "Sugʻurta summasi qoldigʻi toʻlangan tovonga kamayadi, ish beruvchi esa toʻlovga va shartnoma qolgan muddatiga mutanosib mukofot toʻlaydi.",
         "The remaining sum insured is reduced by the payout and the employer tops up the premium pro rata. (unofficial translation)"),
        ["z210_10"], ["OSGOR-09"]))

    A(I("osgor_no_contract", ["ОСГОР", "штраф", "КоАО"],
        ("Что грозит работодателю без договора ОСГОР?",
         "IFJMS shartnomasisiz ish beruvchiga nima tahdid qiladi?",
         "What are the consequences if the employer has no policy?"),
        ("Административный штраф по КоАО (ст. 49²): на должностное лицо 10–15 БРВ, повторно в течение года — 15–30 БРВ. Текста КоАО в библиотеке нет — ссылку нужно сверить по lex.uz перед использованием в письме клиенту.",
         "MJtKga koʻra maʼmuriy jarima (49²-modda): mansabdor shaxsga 10–15 BHM, bir yil ichida takrorlansa — 15–30 BHM. MJtK matni kutubxonada yoʻq.",
         "An administrative fine under the Code of Administrative Liability; the code text is not in the library and must be checked on lex.uz."),
        [], ["OSGOR-11"]))

    A(I("osgor_budget_double", ["ОСГОР", "бюджет", "двойное покрытие"],
        ("Можно ли за счёт бюджета страховать работников, уже застрахованных государством?",
         "Byudjet hisobidan davlat sugʻurtalagan xodimlarni sugʻurtalash mumkinmi?",
         "May budget funds insure employees already covered by the state?"),
        ("Нет: по ЗРУ-210 в редакции ЗРУ-1154 запрещено за счёт бюджетных средств страховать по ОСГОР работников, для которых предусмотрено обязательное государственное страхование жизни и здоровья.",
         "Yoʻq: OʻRQ-1154 tahriridagi OʻRQ-210 ga koʻra byudjet mablagʻlari hisobidan bunday xodimlarni sugʻurtalash taqiqlanadi.",
         "No; budget funds may not be used to double-cover employees under compulsory state life and health insurance. (unofficial translation)"),
        [], ["OSGOR-12"]))

    A(I("annuity_over_year", ["ОСГОР", "аннуитет"],
        ("Возмещение по ОСГОР на срок больше года — как оформляется?",
         "IFJMS boʻyicha bir yildan ortiq muddatga tovon qanday rasmiylashtiriladi?",
         "How is indemnity for more than a year arranged?"),
        ("Через трёхсторонний договор аннуитетов со страховщиком, имеющим лицензию по страхованию жизни (ст. 14, 15 ЗРУ-210, пп. 49–50 Правил ПКМ № 177).",
         "Hayotni sugʻurtalash litsenziyasiga ega sugʻurtalovchi bilan uch tomonlama annuitet shartnomasi orqali.",
         "Through a tripartite annuity contract with a life insurer. (unofficial translation)"),
        [], ["OSGOR-13"]))

    A(I("premium_instalments", ["рассрочка", "взносы", "ст. 942"],
        ("Можно ли принимать премию в рассрочку?",
         "Mukofotni boʻlib-boʻlib olish mumkinmi?",
         "May the premium be paid in instalments?"),
        ("По добровольным видам — да, если это предусмотрено договором; в договоре можно установить последствия неуплаты очередного взноса. По ОСГОР премия уплачивается единовременно в пять рабочих дней.",
         "Ixtiyoriy turlarda — ha, shartnomada nazarda tutilgan boʻlsa; navbatdagi badal toʻlanmaslik oqibatlarini shartnomada belgilash mumkin. IFJMSda mukofot bir yoʻla toʻlanadi.",
         "For voluntary classes yes, if the contract provides for it; for employer's liability the premium is a single payment."),
        ["gk942", "p177_26"], ["premium_unpaid"]))

    A(I("who_is_policyholder", ["страхователь", "выгодоприобретатель"],
        ("Чем страхователь отличается от выгодоприобретателя?",
         "Sugʻurta qildiruvchi naf oluvchidan nimasi bilan farq qiladi?",
         "How does the policyholder differ from the beneficiary?"),
        ("Страхователь заключает договор и платит премию; выгодоприобретатель — лицо, в пользу которого заключён договор и которое получает возмещение. Замена выгодоприобретателя возможна по правилам ГК (ст. 945).",
         "Sugʻurta qildiruvchi shartnoma tuzadi va mukofot toʻlaydi; naf oluvchi — foydasiga shartnoma tuzilgan va tovon oladigan shaxs.",
         "The policyholder concludes the contract and pays; the beneficiary receives the indemnity."),
        ["gk929"], []))

    return it


def selfcheck(data: dict) -> None:
    """Перед записью: каждая цитата дословна, у каждой есть рабочая ссылка."""
    bad = []
    for it in data["items"]:
        for c in it["citations"]:
            if not any((c["quote"] or {}).values()):
                bad.append(f"{it['id']}: цитата {c['article']} пустая на всех языках")
            if not any((c["url"] or {}).values()):
                bad.append(f"{it['id']}: у цитаты {c['article']} нет ни одной ссылки")
            for lang, q in (c["quote"] or {}).items():
                if q and not any(q in raw(rel) for rel in F.values()):
                    bad.append(f"{it['id']}: цитата {c['article']} ({lang}) не найдена дословно")
    if bad:
        for b in bad:
            print(" -", b)
        raise SystemExit("FAQ не собран: цитаты не прошли проверку")


def main():
    build_cites()
    data = {"version": "1.1",
            "generated": date.today().isoformat(),
            "about": "Ответы для сотрудников на ru/uz/en. Официальный текст актов — узбекский; "
                     "русские редакции норм, изменённых ЗРУ-1154, на lex.uz помечены как неофициальный перевод; "
                     "английские тексты есть только у ЗРУ-730 и ЗРУ-547 и отражают редакцию до 2026 года. "
                     "Цитаты дословны: сверяются с файлами library/01_Законодательство (убраны только кнопки "
                     "страницы lex.uz «Предложения по документу», «Прослушать аудио», «Получить ссылку…»); "
                     "комментарии LexUz и заголовки соседних статей в цитату не попадают. "
                     "Ссылки: /ru/docs/<номер> — русский текст, /uz/docs/-<номер> — узбекская латиница "
                     "(дефис обязателен), /uz/docs/<номер> — узбекская кириллица. "
                     "Если нормы нет на языке вопроса (Положение 3845 — только узбекский), показываем "
                     "узбекский оригинал и ссылку на него.",
            "sources": {k: str((LIB / v).relative_to(ROOT.parent)) if False else v for k, v in F.items()},
            "items": items()}
    selfcheck(data)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    n_c = sum(len(i["citations"]) for i in data["items"])
    print(f"Вопросов: {len(data['items'])}, цитат: {n_c}, файл: {OUT}")


if __name__ == "__main__":
    main()
