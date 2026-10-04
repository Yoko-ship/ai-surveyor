/* app/tg/js/settings.js — вкладка «Настройки»: справочники администратора, состояние бота */
/* ---------- уровни риска для админки порогов (Настройки → пороги) ---------- */
function lvName(i, fallback){
  switch (i) {
    case 0: return T("tg.an.lv1", "Низкий");
    case 1: return T("tg.an.lv2", "Умеренный");
    case 2: return T("tg.an.lv3", "Повышенный");
    case 3: return T("tg.an.lv4", "Высокий");
    case 4: return T("tg.an.lv5", "Критический");
    default: return fallback || "—";
  }
}
/* =====================================================================================
   Данные и справочники — только администратору (can_edit из /tg/me). Сервер права проверяет сам.
   ===================================================================================== */
const ADM = {tab: null, th: null, coef: null, coefQ: "", minr: null, minQ: "", norms: null, err: {}};
function admErr(r){
  if (r.status === 403) return T("tg.adm.forbidden", "Сервер не дал сохранить: у вас нет прав администратора или сессия устарела. Войдите заново.");
  if (r.status === 401) return T("tg.err.401", "нужно войти заново");
  return T("tg.adm.not_saved", "Не сохранено: {reason}", {reason: r.error});
}
function admTabs(){
  const all = [["risk_thresholds", () => T("tg.adm.tab.thresholds", "Пороги уровня риска")],
    ["osgor_brv", () => T("tg.adm.tab.brv", "БРВ")],
    ["coefficients", () => T("tg.adm.tab.coef", "Коэффициенты")],
    ["min_rates", () => T("tg.adm.tab.min", "Минимальные ставки")],
    ["depreciation_norms", () => T("tg.adm.tab.norms", "Нормы износа")]];
  return all.filter(x => CAN(x[0]));
}
function loadSettings(){
  paintProfile();
  $("#setAdmin").classList.toggle("hidden", !IS_ADMIN());
  if (IS_ADMIN()) loadBot();
  admPaint();
}
function settingsRepaint(){
  paintProfile();
  $("#setAdmin").classList.toggle("hidden", !IS_ADMIN());
  if (IS_ADMIN()) paintBot();
  admPaint();
}
const admLoadErr = r => '<div class="note">' + errHtml(r.error) + "</div>";
function admPaint(){
  const box = $("#admData");
  const tabs = admTabs();
  if (!tabs.length) { box.innerHTML = ""; return; }
  if (!ADM.tab || !tabs.some(t => t[0] === ADM.tab)) ADM.tab = tabs[0][0];
  box.innerHTML = '<div class="card"><h2>' + esc(T("tg.adm.title", "Данные и справочники")) + '<span class="adm-badge">' + esc(T("tg.admin_badge", "Админ")) + "</span></h2>"
    + '<p class="sub">' + esc(T("tg.adm.lead", "Что вы измените здесь, сразу используют расчёт и аналитика. Каждая правка пишется в журнал; старые значения остаются в истории.")) + "</p>"
    + '<div class="chips" role="group" aria-label="' + esc(T("tg.adm.title", "Данные и справочники")) + '">'
    + tabs.map(t => '<button type="button" class="chip" data-adm="' + t[0] + '" aria-pressed="' + (ADM.tab === t[0]) + '">' + esc(t[1]()) + "</button>").join("")
    + '</div><div id="admBody"></div></div>';
  box.querySelectorAll("[data-adm]").forEach(b => { b.onclick = () => { ADM.tab = b.dataset.adm; admPaint(); }; });
  admBody();
}
function admBody(){
  const body = $("#admBody");
  if (!body) return;
  const t = ADM.tab;
  if (t === "osgor_brv") {
    if (!OS.brv && !OS.brvErr) { body.innerHTML = anLoadingCard(); osLoadBrv(); return; }
    body.innerHTML = brvHtml("adm").replace(/^<div class="card">/, "<div>");
    bindBrv(body);
    return;
  }
  if (t === "risk_thresholds") return admThresholds(body);
  if (t === "coefficients") return admCoef(body);
  if (t === "min_rates") return admMin(body);
  if (t === "depreciation_norms") return admNorms(body);
}

