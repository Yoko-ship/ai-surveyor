/* app/tg/js/osgor.js — вкладка «ОСГОР» */
/* =====================================================================================
   ОСГОР: калькулятор премии (POST /osgor/quick), подсказка ОКЭД по мере ввода (GET /osgor/activities).
   ===================================================================================== */
const OS = {items: [], active: -1, open: false, timer: null, picked: null, res: null, err: null, brv: null, brvErr: "", seq: 0, q: null,
  body: null, calcSeq: 0};

function loadOsgor(){
  if (!$("#osDate").value) $("#osDate").value = new Date().toISOString().slice(0, 10);
  osRepaint();
  osLoadBrv();
}
function osRepaint(){
  osPaintList();
  osPaintPicked();
  osPaintResult();
  osPaintBrv();
  osRelang();
}
/* шаги и примечания приходят с сервера на языке запроса: язык сменили — один тихий пересчёт,
   старый результат остаётся на экране, пока не придёт новый (без мигания) */
async function osRelang(){
  if (!OS.res || !OS.body || OS.res.lang === I18N_LANG || OS.relang === I18N_LANG) return;
  const lang = OS.relang = I18N_LANG;
  const seq = ++OS.calcSeq;
  const r = await api("/osgor/quick", jsonOpts("POST", Object.assign({}, OS.body, {lang: lang})));
  if (OS.relang === lang) OS.relang = null;
  if (seq !== OS.calcSeq || !r.ok || lang !== I18N_LANG) return;
  OS.res = r.data;
  OS.body.lang = lang;
  osPaintResult();
}

/* ФОТ с разделителями прямо при вводе; курсор остаётся после той же цифры */
$("#osPay").addEventListener("input", () => { groupDigitsLive($("#osPay")); $("#osPayErr").textContent = ""; });

function osSearch(){
  clearTimeout(OS.timer);
  OS.timer = setTimeout(async () => {
    const q = $("#osQ").value.trim();
    const seq = ++OS.seq;
    const r = await api("/osgor/activities?limit=20&q=" + encodeURIComponent(q));
    if (seq !== OS.seq) return;                    // пришёл устаревший ответ — ждём свежий
    OS.q = q;
    OS.items = r.ok ? (r.data.items || []) : [];
    OS.err = r.ok ? null : r.error;
    OS.found = r.ok ? r.data.found : 0;
    OS.active = OS.items.length ? 0 : -1;
    OS.open = true;
    osPaintList();
  }, 200);
}
function osPaintList(){
  const ul = $("#osList"), inp = $("#osQ");
  inp.setAttribute("aria-expanded", OS.open ? "true" : "false");
  ul.classList.toggle("hidden", !OS.open);
  if (!OS.open) { inp.removeAttribute("aria-activedescendant"); return; }
  if (OS.err) { ul.innerHTML = '<li class="none">' + esc(OS.err) + "</li>"; return; }
  if (!OS.items.length) { ul.innerHTML = '<li class="none">' + esc(T("tg.os.nothing", "Ничего не нашлось. Попробуйте код ОКЭД или другое слово.")) + "</li>"; return; }
  ul.innerHTML = OS.items.map((x, i) =>
    '<li role="option" id="os-opt-' + i + '" data-i="' + i + '" aria-selected="' + (i === OS.active) + '">'
    + "<b>" + esc(x.okved) + "</b><span>" + esc(x.name) + "</span>"
    + "<em>" + esc(T("tg.os.kst_cat", "КСТ {k} · категория {c} из 20", {k: nf(x.kst, 3), c: x.category})) + "</em></li>").join("")
    + (OS.found > OS.items.length ? '<li class="none">' + esc(T("tg.os.more", "Показаны {n} из {total} — уточните запрос.", {n: OS.items.length, total: OS.found})) + "</li>" : "");
  if (OS.active >= 0) {
    inp.setAttribute("aria-activedescendant", "os-opt-" + OS.active);
    const a = $("#os-opt-" + OS.active); if (a && a.scrollIntoView) a.scrollIntoView({block: "nearest"});
  }
  ul.querySelectorAll("li[data-i]").forEach(li => {
    li.onmousedown = e => e.preventDefault();      // поле не теряет фокус до выбора
    li.onclick = () => osPick(Number(li.dataset.i));
  });
}
function osPick(i){
  const x = OS.items[i];
  if (!x) return;
  OS.picked = x;
  OS.open = false;
  haptic("select");
  $("#osQ").value = x.okved + " · " + x.name;
  $("#osQErr").textContent = "";
  osPaintList();
  osPaintPicked();
}
function osPaintPicked(){
  const x = OS.picked;
  $("#osPicked").innerHTML = x ? esc(T("tg.os.picked", "Выбрано: ОКЭД {code}, категория {c} из 20, КСТ {k}", {code: x.okved, c: x.category, k: nf(x.kst, 3)})) : "";
}
$("#osQ").addEventListener("input", () => { OS.picked = null; osPaintPicked(); osSearch(); });
$("#osQ").addEventListener("focus", () => { if (!OS.picked) osSearch(); });
$("#osQ").addEventListener("blur", () => { setTimeout(() => { OS.open = false; osPaintList(); }, 150); });
$("#osQ").addEventListener("keydown", e => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!OS.open) { osSearch(); return; }
    if (!OS.items.length) return;
    OS.active = (OS.active + (e.key === "ArrowDown" ? 1 : -1) + OS.items.length) % OS.items.length;
    osPaintList();
  } else if (e.key === "Enter") {
    if (OS.open && OS.active >= 0) { e.preventDefault(); osPick(OS.active); }
    else if (OS.picked) osCalc();
  } else if (e.key === "Escape") { OS.open = false; osPaintList(); }
});

