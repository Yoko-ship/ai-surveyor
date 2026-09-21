"""
Обновление справочников на постоянном диске из базы образа (app/refsync.py).

Сценарий: «старая» база диска (нет osgor_activities, нет коэффициентов и ставок спецтехники,
нет версии тарифа регулятора, её id занят версией калибровки) плюс рабочие данные — пользователь,
запрос, объект, расчёты по двум версиям тарифа, результат проверки, прогноз решения, настройка.
Старт с новой базой образа, собранной tools/db_build.py с нуля:

   1  справочники новые: ОСГОР 934 вида, все коэффициенты и ставки образа на месте;
   2  рабочие данные целы: users, requests, calculations, check_results, decision_outcomes, app_settings;
   3  id версий тарифа прежние; версия калибровки на своём id, версия регулятора — на новом,
      минимальные ставки образа привязаны к ней;
   4  ручные правки целы: коэффициент из админки, продукт из админки, пометка юриста у правила;
      лишняя строка сборки удалена, лишнее правило со ссылкой на него оставлено;
   5  PRAGMA foreign_key_check — 0 нарушений, обновление быстрее 5 с;
   6  повторный старт ничего не делает (отпечаток совпал);
   7  образ с битой ссылкой — откат, отпечаток прежний, причина в журнале;
   8  база занята — «занято», без ожидания.

Всё — во временной папке; рабочая data/surveyor.db не открывается.
Запуск:  set PYTHONIOENCODING=utf-8 && set STORAGE_DIR=<копия> && sandbox\\.venv\\Scripts\\python.exe tests\\test_reference_sync.py
"""
import contextlib
import io
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from app import db, refsync  # noqa: E402

FAILS = []


def ok(title, cond):
    print(("  ок   " if cond else "  СБОЙ ") + title)
    if not cond:
        FAILS.append(title)


def build_image(path: Path):
    """Чистая база образа: tools/db_build.py в свой файл (рабочая база не трогается)."""
    import db_build
    old, old_app = db_build.DB, db.DB_PATH
    # db_build подключает app.lawwatch, а тот при импорте пишет в db.DB_PATH — пусть пишет в образ
    db_build.DB = db.DB_PATH = path
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            db_build.main()
    finally:
        db_build.DB, db.DB_PATH = old, old_app
        db.close_pool()


def one(con, sql, *a):
    return con.execute(sql, a).fetchone()[0]


