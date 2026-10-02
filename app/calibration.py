"""
Калибровка тарифов по собственной статистике компании: самообучение ставок.

Что делает модуль:
  1. По накопленным договорам (requests + objects + calculations) и убыткам (claims) считает по каждой
     группе «класс × тип объекта» экспозицию, частоту, тяжесть и наблюдаемую нетто-ставку (burning cost).
  2. Смешивает наблюдаемое с действующим тарифом через теорию доверия (credibility) и формирует
     ПРЕДЛОЖЕНИЕ новых базовых ставок и множителей по факторам. Само ничего не меняет.
  3. Предложение утверждает человек (approve): создаётся новая версия тарифа, значения записываются
     в base_rates / coefficients с пометкой calibrated = 1 и источником «калибровка run N».

Источники формул:
  - burning cost — учебник CII M97 Reinsurance (убытки прошлых лет к экспозиции того же портфеля);
  - теория доверия (limited fluctuation credibility): Z = sqrt(n / n_full), Z ≤ 1;
  - рисковая надбавка — методика Росстрахнадзора (распоряжение № 02-03-36 от 08.07.1993), формула 1:
    Tр = 1,2 × Tо × α(γ) × sqrt((1 − q) / (n × q)).

Функции без побочных эффектов принимают соединение con и ничего не пишут, кроме propose (пишет run)
и approve/reject (меняют справочники и статус run). Все SQL — без диалектных особенностей.
"""
import json
import math
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import db, web
from .engine import FACTORS

ROOT = Path(__file__).resolve().parent.parent
router = APIRouter()

# Квантили нормального распределения α(γ) по методике Росстрахнадзора (таблица к формуле 1)
ALPHA = {0.90: 1.3, 0.95: 1.645, 0.98: 2.0}
N_FULL = 1000            # объём выборки, при котором своей статистике доверяем полностью (Z = 1)
MIN_CONTRACTS = 30       # меньше — группа в калибровку не идёт
MIN_CLAIMS = 5           # меньше — частота и тяжесть ненадёжны
# «урегулирован» и «в работе» — статусы загрузки из Excel (app/claims_import.py)
CLAIM_STATUSES = ("заявлен", "оплачен", "урегулирован", "в работе", "отказ")
PAID_STATUSES = ("оплачен", "урегулирован")       # размер убытка — выплата
NO_OBJECT_TYPE = "не указан"                       # группа для убытков без вида объекта
METHOD = "burning cost (CII M97) × теория доверия Z = sqrt(n/1000); рисковая надбавка по методике Росстрахнадзора"


# ---------- чистые формулы ----------

def credibility(n: int, n_full: int = N_FULL) -> float:
    """
    Доверие к собственной статистике (limited fluctuation credibility):
    Z = min(1, sqrt(n / n_full)). При n = n_full и больше — своей статистике верим полностью,
    при n = 0 — целиком остаёмся на действующем тарифе.
    """
    if n <= 0:
        return 0.0
    return min(1.0, math.sqrt(n / n_full))


def alpha_of(gamma: float) -> float:
    """α(γ) — коэффициент гарантии безопасности из таблицы методики Росстрахнадзора."""
    key = round(gamma, 2)
    if key not in ALPHA:
        raise ValueError(f"γ = {gamma}: допустимы {sorted(ALPHA)}")
    return ALPHA[key]


def risk_load(t0: float, q: float, n: int, gamma: float = 0.95) -> Optional[float]:
    """
    Рисковая надбавка (методика Росстрахнадзора, формула 1):
        Tр = 1,2 × Tо × α(γ) × sqrt((1 − q) / (n × q)),
    где Tо — основная (нетто) ставка, q — частота, n — число договоров, γ — гарантия безопасности.
    Множитель 1,2 методика вводит как запас на неизвестный разброс размера выплат.
    Возвращает None, когда формула не определена (нет убытков или договоров).
    """
    if t0 is None or q <= 0 or q >= 1 or n <= 0:
        return None
    return 1.2 * t0 * alpha_of(gamma) * math.sqrt((1 - q) / (n * q))


def _blend(observed: float, current: float, z: float) -> float:
    """Смешивание по теории доверия: новое = Z × наблюдаемое + (1 − Z) × действующее."""
    return z * observed + (1 - z) * current


