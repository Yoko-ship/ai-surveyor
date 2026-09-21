"""
Команда агентов: делегирование задач и ежедневный доклад.

Делегирование. Заказчик ставит задачу одной фразой (POST /tasks). Руководитель разбирает её по ключевым словам
и раздаёт исполнителям как подзадачи; контролёр всегда последний. Исполнители (агенты Claude Code surveyor-*)
читают очередь через GET /tasks?assignee=..., отмечают «в работе» и «сделана» с результатом, а вопросы к заказчику
кладут через /tasks/{id}/ask — они попадают в доклад. Здесь — очередь, маршрутизация и учёт.

Ежедневный доклад. В 08:00 сервер собирает отчёт за прошедшие сутки: запросы и вердикты, портфельные загрузки,
рынок, калибровка, ёмкость, задачи агентов, журнал, что нужно от заказчика. Хранится в daily_reports и в data/reports.
"""
import json
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from . import background, db, web

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "data" / "reports"
router = APIRouter()

AGENT_NAMES = {"lead": "Руководитель", "law": "Юрист", "data": "Статистик", "actuary": "Актуарий",
               "backend": "Разработчик", "ui": "Дизайнер", "reviewer": "Контролёр"}

# Маршрутизация руководителя: по каким словам какая часть задачи уходит какому агенту
ROUTES = [
    ("law", r"закон|полож|напп|норм|прав|статья|lex\.uz|регулятор|лиценз|кодекс|указ|постановлен|верифик"),
    ("data", r"статист|отч[её]т|выгруз|excel|xlsx|данн|рынок|динамик|убыточн|калибров|обнов"),
    ("actuary", r"ставк|тариф|преми|коэффициент|надбавк|резерв|удержан|[её]мкост|pml|риск|франшиз|расч[её]т"),
    ("backend", r"api|база|сервер|интеграц|импорт|экспорт|телеграм|telegram|railway|развер|docker|вход|парол"),
    ("ui", r"экран|интерфейс|дизайн|страниц|кнопк|тем[аы]|логотип|офис|график|паутин|pdf|печат"),
]


def route(text: str) -> list:
    t = text.lower()
    out = [code for code, pat in ROUTES if re.search(pat, t)]
    return out or ["backend"]


# ---------- задачи ----------

class TaskIn(BaseModel):
    title: str
    detail: str = ""
    author: str = "заказчик"
    assignee: Optional[str] = None       # если не задан — руководитель распределит сам


class TaskUpdate(BaseModel):
    who: str
    result: str = ""


def create_task(con, t: TaskIn) -> dict:
    now = db.now()
    if t.assignee:
        cur = con.execute("INSERT INTO agent_tasks (created_at, author, assignee, title, detail) VALUES (?,?,?,?,?)",
                          (now, t.author, t.assignee, t.title, t.detail))
        db.audit(con, t.author, "поставлена задача", f"task:{cur.lastrowid}", {"assignee": t.assignee, "title": t.title})
        return {"task_id": cur.lastrowid, "subtasks": []}
    cur = con.execute("INSERT INTO agent_tasks (created_at, author, assignee, title, detail, status) VALUES (?,?,?,?,?,?)",
                      (now, t.author, "lead", t.title, t.detail, "в работе"))
    parent = cur.lastrowid
    subs = []
    for code in route(t.title + " " + t.detail):
        c2 = con.execute("INSERT INTO agent_tasks (parent_id, created_at, author, assignee, title, detail) VALUES (?,?,?,?,?,?)",
                         (parent, now, "lead", code, f"{AGENT_NAMES[code]}: {t.title}", t.detail))
        subs.append({"task_id": c2.lastrowid, "assignee": code})
    c3 = con.execute("INSERT INTO agent_tasks (parent_id, created_at, author, assignee, title, detail) VALUES (?,?,?,?,?,?)",
                     (parent, now, "lead", "reviewer", f"Контролёр: проверить результат — {t.title}", "после исполнителей"))
    subs.append({"task_id": c3.lastrowid, "assignee": "reviewer"})
    db.audit(con, "lead", "задача распределена", f"task:{parent}",
             {"title": t.title, "assignees": [s["assignee"] for s in subs]})
    return {"task_id": parent, "subtasks": subs}


