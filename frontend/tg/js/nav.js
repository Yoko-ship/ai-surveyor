/* app/tg/js/nav.js — навигация: меню слева, вкладки, кнопки Telegram, язык, вход, фон раздела, способы входа */
/* ---------- «Открыть в Telegram»: только в обычном браузере ---------- */
const BOT_FALLBACK = "inson_surveyor_bot";
function botName(){
  const pick = o => o && (o.bot_username || o.username || o.bot || "");
  return String(pick(STATUS) || pick(BOT) || BOT_FALLBACK).replace(/^@/, "");
}
function paintTgOpen(){
  $("#tgOpenBar").classList.toggle("hidden", IN_TG);
  if (!IN_TG) $("#tgOpenLink").href = "https://t.me/" + encodeURIComponent(botName()) + "?start=app";
}

/* ---------- меню слева: рейка и раскрытая панель ---------- */
const WIDE = window.matchMedia("(min-width:900px)");
function sideOpen(on){
  document.body.classList.toggle("side-open", !!on);
  $("#burger").setAttribute("aria-expanded", on ? "true" : "false");
  const menu = $("#mobileMenu");
  if (menu) menu.setAttribute("aria-expanded", on ? "true" : "false");
  const side = $("#side");
  side.inert = !on && !WIDE.matches;
  if (menu && !WIDE.matches) {
    const more = $("#mobileNav [data-more]");
    if (more) more.setAttribute("aria-expanded", on ? "true" : "false");
  }
}
function sideSync(){
  document.body.classList.toggle("side-wide", WIDE.matches);
  if (WIDE.matches) sideOpen(true); else sideOpen(false);
}
if (WIDE.addEventListener) WIDE.addEventListener("change", sideSync); else WIDE.addListener(sideSync);
sideSync();
$("#burger").onclick = () => { sideOpen(false); const b = $("#mobileMenu"); if (b) b.focus(); };
if ($("#mobileMenu")) $("#mobileMenu").onclick = () => { sideOpen(true); $("#burger").focus(); };
$("#scrim").onclick = () => { if (!WIDE.matches) sideOpen(false); };
document.addEventListener("keydown", e => {
  if (WIDE.matches || !document.body.classList.contains("side-open")) return;
  if (e.key === "Escape") { sideOpen(false); $("#mobileMenu").focus(); }
  if (e.key === "Tab") {
    const items = Array.from($("#side").querySelectorAll('button, a[href], input, select, [tabindex="0"]')).filter(el => !el.disabled && el.getClientRects().length);
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});

/* ---------- разделы меню: рисуются по ответу сервера ---------- */
/* «Аналитику» и «Фото» сервер отдаёт по отдельности — в интерфейсе это один раздел «ИИ-сюрвейер».
   «Калькулятор» остаётся сам по себе: быстрый расчёт премии без диалога. */
const NAV_MERGE = {analytics: "chat", photos: "chat"};
function navMerge(items){
  const out = [];
  (items || []).forEach(it => {
    const key = NAV_MERGE[it.key] || it.key;
    if (!out.some(x => x.key === key)) out.push(key === it.key ? it : {key: key, title: ""});
  });
  return out;
}
/* полные и короткие (для узкой рейки) подписи — по языку интерфейса */
function navFull(key, title){
  const full = {chat: () => T("tg.nav.chat", "ИИ-сюрвейер"), calc: () => T("tg.nav.calc", "Калькулятор"),
    osgor: () => T("tg.nav.osgor", "ОСГОР"), legal: () => T("tg.nav.legal", "Специалист"),
    users: () => T("tg.nav.users", "Пользователи"),
    settings: () => T("tg.nav.settings", "Настройки")}[key];
  return full ? full() : (title || key);
}
function navRail(key, title){
  const rail = {chat: () => T("tg.rail.chat", "Сюрвейер"), calc: () => T("tg.rail.calc", "Расчёт"),
    osgor: () => T("tg.rail.osgor", "ОСГОР"), legal: () => T("tg.rail.legal", "Спец."),
    users: () => T("tg.rail.users", "Люди"),
    settings: () => T("tg.rail.settings", "Настр.")}[key];
  return rail ? rail() : navFull(key, title);
}
const LOADERS = {chat: loadChat, calc: loadCalc, osgor: loadOsgor, legal: loadLegal, users: loadUsers, settings: loadSettings};
/* раздел из адреса: /tg?tab=calc открывает сразу «Калькулятор» */
let START_TAB = (new URLSearchParams(location.search).get("tab") || "").replace(/[^a-z]/g, "");
let TAB = null;
const BADGES = {};

function sectionKeys(){
  return Array.prototype.slice.call(document.querySelectorAll("[data-section]")).map(s => s.dataset.section);
}

/* repaint=true — только перерисовать подписи (смена языка), раздел не перезагружать */
function paintNav(items, repaint){
  const nav = $("#nav");
  const list = navMerge(items).filter(it => document.querySelector('[data-section="' + it.key + '"]'));
  // «Юрист» — у всех вошедших: раздел только читает законы, прав на него не нужно
  if (ME.user && !list.some(it => it.key === "legal")) {
    const at = list.findIndex(it => it.key === "osgor");
    list.splice(at < 0 ? list.length : at + 1, 0, {key: "legal", title: ""});
  }
  // «Настройки» с профилем — у всех: сервер отдаёт этот пункт только админу, остальным добавляем сами
  if (ME.user && !list.some(it => it.key === "settings")) list.push({key: "settings", title: ""});
  nav.classList.toggle("hidden", !list.length);
  if (!list.length) { nav.innerHTML = ""; paintMobileNav([]); return; }
  nav.innerHTML = list.map(it => {
    const full = navFull(it.key, it.title);
    return '<button type="button" data-tab="' + esc(it.key) + '" title="' + esc(full) + '">'
      + "<i aria-hidden=\"true\">" + uiIcon(it.key) + "</i>"
      + "<u>" + esc(navRail(it.key, it.title)) + "</u>"
      + "<span>" + esc(full) + "</span><cite></cite></button>";
  }).join("");
  nav.querySelectorAll("[data-tab]").forEach(b => { b.onclick = () => openTab(b.dataset.tab); });
  /* «Админка» — переход в общий каркас админки (/admin/hub) в этом же окне: домен тот же,
     вход по cookie сессии, ничего в адрес не подставляем. Признак админа — раздел «Настройки»,
     его сервер отдаёт в меню только админу (app/tgbot.py, NAV_ADMIN). */
  if (IS_ADMIN()) {
    const hub = T("tg.nav.admin", "Админка");
    nav.insertAdjacentHTML("beforeend",
      '<button type="button" id="navAdmin" title="' + esc(hub) + '">'
      + '<i aria-hidden="true">▦</i><u>' + esc(hub) + "</u><span>" + esc(hub) + "</span></button>");
    $("#navAdmin").onclick = () => { location.href = "/admin/hub"; };
  }
  paintMobileNav(list);
  Object.keys(BADGES).forEach(k => navBadge(k, BADGES[k]));
  if (repaint && TAB) { markTab(TAB); return; }
  /* ?tab=osgor — прямая ссылка на раздел: открываем его, если такой раздел человеку доступен */
  const want = START_TAB && list.some(it => it.key === START_TAB) ? START_TAB : null;
  START_TAB = "";
  openTab(want || (list.some(it => it.key === TAB) ? TAB : list[0].key));
}

function paintMobileNav(items){
  const nav = $("#mobileNav");
  if (!nav) return;
  nav.classList.toggle("hidden", !items.length);
  if (!items.length) { nav.innerHTML = ""; return; }
  nav.innerHTML = items.filter(it => ["chat", "calc", "legal"].includes(it.key)).map(it =>
    '<button type="button" data-tab="' + esc(it.key) + '">' + uiIcon(it.key) + '<span>'
    + esc(it.key === "chat" ? T("tg.design.survey", "Осмотр") : navFull(it.key)) + '</span></button>'
  ).join("") + '<button type="button" data-more aria-controls="side" aria-expanded="false">' + uiIcon("menu") + '<span>' + esc(T("tg.design.more", "Ещё")) + '</span></button>';
  nav.querySelectorAll("[data-tab]").forEach(b => { b.onclick = () => openTab(b.dataset.tab); });
  nav.querySelector("[data-more]").onclick = () => { sideOpen(true); $("#burger").focus(); };
}
function markTab(key){
  document.querySelectorAll("#nav [data-tab], #mobileNav [data-tab]").forEach(b => {
    const on = b.dataset.tab === key;
    b.classList.toggle("on", on);
    b.setAttribute("aria-current", on ? "page" : "false");
  });
}

function openTab(key){
  TAB = key;
  sectionKeys().forEach(k => {
    document.querySelector('[data-section="' + k + '"]').classList.toggle("hidden", k !== key);
  });
  markTab(key);
  if (!WIDE.matches) sideOpen(false);          // на телефоне панель закрывается, видно раздел
  window.scrollTo(0, 0);
  if (typeof appBg === "function") appBg(key); // фон раздела: размытая картинка под содержимым
  paintTop();
  if (LOADERS[key]) LOADERS[key]();
  syncTgButtons();
}

function navBadge(key, n){
  BADGES[key] = n;
  const btn = $("#nav").querySelector('[data-tab="' + key + '"]');
  if (!btn) return;
  const base = navFull(key);
  btn.querySelector("span").innerHTML = esc(base) + (n ? ' <em>' + n + "</em>" : "");
  btn.querySelector("cite").textContent = n ? String(n) : "";   // счётчик виден и на узкой рейке
  btn.title = base + (n ? " (" + n + ")" : "");
}

/* ---------- нативные кнопки Telegram: MainButton, BackButton, SettingsButton, HapticFeedback ---------- */
/* В Telegram основное действие раздела или шага мастера — нижняя кнопка клиента (MainButton), «Назад» в мастере —
   BackButton в шапке, «Настройки» (профиль) — SettingsButton в меню мини-аппа. Дубли на странице прячутся
   классами tg-main / tg-back (body.in-tg). Вне Telegram всё работает кнопками страницы, как раньше.
   Обработчик у каждой нативной кнопки один; что он делает, решает syncTgButtons() по разделу и шагу. */
const TGB = {main: null, back: null, busy: false};
if (IN_TG) document.body.classList.add("in-tg");
function tgCall(f){ try { f(); } catch (e) { /* старая версия клиента — кнопки страницы остаются */ } }
function haptic(kind){
  if (!TG || !TG.HapticFeedback) return;
  tgCall(() => { if (kind === "select") TG.HapticFeedback.selectionChanged(); else TG.HapticFeedback.notificationOccurred(kind); });
}
function syncTgButtons(){
  if (!IN_TG) return;
  let text = null, fn = null, back = null, off = false;
  if (SCREEN === "app") {
    if (TAB === "calc") { text = T("tg.calc_button", "Посчитать"); fn = calcRun; off = CALC.busy; }
    else if (TAB === "osgor") { text = T("tg.os.calc", "Рассчитать"); fn = osCalc; }
    else if (TAB === "legal") { text = T("tg.lg.send", "Спросить"); fn = lgAsk; off = LG.busy; }
    else if (TAB === "chat") {
      // мастер: нижняя кнопка — действие шага, «Назад» в шапке — на шаг раньше без потери введённого
      text = wzMainLabel(); fn = wzMain; off = CH.busy || !!CH.sending;
      back = CH.wz > 1 ? wzBack : null;
    }
  }
  TGB.main = fn; TGB.back = back;
  if (TG.MainButton) tgCall(() => {
    if (!text) { TG.MainButton.hide(); return; }
    TG.MainButton.setText(text);
    // Нативная кнопка совпадает с выбранной темой интерфейса.
    if (TG.MainButton.setParams) TG.MainButton.setParams(tgThemeColors());
    if (off && !TGB.busy) TG.MainButton.disable(); else TG.MainButton.enable();
    TG.MainButton.show();
  });
  if (TG.BackButton) tgCall(() => { if (back) TG.BackButton.show(); else TG.BackButton.hide(); });
}
/* прогресс на нижней кнопке, пока идёт запрос */
function tgBusy(on){
  TGB.busy = !!on;
  if (IN_TG && TG.MainButton) tgCall(() => { if (on) TG.MainButton.showProgress(false); else TG.MainButton.hideProgress(); });
}
if (IN_TG) {
  if (TG.MainButton) tgCall(() => TG.MainButton.onClick(() => { if (TGB.main && !TGB.busy) TGB.main(); }));
  if (TG.BackButton) tgCall(() => TG.BackButton.onClick(() => { if (TGB.back) TGB.back(); }));
  if (TG.SettingsButton) tgCall(() => {
    TG.SettingsButton.onClick(() => { if (SCREEN === "app") openTab("settings"); });
    TG.SettingsButton.show();
  });
}

/* ---------- смена языка: мгновенная перерисовка всего, что видно ---------- */
/* app/i18n.js меняет словарь без перезагрузки, ставит тексты по data-i18n и шлёт событие i18n:changed.
   Здесь перерисовываются подписи, которые рисует JS: меню, шапка, текущий экран и открытый раздел. */
const REPAINT = {chat: chatRelang, calc: calcRepaint, osgor: osRepaint, legal: lgRepaint,
  users: usersRepaint, settings: settingsRepaint};
function repaintCurrent(){
  document.title = T("tg.title", "Сюрвейер INSON");
  paintStatic();
  paintTgOpen();
  paintMode();
  if (SCREEN === "app") {
    paintWho();
    paintNav(ME.nav || [], true);
    if (TAB && REPAINT[TAB]) REPAINT[TAB]();
  }
  paintLinks();
  syncTgButtons();
}
window.addEventListener("i18n:changed", repaintCurrent);

/* подписи, которые JS держит сам (у них нет data-i18n: текст меняется по ходу работы) */
function paintStatic(){
  paintTop();
  paintThemeToggle();
}
/* строка заголовка: название раздела; у мастера — название шага, как на снимке «Фото и документы» */
function paintTop(){
  const t = $("#topTitle");
  if (!t) return;
  let s = "";
  if (TAB === "chat") {
    s = [T("tg.wz.top1", "Фото и документы"), T("tg.wz.top2", "Проверка данных"), T("tg.act.top3", "Сюрвейерский акт")][(CH.wz || 1) - 1];
  } else if (TAB) s = navFull(TAB);
  t.textContent = s || T("tg.title", "Сюрвейер INSON");
}
function paintMode(){
  const m = ME.mode ? modeName(ME.mode) : "";
  $("#mode").textContent = !ME.status && !m ? T("tg.connecting", "подключаюсь…")
    : (m || T("tg.mode_browser", "браузер")) + (ME.status ? " · " + statusName(ME.status) : "");
}
function modeName(m){
  if (m === "guest") return "";            // «гость» уже сказано статусом — дважды не пишем
  if (m === "telegram") return "Telegram";
  if (m === "браузер" || m === "browser") return T("tg.mode_browser", "браузер");
  return m;
}
/* статусы учётной записи приходят с сервера по-русски — показываем по словарю */
function statusName(s){
  switch (s) {
    case "активен": return T("status.user.active", "активен");
    case "заблокирован": return T("status.user.blocked", "закрыт");
    case "ожидает подтверждения": return T("status.user.pending", "ожидает подтверждения");
    case "нужна верификация": return T("status.user.needs_verification", "нужна верификация");
    case "гость": return T("tg.guest_status", "гость, вход не нужен");
    case "не вошёл": return T("tg.status_logged_out", "не вошёл");
    default: return String(s || "—");
  }
}
function roleName(r){
  switch (r) {
    case "агент": return T("tg.role.agent", "агент");
    case "андеррайтер": return T("tg.role.underwriter", "андеррайтер");
    case "актуарий": return T("tg.role.actuary", "актуарий");
    case "админ": return T("tg.role.admin", "администратор");
    case "сотрудник": return T("tg.role.employee", "сотрудник");
    default: return String(r || "—");
  }
}
function paintWho(){
  const u = ME.user;
  if (!u) {
    // гость: имени нет и менять нечего — вместо ссылки входа кнопка «Запросить доступ в админку»
    $("#whoText").textContent = T("tg.guest", "Гость");
    $("#outBtn").classList.add("hidden");
    $("#viewSwitch").classList.add("hidden");
    nameFormOpen(false);
    return;
  }
  $("#outBtn").classList.remove("hidden");
  const admin = REAL_ADMIN();
  $("#whoText").innerHTML = '<button type="button" class="who-name" id="whoNameBtn" title="' + esc(T("tg.profile.rename", "Изменить имя")) + '">'
    + esc(u.name || u.full_name || "—") + "</button>"
    + (admin && VIEW_USER
      ? '<span class="viewpill">' + esc(T("tg.view.pill", "Режим пользователя")) + ' ·<button type="button" id="viewBack">' + esc(T("tg.view.back", "Вернуться")) + "</button></span>"
      : admin ? '<span class="adm-badge">' + esc(T("tg.admin_badge", "Админ")) + "</span>" : "")
    + " · " + esc(roleName(u.role)) + (u.branch ? " · " + esc(u.branch) : "");
  $("#whoNameBtn").onclick = () => nameFormOpen(true);
  if ($("#viewBack")) $("#viewBack").onclick = () => setView(false);
  $("#viewSwitch").classList.toggle("hidden", !admin);
  $("#viewToggle").checked = VIEW_USER;
}

/* ---------- режим пользователя: переключение ---------- */
async function setView(on){
  VIEW_USER = !!on;
  try { if (on) sessionStorage.setItem(VIEW_KEY, "user"); else sessionStorage.removeItem(VIEW_KEY); } catch (e) {}
  USERS = null;
  await loadMe();
}
$("#viewToggle").onchange = () => setView($("#viewToggle").checked);

/* ---------- смена имени: левая панель и «Настройки → Профиль» ---------- */
function nameFormOpen(on){
  $("#whoNameForm").classList.toggle("hidden", !on);
  $("#whoText").classList.toggle("hidden", !!on);
  $("#whoNameMsg").innerHTML = "";
  if (on) { $("#whoName").value = (ME.user && (ME.user.name || ME.user.full_name)) || ""; $("#whoName").focus(); }
}
async function saveName(raw, msgSel){
  const msg = $(msgSel);
  const name = String(raw || "").replace(/\s+/g, " ").trim();
  if (name.length < 2) { msg.innerHTML = errHtml(T("tg.reg.name_need", "Напишите имя — как к вам обращаться, не короче 2 символов.")); return false; }
  if (name.length > 80) { msg.innerHTML = errHtml(T("tg.profile.too_long", "Имя длиннее 80 символов — сократите его.")); return false; }
  msg.innerHTML = spin(T("common.saving", "сохраняю…"));
  const r = await api("/tg/me/name", jsonOpts("PUT", {name: name}));
  if (!r.ok) { msg.innerHTML = errHtml(T("tg.profile.not_saved", "Имя не сохранено: {reason}", {reason: r.error})); return false; }
  const got = (r.data && r.data.name) || name;
  ME.user.name = ME.user.full_name = got;
  if (USERS && USERS.items) USERS.items.forEach(x => { if (x.id === ME.user.id) x.full_name = got; });
  paintWho();
  paintProfile();
  if (TAB === "users") usersRepaint();
  msg.innerHTML = okHtml(T("tg.profile.saved", "Имя сохранено."));
  return true;
}
$("#whoNameSave").onclick = async () => { if (await saveName($("#whoName").value, "#whoNameMsg")) setTimeout(() => nameFormOpen(false), 900); };
$("#whoNameCancel").onclick = () => nameFormOpen(false);
$("#whoName").addEventListener("keydown", e => { if (e.key === "Enter") $("#whoNameSave").click(); if (e.key === "Escape") nameFormOpen(false); });
$("#pfSave").onclick = () => saveName($("#pfName").value, "#pfMsg");
$("#pfName").addEventListener("keydown", e => { if (e.key === "Enter") $("#pfSave").click(); });
function paintProfile(){
  const u = ME.user;
  if (!u) return;
  if (document.activeElement !== $("#pfName")) $("#pfName").value = u.name || u.full_name || "";
  $("#pfInfo").innerHTML = "<div><span>" + esc(T("tg.profile.role", "Роль")) + "</span><b>" + esc(roleName(u.role)) + "</b></div>"
    + "<div><span>" + esc(T("tg.branch", "Филиал")) + "</span><b>" + esc(u.branch || "—") + "</b></div>"
    + "<div><span>" + esc(T("tg.mode", "Режим")) + "</span><b>" + esc(modeName(ME.mode) || T("tg.mode_browser", "браузер")) + "</b></div>"
    + "<div><span>" + esc(T("tg.profile.view", "Вид")) + "</span><b>" + esc(REAL_ADMIN() && !VIEW_USER ? T("tg.view.admin", "администратор") : T("tg.view.user", "пользователь")) + "</b></div>";
}

/* ---------- кто открыл приложение ---------- */
/* Регистрации нет (решение заказчика 22.09.2026): приложение открыто всем. Сервер сам решает,
   гость перед ним или админ, — по ответу /tg/me. Вход нужен только для админки. */
async function boot(){
  paintStatic();
  paintMode();
  paintTgOpen();
  const st = await api("/tg/status");
  if (st.ok) {
    STATUS = st.data || {};
    ME.mode = STATUS.mode || "";
    paintMode();
    paintTgOpen();
    if (STATUS.pd_mode === "prod") $("#pdWarn").classList.add("hidden");
  }
  if (IN_TG) {
    // внутри Telegram сессию админа поднимает сервер по username; любой другой ответ — просто гость
    const a = await api("/tg/auth", jsonOpts("POST", {initData: TG.initData}));
    if (a.ok && a.data && a.data.token) setToken(a.data.token);
  }
  await loadMe();
}

const GUEST_NAV = [{key: "chat", title: ""}, {key: "calc", title: ""}, {key: "osgor", title: ""}, {key: "legal", title: ""}];
const IS_GUEST = () => !ME.user;

async function loadMe(){
  const r = await api("/tg/me" + (VIEW_USER ? "?view=user" : ""));
  if (!r.ok) {
    // сервер не ответил — приложение всё равно открываем: разделы гостя работают без сессии
    $("#mode").textContent = T("tg.server_silent", "сервер не ответил");
    banner("#authWarn", "stop", T("tg.who_unknown", "Сервер не сказал, кто вы: {reason}. Обновите страницу.", {reason: r.error}));
    $("#authWarn").classList.remove("hidden");
    ME = Object.assign({}, ME, {user: null, nav: GUEST_NAV, can_edit: [], rights: []});
  } else {
    $("#authWarn").classList.add("hidden");
    ME = Object.assign({can_edit: []}, r.data || ME);
  }
  paintMode();
  $("#whoami").classList.remove("hidden");
  paintWho();
  if (typeof loadLinks === "function") loadLinks();
  if (typeof loadAdminReq === "function") loadAdminReq();
  screen("app");
  paintNav((ME.nav && ME.nav.length ? ME.nav : GUEST_NAV));
  if (ME.user) {
    api("/auth/me").then(m => { if (m.ok) EAIS = m.data.agent_eais_id || null; });
    api("/tg/bot-status").then(b => { if (b.ok) { BOT = b.data; paintTgOpen(); } });
  }
}

$("#outBtn").onclick = async () => {
  await api("/auth/logout", {method: "POST"});
  setToken(null);
  actReset();                     // акт и фото принадлежали вошедшему — после выхода начинаем с чистого листа
  try { sessionStorage.removeItem(VIEW_KEY); } catch (e) {}
  VIEW_USER = false;
  USERS = null;
  await loadMe();                 // вышли — остаёмся в приложении гостем
};


/* =====================================================================================
   Кинематографичные штрихи внутри приложения.

   Фон раздела (#appbg): размытая и затемнённая картинка под содержимым, лёгкий параллакс.
   При системной настройке «меньше движения» и при включённой экономии трафика движение
   и картинки выключаются (классы fx-still / fx-lite).
   Обложку убрали по решению заказчика 28.09.2026: приложение сразу открывается на разделе.
   ===================================================================================== */
/* бережный режим: системная настройка «меньше движения» и экономия трафика у оператора */
const MOTION_OFF = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)");
function cineFlags(){
  const c = navigator.connection || {};
  const slow = !!c.saveData || /^(slow-2g|2g)$/.test(c.effectiveType || "");
  document.body.classList.toggle("fx-lite", slow);
  document.body.classList.toggle("fx-still", slow || !!(MOTION_OFF && MOTION_OFF.matches));
}
document.body.classList.add("cine");
cineFlags();
if (MOTION_OFF && MOTION_OFF.addEventListener) MOTION_OFF.addEventListener("change", cineFlags);

