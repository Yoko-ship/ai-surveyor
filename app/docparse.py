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
import threading
import time
from contextlib import contextmanager
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db

router = APIRouter()


# --------------------------------------------------------------------------- #
# Срок разбора (app/act.py): проверяется внутри циклов поиска подписей, чтобы поток
# действительно освобождался. Срок не задан (все прежние вызовы) — проверка ничего не делает.
# --------------------------------------------------------------------------- #

class ParseTimeout(Exception):
    """Разбор документа не уложился в отведённое время."""


_deadline = threading.local()


@contextmanager
def deadline_scope(seconds: Optional[float]):
    """Срок разбора для текущего потока: tick() после него бросает ParseTimeout."""
    prev = getattr(_deadline, "at", None)
    _deadline.at = (time.monotonic() + max(0.0, float(seconds))) if seconds is not None else None
    if prev is not None and _deadline.at is not None:
        _deadline.at = min(prev, _deadline.at)       # вложенный срок не продлевает внешний
    try:
        yield
    finally:
        _deadline.at = prev


def tick() -> None:
    at = getattr(_deadline, "at", None)
    if at is not None and time.monotonic() > at:
        raise ParseTimeout()

ROOT = Path(__file__).resolve().parent.parent
LAWYER_NOTE = ROOT / "docs" / "Документы объекта — поля для автозаполнения.md"

