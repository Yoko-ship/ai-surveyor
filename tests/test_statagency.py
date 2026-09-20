"""
Проверка обработки данных агентства статистики на ЗАРАНЕЕ ИЗВЕСТНЫХ числах.

Запуск из корня:  set PYTHONIOENCODING=utf-8 && sandbox\\.venv\\Scripts\\python.exe tests\\test_statagency.py

Сети тест не трогает: загрузка (app/stat_sources.py) здесь не вызывается вовсе.
Рабочая база data/surveyor.db НЕ трогается: схема разворачивается во ВРЕМЕННОМ файле,
все цифры ниже выдуманы и подобраны так, чтобы результат считался в уме.

Что проверяется:
  1. премии на душу населения:  100 млн сум / 2 тыс. человек = 50 000 сум на жителя;
  2. проникновение:             2 000 млн сум (= 2 млрд) / 200 млрд сум ВРП = 1,0000 % ВРП;
  3. жильё к жилому фонду:      500 млн сум / 1 000 000 кв. м = 500 сум на кв. м,
                                по регионам — честное «нет данных»;
  4. ОСАГО к автопарку:         всегда «нет данных» с причиной и ссылкой на вопрос 47;
  5. индексация по ИПЦ:         1 000 сум из 2023 в 2025 при индексах 110 % и 120 %
                                = 1 000 × 1,1 × 1,2 = 1 320 сум; обратно = 1 000 / 1,32;
                                нет индекса за год цепочки → «нет данных», а не пропуск года;
  6. индекс цен на жильё:       Q2 110 %, Q3 90 %, Q4 105 % → коэффициент 1,0395;
                                разрыв цепочки кварталов → «нет данных» с перечнем недостающих;
  7. источник для оценки:       статус «Агентство статистики», ссылка на набор, приведение цены.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import statagency as sa                      # noqa: E402

FAILED = []


def check(name, got, want, eps=1e-6):
    ok = (got == want) if not isinstance(want, float) else (got is not None and abs(got - want) < eps)
    print(("  ОК   " if ok else "  СБОЙ ") + name + ": получено %r, ожидалось %r" % (got, want))
    if not ok:
        FAILED.append(name)


def check_true(name, cond, hint=""):
    print(("  ОК   " if cond else "  СБОЙ ") + name + ("" if cond else " — " + hint))
    if not cond:
        FAILED.append(name)


def make_db() -> sqlite3.Connection:
    """Временная база: схема проекта + выдуманные ряды."""
    path = Path(tempfile.mkdtemp(prefix="statagency_")) / "test.db"
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.executescript((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))

    def m(report_date, row_key, row_name, premiums):
        con.execute("INSERT INTO market_stats (report_date,row_key,row_name,premiums_ytd,payouts_ytd,"
                    "liabilities,source_file,loaded_at) VALUES (?,?,?,?,0,0,'ТЕСТ','2026-01-02')",
                    (report_date, row_key, row_name, premiums))

    def sr(dataset_id, key, region, period, value, unit, url="https://stat.uz/тест"):
        con.execute("INSERT INTO stat_series (source,dataset_id,key,region,period,value,unit,"
                    "fetched_at,url) VALUES ('stat.uz',?,?,?,?,?,?,'2026-01-02T00:00:00+05:00',?)",
                    (dataset_id, key, region, period, value, unit, url))

    # срез на 1 января 2026 — это полный 2025 год
    m("2026-01-01", "region:ANDIJON", "Андижанская область", 100.0)
    m("2026-01-01", "total", "Республика Узбекистан", 2000.0)
    m("2026-01-01", "cls8_9", "Классы 8, 9", 500.0)
    m("2026-01-01", "osago", "ОСАГО", 700.0)

    sr("population", "1703", "region:ANDIJON", "2025", 2.0, "тысяч человек")
    sr("population", "1700", "total", "2025", 40.0, "тысяч человек")
    sr("grp", "1700", "total", "2025", 200.0, "в текущих ценах, млрд сум")
    sr("housing_fund_area", "1700", "total", "2025", 1_000_000.0, "на конец года, кв. м")
    sr("cpi_annual", "1700", "", "2024", 110.0, "процент")
    sr("cpi_annual", "1700", "", "2025", 120.0, "процент")
    sr("construction_price_index_y", "1700", "", "2025", 105.0, "процент")
    sr("cars_per_100_households", "1700", "", "2025", 45.0, "штук")
    for per, val in (("2025-Q1", 100.0), ("2025-Q2", 110.0), ("2025-Q3", 90.0), ("2025-Q4", 105.0)):
        sr("house_price_index", "1703", "region:ANDIJON", per, val, "процент")
    con.commit()
    return con


def main():
    con = make_db()

    print("\n0. Справочные мелочи")
    check("код СОАТО → ключ региона", sa.region_key("1703"), "region:ANDIJON")
    check("русское название → ключ региона", sa.region_key("Андижанская область"), "region:ANDIJON")
    check("чужое название не опознаётся", sa.region_key("Москва"), "")
    snap = sa.market_snapshot(con)
    check("срез 1 января — это полный предыдущий год", snap["year"], 2025)
    check("срез помечен как полный", snap["partial"], False)

    print("\n1. Премии на душу населения")
    ppc = sa.premiums_per_capita(con, region="region:ANDIJON")
    row = ppc["Строки"][0]
    check("значение, сум на жителя", row["Значение"], 50000.0)
    check_true("формула словами есть", "разделить на численность" in ppc["Формула"])
    check_true("оба источника названы", len(ppc["Источники"]) == 2
               and any("НАПП" in str(x.get("Название")) for x in ppc["Источники"])
               and any(x.get("id") == "population" for x in ppc["Источники"]))
    check_true("есть предупреждение про начало года",
               any("начало года" in w for w in ppc["Предупреждения"]))
    check("период населения указан", ppc["Период населения"], "на начало 2025 года")

    print("\n2. Проникновение (премии к ВРП)")
    pen = sa.penetration(con, region="total")
    check("проникновение, % ВРП", pen["Строки"][0]["Значение"], 1.0)
    check_true("ссылка на набор ВРП есть", bool(pen["Строки"][0]["Ссылка на набор ВРП"]))
    pen_a = sa.penetration(con, region="region:ANDIJON")
    check("нет ВРП региона — нет данных", pen_a["Строки"][0]["Статус"], sa.STATUS_NODATA)
    check_true("причина названа", "ВРП" in (pen_a["Строки"][0].get("Причина") or ""))

    print("\n3. Имущество к жилому фонду")
    h = sa.housing_vs_fund(con)
    check("сум премии на кв. м", h["Значение"], 500.0)
    check("по регионам — нет данных", h["По регионам"]["Статус"], sa.STATUS_NODATA)
    check_true("причина по регионам объяснена",
               "регион × класс" in h["По регионам"]["Причина"])
    check_true("предупреждение: классы 8 и 9 — не только жильё",
               any("не только жильё" in w for w in h["Предупреждения"]))

    print("\n4. ОСАГО к автопарку")
    o = sa.osago_vs_fleet(con)
    check("статус", o["Статус"], sa.STATUS_NODATA)
    check("значение не придумано", o["Значение"], None)
    check_true("ссылка на открытый вопрос", "47" in o["Вопрос"])
    check_true("рядом показано, что есть на самом деле",
               "Автомобилей на 100 домохозяйств" in o["Что есть вместо этого"])

    print("\n5. Индексация страховой стоимости")
    ix = sa.indexation(con, 1000, 2023, 2025, "cpi")
    check("коэффициент 1,1 × 1,2", ix["Коэффициент"], 1.32)
    check("приведённая сумма", ix["Значение"], 1320.0)
    check("множителей в цепочке", len(ix["Множители"]), 2)
    back = sa.indexation(con, 1320, 2025, 2023, "cpi")
    check("обратное приведение", back["Значение"], 1000.0, eps=0.01)
    gap = sa.indexation(con, 1000, 2020, 2025, "cpi")
    check("разрыв цепочки — нет данных", gap["Статус"], sa.STATUS_NODATA)
    check_true("недостающие годы названы", "2021" in (gap.get("Причина") or ""))
    same = sa.indexation(con, 1000, 2025, 2025, "cpi")
    check("один и тот же год — сумма не меняется", same["Значение"], 1000.0)
    con_ix = sa.indexation(con, 1000, 2024, 2025, "construction")
    check("индекс цен в строительстве", con_ix["Значение"], 1050.0)
    check_true("источник — набор строительных индексов",
               con_ix["Источники"][0]["id"] == "construction_price_index_y")

    print("\n6. Индекс цен на жильё по кварталам")
    ch = sa.house_index_chain(con, "region:ANDIJON", "2025-Q1", "2025-Q4")
    check("коэффициент 1,10 × 0,90 × 1,05", ch["Коэффициент"], 1.0395)
    check("кварталов в цепочке", len(ch["Множители"]), 3)
    check_true("оговорка: это индекс, а не цена",
               any("индекс" in w.lower() for w in ch["Предупреждения"]))
    ch2 = sa.house_index_chain(con, "region:ANDIJON", "2025-Q1")
    check("без целевого квартала берётся последний", ch2["Квартал целевой"], "2025-Q4")
    # разрыв цепочки: индексов за 2023 и 2024 годы в наборе нет, есть только 2025 год
    gap_q = sa.house_index_chain(con, "region:ANDIJON", "2023-Q1", "2025-Q4")
    check("разрыв квартальной цепочки — нет данных", gap_q["Статус"], sa.STATUS_NODATA)
    check("заниженный коэффициент не выдаётся", gap_q["Коэффициент"], None)
    check("недостающих кварталов (2023-Q2…2024-Q4)", len(gap_q.get("Недостающие кварталы") or []), 7)
    check_true("перечень недостающих кварталов назван",
               (gap_q.get("Недостающие кварталы") or [])[:2] == ["2023-Q2", "2023-Q3"]
               and "2024-Q4" in gap_q["Недостающие кварталы"]
               and "2025-Q1" not in gap_q["Недостающие кварталы"])
    check_true("причина объясняет разрыв", "разорвана" in (gap_q.get("Причина") or ""))
    check("множителей при разрыве нет", len(gap_q["Множители"]), 0)
    same_q = sa.house_index_chain(con, "region:ANDIJON", "2025-Q4", "2025-Q4")
    check("один и тот же квартал — коэффициент 1", same_q["Коэффициент"], 1.0)
    back_q = sa.house_index_chain(con, "region:ANDIJON", "2025-Q4", "2025-Q2")
    check("целевой квартал раньше исходного — нет данных", back_q["Статус"], sa.STATUS_NODATA)
    # приведение с базой в квартале, которого нет в наборе, не должно давать цену
    src_gap = sa.valuation_source(con, {"region": "region:ANDIJON", "base_price": 1_000_000,
                                        "base_period": "2023-Q1"})
    check("оценка с разорванной цепочкой цены не даёт", src_gap.get("indexed_price"), None)

    print("\n7. Источник «Агентство статистики» для оценки недвижимости")
    src = sa.valuation_source(con, {"region": "region:ANDIJON", "base_price": 1_000_000,
                                    "base_period": "2025-Q1"})
    check("статус источника", src["status"], "Агентство статистики")
    check("цен в медиану не даёт", src["prices"], [])
    check("приведённая цена", src["indexed_price"], 1_039_500.0)
    check_true("ссылка на набор есть", src["url"].startswith("https://stat.uz/"))
    check_true("оговорка про неутверждённый метод",
               any("не утверждён" in x for x in src["limitations"]))
    empty = sa.valuation_source(con, {"region": "region:ANDIJON"})
    check("без базовой цены приведения нет", empty.get("indexed_price"), None)
    check_true("объяснено, что источник даёт", "индекс" in empty["reason"].lower()
               or "цен квадратного метра" in empty["reason"])

    print()
    if FAILED:
        print("СБОЕВ: %d — %s" % (len(FAILED), "; ".join(FAILED)))
        sys.exit(1)
    print("Все проверки пройдены.")


if __name__ == "__main__":
    main()
