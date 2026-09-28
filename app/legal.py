"""
«ИИ специалист по страхованию INSON»: мгновенные ответы на трёх языках (ru | uz | en).

Зачем: сотрудники задают один и тот же вопрос десятки раз («что если страховая сумма больше
стоимости?», «как выбирают франшизу?»), а ответ у модели занимает секунды и стоит денег. Здесь
ответ собирается из того, что уже лежит в проекте, за десятки миллисекунд:

  0. Роль: помощник отвечает и по практике страхования (андеррайтинг, документы, оценка, убытки),
     и по норме. Практический ответ помечается «практика компании / учебник CII», нормативный —
     цитатой акта. Имя помощника на трёх языках отдаётся в ответах как assistant_name.
  1. FAQ          — docs/Специалист — FAQ.json (старое имя «Юрист — FAQ.json» тоже читается);
  2. индекс      — SQLite FTS5 по library/01_Законодательство/**/*.txt, заметкам docs/*.md
                   и правилам движка (таблица rules);
  3. ИИ          — НЕОБЯЗАТЕЛЬНОЕ улучшение (Gemini или другой провайдер, app/llm): к мгновенному
                   ответу добавляется пересказ строго по найденным пассажам, а если ни FAQ, ни закон
                   вопрос не покрыли — свободный ответ модели с пометкой «ИИ». Системный промпт —
                   роль специалиста плюс правовой блок app/llm_prompts/legal_guard.ru.txt.
                   Нет ключа — ai.status="off", ответ всё равно выдаётся.
  4. lex.uz      — ЖИВОЙ ПОИСК (с 28.09.2026, app/legal_live.py): только если FAQ и индекс нормы не
                   нашли. Ключевые слова вопроса без ПД (только из словаря законодательства) → поиск
                   действующего акта на lex.uz → дословная цитата статьи; акт сохраняется в библиотеку
                   (на сервере с STORAGE_DIR — в STORAGE_DIR/library_live, LIVE_LIB) и в индекс. Поле ответа live: status
                   found | found_base | not_found | unavailable | limit | off | not_needed, подпись
                   label «найдено на lex.uz сейчас». Выключатель — LEX_LIVE=0.
  5. актуальность — у цитат из базы по актам, изменившимся на lex.uz (app/lawwatch: watched_acts
                   «изменился» или правила «требует пересмотра»), поле actuality со ссылкой на
                   действующую редакцию (with_actuality).

Три языка. Файл считается узбекским/английским по пометке в имени («… (uz).txt», «… (узб).txt»,
«… (en).txt»), иначе язык определяется по тексту (app/ingest.detect_language), по умолчанию ru.
Узбекская латиница пишется с разными апострофами (gʻ ‘ ’ ʼ ` '), поэтому и при индексации, и в
запросе апострофы снимаются: «sugʻurta», «sug'urta» и «sugurta» — одно и то же слово.
Если на языке вопроса текста акта в базе нет — отвечаем по-русски и честно пишем об этом в note.

Персональные данные. Текст вопроса НЕ сохраняется: в legal_questions идёт только отпечаток
(sha256), язык, источник ответа, уверенность и время. Этого хватает, чтобы увидеть частые вопросы
без ответа и отдать их юристу на пополнение FAQ.

Подключение (app/main.py): app.include_router(legal.router) — индекс собирается сам при первом
обращении и дособирается по mtime файлов; принудительно — POST /legal/reindex (администратор).
"""
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import db, llm

router = APIRouter()

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library" / "01_Законодательство"
NOTES = ROOT / "docs"
# Акты, найденные живым поиском на lex.uz (app/legal_live.py). На сервере с постоянным диском
# (STORAGE_DIR) — STORAGE_DIR/library_live: library/ в образе Docker пустая и живёт до перезапуска.
# Без STORAGE_DIR — None: акты кладутся в LIB, как при ручной загрузке tools/lex_fetch.py.
LIVE_LIB = (db.DATA_DIR / "library_live") if os.environ.get("STORAGE_DIR") else None
# FAQ переименован 22.09.2026 («ИИ специалист по страхованию»). Старое имя поддерживается:
# на развёрнутом сервере файл мог остаться прежним.
FAQ_FILE_NEW = ROOT / "docs" / "Специалист — FAQ.json"
FAQ_FILE_OLD = ROOT / "docs" / "Юрист — FAQ.json"
FAQ_FILE = FAQ_FILE_NEW if FAQ_FILE_NEW.exists() else FAQ_FILE_OLD

# Как зовут помощника в ответах API (мини-апп показывает это имя пользователю)
ASSISTANT_NAME = {
    "ru": "ИИ специалист по страхованию INSON",
    "uz": "INSON sugʻurta boʻyicha sunʼiy intellekt mutaxassisi",
    "en": "INSON AI insurance specialist",
}
# пометка практического ответа: норма его не покрывает
PRACTICE_NOTE = {
    "ru": "Ответ по практике компании и учебникам CII, а не по норме права.",
    "uz": "Javob huquqiy norma emas, kompaniya amaliyoti va CII darsliklari asosida berildi.",
    "en": "This answer follows company practice and CII textbooks, not a legal rule.",
}
# пометка ответа, собранного моделью
AI_NOTE = {
    "ru": "ИИ: ответ подготовлен моделью, норма его не подтверждает — проверьте у юриста.",
    "uz": "SI: javobni model tayyorladi, norma bilan tasdiqlanmagan — yuristda tekshiring.",
    "en": "AI: drafted by the model and not confirmed by a rule — check with the lawyer.",
}
ACTS_REGISTRY = ROOT / "docs" / "Отслеживаемые акты.json"

LANGS = ("ru", "uz", "en")
DEFAULT_LANG = "ru"
CACHE_TTL_SEC = 3600
CACHE_MAX = 500
MAX_PASSAGES = 5
QUOTE_MAX = 300
AI_TIMEOUT_SEC = 8

# апострофы во всех начертаниях: узбекская латиница пишется то ʻ, то ‘, то обычным '
APOSTROPHES = "'‘’ʻʼʽ′`´"
_APO_RE = re.compile("[" + re.escape(APOSTROPHES) + "]")
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
# «Дополни\xadтельная информация»). Снимаем и в файле, и в цитате — иначе дословность не сходится
ZERO_WIDTH = "﻿​‌‍‎‏­⁠"
_ZW_RE = re.compile("[" + ZERO_WIDTH + "]")

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
GAP = "⁣"          # invisible separator: в текстах актов не встречается

# пустой бланк приложения: строка формы «____ ning», «20__ y. “___” ____», «6-ILOVA ______».
# Заполнять её должен страховщик — как норму такую строку показывать нельзя.
BLANK_RE = re.compile(r"_{3,}")

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

# порог уверенности: ниже — считаем, что нормы по вопросу нет, и так и говорим.
# 0,55 стоял ниже фактического шума: вопрос про срок рассмотрения претензии (такой нормы в
# законодательстве нет) набирал 0,62, а «сколько стоит билет в кино» — 0,67. Порог поднят,
# и одновременно введено правило редкого слова (RARE_SHARE / RARE_PENALTY ниже).
MIN_CONFIDENCE = 0.60

# «редкое слово» вопроса — то, вес которого близок к максимальному: именно оно задаёт тему
# («претензия», «кино», «попугай»). Если его нет ни в одном найденном куске, совпадение идёт
# по общим словам («страхование», «срок») и ответом считаться не может — уверенность делим.
RARE_SHARE = 0.8
RARE_PENALTY = 0.5

# порог темы: если в лучшем куске нет хотя бы 40% значимых слов вопроса — это не ответ,
# а совпадение по общей лексике. Плюс нижний порог самого bm25: кусок, набравший меньше,
# попал в выдачу случайно (вопрос «сколько стоит билет в кино» — 4,6 против 8–20 у настоящих норм)
KEY_SHARE_MIN = 0.40
SCORE_MIN = 6.0
SILENCE_MAX_CONF = 0.3          # «закон молчит» уверенным быть не может

NO_NORM = {
    "ru": "В законодательстве прямой нормы не найдено — смотрите правила страхования компании "
          "и договор; ниже ближайшие по смыслу статьи",
    "uz": "Qonunchilikda toʻgʻridan-toʻgʻri norma topilmadi — kompaniyaning sugʻurta qoidalari "
          "va shartnomaga qarang; quyida maʼno jihatdan eng yaqin moddalar",
    "en": "No direct provision found in the legislation — see the company's insurance rules and "
          "the contract; closest articles below",
}
# тот же ответ, когда ближайших статей показать нечего (вопрос вообще не о страховом праве)
NO_NORM_BARE = {
    "ru": "В законодательстве прямой нормы не найдено — смотрите правила страхования компании "
          "и договор.",
    "uz": "Qonunchilikda toʻgʻridan-toʻgʻri norma topilmadi — kompaniyaning sugʻurta qoidalari "
          "va shartnomaga qarang.",
    "en": "No direct provision found in the legislation — see the company's insurance rules and "
          "the contract.",
}
NO_NORM_NOTE = {
    "ru": "нормы по этому вопросу в базе не найдено — смотрите правила страхования",
    "uz": "bu savol boʻyicha bazada norma topilmadi — sugʻurta qoidalariga qarang",
    "en": "no provision found in the database — see the insurance rules",
}

