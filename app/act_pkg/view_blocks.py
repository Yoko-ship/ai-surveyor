"""Блоки акта на языке: сценарии PML/EML/MFL, франшиза (как считали, варианты), мероприятия."""
from typing import Optional

from .. import act_engine as ae
from .. import act_extras as ax
from .. import act_texts as tx
from ..act_texts import money, pct, t

from .view_fmt import _money_na, _mult, _signed
from .view_rows import _otype_label, _parts_sc_how, _row


def _objects_sc_how(sc: dict, lang: str) -> list:
    """Как посчитаны сценарии договора из объектов: правило и обе разбивки (самый крупный объект и сумма)."""
    d = sc.get("objects") or {}
    lg, sm = d.get("largest") or {}, d.get("sum") or {}
    out = [t("obj_sc_rule", lang)]
    if lg:
        out.append(t("obj_sc_largest", lang, n=lg.get("index"), label=lg.get("label") or "",
                     pml=money(lg.get("PML"), lang), eml=money(lg.get("EML"), lang), mfl=money(lg.get("MFL"), lang)))
    if sm:
        out.append(t("obj_sc_sum", lang, k=sm.get("count"), pml=money(sm.get("PML"), lang),
                     eml=money(sm.get("EML"), lang), mfl=money(sm.get("MFL"), lang)))
    for e in d.get("excluded") or []:
        out.append(t("obj_sc_excluded", lang, n=e.get("index")))
    return out


_PCT_KEYS = ("pct", "from", "to", "rate", "rate_after", "raw", "min", "cap")
_MONEY_KEYS = ("amount", "base", "with", "premium", "before", "after", "sum", "value")


def _fmt_params(params: dict, lang: str) -> dict:
    out = {}
    for k, v in (params or {}).items():
        name = {"from": "from_", "with": "with_"}.get(k, k)
        if v is None:
            out[name] = t("na", lang)
        elif k in _PCT_KEYS:
            out[name] = pct(v, lang)
        elif k == "delta":
            out[name] = _signed(v, lang)
        elif k in _MONEY_KEYS:
            out[name] = money(v, lang)
        elif k == "mult":
            out[name] = _mult(v, lang)
        elif k == "type":
            out[name] = tx.label(tx.FR_TYPE_LABELS, v, lang)
        else:
            out[name] = v
    return out


def _fr_how_text(item: dict, lang: str) -> str:
    p = item.get("params") or {}
    code = item["code"]
    if code == "frh_size" and p.get("from") is not None and p.get("from") == p.get("to"):
        code = "frh_size_one"                     # вилка с равными границами
    return t(code, lang, **_fmt_params(p, lang))


def _alt_view(a: dict, lang: str) -> dict:
    ref = a.get("legal_ref")
    return {"code": a["code"], "text": t(a["code"], lang, **_fmt_params(a.get("params"), lang)),
            "premium": a.get("premium"), "premium_delta": a.get("premium_delta"),
            "base_premium": a.get("base_premium"),
            "legal_ref": tx.label(tx.LEGAL_REFS, ref, lang) if ref else None, "calibrated": ae.CALIBRATED}


def _franchise_extra(fr: dict, how: list, alts: list, lang: str) -> dict:
    """Новые поля блока franchise (29.09.2026). В старых актах их нет — отдаются значения «нет франшизы»."""
    w = fr.get("warning")
    return {"status": fr.get("status") or ("statutory" if fr.get("code") == "fr_statutory" else
                                           "proposed" if fr.get("needed") else "none"),
            "applied": bool(fr.get("applied")), "applied_by": fr.get("applied_by"),
            "type": fr.get("type"),
            "type_label": tx.label(tx.FR_TYPE_LABELS, fr["type"], lang) if fr.get("type") else None,
            "size_pct": fr.get("size_pct", 0.0), "size_amount": fr.get("size_amount", 0), "cap_pct": fr.get("cap_pct"),
            "premium_before": fr.get("premium_before"), "premium_after": fr.get("premium_after"),
            "delta": fr.get("delta"), "delta_pct": fr.get("delta_pct"),
            "rate_before": fr.get("rate_before"), "rate_after": fr.get("rate_after"),
            "multiplier": fr.get("multiplier", 1.0), "floor_applied": bool(fr.get("floor_applied")),
            "how": how, "alternatives": alts,
            "warning": t(w["code"], lang, **_fmt_params(w.get("params"), lang)) if w else None,
            "calibrated": ae.CALIBRATED}


