"""
Живой поиск «ИИ специалиста» на lex.uz (решение заказчика 28.09.2026: «ИИ специалиста подключи в lex.uz»).

Когда работает. Только если локальная база (FAQ, известные молчания закона, индекс актов) ответа
не дала — app/legal.ask зовёт lookup() в ветке «нормы нет». Обычные ответы не замедляются.

Что делает за один вопрос (не больше MAX_PAGES = 3 страниц lex.uz):
  1. Вопрос пропускается через маскировку ПД (app/llm.mask_pd), из него берутся 1–2 ключевых слова.
     На сайт уходят ТОЛЬКО слова из словаря законодательства (разрешённый список, а не чёрный):
     индекс актов legal_chunks, FAQ специалиста, словарь терминов docs/i18n_terms.json, названия
     отслеживаемых актов. Слово вне словаря не отправляется никогда. Даже словарное слово
     отбрасывается, если стоит рядом с «клиент», «страхователь», «ООО», «ул.», «MChJ», «mijoz»…,
     написано с заглавной буквы не в начале предложения или похоже на фамилию (-ов, -ева, -ский,
     -ov, -yeva…), если это не термин из FAQ/словаря. Из оставшихся — самое редкое для базы,
     при наличии — вместе со словом «страхование». Ничего не осталось — на сайт не идём
     (status no_keywords). Полный текст вопроса на сайт не уходит.
  2. Поиск по названию действующих актов: /ru/search/nat?status=Y&lang=1&searchtitle=<слова>.
     Пусто — второй поиск по одному самому редкому слову.
  3. Из выдачи отбрасываются изменяющие акты, проекты, бюджеты, повестки; лучший по названию акт
     скачивается, режется на статьи/пункты тем же разбором, что библиотека (legal.split_units),
     и в нём ищется статья, где встречаются слова вопроса. Порог — как у локального поиска.
  4. Нашлось — акт сохраняется тем же способом, что tools/lex_fetch.py (app/lexuz.save_act):
     на сервере с постоянным диском — в STORAGE_DIR/library_live (legal.live_lib), иначе в
     библиотеку; индекс дособирается только по новому файлу, каталог библиотеки не трогается.
     Цитата берётся legal._citation и сверяется дословно с сохранённым файлом (legal.verbatim).
     Следующий такой вопрос отвечается из базы.

Ограничения:
  * источник — только lex.uz (app/lexuz отклоняет другие адреса и редиректы на них);
  * robots.txt, честный User-Agent, 1 запрос в секунду, срок 8 с на запрос (app/lexuz);
  * срок на весь живой поиск по одному вопросу — QUESTION_BUDGET_SEC (15 с); не уложились —
    «lex.uz сейчас недоступен» и ответ по базе;
  * общий предел живых обращений на сервер — LEX_LIVE_PER_HOUR в час (по умолчанию 60) и личный
    предел живых поисков на одного пользователя или гостя — LEX_LIVE_PER_USER_HOUR (по умолчанию 10);
  * отказ, капча, таймаут → «lex.uz сейчас недоступен», ответ по локальной базе, повторные
    попытки не делаются COOLDOWN_SEC секунд;
  * выдача поиска и страницы актов кэшируются в таблице lex_live_cache на 24 часа; ключ —
    HMAC с солью сервера (LEX_CACHE_SALT или случайная соль в app_settings), адрес выдачи
    поиска не хранится;
  * выключатель — переменная окружения LEX_LIVE (по умолчанию включено; 0/false/off — выключено).
"""
import bisect
import json
import os
import re
import secrets
import threading
import time
import zlib
from collections import deque
from pathlib import Path
from typing import Optional

from . import db, lexuz, llm

MAX_PAGES = 3                    # страниц lex.uz на один вопрос (поиск + акты)
TIMEOUT_SEC = 8                  # срок одного запроса
QUESTION_BUDGET_SEC = 15         # срок всего живого поиска по одному вопросу
CACHE_TTL_SEC = 24 * 3600        # кэш выдачи и страниц
COOLDOWN_SEC = 300               # после отказа сайта живой поиск молчит 5 минут
PER_HOUR_DEFAULT = 60            # общий предел обращений к lex.uz в час на весь сервер
PER_USER_HOUR_DEFAULT = 10       # живых поисков в час на одного пользователя или гостя
CACHE_MAX_BYTES = 4 * 1024 * 1024
NOT_FOUND_MARK = "\x00lex.uz: 404\x00"   # в кэше: страницы нет
WHO = "ИИ специалист"
NOTE_LIVE = "живой поиск ИИ специалиста"

# Пороги принятия статьи из найденного акта — те же, что у локального поиска (app/legal):
# доля значимых слов вопроса в статье и доля их «веса» (редкие слова важнее общих)
KEY_SHARE_MIN = 0.40
MIN_WEIGHTED = 0.55

# подписи для экрана (мини-апп берёт их из ответа сервера: app/tg.html и app/i18n не трогаем)
LABEL = {"ru": "найдено на lex.uz сейчас", "uz": "hozir lex.uz saytida topildi",
         "en": "found on lex.uz just now"}
