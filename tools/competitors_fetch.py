"""
Документы конкурентов (правила, оферты, страницы продуктов) — проверка новых версий на сайтах страховщиков.

Реестр источников — docs/competitors_sources.json: собирается из шапок уже скачанных .txt
(library/03_Рынок_НАПП/Конкуренты/<Компания>/*.txt: «Компания», «Документ», «Источник», «Язык», «Формат оригинала»).
Для pdf/docx в реестр пишется sha256 оригинала, если он лежит рядом: по нему сервер без оригиналов
(в образ Docker идут только .txt) понимает, что документ не менялся.

Проход по реестру:
  * robots.txt домена (urllib.robotparser): закрыт или недоступен — источник пропускается, причина в итоге;
  * пауза не меньше 2,5 с между обращениями к одному домену; User-Agent «ai-surveyor (INSON)»;
  * содержимое сравнивается с прежним по sha256 (pdf/docx — байты файла, страница — нормализованный текст);
    страница, у которой прежнего отпечатка нет, при первом проходе только запоминается («baseline»):
    извлечение текста здесь не совпадает с прежним сборщиком, и без этого все страницы «изменились» бы разом;
  * изменилось — новая версия .txt (шапка как у существующих) в выходной папке, прежняя — в _history
    с датой в имени; персональные данные вырезаются только по явным шаблонам: «Фамилия И. О.»,
    мобильные телефоны +998 (9x, 33, 50, 55, 77, 88, 20) и e-mail на общедоступных почтовых доменах;
  * на страницах (html) ищутся новые ссылки на pdf/docx того же сайта с «правилами/офертой/полисом» в тексте
    ссылки или адресе — они добавляются как источники с пометкой new и сразу скачиваются (в журнал — отдельно).

Только стандартная библиотека и pymupdf (уже в окружении) — для PDF; DOCX читается из zip напрямую.

Запуск (из корня проекта):
    sandbox/.venv/Scripts/python.exe tools/competitors_fetch.py --registry      # пересобрать реестр из шапок
    sandbox/.venv/Scripts/python.exe tools/competitors_fetch.py --run [--limit N]  # проход по сайтам
На сервере проход раз в 7 дней запускает app/competitors.py (поток «competitors-refresh»).
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zipfile
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Optional
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "docs" / "competitors_sources.json"
LOCAL_DIR = ROOT / "library" / "03_Рынок_НАПП" / "Конкуренты"
HISTORY = "_history"
USER_AGENT = "ai-surveyor (INSON)"
DOMAIN_PAUSE_SEC = 2.5
TIMEOUT_SEC = 30
MAX_BYTES = 25 * 1024 * 1024
MAX_NEW_PER_RUN = 20
SEP = "-----"
MARK = ("публичный документ страховщика с его официального сайта; не норма права. "
        "Персональные данные (телефоны, e-mail, ФИО) вырезаны: замен %d.")
LANGS = {"ru": "русский", "uz-latn": "узбекский (латиница)", "uz-cyrl": "узбекский (кириллица)", "en": "английский"}

# --------------------------------------------------------------------------- #
#  Шапка .txt и реестр
# --------------------------------------------------------------------------- #

HEAD_KEYS = ("Компания", "Индексировать", "Документ", "Источник", "Загружено", "Язык", "Формат оригинала",
             "Пометка", "Текст", "Поиск", "Версия")


def split_txt(text: str) -> tuple:
    """(поля шапки, строки шапки, тело). Шапка — до строки «-----»."""
    head, sep, body = text.partition("\n" + SEP + "\n")
    if not sep:
        return {}, [], text
    fields, lines = {}, head.splitlines()
    for line in lines:
        k, s, v = line.partition(":")
        if s and k.strip() in HEAD_KEYS and k.strip() not in fields:
            fields[k.strip()] = v.strip()
    return fields, lines, body


def kind_of(fmt: str, url: str) -> str:
    f = (fmt or "").lower()
    if f.startswith("pdf"):
        return "pdf"
    if f.startswith("docx") or f.startswith("doc"):
        return "docx"
    if f.startswith("html"):
        return "html"
    path = urllib.parse.urlsplit(url or "").path.lower()
    return "pdf" if path.endswith(".pdf") else "docx" if path.endswith(".docx") else "html"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def norm_text(t: str) -> str:
    """Текст страницы для сравнения: без различий в пробелах и регистре."""
    return re.sub(r"\s+", " ", (t or "")).strip().lower()


def sha256_text(t: str) -> str:
    return sha256_bytes(norm_text(t).encode("utf-8"))


def norm_url(u: str) -> str:
    p = urllib.parse.urlsplit((u or "").strip())
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, p.query, ""))


def build_registry(base: Path = LOCAL_DIR) -> dict:
    """Реестр из шапок .txt. Пути — относительно папки «Конкуренты» (Компания/файл.txt)."""
    sources = []
    for p in sorted(base.rglob("*.txt")):
        rel = p.relative_to(base)
        if HISTORY in rel.parts:
            continue
        fields, _, body = split_txt(p.read_text(encoding="utf-8", errors="replace"))
        url = fields.get("Источник", "")
        if not url.startswith(("http://", "https://")):
            continue
        kind = kind_of(fields.get("Формат оригинала", ""), url)
        item = {"company": fields.get("Компания") or rel.parts[0], "document": fields.get("Документ") or p.stem,
                "url": url, "lang": fields.get("Язык", ""), "type": kind, "txt": rel.as_posix(),
                "indexed": "Индексировать: нет" not in "\n".join(f"{k}: {v}" for k, v in fields.items()),
                "loaded": fields.get("Загружено", ""), "status": "known"}
        if (fields.get("Текст") or "").startswith("PDF — скан") and "расшифровка" in fields.get("Текст", ""):
            item["manual_text"] = True      # текст набран вручную по изображениям — при смене версии предупредить
        orig = p.with_suffix("." + kind) if kind in ("pdf", "docx") else None
        if orig is not None and orig.exists():
            item["orig_sha256"] = sha256_bytes(orig.read_bytes())
        sources.append(item)
    return {"generated_at": datetime.now().isoformat(timespec="seconds"), "generator": "tools/competitors_fetch.py",
            "base": "library/03_Рынок_НАПП/Конкуренты", "count": len(sources), "sources": sources}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# --------------------------------------------------------------------------- #
#  Персональные данные: только явные шаблоны
# --------------------------------------------------------------------------- #

_CYR = r"[А-ЯЁЎҚҒҲ][а-яёўқғҳ]+"
# «Иванов И. И.» и «И. И. Иванов» — так пишут людей; адреса («ул. Навои») под шаблон не попадают
NAME_INITIALS = re.compile(rf"\b{_CYR}[^\S\r\n]+[А-ЯЁ]\.[^\S\r\n]*[А-ЯЁ]\.|\b[А-ЯЁ]\.[^\S\r\n]*[А-ЯЁ]\.[^\S\r\n]*{_CYR}")
# мобильные номера Узбекистана; городские (71 …) и короткие номера колл-центров не трогаем
MOBILE = re.compile(r"(?<![\d])(?:\+?998)[\s\-()]*(?:9\d|33|50|55|77|88|20)[\s\-()]*\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)")
FREE_MAIL = ("gmail.com", "mail.ru", "yandex.ru", "yandex.com", "ya.ru", "inbox.ru", "list.ru", "bk.ru",
             "rambler.ru", "outlook.com", "hotmail.com", "live.com", "icloud.com", "yahoo.com", "umail.uz",
             "proton.me", "protonmail.com")
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")


def strip_pd(text: str) -> tuple:
    """(текст, число замен). Корпоративные адреса и телефоны компании остаются: это публичные реквизиты."""
    n = 0

    def sub(rx, repl, s):
        nonlocal n
        out, k = rx.subn(repl, s)
        n += k
        return out

    text = sub(NAME_INITIALS, "[ФИО]", text)
    text = sub(MOBILE, "[ТЕЛЕФОН]", text)

    def mail(m):
        nonlocal n
        if m.group(1).lower() in FREE_MAIL:
            n += 1
            return "[E-MAIL]"
        return m.group(0)
    text = EMAIL.sub(mail, text)
    return text, n


# --------------------------------------------------------------------------- #
#  Извлечение текста
# --------------------------------------------------------------------------- #

_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table",
          "ul", "ol", "header", "footer", "nav", "dt", "dd", "td", "th"}
_SKIP = {"script", "style", "noscript", "svg", "head", "template", "iframe"}


class _Html(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.skip, self._a = [], [], 0, None

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")
        if tag == "a":
            self._a = [dict(attrs).get("href") or "", []]

    def handle_endtag(self, tag):
        if tag in _SKIP and self.skip:
            self.skip -= 1
        elif tag in _BLOCK:
            self.parts.append("\n")
        if tag == "a" and self._a is not None:
            self.links.append((self._a[0], " ".join("".join(self._a[1]).split())))
            self._a = None

    def handle_data(self, data):
        if self.skip:
            return
        self.parts.append(data)
        if self._a is not None:
            self._a[1].append(data)


def decode_html(data: bytes, content_type: str = "") -> str:
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.I) or \
        re.search(rb"<meta[^>]+charset=[\"']?([\w\-]+)", data[:4000], re.I)
    enc = m.group(1) if m else "utf-8"
    enc = enc.decode("ascii", "ignore") if isinstance(enc, bytes) else enc
    try:
        return data.decode(enc, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def html_text(data: bytes, content_type: str = "") -> tuple:
    """(текст, ссылки [(href, текст ссылки)])."""
    p = _Html()
    p.feed(decode_html(data, content_type))
    lines = [" ".join(l.split()) for l in "".join(p.parts).splitlines()]
    return "\n".join(l for l in lines if l), p.links


def docx_text(data: bytes) -> str:
    """Абзацы и строки таблиц («ячейка | ячейка») из word/document.xml."""
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(w + "body")
    out = []

    def para(p):
        return "".join((t.text or "") for t in p.iter(w + "t")).strip()
    for el in list(body) if body is not None else []:
        if el.tag == w + "p":
            t = para(el)
            if t:
                out.append(t)
        elif el.tag == w + "tbl":
            for tr in el.iter(w + "tr"):
                cells = [" ".join(para(p) for p in tc.iter(w + "p")).strip() for tc in tr.findall(w + "tc")]
                if any(cells):
                    out.append(" | ".join(cells))
    return "\n".join(out)


_LAT, _CYRL = re.compile(r"[A-Za-z]"), re.compile(r"[А-Яа-яЁёЎўҚқҒғҲҳ]")


def mixed_share(text: str) -> float:
    """Доля слов, где латиница и кириллица вперемешку, — признак негодного текстового слоя PDF."""
    words = [w for w in re.findall(r"\w{3,}", text) if not w.isdigit()]
    if not words:
        return 0.0
    bad = sum(1 for w in words if _LAT.search(w) and _CYRL.search(w))
    return bad / len(words)


def pdf_text(data: bytes) -> dict:
    try:
        import pymupdf
    except ImportError:                                   # pragma: no cover
        import fitz as pymupdf
    pages, empty = [], 0
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            t = page.get_text("text")
            if len(t.strip()) < 20:
                empty += 1
            pages.append(t)
    text = "\n".join(pages).strip()
    fmt = f"PDF, страниц {len(pages)}, без текстового слоя {empty}"
    index, note = True, None
    share = mixed_share(text)
    if not pages or empty > len(pages) / 2:
        index, text = False, ""
        note = "не извлечён — PDF — скан без текстового слоя; распознавание не выполнялось. Оригинал лежит рядом (.pdf)."
    elif share > 0.30:                 # прежний сборщик брал в индекс и 21 %, с пометкой — так же
        index, text = False, ""
        note = ("не извлечён — текстовый слой PDF негодный (смесь латиницы и кириллицы в словах) — текст не взят. "
                "Оригинал лежит рядом (.pdf).")
    elif share > 0.03:
        fmt += f"; текстовый слой с ошибками распознавания (смешанных слов {round(share * 100)}%) — цитаты сверять с PDF"
    if index and len(text) < 500:
        fmt += "; текста мало"
    return {"text": text, "fmt": fmt, "index": index, "note": note}


def extract(kind: str, data: bytes, content_type: str = "") -> dict:
    """{text, fmt, index, note, links}. Ошибка разбора — исключение (её запишет вызывающий)."""
    if kind == "pdf":
        if not data.startswith(b"%PDF"):
            raise ValueError("вместо PDF пришла страница или другой файл")
        return {**pdf_text(data), "links": []}
    if kind == "docx":
        if not data.startswith(b"PK"):
            raise ValueError("вместо DOCX пришла страница или другой файл")
        return {"text": docx_text(data), "fmt": "DOCX", "index": True, "note": None, "links": []}
    text, links = html_text(data, content_type)
    return {"text": text, "fmt": "HTML-страница", "index": True, "note": None, "links": links}


def detect_lang(text: str) -> str:
    t = text[:20000]
    if not t.strip():
        return "—"
    if len(re.findall(r"[ЎўҚқҒғҲҳ]", t)) > 5:
        return LANGS["uz-cyrl"]
    cyr, lat = len(_CYRL.findall(t)), len(_LAT.findall(t))
    if cyr >= lat:
        return LANGS["ru"]
    if re.search(r"o[ʻ‘'`]|g[ʻ‘'`]|\b(va|uchun|bilan|sug[ʻ‘'`]?urta)\b", t, re.I):
        return LANGS["uz-latn"]
    return LANGS["en"]


# --------------------------------------------------------------------------- #
#  Сеть: robots.txt, пауза на домен
# --------------------------------------------------------------------------- #

def http_get(url: str) -> tuple:
    """(код, content-type, байты). Ошибка сети — исключение; HTTP-ошибка — код и пустое тело."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            data = r.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("файл больше %d МБ" % (MAX_BYTES // 1024 // 1024))
            return r.status, r.headers.get("Content-Type", ""), data
    except urllib.error.HTTPError as e:
        return e.code, "", b""


