// Global A object + shared helpers (toast, modal, tabs, init)
window.A = window.A || {};

Object.assign(window.A, {

  _openPanel() {
    const a = document.getElementById('auth'); if(a) a.style.display = 'none';
    const p = document.getElementById('panel'); if(p) p.classList.remove('hidden');
    try { A.init(); } catch(e){ console.error(e); }
  },

  /* ---- TOAST ---- */
  toast(msg, type='ok') {
    const w = document.getElementById('toast-wrap');
    const d = document.createElement('div');
    d.className = `toast ${type}`;
    d.innerHTML = `<i class="fa-solid ${type==='ok'?'fa-check-circle':'fa-circle-exclamation'}"></i> ${msg}`;
    w.appendChild(d);
    setTimeout(()=>{ d.style.animation='tout 0.3s forwards'; setTimeout(()=>d.remove(),350); }, 2800);
  },

  /* ---- INIT ---- */
  async init() {
    A.fetchStats();      // sets up onSnapshot once
    await A.fetchCats();
    A.loadMatches();     // sets up onSnapshot once
    A.checkMonthlyReward();
  },

  /* ---- TAB SWITCH ---- */
  goTab(id) {
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('show'));
    document.querySelectorAll('.nav-btn').forEach(n=>n.classList.remove('active'));
    const _t=document.getElementById('tab-'+id); _t.classList.add('show','fresh'); setTimeout(()=>_t.classList.remove('fresh'),1400);
    const _nv=document.getElementById('nav-'+id); if(_nv) _nv.classList.add('active');
    if(id==='matches') A.loadMatches();
    if(id==='users') A.loadUsers();
  },

  /* ---- MODAL ---- */
  closeModal(id) { document.getElementById(id).classList.remove('show'); }
});

/* ===== UX helpers ===== */
A.esc = (s)=>String(s==null?'':s).replace(/[<>&"']/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));
A._esc = A.esc;

// number count-up animation
A.countTo = (id, to)=>{
  const el=document.getElementById(id); if(!el) return;
  const from=parseInt(el.innerText)||0, t0=performance.now(), dur=700;
  const step=(t)=>{ const p=Math.min(1,(t-t0)/dur); el.innerText=Math.round(from+(to-from)*(1-Math.pow(1-p,3))); if(p<1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
};

// modals: backdrop tap / Esc closes, page scroll lock
new MutationObserver(()=>{ document.body.style.overflow = document.querySelector('.overlay.show') ? 'hidden' : ''; })
  .observe(document.body,{subtree:true,attributes:true,attributeFilter:['class']});
document.addEventListener('click',e=>{ if(e.target.classList && e.target.classList.contains('overlay')) e.target.classList.remove('show'); });
document.addEventListener('keydown',e=>{ if(e.key==='Escape'){ const o=[...document.querySelectorAll('.overlay.show')].pop(); if(o) o.classList.remove('show'); }});

// haptic feedback on tap (mobile)
document.addEventListener('click',e=>{ if(navigator.vibrate && e.target.closest && e.target.closest('button,.menu-btn,.nav-btn')) navigator.vibrate(8); });

// offline banner
const _nb=document.createElement('div'); _nb.id='net-banner'; _nb.innerText='📡 Internet nahi hai — changes save nahi honge'; document.body.appendChild(_nb);
window.addEventListener('offline',()=>_nb.classList.add('show'));
window.addEventListener('online',()=>{ _nb.classList.remove('show'); A.toast('Back online'); });

// koi bhi unhandled error ab silently fail nahi hoga
window.addEventListener('unhandledrejection',e=>{ console.error(e.reason); A.toast('Error: '+((e.reason&&e.reason.message)||'kuch galat hua'),'err'); });
