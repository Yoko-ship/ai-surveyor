"""
Франшиза только при наличии основания (решение заказчика 27.09.2026).

Дословно заказчик: «необязательно применять франшизу, нужно по ситуации действовать, но не всегда её
применение необходимо». Поэтому по умолчанию франшиза НЕ рекомендуется: решение «no_franchise»,
тип «нет», 0%, текст «Франшиза не требуется» и объяснение, почему основания нет.

Франшиза предлагается, только если сработало хотя бы одно основание (коды в поле grounds):
    small_losses   — в истории franchise_loss_count_high и более убытков за 3 года И доля мелких
                     убытков больше franchise_small_share (франшиза как раз отсекает такой поток);
    level_high     — уровень риска «Высокий» или «Критический» (франшиза как условие приёма);
    dominant_peril — один риск даёт franchise_dominant_share и больше нетто-ставки при
                     неблагоприятном факторе (сейсмозона ≥ franchise_critical_seismic_zone, горючие
                     конструкции/материалы без защиты) → франшиза по ЭТОМУ риску, а не по договору;
    client_request — клиент сам просит снизить премию (optional.want_lower_premium) либо франшиза
                     прямо указана в заявке (optional.deductible).
Уровень «Повышенный» сам по себе основанием не является — только вместе с small_losses или
dominant_peril (либо по запросу клиента).

Размер, когда основание есть, — из прежних экспертных вилок franchise_by_level (Низкий 0–1%,
Умеренный 1–2%, Повышенный 2–5%, Высокий 5–10%, Критический 10–90%); внутри вилки убытки двигают к
верхней границе, чистая история — к нижней; класс ограничивает потолок (franchise_class_caps).
Для «Критического» с неприемлемым риском (землетрясение в 9-балльной зоне у здания без
сейсмостойкости) — как было: франшиза до franchise_critical_peril_pct % по этому риску либо отказ.
По обязательным видам (режим тарифа «нормативный акт») франшиза не предлагается и не считается:
recommend_franchise даёт decision_code «statutory» с одним общим объяснением (основания по одному не
разбираются), what_if тоже отказывает — премия остаётся без изменений. Если в what_if запрошен размер
выше потолка класса (franchise_class_caps), премия считается, но в ответе появляется warning.

Вместо франшизы в ответе всегда есть alternatives[]: предупредительные мероприятия (из reducers),
исключение риска, приведение страховой суммы к стоимости — у каждой альтернативы эффект на премию.
Варианты договора (options): base и measures — всегда, franchise — только при основании или запросе;
любой свой размер франшизы пользователь считает функцией what_if(...) (в ответе — options_custom,
если франшиза передана в optional.deductible).

Эффект на премию считается тем же движком, что и весь расчёт: варианты пересчитываются через
risk_analytics.analyze (то есть через engine.calculate), а мероприятия берутся из блока reducers
(готовые premium_delta). Второго калькулятора здесь нет.

Пороги — в risk_thresholds (calibrated = 0), правит администратор.

Контракт:
    recommend_franchise(con, must, optional=None, *, analysis=None, thresholds=None,
                        market=None, as_of=None) -> dict
    what_if(con, must, optional=None, pct=0.0, *, analysis=None, thresholds=None,
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
PERIL_ONLY = "безусловная по риску"  # франшиза только по одному риску, по договору франшизы нет
NO_FRANCHISE = "нет"

CONTRACT_GROUNDS = ("small_losses", "level_high", "client_request")
LEVELS_HIGH = ("Высокий", "Критический")

# перилы, для которых неблагоприятным фактором служит сейсмозона / горючесть
SEISMIC_PERILS = ("earthquake", "landslide", "mudflow", "collapse", "subsidence")
FIRE_PERILS = ("fire", "explosion", "lightning")
COMBUSTIBLE_CONSTRUCTION = ("wood", "mixed")
COMBUSTIBLE_ACTIVITY = ("flammable",)
WEAK_PROTECTION = ("none", "alarm")

DECISION = {
    "no_franchise": "принять без франшизы — оснований для франшизы нет",
    "accept": "принять",
    "accept_franchise": "принять с безусловной франшизой",
    "accept_peril_franchise": "принять с франшизой по отдельному риску",
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
    must/optional — те же поля, что у risk_analytics.analyze (FIELDS), плюс необязательный флаг
                 optional['want_lower_premium'] — «клиент просит снизить премию»;
    analysis   — готовый результат analyze (чтобы не считать дважды); нет — посчитается здесь;
    thresholds — пороги (нет — берутся из risk_thresholds поверх DEFAULT_THRESHOLDS);
    market     — картина рынка (как её отдаёт app/market_picture.py);
    as_of      — дата расчёта.
    """
    prep = _prepare(con, must, optional, thresholds, market, as_of, analysis)
    if not prep["ok"]:
        return {"ok": False, "validation": prep["validation"],
                "message": "Франшиза не подбиралась: анализ риска не выполнен."}
    must, optional, th, analysis, as_of = (prep["must"], prep["optional"], prep["th"],
                                          prep["analysis"], prep["as_of"])

    s = analysis["summary"]
    S = float(s["sum_insured"])
    level = analysis["level"]["level"]
    score = analysis["level"]["score"]
    classes = ra.parse_classes(must.get("class_code"))
    statutory = _statutory(con, analysis)

    # ---------- история убытков и вилка по уровню ----------
    losses = _losses(optional, S, th)
    band = [float(x) for x in (th["franchise_by_level"].get(level) or [0, 0])]
    cap, cap_why = _cap(classes, th)
    cur_pct = ra._deductible_pct(optional, S) or 0.0
    want_lower = bool(optional.get("want_lower_premium"))

    # ---------- основания ----------
    critical = _critical_peril(analysis, must, optional, th, level)
    dominant, dom_why = _dominant_peril(analysis, must, optional, th)
    checks = _ground_checks(level, losses, th, critical or dominant, dom_why, want_lower, cur_pct)
    if statutory:
        # обязательный вид: основания не разбираются по одному — франшизы нет по самой природе вида,
        # общее объяснение даётся один раз (в basis и в тексте франшизы)
        for c in checks:
            c["ok"] = False
            c["statutory"] = True
    grounds = [c["code"] for c in checks if c["ok"]]
    contract_grounds = [g for g in grounds if g in CONTRACT_GROUNDS]

    # ---------- размер ----------
    pct, why_pick, peril = 0.0, [], None
    if statutory:
        why_pick = [statutory["why"]]
    elif contract_grounds:
        pct, why_pick = _pick(band, level, losses, th)
        if pct <= 0 and "client_request" in grounds and band[1] > 0:
            # клиент просит снизить премию: нулевая франшиза его запрос не исполняет — берём верх вилки
            pct = float(band[1])
            why_pick.append(f"но клиент просит снизить премию, а франшиза 0% премию не снижает — "
                            f"взят верх вилки {num(pct, 2)}%")
        if cap is not None and pct > cap:
            pct, why_pick = cap, why_pick + [cap_why]
        peril = critical or (dominant if "dominant_peril" in grounds else None)
        if peril is not None and peril.get("pct") is None:
            peril["pct"] = _peril_pct(band, cap)
    elif "dominant_peril" in grounds:
        peril = critical or dominant
        if peril.get("pct") is None:
            peril["pct"] = _peril_pct(band, cap)
        why_pick = [f"по договору франшиза не вводится: единственное основание — доминирующий риск "
                    f"«{peril['name']}», франшиза ставится только по нему "
                    f"({num(peril['pct'], 0)}% страховой суммы при убытке от этого риска)"]
    else:
        why_pick = [f"оснований для франшизы нет: ни одно из четырёх оснований не сработало "
                    f"(поток мелких убытков, высокий уровень риска, доминирующий риск, запрос клиента) "
                    f"— франшиза не требуется, вилка {num(band[0], 1)}–{num(band[1], 1)}% уровня "
                    f"«{level}» не применяется"]

    # ---------- варианты премии (пересчёт движком) ----------
    variants = _variants(con, must, optional, th, market, as_of, analysis, pct, peril, grounds)

    # ---------- решение ----------
    code = _decision_code(level, statutory, pct, peril)

    if pct > 0:
        ftype, scope = UNCONDITIONAL, "договор"
        text = (f"Безусловная франшиза {num(pct, 2)}% страховой суммы — {money(S * pct / 100)} сум "
                f"с каждого убытка")
        if peril:
            text += (f"; по риску «{peril['name']}» — {num(peril['pct'], 0)}% "
                     f"({money(S * peril['pct'] / 100)} сум)")
    elif peril:
        ftype, scope = PERIL_ONLY, "риск"
        text = (f"По договору франшиза не требуется; по риску «{peril['name']}» — безусловная "
                f"франшиза {num(peril['pct'], 0)}% = {money(S * peril['pct'] / 100)} сум "
                f"с каждого убытка от этого риска")
    else:
        ftype, scope = NO_FRANCHISE, "нет"
        text = ("Франшиза не применяется: обязательный вид, тариф установлен нормативным актом"
                if statutory else "Франшиза не требуется")

    fr = {
        "type": ftype,
        "scope": scope,
        "pct": round(pct, 2),
        "amount": round(S * pct / 100),
        "peril_pct": (round(float(peril["pct"]), 2) if peril else 0.0),
        "peril_amount": (round(S * float(peril["pct"]) / 100) if peril else 0),
        "band_pct": band,
        "text": text,
        "peril": peril,
        "calibrated": CALIBRATED,
    }

    basis = _basis(analysis, level, score, band, pct, losses, why_pick, peril, statutory, S, checks)
    out = {
        "ok": True,
        "calibrated": CALIBRATED,
        "level": level,
        "level_score": score,
        "decision": DECISION[code],
        "decision_code": code,
        "franchise": fr,
        "needed": bool(pct > 0 or peril),
        "grounds": grounds,
        "grounds_checked": checks,
        "losses": losses,
        "basis": basis,
        "premium_effect": variants["effect"],
        "options": variants["options"],
        "alternatives": _alternatives(analysis, peril, th),
        "tariff_advice": _tariff_advice(analysis, th),
        "thresholds_source": th.get("_source"),
        "thresholds_used": {k: th[k] for k in ra.DEFAULT_THRESHOLDS if k.startswith("franchise_")},
        "legal_refs": [
            {"ref": "ГК РУз, ст. 936", "what": "неполное страхование: выплата в доле суммы к стоимости"},
            {"ref": "ГК РУз, ст. 938", "what": "страховая сумма выше стоимости: договор ничтожен в части превышения"},
            {"ref": "Положение № 1806, п. 15", "what": "обязательства по одному риску ≤ 20% (средства + резервы)"},
            {"ref": "Положение № 1882, п. 11",
             "what": ("части договора, относящиеся к разным учётным группам, учитываются отдельно "
                      "при расчёте резервов; проверка ставки по каждому классу — внутреннее правило "
                      "компании, а не требование этого пункта")},
        ],
        "note": ("Франшиза — часть убытка, которую клиент оставляет себе. Она не обязательна: по решению "
                 "заказчика от 27.09.2026 франшиза предлагается только при основании (поток мелких "
                 "убытков, высокий уровень риска, доминирующий риск, запрос клиента). Любой свой размер "
                 "франшизы можно посчитать отдельно (what_if). Все пороги экспертные (calibrated = 0), "
                 "правятся администратором."),
    }
    if variants.get("custom"):
        out["options_custom"] = variants["custom"]
    out["explanation"] = _explain(out)
    out["llm_text"] = "\n".join(out["explanation"])
    return out