# ---------- сбор данных ----------

def _term_days(attrs: dict, sum_insured, applied_rate_pct, premium) -> int:
    """
    Срок договора в днях. Сначала — по датам term_from/term_to из карточки объекта; если их нет —
    обратным счётом из премии (премия = ставка × сумма × срок / 365); иначе — год.
    """
    tf, tt = attrs.get("term_from"), attrs.get("term_to")
    if tf and tt:
        try:
            d = (date.fromisoformat(str(tt)[:10]) - date.fromisoformat(str(tf)[:10])).days
            if 0 < d <= 5 * 366:
                return d
        except ValueError:
            pass
    if premium and applied_rate_pct and sum_insured:
        d = round(premium / (applied_rate_pct / 100 * sum_insured) * 365)
        if 0 < d <= 5 * 366:
            return d
    return 365


def _contracts(con, period_from: str, period_to: str) -> list:
    """
    Договоры периода: запрос + объект + последний расчёт. Класс — первый класс продукта
    (или attributes.class_code, если карточка его несёт). Отклонённые запросы договорами не стали.
    """
    sql = """SELECT r.id request_id, r.product_code, r.created_at, r.status,
                    o.id object_id, o.object_type, o.sum_insured, o.attributes,
                    c.applied_rate_pct, c.premium,
                    (SELECT class_code FROM product_classes pc WHERE pc.product_code = r.product_code
                     ORDER BY part_no LIMIT 1) class_code
             FROM requests r
             JOIN objects o ON o.request_id = r.id
             LEFT JOIN calculations c ON c.id = (SELECT id FROM calculations WHERE object_id = o.id ORDER BY id DESC LIMIT 1)
             WHERE substr(r.created_at, 1, 10) BETWEEN ? AND ? AND r.status <> 'отклонено'
             ORDER BY r.id"""
    out = []
    for r in db.rows(con, sql, period_from, period_to):
        try:
            attrs = json.loads(r["attributes"] or "{}")
        except ValueError:
            attrs = {}
        factors = attrs.get("factors") or {}
        cls = attrs.get("class_code") or r["class_code"]
        if not cls or not r["sum_insured"]:
            continue
        out.append({"request_id": r["request_id"], "class_code": str(cls), "object_type": r["object_type"],
                    "sum_insured": float(r["sum_insured"]),
                    "term_days": _term_days(attrs, r["sum_insured"], r["applied_rate_pct"], r["premium"]),
                    "factors": {f: factors.get(f) for f in FACTORS if factors.get(f)}})
    return out


def _claims(con, period_from: str, period_to: str, request_ids: set) -> tuple:
    """
    Убытки к выборке: все, что привязаны к договорам выборки (по договорному базису), плюс убытки
    без привязки к договору, если дата события попала в период и указан класс (загрузка из Excel пишет
    класс всегда; без вида объекта убыток идёт в группу «не указан» — виден в экспозиции, но ставку
    по нему калибровка не предлагает: базовой ставки для такой группы нет).
    Отказы не считаются. Размер убытка — выплачено (оплачен, урегулирован), а для ещё не оплаченных —
    заявлено (incurred).
    Возвращает (список убытков, число убытков без привязки).
    """
    rows = db.rows(con, "SELECT * FROM claims WHERE status <> 'отказ'")
    out, unlinked = [], 0
    for c in rows:
        linked = c["request_id"] in request_ids
        if not linked:
            ev = c["event_date"] or c["reported_date"] or ""
            if not (c["class_code"] and period_from <= ev[:10] <= period_to):
                continue
            unlinked += 1
        amount = c["paid"] if c["status"] in PAID_STATUSES and c["paid"] is not None else (c["claimed"] or c["paid"] or 0)
        ot = c["object_type"] or (None if linked else NO_OBJECT_TYPE)
        out.append({"id": c["id"], "request_id": c["request_id"], "class_code": c["class_code"],
                    "object_type": ot, "amount": float(amount), "cause": c["cause"]})
    return out, unlinked


def _coefficients(con) -> dict:
    """(factor_code, option_code) -> {multiplier, class_code, name}. Как в движке — без учёта класса."""
    return {(r["factor_code"], r["option_code"]): {"multiplier": r["multiplier"], "class_code": r["class_code"],
                                                   "name": r["option_name"], "factor_name": r["factor_name"]}
            for r in db.rows(con, "SELECT * FROM coefficients")}


