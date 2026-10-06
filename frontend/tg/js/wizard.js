/* app/tg/js/wizard.js — ИИ-сюрвейер: шаги «Фото» и «Проверить» (загрузка, распознанное, поля класса, объекты) */
/* =====================================================================================
   ИИ-сюрвейер — лёгкая версия (ТЗ 2.0 от 29.09.2026: docs/ТЗ — Мини-приложение ИИ-сюрвейер (лёгкая версия).md).
   Мастер из трёх шагов:
     1 «Фото»      — камера, галерея и файлы, перетаскивание, вставка из буфера. Файлы копятся в списке
                     и по «Дальше» уходят одним POST /act/photos (до 10 файлов; JPG, PNG, PDF, DOCX, XLSX — WEBP
                     и HEIC переводятся в JPG здесь же). Снимки читает языковая модель; DOCX, XLSX и PDF с текстом
                     сервер разбирает без неё («документ разобран · вид»), их prefill подставляется на шаге 2.
                     Шаг 2 «Дополнительно»: защита, сейсмичность, конструкция, деятельность (по классу) и франшиза
                     сотрудника; шаг 3: франшиза (применена / предложена), PML/EML/MFL, рекомендации, альтернативы.
     2 «Проверить» — распознанное (у каждого значения источник и «проверьте», любое можно исправить;
                     разные значения одного поля из разных источников — расхождение), видимые повреждения,
                     каких ракурсов не хватает, четыре обязательных поля и «Дополнительно».
     3 «Акт»       — крупно: решение, уровень риска (три уровня), тариф и премия, франшиза, сумма к стоимости;
                     ниже — сам акт из пяти разделов; Word и PDF (в Telegram бот присылает файл в чат,
                     в браузере — скачивание), копия текста, «Исправить данные», «Новый акт».
   Данные: POST /act/photos, POST /act/make, GET /act/{id}?lang=…, GET /act/{id}.docx|.pdf?lang=…,
           POST /act/{id}/send (app/act.py);
           GET /reference/products, /reference/classes, /reference/checklists — список продуктов.
   Все цифры акта считает сервер (app/act_engine.py): страница их только показывает.
   В sessionStorage — шаг, введённые поля, номер сессии фото, распознанные значения и id акта. Имена файлов,
   заметки модели и тексты сервера не сохраняются. Пока человек печатает, поле не перерисовывается (wzPart).
   ===================================================================================== */
const CH_KEY = "surveyor_act";
const CH_REGION = "surveyor_region";
const CH_MAX_FILES = 10;                   // app/act.py: MAX_FILES
const CH_MAX_MB = 15;                      // app/act.py: MAX_BYTES
const CH_EXT = ["pdf", "jpg", "jpeg", "png", "docx", "xlsx"];
// DOCX и XLSX сервер разбирает без модели (app/act_extras.parse_document); текстовый PDF — тоже
const MIME_DOCS = ["application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"];
const FR_OFF = () => ({on: false, type: "unconditional", unit: "pct", pct: "", amount: null});
const CH = {booted: false, busy: false, phase: "", err: "", wz: 1, seq: 0, queue: [], upPct: 0, filesDirty: false,
  session: "", ai: null, photoCount: 0, modelN: 0, rec: [], damages: [], missingViews: [], requiredViews: [], suggest: [],
  docs: [], pre: {}, preDoc: {}, seeded: false, fr: FR_OFF(), docOpen: {}, anOpen: {},
  msg: "", notes: [], warning: "", upLang: "", stale: false,
  must: {}, opt: {}, errs: {}, act: null, actId: "", actBusy: false, actErr: "", sending: "",
  refs: null, refsErr: "", prodQ: "", prodOpen: false, optOpen: false, later: false, kind: "",
  br: null, ct: null, xc: null,              // запрос филиала, договор, их сверка (30.09.2026)
  cb: null,                                  // отчёт кредитного бюро по заёмщику (01.10.2026)
  sumOpen: false, cbActOpen: null, scoImg: null, scoImgBusy: false, scoImgErr: "", msgAt: "",  // шаг «Акт»: скоринг
  pt: null, ptAdd: false, ptOpen: {}, ptMore: {}, cfCls: "",   // части договора; класс, чьи поля в opt.cf
  ob: null, obBad: {}, obOpen: {}, obMore: {},  // несколько объектов (парк ТС, 02.10.2026)
  files: [],                                 // файлы последней загрузки: id сервера («f1»), номер, ракурс
  vp: null, pfM: {},                         // автозаполнение ТС: подсказки сервера и что из них подставлено
  actLang: ""};                              // язык акта на шаге «Акт»; пусто — язык интерфейса
/* оценка по объявлениям (шаг 2): состояние; функции — ниже, перед «Сформировать акт» */
const MK_KEY = "surveyor_act_market";
const MK_MAX = 5;                          // app/act_market.py: MAX_SHOTS
const MK_MAX_LISTINGS = 40;                // app/act_market.py: MAX_LISTINGS
const MK_RATE = [100, 1000000];            // app/act_market.py: USD_RATE_BOUNDS, сумов за доллар
// app/act_engine.py DEFAULTS["market"]: экспертные пороги, не калиброваны; сервер в акте считает по своим настройкам
const MK_RULE = {min_listings: 3, diff_pct: 15, max_age_months: 6, outlier_low: 0.5, outlier_high: 2.0, allow_undated: false};
const MK_ADD0 = () => ({title: "", price: "", currency: "UZS", year: "", date: "", url: ""});
// declOrig — стоимость, которую заявил клиент, до замены медианой (уходит в акт как declared_value_original)
const MK_FRESH = () => ({links: null, linksKey: "", linksBusy: false, linksErr: "", q: "", qOpen: false, site: "olx",
  queue: [], seq: 0, busy: false, err: "", ss: "", shotDate: "", fx: null, need: false, info: null,
  listings: [], rate: "", addOpen: false, add: MK_ADD0(), addErr: "", mseq: 0, declOrig: 0});
const MK = MK_FRESH();

const ICON_CAM = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" d="M4 8h3l1.6-2.2h6.8L17 8h3v11H4z"/><circle cx="12" cy="13.2" r="3.4" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';
const ICON_IMG = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="3.5" y="4.5" width="17" height="15" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="9" cy="10" r="1.8" fill="none" stroke="currentColor" stroke-width="1.6"/><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" d="m4 17 5-4.5 3.5 3 3-2.5 4.5 4"/></svg>';

/* ---------- подписи: поля распознавания, источники, ракурсы, места, регионы (те же слова, что в акте) ---------- */
function recLabel(key, fallback){
  switch (key) {
    case "object_type": return T("tg.act.f.object_type", "Тип объекта");
    case "brand": return T("tg.act.f.brand", "Марка");
    case "model": return T("tg.act.f.model", "Модель");
    case "manufacture_date": return T("tg.act.f.manufacture_date", "Дата изготовления");
    case "year": return T("tg.act.f.year", "Год выпуска");
    case "serial_no": return T("tg.act.f.serial_no", "Заводской (серийный) номер");
    case "manufacturer": return T("tg.act.f.manufacturer", "Производитель");
    case "engine_no": return T("tg.act.f.engine_no", "Номер двигателя");
    case "engine_model": return T("tg.act.f.engine_model", "Модель двигателя");
    case "engine_power": return T("tg.act.f.engine_power", "Мощность двигателя");
    case "curb_mass": return T("tg.act.f.curb_mass", "Снаряжённая масса");
    case "payload": return T("tg.act.f.payload", "Грузоподъёмность");
    case "dimensions": return T("tg.act.f.dimensions", "Габариты");
    case "color": return T("tg.act.f.color", "Цвет");
    case "mileage": return T("tg.act.f.mileage", "Пробег / моточасы");
    case "location": return T("tg.act.f.location", "Место эксплуатации");
    // значения из разобранных документов (DOCX, XLSX, PDF с текстом)
    case "sum_insured": return T("tg.an.f.sum_insured", "Страховая сумма");
    case "object_value": return T("tg.an.f.object_value", "Стоимость объекта");
    case "term_days": return T("tg.act.f.term_days", "Срок страхования, дней");
    case "region": return T("tg.an.f.region", "Регион");
    case "construction": return T("tg.act.f.construction", "Конструкция, материал стен");
    case "reg_no": return T("tg.act.f.reg_no", "Государственный номер");
    case "cadastre_no": return T("tg.act.f.cadastre_no", "Кадастровый номер");
    default: return fallback || key;
  }
}
/* вид разобранного документа: код сервера (DOC_KIND_LABELS в app/act_texts.py) → подпись на языке интерфейса */
function docKindName(code, fallback){
  switch (code) {
    case "договор": return T("tg.act.dk.contract", "договор");
    case "техпаспорт": return T("tg.act.dk.tech_passport", "техпаспорт");
    case "кадастр": return T("tg.act.dk.cadastre", "кадастровый документ");
    case "отчёт оценщика": return T("tg.act.dk.valuation", "отчёт оценщика");
    case "выписка": return T("tg.act.dk.statement", "выписка");
    case "штатное расписание": return T("tg.act.dk.staff_list", "штатное расписание");
    case "прочее": return T("tg.act.dk.other", "документ");
    case "contract": return T("tg.dq.kind_ct", "договор страхования");
    case "branch_request": return T("tg.dq.kind_br", "запрос филиала");
    default: return fallback ? String(fallback) : "";
  }
}
/* уточнения для сценариев убытка — те значения, что принимает POST /act/make
   (app/act_extras.py: PROT_CODES, CONSTRUCTIONS, ACTIVITIES; подписи — app/risk_analytics.py и act_texts.RA_VALUE_LABELS) */
const PROT_CODES = {"3": ["none", "alarm", "immo", "tracker"], "8": ["none", "alarm", "alarm_guard", "sprinkler"]};
const CONSTRUCTIONS = ["reinforced", "mixed", "wood"];
const ACTIVITIES = ["office", "warehouse", "food", "flammable"];
const SEISMIC = ["5", "6", "7", "8", "9", "10"];
const FR_TYPES = ["unconditional", "conditional", "peril"];
const isProp = cls => cls === "8" || cls === "9";
function protCodes(cls){ return cls === "3" ? PROT_CODES["3"] : isProp(cls) ? PROT_CODES["8"] : []; }
function protName(cls, c){
  if (cls === "3") {
    switch (c) {
      case "none": return T("tg.act.prot3.none", "нет противоугонной системы");
      case "alarm": return T("tg.act.prot3.alarm", "сигнализация");
      case "immo": return T("tg.act.prot3.immo", "сигнализация и иммобилайзер");
      default: return T("tg.act.prot3.tracker", "спутниковый поиск");
    }
  }
  switch (c) {
    case "none": return T("tg.act.prot8.none", "нет сигнализации и охраны");
    case "alarm": return T("tg.act.prot8.alarm", "пожарная сигнализация");
    case "alarm_guard": return T("tg.act.prot8.alarm_guard", "сигнализация и охрана");
    default: return T("tg.act.prot8.sprinkler", "сигнализация, охрана, спринклеры");
  }
}
function consName(c){
  switch (c) {
    case "reinforced": return T("tg.act.cons.reinforced", "железобетон, кирпич");
    case "mixed": return T("tg.act.cons.mixed", "смешанные конструкции");
    default: return T("tg.act.cons.wood", "дерево, сэндвич-панели");
  }
}
function actvName(c){
  switch (c) {
    case "office": return T("tg.act.actv.office", "офис, торговля");
    case "warehouse": return T("tg.act.actv.warehouse", "склад общего назначения");
    case "food": return T("tg.act.actv.food", "пищевое производство");
    default: return T("tg.act.actv.flammable", "работа с горючими материалами");
  }
}
function frTypeName(c){
  switch (c) {
    case "conditional": return T("tg.act.frt.conditional", "условная");
    case "peril": return T("tg.act.frt.peril", "по отдельному риску");
    default: return T("tg.act.frt.unconditional", "безусловная");
  }
}
/* число знаков после запятой — сколько есть у значения (до 4) */
const dp = v => Number(v) % 1 ? Math.min(4, (String(v).split(".")[1] || "").length) : 0;
function srcName(s){
  switch (s) {
    case "photo": return T("tg.act.src.photo", "с фото объекта");
    case "plate": return T("tg.act.src.plate", "с заводской таблички");
    case "document": return T("tg.act.src.document", "из документа");
    case "marking": return T("tg.act.src.marking", "с маркировки на кузове");
    case "input": return T("tg.act.src.input", "введено сотрудником");
    default: return "";
  }
}
function viewName(v){
  switch (v) {
    case "front": return T("tg.act.view.front", "спереди");
    case "back": return T("tg.act.view.back", "сзади");
    case "left": return T("tg.act.view.left", "левый борт");
    case "right": return T("tg.act.view.right", "правый борт");
    case "plate": return T("tg.act.view.plate", "заводская табличка");
    case "odometer": return T("tg.act.view.odometer", "счётчик пробега или моточасов");
    case "document": return T("tg.act.view.document", "снимок документа");
    case "interior": return T("tg.act.view.interior", "внутри");
    case "facade": return T("tg.act.view.facade", "фасад");
    case "roof": return T("tg.act.view.roof", "кровля");
    case "electrical": return T("tg.act.view.electrical", "электрощит");
    case "fire_safety": return T("tg.act.view.fire_safety", "средства пожаротушения");
    case "general": return T("tg.act.view.general", "общий вид");
    case "installation": return T("tg.act.view.installation", "место установки");
    case "packaging": return T("tg.act.view.packaging", "упаковка");
    case "marking": return T("tg.act.view.marking", "маркировка");
    case "transport": return T("tg.act.view.transport", "транспорт");
    default: return T("tg.act.view.other", "другое");
  }
}
const LOCATIONS = ["open_area", "construction", "port", "guarded", "closed_storage", "other"];
function locName(c){
  switch (c) {
    case "open_area": return T("tg.act.loc.open_area", "открытая площадка");
    case "construction": return T("tg.act.loc.construction", "строительная площадка");
    case "port": return T("tg.act.loc.port", "порт");
    case "guarded": return T("tg.act.loc.guarded", "охраняемая территория");
    case "closed_storage": return T("tg.act.loc.closed_storage", "закрытое хранение");
    default: return T("tg.act.loc.other", "другое место");
  }
}
/* регион уходит в акт кодом (tashkent_region): сервер находит его в словаре tg.act.reg.* и печатает на языке акта */
const REGIONS = ["tashkent_city", "tashkent_region", "andijan", "bukhara", "jizzakh", "kashkadarya", "navoi",
  "namangan", "samarkand", "surkhandarya", "syrdarya", "fergana", "khorezm", "karakalpakstan", "uz_all", "other"];
function regionName(c){
  switch (c) {
    case "tashkent_city": return T("tg.act.reg.tashkent_city", "город Ташкент");
    case "tashkent_region": return T("tg.act.reg.tashkent_region", "Ташкентская область");
    case "andijan": return T("tg.act.reg.andijan", "Андижанская область");
    case "bukhara": return T("tg.act.reg.bukhara", "Бухарская область");
    case "jizzakh": return T("tg.act.reg.jizzakh", "Джизакская область");
    case "kashkadarya": return T("tg.act.reg.kashkadarya", "Кашкадарьинская область");
    case "navoi": return T("tg.act.reg.navoi", "Навоийская область");
    case "namangan": return T("tg.act.reg.namangan", "Наманганская область");
    case "samarkand": return T("tg.act.reg.samarkand", "Самаркандская область");
    case "surkhandarya": return T("tg.act.reg.surkhandarya", "Сурхандарьинская область");
    case "syrdarya": return T("tg.act.reg.syrdarya", "Сырдарьинская область");
    case "fergana": return T("tg.act.reg.fergana", "Ферганская область");
    case "khorezm": return T("tg.act.reg.khorezm", "Хорезмская область");
    case "karakalpakstan": return T("tg.act.reg.karakalpakstan", "Республика Каракалпакстан");
    case "uz_all": return T("tg.act.reg.uz_all", "Республика Узбекистан");
    case "other": return T("tg.act.reg.other", "Другое (вне списка, территория текстом)");
    default: return "";
  }
}
/* группа объекта в ракурсах шаблона класса (required_views: vehicle, special, property, equipment, cargo) */
function groupName(g){
  switch (g) {
    case "special": return T("tg.act.grp.special", "Спецтехника");
    case "vehicle": return T("tg.act.grp.vehicle", "Транспорт и спецтехника");
    case "property": return T("tg.act.grp.property", "Здание, помещение");
    case "equipment": return T("tg.act.grp.equipment", "Оборудование");
    case "cargo": return T("tg.act.grp.cargo", "Груз");
    default: return T("tg.act.grp.other", "Объект класса");
  }
}

/* ---------- состояние: sessionStorage до закрытия вкладки ---------- */
function wzSave(){
  try {
    sessionStorage.setItem(CH_KEY, JSON.stringify({wz: CH.wz, session: CH.session, ai: CH.ai, photos: CH.photoCount,
      rec: CH.rec.map(r => ({key: r.key, value: r.value, source: r.source, file: r.file || null, orig: r.orig || null})),
      damages: CH.damages.map(d => ({what: d.what, where: d.where || null})),
      missing: CH.missingViews, required: CH.requiredViews, suggest: CH.suggest,
      // разобранные документы — только номер в запросе, вид и число значений; франшиза и пометки «из документа»
      docs: CH.docs.map(d => ({index: d.index, kind: d.kind, text_layer: d.text_layer, values: d.values})), model: CH.modelN,
      pre: CH.pre, preDoc: CH.preDoc, seeded: CH.seeded, fr: CH.fr,
      must: CH.must, opt: CH.opt, act: CH.actId, kind: CH.kind,
      // запрос филиала и договор: прочитанные поля с правками сотрудника — до закрытия вкладки
      br: CH.br, ct: CH.ct, xc: CH.xc, cb: CH.cb,
      // части договора и поля класса (opt.cf) — до закрытия вкладки
      pt: CH.pt, ptAdd: CH.ptAdd, cfCls: CH.cfCls,
      // парк ТС, файлы загрузки (без имён), автозаполнение ТС (без пояснений модели), язык акта
      ob: CH.ob, files: CH.files.map(f => ({id: f.id, n: f.n, view: f.view})), vp: vpSave(CH.vp), pfM: pfSaveMap(CH.pfM),
      actLang: CH.actLang}));
  } catch (e) { /* без sessionStorage мастер живёт до перезагрузки */ }
}
function wzLoad(){
  let s = null;
  try { s = JSON.parse(sessionStorage.getItem(CH_KEY) || "null"); } catch (e) { s = null; }
  if (!s || typeof s !== "object") return;
  const arr = sArr, obj = sObj;
  CH.wz = [1, 2, 3].indexOf(s.wz) >= 0 ? s.wz : 1;
  CH.session = typeof s.session === "string" ? s.session : "";
  CH.ai = typeof s.ai === "boolean" ? s.ai : null;
  CH.photoCount = Number(s.photos) || 0;
  CH.rec = arr(s.rec).filter(r => r && typeof r.key === "string" && r.value != null)
    .map(r => ({key: r.key, value: String(r.value), source: String(r.source || "photo"), file: r.file || null,
      orig: r.orig && typeof r.orig === "object" ? {value: String(r.orig.value == null ? "" : r.orig.value), source: String(r.orig.source || "")} : null}));
  CH.damages = arr(s.damages).filter(d => d && d.what).map(d => ({what: String(d.what), where: d.where ? String(d.where) : null}));
  CH.missingViews = arr(s.missing).map(String);
  CH.requiredViews = arr(s.required).map(String);
  CH.suggest = arr(s.suggest).map(String);
  CH.must = obj(s.must);
  CH.opt = obj(s.opt);
  CH.docs = arr(s.docs).filter(d => d && Number.isInteger(d.index))
    .map(d => ({index: d.index, kind: d.kind ? String(d.kind) : null, text_layer: !!d.text_layer, values: Number(d.values) || 0}));
  CH.modelN = Number(s.model) || 0;
  CH.pre = obj(s.pre);
  CH.preDoc = obj(s.preDoc);
  CH.seeded = !!s.seeded;
  const f = obj(s.fr);
  CH.fr = {on: !!f.on, type: FR_TYPES.indexOf(f.type) >= 0 ? f.type : "unconditional", unit: f.unit === "amount" ? "amount" : "pct",
    pct: typeof f.pct === "string" ? f.pct.slice(0, 6) : "", amount: Number(f.amount) > 0 ? Number(f.amount) : null};
  CH.actId = typeof s.act === "string" && /^[0-9a-f]{16}$/.test(s.act) ? s.act : "";
  CH.kind = typeof s.kind === "string" && /^[a-z_]{1,40}$/.test(s.kind) ? s.kind : "";
  CH.br = dqBrFrom(s.br);
  CH.ct = dqCtFrom(s.ct);
  CH.xc = s.xc ? dqXcFrom(Object.assign({available: true}, obj(s.xc))) : null;
  CH.cb = cbLoad(s.cb);
  CH.pt = ptLoad(s.pt);
  CH.ptAdd = !!s.ptAdd;
  CH.cfCls = typeof s.cfCls === "string" ? s.cfCls.slice(0, 10) : "";
  if (CH.opt.cf != null && (typeof CH.opt.cf !== "object" || Array.isArray(CH.opt.cf))) delete CH.opt.cf;
  CH.ob = obLoad(s.ob);
  CH.files = arr(s.files).filter(f => f && /^f\d{1,2}$/.test(String(f.id))).slice(0, CH_MAX_FILES)
    .map(f => ({id: String(f.id), n: Number(f.n) || 0, view: typeof f.view === "string" ? f.view.slice(0, 40) : null, name: ""}));
  CH.vp = vpLoad(s.vp);
  CH.pfM = pfLoadMap(s.pfM);
  CH.actLang = ["ru", "uz", "en"].indexOf(s.actLang) >= 0 ? s.actLang : "";
  CH.stale = CH.photoCount > 0;          // файлы в памяти страницы не пережили перезагрузку
}
/* регион из прошлого раза — чтобы не выбирать его каждый раз */
function wzSeedRegion(){
  if (CH.must.region) return;
  let saved = "";
  try { saved = localStorage.getItem(CH_REGION) || ""; } catch (e) {}
  // «Другое» не подставляется: территорию каждый раз вписывают заново
  if (REGIONS.indexOf(saved) >= 0 && saved !== "other") { CH.must.region = saved; CH.seeded = true; }   // регион из документа его заменит
}

/* ---------- справочник продуктов: тот же, что у «Калькулятора» ---------- */
async function chatRefs(){
  if (CH.refs) return;
  const [p, c, l] = await Promise.all([api("/reference/products"), api("/reference/classes"), api("/reference/checklists")]);
  if (!p.ok || !c.ok) { CH.refsErr = p.error || c.error; return; }
  CH.refsErr = "";
  CH.refs = {products: (p.data || []).filter(x => !(x.status === "тест")), classes: c.data || [], lists: l.ok ? (l.data || []) : []};
}
function wzProd(code){ return ((CH.refs && CH.refs.products) || []).filter(p => p.code === code)[0] || null; }
function wzClassesOf(p){
  if (!p) return [];
  if (Array.isArray(p.classes)) return p.classes;
  return String(p.classes || "").split(",").map(x => x.trim()).filter(Boolean);
}
function wzClsName(code, fallback){
  const c = ((CH.refs && CH.refs.classes) || []).filter(x => x.code === code)[0];
  return T("tg.wz.cls." + code, fallback || (c && c.name) || code);
}
/* ставка справа в строке продукта: число — как в тарифной политике, без ставки — «по программе».
   Ставки не придумываем: текст берётся из справочника продуктов (rate_text, pricing_mode). */
function wzRate(p){
  const t = String(p.rate_text || "").trim();
  const m = /^(\d+(?:[.,]\d+)?)\s*%\s*(\S+)?$/.exec(t);
  if (m && (!m[2] || /^\S{1,6}\.$/.test(m[2]))) {
    const d = (m[1].split(/[.,]/)[1] || "").length;
    return nf(Number(m[1].replace(",", ".")), d) + " %" + (m[2] ? " " + T("tg.wz.rate_fixed", "фикс.") : "");
  }
  switch (p.pricing_mode) {
    case "программа": return T("tg.wz.rate_program", "по программе");
    case "по согласованию": return T("tg.wz.rate_agreed", "по согласованию с ЦО");
    case "генеральный договор": return T("tg.wz.rate_general", "по генеральному договору");
    case "нормативный акт": return T("tg.wz.rate_act", "по нормативному акту");
  }
  if (!t) return T("tg.wz.rate_program", "по программе");
  return I18N_LANG === "ru" ? t : T("tg.wz.rate_several", "несколько ставок");
}
/* «документов: N» по продукту — та же логика, что app/analysis_docs.required_docs без типа объекта */
function wzDocCount(p){
  const L = (CH.refs && CH.refs.lists) || [];
  if (!L.length) return null;
  const an = L.filter(c => !c.scope || c.scope === "анализ");
  const names = {};
  an.forEach(c => { if (c.scope_type === "продукт" && c.scope_code === p.code) names[c.doc_name] = 1; });
  if (Object.keys(names).length) return Object.keys(names).length;
  wzClassesOf(p).forEach(cls => an.forEach(c => {
    if (c.scope_type === "всегда" || (!(c.scope_type === "продукт") && c.scope_code === cls)) names[c.doc_name] = 1;
  }));
  return Object.keys(names).length;
}
/* класс объекта для подсказки ракурсов: выбранный класс или первый класс продукта */
function wzClass(){
  const m = CH.must;
  if (m.product_code) return wzClassesOf(wzProd(m.product_code))[0] || m.class_code || "";
  return m.class_code || "";
}

/* ---------- шаги ---------- */
function wzGo(k){
  if (k === CH.wz || CH.busy) return;
  if (k === 3 && !CH.act) return;
  CH.wz = k;
  CH.err = "";
  wzSave();
  haptic("select");
  wzPaint(true);
  window.scrollTo(0, 0);
}
function wzBack(){ if (CH.wz > 1) wzGo(CH.wz - 1); }
function wzMainLabel(){
  if (CH.wz === 1) {
    if (CH.busy) return CH.phase === "read" ? T("tg.act.reading_btn", "Читаю фото…") : T("tg.wz.uploading", "Загружаю… {n}%", {n: CH.upPct || 0});
    return CH.queue.length || CH.photoCount ? T("tg.wz.next", "Проверить материалы") : T("tg.act.no_photos", "Без фото");
  }
  if (CH.wz === 2) return CH.busy ? T("tg.act.making", "Формирую акт…") : T("tg.act.make", "Сформировать акт");
  if (!IN_TG) return T("tg.act.dl_pdf", "Скачать PDF");
  return CH.sending === "pdf" ? T("tg.act.sending_btn", "Отправляю…") : T("tg.act.send_pdf", "Прислать PDF в чат");
}
function wzMain(){
  if (CH.busy) return;
  if (CH.wz === 1) wzNext1();
  else if (CH.wz === 2) actMake();
  else if (IN_TG) actSend("pdf");
  else actDownload("pdf");
}
function wzBarPaint(){
  const b = $("#chatMain");
  if (b) { b.textContent = wzMainLabel(); b.disabled = !!CH.busy || (CH.wz === 2 && (MK.busy || !ptOk())) || (CH.wz === 3 && (!CH.act || !!CH.sending)); }
  syncTgButtons();
}
function wzStepsPaint(){
  const ol = $("#wzSteps");
  if (!ol) return;
  const names = [T("tg.wz.s1", "Фото"), T("tg.wz.s2", "Проверить"), T("tg.act.s3", "Акт")];
  ol.innerHTML = names.map((n, i) => {
    const k = i + 1, can = k < 3 || !!CH.act;
    return '<li class="' + (k === CH.wz ? "on" : k < CH.wz ? "done" : "") + '"><button type="button" data-wz="' + k + '"'
      + (can ? "" : " disabled") + (k === CH.wz ? ' aria-current="step"' : "") + "><b>" + k + "</b> " + esc(n) + "</button></li>";
  }).join("");
  ol.querySelectorAll("[data-wz]").forEach(b => { b.onclick = () => wzGo(Number(b.dataset.wz)); });
}
/* поле, в котором человек сейчас печатает, — внутри этого узла? */
function typing(el){
  const f = document.activeElement;
  if (!f || !el || !el.contains(f)) return false;
  return (f.tagName === "INPUT" && f.type !== "file" && f.type !== "checkbox") || f.tagName === "TEXTAREA" || f.tagName === "SELECT";
}
/* весь шаг; без force — не во время ввода: тогда перерисуем, когда поле потеряет фокус */
function wzPaint(force){
  const box = $("#wzBody");
  if (!box) return;
  wzStepsPaint();
  paintTop();
  if (!force && typing(box)) { CH.later = true; wzBarPaint(); return; }
  CH.later = false;
  box.innerHTML = CH.wz === 3 ? wzStep3Html() : CH.wz === 2 ? wzStep2Html() : wzStep1Html();
  wzBarPaint();
  if (CH.wz === 2) mkLinks();                 // ссылки поиска — по распознанному и на языке интерфейса
}
/* одна часть шага — не трогая поле, в котором человек печатает */
function wzPart(name){
  const map = {files: ["wzFiles", wzFilesHtml], views: ["wzViews", wzViewsHtml], rec: ["wzRec", wzRecHtml],
    prod: ["wzProd", wzProdHtml], list: ["wzProdList", wzProdListHtml], region: ["wzRegion", wzRegionHtml],
    more: ["wzMoreBox", wzMoreHtml], fr: ["wzFr", wzFrHtml], term: ["wzTerm", wzTermHtml],
    tpl: ["wzTpl", tplCardHtml], parts: ["wzParts", ptCardHtml],
    obmode: ["wzObMode", obModeHtml], sums: ["wzSums", wzSumsHtml], obj: ["wzObj", obCardHtml],
    br: ["wzBr", brCardHtml], ct: ["wzCt", ctCardHtml], xc: ["wzXc", xcCardHtml], cb: ["wzCb", cbCardHtml]};
  const m = map[name];
  const el = m && document.getElementById(m[0]);
  if (!el) return;
  if (typing(el)) { CH.later = true; return; }
  el.innerHTML = m[1]();
}
/* место под ошибку поля: id="wzErr-<ключ>" ищет проверка формы */
function wzErrHtml(key, err){ return '<span class="ferr" id="wzErr-' + key + '">' + esc(err || "") + "</span>"; }
const msgHtml = () => '<div class="msg" role="status">' + (CH.err ? errHtml(CH.err) : "") + "</div>";

/* ---------- шаг 1: фото и снимки документов ---------- */
function wzStep1Html(){
  return '<h2 class="wz-h">' + esc(T("tg.wz.s1_title", "Новый осмотр")) + "</h2>"
    + '<p class="wz-lead">' + esc(T("tg.act.s1_lead", "Добавьте фото и документы. Затем проверьте сведения перед подготовкой акта.")) + "</p>"
    + '<div class="capture"><div class="capture-icon" aria-hidden="true">' + ICON_CAM + '</div>'
    + '<h3>' + esc(T("tg.design.capture_title", "Фото и документы объекта")) + '</h3>'
    + '<p>' + esc(T("tg.design.capture_hint", "Общий вид, детали и документы — в одном осмотре")) + '</p>'
    + '<div class="wz-tiles">'
    + '<button type="button" class="wz-tile" data-pick="cam">' + ICON_CAM + "<span>" + esc(T("tg.wz.camera", "Сделать фото")) + "</span></button>"
    + '<button type="button" class="wz-tile" data-pick="files">' + ICON_IMG + "<span>" + esc(T("tg.wz.gallery", "Выбрать файлы")) + "</span></button>"
    + "</div>"
    + '<button type="button" class="wz-drop" id="wzDrop">' + esc(T("tg.wz.drop", "Или перетащите файл сюда / вставьте Ctrl+V")) + "</button></div>"
    + '<details class="upload-help"><summary>' + esc(T("tg.design.file_help", "Форматы и требования к файлам")) + "</summary>"
    + '<p class="hint">' + esc(T("tg.act.formats", "Фото: JPG, PNG, WEBP, HEIC · документы: PDF, Word (DOCX), Excel (XLSX) · до {n} файлов, каждый до {mb} МБ", {n: CH_MAX_FILES, mb: CH_MAX_MB})) + "</p>"
    + '<p class="hint">' + esc(T("tg.act.formats_more", "Кроме фото можно загрузить запрос филиала, договор страхования, техпаспорт или лист технических параметров — скан, фото, PDF, Word или Excel. Запрос и договор приложение прочитает и сверит с расчётом акта; Word, Excel и PDF с текстом разберёт без модели.")) + "</p>"
    + (cbCredit() ? '<p class="hint cb-hint">' + esc(T("tg.cb.s1_hint", "Кредитный продукт: можно загрузить отчёт кредитного бюро по заёмщику — только PDF с текстом (скан не читается: отчёт содержит кредитную историю). Данные бюро в ставку не входят, это проверки андеррайтеру.")) + "</p>" : "")
    + '</details>'
    + '<div id="wzFiles">' + wzFilesHtml() + "</div>"
    + '<div id="wzViews">' + wzViewsHtml() + "</div>"
    + '<p class="wz-warn">' + esc(CH.warning && CH.upLang === I18N_LANG ? CH.warning
      : T("tg.act.warn_pd", "Не загружайте документы с данными людей (паспорт, ФИО, адрес): снимки уходят в языковую модель как картинки. Отчёт кредитного бюро — только PDF с текстом: скан или фото отчёта не читается и в модель не отправляется (отчёт содержит кредитную историю). Сервер тестовый.")) + "</p>"
    + msgHtml();
}
function extOf(name){ const m = /\.([a-z0-9]{1,5})$/i.exec(String(name || "")); return m ? m[1] : "?"; }
function sizeName(b){
  const n = Number(b) || 0;
  return n >= 1048576 ? T("tg.wz.mb", "{n} МБ", {n: nf(n / 1048576, 1)}) : T("tg.wz.kb", "{n} КБ", {n: nf(Math.max(1, n / 1024), 0)});
}
function wzFilesHtml(){
  const reading = CH.busy && CH.phase === "read"
    ? '<div class="wz-reading" role="status"><span class="spin"></span><div><b>' + esc(T("tg.act.reading", "Читаю фото…"))
      + "</b><small>" + esc(T("tg.act.reading_sub", "Обычно это занимает несколько секунд. Не закрывайте приложение.")) + "</small></div></div>"
    : "";
  if (!CH.queue.length) {
    return reading + (CH.stale && CH.photoCount
      ? '<p class="note">' + esc(T("tg.act.stale", "Прежние фото ({n}) уже прочитаны — распознанное на шаге «Проверить». Страница перезагружалась, поэтому самих файлов здесь нет: если нужно добавить снимки, загрузите заново все, чтобы акт учёл каждый ракурс.", {n: CH.photoCount})) + "</p>"
      : "");
  }
  const rows = CH.queue.map(q => {
    let state, cls = "";
    if (CH.busy && !q.error) state = CH.phase === "read" ? T("tg.act.st_reading", "читаю…") : T("tg.wz.sending", "отправляю…");
    else if (q.error) { state = q.error; cls = "err"; }
    else if (q.sent && q.cbOff) { state = T("tg.cb.scan_off", "отчёт бюро: скан не читается — загрузите PDF с текстом"); cls = "warn"; }
    else if (q.sent && q.dk === "cb") {
      // отчёт кредитного бюро: вид документа, балл и класс бюро
      const f = CH.cb && CH.cb.fields;
      state = T("tg.cb.kind", "отчёт кредитного бюро") + (f && (f.score != null || f.score_class)
        ? " · " + T("tg.cb.kind_score", "балл {s}, класс {c}", {s: f.score != null ? f.score : "—", c: f.score_class || "—"}) : "");
      cls = "ok";
    }
    else if (q.sent && (q.dk === "br" || q.dk === "ct")) {
      // запрос филиала или договор: вид документа и сколько строк / полей прочитано
      state = q.dk === "br"
        ? T("tg.dq.kind_br", "запрос филиала") + (CH.br ? " · " + T("tg.dq.rows_n", "строк: {n} из {m}", {n: CH.br.rows_found, m: CH.br.rows_total}) : "")
        : T("tg.dq.kind_ct", "договор страхования") + (CH.ct ? " · " + T("tg.dq.fields_n", "полей: {n}", {n: CH.ct.found_n}) : "");
      cls = "ok";
    }
    else if (q.sent && q.parsed) {
      // документ с текстом разобран сервером без модели: вид документа и сколько значений взято
      const kind = docKindName(q.kind, q.kindLabel);
      state = T("tg.act.st_parsed", "документ разобран") + (kind ? " · " + kind : "")
        + (q.values ? " · " + T("tg.act.st_values", "значений: {n}", {n: q.values}) : "");
      cls = "ok";
    }
    else if (q.sent && q.isDoc) { state = T("tg.act.st_doc_empty", "документ не разобран — введите данные сами"); cls = "warn"; }
    else if (q.sent && q.notRead) { state = T("tg.act.st_not_read", "не прочитан: не поместился в запрос"); cls = "warn"; }
    else if (q.sent) state = q.view ? T("tg.act.st_view", "прочитан · {v}", {v: viewName(q.view)}) : T("tg.act.st_sent", "прочитан");
    else state = T("tg.act.st_queued", "готов к проверке");
    return '<div class="wz-file' + (q.error ? " bad" : "") + '"><span class="ic" aria-hidden="true">'
      + (q.url ? '<img src="' + esc(q.url) + '" alt="">' : esc(extOf(q.name))) + "</span>"
      + "<span><b>" + esc(q.name) + '</b><small class="' + cls + '">' + esc(sizeName(q.size) + " · " + state) + "</small></span>"
      + (CH.busy ? (CH.phase === "upload" ? '<span class="st">' + esc(nf(CH.upPct || 0, 0)) + "%</span>" : '<span class="st"></span>')
        : '<button type="button" class="rm" data-rm="' + q.id + '" aria-label="' + esc(T("tg.wz.remove_aria", "Убрать файл {name}", {name: q.name})) + '">' + '<span aria-hidden="true">×</span>' + "</button>")
      + (CH.busy && CH.phase === "upload" ? '<span class="bar"><i style="width:' + (CH.upPct || 0) + '%"></i></span>' : "")
      + (cbKindable(q) ? '<button type="button" class="cbk" data-cbk="' + q.id + '" aria-pressed="' + (q.isCb ? "true" : "false") + '">'
        + esc(T("tg.cb.mark", "это отчёт бюро")) + "</button>" : "") + "</div>";
  }).join("");
  return reading + '<div class="wz-files"><p class="eyebrow">' + esc(T("tg.wz.files_title", "Файлы · {n}", {n: CH.queue.length})) + "</p>"
    + rows + "</div>";
}
/* какие снимки нужны: после чтения — чего не хватает, до него — таблица по классу (или вся) */
function wzViewsHtml(){
  if (CH.photoCount && CH.ai && CH.requiredViews.length) {
    if (!CH.missingViews.length) return '<p class="note ok">' + esc(T("tg.act.views_all", "Все нужные ракурсы есть.")) + "</p>";
    return '<div class="card wz-need"><h2>' + esc(T("tg.act.views_miss_title", "Не хватает снимков")) + "</h2>"
      + "<p>" + esc(CH.missingViews.map(viewName).join(", ")) + "</p>"
      + '<p class="note">' + esc(T("tg.act.views_skip", "Продолжить можно и без них — в акте это будет отмечено.")) + "</p></div>";
  }
  // ракурсы и виды объекта — из шаблонов классов продукта (required_views, object.kinds); класс не выбран — общий совет
  const classes = ptProdClasses();
  const docs = '<p class="note">' + esc(T("tg.act.views_docs", "Техпаспорт, паспорт самоходной машины, лист технических параметров или кадастровый документ снимайте и загружайте так же, как фото.")) + "</p>";
  const head = '<div class="card wz-need"><h2>' + esc(T("tg.act.views_title", "Какие снимки нужны")) + "</h2>";
  if (!classes.length) {
    return head + '<p class="sub">' + esc(T("tg.act.views_no_class", "Снимите объект целиком со всех сторон, заводскую табличку и документ на объект. Когда выберете продукт или класс на шаге «Проверить», подскажем точный список по шаблону класса.")) + "</p>" + docs + "</div>";
  }
  classes.forEach(tplNeed);
  const got = classes.map(c => TPL.cache[tplKey(c)]).filter(Boolean);
  if (!got.length) return head + anLoadingCard() + "</div>";
  const rows = [];
  got.filter(x => x.template).forEach(x => {
    const tp = x.template, rv = anObj(tp.required_views), by = {};
    // группы с одинаковым списком ракурсов — одной строкой
    Object.keys(rv).forEach(g => {
      const vs = anArr(rv[g]).map(String);
      if (!vs.length) return;
      const k = vs.join(",");
      (by[k] = by[k] || {views: vs, groups: []}).groups.push(g === "default" ? anStr(tp.name) : groupName(g));
    });
    const kinds = anArr(tp.object && tp.object.kinds).map(k => anStr(k && k.label)).filter(Boolean);
    const more = kinds.length > 8 ? " " + T("tg.act.views_kinds_more", "и ещё {n}", {n: kinds.length - 8}) : "";
    Object.keys(by).forEach(k => rows.push("<div><dt>" + esc(by[k].groups.filter(Boolean).join(" / ")) + "</dt><dd>" + esc(by[k].views.map(viewName).join(", "))
      + (kinds.length ? "<small>" + esc(T("tg.act.views_kinds", "Объект: {list}", {list: kinds.slice(0, 8).join(", ") + more})) + "</small>" : "") + "</dd></div>"));
  });
  if (!rows.length) return head + '<p class="note err">' + esc(T("tg.tpl.failed_views", "Шаблон класса не загрузился — снимите объект со всех сторон, табличку и документ.")) + "</p>" + docs + "</div>";
  return head + '<p class="sub">' + esc(T("tg.act.views_by_class", "По выбранному классу страхования.")) + "</p>"
    + '<dl class="vw">' + rows.join("") + "</dl>" + docs + "</div>";
}
/* Картинки других форматов (WEBP, GIF, BMP, AVIF, HEIC — если браузер умеет их открыть) переводим
   в JPG прямо в браузере: сервер принимает JPG, PNG и PDF. Не открылась — вернём null. */
const WZ_CONVERT = /\.(webp|gif|bmp|avif|heic|heif|jfif|tiff?)$/i;
function wzNeedsConvert(f){
  const name = String(f.name || ""), type = String(f.type || "");
  if (/^image\/(jpeg|png)$/.test(type) || /\.(jpe?g|png|pdf|docx|xlsx)$/i.test(name) || type === "application/pdf"
      || MIME_DOCS.indexOf(type) >= 0) return false;
  return /^image\//.test(type) || WZ_CONVERT.test(name);
}
function wzToJpeg(f){
  return new Promise(done => {
    const url = URL.createObjectURL(f);
    const img = new Image();
    const end = v => { URL.revokeObjectURL(url); done(v); };
    img.onerror = () => end(null);
    img.onload = () => {
      try {
        const k = Math.min(1, 2560 / Math.max(img.naturalWidth, img.naturalHeight, 1));
        const c = document.createElement("canvas");
        c.width = Math.max(1, Math.round(img.naturalWidth * k));
        c.height = Math.max(1, Math.round(img.naturalHeight * k));
        const g = c.getContext("2d");
        g.fillStyle = "#fff";                       // прозрачный фон в JPG стал бы чёрным
        g.fillRect(0, 0, c.width, c.height);
        g.drawImage(img, 0, 0, c.width, c.height);
        c.toBlob(b => {
          if (!b) return end(null);
          const name = String(f.name || "photo").replace(/\.[a-z0-9]+$/i, "") + ".jpg";
          end(new File([b], name, {type: "image/jpeg"}));
        }, "image/jpeg", 0.9);
      } catch (e) { end(null); }
    };
    img.src = url;
  });
}
async function wzAddFiles(files){
  let list = Array.prototype.slice.call(files || []);
  if (!list.length || CH.busy) return;
  const bad = [];
  const ready = [];
  for (const f of list) {
    if (!wzNeedsConvert(f)) { ready.push(f); continue; }
    const j = await wzToJpeg(f);
    if (j) ready.push(j);
    else bad.push(T("tg.wz.bad_image", "{name}: не удалось открыть картинку — сохраните её как JPG или PNG.", {name: f.name || "?"}));
  }
  list = ready;
  let over = false;
  list.forEach(f => {
    const name = String(f.name || "");
    const ext = (/\.([a-z0-9]+)$/i.exec(name) || [])[1];
    const type = String(f.type || "");
    const okType = (ext && CH_EXT.indexOf(ext.toLowerCase()) >= 0) || /^image\/(jpeg|png)$/.test(type) || type === "application/pdf"
      || MIME_DOCS.indexOf(type) >= 0;
    if (!okType) { bad.push(T("tg.act.bad_format", "{name}: такой формат не читается — нужен JPG, PNG, WEBP, HEIC, PDF, DOCX или XLSX. Старые DOC и XLS пересохраните в новом формате.", {name: name || "?"})); return; }
    if (f.size > CH_MAX_MB * 1048576) { bad.push(T("tg.wz.too_big", "{name}: файл больше {mb} МБ — сожмите его или разделите.", {name: name, mb: CH_MAX_MB})); return; }
    if (CH.queue.length >= CH_MAX_FILES) { over = true; return; }
    const img = /^image\//.test(type);
    CH.queue.push({id: ++CH.seq, file: f, size: f.size, sent: false, error: "", view: null,
      name: name || ("photo-" + CH.seq + "." + (type === "image/png" ? "png" : "jpg")),
      url: img && window.URL && URL.createObjectURL ? URL.createObjectURL(f) : ""});
    CH.filesDirty = true;
  });
  if (over) bad.push(T("tg.act.too_many", "В акт идёт не больше {n} файлов — уберите лишние, чтобы добавить другие.", {n: CH_MAX_FILES}));
  CH.err = bad.join(" ");
  haptic(bad.length ? "warning" : "select");
  if (CH.wz !== 1) { CH.wz = 1; wzSave(); }
  wzPaint(true);
}
/* кредитный продукт: сотрудник помечает файл «это отчёт бюро» — картинка или скан с такой пометкой в модель не уходит
   (сервер: поле kinds у POST /act/photos, настройка credit_report.allow_scan); DOCX и XLSX — не отчёт бюро */
function cbKindable(q){ return !CH.busy && cbCredit() && !q.error && !/\.(docx|xlsx)$/i.test(String(q.name || "")); }
function cbToggle(id){
  const q = CH.queue.filter(x => x.id === id)[0];
  if (!q || CH.busy) return;
  q.isCb = !q.isCb;
  CH.filesDirty = true;                       // вид файла поменялся — список уйдёт заново
  haptic("select");
  wzPart("files");
  wzBarPaint();
}
function wzRemove(id){
  const q = CH.queue.filter(x => x.id === id)[0];
  if (!q || CH.busy) return;
  if (q.url) URL.revokeObjectURL(q.url);
  CH.queue = CH.queue.filter(x => x !== q);
  if (q.sent) CH.filesDirty = true;           // принятый файл убрали — пришлём оставшиеся заново
  CH.err = "";
  haptic("select");
  wzPart("files");
  wzBarPaint();
}
function chatProgress(){
  document.querySelectorAll("#wzFiles .bar i").forEach(i => { i.style.width = (CH.upPct || 0) + "%"; });
  document.querySelectorAll("#wzFiles .st").forEach(s => { s.textContent = CH.phase === "upload" ? nf(CH.upPct || 0, 0) + "%" : ""; });
  wzBarPaint();
}
function wzNext1(){
  const live = CH.queue.filter(q => !q.error);
  if (live.length && (CH.filesDirty || live.some(q => !q.sent))) { actUpload(); return; }
  wzGo(2);
}
/* все файлы из списка — одним POST /act/photos: сервер не дописывает файлы в прежнюю сессию, поэтому при
   добавлении снимков уходит весь список заново, а исправленные сотрудником значения остаются */
async function actUpload(){
  const list = CH.queue.filter(q => !q.error).slice(0, CH_MAX_FILES);
  if (!list.length || CH.busy) return;
  const fd = new FormData();
  list.forEach(q => fd.append("files", q.file, q.name));
  fd.append("lang", I18N_LANG);
  // вид файла, который выбрал сотрудник: номер файла в запросе (с 1) → credit_report
  const kinds = {};
  list.forEach((q, i) => { if (q.isCb && cbKindable(q)) kinds[i + 1] = "credit_report"; });
  if (Object.keys(kinds).length) fd.append("kinds", JSON.stringify(kinds));
  if (CH.must.product_code) fd.append("product_code", CH.must.product_code);
  else if (CH.must.class_code) fd.append("class_code", CH.must.class_code);
  CH.busy = true;
  CH.phase = "upload";
  CH.err = "";
  CH.upPct = 0;
  tgBusy(true);
  wzPaint(true);
  const res = await new Promise(resolve => {
    const x = new XMLHttpRequest();
    x.open("POST", "/act/photos");
    if (TOKEN) x.setRequestHeader("Authorization", "Bearer " + TOKEN);
    if (IN_TG) x.setRequestHeader("X-Telegram-Init-Data", TG.initData);
    x.upload.onprogress = e => { if (e.lengthComputable) { CH.upPct = Math.round(e.loaded / e.total * 100); chatProgress(); } };
    // файлы ушли — дальше их читает модель: «Читаю фото…»
    x.upload.onload = () => { CH.phase = "read"; wzPart("files"); wzBarPaint(); };
    x.onload = () => {
      let j = null;
      try { j = JSON.parse(x.responseText); } catch (e) { j = null; }
      resolve({status: x.status, j: j});
    };
    x.onerror = () => resolve({status: 0, j: null});
    x.send(fd);
  });
  CH.busy = false;
  CH.phase = "";
  tgBusy(false);
  const j = res.j || {};
  if (!res.status) { CH.err = T("tg.err.offline", "сервер не отвечает"); haptic("error"); wzPaint(true); return; }
  if (res.status >= 400) {
    // 413 — больше 10 файлов, 422 — ни один не принят (причины у файлов), 429 — слишком часто: словами сервера
    actMarkRejected(list, j.rejected || []);
    CH.err = typeof j.detail === "string" && j.detail ? j.detail : why(res.status, j);
    if (j.warning) { CH.warning = String(j.warning); CH.upLang = I18N_LANG; }
    haptic("error");
    wzPaint(true);
    return;
  }
  actApplyPhotos(j, list);
  haptic("success");
  CH.wz = 2;
  wzSave();
  wzPaint(true);
  window.scrollTo(0, 0);
}
/* файл из ответа сервера → файл в списке: по index — порядковому номеру файла в запросе (с 1).
   Имя не годится: сервер скрывает в нём персональные данные, и у двух файлов оно может совпасть. */
const byIndex = (list, r) => {
  const i = r && Number(r.index);
  return Number.isInteger(i) && i >= 1 && i <= list.length ? list[i - 1] : null;
};
function actMarkRejected(list, rejected){
  (Array.isArray(rejected) ? rejected : []).forEach(r => {
    const q = byIndex(list, r);
    if (!q) return;
    q.error = String(r.error || T("tg.wz.not_taken", "не принят"));
    q.sent = false;
    q.notRead = false;
  });
}
function actApplyPhotos(d, list){
  actMarkRejected(list, d.rejected || []);
  const files = Array.isArray(d.files) ? d.files : [];
  const docs = (Array.isArray(d.documents) ? d.documents : []).filter(x => x && Number.isInteger(Number(x.index)));
  files.forEach(f => {
    const q = byIndex(list, f);
    if (!q || q.error) return;
    const doc = docs.filter(x => Number(x.index) === Number(f.index))[0] || null;
    q.sent = true;
    q.sid = f.id ? String(f.id) : "";        // id файла на сервере («f1») — для привязки фото к объекту
    q.view = f.view || null;
    q.parsed = !!f.parsed;
    q.isDoc = f.format === "docx" || f.format === "xlsx";
    q.kind = doc && doc.kind ? String(doc.kind) : null;
    q.kindLabel = doc && doc.kind_label ? String(doc.kind_label) : "";
    q.values = doc ? Number(doc.values) || 0 : 0;
    // запрос филиала / договор: по номеру файла у блока или по виду документа (у договора из нескольких страниц)
    const same = (b, kind) => !!b && b.detected && (b.file === f.id || (!!f.document_kind && f.document_kind === b.kind_label)) ? kind : "";
    q.dk = same(d.branch_request, "br") || same(d.contract, "ct") || same(d.credit_report, "cb");
    q.cbOff = f.credit_scan_withheld === true;   // скан с пометкой «отчёт бюро» в модель не отправлен
    // модель работала, но этот снимок в запрос к ней не поместился (разобранный документ в модель и не уходит)
    q.notRead = !!d.ai && f.read_by_ai === false && !q.parsed && !q.isDoc;
  });
  CH.docs = docs.map(x => ({index: Number(x.index), kind: x.kind ? String(x.kind) : null, text_layer: !!x.text_layer,
    values: Number(x.values) || 0}));
  CH.modelN = files.filter(f => !f.parsed && f.format !== "docx" && f.format !== "xlsx").length;
  CH.filesDirty = false;
  CH.stale = false;
  CH.session = String(d.session || "");
  CH.ai = !!d.ai;
  CH.photoCount = files.length;
  // файлы загрузки для привязки к объектам парка: id сервера, номер в запросе, ракурс; имя — только в памяти
  CH.files = files.filter(f => f && /^f\d{1,2}$/.test(String(f.id || ""))).map(f => {
    const q = byIndex(list, f);
    return {id: String(f.id), n: Number(f.index) || 0, view: f.view || null, name: q ? String(q.name || "") : ""};
  });
  const ids = CH.files.map(f => f.id);
  if (CH.ob) CH.ob.items.forEach(it => { it.photos = it.photos.filter(x => ids.indexOf(x) >= 0); });
  CH.upLang = d.lang || I18N_LANG;
  CH.msg = String(d.message || "");
  CH.notes = Array.isArray(d.notes) ? d.notes.map(String) : [];
  CH.warning = String(d.warning || "");
  // правки сотрудника переживают новое распознавание: его значение встаёт на место того, что он исправил
  const edited = CH.rec.filter(r => r.source === "input");
  const fresh = (Array.isArray(d.recognized) ? d.recognized : []).filter(r => r && r.key && r.value != null && String(r.value).trim())
    .map(r => ({key: String(r.key), value: String(r.value), source: String(r.source || "photo"), file: r.file || null,
      label: r.label || "", note: r.note || "", orig: {value: String(r.value), source: String(r.source || "photo")}}));
  edited.forEach(e => {
    const at = fresh.findIndex(r => r.key === e.key && e.orig && r.orig.value === e.orig.value && r.orig.source === e.orig.source);
    if (at >= 0) fresh[at] = e; else fresh.push(e);
  });
  CH.rec = fresh;
  CH.damages = (Array.isArray(d.damages) ? d.damages : []).filter(x => x && x.what).map(x => ({what: String(x.what), where: x.where ? String(x.where) : null}));
  CH.missingViews = (d.missing_views || []).map(v => String(v.code || v));
  CH.requiredViews = (d.required_views || []).map(v => String(v.code || v));
  CH.suggest = (d.suggest_classes || []).map(String);
  // вид объекта по фото — для ссылок поиска объявлений (раздел площадки)
  CH.kind = d.object_kind && typeof d.object_kind === "object" && /^[a-z_]{1,40}$/.test(String(d.object_kind.code || "")) ? String(d.object_kind.code) : "";
  // запрос филиала и договор: правки сотрудника сохраняются, если документ прочитан так же
  const keep = (old, fresh) => old && fresh && JSON.stringify(old.orig) === JSON.stringify(fresh.orig) ? Object.assign(fresh, {req: old.req}) : fresh;
  CH.br = keep(CH.br, dqBrFrom(d.branch_request));
  CH.ct = keep(CH.ct, dqCtFrom(d.contract));
  CH.xc = dqXcFrom(d.cross_check);
  // отчёт кредитного бюро: правки сотрудника остаются, если отчёт прочитан так же
  const cb = cbFrom(d.credit_report);
  CH.cb = cb && CH.cb && JSON.stringify(CH.cb.orig) === JSON.stringify(cb.orig) ? Object.assign(cb, {fields: CH.cb.fields}) : cb;
  CH.preDoc = {};
  wzApplyPrefill(d.prefill && typeof d.prefill === "object" ? d.prefill : {});
  // автозаполнение ТС (02.10.2026): подпись, год, подгруппа, топливо — в пустые поля, с пометкой «!»
  CH.vp = vpFrom(d);
  if (pfApply() && !obOn()) CH.optOpen = true;   // подгруппа и топливо — в «Дополнительно»: раскрываем
}
/* prefill из документа → поля шага 2 с пометкой «из документа — проверьте».
   Что сотрудник уже ввёл сам, не затирается: значение документа остаётся подсказкой «в документе: …».
   Регион, подставленный из прошлого раза (а не выбранный сейчас), документ заменяет. */
function wzApplyPrefill(pf){
  const take = (key, box, value) => {
    CH.preDoc[key] = value;
    const cur = box[key];
    if (cur == null || cur === "" || CH.pre[key] || (key === "region" && CH.seeded)) {
      box[key] = value;
      CH.pre[key] = true;
      if (key === "region") CH.seeded = false;
    }
  };
  ["sum_insured", "object_value"].forEach(k => {
    const v = pf[k] && Number(pf[k].value);
    if (v > 0 && isFinite(v)) take(k, CH.must, v);
  });
  const days = pf.term_days && Number(pf.term_days.value);
  if (Number.isInteger(days) && days >= 1 && days <= 3660) take("term_days", CH.opt, String(days));
  const rc = pf.region && pf.region.code;
  if (REGIONS.indexOf(rc) >= 0) take("region", CH.must, rc);
  // код продукта из запроса филиала или договора: выбирает продукт в списке, если сотрудник не выбрал сам
  const pc = pf.product_code && String(pf.product_code.value || "");
  if (/^\d{4}$/.test(pc)) {
    CH.preDoc.product_code = pc;
    if ((!CH.must.product_code && !CH.must.class_code) || CH.pre.product_code) {
      const p = wzProd(pc);
      CH.must.product_code = pc;
      CH.must.class_code = p ? wzClassesOf(p)[0] || "" : "";
      CH.pre.product_code = true;
    }
  }
  // даты срока — подсказкой у поля «Срок страхования»
  const d1 = pf.term_from && sIso(pf.term_from.value), d2 = pf.term_to && sIso(pf.term_to.value);
  if (d1 && d2) CH.preDoc.term_dates = d1 + "/" + d2;
  // новая загрузка: пометка «из документа» остаётся только у значений, которые есть в новом ответе
  Object.keys(CH.pre).forEach(k => { if (!(k in CH.preDoc)) delete CH.pre[k]; });
}
/* пометка у поля: «из документа — проверьте» или, если сотрудник ввёл своё, «в документе: …» */
function preTag(key){
  return CH.pre[key] ? ' <span class="pre-tag" id="wzPre-' + key + '">' + esc(T("tg.act.pre_mark", "из документа — проверьте")) + "</span>" : "";
}
function preDiff(key, cur){
  const dv = CH.preDoc[key];
  if (CH.pre[key] || dv == null || dv === "" || String(cur == null ? "" : cur) === String(dv)) return "";
  const shown = key === "region" ? regionName(dv) : key === "term_days" ? T("tg.act.days_n", "{n} дн.", {n: dv})
    : key === "product_code" ? String(dv) : money(dv);
  return '<span class="hint pre-diff">' + esc(T("tg.act.pre_diff", "В документе: {v}. Оставлено ваше значение.", {v: shown})) + "</span>";
}
function preClear(key){
  if (!CH.pre[key]) return;
  delete CH.pre[key];
  const t = $("#wzPre-" + key);
  if (t) t.remove();
}

/* ---------- шаг 2: проверить ---------- */
function wzStep2Html(){
  return '<h2 class="wz-h">' + esc(T("tg.wz.s2_title", "Проверьте данные")) + "</h2>"
    + '<p class="wz-lead">' + esc(CH.photoCount
      ? T("tg.act.s2_lead_photos", "Проверьте, что прочитано с фото, и заполните четыре обязательных поля.")
      : T("tg.act.s2_lead", "Заполните четыре обязательных поля — остальное можно не заполнять.")) + "</p>"
    + '<div id="wzRec">' + wzRecHtml() + "</div>"
    + '<div id="wzBr">' + brCardHtml() + "</div>"
    + '<div id="wzCt">' + ctCardHtml() + "</div>"
    + '<div id="wzXc">' + xcCardHtml() + "</div>"
    + '<div id="wzCb">' + cbCardHtml() + "</div>"
    + '<div class="card"><h2>' + esc(T("tg.wz.must_title", "Обязательные данные")) + "</h2>"
    + '<p class="sub">' + esc(T("tg.wz.must_sub", "Продукт или класс, страховая сумма, стоимость объекта и регион.")) + "</p>"
    + '<div id="wzProd">' + wzProdHtml() + "</div>"
    + '<div id="wzObMode">' + obModeHtml() + "</div>"
    + '<div class="hero2" id="wzSums">' + wzSumsHtml() + "</div>"
    + '<div id="wzRegion">' + wzRegionHtml() + "</div>"
    + '<div id="wzTerm">' + wzTermHtml() + "</div>"
    + "</div>"
    + '<div id="wzObj">' + obCardHtml() + "</div>"
    + '<div id="wzParts">' + ptCardHtml() + "</div>"
    + '<div id="wzTpl">' + tplCardHtml() + "</div>"
    + mkHtml()
    + '<div id="wzMoreBox">' + wzMoreHtml() + "</div>"
    + msgHtml();
}
const normVal = v => String(v == null ? "" : v).toUpperCase().replace(/[\s.,]/g, "");
/* поля распознавания по порядку первого появления; у каждого — все источники */
function recGroups(){
  const order = [], by = {};
  CH.rec.forEach((r, i) => {
    if (!by[r.key]) { by[r.key] = []; order.push(r.key); }
    by[r.key].push(i);
  });
  return order.map(k => ({key: k, idx: by[k]}));
}
function recDisc(idx){
  const vals = {};
  idx.forEach(i => { const v = normVal(CH.rec[i].value); if (v) vals[v] = 1; });
  return Object.keys(vals).length > 1;
}
function recSrcLine(r){
  return srcName(r.source) + (r.source === "input" ? "" : " · " + T("tg.act.check", "проверьте"));
}
function wzRecHtml(){
  const addBtn = '<button type="button" class="btn btn-secondary btn-sm" data-go="addphoto">' + esc(T("tg.act.add_photo", "Добавить фото")) + "</button>";
  if (!CH.photoCount) {
    return '<div class="card"><h2>' + esc(T("tg.act.no_insp_title", "Осмотр по фото")) + "</h2>"
      + '<p class="sub">' + esc(T("tg.act.no_insp", "Фото не загружены — в акте разделы осмотра будут помечены «осмотр не проводился».")) + "</p>"
      + '<div class="actions">' + addBtn + "</div></div>";
  }
  const groups = recGroups();
  const parsed = CH.docs.filter(d => d.text_layer);
  const docsOnly = !CH.ai && !CH.modelN && CH.docs.length > 0;
  let h = '<div class="card"><h2>' + esc(T("tg.act.rec_title", "Распознано")) + "</h2>";
  if (CH.ai) h += '<p class="sub">' + esc(T("tg.act.rec_sub", "Модель прочитала фото и снимки документов и может ошибаться. Проверьте каждое значение — исправить можно прямо здесь.")) + "</p>";
  else if (docsOnly) h += '<p class="sub">' + esc(T("tg.act.rec_docs_sub", "Документы разобраны без модели. Проверьте каждое значение — исправить можно прямо здесь.")) + "</p>";
  else {
    h += '<p class="note warn">' + esc(T("tg.act.rec_off", "Фото сохранены, но прочитать их не удалось — введите данные сами. В акте раздел осмотра будет помечен.")) + "</p>";
    if (CH.msg && CH.upLang === I18N_LANG) h += '<p class="note">' + esc(CH.msg) + "</p>";
  }
  h += parsed.map(d => {
    const kind = docKindName(d.kind);
    return '<p class="note doc-ok">' + esc(T("tg.act.doc_parsed_line", "Документ разобран: {kind} · значений: {n}", {kind: kind || T("tg.act.dk.other", "документ"), n: d.values})) + "</p>";
  }).join("");
  if (CH.upLang === I18N_LANG) h += CH.notes.map(n => '<p class="note warn">' + esc(n) + "</p>").join("");
  if (CH.ai && !groups.length) h += '<p class="note">' + esc(T("tg.act.rec_none", "С фото не удалось прочитать ни одного значения — введите данные ниже.")) + "</p>";
  if (docsOnly && !groups.length) h += '<p class="note">' + esc(T("tg.act.rec_none_docs", "Из документов не удалось взять ни одного значения — введите данные ниже.")) + "</p>";
  h += groups.map(g => {
    const disc = recDisc(g.idx), label = recLabel(g.key, (CH.rec[g.idx[0]] || {}).label);
    return '<div class="rec' + (disc ? " disc" : "") + '" data-key="' + esc(g.key) + '"><div class="rec-h"><b>' + esc(label) + "</b>"
      + '<span class="tag warn rec-tag">' + esc(T("tg.act.disc_tag", "расхождение")) + "</span></div>"
      + g.idx.map(i => {
        const r = CH.rec[i];
        return '<div class="rec-i"><input id="wzr-' + i + '" type="text" autocomplete="off" maxlength="120" data-rec="' + i + '" value="' + esc(r.value)
          + '" aria-label="' + esc(label + ", " + srcName(r.source)) + '"><small id="wzrs-' + i + '">' + esc(recSrcLine(r)) + "</small></div>";
      }).join("")
      + '<p class="note warn rec-hint">' + esc(T("tg.act.disc_hint", "Значения из разных источников не совпадают. Оставьте верное: при расхождении приоритет у документа с печатью производителя, решение за андеррайтером.")) + "</p>"
      + "</div>";
  }).join("");
  h += vpBoxHtml();
  if (CH.ai) {
    h += '<div class="h3">' + esc(T("tg.act.damages", "Видимые повреждения")) + "</div>"
      + (CH.damages.length
        ? '<ul class="dmg">' + CH.damages.map(d => "<li>" + esc(d.what + (d.where ? " (" + d.where + ")" : "")) + "</li>").join("") + "</ul>"
        : '<p class="note">' + esc(T("tg.act.damages_none", "На фото повреждений не видно.")) + "</p>");
  }
  if (CH.missingViews.length) {
    h += '<div class="wz-miss"><p><b>' + esc(T("tg.act.views_miss_title", "Не хватает снимков")) + ":</b> " + esc(CH.missingViews.map(viewName).join(", ")) + "</p>"
      + '<p class="note">' + esc(T("tg.act.views_skip", "Продолжить можно и без них — в акте это будет отмечено.")) + "</p>"
      + '<div class="actions">' + addBtn + "</div></div>";
  } else h += '<div class="actions">' + addBtn + "</div>";
  return h + "</div>";
}
/* правка распознанного: значение и источник меняются на месте, поле не перерисовывается */
function recInput(el){
  const i = Number(el.dataset.rec), r = CH.rec[i];
  if (!r) return;
  r.value = String(el.value || "");
  const o = r.orig;
  r.source = o && r.value.trim() === o.value ? o.source : "input";
  const s = $("#wzrs-" + i);
  if (s) s.textContent = recSrcLine(r);
  const box = el.closest(".rec");
  if (box) box.classList.toggle("disc", recDisc(recGroups().filter(g => g.key === r.key)[0].idx));
  if (r.key === "brand" || r.key === "model" || r.key === "year") mkLinksSoon();
  wzSave();
}

/* продукт или класс: список как в «Тарифной политике» — поиск, группы по классам, код, название,
   «документов: N», ставка справа; класс по фото — подсказкой сверху */
function wzProdRow(p, pressed, asPick){
  const n = wzDocCount(p);
  const inner = "<code>" + esc(p.code) + "</code><span>" + esc(p.name || "")
    + (n != null ? "<small>" + esc(T("tg.wz.docs_n", "документов: {n}", {n: n})) + "</small>" : "") + "</span>"
    + "<b>" + esc(wzRate(p)) + "</b>";
  return asPick ? '<div class="ppick">' + inner + "</div>"
    : '<button type="button" class="prow" data-prod="' + esc(p.code) + '" aria-pressed="' + (!!pressed) + '">' + inner + "</button>";
}
function wzProdHtml(){
  const m = CH.must, code = m.product_code || "", cls = m.class_code || "";
  const err = CH.errs.prod;
  const p = code ? wzProd(code) : null;
  let pick = "";
  if (p) pick = wzProdRow(p, true, true);
  else if (code) pick = '<div class="ppick"><code>' + esc(code) + "</code><span>" + esc(code) + "</span><b></b></div>";
  else if (cls) pick = '<div class="ppick"><code>' + esc(cls) + "</code><span>" + esc(wzClsName(cls))
    + "<small>" + esc(T("tg.wz.class_only_hint", "только класс — продукт не выбран")) + "</small></span><b></b></div>";
  const open = !pick || CH.prodOpen;
  const sug = !pick && CH.suggest.length
    ? '<div class="wz-sug"><span>' + esc(T("tg.act.suggest", "По фото похоже на класс:")) + "</span>"
      + CH.suggest.map(c => '<button type="button" data-clsonly="' + esc(c) + '">' + esc(c + " · " + wzClsName(c)) + "</button>").join("") + "</div>"
    : "";
  return '<div class="' + (err ? "bad" : "") + '"><label class="f"' + (open ? ' for="wzProdQ"' : "") + ">"
    + esc(T("tg.wz.prod_label", "Продукт или класс")) + preTag("product_code") + "</label>"
    + sug
    + (pick && !open ? pick + '<div class="qadd"><button type="button" data-prodopen="1">' + esc(T("tg.wz.prod_change", "Изменить")) + "</button></div>" : "")
    + (open ? '<input id="wzProdQ" type="search" autocomplete="off" enterkeyhint="search" value="' + esc(CH.prodQ) + '" placeholder="'
        + esc(T("tg.wz.prod_ph", "Код или название продукта")) + '">'
        + '<div class="pgroups" id="wzProdList">' + wzProdListHtml() + "</div>"
        + (pick ? '<div class="qadd"><button type="button" data-prodclose="1">' + esc(T("tg.wz.prod_keep", "Оставить выбранный")) + "</button></div>" : "")
      : "")
    + preDiff("product_code", code)
    + '<span class="ferr" id="wzErr-prod">' + esc(err || "") + "</span></div>";
}
function wzProdListHtml(){
  if (CH.refsErr) return '<p class="note err pad">' + esc(T("tg.refs_failed", "Справочники не загрузились: {reason}", {reason: CH.refsErr})) + "</p>";
  const R = CH.refs;
  if (!R) return '<p class="note pad">' + spin(T("common.loading", "загружаю…")) + "</p>";
  const q = String(CH.prodQ || "").trim().toLowerCase();
  const cur = CH.must.product_code || "";
  const hit = p => !q || String(p.code).toLowerCase().indexOf(q) === 0 || String(p.name || "").toLowerCase().indexOf(q) >= 0;
  const groups = R.classes.map(c => {
    const all = R.products.filter(p => wzClassesOf(p)[0] === c.code);
    const byClass = q && (String(c.code).toLowerCase() === q || wzClsName(c.code, c.name).toLowerCase().indexOf(q) >= 0);
    return {c: c, items: byClass ? all : all.filter(hit)};
  }).filter(g => g.items.length);
  if (!groups.length) return '<p class="note pad">' + esc(T("tg.wz.found_none", "Ничего не нашлось — проверьте код или напишите часть названия.")) + "</p>";
  return groups.map(g => '<div class="pgroup"><h4><span>' + esc(g.c.code + ". " + wzClsName(g.c.code, g.c.name)) + "</span>"
      + '<button type="button" data-clsonly="' + esc(g.c.code) + '">' + esc(T("tg.wz.class_pick", "Весь класс")) + "</button></h4>"
      + g.items.map(p => wzProdRow(p, p.code === cur)).join("") + "</div>").join("");
}
function wzPickProduct(code){
  const p = wzProd(code);
  if (!p) return;
  CH.must.product_code = code;
  CH.must.class_code = wzClassesOf(p)[0] || "";
  wzPicked();
}
function wzPickClass(code){
  delete CH.must.product_code;
  CH.must.class_code = code;
  wzPicked();
}
function wzPicked(){
  delete CH.pre.product_code;                 // продукт выбран сотрудником — пометка «из документа» снимается
  CH.prodOpen = false;
  CH.prodQ = "";
  wzClearErr("prod");
  // уточнения, которых у нового класса нет, не уходят в акт
  const cls = wzClass();
  if (CH.opt.protection && protCodes(cls).indexOf(CH.opt.protection) < 0) delete CH.opt.protection;
  if (!isProp(cls)) ["seismic_zone", "construction", "activity"].forEach(k => { delete CH.opt[k]; });
  // поля класса и вид объекта — от шаблона прежнего класса; части — от прежнего состава продукта
  if (CH.cfCls !== cls) { delete CH.opt.cf; delete CH.opt.object_kind; CH.cfCls = cls; }
  if (CH.pt && CH.pt.key !== ptProdClasses().join(",")) { CH.pt = null; CH.ptAdd = false; CH.ptMore = {}; }
  pfApply();                                  // подгруппа и топливо ТС — в поля нового класса, если они пусты
  haptic("select");
  wzSave();
  wzPart("prod");
  wzPart("obmode");
  wzPart("sums");
  wzPart("obj");
  wzPart("parts");
  wzPart("tpl");
  wzPart("more");
  wzBarPaint();
}
const wzSumsHtml = () => ["sum_insured", "object_value"].map(wzMoneyHtml).join("");
function wzMoneyHtml(key){
  if (obOn()) return obMoneyRoHtml(key);
  const id = "wzf-" + key, v = CH.must[key], err = CH.errs[key];
  const other = key === "sum_insured" ? "object_value" : "sum_insured";
  const label = key === "sum_insured" ? T("tg.an.f.sum_insured", "Страховая сумма") : T("tg.an.f.object_value", "Стоимость объекта");
  return '<div class="' + (err ? "bad" : "") + '"><label class="f" for="' + id + '">' + esc(label) + ' <span class="unit">' + esc(SUM()) + "</span>" + preTag(key) + "</label>"
    + '<input id="' + id + '" type="text" inputmode="numeric" enterkeyhint="done" autocomplete="off" data-money="1" data-fk="' + key + '" data-scope="must"'
    + ' value="' + esc(v == null || v === "" ? "" : Number(v).toLocaleString(LOC())) + '" placeholder="0">'
    + '<div class="qadd" data-for="' + key + '">'
    + '<button type="button" data-add="1000000">' + esc(T("tg.chat.add_mln", "+млн")) + "</button>"
    + '<button type="button" data-add="1000000000">' + esc(T("tg.chat.add_bln", "+млрд")) + "</button>"
    + '<button type="button" data-copy="' + other + '">'
    + esc(key === "sum_insured" ? T("tg.an.eq_value", "= стоимости") : T("tg.an.eq_sum", "= страховой сумме")) + "</button></div>"
    + preDiff(key, v)
    + wzErrHtml(key, err) + "</div>";
}
function wzRegionHtml(){
  const cur = CH.must.region || "", err = CH.errs.region;
  return '<div class="' + (err ? "bad" : "") + '"><label class="f" for="wzf-region">' + esc(T("tg.an.f.region", "Регион")) + preTag("region") + "</label>"
    + '<select id="wzf-region" data-fk="region" data-scope="must">'
    + '<option value="">' + esc(T("tg.wz.region_pick", "Выберите регион")) + "</option>"
    + REGIONS.map(c => '<option value="' + c + '"' + (c === cur ? " selected" : "") + ">" + esc(regionName(c)) + "</option>").join("") + "</select>"
    + preDiff("region", cur)
    + '<span class="ferr" id="wzErr-region">' + esc(err || "") + "</span></div>"
    + wzRegionTextHtml(cur === "other");
}
/* «Другое»: территория страхования текстом (must.region_text, до 120 знаков, без имён людей) — обязательно */
function wzRegionTextHtml(show){
  const v = CH.must.region_text || "", err = CH.errs.region_text;
  return '<div id="wzRegionText" class="' + (err ? "bad" : "") + '"' + (show ? "" : " hidden") + '><label class="f" for="wzf-region_text">'
    + esc(T("tg.act.region_text", "Территория страхования")) + "</label>"
    + '<input id="wzf-region_text" type="text" autocomplete="off" enterkeyhint="done" maxlength="120" data-fk="region_text" data-scope="must" value="'
    + esc(v) + '" placeholder="' + esc(T("tg.act.region_text_ph", "например: Республика Казахстан, маршрут Ташкент–Алматы")) + '">'
    + '<span class="hint">' + esc(T("tg.act.region_text_hint", "Обязательно при «Другое». До 120 знаков: страна, область или маршрут — без имён людей.")) + "</span>"
    + '<span class="ferr" id="wzErr-region_text">' + esc(err || "") + "</span></div>";
}
/* срок страхования в днях — необязательный (сервер: term_days 1…3660, без него 365); из договора подставляется */
function wzTermHtml(){
  const v = CH.opt.term_days, err = CH.errs.term_days;
  return '<div class="' + (err ? "bad" : "") + '"><label class="f" for="wzo-term_days">' + esc(T("tg.act.f.term_days", "Срок страхования, дней")) + preTag("term_days") + "</label>"
    + '<input id="wzo-term_days" class="w-short" type="text" inputmode="numeric" enterkeyhint="done" autocomplete="off" maxlength="4" data-int="1" data-fk="term_days" data-scope="opt"'
    + ' value="' + esc(v == null ? "" : v) + '" placeholder="365">'
    + '<span class="hint">' + esc(T("tg.act.term_hint", "Необязательно. Не указан — премия считается за 365 дней.")) + "</span>"
    + (CH.preDoc.term_dates ? '<span class="hint">' + esc(T("tg.dq.term_doc", "В документе: {t}", {t: dqTermText({term_from: CH.preDoc.term_dates.split("/")[0],
      term_to: CH.preDoc.term_dates.split("/")[1]}, true)})) + "</span>" : "")
    + preDiff("term_days", v)
    + '<span class="ferr" id="wzErr-term_days">' + esc(err || "") + "</span></div>";
}
/* «Дополнительно»: необязательные поля лёгкого движка (ТЗ, п. 6 и 8.1–8.4) */
const OPT_KEYS = ["year", "location", "guard", "protection", "seismic_zone", "construction", "activity",
  "losses_count", "losses_small", "losses_amount", "want_lower", "price_new", "purchase_year", "req_rate"];
/* выпадающий список «Дополнительно»; пусто — сервер берёт значение по умолчанию и пишет это в акте */
function optSelect(key, label, codes, nameFn, hint){
  const v = CH.opt[key] == null ? "" : String(CH.opt[key]), err = CH.errs[key];
  return '<div class="' + (err ? "bad" : "") + '"><label class="f" for="wzo-' + key + '">' + esc(label) + "</label>"
    + '<select id="wzo-' + key + '" data-fk="' + key + '" data-scope="opt"><option value="">' + esc(T("tg.act.opt_unset", "не указано")) + "</option>"
    + codes.map(c => '<option value="' + esc(c) + '"' + (String(c) === v ? " selected" : "") + ">" + esc(nameFn(c)) + "</option>").join("") + "</select>"
    + (hint ? '<span class="hint">' + esc(hint) + "</span>" : "")
    + wzErrHtml(key, err) + "</div>";
}
/* обязательный вид страхования (ставка по нормативному акту): франшиза не применяется */
function wzStatutory(){
  const p = CH.must.product_code ? wzProd(CH.must.product_code) : null;
  return !!p && p.pricing_mode === "нормативный акт";
}
/* защита объекта, сейсмичность, конструкция, деятельность — только те, что считаются для класса */
function wzObjHtml(){
  const cls = wzClass();
  if (!cls) return '<div class="full"><p class="hint">' + esc(T("tg.act.obj_pick_class", "Защита объекта, сейсмичность и конструкция появятся, когда выберете продукт или класс.")) + "</p></div>";
  const codes = protCodes(cls);
  if (!codes.length) return "";
  const mk = tplMustKeys();
  let h = mk.protection ? "" : optSelect("protection", cls === "3" ? T("tg.act.f.protection3", "Противоугонная система") : T("tg.act.f.protection", "Защита объекта"),
    codes, c => protName(cls, c), T("tg.act.protection_hint", "Уточняет сценарии убытка PML, EML, MFL."));
  if (isProp(cls)) {
    h += (mk.seismic_zone ? "" : optSelect("seismic_zone", T("tg.act.f.seismic_zone", "Сейсмическая зона"), SEISMIC,
      n => T("tg.act.seismic_n", "{n} баллов", {n: n}), T("tg.act.seismic_hint", "Не указана — MFL считается как полное уничтожение объекта.")))
      + (mk.construction ? "" : optSelect("construction", T("tg.act.f.construction", "Конструкция, материал стен"), CONSTRUCTIONS, consName))
      + (mk.activity ? "" : optSelect("activity", T("tg.act.f.activity", "Деятельность на объекте"), ACTIVITIES, actvName));
  }
  return h;
}
/* франшиза сотрудника: по умолчанию «Не применять»; для обязательных видов блока нет — только пояснение */
function frEqText(){
  const f = CH.fr, S = Number(CH.must.sum_insured) || 0;
  if (!S) return T("tg.act.fr_need_sum", "Укажите страховую сумму — покажем размер франшизы и в сумах, и в процентах.");
  if (f.unit === "amount") {
    const a = Number(f.amount) || 0;
    return a > 0 ? T("tg.act.fr_eq_pct", "Это {p} страховой суммы. Не больше половины суммы.", {p: pct(a / S * 100, 2)})
      : T("tg.act.fr_max_amount", "Не больше половины страховой суммы.");
  }
  const p = dec(f.pct);
  return p > 0 && p <= 50 ? T("tg.act.fr_eq_amount", "Это {a}. Не больше 50 % страховой суммы.", {a: money(Math.round(S * p / 100))})
    : T("tg.act.fr_max_pct", "Не больше 50 % страховой суммы.");
}
function frTypeHint(t){
  switch (t) {
    case "conditional": return T("tg.act.frh.conditional", "Убыток меньше франшизы не возмещается, больше — возмещается полностью.");
    case "peril": return T("tg.act.frh.peril", "Франшиза только по одному риску. Её влияние на премию система не считает — решает андеррайтер.");
    default: return T("tg.act.frh.unconditional", "Из каждого возмещения вычитается размер франшизы.");
  }
}
function wzFrHtml(){
  const title = '<div class="h3">' + esc(T("tg.act.fr_title", "Франшиза")) + "</div>";
  if (wzStatutory()) {
    return title + '<p class="note">' + esc(T("tg.act.fr_statutory_hint", "Обязательный вид страхования: франшиза не применяется, премия — по нормативному акту.")) + "</p>";
  }
  const f = CH.fr, err = CH.errs.fr;
  const seg = (k, v, on, text) => '<button type="button" data-fr="' + k + '" data-value="' + v + '" aria-pressed="' + (!!on) + '">' + esc(text) + "</button>";
  let h = title + '<div class="segsel" role="group" aria-label="' + esc(T("tg.act.fr_title", "Франшиза")) + '">'
    + seg("on", "no", !f.on, T("tg.act.fr_off", "Не применять")) + seg("on", "yes", f.on, T("tg.act.fr_on", "Применить свою")) + "</div>";
  if (!f.on) {
    return h + '<p class="hint">' + esc(T("tg.act.fr_off_hint", "Без франшизы премия считается полностью. Если основания для франшизы есть, акт предложит её размер — применить можно одной кнопкой.")) + "</p>";
  }
  h += '<label class="f">' + esc(T("tg.act.fr_type", "Вид франшизы")) + "</label>"
    + '<div class="segsel" role="group" aria-label="' + esc(T("tg.act.fr_type", "Вид франшизы")) + '">'
    + FR_TYPES.map(t => seg("type", t, f.type === t, frTypeName(t))).join("") + "</div>"
    + '<span class="hint">' + esc(frTypeHint(f.type)) + "</span>"
    + '<label class="f" for="wzo-frval">' + esc(T("tg.act.fr_size", "Размер")) + "</label>"
    + '<div class="segsel" role="group" aria-label="' + esc(T("tg.act.fr_size", "Размер")) + '">'
    + seg("unit", "pct", f.unit === "pct", T("tg.act.fr_unit_pct", "в % от страховой суммы"))
    + seg("unit", "amount", f.unit === "amount", T("tg.act.fr_unit_amount", "суммой, в сумах")) + "</div>"
    + '<div class="fr-val' + (err ? " bad" : "") + '"><input id="wzo-frval" type="text" autocomplete="off" enterkeyhint="done" data-frval="1"'
    + (f.unit === "amount"
      ? ' inputmode="numeric" value="' + esc(f.amount ? Number(f.amount).toLocaleString(LOC()) : "") + '" placeholder="0"'
      : ' inputmode="decimal" maxlength="6" value="' + esc(f.pct || "") + '" placeholder="1"')
    + '><span class="unit">' + esc(f.unit === "amount" ? SUM() : "%") + "</span></div>"
    + '<span class="hint" id="wzFrEq">' + esc(frEqText()) + "</span>"
    + '<span class="ferr" id="wzErr-fr">' + esc(err || "") + "</span>";
  return h;
}
function wzFrSet(k, v){
  const f = CH.fr, S = Number(CH.must.sum_insured) || 0;
  if (k === "on") f.on = v === "yes";
  else if (k === "type" && FR_TYPES.indexOf(v) >= 0) f.type = v;
  else if (k === "unit" && (v === "pct" || v === "amount") && v !== f.unit) {
    // смена единицы — пересчёт уже введённого размера, чтобы не вводить заново
    if (v === "amount") { const p = dec(f.pct); f.amount = p > 0 && S ? Math.round(S * p / 100) : null; }
    else { const a = Number(f.amount) || 0; f.pct = a > 0 && S ? nf(Math.round(a / S * 10000) / 100, dp(Math.round(a / S * 10000) / 100)) : ""; }
    f.unit = v;
  }
  wzClearErr("fr");
  haptic("select");
  wzSave();
  wzPart("fr");
  wzMoreCount();
}
function wzFrInput(el){
  const f = CH.fr;
  if (f.unit === "amount") { groupDigitsLive(el); f.amount = num(el.value) || null; }
  else {
    f.pct = decimalLive(el);
  }
  const eq = $("#wzFrEq");
  if (eq) eq.textContent = frEqText();
  wzClearErr("fr");
  wzSave();
}
/* «Дополнительно · заполнено N» — без перерисовки блока */
function wzOptCount(){
  const mk = tplMustKeys();
  return OPT_KEYS.filter(k => !mk[k] && CH.opt[k] != null && CH.opt[k] !== "").length + (CH.fr.on && !wzStatutory() ? 1 : 0) + tplMoreCount();
}
function wzMoreCount(){
  const b = $("#wzMoreN");
  if (b) { const n = wzOptCount(); b.textContent = n ? " · " + T("tg.wz.more_n", "заполнено {n}", {n: n}) : ""; }
}
function optInput(key, label, kind, hint){
  const v = CH.opt[key], err = CH.errs[key];
  const money = kind === "money";
  return '<div class="' + (err ? "bad" : "") + '"><label class="f" for="wzo-' + key + '">' + esc(label)
    + (money ? ' <span class="unit">' + esc(SUM()) + "</span>" : "") + (key === "year" ? pfMarkHtml("m", "year") : "") + "</label>"
    + '<input id="wzo-' + key + '" type="text" autocomplete="off" inputmode="numeric" data-fk="' + key + '" data-scope="opt"'
    + (money ? ' data-money="1"' : ' data-int="1" maxlength="4"')
    + ' value="' + esc(v == null || v === "" ? "" : money ? Number(v).toLocaleString(LOC()) : v) + '">'
    + (hint ? '<span class="hint">' + esc(hint) + "</span>" : "")
    + wzErrHtml(key, err) + "</div>";
}
function optYesNo(key, label){
  const v = CH.opt[key], err = CH.errs[key];
  return '<div class="full' + (err ? " bad" : "") + '"><label class="f">' + esc(label) + "</label>"
    + '<div class="segsel" role="group" aria-label="' + esc(label) + '">'
    + '<button type="button" data-set="' + key + '" data-value="yes" aria-pressed="' + (v === "yes") + '">' + esc(T("tg.act.yes", "да")) + "</button>"
    + '<button type="button" data-set="' + key + '" data-value="no" aria-pressed="' + (v === "no") + '">' + esc(T("tg.act.no", "нет")) + "</button>"
    + "</div>"
    + wzErrHtml(key, err) + "</div>";
}
function wzMoreHtml(){
  const n = wzOptCount();
  const loc = CH.opt.location || "";
  const mk = tplMustKeys();                   // что шаблон уже спросил в «Полях класса»
  return '<details class="sec" id="wzMore"' + (CH.optOpen ? " open" : "") + '><summary><span><b>' + esc(T("tg.wz.more", "Дополнительно"))
    + '<span id="wzMoreN">' + (n ? " · " + esc(T("tg.wz.more_n", "заполнено {n}", {n: n})) : "") + "</span></b><small>"
    + esc(T("tg.act.more_sub", "Год, место, охрана и защита, убытки, франшиза — по ним уточняются уровень риска, сценарии убытка и премия. Можно не заполнять.")) + "</small></span></summary>"
    + '<div class="in"><div class="fgrid">'
    + (mk.year || obOn() ? "" : optInput("year", T("tg.act.f.year", "Год выпуска"), "year"))
    + (mk.location ? "" : '<div class="' + (CH.errs.location ? "bad" : "") + '"><label class="f" for="wzo-location">' + esc(T("tg.act.f.location", "Место эксплуатации")) + "</label>"
    + '<select id="wzo-location" data-fk="location" data-scope="opt"><option value="">' + esc(T("common.not_set", "не задан")) + "</option>"
    + LOCATIONS.map(c => '<option value="' + c + '"' + (c === loc ? " selected" : "") + ">" + esc(locName(c)) + "</option>").join("") + "</select>"
    + '<span class="ferr" id="wzErr-location">' + esc(CH.errs.location || "") + "</span></div>")
    + (mk.guard ? "" : optYesNo("guard", T("tg.act.guard", "Охрана объекта")))
    + wzObjHtml()
    + tplMoreHtml()
    + '<div class="full"><div class="h3">' + esc(T("tg.act.losses", "Убытки за три года")) + "</div></div>"
    + optInput("losses_count", T("tg.act.losses_count", "Всего убытков"), "int")
    + optInput("losses_small", T("tg.act.losses_small", "Из них мелких"), "int")
    + optInput("losses_amount", T("tg.act.losses_amount", "Сумма убытков"), "money")
    + optYesNo("want_lower", T("tg.act.want_lower", "Клиент просит снизить премию"))
    + optInput("price_new", T("tg.act.price_new", "Цена покупки"), "money", T("tg.act.price_hint", "С годом покупки покажем стоимость с учётом износа — как ориентир."))
    + optInput("purchase_year", T("tg.act.purchase_year", "Год покупки"), "year")
    + rqRateHtml()
    + '<div class="full fr-set" id="wzFr">' + wzFrHtml() + "</div>"
    + "</div></div></details>";
}
/* запрошенная ставка (01.10.2026): необязательно; уходит в акт как optional.requested_rate_pct. Пусто — сервер берёт
   тариф из договора, иначе из запроса филиала (app/act.py, _below_min_block): он показан как подставленный, ввод
   сотрудника главнее. Ниже минимальной ставки — акт отвечает, можно ли застраховать (below_min_assessment). */
function rqDocRate(){
  for (const k of ["ct", "br"]) {
    const d = CH[k], v = d && d.req ? d.req.tariff_pct : null;
    if (isNum(v) && Number(v) > 0) return {v: Number(v), k: k};
  }
  return null;
}
function rqSrcText(){
  const d = rqDocRate(), own = String(CH.opt.req_rate == null ? "" : CH.opt.req_rate).trim() !== "";
  if (!d) return "";
  const p = pct(d.v, Math.max(2, dp(d.v)));
  if (own) return d.k === "ct" ? T("tg.act.req_rate_own_ct", "В договоре — {p}; в акт уйдёт ваша ставка.", {p: p})
    : T("tg.act.req_rate_own_br", "В запросе филиала — {p}; в акт уйдёт ваша ставка.", {p: p});
  return d.k === "ct" ? T("tg.act.req_rate_doc_ct", "Подставлено из договора: {p}. Впишите свою — акт возьмёт её.", {p: p})
    : T("tg.act.req_rate_doc_br", "Подставлено из запроса филиала: {p}. Впишите свою — акт возьмёт её.", {p: p});
}
function rqSrcPaint(){
  const s = $("#wzRqSrc"), el = $("#wzo-req_rate"), d = rqDocRate();
  if (s) s.textContent = rqSrcText();
  if (el) el.placeholder = d ? nf(d.v, dp(d.v)) : "0,05";
}
function rqRateHtml(){
  const v = CH.opt.req_rate, err = CH.errs.req_rate, d = rqDocRate();
  return '<div class="full rq-rate' + (err ? " bad" : "") + '"><label class="f" for="wzo-req_rate">' + esc(T("tg.act.f.req_rate", "Запрошенная ставка, % годовых")) + "</label>"
    + '<div class="fr-val"><input id="wzo-req_rate" type="text" inputmode="decimal" autocomplete="off" enterkeyhint="done" maxlength="8" data-fk="req_rate" data-scope="opt" data-decimal="1"'
    + ' value="' + esc(v == null ? "" : v) + '" placeholder="' + esc(d ? nf(d.v, dp(d.v)) : "0,05") + '"><span class="unit">%</span></div>'
    + '<span class="hint">' + esc(T("tg.act.req_rate_hint", "Необязательно. Если филиал или клиент просят ставку ниже тарифа — впишите, акт оценит, можно ли.")) + "</span>"
    + '<span class="hint rq-src" id="wzRqSrc">' + esc(rqSrcText()) + "</span>"
    + '<span class="ferr" id="wzErr-req_rate">' + esc(err || "") + "</span></div>";
}
function wzClearErr(key){
  if (!CH.errs[key]) return;
  delete CH.errs[key];
  const e = $("#wzErr-" + key);
  if (e) { e.textContent = ""; if (e.parentNode) e.parentNode.classList.remove("bad"); }
}
/* ввод в поле: значение запоминаем, форму не перерисовываем — курсор остаётся на месте */
function wzFieldInput(el){
  if (el.dataset.money) groupDigitsLive(el);
  if (el.dataset.int && /\D/.test(el.value)) el.value = el.value.replace(/\D/g, "");
  if (el.dataset.decimal) decimalLive(el);
  const box = el.dataset.scope === "opt" ? CH.opt : CH.must, key = el.dataset.fk, raw = String(el.value || "").trim();
  if (raw === "") delete box[key];
  else box[key] = el.dataset.money ? num(raw) : raw;
  if (key === "region") {
    try { localStorage.setItem(CH_REGION, raw); } catch (e) {}
    CH.seeded = false;
    const rt = $("#wzRegionText");
    if (rt) rt.hidden = raw !== "other";      // поле территории — только при «Другое»
    if (raw !== "other") wzClearErr("region_text");
  }
  if (el.dataset.scope === "opt" && key === "year") pfDrop("m", "year");   // год исправлен руками — он уже не подсказка
  preClear(key);                              // сотрудник поправил значение из документа — оно уже его
  wzClearErr(key);
  if (key === "losses_count" || key === "losses_small" || key === "losses_amount") wzClearErr("losses_count");
  if (key === "sum_insured") { const eq = $("#wzFrEq"); if (eq) eq.textContent = frEqText(); ptPaintLive(); }
  if (key === "object_value") mkPart("sum");   // расхождение с медианой объявлений — сразу
  if (key === "req_rate") rqSrcPaint();
  if (el.dataset.scope === "opt") wzMoreCount();
  wzSave();
}
function wzSetOpt(b){
  const k = b.dataset.set, v = b.dataset.value;
  const on = CH.opt[k] === v;
  if (on) delete CH.opt[k]; else CH.opt[k] = v;
  b.parentNode.querySelectorAll("[data-set]").forEach(x => x.setAttribute("aria-pressed", String(!on && x === b)));
  wzClearErr(k);
  haptic("select");
  wzMoreCount();
  wzSave();
}
function wzQuickAdd(b){
  const key = b.parentNode.dataset.for, m = CH.must;
  if (!key) return;
  if (b.dataset.add) m[key] = (num(m[key]) || 0) + Number(b.dataset.add);
  else { const v = num(m[b.dataset.copy]); if (!v) return; m[key] = v; }
  const el = $("#wzf-" + key);
  if (el) el.value = Number(m[key]).toLocaleString(LOC());
  preClear(key);
  if (key === "sum_insured") { const eq = $("#wzFrEq"); if (eq) eq.textContent = frEqText(); ptPaintLive(); }
  if (key === "object_value") mkPart("sum");
  wzClearErr(key);
  haptic("select");
  wzSave();
}
function wzFocusField(key){
  setTimeout(() => {
    const el = key === "prod" ? ($("#wzProdQ") || $("#wzErr-prod"))
      : key === "market" ? $("#mkCard")
      : key === "br" || key === "ct" || key === "cb" ? ($("#wzErr-" + key) || $("#" + key + "Card"))
      : key === "fr" ? ($("#wzo-frval") || document.querySelector("[data-fr]"))
      : key === "parts" ? $("#ptCard")
      : key === "ob" ? (document.querySelector("#obCard .bad input, #obCard .bad select") || $("#obCard"))
      : key === "cf" ? ($("#tplCard") || $("#ptCard"))
      : ($("#wzf-" + key) || $("#wzo-" + key) || $("#tf-m-" + key) || document.querySelector('[data-set="' + key + '"]'));
    if (el) { if (el.scrollIntoView) el.scrollIntoView({block: "center"}); try { el.focus({preventScroll: true}); } catch (e) {} }
  }, 60);
}

/* =====================================================================================
   Шаблоны классов (справочник class_templates, приложение А; 30.09.2026): GET /act/templates/{класс}?lang=.
   Шаг «Проверить»: поля must шаблона — карточка «Поля класса», поля optional — в «Дополнительно».
   Значение уходит туда, куда указывает поле input шаблона: optional.class_fields.<код>, optional.year,
   optional.object_kind, optional.<уточнение> (защита, место, конструкция…), recognized.<ключ> (марка, модель).
   Подписи полей и видов объекта — из ответа сервера на языке интерфейса; варианты выбора — подписи словаря.
   Шаблон кэшируется по классу и языку: смена языка запрашивает его заново.
   ===================================================================================== */
const TPL = {cache: {}, busy: {}};
const TPL_SKIP = ["losses", "deductible"];            // убытки и франшиза — свои блоки «Дополнительно»
const TPL_OPT = ["year", "location", "guard", "protection", "construction", "activity", "seismic_zone"];
const tplKey = cls => String(cls) + "|" + I18N_LANG;
function tplOf(cls){ const x = cls ? TPL.cache[tplKey(cls)] : null; return x && x.template ? x : null; }
function tplNeed(cls){
  if (!cls) return;
  const k = tplKey(cls);
  if (TPL.cache[k] || TPL.busy[k]) return;
  TPL.busy[k] = true;
  api("/act/templates/" + encodeURIComponent(cls) + "?lang=" + encodeURIComponent(I18N_LANG)).then(r => {
    delete TPL.busy[k];
    TPL.cache[k] = r.ok && r.data && r.data.template && typeof r.data.template === "object" ? r.data : {err: r.error || ""};
    if (TAB === "chat" && k === tplKey(cls)) tplRepaint();
  });
}
function tplRepaint(){
  pfApply();                                   // шаблон пришёл: подгруппа и топливо ТС — в поля класса
  if (CH.wz === 1) wzPart("views");
  else if (CH.wz === 2) { wzPart("tpl"); wzPart("parts"); wzPart("more"); wzPart("obj"); }
}
/* куда уходит значение поля: {t: cf | opt | kind | rec, key} */
function tfTarget(f){
  if (!f || typeof f !== "object") return null;
  if (f.code === "__kind" || f.type === "kind") return {t: "kind", key: "object_kind"};
  const inp = String(f.input || "");
  let m = /^optional\.class_fields\.([A-Za-z0-9_]{1,40})$/.exec(inp);
  if (m) return {t: "cf", key: m[1]};
  m = /^optional\.([a-z_0-9]{1,40})$/.exec(inp);
  if (m && TPL_OPT.indexOf(m[1]) >= 0) return {t: "opt", key: m[1]};
  m = /^recognized\.([a-z_]{1,40})$/.exec(inp);
  if (m) return {t: "rec", key: m[1]};
  return null;
}
/* поля шаблона, которые вводятся здесь: без вида объекта (он — отдельным списком), убытков и франшизы */
const tfList = (fs, noRec) => anArr(fs).filter(f => {
  const g = tfTarget(f);
  return g && g.t !== "kind" && TPL_SKIP.indexOf(f.type) < 0 && !(noRec && g.t === "rec") && anStr(f.label);
});
const tfScopeCls = s => s === "m" || String(s).charAt(0) === "o" ? wzClass() : ((CH.pt && CH.pt.items[Number(s)]) || {}).cls || "";
function tfKindField(cls){
  const t = tplOf(cls);
  const own = t ? anArr(t.template.must).concat(anArr(t.template.optional)).filter(f => f && f.type === "kind")[0] : null;
  return {code: "__kind", type: "kind", input: "optional.object_kind", label: own && anStr(own.label) ? own.label : T("tg.tpl.kind", "Вид объекта")};
}
function tfDef(s, code){
  const cls = tfScopeCls(s);
  if (code === "__kind") return tfKindField(cls);
  const t = tplOf(cls);
  if (!t) return null;
  return anArr(t.template.must).concat(anArr(t.template.optional)).filter(f => f && f.code === code)[0] || null;
}
function tfBox(s){
  if (s === "m") { if (!CH.opt.cf || typeof CH.opt.cf !== "object") CH.opt.cf = {}; return {cf: CH.opt.cf, opt: CH.opt, kind: CH.opt}; }
  // объект парка: «o0», «o1»… — вид объекта в it.kind, поля класса в it.cf
  if (String(s).charAt(0) === "o") { const o = CH.ob && CH.ob.items[Number(String(s).slice(1))]; return o ? {cf: o.cf, opt: o.f, kind: o} : null; }
  const it = CH.pt && CH.pt.items[Number(s)];
  return it ? {cf: it.cf, opt: it.f, kind: it} : null;
}
function tfVal(s, g){
  if (g.t === "rec") { const r = CH.rec.filter(x => x.key === g.key)[0]; return r ? r.value : ""; }
  const b = tfBox(s);
  if (!b) return "";
  const v = g.t === "cf" ? b.cf[g.key] : g.t === "kind" ? b.kind[s === "m" ? "object_kind" : "kind"] : b.opt[g.key];
  return v == null ? "" : v;
}
function tfSet(s, g, v){
  if (g.t === "rec") {
    const r = CH.rec.filter(x => x.key === g.key)[0];
    if (r) { r.value = v == null ? "" : String(v); r.source = r.orig && r.value.trim() === r.orig.value ? r.orig.source : "input"; }
    else if (v != null && v !== "") CH.rec.push({key: g.key, value: String(v), source: "input", file: null, orig: null});
    if (g.key === "brand" || g.key === "model") mkLinksSoon();
    return;
  }
  const b = tfBox(s);
  if (!b) return;
  const box = g.t === "cf" ? b.cf : g.t === "kind" ? b.kind : b.opt;
  const key = g.t === "kind" ? (s === "m" ? "object_kind" : "kind") : g.key;
  if (v == null || v === "") delete box[key]; else box[key] = v;
}
function cfOptDict(field, c, i){
  switch (field + "." + c) {
    case "activity_kind.consult": return T("tg.tpl.o.activity_kind.consult", "консультации, офисные услуги");
    case "activity_kind.trade": return T("tg.tpl.o.activity_kind.trade", "торговля");
    case "activity_kind.transport": return T("tg.tpl.o.activity_kind.transport", "перевозки");
    case "activity_kind.build": return T("tg.tpl.o.activity_kind.build", "строительство");
    case "activity_kind.medical": return T("tg.tpl.o.activity_kind.medical", "медицина");
    case "activity_kind.hazard": return T("tg.tpl.o.activity_kind.hazard", "опасное производство");
    case "transport_mode.auto": return T("tg.tpl.o.transport_mode.auto", "автомобильный");
    case "transport_mode.rail": return T("tg.tpl.o.transport_mode.rail", "железнодорожный");
    case "transport_mode.air": return T("tg.tpl.o.transport_mode.air", "воздушный");
    case "transport_mode.sea": return T("tg.tpl.o.transport_mode.sea", "морской");
    case "transport_mode.multi": return T("tg.tpl.o.transport_mode.multi", "смешанный");
    case "packaging.container": return T("tg.tpl.o.packaging.container", "контейнер");
    case "packaging.factory": return T("tg.tpl.o.packaging.factory", "заводская упаковка");
    case "packaging.standard": return T("tg.tpl.o.packaging.standard", "обычная упаковка");
    case "packaging.none": return T("tg.tpl.o.packaging.none", "без упаковки");
    case "borrower_industry.manufacturing": return T("tg.tpl.o.borrower_industry.manufacturing", "производство");
    case "borrower_industry.services": return T("tg.tpl.o.borrower_industry.services", "услуги");
    case "borrower_industry.trade": return T("tg.tpl.o.borrower_industry.trade", "торговля");
    case "borrower_industry.build": return T("tg.tpl.o.borrower_industry.build", "строительство");
    case "borrower_industry.agro": return T("tg.tpl.o.borrower_industry.agro", "сельское хозяйство");
    case "credit_history.clean": return T("tg.tpl.o.credit_history.clean", "без просрочек");
    case "credit_history.minor": return T("tg.tpl.o.credit_history.minor", "мелкие просрочки");
    case "credit_history.bad": return T("tg.tpl.o.credit_history.bad", "серьёзные просрочки");
    default: return T("tg.tpl.o_n", "вариант {n}", {n: i + 1});
  }
}
function tfOptName(f, g, cls, c, i){
  const own = anStr(anObj(f.option_labels)[c]).trim();
  if (own) return own;
  if (g.t === "opt") {
    if (g.key === "protection") return protName(cls === "3" ? "3" : "8", c);
    if (g.key === "location") return locName(c);
    if (g.key === "construction") return consName(c);
    if (g.key === "activity") return actvName(c);
  }
  return cfOptDict(f.code, c, i);
}
/* одно поле шаблона; s — "m" (договор) или номер части */
function tfHtml(f, s, cls){
  const g = tfTarget(f);
  if (!g) return "";
  const id = "tf-" + s + "-" + String(f.code).replace(/[^\w]/g, ""), v = tfVal(s, g), label = anStr(f.label);
  const at = ' data-tf="1" data-tfs="' + esc(s) + '" data-tfc="' + esc(f.code) + '"';
  // уточнение акта (год, конструкция…) в «Полях класса»: ошибка проверки — под самим полем
  const ek = s === "m" && g.t === "opt" ? g.key : "";
  const ferr = ek ? '<span class="ferr" id="wzErr-' + ek + '">' + esc(CH.errs[ek] || "") + "</span>" : "";
  const pfm = pfMarkHtml(s, pfFieldKey(g));
  // автозаполнение ТС было, а пробега нет ни в техпаспорте, ни на фото — подсвечиваем: «введите»
  const need = s === "m" && g.t === "rec" && g.key === "mileage" && v === "" && !!(CH.vp && anArr(CH.vp.items).length);
  const bad = (ek && CH.errs[ek] ? " bad" : "") + (need ? " pf-need" : "");
  const needH = need ? '<span class="hint">' + esc(T("tg.act.prefill_mileage", "Введите пробег: в техпаспорте его нет, с фото он не берётся.")) + "</span>" : "";
  if (f.type === "bool") {
    const on = x => '<button type="button" data-tfb="' + x + '" data-tfs="' + esc(s) + '" data-tfc="' + esc(f.code) + '" aria-pressed="' + (v === x) + '">'
      + esc(x === "yes" ? T("tg.act.yes", "да") : T("tg.act.no", "нет")) + "</button>";
    return '<div><label class="f">' + esc(label) + '</label><div class="segsel" role="group" aria-label="' + esc(label) + '">' + on("yes") + on("no") + "</div></div>";
  }
  const lab = '<label class="f" for="' + id + '">' + esc(label) + (f.type === "money" ? ' <span class="unit">' + esc(SUM()) + "</span>" : "") + pfm + "</label>";
  if (f.type === "kind" || f.type === "choice") {
    let opts;
    if (f.type === "kind") {
      const t = tplOf(cls);
      // единственный вид объекта класса (кредит, груз…): сервер берёт его по умолчанию (object.single_kind) — выбирать нечего
      if (t && t.template.object && t.template.object.single_kind) return "";
      opts =anArr(t && t.template.object && t.template.object.kinds).filter(k => k && k.code && anStr(k.label)).map(k => [String(k.code), anStr(k.label)]);
    } else {
      opts = anArr(f.options).map((c, i) => [String(c), tfOptName(f, g, cls, String(c), i)]);
    }
    if (!opts.length) return "";
    const hint = s === "m" && f.type === "kind" && !v && CH.kind && opts.some(o => o[0] === CH.kind)
      ? '<span class="hint">' + esc(T("tg.tpl.kind_photo", "По фото: {k}. Не выбрано — акт возьмёт вид с фото.", {k: opts.filter(o => o[0] === CH.kind)[0][1]})) + "</span>" : "";
    return '<div class="' + bad + '">' + lab + '<select id="' + id + '"' + at + '><option value="">' + esc(T("tg.act.opt_unset", "не указано")) + "</option>"
      + opts.map(o => '<option value="' + esc(o[0]) + '"' + (o[0] === String(v) ? " selected" : "") + ">" + esc(o[1]) + "</option>").join("") + "</select>" + hint + ferr + "</div>";
  }
  const money = f.type === "money", digits = f.type === "int" || f.type === "year";
  const shown = v === "" ? "" : money ? Number(v).toLocaleString(LOC()) : String(v);
  return '<div class="' + (f.type === "text" ? "full" : "") + bad + '">' + lab + '<input id="' + id + '" type="text" autocomplete="off" enterkeyhint="done"' + at
    + (money ? ' inputmode="numeric" placeholder="0"' : digits ? ' inputmode="numeric" maxlength="' + (f.type === "year" ? 4 : 9) + '"'
      : f.type === "number" ? ' inputmode="decimal" maxlength="14"' : ' maxlength="120"')
    + ' value="' + esc(shown) + '">' + needH + ferr + "</div>";
}
function tfInput(el){
  const s = el.dataset.tfs, f = tfDef(s, el.dataset.tfc);
  if (!f) return;
  const g = tfTarget(f);
  if (f.type === "money") groupDigitsLive(el);
  else if ((f.type === "int" || f.type === "year") && /\D/.test(el.value)) el.value = el.value.replace(/\D/g, "");
  else if (f.type === "number") decimalLive(el);
  const raw = String(el.value || "").trim();
  tfSet(s, g, raw === "" ? null : f.type === "money" ? num(raw) : raw);
  pfDrop(s, pfFieldKey(g));                    // исправлено руками — подсказка автозаполнения снята
  const w = el.closest && el.closest(".pf-need");
  if (w && raw) w.classList.remove("pf-need");
  tfDone(s, g.t === "opt" ? g.key : "");
}
function tfBool(b){
  const s = b.dataset.tfs, f = tfDef(s, b.dataset.tfc);
  if (!f) return;
  const g = tfTarget(f), v = b.dataset.tfb;
  const on = String(tfVal(s, g)) === v;
  tfSet(s, g, on ? null : v);
  pfDrop(s, pfFieldKey(g));
  b.parentNode.querySelectorAll("[data-tfb]").forEach(x => x.setAttribute("aria-pressed", String(!on && x === b)));
  haptic("select");
  tfDone(s, g.t === "opt" ? g.key : "");
}
function tfDone(s, key){
  wzClearErr("cf");
  if (key && s === "m") wzClearErr(key);
  if (s === "m") wzMoreCount(); else if (String(s).charAt(0) === "o") obClearBad(Number(String(s).slice(1)), "cf"); else ptEdited();
  wzSave();
}
/* в POST /act/make: значения по типу поля шаблона (деньги и числа — числами, да/нет — true/false) */
function tfCast(f, v){
  const t = f ? f.type : "";
  if (t === "bool") return v === "yes" ? true : v === "no" ? false : null;
  if (t === "money" || t === "int" || t === "year") { const x = num(v); return String(v).trim() === "" ? null : x; }
  if (t === "number") { const x = dec(v); return x == null || isNaN(x) ? null : x; }
  return String(v);
}
function tfBodyCf(cls, map){
  const t = tplOf(cls), out = {};
  const defs = t ? anArr(t.template.must).concat(anArr(t.template.optional)) : [];
  Object.keys(map || {}).forEach(k => {
    const f = defs.filter(x => x && x.code === k)[0];
    const v = f ? tfCast(f, map[k]) : map[k];
    if (v != null && v !== "") out[k] = v;
  });
  return Object.keys(out).length ? out : null;
}
/* ---------- автозаполнение ТС (02.10.2026; сервер — app/vehicle_prefill.py, ответ /act/photos) ----------
   prefill_fields: подпись (object_label), год, подгруппа (class_fields.veh_group), топливо (class_fields.fuel),
   характеристики. Значение встаёт только в пустое поле или в поле, где стоит прежняя подсказка; что сотрудник
   исправил руками — не трогаем. У подставленного поля — значок «!»: источник, уверенность, почему, «проверьте».
   Один объект — CH.pfM; объект парка — it.pf. Подсказки привязаны к объекту через file → photo_ids
   (объект один — ему всё). Пробег не подставляется никогда: поле подсвечено «введите». */
const PF_FIELDS = {"object_label": "label", "year": "year", "class_fields.veh_group": "cf.veh_group", "class_fields.fuel": "cf.fuel"};
const pfFieldKey = g => !g ? "" : g.t === "cf" ? "cf." + g.key : g.t === "opt" && g.key === "year" ? "year" : "";
const pfId = (s, k) => "pf-" + s + "-" + String(k).replace(/[^\w]/g, "_");
function pfStore(s){
  if (s === "m") { if (!CH.pfM || typeof CH.pfM !== "object") CH.pfM = {}; return CH.pfM; }
  const o = String(s).charAt(0) === "o" && CH.ob ? CH.ob.items[Number(String(s).slice(1))] : null;
  if (!o) return null;
  if (!o.pf || typeof o.pf !== "object") o.pf = {};
  return o.pf;
}
function pfGet(s, k){ const st = k ? pfStore(s) : null; return st && st[k] ? st[k] : null; }
/* есть ли поле класса k в шаблоне выбранного класса; null — шаблон ещё не загружен */
function pfCfHas(k){
  const t = tplOf(wzClass());
  if (!t) return null;
  return anArr(t.template.must).concat(anArr(t.template.optional)).some(f => { const g = tfTarget(f); return !!g && g.t === "cf" && g.key === k; });
}
function pfTarget(s, k){
  if (s === "m") {
    if (k === "year") return {box: CH.opt, key: "year"};
    if (k.indexOf("cf.") === 0) { if (!CH.opt.cf || typeof CH.opt.cf !== "object") CH.opt.cf = {}; return {box: CH.opt.cf, key: k.slice(3)}; }
    return null;
  }
  const o = CH.ob && CH.ob.items[Number(String(s).slice(1))];
  if (!o) return null;
  if (k === "label" || k === "year") return {box: o, key: k};
  if (k.indexOf("cf.") === 0) return {box: o.cf, key: k.slice(3)};
  return null;
}
const pfEntry = (x, v) => ({v: v, src: anStr(x.source), srcLabel: anStr(x.source_label), conf: Number(x.confidence) || 0,
  why: anStr(x.why).slice(0, 300), check: anStr(x.check_label), file: anStr(x.file)});
function pfApplyTo(s, list){
  const st = pfStore(s);
  if (!st) return 0;
  let n = 0;
  list.forEach(x => {
    const k = PF_FIELDS[x.field];
    if (!k || (s === "m" && k === "label")) return;          // у одного объекта подписи нет — она в «Распознано»
    if (k.indexOf("cf.") === 0 && pfCfHas(k.slice(3)) !== true) return;   // у класса нет такого поля (или шаблон не пришёл)
    const tg = pfTarget(s, k);
    if (!tg) return;
    const cur = tg.box[tg.key], v = String(x.value).slice(0, 120);
    const ours = st[k] && String(cur == null ? "" : cur) === String(st[k].v);
    if (cur != null && cur !== "" && !ours) return;          // сотрудник ввёл своё — не перезаписываем
    if (String(cur) !== v) n++;
    tg.box[tg.key] = v;
    st[k] = pfEntry(x, v);
  });
  return n;
}
/* подсказки, относящиеся к объекту парка i: по файлу; объект один — все */
function pfForObject(i){
  const items = CH.vp ? anArr(CH.vp.items) : [], o = CH.ob && CH.ob.items[i];
  if (!o) return [];
  return CH.ob.items.length === 1 ? items : items.filter(x => x.file && anArr(o.photos).indexOf(x.file) >= 0);
}
function pfApply(){
  const items = CH.vp ? anArr(CH.vp.items) : [];
  if (!items.length) return 0;
  if (obOn()) return CH.ob.items.reduce((a, o, i) => a + pfApplyTo("o" + i, pfForObject(i)), 0);
  return pfApplyTo("m", items);
}
function pfDrop(s, k){
  const st = k ? pfStore(s) : null;
  if (!st || !st[k]) return;
  delete st[k];
  const e = typeof document !== "undefined" ? document.getElementById(pfId(s, k)) : null;
  if (e) e.remove();
}
function pfSrcName(src){
  return src === "techpassport" ? T("tg.act.prefill_src_passport", "техпаспорт") : T("tg.act.prefill_src_photo", "фото");
}
function pfTip(e){
  return T("tg.act.prefill_src", "Заполнено автоматически: {src}, уверенность {conf} %.", {src: e.srcLabel || pfSrcName(e.src), conf: Math.round((Number(e.conf) || 0) * 100)})
    + (e.why ? " " + T("tg.act.prefill_why", "Почему: {why}.", {why: String(e.why).replace(/[.\s]+$/, "")}) : "")
    + " " + (e.check || T("tg.act.prefill_check", "Проверьте значение."));
}
function pfIconHtml(id, e){
  const tip = pfTip(e);
  return '<span class="pf" id="' + id + '"><button type="button" class="pf-i" data-pf="' + id + '" aria-expanded="false" aria-controls="' + id + '-p" title="' + esc(tip)
    + '" aria-label="' + esc(T("tg.act.prefill_aria", "Заполнено автоматически — пояснение")) + '">!</button>'
    + '<span class="pf-pop" id="' + id + '-p" role="note" hidden>' + esc(tip) + "</span></span>";
}
function pfMarkHtml(s, k){ const e = pfGet(s, k); return e ? pfIconHtml(pfId(s, k), e) : ""; }

/* признаки части: год, место, защита… (validate_part_fields в app/act.py) */
function tfBodyFields(f){
  const out = {};
  Object.keys(f || {}).forEach(k => {
    const v = f[k];
    if (v == null || v === "") return;
    if (k === "year" || k === "seismic_zone") out[k] = Number(v);
    else if (k === "guard") out[k] = v === "yes";
    else out[k] = String(v);
  });
  return out;
}
/* уточнения акта, которые шаблон показывает среди обязательных полей класса — в «Дополнительно» их нет */
function tplMustKeys(){
  const cls = wzClass(), t = tplMainShown() ? tplOf(cls) : null, out = {};
  if (t) tfList(t.template.must).forEach(f => { const g = tfTarget(f); if (g.t === "opt") out[g.key] = 1; });
  return out;
}
const tplMainShown = () => !!wzClass() && !ptOn() && !obOn();
/* карточка «Поля класса»: обязательные поля шаблона, вид объекта, документы по классу */
function tplCardHtml(){
  const cls = wzClass();
  if (!cls || ptOn() || obOn()) return "";      // у парка поля класса — в карточке каждого объекта
  tplNeed(cls);
  const x = TPL.cache[tplKey(cls)];
  const err = CH.errs.cf;
  let h = '<section class="card tpl-card' + (err ? " bad" : "") + '" id="tplCard"><h2>' + esc(T("tg.tpl.title", "Поля класса")) + "</h2>";
  if (!x) return h + '<p class="note">' + spin(T("common.loading", "загружаю…")) + "</p></section>";
  if (!x.template) return h + '<p class="note err">' + esc(T("tg.tpl.failed", "Шаблон класса не загрузился: {reason}. Поля класса можно не заполнять — акт отметит, чего не хватает.", {reason: x.err || T("tg.err.offline", "сервер не отвечает")})) + "</p></section>";
  const tp = x.template;
  h += '<p class="sub">' + esc([anStr(tp.name), anStr(tp.object && tp.object.description)].filter(Boolean).join(". "))
    + ' <span class="tpl-v">' + esc(T("tg.tpl.version", "шаблон {v}", {v: anStr(x.version)})) + "</span></p>"
    + '<div class="fgrid">' + tfHtml(tfKindField(cls), "m", cls) + tfList(tp.must).map(f => tfHtml(f, "m", cls)).join("") + "</div>"
    + '<p class="hint">' + esc(T("tg.tpl.must_hint", "Нужны для акта по этому классу. Не заполнено — акт покажет поле в «чего не хватает»; расчёт всё равно выполнится.")) + "</p>";
  const docs = anArr(tp.documents && tp.documents.items).map(anStr).filter(Boolean);
  if (docs.length) h += '<div class="h3">' + esc(T("tg.tpl.docs", "Документы по классу")) + '</div><ul class="tpl-docs">' + docs.map(d => "<li>" + esc(d) + "</li>").join("") + "</ul>";
  return h + '<span class="ferr" id="wzErr-cf">' + esc(err || "") + "</span></section>";
}
/* необязательные поля класса: обычные, затем поля факторов тарифа (factor: true) под своим подзаголовком.
   Ничего обязательным не становится: пустое поле акт покажет в «Уточнить» карточки «Факторы объекта». */
function tfFactorSplit(fs, render){
  const plain = fs.filter(f => f.factor !== true), fac = fs.filter(f => f.factor === true);
  return plain.map(render).join("")
    + (fac.length ? '<div class="full tf-fac-h"><div class="h3">' + esc(T("tg.tpl.factors_h", "Факторы тарифа")) + "</div>"
      + '<span class="hint">' + esc(T("tg.tpl.factors_hint", "Необязательно. Каждый вариант меняет ставку на свой коэффициент — акт покажет, на сколько.")) + "</span></div>"
      + fac.map(render).join("") : "");
}
/* «Дополнительно»: необязательные поля класса из шаблона */
function tplMoreHtml(){
  const cls = wzClass(), t = tplMainShown() ? tplOf(cls) : null;
  if (!t) return "";
  const fs = tfList(t.template.optional).filter(f => tfTarget(f).t !== "opt");
  if (!fs.length) return "";
  return '<div class="full"><div class="h3">' + esc(T("tg.tpl.more_h", "Поля класса: {name}", {name: anStr(t.template.name)})) + "</div></div>"
    + tfFactorSplit(fs, f => tfHtml(f, "m", cls));
}
function tplMoreCount(){
  const t = tplMainShown() ? tplOf(wzClass()) : null;
  if (!t) return 0;
  const cf = CH.opt.cf || {};
  return tfList(t.template.optional).filter(f => { const g = tfTarget(f); return g.t === "cf" && cf[g.key] != null && cf[g.key] !== ""; }).length;
}

/* =====================================================================================
   Комплексный продукт по частям (ТЗ универсального шаблона, 4.1в; сервер — app/act.py, _parts_plan / _parts_view).
   Шаг «Проверить», карточка «Части договора»: у продукта больше одного класса или сотрудник добавил часть.
   Суммы частей вводит сотрудник или предлагает первый расчёт (parts.suggested_parts); доля — сумма части к
   страховой сумме договора. Кнопка «Сформировать акт» неактивна, пока сумма частей расходится с договором больше
   чем на 1 сум (настройка parts.sum_tolerance). «Подтвердить распределение» → optional.parts и parts_confirmed.
   Стоимость части — только у классов 3–9 (ГК ст. 936, 938). Состояние — в sessionStorage вместе с мастером.
   ===================================================================================== */
const PT_TOL = 1;                                  // app/act_engine.py DEFAULTS["parts"]["sum_tolerance"], сумов
const PT_MAX = 10;                                 // app/act_engine.py MAX_PARTS
const ptValOk = cls => ["3", "4", "5", "6", "7", "8", "9"].indexOf(String(cls)) >= 0;
function ptProdClasses(){
  const m = CH.must;
  if (m.product_code) { const c = wzClassesOf(wzProd(m.product_code)); if (c.length) return c; }
  return m.class_code ? [m.class_code] : [];
}
const ptOn = () => !!wzClass() && !obOn() && (ptProdClasses().length > 1 || !!(CH.pt && CH.pt.items.length) || !!CH.ptAdd);
/* несколько объектов (парк ТС): только у продукта одного класса — сервер не принимает перечень объектов вместе
   с частями комплексного продукта (app/act.py, _objects_in) */
const obOk = () => ptProdClasses().length <= 1;
const obOn = () => !!(CH.ob && CH.ob.on && CH.ob.items && CH.ob.items.length) && !!wzClass() && obOk();
const ptItem = (cls, sum) => ({cls: String(cls), sum: sum > 0 ? sum : null, value: null, kind: "", f: {}, cf: {}, fr: "", desc: "", pcode: "", guess: false});
const ptSum = () => ((CH.pt && CH.pt.items) || []).reduce((a, x) => a + (Number(x.sum) || 0), 0);
function ptOk(){
  if (!CH.pt || !CH.pt.items.length) return true;
  const S = Number(CH.must.sum_insured) || 0;
  if (!S) return true;                               // нет страховой суммы — скажет обязательное поле
  return CH.pt.items.every(x => Number(x.sum) > 0) && Math.abs(ptSum() - S) <= PT_TOL;
}
function ptSrcName(src){
  switch (src) {
    case "default": return T("tg.pt.src.default", "поровну по умолчанию");
    case "contract": return T("tg.pt.src.contract", "по перечню объектов договора");
    case "policy_shares": return T("tg.pt.src.policy_shares", "по долям тарифной политики");
    default: return T("tg.pt.src.employee", "распределение сотрудника");
  }
}
function ptSrcText(){
  const pt = CH.pt;
  if (!pt || !pt.items.length) return T("tg.pt.src.none", "Распределение предложит первый расчёт");
  const P = CH.act && CH.act.parts && CH.act.parts.mode === "multi" ? CH.act.parts : null;
  const label = pt.edited ? ptSrcName("employee") : P && P.source === pt.src && anStr(P.source_label) ? anStr(P.source_label) : ptSrcName(pt.src);
  return T("tg.pt.src_line", "Источник: {s}", {s: label}) + (pt.confirmed ? " · " + T("tg.pt.confirmed_short", "подтверждено")
    : " — " + T("tg.pt.confirm_ask", "подтвердите"));
}
/* части из ответа сервера: предложение (suggested_parts) или посчитанные части; введённое у той же части — сохраняется */
function ptFromServer(P){
  const src = anArr(P.suggested_parts).length ? P.suggested_parts : anArr(P.items);
  const prev = CH.pt;
  CH.pt = {items: src.slice(0, PT_MAX).map((x, i) => {
      const cls = String(x.class_code || ""), old = prev && prev.items[i] && prev.items[i].cls === cls ? prev.items[i] : null;
      const it = ptItem(cls, Number(x.sum_insured) || 0);
      if (old) Object.assign(it, {kind: old.kind, f: old.f, cf: old.cf, fr: old.fr});
      if (!it.kind && x.object_kind) it.kind = String(x.object_kind);
      if (ptValOk(cls) && isNum(x.object_value) && Number(x.object_value) > 0) it.value = Number(x.object_value);
      it.desc = anStr(x.object_description).slice(0, 200);
      it.pcode = anStr(x.product_code);
      it.guess = !!x.class_guess;
      return it;
    }), same: prev ? prev.same : null, sameDef: typeof P.same_object_default === "boolean" ? P.same_object_default : null,
    confirmed: !!P.confirmed, edited: P.source === "employee", src: anStr(P.source) || "default", hint: false,
    key: ptProdClasses().join(",")};
}
/* правка частей: подтверждение снимается, итог и доли — на месте, поля не перерисовываются */
function ptEdited(){
  if (!CH.pt) return;
  CH.pt.edited = true;
  CH.pt.confirmed = false;
  CH.pt.hint = false;
  wzClearErr("parts");
  ptPaintLive();
  wzSave();
}
function ptPaintLive(){
  const S = Number(CH.must.sum_insured) || 0;
  const src = $("#ptSrc");
  if (src) src.textContent = ptSrcText();
  const hint = $("#ptHint");
  if (hint && !(CH.pt && CH.pt.hint)) hint.remove();
  ((CH.pt && CH.pt.items) || []).forEach((x, i) => {
    const e = $("#ptSh-" + i);
    if (e) e.textContent = S && Number(x.sum) > 0 ? pct(x.sum / S * 100, 2) : "—";
  });
  const tot = $("#ptTotal");
  if (tot) tot.innerHTML = ptTotalHtml();
  const b = $("#ptConfirm");
  if (b) { b.disabled = !ptOk(); b.textContent = ptConfirmLabel(); b.setAttribute("aria-pressed", String(!!(CH.pt && CH.pt.confirmed))); }
  wzBarPaint();
}
const ptConfirmLabel = () => CH.pt && CH.pt.confirmed ? T("tg.pt.confirmed", "Распределение подтверждено") : T("tg.pt.confirm", "Подтвердить распределение");
function ptTotalHtml(){
  if (!CH.pt || !CH.pt.items.length) return "";
  const S = Number(CH.must.sum_insured) || 0, tot = ptSum();
  if (!S) return '<p class="pt-tot">' + esc(T("tg.pt.need_sum", "Укажите страховую сумму договора — сверим с ней сумму частей.")) + "</p>";
  const d = tot - S, zero = CH.pt.items.some(x => !(Number(x.sum) > 0)), bad = zero || Math.abs(d) > PT_TOL;
  return '<p class="pt-tot' + (bad ? " bad" : " ok") + '" role="status"><b>' + esc(T("tg.pt.total", "Сумма частей {x} из {y}", {x: money(tot), y: money(S)})) + "</b>"
    + (bad ? "<span>" + esc(zero ? T("tg.pt.zero", "У каждой части должна быть сумма больше нуля — впишите её или удалите часть.")
      : T("tg.pt.diff", "Разница {d}. Суммы частей должны сложиться в страховую сумму договора — до этого акт не сформировать.", {d: sgnMoney(d)})) + "</span>" : "")
    + "</p>";
}
function ptAddHtml(){
  const R = CH.refs;
  const n = (CH.pt && CH.pt.items.length) || 0;
  if (!R || n >= PT_MAX) return "";
  const prod = ptProdClasses();
  return '<div class="pt-add"><label class="f" for="ptAddCls">' + esc(T("tg.pt.add_label", "Добавить часть другого класса")) + "</label>"
    + '<div class="pt-add-row"><select id="ptAddCls"><option value="">' + esc(T("tg.pt.add_pick", "Выберите класс")) + "</option>"
    + R.classes.map(c => '<option value="' + esc(c.code) + '">' + esc(c.code + " — " + wzClsName(c.code, c.name)
      + (prod.length && prod.indexOf(c.code) < 0 ? " · " + T("tg.pt.outside_short", "не из продукта") : "")) + "</option>").join("") + "</select>"
    + '<button type="button" class="btn btn-secondary btn-sm" data-go="ptadd">' + esc(T("tg.pt.add", "+ часть")) + "</button></div>"
    + '<span class="ferr" id="wzErr-ptadd"></span></div>';
}
function ptRowHtml(it, i){
  const S = Number(CH.must.sum_insured) || 0, n = i + 1, prod = ptProdClasses();
  tplNeed(it.cls);
  const x = TPL.cache[tplKey(it.cls)], t = x && x.template ? x : null;
  const outside = prod.length && prod.indexOf(it.cls) < 0 && !it.pcode;
  const tags = (outside ? '<span class="tag warn">' + esc(T("tg.pt.outside", "класс не из состава продукта")) + "</span>" : "")
    + (it.guess ? '<span class="tag warn">' + esc(T("tg.pt.guess", "класс не распознан — проверьте")) + "</span>" : "");
  let h = '<div class="pt-row" data-pti="' + i + '"><div class="pt-h"><p><b>' + esc(T("tg.pt.part_n", "Часть {n}", {n: n})) + "</b> "
    + esc(it.cls + " — " + wzClsName(it.cls)) + "</p>"
    + '<button type="button" class="btn-link pt-del" data-ptdel="' + i + '" aria-label="' + esc(T("tg.pt.del_aria", "Удалить часть {n}", {n: n})) + '">'
    + esc(T("tg.pt.del", "удалить")) + "</button></div>"
    + (tags ? '<p class="pt-tags">' + tags + "</p>" : "")
    + (it.desc ? '<p class="pt-desc">' + esc(it.desc) + "</p>" : "")
    + '<div class="fgrid pt-g">'
    + '<div><label class="f" for="pts-' + i + '">' + esc(T("tg.pt.sum", "Страховая сумма части")) + ' <span class="unit">' + esc(SUM()) + "</span></label>"
    + '<input id="pts-' + i + '" type="text" inputmode="numeric" autocomplete="off" enterkeyhint="done" data-pts="' + i + '" placeholder="0" value="'
    + esc(Number(it.sum) > 0 ? Number(it.sum).toLocaleString(LOC()) : "") + '"></div>'
    + '<div><label class="f">' + esc(T("tg.pt.share", "Доля в договоре")) + '</label><p class="pt-sh" id="ptSh-' + i + '">'
    + esc(S && Number(it.sum) > 0 ? pct(it.sum / S * 100, 2) : "—") + "</p></div>"
    + (ptValOk(it.cls)
      ? '<div><label class="f" for="ptv-' + i + '">' + esc(T("tg.pt.value", "Стоимость объекта части")) + ' <span class="unit">' + esc(SUM()) + "</span></label>"
        + '<input id="ptv-' + i + '" type="text" inputmode="numeric" autocomplete="off" enterkeyhint="done" data-ptv="' + i + '" placeholder="'
        + esc(T("tg.pt.value_ph", "по доле договора")) + '" value="' + esc(Number(it.value) > 0 ? Number(it.value).toLocaleString(LOC()) : "") + '"></div>'
      : '<div><label class="f">' + esc(T("tg.pt.value", "Стоимость объекта части")) + '</label><p class="pt-na">'
        + esc(T("tg.pt.value_na", "не применяется: у класса нет страховой стоимости")) + "</p></div>")
    + '<div><label class="f" for="ptfr-' + i + '">' + esc(T("tg.pt.fr", "Своя франшиза части")) + ' <span class="unit">%</span></label>'
    + '<input id="ptfr-' + i + '" type="text" inputmode="decimal" maxlength="6" autocomplete="off" enterkeyhint="done" data-ptfr="' + i + '" placeholder="'
    + esc(T("tg.pt.fr_ph", "без франшизы")) + '" value="' + esc(it.fr || "") + '"></div>';
  if (!x) h += '<div class="full"><p class="note">' + spin(T("tg.tpl.loading", "загружаю поля класса…")) + "</p></div>";
  else if (!t) h += '<div class="full"><p class="note err">' + esc(T("tg.tpl.failed_short", "Поля класса не загрузились — часть посчитается без них.")) + "</p></div>";
  // марка и модель (recognized.*) — у части 1: это объект договора, у остальных частей своего распознавания нет
  else h += tfHtml(tfKindField(it.cls), String(i), it.cls) + tfList(t.template.must, i > 0).map(f => tfHtml(f, String(i), it.cls)).join("");
  h += "</div>";
  if (t) {
    const more = tfList(t.template.optional, i > 0);
    if (more.length) {
      h += '<details class="pt-more"' + (CH.ptMore && CH.ptMore[i] ? " open" : "") + ' data-ptmore="' + i + '"><summary>' + esc(T("tg.pt.more", "Ещё поля класса · {n}", {n: more.length}))
        + '</summary><div class="fgrid pt-g">' + tfFactorSplit(more, f => tfHtml(f, String(i), it.cls)) + "</div></details>";
    }
  }
  return h + "</div>";
}
function ptCardHtml(){
  if (!wzClass() || obOn()) return "";
  if (!ptOn()) {
    return '<div class="qadd pt-open"><button type="button" data-go="ptopen">' + esc(T("tg.pt.open", "+ часть другого класса")) + "</button></div>";
  }
  const pt = CH.pt, prod = ptProdClasses(), err = CH.errs.parts;
  let h = '<section class="card pt-card' + (err ? " bad" : "") + '" id="ptCard"><h2>' + esc(T("tg.pt.title", "Части договора")) + "</h2>"
    + '<p class="sub" id="ptSrc">' + esc(ptSrcText()) + "</p>";
  if (pt && pt.hint) {
    h += '<p class="pt-hint" id="ptHint">' + esc(T("tg.pt.hint", "Первый расчёт сделан по предложенному распределению. Проверьте суммы частей, подтвердите распределение и сформируйте акт снова. Акт по предложению открыт на шаге «Акт».")) + "</p>";
  }
  if (!pt || !pt.items.length) {
    h += '<p class="note">' + esc(prod.length > 1
        ? T("tg.pt.empty", "Классы продукта: {list}. Как разделить страховую сумму между ними, предложит первый расчёт — нажмите «Сформировать акт», затем проверьте и подтвердите части. Или задайте суммы сами.", {list: prod.map(c => c + " — " + wzClsName(c)).join("; ")})
        : T("tg.pt.empty_one", "Выберите класс второй части — первой частью станет класс продукта с суммой договора.")) + "</p>"
      + (prod.length > 1 ? '<div class="qadd"><button type="button" data-go="ptinit">' + esc(T("tg.pt.init", "Задать суммы частей")) + "</button></div>" : "")
      + ptAddHtml() + '<span class="ferr" id="wzErr-parts">' + esc(err || "") + "</span></section>";
    return h;
  }
  const same = pt.same != null ? pt.same : !!pt.sameDef;
  h += '<label class="f">' + esc(T("tg.pt.objects", "Части относятся к")) + "</label>"
    + '<div class="segsel" role="group" aria-label="' + esc(T("tg.pt.objects", "Части относятся к")) + '">'
    + '<button type="button" data-ptsame="one" aria-pressed="' + same + '">' + esc(T("tg.pt.one", "Один объект")) + "</button>"
    + '<button type="button" data-ptsame="diff" aria-pressed="' + !same + '">' + esc(T("tg.pt.diff_obj", "Разные объекты")) + "</button></div>"
    + '<span class="hint">' + esc(same ? T("tg.pt.one_hint", "Один объект: сценарии убытка договора — по самой крупной части.")
      : T("tg.pt.diff_hint", "Разные объекты: сценарии убытка частей складываются.")) + "</span>"
    + pt.items.map(ptRowHtml).join("")
    + '<div id="ptTotal">' + ptTotalHtml() + "</div>"
    + ptAddHtml()
    + '<div class="actions"><button type="button" class="btn btn-secondary" id="ptConfirm" data-go="ptconfirm" aria-pressed="' + !!pt.confirmed + '"' + (ptOk() ? "" : " disabled") + ">"
    + esc(ptConfirmLabel()) + "</button></div>"
    + '<span class="ferr" id="wzErr-parts">' + esc(err || "") + "</span></section>";
  return h;
}
function ptInit(){
  const prod = ptProdClasses();
  CH.pt = {items: prod.slice(0, PT_MAX).map(c => ptItem(c, 0)), same: null, sameDef: null, confirmed: false, edited: true,
    src: "employee", hint: false, key: prod.join(",")};
  wzSave();
  wzPart("parts");
  wzBarPaint();
}
function ptAdd(){
  const sel = $("#ptAddCls"), cls = sel ? sel.value : "";
  if (!cls) {
    const e = $("#wzErr-ptadd");
    if (e) e.textContent = T("tg.pt.add_err", "Выберите класс части в списке.");
    return;
  }
  const S = Number(CH.must.sum_insured) || 0, prod = ptProdClasses();
  if (!CH.pt || !CH.pt.items.length) {
    CH.pt = {items: [], same: null, sameDef: null, confirmed: false, edited: true, src: "employee", hint: false, key: prod.join(",")};
    // у продукта один класс: первая часть — класс продукта с суммой договора, её сотрудник уменьшит
    if (prod.length <= 1) CH.pt.items.push(ptItem(wzClass(), S));
    else prod.forEach(c => CH.pt.items.push(ptItem(c, 0)));
  }
  if (CH.pt.items.length < PT_MAX) CH.pt.items.push(ptItem(cls, 0));
  CH.ptAdd = true;
  haptic("select");
  ptEdited();
  wzPart("parts");
  const last = $("#pts-" + (CH.pt.items.length - 1));
  if (last) try { last.focus({preventScroll: true}); } catch (e) {}
}
function ptDel(i){
  if (!CH.pt || !CH.pt.items[i]) return;
  CH.pt.items.splice(i, 1);
  CH.ptMore = {};
  // у продукта с одним классом одна часть — это обычный акт
  if (!CH.pt.items.length || (ptProdClasses().length <= 1 && CH.pt.items.length < 2)) { CH.pt = null; CH.ptAdd = false; }
  haptic("select");
  if (CH.pt) ptEdited(); else wzSave();
  wzPart("parts");
  wzPart("tpl");
  wzPart("more");
  wzBarPaint();
}
function ptMoneyInput(el, key){
  const it = CH.pt && CH.pt.items[Number(el.dataset[key === "sum" ? "pts" : "ptv"])];
  if (!it) return;
  groupDigitsLive(el);
  const v = num(el.value);
  it[key] = v > 0 ? v : null;
  ptEdited();
}
function ptFrInput(el){
  const it = CH.pt && CH.pt.items[Number(el.dataset.ptfr)];
  if (!it) return;
  it.fr = decimalLive(el);
  ptEdited();
}
function ptSame(v){
  if (!CH.pt) return;
  CH.pt.same = v === "one";
  haptic("select");
  ptEdited();
  wzPart("parts");
}
function ptConfirm(){
  if (!CH.pt || !ptOk()) return;
  if (ptFrBad()) { CH.errs.parts = errText("parts"); wzPart("parts"); return; }
  CH.pt.confirmed = true;
  CH.pt.edited = true;
  CH.pt.hint = false;
  wzClearErr("parts");
  haptic("success");
  wzSave();
  wzPart("parts");
  wzBarPaint();
}
const ptFrBad = () => ((CH.pt && CH.pt.items) || []).some(x => String(x.fr || "").trim() !== "" && !(dec(x.fr) > 0 && dec(x.fr) <= 50));
/* в POST /act/make: части сотрудника (правка или подтверждение); предложение без правок сервер соберёт сам */
function ptBody(){
  const pt = CH.pt;
  if (!pt || !pt.items.length || !(pt.edited || pt.confirmed)) return null;
  return pt.items.map(it => {
    const p = {class_code: it.cls, sum_insured: Number(it.sum) || 0};
    if (it.pcode) p.product_code = it.pcode;
    if (ptValOk(it.cls) && Number(it.value) > 0) p.object_value = Number(it.value);
    if (it.kind) p.object_kind = it.kind;
    if (it.desc) p.object_description = it.desc;
    const f = tfBodyFields(it.f), cf = tfBodyCf(it.cls, it.cf);
    if (cf) f.class_fields = cf;
    if (Object.keys(f).length) p.fields = f;
    const fr = dec(it.fr);
    if (fr > 0) p.deductible = {pct: fr, type: "unconditional"};
    return p;
  });
}
function ptLoad(s){
  const o = v => v && typeof v === "object" && !Array.isArray(v) ? v : {};
  if (!s || !Array.isArray(s.items)) return null;
  const items = s.items.filter(x => x && /^[0-9]{1,2}[a-zа-я]?$/i.test(String(x.cls || ""))).slice(0, PT_MAX).map(x => Object.assign(ptItem(x.cls, Number(x.sum) || 0), {
    value: Number(x.value) > 0 ? Number(x.value) : null, kind: /^[a-z_]{1,40}$/.test(String(x.kind || "")) ? x.kind : "",
    f: o(x.f), cf: o(x.cf), fr: typeof x.fr === "string" ? x.fr.slice(0, 6) : "", desc: typeof x.desc === "string" ? x.desc.slice(0, 200) : "",
    pcode: typeof x.pcode === "string" ? x.pcode.slice(0, 10) : "", guess: !!x.guess}));
  if (!items.length) return null;
  return {items: items, same: typeof s.same === "boolean" ? s.same : null, sameDef: typeof s.sameDef === "boolean" ? s.sameDef : null,
    confirmed: !!s.confirmed, edited: !!s.edited, src: /^[a-z_]{1,20}$/.test(String(s.src || "")) ? s.src : "default", hint: !!s.hint,
    key: typeof s.key === "string" ? s.key : ""};
}

/* ---------- шаг 3: договор по частям ---------- */
function ptAct(a){ const P = a && a.parts; return P && P.mode === "multi" && anArr(P.items).length ? P : null; }
/* «600 000 soʻm»: единица — отдельным неразрывным словом, переносится целиком, а не посреди «soʻm» */
function premUnitHtml(s){
  const m = /^(.*\d)[\s  ]+(\D+)$/.exec(String(s || ""));
  return m ? esc(m[1]) + ' <span class="u">' + esc(m[2]) + "</span>" : esc(s);
}
/* полоска уровня риска из трёх делений (low / moderate / high) */
function lvBarHtml(lv){
  const lvI = ["low", "moderate", "high"].indexOf(lv);
  return '<i class="lvbar lv-' + esc(lv || "none") + '" aria-hidden="true">' + [0, 1, 2].map(k => "<u" + (k <= lvI ? ' class="on"' : "") + "></u>").join("") + "</i>";
}
function ptSumHtml(a, P){
  const tt = anObj(P.totals), sc = anObj(tt.scenarios), ret = anObj(tt.retention);
  const lv = tt.level, val = a.value || {};
  const cv = anObj(tt.value);
  const ratio = isNum(cv.ratio_pct) ? pct(cv.ratio_pct, dp(cv.ratio_pct) ? 2 : 0) : T("tg.pt.value_na_short", "не применяется");
  const fv = cv.verdict;
  let h = '<div class="act-kpis">'
    + '<div class="kprem"><span>' + esc(T("tg.act.premium", "Премия")) + "</span><b>" + premUnitHtml(anStr(tt.premium_text) || actPremium(a)) + "</b>"
    + "<em>" + esc(T("tg.pt.prem_sum", "сумма премий {n} частей", {n: P.items.length})) + "</em></div>"
    + '<div><span>' + esc(T("tg.act.level", "Уровень риска")) + "</span>"
    + lvBarHtml(lv)
    + '<b class="lvname">' + esc(anStr(tt.level_label) || levelName(lv)) + "</b>"
    + "<em>" + esc(T("tg.pt.worst", "по самой опасной части {n}", {n: tt.worst_index})) + "</em></div>"
    + "<div><span>" + esc(T("tg.act.ratio", "Сумма к стоимости")) + "</span><b>" + esc(ratio) + "</b>"
    + (isNum(cv.ratio_pct) && verdictName(fv) ? '<em class="' + (fv === "normal" ? "ok" : "warn") + '">' + esc(verdictName(fv)
      + (anArr(cv.parts).length ? " · " + T("tg.pt.value_parts", "части {list}", {list: cv.parts.join(", ")}) : "")) + "</em>" : "") + "</div>"
    + "<div><span>" + esc(T("tg.pt.parts_n", "Частей в договоре")) + "</span><b>" + esc(String(P.items.length)) + "</b>"
    + "<em>" + esc(anStr(P.object_mode_label)) + "</em></div>"
    + "</div>";
  if (isNum(tt.reference_rate_pct)) {
    h += '<p class="pt-ref">' + esc(T("tg.pt.ref_rate", "Ставка договора {r} — справочно, минимум проверен по каждому классу.", {r: pct(tt.reference_rate_pct, dp(tt.reference_rate_pct))})) + "</p>";
  }
  if (sc.available) {
    h += '<div class="sc-tiles pt-sc">' + [["pml", "PML"], ["eml", "EML"], ["mfl", "MFL"]].map(k => '<div class="sc-t"><p><b>' + k[1] + "</b>" + esc(scName(k[0])) + "</p>"
      + "<strong>" + esc(money(sc[k[0]])) + "</strong></div>").join("") + "</div>"
      + (anStr(sc.rule_text) ? '<p class="pt-ref">' + esc(sc.rule_text) + "</p>" : "");
  }
  if (ret.known && isNum(ret.limit)) {
    const over = Number(ret.eml_excess) > 0;
    h += '<div class="sc-ret"><span>' + esc(T("tg.act.ret_title", "Лимит собственного удержания")) + "</span><b>" + esc(money(ret.limit)) + "</b>"
      + '<small class="' + (over || ret.status === "temporary" ? "warn" : "") + '">' + esc([over
        ? T("tg.pt.ret_over", "EML договора выше удержания на {x}", {x: money(ret.eml_excess)}) : T("tg.pt.ret_ok", "EML договора в пределах удержания"),
      ret.status === "temporary" ? T("tg.act.ret_temp", "по временным данным о собственных средствах — не отчётность") : ""].filter(Boolean).join("; ")) + "</small></div>";
  }
  const fr = a.franchise || {};
  if (anStr(fr.text)) h += '<p class="act-line"><b>' + esc(T("tg.act.franchise", "Франшиза")) + ":</b> " + esc(fr.text) + "</p>";
  if (!P.confirmed) {
    h += '<div class="pt-warn" role="status"><p><b>' + esc(T("tg.pt.unconfirmed", "Распределение не подтверждено")) + "</b></p>"
      + "<p>" + esc(T("tg.pt.unconfirmed_text", "Акт посчитан так: {s}. Проверьте суммы частей и подтвердите их на шаге «Проверить» — затем сформируйте акт снова.", {s: anStr(P.source_label) || ptSrcName(P.source)})) + "</p>"
      + '<button type="button" class="btn btn-secondary btn-sm" data-go="toparts">' + esc(T("tg.pt.to_parts", "Вернуться к частям")) + "</button></div>";
  }
  return h;
}
function ptTableHtml(P){
  const tb = anObj(P.table), cols = anArr(tb.columns).map(anStr), rows = anArr(tb.rows);
  if (!cols.length || !rows.length) return "";
  return '<table class="an-t pt-t fold"><thead><tr>' + cols.map((c, i) => "<th" + (i >= 2 && i !== 3 && i !== 6 ? ' class="n"' : "") + ">" + esc(c) + "</th>").join("") + "</tr></thead><tbody>"
    + rows.map((r, ri) => '<tr' + (ri === rows.length - 1 && !anStr(anArr(r)[0]) ? ' class="tot"' : "") + ">" + anArr(r).map((v, i) => '<td data-l="' + esc(cols[i] || "") + '"'
      + (i === 1 ? ' class="h"' : i >= 2 && i !== 3 && i !== 6 ? ' class="n"' : "") + ">" + esc(anStr(v) || (i === 0 ? "" : "—")) + "</td>").join("") + "</tr>").join("")
    + "</tbody></table>";
}
function ptPartHtml(p){
  const n = p.index, open = CH.ptOpen && CH.ptOpen[n] != null ? !!CH.ptOpen[n] : false;
  const r = anObj(p.rate), fr = anObj(p.franchise), v = anObj(p.value);
  const tags = (p.class_outside ? '<span class="tag warn">' + esc(T("tg.pt.outside", "класс не из состава продукта")) + "</span>" : "")
    + (p.class_guess ? '<span class="tag warn">' + esc(T("tg.pt.guess", "класс не распознан — проверьте")) + "</span>" : "");
  const fx = anArr(anObj(p.risk).factors).filter(x => x && anStr(x.text));
  const how = anArr(r.how).map(anStr).filter(Boolean);
  const grounds = anArr(fr.grounds).filter(g => g && anStr(g.text));
  const miss = anArr(p.missing).filter(x => x && anStr(x.label));
  const ratio = v.applicable && isNum(v.ratio_pct) ? pct(v.ratio_pct, dp(v.ratio_pct) ? 2 : 0) : "";
  const head = [anStr(p.premium_text), anStr(p.level_label)].filter(Boolean).join(" · ");
  return '<details class="an-c pt-c" data-ptn="' + esc(n) + '"' + (open ? " open" : "") + '><summary><span class="an-ct">' + esc(anStr(p.label) || T("tg.pt.part_n", "Часть {n}", {n: n}))
    + '</span><em class="an-ch">' + esc(head) + '</em></summary><div class="an-in">'
    + (tags ? '<p class="pt-tags">' + tags + "</p>" : "")
    + (anStr(p.object_description) ? '<p class="an-note">' + esc(p.object_description) + "</p>" : "")
    + '<h4 class="an-h4">' + esc(T("tg.pt.h_level", "Уровень риска: {l}", {l: anStr(p.level_label) || levelName(p.level)})) + "</h4>"
    + (fx.length ? '<ul class="pt-fx">' + fx.map(x => '<li class="' + (x.sign === "up" ? "up" : x.sign === "down" ? "down" : "") + '">' + esc(x.text) + "</li>").join("") + "</ul>"
      : anNa(T("tg.pt.no_factors", "Признаков, которые меняют уровень, нет — уровень по умолчанию.")))
    + '<h4 class="an-h4">' + esc(T("tg.pt.h_rate", "Как посчитан тариф")) + "</h4>"
    + (how.length ? '<ul class="pt-fx">' + how.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul>" : anNa())
    + rfPartHtml(p)
    + '<h4 class="an-h4">' + esc(T("tg.act.franchise", "Франшиза")) + "</h4>"
    + "<p>" + esc(anStr(fr.text) || T("tg.act.fr_none", "не требуется")) + "</p>"
    + (grounds.length ? '<ul class="pt-fx">' + grounds.map(g => "<li>" + esc(g.text) + "</li>").join("") + "</ul>" : "")
    + '<h4 class="an-h4">' + esc(T("tg.act.sc_title", "Сценарии убытка")) + "</h4>"
    + scenInner(anObj(p.scenarios))
    + '<h4 class="an-h4">' + esc(T("tg.act.ratio", "Сумма к стоимости")) + "</h4>"
    + "<p>" + (ratio ? "<b>" + esc(ratio) + "</b> " : "") + esc(anStr(v.text) || T("tg.pt.value_na_short", "не применяется")) + "</p>"
    + (miss.length ? '<h4 class="an-h4">' + esc(T("tg.pt.h_missing", "Не хватает данных")) + '</h4><p class="warn">' + esc(miss.map(x => x.label).join(", ")) + "</p>" : "")
    + '<h4 class="an-h4">' + esc(T("tg.an.title", "Аналитика риска")) + "</h4>"
    + (anObj(p.analytics).available ? anCardsHtml(p.analytics, p, "p" + n + ":") : anNa(anStr(anObj(p.analytics).text)))
    + "</div></details>";
}
function ptActHtml(a){
  const P = ptAct(a);
  if (!P) return "";
  const notes = anArr(P.notes).map(anStr).filter(Boolean);
  return '<section class="card act-card pt-act" aria-label="' + esc(T("tg.pt.title", "Части договора")) + '"><h2>' + esc(T("tg.pt.title", "Части договора")) + "</h2>"
    + (anStr(P.summary) ? '<p class="sub">' + esc(P.summary) + "</p>" : "")
    + ptTableHtml(P)
    + (notes.length ? '<ul class="pt-notes">' + notes.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul>" : "")
    + P.items.map(ptPartHtml).join("")
    + "</section>";
}
/* «Каких документов не хватает»: документы шаблона класса (template.documents) */
function actTplDocsHtml(a){
  const tp = anObj(a.template), docs = anArr(anObj(tp.documents).items).map(anStr).filter(Boolean);
  if (!docs.length) return "";
  return '<div class="card act-card"><h2>' + esc(T("tg.tpl.docs_miss", "Каких документов не хватает")) + "</h2>"
    + '<p class="sub">' + esc(T("tg.tpl.docs_sub", "Документы по шаблону класса «{name}». Запросите у клиента те, которых ещё нет.", {name: anStr(tp.name)})) + "</p>"
    + '<ul class="tpl-docs">' + docs.map(d => "<li>" + esc(d) + "</li>").join("") + "</ul></div>";
}

/* =====================================================================================
   Запрос филиала и договор страхования (30.09.2026). POST /act/photos отдаёт branch_request, contract
   и cross_check (app/act.py: branch_view, contract_view, cross_view). Шаг «Проверить»: карточки с прочитанным;
   тариф, премию, франшизу и срок можно исправить — в POST /act/make они уходят как optional.request /
   optional.contract как есть; источник каждого поля и правки «было → стало» сервер определяет сам, сравнивая со своей
   загрузкой (app/act.py, _trust_doc). Сверку с расчётом акта делает сервер
   (app/act_engine.request_check, contract_check); шаг «Акт» показывает request_check, contract_check, cross_check.
   Подписи — из словаря по кодам (при смене языка перерисовываются); тексты из документов — недоверенные:
   только через esc() и не переводятся. В sessionStorage — прочитанные поля с правками до закрытия вкладки,
   без имён файлов.
   ===================================================================================== */
const DQ_PARTIES = ["policyholder", "beneficiary", "pledger"];
const DQ_CUR = ["UZS", "USD", "EUR", "RUB"];
const X_CODES = ["product_code", "sum_insured", "object_value", "tariff_pct", "premium", "term", "franchise", "object"];
const sStr = (v, n) => v == null || v === "" || typeof v === "object" ? null : String(v).slice(0, n || 200);
const sNum = v => { const x = Number(v); return v != null && v !== "" && typeof v !== "object" && isFinite(x) ? x : null; };
const sIso = v => /^\d{4}-\d{2}-\d{2}$/.test(String(v || "")) ? String(v) : null;
const sArr = v => Array.isArray(v) ? v : [];
const sObj = v => v && typeof v === "object" && !Array.isArray(v) ? v : {};
const sCode = v => /^[a-z_]{1,40}$/.test(String(v || "")) ? String(v) : null;
function dqParty(p){
  p = sObj(p);
  if (p.kind === "individual") return {kind: "individual", name: null};
  return p.kind === "legal" && sStr(p.name) ? {kind: "legal", name: sStr(p.name, 160)} : null;
}
function dqFr(f){
  f = f && typeof f === "object" ? f : null;
  if (!f || typeof f.applied !== "boolean") return null;
  const o = {applied: f.applied, text: sStr(f.text, 120), pct: f.applied ? sNum(f.pct) : null, amount: f.applied ? sNum(f.amount) : null};
  if (f.type === "unconditional" || f.type === "conditional") o.type = f.type;
  return o;
}
function dqAreas(a){ a = sObj(a); return {land_m2: sNum(a.land_m2), useful_m2: sNum(a.useful_m2), total_m2: sNum(a.total_m2)}; }
/* готовый optional.request / optional.contract из ответа сервера (или из sessionStorage) — только известные поля */
function dqReq(r, isCt){
  r = sObj(r);
  const days = Number(r.term_days);
  const o = {tariff_pct: sNum(r.tariff_pct), premium: sNum(r.premium), franchise: dqFr(r.franchise),
    term_from: sIso(r.term_from), term_to: sIso(r.term_to), term_days: Number.isInteger(days) && days >= 1 && days <= 3660 ? days : null,
    sum_insured: sNum(r.sum_insured), product_code: /^\d{3,4}$/.test(String(r.product_code || "")) ? String(r.product_code) : null,
    // объект и стоимость — как прочитал сервер (для сверки «запрос ↔ договор» в акте), без обрезки
    object_value: sNum(r.object_value), object_description: sStr(r.object_description, 600), cadastre_no: sStr(r.cadastre_no, 40),
    object_kind: sCode(r.object_kind), class_hint: sCode(r.class_hint)};
  if (!isCt) return o;
  const list = v => sArr(v).map(x => sStr(x, 80)).filter(Boolean).slice(0, 40);
  return Object.assign(o, {contract_no: sStr(r.contract_no, 40), contract_date: sIso(r.contract_date),
    currency: DQ_CUR.indexOf(r.currency) >= 0 ? r.currency : null,
    covered_risks: list(r.covered_risks), exclusions: list(r.exclusions),
    payment_mode: r.payment_mode === "single" || r.payment_mode === "installments" ? r.payment_mode : null,
    payments: sArr(r.payments).filter(p => p && sIso(p.date) && sNum(p.amount) > 0).slice(0, 60)
      .map(p => ({date: p.date, amount: Number(p.amount)})),
    items: sArr(r.items).filter(x => x && sNum(x.sum) > 0).slice(0, 60).map(x => ({name: sStr(x.name, 200), sum: Number(x.sum)}))});
}
/* branch_request сервера (или сохранённый) → состояние карточки «Запрос филиала» */
function dqBrFrom(b){
  b = sObj(b);
  if (!b.detected) return null;
  const f = sObj(b.fields);
  const pf = {product_code: /^\d{3,4}$/.test(String(f.product_code || "")) ? String(f.product_code) : null,
    object_description: sStr(f.object_description, 300), object_description_translated: sStr(f.object_description_translated, 300),
    areas: dqAreas(f.areas), cadastre_no: sStr(f.cadastre_no, 40), object_value: sNum(f.object_value), sum_insured: sNum(f.sum_insured),
    contract_terms: sStr(f.contract_terms, 300), term_inclusive: f.term_inclusive !== false};
  DQ_PARTIES.forEach(k => { pf[k] = dqParty(f[k]); });
  const req = dqReq(b.req || b.request, false);
  return {detected: true, source: b.source === "photo" ? "photo" : "document",
    rows_found: Math.max(0, Number(b.rows_found) || 0), rows_total: Math.max(1, Number(b.rows_total) || 16),
    rows: sArr(b.rows).filter(r => r && sCode(r.code)).slice(0, 20).map(r => ({n: Number(r.n) || 0, code: String(r.code), found: !!r.found})),
    fields: pf, req: req, orig: b.orig ? dqReq(b.orig, false) : dqReq(b.request, false)};
}
/* contract сервера (или сохранённый) → состояние карточки «Договор страхования» */
function dqCtFrom(c){
  c = sObj(c);
  if (!c.detected) return null;
  const f = sObj(c.fields);
  const pf = {contract_no: sStr(f.contract_no, 40), contract_date: sIso(f.contract_date), place: sStr(f.place, 120),
    product_name: sStr(f.product_name, 160), insurer: dqParty(f.insurer),
    object_description: sStr(f.object_description, 300), address: sStr(f.address, 200), region: sStr(f.region, 80),
    cadastre_no: sStr(f.cadastre_no, 40), areas: dqAreas(f.areas), construction: sStr(f.construction, 120),
    year_built: sStr(f.year_built, 12), brand: sStr(f.brand, 60), model: sStr(f.model, 60), vin: sStr(f.vin || f.serial_no, 40),
    reg_no: sStr(f.reg_no, 20), object_value: sNum(f.object_value), sum_insured: sNum(f.sum_insured),
    currency: DQ_CUR.indexOf(f.currency) >= 0 ? f.currency : null,
    items: sArr(f.items).filter(x => x && sNum(x.sum) > 0).slice(0, 30).map(x => ({name: sStr(x.name, 200), sum: Number(x.sum)})),
    payment_mode: f.payment_mode === "single" || f.payment_mode === "installments" ? f.payment_mode : null,
    payments: sArr(f.payments).filter(p => p && sNum(p.amount) > 0).slice(0, 60).map(p => ({date: sIso(p.date), amount: Number(p.amount)})),
    territory: sStr(f.territory, 200), notice: sStr(f.notice, 160),
    special_terms: sArr(f.special_terms).map(x => sStr(x, 400)).filter(Boolean).slice(0, 20), term_inclusive: f.term_inclusive !== false};
  DQ_PARTIES.forEach(k => { pf[k] = dqParty(f[k]); });
  const risks = v => sArr(v).filter(x => x && sCode(x.code)).slice(0, 40).map(x => ({code: String(x.code), label: sStr(x.label, 80)}));
  const req = dqReq(c.req || c.request, true);
  return {detected: true, source: ["document", "document_ai", "photo"].indexOf(c.source) >= 0 ? c.source : "document",
    truncated: !!c.truncated, pages: Number.isInteger(c.pages) && c.pages > 0 ? c.pages : null, fields: pf,
    ai_fields: sArr(c.ai_fields).map(sCode).filter(Boolean),
    found_n: Number.isInteger(c.found_n) ? c.found_n : sArr(c.found).length,
    missing: sArr(c.missing).map(x => sCode(typeof x === "string" ? x : x && x.code)).filter(Boolean),
    essentials: sArr(c.essentials).filter(e => e && sCode(e.code)).map(e => ({code: String(e.code), present: !!e.present})),
    covered_risks: risks(c.covered_risks), exclusions: risks(c.exclusions),
    // бланк договора: какие поля не заполнены (подчёркивания) и строка «Это бланк договора…» из notes сервера
    blank: sArr(c.blank).map(x => sCode(typeof x === "string" ? x : x && x.code)).filter(Boolean),
    tpl: !!(c.is_template || c.tpl), tpl_note: sStr(c.tpl_note || (c.is_template ? sArr(c.notes)[0] : null), 400),
    req: req, orig: c.orig ? dqReq(c.orig, true) : dqReq(c.request, true)};
}
/* cross_check сервера → строки сверки двух документов (значения проверяются по коду строки) */
function dqXcFrom(x){
  x = sObj(x);
  if (!x.available) return null;
  const val = (code, v) => {
    if (v == null) return null;
    if (code === "term") { const o = sObj(v); return {from: sIso(o.from), to: sIso(o.to), days: sNum(o.days)}; }
    if (code === "franchise") return dqFr(v);
    if (code === "object") { const o = sObj(v); return {cadastre_no: sStr(o.cadastre_no, 40), class_hint: sCode(o.class_hint)}; }
    if (code === "product_code") return sStr(v, 8);
    return sNum(v);
  };
  const items = sArr(x.items).filter(i => i && X_CODES.indexOf(i.code) >= 0).map(i => ({code: i.code,
    request: val(i.code, i.request), contract: val(i.code, i.contract),
    verdict: ["same", "differs", "missing"].indexOf(i.verdict) >= 0 ? i.verdict : "missing"}));
  return items.length ? {items: items} : null;
}
/* что-то из условий сотрудник исправил — документ уходит в акт как «введено сотрудником» */
const dqPick = r => JSON.stringify(["tariff_pct", "premium", "franchise", "term_from", "term_to", "term_days"].map(k => r[k]));
function dqEdited(k){ const d = CH[k]; return !!d && dqPick(d.req) !== dqPick(d.orig); }
/* дни между датами (оба крайних дня — как сервер: request_check.term_inclusive) */
function dqDays(a, b, incl){
  const x = sIso(a) && new Date(a + "T00:00:00Z"), y = sIso(b) && new Date(b + "T00:00:00Z");
  if (!x || !y || isNaN(x) || isNaN(y) || y < x) return null;
  return Math.round((y - x) / 86400000) + (incl ? 1 : 0);
}
function dqMoney(v, cur){
  if (!isNum(v)) return "";
  return cur && cur !== "UZS" ? nf(v, Number(v) % 1 ? 2 : 0) + " " + cur : money(v);
}
const dqPct = v => isNum(v) ? pct(v, dp(v)) : "";
function dqPartyHtml(p){
  if (!p) return "";
  return p.kind === "individual" ? '<span class="muted">' + esc(T("tg.dq.individual", "физическое лицо — данные не извлекаются")) + "</span>" : esc(p.name);
}
function dqAreasText(a){
  a = a || {};
  const m2 = v => nf(v, Number(v) % 1 ? 1 : 0) + " " + T("tg.dq.m2", "м²");
  return [isNum(a.land_m2) ? T("tg.dq.area_land", "земля {v}", {v: m2(a.land_m2)}) : "",
    isNum(a.useful_m2) ? T("tg.dq.area_useful", "полезная {v}", {v: m2(a.useful_m2)}) : "",
    isNum(a.total_m2) ? T("tg.dq.area_total", "общая {v}", {v: m2(a.total_m2)}) : ""].filter(Boolean).join(", ");
}
/* строка «название — значение»; html — уже экранирован */
const dqKv = (label, html) => html ? "<div><span>" + esc(label) + "</span><b>" + html + "</b></div>" : "";
const dqAi = on => on ? '<em class="dq-ai">' + esc(T("tg.dq.ai_mark", "прочитано моделью, проверьте")) + "</em>" : "";
function dqTermText(r, incl){
  const days = r.term_from && r.term_to ? dqDays(r.term_from, r.term_to, incl) : r.term_days;
  if (!days) return "";
  const dates = r.term_from && r.term_to ? T("tg.dq.term_dates", "с {a} по {b}", {a: dateOnly(r.term_from), b: dateOnly(r.term_to)}) + " · " : "";
  return dates + T("tg.act.days_n", "{n} дн.", {n: days}) + (days > 366 ? " · " + T("tg.dq.multi_year", "многолетний: премия на весь срок, тариф годовой") : "");
}

/* подписи по кодам сервера (строки бланка, поля и условия договора, риски, исключения, порядок оплаты) —
   те же слова, что app/act_texts.py; код не из словаря — пустая строка, служебный код на экран не выходит */
function dqLbl(key){
  switch (key) {
    case "tg.br.row.product_code": return T("tg.br.row.product_code", "Вид страхования (код по приказу)");
    case "tg.br.row.policyholder": return T("tg.br.row.policyholder", "Страхователь");
    case "tg.br.row.beneficiary": return T("tg.br.row.beneficiary", "Выгодоприобретатель");
    case "tg.br.row.pledger": return T("tg.br.row.pledger", "Залогодатель");
    case "tg.br.row.object": return T("tg.br.row.object", "Объект страхования");
    case "tg.br.row.object_value": return T("tg.br.row.object_value", "Страховая стоимость");
    case "tg.br.row.sum_insured": return T("tg.br.row.sum_insured", "Страховая сумма");
    case "tg.br.row.franchise": return T("tg.br.row.franchise", "Франшиза");
    case "tg.br.row.tariff": return T("tg.br.row.tariff", "Страховой тариф");
    case "tg.br.row.premium": return T("tg.br.row.premium", "Страховая премия");
    case "tg.br.row.term": return T("tg.br.row.term", "Срок страхования");
    case "tg.br.row.contract_terms": return T("tg.br.row.contract_terms", "Изменение/дополнение стандартных условий");
    case "tg.br.row.counterparty": return T("tg.br.row.counterparty", "Контрагент");
    case "tg.br.row.contracts_count": return T("tg.br.row.contracts_count", "Число договоров");
    case "tg.br.row.osgor_class": return T("tg.br.row.osgor_class", "Класс (по ОСГОР)");
    case "tg.br.row.additional_info": return T("tg.br.row.additional_info", "Дополнительные сведения");
    case "tg.ct.miss.contract_no": return T("tg.ct.miss.contract_no", "Номер договора");
    case "tg.ct.miss.contract_date": return T("tg.ct.miss.contract_date", "Дата договора");
    case "tg.ct.miss.policyholder": return T("tg.ct.miss.policyholder", "Страхователь");
    case "tg.ct.miss.object": return T("tg.ct.miss.object", "Объект страхования");
    case "tg.ct.miss.sum_insured": return T("tg.ct.miss.sum_insured", "Страховая сумма");
    case "tg.ct.miss.tariff_pct": return T("tg.ct.miss.tariff_pct", "Тариф");
    case "tg.ct.miss.premium": return T("tg.ct.miss.premium", "Страховая премия");
    case "tg.ct.miss.term": return T("tg.ct.miss.term", "Срок страхования");
    case "tg.ct.miss.franchise": return T("tg.ct.miss.franchise", "Франшиза");
    case "tg.ct.miss.covered_risks": return T("tg.ct.miss.covered_risks", "Застрахованные риски");
    case "tg.ct.ess.object": return T("tg.ct.ess.object", "объект страхования");
    case "tg.ct.ess.insured_event": return T("tg.ct.ess.insured_event", "страховой случай (застрахованные риски)");
    case "tg.ct.ess.sum_insured": return T("tg.ct.ess.sum_insured", "размер страховой суммы");
    case "tg.ct.ess.premium": return T("tg.ct.ess.premium", "размер страховой премии");
    case "tg.ct.ess.term": return T("tg.ct.ess.term", "срок действия договора");
    case "tg.ct.risk.all_risks": return T("tg.ct.risk.all_risks", "все риски");
    case "tg.ct.risk.fire": return T("tg.ct.risk.fire", "пожар");
    case "tg.ct.risk.lightning": return T("tg.ct.risk.lightning", "удар молнии");
    case "tg.ct.risk.explosion": return T("tg.ct.risk.explosion", "взрыв");
    case "tg.ct.risk.water": return T("tg.ct.risk.water", "залив, повреждение водой");
    case "tg.ct.risk.natural": return T("tg.ct.risk.natural", "стихийные бедствия");
    case "tg.ct.risk.earthquake": return T("tg.ct.risk.earthquake", "землетрясение");
    case "tg.ct.risk.flood": return T("tg.ct.risk.flood", "наводнение");
    case "tg.ct.risk.storm": return T("tg.ct.risk.storm", "буря, ураган");
    case "tg.ct.risk.hail": return T("tg.ct.risk.hail", "град");
    case "tg.ct.risk.landslide": return T("tg.ct.risk.landslide", "оползень, сель");
    case "tg.ct.risk.theft": return T("tg.ct.risk.theft", "кража, грабёж, разбой");
    case "tg.ct.risk.third_party": return T("tg.ct.risk.third_party", "противоправные действия третьих лиц");
    case "tg.ct.risk.vehicle_impact": return T("tg.ct.risk.vehicle_impact", "наезд транспорта");
    case "tg.ct.risk.aircraft": return T("tg.ct.risk.aircraft", "падение летательных аппаратов");
    case "tg.ct.risk.glass": return T("tg.ct.risk.glass", "бой стёкол");
    case "tg.ct.risk.breakdown": return T("tg.ct.risk.breakdown", "поломка, авария оборудования");
    case "tg.ct.risk.electrical": return T("tg.ct.risk.electrical", "короткое замыкание");
    case "tg.ct.risk.collision": return T("tg.ct.risk.collision", "ДТП, столкновение");
    case "tg.ct.risk.other": return T("tg.ct.risk.other", "другое");
    case "tg.ct.excl.war": return T("tg.ct.excl.war", "военные действия");
    case "tg.ct.excl.terrorism": return T("tg.ct.excl.terrorism", "терроризм");
    case "tg.ct.excl.nuclear": return T("tg.ct.excl.nuclear", "ядерный взрыв, радиация");
    case "tg.ct.excl.riots": return T("tg.ct.excl.riots", "забастовки, беспорядки");
    case "tg.ct.excl.intent": return T("tg.ct.excl.intent", "умысел страхователя");
    case "tg.ct.excl.gross_negligence": return T("tg.ct.excl.gross_negligence", "грубая неосторожность");
    case "tg.ct.excl.wear": return T("tg.ct.excl.wear", "износ, коррозия");
    case "tg.ct.excl.confiscation": return T("tg.ct.excl.confiscation", "конфискация, арест");
    case "tg.ct.excl.defects": return T("tg.ct.excl.defects", "дефекты, брак");
    case "tg.ct.excl.consequential": return T("tg.ct.excl.consequential", "косвенные убытки, упущенная выгода");
    case "tg.ct.excl.cyber": return T("tg.ct.excl.cyber", "кибер-риски");
    case "tg.ct.excl.mould": return T("tg.ct.excl.mould", "плесень, грибок");
    case "tg.ct.excl.pollution": return T("tg.ct.excl.pollution", "загрязнение");
    case "tg.ct.excl.intoxication": return T("tg.ct.excl.intoxication", "алкогольное или наркотическое опьянение");
    case "tg.ct.excl.other": return T("tg.ct.excl.other", "другое");
    case "tg.ct.pay.single": return T("tg.ct.pay.single", "единовременно");
    case "tg.ct.pay.installments": return T("tg.ct.pay.installments", "в рассрочку");
    default: return "";
  }
}

/* ---------- правка условий: тариф, премия, срок, франшиза (одинаково для запроса и договора) ---------- */
function dqFrMode(fr){ return !fr ? "" : !fr.applied ? "none" : fr.amount != null && fr.pct == null ? "amount" : "pct"; }
function dqFrHtml(k){
  const d = CH[k], fr = d.req.franchise, mode = dqFrMode(fr), id = "dq" + k;
  const seg = (v, text) => '<button type="button" data-dqfr="' + k + '" data-value="' + v + '" aria-pressed="' + (mode === v) + '">' + esc(text) + "</button>";
  const o = d.orig.franchise;
  return '<label class="f">' + esc(T("tg.act.fr_title", "Франшиза")) + "</label>"
    + '<div class="segsel" role="group" aria-label="' + esc(T("tg.act.fr_title", "Франшиза")) + '">'
    + seg("none", T("tg.dq.fr_none", "не применяется")) + seg("pct", T("tg.dq.fr_pct", "в %")) + seg("amount", T("tg.dq.fr_amount", "суммой")) + "</div>"
    + (mode === "pct" || mode === "amount"
      ? '<div class="fr-val"><input id="' + id + '-frval" type="text" autocomplete="off" enterkeyhint="done" data-dq="' + k + '" data-dqf="fr_val"'
        + (mode === "amount" ? ' inputmode="numeric" value="' + esc(isNum(fr.amount) ? Number(fr.amount).toLocaleString(LOC()) : "") + '" placeholder="0"'
          : ' inputmode="decimal" maxlength="7" value="' + esc(isNum(fr.pct) ? nf(fr.pct, dp(fr.pct)) : "") + '" placeholder="1"')
        + ' aria-label="' + esc(T("tg.act.fr_size", "Размер")) + '"><span class="unit">' + esc(mode === "amount" ? SUM() : "%") + "</span></div>" : "")
    + (!fr ? '<span class="hint">' + esc(T("tg.dq.fr_not_found", "В документе франшиза не найдена.")) + "</span>" : "")
    + (o && o.text ? '<span class="hint">' + esc(T("tg.dq.in_doc", "В документе: «{v}»", {v: o.text})) + "</span>" : "");
}
function dqEditHtml(k){
  const d = CH[k], r = d.req, id = "dq" + k, both = !!(r.term_from && r.term_to);
  const err = CH.errs[k];
  return '<div class="h3">' + esc(T("tg.dq.edit_title", "Условия — можно исправить")) + "</div>"
    + '<div class="fgrid dq-edit">'
    + '<div class="half"><label class="f" for="' + id + '-tariff">' + esc(T("tg.dq.tariff", "Тариф")) + ' <span class="unit">' + esc(T("tg.dq.pct_year", "% годовых")) + "</span></label>"
    + '<input id="' + id + '-tariff" type="text" inputmode="decimal" autocomplete="off" enterkeyhint="done" maxlength="8" data-dq="' + k + '" data-dqf="tariff_pct" value="'
    + esc(isNum(r.tariff_pct) ? nf(r.tariff_pct, dp(r.tariff_pct)) : "") + '" placeholder="—"></div>'
    + '<div class="half"><label class="f" for="' + id + '-premium">' + esc(T("tg.act.premium", "Премия")) + ' <span class="unit">' + esc(SUM()) + "</span></label>"
    + '<input id="' + id + '-premium" type="text" inputmode="numeric" autocomplete="off" enterkeyhint="done" data-dq="' + k + '" data-dqf="premium" value="'
    + esc(isNum(r.premium) ? Math.round(r.premium).toLocaleString(LOC()) : "") + '" placeholder="—"></div>'
    + '<div class="half"><label class="f" for="' + id + '-from">' + esc(T("tg.dq.term_from", "Срок: с")) + "</label>"
    + '<input id="' + id + '-from" type="date" data-dq="' + k + '" data-dqf="term_from" value="' + esc(r.term_from || "") + '"></div>'
    + '<div class="half"><label class="f" for="' + id + '-to">' + esc(T("tg.dq.term_to", "по")) + "</label>"
    + '<input id="' + id + '-to" type="date" data-dq="' + k + '" data-dqf="term_to" value="' + esc(r.term_to || "") + '"></div>'
    + '<div class="half"><label class="f" for="' + id + '-days">' + esc(T("tg.act.f.term_days", "Срок страхования, дней")) + "</label>"
    + '<input id="' + id + '-days" class="w-short" type="text" inputmode="numeric" autocomplete="off" maxlength="4" data-dq="' + k + '" data-dqf="term_days" value="'
    + esc(r.term_days || "") + '"' + (both ? " disabled" : "") + ' placeholder="—"></div>'
    + '<div class="full"><span class="hint" id="' + id + '-term">' + esc(dqTermHint(k)) + "</span></div>"
    + '<div class="full fr-set" id="' + id + '-fr">' + dqFrHtml(k) + "</div>"
    + "</div>"
    + '<span class="ferr" id="wzErr-' + k + '">' + esc(err || "") + "</span>"
    + '<div class="dq-src" id="' + id + '-src">' + dqSrcHtml(k) + "</div>";
}
function dqTermHint(k){
  const d = CH[k], r = d.req;
  if (r.term_from && r.term_to) {
    const n = dqDays(r.term_from, r.term_to, d.fields.term_inclusive);
    return n ? T("tg.dq.term_by_dates", "По датам: {n} дн., оба крайних дня включены.", {n: n}) + (n > 366 ? " " + T("tg.dq.multi_year_hint", "Многолетний договор: премия — на весь срок, тариф — годовой.") : "")
      : T("tg.dq.term_bad", "Дата «по» раньше даты «с» — исправьте.");
  }
  return T("tg.dq.term_hint", "Укажите обе даты — дни посчитаются сами. Или только число дней.");
}
function dqSrcHtml(k){
  const ed = dqEdited(k);
  return '<span class="' + (ed ? "ed" : "") + '">' + esc(ed ? T("tg.dq.src_edited", "Исправлено вами — в акт уйдёт как введённое сотрудником.")
      : T("tg.dq.src_doc", "Из документа — проверьте. Исправленное уйдёт в акт как введённое сотрудником.")) + "</span>"
    + (ed ? '<button type="button" class="btn-link" data-dqreset="' + k + '">' + esc(T("tg.dq.reset", "Вернуть как в документе")) + "</button>" : "");
}
function dqSrcPaint(k){ const s = $("#dq" + k + "-src"); if (s) s.innerHTML = dqSrcHtml(k); }
/* ввод в поле условий: значение запоминаем, карточку не перерисовываем — курсор остаётся на месте */
function dqInput(el){
  const k = el.dataset.dq, d = CH[k];
  if (!d) return;
  const r = d.req, f = el.dataset.dqf;
  if (f === "tariff_pct") { const c = decimalLive(el); r.tariff_pct = c === "" ? null : dec(c); }
  else if (f === "premium") { groupDigitsLive(el); r.premium = num(el.value) || null; }
  else if (f === "term_days") { if (/\D/.test(el.value)) el.value = el.value.replace(/\D/g, ""); r.term_days = el.value ? Number(el.value) : null; }
  else if (f === "term_from" || f === "term_to") {
    r[f] = sIso(el.value);
    const both = !!(r.term_from && r.term_to), days = $("#dq" + k + "-days");
    if (both) { const n = dqDays(r.term_from, r.term_to, d.fields.term_inclusive); r.term_days = n; if (days) days.value = n || ""; }
    if (days) days.disabled = both;
    const h = $("#dq" + k + "-term");
    if (h) h.textContent = dqTermHint(k);
  } else if (f === "fr_val" && r.franchise) {
    if (dqFrMode(r.franchise) === "amount") { groupDigitsLive(el); r.franchise.amount = num(el.value) || null; }
    else { const c = decimalLive(el); r.franchise.pct = c === "" ? null : dec(c); }
    r.franchise.text = null;
  }
  wzClearErr(k);
  dqSrcPaint(k);
  if (f === "tariff_pct") rqSrcPaint();
  wzSave();
}
function dqFrSet(k, v){
  const d = CH[k];
  if (!d) return;
  const cur = d.req.franchise;
  if (v === "none") d.req.franchise = {applied: false, text: null, pct: null, amount: null};
  else if (v === "pct") d.req.franchise = {applied: true, text: null, pct: cur && cur.applied ? cur.pct : null, amount: null};
  else if (v === "amount") d.req.franchise = {applied: true, text: null, pct: null, amount: cur && cur.applied ? cur.amount : null};
  // вернулись к тому, что в документе, — это снова значение документа
  if (d.orig.franchise && dqFrMode(d.orig.franchise) === v && !(d.req.franchise.pct != null || d.req.franchise.amount != null)) d.req.franchise = JSON.parse(JSON.stringify(d.orig.franchise));
  if (cur && cur.type && d.req.franchise.applied) d.req.franchise.type = cur.type;
  haptic("select");
  wzClearErr(k);
  const box = $("#dq" + k + "-fr");
  if (box) box.innerHTML = dqFrHtml(k);
  dqSrcPaint(k);
  wzSave();
}
function dqReset(k){
  const d = CH[k];
  if (!d) return;
  d.req = JSON.parse(JSON.stringify(d.orig));
  wzClearErr(k);
  haptic("select");
  wzSave();
  wzPart(k);
}
/* проверка условий до отправки: те же границы, что app/act.py (_validate_terms) */
function dqCheck(k){
  const d = CH[k];
  if (!d) return "";
  const r = d.req, fr = r.franchise;
  const bad = (r.tariff_pct != null && !(r.tariff_pct > 0 && r.tariff_pct <= 100)) || (r.premium != null && !(r.premium > 0))
    || (!!r.term_from !== !!r.term_to) || (r.term_from && r.term_to && !dqDays(r.term_from, r.term_to, d.fields.term_inclusive))
    || (r.term_days != null && !(Number.isInteger(r.term_days) && r.term_days >= 1 && r.term_days <= 3660))
    || (fr && fr.applied && ((fr.pct != null && !(fr.pct > 0 && fr.pct <= 100)) || (fr.amount != null && !(fr.amount > 0))));
  return bad ? errText(k) : "";
}
/* в POST /act/make: условия как есть — как прочитано сервером, с правками; источник каждого поля и правки
   «было → стало» сервер определяет сам, сравнивая со своей загрузкой (source экран не вычисляет) */
function dqBody(k){
  const d = CH[k];
  if (!d) return null;
  const r = JSON.parse(JSON.stringify(d.req));
  if (r.term_from && r.term_to) delete r.term_days;
  else { delete r.term_from; delete r.term_to; }
  Object.keys(r).forEach(x => { if (r[x] == null || (Array.isArray(r[x]) && !r[x].length)) delete r[x]; });
  const terms = ["tariff_pct", "premium", "franchise", "term_days", "term_from"].some(x => r[x] != null);
  if (k === "br") return terms ? r : null;
  return terms || ["sum_insured", "contract_no", "object_description", "covered_risks", "payments"].some(x => r[x] != null) ? r : null;
}

/* =====================================================================================
   Отчёт кредитного бюро по заёмщику (01.10.2026). POST /act/photos отдаёт credit_report (app/act.py,
   credit_report_view): detected, source, file, fields{report_date, subject_type, name, inn, oked, score, score_class,
   score_version, overview{…}, active{…}}. Шаг «Проверить»: карточка с прочитанным; балл, класс, дату и просрочки
   можно исправить — в POST /act/make поля уходят как optional.credit_report, источник и «было → стало» сервер
   определяет сам (_trust_credit). У физического лица наименования и ИНН нет вовсе. Подписи — T() по коду поля
   (при смене языка перерисовываются); значения из документа — только через esc(). Шаг «Акт» — блок borrower.
   ===================================================================================== */
const CB_CLASSES = ["14", "15", "13\u0437"];       // кредитные классы (13з — «з» буквой кириллицы)
const CB_OV = ["applications", "contracts", "contingent", "inquiries", "avg_monthly_payment", "overdue_principal_count",
  "max_overdue_principal_days", "max_overdue_principal_amount", "max_overdue_interest_days", "overdue_interest_total"];
const CB_MONEY = ["overview.avg_monthly_payment", "overview.max_overdue_principal_amount", "overview.overdue_interest_total",
  "active.total_debt", "active.overdue", "active.monthly_payment"];
const CB_EDIT = ["score", "score_class", "report_date", "active.overdue", "overview.overdue_principal_count", "overview.max_overdue_principal_days"];
const cbCredit = () => ptProdClasses().some(c => CB_CLASSES.indexOf(String(c)) >= 0);
const cbInt = (v, hi) => { const x = sNum(v); return x != null && x === Math.floor(x) && x >= 0 && x <= hi ? x : null; };
const cbMon = v => { const x = sNum(v); return x != null && x >= 0 ? x : null; };
/* поля отчёта из ответа сервера (или из sessionStorage) — только известные, в своих границах */
function cbFields(f){
  f = sObj(f);
  const legal = f.subject_type === "legal", ov = sObj(f.overview), ac = sObj(f.active), o = {};
  CB_OV.forEach(k => { o[k] = CB_MONEY.indexOf("overview." + k) >= 0 ? cbMon(ov[k]) : cbInt(ov[k], 100000); });
  return {report_date: sIso(f.report_date), subject_type: f.subject_type === "legal" || f.subject_type === "individual" ? f.subject_type : null,
    name: legal ? sStr(f.name, 200) : null, inn: legal && /^\d{9}$/.test(String(f.inn || "")) ? String(f.inn) : null,
    oked: /^\d{4,5}$/.test(String(f.oked || "")) ? String(f.oked) : null,
    score: cbInt(f.score, 1000), score_class: /^[A-E][1-9]?$/.test(String(f.score_class || "")) ? String(f.score_class) : null,
    score_version: /^\d{1,2}(\.\d{1,2})?$/.test(String(f.score_version || "")) ? String(f.score_version) : null,
    overview: o, active: {count: cbInt(ac.count, 100000), total_debt: cbMon(ac.total_debt), overdue: cbMon(ac.overdue),
      monthly_payment: cbMon(ac.monthly_payment), creditors: sArr(ac.creditors).map(x => sStr(x, 160)).filter(Boolean).slice(0, 10)}};
}
function cbFrom(c){
  c = sObj(c);
  if (!c.detected) return null;
  const f = cbFields(c.fields);
  return {source: c.source === "photo" ? "photo" : "document", file: sStr(c.file, 40), kind_label: sStr(c.kind_label, 80),
    fields: f, orig: JSON.parse(JSON.stringify(f))};
}
function cbLoad(s){
  s = sObj(s);
  if (!s.fields) return null;
  const f = cbFields(s.fields);
  return {source: s.source === "photo" ? "photo" : "document", file: sStr(s.file, 40), kind_label: sStr(s.kind_label, 80),
    fields: f, orig: cbFields(s.orig || s.fields)};
}
const cbGet = (f, code) => { const p = code.split("."); return p.length > 1 ? sObj(f[p[0]])[p[1]] : f[code]; };
function cbSet(f, code, v){ const p = code.split("."); if (p.length > 1) f[p[0]][p[1]] = v; else f[code] = v; }
/* подпись поля отчёта по коду — те же слова, что app/act_texts.py (CR_FIELD_LABELS); код не из списка — пусто */
function cbLbl(code){
  switch (code) {
    case "report_date": return T("tg.cb.f.report_date", "Дата отчёта");
    case "subject_type": return T("tg.cb.f.subject_type", "Тип субъекта");
    case "name": return T("tg.cb.f.name", "Наименование");
    case "inn": return T("tg.cb.f.inn", "ИНН");
    case "oked": return T("tg.cb.f.oked", "ОКЭД");
    case "score": return T("tg.cb.f.score", "Скоринговый балл");
    case "score_class": return T("tg.cb.f.score_class", "Класс оценки");
    case "score_version": return T("tg.cb.f.score_version", "Версия скоринга");
    case "overview.applications": return T("tg.cb.f.applications", "Заявки");
    case "overview.contracts": return T("tg.cb.f.contracts", "Договоры");
    case "overview.contingent": return T("tg.cb.f.contingent", "Условные обязательства");
    case "overview.inquiries": return T("tg.cb.f.inquiries", "Запросы и подписки");
    case "overview.avg_monthly_payment": return T("tg.cb.f.avg_monthly_payment", "Среднемесячный платёж");
    case "overview.overdue_principal_count": return T("tg.cb.f.overdue_principal_count", "Просрочек основного долга");
    case "overview.max_overdue_principal_days": return T("tg.cb.f.max_overdue_principal_days", "Максимальная просрочка основного долга, дней");
    case "overview.max_overdue_principal_amount": return T("tg.cb.f.max_overdue_principal_amount", "Максимальная просрочка основного долга, сумма");
    case "overview.max_overdue_interest_days": return T("tg.cb.f.max_overdue_interest_days", "Максимальная непрерывная просрочка процентов, дней");
    case "overview.overdue_interest_total": return T("tg.cb.f.overdue_interest_total", "Всего просроченных процентов");
    case "active.count": return T("tg.cb.f.active_count", "Действующих договоров");
    case "active.total_debt": return T("tg.cb.f.total_debt", "Остаток задолженности");
    case "active.overdue": return T("tg.cb.f.overdue", "Просроченная часть");
    case "active.monthly_payment": return T("tg.cb.f.monthly_payment", "Среднемесячный платёж по действующим");
    case "active.creditors": return T("tg.cb.f.creditors", "Кредиторы");
    default: return "";
  }
}
/* значение поля словами: суммы — сумами, даты — ДД.ММ.ГГГГ, тип субъекта — словом; пусто — "" */
function cbVal(code, v){
  if (v == null || v === "" || (Array.isArray(v) && !v.length) || typeof v === "object" && !Array.isArray(v)) return "";
  if (code === "report_date") return sIso(v) ? dateOnly(v) : "";
  if (code === "subject_type") return v === "legal" ? T("tg.cb.legal", "юридическое лицо")
    : v === "individual" ? T("tg.cb.individual", "физическое лицо — данные не извлекаются, показан только скоринг") : "";
  if (CB_MONEY.indexOf(code) >= 0) return isNum(v) ? money(v) : "";
  if (Array.isArray(v)) return v.map(x => sStr(x, 160)).filter(Boolean).join("; ");
  return isNum(v) && typeof v === "number" ? nf(v, v % 1 ? 2 : 0) : String(v);
}
const cbEdited = () => !!CH.cb && JSON.stringify(CB_EDIT.map(k => cbGet(CH.cb.fields, k))) !== JSON.stringify(CB_EDIT.map(k => cbGet(CH.cb.orig, k)));
function cbSrcHtml(){
  const ed = cbEdited();
  return '<span class="' + (ed ? "ed" : "") + '">' + esc(ed ? T("tg.cb.src_edited", "Исправлено вами — в акт уйдёт как введённое сотрудником.")
      : T("tg.cb.src_doc", "Из отчёта — проверьте. Исправленное уйдёт в акт как введённое сотрудником.")) + "</span>"
    + (ed ? '<button type="button" class="btn-link" data-cbreset="1">' + esc(T("tg.cb.reset", "Вернуть как в отчёте")) + "</button>" : "");
}
function cbCardHtml(){
  const c = CH.cb;
  if (!c) return "";
  const f = c.fields, ind = f.subject_type === "individual";
  const order = ["subject_type", "name", "inn", "oked", "score_version"].concat(CB_OV.map(k => "overview." + k),
    ["active.count", "active.total_debt", "active.monthly_payment", "active.creditors"]).filter(k => CB_EDIT.indexOf(k) < 0);
  const rows = order.map(k => [k, cbVal(k, cbGet(f, k))]).filter(x => x[1]);
  const inp = (code, label, mode, ph) => {
    const v = cbGet(f, code), id = "cbf-" + code.replace(".", "-");
    const shown = v == null ? "" : mode === "money" ? Math.round(v).toLocaleString(LOC()) : String(v);
    return '<div class="half"><label class="f" for="' + id + '">' + esc(label) + (mode === "money" ? ", " + esc(SUM()) : "") + "</label>"
      + '<input id="' + id + '" type="' + (mode === "date" ? "date" : "text") + '"' + (mode === "date" ? "" : ' inputmode="' + (mode === "text" ? "text" : "numeric") + '" autocomplete="off" enterkeyhint="done"')
      + (mode === "text" ? ' maxlength="2" autocapitalize="characters"' : mode === "int" ? ' maxlength="6"' : "")
      + ' data-cbf="' + code + '" value="' + esc(shown) + '" placeholder="' + esc(ph || "—") + '"></div>';
  };
  return '<div class="card dq-card cb-card" id="cbCard"><div class="dq-top"><h2>' + esc(T("tg.cb.title", "Отчёт кредитного бюро")) + "</h2>"
    + '<span class="tag">' + esc(c.source === "photo" ? T("tg.dq.src_scan", "со скана, прочитано моделью") : T("tg.cb.src_file", "из файла с текстом")) + "</span></div>"
    + '<p class="sub">' + esc(T("tg.cb.sub", "Данные бюро по заёмщику. В уровень риска и ставку не входят — по ним андеррайтер получит проверки.")) + "</p>"
    + (ind ? '<p class="note cb-ind">' + esc(T("tg.cb.individual", "физическое лицо — данные не извлекаются, показан только скоринг")) + "</p>" : "")
    + (rows.length ? '<div class="dq-kv">' + rows.map(x => dqKv(cbLbl(x[0]), esc(x[1]))).join("") + "</div>" : "")
    + '<div class="h3">' + esc(T("tg.cb.edit_title", "Балл, класс, дата и просрочки — можно исправить")) + "</div>"
    + '<div class="fgrid dq-edit">'
    + inp("score", cbLbl("score"), "int", "0–1000") + inp("score_class", cbLbl("score_class"), "text", "B2")
    + inp("report_date", cbLbl("report_date"), "date") + inp("active.overdue", T("tg.cb.e.overdue", "Просрочка сейчас"), "money", "0")
    + inp("overview.overdue_principal_count", T("tg.cb.e.count", "Просрочек долга"), "int", "0")
    + inp("overview.max_overdue_principal_days", T("tg.cb.e.days", "Макс. просрочка, дней"), "int", "0")
    + "</div>"
    + '<span class="ferr" id="wzErr-cb">' + esc(CH.errs.cb || "") + "</span>"
    + '<div class="dq-src" id="cb-src">' + cbSrcHtml() + "</div>"
    + '<p class="note">' + esc(T("tg.cb.no_direct", "Прямого запроса в кредитное бюро нет — отчёт загружает сотрудник; для прямого подключения нужен договор страховщика с бюро и согласие субъекта.")) + "</p>"
    + (cbCredit() ? "" : '<p class="note">' + esc(T("tg.cb.not_credit", "Класс договора не кредитный — проверки по отчёту бюро не применяются.")) + "</p>")
    + "</div>";
}
/* ввод: значение запоминаем, карточку не перерисовываем — курсор остаётся на месте */
function cbInput(el){
  const c = CH.cb;
  if (!c) return;
  const code = el.dataset.cbf;
  if (CB_EDIT.indexOf(code) < 0) return;
  let v;
  if (code === "report_date") v = sIso(el.value);
  else if (code === "score_class") {
    const up = el.value.toUpperCase().replace(/[\u0410\u0412\u0421\u0415]/g, x => ({"\u0410": "A", "\u0412": "B", "\u0421": "C", "\u0415": "E"}[x])).replace(/[^A-Z0-9]/g, "").slice(0, 2);
    if (up !== el.value) el.value = up;
    v = up || null;
  } else if (code === "active.overdue") { groupDigitsLive(el); v = String(el.value).replace(/\D/g, "") === "" ? null : num(el.value); }
  else { if (/\D/.test(el.value)) el.value = el.value.replace(/\D/g, ""); v = el.value === "" ? null : Number(el.value); }
  cbSet(c.fields, code, v);
  wzClearErr("cb");
  const s = $("#cb-src");
  if (s) s.innerHTML = cbSrcHtml();
  wzSave();
}
function cbReset(){
  if (!CH.cb) return;
  CH.cb.fields = JSON.parse(JSON.stringify(CH.cb.orig));
  wzClearErr("cb");
  haptic("select");
  wzSave();
  wzPart("cb");
}
/* те же границы, что app/credit_report.normalize (strict): неверное — сообщение до отправки */
function cbCheck(){
  const c = CH.cb;
  if (!c) return false;
  const f = c.fields, cnt = v => v == null || (Number.isInteger(v) && v >= 0 && v <= 100000);
  return (f.score != null && !(Number.isInteger(f.score) && f.score >= 0 && f.score <= 1000))
    || (f.score_class != null && !/^[A-E][1-9]?$/.test(f.score_class))
    || (f.report_date != null && !(f.report_date >= "2000-01-01" && f.report_date <= "2100-12-31"))
    || (f.active.overdue != null && !(f.active.overdue >= 0))
    || !cnt(f.overview.overdue_principal_count) || !cnt(f.overview.max_overdue_principal_days)
    || (f.score == null && f.score_class == null && f.report_date == null
      && Object.keys(f.overview).every(k => f.overview[k] == null) && ["count", "total_debt", "overdue", "monthly_payment"].every(k => f.active[k] == null));
}
/* в POST /act/make: поля как прочитаны сервером, с правками; source — как заявлено, сервер его не доверяет */
function cbBody(){
  const c = CH.cb;
  if (!c) return null;
  return Object.assign(JSON.parse(JSON.stringify(c.fields)), {source: c.source});
}
/* ---------- шаг «Акт»: «Заёмщик: данные кредитного бюро» (блок borrower ответа) ---------- */
function actCbHtml(a){
  const b = a && a.borrower;
  if (!b || b.available !== true) return "";
  const rows = scoArr(b.rows).filter(r => r && scoS(r.label));
  const checks = scoArr(b.checks).map(scoS).filter(Boolean);
  const edits = scoArr(b.edits).filter(e => e && cbLbl(scoS(e.code)));
  const na = T("tg.act.na", "данные недоступны");
  // на карточке скоринга заёмщик уже показан — тогда блок свёрнут, иначе раскрыт
  const open = CH.cbActOpen != null ? CH.cbActOpen : !(a.scoring && a.scoring.borrower);
  return '<details class="card cb-act" id="cbAct"' + (open ? " open" : "") + '><summary><span><b>' + esc(T("tg.cb.act_title", "Заёмщик: данные кредитного бюро")) + "</b>"
    + "<small>" + esc([scoS(b.source_label), checks.length ? T("tg.cb.checks_n", "проверок андеррайтеру: {n}", {n: checks.length}) : T("tg.cb.checks_0", "проверок нет")].filter(Boolean).join(" · ")) + "</small></span></summary>"
    + (rows.length ? '<div class="dq-kv">' + rows.map(r => dqKv(scoS(r.label), esc(scoS(r.value) || na))).join("") + "</div>" : "")
    + (edits.length ? '<div class="cb-edits"><p class="h3">' + esc(T("tg.cb.edits_title", "Исправлено сотрудником")) + "</p><ul>"
      + edits.map(e => "<li>" + esc(cbLbl(e.code)) + ": <s>" + esc(cbVal(e.code, e.was) || "—") + "</s> → <b>" + esc(cbVal(e.code, e.now) || "—") + "</b></li>").join("") + "</ul></div>" : "")
    + (b.doc_missing ? '<p class="note warn">' + esc(T("tg.cb.doc_missing", "Загрузка отчёта недоступна — значения введены сотрудником.")) + "</p>" : "")
    + (checks.length ? '<div class="cb-checks"><p class="h3">' + esc(T("tg.cb.checks_title", "Проверки андеррайтеру")) + "</p><ul>" + checks.map(c => "<li>" + esc(c) + "</li>").join("") + "</ul></div>"
      : b.credit_product ? '<p class="note ok">' + esc(T("tg.cb.checks_none", "По отчёту бюро проверок нет.")) + "</p>"
        : '<p class="note">' + esc(T("tg.cb.not_credit", "Класс договора не кредитный — проверки по отчёту бюро не применяются.")) + "</p>")
    + (scoS(b.note) ? '<p class="note">' + esc(b.note) + "</p>" : "")
    + (scoS(b.direct_note) ? '<p class="note">' + esc(b.direct_note) + "</p>" : "")
    + "</details>";
}

/* ---------- карточка «Запрос филиала» ---------- */
function brCardHtml(){
  const b = CH.br;
  if (!b) return "";
  const f = b.fields, p = f.product_code ? wzProd(f.product_code) : null;
  const obj = f.object_description_translated
    ? esc(f.object_description_translated) + (f.object_description && f.object_description !== f.object_description_translated
      ? '<small>' + esc(T("tg.dq.orig_text", "в документе: {v}", {v: f.object_description})) + "</small>" : "")
    : esc(f.object_description || "");
  const rows = dqKv(T("tg.dq.product", "Код продукта"), f.product_code ? '<span class="num">' + esc(f.product_code) + "</span>" + (p ? "<small>" + esc(p.name || "") + "</small>" : "") : "")
    + DQ_PARTIES.map(k => dqKv(dqLbl("tg.br.row." + k), dqPartyHtml(f[k]))).join("")
    + dqKv(T("tg.br.row.object", "Объект страхования"), obj)
    + dqKv(T("tg.dq.areas", "Площадь"), esc(dqAreasText(f.areas)))
    + dqKv(T("tg.act.f.cadastre_no", "Кадастровый номер"), f.cadastre_no ? '<span class="num">' + esc(f.cadastre_no) + "</span>" : "")
    + dqKv(T("tg.br.row.object_value", "Страховая стоимость"), esc(dqMoney(f.object_value)))
    + dqKv(T("tg.br.row.sum_insured", "Страховая сумма"), esc(dqMoney(f.sum_insured)))
    + dqKv(T("tg.dq.contract_terms", "Условия договора"), esc(f.contract_terms || ""));
  const lost = b.rows.filter(r => !r.found).map(r => dqLbl("tg.br.row." + r.code)).filter(Boolean);
  return '<div class="card dq-card" id="brCard"><div class="dq-top"><h2>' + esc(T("tg.dq.br_title", "Запрос филиала")) + "</h2>"
    + '<span class="tag">' + esc(b.source === "photo" ? T("tg.dq.src_scan", "со скана, прочитано моделью") : T("tg.dq.src_file", "из файла")) + "</span></div>"
    + '<p class="sub">' + esc(T("tg.dq.rows_read", "Прочитано строк: {n} из {m}", {n: b.rows_found, m: b.rows_total})) + "</p>"
    + '<div class="dq-kv">' + rows + "</div>"
    + (lost.length ? '<p class="note">' + esc(T("tg.dq.br_lost", "Не найдено в запросе: {what}.", {what: lost.join(", ")})) + "</p>" : "")
    + dqEditHtml("br")
    + '<p class="note">' + esc(T("tg.dq.br_note", "Тариф, премию, франшизу и срок акт сверит с расчётом: ниже ли тариф минимального и сходится ли премия.")) + "</p>"
    + "</div>";
}

/* ---------- карточка «Договор страхования» ---------- */
function ctCardHtml(){
  const c = CH.ct;
  if (!c) return "";
  const f = c.fields, ai = k => c.ai_fields.indexOf(k) >= 0, cur = f.currency;
  // поле бланка не заполнено (подчёркивания в документе) — серым «не заполнено»
  const blank = k => (c.blank || []).indexOf(k) >= 0 ? '<span class="muted">' + esc(T("tg.dq.blank", "не заполнено")) + "</span>" : "";
  const noDate = [f.contract_no ? T("tg.dq.no", "№ {v}", {v: f.contract_no}) : "", f.contract_date ? T("tg.dq.from_date", "от {d}", {d: dateOnly(f.contract_date)}) : ""].filter(Boolean).join(" ");
  const car = [f.brand, f.model].filter(Boolean).join(" ");
  let rows = dqKv(T("tg.dq.no_date", "Номер и дата"), (esc(noDate) || blank("contract_no") || blank("contract_date")) + (f.place ? "<small>" + esc(f.place) + "</small>" : ""))
    + dqKv(T("tg.dq.ct_name", "Название"), esc(f.product_name || ""))
    + dqKv(T("tg.dq.insurer", "Страховщик"), dqPartyHtml(f.insurer))
    + DQ_PARTIES.map(k => dqKv(dqLbl("tg.br.row." + k), dqPartyHtml(f[k]) || blank(k))).join("")
    + dqKv(T("tg.br.row.object", "Объект страхования"), f.object_description ? esc(f.object_description) + dqAi(ai("object_description")) : "")
    + dqKv(T("tg.dq.address", "Адрес"), esc(f.address || f.region || ""))
    + dqKv(T("tg.act.f.cadastre_no", "Кадастровый номер"), f.cadastre_no ? '<span class="num">' + esc(f.cadastre_no) + "</span>" : "")
    + dqKv(T("tg.dq.areas", "Площадь"), esc(dqAreasText(f.areas)))
    + dqKv(T("tg.act.f.construction", "Конструкция, материал стен"), esc(f.construction || ""))
    + dqKv(T("tg.dq.year_built", "Год постройки"), esc(f.year_built || ""))
    + dqKv(T("tg.dq.brand_model", "Марка и модель"), esc(car))
    + dqKv(T("tg.dq.vin", "VIN или заводской номер"), f.vin ? '<span class="num">' + esc(f.vin) + "</span>" : "")
    + dqKv(T("tg.act.f.reg_no", "Государственный номер"), f.reg_no ? '<span class="num">' + esc(f.reg_no) + "</span>" : "")
    + dqKv(T("tg.br.row.sum_insured", "Страховая сумма"), f.sum_insured != null ? '<span class="num">' + esc(dqMoney(f.sum_insured, cur)) + "</span>" + dqAi(ai("sum_insured")) : blank("sum_insured"))
    + dqKv(T("tg.br.row.object_value", "Страховая стоимость"), f.object_value != null ? '<span class="num">' + esc(dqMoney(f.object_value, cur)) + "</span>" + dqAi(ai("object_value")) : "")
    + dqKv(T("tg.br.row.premium", "Страховая премия"), blank("premium"))
    + dqKv(T("tg.br.row.term", "Срок страхования"), blank("term"));
  if (f.items.length) {
    rows += dqKv(T("tg.dq.items", "Объекты по договору"), f.items.map(x => esc((x.name || T("tg.dq.item_noname", "без названия")) + " — " + dqMoney(x.sum, cur))).join("<br>"));
  }
  const pay = [f.payment_mode ? dqLbl("tg.ct.pay." + f.payment_mode) : ""]
    .concat(f.payments.map(p => (p.date ? dateOnly(p.date) + " — " : "") + dqMoney(p.amount, cur))).filter(Boolean);
  rows += dqKv(T("tg.dq.payments", "Порядок оплаты"), pay.length ? pay.map(esc).join("<br>") + dqAi(ai("payment_mode") || ai("payments")) : "")
    + dqKv(T("tg.dq.territory", "Территория"), esc(f.territory || ""))
    + dqKv(T("tg.dq.notice", "Срок уведомления о событии"), esc(f.notice || ""));
  const chips = (list, pre, x) => list.map(r => '<span class="dq-chip' + (x ? " x" : "") + '">' + esc(r.code === "other" ? (r.label || T("tg.ct.risk.other", "другое")) : (dqLbl(pre + r.code) || r.label || "")) + "</span>").join("");
  const ess = c.essentials.length
    ? '<div class="h3">' + esc(T("tg.dq.ess_title", "Существенные условия договора")) + "</div>"
      + '<ul class="dq-ess">' + c.essentials.map(e => '<li class="' + (e.present ? "yes" : "no") + '"><i>' + esc(e.present ? T("tg.dq.ess_yes", "есть") : T("tg.dq.ess_no", "нет")) + "</i><span>"
        + esc(dqLbl("tg.ct.ess." + e.code)) + "</span></li>").join("") + "</ul>"
      + '<p class="note">' + esc(T("tg.dq.ess_ref", "Перечень — {ref}. Без существенного условия договор нужно дополнить.", {ref: T("tg.dq.legal_929", "ГК РУз, ст. 929")})) + "</p>"
    : "";
  const src = c.source === "photo" ? T("tg.dq.ct_src_photo", "со скана договора, прочитано моделью")
    : c.source === "document_ai" ? T("tg.dq.ct_src_ai", "из текста договора, часть полей прочитана моделью") : T("tg.dq.ct_src_doc", "из файла договора, без модели");
  return '<div class="card dq-card" id="ctCard"><div class="dq-top"><h2>' + esc(T("tg.dq.ct_title", "Договор страхования")) + "</h2>"
    + '<span class="tag">' + esc(src) + "</span></div>"
    + '<p class="sub">' + esc(T("tg.dq.fields_read", "Прочитано полей: {n}", {n: c.found_n}) + (c.pages ? " · " + T("tg.dq.pages", "страниц: {n}", {n: c.pages}) : "")) + "</p>"
    + (c.tpl && c.tpl_note ? '<p class="note warn">' + esc(c.tpl_note) + "</p>" : "")
    + (c.truncated ? '<p class="note warn">' + esc(T("tg.dq.truncated", "Договор длинный — прочитана только часть документа. Проверьте условия по оригиналу.")) + "</p>" : "")
    + '<div class="dq-kv">' + rows + "</div>"
    + (c.covered_risks.length ? '<div class="h3">' + esc(T("tg.dq.risks", "Застрахованные риски")) + '</div><div class="dq-chips">' + chips(c.covered_risks, "tg.ct.risk.") + "</div>" : "")
    + (c.exclusions.length ? '<div class="h3">' + esc(T("tg.dq.excl", "Исключения")) + '</div><div class="dq-chips">' + chips(c.exclusions, "tg.ct.excl.", true) + "</div>" : "")
    + (f.special_terms.length ? '<details class="mini"><summary>' + esc(T("tg.dq.special", "Особые условия · {n}", {n: f.special_terms.length})) + "</summary><ul>"
      + f.special_terms.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul></details>" : "")
    + ess
    + (c.missing.length && !c.tpl ? '<p class="note warn">' + esc(T("tg.dq.ct_missing", "Не найдено в договоре: {what}.", {what: c.missing.map(x => dqLbl("tg.ct.miss." + x)).filter(Boolean).join(", ")})) + "</p>" : "")
    + (c.ai_fields.length ? '<p class="note">' + esc(T("tg.dq.ai_note", "Значения с пометкой «прочитано моделью» модель взяла из текста договора — сверьте их с оригиналом.")) + "</p>" : "")
    + dqEditHtml("ct")
    + "</div>";
}

/* ---------- «Запрос и договор: расхождения» (шаг 2 — из загрузки, шаг 3 — из акта) ---------- */
function xLabel(code){
  switch (code) {
    case "product_code": return T("tg.dq.x.product_code", "Код продукта");
    case "sum_insured": return T("tg.br.row.sum_insured", "Страховая сумма");
    case "object_value": return T("tg.br.row.object_value", "Страховая стоимость");
    case "tariff_pct": return T("tg.dq.tariff", "Тариф");
    case "premium": return T("tg.br.row.premium", "Страховая премия");
    case "term": return T("tg.dq.x.term", "Срок");
    case "franchise": return T("tg.act.fr_title", "Франшиза");
    default: return T("tg.dq.x.object", "Объект");
  }
}
function hintName(c){
  switch (c) {
    case "building": return T("tg.act.grp.property", "Здание, помещение");
    case "equipment": return T("tg.act.grp.equipment", "Оборудование");
    case "vehicle": case "special_machinery": return T("tg.act.grp.vehicle", "Транспорт и спецтехника");
    case "cargo": return T("tg.act.grp.cargo", "Груз");
    default: return "";
  }
}
function xVal(code, v){
  const na = "—";
  if (v == null || v === "") return na;
  switch (code) {
    case "sum_insured": case "object_value": case "premium": return isNum(v) ? money(v) : na;
    case "tariff_pct": return dqPct(v) || na;
    case "term": return v.days ? T("tg.act.days_n", "{n} дн.", {n: v.days}) + (v.from && v.to ? " (" + dateOnly(v.from) + " – " + dateOnly(v.to) + ")" : "") : na;
    case "franchise": return !v.applied ? T("tg.dq.fr_none", "не применяется") : isNum(v.pct) ? dqPct(v.pct) : isNum(v.amount) ? money(v.amount) : (v.text || na);
    case "object": return v.cadastre_no || hintName(v.class_hint) || na;
    default: return String(v);
  }
}
function xVerdict(v){
  switch (v) {
    case "same": return T("tg.dq.x.same", "совпадает");
    case "differs": return T("tg.dq.x.differs", "расходится");
    default: return T("tg.dq.x.missing", "нет в одном из документов");
  }
}
function xTableHtml(xc){
  const n = xc.items.filter(i => i.verdict === "differs").length;
  const sum = n ? T("tg.dq.x.sum_differs", "Запрос филиала и договор расходятся: {n}. Уточните, какой документ верный.", {n: n})
    : xc.items.some(i => i.verdict === "same") ? T("tg.dq.x.sum_ok", "Запрос филиала и договор совпадают по сверяемым условиям.")
    : T("tg.dq.x.sum_missing", "Сверить не по чему: нужных условий нет в обоих документах.");
  return '<p class="xt-sum' + (n ? " warn" : "") + '">' + esc(sum) + "</p>"
    + '<div class="xt" role="table" aria-label="' + esc(T("tg.dq.x.title", "Запрос и договор: расхождения")) + '">'
    + '<div class="xt-h" role="row"><span role="columnheader">' + esc(T("tg.dq.x.what", "Что")) + '</span><span role="columnheader">' + esc(T("tg.dq.x.in_req", "В запросе"))
    + '</span><span role="columnheader">' + esc(T("tg.dq.x.in_ct", "В договоре")) + '</span><span role="columnheader">' + esc(T("tg.dq.x.verdict", "Вывод")) + "</span></div>"
    + xc.items.map(i => '<div class="xt-r ' + (i.verdict === "differs" ? "xt-bad" : i.verdict === "same" ? "xt-ok" : "xt-na") + '" role="row">'
      + '<b class="xt-l" role="cell">' + esc(xLabel(i.code)) + "</b>"
      + '<span class="xt-v" role="cell"><i>' + esc(T("tg.dq.x.in_req", "В запросе")) + "</i>" + esc(xVal(i.code, i.request)) + "</span>"
      + '<span class="xt-v" role="cell"><i>' + esc(T("tg.dq.x.in_ct", "В договоре")) + "</i>" + esc(xVal(i.code, i.contract)) + "</span>"
      + '<span class="xt-s" role="cell">' + esc(xVerdict(i.verdict)) + "</span></div>").join("")
    + "</div>";
}
function xcCardHtml(){
  if (!CH.xc) return "";
  return '<div class="card dq-card" id="xcCard"><h2>' + esc(T("tg.dq.x.title", "Запрос и договор: расхождения")) + "</h2>"
    + '<p class="sub">' + esc(T("tg.dq.x.sub", "По прочитанному из документов. После правок сверка в акте пересчитывается.")) + "</p>"
    + xTableHtml(CH.xc) + "</div>";
}

/* ---------- шаг «Акт»: сверка с запросом филиала и с договором (request_check / contract_check) ---------- */
function ckVal(code, v){
  if (v == null) return T("tg.dq.ck_none", "нет данных");
  switch (code) {
    case "tariff_min": case "tariff_act": return dqPct(v);
    case "franchise": return Number(v) === 0 ? T("tg.dq.fr_none", "не применяется") : dqPct(v);
    case "term": return T("tg.act.days_n", "{n} дн.", {n: v});
    default: return isNum(v) ? money(v) : T("tg.dq.ck_none", "нет данных");
  }
}
function ckCalcLabel(code){
  switch (code) {
    case "tariff_min": return T("tg.dq.ck_min", "минимальный");
    case "tariff_act": return T("tg.dq.ck_act_rate", "ставка акта");
    case "premium_act": return T("tg.dq.ck_act_prem", "премия акта");
    case "sum_value": return T("tg.br.row.object_value", "Страховая стоимость").toLowerCase();
    case "term": return T("tg.dq.ck_act_term", "в акте");
    case "franchise": return T("tg.dq.ck_act_fr", "в акте");
    case "payments": return T("tg.dq.ck_pay_total", "по графику");
    case "items_sum": return T("tg.dq.ck_items_total", "по объектам");
    default: return T("tg.dq.ck_calc", "расчёт");
  }
}
function ckRowHtml(i, docLabel){
  const v = i.verdict;
  const cls = i.reference ? "ck-ref" : v === "below_min" || v === "differs" || v === "no_essential" ? "ck-warn" : v === "ok" ? "ck-ok" : "ck-na";
  const vals = i.code !== "essentials" && (i.requested != null || i.calculated != null);
  return '<div class="ck-r ' + cls + '"><p class="ck-h"><b>' + esc(i.label || "") + "</b>"
    + '<span class="ck-v">' + esc(i.verdict_label || "") + "</span></p>"
    + (vals ? '<p class="ck-n"><span>' + esc(docLabel) + ": <b>" + esc(ckVal(i.code, i.requested)) + "</b></span><span>"
      + esc(ckCalcLabel(i.code)) + ": <b>" + esc(ckVal(i.code, i.calculated)) + "</b></span></p>" : "")
    + (i.text ? '<p class="ck-t">' + esc(i.text) + "</p>" : "") + "</div>";
}
function actCheckHtml(c, kind){
  if (!c || typeof c !== "object" || !c.available) return "";
  const s = sObj(c.summary), v = s.verdict;
  const tone = v === "ok" ? "ok" : v === "below_min" || v === "differs" || v === "no_essential" ? "warn" : "";
  const items = sArr(c.items).filter(i => i && typeof i === "object");
  const how = sArr(c.how).filter(x => typeof x === "string" && x);
  const doc = kind === "rq" ? T("tg.dq.ck_in_req", "в запросе") : T("tg.dq.ck_in_ct", "в договоре");
  return '<div class="ck-box ' + tone + '"><p class="fr-h"><b>' + esc(kind === "rq" ? T("tg.dq.rq_title", "Сверка с запросом филиала") : T("tg.dq.ct_check_title", "Сверка с договором")) + "</b></p>"
    + (s.text ? '<p class="ck-sum">' + esc(s.text) + "</p>" : "")
    + '<div class="ck-rows">' + items.map(i => ckRowHtml(i, doc)).join("") + "</div>"
    + ckEditsHtml(c.edits)
    + (how.length ? '<details class="mini"><summary>' + esc(T("tg.dq.how", "Как сверено · {n}", {n: how.length})) + "</summary><ul>"
      + how.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul></details>" : "")
    + (c.source_label ? '<p class="ck-src">' + esc(T("tg.dq.ck_src", "Источник: {v}", {v: String(c.source_label)})) + "</p>" : "")
    + "</div>";
}
/* правки сотрудника в условиях документа (сервер сравнил ввод со своей загрузкой): строка и «было → стало» */
function ckEditsHtml(e){
  e = sObj(e);
  const line = typeof e.line === "string" ? e.line : "";
  const items = sArr(e.items).filter(x => x && typeof x.text === "string" && x.text);
  if (!line && !items.length) return "";
  return '<div class="ck-ed' + (items.length ? " on" : "") + '">' + (line ? '<p class="ck-ed-h">' + esc(line) + "</p>" : "")
    + (items.length ? '<ul class="ck-ed-l">' + items.map(x => "<li>" + esc(x.text) + "</li>").join("") + "</ul>" : "") + "</div>";
}
function actXcHtml(a){
  const xc = dqXcFrom(a.cross_check);
  if (!xc) return "";
  return '<div class="ck-box ' + (xc.items.some(i => i.verdict === "differs") ? "warn" : "ok") + '"><p class="fr-h"><b>' + esc(T("tg.dq.x.title", "Запрос и договор: расхождения")) + "</b></p>"
    + xTableHtml(xc) + "</div>";
}

/* =====================================================================================
   Оценка стоимости по объявлениям (30.09.2026): шаг «Проверить», карточка «Оценка по объявлениям».
   Сервер на площадки не ходит (OLX закрыт от автоматических запросов): GET /act/market/links даёт адреса
   поиска для браузера сотрудника; сотрудник снимает экран со списком объявлений и загружает снимки —
   POST /act/market/shots (модель читает цены, сервер пересчитывает доллары и считает предварительную медиану).
   Правки сотрудника (отметка «учитывать», цена, объявления вручную) уходят в POST /act/make → optional.market
   вместе с shots_session и курсом; окончательную оценку и раздел 3 акта считает сервер.
   Предпросмотр на экране — тот же расчёт, что app/act_engine.market_estimate: отбор (отметка, цена, курс,
   срок), выбросы от медианы отобранных, медиана, 25-й и 75-й перцентили линейной интерполяцией,
   расхождение |заявленная − медиана| / заявленная × 100. Пороги — значения по умолчанию act_engine (market),
   экспертные, не калиброваны: это написано на экране.
   Ссылки открываются только https и только на olx.uz, avtoelon.uz, uybor.uz, joymee.uz (в Telegram — TG.openLink).
   В sessionStorage (MK_KEY) — номер загрузки снимков, дата снимков, курс, объявления с правками и «что ищем»;
   имён файлов снимков и текстов сервера там нет.
   ===================================================================================== */
const MK_HOSTS = ["olx.uz", "avtoelon.uz", "uybor.uz", "joymee.uz"];
const MK_SITES = ["olx", "avtoelon", "uybor", "joymee"];
const MK_USD = c => c === "USD" || c === "у.е.";

/* ---------- состояние в sessionStorage: без имён файлов ---------- */
function mkSave(){
  try {
    sessionStorage.setItem(MK_KEY, JSON.stringify({ss: MK.ss, date: MK.shotDate, fx: MK.fx, need: MK.need, rate: MK.rate,
      q: MK.q, site: MK.site, seq: MK.mseq, listings: MK.listings, declOrig: MK.declOrig}));
  } catch (e) { /* без sessionStorage оценка живёт до перезагрузки */ }
}
function mkLoad(){
  let s = null;
  try { s = JSON.parse(sessionStorage.getItem(MK_KEY) || "null"); } catch (e) { s = null; }
  if (!s || typeof s !== "object") return;
  MK.ss = typeof s.ss === "string" && /^[0-9a-f]{8,40}$/.test(s.ss) ? s.ss : "";
  MK.shotDate = /^\d{4}-\d{2}-\d{2}$/.test(String(s.date || "")) ? s.date : "";
  const fx = s.fx && typeof s.fx === "object" && Number(s.fx.rate) > 0 ? s.fx : null;
  MK.fx = fx ? {rate: Number(fx.rate), by: String(fx.by || ""), as_of: String(fx.as_of || "")} : null;
  MK.need = !!s.need;
  MK.rate = typeof s.rate === "string" ? s.rate.slice(0, 12) : "";
  MK.q = typeof s.q === "string" ? s.q.slice(0, 60) : "";
  MK.site = MK_SITES.indexOf(s.site) >= 0 ? s.site : "olx";
  MK.mseq = Number(s.seq) || 0;
  MK.declOrig = Number(s.declOrig) > 0 ? Number(s.declOrig) : 0;
  MK.listings = (Array.isArray(s.listings) ? s.listings : []).filter(r => r && typeof r === "object")
    .slice(0, MK_MAX_LISTINGS).map(r => mkRow(r));
}
function mkReset(){
  MK.queue.forEach(q => { if (q.url) URL.revokeObjectURL(q.url); });
  Object.assign(MK, MK_FRESH());
  try { sessionStorage.removeItem(MK_KEY); } catch (e) {}
}
/* объявление из ответа сервера или из sessionStorage → запись для экрана; всё — недоверенный текст */
function mkRow(r, source){
  const n = v => v == null || v === "" || isNaN(Number(v)) ? null : Number(v);
  const s = (v, lim) => v == null || typeof v === "object" ? null : (String(v).slice(0, lim) || null);
  const price = n(r.price) > 0 ? n(r.price) : null;
  const o = r.orig && typeof r.orig === "object" ? r.orig : r;
  return {id: /^[A-Za-z0-9_-]{1,12}$/.test(String(r.id || "")) ? String(r.id) : "m" + (++MK.mseq),
    title: s(r.title, 160) || "", price: price, currency: MK_USD(r.currency) ? String(r.currency) : "UZS",
    year: n(r.year), mileage_km: n(r.mileage_km), hours: n(r.hours), region: s(r.region, 80),
    posted_date: /^\d{4}-\d{2}-\d{2}$/.test(String(r.posted_date || "")) ? String(r.posted_date) : null,
    date_assumed: r.date_assumed === true, date_bad: r.date_bad === true || r.date_status === "future",
    site: MK_SITES.indexOf(r.site) >= 0 ? r.site : "other",
    relevant: r.relevant !== false, why: s(r.why != null ? r.why : r.why_excluded, 160), url: s(r.url, 300),
    source: (source || r.source) === "manual" ? "manual" : "shot",
    orig: {price: n(o.price) > 0 ? n(o.price) : null, relevant: o.relevant !== false}};
}

/* ---------- что ищем: распознанное или введённое сотрудником ---------- */
function recVal(key){
  const r = CH.rec.filter(x => x.key === key && String(x.value || "").trim())[0];
  return r ? String(r.value).trim() : "";
}
function mkQuery(){
  const typed = String(MK.q || "").trim();
  const yo = String(CH.opt.year || ""), yr = recVal("year");
  const year = /^\d{4}$/.test(yo) ? yo : /^\d{4}$/.test(yr) ? yr : "";
  return {brand: typed || recVal("brand"), model: typed ? "" : recVal("model"), year: year, object_kind: CH.kind || ""};
}
/* ссылки поиска — GET /act/market/links на языке интерфейса; тот же запрос второй раз не уходит */
async function mkLinks(force){
  const q = mkQuery();
  if (!q.brand && !q.model && !q.object_kind) {
    if (MK.links || MK.linksKey) { MK.links = null; MK.linksKey = ""; MK.linksErr = ""; mkPart("links"); }
    return;
  }
  const p = {lang: I18N_LANG, brand: q.brand, model: q.model, year: q.year, object_kind: q.object_kind,
    class_code: CH.must.class_code || "", session: String(MK.q || "").trim() ? "" : CH.session || ""};   // свои слова сильнее фото
  const qs = Object.keys(p).filter(k => p[k]).map(k => k + "=" + encodeURIComponent(p[k])).join("&");
  if (!force && qs === MK.linksKey) return;
  MK.linksKey = qs;
  MK.linksBusy = true;
  MK.linksErr = "";
  mkPart("links");
  const r = await api("/act/market/links?" + qs);
  if (qs !== MK.linksKey) return;                       // успели изменить запрос или язык
  MK.linksBusy = false;
  if (!r.ok) {
    MK.links = null;
    MK.linksErr = r.status === 422 ? T("tg.mk.links_none", "По этим словам искать нечего. Напишите марку и модель в поле «Что ищем».")
      : T("tg.mk.links_failed", "Ссылки не загрузились: {reason}. Откройте OLX сами и найдите объект по марке и модели.", {reason: r.error});
    if (r.status === 422) MK.qOpen = true;
  } else {
    const d = r.data || {};
    MK.links = {links: (Array.isArray(d.links) ? d.links : []).filter(l => l && mkSafe(l.url))
        .map(l => ({site: MK_SITES.indexOf(l.site) >= 0 ? l.site : "other", label: String(l.label || ""), url: String(l.url), hint: String(l.hint || "")})),
      tips: (Array.isArray(d.shot_tips) ? d.shot_tips : []).map(String), warning: String(d.warning || ""),
      lang: String(d.lang || I18N_LANG)};
  }
  mkPart("links");
  mkPart("shots");
}
let mkLinksTimer = null;
function mkLinksSoon(){ clearTimeout(mkLinksTimer); mkLinksTimer = setTimeout(() => mkLinks(), 700); }
/* адрес открываем, только если это https на одной из четырёх площадок */
function mkSafe(u){
  let x;
  try { x = new URL(String(u || "")); } catch (e) { return ""; }
  if (x.protocol !== "https:" || x.username || x.password) return "";
  const h = x.hostname.toLowerCase();
  return MK_HOSTS.some(d => h === d || h.slice(-(d.length + 1)) === "." + d) ? x.href : "";
}
function mkOpen(u, site){
  const url = mkSafe(u);
  if (!url) {
    MK.err = T("tg.mk.bad_link", "Эту ссылку приложение не открывает: только https на olx.uz, avtoelon.uz, uybor.uz или joymee.uz.");
    mkPart("shots");
    return;
  }
  if (site && MK_SITES.indexOf(site) >= 0) { MK.site = site; mkSave(); }
  haptic("select");
  if (TG && TG.openLink) tgCall(() => TG.openLink(url));
  else window.open(url, "_blank", "noopener");
}

/* ---------- предпросмотр: тот же расчёт, что act_engine.market_estimate ---------- */
const mkIso = d => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
const mkDmy = iso => iso ? iso.slice(8, 10) + "." + iso.slice(5, 7) + "." + iso.slice(0, 4) : "";
function mkMonthsBefore(iso, m){
  let y = Number(iso.slice(0, 4)), mo = Number(iso.slice(5, 7)) - m;
  while (mo <= 0) { mo += 12; y--; }
  const d = Math.min(Number(iso.slice(8, 10)), new Date(Date.UTC(y, mo, 0)).getUTCDate());
  return y + "-" + String(mo).padStart(2, "0") + "-" + String(d).padStart(2, "0");
}
function mkQuant(xs, p){
  const s = xs.slice().sort((a, b) => a - b);
  if (!s.length) return null;
  if (s.length === 1) return s[0];
  const pos = p * (s.length - 1), lo = Math.floor(pos), hi = Math.min(lo + 1, s.length - 1);
  return s[lo] + (s[hi] - s[lo]) * (pos - lo);
}
function mkRateOk(v){ const x = dec(v); return x != null && isFinite(x) && x >= MK_RATE[0] && x <= MK_RATE[1] ? x : null; }
function mkRateNow(){ return MK.fx && MK.fx.rate > 0 ? MK.fx.rate : mkRateOk(MK.rate); }
/* округление «половина — вверх», как act_engine.round_half_up на сервере */
const mkRound = x => Math.floor(x + 0.5);
function mkEstimate(){
  const R = MK_RULE, rate = mkRateNow(), shot = MK.shotDate || mkIso(new Date());
  const edge = mkMonthsBefore(shot, R.max_age_months);
  const st = MK.listings.map(() => ({used: false, code: "", uzs: null}));
  const cand = [];
  MK.listings.forEach((r, i) => {
    const s = st[i];
    if (r.price != null && r.price > 0) s.uzs = r.currency === "UZS" ? mkRound(r.price) : MK_USD(r.currency) && rate ? mkRound(r.price * rate) : null;
    if (!r.relevant) s.code = "off";
    else if (!(r.price > 0)) s.code = "no_price";
    else if (s.uzs == null) s.code = "no_rate";
    else if (r.date_bad || (r.posted_date && r.posted_date > shot)) s.code = "bad_date";
    else if (!r.posted_date && !R.allow_undated) s.code = "no_date";
    else if (r.posted_date && r.posted_date < edge) s.code = "old";
    else cand.push(i);
  });
  const out = {st: st, count: MK.listings.length, used: 0, median: null, low: null, high: null, med0: null, verdict: "none", rate: rate};
  if (!cand.length) return out;
  const prices = [];
  if (cand.length < R.min_listings) {
    // мало подходящих — выбросы не ищем (как на сервере)
    cand.forEach(i => { st[i].used = true; prices.push(st[i].uzs); });
  } else {
    const med0 = mkQuant(cand.map(i => st[i].uzs), 0.5);
    out.med0 = mkRound(med0);
    cand.forEach(i => {
      const p = st[i].uzs;
      st[i].code = p < R.outlier_low * med0 ? "low" : p > R.outlier_high * med0 ? "high" : "";
      if (!st[i].code) { st[i].used = true; prices.push(p); }
    });
  }
  out.used = prices.length;
  out.median = mkRound(mkQuant(prices, 0.5));
  out.low = mkRound(mkQuant(prices, 0.25));
  out.high = mkRound(mkQuant(prices, 0.75));
  out.verdict = prices.length < R.min_listings ? "few" : "ready";
  return out;
}
/* расхождение — как valuation.compare_declared: от заявленной стоимости, со знаком, до 0,1 */
const mkDiff = (declared, median) => Math.round((declared - median) / declared * 1000) / 10;

/* ---------- разметка карточки ---------- */
function mkHtml(){
  return '<section class="card mk" id="mkCard" aria-labelledby="mkTitle"><div class="mk-top"><h2 id="mkTitle">'
    + esc(T("tg.mk.title", "Оценка по объявлениям")) + '</h2><span class="tag off">' + esc(T("tg.mk.optional", "необязательно")) + "</span></div>"
    + '<p class="sub">' + esc(T("tg.mk.sub", "Сверим стоимость объекта с ценами похожих объявлений. Можно пропустить — акт сформируется и без оценки.")) + "</p>"
    + '<div id="mkLinks">' + mkLinksHtml() + "</div>"
    + '<div id="mkShots">' + mkShotsHtml() + "</div>"
    + '<div id="mkRes">' + mkResHtml() + "</div>"
    + "</section>";
}
function mkPart(name){
  const map = {links: ["mkLinks", mkLinksHtml], shots: ["mkShots", mkShotsHtml], res: ["mkRes", mkResHtml],
    sum: ["mkSum", mkSumHtml], add: ["mkAdd", mkAddHtml]};
  const m = map[name];
  const el = m && document.getElementById(m[0]);
  if (!el) return;
  if (typing(el)) { CH.later = true; return; }
  el.innerHTML = m[1]();
}
function mkLinksHtml(){
  const q = mkQuery(), known = !!(q.brand || q.model);
  const what = [q.brand, q.model, q.year].filter(Boolean).join(" ");
  let h = "";
  if (!known || MK.qOpen) {
    h += '<label class="f" for="mkQ">' + esc(T("tg.mk.q_label", "Что ищем")) + "</label>"
      + '<div class="mk-q"><input id="mkQ" type="text" autocomplete="off" maxlength="60" enterkeyhint="search" data-mkq="1" value="' + esc(MK.q)
      + '" placeholder="' + esc(T("tg.mk.q_ph", "Марка и модель, например XCMG QY50K5D")) + '">'
      + '<button type="button" class="btn btn-secondary btn-sm" data-mk="find">' + esc(T("tg.mk.q_find", "Показать ссылки")) + "</button></div>"
      + '<span class="hint">' + esc(MK.q && recVal("brand")
        ? T("tg.mk.q_hint_typed", "Ищем по вашим словам. Очистите поле — будем искать по распознанному.")
        : known ? T("tg.mk.q_hint_known", "Пусто — ищем по распознанному: {what}.", {what: what})
        : T("tg.mk.q_hint", "Марка и модель не распознаны — напишите их сами.")) + "</span>";
  } else {
    h += '<p class="mk-what"><span>' + esc(T("tg.mk.what", "Ищем:")) + "</span> <b>" + esc(what) + "</b>"
      + '<button type="button" class="btn-link mk-edit" data-mk="qopen">' + esc(T("tg.mk.q_change", "Искать другое")) + "</button></p>";
  }
  if (MK.linksBusy) h += '<p class="note">' + spin(T("tg.mk.links_loading", "Подбираю ссылки поиска…")) + "</p>";
  else if (MK.linksErr) h += '<p class="note warn">' + esc(MK.linksErr) + "</p>";
  const L = MK.links;
  if (L && L.links.length) {
    // главная кнопка — первая ссылка (OLX) с её подсказкой; остальные площадки — второстепенными кнопками ниже
    const btn = (l, i) => '<button type="button" class="' + (i === 0 ? "mk-go" : "mk-alt") + '" data-mkopen="' + i + '">'
      + esc(i === 0 && l.site === "olx" ? T("tg.mk.find_olx", "Найти на OLX") : l.label) + "</button>";
    h += '<div class="mk-links">' + btn(L.links[0], 0) + "</div>"
      + (L.links[0].hint ? '<span class="hint">' + esc(L.links[0].hint) + "</span>" : "")
      + (L.links.length > 1 ? '<div class="mk-links mk-more">' + L.links.slice(1).map((l, i) => btn(l, i + 1)).join("") + "</div>" : "");
  }
  h += '<ol class="mk-how"><li>' + esc(T("tg.mk.how1", "Откройте поиск.")) + "</li>"
    + "<li>" + esc(T("tg.mk.how2", "Сделайте снимок экрана со списком объявлений (5–10 штук, чтобы были видны цены).")) + "</li>"
    + "<li>" + esc(T("tg.mk.how3", "Загрузите снимок сюда.")) + "</li></ol>";
  // подсказка про продавцов уже есть в предупреждении под загрузкой — второй раз не повторяем
  const tips = L ? L.tips.filter(x => !(L.warning && L.warning.indexOf(x) >= 0)) : [];
  if (tips.length) h += '<p class="hint mk-tips">' + esc(T("tg.mk.tips", "На снимке должны быть видны:")) + " " + esc(tips.join("; ")) + "</p>";
  return h;
}
function mkShotsHtml(){
  const dis = MK.busy ? " disabled" : "";
  let h = '<div class="mk-up">'
    + '<button type="button" class="btn btn-secondary btn-sm" data-mk="pick"' + dis + ">" + ICON_IMG + "<span>" + esc(T("tg.mk.pick", "Выбрать снимки")) + "</span></button>"
    + '<button type="button" class="wz-drop mk-drop" id="mkDrop" data-mk="pick"' + dis + ">" + esc(T("tg.mk.drop", "Или перетащите снимок сюда / вставьте Ctrl+V")) + "</button></div>"
    + '<p class="hint">' + esc(T("tg.mk.formats", "Снимки экрана JPG или PNG (WEBP и HEIC переведём в JPG) · до {n} снимков, каждый до {mb} МБ", {n: MK_MAX, mb: CH_MAX_MB})) + "</p>";
  if (MK.queue.length) {
    h += '<div class="wz-files mk-files"><p class="eyebrow">' + esc(T("tg.mk.shots_n", "Снимки · {n}", {n: MK.queue.length})) + "</p>"
      + MK.queue.map(q => '<div class="wz-file' + (q.error ? " bad" : "") + '"><span class="ic" aria-hidden="true">'
        + (q.url ? '<img src="' + esc(q.url) + '" alt="">' : esc(extOf(q.name))) + "</span>"
        + "<span><b>" + esc(q.name) + '</b><small class="' + (q.error ? "err" : "") + '">'
        + esc(sizeName(q.size) + " · " + (q.error || (MK.busy ? T("tg.act.st_reading", "читаю…") : T("tg.mk.st_queued", "уйдёт по кнопке «Прочитать объявления»")))) + "</small></span>"
        + (MK.busy ? '<span class="st"></span>'
          : '<button type="button" class="rm" data-mkrm="' + q.id + '" aria-label="' + esc(T("tg.wz.remove_aria", "Убрать файл {name}", {name: q.name})) + '">' + esc(T("tg.wz.remove", "Убрать")) + "</button>")
        + "</div>").join("") + "</div>";
    const live = MK.queue.filter(q => !q.error).length;
    if (!MK.busy && live) h += '<div class="mk-read"><button type="button" class="mk-go" data-mk="read">' + esc(T("tg.mk.read", "Прочитать объявления: снимков {n}", {n: live})) + "</button></div>";
  }
  if (MK.busy) {
    h += '<div class="wz-reading" role="status"><span class="spin"></span><div><b>' + esc(T("tg.mk.reading", "Читаю объявления…"))
      + "</b><small>" + esc(T("tg.mk.reading_sub", "Модель читает цены со снимков — обычно до 30 секунд. Не закрывайте приложение.")) + "</small></div></div>";
  }
  if (MK.err) h += '<p class="note err" role="alert">' + esc(MK.err) + "</p>";
  h += '<p class="wz-warn">' + esc(MK.links && MK.links.warning && MK.links.lang === I18N_LANG ? MK.links.warning
    : T("tg.mk.warn", "Снимайте только список объявлений: имена и телефоны продавцов не извлекаются; снимки хранятся 24 часа. Снимки уходят в языковую модель как картинки. Сервер тестовый.")) + "</p>";
  return h;
}
function mkHasUsd(){ return MK.listings.some(r => MK_USD(r.currency) && r.price != null); }
function mkFxHtml(){
  if (!mkHasUsd()) return "";
  if (MK.fx) {
    const own = MK.info && MK.info.lang === I18N_LANG && MK.info.fxText;
    return '<p class="note mk-fx">' + esc(own ? MK.info.fxText : T("tg.mk.fx_line", "Курс: 1 доллар = {rate} {sum} на {date}, источник — {src}.",
      {rate: nf(MK.fx.rate, 2), sum: SUM(), date: mkDmy(MK.fx.as_of), src: MK.fx.by === "cbu" ? T("tg.mk.fx_cbu", "ЦБ РУз (cbu.uz)") : T("tg.mk.fx_manual", "курс из настроек оценки")})) + "</p>";
  }
  const bad = MK.rate && !mkRateOk(MK.rate);
  return '<div class="mk-rate' + (bad ? " bad" : "") + '"><label class="f" for="mkRate">' + esc(T("tg.mk.rate", "Курс доллара")) + ' <span class="unit">'
    + esc(T("tg.mk.rate_unit", "{sum} за 1 доллар", {sum: SUM()})) + "</span></label>"
    + '<input id="mkRate" type="text" inputmode="decimal" autocomplete="off" maxlength="12" data-mkrate="1" value="' + esc(MK.rate) + '" placeholder="12650">'
    + '<span class="hint">' + esc(T("tg.mk.rate_hint", "Курс не получен автоматически. Введите курс ЦБ на дату снимка — цены в долларах пересчитаются в сумы. Без курса такие объявления в расчёт не войдут.")) + "</span>"
    + '<span class="ferr" id="mkRateErr">' + esc(bad ? T("tg.mk.rate_err", "Курс — число от {a} до {b} сумов за доллар.", {a: fmt(MK_RATE[0]), b: fmt(MK_RATE[1])}) : "") + "</span></div>";
}
function mkResHtml(){
  const L = MK.listings;
  let h = "";
  if (L.length || MK.ss) {
    h += '<div class="h3">' + esc(T("tg.mk.list_n", "Объявления · {n}", {n: L.length})) + "</div>";
    const info = MK.info && MK.info.lang === I18N_LANG ? MK.info : null;
    if (info && info.message) h += '<p class="note">' + esc(info.message) + "</p>";
    else if (L.some(r => r.source === "shot")) h += '<p class="note">' + esc(T("tg.mk.check", "Проверьте цены и отметки — модель может ошибаться.")) + "</p>";
    if (info) h += info.notes.filter(n => n !== info.fxText).map(n => '<p class="note warn">' + esc(n) + "</p>").join("");
    if (MK.ss && !L.length) h += '<p class="note warn">' + esc(T("tg.mk.empty", "На снимках не нашлось объявлений с ценами. Проверьте, что на снимке виден список, или добавьте объявления вручную.")) + "</p>";
    h += mkFxHtml();
    const e = mkEstimate();
    h += '<div class="mk-rows">' + L.map((r, i) => mkRowHtml(r, i, e)).join("") + "</div>";
  }
  h += '<div id="mkAdd">' + mkAddHtml() + "</div>";
  h += '<span class="ferr" id="wzErr-market">' + esc(CH.errs.market || "") + "</span>";
  h += '<div id="mkSum">' + mkSumHtml() + "</div>";
  return h;
}
function mkEdited(r){ return r.source === "shot" && (r.price !== r.orig.price || r.relevant !== r.orig.relevant); }
function mkTagsHtml(r){
  if (r.source === "manual") return '<span class="tag off">' + esc(T("tg.mk.tag_manual", "вручную")) + "</span>";
  return mkEdited(r) ? '<span class="tag">' + esc(T("tg.mk.tag_edited", "исправлено")) + "</span>" : "";
}
function mkConvText(r, rate){
  if (!MK_USD(r.currency) || r.price == null) return "";
  return rate ? T("tg.mk.conv", "{p} {c} × {rate} = {sum}", {p: fmt(r.price), c: r.currency, rate: nf(rate, 2), sum: money(Math.round(r.price * rate))})
    : T("tg.mk.conv_none", "цена в долларах — нужен курс");
}
function mkStText(r, e, i){
  const s = e.st[i];
  if (s.used) return T("tg.mk.st_used", "в расчёте");
  switch (s.code) {
    case "off":
      return r.orig.relevant === false
        ? T("tg.mk.x_other", "не в расчёте: другое изделие") + (r.why ? " (" + r.why + ")" : "")
        : T("tg.mk.x_off", "не в расчёте: снята отметка «учитывать»");
    case "no_price": return T("tg.mk.x_price", "не в расчёте: нет цены");
    case "no_rate": return T("tg.mk.x_rate", "не в расчёте: цена в долларах, а курс не задан");
    case "no_date": return T("tg.mk.x_date", "не в расчёте: дата публикации не видна");
    case "bad_date": return T("tg.mk.x_bad_date", "не в расчёте: дата публикации позже даты снимков");
    case "old": return T("tg.mk.x_old", "не в расчёте: объявление старше {m} мес.", {m: MK_RULE.max_age_months});
    case "low": return T("tg.mk.x_low", "не в расчёте: выброс — цена ниже {k} медианы {med}", {k: nf(MK_RULE.outlier_low, 1), med: money(e.med0)});
    default: return T("tg.mk.x_high", "не в расчёте: выброс — цена выше {k} медианы {med}", {k: nf(MK_RULE.outlier_high, 0), med: money(e.med0)});
  }
}
function mkRowHtml(r, i, e){
  const used = e.st[i].used;
  const date = r.posted_date ? mkDmy(r.posted_date)
    : MK_RULE.allow_undated ? (r.source === "manual" ? T("tg.mk.date_none", "дата не указана — принята дата снимка") : T("tg.mk.date_assumed", "дата не видна — принята дата снимка"))
    : r.source === "manual" ? T("tg.mk.date_none_x", "дата не указана") : T("tg.mk.date_missing", "дата не видна");
  const meta = [r.year ? T("tg.mk.year", "{y} г.", {y: r.year}) : "",
    r.mileage_km != null ? T("tg.mk.km", "пробег {n} км", {n: fmt(r.mileage_km)}) : "",
    r.hours != null ? T("tg.mk.hours", "{n} моточасов", {n: fmt(r.hours)}) : "",
    r.region || "", date].filter(Boolean).join(" · ");
  const title = r.title || T("tg.mk.no_title", "без названия");
  return '<div class="mk-row' + (used ? "" : " off") + '" id="mkr-' + i + '">'
    + '<p class="mk-t"><b>' + esc(title) + '</b><span id="mkt-' + i + '">' + mkTagsHtml(r) + "</span></p>"
    + '<small class="mk-meta">' + esc(meta) + "</small>"
    + '<div class="mk-ctl"><label class="mk-use"><input type="checkbox" data-mkuse="' + i + '"' + (r.relevant ? " checked" : "")
    + ' aria-label="' + esc(T("tg.mk.use_aria", "Учитывать объявление: {t}", {t: title})) + '"><span>' + esc(T("tg.mk.use", "учитывать")) + "</span></label>"
    + '<div class="mk-price"><input type="text" inputmode="numeric" autocomplete="off" enterkeyhint="done" data-mkprice="' + i + '" value="'
    + esc(r.price != null ? fmt(r.price) : "") + '" placeholder="0" aria-label="' + esc(T("tg.mk.price_aria", "Цена объявления: {t}", {t: title})) + '">'
    + '<span class="unit">' + esc(r.currency === "UZS" ? SUM() : r.currency) + "</span></div>"
    + (r.source === "manual" ? '<button type="button" class="rm mk-del" data-mkdel="' + i + '">' + esc(T("tg.wz.remove", "Убрать")) + "</button>" : "")
    + "</div>"
    + '<small class="mk-conv" id="mkc-' + i + '">' + esc(mkConvText(r, e.rate)) + "</small>"
    + '<small class="mk-why" id="mkw-' + i + '">' + esc(mkStText(r, e, i)) + "</small>"
    + "</div>";
}
function mkSumHtml(est){
  if (!MK.listings.length) return "";
  const e = est || mkEstimate(), decl = Number(CH.must.object_value) || 0;
  if (!e.used) {
    return '<div class="mk-sum none"><p class="mk-line"><b>' + esc(T("tg.mk.sum_none", "Подходящих объявлений нет.")) + "</b></p>"
      + '<p class="note">' + esc(T("tg.mk.sum_none_sub", "Отметьте объявления того же изделия с ценой или добавьте их вручную — без них оценки не будет.")) + "</p></div>";
  }
  let h = '<div class="mk-sum' + (e.verdict === "few" ? " few" : "") + '" role="status"><p class="eyebrow">' + esc(T("tg.mk.sum_title", "По объявлениям · предварительно")) + "</p>"
    + '<p class="mk-med"><span>' + esc(T("tg.mk.median", "медиана")) + "</span><b>" + esc(money(e.median)) + "</b></p>"
    + '<p class="mk-line">' + esc(T("tg.mk.range", "вилка от {lo} до {hi}", {lo: money(e.low), hi: money(e.high)}) + " · "
      + T("tg.mk.used", "учтено {n} из {m}", {n: e.used, m: e.count})) + "</p>";
  if (e.verdict === "few") h += '<p class="note warn">' + esc(T("tg.mk.few", "Объявлений мало (меньше {n}) — оценка ориентировочная.", {n: MK_RULE.min_listings})) + "</p>";
  if (decl > 0) {
    const d = mkDiff(decl, e.median), big = Math.abs(d) > MK_RULE.diff_pct + 1e-9;
    const P = {v: money(decl), p: pct(Math.abs(d), 1), t: pct(MK_RULE.diff_pct, 0)};
    h += '<p class="note ' + (big ? "warn" : "ok") + '">' + esc(big
      ? T("tg.mk.diff_big", "Заявленная стоимость {v} расходится с медианой на {p} — больше порога {t}. Проверьте стоимость или оставьте свою: в акте будет отмечено.", P)
      : T("tg.mk.diff_ok", "Заявленная стоимость {v} отличается от медианы на {p} — в пределах порога {t}.", P)) + "</p>";
    // при «мало объявлений» медиана — только ориентир: заменять ею стоимость нельзя
    if (big && e.verdict !== "few" && mkRound(decl) !== e.median) h += '<button type="button" class="btn btn-secondary btn-sm" data-mk="usemed">' + esc(T("tg.mk.use_median_replace", "Заменить стоимость объекта на медиану")) + "</button>";
    if (MK.declOrig > 0 && MK.declOrig !== decl) h += '<p class="hint">' + esc(T("tg.mk.decl_orig", "Заявлено клиентом: {v} — в акте будет видно, что стоимость принята по объявлениям.", {v: money(MK.declOrig)})) + "</p>";
  } else if (e.verdict !== "few") {
    h += '<button type="button" class="btn btn-secondary btn-sm" data-mk="usemed">' + esc(T("tg.mk.use_median", "Подставить медиану в стоимость объекта")) + "</button>";
  }
  h += '<p class="hint">' + esc(T("tg.mk.uncal", "Пороги (не меньше {n} объявлений, выбросы, срок {m} мес., расхождение {t}) — экспертные, не калиброваны. Окончательно оценку посчитает сервер в акте.",
    {n: MK_RULE.min_listings, m: MK_RULE.max_age_months, t: pct(MK_RULE.diff_pct, 0)})) + "</p>";
  return h + "</div>";
}
function mkAddHtml(){
  if (!MK.addOpen) {
    return MK.listings.length >= MK_MAX_LISTINGS ? ""
      : '<button type="button" class="btn-link mk-addbtn" data-mk="addopen">' + esc(T("tg.mk.add", "Добавить объявление вручную")) + "</button>";
  }
  const a = MK.add;
  return '<div class="mk-addf"><div class="h3">' + esc(T("tg.mk.add_title", "Объявление вручную")) + "</div>"
    + '<label class="f" for="mka-title">' + esc(T("tg.mk.f_title", "Название")) + "</label>"
    + '<input id="mka-title" type="text" autocomplete="off" maxlength="160" data-mka="title" value="' + esc(a.title) + '">'
    + '<div class="mk-add2"><div><label class="f" for="mka-price">' + esc(T("tg.mk.f_price", "Цена")) + "</label>"
    + '<input id="mka-price" type="text" inputmode="numeric" autocomplete="off" data-mka="price" value="' + esc(a.price) + '" placeholder="0"></div>'
    + '<div><label class="f" for="mka-cur">' + esc(T("tg.mk.f_currency", "Валюта")) + "</label>"
    + '<select id="mka-cur" data-mka="currency"><option value="UZS"' + (a.currency === "UZS" ? " selected" : "") + ">" + esc(SUM()) + "</option>"
    + '<option value="USD"' + (a.currency === "USD" ? " selected" : "") + ">USD</option></select></div>"
    + '<div><label class="f" for="mka-year">' + esc(T("tg.mk.f_year", "Год")) + "</label>"
    + '<input id="mka-year" type="text" inputmode="numeric" autocomplete="off" maxlength="4" data-mka="year" value="' + esc(a.year) + '"></div></div>'
    + '<label class="f" for="mka-date">' + esc(T("tg.mk.f_date", "Дата публикации")) + "</label>"
    + '<input id="mka-date" type="date" autocomplete="off" data-mka="date" max="' + esc(mkIso(new Date())) + '" value="' + esc(a.date) + '">'
    + '<span class="hint">' + esc(T("tg.mk.f_date_hint", "Как в объявлении. Без даты объявление в расчёт не войдёт.")) + "</span>"
    + '<label class="f" for="mka-url">' + esc(T("tg.mk.f_url", "Ссылка на объявление")) + "</label>"
    + '<input id="mka-url" type="url" inputmode="url" autocomplete="off" maxlength="300" data-mka="url" value="' + esc(a.url) + '" placeholder="https://www.olx.uz/">'
    + '<span class="hint">' + esc(T("tg.mk.f_url_hint", "Необязательно. Только https на olx.uz, avtoelon.uz, uybor.uz или joymee.uz.")) + "</span>"
    + '<span class="ferr" id="mkAddErr">' + esc(MK.addErr) + "</span>"
    + '<div class="mk-addbtns"><button type="button" class="btn btn-secondary btn-sm" data-mk="addsave">' + esc(T("tg.mk.add_save", "Добавить в список")) + "</button>"
    + '<button type="button" class="btn-link" data-mk="addcancel">' + esc(T("tg.mk.add_cancel", "Отмена")) + "</button></div></div>";
}

/* ---------- правки: форма не перерисовывается, обновляются статусы строк и итог ---------- */
function mkRefresh(){
  const e = mkEstimate();
  MK.listings.forEach((r, i) => {
    const row = document.getElementById("mkr-" + i);
    if (!row) return;
    row.classList.toggle("off", !e.st[i].used);
    const w = document.getElementById("mkw-" + i); if (w) w.textContent = mkStText(r, e, i);
    const c = document.getElementById("mkc-" + i); if (c) c.textContent = mkConvText(r, e.rate);
    const t = document.getElementById("mkt-" + i); if (t) t.innerHTML = mkTagsHtml(r);
  });
  const s = document.getElementById("mkSum");
  if (s) s.innerHTML = mkSumHtml(e);
  wzClearErr("market");
}
function mkUse(el){
  const r = MK.listings[Number(el.dataset.mkuse)];
  if (!r) return;
  r.relevant = !!el.checked;
  haptic("select");
  mkRefresh();
  mkSave();
}
function mkPriceInput(el){
  const r = MK.listings[Number(el.dataset.mkprice)];
  if (!r) return;
  groupDigitsLive(el);
  r.price = num(el.value) > 0 ? num(el.value) : null;
  mkRefresh();
  mkSave();
}
function mkRateInput(el){
  const c = el.value.replace(/[^\d.,\s]/g, "");
  if (c !== el.value) el.value = c;
  MK.rate = c.trim();
  const bad = MK.rate && !mkRateOk(MK.rate);
  const err = document.getElementById("mkRateErr");
  if (err) err.textContent = bad ? T("tg.mk.rate_err", "Курс — число от {a} до {b} сумов за доллар.", {a: fmt(MK_RATE[0]), b: fmt(MK_RATE[1])}) : "";
  if (el.parentNode) el.parentNode.classList.toggle("bad", !!bad);
  mkRefresh();
  mkSave();
}
function mkAddInput(el){
  const k = el.dataset.mka;
  if (k === "price") groupDigitsLive(el);
  if (k === "year" && /\D/.test(el.value)) el.value = el.value.replace(/\D/g, "");
  MK.add[k] = String(el.value || "");
  if (MK.addErr) { MK.addErr = ""; const e = document.getElementById("mkAddErr"); if (e) e.textContent = ""; }
}
function mkAddSave(){
  const a = MK.add, y = new Date().getFullYear() + 1;
  const title = String(a.title || "").trim(), price = num(a.price), url = String(a.url || "").trim();
  let err = "";
  if (!title) err = T("tg.mk.err_title", "Напишите название объявления.");
  else if (!(price > 0)) err = T("tg.mk.err_price", "Введите цену больше нуля.");
  else if (a.year && !(/^\d{4}$/.test(a.year) && Number(a.year) >= 1950 && Number(a.year) <= y)) err = T("tg.mk.err_year", "Год — четыре цифры, от 1950 до {y}.", {y: y});
  else if (url && !mkSafe(url)) err = T("tg.mk.err_url", "Ссылка — только https на olx.uz, avtoelon.uz, uybor.uz или joymee.uz. Или оставьте поле пустым.");
  else if (a.date && !(/^\d{4}-\d{2}-\d{2}$/.test(a.date) && a.date >= "2000-01-01" && a.date <= mkIso(new Date()))) err = T("tg.mk.err_date", "Дата публикации — не позже сегодняшнего дня.");
  if (err) { MK.addErr = err; haptic("error"); mkPart("add"); return; }
  MK.listings.push(mkRow({id: "m" + (++MK.mseq), title: title, price: price, currency: a.currency === "USD" ? "USD" : "UZS",
    year: a.year ? Number(a.year) : null, posted_date: a.date || null, url: url ? mkSafe(url) : null, relevant: true}, "manual"));
  MK.addOpen = false;
  MK.add = MK_ADD0();
  MK.addErr = "";
  haptic("success");
  mkSave();
  mkPart("res");
}
function mkDel(i){
  const r = MK.listings[i];
  if (!r || r.source !== "manual") return;
  MK.listings.splice(i, 1);
  haptic("select");
  mkSave();
  mkPart("res");
}
/* «Подставить медиану»: поле стоимости меняется только по нажатию */
function mkUseMedian(){
  const e = mkEstimate();
  if (!e.median || e.verdict === "few") return;
  // первая замена запоминает то, что заявил клиент; повторная — не затирает его
  const decl = Number(CH.must.object_value) || 0;
  if (decl > 0 && !(MK.declOrig > 0)) MK.declOrig = decl;
  mkSave();
  CH.must.object_value = e.median;
  const el = document.getElementById("wzf-object_value");
  if (el) { el.value = Number(e.median).toLocaleString(LOC()); el.classList.add("flash"); }
  preClear("object_value");
  wzClearErr("object_value");
  haptic("select");
  wzSave();
  mkPart("sum");
}

/* ---------- снимки: галерея, перетаскивание, вставка из буфера; WEBP и HEIC — в JPG ---------- */
async function mkAddFiles(files){
  const list = Array.prototype.slice.call(files || []);
  if (!list.length || MK.busy) return;
  const bad = [];
  let over = false;
  for (let f of list) {
    if (wzNeedsConvert(f)) {
      const j = await wzToJpeg(f);
      if (!j) { bad.push(T("tg.wz.bad_image", "{name}: не удалось открыть картинку — сохраните её как JPG или PNG.", {name: f.name || "?"})); continue; }
      f = j;
    }
    const name = String(f.name || ""), type = String(f.type || "");
    if (!(/^image\/(jpeg|png)$/.test(type) || /\.(jpe?g|png)$/i.test(name))) {
      bad.push(T("tg.mk.bad_format", "{name}: нужен снимок экрана — JPG или PNG.", {name: name || "?"}));
      continue;
    }
    if (f.size > CH_MAX_MB * 1048576) { bad.push(T("tg.wz.too_big", "{name}: файл больше {mb} МБ — сожмите его или разделите.", {name: name, mb: CH_MAX_MB})); continue; }
    if (MK.queue.filter(q => !q.error).length >= MK_MAX) { over = true; continue; }
    MK.queue.push({id: ++MK.seq, file: f, size: f.size, error: "",
      name: name || ("shot-" + MK.seq + (type === "image/png" ? ".png" : ".jpg")),
      url: window.URL && URL.createObjectURL ? URL.createObjectURL(f) : ""});
  }
  if (over) bad.push(T("tg.mk.too_many", "За один раз — не больше {n} снимков. Уберите лишние, чтобы добавить другие.", {n: MK_MAX}));
  MK.err = bad.join(" ");
  haptic(bad.length ? "warning" : "select");
  mkPart("shots");
  const c = document.getElementById("mkShots");
  if (c && c.scrollIntoView && !bad.length) c.scrollIntoView({block: "nearest"});
}
function mkRemove(id){
  const q = MK.queue.filter(x => x.id === id)[0];
  if (!q || MK.busy) return;
  if (q.url) URL.revokeObjectURL(q.url);
  MK.queue = MK.queue.filter(x => x !== q);
  MK.err = "";
  haptic("select");
  mkPart("shots");
}
/* все снимки из списка — одним POST /act/market/shots; новый ответ заменяет объявления со снимков,
   добавленные вручную остаются */
async function mkRead(){
  const list = MK.queue.filter(q => !q.error).slice(0, MK_MAX);
  if (!list.length || MK.busy || CH.busy) return;
  const q = mkQuery();
  const fd = new FormData();
  list.forEach(x => fd.append("files", x.file, x.name));
  fd.append("lang", I18N_LANG);
  if (CH.session && !String(MK.q || "").trim()) fd.append("session", CH.session);
  fd.append("site", MK.site || "olx");
  ["brand", "model", "year", "object_kind"].forEach(k => { if (q[k]) fd.append(k, q[k]); });
  const rate = mkRateOk(MK.rate);
  if (rate) fd.append("usd_rate", String(rate));
  MK.busy = true;
  MK.err = "";
  tgBusy(true);
  mkPart("shots");
  wzBarPaint();
  const r = await api("/act/market/shots", {method: "POST", body: fd});
  MK.busy = false;
  tgBusy(false);
  if (!r.ok) {
    const j = r.data || {};
    (Array.isArray(j.rejected) ? j.rejected : []).forEach(x => { const it = byIndex(list, x); if (it) it.error = String(x.error || T("tg.wz.not_taken", "не принят")); });
    const reason = r.status === 422 && j.errors && typeof j.errors === "object"
      ? T("tg.mk.err_query", "сервер не принял запрос — проверьте поле «Что ищем» и курс доллара")
      : typeof j.detail === "string" && j.detail ? j.detail : r.error;
    MK.err = T("tg.mk.read_failed", "Объявления не прочитаны: {reason}", {reason: reason});
    haptic("error");
    mkPart("shots");
    wzBarPaint();
    return;
  }
  mkApply(r.data || {}, list);
  haptic("success");
  wzBarPaint();
}
function mkApply(d, list){
  (Array.isArray(d.rejected) ? d.rejected : []).forEach(x => { const it = byIndex(list, x); if (it) it.error = String(x.error || T("tg.wz.not_taken", "не принят")); });
  // прочитанные снимки из списка убираем; не принятые остаются с причиной
  MK.queue.forEach(q => { if (!q.error && q.url) URL.revokeObjectURL(q.url); });
  MK.queue = MK.queue.filter(q => q.error);
  MK.ss = /^[0-9a-f]{8,40}$/.test(String(d.shots_session || "")) ? String(d.shots_session) : "";
  MK.shotDate = /^\d{4}-\d{2}-\d{2}$/.test(String(d.shot_date || "")) ? d.shot_date : mkIso(new Date());
  if (MK_SITES.indexOf(d.site) >= 0) MK.site = d.site;
  const fx = d.fx && typeof d.fx === "object" ? d.fx : null;
  // курс сотрудника остаётся в его поле; из источника проекта — ЦБ РУз или ручной курс настроек
  MK.fx = fx && Number(fx.rate) > 0 && (fx.by === "cbu" || fx.by === "manual_setting")
    ? {rate: Number(fx.rate), by: String(fx.by), as_of: String(fx.as_of || MK.shotDate)} : null;
  MK.need = !!d.usd_rate_needed;
  MK.info = {lang: String(d.lang || I18N_LANG), message: String(d.message || ""),
    notes: (Array.isArray(d.notes) ? d.notes : []).map(String), fxText: fx && fx.text ? String(fx.text) : ""};
  const manual = MK.listings.filter(r => r.source === "manual");
  MK.listings = (Array.isArray(d.listings) ? d.listings : []).filter(r => r && typeof r === "object")
    .slice(0, Math.max(0, MK_MAX_LISTINGS - manual.length)).map(r => mkRow(r, "shot")).concat(manual);
  mkSave();
  mkPart("shots");
  mkPart("res");
}
/* в POST /act/make: объявления с правками, номер загрузки снимков и курс сотрудника */
function mkBody(){
  if (!MK.listings.length) return null;
  const keep = (o, k, v) => { if (v != null && v !== "") o[k] = v; };
  const out = {listings: MK.listings.map(r => {
    const o = {id: r.id, relevant: !!r.relevant};
    keep(o, "title", r.title);
    keep(o, "price", r.price);
    keep(o, "currency", r.price != null ? r.currency : null);
    keep(o, "year", r.year);
    keep(o, "mileage_km", r.mileage_km);
    keep(o, "hours", r.hours);
    keep(o, "why_excluded", r.relevant ? null : r.why);
    keep(o, "url", r.url && mkSafe(r.url) ? r.url : null);
    // дата, площадка и происхождение: сервер берёт их отсюда, только если загрузка снимков уже недоступна
    keep(o, "posted_date", r.posted_date);
    o.date_assumed = !r.posted_date;
    keep(o, "site", MK_SITES.indexOf(r.site) >= 0 ? r.site : null);
    o.source = r.source === "manual" ? "manual" : "shot";
    return o;
  })};
  // курс и его источник: полученный от сервера (ЦБ или настройки) — как есть; «введён сотрудником» — только свой
  if (MK.fx && MK.fx.rate > 0 && (MK.fx.by === "cbu" || MK.fx.by === "manual_setting")) {
    out.fx = {rate: MK.fx.rate, by: MK.fx.by, as_of: /^\d{4}-\d{2}-\d{2}$/.test(MK.fx.as_of || "") ? MK.fx.as_of : null};
  }
  else {
    const own = mkRateOk(MK.rate);
    if (own) { out.fx = {rate: own, by: "employee", as_of: mkIso(new Date())}; out.usd_rate = own; }
  }
  if (MK.ss) out.shots_session = MK.ss;
  return out;
}
function mkAction(k){
  if (k === "pick") { if (!MK.busy) $("#mkFile").click(); return; }
  if (k === "read") { mkRead(); return; }
  if (k === "find") { MK.qOpen = !!String(MK.q || "").trim() || MK.qOpen; mkLinks(true); return; }
  if (k === "qopen") { MK.qOpen = true; mkPart("links"); const q = $("#mkQ"); if (q) q.focus(); return; }
  if (k === "addopen") { MK.addOpen = true; MK.addErr = ""; mkPart("add"); const t = $("#mka-title"); if (t) t.focus(); return; }
  if (k === "addcancel") { MK.addOpen = false; MK.add = MK_ADD0(); MK.addErr = ""; mkPart("add"); return; }
  if (k === "addsave") { mkAddSave(); return; }
  if (k === "usemed") { mkUseMedian(); return; }
}

/* ---------- шаг «Акт»: плитка оценки по объявлениям и плашка источника ---------- */
function mvVerdictName(v){
  switch (v) {
    case "confirmed": return T("tg.mk.v.confirmed", "стоимость подтверждается");
    case "refine": return T("tg.mk.v.refine", "стоимость нужно уточнить");
    case "few": return T("tg.mk.v.few", "объявлений мало — оценка ориентировочная");
    default: return T("tg.mk.v.none", "подходящих объявлений нет");
  }
}
function actMvHtml(a){
  const mv = a.market_value;
  if (!mv || typeof mv !== "object" || !(mv.available || Number(mv.count) > 0)) return "";
  const v = mv.verdict, tone = v === "confirmed" ? "ok" : v === "refine" || v === "few" ? "warn" : "";
  let h = '<div class="mv-box ' + tone + '"><p class="fr-h"><b>' + esc(T("tg.mk.title", "Оценка по объявлениям")) + ":</b> " + esc(mvVerdictName(v)) + "</p>";
  if (mv.available && isNum(mv.median)) {
    h += '<p class="mk-med"><span>' + esc(T("tg.mk.median", "медиана")) + "</span><b>" + esc(money(mv.median)) + "</b><em>" + esc(T("tg.mk.uncal_short", "не калибровано")) + "</em></p>"
      + '<p class="mk-line">' + esc(T("tg.mk.range", "вилка от {lo} до {hi}", {lo: money(mv.low), hi: money(mv.high)}) + " · "
        + T("tg.mk.used", "учтено {n} из {m}", {n: Number(mv.used) || 0, m: Number(mv.count) || 0})) + "</p>";
    if (isNum(mv.diff_pct) && isNum(mv.declared)) {
      h += '<p class="mk-line' + (v === "refine" ? " warn" : "") + '">' + esc(T("tg.mk.diff_line", "Расхождение с заявленной стоимостью {v}: {p}", {v: money(mv.declared), p: pct(Math.abs(Number(mv.diff_pct)), 1)})) + "</p>";
    }
    if (v === "refine" && isNum(mv.refined_value)) {
      h += '<p class="mk-line"><b>' + esc(T("tg.mk.refined", "Уточнённая стоимость: {v}", {v: money(mv.refined_value)})) + "</b></p>";
      const ic = mv.insured_check;
      if (ic && typeof ic === "object" && isNum(ic.ratio_pct)) {
        h += '<p class="mk-line' + (ic.verdict === "normal" ? "" : " warn") + '">' + esc(T("tg.mk.insured", "Страховая сумма к уточнённой стоимости: {p}", {p: pct(ic.ratio_pct, dp(ic.ratio_pct))}))
          + (ic.text ? ". " + esc(String(ic.text)) : "") + "</p>";
      }
    }
    if (v === "few") h += '<p class="fr-sub">' + esc(T("tg.mk.few_act", "Объявлений в расчёте меньше {n} — медиана только ориентир, стоимость ею не подтверждается.", {n: Number((mv.rule || {}).min_listings) || MK_RULE.min_listings})) + "</p>";
  } else {
    h += '<p class="fr-sub">' + esc(T("tg.mk.none_act", "Ни одно объявление не подошло для расчёта — стоимость по объявлениям не проверена.")) + "</p>";
  }
  const links = Array.isArray(mv.links) ? mv.links : [];
  const i0 = links.findIndex(l => l && mkSafe(l.url));
  const l0 = i0 >= 0 ? links[i0] : null;
  h += '<div class="srcbar"><span>' + esc(mv.source_label || T("tg.mk.src_fallback", "Источник: объявления на площадках, снимки загружены сотрудником.")) + "</span>"
    + (l0 ? '<button type="button" class="btn-link mv-open" data-mvopen="' + i0 + '">'
      + esc(l0.site === "olx" ? T("tg.mk.open_olx", "Открыть поиск на OLX") : T("tg.mk.open_site", "Открыть поиск: {site}", {site: String(l0.label || "")})) + "</button>" : "")
    + "</div>";
  return h + "</div>";
}

/* =====================================================================================
   Несколько объектов в одном акте — парк ТС (02.10.2026; сервер — app/act.py: validate_objects, _apply_objects,
   _objects_view). Шаг «Проверить»: переключатель «Один объект / Несколько объектов» (только у продукта одного
   класса). У объекта: название (label, обязательно), сумма, стоимость, год, пробег, часть номера (plate_hint, до
   6 знаков), состояние, вид и поля класса (те же поля шаблона, что у одного объекта), фото из загрузки шага «Фото»
   (id «f1»…), запрошенная ставка. Сумма и стоимость договора — сумма по объектам: поля только для чтения, в запрос
   уходят равными сумме. Отправка — optional.objects. Шаг «Акт»: итоги (objects_total), таблица (objects[]),
   свёрнутые блоки по объектам. Цифры считает сервер; ставки и пороги экспертные (calibrated = 0) — так и помечено.
   ===================================================================================== */
const OB_MAX = 50;                                    // app/act.py: MAX_OBJECTS
const OB_COND = ["new", "good", "worn", "damaged"];   // app/act.py: CONDITIONS
const obItem = () => ({label: "", sum: null, value: null, year: "", mileage: "", plate: "", cond: "", kind: "", cf: {}, f: {},
  photos: [], rate: "", pf: {}});
const obSum = key => ((CH.ob && CH.ob.items) || []).reduce((a, x) => a + (Number(x[key]) || 0), 0);
function obCondName(c){
  switch (c) {
    case "new": return T("tg.act.obj_cond_new", "новое");
    case "good": return T("tg.act.obj_cond_good", "хорошее");
    case "worn": return T("tg.act.obj_cond_worn", "изношенное");
    default: return T("tg.act.obj_cond_damaged", "повреждённое");
  }
}
function obLoad(x){
  const o = sObj;
  const str = (v, n) => typeof v === "string" ? v.slice(0, n) : v == null ? "" : String(v).slice(0, n);
  if (!x || typeof x !== "object" || !Array.isArray(x.items)) return null;
  const items = x.items.filter(it => it && typeof it === "object").slice(0, OB_MAX).map(it => Object.assign(obItem(), {
    label: str(it.label, 120), sum: Number(it.sum) > 0 ? Number(it.sum) : null, value: Number(it.value) > 0 ? Number(it.value) : null,
    year: str(it.year, 4), mileage: str(it.mileage, 7), plate: str(it.plate, 8), cond: OB_COND.indexOf(it.cond) >= 0 ? it.cond : "",
    kind: /^[a-z_]{1,40}$/.test(String(it.kind || "")) ? it.kind : "", cf: o(it.cf), f: o(it.f),
    photos: Array.isArray(it.photos) ? it.photos.filter(p => /^f\d{1,2}$/.test(String(p))).slice(0, 10) : [],
    rate: str(it.rate, 8), pf: pfLoadMap(it.pf)}));
  return {on: !!x.on && items.length > 0, items: items};
}
/* первый объект — из того, что уже введено для одного объекта: суммы, год, вид, поля класса, марка и модель, все фото */
function obSeed(){
  const it = obItem(), rv = k => anStr((CH.rec.filter(r => r.key === k)[0] || {}).value).trim();
  if (Number(CH.must.sum_insured) > 0) it.sum = Number(CH.must.sum_insured);
  if (Number(CH.must.object_value) > 0) it.value = Number(CH.must.object_value);
  if (CH.opt.year) it.year = String(CH.opt.year);
  if (CH.opt.object_kind) it.kind = String(CH.opt.object_kind);
  if (CH.opt.cf && typeof CH.opt.cf === "object") it.cf = Object.assign({}, CH.opt.cf);
  const label = [rv("brand"), rv("model")].filter(Boolean).join(" ");
  it.label = label.slice(0, 120);
  it.photos = CH.files.map(f => f.id);
  // подсказки автозаполнения переезжают вместе со значениями
  Object.keys(CH.pfM || {}).forEach(k => { it.pf[k] = CH.pfM[k]; });
  return it;
}
function obModeHtml(){
  if (!wzClass()) return "";
  if (!obOk()) {
    return CH.ob && CH.ob.on ? '<p class="hint">' + esc(T("tg.act.obj_multi_off", "У продукта несколько классов: несколько объектов в одном акте не оформить — выберите продукт одного класса.")) + "</p>" : "";
  }
  const on = obOn(), lbl = T("tg.act.obj_mode", "Объекты в акте");
  return '<div class="ob-mode"><label class="f">' + esc(lbl) + "</label>"
    + '<div class="segsel" role="group" aria-label="' + esc(lbl) + '">'
    + '<button type="button" data-obmode="one" aria-pressed="' + !on + '">' + esc(T("tg.act.obj_one", "Один объект")) + "</button>"
    + '<button type="button" data-obmode="many" aria-pressed="' + on + '">' + esc(T("tg.act.obj_many", "Несколько объектов")) + "</button></div>"
    + '<span class="hint">' + esc(on ? T("tg.act.obj_many_hint", "У каждого объекта — свой уровень риска, ставка и премия; премия договора — сумма премий.")
      : T("tg.act.obj_one_hint", "Парк машин или техники — выберите «Несколько объектов»: до 50 в одном акте.")) + "</span></div>";
}
function obMode(v){
  if (!CH.ob) CH.ob = {on: false, items: []};
  CH.ob.on = v === "many";
  if (CH.ob.on) {
    if (!CH.ob.items.length) CH.ob.items.push(obSeed());
    CH.pt = null;                              // части комплексного продукта и парк вместе сервер не принимает
    CH.ptAdd = false;
    obSync();
    pfApply();
  }
  CH.obBad = {};
  wzClearErr("ob");
  haptic("select");
  wzSave();
  ["obmode", "sums", "obj", "parts", "tpl", "more"].forEach(wzPart);
  wzBarPaint();
}
/* суммы договора = суммы по объектам: в CH.must, в полях «только чтение», в итоге карточки и у франшизы */
function obSync(){
  if (!obOn()) return;
  const S = obSum("sum"), V = obSum("value");
  if (S > 0) CH.must.sum_insured = S; else delete CH.must.sum_insured;
  if (V > 0) CH.must.object_value = V; else delete CH.must.object_value;
  ["sum_insured", "object_value"].forEach(k => {
    delete CH.pre[k];
    const el = $("#wzf-" + k);
    if (el) el.value = CH.must[k] ? Number(CH.must[k]).toLocaleString(LOC()) : "";
    wzClearErr(k);
  });
  const t = $("#obTot");
  if (t) t.innerHTML = obTotHtml();
  const eq = $("#wzFrEq");
  if (eq) eq.textContent = frEqText();
}
function obMoneyRoHtml(key){
  const v = CH.must[key];
  const label = key === "sum_insured" ? T("tg.an.f.sum_insured", "Страховая сумма") : T("tg.an.f.object_value", "Стоимость объекта");
  return '<div><label class="f" for="wzf-' + key + '">' + esc(label) + ' <span class="unit">' + esc(SUM()) + "</span></label>"
    + '<input id="wzf-' + key + '" type="text" readonly aria-readonly="true" value="' + esc(Number(v) > 0 ? Number(v).toLocaleString(LOC()) : "") + '" placeholder="0">'
    + '<span class="hint">' + esc(T("tg.act.obj_sum_ro", "Сумма по объектам — меняется в карточках объектов ниже.")) + "</span>"
    + wzErrHtml(key) + "</div>";
}
function obTotHtml(){
  const n = (CH.ob && CH.ob.items.length) || 0, S = obSum("sum"), V = obSum("value");
  return '<p class="pt-tot ok" role="status"><b>' + esc(T("tg.act.obj_total", "Объектов: {n} · сумма {s} · стоимость {v}", {n: n, s: money(S), v: money(V)})) + "</b>"
    + (S > 0 && V > 0 ? "<span>" + esc(T("tg.act.obj_total_ratio", "Сумма к стоимости по договору: {p}", {p: pct(S / V * 100, 1)})) + "</span>" : "") + "</p>";
}
/* поля класса объекта: вид объекта отдельно, только поля class_fields (уточнения договора — в «Дополнительно») */
const obTfList = fs => tfList(fs, true).filter(f => tfTarget(f).t === "cf");
function obFileName(f){
  const nm = String(f.name || "");
  return nm ? (nm.length > 22 ? nm.slice(0, 20) + "…" : nm) + (f.view ? " · " + viewName(f.view) : "")
    : T("tg.act.obj_file", "файл {n}", {n: f.n || f.id}) + (f.view ? " · " + viewName(f.view) : "");
}
const pfCharLabel = k => k === "engine_power" ? T("tg.act.f.engine_power", "Мощность двигателя") : k === "max_mass" ? T("tg.act.prefill_ch_max_mass", "Разрешённая максимальная масса")
  : k === "engine_cc" ? T("tg.act.prefill_ch_engine_cc", "Объём двигателя") : k === "seats" ? T("tg.act.prefill_ch_seats", "Число мест") : k;
const pfCharsText = v => Object.keys(anObj(v)).map(k => pfCharLabel(k) + ": " + anStr(v[k])).filter(x => !/: $/.test(x)).join("; ");
function obRowHtml(it, i, cls, x, t){
  const s = "o" + i, n = i + 1, bad = k => CH.obBad[i + "." + k] || "";
  const fld = (k, label, input, o) => {
    o = o || {};
    return '<div class="' + [o.full ? "full" : "", bad(k) ? "bad" : "", o.need ? "pf-need" : ""].filter(Boolean).join(" ") + '"><label class="f" for="ob-' + k + "-" + i + '">'
      + esc(label) + (o.unit ? ' <span class="unit">' + esc(o.unit) + "</span>" : "") + (o.pf ? pfMarkHtml(s, o.pf) : "") + "</label>" + input
      + (o.hint ? '<span class="hint">' + esc(o.hint) + "</span>" : "") + '<span class="ferr" id="obErr-' + i + "-" + k + '">' + esc(bad(k)) + "</span></div>";
  };
  const inp = (k, val, attrs) => '<input id="ob-' + k + "-" + i + '" type="text" autocomplete="off" enterkeyhint="done" data-ob="' + i + '" data-obk="' + k + '"'
    + attrs + ' value="' + esc(val) + '">';
  const mon = v => Number(v) > 0 ? Number(v).toLocaleString(LOC()) : "";
  const pfl = pfForObject(i), needMil = pfl.length > 0 && !String(it.mileage || "").trim();
  let h = '<div class="pt-row ob-row" data-obi="' + i + '"><div class="pt-h"><p><b>' + esc(T("tg.act.obj_n", "Объект {n}", {n: n})) + '</b> <span id="obH-' + i + '">'
    + esc(it.label) + "</span></p>"
    + (CH.ob.items.length > 1 ? '<button type="button" class="btn-link pt-del" data-obdel="' + i + '" aria-label="' + esc(T("tg.act.obj_del_aria", "Удалить объект {n}", {n: n})) + '">'
      + esc(T("tg.act.obj_del", "удалить")) + "</button>" : "") + "</div>"
    + '<div class="fgrid pt-g">'
    + fld("label", T("tg.act.obj_label", "Название объекта"), inp("label", it.label, ' maxlength="120" placeholder="' + esc(T("tg.act.obj_label_ph", "например: Isuzu NPR, самосвал")) + '"'), {full: true, pf: "label"})
    + fld("sum", T("tg.an.f.sum_insured", "Страховая сумма"), inp("sum", mon(it.sum), ' inputmode="numeric" placeholder="0"'), {unit: SUM()})
    + fld("value", T("tg.an.f.object_value", "Стоимость объекта"), inp("value", mon(it.value), ' inputmode="numeric" placeholder="0"'), {unit: SUM()})
    + fld("year", T("tg.act.f.year", "Год выпуска"), inp("year", it.year, ' inputmode="numeric" maxlength="4"'), {pf: "year"})
    + fld("mileage", T("tg.act.obj_mileage", "Пробег, км"), inp("mileage", it.mileage, ' inputmode="numeric" maxlength="7"'),
      {need: needMil, hint: needMil ? T("tg.act.prefill_mileage", "Введите пробег: в техпаспорте его нет, с фото он не берётся.") : ""})
    + fld("plate", T("tg.act.obj_plate", "Часть номера"), inp("plate", it.plate, ' maxlength="8" placeholder="…123"'),
      {hint: T("tg.act.obj_plate_hint", "Не больше 6 знаков, например «…123». Полный госномер в акт не берётся.")})
    + fld("cond", T("tg.act.obj_cond", "Состояние"), '<select id="ob-cond-' + i + '" data-ob="' + i + '" data-obk="cond"><option value="">' + esc(T("tg.act.opt_unset", "не указано")) + "</option>"
      + OB_COND.map(c => '<option value="' + c + '"' + (c === it.cond ? " selected" : "") + ">" + esc(obCondName(c)) + "</option>").join("") + "</select>")
    + fld("rate", T("tg.act.obj_rate", "Запрошенная ставка"), inp("rate", it.rate, ' inputmode="decimal" maxlength="8" placeholder="' + esc(T("tg.act.obj_rate_ph", "не задана")) + '"'), {unit: "%"});
  if (!x) h += '<div class="full"><p class="note">' + spin(T("tg.tpl.loading", "загружаю поля класса…")) + "</p></div>";
  else if (!t) h += '<div class="full"><p class="note err">' + esc(T("tg.act.obj_tpl_failed", "Поля класса не загрузились — объект посчитается без них.")) + "</p></div>";
  else h += tfHtml(tfKindField(cls), s, cls) + obTfList(t.template.must).map(f => tfHtml(f, s, cls)).join("");
  h += "</div>";
  if (t) {
    const more = obTfList(t.template.optional);
    // подгруппа и топливо подставлены автоматически — блок раскрыт, чтобы «!» было видно
    const open = CH.obMore[i] != null ? !!CH.obMore[i] : Object.keys(it.pf || {}).some(k => k.indexOf("cf.") === 0);
    if (more.length) {
      h += '<details class="pt-more" data-obmore="' + i + '"' + (open ? " open" : "") + "><summary>" + esc(T("tg.pt.more", "Ещё поля класса · {n}", {n: more.length}))
        + '</summary><div class="fgrid pt-g">' + tfFactorSplit(more, f => tfHtml(f, s, cls)) + "</div></details>";
    }
  }
  const ch = pfl.filter(p => p.field === "characteristics")[0];
  const chT = ch ? pfCharsText(ch.value) : "";
  if (chT) h += '<p class="hint ob-ch">' + esc(T("tg.act.prefill_chars", "Из техпаспорта: {list}", {list: chT})) + "</p>";
  const pl = T("tg.act.obj_photos", "Фото объекта");
  h += '<div class="ob-ph"><p class="f">' + esc(pl) + "</p>";
  if (!CH.files.length) h += '<p class="hint">' + esc(T("tg.act.obj_photos_none", "Фото не загружены. Добавьте их на шаге «Фото» — потом отметьте здесь, какие относятся к объекту.")) + "</p>";
  else {
    h += '<div class="segsel ob-chips" role="group" aria-label="' + esc(pl) + '">'
      + CH.files.map(f => '<button type="button" data-obph="' + i + "|" + esc(f.id) + '" aria-pressed="' + (it.photos.indexOf(f.id) >= 0) + '">' + esc(obFileName(f)) + "</button>").join("")
      + "</div>" + '<span class="hint">' + esc(T("tg.act.obj_photos_hint", "Отметьте снимки этого объекта: по ним — осмотр и подсказки из техпаспорта.")) + "</span>";
  }
  return h + "</div></div>";
}
function obCardHtml(){
  if (!obOn()) return "";
  const cls = wzClass();
  tplNeed(cls);
  const x = TPL.cache[tplKey(cls)], t = x && x.template ? x : null, err = CH.errs.ob, items = CH.ob.items;
  return '<section class="card ob-card' + (err ? " bad" : "") + '" id="obCard"><h2>' + esc(T("tg.act.obj_title", "Объекты договора"))
    + '<span class="ob-n">' + items.length + " / " + OB_MAX + "</span></h2>"
    + '<p class="sub">' + esc(T("tg.act.obj_sub", "Название — марка и модель или вид техники, без имён людей. Сумма и стоимость договора считаются по объектам.")) + "</p>"
    + items.map((it, i) => obRowHtml(it, i, cls, x, t)).join("")
    + '<div id="obTot">' + obTotHtml() + "</div>"
    + (items.length < OB_MAX ? '<div class="actions"><button type="button" class="btn btn-secondary" data-go="obadd">' + esc(T("tg.act.obj_add", "Добавить объект")) + "</button></div>" : "")
    + '<span class="ferr" id="wzErr-ob">' + esc(err || "") + "</span></section>";
}
function obAdd(){
  if (!CH.ob || CH.ob.items.length >= OB_MAX) return;
  CH.ob.items.push(obItem());
  haptic("select");
  obSync();
  wzSave();
  wzPart("obj");
  const el = $("#ob-label-" + (CH.ob.items.length - 1));
  if (el) { if (el.scrollIntoView) el.scrollIntoView({block: "center"}); try { el.focus({preventScroll: true}); } catch (e) {} }
}
function obDel(i){
  if (!CH.ob || CH.ob.items.length < 2 || !CH.ob.items[i]) return;
  CH.ob.items.splice(i, 1);
  CH.obBad = {};
  CH.obMore = {};
  haptic("select");
  obSync();
  wzSave();
  wzPart("obj");
}
function obPhoto(v){
  const p = String(v).split("|"), it = CH.ob && CH.ob.items[Number(p[0])];
  if (!it || !p[1]) return;
  const at = it.photos.indexOf(p[1]);
  if (at >= 0) it.photos.splice(at, 1); else it.photos.push(p[1]);
  pfApply();                                   // фото с техпаспортом привязано — подсказки в пустые поля объекта
  haptic("select");
  wzSave();
  wzPart("obj");
}
function obClearBad(i, k){
  if (!CH.obBad[i + "." + k]) return;
  delete CH.obBad[i + "." + k];
  const e = $("#obErr-" + i + "-" + k);
  if (e) { e.textContent = ""; if (e.parentNode) e.parentNode.classList.remove("bad"); }
  if (!Object.keys(CH.obBad).length) wzClearErr("ob");
}
/* ввод в карточке объекта: значение запоминаем, карточку не перерисовываем — курсор остаётся на месте */
function obInput(el){
  const i = Number(el.dataset.ob), k = el.dataset.obk, it = CH.ob && CH.ob.items[i];
  if (!it) return;
  if (k === "sum" || k === "value") { groupDigitsLive(el); const v = num(el.value); it[k] = v > 0 ? v : null; obSync(); }
  else if (k === "year" || k === "mileage") {
    if (/\D/.test(el.value)) el.value = el.value.replace(/\D/g, "");
    it[k] = el.value;
    if (k === "year") pfDrop("o" + i, "year");
    const w = el.closest(".pf-need");
    if (k === "mileage" && w && el.value) w.classList.remove("pf-need");
  }
  else if (k === "rate") {
    it.rate = decimalLive(el);
  }
  else if (k === "label") {
    it.label = String(el.value || "").slice(0, 120);
    pfDrop("o" + i, "label");
    const hd = $("#obH-" + i);
    if (hd) hd.textContent = it.label;
  }
  else if (k === "plate") it.plate = String(el.value || "").slice(0, 8);
  else if (k === "cond") it.cond = OB_COND.indexOf(el.value) >= 0 ? el.value : "";
  obClearBad(i, k);
  wzSave();
}
/* проверка перед отправкой — те же правила, что у сервера (validate_objects) */
function obCheck(){
  const bad = {}, y1 = new Date().getFullYear() + 1;
  CH.ob.items.forEach((it, i) => {
    if (!String(it.label || "").trim()) bad[i + ".label"] = T("tg.act.obj_err_label", "Впишите название: марку и модель или вид техники, без имён людей.");
    ["sum", "value"].forEach(k => { if (!(Number(it[k]) > 0)) bad[i + "." + k] = errText("sum_insured"); });
    const y = String(it.year || "").trim();
    if (y && !(/^\d{4}$/.test(y) && Number(y) >= 1950 && Number(y) <= y1)) bad[i + ".year"] = errText("year");
    if (String(it.plate || "").replace(/[\s\-_.]/g, "").length > 6) bad[i + ".plate"] = T("tg.act.obj_err_plate", "Только часть номера — не больше 6 знаков, например «…123».");
    const r = String(it.rate || "").trim();
    if (r) { const v = dec(r); if (!(v > 0 && v <= 100)) bad[i + ".rate"] = errText("req_rate"); }
  });
  CH.obBad = bad;
  return Object.keys(bad).length ? errText("ob") : "";
}
/* в POST /act/make: optional.objects; фото — только из текущей загрузки (сессия жива) */
function obBody(cls){
  const ids = CH.session ? CH.files.map(f => f.id) : [];
  return CH.ob.items.map(it => {
    const o = {label: String(it.label || "").trim().slice(0, 120), sum_insured: Number(it.sum) || 0, object_value: Number(it.value) || 0};
    if (/^\d{4}$/.test(String(it.year || ""))) o.year = Number(it.year);
    if (/^\d{1,7}$/.test(String(it.mileage || ""))) o.mileage = Number(it.mileage);
    const p = String(it.plate || "").trim();
    if (p) o.plate_hint = p;
    if (OB_COND.indexOf(it.cond) >= 0) o.condition = it.cond;
    if (it.kind) o.object_kind = it.kind;
    const cf = tfBodyCf(cls, it.cf);
    if (cf) o.class_fields = cf;
    const ph = it.photos.filter(x => ids.indexOf(x) >= 0);
    if (ph.length) o.photo_ids = ph;
    const r = dec(it.rate);
    if (String(it.rate || "").trim() && r > 0) o.requested_rate_pct = r;
    return o;
  });
}

/* ---------- автозаполнение ТС: ответ /act/photos → состояние, блок в «Распознано» ---------- */
const VP_FIELDS = ["object_label", "year", "class_fields.veh_group", "class_fields.fuel", "characteristics"];
function vpFrom(d){
  const items = anArr(d && d.prefill_fields).filter(x => x && VP_FIELDS.indexOf(x.field) >= 0 && x.value != null && x.value !== "").slice(0, 20)
    .map(x => ({field: String(x.field), value: x.field === "characteristics" ? anObj(x.value) : anStr(x.value).slice(0, 120),
      source: anStr(x.source), source_label: anStr(x.source_label), confidence: Number(x.confidence) || 0, file: anStr(x.file),
      why: anStr(x.why).slice(0, 300), label: anStr(x.label), check_label: anStr(x.check_label), value_label: anStr(x.value_label)}))
    .filter(x => x.field === "characteristics" ? Object.keys(x.value).length > 0 : x.value !== "");
  return items.length ? {items: items} : null;
}
/* в sessionStorage — без пояснений модели (why): после перезагрузки «!» остаётся, «почему» — нет */
function vpSave(v){ return v ? {items: v.items.map(x => Object.assign({}, x, {why: ""}))} : null; }
function vpLoad(v){ return v && Array.isArray(v.items) ? vpFrom({prefill_fields: v.items}) : null; }
function pfSaveMap(m){
  const out = {};
  Object.keys(m || {}).forEach(k => { if (m[k] && typeof m[k] === "object") out[k] = Object.assign({}, m[k], {why: ""}); });
  return out;
}
function pfLoadMap(m){
  const out = {};
  if (!m || typeof m !== "object" || Array.isArray(m)) return out;
  Object.keys(m).forEach(k => {
    const e = m[k];
    if (/^(label|year|cf\.[A-Za-z0-9_]{1,40})$/.test(k) && e && typeof e === "object" && e.v != null) {
      out[k] = {v: String(e.v).slice(0, 120), src: anStr(e.src), srcLabel: anStr(e.srcLabel), conf: Number(e.conf) || 0, why: "",
        check: anStr(e.check), file: anStr(e.file)};
    }
  });
  return out;
}
function vpBoxHtml(){
  const items = CH.vp ? anArr(CH.vp.items) : [];
  if (!items.length) return "";
  const rows = items.map((x, i) => {
    const val = x.field === "characteristics" ? pfCharsText(x.value) : (x.value_label || anStr(x.value));
    const lbl = x.label || (x.field === "object_label" ? T("tg.act.obj_label", "Название объекта") : x.field);
    return '<div class="vp-row"><span>' + esc(lbl) + ":</span><b>" + esc(val) + "</b>" + pfIconHtml("pf-vp-" + i, pfEntry(x, val)) + "</div>";
  }).join("");
  return '<div class="vp-box"><div class="h3">' + esc(T("tg.act.prefill_title", "Автозаполнение по ТС")) + "</div>"
    + '<p class="hint">' + esc(obOn() ? T("tg.act.prefill_sub_obj", "Подставлено в пустые поля объектов по привязанным фото. Знак «!» — значение заполнено автоматически: нажмите, чтобы увидеть источник, и проверьте.")
      : T("tg.act.prefill_sub", "Подставлено в пустые поля: год, подгруппа и топливо. Знак «!» — значение заполнено автоматически: нажмите, чтобы увидеть источник, и проверьте.")) + "</p>"
    + rows + '<p class="hint">' + esc(T("tg.act.prefill_mileage_note", "Пробег не заполняется автоматически — введите его сами.")) + "</p></div>";
}

/* ---------- шаг 3: парк ТС — итоги, таблица объектов, свёрнутые блоки по объектам ---------- */
const obAct = a => !!a && anArr(a.objects).length > 0 && !!a.objects_total && typeof a.objects_total === "object";
const obMoney = v => isNum(v) ? money(v) : "—";
const obPct = v => isNum(v) ? pct(v, Math.max(2, dp(v))) : "—";
function obSumHtml(a){
  const Tt = anObj(a.objects_total), lv = Tt.level, cv = anObj(Tt.value), vb = anObj(Tt.value_by_verdict);
  const ratio = isNum(cv.ratio_pct) ? pct(cv.ratio_pct, dp(cv.ratio_pct) ? 2 : 0) : T("tg.act.na", "данные недоступны");
  const u = anArr(vb.under).length, o = anArr(vb.over).length, nrm = anArr(vb.normal).length;
  return '<div class="act-kpis">'
    + '<div class="kprem"><span>' + esc(T("tg.act.premium", "Премия")) + "</span><b>" + premUnitHtml(anStr(Tt.premium_text) || actPremium(a)) + "</b>"
    + "<em>" + esc(T("tg.act.obj_prem_sum", "сумма премий {n} объектов", {n: anStr(Tt.count)})) + "</em></div>"
    + "<div><span>" + esc(T("tg.act.obj_rate_avg", "Средняя ставка")) + "</span><b>" + esc(obPct(Tt.rate_avg_pct)) + "</b>"
    + "<em>" + esc(T("tg.act.obj_rate_ref", "справочно: премия к страховой сумме")) + "</em></div>"
    + "<div><span>" + esc(T("tg.act.level", "Уровень риска")) + "</span>"
    + lvBarHtml(lv)
    + '<b class="lvname">' + esc(levelName(lv)) + "</b><em>" + esc(T("tg.act.obj_level_worst", "наивысший — объект {n}", {n: anStr(Tt.worst_index)}))
    + (Tt.calibrated ? "" : "; " + esc(T("tg.act.uncal_level", "пороги не калиброваны"))) + "</em></div>"
    + "<div><span>" + esc(T("tg.act.ratio", "Сумма к стоимости")) + "</span><b>" + esc(ratio) + "</b>"
    + '<em class="' + (u || o ? "warn" : "ok") + '">' + esc(T("tg.act.obj_value_split", "в норме: {n}, недострахование: {u}, превышение: {o}", {n: nrm, u: u, o: o})) + "</em></div>"
    + "</div>";
}
function obDetailHtml(o){
  const R = anObj(o.rate), V = anObj(o.value), sc = anObj(o.scenarios);
  const rf = anArr(o.risk_factors).filter(f => f && anStr(f.text));
  const how = anArr(R.how).map(anStr).filter(Boolean);
  let h = "";
  if (rf.length) {
    h += '<p><b>' + esc(T("tg.act.obj_d_risk", "Признаки риска")) + "</b></p><ul>"
      + rf.map(f => '<li class="' + (f.sign === "+" || f.sign === "up" ? "up" : "") + '">' + esc(anStr(f.text)) + "</li>").join("") + "</ul>";
  }
  if (how.length) h += '<p><b>' + esc(T("tg.act.obj_d_how", "Как посчитана ставка")) + "</b></p><ul>" + how.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul>";
  const facts = [];
  if (isNum(R.min_pct)) facts.push(T("tg.act.obj_d_min", "Минимальная ставка: {p}", {p: obPct(R.min_pct)}) + (R.min_applied ? " — " + T("tg.act.obj_d_min_applied", "ставка поднята до минимальной") : ""));
  if (o.below_min) facts.push(T("tg.act.obj_d_below_min", "Запрошено {req} — ниже минимальной {min}. Решение — андеррайтер.", {req: obPct(o.requested_rate_pct), min: obPct(R.min_pct)}));
  else if (isNum(o.requested_rate_pct)) facts.push(T("tg.act.obj_d_req", "Запрошенная ставка: {p}", {p: obPct(o.requested_rate_pct)}));
  if (isNum(R.factor_product)) facts.push(T("tg.act.obj_d_factors", "Факторы объекта: {m}", {m: faMult(R.factor_product)}) + " — "
    + (R.factors_applied ? T("tg.act.fa_mode_apply", "применено к ставке") : T("tg.act.fa_mode_ref", "справочно")));
  if (o.franchise_applied) facts.push(T("tg.act.obj_d_fr", "С франшизой договора: ставка {r}, премия {p}", {r: obPct(o.rate_pct), p: anStr(o.premium_text) || obMoney(o.premium)}));
  if (isNum(o.mileage)) facts.push(T("tg.act.obj_d_mileage", "Пробег: {n} км", {n: nf(o.mileage, 0)}));
  if (anStr(o.plate_hint)) facts.push(T("tg.act.obj_d_plate", "Часть номера: {p}", {p: anStr(o.plate_hint)}));
  const ph = anArr(o.photo_ids).map(anStr).filter(Boolean);
  facts.push(ph.length ? T("tg.act.obj_d_photos", "Фото: {list}; повреждений на фото: {n}", {list: ph.join(", "), n: Number(o.damages) || 0})
    : T("tg.act.obj_d_no_photos", "Фото к объекту не привязаны."));
  if (anStr(V.legal_ref_text)) facts.push(anStr(V.text) + " — " + anStr(V.legal_ref_text));
  if (sc.available) facts.push(T("tg.act.obj_d_scen", "Сценарии убытка: PML {p}, EML {e}, MFL {m}", {p: obMoney(sc.pml), e: obMoney(sc.eml), m: obMoney(sc.mfl)}));
  return h + facts.map(x => "<p>" + esc(x) + "</p>").join("");
}
function obActHtml(a){
  if (!obAct(a)) return "";
  const O = anArr(a.objects), Tt = anObj(a.objects_total), cv = anObj(Tt.value);
  const title = T("tg.act.obj_act_title", "Объекты договора ({n})", {n: O.length});
  const cols = [T("tg.act.obj_col_n", "№"), T("tg.act.obj_col_object", "Объект"), T("tg.act.obj_col_year", "Год"), T("tg.act.obj_col_sum", "Сумма"),
    T("tg.act.obj_col_value", "Стоимость"), T("tg.act.obj_col_level", "Уровень"), T("tg.act.obj_col_rate", "Ставка"), T("tg.act.obj_col_premium", "Премия"),
    T("tg.act.obj_col_ratio", "Сумма к стоимости")];
  const N = [2, 3, 4, 6, 7];
  const td = (j, html) => "<td" + (j === 1 ? ' class="h"' : N.indexOf(j) >= 0 ? ' class="n"' : "") + ' data-l="' + esc(cols[j]) + '">' + html + "</td>";
  const rows = O.map(o => {
    const V = anObj(o.value), worst = Number(o.index) === Number(Tt.worst_index);
    const sub = [anStr(o.kind_label), anStr(o.plate_hint)].filter(Boolean).join(" · ");
    const tags = (o.below_min ? '<span class="tag warn">' + esc(T("tg.act.obj_tag_below", "ниже минимума")) + "</span>" : "")
      + (o.franchise_applied ? '<span class="tag off">' + esc(T("tg.act.obj_tag_fr", "с франшизой")) + "</span>" : "");
    return "<tr" + (worst ? ' class="ob-worst"' : "") + ">" + td(0, esc(anStr(o.index)))
      + td(1, "<b>" + esc(anStr(o.label)) + "</b>" + (sub ? "<small>" + esc(sub) + "</small>" : "") + (tags ? '<span class="an-tags">' + tags + "</span>" : ""))
      + td(2, esc(isNum(o.year) ? String(o.year) : "—")) + td(3, esc(obMoney(o.sum_insured))) + td(4, esc(obMoney(o.object_value)))
      + td(5, esc(levelName(o.level))) + td(6, esc(obPct(o.rate_pct))) + td(7, esc(anStr(o.premium_text) || obMoney(o.premium)))
      + td(8, esc(anStr(V.text) || (isNum(V.ratio_pct) ? pct(V.ratio_pct, 2) : "—"))) + "</tr>";
  }).join("");
  const foot = "<tr>" + td(0, "") + td(1, esc(T("tg.act.obj_total_row", "Итого"))) + td(2, "") + td(3, esc(obMoney(Tt.sum_insured))) + td(4, esc(obMoney(Tt.object_value)))
    + td(5, esc(levelName(Tt.level))) + td(6, esc(obPct(Tt.rate_avg_pct)) + "<small>" + esc(T("tg.act.obj_ref", "справочно")) + "</small>")
    + td(7, esc(anStr(Tt.premium_text) || obMoney(Tt.premium))) + td(8, esc(isNum(cv.ratio_pct) ? pct(cv.ratio_pct, 2) : "—")) + "</tr>";
  const sc = anObj(Tt.scenarios), lg = anObj(sc.largest), sm = anObj(sc.sum), lines = [];
  if (anStr(sc.rule_text)) lines.push(anStr(sc.rule_text));
  if (isNum(lg.index)) lines.push(T("tg.act.obj_sc_largest", "Самый крупный объект — {n} {label}: PML {p}, EML {e}, MFL {m}", {n: lg.index, label: anStr(lg.label), p: obMoney(lg.PML), e: obMoney(lg.EML), m: obMoney(lg.MFL)}));
  if (isNum(sm.count)) lines.push(T("tg.act.obj_sc_sum", "Сумма по {k} объектам: PML {p}, EML {e}, MFL {m}", {k: sm.count, p: obMoney(sm.PML), e: obMoney(sm.EML), m: obMoney(sm.MFL)}));
  const notes = anArr(Tt.notes).map(anStr).filter(Boolean);
  return '<section class="card act-card ob-act" id="obAct" aria-label="' + esc(title) + '"><div class="rf-top"><h2>' + esc(title) + "</h2>"
    + (Tt.calibrated ? "" : '<span class="tag off">' + esc(T("tg.act.obj_uncal", "экспертные ставки и пороги, не калибровано")) + "</span>") + "</div>"
    + '<table class="an-t fold"><thead><tr>' + cols.map((c, j) => "<th" + (N.indexOf(j) >= 0 ? ' class="n"' : "") + ">" + esc(c) + "</th>").join("") + "</tr></thead>"
    + "<tbody>" + rows + "</tbody><tfoot>" + foot + "</tfoot></table>"
    + (lines.length ? '<h3 class="rf-h">' + esc(T("tg.act.obj_sc_title", "Сценарии убытка договора")) + '</h3><ul class="ob-sc">' + lines.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul>" : "")
    + (notes.length ? '<h3 class="rf-h">' + esc(T("tg.act.obj_notes", "Пометки")) + '</h3><ul class="ob-sc">' + notes.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul>" : "")
    + '<h3 class="rf-h">' + esc(T("tg.act.obj_details", "По каждому объекту")) + "</h3>"
    + O.map(o => {
      const k = anStr(o.index);
      return '<details class="ob-d" data-obd="' + esc(k) + '"' + (CH.obOpen[k] ? " open" : "") + "><summary><span><b>"
        + esc(T("tg.act.obj_n", "Объект {n}", {n: k}) + " · " + anStr(o.label)) + "</b><small>"
        + esc([levelName(o.level), obPct(o.rate_pct), anStr(o.premium_text) || obMoney(o.premium)].join(" · ")) + "</small></span></summary>"
        + obDetailHtml(o) + "</details>";
    }).join("")
    + "</section>";
}

/* ---------- шаг 3: регион и территория страхования (особые регионы uz_all и other) ---------- */
function actRegionHtml(a){
  const R = anObj(a.region), Tr = anObj(a.territory);
  let h = "";
  if (anStr(R.label)) {
    h += '<p class="act-line"><b>' + esc(T("tg.an.f.region", "Регион")) + ":</b> " + esc(anStr(R.label))
      + (anStr(R.note) ? ' <span class="rf-ovl">' + esc(anStr(R.note)) + "</span>" : "") + "</p>";
  }
  if (anStr(Tr.text) && Tr.text !== R.label) {
    h += '<p class="act-line"><b>' + esc(T("tg.act.region_text", "Территория страхования")) + ":</b> " + esc(anStr(Tr.text))
      + (anStr(Tr.note) && Tr.note !== R.note ? ' <span class="rf-ovl">' + esc(anStr(Tr.note)) + "</span>" : "") + "</p>";
  }
  return h;
}

/* ---------- шаг 3: язык акта — RU | UZ | EN; акт берётся заново без пересчёта (GET /act/{id}?lang=) ---------- */
const actLang = () => CH.actLang || I18N_LANG;
function actLangHtml(a){
  const langs = anArr(a.langs_available).filter(l => ["ru", "uz", "en"].indexOf(l) >= 0);
  if (langs.length < 2) return "";
  const cur = actLang(), lbl = T("tg.act.lang_label", "Язык акта");
  const name = l => l === "uz" ? T("tg.act.lang_uz", "UZ") : l === "en" ? T("tg.act.lang_en", "EN") : T("tg.act.lang_ru", "RU");
  return '<div class="act-lang"><span class="f">' + esc(lbl) + '</span><div class="segsel" role="group" aria-label="' + esc(lbl) + '">'
    + langs.map(l => '<button type="button" data-actlang="' + l + '" aria-pressed="' + (l === cur) + '"' + (CH.actBusy ? " disabled" : "") + ">" + esc(name(l)) + "</button>").join("")
    + "</div><small>" + (CH.actBusy ? spin(T("tg.act.lang_loading", "загружаю акт на выбранном языке…"))
      : esc(T("tg.act.lang_hint", "Меняет язык акта, Word и PDF. Расчёт не повторяется."))) + "</small></div>";
}
function actLangPick(l){
  if (["ru", "uz", "en"].indexOf(l) < 0 || CH.actBusy || l === actLang()) return;
  CH.actLang = l === I18N_LANG ? "" : l;
  haptic("select");
  wzSave();
  actFetch();
}

/* ---------- обработчики шага: одни на весь #wzBody (части перерисовываются без перепривязки) ---------- */
function wzClick(e){
  const t = e.target.closest("button, a[data-dl]");
  const box = $("#wzBody");
  if (!t || !box.contains(t)) return;
  const d = t.dataset;
  if (d.dl) return;                                  // обычная ссылка на файл — в браузере
  if (d.send) { CH.msgAt = t.closest("#scoCard") ? "sco" : "act"; actSend(d.send); return; }
  if (d.go === "scoimg") { scoImg(); return; }
  if (d.cbreset) { cbReset(); return; }
  if (d.mk) { mkAction(d.mk); return; }
  if (d.mkopen != null) { const l = MK.links && MK.links.links[Number(d.mkopen)]; if (l) mkOpen(l.url, l.site); return; }
  if (d.mvopen != null) { const l = ((CH.act && CH.act.market_value && CH.act.market_value.links) || [])[Number(d.mvopen)]; if (l) mkOpen(l.url); return; }
  if (d.mkrm) { mkRemove(Number(d.mkrm)); return; }
  if (d.mkdel != null) { mkDel(Number(d.mkdel)); return; }
  if (d.pick === "cam") { $("#chatCam").click(); return; }
  if (d.pick === "files" || t.id === "wzDrop") { $("#chatFile").click(); return; }
  if (d.rm) { wzRemove(Number(d.rm)); return; }
  if (d.cbk) { cbToggle(Number(d.cbk)); return; }
  if (d.prodopen) { CH.prodOpen = true; wzPart("prod"); const q = $("#wzProdQ"); if (q) q.focus(); return; }
  if (d.prodclose) { CH.prodOpen = false; CH.prodQ = ""; wzPart("prod"); return; }
  if (d.prod) { wzPickProduct(d.prod); return; }
  if (d.clsonly) { wzPickClass(d.clsonly); return; }
  if (d.add || d.copy) { wzQuickAdd(t); return; }
  if (d.set) { wzSetOpt(t); return; }
  if (d.fr) { wzFrSet(d.fr, d.value); return; }
  if (d.dqfr) { dqFrSet(d.dqfr, d.value); return; }
  if (d.dqreset) { dqReset(d.dqreset); return; }
  if (d.go === "frapply") { actFrApply(); return; }
  if (d.go === "docall") { actDocAll(); return; }
  if (d.go === "anall") { anAll(); return; }
  if (d.go === "addphoto") { wzGo(1); return; }
  if (d.go === "fix") { wzGo(2); return; }
  if (d.go === "restart") { haptic("select"); actReset(); return; }
  if (d.go === "legal") { openTab("legal"); return; }
  // части договора и поля класса
  if (d.tfb) { tfBool(t); return; }
  if (d.ptdel != null) { ptDel(Number(d.ptdel)); return; }
  if (d.ptsame) { ptSame(d.ptsame); return; }
  if (d.go === "ptopen") { CH.ptAdd = true; wzSave(); wzPart("parts"); wzPart("tpl"); wzPart("more"); const s = $("#ptAddCls"); if (s) s.focus(); return; }
  if (d.go === "ptinit") { ptInit(); return; }
  if (d.go === "ptadd") { ptAdd(); return; }
  if (d.go === "ptconfirm") { ptConfirm(); return; }
  if (d.go === "toparts") { wzGo(2); wzFocusField("parts"); return; }
  if (d.go === "copy") { actCopy(); return; }
  if (d.go === "openbot") { actOpenBot(); return; }
  // несколько объектов, автозаполнение ТС, язык акта
  if (d.obmode) { obMode(d.obmode); return; }
  if (d.go === "obadd") { obAdd(); return; }
  if (d.obdel != null) { obDel(Number(d.obdel)); return; }
  if (d.obph) { obPhoto(d.obph); return; }
  if (d.pf) {
    e.preventDefault();                               // значок внутри подписи поля: не передаём щелчок полю
    const p = document.getElementById(d.pf + "-p");
    if (p) { p.hidden = !p.hidden; t.setAttribute("aria-expanded", String(!p.hidden)); }
    return;
  }
  if (d.actlang) { actLangPick(d.actlang); return; }
}
function wzInput(e){
  const el = e.target;
  if (el.id === "wzProdQ") { CH.prodQ = el.value; wzPart("list"); return; }
  if (el.dataset && el.dataset.rec != null) { recInput(el); return; }
  if (el.dataset && el.dataset.frval) { wzFrInput(el); return; }
  if (el.dataset && el.dataset.dq) { dqInput(el); return; }
  if (el.dataset && el.dataset.cbf) { cbInput(el); return; }
  if (el.dataset && el.dataset.mkq) { MK.q = String(el.value || "").slice(0, 60); mkSave(); return; }
  if (el.dataset && el.dataset.mkprice != null) { mkPriceInput(el); return; }
  if (el.dataset && el.dataset.mkrate) { mkRateInput(el); return; }
  if (el.dataset && el.dataset.mka && el.tagName !== "SELECT") { mkAddInput(el); return; }
  if (el.dataset && el.dataset.fk && el.tagName !== "SELECT") wzFieldInput(el);
  if (el.dataset && el.dataset.tf && el.tagName !== "SELECT") { tfInput(el); return; }
  if (el.dataset && el.dataset.pts != null) { ptMoneyInput(el, "sum"); return; }
  if (el.dataset && el.dataset.ptv != null) { ptMoneyInput(el, "value"); return; }
  if (el.dataset && el.dataset.ptfr != null) { ptFrInput(el); return; }
  if (el.dataset && el.dataset.obk && el.tagName !== "SELECT") { obInput(el); return; }
}
function wzChange(e){
  const el = e.target;
  if (el.tagName === "SELECT" && el.dataset.fk) wzFieldInput(el);
  if (el.dataset && el.dataset.mkuse != null) mkUse(el);
  if (el.tagName === "SELECT" && el.dataset.mka) mkAddInput(el);
  if (el.tagName === "SELECT" && el.dataset.tf) tfInput(el);
  if (el.id === "ptAddCls") { const x = $("#wzErr-ptadd"); if (x) x.textContent = ""; }
  if (el.tagName === "SELECT" && el.dataset.obk) obInput(el);
}
function wzToggle(e){
  const el = e.target;
  if (el && el.tagName === "DETAILS" && el.id === "wzMore") CH.optOpen = el.open;
  if (el && el.tagName === "DETAILS" && el.dataset.an) {
    CH.anOpen[el.dataset.an] = el.open;
    const b = document.querySelector('#wzBody [data-go="anall"]');
    if (b) b.textContent = anAllLabel();
  }
  if (el && el.tagName === "DETAILS" && el.id === "actSumD") CH.sumOpen = el.open;
  if (el && el.tagName === "DETAILS" && el.dataset.rfhow) { CH.rfOpen = CH.rfOpen || {}; CH.rfOpen[el.dataset.rfhow] = el.open; }
  if (el && el.tagName === "DETAILS" && el.id === "cbAct") CH.cbActOpen = el.open;
  if (el && el.tagName === "DETAILS" && el.dataset.ptn) CH.ptOpen[el.dataset.ptn] = el.open;
  if (el && el.tagName === "DETAILS" && el.dataset.ptmore) CH.ptMore[el.dataset.ptmore] = el.open;
  if (el && el.tagName === "DETAILS" && el.dataset.obmore) CH.obMore[el.dataset.obmore] = el.open;
  if (el && el.tagName === "DETAILS" && el.dataset.obd) CH.obOpen[el.dataset.obd] = el.open;
  if (el && el.tagName === "DETAILS" && el.dataset.sn) {
    CH.docOpen[el.dataset.sn] = el.open;
    const b = document.querySelector('#wzBody [data-go="docall"]');
    if (b) b.textContent = actDocAllLabel();
  }
}
function wzKey(e){
  const el = e.target;
  if (el.id === "wzProdQ" && e.key === "Enter") {
    e.preventDefault();
    const only = document.querySelectorAll("#wzProdList [data-prod]");
    if (only.length === 1) wzPickProduct(only[0].dataset.prod);
  }
  if (el.id === "mkQ" && e.key === "Enter") { e.preventDefault(); mkLinks(true); }
  if (el.id === "wzProdQ" && e.key === "Escape" && (CH.must.product_code || CH.must.class_code)) { CH.prodOpen = false; CH.prodQ = ""; wzPart("prod"); }
}

/* ---------- файлы: плитки, перетаскивание в любое место экрана, вставка из буфера ---------- */
function chatBoot(){
  const box = $("#wzBody");
  box.addEventListener("click", wzClick);
  box.addEventListener("input", wzInput);
  box.addEventListener("change", wzChange);
  box.addEventListener("toggle", wzToggle, true);          // toggle не всплывает — ловим на погружении
  box.addEventListener("keydown", wzKey);
  // отложенная перерисовка: данные пришли, пока человек печатал, — рисуем, когда он ушёл из поля
  box.addEventListener("focusout", () => setTimeout(() => { if (CH.later && !typing(box)) wzPaint(); }, 0));
  $("#chatMain").onclick = wzMain;
  $("#chatFile").onchange = e => { wzAddFiles(e.target.files); e.target.value = ""; };
  $("#chatCam").onchange = e => { wzAddFiles(e.target.files); e.target.value = ""; };
  $("#mkFile").onchange = e => { mkAddFiles(e.target.files); e.target.value = ""; };
  let depth = 0;
  const zone = on => {
    $("#chatDrop").classList.toggle("hidden", !on);
    const d = $("#wzDrop");
    if (d) d.classList.toggle("over", !!on);
  };
  document.addEventListener("dragenter", e => {
    if (TAB !== "chat" || !e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") < 0) return;
    depth++;
    zone(true);
  });
  document.addEventListener("dragover", e => { if (!$("#chatDrop").classList.contains("hidden")) e.preventDefault(); });
  document.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) zone(false); });
  document.addEventListener("drop", e => {
    if (TAB !== "chat") return;
    depth = 0;
    zone(false);
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      e.preventDefault();
      if (CH.wz === 2) mkAddFiles(e.dataTransfer.files);   // на шаге «Проверить» — снимки объявлений
      else wzAddFiles(e.dataTransfer.files);
    }
  });
  // вставка из буфера: скриншот или файл — в список шага «Фото»
  document.addEventListener("paste", e => {
    if (TAB !== "chat" || !e.clipboardData) return;
    // Windows кладёт снимок экрана не в clipboardData.files, а в items (image/png без имени) — читаем оба пути
    let files = Array.prototype.slice.call(e.clipboardData.files || []);
    if (!files.length) {
      const items = Array.prototype.slice.call(e.clipboardData.items || []);
      files = items.filter(it => it.kind === "file" && /^image\//.test(it.type)).map(it => it.getAsFile()).filter(Boolean);
    }
    if (!files.length) return;
    e.preventDefault();
    // снимку из буфера даём имя с расширением: по нему проверяется формат и строится подпись в списке файлов
    files = files.map((f, i) => {
      if (f.name && /\.[a-z0-9]{2,5}$/i.test(f.name)) return f;
      const ext = /png/.test(f.type) ? "png" : /webp/.test(f.type) ? "webp" : "jpg";
      try { return new File([f], "screenshot-" + Date.now() + "-" + (i + 1) + "." + ext, {type: f.type || "image/" + ext}); }
      catch (_) { return f; }
    });
    if (CH.wz === 2) mkAddFiles(files); else wzAddFiles(files);   // шаг «Проверить»: снимок экрана со списком объявлений
  });
}
