// Users: list, search, manage (balance, ban, verify, delete)
import { db, collection, getDocs, doc, setDoc, getDoc, addDoc, updateDoc, deleteDoc, increment } from './firebase.js';

let _allUsers = [];

Object.assign(window.A, {
  async loadUsers() {
    const list=document.getElementById('user-list');
    list.innerHTML='<div class="empty-box" style="padding:20px"><i class="fa-solid fa-spinner fa-spin"></i></div>';
    const s=await getDocs(collection(db,'users'));
    _allUsers=[];
    s.forEach(d=>_allUsers.push({id:d.id,...d.data()}));
    A.renderUsers(_allUsers);
  },
  renderUsers(arr) {
    const list=document.getElementById('user-list');
    if(!arr.length){ list.innerHTML='<p style="text-align:center;padding:20px;color:var(--sub);">No users</p>'; return; }
    list.innerHTML=arr.map(u=>{
      const dep=(u.depositBalance!==undefined?u.depositBalance:(u.balance||0));
      const wd=(u.withdrawBalance||0);
      return `
      <div class="card" style="${u.isBanned?'border-color:var(--danger);':''}">
        <div class="card-top">
          <div>
            <div class="card-title">${A.esc(u.appName||'No Name')} ${u.isVerified?'<i class="fa-solid fa-circle-check" style="color:#1d9bf0;font-size:0.85rem;margin-left:4px;" title="Verified"></i>':''} ${u.isBanned?'<span style="background:#fee2e2;color:var(--danger);font-size:0.6rem;padding:2px 7px;border-radius:10px;font-weight:700;">BANNED</span>':''}</div>
            <div class="card-sub"><i class="fa-solid fa-phone"></i> ${A.esc(u.phone||'N/A')}</div>
            <div class="card-sub">🎮 FF ID: ${A.esc(u.gameUid||'---')} ${u.gameName?'• '+A.esc(u.gameName):''}</div>
            <div style="font-size:0.68rem;color:var(--sub);">Player ID: ${A.esc(u.playerId||"---")} | DB: ${u.id.substring(0,8)}</div>
          </div>
          <div style="text-align:right;">
            <div style="font-size:1.05rem;font-weight:900;color:var(--primary);">₹${(dep+wd).toFixed(2)}</div>
            <div style="font-size:0.6rem;color:var(--sub);">Dep ₹${dep.toFixed(0)} • Wd ₹${wd.toFixed(0)}</div>
          </div>
        </div>
        <button class="btn btn-outline btn-full" onclick="A.showUserModal('${u.id}')">Manage</button>
      </div>`;}).join('');
  },
  searchUsers(q) {
    if(!q){ A.renderUsers(_allUsers); return; }
    const f=_allUsers.filter(u=>(u.appName&&u.appName.toLowerCase().includes(q.toLowerCase()))||(u.phone&&u.phone.includes(q))||(u.id===q));
    A.renderUsers(f);
  },
  async showUserModal(uid, name) {
    document.getElementById('u-id').value=uid;
    document.getElementById('u-title').innerText=name||'User';
    document.getElementById('u-ban-info').innerHTML='';
    document.getElementById('u-trx').innerHTML='<p style="font-size:0.8rem;color:var(--sub);text-align:center;">Loading...</p>';
    document.getElementById('m-user').classList.add('show');
    // Details + Ban status
    try {
      const ud=(await getDoc(doc(db,'users',uid))).data()||{};
      document.getElementById('u-title').innerText=ud.appName||'User';
      const dep=(ud.depositBalance!==undefined?ud.depositBalance:(ud.balance||0));
      const wd=(ud.withdrawBalance||0);
      document.getElementById('u-details').innerHTML=`
        <div><b>Name:</b> ${A.esc(ud.appName||'---')}</div>
        <div><b>Free Fire Name:</b> ${A.esc(ud.gameName||'---')}</div>
        <div><b>Free Fire ID:</b> ${A.esc(ud.gameUid||'---')}</div>
        <div><b>Player ID:</b> ${A.esc(ud.playerId||"---")}</div>
        <div><b>Phone:</b> ${A.esc(ud.phone||'---')}</div>
        <div><b>Deposit Balance:</b> ₹${dep.toFixed(2)}</div>
        <div><b>Withdrawal Balance:</b> ₹${wd.toFixed(2)}</div>
        <div><b>Total:</b> ₹${(dep+wd).toFixed(2)}</div>`;
      document.getElementById('u-ban-info').innerHTML = ud.isBanned
        ? `<span style="color:var(--danger);font-weight:700;">⛔ Banned — ${ud.bannedUntil==='permanent'?'Permanent':'Until: '+new Date(ud.bannedUntil).toLocaleString()}</span>`
        : `<span style="color:var(--success);font-weight:700;">✅ Not Banned</span>`;
    } catch(e){}

    // Transactions
    try {
      const s=await getDocs(collection(db,'users',uid,'transactions'));
      let trxs=[]; s.forEach(d=>trxs.push(d.data())); trxs.reverse();
      const el=document.getElementById('u-trx');
      if(!trxs.length){ el.innerHTML='<p style="font-size:0.8rem;color:var(--sub);text-align:center;">No transactions</p>'; return; }
      el.innerHTML=trxs.map(t=>{
        const pos=t.amount.toString().startsWith('+');
        return `<div class="trx-row">
          <div><div style="font-weight:700;font-size:0.78rem;">${A.esc(t.title)}</div>
          <div style="font-size:0.68rem;color:var(--sub);">${A.esc(t.date)} • ${A.esc(t.status)}</div></div>
          <div class="${pos?'trx-pos':'trx-neg'}">${t.amount}</div>
        </div>`;
      }).join('');
    } catch(e){ document.getElementById('u-trx').innerHTML='<p style="color:red;font-size:0.8rem;">Error</p>'; }
  },
  async adjustBal() {
    const uid=document.getElementById('u-id').value;
    const amt=parseFloat(document.getElementById('u-bal').value);
    const btype=document.getElementById('u-bal-type').value;
    if(isNaN(amt)) return A.toast('Amount daalo','err');
    const field = btype==='withdraw' ? 'withdrawBalance' : 'depositBalance';
    const label = btype==='withdraw' ? 'withdrawal' : 'deposit';
    const sysAct = amt>=0 ? 'add' : 'cut';
    const title = `System ${label} ${sysAct}: ₹${Math.abs(amt)}`;
    const updates = { [field]: increment(amt) };
    if(amt>=0 && btype!=='withdraw') updates.totalDeposited = increment(amt);
    if(amt<0 && btype==='withdraw')  updates.totalWithdrawn = increment(Math.abs(amt));
    // Instant: toast + close, fire writes in parallel
    A.toast('Balance updated!'); A.closeModal('m-user');
    return Promise.all([
      setDoc(doc(db,'users',uid),updates,{merge:true}),
      addDoc(collection(db,'users',uid,'notifications'),{title, message:`Admin adjusted your ${label} balance.`, date:new Date().toLocaleDateString(), timestamp:new Date()}),
      addDoc(collection(db,'users',uid,'transactions'),{title, amount:`${amt>=0?'+':'-'}₹${Math.abs(amt)}`, type:amt>=0?'deposit':'withdraw', status:'success', date:new Date().toLocaleDateString(), createdAt:Date.now()})
    ]).then(()=>A.loadUsers()).catch(e=>A.toast('Error: '+e.message,'err'));
  },
  async banUser() {
    const uid=document.getElementById('u-id').value;
    const hrs=parseInt(document.getElementById('u-ban-dur').value);
    if(!confirm('Ban karein?')) return;
    const until = hrs===0 ? 'permanent' : new Date(Date.now()+hrs*3600000).toISOString();
    await setDoc(doc(db,'users',uid),{isBanned:true,bannedUntil:until},{merge:true});
    A.toast('User banned!'); A.closeModal('m-user'); A.loadUsers();
  },
  async unbanUser() {
    const uid=document.getElementById('u-id').value;
    if(!confirm('Unban karein?')) return;
    await updateDoc(doc(db,'users',uid),{isBanned:false,bannedUntil:null});
    A.toast('User unbanned!'); A.closeModal('m-user'); A.loadUsers();
  },
  async verifyUser() {
    const uid=document.getElementById('u-id').value;
    if(!uid) return A.toast('No user','err');
    if(!confirm('Verify (blue tick) dein?')) return;
    await setDoc(doc(db,'users',uid),{isVerified:true,verifiedAt:new Date().toISOString()},{merge:true});
    A.toast('User verified ✓','ok'); A.closeModal('m-user'); A.loadUsers();
  },
  async unverifyUser() {
    const uid=document.getElementById('u-id').value;
    if(!uid) return A.toast('No user','err');
    if(!confirm('Verification remove karein?')) return;
    await setDoc(doc(db,'users',uid),{isVerified:false,verifiedAt:null},{merge:true});
    A.toast('Verification removed','ok'); A.closeModal('m-user'); A.loadUsers();
  },
  async deleteUser() {
    const uid=document.getElementById('u-id').value;
    if(!confirm('Permanently DELETE?')) return;
    await deleteDoc(doc(db,'users',uid));
    A.toast('Deleted'); A.closeModal('m-user'); A.loadUsers();
  }
});
