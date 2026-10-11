"""Force and moment breakdown in wind-tunnel and object frames.

Everything is averaged over the convergence window (else the trailing 20 % of iterations),
taken from OpenFOAM's own forces tables, which already separate pressure and viscous parts.
Coefficients exist only when a freestream speed and confirmed reference quantities exist.
"""
import itertools
import json
import numpy as np
from .convergence import averaged
from .quality import latest_table


def unit(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    if n < 1e-12:
        raise ValueError('Axis must be nonzero')
    return v/n


def tables(case, name):
    """Rows of iteration, total, pressure, viscous[, porous] force and the matching moments."""
    force = latest_table(case, f'postProcessing/{name}/*/force.dat')
    moment = {r[0]: r for r in latest_table(case, f'postProcessing/{name}/*/moment.dat')}
    rows = []
    for r in force:
        if len(r) < 10:
            continue
        m = moment.get(r[0])
        rows.append({'iteration': r[0], 'total': r[1:4], 'pressure': r[4:7], 'viscous': r[7:10],
                     'porous': r[10:13] if len(r) >= 13 else [0.0, 0.0, 0.0],
                     'moment': m[1:4] if m is not None and len(m) >= 4 else [0.0, 0.0, 0.0]})
    return rows


def window_of(case):
    # Transient runs average over the transient window, not the steady initialisation.
    transient = case/'results'/'transient.json'
    if transient.is_file():
        try:
            window = json.loads(transient.read_text(encoding='utf-8')).get('window')
            if window:
                return (window['start'], window['end'])
        except (ValueError, KeyError):
            pass
    path = case/'results'/'convergence.json'
    if path.is_file():
        try:
            averaging = json.loads(path.read_text(encoding='utf-8')).get('averaging')
            if averaging:
                return (averaging['start'], averaging['end'])
        except (ValueError, KeyError):
            pass
    return None


def mean_rows(rows, window):
    iterations = [r['iteration'] for r in rows]
    out, std = {}, {}
    for key in ('total', 'pressure', 'viscous', 'porous', 'moment'):
        mean, deviation, basis = averaged(iterations, [r[key] for r in rows], window)
        out[key] = np.asarray(mean, float)
        std[key] = np.asarray(deviation, float)
    samples = sum(1 for i in iterations if window and window[0] <= i <= window[1]) if window else max(1, len(rows) - int(len(rows)*0.8))
    return out, std, basis, samples


def frames(spec):
    drag = unit(spec.references.drag_axis)
    lift = unit(spec.references.lift_axis)
    lift = unit(lift - (lift@drag)*drag)
    side = np.cross(lift, drag)
    forward = unit(spec.references.object_forward) if spec.references.object_forward is not None else -drag
    up = unit(spec.references.object_up)
    up = unit(up - (up@forward)*forward) if abs(up@forward) < 0.99 else lift
    x = -forward
    y = np.cross(up, x)
    return {'wind': {'drag': drag, 'side': side, 'lift': lift},
            'object': {'x': x, 'y': y, 'z': up},
            'object_basis': 'user' if spec.references.object_forward is not None else 'travel_direction'}


def project(vector, axes):
    return {name: float(np.asarray(vector)@axis) for name, axis in axes.items()}


def axle_split(spec, total, moment, origin, axles, forward, up):
    """Front/rear lift from moment equilibrium about the rear axle.

    Two vertical axle loads reproduce the total lift and the moment of the aerodynamic loads
    about the rear axle: L_f = (M_R . (f x u)) / WB, L_r = L - L_f."""
    front, rear = (np.asarray(a, float) for a in axles)
    wheelbase = float((front - rear)@forward)
    if wheelbase <= 1e-9:
        return None
    moment_rear = np.asarray(moment) + np.cross(np.asarray(origin) - rear, np.asarray(total))
    lift = float(np.asarray(total)@up)
    lift_front = float(moment_rear@np.cross(forward, up))/wheelbase
    return lift_front, lift - lift_front, wheelbase


def domain_cross_section(spec, direction):
    extent = np.asarray(spec.domain.maximum, float) - np.asarray(spec.domain.minimum, float)
    d = np.abs(direction)
    return float(d[0]*extent[1]*extent[2] + d[1]*extent[0]*extent[2] + d[2]*extent[0]*extent[1])


def breakdown(case, spec, meta=None, frontal_area=None):
    rows = tables(case, 'loads')
    if not rows:
        return None
    window = window_of(case)
    mean, std, basis, samples = mean_rows(rows, window)
    axes = frames(spec)
    wind, obj = axes['wind'], axes['object']
    rho, speed = spec.fluid.density, spec.flow.speed
    q = 0.5*rho*speed**2
    area, length = spec.references.area, spec.references.length
    confirmed = not spec.use_case or spec.use_case.references_confirmed
    origin = np.asarray(spec.references.origin, float)

    def block(axes_set, moment_axes):
        return {'force_n': project(mean['total'], axes_set), 'pressure_n': project(mean['pressure'], axes_set),
                'viscous_n': project(mean['viscous'], axes_set), 'porous_n': project(mean['porous'], axes_set),
                'moment_nm': project(mean['moment'], moment_axes), 'std_n': project(std['total'], axes_set)}

    wind_block = block(wind, {'roll': wind['drag'], 'pitch': wind['side'], 'yaw': wind['lift']})
    object_block = block(obj, {'roll': obj['x'], 'pitch': obj['y'], 'yaw': obj['z']})
    drag, lift, side_force = (wind_block['force_n'][k] for k in ('drag', 'lift', 'side'))
    coefficients = {k: None for k in ('cd', 'cl', 'cs', 'cda_m2', 'cla_m2', 'l_over_d', 'cd_pressure', 'cd_viscous', 'cm_pitch',
                                      'cl_front', 'cl_rear')}
    unavailable = {}
    if q > 0:
        coefficients['cda_m2'] = drag/q
        coefficients['cla_m2'] = lift/q
    coefficients['l_over_d'] = lift/drag if abs(drag) > 1e-12 else None
    if q > 0 and confirmed:
        coefficients.update(cd=drag/(q*area), cl=lift/(q*area), cs=side_force/(q*area),
                            cd_pressure=wind_block['pressure_n']['drag']/(q*area), cd_viscous=wind_block['viscous_n']['drag']/(q*area),
                            cm_pitch=wind_block['moment_nm']['pitch']/(q*area*length))
    elif q <= 0:
        unavailable['cd'] = 'Coefficients are undefined without a freestream speed'
    else:
        unavailable['cd'] = 'Confirm reference area, length, speed and axes to calculate coefficients'
    forward, up = -obj['x'], obj['z']
    axles, split_basis = None, None
    if spec.references.front_axle is not None and spec.references.rear_axle is not None:
        axles, split_basis = (spec.references.front_axle, spec.references.rear_axle), 'axles'
    elif meta is not None:
        low, high = meta['bounds']
        corners = np.array(list(itertools.product(*zip(low, high))), float)
        center = np.mean(meta['bounds'], axis=0)
        along = corners@forward
        axles = (center + forward*(along.max() - center@forward), center + forward*(along.min() - center@forward))
        split_basis = 'bbox_ends'
    split = axle_split(spec, mean['total'], mean['moment'], origin, axles, forward, up) if axles is not None else None
    lift_split = None
    if split is not None:
        lift_front, lift_rear, wheelbase = split
        lift_split = {'front_n': lift_front, 'rear_n': lift_rear, 'wheelbase_m': wheelbase, 'basis': split_basis}
        if q > 0 and confirmed:
            coefficients.update(cl_front=lift_front/(q*area), cl_rear=lift_rear/(q*area))
    blockage = None
    if spec.mode != 'internal' and frontal_area:
        ratio = frontal_area/domain_cross_section(spec, wind['drag'])
        blockage = {'ratio': ratio, 'method': 'continuity (1 - B)^2 on coefficients; uncorrected values are primary',
                    'cd_corrected': coefficients['cd']*(1-ratio)**2 if coefficients['cd'] is not None else None,
                    'cl_corrected': coefficients['cl']*(1-ratio)**2 if coefficients['cl'] is not None else None}
    parts = []
    total_drag = 0.0
    groups = []
    path = case/'force_groups.json'
    if path.is_file():
        groups = json.loads(path.read_text(encoding='utf-8'))
    part_names = {}
    if meta is not None:
        parents = {p.get('parent_id') for p in meta.get('parts', [])}
        leaves = [p for p in meta.get('parts', []) if p['id'] not in parents]
        for index, part in enumerate(leaves):
            part_names[f'part_{index}'] = part['name']
    for group in groups:
        group_rows = tables(case, 'group_'+group['name'])
        if not group_rows:
            continue
        group_mean, _, _, _ = mean_rows(group_rows, window)
        w = project(group_mean['total'], wind)
        entry = {'name': part_names.get(group['name'], group['name']), 'group': group['name'], 'patches': group['patches'],
                 'wind': {'drag_n': w['drag'], 'lift_n': w['lift'], 'side_n': w['side'],
                          'pressure_drag_n': float(group_mean['pressure']@wind['drag']), 'viscous_drag_n': float(group_mean['viscous']@wind['drag'])},
                 'object': project(group_mean['total'], obj),
                 'cd_contribution': w['drag']/(q*area) if q > 0 and confirmed else None,
                 'share_of_drag': w['drag']/drag if abs(drag) > 1e-12 else None}
        total_drag += w['drag']
        parts.append(entry)
    parts_check = None
    if parts:
        residual = abs(total_drag - drag)/max(abs(drag), 1e-12)
        parts_check = {'residual_fraction': residual, 'ok': residual <= 0.005,
                       'note': 'Parts not assigned to a group are absent; the sum then differs from the total.'}
    curve = None
    if speed > 0 and coefficients['cda_m2'] is not None:
        limit = 0.3*spec.fluid.speed_of_sound if spec.fluid.name == 'air' else float('inf')
        speeds = [v for v in np.linspace(0.25, 2.0, 15)*speed if v < limit]
        curve = {'assumption': 'Constant drag area (Reynolds-number effects neglected); extrapolated, not simulated at these speeds',
                 'points': [{'speed_m_s': float(v), 'drag_n': 0.5*rho*v*v*coefficients['cda_m2'],
                             'power_w': 0.5*rho*v**3*coefficients['cda_m2']} for v in speeds], 'measured': []}
    last = rows[-1]
    return {'schema': 'gustsim.forces/1',
            'averaging': {'method': basis, 'start': window[0] if window else None, 'end': window[1] if window else None,
                          'samples': samples, 'basis': 'force_convergence' if window else 'trailing_mean'},
            'frames': {'wind': {'axes': {k: v.tolist() for k, v in wind.items()}, 'total': wind_block},
                       'object': {'axes': {k: v.tolist() for k, v in obj.items()}, 'total': object_block, 'basis': axes['object_basis']}},
            'coefficients': coefficients, 'lift_split': lift_split, 'blockage': blockage,
            'reference': {'area_m2': area, 'length_m': length, 'origin': origin.tolist(), 'dynamic_pressure_pa': q, 'speed_m_s': speed,
                          'density': rho, 'confirmed': confirmed},
            'frontal_area_m2': frontal_area, 'parts': parts, 'parts_sum_check': parts_check, 'speed_curve': curve,
            'last_iteration': {'iteration': last['iteration'], 'force_n': project(last['total'], wind)},
            'unavailable': unavailable}