@router.post("/tasks")
def post_task(t: TaskIn):
    with db.tx() as con:
        return create_task(con, t)


@router.get("/tasks")
def list_tasks(assignee: Optional[str] = None, status: Optional[str] = None, limit: int = 200):
    sql, args = "SELECT * FROM agent_tasks WHERE 1=1", []
    if assignee:
        sql += " AND assignee=?"; args.append(assignee)
    if status:
        sql += " AND status=?"; args.append(status)
    sql += " ORDER BY id DESC LIMIT ?"; args.append(limit)
    with db.tx() as con:
        return db.rows(con, sql, *args)


@router.post("/tasks/{tid}/start")
def start_task(tid: int, u: TaskUpdate):
    with db.tx() as con:
        con.execute("UPDATE agent_tasks SET status='в работе', started_at=? WHERE id=?", (db.now(), tid))
        db.audit(con, u.who, "взял задачу", f"task:{tid}")
    return {"ok": True}


@router.post("/tasks/{tid}/done")
def done_task(tid: int, u: TaskUpdate):
    with db.tx() as con:
        t = db.rows(con, "SELECT * FROM agent_tasks WHERE id=?", tid)
        if not t:
            raise HTTPException(404, "Задача не найдена")
        con.execute("UPDATE agent_tasks SET status='сделана', done_at=?, result=? WHERE id=?", (db.now(), u.result, tid))
        db.audit(con, u.who, "задача сделана", f"task:{tid}", {"result": u.result[:300]})
        pid = t[0]["parent_id"]
        if pid:  # родительская закрывается, когда закрыты все подзадачи
            left = con.execute("SELECT COUNT(*) FROM agent_tasks WHERE parent_id=? AND status NOT IN ('сделана','отменена')",
                               (pid,)).fetchone()[0]
            if left == 0:
                con.execute("UPDATE agent_tasks SET status='сделана', done_at=? WHERE id=?", (db.now(), pid))
                db.audit(con, "lead", "задача закрыта", f"task:{pid}")
    return {"ok": True}


@router.post("/tasks/{tid}/ask")
def ask_customer(tid: int, u: TaskUpdate):
    """Исполнитель упёрся в вопрос к заказчику: задача переходит в «ждёт заказчика» и попадает в доклад."""
    with db.tx() as con:
        con.execute("UPDATE agent_tasks SET status='ждёт заказчика', result=? WHERE id=?", (u.result, tid))
        db.audit(con, u.who, "вопрос заказчику", f"task:{tid}", {"question": u.result[:300]})
    return {"ok": True}


# ---------- ежедневный доклад ----------

def _fmt(n):
    return "—" if n is None else f"{n:,.0f}".replace(",", " ")


