"""FF Admin server — admin panel + login (env) + backup/delete API + Fampay bot control."""
import os, sys, time, shutil, platform, threading
from flask import Flask, jsonify, request, send_from_directory, send_file, g, Response
from werkzeug.middleware.proxy_fix import ProxyFix

import fb, security, backup, keepalive
from fampay_bot import bot
import admin_lite as lite  # LITE panel (users/wallet/matches/settings) isi service mein merge

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
BOOT = time.time()

app = Flask(__name__, static_folder=None)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024

ok = lambda **k: jsonify(ok=True, **k)
bad = lambda msg, code=400: (jsonify(ok=False, error=msg), code)


@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Referrer-Policy"] = "same-origin"
    if request.path.startswith("/api/") or request.path.endswith((".html", ".js", ".css")) or request.path == "/":
        r.headers["Cache-Control"] = "no-store"
    return r


# ------------------------------------------------------------------ static
@app.route("/")
@app.route("/admin")
def index():
    # Owner session -> main panel (admin.html). Admin session -> lite panel. Koi session nahi -> admin.html
    # khud apna login screen dikhata hai (ek hi form, email+password+PIN /api/login par jaata hai).
    if lite.read_session():
        return Response(lite.HTML, mimetype="text/html")
    return send_from_directory(PUBLIC, "admin.html")


@app.route("/<path:p>")
def static_files(p):
    if p.startswith("api/"):
        return bad("Not found", 404)
    return send_from_directory(PUBLIC, p)


@app.route("/health")
def health():
    # Render health check + UptimeRobot yahi URL ping karte hain (koi secret nahi dikhta)
    return jsonify(ok=True, uptime=int(time.time() - BOOT), bot=bot.state["status"])


# ------------------------------------------------------------------ auth
# Ek hi login form (email + password + PIN): OWNER_* creds match -> main panel, ADMIN_* creds
# match -> lite panel, koi bhi match nahi -> 1 hi shared counter mein fail ginta hai (5 galat
# attempt -> IP 1 hour lock, disk par save — security.py dekho).
@app.post("/api/login")
def login():
    if not security.creds_ready():
        return bad("Server par OWNER_EMAIL/PASSWORD/PIN ya ADMIN_EMAIL/PASSWORD/PIN env set nahi hain (Render → Environment).", 503)
    ip = security.client_ip()
    wait = security.locked_for(ip)
    if wait:
        return bad(f"Bahut zyada galat attempts. {wait // 60 + 1} minute baad try karo.", 429)
    d = request.get_json(silent=True) or {}
    role = security.check_login_combined(d.get("email"), d.get("password"), d.get("pin"))
    if not role:
        security.register_fail(ip)
        time.sleep(0.8)
        return bad("Email, password ya PIN galat hai", 401)
    security.register_ok(ip)
    resp = jsonify(ok=True, role=role)
    if role == "owner":
        resp.set_cookie(security.SESSION_COOKIE, security.make_session(security.creds()[0]),
                        max_age=int(security.SESSION_HOURS * 3600), httponly=True,
                        samesite="Strict", secure=request.is_secure)
        resp.delete_cookie(lite.COOKIE)
    else:
        resp.set_cookie(lite.COOKIE, lite.make_session(),
                        max_age=lite.SESSION_H * 3600, httponly=True,
                        samesite="Strict", secure=request.is_secure)
        resp.delete_cookie(security.SESSION_COOKIE)
    return resp


@app.get("/api/session")
def session():
    u = security.current_user()
    if u:
        return jsonify(ok=True, ready=security.creds_ready(), role="owner", email=u.get("u"))
    if lite.read_session():
        return jsonify(ok=True, ready=security.creds_ready(), role="admin", email=lite.EMAIL)
    return jsonify(ok=False, ready=security.creds_ready())


@app.post("/api/logout")
def logout():
    resp = jsonify(ok=True)
    resp.delete_cookie(security.SESSION_COOKIE)
    resp.delete_cookie(lite.COOKIE)
    return resp


@app.get("/api/firebase-token")
@security.login_required
def firebase_token():
    """Panel ke Firestore client ko admin claim wala token — service key server par hi rehti hai."""
    try:
        return ok(token=fb.make_custom_token(security.creds()[0]))
    except Exception as e:
        return bad(f"Firebase token nahi bana: {e}", 500)


