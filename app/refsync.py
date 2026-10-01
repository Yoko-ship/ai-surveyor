"""
Обновление справочников на постоянном диске из базы, собранной в образе.

Зачем: на сервере база живёт на постоянном диске (STORAGE_DIR), а собранная при сборке образа
data/surveyor.db (tools/db_build.py + tools/market_stats.py) переносится туда только один раз —
init_storage() копирует её, если базы на диске нет. Всё, что добавлено в справочники позже,
на диск не попадало. Здесь — безопасное доведение справочников при старте:

  * отпечаток справочников образа (sha256 содержимого справочных таблиц) хранится на диске
    в app_settings.REFERENCE_BUILD_HASH; совпал — ничего не делаем;
  * не совпал — в одной транзакции (BEGIN IMMEDIATE) строки образа сопоставляются со строками диска
    по естественному ключу; есть — обновляются, нет — добавляются; лишние строки диска удаляются,
    только если это не ручная правка и на них никто не ссылается; иначе остаются и попадают в журнал;
  * ручные правки (админка, калибровка, робот-юрист) не затираются — признаки см. _Manual;
  * id версий тарифа сохраняются: на них ссылаются calculations и min_rates;
  * после обновления PRAGMA foreign_key_check: новое нарушение — откат, сервер работает на старых
    справочниках, причина — в журнале audit.

Рабочие таблицы (users, requests, calculations, решения, app_settings, valuation_settings,
company_financials, …) не читаются и не меняются.
"""
import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from . import db

log = logging.getLogger("surveyor.refsync")

HASH_KEY = "REFERENCE_BUILD_HASH"
WHO = "система: справочники"

# Справочные таблицы: заполняются только tools/db_build.py и tools/market_stats.py.
# (таблица, естественный ключ, режим, колонки, которые из образа не переносятся)
#   upsert — обновить совпавшие, добавить новые, лишние удалить (если можно);
#   add    — только добавить недостающие: таблицу меняет и сам сервер (админка норм износа,
#            ежедневная загрузка статистики НАПП), его строки новее образа.
# Порядок — от родителей к детям: так вставка не нарушает ссылок; удаление идёт в обратном порядке.
# Суррогатный id (coefficients, base_rates, min_rates, checklists) не переносится: на него никто
# не ссылается, а в старой базе и в образе он у одной и той же строки разный.
TABLES = [
    ("groups", ("code",), "upsert", ()),
    ("classes", ("code",), "upsert", ()),
    ("tariff_versions", ("id",), "versions", ()),
    ("products", ("code",), "upsert", ()),
    ("product_classes", ("product_code", "class_code"), "upsert", ()),
    ("perils", ("code",), "upsert", ()),
    ("min_rates", ("tariff_version_id", "product_code", "class_code", "payer_type"), "upsert", ("id",)),
    ("coefficients", ("factor_code", "option_code", "class_code"), "upsert", ("id",)),
    ("base_rates", ("class_code", "object_type"), "upsert", ("id",)),
    ("load_components", ("code",), "upsert", ()),
    ("checklists", ("scope_type", "scope_code", "doc_name"), "upsert", ("id",)),
    # пометки «требует пересмотра» ставит робот-юрист и снимает человек — из образа не берём
    ("rules", ("code",), "upsert", ("review_status", "review_reason", "review_since")),
    ("preventive_measures", ("code",), "upsert", ()),
    ("osgor_activities", ("no",), "upsert", ()),
    ("depreciation_norms", ("code",), "add", ()),
    ("market_stats", ("report_date", "row_key"), "add", ()),
    # претензии и подразделения НАПП (01.10.2026): как market_stats — сервер дописывает их сам раз в сутки
    ("napp_claims", ("report_date", "scope", "key"), "add", ()),
    ("napp_branches", ("report_date", "company_key", "region_key"), "add", ()),
    # пометки к строкам market_stats (итог комплексного заменён суммой пакетов), 01.10.2026
    ("market_stats_notes", ("report_date", "row_key"), "add", ()),
]

RETRY_SEC = 30
RETRY_TIMES = 10


def _cols(con, table) -> list:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def _open_image(path) -> sqlite3.Connection:
    # только чтение: файл образа не меняем (в WAL без -shm режим ro может не открыться — тогда обычный)
    try:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        return con
    except sqlite3.Error:
        return sqlite3.connect(str(path))


