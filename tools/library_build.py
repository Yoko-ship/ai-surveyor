"""
Сборка библиотеки проекта.

Раскладывает исходные документы по подгруппам, копирует к ним извлечённый текст
и строит каталог (КАТАЛОГ.md + catalog.json). Оригиналы на рабочем столе не трогает.

Запуск:  python tools/library_build.py
После добавления новых файлов в library/ запустить повторно — каталог обновится.
"""
import json
import re
import shutil
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"
DESKTOP = Path.home() / "Desktop"
SRC = ROOT / "docs" / "source"

# Подгруппы библиотеки
GROUPS = {
    "01_Законодательство/01_Законы": "Законы Республики Узбекистан",
    "01_Законодательство/02_Акты_регуляторов": "Положения НАПП и Минфина",
    "02_Компания_INSON": "Внутренние документы страховой организации",
    "03_Рынок_НАПП": "Квартальная статистика страхового рынка",
    "03_Рынок_НАПП/Конкуренты": "Публичные правила, оферты и паспорта продуктов других страховщиков РУз (не норма)",
    "04_Образцы_документов": "Образцы полисов, договоров, запросов филиалов",
    "05_Методология": "Учебники и профессиональная литература",
    "06_Заметки_проекта": "Разборы и рабочие материалы проекта",
}

# Реестр: куда, под каким именем, что это, откуда взять оригинал и текст
REGISTRY = [
    dict(group="01_Законодательство/01_Законы",
         name="ЗРУ-1154 от 22.06.2026 — изменения в законодательство",
         source=DESKTOP / "Суғурта тўғрисидаги қонун.pdf",
         text=SRC / "Закон о страховании.txt",
         about="Меняет ГК (ст. 927, 934, 936, 938, 955), Закон о страховой деятельности "
               "(ст. 33¹ полис, ст. 24 уведомительный порядок для сюрвейера, ст. 62 ЕАИС, ст. 63 раскрытие информации), "
               "законы об ОСГО, ОСГОР, ОСГОП. Опубликован 23.06.2026.",
         note="PDF — скан без текстового слоя, разбирался постранично как изображения."),
    dict(group="01_Законодательство/02_Акты_регуляторов",
         name="Положение 1806 — платёжеспособность страховщиков (рус)",
         source=DESKTOP / "1806 12.05.2008 (2).doc", text=SRC / "1806 12.05.2008 (2).txt",
         about="Маржа платёжеспособности, лимит 20% на один риск, 200% совокупно, размещение активов."),
    dict(group="01_Законодательство/02_Акты_регуляторов",
         name="Положение 1806 — платёжеспособность страховщиков (узб)",
         source=DESKTOP / "1806 12.05.2008.doc", text=SRC / "1806 12.05.2008.txt",
         about="Узбекская версия того же документа."),
    dict(group="01_Законодательство/02_Акты_регуляторов",
         name="Положение 1882 — страховые резервы (рус)",
         source=DESKTOP / "1882 15.12.2008.doc", text=SRC / "1882 15.12.2008.txt",
         about="Учётные группы и расчёт РНП, резервы убытков, резерв катастроф, "
               "обязательный состав данных по договору (п. 4)."),
    dict(group="01_Законодательство/02_Акты_регуляторов",
         name="Положение 1882 — страховые резервы (узб)",
         source=DESKTOP / "1882 15.12.2008 (2).doc", text=SRC / "1882 15.12.2008 (2).txt",
         about="Узбекская версия того же документа."),
    dict(group="01_Законодательство/02_Акты_регуляторов",
         name="Положение 3571 — электронные страховые услуги",
         source=DESKTOP / "3571 11.11.2024.doc", text=SRC / "Положение 3571 электронные страховые услуги.txt",
         about="Порядок работы с ЕАИС ersp.e-osgo.uz: регистрация договоров, оформление полисов, "
               "претензии, требования к подключению, форма электронного полиса (19 полей)."),
    dict(group="01_Законодательство/02_Акты_регуляторов",
         name="Положение 3845 — страховые агенты",
         source=DESKTOP / "Sug‘urta agentlari to‘g‘risidagi nizom.pdf",
         text=SRC / "Положение о страховых агентах.txt",
         about="Приказ НАПП №14 от 20.04.2026: реестр агентов и ID в ЕАИС, комиссия не более 25%, "
               "возврат комиссии при досрочном расторжении, обучение 12 часов, мониторинг раз в полгода."),
    dict(group="02_Компания_INSON",
         name="Тарифная политика — приказ 54-П от 23.09.2025",
         source=DESKTOP / "Прилож №1 (Тарифная политика).pdf", text=SRC / "Тарифная политика.txt",
         about="Минимальные тарифы и агентские вознаграждения по всем продуктам компании.",
         note="Скан с распознаванием; ставки по имущественным продуктам сверены по изображениям страниц. "
              "Продукта 0126 (НС заёмщика) в этой версии нет — нужна действующая редакция."),
    dict(group="05_Методология",
         name="CII M05 — Insurance Law (2019)", source=DESKTOP / "Insurance law.pdf",
         text=SRC / "book_Insurance_law.txt",
         about="Принципы страхования по английскому праву: страховой интерес, раскрытие риска, "
               "возмещение, недострахование, непосредственная причина, суброгация, двойное страхование."),
    dict(group="05_Методология",
         name="CII M97 — Reinsurance (2019)", source=DESKTOP / "Reinsurance.pdf",
         text=SRC / "book_Reinsurance.txt",
         about="Виды перестрахования, EML, собственное удержание, burning cost, экспозиционный рейтинг, "
               "катастрофические программы, лимиты накопления."),
    dict(group="05_Методология",
         name="Пфайффер — Введение в перестрахование",
         source=DESKTOP / "pfaiffer_k_vvedenie_v_perestrakhovanie (2).pdf", text=None,
         about="Классика перестрахования: таблица линий, собственное удержание, однородность портфеля.",
         note="Скан без текстового слоя, 149 страниц. Автоматически не читается."),
]


