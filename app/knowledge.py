"""
Знания команды: реестр пробелов и журнал ежедневного обучения.

Зачем. Требование заказчика: агенты каждый день добираются до тонкостей страхования и закрывают
пробелы там, где их знаний не хватает. Перестрахование в продукт НЕ входит (решение заказчика
20.09.2026) — мы его не считаем и модуль не строим, но понятие о нём команда обязана иметь,
иначе не поймёт ни собственное удержание, ни ёмкость. Поэтому оно здесь как тема знания.

Как устроено. knowledge_topics — список тем со статусом «пробел»/«изучено» и приоритетом
(1 — самый срочный). knowledge_log — кто, что и откуда понял и сколько времени на это ушёл.
Закрытие темы пишется и в общий журнал audit, как во всех модулях.

Заметки складываются в docs/Знания/*.md и попадают в библиотеку (tools/library_build.py).
"""
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db

ROOT = Path(__file__).resolve().parent.parent
NOTES_DIR = ROOT / "docs" / "Знания"
router = APIRouter()

AREAS = ("закон", "актуарий", "рынок", "перестрахование", "андеррайтинг", "такафул", "ит")
STATUSES = ("пробел", "изучено")
# коды агентов — как в db/schema.sql (knowledge_log.agent)
AGENTS = ("law", "actuary", "data", "backend", "ui", "reviewer", "lead")
PRIORITY_MIN, PRIORITY_MAX = 1, 5

# Начальный список пробелов: (тема, область, приоритет, где искать).
# Приоритет 1 — то, без чего нельзя обсуждать удержание и ёмкость.
SEED = [
    ("Перестрахование: формы и виды (факультативное, облигаторное, пропорциональное и непропорциональное)",
     "перестрахование", 1, "Пфайффер «Введение в перестрахование»; CII M97 Reinsurance"),
    ("Перестрахование: собственное удержание и таблица линий (квота, эксцедент сумм)",
     "перестрахование", 1, "Пфайффер, гл. о таблице линий; ст. 24 Закона «О страховой деятельности» — норматив удержания"),
    ("Перестрахование: эксцедент убытка XL — приоритет, слои, восстановления",
     "перестрахование", 1, "CII M97; рейтинг слоёв по burning cost"),
    ("Перестрахование: катастрофический cat XL и защита портфеля от землетрясения",
     "перестрахование", 1, "CII M97; связь с картой ОСР и накоплением по сейсмозонам"),
    ("Ретакафул: как перестрахование устроено в такафуле и чем отличается от обычного",
     "такафул", 1, "AAOIFI FAS; практика ретакафул-операторов Малайзии и ОАЭ"),

    ("Актуарные методы: теория доверия (credibility) — полная и частичная",
     "актуарий", 2, "Bühlmann; limited fluctuation credibility — уже применена в app/calibration.py"),
    ("Актуарные методы: chain-ladder и треугольники развития убытков для РПНУ",
     "актуарий", 2, "Mack (1993); Положение о резервах НАПП"),
    ("Актуарные методы: распределения тяжести убытка (логнормальное, Парето, смеси) и хвосты",
     "актуарий", 2, "Klugman, Loss Models; подбор по собственным убыткам"),

    ("Андеррайтинг класса 3 «Наземный транспорт»: что смотреть, какие исключения, какая франшиза",
     "андеррайтинг", 3, "Правила компании по КАСКО; статистика угонов и ДТП по РУз"),
    ("Андеррайтинг класса 7 «Имущество в пути (грузы)»: Инкотермс, маршрут, перегрузки, накопление на складе",
     "андеррайтинг", 3, "Institute Cargo Clauses A/B/C; Инкотермс 2020"),
    ("Андеррайтинг класса 13 «Общая гражданская ответственность»: лимиты, агрегат, claims-made против occurrence",
     "андеррайтинг", 3, "Практика GTPL; ГК РУз, глава о возмещении вреда"),
    ("Андеррайтинг класса 14 «Кредиты»: оценка заёмщика, срок ожидания, доля собственного участия банка",
     "андеррайтинг", 3, "Практика кредитного страхования; требования ЦБ РУз к обеспечению"),

    ("Регресс и суброгация в практике РУз: основания, сроки, судебная перспектива",
     "закон", 2, "ГК РУз ст. 937 и далее; Закон «О страховой деятельности»"),
    ("Стабилизационные резервы по ОСГО, ОСГОР, ОСГОП: расчёт, пополнение, использование",
     "актуарий", 2, "Положение о формировании страховых резервов; законы об обязательных видах"),
    ("Налогообложение страховых операций в РУз: НДС, налог на прибыль, вычеты по резервам",
     "закон", 3, "Налоговый кодекс РУз, разделы о страховании"),
    ("МСФО 17 «Договоры страхования»: общая модель, PAA, CSM — что меняется в отчётности",
     "актуарий", 3, "IFRS 17; переход страховщиков РУз"),
    ("Противодействие страховому мошенничеству: типовые схемы, красные флаги, проверка заявленного убытка",
     "андеррайтинг", 2, "Практика служб безопасности; статистика отказов компании"),
    ("Сейсмическое зонирование РУз: карта ОСР, шкала MSK-64, привязка зоны к коэффициенту",
     "актуарий", 1, "Карта ОСР; КМК 2.01.03-19 «Строительство в сейсмических районах»"),
    ("ПКМ № 458 от 23.07.2025: что именно меняет для андеррайтинга и тарифов",
     "закон", 1, "lex.uz, текст постановления"),
    ("Закон РУз «О персональных данных»: хранение данных граждан только в РУз, обезличивание в журналах",
     "закон", 1, "Закон № ЗРУ-547; практика Мининфокома"),
    ("Telegram mini app для агентов: авторизация initData, ограничения WebApp, офлайн-сценарии",
     "ит", 3, "core.telegram.org/bots/webapps"),
]


