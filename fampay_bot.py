"""Fampay UPI credit watcher — admin panel se control hone wala bot.

Saara Fampay/Gmail saman env se aata hai:
  GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
  POLL_INTERVAL (sec, default 15), GMAIL_QUERY (default: from:no-reply@famapp.in newer_than:30d)
  BOT_AUTOSTART (true/false)
Firestore access: FIREBASE_CREDENTIALS_B64 (service key ka base64) — fb.py se.
"""
import os, re, time, base64, threading, collections
import datetime as dt

import fb

SETTINGS_DOC = ("bot_meta", "settings")
PROCESSED_DOC = ("bot_meta", "processed_ids")
MAX_PROCESSED = 2000
ENV_KEYS = ["GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"]
DEFAULT_QUERY = "from:no-reply@famapp.in newer_than:30d"
TOKEN_URI = "https://oauth2.googleapis.com/token"

UTR_PATTERNS = [
    re.compile(r"UTR[^A-Za-z0-9]{0,5}([A-Za-z0-9]{10,22})", re.I),
    re.compile(r"\bRRN[^A-Za-z0-9]{0,5}([0-9]{10,18})", re.I),
    re.compile(r"\b(?:Transaction|Txn|Ref(?:erence)?)\s*(?:ID|No\.?|#)?[^A-Za-z0-9]{0,5}([A-Za-z0-9]{10,22})", re.I),
]
AMOUNT_PATTERNS = [
    re.compile(r"(?:received|credited|got)[^\d₹Rs.]{0,30}(?:Rs\.?|₹|INR)\s*([0-9]+(?:[.,][0-9]{1,2})?)", re.I),
    re.compile(r"(?:Rs\.?|₹|INR)\s*([0-9]+(?:[.,][0-9]{1,2})?)", re.I),
]


def parse_email(text):
    amount = utr = None
    for pat in AMOUNT_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                amount = float(m.group(1).replace(",", ""))
                break
            except Exception:
                continue
    for pat in UTR_PATTERNS:
        m = pat.search(text)
        if m:
            utr = m.group(1).strip()
            break
    return amount, utr