/* ---------- фон раздела внутри приложения ---------- */
const APP_BG = {chat: "chat", calc: "calc", osgor: "osgor", legal: "specialist",
  users: "documents", settings: "admin"};
let APP_BG_NOW = "";
function appBg(key){
  if (window.INSON_MINI_APP) return; // Спокойный фон Clear Desk / Field Kit без декоративных изображений.
  const scene = APP_BG[key] || "particles";
  if (scene === APP_BG_NOW || document.body.classList.contains("fx-lite")) return;
  APP_BG_NOW = scene;
  const box = $("#appbg");
  const img = new Image();
  img.alt = "";
  img.decoding = "async";
  img.srcset = "/static/bg/" + scene + "-640.jpg 640w, /static/bg/" + scene + ".jpg 1024w";
  img.sizes = "100vw";
  img.src = "/static/bg/" + scene + "-640.jpg";
  img.onload = () => { img.classList.add("on"); };
  img.onerror = () => { img.remove(); };         // картинки нет — остаётся обычный фон темы
  box.innerHTML = "";
  box.appendChild(img);
}
/* лёгкий параллакс фона приложения при прокрутке страницы */
let appTick = false;
window.addEventListener("scroll", () => {
  if (appTick || document.body.classList.contains("fx-still")) return;
  appTick = true;
  requestAnimationFrame(() => {
    appTick = false;
    const img = $("#appbg img");
    if (img) img.style.setProperty("--par", Math.round(window.scrollY * -0.06) + "px");
  });
}, {passive: true});