def seed(con) -> int:
    """Идемпотентно доливает начальные темы. Тема уникальна по тексту — повтор ничего не портит."""
    now = db.now()
    added = 0
    for topic, area, priority, hint in SEED:
        cur = con.execute(
            "INSERT OR IGNORE INTO knowledge_topics (topic, area, priority, source_hint, created_at) VALUES (?,?,?,?,?)",
            (topic, area, priority, hint, now))
        added += cur.rowcount or 0
    return added


def _ensure_seed():
    """Вызывается при инициализации хранения: таблицы уже созданы db_build, темы доливаем."""
    try:
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        with db.tx() as con:
            n = seed(con)
            if n:
                db.audit(con, "lead", "добавлены темы знаний", "knowledge", {"добавлено": n})
    except Exception as e:  # база может быть ещё не собрана — сервер всё равно должен подняться
        print(f"knowledge: начальные темы не залиты: {e}")


# ---------- модели ----------

class TopicIn(BaseModel):
    topic: str
    area: str
    priority: int = 5
    source_hint: Optional[str] = None


class LearnedIn(BaseModel):
    agent: str
    what: str
    source: Optional[str] = None
    note_path: Optional[str] = None
    minutes: Optional[int] = None


# ---------- эндпоинты ----------

@router.get("/knowledge/topics")
def topics(area: Optional[str] = None, status: Optional[str] = None):
    sql = "SELECT * FROM knowledge_topics WHERE 1=1"
    args = []
    if area:
        sql += " AND area = ?"
        args.append(area)
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY status, priority, id"
    with db.tx() as con:
        items = db.rows(con, sql, *args)
        gaps = con.execute("SELECT COUNT(*) FROM knowledge_topics WHERE status='пробел'").fetchone()[0]
    return {"count": len(items), "gaps_total": gaps, "areas": list(AREAS), "items": items}


@router.post("/knowledge/topics")
def add_topic(t: TopicIn):
    if t.area not in AREAS:
        raise HTTPException(400, f"область должна быть одной из: {', '.join(AREAS)}")
    if not t.topic.strip():
        raise HTTPException(400, "тема пустая")
    if not (PRIORITY_MIN <= t.priority <= PRIORITY_MAX):
        raise HTTPException(400, f"приоритет должен быть числом от {PRIORITY_MIN} до {PRIORITY_MAX}")
    with db.tx() as con:
        row = con.execute("SELECT id FROM knowledge_topics WHERE topic = ?", (t.topic.strip(),)).fetchone()
        if row:
            return {"topic_id": row["id"], "created": False}
        cur = con.execute(
            "INSERT INTO knowledge_topics (topic, area, priority, source_hint, created_at) VALUES (?,?,?,?,?)",
            (t.topic.strip(), t.area, t.priority, t.source_hint, db.now()))
        db.audit(con, "lead", "добавлена тема знаний", f"knowledge_topic:{cur.lastrowid}",
                 {"тема": t.topic.strip(), "область": t.area, "приоритет": t.priority})
        return {"topic_id": cur.lastrowid, "created": True}