/* ---------- пороги уровня риска ---------- */
function weightName(k){
  switch (k) {
    case "rate": return T("tg.adm.w.rate", "Ставка к минимуму и рынку");
    case "mfl_retention": return T("tg.adm.w.mfl", "MFL к лимиту удержания");
    case "losses": return T("tg.adm.w.losses", "Убытки за 3 года");
    case "insurance_to_value": return T("tg.adm.w.itv", "Страховая сумма к стоимости");
    case "seismic": return T("tg.adm.w.seismic", "Сейсмичность");
    default: return k;
  }
}
async function admThresholds(body){
  if (!ADM.th) {
    body.innerHTML = anLoadingCard();
    const r = await api("/analytics/risk/thresholds");
    if (!r.ok) { body.innerHTML = admLoadErr(r); return; }
    ADM.th = r.data;
    if (ADM.tab !== "risk_thresholds") return;
  }
  const th = ADM.th.thresholds || {}, lb = th.level_bounds || [], w = th.weights || {};
  body.innerHTML = '<p class="note">' + esc(T("tg.adm.th_hint", "Границы уровней — баллы от 0 до 100 по возрастанию; веса — доли вклада в общий балл (сервер нормирует их сам).")) + "</p>"
    + '<div class="h3">' + esc(T("tg.adm.th_bounds", "Границы уровней")) + '</div><div class="inline">'
    + lb.map((v, i) => '<div><label class="f" for="thB' + i + '">' + esc(T("tg.adm.th_bound", "{a} → {b}", {a: lvName(i), b: lvName(i + 1)})) + "</label>"
      + '<input id="thB' + i + '" type="text" inputmode="decimal" value="' + esc(v) + '"></div>').join("") + "</div>"
    + '<div class="h3">' + esc(T("tg.adm.th_weights", "Веса")) + '</div><div class="inline">'
    + Object.keys(w).map(k => '<div><label class="f" for="thW-' + esc(k) + '">' + esc(weightName(k)) + "</label>"
      + '<input id="thW-' + esc(k) + '" data-w="' + esc(k) + '" type="text" inputmode="decimal" value="' + esc(w[k]) + '"></div>').join("") + "</div>"
    + '<div class="actions"><button type="button" class="btn btn-primary" id="thSave">' + esc(T("tg.adm.th_save", "Сохранить пороги")) + "</button>"
    + '<button type="button" class="btn btn-secondary" id="thReset">' + esc(T("tg.adm.th_reset", "Вернуть значения по умолчанию")) + "</button></div>"
    + '<div class="note msg" id="thMsg"></div>'
    + (th.calibrated === 0 ? '<p class="note warn">' + esc(T("tg.adm.th_uncal", "Шкала экспертная, не калибрована — пока нет выгрузок убытков компании.")) + "</p>" : "")
    + ((ADM.th.history || []).length ? '<details class="how"><summary>' + esc(T("tg.os.brv_history", "История ({n})", {n: ADM.th.history.length})) + '</summary><div class="chain">'
      + ADM.th.history.map(h => "<div><span>" + esc(when(h.created_at) + " · " + (h.created_by || "")) + "</span><b>" + esc(h.note || "") + "</b></div>").join("") + "</div></details>" : "");
  $("#thSave").onclick = async () => {
    const bounds = lb.map((_, i) => dec($("#thB" + i).value));
    const weights = {};
    body.querySelectorAll("[data-w]").forEach(el => { weights[el.dataset.w] = dec(el.value); });
    const okB = bounds.every((v, i) => v != null && !isNaN(v) && v > 0 && v < 100 && (!i || v > bounds[i - 1]));
    const okW = Object.keys(weights).every(k => weights[k] != null && !isNaN(weights[k]) && weights[k] >= 0);
    if (!okB) { $("#thMsg").innerHTML = errHtml(T("tg.adm.th_bad_bounds", "Границы — числа от 0 до 100, каждая больше предыдущей.")); return; }
    if (!okW) { $("#thMsg").innerHTML = errHtml(T("tg.adm.th_bad_weights", "Веса — неотрицательные числа.")); return; }
    $("#thMsg").innerHTML = spin(T("common.saving", "сохраняю…"));
    const r = await api("/analytics/risk/thresholds", jsonOpts("PUT", {level_bounds: bounds, weights: weights}));
    if (!r.ok) { $("#thMsg").innerHTML = errHtml(admErr(r)); return; }
    ADM.th = null; await admThresholds(body);
    $("#thMsg").innerHTML = okHtml(T("tg.adm.th_saved", "Пороги сохранены. Новые анализы считаются по ним."));
  };
  $("#thReset").onclick = async () => {
    $("#thMsg").innerHTML = spin(T("common.saving", "сохраняю…"));
    const r = await api("/analytics/risk/thresholds", jsonOpts("PUT", {reset: true}));
    if (!r.ok) { $("#thMsg").innerHTML = errHtml(admErr(r)); return; }
    ADM.th = null; await admThresholds(body);
    $("#thMsg").innerHTML = okHtml(T("tg.adm.th_reset_done", "Вернули значения по умолчанию."));
  };
}

