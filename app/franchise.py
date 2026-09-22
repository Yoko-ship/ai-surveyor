"""
Франшиза по ситуации и совет по тарифу (задача заказчика 22.09.2026).

Что делает: по готовой аналитике риска (app/risk_analytics.analyze) выдаёт андеррайтеру и агенту
рекомендацию — нужна ли франшиза, какая (тип, процент, сумма в сумах), почему (со ссылкой на
конкретные цифры анализа), как это меняет премию, и три варианта договора: базовый, с франшизой,
с мероприятиями по снижению риска.

Правило (экспертное, calibrated = 0; пороги в таблице risk_thresholds, правятся администратором):
    Низкий       — без франшизы либо 0,5–1%;
    Умеренный    — 1–2%;
    Повышенный   — 2–5% безусловная;
    Высокий      — 5–10% безусловная + мероприятия либо отказ по части рисков;
    Критический  — только с безусловной франшизой вплоть до 90% по конкретному риску
                   (пример: землетрясение в 9-балльной зоне без сейсмостойкости) либо отказ.
Внутри вилки: убытки за 3 года и доля мелких убытков двигают к верхней границе, чистая история — к
нижней. Класс страхования ограничивает потолок (franchise_class_caps); по обязательным видам
(режим тарифа «нормативный акт») франшиза не предлагается — тариф и условия заданы актом.

Эффект на премию считается тем же движком, что и весь расчёт: варианты пересчитываются через
risk_analytics.analyze (то есть через engine.calculate), а мероприятия берутся из блока reducers
(готовые premium_delta). Второго калькулятора здесь нет.

Контракт:
    recommend_franchise(con, must, optional=None, *, analysis=None, thresholds=None,
                        market=None, as_of=None) -> dict
"""
from datetime import date
from typing import Optional

from . import risk_analytics as ra
from .engine import STATUTORY_MODE
from .risk_analytics import money, num

CALIBRATED = 0
EXPERT = ra.EXPERT

UNCONDITIONAL = "безусловная"        # вычитается из каждой выплаты
NO_FRANCHISE = "нет"

DECISION = {
    "accept": "принять",
    "accept_franchise": "принять с безусловной франшизой",
    "accept_conditions": "принять с франшизой и условиями (мероприятия по снижению риска)",
    "accept_or_decline": "принять только с франшизой либо отказать",
    "statutory": "франшиза не применяется (обязательный вид, тариф и условия по нормативному акту)",
}


