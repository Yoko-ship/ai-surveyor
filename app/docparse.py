"""
Разбор документов объекта: техпаспорт автомобиля и кадастровый документ на недвижимость.

Зачем: агент загружает документ, система вытаскивает из него характеристики объекта и подставляет
их в блок «Стоимость объекта» (app/valuation.py), чтобы агент не набивал их руками.

Что умеем и чего не умеем — честно:
  * PDF с текстовым слоем — читаем через pymupdf (fitz) и ищем значения по подписям полей;
  * PDF без текстового слоя (скан) и фотографии — локального распознавания текста в проекте нет
    (пакеты не ставим), поэтому возвращаем статус «нужно распознавание», поля пустые.
    Значения НЕ угадываем и не достраиваем — агент вводит их руками;
  * пробега в техпаспорте нет, года постройки и числа комнат в кадастровом реестре нет —
    такие поля помечены как ручные, с указанием, откуда их брать.

Подписи полей и правила — по заметке юриста «docs/Документы объекта — поля для автозаполнения.md».
Главное из неё: образцов бланков нет, подписи собраны из нормативных актов (ПКМ № 683, ПКМ № 141,
ЕНСО рег. № 3487, ЗРУ-803), поэтому разметка бланка не подтверждена — в ответе это видно полем
«сверено_с_бланком»: false.

Персональные данные (ЗРУ-547 ст. 4 и 10, правило PD-01): ФИО собственника, его адрес,
паспортные данные, ПИНФЛ и ИНН физлица НЕ извлекаются, НЕ сохраняются в базе и НЕ возвращаются
в ответе. Для страховой стоимости они не нужны. Из кадастрового документа берём местонахождение
ОБЪЕКТА (это характеристика имущества), из техпаспорта адресов не берём вовсе — там адрес
владельца (графа 5 свидетельства, ПКМ № 683 п. 23).

Результат разбора сохраняется в photos.parsed_json, чтобы не разбирать один и тот же файл дважды.
"""
import json
import re
from datetime import date
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db

router = APIRouter()

ROOT = Path(__file__).resolve().parent.parent
LAWYER_NOTE = ROOT / "docs" / "Документы объекта — поля для автозаполнения.md"

KIND_PHOTO = "фото объекта"
KIND_PASSPORT = "техпаспорт"
KIND_CADASTRE = "кадастр"
DOC_KINDS = (KIND_PASSPORT, KIND_CADASTRE)          # виды, которые разбираем
ALL_KINDS = (KIND_PHOTO,) + DOC_KINDS

# Статусы разбора
ST_OK = "разобран"
ST_PARTIAL = "частично"
ST_OCR = "нужно распознавание"
ST_ERROR = "ошибка"
ST_EMPTY = "ничего не найдено"

OCR_TEXT = ("В файле нет текстового слоя (это скан или фотография). Распознавания текста "
            "в системе нет, поэтому поля остались пустыми — заполните их вручную.")

# --------------------------------------------------------------------------- #
# Поля и подписи
# --------------------------------------------------------------------------- #
# Каждое поле: ключ, название для человека, тип значения, куда кладём в оценку стоимости,
# подписи для поиска и норма, которой подпись подтверждена. Если нормы нет — такой графы
# нормативные акты не называют, берём её, только если она есть в бланке.
# Подписи пишем строчными, с обычным апострофом и без «ё»: их приводит к такому виду norm().

TP_683 = "ПКМ № 683 от 31.08.2017, п. 11⁷"
TP_141 = "ПКМ № 141 от 24.06.2008, форма полиса ОСГО"
TP_ENSO = "ЕНСО (рег. № 3487), Методика оценки ТС, п. 4"
KAD_803 = "ЗРУ-803, ст. 23 и 29"
KAD_FORM = "форма выписки из госреестра (ПКМ № 1060 утратил силу 26.08.2023 — словарь терминов)"

