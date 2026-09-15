"""Shrink-wrap fallback for CAD that conservative repair cannot close.

The behaviour that matters: an assembly whose parts touch or interpenetrate -- which
conservative preparation can never close, because it refuses to weld across STEP
instance boundaries -- becomes meshable, and is unmistakably labelled as approximated.
"""
import numpy as np
import pytest
import trimesh

from gustsim import db, geometry, validation, workflow, wrap
from gustsim.models import Prepare


def _two_boxes(offset=0.5):
    """Two unit boxes sharing volume: the canonical un-closable assembly."""
    a = trimesh.creation.box(extents=[1, 1, 1])
    b = trimesh.creation.box(extents=[1, 1, 1])
    b.apply_translation([offset, 0, 0])
    return trimesh.util.concatenate([a, b])


# --- the wrap itself ----------------------------------------------------------------

def test_wrap_closes_interpenetrating_bodies():
    mesh = _two_boxes()
    wrapped, info = wrap.wrap(mesh, resolution=128)
    assert wrapped.is_watertight
    assert not wrapped.is_empty
    # Union of two unit cubes overlapping by half is 1.5 m^3.
    assert abs(wrapped.volume) == pytest.approx(1.5, rel=0.08)
    assert info['fidelity'] == 'wrapped'
    assert info['wrap_resolution_m'] > 0


def test_wrap_closes_touching_bodies():
    wrapped, _ = wrap.wrap(_two_boxes(offset=1.0), resolution=128)
    assert wrapped.is_watertight
    assert abs(wrapped.volume) == pytest.approx(2.0, rel=0.08)


def test_wrap_preserves_a_simple_solid():
    wrapped, _ = wrap.wrap(trimesh.creation.icosphere(subdivisions=3, radius=0.5), resolution=128)
    assert wrapped.is_watertight
    assert abs(wrapped.volume) == pytest.approx(4 / 3 * np.pi * 0.125, rel=0.08)


def test_wrap_refuses_rather_than_returning_a_hollow_shell():
    """A hole wider than the wrap resolution lets the flood in; that must be an error,
    not a thin shell silently presented as the body."""
    open_box = trimesh.creation.box(extents=[1, 1, 1])
    open_box.update_faces(np.arange(len(open_box.faces)) > 1)
    assert not open_box.is_watertight
    with pytest.raises(ValueError, match='leaked'):
        wrap.wrap(open_box, resolution=128, close_gaps=0)


def test_wrap_gap_closing_is_bounded_and_reported():
    wrapped, info = wrap.wrap(_two_boxes(), resolution=64, close_gaps=500)
    assert wrapped.is_watertight
    assert info['close_gaps_requested'] == 500
    assert info['close_gaps_voxels'] <= 64 // 8, 'an unbounded dilation would destroy the shape'


def test_wrap_rejects_degenerate_geometry():
    flat = trimesh.Trimesh(vertices=np.zeros((3, 3)), faces=np.array([[0, 1, 2]]), process=False)
    with pytest.raises(ValueError):
        wrap.wrap(flat, resolution=64)


def test_assign_groups_carries_patch_identity_onto_the_wrap():
    mesh = _two_boxes()
    # Label the two boxes as separate components.
    groups = np.concatenate([np.zeros(12, dtype=np.int32), np.ones(12, dtype=np.int32)])
    wrapped, _ = wrap.wrap(mesh, resolution=96)
    assigned = wrap.assign_groups(wrapped, mesh, groups)
    assert len(assigned) == len(wrapped.faces)
    assert set(np.unique(assigned)) <= {0, 1}
    assert len(set(np.unique(assigned))) == 2, 'both components must survive the wrap'


# --- integration through prepare / validation ---------------------------------------

def _import(mesh, name='assembly.stl'):
    return geometry.import_stl(name, mesh.export(file_type='stl'), 'm')


def test_prepare_wrap_produces_a_labelled_immutable_revision():
    source = _import(_two_boxes())
    revision = geometry.prepare(source['id'], Prepare(repair=True, wrap=True, wrap_resolution=96))
    assert revision['geometry_fidelity'] == 'wrapped'
    assert revision['wrap']['wrap_resolution_m'] > 0
    assert revision['parent_id'] == source['id']
    assert revision['diagnostics']['watertight'] is True
    # The original revision is untouched.
    assert db.geometry(source['id']).get('geometry_fidelity') is None


def test_wrap_unifies_a_multi_body_assembly_into_one_closed_surface():
    """Two interpenetrating bodies are two separate shells with overlapping interiors --
    geometry snappyHexMesh cannot turn into a single well-defined fluid boundary. The
    wrap replaces them with one shell, with no blocking findings left."""
    source = _import(_two_boxes())
    before = db.geometry(source['id'])['diagnostics']
    wrapped = geometry.prepare(source['id'], Prepare(repair=True, wrap=True, wrap_resolution=96))
    after = db.geometry(wrapped['id'])['diagnostics']
    assert before['components'] > 1, 'the source is more than one body'
    assert after['components'] == 1, 'the wrap is a single shell'
    assert after['watertight'] is True
    assert not [f['code'] for f in workflow.geometry_findings(db.geometry(wrapped['id']))
                if f['status'] == 'fail']


def test_wrapped_geometry_carries_a_visible_approximation_warning():
    source = _import(_two_boxes())
    wrapped = geometry.prepare(source['id'], Prepare(repair=True, wrap=True, wrap_resolution=96))
    spec = validation.defaults(wrapped['id'])
    report = validation.validate(spec)
    assert any('approximation' in w for w in report['warnings'])
    assert any(f['code'] == 'geometry_fidelity' and f['status'] == 'review' for f in report['findings'])


def test_closed_but_nonmanifold_geometry_is_a_review_not_a_blocker():
    """snappyHexMesh copes with non-manifold junctions on a closed surface; the old gate
    refused them outright, which is what made real assemblies unmeshable."""
    meta = {'diagnostics': {'watertight': True, 'winding_consistent': True,
                            'nonmanifold_edges': 1064, 'degenerate_faces': 0},
            'units_confirmed': True, 'cad_valid': True}
    findings = {f['code']: f['status'] for f in workflow.geometry_findings(meta)}
    assert findings['manifold'] == 'review'
    assert findings['closed_surface'] == 'pass'
    assert 'fail' not in findings.values()


def test_open_surface_still_blocks():
    meta = {'diagnostics': {'watertight': False, 'winding_consistent': True,
                            'nonmanifold_edges': 0, 'degenerate_faces': 0},
            'units_confirmed': True, 'cad_valid': True}
    findings = {f['code']: f['status'] for f in workflow.geometry_findings(meta)}
    assert findings['closed_surface'] == 'fail'


def test_wrapped_fidelity_reaches_the_export_manifest():
    from gustsim.exports import manifest
    source = _import(_two_boxes())
    wrapped = geometry.prepare(source['id'], Prepare(repair=True, wrap=True, wrap_resolution=96))
    spec = validation.defaults(wrapped['id'])
    run = db.enqueue(spec.model_dump(), 'solve')
    record = manifest(db.run(run['id']))
    assert record['geometry']['fidelity'] == 'wrapped'
    assert 'approximation' in record['geometry']['caveat']


def test_imported_geometry_manifest_is_not_labelled_wrapped():
    from gustsim.exports import manifest
    source = _import(trimesh.creation.box(extents=[1, 1, 1]))
    spec = validation.defaults(source['id'])
    run = db.enqueue(spec.model_dump(), 'solve')
    assert manifest(db.run(run['id']))['geometry']['fidelity'] == 'imported'
