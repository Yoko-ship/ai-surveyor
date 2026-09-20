"""
Слежение за законодательством: реестр актов, разбор страниц, события, пометка правил.

Запуск из корня проекта (pytest и httpx в sandbox\\.venv не установлены, поэтому обычные assert
и свой крошечный ASGI-клиент — живой сервер не трогаем и не перезапускаем):
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_lawwatch.py

Сеть НЕ дёргается: страницы lex.uz и napp.uz подменяются заготовками (fetch перехватывается).
Тестовые записи (события 'ТЕСТ-...', тема знаний, пометки правил) удаляются в конце.
"""
import asyncio
import json as _json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db          # noqa: E402
from app import lawwatch    # noqa: E402
from app import team        # noqa: E402
from app.main import app    # noqa: E402

MARK = "ТЕСТ-ЗАКОН"


# ---------- минимальный ASGI-клиент (httpx в окружении нет) ----------

def call(method: str, path: str, body=None, params=None):
    query = urlencode(params or {}, encoding="utf-8")
    payload = _json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(), "headers": [(b"host", b"test"), (b"content-type", b"application/json"),
                                                         (b"content-length", str(len(payload)).encode())],
             "client": ("127.0.0.1", 0), "server": ("test", 80)}
    out = {"status": None, "chunks": []}

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["chunks"].append(msg.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    raw = b"".join(out["chunks"]).decode("utf-8")
    try:
        return out["status"], _json.loads(raw)
    except ValueError:
        return out["status"], raw


# ---------- заготовки страниц вместо сети ----------

ACT_V1 = """<html><head><script>var t=1</script></head><body>
<div>Источники изменений Источники опубликования 08.08.2026 19.07.2026 29.06.2026</div>
<p>Настоящий Кодекс регулирует отношения по договору страхования. Страховая стоимость имущества
определяется на день заключения договора. Просмотров: 1201</p></body></html>"""

ACT_V2 = ACT_V1.replace("08.08.2026", "11.10.2026")
ACT_SAME_OTHER_COUNTER = ACT_V1.replace("Просмотров: 1201", "Просмотров: 1399")

LEX_FEED_PAGE = """<html><body>
<a href="/ru/docs/9000001">О внесении изменений в Положение о страховых агентах</a> 19.09.2026
<a href="/ru/docs/9000002">Об утверждении регламента выдачи справок о наличии земельного участка</a> 19.09.2026
</body></html>"""

NAPP_NEWS_PAGE = """<html><body>
<a href="https://napp.uz/ru/news/test-sugurta"><img></a>
<a href="https://napp.uz/ru/news/test-sugurta">Обсуждены вопросы развития рынка страхования</a> 20.09.2026
<a href="https://napp.uz/ru/news/test-obligation">Размещены облигации эмитента</a> 20.09.2026
</body></html>"""

NAPP_EMPTY = "<html><body>Страница не найдена, но сайт отвечает кодом 200</body></html>"


def fake_fetch(pages):
    """Подменяет lawwatch.fetch: возвращает заготовку по адресу. Сеть не трогается."""
    def _f(url, timeout=None):
        for key, page in pages.items():
            if key in url:
                return (page, "") if page is not None else (None, "источник недоступен (заготовка теста)")
        return None, "адрес в заготовках теста не описан"
    return _f


# ---------- подготовка и уборка ----------

def setup():
    db.ensure_schema()
    with db.tx() as con:
        lawwatch.seed_acts(con)
        con.execute("DELETE FROM watched_acts WHERE code LIKE 'тест_%'")
        con.execute("""INSERT INTO watched_acts (code, title, kind, lex_url, redaction, priority, why,
                       rules_refs, status) VALUES (?,?,?,?,?,?,?,?,?)""",
                    ("тест_акт", f"{MARK}: пробный акт о страховании", "закон",
                     "https://lex.uz/ru/docs/999999999", "08.08.2026", "высокий",
                     "проверка модуля", _json.dumps(["sum_over_value"]), lawwatch.ST_WATCH))
        con.execute("""INSERT INTO watched_acts (code, title, kind, lex_url, redaction, priority, why,
                       rules_refs, status) VALUES (?,?,?,?,?,?,?,?,?)""",
                    ("тест_безадреса", f"{MARK}: акт без адреса", "не установлен", None, None,
                     "высокий", "адрес не найден — ссылку не выдумываем", "[]", lawwatch.ST_NOURL))
        rule = con.execute("SELECT code, review_status FROM rules WHERE code='sum_over_value'").fetchone()
    assert rule, "в базе нет правила sum_over_value — соберите справочники (tools/db_build.py)"
    return rule["review_status"]


def teardown(rule_status):
    with db.tx() as con:
        con.execute("DELETE FROM law_events WHERE title LIKE ? OR url LIKE ?", (f"%{MARK}%", "%test-%"))
        con.execute("DELETE FROM law_events WHERE url IN (?,?)",
                    ("https://lex.uz/ru/docs/9000001", "https://lex.uz/ru/docs/9000002"))
        con.execute("DELETE FROM watched_acts WHERE code LIKE 'тест_%'")
        con.execute("UPDATE rules SET review_status=?, review_reason=NULL, review_since=NULL "
                    "WHERE code='sum_over_value'", (rule_status,))
        con.execute("DELETE FROM knowledge_topics WHERE topic LIKE ?", (f"%{MARK}%",))
        con.execute("DELETE FROM knowledge_topics WHERE topic LIKE ?",
                    ("%О внесении изменений в Положение о страховых агентах%",))
        con.execute("DELETE FROM audit WHERE entity LIKE 'lex.uz тест_%'")
        left = con.execute("SELECT COUNT(*) FROM law_events WHERE title LIKE ?", (f"%{MARK}%",)).fetchone()[0]
    print(f"  очищено; тестовых событий осталось: {left}")


# ---------- проверки ----------

def run():
    # 1. разбор страницы: дата редакции, хэш, устойчивость к счётчикам
    t1 = lawwatch.page_text(ACT_V1)
    assert lawwatch.redaction_of(t1) == "08.08.2026", lawwatch.redaction_of(t1)
    assert "var t=1" not in t1 and "<p>" not in t1, t1
    h1 = lawwatch.text_hash(t1)
    assert h1 == lawwatch.text_hash(lawwatch.page_text(ACT_SAME_OTHER_COUNTER)), \
        "счётчик просмотров меняет хэш — будут ложные события"
    assert h1 != lawwatch.text_hash(lawwatch.page_text(ACT_V2)), "новая редакция не различается"
    assert lawwatch.redaction_of(lawwatch.page_text(ACT_V2)) == "11.10.2026"
    print("1. разбор страницы: дата редакции читается, счётчики не влияют на хэш — ок")

    # 2. первый проход: хэш запоминается, событий нет
    lawwatch.fetch = fake_fetch({"999999999": ACT_V1})
    with db.tx() as con:
        act = db.rows(con, "SELECT * FROM watched_acts WHERE code='тест_акт'")[0]
        r = lawwatch.check_act(con, act)
    assert r["result"] == "первая проверка — хэш запомнен", r
    with db.tx() as con:
        n = con.execute("SELECT COUNT(*) FROM law_events WHERE act_code='тест_акт'").fetchone()[0]
    assert n == 0, "на первой проверке событий быть не должно"
    print("2. первая проверка: хэш запомнен, событие не заводится — ок")

    # 3. страница не изменилась — событий по-прежнему нет
    with db.tx() as con:
        act = db.rows(con, "SELECT * FROM watched_acts WHERE code='тест_акт'")[0]
        r = lawwatch.check_act(con, act)
        n = con.execute("SELECT COUNT(*) FROM law_events WHERE act_code='тест_акт'").fetchone()[0]
    assert r["result"] == "без изменений" and n == 0, (r, n)
    print("3. страница не менялась — ложного события нет — ок")

    # 4. новая редакция: событие, плашка, пометка правила (LAWWATCH-01)
    lawwatch.fetch = fake_fetch({"999999999": ACT_V2})
    with db.tx() as con:
        act = db.rows(con, "SELECT * FROM watched_acts WHERE code='тест_акт'")[0]
        r = lawwatch.check_act(con, act)
    assert r["result"] == lawwatch.ST_CHANGED and r["правила"] == ["sum_over_value"], r
    with db.tx() as con:
        ev = db.rows(con, "SELECT * FROM law_events WHERE act_code='тест_акт' ORDER BY id DESC")[0]
        rule = db.rows(con, "SELECT * FROM rules WHERE code='sum_over_value'")[0]
        acts = db.rows(con, "SELECT * FROM watched_acts WHERE code='тест_акт'")[0]
    assert ev["kind"] == "новая редакция" and ev["source"] == "lex.uz" and ev["seen"] == 0, ev
    assert ev["published_at"] == "11.10.2026" and "08.08.2026" in (ev["note"] or ""), ev
    assert ev["summary"].startswith(lawwatch.DISCLAIMER), ev["summary"]
    assert rule["review_status"] == "требует пересмотра" and "тест_акт" in rule["review_reason"], rule
    assert acts["status"] == lawwatch.ST_CHANGED and acts["redaction"] == "11.10.2026", acts
    print("4. новая редакция: событие с плашкой, правило помечено «требует пересмотра» — ок")

    # 5. та же редакция второй раз событие не дублирует
    with db.tx() as con:
        act = db.rows(con, "SELECT * FROM watched_acts WHERE code='тест_акт'")[0]
        lawwatch.check_act(con, act)
        n = con.execute("SELECT COUNT(*) FROM law_events WHERE act_code='тест_акт'").fetchone()[0]
    assert n == 1, f"событий по акту {n}, ожидалась одна запись"
    print("5. повтор прохода не плодит одинаковые события — ок")

    # 6. акт без адреса: ссылку не выдумываем
    with db.tx() as con:
        act = db.rows(con, "SELECT * FROM watched_acts WHERE code='тест_безадреса'")[0]
        r = lawwatch.check_act(con, act)
    assert r["result"] == lawwatch.ST_NOURL and not r["url"], r
    print("6. акт без адреса остаётся «адрес не найден», ссылка не придумывается — ок")

    # 7. источник недоступен (403 / капча) — пишем честно и идём дальше
    lawwatch.fetch = fake_fetch({"999999999": None})
    with db.tx() as con:
        act = db.rows(con, "SELECT * FROM watched_acts WHERE code='тест_акт'")[0]
        r = lawwatch.check_act(con, act)
        st = db.rows(con, "SELECT status FROM watched_acts WHERE code='тест_акт'")[0]["status"]
    assert r["result"] == lawwatch.ST_DOWN and st == lawwatch.ST_DOWN, (r, st)
    print("7. недоступный источник помечается «источник недоступен», обхода защиты нет — ок")

    # 8. ленты: ключевые слова фильтруют, дубли не заводятся, пустой раздел napp.uz не паникует
    lawwatch.fetch = fake_fetch({"lex.uz/ru/": LEX_FEED_PAGE, "napp.uz": NAPP_NEWS_PAGE})
    with db.tx() as con:
        f1 = lawwatch.check_lex_feed(con)
        n1 = lawwatch.check_napp_news(con)
    lawwatch._flush_topics(f1)                 # темы знаний заводятся уже после коммита
    with db.tx() as con:
        f2 = lawwatch.check_lex_feed(con)
    assert f1["в ленте"] == 2 and f1["новых по страхованию"] == 1, f1
    assert n1["в ленте"] == 2 and n1["новых по страхованию"] == 1, n1
    assert f2["новых по страхованию"] == 0, "лента завела дубль"
    with db.tx() as con:
        doc = db.rows(con, "SELECT * FROM law_events WHERE kind='новый документ' ORDER BY id DESC")[0]
        news = db.rows(con, "SELECT * FROM law_events WHERE kind='новость' ORDER BY id DESC")[0]
    assert doc["published_at"] == "19.09.2026" and doc["summary"].startswith(lawwatch.DISCLAIMER), doc
    assert news["source"] == "napp.uz" and "sugurta" in news["url"], news
    lawwatch.fetch = fake_fetch({"napp.uz": NAPP_EMPTY})
    with db.tx() as con:
        n2 = lawwatch.check_napp_news(con)
    assert "раздел не найден" in n2["результат"], n2
    print("9. ленты: по ключевым словам 1 из 2, дублей нет, страница-пустышка napp.uz не паникует — ок")

    # 9. тема на разбор заведена в реестре знаний
    with db.tx() as con:
        topics = db.rows(con, "SELECT * FROM knowledge_topics WHERE topic LIKE ? ORDER BY id DESC",
                         "%Разобрать изменение законодательства%")
    assert topics and topics[0]["area"] == "закон", topics[:1]
    print(f"10. реестр знаний: тем на разбор — {len(topics)} — ок")

    # 10. роуты
    st, b = call("GET", "/law-events", params={"limit": 20})
    assert st == 200 and b["disclaimer"] == lawwatch.DISCLAIMER, (st, b)
    assert any(r["code"] == "sum_over_value" for r in b["rules_to_review"]), b["rules_to_review"]
    st, b = call("GET", "/law-events", params={"kind": "такого вида нет"})
    assert st == 400, (st, b)
    st, acts = call("GET", "/watched-acts")
    assert st == 200 and acts["count"] >= 22, acts["count"]
    assert any(a["status"] == lawwatch.ST_NOURL and not a["lex_url"] for a in acts["items"]), \
        "акт без адреса должен остаться без ссылки"
    st, page = call("GET", "/law-feed")
    assert st == 200 and "Законодательство" in page, st
    print(f"11. роуты /law-events, /watched-acts ({acts['count']} актов), /law-feed — ок")

    # 11. пометку снимает человек, и только с объяснением
    st, b = call("POST", "/rules/sum_over_value/review-ok", {"who": "law", "note": ""})
    assert st == 400, (st, b)
    st, b = call("POST", "/rules/sum_over_value/review-ok",
                 {"who": "law", "note": "глава 52 не менялась, проверка остаётся как есть"})
    assert st == 200, (st, b)
    with db.tx() as con:
        rule = db.rows(con, "SELECT * FROM rules WHERE code='sum_over_value'")[0]
    assert rule["review_status"] == "ok" and "глава 52" in rule["review_reason"], rule
    print("12. пометку «требует пересмотра» снимает человек и только с объяснением — ок")

    # 12. событие разобрано → акт возвращается в наблюдение
    with db.tx() as con:
        eid = db.rows(con, "SELECT id FROM law_events WHERE act_code='тест_акт'")[0]["id"]
        con.execute("UPDATE watched_acts SET status=? WHERE code='тест_акт'", (lawwatch.ST_CHANGED,))
    st, b = call("POST", f"/law-events/{eid}/seen", {"who": "law", "note": "нас не касается"})
    assert st == 200, (st, b)
    with db.tx() as con:
        ev = db.rows(con, "SELECT * FROM law_events WHERE id=?", eid)[0]
        a = db.rows(con, "SELECT * FROM watched_acts WHERE code='тест_акт'")[0]
    assert ev["seen"] == 1 and a["status"] == lawwatch.ST_WATCH, (ev, a)
    print("13. разобранное событие закрывается, акт возвращается в наблюдение — ок")

    # 13. раздел в ежедневном докладе (чужие разделы на месте)
    with db.tx() as con:
        body, _ = team.build_report(con, date.today())
    assert "## 6б. Изменения законодательства и новости" in body, "нет раздела в докладе"
    assert "## 6а. Согласования" in body, "снесён чужой раздел «Согласования»"
    assert "## 4а. Знания команды" in body, "снесён раздел «Знания команды»"
    print("14. в докладе есть раздел 6б, разделы 4а и 6а на месте — ок")


if __name__ == "__main__":
    real_fetch = lawwatch.fetch
    status = setup()
    try:
        run()
        print("\nВсе проверки слежения за законодательством пройдены.")
    finally:
        lawwatch.fetch = real_fetch
        teardown(status)
