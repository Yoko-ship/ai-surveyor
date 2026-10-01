"""
Сводка шаблонов анализа по классам — docs/Шаблоны классов — сводка.md.

  python tools/templates_summary.py                 # сводка по копии рабочей базы (рабочая база не меняется)
  python tools/templates_summary.py --db путь.db    # по указанной базе (открывается только на чтение)
  python tools/templates_summary.py --out файл.md

Что берётся (только чтение):
  * docs/act_class_templates.json — все шаблоны (классы общего страхования 1–18, варианты 13з и 16у): таблица полей;
  * docs/act_clauses.json — подписи оговорок (поле ru у элемента групп);
  * docs/act_measures.json — подписи мероприятий (catalog[].ru.text), а для мероприятий таблицы
    preventive_measures — колонка measure копии базы;
  * ракурсы — подписи акта (app/act_texts.VIEW_LABELS).
Раньше сводку собирал разовый скрипт; теперь — этот, по всем шаблонам файла.
"""
import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEMPLATES = ROOT / "docs" / "act_class_templates.json"
CLAUSES = ROOT / "docs" / "act_clauses.json"
MEASURES = ROOT / "docs" / "act_measures.json"
OUT = ROOT / "docs" / "Шаблоны классов — сводка.md"

RULE_CLASSES = ("3", "8", "9")       # у этих классов риски акта считает модуль аналитики, доли шаблона — справочно
METHOD_NAMES = {"agreed": "согласованная стоимость", "crop": "стоимость урожая"}


def _ru(x) -> str:
    if isinstance(x, dict):
        return str(x.get("ru") or "")
    return str(x or "")


def clause_labels(path: Path = CLAUSES) -> dict:
    cat = json.loads(path.read_text(encoding="utf-8"))
    return {c["code"]: c.get("ru") or c["code"] for g in (cat.get("groups") or {}).values() for c in g
            if isinstance(c, dict) and c.get("code")}


def measure_labels(path: Path = MEASURES, db_path=None) -> dict:
    """Подписи мероприятий: экспертный список файла, затем таблица preventive_measures копии базы."""
    cat = json.loads(path.read_text(encoding="utf-8"))
    out = {c["code"]: ((c.get("ru") or {}).get("text") or c["code"]) for c in cat.get("catalog") or []
           if isinstance(c, dict) and c.get("code")}
    if db_path:
        con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        try:
            for code, measure in con.execute("SELECT code, measure FROM preventive_measures"):
                out.setdefault(code, measure)
        finally:
            con.close()
    return out


def view_labels() -> dict:
    from app import act_texts as tx
    return {k: _ru(v) for k, v in tx.VIEW_LABELS.items()}


def _union(d) -> list:
    out = []
    for lst in (d or {}).values():
        for c in lst or []:
            if c not in out:
                out.append(c)
    return out


def _heading(code: str, t: dict) -> str:
    name = _ru(t.get("name"))
    if t.get("variant"):
        return f"## Вариант {code} (класс {t.get('variant_of')}). {name}"
    return f"## Класс {code}. {name}"


def _valuation(t: dict) -> str:
    parts = []
    for m in t.get("valuation_methods") or []:
        head = METHOD_NAMES.get(m.get("method"), f"метод {m.get('method')}")
        tail = _ru(m.get("note")) or _ru(m.get("when"))
        parts.append(f"{head}: {tail}" if tail else head)
    return "; ".join(parts)


def _risks(code: str, t: dict) -> str:
    rk = t.get("risks") or {}
    if rk.get("source") == "perils":
        return "Из справочника рисков базы (perils), с долями по справочнику — оценка разработчика, не утверждено " \
               "страховщиком"
    items = [f"{_ru(r['label'])} {r['share_pct']:g} %" + (" (катастрофический)" if r.get("catastrophic") else "")
             for r in rk.get("items") or []]
    text = "; ".join(items)
    if code in RULE_CLASSES:
        text += " — справочно, в расчёт не входит"
    return text + " (экспертно)"


def _row(label: str, value: str) -> str:
    return f"| {label} | {str(value).replace('|', '/')} |"


