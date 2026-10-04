"""Вилка ставки на языке акта: отметки, поправки региона и рынка, источники, строки НАПП."""
import re
from datetime import date
from typing import Optional

from .. import act_engine as ae
from .. import contract_read as cr
from .. import act_texts as tx
from .. import min_rates as mrs
from ..act_texts import money, pct, t

from .regions import region_label
from .view_fmt import _date, _li, _money_na, _mult, _pct_na, _spct
from .view_rows import _row


# --------------------------------------------------------------------------- #
#  Вилка ставки (01.10.2026): показ на языке акта
# --------------------------------------------------------------------------- #

def _sg(x, lang: str) -> str:
    """«+ 15 %» / «− 3,2 %» — слагаемое поправки в формуле «(1 + 15 %)»."""
    v = round(float(x or 0), 2)
    return ("− " if v < 0 else "+ ") + pct(abs(v), lang, 2)


def _fork_src(src: Optional[dict], lang: str) -> Optional[dict]:
    """Источник отметки на языке акта: {title, url, as_of}."""
    if not src:
        return None
    kind = src.get("kind")
    title = src.get("title_ru")
    if kind in ("policy", "regulator", "policy_act") and lang != "ru":
        # название документа в справочнике — по-русски; на других языках — название словами акта и номер приказа
        order = _order_ref_text(title, lang)
        title = t("rf_doc_regulator" if kind == "regulator" else "rf_doc_policy", lang) + (f" — {order}" if order else "")
    if kind == "insurer_min":
        # минимальная ставка страховщика (правка администратора, app/min_rates.py): «… (установлена администратором
        # 02.10.2026, примечание …)»
        title = mrs.source_label(src.get("row") or {}, lang) or title
    elif kind in ("policy", "regulator"):
        title = t("rf_src_policy_part", lang, title=title, code=src.get("product_code") or "") if src.get("part_text") \
            else t("rf_src_policy", lang, title=title, date=_date(src.get("as_of")))
    elif kind == "policy_act":
        title = t("rf_src_policy_act", lang, title=title)
    elif kind == "napp":
        title = t("rf_src_napp", lang, date=_date(src.get("as_of")))
    elif kind == "napp_claims":
        title = t("rf_src_napp_claims", lang, date=_date(src.get("as_of")))
    elif kind == "adjusted":
        title = t("rf_src_adjusted", lang)
    elif kind in ("request", "contract", "technical", "parts", "factors", "objects"):
        title = t("rf_src_" + kind, lang)
    elif kind == "statutory":
        title = t("rf_src_statutory", lang, title=_act_ref_text(title, lang))
    elif kind == "stat" and lang != "ru" and src.get("name"):
        title = f"{str(title or '').split(' — ')[0]} — {src['name']}"     # набор — названием показателя на языке акта
    return {"title": title, "url": src.get("url"), "as_of": src.get("as_of"), "kind": kind}


