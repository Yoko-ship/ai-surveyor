"""Факторы объекта по подгруппам класса на языке акта: строки, вклад в премию, фон региона (stat.uz)."""
from typing import Optional

from .. import act_engine as ae
from .. import class_templates as ctpl
from .. import act_texts as tx
from .. import i18n
from ..act_texts import money, pct, t

from .regions import _fold, region_code
from .view_fmt import _CYR, _mult, _smoney
from .view_rows import _row


def _fa_one(fa: dict, lang: str) -> dict:
    """
    Факторы объекта одного класса (act_engine.factor_adjust с effect) на языке акта: {"json", "lines", "row",
    "s5", "unfilled"}. Строки — построчно: база, каждый фактор с коэффициентом, ставкой после него и вкладом в премию
    в сумах, границы множителя и минимум (если сработали), итог, режим, что уточнить.
    """
    L = lambda x: ctpl.localize(x, lang) if x is not None else None  # noqa: E731
    eff = fa.get("effect") or {}
    steps = {x["group"]: x for x in eff.get("steps") or [] if x.get("kind") == "factor"}
    applied, lines = [], [t("fa_head", lang, n=len(fa.get("applied") or []), total=fa.get("groups") or 0)]
    stat_rows = []
    if eff.get("available"):
        lines.append(t("fa_base_" + (eff.get("base") or "act"), lang, rate=pct(eff["base_pct"], lang)))
    for a in fa.get("applied") or []:
        stp = steps.get(a["group"]) if eff.get("available") else None
        g, o = L(a.get("group_label")) or a["group"], L(a.get("label")) or a["option"]
        note = _loc_text(a.get("note"), lang, "fa_note_expert")
        kw = {"group": g, "option": o, "coef": _mult(a["coef"], lang), "note": note}
        lines.append(t("fa_line_sum", lang, rate=pct(stp["rate_pct"], lang), delta=_smoney(stp["premium_delta"], lang),
                       **kw) if stp else t("fa_line", lang, **kw))
        item = {"group": a["group"], "group_label": g, "option": a["option"], "label": o, "coef": a["coef"],
                "note": note, "rate_pct": stp["rate_pct"] if stp else None,
                "premium_delta": stp["premium_delta"] if stp else None}
        if a.get("stat_ref"):
            # фон региона (stat.uz) к фактору: блок stat из данных акта (на дату формирования), строка explain и
            # строка раздела 4; акт до 02.10.2026 без блока — available = false, reason = old_act
            sb = _fa_stat_local(a.get("stat") or {"available": False, "reason": "old_act",
                                                  "reason_text": "акт сформирован до подключения фона stat.uz",
                                                  "note": ae.STAT_NOTE, "calibrated": ae.CALIBRATED}, lang)
            item["stat"] = sb
            sl = _fa_stat_line(sb, g, lang)
            lines.append(sl)
            stat_rows.append((t("fa_stat_row", lang, group=g), sl))
        applied.append(item)
    for x in eff.get("steps") or []:
        if x.get("kind") == "bound":
            lo, hi = fa.get("bounds") or [None, None]
            lines.append(t("fa_bound", lang, raw=_mult(fa.get("raw_product") or 0, lang), lo=_mult(lo, lang),
                           hi=_mult(hi, lang), mult=_mult(fa["product"], lang), rate=pct(x["rate_pct"], lang),
                           delta=_smoney(x["premium_delta"], lang)))
        elif x.get("kind") == "min":
            lines.append(t("fa_min", lang, min=pct(x["rate_pct"], lang), delta=_smoney(x["premium_delta"], lang)))
    reason = eff.get("reason")
    if eff.get("available"):
        lines.append(t("fa_total", lang, mult=_mult(fa["product"], lang), base=pct(eff["base_pct"], lang),
                       rate=pct(eff["rate_pct"], lang), p0=money(eff["base_premium"], lang),
                       p1=money(eff["premium"], lang), delta=_smoney(eff["delta_premium"], lang)))
    elif reason in ("statutory", "no_rate"):
        lines.append(t("fa_" + reason, lang))
    elif not fa.get("applied"):
        lines.append(t("fa_none", lang))
    mode = fa.get("mode") or "reference"
    if reason != "statutory":
        lines.append(t("fa_mode_" + mode, lang))
    unfilled = [{"group": u["group"], "label": L(u.get("label")) or u["group"]} for u in fa.get("unfilled") or []]
    if unfilled:
        lines.append(t("fa_unfilled", lang, items=", ".join(u["label"] for u in unfilled)))
    # строка раздела 4: факторы, множитель, режим
    if applied:
        value = t("fa_row_value", lang, items="; ".join(f"{a['label']} × {_mult(a['coef'], lang)}" for a in applied),
                  mult=_mult(fa["product"], lang))
    else:
        value = t("fa_row_none", lang)
    if reason in ("statutory", "no_rate"):
        note = t("fa_row_note_" + reason, lang)
    elif eff.get("available") and eff.get("applied_to_act"):
        note = t("fa_row_note_apply", lang)
    elif eff.get("available"):
        note = t("fa_row_note_reference", lang, rate=pct(eff["rate_pct"], lang), premium=money(eff["premium"], lang))
    else:
        note = None
    s5 = []
    if eff.get("available"):
        s5.append(t("fa_s5_apply", lang, mult=_mult(fa["product"], lang)) if eff.get("applied_to_act")
                  else t("fa_s5_reference", lang, rate=pct(eff["rate_pct"], lang), premium=money(eff["premium"], lang)))
    js = {"available": True, "mode": mode, "product": fa.get("product"), "raw_product": fa.get("raw_product"),
          "clamped": fa.get("clamped"), "bounds": fa.get("bounds"), "groups": fa.get("groups"), "applied": applied,
          "unfilled": unfilled,
          "effect": {k: eff.get(k) for k in ("available", "reason", "applied_to_act", "base", "base_pct",
                                             "base_premium", "rate_pct", "premium", "delta_premium", "floored",
                                             "min_pct", "rate_type", "term_days", "steps")},
          "explain": lines, "calibrated": ae.CALIBRATED}
    return {"json": js, "lines": lines, "row": (value, note), "s5": s5, "unfilled": [u["label"] for u in unfilled],
            "stat_rows": stat_rows}