KIND_PHOTO = "фото объекта"
KIND_PASSPORT = "техпаспорт"
KIND_CADASTRE = "кадастр"
# Виды, добавленные для приёма документов (app/ingest.py, задача заказчика 21.09.2026).
# Подписей полей в docparse для них нет — их разбирает ingest по словарям аналитика;
# здесь они нужны, чтобы файл можно было загрузить и отметить в чек-листе.
KIND_VALUATION = "отчёт оценщика"
KIND_CONTRACT = "договор"
KIND_STATEMENT = "выписка"
KIND_STAFF = "штатное расписание"
KIND_OTHER = "прочее"
EXTRA_KINDS = (KIND_VALUATION, KIND_CONTRACT, KIND_STATEMENT, KIND_STAFF, KIND_OTHER)
DOC_KINDS = (KIND_PASSPORT, KIND_CADASTRE)          # виды, для которых есть подписи полей здесь
ALL_KINDS = (KIND_PHOTO,) + DOC_KINDS + EXTRA_KINDS

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
         "labels": ["марка и модель", "марка, модель", "марка", "markasi va modeli",
                    "marka va model", "marka-rusumi", "markasi", "rusumi",
                    "маркаси ва модели", "марка ва модель", "маркаси", "русуми",
                    "brand and model", "make and model"]},
        {"key": "model", "name": "Модель, модификация", "type": "text", "to_valuation": "модель",
         "norm": TP_683,
         "labels": ["модель и модификация", "модель, модификация", "модель", "модели",
                    "model va modifikatsiyasi", "modeli", "model", "модель ва модификацияси"]},
        {"key": "year", "name": "Год выпуска", "type": "year", "to_valuation": "год",
         "norm": TP_141,
         "labels": ["год выпуска", "год изготовления", "дата выпуска",
                    "ishlab chiqarilgan yili", "ishlab chiqarilgan yil", "chiqarilgan yili",
                    "ишлаб чиқарилган йили", "ишлаб чиқарилган йил",
                    "ишлаб чикарилган йили", "чикарилган йили",
                    "year of manufacture", "year of production"]},
        {"key": "vehicle_type", "name": "Тип транспортного средства", "type": "text",
         "to_valuation": None, "norm": TP_683,
         "labels": ["тип транспортного средства", "категория транспортного средства", "тип тс",
                    "тип", "transport vositasining turi", "transport vositasining toifasi",
                    "tipi", "транспорт воситасининг тури", "транспорт воситасининг тоифаси",
                    "типи", "vehicle category", "vehicle type"]},
        {"key": "body_no", "name": "Номер кузова", "type": "vin", "to_valuation": None,
         "norm": TP_683 + "; " + TP_141,
         "labels": ["номер кузова (шасси)", "номер кузова", "kuzov (shassi) raqami",
                    "kuzov raqami", "кузов (шасси) рақами", "кузов рақами", "кузов раками",
                    "body number"]},
        # Отдельной графы VIN в ПКМ № 683 нет (там «идентификационные номера кузова, шасси
        # и двигателя»); VIN назван только в ЕНСО — открытый вопрос из заметки юриста.
        {"key": "vin", "name": "Идентификационный номер (VIN)", "type": "vin",
         "to_valuation": "vin", "norm": TP_ENSO,
         "labels": ["идентификационный номер (vin)", "идентификационный номер", "vin-код", "vin",
                    "identifikatsiya raqami", "vin-kod", "идентификация рақами",
                    "идентификация раками", "vin number"]},
        {"key": "chassis_no", "name": "Номер шасси (рамы)", "type": "vin", "to_valuation": None,
         "norm": TP_683,
         "labels": ["номер шасси (рамы)", "номер шасси", "номер рамы",
                    "shassi (rama) raqami", "shassi raqami", "rama raqami",
                    "шасси (рама) рақами", "шасси рақами", "шасси (рама) раками",
                    "chassis number"]},
        {"key": "engine_no", "name": "Номер двигателя", "type": "vin", "to_valuation": None,
         "norm": TP_683 + "; " + TP_141,
         "labels": ["номер двигателя", "dvigatel raqami", "двигатель рақами",
                    "двигатель раками", "engine number"]},
        {"key": "color", "name": "Цвет", "type": "text", "to_valuation": None,
         "norm": "ПКМ № 683, п. 53",
         "labels": ["цвет окраски", "цвет", "rangi", "bo'yog'i rangi", "ранги", "colour", "color"]},
        {"key": "seats", "name": "Число сидений", "type": "int", "to_valuation": None,
         "norm": TP_683,
         "labels": ["количество сидений", "число сидений", "количество мест",
                    "число посадочных мест", "o'rindiqlar soni", "o'tirg'ichlar soni",
                    "ўриндиқлар сони", "уриндиклар сони", "number of seats"]},
        # ЕНСО объём двигателя требует, а в перечне ПКМ № 683 его нет — открытый вопрос
        {"key": "engine_cc", "name": "Рабочий объём двигателя, см³", "type": "number",
         "to_valuation": None, "norm": TP_ENSO,
         "labels": ["рабочий объём двигателя", "рабочий объем двигателя", "объём двигателя",
                    "объем двигателя", "dvigatel hajmi", "dvigatel ish hajmi",
                    "двигатель ҳажми", "двигатель хажми",
                    "engine capacity", "engine displacement"]},
        # Вид топлива, мощность и масса (02.10.2026, автозаполнение акта: fuel и характеристики объекта).
        # В перечне ПКМ № 683 этих граф нет — берём, только если они есть в бланке (как регион учёта).
        {"key": "fuel", "name": "Вид топлива", "type": "text", "to_valuation": None, "norm": None,
         "labels": ["вид топлива", "тип топлива", "вид двигателя", "тип двигателя", "топливо",
                    "yoqilg'i turi", "yoqilg'i", "ёқилғи тури", "ёкилги тури", "ёқилғи",
                    "fuel type", "type of fuel", "fuel"]},
        {"key": "engine_power", "name": "Мощность двигателя", "type": "text", "to_valuation": None,
         "norm": None,
         "labels": ["мощность двигателя", "мощность", "dvigatel quvvati", "quvvati",
                    "двигатель қуввати", "двигатель куввати", "қуввати", "engine power", "power"]},
        {"key": "max_mass", "name": "Масса (разрешённая максимальная / без нагрузки)", "type": "text",
         "to_valuation": None, "norm": None,
         "labels": ["разрешенная максимальная масса", "максимальная масса", "полная масса",
                    "масса без нагрузки", "снаряженная масса", "масса",
                    "ruxsat etilgan maksimal vazn", "to'la vazni", "yuksiz vazni", "vazni",
                    "рухсат этилган максимал вазн", "тўла вазни", "тула вазни", "вазни",
                    "maximum permissible mass", "gross vehicle weight", "unladen mass", "mass"]},
        # Такой графы нормативные акты не называют. Берём, только если она есть в бланке;
        # из государственного номера регион НЕ выводим — это догадка, а не данные документа.
        {"key": "region", "name": "Регион учёта", "type": "region", "to_valuation": "регион",
         "norm": None,
         "labels": ["регион регистрации", "место регистрации", "регион", "область",
                    "ro'yxatga olingan joyi", "viloyat", "viloyati",
                    "рўйхатга олинган жойи", "вилоят", "вилояти",
                    "region of registration", "region"]},
    ],
    KIND_CADASTRE: [
        {"key": "cadastre_no", "name": "Кадастровый номер", "type": "text", "to_valuation": None,
         "norm": KAD_803,
         "labels": ["кадастровый номер", "кадастровый №", "kadastr raqami", "кадастр рақами",
                    "кадастр раками", "cadastral number"]},
        {"key": "address", "name": "Местонахождение объекта", "type": "text",
         "to_valuation": "адрес", "norm": KAD_803 + "; " + KAD_FORM,
         "labels": ["адрес объекта", "местонахождение", "место нахождения", "местоположение",
                    "почтовый адрес", "адрес",
                    "obyekt manzili", "joylashgan joyi", "joylashgan yeri", "pochta manzili",
                    "manzili", "manzil",
                    "объект манзили", "жойлашган жойи", "почта манзили", "манзили", "манзил",
                    "object address", "address", "location"]},
        {"key": "region", "name": "Регион", "type": "region", "to_valuation": "регион",
         "norm": None,
         "labels": ["регион", "область", "район", "viloyat", "viloyati", "tuman", "tumani",
                    "вилоят", "вилояти", "туман", "тумани", "region", "district"]},
        {"key": "object_kind", "name": "Вид объекта недвижимости", "type": "text",
         "to_valuation": "тип", "norm": KAD_FORM,
         "labels": ["вид объекта недвижимости", "наименование объекта недвижимости",
                    "наименование объекта", "вид объекта", "тип объекта", "назначение",
                    "ko'chmas mulk obyekti turi", "ko'chmas mulk obyektining nomi",
                    "obyekt turi", "кўчмас мулк объекти тури", "кучмас мулк объекти тури",
                    "объект тури", "type of real estate object", "property type"]},
        {"key": "area_m2", "name": "Площадь зданий и сооружений, кв. м", "type": "area",
         "to_valuation": "площадь", "norm": KAD_FORM,
         "labels": ["площадь зданий и сооружений", "общая площадь", "площадь объекта",
                    "площадь, кв. м", "площадь", "umumiy maydoni", "umumiy maydon",
                    "bino va inshootlar maydoni", "maydoni, kv.m", "maydoni",
                    "умумий майдони", "умумий майдон", "бино ва иншоотлар майдони", "майдони",
                    "total area", "area"]},
        {"key": "land_area_ha", "name": "Площадь земельного участка, га", "type": "number",
         "to_valuation": None, "norm": KAD_FORM,
         "labels": ["площадь земельного участка", "площадь участка", "площадь, га",
                    "yer uchastkasi maydoni", "yer uchastkasining maydoni", "maydoni, ga",
                    "ер участкасининг майдони", "майдони, га", "land plot area"]},
        {"key": "right_kind", "name": "Вид права", "type": "text", "to_valuation": None,
         "norm": KAD_803,
         "labels": ["вид права", "huquq turi", "ҳуқуқ тури", "хукук тури", "type of right"]},
        # DOC-KAD-02: кадастровая стоимость не равна страховой — в оценку её не подставляем
        {"key": "cadastral_value", "name": "Кадастровая стоимость, тыс. сум", "type": "number",
         "to_valuation": None, "norm": KAD_803,
         "labels": ["кадастровая стоимость", "kadastr bahosi", "kadastr qiymati",
                    "кадастр баҳоси", "кадастр бахоси", "cadastral value"]},
        {"key": "encumbrance", "name": "Запрет, арест, ограничения", "type": "text",
         "to_valuation": None, "norm": KAD_803,
         "labels": ["наличие запрета, ареста или ограничений", "ограничения", "обременения",
                    "арест", "taqiq, xatlov yoki cheklovlar mavjudligi", "cheklovlar",
                    "тақиқ, хатлов ёки чекловлар мавжудлиги", "чекловлар",
                    "такик, хатлов ёки чекловлар мавжудлиги",
                    "encumbrances", "restrictions"]},
        {"key": "mortgage", "name": "Сведения об ипотеке", "type": "text", "to_valuation": None,
         "norm": KAD_803,
         "labels": ["сведения об ипотеке", "ипотека", "ipoteka", "ipoteka to'g'risida ma'lumot",
                    "ипотека тўғрисида маълумот", "mortgage"]},
        # Числа комнат в госреестре нет (ЗРУ-803, ст. 23): бывает в плане-экспликации
        # кадастрового дела — берём, если встретилось.
        {"key": "rooms", "name": "Число комнат", "type": "int", "to_valuation": "комнаты",
         "norm": None,
         "labels": ["число комнат", "количество комнат", "комнат", "xonalar soni",
                    "хоналар сони", "number of rooms"]},
        # Года ввода в эксплуатацию в реестре нет (ЗРУ-803, ст. 23; ЕНСО, п. 592)
        {"key": "build_year", "name": "Год постройки", "type": "year", "to_valuation": "год",
         "norm": None,
         "labels": ["год постройки", "год ввода в эксплуатацию", "qurilgan yili",
                    "foydalanishga topshirilgan yili", "ishga tushgan yili",
                    "қурилган йили", "ишга тушган йили", "курилган йили",
                    "year built", "year of construction"]},
        {"key": "floors", "name": "Этажность", "type": "int", "to_valuation": None, "norm": None,
         "labels": ["этажность", "число этажей", "количество этажей", "qavatlar soni",
                    "қаватлар сони", "каватлар сони", "number of floors"]},
        {"key": "walls", "name": "Материал стен", "type": "text", "to_valuation": None,
         "norm": None,
         "labels": ["материал стен", "стены", "devor materiali", "девор материали",
                    "wall material"]},
    ],
}

