/* Тема и бренд InsuranceON: светлая/тёмная по выбору пользователя, логотип INSON в меню.
   Подключается одной строкой на каждой странице: <script src="/theme.js"></script> */
(function () {
  // Мини-приложение: Clear Desk / Field Kit. Остальные страницы сохраняют свою тему.
  if (window.INSON_MINI_APP) {
    document.documentElement.dataset.insonUi = "surveyor";
    const style = document.createElement("style");
    style.textContent = `
    html[data-inson-ui="surveyor"]{
      --paper:#F3F6F8;--card:#FFFFFF;--soft:#EDF2F5;--soft2:#F8FAFB;
      --ink:#172B45;--heading:#1D2C8F;--muted:#586B80;--line:#DCE4EB;
      --accent:#147D47;--accent-dim:#11683D;--accent-bg:#E8F4EC;--accent-line:#91C5A7;
      --action:#147D47;--action-hover:#11683D;--action-pressed:#0D5632;--on-accent:#FFFFFF;
      --grad:#147D47;--good:#147D47;--gold:#95620B;--warn:#94600A;--warn-bg:#FFF5E5;--stop:#B3261E;--on-stop:#FFFFFF;
      --brand-a:#1D2C8F;--brand-b:#147D47;--btn-line:#B8C7D3;--btn-hover:#EDF2F5;--focus:#1D2C8F;--link:#1D2C8F;
      --shadow:0 2px 8px rgba(23,43,69,.035);--capture:#F8FAFB;--control:#F8FAFB;
      --safe-bottom:env(safe-area-inset-bottom,0px);color-scheme:light}
    html[data-inson-ui="surveyor"][data-theme="dark"]{
      --paper:#111A20;--card:#18252D;--soft:#20313B;--soft2:#152129;
      --ink:#EDF4F8;--heading:#F3F7FA;--muted:#A7BCCA;--line:#30434F;
      --accent:#46D588;--accent-dim:#46D588;--accent-bg:#17382C;--accent-line:#397957;
      --action:#46D588;--action-hover:#69E39F;--action-pressed:#33C576;--on-accent:#092619;
      --grad:#46D588;--good:#65D69A;--gold:#F1B34B;--warn:#F1B34B;--warn-bg:#332A1C;--stop:#FF9C91;--on-stop:#291210;
      --brand-a:#EDF4F8;--brand-b:#46D588;--btn-line:#557181;--btn-hover:#243742;--focus:#83B6F5;--link:#A6CAFF;
      --shadow:none;--capture:#152129;--control:#111D24;color-scheme:dark}`;
    document.head.appendChild(style);
    let saved;
    try { saved = localStorage.getItem("surveyor-theme"); } catch (e) {}
    const wanted = new URLSearchParams(location.search).get("theme") || saved;
    document.documentElement.dataset.theme = wanted === "dark" ? "dark" : "light";
    return;
  }
  const BRAND_BLUE = "#1D2C8F", BRAND_GREEN = "#22A85A";
  const css = `
  html[data-theme="light"]{--paper:#F5F7F9;--card:#FFFFFF;--ink:#101C26;--muted:#5C6C78;--line:#DDE4E9;--soft:#EDF1F4;
    --accent:#1E9E56;--accent-dim:#167A43;--accent-soft:#E3F3EA;--gold:#B8860B;--warn:#9A6209;--stop:#B23A2F;--ok:#17734F;--edge:#C9D2DA;--raise:#EDF1F4;--hover:#E3E9EE;
    --sb-paper:#FFFFFF;--sb-line:#DDE4E9;--sb-muted:#5C6C78;--sb-accent:#1E9E56;--sb-dim:#167A43}
  html[data-theme="dark"]{--paper:#0F1418;--card:#161C21;--ink:#E6ECF0;--muted:#8E9BA6;--line:#26303A;--soft:#1C242B;
    --accent:#2BC46E;--accent-dim:#1E8F52;--accent-soft:#17303F;--gold:#E2B44D;--warn:#E0A83F;--stop:#EE8272;--ok:#5CC298;--edge:#4A5057;--raise:#2C3034;--hover:#353A3F;
    --sb-paper:#0F1418;--sb-line:#26303A;--sb-muted:#8E9BA6;--sb-accent:#2BC46E;--sb-dim:#1E8F52}
  html[data-theme="dark"] body{background:var(--paper);color:var(--ink)}
  html[data-theme="light"] body{background:var(--paper);color:var(--ink)}
  .theme-toggle{position:fixed;right:14px;bottom:14px;z-index:99;display:inline-flex;align-items:center;gap:8px;
    font:600 12.5px Manrope,system-ui,sans-serif;color:var(--ink,#E6ECF0);-webkit-text-fill-color:var(--ink,#E6ECF0);background:var(--card,#161C21);border:1px solid var(--line,#26303A);
    border-radius:999px;padding:7px 12px;cursor:pointer;box-shadow:0 6px 18px rgba(0,0,0,.18)}
  .theme-toggle i{width:26px;height:14px;border-radius:999px;background:var(--line,#26303A);position:relative;display:inline-block}
  .theme-toggle i::after{content:"";position:absolute;top:2px;left:2px;width:10px;height:10px;border-radius:50%;background:var(--accent,#2ED3A2);transition:left .15s}
  html[data-theme="light"] .theme-toggle i::after{left:14px}
  .brand-logo{display:inline-flex;align-items:baseline;font:800 22px/1 Manrope,system-ui,sans-serif;letter-spacing:-.02em}
  .brand-logo b{color:${BRAND_BLUE}} .brand-logo b + b{color:${BRAND_GREEN}}
  html:not([data-theme="light"]) .brand-logo b:first-child{color:#8EA9FF}
  .brand-sub{display:block;font:600 10.5px Manrope,system-ui,sans-serif;color:var(--muted,#8E9BA6);margin-top:3px;letter-spacing:.03em}`;
  const style = document.createElement("style"); style.textContent = css; document.head.appendChild(style);

  const root = document.documentElement;
  const saved = (() => { try { return localStorage.getItem("surveyor-theme"); } catch (e) { return null; } })();
  const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  const fromUrl = new URLSearchParams(location.search).get("theme");   // ?theme=light — для ссылок и проверок
  root.dataset.theme = (fromUrl === "light" || fromUrl === "dark") ? fromUrl : (saved || (prefersLight ? "light" : "dark"));
  if (fromUrl) { try { localStorage.setItem("surveyor-theme", root.dataset.theme); } catch (e) {} }

  function logo() {
    return `<span class="brand-logo"><b>INS</b><b>ON</b></span><span class="brand-sub">InsuranceON · сюрвейер</span>`;
  }
  function apply() {
    document.querySelectorAll(".brand").forEach(b => { if (!b.querySelector(".brand-logo")) b.innerHTML = `<div>${logo()}</div>`; });
    // на экране расчёта свой заголовок «Сюрвейер» — заменяем логотипом, чтобы он не дублировал меню
    const h = document.querySelector("header .brand h1");
    if (h && !document.querySelector("header .brand-logo")) h.parentElement.innerHTML = `<div>${logo()}</div>`;
    let t = document.querySelector(".theme-toggle");
    if (!t) {
      t = document.createElement("button"); t.className = "theme-toggle"; t.type = "button";
      t.addEventListener("click", () => {
        root.dataset.theme = root.dataset.theme === "light" ? "dark" : "light";
        try { localStorage.setItem("surveyor-theme", root.dataset.theme); } catch (e) {}
        label();
      });
      document.body.appendChild(t);
    }
    label();
    function label() { t.innerHTML = `<i></i>${root.dataset.theme === "light" ? "Светлая тема" : "Тёмная тема"}`; }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply); else apply();
})();
