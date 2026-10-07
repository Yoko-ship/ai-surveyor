"""
Сборка ответа специалиста — ask(): лестница источников и части ответа.

Лестница: тема вопроса (рынок / конкуренты / уточнение — app/legal/market.py) → личный кэш → lex.uz →
FAQ → известное молчание закона → поиск по индексу (норма, документы INSON, заметки) →
свободный ответ ИИ (только с ai=true). Части ответа: data — данные с источником, opinion — вывод
(правила или ИИ), sources — плашки источников; у цитат из изменившихся актов — actuality.
"""
import hashlib
import json
import re
import time
from typing import Optional

from fastapi import HTTPException

from .. import db, llm
from ..chatgpt_plan import actor
from . import market as mx
from . import memory as dm
from .ai import ai_answer, ai_free_answer, ai_reference_answer
from .cache import _cache_get, _cache_put, log_question
from .competitors import _competitor_answer
from .faq import faq_answer, faq_match, related, silence_answer, silence_match
from .index import _registry, ensure_index, market_notes_dirs
from .live import LIVE_NOT_NEEDED, _live, _live_answer, _live_enabled, _live_public, legal_live_text
from .parse import detect_lang, norm
from .search_core import staff
from .search_core import (KEY_SHARE_MIN, RARE_PENALTY, _citation, _on_topic, search, stems_of, summarize_passages,
                          wants_competitor)
from .texts import (ACTUALITY_NOTE, ACTUALITY_SINCE, ACTUALITY_TEXT, ASSISTANT_NAME, ASSISTANT_ROLE, DEFAULT_LANG,
                    LANGS, NO_NORM, NO_NORM_BARE, NO_NORM_NOTE, NOTE_NO_LANG, PART_LABEL, SILENCE_NOTE, part_label,
                    source_label)

# порядок цитат: норма первой, документы конкурентов — последними
KIND_ORDER = {"law": 0, "company": 1, "note": 2, "market": 3, "competitor": 4}


# порог уверенности: ниже — считаем, что нормы по вопросу нет, и так и говорим.
# 0,55 стоял ниже фактического шума: вопрос про срок рассмотрения претензии (такой нормы в
# законодательстве нет) набирал 0,62, а «сколько стоит билет в кино» — 0,67. Порог поднят,
# и одновременно введено правило редкого слова (search_core.RARE_SHARE / RARE_PENALTY).
MIN_CONFIDENCE = 0.60

SILENCE_MAX_CONF = 0.3          # «закон молчит» уверенным быть не может


# --------------------------------------------------------------------------- #
#  Актуальность цитаты: данные слежения за законодательством (app/lawwatch.py)
# --------------------------------------------------------------------------- #

ST_REVIEW = "требует пересмотра"
ST_CHANGED = "изменился"                      # то же значение, что lawwatch.ST_CHANGED


def _doc_num(url: str) -> str:
    m = re.search(r"/docs/-?(\d+)", url or "")
    return m.group(1) if m else ""


def _changed_acts() -> list:
    """Отслеживаемые акты, изменившиеся на lex.uz или с правилами «требует пересмотра».

    Данные — таблицы watched_acts и rules, которые ведёт app/lawwatch.py. Нет таблиц — пусто.
    """
    try:
        with db.tx() as con:
            acts = db.rows(con, "SELECT code, title, kind, status, lex_url, redaction, last_changed_at,"
                                " rules_refs FROM watched_acts")
            review = {r["code"] for r in db.rows(con, "SELECT code FROM rules WHERE review_status=?",
                                                 ST_REVIEW)}
    except Exception:
        return []
    reg = {a.get("code"): a for a in _registry()}
    out = []
    for a in acts:
        try:
            refs = json.loads(a["rules_refs"] or "[]")
        except Exception:
            refs = []
        flagged = [r for r in refs if r in review]
        if a["status"] != ST_CHANGED and not flagged:
            continue
        ra = reg.get(a["code"]) or {}
        ids = {_doc_num(u) for u in (a["lex_url"], ra.get("lex_url"), ra.get("lex_url_uz")) if _doc_num(u)}
        # коды вида «зру-730», «пкм № 141»; четырёхзначный номер — только у положений (рег. № 1806),
        # иначе год в названии («от 23.11.2021») совпал бы с любым актом того же года
        t = norm(a["title"] or "")
        keys = [k.replace(" ", "").replace("№", "") for k in re.findall(r"(?:зру|пкм|уп|пп)[-\s№]*\d+", t)]
        if t.startswith("положение"):
            keys += [k for k in re.findall(r"\b\d{4}\b", t) if not k.startswith(("19", "20"))]
        out.append({"code": a["code"], "title": a["title"], "status": ST_CHANGED if a["status"] == ST_CHANGED
                    else ST_REVIEW, "rules": flagged, "redaction": a["redaction"],
                    "since": a["last_changed_at"], "ids": ids, "keys": keys,
                    "url_ru": a["lex_url"] or ra.get("lex_url"), "url_uz": ra.get("lex_url_uz")})
    return out