@router.post("/knowledge/topics/{topic_id}/learned")
def learned(topic_id: int, x: LearnedIn):
    if not x.what.strip():
        raise HTTPException(400, "не сказано, что именно понято")
    if x.agent not in AGENTS:
        raise HTTPException(400, f"код агента должен быть одним из: {', '.join(AGENTS)}")
    # заметка должна реально лежать на диске, иначе знание останется только в реестре
    if x.note_path and x.note_path.strip():
        if not (ROOT / x.note_path.strip()).is_file():
            raise HTTPException(400, f"файла заметки нет на диске: {x.note_path.strip()} "
                                     f"(путь считается от корня проекта, заметки кладём в docs/Знания)")
    with db.tx() as con:
        t = con.execute("SELECT * FROM knowledge_topics WHERE id = ?", (topic_id,)).fetchone()
        if not t:
            raise HTTPException(404, "тема не найдена")
        repeat = t["status"] == "изучено"     # повторное закрытие не запрещаем, но показываем
        ts = db.now()
        con.execute("INSERT INTO knowledge_log (ts, agent, topic_id, what, source, minutes) VALUES (?,?,?,?,?,?)",
                    (ts, x.agent, topic_id, x.what.strip(), x.source, x.minutes))
        con.execute("UPDATE knowledge_topics SET status='изучено', learned_at=?, note_path=COALESCE(?, note_path) WHERE id=?",
                    (ts, x.note_path, topic_id))
        db.audit(con, x.agent, "изучена тема", f"knowledge_topic:{topic_id}",
                 {"тема": t["topic"], "источник": x.source, "минут": x.minutes, "заметка": x.note_path,
                  "повтор": repeat})
        left = con.execute("SELECT COUNT(*) FROM knowledge_topics WHERE status='пробел'").fetchone()[0]
    return {"ok": True, "topic_id": topic_id, "learned_at": ts, "gaps_left": left, "repeat": repeat}


@router.get("/knowledge/log")
def log(agent: Optional[str] = None, days: int = 30, limit: int = 200):
    sql = ("SELECT l.*, t.topic, t.area FROM knowledge_log l LEFT JOIN knowledge_topics t ON t.id = l.topic_id "
           "WHERE l.ts >= ?")
    # ts пишется локальным временем (UTC+5), поэтому границу считаем по локальной дате, а не date('now')
    args = [(date.today() - timedelta(days=max(int(days), 0))).isoformat()]
    if agent:
        sql += " AND l.agent = ?"
        args.append(agent)
    sql += " ORDER BY l.id DESC LIMIT ?"
    args.append(max(int(limit), 1))
    with db.tx() as con:
        items = db.rows(con, sql, *args)
        minutes = sum((i["minutes"] or 0) for i in items)
    return {"count": len(items), "minutes_total": minutes, "items": items}


@router.get("/knowledge/next")
def next_gap(area: Optional[str] = None):
    """Следующий пробел: самый срочный, при равном приоритете — самый давний."""
    sql = "SELECT * FROM knowledge_topics WHERE status='пробел'"
    args = []
    if area:
        sql += " AND area = ?"
        args.append(area)
    sql += " ORDER BY priority, id LIMIT 1"
    with db.tx() as con:
        items = db.rows(con, sql, *args)
        left = con.execute("SELECT COUNT(*) FROM knowledge_topics WHERE status='пробел'").fetchone()[0]
    return {"topic": items[0] if items else None, "gaps_left": left}


@router.get("/knowledge/daily")
def daily():
    """Урок дня: чем закончили вчера и за что браться сегодня."""
    with db.tx() as con:
        last = db.rows(con, "SELECT l.*, t.topic, t.area FROM knowledge_log l "
                            "LEFT JOIN knowledge_topics t ON t.id = l.topic_id ORDER BY l.id DESC LIMIT 1")
        nxt = db.rows(con, "SELECT * FROM knowledge_topics WHERE status='пробел' ORDER BY priority, id LIMIT 1")
        total = con.execute("SELECT COUNT(*) FROM knowledge_topics").fetchone()[0]
        done = con.execute("SELECT COUNT(*) FROM knowledge_topics WHERE status='изучено'").fetchone()[0]
        today_n = con.execute("SELECT COUNT(*) FROM knowledge_log WHERE ts >= ?",
                              (date.today().isoformat(),)).fetchone()[0]
    return {"last": last[0] if last else None,
            "next": nxt[0] if nxt else None,
            "progress": {"total": total, "learned": done, "gaps": total - done},
            "learned_today": today_n,
            "notes_dir": "docs/Знания"}
