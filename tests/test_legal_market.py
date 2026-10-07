"""
«ИИ специалист» — вопросы о рынке и память диалога: app/market_expert.py + ветка в app/legal.ask.

Запуск из корня (свой ASGI-клиент, живой сервер не трогаем, сеть не нужна):
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_legal_market.py

Вся работа — во временной копии базы (tests/tmpdb.py). Живой поиск lex.uz выключен (LEX_LIVE=0),
модель не вызывается (ai=false). Ожидаемые числа тест берёт из той же копии базы SQL-запросом,
а не из кода модуля — так проверяется именно формула.

Что проверяем:
  1. распознавание: вопросы о рынке на ru/uz/en — market; правовые и практические — нет;
  2. «кто лидер рынка по премиям» → страховщик с наибольшими премиями последнего среза (APEX) и его цифра;
  3. «убыточность по классу 14» → payouts / premiums × 100 из market_stats;
  4. та же сессия: «а по классу 8?» → класс 8 (пакет «8, 9» и одиночная строка), «а год назад?» → срез годом раньше;
  5. «доля INSON» → премии INSON / сумма премий страховщиков; без строки INSON — честное «нет данных»;
  6. uz/en-вопросы: язык ответа и те же числа;
  7. источник есть всегда: плашка НАПП (файл отчёта и дата), source_kind, source_label, пометка ytd;
  8. текст вопроса не попадает в базу (в legal_questions только отпечаток, source=market);
  9. API: session_id, проверка формата, /legal/suggest (8 подсказок, гость тоже видит);
 10. память: не больше 8 реплик, срок 2 часа, чужой пользователь с тем же session_id контекста не видит;
 11. индекс: тип источника law | company | market | note и подпись у цитаты;
 13. финансы страховщиков (рэнкинг snsratings.uz, company_rankings): активы, капитал, резервы, претензии и отказы,
     рентабельность, число договоров, карточка компании — с местом, долей, изменением и источником.
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

os.environ.pop("SURVEYOR_DEV", None)
os.environ["SURVEYOR_NO_BACKGROUND"] = "1"
os.environ["LEX_LIVE"] = "0"                  # lex.uz в этом тесте не нужен

from tmpdb import temp_db                     # noqa: E402
from app import auth, db                      # noqa: E402
from app import legal                         # noqa: E402
from app import market_expert as mx           # noqa: E402
from app.main import app                      # noqa: E402

PREFIX = "тест-рынок-"
EMP = PREFIX + "сотрудник"
TOKENS = {}
passed, failed = 0, 0


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ок  ", name)
    else:
        failed += 1
        print("  ПЛОХО", name, str(extra)[:600])


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
             "client": ("203.0.113.19", 0), "server": ("test", 80)}
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
        cur = con.execute("INSERT INTO users (login, full_name, role, branch, password_hash, salt,"
                          " status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (EMP, "Тест", "сотрудник", "тест-рынок", "x", "y", "активен", ts))
        u = db.rows(con, "SELECT * FROM users WHERE id=?", cur.lastrowid)[0]
        TOKENS[EMP], _ = auth.create_session(con, u, "203.0.113.19", "test")


def q1(sql, *args):
    with db.tx() as con:
        r = con.execute(sql, args).fetchone()
    return r


def latest():
    return q1("SELECT MAX(report_date) FROM market_stats")[0]


ASKED = []                     # все заданные вопросы — для проверки, что текста нет в базе


def ask(q, sid=None, who="u:test-1", lang=None):
    ASKED.append(q)
    return legal.ask(q, lang, who=who, session_id=sid)


def has_source(r) -> bool:
    srcs = r.get("sources") or []
    return bool(srcs) and all(s.get("label") and (s.get("file") or s.get("title")) for s in srcs) \
        and bool(r.get("source_kind")) and bool(r.get("source_label"))


# ---------------------------------------------------------------------------
def check_detect():
    print("1. распознавание вопросов о рынке")
    market = [("кто лидер рынка по премиям", "ru"), ("убыточность по классу 14", "ru"), ("доля INSON", "ru"),
              ("динамика рынка", "ru"), ("премии Узбекинвеста за 2025 год", "ru"), ("убыточность ОСАГО", "ru"),
              ("Рыночная ставка по классу 8 (огонь)", "ru"), ("регионы по премиям", "ru"),
              ("Mukofotlar boʻyicha bozor yetakchisi kim?", "uz"), ("14-klass boʻyicha zararlilik qancha?", "uz"),
              ("INSON bozor ulushi qancha?", "uz"), ("Who is the market leader by premiums?", "en"),
              ("What is the loss ratio for class 14?", "en"), ("What is INSON's market share?", "en")]
    for q, lang in market:
        ok("рынок: " + q, mx.detect(q, lang)["is_market"])
    legal_q = ["Что если страховая сумма больше стоимости имущества?", "Какая максимальная комиссия агента?",
               "How is the premium calculated?", "Что такое PML, EML и MFL простыми словами?",
               "в какой срок страховщик обязан произвести выплату", "что такое убыточность",
               "Можно ли вернуть премию при досрочном расторжении?", "какая доля кредита страхуется",
               "What if the sum insured exceeds market value?", "Franshiza qanday tanlanadi?",
               "Sugʻurta summasi qiymatdan oshsa nima boʻladi?", "Agent komissiyasi eng koʻp qancha?"]
    for q in legal_q:
        ok("не рынок: " + q, not mx.detect(q, legal.detect_lang(q))["is_market"])


def check_leader():
    print("2. лидер рынка по премиям")
    d = latest()
    top = q1("SELECT row_key, premiums_ytd FROM market_stats WHERE report_date=? AND row_key LIKE 'company:%' "
             "ORDER BY premiums_ytd DESC LIMIT 1", d)
    r = ask("кто лидер рынка по премиям", sid="leader-1")
    n = (r.get("market") or {}).get("numbers") or {}
    ok("intent=market, source=market", r.get("intent") == "market" and r["answer"]["source"] == "market",
       (r.get("intent"), r["answer"]))
    ok("лидер — APEX (как в базе)", n.get("leader") == top[0] and "APEX" in top[0] and "APEX" in r["answer"]["text"],
       (n.get("leader"), top))
    ok("цифра лидера из базы", abs((n.get("premiums") or 0) - top[1]) < 1e-6, (n.get("premiums"), top[1]))
    ok("цифра в тексте («2 072 259 млн сум» по формату)", mx._num(top[1], "ru") in r["answer"]["text"],
       r["answer"]["text"])
    ok("в тексте дата среза", mx._ru_date(d) in r["answer"]["text"])
    tbl = r["market"]["table"]
    ok("таблица до 10 строк, первая — лидер", tbl and 0 < len(tbl["rows"]) <= 10 and "APEX" in tbl["rows"][0][1],
       tbl and tbl["rows"][:1])
    ok("источник есть: НАПП, файл и дата", has_source(r) and r["sources"][0]["kind"] == "market"
       and r["sources"][0]["date"] == d and r["sources"][0]["file"], r.get("sources"))
    ok("пометка ytd", "нарастающим итогом" in (r["market"].get("ytd_note") or "") and "ytd" in r["note"], r["note"])
    ok("данные и вывод разделены", r["parts"]["data"]["text"] == r["answer"]["text"]
       and (r["parts"]["opinion"] is None or r["parts"]["opinion"]["label"].startswith("Вывод")), r.get("parts"))
    ok("роль специалиста", "рынку Узбекистана" in (r.get("assistant_role") or {}).get("ru", ""),
       r.get("assistant_role"))


def check_class_and_followups():
    print("3–4. убыточность класса 14, уточнения в той же сессии")
    d = latest()
    p, pay = q1("SELECT premiums_ytd, payouts_ytd FROM market_stats WHERE row_key='cls14' AND report_date=?", d)
    want = round(pay / p * 100, 2)
    r = ask("убыточность по классу 14", sid="sess-A")
    n = r["market"]["numbers"]
    ok("убыточность = выплаты / премии × 100 из базы", n.get("loss_ratio_pct") == want, (n.get("loss_ratio_pct"), want))
    ok("число в тексте", mx._pct(pay / p * 100, "ru") in r["answer"]["text"], r["answer"]["text"])
    ok("класс 14 назван", "14" in r["answer"]["text"])
    ok("источник есть", has_source(r), r.get("sources"))

    r2 = ask("а по классу 8?", sid="sess-A")
    n2 = (r2.get("market") or {}).get("numbers") or {}
    ok("«а по классу 8?» — рынок, по контексту", r2.get("intent") == "market" and r2["context"]["used"],
       (r2.get("intent"), r2.get("context")))
    ok("ответ про класс 8 (пакет «8, 9» + строка класса 8)",
       "8" in r2["answer"]["text"] and (n2.get("alt") or {}).get("row_key") == "cls8", (n2, r2["answer"]["text"]))
    p8, pay8 = q1("SELECT premiums_ytd, payouts_ytd FROM market_stats WHERE row_key=? AND report_date=?",
                  n2.get("row_key"), d)
    ok("показатель перенесён: убыточность класса 8 из базы", n2.get("loss_ratio_pct") == round(pay8 / p8 * 100, 2),
       (n2.get("loss_ratio_pct"), pay8 / p8 * 100))
    ok("источник есть", has_source(r2))

    r3 = ask("а год назад?", sid="sess-A")
    prev = "%04d%s" % (int(d[:4]) - 1, d[4:])
    ok("«а год назад?» — тот же класс, срез годом раньше",
       r3.get("intent") == "market" and r3["market"]["date"] == prev
       and r3["market"]["numbers"].get("row_key") == n2.get("row_key"), (r3["market"].get("date"), prev))

    r4 = ask("а у INSON?", sid="sess-A")
    ok("«а у INSON?» — компания INSON", r4.get("intent") == "market"
       and (r4["market"]["numbers"] or {}).get("row_key") == "company:INSON AJ", r4["answer"]["text"])
    ok("разреза «страховщик × класс» нет — сказано", "страховщик × класс" in r4["answer"]["text"],
       r4["answer"]["text"])

    # без сессии уточнение не разрешается: вопрос уходит в правовую ветку
    r5 = ask("а по классу 8?")
    ok("без session_id «а по классу 8?» не тянет чужой контекст", not (r5.get("context") or {}).get("used"),
       r5.get("context"))
    # чужой пользователь с тем же session_id контекста не видит
    r6 = ask("а год назад?", sid="sess-A", who="u:other-2")
    ok("чужой пользователь, тот же session_id — контекста нет",
       r6.get("intent") != "market" or not r6["context"]["used"], (r6.get("intent"), r6.get("context")))


def check_inson():
    print("5. доля INSON")
    d = latest()
    row = q1("SELECT premiums_ytd FROM market_stats WHERE row_key='company:INSON AJ' AND report_date=?", d)
    tot = q1("SELECT SUM(premiums_ytd) FROM market_stats WHERE row_key LIKE 'company:%' AND report_date=?", d)[0]
    r = ask("доля INSON", sid="inson-1")
    if row:
        want = round(row[0] / tot * 100, 2)
        ok("доля INSON = премии INSON / сумма страховщиков", r["market"]["numbers"].get("share_pct") == want,
           (r["market"]["numbers"].get("share_pct"), want))
        ok("доля в тексте", mx._pct(row[0] / tot * 100, "ru", 2) in r["answer"]["text"], r["answer"]["text"])
    else:
        ok("строки INSON нет — «нет данных»", "нет" in r["answer"]["text"], r["answer"]["text"])
    ok("источник есть", has_source(r))

    # без строки INSON и без сводки фактов — честное «в открытых данных НАПП нет»
    orig = mx.FACTS_FILE
    mx.FACTS_FILE = Path(__file__).resolve().parent / "data" / "нет_такого_файла.json"
    try:
        with db.tx() as con:
            con.execute("DELETE FROM market_stats WHERE row_key='company:INSON AJ'")
        r2 = ask("доля INSON", sid="inson-2")
        ok("нет строки — «В открытых данных НАПП нет …»", r2.get("intent") == "market"
           and r2["answer"]["text"].startswith("В открытых данных НАПП нет") and not r2["market"]["found"],
           r2["answer"]["text"])
        ok("нет данных — источник всё равно показан", has_source(r2), r2.get("sources"))
        r3 = ask("премии APEX за 2019 год", sid="inson-3")
        ok("года нет в данных — «нет данных», цифры другого года не подставлены",
           not r3["market"]["found"] and "НАПП нет" in r3["answer"]["text"], r3["answer"]["text"])
    finally:
        mx.FACTS_FILE = orig
        mx._facts_cache["mtime"] = None


def check_langs():
    print("6. узбекский и английский")
    d = latest()
    top = q1("SELECT row_key FROM market_stats WHERE report_date=? AND row_key LIKE 'company:%' "
             "ORDER BY premiums_ytd DESC LIMIT 1", d)[0]
    r = ask("Mukofotlar boʻyicha bozor yetakchisi kim?", sid="uz-1")
    ok("uz: язык uz, рынок, лидер из базы", r["lang"] == "uz" and r.get("intent") == "market"
       and r["market"]["numbers"].get("leader") == top, (r["lang"], r.get("intent"), r["answer"]["text"][:200]))
    ok("uz: подпись источника на узбекском", r["source_label"] == "NAPP maʼlumotlari", r["source_label"])
    p, pay = q1("SELECT premiums_ytd, payouts_ytd FROM market_stats WHERE row_key='cls14' AND report_date=?", d)
    r2 = ask("What is the loss ratio for class 14?", sid="en-1")
    ok("en: язык en, убыточность класса 14 из базы", r2["lang"] == "en" and r2.get("intent") == "market"
       and r2["market"]["numbers"].get("loss_ratio_pct") == round(pay / p * 100, 2), r2["answer"]["text"][:200])
    ok("en: пометка ytd по-английски", "year to date" in r2["market"]["ytd_note"])
    r3 = ask("14-klass boʻyicha zararlilik qancha?", sid="uz-2")
    ok("uz: класс 14", r3.get("intent") == "market" and r3["market"]["numbers"].get("row_key") == "cls14")
    for x in (r, r2, r3):
        ok("источник есть (%s)" % x["lang"], has_source(x))


def check_legal_kept():
    print("7. правовой вопрос — как раньше, с типом источника")
    r = ask("Что если страховая сумма больше стоимости имущества?", sid="mix-1")
    ok("правовой ответ, intent=legal", r.get("intent") == "legal" and r["answer"]["source"] in ("faq", "passages"),
       r.get("intent"))
    ok("source_kind=law, подпись «закон»", r["source_kind"] == "law" and r["source_label"] == "закон",
       (r["source_kind"], r["source_label"]))
    ok("у цитат есть source_kind и source_label",
       all(c.get("source_kind") and c.get("source_label") for c in r["citations"]), r["citations"][:1])
    ok("части ответа: данные с источником", r["parts"]["data"]["text"] == r["answer"]["text"])
    r2 = ask("Нужно ли страховать туристов при выезде за рубеж?", sid="mix-1")
    ok("перечень продуктов тарифной политики не выдаётся за правовую норму",
       all(c.get("source_kind") != "company" for c in r2["citations"]), [c["act"] for c in r2["citations"]])


def check_privacy():
    print("8. текст вопроса не попадает в базу")
    marker = "Иванов Пётр Сергеевич спрашивает: убыточность по классу 14"
    r = ask(marker, sid="pd-1")
    ok("вопрос с ФИО отвечен как рыночный", r.get("intent") == "market")
    hits = []
    with db.tx() as con:
        tabs = [t[0] for t in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                          "AND name NOT LIKE 'legal_chunks%' AND name NOT LIKE 'sqlite_%'")]
        for t in tabs:
            try:
                rows_ = con.execute('SELECT * FROM "%s"' % t).fetchall()
            except Exception:
                continue
            for row in rows_:
                for v in tuple(row):
                    if isinstance(v, str) and ("Иванов" in v or any(q in v for q in ASKED if len(q) > 12)):
                        hits.append(t)
                        break
    ok("ни ФИО, ни текстов вопросов нет ни в одной таблице", not hits, sorted(set(hits)))
    h = legal.q_hash(marker, "ru")
    row = q1("SELECT source, found FROM legal_questions WHERE q_hash=?", h)
    ok("в legal_questions — только отпечаток, source=market", row and row[0] == "market" and row[1] == 1, row)


def check_api():
    print("9. API: session_id, /legal/suggest")
    st, b = call("POST", "/legal/ask", {"q": "кто лидер рынка по премиям", "session_id": "web-123_ab"}, who=EMP)
    ok("POST /legal/ask с session_id → 200, intent=market", st == 200 and b.get("intent") == "market"
       and b.get("session_id") == "web-123_ab", (st, str(b)[:200]))
    ok("ответ несёт market.table, sources, parts, source_kind", st == 200 and b["market"]["table"]
       and b["sources"] and b["parts"]["data"] and b["source_kind"] == "market")
    st2, b2 = call("POST", "/legal/ask", {"q": "а у INSON?", "session_id": "web-123_ab"}, who=EMP)
    ok("уточнение через API в той же сессии", st2 == 200 and b2.get("intent") == "market"
       and b2["context"]["used"], (st2, str(b2)[:300]))
    st3, b3 = call("POST", "/legal/ask", {"q": "кто лидер рынка", "session_id": "плохой id"}, who=EMP)
    ok("session_id не того формата → 422", st3 == 422, (st3, b3))
    st4, _ = call("POST", "/legal/ask", {"q": "кто лидер рынка", "session_id": "x" * 65}, who=EMP)
    ok("session_id длиннее 64 → 422", st4 == 422, st4)
    for lang in ("ru", "uz", "en"):
        st5, b5 = call("GET", "/legal/suggest", params={"lang": lang}, who=EMP)
        kinds = {i["kind"] for i in b5.get("items") or []} if st5 == 200 else set()
        ok(f"/legal/suggest {lang}: 8 подсказок, есть рынок и право", st5 == 200 and b5["count"] == 8
           and {"market", "law"} <= kinds, (st5, b5))
    st6, b6 = call("GET", "/legal/suggest", params={"lang": "uz"})
    ok("/legal/suggest открыт гостю", st6 == 200 and b6["count"] == 8, st6)
    # подсказки по рынку распознаются как рынок
    for lang in ("ru", "uz", "en"):
        for i in mx.suggest(lang):
            if i["kind"] == "market":
                ok(f"подсказка «{i['q']}» → рынок", mx.detect(i["q"], lang)["is_market"])


def check_memory():
    print("10. память диалога")
    m = mx.DialogMemory()
    k = m.key("s", "u:1")
    for i in range(10):
        m.add(k, "вопрос %d" % i, "ответ %d" % i, {"kind": "market"})
    ok("хранится не больше 8 реплик", len(m.get(k)["turns"]) == 8, len(m.get(k)["turns"]))
    ok("ключ зависит от пользователя", m.key("s", "u:1") != m.key("s", "u:2"))
    m._data[k]["t"] = time.time() - mx.MEMORY_TTL_SEC - 1
    ok("через 2 часа сессия забыта", m.get(k)["turns"] == [] and m.size() == 0)
    ok("без session_id память не ведётся", m.key(None, "u:1") is None and m.get(None)["turns"] == [])


def check_index_kinds():
    print("11. индекс: типы источников")
    kinds = {s["kind"] for s in legal.source_files()}
    ok("в индексе законы и заметки", {"act", "note"} <= kinds, kinds)
    if legal.company_dir().exists():
        ok("тарифная политика INSON в индексе (company)", "company" in kinds, kinds)
    if legal.market_notes_dir().exists() and list(legal.market_notes_dir().glob("*.md")):
        ok("обзоры рынка docs/Знания/Рынок в индексе (market)", "market" in kinds, kinds)
    ok("подписи источников", legal.source_label("company", "ru") == "документ INSON"
       and legal.source_label("market", "ru") == "данные НАПП" and legal.source_label("law", "ru") == "закон")
    ok("тип по пути", legal.source_kind("library/02_Компания_INSON/x.txt") == "company"
       and legal.source_kind("/srv/storage/library_live/Компания/x.txt") == "company"
       and legal.source_kind("docs/Знания/Рынок/01.md") == "market"
       and legal.source_kind("docs/Знания/x.md") == "note"
       and legal.source_kind("library/01_Законодательство/x.txt") == "law")
    legal.ensure_index()
    with db.tx() as con:
        n = con.execute("SELECT COUNT(*) FROM legal_files WHERE path LIKE 'library/02_%'").fetchone()[0]
    if legal.company_dir().exists():
        ok("тарифная политика проиндексирована", n >= 1, n)
    r = ask("Минимальный тариф по тарифной политике INSON для страхования туристов", sid="idx-1")
    comp = [c for c in r["citations"] if c.get("source_kind") == "company"]
    ok("вопрос о компании может опираться на тарифную политику (подпись «документ INSON»)",
       r["source_kind"] in ("company", "note", "law", "none") and all(
           c["source_label"] == "документ INSON" for c in comp), (r["source_kind"], comp[:1]))


def check_review_fixes():
    print("12. правки контролёра: конкуренты, база доли, уточнение, таймаут, lex.uz")
    note = "docs/Знания/Рынок/Конкуренты — продукты и условия.md"
    ok("обзор конкурентов — тип competitor", legal.source_kind(note) == "competitor")
    ok("документы library/03_Рынок_НАПП/Конкуренты — competitor",
       legal.source_kind("library/03_Рынок_НАПП/Конкуренты/X/y.txt") == "competitor")
    ok("подпись competitor — «не норма»", "не норма" in legal.source_label("competitor", "ru"))
    if (legal.market_notes_dir() / "Конкуренты — продукты и условия.md").exists():
        kinds = {s_["path"].name: s_["kind"] for s_ in legal.source_files()}
        ok("в индексе обзор конкурентов идёт как competitor",
           kinds.get("Конкуренты — продукты и условия.md") == "competitor", kinds.get("Конкуренты — продукты и условия.md"))
    r = ask("условия КАСКО у других страховщиков и франшиза", sid="comp-1")
    kinds = [c.get("source_kind") for c in r["citations"] if not c.get("closest")]
    comp = [c for c in r["citations"] if c.get("source_kind") == "competitor"]
    ok("вопрос о конкурентах: цитаты конкурентов есть и помечены «не норма»",
       comp and all("не норма" in c["source_label"] for c in comp), kinds)
    if "law" in kinds and "competitor" in kinds:
        ok("цитаты competitor идут после law",
           max(i for i, k in enumerate(kinds) if k == "law") < min(i for i, k in enumerate(kinds) if k == "competitor"),
           kinds)
    ok("ответ с нормой первым — source_kind=law", r["source_kind"] == "law" or "law" not in kinds, r["source_kind"])
    r2 = ask("Что если страховая сумма больше стоимости имущества?", sid="comp-2")
    kinds2 = [c.get("source_kind") for c in r2["citations"]]
    ok("правовой вопрос: документ конкурента не вместо нормы",
       kinds2[:1] != ["competitor"] and all(c.get("supplement") for c in r2["citations"]
                                            if c.get("source_kind") == "competitor"), kinds2)
    lines = legal._clean_lines("Получить расчёт\nНомер телефона\nФ.И.О\nОтправить заявку\n"
                               "Франшиза по КАСКО составляет 0,5 % страховой суммы по каждому случаю.")
    ok("мусор веб-форм вырезан из пассажа", all(x == legal.GAP or "Франшиза" in x for x in lines)
       and any("Франшиза" in x for x in lines), lines)

    d = latest()
    n_pos = q1("SELECT COUNT(*) FROM market_stats WHERE report_date=? AND row_key LIKE 'company:%' "
               "AND premiums_ytd > 0", d)[0]
    n_all = q1("SELECT COUNT(*) FROM market_stats WHERE report_date=? AND row_key LIKE 'company:%'", d)[0]
    r3 = ask("кто лидер рынка по премиям", sid="share-1")
    ok("страховщики с нулевыми премиями не считаются (%d из %d строк)" % (n_pos, n_all),
       r3["market"]["numbers"]["insurers"] == n_pos and ("премиями: %d" % n_pos) in r3["answer"]["text"],
       r3["answer"]["text"])
    ok("лидер: база доли названа — среди страховщиков общего страхования",
       "доля среди страховщиков общего страхования" in r3["answer"]["text"])
    r4 = ask("убыточность по классу 14", sid="share-2")
    ok("класс: база доли — премии всего рынка, одна база в ответе",
       "доля в премиях всего рынка" in r4["answer"]["text"] and "общего страхования" not in r4["answer"]["text"],
       r4["answer"]["text"])
    r5 = ask("доля INSON", sid="share-3")
    ok("компания: доля среди страховщиков общего страхования, одна база",
       "доля среди страховщиков общего страхования" in r5["answer"]["text"]
       and "всего рынка" not in r5["answer"]["text"], r5["answer"]["text"])

    r6 = ask("а по классу 8?")
    ok("«а по классу 8?» без контекста — просьба уточнить показатель и период",
       r6.get("intent") == "clarify" and r6["answer"]["text"].startswith("Уточните")
       and "показатель" in r6["answer"]["text"] and "период" in r6["answer"]["text"], r6["answer"])
    ok("уточнение не выдаёт FAQ и не предлагает lex.uz", r6["answer"]["source"] == "clarify"
       and r6["lex_search_offer"] is False)
    ok("рыночный ответ не предлагает искать акт на lex.uz", r3["lex_search_offer"] is False)
    ok("правовой ответ (закон) — поиск на lex.uz уместен", r2["lex_search_offer"] is True, r2["source_kind"])
    out = legal._decorate({"answer": {"text": "x", "source": "passages"}, "citations": [
        {"act": "a", "unit": "", "source_kind": "company", "source_label": "тарифная политика INSON"}],
        "ai": {}}, "ru")
    ok("ответ из тарифной политики — без предложения lex.uz", out["lex_search_offer"] is False)

    from app import llm
    before = llm.TIMEOUT_SEC
    legal.ai_free_answer("вопрос", "ru")
    legal.ai_answer("вопрос", [{"act": "a", "unit": "", "body": "текст"}], "ru")
    ok("llm.TIMEOUT_SEC глобально не меняется", llm.TIMEOUT_SEC == before)
    # код специалиста — пакет app/legal (app/market_expert.py — фасад над ним): смотрим все его файлы
    src = "".join(p.read_text(encoding="utf-8") for p in sorted(Path(legal.__file__).parent.glob("*.py")))
    src += Path(mx.__file__).read_text(encoding="utf-8")
    ok("таймаут передаётся параметром вызова", "llm.TIMEOUT_SEC =" not in src and "timeout=" in src)


def check_competitor_intent():
    print("13. условия продуктов конкурентов — intent competitor")
    for q, lang in (("какая франшиза по КАСКО у конкурентов", "ru"),
                    ("KASKO boʻyicha raqobatchilarda franshiza qancha?", "uz"),
                    ("исключения по имуществу у других страховщиков", "ru"),
                    ("Boshqa sugʻurtalovchilarda mulk boʻyicha istisnolar qanday?", "uz")):
        r = ask(q, sid="cmp-" + lang)
        comps = {c.get("company") for c in r["citations"]}
        ok("%s → competitor, цитаты конкурентов (до 5, разные компании), «не норма»" % q,
           r.get("intent") == "competitor" and 0 < len(r["citations"]) <= 5 and len(comps) == len(r["citations"])
           and all(c.get("source_kind") == "competitor" and "не норма" in legal.source_label("competitor", "ru")
                   for c in r["citations"]) and r["sources"] and r["lex_search_offer"] is False,
           (r.get("intent"), r["answer"]["text"][:200]))
    r = ask("какая франшиза по КАСКО у конкурентов", sid="cmp-x")
    ok("статистика класса — только короткой строкой в конце", r["answer"]["text"].rstrip().endswith(
        r["market_line"]) if r.get("market_line") else "НАПП" not in r["answer"]["text"][:120],
       r.get("market_line"))
    if (legal.market_notes_dir() / "Конкуренты — продукты и условия.md").exists():
        r = ask("какая франшиза по КАСКО у конкурентов", sid="cmp-t")
        first = (r["citations"] or [{}])[0].get("quote") or ""
        names = ("Euroasia", "Kapital", "Semurg", "SQB", "Gross", "Alfa", "Imkon", "Neo")
        ok("первая цитата: слово «франшиза» и название компании, без «|» и «MKT-»",
           "франшиз" in first.lower() and any(n in first for n in names) and "|" not in first
           and "MKT-" not in first, first)
        ok("ответ начинается сводкой по компаниям («Компания — …; …»)",
           any(r["answer"]["text"].startswith(n) for n in names) and "; " in r["answer"]["text"].split(".")[0],
           r["answer"]["text"][:200])
        ok("в ответе нет служебных строк обзора", "MKT-" not in r["answer"]["text"]
           and "Цены могут отличаться" not in r["answer"]["text"] and "|" not in r["answer"]["text"])
    r = ask("что продаёт Gross", sid="cmp-g")
    ok("«что продаёт Gross» → документы Gross первыми", r.get("intent") == "competitor" and r["citations"]
       and "Gross" in (r["citations"][0].get("company") or ""), [c.get("company") for c in r["citations"]])
    r = ask("убыточность по классу 3", sid="cmp-m")
    ok("«убыточность по классу 3» — по-прежнему market", r.get("intent") == "market"
       and r["market"]["numbers"].get("row_key") == "cls3", r.get("intent"))


def check_ranking():
    print("13. финансы страховщиков — рэнкинг snsratings.uz")
    from app import rankings
    rankings.ensure_loaded()                   # в копии базы: как при старте сервера
    legal._cache.clear()

    def val(code, comp="INSON AJ"):
        r = q1("SELECT value_cur, rank_cur FROM company_rankings WHERE company=? AND indicator_code=?", comp, code)
        return (r[0], r[1]) if r else (None, None)

    def ind(r, code):
        return ((r.get("market") or {}).get("numbers") or {}).get("indicators", {}).get(code) or {}

    def sns(r):
        return any(s_.get("domain") == "snsratings.uz" and "Рэнкинг snsratings.uz" in (s_.get("title") or "")
                   for s_ in r.get("sources") or [])

    r = ask("активы APEX", sid="rk-1")
    v, rk_ = val("total_assets", "APEX INSURANCE AJ")
    ok("«активы APEX» → market, значение и место из company_rankings, источник рэнкинга",
       r.get("intent") == "market" and ind(r, "total_assets").get("value") == v and ind(r, "total_assets").get("rank")
       == rk_ and sns(r) and has_source(r), r["answer"]["text"][:200])
    r = ask("капитал INSON", sid="rk-2")
    v, rk_ = val("total_capital")
    ok("«капитал INSON» → совокупный капитал, место, изменение к прошлому году",
       ind(r, "total_capital").get("value") == v and mx._num(v, "ru", 1) in r["answer"]["text"]
       and "%d-е место" % rk_ in r["answer"]["text"] and "год назад" in r["answer"]["text"], r["answer"]["text"][:300])
    r = ask("резервы APEX", sid="rk-3")
    ok("«резервы APEX» → чистые и брутто резервы APEX (не собственные средства INSON)",
       ind(r, "reserves_net").get("value") == val("reserves_net", "APEX INSURANCE AJ")[0]
       and ind(r, "reserves_gross"), r["answer"]["text"][:200])
    r = ask("прибыль Kafolat", sid="rk-4")
    ok("«прибыль Kafolat» → чистая прибыль KAFOLAT",
       ind(r, "net_profit").get("value") == val("net_profit", "KAFOLAT SUG'URTA KOMPANIYASI AJ")[0],
       r["answer"]["text"][:200])
    r = ask("претензии и отказы у INSON", sid="rk-5")
    ok("«претензии и отказы у INSON» → поступило, отказано, доля отказов",
       ind(r, "claims_received").get("value") == val("claims_received")[0]
       and ind(r, "claims_refused").get("value") == val("claims_refused")[0] and ind(r, "refused_to_claims"),
       r["answer"]["text"][:200])
    r = ask("рентабельность INSON", sid="rk-6")
    ok("«рентабельность INSON» → ROE и ROA, вывод специалиста сравнивает с рынком",
       ind(r, "roe").get("value") == val("roe")[0] and ind(r, "roa") and r["parts"]["opinion"]
       and "среднего по рынку" in r["parts"]["opinion"]["text"], r["parts"].get("opinion"))
    r = ask("сколько договоров у INSON", sid="rk-7")
    ok("«сколько договоров у INSON» → действующие и заключённые договоры",
       ind(r, "contracts_active").get("value") == val("contracts_active")[0] and ind(r, "contracts_new"),
       r["answer"]["text"][:200])
    r = ask("расскажи про компанию INSON", sid="rk-8")
    codes = set(((r.get("market") or {}).get("numbers") or {}).get("indicators") or {})
    ok("карточка «расскажи про компанию INSON»: премии, активы, капитал, резервы, выплаты, претензии, отказы, "
       "рентабельность",
       {"premiums_total", "total_assets", "total_capital", "reserves_net", "claims_paid", "claims_received",
        "claims_refused", "roe"} <= codes and r["market"]["table"] and sns(r), sorted(codes))
    ok("карточка: пометка — рэнкинг, остатки на 30.06, а не «ytd НАПП»", "snsratings" in (r.get("note") or ""),
       r.get("note"))
    r = ask("у кого больше всего капитала", sid="rk-9")
    ok("«у кого больше всего капитала» → рейтинг из рэнкинга, APEX первым",
       (r["market"]["numbers"] or {}).get("leader") == "APEX INSURANCE AJ" and r["market"]["table"],
       r["answer"]["text"][:200])
    r = ask("INSON aktivlari qancha", sid="rk-10")
    ok("uz: активы INSON — тот же показатель", ind(r, "total_assets").get("value") == val("total_assets")[0]
       and r.get("lang") == "uz", r["answer"]["text"][:200])
    r = ask("может ли страховщик отказать в выплате?", sid="rk-11")
    ok("правовой вопрос об отказе — не рэнкинг", not sns(r), r.get("intent"))


def check_company_access():
    print("14. документы компании: только вошедшему сотруднику, не гостю")
    import tempfile
    from unittest.mock import patch
    from app.legal import answer as la, api as lapi, index as lix, search_core as lsc
    with tempfile.TemporaryDirectory() as root:
        tmp = Path(root, "library_live", "Компания")   # как на сервере: STORAGE_DIR/library_live/Компания
        tmp.mkdir(parents=True)
        Path(tmp, "Актуарный отчёт тест.txt").write_text(
            "Актуарный отчёт INSON\n\n1. Маржа платежеспособности\nМаржа платежеспособности "
            "актуарийзнак составила 1,07 на конец года.\n", encoding="utf-8")
        with patch.object(lix, "company_dir", return_value=tmp):
            lix._index_ready["checked"] = 0    # отпечаток состава сверяется не чаще раза в 10 с
            legal.ensure_index()
            q = "актуарий INSON маржа платежеспособности актуарийзнак"
            kinds = {s_["kind"] for s_ in legal.source_files()}
            ok("документ компании в индексе (company)", "company" in kinds, kinds)
            guest = [r for r in lsc.search(q, "ru") if r.get("source_kind") == "company"]
            mark = lsc.staff.set(True)
            try:
                emp = [r for r in lsc.search(q, "ru") if r.get("source_kind") == "company"]
            finally:
                lsc.staff.reset(mark)
            ok("гость не получает документ компании", not guest, guest[:1])
            ok("сотрудник получает документ компании", bool(emp), emp[:1])
            seen = []
            with patch.object(la, "_ask_routed", side_effect=lambda *a: seen.append(lsc.staff.get()) or {}):
                legal.ask(q, "ru", who="u:test-1")
                legal.ask(q, "ru", who="g:abc")
                legal.ask(q, "ru", who=None)
            ok("ask(): флаг сотрудника только для «u:<id>» и сбрасывается", seen == [True, False, False]
               and lsc.staff.get() is False, seen)
            acts = [a["act"] for a in lapi.legal_acts()["acts"]]
            ok("открытый список актов не называет документы компании",
               not any(a.startswith("Документ INSON") for a in acts), [a for a in acts if "INSON" in a])
        lix._index_ready["checked"] = 0
        legal.ensure_index()


def main():
    with temp_db():
        setup()
        mx.memory.clear()
        legal._cache.clear()
        check_detect()
        check_leader()
        check_class_and_followups()
        check_langs()
        check_legal_kept()
        check_api()
        check_memory()
        check_index_kinds()
        check_review_fixes()
        check_competitor_intent()
        check_ranking()
        check_privacy()
        check_company_access()
        check_inson()              # последним: удаляет строку INSON из копии базы
    print(f"\nИтого: {passed} ок, {failed} плохо")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
