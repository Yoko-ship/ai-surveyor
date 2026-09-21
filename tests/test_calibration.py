"""
Проверка модуля калибровки на СИНТЕТИЧЕСКИХ данных.

Запуск из корня:  set PYTHONIOENCODING=utf-8 && sandbox\\.venv\\Scripts\\python.exe tests\\test_calibration.py
Перед этим:       python tools\\db_build.py   (чтобы схема с таблицами claims / calibration_runs применилась)

Основная база data/surveyor.db НЕ трогается: она копируется во временный файл, и на время теста
app.db.DB_PATH указывает на копию. Все договоры и убытки здесь выдуманы (source = 'СИНТЕТИКА тест').

Заложенная модель (класс 8, «Склад», 400 договоров, 30 учтённых убытков):
  - две равные половины по защите: protection = 'none' и 'alarm', по 200 договоров с одинаковыми
    последовательностями сумм (1, 2, 3 млрд) и конструкций (reinforced / mixed / wood);
  - у 'none' 20 убытков, у 'alarm' — 10, все по 80 млн: частота вдвое выше при равной тяжести,
    значит убыточность 'none' ровно вдвое выше 'alarm';
  - итого выплат 2,4 млрд на экспозицию 798 млрд сум-лет → burning cost портфеля ≈ 0,30 %;
  - плюс 2 убытка со статусом 'отказ' — они не должны учитываться.
"""
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db                                   # noqa: E402
from app import calibration as cal                   # noqa: E402

import json                                          # noqa: E402

SRC_DB = db.ROOT / "data" / "surveyor.db"
SYNTH = "СИНТЕТИКА тест"
PERIOD = ("2024-01-01", "2024-12-31")
SUMS = [1e9, 2e9, 3e9]
CONSTR = ["reinforced", "mixed", "wood"]
PAID = 80e6


