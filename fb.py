"""Firebase Admin init (service key base64 se) + Firestore <-> JSON codec."""
import os, json, base64, math, datetime as dt

# Saare firebase/google imports yahin, module load par (main thread mein).
# Pehle ye function ke andar the -> bot thread + login request ek saath import karte the
# -> "cannot access submodule 'auth' of module 'google' (circular import)" aata tha.
import firebase_admin
from firebase_admin import credentials, firestore, auth
import google.auth  # noqa: F401
import google.oauth2.credentials  # noqa: F401
import googleapiclient.discovery  # noqa: F401
from google.cloud.firestore_v1 import GeoPoint, DocumentReference

_db = None
_project_id = None
_init_error = None


def load_service_account():
    """FIREBASE_CREDENTIALS_B64 (base64 of the service-account JSON). Raw JSON bhi chalega."""
    raw = (os.environ.get("FIREBASE_CREDENTIALS_B64") or "").strip()
    if not raw:
        raise RuntimeError("FIREBASE_CREDENTIALS_B64 env set nahi hai")
    if raw.startswith("{"):
        return json.loads(raw)
    try:
        return json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"FIREBASE_CREDENTIALS_B64 decode nahi hua: {e}")


def init_firebase():
    """Returns (firestore_client). Safe to call many times."""
    global _db, _project_id, _init_error
    if _db is not None:
        return _db
    try:
        info = load_service_account()
        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(info))
        _project_id = info.get("project_id")
        _db = firestore.client()
        _init_error = None
        return _db
    except Exception as e:
        _init_error = str(e)
        raise


def get_db():
    return init_firebase()


def firebase_status():
    return {"ok": _db is not None, "project_id": _project_id, "error": _init_error}


def admin_uid(email):
    """Admin ka Firebase uid (agar account pehle se hai to wahi use hoga)."""
    try:
        return auth.get_user_by_email(email).uid
    except Exception:
        return "ff-admin-panel"


def make_custom_token(email):
    uid = admin_uid(email)
    tok = auth.create_custom_token(uid, {"admin": True, "email": email})
    return tok.decode() if isinstance(tok, bytes) else tok


# ------------------------------------------------------------------ codec
def enc(v):
    """Firestore value -> JSON-safe value (types preserved with __t tags)."""
    if isinstance(v, dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=dt.timezone.utc)
        return {"__t": "ts", "v": v.astimezone(dt.timezone.utc).isoformat()}
    if isinstance(v, GeoPoint):
        return {"__t": "geo", "lat": v.latitude, "lng": v.longitude}
    if isinstance(v, DocumentReference):
        return {"__t": "ref", "path": v.path}
    if isinstance(v, (bytes, bytearray)):
        return {"__t": "bytes", "v": base64.b64encode(bytes(v)).decode()}
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return {"__t": "f", "v": str(v)}
    if isinstance(v, dict):
        return {k: enc(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [enc(x) for x in v]
    return v


def dec(v, db=None):
    """JSON value -> Firestore value."""
    if isinstance(v, dict):
        t = v.get("__t")
        if t == "ts":
            return dt.datetime.fromisoformat(v["v"].replace("Z", "+00:00"))
        if t == "geo":
            return GeoPoint(v["lat"], v["lng"])
        if t == "ref" and db is not None:
            return db.document(v["path"])
        if t == "bytes":
            return base64.b64decode(v["v"])
        if t == "f":
            return float(v["v"])
        return {k: dec(x, db) for k, x in v.items()}
    if isinstance(v, list):
        return [dec(x, db) for x in v]
    return v
