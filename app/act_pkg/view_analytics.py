"""Раздел 4 «Аналитика риска»: шаги по разделам аналитики, строки НАПП, резюме, факторы объекта."""
from typing import Optional

from .. import act_analytics as aa
from .. import act_engine as ae
from .. import market_picture as mpic
from .. import act_texts as tx
from ..act_texts import money, pct, t

from .regions import region_label
from .view_fmt import _cap, _date, _li, _mult, _pct_na, _smoney, _spct, _spp
from .documents import _risk_view
from .view_rows import _class_label, _otype_label, _sc_part_local
from .view_fork import _class_rows_text, _fy_year, _pack_choice_text, _pc_local
from .view_blocks import _fr_text, _measures_view


def _flabel(f: str, lang: str, ru: Optional[str] = None) -> str:
    if f in tx.FACTOR_LABELS:
        return tx.label(tx.FACTOR_LABELS, f, lang)
    return ru if (lang == "ru" and ru) else f


def _olabel(f: str, o, lang: str, ru: Optional[str] = None) -> str:
    if o is None:
        return t("an_f_not_set", lang)
    key = f"{f}:{o}"
    if key in tx.OPTION_LABELS:
        return tx.label(tx.OPTION_LABELS, key, lang)
    return ru if (lang == "ru" and ru) else str(o)


def _fo(f: str, o, lang: str) -> str:
    """«фактор: значение» строчными — для перечней в тексте."""
    return f"{_flabel(f, lang).lower()}: {_olabel(f, o, lang)}"


def _peril_label(code: str, lang: str, ru: Optional[str], cls: Optional[str]) -> str:
    if code.startswith("class"):
        return _class_label(code[5:], ru, lang)
    if code in tx.PERIL_LABELS:
        return tx.label(tx.PERIL_LABELS, code, lang)
    return ru if lang == "ru" and ru else code


def _np_src(src: Optional[dict], lang: str) -> tuple:
    """(строка «Источник: …», запись для плашки) для данных НАПП о претензиях и подразделениях."""
    if not src:
        return None, None
    file = ""
    if lang == "ru" and "(" in (src.get("title") or ""):
        file = " (" + src["title"].split("(", 1)[1]
        file = file if file.endswith(")") else file + ")"
    line = t("an_np_src", lang, sheets=src.get("sheets") or "", file=file, date=_date(src.get("as_of")),
             url=src.get("url") or "")
    title = t("an_np_src_title", lang, sheets=src.get("sheets") or "")
    return line, {"title": title, "url": src.get("url"), "source": src.get("domain") or "napp.uz",
                  "period": _date(src.get("as_of")), "fetched_at": None}


def _np_skip(B: dict, lang: str, sheet: str) -> str:
    """Почему показатель НАПП пропущен — на языке акта."""
    r = B.get("reason")
    if r in ("no_sheet", "no_table"):
        return t("an_np_no_sheet", lang, date=_date(B.get("date")) or t("na", lang), sheet=sheet)
    if r == "no_region_row":
        return t("an_np_no_region_row", lang, date=_date(B.get("date")), region=B.get("region_name") or "")
    if r == "no_contracts":
        return t("an_np_no_contracts", lang, date=_date(B.get("date")))
    if r == "no_company":
        return t("an_np_no_company", lang, date=_date(B.get("date")))
    return t("na", lang)


def _napp_items(NP: dict, lang: str, must: dict) -> list:
    """Строки раздела 4 по претензиям и подразделениям (НАПП) в форме analytics.stats.indicators."""
    if not NP.get("available"):
        return []
    # таблиц ещё нет (база до первой загрузки новых листов) — строк нет вовсе, без ложного «листа нет»
    NP = {k: (v if not (isinstance(v, dict) and v.get("reason") == "no_table") else {}) for k, v in NP.items()}
    lang = tx.lang_of(lang)
    out = []
    reg_name = region_label(must, lang) or ""

    def n2(x):
        return tx._num(float(x), lang, 2) if x is not None else t("na", lang)

    def p1(x):
        return tx.pct_fixed(x, lang, 1) if x is not None else t("na", lang)

    def m0(x):
        return money(x, lang) if x is not None else t("na", lang)

    cw = NP.get("claims_weight", 0.0)
    min_n = NP.get("branch_min_contracts", 200)

    def caveats(B):
        """Оговорки к строкам претензий на языке акта (доля города Ташкента — по срезу)."""
        return t("an_np_caveats", lang, text="; ".join(mpic.claims_caveats(lang, B.get("capital_share_pct"))))

    def item(iid, name, status, value_text, text, scope, B, vs=None, row_note=""):
        line, sx = _np_src(B.get("source"), lang)
        return {"id": iid, "name": name, "scope": scope, "status": status,
                "value": None, "value_text": value_text, "period": _date(B.get("date")) or None,
                "vs_country": vs, "used_in_score": False, "excluded_for_kind": False, "points": None,
                "text": text, "source_lines": [line] if line else [], "sources": [sx] if sx else [],
                "row_note": row_note or (_date(B.get("date")) or "—"), "napp": True, "calibrated": ae.CALIBRATED}

    # 1. претензии в регионе против республики (листы 3.5, 3.4, 3.2)
    RC = NP.get("region_claims") or {}
    reg, rep_ = RC.get("region") or {}, RC.get("republic") or {}
    months = RC.get("months") or "—"
    # строка справочная: в поправку ставки по умолчанию не входит (вес claims_freq = 0, замечание 01.10.2026)
    share = RC.get("capital_share_pct")
    if cw:
        ref = t("an_np_rc_in_fork", lang, w=tx._num(float(cw), lang, 1 if float(cw) != int(cw) else 0))
    else:
        ref = t("an_np_rc_ref", lang, share=tx.pct_fixed(share, lang, 0)) if share is not None \
            else t("an_np_rc_ref_na", lang)
    if RC.get("available"):
        text = t("an_np_rc_line", lang, f=n2(reg["per_1000"]), cf=n2(rep_["per_1000"]), r=p1(reg["refused_pct"]),
                 cr=p1(rep_["refused_pct"]), a=m0(reg["avg_payout"]), ca=m0(rep_["avg_payout"]),
                 n=tx._num(float(reg["received"]), lang, 0), date=_date(RC["date"]), m=months)
        it = item("napp_region_claims", t("an_np_rc_name", lang), "ok",
                  t("an_np_per1000", lang, f=n2(reg["per_1000"])), text + ". " + _cap(ref) + ". " + caveats(RC), "region",
                  RC, {"ratio": RC["ratio"], "diff_pct": RC["diff_pct"]},
                  f"{_date(RC['date'])}; {t('an_st_region', lang)}: {reg_name}; {ref}")
        it.update(reference_only=not cw, in_fork_weight=cw, note=ref,
                  caveats=mpic.claims_caveats(lang, RC.get("capital_share_pct")))
        out.append(it)
    elif rep_ and RC.get("reason") in ("no_region", "region_unknown"):
        text = t("an_np_rc_rep_line", lang, cf=n2(rep_["per_1000"]), cr=p1(rep_["refused_pct"]),
                 ca=m0(rep_["avg_payout"]), date=_date(RC["date"]), m=months)
        it = item("napp_region_claims", t("an_np_rc_name_rep", lang), "ok",
                  t("an_np_per1000", lang, f=n2(rep_["per_1000"])), text + ". " + caveats(RC), "republic", RC)
        it.update(reference_only=True, caveats=mpic.claims_caveats(lang, RC.get("capital_share_pct")))
        out.append(it)
    elif RC.get("reason"):
        why = _np_skip(RC, lang, "3.5")
        out.append(item("napp_region_claims", t("an_np_rc_name", lang), "no_data", why,
                        t("an_np_skip_line", lang, name=t("an_np_rc_name", lang), why=why), "region", RC))
    # 2. претензии: рынок в целом / INSON (листы 2.10, 2.7, 2.5)
    CC = NP.get("company_claims") or {}
    mk_, co = CC.get("market") or {}, CC.get("company") or {}
    if CC.get("available"):
        text = t("an_np_cc_line", lang, mn=tx._num(float(mk_["received"]), lang, 0), mf=n2(mk_["per_1000"]),
                 mr=p1(mk_["refused_pct"]), ma=m0(mk_["avg_payout"]), cn=tx._num(float(co["received"]), lang, 0),
                 cf=n2(co["per_1000"]), cr=p1(co["refused_pct"]), ca=m0(co["avg_payout"]), date=_date(CC["date"]),
                 m=CC.get("months") or "—")
        it = item("napp_company_claims", t("an_np_cc_name", lang), "ok",
                  t("an_np_cc_value", lang, cf=n2(co["per_1000"]), mf=n2(mk_["per_1000"])),
                  text + ". " + caveats(CC), "republic", CC)
        it.update(reference_only=True, caveats=mpic.claims_caveats(lang, CC.get("capital_share_pct")))
        out.append(it)
    elif CC.get("reason"):
        why = _np_skip(CC, lang, "2.10")
        out.append(item("napp_company_claims", t("an_np_cc_name", lang), "no_data", why,
                        t("an_np_skip_line", lang, name=t("an_np_cc_name", lang), why=why), "republic", CC))
    # 3. подразделения INSON в регионе (листы 2.12–2.14)
    BR = NP.get("branches") or {}
    if BR.get("available"):
        r, c = BR["region"], BR["company"]
        text = t("an_np_br_line", lang, lr=p1(r["loss_ratio_pct"]), clr=p1(c["loss_ratio_pct"]),
                 ap=m0(r["avg_premium"]), cap=m0(c["avg_premium"]), p=tx._num(float(r["premiums_mln"]), lang, 1),
                 n=tx._num(float(r["contracts"] or 0), lang, 0), date=_date(BR["date"]))
        small = r.get("contracts") is not None and min_n and float(r["contracts"]) < float(min_n)
        sm = t("an_np_br_small", lang, n=tx._num(float(r["contracts"] or 0), lang, 0),
               min=tx._num(float(min_n), lang, 0)) if small else ""
        it = item("napp_branches", t("an_np_br_name", lang), "ok",
                  t("an_np_br_value", lang, lr=p1(r["loss_ratio_pct"]), clr=p1(c["loss_ratio_pct"])),
                  text + (". " + _cap(sm) if sm else ""), "region", BR, None,
                  f"{_date(BR['date'])}; {t('an_st_region', lang)}: {reg_name}" + (f"; {sm}" if sm else ""))
        it.update(small_base=bool(small), min_contracts=min_n)
        out.append(it)
    elif BR.get("reason") == "not_listed":
        why = t("an_np_br_not_listed_reg" if BR.get("company") else "an_np_br_not_listed", lang, region=reg_name,
                company=BR.get("company_label") or "INSON")
        out.append(item("napp_branches", t("an_np_br_name", lang), "no_data", why,
                        t("an_np_skip_line", lang, name=t("an_np_br_name", lang), why=why), "region", BR))
    elif BR.get("reason") == "no_sheet":
        why = _np_skip(BR, lang, "2.12–2.14")
        out.append(item("napp_branches", t("an_np_br_name", lang), "no_data", why,
                        t("an_np_skip_line", lang, name=t("an_np_br_name", lang), why=why), "region", BR))
    return out


