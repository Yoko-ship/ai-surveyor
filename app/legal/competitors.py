"""
Ответ по документам других страховщиков (intent «competitor»): строки таблиц обзора
«Конкуренты — продукты и условия» по термину вопроса и цитаты из их правил и оферт (тип competitor).
Это не норма права: в ответе пометка «документ другого страховщика — не норма».
"""
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from . import market as mx
from .cache import log_question
from .faq import related
from .index import ensure_index, is_competitor_note, market_note_files, rel_path
from .intents import KIND_STEMS, TERM_STEMS
from .live import LIVE_NOT_NEEDED
from .parse import _WS_RE, _act_name, norm
from .search_core import QUOTE_MAX, _cap, _citation, _clip, search, stems_of
from .texts import ASSISTANT_NAME, ASSISTANT_ROLE, COMPETITOR_TEXT, DEFAULT_LANG, part_label, source_label

COMPETITOR_MAX = 5


def _competitor_of(r: dict) -> str:
    """Компания документа: папка в library/03_…/Конкуренты/<Компания>/ или «обзор конкурентов»."""
    p = Path(r.get("path") or "")
    if "Конкуренты" in p.parts:
        i = p.parts.index("Конкуренты")
        if len(p.parts) > i + 2:
            return p.parts[i + 1]
    return "обзор"


EMPTY_CELLS = {"", "—", "-", "не опубликовано", "нет"}
NOTE_SKIP_RE = re.compile(r"MKT-|Цены могут отличаться", re.IGNORECASE)


def _plain(cell: str) -> str:
    return _WS_RE.sub(" ", (cell or "").replace("**", "").replace("`", "")).strip(" |")


def _q_terms(rq: str) -> tuple:
    q = norm(rq)
    terms = sorted({v for k, v in TERM_STEMS.items() if k in q})
    kind = next((v[0] for k, v in KIND_STEMS.items() if k in q), None)
    return terms, kind



def _note_table_items(cells: list, header: list, section: str, company_words, terms: list, f: Path) -> list:
    """Строка таблицы обзора → «Компания — вид: колонка: значение» по колонкам с термином вопроса."""
    if not header or norm(header[0]) != "компания":
        return []                          # таблицы правил (Код | Суть …) — не про компании
    comp = cells[0]
    if not comp or (company_words and not any(w in norm(comp).replace(" ", "") or w in norm(comp)
                                              for w in company_words)):
        return []
    cols = [i for i, h in enumerate(header) if i and any(t in norm(h) for t in terms)] \
        if terms else [i for i in range(1, len(header) - 1)]
    out = []
    for i in cols:
        val = cells[i] if i < len(cells) else ""
        if norm(val) in EMPTY_CELLS:
            continue
        out.append({"company": comp, "section": section, "column": header[i], "value": val,
                    "text": "%s — %s: %s: %s" % (comp, section, header[i].lower(), val),
                    "path": rel_path(f), "act": "Обзор конкурентов (не норма): " + _act_name(f)})
    return out


def _note_paragraph_item(st: str, section: str, terms: list, f: Path) -> Optional[dict]:
    """Абзац обзора с термином вопроса (не заголовок, не короткая строка, не про INSON)."""
    txt = _plain(st)
    if terms and txt and any(t in norm(txt) for t in terms) and not txt.startswith("#") \
            and len(txt) > 40 and "INSON" not in txt:
        return {"company": "обзор", "section": section, "column": "", "value": txt,
                "text": "%s: %s" % (section, _clip(txt, QUOTE_MAX)), "path": rel_path(f),
                "act": "Обзор конкурентов (не норма): " + _act_name(f)}
    return None