class Client:
    """Вежливый клиент: robots.txt на домен, пауза между запросами к одному домену."""

    def __init__(self, get: Callable = http_get, pause: float = DOMAIN_PAUSE_SEC, sleep: Callable = time.sleep):
        self.get, self.pause, self.sleep = get, pause, sleep
        self.last, self.robots = {}, {}

    def _wait(self, host: str):
        last = self.last.get(host)
        if last is not None:
            left = self.pause - (time.monotonic() - last)
            if left > 0:
                self.sleep(left)
        self.last[host] = time.monotonic()

    def robots_for(self, url: str):
        """RobotFileParser или строка-причина отказа («закрыт robots.txt» / «robots.txt недоступен»)."""
        p = urllib.parse.urlsplit(url)
        host = p.netloc.lower()
        if host in self.robots:
            return self.robots[host]
        self._wait(host)
        rp = urllib.robotparser.RobotFileParser()
        try:
            code, _, data = self.get(f"{p.scheme}://{p.netloc}/robots.txt")
        except Exception as e:
            res = "robots.txt недоступен: %s" % type(e).__name__
        else:
            if code in (404, 410):
                rp.parse([])                     # robots.txt нет — ограничений нет
                res = rp
            elif code in (401, 403):
                res = "закрыт robots.txt (доступ к нему запрещён, %d)" % code
            elif 200 <= code < 300:
                rp.parse(decode_html(data).splitlines())
                res = rp
            else:
                res = "robots.txt недоступен (%d)" % code
        self.robots[host] = res
        return res

    def fetch(self, url: str) -> tuple:
        """(код, content-type, байты) или исключение PermissionError с причиной отказа robots."""
        rp = self.robots_for(url)
        if isinstance(rp, str):
            raise PermissionError(rp)
        if not rp.can_fetch(USER_AGENT, url):
            raise PermissionError("закрыт robots.txt")
        self._wait(urllib.parse.urlsplit(url).netloc.lower())
        return self.get(url)