def _pack_text(mk: dict, lang: str) -> str:
    """Классы строки-пакета НАПП: «8 и 9» / «8 va 9» / «8 and 9» (по ключу строки market_stats)."""
    cl = [str(x) for x in mk.get("pack_classes") or []] or ["8", "9"]
    conj = {"ru": " и ", "uz": " va ", "en": " and "}[tx.lang_of(lang)]
    return ", ".join(cl[:-1]) + conj + cl[-1] if len(cl) > 1 else cl[0]


def _dmark(x: dict, lang: str, by_source: bool = False) -> str:
    """Пометка «по умолчанию» у фактора, значение которого не введено, а принято по умолчанию."""
    on = x.get("source") == "default" if by_source else bool(x.get("assumed"))
    return ", " + t("an_default_mark", lang) if on else ""


def _pts(x, lang: str) -> str:
    """Баллы с нужной формой слова: 24 балла / 24 ball / 24 points."""
    return tx.count_text(round(float(x or 0)), "points", lang)


def _analytics_view(D: dict, lang: str) -> dict:
    """Аналитика раздела 4 на языке акта: резюме, списки с таблицами и JSON analytics для экрана."""
    A = D.get("analytics")
    if not A:                               # акты до 30.09.2026 (вечер) — без аналитики
        return {"summary": None, "lists": [], "json": {"available": False, "reason": "old_act",
                                                       "calibrated": ae.CALIBRATED}}
    if not A.get("available"):
        return {"summary": None, "lists": [{"title": t("an_tariff_title", lang), "items": [t("an_na", lang)]}],
                "json": {"available": False, "reason": A.get("reason") or "no_engine", "text": t("an_na", lang),
                         "calibrated": ae.CALIBRATED}}
    js = {"available": True, "reason": None, "calibrated": ae.CALIBRATED, "sources": []}
    lists = []
    # разделы — по порядку показа: каждый дописывает свой блок в js и свой список в lists
    for step in (_an_risks, _an_factors, _an_sensitivity, _an_tariff, _an_scenarios, _an_retention, _an_score,
                 _an_market, _an_franchise, _an_measures):
        step(D, A, lang, js, lists)

    # ---------- 9. резюме ----------
    sentences = _an_summary(D, A, js, lang)
    js["summary"] = {"text": " ".join(sentences), "sentences": sentences}
    return {"summary": t("an_summary", lang, text=" ".join(sentences)) if sentences else None, "lists": lists,
            "json": js}


def _an_src_add(js: dict, title, url, domain, as_of) -> None:
    """Источник в js["sources"] без повторов (тот же адрес и заголовок)."""
    for s in js["sources"]:
        if s["url"] == url and s["title"] == title:
            return
    js["sources"].append({"title": title, "url": url, "domain": domain, "as_of": as_of})


def _an_risks(D: dict, A: dict, lang: str, js: dict, lists: list) -> None:
    """Раздел «Риски»: доли рисков в нетто-ставке, причины уровня, примечания."""
    cls = D["must"]["class_code"]
    R = A.get("risks") or {}
    ritems, rrows = [], []
    for it in R.get("items") or []:
        name = (it.get("labels") or {}).get(lang) or _peril_label(it["code"], lang, it.get("name_ru"),
                                                                  it.get("class_code"))
        rs = it.get("reason") or {}
        if rs.get("code") == "by_factor":
            why = t("an_r_by_factor", lang, factor=_flabel(rs["factor"], lang), option=_olabel(rs["factor"], rs["option"],
                                                                                            lang), mult=_mult(rs["multiplier"], lang))
            if rs.get("assumed"):
                why += " — " + t("an_default_mark", lang)
        elif rs.get("code") == "zone_unknown":
            why = t("an_r_zone_unknown", lang)
        else:
            why = t("an_r_all_avg", lang)
        parts = [why]
        if it.get("raises"):
            parts.append(t("an_r_raises", lang, what=", ".join(
                f"{_fo(x['factor'], x['option'], lang)} ×{_mult(x['multiplier'], lang)}{_dmark(x, lang)}" for x in it["raises"])))
        if it.get("lowers"):
            parts.append(t("an_r_lowers", lang, what=", ".join(
                f"{_fo(x['factor'], x['option'], lang)} ×{_mult(x['multiplier'], lang)}{_dmark(x, lang)}" for x in it["lowers"])))
        if it.get("unknown"):
            parts.append(t("an_r_unknown", lang, what=", ".join(_flabel(f, lang).lower() for f in it["unknown"])))
        if it.get("measures"):
            parts.append(t("an_r_helps", lang, what=", ".join(
                f"{_fo(m['factor'], m['option_to'], lang)} {_spct(m['delta_pct'], lang)}" for m in it["measures"])))
        why_all = "; ".join(parts)
        share = tx.pct_fixed(it["share_of_net_pct"], lang, 1)
        level = tx.label(tx.PERIL_LEVEL_LABELS, it["level"], lang)
        label = name + (" (" + t("an_r_cat", lang) + ")" if it.get("catastrophic") else "")
        ritems.append({"code": it["code"], "name": name, "share_of_net_pct": it["share_of_net_pct"],
                       "share_text": share, "level": it["level"], "level_label": level, "why": why_all,
                       "reason": rs, "catastrophic": bool(it.get("catastrophic")), "multiplier": it.get("multiplier"),
                       "raises": [_olabel(x["factor"], x["option"], lang) for x in it.get("raises") or []],
                       "lowers": [_olabel(x["factor"], x["option"], lang) for x in it.get("lowers") or []],
                       "unknown": [_flabel(f, lang) for f in it.get("unknown") or []],
                       "measures": [_olabel(m["factor"], m["option_to"], lang) for m in it.get("measures") or []],
                       "text": t("an_r_line", lang, name=label, share=share, level=level, why=why_all),
                       "calibrated": ae.CALIBRATED})
        rrows.append([label, share, level, why_all])
    rnotes = []
    if R.get("whole_class"):
        rnotes.append(t("an_r_whole", lang, cls=cls))
    covered = [x["label"] for x in _risk_view(((D.get("contract") or {}).get("covered_risks")), tx.RISK_LABELS, lang)]
    if covered:
        rnotes.append(t("an_r_covered", lang, what=", ".join(covered)))
    th = R.get("thresholds") or aa.PERIL_LEVEL
    rnotes.append(t("an_r_rule", lang, low=_mult(th["low_max"], lang), high=_mult(th["high_min"], lang)))
    if R.get("source") == "template" and ritems:
        # риски и доли — из шаблона класса (справочник class_templates): в perils класса нет
        tnote = ((((D.get("template") or {}).get("risks") or {}).get("note")) or {})
        rnotes.append(t("an_r_template", lang, cls=cls, total=tx.pct_fixed(R.get("total_pct") or 0, lang, 1),
                        note=tnote.get(lang) or tnote.get("ru") or "").strip())
    elif not R.get("whole_class") and ritems:
        # класс без разбивки на риски — про доли справочника не пишем: там одна строка «весь класс»
        rnotes.append(t("an_r_shares_round" if R.get("rounding") else "an_r_shares", lang,
                        total=tx.pct_fixed(R.get("total_pct") or 0, lang, 1)))
    js["risks"] = {"available": bool(ritems), "items": ritems, "total_pct": R.get("total_pct"),
                   "whole_class": bool(R.get("whole_class")), "covered_by_contract": covered, "notes": rnotes,
                   "source": R.get("source") or ("class" if R.get("whole_class") else "perils"),
                   "calibrated": ae.CALIBRATED}
    if R.get("source") == "template":
        js["risks"]["label"] = t("an_r_tpl_label", lang)
    if ritems:
        lists.append(_li(t("an_risks_title", lang), [x["text"] for x in ritems],
                         {"columns": [t("col_risk", lang), t("col_share", lang), t("col_level", lang),
                                      t("col_why", lang)], "rows": rrows, "widths": [22, 12, 11, 55]},
                         notes=rnotes))