/* ---------- Способы входа (левая панель, у каждого вошедшего) ---------- */
/* Данные: GET /auth/links → {links:[{provider, title, display, external_id_masked, …}],
   can_link, can_unlink, google:{configured, message, hint}, profile}.
   Привязать Google — POST /auth/link/google/start → {url}; Google возвращает на /tg?link=ok
   или /tg?link=error&reason=… — показываем словами.
   Привязать Telegram — POST /auth/link/telegram/start → код боту, дальше опрос
   GET /auth/tg-link/status?link_id=… (тот же порядок, что на экране входа по коду).
   «Отвязать» показываем, только если сервер разрешил (can_unlink): последний способ входа
   отнимать нельзя — человек потеряет вход. */
let LINKS = null;
const LN = {id: "", timer: null, until: 0, note: null};

function linkRow(sel, text, off){
  const b = $(sel);
  b.textContent = text;
  b.classList.toggle("off", !!off);
}
const linkOf = p => ((LINKS && LINKS.links) || []).filter(x => x.provider === p)[0] || null;
const canLink = p => ((LINKS && LINKS.can_link) || []).indexOf(p) >= 0;
const canUnlink = p => ((LINKS && LINKS.can_unlink) || []).indexOf(p) >= 0;

function paintLinks(){
  const show = !!ME.user;
  $("#links").classList.toggle("hidden", !show);
  paintAskAdmin();
  if (!show) return;
  const none = T("tg.links.none", "не привязан");
  const tg = linkOf("telegram"), go = linkOf("google");
  linkRow("#lnTg", tg ? (tg.display || tg.external_id_masked || "") : none, !tg);
  linkRow("#lnGo", go ? (go.display || go.external_id_masked || "") : none, !go);
  $("#lnTgOff").textContent = $("#lnGoOff").textContent = T("tg.links.unbind", "Отвязать");
  $("#lnTgOff").classList.toggle("hidden", !canUnlink("telegram"));
  $("#lnGoOff").classList.toggle("hidden", !canUnlink("google"));
  const g = (LINKS && LINKS.google) || {};
  const gOff = canLink("google") && g.configured === false;
  $("#lnBind").textContent = T("tg.links.bind_google", "Привязать Google");
  $("#lnBind").classList.toggle("hidden", !canLink("google"));
  $("#lnBind").disabled = gOff;
  $("#lnBindTg").textContent = T("tg.links.bind_tg", "Привязать Telegram");
  $("#lnBindTg").classList.toggle("hidden", !canLink("telegram"));
  // «Google не настроен» — не ошибка человека, а состояние сервера: говорим спокойно, без красного
  $("#lnMsg").innerHTML = LN.note ? LN.note()
    : gOff ? esc([g.message, g.hint].filter(Boolean).join(" ")) : "";
}
function lnCodeHide(){
  ["#lnCode", "#lnHow", "#lnOpenBot"].forEach(s => $(s).classList.add("hidden"));
}
async function loadLinks(){
  if (!ME.user) { LINKS = null; paintLinks(); return; }
  const r = await api("/auth/links");
  LINKS = r.ok ? (r.data || {}) : null;
  if (!r.ok) LN.note = () => errHtml(T("tg.links.load_fail", "Способы входа не загрузились: {reason}", {reason: r.error}));
  paintLinks();
}

