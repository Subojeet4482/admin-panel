"""FF Admin LITE - ek hi file: server + admin panel (sub-admin ke liye).

Env (sirf yeh 4):
  FIREBASE_64    -> Firebase service account JSON ka base64
  ADMIN_EMAIL
  ADMIN_PASSWORD
  ADMIN_PIN

Run:   pip install flask firebase-admin gunicorn
       gunicorn admin_lite:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT
Local: python admin_lite.py   (http://localhost:10000)

NOTE (merged mode): jab yeh file app.py se `import admin_lite as lite` ke zariye load hoti hai,
iska apna `app` (Flask instance) aur uske '/', '/health', '/api/login', '/api/session', '/api/logout'
routes KABHI serve nahi hote — woh sirf standalone run (`python admin_lite.py`) mein kaam karte hain.
Merged service mein app.py hi single login + '/' serve karta hai aur yahan se sirf business routes
(users/wallet/matches/settings) ko apne upar register karta hai, isi file ke login_required /
make_session / read_session / rate-limit ko reuse karte hue.

Features: Match create/room/players/prize payout, Wallet (deposit/withdraw accept-reject-refund),
Users (balance dekho, add/remove, ban/unban), Settings (payment QR + top banner URLs).
Data same Firestore mein hai, isliye yahan ka withdrawal 'success' main panel mein bhi success dikhta hai.
"""
import os, re, json, time, hmac, base64, hashlib, secrets, threading, datetime as dt
from functools import wraps
from flask import Flask, request, jsonify, Response, g
from werkzeug.middleware.proxy_fix import ProxyFix
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore import Increment, transactional

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
BOOT = time.time()
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# ------------------------------------------------------------------ env + firebase
def env(k):
    return (os.environ.get(k) or "").strip()

EMAIL, PASSWORD, PIN = env("ADMIN_EMAIL").lower(), os.environ.get("ADMIN_PASSWORD") or "", env("ADMIN_PIN")
# Merged service mein main app (fb.py) FIREBASE_CREDENTIALS_B64 se default Firebase app banata hai —
# lite usi ko reuse karta hai, isliye FIREBASE_64 ek alag env ki zaroorat nahi (par chaho to de sakte ho).
FB64 = env("FIREBASE_64") or env("FIREBASE_B64") or env("FIREBASE_CREDENTIALS_B64")
_db, _fb_err = None, None


def get_db():
    global _db, _fb_err
    if _db is not None:
        return _db
    try:
        if not firebase_admin._apps:
            if not FB64:
                raise RuntimeError("FIREBASE_64 / FIREBASE_CREDENTIALS_B64 env set nahi hai")
            info = json.loads(FB64) if FB64.startswith("{") else json.loads(base64.b64decode(FB64 + "=" * (-len(FB64) % 4)))
            firebase_admin.initialize_app(credentials.Certificate(info))
        _db, _fb_err = firestore.client(), None
        return _db
    except Exception as e:
        _fb_err = str(e)
        raise


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.msg, self.code = msg, code


@app.errorhandler(ApiError)
def _api_err(e):
    return jsonify(ok=False, error=e.msg), e.code


@app.errorhandler(Exception)
def _any_err(e):
    if hasattr(e, "code") and isinstance(e.code, int) and e.code < 500:
        return jsonify(ok=False, error=getattr(e, "description", str(e))), e.code
    print("[error]", repr(e), flush=True)
    return jsonify(ok=False, error=str(e)[:300]), 500


# ------------------------------------------------------------------ helpers
def num(v):
    try:
        return float(re.sub(r"[^0-9.]", "", str(v if v is not None else 0)) or 0)
    except ValueError:
        return 0.0


def money(x):
    x = float(x)
    return str(int(x)) if x == int(x) else f"{x:.2f}".rstrip("0").rstrip(".")


def today():
    d = dt.datetime.now(IST)
    return f"{d.month}/{d.day}/{d.year}"


def now_ms():
    return int(time.time() * 1000)


def tsms(t):
    if t.get("createdAt"):
        return t["createdAt"]
    x = t.get("timestamp")
    return int(x.timestamp() * 1000) if isinstance(x, dt.datetime) else 0


def order_no(typ, doc_id, ms):
    d = dt.datetime.fromtimestamp((ms or now_ms()) / 1000, IST)
    return f"{'WD' if typ == 'withdraw' else 'DP'}-{d:%Y%m%d}-{(doc_id or 'XXXX')[-4:].upper()}"


def clean(v):
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, dt.datetime):
        return v.isoformat()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def notif(txn_or_batch, uref, title, msg):
    txn_or_batch.set(uref.collection("notifications").document(),
                     {"title": title, "message": msg, "date": today(), "timestamp": dt.datetime.now(dt.timezone.utc)})


def trx_doc(txn_or_batch, uref, title, amt_str, typ):
    txn_or_batch.set(uref.collection("transactions").document(),
                     {"title": title, "amount": amt_str, "type": typ, "status": "success", "date": today(), "createdAt": now_ms()})


# ------------------------------------------------------------------ auth (signed cookie, rate limit)
COOKIE, SESSION_H = "ffl_session", 12
MAX_FAILS = int(env("LOGIN_MAX_FAILS") or 5)
LOCK_S = int(env("LOGIN_LOCK_MINUTES") or 60) * 60
_SECRET = hashlib.sha256(("ffl|" + EMAIL + "|" + PASSWORD + "|" + PIN + "|" + FB64).encode()).digest()
_lock, _fails = threading.Lock(), {}

# ---------------------------------------------------------------- disk persistence (Firebase ko touch nahi karta)
# Render free plan ka disk restart/redeploy par saaf ho jaata hai — yeh sirf process crash/reload ke
# beech lock yaad rakhta hai. Redeploy ke paar persist karna ho to LOGIN_LOCK_DIR paid Disk par point karo.
_STATE_DIR = env("LOGIN_LOCK_DIR") or os.path.join(os.path.dirname(__file__), "backups")
try:
    os.makedirs(_STATE_DIR, exist_ok=True)
except Exception:
    pass
_STATE_FILE = os.path.join(_STATE_DIR, "login_locks.json")


def _load_fails():
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            _fails.update({str(k): v for k, v in (json.load(f) or {}).items()})
    except Exception:
        pass


def _save_fails():
    try:
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_fails, f)
        os.replace(tmp, _STATE_FILE)
    except Exception:
        pass


_load_fails()


def _eq(a, b):
    return hmac.compare_digest(str(a).encode(), str(b).encode())


def _sign(b):
    return hmac.new(_SECRET, b.encode(), hashlib.sha256).hexdigest()


def make_session():
    b = base64.urlsafe_b64encode(json.dumps({"u": EMAIL, "exp": int(time.time() + SESSION_H * 3600)}).encode()).decode().rstrip("=")
    return b + "." + _sign(b)


def read_session():
    tok = request.cookies.get(COOKIE) or ""
    try:
        b, sig = tok.split(".", 1)
        if hmac.compare_digest(sig, _sign(b)) and json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))["exp"] > time.time():
            return True
    except Exception:
        pass
    return False


def locked_for(ip):
    with _lock:
        rec = _fails.get(ip)
        return int(rec[1] - time.time()) if rec and rec[1] > time.time() else 0


def reg_fail(ip):
    with _lock:
        rec = _fails.get(ip, [0, 0])
        if rec[1] and rec[1] <= time.time():
            rec = [0, 0]
        rec[0] += 1
        if rec[0] >= MAX_FAILS:
            rec = [0, time.time() + LOCK_S]
        _fails[ip] = rec
        _save_fails()


def login_required(fn):
    @wraps(fn)
    def w(*a, **k):
        if not read_session():
            return jsonify(ok=False, error="Login required", code="auth"), 401
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Requested-With") != "ffadmin":
            return jsonify(ok=False, error="Bad request origin"), 403
        return fn(*a, **k)
    return w


def check_pin(d):
    ip = request.remote_addr or "?"
    wait = locked_for(ip)
    if wait:
        raise ApiError(f"Bahut galat PIN. {wait // 60 + 1} min baad try karo.", 429)
    if not _eq(str(d.get("pin") or ""), PIN):
        reg_fail(ip)
        raise ApiError("PIN galat hai", 403)


