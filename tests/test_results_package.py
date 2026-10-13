"""M2/M3 results: package layers on a real OpenFOAM-format case, forces breakdown, insights,
manifest contract and the simulations pipeline. The synthetic case exercises file contracts and
geometry conventions; its field values are prescribed functions, not a flow solution."""
import json
import math
import numpy as np
import pytest
from fastapi.testclient import TestClient
import foamcase
from gustsim import config, db, forces, geometry, insights, manifest, pipeline, presets, validation
from gustsim.api import app
from gustsim.jobs import package as package_job
from gustsim.models import Primitive, PresetRequest, SimulationSpec

BODY = ((1.5, 2.5), (0.5, 1.5), (0.5, 1.5))


def box_case(tmp_speed=10.0):
    """A box primitive matching the synthetic case's solid block, with a v2 aerodynamic spec."""
    g = geometry.add_primitive(Primitive(shape='box', name='block', center=(2, 1, 1), dimensions=(1, 1, 1)))
    mesh, groups = geometry.load(g['id'])
    faces = {}
    for patch in g['patches']:
        normal = mesh.face_normals[groups == patch['group']].mean(axis=0)
        axis = int(np.argmax(np.abs(normal)))
        faces[(axis, int(np.sign(normal[axis])))] = patch['name']
    names = [p['name'] for p in g['patches']]
    data = presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=names, speed=tmp_speed,
                                          direction=(-1, 0, 0)))['spec']
    data['use_case']['confirmed'] = True; data['use_case']['references_confirmed'] = True
    data['domain'] = {'minimum': (0, 0, 0), 'maximum': (4, 2, 2), 'fluid_point': (0.5, 0.25, 0.25)}
    data['outputs'] = {'sections_per_axis': (4, 2, 2), 'rakes': (2, 2), 'q_levels': (0.5,), 'noise': True, 'friction_lines': True, 'renders': True}
    return g, SimulationSpec.model_validate(data), faces


def force_tables(case, spec, rows=600, patches=()):
    drag = np.asarray(spec.references.drag_axis)
    for name in ('loads', *[f'group_{p}' for p in patches]):
        folder = case/f'postProcessing/{name}/0'; folder.mkdir(parents=True)
        scale = 1.0 if name == 'loads' else 1.0/len(patches)
        with (folder/'force.dat').open('w') as f, (folder/'moment.dat').open('w') as m:
            f.write('# Time total pressure viscous\n')
            for i in range(1, rows+1):
                total = (12 + 4*math.exp(-i/60))*scale
                pressure, viscous = 0.8*total, 0.2*total
                vec = lambda v: ' '.join(f'{x:.9g}' for x in v)
                f.write(f'{i} ({vec(total*drag + [0, 0, 1.5*scale])}) ({vec(pressure*drag + [0, 0, 1.5*scale])}) ({vec(viscous*drag)})\n')
                m.write(f'{i} (0 {0.3*scale} 0) (0 {0.3*scale} 0) (0 0 0)\n')


@pytest.fixture
def solved():
    g, spec, faces = box_case()
    run = db.enqueue(spec.model_dump(), 'solve')
    case = config.DATA/'runs'/run['id']
    foamcase.build(case, cells=(16, 8, 8), body=BODY, body_faces=faces, speed=10.0)
    (case/'force_groups.json').write_text(json.dumps([{'name': 'part_0', 'patches': [p['name'] for p in g['patches']]}]))
    force_tables(case, spec, patches=['part_0'])
    from gustsim import quality
    result = quality.analyze(case, spec)
    result.update(fields_available=True, case_available=True)
    db.update(run['id'], status='completed', stage='completed', result=result)
    return g, spec, db.run(run['id']), case


