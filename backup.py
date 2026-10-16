"""Backup / restore / delete engine (Firestore Admin SDK) + background jobs + auto-backup scheduler."""
import os, re, io, gzip, json, time, uuid, threading, datetime as dt
import fb

BACKUP_DIR = os.path.abspath(os.environ.get("BACKUP_DIR", os.path.join(os.path.dirname(__file__), "backups")))
META_DOC = ("bot_meta", "backup_settings")
FORMAT = "ffbackup-v2"
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.(ffbak\.gz|json|json\.gz)$")
os.makedirs(BACKUP_DIR, exist_ok=True)

# ------------------------------------------------------------------ jobs
_jobs = {}
_job_lock = threading.Lock()
_heavy = threading.Lock()  # ek time par ek hi backup/restore/delete


def _new_job(kind):
    jid = uuid.uuid4().hex[:10]
    j = {"id": jid, "kind": kind, "status": "running", "progress": "Start ho raha hai…",
         "result": None, "error": None, "started": time.time(), "finished": None}
    with _job_lock:
        _jobs[jid] = j
        for k in sorted(_jobs, key=lambda x: _jobs[x]["started"])[:-20]:
            _jobs.pop(k, None)
    return j


def get_job(jid):
    return _jobs.get(jid)


def run_job(kind, fn, *a, **kw):
    """fn(job, *a, **kw) background thread mein chalta hai. Returns job dict or None if busy."""
    if not _heavy.acquire(blocking=False):
        return None
    j = _new_job(kind)

    def runner():
        try:
            j["result"] = fn(j, *a, **kw)
            j["status"] = "done"
        except Exception as e:
            j["status"] = "error"
            j["error"] = str(e)
        finally:
            j["finished"] = time.time()
            _heavy.release()
    threading.Thread(target=runner, daemon=True, name=f"job-{kind}").start()
    return j


# ------------------------------------------------------------------ helpers
def list_root_collections():
    return sorted(c.id for c in fb.get_db().collections())


def count_docs(col):
    try:
        r = fb.get_db().collection(col).count().get()
        return int(r[0][0].value)
    except Exception:
        return None


def _safe_label(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", s or "")[:30].strip("-")


def _walk(col_ref, include_sub, on_doc):
    """col_ref ke saare docs (aur subcollections) par on_doc(path, data) chalao."""
    n = 0
    for snap in col_ref.stream():
        on_doc(snap.reference.path, snap.to_dict() or {})
        n += 1
        if include_sub:
            for sub in snap.reference.collections():
                n += _walk(sub, True, on_doc)
    return n


# ------------------------------------------------------------------ backup
def do_backup(job, collections=None, include_sub=True, label="manual"):
    db = fb.get_db()
    cols = collections or list_root_collections()
    ts = dt.datetime.now(dt.timezone.utc)
    name = f"ffbackup_{ts.strftime('%Y%m%d_%H%M%S')}_{_safe_label(label) or 'manual'}.ffbak.gz"
    path = os.path.join(BACKUP_DIR, name)
    tmp = path + ".part"
    total = 0
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": {"format": FORMAT, "createdAt": ts.isoformat(),
                                      "project": fb.firebase_status().get("project_id"),
                                      "collections": cols, "subcollections": include_sub}}) + "\n")

        def on_doc(p, data):
            nonlocal total
            f.write(json.dumps({"p": p, "d": fb.enc(data)}, ensure_ascii=False) + "\n")
            total += 1
            if total % 100 == 0:
                job["progress"] = f"{total} docs backup ho gaye…"
        for c in cols:
            job["progress"] = f"{c} ({total} docs so far)"
            _walk(db.collection(c), include_sub, on_doc)
    os.replace(tmp, path)
    prune_backups()
    return {"name": name, "docs": total, "size": os.path.getsize(path), "collections": cols}


def list_backups():
    out = []
    for n in os.listdir(BACKUP_DIR):
        if NAME_RE.match(n):
            p = os.path.join(BACKUP_DIR, n)
            st = os.stat(p)
            out.append({"name": n, "size": st.st_size, "mtime": int(st.st_mtime * 1000)})
    return sorted(out, key=lambda x: -x["mtime"])


def backup_path(name):
    if not NAME_RE.match(name or ""):
        return None
    p = os.path.join(BACKUP_DIR, name)
    return p if os.path.isfile(p) else None


def delete_backup(name):
    p = backup_path(name)
    if not p:
        return False
    os.remove(p)
    return True


def prune_backups():
    keep = get_settings().get("keep", 10)
    auto = [b for b in list_backups() if "_auto" in b["name"]]
    for b in auto[keep:]:
        try:
            os.remove(os.path.join(BACKUP_DIR, b["name"]))
        except OSError:
            pass


