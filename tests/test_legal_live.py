"""
Живой поиск «ИИ специалиста» на lex.uz: app/legal_live.py + app/lexuz.py + ветка в app/legal.ask.

СЕТЬ НЕ ИСПОЛЬЗУЕТСЯ: функция lexuz._http подменяется, ответы lex.uz берутся из сохранённых
образцов страниц в tests/fixtures/ (сняты с сайта 28.09.2026):
  lex_search_vklady.html        — выдача поиска, в ней Закон ЗРУ-1031 «О гарантиях защиты вкладов в банках»;
  lex_act_7389439.html          — страница этого закона (полный русский текст);
  lex_search_urozhay.html       — выдача поиска по названию «страхование урожая»;
  lex_act_6993260_shell.html    — русская страница ПКМ № 373 без текста («приводится на узбекском»).

Вся работа — во временной копии базы (tests/tmpdb.py) и во временной папке библиотеки:
рабочая база и library/ проекта не меняются.

Запуск из корня:
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_legal_live.py

Что проверяем:
  1. найдено на lex.uz и процитировано дословно; ссылка на lex.uz; пометка «найдено сейчас»,
     русский текст — неофициальный перевод; акт сохранён в библиотеку и попал в индекс;
  2. повторный вопрос отвечается из базы без обращения к сайту;
  3. сайт недоступен (таймаут, капча, 403, запрет robots.txt) → честное «lex.uz сейчас недоступен»,
     ответ по базе, без повторных попыток;
  4. ПД из вопроса не попадают в запрос к lex.uz;
  5. общий часовой предел обращений срабатывает;
  6. LEX_LIVE=0 отключает живой поиск;
  7. страница-«оболочка» без русского текста не цитируется, даётся ссылка на узбекский оригинал;
  8. только lex.uz: чужой адрес отклоняется до запроса;
  9. кэш на 24 часа: в таблице lex_live_cache, адрес выдачи поиска не хранится;
 10. актуальность: цитата из базы по акту, изменившемуся на lex.uz (lawwatch), помечена;
 11. пауза между запросами не меньше заданной;
 12. (Б1) фамилии, имена, улицы, названия компаний не уходят на lex.uz — ни в адрес запроса,
     ни в кэш, ни в журнал; разрешённый словарь; нет слов — no_keywords; ключ кэша с солью;
 13. (Б2) после сохранения акта tools/library_build.py не запускается;
 14. (Б3) при STORAGE_DIR акт сохраняется в STORAGE_DIR/library_live и виден индексу после перезапуска;
 15. (Б4) имя файла не длиннее 200 байт UTF-8 с расширением, режется по границе символа;
 16. (Б5) личный предел живого поиска на пользователя/гостя;
 17. (Б6) срок на весь запрос, предел чтения 5 МБ и распаковки gzip, 15 с на вопрос;
 18. (Б7) robots.txt: редирект на 404 и HTML вместо файла — «ограничений нет», настоящий запрет соблюдается;
 19. (Б8) пометка для en — про английский текст; is_lex — только https.
"""
import gzip
import hashlib
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["SURVEYOR_NO_BACKGROUND"] = "1"
os.environ["LEX_LIVE"] = "1"
os.environ.pop("LEX_LIVE_PER_HOUR", None)

from tmpdb import temp_db                        # noqa: E402
from app import db, legal, legal_live, lexuz     # noqa: E402

ORIG_INTERVAL = lexuz.MIN_INTERVAL_SEC
ORIG_HTTP = lexuz._http
ORIG_OPENER = lexuz._opener
ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
GK_FILE = ROOT / "library" / "01_Законодательство" / "01_Законы" / "ГК РУз глава 52 Страхование (ст. 914-961).txt"

Q_FOUND = "Какой размер компенсации по гарантированным вкладам в банке?"
Q_SHELL = "Какие субсидии выделяются на страхование урожая посевов?"
Q_OTHER = "Нужно ли страховать туристов при выезде за рубеж?"

passed, failed = 0, 0


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ок  ", name)
    else:
        failed += 1
        print("  ПЛОХО", name, extra)


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


class FakeLex:
    """Подмена lexuz._http: отдаёт образцы страниц и записывает, какие адреса запрашивались."""

    def __init__(self, mode="ok", search="lex_search_vklady.html", robots=None, echo=False, delay=0.0):
        self.mode, self.search, self.robots = mode, search, robots
        self.echo, self.delay = echo, delay
        self.calls = []
        self.agents = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        if timeout > 8:
            raise AssertionError("таймаут живого поиска больше 8 с")
        if self.mode == "timeout":
            raise TimeoutError("timed out")
        if self.delay:
            # медленный сайт: ответ через delay секунд, а если срок меньше — обрыв по сроку
            time.sleep(min(self.delay, timeout))
            if self.delay > timeout:
                raise TimeoutError("timed out")
        if url.endswith("/robots.txt"):
            if self.robots == "redirect404":         # как на lex.uz 28.09.2026: 302 → HTML-страница 404
                return 200, "https://lex.uz/Pages/404.aspx", b"<html><head><title>404</title></head></html>"
            if self.robots == "html200":             # «мягкая» 404: HTML прямо по адресу robots.txt
                return 200, url, b"<!DOCTYPE html>\n<html><body>Page not found</body></html>"
            if self.robots == "403":
                return 403, url, b""
            if self.robots is not None:
                return 200, url, self.robots.encode()
            return 404, "https://lex.uz/Pages/404.aspx", b""       # как на настоящем сайте
        if "/search/" in url:
            if self.mode == "captcha":
                return 200, url, b"<html><body><div class='g-recaptcha'></div>captcha</body></html>"
            if self.mode == "403":
                return 403, url, b""
            page = fixture(self.search)
            if self.echo:
                # настоящая выдача показывает запрос в строке поиска — значит, он попадёт и в кэш
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                words = " ".join(v for vs in q.values() for v in vs)
                page = page + ("<input value='%s'>" % words).encode("utf-8")
            return 200, url, page
        num = lexuz.doc_id(url)
        if num == "7389439":
            return 200, url, fixture("lex_act_7389439.html")
        if num == "6993260":
            return 200, url, fixture("lex_act_6993260_shell.html")
        return 404, "https://lex.uz/Pages/404.aspx", b""

    def lex_calls(self):
        return [u for u in self.calls if not u.endswith("/robots.txt")]


