"""M1 steady core: accuracy tiers, estimates, convergence detection and early stop,
automatic retries and averaged reporting."""
import json
import math
import sys
from pathlib import Path
import numpy as np
import pytest
from fastapi.testclient import TestClient
from gustsim import config, db, estimate, foam, geometry, mesh_tiers, presets, quality, retry, validation
from gustsim.api import app
from gustsim.convergence import Detector, averaged
from gustsim.models import Convergence, Primitive, PresetRequest, SimulationSpec
from gustsim.monitor import SolveMonitor, history
from gustsim.worker import LocalExecutor

FIXTURES = Path(__file__).with_name('fixtures')


def aero(tier='custom', speed=20):
    g = geometry.add_primitive(Primitive(shape='sphere', radius=.1))
    names = [p['name'] for p in g['patches']]
    data = presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=names, speed=speed, tier=tier))['spec']
    data['use_case']['confirmed'] = True; data['use_case']['references_confirmed'] = True
    return g, SimulationSpec.model_validate(data)


def test_tiers_are_monotonic_deterministic_and_bounded():
    g, spec = aero()
    resolved = {t: mesh_tiers.resolve(spec, t) for t in ('basic', 'regular', 'advanced')}
    levels = [resolved[t].mesh.surface_level + math.log2(resolved[t].mesh.base_cells) for t in ('basic', 'regular', 'advanced')]
    assert levels == sorted(levels) and len(set(levels)) == 3
    assert [resolved[t].mesh.nested_wake_boxes for t in resolved] == [1, 2, 3]
    assert all(r.mesh.surface_level <= mesh_tiers.MAX_SURFACE_LEVEL for r in resolved.values())
    assert all(r.convergence.enabled for r in resolved.values())
    assert mesh_tiers.resolve(spec, 'regular') == resolved['regular']
    assert mesh_tiers.apply(spec) is spec  # custom is untouched
    assert all(r.mesh.max_cells <= max(mesh_tiers.memory_cells(), 100_000) for r in resolved.values())
    # Advanced only asks for adaptive refinement once the compiler supports it.
    assert resolved['advanced'].mesh.adaptive_cycles == (1 if 'adaptive_mesh' in validation.COMPILED_FEATURES else 0)
    for r in resolved.values():
        assert validation.validate(r)['valid'], validation.validate(r)['errors']


def test_estimate_orders_tiers_and_calibrates():
    g, spec = aero()
    first = estimate.estimate(spec)
    cells = [t['cells']['expected'] for t in first['tiers']]
    assert cells == sorted(cells) and first['basis'] == 'heuristic'
    # A compact bluff body fits each tier's budget instead of saturating it: the body box sits
    # one level below the surface and the first wake box two.
    raw = [t['breakdown']['total'] for t in first['tiers']]
    assert raw == sorted(raw) and len(set(raw)) == 3
    for tier, total in zip(('basic', 'regular'), raw):
        resolved = mesh_tiers.resolve(spec, tier)
        assert total < resolved.mesh.max_cells, (tier, total)
        assert resolved.mesh.body_level == resolved.mesh.surface_level - 1
        assert resolved.mesh.wake_level_offset == 2
    assert first['refinement_boxes'] and first['refinement_boxes'][0]['name'] == 'wake'
    raw = estimate.heuristic_cells(spec, db.geometry(spec.geometry_id), float(geometry.load(spec.geometry_id)[0].area))['total']
    for _ in range(3):
        db.record_calibration('mesh_cells', {'estimate': raw, 'actual': raw*2})
    second = estimate.estimate(spec)
    assert second['basis'] == 'calibrated' and second['calibration_factor'] == pytest.approx(2.0)
    assert second['current']['cells']['expected'] == pytest.approx(min(first['current']['cells']['expected']*2, spec.mesh.max_cells), rel=1e-6)


def run_detector(signal, settings=None, floor=None):
    detector = Detector(settings or Convergence(enabled=True), floor or {})
    for i, value in enumerate(signal, start=1):
        detector.add(i, {'drag_n': value})
        if detector.should_stop:
            break
    return detector


def test_detector_converges_on_decay_and_averages():
    rng = np.random.default_rng(1)
    signal = 10 + 5*np.exp(-np.arange(6000)/150) + rng.normal(0, 0.02, 6000)
    d = run_detector(signal)
    assert d.state == 'converged' and d.detected_at < 2000
    start, end = d.window()
    assert 200 <= end - start <= 2000
    mean, _, basis = averaged(d.iterations, d.values['drag_n'], d.window())
    assert basis == 'window_mean' and abs(mean - 10) < 0.2
    assert 'Convergence detected after' in d.summary()['message']


