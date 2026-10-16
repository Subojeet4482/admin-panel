// Shared UI helpers for server-backed tabs: PIN/confirm modal + background job tracker
const $ = (id) => document.getElementById(id);
const fmtBytes = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';
A.fmtBytes = fmtBytes;
A.fmtAgo = (ms) => { if(!ms) return '—'; const s = Math.max(0, Math.round((Date.now() - ms) / 1000)); if(s < 60) return s + 's pehle'; if(s < 3600) return Math.floor(s / 60) + 'm pehle'; if(s < 86400) return Math.floor(s / 3600) + 'h pehle'; return Math.floor(s / 86400) + 'd pehle'; };
A.fmtDur = (s) => { s = Math.floor(s); const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return (d ? d + 'd ' : '') + (h ? h + 'h ' : '') + m + 'm'; };

let pending = null;
function settle(v){ if(pending){ const r = pending; pending = null; r(v); } }
new MutationObserver(() => { const m = $('m-danger'); if(m && !m.classList.contains('show')) settle(null); })
  .observe($('m-danger'), { attributes: true, attributeFilter: ['class'] });

/* PIN (+ optional "type to confirm" + optional backup checkbox). Resolves {pin, backup, typed} ya null. */
A.askDanger = function({ title, text, typed, backup = true, backupLabel, okLabel = 'Confirm', danger = true }){
  settle(null);
  $('dg-title').innerText = title; $('dg-text').innerText = text || '';
  $('dg-typed-wrap').style.display = typed ? 'block' : 'none';
  $('dg-typed-want').innerText = typed || ''; $('dg-typed').value = '';
  $('dg-bk-wrap').style.display = backup === null ? 'none' : 'flex';
  $('dg-bk').checked = backup !== false;
  if(backupLabel) $('dg-bk-lbl').innerText = backupLabel;
  $('dg-pin').value = '';
  const ok = $('dg-ok'); ok.innerText = okLabel; ok.className = 'btn ' + (danger ? 'btn-danger' : 'btn-primary'); ok.style.flex = '1';
  $('m-danger').classList.add('show');
  setTimeout(() => $(typed ? 'dg-typed' : 'dg-pin').focus(), 150);
  return new Promise((resolve) => {
    pending = resolve;
    $('dg-cancel').onclick = () => { $('m-danger').classList.remove('show'); };
    ok.onclick = () => {
      if(typed && $('dg-typed').value.trim() !== typed){ A.toast('Confirm text match nahi hua', 'err'); return; }
      const pin = $('dg-pin').value.trim();
      if(!pin){ A.toast('PIN daalo', 'err'); return; }
      const v = { pin, backup: $('dg-bk').checked, typed: $('dg-typed').value.trim() };
      pending = null; $('m-danger').classList.remove('show'); resolve(v);
    };
  });
};

/* Background job ko poll karo; bar mein progress dikhao. Resolves job object. */
A.trackJob = function(jobId, label){
  const bar = $('job-bar'), txt = $('job-txt'), ic = $('job-ic');
  bar.className = 'job-bar'; ic.className = 'fa-solid fa-spinner fa-spin'; txt.innerText = label + '…';
  return new Promise((resolve) => {
    const tick = async () => {
      let j;
      try{ j = (await A.api('/api/jobs/' + jobId)).job; }
      catch(e){ txt.innerText = e.message; setTimeout(tick, 3000); return; }
      if(j.status === 'running'){ txt.innerText = label + ': ' + j.progress; setTimeout(tick, 1200); return; }
      const good = j.status === 'done';
      bar.className = 'job-bar ' + (good ? 'ok' : 'err');
      ic.className = 'fa-solid ' + (good ? 'fa-circle-check' : 'fa-circle-exclamation');
      txt.innerText = good ? label + ' complete ✓' : (label + ' fail: ' + j.error);
      setTimeout(() => bar.classList.add('hidden'), good ? 3500 : 7000);
      resolve(j);
    };
    tick();
  });
};

/* Button double-tap guard for async handlers that don't wait on a modal */
A.once = function(fn){ let busy = false; return async (...a) => { if(busy) return; busy = true; try{ return await fn(...a); } finally{ setTimeout(() => busy = false, 400); } }; };
A.fail = (e) => A.toast(e.message || 'Error', 'err');
