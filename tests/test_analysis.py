"""
Контрольные примеры для модуля «вероятность подтверждения» (app/analysis.py).

Запуск из корня проекта (pytest в sandbox\\.venv не установлен — обычные assert, как в других тестах):
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_analysis.py

Рабочая база data\\surveyor.db не используется: справочник движка собирается в памяти,
а статистика решений пишется во временный файл tests\\data\\analysis_test.db (удаляется в конце).
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import analysis as A                      # noqa: E402
from app.engine import Input, Reference, calculate  # noqa: E402

TMP_DB = ROOT / "tests" / "data" / "analysis_test.db"

DOCS = ["Заявление-анкета на страхование", "Документ о праве на объект", "Справка об убытках за 3 года"]


def ref() -> Reference:
    """Маленький справочник вместо базы: склад, класс 8, продукт 0807 с минимумом 0,05%."""
    return Reference(
        base_rates={("8", "Склад"): 0.15},
        coefficients={
            ("construction", "mixed"): {"name": "Смешанная", "multiplier": 1.0,
                                        "factor_name": "Конструкция", "class_code": "8"},
            ("protection", "alarm"): {"name": "Сигнализация", "multiplier": 0.9,
                                      "factor_name": "Защита", "class_code": "8"},
            ("seismic", "z7"): {"name": "7 баллов", "multiplier": 1.0,
                                "factor_name": "Сейсмозона", "class_code": "8"},
            ("franchise", "f0"): {"name": "Без франшизы", "multiplier": 1.0,
                                  "factor_name": "Франшиза", "class_code": "8"},
            ("franchise", "f1"): {"name": "1% страховой суммы", "multiplier": 0.85,
                                  "factor_name": "Франшиза", "class_code": "8"},
        },
        perils={
            "fire": {"class_code": "8", "share": 0.5, "name": "Пожар", "cat": 0},
            "flood": {"class_code": "8", "share": 0.2, "name": "Затопление", "cat": 0},
            "earthquake": {"class_code": "8", "share": 0.2, "name": "Землетрясение", "cat": 1},
            "nuclear": {"class_code": "8", "share": 0.1, "name": "Ядерная энергия", "cat": 1},
        },
        load_share=0.3,
        min_rates={"0807": {"company": {None: 0.05}, "regulator": {}}},
        product_classes={"0807": ["8"]},
        checklists=[{"scope_type": "всегда", "scope_code": None, "doc_name": d, "required": 1} for d in DOCS],
        financials={"own_funds": 100e9, "reserves": 50e9, "report_date": "2026-07-01"},
        measures=[],
        market={},
        class_factors={"8": ["construction", "protection", "seismic", "franchise"]},
        products={"0807": {"name": "Имущество предприятий", "pricing_mode": "ставка"}},
    )


def warehouse(**over) -> Input:
    """Склад 4,2 млрд: чистый запрос, франшиза 1%, полный пакет документов, землетрясение исключено."""
    args = dict(product_code="0807", class_code="8", object_type="Склад",
                value_amount=4.2e9, sum_insured=4.2e9, term_days=365,
                factors={"construction": "mixed", "protection": "alarm", "seismic": "z7",
                         "franchise": "f1"},
                perils_included=["fire", "flood"],
                docs_received=list(DOCS), premium_paid=True, disclosure_done=True)
    args.update(over)
    return Input(**args)


def ctx(inp: Input) -> dict:
    return {"product_code": inp.product_code, "branch": "Ташкент", "class_code": inp.class_code,
            "sum_insured": inp.sum_insured, "factors": inp.factors}


# ---------- 1. чистый запрос ----------

def test_clean_request_high():
    inp = warehouse()
    calc = calculate(ref(), inp)
    res = A.probability(None, calc=calc, valuation={"value": 4.2e9, "confirmed_by": "Каримов А."},
                        documents=[{"doc_name": d, "received": 1} for d in DOCS],
                        history={"claims": []}, context=ctx(inp))
    assert not res["minus"], res["minus"]
    assert res["probability"] >= 85, res
    assert res["probability"] <= A.CEIL
    assert res["calibrated"] == 0
    assert "Вероятность подтверждения" in res["summary"]
    print("чистый склад 4,2 млрд: ставка %.4f%% → вероятность %d%% (%s)"
          % (calc["rates"]["applied_pct"], res["probability"], res["verdict"]))
    return res


# ---------- 2. стоп-проверка ----------

def test_stop_check_caps_at_10():
    """Страховая сумма 5 млрд при стоимости 4,2 млрд — ГК ст. 938, стоп."""
    inp = warehouse(sum_insured=5.0e9)
    calc = calculate(ref(), inp)
    assert calc["verdict"] == "отклонено"
    res = A.probability(None, calc=calc, valuation=None, documents=[], history=None, context=ctx(inp))
    assert res["probability"] <= 10, res
    assert any(x.get("stop") for x in res["minus"])
    assert any("938" in (x.get("norm") or "") for x in res["minus"]), res["minus"]
    print("сумма 5 млрд при стоимости 4,2 млрд: вероятность %d%% — %s"
          % (res["probability"], res["minus"][0]["text"][:60]))


# ---------- 3. ставка ниже минимума ----------

def test_below_min_rate_caps_at_10():
    """Минимум по продукту 0,05%, агент ставит 0,01% — стоп по тарифной политике."""
    inp = warehouse(applied_rate_pct=0.01, manual_reason="просьба филиала")
    calc = calculate(ref(), inp)
    assert any(c["rule"] == "min_rate" and c["status"] == "stop" for c in calc["checks"])
    res = A.probability(None, calc=calc, documents=[], history=None, context=ctx(inp))
    assert res["probability"] <= 10, res
    norm = [x["norm"] for x in res["minus"] if x.get("rule") == "min_rate"]
    assert norm and "тарифная политика" in norm[0]
    print("ставка 0,01%% при минимуме 0,05%%: вероятность %d%%" % res["probability"])


# ---------- 4. документы ----------

def test_missing_documents_lower():
    full = test_clean_request_high()
    inp = warehouse(docs_received=[DOCS[0]])
    calc = calculate(ref(), inp)
    res = A.probability(None, calc=calc, valuation={"value": 4.2e9, "confirmed_by": "Каримов А."},
                        documents=[{"doc_name": DOCS[0], "received": 1}],
                        history={"claims": []}, context=ctx(inp))
    assert res["probability"] < full["probability"], (res["probability"], full["probability"])
    doc_minus = [x for x in res["minus"] if x["rule"] == "docs_missing"]
    assert doc_minus and doc_minus[0]["delta"] == -2 * A.W_DOC_EACH, doc_minus
    assert any("Приложите недостающие документы" in h["text"] for h in res["how_to_raise"])
    print("не хватает 2 документов: %d%% против %d%% (минус %d п.п.)"
          % (res["probability"], full["probability"], -doc_minus[0]["delta"]))


# ---------- 5. сумма выше оценки стоимости ----------

def test_sum_over_valuation_lowers():
    """Оценка 3,5 млрд, сумма 4,2 млрд — превышение 20%, больше порога расхождения 15%."""
    inp = warehouse()
    calc = calculate(ref(), inp)
    res = A.probability(None, calc=calc, valuation={"value": 3.5e9, "confirmed_by": None},
                        documents=[{"doc_name": d, "received": 1} for d in DOCS],
                        history={"claims": []}, context=ctx(inp))
    item = [x for x in res["minus"] if x["rule"] == "sum_vs_valuation"]
    assert item and item[0]["delta"] == -A.W_SUM_OVER_VALUATION_BIG, res["minus"]
    assert "938" in item[0]["norm"]
    assert res["probability"] < test_clean_request_high()["probability"]
    print("сумма 4,2 млрд при оценке 3,5 млрд: %d%% (минус %d п.п., превышение 20%%)"
          % (res["probability"], A.W_SUM_OVER_VALUATION_BIG))


# ---------- 6. убытки в истории и отсутствие франшизы ----------

def test_claims_and_no_franchise():
    inp = warehouse(factors={"construction": "mixed", "protection": "alarm", "seismic": "z7",
                             "franchise": "f0"})
    calc = calculate(ref(), inp)
    res = A.probability(None, calc=calc, valuation=None,
                        documents=[{"doc_name": d, "received": 1} for d in DOCS],
                        history={"claims": [{"paid": 1e8}, {"paid": 2e8}]}, context=ctx(inp))
    fr = [x for x in res["minus"] if x["rule"] == "no_franchise"]
    cl = [x for x in res["minus"] if x["rule"] == "loss_history"]
    assert fr and fr[0]["delta"] == -A.W_FRANCHISE
    assert cl and cl[0]["delta"] == -2 * A.W_CLAIM_EACH
    assert 0 <= res["probability"] <= 100
    print("без франшизы и с двумя убытками (300 млн выплат): %d%%" % res["probability"])


# ---------- 7. коридор ----------

def test_corridor():
    """Сколько бы минусов ни набралось, итог остаётся в коридоре 3–97%."""
    inp = warehouse(docs_received=[], premium_paid=False, disclosure_done=False,
                    perils_included=["fire", "flood", "earthquake"],
                    factors={"construction": "mixed", "protection": "alarm", "seismic": "z8",
                             "franchise": "f0"})
    calc = calculate(ref(), inp)
    res = A.probability(None, calc=calc, valuation={"value": 2.0e9},
                        documents=[], history={"claims_count": 5, "paid_total": 9e8}, context=ctx(inp))
    assert A.FLOOR <= res["probability"] <= A.CEIL, res["probability"]
    assert res["probability"] < 30, res
    print("всё плохо сразу: %d%% (минусов %d) — «%s»"
          % (res["probability"], len(res["minus"]), res["verdict"]))


# ---------- 8. статистика прошлых решений ----------

def tmp_con(rows_approved=0, rows_rejected=0) -> sqlite3.Connection:
    if TMP_DB.exists():
        TMP_DB.unlink()
    con = sqlite3.connect(TMP_DB)
    con.executescript(A.SCHEMA_SQL)
    n = 0
    for decision, count in (("одобрил", rows_approved), ("отклонил", rows_rejected)):
        for _ in range(count):
            n += 1
            con.execute("INSERT INTO decision_outcomes (request_id, product_code, branch, probability,"
                        " verdict, model_version, sent_at, decision, decided_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        (n, "0807", "Ташкент", 80.0 if decision == "одобрил" else 40.0,
                         "на утверждение", A.MODEL_VERSION, "2026-09-01", decision, "2026-09-02"))
    con.commit()
    return con


def test_stat_not_applied_under_20():
    con = tmp_con(rows_approved=8, rows_rejected=4)      # 12 решений — мало
    st = A.stat_adjustment(con, product_code="0807", branch="Ташкент")
    assert st["applied"] is False and st["n"] == 12 and st["delta"] == 0, st
    assert "12 решений из 20" in st["text"], st["text"]
    inp = warehouse()
    res = A.probability(con, calc=calculate(ref(), inp), valuation=None,
                        documents=[{"doc_name": d, "received": 1} for d in DOCS],
                        history={"claims": []}, context=ctx(inp))
    assert not res["stat"]["applied"]
    assert not any(x["rule"] == "stat" for x in res["minus"] + res["plus"])
    assert "из 20" in res["summary"]
    con.close()
    print("12 решений: поправка не применяется —", st["text"])


def test_stat_applied_over_20():
    con = tmp_con(rows_approved=6, rows_rejected=19)     # 25 решений, подтверждают 24%
    st = A.stat_adjustment(con, product_code="0807", branch="Ташкент")
    assert st["applied"] is True and st["n"] == 25, st
    assert st["share_pct"] == 24.0, st
    raw = round((24.0 - A.BASE) * A.STAT_WEIGHT)                 # (24 − 80) × 0,3 = −16,8 ≈ −17
    assert raw == -17 and st["delta"] == -A.STAT_LIMIT, (raw, st)  # поправка ограничена ±10 п.п.
    inp = warehouse()
    with_stat = A.probability(con, calc=calculate(ref(), inp),
                              valuation={"value": 4.2e9, "confirmed_by": "Каримов А."},
                              documents=[{"doc_name": d, "received": 1} for d in DOCS],
                              history={"claims": []}, context=ctx(inp))
    assert any(x["rule"] == "stat" for x in with_stat["minus"]), with_stat["minus"]
    assert with_stat["probability"] <= test_clean_request_high()["probability"]
    con.close()
    print("25 решений, подтверждают 24%%: поправка %+d п.п. → %d%%"
          % (st["delta"], with_stat["probability"]))


# ---------- 9. сверка с фактом ----------

def test_accuracy_reports():
    # таблицы нет вообще
    empty = sqlite3.connect(":memory:")
    a0 = A.accuracy(empty)
    assert a0["enough"] is False and a0["n"] == 0 and "нет" in a0["note"]
    assert A.save_probability(empty, request_id=1, result={"probability": 50})["saved"] is False
    empty.close()

    con = tmp_con(rows_approved=18, rows_rejected=6)     # 24 решения
    a = A.accuracy(con, product_code="0807")
    # показывали 80% там, где одобрили, и 40% там, где отклонили → предсказания верны всегда
    assert a["n"] == 24 and a["enough"] is True
    assert a["accuracy_pct"] == 100.0, a
    assert abs(a["mean_error_pct"] - (18 * 20 + 6 * 40) / 24) < 0.05, a
    assert a["buckets"], a
    con.close()
    print("сверка с фактом: попаданий %s%%, средняя ошибка %s п.п. на %d решениях"
          % (a["accuracy_pct"], a["mean_error_pct"], a["n"]))


def test_save_and_record():
    con = tmp_con()
    inp = warehouse()
    res = A.probability(con, calc=calculate(ref(), inp), valuation=None,
                        documents=[{"doc_name": d, "received": 1} for d in DOCS],
                        history=None, context=ctx(inp))
    assert A.save_probability(con, request_id=777, result=res, product_code="0807",
                              branch="Ташкент", class_code="8", verdict="на утверждение")["saved"]
    assert A.record_decision(con, request_id=777, decision="одобрил", decided_by="тест")["saved"]
    row = con.execute("SELECT probability, decision FROM decision_outcomes WHERE request_id=777").fetchone()
    assert row[0] == res["probability"] and row[1] == "одобрил"
    con.close()
    print("запись и сверка решения по запросу 777: показывали %d%%, решение «%s»" % (row[0], row[1]))


def cleanup():
    if TMP_DB.exists():
        TMP_DB.unlink()


if __name__ == "__main__":
    for fn in (test_clean_request_high, test_stop_check_caps_at_10, test_below_min_rate_caps_at_10,
               test_missing_documents_lower, test_sum_over_valuation_lowers, test_claims_and_no_franchise,
               test_corridor, test_stat_not_applied_under_20, test_stat_applied_over_20,
               test_accuracy_reports, test_save_and_record):
        fn()
    cleanup()
    print("Все проверки пройдены.")