/* вернулись от Google: /tg?link=ok или /tg?link=error&reason=… — говорим словами и чистим адрес */
(function(){
  const q = new URLSearchParams(location.search), v = q.get("link");
  if (!v) return;
  const reason = q.get("reason") || "";
  LN.note = v === "ok"
    ? () => okHtml(T("tg.links.google_done", "Google привязан."))
    : () => errHtml(T("tg.links.google_err", "Google не привязан: {reason}", {reason: reason}));
  q.delete("link");
  q.delete("reason");
  const rest = q.toString();
  try { history.replaceState(null, "", location.pathname + (rest ? "?" + rest : "") + location.hash); } catch (e) {}
})();

$("#lnBind").onclick = async () => {
  const btn = $("#lnBind");
  btn.disabled = true;
  LN.note = () => spin(T("tg.links.google_open", "открываю вход Google…"));
  paintLinks();
  const r = await api("/auth/link/google/start", jsonOpts("POST", {}));
  if (r.ok && r.data && r.data.url) { location.href = r.data.url; return; }
  btn.disabled = false;
  if (r.error) {
    LN.note = () => errHtml(T("tg.links.google_fail", "Google не открылся: {reason}", {reason: r.error}));
    paintLinks();
    return;
  }
  location.href = "/auth/google?link=1";    // запасной путь: обычный вход через Google с признаком привязки
};

