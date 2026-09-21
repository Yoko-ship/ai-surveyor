"""
Вероятность подтверждения запроса (0–100%) с объяснением по пунктам.

Зачем: агент отправляет запрос на согласование и сразу видит, чем дело кончится и что поправить;
руководитель видит то же самое — одни и те же цифры и один и тот же текст. Модуль ничего не
запрещает и не решает за человека, он только показывает шансы и причины.

Как считается (всё прозрачно, без «чёрного ящика»):
    старт 80 баллов
    − минусы: стоп-проверки, предупреждения движка, недостающие документы, сумма выше оценки,
      убытки в истории;
    + плюсы: франшиза, полный пакет документов, подтверждённая андеррайтером оценка, ставка
      не ниже технической;
    ± поправка по статистике прошлых решений (только когда решений по продукту и филиалу
      накопилось не меньше 20 — иначе поправки нет и об этом сказано в объяснении);
    итог ограничивается коридором 3–97% — стопроцентной гарантии система не обещает;
    сработала стоп-проверка (жёсткое нарушение нормы) — итог не выше 10%.

Правило проекта № 7: ни один вес здесь не выведен из статистики компании. Все веса экспертные,
поэтому в ответе стоит calibrated = 0. Когда придут выгрузки решений (таблица decision_outcomes),
веса пересчитываются по функции accuracy() и правятся отдельным решением актуария.

------------------------------------------------------------------------------------------------
ТАБЛИЦА decision_outcomes — её в схеме ещё нет, SQL добавляет разработчик (db/schema.sql).
Состав (готовый DDL — в константе SCHEMA_SQL ниже):

    id              INTEGER PRIMARY KEY
    request_id      INTEGER REFERENCES requests(id)      -- по какому запросу
    calculation_id  INTEGER REFERENCES calculations(id)  -- какой расчёт показывали
    product_code    TEXT        -- продукт на момент отправки (разрез для статистики)
    branch          TEXT        -- филиал (второй разрез)
    class_code      TEXT        -- класс страхования
    probability     REAL NOT NULL   -- что показали агенту, 0..100
    verdict         TEXT        -- вердикт движка тогда же: 'ок' | 'на утверждение' | 'отклонено'
    factors_json    TEXT        -- JSON: минусы и плюсы, как их посчитали (разбор ошибок модели)
    model_version   TEXT        -- версия правил, сейчас 'prob-1'
    sent_at         TEXT NOT NULL   -- когда запрос ушёл на согласование
    decision        TEXT        -- фактическое решение: 'одобрил' | 'отклонил' | NULL пока нет
    decided_at      TEXT
    decided_by      TEXT        -- логин согласующего
    comment         TEXT

Значения decision совпадают со словарём согласования (app/approvals.py, request_reviewers.status),
итог по запросу — requests.approval_status ('согласован' | 'отклонён').
------------------------------------------------------------------------------------------------
"""
import json
from typing import Optional

MODEL_VERSION = "prob-1"

BASE = 80                 # стартовый уровень «обычного чистого запроса», экспертно (calibrated = 0)
STOP_CAP = 10             # потолок при жёстком нарушении нормы
FLOOR, CEIL = 3, 97       # коридор итога: ни 0, ни 100 система не обещает
MIN_OUTCOMES = 20         # меньше этого числа решений — статистическая поправка не применяется
STAT_WEIGHT = 0.3         # какая доля отклонения статистики от базы идёт в поправку
STAT_LIMIT = 10           # поправка по статистике не больше ±10 п.п.
VALUATION_GAP_PCT = 15.0  # порог расхождения с оценкой стоимости (как в app/valuation.py)

# Вес каждого предупреждения движка, в пунктах вероятности. Экспертно, calibrated = 0.
WARN_WEIGHTS = {
    "below_technical": 15,   # ставка не покрывает ожидаемые убытки — главный повод вернуть запрос
    "manual_rate": 12,       # ставка поставлена руками, расчёт даёт другое
    "statutory_rate": 8,     # обязательный вид: ставка берётся из акта, расчёт не основание
    "value_agreed": 8,       # стоимость в расчёте не совпала с оценкой
    "cat_accumulation": 7,   # катастрофический риск в сейсмозоне
    "risk_limit_20": 6,      # лимит на один риск не проверен (нет цифр по компании)
    "underinsurance": 5,     # сумма ниже стоимости — пропорциональная выплата
    "premium_unpaid": 4,
    "disclosure": 4,
    "multi_class": 3,
    "pricing_mode": 0,       # это не замечание, а пояснение к режиму цены
}
DEFAULT_WARN = 3             # незнакомое предупреждение — небольшой минус, но не ноль