# ================================================================================================
# Публичная функция
# ================================================================================================
def recommend_franchise(con, must: dict, optional: Optional[dict] = None, *,
                        analysis: Optional[dict] = None, thresholds: Optional[dict] = None,
                        market: Optional[dict] = None, as_of: Optional[date] = None) -> dict:
    """Рекомендация по франшизе и совет по тарифу.

    con        — соединение с базой (db.tx());
    must/optional — те же поля, что у risk_analytics.analyze (FIELDS);
    analysis   — готовый результат analyze (чтобы не считать дважды); нет — посчитается здесь;
    thresholds — пороги (нет — берутся из risk_thresholds поверх DEFAULT_THRESHOLDS);
    market     — картина рынка (как её отдаёт app/market_picture.py);
    as_of      — дата расчёта.
    """
    optional = dict(optional or {})
    as_of = as_of or date.today()
    th = ra._merge(ra.DEFAULT_THRESHOLDS, thresholds) if thresholds is not None else ra.load_thresholds(con)
    if analysis is None:
        analysis = ra.analyze(con, must, optional, thresholds=thresholds, market=market, as_of=as_of)
    if not analysis.get("ok"):
        return {"ok": False, "validation": analysis.get("validation"),
                "message": "Франшиза не подбиралась: анализ риска не выполнен."}

    # быстрый режим: анализ шёл на полях, достроенных apply_defaults. Варианты премии считаем
    # на них же — иначе повторный analyze упадёт на незаполненных полях (дефект 22.09.2026)
    inputs = analysis.get("inputs") or {}
    if isinstance(inputs.get("must"), dict) and inputs["must"]:
        must = dict(inputs["must"])
        optional = dict(inputs.get("optional") or optional)

    s = analysis["summary"]
    S = float(s["sum_insured"])
    level = analysis["level"]["level"]
    score = analysis["level"]["score"]
    classes = ra.parse_classes(must.get("class_code"))
    statutory = _statutory(con, analysis)

    # ---------- история убытков ----------
    losses = _losses(optional, S, th)

    # ---------- вилка по уровню и выбор внутри вилки ----------
    band = [float(x) for x in (th["franchise_by_level"].get(level) or [0, 0])]
    pct, why_pick = _pick(band, level, losses, th)
    cap, cap_why = _cap(classes, th)
    if cap is not None and pct > cap:
        pct, why_pick = cap, why_pick + [cap_why]

    # ---------- критический риск: франшиза по конкретному риску ----------
    peril = _critical_peril(analysis, must, optional, th, level)

    if statutory:
        pct, peril = 0.0, None
        why_pick = [statutory["why"]]

    # ---------- варианты премии (пересчёт движком) ----------
    variants = _variants(con, must, optional, th, market, as_of, analysis, pct, peril)

    # ---------- решение ----------
    decision = _decision(level, statutory, peril)

    ftype = NO_FRANCHISE if pct <= 0 else UNCONDITIONAL
    amount = round(S * pct / 100)
    fr = {
        "type": ftype,
        "pct": round(pct, 2),
        "amount": amount,
        "band_pct": band,
        "text": ("Франшиза не требуется" if pct <= 0 else
                 f"Безусловная франшиза {num(pct, 2)}% страховой суммы — {money(amount)} сум "
                 f"с каждого убытка"),
        "peril": peril,
        "calibrated": CALIBRATED,
    }

    basis = _basis(analysis, level, score, band, pct, losses, why_pick, peril, statutory, S)
    advice = _tariff_advice(analysis, th)
    out = {
        "ok": True,
        "calibrated": CALIBRATED,
        "level": level,
        "level_score": score,
        "decision": decision,
        "decision_code": [k for k, v in DECISION.items() if v == decision][0],
        "franchise": fr,
        "losses": losses,
        "basis": basis,
        "premium_effect": variants["effect"],
        "options": variants["options"],
        "tariff_advice": advice,
        "thresholds_source": th.get("_source"),
        "thresholds_used": {k: th[k] for k in ra.DEFAULT_THRESHOLDS if k.startswith("franchise_")},
        "legal_refs": [
            {"ref": "ГК РУз, ст. 936", "what": "неполное страхование: выплата в доле суммы к стоимости"},
            {"ref": "ГК РУз, ст. 938", "what": "страховая сумма выше стоимости: договор ничтожен в части превышения"},
            {"ref": "Положение № 1806, п. 15", "what": "обязательства по одному риску ≤ 20% (средства + резервы)"},
            {"ref": "Положение № 1882, п. 11", "what": "каждый класс договора проверяется отдельно"},
        ],
        "note": ("Франшиза — часть убытка, которую клиент оставляет себе. Безусловная вычитается из каждой "
                 "выплаты. Она убирает поток мелких убытков и снижает премию; крупный убыток она не "
                 "закрывает. Все пороги экспертные (calibrated = 0), правятся администратором."),
    }
    out["explanation"] = _explain(out)
    out["llm_text"] = "\n".join(out["explanation"])
    return out


# ================================================================================================
# История убытков
# ================================================================================================
def _losses(optional: dict, S: float, th: dict) -> dict:
    """Убытки за 3 года: количество, сумма, доля мелких (меньше franchise_small_loss_pct% суммы)."""
    src = optional.get("losses_3y") if isinstance(optional.get("losses_3y"), dict) else {}
    cnt = ra._to_float(src.get("count"))
    amt = ra._to_float(src.get("amount"))
    small_pct = float(th["franchise_small_loss_pct"])
    threshold = S * small_pct / 100
    out = {"known": cnt is not None, "count": int(cnt) if cnt is not None else None,
           "amount": amt, "small_loss_threshold": round(threshold),
           "small_loss_pct": small_pct, "small_count": None, "small_share": None,
           "calibrated": CALIBRATED}
    if cnt is None:
        out["why"] = f"убытки за 3 года не указаны — франшиза подобрана по уровню риска без поправки"
        return out
    n = int(cnt)
    sc = ra._to_float(src.get("small_count"))
    if sc is not None and n > 0:
        out["small_count"] = int(sc)
        out["small_share"] = round(min(1.0, sc / n), 2)
        out["why"] = (f"убытков {n}, из них мелких (меньше {num(small_pct, 1)}% суммы = "
                      f"{money(threshold)} сум) — {int(sc)}, доля {num(out['small_share'])}")
    elif amt is not None and n > 0:
        avg = amt / n
        out["avg_loss"] = round(avg)
        out["small_share"] = 1.0 if avg < threshold else 0.0
        out["assumed"] = True
        out["why"] = (f"убытков {n} на {money(amt)} сум, средний {money(avg)} сум — "
                      + ("меньше" if avg < threshold else "больше")
                      + f" порога мелкого убытка {money(threshold)} сум; разбивки по убыткам нет, "
                        f"доля мелких принята {num(out['small_share'])} по среднему — {EXPERT}")
    else:
        out["why"] = f"убытков за 3 года: {n}; сумм нет — доля мелких убытков не считалась"
    return out


