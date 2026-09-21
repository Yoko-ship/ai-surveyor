"""
Контрольные примеры входа через Telegram (app/telegram.py).
Запуск:  sandbox\\.venv\\Scripts\\python.exe tests/test_telegram.py

Сеть не нужна: initData собираем сами вымышленным токеном «123456:TEST».
Проверяем:
  1) правильно подписанный initData проходит проверку;
  2) испорченная подпись, подмена поля и чужой токен — не проходят;
  3) устаревший auth_date (больше суток) — не проходит;
  4) без токена бота — честный режим «браузер», сервер не падает;
  5) незнакомый telegram_id внутрь не пускается, а получает «нужна регистрация»
     (анкета в мини-приложении, app/registration.py) — заявка в users при этом НЕ создаётся;
  6) повторный вход тем же telegram_id ничего не плодит.
Все записи, созданные тестом в базе, удаляются в конце.
"""
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db                                          # noqa: E402
from app import telegram as tg                              # noqa: E402

TOKEN = "123456:TEST"                     # вымышленный токен, боту не принадлежит
TG_ID = "999000111"                       # вымышленный telegram_id для проверки заявки
MADE_LOGINS = []


def make_init_data(token: str = TOKEN, auth_date: int = None, user: dict = None) -> str:
    """Собирает initData так, как это делает Telegram: подпись по алгоритму Web Apps."""
    user = user or {"id": int(TG_ID), "first_name": "Тест", "last_name": "Тестов", "username": "testuser"}
    fields = {"query_id": "AAEtest", "user": json.dumps(user, ensure_ascii=False),
              "auth_date": str(auth_date or int(time.time()))}
    dcs = tg.data_check_string(list(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_valid_init_data():
    res = tg.check_init_data(make_init_data(), TOKEN)
    assert res["ok"], res
    assert res["user"]["id"] == int(TG_ID), res


def test_broken_hash():
    data = make_init_data()
    broken = data[:-4] + ("0000" if not data.endswith("0000") else "1111")
    res = tg.check_init_data(broken, TOKEN)
    assert not res["ok"] and "подпись" in res["reason"].lower(), res


def test_tampered_field():
    """Подменили пользователя, подпись оставили прежней — не проходит."""
    data = make_init_data()
    tampered = data.replace("AAEtest", "AAEother")
    res = tg.check_init_data(tampered, TOKEN)
    assert not res["ok"], res


def test_wrong_token():
    res = tg.check_init_data(make_init_data(), "654321:OTHER")
    assert not res["ok"], res


def test_expired():
    old = int(time.time()) - 48 * 3600
    res = tg.check_init_data(make_init_data(auth_date=old), TOKEN)
    assert not res["ok"] and "устарел" in res["reason"].lower(), res


def test_no_token_and_garbage():
    assert not tg.check_init_data(make_init_data(), "")["ok"]
    assert not tg.check_init_data("", TOKEN)["ok"]
    assert not tg.check_init_data("мусор без подписи", TOKEN)["ok"]


def test_unknown_id_needs_registration():
    """Незнакомый telegram_id: анкета в мини-приложении, а не заявка админу."""
    with db.tx() as con:
        con.execute("DELETE FROM users WHERE telegram_id=?", (TG_ID,))
        out = tg.link_or_request(con, {"id": int(TG_ID), "first_name": "Тест", "last_name": "Тестов"})
        MADE_LOGINS.append("tg" + TG_ID)
        assert out["status"] == tg.ST_REG, out
        assert out["telegram_id"] == TG_ID and "+998" in out["reason"], out
        # пользователь не заводится: пока человек не заполнил анкету, его в системе нет
        n = con.execute("SELECT COUNT(*) FROM users WHERE telegram_id=?", (TG_ID,)).fetchone()[0]
        assert n == 0, n
        again = tg.link_or_request(con, {"id": int(TG_ID), "first_name": "Тест"})
        assert again["status"] == tg.ST_REG, again


def test_pending_user_goes_to_registration():
    """Заявка, поданная по старому пути, достраивается той же анкетой."""
    with db.tx() as con:
        con.execute("DELETE FROM users WHERE telegram_id=?", (TG_ID,))
        con.execute("INSERT INTO users (login, full_name, role, password_hash, salt, status, telegram_id,"
                    " created_at) VALUES (?,?,?,?,?,?,?,?)",
                    ("tg" + TG_ID, "Тест Тестов", "агент", "x", "y", "ожидает подтверждения",
                     TG_ID, db.now()))
        MADE_LOGINS.append("tg" + TG_ID)
        out = tg.link_or_request(con, {"id": int(TG_ID), "first_name": "Тест"})
        assert out["status"] == tg.ST_REG, out


def test_bad_user_id():
    with db.tx() as con:
        out = tg.link_or_request(con, {"first_name": "Без идентификатора"})
    assert out["status"] == tg.ST_ERR, out


def cleanup():
    with db.tx() as con:
        for login in set(MADE_LOGINS):
            r = db.rows(con, "SELECT id FROM users WHERE login=?", login)
            if r:
                con.execute("DELETE FROM sessions WHERE user_id=?", (r[0]["id"],))
                con.execute("DELETE FROM login_codes WHERE user_id=?", (r[0]["id"],))
            con.execute("DELETE FROM audit WHERE entity=?", ("user:" + login,))
            con.execute("DELETE FROM users WHERE login=?", (login,))
    print("тестовые записи удалены")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    try:
        for t in tests:
            t()
            print("ок:", t.__name__)
    finally:
        cleanup()
    print("\nвсе проверки входа через Telegram пройдены:", len(tests))


if __name__ == "__main__":
    main()
