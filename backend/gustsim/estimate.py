"""Cell-count, memory and runtime estimates for a configuration and each accuracy tier.

Heuristic octree counting, then scaled by calibration samples recorded after real mesh and
solve jobs on this computer. Always labelled an estimate; the mesher decides the mesh.
"""
import statistics
import numpy as np
from . import config, db, geometry, mesh_tiers

SOLVE_SECONDS_PER_CELL_ITERATION = {'laminar': 1.6e-6, 'kOmegaSST': 2.5e-6}
SOLVER_GB_PER_MILLION = {'laminar': 1.0, 'kOmegaSST': 1.3}
MESH_SECONDS_PER_CELL = 4e-4
PARALLEL_EFFICIENCY = 0.75


def wake_boxes(spec, meta):
    """The wake refinement boxes the case compiler emits, as (name, min, max, level)."""
    if spec.mode == 'internal' or not spec.mesh.nested_wake_boxes:
        return []
    # Identical to the v1 compiler: a zero freestream gives a box centred on the model.
    direction = np.asarray(spec.flow.velocity(), float)
    direction = direction/max(np.linalg.norm(direction), 1e-12)
    center = np.mean(meta['bounds'], axis=0)
    length = spec.references.length
    boxes = []
    for k in range(spec.mesh.nested_wake_boxes):
        end = center + direction*length*spec.mesh.wake_length_factor*(1+k)
        pad = length*0.6*(1+0.5*k)
        boxes.append(('wake' if k == 0 else f'wake_{k}', np.minimum(center, end)-pad, np.maximum(center, end)+pad,
                      max(1, spec.mesh.surface_level-spec.mesh.wake_level_offset-k)))
    return boxes


def heuristic_cells(spec, meta, surface_area):
    low, high = np.asarray(spec.domain.minimum, float), np.asarray(spec.domain.maximum, float)
    extent = high - low
    longest = float(extent.max())
    counts = np.maximum(4, np.ceil(spec.mesh.base_cells*extent/longest))
    h0 = longest/spec.mesh.base_cells
    background = float(np.prod(counts))
    hs = h0/2**spec.mesh.surface_level
    band = 1.33*spec.mesh.n_cells_between_levels*surface_area/hs**2
    layers = spec.mesh.layers*surface_area/hs**2
    volume_solid = (meta['diagnostics'].get('volume_m3') or 0.0) if spec.mode != 'internal' else 0.0
    body = 0.0
    if spec.mesh.body_level:
        hb = h0/2**spec.mesh.body_level
        size = np.asarray(meta['dimensions'], float) + 4*hb
        body = max(float(np.prod(size)) - volume_solid, 0.0)/hb**3
    wake = 0.0
    inner = 0.0
    for _, a, b, level in wake_boxes(spec, meta):
        h = h0/2**level
        volume = float(np.prod(np.clip(np.minimum(b, high) - np.maximum(a, low), 0, None)))
        wake += max(volume - inner, 0.0)/h**3
        inner = volume
    if spec.mode == 'internal':
        # Inside a duct the fluid volume itself is refined to the surface level near walls.
        fluid = volume_solid or float(np.prod(meta['dimensions']))*0.5
        background = (meta['diagnostics'].get('volume_m3') or fluid)/h0**3
    total = background + band + layers + body + wake
    return {'background': background, 'surface_band': band, 'layers': layers, 'body_box': body, 'wake': wake, 'total': total}


def calibration_factor():
    samples = [s for s in db.calibration('mesh_cells') if s.get('estimate') and s.get('actual')]
    if len(samples) < 3:
        return 1.0, 0
    ratios = [s['actual']/s['estimate'] for s in samples]
    return float(min(max(statistics.median(ratios), 0.2), 5.0)), len(samples)


def seconds_per_cell_iteration(turbulence):
    samples = [s for s in db.calibration('solve_seconds') if s.get('turbulence') == turbulence and s.get('cell_iterations')]
    if len(samples) < 3:
        return SOLVE_SECONDS_PER_CELL_ITERATION[turbulence], False
    rates = [s['seconds']*s['processes']*PARALLEL_EFFICIENCY/s['cell_iterations'] for s in samples]
    return float(statistics.median(rates)), True


def expected_iterations(spec):
    samples = [s['iterations'] for s in db.calibration('solve_seconds') if s.get('tier') == spec.mesh.tier and s.get('iterations')]
    cap = min(spec.solver.iterations, spec.convergence.cap) if spec.convergence.enabled else spec.solver.iterations
    if len(samples) >= 3:
        return min(cap, int(statistics.median(samples)))
    return int(cap*0.6) if spec.convergence.enabled else cap