# ================================================================================================
# Выбор франшизы внутри вилки
# ================================================================================================
def _pick(band: list, level: str, losses: dict, th: dict) -> tuple:
    lo, hi = band
    why = [f"уровень риска «{level}» → вилка {num(lo, 1)}–{num(hi, 1)}% страховой суммы (экспертная "
           f"таблица franchise_by_level, calibrated = 0)"]
    pct = lo
    n = losses.get("count")
    share = losses.get("small_share")
    high = int(th["franchise_loss_count_high"])
    if n is None:
        pct = (lo + hi) / 2
        why.append(f"истории убытков нет — взята середина вилки {num(pct, 2)}%")
    elif n >= high:
        pct = hi
        why.append(f"{n} убытка(ов) за 3 года (порог {high}) — верх вилки {num(hi, 2)}%")
    elif share is not None and share > float(th["franchise_small_share"]):
        pct = hi
        why.append(f"мелких убытков {num(share)} — больше порога {num(float(th['franchise_small_share']))}; "
                   f"франшиза как раз отсекает такие убытки — верх вилки {num(hi, 2)}%")
    elif n == 0:
        pct = lo
        why.append(f"убытков за 3 года не было — низ вилки {num(lo, 2)}%")
    else:
        pct = (lo + hi) / 2
        why.append(f"{n} убыток(ов) за 3 года, мелких немного — середина вилки {num(pct, 2)}%")
    return round(pct, 2), why


def _cap(classes: list, th: dict) -> tuple:
    caps = [float(v) for k, v in (th.get("franchise_class_caps") or {}).items() if k in classes]
    if not caps:
        return None, ""
    cap = min(caps)
    return cap, (f"по классу {'/'.join(classes)} франшиза ограничена {num(cap, 1)}% "
                 f"(franchise_class_caps, экспертно)")


def _statutory(con, analysis: dict) -> Optional[dict]:
    """Обязательный вид: тариф установлен нормативным актом — франшизу не предлагаем."""
    codes = [p.get("product_code") for p in analysis["summary"]["parts"] if p.get("product_code")]
    if not codes:
        return None
    q = ",".join("?" * len(codes))
    rows = con.execute(f"SELECT code, name, pricing_mode FROM products WHERE code IN ({q})", codes).fetchall()
    for code, name, mode in rows:
        if mode == STATUTORY_MODE:
            return {"product_code": code, "product_name": name,
                    "why": f"продукт {code} «{name}» — обязательный вид: тариф и условия установлены "
                           f"нормативным актом, франшиза договором не вводится"}
    return None


def _decision(level: str, statutory, peril) -> str:
    if statutory:
        return DECISION["statutory"]
    if level in ("Низкий", "Умеренный"):
        return DECISION["accept"]
    if level == "Повышенный":
        return DECISION["accept_franchise"]
    if level == "Высокий":
        return DECISION["accept_conditions"]
    return DECISION["accept_or_decline"]


