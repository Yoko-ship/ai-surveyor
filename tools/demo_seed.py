"""
Демо-данные для показа цикла согласования (ТОЛЬКО тестовый сервер).

Скрипт заводит вымышленных людей и один вымышленный запрос, чтобы заказчик увидел весь путь:
агент подал запрос → назначены два андеррайтера → запрос виден в разделе «Ждут меня» → решения.

НИ ОДНОГО настоящего человека и НИ ОДНОГО настоящего клиента здесь нет: все ФИО, банк
«Тестовый банк» и номер договора придуманы специально для показа. Филиал у всех записей — «демо»,
поэтому демо-данные всегда находятся и удаляются одним фильтром.

Что создаётся
  • три работника (вход по логину и паролю, пароли печатаются один раз при запуске):
      демо-агент            — Тестов Агент Агентович (роль «агент», есть запись в реестре агентов)
      демо-андеррайтер-1    — Проверкин Андеррайтер Первый (роль «андеррайтер»)
      демо-андеррайтер-2    — Сверкина Андеррайтер Вторая (роль «андеррайтер»)
    telegram_id не задаётся: у вымышленных людей его нет, вход — через браузер.
  • одно генеральное соглашение «Тестовый банк» с этими двумя андеррайтерами в составе по умолчанию;
  • один запрос: Chevrolet Cobalt 2021, г. Ташкент, КАСКО «Premium» 0311, страховая сумма
    103 478 017 сум, применённая ставка 2,000 %, премия 2 069 560 сум (цифры взяты из демо-аналитики
    docs/analytics_demo.html, раздел «Итог одним взглядом» и «2. Из чего сложилась ставка»).
    Запрос сразу ставится «на согласовании» на обоих андеррайтеров.

Защита от случайного запуска
  1) режим PD_MODE должен быть 'test' (значение берётся так же, как его видит сервер: app/llm.py get);
  2) обязателен явный флаг --yes;
  3) на машине разработчика (Windows) запрещено писать в рабочую базу data/surveyor.db —
     нужно явно указать копию: --db <путь к копии>.

КАК ЗАПУСТИТЬ НА ТЕСТОВОМ СЕРВЕРЕ RAILWAY (для заказчика)
  1. Откройте проект на railway.app → сервис ИИ-сюрвейера.
  2. Убедитесь, что в разделе Variables стоит PD_MODE = test. Если стоит другое — скрипт откажется работать.
  3. Нажмите на сервисе кнопку с тремя точками → «Run command» (или вкладка Deployments → «Shell»)
     и выполните одну строку:
         python tools/demo_seed.py --yes
  4. В ответе будут логины и пароли трёх демо-работников. Скопируйте их сразу: пароли случайные
     и больше нигде не хранятся, второй раз их показать нельзя (можно только пересоздать демо-данные).
  5. Убрать демо-данные — там же одной строкой:
         python tools/demo_seed.py --yes --clean

Локальная проверка на копии базы (для разработчика):
    set PYTHONIOENCODING=utf-8
    python tools\\demo_seed.py --yes --db C:\\путь\\к\\копии\\surveyor.db
    python tools\\demo_seed.py --yes --clean --db C:\\путь\\к\\копии\\surveyor.db

Только стандартная библиотека (плюс код самого приложения). Рабочую базу скрипт не открывает.
"""
import argparse
import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BRANCH = "демо"                      # филиал-метка: по нему демо-данные ищутся и удаляются
LOGIN_PREFIX = "демо-"
EAIS_ID = "ДЕМО-АГЕНТ-001"           # вымышленный ID агента в реестре
PARTNER = "Тестовый банк"            # вымышленный партнёр по генеральному соглашению
EXTERNAL_NO = "ДЕМО-0311-001"
PRODUCT = "0311"
CLASS_CODE = "3"

