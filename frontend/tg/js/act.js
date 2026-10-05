/* app/tg/js/act.js — ИИ-сюрвейер: шаг «Акт» (сводка, скоринг, вилка, ниже минимума, факторы, аналитика, отправка) */
/* ---------- «Сформировать акт»: POST /act/make ---------- */
const ERR_FIELD = {product_code: "prod", class_code: "prod", sum_insured: "sum_insured", object_value: "object_value",
  region: "region", year: "year", purchase_year: "purchase_year", location: "location", guard: "guard",
  want_lower_premium: "want_lower", losses_3y: "losses_count", price_new: "price_new", term_days: "term_days",
  protection: "protection", seismic_zone: "seismic_zone", construction: "construction", activity: "activity", deductible: "fr",
  market: "market", request: "br", contract: "ct", credit_report: "cb",
  class_fields: "cf", object_kind: "cf", parts: "parts", same_object: "parts", parts_confirmed: "parts",
  requested_rate_pct: "req_rate", objects: "ob", region_text: "region_text"};
function errText(field){
  switch (field) {
    case "br": return T("tg.dq.err_br", "Проверьте условия запроса филиала: тариф — больше 0 и не больше 100 %, премия — больше нуля, срок — обе даты («по» не раньше «с») или число дней от 1 до 3660, франшиза — больше нуля.");
    case "ct": return T("tg.dq.err_ct", "Проверьте условия договора: тариф — больше 0 и не больше 100 %, премия — больше нуля, срок — обе даты («по» не раньше «с») или число дней от 1 до 3660, франшиза — больше нуля.");
    case "cb": return T("tg.cb.err", "Проверьте отчёт бюро: балл — целое от 0 до 1000, класс — буква A–E и цифра (например, B2), дата — не раньше 2000 года, просрочки — числа не меньше нуля.");
    case "prod": return T("tg.wz.err_prod", "Выберите продукт из списка или хотя бы весь класс.");
    case "sum_insured": case "object_value": case "losses_amount": case "price_new":
      return T("tg.act.err_money", "Введите сумму больше нуля, в сумах.");
    case "region": return T("tg.act.err_region", "Выберите регион из списка.");
    case "region_text": return T("tg.act.region_text_err", "Впишите территорию страхования: страну, область или маршрут — до 120 знаков, без имён людей.");
    case "ob": return T("tg.act.obj_err", "Проверьте объекты: у каждого название, сумма и стоимость больше нуля; год — четыре цифры; номер — не больше 6 знаков; ставка — больше 0 и не больше 100.");
    case "year": case "purchase_year": return T("tg.act.err_year", "Год — четыре цифры, не раньше 1950 и не позже текущего.");
    case "losses_count": return T("tg.act.err_losses", "Убытков — целое число; мелких не больше, чем всех.");
    case "term_days": return T("tg.act.err_term", "Срок — целое число дней от 1 до 3660. Или оставьте поле пустым.");
    case "req_rate": return T("tg.act.err_req_rate", "Запрошенная ставка — число больше 0 и не больше 100, например 0,05. Или оставьте поле пустым.");
    case "fr": return T("tg.act.err_fr", "Размер франшизы — больше нуля и не больше 50 % страховой суммы (суммой — не больше половины). Или выберите «Не применять».");
    case "market": return T("tg.mk.err_market", "Проверьте объявления: цена — число больше нуля, год — от 1950, ссылка — https://…, курс доллара — от 100 до 1 000 000 сумов.");
    case "protection": case "seismic_zone": case "construction": case "activity":
      return T("tg.act.err_pick", "Выберите значение из списка или оставьте «не указано».");
    case "cf": return T("tg.tpl.err_cf", "Проверьте поля класса: суммы и количества — цифрами, варианты — из списка.");
    case "parts": return T("tg.pt.err_parts", "Проверьте части договора: у каждой части сумма больше нуля, вместе — страховая сумма договора; своя франшиза части — больше 0 и не больше 50 %.");
    default: return T("tg.act.err_value", "Проверьте значение.");
  }
}
function actBody(){
  const m = CH.must, o = CH.opt;
  // регион — кодом из списка (сервер сам подпишет его на языке акта); не из списка — словами, как есть
  const region = REGIONS.indexOf(m.region) >= 0 ? m.region : String(m.region || "");
  const must = {sum_insured: Number(m.sum_insured), object_value: Number(m.object_value), region: region};
  // «Другое»: территория текстом (до 120 знаков, без имён людей — проверяет и сервер)
  if (region === "other") must.region_text = String(m.region_text || "").trim().slice(0, 120);
  if (m.product_code) must.product_code = m.product_code; else must.class_code = m.class_code;
  const opt = {};
  if (o.year) opt.year = Number(o.year);
  if (o.location) opt.location = o.location;
  if (o.guard) opt.guard = o.guard === "yes";
  if (o.want_lower) opt.want_lower_premium = o.want_lower === "yes";
  if (o.losses_count != null || o.losses_small != null || o.losses_amount != null) {
    opt.losses_3y = {count: o.losses_count != null ? Number(o.losses_count) : null,
      small_count: o.losses_small != null ? Number(o.losses_small) : null,
      amount: o.losses_amount != null ? Number(o.losses_amount) : null};
  }
  if (o.price_new) opt.price_new = Number(o.price_new);
  if (o.purchase_year) opt.purchase_year = Number(o.purchase_year);
  if (o.term_days) opt.term_days = Number(o.term_days);
  // запрошенная ставка сотрудника; пусто — сервер возьмёт тариф договора или запроса филиала
  if (o.req_rate != null && String(o.req_rate).trim() !== "" && dec(o.req_rate) > 0) opt.requested_rate_pct = dec(o.req_rate);
  // уточнения сценариев — только допустимые для класса (app/act.py: validate)
  const cls = wzClass();
  if (o.protection && protCodes(cls).indexOf(o.protection) >= 0) opt.protection = o.protection;
  if (isProp(cls)) {
    if (o.seismic_zone && SEISMIC.indexOf(String(o.seismic_zone)) >= 0) opt.seismic_zone = Number(o.seismic_zone);
    if (o.construction && CONSTRUCTIONS.indexOf(o.construction) >= 0) opt.construction = o.construction;
    if (o.activity && ACTIVITIES.indexOf(o.activity) >= 0) opt.activity = o.activity;
  }
  // франшиза сотрудника: {pct | amount, type}; по обязательным видам не отправляется
  const f = CH.fr;
  if (f.on && !wzStatutory()) opt.deductible = f.unit === "amount" ? {amount: Number(f.amount), type: f.type} : {pct: dec(f.pct), type: f.type};
  // запрос филиала и договор: как прочитано, с правками сотрудника (исправлено — source = input)
  const rq = dqBody("br"), cq = dqBody("ct");
  if (rq) opt.request = rq;
  if (cq) opt.contract = cq;
  // отчёт кредитного бюро: как прочитано, с правками; источник и «было → стало» сервер определяет сам
  const cbb = cbBody();
  if (cbb) opt.credit_report = cbb;
  // оценка по объявлениям: правки сотрудника, номер загрузки снимков, курс
  const mk = mkBody();
  if (mk) opt.market = mk;
  // шаблон класса: вид объекта и поля класса (optional.object_kind, optional.class_fields)
  if (o.object_kind) opt.object_kind = o.object_kind;
  const cf = tplMainShown() ? tfBodyCf(cls, o.cf) : null;
  if (cf) opt.class_fields = cf;
  // части договора: суммы, признаки и поля класса каждой части; «один объект / разные объекты»
  const pb = ptOn() ? ptBody() : null;
  if (pb) { opt.parts = pb; opt.parts_confirmed = !!CH.pt.confirmed; }
  if (ptOn() && CH.pt && CH.pt.same != null) opt.same_object = CH.pt.same;
  // стоимость заменена медианой: в акт уходит и то, что заявил клиент
  if (mk && MK.declOrig > 0 && MK.declOrig !== Number(m.object_value)) opt.declared_value_original = MK.declOrig;
  // несколько объектов: перечень; сумма и стоимость договора — сумма по объектам; год, вид и поля класса — у объектов
  if (obOn()) {
    opt.objects = obBody(cls);
    must.sum_insured = obSum("sum");
    must.object_value = obSum("value");
    ["year", "object_kind", "class_fields", "parts", "parts_confirmed", "same_object"].forEach(k => { delete opt[k]; });
  }
  const body = {lang: I18N_LANG, must: must, optional: opt};
  const rec = CH.rec.filter(r => String(r.value || "").trim())
    .map(r => ({key: r.key, value: String(r.value).trim(), source: r.source, file: r.file || null}));
  if (CH.session) {
    body.session = CH.session;
    body.recognized = rec;
    body.damages = CH.damages.map(d => ({what: d.what, where: d.where}));
  } else if (rec.length) body.recognized = rec;       // марка и модель из «Полей класса» без фото
  return body;
}
async function actMake(){
  const m = CH.must, o = CH.opt, e = {};
  if (!m.class_code && !m.product_code) e.prod = errText("prod");
  if (obOn()) { const x = obCheck(); if (x) e.ob = x; }
  else ["sum_insured", "object_value"].forEach(k => { if (!(Number(m[k]) > 0)) e[k] = errText(k); });
  if (!m.region) e.region = errText("region");
  if (m.region === "other" && !String(m.region_text || "").trim()) e.region_text = errText("region_text");
  ["year", "purchase_year"].forEach(k => { if (o[k] && !/^\d{4}$/.test(String(o[k]))) e[k] = errText(k); });
  if (o.losses_small != null && o.losses_count != null && Number(o.losses_small) > Number(o.losses_count)) e.losses_count = errText("losses_count");
  if (o.term_days && !(/^\d{1,4}$/.test(String(o.term_days)) && Number(o.term_days) >= 1 && Number(o.term_days) <= 3660)) e.term_days = errText("term_days");
  if (o.req_rate != null && String(o.req_rate).trim() !== "") { const x = dec(o.req_rate); if (!(x > 0 && x <= 100)) e.req_rate = errText("req_rate"); }
  const f = CH.fr;
  if (f.on && !wzStatutory()) {
    const S = Number(m.sum_insured) || 0;
    if (f.unit === "amount") { const a = Number(f.amount) || 0; if (!(a > 0) || (S > 0 && a > S / 2)) e.fr = errText("fr"); }
    else { const p = dec(f.pct); if (!(p > 0 && p <= 50)) e.fr = errText("fr"); }
  }
  ["br", "ct"].forEach(k => { const x = dqCheck(k); if (x) e[k] = x; });
  if (cbCheck()) e.cb = errText("cb");
  if (ptOn() && (!ptOk() || ptFrBad())) e.parts = errText("parts");
  if (Object.keys(e).length) { actShowErrors(e); return; }
  CH.errs = {};
  CH.err = "";
  CH.busy = true;
  tgBusy(true);
  wzBarPaint();
  const msg = $("#wzBody .msg");
  if (msg) msg.innerHTML = spin(T("tg.act.making_hint", "Формирую акт — обычно это занимает до 20 секунд."));
  const r = await api("/act/make", jsonOpts("POST", actBody()));
  CH.busy = false;
  tgBusy(false);
  if (!r.ok) {
    const errs = r.status === 422 && r.data && r.data.errors && typeof r.data.errors === "object" ? r.data.errors : null;
    if (errs && Object.keys(errs).length) {
      const mapped = {};
      Object.keys(errs).forEach(k => { const f = ERR_FIELD[k]; if (f) mapped[f] = errText(f); });
      if (Object.keys(mapped).length) { actShowErrors(mapped); return; }
    }
    // 422 без понятного поля — своими словами, а не списком служебных имён полей сервера
    const d = r.data && typeof r.data.detail === "string" ? r.data.detail : "";
    CH.err = T("tg.act.make_failed", "Акт не сформирован: {reason}", {reason: r.status === 422
      ? T("tg.act.err_server", "сервер не принял данные — проверьте поля и попробуйте ещё раз") : (d || r.error)});
    haptic("error");
    wzPaint(true);
    return;
  }
  CH.act = r.data;
  CH.actId = String(r.data.id || "");
  CH.actErr = "";
  CH.actLang = "";                            // новый акт — на языке интерфейса
  CH.actBusy = false;                         // запоздавший ответ прежнего акта не держит выбор языка выключенным
  CH.actSeq = (CH.actSeq || 0) + 1;
  CH.obOpen = {};
  // комплексный продукт: первый расчёт без частей — предложение сервера в карточку «Части договора» и назад на шаг 2
  const P = ptAct(r.data);
  let back = false;
  if (P) {
    const first = !CH.pt || !CH.pt.items.length;
    if (first || (!CH.pt.edited && !CH.pt.confirmed)) ptFromServer(P);
    else { CH.pt.confirmed = !!P.confirmed; if (typeof P.same_object_default === "boolean") CH.pt.sameDef = P.same_object_default; }
    if (first && !P.confirmed) { CH.pt.hint = true; back = true; }
  }
  CH.wz = back ? 2 : 3;
  wzSave();
  haptic("success");
  wzPaint(true);
  window.scrollTo(0, 0);
  if (back) wzFocusField("parts");
}
function actShowErrors(e){
  CH.errs = e;
  CH.err = T("tg.act.err_fix", "Исправьте отмеченные поля — без них акт не сформировать.");
  if (OPT_KEYS.some(k => e[k]) || e.fr) CH.optOpen = true;
  haptic("error");
  wzPaint(true);
  const first = ["br", "ct", "cb", "prod", "sum_insured", "object_value", "region", "region_text", "term_days", "ob", "parts", "cf"].concat(OPT_KEYS, ["fr", "market"]).filter(k => e[k])[0];
  if (first) wzFocusField(first);
}

/* ---------- шаг 3: акт ---------- */
function decName(code){
  switch (code) {
    case "accept": return T("tg.act.dec.accept", "Принять");
    case "accept_with_clauses": return T("tg.act.dec.accept_with_clauses", "Принять с оговорками");
    case "decline": return T("tg.act.dec.decline", "Отказать");
    default: return T("tg.act.na", "данные недоступны");
  }
}
function levelName(code){
  switch (code) {
    case "low": return T("tg.act.lv.low", "низкий");
    case "moderate": return T("tg.act.lv.moderate", "умеренный");
    case "high": return T("tg.act.lv.high", "высокий");
    default: return T("tg.act.na", "данные недоступны");
  }
}
function verdictName(v){
  switch (v) {
    case "normal": return T("tg.act.v.normal", "в норме");
    case "under": return T("tg.act.v.under", "недострахование");
    case "over": return T("tg.act.v.over", "превышение");
    case "refine": return T("tg.act.v.refine", "стоимость нужно уточнить");
    default: return "";
  }
}
const frApplied = a => !!(a.franchise && a.franchise.applied) || !!(a.premium && a.premium.franchise_applied);
function actRate(a){
  const r = a.rate || {};
  // ставка с учётом применённой франшизы (rate.final_pct), иначе — ставка акта
  const v = frApplied(a) && r.final_pct != null && !isNaN(Number(r.final_pct)) ? r.final_pct : r.applied_pct;
  if (v == null || isNaN(Number(v))) return T("tg.act.rate_undefined", "не определён");
  return pct(v, dp(v));
}
function actRateNote(a){
  const r = a.rate || {};
  if (r.mode === "statutory") return T("tg.act.rate_statutory", "по нормативному акту, без поправок");
  if (r.mode === "undefined" || r.mode === "statutory_undefined") return T("tg.act.rate_undefined_sub", "ставку задаёт андеррайтер");
  if (frApplied(a) && r.applied_pct != null && Number(r.final_pct) !== Number(r.applied_pct)) {
    return T("tg.act.rate_with_fr", "с франшизой; без неё {r}", {r: pct(r.applied_pct, dp(r.applied_pct))})
      + (r.calibrated ? "" : "; " + T("tg.act.uncal", "поправка не калибрована"));
  }
  return r.calibrated ? "" : T("tg.act.uncal", "поправка не калибрована");
}
function actPremium(a){
  const p = a.premium || {};
  return p.amount == null || isNaN(Number(p.amount)) ? T("tg.act.na", "данные недоступны") : money(p.amount);
}
/* Word и PDF — на выбранном языке акта, а не интерфейса */
function actFile(kind){ return "/act/" + encodeURIComponent(CH.act.id) + "." + kind + "?lang=" + encodeURIComponent(actLang()); }
/* ---------- шаг 3: страховой скоринг объекта (01.10.2026) ----------
   Блок scoring ответа /act/make и GET /act/{id} (app/act_scoring.py, view): балл 0–500, класс, шкала, из чего
   сложился балл, объект, общий обзор, риски, сценарии, проверки, заёмщик. Тексты — из ответа (на языке акта: при смене
   языка акт приходит заново), подписи экрана — T(). Шкала — inline SVG по scale.bands и score тем же углом, что в PDF
   (act_scoring._angle: 0 — слева, 500 — справа, 250 — вверх). Шкала экспертная, не калибрована — это на карточке. */
const SCO_COLORS = {E: "#CC241F", D: "#ED6E1A", C: "#F7BD14", B: "#9EC730", A: "#459E33"};
const SCO_DARK = {C: true, B: true};                 // на жёлтом и светло-зелёном — тёмные буквы
const SCO_G = {w: 340, h: 184, cx: 170, cy: 160, rOut: 134, rRing: 114, rCol: 111, rIn: 62, rCore: 57,
  rLetter: 99, rTip: 88, rBase: 62, rTick: 151};
