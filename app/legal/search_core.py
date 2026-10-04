"""
Поиск по индексу: основы слов, вес редких слов, ранжирование выдачи, цитаты (только дословные)
и пересказ из цитат. Имя модуля — не search.py: функция search() — публичное имя app.legal.search.
"""
import re
import threading
from typing import Optional

from .. import db
from . import cache
from . import index as ix
from .index import source_kind
from .intents import ALL_STOP, COMPANY_WORDS, COMPETITOR_WORDS, STOP
from .parse import _TOKEN_RE, _WS_RE, _clean_url, norm, quote_norm, segments
from .texts import DEFAULT_LANG, source_label

MAX_PASSAGES = 5
QUOTE_MAX = 300

# «редкое слово» вопроса — то, вес которого близок к максимальному: именно оно задаёт тему
# («претензия», «кино», «попугай»). Если его нет ни в одном найденном куске, совпадение идёт
# по общим словам («страхование», «срок») и ответом считаться не может — уверенность делим.
RARE_SHARE = 0.8
RARE_PENALTY = 0.5

# порог темы: если в лучшем куске нет хотя бы 40% значимых слов вопроса — это не ответ,
# а совпадение по общей лексике. Плюс нижний порог самого bm25: кусок, набравший меньше,
# попал в выдачу случайно (вопрос «сколько стоит билет в кино» — 4,6 против 8–20 у настоящих норм)
KEY_SHARE_MIN = 0.40
SCORE_MIN = 6.0


# Вопрос о конкурентах, рынке, условиях и ставках других страховщиков: только тогда документы
# конкурентов участвуют в ответе наравне с остальными; иначе — дополнением после нормы
def wants_competitor(question: str) -> bool:
    q = norm(question)
    return any(w in q for w in COMPETITOR_WORDS)


def _terms(text: str, lang: str) -> list:
    stop = STOP.get(lang, set()) | ALL_STOP
    out = []
    for tk in _TOKEN_RE.findall(norm(text)):
        if len(tk) < 3 or tk in stop:
            continue
        if tk not in out:
            out.append(tk)
    return out[:12]


# --------------------------------------------------------------------------- #
#  Поиск по индексу
# --------------------------------------------------------------------------- #

# предел длины основы: русский склоняется окончаниями (основа длинная), узбекский лепит суффиксы
# один за другим (qiymat → qiymatidan → qiymatdan), поэтому основу берём короче
STEM_CAP = {"ru": 8, "uz": 6, "en": 8}


def stem(t: str, lang: str = DEFAULT_LANG) -> str:
    """Грубая основа слова вместо морфологии: «страховая» → «страхов», «qiymatdan» → «qiymat».

    Словарей склонения в стандартной библиотеке нет, а «сумма»* не нашло бы «суммы».
    Отрезаем два последних знака и ограничиваем длину по языку.
    """
    return t[:max(4, min(len(t) - 2, STEM_CAP.get(lang, 8)))]


def stems_of(question: str, lang: str) -> list:
    return [stem(t, lang) for t in _terms(question, lang)]


def match_query(question: str, lang: str) -> str:
    """Запрос FTS5: основы слов через OR (одного совпадения достаточно, точность даёт пересчёт ниже)."""
    return " OR ".join('"%s"*' % s for s in stems_of(question, lang))


# веса bm25 по колонкам таблицы: act, act_code, language, unit, title, text, raw, url, path, official
BM25 = "bm25(legal_chunks, 1.0, 0.0, 0.0, 0.5, 4.0, 1.0, 0.0, 0.0, 0.0, 0.0)"

SEARCH_SQL = f"""
SELECT act, act_code, language, unit, title, url, path, official,
       snippet(legal_chunks, 5, '[', ']', ' … ', 24) AS snip,
       raw AS body,
       text AS folded,
       {BM25} AS score
  FROM legal_chunks
 WHERE legal_chunks MATCH ? AND language = ?
 ORDER BY score
 LIMIT ?
"""