STATUS_TEXT = {
    "found": LABEL,
    "found_base": {"ru": "акт найден поиском на lex.uz ранее и уже лежит в базе",
                   "uz": "hujjat avval lex.uz qidiruvida topilgan va bazada bor",
                   "en": "the act was found on lex.uz earlier and is already in the database"},
    "not_found": {"ru": "на lex.uz подходящей действующей нормы тоже не найдено",
                  "uz": "lex.uz saytida ham mos amaldagi norma topilmadi",
                  "en": "no matching provision in force was found on lex.uz either"},
    "unavailable": {"ru": "lex.uz сейчас недоступен — ответ дан по локальной базе",
                    "uz": "lex.uz hozir mavjud emas — javob mahalliy baza asosida berildi",
                    "en": "lex.uz is unavailable right now — the answer is based on the local database"},
    "limit": {"ru": "живой поиск на lex.uz временно приостановлен: исчерпан часовой предел "
                    "обращений — ответ по локальной базе",
              "uz": "lex.uz boʻyicha jonli qidiruv vaqtincha toʻxtatildi: soatlik chegara tugadi — "
                    "javob mahalliy baza asosida",
              "en": "live lookup on lex.uz is paused: the hourly limit is used up — the answer is "
                    "based on the local database"},
    "limit_user": {"ru": "живой поиск на lex.uz для вас временно приостановлен: исчерпан личный "
                         "часовой предел — ответ по локальной базе",
                   "uz": "lex.uz boʻyicha jonli qidiruv siz uchun vaqtincha toʻxtatildi: shaxsiy soatlik "
                         "chegara tugadi — javob mahalliy baza asosida",
                   "en": "live lookup on lex.uz is paused for you: your hourly limit is used up — the "
                         "answer is based on the local database"},
    "off": {"ru": "живой поиск на lex.uz отключён администратором",
            "uz": "lex.uz boʻyicha jonli qidiruv administrator tomonidan oʻchirilgan",
            "en": "live lookup on lex.uz is turned off by the administrator"},
    "no_keywords": {"ru": "в вопросе нет слов, по которым можно искать на lex.uz",
                    "uz": "savolda lex.uz da qidirish uchun soʻz yoʻq",
                    "en": "the question has no words to search lex.uz with"},
}
SHELL_REASON = {
    "ru": "на lex.uz текст этого акта есть только на узбекском языке — цитату по-русски дать нельзя, "
          "откройте оригинал по ссылке",
    "uz": "bu hujjat matni lex.uz da faqat boshqa tilda mavjud",
    "en": "on lex.uz this act is available only in Uzbek — open the original by the link",
}
UNOFFICIAL = {
    "ru": "Русский текст на lex.uz — неофициальный перевод; официальный текст акта — на узбекском языке.",
    "uz": "Rus tilidagi matn — norasmiy tarjima; hujjatning rasmiy matni — oʻzbek tilida.",
    "en": "The English text on lex.uz is an unofficial translation; the official text is in Uzbek.",
}

# акты, которые норму по существу не содержат: изменяющие, проекты, бюджеты, повестки, награды
SKIP_TITLE = re.compile(
    r"^(?:о\s+внесении\s+(?:изменени|дополнени)|о\s+признании\s+утративш|о\s+проекте|о\s+повестке"
    r"|об\s+объединении\s+проектов|о\s+государственном\s+бюджете|об\s+учреждении\s+нагрудного"
    r"|о\s+широком\s+праздновании|о\s+прогнозе)"
    r"|(?:oʻzgartish|o'zgartish|qoʻshimcha(?:lar)?\s+kiritish|loyihasi|davlat\s+budjeti|kun\s+tartibi)",
    re.IGNORECASE)

# слово темы «страхование»: с ним поиск по названию акта точнее («страхование урожая»)
INSURANCE_PREFIX = ("страхов", "страхован", "перестрах", "sugʻurt", "sugurt", "sug'urt", "insur")
INSURANCE_NOUN = {"ru": "страхование", "uz": "sugʻurta", "en": "insurance"}

# вопросительные и служебные слова, которые не годятся в запрос к сайту
LIVE_STOP = {
    "сколько", "каков", "какова", "каковы", "какие", "каким", "какого", "какую", "каком", "кому",
    "чего", "чему", "который", "которая", "которые", "обязан", "обязана", "обязаны", "обязательно",
    "должен", "должна", "должны", "вправе", "может", "могут", "нужен", "нужна", "нужны", "будет",
    "также", "этом", "этого", "того", "тот", "эта", "это", "эти", "при", "про", "над", "под",
    "закон", "закона", "законом", "законе", "норма", "нормы", "статья", "статьи", "порядок",
    "установлен", "установлено", "предусмотрен", "предусмотрено", "вопрос", "случае", "случай",
    "qancha", "qaysi", "qanaqa", "kerakmi", "boʻladimi", "qonun", "modda", "tartib",
    "how", "much", "many", "what", "which", "law", "article", "rule", "must", "shall", "required",
}
# отчество — точно персональные данные, даже если написано со строчной буквы
PATRONYMIC = re.compile(r"(?:ович|евич|овна|евна|ична|ovich|evich|ovna|evna|oʻgʻli|ogli|qizi)$")

_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁёЎўҚқҒғҲҳʻʼ‘’'`]+")


def _setting(key: str, default: str) -> str:
    """Переменная окружения; нет её — файл .env / .secrets.env (так же читает настройки app/llm)."""
    v = os.environ.get(key)
    if v is None or not v.strip():
        try:
            v = llm._env_file().get(key)
        except Exception:
            v = None
    return (v or default).strip()


def enabled() -> bool:
    """Выключатель LEX_LIVE: по умолчанию включено; 0 / false / off — выключено."""
    return _setting("LEX_LIVE", "1").lower() not in ("0", "false", "no", "off", "нет", "выкл")