def what_if(con, must: dict, optional: Optional[dict] = None, pct: float = 0.0, *,
            analysis: Optional[dict] = None, thresholds: Optional[dict] = None,
            market: Optional[dict] = None, as_of: Optional[date] = None) -> dict:
    """Премия при любом размере франшизы, который назвал пользователь (пересчёт тем же движком).

    Рекомендацией не является: считает ровно то, что попросили. Возвращает
    {"ok", "pct", "amount", "base_premium", "premium", "delta", "delta_pct", "how", "text"}
    плюс "decision_code", "applied" и, когда есть что сказать, "warning".

    Два случая, когда «что если» не считает запрошенный размер или считает с оговоркой
    (замечание контролёра 27.09.2026):
      обязательный вид (тариф по нормативному акту) — франшиза не применяется вообще:
          decision_code = "statutory", applied = False, премия без изменений;
      размер выше потолка класса (franchise_class_caps) — считаем, как попросили, но в поле
          warning прямо говорим о превышении потолка (решение за андеррайтером).
    """
    prep = _prepare(con, must, optional, thresholds, market, as_of, analysis)
    if not prep["ok"]:
        return {"ok": False, "validation": prep["validation"],
                "message": "Расчёт «что если» не выполнен: анализ риска не выполнен."}
    must, optional, th, analysis, as_of = (prep["must"], prep["optional"], prep["th"],
                                          prep["analysis"], prep["as_of"])
    S = float(analysis["summary"]["sum_insured"])
    base = float(analysis["summary"]["premium"])
    p = max(0.0, float(ra._to_float(pct) or 0.0))

    statutory = _statutory(con, analysis)
    if statutory:                          # обязательный вид: франшизу не считаем вовсе
        return {"ok": True, "applied": False, "decision_code": "statutory",
                "pct": 0.0, "requested_pct": round(p, 2), "amount": 0,
                "base_premium": round(base), "premium": round(base), "delta": 0, "delta_pct": 0.0,
                "how": "по обязательным видам франшиза не применяется — расчёт не выполнялся",
                "statutory": statutory,
                "message": "по обязательным видам франшиза не применяется",
                "rate_applied_pct": analysis["summary"]["rate_applied_pct"],
                "text": (f"По обязательным видам франшиза не применяется: продукт "
                         f"{statutory['product_code']} «{statutory['product_name']}» — тариф и условия "
                         f"установлены нормативным актом. Запрошенная франшиза {num(p, 2)}% не "
                         f"считалась, премия без изменений — {money(base)} сум"),
                "calibrated": CALIBRATED}

    if p <= 0:
        return {"ok": True, "applied": False, "decision_code": "no_franchise",
                "pct": 0.0, "amount": 0, "base_premium": round(base),
                "premium": round(base), "delta": 0, "delta_pct": 0.0,
                "how": "франшиза не вводится", "text": "Без франшизы премия " + money(base) + " сум",
                "calibrated": CALIBRATED}

    cap, cap_why = _cap(ra.parse_classes(must.get("class_code")), th)
    warning = None
    if cap is not None and p > cap:
        warning = (f"запрошенная франшиза {num(p, 2)}% выше потолка класса — {cap_why}; расчёт "
                   f"выполнен как попросили, но такой размер выходит за внутреннее ограничение "
                   f"компании и требует решения андеррайтера")

    res = _premium_at(con, must, optional, th, market, as_of, analysis, p)
    if not res.get("ok"):
        return {"ok": False, "applied": False, "pct": round(p, 2), "message": res.get("reason"),
                "warning": warning}
    prem = res["premium"]
    text = (f"Франшиза {num(p, 2)}% = {money(S * p / 100)} сум с каждого убытка: премия "
            f"{money(prem)} сум вместо {money(base)} сум ("
            + ("минус " if prem <= base else "плюс ") + f"{money(abs(prem - base))} сум)")
    if warning:
        text += ". Внимание: " + warning
    return {"ok": True, "applied": True, "decision_code": "calculated",
            "pct": round(p, 2), "amount": round(S * p / 100),
            "base_premium": round(base), "premium": round(prem), "delta": round(prem - base),
            "delta_pct": round((prem - base) / base * 100, 1) if base else 0.0,
            "how": res["how"], "rate_applied_pct": res.get("rate_applied_pct"),
            "cap_pct": (round(cap, 2) if cap is not None else None),
            "warning": warning,
            "text": text,
            "calibrated": CALIBRATED}