def fresh(fake=None):
    """Сброс состояния между проверками: кэши ответа, счётчики, robots, подмена сети."""
    legal._cache.clear()
    legal_live.reset()
    lexuz._robots.update({"rp": None, "at": 0.0})
    with db.tx() as con:
        con.execute(legal_live.CACHE_SQL)
        con.execute("DELETE FROM lex_live_cache")
    if fake is not None:
        lexuz._http = fake
    return fake


def setup_library(tmp: Path):
    """Временная библиотека: только ГК глава 52 (для «ответа по базе»), заметок нет."""
    lib = tmp / "library" / "01_Законодательство"
    (lib / "01_Законы").mkdir(parents=True)
    (tmp / "docs").mkdir()
    shutil.copy2(GK_FILE, lib / "01_Законы" / GK_FILE.name)
    legal.ROOT, legal.LIB, legal.NOTES = tmp, lib, tmp / "docs"
    legal._index_ready.update({"stamp": None, "checked": 0.0})
    legal._text_cache.clear()
    return lib


def main():
    orig = (legal.ROOT, legal.LIB, legal.NOTES, lexuz._http, lexuz.MIN_INTERVAL_SEC, legal.LIVE_LIB,
            legal_live.QUESTION_BUDGET_SEC, lexuz._opener)
    tmp = Path(tempfile.mkdtemp(prefix="surveyor-live-"))
    lexuz.MIN_INTERVAL_SEC = 0.0                  # паузу проверяем отдельно (п. 11)
    legal.LIVE_LIB = None                         # постоянный диск проверяем отдельно (п. 14)
    legal_live._salt_cache["v"] = None            # соль — из временной базы, не из рабочей
    try:
        with temp_db():
            lib = setup_library(tmp)
            legal.reindex(force=True)
            with db.tx() as con:                  # актуальность проверяем отдельно (п. 10)
                con.execute("UPDATE watched_acts SET status='следим'")
                con.execute("UPDATE rules SET review_status='ok' WHERE review_status='требует пересмотра'")
            run_all(lib)
            run_more(lib, tmp)
    finally:
        (legal.ROOT, legal.LIB, legal.NOTES, lexuz._http, lexuz.MIN_INTERVAL_SEC, legal.LIVE_LIB,
         legal_live.QUESTION_BUDGET_SEC, lexuz._opener) = orig
        legal_live._salt_cache["v"] = None
        lexuz._robots.update({"rp": None, "at": 0.0})
        legal._index_ready.update({"stamp": None, "checked": 0.0})
        legal._cache.clear()
        legal._text_cache.clear()
        legal_live.reset()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nИтого: {passed} ок, {failed} плохо")
    return failed == 0