def exposure_and_losses(con, period_from: str, period_to: str) -> dict:
    """
    Экспозиция и убытки по группам (class_code, object_type) и по факторам (class_code, factor, option).

    По группе:
      n — договоров; exposure — Σ сумма × срок / 365 (страховая сумма-год);
      exposure_norm — та же экспозиция, взвешенная действующими множителями факторов: она показывает,
        сколько «базовых» единиц риска в группе, и позволяет сравнить наблюдаемое с базовой ставкой;
      m — убытков; losses — сумма выплат (incurred);
      q = m / n — частота; k = средняя выплата / средняя страховая сумма — тяжесть;
      burning_cost = losses / exposure × 100 — наблюдаемая нетто-ставка портфеля, % (CII M97);
      burning_cost_base = losses / exposure_norm × 100 — то же, приведённое к базовому варианту факторов.
    По фактору (внутри класса, все типы объектов):
      expected_excl — ожидаемые убытки по действующему тарифу БЕЗ множителя этого фактора
        (Σ сумма × срок/365 × база(тип) × Π множителей остальных факторов / 100);
      rel = losses / expected_excl — во сколько раз вариант убыточнее, чем ждал тариф без этого фактора.
      Отношение rel(вариант) / rel(базовый вариант) — наблюдаемая относительная убыточность,
      кандидат на новый множитель (однофакторный анализ: влияние других факторов снято только через тариф).
    """
    contracts = _contracts(con, period_from, period_to)
    coef = _coefficients(con)
    base = {(r["class_code"], r["object_type"]): r["net_rate_pct"] for r in db.rows(con, "SELECT * FROM base_rates")}
    claims, unlinked = _claims(con, period_from, period_to, {c["request_id"] for c in contracts})
    by_req = {c["request_id"]: c for c in contracts}

    groups, factors = {}, {}

    def g_of(key):
        return groups.setdefault(key, {"n": 0, "exposure": 0.0, "exposure_norm": 0.0, "sum_total": 0.0,
                                       "m": 0, "losses": 0.0})

    def f_of(key):
        return factors.setdefault(key, {"n": 0, "exposure": 0.0, "expected_excl": 0.0, "m": 0, "losses": 0.0})

    for c in contracts:
        key = (c["class_code"], c["object_type"])
        exp = c["sum_insured"] * c["term_days"] / 365
        mults = {f: coef[(f, o)]["multiplier"] for f, o in c["factors"].items() if (f, o) in coef}
        prod_all = math.prod(mults.values()) if mults else 1.0
        g = g_of(key)
        g["n"] += 1
        g["exposure"] += exp
        g["exposure_norm"] += exp * prod_all
        g["sum_total"] += c["sum_insured"]
        b = base.get(key)
        for f, o in c["factors"].items():
            if (f, o) not in coef:
                continue
            fk = (c["class_code"], f, o)
            fs = f_of(fk)
            fs["n"] += 1
            fs["exposure"] += exp
            if b is not None:
                fs["expected_excl"] += exp * b / 100 * prod_all / mults[f]

    for cl in claims:
        c = by_req.get(cl["request_id"])
        cls = cl["class_code"] or (c["class_code"] if c else None)
        ot = cl["object_type"] or (c["object_type"] if c else None)
        if not cls or not ot:
            continue
        g = g_of((cls, ot))
        g["m"] += 1
        g["losses"] += cl["amount"]
        if c:
            for f, o in c["factors"].items():
                if (f, o) in coef:
                    fs = f_of((cls, f, o))
                    fs["m"] += 1
                    fs["losses"] += cl["amount"]

    for g in groups.values():
        n, m = g["n"], g["m"]
        g["q"] = m / n if n else None
        avg_sum = g["sum_total"] / n if n else None
        g["k"] = (g["losses"] / m / avg_sum) if m and avg_sum else None
        g["burning_cost"] = g["losses"] / g["exposure"] * 100 if g["exposure"] else None
        g["burning_cost_base"] = g["losses"] / g["exposure_norm"] * 100 if g["exposure_norm"] else None
    for fs in factors.values():
        fs["rel"] = fs["losses"] / fs["expected_excl"] if fs["expected_excl"] else None

    return {"period": [period_from, period_to], "contracts": len(contracts), "claims": len(claims),
            "unlinked_claims": unlinked, "groups": groups, "factors": factors}


