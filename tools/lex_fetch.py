"""
Загрузка нормативного акта с lex.uz (Национальная база данных законодательства) в библиотеку.

  python tools/lex_fetch.py https://lex.uz/ru/docs/5739120 --name "ЗРУ-730 О страховой деятельности"
  python tools/lex_fetch.py 5739120 --name "..." --group 01_Законы

Сохраняет HTML как есть (оригинал) и извлечённый текст (.txt) в library/01_Законодательство/<группа>/,
затем пересобирает каталог. Проверяет, что страница действительно с lex.uz и содержит текст акта.

Сеть, разбор и сохранение — общий модуль app/lexuz.py (им же пользуется живой поиск
«ИИ специалиста», app/legal_live.py): честный User-Agent, robots.txt, не чаще 1 запроса в секунду.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import lexuz  # noqa: E402

LIB = lexuz.LIB
TIMEOUT_SEC = 120            # страница кодекса весит больше 2,5 МБ — ручной загрузке ждать можно


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc", help="URL на lex.uz или номер документа")
    ap.add_argument("--name", required=True, help="Имя файла в библиотеке, например 'ЗРУ-730 О страховой деятельности'")
    ap.add_argument("--group", default="01_Законы", help="01_Законы | 02_Акты_регуляторов")
    a = ap.parse_args()

    url = lexuz.to_url(a.doc)
    if not lexuz.is_lex(url):
        sys.exit("Источник должен быть lex.uz — другие сайты не считаются официальными.")
    try:
        page = lexuz.fetch(url, timeout=TIMEOUT_SEC, expect="any")
    except lexuz.LexNotFound:
        sys.exit(f"На lex.uz нет такой страницы: {url}")
    except lexuz.LexUnavailable as e:
        sys.exit(f"lex.uz недоступен: {e.reason}")
    text = lexuz.extract_text(page)
    if not lexuz.looks_like_act(page, text):
        sys.exit(f"Страница не похожа на текст акта (символов: {len(text)}). Проверьте адрес: {url}")

    path = lexuz.save_act(page, url, a.name, a.group, lib=LIB)
    print(f"Сохранено: {path}  ({len(text):,} символов)")

    # признаки редакции — чтобы агент видел, что скачал действующий текст
    for m in lexuz.redaction_marks(text):
        print("  редакция:", m)

    # отметка в журнале — робот-юрист в «офисе» показывает, что сделал
    try:
        import sqlite3
        from datetime import datetime
        con = sqlite3.connect(ROOT / "data" / "surveyor.db")
        con.execute("INSERT INTO audit (ts, who, action, entity, detail) VALUES (?,?,?,?,?)",
                    (datetime.now().isoformat(timespec="seconds"), "юрист", "загружен акт с lex.uz",
                     path.stem, url))
        con.commit(); con.close()
    except Exception:
        pass

    subprocess.run([sys.executable, str(ROOT / "tools" / "library_build.py")], check=False)


if __name__ == "__main__":
    main()