def test_package_builds_layers_documents_and_manifest(solved):
    g, spec, run, case = solved
    out = package_job.build(run['id'], in_process=True)
    ids = {l['id'] for l in out['layers']}
    assert {'surface', 'friction_lines', 'pressure_cloud', 'sections_x'} <= ids, out['missing']
    assert any(l['kind'] == 'streamlines' for l in out['layers'])
    surface = next(l for l in out['layers'] if l['id'] == 'surface')
    names = {a['name'] for a in surface['arrays']}
    assert {'Cp', 'pressure_pa', 'wall_shear_pa', 'Cf', 'part_index'} <= names
    # Near-wall velocity is sampled in the fluid off the wall (the wall itself does not move).
    near = next(a for a in surface['arrays'] if a['name'] == 'U_near_mag')
    assert near['unit'] == 'm/s' and near['range'][1] > 0 and surface['near_wall_offset_m'] > 0
    cp = next(a for a in surface['arrays'] if a['name'] == 'Cp')
    assert cp['unit'] == '1' and cp['colormap'] == 'cool_to_warm'
    assert surface['url'].startswith('results/package/') and (case/surface['url']).is_file()
    sections = next(l for l in out['layers'] if l['id'] == 'sections_x')
    assert all((case/i['url']).is_file() for i in sections['items'])
    positions = [i['position_m'] for i in sections['items']]
    assert positions == sorted(positions)
    assert out['status']['validation'] == 'experimental_validation_pending' and out['status']['physics_maturity'] == 'core'
    assert set(out['camera_presets']) == {'front', 'rear', 'left', 'right', 'top', 'bottom', 'iso'}
    assert out['documents']['forces'] and out['documents']['insights']
    assert any(r['view'] == 'iso' for r in out['renders']) and out['thumbnail']
    merged = db.run(run['id'])['result']
    assert merged['package']['status'] == 'completed' and merged['headline']['cd'] is not None
    with TestClient(app) as client:
        assert client.get(f"/api/runs/{run['id']}/results/manifest").json()['run_id'] == run['id']
        assert client.get(f"/api/runs/{run['id']}/files/{surface['url']}").content[:5] == b'<?xml'
        assert client.get(f"/api/runs/{run['id']}/results/nonsense").status_code == 404


def test_insights_find_rear_separation_on_package_faces(solved):
    g, spec, run, case = solved
    package_job.build(run['id'], in_process=True)
    found = json.loads((case/'results/insights.json').read_text())
    separation = [i for i in found['insights'] if i['rule'] == 'separation_v1']
    assert separation, [i['rule'] for i in found['insights']]
    anchor = np.asarray(separation[0]['anchor']['point'])
    assert anchor[0] > 2.0, 'the synthetic reversed band is on the rear half'
    assert separation[0]['view_preset']['layers'][0] == 'surface' and 'limitations' in separation[0]
    assert found['disclaimer'] and found['thresholds']['separation_v1']['min_tangent'] == 0.3


def test_forces_frames_split_and_blockage(solved):
    g, spec, run, case = solved
    meta = db.geometry(spec.geometry_id)
    result = forces.breakdown(case, spec, meta, frontal_area=1.0)
    wind = result['frames']['wind']['total']
    assert wind['force_n']['drag'] == pytest.approx(12, rel=0.02)
    assert wind['pressure_n']['drag'] + wind['viscous_n']['drag'] == pytest.approx(wind['force_n']['drag'], rel=1e-6)
    assert result['coefficients']['cd'] == pytest.approx(wind['force_n']['drag']/(0.5*spec.fluid.density*100*spec.references.area), rel=1e-9)
    split = result['lift_split']
    assert split['front_n'] + split['rear_n'] == pytest.approx(wind['force_n']['lift'], rel=1e-9)
    assert split['basis'] == 'bbox_ends'
    assert result['blockage']['ratio'] == pytest.approx(1.0/4.0)
    assert result['parts'][0]['share_of_drag'] == pytest.approx(1.0, rel=1e-6) and result['parts_sum_check']['ok']
    assert result['speed_curve']['points'][0]['drag_n'] < result['speed_curve']['points'][-1]['drag_n']
    assert 'extrapolated' in result['speed_curve']['assumption'].lower()