# «Запрос филиала» (образцы заказчика 30.09.2026): таблица из 16 строк. Подписи — узбекская кириллица
# (как в бланке), латиница и русские варианты. Разбирает app/branch_request.py (свёртка — fold()).
# Порядок = номер строки бланка. Стороны договора (2–4) — только названия юрлиц, см. branch_request.party.
KIND_BRANCH_REQUEST = "запрос филиала"
BRANCH_REQUEST_ROWS = [
    ("product_code", ["суғурта тури (буйруқ бўйича код)", "суғурта тури", "суғурта турининг коди",
                      "sug'urta turi (buyruq bo'yicha kod)", "sug'urta turi",
                      "вид страхования (код по приказу)", "вид страхования (код)", "вид страхования",
                      "код продукта"]),
    ("policyholder", ["суғурта қилдирувчи номи", "суғурта қилдирувчи", "sug'urta qildiruvchi nomi",
                      "sug'urta qildiruvchi", "наименование страхователя", "страхователь"]),
    ("beneficiary", ["наф олувчи", "naf oluvchi", "выгодоприобретатель"]),
    ("pledger", ["гаровга қўювчи", "гаровга кўювчи", "garovga qo'yuvchi", "залогодатель"]),
    ("object", ["суғурта объекти", "sug'urta obyekti", "sug'urta ob'ekti", "объект страхования"]),
    ("object_value", ["суғурта қиймати", "sug'urta qiymati", "страховая стоимость"]),
    ("sum_insured", ["суғурта суммаси", "sug'urta summasi", "страховая сумма"]),
    ("franchise", ["франшиза", "franshiza"]),
    ("tariff", ["суғурта тарифи", "sug'urta tarifi", "страховой тариф", "тариф страхования", "тариф"]),
    ("premium", ["суғурта мукофоти", "sug'urta mukofoti", "страховая премия"]),
    ("term", ["суғурта муддати", "sug'urta muddati", "срок страхования", "срок действия договора"]),
    ("contract_terms", ["стандарт суғурта шартномаси шартларини ўзгартириш", "стандарт суғурта шартномаси",
                        "standart sug'urta shartnomasi shartlarini o'zgartirish", "standart sug'urta shartnomasi",
                        "изменение/дополнение стандартных условий договора страхования",
                        "изменение стандартных условий", "изменения стандартных условий",
                        "стандартные условия договора"]),
    ("counterparty", ["контрагент", "kontragent"]),
    ("contracts_count", ["шартнома миқдори", "shartnoma miqdori", "количество договоров", "число договоров"]),
    ("osgor_class", ["класс (осгор бўйича)", "класс", "klass (osgor bo'yicha)", "klass", "sinf",
                     "класс по осгор"]),
    ("additional_info", ["қўшимча маълумот", "qo'shimcha ma'lumot", "дополнительная информация",
                         "дополнительные сведения"]),
]

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
               "давлат рақами белгиси", "давлат рақами",
               "давлат раками белгиси", "давлат раками",
               "registration plate number", "plate number"],
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
    # стороны договора и анкеты: значение такой строки — данные человека
    "страхователь", "застрахованный", "застрахованное лицо", "выгодоприобретатель",
    "заявитель", "гражданин", "гражданка", "потерпевший", "водитель", "доверенность", "залогодатель",
    "garovga qo'yuvchi", "гаровга қўювчи",
    "familiyasi", "ismi", "otasining ismi", "f.i.sh", "fish", "egasi", "mulkdor",
    "huquq egasi", "huquqdagi ulushi", "pasport", "jshshir", "stir",
    "tug'ilgan sanasi", "yashash manzili", "imzo",
    "sug'urta qildiruvchi", "sugurta qildiruvchi", "sug'urtalangan shaxs", "naf oluvchi",
    "fuqaro", "ariza beruvchi", "haydovchi", "ishonchnoma",
    "фамилияси", "исми", "отасининг исми", "эгаси", "мулкдор", "хукук эгаси",
    "ҳуқуқ эгаси", "ҳуқуқдаги улуши", "туғилган санаси", "паспорти", "яшаш манзили", "имзо",
    "суғурта қилдирувчи", "сугурта килдирувчи", "суғурталанган шахс", "наф олувчи",
    "фуқаро", "ариза берувчи", "ҳайдовчи", "ишончнома",
    # английские подписи: в переводных бланках и в штатных расписаниях на английском
    "full name", "surname", "first name", "patronymic", "employee name", "owner",
    "right holder", "passport", "id card", "date of birth", "place of birth",
    "residence address", "home address", "signature", "taxpayer id",
    "policyholder", "insured person", "beneficiary", "applicant", "driver",
    "power of attorney",
]