/* ---------- коэффициенты: поиск и правка множителя ---------- */
const COEF_SOURCE = "правка администратора в мини-аппе";   // пишется в базу, на экран не выводится
async function admCoef(body){
  if (!ADM.coef) {
    body.innerHTML = anLoadingCard();
    const r = await api("/reference/coefficients");
    if (!r.ok) { body.innerHTML = admLoadErr(r); return; }
    ADM.coef = r.data || [];
    if (ADM.tab !== "coefficients") return;
  }
  body.innerHTML = '<label class="f" for="coefQ">' + esc(T("common.search", "Поиск")) + "</label>"
    + '<input id="coefQ" type="search" autocomplete="off" value="' + esc(ADM.coefQ) + '" placeholder="' + esc(T("tg.adm.coef_ph", "фактор, вариант или класс, например сейсм или 8")) + '">'
    + '<div id="coefTbl"></div>';
  $("#coefQ").addEventListener("input", () => { ADM.coefQ = $("#coefQ").value; admCoefTable(); });
  admCoefTable();
}
function admCoefTable(){
  const q = ADM.coefQ.trim().toLowerCase();
  const rows = ADM.coef.filter(c => !q || [c.factor_name, c.option_name, c.class_code, c.factor_code].join(" ").toLowerCase().indexOf(q) >= 0);
  const shown = rows.slice(0, 40);
  $("#coefTbl").innerHTML = '<p class="note">' + esc(T("tg.adm.shown", "Показано {n} из {total}.", {n: shown.length, total: rows.length})) + "</p>"
    // три колонки вместо пяти: класс и вариант — мелко под названием фактора
    + '<div class="tblwrap"><table class="tbl"><thead><tr><th>' + esc(T("tg.adm.factor", "Фактор")) + " · " + esc(T("tg.adm.option", "Вариант")) + "</th><th>"
    + esc(T("tg.adm.mult", "Множитель")) + "</th><th></th></tr></thead><tbody>"
    + shown.map(c => "<tr><td>" + esc(c.factor_name)
      + (c.calibrated ? "" : ' <span class="tag off">' + esc(T("tg.adm.uncal", "не калибровано")) + "</span>")
      + "<small>" + esc(T("tg.adm.cls_n", "класс {c}", {c: c.class_code || "—"})) + " · " + esc(c.option_name) + "</small></td>"
      + '<td class="num w96"><input type="text" inputmode="decimal" data-cm="' + c.id + '" value="' + esc(c.multiplier) + '" aria-label="' + esc(T("tg.adm.mult", "Множитель")) + '"></td>'
      + '<td class="act w1"><button type="button" class="btn btn-secondary btn-sm" data-cs="' + c.id + '">' + esc(T("common.save", "Сохранить")) + "</button></td></tr>").join("")
    + "</tbody></table></div>" + '<div class="note msg" id="coefMsg"></div>';
  document.querySelectorAll("[data-cs]").forEach(b => { b.onclick = () => admCoefSave(Number(b.dataset.cs)); });
}
async function admCoefSave(id){
  const c = ADM.coef.filter(x => x.id === id)[0];
  const v = dec(document.querySelector('[data-cm="' + id + '"]').value);
  if (!c) return;
  if (v == null || isNaN(v) || v <= 0 || v > 10) { $("#coefMsg").innerHTML = errHtml(T("tg.adm.mult_bad", "Множитель — число больше 0 и не больше 10, например 1,15.")); return; }
  $("#coefMsg").innerHTML = spin(T("common.saving", "сохраняю…"));
  const r = await api("/admin/coefficients", jsonOpts("POST", {factor_code: c.factor_code, factor_name: c.factor_name, class_code: c.class_code,
    option_code: c.option_code, option_name: c.option_name, multiplier: v, calibrated: 0, source: COEF_SOURCE}));
  if (!r.ok) { $("#coefMsg").innerHTML = errHtml(admErr(r)); return; }
  c.multiplier = v; c.calibrated = 0;
  COEFS = [];  REFS_DONE = false;                // расчёт перечитает справочник
  $("#coefMsg").innerHTML = okHtml(T("tg.adm.coef_saved", "Сохранено: {f} · {o} = ×{v}. Отметка «не калибровано» стоит.", {f: c.factor_name, o: c.option_name, v: nf(v, 2)}));
}

