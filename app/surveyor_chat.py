"""
ИИ-сюрвейер одним диалогом (задача 222, 22.09.2026).

Вместо трёх вкладок мини-аппа — один сценарий: поздоровались → узнали, договор новый или
продлеваем старый → человек одним действием сбрасывает файлы (договор, общий PDF, фото, Excel)
→ сервер говорит, что нашёл, и показывает чек-лист документов по продукту → заполняются четыре
обязательных поля, остальное человек уточняет по желанию → анализ, ставка, франшиза и пояснение
→ дальше вопрос юристу или уточнение анализа.

Машина состояний детерминированная: шаг, поля и проверки считает сервер (app/risk_analytics.py,
app/analysis_docs.py). ИИ (app/llm.py) нужен ровно для трёх вещей — свободный текст, извлечение
данных из сканов и фотографий, перевод. Если ИИ выключен, диалог работает целиком по правилам.

Ответ сервера — список «карточек», чтобы интерфейс просто их рисовал:
    text      {"type": "text", "text": "...", "tone": "info|warn"}
    checklist {"type": "checklist", "title": "...", "items": [{"name", "ok", "why"}]}
    fields    {"type": "fields", "title": "...", "scope": "must|optional", "fields": [...]}
    chips     {"type": "chips", "key": "...", "title": "...", "items": [{"value", "label"}]}
    result    {"type": "result", "summary", "level", "franchise", "narrative", "documents"}

Персональные данные: в chat_messages текст кладётся уже замаскированным (llm.mask_pd), в
состоянии сессии хранятся только поля объекта и суммы. Гость опознаётся анонимной cookie «gid»
(app/guest.py); сессия чата живёт 7 дней и переживает перезагрузку страницы — cookie при каждом
обращении к /chat/* продлевается на те же 7 дней.
"""
import json
import secrets
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Body, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from . import db, analysis_docs as adocs
from . import auth, db, guest, i18n, ingest, llm
from . import risk_analytics as ra
from . import risk_api

router = APIRouter()

DIR = db.DATA_DIR / "chat"              # временные файлы сессии (фото, Excel); тесты подменяют
TTL_DAYS = 7
TTL_SEC = TTL_DAYS * 24 * 3600
MAX_FILES = 10
MAX_BYTES = 15 * 1024 * 1024            # на один файл
MAX_TEXT = 2000                         # длина реплики человека
MAX_HISTORY = 60                        # сообщений в сессии

STEPS = ("greeting", "contract_mode", "upload", "must", "optional", "result", "followup")