def _actuality_for(c: dict, changed: list, lang: str) -> Optional[dict]:
    num = _doc_num(c.get("url") or "")
    name = norm(c.get("act") or "").replace(" ", "").replace("№", "")
    for a in changed:
        if (num and num in a["ids"]) or any(k and k in name for k in a["keys"]):
            url = (a["url_uz"] if c.get("language") == "uz" else None) or a["url_ru"] or c.get("url")
            since = (ACTUALITY_SINCE.get(lang) or ACTUALITY_SINCE["ru"]) % a["redaction"] if a["redaction"] else ""
            text = (ACTUALITY_TEXT.get(lang) or ACTUALITY_TEXT["ru"]) % (since, url)
            return {"status": a["status"], "act_code": a["code"], "text": text, "url": url,
                    "redaction": a["redaction"], "changed_at": a["since"], "rules": a["rules"]}
    return None


def with_actuality(out: dict) -> dict:
    """Копия ответа, где у цитат из локальной базы по изменившимся актам есть пометка actuality.

    Цитаты, только что найденные на lex.uz (live), и так из действующей редакции — их не трогаем.
    Кэш ответа не портим: цитаты копируются.
    """
    out = dict(out)
    cits = [dict(c) for c in out.get("citations") or []]
    changed = _changed_acts() if cits else []
    lang = out.get("lang") or DEFAULT_LANG
    hit = False
    for c in cits:
        c.pop("actuality", None)
        if c.get("live") or not changed:
            continue
        a = _actuality_for(c, changed, lang)
        if a:
            c["actuality"] = a
            hit = True
    out["citations"] = cits
    if hit:
        mark = ACTUALITY_NOTE.get(lang) or ACTUALITY_NOTE[DEFAULT_LANG]
        note = out.get("note") or ""
        if mark not in note:
            out["note"] = (note + "; " if note else "") + mark
    return out


def ask(question: str, lang: str = None, with_ai: bool = False, who: str = None,
        session_id: str = None) -> dict:
    """Документы компании ищутся только для вошедшего сотрудника (who «u:<id>»), не для гостя."""
    mark = staff.set((who or "").startswith("u:"))
    try:
        return _ask_routed(question, lang, with_ai, who, session_id)
    finally:
        staff.reset(mark)


def _ask_routed(question: str, lang: str = None, with_ai: bool = False, who: str = None,
                session_id: str = None) -> dict:
    """who — кто спрашивает («u:<id>», «g:<guest_id>», «ip:<адрес>»): для личного предела живого
    поиска на lex.uz и для ключа памяти диалога; в журнал и в ответ не попадает.
    session_id — диалог (строка до 64 знаков от фронта): последние 8 реплик держатся в памяти процесса,
    уточнения («а по классу 8?») разрешаются по контексту. Вопрос о рынке отвечается из данных
    (app/market_expert.py), остальное — по праву и практике, как раньше."""
    question = (question or "").strip()
    if not question:
        raise HTTPException(422, "Вопрос пустой")
    if session_id is not None and not dm.SESSION_RE.match(session_id):
        raise HTTPException(422, "session_id: латиница, цифры и знаки _ . : - , до 64 знаков")
    lang = lang if lang in LANGS else detect_lang(question)
    mkey = dm.memory.key(session_id, who)
    mem = dm.memory.get(mkey)
    last = mem["ctx"]
    intent = mx.detect(question, lang, last)
    if intent.get("competitor"):
        out = _competitor_answer(question, lang, intent)
        if with_ai:
            out["ai"] = ai_reference_answer(question, out["answer"]["text"],
                                            out.get("citations") or [], lang, mem["turns"])
        out["session_id"] = session_id
        dm.memory.add(mkey, question, out["answer"]["text"], {"kind": "competitor", "entity": intent.get("entity")})
        return out
    if intent.get("clarify"):
        out = _clarify_answer(question, lang)
        out["session_id"] = session_id
        dm.memory.add(mkey, question, out["answer"]["text"], {"kind": "clarify", "entity": intent.get("entity")})
        return out
    if intent["is_market"]:
        it = mx.resolve(intent, last)
        out = _market_answer(question, lang, with_ai, it, mem["turns"])
        ctx = out.pop("_ctx")
        out["context"] = {"used": bool(it.get("context_used")), "fields": it.get("context_fields") or [],
                          "follow_up": bool(it.get("follow_up"))}
    else:
        out = _decorate(with_actuality(_ask(question, lang, with_ai, who, history=mem["turns"])), lang)
        ctx = {"kind": "legal"}
        out["context"] = {"used": bool(mem["turns"]) and with_ai, "fields": ["history"] if mem["turns"] else [],
                          "follow_up": False}
    out["session_id"] = session_id
    dm.memory.add(mkey, question, (out.get("answer") or {}).get("text") or "", ctx)
    return out