/* ---------- минимальные ставки: просмотр и добавление ---------- */
async function admMin(body){
  if (!ADM.minr) {
    body.innerHTML = anLoadingCard();
    const r = await api("/reference/min_rates");
    if (!r.ok) { body.innerHTML = admLoadErr(r); return; }
    ADM.minr = r.data || [];
    if (ADM.tab !== "min_rates") return;
  }
  const vers = {};
  ADM.minr.forEach(m => { vers[m.tariff_version_id] = (m.version || "") + " · " + (m.level || "") + " · " + (m.effective_from || ""); });
  body.innerHTML = '<div class="h3">' + esc(T("tg.adm.min_add", "Добавить минимальную ставку")) + "</div>"
    + '<div class="inline"><div><label class="f" for="mrVer">' + esc(T("tg.adm.min_ver", "Версия тарифов")) + "</label><select id=\"mrVer\">"
    + Object.keys(vers).map(k => '<option value="' + esc(k) + '">' + esc(vers[k]) + "</option>").join("") + "</select></div>"
    + '<div><label class="f" for="mrProd">' + esc(T("tg.adm.min_prod", "Продукт")) + '</label><input id="mrProd" type="text" autocomplete="off" placeholder="0807"></div>'
    + '<div><label class="f" for="mrCls">' + esc(T("tg.adm.cls", "Класс")) + '</label><input id="mrCls" type="text" autocomplete="off" placeholder="8"></div>'
    + '<div><label class="f" for="mrRate">' + esc(T("tg.adm.min_rate", "Минимум, %")) + '</label><input id="mrRate" type="text" inputmode="decimal" placeholder="0,05"></div></div>'
    + '<div class="actions"><button type="button" class="btn btn-primary" id="mrSave">' + esc(T("tg.adm.min_save", "Добавить ставку")) + "</button></div>"
    + '<div class="note msg" id="mrMsg"></div>'
    + '<label class="f" for="mrQ">' + esc(T("common.search", "Поиск")) + '</label><input id="mrQ" type="search" autocomplete="off" value="' + esc(ADM.minQ) + '" placeholder="' + esc(T("tg.adm.min_ph", "код продукта или класс")) + '">'
    + '<div id="mrTbl"></div>';
  $("#mrQ").addEventListener("input", () => { ADM.minQ = $("#mrQ").value; admMinTable(); });
  $("#mrSave").onclick = admMinSave;
  admMinTable();
}
function admMinTable(){
  const q = ADM.minQ.trim().toLowerCase();
  const rows = ADM.minr.filter(m => !q || [m.product_code, m.class_code].join(" ").toLowerCase().indexOf(q) >= 0);
  const shown = rows.slice(0, 40);
  $("#mrTbl").innerHTML = '<p class="note">' + esc(T("tg.adm.shown", "Показано {n} из {total}.", {n: shown.length, total: rows.length})) + "</p>"
    // две колонки: продукт с классом мелко под ним · минимум с датой мелко под ним
    + '<div class="tblwrap"><table class="tbl"><thead><tr><th>' + esc(T("tg.adm.min_prod", "Продукт")) + "</th><th>"
    + esc(T("tg.adm.min_rate", "Минимум, %")) + "</th></tr></thead><tbody>"
    + shown.map(m => '<tr><td class="n">' + esc(m.product_code) + "<small>" + esc(T("tg.adm.cls_n", "класс {c}", {c: m.class_code || "—"})) + "</small></td>"
      + '<td class="n num">' + esc(pct(m.min_rate_pct, 3)) + "<small>" + esc(T("tg.adm.from_d", "с {d}", {d: dateOnly(m.effective_from)})) + "</small></td></tr>").join("") + "</tbody></table></div>";
}
async function admMinSave(){
  const ver = Number($("#mrVer").value), prod = $("#mrProd").value.trim(), cls = $("#mrCls").value.trim(), rate = dec($("#mrRate").value);
  if (!ver) { $("#mrMsg").innerHTML = errHtml(T("tg.adm.min_no_ver", "Нет версии тарифов — сначала заведите её в админке.")); return; }
  if (!/^\d{4}$/.test(prod)) { $("#mrMsg").innerHTML = errHtml(T("tg.adm.min_bad_prod", "Код продукта — четыре цифры, например 0807.")); return; }
  if (rate == null || isNaN(rate) || rate <= 0 || rate > 100) { $("#mrMsg").innerHTML = errHtml(T("tg.adm.min_bad_rate", "Минимальная ставка — число от 0 до 100, в процентах.")); return; }
  $("#mrMsg").innerHTML = spin(T("common.saving", "сохраняю…"));
  const r = await api("/admin/min-rates", jsonOpts("POST", {tariff_version_id: ver, product_code: prod, class_code: cls || null, min_rate_pct: rate}));
  if (!r.ok) { $("#mrMsg").innerHTML = errHtml(admErr(r)); return; }
  ADM.minr = null;
  await admMin($("#admBody"));
  $("#mrMsg").innerHTML = okHtml(T("tg.adm.min_saved", "Ставка добавлена: продукт {p}, минимум {r}.", {p: prod, r: pct(rate, 3)}));
}

