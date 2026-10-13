"""Live solve monitor: tails force and residual tables while the solver runs.

Polled from LocalExecutor's heartbeat. It feeds the convergence detector, publishes typed
events (residuals, forces, convergence) for live charts, and asks for a clean stop once the
monitored loads have converged and been averaged.
"""
import json
import re
import time
from pathlib import Path
from . import db
from .convergence import Detector, floors, monitored
from .quality import NUMBER


class Tail:
    """Incrementally read numeric rows from the newest file matching a glob."""
    def __init__(self, case, pattern):
        self.case, self.pattern = Path(case), pattern
        self.path, self.offset, self.partial = None, 0, ''

    def rows(self):
        files = sorted(self.case.glob(self.pattern), key=lambda p: float(p.parent.name) if p.parent.name.replace('.', '', 1).isdigit() else 0)
        if not files:
            return []
        if self.path != files[-1]:
            self.path, self.offset, self.partial = files[-1], 0, ''
        with self.path.open('rb') as handle:
            handle.seek(self.offset)
            chunk = handle.read()
            self.offset = handle.tell()
        text = self.partial + chunk.decode(errors='replace')
        lines = text.split('\n')
        self.partial = lines.pop()
        out = []
        for line in lines:
            if line.strip() and not line.lstrip().startswith('#'):
                values = [float(v) for v in re.findall(NUMBER, line)]
                if values:
                    out.append((values, line))
        return out


def residual_columns(path):
    """Column names from a solverInfo.dat header ('# Time U_solver Ux_initial ...')."""
    if path is None or not path.exists():
        return None
    with path.open(errors='replace') as handle:
        for _ in range(5):
            line = handle.readline()
            if line.startswith('#') and 'Time' in line:
                return line.lstrip('#').split()
    return None


def residual_row(columns, values, line):
    # Rows hold, per field, the solver name (a word), initial, final and iterations
    # (vectors per component). Keep the *_initial columns.
    tokens = line.split()
    row = {'iteration': values[0]}
    if columns and len(columns) == len(tokens):
        for name, token in zip(columns[1:], tokens[1:]):
            if name.endswith('_initial'):
                try: row[name[:-8]] = float(token)
                except ValueError: pass
    return row


def segments(case, pattern):
    """Every restart segment of a function-object table (postProcessing/<name>/<start>/file),
    oldest first: a transient run continues the steady run in a new start directory."""
    files = sorted(Path(case).glob(pattern), key=lambda p: float(p.parent.name) if p.parent.name.replace('.', '', 1).isdigit() else 0)
    for path in files:
        tail = Tail(case, path.relative_to(case).as_posix())
        yield path, tail.rows()


def rotor_monitored(spec):
    """True when the convergence signals include the rotor's thrust and torque."""
    return bool(spec.rotation.enabled)


def rotor_loads_by_iteration(case, spec):
    """{iteration: {'thrust_n', 'torque_nm', ...}} from the rotorLoads force and moment tables."""
    if not rotor_monitored(spec):
        return {}
    from .quality import force_history, rotor_load
    out = {}
    for row in force_history(case, 'rotorLoads'):
        load = rotor_load(row, spec)
        if load is not None:
            out[row['iteration']] = load
    return out