CANDIDATES = 40



# вес слова: редкое слово вопроса («попугай», «крыша») решает, есть ли вообще норма по теме,
# частое («страховой», «договор») встречается в половине базы и о совпадении темы не говорит
_df_cache = cache._df_cache


def _doc_freq(con, stem_: str, lang: str) -> int:
    key = (lang, stem_)
    if key in _df_cache["df"]:
        return _df_cache["df"][key]
    try:
        n = con.execute("SELECT COUNT(*) FROM legal_chunks WHERE legal_chunks MATCH ?"
                        " AND language = ?", ('"%s"*' % stem_, lang)).fetchone()[0]
    except Exception:
        n = 0
    _df_cache["df"][key] = n
    return n


def _lang_total(con, lang: str) -> int:
    if lang not in _df_cache["n"]:
        try:
            _df_cache["n"][lang] = con.execute(
                "SELECT COUNT(*) FROM legal_chunks WHERE language = ?", (lang,)).fetchone()[0] or 1
        except Exception:
            _df_cache["n"][lang] = 1
    return _df_cache["n"][lang]


def weights_of(con, stems: list, lang: str) -> dict:
    """Вес каждой основы: log(N / df). Считается один раз на слово и кэшируется до пересборки."""
    import math
    total = _lang_total(con, lang)
    out = {}
    top = math.log(max(2, total))
    for s in stems:
        df = _doc_freq(con, s, lang)
        # слова, которого в базе нет вовсе («попугай», «крыша»), — самый тяжёлый вес: без него
        # совпадение по общим словам не должно выглядеть уверенным ответом
        out[s] = top if not df else max(0.15, math.log(total / df))
    return out


def _coverage_w(r: dict, weights: dict) -> float:
    """Доля ВЕСА слов вопроса, найденного в куске. Три общих слова из пяти больше не дают 0,6:
    если редкое слово темы в норме не встретилось, уверенность падает и ответ честно считается
    ненайденным."""
    if not weights:
        return 0.0
    hay = ((r.get("title") or "") + " " + (r.get("folded") or "")).lower()
    hit = sum(w for s, w in weights.items() if s in hay)
    return hit / sum(weights.values())


def _coverage(r: dict, stems: list) -> float:
    """Сколько РАЗНЫХ слов вопроса встретилось в куске. bm25 этого не умеет: он вознаграждает
    многократное повторение одного слова, из-за чего «стоимость, стоимость, стоимость» обгоняло
    статью, где есть и «страховая сумма», и «страховая стоимость»."""
    hay = (r["title"] + " " + (r["folded"] or "")).lower()
    return sum(1 for s in stems if s in hay) / max(1, len(stems))


def _on_topic(r: dict, stems: list) -> bool:
    """Порог релевантности: в куске есть хотя бы KEY_SHARE_MIN значимых слов вопроса и сам
    bm25 не ниже SCORE_MIN. Иначе кусок попал в выдачу по общей лексике, а не по теме."""
    if not r:
        return False
    return _coverage(r, list(stems)) >= KEY_SHARE_MIN and -r.get("score", 0.0) >= SCORE_MIN



# документ компании (тарифная политика — распознанный скан с перечнем продуктов) отвечает только на вопрос
# о компании: иначе перечень продуктов («страхование лиц, выезжающих за рубеж») выдавался за ответ
# на правовой вопрос «нужно ли страховать туристов» и живой поиск закона на lex.uz не запускался
def wants_company(question: str) -> bool:
    q = norm(question)
    return any(w in q for w in COMPANY_WORDS)