def _decorate(out: dict, lang: str) -> dict:
    """Правовой ответ: тип и подпись источника, разделение «данные» и «мнение/вывод»."""
    out = dict(out)
    # норма — первой, документы других страховщиков — последними (порядок внутри типа сохраняется)
    out["citations"] = sorted(out.get("citations") or [],
                              key=lambda c: (bool(c.get("closest")), KIND_ORDER.get(c.get("source_kind") or "law", 2)))
    ans = out.get("answer") or {}
    cits = [c for c in out.get("citations") or [] if not c.get("closest")]
    ai = out.get("ai") or {}
    if ans.get("source") == "faq":
        basis = " ".join(str(x or "") for x in (ans.get("basis"), ans.get("text"))).lower()
        if cits:
            kind = "law"
        elif "54-п" in basis or "тарифн" in basis or "tarif siyosat" in basis or "tariff policy" in basis:
            kind = "company"         # ответ опирается на тарифную политику компании, а не на закон
        else:
            kind = "note"
    elif cits:
        kind = cits[0].get("source_kind") or "law"
    elif ai.get("status") == "ok":
        kind = "ai"
    else:
        kind = "none"
    out["intent"] = "legal"
    out["source_kind"] = kind
    # предлагать поиск акта на lex.uz уместно только там, где ответ — норма или нормы нет;
    # ответ из тарифной политики, заметки, данных рынка или документа конкурента — не про закон
    out["lex_search_offer"] = kind in ("law", "none", "ai")
    out["source_label"] = source_label(kind, lang)
    srcs = []
    for c in out.get("citations") or []:
        item = {"kind": c.get("source_kind") or "law", "label": c.get("source_label") or source_label("law", lang),
                "title": " ".join(x for x in (c.get("act"), c.get("unit")) if x), "url": c.get("url"),
                "closest": bool(c.get("closest"))}
        if item not in srcs:
            srcs.append(item)
    out["sources"] = srcs
    out["assistant_role"] = dict(ASSISTANT_ROLE)
    data_lbl = PART_LABEL["data"].get(lang) or PART_LABEL["data"][DEFAULT_LANG]
    op_lbl = PART_LABEL["opinion"].get(lang) or PART_LABEL["opinion"][DEFAULT_LANG]
    out["parts"] = {"data": {"label": data_lbl, "text": ans.get("text") or "", "source_kind": kind,
                             "source_label": out["source_label"]},
                    "opinion": ({"label": op_lbl, "text": ai["text"], "by": "ai"}
                                if ai.get("status") == "ok" and ai.get("text") else None)}
    return out


def _clarify_answer(question: str, lang: str) -> dict:
    """Короткое уточнение без контекста («а по классу 8?»): спрашиваем показатель и период, а не подбираем FAQ."""
    t0 = time.time()
    text = mx.CLARIFY[lang] if lang in mx.CLARIFY else mx.CLARIFY[DEFAULT_LANG]
    out = {"lang": lang, "took_ms": int((time.time() - t0) * 1000),
           "answer": {"text": text, "source": "clarify", "confidence": 0.0, "kind": "уточнение"},
           "citations": [], "related": related(lang), "ai": {"status": "off", "text": None}, "note": None,
           "cached": False, "assistant_name": dict(ASSISTANT_NAME), "assistant_role": dict(ASSISTANT_ROLE),
           "live": dict(LIVE_NOT_NEEDED), "intent": "clarify", "source_kind": "none",
           "source_label": source_label("none", lang), "sources": [], "lex_search_offer": False,
           "suggest": [i["q"] for i in mx.suggest(lang) if i["kind"] == "market"],
           "parts": {"data": {"label": part_label("data", lang),
                              "text": text, "source_kind": "none", "source_label": source_label("none", lang)},
                     "opinion": None},
           "context": {"used": False, "fields": [], "follow_up": False}}
    log_question(question, lang, "clarify", 0.0, out["took_ms"], False)
    return out


