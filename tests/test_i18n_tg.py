"""
Языки мини-аппа (app/tg.html) «как швейцарские часы» — задача 145.

Запуск из корня проекта:
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_i18n_tg.py

Без pytest и без сервера: читаются файлы app/tg.html, app/i18n.js, app/i18n/*.json.

Что проверяется:
  1. наборы ключей трёх словарей совпадают, пустых значений нет;
  2. каждый ключ, который мини-апп берёт по data-i18n или T("ключ", "запас"), есть в словарях,
     а русский словарь совпадает с запасным текстом страницы — при смене языка на русский
     подписи не меняются местами и языки не смешиваются;
  3. в разметке нет видимого кириллического текста вне элементов с data-i18n
     (и подсказок/подписей без data-i18n-placeholder / data-i18n-aria / data-i18n-title);
  4. в JS кириллица — только запасной текст внутри T(), значения сервера в сравнениях
     (=== "…", case "…") и небольшой явный список исключений ниже;
  5. смена языка без перезагрузки: app/i18n.js шлёт событие i18n:changed, мини-апп его слушает
     и перерисовывает текущий экран (repaintCurrent);
  6. новые ключи мини-аппа в узбекском словаре отмечены как рабочий перевод (_meta.draft).
"""
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TG = (ROOT / "app" / "tg.html").read_text(encoding="utf-8")
I18N_JS = (ROOT / "app" / "i18n.js").read_text(encoding="utf-8")
DICTS = {lang: json.loads((ROOT / "app" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
         for lang in ("ru", "uz", "en")}
CYR = re.compile("[А-Яа-яЁё]")

# Кириллица в JS, которая НЕ выводится на экран: значения, которые сервер присылает или принимает.
# Каждое — с причиной. Сравнения (=== "…", case "…") сюда вносить не нужно — они разрешены правилом.
JS_EXCEPTIONS = {
    "другое": "значение пункта «другое» из /tg/register/positions (запас, если сервер не прислал)",
    "телефон": "scope согласия для /tg/consent",
    "основное": "scope согласия для /tg/consent",
    "сотрудник": "роль по умолчанию — ключ для roleName(), не подпись",
    "ожидает подтверждения": "статус по умолчанию — ключ для statusName(), не подпись",
    "не вошёл": "статус сессии, как его пишет сервер (/tg/me)",
    "заблокирован": "статус учётной записи, как его пишет сервер",
    "Склад": "тип объекта по умолчанию — значение справочника сервера",
    "сум": "единица денежных полей в ответе /analytics/risk/fields",
    "одобрил": "решение для POST /requests/{id}/decide",
    "отклонил": "решение для POST /requests/{id}/decide",
    "вопрос": "решение для POST /requests/{id}/decide",
    "правка администратора в мини-аппе": "источник правки коэффициента — пишется в базу",
    "решение администратора в мини-аппе": "причина блокировки — пишется в журнал сервера",
    "разбор": "ключ ответа сервера на загрузку документа",
}

passed = failed = 0


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ок  ", name)
    else:
        failed += 1
        print("  ПЛОХО", name, extra)


# ---------------------------------------------------------------------------------------------
#  Разбор JS: строки без комментариев и регулярных выражений
# ---------------------------------------------------------------------------------------------

REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")


def js_tokens(src: str):
    """Возвращает (код без комментариев, список строковых литералов (значение, начало, конец) в этом коде)."""
    out, lits = [], []
    i, n = 0, len(src)
    last = ""                                  # последний значимый символ кода
    pos = [0]                                  # длина уже выданного кода

    def emit(s):
        out.append(s)
        pos[0] += len(s)

    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            emit(" ")
            continue
        if c in "\"'`":
            j = i + 1
            buf = []
            while j < n and src[j] != c:
                if src[j] == "\\" and j + 1 < n:
                    buf.append(src[j:j + 2])
                    j += 2
                    continue
                buf.append(src[j])
                j += 1
            raw = "".join(buf)
            start = pos[0]
            emit(src[i:j + 1])
            lits.append((raw, start, start + (j + 1 - i)))
            i = j + 1
            last = c
            continue
        if c == "/" and (last in REGEX_BEFORE or last == "" or re.search(r"\b(return|typeof)\s*$", "".join(out[-3:]))):
            j, in_cls = i + 1, False
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    in_cls = True
                elif src[j] == "]":
                    in_cls = False
                elif src[j] == "/" and not in_cls:
                    break
                elif src[j] == "\n":
                    break
                j += 1
            j += 1
            while j < n and src[j].isalpha():
                j += 1
            emit('/re/')
            i = j
            last = "/"
            continue
        emit(c)
        if not c.isspace():
            last = c
        i += 1
    return "".join(out), lits


def script_of(html: str) -> str:
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    return max(blocks, key=len)


def used_keys(html: str) -> dict:
    """{ключ: запасной русский текст} — из разметки и из T("ключ", "запас")."""
    pairs = {}
    markup = re.sub(r"<script>.*?</script>", "", html, flags=re.S)
    for m in re.finditer(r'<(\w+)[^>]*?\sdata-i18n="([^"]+)"[^>]*>([^<]*)<', markup):
        pairs[m.group(2)] = m.group(3).strip()
    for attr, dattr in (("placeholder", "data-i18n-placeholder"), ("aria-label", "data-i18n-aria"),
                        ("title", "data-i18n-title")):
        for m in re.finditer(r"<[^>]*\s" + dattr + r'="([^"]+)"[^>]*>', markup):
            v = re.search(r"\s" + attr + r'="([^"]*)"', m.group(0))
            if v:
                pairs[m.group(1)] = v.group(1)
    code, lits = js_tokens(script_of(html))
    for raw, start, _end in lits:
        before = code[max(0, start - 120):start]
        m = re.search(r'\bT\(\s*"([^"]+)"\s*,\s*$', before)
        if m:
            pairs[m.group(1)] = json.loads('"' + raw + '"')
    return pairs


# ---------------------------------------------------------------------------------------------

def test_same_keys():
    keys = {lang: {k for k in d if not k.startswith("_")} for lang, d in DICTS.items()}
    ok("у ru, uz, en одинаковый набор ключей", keys["ru"] == keys["uz"] == keys["en"],
       {l: sorted(keys["ru"] ^ keys[l])[:10] for l in ("uz", "en")})
    empty = [(l, k) for l, d in DICTS.items() for k, v in d.items() if not k.startswith("_") and not str(v).strip()]
    ok("пустых значений нет", not empty, empty[:10])


def test_keys_present():
    pairs = used_keys(TG)
    ok(f"мини-апп берёт подписи по ключам ({len(pairs)} шт.)", len(pairs) > 400, len(pairs))
    missing = [k for k in pairs if k not in DICTS["ru"]]
    ok("каждый ключ мини-аппа есть в словарях", not missing, missing[:15])
    diff = [(k, DICTS["ru"][k], v) for k, v in pairs.items() if k in DICTS["ru"] and DICTS["ru"][k] != v]
    ok("русский словарь совпадает с запасным текстом страницы", not diff, diff[:5])
    same = [k for k in pairs if k in DICTS["en"] and CYR.search(str(DICTS["en"][k]))]
    ok("в английском словаре у ключей мини-аппа нет кириллицы", not same, same[:10])
    same = [k for k in pairs if k in DICTS["uz"] and CYR.search(str(DICTS["uz"][k]))]
    ok("в узбекском словаре у ключей мини-аппа нет кириллицы", not same, same[:10])


class Visible(HTMLParser):
    """Видимый кириллический текст разметки без data-i18n."""
    VOID = {"meta", "input", "br", "img", "link", "hr", "source", "wbr"}
    ATTRS = {"placeholder": "data-i18n-placeholder", "aria-label": "data-i18n-aria",
             "title": "data-i18n-title", "alt": "data-i18n-alt", "data-empty": "data-i18n-empty"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for attr, need in self.ATTRS.items():
            if attr in a and a[attr] and CYR.search(a[attr]) and need not in a:
                self.bad.append(f"<{tag} {attr}=\"{a[attr][:40]}\"> без {need}")
        if tag not in self.VOID:
            self.stack.append((tag, "data-i18n" in a))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if CYR.search(data) and not (self.stack and self.stack[-1][1]):
            where = self.stack[-1][0] if self.stack else "?"
            self.bad.append(f"<{where}> «{data.strip()[:50]}»")


def test_markup():
    markup = re.sub(r"<script\b[^>]*>.*?</script>", "", TG, flags=re.S)
    markup = re.sub(r"<style>.*?</style>", "", markup, flags=re.S)
    markup = re.sub(r"<!--.*?-->", "", markup, flags=re.S)
    p = Visible()
    p.feed(markup)
    ok("в разметке нет кириллицы вне data-i18n", not p.bad, p.bad[:12])


def test_js_literals():
    code, lits = js_tokens(script_of(TG))
    bad = []
    for raw, start, end in lits:
        if not CYR.search(raw):
            continue
        before, after = code[max(0, start - 120):start], code[end:end + 12]
        if re.search(r'\bT\(\s*"[^"]+"\s*,\s*$', before):
            continue                                   # запасной текст перевода
        if re.search(r"(===|!==|==|!=)\s*$", before) or re.search(r"\bcase\s+$", before) or re.match(r"\s*(===|!==)", after):
            continue                                   # значение сервера в сравнении
        if raw in JS_EXCEPTIONS:
            continue
        bad.append(raw[:60])
    ok("в JS кириллица только в T(), сравнениях и списке исключений", not bad, bad[:15])
    ok("регулярные выражения и комментарии не принимаются за строки",
       not any("замечани[а-я]" in r or "Осталось попыток:" in r for r, _s, _e in lits), "")


def test_live_switch():
    ok("app/i18n.js шлёт событие i18n:changed", '"i18n:changed"' in I18N_JS and "dispatchEvent" in I18N_JS)
    ok("app/i18n.js меняет язык без перезагрузки по window.I18N_LIVE", "window.I18N_LIVE" in I18N_JS and "function setLang(" in I18N_JS)
    ok("мини-апп включает живую смену языка до подключения i18n.js",
       TG.index("window.I18N_LIVE = true") < TG.index('<script src="/i18n.js">'))
    ok("мини-апп слушает i18n:changed и перерисовывает экран",
       'addEventListener("i18n:changed", repaintCurrent)' in TG and "function repaintCurrent(" in TG)
    rep = re.search(r"const REPAINT = \{(.*?)\};", TG, re.S)
    sections = set(re.findall(r'data-section="([^"]+)"', re.sub(r"<script>.*?</script>", "", TG, flags=re.S)))
    covered = set(re.findall(r"(\w+):", rep.group(1))) if rep else set()
    ok("у каждого раздела есть перерисовка при смене языка", rep is not None and sections <= covered, sections - covered)
    ok("числа и даты — по языку (ru-RU / uz-UZ / en-US)",
       '"uz-UZ"' in TG and '"en-US"' in TG and 'toLocaleString("ru-RU")' not in TG)


def test_uz_draft():
    draft = set(DICTS["uz"].get("_meta", {}).get("draft") or [])
    new = [k for k in DICTS["uz"] if re.match(r"tg\.(an|os|adm|reg|rail|err|u)\.", k)]
    miss = [k for k in new if k not in draft]
    ok(f"новые ключи мини-аппа ({len(new)}) отмечены в uz как рабочий перевод", new and not miss, miss[:10])
    meta_ok = all(d.get("_meta", {}).get("keys") == len([k for k in d if not k.startswith("_")]) for d in DICTS.values())
    ok("_meta.keys совпадает с числом ключей", meta_ok)


if __name__ == "__main__":
    for t in (test_same_keys, test_keys_present, test_markup, test_js_literals, test_live_switch, test_uz_draft):
        print(t.__name__)
        t()
    print(f"\nпройдено {passed}, не пройдено {failed}")
    sys.exit(1 if failed else 0)