FIELDS = {
    KIND_PASSPORT: [
        {"key": "brand", "name": "Марка", "type": "text", "to_valuation": "марка",
         "norm": TP_683 + "; " + TP_141,
         "labels": ["марка и модель", "марка", "markasi va modeli", "markasi", "rusumi",
                    "маркаси ва модели", "маркаси"]},
        {"key": "model", "name": "Модель, модификация", "type": "text", "to_valuation": "модель",
         "norm": TP_683,
         "labels": ["модель и модификация", "модель, модификация", "модель", "модели",
                    "model va modifikatsiyasi", "modeli", "model", "модель ва модификацияси"]},
        {"key": "year", "name": "Год выпуска", "type": "year", "to_valuation": "год",
         "norm": TP_141,
         "labels": ["год выпуска", "год изготовления", "дата выпуска",
                    "ishlab chiqarilgan yili", "chiqarilgan yili",
                    "ишлаб чикарилган йили", "чикарилган йили"]},
        {"key": "vehicle_type", "name": "Тип транспортного средства", "type": "text",
         "to_valuation": None, "norm": TP_683,
         "labels": ["тип транспортного средства", "тип тс", "тип", "tipi", "типи"]},
        {"key": "body_no", "name": "Номер кузова", "type": "vin", "to_valuation": None,
         "norm": TP_683 + "; " + TP_141,
         "labels": ["номер кузова (шасси)", "номер кузова", "kuzov (shassi) raqami",
                    "kuzov raqami", "кузов раками"]},
        # Отдельной графы VIN в ПКМ № 683 нет (там «идентификационные номера кузова, шасси
        # и двигателя»); VIN назван только в ЕНСО — открытый вопрос из заметки юриста.
        {"key": "vin", "name": "Идентификационный номер (VIN)", "type": "vin",
         "to_valuation": "vin", "norm": TP_ENSO,
         "labels": ["идентификационный номер (vin)", "идентификационный номер", "vin-код", "vin",
                    "identifikatsiya raqami", "идентификация раками"]},
        {"key": "chassis_no", "name": "Номер шасси (рамы)", "type": "vin", "to_valuation": None,
         "norm": TP_683,
         "labels": ["номер шасси (рамы)", "номер шасси", "номер рамы",
                    "shassi (rama) raqami", "shassi raqami", "шасси (рама) раками"]},
        {"key": "engine_no", "name": "Номер двигателя", "type": "vin", "to_valuation": None,
         "norm": TP_683 + "; " + TP_141,
         "labels": ["номер двигателя", "dvigatel raqami", "двигатель раками"]},
        {"key": "color", "name": "Цвет", "type": "text", "to_valuation": None,
         "norm": "ПКМ № 683, п. 53",
         "labels": ["цвет окраски", "цвет", "rangi", "ранги"]},
        {"key": "seats", "name": "Число сидений", "type": "int", "to_valuation": None,
         "norm": TP_683,
         "labels": ["количество сидений", "число сидений", "o'rindiqlar soni", "уриндиклар сони"]},
        # ЕНСО объём двигателя требует, а в перечне ПКМ № 683 его нет — открытый вопрос
        {"key": "engine_cc", "name": "Рабочий объём двигателя, см³", "type": "number",
         "to_valuation": None, "norm": TP_ENSO,
         "labels": ["рабочий объём двигателя", "рабочий объем двигателя", "объём двигателя",
                    "объем двигателя", "dvigatel hajmi", "двигатель хажми"]},
        # Такой графы нормативные акты не называют. Берём, только если она есть в бланке;
        # из государственного номера регион НЕ выводим — это догадка, а не данные документа.
        {"key": "region", "name": "Регион учёта", "type": "region", "to_valuation": "регион",
         "norm": None,
         "labels": ["регион регистрации", "место регистрации", "регион", "область",
                    "viloyat", "вилоят"]},
    ],
    KIND_CADASTRE: [
        {"key": "cadastre_no", "name": "Кадастровый номер", "type": "text", "to_valuation": None,
         "norm": KAD_803,
         "labels": ["кадастровый номер", "кадастровый №", "kadastr raqami", "кадастр раками"]},
        {"key": "address", "name": "Местонахождение объекта", "type": "text",
         "to_valuation": "адрес", "norm": KAD_803 + "; " + KAD_FORM,
         "labels": ["адрес объекта", "местонахождение", "местоположение", "адрес",
                    "obyekt manzili", "joylashgan joyi", "joylashgan yeri", "manzili",
                    "объект манзили", "жойлашган жойи", "манзили"]},
        {"key": "region", "name": "Регион", "type": "region", "to_valuation": "регион",
         "norm": None,
         "labels": ["регион", "область", "район", "viloyat", "tuman", "вилоят", "туман"]},
        {"key": "object_kind", "name": "Вид объекта недвижимости", "type": "text",
         "to_valuation": "тип", "norm": KAD_FORM,
         "labels": ["вид объекта недвижимости", "наименование объекта недвижимости",
                    "наименование объекта", "вид объекта", "тип объекта", "назначение",
                    "ko'chmas mulk obyekti turi", "ko'chmas mulk obyektining nomi",
                    "obyekt turi", "кучмас мулк объекти тури", "объект тури"]},
        {"key": "area_m2", "name": "Площадь зданий и сооружений, кв. м", "type": "area",
         "to_valuation": "площадь", "norm": KAD_FORM,
         "labels": ["площадь зданий и сооружений", "общая площадь", "площадь объекта",
                    "площадь, кв. м", "площадь", "umumiy maydoni",
                    "bino va inshootlar maydoni", "maydoni, kv.m", "maydoni",
                    "умумий майдони", "бино ва иншоотлар майдони", "майдони"]},
        {"key": "land_area_ha", "name": "Площадь земельного участка, га", "type": "number",
         "to_valuation": None, "norm": KAD_FORM,
         "labels": ["площадь земельного участка", "площадь участка", "площадь, га",
                    "yer uchastkasi maydoni", "maydoni, ga", "майдони, га"]},
        {"key": "right_kind", "name": "Вид права", "type": "text", "to_valuation": None,
         "norm": KAD_803,
         "labels": ["вид права", "huquq turi", "хукук тури"]},
        # DOC-KAD-02: кадастровая стоимость не равна страховой — в оценку её не подставляем
        {"key": "cadastral_value", "name": "Кадастровая стоимость, тыс. сум", "type": "number",
         "to_valuation": None, "norm": KAD_803,
         "labels": ["кадастровая стоимость", "kadastr bahosi", "кадастр бахоси"]},
        {"key": "encumbrance", "name": "Запрет, арест, ограничения", "type": "text",
         "to_valuation": None, "norm": KAD_803,
         "labels": ["наличие запрета, ареста или ограничений", "ограничения", "обременения",
                    "арест", "taqiq, xatlov yoki cheklovlar mavjudligi", "cheklovlar",
                    "такик, хатлов ёки чекловлар мавжудлиги"]},
        {"key": "mortgage", "name": "Сведения об ипотеке", "type": "text", "to_valuation": None,
         "norm": KAD_803,
         "labels": ["сведения об ипотеке", "ипотека", "ipoteka"]},
        # Числа комнат в госреестре нет (ЗРУ-803, ст. 23): бывает в плане-экспликации
        # кадастрового дела — берём, если встретилось.
        {"key": "rooms", "name": "Число комнат", "type": "int", "to_valuation": "комнаты",
         "norm": None,
         "labels": ["число комнат", "количество комнат", "комнат", "xonalar soni", "хоналар сони"]},
        # Года ввода в эксплуатацию в реестре нет (ЗРУ-803, ст. 23; ЕНСО, п. 592)
        {"key": "build_year", "name": "Год постройки", "type": "year", "to_valuation": "год",
         "norm": None,
         "labels": ["год постройки", "год ввода в эксплуатацию", "qurilgan yili",
                    "foydalanishga topshirilgan yili", "курилган йили"]},
        {"key": "floors", "name": "Этажность", "type": "int", "to_valuation": None, "norm": None,
         "labels": ["этажность", "число этажей", "количество этажей", "qavatlar soni",
                    "каватлар сони"]},
        {"key": "walls", "name": "Материал стен", "type": "text", "to_valuation": None,
         "norm": None,
         "labels": ["материал стен", "стены", "devor materiali", "девор материали"]},
    ],
}