/* ---------- нормы износа: правка, добавление, удаление ---------- */
async function admNorms(body){
  if (!ADM.norms) {
    body.innerHTML = anLoadingCard();
    const r = await api("/valuation/norms");
    if (!r.ok) { body.innerHTML = admLoadErr(r); return; }
    ADM.norms = r.data || [];
    if (ADM.tab !== "depreciation_norms") return;
  }
  body.innerHTML = '<div class="tblwrap"><table class="tbl norms"><thead><tr><th>' + esc(T("common.name", "Название")) + "</th><th>" + esc(T("tg.adm.norm_rate", "Износ в год, %"))
    + "</th><th>" + esc(T("tg.adm.norm_min", "Остаток не ниже, %")) + "</th><th></th></tr></thead><tbody>"
    + ADM.norms.map(n => "<tr><td>" + esc(n.name) + (n.calibrated ? "" : ' <span class="tag off">' + esc(T("tg.adm.uncal", "не калибровано")) + "</span>")
      + "<small>" + esc(n.code) + "</small></td>"
      + '<td class="w90" data-l="' + esc(T("tg.adm.norm_rate", "Износ в год, %")) + '"><input type="text" inputmode="decimal" data-nr="' + esc(n.code) + '" value="' + esc(n.rate_pct) + '" aria-label="' + esc(T("tg.adm.norm_rate", "Износ в год, %")) + '"></td>'
      + '<td class="w90" data-l="' + esc(T("tg.adm.norm_min", "Остаток не ниже, %")) + '"><input type="text" inputmode="decimal" data-nm="' + esc(n.code) + '" value="' + esc(n.residual_min_pct == null ? "" : n.residual_min_pct) + '" aria-label="' + esc(T("tg.adm.norm_min", "Остаток не ниже, %")) + '"></td>'
      + '<td class="act w1 nw"><button type="button" class="btn btn-secondary btn-sm" data-ns="' + esc(n.code) + '">' + esc(T("common.save", "Сохранить")) + "</button> "
      + '<button type="button" class="btn btn-link btn-sm" data-nd="' + esc(n.code) + '">' + esc(T("common.delete", "Удалить")) + "</button></td></tr>").join("")
    + "</tbody></table></div>"
    + '<div class="h3">' + esc(T("tg.adm.norm_add", "Новая норма")) + '</div><div class="inline">'
    + '<div><label class="f" for="nnCode">' + esc(T("tg.adm.norm_code", "Код")) + '</label><input id="nnCode" type="text" autocomplete="off" placeholder="vehicles"></div>'
    + '<div><label class="f" for="nnName">' + esc(T("common.name", "Название")) + '</label><input id="nnName" type="text" autocomplete="off"></div>'
    + '<div><label class="f" for="nnRate">' + esc(T("tg.adm.norm_rate", "Износ в год, %")) + '</label><input id="nnRate" type="text" inputmode="decimal"></div>'
    + '<div><label class="f" for="nnMin">' + esc(T("tg.adm.norm_min", "Остаток не ниже, %")) + '</label><input id="nnMin" type="text" inputmode="decimal"></div></div>'
    + '<div class="actions"><button type="button" class="btn btn-primary" id="nnSave">' + esc(T("tg.adm.norm_add_btn", "Добавить норму")) + "</button></div>"
    + '<div class="note msg" id="nnMsg"></div>';
  body.querySelectorAll("[data-ns]").forEach(b => { b.onclick = () => {
    const n = ADM.norms.filter(x => x.code === b.dataset.ns)[0];
    admNormSave(n.code, n.name, document.querySelector('[data-nr="' + n.code + '"]').value, document.querySelector('[data-nm="' + n.code + '"]').value, n);
  }; });
  body.querySelectorAll("[data-nd]").forEach(b => { b.onclick = () => {
    // удаление без возврата: первое нажатие спрашивает, второе удаляет
    if (b.dataset.armed !== "1") { b.dataset.armed = "1"; b.textContent = T("tg.adm.confirm_delete", "Точно удалить?"); return; }
    admNormDelete(b.dataset.nd);
  }; });
  $("#nnSave").onclick = () => admNormSave($("#nnCode").value.trim(), $("#nnName").value.trim(), $("#nnRate").value, $("#nnMin").value, null);
}
async function admNormSave(code, name, rateRaw, minRaw, old){
  const rate = dec(rateRaw), min = dec(minRaw);
  if (!/^[a-z0-9_]{2,40}$/.test(code)) { $("#nnMsg").innerHTML = errHtml(T("tg.adm.norm_bad_code", "Код — латинские буквы, цифры и подчёркивание, например vehicles.")); return; }
  if (!name) { $("#nnMsg").innerHTML = errHtml(T("tg.adm.norm_bad_name", "Укажите название нормы.")); return; }
  if (rate == null || isNaN(rate) || rate < 0 || rate > 100 || (min != null && (isNaN(min) || min < 0 || min > 100))) {
    $("#nnMsg").innerHTML = errHtml(T("tg.adm.norm_bad_pct", "Проценты — числа от 0 до 100.")); return;
  }
  $("#nnMsg").innerHTML = spin(T("common.saving", "сохраняю…"));
  const r = await api("/valuation/norms", jsonOpts("POST", {code: code, name: name, rate_pct: rate, residual_min_pct: min, calibrated: 0,
    source: old ? old.source : null, note: old ? old.note : null}));
  if (!r.ok) { $("#nnMsg").innerHTML = errHtml(admErr(r)); return; }
  ADM.norms = null;
  await admNorms($("#admBody"));
  $("#nnMsg").innerHTML = okHtml(T("tg.adm.norm_saved", "Норма «{n}» сохранена.", {n: name}));
}
async function admNormDelete(code){
  $("#nnMsg").innerHTML = spin(T("tg.adm.deleting", "удаляю…"));
  const r = await api("/valuation/norms/" + encodeURIComponent(code), {method: "DELETE"});
  if (!r.ok) { $("#nnMsg").innerHTML = errHtml(admErr(r)); return; }
  ADM.norms = null;
  await admNorms($("#admBody"));
  $("#nnMsg").innerHTML = okHtml(T("tg.adm.norm_deleted", "Норма {c} удалена.", {c: code}));
}