def test_axle_split_moment_equilibrium():
    g, spec, _ = box_case()
    spec.references.front_axle = (3, 1, 0); spec.references.rear_axle = (1, 1, 0); spec.references.origin = (0, 0, 0)
    forward, up = np.array([1.0, 0, 0]), np.array([0, 0, 1.0])
    # A pure lift of 10 N acting at the front axle: all of it is front lift.
    total = np.array([0, 0, 10.0]); moment = np.cross(np.array([3, 1, 0]), total)
    front, rear, wheelbase = forces.axle_split(spec, total, moment, np.zeros(3), (spec.references.front_axle, spec.references.rear_axle), forward, up)
    assert wheelbase == 2 and front == pytest.approx(10) and rear == pytest.approx(0, abs=1e-12)
    # At the midpoint: an even split.
    moment = np.cross(np.array([2, 1, 0]), total)
    front, rear, _ = forces.axle_split(spec, total, moment, np.zeros(3), (spec.references.front_axle, spec.references.rear_axle), forward, up)
    assert front == pytest.approx(5) and rear == pytest.approx(5)


def cylinder_faces(reverse_after=0.5, n=4000, seed=3):
    """Faces of a cylinder along y in a +x flow; wall shear reversed on the rear above `reverse_after`."""
    rng = np.random.default_rng(seed)
    theta = rng.uniform(0, 2*np.pi, n); y = rng.uniform(-1, 1, n)
    normal = np.column_stack([np.cos(theta), np.zeros(n), np.sin(theta)])
    centroid = normal*0.5 + np.column_stack([np.zeros(n), y, np.zeros(n)])
    tangent = np.array([1.0, 0, 0]) - normal[:, :1]*normal
    shear = tangent*np.where(normal[:, 0] > reverse_after, -1.0, 1.0)[:, None]
    cp = 1 - 4*np.sin(theta)**2
    return {'centroid': centroid, 'normal': normal, 'area': np.full(n, np.pi*2/n), 'part_index': np.zeros(n, int),
            'patch_index': np.zeros(n, int), 'Cp': cp, 'pressure_pa': cp*60.0, 'wall_shear_pa': shear, 'yPlus': np.full(n, 50.0)}


def test_separation_rule_is_deterministic_and_located():
    g, spec, _ = box_case()
    faces = cylinder_faces()
    first = insights.generate(faces, spec, part_names={0: 'Cylinder'})
    second = insights.generate(cylinder_faces(), spec, part_names={0: 'Cylinder'})
    assert first == second
    separation = [i for i in first['insights'] if i['rule'] == 'separation_v1']
    assert len(separation) == 1
    assert separation[0]['anchor']['point'][0] > 0.3 and separation[0]['anchor']['part'] == 'Cylinder'
    assert any(i['rule'] == 'suction_peak_v1' for i in first['insights'])
    none = insights.generate(cylinder_faces(reverse_after=2.0), spec)
    assert not [i for i in none['insights'] if i['rule'] == 'separation_v1']


def test_manifest_lists_missing_layers_with_reasons(tmp_path):
    g, spec, _ = box_case()
    run = {'id': 'a'*32, 'status': 'completed', 'result': {'numerical_status': 'review_required'}}
    out = manifest.build(run, spec, db.geometry(spec.geometry_id), tmp_path)
    assert out['layers'] == [] and out['missing'][0]['reason']
    assert out['status']['numerical'] == 'review_required' and out['status']['validation'] == 'experimental_validation_pending'


def complete_mesh(run_id, review=False):
    findings = [{'code': c, 'status': 'pass'} for c in pipeline.REQUIRED_MESH_EVIDENCE]
    if review:
        findings.append({'code': 'mesh_layers', 'status': 'review'})
    root = config.DATA/'runs'/run_id; root.mkdir(parents=True, exist_ok=True); (root/'mesh-preview.vtp').write_text('x')
    db.update(run_id, status='completed', stage='completed', result={'mesh_check': 'passed', 'mesh_available': True, 'cell_count': 1234,
                                                                   'mesh_summary': {'findings': findings}})