# Поля, которых в документе нет: не ищем вовсе и говорим, откуда их брать.
# Источник — заметка юриста, разделы «Чего в техпаспорте НЕТ» и «Чего нет в кадастре».
MANUAL_FIELDS = {
    KIND_PASSPORT: [
        {"key": "mileage_km", "name": "Пробег, км",
         "why": "в техпаспорте пробега нет: показания снимаются с одометра при осмотре, "
                "к оценке прикладывается фото одометра",
         "norm": "ЕНСО, Методика оценки ТС, п. 4 и 5 (правило DOC-TP-01)"},
        {"key": "condition", "name": "Техническое состояние, дефекты",
         "why": "берётся из акта осмотра технического состояния",
         "norm": "ЕНСО, приложение № 1 к Методике оценки ТС"},
        {"key": "trim", "name": "Комплектация и дополнительное оборудование",
         "why": "берётся из технической документации владельца",
         "norm": "ЕНСО, п. 7 Методики оценки ТС"},
    ],
    KIND_CADASTRE: [
        {"key": "book_value", "name": "Балансовая и остаточная стоимость, износ",
         "why": "берётся из бухгалтерской справки, в кадастровых данных её нет",
         "norm": "ЕНСО"},
    ],
}

# Государственный номер. По заметке юриста (раздел 3) он косвенно идентифицирует владельца:
# хранить стоит, только если нужен для полиса, а для оценки хватает марки, года и VIN.
# Поэтому по умолчанию не извлекаем; включается явно параметром with_reg_no.
REG_NO_FIELD = {
    "key": "reg_no", "name": "Государственный регистрационный номерной знак",
    "type": "reg_no", "to_valuation": None, "norm": "ПКМ № 683, п. 2; " + TP_141,
    "labels": ["государственный регистрационный номер", "регистрационный номерной знак",
               "государственный номер", "гос. номер", "госномер", "грнз",
               "davlat raqami belgisi", "davlat raqami", "ro'drb",
               "давлат раками белгиси", "давлат раками"],
}
REG_NO_WHY = ("не извлекается по умолчанию: государственный номер косвенно идентифицирует "
              "владельца, а для оценки достаточно марки, года выпуска и VIN (ЗРУ-547, ст. 10). "
              "Если номер нужен для полиса — запросите разбор с with_reg_no=true")

