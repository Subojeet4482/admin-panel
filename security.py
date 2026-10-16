"""Admin login: email + password + PIN (Render env se). Signed cookie session, rate-limit."""
import os, time, hmac, hashlib, json, base64, secrets, threading
from functools import wraps
from flask import request, jsonify, g

SESSION_COOKIE = "ffa_session"
SESSION_HOURS = float(os.environ.get("SESSION_HOURS", "12"))
MAX_FAILS = int(os.environ.get("LOGIN_MAX_FAILS", "5"))
LOCK_SECONDS = int(os.environ.get("LOGIN_LOCK_MINUTES", "60")) * 60

_SECRET_FROM_ENV = bool(os.environ.get("SESSION_SECRET"))
_SECRET = (os.environ.get("SESSION_SECRET") or secrets.token_hex(32)).encode()
_lock = threading.Lock()
_fails = {}          # ip -> [count, locked_until]
_global_fails = []   # timestamps

# ---------------------------------------------------------------- disk persistence (Firebase ko touch nahi karta)
# Render free plan ka disk restart/redeploy par saaf ho jaata hai (jaise backups/ README mein likha hai) —
# isliye yeh sirf "process crash ya reload" jaisi cheezon ke beech lock yaad rakhta hai. Redeploy ke paar
# sach mein persist karna ho to LOGIN_LOCK_DIR ko Render ke paid persistent Disk mount par point karo.
_STATE_DIR = os.environ.get("LOGIN_LOCK_DIR") or os.environ.get("BACKUP_DIR") or os.path.join(os.path.dirname(__file__), "backups")
try:
    os.makedirs(_STATE_DIR, exist_ok=True)
except Exception:
    pass
_STATE_FILE = os.path.join(_STATE_DIR, "login_locks.json")


def _load_state():
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _fails.update({str(k): v for k, v in (data.get("fails") or {}).items()})
        _global_fails.extend(data.get("global") or [])
    except Exception:
        pass


def _save_state():
    try:
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"fails": _fails, "global": _global_fails}, f)
        os.replace(tmp, _STATE_FILE)
    except Exception:
        pass


_load_state()


def creds():
    """Owner creds — in-logon se MAIN panel khulta hai."""
    return (
        (os.environ.get("OWNER_EMAIL") or "").strip().lower(),
        os.environ.get("OWNER_PASSWORD") or "",
        os.environ.get("OWNER_PIN") or "",
    )


def admin_creds():
    """Admin creds — in-logon se LITE panel khulta hai (ff-admin-lite wala)."""
    return (
        (os.environ.get("ADMIN_EMAIL") or "").strip().lower(),
        os.environ.get("ADMIN_PASSWORD") or "",
        os.environ.get("ADMIN_PIN") or "",
    )


def creds_ready():
    e, p, n = creds()
    ae, ap, an = admin_creds()
    return bool((e and p and n) or (ae and ap and an))


def secret_from_env():
    return _SECRET_FROM_ENV


def _eq(a, b):
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _fingerprint():
    e, p, n = creds()
    return hashlib.sha256(_SECRET + e.encode() + p.encode() + n.encode()).hexdigest()[:16]


def client_ip():
    return request.remote_addr or "?"


def _sign(payload: bytes) -> str:
    return hmac.new(_SECRET, payload, hashlib.sha256).hexdigest()


def make_session(email):
    body = json.dumps({"u": email, "exp": int(time.time() + SESSION_HOURS * 3600),
                       "fp": _fingerprint(), "n": secrets.token_hex(6)}).encode()
    b = base64.urlsafe_b64encode(body).decode().rstrip("=")
    return b + "." + _sign(b.encode())


def read_session(token):
    try:
        b, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(b.encode())):
            return None
        data = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
        if data.get("exp", 0) < time.time():
            return None
        if not hmac.compare_digest(data.get("fp", ""), _fingerprint()):
            return None  # password / pin env badle -> purane session band
        return data
    except Exception:
        return None


def current_user():
    tok = request.cookies.get(SESSION_COOKIE)
    return read_session(tok) if tok else None


# ---------------------------------------------------------------- rate limit
def locked_for(ip):
    with _lock:
        rec = _fails.get(ip)
        now = time.time()
        if rec and rec[1] > now:
            return int(rec[1] - now)
        # global brake: 30 galat attempts / 15 min across all IPs
        recent = [t for t in _global_fails if t > now - LOCK_SECONDS]
        _global_fails[:] = recent
        if len(recent) >= MAX_FAILS * 6:
            return int(recent[0] + LOCK_SECONDS - now)
    return 0


def register_fail(ip):
    with _lock:
        now = time.time()
        rec = _fails.get(ip, [0, 0])
        if rec[1] and rec[1] <= now:
            rec = [0, 0]
        rec[0] += 1
        if rec[0] >= MAX_FAILS:
            rec[1] = now + LOCK_SECONDS
            rec[0] = 0
        _fails[ip] = rec
        _global_fails.append(now)
        _save_state()


def register_ok(ip):
    with _lock:
        if _fails.pop(ip, None) is not None:
            _save_state()


def check_login(email, password, pin):
    """Constant-time check of all three (no early exit)."""
    e, p, n = creds()
    a = _eq((email or "").strip().lower(), e)
    b = _eq(password or "", p)
    c = _eq(str(pin or ""), n)
    return a & b & c


def check_login_combined(email, password, pin):
    """Ek hi login form — owner creds match ho to 'owner', admin creds match ho to 'admin', warna None.
    Dono check har baar chalte hain (no early exit) taaki timing se pata na chale kaunsa set try kiya."""
    email_n, pw, pn = (email or "").strip().lower(), password or "", str(pin or "")
    oe, op, on = creds()
    ae, ap, an = admin_creds()
    owner_ok = bool(oe and op and on) & _eq(email_n, oe) & _eq(pw, op) & _eq(pn, on)
    admin_ok = bool(ae and ap and an) & _eq(email_n, ae) & _eq(pw, ap) & _eq(pn, an)
    if owner_ok:
        return "owner"
    if admin_ok:
        return "admin"
    return None


def check_pin(pin):
    ok = _eq(str(pin or ""), creds()[2])
    ip = client_ip()
    if ok:
        return True
    register_fail(ip)
    return False


# ---------------------------------------------------------------- decorators
def login_required(fn):
    @wraps(fn)
    def w(*a, **k):
        u = current_user()
        if not u:
            return jsonify(ok=False, error="Login required", code="auth"), 401
        # CSRF: state-changing requests must be JSON / custom header (cross-site forms can't)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("X-Requested-With") != "ffadmin":
                return jsonify(ok=False, error="Bad request origin"), 403
        g.user = u
        return fn(*a, **k)
    return w


def pin_required(fn):
    """Destructive routes: body mein 'pin' dobara dena padega."""
    @wraps(fn)
    @login_required
    def w(*a, **k):
        ip = client_ip()
        wait = locked_for(ip)
        if wait:
            return jsonify(ok=False, error=f"Bahut galat PIN. {wait//60+1} min baad try karo."), 429
        body = request.get_json(silent=True) or {}
        pin = body.get("pin") if body else request.form.get("pin")
        if request.files and not pin:
            pin = request.form.get("pin")
        if not check_pin(pin):
            return jsonify(ok=False, error="PIN galat hai", code="pin"), 403
        return fn(*a, **k)
    return w