@app.after_request
def _hdr(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Cache-Control"] = "no-store"
    return r


@app.get("/")
def index():
    return Response(HTML, mimetype="text/html")


@app.get("/health")
def health():
    return jsonify(ok=True, uptime=int(time.time() - BOOT))


@app.post("/api/login")
def login():
    if not (EMAIL and PASSWORD and PIN):
        raise ApiError("Server par ADMIN_EMAIL / ADMIN_PASSWORD / ADMIN_PIN env set nahi hain", 503)
    ip = request.remote_addr or "?"
    wait = locked_for(ip)
    if wait:
        raise ApiError(f"Bahut zyada galat attempts. {wait // 60 + 1} minute baad try karo.", 429)
    d = request.get_json(silent=True) or {}
    ok = _eq((d.get("email") or "").strip().lower(), EMAIL) & _eq(d.get("password") or "", PASSWORD) & _eq(d.get("pin") or "", PIN)
    if not ok:
        reg_fail(ip)
        time.sleep(0.8)
        raise ApiError("Email, password ya PIN galat hai", 401)
    with _lock:
        if _fails.pop(ip, None) is not None:
            _save_fails()
    r = jsonify(ok=True)
    r.set_cookie(COOKIE, make_session(), max_age=SESSION_H * 3600, httponly=True, samesite="Strict", secure=request.is_secure)
    return r


@app.get("/api/session")
def session():
    ready = bool(EMAIL and PASSWORD and PIN)
    return jsonify(ok=read_session(), ready=ready, firebase=bool(FB64))


@app.post("/api/logout")
def logout():
    r = jsonify(ok=True)
    r.delete_cookie(COOKIE)
    return r


# ------------------------------------------------------------------ users
USER_KEYS = ("appName", "phone", "gameUid", "gameName", "playerId", "isBanned", "bannedUntil", "isVerified")


def user_row(doc_id, u):
    dep = u.get("depositBalance", u.get("balance", 0)) or 0
    return {"id": doc_id, **{k: u.get(k) for k in USER_KEYS}, "dep": dep, "wd": u.get("withdrawBalance", 0) or 0}


@app.get("/api/users")
@login_required
def users():
    return jsonify(ok=True, users=clean([user_row(s.id, s.to_dict() or {}) for s in get_db().collection("users").stream()]))


@app.get("/api/users/<uid>")
@login_required
def user_detail(uid):
    ref = get_db().collection("users").document(uid)
    s = ref.get()
    if not s.exists:
        raise ApiError("User nahi mila", 404)
    trx = [t.to_dict() or {} for t in ref.collection("transactions").stream()]
    trx.sort(key=tsms, reverse=True)
    trx = [{k: t.get(k) for k in ("title", "amount", "status", "date")} for t in trx[:40]]
    return jsonify(ok=True, user=clean(user_row(uid, s.to_dict() or {})), trx=clean(trx))


@app.post("/api/users/<uid>/balance")
@login_required
def user_balance(uid):
    d = request.get_json(silent=True) or {}
    check_pin(d)
    try:
        amt = float(d.get("amount"))
    except (TypeError, ValueError):
        raise ApiError("Amount number hona chahiye")
    if amt == 0:
        raise ApiError("Amount 0 nahi ho sakta")
    btype = "withdraw" if d.get("type") == "withdraw" else "deposit"
    ref = get_db().collection("users").document(uid)
    if not ref.get().exists:
        raise ApiError("User nahi mila", 404)
    field, label = ("withdrawBalance", "withdrawal") if btype == "withdraw" else ("depositBalance", "deposit")
    title = f"System {label} {'add' if amt >= 0 else 'cut'}: ₹{money(abs(amt))}"
    up = {field: Increment(amt)}
    if amt >= 0 and btype != "withdraw":
        up["totalDeposited"] = Increment(amt)
    if amt < 0 and btype == "withdraw":
        up["totalWithdrawn"] = Increment(abs(amt))
    b = get_db().batch()
    b.set(ref, up, merge=True)
    notif(b, ref, title, f"Admin adjusted your {label} balance.")
    trx_doc(b, ref, title, f"{'+' if amt >= 0 else '-'}₹{money(abs(amt))}", "deposit" if amt >= 0 else "withdraw")
    b.commit()
    return jsonify(ok=True)


@app.post("/api/users/<uid>/ban")
@login_required
def user_ban(uid):
    d = request.get_json(silent=True) or {}
    try:
        hrs = int(d.get("hours", 0))
    except (TypeError, ValueError):
        raise ApiError("Duration galat")
    until = "permanent" if hrs == 0 else (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hrs)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    get_db().collection("users").document(uid).set({"isBanned": True, "bannedUntil": until}, merge=True)
    return jsonify(ok=True)


@app.post("/api/users/<uid>/unban")
@login_required
def user_unban(uid):
    get_db().collection("users").document(uid).set({"isBanned": False, "bannedUntil": None}, merge=True)
    return jsonify(ok=True)


# ------------------------------------------------------------------ wallet
def trx_item(s):
    t = s.to_dict() or {}
    typ = "withdraw" if t.get("type") == "withdraw" else "deposit"
    ms = tsms(t)
    row = {k: t.get(k) for k in ("status", "amount", "method", "date", "trxId", "number", "accountName", "accountNumber",
                                  "ifsc", "coin", "network", "walletAddress")}
    return {**row, "id": s.id, "uid": s.reference.parent.parent.id if s.reference.parent.parent else "?",
            "type": typ, "order": order_no(typ, s.id, ms), "ts": ms}


@app.get("/api/wallet")
@login_required
def wallet():
    mode = request.args.get("status", "pending")
    cg = get_db().collection_group("transactions")
    try:
        snaps = list(cg.where("status", "==", "pending").stream()) if mode == "pending" else list(cg.stream())
    except Exception:  # collection-group index na ho to sab padh ke filter
        snaps = list(cg.stream())
    items = [trx_item(s) for s in snaps]
    if mode == "pending":
        items = [i for i in items if i["status"] == "pending"]
    # sirf wallet requests (admin adjust / match win wale 'success' rows history mein bhi aate hain, theek hai)
    items.sort(key=lambda i: i["ts"], reverse=True)
    return jsonify(ok=True, items=clean(items[:300]))


@transactional
def _wallet_txn(txn, tref, uref, action):
    s = tref.get(transaction=txn)
    if not s.exists:
        raise ApiError("Transaction nahi mili", 404)
    t = s.to_dict() or {}
    typ = "withdraw" if t.get("type") == "withdraw" else "deposit"
    st, amt = t.get("status"), num(t.get("amount"))
    order = order_no(typ, tref.id, tsms(t))
    utr = str(t.get("trxId") or "")
    dref, dr = None, None
    if typ == "deposit" and utr and action in ("approve", "reject"):
        dref = get_db().collection("deposit_requests").document(utr)
        ds = dref.get(transaction=txn)
        dr = (ds.to_dict() or {}) if ds.exists else None
    stamp = {"orderNo": order, "processedAt": now_ms()}
    uid = uref.id
    if action in ("approve", "reject") and st != "pending":
        raise ApiError("Already processed")
    if action == "approve":
        if typ == "deposit":
            if dr is not None:
                if dr.get("status") == "success" and dr.get("claimed"):
                    txn.update(tref, {"status": "success", **stamp})
                    return "Already credited by auto-verify"
                if dr.get("uid") and dr["uid"] != uid:
                    raise ApiError("UTR belongs to another user")
                txn.update(dref, {"status": "success", "claimed": True, "verifiedAt": now_ms(), "receivedAmount": amt, "verifiedBy": "admin"})
            txn.update(tref, {"status": "success", **stamp})
            txn.set(uref, {"depositBalance": Increment(amt), "totalDeposited": Increment(amt)}, merge=True)
            notif(txn, uref, f"Deposit success: ₹{money(amt)}", f"Order {order} • ₹{money(amt)} added to your deposit balance.")
        else:
            txn.update(tref, {"status": "success", **stamp})
            txn.set(uref, {"totalWithdrawn": Increment(amt)}, merge=True)
            notif(txn, uref, f"Withdrawal success: ₹{money(amt)}", f"Order {order} • ₹{money(amt)} withdrawal approved. Funds will reach you shortly.")
    elif action == "reject":
        txn.update(tref, {"status": "rejected", **stamp})
        if dr is not None:
            txn.update(dref, {"status": "rejected", "rejectedAt": now_ms(), "verifiedBy": "admin"})
        notif(txn, uref, f"{'Withdrawal' if typ == 'withdraw' else 'Deposit'} rejected: ₹{money(amt)}", f"Order {order} • Request rejected by admin. No refund issued.")
    elif action == "refund":
        if typ != "withdraw":
            raise ApiError("Refund sirf withdrawal par hota hai")
        if st in ("refunded", "success"):
            raise ApiError("Isko refund nahi kar sakte")
        txn.update(tref, {"status": "refunded", **stamp})
        txn.set(uref, {"withdrawBalance": Increment(amt)}, merge=True)
        notif(txn, uref, f"Withdrawal refunded: ₹{money(amt)}", f"Order {order} • ₹{money(amt)} returned to your withdrawal balance.")
    else:
        raise ApiError("Unknown action")
    return "Done"


@app.post("/api/wallet/process")
@login_required
def wallet_process():
    d = request.get_json(silent=True) or {}
    uid, tid = str(d.get("uid") or ""), str(d.get("id") or "")
    if not uid or not tid or "/" in uid + tid:
        raise ApiError("uid / id galat")
    db = get_db()
    msg = _wallet_txn(db.transaction(), db.collection("users").document(uid).collection("transactions").document(tid),
                      db.collection("users").document(uid), d.get("action"))
    return jsonify(ok=True, message=msg)


# ------------------------------------------------------------------ matches
@app.get("/api/matches")
@login_required
def matches():
    out = []
    for s in get_db().collection("matches").stream():
        m = s.to_dict() or {}
        m.pop("participants", None)
        out.append({"id": s.id, **m})
    out.sort(key=lambda m: str(m.get("startTime") or ""), reverse=True)
    return jsonify(ok=True, matches=clean(out))


@app.post("/api/matches")
@login_required
def match_create():
    d = request.get_json(silent=True) or {}
    title = (d.get("title") or "").strip()
    if not title:
        raise ApiError("Title daalo")
    mt = d.get("matchType") if d.get("matchType") in ("win_prize", "per_kill", "team_win") else "win_prize"
    doc = {"title": title, "fee": num(d.get("fee")), "prize": num(d.get("prize")), "matchType": mt,
           "teamMode": (d.get("teamMode") or "") if mt == "team_win" else "", "map": d.get("map") or "Bermuda",
           "startTime": d.get("startTime") or "", "total": int(num(d.get("total"))), "img": d.get("img") or "",
           "joined": 0, "status": "upcoming"}
    ref = get_db().collection("matches").document()
    ref.set(doc)
    return jsonify(ok=True, id=ref.id)


@app.get("/api/matches/<mid>")
@login_required
def match_get(mid):
    s = get_db().collection("matches").document(mid).get()
    if not s.exists:
        raise ApiError("Match nahi mila", 404)
    return jsonify(ok=True, match=clean({"id": mid, **(s.to_dict() or {})}))


@app.post("/api/matches/<mid>/delete")
@login_required
def match_delete(mid):
    get_db().collection("matches").document(mid).delete()
    return jsonify(ok=True)


@app.post("/api/matches/<mid>/room")
@login_required
def match_room(mid):
    d = request.get_json(silent=True) or {}
    get_db().collection("matches").document(mid).update({"roomId": d.get("roomId") or "", "roomPass": d.get("roomPass") or ""})
    return jsonify(ok=True)


@app.post("/api/matches/<mid>/complete")
@login_required
def match_complete(mid):
    d = request.get_json(silent=True) or {}
    get_db().collection("matches").document(mid).update({"status": "completed", "winnerName": (d.get("winner") or "N/A")})
    return jsonify(ok=True)


@transactional
def _pay_txn(txn, mref, uc, d):
    s = mref.get(transaction=txn)
    if not s.exists:
        raise ApiError("Match nahi mila", 404)
    m = s.to_dict() or {}
    if m.get("paid"):
        raise ApiError("Is match ka payment pehle hi ho chuka hai!", 409)
    parts = m.get("participants") or []
    valid = {p.get("uid") for p in parts}
    kind = d.get("kind")
    cr = {}  # uid -> dict(amt, fields, title, msg, extra)
    extra_match = {}
    if kind == "win_prize":
        total = num(d.get("amount"))
        if total <= 0:
            raise ApiError("Amount daalo")
        picks = [(d.get("w1"), .5, "1st"), (d.get("w2"), .3, "2nd"), (d.get("w3"), .2, "3rd")]
        picks = [p for p in picks if p[0]]
        if not picks or picks[0][2] != "1st":
            raise ApiError("1st winner select karo")
        if len({p[0] for p in picks}) != len(picks):
            raise ApiError("Ek player do rank mein nahi ho sakta")
        for uid, pct, rank in picks:
            if uid not in valid:
                raise ApiError("Winner is match ka player nahi hai")
            a = round(total * pct, 2)
            cr[uid] = {"amt": a, "rank": rank, "f": {"withdrawBalance": Increment(a), "matchesWon": Increment(1 if rank == "1st" else 0), "totalEarned": Increment(a)},
                       "title": f"Match win success: ₹{money(a)}", "msg": f"{rank} place • Win Prize match reward added to withdrawal balance.", "ttl": f"Match win success: ₹{money(a)}"}
        extra_match["winnerUid"] = picks[0][0]
    elif kind == "per_kill":
        pk = num(m.get("prize"))
        if pk <= 0:
            raise ApiError("Per kill amount match mein set nahi hai")
        for uid, k in (d.get("kills") or {}).items():
            k = int(num(k))
            if k > 0:
                if uid not in valid:
                    raise ApiError("Player is match ka nahi hai")
                a = k * pk
                cr[uid] = {"amt": a, "kills": k, "f": {"withdrawBalance": Increment(a), "totalEarned": Increment(a), "kills": Increment(k)},
                           "title": f"Match win success: ₹{money(a)}", "msg": f"Per Kill reward: {k} × ₹{money(pk)}.", "ttl": f"Match win success: ₹{money(a)} ({k} kills)"}
        if not cr:
            raise ApiError("Kisi ka bhi kill count nahi dala")
    elif kind == "team_win":
        per = num(m.get("prize"))
        uids = [u for u in (d.get("uids") or []) if u]
        if per <= 0:
            raise ApiError("Match mein prize amount set nahi hai")
        if not uids:
            raise ApiError("Winning team members tick karo")
        for uid in uids:
            if uid not in valid:
                raise ApiError("Player is match ka nahi hai")
            cr[uid] = {"amt": per, "f": {"withdrawBalance": Increment(per), "matchesWon": Increment(1), "totalEarned": Increment(per)},
                       "title": f"Match win success: ₹{money(per)}", "msg": "Team win — reward added to withdrawal balance.", "ttl": f"Match win success: ₹{money(per)} (Team Win)"}
    else:
        raise ApiError("Unknown payout type")
    db = get_db()
    for uid, c in cr.items():
        uref = db.collection("users").document(uid)
        txn.set(uref, c["f"], merge=True)
        notif(txn, uref, c["title"], c["msg"])
        trx_doc(txn, uref, c["ttl"], f"+₹{money(c['amt'])}", "deposit")
    upd = {"status": "completed", "paid": True, **extra_match}
    if kind in ("win_prize", "per_kill"):
        upd["participants"] = [({**p, **{k: v for k, v in (("winAmount", cr[p["uid"]]["amt"]), ("rank", cr[p["uid"]].get("rank")), ("kills", cr[p["uid"]].get("kills"))) if v is not None}}
                                if p.get("uid") in cr else {**p, "winAmount": 0, **({"kills": 0} if kind == "per_kill" else {})}) for p in parts]
    txn.update(mref, upd)
    return len(cr)


@app.post("/api/matches/<mid>/pay")
@login_required
def match_pay(mid):
    d = request.get_json(silent=True) or {}
    db = get_db()
    n = _pay_txn(db.transaction(), db.collection("matches").document(mid), None, d)
    return jsonify(ok=True, paid=n)


# ------------------------------------------------------------------ settings: QR + top banners
@app.get("/api/settings")
@login_required
def settings_get():
    d = get_db().collection("config").document("banner").get().to_dict() or {}
    banners = d.get("banners") if isinstance(d.get("banners"), list) else ([d["url"]] if d.get("url") else [])
    return jsonify(ok=True, banners=banners, qr=d.get("qrUrl") or "")


@app.post("/api/settings")
@login_required
def settings_save():
    d = request.get_json(silent=True) or {}
    banners = [str(u).strip() for u in (d.get("banners") or []) if str(u).strip()]
    if any(not re.match(r"^https?://", u) for u in banners):
        raise ApiError("Banner URL http:// ya https:// se shuru hona chahiye")
    up = {"banners": banners, "url": banners[0] if banners else ""}
    qr = d.get("qr")
    if qr:
        if not str(qr).startswith("data:image/") or len(qr) > 900_000:
            raise ApiError("QR image galat ya bahut badi hai")
        up["qrUrl"] = qr
    get_db().collection("config").document("banner").set(up, merge=True)  # baaki fields (notice, upi...) safe rehte hain
    return jsonify(ok=True)


# ------------------------------------------------------------------ frontend
HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>FF Admin Lite</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--primary:#4f46e5;--success:#10b981;--danger:#ef4444;--warning:#f59e0b;--bg:#f8fafc;--card:#fff;--border:#e2e8f0;--text:#0f172a;--sub:#64748b;}
*{margin:0;padding:0;box-sizing:border-box;outline:none;-webkit-tap-highlight-color:transparent;}
body{background:var(--bg);color:var(--text);font-family:'Plus Jakarta Sans',sans-serif;padding-bottom:85px;}
.hidden{display:none!important}
/* AUTH */
#auth{position:fixed;inset:0;background:#fff;z-index:9999;display:flex;align-items:center;justify-content:center;padding:30px;}
.auth-wrap{width:100%;max-width:320px;text-align:center;}
.auth-icon{width:80px;height:80px;background:linear-gradient(135deg,#4f46e5,#f43f5e);border-radius:24px;display:flex;align-items:center;justify-content:center;font-size:2.2rem;margin:0 auto 20px;}
/* HEADER */
.top-bar{padding:15px 20px;background:#fff;position:sticky;top:0;z-index:100;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);}
.logo{font-size:1.3rem;font-weight:800;}
/* TABS */
.tab{display:none;} .tab.show{display:block;}
/* STATS */
.stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px;padding:20px;}
.stat-box{padding:20px;border-radius:20px;color:#fff;position:relative;overflow:hidden;}
.stat-box.blue{background:linear-gradient(135deg,#4f46e5,#818cf8);}
.stat-box.green{background:linear-gradient(135deg,#10b981,#34d399);}
.stat-num{font-size:2rem;font-weight:800;}
.stat-lbl{font-size:0.8rem;opacity:0.9;font-weight:600;}
.stat-bg-icon{position:absolute;right:-8px;bottom:-8px;font-size:4rem;opacity:0.15;}
/* MENU GRID */
.menu-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px;padding:0 20px 20px;}
.menu-btn{background:#fff;border:1px solid var(--border);border-radius:20px;padding:20px;display:flex;flex-direction:column;align-items:center;gap:10px;cursor:pointer;}
.menu-btn:active{background:#f1f5f9;transform:scale(0.97);}
.menu-btn-icon{width:48px;height:48px;border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:1.3rem;color:#fff;}
.menu-btn-lbl{font-weight:700;font-size:0.88rem;}
/* CARDS */
.page-pad{padding:0 20px;}
.card{background:#fff;border:1px solid var(--border);border-radius:18px;padding:16px;margin-bottom:12px;}
.card-top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;}
.card-title{font-weight:700;font-size:0.95rem;margin-bottom:3px;}
.card-sub{font-size:0.78rem;color:var(--sub);}
.badge{font-size:0.65rem;padding:4px 10px;border-radius:20px;font-weight:700;text-transform:uppercase;}
.badge-active{background:#fff7ed;color:#ea580c;}
.badge-done{background:#dcfce7;color:#16a34a;}
/* BUTTONS */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:12px 16px;border-radius:14px;border:none;font-weight:700;font-size:0.88rem;cursor:pointer;transition:0.15s;}
.btn:active{transform:scale(0.97);opacity:0.85;}
.btn-primary{background:var(--primary);color:#fff;box-shadow:0 4px 12px rgba(79,70,229,0.3);}
.btn-success{background:var(--success);color:#fff;box-shadow:0 4px 12px rgba(16,185,129,0.3);}
.btn-danger{background:var(--danger);color:#fff;box-shadow:0 4px 12px rgba(239,68,68,0.3);}
.btn-outline{background:#fff;border:1px solid var(--border);color:var(--text);}
.btn-full{width:100%;}
.btn-row{display:flex;gap:10px;}
/* INPUT */
.inp{width:100%;background:#f1f5f9;border:2px solid transparent;color:var(--text);padding:14px 16px;border-radius:14px;margin-bottom:14px;font-size:0.92rem;font-family:'Plus Jakarta Sans',sans-serif;}
.inp:focus{background:#fff;border-color:var(--primary);}
.lbl{font-size:0.8rem;color:var(--sub);font-weight:700;display:block;margin-bottom:6px;}
/* MODAL */
.overlay{position:fixed;inset:0;background:rgba(0,0,0,0.45);backdrop-filter:blur(3px);z-index:500;display:none;align-items:flex-end;}
.overlay.show{display:flex;}
.sheet{background:#fff;width:100%;border-radius:28px 28px 0 0;padding:28px 20px 40px;max-height:92vh;overflow-y:auto;animation:up 0.3s cubic-bezier(0.16,1,0.3,1);}
.sheet::before{content:'';display:block;width:40px;height:5px;background:var(--border);border-radius:5px;margin:0 auto 22px;}
@keyframes up{from{transform:translateY(100%)}to{transform:translateY(0)}}
.sheet-title{font-size:1.2rem;font-weight:800;margin-bottom:20px;}
/* NAV */
.nav{position:fixed;bottom:0;width:100%;background:#fff;border-top:1px solid var(--border);display:flex;padding:10px 0 18px;z-index:200;}
.nav-btn{flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;color:#94a3b8;font-size:0.68rem;font-weight:600;cursor:pointer;}
.nav-btn i{font-size:1.3rem;}
.nav-btn.active{color:var(--primary);}
/* TOAST */
#toast-wrap{position:fixed;bottom:100px;left:50%;transform:translateX(-50%);z-index:9999;display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none;}
.toast{background:rgba(15,23,42,0.92);color:#fff;padding:11px 22px;border-radius:50px;font-size:0.85rem;font-weight:600;display:flex;align-items:center;gap:8px;animation:tin 0.3s forwards;}
.toast.err{background:rgba(239,68,68,0.92);}
.toast.ok{background:rgba(16,185,129,0.92);}
@keyframes tin{from{opacity:0;transform:translateY(15px)}to{opacity:1;transform:translateY(0)}}
@keyframes tout{to{opacity:0;transform:translateY(-12px)}}
/* LOGO UPLOAD */
.logo-box{width:72px;height:72px;border-radius:18px;border:2px dashed #cbd5e1;background:#f8fafc;display:flex;align-items:center;justify-content:center;font-size:2rem;overflow:hidden;flex-shrink:0;cursor:pointer;position:relative;}
.logo-box img{width:100%;height:100%;object-fit:cover;display:none;}
.logo-box-label{position:absolute;bottom:0;left:0;right:0;background:rgba(0,0,0,0.55);color:#fff;font-size:0.5rem;font-weight:800;text-align:center;padding:3px;letter-spacing:0.5px;}
/* FINANCE BADGES */
.f-badge{display:inline-block;padding:3px 9px;border-radius:12px;font-size:0.68rem;font-weight:700;margin-bottom:4px;}
.f-bank{background:#dbeafe;color:#1d4ed8;}
.f-crypto{background:#fef3c7;color:#92400e;}
/* TRX ROW */
.trx-row{display:flex;justify-content:space-between;align-items:center;padding:9px 10px;background:#f8fafc;border-radius:10px;margin-bottom:6px;font-size:0.78rem;border:1px solid #f0f0f0;}
.trx-pos{color:var(--success);font-weight:700;}
.trx-neg{color:var(--danger);font-weight:700;}
/* timer */
.live-t{font-family:monospace;font-weight:700;color:var(--primary);background:#e0e7ff;padding:2px 7px;border-radius:5px;font-size:0.75rem;}
.ended-t{color:var(--danger);background:#fee2e2;padding:2px 7px;border-radius:5px;font-size:0.75rem;font-weight:700;}
/* fab */
.fab{position:fixed;bottom:90px;right:20px;width:54px;height:54px;background:var(--text);border-radius:16px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:1.4rem;box-shadow:0 8px 20px rgba(0,0,0,0.2);cursor:pointer;z-index:100;}
.section-h{font-size:1rem;font-weight:800;padding:0 20px;margin:4px 0 14px;}

/* WALLET UI */
.fin-summary{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:16px 20px 12px;}
.fin-sum-box{border-radius:18px;padding:14px 16px;color:#fff;}
.fin-sum-num{font-size:1.7rem;font-weight:800;line-height:1.1;}
.fin-sum-lbl{font-size:0.72rem;font-weight:700;opacity:.92;}
.fin-sum-amt{font-size:0.9rem;font-weight:800;margin-top:4px;}
.seg{display:flex;background:#e2e8f0;border-radius:14px;padding:4px;gap:4px;}
.seg-btn{flex:1;border:none;background:transparent;padding:10px 6px;border-radius:11px;font-weight:700;font-size:0.8rem;color:var(--sub);cursor:pointer;font-family:inherit;display:flex;align-items:center;justify-content:center;gap:6px;}
.seg-btn.on{background:#fff;color:var(--text);box-shadow:0 2px 8px rgba(0,0,0,.08);}
.seg-cnt{background:#ef4444;color:#fff;border-radius:10px;font-size:0.65rem;padding:1px 7px;font-weight:800;}
.seg-cnt.zero{background:#cbd5e1;}
.nav-badge{position:absolute;top:-4px;left:calc(50% + 6px);background:#ef4444;color:#fff;font-size:0.6rem;padding:1px 6px;border-radius:10px;font-weight:800;}
.fin-card{background:#fff;border:1px solid var(--border);border-radius:20px;padding:16px;margin-bottom:14px;border-left:5px solid #94a3b8;}
.fin-card.pending{border-left-color:#f59e0b;}
.fin-card.success{border-left-color:#10b981;}
.fin-card.rejected{border-left-color:#ef4444;}
.fin-card.refunded{border-left-color:#f59e0b;}
.fin-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:10px;}
.fin-order{font-family:monospace;font-size:0.78rem;font-weight:800;color:var(--primary);}
.fin-date{font-size:0.72rem;color:var(--sub);margin-top:2px;}
.fin-amt{font-size:1.5rem;font-weight:900;text-align:right;line-height:1;}
.fin-st{display:inline-block;font-size:0.62rem;font-weight:800;text-transform:uppercase;padding:3px 9px;border-radius:20px;margin-top:6px;}
.fin-st.pending{background:#fff7ed;color:#ea580c;}
.fin-st.success{background:#dcfce7;color:#16a34a;}
.fin-st.rejected{background:#fee2e2;color:#dc2626;}
.fin-st.refunded{background:#fef3c7;color:#b45309;}
.fin-row{display:flex;align-items:center;justify-content:space-between;gap:8px;background:#f8fafc;border:1px solid #eef2f7;border-radius:12px;padding:8px 10px;margin-bottom:6px;font-size:0.78rem;}
.fin-row .k{color:var(--sub);font-weight:700;font-size:0.68rem;text-transform:uppercase;}
.fin-row .v{font-family:monospace;font-weight:700;word-break:break-all;text-align:right;flex:1;}
.copy-btn{border:none;background:#e0e7ff;color:var(--primary);border-radius:8px;padding:5px 9px;font-size:0.7rem;cursor:pointer;flex-shrink:0;}
.fin-actions{display:flex;gap:8px;margin-top:10px;}
.fin-actions .btn{flex:1;padding:12px 8px;}
.empty-box{text-align:center;padding:50px 20px;color:var(--sub);font-weight:600;}
.empty-box i{font-size:2.4rem;display:block;margin-bottom:10px;opacity:.4;}

/* ================= UI v2 : polish + animations ================= */
:root{--sh1:0 1px 2px rgba(15,23,42,.05),0 6px 18px rgba(15,23,42,.06);--ease:cubic-bezier(.16,1,.3,1)}
html{scroll-behavior:smooth}
body{background:radial-gradient(900px 320px at 50% -80px,#e0e7ff 0,transparent 65%),var(--bg);padding-bottom:calc(92px + env(safe-area-inset-bottom));-webkit-font-smoothing:antialiased}
.top-bar{background:rgba(255,255,255,.82);backdrop-filter:saturate(180%) blur(14px);-webkit-backdrop-filter:saturate(180%) blur(14px);padding-top:calc(15px + env(safe-area-inset-top))}
/* tab + list entrance */
.tab.show{animation:tabIn .4s var(--ease)}
@keyframes tabIn{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
@keyframes rise{from{opacity:0;transform:translateY(18px) scale(.97)}to{opacity:1;transform:none}}
@keyframes pop{from{opacity:0;transform:scale(.4)}to{opacity:1;transform:scale(1)}}
@keyframes fade{from{opacity:0}to{opacity:1}}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(239,68,68,.55)}70%{box-shadow:0 0 0 8px rgba(239,68,68,0)}100%{box-shadow:0 0 0 0 rgba(239,68,68,0)}}
.fresh .card,.fresh .fin-card,.fresh .menu-btn,.fresh .stat-box,.fresh .fin-sum-box{animation:rise .5s var(--ease) both}
.fresh .card:nth-child(2),.fresh .fin-card:nth-child(2),.fresh .menu-btn:nth-child(2),.fresh .stat-box:nth-child(2),.fresh .fin-sum-box:nth-child(2){animation-delay:.06s}
.fresh .card:nth-child(3),.fresh .fin-card:nth-child(3),.fresh .menu-btn:nth-child(3){animation-delay:.12s}
.fresh .card:nth-child(4),.fresh .fin-card:nth-child(4),.fresh .menu-btn:nth-child(4){animation-delay:.18s}
.fresh .card:nth-child(5),.fresh .fin-card:nth-child(5),.fresh .menu-btn:nth-child(5){animation-delay:.24s}
.fresh .card:nth-child(n+6),.fresh .fin-card:nth-child(n+6),.fresh .menu-btn:nth-child(n+6){animation-delay:.3s}
/* cards / buttons / inputs */
.card,.fin-card,.menu-btn{box-shadow:var(--sh1);transition:transform .25s var(--ease),box-shadow .25s}
.card:active,.fin-card:active{transform:scale(.99)}
.menu-btn:active .menu-btn-icon{transform:rotate(-8deg) scale(1.1)}
.menu-btn-icon{transition:transform .3s var(--ease);box-shadow:0 6px 14px rgba(0,0,0,.15)}
.stat-box{box-shadow:0 10px 24px rgba(79,70,229,.2)}
.btn{position:relative;overflow:hidden;transition:transform .15s var(--ease),box-shadow .2s,opacity .2s}
.btn:active{transform:scale(.94)}
.btn:disabled{opacity:.55;pointer-events:none}
.btn-primary{background:linear-gradient(135deg,#4f46e5,#6366f1)}
.btn-success{background:linear-gradient(135deg,#10b981,#34d399)}
.btn-danger{background:linear-gradient(135deg,#ef4444,#f87171)}
.inp{transition:border-color .2s,box-shadow .2s,background .2s}
.inp:focus{box-shadow:0 0 0 4px rgba(79,70,229,.12)}
/* modal */
.overlay.show{animation:fade .25s}
.sheet{animation:up .5s var(--ease);padding-bottom:calc(40px + env(safe-area-inset-bottom));overscroll-behavior:contain}
/* toast */
.toast{box-shadow:0 10px 28px rgba(0,0,0,.22);backdrop-filter:blur(8px)}
/* nav */
.nav{background:rgba(255,255,255,.9);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);padding-bottom:calc(18px + env(safe-area-inset-bottom));box-shadow:0 -8px 26px rgba(15,23,42,.07)}
.nav-btn{position:relative;transition:color .25s}
.nav-btn i{transition:transform .35s var(--ease)}
.nav-btn.active i{transform:translateY(-3px) scale(1.18)}
.nav-btn.active::after{content:'';position:absolute;bottom:-9px;width:20px;height:4px;border-radius:4px;background:var(--primary);animation:pop .35s var(--ease)}
.nav-badge,#rep-badge,.seg-cnt:not(.zero){animation:pulse 1.8s infinite}
.fab{animation:pop .5s var(--ease);transition:transform .2s var(--ease)}
.fab:active{transform:scale(.88) rotate(90deg)}
.seg-btn{transition:all .25s var(--ease)}
.empty-box i.fa-spin{opacity:.6}
#net-banner{position:fixed;top:0;left:0;right:0;z-index:10000;background:#ef4444;color:#fff;text-align:center;font-size:.8rem;font-weight:700;padding:calc(6px + env(safe-area-inset-top)) 0 6px;transform:translateY(-110%);transition:transform .35s var(--ease)}
#net-banner.show{transform:none}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}

/* ===== SERVER-BACKED TABS (login, backup, data delete, bot) ===== */
.muted{color:var(--sub);font-size:0.8rem;line-height:1.45;}
.card-h{font-weight:800;margin-bottom:8px;}
.lbl{display:block;font-size:0.76rem;font-weight:700;color:var(--sub);margin:2px 0 6px;}
.chk{display:flex;align-items:center;gap:8px;font-size:0.82rem;font-weight:600;margin:4px 0 14px;}
.chk input{width:18px;height:18px;accent-color:var(--primary);}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 12px;}
.chips label{display:flex;align-items:center;gap:6px;background:#f1f5f9;border:1px solid var(--border);border-radius:20px;padding:6px 12px;font-size:0.78rem;font-weight:700;}
.chips input{accent-color:var(--primary);}
.warn{background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;border-radius:14px;padding:12px 14px;font-size:0.8rem;font-weight:600;line-height:1.45;margin-bottom:12px;}
.row{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--border);}
.row:last-child{border-bottom:none;}
.row-t{font-weight:700;font-size:0.86rem;word-break:break-all;}
.row-s{font-size:0.72rem;color:var(--sub);margin-top:2px;}
.row-btns{display:flex;gap:6px;flex-shrink:0;}
.mini{padding:7px 11px;font-size:0.74rem;border-radius:10px;}
.kv{display:flex;justify-content:space-between;gap:12px;padding:7px 0;font-size:0.82rem;border-bottom:1px dashed var(--border);}
.kv:last-child{border-bottom:none;}
.kv span:first-child{color:var(--sub);font-weight:600;}
.kv span:last-child{font-weight:700;text-align:right;word-break:break-all;}
.pill{font-size:0.7rem;font-weight:800;padding:5px 12px;border-radius:20px;background:#e2e8f0;color:#475569;}
.pill.ok{background:#dcfce7;color:#15803d;}
.pill.err{background:#fee2e2;color:#b91c1c;}
.pill.warn{background:#fef3c7;color:#b45309;}
.logbox{background:#0f172a;color:#cbd5e1;font-family:ui-monospace,Menlo,monospace;font-size:0.68rem;line-height:1.5;border-radius:12px;padding:10px 12px;max-height:240px;overflow-y:auto;word-break:break-word;}
.logbox .e{color:#fca5a5;} .logbox .w{color:#fcd34d;} .logbox .k{color:#86efac;}
.bot-dot{position:absolute;top:10px;right:10px;width:10px;height:10px;border-radius:50%;background:#cbd5e1;}
.bot-dot.on{background:#10b981;box-shadow:0 0 0 4px rgba(16,185,129,.2);} .bot-dot.bad{background:#ef4444;}
.job-bar{position:fixed;left:12px;right:12px;bottom:92px;z-index:450;background:#0f172a;color:#fff;border-radius:16px;padding:13px 16px;font-size:0.82rem;font-weight:600;display:flex;gap:10px;align-items:center;box-shadow:0 10px 30px rgba(0,0,0,.3);}
.job-bar.ok{background:#047857;} .job-bar.err{background:#b91c1c;}

</style></head><body>
<div id="toast-wrap"></div>

<div id="auth"><div class="auth-wrap">
  <div class="auth-icon">🔐</div>
  <h2 style="font-size:1.4rem;font-weight:800;margin-bottom:6px;">FF Admin Lite</h2>
  <p id="a-loading" style="color:#64748b;font-size:0.85rem;margin:18px 0;"><i class="fa-solid fa-spinner fa-spin"></i> Checking session...</p>
  <form id="login-form" class="hidden" onsubmit="event.preventDefault();login()" style="text-align:left;margin-top:18px;">
    <input class="inp" id="a-email" type="email" autocomplete="username" placeholder="Admin email" required>
    <input class="inp" id="a-pass" type="password" autocomplete="current-password" placeholder="Password" required>
    <input class="inp" id="a-pin" type="password" inputmode="numeric" autocomplete="off" placeholder="PIN" required>
    <div id="a-err" style="color:#ef4444;font-size:0.8rem;min-height:20px;margin:-4px 0 10px;text-align:center;"></div>
    <button class="btn btn-primary btn-full" id="a-btn" type="submit"><i class="fa-solid fa-lock-open"></i> Login</button>
  </form>
  <div id="a-err2" style="color:#ef4444;font-size:0.8rem;margin-top:10px;"></div>
</div></div>

<div id="panel" class="hidden">
  <div id="tab-matches" class="tab show">
    <div class="top-bar"><div class="logo"><span style="color:var(--primary)">FF</span> Matches</div>
      <button class="btn btn-outline" style="padding:8px 14px;font-size:0.8rem;" onclick="logout()"><i class="fa-solid fa-right-from-bracket"></i></button></div>
    <div class="page-pad" id="match-list" style="padding-top:16px;"></div>
    <div class="fab" onclick="openMatch()"><i class="fa-solid fa-plus"></i></div>
  </div>

  <div id="tab-finance" class="tab">
    <div class="top-bar"><h2 style="font-size:1.1rem;font-weight:800;">Wallet Requests</h2>
      <button class="btn btn-outline" style="padding:8px 14px;font-size:0.8rem;" onclick="loadFin(true)"><i class="fa-solid fa-rotate"></i></button></div>
    <div class="seg" style="margin:16px 20px 10px;">
      <button id="btn-dep" class="seg-btn on" onclick="finType='deposit';renderFin()"><i class="fa-solid fa-arrow-down"></i> Deposits <span class="seg-cnt" id="cnt-dep">0</span></button>
      <button id="btn-wd" class="seg-btn" onclick="finType='withdraw';renderFin()"><i class="fa-solid fa-arrow-up"></i> Withdrawals <span class="seg-cnt" id="cnt-wd">0</span></button>
    </div>
    <div style="padding:0 20px 10px;">
      <input id="fin-search" class="inp" style="margin:0 0 8px;" placeholder="🔍 Order # / UID / UTR search..." onkeyup="finQ=this.value.toLowerCase().trim();renderFin()">
      <div class="seg">
        <button id="st-pending" class="seg-btn on" onclick="finStatus='pending';loadFin()">⏳ Pending</button>
        <button id="st-all" class="seg-btn" onclick="finStatus='all';loadFin()">📜 All History</button>
      </div>
    </div>
    <div class="page-pad" id="finance-list"></div>
  </div>

  <div id="tab-users" class="tab">
    <div class="top-bar"><h2 style="font-size:1.1rem;font-weight:800;">Users</h2></div>
    <div style="padding:16px 20px 8px;"><input class="inp" style="margin:0;" placeholder="🔍 Name / UID / Phone search..." onkeyup="renderUsers(this.value)"></div>
    <div class="page-pad" id="user-list"></div>
  </div>

  <div id="tab-settings" class="tab">
    <div class="top-bar"><h2 style="font-size:1.1rem;font-weight:800;">Payment & Banner</h2></div>
    <div class="page-pad" style="padding-top:16px;">
      <div class="card">
        <div class="lbl">Payment QR</div>
        <div style="display:flex;gap:14px;align-items:center;margin-bottom:6px;">
          <label class="logo-box" style="width:110px;height:110px;"><span id="qr-emoji">📷</span><img id="qr-img"><div class="logo-box-label">UPLOAD QR</div>
            <input type="file" accept="image/*" style="display:none" onchange="pickQr(this)"></label>
          <div id="qr-msg" style="font-size:0.78rem;color:var(--sub);">QR image select karo, phir Save dabao.</div>
        </div>
      </div>
      <div class="card">
        <div class="lbl">Top Banner URLs</div>
        <div id="banner-list" style="display:flex;flex-direction:column;gap:8px;margin-bottom:10px;"></div>
        <button class="btn btn-outline btn-full" onclick="addBanner('')"><i class="fa-solid fa-plus"></i> Banner add</button>
      </div>
      <button class="btn btn-primary btn-full" onclick="saveSettings()"><i class="fa-solid fa-floppy-disk"></i> Save</button>
    </div>
  </div>

  <div class="nav">
    <div class="nav-btn active" id="nav-matches" onclick="goTab('matches')"><i class="fa-solid fa-trophy"></i><span>Matches</span></div>
    <div class="nav-btn" id="nav-finance" onclick="goTab('finance')" style="position:relative;"><i class="fa-solid fa-wallet"></i><span>Wallet</span><b id="nav-fin-badge" class="nav-badge" style="display:none;">0</b></div>
    <div class="nav-btn" id="nav-users" onclick="goTab('users')"><i class="fa-solid fa-users"></i><span>Users</span></div>
    <div class="nav-btn" id="nav-settings" onclick="goTab('settings')"><i class="fa-solid fa-qrcode"></i><span>Payment</span></div>
  </div>
</div>

<!-- CREATE MATCH -->
<div class="overlay" id="m-match"><div class="sheet">
  <div class="sheet-title">Match Banao</div>
  <div class="lbl">Match Title</div><input class="inp" id="m-title" placeholder="e.g. Bermuda Solo #1">
  <div style="display:flex;gap:12px;">
    <div style="flex:1;"><div class="lbl">Entry Fee ₹</div><input class="inp" id="m-fee" type="number" placeholder="0"></div>
    <div style="flex:1;"><div class="lbl">Prize / Per Kill ₹</div><input class="inp" id="m-prize" type="number" placeholder="0"></div>
  </div>
  <div class="lbl">Match Type</div>
  <select class="inp" id="m-match-type" style="background:#fff;" onchange="$('team-mode-row').classList.toggle('hidden',this.value!=='team_win')">
    <option value="win_prize">Win Prize Match</option><option value="per_kill">Per Kill Match</option><option value="team_win">Team Win Match</option>
  </select>
  <div id="team-mode-row" class="hidden"><div class="lbl">Team Mode</div>
    <select class="inp" id="m-team-mode" style="background:#fff;"><option>BR Duo</option><option>BR Squad</option><option>Clash Squad</option><option>Lone Wolf</option></select></div>
  <div class="lbl">Map</div>
  <select class="inp" id="m-map" style="background:#fff;"><optgroup label="Bermuda"><option>Bermuda</option><option>Bermuda Remastered</option></optgroup>
    <option>Purgatory</option><option>Kalahari</option><option>Alpine</option><option>NexTerra</option><option>Solara</option></select>
  <div style="display:flex;gap:12px;">
    <div style="flex:1;"><div class="lbl">Start Time</div><input class="inp" id="m-time" type="datetime-local" style="background:#fff;"></div>
    <div style="flex:1;"><div class="lbl">Total Slots</div><input class="inp" id="m-slots" type="number" placeholder="48"></div>
  </div>
  <div class="lbl">Banner Image URL</div><input class="inp" id="m-img" placeholder="https://...">
  <div class="btn-row"><button class="btn btn-outline" style="flex:1;" onclick="closeM('m-match')">Cancel</button>
    <button class="btn btn-primary" style="flex:1;" onclick="createMatch()">🚀 Publish</button></div>
</div></div>

<!-- ROOM -->
<div class="overlay" id="m-room"><div class="sheet">
  <div class="sheet-title">Room Details</div><input type="hidden" id="room-mid">
  <div class="lbl">Room ID</div><input class="inp" id="room-id" placeholder="Room ID">
  <div class="lbl">Password</div><input class="inp" id="room-pass" placeholder="Password">
  <div class="btn-row"><button class="btn btn-outline" style="flex:1;" onclick="closeM('m-room')">Cancel</button>
    <button class="btn btn-primary" style="flex:1;" onclick="saveRoom()">Save</button></div>
</div></div>

<!-- PLAYERS + PRIZE -->
<div class="overlay" id="m-players"><div class="sheet">
  <div class="sheet-title" id="players-title">Players</div><input type="hidden" id="players-mid">
  <div id="pay-box"></div>
  <div id="players-list"></div>
  <button class="btn btn-outline btn-full" style="margin-top:12px;" onclick="closeM('m-players')">Close</button>
</div></div>

<!-- USER -->
<div class="overlay" id="m-user"><div class="sheet">
  <div class="sheet-title" id="u-title">User</div><input type="hidden" id="u-id">
  <div id="u-details" style="background:#eef2ff;padding:14px;border-radius:14px;margin-bottom:14px;font-size:0.82rem;line-height:1.7;"></div>
  <div style="background:#f1f5f9;padding:14px;border-radius:14px;margin-bottom:14px;">
    <div class="lbl">Balance Add / Remove</div>
    <select class="inp" id="u-bal-type" style="background:#fff;margin-bottom:10px;"><option value="deposit">Deposit Balance</option><option value="withdraw">Withdrawal Balance</option></select>
    <input class="inp" id="u-bal" type="number" min="0" style="background:#fff;margin-bottom:10px;" placeholder="Amount ₹">
    <div class="btn-row"><button class="btn btn-success" style="flex:1;" onclick="adjust(1)"><i class="fa-solid fa-plus"></i> Add</button>
      <button class="btn btn-danger" style="flex:1;" onclick="adjust(-1)"><i class="fa-solid fa-minus"></i> Remove</button></div>
  </div>
  <div style="background:#fff7ed;border:1px solid #fed7aa;padding:14px;border-radius:14px;margin-bottom:14px;">
    <div style="font-weight:800;font-size:0.82rem;color:var(--warning);margin-bottom:8px;"><i class="fa-solid fa-ban"></i> Ban Control</div>
    <div id="u-ban-info" style="font-size:0.8rem;margin-bottom:10px;"></div>
    <div class="lbl">Duration</div>
    <select class="inp" id="u-ban-dur" style="background:#fff;margin-bottom:10px;"><option value="1">1 Hour</option><option value="24">1 Day</option><option value="168">1 Week</option><option value="720">1 Month</option><option value="0">Permanent</option></select>
    <div class="btn-row"><button class="btn btn-danger" style="flex:1;" onclick="ban(1)"><i class="fa-solid fa-ban"></i> Ban</button>
      <button class="btn btn-success" style="flex:1;" onclick="ban(0)"><i class="fa-solid fa-check"></i> Unban</button></div>
  </div>
  <div style="background:#f8fafc;border:1px solid var(--border);padding:14px;border-radius:14px;margin-bottom:14px;">
    <div style="font-weight:800;font-size:0.85rem;margin-bottom:10px;">📋 Transaction History</div>
    <div id="u-trx" style="max-height:180px;overflow-y:auto;"></div>
  </div>
  <button class="btn btn-outline btn-full" onclick="closeM('m-user')">Close</button>
</div></div>

<!-- PIN -->
<div class="overlay" id="m-pin"><div class="sheet">
  <div class="sheet-title" id="pin-title">PIN daalo</div>
  <div id="pin-text" style="font-size:0.85rem;color:var(--sub);margin-bottom:14px;"></div>
  <input class="inp" id="pin-in" type="password" inputmode="numeric" placeholder="PIN" autocomplete="off">
  <div class="btn-row"><button class="btn btn-outline" style="flex:1;" onclick="closeM('m-pin')">Cancel</button>
    <button class="btn btn-primary" style="flex:1;" id="pin-ok">Confirm</button></div>
</div></div>

<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/[<>&"']/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));
const num=v=>parseFloat(String(v||0).replace(/[^0-9.]/g,''))||0;
const closeM=id=>$(id).classList.remove('show'), openM=id=>$(id).classList.add('show');
function toast(m,t='ok'){const d=document.createElement('div');d.className='toast '+t;d.innerHTML='<i class="fa-solid '+(t==='ok'?'fa-check-circle':'fa-circle-exclamation')+'"></i> '+esc(m);$('toast-wrap').appendChild(d);setTimeout(()=>{d.style.animation='tout .3s forwards';setTimeout(()=>d.remove(),350)},2800)}
const fail=e=>toast(e.message||'Error','err');
document.addEventListener('click',e=>{if(e.target.classList&&e.target.classList.contains('overlay'))e.target.classList.remove('show')});
async function api(path,body){
  const init={method:body===undefined?'GET':'POST',credentials:'same-origin',headers:{'X-Requested-With':'ffadmin'}};
  if(body!==undefined){init.headers['Content-Type']='application/json';init.body=JSON.stringify(body)}
  let r;try{r=await fetch(path,init)}catch(e){throw new Error('Server se connect nahi ho paya')}
  let j={};try{j=await r.json()}catch(e){}
  if(r.status===401&&j.code==='auth'){location.reload();throw new Error('Session expire ho gaya')}
  if(!r.ok||j.ok===false)throw new Error(j.error||('HTTP '+r.status));
  return j;
}
function askPin(title,text){return new Promise(res=>{$('pin-title').innerText=title;$('pin-text').innerText=text||'';$('pin-in').value='';openM('m-pin');setTimeout(()=>$('pin-in').focus(),150);
  $('pin-ok').onclick=()=>{const p=$('pin-in').value.trim();if(!p)return toast('PIN daalo','err');closeM('m-pin');res(p)}})}

/* ---- auth ---- */
function showLogin(msg){$('panel').classList.add('hidden');$('auth').style.display='flex';$('a-loading').classList.add('hidden');$('login-form').classList.remove('hidden');$('a-err2').innerText=msg||''}
async function login(){const b=$('a-btn');$('a-err').innerText='';b.disabled=true;
  try{await api('/api/login',{email:$('a-email').value,password:$('a-pass').value,pin:$('a-pin').value});location.reload()}
  catch(e){$('a-err').innerText='❌ '+e.message;$('a-pin').value=''}finally{b.disabled=false}}
async function logout(){if(!confirm('Logout?'))return;try{await api('/api/logout',{})}catch(e){}location.reload()}
(async()=>{let s;try{s=await api('/api/session')}catch(e){return showLogin(e.message)}
  if(!s.ok)return showLogin(s.ready?(s.firebase?'':'FIREBASE_64 env set nahi hai'):'ADMIN_EMAIL / ADMIN_PASSWORD / ADMIN_PIN env set nahi hain');
  $('auth').style.display='none';$('panel').classList.remove('hidden');loadMatches();loadFin();setInterval(()=>{if($('tab-finance').classList.contains('show'))loadFin()},15000)})();

function goTab(id){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('show'));document.querySelectorAll('.nav-btn').forEach(n=>n.classList.remove('active'));
  $('tab-'+id).classList.add('show');$('nav-'+id).classList.add('active');
  if(id==='matches')loadMatches();if(id==='finance')loadFin();if(id==='users')loadUsers();if(id==='settings')loadSettings()}

/* ---- matches ---- */
async function loadMatches(){const list=$('match-list');
  try{const {matches}=await api('/api/matches');
    list.innerHTML=matches.length?matches.map(m=>`<div class="card"><div class="card-top"><div>
      <div class="card-title">${esc(m.title)}</div><div class="card-sub">🕒 ${esc(m.startTime?new Date(m.startTime).toLocaleString():'—')}</div>
      <div class="card-sub" style="color:var(--primary);margin-top:2px;">${m.matchType==='per_kill'?'🎯 Per Kill':m.matchType==='team_win'?'👥 Team Win ('+esc(m.teamMode)+')':'🏆 Win Prize'} • ${esc(m.map||'Bermuda')} • ₹${esc(m.fee)} fee</div></div>
      <span class="badge ${m.status==='completed'?'badge-done':'badge-active'}">${esc(m.status||'active')}</span></div>
      <button class="btn btn-outline btn-full" style="margin-bottom:10px;" onclick="showPlayers('${esc(m.id)}')"><i class="fa-solid fa-users"></i> Players / Prize (${m.joined||0})</button>
      <div class="btn-row"><button class="btn btn-outline" style="flex:1;" onclick="roomModal('${esc(m.id)}')"><i class="fa-solid fa-key"></i> Room</button>
      <button class="btn btn-success" style="flex:1;" onclick="completeMatch('${esc(m.id)}')"><i class="fa-solid fa-flag-checkered"></i></button>
      <button class="btn btn-danger" style="flex:1;" onclick="delMatch('${esc(m.id)}')"><i class="fa-solid fa-trash"></i></button></div></div>`).join(''):'<p style="text-align:center;padding:20px;color:var(--sub);">No matches yet</p>'
  }catch(e){list.innerHTML='<p style="color:red;padding:20px;">'+esc(e.message)+'</p>'}}
function openMatch(){openM('m-match')}
async function createMatch(){const t=$('m-match-type').value;
  const d={title:$('m-title').value,fee:$('m-fee').value,prize:$('m-prize').value,matchType:t,teamMode:t==='team_win'?$('m-team-mode').value:'',map:$('m-map').value,startTime:$('m-time').value,total:$('m-slots').value,img:$('m-img').value};
  if(!d.title.trim())return toast('Title daalo','err');if(!confirm('Match create karein?'))return;
  try{await api('/api/matches',d);toast('Match created!');closeM('m-match');['m-title','m-fee','m-prize','m-time','m-slots','m-img'].forEach(i=>$(i).value='');loadMatches()}catch(e){fail(e)}}
async function delMatch(id){if(!confirm('Delete match?'))return;try{await api('/api/matches/'+id+'/delete',{});toast('Deleted');loadMatches()}catch(e){fail(e)}}
function roomModal(id){$('room-mid').value=id;$('room-id').value='';$('room-pass').value='';openM('m-room')}
async function saveRoom(){try{await api('/api/matches/'+$('room-mid').value+'/room',{roomId:$('room-id').value,roomPass:$('room-pass').value});toast('Room updated!');closeM('m-room')}catch(e){fail(e)}}
async function completeMatch(id){const w=prompt('Winner Name:');if(w===null)return;try{await api('/api/matches/'+id+'/complete',{winner:w||'N/A'});toast('Match completed!');loadMatches()}catch(e){fail(e)}}

async function showPlayers(id){$('players-mid').value=id;$('pay-box').innerHTML='';$('players-list').innerHTML='<div class="empty-box"><i class="fa-solid fa-spinner fa-spin"></i>Loading...</div>';openM('m-players');
  try{const m=(await api('/api/matches/'+id)).match,ps=m.participants||[],mt=m.matchType||'win_prize';
    $('players-title').innerText='Joined Players ('+ps.length+')';
    const opt=ps.map(p=>`<option value="${esc(p.uid)}">${esc(p.appName||'User')} (${esc(p.gameUid||'---')})</option>`).join('');
    const who=p=>`${esc(p.appName||'User')}<br><span style="font-size:0.68rem;color:var(--sub);">${esc(p.gameUid||'---')}</span>`;
    let h='';
    if(m.paid)h='<div class="card" style="background:#ecfdf5;border-color:#a7f3d0;color:#047857;font-weight:700;font-size:0.85rem;">✅ Is match ka prize ho chuka hai.</div>';
    else if(mt==='win_prize')h=`<div class="card"><div class="card-title" style="margin-bottom:10px;">🏆 Win Prize (50% / 30% / 20%)</div>
      <div class="lbl">Total Prize ₹</div><input class="inp" id="wp-amount" type="number" value="${esc(m.prize||'')}">
      ${['1st','2nd','3rd'].map((r,i)=>`<div class="lbl">${r} Winner</div><select class="inp" id="wp-${i+1}" style="background:#fff;"><option value="">-- Select --</option>${opt}</select>`).join('')}
      <button class="btn btn-success btn-full" onclick="pay('win_prize')">💰 Distribute Prize</button></div>`;
    else if(mt==='per_kill')h=`<div class="card"><div class="card-title" style="margin-bottom:10px;">🎯 Per Kill ₹${esc(m.prize||0)}</div>
      ${ps.map(p=>`<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;"><div style="flex:1;font-weight:600;font-size:0.82rem;">${who(p)}</div><input type="number" min="0" placeholder="Kills" class="inp kill-in" style="width:80px;margin:0;" data-uid="${esc(p.uid)}"></div>`).join('')}
      <button class="btn btn-success btn-full" onclick="pay('per_kill')">💰 Credit Kills</button></div>`;
    else h=`<div class="card"><div class="card-title" style="margin-bottom:6px;">👥 Team Win — ₹${esc(m.prize||0)} per player</div><div class="card-sub" style="margin-bottom:10px;">Winning team ke members tick karo.</div>
      ${ps.map(p=>`<label style="display:flex;align-items:center;gap:10px;background:#fff;border-radius:10px;padding:10px;margin-bottom:6px;border:1px solid #e2e8f0;"><input type="checkbox" class="tw-in" value="${esc(p.uid)}" style="width:18px;height:18px;accent-color:var(--success);"><span style="font-weight:600;font-size:0.85rem;">${who(p)}</span></label>`).join('')}
      <button class="btn btn-success btn-full" onclick="pay('team_win')">💰 Reward Team</button></div>`;
    $('pay-box').innerHTML=h;
    $('players-list').innerHTML=ps.length?'<div style="font-weight:700;font-size:0.82rem;color:var(--sub);margin-bottom:8px;">All Players:</div>'+ps.map(p=>`<div style="background:#f8fafc;border-radius:10px;padding:10px;margin-bottom:6px;border:1px solid #f0f0f0;"><b style="font-size:0.85rem;">${esc(p.appName||'User')}</b> <span style="font-size:0.72rem;color:var(--sub);">${esc(p.gameName||'---')} • FF: ${esc(p.gameUid||'---')} • Slot #${esc(p.slot||'-')}${p.winAmount?' • ₹'+esc(p.winAmount):''}</span></div>`).join(''):'<p style="text-align:center;padding:20px;color:var(--sub);">No players joined yet</p>';
  }catch(e){$('players-list').innerHTML='<p style="color:red;">'+esc(e.message)+'</p>'}}
async function pay(kind){const id=$('players-mid').value,d={kind};
  if(kind==='win_prize'){d.amount=$('wp-amount').value;d.w1=$('wp-1').value;d.w2=$('wp-2').value;d.w3=$('wp-3').value;if(!d.w1)return toast('1st winner select karo','err');if(num(d.amount)<=0)return toast('Amount daalo','err')}
  if(kind==='per_kill'){d.kills={};document.querySelectorAll('.kill-in').forEach(i=>{if(num(i.value)>0)d.kills[i.dataset.uid]=num(i.value)});if(!Object.keys(d.kills).length)return toast('Kisi ka bhi kill count nahi dala','err')}
  if(kind==='team_win'){d.uids=[...document.querySelectorAll('.tw-in:checked')].map(i=>i.value);if(!d.uids.length)return toast('Winning team members tick karo','err')}
  if(!confirm('Prize credit karein? Yeh dobara nahi ho sakta.'))return;
  try{const r=await api('/api/matches/'+id+'/pay',d);toast(r.paid+' player(s) credited!');closeM('m-players');loadMatches()}catch(e){fail(e)}}

/* ---- wallet ---- */
let finType='deposit',finStatus='pending',finQ='',finItems=[];
async function loadFin(manual){try{finItems=(await api('/api/wallet?status='+finStatus)).items;renderFin();if(manual)toast('Refreshed')}catch(e){if(manual||finStatus==='all')fail(e)}}
function copyText(t){try{navigator.clipboard.writeText(t);toast('Copied')}catch(e){}}
function renderFin(){
  const pend=finStatus==='pending'?finItems:finItems.filter(t=>t.status==='pending'),pd=pend.filter(t=>t.type==='deposit').length,pw=pend.filter(t=>t.type==='withdraw').length;
  $('cnt-dep').innerText=pd;$('cnt-wd').innerText=pw;$('cnt-dep').classList.toggle('zero',!pd);$('cnt-wd').classList.toggle('zero',!pw);
  const nb=$('nav-fin-badge');nb.style.display=pend.length?'inline-block':'none';nb.innerText=pend.length;
  $('btn-dep').classList.toggle('on',finType==='deposit');$('btn-wd').classList.toggle('on',finType==='withdraw');
  $('st-pending').classList.toggle('on',finStatus==='pending');$('st-all').classList.toggle('on',finStatus==='all');
  let items=finItems.filter(t=>t.type===finType);
  if(finQ)items=items.filter(t=>(t.order||'').toLowerCase().includes(finQ)||(t.uid||'').toLowerCase().includes(finQ)||String(t.trxId||t.number||'').toLowerCase().includes(finQ));
  if(!items.length){$('finance-list').innerHTML='<div class="empty-box"><i class="fa-solid fa-inbox"></i>Koi record nahi mila</div>';return}
  $('finance-list').innerHTML=items.map(t=>{const amt=num(t.amount),st=t.status||'pending',utr=t.trxId||t.number||'',wd=t.type==='withdraw';
    const row=(k,v,c)=>`<div class="fin-row"><span class="k">${k}</span><span class="v">${v}</span>${c?`<button class="copy-btn" onclick="copyText('${esc(c)}')"><i class="fa-regular fa-copy"></i></button>`:''}</div>`;
    let r=row('User',esc(t.uid.substring(0,14))+'…',t.uid);if(utr)r+=row('UTR / Tx',esc(utr),utr);
    if(wd){if(t.method==='BANK')r+=row('🏦 Bank',`${esc(t.accountName||'')}<br>A/C: ${esc(t.accountNumber||'')}<br>IFSC: ${esc(t.ifsc||'')}`,t.accountNumber||'');
      else if(t.method==='CRYPTO')r+=row('₿ '+esc(t.coin||'Crypto')+'<br>'+esc(t.network||''),esc(t.walletAddress||''),t.walletAddress||'');
      r+=`<div class="fin-row" style="background:#ecfdf5;border-color:#a7f3d0;"><span class="k" style="color:#047857;">User ko milega</span><span class="v" style="color:#047857;">₹${Math.max(0,amt-2).toFixed(2)} <span style="font-weight:600;">(₹2 fee ke baad)</span></span></div>`}
    const a=x=>`proc('${esc(t.uid)}','${esc(t.id)}','${x}')`,rf=`<button class="btn btn-outline" style="color:#f59e0b;border-color:#f59e0b;" onclick="${a('refund')}">↩ Refund</button>`;
    let act='';if(st==='pending')act=`<div class="fin-actions"><button class="btn btn-success" onclick="${a('approve')}">✅ Accept</button><button class="btn btn-danger" onclick="${a('reject')}">❌ Reject</button>${wd?rf:''}</div>`;
    else if(wd&&st==='rejected')act=`<div class="fin-actions">${rf}</div>`;
    return `<div class="fin-card ${esc(st)}"><div class="fin-head"><div><div class="fin-order">${esc(t.order)}</div><div class="fin-date">${esc(t.method||t.type)} • ${esc(t.date||'')}</div></div>
      <div><div class="fin-amt" style="color:${wd?'var(--danger)':'var(--success)'};">${wd?'-':'+'}₹${amt}</div><div style="text-align:right;"><span class="fin-st ${esc(st)}">${esc(st)}</span></div></div></div>${r}${act}</div>`}).join('')}
async function proc(uid,id,action){const L={approve:'ACCEPT (Success)',reject:'REJECT (No refund)',refund:'REFUND (Return money)'};if(!confirm(L[action]+'?'))return;
  try{const r=await api('/api/wallet/process',{uid,id,action});toast(r.message||'Done!');loadFin()}catch(e){fail(e);loadFin()}}

/* ---- users ---- */
let allUsers=[];
async function loadUsers(){$('user-list').innerHTML='<div class="empty-box"><i class="fa-solid fa-spinner fa-spin"></i></div>';try{allUsers=(await api('/api/users')).users;renderUsers('')}catch(e){$('user-list').innerHTML='<p style="color:red;padding:20px;">'+esc(e.message)+'</p>'}}
function renderUsers(q){q=(q||'').toLowerCase();const arr=q?allUsers.filter(u=>(u.appName||'').toLowerCase().includes(q)||(u.phone||'').includes(q)||u.id===q):allUsers;
  $('user-list').innerHTML=arr.length?arr.map(u=>`<div class="card" style="${u.isBanned?'border-color:var(--danger);':''}"><div class="card-top"><div>
    <div class="card-title">${esc(u.appName||'No Name')} ${u.isBanned?'<span style="background:#fee2e2;color:var(--danger);font-size:0.6rem;padding:2px 7px;border-radius:10px;font-weight:700;">BANNED</span>':''}</div>
    <div class="card-sub"><i class="fa-solid fa-phone"></i> ${esc(u.phone||'N/A')}</div><div class="card-sub">🎮 FF ID: ${esc(u.gameUid||'---')} ${u.gameName?'• '+esc(u.gameName):''}</div>
    <div style="font-size:0.68rem;color:var(--sub);">Player ID: ${esc(u.playerId||'---')} | DB: ${esc(u.id.substring(0,8))}</div></div>
    <div style="text-align:right;"><div style="font-size:1.05rem;font-weight:900;color:var(--primary);">₹${(u.dep+u.wd).toFixed(2)}</div><div style="font-size:0.6rem;color:var(--sub);">Dep ₹${u.dep.toFixed(0)} • Wd ₹${u.wd.toFixed(0)}</div></div></div>
    <button class="btn btn-outline btn-full" onclick="userModal('${esc(u.id)}')">Manage</button></div>`).join(''):'<p style="text-align:center;padding:20px;color:var(--sub);">No users</p>'}
async function userModal(uid){$('u-id').value=uid;$('u-bal').value='';$('u-details').innerHTML='';$('u-ban-info').innerHTML='';$('u-trx').innerHTML='<p style="font-size:0.8rem;color:var(--sub);text-align:center;">Loading...</p>';openM('m-user');
  try{const {user:u,trx}=await api('/api/users/'+uid);$('u-title').innerText=u.appName||'User';
    $('u-details').innerHTML=`<div><b>Name:</b> ${esc(u.appName||'---')}</div><div><b>Free Fire Name:</b> ${esc(u.gameName||'---')}</div><div><b>Free Fire ID:</b> ${esc(u.gameUid||'---')}</div><div><b>Phone:</b> ${esc(u.phone||'---')}</div>
      <div><b>Deposit Balance:</b> ₹${u.dep.toFixed(2)}</div><div><b>Withdrawal Balance:</b> ₹${u.wd.toFixed(2)}</div><div><b>Total:</b> ₹${(u.dep+u.wd).toFixed(2)}</div>`;
    $('u-ban-info').innerHTML=u.isBanned?`<span style="color:var(--danger);font-weight:700;">⛔ Banned — ${u.bannedUntil==='permanent'?'Permanent':'Until: '+esc(new Date(u.bannedUntil).toLocaleString())}</span>`:'<span style="color:var(--success);font-weight:700;">✅ Not Banned</span>';
    $('u-trx').innerHTML=trx.length?trx.map(t=>{const pos=String(t.amount).startsWith('+');return `<div class="trx-row"><div><div style="font-weight:700;font-size:0.78rem;">${esc(t.title)}</div><div style="font-size:0.68rem;color:var(--sub);">${esc(t.date)} • ${esc(t.status)}</div></div><div class="${pos?'trx-pos':'trx-neg'}">${esc(t.amount)}</div></div>`}).join(''):'<p style="font-size:0.8rem;color:var(--sub);text-align:center;">No transactions</p>';
  }catch(e){fail(e)}}
async function adjust(sign){const uid=$('u-id').value,a=parseFloat($('u-bal').value),type=$('u-bal-type').value;
  if(isNaN(a)||a<=0)return toast('Amount daalo (positive)','err');
  const pin=await askPin(sign>0?'Balance ADD':'Balance REMOVE',`₹${a} ${sign>0?'add':'remove'} — ${type==='withdraw'?'Withdrawal':'Deposit'} balance`);if(!pin)return;
  try{await api('/api/users/'+uid+'/balance',{amount:sign*a,type,pin});toast('Balance updated!');closeM('m-user');loadUsers()}catch(e){fail(e)}}
async function ban(on){const uid=$('u-id').value;if(!confirm(on?'Ban karein?':'Unban karein?'))return;
  try{await api('/api/users/'+uid+(on?'/ban':'/unban'),on?{hours:parseInt($('u-ban-dur').value)}:{});toast(on?'User banned!':'User unbanned!');closeM('m-user');loadUsers()}catch(e){fail(e)}}

/* ---- settings: QR + banners ---- */
let qrB64='';
function addBanner(u){const r=document.createElement('div');r.style.cssText='display:flex;gap:6px;align-items:center;';
  r.innerHTML=`<input class="inp banner-inp" style="margin:0;flex:1;" placeholder="https://... banner image URL" value="${esc(u)}"><button class="btn btn-danger" style="padding:10px 12px;" onclick="this.parentElement.remove()"><i class="fa-solid fa-trash"></i></button>`;$('banner-list').appendChild(r)}
async function loadSettings(){qrB64='';try{const s=await api('/api/settings');$('banner-list').innerHTML='';(s.banners.length?s.banners:['']).forEach(addBanner);
  if(s.qr){$('qr-img').src=s.qr;$('qr-img').style.display='block';$('qr-emoji').style.display='none';$('qr-msg').innerHTML='<span style="color:#10b981;">✅ QR set hai — badalna ho to naya upload karo</span>'}}catch(e){fail(e)}}
function pickQr(inp){const f=inp.files&&inp.files[0];if(!f)return;if(!f.type.startsWith('image/'))return toast('Sirf image select karo','err');
  const r=new FileReader();r.onload=e=>{const im=new Image();im.onload=()=>{const max=400;let w=im.width,h=im.height;if(w>h&&w>max){h=h*max/w;w=max}else if(h>max){w=w*max/h;h=max}
    const cv=document.createElement('canvas');cv.width=w;cv.height=h;cv.getContext('2d').drawImage(im,0,0,w,h);qrB64=cv.toDataURL('image/jpeg',0.8);
    $('qr-img').src=qrB64;$('qr-img').style.display='block';$('qr-emoji').style.display='none';$('qr-msg').innerHTML='<span style="color:#10b981;">✅ QR ready! Save karo.</span>'};im.src=e.target.result};r.readAsDataURL(f)}
async function saveSettings(){const banners=[...document.querySelectorAll('.banner-inp')].map(i=>i.value.trim()).filter(Boolean);
  try{await api('/api/settings',{banners,qr:qrB64});qrB64='';toast('Saved!')}catch(e){fail(e)}}
</script></body></html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