def competitor_note_items(rq: str, company_words=()) -> list:
    """Строки таблиц и абзацы обзора «Конкуренты — продукты и условия», где есть термин вопроса:
    строка таблицы → «Компания — вид: колонка: значение». Служебные строки (MKT-…, «Цены могут
    отличаться»), пустые ячейки и «не опубликовано» не берутся. Только то, что реально написано в обзоре."""
    files = [f for f in market_note_files() if is_competitor_note(f)]
    if not files:
        return []
    terms, kind = _q_terms(rq)
    out = []
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        section, header = "", None
        for ln in lines:
            st = ln.strip()
            if st.startswith("## "):
                section, header = st[3:].strip(), None
                continue
            if kind and kind not in norm(section):
                continue
            if NOTE_SKIP_RE.search(st):
                continue
            if st.startswith("|"):
                cells = [_plain(c) for c in st.strip("|").split("|")]
                if all(set(c) <= set("-: ") for c in cells):
                    continue                       # разделитель |---|
                if header is None:
                    header = cells
                    continue
                out += _note_table_items(cells, header, section, company_words, terms, f)
            else:
                header = None
                item = _note_paragraph_item(st, section, terms, f)
                if item:
                    out.append(item)
    return out


def _competitor_docs(rq: str, terms: list, kind: Optional[str], cwords: set) -> list:
    """Документы конкурентов из индекса: сначала с термином и видом страхования, названная компания — первой."""
    # документы конкурентов из индекса; обзор берём таблицей выше, а не кусками markdown
    found = [r for r in search(rq, DEFAULT_LANG, limit=40, allow_competitor=True)
             if r.get("source_kind") == "competitor" and not (r.get("path") or "").startswith("docs/")]
    kind_words = next((v[1] for v in KIND_STEMS.values() if v[0] == kind), ())

    def _score(r):
        hay = norm((r.get("title") or "") + " " + (r.get("folded") or "") + " " + (r.get("act") or ""))
        has_term = any(t in hay for t in terms) if terms else True
        has_kind = any(w in hay for w in kind_words) if kind_words else True
        return (0 if has_term and has_kind else 1 if has_term or has_kind else 2)
    found.sort(key=_score)                          # устойчивая сортировка: внутри групп порядок поиска
    if cwords:
        mine = [r for r in found if any(w in mx.nrm(_competitor_of(r)).replace(" ", "")
                                        or w in mx.nrm(_competitor_of(r)) for w in cwords)]
        found = mine + [r for r in found if r not in mine]
    return found


def _pick_citations(items: list, found: list, terms: list, stems: list, lang: str) -> list:
    """До 5 цитат по разным компаниям: строки обзора первыми, затем документы — по одной на компанию."""
    label = source_label("competitor", lang)
    picked, seen = [], set()
    for it in items:                                # строки обзора — первыми: в них термин и вид точно есть
        if it["company"] in seen or it["company"] == "обзор" and terms and "обзор" in seen:
            continue
        seen.add(it["company"])
        picked.append({"act": _cap(it["act"]), "unit": it["section"], "quote": it["text"], "url": None,
                       "language": DEFAULT_LANG, "official": False, "closest": False, "source_kind": "competitor",
                       "source_label": label, "company": it["company"], "from_table": bool(it["column"])})
        if len(picked) >= COMPETITOR_MAX:
            break
    for r in found:                                 # затем документы — по одной цитате на компанию
        if len(picked) >= COMPETITOR_MAX:
            break
        who_ = _competitor_of(r)
        if who_ in seen or any(w and w.split()[0].lower() in who_.lower() for w in seen if w != "обзор"):
            continue
        c = _citation(r, DEFAULT_LANG, stems)
        if not c["quote"]:
            continue
        seen.add(who_)
        c["source_label"] = label
        c["company"] = who_
        picked.append(c)
    return picked


def _competitor_text(items: list, picked: list, lang: str) -> str:
    summary = "; ".join("%s — %s" % (it["company"], it["value"]) for it in items if it["column"])[:900]
    head, none_ = COMPETITOR_TEXT.get(lang) or COMPETITOR_TEXT[DEFAULT_LANG]
    if picked:
        text = ((summary + ". ") if summary else "") + head + " " + " ".join(
            "«%s»" % c["quote"] if c.get("from_table") or c["company"] == "обзор"
            else "%s: «%s»" % (c["company"], c["quote"]) for c in picked)
    else:
        text = none_
    return text