def _int_setting(key: str, default: int) -> int:
    try:
        return max(0, int(_setting(key, str(default))))
    except ValueError:
        return default


def per_hour() -> int:
    """LEX_LIVE_PER_HOUR — общий предел обращений к lex.uz в час на весь сервер (по умолчанию 60)."""
    return _int_setting("LEX_LIVE_PER_HOUR", PER_HOUR_DEFAULT)


def per_user_hour() -> int:
    """LEX_LIVE_PER_USER_HOUR — живых поисков в час на одного пользователя или гостя (по умолчанию 10).
    Гостям разрешено 200 вопросов в час (app/guest.py): без личного предела один гость мог бы
    выбрать весь общий предел сервера."""
    return _int_setting("LEX_LIVE_PER_USER_HOUR", PER_USER_HOUR_DEFAULT)


# --------------------------------------------------------------------------- #
#  Предел обращений и пауза после отказа
# --------------------------------------------------------------------------- #

_lock = threading.Lock()
_hits: deque = deque()
_user_hits: dict = {}            # отпечаток пользователя -> deque меток времени
_down = {"until": 0.0, "reason": ""}


def _take() -> bool:
    """Одно обращение к lex.uz из общего часового предела. False — предел исчерпан."""
    now = time.time()
    with _lock:
        while _hits and now - _hits[0] > 3600:
            _hits.popleft()
        if len(_hits) >= per_hour():
            return False
        _hits.append(now)
        return True


def _user_key(who: Optional[str]) -> Optional[str]:
    """В памяти держим не guest_id/адрес, а их отпечаток с солью сервера."""
    if not who:
        return None
    return lexuz.cache_key("user", who, salt=_salt())


def _take_user(key: Optional[str]) -> bool:
    """Один живой поиск из личного часового предела. Без ключа (внутренний вызов) — не считаем."""
    if not key:
        return True
    now = time.time()
    with _lock:
        for k in [k for k, q in _user_hits.items() if not q or now - q[-1] > 3600]:
            _user_hits.pop(k, None)              # старые ключи не копим
        q = _user_hits.setdefault(key, deque())
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= per_user_hour():
            return False
        q.append(now)
        return True


def usage() -> dict:
    now = time.time()
    with _lock:
        used = sum(1 for t in _hits if now - t <= 3600)
        users = sum(1 for q in _user_hits.values() if q and now - q[-1] <= 3600)
    return {"enabled": enabled(), "per_hour": per_hour(), "used_last_hour": used,
            "per_user_hour": per_user_hour(), "users_last_hour": users,
            "cooldown_until": _down["until"] if _down["until"] > now else None,
            "last_error": _down["reason"] or None}


def reset() -> None:
    """Сброс счётчиков (тесты, администратор)."""
    with _lock:
        _hits.clear()
        _user_hits.clear()
    _down.update({"until": 0.0, "reason": ""})


class _Limit(Exception):
    def __init__(self, personal: bool = False):
        super().__init__("limit")
        self.personal = personal


class _Budget(Exception):
    pass


# --------------------------------------------------------------------------- #
#  Кэш страниц: таблица lex_live_cache, 24 часа
# --------------------------------------------------------------------------- #

CACHE_SQL = """CREATE TABLE IF NOT EXISTS lex_live_cache (
    key        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    url        TEXT,
    created_at REAL NOT NULL,
    body       BLOB
)"""


def _cache_get(key: str) -> Optional[str]:
    try:
        with db.tx() as con:
            con.execute(CACHE_SQL)
            row = con.execute("SELECT created_at, body FROM lex_live_cache WHERE key=?", (key,)).fetchone()
    except Exception as e:
        print("legal_live: кэш не прочитан:", e)
        return None
    if not row or time.time() - row[0] > CACHE_TTL_SEC:
        return None
    try:
        return zlib.decompress(row[1]).decode("utf-8")
    except Exception:
        return None


def _cache_put(key: str, kind: str, url: Optional[str], page: str) -> None:
    body = zlib.compress(page.encode("utf-8"), 6)
    if len(body) > CACHE_MAX_BYTES:
        return
    try:
        with db.tx() as con:
            con.execute(CACHE_SQL)
            con.execute("DELETE FROM lex_live_cache WHERE created_at < ?", (time.time() - CACHE_TTL_SEC,))
            con.execute("INSERT OR REPLACE INTO lex_live_cache (key, kind, url, created_at, body)"
                        " VALUES (?,?,?,?,?)", (key, kind, url, time.time(), body))
    except Exception as e:
        print("legal_live: кэш не записан:", e)


SALT_KEY = "LEX_CACHE_SALT"
_salt_cache = {"v": None}


def _salt() -> bytes:
    """Соль сервера для ключей кэша и отпечатков пользователей.

    Переменная окружения LEX_CACHE_SALT (или .env); нет — случайная соль, один раз созданная
    и сохранённая в app_settings. Без соли ключ кэша — sha256 от «search|адрес со словами», и
    слова запроса можно было бы подобрать перебором по словарю."""
    if _salt_cache["v"]:
        return _salt_cache["v"]
    v = _setting(SALT_KEY, "")
    if not v:
        try:
            with db.tx() as con:
                row = con.execute("SELECT value FROM app_settings WHERE key=?", (SALT_KEY,)).fetchone()
                if row and row[0]:
                    v = row[0]
                else:
                    v = secrets.token_hex(32)
                    con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?)",
                                (SALT_KEY, v, db.now()))
        except Exception as e:
            # соль не сохранилась — берём случайную на время работы процесса (кэш просто не
            # переживёт перезапуск); ключ без соли не используем никогда
            print("legal_live: соль кэша не сохранена:", e)
            v = secrets.token_hex(32)
    _salt_cache["v"] = v.encode("utf-8")
    return _salt_cache["v"]