def run_all(lib: Path):
    act_text = lexuz.extract_text(fixture("lex_act_7389439.html").decode("utf-8"))

    print("\n1. найдено на lex.uz и процитировано дословно")
    fake = fresh(FakeLex())
    r = legal.ask(Q_FOUND, "ru")
    live = r.get("live") or {}
    ok("живой поиск: статус found", live.get("status") == "found", live)
    ok("ответ — по норме (source=passages), помечен found_on=lex.uz",
       r["answer"]["source"] == "passages" and r["answer"].get("found_on") == "lex.uz", r["answer"])
    cits = [c for c in r["citations"] if c.get("live")]
    ok("есть цитата, найденная вживую", bool(cits), r["citations"])
    c = cits[0] if cits else {}
    ok("цитата не пустая и дословно есть в тексте акта на lex.uz",
       len(c.get("quote") or "") > 20 and legal.quote_norm(legal.quote_core(c["quote"])) in legal.quote_norm(act_text),
       c.get("quote"))
    ok("акт и статья: ЗРУ-1031, «ст. N»", "1031" in (c.get("act") or "") and (c.get("unit") or "").startswith("ст. "),
       (c.get("act"), c.get("unit")))
    ok("ссылка — страница акта на lex.uz", c.get("url") == "https://lex.uz/ru/docs/7389439", c.get("url"))
    ok("пометка «найдено на lex.uz сейчас» в цитате и в live", c.get("live_label") == "найдено на lex.uz сейчас"
       and live.get("label") == "найдено на lex.uz сейчас", (c.get("live_label"), live.get("label")))
    ok("русский текст — неофициальный перевод (official=False, пометка в note)",
       c.get("official") is False and "неофициальный перевод" in (r.get("note") or ""), r.get("note"))
    ok("не больше 3 страниц lex.uz на вопрос", 1 <= len(fake.lex_calls()) <= 3, fake.lex_calls())
    ok("все обращения — только к lex.uz", all(lexuz.is_lex(u) for u in fake.calls), fake.calls)
    ok("поиск — только действующие акты (status=Y)", "status=Y" in fake.lex_calls()[0], fake.lex_calls())
    saved = list(lib.rglob("*1031*.txt"))
    ok("акт сохранён в библиотеку (.txt и .html)", bool(saved) and saved[0].with_suffix(".html").exists(), saved)
    head = saved[0].read_text(encoding="utf-8")[:300] if saved else ""
    ok("шапка файла: Источник lex.uz и пометка живого поиска",
       "Источник: https://lex.uz/ru/docs/7389439" in head and "живой поиск" in head, head[:200])
    ok("цитата сверена с сохранённым файлом (legal.verbatim)",
       bool(saved) and legal.verbatim(saved[0].relative_to(legal.ROOT).as_posix(), c.get("quote") or ""))
    with db.tx() as con:
        n = con.execute("SELECT COUNT(*) FROM legal_chunks WHERE path LIKE ?", ("%1031%",)).fetchone()[0]
        src = con.execute("SELECT source FROM legal_questions ORDER BY id DESC LIMIT 1").fetchone()[0]
    ok("акт попал в индекс", n > 10, n)
    ok("в журнале вопросов источник lex (текста вопроса нет)", src == "lex", src)

    print("\n2. повтор после сброса кэша — lex.uz проверяется перед базой")
    fake2 = fresh(FakeLex())
    r2 = legal.ask(Q_FOUND, "ru")
    ok("сначала поиск на lex.uz, затем сохранённый акт", bool(fake2.lex_calls()) and r2["live"]["status"] == "found_base", fake2.calls)
    ok("ответ по норме из базы", r2["answer"]["source"] == "passages" and r2["citations"], r2["answer"])
    ok("цитата ЗРУ-1031 из базы", any("1031" in (x.get("act") or "") for x in r2["citations"]), r2["citations"][:1])

    print("\n3. сайт недоступен — честное сообщение и ответ по базе")
    q_other = Q_OTHER
    for mode, word in (("timeout", "не ответил"), ("captcha", "капч"), ("403", "403")):
        fake3 = fresh(FakeLex(mode=mode))
        r3 = legal.ask(q_other, "ru")
        lv = r3.get("live") or {}
        ok(f"{mode}: статус unavailable", lv.get("status") == "unavailable", lv)
        ok(f"{mode}: в ответе «lex.uz сейчас недоступен»", "lex.uz сейчас недоступен" in r3["answer"]["text"],
           r3["answer"]["text"])
        ok(f"{mode}: причина названа ({word})", word in (lv.get("reason") or ""), lv.get("reason"))
        ok(f"{mode}: ответ по локальной базе (source=none, ответ есть)",
           r3["answer"]["source"] == "none" and bool(r3["answer"]["text"]))
        before = len(fake3.calls)
        ok(f"{mode}: без повторных попыток (одно обращение к странице)", len(fake3.lex_calls()) <= 1, fake3.calls)
        legal._cache.clear()
        r3b = legal.ask(q_other + " ", "ru")
        ok(f"{mode}: следующий вопрос в паузе — на сайт не ходим", len(fake3.calls) == before
           and (r3b.get("live") or {}).get("status") == "unavailable", fake3.calls)
    fake_r = fresh(FakeLex(robots="User-agent: *\nDisallow: /\n"))
    rr = legal.ask(q_other, "ru")
    ok("robots.txt запрещает — страницы не запрашиваются", fake_r.lex_calls() == [], fake_r.calls)
    ok("robots.txt запрещает — статус unavailable с причиной",
       (rr.get("live") or {}).get("status") == "unavailable" and "robots" in (rr["live"].get("reason") or ""),
       rr.get("live"))

    print("\n4. персональные данные не уходят на lex.uz")
    pd_q = ("Иванов Иван Иванович, паспорт AA1234567, ПИНФЛ 31234567890123, тел. +998 90 123 45 67, "
            "почта ivanov@mail.uz, ИНН 123456789 — какой размер компенсации по гарантированным вкладам?")
    fake4 = fresh(FakeLex())
    legal.ask(pd_q, "ru")
    sent = " ".join(urllib.parse.unquote(u) for u in fake4.calls).lower()
    leaks = [x for x in ("иванов", "иван", "aa1234567", "1234567", "31234567890123", "998", "123 45 67",
                         "ivanov", "mail.uz", "123456789") if x in sent]
    ok("в запросах к lex.uz нет ФИО, паспорта, ПИНФЛ, телефона, почты, ИНН", not leaks, (leaks, fake4.calls))
    words = legal_live.plan_queries(pd_q, "ru")
    ok("в поиск идут 1–2 ключевых слова, а не весь вопрос",
       all(1 <= len(q.split()) <= 2 for q in words["queries"]), words)
    ok("отчество со строчной буквы выбрасывается вместе с соседями",
       "петров" not in " ".join(legal_live.words_of("петров сергеевич страхование урожая", "ru")))

    print("\n5. общий предел обращений в час")
    os.environ["LEX_LIVE_PER_HOUR"] = "1"
    try:
        fake5 = fresh(FakeLex())
        r5 = legal.ask(Q_SHELL, "ru")
        lv5 = r5.get("live") or {}
        ok("предел 1 в час: второе обращение не выполняется, статус limit",
           lv5.get("status") == "limit" and len(fake5.lex_calls()) == 1, (lv5, fake5.calls))
        ok("при пределе ответ по базе и честная пометка", "предел" in r5["answer"]["text"], r5["answer"]["text"])
        ok("usage() показывает расход", legal_live.usage()["used_last_hour"] == 1 and
           legal_live.usage()["per_hour"] == 1, legal_live.usage())
    finally:
        os.environ.pop("LEX_LIVE_PER_HOUR", None)
    ok("по умолчанию предел 60 в час", legal_live.per_hour() == 60, legal_live.per_hour())

    print("\n6. LEX_LIVE=0 отключает живой поиск")
    os.environ["LEX_LIVE"] = "0"
    try:
        fake6 = fresh(FakeLex())
        r6 = legal.ask(q_other, "ru")
        ok("к lex.uz не обращались", fake6.calls == [], fake6.calls)
        ok("статус off, ответ по базе как раньше", (r6.get("live") or {}).get("status") == "off"
           and r6["answer"]["source"] == "none", r6.get("live"))
    finally:
        os.environ["LEX_LIVE"] = "1"

    print("\n7. «оболочка» без русского текста не цитируется")
    fake7 = fresh(FakeLex(search="lex_search_urozhay.html"))
    r7 = legal.ask(Q_SHELL, "ru")
    lv7 = r7.get("live") or {}
    ok("статус not_found — нормы нет, как раньше", lv7.get("status") == "not_found"
       and r7["answer"]["source"] == "none", lv7)
    ok("не больше 3 страниц", len(fake7.lex_calls()) <= 3, fake7.lex_calls())
    sk = (lv7.get("skipped") or [{}])[0]
    ok("дана ссылка на узбекский оригинал (-6993258)", sk.get("official_url") == "https://lex.uz/ru/docs/-6993258", sk)
    ok("в пометке сказано, что текст только на узбекском", "только на узбекском" in (r7.get("note") or ""),
       r7.get("note"))
    ok("живых цитат нет", not any(x.get("live") for x in r7["citations"]))

    print("\n8. только lex.uz")
    fake8 = fresh(FakeLex())
    try:
        lexuz.fetch("https://example.com/docs/1", 8, "act")
        ok("чужой адрес отклонён", False)
    except lexuz.LexUnavailable:
        ok("чужой адрес отклонён до запроса", fake8.calls == [], fake8.calls)
    ok("User-Agent честный, с названием системы", "INSON" in lexuz.USER_AGENT and "Mozilla" not in lexuz.USER_AGENT)

    print("\n9. кэш на 24 часа")
    fake9 = fresh(FakeLex(search="lex_search_urozhay.html"))
    legal.ask(Q_SHELL, "ru")
    n1 = len(fake9.calls)
    legal._cache.clear()
    legal.ask(Q_SHELL, "ru")
    ok("повтор в течение суток — страницы из кэша, к сайту не ходим", len(fake9.calls) == n1, fake9.calls)
    with db.tx() as con:
        rows = db.rows(con, "SELECT kind, url, created_at FROM lex_live_cache")
    ok("в кэше есть выдача и страница акта", {r["kind"] for r in rows} >= {"search", "act"}, rows)
    ok("адрес выдачи поиска (со словами вопроса) не хранится",
       all(r["url"] is None for r in rows if r["kind"] == "search"), rows)
    with db.tx() as con:
        con.execute("UPDATE lex_live_cache SET created_at = created_at - 90000")
    legal._cache.clear()
    legal_live.reset()
    legal.ask(Q_SHELL, "ru")
    ok("через сутки кэш устарел — страницы берутся заново", len(fake9.calls) > n1, fake9.calls)

    print("\n10. актуальность по данным слежения (lawwatch)")
    fresh(FakeLex())
    q_gk = "Что будет, если страховая сумма превышает страховую стоимость имущества?"
    r10 = legal.ask(q_gk, "ru")
    gk = [x for x in r10["citations"] if "180550" in (x.get("url") or "")]
    ok("вопрос по ГК отвечается из базы", bool(gk), r10["citations"][:1])
    ok("пока акт не менялся — пометки нет", not any(x.get("actuality") for x in r10["citations"]))
    with db.tx() as con:
        con.execute("UPDATE watched_acts SET status='изменился', redaction='08.08.2026' WHERE code='gk_ch52'")
    r10b = legal.ask(q_gk, "ru")                   # из кэша ответа — пометка всё равно свежая
    act = next((x.get("actuality") for x in r10b["citations"] if "180550" in (x.get("url") or "")), None)
    ok("акт изменился — у цитаты пометка actuality", bool(act), r10b["citations"][:1])
    ok("в пометке ссылка на действующую редакцию на lex.uz", act and "https://lex.uz/" in act["text"]
       and act["url"].startswith("https://lex.uz/"), act)
    ok("в note сказано о пересмотре", "пересмотр" in (r10b.get("note") or ""), r10b.get("note"))
    with db.tx() as con:
        con.execute("UPDATE watched_acts SET status='следим' WHERE code='gk_ch52'")
        con.execute("UPDATE rules SET review_status='требует пересмотра' WHERE code='sum_over_value'")
    r10c = legal.ask(q_gk, "ru")
    act_c = next((x.get("actuality") for x in r10c["citations"] if "180550" in (x.get("url") or "")), None)
    ok("правило по акту «требует пересмотра» — пометка тоже есть",
       bool(act_c) and act_c["status"] == "требует пересмотра", act_c)
    with db.tx() as con:
        con.execute("UPDATE rules SET review_status='ok' WHERE code='sum_over_value'")

    print("\n11. пауза между запросами")
    fake11 = fresh(FakeLex())
    lexuz.MIN_INTERVAL_SEC = 0.3
    try:
        t = time.monotonic()
        lexuz.fetch(lexuz.search_url("вклады", "ru"), 8, "search")
        lexuz.fetch(lexuz.search_url("вклады банк", "ru"), 8, "search")
        took = time.monotonic() - t
        ok("между обращениями выдерживается пауза", took >= 0.6, round(took, 2))   # robots + 2 страницы
    finally:
        lexuz.MIN_INTERVAL_SEC = 0.0
    ok("по умолчанию — не чаще 1 запроса в секунду, таймаут 8 с, не больше 3 страниц",
       ORIG_INTERVAL == 1.0 and legal_live.TIMEOUT_SEC == 8 and legal_live.MAX_PAGES == 3)