# Подписи адреса ЧЕЛОВЕКА. Отдельный список: адрес объекта — характеристика имущества,
# он нужен для оценки (см. шапку модуля), поэтому строку с адресом бросаем только тогда,
# когда в ней нет признака объекта (OBJECT_LABELS ниже).
PERSONAL_ADDR_LABELS = [
    "адрес проживания", "адрес регистрации", "адрес прописки", "домашний адрес",
    "фактический адрес", "адрес места жительства", "место проживания", "место регистрации",
    "проживает по адресу", "зарегистрирован по адресу", "постоянное место жительства",
    "почтовый адрес заявителя", "контактный адрес", "адрес физического лица",
    "yashash joyi", "yashash manzil", "doimiy yashash manzili", "turar joy manzili",
    "ro'yxatdan o'tgan manzil", "ro'yxatga olingan manzil", "haqiqiy manzil",
    "uy manzili", "shaxsiy manzil", "manzilda yashaydi",
    "яшаш жойи", "яшаш манзил", "доимий яшаш манзили", "турар жой манзили",
    "рўйхатдан ўтган манзил", "руйхатдан утган манзил", "ҳақиқий манзил",
    "уй манзили", "шахсий манзил", "манзилда яшайди",
    "residential address", "registered address", "home address", "place of residence",
    "address of residence", "permanent address", "actual address", "personal address",
]

