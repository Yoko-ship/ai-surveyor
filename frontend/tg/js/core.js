/* app/tg/js/core.js — утилиты, форматирование чисел и дат, Telegram WebApp, api(), состояние входа (ME) */
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
/* Только отображаемая проза ИИ: JSON, цитаты и исходные пользовательские данные не меняем. */
function aiPlainText(value){
  return String(value == null ? "" : value).replace(/\r\n?/g, "\n")
    .replace(/\uE200cite\uE202[^\uE201]*\uE201/g, "")
    .replace(/^[ \t]*(`{3,}|~{3,})[^\n]*$/gm, "")
    .replace(/^[ \t]{0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?$/gm, "$1")
    .replace(/^[ \t]*(?:[-*_][ \t]*){3,}$/gm, "")
    .replace(/^[ \t]*\|?[ :|-]+\|[ :|-]*$/gm, "")
    .replace(/^[ \t]*[*+-][ \t]+/gm, "• ")
    .replace(/^[ \t]*>[ \t]?/gm, "")
    .replace(/!?\[([^\]\n]+)\]\(([^\s)]+)\)/g, (m, label, url) => /^https?:\/\//i.test(url) ? label + " — " + url : label)
    .replace(/\*\*(?=\S)([^\n]*?\S)\*\*/g, "$1")
    .replace(/\*(?=\S)([^*\n]*?\S)\*/g, "$1")
    .replace(/(^|\s)__([^\n]+?)__(?=\s|[.,:;!?]|$)/g, "$1$2")
    .replace(/(^|\s)_([^_\n]+)_(?=\s|[.,:;!?]|$)/g, "$1$2")
    .replace(/`+([^`\n]+)`+/g, "$1")
    .replace(/^[ \t]*\|(.+)\|[ \t]*$/gm, (m, cells) => cells.split("|").map(s => s.trim()).join(" · "))
    .replace(/\n{3,}/g, "\n\n").trim();
}
function aiText(value){
  const text = aiPlainText(value);
  const urls = /https?:\/\/[^\s<>"']+/gi;
  let out = "", at = 0, match;
  while ((match = urls.exec(text))) {
    const url = match[0].replace(/[.,;:!?)}\]]+$/, "");
    out += esc(text.slice(at, match.index));
    try {
      const parsed = new URL(url);
      if (parsed.username || parsed.password) throw new Error("credentials");
      out += '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(url) + '</a>';
    } catch (e) { out += esc(url); }
    at = match.index + url.length;
  }
  return out + esc(text.slice(at));
}
/* Подписи: T("ключ", "русский запас") из /i18n.js. Словарь не подключился — остаётся русский. */
if (!window.T) window.T = (k, f, v) => { let s = f != null ? f : k; if (v) for (const x in v) s = String(s).split("{" + x + "}").join(v[x]); return s; };
if (!window.I18N_LANG) window.I18N_LANG = "ru";
/* числа и даты — по языку интерфейса */
const LOCALES = {ru: "ru-RU", uz: "uz-UZ", en: "en-US"};
const LOC = () => LOCALES[window.I18N_LANG] || "ru-RU";
const nf = (n, d) => Number(n).toLocaleString(LOC(), {minimumFractionDigits: d, maximumFractionDigits: d});
const fmt = n => (n == null || n === "" || isNaN(Number(n))) ? "—" : Math.round(Number(n)).toLocaleString(LOC());
const pct = (n, d) => (n == null || isNaN(Number(n))) ? "—" : nf(n, d == null ? 3 : d) + "%";
const num = s => Number(String(s == null ? "" : s).replace(/[^\d.-]/g, "")) || 0;
/* десятичное число из поля: пробелы убираем, запятую считаем точкой */
const dec = s => { const t = String(s == null ? "" : s).replace(/[\s  ]/g, "").replace(",", "."); return t === "" ? null : Number(t); };
/* поле ввода десятичного числа: только цифры и один разделитель дробной части; возвращает очищенный текст */
function decimalLive(el){
  const c = el.value.replace(/[^\d.,]/g, "").replace(/^([^.,]*[.,])(.*)$/, (m, a, b) => a + b.replace(/[.,]/g, ""));
  if (c !== el.value) el.value = c;
  return c;
}
/* поле суммы: цифры группируются по разрядам на лету, курсор остаётся после той же цифры */
function groupDigitsLive(el){
  const pos = el.selectionStart || 0;
  const before = el.value.slice(0, pos).replace(/\D/g, "").length;
  const d = el.value.replace(/\D/g, "").replace(/^0+(?=\d)/, "");
  el.value = d ? Number(d).toLocaleString(LOC()) : "";
  let i = 0, seen = 0;
  while (i < el.value.length && seen < before) { if (/\d/.test(el.value[i])) seen++; i++; }
  try { el.setSelectionRange(i, i); } catch (e) {}
}
const SUM = () => T("common.sum", "сум");
const money = n => n == null ? "—" : fmt(n) + " " + SUM();
/* крупные суммы коротко: 50,0 млрд сум */
function compact(n){
  if (n == null || isNaN(Number(n))) return "—";
  const a = Math.abs(Number(n));
  if (a >= 1e12) return nf(n / 1e12, 1) + " " + T("tg.u.trln", "трлн");
  if (a >= 1e9) return nf(n / 1e9, 1) + " " + T("tg.u.bln", "млрд");
  if (a >= 1e6) return nf(n / 1e6, 1) + " " + T("tg.u.mln", "млн");
  return fmt(n);
}
const when = s => {
  if (!s) return "";
  const d = new Date(String(s).replace(" ", "T"));
  return isNaN(d) ? String(s) : d.toLocaleString(LOC(), {day: "2-digit", month: "2-digit", year: "2-digit",
    hour: "2-digit", minute: "2-digit"});
};
const dateOnly = s => {
  if (!s) return "";
  const d = new Date(String(s).slice(0, 10) + "T00:00:00");
  return isNaN(d) ? String(s) : d.toLocaleDateString(LOC(), {day: "2-digit", month: "2-digit", year: "numeric"});
};
const spin = t => '<span class="spin"></span> ' + esc(t);
const errHtml = t => '<span class="err">' + esc(t) + "</span>";
const okHtml = t => '<span class="ok">' + esc(t) + "</span>";
function anLoadingCard(){ return '<p class="note">' + spin(T("common.loading", "загружаю…")) + "</p>"; }
const TG = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

/* ---------- Clear Desk / Field Kit: одна структура, две темы ---------- */
function uiIcon(name){
  const paths = {
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    chat: '<path d="M8 5l2-2h4l2 2h4v15H4V5z"/><circle cx="12" cy="12" r="4"/>',
    calc: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 11h2m4 0h2m-8 4h2m4 0h2m-8 3h2m4 0h2"/>',
    legal: '<path d="M4 4h6l2 2 2-2h6v15h-6l-2 2-2-2H4zM12 6v15"/>',
    osgor: '<path d="M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6"/>',
    users: '<circle cx="9" cy="7" r="3"/><path d="M3 21v-3a6 6 0 0112 0v3M17 4a3 3 0 010 6m1 5a5 5 0 013 4v2"/>',
    settings: '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12l1 1M5 19l1-1M18 6l1-1"/>',
    moon: '<path d="M20 14a8 8 0 01-10-10 8 8 0 1010 10z"/>'
  };
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (paths[name] || paths.menu) + '</svg>';
}
function hexColor(s){
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(s || "").trim());
  if (!m) return null;
  const h = m[1].length === 3 ? m[1].replace(/./g, c => c + c) : m[1];
  return "#" + h.toLowerCase();
}
function luminance(hex){
  const v = [1, 3, 5].map(i => parseInt(hex.substr(i, 2), 16) / 255)
    .map(c => c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
}
const THEME_URL = new URLSearchParams(location.search).get("theme");
let themePreference = THEME_URL;
if (!(themePreference === "light" || themePreference === "dark")) {
  try { themePreference = localStorage.getItem("surveyor-theme"); } catch (e) { themePreference = null; }
}
function paintThemeToggle(){
  const b = $("#themeToggle");
  if (!b) return;
  const dark = document.documentElement.dataset.theme === "dark";
  b.innerHTML = uiIcon(dark ? "sun" : "moon");
  b.setAttribute("aria-label", dark ? T("tg.design.theme_light", "Включить светлую тему") : T("tg.design.theme_dark", "Включить тёмную тему"));
  b.title = b.getAttribute("aria-label");
  b.setAttribute("data-i18n-aria", dark ? "tg.design.theme_light" : "tg.design.theme_dark");
}
function tgThemeColors(){
  const css = getComputedStyle(document.documentElement);
  return {color: hexColor(css.getPropertyValue("--action")), text_color: hexColor(css.getPropertyValue("--on-accent"))};
}
function applyTheme(){
  const root = document.documentElement;
  if (themePreference === "light" || themePreference === "dark") root.dataset.theme = themePreference;
  else {
    const p = TG && TG.themeParams || {};
    const base = hexColor(p.bg_color) || hexColor(p.section_bg_color) || hexColor(p.secondary_bg_color);
    const dark = base ? luminance(base) < 0.4 : !!(TG && TG.initData && TG.colorScheme === "dark");
    root.dataset.theme = dark ? "dark" : "light";
  }
  paintThemeToggle();
  if (TG && TG.initData) {
    const paper = hexColor(getComputedStyle(root).getPropertyValue("--paper"));
    if (paper) {
      tgCallEarly(() => TG.setHeaderColor(paper));
      tgCallEarly(() => TG.setBackgroundColor(paper));
    }
    if (TG.MainButton && TG.MainButton.setParams) tgCallEarly(() => TG.MainButton.setParams(tgThemeColors()));
  }
}
function tgCallEarly(f){ try { f(); } catch (e) { /* старый клиент: сохраняем кнопки страницы */ } }
applyTheme();
const themeToggle = $("#themeToggle");
if (themeToggle) themeToggle.onclick = () => {
  themePreference = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  try { localStorage.setItem("surveyor-theme", themePreference); } catch (e) {}
  if (THEME_URL) {
    const url = new URL(location.href); url.searchParams.set("theme", themePreference);
    history.replaceState(null, "", url);
  }
  applyTheme();
};
if (TG) {
  try { TG.ready(); TG.expand(); TG.onEvent("themeChanged", applyTheme); }
  catch (e) { /* старый клиент */ }
}
const IN_TG = !!(TG && TG.initData);

function why(status, j){
  const d = j && (j.detail || j.error);
  const ru = typeof d === "string" && /[А-Яа-я]/.test(d) ? d : null;
  if (ru) return ru;
  if (status === 401) return T("tg.err.401", "нужно войти заново");
  if (status === 403) return T("tg.err.403", "у вашей роли нет такого права");
  if (status === 404 || status === 405) return T("tg.err.404", "сервер ещё не умеет этот запрос");
  if (status === 422) return T("tg.err.422", "сервер не принял данные формы");
  if (status >= 500) return T("tg.err.500", "ошибка на сервере, смотрите лог sandbox/server.log");
  return T("tg.err.other", "сервер ответил {status}", {status: status});
}

async function api(url, opts){
  try{
    opts = opts || {};
    if (TOKEN) opts.headers = Object.assign({}, opts.headers || {}, {Authorization: "Bearer " + TOKEN});
    // Сервер сам проверяет подпись Telegram и список тестировщиков подписки.
    if (IN_TG && new URL(url, location.href).origin === location.origin)
      opts.headers = Object.assign({}, opts.headers || {}, {"X-Telegram-Init-Data": TG.initData});
    const r = await fetch(url, opts);
    let j = null;
    try { j = await r.json(); } catch (e) { j = null; }
    if (!r.ok) return {ok: false, status: r.status, error: why(r.status, j), data: j};
    return {ok: true, data: j};
  } catch (e) {
    return {ok: false, error: T("tg.err.offline", "сервер не отвечает")};
  }
}
const jsonOpts = (method, body) => ({method: method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});

/* ---------- кто вошёл ---------- */
let ME = {mode: "", status: "", user: null, rights: [], nav: [], can_edit: []};
/* Токен сессии: встроенный браузер Telegram (особенно iOS) может не сохранять cookie,
   поэтому после входа токен хранится здесь и уходит заголовком Authorization. */
let TOKEN = null;
try { TOKEN = sessionStorage.getItem("surveyor_token") || null; } catch (e) {}
function setToken(t){ TOKEN = t || null; try { if (t) sessionStorage.setItem("surveyor_token", t); else sessionStorage.removeItem("surveyor_token"); } catch (e) {} }
let BOT = null, EAIS = null, STATUS = null;
let SCREEN = "app";
/* Режим пользователя (задача 170): администратор смотрит приложение глазами обычного сотрудника.
   Включается переключателем в левой панели или адресом /tg?mode=user (кнопка «Выйти из админки» в /admin/hub),
   помнится в sessionStorage до закрытия вкладки. Права не меняются — сервер проверяет их сам; прячем только
   админские разделы, формы правки справочников и кнопки управления людьми. */
const VIEW_KEY = "surveyor_view";
let VIEW_USER = false;
(function(){
  const q = new URLSearchParams(location.search);
  if (q.get("mode") === "user") {
    try { sessionStorage.setItem(VIEW_KEY, "user"); } catch (e) {}
    q.delete("mode");
    const rest = q.toString();
    try { history.replaceState(null, "", location.pathname + (rest ? "?" + rest : "") + location.hash); } catch (e) {}
    VIEW_USER = true;
  }
  try { VIEW_USER = VIEW_USER || sessionStorage.getItem(VIEW_KEY) === "user"; } catch (e) {}
})();
/* админ на самом деле. При /tg/me?view=user сервер отдаёт role «сотрудник» и is_admin=false,
   а настоящую роль — в admin_available и real_role (app/tgbot.py, tg_me): по ним и показываем «Вернуться». */
const REAL_ADMIN = () => !!(ME.user && (ME.admin_available === true || ME.real_role === "админ"
  || ME.user.is_admin || ME.user.role === "админ"));
const IS_ADMIN = () => REAL_ADMIN() && !VIEW_USER;
const CAN = key => !VIEW_USER && (ME.can_edit || []).indexOf(key) >= 0;

function banner(sel, kind, text){
  const el = $(sel);
  el.className = "banner " + (kind === "ok" ? "ok" : kind === "stop" ? "stop" : "");
  el.textContent = text;
}
function screen(name){
  SCREEN = name;                        // экран остался один: приложение. Входа и регистрации на странице нет
  $("#screen-app").classList.toggle("hidden", name !== "app");
  $("#nav").classList.toggle("hidden", name !== "app" || !$("#nav").querySelector("button"));
  if (typeof syncTgButtons === "function") syncTgButtons();
}
