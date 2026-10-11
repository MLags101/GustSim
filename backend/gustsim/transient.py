"""Transient incompressible flow (pimpleFoam) continuing from the steady solution.

A transient run first solves steadily (with the usual convergence monitor), then the same case
switches to pimpleFoam: backward time differencing, PIMPLE in PISO mode with an adaptive time
step bounded by the Courant number, time averaging (fieldAverage) over the averaging window, and
animation frames from a surfaces function object. OpenFOAM time continues from the last steady
iteration, so physical time is `t - start` (recorded in results/transient_plan.json).

Statistics (results/transient.json) are computed from the force history: window means and
standard deviations, a Welch power spectrum of the lift/side coefficients on a uniformly
resampled signal, the dominant frequency and Strouhal number, and statistical convergence
(the means of the two halves of the window agree). Experimental capability; see the
cylinder_re100 and square_cylinder gates.
"""
import json
import math
from pathlib import Path
import numpy as np

PLAN = 'results/transient_plan.json'
STATS = 'results/transient.json'
FRAMES = 'frames'
SETTLE_TOLERANCE = 0.01      # half-window means within 1 % of the mean (or of the floor)
MIN_PERIODS = 5              # periodic signals need this many cycles in the averaging window


def vector(v):
    return '(' + ' '.join(f'{x:.12g}' for x in v) + ')'


def domain_length(spec):
    direction = np.asarray(spec.flow.velocity(), float)
    direction = direction/max(np.linalg.norm(direction), 1e-12)
    extent = np.asarray(spec.domain.maximum, float) - np.asarray(spec.domain.minimum, float)
    return float(np.abs(direction)@extent)


def smallest_cell(spec):
    from .validation import background_cell_size
    h0 = background_cell_size(spec) or 1.0
    return h0/2**max(spec.mesh.surface_level, spec.mesh.body_level or 0)


def plan(spec, start):
    """Times (in OpenFOAM time, i.e. offset by the steady iterations) for the transient stage."""
    speed = spec.flow.speed
    if speed <= 0:
        raise ValueError('A transient simulation needs a nonzero flow speed')
    time = spec.time
    duration = time.duration_s or time.flow_throughs*domain_length(spec)/speed
    h = smallest_cell(spec)
    first_step = min(0.5*time.max_courant*h/speed, duration/200)
    average_start = start + time.average_after_fraction*duration
    frames = time.frames
    frame_interval = (start + duration - average_start)/frames if frames else None
    return {'start': float(start), 'end': float(start + duration), 'duration_s': float(duration),
            'first_step_s': float(first_step), 'max_courant': time.max_courant, 'max_step_s': float(duration/100),
            'average_start': float(average_start), 'frames': frames, 'frame_interval_s': frame_interval,
            'reference_length_m': spec.references.length, 'speed_m_s': speed,
            'flow_throughs': duration*speed/domain_length(spec), 'convective_times': duration*speed/spec.references.length,
            'time_axis': 'OpenFOAM time continues from the last steady iteration; physical time is t - start'}


def write_schemes(spec, folder):
    from .foam import write
    turbulent = 'div(phi,k) bounded Gauss limitedLinear 1; div(phi,omega) bounded Gauss limitedLinear 1;' if spec.flow.turbulence != 'laminar' else ''
    write(folder, 'system/fvSchemes',
          'ddtSchemes { default backward; }\n'
          'gradSchemes { default Gauss linear; grad(U) cellLimited Gauss linear 1; }\n'
          f'divSchemes {{ default none; div(phi,U) Gauss linearUpwind grad(U); {turbulent} div((nuEff*dev2(T(grad(U))))) Gauss linear; }}\n'
          'laplacianSchemes { default Gauss linear limited 0.5; }\ninterpolationSchemes { default linear; }\n'
          'snGradSchemes { default limited 0.5; }\nwallDist { method meshWave; }')


def write_solution(spec, folder):
    from .foam import write
    write(folder, 'system/fvSolution',
          'solvers { p { solver GAMG; tolerance 1e-7; relTol 0.01; smoother GaussSeidel; } '
          'pFinal { solver GAMG; tolerance 1e-7; relTol 0; smoother GaussSeidel; } '
          '"(U|k|omega)" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0.1; } '
          '"(U|k|omega)Final" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0; } }\n'
          'PIMPLE { nOuterCorrectors 1; nCorrectors 2; nNonOrthogonalCorrectors 1; consistent no; }\n'
          'relaxationFactors { equations { ".*" 1; } }')


