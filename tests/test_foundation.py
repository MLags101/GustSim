"""M0 foundations: migrations with verified backups, lanes, spec v2 compatibility,
capabilities, import formats and the worker program allowlist."""
import hashlib
import io
import json
import sqlite3
import sys
import time
from pathlib import Path
import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient
from gustsim import capabilities, config, db, geometry, migrations, validation, workflow
from gustsim.api import app
from gustsim.models import Primitive, SimulationSpec, Elements, RotatingZone, Environment, DesignBox
from gustsim.worker import LocalExecutor, program_allowed

FIXTURES = Path(__file__).with_name('fixtures')


def legacy_database(path):
    """The pre-migration schema exactly as v0.1 created it, with user data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as c:
        c.executescript("""
        CREATE TABLE geometries(id TEXT PRIMARY KEY, created REAL NOT NULL, metadata TEXT NOT NULL);
        CREATE TABLE studies(id TEXT PRIMARY KEY, created REAL NOT NULL, name TEXT NOT NULL, spec TEXT NOT NULL);
        CREATE TABLE setups(id TEXT PRIMARY KEY, created REAL NOT NULL, name TEXT NOT NULL, spec TEXT NOT NULL);
        CREATE TABLE runs(id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL,
          status TEXT NOT NULL, stage TEXT NOT NULL, kind TEXT NOT NULL, spec TEXT NOT NULL,
          study_id TEXT REFERENCES studies(id), parent_id TEXT REFERENCES runs(id), cancel INTEGER NOT NULL DEFAULT 0,
          error TEXT, result TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id), created REAL NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE views(id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id), created REAL NOT NULL, spec TEXT NOT NULL);
        CREATE TABLE worker(id INTEGER PRIMARY KEY CHECK(id=1), heartbeat REAL NOT NULL, active_run TEXT);
        """)
        c.execute("INSERT INTO runs(id,created,updated,status,stage,kind,spec,result) VALUES('a'||hex(randomblob(15)),1,1,'completed','completed','solve','{\"name\":\"old\"}','{\"metrics\":{\"cd\":0.3}}')")
        c.execute("INSERT INTO runs(id,created,updated,status,stage,kind,spec) VALUES('b'||hex(randomblob(15)),2,2,'queued','queued','view','{}')")


def test_legacy_database_is_backed_up_then_migrated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATA', tmp_path / 'legacy')
    legacy_database(config.DATA / 'gustsim.sqlite3')
    with sqlite3.connect(config.DATA / 'gustsim.sqlite3') as c:
        before = c.execute('SELECT id,spec,result FROM runs ORDER BY created').fetchall()
    assert migrations.migrate() == [m[0] for m in migrations.MIGRATIONS]
    backups = list((config.DATA / 'backups/schema').glob('*.sqlite3'))
    assert len(backups) == 1
    sidecar = json.loads(backups[0].with_suffix('.json').read_text())
    assert sidecar['integrity_check'] == 'ok' and sidecar['to_version'] == migrations.LATEST
    assert sidecar['sha256'] == hashlib.sha256(backups[0].read_bytes()).hexdigest()
    with sqlite3.connect(backups[0]) as c:
        assert c.execute('SELECT id,spec,result FROM runs ORDER BY created').fetchall() == before
    with sqlite3.connect(config.DATA / 'gustsim.sqlite3') as c:
        # Stored snapshots are never rewritten; only new columns are filled.
        assert c.execute('SELECT id,spec,result FROM runs ORDER BY created').fetchall() == before
        assert [r[0] for r in c.execute('SELECT lane FROM runs ORDER BY created')] == ['compute', 'post']
        assert c.execute('PRAGMA user_version').fetchone()[0] == migrations.LATEST
    # Idempotent: nothing more to apply, and no second backup.
    assert migrations.migrate() == []
    assert len(list((config.DATA / 'backups/schema').glob('*.sqlite3'))) == 1


def test_fresh_database_needs_no_backup_and_newer_schema_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATA', tmp_path / 'fresh')
    migrations.migrate()
    assert not (config.DATA / 'backups').exists()
    with sqlite3.connect(config.DATA / 'gustsim.sqlite3') as c:
        c.execute(f'PRAGMA user_version={migrations.LATEST + 1}')
    with pytest.raises(RuntimeError, match='newer'):
        migrations.migrate()
    with pytest.raises(RuntimeError, match='newer'):
        migrations.require_schema(timeout=0)


def sample_spec():
    g = geometry.add_primitive(Primitive(shape='sphere', name='sphere'))
    return g, validation.defaults(g['id'])


def test_lanes_isolate_claims_and_recovery():
    _, spec = sample_spec()
    solve = db.enqueue(spec.model_dump(), 'solve')
    view = db.enqueue({'run_id': solve['id'], 'view_id': '0' * 32, 'view': {}}, 'view', parent_id=solve['id'])
    assert view['lane'] == 'post' and solve['lane'] == 'compute'
    assert solve['configuration_id'] == workflow.identity(spec)
    assert db.claim(('post',))['id'] == view['id']
    assert db.claim(('post',)) is None
    claimed = db.claim(('compute',))
    assert claimed['id'] == solve['id'] and db.run(solve['id'])['started']
    # Restarting the post lane must not interrupt the running solve.
    assert db.recover(lanes=('post',), statuses=('running',)) == [view['id']]
    assert db.run(solve['id'])['status'] == 'running'
    assert db.run(view['id'])['status'] == 'interrupted' and db.run(view['id'])['finished']


def test_lane_heartbeats_and_status():
    db.heartbeat(lane='post')
    status = db.worker_status()
    assert status['lanes']['post']['online'] and not status['online']
    db.heartbeat('x' * 32, lane='compute', capabilities={'available': {'steady_external': True}, 'programs': {}})
    status = db.worker_status()
    assert status['online'] and status['active_run'] == 'x' * 32
    assert status['lanes']['compute']['capabilities']['available']['steady_external']


def test_merge_result_is_additive():
    _, spec = sample_spec()
    run = db.enqueue(spec.model_dump(), 'solve')
    db.update(run['id'], result={'metrics': {'cd': 0.3}})
    merged = db.merge_result(run['id'], {'fields_available': True})
    assert merged == {'metrics': {'cd': 0.3}, 'fields_available': True} == db.run(run['id'])['result']


def legacy_identity(spec):
    """The v0.1 identity algorithm on the v0.1 dump (no v2 fields)."""
    data = spec.model_dump(mode='json')
    for key in ('environment', 'elements', 'convergence', 'time', 'free_surface', 'design', 'outputs'): data.pop(key)
    for key in ('tier', 'parallel', 'quality_profile', 'n_cells_between_levels', 'nested_wake_boxes', 'wake_length_factor', 'wake_level_offset', 'adaptive_cycles', 'adaptive_source'):
        data['mesh'].pop(key)
    for key in ('object_forward', 'object_up', 'front_axle', 'rear_axle', 'force_groups'): data['references'].pop(key)
    for key in ('name', 'solver', 'project_id'): data.pop(key, None)
    def canonical(value):
        if isinstance(value, dict): return {k: canonical(v) for k, v in value.items()}
        if isinstance(value, list): return [canonical(v) for v in value]
        return 0.0 if isinstance(value, float) and value == 0 else value
    return hashlib.sha256(json.dumps(canonical(data), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def test_v1_identity_is_unchanged_by_spec_v2():
    _, spec = sample_spec()
    assert workflow.identity(spec) == legacy_identity(spec)
    # Run control does not change the configuration.
    changed = spec.model_copy(deep=True); changed.convergence.enabled = True; changed.mesh.tier = 'regular'
    assert workflow.identity(changed) == workflow.identity(spec)
    # A real v2 change does.
    changed.environment = Environment(ground='moving')
    assert workflow.identity(changed) != workflow.identity(spec)


def test_mesh_identity_ignores_physics_only_changes():
    _, spec = sample_spec()
    base = workflow.mesh_identity(spec)
    faster = spec.model_copy(deep=True); faster.flow.speed *= 2; faster.flow.turbulence = 'laminar'; faster.fluid.density = 2
    assert workflow.mesh_identity(faster) == base
    finer = spec.model_copy(deep=True); finer.mesh.surface_level += 1
    assert workflow.mesh_identity(finer) != base
    yawed = spec.model_copy(deep=True); yawed.flow.beta_deg = 10
    assert workflow.mesh_identity(yawed) != base, 'the wake box follows the flow direction'
    slip = spec.model_copy(deep=True)
    slip.boundaries = [b.model_copy(update={'kind': 'slip'}) if b.patch == 'sphere' else b for b in slip.boundaries]
    if any(b.patch == 'sphere' for b in spec.boundaries):
        assert workflow.mesh_identity(slip) != base, 'wall typing changes layers and patch types'


def test_v2_mesh_reuse_accepts_speed_change_but_v1_does_not():
    _, spec = sample_spec()
    mesh = {'kind': 'mesh', 'status': 'completed', 'spec': spec.model_dump(), 'result': {'mesh_check': 'passed'}}
    faster = spec.model_copy(deep=True); faster.flow.speed *= 2
    with pytest.raises(ValueError):
        workflow.require_mesh(mesh, faster)
    v2 = spec.model_copy(deep=True); v2.schema_version = 2
    mesh['spec'] = v2.model_dump()
    faster2 = v2.model_copy(deep=True); faster2.flow.speed *= 2
    workflow.require_mesh(mesh, faster2)


def test_uncompiled_elements_are_rejected_and_experimental_needs_review():
    g, spec = sample_spec()
    patch = g['patches'][0]['name']
    spec.schema_version = 2
    spec.elements = Elements(design_boxes=[DesignBox(name='box', center=(0, 0, 0), dimensions=(.2, .2, .2), max_displacement_m=.01)])
    report = validation.validate(spec)
    assert not report['valid'] and any('design advice' in e.lower() for e in report['errors'])
    spec.elements = Elements(rotating_zones=[RotatingZone(name=n, rpm=100, radius=.6, length=.5, blade_radius=.5, patches=[patch]) for n in ('fan', 'fan2')])
    report = validation.validate(spec)
    assert not any('rotating zones' in e for e in report['errors'])
    assert 'mrf_multi' in report['experimental_features'] and report['physics_maturity'] == 'experimental'
    assert any(f['code'] == 'experimental_physics' and f['status'] == 'review' for f in report['findings'])


def test_core_configuration_reports_core_maturity():
    _, spec = sample_spec()
    report = validation.validate(spec)
    assert report['valid'] and report['physics_maturity'] == 'core' and report['experimental_features'] == []
    assert not any(f['code'] == 'experimental_physics' for f in report['findings'])


def test_capability_report_marks_unprobed_and_missing_programs():
    unknown = capabilities.report(None)
    assert unknown['transient']['available'] is None and unknown['transient']['maturity'] == 'experimental'
    assert unknown['steady_external']['gates']['naca0012'] == 'not_executed'
    probe = capabilities.probe_runtime(which=lambda p: p != 'interFoam')
    out = capabilities.report(probe)
    assert out['steady_external']['available'] and not out['free_surface']['available']
    assert 'interFoam' in out['free_surface']['reason']


def two_part_scene():
    a = trimesh.creation.box(extents=(1, 1, 1))
    b = trimesh.creation.icosphere(radius=.3); b.apply_translation((3, 0, 0))
    scene = trimesh.Scene(); scene.add_geometry(a, node_name='body'); scene.add_geometry(b, node_name='wheel')
    return scene


def test_obj_and_gltf_import_keep_components():
    glb = two_part_scene().export(file_type='glb')
    meta = geometry.import_file('assembly.glb', glb, units='m')
    assert sorted(p['name'] for p in meta['parts']) == ['body', 'wheel']
    assert meta['diagnostics']['watertight'] and meta['units_confirmed']
    np.testing.assert_allclose(meta['dimensions'], [3.8, 1, 1], atol=1e-6)
    obj = trimesh.creation.box(extents=(100, 50, 20)).export(file_type='obj')
    meta = geometry.import_file('box.obj', obj.encode() if isinstance(obj, str) else obj, units='mm')
    np.testing.assert_allclose(meta['dimensions'], [.1, .05, .02], atol=1e-9)
    with pytest.raises(ValueError, match='Supported formats'):
        geometry.import_file('model.3mf', b'x')


def test_revision_export_formats():
    g = geometry.add_primitive(Primitive(shape='box'))
    for fmt in ('stl', 'obj', 'glb'):
        path = geometry.export_revision(g['id'], fmt)
        loaded = trimesh.load(path, force='mesh')
        assert len(loaded.faces) == g['triangles']


def test_program_allowlist():
    assert program_allowed(['simpleFoam'])
    assert program_allowed(['mpirun', '-np', '4', 'simpleFoam', '-parallel'])
    assert program_allowed([sys.executable, '-c', 'pass'])
    assert not program_allowed(['bash', '-c', 'rm -rf /'])
    assert not program_allowed(['mpirun', '-np', '2', 'bash'])
    assert not program_allowed([])
    _, spec = sample_spec()
    job = db.enqueue(spec.model_dump(), 'case')
    with pytest.raises(RuntimeError, match='not permitted'):
        LocalExecutor().run(job['id'], ['bash', '-c', 'echo'], config.DATA, 'x', 10)


def test_monitor_stop_file_ends_solver_cleanly(tmp_path):
    _, spec = sample_spec()
    job = db.enqueue(spec.model_dump(), 'solve')
    calls = []
    def monitor(elapsed):
        calls.append(elapsed)
        return 'stop' if len(calls) >= 1 else None
    start = time.monotonic()
    LocalExecutor().run(job['id'], [sys.executable, str(FIXTURES / 'fake_solver.py'), '100000', '0.002'], tmp_path, 'solve', 60, monitor=monitor)
    assert (tmp_path / 'gustsim.stop').exists() and time.monotonic() - start < 30
    assert 'Abort: stop file found' in (tmp_path / 'logs/solve.log').read_text()


def test_api_capabilities_jobs_and_formats():
    with TestClient(app) as client:
        body = client.get('/api/capabilities').json()
        assert body['physics']['transient']['maturity'] == 'experimental'
        assert 'glb' in body['features']['formats'] and body['features']['async_import'] is False
        glb = two_part_scene().export(file_type='glb')
        r = client.post('/api/geometry/import', files={'file': ('a.glb', io.BytesIO(glb))}, data={'units': 'm'})
        assert r.status_code == 201, r.text
        assert client.post('/api/geometry/import-jobs', files={'file': ('a.glb', io.BytesIO(glb))}, data={'units': 'm'}).status_code == 503
        db.heartbeat(lane='cad')
        job = client.post('/api/geometry/import-jobs', files={'file': ('a.glb', io.BytesIO(glb))}, data={'units': 'm'})
        assert job.status_code == 202 and job.json()['lane'] == 'cad'
        from gustsim import cadworker
        cadworker.process(db.claim(('cad',)))
        done = client.get('/api/jobs/' + job.json()['id']).json()
        assert done['status'] == 'completed', done
        assert client.get('/api/geometry/' + done['result']['geometry_id']).json()['parts']
        assert not cadworker.upload_dir(job.json()['id']).exists()
        assert job.json()['id'] not in [r['id'] for r in client.get('/api/runs').json()]
        assert job.json()['id'] in [r['id'] for r in client.get('/api/runs?kinds=all').json()]
        exported = client.get('/api/geometry/' + done['result']['geometry_id'] + '/export/stl')
        assert exported.status_code == 200 and len(exported.content) > 100