# --------------------------------------------------------------------------- #
#  Замечания контролёра 28.09.2026 (Б1–Б8)
# --------------------------------------------------------------------------- #

# Примеры утечек из замечания Б1: (вопрос, язык, слова, которые не должны уйти никуда)
PD_CASES = [
    ("Застраховал склад Каримов, какая франшиза?", "ru", ["каримов"]),
    ("мой клиент ахмедов умер, что положено по страхованию?", "ru", ["ахмедов", "мой"]),
    ("страхователь рахимова просит вернуть премию", "ru", ["рахимова"]),
    ("ташмухамедов бахтиёр страхование урожая", "ru", ["ташмухамедов", "бахтиёр"]),
    ("karimov sugʻurta shartnomasi", "uz", ["karimov"]),
    ("адрес ул. Навои 12, склад сгорел — что делать?", "ru", ["навои"]),
    ("ООО «Ромашка-Строй» застраховало склад, какая франшиза?", "ru", ["ромашка"]),
]


def _audit_rows(since_id: int) -> list:
    with db.tx() as con:
        return db.rows(con, "SELECT * FROM audit WHERE id > ?", since_id)


def _cache_dump() -> str:
    """Всё содержимое кэша живого поиска текстом: ключи, адреса и распакованные страницы."""
    with db.tx() as con:
        con.execute(legal_live.CACHE_SQL)
        rows = con.execute("SELECT key, kind, url, body FROM lex_live_cache").fetchall()
    # сами образцы страниц сайта (там есть любые названия и имена) вычитаем: ищем только то,
    # что добавил наш запрос — эхо строки поиска, адрес, ключ
    fixtures = [lexuz.decode(f.read_bytes()) for f in FIX.glob("*.html")]   # байты: переводы строк как у сайта
    parts = []
    for key, kind, url, body in rows:
        try:
            page = zlib.decompress(body).decode("utf-8")
        except Exception:
            page = ""
        for f in fixtures:
            page = page.replace(f, "")
        parts.append(" ".join([key or "", kind or "", url or "", page]))
    return urllib.parse.unquote(" ".join(parts)).lower()