# --------------------------------------------------------------------------- #
#  Версии
# --------------------------------------------------------------------------- #

def render_txt(src: dict, ex: dict, body: str, n_pd: int, today: date, keep: list, version: str = "") -> str:
    head = [f"Компания: {src['company']}"]
    manual_skip = any(l.startswith("Поиск:") for l in keep)
    if manual_skip or not ex["index"]:
        head.append("Индексировать: нет")
    head += [f"Документ: {src['document']}", f"Источник: {src['url']}", f"Загружено: {today.strftime('%d.%m.%Y')}",
             f"Язык: {detect_lang(body) if body else (src.get('lang') or '—')}", f"Формат оригинала: {ex['fmt']}",
             "Пометка: " + MARK % n_pd]
    if ex.get("note"):
        head.append("Текст: " + ex["note"])
    head += keep
    if version:
        head.append("Версия: " + version)
    return "\n".join(head) + "\n" + SEP + "\n" + body.strip() + "\n"


def _safe(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name or "").strip(" .")
    return re.sub(r"\s+", " ", name)[:120] or "документ"


def current_file(rel: str, out_base: Path, image_base: Optional[Path]) -> Optional[Path]:
    """Действующая версия .txt: из выходной папки или из образа (library) — более свежая."""
    best = None
    for b in (image_base, out_base):
        if b is None:
            continue
        p = Path(b) / rel
        if p.exists() and (best is None or p.stat().st_mtime >= best.stat().st_mtime):
            best = p
    return best


