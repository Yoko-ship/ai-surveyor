"""
Загрузка квартальных отчётов НАПП по страховому рынку.

Берёт страницу «Статистика и анализ», находит ссылки на .xlsx и .pdf,
скачивает недостающие файлы в data/inbox. Уже скачанные пропускает.

Запуск:
  python tools/napp_download.py            # только новые файлы
  python tools/napp_download.py --probe    # плюс перебор старых кварталов по шаблону URL
"""
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "data" / "inbox"
PAGE = "https://napp.uz/pages/statistics-and-analysis-for-im"
BASE = "https://napp.uz"
UA = {"User-Agent": "Mozilla/5.0 (ai-surveyor; NAPP open data collector)"}
TIMEOUT = 120


def get(url: str) -> bytes:
    req = urllib.request.Request(quote(url), headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def quote(url: str) -> str:
    """Экранирует кириллицу и пробелы в пути, схему и хост оставляет как есть."""
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme, p.netloc, urllib.parse.quote(p.path), p.query, p.fragment))


def links_from_page() -> list:
    html = get(PAGE).decode("utf-8", "replace")
    found = re.findall(r'href="([^"]+\.(?:xlsx|pdf))"', html)
    out = []
    for href in found:
        url = href if href.startswith("http") else BASE + ("" if href.startswith("/") else "/") + href
        if "/opendata/" in url and url not in out:
            out.append(url)
    return out


def probe_urls(years=range(2021, 2027)) -> list:
    """Старые кварталы на странице не показываются, но лежат по тому же шаблону."""
    roman = {1: "I", 2: "II", 3: "III", 4: "IV"}
    names = ["Страх отчет({r} чорак {y} йил ) uz — filtr.xlsx",
             "Страх отчет({r} чорак {y} йил ) uz.xlsx"]
    out = []
    for y in years:
        for q in range(1, 5):
            for n in names:
                out.append(f"{BASE}/storage/files/shares/opendata/{y}/{q}Q/" + n.format(r=roman[q], y=y))
    return out


def download(url: str) -> str:
    name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
    dest = INBOX / name
    if dest.exists():
        return f"уже есть  {name}"
    data = get(url)
    # сайт отдаёт страницу-заглушку с кодом 200 вместо 404: настоящий xlsx — это zip («PK»)
    if name.lower().endswith(".xlsx") and data[:2] != b"PK":
        return f"пропуск   {name} (вместо файла страница сайта)"
    if len(data) < 10000:
        return f"пропуск   {name} (файл подозрительно мал)"
    dest.write_bytes(data)
    return f"скачан    {name} ({len(data)//1024} КБ)"


def main():
    INBOX.mkdir(parents=True, exist_ok=True)
    urls = links_from_page()
    print(f"На странице найдено файлов: {len(urls)}")
    if "--probe" in sys.argv:
        urls += [u for u in probe_urls() if u not in urls]
    for u in urls:
        try:
            print(download(u))
        except Exception as e:  # отсутствующий квартал при переборе — это норма
            code = getattr(e, "code", "")
            if code != 404:
                print(f"ошибка    {urllib.parse.unquote(u.rsplit('/',1)[-1])}: {e}")


if __name__ == "__main__":
    main()