MARKET_CONF = 0.95                    # ответ из таблиц НАПП: уверенность высокая, но это не норма


def _market_notes(question: str, lang: str) -> list:
    """Обзоры рынка docs/Знания/Рынок из индекса — дополнительные цитаты к цифрам (если файлы есть)."""
    if not market_notes_dirs():
        return []
    try:
        ensure_index()
        stems = stems_of(question, lang)
        found = [r for r in search(question, lang, limit=8) if r.get("source_kind") == "market"]
        return [_citation(r, lang, stems) for r in found if r.get("coverage", 0) >= KEY_SHARE_MIN][:2]
    except Exception as e:
        print("legal: обзоры рынка не найдены:", e)
        return []


def _market_answer(question: str, lang: str, with_ai: bool, it: dict, history: list) -> dict:
    """Вопрос о рынке: ответ из данных (app/market_expert.py), формат — как у правового ответа плюс market."""
    t0 = time.time()
    res = mx.answer(question, lang, it, with_ai=with_ai, history=history)
    cits = _market_notes(question, lang)
    found = bool(res["found"])
    note = res["ytd_note"]
    if not found:
        note = (note + "; " if note else "") + (mx.NO_DATA[lang] % "").rstrip(" .")
    label = source_label("market", lang)
    # ответ из рэнкинга snsratings (финансы компаний): подпись источника — рэнкинг, а не «данные НАПП»
    srcs = res.get("sources") or []
    if srcs and all((x.get("domain") == "snsratings.uz") for x in srcs):
        label = srcs[0].get("label") or label
    data_lbl = part_label("data", lang)
    ai = res["ai"]
    opinion = None
    if res.get("opinion"):
        opinion = {"label": mx.OPINION_LABEL[lang], "text": res["opinion"], "by": "rules"}
    if ai.get("status") == "ok" and ai.get("text"):
        opinion = {"label": mx.OPINION_LABEL[lang], "text": ai["text"], "by": "ai",
                   "rules_text": res.get("opinion")}
    took = int((time.time() - t0) * 1000)
    out = {"lang": lang, "took_ms": took,
           "answer": {"text": res["text"], "source": "market", "confidence": MARKET_CONF if found else 0.0,
                      "kind": "данные"},
           "citations": cits, "related": related(lang), "ai": ai, "note": note, "cached": False,
           "assistant_name": dict(ASSISTANT_NAME), "assistant_role": dict(ASSISTANT_ROLE),
           "live": dict(LIVE_NOT_NEEDED), "intent": "market", "lex_search_offer": False,
           "source_kind": "market", "source_label": label, "sources": res["sources"],
           "market": {"found": found, "date": res["date"], "period": res["period"], "ytd_note": res["ytd_note"],
                      "unit": mx.MLN[lang], "table": res["table"], "numbers": res["numbers"],
                      "facts": res["facts"]},
           "parts": {"data": {"label": data_lbl, "text": res["text"], "source_kind": "market",
                              "source_label": label, "sources": res["sources"]},
                     "opinion": opinion},
           "_ctx": res["context"]}
    log_question(question, lang, "market", out["answer"]["confidence"], took, found)
    return out


# --------------------------------------------------------------------------- #
#  Ответ по праву и практике (_ask): личный кэш → lex.uz → локальные источники → ИИ
# --------------------------------------------------------------------------- #

def _cache_key(question: str, lang: str, with_ai: bool, history: Optional[list], who: str = None) -> tuple:
    # история диалога влияет только на пересказ модели: без ИИ ответ от неё не зависит
    hist_key = ""
    if with_ai and history:
        hist_key = hashlib.sha256("\n".join(h.get("text") or "" for h in history).encode("utf-8")).hexdigest()[:16]
    # Проверяем доступ на каждом запросе: чужой кэш не обходит допуск к подписке.
    owner = hashlib.sha256(f"{actor.get()}|{who or ''}".encode()).hexdigest()
    return (norm(question), lang, bool(with_ai), _live_enabled(), hist_key,
            owner, bool(with_ai and llm.enabled()))


