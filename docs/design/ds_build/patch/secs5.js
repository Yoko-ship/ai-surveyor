  if(i==="score"){
    var sc=[["Базовый балл","500",""],["Открытая площадка, порт, стройбаза","−60","запрос филиала"],["Работы с механическими повреждениями","−45","запрос филиала"],["Моточасы не зафиксированы","−30","фото"],["Расхождение массы 36 170 / 38 600 кг","−28","табличка / маркировка"],["Убытков за 3 года нет: штрафа нет","+0","запрос филиала"]];
    return '<div class="stack"><div class="card"><div class="gauge" style="max-width:240px">'+gaugeSVG(CR.score)+'<div class="c"><b>337</b><span>из 500 · умеренный</span></div></div></div>'+
      '<div class="card"><div class="cap" style="margin-bottom:8px">Из чего сложился балл</div><div class="tblwrap"><table class="tbl"><thead><tr><th>Признак</th><th class="r">Вклад</th></tr></thead><tbody>'+
      sc.map(function(r){return '<tr><td>'+r[0]+(r[2]?'<div style="margin-top:4px">'+tag(r[2])+'</div>':'')+'</td><td class="r num"><b>'+r[1]+'</b></td></tr>';}).join("")+
      '<tr><td><b>Итого</b></td><td class="r num"><b>337</b></td></tr></tbody></table></div>'+
      '<p class="note" style="margin-top:8px">500 − 60 − 45 − 30 − 28 + 0 = 337. Регион в балл не входит: он учтён поправкой в лестнице ставки (раздел 4), чтобы не считать дважды. '+e+'</p></div>'+
      '<div class="card"><div class="cap" style="margin-bottom:8px">Почему уровень «умеренный»</div><div class="rlist">'+
      [["375–500","низкий · × 1,0"],["250–374","умеренный · × 1,2"],["125–249","повышенный · × 1,4"],["0–124","высокий · × 1,7"]].map(function(r,k){return '<div'+(k===1?' style="font-weight:600"':'')+'><span class="num">'+r[0]+(k===1?' ← 337':'')+'</span><span>'+r[1]+'</span></div>';}).join("")+'</div>'+
      '<p class="note" style="margin-top:8px">Множитель уровня применяется к тарифу политики: 0,35 % × 1,2 = 0,42 %.</p></div>'+
      '<div class="alert"><span class="ic">i</span><div><b>Не является кредитным скорингом</b>Балл описывает риск объекта, а не платёжеспособность клиента. '+e+'</div></div>'+
      goSec(4,"Как оценка стала ставкой — раздел 4 →")+'</div>';
  }
  switch(i){
  case 1: var f1=[["Класс","Спецтехника"],["Тип","Автокран (Truck Crane)"],["Марка/модель","XCMG QY50K5D+"],["Год выпуска","июнь 2026 г."],["Серийный номер (VIN)","LXG•••••••6921 · считан с заводского шильдика"],["Производитель","XCMG, КНР"],["Двигатель","SC9DF340Q61, 248 кВт / 1900 об/мин"],["Грузоподъёмность","50 000 кг при вылете 3 м"],["Полная масса","98 600 кг"],["Габариты","14 375 × 2 650 × 3 480 мм"],["Снаряжённая масса по шильдику","36 170 кг"],["Снаряжённая масса по маркировке бампера","~38 600 кг"],["Место эксплуатации","открытая площадка / порт / стройбаза"]];
    return '<div class="stack"><div class="card"><dl class="kv">'+f1.map(function(r){return '<dt>'+r[0]+'</dt><dd>'+r[1]+'</dd>';}).join("")+'</dl></div>'+
      '<div class="alert"><span class="ic">!</span><div><b>Расхождение в снаряжённой массе</b>По шильдику 36 170 кг, по маркировке бампера ~38 600 кг. До выдачи полиса устранить расхождение.</div></div>'+
      '<p class="note">VIN показан частично. Данные владельца не хранятся.</p></div>';
  case 2: return '<div class="stack"><div class="card"><p class="small">Осмотр проведён по фотоматериалам: виды спереди, сзади, с обоих боковых сторон, шильдик. Комплект признан достаточным.</p>'+
      '<div class="rlist" style="margin-top:12px"><div><span>Фото</span><span class="num">'+S.shots.length+' + шильдик</span></div><div><span>Видимых повреждений</span><span class="lvl l">не выявлено</span></div><div><span>ЛКП</span><span>без сколов и царапин</span></div><div><span>Деформации</span><span>нет</span></div><div><span>Моточасы</span><span class="lvl m">не зафиксированы</span></div><div><span>Паспорт самоходной машины</span><span class="lvl m">не представлен</span></div></div></div>'+
      '<p class="small">Видимых повреждений не выявлено: ЛКП без сколов и царапин, деформаций нет. Показания счётчика моточасов не зафиксированы — данные недоступны. Паспорт самоходной машины не представлен; идентификация по шильдику.</p></div>';
  case 3: return '<div class="stack"><div class="card"><p class="small">Заявленная страховая сумма 2 945 000 000 сум — ~95 % расчётной рыночной стоимости на дату осмотра. В допустимом диапазоне.</p>'+
      '<dl class="kv" style="margin-top:12px"><dt>Страховая сумма</dt><dd class="num">2 945 000 000</dd><dt>Рыночная стоимость, медиана</dt><dd class="num">'+fmt(CR.value)+'</dd><dt>Доля</dt><dd class="num">95 %</dd></dl></div>'+valueHTML()+'</div>';
  case 4: return '<div class="stack"><div class="card"><p class="small">Среда эксплуатации формирует умеренный уровень риска: осадки, вибрация, механические повреждения при работах. Рекомендуемый тариф 0,42 %. Рекомендуется безусловная франшиза.</p>'+
      '<div class="cap" style="margin:12px 0 8px">Оговорки</div><div class="rlist"><div><span>Охраняемое хранение навесного и сменного оборудования</span></div><div><span>Ограничение территории покрытия</span></div><div><span>Повторный осмотр через 12 месяцев или 2 000 моточасов</span></div></div></div>'+
      '<div class="card"><div class="cap" style="margin-bottom:8px">Сценарии убытка PML / EML / MFL</div><div class="tblwrap"><table class="tbl"><thead><tr><th>Сценарий</th><th class="r">Сумма</th><th class="r">Доля</th></tr></thead><tbody>'+
      SCEN.map(function(s){return '<tr><td><b>'+s.k+'</b><div class="note">'+s.p+'</div></td><td class="r num" style="white-space:nowrap">'+mln(s.v)+'</td><td class="r num">'+pc(s.v/2945*100,1)+' %</td></tr>';}).join("")+'</tbody></table></div>'+
      '<p class="note" style="margin-top:8px">Частота — '+tag("экспертно, не калибровано")+'</p>'+
      '<button class="btn btn-s btn-sm" style="margin-top:12px;width:100%" data-a="openSec" data-v="scen">Подробнее о сценариях</button></div>'+
      '<div class="cap">Лестница ставки</div><p class="small muted" style="margin-top:-8px">У каждого шага причина, направление и источник. В премию входят только первые три строки.</p>'+
      '<div class="card"><ol class="ladder">'+ladderHTML()+'</ol></div>'+
      '<div class="cap">Предупредительные мероприятия</div>'+measuresHTML()+'</div>';
  case 5: return '<div class="stack"><div class="card"><p class="small">Техника новая, без повреждений. Сумма соответствует рыночной стоимости. Рекомендован к принятию при соблюдении оговорок. До выдачи полиса андеррайтеру верифицировать паспорт самоходной машины и устранить расхождение в снаряжённой массе.</p>'+
      '<div class="rlist" style="margin-top:12px"><div><span>Рекомендация</span><span class="lvl l">принять при оговорках</span></div><div><span>Верифицировать паспорт самоходной машины</span><span class="lvl m">андеррайтеру</span></div><div><span>Устранить расхождение массы</span><span class="lvl m">андеррайтеру</span></div></div></div>'+
      '<p class="note">Акт сформирован ИИ-сюрвейером INSON, подлежит подтверждению андеррайтером. № '+CR.act+' · 02.10.2026</p></div>';
  }
  return "";
}