# ---------- предложение ----------

def _base_option(cls: str, factor: str, coef: dict, factors_stats: dict) -> Optional[str]:
    """
    Базовый вариант фактора: тот, у которого множитель ровно 1,0 (относительно него заданы остальные);
    если такого нет — вариант с наибольшей экспозицией в данных.
    """
    ones = [o for (f, o), v in coef.items() if f == factor and abs(v["multiplier"] - 1.0) < 1e-9
            and (v["class_code"] in (None, cls))]
    if ones:
        return ones[0]
    cand = [(fs["exposure"], o) for (c, f, o), fs in factors_stats.items() if c == cls and f == factor]
    return max(cand)[1] if cand else None


def propose(con, period_from: str, period_to: str, gamma: float = 0.95) -> dict:
    """
    Формирует предложение по калибровке и записывает прогон в calibration_runs со статусом 'предложено'.
    Справочники не меняет. Возвращает {"run_id", "summary", "items"}.

    Базовая ставка группы: новая = Z × burning_cost_base + (1 − Z) × действующая, Z = credibility(n).
    Множитель варианта: новый = Z_f × (rel_вариант / rel_база) + (1 − Z_f) × действующий,
      Z_f = credibility(min(n_вариант, n_база)) — доверяем не больше, чем меньшей из двух выборок.
    Порог: n ≥ 30 договоров и m ≥ 5 убытков; иначе группа только описывается, но не предлагается.
    """
    alpha_of(gamma)   # проверка допустимости γ до любой записи
    data = exposure_and_losses(con, period_from, period_to)
    coef = _coefficients(con)
    base_rows = {(r["class_code"], r["object_type"]): r for r in db.rows(con, "SELECT * FROM base_rates")}
    class_names = {r["code"]: r["name"] for r in db.rows(con, "SELECT code, name FROM classes")}
    items, skipped = [], []

    for (cls, ot), g in sorted(data["groups"].items()):
        cur = base_rows.get((cls, ot))
        label = f"{cls} {class_names.get(cls, '')} · {ot}".strip()
        if cur is None:
            skipped.append({"scope": label, "why": "нет действующей базовой ставки для этого типа объекта", "n": g["n"], "m": g["m"]})
            continue
        if g["n"] < MIN_CONTRACTS or g["m"] < MIN_CLAIMS:
            skipped.append({"scope": label, "why": f"мало данных: договоров {g['n']} (нужно ≥ {MIN_CONTRACTS}), убытков {g['m']} (нужно ≥ {MIN_CLAIMS})",
                            "n": g["n"], "m": g["m"], "observed": g["burning_cost"]})
            continue
        z = credibility(g["n"])
        observed = g["burning_cost_base"]
        proposed = _blend(observed, cur["net_rate_pct"], z)
        rl = risk_load(proposed, g["q"], g["n"], gamma)
        items.append({
            "kind": "base_rate", "scope": label, "class_code": cls, "object_type": ot,
            "current": round(cur["net_rate_pct"], 4), "observed": round(observed, 4), "proposed": round(proposed, 4),
            "n": g["n"], "m": g["m"], "Z": round(z, 3), "apply": abs(proposed - cur["net_rate_pct"]) > 1e-9,
            "q": round(g["q"], 4), "k": round(g["k"], 4) if g["k"] is not None else None,
            "burning_cost_raw": round(g["burning_cost"], 4),
            "risk_load_pct": round(rl, 4) if rl is not None else None,
            "risk_load_share": round(rl / proposed, 3) if rl and proposed else None,
            "comment": (f"burning cost портфеля {g['burning_cost']:.4f}%, приведённый к базовому варианту {observed:.4f}%; "
                        f"частота q = {g['q']:.4f}, тяжесть k = {g['k']:.4f}; доверие Z = {z:.3f} при n = {g['n']} из {N_FULL}; "
                        + (f"рисковая надбавка по Росстрахнадзору при γ = {gamma}: {rl:.4f} п.п. ({rl / proposed * 100:.0f}% нетто)"
                           if rl is not None else "рисковая надбавка не определена")),
        })

    # факторы: внутри класса, относительно базового варианта
    classes_ok = {it["class_code"] for it in items}
    for cls in sorted({c for (c, _, _) in data["factors"]}):
        if cls not in classes_ok:
            continue   # если базовая ставка класса не калибруется — множители тоже не трогаем
        for factor in FACTORS:
            opts = {o: fs for (c, f, o), fs in data["factors"].items() if c == cls and f == factor}
            if not opts:
                continue
            base_opt = _base_option(cls, factor, coef, data["factors"])
            b = opts.get(base_opt)
            fname = next((v["factor_name"] for (f, _), v in coef.items() if f == factor), factor)
            if not b or b["n"] < MIN_CONTRACTS or b["m"] < MIN_CLAIMS or not b["rel"]:
                skipped.append({"scope": f"{cls} · {fname}", "why": f"базовый вариант «{base_opt}» не набрал данных: договоров {b['n'] if b else 0}, убытков {b['m'] if b else 0}"})
                continue
            for o, fs in sorted(opts.items()):
                cur = coef.get((factor, o))
                if cur is None:
                    continue
                scope = f"{cls} · {fname} · {cur['name']} ({o})"
                if o == base_opt:
                    items.append({"kind": "coefficient", "scope": scope, "class_code": cls, "factor_code": factor, "option_code": o,
                                  "current": cur["multiplier"], "observed": 1.0, "proposed": cur["multiplier"],
                                  "n": fs["n"], "m": fs["m"], "Z": None, "apply": False,
                                  "comment": "базовый вариант: остальные считаются относительно него"})
                    continue
                if fs["n"] < MIN_CONTRACTS or fs["m"] < MIN_CLAIMS or fs["rel"] is None:
                    skipped.append({"scope": scope, "why": f"мало данных: договоров {fs['n']}, убытков {fs['m']}", "n": fs["n"], "m": fs["m"]})
                    continue
                observed = fs["rel"] / b["rel"]
                z = credibility(min(fs["n"], b["n"]))
                proposed = _blend(observed, cur["multiplier"], z)
                items.append({"kind": "coefficient", "scope": scope, "class_code": cls, "factor_code": factor, "option_code": o,
                              "current": round(cur["multiplier"], 4), "observed": round(observed, 4), "proposed": round(proposed, 4),
                              "n": fs["n"], "m": fs["m"], "Z": round(z, 3), "apply": abs(proposed - cur["multiplier"]) > 1e-9,
                              "comment": (f"убыточность варианта к ожидаемой без этого фактора {fs['rel']:.3f}, у базового «{base_opt}» {b['rel']:.3f}; "
                                         f"отношение {observed:.3f}; Z = {z:.3f} по меньшей выборке ({min(fs['n'], b['n'])} договоров)")})

    n_total, m_total = data["contracts"], data["claims"]
    enough = any(it["kind"] == "base_rate" for it in items)
    summary = {
        "period_from": period_from, "period_to": period_to, "gamma": gamma,
        "contracts": n_total, "claims": m_total, "unlinked_claims": data["unlinked_claims"],
        "groups": len(data["groups"]), "enough": enough,
        "message": (None if enough else
                    f"Данных для калибровки пока недостаточно: договоров {n_total}, убытков {m_total}. "
                    f"Нужно не меньше {MIN_CONTRACTS} договоров и {MIN_CLAIMS} убытков в группе."),
        "proposals": sum(1 for it in items if it["apply"]),
        "credibility": round(credibility(n_total), 3),
        "method": METHOD, "skipped": skipped,
        "note": "Все значения — предложение. Тариф меняется только после утверждения (approve).",
    }
    cur = con.execute(
        "INSERT INTO calibration_runs (run_at, period_from, period_to, contracts, claims, method, credibility, proposals, status)"
        " VALUES (?,?,?,?,?,?,?,?,'предложено')",
        (db.now(), period_from, period_to, n_total, m_total, METHOD, summary["credibility"],
         json.dumps({"summary": summary, "items": items}, ensure_ascii=False)))
    run_id = cur.lastrowid
    db.audit(con, "агент-статистик", "калибровка: предложение", f"calibration_run:{run_id}",
             {"contracts": n_total, "claims": m_total, "proposals": summary["proposals"], "enough": enough})
    return {"run_id": run_id, "summary": summary, "items": items}