def _scenarios_view(sc: Optional[dict], must: dict, lang: str) -> dict:
    """Строки раздела 4, абзац с определениями, списки «как посчитано» и JSON блока scenarios."""
    sc = sc or {"available": False, "reason": "sc_na_error", "class_code": must.get("class_code")}
    # порядок заказчика PML ≤ EML ≤ MFL (с 29.09.2026); старые сохранённые акты — прежние тексты
    classic = sc.get("order") == ax.SCENARIO_ORDER
    defs = t("sc_defs_classic" if classic else "sc_defs", lang)
    js = {"available": bool(sc.get("available")), "class_code": sc.get("class_code"), "rule": sc.get("rule"),
          "order": sc.get("order") or "legacy", "pml": None, "eml": None, "mfl": None, "tiles": [],
          "retention": None, "how": [], "assumptions": [], "note": None, "definitions": defs,
          "calibrated": ae.CALIBRATED}
    rows, lists = [], []
    js["assumptions"] = [{"code": a["code"], "text": _assumption_text(a, lang),
                          "note": "принято по умолчанию" if lang == "ru" else t("sc_assumptions_title", lang)}
                         for a in sc.get("assumptions") or []]
    if not sc.get("available"):
        note = t(sc.get("reason") or "sc_na_error", lang)
        js["note"] = note
        rows.append(_row("PML / EML / MFL", t("sc_na", lang), note))
        if js["assumptions"]:
            lists.append({"title": t("sc_assumptions_title", lang), "items": [a["text"] for a in js["assumptions"]]})
        return {"rows": rows, "lists": lists, "paragraph": defs, "json": js}
    # проценты трёх сценариев — с одинаковым числом знаков: все целые — без дроби, иначе один знак
    pcts = [sc["items"][s]["pct"] for s in ("PML", "EML", "MFL")]
    digits = 0 if all(p is not None and float(p) == int(p) for p in pcts) else 1
    for s, lab in (("PML", "sc_pml"), ("EML", "sc_eml"), ("MFL", "sc_mfl")):
        it = sc["items"][s]
        what = _scenario_what(it, lang)
        pct_text = tx.pct_fixed(it["pct"], lang, digits)
        js[s.lower()] = {"amount": round(it["amount"]), "pct": it["pct"], "pct_text": pct_text, "what": what,
                         "what_code": it["what"], "state_code": it.get("state"),
                         "source_scenario": it.get("source_scenario"),
                         # формула собрана модулем по-русски — на другом языке акта её не отдаём
                         "formula": it.get("formula") if lang == "ru" else None, "calibrated": ae.CALIBRATED}
        js["tiles"].append({"code": s.lower(), "name": s, "label": t(lab, lang), "amount": round(it["amount"]),
                            "pct": it["pct"], "pct_text": pct_text, "what": what})
        rows.append(_row(t(lab, lang), t("sc_value", lang, amount=money(it["amount"], lang), pct=pct_text), what))
    ret = sc.get("retention") or {}
    tail = None
    if ret.get("known"):
        basis = t("sc_ret_basis", lang, own=money(ret.get("own_funds"), lang), res=money(ret.get("reserves"), lang),
                  limit=money(ret.get("limit_per_risk"), lang), cls=ret.get("line_class") or sc.get("class_code"),
                  line=_money_na(ret.get("line_retention"), lang),
                  ret=money(ret.get("limit"), lang))
        status = t("sc_ret_temporary" if ret.get("status") == "temporary" else "sc_ret_reported", lang)
        ex = ret.get("mfl_excess")
        if ret.get("compared_with") == "eml":
            ee = ret.get("eml_excess")
            tail = (t("sc_ret_eml_excess", lang, x=money(ee, lang)) if ee else t("sc_ret_eml_within", lang)) + "; " + \
                (t("sc_ret_mfl_excess", lang, x=money(ex, lang)) if ex else t("sc_ret_mfl_within", lang))
        else:
            tail = t("sc_ret_excess", lang, x=money(ex, lang)) if ex else t("sc_ret_within", lang)
        rows.append(_row(t("sc_retention", lang), money(ret.get("limit"), lang), status + "; " + tail))
    else:
        basis = t("sc_ret_unknown", lang)
        rows.append(_row(t("sc_retention", lang), t("sc_ret_unknown", lang)))
    legal = ret.get("legal_ref")
    js["retention"] = {"limit": ret.get("limit"), "known": bool(ret.get("known")), "basis": basis,
                       "status": ret.get("status"), "limit_per_risk": ret.get("limit_per_risk"),
                       "line_retention": ret.get("line_retention"), "compared_with": ret.get("compared_with"),
                       "eml_excess": ret.get("eml_excess"), "within": ret.get("within"),
                       "mfl_excess": ret.get("mfl_excess"), "text": tail,
                       "legal_ref": tx.label(tx.LEGAL_REFS, legal, lang) if legal else None}
    if sc.get("source") == "template":
        simple = sc.get("rule_simple") or {}
        how = [t("sc_how_source_tpl", lang, cls=sc.get("class_code"), rule=simple.get(lang) or simple.get("ru") or "")]
        if sc.get("rule_text"):
            how.append(t("sc_tpl_rule_text", lang, text=sc["rule_text"].get(lang) or sc["rule_text"].get("ru")))
        for c in sc.get("checks") or []:
            # площадь, урожайность и процент (урожай, 16у) — числа, а не суммы
            how.append(t(c["code"], lang, **{k: (_mult(v, lang) if k in ("area", "yield", "pct") else money(v, lang))
                                             if isinstance(v, (int, float)) and k != "by" else v
                                             for k, v in (c.get("params") or {}).items()}))
        js.update(source="template", rule_text=(sc.get("rule_text") or {}).get(lang),
                  checks=[{"code": c["code"], "params": c.get("params")} for c in sc.get("checks") or []])
    elif sc.get("source") == "parts":
        how = _parts_sc_how(sc, lang)
    elif sc.get("source") == "objects":
        how = _objects_sc_how(sc, lang)
    else:
        how = [t("sc_how_source", lang, cls=sc.get("class_code"), rule=t("sc_rule_" + sc["rule"], lang))]
    if classic and sc.get("source") != "template":
        how.append(t("sc_how_names", lang))
    if sc["rule"] != "vehicle" and (sc.get("k") or 1) < 1:
        how.append(t("sc_how_k", lang, k=_mult(sc["k"], lang)))
    how.append(basis)
    js["how"] = how
    lists.append({"title": t("sc_how_title", lang), "items": how})
    if js["assumptions"]:
        lists.append({"title": t("sc_assumptions_title", lang), "items": [a["text"] for a in js["assumptions"]]})
    return {"rows": rows, "lists": lists, "paragraph": defs, "json": js}