def _key_sort(v):
    # NULL и разные типы в одной колонке: сортируем устойчиво
    return tuple((x is None, type(x).__name__, x if x is not None else 0) for x in v)


def fingerprint(con) -> str:
    """Отпечаток справочников: sha256 от колонок и отсортированных строк всех справочных таблиц."""
    h = hashlib.sha256()
    for table, _key, _mode, _skip in TABLES:
        # время загрузки строк в отпечаток не входит: иначе он менялся бы при каждой сборке образа
        cols = [c for c in _cols(con, table) if c not in ("loaded_at", "fetched_at", "created_at", "updated_at")]
        h.update(f"\n#{table}:{','.join(cols)}\n".encode("utf-8"))
        if not cols:
            continue
        rows = [tuple(r) for r in con.execute(f"SELECT {', '.join(cols)} FROM {table}")]
        for r in sorted(rows, key=_key_sort):
            h.update(json.dumps(r, ensure_ascii=False, default=str).encode("utf-8"))
    return h.hexdigest()


class _Manual:
    """
    Признаки ручной правки на диске. Такие строки не обновляются и не удаляются:
      tariff_versions  — создана из админки (audit «новая версия тарифов», version:<id>)
                         или калибровкой (document_ref calibration_run:<id>);
      products         — заведён/изменён из админки (audit «продукт», статус не «действует»);
      product_classes, min_rates — у продукта или версии тарифа выше признак ручной правки,
                         либо ставка добавлена из админки (audit «минимальная ставка»);
      coefficients     — правка из админки (audit «коэффициент») или калибровка (calibrated=1);
      base_rates       — калибровка (calibrated=1);
      depreciation_norms — удалена админом (audit «удалена норма износа»): не возвращаем.
    """

    def __init__(self, con):
        self.versions, self.products, self.min_rates, self.coefs, self.norms_deleted = set(), set(), set(), set(), set()
        for action, entity, detail in con.execute(
                "SELECT action, entity, detail FROM audit WHERE action IN "
                "('новая версия тарифов','продукт','минимальная ставка','коэффициент','удалена норма износа')"):
            try:
                d = json.loads(detail) if detail else {}
            except ValueError:
                d = {}
            d = d if isinstance(d, dict) else {}
            if action == "новая версия тарифов" and (entity or "").startswith("version:"):
                try:
                    self.versions.add(int(entity.split(":", 1)[1]))
                except ValueError:
                    pass
            elif action == "продукт":
                self.products.add(entity)
            elif action == "минимальная ставка":
                self.min_rates.add((d.get("tariff_version_id"), d.get("product_code") or entity,
                                    d.get("class_code"), d.get("payer_type")))
            elif action == "коэффициент":
                self.coefs.add((d.get("factor_code"), d.get("option_code"), d.get("class_code")))
            elif action == "удалена норма износа" and (entity or "").startswith("depreciation_norms:"):
                self.norms_deleted.add(entity.split(":", 1)[1])
        for vid, ref in con.execute("SELECT id, document_ref FROM tariff_versions"):
            if (ref or "").startswith("calibration_run:"):
                self.versions.add(vid)
        for code, status in con.execute("SELECT code, status FROM products"):
            if status != "действует":
                self.products.add(code)

    def __call__(self, table, row: dict) -> bool:
        if table == "tariff_versions":
            return row["id"] in self.versions
        if table == "products":
            return row["code"] in self.products
        if table == "product_classes":
            return row["product_code"] in self.products
        if table == "min_rates":
            return (row["product_code"] in self.products or row["tariff_version_id"] in self.versions
                    or (row["tariff_version_id"], row["product_code"], row["class_code"], row["payer_type"])
                    in self.min_rates)
        if table == "coefficients":
            return bool(row.get("calibrated")) or (row["factor_code"], row["option_code"], row["class_code"]) in self.coefs
        if table == "base_rates":
            return bool(row.get("calibrated"))
        return False


def _fk_violations(con) -> set:
    return {tuple(r) for r in con.execute("PRAGMA foreign_key_check")}


