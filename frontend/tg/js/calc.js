/* app/tg/js/calc.js — вкладка «Калькулятор» */
/* =====================================================================================
   Калькулятор премии (заказчик 22.09.2026): продукт, страховая сумма, срок и пара главных
   факторов класса — и сразу ставка, премия и минимум. Без диалога и без документов.
   Данные: GET /reference/classes, /reference/products, /reference/coefficients;
           POST /calculate — тот же движок, что и раньше.
   Ввод не перерисовываем: меняются только чипы и сегменты, курсор в поле суммы не теряется.
   ===================================================================================== */
const CALC = {classes: [], products: [], coefs: [], done: false, err: "", busy: false,
  cls: "", product: "", q: "", sum: 0, days: 365, factors: {}, res: null, body: null};
const CALC_TERMS = [3, 6, 12];
const CALC_FACTORS = 3;                       // столько главных факторов класса показываем
/* пункты, которые заказчик убрал из интерфейса (их же убирает сервер) */
const HIDE_CHECKS = ["disclosure", "premium_unpaid"];
function keepCheck(c){ return HIDE_CHECKS.indexOf(String((c || {}).code || "")) < 0; }

async function loadCalc(){
  if (!CALC.done && !CALC.busy) {
    CALC.busy = true;
    calcPaint();
    const [c, p, k] = await Promise.all([api("/reference/classes"), api("/reference/products"), api("/reference/coefficients")]);
    CALC.busy = false;
    if (!c.ok || !p.ok || !k.ok) { CALC.err = c.error || p.error || k.error; calcPaint(); return; }
    CALC.err = "";
    CALC.done = true;
    CALC.classes = c.data || []; CALC.products = p.data || []; CALC.coefs = k.data || [];
    if (!CALC.cls) CALC.cls = CALC.classes.some(x => x.code === "8") ? "8" : ((CALC.classes[0] || {}).code || "");
    calcSeedFactors();
  }
  calcPaint();
}
function calcClassesOf(p){
  if (Array.isArray(p.classes)) return p.classes;
  if (typeof p.classes === "string" && p.classes) return p.classes.split(",");
  return [];
}
function calcOptions(factor){ return CALC.coefs.filter(x => x.class_code === CALC.cls && x.factor_code === factor); }
function calcFactorCodes(){
  const codes = [];
  CALC.coefs.filter(x => x.class_code === CALC.cls).forEach(r => {
    if (codes.indexOf(r.factor_code) < 0 && r.factor_code !== "franchise") codes.push(r.factor_code);
  });
  return codes.slice(0, CALC_FACTORS);
}
/* разумное значение по умолчанию: первый вариант каждого фактора */
function calcSeedFactors(){
  CALC.factors = {};
  calcFactorCodes().forEach(code => {
    const o = calcOptions(code)[0];
    if (o) CALC.factors[code] = o.option_code;
  });
}
function calcProducts(){
  const q = String(CALC.q || "").trim().toLowerCase();
  const mine = CALC.products.filter(p => calcClassesOf(p).indexOf(CALC.cls) >= 0);
  if (!q) return mine.slice(0, 6);
  return CALC.products.filter(p => String(p.code).toLowerCase().indexOf(q) === 0
    || String(p.name || "").toLowerCase().indexOf(q) >= 0).slice(0, 12);
}