# Вымышленные работники: (логин, ФИО, роль)
PEOPLE = [
    (LOGIN_PREFIX + "агент", "Тестов Агент Агентович", "агент"),
    (LOGIN_PREFIX + "андеррайтер-1", "Проверкин Андеррайтер Первый", "андеррайтер"),
    (LOGIN_PREFIX + "андеррайтер-2", "Сверкина Андеррайтер Вторая", "андеррайтер"),
]

# Объект показа — из docs/analytics_demo.html (данные сняты 20.09.2026):
#   страховая стоимость и страховая сумма 103 478 017 сум (оценка по 5 объявлениям avtoelon.uz, медиана
#   108 924 228 × 0,95 «скидка на торг»), применённая ставка 2,000 % (минимум тарифной политики
#   для физлица по продукту 0311), премия за год 2 069 560 сум.
SUM_INSURED = 103_478_017.0
APPLIED_RATE_PCT = 2.0
PREMIUM_EXPECTED = 2_069_560         # сверка: столько показывает демо-аналитика
FACTORS = {
    # коды берём из справочника coefficients класса 3 (значения экспертные, calibrated = 0)
    "veh_age": "a7",        # автомобиль 2021 года — в справочнике интервал «3–7 лет»
    "veh_type": "car",
    "antitheft": "alarm",
    "drivers": "limited",
    "loss_history": "clean",
    "franchise": "f0",
}


def fail(text: str):
    print(text)
    sys.exit(1)


def money(x) -> str:
    return f"{round(float(x)):,}".replace(",", " ")


# --------------------------------------------------------------------------- #
#  Защита
# --------------------------------------------------------------------------- #

def guard(db, llm, db_path: Path, explicit_db: bool):
    """Проверки до единой записи: режим test, явный флаг и запрет на рабочую базу разработчика."""
    if not db_path.exists():               # проверяем до обращения к базе: пустой файл не создаём
        fail(f"Отказ: базы нет по пути {db_path}")
    # ВАЖНО: default="" — иначе llm.get подставит своё «test» из DEFAULTS, и проверка
    # никогда не сработает. Режим должен быть задан ЯВНО: в app_settings, в переменной
    # окружения или в .env. Не задан — отказ, даже на тестовой машине.
    mode = (llm.get("PD_MODE", "") or "").strip()
    if mode != "test":
        fail("Отказ: демо-данные создаются только на тестовом сервере. Режим PD_MODE должен быть "
             "задан явно (app_settings, переменная окружения или .env) и равен 'test'. "
             f"Сейчас: {mode or 'не задан'}")
    work_db = (ROOT / "data" / "surveyor.db").resolve()
    if db.DB_PATH.resolve() == work_db and not explicit_db and sys.platform.startswith("win"):
        fail("Отказ: это рабочая база разработчика data/surveyor.db — трогать её нельзя. "
             "Укажите копию: --db <путь к копии базы>. На тестовом сервере (Linux) параметр не нужен.")


# --------------------------------------------------------------------------- #
#  Создание
# --------------------------------------------------------------------------- #