def _score_rows(rs: list, stems: list, weights: dict, company_ok: bool, competitor_ok: bool) -> list:
    """Пересчёт выдачи FTS5: вес найденных слов вопроса, попадание в заголовок, тип источника."""
    worst = max([-r["score"] for r in rs] or [1.0]) or 1.0
    top_w = max(weights.values()) if weights else 0.0
    rare = {s for s, w in weights.items() if top_w and w >= RARE_SHARE * top_w}
    scored = []
    for r in rs:
        hay_all = ((r["title"] or "") + " " + (r["folded"] or "")).lower()
        # каких редких слов темы в куске нет — по ним ask() решает, ответ это или совпадение
        # по общим словам
        r["rare_missing"] = sorted(s for s in rare if s not in hay_all)
        r["coverage"] = _coverage(r, stems)
        r["coverage_w"] = _coverage_w(r, weights)
        title = (r["title"] or "").lower()
        in_title = sum(weights.get(s, 1.0) for s in stems if s in title) / max(1e-9, sum(weights.values()))
        # норма важнее заметки проекта и правила движка: заметка не источник права;
        # документ компании (тарифная политика) — между нормой и заметкой
        kind = source_kind(r["path"])
        r["source_kind"] = kind
        if kind == "company" and not company_ok:
            continue
        if kind == "competitor" and not competitor_ok:
            continue                       # документ конкурента не отвечает на правовой вопрос вместо нормы
        weight = (0.85 if kind == "company" else 0.8 if kind == "competitor"
                  else (1.0 if r["path"].startswith("library/") else 0.8))
        # решает вес найденных слов (редкое слово темы важнее общих), bm25 — только уточняет
        # порядок внутри; попадание в заголовок статьи ценится отдельно: он и есть тема вопроса
        r["rank"] = (0.6 * r["coverage_w"] + 0.25 * in_title + 0.15 * (-r["score"] / worst)) * weight
        scored.append(r)
    scored.sort(key=lambda x: -x["rank"])
    return scored


