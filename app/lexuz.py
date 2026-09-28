"""
lex.uz — Национальная база данных законодательства: общий сетевой слой и разбор страниц.

Одним модулем пользуются:
  * tools/lex_fetch.py        — ручная загрузка акта в библиотеку (юрист, администратор);
  * app/legal_live.py         — живой поиск «ИИ специалиста» (решение заказчика 28.09.2026).

Как устроен сайт (проверено 28.09.2026 обращением к lex.uz):
  * robots.txt нет: https://lex.uz/robots.txt → 302 на /Pages/404.aspx (HTML-страница «не найдено»).
    По RFC 9309 (п. 2.3.1.3) отсутствие robots.txt означает «ограничений нет»: редирект на 404
    и HTML вместо файла правил трактуются именно так. Файл всё равно читается раз в сутки:
    появится настоящий robots.txt с запретом — поиск остановится сам;
  * поиск — обычная страница: /ru/search/nat?status=Y&lang=1&query=<слова>
    (status=Y — только действующие акты; lang: 1 — рус, 4 — узб. латиница, 3 — узб. кириллица,
    2 — англ.). Поиск по точным словоформам и по порядку слов («страхование урожая» находит,
    «урожая страхование» — другое), выдача упорядочена по дате, а не по смыслу;
  * карточка результата: <tr class="dd-table__main-item">, значок статуса status_code_y|r|n
    (действует | утратил силу | не действует), ссылка /ru/docs/<номер>, плашка
    «Закон Республики Узбекистан, от 18.02.2025 г. № ЗРУ-1031»;
  * страница акта отдаёт полный текст сразу (элементы ACT_TEXT, контейнер divCont);
  * на странице акта есть reCAPTCHA формы «предложение по документу» — поэтому капчу
    распознаём не по слову «captcha», а по отсутствию ожидаемой разметки.

Правила обращения (жёстко, см. задание 28.09.2026):
  * только lex.uz — любой другой адрес отклоняется до запроса;
  * честный User-Agent с названием системы, без маскировки под браузер;
  * не чаще 1 запроса в секунду на весь процесс; таймаут задаёт вызывающий (живой поиск — 8 с)
    и это срок на ВЕСЬ запрос (очередь, пауза, robots.txt, соединение, чтение), а не на одну
    операцию сокета;
  * ответ читается не больше MAX_BODY_BYTES (5 МБ), распакованный gzip — не больше
    MAX_UNZIPPED_BYTES: больше — это не страница акта, чтение прерывается;
  * только https;
  * 401/403/429/5xx, капча, редирект на чужой сайт, запрет robots.txt — исключение
    LexUnavailable; повторных попыток и обходов нет.
"""
import hashlib
import hmac
import html as htmllib
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib
from datetime import date
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library" / "01_Законодательство"

HOST = "lex.uz"
BASE = "https://lex.uz"
CONTACT_EMAIL = "j.mirzaumarov@insuranceon.uz"
# заголовки HTTP — только латиница: поэтому имя системы по-английски
USER_AGENT = ("INSON-Surveyor/1.0 (AI insurance specialist of INSON insurance company, Uzbekistan; "
              "looks up legal acts on lex.uz; contact: %s)" % CONTACT_EMAIL)

MIN_INTERVAL_SEC = 1.0          # не чаще одного запроса в секунду
ROBOTS_TTL_SEC = 24 * 3600
TEXT_MIN_CHARS = 3000           # короче — это не текст акта (проверка tools/lex_fetch.py)
MAX_BODY_BYTES = 5 * 1024 * 1024         # столько читаем из сети, не больше
MAX_UNZIPPED_BYTES = 20 * 1024 * 1024    # и столько после распаковки gzip (защита от «бомбы»)
READ_CHUNK = 64 * 1024
MIN_LEFT_SEC = 0.3              # меньше времени осталось — запрос не начинаем

# язык вопроса → раздел сайта и код языка текста в поиске
SEARCH_LANG = {"ru": ("ru", 1), "uz": ("uz", 4), "en": ("ru", 2)}


