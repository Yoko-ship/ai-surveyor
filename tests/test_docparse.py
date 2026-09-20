"""
Контрольные примеры разбора документов объекта.
Запуск:  sandbox\\.venv\\Scripts\\python.exe tests/test_docparse.py

Офлайн: PDF-файлы для проверки создаём сами через pymupdf, сеть не нужна.
Проверяем:
  1) техпаспорт автомобиля с текстовым слоем — марка, модель, год, номера агрегатов;
  2) кадастровый документ (русские и узбекские подписи) — местонахождение, площадь, вид объекта;
  3) скан без текстового слоя и фотографию — статус «нужно распознавание», поля пустые;
  4) персональные данные (ЗРУ-547) в ответ не попадают;
  5) загрузку документа, сохранение разбора и отметку пункта чек-листа.

Подписи полей в примерах — по docs/Документы объекта — поля для автозаполнения.md
(нормативные термины; образцов бланков у нас нет).
Записи, созданные тестом в рабочей базе, удаляются в конце.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf                                            # noqa: E402

from app import db                                        # noqa: E402
from app import docparse as D                             # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="docparse_"))
MADE_REQUESTS = []
MADE_PHOTOS = []

# --------------------------------------------------------------------------- #
# Типовые тексты документов. Персональные данные в них есть намеренно:
# проверяем, что разбор их НЕ берёт.
# --------------------------------------------------------------------------- #

PASSPORT_TEXT = """СВИДЕТЕЛЬСТВО О РЕГИСТРАЦИИ АВТОМОТОТРАНСПОРТНОГО СРЕДСТВА
Государственный регистрационный номер: 01 A 123 AB
Тип транспортного средства: легковой
Марка: Chevrolet
Модель и модификация: Cobalt LTZ
Год выпуска: 2019
Цвет окраски: белый
Рабочий объём двигателя: 1485
Номер кузова: KA123456789
Идентификационный номер (VIN): XWBJA69V0KA123456
Номер двигателя: B15D2123456
Количество сидений: 5
Регион регистрации: город Ташкент
Владелец: Каримов Азиз Акмалович
Адрес владельца: г. Ташкент, ул. Амира Темура, 15
Серия паспорта: AA 1234567
ПИНФЛ: 31234567890123
"""

CADASTRE_TEXT = """КАДАСТРОВЫЙ ПАСПОРТ ОБЪЕКТА НЕДВИЖИМОСТИ
Кадастровый номер: 10:09:05:01:02:1234
Местонахождение: город Ташкент, Мирзо-Улугбекский район, массив Буюк Ипак Йули, дом 28, кв. 41
Вид объекта недвижимости: квартира
Площадь зданий и сооружений: 74,5 кв. м
Число комнат: 3
Этажность: 9
Год постройки: 2007
Материал стен: кирпич
Вид права: частная собственность
Кадастровая стоимость: 185000
Наличие запрета, ареста или ограничений: ипотека в пользу банка
Правообладатель: Каримов Азиз Акмалович
Место жительства: город Ташкент, массив Буюк Ипак Йули, дом 28, кв. 41
ИНН: 301234567
"""

CADASTRE_UZ_TEXT = """KO'CHMAS MULK OBYEKTINING KADASTR PASPORTI
Kadastr raqami: 10:09:05:01:02:1234
Joylashgan joyi: Toshkent shahri, Mirzo Ulug'bek tumani, 28-uy, 41-xonadon
Ko'chmas mulk obyekti turi: kvartira
Bino va inshootlar maydoni: 74,5 kv.m
Xonalar soni: 3
Qavatlar soni: 9
Qurilgan yili: 2007
Huquq turi: xususiy mulk
Kadastr bahosi: 185000
Egasi: Karimov Aziz Akmalovich
"""


def make_pdf(text: str, path: Path) -> Path:
    """PDF с текстовым слоем. Шрифт со встроенной кириллицей — иначе текст не прочитается."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 60), text, fontname="china-s", fontsize=9)
    doc.save(path)
    doc.close()
    return path


def make_scan_pdf(path: Path) -> Path:
    """PDF-«скан»: страница с картинкой и без единого символа текста."""
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 120))
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(50, 50, 250, 170), pixmap=pix)
    doc.save(path)
    doc.close()
    return path


