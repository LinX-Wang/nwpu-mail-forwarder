/* Apply before styles load; only a local visual preference is persisted. */
'use strict';
(() => {
  const key='nwpu-mail-forwarder-theme';
  let mode='auto';
  const valid=value=>['light','dark','auto'].includes(value);
  try { const stored=localStorage.getItem(key); if(valid(stored)) mode=stored; } catch (_) { /* Storage may be disabled. */ }
  const resolve=(value,hour=new Date().getHours())=>value==='auto'?(hour>=7 && hour<19?'light':'dark'):value;
  function apply() {
    document.documentElement.dataset.theme=resolve(mode);
    document.documentElement.dataset.themeMode=mode;
    document.querySelectorAll('[data-theme-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.themeMode===mode)));
  }
  function setMode(value) {
    if(!valid(value)) return;
    mode=value; try { localStorage.setItem(key,mode); } catch (_) { /* Keep the preference for this page. */ }
    apply();
  }
  apply();
  window.NWPUTheme={setMode,getMode:()=>mode,resolve};
  document.addEventListener('DOMContentLoaded',()=>{
    document.querySelectorAll('[data-theme-mode]').forEach(button=>button.addEventListener('click',()=>setMode(button.dataset.themeMode)));
    apply();
  });
  window.addEventListener('storage',event=>{ if(event.key===key) { mode=valid(event.newValue)?event.newValue:'auto';apply(); } });
  document.addEventListener('visibilitychange',()=>{ if(!document.hidden) apply(); });
  setInterval(()=>{ if(mode==='auto') apply(); },60000);
})();
