"""
Проверка модуля верификации пользователей (app/auth.py) на ВРЕМЕННОЙ КОПИИ базы.

Запуск из корня:  set PYTHONIOENCODING=utf-8 && sandbox\\.venv\\Scripts\\python.exe tests\\test_auth.py
Перед этим:       python tools\\db_build.py   (чтобы схема с таблицами users / sessions / login_codes применилась)

Основная база data/surveyor.db не трогается: копируется во временный файл, app.db.DB_PATH указывает на копию.
Функции маршрутов вызываются напрямую (httpx в окружении нет), поэтому проверяется логика с параметром con.

Сценарий: bootstrap первого админа → заявка агента без ID (422) → с ID («ожидает подтверждения») →
вход до подтверждения (403) → подтверждение админом (audit) → вход работает → неверный пароль (401) →
сессия истекает → выход → второй шаг с кодом Telegram (dev-режим) → права ролей → блокировка.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException                     # noqa: E402

from app import db                                    # noqa: E402
from app import auth                                  # noqa: E402

SRC_DB = db.ROOT / "data" / "surveyor.db"
PASSED = []


def ok(name, cond):
    assert cond, f"ПРОВАЛ: {name}"
    PASSED.append(name)
    print("  ✓", name)


def expect(status, fn, *args):
    try:
        fn(*args)
    except HTTPException as e:
        assert e.status_code == status, f"ожидали {status}, получили {e.status_code}: {e.detail}"
        return e.detail
    raise AssertionError(f"ожидали ошибку {status}, а вызов прошёл")


def last_audit(con, action):
    r = db.rows(con, "SELECT * FROM audit WHERE action=? ORDER BY id DESC LIMIT 1", action)
    return r[0] if r else None


def run():
    with db.tx() as con:
        con.executescript(db.SCHEMA.read_text(encoding="utf-8"))
        # копия базы могла содержать чужих пользователей — тест рассчитан на пустую таблицу
        con.execute("DELETE FROM login_codes"); con.execute("DELETE FROM sessions"); con.execute("DELETE FROM users")
        # агент в реестре компании — для сверки
        con.execute("INSERT OR IGNORE INTO agents (eais_id, name, kind, status) VALUES (?,?,?,?)",
                    ("TEST-EAIS-001", "Тестовый агент Ахмедов", "физ", "активен"))

    # 1. bootstrap первого админа
    with db.tx() as con:
        a = auth.register_user(con, auth.RegisterIn(full_name="Администратор Тестовый", login="Admin",
                                                    password="secret-123", role="админ"))
        ok("первый админ создан активным (bootstrap)", a["status"] == "активен" and a["login"] == "admin")
        au = last_audit(con, "создан первый администратор")
        ok("bootstrap записан в журнал", au and au["who"] == "admin")

    # 2. второй админ уже не bootstrap
    with db.tx() as con:
        b = auth.register_user(con, auth.RegisterIn(full_name="Второй Админ", login="admin2",
                                                    password="secret-123", role="админ"))
        ok("второй админ — ожидает подтверждения", b["status"] == "ожидает подтверждения")

    # 3. агент без ID из ЕАИС
    with db.tx() as con:
        detail = expect(422, auth.register_user, con,
                        auth.RegisterIn(full_name="Агент Без Айди", login="agent0", password="password1"))
        ok("агент без ID → 422 с русским текстом и ссылкой на 3845", "ID из ЕАИС" in detail and "3845" in detail)

    # 4. агент с ID
    with db.tx() as con:
        ag = auth.register_user(con, auth.RegisterIn(full_name="Ахмедов Агент", phone="+998901234567", login="agent1",
                                                     password="password1", branch="Ташкент", agent_eais_id="TEST-EAIS-001"))
        ok("агент с ID → ожидает подтверждения", ag["status"] == "ожидает подтверждения")
        au = last_audit(con, "заявка на доступ")
        ok("заявка в журнале с признаком «найден в реестре»", au and '"in_registry": true' in au["detail"])
        expect(409, auth.register_user, con,
               auth.RegisterIn(full_name="Дубль", login="agent1", password="password1", agent_eais_id="X-1"))
        PASSED.append("дубль логина → 409"); print("  ✓ дубль логина → 409")

    # 5. вход до подтверждения
    with db.tx() as con:
        detail = expect(403, auth.login_user, con, auth.LoginIn(login="agent1", password="password1"))
        ok("вход до подтверждения → 403", "не подтверждена" in detail)

    # 6. админ смотрит заявки и подтверждает
    with db.tx() as con:
        admin = auth._user_by_login(con, "admin")
        pend = auth.pending_users(con)
        me = [p for p in pend if p["login"] == "agent1"][0]
        ok("в списке заявок агент со сверкой по реестру", me["registry"] and me["registry"]["name"].startswith("Тестовый"))
        auth.approve_user(con, admin, me["id"])
        au = last_audit(con, "подтверждён пользователь")
        ok("подтверждение записано в журнал от имени админа", au and au["who"] == "admin" and f"user:{me['id']}" == au["entity"])
        # неадмин не должен пройти require('админ')
        agent_user = auth._user_by_login(con, "agent1")
        expect(403, auth.check_role, agent_user, ("админ",))
        ok("агент не проходит require('админ')", True)
        ok("админ проходит любую роль", auth.check_role(admin, ("актуарий",))["login"] == "admin")

    # 7. вход после подтверждения, сессия
    with db.tx() as con:
        r = auth.login_user(con, auth.LoginIn(login="agent1", password="password1"), ip="127.0.0.1", user_agent="test")
        ok("вход после подтверждения → сессия", r["step"] == "done" and r["token"])
        token = r["token"]
        u = auth.session_user(con, token)
        ok("по токену находится пользователь", u and u["login"] == "agent1")
        ok("last_login проставлен", u["last_login"] is not None)

    # 8. неверный пароль и несуществующий логин — одинаковый ответ
    with db.tx() as con:
        d1 = expect(401, auth.login_user, con, auth.LoginIn(login="agent1", password="wrong-pass"))
        d2 = expect(401, auth.login_user, con, auth.LoginIn(login="nobody", password="wrong-pass"))
        ok("неверный пароль → 401, текст не выдаёт, что именно неверно", d1 == d2 == "Неверный логин или пароль")

    # 9. сессия продлевается и истекает
    with db.tx() as con:
        before = db.rows(con, "SELECT expires_at FROM sessions WHERE token=?", token)[0]["expires_at"]
        con.execute("UPDATE sessions SET expires_at=? WHERE token=?",
                    ((datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds"), token))
        auth.session_user(con, token)
        after = db.rows(con, "SELECT expires_at FROM sessions WHERE token=?", token)[0]["expires_at"]
        ok("активность продлевает сессию до 12 часов", after >= before[:16])
        con.execute("UPDATE sessions SET expires_at=? WHERE token=?",
                    ((datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds"), token))
        ok("истёкшая сессия не принимается", auth.session_user(con, token) is None)
        ok("истёкшая сессия удалена", not db.rows(con, "SELECT 1 FROM sessions WHERE token=?", token))

    # 10. выход
    with db.tx() as con:
        token = auth.login_user(con, auth.LoginIn(login="agent1", password="password1"))["token"]
        ok("logout удаляет сессию", auth.logout_session(con, token) and auth.session_user(con, token) is None)
        ok("выход в журнале", last_audit(con, "выход")["who"] == "agent1")
        ok("logout без токена не падает", auth.logout_session(con, None) is False)

    # 11. второй шаг: код в Telegram (бота нет → dev-режим отдаёт код)
    os.environ["SURVEYOR_DEV"] = "1"
    with db.tx() as con:
        con.execute("UPDATE users SET telegram_id='123456' WHERE login='agent1'")
        r = auth.login_user(con, auth.LoginIn(login="agent1", password="password1"))
        ok("с telegram_id вход требует код", r["step"] == "code" and r["delivery"].startswith("недоступно"))
        ok("в dev-режиме код возвращается", len(r.get("dev_code", "")) == 6)
        d = expect(401, auth.verify_login_code, con, auth.CodeIn(login="agent1", code="000000"))
        ok("неверный код → 401 с числом попыток", "попыток" in d)
        r2 = auth.verify_login_code(con, auth.CodeIn(login="agent1", code=r["dev_code"]))
        ok("верный код → сессия", r2["step"] == "done" and auth.session_user(con, r2["token"])["login"] == "agent1")
        ok("код одноразовый", not db.rows(con, "SELECT 1 FROM login_codes"))
        # просроченный код
        r = auth.login_user(con, auth.LoginIn(login="agent1", password="password1"))
        con.execute("UPDATE login_codes SET expires_at=?", ((datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds"),))
        d = expect(401, auth.verify_login_code, con, auth.CodeIn(login="agent1", code=r["dev_code"]))
        ok("просроченный код → 401", "истёк" in d)
    del os.environ["SURVEYOR_DEV"]
    with db.tx() as con:
        r = auth.login_user(con, auth.LoginIn(login="agent1", password="password1"))
        ok("без dev-режима код в ответе не отдаётся", "dev_code" not in r)

    # 12. матрица прав
    with db.tx() as con:
        agent_user = auth._user_by_login(con, "agent1")
        ok("агент: расчёт да, решение нет, калибровка нет",
           auth.can(agent_user, "расчёт") and not auth.can(agent_user, "решение") and not auth.can(agent_user, "калибровка"))

    # 13. блокировка уволенного агента: сессии гаснут, вход закрыт
    with db.tx() as con:
        admin = auth._user_by_login(con, "admin")
        con.execute("UPDATE users SET telegram_id=NULL WHERE login='agent1'")
        token = auth.login_user(con, auth.LoginIn(login="agent1", password="password1"))["token"]
        auth.block_user(con, admin, agent_user["id"], "уволен")
        ok("после блокировки сессия недействительна", auth.session_user(con, token) is None)
        d = expect(403, auth.login_user, con, auth.LoginIn(login="agent1", password="password1"))
        ok("вход заблокированного → 403", "заблокирована" in d)
        ok("блокировка в журнале", last_audit(con, "заблокирован пользователь")["who"] == "admin")
        expect(422, auth.block_user, con, admin, admin["id"])
        ok("админ не может заблокировать себя", True)
        # исключение из реестра компании тоже закрывает вход
        con.execute("UPDATE users SET status='активен' WHERE login='agent1'")
        con.execute("UPDATE agents SET status='исключён' WHERE eais_id='TEST-EAIS-001'")
        d = expect(403, auth.login_user, con, auth.LoginIn(login="agent1", password="password1"))
        ok("агент, исключённый из реестра → 403", "реестра" in d)
        # отклонение заявки
        u2 = auth._user_by_login(con, "admin2")
        auth.reject_user(con, admin, u2["id"], "не сотрудник")
        ok("отклонённая заявка удалена и в журнале", auth._user_by_login(con, "admin2") is None
           and last_audit(con, "отклонена заявка")["who"] == "admin")

    # 14. хэш пароля
    h, s = auth.hash_password("abc12345")
    ok("PBKDF2: 64 hex-символа, соль 32, проверка проходит, другой пароль — нет",
       len(h) == 64 and len(s) == 32 and auth.check_password("abc12345", h, s) and not auth.check_password("abc12346", h, s))
    ok("страница входа и маршруты на месте", (db.ROOT / "app" / "login.html").exists()
       and {r.path for r in auth.router.routes} >= {"/auth/register", "/auth/login", "/auth/verify-code", "/auth/logout",
                                                    "/auth/me", "/auth/pending", "/login"})


def main():
    assert SRC_DB.exists(), "нет data/surveyor.db — выполните python tools/db_build.py"
    tmp = Path(tempfile.mkdtemp(prefix="auth_")) / "surveyor_auth_test.db"
    shutil.copy(SRC_DB, tmp)
    original = db.DB_PATH
    db.DB_PATH = tmp
    try:
        run()
    finally:
        db.DB_PATH = original
        shutil.rmtree(tmp.parent, ignore_errors=True)
    print(f"\nВсе проверки пройдены ({len(PASSED)}). Основная база не изменена, временная копия удалена.")


if __name__ == "__main__":
    main()
