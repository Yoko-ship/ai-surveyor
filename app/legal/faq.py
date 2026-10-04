"""FAQ специалиста (docs/Специалист — FAQ.json), известные молчания закона и ответ из FAQ."""
import json
from typing import Optional

from . import index as ix
from .intents import SILENCES, SYNONYMS
from .parse import _TOKEN_RE, _clean_url, norm
from .search_core import QUOTE_MAX, _citation, _on_topic, _terms, search, stems_of
from .texts import DEFAULT_LANG, NO_NORM_BARE, NOTE_NO_LANG, NOTE_ONLY_LANG, PRACTICE_NOTE, source_label

# FAQ переименован 22.09.2026 («ИИ специалист по страхованию»). Старое имя поддерживается:
# на развёрнутом сервере файл мог остаться прежним.
FAQ_FILE_NEW = ix.ROOT / "docs" / "Специалист — FAQ.json"
FAQ_FILE_OLD = ix.ROOT / "docs" / "Юрист — FAQ.json"
FAQ_FILE = FAQ_FILE_NEW if FAQ_FILE_NEW.exists() else FAQ_FILE_OLD

_faq_cache = {"mtime": None, "items": [], "version": None}


def faq_items() -> list:
    """docs/Специалист — FAQ.json (старое имя «Юрист — FAQ.json» тоже подходит)."""
    global FAQ_FILE
    if not FAQ_FILE.exists():                     # файл могли переименовать на работающем сервере
        FAQ_FILE = FAQ_FILE_NEW if FAQ_FILE_NEW.exists() else FAQ_FILE_OLD
    try:
        st = FAQ_FILE.stat()
    except OSError:
        _faq_cache.update({"mtime": None, "items": [], "version": None})
        return []
    if _faq_cache["mtime"] == st.st_mtime:
        return _faq_cache["items"]
    try:
        data = json.loads(FAQ_FILE.read_text(encoding="utf-8"))
        items = [i for i in (data.get("items") or []) if isinstance(i, dict) and i.get("id")]
        _faq_cache.update({"mtime": st.st_mtime, "items": items, "version": data.get("version")})
    except Exception as e:
        print("legal: FAQ не прочитан:", e)
        _faq_cache.update({"mtime": st.st_mtime, "items": [], "version": None})
    return _faq_cache["items"]


SYN_OF = {}
for _i, _grp in enumerate(SYNONYMS):
    for _w in _grp:
        SYN_OF.setdefault(_w, set()).add(_i)

PREFIX_MIN = 5          # общая основа в пять знаков: «максимальный» и «максимум» — одно слово
FAQ_MIN_SCORE = 0.6


def _groups(word: str) -> set:
    """Номера синонимических групп слова (по самому слову и по его основе)."""
    out = set(SYN_OF.get(word, ()))
    for w, g in SYN_OF.items():
        if _common_prefix(w, word) >= PREFIX_MIN:
            out |= g
    return out


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _hay_tokens(text: str) -> list:
    return _TOKEN_RE.findall(text)


def _term_hit(term: str, tokens: list, groups: set) -> bool:
    """Слово вопроса найдено в вопросе FAQ: тот же корень или синоним.

    Короткие токены («ст», «ли», «в») в сравнении не участвуют: раньше «ст» из тега «ст. 934»
    совпадало с любым словом на «ст-» («ставка», «страхование»), и вопрос про ставку получал
    ответ про дату определения стоимости.
    """
    for tk in tokens:
        if len(tk) < 4:
            # трёхбуквенные аббревиатуры (PML, EML, MFL, БРВ) ловим только полным совпадением:
            # по началу слова они по-прежнему не сравниваются
            if len(tk) == 3 and tk == term:
                return True
            continue
        n = _common_prefix(tk, term)
        if n >= PREFIX_MIN or (n >= 4 and n >= min(len(tk), len(term)) - 1):
            return True
        if groups and groups & SYN_OF.get(tk, set()):
            return True
    return False


def _faq_haystack(item: dict, lang: str) -> str:
    q = item.get("q") or {}
    tags = " ".join(str(t) for t in (item.get("tags") or []))
    return norm(" ".join([str(q.get(lang) or ""), str(q.get(DEFAULT_LANG) or ""), tags,
                          str(item.get("id") or "")]))



def faq_match(question: str, lang: str) -> tuple:
    """Лучший пункт FAQ и уверенность 0..1. Считаем долю слов вопроса, найденных в q + tags."""
    items = faq_items()
    if not items:
        return None, 0.0
    terms = _terms(question, lang)
    if not terms:
        return None, 0.0
    groups = [_groups(t) for t in terms]
    best, best_score = None, 0.0
    for item in items:
        tokens = _hay_tokens(_faq_haystack(item, lang))
        hit = sum(1 for t, g in zip(terms, groups) if _term_hit(t, tokens, g))
        score = hit / len(terms)
        if score > best_score:
            best, best_score = item, score
    return (best, round(best_score, 3)) if best_score >= FAQ_MIN_SCORE else (None, round(best_score, 3))


