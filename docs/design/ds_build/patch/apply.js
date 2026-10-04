const fs=require('fs'),p=require('path');const D=p.join(__dirname,'..');
let s=fs.readFileSync(p.join(D,'tail.html'),'utf8');const R=f=>fs.readFileSync(p.join(__dirname,f),'utf8');
function block(a,b,txt){const i=s.indexOf(a),j=s.indexOf(b,i);if(i<0||j<0)throw new Error('block '+a);s=s.slice(0,i)+txt+s.slice(j);}
function rep(a,b,all){if(s.indexOf(a)<0)throw new Error('miss '+a.slice(0,60));s=all?s.split(a).join(b):s.replace(a,()=>b);}
block('var CASES_SEED = [','var PRODUCTS',R('seed.js'));
block('var L = {','/* ===== ответы специалиста',R('lang.js'));
block('function scrCases(){','function scrNew(){',R('cases.js'));
block('function scrAct(){','/* --- быстрый расчёт и ОСГОР --- */',R('act.js'));
rep('Черновик сохраняется, если выйти на середине.','Если выйти на середине, осмотр сохранится в «Делах».');
rep('Продолжить черновик','Продолжить незаконченный осмотр');
rep('<span class="st draft">Черновик</span>','<span class="note">продолжить ›</span>');
rep('sec:null,sent:false}','sec:null,meas:freshMeas()}',true);
rep('sec:null,sent:true}','sec:null,meas:freshMeas()}',true);
rep('prem:null,st:"draft",date:"04.10.2026"','prem:null,cls:"auto",risk:null,st:"draft",date:"04.10.2026"');
rep('else{S.newCase.prem=CR.prem;','else{S.newCase.prem=CR.prem;S.newCase.risk="m";');
rep('upsertNew("ready")','upsertNew("done")',true);
rep('toast("Черновик сохранён в «Делах».")','toast("Осмотр сохранён в «Делах». Продолжите с того же шага.")');
s=s.replace(/  sendUW:function\(\)\{[^\n]*\n/,'');
rep('  askSpec:function(){','  meas:function(v){var p=v.split(":"),m=S.act.meas[p[0]];if(p[1]==="c"){m.c=m.c?0:1;if(m.c)m.d=1;}else{m.d=m.d?0:1;if(!m.d)m.c=0;}var fv=$("fullVw");if(fv){var st=fv.scrollTop;fv.innerHTML=secHTML(S.act.sec);fv.scrollTop=st;var b=fv.querySelector(\'[data-v="\'+v+\'"]\');if(b)b.focus();}else render();},\n  askSpec:function(){');
rep('openSec:function(v){S.act.sec=+v;render();}','openSec:function(v){S.act.sec=v==="scen"?v:+v;render();}');
rep(`<dt>Статус</dt><dd><span class="st '+cs.st+'">'+ST[cs.st]+'</span></dd>`,`<dt>Класс</dt><dd>'+CLS.filter(function(x){return x[0]===cs.cls;})[0][1]+'</dd><dt>Уровень риска</dt><dd>'+(cs.risk?LVL[cs.risk]:"после осмотра")+'</dd>`);
rep('prem:prem,st:"draft",date:"04.10.2026",step:""});S.flow=null;S.tab="cases";S.filter="all";render();toast("Расчёт сохранён в «Делах» как черновик.");','prem:prem,cls:(q.prod==="0215"?"prop":"auto"),risk:{A:"l",B:"m",C:"h",D:"h"}[q.risk],st:"done",date:"04.10.2026",step:""});S.flow=null;S.tab="cases";S.filter="all";render();toast("Расчёт сохранён в «Делах».");');
rep('prem:prem,st:"draft",date:"04.10.2026",step:""});S.flow=null;S.tab="cases";S.filter="all";render();toast("Расчёт ОСГОР сохранён в «Делах» как черновик.");','prem:prem,cls:"liab",risk:null,st:"done",date:"04.10.2026",step:""});S.flow=null;S.tab="cases";S.filter="all";render();toast("Расчёт ОСГОР сохранён в «Делах».");');
rep('Сообщать о решениях андеррайтера','Присылать акты и ответы специалиста в Telegram');
rep('Дела и черновики','Дела и незаконченные осмотры',true);
rep(' ["Акт",[["act-risks","Вкладка Риски"],["act-market","Вкладка Рынок"],["act-object","Вкладка Объект"],["act-section","Раздел: Тариф и премия"],["act-client","Раздел: Предупредительные мероприятия"],["act-uz","Акт на узбекском"]]],',
 ' ["Акт",[["act-scen","Вкладка Сценарии"],["act-market","Вкладка Рынок"],["act-object","Вкладка Объект"],["act-haz","1 Выявление опасностей"],["act-score","2 Анализ и оценка риска"],["act-rate","3 Лестница ставки"],["act-meas","4 Мероприятия"],["act-ctrl","5 Контроль"],["act-pml","Сценарии: подробнее"],["act-uz","Акт на узбекском"]]],');
rep('    case "act-risks":S.tab="new";toActDone();S.act.tab="risks";break;','    case "act-scen":S.tab="new";toActDone();S.act.tab="scen";break;');
rep('    case "act-section":S.tab="new";toActDone();S.act.sec=3;break;\n    case "act-client":S.tab="new";toActDone();S.act.sec=6;break;',
 '    case "act-haz":S.tab="new";toActDone();S.act.sec=1;break;\n    case "act-score":S.tab="new";toActDone();S.act.sec=2;break;\n    case "act-rate":S.tab="new";toActDone();S.act.sec=3;break;\n    case "act-meas":S.tab="new";toActDone();S.act.sec=4;S.act.meas.park={d:1,c:1};S.act.meas.gps={d:1,c:0};break;\n    case "act-ctrl":S.tab="new";toActDone();S.act.sec=5;S.act.meas.park={d:1,c:1};break;\n    case "act-pml":S.tab="new";toActDone();S.act.sec="scen";break;');
rep('function toActDone(){startInspect();S.chk.stage=3;S.chk.a1="Всё верно";S.chk.a2="Принять 3 100 000 000 сум";S.chk.a3="4 200 моточасов";','function toActDone(){startInspect();S.chk.stage=3;S.chk.a1="Всё верно";S.chk.a2="Принять 3 100 000 000 сум";S.chk.a3="Пропустить, уточню позже";');
fs.writeFileSync(p.join(D,'tail.html'),s);console.log('patched');
