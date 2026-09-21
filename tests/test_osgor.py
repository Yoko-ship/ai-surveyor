"""
Контрольные примеры по ОСГОР (продукт 1323, класс 13). Всё считается по акту:
ПР = СС × ТБ × КСТ / 100, ТБ = 0,1 % (прил. № 9 к Правилам ПКМ № 177, разд. I п. 1, разд. III пп. 1–2).

Запуск из корня:  set PYTHONIOENCODING=utf-8 && sandbox\\.venv\\Scripts\\python.exe tests\\test_osgor.py

Примеры посчитаны вручную:

1. Строительная компания, ФОТ за 12 мес. 4 800 000 000 сум, вид деятельности «Земледелие и
   животноводство, охота» ОКЭД 01000 — категория 13, КСТ 4,000; договор на год:
        4 800 000 000 × 0,1 × 4,000 / 100 = 4 800 000 000 × 0,004 = 19 200 000 сум
   нетто 75 % = 14 400 000, РВД 25 % = 4 800 000.

2. Проектная организация, ФОТ 900 000 000 сум, ОКЭД 41100 «Разработка строительных проектов» —
   категория 1, КСТ 0,571; договор на 120 дней (деятельность короче года):
        900 000 000 × 0,1 × 0,571 / 100 = 513 900 сум за год
        513 900 / 365 × 120 = 168 953,42 сум

3. Микропредприятие, ФОТ 30 000 000 сум, тот же КСТ 0,571, договор на год:
        30 000 000 × 0,000571 = 17 130 сум — ниже минимума
        минимум 0,25 БРВ; при БРВ 412 000 сум это 103 000 сум → к уплате 103 000 сум
   (размер БРВ в тесте задаётся параметром: официальное значение подтверждает заказчик).

4. Доплата премии после выплаты (ЗРУ-210 ст. 10 ч. 3; п. 27 Правил): по договору 1 выплачено
   500 000 000 сум, до конца срока 200 дней:
        500 000 000 × 0,004 = 2 000 000; 2 000 000 / 365 × 200 = 1 095 890,41 сум
        остаток страховой суммы 4 800 000 000 − 500 000 000 = 4 300 000 000 сум

База data/surveyor.db не изменяется: тест только читает market_stats (и работает без базы).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import osgor  # noqa: E402

BRV_TEST = 412_000.0   # значение для теста, не утверждение о действующем БРВ


def eq(got, want, what, tol=0.01):
    ok = abs(got - want) <= tol if isinstance(want, (int, float)) else got == want
    assert ok, f"{what}: получено {got}, ожидалось {want}"
    print(f"  ok  {what}: {got}")


def main():
    acts = osgor.load_activities()
    eq(len(acts), 934, "позиций в классификации видов деятельности")
    kst_values = sorted({a["kst"] for a in acts})
    eq(min(kst_values), 0.571, "минимальный КСТ")
    eq(max(kst_values), 7.714, "максимальный КСТ")
    eq(len({a["category"] for a in acts}), 20, "категорий профессионального риска")

    print("\nПример 1 — ФОТ 4,8 млрд, ОКЭД 01000, договор на год")
    a1 = osgor.find_activity(okved="01000", activities=acts)
    eq(a1["kst"], 4.000, "КСТ по коду 01000")
    eq(a1["category"], 13, "категория профессионального риска")
    p1 = osgor.premium(4_800_000_000, a1["kst"], term_days=365, brv=BRV_TEST, activity=a1)
    eq(p1["rate_pct_of_payroll"], 0.4, "ставка к ФОТ, %")
    eq(p1["premium"], 19_200_000, "премия за год")
    eq(p1["net"], 14_400_000, "нетто-часть 75 %")
    eq(p1["expense"], 4_800_000, "РВД 25 %")
    eq(p1["min_applied"], False, "минимум не применялся")

    print("\nПример 2 — ФОТ 900 млн, ОКЭД 41100, договор на 120 дней")
    a2 = osgor.find_activity(okved="41100", activities=acts)
    eq(a2["kst"], 0.571, "КСТ по коду 41100")
    p2 = osgor.premium(900_000_000, a2["kst"], term_days=120, brv=BRV_TEST, activity=a2)
    eq(p2["premium_base"], 513_900, "премия за полный год")
    eq(round(p2["premium"], 2), 168_953.42, "премия за 120 дней")

    print("\nПример 3 — ФОТ 30 млн, КСТ 0,571, упор в минимум 0,25 БРВ")
    p3 = osgor.premium(30_000_000, 0.571, term_days=365, brv=BRV_TEST)
    eq(p3["premium_term"], 17_130, "расчётная премия")
    eq(p3["min_premium"], 103_000, "минимум 0,25 БРВ")
    eq(p3["premium"], 103_000, "премия к уплате")
    eq(p3["min_applied"], True, "минимум применён")

    print("\nПример 4 — доплата премии после выплаты")
    d = osgor.premium_after_payout(4_800_000_000, 4.000, 500_000_000, days_left=200)
    eq(d["remaining_after"], 4_300_000_000, "остаток страховой суммы")
    eq(round(d["extra_premium"], 2), 1_095_890.41, "доплата премии")

    print("\nВид деятельности вне перечня → КСТ 3,400 (прил. № 9, разд. I, п. 6)")
    a0 = osgor.find_activity(okved="99999", name="торговля лунным грунтом", activities=acts)
    eq(a0["kst"], 3.400, "КСТ по умолчанию")
    eq(a0["listed"], False, "позиция не найдена в классификации")

    print("\nПерерасчёт при изменении ФОТ (п. 22 Правил)")
    ch = osgor.premium_after_payroll_change(4_800_000_000, 5_800_000_000, 4.000, days_left=182)
    # 1 000 000 000 × 0,004 = 4 000 000; 4 000 000 / 365 × 182 = 1 994 520,55
    eq(round(ch["extra_premium"], 2), 1_994_520.55, "доплата за рост ФОТ на 1 млрд")

    print("\nСквозная оценка: тот же работодатель из примера 1")
    emp = osgor.Employer(
        name="условный работодатель", okved="01000", headcount=600,
        payroll_12m=4_800_000_000, share_harmful=0.35, share_dangerous=0.10,
        accidents_3y=4, severe_3y=1, fatal_3y=0,
        safety_service=False, training_done=True, ppe_provided=False,
        labour_inspection_orders=2, wage_arrears=False, term_days=365,
    )
    con = None
    try:
        from app import db
        con = db.connect()
    except Exception:
        pass
    res = osgor.assess(emp, brv=BRV_TEST, con=con, declared_sum=4_800_000_000, activities=acts)
    eq(res["premium"]["premium"], 19_200_000, "премия в сквозном расчёте")
    codes = {c["code"] for c in res["checks"]}
    for code in ("OSGOR-01", "OSGOR-02", "OSGOR-03", "OSGOR-06", "OSGOR-09", "OSGOR-13"):
        assert code in codes, f"нет проверки {code}"
    print(f"  ok  проверки: {len(res['checks'])} шт., коды {sorted(codes)}")
    assert res["assessment"]["calibrated"] == 0, "оценка риска должна быть помечена calibrated = 0"
    assert res["assessment"]["expected_loss_ratio"] > res["assessment"]["market_loss_ratio"], \
        "плохая охрана труда должна давать убыточность выше рыночной"
    print(f"  ok  ожидаемая убыточность {res['assessment']['expected_loss_ratio'] * 100:.1f} % "
          f"при рынке {res['assessment']['market_loss_ratio'] * 100:.1f} %")
    assert res["decision"]["decision"] in (osgor.DECISION_MEASURES, osgor.DECISION_UNDERWRITER), \
        "работодатель с невыполненными предписаниями не может быть принят без условий"
    print(f"  ok  решение: {res['decision']['decision']}")
    assert any(m["code"] == "OSGOR-M01" for m in res["measures"]), "нет мероприятия по службе охраны труда"
    assert any(m["code"] == "OSGOR-M05" for m in res["measures"]), "нет мероприятия по предписаниям"
    print(f"  ok  мероприятий: {len(res['measures'])}, документов в чек-листе: {len(res['checklist'])}")

    print("\nОбразцовый работодатель — решение «принять»")
    good = osgor.Employer(okved="41100", headcount=50, payroll_12m=900_000_000,
                          share_harmful=0.0, share_dangerous=0.0, accidents_3y=0,
                          safety_service=True, training_done=True, ppe_provided=True,
                          labour_inspection_orders=0, term_days=365, burial_amount=3 * BRV_TEST)
    res2 = osgor.assess(good, brv=BRV_TEST, con=con, declared_sum=900_000_000, activities=acts)
    eq(res2["premium"]["premium"], 513_900, "премия образцового работодателя")
    eq(res2["decision"]["decision"], osgor.DECISION_ACCEPT, "решение")

    print("\nРынок из market_stats")
    m = osgor.market_reference(con)
    if m["loss_ratio"] is not None:
        eq(round(m["loss_ratio"] * 100, 1), 47.4, "убыточность рынка ОСГОР за 2025 год, %")
        prev = [y for y in m["years"] if y["year"] == 2024]
        if prev:
            eq(round(prev[0]["loss_ratio"] * 100, 1), 52.5, "убыточность рынка за 2024 год, %")
    else:
        print("  market_stats пуст — рыночный ориентир пропущен")
    if con is not None:
        con.close()

    print("\nПострочное объяснение:")
    print(osgor.explain(res))
    print("\nВсе примеры сошлись.")


if __name__ == "__main__":
    main()