function calcPaint(){
  const box = $("#calcForm");
  if (!box) return;
  if (CALC.err) {
    box.innerHTML = '<div class="card"><p class="note err">' + esc(T("tg.refs_failed", "Справочники не загрузились: {reason}", {reason: CALC.err}))
      + '</p><div class="actions"><button type="button" class="btn btn-secondary" id="calcRetry">' + esc(T("common.retry", "Повторить")) + "</button></div></div>";
    $("#calcRetry").onclick = () => { CALC.err = ""; loadCalc(); };
    return;
  }
  if (!CALC.done) { box.innerHTML = '<div class="card">' + anLoadingCard() + "</div>"; return; }
  const uncal = calcFactorCodes().some(code => calcOptions(code).some(o => !o.calibrated));
  box.innerHTML = '<div class="card">'
    + "<h2>" + esc(T("tg.an.f.class_code", "Класс страхования")) + "</h2>"
    + '<div class="segsel" id="calcCls">' + CALC.classes.slice(0, 8).map(c =>
      '<button type="button" data-cls="' + esc(c.code) + '" aria-pressed="' + (CALC.cls === c.code) + '">'
      + esc(c.code + " · " + c.name) + "</button>").join("") + "</div>"
    + '<div class="h3">' + esc(T("tg.an.f.product_code", "Продукт")) + "</div>"
    + '<div class="chips" id="calcProds">' + calcProducts().map(p =>
      '<button type="button" class="chip" data-prod="' + esc(p.code) + '" aria-pressed="' + (CALC.product === p.code) + '">'
      + esc(p.code + " · " + p.name) + "</button>").join("") + "</div>"
    + '<label class="f" for="calcQ">' + esc(T("tg.chat.find_product", "Найти продукт среди всех")) + "</label>"
    + '<input id="calcQ" type="search" autocomplete="off" value="' + esc(CALC.q) + '" placeholder="'
    + esc(T("tg.an.find_ph", "код или слово, например 0807 или склад")) + '">'
    + "</div>"
    + '<div class="card"><h2>' + esc(T("tg.an.f.sum_insured", "Страховая сумма")) + "</h2>"
    + '<input id="calcSum" type="text" inputmode="numeric" enterkeyhint="done" value="'
    + esc(CALC.sum ? Number(CALC.sum).toLocaleString(LOC()) : "") + '">'
    + '<div class="qadd" id="calcAdd">'
    + '<button type="button" data-add="1000000">' + esc(T("tg.chat.add_mln", "+млн")) + "</button>"
    + '<button type="button" data-add="1000000000">' + esc(T("tg.chat.add_bln", "+млрд")) + "</button></div>"
    + '<div class="h3">' + esc(T("tg.an.f.term_months", "Срок страхования")) + "</div>"
    + '<div class="segsel" id="calcTerm">' + CALC_TERMS.map(m =>
      '<button type="button" data-months="' + m + '" aria-pressed="' + (CALC.days === Math.round(m * 30.4)) + '">'
      + esc(T("tg.an.months_n", "{n} мес.", {n: m})) + "</button>").join("") + "</div>"
    + "</div>"
    + '<div class="card"><h2>' + esc(T("tg.calc.factors", "Главные факторы")) + "</h2>"
    + calcFactorsHtml()
    + (uncal ? '<p class="note warn">' + esc(T("tg.calc_uncal", "Коэффициенты помечены «не калибровано»: это экспертные значения, компания их ещё не подтвердила своей статистикой убытков.")) + "</p>" : "")
    + '<div class="actions"><button type="button" class="btn btn-primary tg-main" id="calcGo">'
    + esc(T("tg.calc_button", "Посчитать")) + "</button></div>"
    + '<div class="note msg" id="calcMsg"></div></div>';
  calcBind();
  calcPaintOut();
  syncTgButtons();
}
function calcFactorsHtml(){
  const codes = calcFactorCodes();
  if (!codes.length) return '<p class="note">' + esc(T("tg.calc_no_factors", "Для этого класса факторов в справочнике нет — ставка считается по базовой.")) + "</p>";
  return codes.map(code => {
    const opts = calcOptions(code);
    const label = T("calc.factor." + code, (opts[0] || {}).factor_name || code);
    const cur = CALC.factors[code] || "";
    if (opts.length <= 5) {
      return '<div class="h3">' + esc(label) + "</div>"
        + '<div class="segsel">' + opts.map(o =>
          '<button type="button" data-fc="' + esc(code) + '" data-fv="' + esc(o.option_code) + '" aria-pressed="' + (cur === o.option_code) + '">'
          + esc(o.option_name) + " ×" + esc(nf(o.multiplier, 2)) + (o.calibrated ? "" : " " + esc(T("tg.calc_uncal_short", "(не калибровано)"))) + "</button>").join("")
        + "</div>";
    }
    return '<label class="f" for="calcf-' + esc(code) + '">' + esc(label) + "</label>"
      + '<select id="calcf-' + esc(code) + '" data-fsel="' + esc(code) + '">'
      + '<option value="">' + esc(T("tg.calc_factor_off", "не учитывать")) + "</option>"
      + opts.map(o => '<option value="' + esc(o.option_code) + '"' + (cur === o.option_code ? " selected" : "") + ">"
        + esc(o.option_name) + " ×" + esc(nf(o.multiplier, 2)) + "</option>").join("") + "</select>";
  }).join("");
}
function calcBind(){
  const box = $("#calcForm");
  box.querySelectorAll("[data-cls]").forEach(b => {
    b.onclick = () => { CALC.cls = b.dataset.cls; CALC.product = ""; calcSeedFactors(); haptic("select"); calcPaint(); };
  });
  box.querySelectorAll("[data-prod]").forEach(b => {
    b.onclick = () => { CALC.product = b.dataset.prod; haptic("select"); calcPaint(); };
  });
  box.querySelectorAll("[data-months]").forEach(b => {
    b.onclick = () => { CALC.days = Math.round(Number(b.dataset.months) * 30.4); haptic("select"); calcPaint(); };
  });
  box.querySelectorAll("[data-fc]").forEach(b => {
    b.onclick = () => { CALC.factors[b.dataset.fc] = b.dataset.fv; haptic("select"); calcPaint(); };
  });
  box.querySelectorAll("[data-fsel]").forEach(sel => {
    sel.onchange = () => { CALC.factors[sel.dataset.fsel] = sel.value; };
  });
  const q = $("#calcQ");
  q.oninput = () => {
    CALC.q = q.value;
    const at = q.selectionStart;
    calcPaint();
    const again = $("#calcQ");
    if (again) { again.focus(); try { again.setSelectionRange(at, at); } catch (e) {} }
  };
  const sum = $("#calcSum");
  sum.oninput = () => { groupDigitsLive(sum); CALC.sum = num(sum.value); };
  box.querySelectorAll("#calcAdd button").forEach(b => {
    b.onclick = () => {
      CALC.sum = (CALC.sum || 0) + Number(b.dataset.add);
      $("#calcSum").value = Number(CALC.sum).toLocaleString(LOC());
      haptic("select");
    };
  });
  $("#calcGo").onclick = calcRun;
}