# ================================================================================================
# Подготовка данных (общая для recommend_franchise и what_if)
# ================================================================================================
def _prepare(con, must, optional, thresholds, market, as_of, analysis) -> dict:
    optional = dict(optional or {})
    as_of = as_of or date.today()
    th = ra._merge(ra.DEFAULT_THRESHOLDS, thresholds) if thresholds is not None else ra.load_thresholds(con)
    if analysis is None:
        analysis = ra.analyze(con, must, optional, thresholds=thresholds, market=market, as_of=as_of)
    if not analysis.get("ok"):
        return {"ok": False, "validation": analysis.get("validation")}
    # быстрый режим: анализ шёл на полях, достроенных apply_defaults. Варианты премии считаем
    # на них же — иначе повторный analyze упадёт на незаполненных полях (дефект 22.09.2026)
    inputs = analysis.get("inputs") or {}
    if isinstance(inputs.get("must"), dict) and inputs["must"]:
        must = dict(inputs["must"])
        optional = dict(inputs.get("optional") or optional)
    return {"ok": True, "must": dict(must or {}), "optional": optional, "th": th,
            "analysis": analysis, "as_of": as_of}


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
        out["why"] = "убытки за 3 года не указаны — потока мелких убытков подтвердить нечем"
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
# Основания для франшизы
# ================================================================================================
def _ground_checks(level: str, losses: dict, th: dict, peril: Optional[dict], dom_why: str,
                   want_lower: bool, cur_pct: float) -> list:
    """Четыре основания: каждое — с ответом «да/нет» и объяснением, почему именно так."""
    out = []
    n = losses.get("count")
    share = losses.get("small_share")
    high = int(th["franchise_loss_count_high"])
    need_share = float(th["franchise_small_share"])
    if n is None:
        ok = False
        why = (f"истории убытков нет — поток мелких убытков не подтверждён "
               f"(основание: {high} и более убытков за 3 года при доле мелких выше {num(need_share)})")
    elif n >= high and share is not None and share > need_share:
        ok = True
        why = (f"убытков за 3 года {n} (порог {high}), доля мелких {num(share)} — больше порога "
               f"{num(need_share)}: франшиза отсекает именно такой поток")
    else:
        ok = False
        parts = [f"убытков за 3 года {n}"]
        if share is not None:
            parts.append(f"доля мелких {num(share)}")
        why = (", ".join(parts) + f" — для основания нужно {high} и более убытков И доля мелких выше "
               f"{num(need_share)}; здесь этого нет")
    out.append({"code": "small_losses", "title": "Поток мелких убытков", "ok": ok, "why": why,
                "calibrated": CALIBRATED})

    lh = level in LEVELS_HIGH
    why = (f"уровень риска «{level}» — франшиза нужна как условие приёма риска" if lh else
           f"уровень риска «{level}» — сам по себе основанием не является"
           + (" («Повышенный» даёт основание только вместе с потоком мелких убытков "
              "или доминирующим риском)" if level == "Повышенный" else ""))
    out.append({"code": "level_high", "title": "Высокий или критический уровень риска",
                "ok": lh, "why": why, "calibrated": CALIBRATED})

    out.append({"code": "dominant_peril", "title": "Доминирующий риск при неблагоприятном факторе",
                "ok": bool(peril), "why": (peril["why"] if peril else dom_why),
                "calibrated": CALIBRATED})

    cr = bool(want_lower or cur_pct > 0)
    if want_lower:
        why = "клиент просит снизить премию — франшиза предложена по его запросу, а не по риску"
    elif cur_pct > 0:
        why = (f"в заявке франшиза уже указана — {num(cur_pct, 2)}% страховой суммы; считаем размер "
               f"под этот запрос")
    else:
        why = "клиент не просил снижать премию и франшизу в заявке не указывал"
    out.append({"code": "client_request", "title": "Запрос клиента", "ok": cr, "why": why,
                "calibrated": CALIBRATED})
    return out


