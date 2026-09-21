"""Доступ к базе. SQLite сейчас, PostgreSQL потом: SQL здесь без диалектных особенностей."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .engine import Reference

ROOT = Path(__file__).resolve().parent.parent
# STORAGE_DIR — постоянный диск на сервере (на Railway смонтирован в /srv/storage): база, фото, загрузки, копии
# живут там и переживают обновления кода. Без переменной — обычная папка data/ проекта.
DATA_DIR = Path(os.environ["STORAGE_DIR"]) if os.environ.get("STORAGE_DIR") else ROOT / "data"
DB_PATH = DATA_DIR / "surveyor.db"


def init_storage():
    """На постоянном диске ещё нет базы — переносим собранную при сборке образа (справочники, статистика)."""
    if DATA_DIR == ROOT / "data":
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    built = ROOT / "data" / "surveyor.db"
    if not built.exists() or built.resolve() == DB_PATH.resolve():
        return
    empty = True
    if DB_PATH.exists():
        # файл мог появиться раньше переноса (модуль подключился к базе при импорте) — тогда он пустой
        try:
            con = sqlite3.connect(DB_PATH)
            empty = con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
            con.close()
        except sqlite3.Error:
            empty = True
    if empty:
        import shutil
        shutil.copy2(built, DB_PATH)
        print(f"хранилище: собранная база перенесена на постоянный диск {DB_PATH}")
    else:
        print(f"хранилище: база на постоянном диске {DB_PATH}")
SCHEMA = ROOT / "db" / "schema.sql"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


@contextmanager
def tx():
    con = connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


# Колонки, добавленные в уже существующие таблицы. SQLite не умеет ADD COLUMN IF NOT EXISTS,
# а CREATE TABLE IF NOT EXISTS старую таблицу не меняет — поэтому доводим руками.
# Типы без диалектных особенностей: на PostgreSQL те же ALTER TABLE ... ADD COLUMN пройдут.
ADDED_COLUMNS = {
    "photos": [
        ("doc_kind", "TEXT NOT NULL DEFAULT 'фото объекта'"),
        ("parse_status", "TEXT"),
        ("parsed_at", "TEXT"),
        ("parsed_json", "TEXT"),
    ],
    "requests": [
        # итог коллективного согласования: 'не требуется'|'на согласовании'|'согласован'|'отклонён'
        ("approval_status", "TEXT NOT NULL DEFAULT 'не требуется'"),
        ("general_agreement_id", "INTEGER"),   # по какому генеральному соглашению идёт запрос
        # кто подал запрос из приложения: у роли «сотрудник» нет ID агента в ЕАИС,
        # поэтому связь «человек — свой запрос» держим напрямую (app/registration.py)
        ("created_by_user_id", "INTEGER"),
    ],
    "users": [
        ("telegram_id", "TEXT"),               # вход из мини-приложения Telegram (app/telegram.py)
        ("position", "TEXT"),                  # должность со слов человека (регистрация, app/registration.py)
        ("department", "TEXT"),                # департамент со слов человека (свободный текст с подсказками)
        ("google_sub", "TEXT"),                # вход через Google: вечный идентификатор аккаунта (app/google_auth.py)
        ("email", "TEXT"),                     # рабочая почта из аккаунта Google (проверена самим Google)
    ],
    "pd_consents": [
        # обязательные поля из раздела 7 docs/Регистрация и роли.md
        ("consent_text_hash", "TEXT"),                       # хэш показанного текста
        ("scope", "TEXT NOT NULL DEFAULT 'основное'"),       # 'основное' | 'телефон'
        ("revoked_at", "TEXT"),                              # отзыв — заполнением даты, строку не удаляем
    ],
    "reg_codes": [
        # защита кода регистрации от перебора (раздел 7 docs/Регистрация и роли.md)
        ("salt", "TEXT"),
        ("sent_total", "INTEGER NOT NULL DEFAULT 0"),
        ("first_sent_at", "TEXT"),
        ("exhausted", "INTEGER NOT NULL DEFAULT 0"),
        ("blocked_until", "TEXT"),
    ],
    "rules": [
        # LAWWATCH-01 (app/lawwatch.py): изменился акт — правила, которые на него ссылаются,
        # помечаются «требует пересмотра». Расчёты не блокируются, пометку снимает юрист.
        ("review_status", "TEXT NOT NULL DEFAULT 'ok'"),   # 'ok' | 'требует пересмотра'
        ("review_reason", "TEXT"),
        ("review_since", "TEXT"),
    ],
    "decision_outcomes": [
        # состав полей модуля вероятности (app/analysis.py, SCHEMA_SQL) плюс result_json:
        # база, заведённая до подключения модуля, доводится без пересоздания
        ("product_code", "TEXT"),
        ("branch", "TEXT"),
        ("class_code", "TEXT"),
        ("verdict", "TEXT"),
        ("factors_json", "TEXT"),
        ("decision", "TEXT"),
        ("decided_at", "TEXT"),
        ("decided_by", "TEXT"),
        ("comment", "TEXT"),
        ("result_json", "TEXT"),
    ],
}


def _old_decision_outcomes(con):
    """
    Первая версия decision_outcomes (created_at NOT NULL, probability 0..1, explanation,
    fact_decision) несовместима с модулем вероятности: ALTER TABLE в SQLite не снимает
    NOT NULL с created_at, поэтому таблицу переименовываем, а данные переносит
    _carry_decision_outcomes() уже после schema.sql.
    """
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(decision_outcomes)")}
    except Exception:
        return False
    if not cols or "sent_at" in cols:          # таблицы нет или она уже новая
        return False
    con.execute("DROP TABLE IF EXISTS decision_outcomes_old")
    con.execute("ALTER TABLE decision_outcomes RENAME TO decision_outcomes_old")
    # имя индекса занято старой таблицей — иначе CREATE INDEX IF NOT EXISTS из schema.sql промолчит
    con.execute("DROP INDEX IF EXISTS ix_decision_outcomes_request")
    con.execute("DROP INDEX IF EXISTS ix_decision_outcomes_scope")
    return True


def _carry_decision_outcomes(con):
    """Переносит прогнозы из старой таблицы в новую: вероятность 0..1 → проценты, факт → decision."""
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(decision_outcomes_old)")}
    except Exception:
        return 0
    if not cols:
        return 0
    moved = 0
    names = [r[1] for r in con.execute("PRAGMA table_info(decision_outcomes_old)")]
    for row in con.execute("SELECT * FROM decision_outcomes_old ORDER BY id").fetchall():
        r = dict(zip(names, tuple(row)))     # соединение может быть без row_factory (tools/db_build.py)
        p = r.get("probability")
        con.execute("INSERT INTO decision_outcomes (request_id, calculation_id, probability, verdict,"
                    " factors_json, model_version, sent_at, decision, decided_at, result_json)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (r.get("request_id"), r.get("calculation_id"),
                     float(p) * 100 if p is not None else 0.0, None, r.get("explanation"),
                     r.get("model_version"), r.get("created_at") or now(),
                     r.get("fact_decision"), r.get("fact_at"), None))
        moved += 1
    con.execute("DROP TABLE decision_outcomes_old")
    return moved


def migrate(con):
    """Добавляет недостающие колонки. Идемпотентно: повторный запуск ничего не делает."""
    added = []
    if _old_decision_outcomes(con):
        added.append("decision_outcomes: старая таблица переименована, строки переносятся")
    for table, cols in ADDED_COLUMNS.items():
        try:
            have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        except Exception:
            continue
        if not have:                       # таблицы ещё нет — её создаст schema.sql
            continue
        for name, decl in cols:
            if name not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                added.append(f"{table}.{name}")
    return added


def ensure_schema():
    with tx() as con:
        # сначала доводим старые таблицы, потом schema.sql: индексы по новым колонкам
        # иначе не создаются
        migrate(con)
        con.executescript(SCHEMA.read_text(encoding="utf-8"))
        _carry_decision_outcomes(con)       # строки старой decision_outcomes — в новую


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def audit(con, who: str, action: str, entity: str = None, detail=None):
    con.execute("INSERT INTO audit (ts, who, action, entity, detail) VALUES (?,?,?,?,?)",
                (now(), who, action, entity, json.dumps(detail, ensure_ascii=False) if detail is not None else None))


def rows(con, sql, *args) -> list:
    return [dict(r) for r in con.execute(sql, args)]


def load_reference(con) -> Reference:
    """Собирает справочники в структуру движка. Действующие версии тарифов — по дате."""
    today = datetime.now().date().isoformat()
    base = {(r["class_code"], r["object_type"]): r["net_rate_pct"] for r in rows(con, "SELECT * FROM base_rates")}
    coef = {(r["factor_code"], r["option_code"]): {"name": r["option_name"], "multiplier": r["multiplier"],
                                                   "factor_name": r["factor_name"]}
            for r in rows(con, "SELECT * FROM coefficients")}
    perils = {r["code"]: {"class_code": r["class_code"], "share": r["base_share"] or 0,
                          "name": r["name"], "cat": bool(r["is_catastrophic"])}
              for r in rows(con, "SELECT * FROM perils")}
    load = sum(r["share"] for r in rows(con, "SELECT share FROM load_components"))
    min_rates = {}
    for r in rows(con, """SELECT m.product_code, m.payer_type, m.min_rate_pct, v.level FROM min_rates m
                          JOIN tariff_versions v ON v.id = m.tariff_version_id
                          WHERE v.effective_from <= ? AND (v.effective_to IS NULL OR v.effective_to >= ?)""",
                  today, today):
        slot = "regulator" if r["level"] == "регулятор" else "company"
        d = min_rates.setdefault(r["product_code"], {"company": {}, "regulator": {}})
        # несколько действующих версий одного уровня — берём наибольшую ставку
        d[slot][r["payer_type"]] = max(d[slot].get(r["payer_type"], 0), r["min_rate_pct"])
    pcs = {}
    for r in rows(con, "SELECT product_code, class_code FROM product_classes ORDER BY part_no"):
        pcs.setdefault(r["product_code"], []).append(r["class_code"])
    checklists = rows(con, "SELECT scope_type, scope_code, doc_name, required FROM checklists")
    fin = rows(con, "SELECT * FROM company_financials ORDER BY report_date DESC LIMIT 1")
    measures = rows(con, "SELECT * FROM preventive_measures")
    # факторы каждого класса: движок строит форму и расчёт по набору своего класса, а не по фиксированному списку
    cls_factors = {}
    for r in rows(con, "SELECT DISTINCT class_code, factor_code FROM coefficients WHERE class_code IS NOT NULL ORDER BY id"):
        lst = cls_factors.setdefault(r["class_code"], [])
        if r["factor_code"] not in lst:
            lst.append(r["factor_code"])
    prods = {r["code"]: {"name": r["name"], "pricing_mode": r["pricing_mode"]}
             for r in rows(con, "SELECT code, name, pricing_mode FROM products")}
    return Reference(base_rates=base, coefficients=coef, perils=perils, load_share=load,
                     min_rates=min_rates, product_classes=pcs, checklists=checklists,
                     financials=fin[0] if fin else None, measures=measures, market=market_rates(con),
                     class_factors=cls_factors, products=prods)


# какая строка отчёта НАПП представляет класс: для 8 и 9 пакет «8,9» показательнее одиночных строк
MARKET_ROW = {"8": "cls8_9", "9": "cls8_9", "7": "cls7", "3": "cls3", "13": "cls13", "14": "cls14",
              "16": "cls16", "1": "cls1", "15": "cls15"}


def market_rates(con) -> dict:
    """Средняя годовая ставка и убыточность по классу на последний срез рыночной статистики."""
    out = {}
    try:
        latest = rows(con, """SELECT m.* FROM market_stats m
                              JOIN (SELECT row_key, MAX(report_date) d FROM market_stats GROUP BY row_key) t
                              ON t.row_key = m.row_key AND t.d = m.report_date""")
    except Exception:
        return out
    by_key = {r["row_key"]: r for r in latest}
    months = {"03-31": 3, "04-01": 3, "07-01": 6, "10-01": 9, "01-01": 12}
    for cls, key in MARKET_ROW.items():
        r = by_key.get(key)
        if not r or not r["premiums_ytd"] or not r["liabilities"]:
            continue
        m = months.get(r["report_date"][5:])
        if not m:
            continue
        year = int(r["report_date"][:4]) - (1 if m == 12 else 0)
        out[cls] = {"row": key, "rate_pct": r["premiums_ytd"] * 12 / m / r["liabilities"] * 100,
                    "loss_ratio": (r["payouts_ytd"] or 0) / r["premiums_ytd"] * 100,
                    "label": f"НАПП, {year} · {m} мес."}
    return out
