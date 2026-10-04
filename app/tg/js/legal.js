/* app/tg/js/legal.js — вкладка «Специалист» */
/* =====================================================================================
   Специалист: диалог на языке интерфейса (ru / uz / en).
   Данные: GET  /legal/suggest?lang=… — {items:[{kind,q,label}], assistant_name, assistant_role} — подсказки до первого вопроса;
           GET  /legal/faq?lang=…     — запасные подсказки, если /legal/suggest не ответил;
           POST /legal/ask            — {q, lang, ai, session_id} → {intent, context, parts, market, sources, citations, …}.
   session_id создаёт страница: один на разговор, новый — по кнопке «Новый разговор».
   История — только в памяти страницы: после перезагрузки разговор пуст (старую ленту из sessionStorage стираем).
   Перерисовываются лента и чипы; поле вопроса не трогаем — курсор остаётся на месте.
   ===================================================================================== */
const LG = {faq: [], faqLang: "", faqErr: "", busy: false, items: [], booted: false,
  sid: "", sug: [], sugLang: "", sugErr: "", name: "", role: null};
const LG_KEY = "surveyor_legal";          // ключ прежней ленты в sessionStorage — удаляется при запуске
const LG_MAX = 40;                        // столько реплик держим в разговоре
const LG_CHIPS = 6;                       // подсказок до первого вопроса
const LG_KINDS = ["market", "law", "company", "competitor", "note", "ai"];
const LG_NOT_LAW = ["market", "company", "competitor", "note"];   // не нормы: без «неофициальный перевод» и без ссылки на lex.uz
/* домен ссылки для подписи «Читать в источнике <домен>» — всегда из самой ссылки */
function lgHost(url){ try { return new URL(url).hostname.replace(/^www\./, ""); } catch (e) { return ""; } }

function lgNewSid(){
  const rnd = Math.random().toString(36).slice(2, 10);
  return ("lg_" + Date.now().toString(36) + "_" + rnd).slice(0, 64);   // латиница, цифры и «_» — до 64 знаков
}
function lgDropOld(){ try { sessionStorage.removeItem(LG_KEY); } catch (e) { /* хранилище недоступно — нечего стирать */ } }

async function lgFetchFaq(){
  const lang = I18N_LANG;
  const r = await api("/legal/faq?lang=" + encodeURIComponent(lang));
  LG.faqLang = lang;
  if (!r.ok) { LG.faqErr = r.error; LG.faq = []; return; }
  LG.faqErr = "";
  LG.faq = r.data.items || [];
}
async function lgFetchSuggest(){
  const lang = I18N_LANG;
  const r = await api("/legal/suggest?lang=" + encodeURIComponent(lang));
  LG.sugLang = lang;
  if (!r.ok || !r.data) { LG.sugErr = r.error || ""; LG.sug = []; }
  else {
    LG.sugErr = "";
    LG.sug = (Array.isArray(r.data.items) ? r.data.items : []).filter(x => x && lgStr(x.q));
    if (lgStr(r.data.assistant_name)) LG.name = lgStr(r.data.assistant_name);
    if (r.data.assistant_role && typeof r.data.assistant_role === "object") LG.role = r.data.assistant_role;
  }
  if (!LG.sug.length && LG.faqLang !== lang) await lgFetchFaq();     // запас — частые вопросы
  lgPaintHead();
  lgPaintChips();
}
function lgPaintHead(){
  const n = $("#lgName"), r = $("#lgRole");
  if (n) n.textContent = LG.name || T("tg.lg.bot_name", "Специалист INSON");
  const role = LG.role && lgStr(LG.role[I18N_LANG] || LG.role.ru);
  if (r) r.textContent = role || T("tg.lg.bot_role", "страхование имущества: рынок, закон, тарифы");
}

