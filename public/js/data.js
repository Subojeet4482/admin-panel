// Data Delete — sab destructive kaam server par, PIN + confirm ke baad, default auto-backup ke saath
const $ = (id) => document.getElementById(id);
const esc = A.esc;

async function runDelete(path, body, label){
  const r = await A.api(path, { body });
  const j = await A.trackJob(r.job, label);
  if(j.status === 'done'){
    const x = j.result || {};
    A.toast(`${x.deleted != null ? x.deleted + ' docs delete' : 'Delete'} ho gaye${x.backup ? ' · backup: ' + x.backup : ''}`);
  }
  A.ddLoad();
}

Object.assign(window.A, {
  async ddLoad(){
    try{
      const c = await A.api('/api/collections');
      $('dd-cols').innerHTML = c.collections.length ? c.collections.map(x => `
        <div class="row"><div><div class="row-t">${esc(x.name)}</div><div class="row-s">${x.count != null ? x.count + ' docs' : 'count unavailable'}</div></div>
        <button class="btn btn-danger mini" data-c="${esc(x.name)}" onclick="A.ddCol(this.dataset.c)"><i class="fa-solid fa-trash"></i> Delete</button></div>`).join('')
        : '<span class="muted">Koi collection nahi</span>';
      $('dd-old-col').innerHTML = c.collections.map(x => `<option value="${esc(x.name)}">${esc(x.name)}</option>`).join('');
    }catch(e){ A.fail(e); }
  },

  async ddCol(name){
    const v = await A.askDanger({ title: `"${name}" poora delete?`, text: 'Collection ke saare docs aur unki subcollections hamesha ke liye hat jayenge.', typed: name, okLabel: 'Delete all' });
    if(!v) return;
    try{ await runDelete('/api/data/delete-collection', { collection: name, confirm: v.typed, pin: v.pin, backup: v.backup }, 'Delete'); }catch(e){ A.fail(e); }
  },

  async ddOlder(){
    const col = $('dd-old-col').value, field = $('dd-old-field').value.trim(), days = parseFloat($('dd-old-days').value);
    if(!col || !field || isNaN(days) || days < 0){ A.toast('Collection, field aur days bharo', 'err'); return; }
    const v = await A.askDanger({ title: 'Purana data delete?', text: `${col} ke woh docs jinka "${field}" ${days} din se purana hai.`, okLabel: 'Delete' });
    if(!v) return;
    try{ await runDelete('/api/data/delete-older', { collection: col, field, days, type: $('dd-old-type').value, pin: v.pin, backup: v.backup }, 'Cleanup'); }catch(e){ A.fail(e); }
  },

  async ddDoc(){
    const path = $('dd-doc-path').value.trim().replace(/^\/+|\/+$/g, '');
    if(!path || path.split('/').length % 2 !== 0){ A.toast('Path format: collection/docId', 'err'); return; }
    const v = await A.askDanger({ title: 'Document delete?', text: path, okLabel: 'Delete' });
    if(!v) return;
    try{ await runDelete('/api/data/delete-doc', { path, recursive: $('dd-doc-sub').checked, pin: v.pin, backup: v.backup }, 'Delete'); $('dd-doc-path').value = ''; }catch(e){ A.fail(e); }
  }
});