def by_key(out: dict, key: str) -> dict:
    for f in out["поля"]:
        if f["поле"] == key:
            return f
    raise AssertionError("нет поля " + key)


def warn_codes(out: dict) -> set:
    return {w["код"] for w in out["предупреждения"]}


# --------------------------------------------------------------------------- #
# 1. Техпаспорт
# --------------------------------------------------------------------------- #

def test_passport_text():
    out = D.parse_text(PASSPORT_TEXT, D.KIND_PASSPORT)
    assert out["статус"] in (D.ST_OK, D.ST_PARTIAL), out["статус"]
    assert by_key(out, "brand")["значение"] == "Chevrolet"
    assert by_key(out, "model")["значение"].startswith("Cobalt")
    assert by_key(out, "year")["значение"] == "2019"
    assert by_key(out, "region")["значение"] == "город Ташкент"
    assert by_key(out, "vin")["значение"] == "XWBJA69V0KA123456"
    assert by_key(out, "body_no")["значение"] == "KA123456789"
    assert by_key(out, "engine_cc")["значение"] == "1485"
    assert by_key(out, "seats")["значение"] == "5"
    v = out["для_оценки"]
    assert v["марка"] == "Chevrolet" and v["год"] == "2019" and v["регион"] == "город Ташкент"
    print("техпаспорт:", v)


def test_passport_norm_refs():
    """У каждого поля видно, чем подтверждена подпись, и что с бланком оно не сверено."""
    out = D.parse_text(PASSPORT_TEXT, D.KIND_PASSPORT)
    assert out["сверено_с_бланком"] is False
    assert "ПКМ № 683" in by_key(out, "brand")["норма"]
    # графы «регион» нормативные акты не называют — это видно в ответе
    assert by_key(out, "region")["подтверждено_нормой"] is False
    assert all(f["сверено_с_бланком"] is False for f in out["поля"])
    print("норма марки:", by_key(out, "brand")["норма"])


def test_passport_no_personal_data():
    """ЗРУ-547 (PD-01): ФИО, адрес владельца, паспорт и ПИНФЛ не должны попасть в ответ."""
    out = D.parse_text(PASSPORT_TEXT, D.KIND_PASSPORT)
    dump = repr(out)
    for secret in ("Каримов", "Акмалович", "Амира Темура", "AA 1234567", "31234567890123"):
        assert secret not in dump, "в ответ попали персональные данные: " + secret
    print("персональные данные в ответ не попали")


def test_reg_no_off_by_default():
    """Госномер косвенно идентифицирует владельца — по умолчанию не извлекаем."""
    out = D.parse_text(PASSPORT_TEXT, D.KIND_PASSPORT)
    r = by_key(out, "reg_no")
    assert r["значение"] is None and r["уверенность"] == "не извлекается"
    assert "01 A 123 AB" not in repr(out)
    on = D.parse_text(PASSPORT_TEXT, D.KIND_PASSPORT, with_reg_no=True)
    assert by_key(on, "reg_no")["значение"] == "01 A 123 AB"
    print("госномер:", r["пояснение"][:60], "…; по запросу:", by_key(on, "reg_no")["значение"])


def test_mileage_is_manual():
    out = D.parse_text(PASSPORT_TEXT, D.KIND_PASSPORT)
    m = by_key(out, "mileage_km")
    assert m["значение"] is None and m["ручное"] is True
    assert m["уверенность"] == "не извлекается"
    assert "DOC-TP-01" in warn_codes(out)
    print("пробег:", m["пояснение"])


def test_passport_warnings():
    out = D.parse_text(PASSPORT_TEXT, D.KIND_PASSPORT)
    assert "DOC-TP-02" in warn_codes(out)          # VIN найден — сверить номера при осмотре
    assert "DOC-TP-03" not in warn_codes(out)      # год выпуска распознан
    no_year = D.parse_text(PASSPORT_TEXT.replace("Год выпуска: 2019", ""), D.KIND_PASSPORT)
    stops = [w for w in no_year["предупреждения"] if w["код"] == "DOC-TP-03"]
    assert stops and stops[0]["уровень"] == "стоп"
    print("без года выпуска:", stops[0]["текст"])


# --------------------------------------------------------------------------- #
# 2. Кадастровый документ
# --------------------------------------------------------------------------- #