def extract_text(payload):
    parts = []

    def walk(p):
        data = (p.get("body") or {}).get("data")
        if data and (p.get("mimeType") or "").startswith("text/"):
            try:
                parts.append(base64.urlsafe_b64decode(data + "===").decode("utf-8", "ignore"))
            except Exception:
                pass
        for sub in p.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return "\n".join(parts)


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FampayBot:
    def __init__(self):
        self.lock = threading.RLock()
        self.logs = collections.deque(maxlen=400)
        self.wake = threading.Event()      # poll-now / interval change
        self.stop_flag = threading.Event()
        self.thread = None
        self.gmail = None
        self.processed = []                # ordered, oldest first
        self.processed_set = set()
        self.processed_loaded = False
        self.state = {
            "status": "stopped", "gmail_connected": False, "gmail_account": None,
            "emails_found": 0, "last_run": None, "last_saved": 0, "total_saved": 0,
            "started_at": None, "errors": [],
        }
        self.settings = {
            "poll_interval": max(5, int(os.environ.get("POLL_INTERVAL", "15") or 15)),
            "query": os.environ.get("GMAIL_QUERY") or DEFAULT_QUERY,
            "enabled": (os.environ.get("BOT_AUTOSTART", "true").lower() not in ("0", "false", "no", "off")),
        }

    # ------------------------------------------------------------ utils
    def log(self, msg, level="info"):
        line = {"t": _now_iso(), "l": level, "m": str(msg)[:400]}
        self.logs.append(line)
        print(f"[bot:{level}] {msg}", flush=True)

    def _err(self, msg):
        self.log(msg, "error")
        with self.lock:
            self.state["errors"] = (self.state["errors"] + [f"{_now_iso()} {msg}"[:300]])[-20:]

    @staticmethod
    def env_report():
        rep = {k: bool(os.environ.get(k)) for k in ENV_KEYS}
        rep["FIREBASE_CREDENTIALS_B64"] = bool(os.environ.get("FIREBASE_CREDENTIALS_B64"))
        return rep

    def configured(self):
        return all(os.environ.get(k) for k in ENV_KEYS)

    # ------------------------------------------------------------ settings
    def load_settings(self):
        """Firestore mein saved settings env defaults ko override karti hain."""
        try:
            doc = fb.get_db().collection(SETTINGS_DOC[0]).document(SETTINGS_DOC[1]).get()
            if doc.exists:
                d = doc.to_dict() or {}
                if "poll_interval" in d:
                    self.settings["poll_interval"] = max(5, int(d["poll_interval"]))
                if d.get("query"):
                    self.settings["query"] = str(d["query"])
                if "enabled" in d:
                    self.settings["enabled"] = bool(d["enabled"])
        except Exception as e:
            self.log(f"settings load: {e}", "warn")

    def save_settings(self, **kw):
        with self.lock:
            if "poll_interval" in kw:
                self.settings["poll_interval"] = max(5, min(3600, int(kw["poll_interval"])))
            if kw.get("query"):
                self.settings["query"] = str(kw["query"]).strip()[:300]
            if "enabled" in kw:
                self.settings["enabled"] = bool(kw["enabled"])
            snap = dict(self.settings)
        try:
            fb.get_db().collection(SETTINGS_DOC[0]).document(SETTINGS_DOC[1]).set(snap, merge=True)
        except Exception as e:
            self.log(f"settings save: {e}", "warn")
        self.wake.set()

    # ------------------------------------------------------------ processed ids
    def _load_processed(self, db):
        if self.processed_loaded:
            return
        try:
            doc = db.collection(PROCESSED_DOC[0]).document(PROCESSED_DOC[1]).get()
            ids = (doc.to_dict() or {}).get("ids", []) if doc.exists else []
            self.processed = list(ids)[-MAX_PROCESSED:]
            self.processed_set = set(self.processed)
            self.processed_loaded = True
        except Exception as e:
            self.log(f"load processed: {e}", "warn")

    def _save_processed(self, db):
        try:
            db.collection(PROCESSED_DOC[0]).document(PROCESSED_DOC[1]).set({"ids": self.processed[-MAX_PROCESSED:]})
        except Exception as e:
            self.log(f"save processed: {e}", "warn")

    def _mark(self, mid):
        if mid not in self.processed_set:
            self.processed_set.add(mid)
            self.processed.append(mid)

    def clear_processed(self):
        """Saare mails dobara scan honge (duplicate UTR phir bhi save nahi honge)."""
        db = fb.get_db()
        self.processed, self.processed_set, self.processed_loaded = [], set(), True
        self._save_processed(db)
        self.log("processed ids cleared — next poll sab mails dobara dekhega")
        self.wake.set()

    # ------------------------------------------------------------ gmail
    def _build_gmail(self):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        creds = Credentials(
            token=None,
            refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
            token_uri=TOKEN_URI,
            client_id=os.environ["GMAIL_CLIENT_ID"],
            client_secret=os.environ["GMAIL_CLIENT_SECRET"],
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        )
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        prof = svc.users().getProfile(userId="me").execute()
        self.state["gmail_account"] = prof.get("emailAddress")
        self.log(f"Gmail connected: {prof.get('emailAddress')}")
        return svc

    def process_once(self):
        from googleapiclient.errors import HttpError
        db = fb.get_db()
        self._load_processed(db)
        query = self.settings["query"]
        try:
            res = self.gmail.users().messages().list(userId="me", q=query, maxResults=50).execute()
        except HttpError as e:
            raise RuntimeError(f"Gmail API error: {e}") from e
        msgs = res.get("messages", []) or []
        self.state["emails_found"] = len(msgs)
        new_count, dirty = 0, False
        for m in msgs:
            mid = m["id"]
            if mid in self.processed_set:
                continue
            try:
                full = self.gmail.users().messages().get(userId="me", id=mid, format="full").execute()
            except HttpError as e:
                self.log(f"fetch {mid}: {e}", "warn")
                continue
            snippet = full.get("snippet", "")
            body = extract_text(full.get("payload", {})) + "\n" + snippet
            low = body.lower()
            if "successfully paid" in low:
                self._mark(mid); dirty = True
                continue
            if "successfully received" not in low:
                self._mark(mid); dirty = True
                continue
            amount, utr = parse_email(body)
            if not (amount and utr):
                self.log(f"skip {mid}: amount={amount} utr={utr}", "warn")
                self._mark(mid); dirty = True
                continue
            try:
                ref = db.collection("fampay_deposits").document(utr)
                if ref.get().exists:
                    self.log(f"dup utr={utr}")
                else:
                    ref.set({"utr": utr, "amount": amount, "source": "famapp-gmail",
                             "gmailMessageId": mid, "receivedAt": int(time.time() * 1000),
                             "rawSnippet": snippet[:300]})
                    self.log(f"saved utr={utr} amount=Rs.{amount}", "ok")
                    new_count += 1
                self._mark(mid); dirty = True
            except Exception as e:
                self._err(f"firestore: {e}")
        if dirty:
            self._save_processed(db)
        return new_count

    # ------------------------------------------------------------ loop / control
    def _loop(self):
        self.log("bot thread start")
        try:
            fb.get_db()
        except Exception as e:
            self._err(f"Firebase init fail: {e}")
            with self.lock:
                self.state["status"] = "error"
            return
        self.load_settings()
        while not self.stop_flag.is_set():
            if self.gmail is None:
                try:
                    self.gmail = self._build_gmail()
                    self.state["gmail_connected"] = True
                except Exception as e:
                    self.state["gmail_connected"] = False
                    self.state["status"] = "error"
                    self._err(f"Gmail init failed: {e} — 30s baad retry")
                    if self.stop_flag.wait(30):
                        break
                    continue
            self.state["status"] = "running"
            try:
                n = self.process_once()
                self.state["last_run"] = _now_iso()
                self.state["last_saved"] = n
                self.state["total_saved"] += n
            except Exception as e:
                self._err(f"loop: {e}")
                self.gmail = None  # token/network issue -> reconnect agle round
            self.wake.wait(self.settings["poll_interval"])
            self.wake.clear()
        self.state["status"] = "stopped"
        self.state["gmail_connected"] = False
        self.gmail = None
        self.log("bot thread stopped")

    def start(self, persist=True):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return False, "Bot pehle se chal raha hai"
            if not self.configured():
                self.state["status"] = "not_configured"
                miss = [k for k in ENV_KEYS if not os.environ.get(k)]
                return False, "Env missing: " + ", ".join(miss)
            self.stop_flag.clear()
            self.state.update(status="starting", started_at=_now_iso())
            self.thread = threading.Thread(target=self._loop, name="fampay-bot", daemon=True)
            self.thread.start()
        if persist:
            self.save_settings(enabled=True)
        return True, "Bot start ho gaya"

    def stop(self, persist=True):
        with self.lock:
            alive = self.thread and self.thread.is_alive()
            self.stop_flag.set()
            self.wake.set()
        if persist:
            self.save_settings(enabled=False)
        if alive:
            self.thread.join(timeout=5)
        self.state["status"] = "stopped"
        return True, "Bot band ho gaya"

    def poll_now(self):
        if not (self.thread and self.thread.is_alive()):
            return False, "Bot chal nahi raha — pehle Start karo"
        self.wake.set()
        return True, "Poll trigger ho gaya"

    def autostart(self):
        """Server boot par: env + saved setting dekh kar start."""
        try:
            fb.get_db()
            self.load_settings()
        except Exception as e:
            self._err(f"Firebase init fail: {e}")
            return
        if not self.configured():
            self.state["status"] = "not_configured"
            self.log("Gmail env set nahi — bot start nahi hua", "warn")
            return
        if self.settings["enabled"]:
            self.start(persist=False)

    def status(self):
        with self.lock:
            s = dict(self.state)
            s["errors"] = list(self.state["errors"])[-5:]
            s["settings"] = dict(self.settings)
            s["env"] = self.env_report()
            s["configured"] = self.configured()
            s["alive"] = bool(self.thread and self.thread.is_alive())
            s["processed_cached"] = len(self.processed)
        return s


bot = FampayBot()