def frame_function(spec, wall_patches, times, point):
    """Animation frames: the object surface (pressure) and a section through the model
    containing the flow and lift directions (velocity)."""
    if not times['frames']:
        return ''
    side = np.cross(np.asarray(spec.references.lift_axis, float), np.asarray(spec.references.drag_axis, float))
    surfaces = ''
    if wall_patches:
        surfaces += f'object {{ type patch; patches ({" ".join(wall_patches)}); interpolate false; triangulate true; }} '
    surfaces += f'section {{ type cuttingPlane; planeType pointAndNormal; pointAndNormalDict {{ point {vector(point)}; normal {vector(side)}; }} interpolate false; triangulate true; }}'
    return (f'\n{FRAMES} {{ type surfaces; libs (sampling); surfaceFormat vtk; formatOptions {{ vtk {{ format binary; legacy false; }} }} '
            f'fields (p U); writeControl adjustableRunTime; writeInterval {times["frame_interval_s"]:.12g}; '
            f'timeStart {times["average_start"]:.12g}; surfaces {{ {surfaces} }} errors warn; }}')


def average_function(times):
    return (f'\ntimeAverage {{ type fieldAverage; libs (fieldFunctionObjects); timeStart {times["average_start"]:.12g}; '
            'restartOnRestart false; restartOnOutput false; writeControl writeTime; '
            'fields ( U { mean on; prime2Mean on; base time; } p { mean on; prime2Mean on; base time; } ); }')


def write_control(spec, folder, times, model_center=None):
    """controlDict for the transient stage: the steady stage's function objects (forces, outputs,
    residuals) plus time averaging and animation frames, continuing from the latest time."""
    from .foam import write
    functions = json.loads((folder/'function_objects.json').read_text(encoding='utf-8'))['functions']
    wall_patches = [b.patch for b in spec.boundaries if b.kind in ('wall', 'moving_wall', 'rotating_wall')]
    if model_center is None:
        model_center = np.mean(np.asarray([spec.domain.minimum, spec.domain.maximum], float), axis=0)
    functions += average_function(times) + frame_function(spec, wall_patches, times, model_center)
    full_writes = 4
    write(folder, 'system/controlDict', f'''application pimpleFoam;
startFrom latestTime; startTime 0; stopAt endTime; endTime {times["end"]:.12g}; deltaT {times["first_step_s"]:.12g};
adjustTimeStep yes; maxCo {times["max_courant"]:.12g}; maxDeltaT {times["max_step_s"]:.12g};
writeControl adjustableRunTime; writeInterval {times["duration_s"]/full_writes:.12g}; purgeWrite 0;
writeFormat binary; writePrecision 10; writeCompression off; timeFormat general; timePrecision 10;
runTimeModifiable false;
functions {{ {functions} }}''')
    (folder/PLAN).parent.mkdir(parents=True, exist_ok=True)
    (folder/PLAN).write_text(json.dumps(times, indent=2), encoding='utf-8')


def release_steady_time_state(folder, start):
    """On restart OpenFOAM restores <time>/uniform/time: the steady pseudo-time step (1) and
    iteration index. With a nonzero index pimpleFoam skips its Courant-based initial step, so the
    first transient step would be 1 s long (or the run ends at once when the duration is under
    half a second). The steady state is set aside as uniform/time.steady (kept, not deleted) in
    the case and every processor directory, so the transient starts at index 0 with the
    controlDict time step."""
    for root in [Path(folder), *sorted(Path(folder).glob('processor*'))]:
        for directory in root.iterdir():
            try:
                if not directory.is_dir() or abs(float(directory.name) - start) > 1e-9*max(1.0, abs(start)):
                    continue
            except ValueError:
                continue
            state = directory/'uniform'/'time'
            if state.is_file():
                state.replace(state.with_name('time.steady'))


def prepare(spec, folder, start, model_center=None):
    release_steady_time_state(folder, start)
    times = plan(spec, start)
    write_schemes(spec, folder)
    write_solution(spec, folder)
    write_control(spec, folder, times, model_center)
    return times


