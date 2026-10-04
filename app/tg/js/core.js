/* app/tg/js/core.js — утилиты, форматирование чисел и дат, Telegram WebApp, api(), состояние входа (ME) */
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
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

/* ---------- Telegram: готовность, разворот, светлая или тёмная тема ---------- */
/* У мини-аппа своя палитра (заказчик 28.09.2026): лавандовый фон, белые карточки, фиолетовый акцент.
   Telegram решает только, светлая она или тёмная, — по яркости своего фона (bg_color, section_bg_color)
   или по colorScheme. Цвета Telegram на страницу не накладываются: ни на фон и текст, ни на кнопки
   (у кнопок свои явные цвета — ui-kit выше), иначе разделы выглядели бы по-разному у разных тем клиента.
   Шапку и фон самого клиента красим в наш цвет фона — так мини-апп не отделяется от рамки Telegram. */
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
const THEME_URL = new URLSearchParams(location.search).get("theme");   // ?theme=dark — проверка тёмной темы
/* светлая — по умолчанию, в том числе в обычном браузере */
document.documentElement.dataset.theme = THEME_URL === "dark" ? "dark" : "light";
function applyTheme(){
  if (!TG) return;
  const root = document.documentElement;
  if (!(THEME_URL === "light" || THEME_URL === "dark")) {
    const p = TG.themeParams || {};
    const base = hexColor(p.bg_color) || hexColor(p.section_bg_color) || hexColor(p.secondary_bg_color);
    const dark = base ? luminance(base) < 0.4 : !!(TG.initData && TG.colorScheme === "dark");
    root.dataset.theme = dark ? "dark" : "light";
  }
  const paper = hexColor(getComputedStyle(root).getPropertyValue("--paper"));
  if (paper && TG.initData) {
    tgCallEarly(() => TG.setHeaderColor(paper));
    tgCallEarly(() => TG.setBackgroundColor(paper));
  }
}
function tgCallEarly(f){ try { f(); } catch (e) { /* старая версия клиента: цвета рамки не меняются */ } }
if (TG) {
  try {
    TG.ready();
    TG.expand();
    applyTheme();
    TG.onEvent("themeChanged", applyTheme);
  } catch (e) { /* старая версия клиента — работаем на наших цветах */ }
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