# Признаки того, что строка описывает ОБЪЕКТ, а не человека: адрес объекта сохраняем
# (ЗРУ-803, ст. 23 — местонахождение объекта входит в сведения госреестра).
OBJECT_LABELS = [
    "объект", "объекта", "объекти", "имущества", "имущество", "недвижимост", "здания", "здание",
    "строения", "помещения", "квартиры", "земельного участка", "местонахождение",
    "местоположение", "место нахождения", "страхования",
    "obyekt", "ob'ekt", "mulk", "ko'chmas mulk", "bino", "inshoot", "joylashgan", "joylashuvi",
    "yer uchastkasi", "sug'urta obyekti",
    "объект манзили", "кўчмас мулк", "кучмас мулк", "бино", "иншоот", "жойлашган",
    "ер участкаси", "суғурта объекти",
    "object", "property", "real estate", "building", "premises", "location", "land plot",
    "insured object",
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
    "тошкент вилояти": "Ташкентская область",
    "ташкент": "город Ташкент", "toshkent": "город Ташкент", "тошкент": "город Ташкент",
    "ферганская": "Ферганская область", "фергана": "Ферганская область",
    "farg'ona": "Ферганская область",
    "хорезмская": "Хорезмская область", "ургенч": "Хорезмская область",
    "xorazm": "Хорезмская область",
}


# Кэш по строке: поиск подписей проходит каждую строку документа для каждого поля (десятки раз),
# и без кэша свёртка одной и той же строки считалась заново — разбор XLSX 200×60 шёл 30+ секунд.
# Длинные тексты (весь документ целиком) не кэшируются: они считаются один раз и заняли бы память.
FOLD_CACHE_MAX_LEN = 4000
FOLD_CACHE_SIZE = 16384


def norm(text: str) -> str:
    """
    Текст к единому виду для сравнения подписей: нижний регистр, один апостроф, без ё.

    Поведение этой функции намеренно НЕ меняется: на него опираются словари определения языка
    (docs/ingest_dicts.json, токены «[a-zа-яўқғҳ']+» — кириллица и апостроф обязаны сохраниться).
    Свёртка узбекской латиницы и кириллицы сделана отдельной функцией fold() ниже.
    """
    if text and isinstance(text, str) and len(text) <= FOLD_CACHE_MAX_LEN:
        return _norm_cached(text)
    return _norm_raw(text)


@lru_cache(maxsize=FOLD_CACHE_SIZE)
def _norm_cached(text: str) -> str:
    return _norm_raw(text)


def _norm_raw(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    for a in APOSTROPHES:
        t = t.replace(a, "'")
    t = t.replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


# --------------------------------------------------------------------------- #
# Свёртка написаний: узбекская латиница ↔ кириллица ↔ русский
# Правило и таблица — docs/i18n_terms.json → normalization (заметка юриста).
# Зачем: «Sugʻurta polisi», «Sug'urta polisi», «Суғурта полиси» и «sugurta polisi» — одно и то же.
# --------------------------------------------------------------------------- #
CYRL_TO_LATN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ғ": "g", "д": "d", "е": "e", "ж": "j",
    "з": "z", "и": "i", "й": "y", "к": "k", "қ": "q", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ў": "o", "ф": "f",
    "х": "x", "ҳ": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
    # ё в таблице нет: его раньше сворачивает norm() в «е» (русские «всё» и «все» — одно слово)
}
FOLD_PUNCT = "-—–.,;:№()[]{}/\\\"«»|_"

# --------------------------------------------------------------------------- #
# Английские слова из-под узбекских правил свёртки выводим: иначе year→ear, owner→ovner,
# costs→coss, tax→tah. Решение принимается ПО СЛОВУ, а не по всему тексту: в двуязычном бланке
# английская подпись должна свернуться одинаково и в документе, и в маркере словаря — иначе
# совпадения потеряются. Безопасная часть свёртки (регистр, апострофы, знаки) применяется всегда.
# --------------------------------------------------------------------------- #
EN_WORDS = {
    # служебные и частые
    "the", "and", "for", "of", "in", "on", "at", "by", "to", "from", "with", "without", "per",
    "is", "are", "was", "were", "be", "as", "or", "not", "no", "this", "that", "each", "all",
    "new", "old", "own", "other", "than", "then", "when", "where", "which", "who", "how",
    # документ, страхование, имущество
    "owner", "owners", "ownership", "year", "years", "yearly", "yield", "date", "dated",
    "name", "surname", "address", "passport", "phone", "number", "no", "code", "type", "kind",
    "document", "documents", "certificate", "registration", "report", "valuation", "appraisal",
    "contract", "agreement", "policy", "insurance", "insured", "insurer", "premium", "rate",
    "sum", "amount", "total", "subtotal", "cost", "costs", "price", "prices", "value", "values",
    "currency", "bank", "account", "payment", "payments", "tax", "taxes", "vat", "salary",
    "wage", "wages", "staff", "employee", "employees", "position", "positions", "department",
    "headcount", "list", "table", "sheet", "page", "note", "notes", "comment", "comments",
    "vehicle", "car", "make", "model", "brand", "engine", "power", "colour", "color", "body",
    "chassis", "seats", "weight", "capacity", "manufacture", "manufactured", "manufacturer",
    "production", "property", "building", "buildings", "construction", "area", "floor", "floors",
    "rooms", "room", "apartment", "house", "land", "plot", "wall", "walls", "roof", "window",
    "windows", "warehouse", "works", "work", "width", "height", "expiry", "expires", "issued",
    "issue", "valid", "until", "since", "term", "period", "start", "end", "limit", "excess",
    "deductible", "risk", "risks", "object", "objects", "extract", "register", "cadastre",
    "cadastral", "state", "republic", "region", "district", "city", "street", "unit", "units",
    "quantity", "index", "max", "min", "next", "text", "example", "exchange", "extension",
    "annex", "appendix", "signature", "stamp", "director", "manager", "company", "branch",
}
# окончания, которых в узбекской латинице практически не бывает
EN_SUFFIX = ("tion", "sion", "ment", "ance", "ence", "ture", "ity", "ness", "able", "ible",
             "ship", "ing", "ly", "ful", "ous")