# ------------------------------------------------------------------ restore
def _iter_records(path):
    """Yield (doc_path, data_json) — v2 ndjson.gz, ya purana v1 JSON dono chalte hain."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        head = f.readline()
        if not head.strip():
            return
        try:
            first = json.loads(head)
        except json.JSONDecodeError:
            first = None
        if isinstance(first, dict) and "_meta" in first:           # v2
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    yield r["p"], r["d"]
            return
        # v1 / single JSON object -> poori file padho
        f.seek(0)
        data = json.load(f)
    if isinstance(data, dict) and "collections" in data:
        for c, docs in data["collections"].items():
            for it in docs:
                yield f"{c}/{it['id']}", it["data"]
    elif isinstance(data, dict) and "collection" in data and "docs" in data:
        for it in data["docs"]:
            yield f"{data['collection']}/{it['id']}", it["data"]
    else:
        raise ValueError("Backup file ka format samajh nahi aaya")


def do_restore(job, path, only=None):
    db = fb.get_db()
    only = set(only or [])
    batch, n, skipped = db.batch(), 0, 0
    pending = 0
    for p, d in _iter_records(path):
        if only and p.split("/")[0] not in only:
            skipped += 1
            continue
        batch.set(db.document(p), fb.dec(d, db))
        pending += 1
        n += 1
        if pending >= 400:
            batch.commit()
            batch, pending = db.batch(), 0
            job["progress"] = f"{n} docs restore ho gaye…"
    if pending:
        batch.commit()
    return {"restored": n, "skipped": skipped}


# ------------------------------------------------------------------ delete
def do_delete_collection(job, col, backup_first=True):
    db = fb.get_db()
    res = {"collection": col}
    if backup_first:
        job["progress"] = f"Delete se pehle {col} ka backup…"
        b = do_backup(job, [col], True, f"pre-delete-{col}")
        res["backup"] = b["name"]
    job["progress"] = f"{col} delete ho raha hai…"
    deleted = db.recursive_delete(db.collection(col))
    res["deleted"] = deleted
    return res


def do_delete_doc(job, path, recursive=True, backup_first=True):
    db = fb.get_db()
    ref = db.document(path)
    res = {"path": path}
    if backup_first:
        # chhota backup: sirf yeh doc (+subs)
        job["progress"] = "Doc ka backup…"
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
        name = f"ffbackup_{ts}_pre-delete-doc.ffbak.gz"
        p = os.path.join(BACKUP_DIR, name)
        with gzip.open(p, "wt", encoding="utf-8") as f:
            f.write(json.dumps({"_meta": {"format": FORMAT, "createdAt": ts, "collections": [path.split('/')[0]]}}) + "\n")
            snap = ref.get()
            if snap.exists:
                f.write(json.dumps({"p": ref.path, "d": fb.enc(snap.to_dict() or {})}) + "\n")
            if recursive:
                for sub in ref.collections():
                    _walk(sub, True, lambda pp, dd: f.write(json.dumps({"p": pp, "d": fb.enc(dd)}) + "\n"))
        res["backup"] = name
    if recursive:
        res["deleted"] = db.recursive_delete(ref)
    else:
        ref.delete()
        res["deleted"] = 1
    return res


def do_delete_older(job, col, field, days, kind="ms", backup_first=True):
    """col mein jin docs ka `field` `days` se purana hai unhe delete karo (subcollections ke saath)."""
    db = fb.get_db()
    cutoff = time.time() - days * 86400
    cutoff_val = int(cutoff * 1000) if kind == "ms" else dt.datetime.fromtimestamp(cutoff, dt.timezone.utc)
    q = db.collection(col).where(field, "<", cutoff_val)
    res = {"collection": col, "field": field, "days": days}
    snaps = list(q.stream())
    res["matched"] = len(snaps)
    if not snaps:
        return res
    if backup_first:
        job["progress"] = f"{len(snaps)} docs ka backup…"
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
        name = f"ffbackup_{ts}_pre-clean-{_safe_label(col)}.ffbak.gz"
        with gzip.open(os.path.join(BACKUP_DIR, name), "wt", encoding="utf-8") as f:
            f.write(json.dumps({"_meta": {"format": FORMAT, "createdAt": ts, "collections": [col]}}) + "\n")
            for s in snaps:
                f.write(json.dumps({"p": s.reference.path, "d": fb.enc(s.to_dict() or {})}) + "\n")
        res["backup"] = name
    n = 0
    for s in snaps:
        db.recursive_delete(s.reference)
        n += 1
        if n % 20 == 0:
            job["progress"] = f"{n}/{len(snaps)} delete ho gaye…"
    res["deleted"] = n
    return res


# ------------------------------------------------------------------ settings + scheduler
_settings = {"interval_hours": int(os.environ.get("BACKUP_INTERVAL_HOURS", "24") or 0),
             "keep": int(os.environ.get("BACKUP_KEEP", "10") or 10), "last_auto": None}


def get_settings():
    return dict(_settings)


def load_settings():
    try:
        d = fb.get_db().collection(*META_DOC[:1]).document(META_DOC[1]).get()
        if d.exists:
            x = d.to_dict() or {}
            for k in ("interval_hours", "keep", "last_auto"):
                if k in x:
                    _settings[k] = x[k]
    except Exception as e:
        print("[backup] settings load:", e, flush=True)


def save_settings(interval_hours=None, keep=None):
    if interval_hours is not None:
        _settings["interval_hours"] = max(0, min(24 * 30, int(interval_hours)))
    if keep is not None:
        _settings["keep"] = max(1, min(100, int(keep)))
    try:
        fb.get_db().collection(META_DOC[0]).document(META_DOC[1]).set(_settings, merge=True)
    except Exception as e:
        print("[backup] settings save:", e, flush=True)


def scheduler_loop():
    time.sleep(20)
    try:
        load_settings()
    except Exception:
        pass
    while True:
        try:
            hrs = _settings.get("interval_hours", 0)
            last = _settings.get("last_auto") or 0
            if hrs and time.time() - last >= hrs * 3600:
                j = run_job("auto-backup", do_backup, None, True, "auto")
                if j:
                    while j["status"] == "running":
                        time.sleep(2)
                    if j["status"] == "done":
                        _settings["last_auto"] = int(time.time())
                        save_settings()
        except Exception as e:
            print("[backup] scheduler:", e, flush=True)
        time.sleep(60)


def start_scheduler():
    threading.Thread(target=scheduler_loop, daemon=True, name="backup-scheduler").start()
