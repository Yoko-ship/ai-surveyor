"""
Разбор текстов актов и документов: нормализация, язык, чистка мусора выгрузки lex.uz и веб-форм,
нарезка файла на статьи и пункты (юниты). Без базы и без сети — только строки.
"""
import re
from pathlib import Path
from typing import Optional

from .. import textnorm
from .intents import _EN_MARKERS, _UZ_MARKERS
from .texts import DEFAULT_LANG

# апострофы во всех начертаниях: узбекская латиница пишется то ʻ, то ‘, то обычным '
APOSTROPHES = textnorm.APOSTROPHES
_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[0-9a-zA-Zа-яёА-ЯЁўқғҳЎҚҒҲ]+")

# мусор постраничной выгрузки lex.uz — в индекс не берём
NOISE = ("Предложения по документу", "Прослушать аудио", "Получить ссылку из элемента документа",
         "Комментарий LexUz", "См. предыдущую редакцию", "См. судебную практику",
         "Hujjat elementidan havola olish", "Hujjat bo'yicha takliflar",
         "Hujjatga taklif yuborish", "Audioni tinglash", "LexUZ sharhi", "LexUz sharhi")

# шапка нашей же выгрузки (tools/lex_fetch.py): адрес источника и дата загрузки — не текст закона
HEAD_RE = re.compile(r"^\s*(?:Источник|Загружено|Скачано|Индексировать|Manba|Yuklandi|Source)\s*:",
                     re.IGNORECASE)

# элементы страницы lex.uz (меню, кнопки формы «предложение по документу»): совпадение по всей
# строке целиком, чтобы не задеть норму, где те же слова стоят внутри предложения.
# Нормализуются ниже, сразу после объявления norm() — здесь norm ещё не определена.
CHROME_RAW = (
    "Все", "Вид", "A", "Ссылка на последующую редакцию", "Ссылка на предыдущую редакцию",
    "Индексация по ОКОЗ", "Индексация по ТСЗ", "Источники изменений", "Источники опубликования",
    "Закрыть", "×", "Предложение по документу", "Техническая ошибка", "Отправить", "Отменить",
    "Выберите тип предложения", "Ознакомился с правилами внесения предложений в законодательство",
    "Реклама на сайте", "RSS", "Новости сайта", "О Центре", "Печать", "Скачать",
    "Hammasi", "Koʻrinishi", "Keyingi tahrirga havola", "Oldingi tahrirga havola",
    "Keyingi tahrirga qarang.", "Oldingi tahrirga qarang.", "QTUK boʻyicha indekslash",
    "QMQ boʻyicha indekslash", "Oʻzgartirishlar manbasi", "Rasmiy nashr manbasi",
    "Yopish", "Joʻnatish", "Bekor qilish", "Taklif turini tanlang", "Hujjatga taklif",
    "Texnik xatolik", "Saytda reklama", "Sayt yangiliklari", "Markaz haqida", "Chop etish",
    # боковая панель карточки документа и кнопки «поделиться»: собраны обходом всех файлов
    # library/01_Законодательство/**/*.txt (встречаются в 12–34 файлах из 43 — это шаблон сайта,
    # а не текст акта). Попадали в цитаты вперемешку с нормой.
    "Основные реквизиты", "Дополнительная информация", "Дата вступления в силу", "Кодификация",
    "Корреспонденты", "Респонденты", "Пересмотренные акты", "Акты основания для пересмотра",
    "Поделиться", "Предложения по документу", "Прослушать аудио", "Печатная версия",
    "Asosiy rekvizitlar", "Qoʻshimcha axborot", "Kuchga kirish sanasi", "Kodifikatsiya",
    "Korrespondentlar", "Respondentlar", "Qayta koʻrib chiqilgan hujjatlar", "Koʻrinish",
    "Hujjatni qayta koʻrib chiqishga asos boʻlgan hujjatlar", "Ulashish",
    "Qonunchilikka taklif berish qoidalari bilan tanishdim",
    # переключатель языка и соцсети в шапке страницы
    "Рус", "Ўзб", "Oʻzb", "Eng", "Рус|Oʻzb", "Рус|Ўзб", "Oʻzb|Рус", "Ўзб|Рус", "Eng|Рус",
    "ONLINE TRANSLATE", "Facebook", "Twitter", "Telegram", "Instagram", "YouTube", "Одноклассники",
)