class LexUnavailable(Exception):
    """Сайт не ответил, отказал, показал капчу или robots.txt запрещает. Обходить нельзя."""

    def __init__(self, reason: str, cooldown: bool = True):
        super().__init__(reason)
        self.reason = reason
        # False — сайт тут ни при чём (не хватило времени до начала запроса, очередь):
        # паузу на всех пользователей из-за этого не включаем
        self.cooldown = cooldown


class LexNotFound(Exception):
    """Сайт ответил, но такой страницы нет (404) — это не недоступность сайта."""


# --------------------------------------------------------------------------- #
#  Сеть
# --------------------------------------------------------------------------- #

_net_lock = threading.Lock()
_last = {"t": 0.0}
_robots = {"rp": None, "at": 0.0, "reason": ""}
_SSL = ssl.create_default_context()


def is_lex(url: str) -> bool:
    """Адрес на lex.uz и только по https."""
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme.lower() != "https":
        return False
    host = (parts.hostname or "").lower()
    return host == HOST or host.endswith("." + HOST)


class _NoForeignRedirect(urllib.request.HTTPRedirectHandler):
    """Редирект разрешён только внутри lex.uz: источник нормы — только этот сайт."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_lex(urllib.parse.urljoin(req.full_url, newurl)):
            raise LexUnavailable("lex.uz перенаправил на другой сайт — переход не выполняется")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_NoForeignRedirect, urllib.request.HTTPSHandler(context=_SSL))


def _read_limited(r, deadline: float) -> bytes:
    """Тело ответа кусками: не больше MAX_BODY_BYTES и не дольше общего срока."""
    buf = bytearray()
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("общий срок запроса истёк во время чтения")
        chunk = r.read(READ_CHUNK)
        if not chunk:
            return bytes(buf)
        buf += chunk
        if len(buf) > MAX_BODY_BYTES:
            raise LexUnavailable("lex.uz отдал ответ больше 5 МБ — чтение прервано")


def _gunzip(raw: bytes) -> bytes:
    """gzip с пределом распакованного размера."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = d.decompress(raw, MAX_UNZIPPED_BYTES + 1)
    except zlib.error:
        raise LexUnavailable("lex.uz отдал повреждённый gzip")
    if len(out) > MAX_UNZIPPED_BYTES or d.unconsumed_tail:
        raise LexUnavailable("распакованный ответ lex.uz больше %d МБ — чтение прервано"
                             % (MAX_UNZIPPED_BYTES // (1024 * 1024)))
    return out


def _http_once(url: str, deadline: float) -> tuple:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,uz;q=0.8",
        "Accept-Encoding": "gzip",
        "From": CONTACT_EMAIL,
    })
    left = max(0.1, deadline - time.monotonic())
    try:
        with _opener.open(req, timeout=left) as r:
            raw = _read_limited(r, deadline)
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = _gunzip(raw)
            return r.status, r.geturl(), raw
    except urllib.error.HTTPError as e:
        final = e.geturl() if hasattr(e, "geturl") else url
        e.close()
        return e.code, final or url, b""


def _http(url: str, timeout: float) -> tuple:
    """Один GET. Возвращает (код, конечный адрес, байты). Тесты подменяют эту функцию образцами.

    timeout — срок на весь запрос. Таймаут сокета в urllib действует на каждую операцию отдельно
    (медленный сервер может тянуть ответ сколько угодно), поэтому запрос идёт в отдельном потоке,
    а вызывающий ждёт его не дольше timeout. Поток сам остановится на следующем куске чтения.
    """
    deadline = time.monotonic() + timeout
    box = {}

    def work():
        try:
            box["res"] = _http_once(url, deadline)
        except BaseException as e:               # отдаём вызывающему как есть
            box["err"] = e

    t = threading.Thread(target=work, name="lexuz-http", daemon=True)
    t.start()
    t.join(max(0.0, deadline - time.monotonic()) + 0.05)
    if t.is_alive():
        raise TimeoutError("lex.uz не уложился в %.0f с" % timeout)
    if "err" in box:
        raise box["err"]
    return box["res"]


def _left(deadline: float) -> float:
    return deadline - time.monotonic()


