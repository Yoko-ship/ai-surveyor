"""lex.uz — первый источник правового ответа, перед FAQ и локальным индексом."""
import time
from typing import Optional

from .ai import ai_answer
from .faq import related
from .search_core import _citation, _unit_of, stems_of, summarize_passages
from .texts import ASSISTANT_NAME, DEFAULT_LANG

LIVE_NOT_NEEDED = {"status": "not_needed", "source": "lex.uz"}


def _live(question: str, lang: str, who: str = None) -> dict:
    try:
        from .. import legal_live
        return legal_live.lookup(question, lang, who=who)
    except Exception as e:                     # без содержимого запроса и внутренних путей в ответе
        print("legal: живой поиск не выполнен:", type(e).__name__)
        return {"status": "unavailable", "source": "lex.uz",
                "text": legal_live_text("unavailable", lang)}


def _live_public(live: dict) -> dict:
    """Поле live ответа: без внутренних пассажей."""
    return {k: v for k, v in (live or {}).items() if k != "passages"}


def _live_enabled() -> bool:
    try:
        from .. import legal_live
        return legal_live.enabled()
    except Exception:
        return False


def legal_live_text(status: str, lang: str) -> str:
    from .. import legal_live
    t = legal_live.STATUS_TEXT.get(status) or {}
    return t.get(lang) or t.get(DEFAULT_LANG) or ""


def _live_answer(question: str, lang: str, with_ai: bool, live: dict, closest: list, t0: float,
                 history: Optional[list] = None):
    """Ответ по норме, найденной на lex.uz. Цитата — только дословная (legal._citation сверяет её
    с сохранённым в библиотеку текстом акта); не сошлась ни одна — ответа нет (None)."""
    from .. import legal_live
    passages = live.get("passages") or []
    stems = stems_of(question, lang)
    fresh = live.get("status") == "found"
    label = legal_live.LABEL.get(lang) or legal_live.LABEL[DEFAULT_LANG]
    cits = []
    for r in passages:
        c = _citation(r, lang, stems)
        if not c["quote"]:
            continue
        c.update({"live": fresh, "found_on": "lex.uz", "live_label": label if fresh else None,
                  "act_badge": live.get("badge")})
        cits.append(c)
    if not cits:
        return None
    kept = [r for r in passages if any(c["unit"] == _unit_of(r) for c in cits)]
    conf = round(max(r.get("coverage_w", 0.0) for r in kept), 2)
    answer = {"text": summarize_passages(kept, lang, stems), "source": "passages",
              "confidence": conf, "live": fresh, "found_on": "lex.uz"}
    parts = [(label + ": " + (live.get("badge") or live.get("act") or "")) if fresh else
             legal_live_text("found_base", lang)]
    if lang != "uz":
        parts.append((legal_live.UNOFFICIAL.get(lang) or legal_live.UNOFFICIAL[DEFAULT_LANG]).rstrip(".")
                     + (f" ({live['official_url']})" if live.get("official_url") else ""))
    note = "; ".join(p for p in parts if p)
    ai = ai_answer(question, kept, lang, history) if with_ai else {"status": "off", "text": None}
    return {"lang": lang, "took_ms": int((time.time() - t0) * 1000), "answer": answer,
            "citations": cits + closest, "related": related(lang), "ai": ai, "note": note,
            "cached": False, "assistant_name": dict(ASSISTANT_NAME), "live": _live_public(live)}