# подвал сайта: всё, что ниже, к акту отношения не имеет — разбор файла на этом месте заканчиваем
FOOTER_RAW = (
    "О Центре", "Markaz haqida", "© Государственное учреждение", "© Oʻzbekiston Respublikasi",
    "Национальный правовой информационный центр",
)

# подпись под актом и выходные данные: «Президент … И. КАРИМОВ», «г. Ташкент,», «16 апреля 2009 г.,»,
# «№ ЗРУ-210», «(Собрание законодательства …)». Это не статья 27 и не пункт — цитировать нельзя.
# Признак: должность + фамилия прописными в конце строки: «… Prezidenti Sh. MIRZIYOYEV»,
# «Президент Республики Узбекистан И. КАРИМОВ», «President of the Republic of Uzbekistan …»
SIGN_RE = re.compile(
    r"(?:Президент|Премьер-министр|Министр|Председатель|Prezident|Bosh\s+vazir|Vazir|Rais"
    r"|President\s+of\s+the\s+Republic|Prime\s+Minister|Minister|Chairman)"
    r"[^\n]*?[A-ZА-ЯЁʻʼ‘’]{4,}[.\s]*$")
IMPRINT_RE = re.compile(
    r"^(?:г\.\s*Ташкент|Ташкент\s*г?\.?|Toshkent\s*sh|№\s*\S+|N\s*\d|\d{1,2}\s+\S+\s+\d{4}\s*г"
    r"|\d{4}-yil\s+\d{1,2}|[OO`ʻ‘'’]?RQ-\d|ЗРУ-\d|ПКМ|\(Собрание\s+законодательства"
    r"|\((?:Oʻ|O‘|O`|O')?zbekiston\s+Respublikasi\s+qonun\s+hujjatlari"
    r"|\(Национальная\s+база|\(Qonun(?:chilik)?\s+hujjatlari)",
    re.IGNORECASE)
IMPRINT_MAX_LINES = 8          # выходные данные длиннее восьми строк не бывают

# невидимые знаки выгрузки: BOM, метки направления письма, мягкий перенос (он стоит внутри слова:
# «Дополни\u00adтельная информация»). Снимаем и в файле, и в цитате — иначе дословность не сходится
ZERO_WIDTH = textnorm.ZERO_WIDTH
_ZW_RE = textnorm._ZW_RE

# строка целиком — адрес страницы («https://lex.uz/docs/...»): это колонтитул выгрузки, не норма
URL_LINE_RE = re.compile(r"^\s*(?:https?://|www\.)\S+\s*$", re.IGNORECASE)
# строка целиком — дата («23.06.2026») или число из карточки документа («20», «100»):
# это ячейки таблицы реквизитов lex.uz, к тексту нормы отношения не имеют
CELL_LINE_RE = re.compile(r"^\s*(?:\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{1,4}|[IVXLC]{1,6}|[a-zA-Zа-яёА-ЯЁ])\s*$")
# редакционная сноска lex.uz с номером версии акта: «(13-modda … OʻRQ-1154-sonli Qonuni tahririda —
# Qonun hujjatlari maʼlumotlari milliy bazasi, …)», «(статья 934 в редакции Закона … № ЗРУ-1154)».
# Это история изменений, а не норма: цитировать её нельзя.
EDIT_NOTE_RE = re.compile(
    r"^\(.{0,400}?(?:в\s+редакции|tahririda|таҳририда|in\s+the\s+wording\s+of"
    r"|Дата\s+обновления|Yangilangan\s+sana)", re.IGNORECASE | re.DOTALL)