def _an_factors(D: dict, A: dict, lang: str, js: dict, lists: list) -> None:
    """Раздел «Факторы»: значение, источник, множитель и вклад каждого фактора."""
    F = A.get("factors") or {}
    fitems, frows = [], []
    for x in F.get("items") or []:
        name = _flabel(x["factor"], lang, x.get("name_ru"))
        value = _olabel(x["factor"], x.get("option"), lang, x.get("option_ru"))
        src = tx.label(tx.AN_SOURCE_LABELS, x["source"], lang)
        if not x.get("applies", True):
            contrib = t("an_f_seismic_na", lang)
        elif x.get("rate_pp"):
            contrib = t("an_f_contrib", lang, pp=_spp(x["rate_pp"], lang), premium=_smoney(x["premium_effect"], lang))
        else:
            contrib = t("an_f_zero", lang)
        text = t("an_f_line", lang, factor=name, value=value, source=src, mult=_mult(x["multiplier"], lang),
                 contrib=contrib)
        fitems.append({"code": x["factor"], "name": name, "option": x.get("option"), "value": value,
                       "source": x["source"], "source_label": src, "status": x["status"],
                       "multiplier": x["multiplier"], "effect_pct": x.get("effect_pct"), "direction": x["direction"],
                       "rate_pp": x.get("rate_pp"), "premium_effect": x.get("premium_effect"), "contribution": contrib,
                       "text": text, "calibrated": ae.CALIBRATED})
        frows.append([name, value, src, "×" + _mult(x["multiplier"], lang), contrib])
    fnote = t("an_f_base", lang, base=pct(F.get("base_pct"), lang), tech=pct(F.get("technical_pct"), lang))
    js["factors"] = {"items": fitems, "base_pct": F.get("base_pct"), "technical_pct": F.get("technical_pct"),
                     "note": fnote, "calibrated": ae.CALIBRATED}
    if fitems:
        lists.append(_li(t("an_factors_title", lang), [x["text"] for x in fitems],
                         {"columns": [t("col_factor", lang), t("col_value", lang), t("col_source", lang),
                                      t("col_mult", lang), t("col_contrib", lang)], "rows": frows,
                          "widths": [20, 20, 22, 10, 28]}, notes=[fnote]))


def _an_sensitivity(D: dict, A: dict, lang: str, js: dict, lists: list) -> None:
    """Чувствительность: как изменится техническая ставка и премия акта от одного фактора."""
    sitems, srows = [], []
    for s in (A.get("sensitivity") or {}).get("items") or []:
        fname = _flabel(s["factor"], lang, s.get("name_ru"))
        to = _olabel(s["factor"], s["option_to"], lang, s.get("option_to_ru"))
        key = "an_s_measure" if s["kind"] == "measure" else "an_s_clarify"
        text = t(key, lang, factor=fname if s["kind"] == "measure" else fname.lower(), to=to,
                 before=pct(s["tech_before"], lang), after=pct(s["tech_after"], lang),
                 delta=_spct(s["delta_pct"], lang))
        act_txt = ""
        if s.get("act_premium_after") is not None:
            d_txt = _smoney(s["act_premium_delta"], lang) if s.get("act_premium_delta") else t("an_ms_zero", lang)
            act_txt = money(s["act_premium_after"], lang) + " (" + d_txt + ")"
            text += t("an_s_act", lang, after=money(s["act_premium_after"], lang), delta=d_txt)
            if s.get("act_floored") and s.get("act_premium_delta"):
                text += t("an_s_act_floor", lang)
        sitems.append({"factor": s["factor"], "name": fname, "from": _olabel(s["factor"], s.get("option_from"), lang),
                       "to": to, "kind": s["kind"], "direction": s["direction"], "delta_pct": s["delta_pct"],
                       "tech_before": s["tech_before"], "tech_after": s["tech_after"],
                       "tech_premium_delta": s.get("tech_premium_delta"),
                       "act_premium_after": s.get("act_premium_after"), "act_premium_delta": s.get("act_premium_delta"),
                       "act_floored": bool(s.get("act_floored")), "text": text, "calibrated": ae.CALIBRATED})
        first = f"{fname}: {to}" if s["kind"] == "measure" else t("an_s_if", lang, factor=fname.lower(), to=to)
        srows.append([first, f"{pct(s['tech_before'], lang)} → {pct(s['tech_after'], lang)}",
                      _spct(s["delta_pct"], lang), act_txt or "—"])
    snote = t("an_s_note", lang)
    js["sensitivity"] = {"items": sitems, "note": snote, "calibrated": ae.CALIBRATED}
    lists.append(_li(t("an_sens_title", lang), [x["text"] for x in sitems] or [t("an_s_none", lang)],
                     {"columns": [t("col_change", lang), t("col_tech", lang), t("col_change", lang),
                                  t("col_act_premium", lang)], "rows": srows,
                      "widths": [38, 24, 12, 26]} if srows else None, notes=[snote]))


