const fs=require('fs'),p=require('path');
const D=__dirname, SP=p.join(D,'..');
const src=fs.readFileSync(p.join(SP,'surveyor_prototype.html'),'utf8').split(/\r?\n/);
let tail=src.slice(365).join('\n'); // markup + script (line 366..)
const rep=[
 [/var\(--ink\)/g,'var(--fg)'],[/var\(--brand\)/g,'var(--accent)'],[/var\(--bad-soft\)/g,'var(--danger-soft)'],
 [/var\(--bad\)/g,'var(--danger-fg)'],[/var\(--warn\)/g,'var(--warn-fg)'],[/"--bad"/g,'"--danger"'],[/"--ok"/g,'"--success"'],
 [/var\(--sans\)/g,'var(--font-sans)'],[/var\(--mono\)/g,'var(--font-mono)'],
 [/margin-top:10px/g,'margin-top:12px'],[/margin-top:6px/g,'margin-top:8px'],[/margin-top:3px/g,'margin-top:4px'],[/margin-top:-6px/g,'margin-top:-8px'],
 [/margin-bottom:6px/g,'margin-bottom:8px'],[/margin-bottom:10px/g,'margin-bottom:12px'],
 [/gap:6px/g,'gap:8px'],[/gap:10px/g,'gap:12px'],[/padding:10px 0/g,'padding:12px 0'],[/padding-top:120px/g,'padding-top:96px'],
 [/font-size:28px/g,'font-size:36px'],[/font-size:22px;font-weight:800;letter-spacing:-.02em/g,'font:var(--t-title-l);letter-spacing:-.02em'],
 [/font-size:20px"/g,'font:var(--t-title-l);letter-spacing:-0.03em"'],[/font-size:17px/g,'font-size:16px'],
 [/font:700 11px var\(--font-mono\)/g,'font:700 12px var(--font-mono)'],[/height:10px;width:60%/g,'height:12px;width:60%'],
];
for(const [a,b] of rep) tail=tail.replace(a,b);
// с 04.10 разметка и скрипт правятся в ds_build/tail.html (уже с заменами выше)
if(fs.existsSync(p.join(D,"tail.html"))) tail=fs.readFileSync(p.join(D,"tail.html"),"utf8");
const head=fs.readFileSync(p.join(D,'head.html'),'utf8');
const css=fs.readFileSync(p.join(D,'tokens.css'),'utf8')+'\n'+fs.readFileSync(p.join(D,'app.css'),'utf8');
fs.writeFileSync(p.join(SP,'surveyor_prototype_ds.html'),head+'<style>\n'+css+'</style>\n'+tail);
const m=tail.match(/<script>([\s\S]*)<\/script>/);fs.writeFileSync(p.join(D,'app.js'),m[1]);
console.log('ok', (head.length+css.length+tail.length));
// страница системы
const dsHead=head.replace('<title>Сюрвейер INSON</title>','<title>Дизайн-система Сюрвейера</title>');
const body=fs.readFileSync(p.join(D,'ds_body.html'),'utf8');
fs.writeFileSync(p.join(SP,'surveyor_design_system.html'),dsHead+'<style>\n'+css+'</style>\n'+body);
const m2=body.match(/<script>([\s\S]*)<\/script>/);fs.writeFileSync(p.join(D,'ds.js'),m2[1]);