def _adverse_factor(must: dict, optional: dict, th: dict, code: str) -> Optional[str]:
    """Неблагоприятный фактор по конкретному риску: сейсмозона или горючесть без защиты."""
    zone = ra._seismic_zone(optional.get("seismic_zone"))
    need = float(th["franchise_critical_seismic_zone"])
    if code in SEISMIC_PERILS and zone is not None and zone >= need:
        return f"сейсмозона {zone:g} баллов (порог {num(need, 0)})"
    if code in FIRE_PERILS:
        comb = (must.get("construction") in COMBUSTIBLE_CONSTRUCTION
                or must.get("activity") in COMBUSTIBLE_ACTIVITY)
        weak = (optional.get("protection") or "none") in WEAK_PROTECTION
        if comb and weak:
            return (f"горючие конструкции или материалы (конструкция «{must.get('construction')}», "
                    f"деятельность «{must.get('activity')}») без достаточной защиты "
                    f"(«{optional.get('protection') or 'none'}»)")
    return None


def _dominant_peril(analysis: dict, must: dict, optional: dict, th: dict) -> tuple:
    """Риск с долей ≥ franchise_dominant_share в нетто-ставке И с неблагоприятным фактором.
    Размер франшизы по риску ставится вызывающей стороной (вилка уровня)."""
    need = float(th["franchise_dominant_share"])
    risks = analysis.get("risks") or []
    if not risks:
        return None, "риски по договору не разложены — доминирующий риск не определён"
    for r in risks:
        if r["share_of_net_pct"] / 100 < need:
            break
        adv = _adverse_factor(must, optional, th, r["code"])
        if adv:
            return ({"code": r["code"], "name": r["name"], "pct": None, "type": UNCONDITIONAL,
                     "share_of_net_pct": r["share_of_net_pct"], "ground": "dominant_peril",
                     "why": (f"риск «{r['name']}» даёт {num(r['share_of_net_pct'], 1)}% нетто-ставки — "
                             f"не меньше порога {num(need * 100, 0)}%, и по нему неблагоприятный фактор: "
                             f"{adv}. Франшиза нужна по этому риску, а не по всему договору"),
                     "alternative": f"исключить риск «{r['name']}» из покрытия",
                     "calibrated": CALIBRATED},
                    "")
    top = risks[0]
    if top["share_of_net_pct"] / 100 >= need:
        return None, (f"наибольший риск «{top['name']}» — {num(top['share_of_net_pct'], 1)}% нетто-ставки "
                      f"(порог {num(need * 100, 0)}%), но неблагоприятного фактора по нему нет "
                      f"(сейсмозона ниже {num(float(th['franchise_critical_seismic_zone']), 0)} баллов, "
                      f"конструкции и защита в норме) — франшиза по риску не нужна")
    return None, (f"доминирующего риска нет: наибольшая доля — «{top['name']}» "
                  f"{num(top['share_of_net_pct'], 1)}% нетто-ставки, порог {num(need * 100, 0)}%")