def make_old(image: Path, disk: Path):
    """База диска «до обновления» с рабочими данными и ручными правками."""
    db.snapshot(image, disk)
    con = sqlite3.connect(disk)
    con.execute("PRAGMA foreign_keys = OFF")
    con.execute("DROP TABLE osgor_activities")
    spec = one(con, "SELECT source FROM coefficients ORDER BY id DESC LIMIT 1")   # спецтехника — в конце
    con.execute("DELETE FROM coefficients WHERE source=?", (spec,))
    con.execute("DELETE FROM base_rates WHERE source=?", (spec,))
    reg = one(con, "SELECT id FROM tariff_versions WHERE level='регулятор'")
    con.execute("DELETE FROM min_rates WHERE tariff_version_id=?", (reg,))
    con.execute("DELETE FROM tariff_versions WHERE id=?", (reg,))
    con.execute("DELETE FROM app_settings WHERE key=?", (refsync.HASH_KEY,))
    con.execute("PRAGMA foreign_keys = ON")
    # калибровка заняла освободившийся id версии регулятора
    con.execute("INSERT INTO tariff_versions (id, level, name, document_ref, effective_from)"
                " VALUES (?, 'компания', 'Калибровка от 2026-09-20', 'calibration_run:1', '2026-09-20')", (reg,))
    # строка сборки поменялась (в образе множитель другой) — должна обновиться
    c1 = con.execute("SELECT id, factor_code, option_code, class_code, multiplier FROM coefficients"
                     " WHERE calibrated=0 ORDER BY id LIMIT 2").fetchall()
    con.execute("UPDATE coefficients SET multiplier=9.99 WHERE id=?", (c1[0][0],))
    # правка из админки — должна остаться
    con.execute("UPDATE coefficients SET multiplier=7.77 WHERE id=?", (c1[1][0],))
    con.execute("INSERT INTO audit (ts, who, action, entity, detail) VALUES (?,?,?,?,?)",
                ("2026-09-20T10:00:00", "admin", "коэффициент", f"{c1[1][1]}/{c1[1][2]}",
                 '{"factor_code": "%s", "option_code": "%s", "class_code": %s, "multiplier": 7.77}'
                 % (c1[1][1], c1[1][2], "null" if c1[1][3] is None else '"%s"' % c1[1][3])))
    # лишняя строка старой сборки — удалится; продукт из админки — останется
    con.execute("INSERT INTO coefficients (factor_code,factor_name,class_code,option_code,option_name,multiplier,"
                "calibrated,source) VALUES ('old_f','старый фактор',NULL,'x','x',1.1,0,'старая сборка')")
    con.execute("INSERT INTO products (code,name,pricing_mode,status) VALUES ('T999','Тестовый продукт','ставка','тест')")
    cls = one(con, "SELECT code FROM classes LIMIT 1")
    con.execute("INSERT INTO product_classes VALUES ('T999', ?, 1)", (cls,))
    # пометка юриста у правила сборки и старое правило, на которое ссылается проверка
    rule = one(con, "SELECT code FROM rules ORDER BY code LIMIT 1")
    con.execute("UPDATE rules SET review_status='требует пересмотра', review_reason='тест' WHERE code=?", (rule,))
    con.execute("INSERT INTO rules (code,name,severity,description) VALUES ('OLD_RULE','старое','подсказка','—')")
    # рабочие данные
    con.execute("INSERT INTO users (login, full_name, role, password_hash, salt, status, created_at)"
                " VALUES ('refsync_user', 'Тестовый Пользователь', 'агент', 'h', 's', 'активен', '2026-09-20')")
    uid = one(con, "SELECT id FROM users WHERE login='refsync_user'")
    prod = one(con, "SELECT code FROM products WHERE status='действует' LIMIT 1")
    con.execute("INSERT INTO requests (product_code, branch, created_at, created_by_user_id) VALUES (?, 'тест', ?, ?)",
                (prod, "2026-09-20T10:00:00", uid))
    rid = one(con, "SELECT MAX(id) FROM requests")
    con.execute("INSERT INTO objects (request_id, object_type) VALUES (?, 'здание')", (rid,))
    oid = one(con, "SELECT MAX(id) FROM objects")
    comp = one(con, "SELECT id FROM tariff_versions WHERE level='компания' ORDER BY id LIMIT 1")
    for tv in (comp, reg):
        con.execute("INSERT INTO calculations (request_id, object_id, tariff_version_id, premium, verdict, explanation,"
                    " created_at) VALUES (?,?,?,100,'ок','{}','2026-09-20T10:00:00')", (rid, oid, tv))
    calc = one(con, "SELECT MAX(id) FROM calculations")
    con.execute("INSERT INTO check_results (calculation_id, rule_code, status) VALUES (?, 'OLD_RULE', 'пройдено')", (calc,))
    con.execute("INSERT INTO decision_outcomes (request_id, calculation_id, probability, sent_at, decision)"
                " VALUES (?, ?, 70, '2026-09-20T10:00:00', 'согласован')", (rid, calc))
    con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES ('REFSYNC_TEST', 'x', '2026-09-20')"
                " ON CONFLICT(key) DO UPDATE SET value='x'")
    con.commit()
    con.close()
    return {"reg_old": reg, "comp": comp, "coef_changed": c1[0], "coef_admin": c1[1], "rule": rule}


# app_settings сверяется отдельно: в ней появляется отпечаток справочников
WORK = ["users", "requests", "objects", "calculations", "check_results", "decision_outcomes",
        "sessions", "request_reviewers", "valuation_settings", "company_financials", "photos"]


def counts(path, tables):
    con = sqlite3.connect(path)
    try:
        return {t: one(con, f"SELECT COUNT(*) FROM {t}") for t in tables}
    finally:
        con.close()


