"""
Мгновенные юридические ответы: app/legal.py (индекс FTS5, FAQ юриста, /legal/*).

Запуск из корня (свой ASGI-клиент, живой сервер не трогаем):
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_legal.py

Вся работа — во временной копии базы (tests/tmpdb.py): индекс пишется в неё, рабочая не открывается.

Что проверяем:
  1. индекс собирается, в нём есть акты на ru и uz, повторный запуск ничего не пересобирает;
  2. вопрос по-русски «страховая сумма больше стоимости» приводит к ГК ст. 938;
  3. тот же вопрос по-узбекски — к 938-modda; апострофы ʻ / ‘ / ' дают один и тот же ответ;
  4. вопрос по-английски отвечается и помечается как неофициальный перевод;
  5. ответ укладывается в 150 мс на копии базы;
  6. доступ: сотрудник — 200, без входа — 401, пересборка индекса сотруднику — 403, админу — 200;
  7. в legal_questions нет текста вопроса — только отпечаток;
  8. FAQ: совпадение даёт source="faq", язык вопроса, цитату и ссылку;
  9. в индексе только глава 52 ГК: полный текст второй части помечен «Индексировать: нет»,
     и ст. 540 (наём имущества) больше не выдаётся как норма о страховании;
 10. ссылки lex.uz на узбекскую латиницу отдаются с дефисом (/uz/docs/-180552 — латиница,
     /uz/docs/180552 — кириллица), у каждой цитаты FAQ есть и текст, и адрес.
"""
import asyncio
import json as _json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.pop("SURVEYOR_DEV", None)          # guard проверяем целиком
os.environ["SURVEYOR_NO_BACKGROUND"] = "1"    # фоновая пересборка индекса в тесте не нужна

from tmpdb import temp_db            # noqa: E402
from app import auth, db             # noqa: E402
from app import legal                # noqa: E402
from app.main import app             # noqa: E402

PREFIX = "тест-юрист-"
EMP, ADM = PREFIX + "сотрудник", PREFIX + "админ"
PEOPLE = {EMP: "сотрудник", ADM: "админ"}
TOKENS = {}
FAQ_SAMPLE = Path(__file__).resolve().parent / "data" / "legal_faq_sample.json"
LIMIT_MS = 150

passed, failed = 0, 0


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ок  ", name)
    else:
        failed += 1
        print("  ПЛОХО", name, extra)


