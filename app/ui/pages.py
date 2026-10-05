"""Общая раскладка и страницы. Не импортирует точку сборки приложения."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from app import web
from app.config import PROJECT_ROOT as ROOT
router = APIRouter()

# разделы бокового меню: адрес, полное название, значок и короткая подпись для узкой рейки
SIDEBAR_ITEMS = [("/", "Главная", "⌂", "Главная"), ("/ui", "Новый расчёт", "₌", "Расчёт"),
                 ("/admin", "Запросы и админка", "❑", "Запросы"), ("/portfolio", "Портфель", "▤", "Портфель"),
                 ("/approvals", "Согласования", "✓", "Согл."), ("/tasks-page", "Задачи команде", "✎", "Задачи"),
                 ("/reports-page", "Ежедневный доклад", "▦", "Доклад"), ("/stats", "Динамика рынка", "↗", "Рынок"),
                 ("/capacity-page", "Ёмкость и удержание", "◍", "Ёмкость"), ("/calibration", "Калибровка", "⚖", "Калибр."),
                 ("/graph", "Паутина знаний", "◈", "Паутина"), ("/law-feed", "Законодательство", "§", "Закон"),
                 ("/office", "Офис агентов", "◉", "Офис"),
                 ("/admin/deploy", "Запуск и обслуживание", "⚙", "Запуск"), ("/docs", "API", "⌨", "API")]

# Одна раскладка для всех страниц: слева узкая рейка со значками, по «гамбургеру» — панель с названиями.
# Ту же логику повторяет мини-приложение app/tg.html (там свой файл, отдельно от сервера).
SIDEBAR_CSS = """<style>
:root{--sb-paper:#0F1418;--sb-line:#26303A;--sb-muted:#8E9BA6;--sb-accent:#2ED3A2;--sb-dim:#1E8F70;--sb-rail:60px;--sb-wide:240px}
#side{position:fixed;left:0;top:0;bottom:0;z-index:60;width:calc(var(--sb-rail) + env(safe-area-inset-left));
  background:var(--sb-paper);border-right:1px solid var(--sb-line);display:flex;flex-direction:column;
  overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch;
  padding:calc(env(safe-area-inset-top) + 6px) 0 calc(env(safe-area-inset-bottom) + 12px) env(safe-area-inset-left);
  font:14px Manrope,system-ui,sans-serif;transition:width .16s ease}
body.side-open #side{width:calc(var(--sb-wide) + env(safe-area-inset-left));box-shadow:0 0 44px rgba(0,0,0,.45)}
body.side-wide #side{box-shadow:none}
#sbScrim{position:fixed;inset:0;z-index:55;background:rgba(0,0,0,.5);opacity:0;pointer-events:none;transition:opacity .16s}
body.side-open:not(.side-wide) #sbScrim{opacity:1;pointer-events:auto}
body.side-wide #sbScrim{display:none}
#side .burger{background:none;border:0;width:100%;min-height:48px;padding:0;color:var(--sb-muted);cursor:pointer;
  display:flex;align-items:center;gap:12px;font:700 12.5px Manrope,system-ui,sans-serif;text-align:left}
#side .burger i{font-style:normal;font-size:20px;line-height:1;flex:none;width:var(--sb-rail);text-align:center}
#side .burger span{display:none}
body.side-open #side .burger span{display:block}
body.side-wide #side .burger{display:none}
#side .brand{display:flex;align-items:center;gap:12px;min-height:46px;color:#E6ECF0;padding:0}
#side .brand i{font-style:normal;flex:none;width:var(--sb-rail);display:grid;place-items:center}
#side .brand i b{width:32px;height:32px;border-radius:50%;background:var(--sb-dim);display:grid;place-items:center;
  font-weight:800;color:#0F1418;font-size:14px}