# ---------- утверждение / отклонение ----------

def _run(con, run_id: int) -> dict:
    r = db.rows(con, "SELECT * FROM calibration_runs WHERE id=?", run_id)
    if not r:
        raise HTTPException(404, "Прогон калибровки не найден")
    run = r[0]
    run["proposals"] = json.loads(run["proposals"]) if run["proposals"] else {"summary": {}, "items": []}
    return run


def approve(con, run_id: int, who: str) -> dict:
    """
    Применяет предложение: новая версия тарифа компании «Калибровка от <дата>» с сегодняшней даты,
    base_rates.net_rate_pct / frequency / severity и coefficients.multiplier обновляются по пунктам
    с apply = True, calibrated = 1, source = 'калибровка run <id>'. Прогон получает статус 'утверждено'.
    """
    run = _run(con, run_id)
    if run["status"] != "предложено":
        raise HTTPException(409, f"Прогон уже в статусе «{run['status']}»")
    items = [it for it in run["proposals"]["items"] if it.get("apply")]
    if not items:
        raise HTTPException(409, "В прогоне нет предложений к применению")
    today = date.today().isoformat()
    src = f"калибровка run {run_id}"
    cur = con.execute("INSERT INTO tariff_versions (level, name, document_ref, effective_from) VALUES (?,?,?,?)",
                      ("компания", f"Калибровка от {today}", f"calibration_run:{run_id}", today))
    version_id = cur.lastrowid
    changed = {"base_rates": 0, "coefficients": 0}
    for it in items:
        if it["kind"] == "base_rate":
            con.execute("UPDATE base_rates SET net_rate_pct=?, frequency=?, severity=?, calibrated=1, source=?"
                        " WHERE class_code=? AND object_type=?",
                        (it["proposed"], it.get("q"), it.get("k"), src, it["class_code"], it["object_type"]))
            changed["base_rates"] += con.execute("SELECT changes()").fetchone()[0]
        elif it["kind"] == "coefficient":
            con.execute("UPDATE coefficients SET multiplier=?, calibrated=1, source=?"
                        " WHERE factor_code=? AND option_code=? AND (class_code=? OR class_code IS NULL)",
                        (it["proposed"], src, it["factor_code"], it["option_code"], it["class_code"]))
            changed["coefficients"] += con.execute("SELECT changes()").fetchone()[0]
    con.execute("UPDATE calibration_runs SET status='утверждено', approved_by=?, approved_at=? WHERE id=?",
                (who, db.now(), run_id))
    db.audit(con, who, "калибровка утверждена", f"calibration_run:{run_id}",
             {"tariff_version_id": version_id, **changed, "items": len(items)})
    db.reference_changed(con)          # новые ставки и коэффициенты — в расчёт сразу, без ожидания кэша
    return {"ok": True, "run_id": run_id, "tariff_version_id": version_id, **changed}


