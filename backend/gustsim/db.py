"""SQLite-backed immutable run snapshots and a durable, lane-partitioned job queue."""
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from . import config

# compute: OpenFOAM in the worker image. post: pvbatch/report/ffmpeg in the worker image.
# cad: OCP import and preparation in the API image. Each lane is its own process.
LANES = ('compute', 'post', 'cad')
KIND_LANES = {'view': 'post', 'package': 'post', 'report': 'post', 'insights': 'post', 'compare_surface': 'post',
              'animate': 'post', 'amr_indicator': 'post', 'import': 'cad', 'prepare': 'cad'}
SIMULATION_KINDS = ('mesh', 'solve', 'case', 'adjoint', 'optimise', 'transient')
TERMINAL = ('completed', 'failed', 'cancelled', 'interrupted')
ONLINE_SECONDS = 15


def uid():
    return uuid.uuid4().hex


def lane_for(kind):
    return KIND_LANES.get(kind, 'compute')


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


def initialize(migrate=True):
    """Create or upgrade the schema. Only the API (and tests/scripts) migrate."""
    from . import migrations
    if migrate:
        return migrations.migrate()
    return migrations.require_schema()


def require_schema(timeout=120):
    from . import migrations
    return migrations.require_schema(timeout)


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


COMPACT = ("json_remove(result,'$.history','$.residuals','$.rotor_history','$.components','$.projected_history',"
           "'$.rotor_load_history') AS result")


def runs(compact=False, kinds=None, simulation_id=None, limit=200, parent_id=None):
    columns = 'id,created,updated,status,stage,kind,spec,study_id,parent_id,cancel,error,lane,simulation_id,attempt,retry_of,retry_reason,started,finished,configuration_id,' + COMPACT if compact else '*'
    where, params = [], []
    if kinds:
        where.append('kind IN (' + ','.join('?' * len(kinds)) + ')'); params += list(kinds)
    if simulation_id:
        where.append('simulation_id=?'); params.append(simulation_id)
    if parent_id:
        where.append('parent_id=?'); params.append(parent_id)
    query = f"SELECT {columns} FROM runs" + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY created DESC LIMIT ?'
    with connection() as c:
        return [decode(r) for r in c.execute(query, (*params, limit))]


def enqueue(spec, kind="solve", study_id=None, parent_id=None, initial_status="queued", lane=None, simulation_id=None,
            attempt=1, retry_of=None, retry_reason=None, configuration_id=None, identifier=None):
    identifier, now = identifier or uid(), time.time()
    if configuration_id is None and kind in ('mesh', 'solve', 'case'):
        try:
            from .workflow import identity
            configuration_id = identity(spec)
        except Exception:
            configuration_id = None
    with connection() as c:
        c.execute("INSERT INTO runs(id,created,updated,status,stage,kind,spec,study_id,parent_id,lane,simulation_id,attempt,retry_of,retry_reason,configuration_id)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (identifier, now, now, initial_status, initial_status, kind, json.dumps(spec), study_id, parent_id,
                   lane or lane_for(kind), simulation_id, attempt, retry_of, retry_reason, configuration_id))
    event(identifier, {"message": "Queued", "stage": "queued"})
    return run(identifier)


def update(identifier, **fields):
    allowed = {"status", "stage", "cancel", "error", "result", "started", "finished"}
    if not set(fields) <= allowed:
        raise ValueError("Invalid run update")
    fields["updated"] = time.time()
    if fields.get("status") in TERMINAL and "finished" not in fields:
        fields["finished"] = fields["updated"]
    if "result" in fields:
        fields["result"] = json.dumps(fields["result"])
    with connection() as c:
        c.execute("UPDATE runs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?", (*fields.values(), identifier))


def merge_result(identifier, patch):
    """Read-modify-write a run's result atomically (package/post jobs add to a finished solve)."""
    with connection() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT result FROM runs WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise KeyError('Run not found')
        result = {**json.loads(row[0]), **patch}
        c.execute('UPDATE runs SET result=?,updated=? WHERE id=?', (json.dumps(result), time.time(), identifier))
    return result


def event(identifier, payload):
    with connection() as c:
        c.execute("INSERT INTO events(run_id,created,payload) VALUES(?,?,?)", (identifier, time.time(), json.dumps(payload)))


def events(identifier, after=0):
    with connection() as c:
        return [{"id": r[0], **json.loads(r[1])} for r in c.execute("SELECT id,payload FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 1000", (identifier, after))]