# ================================================================================================
# Критический риск: большая франшиза по одному риску
# ================================================================================================
def _critical_peril(analysis: dict, must: dict, optional: dict, th: dict, level: str) -> Optional[dict]:
    """Риск, который компания принимать не готова: по нему — франшиза до 90% либо исключение.
    Пример заказчика: землетрясение в 9-балльной зоне у здания без сейсмостойкости."""
    if level != "Критический":
        return None
    zone = ra._seismic_zone(optional.get("seismic_zone"))
    need = float(th["franchise_critical_seismic_zone"])
    weak = must.get("construction") in ("wood", "mixed") or (ra._to_float(optional.get("wear_pct")) or 0) > 45
    eq = next((r for r in analysis["risks"] if r["code"] == "earthquake"), None)
    if zone is not None and zone >= need and weak and eq:
        pct = float(th["franchise_critical_peril_pct"])
        return {"code": "earthquake", "name": eq["name"], "pct": pct, "type": UNCONDITIONAL,
                "share_of_net_pct": eq["share_of_net_pct"],
                "why": (f"сейсмозона {zone:g} баллов (порог {num(need, 0)}), конструкция "
                        f"«{must.get('construction')}» без подтверждённой сейсмостойкости: "
                        f"землетрясение даёт {num(eq['share_of_net_pct'], 1)}% нетто-ставки и в этой зоне "
                        f"не может быть принято на обычных условиях"),
                "alternative": "исключить риск землетрясения из покрытия либо отказать в договоре",
                "calibrated": CALIBRATED}
    return None


# ================================================================================================
# Премия: варианты считаются тем же движком
# ================================================================================================
def _mult(th: dict, pct: float) -> float:
    """Множитель к нетто-ставке за франшизу pct%: узлы справочника + линейная связка между ними."""
    pts = sorted((float(k), float(v)) for k, v in th["franchise_multipliers"].items())
    if pct <= pts[0][0]:
        return pts[0][1]
    if pct >= pts[-1][0]:
        return pts[-1][1]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x1 <= pct <= x2:
            return y1 + (y2 - y1) * (pct - x1) / (x2 - x1) if x2 > x1 else y1
    return pts[-1][1]


ENGINE_NODES = [0.0, 0.5, 1.0, 2.0]      # варианты, которые есть в справочнике коэффициентов


def _engine_node(pct: float) -> float:
    return max([n for n in ENGINE_NODES if n <= pct] or [0.0])


def _premium_at(con, must, optional, th, market, as_of, analysis, pct: float) -> dict:
    """Премия при франшизе pct%. До 2% — полностью движком (справочник коэффициентов);
    выше 2% — премия узла 2% × отношение экспертных множителей (экстраполяция, calibrated = 0)."""
    node = _engine_node(pct)
    opt = dict(optional or {})
    opt["deductible"] = {"pct": node}
    res = ra.analyze(con, must, opt, thresholds=th, market=market, as_of=as_of,
                     assumptions=analysis.get("assumptions"))
    if not res.get("ok"):                 # не падаем: честно говорим, почему вариант не посчитан
        v = res.get("validation") or {}
        why = ", ".join(list(v.get("missing") or []) + list((v.get("errors") or {}).keys())) or "нет данных"
        return {"ok": False, "reason": f"вариант с франшизой {num(pct, 2)}% не посчитан: {why}"}
    prem = float(res["summary"]["premium"])
    extra = _mult(th, pct) / _mult(th, node) if node != pct else 1.0
    how = f"движок, франшиза {num(node, 1)}% из справочника коэффициентов"
    if extra != 1.0:
        prem *= extra
        how += (f"; продолжение до {num(pct, 1)}% — множитель {num(_mult(th, pct))} / {num(_mult(th, node))} "
                f"= {num(extra)} ({EXPERT})")
    floor = _floor_premium(res)
    if floor is not None and prem < floor:
        prem = floor
        how += f"; ниже минимальной ставки компании премия не опускается — {money(floor)} сум"
    return {"ok": True, "premium": round(prem), "how": how, "node_pct": node,
            "rate_applied_pct": res["summary"]["rate_applied_pct"],
            "rate_technical_pct": res["summary"]["rate_technical_pct"]}


def _floor_premium(res: dict) -> Optional[float]:
    s = res["summary"]
    if not s.get("rate_min_pct"):
        return None
    return s["rate_min_pct"] / 100 * s["sum_insured"] * s["term_days"] / 365