def _throttle(deadline: float = None):
    wait = MIN_INTERVAL_SEC - (time.monotonic() - _last["t"])
    if wait > 0:
        if deadline is not None and _left(deadline) - wait < MIN_LEFT_SEC:
            raise LexUnavailable("не хватило времени дождаться очереди к lex.uz", cooldown=False)
        time.sleep(wait)
    _last["t"] = time.monotonic()


def decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _get(url: str, timeout: float, deadline: float = None) -> tuple:
    """GET с паузой. Сетевые сбои → LexUnavailable. Возвращает (код, конечный адрес, текст).

    deadline (time.monotonic) — общий срок: ожидание очереди и пауза в него входят."""
    if not is_lex(url):
        raise LexUnavailable("адрес не на lex.uz — другие сайты источником нормы не считаются")
    deadline = deadline if deadline is not None else time.monotonic() + timeout
    if not _net_lock.acquire(timeout=max(0.0, _left(deadline) - MIN_LEFT_SEC)):
        raise LexUnavailable("не хватило времени дождаться очереди к lex.uz", cooldown=False)
    try:
        _throttle(deadline)
        left = min(timeout, _left(deadline))
        if left < MIN_LEFT_SEC:
            raise LexUnavailable("не хватило времени на запрос к lex.uz", cooldown=False)
        try:
            code, final, raw = _http(url, left)
        except LexUnavailable:
            raise
        except Exception as e:                      # таймаут, TLS, обрыв соединения
            raise LexUnavailable(f"lex.uz не ответил ({type(e).__name__})")
    finally:
        _net_lock.release()
    if final and not is_lex(final):
        raise LexUnavailable("lex.uz перенаправил на другой сайт — переход не выполняется")
    return code, final or url, decode(raw or b"")


def _no_robots_file(code: int, final: str, text: str) -> bool:
    """Ответ означает «файла robots.txt нет»: 404 и прочие 4xx (кроме отказов), редирект на
    страницу «не найдено» (/Pages/404.aspx — так на 28.09.2026 отвечает lex.uz) или HTML-страница
    вместо текстового файла правил."""
    if "/pages/404.aspx" in (final or "").lower():
        return True
    if 400 <= code < 500 and code not in (401, 403, 429):
        return True
    head = (text or "")[:1000].lstrip().lower()
    return code == 200 and (head.startswith("<") or "<html" in head or "<!doctype" in head)


def robots_allowed(url: str, timeout: float = 8, deadline: float = None) -> None:
    """Проверка robots.txt (кэш на сутки). Запрет или недоступность правил → LexUnavailable.

    На 28.09.2026 файла на сайте нет (302 на /Pages/404.aspx) — по RFC 9309 это «ограничений нет».
    Настоящий файл правил (200, текст) разбирается и запреты в нём соблюдаются. 401/403/429,
    5xx или молчание сервера — считаем, что запрещено всё, и на сайт не идём.
    """
    now = time.time()
    if _robots["rp"] is None or now - _robots["at"] > ROBOTS_TTL_SEC:
        code, final, text = _get(BASE + "/robots.txt", timeout, deadline)
        rp = urllib.robotparser.RobotFileParser()
        if _no_robots_file(code, final, text):
            rp.parse([])                              # файла нет — ограничений нет
        elif code == 200:
            rp.parse(text.splitlines())
        else:
            raise LexUnavailable(f"robots.txt lex.uz недоступен (HTTP {code}) — на сайт не идём")
        _robots.update({"rp": rp, "at": now})
    if not (_robots["rp"].can_fetch(USER_AGENT, url) and _robots["rp"].can_fetch("*", url)):
        raise LexUnavailable("robots.txt lex.uz запрещает автоматическую загрузку этой страницы")


