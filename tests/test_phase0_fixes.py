"""Regression tests for the correctness fixes in phase 0.

Each test pins a behaviour that was demonstrably wrong before, so a future edit that
reintroduces the bug fails here rather than silently biasing a coefficient.
"""
import csv
import math

import numpy as np
import pytest
import trimesh

from gustsim import db, geometry, presets, progress, quality, validation
from gustsim.models import PresetRequest, SimulationSpec


def _box(extents=(2.0, 3.0, 4.0), name='box.stl'):
    source = trimesh.creation.box(extents=list(extents)).export(file_type='stl')
    return geometry.import_stl(name, source, 'm')


# --- B1: reference area follows the travel direction -------------------------------

@pytest.mark.parametrize('direction,exact', [
    ((1, 0, 0), 3.0 * 4.0),
    ((0, 1, 0), 2.0 * 4.0),
    ((0, 0, 1), 2.0 * 3.0),
])
def test_projected_area_matches_analytic_box_faces(direction, exact):
    mesh = trimesh.creation.box(extents=[2.0, 3.0, 4.0])
    assert geometry.projected_area(mesh, direction) == pytest.approx(exact, rel=0.01)


def test_projected_area_matches_analytic_sphere_from_any_direction():
    mesh = trimesh.creation.icosphere(subdivisions=4, radius=1.5)
    exact = math.pi * 1.5 ** 2
    for direction in [(1, 0, 0), (0, 1, 0), (1, 1, 1), (0.3, -1.0, 0.5)]:
        assert geometry.projected_area(mesh, direction) == pytest.approx(exact, rel=0.02)


def test_projected_area_does_not_double_count_concave_geometry():
    """0.5*sum(|n.d|*A) would report 5.0 here; the silhouette is 4.0."""
    nested = trimesh.util.concatenate([
        trimesh.creation.box(extents=[2, 2, 2]),
        trimesh.creation.box(extents=[1, 1, 1]),
    ])
    assert geometry.projected_area(nested, (1, 0, 0)) == pytest.approx(4.0, rel=0.01)


def test_projected_area_rejects_zero_direction():
    with pytest.raises(ValueError):
        geometry.projected_area(trimesh.creation.box(), (0, 0, 0))


def test_aerodynamics_preset_area_follows_travel_direction():
    """The old YZ bounding-box face gave 12 m^2 regardless of direction, so Cd was
    wrong by 50% for +Y travel on this box."""
    g = _box()
    areas = {}
    for axis in [(1, 0, 0), (0, 1, 0)]:
        lift = (0, 0, 1) if axis[2] == 0 else (0, 1, 0)
        request = PresetRequest(geometry_id=g['id'], kind='aerodynamics', speed=10.0,
                                direction=axis, lift_axis=lift,
                                patches=[p['name'] for p in g['patches']])
        areas[axis] = presets.generate(request)['spec']['references']['area']
    assert areas[(1, 0, 0)] == pytest.approx(12.0, rel=0.02)
    assert areas[(0, 1, 0)] == pytest.approx(8.0, rel=0.02)
    assert areas[(1, 0, 0)] != pytest.approx(areas[(0, 1, 0)], rel=0.05)


def test_stale_reference_area_is_flagged_when_direction_is_edited():
    g = _box()
    names = [p['name'] for p in g['patches']]
    request = PresetRequest(geometry_id=g['id'], kind='aerodynamics', speed=10.0,
                            direction=(1, 0, 0), lift_axis=(0, 0, 1), patches=names)
    spec = SimulationSpec.model_validate(presets.generate(request)['spec'])
    spec.use_case.confirmed = True
    assert not any('Reference area' in w for w in validation.validate(spec)['warnings'])
    # Rotate travel to +Y but leave the confirmed area describing the +X silhouette.
    spec.references.drag_axis = (0, 1, 0)
    spec.references.lift_axis = (0, 0, 1)
    assert any('Reference area' in w for w in validation.validate(spec)['warnings'])


# --- B4: report export must reflect real evidence ----------------------------------

def test_report_export_is_unavailable_for_a_queued_run():
    from fastapi.testclient import TestClient
    from gustsim.api import app
    g = _box()
    spec = validation.defaults(g['id'])
    job = db.enqueue(spec.model_dump(), 'solve')
    with TestClient(app) as client:
        record = client.get(f"/api/runs/{job['id']}").json()
    assert record['result']['export_available']['report'] is False


def test_report_export_becomes_available_once_evidence_exists():
    from fastapi.testclient import TestClient
    from gustsim.api import app
    g = _box()
    spec = validation.defaults(g['id'])
    job = db.enqueue(spec.model_dump(), 'solve')
    db.update(job['id'], result={'findings': [{'code': 'mesh', 'status': 'pass', 'detail': 'x'}]})
    with TestClient(app) as client:
        record = client.get(f"/api/runs/{job['id']}").json()
    assert record['result']['export_available']['report'] is True


# --- B11: an empty samples.csv must not 500 ----------------------------------------

