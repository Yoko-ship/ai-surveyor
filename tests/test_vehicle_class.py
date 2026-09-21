"""
Проверки классификатора «спецтехника или транспортное средство» (app/vehicle_class.py).

Примеры взяты из заметки юриста docs/Спецтехника и транспорт — граница.md:
документ решает класс, самоходность — запасной признак, карьер уходит к андеррайтеру.
Запуск: PYTHONIOENCODING=utf-8 sandbox\\.venv\\Scripts\\python.exe -m pytest tests/test_vehicle_class.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.vehicle_class import (  # noqa: E402
    VehicleInput, classify, explain_text, normalize_doc, has_plate, detect_subtype,
    DOC_SBDD, DOC_AGRO, DOC_TECH, DOC_NONE,
    G_CAR, G_TRUCK, G_BUS, G_TRAILER, G_SPEC, G_SPEC_CHASSIS, G_EQUIP,
)


def codes(res):
    return [r["code"] for r in res["rules"]]


# --------------------------------------------------------------------------- #
# разбор входа
# --------------------------------------------------------------------------- #

def test_normalize_doc():
    assert normalize_doc("Свидетельство о регистрации СБДД") == DOC_SBDD
    assert normalize_doc("технический паспорт Узагроинспекции") == DOC_AGRO
    assert normalize_doc("техник гувоҳнома") == DOC_TECH
    assert normalize_doc("") == DOC_NONE
    assert normalize_doc("не предъявлен") == DOC_NONE


def test_has_plate():
    assert has_plate(VehicleInput(plate="01 A 123 AA"))
    assert not has_plate(VehicleInput(plate=""))
    assert not has_plate(VehicleInput(plate="нет"))


def test_subtype_longest_keyword_wins():
    st = detect_subtype(VehicleInput(type_text="экскаватор-погрузчик"))
    assert st.code == "ekskavator_pogruzchik"
    st2 = detect_subtype(VehicleInput(type_text="вилочный погрузчик"))
    assert st2.code == "pogruzchik_vil"


# --------------------------------------------------------------------------- #
# обычный транспорт
# --------------------------------------------------------------------------- #

def test_gazelle_is_truck_class3():
    r = classify(VehicleInput(type_text="грузовой бортовой", brand="ГАЗ", model="ГАЗель 3302",
                              doc_kind="свидетельство о регистрации СБДД", plate="01 A 123 AA",
                              self_propelled=True, max_speed_kmh=120, region="Ташкент"))
    assert r["group"] == G_TRUCK
    assert r["class_code"] == "3"
    assert r["osgo"]["required"] is True
    assert r["osgo"]["base_rate_pct"] == 0.35 and r["osgo"]["kt"] == 1.2
    assert "SPEC-05" not in codes(r)          # это обычный грузовик, не спецтехника


def test_cobalt_is_car():
    r = classify(VehicleInput(type_text="легковой", brand="Chevrolet", model="Cobalt",
                              doc_kind="свидетельство СБДД", plate="01 B 777 CC",
                              self_propelled=True, max_speed_kmh=170, region="Самарканд"))
    assert r["group"] == G_CAR and r["class_code"] == "3"
    assert r["osgo"]["base_rate_pct"] == 0.2 and r["osgo"]["kt"] == 1.0
    assert r["confidence"] == "высокая"


def test_kamaz_samosval_is_truck_not_special():
    r = classify(VehicleInput(type_text="самосвал", brand="КамАЗ", model="6520",
                              doc_kind="свидетельство СБДД", plate="01 C 555 DD",
                              self_propelled=True, max_speed_kmh=90, place="стройка"))
    assert r["group"] == G_TRUCK
    assert r["dual_nature"] is None
    assert [p["code"] for p in r["products"]][0] == "0301"


def test_bus():
    r = classify(VehicleInput(type_text="автобус", brand="Isuzu", model="Novo",
                              doc_kind="свидетельство СБДД", plate="01 D 222 EE",
                              self_propelled=True, max_speed_kmh=100, region="Ташкент"))
    assert r["group"] == G_BUS
    assert r["osgo"]["base_rate_pct"] == 0.4


def test_trailer_no_osgo():
    r = classify(VehicleInput(type_text="полуприцеп", brand="Schmitz",
                              doc_kind="свидетельство СБДД", plate="01 E 909 FF",
                              self_propelled=False))
    assert r["group"] == G_TRAILER
    assert r["class_code"] == "3"
    assert r["osgo"]["required"] is False


# --------------------------------------------------------------------------- #
# спецтехника
# --------------------------------------------------------------------------- #

def test_excavator_with_agro_passport_is_class3():
    r = classify(VehicleInput(type_text="экскаватор", brand="JCB", model="JS 220",
                              doc_kind="технический паспорт Узагроинспекции", plate="10 AB 1234",
                              self_propelled=True, max_speed_kmh=35, place="стройка",
                              region="Ташкент"))
    assert r["group"] == G_SPEC and r["class_code"] == "3"
    assert r["osgo"]["required"] is True
    assert r["osgo"]["base_rate_pct"] == 0.075          # строка 4 приложения № 5
    assert round(r["osgo"]["rate_pct"], 4) == 0.09      # 0,075 × 1,2
    assert "SPEC-05" in codes(r)                        # предложить класс 13
    assert [p["code"] for p in r["products"]][0] == "0302"


def test_tractor_mtz_speed_below_20_no_osgo():
    r = classify(VehicleInput(type_text="трактор", brand="МТЗ", model="Беларус 82.1",
                              doc_kind="техпаспорт Узагроинспекции", plate="12 CD 5678",
                              self_propelled=True, max_speed_kmh=18, place="поле"))
    assert r["group"] == G_SPEC and r["class_code"] == "3"
    assert r["osgo"]["required"] is False
    assert "SPEC-07" in codes(r)


def test_combine():
    r = classify(VehicleInput(type_text="комбайн зерноуборочный", brand="Claas",
                              doc_kind="техпаспорт Узагроинспекции", plate="14 EF 4321",
                              self_propelled=True, max_speed_kmh=25, place="сельхоз"))
    assert r["group"] == G_SPEC and r["class_code"] == "3"
    assert r["osgo"]["required"] is True


def test_truck_crane_dual_nature():
    r = classify(VehicleInput(type_text="автокран", brand="Галичанин", model="КС-55713 на шасси КамАЗ",
                              doc_kind="свидетельство СБДД", plate="01 F 321 GG",
                              self_propelled=True, max_speed_kmh=80, place="стройка",
                              region="Ташкент"))
    assert r["group"] == G_SPEC_CHASSIS
    assert r["class_code"] == "3"
    assert r["dual_nature"] is not None
    assert r["dual_nature"]["chassis"]["class_code"] == "3"
    assert r["osgo"]["base_rate_pct"] == 0.35           # база ОСГО — грузовое шасси
    assert "SPEC-05" in codes(r)
    assert r["extra_cover"]["class_code"] == "13"


def test_warehouse_forklift_tech_certificate_is_property():
    r = classify(VehicleInput(type_text="электропогрузчик", brand="Toyota", model="8FBE15",
                              doc_kind="техническое свидетельство", plate="",
                              self_propelled=True, max_speed_kmh=14, place="склад"))
    assert r["class_code"] == "8/9"
    assert "SPEC-02" in codes(r)
    assert r["osgo"]["required"] is False
    assert [p["code"] for p in r["products"]][0] == "0801"


def test_forklift_toyota_brand_alone_is_ambiguous():
    """Марка Toyota встречается и у легковых, и у погрузчиков: без типа — низкая уверенность."""
    r = classify(VehicleInput(brand="Toyota", model="8FBE15"))
    assert r["confidence"] in ("низкая", "средняя")
    assert any("тип" in a.lower() for a in r["ask"])


# --------------------------------------------------------------------------- #
# оборудование и документы
# --------------------------------------------------------------------------- #

def test_seeder_without_documents_is_property():
    r = classify(VehicleInput(type_text="сеялка", brand="Amazone", doc_kind="",
                              self_propelled=False, place="поле"))
    assert r["group"] == G_EQUIP and r["class_code"] == "8/9"
    assert "SPEC-03" in codes(r)


def test_no_document_blocks_class3():
    r = classify(VehicleInput(type_text="экскаватор", brand="Komatsu", doc_kind="",
                              self_propelled=True, max_speed_kmh=30, place="стройка"))
    assert r["class_code"] is None
    assert "SPEC-01" in codes(r)
    assert r["confidence"] == "низкая"
    assert r["products"] == []          # пока класс не подтверждён, продукт не предлагаем


def test_mine_equipment_goes_to_underwriter():
    r = classify(VehicleInput(type_text="самосвал карьерный", brand="БелАЗ",
                              doc_kind="", self_propelled=True, place="карьер"))
    assert "SPEC-06" in codes(r)


def test_explain_text_is_plain_and_complete():
    r = classify(VehicleInput(type_text="экскаватор", brand="JCB",
                              doc_kind="техпаспорт Узагроинспекции", plate="10 AB 1234",
                              self_propelled=True, max_speed_kmh=35, place="стройка"))
    t = explain_text(r)
    assert "Класс:" in t and "ОСГО" in t and "Уверенность" in t
    assert "0302" in t


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("Все проверки классификатора пройдены.")
