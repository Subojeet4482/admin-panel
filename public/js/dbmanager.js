// DB Manager (admin only): per-user + global collection download / upload / delete
import { db, collection, getDocs, doc, setDoc, getDoc, addDoc, updateDoc, deleteDoc, collectionGroup } from './firebase.js';

Object.assign(window.A, {
  _dbmUser:null,
  _dbmFileCB:null,
  _dbmEditCtx:null,
  _dbmUsersCache:null,

  async dbmLoadUsers(force){
    if(A._dbmUsersCache && !force) return A._dbmUsersCache;
    const snap = await getDocs(collection(db,'users'));
    A._dbmUsersCache = snap.docs.map(d=>({id:d.id, ...d.data()}));
    return A._dbmUsersCache;
  },

  async dbmSearch(q){
    q = (q||'').trim().toLowerCase();
    const el = document.getElementById('dbm-search-res');
    if(!q){ el.innerHTML=''; return; }
    const users = await A.dbmLoadUsers();
    const hits = users.filter(u=>{
      return u.id.toLowerCase().includes(q) ||
        (u.appName||'').toLowerCase().includes(q) ||
        (u.name||'').toLowerCase().includes(q) ||
        (u.email||'').toLowerCase().includes(q) ||
        (u.phone||'').toLowerCase().includes(q);
    }).slice(0,30);
    if(!hits.length){ el.innerHTML='<div style="font-size:0.78rem;color:var(--sub);text-align:center;padding:10px;">No user found</div>'; return; }
    el.innerHTML = hits.map(u=>`
      <div class="card" style="margin:0;cursor:pointer;padding:12px;" onclick="A.dbmOpenUser('${u.id}')">
        <div style="font-weight:700;font-size:0.85rem;">${(u.appName||u.name||'User').replace(/</g,'&lt;')}</div>
        <div style="font-size:0.68rem;color:var(--sub);word-break:break-all;">${u.id}</div>
        <div style="font-size:0.7rem;color:var(--sub);">${u.phone||u.email||''}</div>
      </div>`).join('');
  },

  async dbmOpenUser(uid){
    const d = await getDoc(doc(db,'users',uid));
    if(!d.exists()){ return A.toast('User not found','err'); }
    A._dbmUser = {id:uid, data:d.data()};
    document.getElementById('dbm-u-name').innerText = d.data().appName || d.data().name || 'User';
    document.getElementById('dbm-u-uid').innerText = uid;
    document.getElementById('dbm-user-pane').classList.remove('hidden');
  },
  dbmCloseUser(){ A._dbmUser=null; document.getElementById('dbm-user-pane').classList.add('hidden'); },

  _dlJSON(name, obj){
    const blob = new Blob([JSON.stringify(obj,null,2)],{type:'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href=url; a.download=name; a.click();
    setTimeout(()=>URL.revokeObjectURL(url),500);
  },
  _pickFile(cb){
    A._dbmFileCB = cb;
    document.getElementById('dbm-file').click();
  },

  async dbmDownloadUser(){
    if(!A._dbmUser) return;
    const out = {user:A._dbmUser.data, subcollections:{}};
    for(const sub of ['transactions','notifications','matchHistory']){
      try{
        const s = await getDocs(collection(db,'users',A._dbmUser.id,sub));
        out.subcollections[sub] = s.docs.map(d=>({id:d.id, ...d.data()}));
      }catch(e){ out.subcollections[sub]=[]; }
    }
    A._dlJSON(`user_${A._dbmUser.id}.json`, out);
    A.toast('Downloaded','ok');
  },

  dbmUploadUser(){
    if(!A._dbmUser) return;
    A._pickFile(async (json)=>{
      if(!confirm('User document REPLACE kar denge. Continue?')) return;
      const data = json.user || json;
      await setDoc(doc(db,'users',A._dbmUser.id), data);
      if(json.subcollections){
        for(const [sub,arr] of Object.entries(json.subcollections)){
          for(const item of (arr||[])){
            const {id,...rest} = item;
            if(id) await setDoc(doc(db,'users',A._dbmUser.id,sub,id), rest);
            else await addDoc(collection(db,'users',A._dbmUser.id,sub), rest);
          }
        }
      }
      A.toast('User uploaded','ok'); A.dbmOpenUser(A._dbmUser.id);
    });
  },

  dbmEditUser(){
    if(!A._dbmUser) return;
    A._dbmEditCtx = {type:'user', uid:A._dbmUser.id};
    document.getElementById('dbm-edit-title').innerText='Edit User Document';
    document.getElementById('dbm-edit-area').value = JSON.stringify(A._dbmUser.data,null,2);
    document.getElementById('m-dbm-edit').classList.add('show');
  },

  async dbmEditSave(){
    let parsed;
    try{ parsed = JSON.parse(document.getElementById('dbm-edit-area').value); }
    catch(e){ return A.toast('Invalid JSON','err'); }
    const ctx = A._dbmEditCtx;
    if(!ctx) return;
    try{
      if(ctx.type==='user'){
        await setDoc(doc(db,'users',ctx.uid), parsed);
      } else if(ctx.type==='subcol'){
        if(!Array.isArray(parsed)) return A.toast('Array expected','err');
        const old = await getDocs(collection(db,'users',ctx.uid,ctx.sub));
        for(const d of old.docs) await deleteDoc(d.ref);
        for(const item of parsed){
          const {id,...rest} = item;
          if(id) await setDoc(doc(db,'users',ctx.uid,ctx.sub,id), rest);
          else await addDoc(collection(db,'users',ctx.uid,ctx.sub), rest);
        }
      }
      A.toast('Saved','ok'); A.closeModal('m-dbm-edit');
    }catch(e){ A.toast('Err: '+e.message,'err'); }
  },

  async dbmDeleteUserCascade(){
    if(!A._dbmUser) return;
    const uid = A._dbmUser.id;
    if(!confirm('PERMANENTLY DELETE user '+uid+' + saari history + match participation?')) return;
    if(!confirm('Pakka? Yeh undo nahi hoga.')) return;
    try{
      for(const sub of ['transactions','notifications','matchHistory']){
        const s = await getDocs(collection(db,'users',uid,sub));
        for(const d of s.docs) await deleteDoc(d.ref);
      }
      const ms = await getDocs(collection(db,'matches'));
      for(const m of ms.docs){
        const md = m.data();
        let parts = (md.participants||[]).filter(p=>p && p.uid!==uid);
        let slots = (md.takenSlots||[]).filter(sl=>{
          if(typeof sl==='object') return sl.uid!==uid;
          return true;
        });
        let joined = md.joined||0;
        const removedCount = (md.participants||[]).length - parts.length;
        joined = Math.max(0, joined - removedCount);
        if(removedCount>0 || slots.length!==(md.takenSlots||[]).length){
          await updateDoc(m.ref,{participants:parts, takenSlots:slots, joined});
        }
      }
      await deleteDoc(doc(db,'users',uid));
      A._dbmUsersCache=null;
      A.dbmCloseUser();
      document.getElementById('dbm-search').value='';
      document.getElementById('dbm-search-res').innerHTML='';
      A.toast('User & all data removed','ok');
    }catch(e){ A.toast('Err: '+e.message,'err'); }
  },

  async dbmHistDL(sub, filter){
    if(!A._dbmUser) return;
    const s = await getDocs(collection(db,'users',A._dbmUser.id,sub));
    let arr = s.docs.map(d=>({id:d.id, ...d.data()}));
    if(filter && filter!=='all') arr = arr.filter(x=>x.type===filter);
    A._dlJSON(`${A._dbmUser.id}_${sub}_${filter||'all'}.json`, arr);
    A.toast('Downloaded','ok');
  },

  dbmHistUL(sub){
    if(!A._dbmUser) return;
    A._pickFile(async (json)=>{
      if(!Array.isArray(json)) return A.toast('Array expected','err');
      if(!confirm(`Append ${json.length} items to ${sub}?`)) return;
      for(const item of json){
        const {id,...rest} = item;
        if(id) await setDoc(doc(db,'users',A._dbmUser.id,sub,id), rest);
        else await addDoc(collection(db,'users',A._dbmUser.id,sub), rest);
      }
      A.toast('Uploaded','ok');
    });
  },

  async dbmHistEdit(sub){
    if(!A._dbmUser) return;
    const s = await getDocs(collection(db,'users',A._dbmUser.id,sub));
    const arr = s.docs.map(d=>({id:d.id, ...d.data()}));
    A._dbmEditCtx = {type:'subcol', uid:A._dbmUser.id, sub};
    document.getElementById('dbm-edit-title').innerText='Edit '+sub+' (replace all)';
    document.getElementById('dbm-edit-area').value = JSON.stringify(arr,null,2);
    document.getElementById('m-dbm-edit').classList.add('show');
  },

  async dbmHistDel(sub){
    if(!A._dbmUser) return;
    if(!confirm(`Clear all ${sub} for this user?`)) return;
    const s = await getDocs(collection(db,'users',A._dbmUser.id,sub));
    for(const d of s.docs) await deleteDoc(d.ref);
    A.toast('Cleared','ok');
  },

  async dbmAllDL(col){
    const s = await getDocs(collection(db,col));
    const arr = s.docs.map(d=>({id:d.id, ...d.data()}));
    A._dlJSON(`${col}_all.json`, arr);
    A.toast('Downloaded '+arr.length,'ok');
  },
  dbmAllUL(col){
    A._pickFile(async (json)=>{
      if(!Array.isArray(json)) return A.toast('Array expected','err');
      if(!confirm(`Upload ${json.length} docs to ${col}? Existing IDs will be overwritten.`)) return;
      for(const item of json){
        const {id,...rest} = item;
        if(id) await setDoc(doc(db,col,id), rest);
        else await addDoc(collection(db,col), rest);
      }
      A.toast('Uploaded','ok');
    });
  },
  async dbmAllDel(col){
    if(!confirm(`DELETE all ${col}? Irreversible.`)) return;
    if(!confirm('Pakka pakka?')) return;
    const s = await getDocs(collection(db,col));
    for(const d of s.docs) await deleteDoc(d.ref);
    A.toast(`${col} cleared`,'ok');
  },

  async dbmCGDL(sub, filter){
    const s = await getDocs(collectionGroup(db,sub));
    let arr = s.docs.map(d=>({id:d.id, path:d.ref.path, ...d.data()}));
    if(filter && filter!=='all') arr = arr.filter(x=>x.type===filter);
    A._dlJSON(`ALL_${sub}_${filter||'all'}.json`, arr);
    A.toast('Downloaded '+arr.length,'ok');
  },
  async dbmCGDel(sub, filter){
    if(!confirm(`DELETE all ${sub} (${filter||'all'}) across all users?`)) return;
    if(!confirm('Pakka?')) return;
    const s = await getDocs(collectionGroup(db,sub));
    let n=0;
    for(const d of s.docs){
      if(filter && filter!=='all' && d.data().type!==filter) continue;
      await deleteDoc(d.ref); n++;
    }
    A.toast(`${n} deleted`,'ok');
  },

  async dbmLBDL(){
    const out = {};
    const lb = await getDoc(doc(db,'config','leaderboard'));
    out.config = lb.exists()?lb.data():null;
    const users = await getDocs(collection(db,'users'));
    out.users = users.docs.map(d=>{
      const x=d.data();
      const hasNew=(x.depositBalance!==undefined||x.withdrawBalance!==undefined);
      const tot = hasNew ? ((x.depositBalance||0)+(x.withdrawBalance||0)) : (x.balance||0);
      return {id:d.id, name:x.appName||'User', score:tot, kills:x.kills||0, matchesWon:x.matchesWon||0, totalEarned:x.totalEarned||0};
    }).sort((a,b)=>b.score-a.score);
    A._dlJSON('leaderboard.json', out);
    A.toast('Downloaded','ok');
  },
  dbmLBUL(){
    A._pickFile(async (json)=>{
      if(!json.config) return A.toast('config field missing','err');
      if(!confirm('Replace leaderboard config?')) return;
      await setDoc(doc(db,'config','leaderboard'), json.config);
      A.toast('Uploaded','ok');
    });
  },
  async dbmLBReset(){
    if(!confirm('Reset leaderboard config?')) return;
    await setDoc(doc(db,'config','leaderboard'),{lastRewardMonth:'', rewardedAt:null});
    A.toast('Reset','ok');
  }
});

// DB Manager file picker
const _dbmFile = document.getElementById('dbm-file');
if(_dbmFile) _dbmFile.addEventListener('change', async (e)=>{
  const f=e.target.files[0]; e.target.value='';
  if(!f||!A._dbmFileCB) return;
  try{ const txt=await f.text(); const json=JSON.parse(txt); const cb=A._dbmFileCB; A._dbmFileCB=null; await cb(json); }
  catch(err){ A.toast('Bad JSON: '+err.message,'err'); }
});