def test_cadastre_text():
    out = D.parse_text(CADASTRE_TEXT, D.KIND_CADASTRE)
    assert out["статус"] in (D.ST_OK, D.ST_PARTIAL), out["статус"]
    assert by_key(out, "area_m2")["значение"] == "74.5"
    assert by_key(out, "rooms")["значение"] == "3"
    assert by_key(out, "object_kind")["значение"] == "квартира"
    assert by_key(out, "build_year")["значение"] == "2007"
    assert by_key(out, "region")["значение"] == "город Ташкент"
    assert by_key(out, "floors")["значение"] == "9"
    assert by_key(out, "cadastre_no")["значение"] == "10:09:05:01:02:1234"
    addr = by_key(out, "address")["значение"]
    assert addr and "28" in addr
    v = out["для_оценки"]
    assert v["площадь"] == "74.5" and v["комнаты"] == "3" and v["тип"] == "квартира"
    print("кадастр:", v)


def test_cadastral_value_not_insured_value():
    """DOC-KAD-02: кадастровая стоимость извлекается, но в оценку не подставляется."""
    out = D.parse_text(CADASTRE_TEXT, D.KIND_CADASTRE)
    assert by_key(out, "cadastral_value")["значение"] == "185000"
    assert "стоимость" not in out["для_оценки"]
    assert "DOC-KAD-02" in warn_codes(out)
    assert "DOC-KAD-03" in warn_codes(out)          # запись об ипотеке в графе ограничений
    print("предупреждения кадастра:", sorted(warn_codes(out)))


def test_cadastre_uz_labels():
    """Узбекские подписи: Joylashgan joyi, Bino va inshootlar maydoni, Xonalar soni."""
    out = D.parse_text(CADASTRE_UZ_TEXT, D.KIND_CADASTRE)
    assert by_key(out, "area_m2")["значение"] == "74.5"
    assert by_key(out, "rooms")["значение"] == "3"
    assert by_key(out, "build_year")["значение"] == "2007"
    assert by_key(out, "region")["значение"] == "город Ташкент"
    assert by_key(out, "object_kind")["значение"] == "kvartira"
    print("кадастр (узб.):", out["для_оценки"])


def test_cadastre_no_personal_data():
    out = D.parse_text(CADASTRE_TEXT, D.KIND_CADASTRE)
    addr = by_key(out, "address")["значение"] or ""
    dump = repr(out)
    assert "Акмалович" not in dump and "301234567" not in dump
    # местонахождение ОБЪЕКТА берём — это характеристика имущества, а не человека
    assert addr
    print("кадастр: местонахождение объекта взято, данные правообладателя — нет")


def test_build_year_hint():
    """DOC-KAD-01: года постройки в реестре может не быть — тогда подсказка агенту."""
    out = D.parse_text(CADASTRE_TEXT.replace("Год постройки: 2007", ""), D.KIND_CADASTRE)
    assert by_key(out, "build_year")["значение"] is None
    hints = [w for w in out["предупреждения"] if w["код"] == "DOC-KAD-01"]
    assert hints and hints[0]["уровень"] == "подсказка"
    print("без года постройки:", hints[0]["текст"])


# --------------------------------------------------------------------------- #
# 3. Чтение PDF и скан без текста
# --------------------------------------------------------------------------- #

def test_pdf_passport():
    f = make_pdf(PASSPORT_TEXT, TMP / "passport.pdf")
    out = D.parse_file(f, "application/pdf", D.KIND_PASSPORT)
    assert out["статус"] in (D.ST_OK, D.ST_PARTIAL), out
    assert out["для_оценки"].get("марка") == "Chevrolet"
    print("PDF техпаспорт:", out["найдено_полей"], "из", out["всего_полей"], "полей")


def test_pdf_cadastre():
    f = make_pdf(CADASTRE_TEXT, TMP / "cadastre.pdf")
    out = D.parse_file(f, "application/pdf", D.KIND_CADASTRE)
    assert out["статус"] in (D.ST_OK, D.ST_PARTIAL), out
    assert out["для_оценки"].get("площадь") == "74.5"
    print("PDF кадастр:", out["найдено_полей"], "из", out["всего_полей"], "полей")