def _peril_pct(band: list, cap: Optional[float]) -> float:
    """Размер франшизы по отдельному риску — верх вилки уровня, с учётом потолка класса."""
    pct = float(band[1])
    if cap is not None and pct > cap:
        pct = float(cap)
    return round(pct, 2)


# ================================================================================================
# Выбор франшизы внутри вилки (когда основание есть)
# ================================================================================================
def _pick(band: list, level: str, losses: dict, th: dict) -> tuple:
    lo, hi = band
    why = [f"основание для франшизы есть; уровень риска «{level}» → вилка {num(lo, 1)}–{num(hi, 1)}% "
           f"страховой суммы (экспертная таблица franchise_by_level, calibrated = 0)"]
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


def _decision_code(level: str, statutory, pct: float, peril) -> str:
    if statutory:
        return "statutory"
    if pct <= 0 and not peril:
        return "no_franchise"
    if level == "Критический":
        return "accept_or_decline"
    if level == "Высокий":
        return "accept_conditions"
    if pct > 0:
        return "accept_franchise"
    return "accept_peril_franchise"


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
                "share_of_net_pct": eq["share_of_net_pct"], "ground": "critical_peril",
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


def _measures_delta(analysis: dict, measures: list) -> float:
    """Общий эффект нескольких мероприятий. Каждая premium_delta посчитана движком отдельно от базовой
    премии, поэтому скидки перемножаются, а не складываются: иначе при нескольких мероприятиях сумма
    скидок может превысить саму премию. Ниже минимальной ставки компании премия не опускается."""
    base = float(analysis["summary"]["premium"])
    if not measures or base <= 0:
        return 0.0
    k = 1.0
    for r in measures:
        k *= max(0.0, 1 + float(r["premium_delta"]) / base)
    prem = base * k
    floor = _floor_premium(analysis)
    if floor is not None and prem < floor:
        prem = min(floor, base)
    return prem - base


