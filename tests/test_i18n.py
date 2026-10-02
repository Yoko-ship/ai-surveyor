"""
Словарь интерфейса (app/i18n.py и app/i18n/*.json).

Запуск из корня проекта:
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_i18n.py

Без pytest — обычные assert, как в остальных тестах проекта.
База не открывается и не пишется: модуль словаря её не знает, маршруты проверяем
на отдельном FastAPI-приложении своим ASGI-клиентом, живой сервер не трогаем.

Что проверяется:
  1. все три словаря имеют одинаковый набор ключей и ни одного пустого значения;
  2. t() переводит, падает на русский при пропуске и считает пропуски;
  3. подстановка параметров работает, лишняя фигурная скобка не роняет вызов;
  4. pick_lang отрабатывает все источники и их приоритет;
  5. /i18n/uz.json отдаёт валидный JSON с заголовком кэша, /i18n/langs — список языков,
     неизвестный язык → 404 с понятным текстом;
  6. каждый rule-код из app/engine.py либо переведён, либо назван в списке непереведённых.
"""
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI                      # noqa: E402

from app import i18n                             # noqa: E402

passed = failed = 0


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ок  ", name)
    else:
        failed += 1
        print("  ПЛОХО", name, extra)


# ---------- минимальный ASGI-клиент поверх отдельного приложения ----------
app = FastAPI()
app.include_router(i18n.router)


