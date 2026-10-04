"""Маршруты специалиста: /legal/ask, /legal/suggest, /legal/faq, /legal/silences, /legal/acts, /legal/reindex."""
import os
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .. import db
from . import faq as fq
from . import market as mx
from .answer import ask
from .faq import _pick, faq_items
from .index import PARSER_ROW, ensure_index, reindex
from .intents import SILENCES
from .texts import ASSISTANT_NAME, ASSISTANT_ROLE, DEFAULT_LANG, LANGS, SILENCE_NOTE, source_label

router = APIRouter()

# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #

@router.on_event("startup")
def _build_index_on_start():
    """При старте сервера индекс дособирается в фоне: юрист мог докачать узбекские тексты,
    пока сервер был выключен. Отдельным потоком — старт не должен ждать разбор библиотеки."""
    if os.environ.get("SURVEYOR_NO_BACKGROUND") == "1":
        return
    try:
        from .. import background
        background.start("legal-index", lambda: reindex())
    except Exception as e:
        print("legal: индекс при старте не собран:", e)


class AskIn(BaseModel):
    q: str = Field(min_length=2, max_length=1000)
    lang: Optional[str] = None
    ai: bool = False
    session_id: Optional[str] = Field(None, max_length=64)   # диалог: генерирует фронт


def _who(request: Request) -> Optional[str]:
    """Ключ спрашивающего для личного предела живого поиска: вошедший, гость или адрес."""
    try:
        from .. import guest
        from ..guard import client_host
        user = request.scope.get("surveyor_user") or {}
        key = guest.owner_of(request, user)
        if key:
            return key
        host = client_host(request)
        return ("ip:" + host) if host else None
    except Exception:
        return None


@router.post("/legal/ask")
def legal_ask(body: AskIn, request: Request):
    """Мгновенный ответ специалиста (право, практика, рынок). lang не указан — определяем по тексту.
    session_id — память диалога (последние 8 реплик, 2 часа, только в памяти процесса)."""
    return ask(body.q, body.lang, with_ai=body.ai, who=_who(request), session_id=body.session_id or None)


@router.get("/legal/suggest")
def legal_suggest(lang: str = DEFAULT_LANG):
    """8 примеров вопросов — по рынку и по праву — на языке интерфейса."""
    lang = lang if lang in LANGS else DEFAULT_LANG
    items = mx.suggest(lang)
    for i in items:
        i["label"] = source_label(i["kind"], lang)
    return {"lang": lang, "count": len(items), "items": items,
            "assistant_name": dict(ASSISTANT_NAME), "assistant_role": dict(ASSISTANT_ROLE)}


@router.get("/legal/faq")
def legal_faq(lang: str = DEFAULT_LANG):
    """Список вопросов FAQ — чипы «спросить в один клик»."""
    lang = lang if lang in LANGS else DEFAULT_LANG
    items = []
    for item in faq_items():
        q, fb = _pick(item.get("q") or {}, lang)
        if not q:
            continue
        items.append({"id": item["id"], "q": q, "tags": item.get("tags") or [],
                      "kind": item.get("kind") or "норма",
                      "lang": DEFAULT_LANG if fb else lang})
    return {"lang": lang, "version": fq._faq_cache.get("version"), "count": len(items), "items": items,
            "assistant_name": dict(ASSISTANT_NAME),
            "practice_count": sum(1 for i in items if i["kind"] == "практика")}


@router.get("/legal/silences")
def legal_silences(lang: str = DEFAULT_LANG):
    """Известные молчания закона: вопросы, ответ на которые даёт не закон, а правила и договор."""
    lang = lang if lang in LANGS else DEFAULT_LANG
    items = []
    for s in SILENCES:
        q, _ = _pick(s.get("q") or {}, lang)
        a, _ = _pick(s.get("a") or {}, lang)
        items.append({"id": s["id"], "q": q, "a": a})
    return {"lang": lang, "count": len(items), "items": items,
            "note": SILENCE_NOTE.get(lang) or SILENCE_NOTE[DEFAULT_LANG]}


@router.get("/legal/acts")
def legal_acts():
    """Какие акты и на каких языках лежат в индексе — чтобы юрист видел, что ещё не докачано."""
    ensure_index()
    with db.tx() as con:
        rs = db.rows(con, "SELECT act, act_code, language, SUM(chunks) AS chunks FROM legal_files"
                          " WHERE path <> ? GROUP BY act_code, language ORDER BY act", PARSER_ROW)
    acts = {}
    for r in rs:
        a = acts.setdefault(r["act_code"], {"act_code": r["act_code"], "act": r["act"],
                                            "languages": [], "chunks": 0})
        a["languages"].append(r["language"])
        a["chunks"] += r["chunks"] or 0
    out = sorted(acts.values(), key=lambda a: a["act"])
    missing = [a["act"] for a in out if "uz" not in a["languages"]]
    return {"count": len(out), "acts": out, "languages": list(LANGS),
            "without_uz": missing, "faq_file": fq.FAQ_FILE.exists()}


@router.post("/legal/reindex")
def legal_reindex(request: Request, force: bool = True):
    """Пересборка индекса (администратор): юрист докачал узбекский текст — нажал и готово."""
    user = request.scope.get("surveyor_user") or {}
    out = reindex(force=force)
    try:
        with db.tx() as con:
            db.audit(con, user.get("login") or "админ", "пересобран индекс законодательства",
                     "legal_chunks", {"кусков": out["chunks"], "файлов": out["files_total"]})
    except Exception as e:
        print("legal: журнал пересборки не записан:", e)
    return out
