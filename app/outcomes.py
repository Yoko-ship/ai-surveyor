"""
Вероятность подтверждения запроса: сбор данных, хранение прогноза и факта.

Сам расчёт делает актуарий — app/analysis.py. Этот модуль его НЕ считает и не повторяет:
здесь только «подать материал и убрать за собой».

  * собрать то, что нужно модулю: расчёт движка, оценку стоимости, документы, убытки, разрез
    (продукт, филиал, класс, страховая сумма, факторы, франшиза) — compute();
  * сохранить показанное число рядом с расчётом — save() (пишет analysis.save_probability,
    таблица decision_outcomes) и дописать полный ответ в result_json;
  * достать его для карточек и выгрузок — latest(), summary(), brief();
  * записать факт — record_fact(): вызывается из app/approvals.recalc, когда согласование
    завершилось «согласован» или «отклонён» (внутри — analysis.record_decision, чтобы факт
    ложился ровно в ту строку, где лежит прогноз).

Таблица одна на оба модуля: состав полей — как в analysis.SCHEMA_SQL, плюс result_json
(полный ответ модуля: summary и «что повысит» — их в SCHEMA_SQL нет, а показывать надо).
Вероятность хранится в процентах (0..100) — ровно то число, что видел человек.
Версия методики лежит рядом с числом, поэтому старый прогноз читается так же, как в день
расчёта (правило проекта № 9).
"""
import json
from typing import Optional

from . import analysis, db

NOT_READY = "вероятность ещё не рассчитана"


# --------------------------------------------------------------------------- #
#  Сбор исходных данных по запросу
# --------------------------------------------------------------------------- #

def _attrs(obj) -> dict:
    try:
        return json.loads((obj or {}).get("attributes") or "{}")
    except ValueError:
        return {}


def engine_calc(con, rid: int):
    """
    Пересчитывает запрос движком по сохранённым данным: модулю вероятности нужны все проверки
    и ставки, а в check_results попадают только те правила, что заведены в справочнике rules.
    Не получилось (нет объекта, снят продукт) — возвращает None, причина идёт в журнал.
    """
    from .engine import Input, calculate
    req = db.rows(con, "SELECT * FROM requests WHERE id=?", rid)
    obj = db.rows(con, "SELECT * FROM objects WHERE request_id=? ORDER BY id LIMIT 1", rid)
    if not req or not obj:
        return None
    req, obj = req[0], obj[0]
    attrs = _attrs(obj)
    calc = db.rows(con, "SELECT * FROM calculations WHERE request_id=? ORDER BY id DESC LIMIT 1", rid)
    calc = calc[0] if calc else None
    pcs = db.rows(con, "SELECT class_code FROM product_classes WHERE product_code=? ORDER BY part_no",
                  req.get("product_code") or "")
    if not pcs:
        return None
    perils = [r["peril_code"] for r in db.rows(con, "SELECT peril_code FROM object_perils WHERE object_id=?",
                                               obj["id"])]
    docs = [r["doc_name"] for r in db.rows(con, "SELECT doc_name FROM documents WHERE request_id=? AND received=1",
                                           rid)]
    val = None
    try:
        from .valuation import valuation_for_engine
        val = valuation_for_engine(con, key=attrs.get("object_key"), request_id=rid)
    except Exception as e:                    # оценка стоимости не обязательна для вероятности
        print("вероятность: оценка стоимости не прочитана:", e)
    # ручная ставка: пересчитывать её как «ручную» можно только если она такой и была,
    # иначе движок выдаст замечание, которого в исходном расчёте не было
    expl = {}
    try:
        expl = json.loads((calc or {}).get("explanation") or "{}")
    except ValueError:
        expl = {}
    manual = bool(expl.get("manual"))
    inp = Input(product_code=req["product_code"], class_code=pcs[0]["class_code"],
                object_type=obj["object_type"], value_amount=obj.get("value_amount") or 0,
                sum_insured=obj.get("sum_insured") or 0,
                term_days=int(attrs.get("term_days") or 365),
                factors=attrs.get("factors") or {}, perils_included=perils or None,
                docs_received=docs,
                applied_rate_pct=(calc or {}).get("applied_rate_pct") if manual else None,
                manual_reason=expl.get("manual_reason") or "",
                premium_paid=bool(attrs.get("premium_paid")),
                disclosure_done=bool(attrs.get("disclosure_done")),
                credit=attrs.get("credit"), takaful=bool(attrs.get("takaful")),
                payer_type=attrs.get("payer_type"), valuation=val)
    ref = db.load_reference(con)
    return {"calc": calculate(ref, inp), "request": req, "object": obj, "calculation": calc,
            "valuation": val, "attrs": attrs, "class_code": pcs[0]["class_code"]}


