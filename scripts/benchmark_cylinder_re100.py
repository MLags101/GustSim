"""Execute the cylinder_re100 benchmark gate on the pinned OpenCFD v2606 worker.

Laminar flow past a circular cylinder at Re = 100 sheds a periodic, two-dimensional vortex
street. The case is a quasi-2D section: a cylinder spanning the wind tunnel between two symmetry
planes. The gate compares the Strouhal number and the mean drag coefficient from GustSim's
transient pipeline with published values (tolerances fixed in benchmarks/README.md):

  St = 0.1643  Williamson (1996), Annu. Rev. Fluid Mech. 28:477-539, St(Re) = -3.3265/Re + 0.1816
               + 1.6e-4 Re (parallel shedding), evaluated at Re = 100; tolerance 3 %
  Cd = 1.33    Park, Kwon & Choi (1998), KSME Int. J. 12:1200-1205, mean drag at Re = 100;
               tolerance 5 %

Run inside the worker container with a temporary GUSTSIM_DATA and a writable output directory:

  docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps \
    -e GUSTSIM_DATA=/tmp/bench -v ./benchmarks/results:/results worker \
    python /app/scripts/benchmark_cylinder_re100.py --output /results

Both the measurements file and the evaluated gate result are written whatever the outcome; a
failed gate is a result, not an error. Promotion of the transient capability remains a reviewed
code change.
"""
import argparse
import json
import math
import time
from pathlib import Path
import numpy as np
import trimesh
from gustsim import benchmarks, config, db, geometry, validation
from gustsim.models import Boundary, Convergence, TimeControl
from gustsim.worker import process_job

REFERENCE = {'strouhal': (0.1643, 0.03), 'cd_mean': (1.33, 0.05)}
SOURCE = ('Williamson (1996) Annu. Rev. Fluid Mech. 28:477-539, St(Re) = -3.3265/Re + 0.1816 + 1.6e-4 Re at Re = 100; '
          'Park, Kwon & Choi (1998) KSME Int. J. 12:1200-1205, mean Cd = 1.33 at Re = 100')

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--processes', type=int, default=4)
parser.add_argument('--duration', type=float, default=600.0, help='Simulated time (s); one shedding period is about 40 s')
parser.add_argument('--surface-level', type=int, default=3, help='Refinement level at the cylinder (3: D/h = 32)')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
db.initialize()

D, span = 0.1, 0.1
cylinder = trimesh.creation.cylinder(radius=D/2, height=4*span, sections=192)
g = geometry.save(cylinder, np.zeros(len(cylinder.faces), dtype=int), {0: 'cylinder'}, 'cylinder_re100.stl', confirmed=True,
                  operations=[{'benchmark': 'cylinder_re100', 'diameter_m': D}])
s = validation.defaults(g['id'])
s.name = 'Benchmark · cylinder Re = 100'
s.schema_version = 2
nu = s.fluid.dynamic_viscosity/s.fluid.density
s.flow.speed = 100*nu/D
s.flow.turbulence = 'laminar'
s.domain.minimum = (-15*D, -15*D, -span/2)
s.domain.maximum = (25*D, 15*D, span/2)
s.domain.fluid_point = (-10*D, 5*D, 0.0)
kinds = {'inlet': 'velocity_inlet', 'outlet': 'pressure_outlet', 'front': 'freestream', 'back': 'freestream',
         'ground': 'symmetry', 'top': 'symmetry', 'cylinder': 'wall'}
s.boundaries = [Boundary(patch=patch, kind=kind) for patch, kind in kinds.items()]
s.references.area = D*span
s.references.length = D
s.references.drag_axis = (1, 0, 0)
s.references.lift_axis = (0, 1, 0)
s.mesh.tier = 'custom'
s.mesh.base_cells = 160                 # 0.025 m background cells, cubic over the 4.0 m tunnel
s.mesh.surface_level = args.surface_level
s.mesh.feature_level = args.surface_level
s.mesh.body_level = max(args.surface_level - 1, 1)
s.mesh.layers = 0
s.mesh.nested_wake_boxes = 2
s.mesh.wake_length_factor = 10
s.mesh.wake_level_offset = 1
s.mesh.max_cells = 3_000_000
s.solver.processes = args.processes
s.solver.iterations = 300               # steady start only
s.solver.write_interval = 100
s.solver.timeout_minutes = 600
s.convergence = Convergence(enabled=False)
s.time = TimeControl(mode='transient', duration_s=args.duration, average_after_fraction=0.35, frames=40, max_courant=0.8)
report = validation.validate(s)
assert report['valid'], report['errors']
print('Re =', s.flow.speed*D/nu, '· U =', s.flow.speed, 'm/s · blockage', D/(30*D), flush=True)

started = time.time()
job = db.enqueue(s.model_dump(), 'solve')
db.claim(('compute',))
process_job(job)
run = db.run(job['id'])
root = config.DATA/'runs'/run['id']
print(json.dumps({'status': run['status'], 'error': run['error'], 'cells': run['result'].get('cell_count'),
                  'termination': run['result'].get('termination')}, indent=2), flush=True)
if run['status'] != 'completed':
    raise SystemExit(1)
stats = json.loads((root/'results/transient.json').read_text())
print('Transient:', stats['state'], '-', stats['message'], flush=True)
measured = {'strouhal': stats.get('strouhal'), 'cd_mean': ((stats.get('statistics') or {}).get('cd') or {}).get('mean')}
measurements = {
    'benchmark': 'reference', 'gate': 'cylinder_re100',
    'metrics': {name: {'measured': measured[name], 'reference': ref, 'tolerance': tol} for name, (ref, tol) in REFERENCE.items()},
    'provenance': {
        'source': SOURCE, 'run_id': run['id'], 'date': time.strftime('%Y-%m-%d'), 'wall_time_s': round(time.time() - started),
        'openfoam': 'OpenCFD v2606 (pinned worker image)', 'solver': 'simpleFoam start, then pimpleFoam (laminar)',
        'case': {'reynolds': s.flow.speed*D/nu, 'diameter_m': D, 'span_m': span, 'speed_m_s': s.flow.speed,
                 'domain_D': {'upstream': 15, 'downstream': 25, 'lateral': 15}, 'blockage': D/(30*D),
                 'spanwise': 'symmetry planes (quasi-2D)', 'wall_cell_D_over_h': D/(4.0/160/2**s.mesh.surface_level),
                 'cells': run['result'].get('cell_count'), 'duration_s': args.duration, 'max_courant': 0.8},
        'transient': {k: stats.get(k) for k in ('state', 'message', 'periods_in_window', 'dominant_frequency_hz', 'window')},
        'cl_std': ((stats.get('statistics') or {}).get('cl') or {}).get('std'),
    }}
(args.output/'cylinder_re100.measurements.json').write_text(json.dumps(measurements, indent=2) + '\n', encoding='utf-8')
if any(v is None for v in measured.values()):
    print('The transient statistics did not produce a Strouhal number and a mean Cd; the gate cannot be evaluated', flush=True)
    raise SystemExit(1)
result = benchmarks.evaluate(measurements)
(args.output/'cylinder_re100.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'gate': result['gate'], 'status': result['status'], 'measured': measured,
                  'normalized_errors': result['normalized_errors'], 'tolerances': result['tolerances']}, indent=2))
raise SystemExit(0 if result['passed'] else 2)