async function osCalc(){
  $("#osQErr").textContent = ""; $("#osPayErr").textContent = "";
  const raw = $("#osQ").value.trim();
  const code = OS.picked ? OS.picked.okved : (/^[\d.]+$/.test(raw) ? raw : "");
  const pay = Number($("#osPay").value.replace(/\D/g, "")) || 0;
  const days = Number($("#osDays").value.replace(/\D/g, "")) || 365;
  let bad = false;
  if (!code) { $("#osQErr").textContent = T("tg.os.need_okved", "Выберите вид деятельности из списка или введите код ОКЭД цифрами."); bad = true; }
  if (pay <= 0) { $("#osPayErr").textContent = T("tg.os.need_payroll", "Укажите фонд оплаты труда за год — это страховая сумма."); bad = true; }
  if (days < 1 || days > 365) { $("#osMsg").innerHTML = errHtml(T("tg.os.days_bad", "Срок — от 1 до 365 дней.")); bad = true; }
  if (bad) { haptic("error"); return; }
  $("#osBtn").disabled = true;
  tgBusy(true);
  $("#osMsg").innerHTML = spin(T("common.calculating", "считаю…"));
  const body = {okved: code, payroll: pay, term_days: days, lang: I18N_LANG};
  if ($("#osDate").value) body.contract_date = $("#osDate").value;
  const seq = ++OS.calcSeq;
  const r = await api("/osgor/quick", jsonOpts("POST", body));
  tgBusy(false);
  $("#osBtn").disabled = false;
  if (seq !== OS.calcSeq) return;               // пришёл ответ на устаревший запрос
  OS.body = r.ok ? body : null;
  if (!r.ok) { OS.res = null; $("#osMsg").innerHTML = errHtml(T("tg.os.failed", "Премия не рассчитана: {reason}", {reason: r.error})); osPaintResult(); return; }
  $("#osMsg").innerHTML = "";
  OS.res = r.data;
  osPaintResult();
  if (!WIDE.matches) $("#osResult").scrollIntoView({block: "start"});
}
$("#osBtn").onclick = osCalc;