def _an_tariff(D: dict, A: dict, lang: str, js: dict, lists: list) -> None:
    """Состав тарифа: от базовой нетто-ставки до ставки акта и рынка."""
    cls = D["must"]["class_code"]
    rate_res = D["rate"]
    T = A.get("tariff") or {}
    mk = A.get("market") or {}
    if T.get("available"):
        trows = []

        def tr(code, label, value, note=""):
            trows.append({"code": code, "label": label, "value": value, "note": note})

        otype = _otype_label(T.get("object_type") or "", lang)
        bnotes = []
        if T.get("object_type_source") == "default":
            bnotes.append(t("an_t_base_default", lang, v=otype))
        if T.get("base_kind") == "class_average":
            bnotes.append(t("an_t_base_avg", lang))
        tr("base_net", t("an_t_base", lang, cls=T["class_code"], otype=otype), pct(T["base_net_pct"], lang),
           "; ".join(bnotes))
        excl = [tx.label(tx.PERIL_LABELS, c, lang) for c in T.get("excluded") or []]
        if T.get("perils_share") is not None and (T["perils_share"] != 1 or excl):
            tr("perils", t("an_t_perils", lang), "×" + _mult(T["perils_share"], lang),
               t("an_t_excluded", lang, what=", ".join(excl)) if excl else "")
        tr("factors", t("an_t_factors", lang), "×" + _mult(T.get("factors_mult") or 1, lang))
        tr("net", t("an_t_net", lang), pct(T["net_pct"], lang))
        tr("risk_load", t("an_t_risk", lang, r=tx.pct_fixed(T["risk_load_rate"] * 100, lang, 0)),
           "+" + pct(T["risk_load_pct"], lang))
        if T.get("cat_load_pct"):
            tr("cat_load", t("an_t_cat", lang), "+" + pct(T["cat_load_pct"], lang))
        if T.get("load_share") is not None:
            tr("load", t("an_t_load", lang), tx.pct_fixed(T["load_share"] * 100, lang, 0),
               t("an_t_load_takaful", lang) if T.get("takaful") else "")
        tr("technical", t("an_t_tech", lang), pct(T["technical_pct"], lang),
           t("an_t_tech_note", lang, premium=money(T["technical_premium"], lang)))
        # часть комплексного продукта: минимум класса из текста тарифа продукта — это не «ставка продукта»
        cm_src = ((D.get("rate") or {}).get("class_min") or {}).get("source")
        by_text = cm_src in ("rate_text", "rate_text_common")
        cm_kw = {"cls": (D.get("must") or {}).get("class_code") or T.get("class_code"), "code": T.get("product_code")}
        tr("min", t("an_t_min_class" if by_text else "an_t_min", lang, **cm_kw),
           _pct_na(T.get("min_pct"), lang))
        if T.get("act_mode") == "tariff":
            if T.get("policy_rate_pct") is not None:
                tr("policy", t("an_t_policy_class" if by_text else "an_t_policy", lang, **cm_kw),
                   pct(T["policy_rate_pct"], lang))
            else:
                tr("act_base", t("an_t_base_act", lang), pct(T.get("act_base_pct"), lang))
            act_note = t("an_t_act_min_class" if by_text else "an_t_act_min", lang, **cm_kw) \
                if T.get("min_applied") else ""
            if rate_res.get("fork_applied"):         # вилка ставки, режим apply: ставка акта уже с поправками
                act_note = "; ".join(x for x in (act_note, t("rf_an_applied", lang)) if x)
            tr("act", t("an_t_act", lang, adj="+" + pct(T.get("adj_pct") or 0, lang)), pct(T["act_rate_pct"], lang),
               act_note)
        else:
            tr("act", t("an_t_act_other", lang), pct(T["act_rate_pct"], lang) if T.get("act_rate_pct") is not None
               else t("rate_undefined", lang))
        tr("market", t("an_t_market", lang), pct(T["market_rate_pct"], lang) if T.get("market_rate_pct") else
           t("an_t_na_short", lang), (t("an_m_pack", lang, pack=_pack_text(mk, lang)).strip(" ()")
                                      if mk.get("pack") else ""))
        concl = t("an_t_conclusion", lang, act=pct(T["act_rate_pct"], lang) if T.get("act_rate_pct") is not None
                  else t("rate_undefined", lang), tech=pct(T["technical_pct"], lang),
                  market=pct(T["market_rate_pct"], lang) if T.get("market_rate_pct") else t("an_t_na_short", lang))
        js["tariff"] = {"available": True, "rows": trows, "conclusion": concl, "calibrated": ae.CALIBRATED,
                        **{k: T.get(k) for k in ("base_net_pct", "perils_share", "factors_mult", "net_pct",
                                                  "risk_load_pct", "cat_load_pct", "load_share", "takaful",
                                                  "technical_pct", "technical_premium", "min_pct", "policy_rate_pct",
                                                  "adj_pct", "act_rate_pct", "act_premium", "market_rate_pct",
                                                  "act_vs_technical", "act_vs_market_pct", "technical_vs_market_pct")}}
        lists.append(_li(t("an_tariff_title", lang),
                         [f"{r['label']}: {r['value']}" + (f" ({r['note']})" if r["note"] else "") for r in trows],
                         {"columns": [t("col_indicator", lang), t("col_value", lang), t("col_note", lang)],
                          "rows": [[r["label"], r["value"], r["note"]] for r in trows], "widths": [45, 18, 37]},
                         notes=[concl]))
    else:
        txt = t("an_t_na", lang, cls=cls)
        js["tariff"] = {"available": False, "reason": T.get("reason") or "no_base", "text": txt,
                        "conclusion": txt, "calibrated": ae.CALIBRATED}
        lists.append(_li(t("an_tariff_title", lang), [txt]))


def _an_scenarios(D: dict, A: dict, lang: str, js: dict, lists: list) -> None:
    """Сценарии PML/EML/MFL подробно и варианты «что если»."""
    NA = t("na", lang)
    SC = A.get("scenarios") or {}
    if SC.get("available"):
        scitems, scrows = [], []
        for it in SC.get("items") or []:
            how = []
            for p in it["parts"]:
                share = _mult(p["share"], lang) if p.get("share") is not None else NA
                if p["peril"] == "fire":
                    how.append(t("an_sc_fire", lang, base=money(p["base"], lang),
                                 where=t("an_sc_where_" + p["base_kind"], lang), share=share,
                                 amount=money(p["amount"], lang)))
                elif p["peril"] == "earthquake":
                    if p.get("amount") is None:
                        how.append(t("an_sc_eq_na", lang))
                    else:
                        zone = t("an_sc_zone", lang, z=_pts(p["zone"], lang)) if p.get("zone") is not None \
                            else t("an_sc_zone_na", lang)
                        how.append(t("an_sc_eq", lang, base=money(p["base"], lang), zone=zone, share=share,
                                     amount=money(p["amount"], lang)))
                elif p["peril"] == "damage9":
                    how.append(t("an_sc_c9", lang, base=money(p["base"], lang),
                                 where=t("an_sc_where_" + p["base_kind"], lang), share=share,
                                 amount=money(p["amount"], lang)))
                elif p["peril"] == "template":
                    w = p.get("what") or {}
                    how.append(t("an_sc_tpl", lang, what=w.get(lang) or w.get("ru") or it["name"],
                                 amount=money(p["amount"], lang)))
                else:
                    how.append(t("an_sc_veh", lang, base=money(p["base"], lang), share=share,
                                 amount=money(p["amount"], lang)))
            if it["rule"] == "property8" and len([p for p in it["parts"] if p.get("amount") is not None]) > 1:
                how.append(t("an_sc_taken", lang, what=tx.label(tx.PERIL_LABELS, it["chosen"], lang).lower()))
            if it.get("k") is not None and it["k"] < 1 and it["rule"] != "vehicle":
                how.append(t("an_sc_k", lang, k=_mult(it["k"], lang)))
            if it.get("bi_loss"):
                how.append(t("an_sc_bi", lang, x=money(it["bi_loss"], lang)))
            if it.get("source") == "template":
                pass                             # простое правило шаблона: защиты в правиле нет
            elif it["rule"] != "vehicle":
                how.append(t("an_sc_prot_assumed", lang) if it.get("protection_assumed")
                           else t("an_sc_prot", lang, v=_olabel("protection", it.get("protection"), lang)))
            elif it.get("protection"):
                how.append(t("an_sc_prot", lang, v=_olabel("protection", it["protection"], lang)))
            how_txt = "; ".join(how)
            pct_txt = tx.pct_fixed(it["pct"], lang, 1) if it.get("pct") is not None else NA
            scitems.append({"name": it["name"], "amount": it["amount"], "pct": it.get("pct"), "pct_text": pct_txt,
                            "formula": how_txt, "parts": [_sc_part_local(p, lang) for p in it["parts"]],
                            "chosen": it.get("chosen"),
                            "text": t("an_sc_line", lang, name=it["name"], amount=money(it["amount"], lang),
                                      pct=pct_txt, how=how_txt), "calibrated": ae.CALIBRATED})
            scrows.append([it["name"], money(it["amount"], lang) + " (" + pct_txt + ")", how_txt])
        lists.append(_li(t("an_sc_title", lang), [x["text"] for x in scitems],
                         {"columns": [t("col_scenario", lang), t("col_amount", lang), t("col_how", lang)],
                          "rows": scrows, "widths": [10, 25, 65]}))
        # «что если»
        witems, wrows = [], []
        for w in SC.get("whatif") or []:
            if not w.get("ok"):
                continue
            val = _olabel("protection", w["value"], lang) if w["change"] == "protection" else _pts(w["value"], lang)
            what = tx.label(tx.AN_WHATIF_LABELS, w["change"], lang).format(v=val)
            tail = ""
            if w.get("eml_excess") is not None:
                tail = t("an_wi_excess", lang, x=money(w["eml_excess"], lang)) if w["eml_excess"] \
                    else t("an_wi_within", lang)
            dp = _smoney(w["pml_delta"], lang) if w["pml_delta"] else t("an_wi_same", lang)
            de = _smoney(w["eml_delta"], lang) if w["eml_delta"] else t("an_wi_same", lang)
            text = t("an_wi_line", lang, what=what, pml=money(w["pml"], lang), dp=dp, eml=money(w["eml"], lang),
                     de=de, mfl=money(w["mfl"], lang), tail=tail)
            witems.append({"change": w["change"], "value": w["value"], "label": what, "pml": w["pml"], "eml": w["eml"],
                           "mfl": w["mfl"], "pml_delta": w["pml_delta"], "eml_delta": w["eml_delta"],
                           "mfl_delta": w["mfl_delta"], "eml_excess": w.get("eml_excess"), "text": text,
                           "calibrated": ae.CALIBRATED})
            wrows.append([what, money(w["pml"], lang), money(w["eml"], lang), money(w["mfl"], lang)])
        lists.append(_li(t("an_whatif_title", lang), [x["text"] for x in witems] or [t("an_wi_none", lang)],
                         {"columns": [t("col_variant", lang), "PML", "EML", "MFL"], "rows": wrows,
                          "widths": [34, 22, 22, 22]} if wrows else None))
        js["scenarios"] = {"available": True, "items": scitems, "whatif": witems, "calibrated": ae.CALIBRATED}
    else:
        js["scenarios"] = {"available": False, "reason": SC.get("reason"), "items": [], "whatif": [],
                           "calibrated": ae.CALIBRATED}