# ------------------------------------------------------------------ server info
@app.get("/api/status")
@security.login_required
def status():
    try:
        total, used, free = shutil.disk_usage(backup.BACKUP_DIR)
    except Exception:
        total = used = free = 0
    st = fb.firebase_status()
    if not st["ok"]:
        try:
            fb.get_db()
        except Exception:
            pass
        st = fb.firebase_status()
    return ok(
        uptime=int(time.time() - BOOT), python=platform.python_version(), firebase=st,
        env={
            "OWNER_EMAIL": bool(os.environ.get("OWNER_EMAIL")), "OWNER_PASSWORD": bool(os.environ.get("OWNER_PASSWORD")),
            "OWNER_PIN": bool(os.environ.get("OWNER_PIN")),
            "ADMIN_EMAIL": bool(os.environ.get("ADMIN_EMAIL")), "ADMIN_PASSWORD": bool(os.environ.get("ADMIN_PASSWORD")),
            "ADMIN_PIN": bool(os.environ.get("ADMIN_PIN")), "SESSION_SECRET": security.secret_from_env(),
            **bot.env_report(),
        },
        backup_dir=backup.BACKUP_DIR, disk_free=free, session_hours=security.SESSION_HOURS,
        user=g.user.get("u"), keepalive=keepalive.state,
    )


# ------------------------------------------------------------------ jobs
@app.get("/api/jobs/<jid>")
@security.login_required
def job(jid):
    j = backup.get_job(jid)
    return ok(job=j) if j else bad("Job nahi mila", 404)


def _start(kind, fn, *a, **kw):
    j = backup.run_job(kind, fn, *a, **kw)
    if not j:
        return bad("Doosra backup/restore/delete abhi chal raha hai, thoda ruko.", 409)
    return ok(job=j["id"])


# ------------------------------------------------------------------ backups
@app.get("/api/collections")
@security.login_required
def collections():
    try:
        cols = backup.list_root_collections()
    except Exception as e:
        return bad(f"Firestore error: {e}", 500)
    return ok(collections=[{"name": c, "count": backup.count_docs(c)} for c in cols])


@app.get("/api/backups")
@security.login_required
def backups():
    return ok(backups=backup.list_backups(), settings=backup.get_settings(), dir=backup.BACKUP_DIR)


@app.post("/api/backups")
@security.login_required
def backup_create():
    d = request.get_json(silent=True) or {}
    cols = [c for c in (d.get("collections") or []) if isinstance(c, str) and "/" not in c] or None
    return _start("backup", backup.do_backup, cols, bool(d.get("subcollections", True)), "manual")


@app.get("/api/backups/<name>/download")
@security.login_required
def backup_download(name):
    p = backup.backup_path(name)
    return send_file(p, as_attachment=True, download_name=name) if p else bad("File nahi mili", 404)


@app.post("/api/backups/<name>/delete")
@security.pin_required
def backup_delete(name):
    return ok() if backup.delete_backup(name) else bad("File nahi mili", 404)


@app.post("/api/backups/<name>/restore")
@security.pin_required
def backup_restore(name):
    p = backup.backup_path(name)
    if not p:
        return bad("File nahi mili", 404)
    only = (request.get_json(silent=True) or {}).get("collections")
    return _start("restore", backup.do_restore, p, only)


@app.post("/api/restore-upload")
@security.pin_required
def restore_upload():
    f = request.files.get("file")
    if not f:
        return bad("File select karo")
    fn = f.filename or ""
    ext = ".ffbak.gz" if fn.endswith(".ffbak.gz") else (".json.gz" if fn.endswith(".json.gz") else ".json")
    tmp = os.path.join(backup.BACKUP_DIR, f"upload_{int(time.time())}{ext}")
    f.save(tmp)

    def run(job, path):
        try:
            return backup.do_restore(job, path)
        finally:
            try: os.remove(path)
            except OSError: pass
    return _start("restore", run, tmp)


@app.post("/api/backup-settings")
@security.login_required
def backup_settings():
    d = request.get_json(silent=True) or {}
    backup.save_settings(d.get("interval_hours"), d.get("keep"))
    return ok(settings=backup.get_settings())


# ------------------------------------------------------------------ data delete
@app.post("/api/data/delete-collection")
@security.pin_required
def del_collection():
    d = request.get_json(silent=True) or {}
    col = (d.get("collection") or "").strip()
    if not col or "/" in col:
        return bad("Collection ka naam galat hai")
    if d.get("confirm") != col:
        return bad("Confirm text collection ke naam se match nahi hua")
    return _start("delete", backup.do_delete_collection, col, bool(d.get("backup", True)))


@app.post("/api/data/delete-doc")
@security.pin_required
def del_doc():
    d = request.get_json(silent=True) or {}
    path = (d.get("path") or "").strip().strip("/")
    if not path or len(path.split("/")) % 2 != 0:
        return bad("Doc path galat (format: collection/docId)")
    return _start("delete", backup.do_delete_doc, path, bool(d.get("recursive", True)), bool(d.get("backup", True)))


