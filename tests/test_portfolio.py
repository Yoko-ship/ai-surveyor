"""
Контрольные примеры портфельного аудита. Запуск из корня проекта:
    set PYTHONIOENCODING=utf-8
    python tools\db_build.py
    sandbox\.venv\Scripts\python.exe tests\test_portfolio.py
Без pytest: обычные assert. Функции модуля вызываются напрямую, сервер не нужен.
Загрузка идёт во ВРЕМЕННУЮ копию базы (tests/tmpdb.py) — рабочая data/surveyor.db не меняется.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openpyxl  # noqa: E402

from tmpdb import temp_db  # noqa: E402  (tests/tmpdb.py)
from app import db  # noqa: E402
from app import portfolio as pf  # noqa: E402

HEADERS = ["Договор №", "Вид страхования", "Страхователь", "Филиал", "Страховая сумма", "Страховая стоимость",
           "Премия", "Ставка %", "Франшиза", "Срок, дней"]

# пять договоров: нормальный, ниже минимума, сумма выше стоимости, без ставки (ставка из премии), КАСКО по номеру
ROWS = [
    ["0275/0807/26/0001", "0807.Страхование имущества от огня и стихийных бедствий", "ООО «Ташкент Логистик»",
     "Ташкентский", 4_200_000_000, 4_200_000_000, 12_600_000, 0.300, 42_000_000, 365],
    ["0275/0807/26/0002", "0807.Страхование имущества от огня и стихийных бедствий", "ООО «Самарканд Текстиль»",
     "Самаркандский", 2_000_000_000, 2_000_000_000, 600_000, 0.030, 0, 365],
    ["0275/0807/26/0003", "0807.Страхование имущества от огня и стихийных бедствий", "ЧП «Бухара Савдо»",
     "Бухарский", 1_500_000_000, 1_200_000_000, 4_500_000, 0.300, 15_000_000, 365],
    ["0275/0807/26/0004", "0807.Страхование имущества от огня и стихийных бедствий", "АО «Наманган Ун»",
     "Наманганский", 900_000_000, 900_000_000, 2_700_000, None, 9_000_000, 365],
    ["0275/0311/26/0005", "КАСКО «Premium»", "Ходжаев А.А.", "Ташкентский",
     146_100_000, 146_100_000, 3_900_000, 2.6694, 1_461_000, 365],
]


def make_xlsx(folder: Path) -> Path:
    """Файл как реальная выгрузка: шапка отчёта сверху, потом заголовки, потом договоры и строка «Итого»."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Договоры"
    ws.append(["Выгрузка договоров за сентябрь 2026"])
    ws.append([])
    ws.append(HEADERS)
    for r in ROWS:
        ws.append(r)
    ws.append(["Итого", None, None, None, sum(r[4] for r in ROWS), None, sum(r[6] for r in ROWS)])
    path = folder / "выгрузка_тест.xlsx"
    wb.save(path)
    return path


def test_mapping_and_review():
    with temp_db(), tempfile.TemporaryDirectory() as tmp:
        path = make_xlsx(Path(tmp))
        sh = pf.read_sheet(path)
        assert sh["header_row"] == 3, sh["header_row"]
        m = pf.map_columns(sh["headers"])
        assert not m["missing"], m
        want = {"external_no": "Договор №", "product": "Вид страхования", "policyholder": "Страхователь",
                "branch": "Филиал", "sum_insured": "Страховая сумма", "value_amount": "Страховая стоимость",
                "premium": "Премия", "rate": "Ставка %", "franchise": "Франшиза", "term_days": "Срок, дней"}
        assert m["names"] == want, m["names"]
        print("сопоставление: все 10 колонок найдены")

        out = pf.import_file(path, path.name)
        rows = {r["external_no"]: r for r in out["rows"]}
        assert len(rows) == 5, f"строк {len(rows)} — строка «Итого» должна быть отброшена"
        assert out["batch_id"] > 0

        ok = rows["0275/0807/26/0001"]
        assert ok["product_code"] == "0807" and ok["applied_rate_pct"] == 0.3
        assert ok["verdict"] != "отклонено", ok["violations"]
        assert not any(v["rule"] == "min_rate" for v in ok["violations"])

        low = rows["0275/0807/26/0002"]
        assert low["verdict"] == "отклонено", low
        assert any(v["rule"] == "min_rate" and v["status"] == "stop" for v in low["violations"])
        assert any(v["rule"] == "no_franchise" for v in low["violations"])

        over = rows["0275/0807/26/0003"]
        assert any(v["rule"] == "sum_over_value" for v in over["violations"]) and over["verdict"] == "отклонено"

        norate = rows["0275/0807/26/0004"]
        assert abs(norate["applied_rate_pct"] - 0.3) < 1e-6, norate["applied_rate_pct"]   # 2,7 млн / 900 млн

        kasko = rows["0275/0311/26/0005"]
        assert kasko["product_code"] == "0311" and kasko["class_code"] == "3" and kasko["object_type"] == "Легковой"
        assert not any(v["rule"] == "min_rate" for v in kasko["violations"])

        s = out["summary"]
        assert s["rows_total"] == 5 and s["below_min"] == 1 and s["sum_over_value"] == 1 and s["no_franchise"] == 1
        assert s["lost_premium"] > 0 and s["top_branches"] and s["top_products"][0]["name"] == "0807"
        print("проверка: ниже минимума → отклонено; сумма выше стоимости → отклонено; нормальный —", ok["verdict"])
        print(f"сводка: недополученная премия {s['lost_premium']:,.0f} сум; топ-филиал {s['top_branches'][0]['name']}")

        # то же — из базы
        with db.tx() as con:
            saved = pf.batch_rows(con, out["batch_id"])
            stops = pf.batch_rows(con, out["batch_id"], "отклонено")
        assert len(saved) == 5 and len(stops) == 2
        assert pf.summarize(saved)["below_min"] == 1
        print("база: 5 строк сохранено, из них отклонено 2")


def test_missing_column_message():
    """Без колонки со страховой суммой — понятная ошибка по-русски."""
    with temp_db(), tempfile.TemporaryDirectory() as tmp:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Договор №", "Страхователь", "Премия"])
        ws.append(["0275/0807/26/0009", "Кто-то", 100])
        p = Path(tmp) / "плохой.xlsx"
        wb.save(p)
        try:
            pf.import_file(p, p.name)
            assert False, "ожидалась ошибка"
        except ValueError as e:
            assert "Страховая сумма" in str(e), str(e)
            print("ошибка объяснена:", str(e)[:70], "…")


def test_product_code_from_contract_no():
    known = {"0807": ["8"], "0311": ["3"]}
    assert pf.product_code_of("0807.Страхование имущества", None, known) == "0807"
    assert pf.product_code_of(807, None, known) == "0807"
    assert pf.product_code_of("Имущество", "0275/0311/26/0030", known) == "0311"
    assert pf.product_code_of("Неизвестно", "АБВ-12", known) is None
    print("код продукта: из текста, из числа, из номера договора")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("Все проверки пройдены.")