def test_empty_samples_csv_returns_empty_columns(tmp_path):
    from fastapi.testclient import TestClient
    from gustsim.api import app
    from gustsim import config
    g = _box()
    spec = validation.defaults(g['id'])
    job = db.enqueue(spec.model_dump(), 'solve')
    view_id = 'a' * 32
    target = config.DATA / 'runs' / job['id'] / 'views' / view_id
    target.mkdir(parents=True, exist_ok=True)
    (target / 'samples.csv').write_text('', encoding='utf-8')
    with TestClient(app) as client:
        response = client.get(f"/api/runs/{job['id']}/views/{view_id}/samples")
    assert response.status_code == 200
    assert response.json() == {'columns': [], 'rows': [], 'association': 'interpolated points'}


# --- B13: a stopped attempt never reports progress toward completion ---------------

@pytest.mark.parametrize('status', ['failed', 'cancelled', 'interrupted'])
def test_stopped_runs_report_zero_percent_and_name_the_stage(tmp_path, status):
    run = {'status': status, 'stage': 'solve', 'kind': 'solve',
           'spec': {'solver': {'iterations': 1000}}}
    described = progress.describe(run, tmp_path)
    assert described['percent'] == 0
    assert described['basis'] == 'stopped'
    assert described['stopped_stage'] == 'solve'


def test_running_solve_still_reports_iteration_progress(tmp_path):
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs' / 'solve.log').write_text('Time = 300\n', encoding='utf-8')
    run = {'status': 'running', 'stage': 'solve', 'kind': 'solve',
           'spec': {'solver': {'iterations': 1000}}}
    described = progress.describe(run, tmp_path)
    assert described['basis'] == 'iteration budget'
    assert 0 < described['percent'] < 100


# --- B12: a retried view job stays attached to its solved run ----------------------

def test_retried_view_job_remains_visible_in_the_views_listing():
    from fastapi.testclient import TestClient
    from gustsim.api import app
    import json
    import time
    g = _box()
    spec = validation.defaults(g['id'])
    parent = db.enqueue(spec.model_dump(), 'solve')
    db.update(parent['id'], status='completed', stage='completed',
              result={'fields_available': True, 'case_available': True})
    view_id = db.uid()
    with db.connection() as c:
        c.execute('INSERT INTO views VALUES(?,?,?,?)',
                  (view_id, parent['id'], time.time(), json.dumps({'kind': 'surface'})))
    failed = db.enqueue({'run_id': parent['id'], 'view_id': view_id, 'view': {'kind': 'surface'}},
                        'view', parent_id=parent['id'])
    db.update(failed['id'], status='failed', error='pvbatch died')
    db.heartbeat(failed['id'])
    with TestClient(app) as client:
        retried = client.post(f"/api/runs/{failed['id']}/retry").json()
        listing = client.get(f"/api/runs/{parent['id']}/views").json()
    assert retried['parent_id'] == parent['id']
    assert [item['id'] for item in listing] == [view_id]
    assert listing[0]['status'] == 'queued'


# --- B18: per-component forces are parsed once per patch ---------------------------

def test_component_forces_are_collected_without_reparsing(monkeypatch, tmp_path):
    calls = []
    real = quality.force_history

    def counted(case, name='loads'):
        calls.append(name)
        return real(case, name)

    monkeypatch.setattr(quality, 'force_history', counted)
    g = _box()
    spec = validation.defaults(g['id'])
    (tmp_path / 'logs').mkdir()
    quality.analyze(tmp_path, spec)
    per_patch = [name for name in calls if name.startswith('load_')]
    assert len(per_patch) == len(set(per_patch)), 'each patch must be parsed at most once'


# --- Workstream B: the automatic result set ----------------------------------------

def _aero_spec():
    g = _box()
    request = PresetRequest(geometry_id=g['id'], kind='aerodynamics', speed=10.0,
                            direction=(1, 0, 0), lift_axis=(0, 0, 1),
                            patches=[p['name'] for p in g['patches']])
    return SimulationSpec.model_validate(presets.generate(request)['spec'])


def test_aerodynamics_default_views_cover_wake_streamlines_and_wall_resolution():
    from gustsim.analysis import default_views
    spec = _aero_spec()
    spec.flow.turbulence = 'kOmegaSST'
    views = default_views(spec)
    kinds = {(v.kind, v.field) for v in views}
    assert ('surface', 'pressure_pa') in kinds
    assert ('streamlines', 'U') in kinds
    assert ('surface', 'yPlus') in kinds, 'y+ decides whether wall functions were valid'
    assert sum(1 for v in views if v.kind == 'slice') >= 3, 'need orthogonal sections plus a wake plane'


def test_laminar_run_does_not_request_a_yplus_view():
    """yPlus is only written by the function object for turbulent runs."""
    from gustsim.analysis import default_views
    spec = _aero_spec()
    spec.flow.turbulence = 'laminar'
    assert not any(v.field == 'yPlus' for v in default_views(spec))


def test_wake_plane_follows_the_travel_direction():
    from gustsim.analysis import default_views
    import numpy as np
    spec = _aero_spec()
    spec.flow.turbulence = 'laminar'
    direction = np.asarray(spec.flow.velocity(), dtype=float)
    direction = direction / np.linalg.norm(direction)
    wake = [v for v in default_views(spec)
            if v.kind == 'slice' and abs(abs(np.dot(v.normal, direction)) - 1) < 1e-6]
    assert wake, 'a plane normal to the flow is needed to show the wake'


def test_default_view_ids_changed_with_the_view_set():
    """The per-index id embeds a version; reusing v1 would collide with older runs."""
    import inspect
    from gustsim import analysis
    assert ':v2' in inspect.getsource(analysis.queue_default_views)