async function calcRun(){
  if (!CALC.product) { $("#calcMsg").innerHTML = errHtml(T("tg.no_product", "Выберите продукт — без него движок не знает минимальный тариф.")); return; }
  if (!CALC.sum) { $("#calcMsg").innerHTML = errHtml(T("tg.zero_sum", "Страховая сумма и стоимость объекта должны быть больше нуля.")); return; }
  const prod = CALC.products.filter(p => p.code === CALC.product)[0] || {};
  const body = {product_code: CALC.product, class_code: calcClassesOf(prod)[0] || CALC.cls,
    object_type: CALC_OBJECT, value_amount: CALC.sum, sum_insured: CALC.sum, term_days: CALC.days,
    factors: Object.assign({}, CALC.factors), premium_paid: true, disclosure_done: true};
  CALC.busy = true;
  tgBusy(true);
  $("#calcGo").disabled = true;
  $("#calcMsg").innerHTML = spin(T("common.calculating", "считаю…"));
  const r = await api("/calculate", jsonOpts("POST", body));
  CALC.busy = false;
  tgBusy(false);
  if ($("#calcGo")) $("#calcGo").disabled = false;
  if (!r.ok) { haptic("error"); $("#calcMsg").innerHTML = errHtml(T("tg.calc_failed", "Расчёт не выполнен: {reason}", {reason: r.error})); return; }
  $("#calcMsg").innerHTML = "";
  CALC.res = r.data;
  CALC.body = body;
  haptic("success");
  calcPaintOut();
  const out = $("#calcOut");
  if (out && out.scrollIntoView) out.scrollIntoView({block: "start", behavior: "smooth"});
}
const CALC_OBJECT = "Склад";                 // тип объекта по справочнику сервера (значение, не подпись)

function calcPaintOut(){
  const box = $("#calcOut");
  if (!box) return;
  const j = CALC.res, b = CALC.body;
  if (!j || !b) { box.innerHTML = ""; return; }
  const r = j.rates || {};
  const checks = (j.checks || []).filter(keepCheck);
  box.innerHTML = '<div class="card"><h2>' + esc(T("tg.rate_and_premium", "Ставка и премия")) + "</h2>"
    + '<p class="sub">' + esc(T("tg.calc_rate_note", "Ставка годовая, премия — за {n} дней.", {n: b.term_days})) + "</p>"
    + '<div class="headline"><div class="big">' + pct(r.applied_pct) + '</div><span class="sm">' + esc(money(j.premium)) + "</span></div>"
    + '<div class="seg">'
    + "<div><span>" + esc(T("tg.calc_technical", "Техническая ставка")) + "</span><b>" + pct(r.technical_pct) + "</b></div>"
    + "<div><span>" + esc(T("tg.calc_min", "Минимум по продукту")) + "</span><b>" + pct(r.min_pct) + "</b></div>"
    + "</div>"
    + '<details class="opt"><summary>' + esc(T("calc.breakdown", "Из чего сложилась ставка")) + "</summary>"
    + '<div class="chain">' + (j.explanation || []).map(calcChainRow).join("") + "</div>"
    + (checks.length ? '<div class="h3">' + esc(T("calc.checks", "Проверки")) + "</div>"
        + checks.map(c => '<div class="check ' + (c.status === "ok" ? "ok" : c.status === "stop" ? "stop" : "warn") + '"><i></i><div><b>'
          + esc(c.title) + "</b><span>" + esc(c.detail) + "</span></div></div>").join("") : "")
    + "</details></div>";
}
function calcChainRow(x){
  let v = "";
  if (x.value_pct != null) v = pct(x.value_pct);
  else if (x.mult != null) v = "×" + nf(x.mult, 2);
  else if (x.add_pct != null) v = "+" + pct(x.add_pct);
  else if (x.divide_by != null) v = "÷" + nf(x.divide_by, 2);
  return "<div><span>" + esc(x.name) + "</span><b>" + v + "</b></div>";
}
/* смена языка: форма и итог перерисовываются целиком, введённые значения лежат в CALC */
function calcRepaint(){ calcPaint(); }