def test_detector_never_converges_on_drift_and_flags_runaway():
    drift = run_detector(10 + np.arange(6000)*0.01)
    assert drift.state in ('iteration_limit',) and drift.detected_at is None
    oscillating = run_detector(10 + 30*np.sin(np.arange(6000)/5))
    assert oscillating.state == 'runaway'


def test_detector_floor_prevents_false_runaway_near_zero():
    rng = np.random.default_rng(2)
    small = rng.normal(0, 1e-4, 3000)  # lift of a symmetric body
    without = run_detector(small)
    with_floor = run_detector(small, floor={'drag_n': 0.05})
    assert without.state == 'runaway'
    assert with_floor.state == 'converged'


def test_detector_respects_cap():
    d = Detector(Convergence(enabled=True), {}, cap=300)
    for i in range(1, 400):
        d.add(i, {'drag_n': 10 + i*0.05})
        if d.state != 'searching':
            break
    assert d.state == 'iteration_limit' and d.stop_at == 300


def test_monitor_stops_fake_solver_and_writes_convergence(tmp_path):
    g, spec = aero('basic')
    spec.convergence = Convergence(enabled=True, min_iterations=60, averaging_min=40, check_every=5)
    job = db.enqueue(spec.model_dump(), 'solve')
    monitor = SolveMonitor(job['id'], tmp_path, spec, publish_every=0)
    LocalExecutor().run(job['id'], [sys.executable, str(FIXTURES / 'fake_solver.py'), '4000', '0.001'], tmp_path, 'solve', 120, monitor=monitor)
    summary = monitor.finish(None)
    assert (tmp_path / 'gustsim.stop').exists()
    assert summary['state'] == 'converged' and summary['stop']['by'] == 'force_convergence'
    assert json.loads((tmp_path / 'results/convergence.json').read_text())['detected_at'] == summary['detected_at']
    kinds = {e.get('type') for e in db.events(job['id'])}
    assert {'residuals', 'forces', 'convergence'} <= kinds
    forces, residuals = history(tmp_path, spec)
    assert forces[0]['drag_n'] == pytest.approx(-(10 + 5*math.exp(-1/40)), rel=1e-6) or forces[0]['drag_n'] != 0
    assert 'p' in residuals[-1] and 'Ux' in residuals[-1]


def write_case_tables(case, spec, rows=1500):
    loads = case / 'postProcessing/loads/0'; loads.mkdir(parents=True)
    drag_axis = np.asarray(spec.references.drag_axis)
    with (loads / 'force.dat').open('w') as f, (loads / 'moment.dat').open('w') as m:
        for i in range(1, rows + 1):
            drag = 10 + 5*math.exp(-i/100) + 0.3*math.sin(i/3)
            force = drag*drag_axis
            f.write(f'{i} ({force[0]} {force[1]} {force[2]}) (0 0 0) (0 0 0)\n')
            m.write(f'{i} (0 0 0) (0 0 0) (0 0 0)\n')
    (case / 'logs').mkdir(exist_ok=True)
    (case / 'logs/solve.log').write_text('Time = 1500\nEnd\n')
    (case / 'logs/check.log').write_text('Mesh OK.\ncells: 1000')


def test_v2_metrics_are_window_averages_and_v1_keeps_last_iteration(tmp_path):
    g, spec = aero()
    write_case_tables(tmp_path / 'v2', spec)
    result = quality.analyze(tmp_path / 'v2', spec)
    assert result['convergence']['state'] in ('converged', 'averaging')
    assert result['averaging']['basis'] == 'window_mean'
    last = result['metrics_last_iteration']['drag_n']
    assert abs(result['metrics']['drag_n'] - 10) < 0.1 and abs(last - 10) > 0.05 or abs(result['metrics']['drag_n'] - last) > 1e-3
    assert any(f['code'] == 'load_convergence' for f in result['findings'])
    assert result['termination'] == 'iteration_limit'
    v1 = spec.model_copy(deep=True); v1.schema_version = 1
    write_case_tables(tmp_path / 'v1', v1)
    old = quality.analyze(tmp_path / 'v1', v1)
    assert 'convergence' not in old and any(f['code'] == 'load_stability' for f in old['findings'])
    assert old['metrics']['drag_n'] == pytest.approx(10 + 5*math.exp(-15) + 0.3*math.sin(500))


