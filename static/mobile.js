if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
let installPrompt;
window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;const b=document.getElementById('install-pwa');if(b){b.hidden=false;b.onclick=async()=>{await installPrompt.prompt();installPrompt=null;b.hidden=true;};}});
window.addEventListener('appinstalled',()=>{const b=document.getElementById('install-pwa');if(b)b.hidden=true;});