def estimate_one(spec, meta, surface_area, factor):
    raw = heuristic_cells(spec, meta, surface_area)
    expected = min(raw['total']*factor, spec.mesh.max_cells)
    cells = {'low': expected*0.7, 'expected': expected, 'high': min(expected*1.4, spec.mesh.max_cells*1.05)}
    turbulence = spec.flow.turbulence
    processes = spec.solver.processes
    rate, _ = seconds_per_cell_iteration(turbulence)
    iterations = expected_iterations(spec)
    parallel = processes*PARALLEL_EFFICIENCY if processes > 1 else 1
    mesh_minutes = expected*MESH_SECONDS_PER_CELL/(parallel if spec.mesh.parallel and spec.schema_version >= 2 else 1)/60
    solve_minutes = expected*iterations*rate/parallel/60
    time_steps = None
    if spec.time.mode == 'transient' and spec.flow.speed > 0:
        # The adaptive step settles near the Courant limit in the smallest cells; a PISO step
        # (two pressure correctors) costs about two SIMPLE iterations.
        from .transient import plan, smallest_cell
        times = plan(spec, 0)
        time_steps = int(times['duration_s']/(times['max_courant']*smallest_cell(spec)/spec.flow.speed))
        solve_minutes += expected*time_steps*2*rate/parallel/60
    memory_mesh = expected/1e6*mesh_tiers.SNAPPY_GB_PER_MILLION
    memory_solve = expected/1e6*SOLVER_GB_PER_MILLION[turbulence]
    feasible = max(memory_mesh, memory_solve) <= config.MEMORY_GB
    return {'tier': spec.mesh.tier, 'cells': {k: int(v) for k, v in cells.items()},
            'memory_gb': {'mesh': round(memory_mesh, 2), 'solve': round(memory_solve, 2)},
            'runtime_min': {'mesh': round(mesh_minutes, 1), 'solve': round(solve_minutes, 1), 'post': round(2 + expected/1e6*1.5, 1)},
            'iterations': iterations, 'time_steps': time_steps, 'feasible': feasible,
            'reason': None if feasible else f'Needs about {max(memory_mesh, memory_solve):.1f} GB; the worker limit is {config.MEMORY_GB:g} GB',
            'breakdown': {k: int(v) for k, v in raw.items()},
            'resolved_mesh': spec.mesh.model_dump(mode='json'), 'resolved_solver': {'iterations': spec.solver.iterations}}


def estimate(spec):
    meta = db.geometry(spec.geometry_id)
    mesh, _ = geometry.load(spec.geometry_id)
    area = float(mesh.area)
    factor, samples = calibration_factor()
    current = mesh_tiers.apply(spec, meta)
    tiers = [estimate_one(mesh_tiers.resolve(spec, tier, meta), meta, area, factor) for tier in mesh_tiers.TIERS]
    extent = np.asarray(current.domain.maximum, float) - np.asarray(current.domain.minimum, float)
    h0 = float(extent.max())/current.mesh.base_cells
    boxes = [{'name': name, 'min': a.tolist(), 'max': b.tolist(), 'level': level, 'cell_size_m': h0/2**level}
             for name, a, b, level in wake_boxes(current, meta)]
    return {'basis': 'calibrated' if samples else 'heuristic', 'samples': samples, 'calibration_factor': factor,
            'current': estimate_one(current, meta, area, factor), 'tiers': tiers,
            'refinement_boxes': boxes, 'background_cell_m': h0,
            'surface_cell_m': h0/2**current.mesh.surface_level, 'first_layer_m': current.mesh.first_layer_m,
            'note': 'Estimate from octree counting and this computer\'s past runs; the mesher decides the final mesh.'}


def record_mesh(spec, cell_count, seconds=None):
    """Store estimate vs actual after a mesh job so later estimates calibrate."""
    try:
        meta = db.geometry(spec.geometry_id)
        mesh, _ = geometry.load(spec.geometry_id)
        raw = heuristic_cells(spec, meta, float(mesh.area))['total']
        db.record_calibration('mesh_cells', {'estimate': raw, 'actual': cell_count, 'seconds': seconds,
                                             'tier': spec.mesh.tier, 'processes': spec.solver.processes})
    except Exception:
        pass


def record_solve(spec, cells, iterations, seconds):
    if cells and iterations and seconds:
        db.record_calibration('solve_seconds', {'cell_iterations': cells*iterations, 'iterations': iterations, 'seconds': seconds,
                                                'processes': spec.solver.processes, 'turbulence': spec.flow.turbulence, 'tier': spec.mesh.tier})