# норма существует только на другом языке (Положение 3845 — только узбекский текст)
NOTE_ONLY_LANG = {
    "ru": "текст нормы есть только на языке: %s — показан оригинал и ссылка на него",
    "uz": "normaning matni faqat %s tilida mavjud — asl matn va unga havola koʻrsatilgan",
    "en": "the provision exists only in %s — the original text and its link are shown",
}

# --------------------------------------------------------------------------- #
#  Известные молчания закона
# --------------------------------------------------------------------------- #
# Вопросы, на которые закон РУз ответа НЕ даёт: по добровольным видам это отдано правилам
# страхования и договору. Раньше поиск подбирал к ним нормы об ОБЯЗАТЕЛЬНОМ страховании
# (ПКМ 141 об ОСГО, ЗРУ-386 о перевозчике) и выдавал их за ответ. Теперь отвечаем честно
# и показываем ближайшую по смыслу норму.
#   all  — группы слов, из каждой должно встретиться хотя бы одно (сравнение по основам);
#   none — слова, при которых пункт не применяется (вопрос про обязательный вид — там нормы есть);
#   near — запрос, которым ищем ближайшую норму для ссылки.
SILENCE_NOTE = {
    "ru": "законом не установлено — регулируется правилами страхования и договором",
    "uz": "qonun bilan belgilanmagan — sugʻurta qoidalari va shartnoma bilan tartibga solinadi",
    "en": "not set by law — governed by the insurance rules and the contract",
}
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

NOTE_NO_LANG = {
    "uz":"Bu hujjatning oʻzbekcha matni bazada yoʻq — javob rus tilidagi matn asosida.",
    "en": "The Uzbek/English text of this act is not in the database — the answer is based on the Russian text.",
    "ru": "узбекский/английский текст этого акта в базе отсутствует",
}


# --------------------------------------------------------------------------- #
#  Нормализация и язык
# --------------------------------------------------------------------------- #

def fold(s: str) -> str:
    """Для индекса: NFC, апострофы сняты, пробелы схлопнуты. Регистр сохраняем — его складывает сам
    токенайзер unicode61, а вот апостроф он считает разделителем, и «sugʻurta» распалось бы на
    «sug» и «urta». Поэтому апострофы именно снимаются, а не заменяются."""
    s = unicodedata.normalize("NFC", s or "")
    s = _ZW_RE.sub("", s)
    s = _APO_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()


def quote_norm(s: str) -> str:
    """Для сверки дословности: сняты апострофы всех начертаний и невидимые знаки, пробелы
    схлопнуты. Регистр и знаки препинания сохраняются — иначе «дословно» ничего не значит."""
    s = unicodedata.normalize("NFC", s or "")
    s = _ZW_RE.sub("", s)
    s = _APO_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()


def norm(s: str) -> str:
    """То же самое плюс нижний регистр — для сравнения строк в Python."""
    return fold(s).lower()


CHROME = {norm(x) for x in CHROME_RAW}
FOOTER = tuple(norm(x) for x in FOOTER_RAW)


_UZ_MARKERS = {"sugurta", "modda", "boyicha", "qiymat", "summasi", "shartnoma", "tashkiloti",
               "qonun", "band", "oshsa", "toldirish", "nima", "qanday", "uchun", "bilan"}
_EN_MARKERS = {"insurance", "sum", "value", "article", "law", "contract", "policy", "claim",
               "insured", "agent", "premium"}


def detect_lang(text: str) -> str:
    """Язык короткого вопроса. Сначала штатный определитель, потом простая эвристика.

    app/ingest.detect_language рассчитан на документы и на вопросе из пяти слов честно
    возвращает None — поэтому ниже добор по алфавиту и характерным словам.
    """
    t = norm(text)
    if not t:
        return DEFAULT_LANG
    try:
        from .ingest import detect_language
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

    def drop():
        nonlocal dropped
        dropped = True

    def keep(s: str):
        nonlocal dropped
        if dropped and out:
            out.append(GAP)                # разрыв: дальше идёт уже не продолжение предыдущей строки
        dropped = False
        out.append(s)

    for raw in text.splitlines():
        ln = _ZW_RE.sub("", raw).strip()
        if not ln:
            continue
        if skip_left:
            skip_left -= 1
            if "]" in ln:
                skip_left = 0
            drop()
            continue
        low = norm(ln)
        if any(low.startswith(f) for f in FOOTER):
            break                          # подвал сайта — текст акта кончился
        if HEAD_RE.match(ln):
            drop()
            continue                       # шапка нашей выгрузки: «Источник: …», «Загружено: …»
        if any(n in ln for n in NOISE):
            drop()
            continue
        if low in CHROME:
            drop()
            continue                       # кнопка или пункт меню страницы lex.uz
        if ln.startswith("["):
            if "]" not in ln:
                skip_left = RUBRIC_MAX_LINES      # предел, чтобы незакрытая скобка не съела акт
            drop()
            continue
        if ln.startswith("=== стр."):
            drop()
            continue
        if URL_LINE_RE.match(ln) or STAMP_LINE_RE.match(ln) or CELL_LINE_RE.match(ln):
            drop()
            continue                       # адрес страницы, «Дата обновления», ячейка карточки акта
        if NOTE_LINE_RE.match(ln):
            drop()
            continue                       # редакционная справка «Неофициальный перевод. …»
        if len(ln) < 600 and EDIT_NOTE_RE.match(ln):
            drop()
            continue                       # сноска о редакции и номере изменяющего акта — не норма
        if BLANK_RE.search(ln):
            drop()
            continue                       # пустой бланк приложения — не норма
        if imprint_left and IMPRINT_RE.match(ln):
            imprint_left -= 1
            drop()
            continue                       # «г. Ташкент,», «16 апреля 2009 г.,», «№ ЗРУ-210»
        imprint_left = 0
        if len(ln) < 200 and SIGN_RE.search(ln):
            # подпись под актом: дальше идут только выходные данные (город, дата, номер, источник
            # опубликования). Раньше всё это приклеивалось к последней статье и цитировалось
            # как её текст — «27-modda» отдавала «KARIMOV Toshkent sh., 2009-yil 16-aprel…».
            imprint_left = IMPRINT_MAX_LINES
            drop()
            continue
        keep(ln)
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
#  Источники: акты, заметки, правила
# --------------------------------------------------------------------------- #

_SOURCE_RE = re.compile(r"Источник:\s*(https?://\S+)")


def _slug(name: str) -> str:
    """Код акта из имени файла: только буквы/цифры, пробелы → подчёркивание. Для группировки uz/ru."""
    base = norm(name)
    base = re.sub(r"\((?:uz|ru|en|узб|рус|англ)\.?\)", " ", base)
    base = re.sub(r"[^0-9a-zа-яё]+", "_", base).strip("_")
    return base[:60] or "act"


_registry_cache = {"data": None}


def _registry() -> list:
    """docs/Отслеживаемые акты.json — оттуда берём ссылки lex.uz, если их нет в шапке файла."""
    if _registry_cache["data"] is None:
        try:
            data = json.loads(ACTS_REGISTRY.read_text(encoding="utf-8"))
            _registry_cache["data"] = data.get("acts") or []
        except Exception:
            _registry_cache["data"] = []
    return _registry_cache["data"]


def _clean_url(url: str) -> str:
    """Убираем хвостовые знаки препинания, приклеившиеся к адресу при вырезании из текста.

    ВАЖНО: дефис в адресе lex.uz вида https://lex.uz/uz/docs/-180552 НЕ лишний. Проверено
    22.09.2026 обращением к сайту: /uz/docs/-<номер> отдаёт узбекский текст ЛАТИНИЦЕЙ,
    /uz/docs/<номер> — тот же акт КИРИЛЛИЦЕЙ. Обе страницы открываются (HTTP 200).
    В библиотеке лежат латинские выгрузки, поэтому дефис сохраняем — иначе сотрудник
    открывает кириллическую страницу и не находит в ней показанную цитату.
    """
    return (url or "").strip().rstrip(")").rstrip(".")


_LEX_LANG_RE = re.compile(r"(lex\.uz)/(?:(?:ru|uz|en|oz|o'z|uzc)/)?docs/", re.IGNORECASE)


def _lang_url(url: str, language: str) -> str:
    """Ссылка на lex.uz в языке процитированного текста.

    Английские файлы выгружены с адресом вида https://lex.uz/ru/docs/6124730: номер документа
    английский, а раздел сайта русский — по такой ссылке открывается не тот язык, что в цитате.
    Номер документа не трогаем, правим только языковой сегмент пути.
    """
    if not url or "lex.uz" not in url:
        return url
    seg = {"ru": "ru", "uz": "uz", "en": "en"}.get(language, "ru")
    return _LEX_LANG_RE.sub(r"\1/%s/docs/" % seg, url, count=1)


def _url_for(path: Path, head: str, language: str) -> str:
    """Ссылка на источник: сначала шапка файла («Источник: …»), потом реестр отслеживаемых актов."""
    m = _SOURCE_RE.search(head)
    if m:
        return _lang_url(_clean_url(m.group(1)), language)
    fname = norm(path.stem)
    for act in _registry():
        for key in re.findall(r"(?:зру|пкм|уп)[-\s№]*\d+|\b\d{4}\b", norm(act.get("title") or "")):
            if key and key.replace(" ", "") in fname.replace(" ", "").replace("№", ""):
                # из реестра берём готовый адрес нужного языка и язык в пути не правим:
                # у ru и uz версий на lex.uz разные номера документов
                url = (act.get("lex_url_uz") if language == "uz" else None) or act.get("lex_url") or ""
                return _clean_url(url)
    return ""