def history_of(con, rid: int, policyholder: Optional[str]) -> Optional[dict]:
    """
    Убытки страхователя. Таблица claims пустая (выгрузки ещё не пришли) — возвращаем None:
    «неизвестно» честнее, чем «убытков нет», иначе модуль поставит плюс за чистую историю.
    """
    try:
        total = con.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    except Exception:
        return None
    if not total:
        return None
    if policyholder:
        rows = db.rows(con, "SELECT paid, claimed FROM claims WHERE request_id IN"
                            " (SELECT id FROM requests WHERE policyholder=?)", policyholder)
    else:
        rows = db.rows(con, "SELECT paid, claimed FROM claims WHERE request_id=?", rid)
    return {"claims": rows}


def context_of(req: dict, obj: dict, attrs: dict, class_code=None) -> dict:
    """Разрез для статистики и для правил: класс страхования берётся из состава продукта."""
    return {"product_code": req.get("product_code"), "branch": req.get("branch"),
            "class_code": class_code, "sum_insured": (obj or {}).get("sum_insured"),
            "factors": attrs.get("factors") or {}, "franchise": (obj or {}).get("franchise")}


def compute(con, rid: int) -> Optional[dict]:
    """Считает вероятность по данным запроса. Ответ — как у analysis.probability, либо None."""
    try:
        data = engine_calc(con, rid)
    except Exception as e:                    # ошибку не глотаем: она идёт в журнал
        db.audit(con, "system", "вероятность не рассчитана", f"request:{rid}", {"ошибка": str(e)})
        return None
    if not data:
        db.audit(con, "system", "вероятность не рассчитана", f"request:{rid}",
                 {"причина": "нет объекта или продукт снят со справочника"})
        return None
    req, obj = data["request"], data["object"]
    ctx = context_of(req, obj, data["attrs"], data["class_code"])
    docs = db.rows(con, "SELECT doc_name, received FROM documents WHERE request_id=?", rid)
    try:
        return analysis.probability(con, calc=data["calc"], valuation=data["valuation"],
                                    documents=docs, history=history_of(con, rid, req.get("policyholder")),
                                    context=ctx)
    except Exception as e:
        db.audit(con, "system", "вероятность не рассчитана", f"request:{rid}", {"ошибка": str(e)})
        return None


# --------------------------------------------------------------------------- #
#  Хранение
# --------------------------------------------------------------------------- #

def save(con, rid: int, result: dict, calculation_id: Optional[int] = None,
         product_code=None, branch=None, class_code=None, verdict=None) -> dict:
    """Записывает показанное число (analysis.save_probability) и полный ответ модуля."""
    if not result:
        return {"saved": False, "note": NOT_READY}
    if calculation_id is None or product_code is None or branch is None:
        r = db.rows(con, "SELECT product_code, branch FROM requests WHERE id=?", rid)
        c = db.rows(con, "SELECT id, verdict FROM calculations WHERE request_id=? ORDER BY id DESC LIMIT 1", rid)
        calculation_id = calculation_id if calculation_id is not None else (c[0]["id"] if c else None)
        product_code = product_code if product_code is not None else (r[0]["product_code"] if r else None)
        branch = branch if branch is not None else (r[0]["branch"] if r else None)
        verdict = verdict or (c[0]["verdict"] if c else None)
        if class_code is None and r:
            pcs = db.rows(con, "SELECT class_code FROM product_classes WHERE product_code=? ORDER BY part_no",
                          r[0]["product_code"] or "")
            class_code = pcs[0]["class_code"] if pcs else None
    out = analysis.save_probability(con, request_id=rid, result=result, calculation_id=calculation_id,
                                    product_code=product_code, branch=branch,
                                    class_code=class_code,
                                    verdict=verdict)
    if out.get("saved"):
        # summary и «что повысит» в SCHEMA_SQL не предусмотрены — держим их рядом, в result_json
        con.execute("UPDATE decision_outcomes SET result_json=? WHERE id ="
                    " (SELECT MAX(id) FROM decision_outcomes WHERE request_id=?)",
                    (json.dumps(result, ensure_ascii=False), rid))
    return out


def compute_and_save(con, rid: int) -> Optional[dict]:
    """Расчёт при отправке на согласование: посчитать и сразу положить рядом с расчётом."""
    res = compute(con, rid)
    if res:
        save(con, rid, res)
    return res


