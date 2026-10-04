"""
Тексты акта после разбиения app/act_texts.py на пакет app/act_texts/ по темам (04.10.2026).

Проверяем:
  1. набор имён, ключей и значений идентичен прежнему единому файлу — по отпечаткам
     tests/data/act_texts_ref.json (снят с файла ДО разбиения; порядок ключей тоже входит в отпечаток);
  2. у каждого ключа TX есть непустые ru, uz и en;
  3. дублей нет: каждый ключ TX задан в файлах тем ровно один раз, и ни в одном словаре нет повторов
     (тексты сверки с договором ct_* выводятся из rq_* в docs.derive_ct и затем уточняются — это не дубль);
  4. t(), money(), pct() отвечают как раньше.

Намеренно поменяли тексты — обновить эталон:
    sandbox\.venv\Scripts\python.exe tests\test_act_texts.py --update
"""
import ast
import hashlib
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import act_texts as tx  # noqa: E402

REF = Path(__file__).resolve().parent / "data" / "act_texts_ref.json"
PKG = ROOT / "app" / "act_texts"
passed, failed = 0, 0


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ок  ", name)
    else:
        failed += 1
        print("  ПЛОХО", name, str(extra)[:400])


def data_names(mod) -> dict:
    """Данные модуля (без функций и вложенных модулей)."""
    return {k: v for k, v in vars(mod).items()
            if not k.startswith("__") and not isinstance(v, (types.FunctionType, types.ModuleType, type))}


def fingerprint(value) -> str:
    def enc(o):
        if isinstance(o, (set, frozenset)):
            return {"__set__": sorted(map(repr, o))}
        return repr(o)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, default=enc).encode("utf-8")).hexdigest()


def snapshot(mod) -> dict:
    names = data_names(mod)
    return {"names": sorted(names), "tx_keys": len(names["TX"]),
            "hash": {k: fingerprint(v) for k, v in sorted(names.items())}}


def literal_tx_keys() -> list:
    """(ключ, файл, строка) всех ключей блоков TX_* в файлах тем."""
    out = []
    for p in sorted(PKG.glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in tree.body:
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) \
                    and n.targets[0].id.startswith("TX_") and isinstance(n.value, ast.Dict):
                out += [(k.value, p.name, k.lineno) for k in n.value.keys if isinstance(k, ast.Constant)]
    return out


def dict_literal_dups() -> list:
    """Повтор ключа внутри одного словаря-литерала (последнее значение молча затирало первое)."""
    out = []
    for p in sorted(PKG.glob("*.py")):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Dict):
                seen = set()
                for k in n.keys:
                    if isinstance(k, ast.Constant):
                        if k.value in seen:
                            out.append((p.name, k.lineno, k.value))
                        seen.add(k.value)
    return out


def main():
    if "--update" in sys.argv:
        REF.write_text(json.dumps(snapshot(tx), ensure_ascii=False, indent=1), encoding="utf-8")
        print("эталон обновлён:", REF)
        return
    ref = json.loads(REF.read_text(encoding="utf-8"))
    cur = snapshot(tx)
    print("1. набор имён и значений — как в прежнем act_texts.py")
    ok("те же имена (%d)" % len(ref["names"]), cur["names"] == ref["names"],
       {"нет": sorted(set(ref["names"]) - set(cur["names"])), "лишние": sorted(set(cur["names"]) - set(ref["names"]))})
    ok("ключей TX столько же (%d)" % ref["tx_keys"], cur["tx_keys"] == ref["tx_keys"], cur["tx_keys"])
    diff = [k for k in ref["hash"] if cur["hash"].get(k) != ref["hash"][k]]
    ok("значения и порядок ключей совпадают у всех %d словарей" % len(ref["hash"]), not diff, diff)

    print("2. три языка у каждого ключа")
    bad = [k for k, v in tx.TX.items()
           if not (isinstance(v, dict) and all(isinstance(v.get(lg), str) and v.get(lg) for lg in ("ru", "uz", "en")))]
    ok("у всех %d ключей TX есть ru, uz и en" % len(tx.TX), not bad, bad[:10])

    print("3. дублей нет")
    keys = literal_tx_keys()
    seen, dups = {}, []
    for k, f, ln in keys:
        if k in seen:
            dups.append((k, seen[k], "%s:%d" % (f, ln)))
        seen[k] = "%s:%d" % (f, ln)
    ok("каждый ключ TX задан один раз (%d ключей в файлах тем)" % len(keys), not dups, dups[:10])
    ok("в словарях-литералах нет повторов ключей", not dict_literal_dups(), dict_literal_dups()[:10])
    derived = set(tx.TX) - set(seen)
    ok("остальные ключи выведены из rq_* (ct_*, c_ct_*): %d" % len(derived),
       all(k.startswith(("ct", "c_ct_")) for k in derived), sorted(derived)[:10])

    print("4. функции не менялись")
    ok("t: ключ, подстановка, язык по умолчанию", tx.t("нет-такого-ключа") == "нет-такого-ключа"
       and tx.t("ph_empty_file", "uz") == tx.TX["ph_empty_file"]["uz"]
       and tx.t("ph_empty_file", "xx") == tx.TX["ph_empty_file"]["ru"])
    ok("money", tx.money(1234567.5, "ru") == "1\u00a0234\u00a0568\u00a0сум"
       and tx.money(1234567.5, "en") == "1,234,568\u00a0UZS",
       (tx.money(1234567.5, "ru"), tx.money(1234567.5, "en")))
    ok("pct", tx.pct(0.0125, "ru") == "0,0125\u00a0%" and tx.pct(0.0125, "en") == "0.0125%"
       and tx.pct(12.5, "uz") == "12,50\u00a0%",
       (tx.pct(0.0125, "ru"), tx.pct(0.0125, "en")))
    print(f"\nИтого: {passed} ок, {failed} плохо")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
