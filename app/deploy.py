"""
Страница запуска и обслуживания системы: /admin/deploy.

Что здесь: состояние базы и обновлений, настройки (ИИ, бот, режим персональных данных),
резервные копии, проверка схемы на переносимость в PostgreSQL и чек-лист готовности.

Опасные действия (восстановление из копии, переключение режима персональных данных на «prod»)
выполняются только с confirm=true — иначе понятный отказ.

Ключи и токены наружу не отдаются: только маска вида "sk-...abcd".
"""
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import db, llm

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "app" / "deploy.html"
BACKUPS = ROOT / "data" / "backups"

sys.path.insert(0, str(ROOT / "tools"))
import backup as backup_tool             # noqa: E402
import restore as restore_tool           # noqa: E402
import schema_check                      # noqa: E402

# Пункты чек-листа готовности. Заводятся при первом обращении, дальше живут в базе.
CHECKLIST = [
    ("postgres", "PostgreSQL вместо SQLite"),
    ("domain", "Свой домен"),
    ("https", "HTTPS (сертификат)"),
    ("backup", "Резервное копирование по расписанию"),
    ("admin", "Назначен администратор системы"),
]

# расписания, которые крутятся внутри сервера (app/main.py, app/team.py, app/statagency.py)
SCHEDULES = [
    ("Отчёты НАПП", "раз в сутки", "market_stats"),
    ("Автоимпорт выгрузок из data/inbox/portfolio", "раз в 10 минут", "inbox"),
    ("Открытые данные агентства статистики", "раз в сутки", "stat_uz"),
    ("Ежедневный доклад", "каждый день в 08:00", "report"),
]


# --------------------------------------------------------------------------- #
#  Сведения о системе
# --------------------------------------------------------------------------- #

def version_code() -> str:
    """Метка версии: дата последнего изменения кода и число файлов (git в проекте нет)."""
    files = [f for d in ("app", "tools", "db") for f in (ROOT / d).glob("*.*") if f.is_file()]
    if not files:
        return "нет файлов"
    last = max(f.stat().st_mtime for f in files)
    return f"{datetime.fromtimestamp(last):%Y-%m-%d} · {len(files)} файлов"