def make_synthetic(con):
    """400 договоров и 32 убытка (30 учитываемых) в копии базы."""
    assert not db.rows(con, "SELECT 1 FROM claims LIMIT 1"), "в исходной базе уже есть убытки — тест рассчитан на пустую таблицу"
    ids = {"none": [], "alarm": []}
    for prot in ("none", "alarm"):
        for i in range(200):
            # конструкция идёт своим циклом (i // 3), чтобы не совпадать с циклом сумм (i % 3)
            factors = {"construction": CONSTR[(i // 3) % 3], "activity": "warehouse", "protection": prot,
                       "seismic": "z7", "wear": "mid", "loss_history": "clean", "franchise": "f0"}
            cur = con.execute("INSERT INTO requests (external_no, branch, product_code, policyholder, created_at, status)"
                              " VALUES (?,?,?,?,?,?)",
                              (f"SYN-{prot}-{i:03d}", SYNTH, "0807", f"Синтетический склад {prot} {i}",
                               f"2024-{1 + i % 12:02d}-15T10:00:00", "утверждено"))
            rid = cur.lastrowid
            con.execute("INSERT INTO objects (request_id, object_type, value_amount, sum_insured, attributes) VALUES (?,?,?,?,?)",
                        (rid, "Склад", SUMS[i % 3], SUMS[i % 3],
                         json.dumps({"factors": factors, "term_from": "2024-01-01", "term_to": "2024-12-31", "synthetic": True},
                                    ensure_ascii=False)))
            ids[prot].append(rid)
    claim_no = 0
    for prot, step in (("none", 10), ("alarm", 20)):
        for i in range(0, 200, step):
            claim_no += 1
            con.execute("INSERT INTO claims (request_id, external_no, event_date, reported_date, paid_date, cause, claimed, paid, status, source)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (ids[prot][i], f"SYN-CL-{claim_no:03d}", "2024-06-01", "2024-06-03", "2024-07-01", "пожар",
                         PAID * 1.1, PAID, "оплачен", SYNTH))
    for rid in (ids["none"][5], ids["alarm"][5]):   # отказы — не должны попасть в статистику
        con.execute("INSERT INTO claims (request_id, external_no, event_date, cause, claimed, status, source) VALUES (?,?,?,?,?,?,?)",
                    (rid, "SYN-REJ", "2024-08-01", "не страховой случай", 500e6, "отказ", SYNTH))
    # term_to = 31.12 минус term_from = 01.01 даёт 365 дней в 2024 (високосный год, но разница дат — 365)
    return ids


def by_scope(items, kind, **match):
    out = [it for it in items if it["kind"] == kind and all(it.get(k) == v for k, v in match.items())]
    assert len(out) == 1, (kind, match, len(out))
    return out[0]


def test_calibration_cycle(con):
    # --- формулы ---
    assert cal.credibility(0) == 0 and abs(cal.credibility(250) - 0.5) < 1e-12 and cal.credibility(5000) == 1.0
    assert abs(cal.risk_load(1.0, 0.075, 400, 0.95) - 1.2 * 1.645 * (0.925 / 30) ** 0.5) < 1e-9
    print("формулы: Z(250) = 0,5; Z(5000) = 1; рисковая надбавка по формуле Росстрахнадзора считается")

    # --- экспозиция и убытки ---
    d = cal.exposure_and_losses(con, *PERIOD)
    g = d["groups"][("8", "Склад")]
    assert d["contracts"] == 400 and d["claims"] == 30, (d["contracts"], d["claims"])
    assert g["n"] == 400 and g["m"] == 30
    assert abs(g["exposure"] - 798e9) < 1e3, g["exposure"]
    assert abs(g["q"] - 0.075) < 1e-12
    assert abs(g["k"] - PAID / (798e9 / 400)) < 1e-9
    assert abs(g["burning_cost"] - 0.3008) < 0.001, g["burning_cost"]          # заложено ≈ 0,30 %
    print(f"экспозиция: n = {g['n']}, m = {g['m']}, экспозиция {g['exposure']/1e9:.0f} млрд, q = {g['q']:.4f}, k = {g['k']:.4f},"
          f" burning cost {g['burning_cost']:.4f}% (заложено 0,30%), приведённый к базе {g['burning_cost_base']:.4f}%")

    # --- предложение ---
    before = db.rows(con, "SELECT net_rate_pct FROM base_rates WHERE class_code='8' AND object_type='Склад'")[0]["net_rate_pct"]
    p1 = cal.propose(con, *PERIOD)
    s = p1["summary"]
    assert s["enough"] and s["message"] is None
    base = by_scope(p1["items"], "base_rate", class_code="8", object_type="Склад")
    assert 0 < base["Z"] <= 1 and abs(base["Z"] - (400 / 1000) ** 0.5) < 1e-3
    assert base["current"] == round(before, 4)
    assert min(base["current"], base["observed"]) - 1e-9 <= base["proposed"] <= max(base["current"], base["observed"]) + 1e-9
    assert base["risk_load_pct"] is not None and base["risk_load_pct"] > 0
    none = by_scope(p1["items"], "coefficient", factor_code="protection", option_code="none")
    alarm = by_scope(p1["items"], "coefficient", factor_code="protection", option_code="alarm")
    assert abs(none["observed"] - 2.0) < 0.05, none["observed"]                  # заложено ровно вдвое
    assert 0 < none["Z"] <= 1 and abs(none["Z"] - (200 / 1000) ** 0.5) < 1e-3
    assert none["proposed"] > alarm["proposed"] and none["apply"] and not alarm["apply"]
    assert none["current"] < none["proposed"] < none["observed"]
    run = db.rows(con, "SELECT * FROM calibration_runs WHERE id=?", p1["run_id"])[0]
    assert run["status"] == "предложено" and run["contracts"] == 400 and run["claims"] == 30
    # ничего не изменилось в справочниках до утверждения
    assert db.rows(con, "SELECT net_rate_pct, calibrated FROM base_rates WHERE class_code='8' AND object_type='Склад'")[0] == {"net_rate_pct": before, "calibrated": 0}
    print(f"предложение № {p1['run_id']}: база {base['current']} → наблюдаемое {base['observed']} → предлагаемое {base['proposed']}, Z = {base['Z']},"
          f" рисковая надбавка {base['risk_load_pct']} п.п. ({base['risk_load_share']*100:.0f}% нетто)")
    print(f"  protection none: {none['current']} → {none['observed']} → {none['proposed']} (Z = {none['Z']}); alarm (база): {alarm['proposed']}")
    for it in p1["items"]:
        if it["kind"] == "coefficient" and it["factor_code"] == "construction":
            print(f"  construction {it['option_code']}: {it['current']} → {it['observed']} → {it['proposed']} (Z = {it['Z']})")
    print(f"  всего предложений к применению: {s['proposals']}, пропущено групп: {len(s['skipped'])}")

    # --- утверждение ---
    versions_before = con.execute("SELECT COUNT(*) FROM tariff_versions").fetchone()[0]
    a = cal.approve(con, p1["run_id"], "тест-андеррайтер")
    assert a["base_rates"] == 1 and a["coefficients"] >= 1
    assert con.execute("SELECT COUNT(*) FROM tariff_versions").fetchone()[0] == versions_before + 1
    v = db.rows(con, "SELECT * FROM tariff_versions WHERE id=?", a["tariff_version_id"])[0]
    assert v["level"] == "компания" and v["name"].startswith("Калибровка от") and v["effective_from"] == date.today().isoformat()
    br = db.rows(con, "SELECT * FROM base_rates WHERE class_code='8' AND object_type='Склад'")[0]
    assert br["calibrated"] == 1 and abs(br["net_rate_pct"] - base["proposed"]) < 1e-9 and br["source"] == f"калибровка run {p1['run_id']}"
    assert abs(br["frequency"] - 0.075) < 1e-9 and br["severity"] is not None
    cn = db.rows(con, "SELECT * FROM coefficients WHERE factor_code='protection' AND option_code='none'")[0]
    assert cn["calibrated"] == 1 and abs(cn["multiplier"] - none["proposed"]) < 1e-9
    ca = db.rows(con, "SELECT * FROM coefficients WHERE factor_code='protection' AND option_code='alarm'")[0]
    assert ca["calibrated"] == 0 and ca["multiplier"] == 1.0, "базовый вариант не трогаем"
    assert db.rows(con, "SELECT status, approved_by FROM calibration_runs WHERE id=?", p1["run_id"])[0] == {"status": "утверждено", "approved_by": "тест-андеррайтер"}
    assert db.rows(con, "SELECT 1 FROM audit WHERE action='калибровка утверждена' AND who='тест-андеррайтер'")
    # повторно утвердить нельзя
    try:
        cal.approve(con, p1["run_id"], "кто-то ещё")
        assert False, "повторное утверждение должно быть отклонено"
    except Exception as e:
        assert getattr(e, "status_code", None) == 409
    print(f"утверждено: версия тарифа № {a['tariff_version_id']} «{v['name']}», ставок {a['base_rates']}, множителей {a['coefficients']}, calibrated = 1")

    # --- второй прогон стартует от новых значений ---
    p2 = cal.propose(con, *PERIOD)
    base2 = by_scope(p2["items"], "base_rate", class_code="8", object_type="Склад")
    none2 = by_scope(p2["items"], "coefficient", factor_code="protection", option_code="none")
    assert abs(base2["current"] - base["proposed"]) < 1e-9
    assert abs(none2["current"] - none["proposed"]) < 1e-9
    assert abs(none2["observed"] - 2.0) < 0.05
    assert none2["proposed"] > none["proposed"], "второй шаг продолжает движение к наблюдаемому"
    r = cal.reject(con, p2["run_id"], "тест-андеррайтер", "проверка отклонения")
    assert r["status"] == "отклонено"
    assert db.rows(con, "SELECT note FROM calibration_runs WHERE id=?", p2["run_id"])[0]["note"] == "проверка отклонения"
    print(f"второй прогон № {p2['run_id']}: база стартует от {base2['current']} (= прошлое предложение), none {none2['current']} → {none2['proposed']}; отклонён с заметкой")


def main():
    assert SRC_DB.exists(), "нет data/surveyor.db — выполните python tools/db_build.py"
    tmp = Path(tempfile.mkdtemp(prefix="calib_")) / "surveyor_synthetic.db"
    db.snapshot(SRC_DB, tmp)          # штатная копия SQLite: учитывает журнал WAL рабочей базы
    original = db.DB_PATH
    db.DB_PATH = tmp
    try:
        with db.tx() as con:
            con.executescript(db.SCHEMA.read_text(encoding="utf-8"))   # на случай, если основная база собрана до новых таблиц
            make_synthetic(con)
            test_calibration_cycle(con)
        # копия — единственное место, где есть синтетика
        with db.tx() as con:
            assert con.execute("SELECT COUNT(*) FROM requests WHERE branch=?", (SYNTH,)).fetchone()[0] == 400
    finally:
        db.DB_PATH = original
        db.close_pool()                 # соединения пула держат файл копии открытым
        shutil.rmtree(tmp.parent, ignore_errors=True)
    import sqlite3
    main_con = sqlite3.connect(SRC_DB)
    assert main_con.execute("SELECT COUNT(*) FROM requests WHERE branch=?", (SYNTH,)).fetchone()[0] == 0, "синтетика попала в основную базу!"
    main_con.close()
    print("Все проверки пройдены. Основная база не изменена, временная копия удалена.")


if __name__ == "__main__":
    main()