def put(entry: dict) -> dict:
    """Копирует документ и его текст в библиотеку, возвращает запись каталога."""
    dst_dir = LIB / entry["group"]
    dst_dir.mkdir(parents=True, exist_ok=True)
    src = entry["source"]
    rec = {k: entry.get(k) for k in ("name", "group", "about", "note", "tag")}
    rec["файл"] = rec["текст"] = None
    if src and src.exists():
        dst = dst_dir / (entry["name"] + src.suffix)
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            shutil.copy2(src, dst)
        rec["файл"] = str(dst.relative_to(LIB)).replace("\\", "/")
    txt = entry.get("text")
    if txt and txt.exists():
        dst = dst_dir / (entry["name"] + ".txt")
        if not dst.exists() or dst.stat().st_mtime < txt.stat().st_mtime:
            shutil.copy2(txt, dst)
        rec["текст"] = str(dst.relative_to(LIB)).replace("\\", "/")
    return rec


COMPETITORS = "03_Рынок_НАПП/Конкуренты"


def collect_competitors(known: set) -> list:
    """Публичные документы других страховщиков: одна запись на документ (оригинал + текст рядом).
    Компания и источник — из шапки .txt; скан без расшифровки помечается."""
    out = []
    base = LIB / COMPETITORS
    if not base.exists():
        return out
    for txt in sorted(base.rglob("*.txt")):
        rel_txt = str(txt.relative_to(LIB)).replace("\\", "/")
        orig = next((txt.with_suffix(s) for s in (".pdf", ".docx", ".doc") if txt.with_suffix(s).exists()), None)
        rel_orig = str(orig.relative_to(LIB)).replace("\\", "/") if orig else None
        known.update(x for x in (rel_txt, rel_orig) if x)
        head = txt.read_text(encoding="utf-8", errors="ignore").partition("\n-----\n")[0]
        company = re.search(r"Компания:\s*(.+)", head)
        company = company.group(1).strip() if company else txt.parent.name
        note = None
        if "Индексировать: нет" in head:
            note = "Скан без текста (или негодный текстовый слой): в поиск не идёт, читать оригинал."
        elif "Текст: PDF — скан" in head:
            note = "Скан: в тексте — выборочная дословная расшифровка по изображениям страниц."
        elif "ошибками распознавания" in head:
            note = "Текстовый слой PDF с ошибками распознавания — цитаты сверять с оригиналом."
        out.append({"name": f"{company} — {txt.stem}", "group": COMPETITORS,
                    "about": "Публичный документ страховщика с его официального сайта (правила, оферта, "
                             "паспорт или страница продукта). Не норма права.",
                    "note": note, "tag": "рынок", "файл": rel_orig, "текст": rel_txt})
    return out


def collect_extra(known: set) -> list:
    """Файлы, положенные в библиотеку вручную, тоже попадают в каталог."""
    extra = []
    for p in sorted(LIB.rglob("*")):
        if p.is_file() and p.name != "КАТАЛОГ.md" and p.name != "catalog.json":
            rel = str(p.relative_to(LIB)).replace("\\", "/")
            if "__pycache__" in rel:
                continue
            if rel not in known:
                extra.append({"name": p.stem, "group": str(p.parent.relative_to(LIB)).replace("\\", "/"),
                              "about": None, "note": None, "tag": None, "файл": rel, "текст": None})
    return extra


LANG_TITLES = {"ru": "русский", "uz": "узбекский", "en": "английский"}


def source_url(path: str | None) -> str | None:
    """Ссылка на первоисточник — из первой строки текста, куда её пишет lex_fetch.py."""
    if not path:
        return None
    p = LIB / path
    try:
        head = p.read_text(encoding="utf-8", errors="ignore")[:300]
    except Exception:
        return None
    m = re.search(r"Источник:\s*(\S+)", head)
    return m.group(1) if m else None