@app.post("/api/data/delete-older")
@security.pin_required
def del_older():
    d = request.get_json(silent=True) or {}
    col, field = (d.get("collection") or "").strip(), (d.get("field") or "").strip()
    try:
        days = float(d.get("days"))
    except (TypeError, ValueError):
        return bad("Days number hona chahiye")
    if not col or "/" in col or not field or days < 0:
        return bad("Collection / field / days check karo")
    return _start("delete", backup.do_delete_older, col, field, days,
                  "ts" if d.get("type") == "ts" else "ms", bool(d.get("backup", True)))


# ------------------------------------------------------------------ fampay bot
@app.get("/api/bot/status")
@security.login_required
def bot_status():
    return ok(bot=bot.status(), logs=list(bot.logs)[-120:])


@app.post("/api/bot/<action>")
@security.login_required
def bot_action(action):
    if action == "start":
        good, msg = bot.start()
    elif action == "stop":
        good, msg = bot.stop()
    elif action == "poll":
        good, msg = bot.poll_now()
    else:
        return bad("Unknown action", 404)
    return (ok(message=msg) if good else bad(msg, 400))


@app.post("/api/bot/settings")
@security.login_required
def bot_settings():
    d = request.get_json(silent=True) or {}
    try:
        bot.save_settings(poll_interval=int(d.get("poll_interval", bot.settings["poll_interval"])), query=d.get("query"))
    except (TypeError, ValueError):
        return bad("Poll interval number hona chahiye")
    return ok(settings=bot.settings)


@app.post("/api/bot/clear-processed")
@security.pin_required
def bot_clear():
    bot.clear_processed()
    return ok()


@app.get("/api/bot/deposits")
@security.login_required
def bot_deposits():
    try:
        snaps = fb.get_db().collection("fampay_deposits").order_by("receivedAt", direction="DESCENDING").limit(30).stream()
        return ok(deposits=[{"id": s.id, **{k: v for k, v in (s.to_dict() or {}).items() if k in ("utr", "amount", "receivedAt", "source")}} for s in snaps])
    except Exception as e:
        return bad(f"Firestore error: {e}", 500)


# ------------------------------------------------------------------ lite panel routes (merged)
# admin_lite.py ke functions pehle se @login_required (lite ka apna session/cookie check) se decorated
# hain, isliye yahan unhi ko seedha register kar rahe hain — ek hi process, ek hi Firestore connection.
@app.errorhandler(lite.ApiError)
def _lite_api_err(e):
    return jsonify(ok=False, error=e.msg), e.code


@app.errorhandler(500)
def _json_500(e):
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error="Server error"), 500
    return e


_LITE_ROUTES = [
    ("/api/users", ["GET"], lite.users),
    ("/api/users/<uid>", ["GET"], lite.user_detail),
    ("/api/users/<uid>/balance", ["POST"], lite.user_balance),
    ("/api/users/<uid>/ban", ["POST"], lite.user_ban),
    ("/api/users/<uid>/unban", ["POST"], lite.user_unban),
    ("/api/wallet", ["GET"], lite.wallet),
    ("/api/wallet/process", ["POST"], lite.wallet_process),
    ("/api/matches", ["GET"], lite.matches),
    ("/api/matches", ["POST"], lite.match_create),
    ("/api/matches/<mid>", ["GET"], lite.match_get),
    ("/api/matches/<mid>/delete", ["POST"], lite.match_delete),
    ("/api/matches/<mid>/room", ["POST"], lite.match_room),
    ("/api/matches/<mid>/complete", ["POST"], lite.match_complete),
    ("/api/matches/<mid>/pay", ["POST"], lite.match_pay),
    ("/api/settings", ["GET"], lite.settings_get),
    ("/api/settings", ["POST"], lite.settings_save),
]
for _rule, _methods, _fn in _LITE_ROUTES:
    app.add_url_rule(_rule, endpoint=f"lite_{_fn.__name__}_{_methods[0]}", view_func=_fn, methods=_methods)


# ------------------------------------------------------------------ boot
_booted = False
_boot_lock = threading.Lock()


def boot():
    global _booted
    with _boot_lock:
        if _booted:
            return
        _booted = True
    if not security.creds_ready():
        print("[warn] OWNER_EMAIL/PASSWORD/PIN aur ADMIN_EMAIL/PASSWORD/PIN dono missing — login disabled", flush=True)
    if not security.secret_from_env():
        print("[warn] SESSION_SECRET set nahi — restart par sab admin logout ho jayenge", flush=True)
    threading.Thread(target=bot.autostart, daemon=True, name="bot-boot").start()
    backup.start_scheduler()
    keepalive.start()


boot()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