def history(case, spec, after=-1.0, limit=5000):
    """Full residual and load histories from the solver's own tables (all restart segments)."""
    forces, residuals = [], []
    rotor = rotor_loads_by_iteration(case, spec)
    for _, rows in segments(case, 'postProcessing/loads/*/force.dat'):
        for values, _ in rows:
            if len(values) >= 4 and values[0] > after:
                forces.append({'iteration': values[0], **monitored(spec, {'force': values[1:4]}, rotor.get(values[0]))})
    for path, rows in segments(case, 'postProcessing/residuals/*/solverInfo.dat'):
        columns = residual_columns(path)
        residuals += [residual_row(columns, values, line) for values, line in rows if values[0] > after]
    return forces[::max(1, len(forces)//limit)], residuals[::max(1, len(residuals)//limit)]


class SolveMonitor:
    def __init__(self, run_id, case, spec, publish_every=2.0):
        self.run_id, self.case, self.spec = run_id, Path(case), spec
        self.forces = Tail(case, 'postProcessing/loads/*/force.dat')
        self.residuals = Tail(case, 'postProcessing/residuals/*/solverInfo.dat')
        self.enabled = spec.convergence.enabled
        cap = min(spec.convergence.cap, spec.solver.iterations)
        self.detector = Detector(spec.convergence, floors(spec), cap=cap)
        self.publish_every = publish_every
        self.last_publish = 0.0
        self.pending_forces, self.pending_residuals = [], []
        self.columns = None
        self.stop_requested = False
        # While held (the first-order startup stage of the robust profile) loads are recorded but
        # no stop is requested: the reported solution must come from the final numerics.
        self.hold = False
        # Propellers converge on thrust and torque, read from the rotorLoads tables.
        self.use_rotor = rotor_monitored(spec)
        self.rotor_force = Tail(case, 'postProcessing/rotorLoads/*/force.dat')
        self.rotor_moment = Tail(case, 'postProcessing/rotorLoads/*/moment.dat')
        self.rotor_forces, self.rotor_moments, self.waiting = {}, {}, []

    def restart_detection(self):
        """Start convergence detection afresh (after the robust startup stage)."""
        self.detector = Detector(self.spec.convergence, floors(self.spec), cap=self.detector.cap)

    def _ready_rows(self):
        """(iteration, force, rotor load or None) rows whose rotor loads have been read too."""
        for values, _ in self.forces.rows():
            if len(values) >= 4:
                self.waiting.append((values[0], values[1:4]))
        if not self.use_rotor:
            ready = [(i, f, None) for i, f in self.waiting]
            self.waiting = []
            return ready
        for values, _ in self.rotor_force.rows():
            if len(values) >= 4:
                self.rotor_forces[values[0]] = values[1:4]
        for values, _ in self.rotor_moment.rows():
            if len(values) >= 4:
                self.rotor_moments[values[0]] = values[1:4]
        if not self.rotor_forces and len(self.waiting) > 50:
            # No rotor table appeared: fall back to the total load rather than stall detection.
            self.use_rotor = False
            return self._ready_rows()
        from .quality import rotor_load
        ready, keep = [], []
        latest = min(max(self.rotor_forces, default=-1), max(self.rotor_moments, default=-1))
        for iteration, force in self.waiting:
            if iteration in self.rotor_forces and iteration in self.rotor_moments:
                load = rotor_load({'iteration': iteration, 'force': self.rotor_forces.pop(iteration),
                                   'moment': self.rotor_moments.pop(iteration)}, self.spec)
                if load is not None:
                    ready.append((iteration, force, load))
            elif iteration > latest:
                keep.append((iteration, force))
        self.waiting = keep[-5000:]
        return ready

    def __call__(self, elapsed=None):
        for iteration, force, rotor in self._ready_rows():
            signals = monitored(self.spec, {'iteration': iteration, 'force': force}, rotor)
            self.pending_forces.append({'iteration': iteration, **{k: round(v, 9) for k, v in signals.items()}})
            if self.enabled:
                self.detector.add(iteration, signals)
        for values, line in self.residuals.rows():
            if self.columns is None:
                self.columns = residual_columns(self.residuals.path)
            self.pending_residuals.append(residual_row(self.columns, values, line))
        now = time.monotonic()
        if now - self.last_publish >= self.publish_every:
            self.publish()
            self.last_publish = now
        if self.enabled and not self.hold and self.detector.should_stop and not self.stop_requested:
            self.stop_requested = True
            self.publish()
            return 'stop'
        return None

    def publish(self):
        if self.pending_residuals:
            db.event(self.run_id, {'type': 'residuals', 'stage': 'solve', 'rows': self.pending_residuals[-200:]})
            self.pending_residuals = []
        if self.pending_forces:
            db.event(self.run_id, {'type': 'forces', 'stage': 'solve', 'rows': self.pending_forces[-200:]})
            self.pending_forces = []
        if self.enabled:
            window = self.detector.window()
            state = {'type': 'convergence', 'stage': 'solve', 'state': self.detector.state,
                     'detected_at': self.detector.detected_at, 'averaging_start': window[0] if window else None,
                     'iteration': self.detector.iterations[-1] if self.detector.iterations else None,
                     'cap': self.detector.cap}
            db.event(self.run_id, state)
            # Progress reporting reads this small file instead of the event log.
            live = self.case / 'results' / 'convergence_live.json'
            live.parent.mkdir(parents=True, exist_ok=True)
            live.write_text(json.dumps(state), encoding='utf-8')

    def finish(self, final_iteration=None):
        """Write results/convergence.json; also used without early stop (post-hoc)."""
        self()
        self.publish()
        by = None
        if self.stop_requested:
            by = 'runaway_stopped' if self.detector.state == 'runaway' else 'force_convergence'
        summary = self.detector.summary(stop_by=by, final_iteration=final_iteration)
        target = self.case / 'results' / 'convergence.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(summary, indent=2), encoding='utf-8')
        return summary


def assess(case, spec, history):
    """Post-hoc convergence assessment of a finished solve's force history.

    Used for every v2 run (with or without early stop) so the averaging window and verdict
    are always available; it never stops anything."""
    detector = Detector(spec.convergence, floors(spec), cap=float('inf'))
    rotor = rotor_loads_by_iteration(case, spec)
    for row in history:
        load = rotor.get(row['iteration'])
        if rotor and load is None:
            continue  # keep one consistent signal set: thrust and torque need the rotor row
        detector.add(row['iteration'], monitored(spec, row, load))
    # A solve that ended on its own residual targets is judged on its loads even when it was
    # shorter than the detector's minimum history (fast, attached-flow cases).
    log = case/'logs'/'solve.log'
    try:
        residual_stop = log.is_file() and 'solution converged' in log.read_text(errors='replace')[-20000:].lower()
    except OSError:
        residual_stop = False
    if residual_stop and detector.state != 'converged':
        detector.accept_residual_stop()
    if detector.state == 'searching':
        detector.state = 'iteration_limit'
    return detector
