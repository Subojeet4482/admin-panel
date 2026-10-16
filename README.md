# FF Admin — Server + MAIN panel + LITE panel + Fampay Bot (ek hi Render service, ek hi login)

Ab **ek hi** Flask service mein **ek hi login form** hai (email + password + PIN). Server khud decide karta hai
kaunsa panel kholna hai:

- **OWNER_EMAIL / OWNER_PASSWORD / OWNER_PIN** sahi diye → **MAIN panel** khulta hai (sab kuch: matches,
  wallet, users, backup/restore/delete, Fampay bot control).
- **ADMIN_EMAIL / ADMIN_PASSWORD / ADMIN_PIN** sahi diye → **LITE panel** khulta hai (sirf matches, wallet,
  users, settings — backup/restore/delete/bot nahi).
- Dono mein se kuch bhi match nahi hua → **1 hi shared counter**: 5 galat attempt ke baad us IP ke liye
  **1 hour** lock. Yeh lock **disk par** (`backups/login_locks.json`) save hota hai, Firebase mein kuch nahi —
  process restart tak yaad rehta hai (Render free plan redeploy par disk wipe karta hai, jaisa neeche likha hai).

Kaam isi ek service ke andar:

1. **Admin panel serve karta hai** (`public/` = main panel, `admin_lite.py` ka embedded HTML = lite panel).
2. **Backup / Restore / Data Delete** — sirf owner ke liye, Firebase Admin SDK se (service key ka base64 env mein).
3. **Fampay bot** (Gmail watcher) isi service ke andar thread mein chalta hai — sirf owner panel se Start / Stop / Poll now / settings / live log.
4. **Self keepalive + health** — ek hi `/health` URL (UptimeRobot isi ko ping kare), neeche "Auto-alive" section mein details.

User app (`tournament app`) ko kuch nahi chhua gaya.

## Render par deploy (5 steps)

1. Yeh folder GitHub repo mein push karo → Render → **New → Web Service** (ya `render.yaml` se Blueprint).
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn app:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT`
   - **Workers 1 hi rakho** (bot aur scheduler isi process mein hain).
2. Service key ka base64 banao: Firebase Console → Project settings → Service accounts → *Generate new private key*, phir
   `python tools/make_b64.py serviceAccount.json` → output copy karo.
3. Render → Environment mein yeh set karo:

| Key | Kaam |
|---|---|
| `OWNER_EMAIL`, `OWNER_PASSWORD`, `OWNER_PIN` | Login se **MAIN panel** khulta hai (teeno chahiye) |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_PIN` | Login se **LITE panel** khulta hai (teeno chahiye) |
| `LOGIN_MAX_FAILS` (5), `LOGIN_LOCK_MINUTES` (60) | Galat attempt lock (dono panel ke liye shared) |
| `SESSION_SECRET` | Random lamba string (Blueprint auto bana deta hai) |
| `FIREBASE_CREDENTIALS_B64` | Step 2 ka base64 |
| `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` | Fampay bot (Gmail) |
| `POLL_INTERVAL` (15), `GMAIL_QUERY`, `BOT_AUTOSTART` (true) | Bot defaults — panel se bhi badal sakte ho |
| `BACKUP_INTERVAL_HOURS` (24), `BACKUP_KEEP` (10), `BACKUP_DIR` | Auto-backup |

4. **Firestore rules** — `firestore.rules` publish karo. Sirf `isAdmin()` badla hai: ab `request.auth.token.admin == true` bhi chalta hai (server yahi claim deta hai). Purani email wali condition bhi rakhi hai, baaki rules same.
5. Service URL kholo → login → **Fampay Bot** tab mein status dekho.

> Pehli baar rules publish karne ke baad hi panel ka Firestore data (users, matches, wallet…) dikhega, kyunki panel ab custom admin token se login hota hai.

## Security

- Password/PIN ab `config.js` mein nahi hain. Purani file mein the, isliye **Firebase Auth mein admin account ka password badal do** (purana password GitHub / zip mein dikh chuka hai).
- Login: constant-time check (owner + admin dono creds), 5 galat attempt par 1 hour lock (IP + global, disk par save), signed HttpOnly SameSite=Strict cookie (12h), env badalte hi purane session band.
- Delete / restore / backup-delete par **PIN dobara** lagta hai; collection delete par naam type karna padta hai; default mein delete se pehle auto-backup banta hai.
- Service key sirf server par rehti hai, browser ko kabhi nahi milti.

## Backup ke baare mein

- Format `.ffbak.gz` (Timestamp, GeoPoint, references sahi se restore hote hain). Purane `.json` backups bhi restore ho jaate hain.
- **Render free plan ka disk restart par saaf ho jaata hai.** Isliye backup ko panel se **download** karke rakho, ya Render Disk lagao aur `BACKUP_DIR=/var/data/backups` set karo.
- Free plan 15 min idle par sleep karta hai — bot tab tak band rehta hai. Bot 24x7 chahiye to paid plan ya UptimeRobot se `/health` ping karo.

## Auto-alive + uptime

- **Self-ping:** server har 10 min (`KEEPALIVE_INTERVAL`) apne public `/health` ko ping karta hai, taaki Render free plan idle sleep na kare. URL Render ka `RENDER_EXTERNAL_URL` automatically use hota hai. Status panel → Server tab mein dikhta hai.
- **Bot watchdog:** bot thread mar jaye aur tumne Stop nahi dabaya ho to 60s mein apne aap restart.
- **Uptime monitor (zaroori backup):** self-ping tabhi kaam karta hai jab server jaag raha ho. Agar crash ya deploy ke baad so gaya, to bahar se koi jagaye. [UptimeRobot](https://uptimerobot.com) (free) → *Add New Monitor* → type **HTTP(s)**, URL `https://<tumhari-service>.onrender.com/health`, interval **5 minutes**. Chaho to Keyword monitor mein `"ok":true` daalo, aur email/Telegram alert on karo.
- `/health` sirf ok, uptime aur bot status batata hai — koi secret nahi.
- Free plan ke 750 ghante/mahina ek service ko 24x7 chalane ke liye kaafi hain.

## Local test

```bash
pip install -r requirements.txt
set -a; . ./.env; set +a      # .env.example copy karke bharo
python app.py                 # http://localhost:10000
```
