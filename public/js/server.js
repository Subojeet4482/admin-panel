// Server tab — status + env checklist
const $ = (id) => document.getElementById(id);
const kv = (k, v) => `<div class="kv"><span>${k}</span><span>${v}</span></div>`;
A.srvLoad = async function(){
  try{
    const s = await A.api('/api/status');
    $('srv-kv').innerHTML =
      kv('Logged in as', A.esc(s.user)) + kv('Uptime', A.fmtDur(s.uptime)) + kv('Python', s.python) +
      kv('Firebase', s.firebase.ok ? '✅ ' + A.esc(s.firebase.project_id) : '❌ ' + A.esc(s.firebase.error || 'not connected')) +
      kv('Auto-alive', s.keepalive.enabled ? (s.keepalive.last_error ? '⚠️ ' + A.esc(s.keepalive.last_error) : (s.keepalive.last_ok ? '✅ ' + A.fmtAgo(s.keepalive.last_ok * 1000) : '⏳ pehla ping baaki')) : '❌ off (URL nahi mila)') +
      kv('Pings / fails', s.keepalive.pings + ' / ' + s.keepalive.fails) + kv('Bot watchdog restarts', s.keepalive.watchdog_restarts) +
      kv('Session', s.session_hours + ' ghante') + kv('Backup disk free', A.fmtBytes(s.disk_free));
    $('srv-env').innerHTML = A.envRows(s.env);
  }catch(e){ A.fail(e); }
};
