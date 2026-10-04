const fs=require('fs'),p=require('path');const F=p.join(__dirname,'..','tail.html');let s=fs.readFileSync(F,'utf8');
function rep(a,b,all){if(s.indexOf(a)<0)throw new Error('miss '+a.slice(0,70));s=all?s.split(a).join(b):s.replace(a,()=>b);}
const a=s.indexOf("  switch(i){\n  case 1: var hz=["),b=s.indexOf("function lstep(");if(a<0||b<0)throw 'blk';
s=s.slice(0,a)+fs.readFileSync(p.join(__dirname,'secs5.js'),'utf8')+s.slice(b);
// названия и подписи разделов
s=s.replace(/secs:\["Выявление опасностей"[^\]]*\]/,'secs:["Объект и идентификация","Результаты осмотра. Предсуществующие повреждения","Стоимость и страховая сумма","Риск-факторы и франшиза","Заключение и рекомендация"]');
s=s.replace(/sub:\["5 опасностей[^\]]*\]/,'sub:["XCMG QY50K5D+, 2026 · расхождение массы","5 ракурсов · повреждений нет · моточасы не зафиксированы","2 945 000 000 · ~95 % рыночной · в норме","умеренный · 0,42 % · франшиза · сценарии · мероприятия","рекомендован при оговорках"]');
s=s.replace(/secs:\["Xavflarni aniqlash"[^\]]*\]/,'secs:["Obyekt va identifikatsiya","Ko\'rik natijalari. Oldindan mavjud shikastlar","Qiymat va sug\'urta summasi","Xavf omillari va franshiza","Xulosa va tavsiya"]');
s=s.replace(/sub:\["5 ta xavf[^\]]*\]/,'sub:["XCMG QY50K5D+, 2026 · massa farqi","5 ta rakurs · shikast yo\'q · motosoat qayd etilmagan","2 945 000 000 · bozorning ~95 % i · me\'yorda","o\'rtacha · 0,42 % · franshiza · ssenariylar · chora-tadbirlar","shartlar bilan tavsiya etiladi"]');
s=s.replace(/secs:\["Hazard identification"[^\]]*\]/,'secs:["Object and identification","Inspection results. Pre-existing damage","Value and sum insured","Risk factors and deductible","Conclusion and recommendation"]');
s=s.replace(/sub:\["5 hazards[^\]]*\]/,'sub:["XCMG QY50K5D+, 2026 · mass mismatch","5 views · no damage · engine hours not recorded","2 945 000 000 · ~95 % of market · within range","moderate · 0.42 % · deductible · scenarios · measures","recommended subject to conditions"]');
rep('Почему — раздел 3, как законно снизить — раздел 4.','Почему и как законно снизить — раздел 4.');
rep('Sababi — 3-bo\'lim, qonuniy pasaytirish — 4-bo\'lim.','Sababi va qonuniy pasaytirish — 4-bo\'lim.');
rep('Why — section 3; how to lower it lawfully — section 4.','Why, and how to lower it lawfully — section 4.');
rep('— раздел 3 →"','— раздел 4 →"');rep('— 3-bo\'lim →"','— 4-bo\'lim →"');rep('— section 3 →"','— section 4 →"');
rep('data-a="openSec" data-v="3">\'+t.forkMore','data-a="openSec" data-v="4">\'+t.forkMore');
rep('data-a="openSec" data-v="2">\'+t.why','data-a="openSec" data-v="score">\'+t.why');
rep('Подробно — раздел 3.','Подробно — раздел 4.');
rep('Меры закрывают опасности из раздела 1.','Меры закрывают риск-факторы этого раздела.');
rep(' · раздел 1</span>','</span>');
// листы вне разделов
rep('S.act.sec=v==="scen"?v:+v;','S.act.sec=(v==="scen"||v==="score")?v:+v;');
rep('var t=L[S.act.lang],s=S.act.sec,scen=s==="scen";\n  var title=scen?"Сценарии крупного убытка":t.secs[s-1];',
    'var t=L[S.act.lang],s=S.act.sec,scen=s==="scen",sco=s==="score";\n  var title=scen?"Сценарии крупного убытка":sco?"Из чего сложился балл":t.secs[s-1];');
rep("(scen?'PML · EML · MFL':t.sec+' '+s)","(scen?'PML · EML · MFL':sco?'337 из 500 · умеренный':t.sec+' '+s)");
// витрина экранов
s=s.replace(/\["act-haz","1 Выявление опасностей"\],\["act-score","2 Анализ и оценка риска"\],\["act-rate","3 Лестница ставки"\],\["act-meas","4 Мероприятия"\],\["act-ctrl","5 Контроль"\],/,'["act-score","Из чего сложился балл"],["act-s1","1 Объект"],["act-s2","2 Осмотр"],["act-s3","3 Стоимость"],["act-s4","4 Риск-факторы и франшиза"],["act-s5","5 Заключение"],');
s=s.replace(/    case "act-haz":[\s\S]*?case "act-ctrl":[^\n]*\n/,'    case "act-score":S.tab="new";toActDone();S.act.sec="score";break;\n    case "act-s1":S.tab="new";toActDone();S.act.sec=1;break;\n    case "act-s2":S.tab="new";toActDone();S.act.sec=2;break;\n    case "act-s3":S.tab="new";toActDone();S.act.sec=3;break;\n    case "act-s4":S.tab="new";toActDone();S.act.sec=4;S.act.meas.park={d:1,c:1};S.act.meas.gps={d:1,c:0};break;\n    case "act-s5":S.tab="new";toActDone();S.act.sec=5;break;\n');
// объект: модель, год, VIN
rep('XCMG QY50K, 2022','XCMG QY50K5D+, 2026',true);
rep('"•••••••••••••4417"','"LXG•••••••6921"');
fs.writeFileSync(F,s);console.log('ok');