def _is_english_word(w: str) -> bool:
    if not w or not w.isascii() or not w.isalpha():
        return False
    if w in EN_WORDS:
        return True
    if w.endswith("s") and w[:-1] in EN_WORDS:
        return True
    return len(w) >= 5 and w.endswith(EN_SUFFIX)


_WORD_RX = re.compile(r"[a-z0-9']+")


def _english_flags(src: str, lang: str = None) -> List[bool]:
    """Для каждой позиции строки: относится ли она к английскому слову (узбекские правила — мимо)."""
    if lang:
        return [lang == "en"] * len(src)
    flags = [False] * len(src)
    for m in _WORD_RX.finditer(src):
        if _is_english_word(m.group(0).replace("'", "")):
            for i in range(m.start(), m.end()):
                flags[i] = True
    return flags


def fold_map(text: str, lang: str = None):
    """
    Свёрнутый текст и карта «позиция в свёрнутом → позиция в исходном».
    Карта нужна, чтобы после совпадения подписи взять значение из ИСХОДНОЙ строки: свёртка
    меняет длину (ц→ts, ъ→пусто), поэтому считать смещение по свёрнутому тексту нельзя.

    lang: "en" — узбекские правила не применять совсем; другой язык — применять ко всему;
    не указан — решаем по каждому слову (см. _english_flags).
    """
    if text and isinstance(text, str) and len(text) <= FOLD_CACHE_MAX_LEN:
        folded, idx = _fold_map_cached(text, lang)
        return folded, list(idx)           # копия: вызывающий получает свой список, как раньше
    return _fold_map_raw(text, lang)


@lru_cache(maxsize=FOLD_CACHE_SIZE)
def _fold_map_cached(text: str, lang: Optional[str]):
    folded, idx = _fold_map_raw(text, lang)
    return folded, tuple(idx)


def _fold_map_raw(text: str, lang: str = None):
    src = norm(text)                       # нижний регистр, ё→е, один апостроф
    en_src = _english_flags(src, lang)
    buf, idx, en = [], [], []
    for i, ch in enumerate(src):
        if ch == "'":                      # апостроф убираем совсем: oʻ → o, maʼlumot → malumot
            continue
        for c in CYRL_TO_LATN.get(ch, ch):
            buf.append(c)
            idx.append(i)
            en.append(en_src[i])
    # свёртка различий латиницы: x→h, ts→s, начальное ye→e, w→v (только для неанглийских слов)
    buf2, idx2, i = [], [], 0
    while i < len(buf):
        c = buf[i]
        if en[i]:
            buf2.append(c); idx2.append(idx[i]); i += 1
        elif c == "x":
            buf2.append("h"); idx2.append(idx[i]); i += 1
        elif c == "w":
            buf2.append("v"); idx2.append(idx[i]); i += 1
        elif c == "t" and i + 1 < len(buf) and buf[i + 1] == "s":
            buf2.append("s"); idx2.append(idx[i]); i += 2
        elif (c == "y" and i + 1 < len(buf) and buf[i + 1] == "e"
              and (i == 0 or not buf[i - 1].isalnum())):
            buf2.append("e"); idx2.append(idx[i]); i += 2
        else:
            buf2.append(c); idx2.append(idx[i]); i += 1
    # знаки препинания и повторяющиеся пробелы → один пробел
    out, oidx, space = [], [], True
    for c, k in zip(buf2, idx2):
        if c in FOLD_PUNCT or c.isspace():
            if not space:
                out.append(" "); oidx.append(k); space = True
            continue
        out.append(c); oidx.append(k); space = False
    while out and out[-1] == " ":
        out.pop(); oidx.pop()
    return "".join(out), oidx


def fold(text: str, lang: str = None) -> str:
    """Свёрнутое написание строки — для сравнения подписей и маркеров на четырёх написаниях."""
    if text and isinstance(text, str) and len(text) <= FOLD_CACHE_MAX_LEN:
        return _fold_map_cached(text, lang)[0]
    return _fold_map_raw(text, lang)[0]


def _normalize_labels():
    """Подписи в таблицах пишутся людьми — приводим их к тому же виду, что и текст документа."""
    for lst in FIELDS.values():
        for f in lst:
            f["labels"] = [norm(x) for x in f["labels"]]
            f["folded"] = sorted({fold(x) for x in f["labels"] if fold(x)}, key=len, reverse=True)
    REG_NO_FIELD["labels"] = [norm(x) for x in REG_NO_FIELD["labels"]]
    REG_NO_FIELD["folded"] = sorted({fold(x) for x in REG_NO_FIELD["labels"] if fold(x)},
                                    key=len, reverse=True)


