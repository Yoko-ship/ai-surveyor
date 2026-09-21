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
     * рисует переключатель RU / UZ / EN рядом с переключателем темы.

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
      .then(function (j) { if (j) { cachePut(l, j); D = j; paint(); } })
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
    document.documentElement.lang = lang;
  }
  window.T = T;
  window.i18nPaint = paint;
  window.I18N_LANG = lang;
  window.I18N_HAS = function (key) { return D[key] != null; };

  /* ---------- переключатель ---------- */
  function switcher() {
    if (document.querySelector(".lang-switch")) return;
    var hasTheme = !!document.querySelector('script[src="/theme.js"]');
    var css = document.createElement("style");
    css.textContent =
      ".lang-switch{position:fixed;z-index:99;bottom:14px;right:" + (hasTheme ? "165px" : "14px") + ";" +
      "display:inline-flex;gap:2px;padding:3px;border-radius:999px;background:var(--card,#161C21);" +
      "border:1px solid var(--line,#26303A);box-shadow:0 6px 18px rgba(0,0,0,.18)}" +
      ".lang-switch button{font:700 12px/1 Manrope,system-ui,sans-serif;color:var(--muted,#8E9BA6);-webkit-text-fill-color:var(--muted,#8E9BA6);" +
      "background:transparent;border:0;border-radius:999px;padding:6px 9px;min-height:0;width:auto;cursor:pointer}" +
      ".lang-switch button[aria-pressed=\"true\"]{color:var(--ink,#E6ECF0);-webkit-text-fill-color:var(--ink,#E6ECF0);background:var(--soft,#1C242B)}";
    document.head.appendChild(css);

    var box = document.createElement("div");
    box.className = "lang-switch";
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", T("common.lang", "Язык"));
    LANGS.forEach(function (l) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = SHORT[l];
      b.title = TITLE[l];
      b.setAttribute("aria-pressed", l === lang ? "true" : "false");
      b.onclick = function () {
        if (l === lang) return;
        document.cookie = "lang=" + l + ";path=/;max-age=31536000;samesite=lax";
        try { localStorage.setItem(PICKED, l); } catch (e) {}
        var q = new URLSearchParams(location.search);
        q.set("lang", l);
        location.search = q.toString();        // перезагрузка: подписи ставит и сервер, и страница
      };
      box.appendChild(b);
    });
    document.body.appendChild(box);
  }

  function start() { paint(); switcher(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