def seed(db, auth, approvals, main_mod):
    from app.main import RequestIn

    db.ensure_schema()
    passwords = {}
    with db.tx() as con:
        if con.execute("SELECT COUNT(*) FROM users WHERE login LIKE ?", (LOGIN_PREFIX + "%",)).fetchone()[0]:
            fail("Отказ: демо-данные уже созданы. Сначала уберите их: --yes --clean")
        ts = db.now()
        uids = {}
        for login, full_name, role in PEOPLE:
            pwd = secrets.token_urlsafe(9)
            passwords[login] = pwd
            pw_hash, salt = auth.hash_password(pwd)
            cur = con.execute(
                "INSERT INTO users (login, full_name, role, branch, agent_eais_id, password_hash, salt, status,"
                " created_at, approved_by, approved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (login, full_name, role, BRANCH, EAIS_ID if role == "агент" else None,
                 pw_hash, salt, "активен", ts, "демо-данные", ts))
            uids[login] = cur.lastrowid
        # запись в реестре агентов: без неё не работает связь «кто подал запрос»
        con.execute("INSERT INTO agents (eais_id, name, kind, status) VALUES (?,?,?,?)",
                    (EAIS_ID, "Тестов Агент Агентович (вымышленный агент)", "физическое лицо", "активен"))
        ga = con.execute(
            "INSERT INTO general_agreements (partner, product_code, terms, default_reviewers, status, created_at, note)"
            " VALUES (?,?,?,?,?,?,?)",
            (PARTNER, PRODUCT, "Демонстрационные условия: КАСКО для клиентов банка, состав согласующих — два андеррайтера.",
             json.dumps([uids[PEOPLE[1][0]], uids[PEOPLE[2][0]]]), "действует", ts,
             "Вымышленный партнёр, создан скриптом tools/demo_seed.py для показа.")).lastrowid

    # запрос создаём штатной точкой POST /requests (та же функция, что вызывает сервер)
    res = main_mod.create_request(RequestIn(
        product_code=PRODUCT, class_code=CLASS_CODE, object_type="Легковой автомобиль",
        value_amount=SUM_INSURED, sum_insured=SUM_INSURED, term_days=365, factors=FACTORS,
        applied_rate_pct=APPLIED_RATE_PCT, payer_type="физ", premium_paid=False, disclosure_done=False,
        external_no=EXTERNAL_NO, branch=BRANCH, policyholder="Демо-клиент (вымышленное лицо)",
        agent_eais_id=EAIS_ID, address="г. Ташкент", region="Ташкент",
        manual_reason="Демо-показ: ставка 2,000 % — минимум тарифной политики для физлица по продукту 0311."))
    rid = res["request_id"]

    with db.tx() as con:
        out = approvals.assign(con, rid, [uids[PEOPLE[1][0]], uids[PEOPLE[2][0]]],
                               who=PEOPLE[0][0], agreement_id=ga)

    print("Демо-данные созданы (все лица и «Тестовый банк» — вымышленные).")
    print(f"  филиал-метка: {BRANCH}; генеральное соглашение: «{PARTNER}» (id {ga})")
    print(f"  запрос № {rid} ({EXTERNAL_NO}): Chevrolet Cobalt 2021, г. Ташкент, КАСКО «Premium» 0311")
    print(f"    страховая сумма {money(SUM_INSURED)} сум; применённая ставка "
          f"{res['rates']['applied_pct']:.3f} %; премия {money(res['premium'])} сум"
          f" (в демо-аналитике {money(PREMIUM_EXPECTED)} сум)")
    print(f"    техническая ставка по справочнику {res['rates']['technical_pct']:.3f} %; "
          f"минимум политики {res['rates']['min_pct']:.3f} %; вердикт: {res['verdict']}")
    print(f"    состояние согласования: {out['approval_status']}; согласующие: "
          + ", ".join(f"{r['full_name']} ({r['status']})" for r in out["reviewers"]))
    print("\n  Входы (пароли показываются ОДИН раз, сохраните их сейчас):")
    for login, full_name, role in PEOPLE:
        print(f"    {login:<22} {passwords[login]:<14} {role:<12} {full_name}")
    print("\n  Убрать демо-данные: python tools/demo_seed.py --yes --clean")


# --------------------------------------------------------------------------- #
#  Удаление
# --------------------------------------------------------------------------- #