_normalize_labels()
PERSONAL_LABELS = [norm(x) for x in PERSONAL_LABELS]
# свёрнутые подписи ПД: короткие выбрасываем — «инн» → «inn» совпал бы внутри обычных слов
PERSONAL_FOLDED = sorted({fold(x) for x in PERSONAL_LABELS if len(fold(x)) >= 4},
                         key=len, reverse=True)
PERSONAL_ADDR_LABELS = [norm(x) for x in PERSONAL_ADDR_LABELS]
PERSONAL_ADDR_FOLDED = sorted({fold(x) for x in PERSONAL_ADDR_LABELS if len(fold(x)) >= 4},
                              key=len, reverse=True)
OBJECT_LABELS = [norm(x) for x in OBJECT_LABELS]
OBJECT_FOLDED = sorted({fold(x) for x in OBJECT_LABELS if len(fold(x)) >= 4},
                       key=len, reverse=True)
REGIONS = {norm(k): v for k, v in REGIONS.items()}
REGIONS_FOLDED = {fold(k): v for k, v in REGIONS.items() if fold(k)}


# Без разделителя подпись и значение стоят через пробел («Адрес проживания Самарканд
# улица Беруни здание 44») — так часто отдаёт текст из PDF. Подписью считаем первые слова.
LABEL_WORDS = 3
LABEL_LINE_WORDS = 5             # короткая строка без цифр — это сама подпись, а не подпись+значение


def label_part(line: str) -> str:
    """
    Подпись строки — текст до «:» или до « | ». Признак объекта ищем только в ней:
    в узбекских адресах слова «здание», «квартира», «помещения», bino стоят в самом
    адресе проживания и иначе отменяли бы персональную проверку.

    Разделителя нет — берём первые LABEL_WORDS слов: иначе «здание» из значения
    («Адрес проживания Самарканд улица Беруни здание 44») отменяло бы персональную
    проверку. Короткая строка без цифр — это подпись бланка целиком («Location of
    the property»), её не режем.
    """
    s = line or ""
    cuts = [p for p in (s.find(":"), s.find(" | ")) if p >= 0]
    if cuts:
        return s[:min(cuts)]
    words = s.split()
    if len(words) <= LABEL_LINE_WORDS and not any(c.isdigit() for c in s):
        return s
    return " ".join(words[:LABEL_WORDS])


def is_object_label(line: str) -> bool:
    """Подпись строки описывает объект (имущество), а не человека."""
    head = label_part(line)
    n = norm(head)
    if any(p in n for p in OBJECT_LABELS):
        return True
    return any(p in fold(head) for p in OBJECT_FOLDED)


def is_personal_label(line: str) -> bool:
    """Строка подписана как персональные данные — значение из неё не берём (PD-01)."""
    if line and isinstance(line, str) and len(line) <= FOLD_CACHE_MAX_LEN:
        return _is_personal_cached(line)
    return _is_personal_raw(line)


@lru_cache(maxsize=FOLD_CACHE_SIZE)
def _is_personal_cached(line: str) -> bool:
    return _is_personal_raw(line)


def _is_personal_raw(line: str) -> bool:
    n = norm(line)
    f = fold(line)                       # второй заход: другое написание той же подписи
    if any(p in n for p in PERSONAL_LABELS) or any(p in f for p in PERSONAL_FOLDED):
        return True
    # адрес: подпись человека («адрес проживания») отбрасываем, подпись объекта
    # («местонахождение объекта») оставляем — это характеристика имущества
    if any(p in n for p in PERSONAL_ADDR_LABELS) or any(p in f for p in PERSONAL_ADDR_FOLDED):
        return not is_object_label(line)
    return False


# Остаток подписи, попавший в значение: «Местонахождение объекта: г. Ташкент» → подпись
# совпала только словом «местонахождение», и в хвосте остаётся «объекта: ».
_TAIL_LABEL = re.compile(r"^[^\d:|\n]{1,40}:\s*")

# Слова, которыми продолжается подпись и которые иначе попадают в начало значения
# («Местонахождение объекта Ташкент …» → в адресе оставалось «объекта Ташкент …»).
# Режем не больше TAIL_LABEL_MAX таких слов подряд и только там, где двоеточия в строке нет.
# Двух не хватало: «Location of the property / Tashkent…» — хвост «of the property» из трёх
# слов, и в адрес попадало значение «property». Если хвост срезан весь, значение пустое —
# find_field возьмёт его со следующей строки (уверенность «средняя»).
TAIL_LABEL_MAX = 4
TAIL_LABEL_WORDS = {"объекта", "объекти", "обьекта", "объекту", "страхования", "имущества",
                    "недвижимости", "здания", "жительства", "проживания", "регистрации",
                    "obyekti", "obyektining", "manzili", "joyi", "sugurta", "sug'urta",
                    "mulki", "property", "object", "address", "of", "the",
                    "insured", "застрахованного", "страхуемого", "sugurtalangan"}