def _loc_text(x, lang: str, fallback_key: Optional[str] = None) -> str:
    """Подпись {ru, uz, en} или строка шаблона на языке акта. Строка только по-русски в акте на узбекском или
    английском не печатается: вместо неё — общая подпись fallback_key (или пусто)."""
    if x is None:
        return ""
    if isinstance(x, dict):
        x = ctpl.localize(x, lang) if x.get(tx.lang_of(lang)) else x.get("ru")
    s = str(x or "")
    if tx.lang_of(lang) != "ru" and _CYR.search(s):
        return t(fallback_key, lang) if fallback_key else ""
    return s


def _region_name_local(name: Optional[str], lang: str) -> str:
    """Название региона из справочника (по-русски) → на языке акта через словарь регионов; не нашлось — как есть."""
    if not name or tx.lang_of(lang) == "ru":
        return name or ""
    code = region_code(name)
    if code:
        got = i18n.t(f"tg.act.reg.{code}", lang)
        if got and not got.startswith("tg.act."):
            return got
    low = _fold(name)
    if "республика узбекистан" in low or low == "узбекистан":
        return i18n.t("tg.act.reg.uz_all", lang)
    return name


def _fa_stat_local(sb: dict, lang: str) -> dict:
    """Блок stat фактора на языке акта: причина, пометка и формула — из act_texts, регион — по словарю."""
    out = dict(sb)
    code = sb.get("reason")
    if code and "fa_stat_r_" + code in tx.TX:
        out["reason_text"] = t("fa_stat_r_" + code, lang)
    elif sb.get("reason_text"):
        out["reason_text"] = _loc_text(sb["reason_text"], lang)
    out["note"] = t("fa_stat_note", lang)
    if sb.get("formula"):
        out["formula"] = t("fa_stat_formula_" + (sb.get("kind") or "value"), lang)
    if sb.get("region_name"):
        out["region_name"] = _region_name_local(sb["region_name"], lang)
    if tx.lang_of(lang) != "ru" and sb.get("name") and _CYR.search(str(sb["name"])):
        out["name"] = "; ".join(str(x) for x in sb.get("dataset") or []) or None
    return out