def _scenario_what(it: dict, lang: str) -> str:
    """Подпись сценария: состояние защиты (у имущества) и причина — ровно то, что посчитано."""
    if it.get("what_text"):                  # простое правило шаблона класса: подпись из шаблона на языке акта
        return it["what_text"].get(lang) or it["what_text"].get("ru") or ""
    what = t(it["what"], lang, **(it.get("what_params") or {}))
    if it.get("state"):
        what = t(it["state"], lang) + ": " + what
    return what


def _assumption_text(a: dict, lang: str) -> str:
    p = dict(a.get("params") or {})
    v = p.get("value")
    if a["code"] == "as_object_type":
        p["value"] = _otype_label(v, lang) if v else t("na", lang)
    elif v is not None and a["code"] in ("as_construction", "as_activity", "as_vehicle_type", "as_activity_text",
                                         "as_activity_by_type"):
        p["value"] = tx.label(tx.RA_VALUE_LABELS, v, lang)
    if a["code"] == "as_activity_by_type":
        p["type"] = _otype_label(p.get("type"), lang) if p.get("type") else t("na", lang)
    if a["code"] in ("as_tpl_limit_case_over", "as_tpl_limit_aggregate_over"):
        p = {k: money(v, lang) for k, v in p.items()}
    return t(a["code"], lang, **p)