#side .brand div{display:none;min-width:0}
body.side-open #side .brand div{display:block}
#side .brand-logo{display:inline-flex;align-items:baseline;font:800 20px/1 Manrope,system-ui,sans-serif;letter-spacing:-.02em}
#side .brand-logo b{color:#8EA9FF} #side .brand-logo b + b{color:#3FBE74}
#side .brand-sub{display:block;font:600 10.5px Manrope,system-ui,sans-serif;color:var(--sb-muted);margin-top:3px;white-space:nowrap}
#sb{display:flex;flex-direction:column;gap:2px;padding:8px 0}
#sb a{position:relative;color:var(--sb-muted);text-decoration:none;min-height:52px;padding:6px 1px;font-weight:600;
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;line-height:1.1;text-align:center}
#sb a i{font-style:normal;font-size:18px;line-height:1}
#sb a u{text-decoration:none;font-size:9.5px;max-width:var(--sb-rail);padding:0 2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#sb a span{display:none;font-size:13.5px}
#sb a.on{color:var(--sb-accent);background:rgba(46,211,162,.10);box-shadow:inset 3px 0 0 var(--sb-accent)}
#sb a:focus-visible,#side .burger:focus-visible{outline:2px solid var(--sb-accent);outline-offset:-2px}
body.side-open #sb a{flex-direction:row;justify-content:flex-start;gap:12px;min-height:48px;padding:6px 12px 6px 0;text-align:left}
body.side-open #sb a i{flex:none;width:var(--sb-rail);text-align:center}
body.side-open #sb a u{display:none}
body.side-open #sb a span{display:block;flex:1 1 auto;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
body{padding-left:calc(var(--sb-rail) + env(safe-area-inset-left)) !important;padding-right:env(safe-area-inset-right)}
body.side-wide{padding-left:calc(var(--sb-wide) + env(safe-area-inset-left)) !important}
</style>"""

SIDEBAR_JS = """<script>
(function(){
  var wide = window.matchMedia("(min-width:900px)");
  var burger = document.getElementById("burger"), scrim = document.getElementById("sbScrim");
  function open(on){
    document.body.classList.toggle("side-open", !!on);
    burger.setAttribute("aria-expanded", on ? "true" : "false");
  }
  function sync(){ document.body.classList.toggle("side-wide", wide.matches); open(wide.matches); }
  if (wide.addEventListener) wide.addEventListener("change", sync); else wide.addListener(sync);
  sync();
  burger.addEventListener("click", function(){ open(!document.body.classList.contains("side-open")); });
  scrim.addEventListener("click", function(){ if (!wide.matches) open(false); });
  document.addEventListener("keydown", function(e){ if (e.key === "Escape" && !wide.matches) open(false); });
})();
</script>"""


def sidebar(active: str) -> str:
    links = "".join(
        f'<a href="{h}"{" class=on" if h == active else ""} title="{t}"'
        f'{" aria-current=page" if h == active else ""}>'
        f'<i aria-hidden="true">{ico}</i><u>{short}</u><span>{t}</span></a>'
        for h, t, ico, short in SIDEBAR_ITEMS)
    return (SIDEBAR_CSS
            + '<aside id="side">'
              '<button class="burger" id="burger" type="button" aria-controls="sb" aria-expanded="false">'
              '<i aria-hidden="true">&#9776;</i><span>Свернуть меню</span></button>'
              '<div class="brand"><i aria-hidden="true"><b>S</b></i>'
              '<div><span class="brand-logo"><b>INS</b><b>ON</b></span>'
              '<span class="brand-sub">Сюрвейер</span></div></div>'
              f'<nav id="sb" aria-label="Разделы">{links}</nav></aside>'
              '<div id="sbScrim"></div>'
            + SIDEBAR_JS)


# Встраивание в единую админку (/admin/hub): страница едет в <iframe> на том же домене,
# и собственное меню там лишнее — прячем и общую рейку sidebar(), и свой <aside> страницы,
# и отступ body под рейку. Права это не меняет: guard/access проверяются как обычно.
EMBED_CSS = ("<style id=\"embed-nav-off\">"
             # прячем только меню (.app>aside и общую рейку), но не панели с данными,
             # например aside.summary на экране расчёта
             "#side,#sbScrim,.app>aside{display:none!important}"
             ".app{grid-template-columns:1fr!important}"
             "body{padding-left:0!important;padding-right:0!important}"
             "</style>")


# «Выйти из админки» (22.09.2026): уводит в мини-апп в режиме обычного пользователя.
# Права не меняет — это только вид; мини-апп по ?mode=user прячет админские разделы и показывает
# плашку «Вернуться в админку». Единая админка /admin/hub рисует кнопку сама, здесь — /admin и /admin/deploy.
EXIT_ADMIN_URL = "/tg?mode=user"
EXIT_ADMIN_PAGES = ("/admin", "/admin/deploy")
EXIT_ADMIN_CSS = (
    "<style id=\"exit-admin-css\">#exitAdmin{position:fixed;top:12px;right:16px;z-index:70;display:inline-flex;"
    "align-items:center;gap:8px;min-height:40px;padding:8px 14px;border-radius:10px;border:1px solid var(--line,#6B7883);"
    "background:var(--card,#161C21);color:var(--ink,#E6ECF0);font:600 13px Manrope,system-ui,sans-serif;"
    "text-decoration:none;box-shadow:0 6px 18px rgba(0,0,0,.25)}"
    "#exitAdmin:hover{background:var(--soft,#1C242B)}#exitAdmin:focus-visible{outline:2px solid #8EA9FF;outline-offset:2px}"
    "header #exitAdmin{position:static;box-shadow:none;margin-right:14px}"
    "@media (max-width:600px){#exitAdmin{top:auto;bottom:62px;right:14px}header #exitAdmin{margin:0 14px 0 0}}</style>")
EXIT_ADMIN_LINK = (f'<a id="exitAdmin" class="btn btn-secondary btn-sm" href="{EXIT_ADMIN_URL}" data-i18n="admin.exit" '
                   'title="Открыть мини-приложение так, как его видит обычный сотрудник">Выйти из админки</a>')
EXIT_ADMIN_HTML = EXIT_ADMIN_CSS + EXIT_ADMIN_LINK


def page(html: str, active: str = "", embed: bool = False) -> str:
    """Одна раскладка для всех экранов.
    embed=True — отдаём без меню (для iframe админки); иначе подставляем общую рейку
    вместо метки <!--SIDEBAR-->, а страницы со своим <aside> остаются как были."""
    if embed:
        return html + EMBED_CSS
    if active in EXIT_ADMIN_PAGES:
        html = html + EXIT_ADMIN_HTML
    if active and "<!--SIDEBAR-->" in html:
        return html.replace("<!--SIDEBAR-->", sidebar(active), 1)
    return html


# Мост UI_BRIDGE убран 20.09.2026: экран /ui (docs/agent_ui.html) сам показывает рынок,
# предупредительные мероприятия, сохраняет запрос и даёт ссылку на PDF.


# Картинки интерфейса (фоны разделов мини-аппа; обложка удалена 28.09.2026) отдаются как есть из app/static.
# Одна строка монтирования; адреса вида /static/bg/calc-640.jpg открыты до входа (app/guard.py, WHITE_PREFIX).


@router.get("/theme.js")
def theme_js(request: Request):
    return web.asset_response(request, ROOT / "app" / "theme.js", "application/javascript")


@router.get("/i18n.js")
def i18n_js(request: Request):
    """Словарь интерфейса на странице: выбор языка, подписи по data-i18n, функция T().
    Отдаётся рядом с /theme.js и так же открыт до входа (app/guard.py)."""
    return web.asset_response(request, ROOT / "app" / "i18n.js", "application/javascript")


AGENT_UI = ROOT / "docs" / "agent_ui.html"


def _ui_page(embed: bool) -> str:
    html = web.read_text(AGENT_UI)
    head = ("<!doctype html><html><meta charset='utf-8'>"
            "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Manrope:wght@600;800&display=swap'>")
    bar = "" if embed else sidebar("/ui")
    return page(head + bar + html + "<script src='/theme.js'></script>", embed=embed)


@router.get("/ui", response_class=HTMLResponse)
def ui(embed: int = 0):
    # склейка страницы (300 КБ) — одна на версию файла, а не на каждый запрос
    return web.build(("ui", bool(embed)), [AGENT_UI], lambda: _ui_page(bool(embed)))


@router.get("/admin", response_class=HTMLResponse)
def admin():
    html = web.read_text(ROOT / "app" / "admin.html")
    html = html.replace("<div class=\"wrap\">", sidebar("/admin") + "<div class=\"wrap\">", 1)
    # в шапке /admin справа уже есть ссылки — кнопку ставим к ним, а не поверх
    if "<header><h1>Админка сюрвейера</h1><nav>" in html:
        return EXIT_ADMIN_CSS + html.replace("<header><h1>Админка сюрвейера</h1><nav>",
                                             "<header><h1>Админка сюрвейера</h1><nav>" + EXIT_ADMIN_LINK, 1)
    return html + EXIT_ADMIN_HTML


ADMIN_HUB = ROOT / "app" / "admin_hub.html"


@router.get("/admin/hub", response_class=HTMLResponse)
@router.get("/admin/hub/", response_class=HTMLResponse)
def admin_hub():
    """Единая админка: каркас с левым меню, разделы открываются в iframe с ?embed=1.
    Файл верстает дизайнер; пока его нет — понятная 404, сервер поднимается как обычно."""
    if not ADMIN_HUB.exists():
        raise HTTPException(404, "страница app/admin_hub.html ещё не сделана")
    return page(web.read_text(ADMIN_HUB), "/admin/hub")


@router.get("/graph", response_class=HTMLResponse)
def graph(embed: int = 0):
    """Паутина знаний в стиле Obsidian: продукты → классы → учётные группы → правила РНП.

    Своего бокового меню страница не имеет, поэтому в режиме встраивания (?embed=1, внутри
    единой админки) убираем только ссылку «← к приложению»: внутри рамки она уводила бы
    пользователя из админки прямо в окне раздела.
    """
    html = web.read_text(ROOT / "docs" / "tariff_web.html")
    back = ('<a href="/stats" style="position:fixed;right:16px;bottom:14px;z-index:9;font:600 13px Manrope,system-ui;'
            'color:#2ED3A2;text-decoration:none;background:#161C21;border:1px solid #26303A;border-radius:999px;padding:7px 13px">← к приложению</a>')
    return "<!doctype html><meta charset='utf-8'>" + html + ("" if embed else back)


@router.get("/", response_class=HTMLResponse)
def index():
    return web.read_text(ROOT / "app" / "home.html").replace("<!--SIDEBAR-->", sidebar("/"))