def _an_retention(D: dict, A: dict, lang: str, js: dict, lists: list) -> None:
    """Удержание: лимит на один риск по норме и сравнение с EML/MFL (оценка, не факт)."""
    must = D["must"]
    cls = must["class_code"]
    RET = A.get("retention") or {}
    # удержание — оценка, а не факт (30.09.2026): лимит по норме на временных цифрах, таблица линий — экспертная
    rlines, est_note = [], None
    if RET.get("known"):
        temp = RET.get("status") == "temporary"
        est = t("an_ret_est" if temp else "an_ret_est_rep", lang)
        rlines.append(t("an_ret_law", lang, own=money(RET.get("own_funds"), lang), res=money(RET.get("reserves"), lang),
                        lpr=money(RET.get("limit_per_risk"), lang),
                        temp=t("an_ret_temp" if temp else "an_ret_reported", lang)))
        S_ret = RET.get("sum_insured") if RET.get("sum_insured") is not None else must.get("sum_insured")
        if RET.get("sum_within_limit_20") is False:
            rlines.append(t("an_ret_over20", lang))
        elif RET.get("limit_per_risk") is not None and S_ret is not None:
            rlines.append(t("an_ret_sum_in", lang, sum=money(S_ret, lang), lpr=money(RET.get("limit_per_risk"), lang)))
        if RET.get("line_retention") is not None:
            rlines.append(t("an_ret_line", lang, cls=RET.get("line_class") or cls,
                            line=money(RET.get("line_retention"), lang)))
            rlines.append(t("an_ret_calc", lang, limit=money(RET.get("limit"), lang)))
        else:
            rlines.append(t("an_ret_calc_one", lang, limit=money(RET.get("limit"), lang)))
        rlines.append(t("an_ret_cmp", lang, eml=money(RET.get("eml"), lang), limit=money(RET.get("limit"), lang),
                        mfl=money(RET.get("mfl"), lang)))
        if RET["verdict"] == "within":
            rlines.append(t("an_ret_within", lang, est=est))
        elif RET["verdict"] == "mfl_excess":
            rlines.append(t("an_ret_mfl", lang, x=money(RET.get("mfl_excess"), lang), est=est))
        else:
            rlines.append(t("an_ret_eml", lang, x=money(RET.get("eml_excess"), lang),
                            y=money(RET.get("mfl_excess"), lang), est=est))
        parts = ([t("an_ret_note_temp", lang)] if temp else []) + \
            ([t("an_ret_note_line", lang)] if RET.get("line_retention") is not None else [])
        if parts:
            est_note = t("an_ret_note", lang, what="; ".join(parts))
            rlines.append(est_note)
    else:
        rlines.append(t("an_ret_unknown", lang))
    js["retention"] = {**{k: RET.get(k) for k in ("known", "limit", "limit_per_risk", "line_retention", "line_class",
                                                   "own_funds", "reserves", "status", "eml", "mfl", "eml_excess",
                                                   "mfl_excess", "sum_within_limit_20", "verdict", "need",
                                                   "sum_insured", "funds_source", "line_rule")},
                       "estimate": bool(RET.get("known")), "estimate_note": est_note,
                       "legal_ref": tx.label(tx.LEGAL_REFS, RET.get("legal_ref") or "Положение № 1806, п. 15", lang),
                       "lines": rlines, "text": " ".join(rlines), "calibrated": ae.CALIBRATED}
    lists.append(_li(t("an_ret_title", lang), rlines))


def _an_score(D: dict, A: dict, lang: str, js: dict, lists: list) -> None:
    """Балл риска 0–100: компоненты, веса, вклад."""
    S5 = A.get("score") or {}
    if S5.get("available"):
        prm = S5.get("params") or {}
        citems, crows = [], []
        for c in S5.get("components") or []:
            code, v = c["code"], c.get("value")
            if code == "rate":
                why = t("an_why_rate", lang, v=_mult(v, lang), lo=_mult(prm["rate_ratio"][0], lang),
                        hi=_mult(prm["rate_ratio"][1], lang)) if v is not None else t("an_why_rate_na", lang)
            elif code == "mfl_retention":
                why = t("an_why_mfl_ret", lang, v=_mult(v, lang), lo=_mult(prm["mfl_to_retention"][0], lang),
                        hi=_mult(prm["mfl_to_retention"][1], lang)) if c.get("basis") == "retention" \
                    else t("an_why_mfl_sum", lang, v=tx.pct_fixed(v or 0, lang, 0))
            elif code == "losses":
                why = t("an_why_losses", lang, v=v) if v is not None else t("an_why_unknown", lang,
                                                                            v=prm.get("unknown_points"))
            elif code == "insurance_to_value":
                why = t("an_why_itv", lang, v=tx.pct_fixed((v or 0) * 100, lang, 0))
            elif code == "seismic":
                why = (t("an_why_seismic", lang, v=_pts(v, lang)) if v is not None else t("an_why_unknown", lang,
                                                                              v=prm.get("unknown_points"))) \
                    if c["applicable"] else t("an_why_seismic_na", lang)
            elif code == "external_stats":
                why = t("an_why_ext", lang, v=_mult(v, lang)) if c["applicable"] and v is not None \
                    else t({"kind": "an_why_ext_kind", "region_unknown": "an_why_ext_region",
                            "no_regional": "an_why_ext_noreg"}.get(c.get("reason"), "an_why_ext_na"), lang)
            else:
                why = ""
            name = tx.label(tx.SCORE_COMP_LABELS, code, lang)
            pts = tx._num(float(c["points"]), lang, 0)
            if c["applicable"]:
                text = t("an_sc_comp", lang, name=name, points=_pts(c["points"], lang),
                         weight=_mult(c.get("weight") or 0, lang),
                         contrib=tx._num(float(c.get("contribution") or 0), lang, 1), why=why)
            else:
                text = t("an_sc_comp_na", lang, name=name, why=why)
            citems.append({"code": code, "name": name, "points": c["points"], "weight": c.get("weight"),
                           "contribution": c.get("contribution"), "applicable": c["applicable"], "value": v,
                           "why": why, "text": text, "calibrated": ae.CALIBRATED})
            crows.append([name, pts if c["applicable"] else "—",
                          _mult(c.get("weight") or 0, lang) if c["applicable"] else "—",
                          tx._num(float(c.get("contribution") or 0), lang, 1) if c["applicable"] else "—"])
        b = S5.get("bounds") or [20, 40, 60, 80]
        level5 = tx.label(tx.LEVEL5_LABELS, S5.get("level") or "moderate", lang)
        act_lvl = tx.label(tx.LEVEL_LABELS, S5.get("act_level") or "moderate", lang)
        stext = t("an_sc_score", lang, score=tx._num(float(S5.get("score") or 0), lang, 1), level=level5, act=act_lvl,
                  b1=b[0], b2=b[1], b3=b[2], b4=b[3])
        js["score"] = {"available": True, "score": S5.get("score"), "level": S5.get("level"), "level_label": level5,
                       "housing_excluded": list(S5.get("housing_excluded") or []),
                       "act_level": S5.get("act_level"), "act_level_label": act_lvl, "components": citems,
                       "bounds": b, "text": stext, "override": bool(S5.get("override")), "calibrated": ae.CALIBRATED}
        lists.append(_li(t("an_score_title", lang), [stext] + [x["text"] for x in citems],
                         {"columns": [t("col_indicator", lang), t("col_points", lang), t("col_weight", lang),
                                      t("col_contribution", lang)], "rows": crows, "widths": [52, 16, 16, 16]},
                         notes=[stext]))
        lists[-1]["items"] = [stext] + [x["text"] for x in citems]
    else:
        js["score"] = {"available": False, "calibrated": ae.CALIBRATED}