def _apply(con, img, manual: _Manual) -> dict:
    """Переносит справочники образа в открытую транзакцию con. Возвращает сводку по таблицам."""
    report, leftovers = {}, {}
    vmap = {}                                   # id версии тарифа в образе -> id на диске
    stale = []                                  # (таблица, rowid) — удалить после всех вставок
    for table, key, mode, skip in TABLES:
        icols = _cols(img, table)
        dcols = _cols(con, table)
        if not icols or not dcols:
            report[table] = {"пропущено": "таблицы нет" + (" в образе" if not icols else " на диске")}
            continue
        cols = [c for c in icols if c in dcols and c not in skip]
        stat = {"добавлено": 0, "обновлено": 0, "без изменений": 0, "ручная правка": 0}
        img_rows = [dict(zip(icols, r)) for r in img.execute(f"SELECT {', '.join(icols)} FROM {table}")]
        img_rows.sort(key=lambda r: _key_sort([r.get(c) for c in icols]))
        disk = {}
        for r in con.execute(f"SELECT rowid, {', '.join(dcols)} FROM {table} ORDER BY rowid"):
            d = dict(zip(["rowid"] + dcols, r))
            disk.setdefault(tuple(d.get(k) for k in key), []).append(d)
        matched = set()

        if mode == "versions":
            # id сохраняется: занят ручной версией (калибровка, админка) — образ получает новый id
            used = {r["id"] for rows in disk.values() for r in rows}
            for r in sorted(img_rows, key=lambda x: x["id"]):
                old = disk.get((r["id"],), [None])[0]
                if old is not None and not manual(table, old):
                    vmap[r["id"]] = r["id"]
                    matched.add(old["rowid"])
                    if any(old.get(c) != r.get(c) for c in cols):
                        sets = [c for c in cols if c != "id"]
                        con.execute(f"UPDATE {table} SET {', '.join(c + '=?' for c in sets)} WHERE id=?",
                                    [r[c] for c in sets] + [r["id"]])
                        stat["обновлено"] += 1
                    else:
                        stat["без изменений"] += 1
                    continue
                new_id = r["id"] if old is None and r["id"] not in used else max(used | {0}) + 1
                ins = {c: r[c] for c in cols}
                ins["id"] = new_id
                con.execute(f"INSERT INTO {table} ({', '.join(ins)}) VALUES ({', '.join('?' * len(ins))})",
                            list(ins.values()))
                used.add(new_id)
                vmap[r["id"]] = new_id
                stat["добавлено"] += 1
                if old is not None:
                    stat["id занят ручной версией"] = stat.get("id занят ручной версией", 0) + 1
            # версии не удаляем никогда: на них ссылаются старые расчёты (воспроизводимость)
            report[table] = stat
            continue

        for r in img_rows:
            if table == "min_rates" and r.get("tariff_version_id") in vmap:
                r["tariff_version_id"] = vmap[r["tariff_version_id"]]
            if table == "depreciation_norms" and r.get("code") in manual.norms_deleted:
                stat["ручная правка"] += 1
                continue
            k = tuple(r.get(c) for c in key)
            cands = [d for d in disk.get(k, []) if d["rowid"] not in matched]
            if cands:
                old = cands[0]
                matched.add(old["rowid"])
                if mode == "add":
                    stat["без изменений"] += 1
                    continue
                if manual(table, old):
                    stat["ручная правка"] += 1
                    continue
                sets = [c for c in cols if c not in key]
                if any(old.get(c) != r.get(c) for c in sets):
                    con.execute(f"UPDATE {table} SET {', '.join(c + '=?' for c in sets)} WHERE rowid=?",
                                [r[c] for c in sets] + [old["rowid"]])
                    stat["обновлено"] += 1
                else:
                    stat["без изменений"] += 1
                continue
            con.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [r[c] for c in cols])
            stat["добавлено"] += 1

        if mode == "upsert":
            for rows in disk.values():
                for d in rows:
                    if d["rowid"] in matched:
                        continue
                    if manual(table, d):
                        leftovers.setdefault(table, []).append(_label(table, key, d) + " (ручная правка)")
                    else:
                        stale.append((table, key, d))
        report[table] = stat

    # лишние строки: удаляем от детей к родителям; на строку ссылаются (расчёты, запросы,
    # результаты проверок) — SQLite не даст удалить, строка остаётся и попадает в журнал
    order = {t[0]: i for i, t in enumerate(TABLES)}
    for table, key, d in sorted(stale, key=lambda x: -order[x[0]]):
        con.execute("SAVEPOINT refsync_row")
        try:
            con.execute(f"DELETE FROM {table} WHERE rowid=?", (d["rowid"],))
            con.execute("RELEASE refsync_row")
            report[table]["удалено"] = report[table].get("удалено", 0) + 1
        except sqlite3.IntegrityError:
            con.execute("ROLLBACK TO refsync_row")
            con.execute("RELEASE refsync_row")
            leftovers.setdefault(table, []).append(_label(table, key, d) + " (на неё ссылаются)")
    return {"таблицы": report, "оставлены": leftovers, "версии": vmap}