def _act_name(path: Path) -> str:
    name = path.stem
    return re.sub(r"\s*\((?:uz|ru|en|узб|рус|англ)\.?\)\s*$", "", name, flags=re.IGNORECASE).strip()


SKIP_MARK = "Индексировать: нет"


def skipped(path: Path) -> bool:
    """Файл помечен в шапке «Индексировать: нет» — в индекс не берём.

    Так исключаются полные кодексы, из которых для ответов вырезана нужная глава: иначе на
    вопрос по страхованию поиск выдавал ст. 540 (наём имущества) из файла, названного «глава 52».
    Сам файл остаётся в библиотеке — он нужен для проверки цитат и для других глав.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return SKIP_MARK in f.read(400)
    except OSError:
        return False


def live_lib() -> Path:
    """Куда живой поиск сохраняет найденные акты: постоянный диск или сама библиотека."""
    return LIVE_LIB if LIVE_LIB is not None else LIB


def rel_path(p: Path) -> str:
    """Путь файла для индекса и цитат: внутри проекта — относительный, на постоянном диске
    (STORAGE_DIR вне проекта) — абсолютный. Читатели делают ROOT / путь — это работает для обоих
    (тот же подход, что app/db.stored_path)."""
    p = Path(p)
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def source_files() -> list:
    """Что индексируем: акты библиотеки (норма), акты живого поиска на постоянном диске
    и заметки проекта (пометка «заметка, не норма»)."""
    out = []
    dirs = [LIB]
    if LIVE_LIB is not None and LIVE_LIB != LIB:
        dirs.append(LIVE_LIB)
    for d in dirs:
        if not d.exists():
            continue
        for p in sorted(d.rglob("*.txt")):
            if skipped(p):
                continue
            out.append({"path": p, "kind": "act"})
    if NOTES.exists():
        for p in sorted(NOTES.glob("*.md")):
            out.append({"path": p, "kind": "note"})
    return out


def _file_language(path: Path, text: str) -> str:
    lang = lang_of_name(path.name)
    if lang:
        return lang
    try:
        from .ingest import detect_language
        got = (detect_language(text[:20000]) or {}).get("language")
    except Exception:
        got = None
    if got in ("uz-latn", "uz-cyrl"):
        return "uz"
    if got == "en":
        return "en"
    return DEFAULT_LANG


# --------------------------------------------------------------------------- #
#  Сборка индекса
# --------------------------------------------------------------------------- #

_index_lock = threading.Lock()
_index_ready = {"stamp": None, "checked": 0.0}


def _insert(con, rows_):
    con.executemany(
        "INSERT INTO legal_chunks (act, act_code, language, unit, title, text, raw, url, path, official)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)", rows_)


def _index_file(con, path: Path, kind: str) -> dict:
    raw = path.read_text(encoding="utf-8", errors="replace")
    rel = rel_path(path)
    language = _file_language(path, raw)
    act = _act_name(path)
    code = _slug(path.stem)
    url = _url_for(path, raw[:400], language) if kind == "act" else ""
    # официальным считаем только узбекский текст акта: русские версии на lex.uz помечены
    # «Неофициальный перевод» (см. ГК, ст. 938), английского официального текста нет вовсе
    official = 1 if (kind == "act" and language == "uz") else 0
    if kind == "note":
        act = "Заметка проекта: " + act
    units = split_units(raw, language)
    rows_ = []
    for u in units:
        body = fold(strip_gaps(u["text"]))      # в поиск разрывы не идут, в raw остаются
        if not body:
            continue
        rows_.append((fold(act), code, language, fold(u["unit"]), fold(u["title"]),
                      body, u["text"].strip(), url, rel, official))
    con.execute("DELETE FROM legal_chunks WHERE path=?", (rel,))
    _insert(con, rows_)
    st = path.stat()
    con.execute("DELETE FROM legal_files WHERE path=?", (rel,))
    con.execute("INSERT INTO legal_files (path, mtime, size, language, act, act_code, chunks, indexed_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (rel, st.st_mtime, st.st_size, language, act, code, len(rows_), db.now()))
    return {"path": rel, "chunks": len(rows_), "language": language, "act": act}


def _index_rules(con) -> int:
    """Правила проверок движка: у них есть legal_ref — вопрос «почему система не даёт» тоже юридический."""
    con.execute("DELETE FROM legal_chunks WHERE path=?", ("db:rules",))
    try:
        rs = db.rows(con, "SELECT code, name, severity, legal_ref, description FROM rules")
    except Exception:
        return 0
    rows_ = []
    for r in rs:
        text = f"{r['name']}. {r['description'] or ''} Основание: {r['legal_ref'] or '—'}."
        rows_.append(("Правило проверки INSON", "rule_" + (r["code"] or ""), DEFAULT_LANG,
                      fold(r["code"] or ""), fold(r["name"] or ""), fold(text), text, "",
                      "db:rules", 0))
    _insert(con, rows_)
    return len(rows_)


# версия разбора текста: меняется вместе с правилами очистки и разметки разрывов. Индекс,
# собранный прежней версией, пересобирается сам — иначе в цитатах остаётся старый мусор.
PARSER_VERSION = "2026-09-22.quotes"
PARSER_ROW = "db:parser"


def index_stamp() -> str:
    """Отпечаток состава источников: пути + mtime + размеры. Изменился — индекс пересобирается."""
    parts = [PARSER_VERSION]
    for s in source_files():
        try:
            st = s["path"].stat()
        except OSError:
            continue
        parts.append(f"{s['path'].as_posix()}|{int(st.st_mtime)}|{st.st_size}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _ensure_table(con) -> bool:
    """FTS5-таблицу нельзя доработать ALTER'ом: если состав колонок старый (база собрана прежней
    версией schema.sql) — пересоздаём её целиком и просим полную пересборку."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(legal_chunks)")}
    if cols and "raw" in cols:
        return False
    con.execute("DROP TABLE IF EXISTS legal_chunks")
    con.execute("DELETE FROM legal_files")
    sql = db.SCHEMA.read_text(encoding="utf-8")
    start = sql.index("CREATE VIRTUAL TABLE IF NOT EXISTS legal_chunks")
    con.execute(sql[start:sql.index(");", start) + 2])       # определение берём из schema.sql, не копируем
    return True


def reindex(force: bool = False) -> dict:
    """Пересобирает индекс по изменившимся файлам. Идемпотентно: без изменений — ничего не пишет."""
    t0 = time.time()
    with _index_lock:
        with db.tx() as con:
            try:
                force = _ensure_table(con) or force
            except Exception as e:
                print("legal: таблица индекса не проверена:", e)
            have = {r["path"]: r for r in db.rows(con, "SELECT * FROM legal_files")}
            # индекс собран прежней версией разбора — пересобираем целиком
            if (have.get(PARSER_ROW) or {}).get("act") != PARSER_VERSION:
                force = True
            seen, changed = set(), []
            for s in source_files():
                p = s["path"]
                rel = rel_path(p)
                seen.add(rel)
                try:
                    st = p.stat()
                except OSError:
                    continue
                old = have.get(rel)
                if not force and old and abs(old["mtime"] - st.st_mtime) < 1e-6 and old["size"] == st.st_size:
                    continue
                try:
                    changed.append(_index_file(con, p, s["kind"]))
                except Exception as e:
                    # ошибку не глотаем: файл пропускаем, но в журнал она попадает
                    db.audit(con, "system", "индекс закона: файл не разобран", rel, {"ошибка": str(e)[:300]})
            for gone in set(have) - seen - {PARSER_ROW}:  # файл удалили — убираем и из индекса
                con.execute("DELETE FROM legal_chunks WHERE path=?", (gone,))
                con.execute("DELETE FROM legal_files WHERE path=?", (gone,))
            rules_n = _index_rules(con)
            # отметка версии разбора: по ней следующий запуск поймёт, что индекс свежий
            con.execute("DELETE FROM legal_files WHERE path=?", (PARSER_ROW,))
            con.execute("INSERT INTO legal_files (path, mtime, size, language, act, act_code,"
                        " chunks, indexed_at) VALUES (?,?,?,?,?,?,?,?)",
                        (PARSER_ROW, 0.0, 0, "", PARSER_VERSION, "parser", 0, db.now()))
            total = con.execute("SELECT COUNT(*) FROM legal_chunks").fetchone()[0]
        _index_ready["stamp"] = index_stamp()
        _cache.clear()
        _df_cache["n"].clear()
        _df_cache["df"].clear()            # веса слов считались по старому составу индекса
    return {"files_changed": len(changed), "files_total": len(seen), "rules": rules_n,
            "chunks": total, "took_ms": int((time.time() - t0) * 1000), "changed": changed[:20]}


STAMP_EVERY_SEC = 10          # чаще сверять отпечаток смысла нет: это сотня обращений к диску


def ensure_index() -> None:
    """Ленивая сборка: первый вопрос собирает индекс, дальше не чаще раза в 10 с сверяется отпечаток."""
    if _index_ready["stamp"] and time.time() - _index_ready.get("checked", 0) < STAMP_EVERY_SEC:
        return
    _index_ready["checked"] = time.time()
    if _index_ready["stamp"] == index_stamp():
        return
    reindex()