def _variants(con, must, optional, th, market, as_of, analysis, pct, peril) -> dict:
    S = float(analysis["summary"]["sum_insured"])
    base = float(analysis["summary"]["premium"])
    cur_pct = ra._deductible_pct(optional, S) or 0.0

    no_change = {"ok": True, "premium": round(base), "how": "франшиза не вводится", "node_pct": 0.0,
                 "rate_applied_pct": analysis["summary"]["rate_applied_pct"],
                 "rate_technical_pct": analysis["summary"]["rate_technical_pct"]}
    with_fr = _premium_at(con, must, optional, th, market, as_of, analysis, pct) if pct > 0 else no_change
    if not with_fr.get("ok"):             # вариант не посчитан — показываем базовую премию и причину
        with_fr = no_change | {"how": with_fr.get("reason") or "вариант не посчитан",
                               "не_посчитан": True}
    peril_delta = 0.0
    peril_line = None
    if peril:
        # франшиза по одному риску: снимается доля этого риска в нетто-ставке, пропорционально франшизе
        share = peril["share_of_net_pct"] / 100
        peril_delta = -with_fr["premium"] * share * peril["pct"] / 100
        peril_line = (f"франшиза {num(peril['pct'], 0)}% по риску «{peril['name']}»: доля риска в нетто-ставке "
                      f"{num(peril['share_of_net_pct'], 1)}% × {num(peril['pct'] / 100)} = минус "
                      f"{money(-peril_delta)} сум ({EXPERT})")

    fr_premium = round(with_fr["premium"] + peril_delta)

    # мероприятия — готовые premium_delta из блока reducers (движок уже посчитал)
    measures = [r for r in analysis.get("reducers", []) if r["kind"] == "мероприятие" and (r.get("premium_delta") or 0) < 0]
    m_delta = sum(r["premium_delta"] for r in measures)
    m_premium = round(base + m_delta)

    options = [
        {"key": "base", "title": "Базовый вариант (как сейчас)", "premium": round(base),
         "delta": 0, "delta_pct": 0.0,
         "franchise_pct": round(cur_pct, 2), "franchise_amount": round(S * cur_pct / 100),
         "what": ("Условия из заявки" + (f", франшиза {num(cur_pct, 2)}%" if cur_pct else ", без франшизы")),
         "calibrated": CALIBRATED},
        {"key": "franchise", "title": f"С франшизой {num(pct, 2)}%" if pct > 0 else "Без франшизы",
         "premium": fr_premium, "delta": round(fr_premium - base),
         "delta_pct": round((fr_premium - base) / base * 100, 1) if base else 0.0,
         "franchise_pct": round(pct, 2), "franchise_amount": round(S * pct / 100),
         "what": (f"Безусловная франшиза {num(pct, 2)}% = {money(S * pct / 100)} сум с каждого убытка"
                  if pct > 0 else "Франшиза не требуется")
                 + (f"; дополнительно {peril_line}" if peril_line else ""),
         "how": with_fr["how"], "rate_applied_pct": with_fr["rate_applied_pct"],
         "calibrated": CALIBRATED},
        {"key": "measures", "title": "С мероприятиями по снижению риска", "premium": m_premium,
         "delta": round(m_delta), "delta_pct": round(m_delta / base * 100, 1) if base else 0.0,
         "franchise_pct": round(cur_pct, 2), "franchise_amount": round(S * cur_pct / 100),
         "what": ("; ".join(r["text"] for r in measures) if measures
                  else "Мероприятий, снижающих ставку, движок не предложил"),
         "measures": [{"text": r["text"], "why": r.get("why"), "premium_delta": r["premium_delta"],
                       "mandatory": r.get("mandatory"), "deadline_days": r.get("deadline_days")}
                      for r in measures],
         "calibrated": CALIBRATED},
    ]
    effect = {
        "base_premium": round(base),
        "premium_with_franchise": fr_premium,
        "delta": round(fr_premium - base),
        "delta_pct": round((fr_premium - base) / base * 100, 1) if base else 0.0,
        "formula": (f"премия без изменения условий {money(base)} сум → с франшизой "
                    f"{num(pct, 2)}% {money(fr_premium)} сум: "
                    + ("минус " if fr_premium <= base else "плюс ")
                    + f"{money(abs(fr_premium - base))} сум"),
        "how": with_fr["how"] + (f"; {peril_line}" if peril_line else ""),
        "client_keeps": round(S * pct / 100),
        "calibrated": CALIBRATED,
    }
    return {"options": options, "effect": effect}


