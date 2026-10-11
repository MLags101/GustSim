"""Force-history convergence detection and averaging.

The method follows the published approach used by steady RANS services (AirShaper's open
openfoam-convergence-detection): normalise each monitored load by its recent mean, fit a
line over the last third of the history and call it converged when the fitted drift stays
inside a band for several consecutive checks; then average over a window until the running
mean settles. Converged here means *the monitored loads stopped changing*; it is reported
separately from residual convergence and never implies accuracy.
"""
import numpy as np

STATES = ('searching', 'averaging', 'converged', 'runaway', 'iteration_limit')


class Detector:
    def __init__(self, settings, scales_floor=None, cap=None):
        """settings: a models.Convergence. scales_floor: {signal: minimum normalisation scale}
        so a load that averages to ~0 (lift of a symmetric body) cannot fake a runaway."""
        self.s = settings
        self.floor = scales_floor or {}
        self.cap = cap or settings.cap
        self.iterations = []
        self.values = {}
        self.state = 'searching'
        self.passes = 0
        self.runaway_checks = 0
        self.detected_at = None
        self.stop_at = None
        self.last_check = 0
        self.signals = {}

    def add(self, iteration, signals):
        """Append one sample; signals: {name: value}. Returns the state after any check."""
        if self.iterations and iteration <= self.iterations[-1]:
            return self.state
        self.iterations.append(float(iteration))
        for name, value in signals.items():
            self.values.setdefault(name, []).append(float(value))
        n = len(self.iterations)
        if self.state in ('converged', 'runaway', 'iteration_limit'):
            return self.state
        if iteration >= self.cap:
            self.state = 'converged' if self.state == 'averaging' and self._averaging_settled(final=True) else 'iteration_limit'
            self.stop_at = iteration
            return self.state
        if n >= self.s.min_iterations and iteration - self.last_check >= self.s.check_every:
            self.last_check = iteration
            self._check()
        return self.state

    def _scale(self, name, values):
        half = values[len(values)//2:]
        return max(abs(float(np.mean(half))), self.floor.get(name, 0.0), 1e-12)

    def _window(self):
        n = len(self.iterations)
        return max(30, n//3)

    def _check(self):
        window = self._window()
        drift_ok = True
        runaway = False
        for name, values in self.values.items():
            y = np.asarray(values, dtype=float)
            if len(y) < window or not np.isfinite(y[-window:]).all():
                drift_ok = False
                continue
            scale = self._scale(name, y)
            w = y[-window:]/scale
            k = np.arange(window, dtype=float)
            slope = np.polyfit(k, w, 1)[0]
            drift = abs(slope)*window
            band = float(np.max(np.abs(w - w.mean())))
            self.signals[name] = {'scale': scale, 'drift': float(drift), 'band': band,
                                  'mean': float(np.mean(y[-window:])), 'std': float(np.std(y[-window:])),
                                  'normalised_std': float(np.std(w))}
            if drift > self.s.tolerance:
                drift_ok = False
            if (w.max() - w.min()) > self.s.stability_factor:
                runaway = True
        self.runaway_checks = self.runaway_checks + 1 if runaway else 0
        if self.runaway_checks >= 3:
            self.state = 'runaway'
            self.stop_at = self.iterations[-1]
            return
        if self.state == 'searching':
            self.passes = self.passes + 1 if drift_ok else 0
            if self.passes >= 3:
                self.state = 'averaging'
                self.detected_at = self.iterations[-1]
        elif self.state == 'averaging' and self._averaging_settled():
            self.state = 'converged'
            self.stop_at = self.iterations[-1]

    def _averaging_settled(self, final=False):
        start = self._index(self.detected_at)
        length = len(self.iterations) - start
        if length >= self.s.averaging_max:
            return True
        if length < self.s.averaging_min and not final:
            return False
        middle = start + length//2
        for name, values in self.values.items():
            y = np.asarray(values, dtype=float)
            full = float(np.mean(y[start:]))
            half = float(np.mean(y[start:middle+1]))
            if abs(full - half)/self._scale(name, y) > self.s.averaging_tolerance:
                return final and length >= self.s.averaging_min
        return True

    def _index(self, iteration):
        if iteration is None:
            return 0
        return int(np.searchsorted(self.iterations, iteration))

    def window(self):
        """Averaging window (start, end) in iterations, or None before detection."""
        if self.detected_at is None:
            return None
        return (self.detected_at, self.iterations[-1])

    @property
    def should_stop(self):
        return self.state in ('converged', 'runaway')

    def summary(self, stop_by=None, final_iteration=None):
        window = self.window()
        averaging = None
        if window:
            start = self._index(window[0])
            averaging = {'start': window[0], 'end': window[1], 'samples': len(self.iterations) - start}
        if self.state == 'converged':
            message = (f'Convergence detected after {int(self.detected_at)} iterations; averaged over iterations '
                       f'{int(window[0])}–{int(window[1])}.')
        elif self.state == 'runaway':
            message = 'Monitored loads grew or oscillated beyond the stability limit; the solve was stopped for review.'
        elif self.state == 'averaging':
            message = (f'Convergence detected after {int(self.detected_at)} iterations, but the iteration budget ended '
                       'before the averaging window settled.')
        else:
            message = 'Monitored loads did not settle within the iteration budget; values are a trailing mean.'
        return {'schema': 'gustsim.convergence/1', 'state': self.state, 'detected_at': self.detected_at,
                'averaging': averaging, 'signals': self.signals,
                'parameters': self.s.model_dump(mode='json') if hasattr(self.s, 'model_dump') else dict(self.s),
                'stop': {'requested_at': self.stop_at, 'by': stop_by, 'solver_final_iteration': final_iteration},
                'message': message}


def averaged(iterations, values, window):
    """Mean and standard deviation of `values` over the window, else over the last 20%."""
    x = np.asarray(iterations, dtype=float)
    y = np.asarray(values, dtype=float)
    if not len(x):
        return None, None, 'none'
    if window:
        # An open end (None) means "to the last sample", e.g. an unfinished transient window.
        mask = (x >= window[0]) & (x <= (window[1] if window[1] is not None else np.inf))
        basis = 'window_mean'
    else:
        mask = x >= x[int(len(x)*0.8)] if len(x) > 5 else np.ones(len(x), bool)
        basis = 'trailing_mean'
    selected = y[mask] if mask.any() else y[-1:]
    return selected.mean(axis=0), selected.std(axis=0), basis


def floors(spec):
    """Normalisation floors per monitored signal (2% of the dynamic-pressure force scale)."""
    q = 0.5*spec.fluid.density*spec.flow.speed**2
    force = max(0.02*q*spec.references.area, 1e-9)
    return {'drag_n': force, 'lift_n': force, 'side_n': force}


def monitored(spec, row, rotor=None):
    """Signals the detector tracks for one force row (and optional rotor-load row)."""
    force = np.asarray(row['force'], dtype=float)
    kind = spec.use_case.kind if spec.use_case else ('propeller' if spec.rotation.enabled else 'aerodynamics')
    mode = spec.convergence.monitor
    signals = {}
    if kind == 'pipe':
        # Wall loads of a duct settle with its pressure drop; their magnitude is the signal.
        return {'force_n': float(np.linalg.norm(force))}
    if mode in ('auto', 'drag') and kind != 'propeller':
        signals['drag_n'] = float(force @ np.asarray(spec.references.drag_axis))
    if mode in ('auto', 'lift') and kind == 'aerodynamics':
        signals['lift_n'] = float(force @ np.asarray(spec.references.lift_axis))
    if rotor is not None and (mode in ('auto', 'thrust', 'torque') and kind == 'propeller'):
        if mode in ('auto', 'thrust'): signals['thrust_n'] = rotor['thrust_n']
        if mode in ('auto', 'torque'): signals['torque_nm'] = rotor['torque_nm']
    if not signals:
        signals['force_n'] = float(np.linalg.norm(force))
    return signals