# редакционная справка lex.uz отдельной строкой: «Неофициальный перевод. Статья 35 дополнена
# частью третьей Законом … Дата вступления в силу — 25 июля 2026 года.» Это история изменений
# и отсылка к официальному тексту, а не норма — в цитату попадать не должна.
NOTE_LINE_RE = re.compile(r"^(?:Неофициальный\s+перевод|Norasmiy\s+tarjima|Unofficial\s+translation)\b",
                          re.IGNORECASE)
# «Дата обновления: …», «Версия 3», «Redaksiya …» отдельной строкой
STAMP_LINE_RE = re.compile(
    r"^\s*(?:Дата\s+обновления|Дата\s+актуализации|Версия|Редакция\s+от|Yangilangan"
    r"|Versiya|Tahrir\s+sanasi|Last\s+updated|Version)\b", re.IGNORECASE)

# разрыв: между соседними строками выброшен служебный мусор. Цитата не должна склеиваться
# через такой разрыв — иначе в ней окажется фрагмент, которого в акте нет подряд.
GAP = "\u2063"     # invisible separator: в текстах актов не встречается

# пустой бланк приложения: строка формы «____ ning», «20__ y. “___” ____», «6-ILOVA ______».
# Заполнять её должен страховщик — как норму такую строку показывать нельзя.
BLANK_RE = re.compile(r"_{3,}")


# --------------------------------------------------------------------------- #
#  Нормализация и язык (app/textnorm)
# --------------------------------------------------------------------------- #

def fold(s: str) -> str:
    """Для индекса: NFC, апострофы сняты, пробелы схлопнуты. Регистр сохраняем — его складывает сам
    токенайзер unicode61, а вот апостроф он считает разделителем, и «sugʻurta» распалось бы на
    «sug» и «urta». Поэтому апострофы именно снимаются, а не заменяются."""
    return textnorm.fold(s)


def quote_norm(s: str) -> str:
    """Для сверки дословности: сняты апострофы всех начертаний и невидимые знаки, пробелы
    схлопнуты. Регистр и знаки препинания сохраняются — иначе «дословно» ничего не значит."""
    return textnorm.fold(s)


def norm(s: str) -> str:
    """То же самое плюс нижний регистр — для сравнения строк в Python."""
    return fold(s).lower()


CHROME = {norm(x) for x in CHROME_RAW}
FOOTER = tuple(norm(x) for x in FOOTER_RAW)


def detect_lang(text: str) -> str:
    """Язык короткого вопроса. Сначала штатный определитель, потом простая эвристика.

    app/ingest.detect_language рассчитан на документы и на вопросе из пяти слов честно
    возвращает None — поэтому ниже добор по алфавиту и характерным словам.
    """
    t = norm(text)
    if not t:
        return DEFAULT_LANG
    try:
        from ..ingest import detect_language
        lang = (detect_language(text) or {}).get("language")
    except Exception:
        lang = None
    if lang in ("uz-latn", "uz-cyrl"):
        return "uz"
    if lang in ("ru", "en"):
        return lang
    tokens = set(_TOKEN_RE.findall(t))
    if re.search(r"[а-яёўқғҳ]", t):
        return DEFAULT_LANG                      # кириллица: ru (узбекская кириллица в вопросах не ждём)
    if tokens & _UZ_MARKERS:
        return "uz"
    if tokens & _EN_MARKERS:
        return "en"
    # латиница без опознавательных слов: узбекские буквы q/x/oʻ встречаются чаще, чем в английском
    if re.search(r"[qx]", t) and not tokens & _EN_MARKERS:
        return "uz"
    return "en"


def lang_of_name(name: str) -> Optional[str]:
    """Язык по пометке в имени файла: «… (uz).txt», «… (узб).txt», «… (en).txt», «… (рус).txt»."""
    low = name.lower()
    for mark in ("(uz)", "(узб)", "(узб.)", "(uz.)", "(o'zb)", "(oʻzb)"):
        if mark in low:
            return "uz"
    for mark in ("(en)", "(eng)", "(англ)"):
        if mark in low:
            return "en"
    for mark in ("(ru)", "(рус)", "(рус.)"):
        if mark in low:
            return "ru"
    return None


# --------------------------------------------------------------------------- #
#  Разбор файла на юниты
# --------------------------------------------------------------------------- #