def _variants(con, must, optional, th, market, as_of, analysis, pct, peril, grounds) -> dict:
    """base и measures — всегда; franchise — только при основании или запросе клиента;
    options_custom — если пользователь сам передал франшизу в optional.deductible."""
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
        peril_delta = -with_fr["premium"] * share * float(peril["pct"]) / 100
        peril_line = (f"франшиза {num(float(peril['pct']), 0)}% по риску «{peril['name']}»: доля риска в "
                      f"нетто-ставке {num(peril['share_of_net_pct'], 1)}% × "
                      f"{num(float(peril['pct']) / 100)} = минус {money(-peril_delta)} сум ({EXPERT})")

    fr_premium = round(with_fr["premium"] + peril_delta)

    # мероприятия — готовые premium_delta из блока reducers (движок уже посчитал)
    measures = [r for r in analysis.get("reducers", []) if r["kind"] == "мероприятие" and (r.get("premium_delta") or 0) < 0]
    m_delta = _measures_delta(analysis, measures)
    m_premium = round(base + m_delta)

    base_opt = {"key": "base", "title": "Базовый вариант (как сейчас)", "premium": round(base),
                "delta": 0, "delta_pct": 0.0,
                "franchise_pct": round(cur_pct, 2), "franchise_amount": round(S * cur_pct / 100),
                "what": ("Условия из заявки" + (f", франшиза {num(cur_pct, 2)}%" if cur_pct else ", без франшизы")),
                "calibrated": CALIBRATED}
    measures_opt = {"key": "measures", "title": "С мероприятиями по снижению риска", "premium": m_premium,
                    "delta": round(m_delta), "delta_pct": round(m_delta / base * 100, 1) if base else 0.0,
                    "franchise_pct": round(cur_pct, 2), "franchise_amount": round(S * cur_pct / 100),
                    "what": ("; ".join(r["text"] for r in measures) if measures
                             else "Мероприятий, снижающих ставку, движок не предложил"),
                    "measures": [{"text": r["text"], "why": r.get("why"), "premium_delta": r["premium_delta"],
                                  "mandatory": r.get("mandatory"), "deadline_days": r.get("deadline_days")}
                                 for r in measures],
                    "calibrated": CALIBRATED}
    options = [base_opt]
    if pct > 0 or peril:
        title = (f"С франшизой {num(pct, 2)}%" if pct > 0 else
                 f"С франшизой {num(float(peril['pct']), 0)}% по риску «{peril['name']}»")
        what = (f"Безусловная франшиза {num(pct, 2)}% = {money(S * pct / 100)} сум с каждого убытка"
                if pct > 0 else f"По договору франшизы нет; {peril_line}")
        if pct > 0 and peril_line:
            what += f"; дополнительно {peril_line}"
        options.append({"key": "franchise", "title": title,
                        "premium": fr_premium, "delta": round(fr_premium - base),
                        "delta_pct": round((fr_premium - base) / base * 100, 1) if base else 0.0,
                        "franchise_pct": round(pct, 2), "franchise_amount": round(S * pct / 100),
                        "peril_pct": round(float(peril["pct"]), 2) if peril else 0.0,
                        "grounds": list(grounds),
                        "what": what, "how": with_fr["how"],
                        "rate_applied_pct": with_fr["rate_applied_pct"],
                        "calibrated": CALIBRATED})
    options.append(measures_opt)

    custom = None
    if cur_pct > 0:
        c = _premium_at(con, must, optional, th, market, as_of, analysis, cur_pct)
        if c.get("ok"):
            custom = {"key": "custom", "title": f"Ваш размер франшизы {num(cur_pct, 2)}%",
                      "premium": round(c["premium"]), "delta": round(c["premium"] - base),
                      "delta_pct": round((c["premium"] - base) / base * 100, 1) if base else 0.0,
                      "franchise_pct": round(cur_pct, 2), "franchise_amount": round(S * cur_pct / 100),
                      "what": (f"Размер франшизы задан пользователем: {num(cur_pct, 2)}% = "
                               f"{money(S * cur_pct / 100)} сум с каждого убытка"),
                      "how": c["how"], "rate_applied_pct": c.get("rate_applied_pct"),
                      "calibrated": CALIBRATED}

    effect = {
        "base_premium": round(base),
        "premium_with_franchise": fr_premium,
        "delta": round(fr_premium - base),
        "delta_pct": round((fr_premium - base) / base * 100, 1) if base else 0.0,
        "formula": ((f"премия без изменения условий {money(base)} сум → с франшизой "
                     f"{num(pct, 2)}% {money(fr_premium)} сум: "
                     + ("минус " if fr_premium <= base else "плюс ")
                     + f"{money(abs(fr_premium - base))} сум") if (pct > 0 or peril) else
                    (f"франшиза не вводится — премия остаётся {money(base)} сум")),
        "how": with_fr["how"] + (f"; {peril_line}" if peril_line else ""),
        "client_keeps": round(S * pct / 100) if pct > 0 else (
            round(S * float(peril["pct"]) / 100) if peril else 0),
        "calibrated": CALIBRATED,
    }
    return {"options": options, "effect": effect, "custom": custom}


