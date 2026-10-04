function scrCases(){
  var counts={all:S.cases.length};
  S.cases.forEach(function(c){counts[c.cls]=(counts[c.cls]||0)+1;});
  var body='<div class="stack">'+
    '<button class="bignew" data-a="startInspect"><span class="pl">'+IC.plus2+'</span><span><b>Новый осмотр</b><span>Снять объект, проверить данные, получить акт</span></span></button>'+
    '<div class="search">'+IC.search+'<input class="inp" id="q" type="search" placeholder="Объект, продукт или номер акта" value="'+esc(S.query)+'" aria-label="Поиск по делам"></div>'+
    '<div class="chips scroll" role="tablist" aria-label="Класс страхования">'+CLS.map(function(x){return '<button class="chip'+(S.filter===x[0]?' on':'')+'" data-a="filter" data-v="'+x[0]+'">'+x[1]+'<span class="c">'+(counts[x[0]]||0)+'</span></button>';}).join("")+'</div>'+
    '<div id="caseList" class="cases"></div></div>';
  return {head:head("Дела","Ташкентский областной филиал",'',' <button class="ava" data-a="tab" data-v="profile" aria-label="Профиль">ДМ</button>'),body:body,nav:true};
}
function caseCard(c){
  return '<button class="case" data-a="openCase" data-v="'+c.id+'"><div class="top"><span><span class="code">'+esc(c.code)+'</span>'+esc(c.prod)+'</span><span class="num">'+c.date+'</span></div>'+
    '<div class="obj">'+esc(c.obj)+'</div>'+
    '<div class="bot">'+(c.prem?'<span class="prem num">'+fmt(c.prem)+'<small>сум</small></span>':'<span class="small muted">'+esc(c.step||"")+'</span>')+riskTag(c.risk)+'</div></button>';
}
function caseListHTML(){
  if(S.loading||S.pinSk){
    var sk='';for(var i=0;i<4;i++){sk+='<div class="skcard"><div class="sk" style="height:12px;width:40%"></div><div class="sk" style="height:16px;width:85%"></div><div style="display:flex;justify-content:space-between"><div class="sk" style="height:14px;width:35%"></div><div class="sk" style="height:20px;width:28%;border-radius:999px"></div></div></div>';}
    return sk;
  }
  var q=S.query.trim().toLowerCase();
  var list=S.cases.filter(function(c){
    if(S.filter!=="all"&&c.cls!==S.filter)return false;
    if(!q)return true;
    return (c.obj+" "+c.code+" "+c.prod+" "+(c.no||"")).toLowerCase().indexOf(q)>=0;
  });
  if(!list.length){
    return '<div class="empty"><div class="eic">'+IC.search+'</div><b>Ничего не нашлось</b><p>'+(q?'По запросу «'+esc(S.query)+'» дел нет. ':'В этом классе дел пока нет. ')+'Проверьте написание или покажите все классы.</p><button class="btn btn-s btn-sm" data-a="resetFilter">Сбросить поиск и класс</button></div>';
  }
  return list.map(caseCard).join("");
}