def _distinct(scored: list, limit: int) -> list:
    out, seen = [], set()
    for r in scored:
        # один и тот же акт лежит в библиотеке дважды (отдельная глава и документ целиком)
        key = (r["unit"], norm(r["body"])[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= limit:
            break
    return out


def search(question: str, lang: str, limit: int = MAX_PASSAGES, allow_competitor: Optional[bool] = None) -> list:
    stems = stems_of(question, lang)
    company_ok = wants_company(question)
    competitor_ok = wants_competitor(question) if allow_competitor is None else allow_competitor
    q = match_query(question, lang)
    if not q:
        return []
    with db.tx() as con:
        try:
            rs = db.rows(con, SEARCH_SQL, q, lang, CANDIDATES)
        except Exception as e:
            print("legal: поиск не выполнен:", e)
            return []
        weights = weights_of(con, stems, lang)
    rs = [dict(r) for r in rs]
    return _distinct(_score_rows(rs, stems, weights, company_ok, competitor_ok), limit)


SENT_RE = re.compile(r"(?<=[.!?;])\s+")


def _first_sentences(text: str, n: int = 2, limit: int = 400) -> str:
    sents = [s.strip() for s in SENT_RE.split((text or "").strip()) if s.strip()]
    return _clip(" ".join(sents[:n]), limit)


def _clip(out: str, limit: int) -> str:
    """Обрезаем по границе слова: цитата не должна обрываться на половине слова."""
    out = _WS_RE.sub(" ", (out or "").strip())
    if len(out) <= limit:
        return out
    cut = out[:limit]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > limit // 2 else cut).rstrip(" ,;:-") + "…"


def _best_sentences(text: str, stems: list, n: int = 2, limit: int = QUOTE_MAX) -> str:
    """Цитата — предложение, где действительно встретились слова вопроса, а не первое в куске.

    В акте первым предложением часто идёт служебная строка («Глава 46. Поручение»,
    «Oldingi tahrirga qarang») — цитировать её бессмысленно.

    Цитата берётся внутри ОДНОГО непрерывного куска (см. segments): фраза, склеенная через
    выброшенный служебный мусор, в самом акте не встречается и цитатой быть не может.
    """
    best_seg, best_i, best = None, 0, -1
    for seg in segments(text) or [(text or "").strip()]:
        # короткие «предложения» («4-боб.», «16.») из списка НЕ выбрасываем: без них соседние
        # фразы склеивались в цитату, которой в акте нет. Они лишь не годятся как начало цитаты.
        sents = [s.strip() for s in SENT_RE.split(seg) if s.strip()]
        if not sents:
            continue
        for i, s in enumerate(sents):
            if len(s) <= 15:
                continue
            low = s.lower()
            hit = sum(1 for st in stems if st in low)
            if hit > best:
                best_seg, best_i, best = sents, i, hit
    if not best_seg:
        first = (segments(text) or [(text or "").strip()])[0]
        return _clip(_first_sentences(first, n, limit), limit)
    if best <= 0:
        best_i = 0
    return _clip(" ".join(best_seg[best_i:best_i + n]), limit)


def _cap(s: str) -> str:
    return (s[:1].upper() + s[1:]) if s else s


def _unit_of(r: dict) -> str:
    return r.get("unit") or r.get("title") or ""


def summarize_passages(passages: list, lang: str, stems: list = ()) -> str:
    """Резюме из предложений найденных пассажей. Ничего не сочиняем — только цитируем с указанием статьи."""
    parts = []
    for r in passages[:2]:
        head = ", ".join(x for x in (_cap(r["act"]), _unit_of(r)) if x)
        body = _best_sentences(r["body"], list(stems), 2, 400)
        if body:
            parts.append(f"{head}: {_cap(body)}")
    if not parts:
        return {"ru": "В базе нет нормы, прямо отвечающей на этот вопрос.",
                "uz": "Bazada bu savolga bevosita javob beradigan norma yoʻq.",
                "en": "No provision in the database answers this question directly."}[lang]
    return " ".join(parts)


# тексты файлов для сверки дословности: файлов десятки, каждый до полумегабайта — держим
# последние несколько и сверяем mtime, чтобы не читать диск на каждый вопрос
_TEXT_CACHE_MAX = 8
_text_cache = {}
_text_lock = threading.Lock()


def file_text(rel: str) -> str:
    """Нормализованный текст исходного файла акта (апострофы и пробелы), пустая строка — нет файла."""
    if not rel or rel.startswith("db:"):
        return ""
    p = ix.ROOT / rel
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return ""
    with _text_lock:
        hit = _text_cache.get(rel)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        txt = quote_norm(p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        txt = ""
    with _text_lock:
        if len(_text_cache) >= _TEXT_CACHE_MAX:
            _text_cache.clear()
        _text_cache[rel] = (mtime, txt)
    return txt


def quote_core(quote: str) -> str:
    """Цитата без нашего многоточия обрезки — именно её ищем в исходном файле."""
    return (quote or "").rstrip("…").strip()


def verbatim(rel: str, quote: str) -> bool:
    """Цитата дословно встречается в исходном файле (с точностью до апострофов и пробелов).

    Правила движка (path='db:rules') и заметки проекта не акты — их не сверяем.
    """
    core = quote_core(quote)
    if len(core) < 20:
        return False
    text = file_text(rel)
    if not text:
        return True                      # файла нет (правило движка) — сверять нечего
    return quote_norm(core) in text


def _citation(r: dict, lang: str, stems: list = ()) -> dict:
    """Цитата из пассажа. Не дословную не отдаём: лучше без текста, чем выдуманная норма."""
    quote = _best_sentences(r["body"], list(stems), 2, QUOTE_MAX)
    rel = r.get("path") or ""
    if not verbatim(rel, quote):
        # длинная склейка не сошлась — пробуем одно первое предложение лучшего куска
        quote = _best_sentences(r["body"], list(stems), 1, QUOTE_MAX)
        if not verbatim(rel, quote):
            quote = ""
    kind = source_kind(rel)
    return {"act": _cap(r["act"]), "unit": _unit_of(r), "quote": quote,
            "url": _clean_url(r["url"]) or None, "language": r["language"],
            "official": bool(r["official"]), "closest": False,
            "source_kind": kind, "source_label": source_label(kind, lang)}
