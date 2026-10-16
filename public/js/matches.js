// Matches: list, create, room creds, complete + players, booya, auto payouts
import { db, collection, doc, setDoc, getDoc, addDoc, updateDoc, deleteDoc, increment, onSnapshot } from './firebase.js';

let _timerID = null;

Object.assign(window.A, {

  /* ---- MATCH LIST ---- */
  _matchesUnsub: null,
  loadMatches() {
    const list = document.getElementById('match-list');
    if(!A._matchesUnsub) list.innerHTML = '<div class="empty-box" style="padding:20px"><i class="fa-solid fa-spinner fa-spin"></i>Loading...</div>';
    try {
      if(A._matchesUnsub){ try{A._matchesUnsub();}catch(e){} }
      A._matchesUnsub = onSnapshot(collection(db,'matches'),(s)=>{
        let arr = [];
        s.forEach(d=>arr.push({id:d.id,...d.data()}));
        arr.sort((a,b)=>new Date(b.startTime||0)-new Date(a.startTime||0));
        if(!arr.length) { list.innerHTML='<p style="text-align:center;padding:20px;color:var(--sub);">No matches yet</p>'; return; }
        list.innerHTML = arr.map(m=>`
        <div class="card">
          <div class="card-top">
            <div>
              <div class="card-title">${A.esc(m.title)}</div>
              <div class="card-sub match-t" data-t="${m.startTime||''}">⏳ Loading...</div>
              <div class="card-sub" style="color:var(--primary);margin-top:2px;">${m.matchType==='win_prize'?'🏆 Win Prize':m.matchType==='per_kill'?'🎯 Per Kill':m.matchType==='team_win'?'👥 Team Win ('+A.esc(m.teamMode)+')':'Match'} • ${A.esc(m.map||'Bermuda')} • ₹${m.fee} fee</div>
            </div>
            <span class="badge ${m.status==='completed'?'badge-done':'badge-active'}">${m.status||'active'}</span>
          </div>
          <button class="btn btn-outline btn-full" style="margin-bottom:10px;" onclick="A.showPlayers('${m.id}')"><i class="fa-solid fa-users"></i> Players (${m.joined||0})</button>
          <div class="btn-row">
            <button class="btn btn-outline" style="flex:1;" onclick="A.showRoomModal('${m.id}')"><i class="fa-solid fa-key"></i> Room</button>
            <button class="btn btn-success" style="flex:1;" onclick="A.completeMatch('${m.id}')"><i class="fa-solid fa-flag-checkered"></i></button>
            <button class="btn btn-danger" style="flex:1;" onclick="A.delMatch('${m.id}')"><i class="fa-solid fa-trash"></i></button>
          </div>
        </div>`).join('');
        A.startTimer();
      }, (e)=>{ list.innerHTML='<p style="color:red;padding:20px;">Error loading</p>'; });
    } catch(e) { list.innerHTML='<p style="color:red;padding:20px;">Error loading</p>'; }
  },
  startTimer() {
    if(_timerID) clearInterval(_timerID);
    _timerID = setInterval(()=>{
      const now = Date.now();
      document.querySelectorAll('.match-t').forEach(el=>{
        const t = el.getAttribute('data-t');
        if(!t) return;
        const diff = new Date(t).getTime()-now;
        if(diff<0){ el.innerHTML="<span class='ended-t'>LIVE / ENDED</span>"; return; }
        const d=Math.floor(diff/86400000), h=Math.floor((diff%86400000)/3600000), m=Math.floor((diff%3600000)/60000), s=Math.floor((diff%60000)/1000);
        el.innerHTML=`<span class="live-t">⏱ ${d>0?d+'d ':''} ${h}h ${m}m ${s}s</span>`;
      });
    },1000);
  },

  /* ---- CREATE / DELETE / ROOM / COMPLETE ---- */
  showMatchModal() { document.getElementById('m-match').classList.add('show'); A.onMatchTypeChange(); },
  onMatchTypeChange() {
    const t = document.getElementById('m-match-type').value;
    document.getElementById('team-mode-row').classList.toggle('hidden', t !== 'team_win');
  },
  async createMatch() {
    const matchType = document.getElementById('m-match-type').value;
    const teamMode = matchType === 'team_win' ? document.getElementById('m-team-mode').value : '';
    const d = {
      title: document.getElementById('m-title').value,
      fee: Number(document.getElementById('m-fee').value),
      prize: Number(document.getElementById('m-prize').value),
      matchType: matchType,
      teamMode: teamMode,
      map: document.getElementById('m-map').value,
      startTime: document.getElementById('m-time').value,
      total: Number(document.getElementById('m-slots').value),
      img: document.getElementById('m-img').value,
      joined: 0, status:'upcoming'
    };
    if(!d.title) return A.toast('Title daalo','err');
    if(!confirm('Match create karein?')) return;
    await addDoc(collection(db,'matches'),d);
    A.toast('Match created!'); A.closeModal('m-match'); A.loadMatches();
    ['m-title','m-fee','m-prize','m-time','m-slots','m-img'].forEach(id=>document.getElementById(id).value='');
  },
  async delMatch(id) {
    if(!confirm('Delete match?')) return;
    await deleteDoc(doc(db,'matches',id));
    A.toast('Deleted'); A.loadMatches();
  },
  showRoomModal(id) {
    document.getElementById('room-mid').value=id;
    document.getElementById('room-id').value='';
    document.getElementById('room-pass').value='';
    document.getElementById('m-room').classList.add('show');
  },
  async saveRoom() {
    const id=document.getElementById('room-mid').value;
    await updateDoc(doc(db,'matches',id),{
      roomId:document.getElementById('room-id').value,
      roomPass:document.getElementById('room-pass').value
    });
    A.toast('Room updated!'); A.closeModal('m-room');
  },
  async completeMatch(id) {
    const win=prompt('Winner Name:'); if(win===null) return;
    await updateDoc(doc(db,'matches',id),{status:'completed',winnerName:win||'N/A'});
    A.toast('Match completed!'); A.loadMatches();
  },

  /* ---- MATCH PLAYERS + AUTO PAYMENT ---- */
  async showPlayers(id) {
    document.getElementById('players-mid').value=id;
    const list=document.getElementById('players-list');
    list.innerHTML='<div class="empty-box" style="padding:20px"><i class="fa-solid fa-spinner fa-spin"></i>Loading...</div>';
    ['win-prize-section','per-kill-section','team-win-section'].forEach(s=>document.getElementById(s).classList.add('hidden'));
    document.getElementById('m-players').classList.add('show');
    try {
      const m=(await getDoc(doc(db,'matches',id))).data()||{};
      const players=m.participants||[];
      const matchType = m.matchType||'win_prize';
      document.getElementById('players-title').innerText=`Joined Players (${players.length})`;
      document.getElementById('players-match-type').value=matchType;
      document.getElementById('players-prize').value=m.prize||0;
      document.getElementById('players-team-mode').value=m.teamMode||'';

      const optHtml = players.map(p=>`<option value="${p.uid}" data-name="${A.esc(p.appName||'User')}">${A.esc(p.appName||'User')} (${A.esc(p.gameUid||'---')})</option>`).join('');

      if(matchType==='win_prize'){
        document.getElementById('win-prize-section').classList.remove('hidden');
        ['wp-1st','wp-2nd','wp-3rd'].forEach(id=>{
          document.getElementById(id).innerHTML='<option value="">-- Select --</option>'+optHtml;
        });
        document.getElementById('wp-amount').value=m.prize||'';
      } else if(matchType==='per_kill'){
        document.getElementById('per-kill-section').classList.remove('hidden');
        document.getElementById('kill-entry-list').innerHTML = players.map(p=>`
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
            <div style="flex:1;font-weight:600;font-size:0.82rem;">${A.esc(p.appName||'User')}<br><span style="font-size:0.68rem;color:var(--sub);">${A.esc(p.gameUid||'---')}</span></div>
            <input type="number" min="0" placeholder="Kills" class="inp" style="width:80px;margin:0;" data-uid="${p.uid}" data-name="${A.esc(p.appName||'User')}" id="kill-${p.uid}">
          </div>`).join('');
      } else if(matchType==='team_win'){
        document.getElementById('team-win-section').classList.remove('hidden');
        const mode=m.teamMode||'BR Squad';
        const teamSize = mode==='BR Duo'?2 : mode==='Lone Wolf'?1 : 4;
        const teamPrize = (m.fee||0)*teamSize;
        document.getElementById('team-win-info').innerText=`Mode: ${mode} | Team Size: ${teamSize} | Team Prize Pool: ₹${teamPrize} (Entry ₹${m.fee} × ${teamSize} members)`;
        document.getElementById('team-player-list').innerHTML = players.map(p=>`
          <div style="display:flex;align-items:center;gap:10px;background:#fff;border-radius:10px;padding:10px;margin-bottom:6px;border:1px solid #e2e8f0;">
            <input type="checkbox" id="tw-${p.uid}" data-uid="${p.uid}" data-name="${A.esc(p.appName||'User')}" style="width:18px;height:18px;accent-color:var(--success);">
            <label for="tw-${p.uid}" style="font-weight:600;font-size:0.85rem;flex:1;">${A.esc(p.appName||'User')}<br><span style="font-size:0.68rem;color:var(--sub);">${A.esc(p.gameUid||'---')}</span></label>
          </div>`).join('');
      }

      if(!players.length){ list.innerHTML='<p style="text-align:center;padding:20px;color:var(--sub);">No players joined yet</p>'; return; }
      list.innerHTML='<div style="font-weight:700;font-size:0.82rem;color:var(--sub);margin-bottom:8px;">All Players:</div>'+players.map(p=>`
        <div style="display:flex;align-items:center;gap:10px;background:#f8fafc;border-radius:10px;padding:10px;margin-bottom:6px;border:1px solid #f0f0f0;">
          <div style="font-weight:700;font-size:0.85rem;">${A.esc(p.appName||'User')}</div>
          <div style="font-size:0.72rem;color:var(--sub);">${A.esc(p.gameName||'---')} • FF: ${A.esc(p.gameUid||'---')} • Slot #${p.slot||'-'}</div>
        </div>`).join('');
      // Booya picker
      const bset = new Set((m.booyaPlayers||[]).map(b=>b.uid));
      document.getElementById('booya-player-list').innerHTML = players.map(p=>`
        <div style="display:flex;align-items:center;gap:10px;background:#fff;border-radius:10px;padding:8px 10px;margin-bottom:6px;border:1px solid #fecaca;">
          <input type="checkbox" id="by-${p.uid}" data-uid="${p.uid}" data-name="${A.esc(p.appName||'User')}" ${bset.has(p.uid)?'checked':''} style="width:18px;height:18px;accent-color:#ef4444;">
          <label for="by-${p.uid}" style="font-weight:600;font-size:0.85rem;flex:1;">${A.esc(p.appName||'User')}<br><span style="font-size:0.68rem;color:var(--sub);">${A.esc(p.gameUid||'---')}</span></label>
        </div>`).join('') || '<p style="font-size:0.78rem;color:var(--sub);">No players</p>';
    } catch(e){ list.innerHTML='<p style="color:red;padding:20px;">Error loading players</p>'; }
  },

  async _alreadyPaid(matchId){
    const m=(await getDoc(doc(db,'matches',matchId))).data()||{};
    if(m.paid){ A.toast('Is match ka payment pehle hi ho chuka hai!','err'); return true; }
    return false;
  },

  async saveBooya() {
    const matchId=document.getElementById('players-mid').value;
    const checked=document.querySelectorAll('#booya-player-list input[type=checkbox]:checked');
    const booyaPlayers=Array.from(checked).map(cb=>({uid:cb.dataset.uid, name:cb.dataset.name}));
    A.toast('Booya saved!');
    return updateDoc(doc(db,'matches',matchId),{booyaPlayers}).catch(e=>A.toast('Error: '+e.message,'err'));
  },

  // Win Prize: 1st/2nd/3rd auto credit
  async distributeWinPrize() {
    const matchId=document.getElementById('players-mid').value;
    if(await A._alreadyPaid(matchId)) return;
    const totalAmt=parseFloat(document.getElementById('wp-amount').value);
    if(isNaN(totalAmt)||totalAmt<=0) return A.toast('Amount daalo','err');
    const w1uid=document.getElementById('wp-1st').value;
    const w2uid=document.getElementById('wp-2nd').value;
    const w3uid=document.getElementById('wp-3rd').value;
    if(!w1uid) return A.toast('1st winner select karo','err');
    if(!confirm(`Prize distribute karein? Total ₹${totalAmt}`)) return;
    // Split: 50% / 30% / 20%
    const splits = [{uid:w1uid,pct:0.5,rank:'1st'},{uid:w2uid,pct:0.3,rank:'2nd'},{uid:w3uid,pct:0.2,rank:'3rd'}].filter(x=>x.uid);
    try {
      const winMap = {};
      const writes = [];
      for(const w of splits){
        const amt=parseFloat((totalAmt*w.pct).toFixed(2));
        winMap[w.uid] = {amt, rank:w.rank};
        writes.push(setDoc(doc(db,'users',w.uid),{withdrawBalance:increment(amt),matchesWon:increment(w.rank==='1st'?1:0),totalEarned:increment(amt)},{merge:true}));
        writes.push(addDoc(collection(db,'users',w.uid,'notifications'),{title:`Match win success: ₹${amt}`,message:`${w.rank} place • Win Prize match reward added to withdrawal balance.`,date:new Date().toLocaleDateString(),timestamp:new Date()}));
        writes.push(addDoc(collection(db,'users',w.uid,'transactions'),{title:`Match win success: ₹${amt}`,amount:`+₹${amt}`,type:'deposit',status:'success',date:new Date().toLocaleDateString(),createdAt:Date.now()}));
      }
      await Promise.all(writes);
      const msnap=await getDoc(doc(db,'matches',matchId));
      const parts=((msnap.data()||{}).participants||[]).map(p=>{
        const w=winMap[p.uid];
        return w ? {...p, winAmount:w.amt, rank:w.rank} : {...p, winAmount:0};
      });
      await updateDoc(doc(db,'matches',matchId),{status:'completed',paid:true,winnerUid:w1uid,participants:parts});
      A.toast('Winners credited!'); A.closeModal('m-players'); A.loadMatches();
    } catch(e){ A.toast('Error: '+e.message,'err'); }
  },

  // Per Kill: kill count × per kill amount
  async creditKills() {
    const matchId=document.getElementById('players-mid').value;
    if(await A._alreadyPaid(matchId)) return;
    const perKill=parseFloat(document.getElementById('players-prize').value)||0;
    if(perKill<=0) return A.toast('Per kill amount set nahi hai match me','err');
    const inputs=document.querySelectorAll('#kill-entry-list input[data-uid]');
    let credits=[];
    inputs.forEach(inp=>{
      const kills=parseInt(inp.value)||0;
      if(kills>0) credits.push({uid:inp.dataset.uid, name:inp.dataset.name, kills, amt:kills*perKill});
    });
    if(!credits.length) return A.toast('Kisi ka bhi kill count nahi dala','err');
    const summary=credits.map(c=>`${c.name}: ${c.kills} kills × ₹${perKill} = ₹${c.amt}`).join('\n');
    if(!confirm(`Credit karein?\n${summary}`)) return;
    try {
      const killMap = {};
      const writes = [];
      for(const c of credits){
        killMap[c.uid] = {kills:c.kills, amt:c.amt};
        writes.push(setDoc(doc(db,'users',c.uid),{withdrawBalance:increment(c.amt),totalEarned:increment(c.amt),kills:increment(c.kills)},{merge:true}));
        writes.push(addDoc(collection(db,'users',c.uid,'notifications'),{title:`Match win success: ₹${c.amt}`,message:`Per Kill reward: ${c.kills} × ₹${perKill}.`,date:new Date().toLocaleDateString(),timestamp:new Date()}));
        writes.push(addDoc(collection(db,'users',c.uid,'transactions'),{title:`Match win success: ₹${c.amt} (${c.kills} kills)`,amount:`+₹${c.amt}`,type:'deposit',status:'success',date:new Date().toLocaleDateString(),createdAt:Date.now()}));
      }
      await Promise.all(writes);
      const msnap=await getDoc(doc(db,'matches',matchId));
      const parts=((msnap.data()||{}).participants||[]).map(p=>{
        const k=killMap[p.uid];
        return k ? {...p, kills:k.kills, winAmount:k.amt} : {...p, kills:0, winAmount:0};
      });
      await updateDoc(doc(db,'matches',matchId),{status:'completed',paid:true,participants:parts});
      A.toast('Kill rewards credited!'); A.closeModal('m-players'); A.loadMatches();
    } catch(e){ A.toast('Error: '+e.message,'err'); }
  },

  // Team Win: checked players get equal share
  async creditTeamWin() {
    const matchId=document.getElementById('players-mid').value;
    if(await A._alreadyPaid(matchId)) return;
    const fee=parseFloat(document.getElementById('players-prize').value)||0; // prize field = fee per player
    const checked=document.querySelectorAll('#team-player-list input[type=checkbox]:checked');
    if(!checked.length) return A.toast('Winning team members tick karo','err');
    const teamSize=checked.length;
    const totalPrize=fee*teamSize;
    const perPlayer=parseFloat((totalPrize/teamSize).toFixed(2));
    if(!confirm(`${teamSize} players ko ₹${perPlayer} each credit karein? (Total ₹${totalPrize})`)) return;
    try {
      const twWrites = [];
      for(const cb of checked){
        const uid=cb.dataset.uid;
        twWrites.push(setDoc(doc(db,'users',uid),{withdrawBalance:increment(perPlayer),matchesWon:increment(1),totalEarned:increment(perPlayer)},{merge:true}));
        twWrites.push(addDoc(collection(db,'users',uid,'notifications'),{title:`Match win success: ₹${perPlayer}`,message:`Team win — reward added to withdrawal balance.`,date:new Date().toLocaleDateString(),timestamp:new Date()}));
        twWrites.push(addDoc(collection(db,'users',uid,'transactions'),{title:`Match win success: ₹${perPlayer} (Team Win)`,amount:`+₹${perPlayer}`,type:'deposit',status:'success',date:new Date().toLocaleDateString(),createdAt:Date.now()}));
      }
      await Promise.all(twWrites);
      await updateDoc(doc(db,'matches',matchId),{status:'completed',paid:true});
      A.toast('Team rewarded!'); A.closeModal('m-players'); A.loadMatches();
    } catch(e){ A.toast('Error: '+e.message,'err'); }
  }
});
