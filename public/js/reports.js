// User reports + live open-report badge
import { db, collection, doc, updateDoc, query, orderBy, onSnapshot, where, limit } from './firebase.js';

A._repUnsub = null;
A.loadReports = async function(status){
  status = status || 'open';
  const list = document.getElementById('reports-list');
  list.innerHTML = '<div style="padding:20px;color:var(--sub);text-align:center;">Loading...</div>';
  try{ if(A._repUnsub) A._repUnsub(); }catch(e){}
  const q1 = query(collection(db,'reports'), where('status','==',status), orderBy('createdAt','desc'), limit(80));
  A._repUnsub = onSnapshot(q1, (s)=>{
    if(s.empty){ list.innerHTML='<div style="padding:20px;color:var(--sub);text-align:center;">No reports</div>'; return; }
    const rows=[]; s.forEach(d=>rows.push({id:d.id,...d.data()}));
    list.innerHTML = rows.map(r=>{
      const t = r.createdAt && r.createdAt.seconds ? new Date(r.createdAt.seconds*1000).toLocaleString() : '';
      return `<div class="card">
        <div style="display:flex;justify-content:space-between;">
          <div style="font-weight:800;">${(r.type||'?').toUpperCase()}: ${A._esc((r.target||'').slice(0,30))}</div>
          <div style="font-size:0.7rem;color:var(--sub);">${t}</div>
        </div>
        <div style="color:var(--text);font-size:0.85rem;margin-top:6px;">${A._esc(r.reason||'')}</div>
        <div style="color:var(--sub);font-size:0.72rem;margin-top:6px;">by ${A._esc((r.reporter||'').slice(0,12))}</div>
        <div style="display:flex;gap:6px;margin-top:10px;">
          <button class="btn btn-primary" style="padding:6px 10px;font-size:0.75rem;flex:1;" onclick="A.resolveReport('${r.id}')">Resolve</button>
          <button class="btn btn-danger" style="padding:6px 10px;font-size:0.75rem;flex:1;" onclick="A.banUserChat('${r.target}')">Ban Target</button>
        </div>
      </div>`;
    }).join('');
  });
};
A.resolveReport = async function(id){ await updateDoc(doc(db,'reports',id),{status:'resolved', resolvedAt: new Date()}); A.toast('Resolved'); };

// Live open-report badge
try{
  onSnapshot(query(collection(db,'reports'), where('status','==','open')), (s)=>{
    const b = document.getElementById('rep-badge'); if(!b) return;
    if(s.size>0){ b.style.display='inline-block'; b.innerText=s.size; } else b.style.display='none';
  });
}catch(e){}