_LAT = {"А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "К": "K", "Л": "L", "М": "M", "Н": "N", "О": "O",
        "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "X", "Ц": "TS", "Ч": "CH", "Ш": "SH"}


def _order_ref_text(ref: Optional[str], lang: str) -> Optional[str]:
    """Номер и дата приказа из названия версии тарифа («… — Приказ №54-П от 23.09.2025») на языке акта:
    uz — «54-P-son buyruq, 23.09.2025», en — «Order No. 54-P of 23.09.2025»; не нашли — None."""
    m = re.search(r"Приказ\s*№\s*([0-9A-Za-zА-Яа-яЁё\-/]+)\s*(?:от\s*(\d{2}\.\d{2}\.\d{4}))?", str(ref or ""))
    if not m:
        return None
    no = "".join(_LAT.get(ch.upper(), ch) if re.match(r"[А-Яа-яЁё]", ch) else ch for ch in m.group(1))
    dt = m.group(2)
    if lang == "uz":
        return f"{no}-son buyruq" + (f", {dt}" if dt else "")
    if lang == "en":
        return f"Order No. {no}" + (f" of {dt}" if dt else "")
    return m.group(0)


def _act_ref_text(ref: Optional[str], lang: str) -> str:
    """«ПКМ №532» на языке акта: uz — «VMQ №532», en — «CM Resolution No. 532»; иначе — «нормативный акт»."""
    if not ref:
        return t("na", lang)
    if lang == "ru":
        return ref
    s = re.sub(r"ПКМ", {"uz": "VMQ", "en": "CM Resolution"}[tx.lang_of(lang)], str(ref))
    if lang == "en":
        s = s.replace("№", "No. ")
    return s if not re.search(r"[А-Яа-яЁё]", s) else t("rf_doc_regulation", lang)


def _fork_note(m: dict, lang: str, F: dict) -> str:
    code, p = m.get("note") or "", m.get("params") or {}
    if code == "act":
        return t("rf_n_act", lang, adj=_spct(p.get("level") or 0, lang, 2))
    if code in ("adjusted", "adjusted_min"):
        return t("rf_n_" + code, lang, reg=_sg(p.get("region"), lang), mkt=_sg(p.get("market"), lang))
    if code in ("market", "market_below_min"):
        return t("rf_n_" + code, lang, date=_date((m.get("source") or {}).get("as_of")))
    if code in ("factors", "factors_min"):
        return t("rf_n_" + code, lang, mult=_mult(p.get("mult") or 1, lang))
    return t("rf_n_" + code, lang) if code else ""


def _fork_num(x, lang: str) -> str:
    """Число процента без знака % (для строки «0,35 – 0,42 – 0,695 %»)."""
    return pct(x, lang).replace("%", "").replace(tx.NBSP, "").strip() if x is not None else "—"


def _stat_name(i: dict, lang: str) -> str:
    return tx.label(tx.STAT_LABELS, i["id"], lang) if i["id"] in tx.STAT_LABELS else (i.get("name_ru") or i["id"])


def _fork_region_lines(R: dict, lang: str, cls: Optional[str]) -> tuple:
    """(строки поправки региона, источники) на языке акта."""
    pct_txt = _spct(R.get("pct") or 0, lang, 2) if R.get("pct") else pct(0, lang)
    lines, srcs = [], []
    head = t("rf_reg_title", lang, pct=pct_txt)
    if R.get("reason"):
        why = {"no_rules": t("rf_reg_r_no_rules", lang, cls=cls or ""), "kind": t("rf_reg_r_kind", lang, cls=cls or ""),
               "region_unknown": t("rf_reg_r_region_unknown", lang, region=R.get("region_requested") or ""),
               "no_regional": t("rf_reg_r_no_regional", lang),
               "zero_weight": t("rf_reg_r_zero_weight", lang),
               "republic": t("rf_reg_r_republic", lang), "outside": t("rf_reg_r_outside", lang)}.get(R["reason"], "")
        lines.append(f"{head} — {t('rf_reg_none', lang)} ({why})")
    else:
        lo, hi = R["bounds"]
        txt = f"{head} — " + t("rf_reg_how", lang, sens=_mult(R["sensitivity"], lang), lo=_spct(lo, lang, 2),
                               hi=_spct(hi, lang, 2))
        if R.get("clamped"):
            txt += "; " + t("rf_reg_raw", lang, raw=_spct(R["raw_pct"], lang, 2), lim=_spct(hi if R["clamped"] == "max"
                                                                                           else lo, lang, 2))
        lines.append(txt)
    per1000 = {"ru": "на 1 000 жителей", "uz": "1 000 aholiga", "en": "per 1,000 people"}[tx.lang_of(lang)]
    for i in R.get("indicators") or []:
        name = tx.label(tx.STAT_LABELS, i["id"], lang) if i["id"] in tx.STAT_LABELS else i.get("name_ru") or i["id"]
        if i.get("used"):
            unit = tx.label(tx.STAT_UNITS, i.get("value_unit_ru"), lang) if i.get("value_unit_ru") in tx.STAT_UNITS \
                else (i.get("value_unit_ru") or "")
            if i.get("kind") == "count_pc":
                unit = f"{unit} {per1000}"
            line = t("rf_reg_ind", lang, name=name, period=i.get("period") or "", reg=_mult(i["region_value"], lang),
                     cty=_mult(i["country_value"], lang), unit=unit, ratio=_mult(i["ratio"], lang),
                     effect=_spct(i["effect_pct"], lang, 2))
            cav = _caveat(i["id"], lang)
            lines.append(line + (f" ({t('rf_caveat', lang, text=cav)})" if cav else ""))
            s = _fork_src(dict(i["source"], name=name) if i.get("source") else None, lang)
            if s:
                srcs.append(s)
        else:
            lines.append(t("rf_reg_ind_off", lang, name=name, why=tx.label(tx.FORK_WHY_LABELS, i.get("why"), lang)))
    return lines, srcs


def _territory(D: dict, lang: str) -> Optional[dict]:
    """Территория страхования: у класса 7 (грузы, в том числе часть 7 комплексного продукта) — всегда; у остальных —
    только если сотрудник ввёл её текстом при обычном регионе. {"text", "note", "code", "scope"} или None."""
    must = D["must"]
    classes = {must.get("class_code")} | {p.get("class_code") for p in (D.get("parts") or {}).get("items") or []}
    scope = must.get("region_scope")
    text = must.get("region_text") or region_label(must, lang)
    if "7" not in classes and not (must.get("region_text") and scope != "outside"):
        return None
    return {"text": text, "note": t("reg_note_" + scope, lang) if scope else None,
            "code": must.get("region_code"), "scope": scope, "as_entered": bool(must.get("region_text"))}


def _caveat(iid: str, lang: str) -> Optional[str]:
    """Оговорка показателя региона: что он измеряет на самом деле (все ДТП, все кражи, жилой фонд)."""
    return tx.label(tx.STAT_CAVEATS, iid, lang) if iid in tx.STAT_CAVEATS else None


def _pack_choice_text(pc: Optional[dict], lang: str, cls: Optional[str] = None, prefix: str = "rf_mkt_pack_") -> Optional[str]:
    """Какая строка НАПП взята для комплексного продукта и почему (exact | nearest | class) — на языке акта."""
    if not pc or not pc.get("how"):
        return None
    kw = {"pack": ",".join(pc.get("pack_classes") or []), "code": pc.get("product_code") or "",
          "classes": ",".join(pc.get("classes") or []), "cls": pc.get("class_used") or cls or ""}
    txt = t(prefix + pc["how"], lang, **kw)
    if pc.get("subclass_note"):
        txt += t(prefix + "sub", lang, sub=", ".join(x for x in pc.get("product_classes") or []
                                                      if not str(x).isdigit()))
    return txt


def _pc_local(pc: Optional[dict], lang: str, cls: Optional[str] = None) -> Optional[dict]:
    """Выбор строки НАПП в ответе: пояснение note в снимке по-русски — на языке акта (как pack_note)."""
    if not isinstance(pc, dict) or tx.lang_of(lang) == "ru":
        return pc
    return dict(pc, note=_pack_choice_text(pc, lang, cls) or None)


def _fork_market_lines(M: dict, lang: str, cls: Optional[str] = None) -> list:
    pct_txt = _spct(M.get("pct") or 0, lang, 2) if M.get("pct") else pct(0, lang)
    head = t("rf_mkt_title", lang, pct=pct_txt)
    r = M.get("reason")
    kw = {"act": pct(M.get("act_pct"), lang), "market": pct(M.get("market_rate_pct"), lang),
          "lr": tx.pct_fixed(M.get("loss_ratio_pct"), lang, 1) if M.get("loss_ratio_pct") is not None else t("na", lang),
          # убыточность взята за полный год — дата его среза (01.01 следующего года), а не последнего среза
          "date": (_date(f"{int(_fy_year(M.get('full_year_period'))) + 1}-01-01")
                   if M.get("basis") == "full_year" and _fy_year(M.get("full_year_period"))
                   else _date(M.get("as_of"))), "pct": pct_txt,
          "thr": tx.pct_fixed(M.get("threshold") if M.get("threshold") is not None
                              else ((M.get("steps") or [[0]])[0][0]), lang, 0)}
    body = t("rf_mkt_" + (r or "no_data") if r in ("applied", "act_not_below", "lr_below", "no_data")
             else "rf_mkt_no_data", lang, **kw)
    lines = [f"{head} — {body}"]
    if M.get("full_year_switch"):
        # скачок убыточности за неполный год: ступень взята по полному году (rate_fork.market.basis)
        lines.append(t("rf_mkt_fy_switch", lang, last=tx.pct_fixed(M.get("loss_ratio_last_pct"), lang, 1),
                       fy=tx.pct_fixed(M.get("loss_ratio_full_year_pct"), lang, 1),
                       year=_fy_year(M.get("full_year_period")) or "—"))
    if M.get("capped"):
        lines.append(t("rf_mkt_capped", lang, market=pct(M.get("market_rate_pct"), lang),
                       pct=_spct(M.get("effective_pct") or 0, lang, 2) if M.get("effective_pct") else pct(0, lang)))
    steps = "; ".join(t("rf_mkt_step", lang, thr=tx.pct_fixed(s[0], lang, 0), pct=_spct(s[1], lang, 2))
                      for s in M.get("steps") or [])
    if steps:
        lines.append(t("rf_mkt_steps", lang, steps=steps, basis=t("rf_mkt_basis_" + (M.get("basis") or "last"), lang)))
    if M.get("cap_at_market"):
        lines.append(t("rf_mkt_cap_on", lang))
    pcx = _pack_choice_text(M.get("pack_choice"), lang, cls)
    if pcx:
        lines.append(pcx)
    elif M.get("pack"):
        lines.append(t("rf_mkt_pack", lang))
    if (M.get("pack_choice") or {}).get("how") in ("exact", "nearest"):
        # полный год пакета и одиночные строки классов продукта (правило проекта № 5 — по каждому классу)
        if M.get("loss_ratio_full_year_pct") is not None and M.get("rate_full_year_pct") is not None:
            lines.append(t("rf_mkt_pack_fy", lang, year=_fy_year(M.get("full_year_period")) or "—",
                           rate=pct(M.get("rate_full_year_pct"), lang),
                           lr=tx.pct_fixed(M.get("loss_ratio_full_year_pct"), lang, 1)))
        cr = _class_rows_text(M.get("class_rows"), lang)
        if cr:
            lines.append(cr)
    return lines


def _fork_unit(i: dict, lang: str) -> Optional[str]:
    """Единица показателя региона на языке акта (как в строке «Поправка региона»)."""
    vu = i.get("value_unit_ru")
    if vu is None:
        return i.get("unit_ru")
    unit = tx.label(tx.STAT_UNITS, vu, lang) if vu in tx.STAT_UNITS else (vu if tx.lang_of(lang) == "ru" else "")
    if i.get("kind") == "count_pc":
        unit = (unit + " " + t("rf_per1000", lang)).strip()
    return unit or None


def _fy_text(period: Optional[str], lang: str) -> Optional[str]:
    """«2025 год» → на языке акта («2025 yil», «2025»)."""
    y = _fy_year(period)
    if not y:
        return period if tx.lang_of(lang) == "ru" else None
    return t("rf_fy_period", lang, year=y)


def _fy_year(period: Optional[str]) -> Optional[str]:
    """«2025 год» → «2025» (год полного среза НАПП)."""
    d = "".join(ch for ch in str(period or "") if ch.isdigit())[:4]
    return d or None


def _class_rows_text(rows: Optional[list], lang: str) -> Optional[str]:
    """«по одиночным строкам классов: класс 3 — 14,6 %, класс 14 — 24,2 % (убыточность, срез …; ставка: …)»."""
    rows = [r for r in rows or [] if r.get("class_code")]
    if not rows:
        return None
    lrs, rates = [], []
    for r in rows:
        if r.get("available") and r.get("loss_ratio_pct") is not None:
            lrs.append(t("rf_mkt_class_row", lang, cls=r["class_code"], v=tx.pct_fixed(r["loss_ratio_pct"], lang, 1)))
        else:
            lrs.append(t("rf_mkt_class_row_na", lang, cls=r["class_code"]))
        if r.get("available") and r.get("rate_pct") is not None:
            rates.append(t("rf_mkt_class_row", lang, cls=r["class_code"], v=pct(r["rate_pct"], lang)))
    date = next((r.get("date") for r in rows if r.get("date")), None)
    return t("rf_mkt_class_rows", lang, rows=", ".join(lrs), date=_date(date) or "—",
             rates=", ".join(rates) or t("na", lang))


def _fork_view(F: Optional[dict], lang: str, cls: Optional[str] = None) -> dict:
    """
    Вилка ставки одного класса (или справочная договора) на языке акта:
    {"json": блок rate_fork ответа, "list": список раздела 4 с таблицей, "summary": одна фраза, "row": строка
    раздела 4 рядом с премией или None, "overview": «0,35 – 0,42 – 0,695 %» для страницы скоринга}.
    """
    lang = tx.lang_of(lang)
    NA = t("na", lang)
    if not F:                                # акты до 01.10.2026 — без вилки
        return {"json": {"available": False, "reason": "old_act", "marks": [], "calibrated": ae.CALIBRATED},
                "list": None, "summary": None, "row": None, "overview": None}
    marks = []
    for m in F.get("marks") or []:
        lab = t("rf_m_" + m["code"], lang)
        marks.append({"code": m["code"], "label": lab, "rate_pct": m.get("rate_pct"), "premium": m.get("premium"),
                      "is_recommended": bool(m.get("is_recommended")), "source": _fork_src(m.get("source"), lang),
                      "note": _fork_note(m, lang, F)})
    by = {m["code"]: m for m in marks}
    adj = F.get("adjustments") or {}
    R = adj.get("region") or F.get("region_data")
    MK = adj.get("market")
    how, srcs = [], []
    reason = F.get("reason")
    # одна фраза вывода
    if reason in ("statutory", "statutory_undefined"):
        ref_txt = ((F.get("marks") or [{}])[0].get("source") or {}).get("title_ru") if F.get("marks") else None
        summary = t("rf_sum_statutory", lang, rate=pct(by["act"]["rate_pct"], lang), ref=_act_ref_text(ref_txt, lang)) \
            if "act" in by else t("rf_sum_statutory_na", lang, ref=NA)
    elif reason == "undefined":
        summary = t("rf_sum_undefined", lang, market=pct(by["market"]["rate_pct"], lang)) if "market" in by \
            else t("rf_sum_undefined_nomarket", lang)
    elif reason == "error":
        summary = t("rf_sum_error", lang)
    elif reason == "parts_reference":
        summary = t("rf_sum_objects" if F.get("objects") else "rf_sum_parts", lang,
                    n=F.get("count") or 0, min=pct((by.get("min") or {}).get("rate_pct"), lang),
                    act=pct((by.get("act") or {}).get("rate_pct"), lang),
                    adj=pct((by.get("adjusted") or {}).get("rate_pct"), lang))
    else:
        rec = by[F["recommended"]["code"]]
        base_kw = {"min": pct((by.get("min") or {}).get("rate_pct"), lang), "rec": pct(rec["rate_pct"], lang)}
        summary = t("rf_sum", lang, market=pct(by["market"]["rate_pct"], lang), **base_kw) if "market" in by \
            else t("rf_sum_nomarket", lang, **base_kw)
        if F["recommended"]["code"] == "act" and "adjusted" in by and by["adjusted"]["rate_pct"] != rec["rate_pct"]:
            summary += t("rf_sum_adj", lang, adj=pct(by["adjusted"]["rate_pct"], lang))
        if F["recommended"]["code"] == "adjusted" and by["act"]["rate_pct"] != rec["rate_pct"]:
            summary += t("rf_sum_act", lang, act=pct(by["act"]["rate_pct"], lang))
    for code in ("request", "contract"):
        if code in by:
            summary += t("rf_sum_doc", lang, what=by[code]["label"], rate=pct(by[code]["rate_pct"], lang),
                         pos=tx.label(tx.FORK_POS_LABELS, (F.get("position") or {}).get(code) or "none", lang)) \
                if reason not in ("parts_reference",) else ""
    # поправки
    reg_js = mkt_js = None
    # парк ТС: поправки региона и рынка — общие для всех объектов, показываются у договора
    if R and F.get("available") and (reason != "parts_reference" or F.get("objects")):
        rl, rs_ = _fork_region_lines(R, lang, cls)
        how += rl
        srcs += rs_
        reg_js = {"pct": R.get("pct"), "raw_pct": R.get("raw_pct"),
                  "clamped": R.get("clamped"), "bounds": R.get("bounds"), "sensitivity": R.get("sensitivity"),
                  "reason": R.get("reason"), "region_key": R.get("region_key"),
                  "indicators": [{"id": i["id"], "name": tx.label(tx.STAT_LABELS, i["id"], lang)
                                  if i["id"] in tx.STAT_LABELS else i.get("name_ru"),
                                  "region_value": i.get("region_value"), "country_value": i.get("country_value"),
                                  "ratio": i.get("ratio"), "effect_pct": i.get("effect_pct"), "period": i.get("period"),
                                  "unit": _fork_unit(i, lang), "unit_ru": i.get("unit_ru"),
                                  "source": _fork_src(dict(i["source"], name=_stat_name(i, lang))
                                                                  if i.get("source") else None, lang),
                                  "used": bool(i.get("used")), "why": i.get("why"), "caveat": _caveat(i["id"], lang),
                                  "why_text": tx.label(tx.FORK_WHY_LABELS, i["why"], lang) if i.get("why") else None}
                                 for i in R.get("indicators") or []],
                  "text": rl[0], "lines": rl, "calibrated": ae.CALIBRATED}
    if MK:
        ml = _fork_market_lines(MK, lang, cls)
        how += ml
        mds = _fork_src(MK.get("source"), lang)
        mkt_js = {"pct": MK.get("pct"), "reason": MK.get("reason"), "loss_ratio_pct": MK.get("loss_ratio_pct"),
                  "capped": bool(MK.get("capped")), "effective_pct": MK.get("effective_pct"),
                  "cap_at_market": bool(MK.get("cap_at_market")),
                  "loss_ratio_basis": MK.get("basis"), "market_rate_pct": MK.get("market_rate_pct"),
                  "threshold": MK.get("threshold"), "steps": MK.get("steps"), "as_of": MK.get("as_of"),
                  "row_key": MK.get("row_key"), "pack_choice": _pc_local(MK.get("pack_choice"), lang, cls),
                  # 01.10.2026: скачок убыточности за неполный год — ступень по полному году (rate_fork.market.basis)
                  "basis": MK.get("basis"), "full_year_switch": bool(MK.get("full_year_switch")),
                  "loss_ratio_last_pct": MK.get("loss_ratio_last_pct"),
                  "loss_ratio_full_year_pct": MK.get("loss_ratio_full_year_pct"),
                  "rate_full_year_pct": MK.get("rate_full_year_pct"),
                  # «2025 год» снимка — на языке акта; год числом — full_year
                  "full_year_period": _fy_text(MK.get("full_year_period"), lang),
                  "full_year": _fy_year(MK.get("full_year_period")),
                  "class_rows": list(MK.get("class_rows") or []),
                  "pack_note": _pack_choice_text(MK.get("pack_choice"), lang, cls),
                  "source": mds, "text": ml[0], "lines": ml, "calibrated": ae.CALIBRATED}
    if F.get("available") and reason != "parts_reference":
        how.append(t("rf_mode_" + (F.get("mode") or "reference"), lang))
        if F.get("franchise_applied") and F.get("premium_final") is not None:
            how.append(t("rf_fr_note", lang, premium=money(F["premium_final"], lang)))
        if F.get("rate_type") == "fixed":
            how.append(t("rf_fixed_note", lang, days=F.get("term_days")))
        how.append(t("rf_calibrated", lang))
    # источники: отметки и показатели (без повторов)
    seen, src_lines = set(), []
    for s in [m["source"] for m in marks if m.get("source")] + srcs:
        if s.get("kind") in ("adjusted", "policy_act", "request", "contract", "technical", "parts", "factors",
                             "objects"):
            continue
        key = (s["title"], s.get("url"))
        if key in seen:
            continue
        seen.add(key)
        src_lines.append(t("rf_src_line", lang, title=s["title"], url=s["url"]) if s.get("url")
                         else t("rf_src_line", lang, title=s["title"], url="").rstrip(" —"))
    rows = []
    for m in marks:
        lab = m["label"] + (f" — {t('rf_recommended', lang)}" if m["is_recommended"] else "")
        rows.append([lab, _pct_na(m["rate_pct"], lang),
                     _money_na(m["premium"], lang), m["note"],
                     (m["source"] or {}).get("title") or ""])
    table = {"columns": [t("rf_col_mark", lang), t("rf_col_rate", lang), t("rf_col_premium", lang),
                         t("rf_col_why", lang), t("rf_col_src", lang)], "rows": rows, "widths": [18, 10, 15, 32, 25]}
    li = _li(t("rf_title", lang), [], table if rows else None, src_lines, [summary] + how)
    rec = F.get("recommended")
    js = {"available": bool(F.get("available")), "reason": reason, "mode": F.get("mode"), "title": t("rf_title", lang),
          "unit": t("rf_unit", lang), "marks": marks,
          "adjustments": {"region": reg_js, "market": mkt_js} if (reg_js or mkt_js) else None,
          "recommended": dict(rec) if rec else None, "position": dict(F.get("position") or {}),
          "summary": summary, "how": how, "sources": [s for s in [m["source"] for m in marks if m.get("source")] + srcs],
          "reference_only": bool(F.get("reference_only")), "term_days": F.get("term_days"),
          # премия акта с учётом применённой франшизы (та же, что premium.amount)
          "premium_final": F.get("premium_final"), "franchise_applied": bool(F.get("franchise_applied")),
          "rate_type": F.get("rate_type") or "annual",
          "table": table, "calibrated": ae.CALIBRATED}
    row = None
    if F.get("available") and reason != "parts_reference" and "adjusted" in by:
        if F["recommended"]["code"] == "act":
            row = _row(t("rf_row_adjusted", lang), pct(by["adjusted"]["rate_pct"], lang),
                       t("rf_row_adjusted_note", lang, premium=money(by["adjusted"]["premium"], lang)))
        else:
            row = _row(t("rf_row_act", lang), pct(by["act"]["rate_pct"], lang),
                       t("rf_row_act_note", lang, premium=money(by["act"]["premium"], lang)))
    ov_rec = (rec or {}).get("rate_pct")
    ov_min = (by.get("min") or {}).get("rate_pct")
    ov_mkt = (by.get("market") or {}).get("rate_pct")
    overview = None if ov_rec is None and ov_min is None and ov_mkt is None else \
        f"{_fork_num(ov_min, lang)} – {_fork_num(ov_rec, lang)} – {_fork_num(ov_mkt, lang)}" + \
        ("%" if lang == "en" else tx.NBSP + "%")
    return {"json": js, "list": li, "summary": summary, "row": row, "overview": overview}


def _fork_parts_view(D: dict, PV: dict, lang: str) -> dict:
    """Договор из частей: вилка по каждой части (таблица) и справочная вилка договора."""
    items = (D.get("parts") or {}).get("items") or []
    cv = _fork_view(D.get("rate_fork"), lang)
    NA = t("na", lang)
    rows, notes, src_lines = [], [], []
    for p, pj in zip(items, PV["json"]["items"]):
        fv = _fork_view(p.get("rate_fork"), lang, p["class_code"])
        pj["rate_fork"] = fv["json"]
        by = {m["code"]: m for m in fv["json"]["marks"]}
        adj = fv["json"].get("adjustments") or {}
        cell = lambda c: (pct(by[c]["rate_pct"], lang) + (" (" + t("rf_rec_short", lang) + ")" if by[c]["is_recommended"] else "")) if c in by else "—"  # noqa
        rows.append([pj["label"], cell("min"), cell("act"), cell("adjusted"), cell("market"),
                     " / ".join(_spct(x, lang, 2) if x else pct(0, lang)
                                for x in ((adj.get("region") or {}).get("pct"), (adj.get("market") or {}).get("pct")))
                     if fv["json"]["available"] else "—"])
        notes.append(t("rf_part_line", lang, part=pj["label"], text=fv["summary"]))
        skip = (t("rf_calibrated", lang), t("rf_mode_reference", lang), t("rf_mode_apply", lang))
        notes += [t("rf_part_line", lang, part=t("pt_part_short", lang, n=p["index"]), text=x)
                  for x in fv["json"]["how"] if x not in skip]
        for s in fv["list"]["sources"] if fv["list"] and fv["list"].get("sources") else []:
            if s not in src_lines:
                src_lines.append(s)
    cby = {m["code"]: m for m in cv["json"]["marks"]}
    rows.append([t("rf_contract_row", lang)] + [pct(cby[c]["rate_pct"], lang) if c in cby else "—"
                                                 for c in ("min", "act", "adjusted")] + ["—", "—"])
    notes += [t("rf_mode_" + (cv["json"].get("mode") or "reference"), lang), t("rf_calibrated", lang)]
    table = {"columns": [t("rf_col_part", lang), t("rf_m_min", lang), t("rf_m_act", lang), t("rf_m_adjusted", lang),
                         t("rf_m_market", lang), "±"], "rows": rows, "widths": [26, 13, 13, 16, 14, 18]}
    js = dict(cv["json"], parts=[{"index": p["index"], "class_code": p["class_code"], "summary": pj["rate_fork"]["summary"],
                                  "recommended": pj["rate_fork"].get("recommended")}
                                 for p, pj in zip(items, PV["json"]["items"])])
    return {"json": js, "list": _li(t("rf_parts_title", lang), [], table, src_lines, [cv["summary"]] + notes),
            "summary": cv["summary"], "overview": cv["overview"] or NA}