# ================================================================================================
# Альтернативы франшизе
# ================================================================================================
def _alternatives(analysis: dict, peril: Optional[dict], th: dict) -> list:
    """Чем можно обойтись вместо франшизы: мероприятия, исключение риска, сумма к стоимости."""
    s = analysis["summary"]
    base = float(s["premium"])
    S = float(s["sum_insured"])
    V = ra._to_float(s.get("object_value"))
    out = []

    measures = [r for r in analysis.get("reducers", [])
                if r["kind"] == "мероприятие" and (r.get("premium_delta") or 0) < 0]
    free = [r for r in analysis.get("reducers", [])
            if r["kind"] == "мероприятие" and not (r.get("premium_delta") or 0)]
    if measures or free:
        d = _measures_delta(analysis, measures)
        out.append({"code": "measures", "title": "Предупредительные мероприятия вместо франшизы",
                    "what": "; ".join(r["text"] for r in (measures + free)),
                    "premium_delta": round(d), "premium": round(base + d),
                    "effect": (f"премия {money(base)} → {money(base + d)} сум (минус {money(-d)} сум)"
                               if d else "на ставку не влияет, но снижает вероятность убытка"),
                    "deadline_days": int(th["franchise_measures_note_days"]),
                    "keeps_cover": True, "calibrated": CALIBRATED})

    excl = [r for r in analysis.get("reducers", []) if r["kind"] == "риск" and (r.get("premium_delta") or 0) < 0]
    for r in excl[:3]:
        d = r["premium_delta"]
        out.append({"code": "exclude_peril", "title": "Исключение риска из покрытия",
                    "what": r["text"], "premium_delta": round(d), "premium": round(base + d),
                    "effect": f"премия {money(base)} → {money(base + d)} сум (минус {money(-d)} сум)",
                    "keeps_cover": False,
                    "why": "убыток от исключённого риска клиент несёт полностью, не только франшизу",
                    "calibrated": CALIBRATED})
    if not excl and peril:
        d = -base * peril["share_of_net_pct"] / 100
        out.append({"code": "exclude_peril", "title": "Исключение риска из покрытия",
                    "what": peril.get("alternative") or f"исключить риск «{peril['name']}»",
                    "premium_delta": round(d), "premium": round(base + d),
                    "effect": (f"доля риска в нетто-ставке {num(peril['share_of_net_pct'], 1)}% → "
                               f"премия {money(base)} → {money(base + d)} сум ({EXPERT})"),
                    "keeps_cover": False, "calibrated": CALIBRATED})

    if V and S and abs(S - V) > 1:
        if S < V:
            d = base * (V / S - 1)
            out.append({"code": "sum_to_value", "title": "Привести страховую сумму к стоимости",
                        "what": (f"поднять сумму с {money(S)} до {money(V)} сум — выплата станет полной, "
                                 f"без пропорции (ГК РУз, ст. 936)"),
                        "premium_delta": round(d), "premium": round(base + d),
                        "effect": (f"премия {money(base)} → {money(base + d)} сум (плюс {money(d)} сум): "
                                   f"это не снижение премии, а замена франшизы полным покрытием — сейчас "
                                   f"клиент и так получает лишь {num(S / V * 100, 1)}% убытка"),
                        "keeps_cover": True, "legal_ref": "ГК РУз, ст. 936", "calibrated": CALIBRATED})
        else:
            d = base * (V / S - 1)
            out.append({"code": "sum_to_value", "title": "Снизить страховую сумму до стоимости",
                        "what": (f"сумма {money(S)} сум выше стоимости {money(V)} сум — в части превышения "
                                 f"договор ничтожен (ГК РУз, ст. 938); снизить сумму до стоимости"),
                        "premium_delta": round(d), "premium": round(base + d),
                        "effect": f"премия {money(base)} → {money(base + d)} сум (минус {money(-d)} сум)",
                        "keeps_cover": True, "legal_ref": "ГК РУз, ст. 938", "calibrated": CALIBRATED})
    return out


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
def _basis(analysis, level, score, band, pct, losses, why_pick, peril, statutory, S, checks) -> list:
    out = [f"Уровень риска — «{level}» ({num(score, 1)} из 100 по шкале анализа)."]
    for c in analysis["level"]["components"]:
        if c.get("applicable") and c.get("contribution"):
            out.append(f"  вклад: {c['name']} — {num(c['contribution'], 1)} балла ({c['why']}).")
    if losses.get("why"):
        out.append("Убытки: " + losses["why"] + ".")
    if statutory:
        # одно общее объяснение вместо разбора четырёх оснований
        out.append("Основания для франшизы не разбираются: по обязательным видам франшиза не "
                   "применяется — тариф и условия установлены нормативным актом.")
    else:
        out.append("Основания для франшизы (нужно хотя бы одно):")
        for c in checks:
            out.append(f"  {'есть' if c['ok'] else 'нет'} — {c['title']}: {c['why']}.")
    out += why_pick
    if pct > 0:
        out.append(f"Итого франшиза {num(pct, 2)}% страховой суммы = {money(S * pct / 100)} сум "
                   f"с каждого убытка; тип — безусловная.")
    elif not peril and not statutory:
        out.append("Итого: франшиза не требуется — вместо неё смотрим альтернативы "
                   "(мероприятия, исключение риска, приведение суммы к стоимости).")
    if peril:
        out.append(f"Отдельно по риску «{peril['name']}»: {peril['why']}. Франшиза "
                   f"{num(float(peril['pct']), 0)}% или {peril['alternative']}.")
    if statutory:
        out.append(statutory["why"] + ".")
    out.append("Все пороги экспертные (calibrated = 0) — до выгрузки убытков компании за 3–5 лет.")
    return out