# Отдельные веса (эти пункты считаются по числам, а не по факту предупреждения)
W_DOC_EACH, W_DOC_MAX = 3, 18        # минус за каждый недостающий обязательный документ
W_DOCS_FULL = 4                      # плюс за полный пакет
W_FRANCHISE = 6                      # плюс за франшизу / минус за её отсутствие
W_SUM_OVER_VALUATION = 8             # сумма выше оценки
W_SUM_OVER_VALUATION_BIG = 15        # то же, если превышение больше порога расхождения 15%
W_VALUATION_CONFIRMED = 5            # оценку подтвердил андеррайтер
W_RATE_OK = 4                        # ставка не ниже технической и не ниже минимума
W_CLAIM_EACH, W_CLAIM_MAX = 7, 20    # минус за каждый убыток в истории
W_NO_CLAIMS = 4                      # плюс за чистую историю

# На какую норму ссылаться в объяснении. Ключ — код правила движка (app/engine.py).
NORMS = {
    "sum_over_value": "ГК ст. 938: в части превышения стоимости договор недействителен",
    "underinsurance": "ГК ст. 936: выплата уменьшается в той же доле",
    "sum_vs_valuation": "ГК ст. 934, 936, 938: стоимость — действительная стоимость на день договора",
    "value_agreed": "ГК ст. 932, 935: расхождение снимается до выпуска полиса",
    "min_rate": "тарифная политика компании (минимальные ставки); для обязательных видов — тариф из акта",
    "statutory_rate": "тариф обязательного вида установлен нормативным актом (постановление ПКМ)",
    "risk_limit_20": "Положение 1806, п. 15: на один риск не более 20% от собственных средств и резервов",
    "multi_class": "Положение 1882, п. 11: каждая часть продукта — отдельный условный договор",
    "credit_uncovered": "требование к кредитному страхованию: не выше необеспеченной части и 50% кредита",
    "credit_payer": "требование к кредитному страхованию: страхователь и плательщик — банк",
    "premium_unpaid": "Закон о страховой деятельности, ст. 33¹: полис без оплаты обязывает страховщика полностью",
    "disclosure": "Закон о страховой деятельности, ст. 63: клиенту раскрывают цену, риски и порядок претензий",
    "manual_rate": "внутренний порядок: отступление от расчёта согласуется с андеррайтером",
    "below_technical": "техническая ставка — нижняя граница безубыточности расчёта",
    "cat_accumulation": "контроль накопления по сейсмозоне: собственное удержание и перестрахование "
                        "(Положение 1806, п. 15 — лимит на один риск)",
    "docs_missing": "чек-лист документов компании (docs/Чек-листы документов.md)",
    "no_franchise": "андеррайтерская практика: франшиза отсекает мелкие убытки",
}

# Человеческие формулировки. Если кода нет в словаре — берётся заголовок проверки как есть.
RULE_TEXT = {
    "below_technical": "Ставка ниже технической — по такой цене договор убыточен",
    "manual_rate": "Ставка поставлена вручную и отличается от расчёта",
    "statutory_rate": "Это обязательный вид: ставку берут из нормативного акта, а не из расчёта",
    "value_agreed": "Стоимость в расчёте не совпадает с оценкой системы",
    "cat_accumulation": "Объект в сейсмозоне, землетрясение включено — риск копится по зоне",
    "risk_limit_20": "Лимит на один риск не проверен: нет цифр по собственным средствам и резервам",
    "underinsurance": "Страховая сумма ниже стоимости — при убытке выплатят только часть",
    "premium_unpaid": "Премия ещё не поступила",
    "disclosure": "Клиенту не раскрыта информация о договоре",
    "multi_class": "Класс расчёта не совпадает с составом продукта",
    "sum_over_value": "Страховая сумма выше страховой стоимости",
    "min_rate": "Ставка ниже минимальной по тарифной политике",
    "credit_uncovered": "Страховая сумма выше допустимой по кредиту",
    "credit_payer": "Страхователь или плательщик — не банк",
}