class _Session:
    """Один вопрос: сколько страниц уже взято у сайта, общий срок и личный предел."""

    def __init__(self, user_key: Optional[str] = None, budget: float = None):
        self.pages = 0
        self.fetched = 0
        self.user_key = user_key
        self.user_counted = False
        self.deadline = time.monotonic() + (QUESTION_BUDGET_SEC if budget is None else budget)

    def left(self) -> float:
        return self.deadline - time.monotonic()

    def get(self, url: str, expect: str) -> str:
        # в кэше выдачи адрес не храним: в нём слова вопроса (уже без ПД, но всё же)
        key = lexuz.cache_key(expect, url, salt=_salt())
        page = _cache_get(key)
        if page == NOT_FOUND_MARK:
            raise lexuz.LexNotFound(url)
        if page is not None:
            return page
        if self.pages >= MAX_PAGES:
            raise _Budget()
        left = self.left()
        if left < lexuz.MIN_LEFT_SEC:
            raise lexuz.LexUnavailable("lex.uz не ответил за %d с" % QUESTION_BUDGET_SEC, cooldown=False)
        # личный предел считаем по живым поискам, которые действительно пошли на сайт:
        # ответ из кэша его не тратит
        if not self.user_counted:
            if not _take_user(self.user_key):
                raise _Limit(personal=True)
            self.user_counted = True
        if not _take():
            raise _Limit()
        self.pages += 1
        self.fetched += 1
        try:
            page = lexuz.fetch(url, min(TIMEOUT_SEC, left), expect)
        except lexuz.LexNotFound:
            # «такой страницы нет» тоже запоминаем: иначе каждый вопрос тратил бы на неё обращение
            _cache_put(key, expect, url if expect == "act" else None, NOT_FOUND_MARK)
            raise
        _cache_put(key, expect, url if expect == "act" else None, page)
        return page


# --------------------------------------------------------------------------- #
#  Ключевые слова вопроса (без ПД)
# --------------------------------------------------------------------------- #

def _apostrophe_uz(word: str) -> str:
    """Узбекская латиница: после g и o — ʻ (gʻ, oʻ), в остальных местах — ʼ. Так пишет lex.uz."""
    return re.sub(r"(?<=[gGoO])[ʻʼ‘’'`]", "ʻ", re.sub(r"(?<![gGoO])[ʻʼ‘’'`]", "ʼ", word))


def masked(question: str) -> str:
    """Вопрос после той же маскировки ПД, что перед отправкой в модель (app/llm.mask_pd)."""
    text = llm.mask_pd(question or "")
    return re.sub(r"\[[^\]]*\]", " ", text)          # плейсхолдеры [ФИО], [ИНН] в запрос не идут


# --- Разрешённый словарь: на сайт уходят только слова законодательства -------------------------

TERMS_FILE = lexuz.ROOT / "docs" / "i18n_terms.json"
_vocab = {"stamp": None, "all": [], "terms": []}
_vocab_lock = threading.Lock()


