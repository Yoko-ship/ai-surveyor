"""
Загрузка нормативного акта с lex.uz (Национальная база данных законодательства) в библиотеку.

  python tools/lex_fetch.py https://lex.uz/ru/docs/5739120 --name "ЗРУ-730 О страховой деятельности"
  python tools/lex_fetch.py 5739120 --name "..." --group 01_Законы

Сохраняет HTML как есть (оригинал) и извлечённый текст (.txt) в library/01_Законодательство/<группа>/,
затем пересобирает каталог. Проверяет, что страница действительно с lex.uz и содержит текст акта.
"""
import argparse
import html as htmllib
import re
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library" / "01_Законодательство"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
      "Accept-Language": "ru,uz;q=0.8"}


def to_url(arg: str) -> str:
    if arg.startswith("http"):
        return arg
    return f"https://lex.uz/ru/docs/{arg.strip()}"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    for enc in ("utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def extract_text(page: str) -> str:
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    page = re.sub(r"<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>", "\n", page, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", page)
    text = htmllib.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc", help="URL на lex.uz или номер документа")
    ap.add_argument("--name", required=True, help="Имя файла в библиотеке, например 'ЗРУ-730 О страховой деятельности'")
    ap.add_argument("--group", default="01_Законы", help="01_Законы | 02_Акты_регуляторов")
    a = ap.parse_args()

    url = to_url(a.doc)
    if "lex.uz" not in url:
        sys.exit("Источник должен быть lex.uz — другие сайты не считаются официальными.")
    page = fetch(url)
    text = extract_text(page)
    if len(text) < 3000 or "lex.uz" not in page.lower():
        sys.exit(f"Страница не похожа на текст акта (символов: {len(text)}). Проверьте адрес: {url}")

    dest = LIB / a.group
    dest.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]+', " ", a.name).strip()
    (dest / f"{safe}.html").write_text(page, encoding="utf-8")
    header = f"Источник: {url}\nЗагружено: {date.today().isoformat()}\n\n"
    (dest / f"{safe}.txt").write_text(header + text, encoding="utf-8")
    print(f"Сохранено: {dest / (safe + '.txt')}  ({len(text):,} символов)")

    # признаки редакции — чтобы агент видел, что скачал действующий текст
    for m in re.findall(r"(в редакции[^\n]{0,120}|редакци[яи][^\n]{0,120}|утратил[а]? силу[^\n]{0,80})", text, flags=re.I)[:5]:
        print("  редакция:", m.strip()[:140])

    # отметка в журнале — робот-юрист в «офисе» показывает, что сделал
    try:
        import sqlite3
        from datetime import datetime
        con = sqlite3.connect(ROOT / "data" / "surveyor.db")
        con.execute("INSERT INTO audit (ts, who, action, entity, detail) VALUES (?,?,?,?,?)",
                    (datetime.now().isoformat(timespec="seconds"), "юрист", "загружен акт с lex.uz", safe, url))
        con.commit(); con.close()
    except Exception:
        pass

    subprocess.run([sys.executable, str(ROOT / "tools" / "library_build.py")], check=False)


if __name__ == "__main__":
    main()
