function scrAct(){
  var A=S.act,t=L[A.lang];
  var langSeg='<div class="seg" style="width:132px" role="group" aria-label="'+t.lang+'">'+["ru","uz","en"].map(function(l){return '<button class="'+(A.lang===l?'on':'')+'" data-a="actLang" data-v="'+l+'">'+l.toUpperCase()+'</button>';}).join("")+'</div>';
  var hd=head("Акт",'№ <span class="num">'+(S.fromCase?CR.act:(S.newCase?S.newCase.no:CR.act))+'</span>',backBtn(S.fromCase?"closeFlow":"wizBack"),A.loading||A.pin?'':langSeg);
  if(A.loading||A.pin)return {head:hd,body:actSkeleton(),cta:'<button class="btn btn-p" disabled>Формирую акт…</button>'};
  var body='<div class="stack">'+
    '<div class="gauge">'+gaugeSVG(CR.score)+'<div class="c"><b>'+CR.score+'</b><span>'+t.scoreOf+'</span></div></div>'+
    '<div style="display:grid;justify-items:center;gap:4px;margin-top:-8px"><button class="lnk" data-a="openSec" data-v="2">'+t.why+'</button>'+
    '<p class="note" style="text-align:center">'+t.notCredit+' <span class="tg exp">'+t.exp+'</span></p></div>'+
    '<div class="kpi"><div><b>'+fmt(CR.prem)+'</b><span>'+t.prem+'</span></div><div><b class="rec">'+pc(CR.rate)+' %</b><span>'+t.rec+'</span></div><div><b style="font-family:var(--font-sans)">'+t.lvlW+'</b><span>'+t.lvl+'</span></div></div>'+
    '<div class="btn3"><button class="btn btn-s" data-a="export" data-v="PDF">'+IC.down+t.pdf+'</button><button class="btn btn-s" data-a="export" data-v="Word">'+IC.down+t.word+'</button><button class="btn btn-g" data-a="export" data-v="chat">'+IC.send+t.chat+'</button></div>'+
    '<div class="alert bad"><span class="ic">!</span><div><b>'+t.alertT+'</b>'+t.alertX+'</div></div>'+
    '<div class="card"><div class="cap">'+t.fork+'</div>'+forkHTML(t)+
      '<div class="pts">'+t.pts.map(function(p){return '<div class="'+p[1]+'"><b>'+pc(p[0],p[0]===CR.regm||p[0]===CR.mkt?3:2)+'</b><span><em>'+p[2]+'</em> · '+p[3]+'</span></div>';}).join("")+'</div>'+
      '<button class="lnk" data-a="openSec" data-v="3">'+t.forkMore+'</button></div>'+
    '<div class="tabs" role="tablist">'+t.tabs.map(function(x,i){var k=["act","scen","market","object"][i];return '<button role="tab" aria-selected="'+(A.tab===k)+'" class="'+(A.tab===k?'on':'')+'" data-a="actTab" data-v="'+k+'">'+x+'</button>';}).join("")+'</div>'+
    '<div id="actTabBody">'+actTabHTML()+'</div>'+
    '<p class="note">№ '+CR.act+' · 02.10.2026 · '+t.foot+'</p></div>';
  return {head:hd,body:body,cta:'<button class="btn btn-p" data-a="askSpec">'+IC.chat+t.ask+'</button>'};
}
function actTabHTML(){
  var A=S.act,t=L[A.lang];
  if(A.tab==="act"){
    var cnt=[1,0,0,0,0];
    return '<div class="stack"><div class="secs">'+t.secs.map(function(s,i){return '<button class="sec" data-a="openSec" data-v="'+(i+1)+'"><span class="n">'+(i+1)+'</span><span class="t"><b>'+s+'</b><span>'+t.sub[i]+'</span></span>'+(cnt[i]?'<span class="cnt">'+cnt[i]+'</span>':'')+'<span class="chev">›</span></button>';}).join("")+'</div>'+sayHTML(t)+'</div>';
  }
  if(A.tab==="scen") return scenHTML();
  if(A.tab==="market") return marketHTML();
  return '<div class="stack">'+objectHTML()+valueHTML()+'</div>';
}
/* обязательный блок: ст. 63 Закона о страховой деятельности */
function sayHTML(t){
  var m=measCalc();
  return '<div class="card"><div class="cap" style="margin-bottom:12px">'+t.say+'</div><dl class="say">'+
    '<div><dt>Цена</dt><dd><b class="num">0,42 %</b> в год, премия <b class="num">'+fmt(CR.prem)+' сум</b> за 365 дней. Если выполнить и подтвердить меры из раздела 4 — '+(m.used.length?'<b class="num">'+pc(m.rate,3)+' %</b>, '+fmt(m.prem)+' сум.':'до <b class="num">0,379 %</b>.')+'</dd></div>'+
    '<div><dt>Покрыто</dt><dd>Опрокидывание при подъёме, ДТП при переезде, пожар, кража и угон, стихийные явления.</dd></div>'+
    '<div><dt>Не покрыто</dt><dd>Износ и поломки без внешней причины, работа без допуска оператора, подъём сверх грузоподъёмности, умысел. Сверить с правилами продукта 0318.</dd></div>'+
    '<div><dt>Недострахование</dt><dd>Сумма 2 945 000 000 — это 95 % стоимости 3 100 000 000. При частичном убытке выплата может быть пропорциональна 95 %, если в договоре нет иного условия.</dd></div>'+
    '<div><dt>Возврат премии</dt><dd>При досрочном расторжении возвращается часть премии за неистёкший срок, по правилам страхования.</dd></div>'+
    '<div><dt>Претензии</dt><dd>Письменно в филиал или через приложение. Сроки ответа — по правилам страхования, спор — в суде.</dd></div>'+
    '</dl><p class="note" style="margin-top:12px">Ст. 63 Закона о страховой деятельности: это нужно сказать до заключения договора.</p></div>';
}
var SCEN=[
  {k:"PML",n:"вероятный крупный убыток",v:CR.pml,p:"1 раз в 25 лет",py:"≈ 4 % в год",what:"Самый крупный убыток, который реально ждать при обычной работе: опрокидывание при подъёме на неровной стройплощадке, ремонт стрелы и поворотной части.",how:"29,6 % страховой суммы: доля для колёсного крана при опрокидывании с ремонтом. Защиты нет (площадка открытая, допуск оператора не подтверждён), поэтому доля не снижена."},
  {k:"EML",n:"оценка крупного убытка",v:CR.eml,p:"1 раз в 100 лет",py:"≈ 1 % в год",what:"Крупный убыток, когда часть защиты не сработала: опрокидывание с падением груза или пожар двигателя. Кран восстанавливают частично.",how:"59,4 % страховой суммы: доля для спецтехники с дизелем при пожаре или тяжёлом опрокидывании. Огнетушителей в кабине нет — доля не снижена."},
  {k:"MFL",n:"максимально возможный убыток",v:CR.mfl,p:"1 раз в 250 лет",py:"≈ 0,4 % в год",what:"Худший случай: полная гибель крана или угон без возврата.",how:"98,5 % страховой суммы: полная гибель за вычетом годных остатков. GPS с блокировкой нет, поэтому угон считается невозвратным."}
];
function mln(v){return v>=1000?pc(v/1000,v%100?2:1)+' млрд':v+' млн';}
function scenHTML(){
  var t=L[S.act.lang];
  return '<div class="stack"><div class="card"><div class="cap" style="margin-bottom:12px">Крупный убыток против страховой суммы</div><div class="bars">'+
    SCEN.map(function(r){return '<div class="bar"><div class="bl"><span><b>'+r.k+'</b> <span class="muted">'+r.p+'</span></span><span class="num">'+mln(r.v)+'</span></div><div class="tr"><i style="width:'+(r.v/2945*100).toFixed(1)+'%"></i></div></div>';}).join("")+
    '<div class="bar"><div class="bl"><span><b>Сумма</b> <span class="muted">страховая</span></span><span class="num">2,945 млрд</span></div><div class="tr"><i class="hi" style="width:100%"></i></div></div></div>'+
    '<div class="axis abs"><span style="left:0">0</span><span style="left:'+(1000/2945*100).toFixed(1)+'%;transform:translateX(-50%)">1 млрд</span><span style="left:'+(2000/2945*100).toFixed(1)+'%;transform:translateX(-50%)">2 млрд</span><span style="right:0">2,945</span></div>'+
    '<p class="note" style="margin-top:8px">'+tag("экспертно, не калибровано")+' Частоты — экспертная оценка разработчика, без данных об убытках компании.</p>'+
    '<button class="btn btn-s btn-sm" style="margin-top:12px;width:100%" data-a="openSec" data-v="scen">'+t.more+': что это, как часто, из чего</button></div>'+
    '<div class="card"><div class="cap" style="margin-bottom:8px">Главные риски объекта</div><div class="rlist">'+
    [["Опрокидывание при подъёме","h","высокий"],["ДТП при переезде","m","средний"],["Пожар двигателя","m","средний"],["Кража узлов на стоянке","m","средний"],["Стихийные явления","l","низкий"]].map(function(r){return '<div><span>'+r[0]+'</span><span class="lvl '+r[1]+'">'+r[2]+'</span></div>';}).join("")+'</div></div></div>';
}
function marketHTML(){
  var rows=[["APEX","1-е место",27.6,false],["INSON","15-е место",1.33,true]];
  return '<div class="stack"><div class="card"><div class="cap" style="margin-bottom:12px">Доля рынка по сборам</div><div class="bars">'+
    rows.map(function(r){return '<div class="bar"><div class="bl"><span><b>'+r[0]+'</b> <span class="muted">'+r[1]+'</span></span><span class="num">'+pc(r[2],r[2]<10?2:1)+' %</span></div><div class="tr"><i'+(r[3]?'':' class="hi"')+' style="width:'+(r[2]/30*100).toFixed(1)+'%"></i></div></div>';}).join("")+'</div>'+
    '<div class="axis"><span>0 %</span><span>10 %</span><span>20 %</span><span>30 %</span></div>'+
    '<div class="srcbar"><span>НАПП, сборы по компаниям · пример</span><button data-a="srcLink" data-v="napp.uz">Читать в источнике napp.uz</button></div></div>'+
    '<div class="card"><dl class="kv"><dt>Рыночная ставка по классу 3</dt><dd class="num">0,695 %</dd><dt>Наша рекомендуемая</dt><dd class="num" style="color:var(--accent)">0,42 %</dd><dt>С поправкой региона и рынка</dt><dd class="num">0,465 %</dd></dl>'+
    '<p class="note" style="margin-top:8px">Рыночная — премии, делённые на обязательства по классу. Поправки региона и рынка в премию не входят: их ещё проверяет актуарий. Подробно — раздел 3.</p></div></div>';
}
function objectHTML(){
  var f={};S.facts.forEach(function(x){f[x.k]=x;});
  var mil=S.chk.a3&&S.chk.a3.indexOf("Пропустить")<0?S.chk.a3:"не указано";
  var rows=[["Объект",f.obj.v,f.obj.src],["Тип и топливо",f.type.v,f.type.src],["Грузоподъёмность","50 т","техпаспорт"],["Масса",f.mass.v,f.mass.src],["Моточасы",mil,mil==="не указано"?"уточнить":"введено сотрудником"],["Регион",f.region.v,f.region.src],["Госномер","01 A ••• ••","техпаспорт"],["VIN","•••••••••••••4417","техпаспорт"],["Фото",S.shots.length+" из 5","съёмка"]];
  return '<div class="card"><div class="tblwrap"><table class="tbl"><tbody>'+rows.map(function(r){return '<tr><td class="muted" style="width:38%">'+r[0]+'</td><td><b style="font-weight:600">'+esc(r[1])+'</b><div style="margin-top:4px">'+tag(r[2])+'</div></td></tr>';}).join("")+'</tbody></table></div>'+
    '<p class="note" style="margin-top:8px">Номера и VIN показаны частично. Данные владельца не хранятся.</p></div>';
}
function valueHTML(){
  var ads=[3050000000,3080000000,3120000000,3190000000];
  return '<div class="card"><div class="cap" style="margin-bottom:8px">Оценка стоимости</div><div class="tblwrap"><table class="tbl"><thead><tr><th>Объявление</th><th class="r">Цена, сум</th></tr></thead><tbody>'+
    ads.map(function(a,k){return '<tr><td>XCMG QY50K, 2022 · avtoelon · '+["08.2026","07.2026","09.2026","05.2026"][k]+'</td><td class="r num">'+fmt(a)+'</td></tr>';}).join("")+
    '<tr><td><b>Медиана</b></td><td class="r num"><b>'+fmt(CR.value)+'</b></td></tr></tbody></table></div>'+
    '<p class="note" style="margin-top:8px">Сумма 2 945 000 000 = 95 % стоимости, в норме (ГК ст. 936). Порог расхождения оценок 15 %, разброс 4,6 %.</p>'+
    '<div class="srcbar"><span>Объявления не старше 6 месяцев</span><button data-a="srcLink" data-v="avtoelon.uz">Читать в источнике avtoelon.uz</button></div></div>';
}
function goSec(n,label){return '<button class="lnk" data-a="openSec" data-v="'+n+'">'+label+'</button>';}
function secHTML(i){
  var e=tag("экспертно, не калибровано");
  if(i==="scen"){
    return '<div class="stack"><p class="small muted">Три сценария крупного убытка по этому крану: что случится, как часто и из чего посчитана сумма.</p>'+
      SCEN.map(function(s){return '<div class="card"><div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px"><b style="font:var(--t-title-m);font-weight:700">'+s.k+' <span class="muted" style="font:var(--t-label-l)">'+s.n+'</span></b><b class="num">'+mln(s.v)+'</b></div>'+
        '<dl class="say" style="margin-top:12px"><div><dt>Что это</dt><dd>'+s.what+'</dd></div>'+
        '<div><dt>Как часто</dt><dd><b>'+s.p+'</b> ('+s.py+'). <span class="tg exp">экспертная оценка разработчика, не калибровано</span></dd></div>'+
        '<div><dt>Из чего</dt><dd>'+s.how+'</dd></div></dl></div>';}).join("")+
      '<div class="card"><div class="cap" style="margin-bottom:8px">Лимит на один риск</div><dl class="kv"><dt>Не более 20 % собственных средств</dt><dd>Положение 1806, п. 15</dd><dt>Собственные средства</dt><dd class="num">120 млрд сум</dd><dt>Лимит на один риск</dt><dd class="num">24 млрд сум</dd></dl>'+
        '<p class="note" style="margin-top:8px">'+tag("временные, не настоящие")+' MFL 2,9 млрд в лимит укладывается. Повторить проверку на собственных средствах компании.</p></div>'+
      '<div class="card"><div class="cap" style="margin-bottom:8px">Откуда возьмётся точность</div>'+
        '<p class="small">Сейчас частоты и доли — экспертная оценка. Их будут пересчитывать из данных компании:</p>'+
        '<div class="rlist" style="margin-top:8px"><div><span>Департамент претензий: частота и тяжесть убытков по классу</span></div><div><span>Актуарные заключения и резервы компании</span></div><div><span>Загрузка через админку: «Страховые случаи» из Excel</span></div></div>'+
        '<p class="note" style="margin-top:8px">Платформа уточняет оценки сама по мере накопления данных. Пометка «не калибровано» снимется, когда расчёт пройдёт проверку актуария.</p></div></div>';
  }
  switch(i){
  case 1: var hz=[
      ["Открытая площадка ночью","На фото сзади и слева: грунтовая площадка без ограждения и освещения.","фото","кража узлов, угон, вандализм"],
      ["Расхождение массы","Табличка 36 170 кг, техпаспорт 38 600 кг. Разница 2 430 кг — уточнить на месте.","табличка / техпаспорт","верность документов, расчёт нагрузки"],
      ["Работа на стройке","В запросе филиала: кран работает на строительных площадках.","запрос филиала","опрокидывание, падение груза"],
      ["Нет сведений о моточасах","Моточасов нет ни в запросе, ни в техпаспорте, на осмотре не уточнены.","запрос филиала","износ и отказ узлов"],
      ["Регион с высокой аварийностью","Ташкентская область: ДТП и кражи транспорта выше среднего по республике.","stat.uz","ДТП при переезде, кража"]];
    return '<div class="stack"><p class="small muted">Что обнаружено по фото, документам и данным. Из этих опасностей складываются балл (раздел 2) и ставка (раздел 3).</p>'+
      '<div class="card"><div class="haz">'+hz.map(function(h){return '<div><b>'+h[0]+'</b><p>'+h[1]+'</p><div>'+tag(h[2])+'</div><span class="af">Влияет на: '+h[3]+'</span></div>';}).join("")+'</div></div>'+
      '<div class="card"><div class="cap" style="margin-bottom:8px">Не обнаружено</div><div class="rlist"><div><span>Убытков за 3 года нет</span>'+tag("запрос филиала")+'</div><div><span>Повреждений на фото нет</span>'+tag("фото")+'</div></div></div>'+
      '<div class="card"><div class="cap" style="margin-bottom:8px">Файлы</div><div class="rlist">'+
      [["Фото объекта",S.shots.length+" шт."],["Техпаспорт","7 полей"],["Запрос филиала","16 строк"]].map(function(r){return '<div><span>'+r[0]+'</span><span class="num">'+r[1]+'</span></div>';}).join("")+'</div></div>'+
      goSec(2,"Как опасности дали оценку — раздел 2 →")+'</div>';
  case 2: var sc=[["Базовый балл","500",""],["Открытая площадка ночью","−60","фото"],["Работа на стройке","−45","запрос филиала"],["Возраст и моточасы неизвестны","−30","запрос филиала"],["Расхождение массы 36 170 / 38 600 кг","−28","табличка / техпаспорт"],["Убытков за 3 года нет: штрафа нет","+0","запрос филиала"]];
    return '<div class="stack"><div class="card"><div class="gauge" style="max-width:240px">'+gaugeSVG(CR.score)+'<div class="c"><b>337</b><span>из 500 · умеренный</span></div></div></div>'+
      '<div class="card"><div class="cap" style="margin-bottom:8px">Из чего сложился балл</div><div class="tblwrap"><table class="tbl"><thead><tr><th>Признак</th><th class="r">Вклад</th></tr></thead><tbody>'+
      sc.map(function(r){return '<tr><td>'+r[0]+(r[2]?'<div style="margin-top:4px">'+tag(r[2])+'</div>':'')+'</td><td class="r num"><b>'+r[1]+'</b></td></tr>';}).join("")+
      '<tr><td><b>Итого</b></td><td class="r num"><b>337</b></td></tr></tbody></table></div>'+
      '<p class="note" style="margin-top:8px">500 − 60 − 45 − 30 − 28 + 0 = 337. Регион в балл не входит: он учтён поправкой в разделе 3, чтобы не считать дважды. '+e+'</p></div>'+
      '<div class="card"><div class="cap" style="margin-bottom:8px">Почему уровень «умеренный»</div><div class="rlist">'+
      [["375–500","низкий · × 1,0"],["250–374","умеренный · × 1,2"],["125–249","повышенный · × 1,4"],["0–124","высокий · × 1,7"]].map(function(r,k){return '<div'+(k===1?' style="font-weight:600"':'')+'><span class="num">'+r[0]+(k===1?' ← 337':'')+'</span><span>'+r[1]+'</span></div>';}).join("")+'</div>'+
      '<p class="note" style="margin-top:8px">Множитель уровня применяется к тарифу политики: 0,35 % × 1,2 = 0,42 %. Шаг за шагом — раздел 3.</p></div>'+
      '<div class="card"><div class="cap" style="margin-bottom:8px">Сценарии крупного убытка</div><dl class="kv">'+SCEN.map(function(s){return '<dt><b style="color:var(--fg)">'+s.k+'</b> · '+s.p+'</dt><dd class="num">'+mln(s.v)+'</dd>';}).join("")+'</dl>'+
      '<button class="btn btn-s btn-sm" style="margin-top:12px;width:100%" data-a="openSec" data-v="scen">Подробнее о сценариях</button></div>'+
      '<div class="alert"><span class="ic">i</span><div><b>Не является кредитным скорингом</b>Балл описывает риск объекта, а не платёжеспособность клиента. '+e+'</div></div>'+
      goSec(3,"Как оценка стала ставкой — раздел 3 →")+'</div>';
  case 3: return '<div class="stack"><p class="small muted">Лестница ставки: у каждого шага причина, направление и источник. В премию входят только первые три строки.</p>'+
      '<div class="card"><ol class="ladder">'+ladderHTML()+'</ol></div>'+
      '<div class="card"><div class="cap" style="margin-bottom:8px">Что снизит ставку</div><p class="small">Охраняемая стоянка, GPS с блокировкой и допуск оператора закрывают опасности из раздела 1. Все три, подтверждённые документами: <b class="num">0,379 %</b>, не ниже минимума 0,35 %.</p>'+goSec(4,"Предупредительные мероприятия — раздел 4 →")+'</div>'+
      '<p class="note">'+e+' Подлежит подтверждению андеррайтером.</p></div>';
  case 4: return measuresHTML();
  case 5: return controlHTML();
  }
  return "";
}
function lstep(cls,val,title,dir,why,src){
  var d={up:"▲ повышает",dn:"▼ понижает",base:"• основа",no:"▼ нельзя"}[dir]||"";
  return '<li class="'+cls+'"><div class="lv">'+val+'</div><div><div class="lt"><b>'+title+'</b>'+(d?'<span class="dir '+dir+'">'+d+'</span>':'')+'</div><p>'+why+'</p>'+(src?'<div>'+src.split("|").map(tag).join(" ")+'</div>':'')+'</div></li>';
}
function lgrp(t){return '<li class="grp"><div class="cap">'+t+'</div></li>';}
function ladderHTML(){
  return lgrp("В премию")+
    lstep("k","0,35 %","Тариф политики","base","Минимальная ставка продукта 0318 «Спецтехника юрлиц». Ниже неё страховщик договор не заключает.","приказ 54-П")+
    lstep("k","× 1,2","+20 % за умеренный уровень риска","up","Балл 337 попал в зону «умеренный». Его дали опасности: <em>открытая площадка</em> (−60), <em>работа на стройке</em> (−45), <em>нет моточасов</em> (−30). Убытков нет, поэтому уровень не выше.","скоринг · экспертно")+
    lstep("rec","0,42 %","Рекомендуемая ставка","","0,35 × 1,2 = 0,42 %. Премия: 2 945 000 000 × 0,42 % = <em>12 369 000 сум</em> за 365 дней.","расчёт")+
    lgrp("Справочно: в премию не входит, и почему")+
    lstep("k","× 1,05","Дизель","up","Старые дизельные двигатели чаще текут топливом и маслом — выше риск пожара в моторном отсеке.","техпаспорт")+
    lstep("k","× 1,4","Такси / аренда","up","Кран сдаётся в аренду с оператором: частая смена водителей и интенсивная эксплуатация, больше часов под нагрузкой.","запрос филиала")+
    lstep("k","× 1,15","Открытая площадка","up","Ночью кран стоит без охраны: кража узлов, угон, вандализм.","фото")+
    lstep("ref","0,71 %","С факторами объекта","","0,42 × 1,05 × 1,4 × 1,15 = 0,71 %. В премию не входит: коэффициенты не калиброваны на убытках компании, а открытая площадка уже снизила балл — брать за неё второй раз нельзя.","экспертно, не калибровано")+
    lstep("k","+10,75 %","Поправка региона","up","В Ташкентской области ДТП и кражи транспорта выше, чем в среднем по республике.","stat.uz")+
    lstep("k","0 %","Поправка рынка","base","Сборы и выплаты по классу 3 за год без резких изменений — поправлять не за что.","НАПП")+
    lstep("ref","0,465 %","Регион + рынок","","0,42 × 1,1075 × 1,00 = 0,465 %. В премию не входит: поправки ещё проверяет актуарий. Ориентир, если клиент сравнивает цены.","экспертно, не калибровано")+
    lgrp("Сравнение с рынком")+
    lstep("ref","0,695 %","Рынок НАПП","","Премии, делённые на обязательства по классу 3. Наша 0,42 % ниже рынка на 40 % — цена для клиента конкурентная.","НАПП")+
    lgrp("Запрос филиала")+
    lstep("bad","0,20 %","Запрошено — нет","no","Ниже минимума 0,35 %: это <em>57 %</em> минимума при пороге 60 %. Почему нельзя: это минимальная ставка страховщика; уровень риска умеренный, а не низкий; убытков нет, но защиты нет — площадка открытая, GPS и допуск оператора не подтверждены.","запрос филиала");
}
function measuresHTML(){
  var m=S.act.meas,c=measCalc();
  var f=c.used.length?'0,42 × '+c.used.map(function(x){return pc(x.r,2);}).join(" × ")+' = '+pc(CR.rate*c.f,3)+' %':'Ни одна мера не подтверждена документом: ставка 0,42 %';
  return '<div class="stack"><p class="small muted">Меры закрывают опасности из раздела 1. Скидка учитывается, когда мера подтверждена документом. Скидки перемножаются, ставка не ниже минимума 0,35 %.</p>'+
    '<div class="card">'+MEAS.map(function(x){var s=m[x.k];
      return '<div class="meas"><div class="mh"><b>'+x.t+'</b><span class="eff '+x.cl+'">'+x.eff+'</span></div><p>'+x.why+'</p>'+
        '<span class="note">Закрывает: <b style="color:var(--fg)">'+x.closes+'</b> · раздел 1</span>'+
        '<div class="mrow"><button class="cbx" role="checkbox" aria-checked="'+!!s.d+'" data-a="meas" data-v="'+x.k+':d"><i></i>Выполнено</button><button class="cbx" role="checkbox" aria-checked="'+!!s.c+'" data-a="meas" data-v="'+x.k+':c"><i></i>Подтверждено документом</button></div>'+
        (s.d&&!s.c&&x.r?'<span class="note" style="color:var(--warn-fg)">Скидка не учтена: нужен документ — '+x.doc+'.</span>':'<span class="note">Документ: '+x.doc+'.</span>')+'</div>';}).join("")+'</div>'+
    '<div class="mtot" aria-live="polite"><div class="f">'+f+'</div><dl class="kv"><dt>Ставка</dt><dd class="num">'+pc(c.rate,3)+' %</dd><dt>Премия</dt><dd class="num">'+fmt(c.prem)+' сум</dd><dt>Экономия клиента</dt><dd class="num" style="color:var(--success-fg)">'+fmt(c.save)+' сум</dd></dl>'+
      (c.floor?'<span class="note">Упёрлись в минимум 0,35 %: ниже ставка не опускается.</span>':'')+
      '<span class="note">Все три меры со скидкой: 0,42 × 0,95 × 0,97 × 0,98 = 0,379 %. Премию движок считает от премии акта: 12 369 000 × 0,903 = 11 170 073 сум.</span></div>'+
    '<p class="note">'+tag("экспертно, не калибровано")+' Размеры скидок — справочник мероприятий движка. Подлежит подтверждению андеррайтером.</p></div>';
}
function controlHTML(){
  var m=S.act.meas,c=measCalc();
  return '<div class="stack"><p class="small muted">Что проверить при продлении договора, чтобы понять, сработали ли меры и верна ли оценка.</p>'+
    '<div class="card"><div class="cap" style="margin-bottom:8px">Проверить при продлении</div><div class="haz">'+
      '<div><b>Подтверждения мероприятий</b><div class="rlist">'+MEAS.map(function(x){var s=m[x.k];return '<div><span>'+x.t+'</span><span class="lvl '+(s.c?'l':s.d?'m':'h')+'">'+(s.c?'подтверждено':s.d?'нет документа':'не выполнено')+'</span></div>';}).join("")+'</div></div>'+
      '<div><b>Убытки за период</b><p>Сколько и каких случаев было за год. Данные — из департамента претензий: «Страховые случаи» из Excel через админку.</p></div>'+
      '<div><b>Повторный осмотр</b><p>Фото стоянки и установленного GPS, табличка с массой, моточасы с приборной панели.</p></div>'+
      '<div><b>Пересчёт балла</b><p>Закрытая опасность возвращает баллы: стоянка +60, моточасы +30, масса уточнена +28. Убытки за период балл снижают.</p></div>'+
    '</div></div>'+
    '<div class="card"><div class="cap" style="margin-bottom:8px">Как изменится ставка</div><div class="tblwrap"><table class="tbl"><thead><tr><th>Что подтверждено</th><th class="r">Балл</th><th class="r">Ставка</th></tr></thead><tbody>'+
      '<tr><td>Ничего, как сейчас</td><td class="r num">337</td><td class="r num">0,42 %</td></tr>'+
      '<tr><td>Меры раздела 4 с документом: '+c.used.length+' из 3</td><td class="r num">337</td><td class="r num">'+pc(c.rate,3)+' %</td></tr>'+
      '<tr><td>При продлении: стоянка, моточасы и масса подтверждены, убытков нет → уровень низкий, × 1,0</td><td class="r num">455</td><td class="r num">0,35 %</td></tr>'+
    '</tbody></table></div>'+
    '<p class="note" style="margin-top:8px">337 + 60 + 30 + 28 = 455, это зона «низкий». 0,35 × 1,0 × скидки мер = 0,316 %, но ставка не ниже минимума 0,35 %. '+tag("экспертно, не калибровано")+' Пересчитает движок по данным продления.</p></div></div>';
}
var lastFull=null;
function fullHTML(){
  if(S.act.sec==null){lastFull=null;return "";}
  var t=L[S.act.lang],s=S.act.sec,scen=s==="scen";
  var title=scen?"Сценарии крупного убытка":t.secs[s-1];
  var anim=lastFull!==s;lastFull=s;
  return '<div class="full'+(anim?' in':'')+'" role="dialog" aria-modal="true" aria-label="'+esc(title)+'"><header class="hd">'+backBtn("closeSec",t.tabs[0])+'<div class="ttl"><b>'+title+'</b><span>'+(scen?'PML · EML · MFL':t.sec+' '+s)+' · № '+CR.act+'</span></div></header><div class="vw" id="fullVw">'+secHTML(s)+'</div></div>';
}