def _strings(obj, skip=()):
    """Все строки JSON-структуры (ключи из skip пропускаются)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in skip:
                yield from _strings(v, skip)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, skip)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _vocab_ready() -> dict:
    """Словарь терминов (FAQ + i18n_terms) и полный словарь (плюс названия отслеживаемых актов).
    Отсортированные списки нормализованных слов — проверка по префиксу основы через bisect."""
    from . import legal
    files = (legal.FAQ_FILE, TERMS_FILE, legal.ACTS_REGISTRY)
    stamp = []
    for f in files:
        try:
            stamp.append(Path(f).stat().st_mtime)
        except OSError:
            stamp.append(None)
    stamp = tuple(stamp)
    if _vocab["stamp"] == stamp:
        return _vocab
    with _vocab_lock:
        texts_terms, texts_titles = [], []
        faq = _read_json(legal.FAQ_FILE) or {}
        texts_terms += list(_strings(faq.get("items") if isinstance(faq, dict) else faq, skip=("id",)))
        texts_terms += list(_strings(_read_json(TERMS_FILE) or {}, skip=("meta",)))
        reg = _read_json(legal.ACTS_REGISTRY) or {}
        texts_titles += [a.get("title") or "" for a in (reg.get("acts") or []) if isinstance(a, dict)]

        def toks(texts):
            out = set()
            for t in texts:
                out.update(w for w in legal._TOKEN_RE.findall(legal.norm(t)) if len(w) >= 3)
            return out
        terms = toks(texts_terms)
        _vocab.update({"stamp": stamp, "terms": sorted(terms),
                       "all": sorted(terms | toks(texts_titles))})
    return _vocab


def _has_prefix(words: list, prefix: str) -> bool:
    i = bisect.bisect_left(words, prefix)
    return i < len(words) and words[i].startswith(prefix)


def _in_index(con, stem_: str, lang: str) -> bool:
    """Основа встречается в тексте или заголовке статей актов (не в заметках проекта, не в путях)."""
    from . import legal
    key = ("live", lang, stem_)
    if key in legal._df_cache["df"]:
        return legal._df_cache["df"][key] > 0
    n = 0
    if con is not None:
        try:
            n = con.execute("SELECT COUNT(*) FROM (SELECT 1 FROM legal_chunks WHERE legal_chunks MATCH ?"
                            " AND language = ? AND act NOT LIKE ? LIMIT 1)",
                            ('{title text} : "%s"*' % stem_, lang, "Заметка проекта%")).fetchone()[0]
        except Exception:
            n = 0
    legal._df_cache["df"][key] = n
    return n > 0


def _lowercase_in_acts(con, bare: str, lang: str) -> bool:
    """Эта словоформа встречается в тексте актов со строчной буквы — значит, это обычное слово
    закона («туристов», «посевов», «рисков»), а не фамилия: фамилии в актах пишутся с заглавной."""
    from . import legal
    key = ("live-lc", lang, bare)
    if key in legal._df_cache["df"]:
        return legal._df_cache["df"][key] > 0
    n = 0
    if con is not None and re.fullmatch(r"\w+", bare):
        try:
            rows = con.execute("SELECT text FROM legal_chunks WHERE legal_chunks MATCH ? AND language = ?"
                               " AND act NOT LIKE ? LIMIT 30",
                               ('{title text} : "%s"' % bare, lang, "Заметка проекта%")).fetchall()
            rx = re.compile(r"(?<!\w)%s(?!\w)" % re.escape(bare))
            n = 1 if any(rx.search(r[0] or "") for r in rows) else 0
        except Exception:
            n = 0
    legal._df_cache["df"][key] = n
    return n > 0


# --- Признаки персональных данных -------------------------------------------------------------

# притяжательные и личные местоимения: «мой клиент», «наш страхователь»
PRONOUNS = {
    "мой", "моя", "моё", "мое", "мои", "моего", "моей", "моему", "моим", "моих", "мною", "мне", "меня",
    "наш", "наша", "наше", "наши", "нашего", "нашей", "нашему", "нашим", "наших", "нас", "нам",
    "ваш", "ваша", "ваше", "ваши", "вашего", "вашей", "вашему", "вашим", "ваших", "вас", "вам",
    "его", "её", "ее", "их", "он", "она", "они", "оно", "ему", "ей", "им", "ими", "него", "неё", "нее",
    "них", "свой", "своя", "своё", "свое", "свои", "своего", "своей", "своему", "своим", "своих", "себя",
    "mening", "meni", "menga", "mendan", "bizning", "bizni", "bizga", "sizning", "sizni", "sizga",
    "uning", "unga", "uni", "ular", "ularning", "men", "biz", "siz", "oʻzi", "ozi", "oʻz",
    "my", "our", "your", "his", "her", "their", "him", "them", "mine", "ours", "yours",
}
# после этих слов стоит имя человека: следующее слово на сайт не уходит
MARK_NEXT = {
    "клиент", "клиента", "клиенту", "клиентом", "клиенте", "клиентка", "клиентки", "клиентке",
    "клиенткой", "страхователь", "страхователя", "страхователю", "страхователем", "страхователе",
    "страхователи", "страхователей", "застрахованный", "застрахованная", "застрахованного",
    "застрахованной", "застрахованному", "застрахованным", "застрахованные", "застрахованных",
    "выгодоприобретатель", "выгодоприобретателя", "гражданин", "гражданина", "гражданину",
    "гражданином", "гражданка", "гражданки", "гражданке", "гражданкой", "господин", "господина",
    "госпожа", "госпожи", "заявитель", "заявителя", "потерпевший", "потерпевшего", "потерпевшая",
    "пострадавший", "пострадавшего", "умерший", "умершего", "фамилия", "фамилии", "имя", "фио",
    "зовут", "mijoz", "mijozi", "mijozim", "mijozimiz", "mijozning", "mijozga", "sugurtalanuvchi",
    "sugurtalanuvchining", "fuqaro", "fuqarosi", "fuqaroning", "janob", "xonim",
    "familiyasi", "ismi", "client", "insured", "policyholder", "mr", "mrs", "ms", "miss", "named",
    "surname", "citizen",
}
# организация и адрес: название стоит и после (ООО «…», ул. …), и перед (… MChJ, … koʻchasi)
MARK_AROUND = {
    "ооо", "оао", "зао", "ао", "чп", "ип", "сп", "хк", "гуп", "мчж", "фирма", "фирмы",
    "ул", "улица", "улице", "улицы", "проспект", "проспекте", "пр", "пер", "переулок", "переулке",
    "проезд", "тупик", "махалля", "махалле", "махалли", "мфй", "массив", "массиве", "квартал", "кв",
    "mchj", "aj", "xk", "yatt", "qk", "ok", "mfy", "mahalla", "mahallasi", "kocha", "kochasi",
    "kochada", "massivi", "llc", "ltd", "inc", "corp", "jsc", "street", "st", "avenue", "ave",
}
# типичные окончания фамилий (-ов, -ева, -ский, -ov, -yeva…): слово отбрасываем, если это не термин
SURNAME = re.compile(r"(?:ов|ев|ёв|ова|ева|ёва|ин|ина|ский|ская|ov|ev|ova|eva|yev|yeva)$")
_SENT_END = re.compile(r"[.!?…\n]")


def _tokens(question: str) -> list:
    """Слова маскированного вопроса: (как написано, в начале ли предложения)."""
    text = masked(question)
    out, prev_end = [], 0
    for m in _WORD_RE.finditer(text):
        gap = text[prev_end:m.start()]
        out.append((m.group(0), not out or bool(_SENT_END.search(gap))))
        prev_end = m.end()
    return out


def words_of(question: str, lang: str, explain: dict = None) -> list:
    """Слова вопроса, которые можно отправить на lex.uz, в исходной форме и порядке.

    Разрешённый словарь: слово проходит, только если его основа есть в словаре законодательства
    (индекс актов, FAQ, термины, названия отслеживаемых актов). Плюс признаки ПД, которые
    отбрасывают даже словарное слово. explain — сюда кладутся причины отказа (для тестов)."""
    from . import legal
    stop = legal.STOP.get(lang, set()) | legal.ALL_STOP | LIVE_STOP | PRONOUNS
    toks = _tokens(question)
    lows = [legal.norm(w) for w, _ in toks]
    voc = _vocab_ready()

    def pick(con) -> list:
        def curated(bare: str) -> bool:
            return _has_prefix(voc["terms"], legal.stem(bare, lang))

        def term(bare: str) -> bool:
            # юридический термин: есть в FAQ/словаре или в тексте актов пишется со строчной
            return curated(bare) or _lowercase_in_acts(con, bare, lang)

        hard, soft = set(), set()             # hard — выбросить всегда; soft — если не термин
        for i, ((w, start), low) in enumerate(zip(toks, lows)):
            # отчество («сергеевич», «oʻgʻli») — признак ФИО: выбрасываем и соседей (имя, фамилия)
            if PATRONYMIC.search(low):
                hard |= {i - 1, i, i + 1}
            if low in MARK_NEXT:
                hard.add(i + 1)
            if low in MARK_AROUND:
                hard |= {i - 1, i + 1}
            # заглавная не в начале предложения — имя, фамилия, название, улица. Аббревиатура
            # целиком заглавными (ОСГОР, НАПП) остаётся, только если это термин FAQ/словаря
            if not start and w[:1].isupper():
                if not (w.isupper() and len(w) >= 3 and curated(low)):
                    hard.add(i)
                    soft |= {i - 1, i + 1}
            if len(low) >= 4 and SURNAME.search(low) and not term(low):
                hard.add(i)
                soft |= {i - 1, i + 1}
        for i in range(len(lows) - 2):         # «sugʻurta qildiruvchi <имя>» — метка из двух слов
            if lows[i] == "sugurta" and lows[i + 1] == "qildiruvchi":
                hard.add(i + 2)

        out = []
        for i, ((w, _start), bare) in enumerate(zip(toks, lows)):
            low = w.lower()
            if lang == "uz":
                low = _apostrophe_uz(low)
            if len(bare) < 3 or any(ch.isdigit() for ch in bare) or bare in stop or low in stop:
                continue
            why = None
            if i in hard:
                why = "признак ПД"
            elif i in soft and not term(bare):
                why = "рядом с именем"
            else:
                st = legal.stem(bare, lang)
                if not (_has_prefix(voc["all"], st) or _in_index(con, st, lang)):
                    why = "нет в словаре законодательства"
            if why:
                if explain is not None:
                    explain[low] = why
                continue
            if low not in out:
                out.append(low)
        return out[:12]

    try:
        with db.tx() as con:
            return pick(con)
    except Exception as e:
        # база недоступна — остаётся словарь FAQ/терминов/названий: он строже, не шире
        print("legal_live: индекс для словаря не прочитан:", type(e).__name__)
        return pick(None)


# глагол в вопросе («возмещает», «выплачивает») в названии акта не встречается — такие слова
# в запрос ставим последними
VERB_RU = re.compile(r"(?:ает|яет|ует|еет|ают|яют|уют|ать|ять|ить|еть|уть|ться|тся)$")


def _is_ins(word: str) -> bool:
    from . import legal
    bare = legal.norm(word)
    return any(bare.startswith(legal.norm(p)) for p in INSURANCE_PREFIX)


def plan_queries(question: str, lang: str) -> dict:
    """Какие слова отправить в поиск: не больше двух запросов по одному-двум словам.

    Вес слова — редкость в локальной базе (legal.weights_of): чего в базе нет, то и тема.
    Слово «страхование» ставится в пару к самому редкому слову: названия актов о страховании
    содержат его («страхование урожая»). Из равных по весу предпочитаем слово рядом с ним.
    """
    from . import legal
    words = words_of(question, lang)
    if not words:
        return {"words": [], "queries": []}
    stems = {w: legal.stem(legal.norm(w), lang) for w in words}
    try:
        with db.tx() as con:
            weights = legal.weights_of(con, list(set(stems.values())), lang)
    except Exception:
        weights = {}
    ins = [i for i, w in enumerate(words) if _is_ins(w)]
    # из слов о страховании берём процесс («страхование», «sugʻurta»): поиск по названию ищет по
    # начальной форме слова, и «страховать» или «страхователем» названия «О страховании …» не находят
    main = [i for i in ins if legal.norm(words[i]).startswith(("страхован", "перестрахован", "sugurta",
                                                                  "insurance", "reinsurance"))]
    ins_i = (main or ins or [None])[0]
    if ins_i is not None:
        words = list(words)
        words[ins_i] = INSURANCE_NOUN.get(lang, "страхование") if not main else words[ins_i]
        stems[words[ins_i]] = legal.stem(legal.norm(words[ins_i]), lang)

    def score(i):
        w = words[i]
        s = weights.get(stems[w], 1.0)
        if lang == "ru" and VERB_RU.search(w):
            s -= 100.0
        if ins_i is not None:
            near = (i == ins_i - 1) if lang == "uz" else (i == ins_i + 1)
            s += 0.01 if near else 0.0
        return s

    rare = sorted((i for i in range(len(words)) if i not in ins),
                  key=lambda i: (-score(i), i))
    queries = []
    if ins_i is not None and rare:
        queries.append(sorted([ins_i, rare[0]]))
        queries.append(sorted([ins_i, rare[1]]) if len(rare) > 1 else [rare[0]])
    elif rare:
        queries.append(sorted(rare[:2]))
        if len(rare) > 1:
            queries.append([rare[0]])
    elif ins_i is not None:
        queries.append([ins_i])
    out, seen = [], set()
    for q in queries:
        s = " ".join(words[i] for i in q)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return {"words": words, "queries": out[:2]}


# --------------------------------------------------------------------------- #
#  Выбор акта и статьи
# --------------------------------------------------------------------------- #

FORM_BONUS = (("кодекс", 0.30), ("kodeks", 0.30), ("закон ", 0.30), ("qonuni", 0.30),
              ("кабинета министров", 0.15), ("vazirlar mahkamasi", 0.15),
              ("приказ", 0.10), ("buyrugʻi", 0.10), ("положени", 0.10))


def _prefixes(question: str, lang: str) -> list:
    from . import legal
    return [legal.norm(w)[:5] for w in words_of(question, lang) if len(legal.norm(w)) >= 4]


def rank_candidates(items: list, question: str, lang: str) -> list:
    """Выдача поиска → кандидаты по убыванию пригодности. Утратившие силу и изменяющие — вон."""
    from . import legal
    pref = _prefixes(question, lang)
    out = []
    for n, it in enumerate(items):
        if it.get("state") and it["state"] != "Y":
            continue
        title = it.get("title") or ""
        if not title or SKIP_TITLE.search(legal.norm(title)):
            continue
        tw = [legal.norm(t) for t in re.findall(r"\w+", title)]
        hit = sum(1 for p in pref if any(t.startswith(p) for t in tw))
        low = (it.get("badge") or "").lower() + " " + title.lower()
        bonus = max([b for k, b in FORM_BONUS if k in low] or [0.0])
        out.append(dict(it, score=round(hit + bonus - n * 0.001, 4)))
    out.sort(key=lambda x: -x["score"])
    return out


def best_units(text: str, question: str, lang: str, query_words: list, limit: int = 2) -> list:
    """Статьи/пункты акта, отвечающие на вопрос. Пороги — как у локального поиска."""
    from . import legal
    units = legal.split_units(text, lang)
    # оглавление lex.uz стоит перед текстом: одна и та же «ст. 42» встречается дважды —
    # настоящая статья идёт последней
    last = {}
    for i, u in enumerate(units):
        if u["unit"]:
            last[u["unit"]] = i
    stems = legal.stems_of(masked(question), lang)
    if not stems:
        return []
    try:
        with db.tx() as con:
            weights = legal.weights_of(con, stems, lang)
    except Exception:
        weights = {s: 1.0 for s in stems}
    must = [legal.stem(legal.norm(w), lang) for w in query_words if not _is_ins(w)] or \
           [legal.stem(legal.norm(w), lang) for w in query_words]
    out = []
    for i, u in enumerate(units):
        if not u["unit"] or last.get(u["unit"]) != i:
            continue
        body = legal.strip_gaps(u["text"])
        r = {"title": legal.fold(u["title"]), "folded": legal.fold(body)}
        hay = (r["title"] + " " + r["folded"]).lower()
        if must and not any(m in hay for m in must):
            continue
        cov = legal._coverage(r, stems)
        cov_w = legal._coverage_w(r, weights)
        if cov < KEY_SHARE_MIN or cov_w < MIN_WEIGHTED:
            continue
        title = r["title"].lower()
        in_title = sum(weights.get(s, 1.0) for s in stems if s in title) / max(1e-9, sum(weights.values()))
        # точность заголовка: «Размер компенсации» целиком из слов вопроса — это и есть ответ;
        # длинный заголовок, где слова вопроса лишь встречаются, — нет
        tw = [t for t in re.findall(r"\w+", title) if len(t) >= 3 and t not in legal.ALL_STOP]
        precision = (sum(1 for t in tw if any(t.startswith(s) for s in stems)) / len(tw)) if tw else 0.0
        out.append(dict(u, coverage=round(cov, 3), coverage_w=round(cov_w, 3),
                        rank=0.6 * cov_w + 0.25 * in_title + 0.15 * cov + 0.2 * precision))
    out.sort(key=lambda x: -x["rank"])
    return out[:limit]


# --------------------------------------------------------------------------- #
#  Главная функция
# --------------------------------------------------------------------------- #

def _status(status: str, lang: str, **extra) -> dict:
    txt = STATUS_TEXT.get(status, {})
    return dict({"status": status, "text": txt.get(lang) or txt.get("ru"),
                 "label": LABEL.get(lang) or LABEL["ru"], "source": "lex.uz"}, **extra)


def _file_text(path: Path) -> str:
    """Текст сохранённого акта без шапки «Источник/Загружено»."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    return re.sub(r"^(?:Источник|Загружено)[^\n]*\n", "", raw, flags=re.M)