def reject(con, run_id: int, who: str, note: str = "") -> dict:
    run = _run(con, run_id)
    if run["status"] != "предложено":
        raise HTTPException(409, f"Прогон уже в статусе «{run['status']}»")
    con.execute("UPDATE calibration_runs SET status='отклонено', approved_by=?, approved_at=?, note=? WHERE id=?",
                (who, db.now(), note, run_id))
    db.audit(con, who, "калибровка отклонена", f"calibration_run:{run_id}", {"note": note})
    return {"ok": True, "run_id": run_id, "status": "отклонено"}


# ---------- API ----------

class RunIn(BaseModel):
    period_from: str                 # ГГГГ-ММ-ДД
    period_to: str
    gamma: float = 0.95


class Who(BaseModel):
    who: str
    note: str = ""


class ClaimIn(BaseModel):
    request_id: Optional[int] = None
    external_no: Optional[str] = None
    class_code: Optional[str] = None
    object_type: Optional[str] = None
    event_date: Optional[str] = None
    reported_date: Optional[str] = None
    paid_date: Optional[str] = None
    cause: Optional[str] = None
    claimed: Optional[float] = None
    paid: Optional[float] = None
    status: str = "заявлен"
    source: Optional[str] = "ручной ввод"


@router.get("/calibration/exposure")
def calibration_exposure(period_from: str, period_to: str):
    """Экспозиция и убытки по группам без записи прогона — чтобы посмотреть, хватает ли данных."""
    with db.tx() as con:
        d = exposure_and_losses(con, period_from, period_to)
    return {"period": d["period"], "contracts": d["contracts"], "claims": d["claims"], "unlinked_claims": d["unlinked_claims"],
            "groups": [{"class_code": k[0], "object_type": k[1], **v} for k, v in sorted(d["groups"].items())],
            "enough": any(v["n"] >= MIN_CONTRACTS and v["m"] >= MIN_CLAIMS for v in d["groups"].values()),
            "min_contracts": MIN_CONTRACTS, "min_claims": MIN_CLAIMS}