def dump(path, table):
    con = sqlite3.connect(path)
    try:
        return sorted(map(tuple, con.execute(f"SELECT * FROM {table}")), key=str)
    finally:
        con.close()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="refsync-"))
    original = db.DB_PATH
    try:
        image = tmp / "image" / "surveyor.db"
        image.parent.mkdir()
        t = time.monotonic()
        build_image(image)
        print(f"  образ собран tools/db_build.py за {time.monotonic() - t:.1f} с")
        # у версии регулятора в сборке минимальных ставок нет — добавляем одну, чтобы проверить перенос id
        i = sqlite3.connect(image)
        i.execute("INSERT INTO min_rates (tariff_version_id, product_code, class_code, payer_type, min_rate_pct)"
                  " SELECT v.id, '1323', '13', NULL, 0.1 FROM tariff_versions v WHERE v.level='регулятор'")
        i.commit()
        i.close()
        disk = tmp / "disk" / "surveyor.db"
        disk.parent.mkdir()
        m = make_old(image, disk)
        before = counts(disk, WORK)
        settings_before = counts(disk, ["app_settings"])["app_settings"]
        work_before = {t: dump(disk, t) for t in ("users", "requests", "calculations", "check_results",
                                                   "decision_outcomes")}

        # старт сервера: ensure_schema() + sync_on_start()
        db.DB_PATH = disk
        db.ensure_schema()
        db.close_pool()
        res = refsync.sync_on_start(image)
        print("  итог:", res["status"], res.get("ms"), "мс")

        img = sqlite3.connect(image)
        con = sqlite3.connect(disk)
        ok("0. статус «обновлено»", res["status"] == "обновлено")
        n_osgor = one(con, "SELECT COUNT(*) FROM osgor_activities")
        ok(f"1. ОСГОР: {n_osgor} видов деятельности (в образе {one(img, 'SELECT COUNT(*) FROM osgor_activities')})",
           n_osgor == 934 and n_osgor == one(img, "SELECT COUNT(*) FROM osgor_activities"))
        key = "factor_code||'/'||option_code||'/'||IFNULL(class_code,'')"
        img_keys = {r[0] for r in img.execute(f"SELECT {key} FROM coefficients")}
        disk_keys = {r[0] for r in con.execute(f"SELECT {key} FROM coefficients")}
        ok(f"1а. коэффициенты: все {len(img_keys)} ключей образа на диске, лишний old_f удалён",
           img_keys <= disk_keys and "old_f/x/" not in disk_keys and len(disk_keys) == len(img_keys))
        bkey = "class_code||'/'||object_type"
        ok("1б. базовые ставки образа все на диске",
           {r[0] for r in img.execute(f"SELECT {bkey} FROM base_rates")}
           <= {r[0] for r in con.execute(f"SELECT {bkey} FROM base_rates")})
        for tb in ("checklists", "rules", "products", "classes", "perils", "preventive_measures"):
            ok(f"1в. {tb}: на диске не меньше строк, чем в образе",
               one(con, f"SELECT COUNT(*) FROM {tb}") >= one(img, f"SELECT COUNT(*) FROM {tb}"))
        cid, f, o, c, mult = m["coef_changed"]
        img_mult = one(img, "SELECT multiplier FROM coefficients WHERE factor_code=? AND option_code=?"
                            " AND class_code IS ?", f, o, c)
        ok("1г. изменённая сборкой строка обновлена (9.99 → %s)" % img_mult,
           one(con, "SELECT multiplier FROM coefficients WHERE factor_code=? AND option_code=? AND class_code IS ?",
               f, o, c) == img_mult)

        after = counts(disk, WORK)
        ok("2. рабочие таблицы: строк столько же " + str(after), after == before)
        ok("2а. users/requests/calculations/check_results/decision_outcomes — строки те же",
           all(dump(disk, t) == rows for t, rows in work_before.items()))
        ok("2б. app_settings: своя настройка на месте, добавлен только отпечаток",
           one(con, "SELECT value FROM app_settings WHERE key='REFSYNC_TEST'") == "x"
           and one(con, "SELECT COUNT(*) FROM app_settings") == settings_before + 1
           and one(con, "SELECT value FROM app_settings WHERE key=?", refsync.HASH_KEY) == res["hash"])

        reg_new = one(con, "SELECT id FROM tariff_versions WHERE level='регулятор'")
        ok("3. версия компании — прежний id %s; калибровка на id %s; регулятор получил новый id %s"
           % (m["comp"], m["reg_old"], reg_new),
           one(con, "SELECT name FROM tariff_versions WHERE id=?", m["comp"]) == "Тарифная политика INSON"
           and one(con, "SELECT document_ref FROM tariff_versions WHERE id=?", m["reg_old"]) == "calibration_run:1"
           and reg_new not in (m["comp"], m["reg_old"]))
        ok("3а. расчёты ссылаются на прежние версии",
           sorted(r[0] for r in con.execute("SELECT tariff_version_id FROM calculations WHERE request_id="
                                             "(SELECT MAX(id) FROM requests)")) == sorted([m["comp"], m["reg_old"]]))
        img_reg = one(img, "SELECT id FROM tariff_versions WHERE level='регулятор'")
        n_img_reg = one(img, "SELECT COUNT(*) FROM min_rates WHERE tariff_version_id=?", img_reg)
        ok(f"3б. минимальные ставки версии регулятора: {n_img_reg} в образе → на диске у id {reg_new}",
           n_img_reg == 1 and one(con, "SELECT COUNT(*) FROM min_rates WHERE tariff_version_id=?", reg_new) == n_img_reg
           and one(con, "SELECT COUNT(*) FROM min_rates WHERE tariff_version_id=?", m["reg_old"]) == 0)

        _, f2, o2, c2, _ = m["coef_admin"]
        ok("4. коэффициент из админки сохранён (7.77)",
           one(con, "SELECT multiplier FROM coefficients WHERE factor_code=? AND option_code=? AND class_code IS ?",
               f2, o2, c2) == 7.77)
        ok("4а. продукт из админки T999 и его класс на месте",
           one(con, "SELECT COUNT(*) FROM products WHERE code='T999'") == 1
           and one(con, "SELECT COUNT(*) FROM product_classes WHERE product_code='T999'") == 1)
        ok("4б. пометка юриста у правила %s сохранена" % m["rule"],
           one(con, "SELECT review_status FROM rules WHERE code=?", m["rule"]) == "требует пересмотра")
        ok("4в. правило OLD_RULE (на него ссылается проверка) оставлено и названо в отчёте",
           one(con, "SELECT COUNT(*) FROM rules WHERE code='OLD_RULE'") == 1
           and any("OLD_RULE" in x for x in res["оставлены"].get("rules", [])))

        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        ok(f"5. foreign_key_check: {len(fk)} нарушений", not fk)
        ok(f"5а. обновление за {res.get('ms')} мс (< 5000)", res.get("ms", 99999) < 5000)
        ok("5б. в журнале запись «справочники обновлены из сборки»",
           one(con, "SELECT COUNT(*) FROM audit WHERE action='справочники обновлены из сборки'") == 1)
        con.close()

        snap = {t: dump(disk, t) for t, *_ in refsync.TABLES}
        res2 = refsync.sync_on_start(image)
        ok("6. повторный старт: «%s», справочники не менялись" % res2["status"],
           res2["status"] == "актуально" and all(dump(disk, t) == rows for t, rows in snap.items()))

        # 7. образ с битой ссылкой — откат
        bad = tmp / "bad" / "surveyor.db"
        bad.parent.mkdir()
        db.snapshot(image, bad)
        b = sqlite3.connect(bad)
        b.execute("PRAGMA foreign_keys = OFF")
        b.execute("INSERT INTO product_classes VALUES ('0339', 'нет-такого-класса', 9)")
        b.commit()
        b.close()
        res3 = refsync.sync_on_start(bad)
        con = sqlite3.connect(disk)
        ok("7. битая ссылка в образе → «%s», отпечаток прежний, справочники прежние, причина в журнале"
           % res3["status"],
           res3["status"] == "откат"
           and one(con, "SELECT value FROM app_settings WHERE key=?", refsync.HASH_KEY) == res["hash"]
           and all(dump(disk, t) == rows for t, rows in snap.items())
           and one(con, "SELECT COUNT(*) FROM audit WHERE action='справочники не обновлены: нарушены ссылки'") == 1)
        con.close()

        # 8. база занята другим писателем
        lock = sqlite3.connect(disk, isolation_level=None)
        lock.execute("BEGIN IMMEDIATE")
        t = time.monotonic()
        res4 = refsync.sync(bad, busy_ms=100)
        lock.execute("ROLLBACK")
        lock.close()
        ok("8. база занята → «%s» за %.2f с" % (res4["status"], time.monotonic() - t),
           res4["status"] == "занято" and time.monotonic() - t < 2)
        img.close()
    finally:
        db.DB_PATH = original
        db.close_pool()
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILS:
        print(f"\nСБОЕВ: {len(FAILS)}")
        sys.exit(1)
    print("\nВсе проверки обновления справочников пройдены.")


if __name__ == "__main__":
    main()
