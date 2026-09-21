"""
Контрольные примеры методики оценки стоимости. Запуск:  sandbox\\.venv\\Scripts\\python.exe tests/test_valuation.py

Без сети: площадки не опрашиваются, результаты адаптеров подставляются вручную (use_market=False
или готовый список sources). Записи, созданные тестом в рабочей базе, удаляются в конце.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db                                           # noqa: E402
from app import valuation as V                               # noqa: E402
from app.engine import Input, calculate                      # noqa: E402

MADE = []        # id созданных оценок — чистим в конце


# ---------- шаг 3: износ ----------

def test_years():
    assert V.years_between("2016-01-01", "2026-01-01") == 10.0
    assert V.years_between("2022-09-20", "2026-09-20") == 4.0
    assert abs(V.years_between("2026-03-20", "2026-09-20") - 0.5) < 0.01
    assert V.years_between("2026-09-20", "2026-01-01") == 0.0
    print("срок: 2016-01-01 → 2026-01-01 =", V.years_between("2016-01-01", "2026-01-01"), "года")


def test_building_10_years():
    """Здание, норма 3% в год, 10 лет: 1 000 000 000 × (1 − 0,30) = 700 000 000 сум."""
    with db.tx() as con:
        norm = V.norm_by_code(con, "buildings")
    assert norm["rate_pct"] == 3.0
    d = V.depreciated_value(1_000_000_000, norm["rate_pct"],
                            V.years_between("2016-01-01", "2026-01-01"), norm["residual_min_pct"])
    assert d["wear_pct"] == 30.0
    assert d["value"] == 700_000_000
    assert d["floor"] == 300_000_000 and not d["floor_applied"]
    print("здание 10 лет:", f"{d['value']:,.0f}".replace(",", " "), "сум (износ", d["wear_pct"], "%)")


def test_car_4_years():
    """Авто, норма 20% в год, 4 года: 200 000 000 × (1 − 0,80) = 40 000 000 сум = остаточный минимум."""
    with db.tx() as con:
        norm = V.norm_by_code(con, "vehicles_other")
    assert norm["rate_pct"] == 20.0 and norm["residual_min_pct"] == 20.0
    d = V.depreciated_value(200_000_000, norm["rate_pct"],
                            V.years_between("2022-09-20", "2026-09-20"), norm["residual_min_pct"])
    assert d["wear_pct"] == 80.0
    assert d["value"] == 40_000_000 == d["floor"]
    print("авто 4 года:", f"{d['value']:,.0f}".replace(",", " "), "сум")


def test_floor_applied():
    """Через 6 лет линейный расчёт даёт ноль — берётся остаточный минимум 20%."""
    d = V.depreciated_value(200_000_000, 20.0, 6.0, 20.0)
    assert d["linear"] == 0.0 and d["floor_applied"] and d["value"] == 40_000_000
    dc = V.depreciated_value(200_000_000, 20.0, 3.0, 20.0, mileage_k=0.85)
    assert dc["linear"] == 80_000_000 and dc["adjusted"] == 68_000_000 and dc["value"] == 68_000_000
    print("упор в минимум:", f"{d['value']:,.0f}".replace(",", " "), "сум; с пробегом:",
          f"{dc['value']:,.0f}".replace(",", " "), "сум")


def test_mileage():
    """Норматив 20 000 км в год, 4 года = 80 000 км ожидаемого пробега."""
    norm_km, sens, lim = 20000.0, 0.15, 15.0
    assert V.mileage_factor(80_000, 4.0, norm_km, sens, lim)["k"] == 1.0
    assert V.mileage_factor(160_000, 4.0, norm_km, sens, lim)["k"] == 0.85    # вдвое больше нормы
    assert V.mileage_factor(0, 4.0, norm_km, sens, lim)["k"] == 1.15          # пробега нет
    big = V.mileage_factor(400_000, 4.0, norm_km, sens, lim)
    assert big["k"] == 0.85 and big["capped"]                                 # предел не пробить
    assert V.mileage_factor(None, 4.0, norm_km, sens, lim)["k"] == 1.0
    print("пробег: 160 000 км за 4 года → коэффициент", V.mileage_factor(160_000, 4.0, norm_km, sens, lim)["k"])


# ---------- шаг 1: рынок ----------

def fake_sources(prices, source="olx.uz", status="ok"):
    return [{"source": source, "status": status, "url": "https://" + source,
             "ads_count": len(prices), "prices": prices,
             "samples": [{"title": "объявление %d" % i, "price": p, "currency": "UZS",
                          "price_uzs": p, "published": "2026-08-01",
                          "url": "https://%s/%d" % (source, i)}
                         for i, p in enumerate(prices[:5], 1)],
             "fetched_at": "2026-09-20T10:00:00+05:00"}]


def test_market():
    prices = [100e6, 110e6, 120e6, 130e6, 140e6]
    m = V.market_aggregate(fake_sources(prices), "2026-09-20", min_ads=5, bargain_pct=5,
                           window_months=6)
    assert m["median"] == 120e6 and m["q1"] == 110e6 and m["q3"] == 130e6
    assert m["enough"] and m["value"] == 114e6              # 120 млн × 0,95 (скидка на торг)
    assert m["spread"]["min"] == 104.5e6 and m["spread"]["max"] == 123.5e6
    assert len(m["samples"]) == 5

    few = V.market_aggregate(fake_sources(prices[:3]), "2026-09-20", 5, 5, 6)
    assert not few["enough"] and "не меньше 5" in few["reason"]

    down = V.market_aggregate([{"source": "olx.uz", "status": "источник недоступен",
                                "reason": "таймаут"}], "2026-09-20", 5, 5, 6)
    assert not down["enough"] and "источник недоступен" in down["reason"]

    stale = V.market_aggregate(
        [{"source": "olx.uz", "status": "ok", "ads_count": 1, "prices": [100e6],
          "samples": [{"title": "старое", "price_uzs": 100e6, "published": "2025-01-01"}]}],
        "2026-09-20", 1, 5, 6)
    assert stale["samples"] == [] and any("старше 6 мес" in n for n in stale["notes"])
    print("рынок: медиана", f"{m['median']:,.0f}".replace(",", " "),
          "→ с торгом", f"{m['value']:,.0f}".replace(",", " "), "сум")


# ---------- шаг 2: письмо дилеру ----------

def test_dealer_letter():
    letter = V.dealer_letter({"brand": "Chevrolet", "model": "Cobalt", "year": 2022,
                              "vin": "X9L1234567890", "mileage_km": 90000}, "2026-09-20")
    assert "Chevrolet" in letter["text"] and "X9L1234567890" in letter["text"]
    assert "цену нового автомобиля" in letter["text"]
    if not letter["dealer_found"]:
        assert letter["to"] == V.DEALER_UNKNOWN and "не придумывает" in letter["note"]
    no_vin = V.dealer_letter({"brand": "Kia", "model": "Sportage"}, "2026-09-20")
    assert "VIN: не указан" in no_vin["text"]
    print("письмо дилеру: адресат —", letter["to"])


# ---------- шаг 4: сверка с ценой агента ----------

def test_spread():
    over = V.compare_declared(ai_value=80e6, declared=100e6, threshold_pct=15)
    assert over["diff_pct"] == 20.0 and over["accepted"] == "оценка ИИ" and over["value"] == 80e6
    ok = V.compare_declared(ai_value=95e6, declared=100e6, threshold_pct=15)
    assert ok["diff_pct"] == 5.0 and ok["accepted"] == "цена агента" and ok["value"] == 100e6
    none = V.compare_declared(ai_value=None, declared=100e6, threshold_pct=15)
    assert none["accepted"] == "цена агента"
    print("сверка: расхождение 20% →", over["accepted"], "; 5% →", ok["accepted"])


# ---------- сквозной расчёт и воспроизводимость ----------

def test_estimate_market():
    prices = [100e6, 110e6, 120e6, 130e6, 140e6]
    with db.tx() as con:
        out = V.estimate_value(con, object_type="авто",
                               params={"brand": "Chevrolet", "model": "Cobalt", "year": 2022},
                               as_of="2026-09-20", declared_value=150e6,
                               sources=fake_sources(prices))
    assert out["method"] == "объявления" and out["ai_value"] == 114e6
    assert out["accepted"] == "оценка ИИ" and out["final_value"] == 114e6
    assert out["method_version"] == V.METHOD_VERSION
    assert any("Шаг 1" in l for l in out["explanation"]["lines"])
    assert out["explanation"]["expert_values"]
    print("сквозной (рынок):", out["method"], f"{out['ai_value']:,.0f}".replace(",", " "), "сум")


def test_estimate_depreciation_and_repeat():
    """Рынка нет → письмо дилеру + износ. Повтор с теми же данными даёт тот же результат."""
    args = dict(object_type="авто", params={"brand": "Chevrolet", "model": "Nexia", "year": 2022},
                as_of="2026-09-20", declared_value=90e6, norm_code="vehicles_other",
                initial_value=200e6, commissioned_at="2022-09-20", mileage_km=160000,
                sources=[{"source": "olx.uz", "status": "нет объявлений"}])
    with db.tx() as con:
        a = V.estimate_value(con, **args)
        b = V.estimate_value(con, **args)
    assert a["method"] == "износ"
    # 200 млн × (1 − 0,80) = 40 млн; поправка 0,85 → 34 млн; минимум 20% = 40 млн → берём минимум
    assert a["explanation"]["depreciation"]["adjusted"] == 34e6
    assert a["explanation"]["depreciation"]["floor_applied"] and a["ai_value"] == 40e6
    assert a["explanation"]["dealer_letter"] is not None
    assert a["accepted"] == "оценка ИИ"                       # 40 млн против 90 млн — больше порога
    assert a["ai_value"] == b["ai_value"] and a["method_version"] == b["method_version"]
    assert a["explanation"]["lines"] == b["explanation"]["lines"]
    print("сквозной (износ):", f"{a['ai_value']:,.0f}".replace(",", " "), "сум, версия методики",
          a["method_version"])


def test_saved_version_is_kept():
    """Сохранённая оценка помнит свою версию методики — старый расчёт воспроизводится."""
    body = V.EstimateIn(object_type="авто",
                        params={"brand": "ТЕСТ", "model": "Методика", "year": 2022},
                        as_of="2026-09-20", declared_value=150e6, use_market=False,
                        norm_code="vehicles_other", initial_value=200e6,
                        commissioned_at="2024-09-20", created_by="тест")
    out = V.estimate(body)
    MADE.append(out["id"])
    with db.tx() as con:
        saved = V.get_valuation(out["id"])
        assert saved["method_version"] == V.METHOD_VERSION
        last = V.valuation_for_engine(con, key=out["object_key"])
    assert last["id"] == out["id"] and last["value"] == out["ai_value"]
    # 200 млн × (1 − 0,4) = 120 млн, минимум 40 млн не действует
    assert out["ai_value"] == 120e6
    print("сохранено: оценка", out["id"], "метод", out["method"], "версия", saved["method_version"])


# ---------- шаг 5: движок ----------

def test_engine_uses_valuation():
    with db.tx() as con:
        ref = db.load_reference(con)
    base = dict(product_code="0807", class_code="8", object_type="Склад",
                factors={"franchise": "f1"})
    val = {"id": 0, "value": 100e6, "method": "объявления", "method_version": V.METHOD_VERSION,
           "as_of": "2026-09-20", "confirmed_by": None}
    over = calculate(ref, Input(value_amount=120e6, sum_insured=120e6, valuation=val, **base))
    c = [x for x in over["checks"] if x["rule"] == "sum_vs_valuation"]
    assert c and c[0]["status"] == "warn" and "938" in c[0]["detail"]
    assert any(x["rule"] == "value_agreed" for x in over["checks"])

    eq = calculate(ref, Input(value_amount=100e6, sum_insured=100e6, valuation=val, **base))
    c = [x for x in eq["checks"] if x["rule"] == "sum_vs_valuation"]
    assert c and c[0]["status"] == "ok"

    plain = calculate(ref, Input(value_amount=100e6, sum_insured=100e6, **base))
    assert not [x for x in plain["checks"] if x["rule"] == "sum_vs_valuation"]
    print("движок: сумма 120 млн против оценки 100 млн → предупреждение sum_vs_valuation выдано")


# ---------- спецтехника (добавлено вместе с app/vehicle_class.py) ----------

def test_spec_section_lookup():
    """Подтип машины → раздел avtoelon.uz; незнакомый подтип раздел не выдумывает."""
    assert V.spec_section("экскаватор") == "gruzovaja-tehnika/ekskavator"
    assert V.spec_section("экскаватор гусеничный") == "gruzovaja-tehnika/ekskavator"
    assert V.spec_section("погрузчик вилочный") == "gruzovaja-tehnika/vilochnyiy-pogruzchik"
    assert V.spec_section("подводная лодка") is None
    assert V.spec_section("") is None


def test_spec_source_without_subtype_is_honest():
    """Без подтипа адаптер не ходит в сеть и честно говорит, почему ничего не нашёл."""
    r = V.fetch_avtoelon_special({"марка": "JCB"}, date(2026, 9, 21), rate=12000.0)
    assert r["status"] == "источник недоступен"
    assert "подтип" in r["reason"]
    assert r["ads_count"] == 0


def test_engine_hours_factor():
    """9000 моточасов за 3 года при норме 1500 в год — это двойная наработка: −15% стоимости."""
    f = V.engine_hours_factor(9000, 3.0, 1500, 0.15, 15)
    assert f["expected_hours"] == 4500 and f["deviation_pct"] == 100.0
    assert f["k"] == 0.85
    f0 = V.engine_hours_factor(0, 3.0, 1500, 0.15, 15)
    assert f0["k"] == 1.15                      # почти новая машина: плюс 15%, дальше предел
    fn = V.engine_hours_factor(None, 3.0, 1500, 0.15, 15)
    assert fn["k"] == 1.0 and "не указана" in fn["reason"]
    print("наработка 9000 моточасов за 3 года: коэффициент", f["k"])


def test_special_equipment_depreciation_path():
    """
    Экскаватор 2019 года, 4200 моточасов, первоначально 1,2 млрд сум, оценка на 21.09.2026.

    Рынок не опрашиваем (use_market=False): проверяем ветку износа по норме «прочие
    транспортные средства» (20% в год) с поправкой по наработке вместо пробега.
    """
    with db.tx() as con:
        out = V.estimate_value(con, object_type="спецтехника",
                               params={"subtype": "экскаватор", "марка": "JCB", "модель": "JS 220",
                                       "год": 2019, "моточасы": 4200},
                               as_of="2026-09-21", norm_code="vehicles_other",
                               initial_value=1_200_000_000, commissioned_at="2019-09-21",
                               use_market=False, declared_value=300_000_000)
    dep = out["explanation"]["depreciation"]
    assert out["method"] == "износ"
    assert dep["wear_pct"] == 100.0                      # 20% × 7 лет — списан полностью
    assert dep["value"] == dep["floor"] == 240_000_000   # остаточный минимум 20%
    assert "наработк" in dep["mileage"]["reason"]        # поправка считалась по моточасам
    assert out["explanation"]["dealer_letter"]["to"]      # письмо представителю подготовлено
    assert any("наработке" in ln or "наработка" in ln for ln in out["explanation"]["lines"])
    print("экскаватор 2019 г.:", f"{dep['value']:,.0f}".replace(",", " "), "сум по износу")


def test_special_dealer_letter_has_no_invented_address():
    """Представителя марки спецтехники в справочнике нет — адрес не придумывается."""
    letter = V.dealer_letter({"марка": "Caterpillar", "модель": "320D", "год": 2018,
                              "моточасы": 7000, "подтип": "экскаватор"},
                             "2026-09-21", object_type="спецтехника")
    assert letter["to"] == V.DEALER_UNKNOWN
    assert letter["dealer_found"] is False
    assert "моточасов" in letter["text"]
    assert "не найден" in letter["note"]


def cleanup():
    if not MADE:
        return
    with db.tx() as con:
        for vid in MADE:
            con.execute("DELETE FROM valuation_sources WHERE valuation_id=?", (vid,))
            con.execute("DELETE FROM valuations WHERE id=?", (vid,))
            con.execute("DELETE FROM audit WHERE entity=?", (f"valuation:{vid}",))
    print("убрано тестовых оценок:", len(MADE))


if __name__ == "__main__":
    try:
        for name, fn in sorted(list(globals().items())):
            if name.startswith("test_"):
                fn()
    finally:
        cleanup()
    print("методика оценки: все контрольные примеры сошлись")