def latest(con, rid: int) -> Optional[dict]:
    """Последний прогноз по запросу или None. Нет таблицы (старая база) — тоже None."""
    try:
        rows = db.rows(con, "SELECT * FROM decision_outcomes WHERE request_id=? ORDER BY id DESC LIMIT 1", rid)
    except Exception:
        return None
    if not rows:
        return None
    row = rows[0]
    for key in ("factors_json", "result_json"):
        try:
            row[key] = json.loads(row[key]) if row.get(key) else None
        except ValueError:
            row[key] = None
    return row


def record_fact(con, rid: int, status: str, decided_by=None, comment=None):
    """Согласование завершилось — факт ложится в ту же строку, где лежит прогноз."""
    try:
        return analysis.record_decision(con, request_id=rid, decision=status,
                                        decided_by=decided_by, comment=comment)
    except Exception as e:                    # факт не записался — согласование это не отменяет
        print("факт решения не записан:", e)
        return {"saved": False, "note": str(e)}


# --------------------------------------------------------------------------- #
#  Что показывать
# --------------------------------------------------------------------------- #

def view(result: dict, row: Optional[dict] = None) -> dict:
    """Единый вид вероятности для API, карточек и выгрузок."""
    row = row or {}
    value = int(round(float(result.get("probability") or 0)))
    has_stop = any(x.get("stop") for x in (result.get("minus") or []))
    return {"ready": True, "probability": value, "percent": value,
            "verdict": result.get("verdict"),
            "verdict_code": result.get("verdict_code") or analysis.verdict_code(value, has_stop),
            "summary": result.get("summary"),
            "minus": result.get("minus") or [], "plus": result.get("plus") or [],
            "how_to_raise": result.get("how_to_raise") or [], "stat": result.get("stat") or {},
            "base": result.get("base"), "cap": result.get("cap"),
            "model_version": result.get("model_version") or analysis.MODEL_VERSION,
            "calibrated": result.get("calibrated", 0),
            "has_stop": has_stop,
            "note": "оценка экспертная, не калибрована",
            "sent_at": row.get("sent_at"), "decision": row.get("decision"),
            "decided_at": row.get("decided_at"), "decided_by": row.get("decided_by"),
            "calculation_id": row.get("calculation_id"),
            "text": "%d %% — вероятность, что запрос подтвердят" % value}


def empty(reason: str = "") -> dict:
    return {"ready": False, "probability": None, "percent": None, "verdict": None, "verdict_code": None,
            "summary": None, "minus": [], "plus": [], "how_to_raise": [], "stat": {},
            "base": None, "cap": None, "model_version": None, "calibrated": 0, "has_stop": False,
            "note": "оценка экспертная, не калибрована", "sent_at": None, "decision": None,
            "decided_at": None, "decided_by": None, "calculation_id": None,
            "text": NOT_READY + (" (" + reason + ")" if reason else "")}


def summary(con, rid: int) -> dict:
    """Что показать в выгрузках и карточках. Прогноза нет — честный текст вместо числа."""
    row = latest(con, rid)
    if not row:
        return empty("запрос ещё не отправляли на согласование")
    res = row.get("result_json")
    if not isinstance(res, dict):
        # строка от старой версии или обрезанная: собираем из того, что есть в таблице
        res = {"probability": row.get("probability"), "verdict": row.get("verdict"),
               "model_version": row.get("model_version"), "calibrated": 0}
        res.update(row.get("factors_json") or {})
        res["summary"] = res.get("summary") or ("Вероятность подтверждения ~%d%%."
                                                % round(float(row.get("probability") or 0)))
    return view(res, row)


def brief(con, rid: int) -> dict:
    """Короткий вид для списков («Мои запросы», «Ждут меня»): число, вердикт, одна строка."""
    s = summary(con, rid)
    return {"ready": s["ready"], "probability": s["probability"], "verdict": s["verdict"],
            "verdict_code": s["verdict_code"],
            "summary": s["summary"] or s["text"], "has_stop": s["has_stop"],
            "model_version": s["model_version"], "calibrated": s["calibrated"],
            "note": s["note"], "decision": s["decision"]}


def line(result_or_summary: dict) -> str:
    """Одна строка для карточки в Telegram: число, вердикт и почему."""
    s = result_or_summary or {}
    if not s.get("probability"):
        return NOT_READY
    return "Вероятность подтверждения: %d %% — %s\n%s" % (
        int(round(s["probability"])), s.get("verdict") or "", s.get("summary") or "")
