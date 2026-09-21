"""
Проверка разбора строк ОСГОР из отчётов НАПП (лист 1.4 — деньги, лист 1.5 — договоры).

Запуск из корня:  set PYTHONIOENCODING=utf-8 && sandbox\\.venv\\Scripts\\python.exe tests\\test_market_osgor.py

Что проверяется:
  1. строка «ish beruvchining fuqarolik javobgarligi» → ключ osgor, а аннуитетная строка
     «... (annuitet)» → отдельный ключ osgor_annuity (порядок образцов в KEYS важен:
     общий образец не должен перехватывать аннуитетную строку);
  2. строки ОСГОР не путаются с ОСАГО и с блоком «Majburiy sug'urta» в целом;
  3. на срезе 01.01.2026 (итог 2025 года) из отчёта за IV квартал 2025 читаются
     заранее известные числа отчёта: премии 285 879,358 млн сум, выплаты 135 434,461 млн сум,
     убыточность 47,375 %, договоров действующих 51 144;
  4. в 1.5 у ОСГОР разделяются «действующие на дату» и «заключённые за период».

База data/surveyor.db не трогается: читаются только разобранные CSV из data/parsed.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import market_stats as ms  # noqa: E402

FOLDER = "Страх_отчет(IV_чорак_2025_йил_)_uz_—_filtr"


def eq(got, want, what):
    assert got == want, f"{what}: получено {got}, ожидалось {want}"
    print(f"  ok  {what}: {got}")


def main():
    # 1–2. ключи строк
    eq(ms.row_key("ish beruvchining fuqarolik javobgarligi")[0], "osgor", "ключ строки ОСГОР")
    eq(ms.row_key("ish beruvchining fuqarolik javobgarligi (annuitet)")[0], "osgor_annuity",
       "ключ аннуитетной строки ОСГОР")
    eq(ms.row_key("transport vositalari egalarining fuqarolik javobgarligi")[0], "osago", "ОСАГО не задет")
    eq(ms.row_key("Majburiy sug'urta, shu jumladan:")[0], "mandatory", "итог обязательного не задет")

    sheet = ROOT / "data" / "parsed" / FOLDER / "1.4.csv"
    if not sheet.exists():
        print("Отчёта за IV квартал 2025 в data/parsed нет — числовая часть теста пропущена.")
        return
    # 3. числа среза 01.01.2026 = итог 2025 года
    rows = {(d, k): (prem, pay) for d, k, _ru, prem, pay, _l in ms.load_sheet(sheet)}
    prem, pay = rows[("2026-01-01", "osgor")]
    eq(round(prem, 3), 285879.358, "премии ОСГОР за 2025 год, млн сум")
    eq(round(pay, 3), 135434.461, "выплаты ОСГОР за 2025 год, млн сум")
    eq(round(pay / prem * 100, 3), 47.375, "убыточность ОСГОР за 2025 год, %")

    # 4. договоры из листа 1.5
    cnt = ms.contracts_15()
    eq(cnt[("osgor", "2026-01-01")], (51144, 51046), "ОСГОР: договоров действующих и заключённых за 2025 год")
    eq(cnt[("osgor_annuity", "2026-01-01")], (1207, 239), "аннуитеты ОСГОР: действующие и заключённые за 2025 год")
    print("Все проверки пройдены.")


if __name__ == "__main__":
    main()
