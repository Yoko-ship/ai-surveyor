"""
Сборка экрана агента: база → docs/agent_data.json → docs/agent_ui.html.

Запуск: PYTHONIOENCODING=utf-8 sandbox/.venv/Scripts/python.exe tools/ui_build.py
Ключ --data-only — только выгрузить справочники, без сборки html.

Экран берёт справочники из этого файла и ничего не придумывает сам:
все продукты, классы, коэффициенты, базовые ставки, минимальные тарифы и риски — из базы.
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "surveyor.db"
DATA = ROOT / "docs" / "agent_data.json"
TEMPLATE = ROOT / "docs" / "agent_ui.template.html"
OUT = ROOT / "docs" / "agent_ui.html"


def rows(con, sql, *a):
    return [dict(r) for r in con.execute(sql, a)]


def settings(con, keys) -> dict:
    """Значения из app_settings по списку ключей. Нет таблицы или ключа — пустой словарь."""
    out = {}
    try:
        for r in rows(con, "SELECT key, value FROM app_settings"):
            if r["key"] in keys:
                out[r["key"]] = r["value"]
    except sqlite3.Error:
        pass
    return out


def export() -> dict:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    today = __import__("datetime").date.today().isoformat()

    classes = rows(con, "SELECT code, name, kind, branch, group_code FROM classes ORDER BY CAST(code AS INTEGER), code")
    products = rows(con, "SELECT code, name, rate_text, commission_text, commission_pct, pricing_mode, is_general,"
                         " status, note FROM products WHERE status='действует' ORDER BY code")
    pcs = {}
    for r in rows(con, "SELECT product_code, class_code FROM product_classes ORDER BY part_no"):
        pcs.setdefault(r["product_code"], []).append(r["class_code"])

    # действующие минимальные тарифы: отдельно ставка компании и ставка регулятора, по типу плательщика
    mins = {}
    for r in rows(con, """SELECT m.product_code, m.payer_type, m.min_rate_pct, v.level
                          FROM min_rates m JOIN tariff_versions v ON v.id = m.tariff_version_id
                          WHERE v.effective_from <= ? AND (v.effective_to IS NULL OR v.effective_to >= ?)""",
                  today, today):
        slot = "regulator" if r["level"] == "регулятор" else "company"
        d = mins.setdefault(r["product_code"], {"company": {}, "regulator": {}})
        key = r["payer_type"] or "any"
        d[slot][key] = max(d[slot].get(key, 0), r["min_rate_pct"])

    for p in products:
        p["classes"] = pcs.get(p["code"], [])
        p["min"] = mins.get(p["code"], {"company": {}, "regulator": {}})

    # порядок строк = порядок факторов в форме и в расчёте, тот же, что берёт движок (по id)
    coefficients = rows(con, "SELECT factor_code, factor_name, class_code, option_code, option_name,"
                             " multiplier, calibrated FROM coefficients ORDER BY id")
    class_factors = {}
    for c in coefficients:
        lst = class_factors.setdefault(c["class_code"], [])
        if c["factor_code"] not in lst:
            lst.append(c["factor_code"])

    data = {
        "classes": classes,
        "products": products,
        "class_factors": class_factors,
        "coefficients": coefficients,
        "base_rates": rows(con, "SELECT class_code, object_type, net_rate_pct, calibrated FROM base_rates"
                                " ORDER BY class_code, id"),
        "perils": rows(con, "SELECT code, name, class_code, is_catastrophic, base_share FROM perils"
                            " ORDER BY class_code, base_share DESC"),
        "load": rows(con, "SELECT code, name, share FROM load_components"),
        "checklists": rows(con, "SELECT scope_type, scope_code, doc_name, required, condition FROM checklists"
                                " ORDER BY scope_type, scope_code, id"),
        "rules": rows(con, "SELECT code, name, severity, legal_ref, description FROM rules"),
        "financials": (rows(con, "SELECT report_date, own_funds, reserves, source FROM company_financials"
                                 " ORDER BY report_date DESC LIMIT 1") or [None])[0],
        "tariff_versions": rows(con, "SELECT level, name, document_ref, effective_from, effective_to FROM tariff_versions"),
        # общие настройки: экрану ОСГОР нужен размер БРВ (минимальная премия 0,25 БРВ, п. 23 Правил).
        # Пока заказчик его не назвал, ключа в базе нет и экран спрашивает БРВ у агента.
        "settings": settings(con, ("BRV",)),
    }
    con.close()
    return data


# В страницу встраиваем только то, что читает шаблон (D.<ключ>). rules и tariff_versions экран
# не использует — они остаются в docs/agent_data.json для сверки, но в /ui не уходят (−15 КБ).
# Пробелы-разделители JSON тоже не нужны браузеру.
EMBED = ("classes", "products", "class_factors", "coefficients", "base_rates", "perils", "load",
         "checklists", "financials", "settings")


def build(data: dict):
    tpl = TEMPLATE.read_text(encoding="utf-8")
    embed = {k: data[k] for k in EMBED if k in data}
    OUT.write_text(tpl.replace("__DATA__", json.dumps(embed, ensure_ascii=False, separators=(",", ":"))),
                   encoding="utf-8")


if __name__ == "__main__":
    d = export()
    DATA.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"agent_data.json: продуктов {len(d['products'])}, классов {len(d['classes'])},"
          f" коэффициентов {len(d['coefficients'])}, базовых ставок {len(d['base_rates'])},"
          f" рисков {len(d['perils'])}")
    if "--data-only" not in sys.argv:
        build(d)
        print(f"agent_ui.html собран: {OUT.stat().st_size // 1024} КБ")