function osPaintResult(){
  const box = $("#osResult");
  const d = OS.res;
  if (!d) { box.innerHTML = ""; return; }
  const a = d.activity || {};
  const notes = (d.notes || []).slice();
  if (d.note && notes.indexOf(d.note) < 0) notes.unshift(d.note);
  box.innerHTML = '<div class="card"><p class="eyebrow">' + esc(T("tg.os.premium", "Премия ОСГОР")) + "</p>"
    + '<div class="big">' + esc(money(d.premium)) + "</div>"
    + '<p class="sub">' + esc(T("tg.os.premium_for", "за {n} дн. · страховая сумма {s}", {n: d.term_days, s: money(d.sum_insured)})) + "</p>"
    // вид деятельности — строкой; категория, КСТ, минимум и БРВ — одним блоком с сегментами
    + '<div class="mini"><div class="wide"><span>' + esc(T("tg.os.activity", "Вид деятельности")) + "</span><b>" + esc((a.okved || "") + " · " + (a.name || "—")) + "</b></div></div>"
    + '<div class="seg">'
    + "<div><span>" + esc(T("tg.os.category", "Категория риска")) + "</span><b>" + esc(a.category != null ? T("tg.os.cat_of", "{c} из 20", {c: a.category}) : "—") + "</b></div>"
    + "<div><span>" + esc(T("tg.os.kst", "КСТ")) + "</span><b>" + esc(d.kst != null ? nf(d.kst, 3) : "—") + "</b></div>"
    + (d.min_applied ? "<div><span>" + esc(T("tg.os.min", "Минимальная премия")) + "</span><b>" + esc(money(d.min_premium)) + "</b></div>" : "")
    + "<div><span>" + esc(T("tg.os.brv", "БРВ")) + "</span><b>" + esc(d.brv ? money(d.brv) : T("tg.os.brv_none", "не введён")) + "</b></div>"
    + "</div>"
    + '<div class="h3">' + esc(T("tg.os.lines", "Расчёт по шагам")) + '</div><div class="lines">'
    + (d.lines || []).map(l => '<div><div class="top"><span>' + esc(l.step) + "</span><b>" + esc(l.value) + "</b></div>"
      + (l.explain ? "<p>" + esc(l.explain) + "</p>" : "") + (l.legal_ref ? "<small>" + esc(l.legal_ref) + "</small>" : "") + "</div>").join("")
    + "</div>"
    + (notes.length ? notes.map(n => '<p class="note warn os-note">' + esc(n) + "</p>").join("") : "")
    + (d.brv_source ? '<p class="note">' + esc(T("tg.os.brv_source", "БРВ: {s}", {s: d.brv_source})) + "</p>" : "")
    + (d.legal_note_en ? '<p class="note">' + esc(d.legal_note_en) + "</p>" : "")
    + '<div class="srcbar"><span>' + esc(T("tg.os.legal", "Норма: ПКМ № 177, Правила ОСГОР, прил. 9")) + '</span><a href="https://lex.uz/" target="_blank" rel="noopener">'
    + esc(T("tg.an.read_in", "Читать в источнике {d}", {d: "lex.uz"})) + "</a></div>"
    + "</div>";
}

