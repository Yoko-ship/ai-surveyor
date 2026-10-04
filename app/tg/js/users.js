/* app/tg/js/users.js — вкладка «Пользователи» и запуск приложения (boot) */
/* ---------- пользователи (список видят все вошедшие) ---------- */
let CAN_MANAGE = false, USERS = null;
const USER_MSG = {};

async function loadUsers(){
  $("#usrList").innerHTML = '<div class="card"><div class="note">' + spin(T("common.loading", "загружаю…")) + "</div></div>";
  const r = await api("/tg/users");
  USERS = r.ok ? r.data : {err: r.error};
  usersRepaint();
}
function usersRepaint(){
  if (!USERS) return;
  admReqsPaint();                 // блок «Доступ в админку» — только у владельца
  if (USERS.err) { $("#usrList").innerHTML = '<div class="card"><div class="note err">' + esc(T("tg.list_failed", "Список не загрузился: {reason}", {reason: USERS.err})) + "</div></div>"; return; }
  CAN_MANAGE = !!USERS.can_manage && !VIEW_USER;
  const items = USERS.items || [];
  $("#usrList").innerHTML = '<p class="count"><b>' + esc(T("tg.users_total", "Всего людей: {n}", {n: USERS.count != null ? USERS.count : items.length})) + "</b>"
    + " · " + esc(CAN_MANAGE
      ? T("tg.users_admin_note", "Вы администратор: можете выдать и снять права администратора. Заблокированные остаются в базе — так сохраняется история их решений.")
      : T("tg.users_note", "Права администратора выдаёт и снимает администратор системы.")) + "</p>"
    + items.map(userCard).join("");
  bindUsers();
}

/* способ входа из ответа /tg/users; старый сервер поля не отдаёт — тогда прочерк */
function loginMethod(m){
  if (m === "telegram") return T("tg.login_method.telegram", "Telegram");
  if (m === "google") return T("tg.login_method.google", "Google");
  if (m === "both") return T("tg.login_method.both", "Telegram и Google");
  if (m === "service") return T("tg.login_method.service", "Служебный");
  return "—";
}

function userCard(u){
  const me = ME.user && ME.user.id === u.id;
  const m = USER_MSG[u.id];
  return '<div class="card" id="usr-' + u.id + '"><h2>' + esc(u.full_name || u.login)
    + (u.is_admin ? '<span class="tag">' + esc(T("tg.admin_tag", "админ")) + "</span>" : "")
    + (u.status !== "активен" ? '<span class="tag off">' + esc(statusName(u.status)) + "</span>" : "") + "</h2>"
    // департамент и должность — одной строкой под именем; контакты — мини-полями по два в ряд
    + '<p class="sub">' + esc([u.department || T("tg.dept_unset", "департамент не указан"), u.position].filter(Boolean).join(" · "))
    + (me ? " · " + esc(T("tg.its_you", "это вы")) : "") + (u.telegram ? " · " + esc(T("tg.telegram_linked", "Telegram привязан")) : "") + "</p>"
    + '<div class="mini m4">'
    + "<div><span>" + esc(T("tg.reg.phone", "Телефон")) + "</span><b>" + esc(u.phone || "—") + "</b></div>"
    + "<div><span>" + esc(T("tg.user_email", "Почта")) + "</span><b>" + esc(u.email || "—") + "</b></div>"
    + "<div><span>" + esc(T("tg.user_login_method", "Вход")) + "</span><b>" + esc(loginMethod(u.login_method)) + "</b></div>"
    + "<div><span>" + esc(T("tg.branch", "Филиал")) + "</span><b>" + esc(u.branch || "—") + "</b></div>"
    + "</div>"
    + (CAN_MANAGE && !me
      ? '<div class="actions">'
        + (u.is_admin
          ? '<button type="button" class="btn btn-secondary" data-revoke="' + u.id + '">' + esc(T("tg.revoke_admin", "Снять админа")) + "</button>"
          : '<button type="button" class="btn btn-primary" data-make="' + u.id + '">' + esc(T("tg.make_admin", "Сделать админом")) + "</button>")
        + (u.status === "активен" ? '<button type="button" class="btn btn-danger" data-block="' + u.id + '">' + esc(T("tg.block", "Заблокировать")) + "</button>" : "")
        + "</div>" : "")
    + '<div class="note msg" id="um-' + u.id + '">' + (m ? okHtml(m()) : "") + "</div></div>";
}

function bindUsers(){
  document.querySelectorAll("[data-make]").forEach(b => { b.onclick = () => setAdmin(b.dataset.make, true); });
  document.querySelectorAll("[data-revoke]").forEach(b => { b.onclick = () => setAdmin(b.dataset.revoke, false); });
  document.querySelectorAll("[data-block]").forEach(b => { b.onclick = () => blockUser(b.dataset.block); });
}