def _after_save(path: Path, url: str) -> None:
    """Журнал и дособорка индекса специалиста (reindex берёт только изменившиеся файлы).

    tools/library_build.py отсюда НЕ запускается: в образе сервера нет оригиналов библиотеки
    (.dockerignore оставляет только catalog.json), и пересборка затёрла бы каталог."""
    from . import legal
    try:
        with db.tx() as con:
            db.audit(con, WHO, "загружен акт с lex.uz (живой поиск)", path.stem[:200], {"url": url})
    except Exception as e:
        print("legal_live: журнал не записан:", e)
    try:
        legal.reindex()
    except Exception as e:
        print("legal_live: индекс не дособран:", e)


def _known_acts() -> dict:
    """Акты, уже лежащие в базе: библиотека и каталог живого поиска на постоянном диске."""
    from . import legal
    known = {}
    for d in dict.fromkeys((legal.live_lib(), legal.LIB)):
        for k, v in lexuz.library_doc_ids(d).items():
            known.setdefault(k, v)
    return known


def lookup(question: str, lang: str, who: str = None) -> dict:
    """Живой поиск нормы на lex.uz. Всегда возвращает словарь со status; при status='found' —
    ещё passages (строки в формате legal.search) и сведения об акте.

    who — кто спрашивает (для личного часового предела); хранится только отпечаток с солью."""
    t0 = time.time()
    lang = lang if lang in lexuz.SEARCH_LANG else "ru"
    if not enabled():
        return _status("off", lang)
    if _down["until"] > time.time():
        return _status("unavailable", lang, reason=_down["reason"], took_ms=0)
    plan = plan_queries(question, lang)
    if not plan["queries"]:
        return _status("no_keywords", lang)
    ses = _Session(_user_key(who))
    from . import legal
    known = _known_acts()
    tried = set()
    try:
        cands = []
        for q in plan["queries"]:
            page = ses.get(lexuz.search_url(q, lang, "searchtitle"), "search")
            cands = [c for c in rank_candidates(lexuz.parse_search(page), question, lang)
                     if c["doc_id"] not in tried]
            used_query = q
            if cands:
                break
        if not cands:
            return _status("not_found", lang, query=plan["queries"], pages=ses.fetched,
                           took_ms=int((time.time() - t0) * 1000))
        skipped = []
        for c in cands[:2]:
            tried.add(c["doc_id"])
            in_base = known.get((c["doc_id"] or "").lstrip("-"))
            links = {}
            if in_base:
                page, text = None, _file_text(in_base)
            else:
                try:
                    page = ses.get(c["url"], "act")
                except lexuz.LexNotFound:
                    continue
                except _Budget:
                    break
                text = lexuz.extract_text(page)
                links = lexuz.lang_links(page)
                if lexuz.is_shell(page, text):
                    # на языке вопроса текста нет — только узбекский оригинал. Сравнить вопрос
                    # с ним без перевода нельзя, а переводить норму мы не вправе: даём ссылку
                    skipped.append({"act": c["title"], "badge": c.get("badge"), "url": c["url"],
                                    "official_url": links.get("uz"),
                                    "reason": SHELL_REASON.get(lang) or SHELL_REASON["ru"]})
                    continue
                if not lexuz.looks_like_act(page, text):
                    continue
            units = best_units(text, question, lang, used_query.split())
            if not units:
                continue
            fetched = in_base is None
            if fetched:
                name, group = lexuz.act_file_name(c, lang)
                path = lexuz.save_act(page, c["url"], name, group, lib=legal.live_lib(), note=NOTE_LIVE)
            else:
                path = in_base
            rel = legal.rel_path(path)
            act = legal._act_name(path)
            passages = [{"act": legal.fold(act), "act_code": legal._slug(path.stem), "language": lang,
                         "unit": u["unit"], "title": u["title"], "url": c["url"], "path": rel,
                         "official": 1 if lang == "uz" else 0, "body": u["text"],
                         "folded": legal.fold(legal.strip_gaps(u["text"])),
                         "coverage": u["coverage"], "coverage_w": u["coverage_w"], "score": 0.0}
                        for u in units]
            if fetched:
                _after_save(path, c["url"])
            return _status("found" if fetched else "found_base", lang, passages=passages,
                           act=c["title"], badge=c.get("badge"), url=c["url"], saved=rel,
                           official_url=links.get("uz") if lang != "uz" else c["url"],
                           fetched=fetched, query=used_query, pages=ses.fetched,
                           took_ms=int((time.time() - t0) * 1000))
        return _status("not_found", lang, query=plan["queries"], pages=ses.fetched, skipped=skipped,
                       took_ms=int((time.time() - t0) * 1000))
    except _Limit as e:
        if e.personal:
            txt = STATUS_TEXT["limit_user"]
            return _status("limit", lang, text=txt.get(lang) or txt["ru"], personal=True,
                           per_user_hour=per_user_hour(), took_ms=int((time.time() - t0) * 1000))
        return _status("limit", lang, per_hour=per_hour(), took_ms=int((time.time() - t0) * 1000))
    except _Budget:
        return _status("not_found", lang, query=plan["queries"], pages=ses.fetched,
                       took_ms=int((time.time() - t0) * 1000))
    except lexuz.LexNotFound:
        return _status("not_found", lang, query=plan["queries"], pages=ses.fetched,
                       took_ms=int((time.time() - t0) * 1000))
    except lexuz.LexUnavailable as e:
        if getattr(e, "cooldown", True):
            _down.update({"until": time.time() + COOLDOWN_SEC, "reason": e.reason})
        try:
            with db.tx() as con:
                db.audit(con, WHO, "lex.uz недоступен", "lex.uz", {"причина": e.reason[:300]})
        except Exception:
            pass
        return _status("unavailable", lang, reason=e.reason, took_ms=int((time.time() - t0) * 1000))
