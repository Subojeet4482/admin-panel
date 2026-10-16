// Login: email + password + PIN server par check hote hain (Render env). Phir server Firebase token deta hai.
import { auth, signInWithCustomToken, signOut, onAuthStateChanged } from './firebase.js';

/* ---------- API helper (sab modules yahi use karte hain) ---------- */
A.api = async function(path, opt = {}){
  const init = { method: opt.method || ((opt.body || opt.form) ? 'POST' : 'GET'), credentials: 'same-origin',
                 headers: { 'X-Requested-With': 'ffadmin' } };
  if(opt.form) init.body = opt.form;
  else if(opt.body){ init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opt.body); }
  let r; try{ r = await fetch(path, init); }catch(e){ throw new Error('Server se connect nahi ho paya'); }
  let j = {}; try{ j = await r.json(); }catch(e){}
  if(r.status === 401 && j.code === 'auth'){ A._sessionExpired(); throw new Error('Session expire ho gaya — dobara login karo'); }
  if(!r.ok || j.ok === false){ const e = new Error(j.error || ('HTTP ' + r.status)); e.code = j.code; throw e; }
  return j;
};

let opened = false;
const $ = (id) => document.getElementById(id);

function showLogin(msg){
  $('panel').classList.add('hidden');
  $('auth').style.display = 'flex';
  $('a-loading').classList.add('hidden');
  $('login-form').classList.remove('hidden');
  $('a-err2').innerText = msg || '';
}

async function ensureFirebase(){
  const t = await A.api('/api/firebase-token');
  await signInWithCustomToken(auth, t.token);
}

// Panel tabhi khulega jab server session valid ho AUR Firebase token mein admin claim ho
async function tryOpen(){
  if(opened || !A._sessionOk) return;
  const u = auth.currentUser; if(!u) return;
  let claims = {};
  try{ claims = (await u.getIdTokenResult()).claims; }catch(e){}
  if(claims.admin === true){ opened = true; A._openPanel(); return; }
  // purana (non-admin) Firebase session — nayi se badlo
  try{ await signOut(auth); await ensureFirebase(); }catch(e){ showLogin('Firebase login fail: ' + e.message); }
}

Object.assign(window.A, {
  togglePass(el){ const i = $('a-pass'); const s = i.type === 'password'; i.type = s ? 'text' : 'password'; el.className = 'fa-solid ' + (s ? 'fa-eye-slash' : 'fa-eye'); },

  async login(){
    const err = $('a-err'), btn = $('a-btn'); err.innerText = '';
    btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Checking...';
    try{
      const r = await A.api('/api/login', { body: { email: $('a-email').value, password: $('a-pass').value, pin: $('a-pin').value } });
      $('a-pass').value = ''; $('a-pin').value = '';
      if(r.role === 'admin'){ location.reload(); return; }  // lite panel — server ab '/' par usko serve karega
      A._sessionOk = true;
      await ensureFirebase();
      await tryOpen();
    }catch(e){
      err.innerText = '❌ ' + e.message; $('a-pin').value = '';
    }finally{
      btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-lock-open"></i> Login';
    }
  },

  async logout(){
    if(!confirm('Logout?')) return;
    try{ await A.api('/api/logout', { method: 'POST' }); }catch(e){}
    try{ await signOut(auth); }catch(e){}
    location.reload();
  },

  async _sessionExpired(){
    if(A._expiring) return; A._expiring = true;
    try{ await signOut(auth); }catch(e){}
    location.reload();
  }
});

onAuthStateChanged(auth, () => { tryOpen(); });

(async function boot(){
  let s;
  try{ s = await A.api('/api/session'); }
  catch(e){ showLogin(e.message); return; }
  if(!s.ok){
    try{ if(auth.currentUser) await signOut(auth); }catch(e){}
    showLogin(s.ready ? '' : 'Server par login env (OWNER_* / ADMIN_*) set nahi hain');
    return;
  }
  A._sessionOk = true;
  if(!auth.currentUser){ try{ await ensureFirebase(); }catch(e){ showLogin(e.message); return; } }
  await tryOpen();
})();