def clean(db, approvals):
    db.ensure_schema()
    with db.tx() as con:
        rids = [r["id"] for r in db.rows(con, "SELECT id FROM requests WHERE branch=?", BRANCH)]
        uids = [r["id"] for r in db.rows(con, "SELECT id FROM users WHERE login LIKE ?", LOGIN_PREFIX + "%")]
        n = {"запросы": len(rids), "люди": len(uids)}
        for rid in rids:
            con.execute("DELETE FROM request_reviewers WHERE request_id=?", (rid,))
            con.execute("DELETE FROM check_results WHERE calculation_id IN"
                        " (SELECT id FROM calculations WHERE request_id=?)", (rid,))
            con.execute("DELETE FROM recommendations WHERE calculation_id IN"
                        " (SELECT id FROM calculations WHERE request_id=?)", (rid,))
            con.execute("DELETE FROM calculations WHERE request_id=?", (rid,))
            con.execute("DELETE FROM object_perils WHERE object_id IN"
                        " (SELECT id FROM objects WHERE request_id=?)", (rid,))
            con.execute("DELETE FROM objects WHERE request_id=?", (rid,))
            con.execute("DELETE FROM documents WHERE request_id=?", (rid,))
            try:
                con.execute("DELETE FROM photos WHERE request_id=?", (rid,))
            except Exception:
                pass
            con.execute("DELETE FROM requests WHERE id=?", (rid,))
            con.execute("DELETE FROM audit WHERE entity=?", (f"request:{rid}",))
        for uid in uids:
            con.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            try:
                con.execute("DELETE FROM login_codes WHERE user_id=?", (uid,))
            except Exception:
                pass
        n["соглашения"] = con.execute("SELECT COUNT(*) FROM general_agreements WHERE partner=?",
                                      (PARTNER,)).fetchone()[0]
        con.execute("DELETE FROM general_agreements WHERE partner=?", (PARTNER,))
        con.execute("DELETE FROM users WHERE login LIKE ?", (LOGIN_PREFIX + "%",))
        con.execute("DELETE FROM audit WHERE who LIKE ? OR who=?", (LOGIN_PREFIX + "%", BRANCH))
        n["агенты"] = con.execute("SELECT COUNT(*) FROM agents WHERE eais_id=?", (EAIS_ID,)).fetchone()[0]
        con.execute("DELETE FROM agents WHERE eais_id=?", (EAIS_ID,))
        # страховка: запрос без состава не должен остаться «на согласовании»
        for r in db.rows(con, "SELECT id FROM requests WHERE approval_status=?", "на согласовании"):
            if not db.rows(con, "SELECT 1 FROM request_reviewers WHERE request_id=?", r["id"]):
                approvals.recalc(con, r["id"])
        left_u = con.execute("SELECT COUNT(*) FROM users WHERE login LIKE ? OR branch=?",
                             (LOGIN_PREFIX + "%", BRANCH)).fetchone()[0]
        left_r = con.execute("SELECT COUNT(*) FROM requests WHERE branch=?", (BRANCH,)).fetchone()[0]
        left_g = con.execute("SELECT COUNT(*) FROM general_agreements WHERE partner=?", (PARTNER,)).fetchone()[0]
        left_a = con.execute("SELECT COUNT(*) FROM agents WHERE eais_id=?", (EAIS_ID,)).fetchone()[0]
    print("Демо-данные удалены: " + ", ".join(f"{k} — {v}" for k, v in n.items()))
    print(f"  осталось: людей {left_u}, запросов {left_r}, соглашений {left_g}, записей в реестре агентов {left_a}")


def main():
    ap = argparse.ArgumentParser(description="Демо-данные для показа согласования (только PD_MODE=test)")
    ap.add_argument("--yes", action="store_true", help="подтверждение: без него скрипт ничего не делает")
    ap.add_argument("--clean", action="store_true", help="удалить ранее созданные демо-данные")
    ap.add_argument("--db", help="путь к базе (по умолчанию — база приложения)")
    args = ap.parse_args()

    from app import db
    if args.db:
        db.DB_PATH = Path(args.db).resolve()
    from app import llm
    guard(db, llm, db.DB_PATH, explicit_db=bool(args.db))
    if not args.yes:
        fail("Отказ: нужен явный флаг --yes (защита от случайного запуска). "
             "Создать: --yes; убрать: --yes --clean")

    from app import approvals
    print(f"База: {db.DB_PATH}")
    if args.clean:
        clean(db, approvals)
    else:
        from app import auth
        from app import main as main_mod
        seed(db, auth, approvals, main_mod)


if __name__ == "__main__":
    main()
