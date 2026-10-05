"""Доведение баз до версии 1; SQL прежней версии сохранён."""
import logging
import sqlite3
from datetime import datetime
log = logging.getLogger("surveyor.migrations")

def now():
    return datetime.now().isoformat(timespec="seconds")

ADDED_COLUMNS = {
    "company_financials": [
        # 03.10.2026: разбивка собственных средств и резервов и происхождение цифр (tools/ranking_parse.py)
        ("total_capital", "REAL"),
        ("share_capital", "REAL"),
        ("reserves_gross", "REAL"),
        ("reserves_net", "REAL"),
        ("total_assets", "REAL"),
        ("basis", "TEXT"),
        ("source_url", "TEXT"),
        ("confirmed", "INTEGER"),
    ],
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
        ("unit", "TEXT"),                      # отдел: сотрудника заводит администратор вручную (app/staff.py)
        ("must_change_password", "INTEGER NOT NULL DEFAULT 0"),   # временный пароль — сменить при входе
    ],
    "claims": [
        # загрузка страховых случаев из Excel (app/claims_import.py)
        ("product_code", "TEXT"),
        ("contract_no", "TEXT"),
        ("branch", "TEXT"),
        ("region", "TEXT"),
        ("sum_insured", "REAL"),
        ("premium", "REAL"),
        ("batch_id", "INTEGER"),
        ("updated_at", "TEXT"),
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
    "checklists": [
        # 22.09.2026: зачем документ нужен. 'анализ' — для оценки риска, 'оформление' — только
        # для заключения договора (заявление-анкета). Анализ риска строки 'оформление' не спрашивает.
        ("scope", "TEXT NOT NULL DEFAULT 'анализ'"),
    ],
    "google_oauth_states": [
        # зачем начат вход (23.09.2026): 'вход' | 'админ' (один клик для админки) |
        # 'привязка' (добавить Google к уже открытому профилю, app/login_links.py)
        ("purpose", "TEXT"),
        ("link_user_id", "INTEGER"),           # чей профиль дополняем при purpose='привязка'
    ],
    "tg_link_codes": [
        # то же самое для входа по коду боту (app/tg_link.py)
        ("purpose", "TEXT"),
        ("link_user_id", "INTEGER"),
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


# справочник классов на экранах и в /reference/classes: классов страхования жизни в справочнике нет — строки L*,
# если они вдруг завелись, не показываются; порядок 1…13, 13з, 14…16, 16у, 17, 18 (CAST берёт число в начале кода)
CLASSES_SHOWN = "code NOT LIKE 'L%'"
CLASSES_ORDER = "CAST(code AS INTEGER), code"


CLASSES_SQL = """CREATE TABLE {name} (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    group_code  TEXT REFERENCES groups(code),
    branch      TEXT NOT NULL,
    kind        TEXT NOT NULL
)"""


def _classes_group_nullable(con) -> bool:
    """
    Учётная группа класса (classes.group_code) была NOT NULL. У класса 18 общего страхования («Tibbiy sugʻurta»,
    ПКМ № 80, прил. 6) учётной группы РНП нет: Положение 1882, п. 10 перечисляет классы 1–17 — поэтому колонка
    допускает NULL (отнести класс 18 к группе — решение страховщика). ALTER TABLE в SQLite
    NOT NULL не снимает — таблица пересоздаётся штатным порядком SQLite (проверка ссылок выключена на время
    пересоздания, всё в одной транзакции; строки и ссылки других таблиц на classes сохраняются).
    Идемпотентно: колонка уже допускает NULL или таблицы нет — ничего не делает. Открыта транзакция — пропуск
    (проверку ссылок внутри транзакции не выключить), доведёт следующий запуск.
    """
    try:
        info = con.execute("PRAGMA table_info(classes)").fetchall()
    except Exception:
        return False
    col = [r for r in info if r[1] == "group_code"]
    if not col or not col[0][3]:
        return False
    if con.in_transaction:
        log.warning("classes.group_code: открыта транзакция — пересоздание отложено")
        return False
    fk = con.execute("PRAGMA foreign_keys").fetchone()[0]
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute("BEGIN")
        try:
            con.execute("DROP TABLE IF EXISTS classes_new")
            con.execute(CLASSES_SQL.format(name="classes_new"))
            con.execute("INSERT INTO classes_new (code, name, group_code, branch, kind) "
                        "SELECT code, name, group_code, branch, kind FROM classes")
            con.execute("DROP TABLE classes")
            con.execute("ALTER TABLE classes_new RENAME TO classes")
            bad = con.execute("PRAGMA foreign_key_check(classes)").fetchall()
            if bad:
                raise sqlite3.IntegrityError(f"classes: нарушены ссылки на учётные группы ({len(bad)})")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.execute(f"PRAGMA foreign_keys = {'ON' if fk else 'OFF'}")
    return True


def migrate(con):
    """Добавляет недостающие колонки. Идемпотентно: повторный запуск ничего не делает."""
    added = []
    if _old_decision_outcomes(con):
        added.append("decision_outcomes: старая таблица переименована, строки переносятся")
    if _classes_group_nullable(con):
        added.append("classes.group_code: допускает NULL (класс 18 без учётной группы)")
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
                if (table, name) == ("checklists", "scope"):
                    # LIKE без lower(): в SQLite lower() кириллицу не приводит
                    con.execute("UPDATE checklists SET scope='оформление' WHERE doc_name LIKE '%аявлен%'")
    return added


def backfill_login_links(con) -> int:
    """
    Переносит уже существующие привязки из users (telegram_id, google_sub) в login_links.
    Идемпотентно: строка создаётся, только если такой пары «провайдер + аккаунт» ещё нет.

    Если один и тот же telegram_id по недосмотру оказался у двух профилей, берём самый ранний:
    уникальный индекс не даст создать два способа входа, ведущих в разные профили.
    """
    made = 0
    for provider, column, display in (("telegram", "telegram_id", "NULL"),
                                      ("google", "google_sub", "email")):
        sql = (f"INSERT INTO login_links (user_id, provider, external_id, display, linked_at)"
               f" SELECT u.id, ?, u.{column}, {display}, COALESCE(u.created_at, ?)"
               f" FROM users u WHERE u.{column} IS NOT NULL AND u.{column} <> ''"
               f"   AND u.id = (SELECT MIN(u2.id) FROM users u2 WHERE u2.{column} = u.{column})"
               f"   AND NOT EXISTS (SELECT 1 FROM login_links l"
               f"                   WHERE l.provider = ? AND l.external_id = u.{column})"
               f"   AND NOT EXISTS (SELECT 1 FROM login_links l2"
               f"                   WHERE l2.provider = ? AND l2.user_id = u.id)")
        made += con.execute(sql, (provider, now(), provider, provider)).rowcount or 0
    return made