# «Статья 938.», «938-modda», «Article 938», «4.» в начале строки
UNIT_RE = re.compile(
    r"^\s*(?:(?P<ru>Стать[яи]\s+(?P<ru_n>\d+[\d¹²³¹²³\-]*))"
    r"|(?P<uz>(?P<uz_n>\d+[\d\-]*)\s*-?\s*modda)"
    r"|(?P<en>Article\s+(?P<en_n>\d+[\d\-]*))"
    r")\s*[\.\)]?\s*(?P<title>.*)$", re.IGNORECASE)
POINT_RE = re.compile(r"^\s*(?P<n>\d+(?:\.\d+)*)\.\s+(?P<rest>\S.*)$")

# приложение к акту («1-ILOVA», «ПРИЛОЖЕНИЕ № 2»): это отдельный документ — бланк, таблица тарифов.
# Без этой границы содержимое приложения приклеивалось к последнему пункту и выдавалось как «п. 45».
APPENDIX_RE = re.compile(r"^\s*(?:(?P<uz>\d+(?:\s*\d+)?)\s*-\s*ILOVA"
                         r"|ПРИЛОЖЕНИЕ\s*(?:№\s*)?(?P<ru>\d+)?)\b", re.IGNORECASE)


def _appendix_label(m: re.Match) -> str:
    n = (m.group("uz") or m.group("ru") or "").replace(" ", "")
    return ("прил. " + n).strip() if m.group("ru") is not None or not m.group("uz") else n + "-ilova"

CHUNK_CHARS = 1800          # если структуры нет — режем на куски примерно по абзацу-полтора

TOC_MIN_CHARS = 100         # короче — это строка оглавления, а не норма
RUBRIC_MAX_LINES = 40       # рубрикатор lex.uz длиннее сорока строк не бывает


# элементы веб-форм на страницах продуктов других страховщиков: строка целиком — не текст условий
FORM_JUNK_RE = re.compile(
    r"^\s*(?:получить\s+расч[её]т|рассчитать(?:\s+стоимость)?|номер\s+телефона|телефон|ф\.?\s*и\.?\s*о\.?"
    r"|ваше\s+имя|имя|e-?mail|эл\.?\s*почта|отправить(?:\s+заявку)?|оставить\s+заявку|заказать\s+звонок"
    r"|оформить(?:\s+онлайн)?|купить(?:\s+полис)?|подробнее|согласен\s+на\s+обработку.*|\+?998[\d\s\-()]*"
    r"|hisoblash|telefon\s+raqami|ism|yuborish|ariza\s+qoldirish|get\s+a\s+quote|phone(?:\s+number)?"
    r"|full\s+name|send|submit)\s*[:*]?\s*$", re.IGNORECASE)



def _junk_head(ln: str, low: str) -> bool:
    """Шапка нашей выгрузки, шум lex.uz, кнопка или пункт меню, поле веб-формы страницы продукта."""
    if HEAD_RE.match(ln):
        return True                        # «Источник: …», «Загружено: …»
    if any(n in ln for n in NOISE):
        return True
    if low in CHROME:
        return True                        # кнопка или пункт меню страницы lex.uz
    return len(ln) < 80 and bool(FORM_JUNK_RE.match(ln))


def _junk_tail(ln: str) -> bool:
    """Колонтитул, ячейка карточки акта, редакционная справка или сноска, пустой бланк — не норма."""
    if ln.startswith("=== стр."):
        return True
    if URL_LINE_RE.match(ln) or STAMP_LINE_RE.match(ln) or CELL_LINE_RE.match(ln):
        return True                        # адрес страницы, «Дата обновления», ячейка карточки акта
    if NOTE_LINE_RE.match(ln):
        return True                        # редакционная справка «Неофициальный перевод. …»
    if len(ln) < 600 and EDIT_NOTE_RE.match(ln):
        return True                        # сноска о редакции и номере изменяющего акта — не норма
    return bool(BLANK_RE.search(ln))       # пустой бланк приложения — не норма