def build_report(con, day: date):
    d0, d1 = day.isoformat(), (day + timedelta(days=1)).isoformat()
    s = {}
    s["requests"] = db.rows(con, """SELECT r.id, r.branch, r.product_code, c.applied_rate_pct, c.gross_rate_pct, c.premium, c.verdict
                                    FROM requests r LEFT JOIN calculations c ON c.request_id=r.id
                                    WHERE r.created_at >= ? AND r.created_at < ?""", d0, d1)
    s["batches"] = db.rows(con, "SELECT * FROM portfolio_batches WHERE imported_at >= ? AND imported_at < ?", d0, d1)
    s["claims"] = db.rows(con, "SELECT * FROM claims WHERE reported_date >= ? AND reported_date < ?", d0, d1)
    s["calibration"] = db.rows(con, "SELECT * FROM calibration_runs WHERE run_at >= ? AND run_at < ?", d0, d1)
    s["tasks_done"] = db.rows(con, "SELECT * FROM agent_tasks WHERE done_at >= ? AND done_at < ?", d0, d1)
    s["tasks_open"] = db.rows(con, "SELECT * FROM agent_tasks WHERE status IN ('новая','в работе') AND parent_id IS NOT NULL")
    s["tasks_wait"] = db.rows(con, "SELECT * FROM agent_tasks WHERE status='ждёт заказчика'")
    s["audit"] = db.rows(con, "SELECT ts, who, action, entity FROM audit WHERE ts >= ? AND ts < ? ORDER BY id", d0, d1)
    s["market_dates"] = [r["report_date"] for r in db.rows(con, "SELECT DISTINCT report_date FROM market_stats ORDER BY 1")]
    s["market_new"] = [r["report_date"] for r in db.rows(
        con, "SELECT DISTINCT report_date FROM market_stats WHERE loaded_at >= ? AND loaded_at < ?", d0, d1)]
    # открытые данные агентства статистики: что обновилось за сутки
    try:
        s["stat_new"] = db.rows(con, """SELECT dataset_id, COUNT(*) n, MIN(period) p_min, MAX(period) p_max
                                        FROM stat_series WHERE fetched_at >= ? AND fetched_at < ?
                                        GROUP BY dataset_id ORDER BY dataset_id""", d0, d1)
        s["stat_rev"] = db.rows(con, """SELECT dataset_id, COUNT(*) n, MIN(period) p_min, MAX(period) p_max
                                        FROM stat_series_revisions
                                        WHERE replaced_at >= ? AND replaced_at < ?
                                        GROUP BY dataset_id ORDER BY dataset_id""", d0, d1)
        s["stat_rows"] = con.execute("SELECT COUNT(*) FROM stat_series").fetchone()[0]
    except Exception:   # таблиц ещё нет — доклад всё равно должен собраться
        s["stat_new"], s["stat_rev"], s["stat_rows"] = [], [], 0
    try:
        from . import statagency
        s["stat_state"] = statagency.last_statuses()
        s["stat_names"] = {k: v["name"] for k, v in statagency.ss.DATASETS.items()}
    except Exception:
        s["stat_state"], s["stat_names"] = {"last": None, "statuses": {}}, {}

    # согласования: кто что решил за сутки и сколько запросов висит
    try:
        s["approvals_day"] = db.rows(con, """SELECT rr.full_name, rr.position, rr.status decision, rr.comment,
                                                    rr.decided_at, rr.request_id, r.external_no, r.branch,
                                                    g.partner
                                             FROM request_reviewers rr
                                             JOIN requests r ON r.id = rr.request_id
                                             LEFT JOIN general_agreements g ON g.id = r.general_agreement_id
                                             WHERE rr.decided_at >= ? AND rr.decided_at < ?
                                             ORDER BY rr.decided_at""", d0, d1)
        s["approvals_pending"] = db.rows(con, """SELECT r.id, r.external_no, r.branch, r.product_code,
                                                        COUNT(rr.id) total,
                                                        SUM(CASE WHEN rr.status='ожидает' THEN 1 ELSE 0 END) waiting
                                                 FROM requests r JOIN request_reviewers rr ON rr.request_id = r.id
                                                 WHERE r.approval_status='на согласовании'
                                                 GROUP BY r.id ORDER BY r.id""")
    except Exception:   # таблиц ещё нет — доклад всё равно должен собраться
        s["approvals_day"], s["approvals_pending"] = [], []

    # слежение за законодательством: что робот-юрист принёс за сутки
    try:
        from . import lawwatch
        s["law_events"] = lawwatch.events_for_report(con, d0, d1)
        s["law_rules_review"] = lawwatch.rules_to_review(con)
        s["law_acts_down"] = db.rows(con, "SELECT code, title, lex_url FROM watched_acts "
                                          "WHERE status='источник недоступен' ORDER BY code")
        s["law_status"] = lawwatch.last_status()
    except Exception:   # модуля или таблиц ещё нет — доклад всё равно должен собраться
        s["law_events"], s["law_rules_review"], s["law_acts_down"] = [], [], []
        s["law_status"] = {}

    from . import capacity as cap
    c = cap.capacity(con)
    s["capacity"] = {"limit": c["limit_per_risk"], "warnings": c["warnings"], "missing": c["missing"], "temporary": c["temporary"]}
    s["uncalibrated"] = con.execute("SELECT COUNT(*) FROM coefficients WHERE calibrated=0").fetchone()[0]
    # знания команды: что закрыли за сутки и за что браться дальше
    try:
        s["knowledge_day"] = db.rows(con, """SELECT l.agent, l.what, l.source, l.minutes, t.topic, t.area
                                             FROM knowledge_log l LEFT JOIN knowledge_topics t ON t.id = l.topic_id
                                             WHERE l.ts >= ? AND l.ts < ? ORDER BY l.id""", d0, d1)
        s["knowledge_next"] = db.rows(con, "SELECT topic, area, priority, source_hint FROM knowledge_topics "
                                            "WHERE status='пробел' ORDER BY priority, id LIMIT 3")
        s["knowledge_gaps"] = con.execute("SELECT COUNT(*) FROM knowledge_topics WHERE status='пробел'").fetchone()[0]
    except Exception:   # таблиц ещё нет — доклад всё равно должен собраться
        s["knowledge_day"], s["knowledge_next"], s["knowledge_gaps"] = [], [], 0

    verd = {}
    for r in s["requests"]:
        verd[r["verdict"]] = verd.get(r["verdict"], 0) + 1
    below = [r for r in s["requests"] if r["applied_rate_pct"] and r["gross_rate_pct"] and r["applied_rate_pct"] < r["gross_rate_pct"] - 1e-9]

    L = [f"# Ежедневный доклад команды сюрвейера — {day.strftime('%d.%m.%Y')}", ""]
    L += ["## 1. Аналитика запросов",
          f"- Новых запросов: **{len(s['requests'])}**" + (" (" + ", ".join(f"{k}: {v}" for k, v in verd.items()) + ")" if verd else ""),
          f"- Ниже технической ставки: **{len(below)}**" + ("" if not below else " — " + ", ".join(f"№{r['id']} {r['branch'] or ''} {r['product_code']}" for r in below[:10])),
          f"- Премий по новым запросам: **{_fmt(sum((r['premium'] or 0) for r in s['requests']))} сум**", ""]
    L += ["## 2. Портфель"]
    if s["batches"]:
        for b in s["batches"]:
            L.append(f"- Загрузка № {b['id']} «{b['file_name']}»: {b['rows_total']} договоров, отклонено {b['rows_stop']}, на утверждение {b['rows_warn']}")
    else:
        L.append("- Новых выгрузок не было. Папка для автоимпорта: data/inbox/portfolio")
    L += ["", "## 3. Рынок (НАПП)",
          f"- Срезов в базе: {len(s['market_dates'])}, последний {s['market_dates'][-1] if s['market_dates'] else '—'}",
          "- За сутки " + ("загружены новые срезы: " + ", ".join(s["market_new"]) if s["market_new"] else "новых отчётов на сайте НАПП не появилось"), ""]
    L += ["## 3а. Агентство статистики: что обновилось"]
    names = s.get("stat_names") or {}
    if s["stat_new"]:
        for r in s["stat_new"]:
            L.append(f"- {names.get(r['dataset_id'], r['dataset_id'])}: обновлено строк {r['n']}, "
                     f"периоды {r['p_min']}–{r['p_max']}")
    else:
        L.append("- За сутки новых значений у агентства статистики не появилось")
    if s["stat_rev"]:
        L.append("- **Ряды пересчитаны задним числом** (прежние значения сохранены в журнале пересчётов):")
        for r in s["stat_rev"]:
            L.append(f"  - {names.get(r['dataset_id'], r['dataset_id'])}: {r['n']} точек, "
                     f"периоды {r['p_min']}–{r['p_max']} — расчёты за эти периоды стоит перепроверить")
    down = [(k, v) for k, v in ((s.get("stat_state") or {}).get("statuses") or {}).items()
            if v.get("status") != "ok"]
    if down:
        L.append("- Источники, которые не отдали данные:")
        for k, v in down:
            L.append(f"  - {names.get(k, k)}: {v.get('status')}" + (f" — {v.get('reason')}" if v.get("reason") else ""))
    elif (s.get("stat_state") or {}).get("last"):
        L.append(f"- Все источники ответили; последнее обновление {s['stat_state']['last']}")
    else:
        L.append("- С запуска сервера обновление ещё не запускалось (расписание — раз в сутки)")
    L.append(f"- Всего строк в stat_series: {s['stat_rows']}")
    L.append("")
    L += ["## 4. Самообучение (калибровка)",
          f"- Зарегистрировано убытков за сутки: {len(s['claims'])}",
          f"- Прогонов калибровки: {len(s['calibration'])}" + ("" if not s["calibration"] else " — " + "; ".join(f"№{r['id']} {r['status']}" for r in s["calibration"])),
          f"- Некалиброванных коэффициентов: {s['uncalibrated']}" + (" — ждём выгрузки договоров и убытков" if s["uncalibrated"] else ""), ""]
    L += ["## 4а. Знания команды"]
    if s["knowledge_day"]:
        for k in s["knowledge_day"]:
            mins = f", {k['minutes']} мин" if k["minutes"] else ""
            src = f" (источник: {k['source']})" if k["source"] else ""
            L.append(f"- {AGENT_NAMES.get(k['agent'], k['agent'])} — {k['topic'] or 'без темы'}: {k['what'][:200]}{src}{mins}")
        L.append(f"- Потрачено на обучение: {sum((k['minutes'] or 0) for k in s['knowledge_day'])} мин")
    else:
        L.append("- За сутки никто ничего не изучил")
    if s["knowledge_next"]:
        n = s["knowledge_next"][0]
        L.append(f"- Следующий пробел (приоритет {n['priority']}, {n['area']}): **{n['topic']}**"
                 + (f" — {n['source_hint']}" if n["source_hint"] else ""))
        if len(s["knowledge_next"]) > 1:
            L.append("- Далее в очереди: " + "; ".join(t["topic"][:70] for t in s["knowledge_next"][1:]))
    L.append(f"- Всего открытых пробелов: {s['knowledge_gaps']}")
    L.append("")
    L += ["## 5. Ёмкость и нормативы",
          f"- Лимит на один риск: {_fmt(s['capacity']['limit'])} сум" + (" (временные цифры)" if s["capacity"]["temporary"] else ""),
          "- Нарушений нормативов: " + ("нет" if not s["capacity"]["warnings"] else "; ".join(s["capacity"]["warnings"])),
          "- Не хватает отчётности: " + (", ".join(s["capacity"]["missing"]) if s["capacity"]["missing"] else "всё загружено"), ""]
    L += ["## 6. Команда"]
    done_sub = [t for t in s["tasks_done"] if t["parent_id"]]
    if done_sub:
        for t in done_sub:
            L.append(f"- ✅ {t['title']}" + (f" — {t['result'][:160]}" if t["result"] else ""))
    else:
        L.append("- Закрытых задач за сутки нет")
    if s["tasks_open"]:
        L.append(f"- В работе: {len(s['tasks_open'])} — " + "; ".join(t['title'][:70] for t in s["tasks_open"][:8]))
    if s["tasks_wait"]:
        L.append("- **Ждут вашего ответа:**")
        L += [f"  - {t['title']}: {t['result']}" for t in s["tasks_wait"]]
    L += ["", "## 6а. Согласования"]
    if s["approvals_day"]:
        for a in s["approvals_day"]:
            src = f" (генсоглашение {a['partner']})" if a["partner"] else ""
            cm = f" — {a['comment'][:160]}" if a["comment"] else ""
            L.append(f"- {a['full_name']} ({a['position'] or 'должность не указана'}) {a['decision']} "
                     f"запрос № {a['request_id']} {a['external_no'] or ''}{src}{cm}")
    else:
        L.append("- За сутки решений по согласованию не было")
    if s["approvals_pending"]:
        L.append(f"- Ждут решения: **{len(s['approvals_pending'])}** запросов — "
                 + "; ".join(f"№{p['id']} (осталось {p['waiting']} из {p['total']})" for p in s["approvals_pending"][:10]))
    else:
        L.append("- Запросов на согласовании нет")

    L += ["", "## 6б. Изменения законодательства и новости"]
    if s["law_events"]:
        for e in s["law_events"]:
            link = f" — {e['url']}" if e.get("url") else ""
            when = f" от {e['published_at']}" if e.get("published_at") else ""
            L.append(f"- **{e['kind']}** ({e['source']}){when}: {e['title'][:180]}{link}")
            if e.get("summary"):
                L.append(f"  - {str(e['summary']).replace(chr(10), ' ')[:400]}")
    else:
        L.append("- За сутки изменений и новостей по страхованию не замечено")
    if s["law_rules_review"]:
        L.append("- **Правила движка, требующие пересмотра юристом:** "
                 + "; ".join(f"{r['code']} ({r['review_reason'] or 'причина не указана'})"
                             for r in s["law_rules_review"][:10]))
    if s["law_acts_down"]:
        L.append("- Источник недоступен (проверить вручную): "
                 + "; ".join(f"{a['title'][:70]} — {a['lex_url']}" for a in s["law_acts_down"][:6]))
    if s.get("law_status", {}).get("last"):
        L.append(f"- Последняя проверка актов: {s['law_status']['last']}, "
                 f"непрочитанных событий {s['law_status'].get('unseen_events', 0)}")
    else:
        L.append("- Проверка актов ещё ни разу не выполнялась")

    L += ["", "## 7. Журнал за сутки", f"- Записей: {len(s['audit'])}"]
    by_who = {}
    for a in s["audit"]:
        by_who.setdefault(a["who"], []).append(a["action"])
    for who, acts in by_who.items():
        L.append(f"- {who}: " + ", ".join(f"{a} ×{acts.count(a)}" if acts.count(a) > 1 else a for a in dict.fromkeys(acts)))
    L += ["", "## 8. Что нужно от заказчика"]
    need = []
    if s["uncalibrated"]:
        need.append("выгрузки договоров и убытков за 3–5 лет — без них ставки остаются экспертными")
    if s["capacity"]["missing"]:
        need.append("отчётность по резервам, активам и марже платёжеспособности (приложения к Положениям 1806 и 1882)")
    if s["capacity"]["temporary"]:
        need.append("реальные собственные средства и резервы на последнюю дату")
    need += [t["result"] for t in s["tasks_wait"] if t["result"]]
    L += [f"- {n}" for n in need] or ["- ничего"]
    stats = {k: (len(v) if isinstance(v, list) else v) for k, v in s.items()}
    return "\n".join(L), stats


