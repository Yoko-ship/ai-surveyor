"""
Ёмкость компании и собственное удержание по классам.
Считает по данным отчётности (таблицы reserve_reports, allocated_assets, solvency_reports, company_financials);
чего нет — честно помечает и включает в список запроса данных.
Нормы: Положение 1806 (пп. 6–16), Положение 1882 (пп. 23, 33, 38, 40, 41).
"""
from . import db

LEGAL_SHARE_PER_RISK = 0.20        # 1806 п. 15
LEGAL_TOP5_SHARE = 2.00            # 1806 п. 16
ASSETS_MIN, ASSETS_MAX = 1.00, 1.05  # 1882 пп. 40–41
LIQUID_MIN = 0.70                  # 1882 п. 38
RPNU_MIN_SHARE = 0.10              # 1882 п. 23

DATA_REQUEST = [
    ("solvency", "Расчёт маржи платёжеспособности (приложение № 1 к Положению 1806) на последнюю дату",
     "собственные средства, вычеты, премии за 12 мес., выплаты за 36 мес., минимальный капитал"),
    ("reserves", "Отчёт о страховых резервах по учётным группам (приложения к Положению 1882)",
     "РНП, РЗУ, РПНУ, стабилизационные — по группам и видам"),
    ("assets", "Отчёт о размещении выделенных активов по категориям",
     "госбумаги, депозиты, деньги, ценные бумаги, недвижимость"),
    ("top5", "Сведения о пяти крупнейших рисках (приложение № 3 к Положению 1806)",
     "обязательства по пяти крупнейшим договорам"),
]


def _latest(con, table, order="report_date"):
    r = db.rows(con, f"SELECT * FROM {table} ORDER BY {order} DESC LIMIT 1")
    return r[0] if r else None