/* --- привязка Telegram: код боту и опрос, как на экране входа --- */
function lnStop(){ if (LN.timer) { clearInterval(LN.timer); LN.timer = null; } }
function lnLeft(){
  const s = Math.max(0, Math.round((LN.until - Date.now()) / 1000));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}
function lnWait(){
  LN.note = () => okHtml(T("tg.links.wait", "Жду сообщение боту — код действует {left}", {left: lnLeft()}));
  paintLinks();
}
$("#lnBindTg").onclick = async () => {
  const btn = $("#lnBindTg");
  btn.disabled = true;
  LN.note = () => spin(T("tg.links.code_wait", "готовлю код…"));
  paintLinks();
  const r = await api("/auth/link/telegram/start", jsonOpts("POST", {}));
  btn.disabled = false;
  if (!r.ok) {
    LN.note = () => errHtml(T("tg.links.code_fail", "Код не выдан: {reason}", {reason: r.error}));
    paintLinks();
    return;
  }
  const d = r.data || {};
  LN.id = d.link_id || "";
  LN.until = Date.now() + (d.expires_in || 600) * 1000;
  $("#lnCode").textContent = d.code || "";
  $("#lnHow").textContent = T("tg.links.how", "Отправьте этот код боту {bot} — и Telegram станет вторым входом.",
                              {bot: d.bot || botName()});
  $("#lnOpenBot").href = d.link || ("https://t.me/" + encodeURIComponent(botName()));
  ["#lnCode", "#lnHow", "#lnOpenBot"].forEach(s => $(s).classList.remove("hidden"));
  lnStop();
  LN.timer = setInterval(lnPoll, (d.poll_seconds || 2) * 1000);
  lnWait();
};
async function lnPoll(){
  if (!LN.id) { lnStop(); return; }
  const r = await api("/auth/tg-link/status?link_id=" + encodeURIComponent(LN.id));
  if (!r.ok) return;                        // сеть моргнула — ждём следующую попытку
  const d = r.data || {};
  if (d.status === "ожидание") { lnWait(); return; }
  lnStop();
  LN.id = "";
  lnCodeHide();
  if (d.status === "привязано") {
    LN.note = () => okHtml(T("tg.links.tg_linked", "Telegram привязан: {who}", {who: d.display || ""}));
    await loadLinks();
    return;
  }
  LN.note = () => errHtml(T("tg.links.link_fail", "Привязка не удалась: {reason}", {reason: d.reason || ""}));
  paintLinks();
}