def _label(table, key, d) -> str:
    return "/".join("" if d.get(k) is None else str(d.get(k)) for k in key)


def sync(image_path, target_path=None, busy_ms: int = 2000) -> dict:
    """
    Доводит справочники базы target_path (по умолчанию db.DB_PATH) до базы образа image_path.
    Возвращает {"status": "актуально"|"обновлено"|"откат"|"занято"|"нет образа", ...}.
    """
    target_path = Path(target_path or db.DB_PATH)
    image_path = Path(image_path)
    if not image_path.exists():
        return {"status": "нет образа"}
    t0 = time.monotonic()
    img = _open_image(image_path)
    try:
        new_hash = fingerprint(img)
        con = sqlite3.connect(str(target_path), isolation_level=None)   # транзакцией управляем сами
        try:
            con.execute("PRAGMA foreign_keys = ON")
            con.execute(f"PRAGMA busy_timeout = {int(busy_ms)}")
            row = con.execute("SELECT value FROM app_settings WHERE key=?", (HASH_KEY,)).fetchone()
            if row and row[0] == new_hash:
                return {"status": "актуально", "hash": new_hash}
            try:
                con.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as e:
                return {"status": "занято", "error": str(e)}
            try:
                before = _fk_violations(con)
                try:
                    res = _apply(con, img, _Manual(con))
                except sqlite3.IntegrityError as e:
                    # строка образа ссылается на то, чего нет (проверка ссылок включена) — как нарушение ниже
                    con.execute("ROLLBACK")
                    _audit(con, "справочники не обновлены: нарушены ссылки",
                           {"ошибка": str(e), "отпечаток": new_hash})
                    return {"status": "откат", "fk": 1, "sample": [str(e)]}
                new_fk = _fk_violations(con) - before
                if new_fk:
                    con.execute("ROLLBACK")
                    sample = sorted(new_fk, key=str)[:10]
                    _audit(con, "справочники не обновлены: нарушены ссылки",
                           {"нарушений": len(new_fk), "примеры": [list(x) for x in sample], "отпечаток": new_hash})
                    return {"status": "откат", "fk": len(new_fk), "sample": sample}
                con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?)"
                            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                            (HASH_KEY, new_hash, db.now()))
                ms = int((time.monotonic() - t0) * 1000)
                detail = {"было": row[0] if row else None, "стало": new_hash, "мс": ms, **res,
                          "версии": {str(k): v for k, v in res["версии"].items()}}
                con.execute("INSERT INTO audit (ts, who, action, entity, detail) VALUES (?,?,?,?,?)",
                            (db.now(), WHO, "справочники обновлены из сборки", "справочники",
                             json.dumps(detail, ensure_ascii=False)))
                con.execute("COMMIT")
            except Exception as e:
                if con.in_transaction:
                    con.execute("ROLLBACK")
                _audit(con, "справочники не обновлены: ошибка", {"ошибка": f"{type(e).__name__}: {e}"})
                raise
        finally:
            con.close()
    finally:
        img.close()
    db.invalidate_reference()
    return {"status": "обновлено", "hash": new_hash, "ms": ms, **res}


def _audit(con, action, detail):
    # отдельная короткая запись после отката: причина должна остаться в журнале
    try:
        con.execute("INSERT INTO audit (ts, who, action, entity, detail) VALUES (?,?,?,?,?)",
                    (db.now(), WHO, action, "справочники", json.dumps(detail, ensure_ascii=False, default=str)))
    except sqlite3.Error:
        log.exception("запись в журнал не удалась")


def image_path() -> Path:
    return db.ROOT / "data" / "surveyor.db"