const scoS = v => v == null || typeof v === "object" ? "" : String(v);
const scoArr = v => Array.isArray(v) ? v : [];
const scoR = x => Math.round(x * 100) / 100;
/* значение шкалы 0–500 → угол (радианы): 0 слева (π), 500 справа (0); вне шкалы — к краю */
function scoAngle(v){ const x = Math.max(0, Math.min(500, Number(v) || 0)); return Math.PI * (1 - x / 500); }
function scoPt(r, a){ return [SCO_G.cx + r * Math.cos(a), SCO_G.cy - r * Math.sin(a)]; }
/* остриё стрелки: угол в градусах и точка — для проверки положения */
function scoNeedle(v){ const a = scoAngle(v), t = scoPt(SCO_G.rTip, a); return {deg: scoR(a * 180 / Math.PI), x: scoR(t[0]), y: scoR(t[1])}; }
/* сектор шкалы по баллу: from и to включительно (E 0–99, D 100–199 …) */
function scoBand(v, bands){ const x = Number(v); return scoArr(bands).filter(b => b && x >= Number(b.from) && x <= Number(b.to))[0] || null; }
/* кольцевой сектор между радиусами r0 < r1 от значения v0 до v1 */
function scoArc(r0, r1, v0, v1){
  const a0 = scoAngle(v0), a1 = scoAngle(v1), p = (r, a) => scoPt(r, a).map(scoR).join(" ");
  return "M" + p(r1, a0) + " A" + r1 + " " + r1 + " 0 0 1 " + p(r1, a1) + " L" + p(r0, a1) + " A" + r0 + " " + r0 + " 0 0 0 " + p(r0, a0) + " Z";
}
const scoTo = b => Number(b.to) < 500 ? Number(b.to) + 1 : 500;     // сектор рисуется до начала следующего
function scoGaugeSvg(sc){
  const G = SCO_G, bands = scoArr(sc.scale && sc.scale.bands).filter(b => b && SCO_COLORS[b.code] && isNum(b.from) && isNum(b.to));
  const score = Number(sc.score), has = sc.score != null && isFinite(score);
  let s = '<svg viewBox="0 0 ' + G.w + " " + G.h + '" role="img" aria-label="'
    + esc(T("tg.sc.gauge_aria", "Шкала от 0 до 500: балл {s}, класс {c}", {s: has ? score : "—", c: scoS(sc.class_code) || "—"})) + '">'
    + '<path class="sco-ring" d="' + scoArc(G.rRing, G.rOut, 0, 500) + '"/>';
  bands.forEach(b => { s += '<path class="sco-seg" fill="' + SCO_COLORS[b.code] + '" d="' + scoArc(G.rIn, G.rCol, b.from, scoTo(b)) + '"/>'; });
  s += '<path class="sco-core" d="M' + (G.cx - G.rCore) + " " + G.cy + " A" + G.rCore + " " + G.rCore + " 0 0 1 " + (G.cx + G.rCore) + " " + G.cy + ' Z"/>';
  bands.forEach(b => {
    const am = (scoAngle(b.from) + scoAngle(scoTo(b))) / 2;
    const l = scoPt(G.rLetter, am).map(scoR), w = scoPt((G.rRing + G.rOut) / 2, am).map(scoR);
    const lab = scoS(b.label);
    s += '<text class="sco-let' + (SCO_DARK[b.code] ? " dk" : "") + '" x="' + l[0] + '" y="' + l[1] + '">' + esc(b.code) + "</text>"
      + '<text class="sco-lvl" font-size="' + (lab.length > 9 ? 8 : 9.5) + '" transform="translate(' + w[0] + " " + w[1] + ") rotate("
      + scoR(90 - am * 180 / Math.PI) + ')">' + esc(lab) + "</text>";
  });
  for (let v = 0; v <= 500; v += 100) {
    if (v === 0 || v === 500) {
      s += '<text class="sco-tick" x="' + (G.cx + (v ? 1 : -1) * (G.rRing + G.rOut) / 2) + '" y="' + (G.cy + 15) + '">' + v + "</text>";
      continue;
    }
    const a = scoAngle(v), p0 = scoPt(G.rOut + 1, a).map(scoR), p1 = scoPt(G.rOut + 6, a).map(scoR), t = scoPt(G.rTick, a).map(scoR);
    s += '<line class="sco-tl" x1="' + p0[0] + '" y1="' + p0[1] + '" x2="' + p1[0] + '" y2="' + p1[1] + '"/>'
      + '<text class="sco-tick" x="' + t[0] + '" y="' + t[1] + '">' + v + "</text>";
  }
  s += '<line class="sco-base" x1="' + (G.cx - G.rOut) + '" y1="' + G.cy + '" x2="' + (G.cx + G.rOut) + '" y2="' + G.cy + '"/>';
  if (has) {
    const a = scoAngle(score), ux = Math.cos(a), uy = -Math.sin(a), h = 6.5;
    const bx = G.cx + ux * G.rBase, by = G.cy + uy * G.rBase, n = scoNeedle(score);
    const pts = [[bx - uy * h, by + ux * h], [n.x, n.y], [bx + uy * h, by - ux * h]].map(p => p.map(scoR).join(" "));
    s += '<g class="sco-needle" data-deg="' + n.deg + '"><path d="M' + pts[0] + " L" + pts[1] + " L" + pts[2] + ' Z"/>'
      + '<circle cx="' + scoR(bx) + '" cy="' + scoR(by) + '" r="' + h + '"/></g>'
      + '<text class="sco-num" x="' + G.cx + '" y="' + (G.cy - 13) + '">' + esc(String(Math.round(score))) + "</text>";
  }
  return s + "</svg>";
}
const scoBand_ = t => '<h3 class="sco-band">' + esc(t) + "</h3>";
/* «значение — подпись» (общий обзор, заёмщик): в две колонки, на телефоне — в одну */
function scoPairs(rows){
  return '<div class="sco-ov">' + scoArr(rows).filter(r => r && (scoS(r.value) || scoS(r.label)))
    .map(r => { const v = scoS(r.value) || T("tg.act.na", "данные недоступны");
      // число с единицей («2 945 000 000 сум», «95 %») не рвётся; текст («Франшиза не требуется») переносится
      return "<div><b" + (/^[\d\s  .,%\/+−-]+(\s\S{1,6})?$/.test(v) ? ' class="nw"' : "") + ">" + esc(v) + "</b><span>" + esc(scoS(r.label)) + "</span></div>"; }).join("") + "</div>";
}
function scoUrl(a, kind){
  const d = (a && a.downloads) || {}, s = (a && a.scoring && a.scoring.downloads) || {};
  const u = String((kind === "pdf" ? d.scoring_pdf || s.pdf : d.scoring_png || s.png) || "");
  return /^\/act\/[0-9a-f]{16}\/scoring\.(pdf|png)(\?lang=[a-z]{2})?$/.test(u) ? u : "";
}
function scoCardHtml(a){
  const sc = a && a.scoring;
  if (!sc || sc.available !== true) return "";
  const brand = /^#[0-9a-fA-F]{6}$/.test(scoS(sc.brand_color)) ? sc.brand_color : "#0B4F8A";
  const rq = sc.request || {}, sub = sc.subject || {};
  const code = scoS(sc.class_code), lab = scoS(sc.class_label), cls = SCO_COLORS[sc.class] ? sc.class : "";
  const r100 = isNum(sc.risk_score_100) ? nf(sc.risk_score_100, Number(sc.risk_score_100) % 1 ? 1 : 0) : "—";
  const kv = [[T("tg.sc.score", "Страховой балл"), scoS(sc.score) || "—"],
    [T("tg.sc.class", "Страховой класс"), code ? code + (lab ? ", " + lab : "") : "—"],
    [T("tg.sc.version", "Версия шкалы"), scoS(sc.version) || "—"],
    [sc.basis === "risk_score" ? T("tg.sc.risk100", "Балл риска 0–100") : T("tg.sc.by_level", "Оценка по уровню риска"), r100]];
  const comps = scoArr(sc.components).filter(c => c && scoS(c.label));
  const parts = scoArr(sc.parts).filter(p => p && scoS(p.text));
  const risks = scoArr(sc.risks).filter(r => r && (scoS(r.name) || scoS(r.share_text)));
  const scen = scoArr(sc.scenarios).filter(x => x && scoS(x.amount_text));
  const checks = scoArr(sc.checks).map(scoS).filter(Boolean);
  const bw = sc.borrower && scoArr(sc.borrower.rows).length ? sc.borrower : null;
  const rqRows = scoArr(rq.rows).filter(r => r && r.code !== "type" && scoS(r.value));
  const pdf = scoUrl(a, "pdf"), png = scoUrl(a, "png");
  // рекомендация и уровень риска акта рядом с плашкой; «отказать» — плашка перечёркнута красным
  const dec = sc.decision && typeof sc.decision === "object" ? sc.decision : {}, alv = sc.act_level && typeof sc.act_level === "object" ? sc.act_level : {};
  const dcode = ["accept", "accept_with_clauses", "decline"].indexOf(scoS(dec.code)) >= 0 ? dec.code : "";
  const no = dcode === "decline";
  const anl = sc.analytics_level && typeof sc.analytics_level === "object" ? scoS(sc.analytics_level.text) : "";
  const decHtml = scoS(dec.text) || scoS(alv.label) ? '<div class="sco-dec">'
    + (no ? '<p class="sco-x">' + esc(scoS(dec.warning) || T("tg.sc.dec_see", "см. рекомендацию акта: отказать")) + "</p>" : "")
    + (scoS(dec.text) ? "<p><span>" + esc(scoS(dec.title) || T("tg.sc.dec_title", "Рекомендация акта")) + ':</span> <b class="d-' + esc(dcode || "none") + '">' + esc(dec.text) + "</b></p>" : "")
    + (scoS(alv.label) ? "<p><span>" + esc(scoS(alv.title) || T("tg.sc.act_level", "Уровень риска акта")) + ":</span> <b>" + esc(alv.label) + "</b></p>" : "")
    + "</div>" : "";
  return '<section class="card sco" id="scoCard" style="--sco:' + esc(brand) + '">'
    + '<div class="sco-top"><h2>' + esc(T("tg.sc.title", "Страховой скоринг объекта")) + "</h2>"
    + '<span class="sco-unc">' + esc(T("tg.sc.uncal", "экспертная шкала, не калибрована")) + "</span></div>"
    + (rqRows.length ? '<dl class="sco-rq">' + rqRows.map(r => "<div><dt>" + esc(scoS(r.label)) + "</dt><dd>" + esc(scoS(r.value)) + "</dd></div>").join("") + "</dl>" : "")
    // скоринг: балл, класс, версия, балл риска; шкала; плашка класса цветом сектора
    + scoBand_(T("tg.sc.b_score", "Скоринг"))
    + '<div class="sco-sec"><div class="sco-main">'
    + '<div class="sco-kvw"><dl class="sco-kv">' + kv.map(x => "<div><dt>" + esc(x[0]) + "</dt><dd>" + esc(x[1]) + "</dd></div>").join("") + "</dl>"
    + (anl ? '<p class="sco-anl">' + esc(anl) + "</p>" : "")
    + (scoS(sc.formula) ? '<p class="sco-anl sco-fx">' + esc(sc.formula) + "</p>" : "") + "</div>"
    + '<div class="sco-gauge">' + scoGaugeSvg(sc)
    + (scoS(sc.gauge_caption) ? '<p class="sco-cap">' + esc(sc.gauge_caption) + "</p>" : "") + "</div>"
    + '<div class="sco-plq' + (SCO_DARK[cls] ? " dk" : "") + (no ? " x" : "") + '"' + (cls ? ' style="background:' + SCO_COLORS[cls] + '"' : "") + "><b>" + esc(code || "—") + "</b>"
    + (lab ? "<span>" + esc(lab) + "</span>" : "") + "</div>"
    + decHtml
    + "</div>"
    + (scoS(sc.text) ? '<p class="sco-text">' + esc(sc.text) + "</p>" : "")
    + (parts.length ? '<div class="sco-parts">' + (scoS(sc.parts_note) && scoS(sc.text).indexOf(scoS(sc.parts_note)) < 0 ? "<p><b>" + esc(sc.parts_note) + "</b></p>" : "")
      + "<ul>" + parts.map(p => '<li><i class="sco-dot" style="background:' + (SCO_COLORS[p.class] || "var(--line)") + '"></i>' + esc(p.text) + "</li>").join("") + "</ul></div>" : "")
    // из чего сложился балл: очки из максимума; как посчитано — по раскрытию
    + (comps.length ? '<div class="sco-comp"><p class="sco-h">' + esc(T("tg.sc.comp_title", "Из чего сложился балл")) + "</p>"
      + comps.map(c => {
        const on = c.applicable !== false && isNum(c.points) && isNum(c.max);
        const w = on && Number(c.max) > 0 ? Math.max(0, Math.min(100, Number(c.points) / Number(c.max) * 100)) : 0;
        return '<details class="sco-c' + (on ? "" : " off") + '"><summary><span>' + esc(c.label) + "</span><b>"
          + esc(on ? T("tg.sc.of", "{p} из {m}", {p: c.points, m: c.max}) : "—") + "</b>"
          + '<i class="sco-bar" aria-hidden="true"><u style="width:' + scoR(w) + '%"></u></i></summary>'
          + (scoS(c.why) ? "<p>" + esc(c.why) + "</p>" : "") + "</details>";
      }).join("") + "</div>" : "")
    + "</div>"
    // объект и общий обзор
    + (scoArr(sub.rows).length ? scoBand_(T("tg.sc.b_object", "Объект")) + '<div class="sco-sec"><dl class="sco-obj">'
      + scoArr(sub.rows).filter(r => r && scoS(r.label)).map(r => "<div><dt>" + esc(r.label) + "</dt><dd" + (r.code === "name" ? ' class="b"' : "") + ">"
        + esc(scoS(r.value) || T("tg.act.na", "данные недоступны")) + "</dd></div>").join("") + "</dl></div>" : "")
    + (scoArr(sc.overview).length ? scoBand_(T("tg.sc.b_overview", "Общий обзор")) + '<div class="sco-sec">' + scoPairs(sc.overview) + "</div>" : "")
    // риски и сценарии
    + scoBand_(T("tg.sc.b_risks", "Риски")) + '<div class="sco-sec">'
    + (risks.length ? '<table class="sco-t"><thead><tr><th>' + esc(T("tg.sc.col_risk", "Риск")) + '</th><th class="n w-s">' + esc(T("tg.sc.col_share", "Доля"))
      + '</th><th class="n w-l">' + esc(T("tg.sc.col_level", "Уровень")) + "</th></tr></thead><tbody>"
      + risks.map(r => "<tr><td>" + esc(scoS(r.name) || T("tg.act.na", "данные недоступны")) + '</td><td class="n num">' + esc(scoS(r.share_text)) + '</td><td class="n lv-'
        + esc(/^[a-z_]{1,20}$/.test(scoS(r.level)) ? r.level : "none") + '">' + esc(scoS(r.level_label)) + "</td></tr>").join("") + "</tbody></table>"
      : '<p class="note">' + esc(T("tg.sc.r_none", "Риски не разбиты — смотрите раздел 4 акта.")) + "</p>")
    + "</div>"
    + scoBand_(T("tg.sc.b_scen", "Сценарии убытка")) + '<div class="sco-sec">'
    + (scen.length ? '<table class="sco-t"><thead><tr><th>' + esc(T("tg.sc.col_scen", "Сценарий")) + '</th><th class="n w-a">' + esc(T("tg.sc.col_amount", "Сумма"))
      + '</th><th class="n w-p">' + esc(T("tg.sc.col_pct", "% суммы")) + "</th></tr></thead><tbody>"
      + scen.map(x => "<tr><td><b>" + esc(scoS(x.name)) + '</b></td><td class="n num">' + esc(x.amount_text) + '</td><td class="n num">' + esc(scoS(x.pct_text)) + "</td></tr>"
        + (scoS(x.what) || scoS(x.label) ? '<tr class="sub"><td colspan="3">' + esc(scoS(x.what) || scoS(x.label)) + "</td></tr>" : "")).join("") + "</tbody></table>"
      : '<p class="note">' + esc(T("tg.sc.sc_none", "Сценарии не посчитаны — смотрите раздел 4 акта.")) + "</p>")
    + "</div>"
    // заёмщик (кредитные продукты): отчёт кредитного бюро, в балл не входит
    + (bw ? scoBand_(T("tg.sc.b_borrower", "Заёмщик (кредитное бюро)")) + '<div class="sco-sec">' + scoPairs(bw.rows)
      + '<p class="note">' + esc(scoS(bw.note) || T("tg.sc.borrower_note", "Проверки заёмщика добавляют оговорки к рекомендации; в балл, уровень риска и ставку не входят. Данные кредитного отчёта хранятся в акте 7 дней; требуется согласие субъекта на получение кредитного отчёта (обязанность страховщика).")) + "</p></div>" : "")
    // что проверить андеррайтеру
    + scoBand_(T("tg.sc.b_checks", "Что проверить андеррайтеру")) + '<div class="sco-sec">'
    + (checks.length ? '<ul class="sco-chk">' + checks.map(c => "<li>" + esc(c) + "</li>").join("") + "</ul>"
      : '<p class="note">' + esc(T("tg.sc.checks_none", "Проверок нет.")) + "</p>")
    + (scoS(sc.checks_more) ? '<p class="note">' + esc(sc.checks_more) + "</p>" : "")
    + "</div>"
    + '<div class="sco-sec sco-end">'
    + (scoS(sc.footer_line) ? '<p class="sco-foot">' + esc(sc.footer_line) + "</p>" : "")
    + (scoS(sc.method_text) || scoS(sc.note) ? '<p class="sco-meth">' + esc([scoS(sc.method_text), scoS(sc.note)].filter(Boolean).join(" ")) + "</p>" : "")
    + (pdf || png ? '<div class="act-btns sco-btns">'
      + (IN_TG ? actSendBtnHtml("pdf") + (png ? '<button type="button" class="btn btn-secondary" data-go="scoimg">' + esc(scoImgLabel()) + "</button>" : "")
        : (pdf ? '<a class="btn btn-secondary" data-dl="scoring_pdf" href="' + esc(pdf) + '" download>' + esc(T("tg.sc.dl_pdf", "Скачать скоринг PDF")) + "</a>" : "")
          + (png ? '<a class="btn btn-secondary" data-dl="scoring_png" href="' + esc(png) + '" download>' + esc(T("tg.sc.dl_png", "Картинка шкалы")) + "</a>" : ""))
      + "</div>"
      + (IN_TG ? '<p class="hint">' + esc(T("tg.sc.tg_hint", "Скоринг — первая страница PDF акта: бот пришлёт его в чат. Картинку шкалы можно открыть здесь и сохранить долгим нажатием.")) + "</p>" : "")
      + '<div id="scoImg">' + scoImgHtml() + "</div>" : "")
    + '<div class="msg" id="scoMsg" role="status"></div>'
    + "</div></section>";
}
/* ---------- картинка шкалы в Telegram: скачивание из WebView не работает — открываем её здесь ----------
   GET /act/{id}/scoring.png с тем же входом, что у остальных запросов (заголовок Authorization), → blob → <img>. */