def _an_market(D: dict, A: dict, lang: str, js: dict, lists: list) -> None:
    """Рынок (НАПП) и статистика региона (stat.uz, НАПП по регионам) с источниками."""
    must = D["must"]
    cls = must["class_code"]
    mk = A.get("market") or {}
    mitems, msrc, mrows, row_note = _an_market_rate(mk, cls, lang, js)
    ST = A.get("stats") or {}
    sitems2, nf = _an_stats(A, must, cls, lang, js, mitems, msrc, mrows)
    js["market"] = {"available": bool(mk.get("available")), "rate_pct": mk.get("rate_pct"),
                    "rate_date": mk.get("rate_date"), "months": mk.get("months"),
                    "loss_ratio_pct": mk.get("loss_ratio_pct"), "rate_full_year_pct": mk.get("rate_full_year_pct"),
                    "loss_ratio_full_year_pct": mk.get("loss_ratio_full_year_pct"), "full_year": mk.get("full_year"),
                    "pack": bool(mk.get("pack")), "row_key": mk.get("row_key"), "row_note": row_note,
                    "pack_choice": _pc_local(mk.get("pack_choice"), lang, cls),
                    "pack_classes": list(mk.get("pack_classes") or []),
                    "class_rows": list(mk.get("class_rows") or []),
                    # подпись строки на экране: у комплексного продукта — какой пакет взят и почему
                    "pack_label": (row_note if mk.get("pack_choice") else
                                   (t("an_m_pack_label", lang, pack=_pack_text(mk, lang)) if mk.get("pack") else None)),
                    "pack_premiums": mk.get("pack_premiums"), "alt_row_key": mk.get("alt_row_key"),
                    "alt_premiums": mk.get("alt_premiums"), "alt_share_pct": mk.get("alt_share_pct"),
                    "act_rate_pct": mk.get("act_rate_pct"),
                    "act_vs_market_pp": mk.get("act_vs_market_pp"), "act_vs_market_pct": mk.get("act_vs_market_pct"),
                    "technical_pct": mk.get("technical_pct"), "tech_vs_market_pct": mk.get("tech_vs_market_pct"),
                    "source": ({"title": (mk.get("source") or {}).get("title_ru") if lang == "ru" else "NAPP",
                                "url": (mk.get("source") or {}).get("url"),
                                "domain": (mk.get("source") or {}).get("domain"),
                                "as_of": (mk.get("source") or {}).get("slice")} if mk.get("source") else None),
                    "lines": list(mitems), "source_lines": list(msrc), "calibrated": ae.CALIBRATED}
    js["stats"] = {"available": bool(ST.get("available")), "region": region_label(must, lang),
                   "indicators": sitems2, "not_found": nf, "applicable": bool(ST.get("applicable")),
                   "points": ST.get("points"), "calibrated": ae.CALIBRATED,
                   # особый регион (02.10.2026): outside — вне Узбекистана, republic — по республике
                   "reason": ST.get("reason"), "scope": ST.get("scope"),
                   "note": t("an_st_outside", lang) if ST.get("reason") == "outside" else
                   (t("an_st_republic_note", lang) if ST.get("scope") == "republic" else None)}
    lists.append(_li(t("an_market_title", lang), mitems,
                     {"columns": [t("col_indicator", lang), t("col_value", lang), t("col_period", lang)],
                      "rows": mrows, "widths": [40, 32, 28]} if mrows else None, sources=msrc,
                     notes=[row_note] if row_note else None))

def _an_market_rate(mk: dict, cls: str, lang: str, js: dict) -> tuple:
    """Строки рынка НАПП: ставка, убыточность, полный год, строки классов, сравнение со ставкой акта, источник.
    Возвращает (строки, источники, строки таблицы, пояснение к выбору строки отчёта)."""
    NA = t("na", lang)
    mitems, msrc, mrows = [], [], []
    row_note = None
    if mk.get("available"):
        pack = t("an_m_pack", lang, pack=_pack_text(mk, lang)) if mk.get("pack") else ""
        line = t("an_m_rate", lang, cls=cls, pack=pack, rate=pct(mk["rate_pct"], lang), date=_date(mk["rate_date"]),
                 months=mk.get("months") or "—")
        mitems.append(line)
        mrows.append([t("an_t_market", lang), pct(mk["rate_pct"], lang), _date(mk["rate_date"])])
        if mk.get("loss_ratio_pct") is not None:
            mitems.append(t("an_m_lr", lang, lr=tx.pct_fixed(mk["loss_ratio_pct"], lang, 1), date=_date(mk["rate_date"])))
            mrows.append([t("an_m_lr_label", lang), tx.pct_fixed(mk["loss_ratio_pct"], lang, 1),
                          _date(mk["rate_date"])])
        if mk.get("rate_full_year_pct") is not None and mk.get("full_year"):
            # полный год — отдельной строкой, как на экране (не хвостом строки убыточности среза)
            fy = t("an_m_fy", lang, year=mk["full_year"], rate=pct(mk["rate_full_year_pct"], lang),
                   lr=tx.pct_fixed(mk["loss_ratio_full_year_pct"] or 0, lang, 1))
            mitems.append(fy)
            # в таблице документа — две строки (ставка, убыточность), как две величины на экране
            mrows.append([t("an_m_fy_rate_label", lang, year=mk["full_year"]), pct(mk["rate_full_year_pct"], lang),
                          str(mk["full_year"])])
            if mk.get("loss_ratio_full_year_pct") is not None:
                mrows.append([t("an_m_fy_lr_label", lang, year=mk["full_year"]),
                              tx.pct_fixed(mk["loss_ratio_full_year_pct"], lang, 1), str(mk["full_year"])])
        if mk.get("class_rows"):
            # рядом с пакетом — одиночные строки классов продукта (правило проекта № 5: ставка по каждому классу)
            for r in mk["class_rows"]:
                if not r.get("available"):
                    mrows.append([t("an_m_class_label", lang, cls=r["class_code"]), NA, "—"])
                    continue
                val = t("an_m_class_value", lang, rate=pct(r.get("rate_pct"), lang),
                        lr=tx.pct_fixed(r["loss_ratio_pct"], lang, 1) if r.get("loss_ratio_pct") is not None else NA)
                if r.get("rate_full_year_pct") is not None:
                    val += t("an_m_class_fy", lang, year=_fy_year(r.get("full_year_period")) or "—",
                             rate=pct(r["rate_full_year_pct"], lang),
                             lr=tx.pct_fixed(r.get("loss_ratio_full_year_pct"), lang, 1)
                             if r.get("loss_ratio_full_year_pct") is not None else NA)
                mrows.append([t("an_m_class_label", lang, cls=r["class_code"]), val, _date(r.get("date"))])
            cr_line = _class_rows_text(mk["class_rows"], lang)
            if cr_line:
                mitems.append(cr_line[:1].upper() + cr_line[1:] + ".")
        if mk.get("pack_choice"):
            # комплексный продукт: пакет НАПП с классами продукта (точный / ближайший) или строка класса
            row_note = _pack_choice_text(mk["pack_choice"], lang, cls, "an_m_prod_")
        elif mk.get("pack"):
            # какая строка отчёта взята и почему — по объёму премий строк в market_stats
            if mk.get("alt_premiums") is not None and mk.get("pack_premiums"):
                row_note = t("an_m_row", lang, pack=_pack_text(mk, lang), rk=mk.get("row_key"), cls=cls,
                             pp=tx._num(float(mk["pack_premiums"]), lang, 0), date=_date(mk.get("rate_date")),
                             alt=mk.get("alt_row_key"), ap=tx._num(float(mk["alt_premiums"]), lang, 0),
                             share=tx.pct_fixed(mk.get("alt_share_pct") or 0, lang, 1))
            else:
                row_note = t("an_m_row_only", lang, pack=_pack_text(mk, lang), rk=mk.get("row_key"), cls=cls)
        act = mk.get("act_rate_pct")
        if act is not None and mk.get("act_vs_market_pp") is not None:
            key = "an_m_cmp_above" if mk["act_vs_market_pp"] > 0 else "an_m_cmp_below"
            cmp_line = t(key, lang, act=pct(act, lang), pp=_mult(abs(mk["act_vs_market_pp"]), lang),
                         pct=_spct(mk["act_vs_market_pct"], lang))
            if mk.get("tech_vs_market_pct") is not None:
                cmp_line += "; " + t("an_m_cmp_tech", lang, tech=pct(mk.get("technical_pct"), lang),
                                     pct=_spct(mk["tech_vs_market_pct"], lang))
            mitems.append(cmp_line)
            mrows.append([t("an_m_cmp_label", lang), _spct(mk["act_vs_market_pct"], lang),
                          pct(act, lang) + " / " + pct(mk["rate_pct"], lang)])
        if mk.get("missing_quarters") and lang == "ru":
            mitems.append(t("an_m_gap", lang, what=", ".join(mk["missing_quarters"])))
        s = mk.get("source") or {}
        if s:
            file = ""
            if lang == "ru" and "(" in (s.get("title_ru") or ""):
                file = " (" + s["title_ru"].split("(", 1)[1]
                file = file if file.endswith(")") else file + ")"
            msrc.append(t("an_m_src", lang, file=file, date=_date(s.get("slice") or s.get("as_of")), url=s["url"]))
            _an_src_add(js, t("an_m_src", lang, file="", date=_date(s.get("slice") or s.get("as_of")), url="")
                    .rstrip(" —").split(": ", 1)[-1], s["url"], s.get("domain"), s.get("slice") or s.get("as_of"))
    else:
        mitems.append(t("an_m_na", lang, cls=cls))
    return mitems, msrc, mrows, row_note