def cut_tail(tail: str) -> str:
    """
    Чистит хвост строки после подписи: в строке таблицы берёт следующую ячейку,
    в обычной строке отбрасывает остаток подписи до двоеточия. Цифры до двоеточия
    не трогаем — это время («10:09») или кадастровый номер.
    """
    t = tail or ""
    if " | " in t:
        return t.split(" | ", 1)[1].strip()
    out = _TAIL_LABEL.sub("", t, count=1)
    if ":" in t:
        return out
    # разделителя нет: остаток подписи стоит перед значением через пробел
    # («Местонахождение объекта Ташкент …» → подпись совпала словом «местонахождение»)
    words = out.split()
    cut = 0
    while (cut < len(words) and cut < TAIL_LABEL_MAX
           and norm(words[cut]).strip(",.") in TAIL_LABEL_WORDS):
        cut += 1
    return " ".join(words[cut:]) if cut else out


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
        # порядок словаря значения не имеет: берём совпадение, которое стоит раньше в строке,
        # а при равном положении — самое длинное. Иначе «город Самарканд, улица Навои»
        # давал бы Навоийскую область, и территориальный коэффициент был бы чужим
        best = None
        for text, table in ((norm(v), REGIONS), (fold(v), REGIONS_FOLDED)):
            for key, name in table.items():
                pos = text.find(key)
                if pos < 0:
                    continue
                cand = (pos, -len(key), name)
                if best is None or cand[:2] < best[:2]:
                    best = cand
            if best is not None:
                return best[2]            # нашли в обычном написании — свёрнутое не нужно
        return None
    if kind == "vin":
        m = re.search(r"\b([A-HJ-NPR-Z0-9]{9,17})\b", v.upper())
        return m.group(1) if m else None
    if kind == "reg_no":
        m = re.search(r"\b(\d{2}\s?[A-ZА-Я]\s?\d{3}\s?[A-ZА-Я]{2})\b", v.upper())
        return re.sub(r"\s+", " ", m.group(1)) if m else None
    return v or None


def _tail_after_label(line: str, labels: List[str], folded: bool) -> Optional[tuple]:
    """Хвост строки после подписи. folded=True — сравнение по свёрнутому написанию (fold)."""
    if folded:
        n, idx = fold_map(line)
    else:
        n, idx = norm(line), None
    for lab in labels:
        if not lab:
            continue
        hit = n.startswith(lab) or f" {lab}" in n or (not folded and f"{lab}:" in n)
        if not hit:
            continue
        end = n.find(lab) + len(lab)
        if idx is not None:                         # свёртка меняет длину — идём по карте позиций
            pos = (idx[end - 1] + 1) if end - 1 < len(idx) else len(line)
        else:
            pos = end                               # прежнее поведение: смещение по norm()
        tail = line[pos:] if pos <= len(line) else ""
        return lab, tail
    return None


# публичное имя для app/ingest.py: тот же поиск хвоста строки после подписи
tail_after_label = _tail_after_label


def find_field(lines: List[str], field: dict) -> Optional[dict]:
    """
    Ищет значение поля по подписям. Значение берём справа от подписи (после двоеточия),
    а если справа пусто — со следующей непустой строки (в бланках так свёрстаны таблицы).
    Уверенность: «высокая» — значение на той же строке, что и подпись;
    «средняя» — значение взято со следующей строки.

    Два прохода: сначала по norm() (как было), затем по fold() — он ловит то же самое поле,
    написанное иначе: «Sugʻurta», «Sug'urta», «Суғурта», «sugurta» (docs/i18n_terms.json).
    """
    labels = sorted(field["labels"], key=len, reverse=True)
    folded_labels = field.get("folded") or []
    for use_fold in (False, True):
        use = folded_labels if use_fold else labels
        if not use:
            continue
        for i, line in enumerate(lines):
            tick()
            if is_personal_label(line):             # строка про человека — пропускаем целиком
                continue
            got = _tail_after_label(line, use, use_fold)
            if not got:
                continue
            lab, tail = got
            tail = SEP.sub("", tail, count=1) if SEP.match(tail) else tail
            tail = cut_tail(tail)
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


def parse_text(text: str, doc_kind: str, with_reg_no: bool = False, use_llm: bool = True) -> dict:
    """Разбор готового текста документа. Отдельно от чтения файла — так его удобно проверять тестом.
    use_llm=False — без ИИ-дозаполнения (сюрвейерский акт разбирает документы только подписями)."""
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

    ai = ai_fill(fields, values, for_valuation, text, doc_kind) if use_llm else {}
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
    # виды из app/ingest.py: формулировки берутся из справочника checklists, если они там есть;
    # нет подходящего пункта — mark_received честно вернёт «не отмечен» и причину
    KIND_VALUATION: ["отчёт об оценке", "отчет об оценке", "отчёт оценщика",
                     "документ о стоимости объекта"],
    KIND_CONTRACT: ["договор", "договор купли-продажи", "правоустанавливающий документ",
                    "документ о праве на объект"],
    KIND_STATEMENT: ["выписка", "выписка из государственного реестра",
                     "банковская выписка", "документ о праве на объект"],
    KIND_STAFF: ["штатное расписание", "сведения о численности работников"],
    KIND_OTHER: [],
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
