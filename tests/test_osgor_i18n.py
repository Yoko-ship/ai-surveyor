"""
Шаги и примечания калькулятора ОСГОР на узбекском и английском (задача 173, дефект контролёра 22.09.2026).

Запуск из корня (свой ASGI-клиент, живой сервер не трогаем; работа — во временной копии базы):
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_osgor_i18n.py

Проверки:
  1. lang=uz и lang=en (в теле и в ?lang=): ни в одном текстовом поле ответа нет кириллицы
     (кроме activity.name — это данные справочника) и нет «ОСГОР/OSGOR»; у шагов есть code,
     у примечаний — note_codes; en — пометка legal_note_en.
  2. lang=ru (и без lang): ответ побайтно как до перевода, если убрать новые поля
     (lines[].code, note_codes, note_code, lang). Эталон снят до правки: tests/data/osgor_quick_ru_golden.json,
     среди примеров — ФОТ 4,8 млрд, КСТ 4,0 → 19 200 000.
  3. GET /osgor/brv?lang=uz|en — note и legal_ref без кириллицы.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_risk_api as api          # noqa: E402  (ASGI-клиент, пользователи, сессии)
from tmpdb import temp_db             # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "data" / "osgor_quick_ru_golden.json"
CASES = {
    "base": {"okved": "01000", "payroll": 4_800_000_000, "term_days": 365, "contract_date": "2026-09-22"},
    "min": {"okved": "41100", "payroll": 30_000_000, "brv": 412_000, "contract_date": "2026-09-22"},
    "above": {"okved": "01000", "payroll": 4_800_000_000, "brv": 412_000, "contract_date": "2026-09-22"},
    "short": {"okved": "01000", "payroll": 900_000_000, "term_days": 120, "brv": 412_000,
              "contract_date": "2026-09-22"},
    "short_nobrv": {"okved": "01000", "payroll": 900_000_000, "term_days": 120, "contract_date": "2026-09-22"},
    "notlisted": {"okved": "999999", "payroll": 100_000_000, "contract_date": "2026-09-22"},
}
STEP_CODES = {"sum", "activity_kst", "kst", "rate", "year", "term", "min_year", "min_term", "final"}
NOTE_CODES = {"min_term", "brv_not_passed", "no_deviation"}
CYR = re.compile(r"[Ѐ-ӿ]")
BANNED = re.compile(r"ОСГОР|OSGOR", re.I)
passed, failed = 0, 0


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ок  ", name)
    else:
        failed += 1
        print("  ПЛОХО", name, extra)


def texts(obj, path=""):
    """Все строки ответа (и ключи словарей) с путём; activity.name — данные справочника, пропускаем."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if p == "activity.name":
                continue
            yield p + " (ключ)", k
            yield from texts(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from texts(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def strip_new(d: dict) -> dict:
    for f in ("note_codes", "note_code", "lang", "legal_note_en"):
        d.pop(f, None)
    for line in d.get("lines", []):
        line.pop("code", None)
    return d


def check_foreign():
    print("1. /osgor/quick на uz и en: без кириллицы и без «ОСГОР/OSGOR»")
    for lang in ("uz", "en"):
        for name, body in CASES.items():
            for how, b, params in (("тело", {**body, "lang": lang}, None),
                                   ("?lang=", body, {"lang": lang})):
                st, r = api.call("POST", "/osgor/quick", b, params=params, who=api.EMP)
                if st != 200:
                    ok(f"{lang} {name} ({how}) → 200", False, (st, r))
                    continue
                bad = [(p, t) for p, t in texts(r) if CYR.search(t) or BANNED.search(t)]
                ok(f"{lang} {name} ({how}): кириллицы и ОСГОР нет", not bad, bad[:3])
                ok(f"{lang} {name} ({how}): lang = {lang}", r.get("lang") == lang)
                codes = [line.get("code") for line in r["lines"]]
                ok(f"{lang} {name}: у каждого шага code", all(c in STEP_CODES for c in codes), codes)
                ok(f"{lang} {name}: note_codes по числу примечаний",
                   len(r["note_codes"]) == len(r["notes"]) and set(r["note_codes"]) <= NOTE_CODES,
                   (r["note_codes"], r["notes"]))
                ok(f"{lang} {name}: пометка о неофициальном переводе только в en",
                   (r.get("legal_note_en") == "English wording is an unofficial translation") == (lang == "en"))
    # числа и деньги по локали
    st, r = api.call("POST", "/osgor/quick", {**CASES["base"], "lang": "uz"}, who=api.EMP)
    ok("uz: деньги «19 200 000 soʻm», апостроф ʻ (U+02BB)",
       r["lines"][-1]["value"] == "19 200 000 soʻm" and r["lines"][-1]["code"] == "final",
       r["lines"][-1])
    ok("uz: норма — «OʻRQ-210-son Qonuni», «9-ilova», «-band»",
       "OʻRQ-210-son Qonuni" in r["lines"][0]["legal_ref"] and "9-ilova" in r["legal_ref"]["formula"]
       and "-band" in r["legal_ref"]["min"], r["legal_ref"])
    st, r = api.call("POST", "/osgor/quick", {**CASES["base"], "lang": "en"}, who=api.EMP)
    ok("en: деньги «UZS 19,200,000»", r["lines"][-1]["value"] == "UZS 19,200,000", r["lines"][-1])
    ok("en: норма — «Law LRU-210 of 16.04.2009», «CM Resolution No. 177, EL Rules, Annex 9»",
       "Law LRU-210 of 16.04.2009" in r["lines"][0]["legal_ref"]
       and "CM Resolution No. 177, EL Rules, Annex 9" in r["legal_ref"]["formula"], r["legal_ref"])
    st, r = api.call("POST", "/osgor/quick", {**CASES["base"], "lang": "de"}, who=api.EMP)
    ok("неизвестный язык → ru", st == 200 and r["lang"] == "ru" and r["lines"][0]["step"] == "страховая сумма")


def check_ru():
    print("2. /osgor/quick на ru: побайтно как до перевода (без новых полей)")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for name, body in CASES.items():
        for how, b, params in (("без lang", body, None), ("lang=ru", {**body, "lang": "ru"}, None),
                               ("?lang=ru", body, {"lang": "ru"})):
            st, r = api.call("POST", "/osgor/quick", b, params=params, who=api.EMP)
            raw = json.dumps(strip_new(r), ensure_ascii=False, separators=(",", ":"))
            ok(f"ru {name} ({how}) совпадает с эталоном", st == 200 and raw == golden[name],
               (st, raw[:200]))
    st, r = api.call("POST", "/osgor/quick", CASES["base"], who=api.EMP)
    ok("ru: ФОТ 4,8 млрд, КСТ 4,0 → 19 200 000", r["premium"] == 19_200_000 and r["kst"] == 4.0)
    ok("ru: у шагов тоже есть code", [x["code"] for x in r["lines"]]
       == ["sum", "activity_kst", "rate", "year", "final"], [x.get("code") for x in r["lines"]])


def check_brv():
    print("3. GET /osgor/brv?lang=")
    for lang in ("uz", "en"):
        st, r = api.call("GET", "/osgor/brv", params={"lang": lang}, who=api.EMP)
        bad = [(p, t) for p, t in texts({"note": r.get("note"), "legal_ref": r.get("legal_ref")})
               if CYR.search(t) or BANNED.search(t)]
        ok(f"{lang}: note и legal_ref без кириллицы и ОСГОР", st == 200 and r["note"] and not bad, (st, bad))
    st, r = api.call("GET", "/osgor/brv", who=api.EMP)
    ok("ru по умолчанию — как раньше", r["note"] == "Размер БРВ не введён — его вносит администратор"
       and "минимум" in r["legal_ref"])


def main():
    with temp_db():
        api.setup()                   # пользователи и пустой справочник БРВ — в копии базы
        check_foreign()
        check_ru()
        check_brv()
    print(f"\nИтого: {passed} ок, {failed} плохо")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