function scoImgOn(){ const a = CH.act, s = CH.scoImg; return !!(a && s && s.id === a.id && s.lang === a.lang && s.url); }
function scoImgLabel(){
  if (CH.scoImgBusy) return T("tg.sc.img_loading", "Открываю картинку…");
  return scoImgOn() && CH.scoImg.open ? T("tg.sc.img_hide", "Скрыть картинку шкалы") : T("tg.sc.img_open", "Открыть картинку шкалы");
}
function scoImgHtml(){
  if (CH.scoImgErr) return '<p class="note err">' + esc(CH.scoImgErr) + "</p>";
  if (!scoImgOn() || !CH.scoImg.open) return "";
  const cap = scoS(CH.act && CH.act.scoring && CH.act.scoring.gauge_caption);
  return '<figure class="sco-img"><img src="' + esc(CH.scoImg.url) + '" alt="' + esc(T("tg.sc.img_alt", "Шкала страхового скоринга с классом объекта")) + '">'
    + "<figcaption>" + (cap ? '<b class="sco-cap">' + esc(cap) + "</b> " : "") + esc(T("tg.sc.img_save", "Чтобы сохранить картинку, нажмите на неё и удерживайте.")) + "</figcaption></figure>";
}
function scoImgPaint(){
  const box = $("#scoImg");
  if (box) box.innerHTML = scoImgHtml();
  const b = document.querySelector('#wzBody [data-go="scoimg"]');
  if (b) { b.textContent = scoImgLabel(); b.disabled = !!CH.scoImgBusy; }
}
async function scoImg(){
  const a = CH.act, url = scoUrl(a, "png");
  if (!url || CH.scoImgBusy) return;
  CH.scoImgErr = "";
  if (scoImgOn()) { CH.scoImg.open = !CH.scoImg.open; haptic("select"); scoImgPaint(); return; }
  CH.scoImgBusy = true;
  scoImgPaint();
  try {
    const r = await fetch(url, {headers: TOKEN ? {Authorization: "Bearer " + TOKEN} : {}});
    if (!r.ok) throw new Error(why(r.status, null));
    const blob = await r.blob();
    if (CH.scoImg && CH.scoImg.url) URL.revokeObjectURL(CH.scoImg.url);
    CH.scoImg = {id: a.id, lang: a.lang, url: URL.createObjectURL(blob), open: true};
    haptic("success");
  } catch (e) {
    CH.scoImgErr = T("tg.sc.img_failed", "Картинка не открылась: {reason}. Попросите бота прислать PDF акта — скоринг на первой странице.",
      {reason: e && e.message && !/fetch/i.test(e.message) ? e.message : T("tg.err.offline", "сервер не отвечает")});
    haptic("error");
  }
  CH.scoImgBusy = false;
  scoImgPaint();
}
/* ---------- /скоринг ---------- */
function wzStep3Html(){
  const a = CH.act;
  if (!a) {
    return '<h2 class="wz-h">' + esc(T("tg.act.s3_title", "Сюрвейерский акт")) + "</h2>"
      + (CH.actBusy ? '<p class="note">' + spin(T("tg.act.loading", "Загружаю акт…")) + "</p>"
        : '<p class="note' + (CH.actErr ? " err" : "") + '">' + esc(CH.actErr || T("tg.act.no_act", "Акта ещё нет — заполните данные на шаге «Проверить».")) + "</p>");
  }
  const dec = (a.decision || {}).code || "", risk = a.risk || {}, val = a.value || {}, prem = a.premium || {};
  const tone = dec === "accept" ? "ok" : dec === "decline" ? "stop" : "warn";
  const lv = risk.level;
  const disc = (a.discrepancies || []).length;
  const ratio = val.ratio_pct == null || isNaN(Number(val.ratio_pct)) ? T("tg.act.na", "данные недоступны") : pct(val.ratio_pct, Number(val.ratio_pct) % 1 ? 2 : 0);
  const rateNote = actRateNote(a);
  // тип ставки продукта (rate.rate_type_label): годовая или фиксированная на весь срок; у фиксированной — годовой эквивалент
  const R = anObj(a.rate), rtl = anStr(R.rate_type_label), fixed = R.rate_type === "fixed";
  const rtNote = rtl ? (fixed ? T("tg.act.rate_type_fixed", "ставка {t}, на весь срок", {t: rtl}) : T("tg.act.rate_type", "ставка {t}", {t: rtl})) : "";
  const rtEquiv = fixed && isNum(R.annual_equiv_pct) ? T("tg.act.rate_equiv", "годовой эквивалент {p} — для сравнения с рынком",
    {p: pct(R.annual_equiv_pct, Math.max(2, dp(R.annual_equiv_pct)))}) : "";
  // итоговый вывод раздела 3 (сервер: value.final_verdict); в старых актах его нет — вывод по заявленной стоимости
  const fv = val.final_verdict || val.verdict;
  // многолетний договор: премия — на весь срок, тариф — годовой
  const multi = Number(prem.term_days) > 366;
  // комплексный продукт по частям: плитки договора, таблица и карточки частей (parts.mode = multi)
  const P = ptAct(a);
  // несколько объектов (парк ТС): плитки итогов договора и карточка объектов
  const O = obAct(a);
  // страховой скоринг объекта — первым; сводка ниже свёрнута под «Сводка акта» (карточка её не дублирует)
  const sco = scoCardHtml(a);
  // строка вилки «минимум – акт – рынок» — как отдаёт сервер (rate_fork.overview)
  const rfOv = anStr(anObj(a.rate_fork).overview);
  const btns = '<div class="act-btns">'
    + (IN_TG ? ["docx", "pdf"].map(actSendBtnHtml).join("")
      : '<a class="btn btn-secondary" data-dl="docx" href="' + esc(actFile("docx")) + '" download>' + esc(T("tg.act.dl_docx", "Скачать Word")) + "</a>"
        + '<a class="btn btn-secondary" data-dl="pdf" href="' + esc(actFile("pdf")) + '" download>' + esc(T("tg.act.dl_pdf", "Скачать PDF")) + "</a>")
    + '<button type="button" class="btn btn-secondary" data-go="copy">' + esc(T("tg.act.copy", "Скопировать текст акта")) + "</button>"
    + "</div>"
    + '<div class="msg" id="actMsg" role="status"></div>';
  return '<h2 class="wz-h">' + esc(T("tg.act.s3_title", "Сюрвейерский акт")) + "</h2>"
    + '<p class="wz-lead">' + esc(T("tg.act.lead", "№ {n} от {d}. Акт — совет ИИ-сюрвейера, решение принимает андеррайтер.", {n: a.number || "", d: a.date || ""})) + "</p>"
    + actLangHtml(a)
    + sco
    // вилка ставки — сразу после скоринга (без скоринга — первой), развёрнута
    + rfCardHtml(a)
    // ставка ниже минимальной: можно ли застраховать — сразу после вилки (только если оценка есть)
    + bmCardHtml(a)
    // факторы объекта по подгруппам класса — после вилки и оценки заниженной ставки
    + faCardHtml(a)
    + (sco ? '<details class="card act-sum act-sumd ' + tone + '" id="actSumD"' + (CH.sumOpen ? " open" : "") + "><summary><span><b>"
        + esc(T("tg.sc.sum_title", "Сводка акта")) + "</b><small>" + esc((P ? T("tg.sc.sum_sub_dec", "Рекомендация: {d}", {d: decName(dec)})
          : T("tg.sc.sum_sub", "Рекомендация: {d} · тариф {r} · премия {p}", {d: decName(dec), r: actRate(a), p: actPremium(a)}))
          + (rfOv ? " · " + T("tg.rf.sum_sub", "вилка {v}", {v: rfOv}) : "")) + "</small></span></summary>"
      : '<div class="card act-sum ' + tone + '">')
    + '<p class="eyebrow">' + esc(T("tg.act.dec_title", "Рекомендация")) + "</p>"
    + '<div class="act-dec">' + esc(decName(dec)) + "</div>"
    + (P ? ptSumHtml(a, P) : O ? obSumHtml(a) + actFrHtml(a) : '<div class="act-kpis">'
    + '<div><span>' + esc(T("tg.act.level", "Уровень риска")) + "</span>"
    + lvBarHtml(lv)
    + "<b class=\"lvname\">" + esc(levelName(lv)) + "</b>" + (risk.calibrated ? "" : "<em>" + esc(T("tg.act.uncal_level", "пороги не калиброваны")) + "</em>") + "</div>"
    + "<div><span>" + esc(T("tg.act.rate", "Рекомендуемый тариф")) + "</span><b>" + esc(actRate(a)) + "</b>"
    + (rateNote || multi || rtNote ? "<em>" + esc([rtNote || (multi ? T("tg.act.rate_annual", "годовых") : ""), rateNote].filter(Boolean).join("; ")) + "</em>" : "")
    + (rtEquiv ? "<em>" + esc(rtEquiv) + "</em>" : "") + "</div>"
    + "<div><span>" + esc(T("tg.act.premium", "Премия")) + "</span><b>" + esc(actPremium(a)) + "</b>"
    + (prem.term_days || frApplied(a) ? "<em>" + esc([frApplied(a) ? T("tg.act.prem_with_fr", "с франшизой") : "",
        prem.term_days ? T("tg.act.term", "за {n} дн.", {n: prem.term_days}) : "", multi ? T("tg.act.term_whole", "на весь срок") : ""].filter(Boolean).join(", ")) + "</em>" : "") + "</div>"
    + "<div><span>" + esc(T("tg.act.ratio", "Сумма к стоимости")) + "</span><b>" + esc(ratio) + "</b>"
    + (verdictName(fv) ? '<em class="' + (fv === "normal" ? "ok" : "warn") + '">' + esc(verdictName(fv)
      + (fv === "refine" && isNum(val.refined_ratio_pct) ? T("tg.act.v.refine_ratio", ": к уточнённой {p}", {p: pct(val.refined_ratio_pct, dp(val.refined_ratio_pct))}) : "")) + "</em>" : "") + "</div>"
    + "</div>"
    + actFrHtml(a))
    + (rfOv ? '<p class="act-line"><b>' + esc(T("tg.rf.title", "Вилка ставки")) + ":</b> " + esc(rfOv)
      + ' <span class="rf-ovl">' + esc(T("tg.rf.ov_label", "минимум – рекомендуемая – рынок")) + "</span></p>" : "")
    + actRegionHtml(a)
    + bmOvHtml(a)
    + actMvHtml(a)
    + actCheckHtml(a.request_check, "rq")
    + actCheckHtml(a.contract_check, "ct")
    + actXcHtml(a)
    + (disc ? '<p class="act-line warn"><b>' + esc(T("tg.act.disc_n", "Расхождения в данных: {n}", {n: disc})) + "</b> "
      + esc(T("tg.act.disc_where", "— выделены в акте, проверьте до выдачи полиса.")) + "</p>" : "")
    // со скорингом кнопки акта — под свёрнутой сводкой, чтобы были видны и без её раскрытия
    + (sco ? '</details><div class="act-btns-row">' + btns + "</div>" : btns + "</div>")
    // заёмщик по отчёту кредитного бюро (01.10.2026)
    + actCbHtml(a)
    // аналитика риска — после сводки, перед документом; сценарии с формулами и удержание — в ней,
    // поэтому отдельная карточка сценариев показывается только у актов без аналитики
    // парк ТС: таблица объектов и блоки по объектам — перед аналитикой договора
    + (O ? obActHtml(a) : "")
    + (P ? ptActHtml(a) : actAnHtml(a) + (anScenOk(a) ? "" : actScenHtml(a)))
    + actMeasuresHtml(a)
    + actAltHtml(a)
    + actTplDocsHtml(a)
    + actDocHtml(a)
    + '<div class="actions">'
    + '<button type="button" class="btn btn-secondary" data-go="fix">' + esc(T("tg.act.fix", "Исправить данные")) + "</button>"
    + '<button type="button" class="btn btn-secondary" data-go="restart">' + esc(T("tg.act.new", "Новый акт")) + "</button>"
    + '<button type="button" class="btn btn-secondary" data-go="legal">' + esc(T("tg.chat.ask_legal", "Спросить специалиста")) + "</button>"
    + "</div>";
}
const sgnMoney = x => (Number(x) < 0 ? "−" : "+") + money(Math.abs(Number(x)));
const isNum = x => x != null && x !== "" && !isNaN(Number(x));
/* франшиза в сводке: применена (было → стало), предложена (карточка с кнопкой «Применить»),
   не требуется, не применяется по обязательному виду. Цифры — из ответа сервера (franchise). */
function actFrHtml(a){
  const f = a.franchise || {};
  const st = f.status || (f.needed ? "proposed" : "none");
  const lbl = T("tg.act.franchise", "Франшиза");
  const size = f.size_pct > 0 ? pct(f.size_pct, dp(f.size_pct)) : "";
  const amt = f.size_amount > 0 ? money(f.size_amount) : "";
  const what = [frTypeName(f.type), size].filter(Boolean).join(" ") + (amt ? " — " + amt : "");
  const warn = f.warning ? '<p class="fr-warn">' + esc(f.warning) + "</p>" : "";
  const moved = isNum(f.premium_before) && isNum(f.premium_after) && Number(f.premium_after) !== Number(f.premium_before);
  if (st === "applied") {
    return '<div class="fr-box on"><p class="fr-h"><b>' + esc(T("tg.act.fr_applied", "Франшиза применена")) + ":</b> " + esc(what) + "</p>"
      + (moved
        ? '<p class="fr-move"><span>' + esc(T("tg.act.premium", "Премия")) + ":</span> <s>" + esc(money(f.premium_before)) + "</s> → <b>"
          + esc(money(f.premium_after)) + "</b> <em>" + esc(sgnMoney(f.delta != null ? f.delta : f.premium_after - f.premium_before)
          + (isNum(f.delta_pct) ? ", " + (Number(f.delta_pct) < 0 ? "−" : "+") + pct(Math.abs(f.delta_pct), dp(f.delta_pct)) : "")) + "</em></p>"
        : '<p class="fr-sub">' + esc(f.text || "") + "</p>")
      + warn + "</div>";
  }
  if (st === "proposed") {
    const can = f.size_pct > 0 && FR_TYPES.indexOf(f.type || "unconditional") >= 0;
    const grounds = (f.grounds || []).filter(g => g && g.text);
    return '<div class="fr-box prop"><p class="fr-h"><b>' + esc(T("tg.act.fr_advice", "Система советует франшизу")) + (what ? ":</b> " + esc(what) : "</b>") + "</p>"
      + '<p class="fr-sub">' + esc(moved && Number(f.premium_after) < Number(f.premium_before)
        ? T("tg.act.fr_prop_prem", "Премия с ней — {after} вместо {before}. Пока не применена: решает андеррайтер.", {after: money(f.premium_after), before: money(f.premium_before)})
        : T("tg.act.fr_prop_na", "Влияние на премию не посчитано — решает андеррайтер.")) + "</p>"
      + (grounds.length ? '<details class="mini"><summary>' + esc(T("tg.act.fr_grounds", "Основания · {n}", {n: grounds.length})) + "</summary><ul>"
        + grounds.map(g => "<li>" + esc(g.text) + "</li>").join("") + "</ul></details>" : "")
      + warn
      + (can ? '<button type="button" class="btn btn-secondary btn-sm" data-go="frapply">' + esc(T("tg.act.fr_apply", "Применить и пересобрать акт")) + "</button>" : "")
      + "</div>";
  }
  if (st === "statutory") {
    return '<p class="act-line"><b>' + esc(lbl) + ":</b> " + esc(T("tg.act.fr_statutory", "не применяется: обязательный вид страхования")) + "</p>" + warn;
  }
  return '<p class="act-line"><b>' + esc(lbl) + ":</b> " + esc(T("tg.act.fr_none", "не требуется")) + "</p>" + warn;
}
/* «Применить» у предложенной франшизы: блок франшизы на шаге 2 заполняется её видом и размером, акт пересобирается */
function actFrApply(){
  const f = (CH.act && CH.act.franchise) || {};
  if (!(f.size_pct > 0) || CH.busy) return;
  CH.fr = {on: true, type: FR_TYPES.indexOf(f.type) >= 0 ? f.type : "unconditional", unit: "pct",
    pct: nf(f.size_pct, dp(f.size_pct)).replace(/\s/g, ""), amount: null};
  CH.optOpen = true;
  CH.errs = {};
  CH.err = "";
  CH.wz = 2;
  wzSave();
  haptic("select");
  wzPaint(true);
  const box = $("#wzFr");
  if (box) { box.classList.add("flash"); if (box.scrollIntoView) box.scrollIntoView({block: "center"}); }
  actMake();
}
/* сценарии убытка: три плитки, лимит удержания, что принято по умолчанию */
function scName(k){
  switch (k) {
    case "pml": return T("tg.act.sc.pml", "вероятный максимальный убыток");
    case "eml": return T("tg.act.sc.eml", "оценочный максимальный убыток");
    default: return T("tg.act.sc.mfl", "максимально возможный убыток");
  }
}
function actScenHtml(a){
  const s = a.scenarios;
  if (!s || typeof s !== "object") return "";                   // акт до 29.09.2026 — блока нет
  return '<div class="card act-card"><h2>' + esc(T("tg.act.sc_title", "Сценарии убытка")) + "</h2>" + scenInner(s) + "</div>";
}
/* плитки PML / EML / MFL, удержание и принятое по умолчанию — для акта и для части договора */
function scenInner(s){
  if (!s.available) {
    return '<p class="note">PML / EML / MFL: ' + esc(anStr(s.note) || T("tg.act.na", "данные недоступны")) + "</p>";
  }
  // порядок плиток и подписи — из ответа сервера (scenarios.tiles); без него — прежний вид
  const list = Array.isArray(s.tiles) && s.tiles.length ? s.tiles
    : ["pml", "eml", "mfl"].filter(k => s[k]).map(k => Object.assign({code: k, name: k.toUpperCase(), label: ""}, s[k]));
  const tiles = list.map(x => {
    if (!x || typeof x !== "object") return "";
    const name = String(x.name || x.code || "").toUpperCase();
    const label = String(x.label || "").replace(/^\s*[A-Z]{3}\s*[—–-]\s*/, "") || scName(String(x.code || ""));
    const p = x.pct_text ? String(x.pct_text) : isNum(x.pct) ? pct(x.pct, dp(x.pct) ? 1 : 0) : "";
    return '<div class="sc-t"><p><b>' + esc(name) + "</b>" + esc(label) + "</p>"
      + "<strong>" + esc(money(x.amount)) + "</strong>"
      + (p ? "<span>" + esc(T("tg.act.sc_pct", "{p} страховой суммы", {p: p})) + "</span>" : "")
      + (x.what ? "<small>" + esc(x.what) + "</small>" : "") + "</div>";
  }).join("");
  const r = s.retention || {};
  const known = !!r.known && isNum(r.limit);
  const retNote = !known ? "" : r.status === "temporary" ? T("tg.act.ret_temp", "по временным данным о собственных средствах — не отчётность")
    : r.status === "reported" ? T("tg.act.ret_reported", "по отчётности") : "";
  // с чем сравнено удержание (EML) и справка по MFL — текстом сервера; старый акт — прежняя строка
  const excess = !known ? "" : r.text ? String(r.text)
    : Number(r.mfl_excess) > 0 ? T("tg.act.ret_excess", "MFL выше лимита на {x}", {x: money(r.mfl_excess)}) : "";
  const warnRet = r.status === "temporary" || Number(r.eml_excess) > 0 || (!r.text && Number(r.mfl_excess) > 0);
  const asm = (s.assumptions || []).filter(x => x && x.text);
  return '<div class="sc-tiles">' + tiles + "</div>"
    + '<div class="sc-ret"><span>' + esc(T("tg.act.ret_title", "Лимит собственного удержания")) + "</span><b>"
    + esc(known ? money(r.limit) : T("tg.act.ret_unknown", "не задан")) + "</b>"
    + (retNote || excess ? '<small class="' + (warnRet ? "warn" : "") + '">' + esc([retNote, excess].filter(Boolean).join("; ")) + "</small>" : "")
    + "</div>"
    + (s.definitions ? '<p class="note">' + esc(String(s.definitions)) + "</p>" : "")
    + '<p class="note">' + esc(T("tg.act.sc_uncal", "Экспертная оценка: коэффициенты не калиброваны по убыткам компании.")) + "</p>"
    + (asm.length ? '<details class="mini"><summary>' + esc(T("tg.act.sc_assumed", "Принято по умолчанию · {n}", {n: asm.length})) + "</summary><ul>"
      + asm.map(x => "<li>" + esc(x.text) + "</li>").join("") + "</ul></details>" : "");
}
/* рекомендации страхователю: что сделать, зачем, срок, влияние на премию; обязательные отмечены */
function actMeasuresHtml(a){
  const ms = a.measures;
  if (!Array.isArray(ms)) return "";
  const sum = a.measures_summary || {};
  const title = '<h2>' + esc(T("tg.act.ms_title", "Рекомендации страхователю")) + "</h2>";
  if (!ms.length) return '<div class="card act-card">' + title + '<p class="note">' + esc(T("tg.act.ms_none", "Дополнительных мероприятий не требуется.")) + "</p></div>";
  const must = ms.filter(m => m.mandatory).length;
  return '<div class="card act-card">' + title
    + '<p class="sub">' + esc(T("tg.act.ms_sub", "Мероприятий: {n}, из них обязательных: {m}.", {n: ms.length, m: must})) + "</p>"
    + '<ol class="ms">' + ms.map(m => '<li' + (m.mandatory ? ' class="must"' : "") + '><p class="ms-t">' + esc(m.text || "")
      + (m.mandatory ? '<span class="tag must">' + esc(T("tg.act.ms_mandatory", "обязательно")) + "</span>" : "") + "</p>"
      + (m.why ? '<p class="ms-why">' + esc(m.why) + "</p>" : "")
      + '<p class="ms-meta">' + (m.deadline_days ? "<span>" + esc(T("tg.act.ms_deadline", "Срок: {n} дн.", {n: m.deadline_days})) + "</span>" : "")
      + (m.effect_text ? '<span class="' + (Number(m.premium_delta) < 0 ? "ok" : "") + '">' + esc(m.effect_text) + "</span>" : "") + "</p></li>").join("")
    + "</ol>"
    + (sum.text ? '<p class="ms-sum">' + esc(sum.text) + "</p>" : "")
    + "</div>";
}
/* «Вместо франшизы можно»: мероприятия, исключение риска, сумма к стоимости — тексты сервера с эффектом */
function actAltHtml(a){
  const alts = ((a.franchise || {}).alternatives || []).filter(x => x && x.text);
  if (!alts.length) return "";
  return '<div class="card act-card"><h2>' + esc(T("tg.act.alt_title", "Вместо франшизы можно")) + "</h2>"
    + '<ul class="alt">' + alts.map(x => "<li>" + esc(x.text) + "</li>").join("") + "</ul></div>";
}
/* ---------- шаг 3: вилка ставки (01.10.2026) ----------
   Заказчик: «система выдаёт вилку ставки с объяснением, из чего она сложилась». Блок rate_fork ответа /act/make и
   GET /act/{id} (app/act.py → _fork_view, числа — act_engine, раздел 12): отметки min / act / adjusted / market,
   ставки документов request / contract, справочная technical. Здесь ничего не пересчитывается, кроме промежуточной
   ставки «после поправки региона» — той же формулой, что act_engine.apply_fork: round(акт × (1 + регион / 100), 4).
   Шкала — HTML с положением в процентах: 0 … max(отметки без технической) × 1,1; техническая за краем — стрелкой.
   Подписи раскладываются по рядам заранее для двух ширин (телефон ~266 px, шире 600 px ~476 px), чтобы не наезжали. */