def write_version(src: dict, ex: dict, raw: bytes, out_base: Path, image_base: Optional[Path], today: date) -> dict:
    """Пишет новую версию; прежнюю — в _history с датой. Возвращает {txt, history, pd}."""
    rel = src["txt"]
    cur = current_file(rel, out_base, image_base)
    keep, hist_rel = [], None
    if cur is not None:
        _, lines, _ = split_txt(cur.read_text(encoding="utf-8", errors="replace"))
        keep = [l for l in lines if l.startswith("Поиск:")]     # ручное решение «не индексировать» сохраняем
        hist_dir = out_base / Path(rel).parent / HISTORY
        hist_dir.mkdir(parents=True, exist_ok=True)
        stamp = today.isoformat()
        hist = hist_dir / f"{Path(rel).stem} ({stamp}).txt"
        k = 2
        while hist.exists():
            hist = hist_dir / f"{Path(rel).stem} ({stamp}-{k}).txt"
            k += 1
        hist.write_bytes(cur.read_bytes())
        orig = cur.with_suffix("." + src["type"]) if src["type"] in ("pdf", "docx") else None
        if orig is not None and orig.exists():
            hist.with_suffix("." + src["type"]).write_bytes(orig.read_bytes())
        if out_base in cur.parents:
            cur.unlink()                    # из выходной папки — переносим; файл образа не трогаем
            if orig is not None and orig.exists() and out_base in orig.parents:
                orig.unlink()
        hist_rel = (Path(rel).parent / HISTORY / hist.name).as_posix()
    body, n_pd = strip_pd(ex["text"])
    version = f"новая версия от {today.strftime('%d.%m.%Y')}; прежняя — {hist_rel}" if hist_rel else \
        f"новый документ, найден {today.strftime('%d.%m.%Y')}"
    dst = out_base / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_text(render_txt(src, ex, body, n_pd, today, keep, version), encoding="utf-8")
    tmp.replace(dst)
    if src["type"] in ("pdf", "docx"):
        dst.with_suffix("." + src["type"]).write_bytes(raw)        # оригинал рядом, как у прежних
    return {"txt": rel, "history": hist_rel, "pd": n_pd}