def guess_lang(name: str, path: str | None) -> str:
    """Язык документа: по суффиксу в имени, иначе по алфавиту первых строк текста."""
    low = name.lower()
    if low.endswith("(uz)") or "(узб" in low or "(uz)" in low:
        return "uz"
    if low.endswith("(en)") or "(en)" in low:
        return "en"
    if "(рус" in low or "(ru)" in low:
        return "ru"
    if path:
        url = source_url(path) or ""
        if "/uz/docs/" in url:
            return "uz"
        if "/ru/docs/" in url:
            return "ru"
        p = LIB / path
        try:
            head = p.read_text(encoding="utf-8", errors="ignore")[:4000]
        except Exception:
            return "ru"
        # шапка выгрузки документов страховщиков: «Язык: узбекский (латиница)» и т.п.
        m = re.search(r"^Язык:\s*(\S+)", head, re.MULTILINE)
        if m and m.group(1).startswith("узбек"):
            return "uz"
        if m and m.group(1).startswith("англ"):
            return "en"
        if m and m.group(1).startswith("рус"):
            return "ru"
        if "ў" in head.lower() or "ғ" in head.lower() or "қ" in head.lower():
            return "uz"
        if "sug‘urta" in head.lower() or "sugʻurta" in head.lower() or "toʻgʻrisida" in head.lower():
            return "uz"
    return "ru"


def enrich(rec: dict) -> dict:
    txt = rec.get("текст") or (rec.get("файл") if (rec.get("файл") or "").endswith(".txt") else None)
    rec["язык"] = guess_lang(rec["name"], txt)
    rec["источник"] = source_url(txt)
    return rec


def main():
    LIB.mkdir(exist_ok=True)
    records = [put(e) for e in REGISTRY]

    # рыночные отчёты и заметки проекта подтягиваем из рабочих папок
    for xlsx in sorted((ROOT / "data" / "inbox").glob("*.xlsx")):
        records.append(put(dict(group="03_Рынок_НАПП", name=xlsx.stem, source=xlsx, text=None,
                                about="Квартальный отчёт НАПП по страховому рынку. "
                                      "Разобранные листы — в data/parsed.")))
    for md in sorted((ROOT / "docs").glob("*.md")):
        records.append(put(dict(group="06_Заметки_проекта", name=md.stem, source=md, text=None,
                                about="Рабочая заметка проекта.")))
    # разборы тем из реестра знаний (app/knowledge.py) — отдельной пометкой «знание»
    for md in sorted((ROOT / "docs" / "Знания").glob("*.md")):
        records.append(put(dict(group="06_Заметки_проекта", name=md.stem, source=md, text=None,
                                about="Разбор темы из реестра знаний команды.", tag="знание")))
    # обзоры рынка (docs/Знания/Рынок): сводки по НАПП и по продуктам других страховщиков
    for md in sorted((ROOT / "docs" / "Знания" / "Рынок").glob("*.md")):
        records.append(put(dict(group="06_Заметки_проекта", name="Рынок — " + md.stem, source=md, text=None,
                                about="Обзор рынка из реестра знаний команды.", tag="рынок")))

    known = {r["файл"] for r in records if r["файл"]} | {r["текст"] for r in records if r["текст"]}
    records += collect_competitors(known)
    records += collect_extra(known)
    records = [enrich(r) for r in records]

    (LIB / "catalog.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# Каталог библиотеки", "",
             f"Обновлён: {date.today().strftime('%d.%m.%Y')}. Документов: {len(records)}.", "",
             "Каталог собирается скриптом `tools/library_build.py`. "
             "Новый документ достаточно положить в нужную подгруппу и запустить скрипт заново.", ""]
    for group, title in GROUPS.items():
        rows = [r for r in records if r["group"] == group]
        if not rows:
            continue
        lines += [f"## {group.replace('_', ' ').replace('/', ' → ')}", "", f"*{title}*", ""]
        for r in sorted(rows, key=lambda x: x["name"]):
            links = []
            if r["файл"]:
                links.append(f"[оригинал]({r['файл'].replace(' ', '%20')})")
            if r["текст"]:
                links.append(f"[текст]({r['текст'].replace(' ', '%20')})")
            lang = LANG_TITLES.get(r.get("язык", "ru"), r.get("язык"))
            if r.get("источник"):
                links.append(f"[источник]({r['источник']})")
            lines.append(f"- **{r['name']}** — язык: {lang} — {' · '.join(links) if links else 'файла нет'}")
            if r["about"]:
                lines.append(f"  - {r['about']}")
            if r.get("tag"):          # пометка вида «знание» — не предупреждение, печатаем без значка
                lines.append(f"  - пометка: {r['tag']}")
            if r["note"]:
                lines.append(f"  - ⚠ {r['note']}")
        lines.append("")
    (LIB / "КАТАЛОГ.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"В каталоге записей: {len(records)}")
    for group in GROUPS:
        n = len([r for r in records if r["group"] == group])
        print(f"  {group}: {n}")


if __name__ == "__main__":
    main()