const RF_CODES = ["min", "act", "adjusted", "market", "request", "contract", "technical", "factors"];
const RF_MAIN = ["min", "act", "adjusted", "market"];
const RF_W = {n: 266, w: 476, x: 620};    // ширина шкалы, по которой раскладываются подписи: телефон, от 600 и от 1000 px
const rfR4 = x => Math.round(Number(x) * 10000) / 10000;
const rfFmt = v => isNum(v) ? pct(v, Math.max(2, dp(rfR4(v)))) : "—";
const rfHttps = u => /^https:\/\/[^\s"<>]+$/i.test(anStr(u)) ? anStr(u) : "";
function rfShort(code){
  switch (code) {
    case "min": return T("tg.rf.s_min", "минимум");
    case "act": return T("tg.rf.s_act", "акт");
    case "adjusted": return T("tg.rf.s_adjusted", "регион и рынок");
    case "market": return T("tg.rf.s_market", "рынок");
    case "request": return T("tg.rf.s_request", "запрос филиала");
    case "contract": return T("tg.rf.s_contract", "договор");
    case "factors": return T("tg.rf.s_factors", "с факторами объекта");
    default: return T("tg.rf.s_technical", "техническая");
  }
}
/* отметки ответа: известный код и число ставки; остальное на экран не идёт */
function rfMarks(F){
  return anArr(anObj(F).marks).filter(m => m && RF_CODES.indexOf(m.code) >= 0 && isNum(m.rate_pct) && Number(m.rate_pct) >= 0);
}
/* отметки с одной ставкой — одна подпись («минимум = акт») */
function rfGroups(list, x){
  const out = [];
  list.slice().sort((a, b) => Number(a.rate_pct) - Number(b.rate_pct)).forEach(m => {
    const g = out.find(o => Math.abs(o.rate - Number(m.rate_pct)) < 1e-9);
    if (g) g.marks.push(m); else out.push({rate: Number(m.rate_pct), x: x(Number(m.rate_pct)), marks: [m]});
  });
  return out;
}
/* ширина подписи в px (оценка с запасом): число моноширинным 13 px, название 11,5 px, «рекомендуем» плашкой */
function rfLabelPx(g){
  return Math.max(g.val.length * 8, g.name.length * 6.3, g.rec ? 80 : 0) + 8;
}
/* ряды подписей: слева направо, в первый ряд без наложения (зазор 2 %); край шкалы — подпись прижата к краю */
function rfRows(groups, width){
  const rows = [];
  return groups.map(g => {
    const w = Math.min(100, rfLabelPx(g) / width * 100);
    // варианты: по центру отметки, кончается на отметке, начинается от отметки (у края — прижата к краю)
    const fit = (l, ta) => l < 0 ? [0, "left"] : l + w > 100 ? [100 - w, "right"] : [l, ta];
    const cand = [fit(g.x - w / 2, "center"), fit(g.x - w, "right"), fit(g.x, "left")];
    const free = (row, l) => !row.some(s => l < s[1] + 2 && s[0] < l + w + 2);
    let r = 0, pick = null;
    for (; !pick; r++) {
      const row = rows[r] || [];
      pick = cand.find(c => free(row, c[0])) || null;
      if (pick) { (rows[r] = row).push([pick[0], pick[0] + w]); break; }
    }
    return {l: rfR4(pick[0]), w: rfR4(w), r: r, ta: pick[1]};
  }).concat([{rows: rows.length}]);
}
/* раскладка шкалы: положение каждой отметки в % и ряды подписей для двух ширин */
function rfLayout(F){
  const ms = rfMarks(F);
  const onScale = ms.filter(m => m.code !== "technical");
  const top = Math.max.apply(null, onScale.map(m => Number(m.rate_pct)).concat([0]));
  const max = top > 0 ? top * 1.1 : 0;
  if (!(max > 0)) return null;
  const x = v => rfR4(Math.max(0, Math.min(100, v / max * 100)));
  const tech = ms.filter(m => m.code === "technical")[0] || null;
  const techOver = !!tech && Number(tech.rate_pct) > max;
  const upper = rfGroups(ms.filter(m => RF_MAIN.indexOf(m.code) >= 0), x);
  const lower = rfGroups(ms.filter(m => m.code === "request" || m.code === "contract" || m.code === "factors" || (m.code === "technical" && !techOver)), x);
  if (techOver) lower.push({rate: Number(tech.rate_pct), x: 100, marks: [tech], over: true});
  [upper, lower].forEach(gs => gs.forEach(g => {
    g.rec = g.marks.some(m => m.is_recommended === true);
    const doc = g.marks.filter(m => m.code === "request" || m.code === "contract")[0];
    g.pos = doc ? rfDocPos(F, doc.code) : g.marks.some(m => m.code === "factors") ? "factors" : "";
    g.val = (g.over ? "→ " : "") + rfFmt(g.rate);
    g.name = g.marks.map(m => rfShort(m.code)).join(" = ") + (g.marks.some(m => m.code === "technical") ? ", " + T("tg.rf.ref", "справочно") : "");
  }));
  const lay = (gs, k) => { const r = rfRows(gs, RF_W[k]); const n = r.pop().rows; gs.forEach((g, i) => { g[k === "x" ? "x2" : k] = r[i]; }); return n; };
  const rows = {un: lay(upper, "n"), uw: lay(upper, "w"), ux: lay(upper, "x"), ln: lay(lower, "n"), lw: lay(lower, "w"), lx: lay(lower, "x")};
  const by = {};
  ms.forEach(m => { by[m.code] = m; });
  return {max: max, x: x, upper: upper, lower: lower, rows: rows, by: by, tech: tech, techOver: techOver, marks: ms};
}
function rfDocPos(F, code){
  const p = anStr(anObj(anObj(F).position)[code]);
  return ["below_min", "inside", "above_market"].indexOf(p) >= 0 ? p : "inside";
}
function rfLabHtml(g, side){
  const st = "--x:" + g.x + "%;--nl:" + g.n.l + "%;--nw:" + g.n.w + "%;--nr:" + g.n.r + ";--nta:" + g.n.ta
    + ";--wl:" + g.w.l + "%;--ww:" + g.w.w + "%;--wr:" + g.w.r + ";--wta:" + g.w.ta
    + ";--xl:" + g.x2.l + "%;--xw:" + g.x2.w + "%;--xr:" + g.x2.r + ";--xta:" + g.x2.ta;
  const codes = g.marks.map(m => m.code).join(" ");
  return '<i class="rf-lead' + (g.over ? " over" : "") + '" style="' + st + '" aria-hidden="true"></i>'
    + '<span class="rf-lab' + (g.rec ? " best" : "") + (g.pos ? " pos-" + g.pos : "") + (g.over ? " over" : "") + '" data-codes="' + esc(codes) + '" data-x="' + g.x + '" data-nr="' + g.n.r + '" data-wr="' + g.w.r
    + '" data-nl="' + g.n.l + '" data-nw="' + g.n.w + '" data-wl="' + g.w.l + '" data-ww="' + g.w.w + '" style="' + st + '">'
    + (side === "u" && g.rec ? '<em class="rf-chip">' + esc(T("tg.rf.rec", "рекомендуем")) + "</em><br>" : "")
    + "<b>" + esc(g.val) + "</b><br><small>" + esc(g.name) + "</small></span>";
}
/* шкала: штриховка ниже минимума, коридор «минимум — рынок», отметки сверху, документы и техническая — снизу */
function rfScaleHtml(F, L){
  if (!L) return "";
  const by = L.by, xm = by.min ? L.x(Number(by.min.rate_pct)) : 0;
  const xk = by.market ? L.x(Number(by.market.rate_pct)) : null;
  let tr = (xm > 0 ? '<i class="rf-zone" style="width:' + xm + '%"></i>' : "")
    + (xk != null && xk > xm ? '<i class="rf-corr" style="left:' + xm + "%;width:" + rfR4(xk - xm) + '%"></i>' : "");
  L.upper.forEach(g => { tr += '<i class="rf-tk' + (g.rec ? " best" : "") + '" data-codes="' + esc(g.marks.map(m => m.code).join(" ")) + '" style="left:' + g.x + '%"></i>'; });
  L.lower.forEach(g => {
    const doc = g.marks.filter(m => m.code === "request" || m.code === "contract")[0];
    if (g.over) tr += '<i class="rf-over" aria-hidden="true"></i>';
    else if (doc || g.pos === "factors") tr += '<i class="rf-doc pos-' + g.pos + '" data-codes="' + esc(g.marks.map(m => m.code).join(" ")) + '" style="left:' + g.x + '%"></i>';
    if (!g.over && g.marks.some(m => m.code === "technical")) tr += '<i class="rf-tech" style="left:' + g.x + '%"></i>';
  });
  const aria = T("tg.rf.aria", "Шкала ставок от 0 до {max}: {list}", {max: rfFmt(L.max),
    list: L.marks.map(m => (anStr(m.label) || rfShort(m.code)) + " " + rfFmt(m.rate_pct)).join("; ")});
  return '<div class="rf-scale" role="img" aria-label="' + esc(aria) + '" data-max="' + rfR4(L.max) + '" style="--un:' + L.rows.un + ";--uw:" + L.rows.uw + ";--ux:" + L.rows.ux
    + ";--ln:" + L.rows.ln + ";--lw:" + L.rows.lw + ";--lx:" + L.rows.lx + '">'
    + '<div class="rf-up">' + L.upper.map(g => rfLabHtml(g, "u")).join("") + "</div>"
    + '<div class="rf-track">' + tr + "</div>"
    + '<div class="rf-lo">' + L.lower.map(g => rfLabHtml(g, "l")).join("") + "</div>"
    + "</div>"
    + '<ul class="rf-leg">'
    + (xm > 0 ? '<li><i class="sw-zone"></i>' + esc(T("tg.rf.lg_zone", "ниже минимума — только с отступлением")) + "</li>" : "")
    + (xk != null && xk > xm ? '<li><i class="sw-corr"></i>' + esc(T("tg.rf.lg_corr", "от минимума до рынка")) + "</li>" : "")
    + (L.upper.some(g => g.rec) ? '<li><i class="sw-rec"></i>' + esc(T("tg.rf.lg_rec", "рекомендуемая ставка")) + "</li>" : "")
    + (L.lower.some(g => g.marks.some(m => m.code === "request" || m.code === "contract")) ? '<li><i class="sw-doc"></i>' + esc(T("tg.rf.lg_doc", "ставка из документа")) + "</li>" : "")
    + (L.lower.some(g => g.marks.some(m => m.code === "factors")) ? '<li><i class="sw-fac"></i>' + esc(T("tg.rf.lg_fac", "с факторами объекта")) + "</li>" : "")
    + (L.tech ? '<li><i class="sw-tech"></i>' + esc(T("tg.rf.lg_tech", "техническая ставка — справочно, в премию не идёт")) + "</li>" : "")
    + "</ul>";
}
/* источник: название и ссылка (только https) */
function rfSrcCell(s){
  const o = anObj(s), t = anStr(o.title), u = rfHttps(o.url);
  if (!t && !u) return "—";
  return (u ? '<a href="' + esc(u) + '" target="_blank" rel="noopener">' + esc(t || anDomain(u)) + "</a>" : esc(t));
}
/* плашка источника под внешними данными (правило проекта: .srcbar) */
function rfSrcbar(list){
  const seen = {}, L = anArr(list).map(anObj).filter(s => anStr(s.title) || rfHttps(s.url)).filter(s => {
    const k = anStr(s.title) + "|" + anStr(s.url); if (seen[k]) return false; seen[k] = 1; return true; });
  if (!L.length) return "";
  return '<div class="srcbar act-src rf-src">' + L.map(s => {
    const as = anStr(s.as_of), d = /^\d{4}-\d{2}-\d{2}/.test(as) ? dateOnly(as) : as;
    return "<span>" + esc([anStr(s.title), d && anStr(s.title).indexOf(d) < 0 ? d : ""].filter(Boolean).join(", "))
      + (rfHttps(s.url) ? " " + anLink(rfHttps(s.url)) : "") + "</span>";
  }).join("") + "</div>";
}
function rfTableHtml(F, ms){
  const unit = anStr(F.unit) || T("tg.rf.unit", "% годовых");
  const cols = [T("tg.rf.col_mark", "Отметка"), T("tg.rf.col_rate", "Ставка, {u}", {u: unit}), T("tg.rf.col_prem", "Премия на весь срок"),
    T("tg.rf.col_src", "Источник"), T("tg.rf.col_note", "Примечание")];
  return '<table class="an-t fold rf-t"><thead><tr>' + cols.map((c, i) => "<th" + (i === 1 || i === 2 ? ' class="n"' : "") + ">" + esc(c) + "</th>").join("")
    + "</tr></thead><tbody>"
    + ms.map(m => {
      const doc = m.code === "request" || m.code === "contract";
      const cls = [m.is_recommended === true ? "rf-rec" : "", doc ? "rf-docrow pos-" + rfDocPos(F, m.code) : "", m.code === "technical" ? "rf-techrow" : ""].filter(Boolean).join(" ");
      return '<tr data-code="' + esc(m.code) + '"' + (cls ? ' class="' + cls + '"' : "") + ">"
        + '<td class="h" data-l="' + esc(cols[0]) + '">' + esc(anStr(m.label) || rfShort(m.code))
        + (m.is_recommended === true ? ' <span class="tag rf-tag">' + esc(T("tg.rf.rec", "рекомендуем")) + "</span>" : "") + "</td>"
        + '<td class="n" data-l="' + esc(cols[1]) + '"><b>' + esc(rfFmt(m.rate_pct)) + "</b></td>"
        + '<td class="n" data-l="' + esc(cols[2]) + '">' + esc(isNum(m.premium) ? money(m.premium) : T("tg.act.na", "данные недоступны")) + "</td>"
        + '<td data-l="' + esc(cols[3]) + '">' + rfSrcCell(m.source) + "</td>"
        + '<td data-l="' + esc(cols[4]) + '">' + esc(anStr(m.note) || "—") + "</td></tr>";
    }).join("") + "</tbody></table>";
}
/* «Из чего сложилась»: ставка тарифной политики → уровень риска → регион → рынок → итог */
function rfStepsHtml(F, rate, by){
  const adj = anObj(F.adjustments), R = anObj(adj.region), M = anObj(adj.market), r = anObj(rate);
  const act = by.act, ad = by.adjusted;
  if (!act) return "";
  const step = (cls, name, chg, res, body) => '<li class="rf-st ' + cls + '"><div class="rf-sh"><span>' + esc(name) + "</span>"
    + (chg ? "<em>" + esc(chg) + "</em>" : "") + (res ? "<b>" + esc(res) + "</b>" : "") + "</div>" + (body || "") + "</li>";
  let h = "";
  if (r.mode === "tariff" && isNum(r.base_pct)) {
    h += step("pol", T("tg.rf.st_policy", "Ставка тарифной политики"), "", rfFmt(r.base_pct), "");
    h += step("lvl", T("tg.rf.st_level", "Поправка по уровню риска"), isNum(r.adj_pct) ? sgnPct(r.adj_pct, Math.min(2, dp(r.adj_pct))) || "0" : "",
      "= " + rfFmt(act.rate_pct),
      '<p class="rf-sn">' + esc(T("tg.rf.st_act", "Ставка акта")
        + (r.min_applied ? " — " + T("tg.rf.st_min", "поднята до минимума {m}", {m: rfFmt(r.min_pct)}) : "")) + "</p>");
  } else {
    h += step("lvl", T("tg.rf.st_act", "Ставка акта"), "", rfFmt(act.rate_pct), "");
  }
  // регион: показатели региона против республики; неучтённые — приглушённо с причиной
  if (isNum(R.pct)) {
    const after = rfR4(Number(act.rate_pct) * (1 + Number(R.pct) / 100));
    const inds = anArr(R.indicators).filter(i => i && anStr(i.name));
    const used = inds.filter(i => i.used === true), off = inds.filter(i => i.used !== true);
    const num = v => isNum(v) ? nf(v, Math.min(4, dp(v))) : "—";
    const unit = i => CH.act && CH.act.lang === "ru" && anStr(i.unit) ? " " + anStr(i.unit) : "";
    const tb = used.length ? '<table class="an-t fold rf-ind"><thead><tr><th>' + esc(T("tg.rf.ind_name", "Показатель")) + '</th><th class="n">'
      + esc(T("tg.rf.ind_reg", "Регион")) + '</th><th class="n">' + esc(T("tg.rf.ind_cty", "Республика")) + '</th><th class="n">'
      + esc(T("tg.rf.ind_ratio", "Отношение")) + '</th><th class="n">' + esc(T("tg.rf.ind_eff", "Вклад")) + "</th></tr></thead><tbody>"
      + used.map(i => '<tr><td class="h" data-l="">' + esc(i.name) + (anStr(i.period) ? " <small>" + esc(i.period) + "</small>" : "")
        + (rfHttps(anObj(i.source).url) ? " <small>" + anLink(rfHttps(i.source.url)) + "</small>" : "") + "</td>"
        + '<td class="n" data-l="' + esc(T("tg.rf.ind_reg", "Регион")) + '">' + esc(num(i.region_value) + unit(i)) + "</td>"
        + '<td class="n" data-l="' + esc(T("tg.rf.ind_cty", "Республика")) + '">' + esc(num(i.country_value) + unit(i)) + "</td>"
        + '<td class="n" data-l="' + esc(T("tg.rf.ind_ratio", "Отношение")) + '">' + esc(isNum(i.ratio) ? "×" + nf(i.ratio, Math.min(3, dp(i.ratio))) : "—") + "</td>"
        + '<td class="n" data-l="' + esc(T("tg.rf.ind_eff", "Вклад")) + '">' + esc(isNum(i.effect_pct) ? sgnPct(i.effect_pct, Math.min(2, dp(i.effect_pct))) : "—") + "</td></tr>").join("")
      + "</tbody></table>" : "";
    const offH = off.length ? '<ul class="rf-off">' + off.map(i => "<li>" + esc(T("tg.rf.ind_off", "{name}: не учтён — {why}", {name: i.name,
      why: anStr(i.why_text) || T("tg.act.na", "данные недоступны")})) + "</li>").join("") + "</ul>" : "";
    h += step("reg", T("tg.rf.st_region", "Поправка региона"), sgnPct(R.pct, Math.min(2, dp(R.pct))) || "0", "→ " + rfFmt(after),
      (anStr(R.text) ? '<p class="rf-sn">' + esc(R.text) + "</p>" : "") + tb + offH + rfSrcbar(used.map(i => i.source)));
  }
  if (isNum(M.pct)) {
    const facts = [[T("tg.rf.mk_lr", "Убыточность класса"), isNum(M.loss_ratio_pct) ? pct(M.loss_ratio_pct, 1) : "—"],
      [T("tg.rf.mk_rate", "Рыночная ставка"), rfFmt(M.market_rate_pct)],
      [T("tg.rf.mk_asof", "Срез НАПП"), anStr(M.as_of) ? dateOnly(M.as_of) : "—"]];
    h += step("mkt", T("tg.rf.st_market", "Поправка рынка"), sgnPct(M.pct, Math.min(2, dp(M.pct))) || "0", ad ? "→ " + rfFmt(ad.rate_pct) : "",
      '<dl class="rf-facts">' + facts.map(f => "<div><dt>" + esc(f[0]) + "</dt><dd>" + esc(f[1]) + "</dd></div>").join("") + "</dl>"
      + (anStr(M.text) ? '<p class="rf-sn">' + esc(M.text) + "</p>" : "") + rfSrcbar([M.source]));
  }
  if (ad) {
    h += step("tot", T("tg.rf.st_total", "Итог: с учётом региона и рынка"), "", rfFmt(ad.rate_pct),
      '<p class="rf-sn">' + esc([isNum(ad.premium) ? T("tg.rf.st_prem", "премия {p}", {p: money(ad.premium)}) : "", anStr(ad.note)].filter(Boolean).join(" · ")) + "</p>");
  }
  return '<h3 class="rf-h">' + esc(T("tg.rf.h_how", "Из чего сложилась")) + '</h3><ol class="rf-steps">' + h + "</ol>";
}
function rfModeHtml(F){
  const ap = F.mode === "apply";
  return '<p class="rf-mode' + (ap ? " ap" : "") + '">' + esc(ap ? T("tg.rf.mode_apply", "Премия акта посчитана по ставке с учётом региона и рынка.")
    : T("tg.rf.mode_reference", "Премия акта посчитана по ставке акта. Ставка с учётом региона и рынка показана справочно.")) + "</p>";
}
function rfHowHtml(F, key){
  // служебное имя настройки в скобках («(настройка rate_fork)») на экран не выводится
  const how = anArr(F.how).map(anStr).map(x => x.replace(/\s*\([^()]*\brate_fork\b[^()]*\)/g, "")).filter(Boolean);
  if (!how.length) return "";
  const open = !!(CH.rfOpen && CH.rfOpen[key]);
  return '<details class="mini rf-howd" data-rfhow="' + esc(key) + '"' + (open ? " open" : "") + "><summary>" + esc(T("tg.rf.how", "Как посчитано")) + "</summary><ul>"
    + how.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul></details>";
}
const rfUncal = () => '<span class="tag off rf-unc">' + esc(T("tg.rf.uncal", "поправки экспертные, не калиброваны")) + "</span>";
/* карточка «Вилка ставки» шага «Акт»: развёрнута, сразу после скоринга */
function rfCardHtml(a){
  if (!a) return "";
  const F = anObj(a.rate_fork), P = ptAct(a);
  // пометка о некалиброванных поправках — только там, где поправки есть (у обязательного вида их нет)
  const head = '<div class="rf-top"><h2>' + esc(anStr(F.title) || T("tg.rf.title", "Вилка ставки")) + "</h2>" + (F.available === true ? rfUncal() : "") + "</div>";
  if (!a.rate_fork || F.reason === "old_act") {
    return '<section class="card act-card rf" id="rfCard">' + head + '<p class="note">' + esc(T("tg.rf.old", "Для этого акта вилка ставки не посчитана — сформируйте акт заново.")) + "</p></section>";
  }
  const ms = rfMarks(F);
  if (F.available !== true) {
    // обязательный вид или ставка не определена: текст сервера и рыночный ориентир, если он есть
    const mk = ms.filter(m => m.code === "market")[0], one = ms.filter(m => m.code === "act")[0];
    return '<section class="card act-card rf rf-na" id="rfCard">' + head
      + '<p class="rf-sum">' + esc(anStr(F.summary) || T("tg.act.na", "данные недоступны")) + "</p>"
      + (one ? '<p class="act-line"><b>' + esc(anStr(one.label) || rfShort("act")) + ":</b> " + esc(rfFmt(one.rate_pct))
        + (isNum(one.premium) ? " · " + esc(T("tg.rf.st_prem", "премия {p}", {p: money(one.premium)})) : "")
        + (anStr(anObj(one.source).title) ? " · " + esc(one.source.title) : "") + "</p>" : "")
      + (mk ? '<p class="act-line"><b>' + esc(T("tg.rf.na_market", "Рыночный ориентир")) + ":</b> " + esc(rfFmt(mk.rate_pct))
        + (anStr(mk.note) ? " — " + esc(mk.note) : "") + "</p>" + rfSrcbar([mk.source]) : "")
      + "</section>";
  }
  const L = rfLayout(F);
  const by = L ? L.by : {};
  const parts = P ? anArr(F.parts).filter(p => p && anStr(p.summary)) : [];
  return '<section class="card act-card rf" id="rfCard" aria-label="' + esc(anStr(F.title) || T("tg.rf.title", "Вилка ставки")) + '">' + head
    + '<p class="rf-sum">' + esc(anStr(F.summary)) + "</p>"
    + rfScaleHtml(F, L)
    + rfMinSrcHtml(a, by)
    + (ms.length ? rfTableHtml(F, ms) : "")
    + (P ? (parts.length ? '<h3 class="rf-h">' + esc(T("tg.rf.parts_h", "Вилки частей")) + '</h3><ul class="rf-parts">'
        + parts.map(p => "<li><b>" + esc(T("tg.rf.part_n", "Часть {n}, класс {c}", {n: anStr(p.index), c: anStr(p.class_code)})) + ":</b> " + esc(p.summary) + "</li>").join("") + "</ul>" : "")
        + '<p class="rf-sn">' + esc(T("tg.rf.parts_note", "Вилка договора справочная: минимум и поправки проверены по каждой части отдельно — шкалы частей в карточках частей ниже.")) + "</p>"
      : rfStepsHtml(F, a.rate, by))
    + rfModeHtml(F)
    + rfHowHtml(F, "act")
    + "</section>";
}
/* источник минимальной ставки у отметки «минимальная»: min_rate.source_text (иначе rate.min_source) и тип ставки —
   «минимальная ставка страховщика (установлена администратором …)» или «из тарифной политики, приказ 54-П» */
function rfMinSrcHtml(a, by){
  const mr = anObj(a.min_rate), r = anObj(a.rate);
  const src = anStr(mr.source_text) || anStr(r.min_source);
  const v = isNum(mr.min_pct) ? mr.min_pct : by && by.min ? by.min.rate_pct : r.min_pct;
  if (!src || !isNum(v)) return "";
  const tl = anStr(mr.rate_type_label);
  return '<p class="rf-minsrc">' + esc(T("tg.rf.min_src", "Минимальная ставка")) + " <b>" + esc(rfFmt(v)) + "</b>"
    + (tl ? " <span>(" + esc(tl) + ")</span>" : "") + " — " + esc(src) + "</p>";
}
/* компактная вилка части договора: шкала и итог */
function rfPartHtml(p){
  const F = anObj(p && p.rate_fork);
  if (!p || !p.rate_fork) return "";
  const L = F.available === true ? rfLayout(F) : null;
  return '<h4 class="an-h4">' + esc(anStr(F.title) || T("tg.rf.title", "Вилка ставки")) + "</h4>"
    + '<div class="rf rf-c" data-rfpart="' + esc(anStr(p.index)) + '">'
    + '<p class="rf-sum sm">' + esc(anStr(F.summary) || T("tg.act.na", "данные недоступны")) + "</p>"
    + (L ? rfScaleHtml(F, L) : "")
    + (F.available === true ? rfSrcbar(anArr(anObj(anObj(F.adjustments).region).indicators).filter(i => i && i.used === true).map(i => i.source)
      .concat([anObj(anObj(F.adjustments).market).source])) : rfSrcbar(rfMarks(F).filter(m => m.code === "market").map(m => m.source)))
    + rfHowHtml(F, "p" + anStr(p.index))
    + "</div>";
}
/* ---------- /вилка ставки ---------- */
/* ---------- шаг 3: ставка ниже минимальной — можно ли застраховать (01.10.2026) ----------
   Блок below_min_assessment ответа /act/make и GET /act/{id} (app/act.py → _below_min_view; правило —
   act_engine.below_min_assess, пороги — настройка акта below_min). Здесь ничего не пересчитывается: ответ, доводы
   с цифрами, условия и пометка — как отдаёт сервер на языке акта; коды (verdict, sign, effect, requested_source)
   выбирают только цвет и подпись словаря, неизвестный код на экран не выводится. Карточка — только при available. */
const BM_V = ["allowed", "allowed_with_conditions", "not_allowed"];
const BM_EF = ["blocks", "no_conditions", "no_yes"];
function bmTone(v){ return v === "allowed" ? "ok" : v === "not_allowed" ? "stop" : v === "allowed_with_conditions" ? "warn" : ""; }
function bmAnswer(B){
  switch (B.verdict) {
    case "allowed": return T("tg.bm.v_allowed", "Да");
    case "allowed_with_conditions": return T("tg.bm.v_allowed_with_conditions", "Да, при условиях");
    case "not_allowed": return T("tg.bm.v_not_allowed", "Нет");
    default: return anStr(B.verdict_label);
  }
}
function bmSrc(B){
  switch (B.requested_source) {
    case "request": return T("tg.bm.src_request", "из запроса филиала");
    case "contract": return T("tg.bm.src_contract", "из договора");
    case "employee": return T("tg.bm.src_employee", "введено сотрудником");
    default: return anStr(B.requested_source_label);
  }
}
function bmEf(e){
  switch (e) {
    case "blocks": return T("tg.bm.ef_blocks", "исключает отступление");
    case "no_conditions": return T("tg.bm.ef_no", "даёт ответ «нет»");
    case "no_yes": return T("tg.bm.ef_cond", "нужны условия");
    default: return "";
  }
}
/* «Запрошено 0,05 % (из запроса филиала) при минимуме 0,08 % — ниже на 0,03 %, недобор премии за срок (365 дн.) 300 000 сум» */
function bmLineText(B){
  const p = v => isNum(v) ? pct(v, Math.max(2, dp(v))) : T("tg.act.na", "данные недоступны");
  const src = bmSrc(B);
  let s = T("tg.bm.line", "Запрошено {req}{src} при минимуме {min} — ниже на {gap}", {req: p(B.requested_pct),
    src: src ? " (" + src + ")" : "", min: p(B.min_pct), gap: p(B.gap_pct)});
  if (isNum(B.shortfall)) {
    s += isNum(B.term_days) ? T("tg.bm.line_short_days", ", недобор премии за срок ({n} дн.) {sum}", {n: B.term_days, sum: money(B.shortfall)})
      : T("tg.bm.line_short", ", недобор премии за срок {sum}", {sum: money(B.shortfall)});
  }
  if (B.rate_type === "fixed" && isNum(B.requested_annual_pct)) {
    s += ". " + T("tg.bm.line_fixed", "Ставка фиксированная, на весь срок; годовой эквивалент запрошенной — {p}", {p: p(B.requested_annual_pct)});
  }
  return s;
}
function bmColHtml(cls, title, list){
  const ord = r => { const i = BM_EF.indexOf(r.effect); return i < 0 ? 9 : i; };
  const L = list.slice().sort((a, b) => ord(a) - ord(b));
  return '<div class="bm-col ' + cls + '"><h3>' + esc(title) + "<i>" + L.length + "</i></h3>"
    + (L.length ? "<ul>" + L.map(r => {
      const ef = cls === "against" && BM_EF.indexOf(r.effect) >= 0 ? r.effect : "";
      return '<li class="ef-' + (ef || "none") + '">' + (ef ? '<span class="bm-ef ' + ef + '">' + esc(bmEf(ef)) + "</span><br>" : "") + esc(anStr(r.text)) + "</li>";
    }).join("") + "</ul>" : '<p class="none">' + esc(T("tg.bm.none", "доводов нет")) + "</p>")
    + "</div>";
}
function bmCardHtml(a){
  const B = anObj(a && a.below_min_assessment);
  if (B.available !== true || BM_V.indexOf(B.verdict) < 0) return "";
  const rs = anArr(B.reasons).filter(r => r && anStr(r.text) && (r.sign === "for" || r.sign === "against"));
  const conds = anArr(B.conditions).map(c => anStr(anObj(c).text)).filter(Boolean);
  // служебное имя настройки в скобках («(below_min)») на экран не выводится
  const note = anStr(B.note).replace(/\s*\(\s*below_min\s*\)/g, "");
  const title = T("tg.bm.title", "Ставка ниже минимальной: можно ли застраховать");
  return '<section class="card act-card bm bm-' + esc(B.verdict) + '" id="bmCard" aria-label="' + esc(title) + '">'
    + '<div class="rf-top"><h2>' + esc(title) + "</h2>"
    + (B.calibrated ? "" : '<span class="tag off rf-unc">' + esc(T("tg.bm.uncal", "правило разработчика, не калибровано")) + "</span>") + "</div>"
    + '<div class="bm-ans"><p class="eyebrow">' + esc(T("tg.bm.q", "Можно ли застраховать по запрошенной ставке")) + "</p>"
    + "<b>" + esc(bmAnswer(B)) + "</b>"
    + (B.verdict === "not_allowed" && B.hard === true ? "<small>" + esc(T("tg.bm.hard", "Есть довод, который исключает отступление, — условия не помогут.")) + "</small>" : "")
    + "</div>"
    + '<p class="bm-line">' + esc(bmLineText(B)) + "</p>"
    + '<div class="bm-cols">'
    + bmColHtml("for", T("tg.bm.for", "За"), rs.filter(r => r.sign === "for"))
    + bmColHtml("against", T("tg.bm.against", "Против"), rs.filter(r => r.sign === "against"))
    + "</div>"
    + (conds.length && B.verdict !== "not_allowed" ? '<h3 class="rf-h">' + esc(B.verdict === "allowed" ? T("tg.bm.conds_yes", "Условие") : T("tg.bm.conds", "Условия"))
      + '</h3><ol class="bm-conds">' + conds.map(c => "<li>" + esc(c) + "</li>").join("") + "</ol>" : "")
    + '<p class="bm-uw">' + esc(T("tg.bm.uw", "Окончательное решение — андеррайтер.")) + "</p>"
    + (note ? '<p class="note">' + esc(note) + "</p>" : "")
    + "</section>";
}
/* строка общего обзора below_min (scoring.overview) — для сводки акта; без скоринга — ответ оценки */
function bmOvHtml(a){
  const ov = anArr(anObj(a && a.scoring).overview).filter(o => o && o.code === "below_min")[0];
  const B = anObj(a && a.below_min_assessment);
  let label = "", val = "", raw = "";
  if (ov) { label = anStr(ov.label); val = anStr(ov.value); raw = anStr(ov.raw); }
  else if (B.available === true) { val = anStr(B.verdict_label); raw = anStr(B.verdict); }
  if (!val) return "";
  label = label || T("tg.bm.ov_label", "ставка ниже минимальной: можно ли принять");
  const tone = bmTone(raw);
  return '<p class="act-line bm-ov' + (tone ? " v-" + tone : "") + '"><b>' + esc(label.charAt(0).toUpperCase() + label.slice(1)) + ":</b> <span>" + esc(val) + "</span></p>";
}
/* ---------- /ставка ниже минимальной ---------- */
/* ---------- факторы объекта (02.10.2026) ----------
   Блок factor_adjustment ответа /act/make и GET /act/{id} (app/act.py → _factor_view, коэффициенты — act_engine.factor_adjust).
   Здесь ничего не пересчитывается: группы, коэффициенты, ставки после каждого фактора и вклад в премию — как отдал сервер.
   Режим reference — ставка акта не менялась (справочно), apply — ставка и премия акта посчитаны с коэффициентами.
   Комплексный продукт — by_parts: подблок на каждую часть. Акты до 02.10.2026 — reason = old_act. */
const faMult = c => "×" + Number(c).toLocaleString(LOC(), {minimumFractionDigits: 2, maximumFractionDigits: 3});
const faDelta = v => !isNum(v) ? "—" : Number(v) > 0 ? "+" + money(Number(v)) : Number(v) < 0 ? "−" + money(-Number(v)) : money(0);
const faModeTag = ap => '<span class="tag fa-mode' + (ap ? " ap" : " off") + '">'
  + esc(ap ? T("tg.act.fa_mode_apply", "применено к ставке") : T("tg.act.fa_mode_ref", "справочно")) + "</span>";
/* фон региона по stat.uz под фактором (applied[i].stat): справочно, коэффициент не меняет.
   available:false — молчим, кроме no_data. Ссылка — только http/https. */
const faStatUrl = u => /^https?:\/\/[^\s"<>]+$/i.test(anStr(u)) ? anStr(u) : "";
function faStatHtml(st){
  const S = anObj(st);
  if (S.available !== true) {
    return S.available === false && S.reason === "no_data"
      ? '<small class="fa-stat none">' + esc(T("tg.act.fa_stat_nodata", "данных stat.uz по региону пока нет")) + "</small>" : "";
  }
  if (!isNum(S.share_pct)) return "";
  const n = Number(S.share_pct).toLocaleString(LOC(), {maximumFractionDigits: 1});
  const v = {r: anStr(S.region_name) || "—", p: anStr(S.period) || "—", n: n};
  const line = S.kind === "gas_share" ? T("tg.act.fa_stat_gas", "stat.uz: {r}, {p} — {n} % квартир с газом", v)
    : T("tg.act.fa_stat_walls", "stat.uz: {r}, {p} — {n} % жилищного фонда", v);
  const u = faStatUrl(S.url);
  return '<small class="fa-stat">' + esc(line) + '<span class="fa-stat-bg">' + esc(T("tg.act.fa_stat_bg", "фон региона, коэффициент не меняет")) + "</span></small>"
    + (u ? '<span class="srcbar act-src fa-stat-src"><a href="' + esc(u) + '" target="_blank" rel="noopener">'
      + esc(T("tg.act.fa_stat_src", "Читать в источнике stat.uz")) + "</a></span>" : "");
}
/* тело блока одной части или договора */
function faBodyHtml(F){
  const E = anObj(F.effect), ap = F.mode === "apply" && E.applied_to_act === true;
  const applied = anArr(F.applied).filter(x => x && isNum(x.coef));
  const unf = anArr(F.unfilled).map(u => anStr(anObj(u).label) || anStr(anObj(u).group)).filter(Boolean);
  const total = isNum(F.groups) ? Number(F.groups) : applied.length + unf.length;
  const cols = [T("tg.act.fa_col_group", "Группа"), T("tg.act.fa_col_option", "Вариант"), T("tg.act.fa_col_coef", "Коэффициент"),
    T("tg.act.fa_col_rate", "Ставка после"), T("tg.act.fa_col_prem", "Премия ±")];
  let h = '<p class="fa-fill">' + esc(T("tg.act.fa_filled", "Заполнено {n} из {m}", {n: applied.length, m: total})) + "</p>";
  if (applied.length) {
    h += '<table class="an-t fold fa-t"><thead><tr>' + cols.map((c, i) => "<th" + (i >= 2 ? ' class="n"' : "") + ">" + esc(c) + "</th>").join("") + "</tr></thead><tbody>"
      + applied.map(x => {
        const c = Number(x.coef), dir = c > 1 ? " up" : c < 1 ? " down" : "";
        return '<tr data-group="' + esc(anStr(x.group)) + '"><td class="h" data-l="' + esc(cols[0]) + '">' + esc(anStr(x.group_label) || anStr(x.group)) + "</td>"
          + '<td data-l="' + esc(cols[1]) + '">' + esc(anStr(x.label) || anStr(x.option))
          + (anStr(x.note) ? '<small class="fa-note">' + esc(x.note) + "</small>" : "") + faStatHtml(x.stat) + "</td>"
          + '<td class="n fa-k' + dir + '" data-l="' + esc(cols[2]) + '"><b>' + esc(faMult(c)) + "</b></td>"
          + '<td class="n" data-l="' + esc(cols[3]) + '">' + esc(isNum(x.rate_pct) ? rfFmt(x.rate_pct) : "—") + "</td>"
          + '<td class="n" data-l="' + esc(cols[4]) + '">' + esc(faDelta(x.premium_delta)) + "</td></tr>";
      }).join("") + "</tbody></table>";
  } else {
    h += '<p class="note">' + esc(T("tg.act.fa_none", "Факторы не заполнены — ставка без поправок по объекту.")) + "</p>";
  }
  if (E.available === true) {
    const facts = [[T("tg.act.fa_mult", "Множитель"), isNum(F.product) ? faMult(F.product) : "—"],
      [T("tg.act.fa_rate", "Ставка"), rfFmt(E.base_pct) + " → " + rfFmt(E.rate_pct)],
      [T("tg.act.fa_prem", "Премия"), (isNum(E.base_premium) ? money(E.base_premium) : "—") + " → " + (isNum(E.premium) ? money(E.premium) : "—")
        + (isNum(E.delta_premium) ? " (" + faDelta(E.delta_premium) + ")" : "")]];
    h += '<dl class="rf-facts fa-facts">' + facts.map(f => "<div><dt>" + esc(f[0]) + "</dt><dd>" + esc(f[1]) + "</dd></div>").join("") + "</dl>";
  } else if (E.reason === "statutory" || E.reason === "no_rate") {
    h += '<p class="note">' + esc(E.reason === "statutory" ? T("tg.act.fa_statutory", "Обязательный вид: ставка установлена законом, факторы её не меняют.")
      : T("tg.act.fa_no_rate", "Ставка акта не определена — влияние факторов не посчитано.")) + "</p>";
  }
  const B = anArr(F.bounds);
  if (F.clamped === true && isNum(B[0]) && isNum(B[1])) {
    h += '<p class="fa-warn">' + esc(T("tg.act.fa_clamped", "Произведение коэффициентов {raw} ограничено границами {lo} – {hi}.",
      {raw: isNum(F.raw_product) ? faMult(F.raw_product) : "—", lo: faMult(B[0]), hi: faMult(B[1])})) + "</p>";
  }
  if (E.floored === true) {
    h += '<p class="fa-warn">' + esc(T("tg.act.fa_floored", "Ставка не ниже минимальной: {p}.", {p: isNum(E.min_pct) ? rfFmt(E.min_pct) : "—"})) + "</p>";
  }
  if (E.available === true) {
    h += '<p class="rf-mode fa-st' + (ap ? " ap" : "") + '">' + esc(ap ? T("tg.act.fa_applied", "Ставка и премия акта посчитаны с этими коэффициентами.")
      : T("tg.act.fa_ref_note", "Справочно: ставка акта не менялась. Так изменилась бы ставка, если применить коэффициенты.")) + "</p>";
  }
  if (unf.length) {
    h += '<h3 class="rf-h">' + esc(T("tg.act.fa_unfilled", "Уточнить")) + '</h3><ul class="fa-unf">' + unf.map(u => "<li>" + esc(u) + "</li>").join("") + "</ul>";
  }
  const ex = anArr(F.explain).map(anStr).filter(Boolean);
  if (ex.length) {
    h += '<details class="mini fa-how"><summary>' + esc(T("tg.act.fa_how", "Как посчитано")) + "</summary><ul>" + ex.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul></details>";
  }
  return h;
}
function faCardHtml(a){
  if (!a || !a.factor_adjustment) return "";
  const F = anObj(a.factor_adjustment), title = T("tg.act.fa_title", "Факторы объекта");
  // класс без групп факторов — карточки нет
  if (F.reason === "no_groups") return "";
  const uncal = '<p class="fa-uncal">' + esc(T("tg.act.fa_uncal", "Экспертные коэффициенты, не калибровано.")) + "</p>";
  const parts = F.by_parts === true ? anArr(F.parts).filter(p => p && p.available === true)
    : F.by_objects === true ? anArr(F.objects).filter(p => p && p.available === true) : [];
  const ap = F.by_parts === true || F.by_objects === true ? parts.some(p => p.mode === "apply" && anObj(p.effect).applied_to_act === true)
    : F.mode === "apply" && anObj(F.effect).applied_to_act === true;
  const head = '<div class="rf-top"><h2>' + esc(title) + "</h2>" + (F.available === true ? faModeTag(ap) : "") + "</div>";
  const open = '<section class="card act-card fa" id="faCard" aria-label="' + esc(title) + '">' + head;
  if (F.available !== true) {
    return F.reason === "old_act" ? open + '<p class="note">' + esc(T("tg.act.fa_old", "Для этого акта факторы объекта не посчитаны — сформируйте акт заново.")) + "</p></section>" : "";
  }
  if (F.by_objects === true) {
    const objs = anArr(F.objects).filter(o => o && o.available === true);
    if (!objs.length) return "";
    return open + '<p class="hint">' + esc(T("tg.act.obj_fa_hint", "У каждого объекта свои поля класса — коэффициенты по объектам.")) + "</p>"
      + objs.map(o => '<details class="fa-part fa-obj" data-faobj="' + esc(anStr(o.index)) + '"><summary>'
        + esc(T("tg.act.obj_n", "Объект {n}", {n: anStr(o.index)}) + (anStr(o.label) ? " · " + anStr(o.label) : "") + (isNum(o.product) ? " · " + faMult(o.product) : ""))
        + "</summary>" + faBodyHtml(o) + "</details>").join("") + uncal + "</section>";
  }
  if (F.by_parts === true) {
    if (!parts.length) return "";
    return open + parts.map(p => '<div class="fa-part" data-fapart="' + esc(anStr(p.index)) + '"><h3 class="an-h4">'
      + esc(T("tg.act.fa_part", "Часть {n}, класс {c}", {n: anStr(p.index), c: anStr(p.class_code)}) + (anStr(p.label) ? " · " + anStr(p.label) : "")) + "</h3>"
      + faBodyHtml(p) + "</div>").join("") + uncal + "</section>";
  }
  return open + faBodyHtml(F) + uncal + "</section>";
}
/* ---------- /факторы объекта ---------- */
/* ---------- шаг 3: «Аналитика риска» (заказчик 30.09.2026: «нужно дать более детализированную аналитику») ----------
   Всё — из ответа сервера a.analytics (app/act.py → _analytics_view): числа и подписи на языке акта. Коды (уровень,
   источник, вердикт, что ввести) выбирают только оформление и подпись словаря; неизвестный код на экран не выводится.
   Карточки — таблицы и полосы; связный текст — в документе акта ниже, без повторов. */
const AN_CARDS = ["sum", "risks", "factors", "sens", "tariff", "scen", "ret", "score", "market", "fr", "ms"];
const anObj = x => (x && typeof x === "object" && !Array.isArray(x)) ? x : {};
const anArr = x => Array.isArray(x) ? x : [];
const anStr = x => (x == null || typeof x === "object" || (typeof x === "number" && isNaN(x))) ? "" : String(x);
const anPct = v => isNum(v) ? pct(v, dp(v)) : "—";
const sgnPct = (v, d) => isNum(v) ? (Number(v) < 0 ? "−" : Number(v) > 0 ? "+" : "") + pct(Math.abs(Number(v)), d == null ? dp(v) : d) : "—";
const sgnPp = v => (Number(v) < 0 ? "−" : "+") + nf(Math.abs(Number(v)), Math.max(1, dp(v))) + " " + T("tg.an.pp", "п. п.");
const anMult = v => isNum(v) ? "×" + nf(v, dp(v)) : "—";
function anOk(a){ return !!(a && a.analytics && a.analytics.available); }
function anScenOk(a){ return anOk(a) && !!anObj(a.analytics.scenarios).available; }
function anDomain(url){ try { return new URL(url).hostname.replace(/^www\./, ""); } catch (e) { return ""; } }
/* «Читать в источнике <домен>» — только для http(s)-адресов */
function anLink(url, domain){
  const u = anStr(url);
  if (!/^https?:\/\//i.test(u)) return "";
  const d = anStr(domain) || anDomain(u);
  return '<a href="' + esc(u) + '" target="_blank" rel="noopener">' + esc(T("tg.an.read_src", "Читать в источнике {d}", {d: d})) + "</a>";
}
/* строки «Источник: … — https://…» (сервер): текст и ссылка на источник */
function actSrcLines(list){
  const L = anArr(list).map(anStr).filter(Boolean);
  if (!L.length) return "";
  return '<div class="srcbar act-src">' + L.map(x => {
    const m = x.match(/^(.*?)\s+—\s+(https?:\/\/\S+)$/);
    return m ? "<span>" + esc(m[1]) + " " + anLink(m[2]) + "</span>" : "<span>" + esc(x) + "</span>";
  }).join("") + "</div>";
}
/* таблица документа акта (раздел 4): columns / rows / widths; 4 и больше колонок на телефоне — карточки-строки */
function actDocTableHtml(li){
  const tb = li.table, cols = tb.columns.map(anStr), w = anArr(tb.widths);
  const fold = cols.length >= 4;
  return '<div class="act-list act-tlist"><h4>' + esc(li.title) + "</h4>"
    + '<table class="an-t doc' + (fold ? " fold" : "") + '">'
    + (w.length === cols.length && w.every(isNum) ? "<colgroup>" + w.map(x => '<col style="width:' + Number(x) + '%">').join("") + "</colgroup>" : "")
    + "<thead><tr>" + cols.map(c => "<th>" + esc(c) + "</th>").join("") + "</tr></thead><tbody>"
    + tb.rows.map(r => "<tr>" + anArr(r).map((v, i) => '<td data-l="' + esc(cols[i] || "") + '"' + (i === 0 ? ' class="h"' : "") + ">"
      + esc(anStr(v)) + "</td>").join("") + "</tr>").join("")
    + "</tbody></table>"
    + anArr(li.notes).map(anStr).filter(Boolean).map(n => '<p class="act-tnote">' + esc(n) + "</p>").join("")
    + actSrcLines(li.sources)
    + "</div>";
}
/* таблица карточки: cols [{l, n}], rows — массивы готового HTML; fold — карточки-строки на телефоне */
function anTable(cols, rows, fold){
  const f = fold == null ? cols.length >= 4 : fold;
  return '<table class="an-t' + (f ? " fold" : "") + '"><thead><tr>'
    + cols.map(c => "<th" + (c.n ? ' class="n"' : "") + ">" + esc(c.l) + "</th>").join("") + "</tr></thead><tbody>"
    + rows.map(r => "<tr>" + r.map((v, i) => {
      const cls = [i === 0 ? "h" : "", cols[i] && cols[i].n ? "n" : ""].filter(Boolean).join(" ");
      return '<td data-l="' + esc(cols[i] ? cols[i].l : "") + '"' + (cls ? ' class="' + cls + '"' : "") + ">" + v + "</td>";
    }).join("") + "</tr>").join("")
    + "</tbody></table>";
}
function anCard(id, title, head, body){
  const open = CH.anOpen[id] != null ? !!CH.anOpen[id] : id.split(":").pop() === "sum";
  return '<details class="an-c" data-an="' + id + '"' + (open ? " open" : "") + '><summary><span class="an-ct">' + esc(title) + "</span>"
    + (head ? '<em class="an-ch">' + head + "</em>" : "") + '</summary><div class="an-in">' + body + "</div></details>";
}
const anNote = t => anStr(t) ? '<p class="an-note">' + esc(t) + "</p>" : "";
const anNa = t => '<p class="an-na">' + esc(anStr(t) || T("tg.act.na", "данные недоступны")) + "</p>";
const anUncal = () => '<span class="tag off">' + esc(T("tg.an.uncal", "экспертно, не калибровано")) + "</span>";
/* уровень: low / moderate / high (и elevated / critical у балла) — цветная метка; подпись — сервера */
function anLv(code, label){
  const k = ["low", "moderate", "elevated", "high", "critical"].indexOf(code) >= 0 ? code : "";
  const l = anStr(label) || (["low", "moderate", "high"].indexOf(code) >= 0 ? levelName(code) : "");
  return l ? '<span class="an-lv' + (k ? " lv-" + k : "") + '">' + esc(l) + "</span>" : "";
}
/* полоса: доля 0–100 % */
const anBar = (v, cls) => '<span class="an-bar" aria-hidden="true"><i' + (cls ? ' class="' + cls + '"' : "")
  + ' style="width:' + Math.max(0, Math.min(100, Number(v) || 0)).toFixed(1) + '%"></i></span>';

function actAnHtml(a){
  const an = a.analytics;
  const title = "<h2>" + esc(T("tg.an.title", "Аналитика риска")) + "</h2>";
  if (!an || !an.available) {
    const r = anObj(an).reason;
    const txt = !an || r === "old_act" ? T("tg.an.old_act", "Для этого акта подробная аналитика недоступна — сформируйте акт заново.")
      : anStr(an.text) || T("tg.an.na", "Подробная аналитика для этого акта не посчитана.");
    return '<div class="card act-card an-box">' + title + '<p class="note">' + esc(txt) + "</p></div>";
  }
  return '<section class="card act-card an-box" aria-label="' + esc(T("tg.an.title", "Аналитика риска")) + '">'
    + '<div class="an-top">' + title + '<button type="button" class="btn-link" data-go="anall">' + esc(anAllLabel()) + "</button></div>"
    + '<p class="sub">' + esc(T("tg.an.lead", "Из чего сложилась оценка: риски, факторы, тариф, сценарии убытка, рынок. Коэффициенты экспертные, не калиброваны по убыткам компании.")) + "</p>"
    + anCardsHtml(an, a, "")
    + "</section>";
}
/* одиннадцать карточек аналитики: для акта (pre = "") и для части договора (pre = "p1:" — a — сама часть) */
function anCardsHtml(an, a, pre){
  const cards = [
    ["sum", T("tg.an.c.sum", "Кратко"), "", anSumHtml(an)],
    ["risks", T("tg.an.c.risks", "Риски"), anRisksHead(an.risks), anRisksHtml(anObj(an.risks))],
    ["factors", T("tg.an.c.factors", "Факторы"), anObj(an.factors).technical_pct != null ? esc(anPct(an.factors.technical_pct)) : "", anFactorsHtml(anObj(an.factors))],
    ["sens", T("tg.an.c.sens", "Что изменит ставку"), anArr(anObj(an.sensitivity).items).length ? esc(String(an.sensitivity.items.length)) : "", anSensHtml(anObj(an.sensitivity))],
    ["tariff", T("tg.an.c.tariff", "Состав тарифа"), anObj(an.tariff).available && isNum(an.tariff.act_rate_pct) ? esc(anPct(an.tariff.act_rate_pct)) : "", anTariffHtml(anObj(an.tariff))],
    ["scen", T("tg.an.c.scen", "Сценарии убытка"), anScenHead(anObj(an.scenarios)), anScenHtml(anObj(an.scenarios), a)],
    ["ret", T("tg.an.c.ret", "Лимит собственного удержания"), anRetHead(anObj(an.retention)), anRetHtml(anObj(an.retention))],
    ["score", T("tg.an.c.score", "Балл риска (справочно)"), anObj(an.score).available && isNum(an.score.score) ? esc(nf(an.score.score, 1)) : "", anScoreHtml(anObj(an.score))],
    ["market", T("tg.an.c.market", "Рынок и статистика"), anObj(an.market).available && isNum(an.market.rate_pct) ? esc(anPct(an.market.rate_pct)) : "", anMarketHtml(anObj(an.market), anObj(an.stats))],
    ["fr", T("tg.an.c.fr", "Варианты франшизы (справочно)"), anObj(an.franchise).available ? esc(String(anArr(an.franchise.rows).length)) : "", anFrHtml(anObj(an.franchise))],
    ["ms", T("tg.an.c.ms", "Мероприятия"), anArr(anObj(an.measures).items).length ? esc(String(an.measures.items.length)) : "", anMsHtml(anObj(an.measures))],
  ];
  return cards.map(c => anCard(pre + c[0], c[1], c[2], c[3])).join("");
}
/* открыты ли все карточки — по CH.anOpen (его ведёт обработчик toggle), без чтения старой разметки */
function anAllOpen(){ return AN_CARDS.every(k => CH.anOpen[k] != null ? !!CH.anOpen[k] : k === "sum"); }
function anAllLabel(){ return anAllOpen() ? T("tg.an.collapse", "Свернуть все карточки") : T("tg.an.expand", "Развернуть все карточки"); }
function anAll(){
  const open = !anAllOpen();
  AN_CARDS.forEach(k => { CH.anOpen[k] = open; });
  document.querySelectorAll("#wzBody .an-box details.an-c").forEach(d => { d.open = open; });
  const b = document.querySelector('#wzBody [data-go="anall"]');
  if (b) b.textContent = anAllLabel();
}
/* 1. Кратко */
function anSumHtml(an){
  const s = anArr(anObj(an.summary).sentences).map(anStr).filter(Boolean);
  return s.length ? '<ul class="an-sum">' + s.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul>" : anNa();
}
/* 2. Риски: доля в ставке полосой, уровень меткой; раскрытие — что повышает, снижает, чего не хватает, меры */
function anRisksHead(R){
  const top = anArr(anObj(R).items).filter(x => x && anStr(x.name))[0];
  return top ? esc(anStr(top.name) + " · " + (anStr(top.share_text) || anPct(top.share_of_net_pct))) : "";
}
function anRisksHtml(R){
  const items = anArr(R.items).filter(x => x && anStr(x.name));
  if (!items.length) return anNa();
  const row = (lbl, list, cls) => {
    const L = anArr(list).map(anStr).filter(Boolean);
    return L.length ? '<p class="an-rl' + (cls ? " " + cls : "") + '"><span>' + esc(lbl) + "</span>" + esc(L.join(", ")) + "</p>" : "";
  };
  return '<div class="an-rh" aria-hidden="true"><span>' + esc(T("tg.an.col_risk", "Риск")) + "</span><span>" + esc(T("tg.an.col_share", "Доля в ставке"))
    + "</span><span>" + esc(T("tg.an.col_level", "Уровень")) + "</span><span>" + esc(T("tg.an.col_why", "Почему")) + "</span></div>"
    + items.map(x => {
      const share = anStr(x.share_text) || anPct(x.share_of_net_pct);
      const why = anStr(x.why).split("; ")[0];
      const more = row(T("tg.an.r_raises", "Повышает"), x.raises, "up") + row(T("tg.an.r_lowers", "Снижает"), x.lowers, "down")
        + row(T("tg.an.r_unknown", "Не хватает данных"), x.unknown, "unk") + row(T("tg.an.r_measures", "Что поможет"), x.measures, "down");
      const head = '<b class="an-rn">' + esc(x.name) + (x.catastrophic ? "<small>" + esc(T("tg.an.r_cat", "катастрофический")) + "</small>" : "") + "</b>"
        + '<span class="an-share">' + anBar(x.share_of_net_pct) + "<b>" + esc(share) + "</b></span>"
        + '<span class="an-rlv">' + anLv(x.level, x.level_label) + "</span>"
        + '<span class="an-why">' + esc(why) + "</span>";
      return more ? '<details class="an-rk"><summary>' + head + '<i class="an-caret" aria-hidden="true"></i></summary><div class="an-rd">' + more + "</div></details>"
        : '<div class="an-rk flat"><div class="an-rs">' + head + "<i></i></div></div>";
    }).join("")
    + anArr(R.notes).map(anNote).join("");
}
/* 3. Факторы: значение, источник, множитель, влияние на техническую ставку и премию */
function anSrcName(code){
  switch (code) {
    case "input": return T("tg.an.src.input", "введено");
    case "document": case "document_ai": return T("tg.an.src.document", "из документа");
    case "text": return T("tg.an.src.text", "из документа, проверьте");
    case "photo": case "plate": return T("tg.an.src.photo", "распознано");
    case "kind": return T("tg.an.src.kind", "по виду объекта");
    case "default": return T("tg.an.src.default", "по умолчанию, проверьте");
    case "not_set": return T("tg.an.src.not_set", "не указано");
    case "act_terms": return T("tg.an.src.terms", "условия акта");
    default: return "";
  }
}
function anSrcTag(code, label){
  const n = anSrcName(code) || anStr(label);
  if (!n) return "—";
  const warn = ["text", "default", "not_set"].indexOf(code) >= 0;
  return '<span class="an-src' + (warn ? " chk" : "") + '"' + (anStr(label) ? ' title="' + esc(label) + '"' : "") + ">" + esc(n) + "</span>";
}
function anFactorsHtml(F){
  const items = anArr(F.items).filter(x => x && anStr(x.name));
  if (!items.length) return anNa();
  const rows = items.map(x => {
    const dir = x.direction === "up" ? "up" : x.direction === "down" ? "down" : "";
    const pp = isNum(x.rate_pp) && Number(x.rate_pp) !== 0;
    return [esc(x.name),
      '<span class="' + (x.status === "not_set" ? "an-mut" : "") + '">' + esc(anStr(x.value) || "—") + "</span>",
      anSrcTag(x.source, x.source_label),
      '<span class="an-' + (dir || "mut") + '">' + esc(anMult(x.multiplier)) + "</span>",
      pp ? '<span class="an-' + (Number(x.rate_pp) > 0 ? "up" : "down") + '">' + esc(sgnPp(x.rate_pp)) + "</span>"
        : '<span class="an-mut">' + esc(anStr(x.contribution) || T("tg.an.f_zero", "не влияет")) + "</span>",
      isNum(x.premium_effect) && Number(x.premium_effect) !== 0
        ? '<span class="an-' + (Number(x.premium_effect) > 0 ? "up" : "down") + '">' + esc(sgnMoney(x.premium_effect)) + "</span>" : "—"];
  });
  return anTable([{l: T("tg.an.col_factor", "Фактор")}, {l: T("tg.an.col_value", "Значение")}, {l: T("tg.an.col_source", "Источник")},
    {l: T("tg.an.col_mult", "Множитель"), n: 1}, {l: T("tg.an.col_rate_pp", "Влияние на ставку"), n: 1}, {l: T("tg.an.col_prem", "Влияние на премию"), n: 1}], rows)
    + (isNum(F.technical_pct) ? '<div class="an-foot"><span>' + esc(T("tg.an.f_base", "Без факторов {b} → техническая ставка", {b: anPct(F.base_pct)}))
      + "</span><b>" + esc(anPct(F.technical_pct)) + "</b>" + anUncal() + "</div>" : "")
    + '<p class="an-note">' + esc(T("tg.an.f_note", "Премия — по технической ставке за срок акта; тариф акта считается по тарифной политике.")) + "</p>";
}
/* 4. Что изменит ставку: меры страхователя и уточнения данных — разными значками */
const AN_ICO = {
  measure: '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M10 2l6 2.5v4.6c0 4-2.6 7-6 8.9-3.4-1.9-6-4.9-6-8.9V4.5z" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M7 10l2.2 2.2L13.5 8" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>',
  clarify: '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><circle cx="10" cy="10" r="7.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M7.8 7.8a2.3 2.3 0 1 1 3.2 2.1c-.7.3-1 .8-1 1.5v.4" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="10" cy="14.4" r="1" fill="currentColor"/></svg>'
};
function anSensHtml(S){
  const items = anArr(S.items).filter(x => x && anStr(x.name) && anStr(x.to));
  if (!items.length) return anNa(T("tg.an.s_none", "Ставку заметно не меняет ни одна мера и ни одно уточнение."));
  return '<p class="an-legend"><span><span class="an-ico m">' + AN_ICO.measure + "</span>" + esc(T("tg.an.s_measure", "мера страхователя")) + "</span>"
    + '<span><span class="an-ico c">' + AN_ICO.clarify + "</span>" + esc(T("tg.an.s_clarify", "уточнение данных")) + "</span></p>"
    + items.map(x => {
      const m = x.kind === "measure";
      const lbl = m ? anStr(x.name) + ": " + anStr(x.to) : T("tg.an.s_if", "если {f} — «{to}»", {f: anStr(x.name).toLowerCase(), to: anStr(x.to)});
      const dcls = Number(x.delta_pct) < 0 ? "down" : "up";
      const act = isNum(x.act_premium_after)
        ? '<p class="an-sv"><span>' + esc(T("tg.an.s_act", "премия акта")) + "</span><b>" + esc(money(x.act_premium_after)) + "</b>"
          + (isNum(x.act_premium_delta) && Number(x.act_premium_delta) !== 0 ? '<em class="an-down">' + esc(sgnMoney(x.act_premium_delta)) + "</em>"
            : '<em class="an-mut">' + esc(T("tg.an.s_same", "без изменения")) + "</em>")
          + (x.act_floored ? '<span class="tag off">' + esc(T("tg.an.floor", "упирается в минимум")) + "</span>" : "") + "</p>" : "";
      return '<div class="an-sn"><span class="an-ico ' + (m ? "m" : "c") + '" role="img" aria-label="' + esc(m ? T("tg.an.s_measure", "мера страхователя") : T("tg.an.s_clarify", "уточнение данных")) + '">'
        + (m ? AN_ICO.measure : AN_ICO.clarify) + "</span><div>"
        + '<p class="an-st">' + esc(lbl) + "</p>"
        + '<p class="an-sv"><span>' + esc(T("tg.an.s_tech", "техническая ставка")) + "</span><b>" + esc(anPct(x.tech_before) + " → " + anPct(x.tech_after)) + "</b>"
        + '<em class="an-' + dcls + '">' + esc(sgnPct(x.delta_pct)) + "</em></p>"
        + act + "</div></div>";
    }).join("")
    + anNote(S.note);
}
/* 5. Состав тарифа: тариф акта, техническая ставка и рынок полосами; строки расчёта таблицей */
function anTariffHtml(Tt){
  if (!Tt.available) return anNa(Tt.text || Tt.conclusion);
  const rows = anArr(Tt.rows).filter(r => r && anStr(r.label));
  const bars = [[T("tg.an.t_act", "Тариф акта"), Tt.act_rate_pct, "acc"], [T("tg.an.t_tech", "Техническая ставка"), Tt.technical_pct, "tech"],
    [T("tg.an.t_market", "Рынок (НАПП)"), Tt.market_rate_pct, "mk"]].filter(b => isNum(b[1]) && Number(b[1]) > 0);
  const max = Math.max.apply(null, bars.map(b => Number(b[1])).concat([0]));
  return (bars.length > 1 ? '<div class="an-cmp">' + bars.map(b => '<div><span>' + esc(b[0]) + "</span>" + anBar(Number(b[1]) / max * 100, b[2])
      + "<b>" + esc(anPct(b[1])) + "</b></div>").join("") + "</div>" : "")
    + anTable([{l: T("tg.an.col_indicator", "Показатель")}, {l: T("tg.an.col_value", "Значение"), n: 1}, {l: T("tg.an.col_note", "Пояснение")}],
      rows.map(r => [esc(r.label), esc(anStr(r.value) || "—"), '<span class="an-mut">' + esc(anStr(r.note)) + "</span>"]), false)
    + (anStr(Tt.conclusion) ? '<p class="an-concl">' + esc(Tt.conclusion) + "</p>" : "");
}
/* 6. Сценарии убытка: PML / EML / MFL с формулой, «что если» */
function anScenHead(S){
  const e = anArr(S.items).find(x => x && x.name === "EML");
  return S.available && e && isNum(e.amount) ? esc("EML " + compact(e.amount) + " " + SUM()) : "";
}
function anScenHtml(S, a){
  if (!S.available) return anNa(anObj(a.scenarios).note);
  const sc = anObj(a.scenarios);
  const tiles = anArr(S.items).filter(x => x && ["PML", "EML", "MFL"].indexOf(x.name) >= 0).map(x => {
    const part = anArr(x.parts).find(p => p && p.peril === x.chosen && isNum(p.base) && isNum(p.share)) || null;
    const math = part ? compact(part.base) + " × " + nf(part.share, dp(part.share)) + " = " + compact(part.amount) : "";
    const lines = anStr(x.formula).split("; ").filter(Boolean);
    return '<div class="sc-t an-sc"><p><b>' + esc(x.name) + "</b>" + esc(scName(x.name.toLowerCase())) + "</p>"
      + "<strong>" + esc(money(x.amount)) + "</strong>"
      + (anStr(x.pct_text) ? "<span>" + esc(T("tg.act.sc_pct", "{p} страховой суммы", {p: x.pct_text})) + "</span>" : "")
      + (math ? '<code class="an-math">' + esc(math) + "</code>" : "")
      + (lines.length ? '<ul class="an-fl">' + lines.map(l => "<li>" + esc(l) + "</li>").join("") + "</ul>" : "") + "</div>";
  }).join("");
  const wi = anArr(S.whatif).filter(w => w && anStr(w.label));
  // «что если» — крупные суммы коротко (47,4 млрд сум); точные суммы — в документе акта
  const cm = v => compact(v) + " " + SUM();
  const cell = (v, d) => esc(cm(v)) + (isNum(d) ? (Number(d) !== 0 ? '<small class="an-' + (Number(d) < 0 ? "down" : "up") + '">' + esc((Number(d) < 0 ? "−" : "+") + cm(Math.abs(Number(d)))) + "</small>"
    : '<small class="an-mut">' + esc(T("tg.an.s_same", "без изменения")) + "</small>") : "");
  const asm = anArr(sc.assumptions).filter(x => x && anStr(x.text));
  return '<div class="sc-tiles">' + tiles + "</div>"
    + (anStr(sc.definitions) ? anNote(sc.definitions) : "")
    + (wi.length ? '<h4 class="an-h4">' + esc(T("tg.an.wi_title", "Что если")) + "</h4>"
      + anTable([{l: T("tg.an.col_variant", "Вариант")}, {l: "PML", n: 1}, {l: "EML", n: 1}, {l: "MFL", n: 1}, {l: T("tg.an.col_excess", "EML сверх удержания"), n: 1}],
        wi.map(w => [esc(w.label), cell(w.pml, w.pml_delta), cell(w.eml, w.eml_delta), cell(w.mfl, w.mfl_delta),
          !isNum(w.eml_excess) ? "—" : Number(w.eml_excess) > 0 ? '<span class="an-up">' + esc(cm(w.eml_excess)) + "</span>"
            : '<span class="an-down">' + esc(T("tg.an.wi_within", "в пределах")) + "</span>"])) : "")
    + '<p class="an-note">' + esc(T("tg.act.sc_uncal", "Экспертная оценка: коэффициенты не калиброваны по убыткам компании.")) + "</p>"
    + (asm.length ? '<details class="mini"><summary>' + esc(T("tg.act.sc_assumed", "Принято по умолчанию · {n}", {n: asm.length})) + "</summary><ul>"
      + asm.map(x => "<li>" + esc(x.text) + "</li>").join("") + "</ul></details>" : "");
}
/* 7. Лимит собственного удержания: вердикт цветом, EML и MFL против лимита полосами, что ввести в админке */
function anRetName(v){
  switch (v) {
    case "within": return T("tg.an.ret.within", "EML и MFL в пределах расчётного удержания (оценка)");
    case "eml_excess": return T("tg.an.ret.eml_excess", "EML выше расчётного удержания (оценка)");
    case "mfl_excess": return T("tg.an.ret.mfl_excess", "MFL выше расчётного удержания (оценка)");
    case "unknown": return T("tg.an.ret.unknown", "удержание не задано");
    default: return "";
  }
}
function anRetCls(v){ return v === "within" ? "ok" : v === "eml_excess" ? "stop" : v === "mfl_excess" ? "warn" : "off"; }
function anRetHead(R){ const n = anRetName(R.verdict); return n ? '<span class="an-vd ' + anRetCls(R.verdict) + '">' + esc(n) + "</span>" : ""; }
function anNeedName(c){
  switch (c) {
    case "own_funds": return T("tg.an.need.own_funds", "собственные средства");
    case "reserves": return T("tg.an.need.reserves", "страховые резервы");
    default: return "";
  }
}
function anRetHtml(R){
  const v = R.verdict, name = anRetName(v);
  const known = !!R.known && isNum(R.limit) && Number(R.limit) > 0;
  let meter = "";
  if (known && (isNum(R.eml) || isNum(R.mfl))) {
    const lim = Number(R.limit), scale = Math.max(lim, Number(R.eml) || 0, Number(R.mfl) || 0) * 1.08;
    const mk = (lbl, x) => isNum(x) ? '<div class="an-mrow"><span>' + esc(lbl) + '</span><span class="an-meter" aria-hidden="true"><i class="'
      + (Number(x) > lim ? "over" : "") + '" style="width:' + (Number(x) / scale * 100).toFixed(1) + '%"></i><u style="left:' + (lim / scale * 100).toFixed(1) + '%"></u></span><b>'
      + esc(compact(x) + " " + SUM()) + "</b></div>" : "";
    meter = '<div class="an-meterbox">' + mk("EML", R.eml) + mk("MFL", R.mfl)
      + '<p class="an-mleg"><u aria-hidden="true"></u>' + esc(T("tg.an.ret_limit", "расчётное удержание {x}", {x: money(R.limit)})) + "</p></div>";
  }
  const need = anArr(R.need).map(anNeedName).filter(Boolean);
  return (name ? '<p class="an-verdict ' + anRetCls(v) + '">' + esc(name) + (R.status === "temporary"
      ? ' <span class="tag off">' + esc(T("tg.an.ret_temp", "цифры временные, до данных бухгалтерии")) + "</span>" : "") + "</p>" : "")
    + meter
    + anArr(R.lines).map(anStr).filter(Boolean).map(l => '<p class="an-line">' + esc(l) + "</p>").join("")
    + (need.length ? '<p class="an-need">' + esc(T("tg.an.ret_need", "Что ввести в админке (раздел «Финансы»): {what}", {what: need.join(", ")})) + "</p>" : "")
    + (anStr(R.legal_ref) ? '<p class="an-note">' + esc(T("tg.an.ret_norm", "Норма: {r}", {r: R.legal_ref})) + "</p>" : "");
}
/* 8. Балл риска 0–100 (справочно) и его составляющие */
function anScoreHtml(S){
  if (!S.available || !isNum(S.score)) return anNa();
  const b = anArr(S.bounds).filter(isNum).map(Number);
  const edges = [0].concat(b.length === 4 ? b : [20, 40, 60, 80]).concat([100]);
  const seg = edges.slice(1).map((e, i) => '<i class="g' + i + '" style="width:' + (e - edges[i]) + '%"></i>').join("");
  const sc = Math.max(0, Math.min(100, Number(S.score)));
  const comps = anArr(S.components).filter(c => c && anStr(c.name));
  return '<div class="an-score"><div class="an-big"><b>' + esc(nf(S.score, 1)) + "</b><span>/ 100</span></div>"
    + '<div class="an-sl">' + anLv(S.level, S.level_label)
    + (anStr(S.act_level_label) ? '<p>' + esc(T("tg.an.sc_act", "Уровень акта: {l}", {l: S.act_level_label})) + "</p>" : "") + "</div></div>"
    + '<div class="an-gauge" aria-hidden="true">' + seg + '<u style="left:' + sc.toFixed(1) + '%"></u></div>'
    + '<div class="an-gl" aria-hidden="true">' + edges.map(e => '<span style="left:' + e + '%">' + e + "</span>").join("") + "</div>"
    + (comps.length ? anTable([{l: T("tg.an.col_indicator", "Показатель")}, {l: T("tg.an.col_points", "Баллы"), n: 1}, {l: T("tg.an.col_weight", "Вес"), n: 1},
      {l: T("tg.an.col_contrib", "Вклад"), n: 1}, {l: T("tg.an.col_why", "Почему")}],
      comps.map(c => [esc(c.name), c.applicable && isNum(c.points) ? esc(nf(c.points, 0)) : "—", c.applicable && isNum(c.weight) ? esc(nf(c.weight, dp(c.weight))) : "—",
        c.applicable && isNum(c.contribution) ? "<b>" + esc(nf(c.contribution, 1)) + "</b>" : "—", '<span class="an-mut">' + esc(anStr(c.why)) + "</span>"])) : "")
    + '<p class="an-note">' + esc(T("tg.an.sc_ref", "Балл — справочно: на тариф акта не влияет. Шкала и веса экспертные, не калиброваны.")) + "</p>";
}
/* 9. Рынок (НАПП) и статистика региона — под каждым внешним показателем плашка источника */
function anMarketHtml(M, ST){
  let h = "";
  if (M.available && isNum(M.rate_pct)) {
    const cmp = isNum(M.act_vs_market_pct) ? (Number(M.act_vs_market_pct) < 0
      ? T("tg.an.mk_below", "ниже рынка на {p}", {p: pct(Math.abs(M.act_vs_market_pct), dp(M.act_vs_market_pct))})
      : T("tg.an.mk_above", "выше рынка на {p}", {p: pct(M.act_vs_market_pct, dp(M.act_vs_market_pct))})) : "";
    /* взят пакет классов (комплексный продукт или строка «8, 9») — подписи «пакета классов», а не «класса» */
    const isPack = !!(M.pack || anStr(M.pack_label));
    const rows = [[isPack ? T("tg.an.mk_rate_pack", "Рыночная ставка пакета классов (НАПП)") : T("tg.an.mk_rate", "Рыночная ставка класса (НАПП)"), anPct(M.rate_pct),
      [M.rate_date ? T("tg.an.mk_on", "на {d}", {d: dateOnly(M.rate_date)}) : "", isNum(M.months) ? T("tg.an.mk_months", "{n} мес.", {n: M.months}) : "",
        anStr(M.pack_label) || (M.pack ? T("tg.an.mk_pack", "строка классов 8 и 9") : "")].filter(Boolean).join(", ")]];
    if (isNum(M.loss_ratio_pct)) rows.push([isPack ? T("tg.an.mk_lr_pack", "Убыточность пакета классов") : T("tg.an.mk_lr", "Убыточность класса"), pct(M.loss_ratio_pct, 1), M.rate_date ? T("tg.an.mk_on", "на {d}", {d: dateOnly(M.rate_date)}) : ""]);
    if (isNum(M.rate_full_year_pct) && isNum(M.full_year)) rows.push([T("tg.an.mk_fy", "За {y} год", {y: M.full_year}), anPct(M.rate_full_year_pct),
      isNum(M.loss_ratio_full_year_pct) ? T("tg.an.mk_fy_lr", "убыточность {p}", {p: pct(M.loss_ratio_full_year_pct, 1)}) : ""]);
    /* комплексный продукт: рядом с пакетом — одиночные строки классов продукта (ставка, убыточность), правило № 5 */
    anArr(M.class_rows).filter(c => c && c.available && isNum(c.rate_pct)).forEach(c => rows.push([
      T("tg.an.mk_cls", "Класс {c} отдельно (НАПП)", {c: c.class_code}), anPct(c.rate_pct),
      [isNum(c.loss_ratio_pct) ? T("tg.an.mk_fy_lr", "убыточность {p}", {p: pct(c.loss_ratio_pct, 1)}) : "",
        c.date ? T("tg.an.mk_on", "на {d}", {d: dateOnly(c.date)}) : ""].filter(Boolean).join(", ")]));
    if (isNum(M.act_rate_pct)) rows.push([T("tg.an.t_act", "Тариф акта"), anPct(M.act_rate_pct), cmp]);
    if (isNum(M.technical_pct)) rows.push([T("tg.an.t_tech", "Техническая ставка"), anPct(M.technical_pct),
      isNum(M.tech_vs_market_pct) ? T("tg.an.mk_to_market", "{p} к рынку", {p: sgnPct(M.tech_vs_market_pct)}) : ""]);
    const s = anObj(M.source);
    h += (cmp ? '<p class="an-verdict ' + (Number(M.act_vs_market_pct) < 0 ? "warn" : "ok") + '">' + esc(T("tg.an.mk_cmp", "Ставка акта {c}", {c: cmp})) + "</p>" : "")
      + anTable([{l: T("tg.an.col_indicator", "Показатель")}, {l: T("tg.an.col_value", "Значение"), n: 1}, {l: T("tg.an.col_note", "Пояснение")}],
        rows.map(r => [esc(r[0]), esc(r[1]), '<span class="an-mut">' + esc(r[2]) + "</span>"]), false)
      + (anLink(s.url, s.domain) ? '<div class="srcbar"><span>' + esc([anStr(s.title), s.as_of ? T("tg.an.src_slice", "срез на {d}", {d: dateOnly(s.as_of)}) : ""].filter(Boolean).join(", "))
        + "</span>" + anLink(s.url, s.domain) + "</div>" : "");
  } else {
    h += anNa(anArr(M.lines).map(anStr)[0] || T("tg.an.mk_na", "Рыночной ставки класса в отчётах НАПП нет."));
  }
  const inds = anArr(ST.indicators).filter(i => i && anStr(i.name));
  h += '<h4 class="an-h4">' + esc(anStr(ST.region) ? T("tg.an.st_title", "Показатели региона: {r}", {r: ST.region}) : T("tg.an.st_title0", "Показатели региона")) + "</h4>";
  if (!inds.length) h += anNa(T("tg.an.st_none", "Показателей региона для этого класса в базе нет."));
  h += inds.map(i => {
    const ok = i.status === "ok";
    const meta = [anStr(i.period), i.scope === "region" ? T("tg.an.st_region", "регион") : i.scope === "republic" ? T("tg.an.st_rep", "по республике") : ""].filter(Boolean).join(" · ");
    const vs = anObj(i.vs_country);
    const cmp = isNum(vs.diff_pct) ? '<span class="an-vs an-' + (Number(vs.diff_pct) > 0 ? "up" : "down") + '">' + esc(T("tg.an.st_cmp", "к республике {p}", {p: sgnPct(vs.diff_pct, 1)})) + "</span>" : "";
    const pts = i.used_in_score && isNum(i.points) ? '<span class="tag off">' + esc(T("tg.an.st_pts", "в балле {n}", {n: nf(i.points, 0)})) + "</span>" : "";
    const seen = {};
    const srcs = anArr(i.sources).filter(s => s && anLink(s.url) && !seen[s.url + "|" + s.title] && (seen[s.url + "|" + s.title] = 1));
    return '<div class="an-ind"><div class="an-ih"><b>' + esc(i.name) + "</b>"
      + (ok ? '<span class="an-iv">' + esc(anStr(i.value_text)) + "</span>" : '<span class="an-iv an-mut">' + esc(T("tg.an.st_nodata", "нет в открытых данных")) + "</span>") + "</div>"
      + '<p class="an-im">' + esc(meta) + cmp + pts + "</p>"
      + (srcs.length ? '<div class="srcbar an-srcs">' + srcs.map(s => "<span>" + esc([anStr(s.title), anStr(s.period)].filter(Boolean).join(", ")) + " "
        + anLink(s.url, s.source) + "</span>").join("") + "</div>"
        : '<div class="srcbar an-srcs"><span>' + esc(T("tg.an.st_nosrc", "Источник не указан — нет в открытых данных")) + "</span></div>")
      + "</div>";
  }).join("");
  if (anStr(ST.not_found)) h += '<p class="an-note">' + esc(T("tg.an.st_nf", "Нет в открытых данных: {what}", {what: ST.not_found})) + "</p>";
  return h;
}
/* 10. Варианты франшизы (справочно) */
function anFrHtml(F){
  if (!F.available) return anNa(F.text);
  const rows = anArr(F.rows).filter(r => r && isNum(r.pct));
  return anTable([{l: T("tg.an.col_fr", "Франшиза"), n: 1}, {l: T("tg.an.col_fr_amount", "С каждого убытка"), n: 1}, {l: T("tg.an.col_premium", "Премия"), n: 1},
    {l: T("tg.an.col_saving", "Экономия"), n: 1}],
    rows.map(r => {
      if (!r.ok) return [esc(anPct(r.pct)), esc(money(r.amount)), '<span class="an-mut">' + esc(T("tg.an.fr_na", "не посчитано")) + "</span>", "—"];
      const tags = (r.floored ? '<span class="tag off">' + esc(T("tg.an.floor", "упирается в минимум")) + "</span>" : "")
        + (r.extrapolated ? '<span class="tag off">' + esc(T("tg.an.fr_extra", "экспертное продолжение")) + "</span>" : "");
      return [esc(anPct(r.pct)), esc(money(r.amount)), esc(money(r.premium)) + (tags ? '<span class="an-tags">' + tags + "</span>" : ""),
        (Number(r.saving) > 0 ? '<span class="an-down">' : '<span class="an-mut">') + esc(money(r.saving))
          + (isNum(r.saving_pct) && Number(r.saving_pct) > 0 ? " (" + esc(pct(r.saving_pct, 1)) + ")" : "") + "</span>"];
    }))
    + anNote(F.note);
}
/* 11. Мероприятия: эффект на техническую ставку и премию акта; итог «если выполнить все» */
function anMsHtml(M){
  const items = anArr(M.items).filter(x => x && anStr(x.name));
  if (!items.length) return anNa(T("tg.act.ms_none", "Дополнительных мероприятий не требуется."));
  const tot = anObj(M.total);
  const any = items.some(x => isNum(x.tech_after));
  const total = anStr(tot.text) || (any ? "" : T("tg.an.ms_total_none", "Если выполнить все: ставка и премия акта не изменятся — эти мероприятия снижают вероятность убытка."));
  return anTable([{l: T("tg.an.col_measure", "Мероприятие")}, {l: T("tg.an.col_tech", "Техническая ставка"), n: 1}, {l: T("tg.an.col_act_prem", "Премия акта"), n: 1}],
    items.map(x => [esc(x.name),
      isNum(x.tech_after) ? esc(anPct(x.tech_before) + " → " + anPct(x.tech_after)) + ' <em class="an-down">' + esc(sgnPct(x.effect_pct)) + "</em>"
        : '<span class="an-mut">' + esc(T("tg.an.ms_no_rate", "на ставку не влияет")) + "</span>",
      isNum(x.premium_delta) ? (Number(x.premium_delta) !== 0 ? '<span class="an-down">' + esc(sgnMoney(x.premium_delta)) + "</span>"
        : '<span class="an-mut">' + esc(T("tg.an.s_same", "без изменения")) + "</span>") : "—"]), true)
    + (total ? '<p class="an-concl">' + esc(total) + "</p>" : "");
}
function actRowsHtml(rows, discLabels, cls){
  if (!rows || !rows.length) return "";
  return '<div class="act-rows' + (cls ? " " + cls : "") + '">' + rows.map(r =>
    '<div' + (discLabels.indexOf(r.label) >= 0 ? ' class="disc"' : "") + "><span>" + esc(r.label) + "</span><b>" + esc(r.value) + "</b>"
      + (r.note ? "<small>" + esc(r.note) + "</small>" : "") + "</div>").join("") + "</div>";
}
/* акт как документ: шапка, пять разделов, строка о подтверждении андеррайтером; расхождения выделены */
function actDocHtml(a){
  const discText = (a.discrepancies || []).map(d => d.text);
  const discLabels = (a.discrepancies || []).map(d => d.label);
  const secs = a.sections || [];
  return '<article class="card act-doc">'
    + '<div class="act-doc-top"><p class="eyebrow">' + esc(T("tg.act.doc_eyebrow", "Текст акта")) + "</p>"
    + (secs.length ? '<button type="button" class="btn-link" data-go="docall">' + esc(actDocAllLabel()) + "</button>" : "") + "</div>"
    + (a.insurer_known ? '<p class="act-ins">' + esc(a.insurer) + "</p>" : "")
    + '<h2 class="act-title">' + esc(a.title) + "</h2>"
    + actRowsHtml(a.header, [], "act-head")
    // разделы сворачиваются; 4 и 5 кратко есть в сводке выше, поэтому по умолчанию свёрнуты
    + secs.map(s => '<details class="act-s" data-sn="' + esc(s.n) + '"' + (actSecOpen(s.n) ? " open" : "") + '><summary><h3><span>' + esc(s.n) + "</span>" + esc(s.title) + "</h3></summary>"
      + (s.paragraphs || []).map(p => "<p>" + esc(p) + "</p>").join("")
      + actRowsHtml(s.rows, s.n === 1 ? discLabels : [])
      // строки источника под таблицей (оценка по объявлениям): плашка источника обязательна
      + (Array.isArray(s.source_lines) && s.source_lines.length ? actSrcLines(s.source_lines) : "")
      + (s.lists || []).map(li => {
        // список с таблицей (раздел 4, аналитика): настоящая таблица, под ней пояснения и источники
        if (li.table && Array.isArray(li.table.columns) && li.table.columns.length && Array.isArray(li.table.rows)) return actDocTableHtml(li);
        const hot = (li.items || []).some(x => discText.indexOf(x) >= 0);
        return '<div class="act-list' + (hot ? " disc" : "") + '"><h4>' + esc(li.title) + "</h4><ul>"
          + (li.items || []).map(x => "<li" + (discText.indexOf(x) >= 0 ? ' class="disc"' : "") + ">" + esc(anStr(x)) + "</li>").join("") + "</ul></div>";
      }).join("")
      + "</details>").join("")
    + '<p class="act-foot">' + esc(a.footer) + "</p>"
    + "</article>";
}
function actSecOpen(n){ return CH.docOpen[n] != null ? !!CH.docOpen[n] : Number(n) <= 3; }
function actDocAllOpen(){
  const all = document.querySelectorAll("#wzBody details.act-s");
  return all.length > 0 && Array.prototype.every.call(all, d => d.open);
}
function actDocAllLabel(){
  const secs = (CH.act && CH.act.sections) || [];
  const open = document.querySelector("#wzBody details.act-s") ? actDocAllOpen() : secs.length > 0 && secs.every(s => actSecOpen(s.n));
  return open ? T("tg.act.doc_collapse", "Свернуть все разделы") : T("tg.act.doc_expand", "Развернуть все разделы");
}
function actDocAll(){
  const open = !actDocAllOpen();
  document.querySelectorAll("#wzBody details.act-s").forEach(d => { d.open = open; CH.docOpen[d.dataset.sn] = open; });
  const b = document.querySelector('#wzBody [data-go="docall"]');
  if (b) b.textContent = actDocAllLabel();
}
/* полный текст акта — для буфера обмена */
function actText(a){
  const L = [];
  if (a.insurer_known) L.push(a.insurer);
  L.push(a.title);
  (a.header || []).forEach(r => L.push(r.label + ": " + r.value));
  (a.sections || []).forEach(s => {
    L.push("", s.n + ". " + s.title);
    (s.paragraphs || []).forEach(p => L.push(p));
    (s.rows || []).forEach(r => L.push(r.label + ": " + r.value + (r.note ? " (" + r.note + ")" : "")));
    (s.source_lines || []).forEach(x => L.push(x));
    (s.lists || []).forEach(li => { L.push(li.title + ":"); (li.items || []).forEach(x => L.push("• " + x)); });
  });
  L.push("", a.footer);
  return L.join("\n");
}
/* сообщение — под той кнопкой, которую нажали: в карточке скоринга (#scoMsg) или под кнопками акта (#actMsg) */
function actMsg(html){
  const here = CH.msgAt === "sco" && $("#scoMsg") ? "#scoMsg" : "#actMsg";
  ["#actMsg", "#scoMsg"].forEach(id => { const m = $(id); if (m) m.innerHTML = id === here ? html : ""; });
}
/* скачать в обычном браузере: ссылка на файл */
function actDownload(kind){
  if (!CH.act) return;
  const a = document.createElement("a");
  a.href = actFile(kind);
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
/* ---------- в Telegram: бот присылает файл акта в чат (POST /act/{id}/send) ----------
   Скачивание из WebView Telegram на телефоне не работает, поэтому файл собирает сервер и отправляет бот.
   Кто человек, сервер узнаёт по initData мини-приложения; акт отправляется только тому, кто его сформировал. */
function actSendLabel(kind){
  if (CH.sending === kind) return T("tg.act.sending_btn", "Отправляю…");
  return kind === "docx" ? T("tg.act.send_docx", "Прислать Word в чат") : T("tg.act.send_pdf", "Прислать PDF в чат");
}
function actSendBtnHtml(kind){
  return '<button type="button" class="btn btn-secondary" data-send="' + kind + '"' + (CH.sending ? " disabled" : "")
    + (CH.sending === kind ? ' aria-busy="true"' : "") + ">" + esc(actSendLabel(kind)) + "</button>";
}
/* кнопки отправки — на месте, без перерисовки шага: сообщение под ними остаётся */
function actSendPaint(){
  document.querySelectorAll("#wzBody [data-send]").forEach(b => {
    b.disabled = !!CH.sending;
    if (b.closest(".act-btns")) b.textContent = actSendLabel(b.dataset.send);
    if (CH.sending === b.dataset.send) b.setAttribute("aria-busy", "true"); else b.removeAttribute("aria-busy");
  });
  wzBarPaint();
}
async function actSend(kind){
  if (!CH.act || CH.sending || (kind !== "pdf" && kind !== "docx")) return;
  const id = String(CH.act.id || CH.actId);
  CH.sending = kind;
  tgBusy(true);
  actSendPaint();
  actMsg(spin(T("tg.act.sending", "Отправляю акт в чат с ботом…")));
  const r = await api("/act/" + encodeURIComponent(id) + "/send",
    jsonOpts("POST", {format: kind, lang: actLang(), initData: (TG && TG.initData) || ""}));
  CH.sending = "";
  tgBusy(false);
  actSendPaint();
  if (r.ok) {
    haptic("success");
    actMsg(okHtml(T("tg.act.sent", "Акт отправлен в чат с ботом.")));
    return;
  }
  haptic("error");
  const d = r.data || {};
  if (d.code === "start_bot") {
    // бот не может написать первым: человек ещё не нажал «Старт» или остановил бота
    actMsg(errHtml(T("tg.act.send_start", "Бот пока не может вам написать. Откройте бота, нажмите «Старт» и вернитесь — затем отправьте акт ещё раз."))
      + '<div class="act-send-more">'
      + '<button type="button" class="btn btn-secondary" data-go="openbot">' + esc(T("tg.act.open_bot", "Открыть бота")) + "</button>"
      + '<button type="button" class="btn btn-secondary" data-send="' + kind + '">' + esc(T("tg.act.send_again", "Отправить ещё раз")) + "</button>"
      + "</div>");
    return;
  }
  if (d.code === "limit") {
    const min = Math.max(1, Math.ceil((Number(d.retry_after_sec) || 0) / 60));
    actMsg(errHtml(T("tg.act.send_limit", "Отправок за час больше, чем можно. Повторите через {n} мин. — или скопируйте текст акта.", {n: min})));
    return;
  }
  const said = typeof d.detail === "string" && d.detail ? d.detail : r.error;
  // 403: подпись мини-приложения не прошла (устарела) — помогает открыть приложение заново из бота
  if (d.code === "bad_init_data" || r.status === 403) {
    actMsg(errHtml(said + " " + T("tg.act.send_reopen", "Откройте приложение заново из бота и отправьте акт ещё раз.")));
    return;
  }
  // 409: к учётной записи не привязан Telegram
  if (d.code === "no_telegram" || r.status === 409) {
    actMsg(errHtml(said + " " + T("tg.act.send_link_tg", "Привяжите Telegram в профиле — и бот пришлёт акт в чат.")));
    return;
  }
  // остальное (404, 422, 503 bot_off, 502 telegram_error) — словами сервера на языке lang
  actMsg(errHtml(said));
}
function actOpenBot(){
  const url = "https://t.me/" + encodeURIComponent(botName());
  haptic("select");
  if (TG && TG.openTelegramLink) tgCall(() => TG.openTelegramLink(url));
  else window.open(url, "_blank", "noopener");
}
function actCopy(){
  const text = actText(CH.act);
  const done = () => { haptic("success"); actMsg(okHtml(T("tg.act.copied", "Текст акта скопирован — вставьте его в сообщение или документ."))); };
  const fallback = () => {
    try {
      const t = document.createElement("textarea");
      t.value = text;
      t.setAttribute("readonly", "");
      t.style.position = "fixed";
      t.style.opacity = "0";
      document.body.appendChild(t);
      t.select();
      const ok = document.execCommand("copy");
      t.remove();
      if (ok) done(); else actMsg(errHtml(T("tg.act.copy_failed", "Скопировать не получилось — выделите текст акта и скопируйте вручную.")));
    } catch (e) { actMsg(errHtml(T("tg.act.copy_failed", "Скопировать не получилось — выделите текст акта и скопируйте вручную."))); }
  };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
  else fallback();
}
/* акт с сервера на текущем языке: после перезагрузки и при смене языка */
async function actFetch(){
  const id = CH.actId, lang = actLang();
  if (!id) return;
  // номер запроса: флаг «загружаю» снимает только последний запрос; новый акт и «Новый акт» номер увеличивают
  const seq = CH.actSeq = (CH.actSeq || 0) + 1;
  CH.actBusy = true;
  CH.actErr = "";
  if (CH.wz === 3 && TAB === "chat") wzPaint(true);       // без акта — «Загружаю акт…», с актом — занятый выбор языка
  let r;
  try { r = await api("/act/" + encodeURIComponent(id) + "?lang=" + encodeURIComponent(lang)); }
  finally { if (seq === CH.actSeq) CH.actBusy = false; }
  if (seq !== CH.actSeq || id !== CH.actId || lang !== actLang()) return;   // успели начать новый акт или сменить язык ещё раз
  if (!r.ok) {
    if (r.status === 404) {
      CH.act = null;
      CH.actId = "";
      if (CH.wz === 3) CH.wz = 2;
      CH.err = T("tg.act.gone", "Акт не найден: он хранится 7 дней и открывается только у того, кто его сформировал. Сформируйте акт заново.");
      wzSave();
    } else {
      CH.actErr = T("tg.act.load_failed", "Акт не загрузился: {reason}", {reason: r.error});
      if (CH.act) { CH.actLang = CH.act.lang && CH.act.lang !== I18N_LANG ? CH.act.lang : ""; CH.err = CH.actErr; }
    }
    if (TAB === "chat") wzPaint(true);
    return;
  }
  CH.act = r.data;
  if (TAB === "chat") wzPaint(CH.wz === 3);
}
function actReset(){
  CH.queue.forEach(q => { if (q.url) URL.revokeObjectURL(q.url); });
  Object.assign(CH, {wz: 1, queue: [], filesDirty: false, session: "", ai: null, photoCount: 0, rec: [], damages: [],
    missingViews: [], requiredViews: [], suggest: [], msg: "", notes: [], warning: "", upLang: "", stale: false,
    modelN: 0, docs: [], pre: {}, preDoc: {}, seeded: false, fr: FR_OFF(), docOpen: {}, anOpen: {},
    must: {}, opt: {}, errs: {}, act: null, actId: "", actErr: "", err: "", prodQ: "", prodOpen: false, optOpen: false, kind: "",
    br: null, ct: null, xc: null, pt: null, ptAdd: false, ptOpen: {}, ptMore: {}, cfCls: "",
    cb: null, sumOpen: false, cbActOpen: null, scoImgErr: "", msgAt: "",
    ob: null, obBad: {}, obOpen: {}, obMore: {}, files: [], vp: null, pfM: {}, actLang: "",
    actBusy: false, actSeq: (CH.actSeq || 0) + 1});     // загрузка прежнего акта больше не держит «Загружаю акт…»
  if (CH.scoImg && CH.scoImg.url) URL.revokeObjectURL(CH.scoImg.url);
  CH.scoImg = null;
  mkReset();
  wzSeedRegion();
  wzSave();
  wzPaint(true);
  window.scrollTo(0, 0);
}

async function loadChat(){
  if (CH.booted) { wzPaint(true); return; }
  CH.booted = true;
  wzLoad();
  mkLoad();
  wzSeedRegion();
  if (CH.wz === 3 && !CH.actId) CH.wz = 2;
  wzPaint(true);
  chatRefs().then(() => {
    // код продукта подставлен из документа до загрузки справочника — класс берём теперь
    const p = CH.must.product_code && !CH.must.class_code ? wzProd(CH.must.product_code) : null;
    if (p) { CH.must.class_code = wzClassesOf(p)[0] || ""; wzSave(); }
    wzPart("prod"); wzPart("views"); wzPart("br");
  });
  if (CH.actId) actFetch();
}

/* ---------- смена языка: подписи из словаря; акт сервер отдаёт на новом языке (GET /act/{id}?lang=) ---------- */
function chatRelang(){
  wzPaint(true);
  // язык акта, выбранный на шаге «Акт», остаётся; не выбран — акт идёт за языком интерфейса
  if (CH.act && CH.act.lang !== actLang()) actFetch();
}

chatBoot();