def _from_cache(key: tuple, t0: float) -> Optional[dict]:
    cached = _cache_get(key)
    if not cached:
        return None
    out = dict(cached)
    out["took_ms"] = int((time.time() - t0) * 1000)
    out["cached"] = True
    return out


def _silence_out(question: str, lang: str, silent: dict, key: tuple, t0: float,
                 live: dict, with_ai: bool, history: Optional[list]) -> dict:
    """Известное молчание закона: отвечаем по списку, нормы не подбираем."""
    text, citations = silence_answer(silent, lang)
    answer = {"text": text, "source": "none", "confidence": SILENCE_MAX_CONF,
              "silence_id": silent["id"]}
    note = SILENCE_NOTE.get(lang) or SILENCE_NOTE[DEFAULT_LANG]
    out = {"lang": lang, "took_ms": int((time.time() - t0) * 1000), "answer": answer,
           "citations": citations, "related": related(lang),
           "ai": ai_reference_answer(question, text, citations, lang, history) if with_ai
                 else {"status": "off", "text": None}, "note": note, "cached": False,
           "assistant_name": dict(ASSISTANT_NAME), "live": _live_public(live)}
    _cache_put(key, out)
    log_question(question, lang, "none", SILENCE_MAX_CONF, out["took_ms"], False)
    return out


def _find_passages(question: str, lang: str) -> tuple:
    """(основы слов, пассажи, пометка). Нет текста акта на языке вопроса — ищем по-русски и говорим об этом."""
    note = None
    stems = stems_of(question, lang)
    passages = search(question, lang)
    if not passages and lang != DEFAULT_LANG:
        stems = stems_of(question, DEFAULT_LANG)
        passages = search(question, DEFAULT_LANG)
        if passages:
            note = NOTE_NO_LANG.get(lang) or NOTE_NO_LANG["ru"]
    return stems, passages, note


def _confidence(passages: list) -> float:
    """Уверенность — по лучшему пассажу и по весу найденных слов, а не по среднему числу совпавших слов:
    среднее по трём случайным нормам давало «приемлемые» 0,5–0,7 там, где закон вопроса не касается.
    Кусок считается ответом, только если в нём есть все редкие слова темы. Нет ни одного такого куска —
    уверенность делим: совпали общие слова, а сама тема в норме не встретилась."""
    full = [r for r in passages if not r.get("rare_missing")]
    best = max((r.get("coverage_w", r["coverage"]) for r in (full or passages)), default=0.0)
    if passages and not full:
        best *= RARE_PENALTY
    return round(best, 2)


def _no_norm_out(question: str, lang: str, with_ai: bool, who: Optional[str], history: Optional[list], key: tuple,
                 t0: float, stems: list, passages: list, conf: float, note: Optional[str], on_topic: bool,
                 live: dict) -> dict:
    """Нормы по вопросу в базе нет: ближайшие статьи, живой поиск на lex.uz, свободный ответ ИИ."""
    # закон молчит: не выдаём три произвольных нормы за ответ. Ближайшие по смыслу
    # статьи показываем отдельной пометкой closest — чтобы было что проверить руками,
    # но только если вопрос вообще о страховом праве (иначе цитаты бессмысленны).
    # «ближайшая по смыслу» — это кусок, где есть редкое слово темы вопроса. Если такого
    # нет ни в одном (вопрос про крышу склада, про билет в кино), показывать нечего
    near = [r for r in passages[:3] if not r.get("rare_missing")] if on_topic else []
    closest = [dict(_citation(r, lang, stems), closest=True) for r in near]
    # lex.uz уже проверен до FAQ/индекса; повторный сетевой запрос здесь не нужен.
    conf = min(conf, SILENCE_MAX_CONF)
    passages, citations = [], closest
    texts = NO_NORM if closest else NO_NORM_BARE
    answer = {"text": texts.get(lang) or texts[DEFAULT_LANG],
              "source": "none", "confidence": conf}
    note = note or NO_NORM_NOTE.get(lang) or NO_NORM_NOTE[DEFAULT_LANG]
    if live.get("status") in ("unavailable", "limit", "not_found") and live.get("text"):
        # честно: сайт недоступен / предел исчерпан / там тоже нет — ответ по базе
        note = note + "; " + live["text"]
        if live["status"] in ("unavailable", "limit"):
            answer["text"] = answer["text"].rstrip(".") + ". " + live["text"] + "."
        for s in (live.get("skipped") or [])[:2]:
            # похожий акт есть, но только на узбекском — ссылку даём, норму не пересказываем
            note += "; %s: %s — %s" % (s["reason"], s.get("badge") or s["act"],
                                       s.get("official_url") or s["url"])
    # ни FAQ, ни закон не покрыли вопрос — отвечает модель, ответ помечен «ИИ»
    ai = ai_free_answer(question, lang, history) if with_ai else {"status": "off", "text": None}
    if ai.get("status") == "ok":
        note = note + " " + (ai.get("note") or "")
    out = {"lang": lang, "took_ms": int((time.time() - t0) * 1000), "answer": answer,
           "citations": citations, "related": related(lang),
           "ai": ai, "assistant_name": dict(ASSISTANT_NAME),
           "note": note, "cached": False, "live": _live_public(live)}
    if live.get("status") not in ("unavailable", "limit", "error"):
        _cache_put(key, out)          # «сайт недоступен» не запоминаем: через минуту он может ожить
    log_question(question, lang, "none", conf, out["took_ms"], False)
    return out