def call(method, path, body=None, params=None, who=None):
    query = urlencode(params or {}, encoding="utf-8")
    payload = _json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
    hdrs = [(b"host", b"test"), (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode())]
    if who:
        hdrs.append((b"cookie", f"sid={TOKENS[who]}".encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(), "headers": hdrs,
             "client": ("203.0.113.9", 0), "server": ("test", 80)}
    out = {"status": None, "chunks": []}

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["chunks"].append(msg.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    raw = b"".join(out["chunks"]).decode("utf-8", "replace")
    try:
        return out["status"], _json.loads(raw)
    except ValueError:
        return out["status"], raw


def setup():
    db.ensure_schema()
    with db.tx() as con:
        ts = db.now()
        for login, role in PEOPLE.items():
            cur = con.execute("INSERT INTO users (login, full_name, role, branch, password_hash, salt,"
                              " status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                              (login, "Тест", role, "тест-юрист", "x", "y", "активен", ts))
            u = db.rows(con, "SELECT * FROM users WHERE id=?", cur.lastrowid)[0]
            TOKENS[login], _ = auth.create_session(con, u, "203.0.113.9", "test")


def units(answer: dict) -> str:
    return " | ".join(f"{c['act']} {c['unit']}" for c in answer.get("citations") or [])


# ---------- 1. индекс ----------

def check_index():
    print("1. Сборка индекса")
    t0 = time.time()
    out = legal.reindex(force=True)
    print(f"     собрано {out['chunks']} кусков из {out['files_total']} файлов за {out['took_ms']} мс")
    ok("куски в индексе есть", out["chunks"] > 1000, out)
    ok("файлы разобраны", out["files_total"] >= 10, out)
    with db.tx() as con:
        langs = {r["language"] for r in db.rows(con, "SELECT DISTINCT language FROM legal_files")}
        gk = db.rows(con, "SELECT COUNT(*) AS n FROM legal_chunks WHERE unit='ст. 938'")[0]["n"]
    ok("в индексе есть русские тексты", "ru" in langs, langs)
    ok("в индексе есть узбекские тексты", "uz" in langs, langs)
    ok("ГК ст. 938 разобрана в отдельный юнит", gk >= 1, gk)

    again = legal.reindex()          # идемпотентность: mtime не менялся — ничего не пересобираем
    ok("повторная сборка ничего не трогает", again["files_changed"] == 0, again)
    ok("оглавление отброшено (кусков меньше, чем строк оглавления)",
       out["chunks"] < 20000, out["chunks"])


# ---------- 2. ответы на трёх языках ----------

def check_ru():
    print("2. Вопрос по-русски")
    r = legal.ask("что если страховая сумма больше стоимости?")
    ok("язык определён как ru", r["lang"] == "ru", r["lang"])
    ok("источник — пассажи из актов", r["answer"]["source"] in ("passages", "faq"), r["answer"])
    ok("в цитатах есть ГК ст. 938", any("938" in c["unit"] for c in r["citations"]), units(r))
    ok("у цитаты есть ссылка на lex.uz",
       any((c["url"] or "").startswith("https://lex.uz/") for c in r["citations"]), units(r))
    ok("ответ не пустой и не выдуман (цитата из акта в тексте)",
       len(r["answer"]["text"]) > 40 and "938" in r["answer"]["text"] + units(r), r["answer"]["text"][:120])


def check_uz():
    print("3. Вопрос по-узбекски и апострофы")
    variants = ["sugʻurta summasi qiymatdan oshsa?", "sug'urta summasi qiymatdan oshsa?",
                "sug‘urta summasi qiymatdan oshsa?", "sugurta summasi qiymatdan oshsa?"]
    results = []
    for q in variants:
        legal._cache.clear()                     # кэш не должен маскировать разницу написаний
        results.append(legal.ask(q))
    ok("язык определён как uz", all(r["lang"] == "uz" for r in results), [r["lang"] for r in results])
    ok("все написания апострофа дают один и тот же набор статей",
       len({units(r) for r in results}) == 1, [units(r)[:60] for r in results])
    r = results[0]
    if "uz" in {c["language"] for c in r["citations"]}:
        ok("в цитатах есть 938-modda", any("938" in c["unit"] for c in r["citations"]), units(r))
        ok("узбекский текст помечен официальным", any(c["official"] for c in r["citations"]), units(r))
        ok("апострофы в цитате сохранены (ʻ или ‘)",
           any("ʻ" in c["quote"] or "‘" in c["quote"] for c in r["citations"]), units(r))
    else:
        ok("узбекского текста нет — есть пометка об этом", bool(r["note"]), r["note"])


def check_en():
    print("4. Вопрос по-английски")
    r = legal.ask("what if the sum insured exceeds the insurable value?")
    ok("язык определён как en", r["lang"] == "en", r["lang"])
    ok("ответ есть", bool(r["citations"]), r["answer"])
    ok("английский помечен неофициальным",
       (not any(c["official"] for c in r["citations"])) and "unofficial" in (r["note"] or ""), r["note"])


# ---------- 3. скорость ----------

def check_speed():
    print("5. Скорость")
    qs = ["страховая сумма и страховая стоимость", "лимит на один риск страховщика",
          "срок действия договора страхования", "суброгация к виновному лицу",
          "обязанности страхового агента", "franchise and deductible in the policy",
          "sugʻurta shartnomasi muddati", "возврат премии при досрочном расторжении",
          "оценка объекта и износ", "страховой случай и отказ в выплате"]
    times = []
    for q in qs:
        legal._cache.clear()
        t0 = time.time()
        legal.ask(q)
        times.append((time.time() - t0) * 1000)
    avg, worst = sum(times) / len(times), max(times)
    print(f"     среднее {avg:.0f} мс, худшее {worst:.0f} мс на {len(qs)} разных вопросах")
    ok(f"среднее время < {LIMIT_MS} мс", avg < LIMIT_MS, round(avg))
    ok(f"худшее время < {LIMIT_MS * 2} мс", worst < LIMIT_MS * 2, round(worst))
    legal.ask(qs[0])                       # прогреваем кэш: выше он чистился перед каждым замером
    t0 = time.time()
    r = legal.ask(qs[0])
    ok("повтор берётся из кэша", r.get("cached") and (time.time() - t0) * 1000 < 20,
       round((time.time() - t0) * 1000))


# ---------- 4. доступ ----------

def check_access():
    print("6. Доступ")
    st, b = call("POST", "/legal/ask", {"q": "страховая сумма больше стоимости"}, who=EMP)
    ok("сотрудник спрашивает → 200", st == 200, (st, b))
    ok("в ответе есть lang, answer, citations, ai",
       st == 200 and {"lang", "answer", "citations", "ai", "took_ms"} <= set(b), list(b) if st == 200 else b)
    ok("ИИ выключен, но ответ выдан", st == 200 and b["ai"]["status"] in ("off", "ok", "error"), b.get("ai"))

    st, b = call("POST", "/legal/ask", {"q": "страховая сумма больше стоимости"})
    # 22.09.2026: юридические ответы открыты гостю (лимит вопросов — app/guest.py)
    ok("без входа → 200 (гость)", st == 200, (st, str(b)[:80]))

    st, _ = call("GET", "/legal/acts", who=EMP)
    ok("GET /legal/acts сотруднику → 200", st == 200, st)
    st, _ = call("GET", "/legal/faq", params={"lang": "uz"}, who=EMP)
    ok("GET /legal/faq сотруднику → 200", st == 200, st)

    st, b = call("POST", "/legal/reindex", {}, who=EMP)
    ok("пересборка сотруднику → 403", st == 403, (st, b))
    st, b = call("POST", "/legal/reindex", {}, who=ADM)
    ok("пересборка админу → 200", st == 200, (st, b))
    with db.tx() as con:
        a = db.rows(con, "SELECT who FROM audit WHERE action LIKE '%индекс законодательства%'"
                         " ORDER BY id DESC LIMIT 1")
    ok("пересборка записана в журнал с логином админа", a and a[0]["who"] == ADM, a)


# ---------- 5. журнал без персональных данных ----------

def check_log():
    print("7. Журнал вопросов без текста вопроса")
    secret = "может ли Иванов Иван Иванович ИНН 123456789 застраховать склад"
    legal._cache.clear()
    legal.ask(secret)
    with db.tx() as con:
        cols = {r[1] for r in con.execute("PRAGMA table_info(legal_questions)")}
        rowsx = db.rows(con, "SELECT * FROM legal_questions ORDER BY id DESC LIMIT 5")
    ok("колонки с текстом вопроса нет", not ({"q", "question", "text"} & cols), cols)
    dump = _json.dumps([dict(r) for r in rowsx], ensure_ascii=False)
    ok("ни ФИО, ни ИНН в журнале нет", "Иванов" not in dump and "123456789" not in dump, dump[:200])
    ok("отпечаток вопроса записан", rowsx and len(rowsx[0]["q_hash"]) == 32, rowsx[:1])
    ok("язык и время записаны", rowsx and rowsx[0]["lang"] == "ru" and rowsx[0]["took_ms"] is not None,
       rowsx[:1])


# ---------- 6. FAQ ----------

def check_faq():
    print("8. FAQ юриста")
    real = legal.FAQ_FILE
    legal.FAQ_FILE = FAQ_SAMPLE
    legal._faq_cache.update({"mtime": None, "items": [], "version": None})
    try:
        legal._cache.clear()
        r = legal.ask("что будет если страховая сумма меньше страховой стоимости?")
        ok("ответ взят из FAQ", r["answer"]["source"] == "faq", r["answer"])
        ok("уверенность высокая", r["answer"]["confidence"] >= 0.6, r["answer"])
        ok("цитата со ссылкой на lex.uz",
           r["citations"] and (r["citations"][0]["url"] or "").startswith("https://lex.uz/"), r["citations"])
        legal._cache.clear()
        ru = legal.ask("sugʻurta summasi sugʻurta qiymatidan kam boʻlsa nima boʻladi?")
        ok("узбекский вопрос — узбекский ответ FAQ",
           ru["answer"]["source"] == "faq" and ru["lang"] == "uz", ru["answer"])
        legal._cache.clear()
        en = legal.ask("what happens if the sum insured is below the insurable value?")
        ok("английского ответа в FAQ нет — откат на русский с пометкой",
           en["answer"]["source"] == "faq" and bool(en["note"]), (en["answer"], en["note"]))
        st, b = call("GET", "/legal/faq", params={"lang": "uz"}, who=EMP)
        ok("GET /legal/faq отдаёт чипы", st == 200 and b["count"] == 1, (st, b))
        st, b = call("GET", "/legal/acts", who=EMP)
        ok("GET /legal/acts показывает акты и языки",
           st == 200 and b["count"] > 5 and "uz" in b["languages"], (st, list(b) if st == 200 else b))
    finally:
        legal.FAQ_FILE = real
        legal._faq_cache.update({"mtime": None, "items": [], "version": None})


# ---------- 9. дефекты контролёра 22.09.2026 ----------

def check_silence():
    print("9. Когда закон молчит")
    for q in ("можно ли застраховать домашнего попугая от гриппа?",
              "нужно ли красить крышу склада в белый цвет?"):
        legal._cache.clear()
        r = legal.ask(q)
        ok("ответ «нормы нет», а не три случайных пассажа",
           r["answer"]["source"] == "none" and not r["citations"],
           (r["answer"]["source"], r["answer"]["confidence"], units(r)))
        ok("уверенность ниже порога", r["answer"]["confidence"] < legal.MIN_CONFIDENCE,
           r["answer"]["confidence"])
        ok("сказано смотреть правила страхования",
           "правил" in r["answer"]["text"].lower(), r["answer"]["text"][:80])


def check_rubricator():
    print("10. Рубрикаторы ОКОЗ/СПиТ не попадают в индекс")
    with db.tx() as con:
        n = db.rows(con, "SELECT COUNT(*) AS n FROM legal_chunks WHERE raw LIKE '%03.11.22.02%'"
                         " OR raw LIKE '%06.02.00.00%' OR raw LIKE '%OKOZ:%' OR raw LIKE '%[ ОКОЗ%'"
                         " OR raw LIKE '%TSZ:%' OR raw LIKE '%[ СПиТ%'")[0]["n"]
        starts = db.rows(con, "SELECT COUNT(*) AS n FROM legal_chunks WHERE raw LIKE '0_.__.__.__%'")[0]["n"]
    ok("служебной разметки lex.uz в индексе нет", n == 0, n)
    ok("ни один кусок не начинается с кода рубрикатора", starts == 0, starts)


def check_url_lang():
    print("11. Ссылка ведёт на язык процитированного текста")
    with db.tx() as con:
        rs = db.rows(con, "SELECT DISTINCT language, url FROM legal_chunks WHERE url <> ''")
    bad = [r["url"] for r in rs if r["language"] == "en" and "/en/docs/" not in r["url"]]
    ok("английские цитаты ссылаются на /en/docs/", not bad, bad)
    ok("английские тексты в индексе есть", any(r["language"] == "en" for r in rs), len(rs))


def check_faq_phrasing():
    print("12. Естественные формулировки попадают в FAQ")
    for q in ("Какой максимальный размер комиссии агента?",
              "Сколько можно платить агенту комиссии?",
              "комиссия агента максимум"):
        legal._cache.clear()
        r = legal.ask(q)
        ok(f"«{q}» → FAQ", r["answer"]["source"] == "faq",
           (r["answer"]["source"], r["answer"]["confidence"], units(r)))
        ok("ответ про предел 25% из Положения 3845",
           "25" in r["answer"]["text"] and "3845" in (r["answer"]["text"] + units(r)),
           r["answer"]["text"][:90])


def check_gk_chapter():
    """13. Полный текст ГК в индекс не идёт, и нестраховые статьи не выдаются как норма о страховании."""
    print("13. В индексе — глава 52, а не вся вторая часть ГК")
    with db.tx() as con:
        paths = [r["path"] for r in db.rows(con, "SELECT DISTINCT path FROM legal_files")]
    full = [p for p in paths if "часть вторая" in p]
    ok("полный текст второй части ГК не индексируется", not full, full)
    ok("глава 52 в индексе есть (ru и uz)",
       sum(1 for p in paths if "глава 52 Страхование" in p) >= 2,
       [p for p in paths if "глава 52" in p])
    legal._cache.clear()
    r = legal.ask("максимальный срок рассмотрения претензии по добровольному имущественному страхованию", "ru")
    bad = [c for c in r["citations"] if c["unit"] in ("ст. 540", "ст. 539", "ст. 541")]
    ok("ст. 540 (наём имущества) больше не выдаётся как норма о страховании", not bad, bad)


def check_uz_links():
    """14. Узбекские ссылки lex.uz отдаются с дефисом — это латиница, проверено 22.09.2026."""
    print("14. Ссылки на узбекскую латиницу не портятся")
    ok("дефис в адресе сохраняется",
       legal._clean_url("https://lex.uz/uz/docs/-180552") == "https://lex.uz/uz/docs/-180552")
    ok("хвостовая точка убирается",
       legal._clean_url("https://lex.uz/ru/docs/180550.") == "https://lex.uz/ru/docs/180550")
    legal._cache.clear()
    r = legal.ask("sugʻurta summasi qiymatdan katta", "uz")
    urls = [c["url"] for c in r["citations"] if c["url"]]
    ok("у узбекского ответа есть ссылка", bool(urls), urls)
    ok("ссылка уз-версии — с дефисом", all("/docs/-" in u for u in urls if "/uz/docs/" in u), urls)
    # каждая цитата FAQ проверяема: есть текст и адрес
    empty = [i["id"] for i in legal.faq_items()
             for c in i.get("citations") or []
             if not any((c.get("quote") or {}).values()) or not any((c.get("url") or {}).values())]
    ok("в FAQ нет цитат без текста или без ссылки", not empty, empty[:5])
    r = legal.ask("комиссия страхового агента максимальная", "ru")
    c0 = r["citations"][0] if r["citations"] else {}
    ok("комиссия агента: цитата и ссылка есть даже без русского текста нормы",
       bool(c0.get("quote")) and bool(c0.get("url")), c0)
    ok("и сказано, на каком языке норма", bool(r.get("note")), r.get("note"))


def main():
    with temp_db():
        setup()
        check_index()
        check_ru()
        check_uz()
        check_en()
        check_speed()
        check_access()
        check_log()
        check_faq()
        check_silence()
        check_rubricator()
        check_url_lang()
        check_faq_phrasing()
        check_gk_chapter()
        check_uz_links()
    print(f"\nИтого: {passed} ок, {failed} плохо")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