/* ---------- настройки: состояние бота ---------- */
let BOT_ERR = "";
async function loadBot(){
  $("#botKv").innerHTML = "<span>" + esc(T("tg.bot_state", "Состояние")) + "</span><b><span class=\"spin\"></span></b>";
  const r = await api("/tg/bot-status");
  BOT_ERR = r.ok ? "" : r.error;
  if (r.ok) BOT = r.data;
  paintBot();
}
function paintBot(){
  if (BOT_ERR) { $("#botKv").innerHTML = "<span>" + esc(T("tg.bot_state", "Состояние")) + "</span><b>" + esc(BOT_ERR) + "</b>"; return; }
  const b = BOT;
  if (!b) return;
  const yes = T("common.yes", "да"), no = T("common.no", "нет");
  $("#botKv").innerHTML = "<span>" + esc(T("tg.bot_connected", "Бот подключён")) + "</span><b>" + esc(b.connected ? yes : no) + "</b>"
    + "<span>" + esc(T("tg.bot_token", "Токен")) + "</span><b>" + esc(b.token_mask || T("common.not_set", "не задан")) + "</b>"
    + "<span>" + esc(T("tg.bot_webhook", "Вебхук")) + "</span><b>" + esc(b.webhook_set ? T("tg.bot_webhook_on", "настроен") : T("tg.bot_webhook_off", "не настроен")) + "</b>"
    + "<span>" + esc(T("tg.bot_polling", "Опрос обновлений")) + "</span><b>" + esc(b.polling ? (b.polling_running ? T("tg.bot_polling_on", "идёт") : T("tg.bot_polling_idle", "включён, но не запущен")) : T("tg.bot_polling_off", "выключен")) + "</b>"
    + (b.polling_error ? "<span>" + esc(T("tg.bot_last_error", "Последняя ошибка опроса")) + "</span><b>" + esc(b.polling_error) + "</b>" : "")
    + "<span>" + esc(T("tg.bot_messages", "Сообщений в журнале")) + "</span><b>" + fmt(b.messages) + "</b>"
    + "<span>" + esc(T("tg.bot_last_message", "Последнее сообщение")) + "</span><b>" + esc(when(b.last_message_at) || "—") + "</b>"
    + "<span>" + esc(T("tg.bot_admin_code", "Код первого администратора")) + "</span><b>" + esc(b.bootstrap_code_set ? T("tg.set", "задан") : T("common.not_set", "не задан")) + "</b>"
    + "<span>" + esc(T("tg.consent_version", "Согласие, версия")) + "</span><b>" + esc(b.consent_version || "—") + "</b>"
    + (b.reason ? "<span>" + esc(T("tg.bot_reason", "Пояснение")) + "</span><b>" + esc(b.reason) + "</b>" : "");
}
$("#botReload").onclick = loadBot;


