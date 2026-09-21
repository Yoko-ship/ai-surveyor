/* Словарь интерфейса на странице: русский, узбекский (латиница), английский.

   Подключается одной строкой, ДО собственного скрипта страницы:
       <script src="/i18n.js"></script>

   Что делает:
     * выбирает язык в том же порядке, что и сервер (app/i18n.py, pick_lang):
       ?lang= → cookie lang → прошлый выбор в localStorage → язык клиента Telegram → русский;
     * забирает /i18n/{lang}.json одним запросом и кладёт его в localStorage,
       чтобы следующая загрузка страницы не ждала сеть;
     * ставит тексты по data-i18n / data-i18n-placeholder / data-i18n-title;
     * даёт T("ключ", "русский запасной текст", {подстановки}) для подписей, которые рисует JS,
       и i18nPaint(узел) — позвать после перерисовки куска страницы;
     * рисует переключатель RU / UZ / EN рядом с переключателем темы;
     * страница, которая до подключения поставила window.I18N_LIVE = true (мини-апп app/tg.html),
       меняет язык БЕЗ перезагрузки: словарь подменяется, тексты по data-i18n ставятся заново,
       а на window уходит событие "i18n:changed" ({detail: {lang}}) — по нему страница перерисовывает
       то, что рисует сама. Остальные страницы по-прежнему перезагружаются.

   Ничего не ломается, если словарь не пришёл: на странице остаётся русский текст.
   Для русского языка запрос вообще не делается — страницы написаны по-русски.                */