def run_more(lib: Path, tmp: Path):
    print("\n12. (Б1) персональные данные: разрешённый словарь, соседи имён, соль кэша")
    with db.tx() as con:
        audit0 = con.execute("SELECT COALESCE(MAX(id), 0) FROM audit").fetchone()[0]
    for q, lang, bad in PD_CASES:
        # и через ask (как в жизни), и прямо через lookup — чтобы запрос к сайту точно был
        fake = fresh(FakeLex(echo=True, search="lex_search_urozhay.html"))
        legal.ask(q, lang, who="g:test-guest-token-000000000")
        legal_live.lookup(q, lang, who="g:test-guest-token-000000000")
        sent = " ".join(urllib.parse.unquote(u) for u in fake.calls).lower()
        plan = legal_live.plan_queries(q, lang)
        planned = " ".join(plan["queries"] + plan["words"]).lower()
        cache = _cache_dump()
        leaks = [w for w in bad if w in sent or w in planned]
        ok(f"«{q[:40]}»: {bad} не в адресе запроса", not leaks, (leaks, plan["queries"], fake.calls))
        ok(f"«{q[:40]}»: {bad} не в кэше", not [w for w in bad if w in cache], plan["queries"])
    audit = " ".join(str(r) for r in _audit_rows(audit0)).lower()
    with db.tx() as con:
        qlog = " ".join(str(r) for r in db.rows(con, "SELECT * FROM legal_questions")).lower()
    all_bad = [w for _q, _l, bad in PD_CASES for w in bad if w != "мой"]
    ok("ни одно из слов-примеров не попало в журнал (audit)", not [w for w in all_bad if w in audit],
       [w for w in all_bad if w in audit])
    ok("и в журнал вопросов (legal_questions) тоже", not [w for w in all_bad if w in qlog])
    ex = {}
    legal_live.words_of("клиент Ахмедов Бахтиёр и квазибрульонный склад", "ru", ex)
    ok("слово вне словаря законодательства не отправляется",
       ex.get("квазибрульонный") == "нет в словаре законодательства", ex)
    ok("«мой» — стоп-слово", "мой" not in legal_live.words_of("мой склад сгорел", "ru"))
    ok("слово с заглавной не в начале предложения — вон",
       "каримов" not in legal_live.words_of("Склад Каримов сгорел", "ru"))
    ok("обычное слово закона на -ов (рисков, договоров) остаётся",
       {"рисков", "договоров"} <= set(legal_live.words_of("сколько рисков и договоров", "ru")),
       legal_live.words_of("сколько рисков и договоров", "ru"))
    fake = fresh(FakeLex())
    r = legal_live.lookup("Бахтиёр Ташмухамедов?", "ru")
    ok("после фильтра слов нет — на сайт не идём, status no_keywords",
       r["status"] == "no_keywords" and fake.calls == [], (r, fake.calls))
    fresh(FakeLex(search="lex_search_urozhay.html"))
    legal_live.lookup(Q_SHELL, "ru")
    with db.tx() as con:
        keys = {row[0] for row in con.execute("SELECT key FROM lex_live_cache")}
        salt = con.execute("SELECT value FROM app_settings WHERE key='LEX_CACHE_SALT'").fetchone()
    q0 = legal_live.plan_queries(Q_SHELL, "ru")["queries"][0]
    plain = hashlib.sha256(("search|" + lexuz.search_url(q0, "ru")).encode("utf-8")).hexdigest()[:40]
    ok("ключ кэша — с солью: простой sha256 от адреса его не даёт", keys and plain not in keys, keys)
    ok("соль случайная и сохранена в базе (app_settings)", bool(salt and len(salt[0]) >= 32))
    os.environ["LEX_CACHE_SALT"] = "соль-из-окружения"
    legal_live._salt_cache["v"] = None
    try:
        ok("соль из окружения важнее сохранённой", legal_live._salt() == "соль-из-окружения".encode("utf-8"))
    finally:
        os.environ.pop("LEX_CACHE_SALT", None)
        legal_live._salt_cache["v"] = None

    print("\n13. (Б2) library_build из живого поиска не запускается")
    import subprocess
    started = []
    orig_run, orig_popen = subprocess.run, subprocess.Popen
    subprocess.run = lambda *a, **k: started.append(a)
    subprocess.Popen = lambda *a, **k: started.append(a)
    os.environ.pop("SURVEYOR_NO_BACKGROUND", None)
    try:
        for p in list(lib.rglob("*1031*")):
            p.unlink()
        legal.reindex()
        fresh(FakeLex())
        r13 = legal_live.lookup(Q_FOUND, "ru")
        time.sleep(0.2)
        ok("акт найден и сохранён", r13["status"] == "found", r13.get("status"))
        ok("ни subprocess, ни поток library-build не запускались",
           not started and not any(t.name == "library-build" for t in threading.enumerate()), started)
    finally:
        subprocess.run, subprocess.Popen = orig_run, orig_popen
        os.environ["SURVEYOR_NO_BACKGROUND"] = "1"

    print("\n14. (Б3) постоянный диск: STORAGE_DIR/library_live")
    for p in list(lib.rglob("*1031*")):
        p.unlink()
    live_dir = tmp / "storage" / "library_live"
    legal.LIVE_LIB = live_dir
    try:
        legal.reindex()
        fresh(FakeLex())
        r14 = legal.ask(Q_FOUND, "ru")
        saved = list(live_dir.rglob("*1031*.txt"))
        ok("акт сохранён в STORAGE_DIR/library_live, а не в library/",
           bool(saved) and not list(lib.rglob("*1031*")), (saved, list(lib.rglob("*1031*"))))
        ok("ответ найден вживую", (r14.get("live") or {}).get("status") == "found", r14.get("live"))
        # «перезапуск»: отметка индекса сброшена, индекс пересобран с нуля
        legal._index_ready.update({"stamp": None, "checked": 0.0})
        legal.reindex(force=True)
        with db.tx() as con:
            n = con.execute("SELECT COUNT(*) FROM legal_chunks WHERE path LIKE ?", ("%library_live%1031%",)).fetchone()[0]
        ok("после перезапуска индекс видит акт на постоянном диске", n > 10, n)
        ok("legal.source_files включает library_live",
           any("library_live" in s["path"].as_posix() for s in legal.source_files()))
        c = next((x for x in r14["citations"] if x.get("live")), {})
        ok("цитата сверяется дословно с файлом на постоянном диске",
           bool(saved) and legal.verbatim(legal.rel_path(saved[0]), c.get("quote") or ""))
        fake14 = fresh(FakeLex())
        r14b = legal.ask(Q_FOUND, "ru")
        ok("повтор после перезапуска — сначала lex.uz", bool(fake14.lex_calls())
           and r14b["live"]["status"] == "found_base" and r14b["answer"]["source"] == "passages",
           fake14.calls)
    finally:
        legal.LIVE_LIB = None
        shutil.rmtree(live_dir, ignore_errors=True)
        legal.reindex()

    print("\n15. (Б4) длина имени файла в байтах")
    long_ru = "ЗРУ-1031 от 18.02.2025 " + "О гарантиях защиты вкладов в банках и страховании " * 10
    name = lexuz.safe_name(long_ru)
    ok("имя с «.html» не длиннее 200 байт UTF-8", len((name + ".html").encode("utf-8")) <= 200,
       len((name + ".html").encode("utf-8")))
    ok("имя — начало исходного, без обрезанных букв", long_ru.startswith(name) and "�" not in name, name)
    emoji = "Акт " + "😀" * 100
    cut = lexuz.safe_name(emoji, limit=500)
    ok("4-байтовые знаки режутся целиком", len(cut.encode("utf-8")) <= 195 and cut.encode("utf-8").decode("utf-8") == cut)
    nm, _g = lexuz.act_file_name({"badge": "Постановление Кабинета Министров, от 01.01.2024 г. № 1",
                                  "number": "1", "date": "01.01.2024", "title": long_ru}, "uz")
    ok("узбекское имя сохраняет пометку « (uz)» и укладывается в 200 байт",
       nm.endswith(" (uz)") and len((nm + ".html").encode("utf-8")) <= 200, nm)
    p15 = lexuz.save_act("<html>lex.uz</html>", "https://lex.uz/ru/docs/1", long_ru, "99_тест", lib=tmp / "names")
    ok("save_act создаёт файл с таким именем", p15.exists() and len(p15.name.encode("utf-8")) <= 200)

    print("\n16. (Б5) личный предел живого поиска")
    os.environ["LEX_LIVE_PER_USER_HOUR"] = "1"
    try:
        fake16 = fresh(FakeLex(search="lex_search_urozhay.html"))
        a = legal_live.lookup(Q_SHELL, "ru", who="g:guest-one-000000000000000")
        with db.tx() as con:
            con.execute("DELETE FROM lex_live_cache")
        n_before = len(fake16.calls)
        b = legal_live.lookup(Q_SHELL, "ru", who="g:guest-one-000000000000000")
        ok("первый живой поиск гостя выполнен", a["status"] != "limit" and n_before > 0, a.get("status"))
        ok("второй у того же гостя — status limit, личный, на сайт не ходим",
           b["status"] == "limit" and b.get("personal") and len(fake16.calls) == n_before, b)
        ok("в тексте сказано про личный предел", "личный" in (b.get("text") or ""), b.get("text"))
        c16 = legal_live.lookup(Q_SHELL, "ru", who="g:guest-two-000000000000000")
        ok("другой гость в пределе не ограничен", c16["status"] != "limit", c16.get("status"))
        ok("в памяти не хранится guest_id в открытом виде",
           not any("guest-one" in k for k in legal_live._user_hits), list(legal_live._user_hits))
        ok("общий предел остаётся", legal_live.per_hour() == 60)
    finally:
        os.environ.pop("LEX_LIVE_PER_USER_HOUR", None)
    ok("по умолчанию личный предел 10 в час", legal_live.per_user_hour() == 10)
    try:
        from starlette.requests import Request
        req = Request({"type": "http", "headers": [], "client": ("10.1.2.3", 5555),
                       "surveyor_guest": "abcdefghijklmnopqrstuvwx"})
        req2 = Request({"type": "http", "headers": [], "client": ("10.1.2.3", 5555)})
        ok("/legal/ask: ключ гостя — g:<guest_id>, без cookie — адрес",
           legal._who(req) == "g:abcdefghijklmnopqrstuvwx" and legal._who(req2) == "ip:10.1.2.3",
           (legal._who(req), legal._who(req2)))
    except ImportError:
        ok("starlette есть в окружении", False)

    print("\n17. (Б6) сроки и пределы чтения")
    lexuz._http = ORIG_HTTP

    class Resp:
        def __init__(self, chunks, headers=None, delay=0.0):
            self.chunks, self.headers, self.delay = list(chunks), headers or {}, delay
            self.status = 200

        def read(self, n=-1):
            if self.delay:
                time.sleep(self.delay)
            return self.chunks.pop(0) if self.chunks else b""

        def geturl(self):
            return "https://lex.uz/ru/docs/1"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Opener:
        def __init__(self, resp):
            self.resp = resp

        def open(self, req, timeout=None):
            return self.resp

    try:
        lexuz._opener = Opener(Resp([b"x" * (1024 * 1024)] * 6))
        try:
            lexuz._http("https://lex.uz/ru/docs/1", 5)
            ok("ответ больше 5 МБ прерывается", False)
        except lexuz.LexUnavailable as e:
            ok("ответ больше 5 МБ прерывается", "5 МБ" in e.reason, e.reason)
        bomb = gzip.compress(b"\0" * (30 * 1024 * 1024))
        lexuz._opener = Opener(Resp([bomb], {"Content-Encoding": "gzip"}))
        try:
            lexuz._http("https://lex.uz/ru/docs/1", 5)
            ok("gzip-бомба (30 МБ нулей) прерывается", False)
        except lexuz.LexUnavailable as e:
            ok(f"gzip-бомба ({len(bomb)} байт → 30 МБ) прерывается", "распакованный" in e.reason, e.reason)
        lexuz._opener = Opener(Resp([gzip.compress("акт".encode("utf-8"))], {"Content-Encoding": "gzip"}))
        ok("обычный gzip читается", lexuz._http("https://lex.uz/ru/docs/1", 5)[2].decode("utf-8") == "акт")
        lexuz._opener = Opener(Resp([b"a"] * 1000, delay=0.1))     # сервер «капает» по байту
        t = time.monotonic()
        try:
            lexuz._http("https://lex.uz/ru/docs/1", 0.5)
            ok("медленный ответ обрывается по общему сроку", False)
        except TimeoutError:
            took = time.monotonic() - t
            ok("медленный ответ обрывается по общему сроку (0,5 с), а не по сокету", took < 1.0, round(took, 2))
    finally:
        lexuz._opener = ORIG_OPENER
    ok("срок живого поиска на вопрос — 15 с", legal_live.QUESTION_BUDGET_SEC == 15)
    legal_live.QUESTION_BUDGET_SEC = 0.8
    try:
        fake17 = fresh(FakeLex(delay=0.5, search="lex_search_urozhay.html"))
        t = time.monotonic()
        r17 = legal.ask("Какие льготы по страхованию экспорта?", "ru")
        took = time.monotonic() - t
        lv = r17.get("live") or {}
        ok("не уложились в срок вопроса — unavailable", lv.get("status") == "unavailable", lv)
        ok("ответ честный: «lex.uz сейчас недоступен», ответ по базе",
           "lex.uz сейчас недоступен" in r17["answer"]["text"] and r17["answer"]["source"] == "none")
        ok("срок вопроса соблюдён", took < 0.8 + 0.7, round(took, 2))
        fresh(FakeLex(delay=5))
        t = time.monotonic()
        r17b = legal_live.lookup("Какие льготы по страхованию экспорта?", "ru")
        took = time.monotonic() - t
        ok("сайт молчит — обрыв в пределах срока вопроса", r17b["status"] == "unavailable" and took < 1.5,
           (r17b.get("status"), round(took, 2)))
    finally:
        legal_live.QUESTION_BUDGET_SEC = 15

    print("\n18. (Б7) robots.txt")
    for mode, title in (("redirect404", "302 на /Pages/404.aspx (HTML)"), ("html200", "HTML вместо файла"),
                        (None, "404")):
        fresh(FakeLex(robots=mode))
        try:
            lexuz.robots_allowed("https://lex.uz/ru/search/nat?status=Y", 8)
            ok(f"robots: {title} — «файла нет, ограничений нет»", True)
        except lexuz.LexUnavailable as e:
            ok(f"robots: {title} — «файла нет, ограничений нет»", False, e.reason)
    fake18 = fresh(FakeLex(robots="redirect404", search="lex_search_urozhay.html"))
    r18 = legal_live.lookup(Q_SHELL, "ru")
    ok("при редиректе robots на 404 живой поиск идёт на сайт", r18["status"] != "unavailable"
       and fake18.lex_calls(), (r18.get("status"), r18.get("reason")))
    fresh(FakeLex(robots="User-agent: *\nDisallow: /ru/search/\n"))
    try:
        lexuz.robots_allowed("https://lex.uz/ru/search/nat?status=Y", 8)
        ok("настоящий robots.txt с запретом соблюдается", False)
    except lexuz.LexUnavailable as e:
        ok("настоящий robots.txt с запретом соблюдается", "запрещает" in e.reason, e.reason)
    lexuz.robots_allowed("https://lex.uz/ru/docs/7389439", 8)
    ok("и разрешённое им по-прежнему разрешено", True)
    fresh(FakeLex(robots="403"))
    try:
        lexuz.robots_allowed("https://lex.uz/ru/docs/1", 8)
        ok("robots.txt 403 — на сайт не идём", False)
    except lexuz.LexUnavailable:
        ok("robots.txt 403 — на сайт не идём", True)

    print("\n19. (Б8) пометка для en; is_lex только https")
    ok("en: про английский текст", legal_live.UNOFFICIAL["en"].startswith("The English text on lex.uz is an "
                                                                            "unofficial translation"))
    fresh(FakeLex())
    live = legal_live.lookup(Q_FOUND, "ru")
    out = legal._live_answer(Q_FOUND, "en", False, live, [], time.time()) if live.get("passages") else None
    ok("в note ответа на en нет слов «Russian text»", out is None or "Russian text" not in (out.get("note") or ""),
       out and out.get("note"))
    ok("is_lex: http:// отклоняется", not lexuz.is_lex("http://lex.uz/ru/docs/1"))
    ok("is_lex: https://lex.uz — да", lexuz.is_lex("https://lex.uz/ru/docs/1"))
    ok("is_lex: чужой хост с lex.uz в имени пользователя — нет", not lexuz.is_lex("https://lex.uz@evil.example/"))
    ok("is_lex: lex.uz.evil — нет", not lexuz.is_lex("https://lex.uz.evil.example/"))


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