# ---- statistics ------------------------------------------------------------------------------
def welch(signal, dt, segments=4):
    """One-sided Welch power spectral density with Hann windows and 50 % overlap."""
    n = len(signal)
    length = max(16, int(2*n/(segments + 1)))
    if n < length:
        length = n
    step = max(1, length//2)
    window = np.hanning(length)
    scale = 1.0/(np.sum(window**2)/dt)
    spectra = []
    for begin in range(0, n - length + 1, step):
        piece = signal[begin:begin + length]
        piece = (piece - piece.mean())*window
        spectra.append(np.abs(np.fft.rfft(piece))**2*scale)
    psd = np.mean(spectra, axis=0)
    psd[1:-1] *= 2
    return np.fft.rfftfreq(length, dt), psd


def coefficient_series(case, spec, times):
    from .quality import force_history
    rows = [r for r in force_history(case) if r['iteration'] >= times['start'] - 1e-12]
    if len(rows) < 8:
        return None
    t = np.asarray([r['iteration'] for r in rows], float) - times['start']
    force = np.asarray([r['force'] for r in rows], float)
    q_area = 0.5*spec.fluid.density*spec.flow.speed**2*spec.references.area
    drag = np.asarray(spec.references.drag_axis, float)
    lift = np.asarray(spec.references.lift_axis, float)
    side = np.cross(lift, drag)
    order = np.argsort(t, kind='stable')
    t, force = t[order], force[order]
    keep = np.concatenate([[True], np.diff(t) > 0])
    t, force = t[keep], force[keep]
    return {'t': t, 'drag_n': force@drag, 'lift_n': force@lift, 'side_n': force@side, 'q_area': q_area}


def analyse(case, spec):
    """results/transient.json from the force history after the steady stage."""
    case = Path(case)
    times = json.loads((case/PLAN).read_text(encoding='utf-8'))
    series = coefficient_series(case, spec, times)
    result = {'schema': 'gustsim.transient/1', 'plan': times, 'state': 'unavailable', 'message': 'No transient force history was recorded'}
    if series is None:
        (case/STATS).write_text(json.dumps(result, indent=2), encoding='utf-8')
        return result
    t = series['t']
    window = t >= times['average_start'] - times['start']
    reached = float(t[-1])
    result.update(duration_reached_s=reached, samples=int(len(t)))
    if window.sum() < 8:
        result.update(state='incomplete', message=f'The run ended at {reached:.4g} s, before enough of the averaging window was recorded')
        (case/STATS).write_text(json.dumps(result, indent=2), encoding='utf-8')
        return result
    tw = t[window]
    # Adaptive time steps: resample on a uniform grid at the median step.
    dt = float(np.median(np.diff(tw)))
    grid = np.arange(tw[0], tw[-1], dt)
    q_area = series['q_area']
    floor = 0.02*q_area if q_area > 0 else 1e-9
    stats, spectra = {}, {}
    for key in ('drag_n', 'lift_n', 'side_n'):
        values = series[key][window]
        mean, std = float(values.mean()), float(values.std())
        half = len(values)//2
        first, second = float(values[:half].mean()), float(values[half:].mean())
        stats[key] = {'mean': mean, 'std': std, 'rms_fluctuation': std, 'min': float(values.min()), 'max': float(values.max()),
                      'half_window_change': abs(second - first)/max(abs(mean), floor)}
        uniform = np.interp(grid, tw, values)
        if len(uniform) >= 32:
            freq, psd = welch(uniform, dt)
            spectra[key] = {'frequency_hz': freq.tolist(), 'psd': psd.tolist()}
    if q_area > 0:
        for key, name in (('drag_n', 'cd'), ('lift_n', 'cl'), ('side_n', 'cs')):
            stats[name] = {k: (v/q_area if k != 'half_window_change' else v) for k, v in stats[key].items()}
    # Dominant shedding frequency: the largest PSD peak of the cross-flow force (lift or side,
    # whichever fluctuates more), ignoring the zero-frequency bin.
    cross = 'lift_n' if stats['lift_n']['std'] >= stats['side_n']['std'] else 'side_n'
    frequency = strouhal = periods = None
    if cross in spectra and stats[cross]['std'] > 1e-6*max(abs(stats['drag_n']['mean']), floor):
        freq, psd = np.asarray(spectra[cross]['frequency_hz']), np.asarray(spectra[cross]['psd'])
        if len(freq) > 2:
            peak = int(np.argmax(psd[1:])) + 1
            frequency = float(freq[peak])
            strouhal = frequency*spec.references.length/spec.flow.speed
            periods = frequency*(tw[-1] - tw[0])
    settled = all(stats[k]['half_window_change'] <= SETTLE_TOLERANCE for k in ('drag_n', 'lift_n'))
    enough = periods is None or periods >= MIN_PERIODS
    complete = reached >= times['duration_s']*(1 - 1e-6)
    if settled and enough and complete:
        state, message = 'converged', 'Time-averaged loads settled: the two halves of the averaging window agree within 1 %'
    elif not complete:
        state, message = 'incomplete', f'The run stopped at {reached:.4g} s of {times["duration_s"]:.4g} s'
    elif not enough:
        state, message = 'review', f'Only {periods:.1f} shedding periods in the averaging window; at least {MIN_PERIODS} are needed for stable statistics'
    else:
        state, message = 'review', 'Time-averaged loads still drift between the two halves of the averaging window; run longer'
    result.update(state=state, message=message, window={'start': float(tw[0]) + times['start'], 'end': float(tw[-1]) + times['start'],
                  'start_s': float(tw[0]), 'end_s': float(tw[-1])},
                  statistics=stats, spectra=spectra, cross_flow=cross, dominant_frequency_hz=frequency, strouhal=strouhal,
                  periods_in_window=periods, sample_step_s=dt,
                  history={'t_s': t.tolist(), 'drag_n': series['drag_n'].tolist(), 'lift_n': series['lift_n'].tolist(),
                           'side_n': series['side_n'].tolist()},
                  notes={'strouhal': 'St = f L / U with the reference length and freestream speed',
                         'basis': 'Loads are averaged over the window; fluctuations are standard deviations'})
    (case/STATS).write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def max_courant(log_text):
    import re
    values = re.findall(r'Courant Number mean: [-+0-9.eE]+ max: ([-+0-9.eE]+)', log_text)
    return max((float(v) for v in values), default=None)
