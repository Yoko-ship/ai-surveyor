"""
Разбор Excel-файлов (xlsx / xlsm / xls) из data/inbox.

Для каждого файла создаёт папку data/parsed/<имя_файла>/:
  - report.md       — обзор: листы, размеры, объединённые ячейки, первые строки
  - <лист>.csv      — полное содержимое листа (объединённые ячейки заполнены)

Запуск:
  python tools/inspect_excel.py              # все файлы из data/inbox
  python tools/inspect_excel.py путь\к\файлу.xlsx
"""
import csv
import re
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "data" / "inbox"
PARSED = ROOT / "data" / "parsed"
PREVIEW_ROWS = 40
PREVIEW_COLS = 20
EXTENSIONS = {".xlsx", ".xlsm", ".xls"}


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "sheet"


def cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).replace("\n", " ").strip()


def read_xlsx(path: Path):
    """Возвращает список (имя_листа, состояние, строки, объединённые_диапазоны)."""
    wb = load_workbook(path, data_only=True)  # data_only: значения, а не формулы
    sheets = []
    for ws in wb.worksheets:
        merged = [str(r) for r in ws.merged_cells.ranges]
        grid = [[c for c in row] for row in ws.iter_rows(values_only=True)]
        # Заполняем объединённые ячейки значением левой верхней —
        # в статистических таблицах так устроены многоуровневые заголовки.
        for rng in ws.merged_cells.ranges:
            top = grid[rng.min_row - 1][rng.min_col - 1] if rng.min_row - 1 < len(grid) else None
            for r in range(rng.min_row - 1, min(rng.max_row, len(grid))):
                for c in range(rng.min_col - 1, min(rng.max_col, len(grid[r]))):
                    grid[r][c] = top
        sheets.append((ws.title, ws.sheet_state, grid, merged))
    return sheets


def read_xls(path: Path):
    frames = pd.read_excel(path, sheet_name=None, header=None, engine="xlrd")
    return [
        (name, "visible", df.where(pd.notna(df), None).values.tolist(), [])
        for name, df in frames.items()
    ]


def trim(grid):
    """Убирает пустые строки и столбцы в конце."""
    rows = [list(r) for r in grid]
    while rows and all(cell_text(v) == "" for v in rows[-1]):
        rows.pop()
    width = 0
    for r in rows:
        for i in range(len(r) - 1, -1, -1):
            if cell_text(r[i]) != "":
                width = max(width, i + 1)
                break
    return [r[:width] + [None] * (width - len(r)) for r in rows], width


def process(path: Path) -> Path:
    out_dir = PARSED / safe_name(path.stem)
    out_dir.mkdir(parents=True, exist_ok=True)

    sheets = read_xls(path) if path.suffix.lower() == ".xls" else read_xlsx(path)

    lines = [f"# {path.name}", "", f"Листов: {len(sheets)}", ""]
    for name, state, grid, merged in sheets:
        rows, width = trim(grid)
        csv_path = out_dir / f"{safe_name(name)}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerows([[cell_text(v) for v in r] for r in rows])

        lines += [
            f"## Лист «{name}»" + ("" if state == "visible" else f" ({state})"),
            "",
            f"- Размер: {len(rows)} строк × {width} столбцов",
            f"- Объединённых диапазонов: {len(merged)}"
            + (f" (первые: {', '.join(merged[:10])})" if merged else ""),
            f"- CSV: `{csv_path.name}`",
            "",
        ]
        if rows:
            cols = min(width, PREVIEW_COLS)
            lines.append("| # | " + " | ".join(chr(65 + i) if i < 26 else f"C{i+1}" for i in range(cols)) + " |")
            lines.append("|---" * (cols + 1) + "|")
            for i, r in enumerate(rows[:PREVIEW_ROWS], start=1):
                cells = [cell_text(v).replace("|", "/")[:40] for v in r[:cols]]
                lines.append(f"| {i} | " + " | ".join(cells) + " |")
            if len(rows) > PREVIEW_ROWS:
                lines.append(f"\n… ещё {len(rows) - PREVIEW_ROWS} строк в CSV")
        lines.append("")

    report = out_dir / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main():
    targets = [Path(a) for a in sys.argv[1:]] or sorted(
        p for p in INBOX.iterdir() if p.suffix.lower() in EXTENSIONS and not p.name.startswith("~$")
    )
    if not targets:
        print(f"Нет файлов. Положите .xlsx/.xls в {INBOX}")
        return
    for path in targets:
        try:
            print(f"OK   {path.name} -> {process(path)}")
        except Exception as e:  # один битый файл не должен останавливать остальные
            print(f"FAIL {path.name}: {e}")


if __name__ == "__main__":
    main()