/* уточнения после ответа — формируются здесь, по intent ответа */
function lgFollowUps(it){
  const intent = it && it.res && it.res.intent;
  if (intent === "market") {
    return [{q: T("tg.lg.fu_year", "А год назад?")}, {q: T("tg.lg.fu_class8", "А по классу 8?")}, {q: T("tg.lg.fu_inson", "А у INSON?")}];
  }
  if (intent === "legal") {
    const to = I18N_LANG === "uz" ? "ru" : "uz";
    return [{q: T("tg.lg.fu_article", "Какая статья?")}, {q: T("tg.lg.fu_act", "Что это значит для акта?")},
      {q: to === "uz" ? T("tg.lg.fu_in_uz", "На узбекском") : T("tg.lg.fu_in_ru", "На русском"), redo: it.q, lang: to}];
  }
  return [];
}
function lgPaintChips(){
  const box = $("#lgChips");
  if (!box) return;
  if (LG.busy) { box.innerHTML = ""; return; }
  const last = LG.items[LG.items.length - 1];
  let list = [];
  if (!LG.items.length) {
    list = LG.sug.length ? LG.sug.slice(0, LG_CHIPS).map(x => ({q: lgStr(x.q), label: lgStr(x.label) || lgStr(x.q)}))
      : LG.faq.slice(0, LG_CHIPS).map(x => ({q: lgStr(x.q), label: lgStr(x.q)}));
    if (!list.length) {
      box.innerHTML = LG.faqErr || LG.sugErr
        ? '<p class="note err">' + esc(T("tg.lg.faq_failed", "Частые вопросы не загрузились: {reason}", {reason: LG.sugErr || LG.faqErr})) + "</p>"
        : (LG.sugLang === I18N_LANG ? '<p class="note">' + esc(T("tg.lg.faq_empty", "Готовых вопросов пока нет — спросите своими словами.")) + "</p>" : "");
      return;
    }
  } else if (last && last.res) {
    list = lgFollowUps(last);
  }
  box.innerHTML = list.map((x, i) => '<button type="button" class="qchip" data-ci="' + i + '">' + esc(x.label || x.q) + "</button>").join("");
  box.querySelectorAll("[data-ci]").forEach(b => {
    const x = list[+b.dataset.ci];
    b.onclick = () => x.redo ? lgAsk(x.redo, x.lang, x.q) : lgAsk(x.q);
  });
}

function lgSourceName(src, ai){
  if (ai === "ok") return T("tg.lg.src_ai", "ответ ИИ — проверьте по тексту акта");
  if (src === "faq") return T("tg.lg.src_faq", "готовый ответ юриста");
  if (src === "passages") return T("tg.lg.src_law", "найдено в тексте закона");
  return T("tg.lg.src_none", "в законах не нашлось");
}
function lgLangName(code){
  switch (code) {
    case "ru": return T("tg.lg.lang_ru", "русский текст");
    case "uz": return T("tg.lg.lang_uz", "узбекский текст");
    case "en": return T("tg.lg.lang_en", "английский текст");
    default: return code || "";
  }
}
/* Живой поиск на lex.uz (app/legal_live.py): поле live ответа и пометки у цитат.
   live.status: found — акт найден и сохранён сейчас; found_base — найден раньше, уже в базе;
   not_found / unavailable / limit / off — одна строка-примечание; not_needed — ничего не пишем. */