APPROVED_WORDS = {"одобрил", "одобрен", "согласован", "подтверждён", "подтвержден", "approved", "approve"}
REJECTED_WORDS = {"отклонил", "отклонён", "отклонен", "rejected", "reject"}

# Готовый DDL для разработчика (и для тестов, чтобы завести временную базу).
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS decision_outcomes (
    id             INTEGER PRIMARY KEY,
    request_id     INTEGER,
    calculation_id INTEGER,
    product_code   TEXT,
    branch         TEXT,
    class_code     TEXT,
    probability    REAL NOT NULL,
    verdict        TEXT,
    factors_json   TEXT,
    model_version  TEXT,
    sent_at        TEXT NOT NULL,
    decision       TEXT,
    decided_at     TEXT,
    decided_by     TEXT,
    comment        TEXT
);
CREATE INDEX IF NOT EXISTS ix_decision_outcomes_request ON decision_outcomes(request_id);
CREATE INDEX IF NOT EXISTS ix_decision_outcomes_scope
    ON decision_outcomes(product_code, branch, decision);
"""


# ---------- мелкие помощники ----------

def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def _pct(x: float) -> str:
    return ("%.0f" % x)


def _sum(x) -> str:
    try:
        return "{:,.0f}".format(float(x)).replace(",", " ")
    except (TypeError, ValueError):
        return str(x)


def _checks(calc: dict) -> list:
    return list((calc or {}).get("checks") or [])


def _check(calc: dict, rule: str) -> Optional[dict]:
    for c in _checks(calc):
        if c.get("rule") == rule:
            return c
    return None


def missing_documents(calc: dict, documents=None) -> list:
    """
    Каких обязательных документов не хватает. Основной источник — проверка движка docs_missing
    (она знает чек-лист по классу), дополнительно смотрим список документов запроса:
    строка есть, но received = 0 — документ тоже считается непринятым.
    """
    out = []
    c = _check(calc, "docs_missing")
    if c and c.get("detail"):
        out = [s.strip() for s in str(c["detail"]).split(";") if s.strip()]
    for d in documents or []:
        if isinstance(d, dict):
            name = (d.get("doc_name") or d.get("name") or "").strip()
            received = d.get("received")
            if name and received is not None and not received and name not in out:
                out.append(name)
    return out


def has_franchise(calc: dict, context: dict) -> Optional[bool]:
    """
    Есть ли франшиза. True/False, либо None — если к этому классу франшиза не применяется
    (тогда пункт в объяснении не показываем).
    """
    ctx = context or {}
    if ctx.get("franchise") is not None:
        try:
            return float(ctx["franchise"]) > 0
        except (TypeError, ValueError):
            pass
    factors = ctx.get("factors") or {}
    if "franchise" in factors:
        return str(factors.get("franchise") or "f0") != "f0"
    if _check(calc, "no_franchise"):
        return False
    return None


def _claims_of(history) -> dict:
    """Приводит историю убытков к виду {'count': n, 'paid': сумма, 'known': True/False}."""
    if not history:
        return {"count": 0, "paid": 0.0, "known": False}
    if isinstance(history, dict):
        items = history.get("claims")
        if isinstance(items, list):
            paid = 0.0
            for it in items:
                if isinstance(it, dict):
                    try:
                        paid += float(it.get("paid") or it.get("claimed") or 0)
                    except (TypeError, ValueError):
                        pass
            return {"count": len(items), "paid": paid, "known": True}
        if history.get("claims_count") is not None:
            try:
                n = int(history["claims_count"])
            except (TypeError, ValueError):
                return {"count": 0, "paid": 0.0, "known": False}
            try:
                paid = float(history.get("paid_total") or 0)
            except (TypeError, ValueError):
                paid = 0.0
            return {"count": n, "paid": paid, "known": True}
    if isinstance(history, list):
        return {"count": len(history), "paid": 0.0, "known": True}
    return {"count": 0, "paid": 0.0, "known": False}


# ---------- статистика прошлых решений ----------

def table_exists(con, name: str = "decision_outcomes") -> bool:
    if con is None:
        return False
    try:
        r = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        return r is not None
    except Exception:
        # другая СУБД или закрытое соединение — пробуем просто прочитать таблицу
        try:
            con.execute("SELECT 1 FROM %s LIMIT 1" % name).fetchone()
            return True
        except Exception:
            return False


def _decided_rows(con, product_code=None, branch=None) -> list:
    """Решённые записи из decision_outcomes в нужном разрезе. Нет таблицы — пустой список."""
    if not table_exists(con):
        return []
    sql = ("SELECT probability, decision, product_code, branch, sent_at, decided_at "
           "FROM decision_outcomes WHERE decision IS NOT NULL AND decision <> ''")
    args = []
    if product_code:
        sql += " AND product_code = ?"
        args.append(product_code)
    if branch:
        sql += " AND branch = ?"
        args.append(branch)
    try:
        return [dict(r) if not isinstance(r, dict) else r
                for r in _fetch(con, sql, args)]
    except Exception:
        return []


def _fetch(con, sql, args) -> list:
    cur = con.execute(sql, tuple(args))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _outcome_flag(decision: str) -> Optional[int]:
    d = (decision or "").strip().lower()
    if d in APPROVED_WORDS:
        return 1
    if d in REJECTED_WORDS:
        return 0
    return None


def stat_adjustment(con, *, product_code=None, branch=None) -> dict:
    """
    Поправка по статистике прошлых решений: насколько по этому продукту и филиалу обычно
    подтверждают. Применяется только при MIN_OUTCOMES решениях и больше — иначе честно
    пишем, сколько накоплено.
    """
    rows = [r for r in _decided_rows(con, product_code, branch) if _outcome_flag(r.get("decision")) is not None]
    n = len(rows)
    scope = "по продукту %s" % product_code if product_code else "по всем продуктам"
    if branch:
        scope += ", филиал %s" % branch
    if n < MIN_OUTCOMES:
        return {"applied": False, "n": n, "need": MIN_OUTCOMES, "delta": 0, "share_pct": None,
                "text": ("Поправки по прошлым решениям нет: %s накоплено %d решений из %d нужных. "
                         "Пока цифра опирается только на правила андеррайтинга."
                         % (scope, n, MIN_OUTCOMES))}
    approved = sum(_outcome_flag(r["decision"]) for r in rows)
    share = approved / n * 100
    delta = int(_clip(round((share - BASE) * STAT_WEIGHT), -STAT_LIMIT, STAT_LIMIT))
    return {"applied": True, "n": n, "need": MIN_OUTCOMES, "delta": delta, "share_pct": round(share, 1),
            "text": ("Статистика: %s из %d похожих запросов подтвердили %d (%s%%) — поправка %+d п.п."
                     % (scope, n, approved, _pct(share), delta))}


# ---------- главная функция ----------

def probability(con=None, *, calc: dict, valuation: dict = None, documents: list = None,
                history: dict = None, context: dict = None) -> dict:
    """
    Вероятность подтверждения запроса и объяснение по пунктам.

    con        — соединение с базой (нужно только для поправки по прошлым решениям); можно None;
    calc       — то, что вернул app.engine.calculate (checks, rates, verdict, premium);
    valuation  — карточка оценки стоимости: app.valuation.valuation_for_engine (или None);
    documents  — строки documents по запросу: [{'doc_name': ..., 'received': 0|1}, ...] или имена;
    history    — история убытков: {'claims': [строки claims]} либо {'claims_count': n, 'paid_total': s};
    context    — {'product_code','branch','class_code','sum_insured','factors','franchise'} — всё необязательно.

    Возвращает:
    {'probability': 72, 'verdict': '...', 'minus': [{'text','delta','norm','rule'}], 'plus': [...],
     'how_to_raise': [...], 'stat': {...}, 'summary': '...', 'base': 80, 'cap': None,
     'model_version': 'prob-1', 'calibrated': 0}
    """
    calc = calc or {}
    ctx = dict(context or {})
    checks = _checks(calc)
    minus, plus, how = [], [], []
    score = float(BASE)
    cap = None

    def m(text, delta, rule=None, norm=None, stop=False):
        minus.append({"text": text, "delta": -abs(int(delta)) if not stop else -100,
                      "rule": rule, "norm": norm or NORMS.get(rule or ""), "stop": bool(stop)})

    def p(text, delta, rule=None, norm=None):
        plus.append({"text": text, "delta": abs(int(delta)), "rule": rule, "norm": norm or NORMS.get(rule or "")})

    # 1) стоп-проверки: жёсткое нарушение нормы. Итог не выше 10% — арифметика ниже уже не важна.
    stops = [c for c in checks if c.get("status") == "stop"]
    for c in stops:
        rule = c.get("rule")
        text = RULE_TEXT.get(rule) or c.get("title") or "Нарушение нормы"
        if rule == "min_rate":
            text = "Ставка ниже минимальной по тарифной политике — так оформлять нельзя"
        m(text + ". Это жёсткое нарушение: пока оно не снято, согласование почти невозможно",
          0, rule=rule, stop=True)
        how.append({"text": "Снимите нарушение «%s» — без этого выше 10%% не поднимется" % (c.get("title") or text),
                    "delta": None, "rule": rule})
    if stops:
        cap = STOP_CAP

    # 2) предупреждения движка. Документы, франшиза и сверка с оценкой считаются отдельно, ниже.
    special = {"docs_missing", "no_franchise", "sum_vs_valuation"}
    for c in checks:
        if c.get("status") != "warn" or c.get("rule") in special:
            continue
        rule = c.get("rule")
        w = WARN_WEIGHTS.get(rule, DEFAULT_WARN)
        if not w:
            continue
        score -= w
        m(RULE_TEXT.get(rule) or c.get("title") or "Замечание", w, rule=rule)

    # 3) документы по чек-листу
    miss = missing_documents(calc, documents)
    if miss:
        d = min(len(miss) * W_DOC_EACH, W_DOC_MAX)
        score -= d
        m("Не хватает обязательных документов: %d (%s)" % (len(miss), ", ".join(miss[:3])
                                                           + ("…" if len(miss) > 3 else "")),
          d, rule="docs_missing")
        how.append({"text": "Приложите недостающие документы (%d шт.) — вернёт %+d п.п." % (len(miss), d),
                    "delta": d, "rule": "docs_missing"})
    elif _check(calc, "docs_ok"):
        score += W_DOCS_FULL
        p("Обязательные документы собраны полностью", W_DOCS_FULL, rule="docs_missing")

    # 4) франшиза
    fr = has_franchise(calc, ctx)
    if fr is True:
        score += W_FRANCHISE
        p("Есть франшиза: мелкие убытки не ложатся на компанию", W_FRANCHISE, rule="no_franchise")
    elif fr is False:
        score -= W_FRANCHISE
        m("Франшиза не установлена — все мелкие убытки оплачивает компания", W_FRANCHISE, rule="no_franchise")
        how.append({"text": "Поставьте франшизу (хотя бы 1%% страховой суммы) — вернёт +%d п.п." % W_FRANCHISE,
                    "delta": W_FRANCHISE, "rule": "no_franchise"})

    # 5) страховая сумма против оценки стоимости объекта
    val = valuation if isinstance(valuation, dict) else None
    vv = None
    if val:
        try:
            vv = float(val.get("value") or 0) or None
        except (TypeError, ValueError):
            vv = None
    si = ctx.get("sum_insured")
    try:
        si = float(si) if si is not None else None
    except (TypeError, ValueError):
        si = None
    if vv and si and si > vv:
        over = si - vv
        over_pct = over / vv * 100
        w = W_SUM_OVER_VALUATION_BIG if over_pct > VALUATION_GAP_PCT else W_SUM_OVER_VALUATION
        score -= w
        m("Страховая сумма выше оценки стоимости на %s сум (%s%%)" % (_sum(over), _pct(over_pct)),
          w, rule="sum_vs_valuation")
        how.append({"text": "Снизьте страховую сумму до оценки %s сум — вернёт +%d п.п." % (_sum(vv), w),
                    "delta": w, "rule": "sum_vs_valuation"})
    elif _check(calc, "sum_vs_valuation") and "выше оценки" in (_check(calc, "sum_vs_valuation").get("title") or ""):
        score -= W_SUM_OVER_VALUATION
        m("Страховая сумма выше оценки стоимости объекта", W_SUM_OVER_VALUATION, rule="sum_vs_valuation")
    if val and val.get("confirmed_by"):
        score += W_VALUATION_CONFIRMED
        p("Оценку стоимости подтвердил андеррайтер (%s)" % val.get("confirmed_by"), W_VALUATION_CONFIRMED,
          rule="sum_vs_valuation")

    # 6) ставка: не ниже технической и не ниже минимума — это главный довод «за»
    rates = calc.get("rates") or {}
    tech_ok = _check(calc, "below_technical")
    min_ok = _check(calc, "min_rate")
    if tech_ok and tech_ok.get("status") == "ok" and (min_ok is None or min_ok.get("status") == "ok"):
        score += W_RATE_OK
        p("Ставка %s%% покрывает расчётную (техническую) и не ниже минимума"
          % (("%.3f" % rates["applied_pct"]) if rates.get("applied_pct") is not None else "—"),
          W_RATE_OK, rule="below_technical")

    # 7) убытки в истории
    cl = _claims_of(history)
    if cl["known"] and cl["count"]:
        d = min(cl["count"] * W_CLAIM_EACH, W_CLAIM_MAX)
        score -= d
        tail = (", выплачено %s сум" % _sum(cl["paid"])) if cl["paid"] else ""
        m("По этому страхователю (объекту) уже были убытки: %d%s" % (cl["count"], tail), d,
          rule="loss_history", norm="справка об убытках за 3 года — чек-лист документов")
    elif cl["known"]:
        score += W_NO_CLAIMS
        p("Убытков в истории нет", W_NO_CLAIMS, rule="loss_history")

    # 8) поправка по статистике прошлых решений
    stat = stat_adjustment(con, product_code=ctx.get("product_code"), branch=ctx.get("branch"))
    if stat.get("applied") and stat.get("delta"):
        score += stat["delta"]
        (plus if stat["delta"] > 0 else minus).append(
            {"text": stat["text"], "delta": int(stat["delta"]), "rule": "stat",
             "norm": "статистика решений компании (decision_outcomes)"})

    # 9) коридор и потолок при стоп-проверке
    value = int(round(_clip(score, FLOOR, CEIL)))
    if cap is not None:
        value = min(value, cap)
    value = int(_clip(value, FLOOR, CEIL))

    minus.sort(key=lambda x: x["delta"])
    plus.sort(key=lambda x: -x["delta"])
    verdict = verdict_text(value, bool(stops))
    out = {"probability": value, "verdict": verdict,
           "verdict_code": verdict_code(value, bool(stops)), "minus": minus, "plus": plus,
           "how_to_raise": how, "stat": stat, "base": BASE, "cap": cap,
           "model_version": MODEL_VERSION, "calibrated": 0}
    out["summary"] = summary_text(out)
    return out


def verdict_code(value: int, has_stop: bool = False) -> str:
    """Код вердикта для перевода на экране; пороги те же, что в verdict_text."""
    if has_stop:
        return "stop"
    if value >= 80:
        return "likely"
    if value >= 60:
        return "good"
    if value >= 40:
        return "any"
    if value >= 20:
        return "rework"
    return "unlikely"


def verdict_text(value: int, has_stop: bool = False) -> str:
    if has_stop:
        return "Почти наверняка вернут: нарушена норма"
    if value >= 80:
        return "Скорее всего подтвердят"
    if value >= 60:
        return "Шансы хорошие, но есть замечания"
    if value >= 40:
        return "Решение может быть любым: замечаний много"
    if value >= 20:
        return "Скорее вернут на доработку"
    return "Почти наверняка не подтвердят"


def summary_text(res: dict) -> str:
    """Одно короткое объяснение простыми словами — для агента и для руководителя."""
    parts = ["Вероятность подтверждения ~%d%%. %s." % (res["probability"], res["verdict"])]
    if res["minus"]:
        items = []
        for x in res["minus"][:3]:
            items.append(x["text"] if x.get("stop") else "%s (−%d)" % (x["text"], abs(x["delta"])))
        parts.append("Что снижает: " + "; ".join(items) + ".")
    else:
        parts.append("Снижающих обстоятельств не нашлось.")
    if res["how_to_raise"]:
        ups = [x["text"] for x in res["how_to_raise"][:3]]
        parts.append("Что повысит: " + "; ".join(ups) + ".")
    elif res["plus"]:
        parts.append("В плюс идут: " + "; ".join("%s (+%d)" % (x["text"], x["delta"]) for x in res["plus"][:3]) + ".")
    if not res["stat"].get("applied"):
        parts.append(res["stat"]["text"])
    parts.append("Цифра справочная: веса правил экспертные, статистикой компании пока не подтверждены.")
    return " ".join(parts)


# ---------- самообучение: сверка показанной вероятности с фактом ----------

def save_probability(con, *, request_id, result: dict, calculation_id=None, product_code=None,
                     branch=None, class_code=None, verdict=None, sent_at=None) -> dict:
    """
    Записывает показанную вероятность, чтобы потом сверить её с решением.
    Таблицы decision_outcomes ещё нет — ничего не пишем и честно возвращаем note.
    """
    if not table_exists(con):
        return {"saved": False, "note": "Таблицы decision_outcomes в базе нет — сохранять некуда, "
                                        "SQL добавляет разработчик (SCHEMA_SQL в app/analysis.py)."}
    from datetime import datetime
    ts = sent_at or datetime.now().isoformat(timespec="seconds")
    factors = json.dumps({"minus": result.get("minus"), "plus": result.get("plus"),
                          "stat": result.get("stat"), "base": result.get("base"),
                          "cap": result.get("cap")}, ensure_ascii=False)
    con.execute("INSERT INTO decision_outcomes (request_id, calculation_id, product_code, branch,"
                " class_code, probability, verdict, factors_json, model_version, sent_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (request_id, calculation_id, product_code, branch, class_code,
                 float(result.get("probability") or 0), verdict, factors,
                 result.get("model_version") or MODEL_VERSION, ts))
    return {"saved": True, "request_id": request_id}


def record_decision(con, *, request_id, decision: str, decided_by=None, comment=None,
                    decided_at=None) -> dict:
    """Проставляет фактическое решение последней записи по запросу."""
    if not table_exists(con):
        return {"saved": False, "note": "Таблицы decision_outcomes в базе нет."}
    from datetime import datetime
    ts = decided_at or datetime.now().isoformat(timespec="seconds")
    rows = _fetch(con, "SELECT id FROM decision_outcomes WHERE request_id=? ORDER BY id DESC LIMIT 1",
                  [request_id])
    if not rows:
        return {"saved": False, "note": "По запросу %s показанной вероятности не сохранено." % request_id}
    con.execute("UPDATE decision_outcomes SET decision=?, decided_at=?, decided_by=?, comment=? WHERE id=?",
                (decision, ts, decided_by, comment, rows[0]["id"]))
    return {"saved": True, "id": rows[0]["id"], "decision": decision}


def accuracy(con, *, product_code=None, branch=None) -> dict:
    """
    Насколько модель попадает: доля правильных предсказаний (выше 50% — ждали подтверждения)
    и средняя ошибка в пунктах. Данных мало или таблицы нет — «данных недостаточно», без падения.
    """
    empty = {"enough": False, "n": 0, "need": MIN_OUTCOMES, "accuracy_pct": None,
             "mean_error_pct": None, "brier": None, "buckets": [], "calibrated": 0}
    if not table_exists(con):
        return {**empty, "note": "Таблицы decision_outcomes в базе нет — сверять не с чем."}
    rows = []
    for r in _decided_rows(con, product_code, branch):
        flag = _outcome_flag(r.get("decision"))
        if flag is None:
            continue
        try:
            pr = float(r.get("probability"))
        except (TypeError, ValueError):
            continue
        rows.append((pr, flag))
    n = len(rows)
    if n == 0:
        return {**empty, "note": "Фактических решений пока нет — сверять не с чем."}
    hits = sum(1 for pr, f in rows if (pr >= 50) == bool(f))
    mean_err = sum(abs(pr - f * 100) for pr, f in rows) / n
    brier = sum((pr / 100 - f) ** 2 for pr, f in rows) / n
    buckets = []
    for lo, hi in ((0, 25), (25, 50), (50, 75), (75, 101)):
        part = [(pr, f) for pr, f in rows if lo <= pr < hi]
        if part:
            buckets.append({"range": "%d–%d%%" % (lo, min(hi, 100)), "n": len(part),
                            "predicted_pct": round(sum(pr for pr, _ in part) / len(part), 1),
                            "fact_pct": round(sum(f for _, f in part) / len(part) * 100, 1)})
    enough = n >= MIN_OUTCOMES
    note = ("Модель угадывает исход в %s%% случаев, средняя ошибка %s п.п. (решений: %d)."
            % (_pct(hits / n * 100), _pct(mean_err), n)) if enough else \
           ("Данных недостаточно: решений %d из %d. Цифры ниже — предварительные, "
            "веса правил не пересматриваем." % (n, MIN_OUTCOMES))
    return {"enough": enough, "n": n, "need": MIN_OUTCOMES,
            "accuracy_pct": round(hits / n * 100, 1), "mean_error_pct": round(mean_err, 1),
            "brier": round(brier, 4), "buckets": buckets, "note": note, "calibrated": 0}