def _class_market_line(question: str, lang: str, intent: dict, ent: dict) -> Optional[str]:
    """Рыночная статистика класса — одной строкой, если класс назван и строка есть."""
    if ent.get("type") != "class":
        return None
    try:
        res = mx.answer(question, lang, dict(intent, metric=None, rank=False, is_market=True))
        n = res.get("numbers") or {}
        if res.get("found") and n.get("premiums") is not None:
            return {"ru": "Рынок по классу (НАПП, %s, ytd): премии %s млн сум, убыточность %s.",
                    "uz": "Klass boʻyicha bozor (NAPP, %s, ytd): mukofot %s mln soʻm, zararlilik %s.",
                    "en": "Class market (NAPP, %s, ytd): premiums %s UZS m, loss ratio %s."}[lang] % (
                mx._ru_date(res["date"]), mx._num(n["premiums"], lang), mx._pct(n.get("loss_ratio_pct"), lang))
    except Exception as e:
        print("legal: статистика класса к ответу о конкурентах не добавлена:", e)
    return None


def _competitor_sources(picked: list, lang: str) -> list:
    sources, doms = [], set()
    for c in picked:
        dom = urlsplit(c.get("url") or "").netloc or None
        key = dom or c["act"]
        if key in doms:
            continue
        doms.add(key)
        sources.append({"kind": "competitor", "label": source_label("competitor", lang), "title": c["act"],
                        "url": c.get("url"), "domain": dom, "company": c["company"]})
    if not sources:
        sources.append({"kind": "competitor", "label": source_label("competitor", lang),
                        "title": "library/03_Рынок_НАПП/Конкуренты", "url": None, "domain": None})
    return sources


def _competitor_answer(question: str, lang: str, intent: dict) -> dict:
    """Условия продуктов других страховщиков: обзор конкурентов (строки таблиц по термину вопроса) и их
    документы (тип competitor). Сначала сводка по компаниям, затем до 5 цитат по разным компаниям;
    рыночная статистика класса — короткой строкой в конце, если есть."""
    t0 = time.time()
    ensure_index()
    rq = mx.ru_query(question, lang)
    stems = stems_of(rq, DEFAULT_LANG)
    terms, kind = _q_terms(rq)
    ent = intent.get("entity") or {}
    cwords = set(ent.get("words") or []) if ent.get("type") == "company" else set()
    items = competitor_note_items(rq, cwords)
    found = _competitor_docs(rq, terms, kind, cwords)
    picked = _pick_citations(items, found, terms, stems, lang)
    text = _competitor_text(items, picked, lang)
    market_line = _class_market_line(question, lang, intent, ent)
    if market_line:
        text += " " + market_line
    sources = _competitor_sources(picked, lang)
    took = int((time.time() - t0) * 1000)
    out = {"lang": lang, "took_ms": took,
           "answer": {"text": text, "source": "competitor", "confidence": 0.5 if picked else 0.0,
                      "kind": "документы конкурентов"},
           "citations": picked, "related": related(lang), "ai": {"status": "off", "text": None},
           "note": source_label("competitor", lang), "cached": False,
           "assistant_name": dict(ASSISTANT_NAME), "assistant_role": dict(ASSISTANT_ROLE),
           "live": dict(LIVE_NOT_NEEDED), "intent": "competitor", "lex_search_offer": False,
           "source_kind": "competitor", "source_label": source_label("competitor", lang), "sources": sources,
           "market_line": market_line,
           "parts": {"data": {"label": part_label("data", lang),
                              "text": text, "source_kind": "competitor",
                              "source_label": source_label("competitor", lang), "sources": sources},
                     "opinion": None},
           "context": {"used": False, "fields": [], "follow_up": False}}
    log_question(question, lang, "competitor", out["answer"]["confidence"], took, bool(picked))
    return out