@router.post("/calibration/run")
def calibration_run(body: RunIn):
    try:
        with db.tx() as con:
            return propose(con, body.period_from, body.period_to, body.gamma)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/calibration/runs")
def calibration_runs(limit: int = 50):
    with db.tx() as con:
        out = db.rows(con, "SELECT id, run_at, period_from, period_to, contracts, claims, credibility, status, approved_by, approved_at, note"
                           " FROM calibration_runs ORDER BY id DESC LIMIT ?", limit)
    return out


@router.get("/calibration/runs/{run_id}")
def calibration_run_card(run_id: int):
    with db.tx() as con:
        return _run(con, run_id)


@router.post("/calibration/runs/{run_id}/approve")
def calibration_approve(run_id: int, body: Who):
    if not body.who.strip():
        raise HTTPException(422, "Укажите, кто утверждает")
    with db.tx() as con:
        return approve(con, run_id, body.who.strip())


@router.post("/calibration/runs/{run_id}/reject")
def calibration_reject(run_id: int, body: Who):
    if not body.who.strip():
        raise HTTPException(422, "Укажите, кто отклоняет")
    with db.tx() as con:
        return reject(con, run_id, body.who.strip(), body.note)


@router.post("/claims")
def add_claim(c: ClaimIn):
    if c.status not in CLAIM_STATUSES:
        raise HTTPException(422, f"Статус: {' | '.join(CLAIM_STATUSES)}")
    with db.tx() as con:
        if c.request_id is not None and not db.rows(con, "SELECT 1 FROM requests WHERE id=?", c.request_id):
            raise HTTPException(404, f"Запрос {c.request_id} не найден")
        cur = con.execute("INSERT INTO claims (request_id, external_no, class_code, object_type, event_date, reported_date,"
                          " paid_date, cause, claimed, paid, status, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (c.request_id, c.external_no, c.class_code, c.object_type, c.event_date, c.reported_date,
                           c.paid_date, c.cause, c.claimed, c.paid, c.status, c.source))
        db.audit(con, "api", "зарегистрирован убыток", f"claim:{cur.lastrowid}", c.model_dump())
        return {"id": cur.lastrowid}


@router.get("/claims")
def list_claims(status: Optional[str] = None, limit: int = 200):
    # колонки перечислены явно: у claims теперь свои product_code и contract_no (загрузка из Excel),
    # а при совпадении имён строка базы отдаёт первое из них
    sql, args = ("SELECT c.id, c.request_id, c.external_no, c.class_code, c.object_type, c.event_date,"
                 " c.reported_date, c.paid_date, c.cause, c.claimed, c.paid, c.status, c.source,"
                 " c.branch, c.region, c.sum_insured, c.premium, c.batch_id, c.updated_at,"
                 " COALESCE(c.contract_no, r.external_no) AS contract_no,"
                 " COALESCE(c.product_code, r.product_code) AS product_code"
                 " FROM claims c LEFT JOIN requests r ON r.id = c.request_id WHERE 1=1"), []
    if status:
        sql += " AND c.status=?"; args.append(status)
    sql += " ORDER BY c.id DESC LIMIT ?"; args.append(limit)
    with db.tx() as con:
        return db.rows(con, sql, *args)


@router.get("/calibration", response_class=HTMLResponse)
def calibration_page(embed: int = 0):
    return _page(web.read_text(ROOT / "app" / "calibration.html"), "/calibration", embed)


def _page(html: str, active: str, embed: int) -> str:
    """Общая раскладка из app/main.py; импорт отложенный — main.py сам подключает этот модуль."""
    from .main import page
    return page(html, active, bool(embed))