def call(path: str):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": b"", "headers": [(b"host", b"test")],
             "client": ("127.0.0.1", 0), "server": ("test", 80)}
    out = {"status": None, "chunks": [], "headers": {}}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = {k.decode().lower(): v.decode() for k, v in msg.get("headers", [])}
        elif msg["type"] == "http.response.body":
            out["chunks"].append(msg.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    raw = b"".join(out["chunks"]).decode("utf-8", "replace")
    try:
        return out["status"], json.loads(raw), out["headers"]
    except ValueError:
        return out["status"], raw, out["headers"]


# ---------- 1. одинаковый набор ключей ----------

def test_same_keys():
    sets = {lang: set(i18n.keys(lang)) for lang in i18n.LANGS}
    base = sets["ru"]
    for lang in ("uz", "en"):
        only_here = sorted(sets[lang] - base)
        only_ru = sorted(base - sets[lang])
        ok(f"{lang}: набор ключей совпадает с ru", not only_here and not only_ru,
           f"лишние: {only_here[:5]} нет: {only_ru[:5]}")
    ok("ключей больше 400", len(base) > 400, len(base))

    for lang in i18n.LANGS:
        d = i18n.load(lang)
        empty = [k for k in sets[lang] if not str(d[k]).strip()]
        ok(f"{lang}: пустых значений нет", not empty, empty[:5])

    # разделы на месте
    for prefix in ("calc.", "tg.", "status.", "doc.", "check.", "rec.", "term.", "common."):
        ok(f"есть раздел {prefix}*", any(k.startswith(prefix) for k in base))

    # у каждой проверки есть и заголовок, и пояснение
    titles = {k[:-6] for k in base if k.startswith("check.") and k.endswith(".title")}
    details = {k[:-7] for k in base if k.startswith("check.") and k.endswith(".detail")}
    ok("у каждой проверки есть title и detail", titles == details,
       sorted(titles ^ details)[:5])

    # параметры в фигурных скобках одинаковые во всех трёх языках
    braces = re.compile(r"\{(\w+)\}")
    bad = []
    for k in base:
        want = set(braces.findall(i18n.load("ru")[k]))
        for lang in ("uz", "en"):
            if set(braces.findall(i18n.load(lang)[k])) != want:
                bad.append(f"{lang}:{k}")
    ok("подстановки совпадают во всех языках", not bad, bad[:5])


# ---------- 2-3. перевод, падение на русский, подстановка ----------

def test_translate():
    i18n.reset_misses()
    ok("uz переводит", i18n.t("common.save", "uz") == "Saqlash", i18n.t("common.save", "uz"))
    ok("en переводит", i18n.t("common.save", "en") == "Save", i18n.t("common.save", "en"))
    ok("без языка — русский", i18n.t("common.save") == "Сохранить")
    ok("uz-Latn понимается", i18n.t("common.save", "uz-Latn") == "Saqlash")

    # ключа нет ни в одном словаре — отдаём сам ключ и не падаем
    ok("нет ключа — возвращаем ключ", i18n.t("нет.такого.ключа", "uz") == "нет.такого.ключа")
    ok("пропуск посчитан", i18n.misses().get("uz:нет.такого.ключа") == 1, i18n.misses())

    # ключ есть только в русском — проверяем падение на русский на живом словаре
    i18n.load("uz").pop("_проба", None)
    i18n.load("ru")["_проба"] = "только по-русски"
    ok("падение на русский", i18n.t("_проба", "uz") == "только по-русски")
    ok("падение посчитано", i18n.misses().get("uz:_проба") == 1)
    i18n.load("ru").pop("_проба", None)

    # неизвестный язык не ломает перевод
    ok("неизвестный язык — русский", i18n.t("common.save", "de") == "Сохранить")

    # подстановка
    got = i18n.t("calc.premium_note", "ru", days=90)
    ok("подстановка параметра", "90" in got and "{" not in got, got)
    got_uz = i18n.t("calc.premium_note", "uz", days=90)
    ok("подстановка в узбекском", "90" in got_uz, got_uz)
    ok("нет параметра — текст, а не ошибка",
       "{" in i18n.t("calc.premium_note", "ru"), i18n.t("calc.premium_note", "ru"))

    i18n.reset_misses()
    ok("счётчик сбрасывается", i18n.misses() == {})


# ---------- 4. выбор языка ----------

def test_pick_lang():
    ok("по умолчанию ru", i18n.pick_lang() == "ru")
    ok("параметр важнее всего", i18n.pick_lang("en", "uz", "ru") == "en")
    ok("cookie важнее Telegram", i18n.pick_lang(None, "uz", "en") == "uz")
    ok("Telegram, если больше ничего нет", i18n.pick_lang(None, None, "en") == "en")
    ok("uz-Latn из Telegram", i18n.pick_lang(None, None, "uz-Latn") == "uz")
    ok("uz-Cyrl тоже uz", i18n.pick_lang(None, None, "uz-Cyrl") == "uz")
    ok("ru-RU → ru", i18n.pick_lang(None, None, "ru-RU") == "ru")
    ok("чужой язык → ru", i18n.pick_lang(None, None, "de") == "ru")
    ok("пустые значения пропускаются", i18n.pick_lang("", "", "uz") == "uz")
    ok("мусор в параметре не мешает cookie", i18n.pick_lang("fr", "en", None) == "en")


# ---------- 5. маршруты ----------

def test_routes():
    st, body, hdrs = call("/i18n/uz.json")
    ok("/i18n/uz.json — 200", st == 200, st)
    ok("/i18n/uz.json — это JSON со словарём", isinstance(body, dict) and body.get("common.save") == "Saqlash")
    ok("заголовок кэша стоит", "max-age" in (hdrs.get("cache-control") or ""), hdrs.get("cache-control"))
    ok("в uz.json есть список рабочих переводов",
       isinstance(body.get("_meta", {}).get("draft"), list))

    for lang in ("ru", "en"):
        st, body, _ = call(f"/i18n/{lang}.json")
        ok(f"/i18n/{lang}.json — 200", st == 200 and isinstance(body, dict), st)

    st, body, _ = call("/i18n/de.json")
    ok("неизвестный язык — 404", st == 404, st)
    detail = (body or {}).get("detail", "") if isinstance(body, dict) else str(body)
    ok("404 объясняет, какие языки есть", "ru" in detail and "uz" in detail, detail)

    st, body, _ = call("/i18n/langs")
    ok("/i18n/langs — 200", st == 200, st)
    codes = [x["code"] for x in (body or {}).get("langs", [])] if isinstance(body, dict) else []
    ok("/i18n/langs перечисляет три языка", codes == ["ru", "uz", "en"], codes)
    ok("/i18n/langs называет cookie", (body or {}).get("cookie") == "lang")


# ---------- 6. коды правил движка ----------

def test_rules():
    src = (ROOT / "app" / "engine.py").read_text(encoding="utf-8")
    # add("код", "статус", "заголовок", …) в checks_for
    codes = sorted(set(re.findall(r'add\(\s*"([a-z_0-9]+)"', src)))
    ok("коды правил из движка нашлись", len(codes) >= 15, codes)

    known = set(i18n.rule_codes())
    missing = [c for c in codes if c not in known]
    ok("перевод есть у каждого кода правила", not missing, missing)

    ru = i18n.load("ru")
    broken = [k for c in known for k in (f"check.{c}", )
              if not any(x.startswith(k + ".") for x in ru)]
    ok("у каждого кода в словаре есть строки", not broken, broken)

    # текст проверки по коду и статусу
    got = i18n.rule_text("min_rate", "uz", status="ok", product="0807", floor="0,050%", source="регулятор")
    ok("rule_text отдаёт заголовок и текст", got["title"] and "0807" in got["detail"], got)
    ru_got = i18n.rule_text("min_rate", "ru", status="stop", floor="0,050%", source="регулятор")
    ok("rule_text различает статусы", ru_got["title"] == "Ставка ниже минимальной", ru_got)

    # вариант выбирается по заголовку движка, когда статусы одинаковые
    v = i18n._rule_key("sum_vs_valuation", "warn", None, "Страховая сумма ниже оценки стоимости")
    ok("вариант по заголовку движка", v == "sum_vs_valuation.under", v)

    # неизвестное правило не роняет вызов
    i18n.reset_misses()
    unknown = i18n.rule_text("совсем_новое_правило", "uz")
    ok("новое правило движка не ломает перевод", unknown["title"] == "совсем_новое_правило")
    ok("новое правило попало в диагностику", "rule:совсем_новое_правило" in i18n.misses())
    i18n.reset_misses()

    # рекомендации движка
    rec = i18n.rec_text({"kind": "франшиза", "text": "Франшиза 1% страховой суммы"}, "en")
    ok("rec_text переводит подсказку", rec["text"] == "Deductible of 1% of the sum insured", rec)
    ok("rec_text переводит вид", rec["kind"] == "deductible", rec)


def test_act_regions():
    """регионы акта: 14 областей и два особых — вся республика (uz_all) и «Другое» (other) на трёх языках"""
    want = {"uz_all": {"ru": "Республика Узбекистан", "uz": "Oʻzbekiston Respublikasi", "en": "Republic of Uzbekistan"}}
    for lang in i18n.LANGS:
        regs = [k for k in i18n.keys(lang) if k.startswith("tg.act.reg.")]
        ok(f"{lang}: регионов в словаре 16 (14 + uz_all + other)", len(regs) == 16, len(regs))
        ok(f"{lang}: «вся республика»", i18n.t("tg.act.reg.uz_all", lang) == want["uz_all"][lang])
        other = i18n.t("tg.act.reg.other", lang)
        ok(f"{lang}: «Другое» переведено и не пустое", other and not other.startswith("tg.act."), other)
    ok("uz/en: «Другое» без кириллицы", not re.search(r"[А-Яа-яЁё]", i18n.t("tg.act.reg.other", "uz")
                                                     + i18n.t("tg.act.reg.other", "en")))


def main():
    print("Словарь интерфейса:")
    for fn in (test_same_keys, test_translate, test_pick_lang, test_routes, test_rules, test_act_regions):
        print("-", fn.__doc__ or fn.__name__)
        fn()
    print(f"\nитог: ок {passed}, плохо {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