# Предупреждения агенту по результату разбора. Коды — из заметки юриста, раздел 4.
# Условие: empty — поле не заполнено, filled — заполнено, always — всегда.
WARNINGS = {
    KIND_PASSPORT: [
        ("DOC-TP-03", "year", "empty", "стоп",
         "Год выпуска не распознан — укажите его вручную, без него не считается износ.",
         "ЕНСО, Методика оценки ТС"),
        ("DOC-TP-01", "mileage_km", "always", "подсказка",
         "Пробега в техпаспорте нет. Введите показания одометра и приложите его фото.",
         "ЕНСО, п. 4 и 5 Методики оценки ТС"),
        ("DOC-TP-02", "vin", "filled", "предупреждение",
         "Сверьте VIN и номера кузова, шасси и двигателя с фактическими при осмотре: "
         "при расхождении оценку приостановить.",
         "ЕНСО, п. 6 и 9 Методики оценки ТС"),
    ],
    KIND_CADASTRE: [
        ("DOC-KAD-01", "build_year", "empty", "подсказка",
         "Года постройки в кадастровых данных нет — нужна справка балансодержателя.",
         "ЗРУ-803, ст. 23; ЕНСО, п. 592"),
        ("DOC-KAD-02", "cadastral_value", "filled", "предупреждение",
         "Кадастровая стоимость не является страховой стоимостью — в страховую сумму "
         "её не подставляем.",
         "ЗРУ-803, ст. 23"),
        ("DOC-KAD-03", "encumbrance", "filled", "предупреждение",
         "По объекту есть запись об ограничении, аресте или запрете — уточните "
         "выгодоприобретателя.",
         "ЗРУ-803, ст. 23 и 29"),
        ("DOC-KAD-03", "mortgage", "filled", "предупреждение",
         "По объекту есть сведения об ипотеке — уточните выгодоприобретателя.",
         "ЗРУ-803, ст. 23 и 29"),
    ],
}

# PD-02 (ЗРУ-547, ст. 10 и 17): скан целиком содержит персональные данные, после извлечения
# полей исходник положено удалять. Сами не удаляем — срок хранения определяет заказчик.
PD_NOTICE = ("Загруженный файл целиком содержит персональные данные владельца. После оценки "
             "исходник подлежит удалению (ЗРУ-547, ст. 10 и 17, правило PD-02); "
             "срок хранения — решение заказчика.")

# --------------------------------------------------------------------------- #
# Персональные данные: подписи, значения которых не извлекаем никогда
# (ЗРУ-547 ст. 4 и 10; заметка юриста, раздел 3, правило PD-01)
# --------------------------------------------------------------------------- #
PERSONAL_LABELS = [
    "фамилия", "имя", "отчество", "ф.и.о", "фио", "ф. и. о",
    "собственник", "владелец", "собственники", "правообладатель", "доля в праве",
    "паспорт", "серия паспорта", "номер паспорта", "id-карт", "пинфл", "жшшир",
    "инн", "стир", "дата рождения", "место рождения",
    "адрес собственника", "адрес владельца", "место жительства", "прописка", "подпись",
    "familiyasi", "ismi", "otasining ismi", "f.i.sh", "fish", "egasi", "mulkdor",
    "huquq egasi", "huquqdagi ulushi", "pasport", "jshshir", "stir",
    "tug'ilgan sanasi", "yashash manzili", "imzo",
    "фамилияси", "исми", "отасининг исми", "эгаси", "мулкдор", "хукук эгаси",
    "паспорти", "яшаш манзили", "имзо",
]

# --------------------------------------------------------------------------- #
# Нормализация текста
# --------------------------------------------------------------------------- #
APOSTROPHES = "‘’ʻʼ`´′ʹ'"

REGIONS = {
    "республика каракалпакстан": "Республика Каракалпакстан",
    "каракалпакстан": "Республика Каракалпакстан",
    "qoraqalpog'iston": "Республика Каракалпакстан",
    "андижанская": "Андижанская область", "андижан": "Андижанская область",
    "andijon": "Андижанская область",
    "бухарская": "Бухарская область", "бухара": "Бухарская область", "buxoro": "Бухарская область",
    "джизакская": "Джизакская область", "джизак": "Джизакская область",
    "jizzax": "Джизакская область",
    "кашкадарьинская": "Кашкадарьинская область", "карши": "Кашкадарьинская область",
    "qashqadaryo": "Кашкадарьинская область",
    "навоийская": "Навоийская область", "навои": "Навоийская область",
    "navoiy": "Навоийская область",
    "наманганская": "Наманганская область", "наманган": "Наманганская область",
    "namangan": "Наманганская область",
    "самаркандская": "Самаркандская область", "самарканд": "Самаркандская область",
    "samarqand": "Самаркандская область",
    "сурхандарьинская": "Сурхандарьинская область", "термез": "Сурхандарьинская область",
    "surxondaryo": "Сурхандарьинская область", "surxandaryo": "Сурхандарьинская область",
    "сырдарьинская": "Сырдарьинская область", "гулистан": "Сырдарьинская область",
    "sirdaryo": "Сырдарьинская область",
    "город ташкент": "город Ташкент", "г. ташкент": "город Ташкент",
    "toshkent shahri": "город Ташкент", "тошкент шахри": "город Ташкент",
    "ташкентская": "Ташкентская область", "toshkent viloyati": "Ташкентская область",
    "ташкент": "город Ташкент", "toshkent": "город Ташкент", "тошкент": "город Ташкент",
    "ферганская": "Ферганская область", "фергана": "Ферганская область",
    "farg'ona": "Ферганская область",
    "хорезмская": "Хорезмская область", "ургенч": "Хорезмская область",
    "xorazm": "Хорезмская область",
}