/* карточка перерисовывается на месте: список не дёргается и не прокручивается заново */
async function setAdmin(uid, make){
  const msg = $("#um-" + uid);
  msg.innerHTML = spin(make ? T("tg.granting", "выдаю права…") : T("tg.revoking", "снимаю права…"));
  const r = await api("/tg/users/" + uid + "/" + (make ? "make-admin" : "revoke-admin"), {method: "POST"});
  if (!r.ok) { msg.innerHTML = errHtml(r.error); return; }
  const got = await api("/tg/users");
  if (!got.ok) { msg.innerHTML = okHtml(T("tg.done_refresh", "Готово. Обновите список.")); return; }
  USERS = got.data;
  USER_MSG[uid] = make ? () => T("tg.admin_granted", "Права администратора выданы.") : () => T("tg.admin_revoked", "Права администратора сняты.");
  const u = (got.data.items || []).filter(x => x.id === Number(uid))[0];
  const card = $("#usr-" + uid);
  if (u && card) {
    card.outerHTML = userCard(u);
    bindUsers();
  }
}

async function blockUser(uid){
  const msg = $("#um-" + uid);
  msg.innerHTML = spin(T("tg.blocking", "закрываю доступ…"));
  const r = await api("/auth/block/" + uid + "?reason=" + encodeURIComponent(BLOCK_REASON), {method: "POST"});
  if (!r.ok) { msg.innerHTML = errHtml(T("tg.not_done", "Не получилось: {reason}", {reason: r.error})); return; }
  USER_MSG[uid] = () => T("tg.blocked_done", "Доступ закрыт, сессии завершены.");
  msg.innerHTML = okHtml(USER_MSG[uid]());
  (USERS.items || []).forEach(x => { if (x.id === Number(uid)) x.status = "заблокирован"; });
  const b = document.querySelector('[data-block="' + uid + '"]');
  if (b) b.remove();
}
const BLOCK_REASON = "решение администратора в мини-аппе";   // пишется в журнал сервера, на экран не выводится
$("#usrReload").onclick = loadUsers;

/* ---------- запросы доступа в «Пользователях»: их видит только владелец ---------- */
const REQ_MSG = {};
function admReqsPaint(){
  const box = $("#admReqs");
  if (!(USERS && USERS.is_owner) || VIEW_USER) { box.innerHTML = ""; return; }
  const items = USERS.admin_requests || [];
  box.innerHTML = '<div class="card"><h2>' + esc(T("tg.adminreq.title", "Доступ в админку")) + "</h2>"
    + '<p class="sub">' + esc(T("tg.adminreq.lead", "Эти люди просят открыть им админку. Те же кнопки приходят вам в Telegram.")) + "</p>"
    + (items.length ? "" : '<div class="note">' + esc(T("tg.adminreq.none", "Новых запросов нет.")) + "</div>")
    + "</div>" + items.map(admReqCard).join("");
  bindAdmReqs();
}
function wayName(w){
  const title = w.provider === "google" ? T("tg.links.google", "Google") : T("tg.links.tg", "Telegram");
  return title + " — " + (w.display || "");
}
function admReqCard(r){
  const m = REQ_MSG[r.id];
  return '<div class="card" id="areq-' + r.id + '"><h2>' + esc(r.name || "") + "</h2>"
    + '<p class="ways">' + esc((r.ways || []).map(wayName).join(" · ")) + "</p>"
    + '<p class="sub">' + esc(when(r.created_at) || "") + "</p>"
    + '<div class="actions">'
    + '<button type="button" class="btn btn-primary" data-areq-ok="' + r.id + '">' + esc(T("tg.adminreq.approve", "Подтвердить")) + "</button>"
    + '<button type="button" class="btn btn-secondary" data-areq-no="' + r.id + '">' + esc(T("tg.adminreq.reject", "Отклонить")) + "</button>"
    + "</div>"
    + '<div class="note msg" id="areqm-' + r.id + '">' + (m ? m() : "") + "</div></div>";
}
function bindAdmReqs(){
  document.querySelectorAll("[data-areq-ok]").forEach(b => { b.onclick = () => admReqDecide(b.dataset.areqOk, true); });
  document.querySelectorAll("[data-areq-no]").forEach(b => { b.onclick = () => admReqDecide(b.dataset.areqNo, false); });
}
async function admReqDecide(rid, yes){
  const msg = $("#areqm-" + rid);
  msg.innerHTML = spin(T("tg.adminreq.deciding", "решаю…"));
  const r = await api("/auth/admin-request/" + rid + "/" + (yes ? "approve" : "reject"), {method: "POST"});
  if (!r.ok) { msg.innerHTML = errHtml(T("tg.adminreq.decide_fail", "Не получилось: {reason}", {reason: r.error})); return; }
  REQ_MSG[rid] = yes
    ? () => okHtml(T("tg.adminreq.approved", "Доступ в админку открыт."))
    : () => okHtml(T("tg.adminreq.rejected", "Запрос отклонён."));
  await loadUsers();
}

/* запуск — последней строкой собранной страницы */
boot();