FMT_MIME = {"pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
DOC_FORMATS = {"pdf", "docx"}           # их разбирает app/analysis_docs.py (договор, общий PDF)
AI_FORMATS = {"jpg", "jpeg", "png"}     # фото объекта — только через ИИ
TEXT_FORMATS = {"xlsx"}                 # таблицы — текстом через app/ingest

SCHEMA_SQL = [
    """CREATE TABLE IF NOT EXISTS chat_sessions (
        id          TEXT PRIMARY KEY,
        owner_key   TEXT NOT NULL DEFAULT '',
        user_id     INTEGER NOT NULL DEFAULT 0,
        lang        TEXT NOT NULL DEFAULT 'ru',
        step        TEXT NOT NULL DEFAULT 'greeting',
        state_json  TEXT NOT NULL DEFAULT '{}',
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        expires_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS chat_messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id  TEXT NOT NULL,
        role        TEXT NOT NULL,
        kind        TEXT NOT NULL DEFAULT 'text',
        text        TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages (session_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner ON chat_sessions (owner_key, expires_at)",
]

NOT_FOUND = "Диалог не найден или его срок (7 дней) истёк — начните заново"


def ensure_tables(con) -> None:
    for sql in SCHEMA_SQL:
        con.execute(sql)


# --------------------------------------------------------------------------- #
#  Тексты интерфейса (i18n). Ключи заведены в app/i18n/{ru,uz,en}.json
# --------------------------------------------------------------------------- #

def T(key: str, lang: str, **kw) -> str:
    return i18n.t(key, lang, **kw)


def ai_off_text(reason: Optional[str], lang: str) -> str:
    """
    Честное объяснение, почему ответил не ИИ: не подключён / кончилась квота у поставщика /
    ошибка связи. Одно «ИИ не подключён» на все случаи вводит человека в заблуждение.
    """
    text = str(reason or llm.last_error.get("text") or "").strip()
    low = text.lower()
    if not text or "не подключ" in low or "выключен" in low or "не поддержан" in low:
        return T("chat.ai_off", lang)
    if "лимит" in low or "средства" in low or "квот" in low:
        return T("chat.ai_quota", lang)
    return T("chat.ai_error", lang, reason=text)


# --------------------------------------------------------------------------- #
#  Франшиза: модуль пишет актуарий параллельно (app/franchise.py)
# --------------------------------------------------------------------------- #

def franchise_advice(analysis: dict, state: dict) -> dict:
    """
    Рекомендация по франшизе. Основной источник — app/franchise.recommend_franchise(...).
    Модуля ещё нет — отдаём честную заглушку, а не выдуманный порог: правила страхования
    и пороги франшизы определяет компания, придумывать их нельзя.
    """
    try:
        from .franchise import recommend_franchise            # noqa: PLC0415
    except Exception:
        s = (analysis or {}).get("summary") or {}
        return {"ok": False, "source": "нет",
                "reason": "Пороги франшизы ещё не утверждены в системе — решение за андеррайтером",
                "current": (state.get("optional") or {}).get("deductible"),
                "sum_insured": s.get("sum_insured")}
    try:
        # поля берём те, на которых анализ реально считался: в быстром режиме это набор
        # после apply_defaults (analysis["inputs"]), иначе пересчёт упадёт на пустых полях
        inputs = (analysis or {}).get("inputs") or {}
        must = dict(inputs.get("must") or state.get("must") or {})
        optional = dict(inputs.get("optional") or state.get("optional") or {})
        # сигнатура актуария: recommend_franchise(con, must, optional, *, analysis=...) — анализ передаём готовый
        with db.tx() as con:
            out = recommend_franchise(con, must, optional, analysis=analysis)
    except Exception as e:
        return {"ok": False, "source": "нет",
                "reason": f"Расчёт франшизы не выполнен: {type(e).__name__}: {str(e)[:200]}"}
    if isinstance(out, dict):
        return {"ok": True, "source": "правила компании", **out}
    return {"ok": True, "source": "правила компании", "value": out}


# --------------------------------------------------------------------------- #
#  Сессия
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    return datetime.now()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def new_state() -> dict:
    return {"product": {}, "must": {}, "optional": {}, "docs": [], "doc_ids": [],
            "contract": None, "previous_contract": None, "analysis": None,
            "assumed": [], "checklist": [], "ai_notes": []}


def cleanup(con=None) -> int:
    """Чистка просроченных диалогов и их файлов. Вызывается при каждом /chat/start."""
    def _run(c):
        ensure_tables(c)
        old = db.rows(c, "SELECT id FROM chat_sessions WHERE expires_at <= ?", _iso(_now()))
        for r in old:
            shutil.rmtree(DIR / r["id"], ignore_errors=True)
            c.execute("DELETE FROM chat_messages WHERE session_id=?", (r["id"],))
            c.execute("DELETE FROM chat_sessions WHERE id=?", (r["id"],))
        return len(old)
    if con is not None:
        return _run(con)
    with db.tx() as c:
        return _run(c)


def _session(con, sid: str, owner: Optional[str]) -> Optional[dict]:
    """Сессия по id — только своя (owner_key) и только не просроченная."""
    if not sid or not owner:
        return None
    rows = db.rows(con, "SELECT * FROM chat_sessions WHERE id=? AND owner_key=? AND expires_at > ?",
                   sid, owner, _iso(_now()))
    return rows[0] if rows else None


def _load_state(row: dict) -> dict:
    try:
        st = json.loads(row["state_json"] or "{}")
    except Exception:
        st = {}
    return new_state() | (st if isinstance(st, dict) else {})


def _save(con, sid: str, step: str, state: dict, lang: str = None) -> None:
    now = _now()
    sets = ["step=?", "state_json=?", "updated_at=?", "expires_at=?"]
    args = [step, json.dumps(state, ensure_ascii=False, default=str), _iso(now),
            _iso(now + timedelta(seconds=TTL_SEC))]
    if lang:
        sets.append("lang=?")
        args.append(lang)
    args.append(sid)
    con.execute("UPDATE chat_sessions SET " + ", ".join(sets) + " WHERE id=?", args)


def _log(con, sid: str, role: str, text: str, kind: str = "text") -> None:
    """Реплика в журнал диалога. Текст маскируется: ПД в базе чата не хранятся."""
    con.execute("INSERT INTO chat_messages (session_id, role, kind, text, created_at) VALUES (?,?,?,?,?)",
                (sid, role, kind, llm.mask_pd(text or "")[:4000], db.now()))
    con.execute("DELETE FROM chat_messages WHERE session_id=? AND id NOT IN "
                "(SELECT id FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT ?)",
                (sid, sid, MAX_HISTORY))


def _history(con, sid: str, limit: int = 20) -> list:
    rows = db.rows(con, "SELECT role, kind, text, created_at FROM chat_messages "
                        "WHERE session_id=? ORDER BY id DESC LIMIT ?", sid, int(limit))
    return list(reversed(rows))


def last_session(con, owner: str) -> Optional[dict]:
    """Последний живой диалог этого гостя или пользователя — «прошлый договор»."""
    rows = db.rows(con, "SELECT * FROM chat_sessions WHERE owner_key=? AND expires_at > ? "
                        "ORDER BY updated_at DESC LIMIT 1", owner, _iso(_now()))
    return rows[0] if rows else None


# --------------------------------------------------------------------------- #
#  Карточки
# --------------------------------------------------------------------------- #

def card_text(text: str, tone: str = "info") -> dict:
    return {"type": "text", "text": text, "tone": tone}


def card_chips(key: str, title: str, items: List[dict]) -> dict:
    return {"type": "chips", "key": key, "title": title, "items": items}


def card_checklist(title: str, items: List[dict], basis: str = None) -> dict:
    return {"type": "checklist", "title": title, "items": items, "basis": basis}


def card_fields(title: str, scope: str, fields: List[dict], note: str = None) -> dict:
    return {"type": "fields", "title": title, "scope": scope, "fields": fields, "note": note}


# --------------------------------------------------------------------------- #
#  Поля
# --------------------------------------------------------------------------- #

def _field_defs(class_code: str) -> tuple:
    f = ra.fields_for(class_code or "")
    return f.get("must") or [], f.get("optional") or []


def must_card(state: dict, lang: str) -> dict:
    """Четыре обязательных поля быстрого режима: класс/продукт, сумма, стоимость, регион."""
    must_defs, _ = _field_defs(str(state["must"].get("class_code") or ""))
    by_key = {d["key"]: d for d in must_defs}
    fields = []
    for key in ra.QUICK_MUST:
        d = dict(by_key.get(key) or {"key": key, "label": key, "type": "text"})
        d["value"] = state["must"].get(key)
        d["required"] = True
        fields.append(d)
    # продукт разрешаем вместо класса — так написано в правилах быстрого режима
    prod = dict(by_key.get("product_code") or {"key": "product_code", "label": "Продукт", "type": "text"})
    prod["value"] = state["must"].get("product_code")
    prod["required"] = False
    fields.append(prod)
    return card_fields(T("chat.must.title", lang), "must", fields, T("chat.must.note", lang))


def _impact_order(class_code: str) -> list:
    group = ra._group(ra.parse_classes(class_code or "")) if class_code else "other"
    return [k for k, _w, _why in ra.IMPACT.get(group, ra.IMPACT["other"])]


def optional_card(state: dict, lang: str) -> dict:
    """«Что ещё уточнить»: человек сам выбирает, какие поля заполнять. Порядок — по влиянию."""
    class_code = str(state["must"].get("class_code") or "")
    _must, opt_defs = _field_defs(class_code)
    order = _impact_order(class_code)
    why = {k: w for k, _p, w in ra.IMPACT.get(
        ra._group(ra.parse_classes(class_code)) if class_code else "other", ra.IMPACT["other"])}
    seen, fields = set(), []
    for d in opt_defs:
        if d["key"] in seen or d["key"] == "documents":
            continue
        seen.add(d["key"])
        item = dict(d)
        item["value"] = state["optional"].get(d["key"])
        item["why"] = why.get(d["key"])
        item["rank"] = order.index(d["key"]) if d["key"] in order else 99
        fields.append(item)
    fields.sort(key=lambda x: x["rank"])
    return card_fields(T("chat.optional.title", lang), "optional", fields, T("chat.optional.note", lang))


# --------------------------------------------------------------------------- #
#  Чек-лист документов по продукту
# --------------------------------------------------------------------------- #

def checklist_cards(con, state: dict, lang: str) -> List[dict]:
    """«В PDF должны быть: …» — источник тот же, что у /analytics/risk/docs."""
    must = state["must"]
    try:
        need = adocs.required_docs(con, str(must.get("product_code") or "")[:20],
                                   str(must.get("class_code") or "")[:20],
                                   str(must.get("object_type") or "")[:120])
    except Exception:
        return []
    closed = set()
    for d in state.get("docs") or []:
        closed.update(d.get("closes") or [])
    ai_seen = {k.strip().lower(): v for k, v in (state.get("pdf_contains") or {}).items()}
    items = []
    for d in need.get("required") or []:
        name = d.get("doc_name")
        ok = name in closed or bool(ai_seen.get(str(name).strip().lower()))
        items.append({"name": name, "ok": ok,
                      "why": d.get("legal_ref") or d.get("why"),
                      "source": "разбор файла" if name in closed else ("ИИ" if ok else None)})
    state["checklist"] = items
    return [card_checklist(T("chat.checklist.title", lang), items, need.get("basis_text"))]


# --------------------------------------------------------------------------- #
#  Загрузка файлов
# --------------------------------------------------------------------------- #

def _format_of(blob: bytes, filename: str) -> str:
    name = (filename or "").lower()
    if blob[:4] == b"%PDF":
        return "pdf"
    if blob[:3] == b"\xff\xd8\xff":
        return "jpg"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if blob[:2] == b"PK":
        return "xlsx" if name.endswith(".xlsx") else "docx"
    return name.rsplit(".", 1)[-1] if "." in name else ""


async def _handle_doc(request: Request, upload: UploadFile, blob: bytes, state: dict) -> dict:
    """PDF и DOCX идут в общий разбор договоров (app/analysis_docs.py): там и поля, и чек-лист."""
    upload.file.seek(0)
    must = state["must"]
    try:
        out = await adocs.upload_document(request, file=upload,
                                          class_code=str(must.get("class_code") or "")[:20],
                                          product_code=str(must.get("product_code") or "")[:20],
                                          object_type=str(must.get("object_type") or "")[:120])
    except Exception as e:
        detail = getattr(e, "detail", None) or type(e).__name__
        return {"name": upload.filename, "ok": False, "error": str(detail)}
    return {"name": out.get("filename"), "ok": True, "doc_id": out.get("id"),
            "kind": out.get("kind"), "status": out.get("status"),
            "closes": out.get("closes") or [], "notes": out.get("notes") or [],
            "prefill": (out.get("prefill") or {})}


def _apply_prefill(state: dict, prefill: dict) -> List[str]:
    """Значения из документа подставляем только в пустые поля — ручной ввод человека главнее."""
    filled = []
    for scope in ("must", "optional"):
        for k, v in (prefill.get(scope) or {}).items():
            value = v.get("value") if isinstance(v, dict) else v
            if value in (None, "") or state[scope].get(k) not in (None, ""):
                continue
            state[scope][k] = value
            filled.append(k)
    return filled


def _files_for_ai(saved: List[dict]) -> List[dict]:
    return [{"name": f["name"], "mime": f["mime"], "path": f["path"], "format": f["format"]}
            for f in saved]


# --------------------------------------------------------------------------- #
#  Анализ
# --------------------------------------------------------------------------- #

def run_analysis(request: Request, state: dict, mode: str, lang: str) -> dict:
    """Анализ риска тем же кодом, что и вкладка «Аналитика»: app/risk_api.risk_analyze."""
    body = risk_api.RiskIn(must=dict(state["must"]), optional=dict(state["optional"]),
                           # id документа бывает пустым (скан без текстового слоя) — такие не шлём
                           doc_ids=[str(d) for d in (state.get("doc_ids") or [])
                                    if d][:adocs.MAX_DOC_IDS],
                           mode="quick" if mode == "quick" else "full")
    res = risk_api.risk_analyze(body, request)
    if isinstance(res, JSONResponse):     # 422: чего не хватает — отдаём как есть, без исключения
        try:
            return {"ok": False, **json.loads(bytes(res.body).decode("utf-8"))}
        except Exception:
            return {"ok": False, "detail": "Анализ не запущен: проверьте заполненные поля"}
    return res


def result_cards(analysis: dict, state: dict, lang: str) -> List[dict]:
    s = analysis.get("summary") or {}
    fr = franchise_advice(analysis, state)
    nar = llm.risk_narrative(analysis, lang, franchise=fr if fr.get("ok") else None)
    card = {"type": "result",
            "summary": {"class_code": s.get("class_code"), "product_code": s.get("product_code"),
                        "object_type": s.get("object_type"), "region": s.get("region"),
                        "sum_insured": s.get("sum_insured"), "object_value": s.get("object_value"),
                        "ratio_sum_to_value": s.get("ratio_sum_to_value"),
                        "rate_applied_pct": s.get("rate_applied_pct"),
                        "rate_min_pct": s.get("rate_min_pct"), "premium": s.get("premium")},
            "level": analysis.get("level") or {},
            "completeness": analysis.get("completeness") or {},
            "assumptions": analysis.get("assumptions") or [],
            "documents": analysis.get("documents") or {},
            "franchise": fr,
            "narrative": nar.get("text"), "narrative_source": nar.get("source"),
            "mode": analysis.get("mode")}
    cards = [card]
    if analysis.get("assumptions"):
        cards.append(card_text(T("chat.assumed.note", lang), "warn"))
    cards.append(card_chips("next", T("chat.next.title", lang), [
        {"value": "legal", "label": T("chat.next.legal", lang)},
        {"value": "refine", "label": T("chat.next.refine", lang)},
        {"value": "restart", "label": T("chat.next.restart", lang)}]))
    return cards


# --------------------------------------------------------------------------- #
#  Служебное для обработчиков
# --------------------------------------------------------------------------- #

def _user(request: Request):
    u = request.scope.get("surveyor_user")
    if u is None:
        token = auth.request_token(request)
        if token:
            with db.tx() as con:
                u = auth.session_user(con, token)
    return u


def _cookie_7d(gid: str) -> bytes:
    """Та же cookie «gid», но на 7 дней: диалог должен пережить перезагрузку страницы."""
    parts = [f"{guest.COOKIE}={gid}", "Path=/", f"Max-Age={TTL_SEC}", "HttpOnly", "SameSite=Lax"]
    if guest.cookie_secure():
        parts.append("Secure")
    return "; ".join(parts).encode("latin-1")


def _reply(request: Request, payload: dict, status: int = 200) -> JSONResponse:
    res = JSONResponse(status_code=status, content=payload)
    gid = guest.from_request(request)
    if gid:
        res.raw_headers.append((b"set-cookie", _cookie_7d(gid)))
    return res


def _fail(request: Request, message: str, status: int = 404) -> JSONResponse:
    return _reply(request, {"ok": False, "detail": message,
                            "cards": [card_text(message, "warn")]}, status)


def _lang_of(request: Request, given: str = None, row: dict = None) -> str:
    if given and i18n.normalize_lang(given):
        return i18n.normalize_lang(given)
    if row and i18n.normalize_lang(row.get("lang")):
        return i18n.normalize_lang(row["lang"])
    return i18n.pick_lang(None, request.cookies.get(i18n.COOKIE))


def _state_payload(row: dict, state: dict, lang: str, cards: List[dict], step: str) -> dict:
    return {"ok": True, "session_id": row["id"], "lang": lang, "step": step,
            "state": {"must": state["must"], "optional": state["optional"],
                      "docs": [{"name": d.get("name"), "kind": d.get("kind"),
                                "status": d.get("status"), "ok": d.get("ok")}
                               for d in state.get("docs") or []],
                      "has_analysis": bool(state.get("analysis")),
                      "previous_contract": state.get("previous_contract")},
            "cards": cards,
            "ai": {"connected": llm.enabled(), "provider": llm.provider(),
                   "files": llm.supports_files()}}


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #

@router.post("/chat/start")
def chat_start(request: Request, body: dict = Body(default={})):
    """
    Новый диалог. Тело: {"lang": "ru|uz|en"} (необязательно).
    Ответ: приветствие и, если у этого гостя есть прошлый диалог, вопрос «старый или новый договор».
    """
    user = _user(request)
    owner = guest.owner_of(request, user)
    if not owner:
        return _fail(request, "Не удалось опознать сессию — откройте приложение заново", 400)
    lang = _lang_of(request, (body or {}).get("lang"))
    sid = secrets.token_hex(12)
    now = _now()
    with db.tx() as con:
        ensure_tables(con)
        cleanup(con)
        prev = last_session(con, owner)
        state = new_state()
        if prev:
            prev_state = _load_state(prev)
            if prev_state["must"].get("class_code") or prev_state.get("analysis"):
                state["previous_contract"] = {
                    "session_id": prev["id"], "updated_at": prev["updated_at"],
                    "class_code": prev_state["must"].get("class_code"),
                    "product_code": prev_state["must"].get("product_code"),
                    "object_type": prev_state["must"].get("object_type")}
        con.execute("INSERT INTO chat_sessions (id, owner_key, user_id, lang, step, state_json,"
                    " created_at, updated_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (sid, owner, (user or {}).get("id") or 0, lang,
                     "contract_mode" if state["previous_contract"] else "upload",
                     json.dumps(state, ensure_ascii=False), _iso(now), _iso(now),
                     _iso(now + timedelta(seconds=TTL_SEC))))
        cards = [card_text(T("chat.greeting", lang))]
        if state["previous_contract"]:
            p = state["previous_contract"]
            cards.append(card_chips("contract_mode", T("chat.contract_mode.title", lang), [
                {"value": "previous", "label": T("chat.contract_mode.previous", lang,
                                                 what=p.get("object_type") or p.get("class_code") or "")},
                {"value": "new", "label": T("chat.contract_mode.new", lang)}]))
            step = "contract_mode"
        else:
            cards.append(card_text(T("chat.upload.hint", lang)))
            # карточку четырёх полей даём сразу: путь «старт → выбрал объект → результат»
            # должен работать и без загрузки файлов
            cards.append(must_card(state, lang))
            step = "upload"
        _log(con, sid, "assistant", T("chat.greeting", lang))
        row = db.rows(con, "SELECT * FROM chat_sessions WHERE id=?", sid)[0]
    return _reply(request, _state_payload(row, state, lang, cards, step))


@router.get("/chat/state")
def chat_state(request: Request, session_id: str = ""):
    """Состояние диалога и последние реплики — чтобы интерфейс восстановился после перезагрузки."""
    owner = guest.owner_of(request, _user(request))
    with db.tx() as con:
        ensure_tables(con)
        row = _session(con, session_id, owner)
        if not row:
            return _fail(request, NOT_FOUND)
        state = _load_state(row)
        history = _history(con, row["id"])
    lang = _lang_of(request, None, row)
    payload = _state_payload(row, state, lang, [], row["step"])
    payload["messages"] = history
    payload["checklist"] = state.get("checklist") or []
    if state.get("analysis"):
        payload["cards"] = result_cards(state["analysis"], state, lang)
    return _reply(request, payload)


@router.post("/chat/answer")
def chat_answer(request: Request, body: dict = Body(...)):
    """
    Ответ на чип или заполнение полей.
    Тело: {"session_id", "key": "contract_mode|next|fields", "value": ..., "fields": {...},
           "scope": "must|optional"}.
    """
    owner = guest.owner_of(request, _user(request))
    sid = str((body or {}).get("session_id") or "")
    with db.tx() as con:
        ensure_tables(con)
        row = _session(con, sid, owner)
        if not row:
            return _fail(request, NOT_FOUND)
        lang = _lang_of(request, body.get("lang"), row)
        state = _load_state(row)
        key = str(body.get("key") or "fields")
        cards, step = [], row["step"]
        if key == "contract_mode":
            if str(body.get("value")) == "previous" and state.get("previous_contract"):
                prev = db.rows(con, "SELECT * FROM chat_sessions WHERE id=? AND owner_key=?",
                               state["previous_contract"]["session_id"], owner)
                if prev:
                    old = _load_state(prev[0])
                    state["must"] = dict(old.get("must") or {})
                    state["optional"] = dict(old.get("optional") or {})
                    cards.append(card_text(T("chat.contract_mode.loaded", lang)))
            else:
                state["must"], state["optional"] = {}, {}
                cards.append(card_text(T("chat.contract_mode.new_ok", lang)))
            cards.append(card_text(T("chat.upload.hint", lang)))
            step = "upload"
        elif key == "next":
            value = str(body.get("value") or "")
            if value == "refine":
                cards.append(optional_card(state, lang))
                step = "optional"
            elif value == "legal":
                cards.append(card_text(T("chat.next.legal_hint", lang)))
                step = "followup"
            else:
                cards.append(card_text(T("chat.next.restart_hint", lang)))
                step = "upload"
        else:
            scope = "optional" if str(body.get("scope")) == "optional" else "must"
            fields = body.get("fields")
            if not isinstance(fields, dict):
                fields = {str(body.get("key")): body.get("value")} if body.get("key") else {}
            if len(fields) > 60:
                return _fail(request, "Слишком много полей в одном ответе", 422)
            for k, v in fields.items():
                if v in (None, ""):
                    state[scope].pop(str(k)[:40], None)
                else:
                    state[scope][str(k)[:40]] = v
            v = ra.validate_quick(state["must"], state["optional"])
            if not v.get("ok"):
                cards.append(must_card(state, lang))
                cards.append(card_text(T("chat.must.missing", lang), "warn"))
                step = "must"
            else:
                cards.append(card_text(T("chat.must.ready", lang)))
                cards.append(optional_card(state, lang))
                step = "optional"
                cards += checklist_cards(con, state, lang)
        _save(con, sid, step, state, lang)
        _log(con, sid, "user", json.dumps(body.get("fields") or body.get("value") or "",
                                          ensure_ascii=False), "answer")
        row = db.rows(con, "SELECT * FROM chat_sessions WHERE id=?", sid)[0]
    return _reply(request, _state_payload(row, state, lang, cards, step))


@router.post("/chat/upload")
async def chat_upload(request: Request, session_id: str = Form(""),
                      files: List[UploadFile] = File(...)):
    """
    Загрузка одним действием: договор (PDF/DOCX), общий PDF со всеми документами, фото (JPG/PNG),
    Excel. Поля формы: session_id и files (несколько). PDF и DOCX разбирает app/analysis_docs.py,
    фото и сканы — Gemini (если подключён), Excel — app/ingest текстом.
    """
    owner = guest.owner_of(request, _user(request))
    with db.tx() as con:
        ensure_tables(con)
        row = _session(con, session_id, owner)
    if not row:
        return _fail(request, NOT_FOUND)
    lang = _lang_of(request, None, row)
    state = _load_state(row)
    if len(files) > MAX_FILES:
        return _fail(request, T("chat.upload.too_many", lang, n=MAX_FILES), 413)

    folder = DIR / row["id"]
    folder.mkdir(parents=True, exist_ok=True)
    results, ai_files, notes = [], [], []
    for up in files:
        blob = await up.read(MAX_BYTES + 1)
        if not blob:
            results.append({"name": up.filename, "ok": False, "error": "файл пустой"})
            continue
        if len(blob) > MAX_BYTES:
            results.append({"name": up.filename, "ok": False,
                            "error": T("chat.upload.too_big", lang, mb=MAX_BYTES // (1024 * 1024))})
            continue
        fmt = _format_of(blob, up.filename)
        if fmt in DOC_FORMATS:
            got = await _handle_doc(request, up, blob, state)
            if got.get("ok"):
                if got.get("doc_id"):
                    state["doc_ids"] = list(dict.fromkeys(
                        [d for d in (state.get("doc_ids") or []) if d] + [str(got["doc_id"])]))
                filled = _apply_prefill(state, got.get("prefill") or {})
                got["filled"] = filled
                # скан без текстового слоя — отдаём его ИИ картинкой
                if got.get("status") == ingest.ST_OCR:
                    path = folder / f"{secrets.token_hex(6)}.pdf"
                    path.write_bytes(blob)
                    ai_files.append({"name": up.filename, "mime": FMT_MIME["pdf"],
                                     "path": str(path), "format": "pdf"})
            state.setdefault("docs", []).append(got)
            results.append(got)
        elif fmt in AI_FORMATS or fmt in TEXT_FORMATS:
            path = folder / f"{secrets.token_hex(6)}.{fmt}"
            path.write_bytes(blob)
            item = {"name": ingest._mask_str(Path(up.filename or "").name)[:200] or ("файл." + fmt),
                    "ok": True, "kind": "фото объекта" if fmt in AI_FORMATS else "таблица",
                    "status": "принят", "closes": []}
            state.setdefault("docs", []).append(item)
            results.append(item)
            ai_files.append({"name": item["name"], "mime": FMT_MIME.get(fmt, "application/octet-stream"),
                             "path": str(path), "format": "jpg" if fmt == "jpeg" else fmt})
        else:
            results.append({"name": up.filename, "ok": False, "error": T("chat.upload.format", lang)})

    ai = {"ok": False}
    if ai_files:
        names = [c.get("name") for c in (state.get("checklist") or [])] or None
        ai = llm.extract_from_files(ai_files, product_hint=str(state["must"].get("product_code") or ""),
                                    checklist=names)
        if ai.get("ok"):
            # характеристики объекта с фотографии кладём только в пустые поля
            obj = ai.get("object") or {}
            for k in ("construction", "floors", "wear_pct"):
                if obj.get(k) not in (None, "") and state["must"].get(k) in (None, ""):
                    state["must"][k] = obj[k]
            if obj.get("protection") and state["optional"].get("protection") in (None, ""):
                state["optional"]["protection_note"] = str(obj["protection"])[:200]
            state["pdf_contains"] = dict(state.get("pdf_contains") or {}) | (ai.get("pdf_contains") or {})
            state["ai_notes"] = (ai.get("notes") or [])[:10]
        notes += ai.get("notes") or []

    cards = [card_text(T("chat.upload.done", lang, n=sum(1 for r in results if r.get("ok"))))]
    bad = [r for r in results if not r.get("ok")]
    if bad:
        cards.append(card_text("; ".join(f"{r.get('name')}: {r.get('error')}" for r in bad), "warn"))
    # про разбор снимков — ровно одно сообщение по факту: нашли поля, не нашли, или не разобрали
    if ai_files:
        found = sorted({k for d in (ai.get("documents") or []) for k in (d.get("fields") or {})} |
                       set((ai.get("object") or {}).keys()))
        if ai.get("ok") and found:
            cards.append(card_text(T("chat.upload.ai_found", lang, fields=", ".join(found[:12])), "info"))
        elif ai.get("ok"):
            cards.append(card_text(T("chat.upload.ai_empty", lang), "warn"))
        else:
            cards.append(card_text(T("chat.upload.ai_fail", lang,
                                     reason=str(ai.get("reason") or "")[:200]), "warn"))
        # замечания о «нужно распознавание» после удачного разбора только путают
        if ai.get("ok") and found:
            notes = [n for n in notes if "распозна" not in str(n).lower()]
    if notes:
        cards.append(card_text(" ".join(str(n) for n in notes[:6]), "info"))
    with db.tx() as con:
        cards += checklist_cards(con, state, lang)
        v = ra.validate_quick(state["must"], state["optional"])
        step = "optional" if v.get("ok") else "must"
        cards.append(optional_card(state, lang) if v.get("ok") else must_card(state, lang))
        _save(con, row["id"], step, state, lang)
        _log(con, row["id"], "user", f"загружено файлов: {len(results)}", "upload")
        row = db.rows(con, "SELECT * FROM chat_sessions WHERE id=?", row["id"])[0]
    payload = _state_payload(row, state, lang, cards, step)
    payload["files"] = results
    payload["extracted"] = {"ok": bool(ai.get("ok")), "documents": ai.get("documents") or [],
                            "object": ai.get("object") or {}, "source": ai.get("source") or "правила"}
    return _reply(request, payload)


@router.post("/chat/analyze")
def chat_analyze(request: Request, body: dict = Body(default={})):
    """Анализ: {"session_id", "mode": "quick|full"}. Ставка, франшиза и пояснение одним ответом."""
    owner = guest.owner_of(request, _user(request))
    sid = str((body or {}).get("session_id") or "")
    with db.tx() as con:
        ensure_tables(con)
        row = _session(con, sid, owner)
        if not row:
            return _fail(request, NOT_FOUND)
        state = _load_state(row)
    lang = _lang_of(request, (body or {}).get("lang"), row)
    mode = "full" if str((body or {}).get("mode") or "quick").lower() == "full" else "quick"
    res = run_analysis(request, state, mode, lang)
    if not res.get("ok"):
        cards = [card_text(res.get("detail") or T("chat.must.missing", lang), "warn"),
                 must_card(state, lang)]
        with db.tx() as con:
            _save(con, sid, "must", state, lang)
            row = db.rows(con, "SELECT * FROM chat_sessions WHERE id=?", sid)[0]
        payload = _state_payload(row, state, lang, cards, "must")
        payload["ok"] = False
        payload["missing"] = res.get("missing") or []
        payload["errors"] = res.get("errors") or {}
        return _reply(request, payload, 200)
    state["analysis"] = res
    cards = result_cards(res, state, lang)
    with db.tx() as con:
        _save(con, sid, "result", state, lang)
        _log(con, sid, "assistant", "анализ выполнен", "result")
        row = db.rows(con, "SELECT * FROM chat_sessions WHERE id=?", sid)[0]
    return _reply(request, _state_payload(row, state, lang, cards, "result"))


@router.post("/chat/message")
def chat_message(request: Request, body: dict = Body(...)):
    """
    Свободный текст: {"session_id", "text"}. Отвечает ИИ (с учётом уже собранных данных);
    если ИИ не подключён — честная подсказка по правилам, что делать дальше.
    """
    owner = guest.owner_of(request, _user(request))
    sid = str((body or {}).get("session_id") or "")
    text = str((body or {}).get("text") or "").strip()[:MAX_TEXT]
    if not text:
        return _fail(request, "Пустой вопрос", 422)
    with db.tx() as con:
        ensure_tables(con)
        row = _session(con, sid, owner)
        if not row:
            return _fail(request, NOT_FOUND)
        state = _load_state(row)
        lang = _lang_of(request, (body or {}).get("lang"), row)
        history = _history(con, sid, 10)
        _log(con, sid, "user", text)
    msgs = [{"role": "assistant" if h["role"] == "assistant" else "user", "content": h["text"]}
            for h in history if h["kind"] == "text"]
    context = {"собрано": state["must"], "уточнено": state["optional"],
               "анализ_выполнен": bool(state.get("analysis"))}
    if state.get("analysis"):
        context["итог"] = llm.risk_summary_data(state["analysis"])
    msgs.append({"role": "user", "content": "Данные диалога (JSON):\n" +
                 json.dumps(context, ensure_ascii=False, default=str) + "\n\nВопрос: " + text})
    got = llm.chat_dialog(msgs, lang)
    answer = got.get("text") or ai_off_text(got.get("reason"), lang)
    with db.tx() as con:
        _log(con, sid, "assistant", answer)
        _save(con, sid, row["step"], state, lang)
        row = db.rows(con, "SELECT * FROM chat_sessions WHERE id=?", sid)[0]
    cards = [card_text(answer, "info" if got.get("ok") else "warn")]
    payload = _state_payload(row, state, lang, cards, row["step"])
    payload["source"] = got.get("source") or "правила"
    return _reply(request, payload)


@router.post("/chat/lang")
def chat_lang(request: Request, body: dict = Body(...)):
    """
    Смена языка: {"session_id", "lang": "ru|uz|en", "translate": ["свободный текст", ...]}.
    Структурные строки интерфейс берёт из /i18n/{lang}.json; свободный текст (пояснение ИИ,
    заметки) переводится здесь, с кэшем по хэшу «текст + язык + модель».
    """
    owner = guest.owner_of(request, _user(request))
    sid = str((body or {}).get("session_id") or "")
    lang = i18n.normalize_lang((body or {}).get("lang")) or "ru"
    with db.tx() as con:
        ensure_tables(con)
        row = _session(con, sid, owner)
        if not row:
            return _fail(request, NOT_FOUND)
        state = _load_state(row)
        _save(con, sid, row["step"], state, lang)
        row = db.rows(con, "SELECT * FROM chat_sessions WHERE id=?", sid)[0]
    items = [str(x)[:MAX_TEXT] for x in ((body or {}).get("translate") or [])][:10]
    translated = [llm.translate(x, lang) for x in items]
    cards = [card_text(T("chat.lang.changed", lang))]
    if state.get("analysis"):
        cards += result_cards(state["analysis"], state, lang)
    payload = _state_payload(row, state, lang, cards, row["step"])
    payload["translated"] = [{"source": t_["source"], "text": t_["text"]} for t_ in translated]
    return _reply(request, payload)
