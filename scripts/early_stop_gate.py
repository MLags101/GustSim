"""Measurements for the early_stop_consistency gate, replayed from full-budget solves.

For each solve run to its full iteration budget *without* early stopping, the recorded force
history is fed to the same Detector the live monitor uses, with early stopping enabled and the
run's own settings. The early-stopped loads are the detector's window average at the iteration
it would have stopped; the reference is the window average of the same run from the detection
iteration to the end of its full budget. SIMPLE iterations are deterministic, so up to the stop
the solution is the one an early-stopped run would have had: the replay measures exactly what
early stopping would have reported. Runs whose detector never declares convergence would run the
full budget either way and are listed without metrics.

Usage:
  python scripts/early_stop_gate.py http://127.0.0.1:8080 <run id> [...] > measurements.json
  python -m gustsim.benchmarks measurements.json --output benchmarks/results/early_stop_consistency.json
"""
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
from gustsim.convergence import Detector, floors  # noqa: E402
from gustsim.models import SimulationSpec  # noqa: E402

# Loads near zero (lift of a symmetric body) cannot be compared as a relative error; a load is
# a gate metric when its mean is at least this fraction of the largest monitored load of the same
# unit (forces with forces, moments with moments).
SIGNIFICANT = 0.1


def fetch(url):
    with urllib.request.urlopen(url) as response:
        return json.load(response)


def replay(base, run_id):
    run = fetch(f'{base}/api/runs/{run_id}')
    spec = SimulationSpec.model_validate(run['spec'])
    if spec.convergence.enabled:
        raise SystemExit(f'{run_id} was solved with early stopping; the gate needs full-budget runs')
    spec.convergence.enabled = True
    rows = fetch(f'{base}/api/runs/{run_id}/monitor')['forces']
    names = [k for k in rows[0] if k != 'iteration']
    detector = Detector(spec.convergence, floors(spec), cap=spec.solver.iterations)
    for row in rows:
        detector.add(row['iteration'], {k: row[k] for k in names if row.get(k) is not None})
        if detector.should_stop:
            break
    iterations = np.array([r['iteration'] for r in rows], float)
    case = {'run_id': run_id, 'name': spec.name, 'budget': spec.solver.iterations, 'final_iteration': float(iterations[-1]),
            'detector_state': detector.state, 'detected_at': detector.detected_at, 'stopped_at': detector.stop_at}
    if iterations[-1] < spec.solver.iterations:
        case['note'] = 'The run ended before its budget (residual control or cancellation); not a full-budget reference'
        return case, {}, {}
    if detector.state != 'converged':
        case['note'] = 'The detector never declared convergence: early stopping would have run the full budget'
        return case, {}, {}
    start, stop = detector.detected_at, detector.stop_at
    means = {}
    for name in names:
        values = np.array([r.get(name, np.nan) for r in rows], float)
        means[name] = (float(np.nanmean(values[(iterations >= start) & (iterations <= stop)])),
                       float(np.nanmean(values[iterations >= start])))
    unit = lambda name: name.rsplit('_', 1)[-1]  # drag_n -> n, torque_nm -> nm
    largest = {}
    for name, (_, f) in means.items():
        largest[unit(name)] = max(largest.get(unit(name), 0.0), abs(f))
    early, full = {}, {}
    for name, (e, f) in means.items():
        if abs(f) >= SIGNIFICANT*largest[unit(name)]:
            early[f'{spec.name} {name}'], full[f'{spec.name} {name}'] = e, f
    case['iterations_saved'] = float(iterations[-1] - stop)
    return case, early, full


def main(argv):
    base, ids = argv[1], argv[2:]
    cases, early, full = [], {}, {}
    for run_id in ids:
        case, e, f = replay(base, run_id)
        cases.append(case)
        early.update(e)
        full.update(f)
    print(json.dumps({'benchmark': 'early_stop_consistency', 'gate': 'early_stop_consistency',
                      'early_stop': early, 'full_budget': full,
                      'provenance': {'method': 'Detector replay on recorded full-budget force histories (scripts/early_stop_gate.py)',
                                     'cases': cases}}, indent=2))


if __name__ == '__main__':
    main(sys.argv)
