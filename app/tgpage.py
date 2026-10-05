r"""
Сборка мини-приложения Telegram: исходники в frontend/tg/ → одна страница app/tg.html.

Telegram получает один HTML, как раньше: стили и скрипт встроены в страницу. Исходники:
  frontend/tg/tg.html   — разметка-шаблон с метками /*@TG_CSS*/ и /*@TG_JS*/
  frontend/tg/tg.css    — стили
  frontend/tg/js/*.js   — модули скрипта, склеиваются в порядке MODULES в один <script>
                     (общие имена — CH, MK, TAB, ME и т. п. — глобальные, как в одном файле)

Собранный app/tg.html хранится в репозитории и отдаётся сервером (app/telegram.py, GET /tg).
Пересборка: sandbox\.venv\Scripts\python.exe tools\tg_build.py; в режиме разработчика
(SURVEYOR_DEV=1) сервер при старте пересобирает страницу сам, если исходники новее (ensure_fresh).
Подстановок на сервере нет: имя бота, язык и версия приходят странице из /tg/status, /tg/me и /i18n.js.
"""
from pathlib import Path

APP = Path(__file__).resolve().parent
SRC = APP.parent / "frontend" / "tg"
OUT = APP / "tg.html"
TEMPLATE = SRC / "tg.html"
CSS = SRC / "tg.css"
# порядок — порядок выполнения верхнего уровня скрипта; boot() — последней строкой users.js
MODULES = ["core", "nav", "legal", "calc", "wizard", "act", "osgor", "settings", "users"]
CSS_MARK, JS_MARK = "/*@TG_CSS*/", "/*@TG_JS*/"


def sources() -> list:
    return [TEMPLATE, CSS] + [SRC / "js" / (m + ".js") for m in MODULES]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def build_page() -> str:
    """Готовая страница мини-приложения из исходников frontend/tg/."""
    tpl = _read(TEMPLATE)
    if tpl.count(CSS_MARK) != 1 or tpl.count(JS_MARK) != 1:
        raise ValueError("в frontend/tg/tg.html должно быть ровно по одной метке " + CSS_MARK + " и " + JS_MARK)
    js = "".join(_read(SRC / "js" / (m + ".js")) for m in MODULES)
    if "</script" in js.lower():
        raise ValueError("в модуле скрипта встретилось «</script» — страница сломается")
    return tpl.replace(CSS_MARK, _read(CSS)).replace(JS_MARK, js)


def is_fresh() -> bool:
    """Собранный app/tg.html совпадает с тем, что дают исходники."""
    return OUT.exists() and OUT.read_text(encoding="utf-8") == build_page()


def write_page() -> bool:
    """Пересобирает app/tg.html. True — файл изменился."""
    page = build_page()
    if OUT.exists() and OUT.read_text(encoding="utf-8") == page:
        return False
    OUT.write_bytes(page.encode("utf-8"))
    return True


def ensure_fresh() -> bool:
    """Режим разработчика: исходники новее собранного файла — пересобрать. Без исходников ничего не делает."""
    if not TEMPLATE.exists():
        return False
    if OUT.exists():
        built = OUT.stat().st_mtime
        if all(p.stat().st_mtime <= built for p in sources()):
            return False
    return write_page()