def _passage_citations(question: str, lang: str, stems: list, passages: list, note: Optional[str]) -> tuple:
    """Цитаты найденных пассажей; документ другого страховщика — только дополнением после нормы."""
    citations = [_citation(r, lang, stems) for r in passages]
    if not wants_competitor(question) and any(c.get("source_kind") == "law" for c in citations):
        # норма найдена — документ другого страховщика можно показать только дополнением после неё
        extra = [r for r in search(question, lang, limit=8, allow_competitor=True)
                 if r.get("source_kind") == "competitor" and _on_topic(r, stems)][:1]
        citations += [dict(_citation(r, lang, stems), supplement=True) for r in extra]
    if lang == "en" and citations and not any(c["official"] for c in citations):
        note = (note + " " if note else "") + "unofficial: no official English text of the act exists"
    return citations, note


def _ask(question: str, lang: str = None, with_ai: bool = False, who: str = None,
         history: Optional[list] = None) -> dict:
    t0 = time.time()
    question = (question or "").strip()
    if not question:
        raise HTTPException(422, "Вопрос пустой")
    lang = lang if lang in LANGS else detect_lang(question)
    ensure_index()
    key = _cache_key(question, lang, with_ai, history, who)
    hit = _from_cache(key, t0)
    if hit:
        return hit

    live = _live(question, lang, who)
    if live.get("status") in ("found", "found_base"):
        out = _live_answer(question, lang, with_ai, live, [], t0, history)
        if out:
            _cache_put(key, out)
            log_question(question, lang, "lex", out["answer"]["confidence"], out["took_ms"], True)
            return out
        live = dict(live, status="not_found", text=legal_live_text("not_found", lang))

    note = None
    item, conf = faq_match(question, lang)
    silent = None if item else silence_match(question, lang)
    if silent:
        return _silence_out(question, lang, silent, key, t0, live, with_ai, history)
    if item:
        a = faq_answer(item, lang, conf)
        answer = {"text": a["text"], "source": "faq", "confidence": conf,
                  "kind": a.get("kind"), "basis": a.get("basis")}
        citations, note = a["citations"], a["note"]
        passages = []
    else:
        stems, passages, note = _find_passages(question, lang)
        conf = _confidence(passages)
        # порог релевантности лучшего пассажа: доля значимых слов вопроса и сам bm25.
        # Без него на вопрос без нормы («срок рассмотрения претензии по добровольному виду»)
        # выдавался посторонний пассаж с обычной уверенностью.
        on_topic = _on_topic(passages[0] if passages else None, stems)
        if conf < MIN_CONFIDENCE or not on_topic:
            return _no_norm_out(question, lang, with_ai, who, history, key, t0, stems, passages, conf, note,
                                on_topic, live)
        answer = {"text": summarize_passages(passages, lang, stems),
                  "source": "passages", "confidence": conf}
        citations, note = _passage_citations(question, lang, stems, passages, note)

    ai = {"status": "off", "text": None}
    if with_ai and passages:
        ai = ai_answer(question, passages, lang, history)
    elif with_ai and item:
        ai = ai_reference_answer(question, answer["text"], citations, lang, history)

    took = int((time.time() - t0) * 1000)
    out = {"lang": lang, "took_ms": took, "answer": answer, "citations": citations,
           "related": related(lang), "ai": ai, "note": note, "cached": False,
           "assistant_name": dict(ASSISTANT_NAME), "live": _live_public(live)}
    _cache_put(key, out)
    log_question(question, lang, answer["source"], answer["confidence"], took, bool(citations))
    return out