def claim(lanes=None):
    """Atomically take the oldest queued job, optionally restricted to some lanes."""
    lanes = tuple(lanes) if lanes else None
    with connection() as c:
        c.execute("BEGIN IMMEDIATE")
        query = "SELECT * FROM runs WHERE status='queued' AND cancel=0"
        if lanes:
            query += " AND lane IN (" + ",".join("?" * len(lanes)) + ")"
        row = c.execute(query + " ORDER BY created LIMIT 1", lanes or ()).fetchone()
        if not row:
            return None
        now = time.time()
        c.execute("UPDATE runs SET status='running',stage='preparing',updated=?,started=? WHERE id=?", (now, now, row["id"]))
        return decode(row)


def heartbeat(active=None, lane='compute', capabilities=None):
    now = time.time()
    with connection() as c:
        if lane == 'compute':
            # The single-row table stays for older healthchecks and tools.
            c.execute("INSERT INTO worker VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat,active_run=excluded.active_run", (now, active))
        if capabilities is None:
            c.execute("INSERT INTO workers(lane,heartbeat,active_run,pid) VALUES(?,?,?,?) ON CONFLICT(lane) DO UPDATE SET "
                      "heartbeat=excluded.heartbeat,active_run=excluded.active_run,pid=excluded.pid", (lane, now, active, os.getpid()))
        else:
            c.execute("INSERT INTO workers(lane,heartbeat,active_run,pid,capabilities) VALUES(?,?,?,?,?) ON CONFLICT(lane) DO UPDATE SET "
                      "heartbeat=excluded.heartbeat,active_run=excluded.active_run,pid=excluded.pid,capabilities=excluded.capabilities",
                      (lane, now, active, os.getpid(), json.dumps(capabilities)))


def lanes_status():
    now = time.time()
    with connection() as c:
        rows = {r['lane']: r for r in c.execute('SELECT * FROM workers')}
        legacy = c.execute("SELECT * FROM worker WHERE id=1").fetchone()
    status = {}
    for lane in LANES:
        row = rows.get(lane)
        if row is None and lane == 'compute' and legacy is not None:
            status[lane] = {'online': now - legacy['heartbeat'] < ONLINE_SECONDS, 'active_run': legacy['active_run'], 'capabilities': {}}
        elif row is None:
            status[lane] = {'online': False, 'active_run': None, 'capabilities': {}}
        else:
            status[lane] = {'online': now - row['heartbeat'] < ONLINE_SECONDS, 'active_run': row['active_run'],
                            'capabilities': json.loads(row['capabilities'] or '{}')}
    return status


def lane_online(lane):
    return lanes_status()[lane]['online']


def worker_status():
    lanes = lanes_status()
    # `online` keeps meaning "the compute lane is online" for existing callers.
    return {"online": lanes['compute']['online'], "active_run": lanes['compute']['active_run'], "lanes": lanes}


def recover(lanes=None, statuses=("running", "generating")):
    """Mark stranded jobs interrupted. Each lane recovers only its own jobs.

    'generating' is the API's inline case compilation; nothing else resets it, so an API
    restart mid-compile would otherwise strand the run forever.
    """
    where = "status IN (" + ",".join("?" * len(statuses)) + ")"
    params = list(statuses)
    if lanes:
        where += " AND lane IN (" + ",".join("?" * len(lanes)) + ")"
        params += list(lanes)
    with connection() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM runs WHERE " + where, params)]
        now = time.time()
        c.execute("UPDATE runs SET status='interrupted',stage='interrupted',error='Worker stopped. Artifacts preserved; retry creates a new attempt.',"
                  "updated=?,finished=? WHERE " + where, (now, now, *params))
    for identifier in ids:
        event(identifier, {"stage": "interrupted", "message": "Worker restarted; this attempt was interrupted"})
    return ids


def create_study(name, spec, kind='sweep', definition_version=1):
    identifier = uid()
    with connection() as c:
        c.execute("INSERT INTO studies(id,created,name,spec,kind,definition_version) VALUES(?,?,?,?,?,?)",
                  (identifier, time.time(), name, json.dumps(spec), kind, definition_version))
    return identifier


def record_calibration(kind, sample):
    with connection() as c:
        c.execute('INSERT INTO calibration VALUES(?,?,?,?)', (uid(), time.time(), kind, json.dumps(sample)))


def calibration(kind, limit=200):
    with connection() as c:
        return [json.loads(r[0]) for r in c.execute('SELECT sample FROM calibration WHERE kind=? ORDER BY created DESC LIMIT ?', (kind, limit))]