# ================================================================================================
# Совет по тарифу
# ================================================================================================
def _tariff_advice(analysis: dict, th: dict) -> dict:
    s = analysis["summary"]
    mk = analysis.get("market") or {}
    applied = s["rate_applied_pct"]
    tech = s["rate_technical_pct"]
    mrate = mk.get("market_rate_pct")
    ups = [{"factor": d["factor"], "option": d["option"], "multiplier": d["multiplier"],
            "premium_effect": d["premium_effect"]} for d in analysis.get("top_drivers", [])]
    downs = []
    seen = set()
    for r in analysis.get("risks", []):
        for d in r.get("drivers", []):
            key = (d["factor"], d["option"])
            if d["multiplier"] < 1 and key not in seen:
                seen.add(key)
                downs.append({"factor": d["factor"], "option": d["option"], "multiplier": d["multiplier"]})
    lines = [f"Применённая ставка {num(applied, 4)}% — это то, что платит клиент.",
             f"Техническая ставка движка {num(tech, 4)}% — цена риска по справочнику."]
    if s.get("rate_min_pct"):
        min_applied = abs(applied - s["rate_min_pct"]) < 1e-6 and s["rate_min_pct"] > tech
        lines.append(f"Минимум компании по продукту {num(s['rate_min_pct'], 4)}%: "
                     + ("он и применён — техническая ставка ниже минимума."
                        if min_applied else "техническая ставка выше минимума, применена техническая."))
    if mrate:
        rel = applied / mrate
        lines.append(f"Рынок {num(mrate, 4)}% ({mk.get('label') or 'ориентир рынка'}): наша ставка "
                     f"{'выше' if rel > 1 else 'ниже'} рынка в {num(rel)} раза.")
    for d in ups[:3]:
        lines.append(f"Поднимает: {d['factor']} — «{d['option']}» (×{num(d['multiplier'])}, "
                     f"{money(d['premium_effect'])} сум премии).")
    for d in downs[:3]:
        lines.append(f"Опускает: {d['factor']} — «{d['option']}» (×{num(d['multiplier'])}).")
    return {"rate_applied_pct": applied, "rate_technical_pct": tech, "rate_net_pct": s["rate_net_pct"],
            "rate_min_pct": s.get("rate_min_pct"), "rate_market_pct": mrate,
            "market_label": mk.get("label"),
            "applied_vs_technical": round(applied / tech, 2) if tech else None,
            "applied_vs_market": round(applied / mrate, 2) if mrate else None,
            "raises": ups, "lowers": downs, "lines": lines, "calibrated": CALIBRATED}


# ================================================================================================
# Обоснование и текст
# ================================================================================================
def _basis(analysis, level, score, band, pct, losses, why_pick, peril, statutory, S) -> list:
    out = [f"Уровень риска — «{level}» ({num(score, 1)} из 100 по шкале анализа)."]
    for c in analysis["level"]["components"]:
        if c.get("applicable") and c.get("contribution"):
            out.append(f"  вклад: {c['name']} — {num(c['contribution'], 1)} балла ({c['why']}).")
    if losses.get("why"):
        out.append("Убытки: " + losses["why"] + ".")
    out += why_pick
    if pct > 0:
        out.append(f"Итого франшиза {num(pct, 2)}% страховой суммы = {money(S * pct / 100)} сум "
                   f"с каждого убытка; тип — безусловная.")
    if peril:
        out.append(f"Отдельно по риску «{peril['name']}»: {peril['why']}. Франшиза {num(peril['pct'], 0)}% "
                   f"или {peril['alternative']}.")
    if statutory:
        out.append(statutory["why"] + ".")
    out.append("Все пороги экспертные (calibrated = 0) — до выгрузки убытков компании за 3–5 лет.")
    return out


def _explain(out: dict) -> list:
    fr = out["franchise"]
    lines = [f"Решение: {out['decision']} (уровень риска «{out['level']}», {num(out['level_score'], 1)} из 100).",
             f"Франшиза: {fr['text']}."]
    lines += ["Почему так:"] + [f"— {b}" for b in out["basis"]]
    lines += ["Эффект на премию: " + out["premium_effect"]["formula"] + ".",
              "Как посчитано: " + out["premium_effect"]["how"] + "."]
    lines += ["Три варианта:"]
    for o in out["options"]:
        d = o["delta"]
        lines.append(f"— {o['title']}: премия {money(o['premium'])} сум"
                     + ("" if d == 0 else f" ({'минус' if d < 0 else 'плюс'} {money(abs(d))} сум, "
                                          f"{num(o['delta_pct'], 1)}%)")
                     + f". {o['what']}.")
    lines += ["Совет по тарифу:"] + [f"— {x}" for x in out["tariff_advice"]["lines"]]
    lines.append(out["note"])
    return lines