def norm(text: str) -> str:
    """Текст к единому виду для сравнения подписей: нижний регистр, один апостроф, без ё."""
    t = (text or "").lower().replace("ё", "е")
    for a in APOSTROPHES:
        t = t.replace(a, "'")
    t = t.replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


def _normalize_labels():
    """Подписи в таблицах пишутся людьми — приводим их к тому же виду, что и текст документа."""
    for lst in FIELDS.values():
        for f in lst:
            f["labels"] = [norm(x) for x in f["labels"]]
    REG_NO_FIELD["labels"] = [norm(x) for x in REG_NO_FIELD["labels"]]


_normalize_labels()
PERSONAL_LABELS = [norm(x) for x in PERSONAL_LABELS]
REGIONS = {norm(k): v for k, v in REGIONS.items()}


def is_personal_label(line: str) -> bool:
    """Строка подписана как персональные данные — значение из неё не берём (PD-01)."""
    n = norm(line)
    return any(p in n for p in PERSONAL_LABELS)


# --------------------------------------------------------------------------- #
# Чтение PDF
# --------------------------------------------------------------------------- #

MIN_TEXT_CHARS = 20          # меньше — считаем, что текстового слоя нет (скан)


def pdf_text(path: Path) -> str:
    """Текстовый слой PDF. Без pymupdf и на битом файле — понятная ошибка, а не падение."""
    try:
        import pymupdf
    except ImportError:                                   # pragma: no cover
        try:
            import fitz as pymupdf                        # старое имя того же пакета
        except ImportError:
            raise RuntimeError("pymupdf не установлен — PDF прочитать нечем")
    out = []
    with pymupdf.open(path) as doc:
        for page in doc:
            out.append(page.get_text("text"))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Разбор значений
# --------------------------------------------------------------------------- #

SEP = re.compile(r"\s*[:：]\s*")
THIS_YEAR = date.today().year


def clean_value(v: str) -> str:
    v = (v or "").strip(" \t.,;—–-_|")
    return re.sub(r"\s+", " ", v)[:200]


def cast(kind: str, raw: str) -> Optional[str]:
    """Проверяет значение по типу поля. Не подходит — None: лучше пусто, чем мусор."""
    v = clean_value(raw)
    if not v:
        return None
    if kind == "year":
        m = re.search(r"\b(19\d{2}|20\d{2})\b", v)
        if not m:
            return None
        y = int(m.group(1))
        return str(y) if 1900 <= y <= THIS_YEAR + 1 else None
    if kind == "int":
        m = re.search(r"\b(\d{1,3})\b", v)
        return m.group(1) if m else None
    if kind == "number":
        m = re.search(r"(\d+(?:[.,]\d+)?)", v.replace(" ", ""))
        return m.group(1).replace(",", ".") if m else None
    if kind == "area":
        m = re.search(r"(\d+(?:[.,]\d+)?)", v.replace(" ", ""))
        if not m:
            return None
        val = float(m.group(1).replace(",", "."))
        return str(val) if 1 <= val <= 1_000_000 else None
    if kind == "region":
        n = norm(v)
        for key, name in REGIONS.items():
            if key in n:
                return name
        return None
    if kind == "vin":
        m = re.search(r"\b([A-HJ-NPR-Z0-9]{9,17})\b", v.upper())
        return m.group(1) if m else None
    if kind == "reg_no":
        m = re.search(r"\b(\d{2}\s?[A-ZА-Я]\s?\d{3}\s?[A-ZА-Я]{2})\b", v.upper())
        return re.sub(r"\s+", " ", m.group(1)) if m else None
    return v or None


def find_field(lines: List[str], field: dict) -> Optional[dict]:
    """
    Ищет значение поля по подписям. Значение берём справа от подписи (после двоеточия),
    а если справа пусто — со следующей непустой строки (в бланках так свёрстаны таблицы).
    Уверенность: «высокая» — значение на той же строке, что и подпись;
    «средняя» — значение взято со следующей строки.
    """
    labels = sorted(field["labels"], key=len, reverse=True)
    for i, line in enumerate(lines):
        if is_personal_label(line):                 # строка про человека — пропускаем целиком
            continue
        n = norm(line)
        for lab in labels:
            if not (n.startswith(lab) or f" {lab}" in n or f"{lab}:" in n):
                continue
            pos = n.find(lab) + len(lab)            # отрезаем подпись и всё, что было слева
            tail = line[pos:] if pos <= len(line) else ""
            tail = SEP.sub("", tail, count=1) if SEP.match(tail) else tail
            val = cast(field["type"], tail)
            if val:
                return {"value": val, "label": lab, "confidence": "высокая", "line": i}
            for j in range(i + 1, min(i + 3, len(lines))):
                nxt = lines[j]
                if not nxt.strip() or is_personal_label(nxt):
                    continue
                val = cast(field["type"], nxt)
                if val:
                    return {"value": val, "label": lab, "confidence": "средняя", "line": j}
                break
    return None


