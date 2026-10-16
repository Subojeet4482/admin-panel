// Dashboard: stats, monthly reward, categories, notifications, send cash, settings
import { db, collection, getDocs, doc, setDoc, getDoc, addDoc, deleteDoc, increment, query, orderBy, onSnapshot } from './firebase.js';

let _logoB64 = '';
let _qrB64 = '';
let _cats = [];

Object.assign(window.A, {

  /* ---- STATS ---- */
  _statsUnsub: null,
  fetchStats() {
    try {
      if(A._statsUnsub){ try{A._statsUnsub();}catch(e){} }
      const offU = onSnapshot(collection(db,'users'),(s)=>{A.countTo('s-users',s.size);});
      const offM = onSnapshot(collection(db,'matches'),(s)=>{A.countTo('s-matches',s.size);});
      A._statsUnsub = ()=>{offU();offM();};
    } catch(e) {}
  },

  /* ---- MONTHLY LEADERBOARD REWARD ---- */
  async checkMonthlyReward() {
    try {
      const today = new Date();
      if(today.getDate() !== 1) return; // only on 1st
      const monthKey = today.getFullYear()+'-'+(today.getMonth()+1);
      const cfg = (await getDoc(doc(db,'config','leaderboard'))).data() || {};
      if(cfg.lastRewardMonth === monthKey) return; // already done this month
      await setDoc(doc(db,'config','leaderboard'),{lastRewardMonth:monthKey, rewardedAt:new Date()},{merge:true});
      const snap = await getDocs(collection(db,'users'));
      let arr = [];
      snap.forEach(d=>{ const x=d.data(); const hasNew=(x.depositBalance!==undefined||x.withdrawBalance!==undefined); const tot = hasNew ? ((x.depositBalance||0)+(x.withdrawBalance||0)) : (x.balance||0); arr.push({id:d.id, name:x.appName||'User', score:tot}); });
      arr.sort((a,b)=>b.score-a.score);
      const rewards = [10, 5, 5];
      for(let i=0; i<3 && i<arr.length; i++){
        const u = arr[i]; const r = rewards[i];
        await setDoc(doc(db,'users',u.id),{withdrawBalance:increment(r),totalEarned:increment(r)},{merge:true});
        await addDoc(collection(db,'users',u.id,'notifications'),{title:`🏆 Monthly Rank #${i+1} Reward!`, message:`You earned ₹${r} for being on top of this month\'s leaderboard.`, date:new Date().toLocaleDateString(), timestamp:new Date()});
        await addDoc(collection(db,'users',u.id,'transactions'),{title:`Monthly Leaderboard Rank #${i+1}`, amount:`+₹${r}`, type:'deposit', status:'success', date:new Date().toLocaleDateString(), timestamp:new Date(), createdAt:Date.now()});
      }
      A.toast('Monthly rewards distributed!');
    } catch(e){ console.error('reward error', e); }
  },

  /* ---- CATEGORIES ---- */
  showCatModal() { document.getElementById('m-cat').classList.add('show'); A.renderCats(); },
  async fetchCats() {
    try {
      const s = await getDocs(collection(db,'categories'));
      _cats = [];
      s.forEach(d => _cats.push({id:d.id,...d.data()}));
      const sel = document.getElementById('m-type');
      if(sel) sel.innerHTML = _cats.length ? _cats.map(c=>`<option>${A.esc(c.name)}</option>`).join('') : '<option>Solo</option>';
    } catch(e) {}
  },
  renderCats() {
    const box = document.getElementById('cat-chips');
    box.innerHTML = _cats.map(c=>`
      <div style="display:inline-flex;align-items:center;background:#f1f5f9;border:1px solid var(--border);padding:7px 14px;border-radius:20px;font-size:0.82rem;font-weight:600;gap:8px;">
        ${A.esc(c.name)}
        <span style="color:var(--danger);cursor:pointer;font-size:1rem;" onclick="A.delCat('${c.id}')">×</span>
      </div>`).join('');
  },
  async addCat() {
    const n = document.getElementById('cat-new').value.trim();
    if(!n) return;
    await addDoc(collection(db,'categories'),{name:n});
    document.getElementById('cat-new').value='';
    await A.fetchCats(); A.renderCats(); A.toast('Category added!');
  },
  async delCat(id) {
    if(!confirm('Remove?')) return;
    await deleteDoc(doc(db,'categories',id));
    await A.fetchCats(); A.renderCats();
  },

  /* ---- NOTIFICATIONS ---- */
  showNotifModal() { document.getElementById('m-notif').classList.add('show'); A.loadNotifManage(); },
  async sendNotif() {
    const t = document.getElementById('n-title').value;
    const m = document.getElementById('n-msg').value;
    if(!t||!m) return A.toast('Fill all fields','err');
    if(!confirm('Send to ALL users?')) return;
    await addDoc(collection(db,'notifications'),{title:t,message:m,date:new Date().toLocaleDateString(),timestamp:new Date()});
    A.toast('Sent!');
    document.getElementById('n-title').value='';
    document.getElementById('n-msg').value='';
    A.loadNotifManage();
  },
  async loadNotifManage() {
    const box=document.getElementById('notif-manage-list');
    box.innerHTML='<div class="empty-box" style="padding:20px"><i class="fa-solid fa-spinner fa-spin"></i></div>';
    try {
      const s=await getDocs(query(collection(db,'notifications'),orderBy('timestamp','desc')));
      if(s.empty){ box.innerHTML='<p style="font-size:0.8rem;color:var(--sub);text-align:center;">No broadcasts</p>'; return; }
      let html='';
      s.forEach(d=>{const n=d.data(); html+=`<div class="trx-row"><div><div style="font-weight:700;font-size:0.78rem;">${A.esc(n.title)}</div><div style="font-size:0.68rem;color:var(--sub);">${A.esc(n.date)}</div></div><span style="color:var(--danger);cursor:pointer;font-size:1.1rem;" onclick="A.delNotif('${d.id}')">🗑</span></div>`;});
      box.innerHTML=html;
    } catch(e){ box.innerHTML='<p style="color:red;font-size:0.8rem;">Error</p>'; }
  },
  async delNotif(id) {
    if(!confirm('Delete this broadcast?')) return;
    await deleteDoc(doc(db,'notifications',id));
    A.toast('Deleted'); A.loadNotifManage();
  },
  async sendCash() {
    const uid=document.getElementById('cash-uid').value.trim();
    const type=document.getElementById('cash-type').value;
    const amt=parseFloat(document.getElementById('cash-amt').value);
    if(!uid||isNaN(amt)) return A.toast('UID & amount daalo','err');
    const field = type==='withdraw' ? 'withdrawBalance' : 'depositBalance';
    const label = type==='withdraw' ? 'withdrawal' : 'deposit';
    try {
      // Deductions happen instantly (admin debit)
      if(amt < 0){
        const title = `System ${label} cut: ₹${Math.abs(amt)}`;
        const updates = { [field]: increment(amt) };
        if(type==='withdraw') updates.totalWithdrawn = increment(Math.abs(amt));
        A.toast('Amount deducted');
        document.getElementById('cash-uid').value=''; document.getElementById('cash-amt').value='';
        await Promise.all([
          setDoc(doc(db,'users',uid),updates,{merge:true}),
          addDoc(collection(db,'users',uid,'notifications'),{title, message:`Admin deducted ₹${Math.abs(amt)} from your ${label} balance.`, date:new Date().toLocaleDateString(), timestamp:new Date()}),
          addDoc(collection(db,'users',uid,'transactions'),{title, amount:`-₹${Math.abs(amt)}`, type:'withdraw', status:'success', date:new Date().toLocaleDateString(), createdAt:Date.now()})
        ]);
        return;
      }
      // POSITIVE = admin instantly credits balance
      const title = `🎁 You received ₹${amt} from Admin`;
      const message = `An admin has added ₹${amt} to your ${label} balance.`;
      const updates = { [field]: increment(amt) };
      if(type!=='withdraw') updates.totalDeposited = increment(amt);
      A.toast('Amount credited to user!');
      document.getElementById('cash-uid').value=''; document.getElementById('cash-amt').value='';
      await Promise.all([
        setDoc(doc(db,'users',uid),updates,{merge:true}),
        addDoc(collection(db,'users',uid,'notifications'),{title, message, date:new Date().toLocaleDateString(), timestamp:new Date()}),
        addDoc(collection(db,'users',uid,'transactions'),{title:`Admin credit: ₹${amt}`, amount:`+₹${amt}`, type:'deposit', status:'success', date:new Date().toLocaleDateString(), createdAt:Date.now()})
      ]);
    } catch(e){ A.toast('Error: '+e.message,'err'); }
  },

  /* ---- LOGO UPLOAD (base64) ---- */
  selectLogo(input) {
    if(!input.files||!input.files[0]) return;
    const f = input.files[0];
    if(!f.type.startsWith('image/')) return A.toast('Sirf image select karo','err');
    const msg = document.getElementById('logo-msg');
    msg.style.color='#f59e0b'; msg.innerText='Processing...';
    const r = new FileReader();
    r.onload = e => {
      _logoB64 = e.target.result;
      const img = document.getElementById('logo-img');
      const em = document.getElementById('logo-emoji');
      img.src = _logoB64; img.style.display='block'; em.style.display='none';
      msg.style.color='#10b981';
      msg.innerHTML='✅ Logo ready! Save karo.';
      A.toast('Logo select hua! Save karo.');
    };
    r.onerror = () => { msg.style.color='#ef4444'; msg.innerText='❌ Error. Dobara try karo.'; };
    r.readAsDataURL(f);
  },

  /* ---- QR UPLOAD (base64, compressed) ---- */
  selectQr(input) {
    if(!input.files||!input.files[0]) return;
    const f = input.files[0];
    if(!f.type.startsWith('image/')) return A.toast('Sirf image select karo','err');
    const msg = document.getElementById('qr-msg');
    msg.style.color='#f59e0b'; msg.innerText='Processing...';
    const r = new FileReader();
    r.onload = e => {
      const tmp = new Image();
      tmp.onload = () => {
        const max=400; let w=tmp.width, h=tmp.height;
        if(w>h && w>max){ h=h*max/w; w=max; } else if(h>max){ w=w*max/h; h=max; }
        const cv=document.createElement('canvas'); cv.width=w; cv.height=h;
        cv.getContext('2d').drawImage(tmp,0,0,w,h);
        _qrB64 = cv.toDataURL('image/jpeg',0.8);
        const img = document.getElementById('qr-img');
        const em = document.getElementById('qr-emoji');
        img.src = _qrB64; img.style.display='block'; em.style.display='none';
        msg.style.color='#10b981';
        msg.innerHTML='✅ QR ready! Save karo.';
        A.toast('QR select hua! Save karo.');
      };
      tmp.onerror=()=>{ msg.style.color='#ef4444'; msg.innerText='❌ Error.'; };
      tmp.src = e.target.result;
    };
    r.onerror = () => { msg.style.color='#ef4444'; msg.innerText='❌ Error.'; };
    r.readAsDataURL(f);
  },

  /* ---- BANNER ROWS (multi-banner) ---- */
  _renderBannerRows(list) {
    const box = document.getElementById('banner-list');
    box.innerHTML = '';
    (list && list.length ? list : ['']).forEach((url,i)=>{
      const row = document.createElement('div');
      row.style.cssText='display:flex;gap:6px;align-items:center;';
      row.innerHTML = `<input class="inp banner-inp" style="margin:0;flex:1;" placeholder="https://... banner image URL" value="${(url||'').replace(/"/g,'&quot;')}">
        <button class="btn btn-danger" style="padding:10px 12px;" onclick="A.removeBannerRow(this)"><i class="fa-solid fa-trash"></i></button>`;
      box.appendChild(row);
    });
  },
  addBannerRow() {
    const box = document.getElementById('banner-list');
    const row = document.createElement('div');
    row.style.cssText='display:flex;gap:6px;align-items:center;';
    row.innerHTML = `<input class="inp banner-inp" style="margin:0;flex:1;" placeholder="https://... banner image URL">
      <button class="btn btn-danger" style="padding:10px 12px;" onclick="A.removeBannerRow(this)"><i class="fa-solid fa-trash"></i></button>`;
    box.appendChild(row);
  },
  removeBannerRow(btn) { btn.parentElement.remove(); },

  /* ---- SETTINGS ---- */
  async showSettingsModal() {
    _logoB64 = ''; _qrB64 = '';
    document.getElementById('qr-img').style.display='none';
    document.getElementById('qr-emoji').style.display='block';
    document.getElementById('qr-msg').innerText='';
    try {
      const d = (await getDoc(doc(db,'config','banner'))).data()||{};
      let banners = Array.isArray(d.banners) ? d.banners : (d.url ? [d.url] : []);
      A._renderBannerRows(banners);
      document.getElementById('s-notice').value = d.notice||'';
      document.getElementById('s-support').value = d.supportNumber||'';
      document.getElementById('s-upi').value = d.upi||'';
      document.getElementById('s-coin').value = d.cryptoCoin||'';
      document.getElementById('s-addr').value = d.cryptoAddr||'';
      document.getElementById('s-wa-name').value = d.whatsappName||'';
      document.getElementById('s-wa-link').value = d.whatsappLink||d.whatsappNumber||'';
      document.getElementById('s-tg-name').value = d.telegramName||'';
      document.getElementById('s-tg-link').value = d.telegramLink||d.telegramChannel||'';
      if(d.qrUrl) {
        const img=document.getElementById('qr-img');
        img.src=d.qrUrl; img.style.display='block';
        document.getElementById('qr-emoji').style.display='none';
        document.getElementById('qr-msg').innerHTML='<span style="color:#10b981;">✅ QR already set — naya upload karo change ke liye</span>';
      }
    } catch(e) { A.toast('Load error: '+e.message,'err'); }
    document.getElementById('m-settings').classList.add('show');
  },
  async saveSettings() {
    try {
      const existing = (await getDoc(doc(db,'config','banner'))).data() || {};
      const banners = Array.from(document.querySelectorAll('#banner-list .banner-inp'))
        .map(i => i.value.trim()).filter(Boolean);
      const data = {
        ...existing,
        banners,
        url: banners[0] || '',
        notice: document.getElementById('s-notice').value,
        supportNumber: document.getElementById('s-support').value,
        upi: document.getElementById('s-upi').value,
        cryptoCoin: document.getElementById('s-coin').value,
        cryptoAddr: document.getElementById('s-addr').value,
        whatsappName: document.getElementById('s-wa-name').value.trim(),
        whatsappLink: document.getElementById('s-wa-link').value.trim(),
        telegramName: document.getElementById('s-tg-name').value.trim(),
        telegramLink: document.getElementById('s-tg-link').value.trim(),
      };
      if(_qrB64) data.qrUrl = _qrB64;
      await setDoc(doc(db,'config','banner'), data);
      _logoB64 = ''; _qrB64 = '';
      A.toast('Settings saved!');
      A.closeModal('m-settings');
    } catch(e) {
      A.toast('Save Error: ' + e.message, 'err');
    }
  }
});