(function () {
  "use strict";
  var LANGS = ["ru", "uz", "en"];
  var SHORT = {ru: "RU", uz: "UZ", en: "EN"};
  var TITLE = {ru: "Русский", uz: "Oʻzbekcha", en: "English"};
  var STORE = "surveyor-i18n:";           // кэш словаря по языку
  var PICKED = "surveyor-lang";           // прошлый выбор человека

  function norm(code) {
    code = String(code || "").toLowerCase().split(/[-_]/)[0];
    return LANGS.indexOf(code) >= 0 ? code : null;
  }
  function cookie(name) {
    var m = document.cookie.match("(^|; )" + name + "=([^;]*)");
    return m ? decodeURIComponent(m[2]) : null;
  }
  function saved() { try { return localStorage.getItem(PICKED); } catch (e) { return null; } }
  var tgUser = (window.Telegram && Telegram.WebApp && Telegram.WebApp.initDataUnsafe
                && Telegram.WebApp.initDataUnsafe.user) || null;

  var lang = norm(new URLSearchParams(location.search).get("lang"))
          || norm(cookie("lang")) || norm(saved())
          || norm(tgUser && tgUser.language_code) || "ru";

  var D = {};

  /* ---------- словарь ---------- */
  function cacheGet(l) {
    try { var s = localStorage.getItem(STORE + l); return s ? JSON.parse(s) : null; } catch (e) { return null; }
  }
  function cachePut(l, dict) {
    try { localStorage.setItem(STORE + l, JSON.stringify(dict)); } catch (e) { /* места нет — не беда */ }
  }
  function fetchSync(l) {                 // первая загрузка языка: ждём словарь, чтобы текст не мигал
    try {
      var x = new XMLHttpRequest();
      x.open("GET", "/i18n/" + l + ".json", false);
      x.send(null);
      if (x.status === 200) return JSON.parse(x.responseText);
    } catch (e) { /* сервер не ответил — остаётся русский текст страницы */ }
    return null;
  }
  function fetchLater(l) {                // словарь мог обновиться — тихо обновляем кэш
    fetch("/i18n/" + l + ".json").then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { if (j) { cachePut(l, j); if (l === lang) { D = j; paint(); } } })
      .catch(function () {});
  }
  if (lang !== "ru") {
    var cached = cacheGet(lang);
    if (cached) { D = cached; fetchLater(lang); }
    else { var got = fetchSync(lang); if (got) { D = got; cachePut(lang, got); } }
  }

  /* ---------- перевод ---------- */
  /* T("calc.premium_note", "Ставка годовая", {days: 90})
     Второй аргумент — русский текст из кода: он и остаётся, если ключа в словаре нет. */
  function T(key, fallback, vars) {
    if (fallback && typeof fallback === "object") { vars = fallback; fallback = null; }
    var s = (D && D[key] != null) ? D[key] : (fallback != null ? fallback : key);
    if (vars) for (var k in vars) s = String(s).split("{" + k + "}").join(vars[k]);
    return s;
  }
  function paint(root) {
    var box = root || document;
    box.querySelectorAll("[data-i18n]").forEach(function (e) {
      var v = D[e.dataset.i18n]; if (v != null) e.textContent = v;
    });
    box.querySelectorAll("[data-i18n-placeholder]").forEach(function (e) {
      var v = D[e.dataset.i18nPlaceholder]; if (v != null) e.placeholder = v;
    });
    box.querySelectorAll("[data-i18n-title]").forEach(function (e) {
      var v = D[e.dataset.i18nTitle]; if (v != null) e.title = v;
    });
    box.querySelectorAll("[data-i18n-aria]").forEach(function (e) {
      var v = D[e.dataset.i18nAria]; if (v != null) e.setAttribute("aria-label", v);
    });
    document.documentElement.lang = lang;
  }
  window.T = T;
  window.i18nPaint = paint;
  window.I18N_LANG = lang;
  window.I18N_HAS = function (key) { return D[key] != null; };

  /* ---------- переключатель ---------- */
  /* Выбор языка человеком: запоминаем и меняем язык (без перезагрузки при I18N_LIVE). */
  function pick(l) {
    l = norm(l);
    if (!l || l === lang) return;
    document.cookie = "lang=" + l + ";path=/;max-age=31536000;samesite=lax";
    try { localStorage.setItem(PICKED, l); } catch (e) {}
    var q = new URLSearchParams(location.search);
    q.set("lang", l);
    if (window.I18N_LIVE) { setLang(l, q); return; }
    location.search = q.toString();            // перезагрузка: подписи ставит и сервер, и страница
  }
  /* Свои кнопки страницы: <button data-lang-pick="uz">. Нажатие ловим здесь, отметку текущего языка ставим тоже здесь. */
  function markPicks() {
    document.querySelectorAll(".lang-switch button, [data-lang-pick]").forEach(function (b) {
      var l = b.getAttribute("data-lang-pick") || b.dataset.lang;
      b.setAttribute("aria-pressed", l === lang ? "true" : "false");
    });
  }
  document.addEventListener("click", function (e) {
    var b = e.target && e.target.closest ? e.target.closest("[data-lang-pick]") : null;
    if (b) pick(b.getAttribute("data-lang-pick"));
  });

  /* Плавающий переключатель внизу справа. Страница со своим переключателем ставит до подключения
     window.I18N_NO_FLOAT = true (мини-апп app/tg.html) — тогда плавающего нет. */
  function switcher() {
    if (window.I18N_NO_FLOAT) return;
    if (document.querySelector(".lang-switch")) return;
    var hasTheme = !!document.querySelector('script[src="/theme.js"]');
    var css = document.createElement("style");
    css.textContent =
      ".lang-switch{position:fixed;z-index:99;bottom:14px;right:" + (hasTheme ? "165px" : "14px") + ";" +
      "display:inline-flex;gap:2px;padding:3px;border-radius:999px;background:var(--card,#161C21);" +
      "border:1px solid var(--line,#26303A);box-shadow:0 6px 18px rgba(0,0,0,.18)}" +
      ".lang-switch button{font:700 12px/1 Manrope,system-ui,sans-serif;color:var(--muted,#8E9BA6);-webkit-text-fill-color:var(--muted,#8E9BA6);" +
      "background:transparent;border:0;border-radius:999px;padding:6px 9px;min-height:0;width:auto;cursor:pointer}" +
      ".lang-switch button[aria-pressed=\"true\"]{color:var(--ink,#E6ECF0);-webkit-text-fill-color:var(--ink,#E6ECF0);background:var(--soft,#1C242B)}" +
      /* место внизу страницы, чтобы последние строки можно было прокрутить выше переключателя */
      "body::after{content:\"\";display:block;height:calc(56px + env(safe-area-inset-bottom))}";
    document.head.appendChild(css);

    var box = document.createElement("div");
    box.className = "lang-switch";
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", T("common.lang", "Язык"));
    LANGS.forEach(function (l) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = SHORT[l];
      b.dataset.lang = l;
      b.title = TITLE[l];
      b.setAttribute("aria-pressed", l === lang ? "true" : "false");
      b.onclick = function () { pick(l); };
      box.appendChild(b);
    });
    document.body.appendChild(box);
  }

  /* Смена языка без перезагрузки (window.I18N_LIVE). Русский словарь тоже берём с сервера:
     подписи по data-i18n уже могли стать узбекскими, и вернуть их можно только по словарю. */
  var switching = 0;
  function loadDict(l) {
    var c = cacheGet(l);
    if (c) { fetchLater(l); return Promise.resolve(c); }
    return fetch("/i18n/" + l + ".json").then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { if (j) cachePut(l, j); return j; })
      .catch(function () { return null; });
  }
  function setLang(l, q) {
    var my = ++switching;
    loadDict(l).then(function (dict) {
      if (my !== switching) return;            // пока грузили, человек выбрал другой язык
      if (!dict && l !== "ru") return;         // словарь не пришёл — остаёмся на прежнем языке целиком
      D = dict || {};
      lang = l;
      window.I18N_LANG = l;
      try { history.replaceState(null, "", location.pathname + "?" + q.toString() + location.hash); } catch (e) {}
      paint();
      var box = document.querySelector(".lang-switch");
      if (box) box.setAttribute("aria-label", T("common.lang", "Язык"));
      markPicks();
      var ev;
      try { ev = new CustomEvent("i18n:changed", {detail: {lang: l}}); }
      catch (e) { ev = document.createEvent("CustomEvent"); ev.initCustomEvent("i18n:changed", false, false, {lang: l}); }
      window.dispatchEvent(ev);
    });
  }
  window.I18N_SET = function (l) { l = norm(l); if (l && l !== lang) setLang(l, new URLSearchParams(location.search)); };
  window.I18N_PICK = pick;

  function start() { paint(); switcher(); markPicks(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