def _item(f: dict, hit: Optional[dict]) -> dict:
    return {"поле": f["key"], "название": f["name"],
            "значение": hit["value"] if hit else None,
            "найдено_по": hit["label"] if hit else None,
            "уверенность": hit["confidence"] if hit else "не найдено",
            "ручное": hit is None,
            "норма": f.get("norm"),
            "подтверждено_нормой": bool(f.get("norm")),
            "сверено_с_бланком": False}


def build_warnings(doc_kind: str, values: dict) -> List[dict]:
    """Подсказки, предупреждения и стопы по правилам из заметки юриста (раздел 4)."""
    out = []
    for code, key, when, level, text, ref in WARNINGS.get(doc_kind, []):
        filled = bool(values.get(key))
        if when == "always" or (when == "empty" and not filled) or (when == "filled" and filled):
            out.append({"код": code, "уровень": level, "текст": text, "норма": ref})
    return out


ST_AI = "ИИ, требует проверки"


def ai_fill(fields: list, values: dict, for_valuation: dict, text: str, doc_kind: str) -> dict:
    """
    Поля, которые не нашлись подписями, пробуем достать через ИИ — и только их.
    Персональные данные маскируются внутри app/llm.py. Без ИИ функция ничего не делает,
    разбор остаётся прежним. Всё найденное ИИ помечается как требующее проверки человеком.
    """
    try:
        from . import llm
    except Exception:                      # модуль ИИ не подключён — работаем как раньше
        return {}
    if not llm.enabled():
        return {}
    by_key = {f["key"]: f for f in FIELDS.get(doc_kind, [])}
    missing = [{"key": it["поле"], "name": it["название"]} for it in fields
               if it["значение"] is None and it["поле"] in by_key]
    if not missing:
        return {}
    try:
        got = llm.extract_fields(text, missing, doc_kind)
    except Exception as e:                 # ошибку не глотаем молча — она уже в журнале llm_calls
        print("ИИ-дозаполнение полей:", e)
        return {}
    filled = {}
    for it in fields:
        key = it["поле"]
        if it["значение"] is not None or key not in got:
            continue
        f = by_key[key]
        val = cast(f.get("type", "text"), str(got[key]))
        if not val:
            continue
        it.update({"значение": val, "найдено_по": "ИИ", "уверенность": ST_AI, "ручное": False,
                   "источник": "ИИ", "требует_проверки": True,
                   "пояснение": "Значение предложено ИИ по тексту документа — проверьте по оригиналу"})
        values[key] = val
        if f.get("to_valuation"):
            for_valuation[f["to_valuation"]] = val
        filled[key] = val
    return filled


def parse_text(text: str, doc_kind: str, with_reg_no: bool = False) -> dict:
    """Разбор готового текста документа. Отдельно от чтения файла — так его удобно проверять тестом."""
    if doc_kind not in FIELDS:
        raise ValueError("разбираем только: " + ", ".join(DOC_KINDS))
    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    fields, for_valuation, values, found = [], {}, {}, 0

    for f in FIELDS[doc_kind]:
        hit = find_field(lines, f)
        fields.append(_item(f, hit))
        if hit:
            found += 1
            values[f["key"]] = hit["value"]
            if f["to_valuation"]:
                for_valuation[f["to_valuation"]] = hit["value"]

    # Отдельной графы «регион» в кадастровых документах нет: берём его из местонахождения
    # объекта — это тот же документ, а не догадка.
    if doc_kind == KIND_CADASTRE and not values.get("region") and values.get("address"):
        reg = cast("region", values["address"])
        if reg:
            values["region"] = reg
            for_valuation["регион"] = reg
            for it in fields:
                if it["поле"] == "region":
                    it.update({"значение": reg, "найдено_по": "местонахождение объекта",
                               "уверенность": "средняя", "ручное": False})
            found += 1

    if doc_kind == KIND_PASSPORT:
        if with_reg_no:
            hit = find_field(lines, REG_NO_FIELD)
            fields.append(_item(REG_NO_FIELD, hit))
            if hit:
                values[REG_NO_FIELD["key"]] = hit["value"]
        else:
            it = _item(REG_NO_FIELD, None)
            it.update({"уверенность": "не извлекается", "пояснение": REG_NO_WHY})
            fields.append(it)

    for m in MANUAL_FIELDS.get(doc_kind, []):
        fields.append({"поле": m["key"], "название": m["name"], "значение": None,
                       "найдено_по": None, "уверенность": "не извлекается", "ручное": True,
                       "пояснение": m["why"], "норма": m["norm"],
                       "подтверждено_нормой": True, "сверено_с_бланком": False})

    ai = ai_fill(fields, values, for_valuation, text, doc_kind)
    found += len(ai)

    total = len(FIELDS[doc_kind])
    status = ST_OK if found == total else (ST_PARTIAL if found else ST_EMPTY)
    out = {"вид_документа": doc_kind, "статус": status,
           "найдено_полей": found, "всего_полей": total,
           "поля": fields, "для_оценки": for_valuation,
           "предупреждения": build_warnings(doc_kind, values),
           "персональные_данные": PD_NOTICE,
           "источник_подписей": "docs/" + LAWYER_NOTE.name if LAWYER_NOTE.exists() else None,
           "сверено_с_бланком": False,
           "примечание": None}
    if ai:
        out["ИИ_дозаполнил"] = sorted(ai)
        out["предупреждения"].append(
            {"код": "ai-fields", "уровень": "внимание",
             "текст": "Поля %s предложены ИИ и требуют проверки по оригиналу документа" % ", ".join(sorted(ai)),
             "норма": None})
    if status == ST_EMPTY:
        out["примечание"] = ("Текст в файле есть, но подписи полей не распознаны — "
                             "проверьте, тот ли это документ, и заполните поля вручную.")
    if not LAWYER_NOTE.exists():                 # pragma: no cover — заметка юриста на месте
        out["предупреждение"] = ("Перечень полей не подтверждён юристом (нет файла «%s») — "
                                 "подписи взяты по типовой форме." % LAWYER_NOTE.name)
    return out


