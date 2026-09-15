"""Mesh quality evidence parsed from the logs OpenFOAM already writes.

Before this, the entire interpretation of checkMesh was `"Mesh OK." in text` plus a cell
count, and achieved layer coverage was hardcoded as unavailable -- so a mesh that
achieved none of its requested near-wall layers passed exactly like one that achieved
all of them. Layers set the near-wall resolution that wall shear, and therefore drag,
depends on.

The excerpts below are taken from real OpenCFD v2606 output.
"""
import pytest

from gustsim import validation, workflow
from gustsim.models import SimulationSpec

CHECK_LOG = """
Mesh stats
    points:           6333
    faces:            16053
    internal faces:   14385
    cells:            4911
    hexahedra:        4400

Checking geometry...
    Overall domain bounding box (-0.3 -0.3 -0.3) (0.9 0.3 0.3)
    Mesh has 3 solution directions
    Boundary openness (1.2e-16 -3.4e-17 5.6e-18) OK.
    Max cell openness = 2.1e-16 OK.
    Max aspect ratio = 1.775105926 OK.
    Minimum face area = 3.05e-05. Maximum face area = 0.0015.  Face area magnitudes OK.
    Min volume = 1.7e-07. Max volume = 5.9e-05.  Total volume = 0.42.  Cell volumes OK.
    Mesh non-orthogonality Max: 27.26398619 average: 8.927577057
    Non-orthogonality check OK.
    Face pyramids OK.
    Max skewness = 0.7365483188 OK.
    Coupled point location match (average 0) OK.

Mesh OK.
"""

BAD_CHECK_LOG = """
Mesh stats
    points:           1000
    cells:            900
Checking geometry...
    Max aspect ratio = 412.5.
    Mesh non-orthogonality Max: 82.4 average: 31.2
   *Number of severely non-orthogonal (> 70 degrees) faces: 145.
    Max skewness = 6.12
 ***Zero or negative cell volume detected.  Minimum negative volume: -1e-12.
 ***Error in face pyramids: 12 faces are incorrectly oriented.
Failed 3 mesh checks.
"""

# snappyHexMesh prints this table after the layer addition phase.
LAYER_LOG = """
Layer addition iteration 3
----------------------------

Extruding 140 out of 1668 faces (8.39%). Removed extrusion at 0 faces.

patch            faces    layers avg thickness[m]
                                 near-wall overall
-----            -----    ------ --------- -------
smoke_external_0 140      2      0.0002    0.00044


Layer mesh : cells:4911  faces:16053  points:6333
"""

SHORTFALL_LOG = """
patch            faces    layers avg thickness[m]
                                 near-wall overall
-----            -----    ------ --------- -------
body             800      1.1    0.0001    0.00022
wheels           200      0      0         0
"""


# --- checkMesh numbers --------------------------------------------------------------

def test_check_metrics_are_extracted_from_a_passing_log():
    metrics = workflow.mesh_metrics(CHECK_LOG)
    assert metrics['cells'] == 4911
    assert metrics['points'] == 6333
    assert metrics['max_non_orthogonality'] == pytest.approx(27.264, abs=1e-3)
    assert metrics['average_non_orthogonality'] == pytest.approx(8.9276, abs=1e-3)
    assert metrics['max_skewness'] == pytest.approx(0.73655, abs=1e-4)
    assert metrics['max_aspect_ratio'] == pytest.approx(1.7751, abs=1e-3)
    assert metrics['negative_volume_cells'] is False


def test_check_metrics_capture_a_bad_mesh():
    metrics = workflow.mesh_metrics(BAD_CHECK_LOG)
    assert metrics['max_non_orthogonality'] == pytest.approx(82.4)
    assert metrics['max_skewness'] == pytest.approx(6.12)
    assert metrics['max_aspect_ratio'] == pytest.approx(412.5)
    assert metrics['severely_non_orthogonal_faces'] == 145
    assert metrics['incorrectly_oriented_faces'] == 12
    assert metrics['negative_volume_cells'] is True


def test_check_metrics_tolerate_an_empty_log():
    assert workflow.mesh_metrics('') == {'negative_volume_cells': False}


