"""Versioned, additive SQLite schema migrations with a verified backup before any upgrade.

Rules (AGENTS.md): back up before migrating, never rewrite stored run snapshots, never
downgrade. Every migration only adds tables, columns or indexes, or fills a new column.
Only the API process migrates; worker and CAD processes wait for the schema.
"""
import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from . import config

BASELINE = """
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
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied REAL NOT NULL, description TEXT NOT NULL,
  backup_path TEXT, code_version TEXT);
"""

MIGRATIONS = [
    (1, 'baseline schema', [s.strip() for s in BASELINE.split(';') if s.strip()]),
    (2, 'job lanes, attempts and configuration identity', [
        "ALTER TABLE runs ADD COLUMN lane TEXT NOT NULL DEFAULT 'compute'",
        'ALTER TABLE runs ADD COLUMN simulation_id TEXT',
        'ALTER TABLE runs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1',
        'ALTER TABLE runs ADD COLUMN retry_of TEXT',
        'ALTER TABLE runs ADD COLUMN retry_reason TEXT',
        'ALTER TABLE runs ADD COLUMN started REAL',
        'ALTER TABLE runs ADD COLUMN finished REAL',
        'ALTER TABLE runs ADD COLUMN configuration_id TEXT',
        "UPDATE runs SET lane='post' WHERE kind='view'",
        'CREATE INDEX IF NOT EXISTS idx_runs_lane_status ON runs(lane, status, created)',
        'CREATE INDEX IF NOT EXISTS idx_runs_simulation ON runs(simulation_id, created)',
    ]),
    (3, 'per-lane worker heartbeats', [
        "CREATE TABLE IF NOT EXISTS workers(lane TEXT PRIMARY KEY, heartbeat REAL NOT NULL, active_run TEXT, pid INTEGER, capabilities TEXT NOT NULL DEFAULT '{}')",
    ]),
    (4, 'simulations and tags', [
        """CREATE TABLE IF NOT EXISTS simulations(id TEXT PRIMARY KEY, project_id TEXT, created REAL NOT NULL, updated REAL NOT NULL,
           name TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, plan TEXT NOT NULL, spec TEXT NOT NULL,
           summary TEXT NOT NULL DEFAULT '{}', archived INTEGER NOT NULL DEFAULT 0)""",
        'CREATE INDEX IF NOT EXISTS idx_simulations_project ON simulations(project_id, updated)',
        'CREATE TABLE IF NOT EXISTS simulation_tags(simulation_id TEXT NOT NULL REFERENCES simulations(id), tag TEXT NOT NULL, PRIMARY KEY(simulation_id, tag))',
        'CREATE INDEX IF NOT EXISTS idx_simulation_tags_tag ON simulation_tags(tag)',
    ]),
    (5, 'studies v2', [
        "ALTER TABLE studies ADD COLUMN kind TEXT NOT NULL DEFAULT 'sweep'",
        'ALTER TABLE studies ADD COLUMN definition_version INTEGER NOT NULL DEFAULT 1',
    ]),
    (6, 'estimate calibration samples', [
        'CREATE TABLE IF NOT EXISTS calibration(id TEXT PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL, sample TEXT NOT NULL)',
        'CREATE INDEX IF NOT EXISTS idx_calibration_kind ON calibration(kind, created)',
    ]),
]
LATEST = MIGRATIONS[-1][0]


def database_path():
    return config.DATA / 'gustsim.sqlite3'


def code_version():
    try:
        import importlib.metadata
        return importlib.metadata.version('gustsim')
    except Exception:
        return 'unknown'


def current_version(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version:
        return version
    # A database created before migrations existed has the baseline tables but no version.
    legacy = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'").fetchone()
    return -1 if legacy else 0


def has_user_data(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return any(db.execute(f'SELECT 1 FROM {t} LIMIT 1').fetchone() for t in ('runs', 'geometries', 'studies', 'setups') if t in tables)


@contextlib.contextmanager
def migration_lock():
    config.initialize()
    with (config.DATA / 'migrate.lock').open('a+b') as handle:
        handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1); break
                except OSError:
                    time.sleep(0.2)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def backup_database(source_version, target_version):
    """Copy the live database with SQLite's online backup API and verify the copy.

    Refuses (raises) when the copy fails its integrity check or the disk is too full;
    migration must not start without a verified backup.
    """
    source = database_path()
    folder = config.DATA / 'backups' / 'schema'
    folder.mkdir(parents=True, exist_ok=True)
    size = source.stat().st_size if source.exists() else 0
    if shutil.disk_usage(folder).free < 2 * size + 16 * 1024 * 1024:
        raise RuntimeError('Not enough free disk space to back up the database before migrating; free space and restart')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = folder / f'gustsim-{stamp}-v{max(source_version, 0)}-to-v{target_version}.sqlite3'
    with contextlib.closing(sqlite3.connect(source, timeout=30)) as live, contextlib.closing(sqlite3.connect(target)) as copy:
        live.backup(copy)
        status = copy.execute('PRAGMA integrity_check').fetchone()[0]
    if status != 'ok':
        target.unlink(missing_ok=True)
        raise RuntimeError(f'Database backup failed its integrity check ({status}); refusing to migrate')
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.json').write_text(json.dumps({
        'from_version': source_version, 'to_version': target_version, 'code_version': code_version(),
        'created': time.time(), 'sha256': digest, 'integrity_check': status}, indent=2), encoding='utf-8')
    return target


def migrate():
    """Bring the database to LATEST. Returns the list of applied versions."""
    config.initialize()
    applied = []
    with migration_lock():
        db = sqlite3.connect(database_path(), timeout=30, isolation_level=None)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            version = current_version(db)
            if version > LATEST:
                raise RuntimeError(f'Database schema v{version} is newer than this GustSim (v{LATEST}); refusing to start. '
                                   'Upgrade GustSim or restore a matching backup.')
            if version == LATEST:
                return applied
            backup = None
            if version != 0 and has_user_data(db):
                backup = str(backup_database(version, LATEST).relative_to(config.DATA))
            start = 1 if version <= 0 else version + 1
            for number, description, statements in MIGRATIONS:
                if number < start:
                    continue
                db.execute('BEGIN IMMEDIATE')
                try:
                    for statement in statements:
                        db.execute(statement)
                    db.execute('INSERT OR REPLACE INTO schema_migrations VALUES(?,?,?,?,?)',
                               (number, time.time(), description, backup, code_version()))
                    db.execute(f'PRAGMA user_version={number}')
                    db.execute('COMMIT')
                except BaseException:
                    db.execute('ROLLBACK')
                    raise
                applied.append(number)
        finally:
            db.close()
    return applied


def schema_version():
    if not database_path().exists():
        return 0
    with contextlib.closing(sqlite3.connect(database_path(), timeout=30)) as db:
        return db.execute('PRAGMA user_version').fetchone()[0]


def require_schema(timeout=120, interval=1.0):
    """Worker and CAD processes wait for the API to finish migrating instead of racing it."""
    deadline = time.monotonic() + timeout
    while True:
        version = schema_version()
        if version == LATEST:
            return version
        if version > LATEST:
            raise RuntimeError(f'Database schema v{version} is newer than this worker (v{LATEST})')
        if time.monotonic() > deadline:
            raise RuntimeError(f'Database schema v{version} is older than v{LATEST}; start the API so it can back up and migrate')
        time.sleep(interval)