def _blank(doc_kind: str, status: str, note: str) -> dict:
    return {"вид_документа": doc_kind, "статус": status, "найдено_полей": 0,
            "всего_полей": len(FIELDS.get(doc_kind, [])), "поля": [], "для_оценки": {},
            "предупреждения": [], "персональные_данные": PD_NOTICE,
            "сверено_с_бланком": False, "примечание": note}


def parse_file(path: Path, mime: str, doc_kind: str, with_reg_no: bool = False) -> dict:
    """Разбор файла на диске: PDF читаем, картинку — нет (распознавания в системе нет)."""
    if mime != "application/pdf":
        return _blank(doc_kind, ST_OCR, OCR_TEXT)
    try:
        text = pdf_text(path)
    except Exception as e:
        return _blank(doc_kind, ST_ERROR, "PDF прочитать не удалось: %s" % type(e).__name__)
    if len(norm(text)) < MIN_TEXT_CHARS:
        return _blank(doc_kind, ST_OCR, OCR_TEXT)
    return parse_text(text, doc_kind, with_reg_no)


# --------------------------------------------------------------------------- #
# Чек-лист документов: отметка «получен»
# --------------------------------------------------------------------------- #
# Механизм в проекте уже есть: таблица documents (request_id, doc_name, file_path, received,
# extracted) плюс справочник checklists (doc_name). Здесь только связываем вид загруженного
# файла с пунктом чек-листа и ставим received = 1.

CHECKLIST_MATCH = {
    KIND_PASSPORT: ["технический паспорт и свидетельство о регистрации",
                    "технический паспорт или кадастровые документы"],
    KIND_CADASTRE: ["технический паспорт или кадастровые документы",
                    "документ о праве на объект"],
    KIND_PHOTO: ["фотографии объекта", "фотографии с четырёх сторон и фото одометра"],
}


def checklist_item(con, doc_kind: str) -> Optional[str]:
    """Пункт чек-листа, который закрывает этот вид документа. Формулировку берём из справочника."""
    names = {norm(r["doc_name"]): r["doc_name"]
             for r in db.rows(con, "SELECT doc_name FROM checklists")}
    for want in CHECKLIST_MATCH.get(doc_kind, []):
        if norm(want) in names:
            return names[norm(want)]
    return None


def mark_received(con, rid: int, doc_kind: str, file_path: str, parsed: dict,
                  who: str = "api") -> dict:
    """
    Отмечает пункт чек-листа полученным. Идемпотентно: повторная загрузка того же вида
    обновляет существующую строку, дублей не плодит.
    """
    doc_name = checklist_item(con, doc_kind)
    if not doc_name:
        return {"отмечен": False,
                "причина": "в справочнике checklists нет пункта для вида «%s»" % doc_kind}
    extracted = json.dumps(parsed, ensure_ascii=False) if parsed else None
    rows = db.rows(con, "SELECT id FROM documents WHERE request_id=? AND doc_name=?", rid, doc_name)
    if rows:
        con.execute("UPDATE documents SET file_path=?, received=1, extracted=? WHERE id=?",
                    (file_path, extracted, rows[0]["id"]))
        doc_id = rows[0]["id"]
    else:
        cur = con.execute("INSERT INTO documents (request_id, doc_name, file_path, received,"
                          " extracted) VALUES (?,?,?,1,?)", (rid, doc_name, file_path, extracted))
        doc_id = cur.lastrowid
    db.audit(con, who, "документ получен по чек-листу", f"request:{rid}",
             {"doc_id": doc_id, "doc_name": doc_name, "doc_kind": doc_kind})
    return {"отмечен": True, "document_id": doc_id, "пункт": doc_name}