def _measures_view(ms: Optional[dict], lang: str) -> dict:
    """Рекомендации страхователю: строки для раздела 5 и JSON (measures[], measures_summary)."""
    ms = ms or {"items": [], "total": {"count": 0}}
    out = []
    for it in ms.get("items") or []:
        text = (it.get("text") or {}).get(lang) or (it.get("text") or {}).get("ru") or ""
        why = (it.get("why") or {}).get(lang) or (it.get("why") or {}).get("ru") or ""
        eff = None
        if it.get("effect_pct") is not None:
            eff = ("−" if it["effect_pct"] < 0 else "+") + pct(abs(it["effect_pct"]), lang, 1)
        if it.get("premium_delta"):
            effect = t("ms_effect", lang, effect=eff, delta=_signed(it["premium_delta"], lang))
        elif (ms.get("total") or {}).get("statutory"):
            effect = t("ms_effect_statutory", lang)
        elif it.get("effect_pct") is not None and it.get("premium_delta") == 0:
            effect = t("ms_effect_zero", lang)
        elif it.get("effect_pct") is not None:
            effect = t("ms_effect_pct", lang, effect=eff)
        else:
            effect = t("ms_effect_na", lang)
        deadline = t("ms_deadline_default" if it.get("deadline_default") else "ms_deadline", lang,
                     n=it.get("deadline_days"))
        line = t("ms_item", lang, text=text.rstrip("."), why=why if why.endswith(".") else why + ".",
                 deadline=deadline.rstrip("."), mandatory=t("ms_mandatory", lang) if it.get("mandatory") else "",
                 effect=effect)
        out.append({"line": line, "json": {
            "code": it["code"], "text": text, "why": why, "effect_pct": it.get("effect_pct"),
            "premium_delta": it.get("premium_delta"), "mandatory": bool(it.get("mandatory")),
            "deadline_days": it.get("deadline_days"), "deadline_default": bool(it.get("deadline_default")),
            "legal_ref": it.get("legal_ref"), "source": it.get("source"), "effect_text": effect,
            "calibrated": ae.CALIBRATED}})
    tot = ms.get("total") or {}
    summary = {"count": tot.get("count", 0), "with_effect": tot.get("with_effect", 0),
               "premium_before": tot.get("premium_before"), "premium_after": tot.get("premium_after"),
               "delta": tot.get("delta"), "floor_applied": bool(tot.get("floor_applied")), "text": None,
               "calibrated": ae.CALIBRATED}
    if tot.get("delta"):
        summary["text"] = t("ms_total", lang, after=money(tot["premium_after"], lang),
                            before=money(tot["premium_before"], lang))
        if tot.get("floor_applied"):
            summary["text"] += " " + t("ms_total_floor", lang)
    return {"items": out, "summary": summary}


def _fr_text(fr: dict, lang: str) -> str:
    status = fr.get("status")
    if status == "applied":
        params = dict(type=tx.label(tx.FR_TYPE_LABELS, fr.get("type") or "unconditional", lang),
                      pct=pct(fr.get("size_pct"), lang), amount=money(fr.get("size_amount"), lang))
        if _fr_effect_done(fr):
            text = t("fr_applied", lang, before=money(fr.get("premium_before"), lang),
                     after=money(fr.get("premium_after"), lang), **params)
            text += _fr_effect_notes(fr, lang)
        else:
            text = t("fr_applied_na", lang, **params)
        w = fr.get("warning")
        if w:
            text += " " + t(w["code"], lang, **_fmt_params(w.get("params"), lang))
        return text
    if status == "statutory" and fr.get("warning"):
        return t("fr_statutory", lang) + ". " + t("fr_w_statutory", lang)
    base = _fr_text_base(fr, lang)
    if status == "proposed" and fr.get("size_pct"):
        params = dict(type=tx.label(tx.FR_TYPE_LABELS, fr.get("type") or "unconditional", lang),
                      pct=pct(fr["size_pct"], lang), amount=money(fr.get("size_amount"), lang))
        if _fr_effect_done(fr):
            base += t("fr_proposed_tail", lang, before=money(fr.get("premium_before"), lang),
                      after=money(fr.get("premium_after"), lang), **params)
            base += _fr_effect_notes(fr, lang)
        else:
            base += t("fr_proposed_tail_na", lang, **params)
    return base


def _fr_effect_notes(fr: dict, lang: str) -> str:
    """Пояснения к премии с франшизой (и применённой, и предложенной): упёрлась в минимум, множитель выше 2 %."""
    out = ""
    if fr.get("floor_applied"):
        out += " " + t("fr_floor_note", lang)
    if (fr.get("engine") or {}).get("extrapolated"):
        out += " " + t("fr_extrapolated_note", lang)
    return out


def _fr_effect_done(fr: dict) -> bool:
    """Эффект франшизы на премию акта посчитан (есть шаг «ставка акта × множитель»)."""
    return any(h.get("code") == "frh_apply" for h in fr.get("how") or [])


def _fr_text_base(fr: dict, lang: str) -> str:
    code = fr.get("code") or "fr_not_needed"
    if code == "fr_advise_range":
        s = fr["size"]
        if s["from_pct"] == s["to_pct"]:           # вилка с равными границами — одно число, а не «от 5 % до 5 %»
            rng = pct(s["to_pct"], lang)
            amount = money(s["to_amount"], lang)
        elif s["from_pct"] <= 0:
            rng = t("fr_range_upto", lang, to=pct(s["to_pct"], lang))
            amount = t("fr_range_upto", lang, to=money(s["to_amount"], lang))
        else:
            rng = t("fr_range", lang, from_=pct(s["from_pct"], lang), to=pct(s["to_pct"], lang))
            amount = t("fr_range", lang, from_=money(s["from_amount"], lang), to=money(s["to_amount"], lang))
        return t("fr_advise_range", lang, range=rng, amount=amount)
    return t(code, lang)