def _clean_lines(text: str) -> list:
    """Строки файла без служебного мусора выгрузки lex.uz.

    Рубрикаторы («[ ОКОЗ: 03.00.00.00 Гражданское законодательство / … ]», «[ OKOZ: … ]»,
    «[ СПиТ: … ]», «[ TSZ: … ]») почти всегда разорваны на несколько строк: «[» стоит в первой,
    «]» — через две-три строки. Раньше отбрасывалась только первая строка, а продолжение
    («03.11.22.02 Majburiy sugʻurta]») попадало в индекс и цитировалось как текст закона.
    Поэтому идём с состоянием: открылась скобка — пропускаем до закрывающей.
    """
    out = []
    skip_left = 0                          # сколько ещё строк рубрикатора пропускаем
    imprint_left = 0                       # сколько строк выходных данных после подписи пропускаем
    dropped = False                        # между соседними строками выброшен мусор

    for raw in text.splitlines():
        ln = _ZW_RE.sub("", raw).strip()
        if not ln:
            continue
        if skip_left:
            skip_left -= 1
            if "]" in ln:
                skip_left = 0
            dropped = True
            continue
        low = norm(ln)
        if any(low.startswith(f) for f in FOOTER):
            break                          # подвал сайта — текст акта кончился
        if _junk_head(ln, low):
            dropped = True
            continue
        if ln.startswith("["):
            if "]" not in ln:
                skip_left = RUBRIC_MAX_LINES      # предел, чтобы незакрытая скобка не съела акт
            dropped = True
            continue
        if _junk_tail(ln):
            dropped = True
            continue
        if imprint_left and IMPRINT_RE.match(ln):
            imprint_left -= 1
            dropped = True
            continue                       # «г. Ташкент,», «16 апреля 2009 г.,», «№ ЗРУ-210»
        imprint_left = 0
        if len(ln) < 200 and SIGN_RE.search(ln):
            # подпись под актом: дальше идут только выходные данные (город, дата, номер, источник
            # опубликования). Раньше всё это приклеивалось к последней статье и цитировалось
            # как её текст — «27-modda» отдавала «KARIMOV Toshkent sh., 2009-yil 16-aprel…».
            imprint_left = IMPRINT_MAX_LINES
            dropped = True
            continue
        if dropped and out:
            out.append(GAP)                # разрыв: дальше идёт уже не продолжение предыдущей строки
        dropped = False
        out.append(ln)
    return out


def segments(body: str) -> list:
    """Непрерывные куски текста акта: разрыв GAP означает, что между ними был выброшен мусор.

    Цитата берётся только внутри одного куска — иначе она склеивает два места акта и в самом
    акте такой фразы нет (дефект контролёра 22.09.2026).
    """
    return [s.strip() for s in (body or "").split(GAP) if s.strip()]


def strip_gaps(body: str) -> str:
    """Текст без меток разрыва — для поиска и для показа целиком."""
    return _WS_RE.sub(" ", (body or "").replace(GAP, " ")).strip()


def _unit_label(m: re.Match, lang: str) -> str:
    if m.group("ru"):
        return "ст. " + m.group("ru_n")
    if m.group("uz"):
        return m.group("uz_n").rstrip("-") + "-modda"
    return "Article " + m.group("en_n")


