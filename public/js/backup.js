// Backup / Restore — server (Firebase Admin) par chalta hai, panel sirf control karta hai
const $ = (id) => document.getElementById(id);
const esc = A.esc;

Object.assign(window.A, {
  async bkLoad(){
    try{
      const [c, b] = await Promise.all([A.api('/api/collections'), A.api('/api/backups')]);
      $('bk-cols').innerHTML = c.collections.length
        ? c.collections.map(x => `<label><input type="checkbox" class="bk-c" value="${esc(x.name)}" checked> ${esc(x.name)}${x.count != null ? ' (' + x.count + ')' : ''}</label>`).join('')
        : '<span class="muted">Koi collection nahi mili</span>';
      $('bk-list').innerHTML = b.backups.length ? b.backups.map(f => `
        <div class="row">
          <div><div class="row-t">${esc(f.name)}</div><div class="row-s">${A.fmtBytes(f.size)} · ${A.fmtAgo(f.mtime)}</div></div>
          <div class="row-btns">
            <button class="btn btn-outline mini" data-n="${esc(f.name)}" onclick="A.bkDownload(this.dataset.n)" title="Download"><i class="fa-solid fa-download"></i></button>
            <button class="btn btn-primary mini" data-n="${esc(f.name)}" onclick="A.bkRestore(this.dataset.n)" title="Restore"><i class="fa-solid fa-rotate-left"></i></button>
            <button class="btn btn-danger mini" data-n="${esc(f.name)}" onclick="A.bkDelete(this.dataset.n)" title="Delete"><i class="fa-solid fa-trash"></i></button>
          </div>
        </div>`).join('') : '<span class="muted">Abhi koi backup nahi — upar se banao.</span>';
      $('bk-dir').innerText = 'Server folder: ' + b.dir + ' (Render free plan par restart ke baad files ud jaati hain — isliye download karke bhi rakho, ya Render Disk lagao).';
      $('bk-int').value = String(b.settings.interval_hours || 0);
      $('bk-keep').value = b.settings.keep || 10;
      $('bk-last').innerText = b.settings.last_auto ? 'Last auto-backup: ' + new Date(b.settings.last_auto * 1000).toLocaleString() : 'Auto-backup abhi tak nahi hua';
    }catch(e){ A.fail(e); }
  },

  async bkCreate(){
    const cols = [...document.querySelectorAll('.bk-c:checked')].map(x => x.value);
    const all = document.querySelectorAll('.bk-c').length;
    if(!cols.length){ A.toast('Kam se kam ek collection chuno', 'err'); return; }
    try{
      const r = await A.api('/api/backups', { body: { collections: cols.length === all ? null : cols, subcollections: $('bk-sub').checked } });
      const j = await A.trackJob(r.job, 'Backup');
      if(j.status === 'done') A.toast(`Backup ready: ${j.result.docs} docs (${A.fmtBytes(j.result.size)})`);
      A.bkLoad();
    }catch(e){ A.fail(e); }
  },

  bkDownload(name){ location.href = '/api/backups/' + encodeURIComponent(name) + '/download'; },

  async bkDelete(name){
    const v = await A.askDanger({ title: 'Backup delete?', text: name, backup: null, okLabel: 'Delete' });
    if(!v) return;
    try{ await A.api('/api/backups/' + encodeURIComponent(name) + '/delete', { body: { pin: v.pin } }); A.toast('Backup delete ho gaya'); A.bkLoad(); }
    catch(e){ A.fail(e); }
  },

  async _bkSafetyBackup(){
    const r = await A.api('/api/backups', { body: { subcollections: true } });
    const j = await A.trackJob(r.job, 'Restore se pehle backup');
    return j.status === 'done';
  },

  async bkRestore(name){
    const v = await A.askDanger({ title: 'Restore karna hai?', text: `${name} ke docs Firestore mein overwrite honge.`, typed: 'RESTORE',
      backup: true, backupLabel: 'Restore se pehle abhi ka data backup lo', okLabel: 'Restore' });
    if(!v) return;
    try{
      if(v.backup && !(await A._bkSafetyBackup())) return;
      const r = await A.api('/api/backups/' + encodeURIComponent(name) + '/restore', { body: { pin: v.pin } });
      const j = await A.trackJob(r.job, 'Restore');
      if(j.status === 'done') A.toast(`${j.result.restored} docs restore ho gaye`);
      A.bkLoad();
    }catch(e){ A.fail(e); }
  },

  async bkUploadRestore(){
    const f = $('restore-file').files[0];
    if(!f){ A.toast('Pehle file select karo', 'err'); return; }
    const v = await A.askDanger({ title: 'Upload karke restore?', text: `${f.name} ke docs overwrite honge.`, typed: 'RESTORE',
      backup: true, backupLabel: 'Restore se pehle abhi ka data backup lo', okLabel: 'Restore' });
    if(!v) return;
    try{
      if(v.backup && !(await A._bkSafetyBackup())) return;
      const fd = new FormData(); fd.append('file', f); fd.append('pin', v.pin);
      const r = await A.api('/api/restore-upload', { form: fd });
      const j = await A.trackJob(r.job, 'Restore');
      if(j.status === 'done'){ A.toast(`${j.result.restored} docs restore ho gaye`); $('restore-file').value = ''; }
      A.bkLoad();
    }catch(e){ A.fail(e); }
  },

  async bkSaveSchedule(){
    try{
      await A.api('/api/backup-settings', { body: { interval_hours: parseInt($('bk-int').value, 10) || 0, keep: parseInt($('bk-keep').value, 10) || 10 } });
      A.toast('Schedule save ho gaya'); A.bkLoad();
    }catch(e){ A.fail(e); }
  }
});