def make_report(day: Optional[date] = None) -> dict:
    day = day or (date.today() - timedelta(days=1))
    with db.tx() as con:
        body, stats = build_report(con, day)
        con.execute("INSERT OR REPLACE INTO daily_reports VALUES (?,?,?,?)",
                    (day.isoformat(), db.now(), body, json.dumps(stats, ensure_ascii=False, default=str)))
        db.audit(con, "lead", "ежедневный доклад", f"report:{day.isoformat()}")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"{day.isoformat()}.md").write_text(body, encoding="utf-8")
    return {"report_date": day.isoformat(), "body_md": body}


def scheduler():
    """Каждый день в 08:00 — доклад за вчера. Если сервер стоял в 08:00, доклад делается при первом запуске после."""
    while True:
        now = datetime.now()
        target = now.replace(hour=8, minute=0, second=0, microsecond=0)
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        try:
            with db.tx() as con:
                have = con.execute("SELECT 1 FROM daily_reports WHERE report_date=?", (yesterday,)).fetchone()
            if not have and now >= target:
                make_report()
            background.ok("daily-report")
        except Exception as e:                       # поток живёт дальше, ошибка — в журнал
            background.failed("daily-report", e)
        time.sleep(600)


def start_scheduler():
    background.start("daily-report", scheduler)


@router.post("/reports/make")
def report_now(day: Optional[str] = None):
    return make_report(date.fromisoformat(day) if day else date.today())


@router.get("/reports")
def reports_list():
    with db.tx() as con:
        return db.rows(con, "SELECT report_date, created_at FROM daily_reports ORDER BY report_date DESC LIMIT 60")


@router.get("/reports/{day}.md", response_class=PlainTextResponse)
def report_md(day: str):
    with db.tx() as con:
        r = db.rows(con, "SELECT body_md FROM daily_reports WHERE report_date=?", day)
    if not r:
        raise HTTPException(404, "Доклада за эту дату нет")
    return r[0]["body_md"]


@router.get("/reports-page", response_class=HTMLResponse)
def reports_page(embed: int = 0):
    return _page(web.read_text(ROOT / "app" / "reports.html"), "/reports-page", embed)


@router.get("/tasks-page", response_class=HTMLResponse)
def tasks_page(embed: int = 0):
    return _page(web.read_text(ROOT / "app" / "tasks.html"), "/tasks-page", embed)


def _page(html: str, active: str, embed: int) -> str:
    """Общая раскладка из app/main.py; импорт отложенный — main.py сам подключает этот модуль."""
    from .main import page
    return page(html, active, bool(embed))
