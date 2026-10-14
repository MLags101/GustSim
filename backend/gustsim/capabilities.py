"""Physics capability registry: maturity, benchmark gates and runtime availability.

Maturity is a deliberate code decision. A capability moves from `experimental` to `core`
only by editing this table after its gates have passed (benchmarks/results/<gate>.json);
nothing is promoted automatically, and a passed smoke test is never a gate.
"""
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Gate results: the source tree's benchmarks/results, or GUSTSIM_BENCHMARK_RESULTS where the
# package is installed elsewhere (the images install it into site-packages).
RESULTS = Path(os.environ.get('GUSTSIM_BENCHMARK_RESULTS') or ROOT / 'benchmarks' / 'results')

CAPABILITIES = {
    'steady_external': {'label': 'Steady external aerodynamics', 'maturity': 'core', 'gates': ['naca0012', 'mesh_study', 'domain_sensitivity'], 'probe': ['simpleFoam', 'snappyHexMesh']},
    'steady_internal': {'label': 'Steady internal flow', 'maturity': 'core', 'gates': ['laminar_pipe'], 'probe': ['simpleFoam']},
    'mrf_single': {'label': 'Single MRF rotating region', 'maturity': 'core', 'gates': ['rotating_cylinders', 'pptc'], 'probe': ['simpleFoam', 'topoSet']},
    'mrf_multi': {'label': 'Multiple rotating regions', 'maturity': 'experimental', 'gates': ['multi_zone_couette'], 'probe': ['topoSet']},
    'ground_wheels': {'label': 'Moving ground and rotating wheels', 'maturity': 'experimental', 'gates': ['fackrell_wheel', 'drivaer_ground'], 'probe': ['simpleFoam']},
    'porous': {'label': 'Porous radiators (Darcy-Forchheimer)', 'maturity': 'experimental', 'gates': ['porous_duct'], 'probe': ['topoSet']},
    'openings': {'label': 'Flow openings on the model', 'maturity': 'experimental', 'gates': ['laminar_pipe'], 'probe': ['simpleFoam']},
    # Promoted 2026-10-13 at the user's request: benchmarks/results/early_stop_consistency.json passed
    # (propeller and Ahmed body, early-stopped loads within 0.09 % of the full-budget averages).
    'early_stop': {'label': 'Force-based convergence detection', 'kind': 'numerics', 'maturity': 'core', 'gates': ['early_stop_consistency'], 'probe': ['simpleFoam'], 'function_object': 'abort'},
    'adaptive_mesh': {'label': 'Adaptive mesh refinement', 'kind': 'numerics', 'maturity': 'experimental', 'gates': ['mesh_study'], 'probe': ['mapFields']},
    'design_advice': {'label': 'Adjoint design advice (sensitivity maps)', 'maturity': 'experimental', 'gates': ['adjoint_fd'], 'probe': ['adjointOptimisationFoam']},
    'morphing': {'label': 'Adjoint shape morphing', 'maturity': 'experimental', 'gates': ['morph_verification'], 'probe': ['adjointOptimisationFoam']},
    'transient': {'label': 'Transient incompressible (pimpleFoam)', 'maturity': 'experimental', 'gates': ['cylinder_re100', 'square_cylinder'], 'probe': ['pimpleFoam']},
    'free_surface': {'label': 'Free surface (interFoam VOF)', 'maturity': 'experimental', 'gates': ['hydrostatic_box', 'dtc_resistance'], 'probe': ['interFoam', 'setFields']},
    'ami': {'label': 'Sliding mesh (cyclicAMI)', 'maturity': 'experimental', 'gates': ['pptc'], 'probe': ['pimpleFoam', 'createBaffles']},
    'noise_proxy': {'label': 'Noise clouds (Proudman estimate)', 'kind': 'output', 'maturity': 'qualitative', 'gates': [], 'probe': ['simpleFoam']},
    'video': {'label': 'Animation video export', 'kind': 'output', 'maturity': 'core', 'gates': [], 'probe': ['ffmpeg']},
}


def gate_status(gate):
    path = RESULTS / f'{gate}.json'
    if not path.is_file():
        return 'not_executed'
    try:
        status = json.loads(path.read_text(encoding='utf-8')).get('status')
    except (OSError, ValueError):
        return 'not_executed'
    return status if status in ('passed', 'failed') else 'not_executed'


def probe_runtime(which=shutil.which):
    """Which capabilities the worker image can run. Executable presence is necessary,
    not sufficient: function-object keywords are confirmed by scripts/probe_runtime.py."""
    programs = sorted({p for c in CAPABILITIES.values() for p in c['probe']})
    found = {p: bool(which(p)) for p in programs}
    from .hardware import report
    return {'programs': found,
            'available': {k: all(found[p] for p in c['probe']) for k, c in CAPABILITIES.items()},
            'hardware': report()}


def capabilities_used(spec):
    """Capabilities a configuration depends on. Accepts a SimulationSpec."""
    used = ['steady_internal' if spec.mode == 'internal' else 'steady_external']
    elements = getattr(spec, 'elements', None)
    environment = getattr(spec, 'environment', None)
    zones = len(elements.rotating_zones) if elements else 0
    total = int(spec.rotation.enabled) + zones
    if total:
        used.append('mrf_single' if total == 1 else 'mrf_multi')
    if elements:
        if elements.wheels or (environment and environment.ground != 'none'):
            used.append('ground_wheels')
        if elements.porous:
            used.append('porous')
        if elements.openings:
            used.append('openings')
    design = getattr(spec, 'design', None)
    if design is not None and design.enabled:
        used.append('design_advice')
        if elements and elements.design_boxes:
            used.append('morphing')
    convergence = getattr(spec, 'convergence', None)
    if convergence is not None and convergence.enabled:
        used.append('early_stop')
    mesh = spec.mesh
    if getattr(mesh, 'adaptive_cycles', 0):
        used.append('adaptive_mesh')
    time_control = getattr(spec, 'time', None)
    if time_control is not None and time_control.mode == 'transient':
        used.append('transient')
        if getattr(time_control, 'sliding_mesh', False):
            used.append('ami')
    if getattr(spec, 'free_surface', None) is not None:
        used.append('free_surface')
    return used


def experimental_used(spec):
    """Experimental capabilities in use, physics and numerics alike. Force-based early stopping
    and adaptive refinement change which solution is reported, so until their gates pass they
    need the same acknowledgement and labels as experimental physics. Outputs (the qualitative
    noise estimate, video) never make a run experimental."""
    return [c for c in capabilities_used(spec) if CAPABILITIES[c]['maturity'] == 'experimental' and CAPABILITIES[c].get('kind', 'physics') != 'output']


def report(worker_capabilities=None):
    available = (worker_capabilities or {}).get('available', {})
    known = bool(available)
    out = {}
    for key, c in CAPABILITIES.items():
        ok = available.get(key) if known else None
        out[key] = {'label': c['label'], 'kind': c.get('kind', 'physics'), 'maturity': c['maturity'],
                    'gates': {g: gate_status(g) for g in c['gates']},
                    'available': ok,
                    'reason': None if ok else ('Compute worker has not reported its runtime probe' if not known
                                               else 'Required programs are missing from the worker image: '
                                               + ', '.join(p for p in c['probe'] if not (worker_capabilities or {}).get('programs', {}).get(p)))}
    return out