def fetch(url: str, timeout: float = 8, expect: str = "act") -> str:
    """Страница lex.uz. expect: 'act' — страница акта, 'search' — выдача поиска.

    timeout — срок на всё обращение вместе с проверкой robots.txt и паузой.
    Проверяет robots.txt, код ответа и то, что пришла именно ожидаемая страница, а не
    проверка браузера. Ошибки: LexUnavailable (сайт недоступен), LexNotFound (нет страницы).
    """
    if not is_lex(url):
        raise LexUnavailable("адрес не на lex.uz — другие сайты источником нормы не считаются")
    deadline = time.monotonic() + timeout
    robots_allowed(url, timeout, deadline)
    code, final, page = _get(url, timeout, deadline)
    if code in (401, 403, 429):
        raise LexUnavailable(f"lex.uz отказал в доступе (HTTP {code}) — обходить защиту нельзя")
    if code == 404 or "/pages/404.aspx" in (final or "").lower():
        raise LexNotFound(url)
    if code >= 500 or code == 0:
        raise LexUnavailable(f"lex.uz не отвечает (HTTP {code})")
    if code != 200:
        raise LexUnavailable(f"lex.uz ответил неожиданно (HTTP {code})")
    if not page_ok(page, expect):
        low = page.lower()
        if "captcha" in low or "challenge" in low or "проверка браузера" in low:
            raise LexUnavailable("вместо страницы lex.uz показал проверку (капчу) — обходить нельзя")
        raise LexUnavailable("lex.uz отдал страницу без ожидаемого содержания")
    return page


def page_ok(page: str, expect: str) -> bool:
    """Ожидаемая разметка есть: выдача поиска (или «ничего не найдено») / текст акта."""
    if expect == "search":
        return "dd-table__main-item" in page or "refindHeader__notFound" in page
    if expect == "act":
        return "ACT_TEXT" in page or 'id="divCont"' in page
    return "lex.uz" in (page or "").lower()          # 'any': ручная загрузка любой страницы акта


# --------------------------------------------------------------------------- #
#  Адреса и текст акта
# --------------------------------------------------------------------------- #

def to_url(arg: str) -> str:
    """Номер документа или адрес → адрес страницы акта."""
    arg = (arg or "").strip()
    if arg.startswith("http"):
        return arg
    return f"{BASE}/ru/docs/{arg}"


_DOC_RE = re.compile(r"/docs/(-?\d+)")


def doc_id(url: str) -> Optional[str]:
    """Номер документа из адреса (с дефисом у узбекской латиницы: /uz/docs/-180552)."""
    m = _DOC_RE.search(url or "")
    return m.group(1) if m else None


def clean_doc_url(url: str) -> str:
    """Адрес страницы акта без параметров поиска и якоря: https://lex.uz/ru/docs/7389439."""
    full = urllib.parse.urljoin(BASE, url or "")
    parts = urllib.parse.urlsplit(full)
    return urllib.parse.urlunsplit((parts.scheme or "https", parts.netloc, parts.path, "", ""))


