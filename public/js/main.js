// Entry point — saare modules yahan load hote hain (order matters: core pehle)
import './firebase.js';
import './core.js';
import './auth.js';
import './dashboard.js';
import './matches.js';
import './finance.js';
import './users.js';
import './dbmanager.js';
import './chat.js';
import './ui-helpers.js';
import './backup.js';
import './data.js';
import './bot.js';
import './server.js';
import './reports.js';

// goTab hook: extra loading for finance / chat / reports / backup
const _origGoTab = A.goTab;
A.goTab = function(id){
  _origGoTab.call(A, id);
  if(id!=='bot') A.botClose();
  if(id==='finance') A._renderFinance();
  if(id==='chat') A.loadChatMod();
  if(id==='reports') A.loadReports('open');
  if(id==='backup') A.bkLoad();
  if(id==='data') A.ddLoad();
  if(id==='bot') A.botOpen();
  if(id==='server') A.srvLoad();
};

/* ===== DOUBLE-TAP PROTECTION =====
   Har write/action function ek baar chal rahi ho to dubara tap ignore hoga,
   button disable ho jaata hai, aur khatam hone ke 0.5s baad hi dobara chalega. */
const LOCKED = [
  'sendNotif','delNotif','sendCash','addCat','delCat','saveSettings',
  'createMatch','delMatch','saveRoom','completeMatch','saveBooya',
  'distributeWinPrize','creditKills','creditTeamWin','processTrx',
  'adjustBal','banUser','unbanUser','verifyUser','unverifyUser','deleteUser',
  'dbmEditSave','dbmDeleteUserCascade','dbmHistDel','dbmAllDel','dbmCGDel','dbmLBReset',
  'resolveReport','pinMsg','softDelMsg','hardDelMsg','banUserChat','deleteGroup',
  'pinChatBanner','clearWorldChat','chatSettings','editBannedWords'
];
const _busy = new Set();
LOCKED.forEach(name=>{
  const fn = A[name];
  if(typeof fn !== 'function') return;
  A[name] = function(...args){
    const key = name+':'+JSON.stringify(args.map(a=>(a && a.nodeType)?'el':a));
    if(_busy.has(key)) return;
    _busy.add(key);
    const ev = window.event;
    const btn = ev && ev.target && ev.target.closest ? ev.target.closest('button') : null;
    if(btn){ btn.disabled = true; btn.style.opacity = '.55'; }
    const done = ()=> setTimeout(()=>{ _busy.delete(key); if(btn){ btn.disabled=false; btn.style.opacity=''; } }, 500);
    let r;
    try { r = fn.apply(A, args); } catch(e){ done(); throw e; }
    Promise.resolve(r).finally(done);
    return r;
  };
});

// panel khulte hi bot ka status dot refresh
const _op = A._openPanel;
A._openPanel = function(){ _op.call(A); A.botLoad(); };