# --- achieved layer coverage --------------------------------------------------------

def test_layer_coverage_reads_the_snappy_table():
    coverage = workflow.layer_coverage(LAYER_LOG)
    assert coverage['faces'] == 140
    assert coverage['mean_layers'] == pytest.approx(2.0)
    assert coverage['patches'][0]['patch'] == 'smoke_external_0'
    assert coverage['patches'][0]['near_wall_thickness_m'] == pytest.approx(0.0002)


def test_layer_coverage_is_face_weighted_across_patches():
    coverage = workflow.layer_coverage(SHORTFALL_LOG)
    # (800*1.1 + 200*0) / 1000
    assert coverage['mean_layers'] == pytest.approx(0.88)
    assert coverage['min_layers'] == 0
    assert coverage['faces'] == 1000


def test_layer_coverage_is_none_without_a_table():
    assert workflow.layer_coverage('Finished meshing in = 0.48 s.') is None


# --- how coverage reaches the user --------------------------------------------------

def _spec(tmp_path, layers, max_cells=2_000_000):
    import trimesh
    from gustsim import geometry
    g = geometry.import_stl('b.stl', trimesh.creation.box(extents=[1, 1, 1]).export(file_type='stl'), 'm')
    spec = validation.defaults(g['id'])
    spec.mesh.layers = layers
    spec.mesh.max_cells = max_cells
    return spec


def _evidence(tmp_path, spec, meshing='', check=CHECK_LOG):
    (tmp_path / 'logs').mkdir(exist_ok=True)
    (tmp_path / 'logs' / 'check.log').write_text(check, encoding='utf-8')
    (tmp_path / 'logs' / 'meshing.log').write_text(meshing, encoding='utf-8')
    return workflow.mesh_evidence(tmp_path, spec, True, True, True)


def test_matching_layer_coverage_passes(tmp_path):
    summary = _evidence(tmp_path, _spec(tmp_path, 2), LAYER_LOG)
    finding = next(f for f in summary['findings'] if f['code'] == 'mesh_layers')
    assert finding['status'] == 'pass'
    assert summary['layer_coverage']['mean_layers'] == pytest.approx(2.0)


def test_layer_shortfall_is_reported_for_review(tmp_path):
    """5 requested, 0.88 achieved: this used to pass silently as an 'info' finding."""
    summary = _evidence(tmp_path, _spec(tmp_path, 5), SHORTFALL_LOG)
    finding = next(f for f in summary['findings'] if f['code'] == 'mesh_layers')
    assert finding['status'] == 'review'
    assert 'drag' in finding['detail']


def test_missing_layer_table_is_unavailable_not_a_pass(tmp_path):
    summary = _evidence(tmp_path, _spec(tmp_path, 3), 'no table here')
    finding = next(f for f in summary['findings'] if f['code'] == 'mesh_layers')
    assert finding['status'] == 'unavailable'


def test_no_layers_requested_is_informational(tmp_path):
    summary = _evidence(tmp_path, _spec(tmp_path, 0), '')
    finding = next(f for f in summary['findings'] if f['code'] == 'mesh_layers')
    assert finding['status'] == 'info'


def test_reaching_the_cell_budget_is_flagged(tmp_path):
    spec = _spec(tmp_path, 0, max_cells=4911)
    summary = _evidence(tmp_path, spec)
    assert any(f['code'] == 'mesh_budget' and f['status'] == 'review' for f in summary['findings'])


def test_comfortable_cell_budget_is_not_flagged(tmp_path):
    summary = _evidence(tmp_path, _spec(tmp_path, 0, max_cells=2_000_000))
    assert not any(f['code'] == 'mesh_budget' for f in summary['findings'])


def test_mesh_metrics_are_attached_to_the_summary(tmp_path):
    summary = _evidence(tmp_path, _spec(tmp_path, 0))
    assert summary['mesh_metrics']['max_non_orthogonality'] == pytest.approx(27.264, abs=1e-3)


# --- first-layer thickness from a y+ target -----------------------------------------

