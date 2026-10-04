
(function(){
"use strict";
var C=[
 ["--bg","#f7f8fb","#0b1020","rv","Фон экрана"],["--surface","#ffffff","#141b2e","rv","Карточки, поля, навигация"],["--surface-warm","#eef4ff","#1b2542","rv","Выделенная подложка"],
 ["--fg","#111827","#f1f5f9","rv","Основной текст"],["--fg-2","#334155","#cbd5e1","rv","Второй текст, подписи полей"],["--muted","#64748b","#94a3b8","rv","Пояснения, неактивное"],
 ["--border","#dbe3ef","#2a3550","rv","Кольцо карточки и поля"],["--border-soft","#edf2f7","#1c2539","rv","Разделители, дорожки"],
 ["--accent","#1D2C8F","#93a3ff","in","Основная кнопка, активный раздел"],["--accent-on","#ffffff","#0b1020","in","Текст на акценте"],["--accent-soft","#e6e9f7","#212c55","dv","Индикатор навигации, мягкие бейджи"],
 ["--success","#22A85A","#34c474","in","Зелёная кнопка, переключатель, прогресс"],["--success-fg","#157a3f","#5bd08e","dv","Текст «Подтверждён»"],["--success-soft","#e3f5ea","#12301f","dv","Подложка успеха"],
 ["--warn","#f59e0b","#fbbf24","rv","Иконка и рамка предупреждения"],["--warn-fg","#b45309","#fbbf24","dv","Текст «риск умеренный», «+10 % без него»"],["--warn-soft","#fef3d7","#3a2b0c","dv","Плашка предупреждения"],
 ["--danger","#ef4444","#f87171","rv","Иконка ошибки, рамка поля"],["--danger-fg","#c62828","#f87171","dv","Текст ошибки"],["--danger-soft","#fde8e8","#3b1719","dv","Плашка ошибки"]
];
var O={rv:'<span class="orig rv">Revolut</span>',in:'<span class="orig in">INSON</span>',dv:'<span class="orig dv">производная</span>'};
document.querySelector("#colorTable tbody").innerHTML=C.map(function(c){
  return '<tr><td><span class="sw" style="background:var('+c[0]+')"></span></td><td><code>'+c[0]+'</code></td><td class="mono">'+c[1]+'</td><td class="mono">'+c[2]+'</td><td>'+(c[0]==="--accent-soft"||c[3]!=="rv"&&c[3]!=="in"?O.dv:O[c[3]])+'</td><td>'+c[4]+'</td></tr>';
}).join("");
document.getElementById("ruler").innerHTML=[4,8,12,16,20,24,32,48].map(function(v){return '<div><code>--space-'+v+'</code><i style="width:'+v*4+'px;max-width:100%"></i></div>';}).join("");
var IC={cases:'<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 9h8M8 13h8M8 17h5"/></svg>',plus:'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/></svg>',chat:'<svg viewBox="0 0 24 24"><path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H10l-5 4V6a1 1 0 0 1 1-1z"/></svg>',user:'<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 20c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>'};
var cur="cases";
function nav(){document.getElementById("demoNav").innerHTML=[["cases","Дела",IC.cases],["new","Новый",IC.plus],["spec","Специалист",IC.chat],["profile","Профиль",IC.user]].map(function(n){return '<button class="'+(cur===n[0]?'on':'')+'" data-n="'+n[0]+'" aria-current="'+(cur===n[0]?'page':'false')+'">'+n[2]+n[1]+'</button>';}).join("");}
nav();
document.getElementById("demoNav").addEventListener("click",function(e){var b=e.target.closest("button");if(b){cur=b.getAttribute("data-n");nav();}});
document.getElementById("moBtn").addEventListener("click",function(){document.getElementById("mo").classList.toggle("go");});
var seg=document.getElementById("themeSeg");
function setT(t){if(t==="auto")document.documentElement.removeAttribute("data-theme");else document.documentElement.setAttribute("data-theme",t);
  [].forEach.call(seg.querySelectorAll("button"),function(b){var on=b.getAttribute("data-t")===t;b.classList.toggle("on",on);b.setAttribute("aria-pressed",on);});
  try{localStorage.setItem("ds-theme",t);}catch(e){}}
seg.addEventListener("click",function(e){var b=e.target.closest("button");if(b)setT(b.getAttribute("data-t"));});
var h=(location.hash||"").slice(1),t=null;try{t=localStorage.getItem("ds-theme");}catch(e){}
if(h==="dark"||h==="light")t=h;
if(t)setT(t);
})();
