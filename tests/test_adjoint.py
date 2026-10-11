"""Design advice dictionaries, restrictions and morph bookkeeping. No OpenFOAM run."""
import numpy as np
from gustsim import adjoint, config, db, foam, geometry, insights, validation, workflow
from gustsim.jobs.optimise import enqueue
from gustsim.models import DesignBox, Primitive, SimulationSpec


def external():
    g = geometry.add_primitive(Primitive(shape='sphere', radius=.1))
    spec = SimulationSpec.model_validate(validation.defaults(g['id']))
    spec.schema_version = 2
    spec.flow.turbulence = 'laminar'
    spec.design.enabled = True
    spec.design.iterations = 80
    return g, spec


def test_default_design_does_not_change_identity_and_enabling_it_does():
    _, spec = external()
    spec.design.enabled = False
    plain = spec.model_copy(deep=True)
    assert workflow.identity(spec) == workflow.identity(plain)
    spec.design.enabled = True
    assert workflow.identity(spec) != workflow.identity(plain)


def test_design_advice_is_experimental_and_rejects_rotating_flow():
    _, spec = external()
    report = validation.validate(spec)
    assert report['valid'], report['errors']
    assert report['physics_maturity'] == 'experimental'
    assert 'design_advice' in report['experimental_features']
    spec.rotation.enabled = True
    spec.rotation.patches = [spec.boundaries[0].patch]
    spec.rotation.radius = .2
    spec.rotation.length = .2
    spec.rotation.blade_radius = .11
    spec.rotation.rpm = 10
    spec.mode = 'rotor'
    report = validation.validate(spec)
    assert not report['valid'] and any('rotating region' in error for error in report['errors'])


def test_sensitivity_dictionary_names_the_objective_and_skips_the_wind_tunnel():
    _, spec = external()
    text = adjoint.optimisation_dict(spec, adjoint.objective_patches(spec))
    assert 'optimisationManager singleRun' in text
    assert 'type force' in text and 'drag' in text
    assert 'suffix gustsim' in text
    assert 'inlet' not in text and 'ground' not in text
    assert 'adjointTurbulence on' in adjoint.turbulence_dict(spec)
    spec.flow.turbulence = 'kOmegaSST'
    assert 'adjointTurbulence off' in adjoint.turbulence_dict(spec)
    root = config.DATA / 'adjoint-case'
    foam.compile_case(spec, root)
    adjoint.write_sensitivity(spec, root)
    written = (root / 'system' / 'optimisationDict').read_text(encoding='utf-8')
    assert 'singleRun' in written and 'div(phi,Ua)' in (root / 'system' / 'fvSchemes').read_text(encoding='utf-8')
    assert 'startFrom latestTime' in (root / 'system' / 'controlDict').read_text(encoding='utf-8')


def test_morph_dictionary_uses_the_design_box_and_bfgs():
    _, spec = external()
    spec.elements.design_boxes = [DesignBox(name='nose', center=(0.05, 0, 0), dimensions=(0.12, 0.12, 0.12), max_displacement_m=0.002)]
    report = validation.validate(spec)
    assert report['valid'], report['errors']
    assert 'morphing' in report['experimental_features']
    body = adjoint.optimisation_dict(spec, ['body'], morph=True)
    mesh = adjoint.dynamic_mesh_dict(spec)
    assert 'steadyOptimisation' in body and 'method BFGS' in body and 'maxInitChange 0.002' in body
    assert 'nose' in mesh and 'axisAligned' in mesh and 'confineBoundaryControlPoints true' in mesh
    bare = spec.model_copy(deep=True)
    bare.design.enabled = False
    report = validation.validate(bare)
    assert not report['valid'] and any('design advice' in error.lower() for error in report['errors'])


def test_sensitivity_is_parked_onto_the_primal_time():
    _, spec = external()
    root = geometry.revision_path(spec.geometry_id).parent.parent / 'adjoint-park'
    primal = root / '40'
    later = root / '41'
    primal.mkdir(parents=True)
    later.mkdir()
    (primal / 'U').write_text('primal', encoding='utf-8')
    (later / 'faceSensNormalgustsim').write_text('map', encoding='utf-8')
    (later / 'p').write_text('adjoint-only', encoding='utf-8')
    assert adjoint.park_sensitivity(root, '40') == ['faceSensNormalgustsim']
    assert (primal / 'faceSensNormalgustsim').read_text(encoding='utf-8') == 'map'
    assert not later.exists() and (primal / 'U').read_text(encoding='utf-8') == 'primal'
    adjoint.discard_later_times(root, '40')


def test_objective_history_keeps_the_cycle_before_the_rise():
    log = '\n'.join([
        'Time = 1', 'drag: 1.20', 'Time = 2', 'drag: 1.05', 'Time = 3', 'drag: 1.00', 'Time = 4', 'drag: 1.04',
    ])
    history = adjoint.objective_history(log, 'drag')
    assert history == [1.2, 1.05, 1.0, 1.04]
    assert adjoint.best_cycle(history) == 2
    assert adjoint.best_cycle([3.0, 2.0, 1.5]) == 2
    assert adjoint.best_cycle([]) is None


def test_design_summary_and_insight_quote_a_one_millimetre_move():
    _, spec = external()
    faces = {
        'centroid': np.array([[0.1, 0, 0], [0.1, 0.01, 0], [0.0, 0, 0.1]], float),
        'normal': np.tile([0, 0, 1], (3, 1)),
        'area': np.array([0.02, 0.02, 0.02]),
        'part_index': np.zeros(3),
        'Cp': np.zeros(3),
        'pressure_pa': np.zeros(3),
        'sensitivity': np.array([-100.0, -80.0, 20.0]),
    }
    summary = adjoint.summarise(faces, spec, {'fields': ['faceSensNormalgustsim']})
    assert summary['available'] and summary['summary']['min_n_per_m'] == -100
    assert summary['turbulence'] == 'laminar'
    found = insights.generate(faces, spec)
    advice = [item for item in found['insights'] if item['rule'] == 'design_advice_v1']
    assert advice and advice[0]['evidence']['delta_n_per_mm']['unit'] == 'N'
    assert 'not an experimental result' in advice[0]['limitations']
    assert advice[0]['view_preset']['array'] == 'sensitivity'


def test_morph_enqueue_is_idempotent_and_does_not_run_a_solver():
    _, spec = external()
    run = db.enqueue(spec.model_dump(), 'solve')
    db.update(run['id'], status='completed', stage='completed')
    first = enqueue(run['id'])
    second = enqueue(run['id'])
    assert first['id'] == second['id'] and first['kind'] == 'optimise' and first['parent_id'] == run['id']
    assert first['status'] == 'queued'
