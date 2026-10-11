"""One adaptive refinement cycle after a solved attempt.

The indicator is |ω|·h / U∞. Cells above the threshold become a surface, and the next mesh
refines within a short distance of that surface. mapFields then starts the new solve from the
previous fields. ΔCd is recorded on the refined solve. This is a numerics aid, not a mesh-study
acceptance; the mesh_study gate is still required before the feature is treated as validated.
"""
import hashlib
import struct
from pathlib import Path

THRESHOLD = 0.5


def mask(omega, length, speed, threshold=THRESHOLD):
    """True where the dimensionless vorticity indicator exceeds the threshold."""
    speed = abs(speed) or 1e-12
    return [bool(w * h / speed > threshold) for w, h in zip(omega, length)]


def distance_and_level(spec):
    """Refinement band, in metres, and the snappy level applied inside it."""
    extent = max(b - a for a, b in zip(spec.domain.minimum, spec.domain.maximum))
    spacing = extent / max(spec.mesh.base_cells, 1)
    level = min(max(spec.mesh.surface_level, 1), 6)
    return spacing * 4, level


def cycles_done(runs):
    return sum(1 for run in runs if run['kind'] == 'solve' and run['status'] == 'completed'
               and (run['spec'].get('mesh') or {}).get('adaptive_source'))


def _pending_mesh(identifier, runs):
    solved = {run.get('parent_id') for run in runs if run['kind'] == 'solve'}
    return [run for run in runs if run['kind'] == 'mesh' and run.get('simulation_id') == identifier and run['id'] not in solved]


def action(simulation, runs):
    """'indicator' or 'mesh' when another adaptive cycle should start. None when it should not."""
    spec = simulation['spec']
    mesh = spec.get('mesh') or {}
    budget = mesh.get('adaptive_cycles') or 0
    if budget <= 0 or spec.get('schema_version', 1) < 2:
        return None
    if _pending_mesh(simulation['id'], runs):
        return None
    solves = [run for run in runs if run['kind'] == 'solve' and run['status'] == 'completed']
    if not solves or cycles_done(runs) >= budget:
        return None
    latest = solves[-1]
    indicators = [run for run in runs if run['kind'] == 'amr_indicator' and run.get('parent_id') == latest['id']]
    if not indicators:
        return ('indicator', latest)
    indicator = indicators[-1]
    if indicator['status'] != 'completed':
        return None
    already = [run for run in runs if run['kind'] == 'mesh'
               and ((run['spec'].get('mesh') or {}).get('adaptive_source') or {}).get('run_id') == latest['id']]
    if already:
        return None
    return ('mesh', latest, indicator)


def refined_spec(spec, solve_id, indicator_result):
    from .models import AdaptiveSource, SimulationSpec
    out = SimulationSpec.model_validate(spec)
    out.mesh.adaptive_source = AdaptiveSource(
        run_id=solve_id, sha256=indicator_result['sha256'], level=int(indicator_result['level']),
        distance_m=float(indicator_result['distance_m']))
    return out


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_stl(path, triangles):
    """ASCII STL. `triangles` is a list of 3-tuples of xyz points."""
    lines = ['solid indicator']
    for tri in triangles:
        lines.append(' facet normal 0 0 0')
        lines.append('  outer loop')
        for point in tri:
            lines.append(f'   vertex {point[0]:.8g} {point[1]:.8g} {point[2]:.8g}')
        lines.append('  endloop')
        lines.append(' endfacet')
    lines.append('endsolid indicator')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')


def boxes_as_triangles(centers, size):
    """Two triangles on the top of a box at each centre. Enough for a distance refinement surface."""
    half = size / 2
    out = []
    for x, y, z in centers:
        z2 = z + half
        a, b, c, d = (x - half, y - half, z2), (x + half, y - half, z2), (x + half, y + half, z2), (x - half, y + half, z2)
        out.append((a, b, c))
        out.append((a, c, d))
    return out


def coefficient_change(previous, current):
    """Relative change of a coefficient. None when the baseline is missing or zero."""
    if previous in (None, 0) or current is None:
        return None
    return (current - previous) / abs(previous)


def indicator_id(solve_id):
    return hashlib.sha256(f'{solve_id}:amr_indicator:1'.encode()).hexdigest()[:32]


# Binary STL reader used only to confirm a pvbatch write. ASCII is what the tests emit.
def read_stl_triangles(path):
    data = Path(path).read_bytes()
    if data.lstrip().startswith(b'solid'):
        text = data.decode('utf-8', errors='replace')
        count = text.count('endfacet')
        return count
    if len(data) < 84:
        return 0
    count = struct.unpack_from('<I', data, 80)[0]
    return count