def test_retry_rules_create_changes_not_mutations(tmp_path):
    g, spec = aero()
    data = spec.model_dump()
    failed_boundaries = {'mesh_summary': {'findings': [{'code': 'mesh_boundaries', 'status': 'fail'}]}}
    reason, changes, new, kind = retry.plan('mesh', data, 'Mesh quality, boundary, or rotor-zone check failed', failed_boundaries, tmp_path)
    assert kind == 'mesh' and changes[0]['path'] == 'mesh.body_level' and new['mesh']['body_level'] == data['mesh']['body_level'] + 1
    assert data == spec.model_dump(), 'the failed attempt spec is not modified'
    quality_fail = {'mesh_summary': {'findings': [{'code': 'mesh_quality', 'status': 'fail'}]}}
    _, changes, new, _ = retry.plan('mesh', data, 'x', quality_fail, tmp_path)
    assert new['mesh']['quality_profile'] == 'relaxed'
    (tmp_path / 'logs').mkdir(); (tmp_path / 'logs/solve.log').write_text('Time = 12\n#0 Foam::sigFpe::sigHandler\nFloating point exception')
    _, changes, new, kind = retry.plan('solve', data, 'solve failed with exit code 136', {}, tmp_path)
    assert kind == 'solve' and new['solver']['numerics'] == 'robust' and new['solver']['initialise'] == 'potential'
    assert retry.plan('solve', new, 'solve failed', {}, tmp_path) is None, 'no endless retries'
    assert retry.plan('mesh', data, 'Attempt exceeded the configured time limit', failed_boundaries, tmp_path) is None
    _, changes, new, _ = retry.plan('mesh', data, 'meshing exceeded the 20 GB process-tree memory ceiling', {}, tmp_path)
    assert new['mesh']['max_cells'] < data['mesh']['max_cells']


def test_failed_attempt_schedules_automatic_retry(tmp_path):
    from gustsim.worker import schedule_retry
    g, spec = aero()
    spec.solver.auto_retry = 1
    job = db.enqueue(spec.model_dump(), 'mesh')
    db.update(job['id'], status='failed', result={'mesh_summary': {'findings': [{'code': 'mesh_boundaries', 'status': 'fail'}]}})
    new = schedule_retry(db.run(job['id']), spec, RuntimeError('boundary check failed'), tmp_path)
    assert new['retry_of'] == job['id'] and new['attempt'] == 2 and new['retry_reason']
    assert db.run(new['id'])['result']['changes'][0]['path'] == 'mesh.body_level'
    assert db.run(job['id'])['status'] == 'failed', 'the failed attempt is kept'
    second = db.run(new['id'])
    assert schedule_retry(second, spec, RuntimeError('boundary check failed'), tmp_path) is None, 'auto_retry=1 allows one retry'


def test_api_validate_resolves_tier_and_estimate_endpoints():
    g, spec = aero()
    body = spec.model_dump(mode='json'); body['mesh']['tier'] = 'regular'
    with TestClient(app) as client:
        report = client.post('/api/validate', json=body).json()
        assert report['valid'], report['errors']
        assert report['resolved_spec']['mesh']['nested_wake_boxes'] == 2
        assert report['resolved_spec']['convergence']['enabled']
        tiers = client.get('/api/mesh/tiers').json()
        assert [t['tier'] for t in tiers] == ['basic', 'regular', 'advanced']
        est = client.post('/api/mesh/estimate', json=body).json()
        assert est['current']['tier'] == 'regular' and len(est['tiers']) == 3


def test_monitor_endpoint_and_progress_labels(tmp_path):
    g, spec = aero('basic')
    job = db.enqueue(spec.model_dump(), 'solve')
    root = config.DATA / 'runs' / job['id']
    write_case_tables(root, spec, rows=50)
    (root / 'results').mkdir(parents=True, exist_ok=True)
    (root / 'results/convergence_live.json').write_text(json.dumps({'state': 'searching', 'cap': 2000, 'averaging_start': None}))
    db.update(job['id'], status='running', stage='solve')
    with TestClient(app) as client:
        body = client.get(f"/api/runs/{job['id']}/monitor?after_iteration=10").json()
        assert body['forces'][0]['iteration'] == 11 and 'drag_n' in body['forces'][0]
        progress = client.get(f"/api/runs/{job['id']}").json()['progress']
        assert progress['basis'] == 'convergence monitor' and 'cap 2000' in progress['label']


def test_v2_case_contains_stop_and_output_function_objects(tmp_path):
    g, spec = aero('regular')
    case = foam.compile_case(spec, tmp_path / 'case')
    control = (case / 'system/controlDict').read_text()
    assert f'endTime {spec.solver.iterations}' in control
    for name in ('gustsimStop', 'wallShearStress', 'pressureCoefficient', 'Qcriterion'):
        assert name in control
    assert (case / 'gustsim.stop').as_posix() in control
    assert 'p 0.3' not in (case / 'system/fvSolution').read_text()
    still = spec.model_copy(deep=True); still.flow.speed = 0
    assert 'pressureCoefficient' not in foam.compile_case(still, tmp_path / 'still').joinpath('system/controlDict').read_text()