const lgStr = v => (typeof v === "string" || typeof v === "number") ? String(v).trim() : "";
const lgUrl = v => { const s = lgStr(v); return /^https?:\/\//i.test(s) ? s : ""; };
const lgA = (url, text) => '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(text) + "</a>";
function lgLive(res){ const lv = res && res.live; return lv && typeof lv === "object" ? lv : {}; }
function lgLiveLine(lv){
  let t = "";
  switch (lv.status) {
    case "found_base": t = T("legal.live.found_base", "Акт найден на lex.uz раньше и уже есть в базе — цитата из сохранённого текста"); break;
    case "not_found": t = T("legal.live.not_found", "На lex.uz подходящей действующей нормы тоже не нашлось"); break;
    case "unavailable": t = T("legal.live.unavailable", "lex.uz сейчас недоступен — ответ дан по локальной базе. Спросите ещё раз через несколько минут"); break;
    case "limit": t = T("legal.live.limit", "Поиск на lex.uz на час приостановлен: исчерпан предел обращений. Ответ дан по локальной базе"); break;
    case "off": t = T("legal.live.off", "Поиск на lex.uz отключён администратором — ответ дан по локальной базе"); break;
  }
  return t ? '<p class="note lglivenote">' + esc(t) + "</p>" : "";
}
/* из общей пометки сервера убираем то, что уже показано отдельной строкой или ссылкой */
function lgNoteText(res){
  let n = lgStr(res.note);
  const lv = lgLive(res), shown = ["found", "found_base", "not_found", "unavailable", "limit", "off"];
  if (!n || shown.indexOf(lv.status) < 0) return n;
  const off = lgUrl(lv.official_url);
  if (off) n = n.split(" (" + off + ")").join("");
  const cut = [];
  if (lv.status === "found" && lgStr(lv.label)) cut.push(lgStr(lv.label) + ": " + (lgStr(lv.badge) || lgStr(lv.act)));
  if (lgStr(lv.text)) cut.push(lgStr(lv.text));
  (Array.isArray(lv.skipped) ? lv.skipped : []).forEach(s => {
    if (s && lgStr(s.reason)) cut.push(lgStr(s.reason) + ": " + (lgStr(s.badge) || lgStr(s.act)) + " — " + (lgUrl(s.official_url) || lgUrl(s.url)));
  });
  cut.forEach(f => { n = n.split("; " + f).join("").split(f + "; ").join("").split(f).join(""); });
  return n.replace(/^[;\s]+|[;\s]+$/g, "");
}
/* «сайт недоступен / предел» сервер дописывает и в текст ответа — там его показывает строка-примечание */
function lgAnswerText(res, a){
  const t = lgStr(a.text), lv = lgLive(res), tail = " " + lgStr(lv.text) + ".";
  return (lv.status === "unavailable" || lv.status === "limit") && lgStr(lv.text) && t.endsWith(tail)
    ? t.slice(0, -tail.length) : t;
}
function lgSkipped(lv){
  return (Array.isArray(lv.skipped) ? lv.skipped : []).map(s => {
    const url = s && (lgUrl(s.official_url) || lgUrl(s.url));
    if (!url) return "";
    return '<div class="srcbar lgskip"><span>' + esc(T("legal.live.uz_only", "{act}: на lex.uz текст только на узбекском — цитату по-русски дать нельзя",
      {act: lgStr(s.badge) || lgStr(s.act) || "lex.uz"})) + "</span>"
      + lgA(url, T("legal.live.open_original", "Открыть оригинал на lex.uz")) + "</div>";
  }).join("");
}
function lgCitation(c, lv){
  lv = lv || {};
  const head = lgStr(c.act) + (lgStr(c.unit) ? ", " + lgStr(c.unit) : "");
  const url = lgUrl(c.url);
  const fresh = c.live === true;
  const badge = lgStr(c.act_badge);
  const official = c.found_on === "lex.uz" && lgUrl(lv.official_url) !== url && (!lgUrl(lv.url) || lgUrl(lv.url) === url)
    ? lgUrl(lv.official_url) : "";
  const act = c.actuality && typeof c.actuality === "object" ? c.actuality : null;
  const actUrl = act ? (lgUrl(act.url) || url) : "";
  const notLaw = LG_NOT_LAW.indexOf(c.source_kind) >= 0;
  const srcLabel = c.source_kind === "competitor" && !lgStr(c.source_label)
    ? T("tg.lg.src_competitor", "документ другого страховщика — не норма") : c.source_label;
  return '<div class="cit' + (fresh ? " live" : "") + '">'
    + (fresh ? '<div class="lglive"><span class="tag">' + esc(T("legal.live.found", "найдено на lex.uz сейчас")) + "</span>"
      + (badge ? "<span>" + esc(T("legal.live.in_force", "действует · {badge}", {badge: badge})) + "</span>" : "") + "</div>" : "")
    + "<b>" + esc(head) + "</b>"
    + (lgStr(c.quote) ? "<q>" + esc(lgStr(c.quote)) + "</q>" : "")
    + '<div class="lgmeta">' + lgKindBadge(c.source_kind, srcLabel)
    + (notLaw ? "" : '<span class="tag' + (c.official ? "" : " off") + '">'
      + esc(c.official ? T("tg.lg.official", "официальный текст") : T("tg.lg.unofficial", "неофициальный перевод")) + "</span>")
    + (lgLangName(c.language) ? "<span>" + esc(lgLangName(c.language)) + "</span>" : "") + "</div>"
    + (act ? '<p class="warnline">' + esc(lgStr(act.redaction)
        ? T("legal.actuality.changed_since", "Акт изменился на lex.uz (редакция от {date}) — сверьте цитату с действующей редакцией", {date: lgStr(act.redaction)})
        : T("legal.actuality.changed", "Акт изменился на lex.uz — сверьте цитату с действующей редакцией"))
      + (actUrl ? " " + lgA(actUrl, T("legal.actuality.open", "Открыть действующую редакцию")) : "") + "</p>" : "")
    + (notLaw ? (url ? '<div class="srcbar">' + lgA(url, T("tg.lg.read_in", "Читать в источнике {site}", {site: lgHost(url) || url})) + "</div>" : "") + "</div>" : "")
    + (notLaw ? "" : '<div class="srcbar">'
    + (url ? lgA(url, T("tg.lg.read_source", "Читать в источнике lex.uz"))
      : "<span>" + esc(T("legal.src_missing", "Ссылки на акт нет — найдите его по названию")) + "</span>" + lgA("https://lex.uz/", T("tg.lg.read_source", "Читать в источнике lex.uz")))
    + (official ? lgA(official, T("legal.live.official_link", "Официальный текст на узбекском")) : "")
    + "</div></div>");
}
/* бейдж источника: цвет по kind (market — синий, law — зелёный, company — фиолетовый, competitor — оранжевый, note — серый, ai — жёлтый) */
function lgKindBadge(kind, label){
  const k = LG_KINDS.indexOf(kind) >= 0 ? kind : "none";
  const t = lgStr(label);
  return t ? '<span class="sk sk-' + k + '">' + esc(t) + "</span>" : "";
}
/* таблица рынка: числа выводим как пришли — разделители уже расставил сервер */
function lgCell(v){
  if (v == null) return "";
  if (typeof v === "object") return lgStr(v.text != null ? v.text : v.value);
  return String(v);
}
function lgTable(t){
  if (!t || typeof t !== "object") return "";
  const cols = Array.isArray(t.columns) ? t.columns : [], rows = (Array.isArray(t.rows) ? t.rows : []).slice(0, 10);
  if (!cols.length || !rows.length) return "";
  const key = c => (c && typeof c === "object") ? (c.key || c.label) : c;
  const head = c => (c && typeof c === "object") ? lgStr(c.label || c.key) : lgStr(c);
  const num = s => /\d/.test(s) && /^[-+−]?[\d\s  .,]+%?$/.test(s);
  return '<div class="lgtw"><table class="lgt"><thead><tr>' + cols.map(c => "<th>" + esc(head(c)) + "</th>").join("") + "</tr></thead><tbody>"
    + rows.map(r => "<tr>" + cols.map((c, i) => {
      const s = lgCell(Array.isArray(r) ? r[i] : (r && typeof r === "object" ? r[key(c)] : ""));
      return "<td" + (i > 0 && num(s) ? ' class="n"' : "") + ">" + esc(s) + "</td>";
    }).join("") + "</tr>").join("") + "</tbody></table></div>";
}
/* плашка источника: «Читать в источнике napp.uz / lex.uz» — только для ссылок http/https */
function lgSourceBar(s){
  if (!s || typeof s !== "object") return "";
  const url = lgUrl(s.url);
  const site = url ? lgHost(url) : "";
  const what = [lgStr(s.title), lgStr(s.date_text) || lgStr(s.date), lgStr(s.period)].filter(Boolean).join(" · ");
  if (!what && !url) return "";
  const lab = s.kind === "competitor" && !lgStr(s.label) ? T("tg.lg.src_competitor", "документ другого страховщика — не норма") : s.label;
  return '<div class="srcbar lgsrc">' + lgKindBadge(s.kind, lab) + (what ? "<span>" + esc(what) + "</span>" : "")
    + (url ? lgA(url, T("tg.lg.read_in", "Читать в источнике {site}", {site: site || url})) : "") + "</div>";
}
function lgUserBubble(it){
  return '<div class="lgu">' + esc(it.shown || it.q) + "</div>";
}
function lgReply(it){
  if (it.pending) {
    return '<div class="lga"><p class="lgthink">' + spin(T("tg.lg.thinking", "специалист думает…")) + "</p>"
      + '<div class="lgskel"><span></span><span></span><span></span></div></div>';
  }
  if (it.err) {
    return '<div class="lga err"><p class="note err">'
      + esc(T("tg.lg.failed", "Ответ не получен: {reason}. Задайте вопрос ещё раз.", {reason: it.err})) + "</p></div>";
  }
  const res = it.res || {}, a = res.answer || {}, ai = (res.ai || {}).status || "off";
  const parts = res.parts && typeof res.parts === "object" ? res.parts : {};
  const data = parts.data && typeof parts.data === "object" ? parts.data : null;
  const op = parts.opinion && typeof parts.opinion === "object" && lgStr(parts.opinion.text) ? parts.opinion : null;
  const mk = res.market && typeof res.market === "object" ? res.market : null;
  const ctx = res.context && typeof res.context === "object" ? res.context : {};
  const cits = Array.isArray(res.citations) ? res.citations : [];
  const srcs = Array.isArray(res.sources) ? res.sources : [];
  const rel = (res.related || []).filter(x => String(x.q || "").trim() !== it.q);
  const conf = a.confidence != null ? Math.round(a.confidence * 100) : null;
  const lv = lgLive(res), note = lgNoteText(res);
  const dataText = (data && lgStr(data.text)) || lgAnswerText(res, a);
  const kind = (data && data.source_kind) || res.source_kind;
  const label = (data && data.source_label) || res.source_label;
  return '<div class="lga">'
    + (ctx.follow_up ? '<p class="lgfu">' + esc(T("tg.lg.follow_up", "уточнение к предыдущему вопросу")) + "</p>" : "")
    + '<div class="lgblk"><div class="lgblk-h">' + esc((data && lgStr(data.label)) || T("tg.lg.data", "Данные")) + lgKindBadge(kind, label) + "</div>"
    + '<p class="lgans">' + esc(dataText || T("tg.lg.none", "В текстах актов ответа не нашлось. Спросите короче или другими словами.")) + "</p>"
    + (mk ? lgTable(mk.table) + (lgStr(mk.ytd_note) ? '<p class="lgytd">' + esc(lgStr(mk.ytd_note)) + "</p>" : "") : "")
    + "</div>"
    + (op ? '<div class="lgblk lgop"><div class="lgblk-h">' + esc(lgStr(op.label) || T("tg.lg.opinion", "Вывод специалиста"))
      + '<span class="sk ' + (op.by === "ai" ? "sk-ai" : "sk-note") + '">'
      + esc(op.by === "ai" ? T("tg.lg.by_ai", "ИИ") : T("tg.lg.by_rules", "по правилам")) + "</span></div>"
      + '<p class="lgans">' + esc(lgStr(op.text)) + "</p></div>" : "")
    + lgLiveLine(lv)
    + (note ? '<p class="note mt-s2">' + esc(note) + "</p>" : "")
    + (!op && ai === "ok" && res.ai.text ? '<p class="lgans mt-s2">' + esc(res.ai.text) + "</p>" : "")
    + lgSkipped(lv)
    + cits.map(c => lgCitation(c || {}, lv)).join("")
    + srcs.map(lgSourceBar).join("")
    + '<div class="lgmeta">' + (srcs.length || label ? "" : '<span class="tag' + (a.source === "faq" ? "" : " off") + '">' + esc(lgSourceName(a.source, ai)) + "</span>")
    + (conf != null ? "<span>" + esc(T("tg.lg.match", "совпадение {n}%", {n: conf})) + "</span>" : "")
    + (res.took_ms != null ? "<span>" + esc(T("tg.lg.took", "ответ за {n} мс", {n: res.took_ms})) + "</span>" : "")
    + (res.cached ? "<span>" + esc(T("tg.lg.cached", "из памяти")) + "</span>" : "") + "</div>"
    + (rel.length && res.intent !== "market" ? '<div class="h3">' + esc(T("tg.lg.related", "Похожие вопросы")) + "</div>"
      + '<div class="qchips">' + rel.slice(0, 3).map(x => '<button type="button" class="qchip" data-rel="' + esc(x.q) + '">' + esc(x.q) + "</button>").join("") + "</div>" : "")
    + "</div>";
}
function lgPaintFeed(scroll){
  const box = $("#lgFeed");
  if (!box) return;
  box.innerHTML = LG.items.map(it => lgUserBubble(it) + lgReply(it)).join("");
  box.querySelectorAll("[data-rel]").forEach(b => { b.onclick = () => lgAsk(b.dataset.rel); });
  if (scroll && box.lastElementChild) {
    try { box.lastElementChild.scrollIntoView({block: "nearest", behavior: "smooth"}); } catch (e) { /* старый браузер — без прокрутки */ }
  }
}
function lgPaintMsg(text, kind){
  const m = $("#lgMsg");
  if (!m) return;
  m.innerHTML = !text ? "" : kind === "err" ? errHtml(text) : '<span class="note">' + esc(text) + "</span>";
}
/* роль специалиста может прийти и в ответе — на трёх языках */
function lgTakeRole(res){
  if (res && res.assistant_role && typeof res.assistant_role === "object") { LG.role = res.assistant_role; lgPaintHead(); }
}
/* текст ошибки — detail сервера, если он строка; иначе общее объяснение */
function lgErrText(r){
  const d = r && r.data && r.data.detail;
  return typeof d === "string" && d.trim() ? d.trim() : r.error;
}

/* q — текст вопроса (из чипа) или пусто (из поля); lang — язык ответа, если не язык интерфейса; shown — что видно в реплике */
async function lgAsk(q, lang, shown){
  const field = $("#lgQ");
  const fromChip = typeof q === "string";
  const text = String(fromChip ? q : (field ? field.value : "")).trim();
  if (!text) { lgPaintMsg(T("tg.lg.empty", "Напишите вопрос — хотя бы несколько слов."), "err"); if (field) field.focus(); return; }
  if (LG.busy) return;
  if (!LG.sid) LG.sid = lgNewSid();
  lgPaintMsg("");
  if (field && !fromChip) field.value = "";
  const ask = typeof lang === "string" && lang ? lang : I18N_LANG;
  const item = {q: text, shown: typeof shown === "string" ? shown : "", pending: true, lang: ask};
  LG.items.push(item);
  if (LG.items.length > LG_MAX) LG.items = LG.items.slice(-LG_MAX);
  LG.busy = true;
  lgPaintFeed(true);                               // реплика и «специалист думает…» видны сразу
  lgPaintChips();
  syncTgButtons();
  tgBusy(true);
  const sid = LG.sid;
  const body = Object.assign({q: text, lang: I18N_LANG, ai: false}, {lang: ask, session_id: sid});
  const r = await api("/legal/ask", jsonOpts("POST", body));
  tgBusy(false);
  LG.busy = false;
  if (sid !== LG.sid) { syncTgButtons(); return; }   // пока ждали ответ, начали новый разговор
  delete item.pending;
  if (!r.ok) { item.err = lgErrText(r); haptic("error"); }
  else {
    item.res = r.data || {};
    item.lang = item.res.lang || ask;
    if (/^[A-Za-z0-9_.:-]{1,64}$/.test(lgStr(item.res.session_id))) LG.sid = lgStr(item.res.session_id);
    lgTakeRole(item.res);
  }
  lgPaintFeed(true);
  lgPaintChips();
  syncTgButtons();
}
/* «Новый разговор»: новый session_id, пустая история, снова подсказки */
function lgReset(){
  LG.sid = lgNewSid();
  LG.items = [];
  LG.busy = false;
  tgBusy(false);
  lgPaintMsg("");
  lgPaintFeed();
  lgPaintChips();
  syncTgButtons();
  const f = $("#lgQ");
  if (f) f.focus();
}

async function loadLegal(){
  if (!LG.booted) {
    LG.booted = true;
    lgDropOld();
    LG.sid = lgNewSid();
    $("#lgSend").onclick = () => lgAsk();
    $("#lgNew").onclick = lgReset;
    $("#lgQ").addEventListener("keydown", e => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); lgAsk(); }
    });
  }
  lgPaintHead();
  lgPaintFeed();
  if (LG.sugLang !== I18N_LANG) {
    if (!LG.items.length) $("#lgChips").innerHTML = anLoadingCard();
    await lgFetchSuggest();
  } else lgPaintChips();
}
/* смена языка: подписи и подсказки — на новом языке; разговор не теряем и заново не спрашиваем */
function lgRepaint(){
  lgPaintHead();
  lgPaintFeed();
  lgPaintChips();
  if (LG.booted && LG.sugLang !== I18N_LANG) lgFetchSuggest();
}