# --------------------------------------------------------------------------- #
#  Поиск новых документов на страницах
# --------------------------------------------------------------------------- #

DOC_WORDS = ("правил", "оферт", "полис", "договор", "услови", "программ", "qoida", "oferta", "ofert", "polis",
             "shartnoma", "shart", "dastur", "rules", "offer", "policy", "terms", "қоида", "шартнома")


def _site(host: str) -> str:
    parts = host.lower().split(":")[0].split(".")
    return ".".join(parts[-2:])


def discover(page_url: str, links: list, known: set, company: str, names: Optional[set] = None) -> list:
    out = []
    site = _site(urllib.parse.urlsplit(page_url).netloc)
    for href, text in links:
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        url = norm_url(urllib.parse.urljoin(page_url, href))
        p = urllib.parse.urlsplit(url)
        ext = p.path.lower().rsplit(".", 1)[-1] if "." in p.path else ""
        if ext not in ("pdf", "docx") or _site(p.netloc) != site or url in known:
            continue
        hay = (urllib.parse.unquote(p.path) + " " + (text or "")).lower()
        if not any(w in hay for w in DOC_WORDS):
            continue
        known.add(url)
        name = _safe(text) if text and len(text) > 3 else _safe(Path(urllib.parse.unquote(p.path)).stem)
        txt, k = f"{_safe(company)}/{name}.txt", 2
        while names is not None and txt in names:       # имя занято другим документом — не затираем его
            txt, k = f"{_safe(company)}/{name} ({k}).txt", k + 1
        if names is not None:
            names.add(txt)
        out.append({"company": company, "document": name, "url": url, "lang": "", "type": ext,
                    "txt": txt, "indexed": True, "status": "new", "found_on": page_url,
                    "found_at": date.today().isoformat()})
    return out


# --------------------------------------------------------------------------- #
#  Проход
# --------------------------------------------------------------------------- #