# --------------------------------------------------------------------------- #
#  FAQ юриста
# --------------------------------------------------------------------------- #

_faq_cache = {"mtime": None, "items": [], "version": None}


def faq_items() -> list:
    """docs/Специалист — FAQ.json (старое имя «Юрист — FAQ.json» тоже подходит)."""
    global FAQ_FILE
    if not FAQ_FILE.exists():                     # файл могли переименовать на работающем сервере
        FAQ_FILE = FAQ_FILE_NEW if FAQ_FILE_NEW.exists() else FAQ_FILE_OLD
    try:
        st = FAQ_FILE.stat()
    except OSError:
        _faq_cache.update({"mtime": None, "items": [], "version": None})
        return []
    if _faq_cache["mtime"] == st.st_mtime:
        return _faq_cache["items"]
    try:
        data = json.loads(FAQ_FILE.read_text(encoding="utf-8"))
        items = [i for i in (data.get("items") or []) if isinstance(i, dict) and i.get("id")]
        _faq_cache.update({"mtime": st.st_mtime, "items": items, "version": data.get("version")})
    except Exception as e:
        print("legal: FAQ не прочитан:", e)
        _faq_cache.update({"mtime": st.st_mtime, "items": [], "version": None})
    return _faq_cache["items"]


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
SYN_OF = {}
for _i, _grp in enumerate(SYNONYMS):
    for _w in _grp:
        SYN_OF.setdefault(_w, set()).add(_i)

PREFIX_MIN = 5          # общая основа в пять знаков: «максимальный» и «максимум» — одно слово
FAQ_MIN_SCORE = 0.6


def _groups(word: str) -> set:
    """Номера синонимических групп слова (по самому слову и по его основе)."""
    out = set(SYN_OF.get(word, ()))
    for w, g in SYN_OF.items():
        if _common_prefix(w, word) >= PREFIX_MIN:
            out |= g
    return out


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _hay_tokens(text: str) -> list:
    return _TOKEN_RE.findall(text)


def _term_hit(term: str, tokens: list, groups: set) -> bool:
    """Слово вопроса найдено в вопросе FAQ: тот же корень или синоним.

    Короткие токены («ст», «ли», «в») в сравнении не участвуют: раньше «ст» из тега «ст. 934»
    совпадало с любым словом на «ст-» («ставка», «страхование»), и вопрос про ставку получал
    ответ про дату определения стоимости.
    """
    for tk in tokens:
        if len(tk) < 4:
            # трёхбуквенные аббревиатуры (PML, EML, MFL, БРВ) ловим только полным совпадением:
            # по началу слова они по-прежнему не сравниваются
            if len(tk) == 3 and tk == term:
                return True
            continue
        n = _common_prefix(tk, term)
        if n >= PREFIX_MIN or (n >= 4 and n >= min(len(tk), len(term)) - 1):
            return True
        if groups and groups & SYN_OF.get(tk, set()):
            return True
    return False


def _faq_haystack(item: dict, lang: str) -> str:
    q = item.get("q") or {}
    tags = " ".join(str(t) for t in (item.get("tags") or []))
    return norm(" ".join([str(q.get(lang) or ""), str(q.get(DEFAULT_LANG) or ""), tags,
                          str(item.get("id") or "")]))


def _terms(text: str, lang: str) -> list:
    stop = STOP.get(lang, set()) | ALL_STOP
    out = []
    for tk in _TOKEN_RE.findall(norm(text)):
        if len(tk) < 3 or tk in stop:
            continue
        if tk not in out:
            out.append(tk)
    return out[:12]


def faq_match(question: str, lang: str) -> tuple:
    """Лучший пункт FAQ и уверенность 0..1. Считаем долю слов вопроса, найденных в q + tags."""
    items = faq_items()
    if not items:
        return None, 0.0
    terms = _terms(question, lang)
    if not terms:
        return None, 0.0
    groups = [_groups(t) for t in terms]
    best, best_score = None, 0.0
    for item in items:
        tokens = _hay_tokens(_faq_haystack(item, lang))
        hit = sum(1 for t, g in zip(terms, groups) if _term_hit(t, tokens, g))
        score = hit / len(terms)
        if score > best_score:
            best, best_score = item, score
    return (best, round(best_score, 3)) if best_score >= FAQ_MIN_SCORE else (None, round(best_score, 3))


def silence_match(question: str, lang: str) -> Optional[dict]:
    """Известное молчание закона: вопрос из списка SILENCES. Сравнение по основам слов.

    Проверяется ДО поиска по актам (но после FAQ юриста): иначе поиск подбирает к такому
    вопросу норму об обязательном виде страхования и выдаёт её за ответ.
    """
    q = norm(question)
    if not q:
        return None
    for item in SILENCES:
        if any(bad in q for bad in item.get("none") or ()):
            continue
        if all(any(w in q for w in group) for group in item["all"]):
            return item
    return None


def silence_answer(item: dict, lang: str) -> tuple:
    """Ответ по известному молчанию: текст + ближайшая норма как «ближайшая» цитата."""
    text, _ = _pick(item.get("a") or {}, lang)
    near_q, _ = _pick(item.get("near") or {}, lang)
    citations = []
    if near_q:
        stems = stems_of(near_q, lang)
        # ближайшую норму показываем только если она действительно по теме: иначе к узбекскому
        # вопросу подставлялся случайный пункт Положения 1882 на кириллице
        found = [r for r in search(near_q, lang, limit=3) if _on_topic(r, stems)][:2]
        citations = [dict(_citation(r, lang, stems), closest=True) for r in found]
    return text or NO_NORM_BARE[lang], citations


# --------------------------------------------------------------------------- #
#  Поиск по индексу
# --------------------------------------------------------------------------- #

# предел длины основы: русский склоняется окончаниями (основа длинная), узбекский лепит суффиксы
# один за другим (qiymat → qiymatidan → qiymatdan), поэтому основу берём короче
STEM_CAP = {"ru": 8, "uz": 6, "en": 8}


def stem(t: str, lang: str = DEFAULT_LANG) -> str:
    """Грубая основа слова вместо морфологии: «страховая» → «страхов», «qiymatdan» → «qiymat».

    Словарей склонения в стандартной библиотеке нет, а «сумма»* не нашло бы «суммы».
    Отрезаем два последних знака и ограничиваем длину по языку.
    """
    return t[:max(4, min(len(t) - 2, STEM_CAP.get(lang, 8)))]


def stems_of(question: str, lang: str) -> list:
    return [stem(t, lang) for t in _terms(question, lang)]


def match_query(question: str, lang: str) -> str:
    """Запрос FTS5: основы слов через OR (одного совпадения достаточно, точность даёт пересчёт ниже)."""
    return " OR ".join('"%s"*' % s for s in stems_of(question, lang))


# веса bm25 по колонкам таблицы: act, act_code, language, unit, title, text, raw, url, path, official
BM25 = "bm25(legal_chunks, 1.0, 0.0, 0.0, 0.5, 4.0, 1.0, 0.0, 0.0, 0.0, 0.0)"

SEARCH_SQL = f"""
SELECT act, act_code, language, unit, title, url, path, official,
       snippet(legal_chunks, 5, '[', ']', ' … ', 24) AS snip,
       raw AS body,
       text AS folded,
       {BM25} AS score
  FROM legal_chunks
 WHERE legal_chunks MATCH ? AND language = ?
 ORDER BY score
 LIMIT ?
"""

CANDIDATES = 40


# вес слова: редкое слово вопроса («попугай», «крыша») решает, есть ли вообще норма по теме,
# частое («страховой», «договор») встречается в половине базы и о совпадении темы не говорит
_df_cache = {"n": {}, "df": {}}


def _doc_freq(con, stem_: str, lang: str) -> int:
    key = (lang, stem_)
    if key in _df_cache["df"]:
        return _df_cache["df"][key]
    try:
        n = con.execute("SELECT COUNT(*) FROM legal_chunks WHERE legal_chunks MATCH ?"
                        " AND language = ?", ('"%s"*' % stem_, lang)).fetchone()[0]
    except Exception:
        n = 0
    _df_cache["df"][key] = n
    return n


def _lang_total(con, lang: str) -> int:
    if lang not in _df_cache["n"]:
        try:
            _df_cache["n"][lang] = con.execute(
                "SELECT COUNT(*) FROM legal_chunks WHERE language = ?", (lang,)).fetchone()[0] or 1
        except Exception:
            _df_cache["n"][lang] = 1
    return _df_cache["n"][lang]


def weights_of(con, stems: list, lang: str) -> dict:
    """Вес каждой основы: log(N / df). Считается один раз на слово и кэшируется до пересборки."""
    import math
    total = _lang_total(con, lang)
    out = {}
    top = math.log(max(2, total))
    for s in stems:
        df = _doc_freq(con, s, lang)
        # слова, которого в базе нет вовсе («попугай», «крыша»), — самый тяжёлый вес: без него
        # совпадение по общим словам не должно выглядеть уверенным ответом
        out[s] = top if not df else max(0.15, math.log(total / df))
    return out


def _coverage_w(r: dict, weights: dict) -> float:
    """Доля ВЕСА слов вопроса, найденного в куске. Три общих слова из пяти больше не дают 0,6:
    если редкое слово темы в норме не встретилось, уверенность падает и ответ честно считается
    ненайденным."""
    if not weights:
        return 0.0
    hay = ((r.get("title") or "") + " " + (r.get("folded") or "")).lower()
    hit = sum(w for s, w in weights.items() if s in hay)
    return hit / sum(weights.values())