def _an_stats(A: dict, must: dict, cls: str, lang: str, js: dict, mitems: list, msrc: list, mrows: list) -> tuple:
    """Показатели stat.uz и НАПП по региону — дописываются в строки, источники и таблицу рынка.
    Возвращает (показатели для JSON, «не найдено» по классу)."""
    ST = A.get("stats") or {}
    sitems2 = []
    for i in ST.get("indicators") or []:
        name = tx.label(tx.STAT_LABELS, i["id"], lang) if i["id"] in tx.STAT_LABELS else \
            (i.get("name_ru") if lang == "ru" else i["id"])
        where = t("an_st_region", lang) + ": " + (region_label(must, lang) or "") if i["scope"] == "region" \
            else t("an_st_republic", lang)
        if i["status"] != "ok":
            value = t("an_st_nodata", lang)
        else:
            unit = tx.label(tx.STAT_UNITS, i.get("unit_ru"), lang) if i.get("unit_ru") in tx.STAT_UNITS else \
                (i.get("unit_ru") if lang == "ru" else "")
            num = i["value"]
            if lang == "ru" and i.get("unit_ru") in tx.UNIT_FORMS_RU:
                unit = tx.plural_ru(num, tx.UNIT_FORMS_RU[i["unit_ru"]])      # 32 случая, 21 кража
            value = tx._num(float(num), lang, 0 if float(num) == int(num) or abs(num) >= 1000 else 2) + \
                (("" if lang == "en" and unit.startswith("%") else " ") + unit if unit else "")
            if i.get("per_1000") is not None:
                value += t("an_st_per1000", lang, v=tx._num(float(i["per_1000"]), lang, 2))
        cmpx = ""
        if i.get("vs_country"):
            cmpx = t("an_st_cmp", lang, pct=_spct(i["vs_country"]["diff_pct"], lang),
                     pts=t("an_st_pts", lang, p=tx._num(float(i["points"]), lang, 0)) if i.get("used_in_score")
                     and i.get("points") is not None else "")
        if i.get("excluded_for_kind"):
            cmpx += t("an_st_kind", lang, kind=tx.label(tx.GROUP_LABELS, A.get("group") or "other", lang))
        line = t("an_st_line", lang, name=name, where=where, period=i.get("period") or "—", value=value, cmp=cmpx)
        srcs = []
        for s in i.get("sources") or []:
            if not s.get("url"):
                continue
            nm = (s.get("name_ru") if lang == "ru" else name) or name
            line_s = t("an_st_src" if s.get("fetched_at") else "an_st_src_nf", lang, src=s.get("source") or "",
                       name=nm, period=s.get("period") or "—", fetched=_date(s.get("fetched_at")), url=s["url"])
            if line_s not in msrc and line_s not in srcs:
                srcs.append(line_s)
            _an_src_add(js, (s.get("source") or "") + " — " + nm, s["url"], s.get("source"), s.get("period"))
        sitems2.append({"id": i["id"], "name": name, "scope": i["scope"], "status": i["status"],
                        "value": i.get("value"), "value_text": value, "period": i.get("period"),
                        "vs_country": i.get("vs_country"), "used_in_score": i.get("used_in_score"),
                        "excluded_for_kind": bool(i.get("excluded_for_kind")),
                        "points": i.get("points"), "text": line, "source_lines": srcs,
                        "sources": [{"title": x.get("name_ru") if lang == "ru" else name, "url": x.get("url"),
                                     "source": x.get("source"), "period": x.get("period"),
                                     "fetched_at": x.get("fetched_at")} for x in i.get("sources") or []],
                        "calibrated": ae.CALIBRATED})
        mitems.append(line)
        mrows.append([name, value + cmpx, f"{i.get('period') or '—'}; {where}"])
        msrc += srcs
    if ST.get("reason") == "outside":
        mitems.append(t("an_st_outside", lang))
    elif not sitems2:
        mitems.append(t("an_st_none", lang, cls=cls))
    if ST.get("scope") == "republic":
        mitems.append(t("an_st_republic_note", lang))
    nf = tx.label(tx.STAT_NOT_FOUND, cls, lang) if cls in tx.STAT_NOT_FOUND else \
        ((ST.get("not_found") or {}).get(cls) if lang == "ru" else None)
    if nf:
        mitems.append(t("an_st_nf", lang, what=nf))
    # претензии региона, рынок / INSON, подразделения INSON в регионе (НАПП, 01.10.2026): тот же формат строки
    # показателя, что у stat.uz (экран показывает их в analytics.stats.indicators со своей плашкой источника)
    for it in _napp_items(A.get("napp") or {}, lang, must):
        sitems2.append(it)
        mitems.append(it["text"])
        mrows.append([it["name"], it["value_text"], it["row_note"]])
        for line_s in it["source_lines"]:
            if line_s not in msrc:
                msrc.append(line_s)
        for x in it["sources"]:
            _an_src_add(js, x["title"], x["url"], x["source"], x["period"])
    return sitems2, nf


def _an_franchise(D: dict, A: dict, lang: str, js: dict, lists: list) -> None:
    """Варианты франшизы справочно: ставка, премия, экономия."""
    FT = A.get("franchise") or {}
    fr_verdict = _fr_text(D["franchise"], lang)
    if FT.get("available"):
        fitems2, frows2 = [], []
        for r in FT.get("rows") or []:
            p = pct(r["pct"], lang)
            if not r.get("ok"):
                text = t("an_fr_na_row", lang, pct=p)
                frows2.append([p, money(r["amount"], lang), "—", "—", "—"])
            else:
                note = (t("an_fr_floor", lang) if r.get("floored") else "") + \
                    (t("an_fr_extra", lang) if r.get("extrapolated") else "")
                text = t("an_fr_line", lang, pct=p, amount=money(r["amount"], lang), rate=pct(r["rate_pct"], lang),
                         premium=money(r["premium"], lang), saving=money(r["saving"], lang), note=note)
                frows2.append([p, money(r["amount"], lang), pct(r["rate_pct"], lang) + note, money(r["premium"], lang),
                               money(r["saving"], lang) + (" (" + tx.pct_fixed(r["saving_pct"], lang, 1) + ")"
                                                           if r.get("saving_pct") is not None else "")])
            fitems2.append({**{k: r.get(k) for k in ("pct", "amount", "multiplier", "rate_pct", "premium", "saving",
                                                     "saving_pct", "floored", "extrapolated", "ok")},
                            "text": text, "calibrated": ae.CALIBRATED})
        fnote2 = t("an_fr_note", lang, verdict=fr_verdict.rstrip("."))
        js["franchise"] = {"available": True, "reference_only": True, "base_premium": FT.get("base_premium"),
                           "base_rate_pct": FT.get("base_rate_pct"), "cap_pct": FT.get("cap_pct"), "rows": fitems2,
                           "verdict": fr_verdict, "note": fnote2, "calibrated": ae.CALIBRATED}
        lists.append(_li(t("an_fr_title", lang), [x["text"] for x in fitems2],
                         {"columns": [t("col_franchise", lang), t("col_per_loss", lang), t("col_rate", lang),
                                      t("col_premium", lang), t("col_saving", lang)], "rows": frows2,
                          "widths": [12, 24, 14, 24, 26]}, notes=[fnote2]))
    else:
        txt = t("an_fr_na_" + (FT.get("reason") or "error"), lang)
        js["franchise"] = {"available": False, "reason": FT.get("reason"), "text": txt, "verdict": fr_verdict,
                           "calibrated": ae.CALIBRATED}
        lists.append(_li(t("an_fr_title", lang), [txt]))