def checklist_state(con, rid: int) -> List[dict]:
    """Что из чек-листа уже получено по этому запросу (для экрана агента)."""
    got = {norm(r["doc_name"]): r for r in
           db.rows(con, "SELECT id, doc_name, received, file_path FROM documents"
                        " WHERE request_id=?", rid)}
    out = []
    for r in db.rows(con, "SELECT scope_type, scope_code, doc_name, required FROM checklists"
                          " ORDER BY scope_type, scope_code, id"):
        d = got.get(norm(r["doc_name"]))
        out.append({"пункт": r["doc_name"], "область": r["scope_type"], "код": r["scope_code"],
                    "обязателен": bool(r["required"]),
                    "получен": bool(d and d["received"]),
                    "document_id": d["id"] if d else None})
    return out


# --------------------------------------------------------------------------- #
# Эндпоинты
# --------------------------------------------------------------------------- #

def _photo(con, photo_id: int) -> dict:
    r = db.rows(con, "SELECT * FROM photos WHERE id=?", photo_id)
    if not r:
        raise HTTPException(404, "Файл не найден")
    return r[0]


class ParseIn(BaseModel):
    photo_id: int
    doc_kind: Optional[str] = None          # если при загрузке вид не указали
    force: bool = False                     # разобрать заново, даже если результат уже сохранён
    with_reg_no: bool = False               # извлекать государственный номер (см. REG_NO_WHY)
    who: str = "api"


def parse_photo(con, photo_id: int, doc_kind: str = None, force: bool = False,
                who: str = "api", with_reg_no: bool = False) -> dict:
    """Разбор загруженного файла с сохранением результата в photos.parsed_json."""
    p = _photo(con, photo_id)
    kind = doc_kind or p.get("doc_kind") or KIND_PHOTO
    if kind not in DOC_KINDS:
        raise HTTPException(400, "Разбираем только документы: " + ", ".join(DOC_KINDS))
    if p.get("parsed_json") and not force:
        out = json.loads(p["parsed_json"])
        out["из_кэша"] = True
        return out
    full = ROOT / p["path"]
    if not full.exists():
        raise HTTPException(404, "Файл не найден на диске")
    out = parse_file(full, p["mime"], kind, with_reg_no)
    con.execute("UPDATE photos SET doc_kind=?, parse_status=?, parsed_at=?, parsed_json=? WHERE id=?",
                (kind, out["статус"], db.now(), json.dumps(out, ensure_ascii=False), photo_id))
    db.audit(con, who, "разобран документ", f"request:{p['request_id']}",
             {"photo_id": photo_id, "doc_kind": kind, "статус": out["статус"],
              "найдено_полей": out.get("найдено_полей")})
    out["чек_лист"] = mark_received(con, p["request_id"], kind, p["path"], out, who)
    out["из_кэша"] = False
    out["photo_id"] = photo_id
    return out


@router.post("/documents/parse")
def parse_endpoint(body: ParseIn):
    """Разобрать загруженный документ. Результат сохраняется, повторный вызов берёт его из базы."""
    with db.tx() as con:
        return parse_photo(con, body.photo_id, body.doc_kind, body.force, body.who,
                           body.with_reg_no)


@router.get("/requests/{rid}/documents/fields")
def request_fields(rid: int):
    """
    Всё, что удалось извлечь из документов запроса, — для подстановки в блок «Стоимость объекта».
    При расхождении берём более свежий разбор.
    """
    with db.tx() as con:
        if not db.rows(con, "SELECT id FROM requests WHERE id=?", rid):
            raise HTTPException(404, "Запрос не найден")
        docs = db.rows(con, "SELECT id, doc_kind, parse_status, parsed_at, parsed_json FROM photos"
                            " WHERE request_id=? AND doc_kind<>? ORDER BY id", rid, KIND_PHOTO)
        merged, parsed, warn = {}, [], []
        for d in docs:
            if not d["parsed_json"]:
                parsed.append({"photo_id": d["id"], "вид_документа": d["doc_kind"],
                               "статус": d["parse_status"] or "не разобран"})
                continue
            p = json.loads(d["parsed_json"])
            p["photo_id"] = d["id"]
            parsed.append(p)
            merged.update(p.get("для_оценки") or {})
            warn.extend(p.get("предупреждения") or [])
        return {"request_id": rid, "для_оценки": merged, "предупреждения": warn,
                "документы": parsed, "чек_лист": checklist_state(con, rid)}


@router.get("/requests/{rid}/checklist")
def request_checklist(rid: int):
    """Чек-лист документов по запросу: что получено, что ещё нужно."""
    with db.tx() as con:
        if not db.rows(con, "SELECT id FROM requests WHERE id=?", rid):
            raise HTTPException(404, "Запрос не найден")
        return {"request_id": rid, "пункты": checklist_state(con, rid)}