def template_block(code: str, t: dict, cl: dict, ms: dict, views: dict) -> list:
    L = [_heading(code, t), "", "| Поле | Содержание |", "|---|---|"]
    if t.get("variant"):
        L.append(_row("Отрасль", f"общее страхование, вариант класса {t.get('variant_of')}"))
    elif t.get("official_name"):
        on = t["official_name"]
        L.append(_row("Отрасль", f"общее страхование, класс {code} — «{on.get('text_uz', '')}» ({on.get('source', '')}); "
                                 "учётная группа РНП — " + (str(t["accounting_group"]) if t.get("accounting_group")
                                                            else "не задана (в Положении 1882, п. 10 класса нет)")))
    else:
        L.append(_row("Отрасль", "общее страхование"))
    obj = t.get("object") or {}
    L.append(_row("Объект", _ru(obj.get("description"))))
    kinds = [_ru(k.get("label")) for k in obj.get("kinds") or []]
    if len(kinds) > 1:
        L.append(_row("Виды объекта", ", ".join(kinds)))
    L.append(_row("Обязательные данные", "; ".join(_ru(f.get("label")) for f in t.get("must") or [])))
    L.append(_row("Дополнительные данные", "; ".join(_ru(f.get("label")) for f in t.get("optional") or [])))
    L.append(_row("Оценка стоимости", _valuation(t)))
    L.append(_row("Риски и доли в ставке", _risks(code, t)))
    L.append(_row("Факторы риска", "; ".join(_ru(f.get("label")) for f in t.get("factors") or [])))
    sr = t.get("scenario_rule") or {}
    rule = _ru(sr.get("text"))
    if sr.get("params"):
        rule += " (параметры: " + ", ".join(f"{k} = {v:g}" for k, v in sr["params"].items()) + ", экспертно)"
    L.append(_row("Сценарий убытка", rule))
    L.append(_row("Нужные снимки", ", ".join(views.get(v, v) for v in _union(t.get("required_views")))))
    L.append(_row("Документы", "; ".join(_ru(d) for d in (t.get("documents") or {}).get("items") or [])))
    L.append(_row("Внешняя статистика", ", ".join(t.get("stats") or []) or "открытых данных нет"))
    L.append("")
    clauses = _union(t.get("clauses"))
    if clauses:
        L += ["Оговорки:"] + [f"- {cl.get(c, c)}" for c in clauses] + [""]
    measures = _union(t.get("measures"))
    if measures:
        L += ["Рекомендации страхователю:"] + [f"- {ms.get(c, c)}" for c in measures] + [""]
    notes = [_ru(n.get("text")) for n in t.get("notes") or [] if _ru(n.get("text"))]
    if notes:
        L += ["Особые правила:"] + [f"- {n}" for n in notes] + [""]
    return L


def build(db_path=None) -> str:
    data = json.loads(TEMPLATES.read_text(encoding="utf-8"))
    cl, ms, views = clause_labels(), measure_labels(db_path=db_path), view_labels()
    C = data["classes"]
    C = {c: t for c, t in C.items() if not str(c).upper().startswith("L")}    # классов жизни в шаблонах нет
    general = [c for c, t in C.items() if not t.get("variant")]
    variants = [c for c, t in C.items() if t.get("variant")]
    cls_note = data.get("classification") or {}
    out = ["# Шаблоны анализа по классам страхования", "",
           f"Справочник шаблонов, версия {data.get('version')} от {data.get('date')}. Источник — приложение А к ТЗ. "
           "Все доли рисков, параметры сценариев, оговорки и рекомендации экспертные — оценка разработчика, не "
           "утверждено страховщиком (calibrated = 0). Администратор меняет шаблон в админке, раздел «Шаблоны классов».",
           "",
           f"Состав: {len(C)} шаблонов — все классы общего страхования: {len(general)} классов ({', '.join(general)}) "
           f"и {len(variants)} варианта ({', '.join(variants)}) со своими шаблонами.",
           ""]
    if cls_note:
        out += [f"Класс 18 общего страхования — по акту: {cls_note.get('act')}. Текст: {cls_note.get('url_uz')}. "
                + _ru(cls_note.get("language_note")) + ".", ""]
    out += ["Общие обязательные данные для всех классов: "
            + ", ".join(_ru(f.get("label")).lower() for f in data.get("common_must") or []) + ".", "",
            "Сводка собрана скриптом tools/templates_summary.py из docs/act_class_templates.json, docs/act_clauses.json, "
            "docs/act_measures.json и таблицы preventive_measures копии базы.", ""]
    for code, t in C.items():
        out += template_block(code, t, cl, ms, views)
    return "\n".join(out).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", help="база для подписей мероприятий preventive_measures (только чтение)")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    if a.db:
        text = build(a.db)
    else:
        from app import db
        with tempfile.TemporaryDirectory(prefix="tpl-summary-") as tmp:
            copy = Path(tmp) / "copy.db"
            db.snapshot(db.DB_PATH, copy)           # копия: рабочая база не открывается на запись
            text = build(copy)
    Path(a.out).write_text(text, encoding="utf-8")
    print(f"Сводка: {a.out} ({text.count(chr(10) + '## ')} шаблонов)")


if __name__ == "__main__":
    main()