/* ---------- БРВ: значение для всех, форма — только админу ---------- */
async function osLoadBrv(){
  const r = await api("/osgor/brv?lang=" + encodeURIComponent(I18N_LANG));
  OS.brv = r.ok ? r.data : null;
  OS.brvErr = r.ok ? "" : r.error;
  osPaintBrv();
  if (TAB === "settings") admPaint();
}
function brvHtml(prefix){
  const b = OS.brv;
  if (!b) return '<div class="card"><h2>' + esc(T("tg.os.brv_title", "Базовая расчётная величина (БРВ)")) + '</h2><div class="note">'
    + (OS.brvErr ? errHtml(OS.brvErr) : spin(T("common.loading", "загружаю…"))) + "</div></div>";
  const cur = b.current;
  const edit = !VIEW_USER && (CAN("osgor_brv") || b.can_edit);
  return '<div class="card"><h2>' + esc(T("tg.os.brv_title", "Базовая расчётная величина (БРВ)")) + "</h2>"
    + '<p class="sub">' + esc(T("tg.os.brv_hint", "Нужна для минимальной премии (0,25 БРВ) и выплаты на погребение. Значение вносит администратор.")) + "</p>"
    + (cur
      ? '<div class="seg"><div class="main"><span>' + esc(T("tg.os.brv_now", "Действует сейчас")) + "</span><b>" + esc(money(cur.value)) + "</b></div>"
        + "<div><span>" + esc(T("tg.os.brv_from", "С даты")) + "</span><b>" + esc(dateOnly(cur.effective_from)) + "</b></div></div>"
        + '<p class="note brv-src">' + esc(T("common.source", "Источник")) + ": " + esc(cur.source || "—") + "</p>"
      : '<p class="note warn">' + esc(T("tg.os.brv_missing", "Размер БРВ ещё не введён — минимальная премия не проверяется.")) + "</p>")
    + (b.next ? '<p class="note">' + esc(T("tg.os.brv_next", "С {d} будет {v}", {d: dateOnly(b.next.effective_from), v: money(b.next.value)})) + "</p>" : "")
    + (edit
      ? '<div class="h3">' + esc(T("tg.os.brv_new", "Новое значение")) + "</div>"
        + '<div class="inline"><div><label class="f" for="' + prefix + 'BrvV">' + esc(T("tg.os.brv_value", "Размер, сум")) + "</label>"
        + '<input id="' + prefix + 'BrvV" type="text" inputmode="numeric"></div>'
        + '<div><label class="f" for="' + prefix + 'BrvD">' + esc(T("tg.os.brv_date", "Вступает в силу")) + "</label>"
        + '<input id="' + prefix + 'BrvD" type="date"></div></div>'
        + '<label class="f" for="' + prefix + 'BrvS">' + esc(T("tg.os.brv_src", "Источник — акт или ссылка")) + "</label>"
        + '<input id="' + prefix + 'BrvS" type="text" autocomplete="off" placeholder="' + esc(T("tg.os.brv_src_ph", "например Указ Президента № … или ссылка на lex.uz")) + '">'
        + '<div class="actions"><button type="button" class="btn btn-primary" data-brvsave="' + prefix + '">' + esc(T("tg.os.brv_save", "Сохранить БРВ")) + "</button></div>"
        + '<div class="note msg" id="' + prefix + 'BrvMsg"></div>'
      : "")
    + ((b.history || []).length
      ? '<details class="how"><summary>' + esc(T("tg.os.brv_history", "История ({n})", {n: b.history.length})) + '</summary><div class="chain">'
        + b.history.map(h => "<div><span>" + esc(dateOnly(h.effective_from) + " · " + (h.source || "")) + "</span><b>" + esc(money(h.value)) + "</b></div>").join("") + "</div></details>"
      : "")
    + "</div>";
}
function bindBrv(root){
  root.querySelectorAll("[data-brvsave]").forEach(b => { b.onclick = () => saveBrv(b.dataset.brvsave); });
  root.querySelectorAll("input[id$='BrvV']").forEach(el => el.addEventListener("input", () => groupDigitsLive(el)));
}
function osPaintBrv(){
  const box = $("#osBrvBox");
  box.innerHTML = brvHtml("os");
  bindBrv(box);
}
async function saveBrv(prefix){
  const msg = $("#" + prefix + "BrvMsg");
  const value = Number($("#" + prefix + "BrvV").value.replace(/\D/g, "")) || 0;
  const date = $("#" + prefix + "BrvD").value;
  const source = $("#" + prefix + "BrvS").value.trim();
  if (value <= 0) { msg.innerHTML = errHtml(T("tg.os.brv_bad_value", "Размер БРВ — положительное число в сумах.")); return; }
  if (!date) { msg.innerHTML = errHtml(T("tg.os.brv_bad_date", "Укажите дату вступления в силу.")); return; }
  if (source.length < 3) { msg.innerHTML = errHtml(T("tg.os.brv_bad_src", "Укажите источник: акт, которым установлен размер, или ссылку на него.")); return; }
  msg.innerHTML = spin(T("common.saving", "сохраняю…"));
  const r = await api("/osgor/brv", jsonOpts("PUT", {value: value, effective_from: date, source: source}));
  if (!r.ok) { msg.innerHTML = errHtml(admErr(r)); return; }
  await osLoadBrv();
  const m2 = $("#" + prefix + "BrvMsg");
  if (m2) m2.innerHTML = okHtml(T("tg.os.brv_saved", "БРВ сохранена. Расчёты по договорам с этой даты возьмут новое значение."));
}

