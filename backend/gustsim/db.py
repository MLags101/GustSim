"""SQLite-backed immutable run snapshots and a single-consumer durable queue."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from . import config

def uid():
    return uuid.uuid4().hex

@contextmanager
def connection():
    config.initialize()
    db = sqlite3.connect(config.DATA / "gustsim.sqlite3", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()

def initialize():
    with connection() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS geometries(id TEXT PRIMARY KEY, created REAL NOT NULL, metadata TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS studies(id TEXT PRIMARY KEY, created REAL NOT NULL, name TEXT NOT NULL, spec TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS setups(id TEXT PRIMARY KEY, created REAL NOT NULL, name TEXT NOT NULL, spec TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL,
          status TEXT NOT NULL, stage TEXT NOT NULL, kind TEXT NOT NULL, spec TEXT NOT NULL,
          study_id TEXT REFERENCES studies(id), parent_id TEXT REFERENCES runs(id), cancel INTEGER NOT NULL DEFAULT 0,
          error TEXT, result TEXT NOT NULL DEFAULT '{}');
        CREATE INDEX IF NOT EXISTS idx_runs_status_created ON runs(status, created);
        CREATE INDEX IF NOT EXISTS idx_runs_study_created ON runs(study_id, created);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id), created REAL NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id, id);
        CREATE TABLE IF NOT EXISTS views(id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id), created REAL NOT NULL, spec TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS worker(id INTEGER PRIMARY KEY CHECK(id=1), heartbeat REAL NOT NULL, active_run TEXT);
        """)

def save_geometry(meta):
    with connection() as c:
        c.execute("INSERT INTO geometries VALUES(?,?,?)", (meta["id"], time.time(), json.dumps(meta)))
    return meta

def geometry(identifier):
    with connection() as c:
        row = c.execute("SELECT metadata FROM geometries WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise KeyError("Geometry revision not found")
    return json.loads(row[0])

def geometries():
    with connection() as c:
        return [json.loads(r[0]) for r in c.execute("SELECT metadata FROM geometries ORDER BY created DESC")]

def decode(row):
    if row is None:
        raise KeyError("Run not found")
    obj = dict(row)
    for key in ("spec", "result"):
        obj[key] = json.loads(obj[key])
    return obj

def run(identifier):
    with connection() as c:
        return decode(c.execute("SELECT * FROM runs WHERE id=?", (identifier,)).fetchone())

def runs(compact=False):
    with connection() as c:
        if compact:
            return [decode(r) for r in c.execute("SELECT id,created,updated,status,stage,kind,spec,study_id,parent_id,cancel,error,json_remove(result,'$.history','$.residuals','$.rotor_history','$.components','$.projected_history','$.rotor_load_history') AS result FROM runs ORDER BY created DESC LIMIT 200")]
        return [decode(r) for r in c.execute("SELECT * FROM runs ORDER BY created DESC LIMIT 200")]

def enqueue(spec, kind="solve", study_id=None, parent_id=None, initial_status="queued"):
    identifier, now = uid(), time.time()
    with connection() as c:
        c.execute("INSERT INTO runs(id,created,updated,status,stage,kind,spec,study_id,parent_id) VALUES(?,?,?,?,?,?,?,?,?)",
                  (identifier, now, now, initial_status, initial_status, kind, json.dumps(spec), study_id, parent_id))
    event(identifier, {"message": "Queued", "stage": "queued"})
    return run(identifier)

def update(identifier, **fields):
    allowed = {"status", "stage", "cancel", "error", "result"}
    if not set(fields) <= allowed:
        raise ValueError("Invalid run update")
    fields["updated"] = time.time()
    if "result" in fields:
        fields["result"] = json.dumps(fields["result"])
    with connection() as c:
        c.execute("UPDATE runs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?", (*fields.values(), identifier))

def event(identifier, payload):
    with connection() as c:
        c.execute("INSERT INTO events(run_id,created,payload) VALUES(?,?,?)", (identifier, time.time(), json.dumps(payload)))

def events(identifier, after=0):
    with connection() as c:
        return [{"id": r[0], **json.loads(r[1])} for r in c.execute("SELECT id,payload FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 1000", (identifier, after))]

def claim():
    with connection() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM runs WHERE status='queued' AND cancel=0 ORDER BY created LIMIT 1").fetchone()
        if not row:
            return None
        c.execute("UPDATE runs SET status='running',stage='preparing',updated=? WHERE id=?", (time.time(), row["id"]))
        return decode(row)

def heartbeat(active=None):
    with connection() as c:
        c.execute("INSERT INTO worker VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat,active_run=excluded.active_run", (time.time(), active))

def worker_status():
    with connection() as c:
        row = c.execute("SELECT * FROM worker WHERE id=1").fetchone()
    return {"online": bool(row and time.time() - row["heartbeat"] < 15), "active_run": row["active_run"] if row else None}

def recover():
    # 'generating' is the API's inline case compilation. Nothing else ever resets it, so
    # an API restart mid-compile would otherwise strand the run forever: claim() only
    # looks at 'queued' and cancel() only accepts queued/running.
    stale = ("running", "generating")
    with connection() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM runs WHERE status IN (?,?)", stale)]
        c.execute("UPDATE runs SET status='interrupted',stage='interrupted',error='Worker stopped. Artifacts preserved; retry creates a new attempt.',updated=? WHERE status IN (?,?)", (time.time(), *stale))
    for identifier in ids:
        event(identifier, {"stage": "interrupted", "message": "Worker restarted; this attempt was interrupted"})

def create_study(name, spec):
    identifier = uid()
    with connection() as c:
        c.execute("INSERT INTO studies VALUES(?,?,?,?)", (identifier, time.time(), name, json.dumps(spec)))
    return identifier