def run(registry: dict, state: dict, out_base: Path, image_base: Optional[Path] = None,
        client: Optional[Client] = None, limit: Optional[int] = None, today: Optional[date] = None,
        find_new: bool = True, log: Callable = print) -> dict:
    """Один проход. state меняется на месте (отпечатки, найденные источники, итог). Возвращает итог."""
    client = client or Client()
    today = today or date.today()
    out_base = Path(out_base)
    t0 = time.time()
    st_src = state.setdefault("sources", {})
    found = state.setdefault("discovered", [])
    sources = list(registry.get("sources") or []) + list(found)
    known = {norm_url(s["url"]) for s in sources}
    names = {s["txt"] for s in sources}
    summary = {"started_at": datetime.now().isoformat(timespec="seconds"), "checked": 0, "changed": 0,
               "unchanged": 0, "baseline": 0, "new_found": 0, "new_saved": 0, "robots_closed": 0, "errors": 0,
               "changes": [], "robots": [], "error_list": [], "new_list": []}
    queue = sources[:limit] if limit else sources
    i = 0
    while i < len(queue):
        src = queue[i]
        i += 1
        key = src["txt"]
        rec = st_src.setdefault(key, {})
        rec["checked_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            code, ctype, data = client.fetch(src["url"])
        except PermissionError as e:
            summary["robots_closed"] += 1
            summary["robots"].append({"txt": key, "url": src["url"], "reason": str(e)})
            rec["status"] = "robots"
            rec["reason"] = str(e)
            continue
        except Exception as e:
            summary["errors"] += 1
            summary["error_list"].append({"txt": key, "url": src["url"], "error": type(e).__name__})
            rec.update(status="error", reason=type(e).__name__)
            continue
        summary["checked"] += 1
        if not 200 <= code < 300 or not data:
            summary["errors"] += 1
            summary["error_list"].append({"txt": key, "url": src["url"], "error": "HTTP %s" % code})
            rec.update(status="error", reason="HTTP %s" % code)
            continue
        try:
            ex = extract(src["type"], data, ctype)
        except Exception as e:
            summary["errors"] += 1
            summary["error_list"].append({"txt": key, "url": src["url"], "error": "разбор: " + type(e).__name__})
            rec.update(status="error", reason="разбор: " + type(e).__name__)
            continue
        digest = sha256_bytes(data) if src["type"] in ("pdf", "docx") else sha256_text(ex["text"])
        prev = rec.get("hash") or (src.get("orig_sha256") if src["type"] in ("pdf", "docx") else None)
        is_new = src.get("status") == "new" and not rec.get("hash")
        if find_new and src["type"] == "html" and ex.get("links"):
            fresh = discover(src["url"], ex["links"], known, src["company"], names)
            room = MAX_NEW_PER_RUN - summary["new_found"]
            for n in fresh[:max(0, room)]:
                found.append(n)
                queue.append(n)
                summary["new_found"] += 1
                summary["new_list"].append({"txt": n["txt"], "url": n["url"], "found_on": n["found_on"]})
        if prev is None and not is_new:
            rec.update(hash=digest, status="baseline", reason=None)        # первый отпечаток страницы
            summary["baseline"] += 1
            continue
        if prev == digest:
            rec.update(hash=digest, status="unchanged", reason=None)
            summary["unchanged"] += 1
            continue
        try:
            w = write_version(src, ex, data, out_base, image_base, today)
        except Exception as e:
            summary["errors"] += 1
            summary["error_list"].append({"txt": key, "url": src["url"], "error": "запись: " + type(e).__name__})
            rec.update(status="error", reason="запись: " + type(e).__name__)
            continue
        rec.update(hash=digest, status="new" if is_new else "changed", reason=None, changed_at=today.isoformat(),
                   history=w["history"])
        if is_new:
            summary["new_saved"] += 1
        else:
            summary["changed"] += 1
        summary["changes"].append({"txt": key, "url": src["url"], "history": w["history"], "new": is_new,
                                   "manual_text_lost": bool(src.get("manual_text")), "indexed": ex["index"]})
        log(("новый: " if is_new else "изменился: ") + key)
    summary["took_s"] = int(time.time() - t0)
    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    state["last_run"] = {k: v for k, v in summary.items()}
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="Документы конкурентов: реестр и проверка новых версий")
    ap.add_argument("--registry", action="store_true", help="пересобрать docs/competitors_sources.json из шапок")
    ap.add_argument("--run", action="store_true", help="пройти по сайтам (сеть)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--state", default=str(ROOT / "data" / "competitors_state.json"))
    a = ap.parse_args(argv)
    if a.registry:
        reg = build_registry()
        save_json(REGISTRY, reg)
        print(f"реестр: {reg['count']} источников → {REGISTRY}")
    if a.run:
        reg = load_json(REGISTRY, {"sources": []})
        state = load_json(Path(a.state), {})
        res = run(reg, state, LOCAL_DIR, None, limit=a.limit)
        save_json(Path(a.state), state)
        print(json.dumps({k: v for k, v in res.items() if not isinstance(v, list)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