def silence_match(question: str, lang: str) -> Optional[dict]:
    """Известное молчание закона: вопрос из списка SILENCES. Сравнение по основам слов.

    Проверяется ДО поиска по актам (но после FAQ юриста): иначе поиск подбирает к такому
    вопросу норму об обязательном виде страхования и выдаёт её за ответ.
    """
    q = norm(question)
    if not q:
        return None
    for item in SILENCES:
        if any(bad in q for bad in item.get("none") or ()):
            continue
        if all(any(w in q for w in group) for group in item["all"]):
            return item
    return None


def silence_answer(item: dict, lang: str) -> tuple:
    """Ответ по известному молчанию: текст + ближайшая норма как «ближайшая» цитата."""
    text, _ = _pick(item.get("a") or {}, lang)
    near_q, _ = _pick(item.get("near") or {}, lang)
    citations = []
    if near_q:
        stems = stems_of(near_q, lang)
        # ближайшую норму показываем только если она действительно по теме: иначе к узбекскому
        # вопросу подставлялся случайный пункт Положения 1882 на кириллице
        found = [r for r in search(near_q, lang, limit=3) if _on_topic(r, stems)][:2]
        citations = [dict(_citation(r, lang, stems), closest=True) for r in found]
    return text or NO_NORM_BARE[lang], citations


# --------------------------------------------------------------------------- #
#  Ответ из FAQ
# --------------------------------------------------------------------------- #

def _pick(d, lang: str):
    """Значение на языке вопроса; нет — русское. Возвращает (текст, был ли откат на ru)."""
    if not isinstance(d, dict):
        return (d, False) if d else (None, False)
    v = d.get(lang)
    if v:
        return v, False
    return d.get(DEFAULT_LANG), bool(d.get(DEFAULT_LANG)) and lang != DEFAULT_LANG


def _pick_any(d, lang: str) -> tuple:
    """Значение на языке вопроса; нет — по порядку ru, uz, en. Возвращает (значение, язык).

    Нужно там, где норма есть только на одном языке: у Положения 3845 текст только узбекский,
    и без этого отката сотрудник видел ответ вовсе без цитаты и без ссылки — проверить норму
    было нельзя. Лучше показать узбекский оригинал с пометкой, чем пустое место.
    """
    if not isinstance(d, dict):
        return (d, lang) if d else (None, None)
    for cand in (lang, DEFAULT_LANG, "uz", "en"):
        if d.get(cand):
            return d[cand], cand
    return None, None


def faq_answer(item: dict, lang: str, confidence: float) -> dict:
    text, fell_back = _pick(item.get("a") or {}, lang)
    citations = []
    for c in item.get("citations") or []:
        quote, q_lang = _pick_any(c.get("quote") or {}, lang)
        url, u_lang = _pick_any(c.get("url") or {}, lang)
        q_fb = bool(quote) and q_lang != lang
        official = (c.get("official") or {})
        official = bool(official.get(q_lang or lang)) if isinstance(official, dict) else bool(official)
        citations.append({"act": c.get("act") or "", "unit": c.get("article") or "",
                          "quote": (quote or "")[:QUOTE_MAX], "url": _clean_url(url) or None,
                          "language": q_lang or lang, "official": official,
                          "source_kind": "law", "source_label": source_label("law", lang)})
        fell_back = fell_back or q_fb
    note = None
    if fell_back:
        note = NOTE_NO_LANG.get(lang) if lang != DEFAULT_LANG else None
    # цитата показана не на языке вопроса — честно говорим, на каком она
    other = {c["language"] for c in citations if c["quote"]} - {lang}
    if other and not note:
        note = NOTE_ONLY_LANG.get(lang, NOTE_ONLY_LANG[DEFAULT_LANG]) % ", ".join(sorted(other))
    if lang == "en" and not any(c["official"] for c in citations):
        note = (note + " " if note else "") + "unofficial: no official English text of the act exists"
    # практический вопрос (андеррайтинг, документы, оценка, убытки): нормы нет, есть заметка проекта
    if (item.get("kind") == "практика") or (not citations and item.get("basis")):
        basis, _ = _pick_any(item.get("basis") or {}, lang)
        mark = PRACTICE_NOTE.get(lang) or PRACTICE_NOTE[DEFAULT_LANG]
        note = (note + " " if note else "") + mark + (f" ({basis})" if basis else "")
    return {"text": text or "", "citations": citations, "note": note,
            "confidence": confidence, "kind": item.get("kind") or "норма",
            "basis": _pick_any(item.get("basis") or {}, lang)[0]}


def related(lang: str, limit: int = 6) -> list:
    out = []
    for item in faq_items()[:50]:
        q, _ = _pick(item.get("q") or {}, lang)
        if q:
            out.append({"id": item["id"], "q": q})
        if len(out) >= limit:
            break
    return out