def _layer_spec(speed=27.8, treatment='wall_function', fluid='air', length=4.5):
    import trimesh
    from gustsim import geometry
    from gustsim.models import Fluid
    g = geometry.import_stl('car.stl',
                            trimesh.creation.box(extents=[length, 1.8, 1.4]).export(file_type='stl'), 'm')
    spec = validation.defaults(g['id'])
    spec.flow.speed = speed
    spec.mesh.wall_treatment = treatment
    if fluid == 'water':
        spec.fluid = Fluid(name='water', density=998.2, dynamic_viscosity=.001002, speed_of_sound=1482)
    return spec


def test_wall_resolved_asks_for_a_much_thinner_first_layer():
    """`wall_treatment` used to change only a boundary condition; the mesh was identical."""
    functions = validation.first_layer_thickness(_layer_spec(treatment='wall_function'))
    resolved = validation.first_layer_thickness(_layer_spec(treatment='resolved'))
    assert resolved < functions / 10
    # y+ ~ 50 vs ~ 1 is a factor of about 50.
    assert functions / resolved == pytest.approx(50, rel=0.3)


def test_first_layer_thins_as_speed_rises():
    slow = validation.first_layer_thickness(_layer_spec(speed=10))
    fast = validation.first_layer_thickness(_layer_spec(speed=50))
    assert fast < slow


def test_first_layer_is_a_plausible_automotive_size():
    """A car at 100 km/h with wall functions wants roughly a millimetre, not metres."""
    thickness = validation.first_layer_thickness(_layer_spec(speed=27.8))
    assert 2e-4 < thickness < 1e-2


def test_first_layer_depends_on_the_fluid():
    air = validation.first_layer_thickness(_layer_spec(fluid='air', speed=5))
    water = validation.first_layer_thickness(_layer_spec(fluid='water', speed=5))
    assert water < air


def test_first_layer_survives_zero_speed():
    """A still-air rotor has no freestream; sizing must not divide by zero."""
    spec = _layer_spec(speed=0)
    thickness = validation.first_layer_thickness(spec)
    assert thickness > 0 and thickness < spec.references.length


def test_static_rotor_sizes_against_blade_tip_speed():
    spec = _layer_spec(speed=0)
    spec.rotation.enabled = True
    spec.rotation.rpm = 3000
    spec.rotation.blade_radius = 0.3
    spinning = validation.first_layer_thickness(spec)
    spec.rotation.rpm = 300
    slow = validation.first_layer_thickness(spec)
    assert spinning < slow, 'a faster tip means higher shear and a thinner first cell'


# --- the wake refinement box belongs to external flow only --------------------------

def _internal_spec():
    """A closed box treated as a duct: only its own patches, fluid point inside."""
    import numpy as np
    import trimesh
    from gustsim import db, geometry
    from gustsim.models import Boundary
    g = geometry.import_stl('duct.stl',
                            trimesh.creation.box(extents=[2, 1, 1]).export(file_type='stl'), 'm')
    spec = validation.defaults(g['id'])
    names = [p['name'] for p in db.geometry(g['id'])['patches']]
    spec.mode = 'internal'
    spec.boundaries = ([Boundary(patch=names[0], kind='velocity_inlet', velocity=(1, 0, 0)),
                        Boundary(patch=names[1], kind='pressure_outlet')]
                       + [Boundary(patch=n, kind='wall') for n in names[2:]])
    spec.domain.fluid_point = tuple(np.mean(db.geometry(g['id'])['bounds'], axis=0))
    return spec


def test_internal_mode_does_not_add_a_wake_refinement_box(tmp_path):
    from gustsim import foam
    spec = _internal_spec()
    folder = foam.compile_case(spec, tmp_path / 'internal')
    assert 'wake' not in (folder / 'system' / 'snappyHexMeshDict').read_text()


def test_external_mode_keeps_the_wake_refinement_box(tmp_path):
    from gustsim import foam
    folder = foam.compile_case(_layer_spec(), tmp_path / 'external')
    assert 'wake' in (folder / 'system' / 'snappyHexMeshDict').read_text()
