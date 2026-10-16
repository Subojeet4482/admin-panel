// Chat moderation: world chat, groups, DMs, banner, rules, banned words
import { db, collection, getDocs, doc, setDoc, getDoc, updateDoc, deleteDoc, collectionGroup, query, orderBy, onSnapshot, limit } from './firebase.js';

A.chatSection = 'world';
A.chatModItems = [];
A._chatModUnsub = null;

A.loadChatMod = async function(){
  try{ if(A._chatModUnsub) A._chatModUnsub(); }catch(e){}
  const list = document.getElementById('chat-mod-list');
  list.innerHTML = '<div style="padding:20px;color:var(--sub);text-align:center;">Loading...</div>';
  try{
    if(A.chatSection==='world'){
      const q1 = query(collection(db,'world_messages'), orderBy('createdAt','desc'), limit(150));
      A._chatModUnsub = onSnapshot(q1,(s)=>{
        A.chatModItems = []; s.forEach(d=>A.chatModItems.push({id:d.id,...d.data(), _col:'world_messages'}));
        A.renderChatMod();
      });
    } else if(A.chatSection==='groups'){
      const s = await getDocs(query(collection(db,'groups'), limit(50)));
      A.chatModItems = []; s.forEach(d=>A.chatModItems.push({id:d.id, isGroup:true, ...d.data()}));
      A.renderChatMod();
    } else {
      // Recent DMs via collectionGroup on messages
      try{
        const s = await getDocs(query(collectionGroup(db,'messages'), orderBy('createdAt','desc'), limit(100)));
        A.chatModItems = []; s.forEach(d=>{ const p = d.ref.path; A.chatModItems.push({id:d.id, _path:p, ...d.data()}); });
      }catch(e){ A.chatModItems = []; }
      A.renderChatMod();
    }
  }catch(e){ list.innerHTML = '<div style="padding:20px;color:#ef4444;">'+e.message+'</div>'; }
};
A.renderChatMod = function(){
  const list = document.getElementById('chat-mod-list');
  const flt = (document.getElementById('cm-search')||{}).value || '';
  const q = flt.toLowerCase();
  const rows = A.chatModItems.filter(m=>!q || (m.text||'').toLowerCase().includes(q) || (m.uid||'').includes(q) || (m.name||'').toLowerCase().includes(q));
  if(!rows.length){ list.innerHTML = '<div style="padding:20px;color:var(--sub);text-align:center;">Empty</div>'; return; }
  list.innerHTML = rows.map(m=>{
    if(m.isGroup) return `<div class="card"><div style="display:flex;justify-content:space-between;align-items:center;"><div><div style="font-weight:800;">${A._esc(m.name||'Group')}</div><div style="color:var(--sub);font-size:0.75rem;">${(m.members||[]).length} members · ${m.public?'Public':'Private'}</div></div><div style="display:flex;gap:6px;"><button class="btn btn-outline" style="padding:6px 10px;font-size:0.75rem;" onclick="A.viewGroup('${m.id}')">View</button><button class="btn btn-danger" style="padding:6px 10px;font-size:0.75rem;" onclick="A.deleteGroup('${m.id}')">Del</button></div></div></div>`;
    const t = m.createdAt && m.createdAt.seconds ? new Date(m.createdAt.seconds*1000).toLocaleString() : '';
    const path = m._col ? m._col : (m._path||'').replace(/\/[^/]+$/,'');
    const content = m.deleted?'<i>deleted</i>' : (m.text||('['+(m.type||'media')+'] '+(m.fileName||'')));
    return `<div class="card">
      <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start;">
        <div style="flex:1;min-width:0;">
          <div style="font-weight:800;font-size:0.85rem;">${A._esc(m.name||'User')} <span style="color:var(--sub);font-weight:500;font-size:0.72rem;">${(m.uid||'').slice(0,10)}</span></div>
          <div style="color:var(--text);font-size:0.85rem;margin-top:4px;word-break:break-word;">${A._esc(content)}</div>
          <div style="color:var(--sub);font-size:0.7rem;margin-top:6px;">${t} ${m.pinned?'· 📌 pinned':''} ${m.edited?'· edited':''}</div>
        </div>
      </div>
      <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap;">
        <button class="btn btn-outline" style="padding:6px 10px;font-size:0.72rem;" onclick="A.pinMsg('${m.id}','${path}',${!m.pinned})"><i class="fa-solid fa-thumbtack"></i> ${m.pinned?'Unpin':'Pin'}</button>
        <button class="btn btn-outline" style="padding:6px 10px;font-size:0.72rem;" onclick="A.softDelMsg('${m.id}','${path}')"><i class="fa-solid fa-eye-slash"></i> Hide</button>
        <button class="btn btn-danger" style="padding:6px 10px;font-size:0.72rem;" onclick="A.hardDelMsg('${m.id}','${path}')"><i class="fa-solid fa-trash"></i> Delete</button>
        <button class="btn btn-outline" style="padding:6px 10px;font-size:0.72rem;" onclick="A.banUserChat('${m.uid}')"><i class="fa-solid fa-user-slash"></i> Ban user</button>
      </div>
    </div>`;
  }).join('');
};
A.filterChatMod = function(){ A.renderChatMod(); };
A._esc = function(s){ return String(s||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c])); };
A.pinMsg = async function(id, path, on){ try{ await updateDoc(doc(db, ...path.split('/'), id),{pinned:on}); A.toast(on?'Pinned':'Unpinned'); }catch(e){ A.toast(e.message); } };
A.softDelMsg = async function(id, path){ try{ await updateDoc(doc(db, ...path.split('/'), id),{deleted:true, text:'', hiddenBy:'admin'}); A.toast('Hidden'); }catch(e){ A.toast(e.message); } };
A.hardDelMsg = async function(id, path){ if(!confirm('Permanently delete?')) return; try{ await deleteDoc(doc(db, ...path.split('/'), id)); A.toast('Deleted'); }catch(e){ A.toast(e.message); } };
A.banUserChat = async function(uid){ if(!uid) return; const days = parseInt(prompt('Ban days (0 = permanent):','7')||'0'); const until = days>0?Date.now()+days*86400000:0; try{ await updateDoc(doc(db,'users',uid),{chatBanned:true, chatBannedUntil: until}); A.toast('Banned'); }catch(e){ A.toast(e.message); } };
A.viewGroup = async function(gid){
  const s = await getDoc(doc(db,'groups',gid)); if(!s.exists()){A.toast('Not found'); return;}
  const g = s.data();
  alert('Group: '+g.name+'\nMembers: '+(g.members||[]).length+'\nPublic: '+!!g.public+'\nOwner: '+(g.ownerUid||'-'));
};
A.deleteGroup = async function(gid){ if(!confirm('Delete group + all messages?')) return; try{ const ms = await getDocs(collection(db,'groups',gid,'messages')); for(const m of ms.docs) await deleteDoc(m.ref); await deleteDoc(doc(db,'groups',gid)); A.toast('Group deleted'); A.loadChatMod(); }catch(e){ A.toast(e.message); } };
A.pinChatBanner = async function(){
  const text = prompt('Banner text (empty to remove):');
  await setDoc(doc(db,'config','chat_banner'),{text: text||'', updatedAt: new Date()},{merge:true});
  A.toast('Banner updated');
};
A.clearWorldChat = async function(){
  if(!confirm('Delete ALL world chat messages?')) return;
  const s = await getDocs(collection(db,'world_messages'));
  for(const d of s.docs) await deleteDoc(d.ref);
  A.toast('Cleared '+s.size+' messages');
};
A.chatSettings = async function(){
  const s = await getDoc(doc(db,'config','chat_settings'));
  const cur = s.exists()?s.data():{cooldown:2, maxLen:500, mediaOn:true};
  const cd = parseInt(prompt('Cooldown seconds between messages:', cur.cooldown||2)||cur.cooldown);
  const ml = parseInt(prompt('Max message length:', cur.maxLen||500)||cur.maxLen);
  const media = confirm('Allow media (images/video/files)? OK=Yes');
  await setDoc(doc(db,'config','chat_settings'),{cooldown:cd, maxLen:ml, mediaOn:media, updatedAt: new Date()},{merge:true});
  A.toast('Saved');
};
A.editBannedWords = async function(){
  const _old = document.getElementById('banned-words-modal'); if(_old) _old.remove();
  const s = await getDoc(doc(db,'config','chat_words'));
  const cur = s.exists() ? (s.data().words||[]) : [];
  const modal = document.createElement('div');
  modal.id = 'banned-words-modal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:9999;display:flex;align-items:flex-end;justify-content:center;';
  modal.innerHTML = `
    <div style="background:#fff;width:100%;max-width:520px;max-height:85vh;border-radius:20px 20px 0 0;padding:20px;display:flex;flex-direction:column;gap:12px;overflow:hidden;">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div style="font-weight:800;font-size:1.05rem;"><i class="fa-solid fa-ban" style="color:#dc2626;"></i> Banned Words</div>
        <button onclick="document.getElementById('banned-words-modal').remove()" style="background:none;border:none;font-size:1.4rem;cursor:pointer;">&times;</button>
      </div>
      <div style="font-size:0.78rem;color:#666;">Messages containing any of these words will be auto-blocked and deleted from World Chat.</div>
      <div style="display:flex;gap:6px;">
        <input id="bw-new" placeholder="Add word or phrase..." style="flex:1;padding:10px;border:1px solid #ddd;border-radius:10px;font-size:0.9rem;">
        <button id="bw-add" style="background:#4f46e5;color:#fff;border:none;padding:10px 16px;border-radius:10px;font-weight:700;cursor:pointer;">Add</button>
      </div>
      <div id="bw-list" style="flex:1;overflow-y:auto;display:flex;flex-wrap:wrap;gap:8px;padding:8px;background:#f9fafb;border-radius:12px;min-height:120px;"></div>
      <div style="display:flex;gap:8px;">
        <button id="bw-save" style="flex:1;background:#059669;color:#fff;border:none;padding:12px;border-radius:12px;font-weight:800;cursor:pointer;">💾 Save</button>
        <button id="bw-clear" style="background:#fee2e2;color:#dc2626;border:none;padding:12px 16px;border-radius:12px;font-weight:700;cursor:pointer;">Clear All</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  let words = [...cur];
  const render = () => {
    const box = modal.querySelector('#bw-list');
    if(!words.length){ box.innerHTML = '<div style="color:#999;font-size:0.82rem;margin:auto;">No banned words yet.</div>'; return; }
    box.innerHTML = words.map((w,i)=>`<span style="display:inline-flex;align-items:center;gap:6px;background:#fee2e2;color:#991b1b;padding:6px 10px;border-radius:20px;font-size:0.82rem;font-weight:600;">${w.replace(/[<>&]/g,'')} <button data-i="${i}" style="background:none;border:none;color:#991b1b;cursor:pointer;font-weight:900;">&times;</button></span>`).join('');
    box.querySelectorAll('button[data-i]').forEach(b=>b.onclick=()=>{ words.splice(+b.dataset.i,1); render(); });
  };
  render();
  modal.querySelector('#bw-add').onclick = () => {
    const inp = modal.querySelector('#bw-new');
    const v = inp.value.trim().toLowerCase();
    if(v && !words.includes(v)){ words.push(v); render(); }
    inp.value=''; inp.focus();
  };
  modal.querySelector('#bw-new').addEventListener('keydown', e=>{ if(e.key==='Enter') modal.querySelector('#bw-add').click(); });
  modal.querySelector('#bw-clear').onclick = () => { if(confirm('Remove all banned words?')){ words=[]; render(); } };
  modal.querySelector('#bw-save').onclick = async () => {
    await setDoc(doc(db,'config','chat_words'),{words, updatedAt: new Date()},{merge:true});
    A.toast('Saved '+words.length+' banned words');
    modal.remove();
  };
};