def _coverage(r: dict, stems: list) -> float:
    """Сколько РАЗНЫХ слов вопроса встретилось в куске. bm25 этого не умеет: он вознаграждает
    многократное повторение одного слова, из-за чего «стоимость, стоимость, стоимость» обгоняло
    статью, где есть и «страховая сумма», и «страховая стоимость»."""
    hay = (r["title"] + " " + (r["folded"] or "")).lower()
    return sum(1 for s in stems if s in hay) / max(1, len(stems))


def _on_topic(r: dict, stems: list) -> bool:
    """Порог релевантности: в куске есть хотя бы KEY_SHARE_MIN значимых слов вопроса и сам
    bm25 не ниже SCORE_MIN. Иначе кусок попал в выдачу по общей лексике, а не по теме."""
    if not r:
        return False
    return _coverage(r, list(stems)) >= KEY_SHARE_MIN and -r.get("score", 0.0) >= SCORE_MIN


def search(question: str, lang: str, limit: int = MAX_PASSAGES) -> list:
    stems = stems_of(question, lang)
    q = match_query(question, lang)
    if not q:
        return []
    with db.tx() as con:
        try:
            rs = db.rows(con, SEARCH_SQL, q, lang, CANDIDATES)
        except Exception as e:
            print("legal: поиск не выполнен:", e)
            return []
        weights = weights_of(con, stems, lang)
    rs = [dict(r) for r in rs]
    worst = max([-r["score"] for r in rs] or [1.0]) or 1.0
    top_w = max(weights.values()) if weights else 0.0
    rare = {s for s, w in weights.items() if top_w and w >= RARE_SHARE * top_w}
    scored = []
    for r in rs:
        hay_all = ((r["title"] or "") + " " + (r["folded"] or "")).lower()
        # каких редких слов темы в куске нет — по ним ask() решает, ответ это или совпадение
        # по общим словам
        r["rare_missing"] = sorted(s for s in rare if s not in hay_all)
        r["coverage"] = _coverage(r, stems)
        r["coverage_w"] = _coverage_w(r, weights)
        title = (r["title"] or "").lower()
        in_title = sum(weights.get(s, 1.0) for s in stems if s in title) / max(1e-9, sum(weights.values()))
        # норма важнее заметки проекта и правила движка: заметка не источник права
        weight = 1.0 if r["path"].startswith("library/") else 0.8
        # решает вес найденных слов (редкое слово темы важнее общих), bm25 — только уточняет
        # порядок внутри; попадание в заголовок статьи ценится отдельно: он и есть тема вопроса
        r["rank"] = (0.6 * r["coverage_w"] + 0.25 * in_title + 0.15 * (-r["score"] / worst)) * weight
        scored.append(r)
    scored.sort(key=lambda x: -x["rank"])
    out, seen = [], set()
    for r in scored:
        # один и тот же акт лежит в библиотеке дважды (отдельная глава и документ целиком)
        key = (r["unit"], norm(r["body"])[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= limit:
            break
    return out


SENT_RE = re.compile(r"(?<=[.!?;])\s+")


def _first_sentences(text: str, n: int = 2, limit: int = 400) -> str:
    sents = [s.strip() for s in SENT_RE.split((text or "").strip()) if s.strip()]
    return _clip(" ".join(sents[:n]), limit)


def _clip(out: str, limit: int) -> str:
    """Обрезаем по границе слова: цитата не должна обрываться на половине слова."""
    out = _WS_RE.sub(" ", (out or "").strip())
    if len(out) <= limit:
        return out
    cut = out[:limit]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > limit // 2 else cut).rstrip(" ,;:-") + "…"


def _best_sentences(text: str, stems: list, n: int = 2, limit: int = QUOTE_MAX) -> str:
    """Цитата — предложение, где действительно встретились слова вопроса, а не первое в куске.

    В акте первым предложением часто идёт служебная строка («Глава 46. Поручение»,
    «Oldingi tahrirga qarang») — цитировать её бессмысленно.

    Цитата берётся внутри ОДНОГО непрерывного куска (см. segments): фраза, склеенная через
    выброшенный служебный мусор, в самом акте не встречается и цитатой быть не может.
    """
    best_seg, best_i, best = None, 0, -1
    for seg in segments(text) or [(text or "").strip()]:
        # короткие «предложения» («4-боб.», «16.») из списка НЕ выбрасываем: без них соседние
        # фразы склеивались в цитату, которой в акте нет. Они лишь не годятся как начало цитаты.
        sents = [s.strip() for s in SENT_RE.split(seg) if s.strip()]
        if not sents:
            continue
        for i, s in enumerate(sents):
            if len(s) <= 15:
                continue
            low = s.lower()
            hit = sum(1 for st in stems if st in low)
            if hit > best:
                best_seg, best_i, best = sents, i, hit
    if not best_seg:
        first = (segments(text) or [(text or "").strip()])[0]
        return _clip(_first_sentences(first, n, limit), limit)
    if best <= 0:
        best_i = 0
    return _clip(" ".join(best_seg[best_i:best_i + n]), limit)


def _cap(s: str) -> str:
    return (s[:1].upper() + s[1:]) if s else s


def _unit_of(r: dict) -> str:
    return r.get("unit") or r.get("title") or ""


def summarize_passages(passages: list, lang: str, stems: list = ()) -> str:
    """Резюме из предложений найденных пассажей. Ничего не сочиняем — только цитируем с указанием статьи."""
    parts = []
    for r in passages[:2]:
        head = ", ".join(x for x in (_cap(r["act"]), _unit_of(r)) if x)
        body = _best_sentences(r["body"], list(stems), 2, 400)
        if body:
            parts.append(f"{head}: {_cap(body)}")
    if not parts:
        return {"ru": "В базе нет нормы, прямо отвечающей на этот вопрос.",
                "uz": "Bazada bu savolga bevosita javob beradigan norma yoʻq.",
                "en": "No provision in the database answers this question directly."}[lang]
    return " ".join(parts)


# тексты файлов для сверки дословности: файлов десятки, каждый до полумегабайта — держим
# последние несколько и сверяем mtime, чтобы не читать диск на каждый вопрос
_TEXT_CACHE_MAX = 8
_text_cache = {}
_text_lock = threading.Lock()


def file_text(rel: str) -> str:
    """Нормализованный текст исходного файла акта (апострофы и пробелы), пустая строка — нет файла."""
    if not rel or rel.startswith("db:"):
        return ""
    p = ROOT / rel
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return ""
    with _text_lock:
        hit = _text_cache.get(rel)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        txt = quote_norm(p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        txt = ""
    with _text_lock:
        if len(_text_cache) >= _TEXT_CACHE_MAX:
            _text_cache.clear()
        _text_cache[rel] = (mtime, txt)
    return txt


def quote_core(quote: str) -> str:
    """Цитата без нашего многоточия обрезки — именно её ищем в исходном файле."""
    return (quote or "").rstrip("…").strip()


def verbatim(rel: str, quote: str) -> bool:
    """Цитата дословно встречается в исходном файле (с точностью до апострофов и пробелов).

    Правила движка (path='db:rules') и заметки проекта не акты — их не сверяем.
    """
    core = quote_core(quote)
    if len(core) < 20:
        return False
    text = file_text(rel)
    if not text:
        return True                      # файла нет (правило движка) — сверять нечего
    return quote_norm(core) in text


def _citation(r: dict, lang: str, stems: list = ()) -> dict:
    """Цитата из пассажа. Не дословную не отдаём: лучше без текста, чем выдуманная норма."""
    quote = _best_sentences(r["body"], list(stems), 2, QUOTE_MAX)
    rel = r.get("path") or ""
    if not verbatim(rel, quote):
        # длинная склейка не сошлась — пробуем одно первое предложение лучшего куска
        quote = _best_sentences(r["body"], list(stems), 1, QUOTE_MAX)
        if not verbatim(rel, quote):
            quote = ""
    return {"act": _cap(r["act"]), "unit": _unit_of(r), "quote": quote,
            "url": _clean_url(r["url"]) or None, "language": r["language"],
            "official": bool(r["official"]), "closest": False}


# --------------------------------------------------------------------------- #
#  Ответ из FAQ
# --------------------------------------------------------------------------- #

def _pick(d, lang: str):
    """Значение на языке вопроса; нет — русское. Возвращает (текст, был ли откат на ru)."""
    if not isinstance(d, dict):
        return (d, False) if d else (None, False)
    v = d.get(lang)
    if v:
        return v, False
    return d.get(DEFAULT_LANG), bool(d.get(DEFAULT_LANG)) and lang != DEFAULT_LANG


def _pick_any(d, lang: str) -> tuple:
    """Значение на языке вопроса; нет — по порядку ru, uz, en. Возвращает (значение, язык).

    Нужно там, где норма есть только на одном языке: у Положения 3845 текст только узбекский,
    и без этого отката сотрудник видел ответ вовсе без цитаты и без ссылки — проверить норму
    было нельзя. Лучше показать узбекский оригинал с пометкой, чем пустое место.
    """
    if not isinstance(d, dict):
        return (d, lang) if d else (None, None)
    for cand in (lang, DEFAULT_LANG, "uz", "en"):
        if d.get(cand):
            return d[cand], cand
    return None, None


def faq_answer(item: dict, lang: str, confidence: float) -> dict:
    text, fell_back = _pick(item.get("a") or {}, lang)
    citations = []
    for c in item.get("citations") or []:
        quote, q_lang = _pick_any(c.get("quote") or {}, lang)
        url, u_lang = _pick_any(c.get("url") or {}, lang)
        q_fb = bool(quote) and q_lang != lang
        official = (c.get("official") or {})
        official = bool(official.get(q_lang or lang)) if isinstance(official, dict) else bool(official)
        citations.append({"act": c.get("act") or "", "unit": c.get("article") or "",
                          "quote": (quote or "")[:QUOTE_MAX], "url": _clean_url(url) or None,
                          "language": q_lang or lang, "official": official})
        fell_back = fell_back or q_fb
    note = None
    if fell_back:
        note = NOTE_NO_LANG.get(lang) if lang != DEFAULT_LANG else None
    # цитата показана не на языке вопроса — честно говорим, на каком она
    other = {c["language"] for c in citations if c["quote"]} - {lang}
    if other and not note:
        note = NOTE_ONLY_LANG.get(lang, NOTE_ONLY_LANG[DEFAULT_LANG]) % ", ".join(sorted(other))
    if lang == "en" and not any(c["official"] for c in citations):
        note = (note + " " if note else "") + "unofficial: no official English text of the act exists"
    # практический вопрос (андеррайтинг, документы, оценка, убытки): нормы нет, есть заметка проекта
    if (item.get("kind") == "практика") or (not citations and item.get("basis")):
        basis, _ = _pick_any(item.get("basis") or {}, lang)
        mark = PRACTICE_NOTE.get(lang) or PRACTICE_NOTE[DEFAULT_LANG]
        note = (note + " " if note else "") + mark + (f" ({basis})" if basis else "")
    return {"text": text or "", "citations": citations, "note": note,
            "confidence": confidence, "kind": item.get("kind") or "норма",
            "basis": _pick_any(item.get("basis") or {}, lang)[0]}


# --------------------------------------------------------------------------- #
#  ИИ — необязательное улучшение
# --------------------------------------------------------------------------- #

GUARD_FILE = ROOT / "app" / "llm_prompts" / "legal_guard.ru.txt"
SYSTEM_FILE = ROOT / "app" / "llm_prompts" / "system.json"
_guard_cache = {"mtime": None, "text": ""}

# Роль помощника. Правовой блок (legal_guard.ru.txt) подклеивается к ней целиком: запреты
# «не сочинять нормы», «не обещать выплату», «не толковать договор» действуют и здесь.
AI_ROLE = ("Ты «ИИ специалист по страхованию INSON» — помощник сотрудников страховой организации "
           "в Узбекистане. Ты отвечаешь и на вопросы практики (андеррайтинг, документы, оценка, "
           "убытки), и на правовые вопросы. Практику объясняй просто и по делу; норму — только со "
           "ссылкой на акт, статью и пункт. Ответ — 2–5 предложений. Язык ответа строго: %s.")

AI_SYSTEM = AI_ROLE + ("\nОтвечай ТОЛЬКО по приведённым ниже пассажам. Ничего не добавляй от себя: "
                       "если в пассажах ответа нет — так и напиши. Цитируй номера статей и пунктов.")

# Нормы в пассажах нет: модель отвечает по общей практике страхования и обязана это пометить
AI_SYSTEM_FREE = AI_ROLE + ("\nНормы по этому вопросу тебе не передали. Отвечай по общей практике "
                            "страхования и учебникам, НЕ ссылайся на конкретные статьи и пункты и не "
                            "называй номера актов. Если вопрос требует нормы — скажи, что нужен юрист.")


def guard_text() -> str:
    """Правовой блок системного промпта (app/llm_prompts/legal_guard.ru.txt). Файла нет — работаем без него."""
    try:
        st = GUARD_FILE.stat()
    except OSError:
        return ""
    if _guard_cache["mtime"] != st.st_mtime:
        try:
            _guard_cache.update({"mtime": st.st_mtime,
                                 "text": GUARD_FILE.read_text(encoding="utf-8")})
        except Exception as e:
            print("legal: правовой блок промпта не прочитан:", e)
            _guard_cache.update({"mtime": st.st_mtime, "text": ""})
    return _guard_cache["text"]


def system_prompt(lang: str, free: bool = False) -> str:
    """Системный промпт «ИИ специалиста»: роль + правовой блок юриста."""
    base = (AI_SYSTEM_FREE if free else AI_SYSTEM) % lang
    guard = guard_text()
    return base + ("\n\n" + guard if guard else "")


def ai_answer(question: str, passages: list, lang: str) -> dict:
    """Пересказ по найденным пассажам. Нет ключа — ai.status='off', мгновенный ответ уже отдан."""
    if not llm.enabled():
        return {"status": "off", "text": None}
    body = "\n\n".join(f"[{_cap(r['act'])} {_unit_of(r)}]\n{r['body'][:1200]}" for r in passages[:MAX_PASSAGES])
    if not body:
        return {"status": "off", "text": None}
    old = getattr(llm, "TIMEOUT_SEC", None)
    try:
        llm.TIMEOUT_SEC = AI_TIMEOUT_SEC            # ответ по норме ждать дольше 8 с нет смысла
        text = llm.chat("вопрос специалисту по страхованию", system_prompt(lang),
                        f"Вопрос: {question}\n\nПассажи:\n{body}", max_tokens=400)
    except Exception as e:
        return {"status": "error", "text": None, "reason": str(e)[:200]}
    finally:
        if old is not None:
            llm.TIMEOUT_SEC = old
    if not text:
        return {"status": "error", "text": None, "reason": (llm.last_error or {}).get("text")}
    return {"status": "ok", "text": text.strip()}


def ai_free_answer(question: str, lang: str) -> dict:
    """Ни FAQ, ни закон вопрос не покрыли: отвечает модель, ответ помечается «ИИ»."""
    if not llm.enabled():
        return {"status": "off", "text": None}
    old = getattr(llm, "TIMEOUT_SEC", None)
    try:
        llm.TIMEOUT_SEC = AI_TIMEOUT_SEC
        text = llm.chat("вопрос специалисту по страхованию (без нормы)",
                        system_prompt(lang, free=True), f"Вопрос: {question}", max_tokens=400)
    except Exception as e:
        return {"status": "error", "text": None, "reason": str(e)[:200]}
    finally:
        if old is not None:
            llm.TIMEOUT_SEC = old
    if not text:
        return {"status": "error", "text": None, "reason": (llm.last_error or {}).get("text")}
    return {"status": "ok", "text": text.strip(), "source": "ai",
            "note": AI_NOTE.get(lang) or AI_NOTE[DEFAULT_LANG]}


# --------------------------------------------------------------------------- #
#  Кэш и журнал
# --------------------------------------------------------------------------- #

_cache = {}
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        if time.time() - hit[0] > CACHE_TTL_SEC:
            _cache.pop(key, None)
            return None
        return hit[1]


def _cache_put(key, value):
    with _cache_lock:
        if len(_cache) >= CACHE_MAX:
            _cache.clear()
        _cache[key] = (time.time(), value)


def q_hash(question: str, lang: str) -> str:
    """Отпечаток вопроса вместо текста: в вопросе может быть ФИО или ИНН — в базу их не кладём."""
    return hashlib.sha256(norm(question).encode("utf-8")).hexdigest()[:32]


def log_question(question: str, lang: str, source: str, confidence: float, took_ms: int, found: bool):
    try:
        with db.tx() as con:
            con.execute("INSERT INTO legal_questions (created_at, lang, q_hash, source, confidence,"
                        " took_ms, found) VALUES (?,?,?,?,?,?,?)",
                        (db.now(), lang, q_hash(question, lang), source, confidence, took_ms, int(found)))
    except Exception as e:
        print("legal: вопрос не записан в журнал:", e)


# --------------------------------------------------------------------------- #
#  Главная логика
# --------------------------------------------------------------------------- #

def related(lang: str, limit: int = 6) -> list:
    out = []
    for item in faq_items()[:50]:
        q, _ = _pick(item.get("q") or {}, lang)
        if q:
            out.append({"id": item["id"], "q": q})
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
#  Актуальность цитаты: данные слежения за законодательством (app/lawwatch.py)
# --------------------------------------------------------------------------- #

ACTUALITY_TEXT = {
    "ru": "Акт изменился на lex.uz%s — цитата из базы может быть из прежней редакции. "
          "Сверьте с действующей редакцией: %s",
    "uz": "Hujjat lex.uz saytida oʻzgargan%s — bazadagi iqtibos avvalgi tahrirdan boʻlishi mumkin. "
          "Amaldagi tahrir bilan solishtiring: %s",
    "en": "The act has changed on lex.uz%s — the quote from the database may be from the previous "
          "version. Check the current version: %s",
}
ACTUALITY_SINCE = {"ru": " (редакция от %s)", "uz": " (%s tahriri)", "en": " (version of %s)"}
ACTUALITY_NOTE = {
    "ru": "есть цитаты из актов, изменившихся на lex.uz: правила по ним требуют пересмотра юристом",
    "uz": "lex.uz da oʻzgargan hujjatlardan iqtiboslar bor: ular boʻyicha qoidalar yurist tomonidan "
          "qayta koʻrib chiqilishi kerak",
    "en": "some quotes come from acts that have changed on lex.uz: the related rules need the "
          "lawyer's review",
}
ST_REVIEW = "требует пересмотра"
ST_CHANGED = "изменился"                      # то же значение, что lawwatch.ST_CHANGED


def _doc_num(url: str) -> str:
    m = re.search(r"/docs/-?(\d+)", url or "")
    return m.group(1) if m else ""


def _changed_acts() -> list:
    """Отслеживаемые акты, изменившиеся на lex.uz или с правилами «требует пересмотра».

    Данные — таблицы watched_acts и rules, которые ведёт app/lawwatch.py. Нет таблиц — пусто.
    """
    try:
        with db.tx() as con:
            acts = db.rows(con, "SELECT code, title, kind, status, lex_url, redaction, last_changed_at,"
                                " rules_refs FROM watched_acts")
            review = {r["code"] for r in db.rows(con, "SELECT code FROM rules WHERE review_status=?",
                                                 ST_REVIEW)}
    except Exception:
        return []
    reg = {a.get("code"): a for a in _registry()}
    out = []
    for a in acts:
        try:
            refs = json.loads(a["rules_refs"] or "[]")
        except Exception:
            refs = []
        flagged = [r for r in refs if r in review]
        if a["status"] != ST_CHANGED and not flagged:
            continue
        ra = reg.get(a["code"]) or {}
        ids = {_doc_num(u) for u in (a["lex_url"], ra.get("lex_url"), ra.get("lex_url_uz")) if _doc_num(u)}
        # коды вида «зру-730», «пкм № 141»; четырёхзначный номер — только у положений (рег. № 1806),
        # иначе год в названии («от 23.11.2021») совпал бы с любым актом того же года
        t = norm(a["title"] or "")
        keys = [k.replace(" ", "").replace("№", "") for k in re.findall(r"(?:зру|пкм|уп|пп)[-\s№]*\d+", t)]
        if t.startswith("положение"):
            keys += [k for k in re.findall(r"\b\d{4}\b", t) if not k.startswith(("19", "20"))]
        out.append({"code": a["code"], "title": a["title"], "status": ST_CHANGED if a["status"] == ST_CHANGED
                    else ST_REVIEW, "rules": flagged, "redaction": a["redaction"],
                    "since": a["last_changed_at"], "ids": ids, "keys": keys,
                    "url_ru": a["lex_url"] or ra.get("lex_url"), "url_uz": ra.get("lex_url_uz")})
    return out


def _actuality_for(c: dict, changed: list, lang: str) -> Optional[dict]:
    num = _doc_num(c.get("url") or "")
    name = norm(c.get("act") or "").replace(" ", "").replace("№", "")
    for a in changed:
        if (num and num in a["ids"]) or any(k and k in name for k in a["keys"]):
            url = (a["url_uz"] if c.get("language") == "uz" else None) or a["url_ru"] or c.get("url")
            since = (ACTUALITY_SINCE.get(lang) or ACTUALITY_SINCE["ru"]) % a["redaction"] if a["redaction"] else ""
            text = (ACTUALITY_TEXT.get(lang) or ACTUALITY_TEXT["ru"]) % (since, url)
            return {"status": a["status"], "act_code": a["code"], "text": text, "url": url,
                    "redaction": a["redaction"], "changed_at": a["since"], "rules": a["rules"]}
    return None


def with_actuality(out: dict) -> dict:
    """Копия ответа, где у цитат из локальной базы по изменившимся актам есть пометка actuality.

    Цитаты, только что найденные на lex.uz (live), и так из действующей редакции — их не трогаем.
    Кэш ответа не портим: цитаты копируются.
    """
    out = dict(out)
    cits = [dict(c) for c in out.get("citations") or []]
    changed = _changed_acts() if cits else []
    lang = out.get("lang") or DEFAULT_LANG
    hit = False
    for c in cits:
        c.pop("actuality", None)
        if c.get("live") or not changed:
            continue
        a = _actuality_for(c, changed, lang)
        if a:
            c["actuality"] = a
            hit = True
    out["citations"] = cits
    if hit:
        mark = ACTUALITY_NOTE.get(lang) or ACTUALITY_NOTE[DEFAULT_LANG]
        note = out.get("note") or ""
        if mark not in note:
            out["note"] = (note + "; " if note else "") + mark
    return out


# --------------------------------------------------------------------------- #
#  Живой поиск на lex.uz (app/legal_live.py)
# --------------------------------------------------------------------------- #

LIVE_NOT_NEEDED = {"status": "not_needed", "source": "lex.uz"}


def _live(question: str, lang: str, who: str = None) -> dict:
    try:
        from . import legal_live
        return legal_live.lookup(question, lang, who=who)
    except Exception as e:                     # живой поиск не должен ронять ответ по базе
        print("legal: живой поиск не выполнен:", e)
        return {"status": "error", "source": "lex.uz", "reason": str(e)[:200]}


def _live_public(live: dict) -> dict:
    """Поле live ответа: без внутренних пассажей."""
    return {k: v for k, v in (live or {}).items() if k != "passages"}


def _live_enabled() -> bool:
    try:
        from . import legal_live
        return legal_live.enabled()
    except Exception:
        return False


def ask(question: str, lang: str = None, with_ai: bool = False, who: str = None) -> dict:
    """who — кто спрашивает («u:<id>», «g:<guest_id>», «ip:<адрес>»): только для личного предела
    живого поиска на lex.uz; в журнал и в ответ не попадает."""
    return with_actuality(_ask(question, lang, with_ai, who))


def _ask(question: str, lang: str = None, with_ai: bool = False, who: str = None) -> dict:
    t0 = time.time()
    question = (question or "").strip()
    if not question:
        raise HTTPException(422, "Вопрос пустой")
    lang = lang if lang in LANGS else detect_lang(question)
    ensure_index()

    key = (norm(question), lang, bool(with_ai), _live_enabled())
    cached = _cache_get(key)
    if cached:
        out = dict(cached)
        out["took_ms"] = int((time.time() - t0) * 1000)
        out["cached"] = True
        return out

    note = None
    item, conf = faq_match(question, lang)
    silent = None if item else silence_match(question, lang)
    if silent:
        # известное молчание закона: отвечаем по списку, нормы не подбираем
        text, citations = silence_answer(silent, lang)
        answer = {"text": text, "source": "none", "confidence": SILENCE_MAX_CONF,
                  "silence_id": silent["id"]}
        note = SILENCE_NOTE.get(lang) or SILENCE_NOTE[DEFAULT_LANG]
        out = {"lang": lang, "took_ms": int((time.time() - t0) * 1000), "answer": answer,
               "citations": citations, "related": related(lang),
               "ai": {"status": "off", "text": None}, "note": note, "cached": False,
               "assistant_name": dict(ASSISTANT_NAME), "live": dict(LIVE_NOT_NEEDED)}
        _cache_put(key, out)
        log_question(question, lang, "none", SILENCE_MAX_CONF, out["took_ms"], False)
        return out
    if item:
        a = faq_answer(item, lang, conf)
        answer = {"text": a["text"], "source": "faq", "confidence": conf,
                  "kind": a.get("kind"), "basis": a.get("basis")}
        citations, note = a["citations"], a["note"]
        passages = []
    else:
        stems = stems_of(question, lang)
        passages = search(question, lang)
        if not passages and lang != DEFAULT_LANG:
            # узбекского или английского текста этого акта в базе нет — отвечаем по-русски и говорим об этом
            stems = stems_of(question, DEFAULT_LANG)
            passages = search(question, DEFAULT_LANG)
            if passages:
                note = NOTE_NO_LANG.get(lang) or NOTE_NO_LANG["ru"]
        # уверенность — по лучшему пассажу и по весу найденных слов, а не по среднему числу
        # совпавших слов: среднее по трём случайным нормам давало «приемлемые» 0,5–0,7 там,
        # где закон вопроса вообще не касается
        # кусок считается ответом, только если в нём есть все редкие слова темы. Нет ни одного
        # такого куска — уверенность делим: совпали общие слова, а сама тема в норме не встретилась
        full = [r for r in passages if not r.get("rare_missing")]
        best = max((r.get("coverage_w", r["coverage"]) for r in (full or passages)), default=0.0)
        if passages and not full:
            best *= RARE_PENALTY
        conf = round(best, 2)
        # порог релевантности лучшего пассажа: доля значимых слов вопроса и сам bm25.
        # Без него на вопрос без нормы («срок рассмотрения претензии по добровольному виду»)
        # выдавался посторонний пассаж с обычной уверенностью.
        on_topic = _on_topic(passages[0] if passages else None, stems)
        if conf < MIN_CONFIDENCE or not on_topic:
            # закон молчит: не выдаём три произвольных нормы за ответ. Ближайшие по смыслу
            # статьи показываем отдельной пометкой closest — чтобы было что проверить руками,
            # но только если вопрос вообще о страховом праве (иначе цитаты бессмысленны).
            # «ближайшая по смыслу» — это кусок, где есть редкое слово темы вопроса. Если такого
            # нет ни в одном (вопрос про крышу склада, про билет в кино), показывать нечего
            near = [r for r in passages[:3] if not r.get("rare_missing")] if on_topic else []
            closest = [dict(_citation(r, lang, stems), closest=True) for r in near]
            # локальная база ответа не дала — ищем на lex.uz (app/legal_live.py). Только здесь:
            # обычные ответы из базы живой поиск не замедляет
            live = _live(question, lang, who)
            if live.get("status") in ("found", "found_base"):
                out = _live_answer(question, lang, with_ai, live, closest, t0)
                if out:
                    _cache_put(key, out)
                    log_question(question, lang, "lex", out["answer"]["confidence"], out["took_ms"], True)
                    return out
                live = dict(live, status="not_found",
                            text=(legal_live_text("not_found", lang)))
            conf = min(conf, SILENCE_MAX_CONF)
            passages, citations = [], closest
            texts = NO_NORM if closest else NO_NORM_BARE
            answer = {"text": texts.get(lang) or texts[DEFAULT_LANG],
                      "source": "none", "confidence": conf}
            note = note or NO_NORM_NOTE.get(lang) or NO_NORM_NOTE[DEFAULT_LANG]
            if live.get("status") in ("unavailable", "limit", "not_found") and live.get("text"):
                # честно: сайт недоступен / предел исчерпан / там тоже нет — ответ по базе
                note = note + "; " + live["text"]
                if live["status"] in ("unavailable", "limit"):
                    answer["text"] = answer["text"].rstrip(".") + ". " + live["text"] + "."
                for s in (live.get("skipped") or [])[:2]:
                    # похожий акт есть, но только на узбекском — ссылку даём, норму не пересказываем
                    note += "; %s: %s — %s" % (s["reason"], s.get("badge") or s["act"],
                                               s.get("official_url") or s["url"])
            # ни FAQ, ни закон не покрыли вопрос — отвечает модель, ответ помечен «ИИ»
            ai = ai_free_answer(question, lang) if with_ai else {"status": "off", "text": None}
            if ai.get("status") == "ok":
                note = note + " " + (ai.get("note") or "")
            out = {"lang": lang, "took_ms": int((time.time() - t0) * 1000), "answer": answer,
                   "citations": citations, "related": related(lang),
                   "ai": ai, "assistant_name": dict(ASSISTANT_NAME),
                   "note": note, "cached": False, "live": _live_public(live)}
            if live.get("status") not in ("unavailable", "limit", "error"):
                _cache_put(key, out)          # «сайт недоступен» не запоминаем: через минуту он может ожить
            log_question(question, lang, "none", conf, out["took_ms"], False)
            return out
        answer = {"text": summarize_passages(passages, lang, stems),
                  "source": "passages", "confidence": conf}
        citations = [_citation(r, lang, stems) for r in passages]
        if lang == "en" and citations and not any(c["official"] for c in citations):
            note = (note + " " if note else "") + "unofficial: no official English text of the act exists"

    ai = {"status": "off", "text": None}
    if with_ai and passages:
        ai = ai_answer(question, passages, lang)

    took = int((time.time() - t0) * 1000)
    out = {"lang": lang, "took_ms": took, "answer": answer, "citations": citations,
           "related": related(lang), "ai": ai, "note": note, "cached": False,
           "assistant_name": dict(ASSISTANT_NAME), "live": dict(LIVE_NOT_NEEDED)}
    _cache_put(key, out)
    log_question(question, lang, answer["source"], answer["confidence"], took, bool(citations))
    return out


def legal_live_text(status: str, lang: str) -> str:
    from . import legal_live
    t = legal_live.STATUS_TEXT.get(status) or {}
    return t.get(lang) or t.get(DEFAULT_LANG) or ""


def _live_answer(question: str, lang: str, with_ai: bool, live: dict, closest: list, t0: float):
    """Ответ по норме, найденной на lex.uz. Цитата — только дословная (legal._citation сверяет её
    с сохранённым в библиотеку текстом акта); не сошлась ни одна — ответа нет (None)."""
    from . import legal_live
    passages = live.get("passages") or []
    stems = stems_of(question, lang)
    fresh = live.get("status") == "found"
    label = legal_live.LABEL.get(lang) or legal_live.LABEL[DEFAULT_LANG]
    cits = []
    for r in passages:
        c = _citation(r, lang, stems)
        if not c["quote"]:
            continue
        c.update({"live": fresh, "found_on": "lex.uz", "live_label": label if fresh else None,
                  "act_badge": live.get("badge")})
        cits.append(c)
    if not cits:
        return None
    kept = [r for r in passages if any(c["unit"] == _unit_of(r) for c in cits)]
    conf = round(max(r.get("coverage_w", 0.0) for r in kept), 2)
    answer = {"text": summarize_passages(kept, lang, stems), "source": "passages",
              "confidence": conf, "live": fresh, "found_on": "lex.uz"}
    parts = [(label + ": " + (live.get("badge") or live.get("act") or "")) if fresh else
             legal_live_text("found_base", lang)]
    if lang != "uz":
        parts.append((legal_live.UNOFFICIAL.get(lang) or legal_live.UNOFFICIAL[DEFAULT_LANG]).rstrip(".")
                     + (f" ({live['official_url']})" if live.get("official_url") else ""))
    note = "; ".join(p for p in parts if p)
    ai = ai_answer(question, kept, lang) if with_ai else {"status": "off", "text": None}
    return {"lang": lang, "took_ms": int((time.time() - t0) * 1000), "answer": answer,
            "citations": cits + closest, "related": related(lang), "ai": ai, "note": note,
            "cached": False, "assistant_name": dict(ASSISTANT_NAME), "live": _live_public(live)}


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #

@router.on_event("startup")
def _build_index_on_start():
    """При старте сервера индекс дособирается в фоне: юрист мог докачать узбекские тексты,
    пока сервер был выключен. Отдельным потоком — старт не должен ждать разбор библиотеки."""
    if os.environ.get("SURVEYOR_NO_BACKGROUND") == "1":
        return
    try:
        from . import background
        background.start("legal-index", lambda: reindex())
    except Exception as e:
        print("legal: индекс при старте не собран:", e)


class AskIn(BaseModel):
    q: str = Field(min_length=2, max_length=1000)
    lang: Optional[str] = None
    ai: bool = False


def _who(request: Request) -> Optional[str]:
    """Ключ спрашивающего для личного предела живого поиска: вошедший, гость или адрес."""
    try:
        from . import guest
        from .guard import client_host
        user = request.scope.get("surveyor_user") or {}
        key = guest.owner_of(request, user)
        if key:
            return key
        host = client_host(request)
        return ("ip:" + host) if host else None
    except Exception:
        return None


@router.post("/legal/ask")
def legal_ask(body: AskIn, request: Request):
    """Мгновенный юридический ответ. lang не указан — определяем по тексту вопроса."""
    return ask(body.q, body.lang, with_ai=body.ai, who=_who(request))


@router.get("/legal/faq")
def legal_faq(lang: str = DEFAULT_LANG):
    """Список вопросов FAQ — чипы «спросить в один клик»."""
    lang = lang if lang in LANGS else DEFAULT_LANG
    items = []
    for item in faq_items():
        q, fb = _pick(item.get("q") or {}, lang)
        if not q:
            continue
        items.append({"id": item["id"], "q": q, "tags": item.get("tags") or [],
                      "kind": item.get("kind") or "норма",
                      "lang": DEFAULT_LANG if fb else lang})
    return {"lang": lang, "version": _faq_cache.get("version"), "count": len(items), "items": items,
            "assistant_name": dict(ASSISTANT_NAME),
            "practice_count": sum(1 for i in items if i["kind"] == "практика")}


@router.get("/legal/silences")
def legal_silences(lang: str = DEFAULT_LANG):
    """Известные молчания закона: вопросы, ответ на которые даёт не закон, а правила и договор."""
    lang = lang if lang in LANGS else DEFAULT_LANG
    items = []
    for s in SILENCES:
        q, _ = _pick(s.get("q") or {}, lang)
        a, _ = _pick(s.get("a") or {}, lang)
        items.append({"id": s["id"], "q": q, "a": a})
    return {"lang": lang, "count": len(items), "items": items,
            "note": SILENCE_NOTE.get(lang) or SILENCE_NOTE[DEFAULT_LANG]}


@router.get("/legal/acts")
def legal_acts():
    """Какие акты и на каких языках лежат в индексе — чтобы юрист видел, что ещё не докачано."""
    ensure_index()
    with db.tx() as con:
        rs = db.rows(con, "SELECT act, act_code, language, SUM(chunks) AS chunks FROM legal_files"
                          " WHERE path <> ? GROUP BY act_code, language ORDER BY act", PARSER_ROW)
    acts = {}
    for r in rs:
        a = acts.setdefault(r["act_code"], {"act_code": r["act_code"], "act": r["act"],
                                            "languages": [], "chunks": 0})
        a["languages"].append(r["language"])
        a["chunks"] += r["chunks"] or 0
    out = sorted(acts.values(), key=lambda a: a["act"])
    missing = [a["act"] for a in out if "uz" not in a["languages"]]
    return {"count": len(out), "acts": out, "languages": list(LANGS),
            "without_uz": missing, "faq_file": FAQ_FILE.exists()}


@router.post("/legal/reindex")
def legal_reindex(request: Request, force: bool = True):
    """Пересборка индекса (администратор): юрист докачал узбекский текст — нажал и готово."""
    user = request.scope.get("surveyor_user") or {}
    out = reindex(force=force)
    try:
        with db.tx() as con:
            db.audit(con, user.get("login") or "админ", "пересобран индекс законодательства",
                     "legal_chunks", {"кусков": out["chunks"], "файлов": out["files_total"]})
    except Exception as e:
        print("legal: журнал пересборки не записан:", e)
    return out