def extract_text(page: str) -> str:
    """HTML страницы → текст (тот же способ, что был в tools/lex_fetch.py: библиотека единообразна)."""
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    page = re.sub(r"<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>", "\n", page, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", page)
    text = htmllib.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def looks_like_act(page: str, text: str) -> bool:
    return len(text) >= TEXT_MIN_CHARS and "lex.uz" in (page or "").lower()


SHELL_TEXT_MAX = 8000


def is_shell(page: str, text: str = None) -> bool:
    """Страница без текста акта на этом языке: одни реквизиты и пометка «Текст акта приводится
    на узбекском языке» (блок COMMENT_FOR_WARNING, элементов ACT_TEXT нет). Так на lex.uz выглядят
    русские страницы многих постановлений и положений: официальный текст — только узбекский."""
    page = page or ""
    if "ACT_TEXT" in page:
        return False
    text = extract_text(page) if text is None else text
    return "COMMENT_FOR_WARNING" in page and len(text) < SHELL_TEXT_MAX


_LANG_LINK_RE = re.compile(r"openUrl\('(/[a-z]{2}/docs/-?\d+)'\)\"\s+title=\"([^\"]+)\"")


def lang_links(page: str) -> dict:
    """Ссылки на другие языковые версии акта со страницы: {'uz': латиница, 'uz_cyrl', 'en'}.

    На lex.uz у каждой версии свой номер: /ru/docs/-6993258 — узбекская латиница,
    /ru/docs/6993258 — кириллица, отдельный номер — английский перевод.
    """
    out = {}
    for path, title in _LANG_LINK_RE.findall(page or ""):
        t = title.lower()
        if "english" in t:
            key = "en"
        elif "ўзбек" in t:
            key = "uz_cyrl"
        elif "zbek" in t:
            key = "uz"
        else:
            continue
        out.setdefault(key, BASE + path)
    return out


def redaction_marks(text: str, limit: int = 5) -> list:
    """Признаки редакции — чтобы было видно, что скачан действующий текст."""
    return [m.strip()[:140] for m in re.findall(
        r"(в редакции[^\n]{0,120}|редакци[яи][^\n]{0,120}|утратил[а]? силу[^\n]{0,80})",
        text, flags=re.I)[:limit]]


NAME_MAX_BYTES = 200            # имя файла вместе с расширением, в байтах UTF-8
EXT_MAX_BYTES = 5               # «.html» — самое длинное расширение, которое пишет save_act


def _cut_bytes(s: str, max_bytes: int) -> str:
    """Не длиннее max_bytes в UTF-8; режем по границе символа, а не посреди буквы."""
    b = s.encode("utf-8")
    if len(b) <= max_bytes:
        return s
    return b[:max_bytes].decode("utf-8", "ignore")


def safe_name(name: str, limit: int = 140, max_bytes: int = NAME_MAX_BYTES - EXT_MAX_BYTES) -> str:
    """Имя файла без запрещённых в Windows знаков, не длиннее limit знаков (путь Windows < 260)
    и max_bytes байт UTF-8 (Linux: 255 байт на имя; кириллица — 2 байта на букву).
    По умолчанию имя с расширением «.html»/«.txt» укладывается в 200 байт."""
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", name or "")
    safe = re.sub(r"\s+", " ", safe).strip(" .")
    if len(safe) > limit or len(safe.encode("utf-8")) > max_bytes:
        cut = _cut_bytes(safe[:limit], max_bytes)
        word = cut.rsplit(" ", 1)[0] if " " in cut else cut
        safe = word.rstrip(" ,.;—-") or cut.rstrip(" ,.;—-")
    return safe or "акт lex.uz"


def save_act(page: str, url: str, name: str, group: str = "01_Законы", lib: Path = None,
             note: str = "") -> Path:
    """Кладёт акт в библиотеку: оригинал .html и текст .txt с шапкой «Источник: …».

    Возвращает путь к .txt. Шапку читает app/legal.py (ссылка на источник) и в индекс её
    не берёт. note — пометка к дате загрузки («живой поиск ИИ специалиста»).
    """
    if not is_lex(url):
        raise ValueError("Источник должен быть lex.uz — другие сайты не считаются официальными.")
    dest = Path(lib or LIB) / group
    dest.mkdir(parents=True, exist_ok=True)
    safe = safe_name(name)
    text = extract_text(page)
    (dest / f"{safe}.html").write_text(page, encoding="utf-8")
    loaded = date.today().isoformat() + (f" ({note})" if note else "")
    header = f"Источник: {url}\nЗагружено: {loaded}\n\n"
    path = dest / f"{safe}.txt"
    path.write_text(header + text, encoding="utf-8")
    return path


def library_doc_ids(lib: Path = None) -> dict:
    """{номер документа lex.uz: путь .txt} по шапкам «Источник: …» файлов библиотеки."""
    out = {}
    root = Path(lib or LIB)
    if not root.exists():
        return out
    for p in root.rglob("*.txt"):
        try:
            with p.open("r", encoding="utf-8", errors="replace") as f:
                head = f.read(400)
        except OSError:
            continue
        m = re.search(r"Источник:\s*(https?://\S+)", head)
        if m and is_lex(m.group(1)):
            d = doc_id(m.group(1))
            if d:
                out.setdefault(d.lstrip("-"), p)
    return out


# --------------------------------------------------------------------------- #
#  Поиск на lex.uz
# --------------------------------------------------------------------------- #

def search_url(query: str, lang: str, field: str = "searchtitle") -> str:
    """Адрес выдачи: только действующие акты (status=Y), текст на языке вопроса.

    field: 'searchtitle' — поиск по названию акта (учитывает словоформы и не зависит от порядка
    слов: «урожая страхование» находит «…при страховании урожая посевов»); 'query' — по тексту
    (точные словоформы, порядок слов важен). Живой поиск использует поиск по названию.
    """
    seg, code = SEARCH_LANG.get(lang, SEARCH_LANG["ru"])
    field = field if field in ("searchtitle", "query") else "searchtitle"
    q = urllib.parse.quote(re.sub(r"\s+", " ", query or "").strip()[:100])
    return f"{BASE}/{seg}/search/nat?status=Y&lang={code}&{field}={q}"


_ROW_RE = re.compile(r'<tr class="dd-table__main-item">(.*?)</tr>', re.S)
_LINK_RE = re.compile(r'<a class="lx_link\s*"\s+href="([^"]*?/docs/-?\d+[^"]*)"[^>]*>(.*?)</a>', re.S)
_STATE_RE = re.compile(r"status_code_([a-z])", re.I)
_BADGE_RE = re.compile(r'badge-nine">([^<]*)<')
_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")
_NUM_RE = re.compile(r"№\s*([^\s,]+)|(\S+)-son\b")


def _plain(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def parse_search(page: str) -> list:
    """Выдача поиска → [{doc_id, url, title, badge, state, date, number}]. Порядок — как на сайте."""
    out = []
    for block in _ROW_RE.findall(page or ""):
        link = _LINK_RE.search(block)
        if not link:
            continue
        url = clean_doc_url(link.group(1))
        st = _STATE_RE.search(block)
        b = _BADGE_RE.search(block)
        badge = _plain(b.group(1)) if b else ""
        d = _DATE_RE.search(badge)
        n = _NUM_RE.search(badge)
        out.append({"doc_id": doc_id(url), "url": url, "title": _plain(link.group(2)),
                    "badge": badge, "state": (st.group(1).upper() if st else ""),
                    "date": d.group(1) if d else "",
                    "number": ((n.group(1) or n.group(2)) if n else "").strip(".")})
    return out


def not_found_page(page: str) -> bool:
    return "refindHeader__notFound" in (page or "")


def act_file_name(item: dict, lang: str = "ru") -> tuple:
    """Имя файла и группа библиотеки по карточке выдачи: «ЗРУ-1031 от 18.02.2025 О гарантиях …».

    Реквизиты берутся только из плашки lex.uz — ничего не додумываем. Закон и кодекс —
    в 01_Законы, остальное — в 02_Акты_регуляторов (как раскладывает tools/lex_fetch.py).
    """
    badge = item.get("badge") or ""
    low = badge.lower()
    num, dt, title = item.get("number") or "", item.get("date") or "", item.get("title") or ""
    if "кабинета министров" in low or "vazirlar mahkamasi" in low:
        code = f"ПКМ № {num}" if num else "ПКМ"
    elif num and re.match(r"^[A-Za-zА-Яа-яЁёʻ‘'-]+-\d", num):
        code = num                                   # ЗРУ-1031, ПП-108, УП-164, OʻRQ-…
    elif num:
        code = (badge.split(",")[0].strip()[:40] + f" № {num}").strip()
    else:
        code = badge.split(",")[0].strip()[:40]
    name = " ".join(x for x in (code, f"от {dt}" if dt else "", title) if x)
    if lang == "uz":
        # место под « (uz)» оставляем заранее, иначе общий предел длины срезал бы пометку языка
        name = safe_name(name, 130, NAME_MAX_BYTES - EXT_MAX_BYTES - len(" (uz)")) + " (uz)"
    law = ("закон" in low or "кодекс" in low or "qonun" in low or "kodeks" in low)
    return safe_name(name), ("01_Законы" if law else "02_Акты_регуляторов")


def cache_key(*parts, salt: bytes = b"") -> str:
    """Ключ кэша. С солью сервера (salt) — HMAC: по ключу нельзя перебором восстановить слова
    запроса, не зная соли."""
    msg = "|".join(str(p) for p in parts).encode("utf-8")
    if salt:
        return hmac.new(salt, msg, hashlib.sha256).hexdigest()[:40]
    return hashlib.sha256(msg).hexdigest()[:40]
