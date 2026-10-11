"""M3: PDF report, run comparison (metrics and surface delta) and batch runs."""
import json
import re
import pytest
from fastapi.testclient import TestClient
import foamcase
from test_results_package import BODY, box_case, force_tables, complete_mesh
from gustsim import batches, config, db, pipeline, quality
from gustsim.api import app
from gustsim.jobs import package as package_job
from gustsim.jobs import report as report_job
from gustsim.models import Batch


def solved_run(speed=10.0, name='block'):
    g, spec, faces = box_case(speed)
    spec.name = name
    run = db.enqueue(spec.model_dump(), 'solve')
    case = config.DATA/'runs'/run['id']
    foamcase.build(case, cells=(16, 8, 8), body=BODY, body_faces=faces, speed=speed)
    force_tables(case, spec)
    result = quality.analyze(case, spec)
    result.update(fields_available=True, case_available=True, cell_count=960)
    db.update(run['id'], status='completed', stage='completed', result=result)
    package_job.build(run['id'], in_process=True)
    return db.run(run['id']), case


def test_pdf_report_pages_and_honesty_metadata():
    run, case = solved_run()
    path = report_job.build(run['id'])
    data = path.read_bytes()
    assert data[:4] == b'%PDF'
    assert len(re.findall(rb'/Type\s*/Page\b', data)) >= 8
    assert b'experimental validation pending' in data
    assert db.run(run['id'])['result']['report_pdf'] == 'results/report.pdf'
    with TestClient(app) as client:
        assert client.get(f"/api/runs/{run['id']}").json()['result']['export_available']['pdf']
        response = client.get(f"/api/runs/{run['id']}/export/pdf")
        assert response.status_code == 200 and response.content[:4] == b'%PDF'
        assert client.get(f"/api/runs/{run['id']}/export/package").content[:2] == b'PK'


def test_compare_metrics_deltas_and_surface():
    a, _ = solved_run(10.0, 'baseline')
    b, case_b = solved_run(12.0, 'faster')
    with TestClient(app) as client:
        body = client.get(f"/api/compare?runs={a['id']},{b['id']}").json()
        columns = {c['id'] for c in body['columns']}
        assert {'cd', 'drag_n', 'speed_m_s'} <= columns
        rows = {r['run_id']: r for r in body['rows']}
        assert rows[a['id']]['deltas']['speed_m_s']['abs'] == 0
        assert rows[b['id']]['deltas']['speed_m_s']['rel'] == pytest.approx(0.2)
        assert any(d['path'] == 'flow.speed' for d in body['config_diff'])
        assert 'note' in body and body['baseline'] == a['id']
        delta = client.post('/api/compare/surface', json={'a': a['id'], 'b': b['id'], 'field': 'Cp'}).json()
        assert delta['coverage'] == pytest.approx(1.0) and delta['arrays'] == ['a', 'b', 'delta', 'valid']
        file = client.get(delta['url'])
        assert file.status_code == 200 and file.content[:5] == b'<?xml'
        assert client.post('/api/compare/surface', json={'a': a['id'], 'b': b['id'], 'field': 'p; rm'}).status_code == 422
        assert client.get(f"/api/compare?runs={a['id']}").status_code == 422


def test_batch_shares_one_mesh_across_speeds():
    g, spec, _ = box_case()
    batch = Batch(name='Speed sweep', base=spec, kind='sweep', acknowledge_review=True,
                  parameters=[{'path': 'flow.speed', 'values': [10, 15, 20]}], tags=['sweep'])
    detail = batches.create(batch)
    assert len(detail['members']) == 3
    owners = [m for m in detail['members'] if not m['batch']['mesh_from']]
    assert len(owners) == 1, 'speed changes keep the mesh identity, so one mesh serves all three'
    meshes = [r for r in db.runs(kinds=('mesh',)) if r['simulation_id'] in {m['id'] for m in detail['members']}]
    assert len(meshes) == 1
    complete_mesh(meshes[0]['id'])
    pipeline.advance(owners[0]['id'])
    solves = [r for r in db.runs(kinds=('solve',)) if r['simulation_id'] in {m['id'] for m in detail['members']}]
    assert len(solves) == 3 and {s['parent_id'] for s in solves} == {meshes[0]['id']}
    assert sorted(s['spec']['flow']['speed'] for s in solves) == [10, 15, 20]
    assert all(f'batch:{detail["id"][:8]}' in m['tags'] for m in detail['members'])


def test_batch_rejects_unsafe_and_oversized_definitions():
    g, spec, _ = box_case()
    with pytest.raises(Exception):
        Batch(name='x', base=spec, kind='sweep', parameters=[{'path': 'solver.processes', 'values': [1]}])
    with pytest.raises(Exception):
        Batch(name='x', base=spec, kind='grid', parameters=[{'path': 'flow.speed', 'values': list(range(1, 7))},
                                                             {'path': 'flow.beta_deg', 'values': list(range(6))}])
    with TestClient(app) as client:
        body = {'name': 'Variants', 'base': spec.model_dump(mode='json'), 'kind': 'variants', 'acknowledge_review': True,
                'variants': [{'label': 'Basic', 'overrides': {'mesh.tier': 'basic'}}, {'label': 'Regular', 'overrides': {'mesh.tier': 'regular'}}]}
        created = client.post('/api/batches', json=body)
        assert created.status_code == 202, created.text
        members = created.json()['members']
        assert {m['setup_summary']['tier'] for m in members} == {'basic', 'regular'}
        assert all(not m['batch']['mesh_from'] for m in members), 'different tiers need their own meshes'
        assert client.get('/api/batches').json()[0]['id'] == created.json()['id']


def test_ride_height_batch_moves_the_ground_per_member():
    from gustsim import presets
    from gustsim.models import PresetRequest, SimulationSpec
    g, spec, _ = box_case()
    data = presets.generate(PresetRequest(geometry_id=spec.geometry_id, kind='aerodynamics', patches=[p['name'] for p in g['patches']],
                                          speed=10, direction=(-1, 0, 0), ground='moving', ride_height_m=0.1))['spec']
    data['use_case']['confirmed'] = True; data['use_case']['references_confirmed'] = True
    base = SimulationSpec.model_validate(data)
    bottom = db.geometry(spec.geometry_id)['bounds'][0][2]
    members = batches.expand(Batch(name='Ride height', base=base, kind='sweep', acknowledge_review=True,
                                   parameters=[{'path': 'environment.ride_height_m', 'values': [0.05, 0.2]}]))
    floors = {label: member.domain.minimum[2] for label, _, member in members}
    assert floors == pytest.approx({'ride_height_m=0.05': bottom - 0.05, 'ride_height_m=0.2': bottom - 0.2})
    assert [m.environment.ride_height_m for _, _, m in members] == [0.05, 0.2]
    with pytest.raises(ValueError, match='ground plane'):
        batches.expand(Batch(name='No ground', base=spec, kind='sweep', parameters=[{'path': 'environment.ride_height_m', 'values': [0.1]}]))
