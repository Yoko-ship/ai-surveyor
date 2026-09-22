"""
Вход через Google (app/google_auth.py) — на ВРЕМЕННОЙ КОПИИ базы.

Запуск из корня проекта:
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_google_auth.py

Сеть не трогаем: обращения к Google (exchange_code_for_tokens и fetch_tokeninfo) подменяются
своими функциями, настройки — подменой llm.get. Рабочая data/surveyor.db не открывается вообще
(tests/tmpdb.py подменяет db.DB_PATH копией).

Что проверяется:
   1  вход не настроен: status.configured = False, GET /auth/google → 503;
   2  возврат от Google без state, с чужим state и с просроченным state — отказ 400;
   3  ответ Google выдан другому приложению (aud), пришёл не от Google (iss), просрочен (exp) — 403;
   4  почта не подтверждена Google (email_verified = "false") — 403;
   5  ограничение по доменам: @gmail.com при GOOGLE_ALLOWED_DOMAINS=insuranceon.uz — 403,
      @insuranceon.uz проходит, пустая настройка пропускает любой адрес;
   6  незнакомый аккаунт: обмен даёт «register», анкета создаёт активного «сотрудника»
      с google_sub, email и согласием в pd_consents (основное);
   7  повторный вход тем же аккаунтом: обмен сразу даёт «done», тот же users.id, новых строк нет;
   8  одноразовый код обмена: второй раз — 400, просроченный — 400;
   9  ключ анкеты одноразовый: второй register тем же reg_token — 400;
  10  телефон в анкете: без согласия на его обработку — 422; с согласием — в pd_consents
      две строки (основное и телефон);
  11  настройки читаются не только из .env, но и из .secrets.env (на временном файле;
      рабочий .secrets.env проекта тесты не открывают).
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import HTTPException                       # noqa: E402
from starlette.requests import Request                  # noqa: E402

from tmpdb import temp_db                               # noqa: E402
from app import db, google_auth, llm                    # noqa: E402

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
SUB = "тест-google-sub-100500"                          # вымышленный, ни у кого такого нет
EMAIL = "testov@insuranceon.uz"
SUB_PHONE = "тест-google-sub-100501"                    # второй вымышленный аккаунт: анкета с телефоном
EMAIL_PHONE = "svyaznov@insuranceon.uz"
GMAIL = "testov@gmail.com"

PASSED = []
SETTINGS = {}                                           # подменённые настройки: ключ → значение
TOKENS = {"id_token": "id-token-заглушка"}              # что «вернул» Google на обмен кода
INFO = {}                                               # что «вернул» tokeninfo


# --------------------------------------------------------------------------- #
#  Заглушки вместо Google и настроек
# --------------------------------------------------------------------------- #

def fake_get(key: str, default: str = None) -> str:
    return SETTINGS.get(key, "" if default is None else default)


def fake_exchange(code: str, code_verifier: str) -> dict:
    assert code_verifier, "PKCE-секрет должен доходить до обмена кода"
    return dict(TOKENS)


def fake_tokeninfo(id_token: str) -> dict:
    return dict(INFO)


def tokeninfo(**over) -> dict:
    """Нормальный ответ Google, поверх которого тест портит одно поле."""
    base = {"aud": CLIENT_ID, "iss": "https://accounts.google.com",
            "exp": int((datetime.now() + timedelta(minutes=5)).timestamp()),
            "email_verified": "true", "email": EMAIL, "sub": SUB,
            "given_name": "Тест", "family_name": "Тестов", "name": "Тест Тестов"}
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
#  Мелочи
# --------------------------------------------------------------------------- #

def ok(name, cond):
    assert cond, f"ПРОВАЛ: {name}"
    PASSED.append(name)
    print("  ✓", name)


def expect(status, fn, *args, **kw):
    try:
        fn(*args, **kw)
    except HTTPException as e:
        assert e.status_code == status, f"ожидали {status}, получили {e.status_code}: {e.detail}"
        return e.detail
    raise AssertionError(f"ожидали ошибку {status}, а вызов прошёл")


def fake_request(cookies: dict = None) -> Request:
    """Минимальный Request: маршрутам нужны только адрес собеседника и cookie."""
    headers = []
    if cookies:
        headers.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode("utf-8")))
    return Request({"type": "http", "method": "GET", "path": "/auth/google", "headers": headers,
                    "query_string": b"", "client": ("127.0.0.1", 0), "scheme": "http",
                    "root_path": "", "server": ("test", 80), "http_version": "1.1"})


def go_through_google(info: dict, next_url: str = "/tg") -> dict:
    """Полный проход: начало входа → возврат от Google. Возвращает то же, что handle_callback."""
    INFO.clear(); INFO.update(info)
    with db.tx() as con:
        started = google_auth.start_login(con, next_url, "127.0.0.1")
    with db.tx() as con:
        return google_auth.handle_callback(con, "код-от-google", started["state"], started["state"])


def users_with_sub(con, sub: str) -> list:
    return db.rows(con, "SELECT * FROM users WHERE google_sub=?", sub)


# --------------------------------------------------------------------------- #
#  Сценарий
# --------------------------------------------------------------------------- #

def run():
    with db.tx() as con:
        con.executescript(db.SCHEMA.read_text(encoding="utf-8"))
        con.execute("DELETE FROM google_oauth_states")
        con.execute("DELETE FROM google_login_codes")
        con.execute("DELETE FROM users WHERE google_sub IS NOT NULL")

    # --- 1. вход не настроен ---
    SETTINGS.clear()
    SETTINGS["SERVER_URL"] = "http://127.0.0.1:8000"
    ok("не настроено: configured = False", google_auth.get_status()["configured"] is False)
    ok("не настроено: есть подсказка администратору", bool(google_auth.get_status()["hint"]))
    expect(503, google_auth.go, fake_request())

    # дальше вход настроен
    SETTINGS["GOOGLE_CLIENT_ID"] = CLIENT_ID
    SETTINGS["GOOGLE_CLIENT_SECRET"] = "test-secret"
    ok("настроено: configured = True", google_auth.get_status()["configured"] is True)

    # --- 2. state ---
    with db.tx() as con:
        expect(400, google_auth.take_state, con, "", "")
        started = google_auth.start_login(con, "/tg", "127.0.0.1")
    with db.tx() as con:
        expect(400, google_auth.take_state, con, started["state"], "чужой-state")
        ok("state без cookie не принимается", True)
    with db.tx() as con:                              # state есть в cookie, но нет в базе
        expect(400, google_auth.take_state, con, "неизвестный", "неизвестный")

    with db.tx() as con:                              # просроченный state
        st = google_auth.start_login(con, "/tg")["state"]
        con.execute("UPDATE google_oauth_states SET expires_at=? WHERE state=?",
                    ((datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds"), st))
    with db.tx() as con:
        expect(400, google_auth.take_state, con, st, st)
    with db.tx() as con:
        ok("просроченный state удалён из базы",
           not db.rows(con, "SELECT 1 FROM google_oauth_states WHERE state=?", st))

    # --- 3. проверки ответа Google ---
    expect(403, go_through_google, tokeninfo(aud="чужое-приложение.apps.googleusercontent.com"))
    ok("токен другого приложения отклонён (aud)", True)
    expect(403, go_through_google, tokeninfo(iss="https://зло.example"))
    ok("ответ не от Google отклонён (iss)", True)
    expect(403, go_through_google,
           tokeninfo(exp=int((datetime.now() - timedelta(minutes=1)).timestamp())))
    ok("просроченный ответ отклонён (exp)", True)

    # --- 4. почта не подтверждена ---
    expect(403, go_through_google, tokeninfo(email_verified="false"))
    ok("неподтверждённая почта отклонена", True)

    # --- 5. домены ---
    SETTINGS["GOOGLE_ALLOWED_DOMAINS"] = "insuranceon.uz"
    expect(403, go_through_google, tokeninfo(email=GMAIL))
    ok("чужой домен почты отклонён", True)
    out = go_through_google(tokeninfo())
    ok("свой домен почты проходит", bool(out["auth"]) and out["token"] is None)
    with db.tx() as con:                              # ни один отказ пользователя не создал
        ok("после отказов пользователей не создано", not users_with_sub(con, SUB))

    SETTINGS["GOOGLE_ALLOWED_DOMAINS"] = ""           # пусто — пускаем любой аккаунт
    google_auth.check_domain(GMAIL)
    ok("пустой список доменов пропускает любой адрес", True)
    SETTINGS["GOOGLE_ALLOWED_DOMAINS"] = "insuranceon.uz"

    # --- 6. незнакомый аккаунт: обмен → анкета → новый сотрудник ---
    out = go_through_google(tokeninfo(), next_url="/tg?page=calc")
    ok("возврат ведёт туда, откуда начали", out["next"] == "/tg?page=calc")
    with db.tx() as con:
        ex = google_auth.exchange(con, out["auth"])
    ok("новый аккаунт: шаг «register»", ex["step"] == "register" and bool(ex["reg_token"]))
    ok("в анкету подставлены ФИО и почта из Google",
       ex["profile"]["full_name"] == "Тестов Тест" and ex["profile"]["email"] == EMAIL)

    reg_token = ex["reg_token"]
    data = google_auth.RegisterIn(reg_token=reg_token, full_name="Тестов Тест Тестович",
                                  department="Департамент андеррайтинга", position="менеджер",
                                  consent=True)
    with db.tx() as con:
        created = google_auth.register(con, data, "127.0.0.1")
    ok("создан активный «сотрудник»",
       created["role"] == "сотрудник" and created["status"] == "активен" and bool(created["token"]))
    uid = created["user_id"]
    with db.tx() as con:
        u = db.rows(con, "SELECT * FROM users WHERE id=?", uid)[0]
        ok("в users записаны google_sub и email", u["google_sub"] == SUB and u["email"] == EMAIL)
        cons = db.rows(con, "SELECT * FROM pd_consents WHERE user_id=?", uid)
        ok("согласие на обработку ПД записано (основное)",
           len(cons) == 1 and cons[0]["scope"] == "основное" and bool(cons[0]["consent_text_hash"]))
        j = json.dumps(db.rows(con, "SELECT * FROM audit WHERE entity=?", f"user:{uid}"),
                       ensure_ascii=False)
        ok("в журнале почта только маской", EMAIL not in j and "t***v@insuranceon.uz" in j)

    # --- 10. телефон в анкете: только вместе с согласием на его обработку ---
    out_p = go_through_google(tokeninfo(sub=SUB_PHONE, email=EMAIL_PHONE))
    with db.tx() as con:
        ex_p = google_auth.exchange(con, out_p["auth"])
    bad = google_auth.RegisterIn(reg_token=ex_p["reg_token"], full_name="Связнов Связ Связович",
                                 department="Департамент андеррайтинга", position="менеджер",
                                 consent=True, phone="+998901234567")
    with db.tx() as con:
        detail = expect(422, google_auth.register, con, bad, "127.0.0.1")
    ok("телефон без согласия на его обработку не принимается", "согласие на его обработку" in detail)
    with db.tx() as con:
        ok("после отказа пользователь не создан", not users_with_sub(con, SUB_PHONE))

    out_p = go_through_google(tokeninfo(sub=SUB_PHONE, email=EMAIL_PHONE))
    with db.tx() as con:
        ex_p = google_auth.exchange(con, out_p["auth"])
    good = google_auth.RegisterIn(reg_token=ex_p["reg_token"], full_name="Связнов Связ Связович",
                                  department="Департамент андеррайтинга", position="менеджер",
                                  consent=True, consent_phone=True, phone="+998901234567")
    with db.tx() as con:
        created_p = google_auth.register(con, good, "127.0.0.1")
    with db.tx() as con:
        cons_p = db.rows(con, "SELECT * FROM pd_consents WHERE user_id=? ORDER BY scope",
                         created_p["user_id"])
        scopes = [c["scope"] for c in cons_p]
        ok("с согласием на телефон в pd_consents две строки (основное и телефон)",
           len(cons_p) == 2 and set(scopes) == {"основное", "телефон"})
        ok("у обеих строк есть версия, хэш и канал «вход через Google»",
           all(c["version"] and c["consent_text_hash"] and c["channel"] == "вход через Google"
               for c in cons_p))
        u_p = db.rows(con, "SELECT * FROM users WHERE id=?", created_p["user_id"])[0]
        ok("номер телефона сохранён", bool(u_p["phone"]))

    # анкета без телефона согласия на телефон не требует и второй строки не создаёт
    with db.tx() as con:
        cons_main = db.rows(con, "SELECT * FROM pd_consents WHERE user_id=?", uid)
    ok("без телефона строки «телефон» нет",
       len(cons_main) == 1 and cons_main[0]["scope"] == "основное")

    # --- 9. ключ анкеты одноразовый ---
    with db.tx() as con:
        expect(400, google_auth.register, con, data, "127.0.0.1")
    ok("второй раз тем же ключом анкеты нельзя", True)

    # --- 7. повторный вход тем же аккаунтом ---
    with db.tx() as con:
        before = len(db.rows(con, "SELECT id FROM users"))
    out2 = go_through_google(tokeninfo())
    ok("повторный вход сразу с сессией", bool(out2["token"]))
    with db.tx() as con:
        ex2 = google_auth.exchange(con, out2["auth"])
    ok("повторный вход: шаг «done»", ex2["step"] == "done" and ex2["token"] == out2["token"])
    ok("тот же пользователь", ex2["user"]["id"] == uid)
    with db.tx() as con:
        ok("новых пользователей не появилось", len(db.rows(con, "SELECT id FROM users")) == before)
        ok("на один аккаунт Google — одна учётная запись", len(users_with_sub(con, SUB)) == 1)

    # --- 8. одноразовый код обмена ---
    with db.tx() as con:
        expect(400, google_auth.exchange, con, out2["auth"])
    ok("код обмена второй раз не работает", True)

    out3 = go_through_google(tokeninfo())
    with db.tx() as con:                              # состарим код руками
        con.execute("UPDATE google_login_codes SET expires_at=? WHERE used_at IS NULL AND kind='обмен'",
                    ((datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds"),))
    with db.tx() as con:
        expect(400, google_auth.exchange, con, out3["auth"])
    ok("просроченный код обмена не работает", True)

    # чужой код
    with db.tx() as con:
        expect(400, google_auth.exchange, con, "выдуманный-код")
    ok("выдуманный код обмена не работает", True)


def secrets_file_test():
    """Настройки читаются и из .env, и из .secrets.env. Рабочий .secrets.env проекта не трогаем:
    llm.ENV_FILES на время подменяем временными файлами."""
    import tempfile
    real_files, real_get = llm.ENV_FILES, llm.get
    llm.get = real_get                                  # здесь нужна настоящая функция, без заглушки
    tmp = Path(tempfile.mkdtemp(prefix="surveyor-env-"))
    try:
        env = tmp / ".env"
        sec = tmp / ".secrets.env"
        env.write_text("GOOGLE_CLIENT_ID=из-env\n", encoding="utf-8")
        # BOM в начале и переводы строк Windows — ровно как в рабочем файле
        sec.write_text("\ufeffGOOGLE_CLIENT_SECRET=из-secrets\r\n"
                       "GOOGLE_CLIENT_ID=не-должен-победить\r\n"
                       "# комментарий\r\n"
                       "GOOGLE_ALLOWED_DOMAINS=insuranceon.uz\r\n", encoding="utf-8")
        llm.ENV_FILES = (env, sec)
        # чтобы проверять именно файл: убираем ту же настройку из базы и из окружения
        with db.tx() as con:
            con.execute("DELETE FROM app_settings WHERE key='GOOGLE_ALLOWED_DOMAINS'")
        os.environ.pop("GOOGLE_ALLOWED_DOMAINS", None)
        vals = llm._env_file()
        ok("значение из .secrets.env прочитано", vals.get("GOOGLE_CLIENT_SECRET") == "из-secrets")
        ok("первый ключ файла с BOM читается без лишних знаков",
           not any(k.startswith("\ufeff") for k in vals))
        ok(".env главнее .secrets.env", vals.get("GOOGLE_CLIENT_ID") == "из-env")
        ok("комментарии пропускаются", all(not k.startswith("#") for k in vals))
        ok("настройка из .secrets.env доходит до llm.get",
           llm.get("GOOGLE_ALLOWED_DOMAINS") == "insuranceon.uz")
    finally:
        llm.ENV_FILES = real_files
        for f in tmp.glob("*"):
            f.unlink()
        tmp.rmdir()


def main():
    real_get, real_exchange, real_info = llm.get, google_auth.exchange_code_for_tokens, google_auth.fetch_tokeninfo
    llm.get = fake_get
    google_auth.exchange_code_for_tokens = fake_exchange
    google_auth.fetch_tokeninfo = fake_tokeninfo
    try:
        with temp_db("surveyor-test-google.db"):
            db.ensure_schema()          # как при старте сервера: новые таблицы и колонки
            run()
            llm.get = real_get                          # чтение настроек проверяем без заглушки
            secrets_file_test()
    finally:
        llm.get = real_get
        google_auth.exchange_code_for_tokens = real_exchange
        google_auth.fetch_tokeninfo = real_info
    print(f"\nВход через Google: все проверки пройдены ({len(PASSED)}).")


if __name__ == "__main__":
    main()
