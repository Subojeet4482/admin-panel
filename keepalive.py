"""Auto-alive: server khud apne public URL ko ping karta hai (Render free sleep se bachne ke liye)
+ watchdog: bot thread mar jaye (aur admin ne Stop nahi kiya) to apne aap dobara start."""
import os, time, threading, urllib.request
from fampay_bot import bot

INTERVAL = max(60, int(os.environ.get("KEEPALIVE_INTERVAL", "600") or 600))   # default 10 min
ENABLED = os.environ.get("KEEPALIVE", "true").lower() not in ("0", "false", "no", "off")
BASE = (os.environ.get("KEEPALIVE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")

state = {"enabled": ENABLED and bool(BASE), "url": BASE, "interval": INTERVAL, "last_ping": None,
         "last_ok": None, "last_error": None, "pings": 0, "fails": 0, "watchdog_restarts": 0}


def _ping():
    state["pings"] += 1
    state["last_ping"] = int(time.time())
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + "/health", headers={"User-Agent": "ff-keepalive"}), timeout=20) as r:
            if r.status == 200:
                state["last_ok"] = int(time.time()); state["last_error"] = None
                return
            raise RuntimeError(f"HTTP {r.status}")
    except Exception as e:
        state["fails"] += 1; state["last_error"] = str(e)[:200]


def _ping_loop():
    time.sleep(30)
    while True:
        _ping()
        time.sleep(INTERVAL)


def _watchdog_loop():
    time.sleep(90)
    while True:
        try:
            alive = bot.thread and bot.thread.is_alive()
            if not alive and bot.settings.get("enabled") and bot.configured() and bot.state["status"] != "starting":
                ok, msg = bot.start(persist=False)
                if ok:
                    state["watchdog_restarts"] += 1
                    bot.log("watchdog: bot band mila, dobara start kiya", "warn")
        except Exception as e:
            print("[watchdog]", e, flush=True)
        time.sleep(60)


def start():
    threading.Thread(target=_watchdog_loop, daemon=True, name="watchdog").start()
    if state["enabled"]:
        threading.Thread(target=_ping_loop, daemon=True, name="keepalive").start()
        print(f"[keepalive] har {INTERVAL}s par {BASE}/health ping hoga", flush=True)
    else:
        print("[keepalive] off — RENDER_EXTERNAL_URL / KEEPALIVE_URL nahi mila ya KEEPALIVE=false", flush=True)