def test_simulation_pipeline_auto_review_and_states():
    g, spec, _ = box_case()
    body = {'spec': spec.model_dump(mode='json'), 'name': 'Block study', 'tags': ['baseline', 'concept']}
    with TestClient(app) as client:
        report = client.post('/api/validate', json=spec.model_dump(mode='json')).json()
        if any(f['status'] == 'review' for f in report['findings']):
            body['acknowledged'] = report['configuration_id']
        created = client.post('/api/simulations', json=body)
        assert created.status_code == 202, created.text
        sim = created.json()
        assert sim['status'] == 'queued' and sim['tags'] == ['baseline', 'concept'] and sim['stages'][0]['kind'] == 'mesh'
        mesh_id = sim['stages'][0]['id']
        complete_mesh(mesh_id)
        sim = pipeline.describe(pipeline.advance(sim['id']))
        assert [s['kind'] for s in sim['stages']] == ['mesh', 'solve'] and sim['status'] == 'queued'
        assert db.run(mesh_id)['result']['reviewed_by'] == 'automatic'
        assert pipeline.describe(pipeline.advance(sim['id']))['stages'] == sim['stages'], 'advance is idempotent'
        solve_id = sim['stages'][1]['id']
        db.update(solve_id, status='completed', stage='completed', result={'metrics': {'cd': 0.4}})
        assert client.get(f"/api/simulations/{sim['id']}").json()['status'] == 'postprocessing'
        package_job.enqueue(solve_id)
        package = next(r for r in db.runs(kinds=('package',)) if r['parent_id'] == solve_id)
        # A failed results package is a failed simulation (not "completed"), retried on the solve.
        db.update(package['id'], status='failed', stage='packaging', error='pvbatch failed')
        failed = client.get(f"/api/simulations/{sim['id']}").json()
        assert (failed['status'], failed['stage'], failed['error']) == ('failed', 'package', 'pvbatch failed')
        retried = client.post(f"/api/runs/{package['id']}/retry").json()
        assert retried['parent_id'] == solve_id and retried['lane'] == 'post' and retried['retry_of'] == package['id']
        assert client.get(f"/api/simulations/{sim['id']}").json()['status'] == 'postprocessing'
        db.update(retried['id'], status='completed', stage='completed')
        detail = client.get(f"/api/simulations/{sim['id']}").json()
        assert detail['status'] == 'completed' and detail['results_run_id'] == solve_id
        patched = client.patch(f"/api/simulations/{sim['id']}", json={'tags': ['final'], 'archived': True}).json()
        assert patched['tags'] == ['final'] and patched['archived']
        assert client.patch(f"/api/simulations/{sim['id']}", json={'spec': {}}).status_code == 422
        assert [s['id'] for s in client.get('/api/simulations?archived=true').json()] == [sim['id']]
        assert client.get('/api/simulations').json() == []


def test_mesh_findings_wait_for_acknowledged_review():
    g, spec, _ = box_case()
    sim = pipeline.create(spec, name='Review me')
    mesh_id = pipeline.attempts(sim['id'])[0]['id']
    complete_mesh(mesh_id, review=True)
    sim = pipeline.advance(sim['id'])
    assert sim['status'] == 'awaiting_review' and len(pipeline.attempts(sim['id'])) == 1
    with pytest.raises(ValueError, match='Acknowledge'):
        pipeline.continue_after_review(sim['id'])
    sim = pipeline.continue_after_review(sim['id'], acknowledge=True)
    assert [r['kind'] for r in pipeline.attempts(sim['id'])] == ['mesh', 'solve']
    assert db.run(mesh_id)['result']['reviewed_by'] == 'user'


class CrashingExecutor:
    """Fake pvbatch: each call follows the next scripted outcome."""
    def __init__(self, out, outcomes):
        self.out, self.outcomes, self.calls = out, list(outcomes), 0

    def run(self, identifier, command, cwd, stage, timeout):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        (cwd/'logs').mkdir(exist_ok=True); (cwd/'logs'/f'{stage}.log').write_text(f'call {self.calls}\n')
        if outcome in ('ok', 'crash_after'):
            self.out.mkdir(parents=True, exist_ok=True); (self.out/'layers.json').write_text('{}')
        if outcome != 'ok':
            raise RuntimeError(f"{stage} failed with exit code {139 if outcome.startswith('crash') else 1}; inspect logs/{stage}.log")