def _fa_stat_line(sb: dict, group: str, lang: str) -> str:
    """Строка фона региона к фактору: доля материала стен в жилищном фонде или показатель (газ) со ссылкой."""
    if not sb.get("available"):
        return t("fa_stat_none", lang, group=group, reason=sb.get("reason_text") or sb.get("reason") or "—")
    what = "; ".join(x for x in (_loc_text(z, lang) for z in sb.get("labels") or []) if x) or sb.get("name") or ""
    kw = {"region": _region_name_local(sb.get("region_name"), lang) or sb.get("region") or "",
          "period": sb.get("period"), "what": what,
          "share": tx.pct_fixed(sb.get("share_pct"), lang, 1), "url": sb.get("url") or ""}
    return t("fa_stat_walls" if sb.get("kind") == "walls_share" else "fa_stat_value", lang, **kw)


def _factor_view(D: dict, lang: str) -> dict:
    """
    Блок factor_adjustment ответа, строки раздела 4, фраза раздела 5 и перечень «уточнить». Акты до 02.10.2026 —
    без блока (available = false, reason = old_act); шаблон без групп факторов — пусто.
    """
    FA = D.get("factor_adjustment")
    empty = {"json": {"available": False, "reason": "old_act", "calibrated": ae.CALIBRATED}, "rows": [], "lines": [],
             "s5": [], "unfilled": []}
    if not FA:
        return empty
    if FA.get("by_objects"):
        objs, rows, lines, s5, unf = [], [], [], [], []
        for of in FA.get("objects") or []:
            if not of.get("groups"):
                continue
            one = _fa_one(of, lang)
            n, lab = of.get("index"), of.get("label") or ""
            objs.append(dict(one["json"], index=n, label=lab))
            rows.append(_row(t("fa_row_obj", lang, n=n, label=lab), one["row"][0], one["row"][1]))
            lines += [t("fa_obj", lang, n=n, text=x) for x in one["lines"]]
            s5 += [t("fa_obj", lang, n=n, text=x) for x in one["s5"]]
            unf += [t("fa_obj", lang, n=n, text=x) for x in one["unfilled"]]
        return {"json": {"available": bool(objs), "by_objects": True, "mode": FA.get("mode"), "objects": objs,
                         "explain": lines, "calibrated": ae.CALIBRATED},
                "rows": rows, "lines": lines, "s5": s5, "unfilled": unf}
    if FA.get("by_parts"):
        parts, rows, lines, s5, unf = [], [], [], [], []
        for pf in FA.get("parts") or []:
            if not pf.get("groups"):
                continue
            one = _fa_one(pf, lang)
            n, cls = pf.get("index"), pf.get("class_code")
            parts.append(dict(one["json"], index=n, class_code=cls))
            rows.append(_row(t("fa_row_part", lang, n=n), one["row"][0], one["row"][1]))
            rows += [_row(t("fa_part", lang, n=n, cls=cls, text=lb), v) for lb, v in one["stat_rows"]]
            lines += [t("fa_part", lang, n=n, cls=cls, text=x) for x in one["lines"]]
            s5 += [t("fa_part", lang, n=n, cls=cls, text=x) for x in one["s5"]]
            unf += [t("fa_part", lang, n=n, cls=cls, text=x) for x in one["unfilled"]]
        return {"json": {"available": bool(parts), "by_parts": True, "mode": FA.get("mode"), "parts": parts,
                         "explain": lines, "calibrated": ae.CALIBRATED},
                "rows": rows, "lines": lines, "s5": s5, "unfilled": unf}
    if not FA.get("groups"):
        return dict(empty, json={"available": False, "reason": "no_groups", "mode": FA.get("mode"),
                                 "calibrated": ae.CALIBRATED})
    one = _fa_one(FA, lang)
    return {"json": one["json"], "rows": [_row(t("fa_row", lang), one["row"][0], one["row"][1])] +
            [_row(lb, v) for lb, v in one["stat_rows"]],
            "lines": one["lines"], "s5": one["s5"], "unfilled": one["unfilled"]}
