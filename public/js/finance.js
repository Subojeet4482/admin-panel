// Wallet: deposit / withdraw requests — LIVE (naye requests khud aa jaate hain)
import { db, collection, getDocs, doc, setDoc, getDoc, addDoc, updateDoc, collectionGroup, increment, query, where, onSnapshot } from './firebase.js';

const mapDoc = (d) => {
  const t = d.data();
  t._docId = d.id;
  t._uid = d.ref.parent.parent ? d.ref.parent.parent.id : '?';
  t._order = A._orderNum(t, d.id, t.createdAt);
  return t;
};
const num = (v) => parseFloat(String(v||0).replace(/[^0-9.]/g,'')) || 0;
const ts = (t) => t.createdAt || (t.timestamp && t.timestamp.seconds*1000) || Date.parse(t.date) || 0;
const esc = (s) => String(s==null?'':s).replace(/[<>&"']/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));

Object.assign(window.A, {
  _finType: 'deposit', _finStatus: 'pending', _finSearch: '',
  _pendCache: [], _allCache: [], _finUnsub: null, _finFirst: true,

  _orderNum(t, docId, createdAt){
    const prefix = t.type==='withdraw' ? 'WD' : 'DP';
    const ms = createdAt || (t.createdAt) || (t.timestamp && t.timestamp.seconds*1000) || Date.parse(t.date) || Date.now();
    const d = new Date(ms);
    const ymd = d.getFullYear().toString()+String(d.getMonth()+1).padStart(2,'0')+String(d.getDate()).padStart(2,'0');
    return `${prefix}-${ymd}-${(docId||'XXXX').slice(-4).toUpperCase()}`;
  },

  /* ---- LIVE WATCH: pending requests realtime ---- */
  startFinanceWatch(){
    try{ if(A._finUnsub) A._finUnsub(); }catch(e){}
    A._finFirst = true;
    const onData = (s) => {
      if(!A._finFirst){
        s.docChanges().forEach(ch=>{
          if(ch.type==='added'){
            const t = ch.doc.data();
            if(t.status==='pending') A.toast((t.type==='withdraw'?'💸 New withdrawal':'💰 New deposit')+' request: ₹'+num(t.amount),'ok');
          }
        });
      }
      A._finFirst = false;
      const arr = []; s.forEach(d=>{ const t=mapDoc(d); if(t.status==='pending') arr.push(t); });
      A._pendCache = arr;
      A._renderFinance();
    };
    const start = (q, fallback) => onSnapshot(q, onData, (err)=>{
      console.error('finance watch error', err);
      if(fallback){ A._finUnsub = start(fallback, null); }
      else A.toast('Wallet live error: '+err.message,'err');
    });
    // Primary: sirf pending (index chahiye — na ho to automatically fallback)
    A._finUnsub = start(
      query(collectionGroup(db,'transactions'), where('status','==','pending')),
      collectionGroup(db,'transactions')
    );
  },

  async _fetchAllFin(){
    const list=document.getElementById('finance-list');
    list.innerHTML='<div class="empty-box"><i class="fa-solid fa-spinner fa-spin"></i>Loading history...</div>';
    try{
      const s = await getDocs(collectionGroup(db,'transactions'));
      const arr=[]; s.forEach(d=>arr.push(mapDoc(d)));
      A._allCache = arr;
      A._renderFinance();
    }catch(e){ list.innerHTML='<div class="empty-box">Error: '+esc(e.message)+'</div>'; }
  },

  refreshFinance(){
    if(A._finStatus==='all') return A._fetchAllFin();
    A.startFinanceWatch(); A.toast('Refreshed');
  },
  loadFinance(type){
    A._finType = type;
    if(A._finStatus==='all' && !A._allCache.length) return A._fetchAllFin();
    A._renderFinance();
  },
  setFinStatus(s){
    A._finStatus = s;
    if(s==='all') return A._fetchAllFin();
    A._renderFinance();
  },
  filterFinance(q){ A._finSearch=(q||'').toLowerCase().trim(); A._renderFinance(); },
  copyText(txt){
    try{ navigator.clipboard.writeText(txt); A.toast('Copied'); }
    catch(e){ const i=document.createElement('input'); i.value=txt; document.body.appendChild(i); i.select(); document.execCommand('copy'); i.remove(); A.toast('Copied'); }
  },

  _renderFinance(){
    const list = document.getElementById('finance-list'); if(!list) return;
    const type = A._finType;

    // summary + badges (from pending cache)
    const pd = A._pendCache.filter(t=>t.type!=='withdraw');
    const pw = A._pendCache.filter(t=>t.type==='withdraw');
    const sum = (a)=>a.reduce((x,t)=>x+num(t.amount),0);
    const set = (id,v)=>{ const e=document.getElementById(id); if(e) e.innerText=v; };
    set('fs-dep',pd.length); set('fs-dep-amt','₹'+sum(pd).toFixed(0));
    set('fs-wd',pw.length);  set('fs-wd-amt','₹'+sum(pw).toFixed(0));
    set('cnt-dep',pd.length); set('cnt-wd',pw.length);
    ['cnt-dep','cnt-wd'].forEach((id,i)=>{ const e=document.getElementById(id); if(e) e.classList.toggle('zero',(i?pw:pd).length===0); });
    const nb = document.getElementById('nav-fin-badge');
    if(nb){ const n=A._pendCache.length; nb.style.display=n?'inline-block':'none'; nb.innerText=n; }

    // toggle states
    document.getElementById('btn-dep').classList.toggle('on', type==='deposit');
    document.getElementById('btn-wd').classList.toggle('on', type==='withdraw');
    document.getElementById('st-pending').classList.toggle('on', A._finStatus==='pending');
    document.getElementById('st-all').classList.toggle('on', A._finStatus==='all');

    let items = (A._finStatus==='all' ? A._allCache : A._pendCache).filter(t=> (t.type==='withdraw'?'withdraw':'deposit')===type);
    if(A._finSearch){
      const q=A._finSearch;
      items = items.filter(t => (t._order||'').toLowerCase().includes(q) || (t._uid||'').toLowerCase().includes(q) || String(t.trxId||t.number||'').toLowerCase().includes(q));
    }
    items.sort((a,b)=>ts(b)-ts(a));
    if(!items.length){
      list.innerHTML = `<div class="empty-box"><i class="fa-solid fa-inbox"></i>${A._finStatus==='pending'?'Koi pending '+(type==='deposit'?'deposit':'withdrawal')+' request nahi':'Koi record nahi mila'}</div>`;
      return;
    }

    list.innerHTML = items.map(t=>{
      const uid=t._uid, amt=num(t.amount), st=t.status||'pending';
      const utr = t.trxId||t.number||'';
      let rows = `<div class="fin-row"><span class="k">User</span><span class="v">${esc(uid.substring(0,14))}…</span><button class="copy-btn" onclick="A.copyText('${esc(uid)}')"><i class="fa-regular fa-copy"></i></button></div>`;
      if(utr) rows += `<div class="fin-row"><span class="k">UTR / Tx</span><span class="v">${esc(utr)}</span><button class="copy-btn" onclick="A.copyText('${esc(utr)}')"><i class="fa-regular fa-copy"></i></button></div>`;
      if(type==='withdraw'){
        if(t.method==='BANK'){
          rows += `<div class="fin-row"><span class="k">🏦 Bank</span><span class="v">${esc(t.accountName||'')}<br>A/C: ${esc(t.accountNumber||'')}<br>IFSC: ${esc(t.ifsc||'')}</span><button class="copy-btn" onclick="A.copyText('${esc(t.accountNumber||'')}')"><i class="fa-regular fa-copy"></i></button></div>`;
        } else if(t.method==='CRYPTO'){
          rows += `<div class="fin-row"><span class="k">₿ ${esc(t.coin||'Crypto')}<br>${esc(t.network||'')}</span><span class="v" style="font-size:0.68rem;">${esc(t.walletAddress||'')}</span><button class="copy-btn" onclick="A.copyText('${esc(t.walletAddress||'')}')"><i class="fa-regular fa-copy"></i></button></div>`;
        }
        rows += `<div class="fin-row" style="background:#ecfdf5;border-color:#a7f3d0;"><span class="k" style="color:#047857;">User ko milega</span><span class="v" style="color:#047857;">₹${Math.max(0,amt-2).toFixed(2)} <span style="font-weight:600;">(₹2 fee ke baad)</span></span></div>`;
      }
      const a = (act)=>`A.processTrx('${esc(uid)}','${esc(t._docId)}','${type}','${esc(t.amount)}','${act}')`;
      let actions='';
      if(st==='pending'){
        actions = `<div class="fin-actions">
          <button class="btn btn-success" onclick="${a('approve')}">✅ Accept</button>
          <button class="btn btn-danger" onclick="${a('reject')}">❌ Reject</button>
          ${type==='withdraw'?`<button class="btn btn-outline" style="color:#f59e0b;border-color:#f59e0b;" onclick="${a('refund')}">↩ Refund</button>`:''}
        </div>`;
      } else if(type==='withdraw' && st==='rejected'){
        actions = `<div class="fin-actions"><button class="btn btn-outline" style="color:#f59e0b;border-color:#f59e0b;" onclick="${a('refund')}">↩ Refund</button></div>`;
      }
      return `<div class="fin-card ${esc(st)}">
        <div class="fin-head">
          <div><div class="fin-order">${esc(t._order)}</div><div class="fin-date">${esc(t.method||type)} • ${esc(t.date||'')}</div></div>
          <div><div class="fin-amt" style="color:${type==='deposit'?'var(--success)':'var(--danger)'};">${type==='deposit'?'+':'-'}₹${amt}</div><div style="text-align:right;"><span class="fin-st ${esc(st)}">${esc(st)}</span></div></div>
        </div>
        ${rows}${actions}
      </div>`;
    }).join('');
  },

  async processTrx(uid, trxId, type, amtRaw, action) {
    const labels={approve:'ACCEPT (Success)', reject:'REJECT (No refund)', refund:'REFUND (Return money)'};
    if(!confirm(labels[action]+'?')) return;
    try {
      const amt = parseFloat(amtRaw.toString().replace(/[^0-9.]/g,''));
      // Re-fetch the transaction so we know its current status (don't process twice)
      const trxRef = doc(db,'users',uid,'transactions',trxId);
      const trxSnap = await getDoc(trxRef);
      const trx = trxSnap.data()||{};
      const order = A._orderNum({type}, trxId, trx.createdAt);
      let newStatus = trx.status;
      let notifTitle = '', notifMsg = '';

      if(action==='approve'){
        if(trx.status!=='pending'){ A.toast('Already processed','err'); return; }
        newStatus = 'success';
        if(type==='deposit'){
          const utr = trx.trxId || '';
          if(utr){
            const drRef = doc(db,'deposit_requests',utr);
            const drSnap = await getDoc(drRef);
            if(drSnap.exists()){
              const dr = drSnap.data();
              if(dr.status==='success' && dr.claimed){
                await updateDoc(trxRef,{status:'success', orderNo:order, processedAt:Date.now()});
                A.toast('Already credited by auto-verify','err'); return;
              }
              if(dr.uid && dr.uid!==uid){ A.toast('UTR belongs to another user','err'); return; }
              await updateDoc(drRef,{status:'success', claimed:true, verifiedAt:Date.now(), receivedAmount:amt, verifiedBy:'admin'});
            }
          }
          // Status PEHLE success karo — taaki double-tap pe 2 baar paisa na jaaye
          await updateDoc(trxRef,{status:'success', orderNo:order, processedAt:Date.now()});
          await setDoc(doc(db,'users',uid),{depositBalance:increment(amt), totalDeposited:increment(amt)},{merge:true});
          notifTitle = `Deposit success: ₹${amt}`;
          notifMsg = `Order ${order} • ₹${amt} added to your deposit balance.`;
        } else {
          await updateDoc(trxRef,{status:'success', orderNo:order, processedAt:Date.now()});
          await setDoc(doc(db,'users',uid),{totalWithdrawn:increment(amt)},{merge:true});
          notifTitle = `Withdrawal success: ₹${amt}`;
          notifMsg = `Order ${order} • ₹${amt} withdrawal approved. Funds will reach you shortly.`;
        }
      } else if(action==='reject'){
        if(trx.status!=='pending'){ A.toast('Already processed','err'); return; }
        newStatus = 'rejected';
        await updateDoc(trxRef,{status:'rejected', orderNo:order, processedAt:Date.now()});
        if(type==='deposit'){
          const utr = trx.trxId || '';
          if(utr){
            try { await updateDoc(doc(db,'deposit_requests',utr),{status:'rejected', rejectedAt:Date.now(), verifiedBy:'admin'}); } catch(_){}
          }
        }
        notifTitle = type==='withdraw' ? `Withdrawal rejected: ₹${amt}` : `Deposit rejected: ₹${amt}`;
        notifMsg = `Order ${order} • Request rejected by admin. No refund issued.`;
      } else if(action==='refund'){
        if(type!=='withdraw'){ A.toast('Refund only applies to withdrawals','err'); return; }
        if(trx.status==='refunded'||trx.status==='success'){ A.toast('Cannot refund this','err'); return; }
        await updateDoc(trxRef,{status:'refunded', orderNo:order, processedAt:Date.now()});
        await setDoc(doc(db,'users',uid),{withdrawBalance:increment(amt)},{merge:true});
        notifTitle = `Withdrawal refunded: ₹${amt}`;
        notifMsg = `Order ${order} • ₹${amt} returned to your withdrawal balance.`;
      }

      if(notifTitle){
        await addDoc(collection(db,'users',uid,'notifications'),{title:notifTitle, message:notifMsg, date:new Date().toLocaleDateString(), timestamp:new Date()});
      }
      A.toast('Done!');
      if(A._finStatus==='all') A._fetchAllFin();
    } catch(e){ A.toast('Error: '+e.message,'err'); }
  }
});

// Admin panel khulte hi live watch start
const _baseInit = A.init;
A.init = async function(){ await _baseInit.call(A); A.startFinanceWatch(); };