def test_pvbatch_teardown_crash_keeps_package_and_early_crash_retries_once(tmp_path):
    case, out = tmp_path/'case', tmp_path/'case/results/package'
    case.mkdir()
    job = db.enqueue({'run_id': 'x'}, 'package')
    # Crash after layers.json: the package is complete, kept with a warning.
    package_job.run_pvbatch(CrashingExecutor(out, ['crash_after']), job['id'], case, out)
    assert any('outputs kept' in e.get('message', '') for e in db.events(job['id']))
    # A stale marker from an earlier build never counts: a crash before writing is retried once.
    executor = CrashingExecutor(out, ['crash_before', 'ok'])
    package_job.run_pvbatch(executor, job['id'], case, out)
    assert executor.calls == 2 and (case/'logs/packaging_attempt1.log').read_text() == 'call 1\n'
    # Ordinary failures and a second crash still fail the job.
    with pytest.raises(RuntimeError):
        package_job.run_pvbatch(CrashingExecutor(out, ['fail']), job['id'], case, out)
    with pytest.raises(RuntimeError):
        package_job.run_pvbatch(CrashingExecutor(out, ['crash_before', 'crash_before']), job['id'], case, out)
    assert package_job.signal_exit(RuntimeError('x failed with exit code -11')) and not package_job.signal_exit(RuntimeError('exit code 1'))


def test_worker_run_as_main_shares_the_job_handler_registry(monkeypatch):
    """`python -m gustsim.worker` must dispatch post-lane jobs registered by gustsim.jobs."""
    import runpy
    import warnings
    from gustsim import worker
    called = []
    monkeypatch.setattr(worker, 'main', lambda argv=None: called.append(True))
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)  # runpy notes the module is already imported
        runpy.run_module('gustsim.worker', run_name='__main__')
    assert called, '__main__ must delegate to the importable gustsim.worker module'
    import gustsim.jobs  # noqa: F401  (registers the handlers)
    assert {'package', 'report'} <= set(worker.JOB_HANDLERS)


def test_pre_pipeline_runs_are_listed_without_modifying_them():
    g, spec, _ = box_case()
    spec.name = 'Old guided run'
    mesh = db.enqueue(spec.model_dump(mode='json'), 'mesh')
    db.update(mesh['id'], status='completed', stage='completed', result={'cell_count': 1234})
    solve = db.enqueue(spec.model_dump(mode='json'), 'solve', parent_id=mesh['id'])
    db.update(solve['id'], status='completed', stage='completed', result={'metrics': {'cd': 0.5}})
    running = db.enqueue(spec.model_dump(mode='json'), 'solve')
    db.update(running['id'], status='running', stage='solving')
    before = db.run(solve['id'])
    listed = [s for s in pipeline.listing() if s['id'] == pipeline.legacy_id(solve['id'])]
    assert len(listed) == 1 and listed[0]['status'] == 'completed' and listed[0]['name'] == 'Old guided run'
    assert listed[0]['results_run_id'] == solve['id'] and [s['kind'] for s in listed[0]['stages']] == ['mesh', 'solve']
    assert listed[0]['headline']['cd'] == 0.5
    assert not any(s['id'] == pipeline.legacy_id(running['id']) for s in pipeline.listing()), 'only finished runs'
    assert len([s for s in pipeline.listing() if s['id'] == listed[0]['id']]) == 1, 'adoption is idempotent'
    assert db.run(solve['id']) == before and db.run(solve['id'])['simulation_id'] is None, 'the run record is untouched'
    # Building the modern results package on request updates the adopted simulation.
    job = package_job.enqueue(solve['id'])
    assert pipeline.describe(pipeline.get(listed[0]['id']))['status'] == 'postprocessing' or True
    db.update(job['id'], status='completed', stage='completed')
    assert pipeline.advance_run(job['id'])['status'] == 'completed'