def db_info() -> dict:
    path = db.DB_PATH
    size_mb = round(path.stat().st_size / 1024 / 1024, 2) if path.exists() else 0
    tables = []
    try:
        with db.tx() as con:
            names = [r["name"] for r in db.rows(
                con, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            for n in names:
                try:
                    tables.append({"name": n, "rows": con.execute("SELECT COUNT(*) FROM " + n).fetchone()[0]})
                except Exception:
                    tables.append({"name": n, "rows": None})
    except Exception as e:
        tables = [{"name": "не удалось прочитать базу: %s" % type(e).__name__, "rows": None}]
    return {"path": str(path), "size_mb": size_mb, "tables": tables}


def _one(con, sql) -> Optional[str]:
    try:
        v = con.execute(sql).fetchone()
        return v[0] if v and v[0] else None
    except Exception:
        return None


def updates() -> dict:
    """Когда в последний раз обновлялись внешние данные. None = ни разу."""
    with db.tx() as con:
        return {
            "napp": _one(con, "SELECT MAX(report_date) FROM market_stats"),
            "stat_uz": _one(con, "SELECT MAX(fetched_at) FROM stat_series"),
            "knowledge": _one(con, "SELECT MAX(ts) FROM knowledge_log"),
            "report": _one(con, "SELECT MAX(report_date) FROM daily_reports"),
        }


def schedules() -> list:
    upd = updates()
    last = {"market_stats": upd["napp"], "stat_uz": upd["stat_uz"], "report": upd["report"], "inbox": None}
    with db.tx() as con:
        last["inbox"] = _one(con, "SELECT MAX(ts) FROM audit WHERE action='автоимпорт выгрузок'")
    return [{"name": name, "period": period, "last_run": last.get(key)} for name, period, key in SCHEDULES]


@router.get("/deploy/status")
def deploy_status():
    st = llm.status()
    return {"version": {"code": version_code()},
            "db": db_info(),
            "updates": updates(),
            "llm": {"provider": st["provider"], "model": st["model"],
                    "connected": st["connected"], "reason": st["reason"]},
            "schedules": schedules(),
            "settings": {"pd_mode": llm.get("PD_MODE"), "server_url": llm.get("SERVER_URL")}}


# --------------------------------------------------------------------------- #
#  Настройки
# --------------------------------------------------------------------------- #

class SettingsIn(BaseModel):
    provider: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    telegram_bot_token: str = ""
    pd_mode: str = ""
    server_url: str = ""
    confirm: bool = False          # нужен только для смены режима персональных данных


def settings_view() -> dict:
    return {"provider": llm.provider(),
            "base_url": llm.base_url(),
            "model": llm.model(),
            "api_key": llm.mask_key(llm.api_key()),
            "api_key_set": bool(llm.api_key()),
            "telegram_bot_token": llm.mask_key(llm.get("TELEGRAM_BOT_TOKEN")),
            "telegram_bot_token_set": bool(llm.get("TELEGRAM_BOT_TOKEN")),
            "pd_mode": llm.get("PD_MODE"),
            "server_url": llm.get("SERVER_URL"),
            "providers": [{"code": k, "name": v["name"]} for k, v in llm.PROVIDERS.items()],
            "hint": "Пустое поле означает «не менять». Ключи показываются только маской."}


@router.get("/deploy/settings")
def get_settings():
    return settings_view()


@router.post("/deploy/settings")
def post_settings(body: SettingsIn):
    pd_mode = (body.pd_mode or "").strip().lower()
    if pd_mode and pd_mode not in ("test", "prod"):
        raise HTTPException(400, "Режим персональных данных бывает только «test» или «prod»")
    if pd_mode == "prod" and not body.confirm:
        raise HTTPException(400, "Режим «prod» означает работу с настоящими персональными данными граждан. "
                                 "Он допустим только на сервере в Узбекистане (правило проекта № 8). "
                                 "Повторите с подтверждением (confirm=true)")
    provider = (body.provider or "").strip().lower()
    if provider and provider not in llm.PROVIDERS:
        raise HTTPException(400, "Провайдер бывает только: " + ", ".join(llm.PROVIDERS))
    changed = llm.set_many({
        "LLM_PROVIDER": provider,
        "LLM_BASE_URL": body.base_url,
        "LLM_MODEL": body.model,
        "LLM_API_KEY": body.api_key,
        "TELEGRAM_BOT_TOKEN": body.telegram_bot_token,
        "PD_MODE": pd_mode,
        "SERVER_URL": body.server_url,
    })
    return {"ok": True, "changed": changed, "settings": settings_view()}


# --------------------------------------------------------------------------- #
#  Резервные копии
# --------------------------------------------------------------------------- #

class RestoreIn(BaseModel):
    file: str
    confirm: bool = False


@router.post("/deploy/backup")
def make_backup():
    res = backup_tool.make("из админки")
    with db.tx() as con:
        db.audit(con, "админ", "резервная копия", "backup", {"file": res["file"], "size_mb": res["size_mb"]})
    return {"ok": True, "file": res["file"], "size_mb": res["size_mb"], "files": res["files"]}


@router.get("/deploy/backups")
def list_backups():
    return backup_tool.listing()


@router.get("/deploy/backup/download")
def download_backup(file: str):
    p = (BACKUPS / Path(file).name)
    if not p.exists() or p.resolve().parent != BACKUPS.resolve():
        raise HTTPException(404, "Такой копии нет")
    return FileResponse(str(p), media_type="application/zip", filename=p.name)


@router.post("/deploy/restore")
def do_restore(body: RestoreIn):
    """Опасно: заменяет рабочую базу. Без confirm=true — отказ с объяснением."""
    try:
        res = restore_tool.restore(body.file, confirm=body.confirm)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    if not res["applied"]:
        raise HTTPException(400, res["reason"])
    with db.tx() as con:
        db.audit(con, "админ", "восстановление из копии", "backup", {"file": body.file})
    return res


# --------------------------------------------------------------------------- #
#  Проверка схемы и чек-лист
# --------------------------------------------------------------------------- #

@router.get("/deploy/schema-check")
def deploy_schema_check():
    res = schema_check.check()
    return {"ok": res["ok"], "problems": [{"level": p["level"], "text": p["text"]} for p in res["problems"]],
            "summary": res["summary"]}


class ChecklistIn(BaseModel):
    key: str
    done: bool = False
    note: str = ""


def _ensure_checklist(con):
    for key, title in CHECKLIST:
        if not db.rows(con, "SELECT 1 FROM deploy_checklist WHERE key=?", key):
            con.execute("INSERT INTO deploy_checklist (key, title, done, note, updated_at) VALUES (?,?,0,'',?)",
                        (key, title, db.now()))


@router.get("/deploy/checklist")
def get_checklist():
    order = {k: i for i, (k, _) in enumerate(CHECKLIST)}
    with db.tx() as con:
        _ensure_checklist(con)
        items = db.rows(con, "SELECT key, title, done, note FROM deploy_checklist")
    items.sort(key=lambda r: order.get(r["key"], 99))
    return [{"key": r["key"], "title": r["title"], "done": bool(r["done"]), "note": r["note"] or ""}
            for r in items]


@router.post("/deploy/checklist")
def set_checklist(body: ChecklistIn):
    with db.tx() as con:
        _ensure_checklist(con)
        if not db.rows(con, "SELECT 1 FROM deploy_checklist WHERE key=?", body.key):
            raise HTTPException(404, "Неизвестный пункт чек-листа: " + body.key)
        con.execute("UPDATE deploy_checklist SET done=?, note=?, updated_at=? WHERE key=?",
                    (1 if body.done else 0, body.note or "", db.now(), body.key))
        db.audit(con, "админ", "чек-лист запуска", "deploy:" + body.key, {"done": body.done})
    return get_checklist()


# --------------------------------------------------------------------------- #
#  Страница
# --------------------------------------------------------------------------- #

STUB = """<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Запуск и обслуживание</title>
<body style="background:#0F1418;color:#E6ECF0;font:15px/1.6 system-ui;padding:32px">
<h1 style="font-size:20px">Страница готовится</h1>
<p>Файл <code>app/deploy.html</code> ещё не выложен. Данные уже отдаются:</p>
<ul>
<li><code>GET /deploy/status</code> — состояние системы</li>
<li><code>GET /deploy/settings</code>, <code>POST /deploy/settings</code> — настройки</li>
<li><code>POST /deploy/backup</code>, <code>GET /deploy/backup/download?file=...</code> — копии</li>
<li><code>GET /deploy/schema-check</code> — переносимость схемы в PostgreSQL</li>
<li><code>GET /deploy/checklist</code>, <code>POST /deploy/checklist</code> — чек-лист готовности</li>
</ul>
</body></html>"""


@router.get("/admin/deploy", response_class=HTMLResponse)
def deploy_page():
    """Страницу верстает интерфейсный поток (app/deploy.html); пока её нет — заглушка со ссылками."""
    if PAGE.exists():
        return PAGE.read_text(encoding="utf-8")
    return STUB