def split_units(text: str, lang: str) -> list:
    """Файл → список юнитов [{unit, title, text}]. Статьи, затем пункты, затем куски по размеру."""
    lines = _clean_lines(text)
    units, cur = [], None
    for ln in lines:
        m = UNIT_RE.match(ln)
        if m and len(ln) < 300:
            if cur:
                units.append(cur)
            cur = {"unit": _unit_label(m, lang), "title": (m.group("title") or "").strip(), "body": []}
            continue
        ap = APPENDIX_RE.match(ln)
        if ap and len(ln) < 200:
            if cur:
                units.append(cur)
            cur = {"unit": _appendix_label(ap), "title": ln.strip()[:120], "body": []}
            continue
        if cur is None:
            cur = {"unit": "", "title": "", "body": []}
        cur["body"].append(ln)
    if cur:
        units.append(cur)

    out = []
    for u in units:
        body = _tidy("\n".join(u["body"]))
        if not body and not u["title"]:
            continue
        if len(body) <= CHUNK_CHARS * 2 or u["unit"]:
            # статья целиком: дробить её вредно — вопрос обычно про статью, а не про абзац
            for part in _split_long(body):
                out.append({"unit": u["unit"], "title": u["title"], "text": part})
        else:
            for sub in _split_points(body):
                out.append({"unit": sub["unit"], "title": u["title"], "text": sub["text"]})
    # отбрасываем оглавление: в выгрузке lex.uz перед текстом идёт список статей, где под
    # «Статья 816. Последствия неоплаты чека» стоит одна строка «Глава 46. Поручение».
    # Такие куски имеют настоящий заголовок и пустое содержание — они забивали выдачу.
    return [dict(u, text=_tidy(u["text"])) for u in out
            if len(strip_gaps(u["text"])) >= TOC_MIN_CHARS]


_GAPS_RE = re.compile(r"(?:\s*" + GAP + r"\s*)+")


def _tidy(body: str) -> str:
    """Схлопываем подряд идущие разрывы и убираем их с краёв куска."""
    return _GAPS_RE.sub(GAP, (body or "").strip()).strip().strip(GAP).strip()


def _split_long(body: str) -> list:
    """Очень длинная статья → куски по абзацам, чтобы пассаж помещался в ответ."""
    if len(body) <= CHUNK_CHARS * 3:
        return [body]
    parts, buf = [], ""
    for para in body.split("\n"):
        if len(buf) + len(para) > CHUNK_CHARS * 2 and buf:
            parts.append(buf)
            buf = ""
        buf += ("\n" if buf else "") + para
    if buf:
        parts.append(buf)
    return parts


def _split_points(body: str) -> list:
    """Текст без статей — по пунктам «N.»; что не попало в пункт, идёт кусками по размеру."""
    out, cur = [], {"unit": "", "text": ""}
    for ln in body.split("\n"):
        m = POINT_RE.match(ln)
        if m and len(m.group("n")) <= 8:
            if cur["text"].strip():
                out.append(cur)
            cur = {"unit": "п. " + m.group("n"), "text": m.group("rest")}
        else:
            if len(cur["text"]) > CHUNK_CHARS * 2:
                out.append(cur)
                cur = {"unit": cur["unit"], "text": ""}
            cur["text"] += ("\n" if cur["text"] else "") + ln
    if cur["text"].strip():
        out.append(cur)
    return out


# --------------------------------------------------------------------------- #
#  Имя акта и ссылка
# --------------------------------------------------------------------------- #

def _slug(name: str) -> str:
    """Код акта из имени файла: только буквы/цифры, пробелы → подчёркивание. Для группировки uz/ru."""
    base = norm(name)
    base = re.sub(r"\((?:uz|ru|en|узб|рус|англ)\.?\)", " ", base)
    base = re.sub(r"[^0-9a-zа-яё]+", "_", base).strip("_")
    return base[:60] or "act"


def _clean_url(url: str) -> str:
    """Убираем хвостовые знаки препинания, приклеившиеся к адресу при вырезании из текста.

    ВАЖНО: дефис в адресе lex.uz вида https://lex.uz/uz/docs/-180552 НЕ лишний. Проверено
    22.09.2026 обращением к сайту: /uz/docs/-<номер> отдаёт узбекский текст ЛАТИНИЦЕЙ,
    /uz/docs/<номер> — тот же акт КИРИЛЛИЦЕЙ. Обе страницы открываются (HTTP 200).
    В библиотеке лежат латинские выгрузки, поэтому дефис сохраняем — иначе сотрудник
    открывает кириллическую страницу и не находит в ней показанную цитату.
    """
    return (url or "").strip().rstrip(")").rstrip(".")


def _act_name(path: Path) -> str:
    name = path.stem
    return re.sub(r"\s*\((?:uz|ru|en|узб|рус|англ)\.?\)\s*$", "", name, flags=re.IGNORECASE).strip()