def _an_measures(D: dict, A: dict, lang: str, js: dict, lists: list) -> None:
    """Эффект мероприятий на техническую ставку и премию акта."""
    ME = A.get("measures") or {}
    ms_texts = {m["json"]["code"]: m for m in (_measures_view(D.get("measures"), lang)["items"] or [])}
    mitems3, mrows3 = [], []
    for it in ME.get("items") or []:
        mv = (ms_texts.get(it["code"]) or {}).get("json") or {}
        text0 = (mv.get("text") or it["code"]).rstrip(".")
        if it.get("ratio"):
            delta = _smoney(it["premium_delta"], lang) if it.get("premium_delta") else t("an_ms_zero", lang)
            text = t("an_ms_line", lang, text=text0, before=pct(it["tech_before"], lang),
                     after=pct(it["tech_after"], lang), eff=_spct(it["effect_pct"], lang), delta=delta)
            mrows3.append([text0, f"{pct(it['tech_before'], lang)} → {pct(it['tech_after'], lang)} "
                                  f"({_spct(it['effect_pct'], lang)})", delta])
        else:
            text = t("an_ms_line_na", lang, text=text0)
            mrows3.append([text0, t("an_f_zero", lang), "—"])
        mitems3.append({"code": it["code"], "text": text, "name": text0, "effect_pct": it.get("effect_pct"),
                        "tech_before": it.get("tech_before"), "tech_after": it.get("tech_after"),
                        "tech_delta_pp": it.get("tech_delta_pp"), "premium_delta": it.get("premium_delta"),
                        "calibrated": ae.CALIBRATED})
    total_txt = None
    if ME.get("tech_after_all") is not None and ME.get("premium_after") is not None:
        total_txt = t("an_ms_total", lang, before=pct(ME["tech_before"], lang), after=pct(ME["tech_after_all"], lang),
                      pb=money(ME["premium_before"], lang), pa=money(ME["premium_after"], lang))
        if ME.get("floor_applied") or ME.get("premium_after") == ME.get("premium_before"):
            total_txt += " " + t("ms_total_floor", lang)
    js["measures"] = {"items": mitems3, "total": {"tech_before": ME.get("tech_before"),
                                                  "tech_after": ME.get("tech_after_all"),
                                                  "premium_before": ME.get("premium_before"),
                                                  "premium_after": ME.get("premium_after"),
                                                  "premium_delta": ME.get("premium_delta"),
                                                  "floor_applied": bool(ME.get("floor_applied")), "text": total_txt},
                      "calibrated": ae.CALIBRATED}
    lists.append(_li(t("an_ms_title", lang), [x["text"] for x in mitems3] or [t("an_ms_none", lang)],
                     {"columns": [t("col_measure", lang), t("col_tech", lang), t("col_act_premium", lang)],
                      "rows": mrows3, "widths": [46, 30, 24]} if mrows3 else None,
                     notes=[total_txt] if total_txt else None))


def _an_summary(D: dict, A: dict, js: dict, lang: str) -> list:
    """Резюме раздела 4 в 5–7 предложениях — из тех же чисел, что в таблицах."""
    out = []
    must, rate_res = D["must"], D["rate"]
    R = A.get("risks") or {}
    items = R.get("items") or []
    if R.get("whole_class"):
        out.append(t("an_sum_whole", lang, cls=must["class_code"]))
    elif items:
        top = items[:3]
        out.append(t("an_sum_risks", lang, what=", ".join(
            t("an_sum_risk_item", lang, name=_peril_label(x["code"], lang, x.get("name_ru"), x.get("class_code")).lower()
              if lang != "en" else _peril_label(x["code"], lang, x.get("name_ru"), x.get("class_code")).lower(),
              share=tx.pct_fixed(x["share_of_net_pct"], lang, 1)) for x in top)))
    fx = (A.get("factors") or {}).get("items") or []
    ups = [f"{_fo(x['factor'], x['option'], lang)} (×{_mult(x['multiplier'], lang)}{_dmark(x, lang, True)})" for x in fx
           if x["direction"] == "up"]
    downs = [f"{_fo(x['factor'], x['option'], lang)} (×{_mult(x['multiplier'], lang)}{_dmark(x, lang, True)})" for x in fx
             if x["direction"] == "down"]
    if ups:
        out.append(t("an_sum_up", lang, what=", ".join(ups)))
    if downs:
        out.append(t("an_sum_down", lang, what=", ".join(downs)))
    elif not ups:
        out.append(t("an_sum_nodown", lang))
    T = A.get("tariff") or {}
    mrate = (A.get("market") or {}).get("rate_pct")
    if rate_res.get("mode") == "tariff" and rate_res.get("applied_pct") is not None:
        key = "an_sum_tariff" if rate_res.get("base_source") == "product_rate" else "an_sum_tariff_tech"
        out.append(t(key, lang, act=pct(rate_res["applied_pct"], lang), base=pct(rate_res["base_pct"], lang),
                     adj=pct(rate_res.get("adj_pct") or 0, lang),
                     level=tx.label(tx.LEVEL_LABELS, D["risk"]["level"], lang),
                     min=t("an_sum_min", lang, m=pct(rate_res["min_pct"], lang)) if rate_res.get("min_pct") else "",
                     tech=pct(T.get("technical_pct"), lang) if T.get("technical_pct") is not None else
                     t("an_t_na_short", lang),
                     market=t("an_sum_market", lang, m=pct(mrate, lang)) if mrate else ""))
    else:
        what = pct(rate_res["applied_pct"], lang) if rate_res.get("applied_pct") is not None else \
            t("rate_undefined", lang)
        out.append(t("an_sum_tariff_other", lang, what=what))
    RET = A.get("retention") or {}
    if RET.get("eml") is not None:
        est = t("an_ret_est" if RET.get("status") == "temporary" else "an_ret_est_rep", lang)
        if not RET.get("known"):
            ret = t("an_sum_ret_na", lang)
        elif RET.get("verdict") == "within":
            ret = t("an_sum_ret_within", lang, est=est)
        elif RET.get("verdict") == "mfl_excess":
            ret = t("an_sum_ret_mfl", lang, limit=money(RET.get("limit"), lang), est=est)
        else:
            ret = t("an_sum_ret_eml", lang, est=est)
        out.append(t("an_sum_loss", lang, eml=money(RET["eml"], lang), mfl=money(RET["mfl"], lang), ret=ret))
    advice = []
    meas = [s for s in (A.get("sensitivity") or {}).get("items") or [] if s["kind"] == "measure"
            and s["direction"] == "down"]
    if meas:
        s = meas[0]
        advice.append(t("an_sum_advice_measure", lang,
                        text=f"{_flabel(s['factor'], lang).lower()}: {_olabel(s['factor'], s['option_to'], lang)}",
                        eff=_spct(s["delta_pct"], lang)))
    else:
        mi = [m for m in (js.get("measures") or {}).get("items") or [] if m.get("effect_pct")]
        if mi:
            advice.append(t("an_sum_advice_measure", lang, text=mi[0]["name"][:1].lower() + mi[0]["name"][1:],
                            eff=_spct(mi[0]["effect_pct"], lang)))
    fr = D["franchise"]
    if fr.get("status") in (None, "none") and fr.get("code") == "fr_not_needed":
        advice.append(t("an_sum_advice_fr_no", lang))
    elif fr.get("needed"):
        advice.append(t("an_sum_advice_fr", lang))
    if advice:
        out.append(t("an_sum_advice", lang, what="; ".join(advice)))
    clar = [_flabel(x["factor"], lang).lower() for x in fx if x["source"] in ("default", "not_set")][:4]
    if clar:
        out.append(t("an_sum_clarify", lang, what=", ".join(clar)))
    S5 = A.get("score") or {}
    if S5.get("available") and len(out) < 7:
        out.append(t("an_sum_score", lang, score=tx._num(float(S5.get("score") or 0), lang, 1),
                     level=tx.label(tx.LEVEL5_LABELS, S5.get("level") or "moderate", lang)))
    return out[:7]


# ================================================================================================
#  Факторы объекта по подгруппам класса (02.10.2026): показ на языке акта
# ================================================================================================