def _explain(out: dict) -> list:
    fr = out["franchise"]
    lines = [f"Решение: {out['decision']} (уровень риска «{out['level']}», {num(out['level_score'], 1)} из 100).",
             f"Франшиза: {fr['text']}."]
    if out["grounds"]:
        lines.append("Основания: " + ", ".join(c["title"] for c in out["grounds_checked"] if c["ok"]) + ".")
    elif out["decision_code"] == "statutory":
        lines.append("По обязательным видам франшиза не применяется — основания не разбираются.")
    else:
        lines.append("Оснований для франшизы нет — она не нужна.")
    lines += ["Почему так:"] + [(b if b.startswith(" ") else f"— {b}") for b in out["basis"]]
    lines += ["Эффект на премию: " + out["premium_effect"]["formula"] + ".",
              "Как посчитано: " + out["premium_effect"]["how"] + "."]
    lines += ["Варианты договора:"]
    for o in out["options"]:
        d = o["delta"]
        lines.append(f"— {o['title']}: премия {money(o['premium'])} сум"
                     + ("" if d == 0 else f" ({'минус' if d < 0 else 'плюс'} {money(abs(d))} сум, "
                                          f"{num(o['delta_pct'], 1)}%)")
                     + f". {o['what']}.")
    if out.get("options_custom"):
        c = out["options_custom"]
        lines.append(f"— {c['title']}: премия {money(c['premium'])} сум. {c['what']}.")
    if out.get("alternatives"):
        lines += ["Вместо франшизы можно:"]
        for a in out["alternatives"]:
            lines.append(f"— {a['title']}: {a['what']} — {a['effect']}.")
    lines += ["Совет по тарифу:"] + [f"— {x}" for x in out["tariff_advice"]["lines"]]
    lines.append(out["note"])
    return lines
