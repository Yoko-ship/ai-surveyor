"""
Контрольные примеры подбора франшизы (app/franchise.recommend_franchise).

Запуск из корня:
    PYTHONIOENCODING=utf-8 sandbox/.venv/Scripts/python.exe tests/test_franchise.py

Всё идёт в копию базы (tests/tmpdb.temp_db); рабочая база не открывается.
Объекты те же, что в tests/test_risk_analytics.py (примеры 1–3), плюс критический пример.

Множители франшизы (справочник коэффициентов, calibrated = 0): 0% → 1,00; 0,5% → 0,92;
1% → 0,85; 2% → 0,75. Экспертное продолжение (пороги franchise_multipliers): 5% → 0,60;
10% → 0,45; 90% → 0,15. Между узлами — прямая.

Пример A. Низкий риск (склад, класс 9, 4,2 млрд, убытков нет).
    премия без франшизы = 0,144% × 4,2 млрд = 6 048 000 сум
    уровень «Низкий» → вилка 0–1%; убытков за 3 года 0 → низ вилки → франшиза 0% (не требуется)
    вариант «с франшизой» = базовый = 6 048 000 сум, эффект 0.

Пример B. Повышенный риск (производство 8/9, 50 млрд, сейсмозона 9, 1 убыток 300 млн,
    действующая франшиза 0,5%).
    базовая премия 612 927 966 сум (уровень «Повышенный»)
    вилка 2–5%; 1 убыток, средний 300 млн > порога мелкого (1% от 50 млрд = 500 млн)? нет:
    300 млн < 500 млн → доля мелких 1,0 > 0,5 → верх вилки 5%
    премия при 5%: узел движка 2% → нетто ×0,75 вместо ×0,92, дальше ×(0,60/0,75)
    контроль: 612 927 966 / 0,92 × 0,75 × (0,60/0,75) = 612 927 966 × 0,60 / 0,92 = 399 735 630 сум
    эффект ≈ минус 213 192 336 сум.

Пример C. Критический риск (деревянный склад, горючие материалы, 9-балльная зона, износ 60%,
    3 убытка на 9 млрд, сумма 100 млрд = 50% стоимости 200 млрд).
    базовая премия 4 875 891 429 сум (ставка 4,8759% × 100 млрд)
    уровень «Критический» → вилка 10–90%; 3 убытка ≥ 2 → верх вилки 90%
    премия при 90%: узел движка 2% (×0,75) × (0,15 / 0,75) = базовая × 0,15 = 731 383 714 сум
    плюс франшиза 90% по риску «землетрясение» (доля риска 20,2% нетто):
    минус 731 383 714 × 0,202 × 0,9 = 132 965 559 → итого 598 418 155 сум.
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from tmpdb import temp_db  # noqa: E402
from app import db, franchise as fr, risk_analytics as ra  # noqa: E402

AS_OF = date(2026, 9, 21)
NO_EXT = {"weights": {"external_stats": 0}}
MARKET = {"rate_pct": 0.18, "label": "тест"}

EX_A = ({"class_code": "9", "object_type": "Склад", "sum_insured": 4.2e9, "object_value": 4.2e9,
         "region": "Ташкентская область", "term_months": 12, "construction": "reinforced",
         "activity": "warehouse"},
        {"protection": "alarm_guard", "losses_3y": {"count": 0, "amount": 0}})
EX_B = ({"class_code": "8/9", "object_type": "Производство", "sum_insured": 50e9, "object_value": 50e9,
         "region": "г. Ташкент", "term_months": 12, "construction": "reinforced", "activity": "food"},
        {"protection": "alarm_guard", "seismic_zone": "8–9", "wear_pct": 30,
         "losses_3y": {"count": 1, "amount": 300e6}, "deductible": {"pct": 0.5},
         "compartments": {"count": 4, "largest_value": 18e9},
         "bi": {"monthly_amount": 1e9, "recovery_months": 4}, "fire_station_km": 5, "floors": 2})
EX_C = ({"class_code": "8", "object_type": "Склад", "sum_insured": 100e9, "object_value": 200e9,
         "region": "Ферганская область", "term_months": 12, "construction": "wood",
         "activity": "flammable"},
        {"protection": "none", "seismic_zone": 9, "wear_pct": 60,
         "losses_3y": {"count": 3, "amount": 9e9}, "fire_station_km": 25, "floors": 1})


def eq(got, want, what, tol=1.0):
    ok = abs(got - want) <= tol if isinstance(want, (int, float)) and not isinstance(want, bool) else got == want
    assert ok, f"{what}: получено {got}, ожидалось {want}"
    print(f"  ok  {what}: {got}")


def fix_company(con):
    for t in ("solvency_reports", "reserve_reports", "company_financials"):
        con.execute(f"DELETE FROM {t}")
    con.execute("INSERT INTO company_financials (report_date, own_funds, reserves, source) VALUES (?,?,?,?)",
                ("2026-07-01", 180e9, 240e9, "временно, тест"))


def run(must, optional):
    with temp_db():
        with db.tx() as con:
            fix_company(con)
            return fr.recommend_franchise(con, must, optional, thresholds=NO_EXT,
                                          market=MARKET, as_of=AS_OF)


def check_shape(r):
    for k in ("ok", "level", "decision", "franchise", "basis", "premium_effect", "options",
              "tariff_advice", "explanation", "llm_text", "thresholds_used"):
        assert k in r, f"нет поля {k}"
    assert [o["key"] for o in r["options"]] == ["base", "franchise", "measures"], r["options"]
    assert r["calibrated"] == 0 and r["franchise"]["calibrated"] == 0
    assert r["llm_text"].strip()


def test_a_low():
    print("\nПример A — низкий риск, склад класс 9")
    r = run(*EX_A)
    check_shape(r)
    eq(r["level"], "Низкий", "уровень риска")
    eq(r["franchise"]["pct"], 0.0, "франшиза, %", 1e-9)
    eq(r["franchise"]["type"], "нет", "тип франшизы")
    eq(r["decision"], "принять", "решение")
    eq(r["options"][0]["premium"], 6_048_000, "базовая премия, сум")
    eq(r["options"][1]["premium"], 6_048_000, "премия с франшизой, сум")
    eq(r["premium_effect"]["delta"], 0, "эффект на премию, сум")
    assert any("убытков за 3 года не было" in b for b in r["basis"]), r["basis"]


def test_b_high_band():
    print("\nПример B — повышенный риск, производство 8/9")
    r = run(*EX_B)
    check_shape(r)
    eq(r["level"], "Повышенный", "уровень риска")
    eq(r["franchise"]["band_pct"], [2, 5], "вилка по уровню")
    eq(r["franchise"]["pct"], 5.0, "франшиза, %", 1e-9)
    eq(r["franchise"]["type"], "безусловная", "тип франшизы")
    eq(r["franchise"]["amount"], 2_500_000_000, "франшиза в сумах")
    eq(r["decision"], "принять с безусловной франшизой", "решение")
    base = r["options"][0]["premium"]
    eq(base, 612_927_966, "базовая премия, сум", 2)
    want = base * 0.60 / 0.92
    eq(r["options"][1]["premium"], round(want), "премия при франшизе 5%, сум", 2)
    eq(r["premium_effect"]["delta"], round(want - base), "эффект на премию, сум", 2)
    assert r["tariff_advice"]["rate_market_pct"] == 0.18
    assert r["tariff_advice"]["raises"], "должны быть факторы, поднимающие ставку"


def test_c_critical():
    print("\nПример C — критический риск, дерево, 9 баллов, износ 60%")
    r = run(*EX_C)
    check_shape(r)
    eq(r["level"], "Критический", "уровень риска")
    eq(r["franchise"]["pct"], 90.0, "франшиза, %", 1e-9)
    eq(r["decision"], "принять только с франшизой либо отказать", "решение")
    p = r["franchise"]["peril"]
    assert p and p["code"] == "earthquake" and p["pct"] == 90, p
    print(f"  ok  франшиза по риску: {p['name']} {p['pct']}% (доля риска {p['share_of_net_pct']}% нетто)")
    base = r["options"][0]["premium"]
    with_fr = r["options"][1]["premium"]
    assert with_fr < base, (base, with_fr)
    print(f"  ok  премия: базовая {base}, с франшизой {with_fr}, эффект {r['premium_effect']['delta']}")
    assert "исключить риск землетрясения" in " ".join(r["basis"])


def test_thresholds_in_db():
    print("\nПороги франшизы правятся админом (risk_thresholds)")
    with temp_db():
        with db.tx() as con:
            fix_company(con)
            ra.save_thresholds(con, {"franchise_by_level": {"Низкий": [0, 1], "Умеренный": [1, 2],
                                                            "Повышенный": [3, 3], "Высокий": [5, 10],
                                                            "Критический": [10, 90]},
                                     "weights": {"external_stats": 0}}, "тест")
            r = fr.recommend_franchise(con, *EX_B, market=MARKET, as_of=AS_OF)
            eq(r["franchise"]["pct"], 3.0, "франшиза после правки порогов, %", 1e-9)
            eq(r["thresholds_source"]["what"], "правка администратора", "источник порогов")
            bad = ra.check_thresholds(ra._merge(ra.DEFAULT_THRESHOLDS,
                                                {"franchise_multipliers": {"0": 0.5, "1": 0.9}}))
            assert any("franchise_multipliers" in e for e in bad), bad
            print(f"  ok  неверные пороги отклоняются: {[e for e in bad if 'franchise' in e]}")


def main():
    test_a_low()
    test_b_high_band()
    test_c_critical()
    test_thresholds_in_db()
    print("\nВсе примеры сошлись.")


if __name__ == "__main__":
    main()