async function lnUnbind(p){
  LN.note = () => spin(T("tg.links.unbinding", "отвязываю…"));
  paintLinks();
  const r = await api("/auth/link/" + p, {method: "DELETE"});
  if (!r.ok) {
    LN.note = () => errHtml(T("tg.links.unbind_fail", "Не отвязали: {reason}", {reason: r.error}));
    paintLinks();
    return;
  }
  LN.note = () => okHtml(T("tg.links.unbind_done", "Способ входа отвязан. Вход остался по второму способу."));
  await loadLinks();
}
$("#lnTgOff").onclick = () => lnUnbind("telegram");
$("#lnGoOff").onclick = () => lnUnbind("google");

/* ---------- Доступ в админку: запрос владельцу ---------- */
/* Админом никто не становится сам. Гость внутри Telegram: POST /auth/admin-request {initData} —
   сервер проверяет подпись, заводит профиль «сотрудник» и ставит запрос владельцу, токен сессии
   запоминаем через setToken. Гость в браузере: уходим на /login — там вход Telegram или Google
   одним кликом, а после входа эта же кнопка ждёт на месте.
   Свой статус — GET /auth/admin-request: ожидает / подтверждён / отклонён / нет. */
let ADMREQ = null, ASK_NOTE = null;

function paintAskAdmin(){
  const show = !(ME.user && REAL_ADMIN());  // у администратора просить нечего
  $("#askAdmBox").classList.toggle("hidden", !show);
  if (!show) return;
  const st = (ADMREQ && ADMREQ.status) || "";
  const wait = st === "ожидает";
  $("#askAdm").textContent = T("tg.adminreq.ask", "Запросить доступ в админку");
  $("#askAdm").classList.toggle("hidden", wait);
  $("#askMsg").innerHTML = ASK_NOTE ? ASK_NOTE()
    : wait ? okHtml(T("tg.adminreq.wait", "Запрос отправлен владельцу — ждите подтверждения в Telegram."))
    : st === "отклонён" ? errHtml(T("tg.adminreq.no", "Владелец доступ не открыл. Напишите ему, если это ошибка."))
    : "";
}
async function loadAdminReq(){
  if (!ME.user) { ADMREQ = null; paintAskAdmin(); return; }
  const r = await api("/auth/admin-request");
  ADMREQ = r.ok ? (r.data || null) : null;
  paintAskAdmin();
}
$("#askAdm").onclick = async () => {
  const btn = $("#askAdm");
  if (!ME.user && !IN_TG) {                 // в браузере сначала вход: /login, оттуда вернёмся сюда
    ASK_NOTE = () => spin(T("tg.adminreq.to_login", "открываю вход…"));
    paintAskAdmin();
    location.href = (ME.login_url || "/login?next=/tg");
    return;
  }
  btn.disabled = true;
  ASK_NOTE = () => spin(T("tg.adminreq.sending", "отправляю запрос…"));
  paintAskAdmin();
  const r = await api("/auth/admin-request", jsonOpts("POST", ME.user ? {} : {initData: (TG && TG.initData) || ""}));
  btn.disabled = false;
  if (!r.ok) {
    ASK_NOTE = () => errHtml(T("tg.adminreq.fail", "Запрос не отправлен: {reason}", {reason: r.error}));
    paintAskAdmin();
    return;
  }
  const d = r.data || {};
  if (d.token) setToken(d.token);           // гость мини-аппа: профиль только что заведён
  ADMREQ = {status: d.status || "", request_id: d.request_id};
  ASK_NOTE = d.already_admin
    ? () => okHtml(T("tg.adminreq.already", "Доступ в админку уже открыт."))
    : () => okHtml(T("tg.adminreq.sent", "Запрос отправлен владельцу — ждите подтверждения в Telegram."));
  if (d.token) await loadMe(); else paintAskAdmin();
};