def capacity(con) -> dict:
    out = {"warnings": [], "missing": [], "temporary": []}

    # --- собственные средства и резервы ---
    solv = _latest(con, "solvency_reports")
    fin = _latest(con, "company_financials")
    own = solv["own_funds"] if solv else (fin["own_funds"] if fin else None)
    res_rows = db.rows(con, "SELECT * FROM reserve_reports WHERE report_date=(SELECT MAX(report_date) FROM reserve_reports)")
    total_row = next((r for r in res_rows if r["scope_type"] == "итого"), None)
    reserves = None
    if total_row:
        reserves = sum(total_row[k] or 0 for k in ("rnp", "rzu", "rpnu", "stab", "cat_reserve", "other"))
    elif res_rows:
        groups = [r for r in res_rows if r["scope_type"] == "группа"]
        reserves = sum(sum(r[k] or 0 for k in ("rnp", "rzu", "rpnu", "stab", "cat_reserve", "other")) for r in groups) or None
    if reserves is None and fin:
        reserves = fin["reserves"]
    if not solv:
        out["missing"].append("solvency")
        if fin:
            out["temporary"].append(f"собственные средства и резервы — временные цифры ({fin['source'] or 'без источника'})")
    if not res_rows:
        out["missing"].append("reserves")

    out["own_funds"] = own
    out["reserves"] = reserves
    out["limit_per_risk"] = LEGAL_SHARE_PER_RISK * (own + reserves) if own is not None and reserves is not None else None
    out["limit_top5"] = LEGAL_TOP5_SHARE * own if own is not None else None

    # --- маржа платёжеспособности (1806, пп. 7–14) ---
    if solv:
        k = 1.0
        if solv["claims_36m"] and solv["claims_36m_net"] is not None:
            k = max(0.5, solv["claims_36m_net"] / solv["claims_36m"])
        by_prem = 0.20 * (solv["premiums_12m"] or 0) * k
        rzu = (total_row["rzu"] if total_row else 0) or 0
        by_claims = 0.10 * ((solv["claims_36m"] or 0) + rzu) * k
        norm = max(solv["min_capital"] or 0, by_prem, by_claims)
        actual = solv["own_funds"] - (solv["deductions"] or 0)
        out["margin"] = {"actual": actual, "normative": norm, "ratio": (actual / norm) if norm else None,
                         "by_premiums": by_prem, "by_claims": by_claims, "coefficient": k,
                         "headroom": actual - norm}
        if norm and actual / norm < 1:
            out["warnings"].append("Маржа платёжеспособности ниже норматива (1806, п. 6)")
        if solv["top5_liabilities"] and out["limit_top5"] and solv["top5_liabilities"] > out["limit_top5"]:
            out["warnings"].append("Пять крупнейших рисков превышают 200% собственных средств (1806, п. 16)")
        if solv["top5_liabilities"] is None:
            out["missing"].append("top5")
    else:
        out["margin"] = None

    # --- резервы по группам и нагрузка на резерв ---
    out["reserves_by_scope"] = [{k: r[k] for k in ("scope_type", "scope_code", "rnp", "rzu", "rpnu", "stab", "cat_reserve", "other")}
                                for r in res_rows]
    if total_row and total_row["base_premium_12m"]:
        need = RPNU_MIN_SHARE * total_row["base_premium_12m"]
        if (total_row["rpnu"] or 0) < need:
            out["warnings"].append(f"РПНУ ниже нормы 10% базовой премии: нужно не меньше {need:,.0f} (1882, п. 23)")

    assets = db.rows(con, "SELECT * FROM allocated_assets WHERE report_date=(SELECT MAX(report_date) FROM allocated_assets)")
    if assets:
        total_assets = sum(a["amount"] for a in assets)
        liquid = sum(a["amount"] for a in assets if a["is_liquid"])
        cover = total_assets / reserves if reserves else None
        out["assets"] = {"total": total_assets, "liquid": liquid, "liquid_share": liquid / total_assets if total_assets else None,
                         "coverage": cover, "by_category": assets,
                         "claims_load": ((total_row["rzu"] or 0) + (total_row["rpnu"] or 0)) / total_assets if total_row and total_assets else None}
        if cover is not None and cover < ASSETS_MIN:
            out["warnings"].append("Выделенных активов меньше суммы резервов (1882, п. 40)")
        if cover is not None and cover > ASSETS_MAX:
            out["warnings"].append("Выделенных активов больше 105% резервов — излишек надо перевести в невыделенные (1882, п. 41)")
        if total_assets and liquid / total_assets < LIQUID_MIN:
            out["warnings"].append("Ликвидных активов меньше 70% выделенных (1882, п. 38)")
    else:
        out["assets"] = None
        out["missing"].append("assets")

    out["data_request"] = [{"code": c, "what": w, "fields": f} for c, w, f in DATA_REQUEST if c in out["missing"]]
    return out


def retention_table(con, limit_per_risk: float) -> list:
    """
    Таблица линий: удержание обратно пропорционально нетто-ставке класса,
    чистая премия с удержания одинакова; потолок — лимит 20% на один риск.
    Для классов 8 (огонь) удержание применяется к PML, для землетрясения PML = вся площадка.
    """
    rates = db.rows(con, "SELECT class_code, AVG(net_rate_pct) rate FROM base_rates GROUP BY class_code")
    if not rates or not limit_per_risk:
        return []
    safest = min(r["rate"] for r in rates)
    K = limit_per_risk * safest                      # премия с удержания в самом безопасном классе
    names = {r["code"]: r["name"] for r in db.rows(con, "SELECT code, name FROM classes")}
    out = []
    for r in sorted(rates, key=lambda x: x["rate"]):
        ret = min(limit_per_risk, K / r["rate"])
        out.append({"class_code": r["class_code"], "class_name": names.get(r["class_code"], ""),
                    "net_rate_pct": round(r["rate"], 3), "retention": round(ret),
                    "share_of_limit": round(ret / limit_per_risk, 3),
                    "basis": "PML по отсекам; землетрясение — вся площадка" if r["class_code"] == "8" else "страховая сумма",
                    "net_premium_on_retention": round(ret * r["rate"] / 100)})  # одинакова для всех классов
    return out