def sync_templates(target_path=None, busy_ms: int = 2000) -> dict:
    """
    Шаблоны анализа по классам (app/class_templates.py): у них свой источник — docs/act_class_templates.json в
    образе, а не база образа. Таблицы на диске нет — создаётся; класса нет или версия файла новее последней версии
    в базе — добавляется строка; правки администратора (новые версии) и история не трогаются.
    Здесь же доводится справочник classes: колонка учётной группы допускает NULL и добавляется класс 18 общего
    страхования (ct.ensure_classes, учётная группа NULL — в Положении 1882, п. 10 его нет); имеющиеся строки не
    меняются. Новая версия файла при действующей правке администратора её не заменяет (строка file_pending).
    """
    from . import class_templates as ct
    target_path = Path(target_path or db.DB_PATH)
    if not target_path.exists():
        return {"status": "нет базы"}
    con = sqlite3.connect(str(target_path), isolation_level=None)
    try:
        con.execute(f"PRAGMA busy_timeout = {int(busy_ms)}")
        try:
            # учётная группа класса допускает NULL (класс 18) — старая база доводится здесь же
            if db._classes_group_nullable(con):
                print("справочник классов: учётная группа допускает NULL (класс 18)")
        except sqlite3.OperationalError as e:
            return {"status": "занято", "error": str(e)}
        try:
            con.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            return {"status": "занято", "error": str(e)}
        try:
            res = ct.ensure(con, force=True)
            con.execute("COMMIT")
        except Exception:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    added = res.get("added") or []
    if added:
        print(f"шаблоны классов: доведены из файла {res.get('version')}: {', '.join(added)}")
    if res.get("pending"):
        print(f"шаблоны классов: доступна новая версия шаблона из поставки {res.get('version')} — действует правка "
              f"администратора: {', '.join(res['pending'])}")
    if res.get("classes_added"):
        print(f"справочник классов: добавлены классы из файла шаблонов {', '.join(res['classes_added'])}")
    return {"status": "обновлено" if added or res.get("classes_added") else "актуально", **res}


def sync_on_start(image=None) -> dict:
    """
    Вызывается при старте после ensure_schema(). Без постоянного диска (база и есть образ) — справочники не
    переносятся, но шаблоны классов доводятся из файла всегда (sync_templates).
    База занята — повтор в фоне, старт не ждёт. Ошибка обновления старт не ломает.
    """
    try:
        sync_templates()
    except Exception:
        log.exception("шаблоны классов не доведены")
    image = Path(image) if image else image_path()
    if not image.exists() or image.resolve() == Path(db.DB_PATH).resolve():
        return {"status": "не требуется"}
    try:
        res = sync(image)
    except Exception as e:
        log.exception("справочники не обновлены")
        return {"status": "ошибка", "error": str(e)}
    if res["status"] == "занято":
        threading.Thread(target=_retry, args=(image,), name="refsync-retry", daemon=True).start()
    _after_sync(res)
    _print(res)
    return res


def _after_sync(res) -> None:
    """Образ собран до шаблонов 1.2.0 (в его classes нет класса 18) — upsert мог убрать его с диска: после
    обновления справочников класс 18 доводится из файла шаблонов ещё раз (ничего не исключаем)."""
    if (res or {}).get("status") != "обновлено":
        return
    from . import class_templates as ct
    ct.reset_cache()
    try:
        sync_templates()
    except Exception:
        log.exception("класс 18 после обновления справочников не доведён")


def _retry(image):
    for _ in range(RETRY_TIMES):
        time.sleep(RETRY_SEC)
        try:
            res = sync(image, busy_ms=5000)
        except Exception:
            log.exception("справочники не обновлены (повтор)")
            return
        if res["status"] != "занято":
            _after_sync(res)
            _print(res)
            return
    log.error("справочники не обновлены: база занята %d попыток", RETRY_TIMES)


def _print(res):
    st = res["status"]
    if st == "обновлено":
        changed = {t: s for t, s in res["таблицы"].items()
                   if any(s.get(k) for k in ("добавлено", "обновлено", "удалено"))}
        print(f"справочники: обновлены из сборки за {res['ms']} мс: {json.dumps(changed, ensure_ascii=False)}")
        if res["оставлены"]:
            print("справочники: оставлены строки не из сборки:",
                  {t: len(v) for t, v in res["оставлены"].items()})
    elif st == "откат":
        print(f"справочники: обновление отменено — новых нарушений ссылок {res['fk']}, работаем на старых")
    elif st == "занято":
        print("справочники: база занята, обновление повторится в фоне")