def test_scan_needs_ocr():
    f = make_scan_pdf(TMP / "scan.pdf")
    out = D.parse_file(f, "application/pdf", D.KIND_PASSPORT)
    assert out["статус"] == D.ST_OCR, out
    assert out["поля"] == [] and out["для_оценки"] == {}
    print("скан без текста:", out["примечание"])


def test_photo_needs_ocr():
    out = D.parse_file(TMP / "нет.jpg", "image/jpeg", D.KIND_PASSPORT)
    assert out["статус"] == D.ST_OCR and out["для_оценки"] == {}
    print("фотография:", out["статус"])


def test_broken_pdf():
    f = TMP / "broken.pdf"
    f.write_bytes(b"%PDF-1.4 not really a pdf")
    out = D.parse_file(f, "application/pdf", D.KIND_CADASTRE)
    assert out["статус"] in (D.ST_ERROR, D.ST_OCR), out
    print("битый PDF:", out["статус"], "—", out["примечание"])


# --------------------------------------------------------------------------- #
# 4. Загрузка, сохранение результата и отметка чек-листа
# --------------------------------------------------------------------------- #

def test_store_and_checklist():
    from app import photos as P
    f = make_pdf(PASSPORT_TEXT, TMP / "passport2.pdf")
    blob = f.read_bytes()
    with db.tx() as con:
        cur = con.execute("INSERT INTO requests (external_no, branch, product_code, policyholder,"
                          " created_at, status) VALUES (?,?,?,?,?,?)",
                          ("тест-docparse", "тест", None, "тест", db.now(), "новый"))
        rid = cur.lastrowid
        MADE_REQUESTS.append(rid)

        assert P.sniff_mime(blob) == "application/pdf"
        rec = P._store(con, rid, blob, "паспорт.pdf", "тест", None, D.KIND_PASSPORT)
        MADE_PHOTOS.append(rec["id"])
        assert rec["doc_kind"] == D.KIND_PASSPORT

        out = D.parse_photo(con, rec["id"], D.KIND_PASSPORT, who="тест")
        assert out["для_оценки"].get("марка") == "Chevrolet"
        assert out["чек_лист"]["отмечен"] is True
        print("чек-лист отмечен:", out["чек_лист"]["пункт"])

        # повторный вызов берёт сохранённый результат, файл заново не разбирается
        again = D.parse_photo(con, rec["id"], D.KIND_PASSPORT, who="тест")
        assert again["из_кэша"] is True

        state = D.checklist_state(con, rid)
        got = [s for s in state if s["получен"]]
        assert len(got) == 1 and got[0]["пункт"] == out["чек_лист"]["пункт"]

        # в базу персональные данные тоже не попали
        saved = db.rows(con, "SELECT parsed_json FROM photos WHERE id=?", rec["id"])[0]
        assert "Каримов" not in saved["parsed_json"] and "31234567890123" not in saved["parsed_json"]

        # PDF нельзя загрузить как фотографию объекта
        try:
            P._store(con, rid, blob, "паспорт.pdf", "тест", None, D.KIND_PHOTO)
            raise AssertionError("PDF приняли как фотографию объекта")
        except Exception as e:
            assert getattr(e, "status_code", None) == 415, e
        print("PDF как фото объекта отклонён — верно")


def cleanup():
    """Тест пишет в рабочую базу — убираем за собой."""
    import shutil
    from app.photos import ROOT as PROOT
    with db.tx() as con:
        for pid in MADE_PHOTOS:
            r = db.rows(con, "SELECT path FROM photos WHERE id=?", pid)
            if r:
                fp = PROOT / r[0]["path"]
                if fp.exists():
                    fp.unlink()
            con.execute("DELETE FROM photos WHERE id=?", (pid,))
        for rid in MADE_REQUESTS:
            con.execute("DELETE FROM documents WHERE request_id=?", (rid,))
            con.execute("DELETE FROM audit WHERE entity=?", (f"request:{rid}",))
            con.execute("DELETE FROM requests WHERE id=?", (rid,))
            folder = PROOT / "data" / "photos" / str(rid)
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
    shutil.rmtree(TMP, ignore_errors=True)
    print("тестовые записи удалены")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    try:
        for t in tests:
            t()
    finally:
        cleanup()
    print("\nвсе проверки разбора документов пройдены:", len(tests))


if __name__ == "__main__":
    main()
