"""
Контрольные примеры аналитики риска (app/risk_analytics.py) и калькулятора ОСГОР (osgor.quick_premium).

Запуск из корня:
    PYTHONIOENCODING=utf-8 sandbox/.venv/Scripts/python.exe tests/test_risk_analytics.py

Всё идёт в копию базы (tests/tmpdb.temp_db). В копии, чтобы числа не зависели от будущих отчётов:
  - собственные средства 180 млрд и резервы 240 млрд (company_financials, как временные цифры сейчас),
    отчётности маржи и резервов нет → лимит на один риск = 20% × (180 + 240) = 84 000 000 000 сум;
  - рынок передаётся параметром market (как его отдаст app/market_picture.py).

Справочник (экспертный, calibrated = 0): нагрузка 30% (РВД 15 + комиссия 10 + прибыль 5),
рисковая надбавка 12% нетто, катастрофическая 5% нетто (если в покрытии землетрясение/сель/оползень).
Техническая ставка = нетто × (1 + 0,12 [+ 0,05]) / 0,7.

Пример 1. Склад кирпичный, класс 9 (без сейсмики: землетрясение — риск класса 8), 4,2 млрд, год,
    сигнализация и охрана, убытков не было.
    нетто = 0,10 (база «Склад», кл. 9) × 0,9 (убытков не было) = 0,09%
    техническая = 0,09 × 1,12 / 0,7 = 0,144%  > минимума 0808 0,05% → применена 0,144%
    премия = 0,144% × 4 200 000 000 = 6 048 000 сум
    сценарии класса 9 (отсеков нет → весь склад одно помещение 4,2 млрд; защита «сигнализация и охрана» × 0,8):
      EML = 4,2 млрд × 0,10 × 0,8 = 336 000 000;  PML = × 0,25 × 0,8 = 840 000 000;  MFL = × 0,50 × 0,8 = 1 680 000 000
    уровень: ставка 0,144 / рынок 0,18 = 0,80 → 0 баллов; MFL / удержание 1,68 / 84 млрд → 0; убытков 0 → 0;
      сумма = стоимость → 0; сейсмика не применяется → итого 0 → «Низкий».

Пример 2. Производство 8/9, 50 млрд, железобетон, пищевое, сигнализация и охрана, сейсмозона «8–9» (берётся 9),
    износ 30% (→ «10–30 лет»), один убыток 300 млн, франшиза 0,5%, 4 отсека, наибольший 18 млрд,
    простой 1 млрд/мес × 4 мес.
    класс 8: нетто = 0,35 × 1,0 (все риски, кроме ядерного) × 0,9 × 1,2 × 0,9 × 1,7 × 1,0 × 1,15 × 0,92
             = 0,61188372%; техническая = 0,61188372 × 1,17 / 0,7 = 1,02272%; премия 511 359 966 сум
    класс 9: нетто = 0,12 × 1,15 × 0,92 = 0,12696%; техническая = 0,12696 × 1,12 / 0,7 = 0,203136%;
             премия 101 568 000 сум
    итого премия 612 927 966 сум (каждый класс — отдельный условный договор, Положение 1882 п. 11)
    сценарии (класс 8 больше класса 9, одно событие):
      EML = max(пожар 18 млрд × 0,40 = 7,2 млрд; землетрясение 50 млрд × 0,20 = 10 млрд) + простой 4 млрд × 0,40
          = 10 + 1,6 = 11 600 000 000
      PML = max(18 × 0,70 = 12,6; 50 × 0,35 = 17,5) + 4 × 0,70 = 17,5 + 2,8 = 20 300 000 000
      MFL = max(18 × 1,0; 50 × 0,60 = 30) + 4 × 1,0 = 34 000 000 000
    удержание (EML по заказчику) = min(11,6 млрд; 84 млрд; таблица линий кл. 8) = 11 600 000 000
    уровень: ставка 1,2258 / 0,18 = 6,8 → 100 × 0,25 = 25; MFL/удержание 34 / 43,41 = 0,783 → 11,3 × 0,25 = 2,8;
      убытки: 1 → 50 (100 млн в год / 613 млн премии = 0,16 → 0), 50 × 0,2 = 10; сумма = стоимость → 0;
      9 баллов → 100 × 0,2 = 20; итого 57,8 → «Повышенный».

Пример 3. Спецтехника — экскаватор, класс 3, стоимость 1,5 млрд, сумма 1,2 млрд, 2021 г. (5 лет → «3–7 лет»),
    спутниковый поиск, 2 убытка на 150 млн.
    нетто = 0,75 × 1,0 × 0,9 (спецтехника) × 0,9 (поиск) × 1,5 (два убытка) = 0,91125%
    техническая = 0,91125 × 1,12 / 0,7 = 1,458% (минимум 0318 — 0,35%); премия = 1,458% × 1,2 млрд = 17 496 000
    сценарии (доля страхования 1,2/1,5 = 0,8; считаем от 1,2 млрд):
      EML = 1,2 млрд × 0,50 = 600 000 000; PML = × 0,75 (есть поиск) = 900 000 000; MFL = × 1,0 = 1 200 000 000
    уровень: ставка 1,458 / рынок 0,70 = 2,08 → 100 × 0,3125 = 31,25; MFL/удержание 1,2/11,1 → 0;
      убытки 2 → 100 × 0,25 = 25; сумма 80% стоимости → 40 × 0,125 = 5; сейсмика не применяется;
      итого 61,25 → «Высокий».
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from tmpdb import temp_db  # noqa: E402
from app import db, osgor, risk_analytics as ra  # noqa: E402
from app import capacity as cap  # noqa: E402

AS_OF = date(2026, 9, 21)


def eq(got, want, what, tol=1.0):
    ok = abs(got - want) <= tol if isinstance(want, (int, float)) and not isinstance(want, bool) else got == want
    assert ok, f"{what}: получено {got}, ожидалось {want}"
    print(f"  ok  {what}: {got}")


EX1 = ({"class_code": "9", "object_type": "Склад", "sum_insured": 4.2e9, "object_value": 4.2e9,
        "region": "Ташкентская область", "term_months": 12, "construction": "reinforced", "activity": "warehouse"},
       {"protection": "alarm_guard", "losses_3y": {"count": 0, "amount": 0}})
EX2 = ({"class_code": "8/9", "object_type": "Производство", "sum_insured": 50e9, "object_value": 50e9,
        "region": "г. Ташкент", "term_months": 12, "construction": "reinforced", "activity": "food"},
       {"protection": "alarm_guard", "seismic_zone": "8–9", "wear_pct": 30,
        "losses_3y": {"count": 1, "amount": 300e6}, "deductible": {"pct": 0.5},
        "compartments": {"count": 4, "largest_value": 18e9},
        "bi": {"monthly_amount": 1e9, "recovery_months": 4}, "fire_station_km": 5, "floors": 2})
EX3 = ({"class_code": "3", "object_type": "Спецтехника — экскаватор", "sum_insured": 1.2e9, "object_value": 1.5e9,
        "region": "Навоийская область", "term_months": 12, "vehicle_type": "special", "year": 2021},
       {"protection": "tracker", "losses_3y": {"count": 2, "amount": 150e6}})


def fix_company(con):
    """Копия базы: известные цифры компании, без отчётности (детерминированный лимит 84 млрд)."""
    for t in ("solvency_reports", "reserve_reports", "company_financials"):
        con.execute(f"DELETE FROM {t}")
    con.execute("INSERT INTO company_financials (report_date, own_funds, reserves, source) VALUES (?,?,?,?)",
                ("2026-07-01", 180e9, 240e9, "временно, тест"))


def test_validate():
    print("\nПроверка ввода")
    v = ra.validate({}, {})
    assert not v["ok"]
    for k in ("class_code", "object_type", "sum_insured", "object_value", "region", "term_months"):
        assert k in v["missing"], k
    print(f"  ok  без обязательных полей: missing = {v['missing']}")
    v8 = ra.validate({"class_code": "8", "object_type": "Склад", "sum_insured": 1, "object_value": 1,
                      "region": "x", "term_months": 12}, {})
    assert set(v8["missing"]) == {"construction", "activity"}, v8
    v3 = ra.validate({"class_code": "3", "object_type": "Легковой", "sum_insured": 1, "object_value": 1,
                      "region": "x", "term_months": 12}, {})
    assert set(v3["missing"]) == {"vehicle_type", "year"}, v3
    bad = ra.validate({**EX2[0], "sum_insured": -5, "term_months": 99},
                      {"compartments": {"largest_value": 60e9}, "seismic_zone": 12})
    assert {"sum_insured", "term_months", "compartments", "seismic_zone"} <= set(bad["errors"]), bad
    print(f"  ok  ошибки формата: {sorted(bad['errors'])}")
    assert ra.validate(*EX1)["ok"] and ra.validate(*EX2)["ok"] and ra.validate(*EX3)["ok"]
    with temp_db():
        with db.tx() as con:
            r = ra.analyze(con, {"class_code": "8"}, {})
            assert r["ok"] is False and "construction" in r["validation"]["missing"]
            r = ra.analyze(con, {**EX1[0], "class_code": "99"}, EX1[1])
            assert r["ok"] is False and "class_code" in r["validation"]["errors"]
    print("  ok  без обязательных полей анализ не запускается")


def test_example1_warehouse_class9():
    print("\nПример 1 — склад кирпичный, класс 9, 4,2 млрд")
    with temp_db():
        with db.tx() as con:
            fix_company(con)
            r = ra.analyze(con, *EX1, thresholds={}, market={"rate_pct": 0.18, "label": "тест"}, as_of=AS_OF)
    s = r["summary"]
    eq(s["rate_net_pct"], 0.09, "нетто-ставка, %", 1e-6)
    eq(s["rate_technical_pct"], 0.144, "техническая ставка, %", 1e-6)
    eq(s["rate_min_pct"], 0.05, "минимум продукта 0808, %", 1e-9)
    eq(s["premium"], 6_048_000, "премия, сум")
    eq(s["insurance_to_value"]["status"], "ok", "сумма равна стоимости")
    sc = r["scenarios"]
    eq(sc["EML"]["amount"], 336_000_000, "EML")
    eq(sc["PML"]["amount"], 840_000_000, "PML")
    eq(sc["MFL"]["amount"], 1_680_000_000, "MFL")
    assert sc["MFL"]["earthquake_loss"] is None, "в классе 9 нет землетрясения"
    eq(r["retention"]["limit_per_risk"], 84_000_000_000, "лимит 20% на один риск")
    eq(r["retention"]["status"], "временно", "цифры компании помечены как временные")
    eq(sc["EML"]["retention"]["retention"], 336_000_000, "удержание по EML")
    eq(r["market"]["deviation_pct"], -20.0, "отклонение от рынка, %", 0.05)
    eq(r["level"]["score"], 0.0, "балл уровня риска", 0.05)
    eq(r["level"]["level"], "Низкий", "уровень риска")
    assert all(a["calibrated"] == 0 for x in sc.values() for a in x["assumptions"])
    assert r["risks"][0]["code"] == "burglary" and r["risks"][0]["share_of_net_pct"] == 45.0
    assert any(x["kind"] == "франшиза" and x["premium_delta"] < 0 for x in r["reducers"])
    f1 = next(x for x in r["reducers"] if x["kind"] == "франшиза" and "1%" in x["text"])
    # франшиза 1%: 0,09 × 0,85 × 1,12 / 0,7 = 0,1224% → 5 140 800; экономия 907 200
    eq(f1["premium_delta"], -907_200, "франшиза 1% снижает премию на")


def test_example2_production_8_9_seismic():
    print("\nПример 2 — производство 8/9, сейсмозона 8–9, отсеки, простой")
    with temp_db():
        with db.tx() as con:
            fix_company(con)
            r = ra.analyze(con, *EX2, thresholds={}, market={"rate_pct": 0.18}, as_of=AS_OF)
            lines = cap.retention_table(con, 84e9)
    s = r["summary"]
    p8, p9 = s["parts"]
    eq(p8["class_code"], "8", "первая часть — класс 8")
    eq(p8["rate_technical_pct"], 1.0227, "техническая ставка класса 8, %", 1e-4)
    eq(p8["premium"], 511_359_966, "премия класса 8")
    eq(p9["premium"], 101_568_000, "премия класса 9")
    eq(s["premium"], 612_927_966, "премия всего")
    sc = r["scenarios"]
    eq(sc["EML"]["amount"], 11_600_000_000, "EML")
    eq(sc["PML"]["amount"], 20_300_000_000, "PML")
    eq(sc["MFL"]["amount"], 34_000_000_000, "MFL")
    eq(sc["MFL"]["dominant"], "землетрясение", "в MFL преобладает землетрясение")
    eq(sc["PML"]["bi_loss"], 2_800_000_000, "простой в PML")
    line8 = next(l for l in lines if l["class_code"] == "8")["retention"]
    eq(r["retention"]["retention_limit"], min(84e9, line8), "лимит удержания = min(84 млрд, таблица линий)")
    eq(sc["EML"]["retention"]["retention"], 11_600_000_000, "удержание по EML")
    need = max(34e9 - min(84e9, line8), 0)
    eq(sc["MFL"]["catastrophe"]["reinsurance_need"], need, "сверх удержания по MFL (оценочно)")
    assert sc["PML"]["purpose"] == "tariff" and "tariff" in sc["PML"]
    # ожидаемый убыток = нетто 0,7389% (движок округляет до 4 знаков) × 50 млрд = 369 450 000;
    # / PML 20,3 млрд = 1,82% в год → в среднем раз в 55 лет
    eq(sc["PML"]["tariff"]["implied_return_period_years"], 55, "частота события размера PML, лет")
    eq(r["level"]["score"], 57.8, "балл уровня риска", 0.15)
    eq(r["level"]["level"], "Повышенный", "уровень риска")
    top = [d["factor_code"] for d in r["top_drivers"]]
    eq(top[0], "seismic", "главный драйвер")
    # без сейсмики 1,7: 511 359 966 / 1,7 = 300 799 980; вклад 210 559 986 сум
    eq(r["top_drivers"][0]["premium_effect"], 210_559_986, "вклад сейсмозоны в премию, сум")
    assert any(x["kind"] == "риск" and "землетрясение" in x["text"].lower() for x in r["reducers"])
    assert r["completeness"]["confidence"] == "высокая"
    assert "note" in sc["EML"] and "SCENARIO_PURPOSE" in sc["EML"]["note"]


def test_example3_special_machinery():
    print("\nПример 3 — спецтехника (экскаватор), класс 3, недострахование")
    with temp_db():
        with db.tx() as con:
            fix_company(con)
            r = ra.analyze(con, *EX3, thresholds={}, market={"rate_pct": 0.70}, as_of=AS_OF)
    s = r["summary"]
    eq(s["rate_technical_pct"], 1.458, "техническая ставка, %", 1e-6)
    eq(s["premium"], 17_496_000, "премия")
    eq(s["parts"][0]["product_code"], "0318", "типовой продукт спецтехники")
    eq(s["insurance_to_value"]["status"], "warn", "недострахование (ГК ст. 936)")
    sc = r["scenarios"]
    eq(sc["EML"]["amount"], 600_000_000, "EML")
    eq(sc["PML"]["amount"], 900_000_000, "PML")
    eq(sc["MFL"]["amount"], 1_200_000_000, "MFL")
    eq(r["level"]["score"], 61.25, "балл уровня риска", 0.1)
    eq(r["level"]["level"], "Высокий", "уровень риска")
    eq(r["top_drivers"][0]["factor_code"], "loss_history", "главный драйвер — убытки")


def test_no_company_data_and_purpose_switch():
    print("\nНет данных компании; переключение трактовки сценариев")
    with temp_db():
        with db.tx() as con:
            for t in ("solvency_reports", "reserve_reports", "company_financials"):
                con.execute(f"DELETE FROM {t}")
            r = ra.analyze(con, *EX2, thresholds={}, market={"rate_pct": 0.18}, as_of=AS_OF)
            eq(r["retention"]["status"], "нет данных компании", "статус удержания")
            assert r["retention"]["limit_per_risk"] is None and r["retention"]["retention_limit"] is None
            assert r["scenarios"]["EML"]["retention"]["retention"] is None
            assert r["scenarios"]["MFL"]["catastrophe"]["reinsurance_need"] is None
            print("  ok  без цифр компании лимит и перестрахование = null, без выдуманных чисел")
            saved = dict(ra.SCENARIO_PURPOSE)
            try:
                ra.SCENARIO_PURPOSE.update({"EML": "tariff", "PML": "retention"})
                r2 = ra.analyze(con, *EX1, thresholds={}, as_of=AS_OF)
                assert "retention" in r2["scenarios"]["PML"] and "tariff" in r2["scenarios"]["EML"]
                print("  ok  трактовка заметки включается одной правкой SCENARIO_PURPOSE")
            finally:
                ra.SCENARIO_PURPOSE.clear()
                ra.SCENARIO_PURPOSE.update(saved)


def test_thresholds_in_db():
    print("\nПороги уровня риска из базы")
    with temp_db():
        with db.tx() as con:
            fix_company(con)
            t0 = ra.load_thresholds(con)
            assert t0["_source"]["id"] is None and t0["level_bounds"] == [20, 40, 60, 80]
            try:
                ra.save_thresholds(con, {"level_bounds": [50, 40, 30, 20]}, "тест")
                raise AssertionError("неверные пороги должны отклоняться")
            except ValueError as e:
                print(f"  ok  неверные пороги отклонены: {e}")
            t1 = ra.save_thresholds(con, {"level_bounds": [10, 20, 30, 40]}, "тест")
            assert t1["_source"]["id"] and t1["_source"]["created_by"] == "тест"
            assert t1["calibrated"] == 0
            r = ra.analyze(con, *EX2, market={"rate_pct": 0.18}, as_of=AS_OF)   # thresholds=None → из базы
            eq(r["level"]["level"], "Критический", "тот же балл 57,8 при границах 10/20/30/40")
            assert r["thresholds_source"]["id"] == t1["_source"]["id"]
            n = con.execute("SELECT COUNT(*) FROM risk_thresholds").fetchone()[0]
            eq(n, 1, "в истории одна версия")


def test_osgor_quick_premium():
    print("\nКалькулятор ОСГОР: ОКЭД + ФОТ + срок → премия построчно")
    with temp_db():
        with db.tx() as con:
            # 4 800 000 000 × 0,1 × 4,000 / 100 = 19 200 000 (ОКЭД 01000, КСТ 4,000)
            q = osgor.quick_premium(con, "01000", 4_800_000_000, 365, brv=412_000)
            eq(q["premium"], 19_200_000, "премия за год")
            eq(q["kst"], 4.0, "КСТ")
            assert q["lines"] and all("legal_ref" in x for x in q["lines"])
            # 30 000 000 × 0,1 × 0,571 / 100 = 17 130 < 0,25 × 412 000 = 103 000 → минимум
            q2 = osgor.quick_premium(con, "41100", 30_000_000, 365, brv=412_000)
            eq(q2["premium"], 103_000, "премия с минимумом 0,25 БРВ")
            eq(q2["min_applied"], True, "минимум применён")
            # 120 дней: 513 900 / 365 × 120 = 168 953,42
            q3 = osgor.quick_premium(con, "41100", 900_000_000, 120, brv=412_000)
            eq(q3["premium"], 168_953.42, "премия за 120 дней", 0.01)
            con.execute("DELETE FROM osgor_brv") if con.execute(
                "SELECT 1 FROM sqlite_master WHERE name='osgor_brv'").fetchone() else None
            q4 = osgor.quick_premium(con, "41100", 30_000_000, 365)
            assert q4["brv"] is None and q4["min_applied"] is False
            assert any("БРВ" in n for n in q4["notes"])
            print("  ok  БРВ не задан — минимум не проверен, об этом строка в notes")


def main():
    test_validate()
    test_example1_warehouse_class9()
    test_example2_production_8_9_seismic()
    test_example3_special_machinery()
    test_no_company_data_and_purpose_switch()
    test_thresholds_in_db()
    test_osgor_quick_premium()
    print("\nВсе примеры сошлись.")


if __name__ == "__main__":
    main()
