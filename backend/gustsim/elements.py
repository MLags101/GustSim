"""Element results: rotating zones, wheels, porous zones and openings, from measured run evidence."""
import math
import numpy as np
from .foam_elements import (POROUS_CELLS_ACROSS, effective_boundaries, porous_effective_thickness, porous_fit,
                            porous_level, wheel_rotation, zone_names)


def _mean(case, name, columns, window):
    """Window mean of volFieldValue columns (rows: time, U x y z, p)."""
    from .quality import latest_table
    from .convergence import averaged
    rows = [r for r in latest_table(case, f'postProcessing/{name}/*/volFieldValue.dat') if len(r) >= columns.stop]
    if not rows:
        return None
    values = [r[columns] if columns.stop - columns.start > 1 else r[columns.start] for r in rows]
    mean, _, _ = averaged([r[0] for r in rows], values, window)
    return mean


def _forces(case, name, window):
    from .forces import mean_rows, tables
    rows = tables(case, name)
    if not rows:
        return None
    mean, _, _, _ = mean_rows(rows, window)
    return mean


def efficiency(thrust, advance, power):
    """Propulsive efficiency T·V/P, only where it is physically meaningful (0 < η <= 1)."""
    if not (power > 0 and advance > 0 and thrust > 0):
        return None
    value = thrust*advance/power
    return value if value <= 1 else None


def build(spec, result, case=None):
    from .forces import window_of
    window = window_of(case) if case is not None else None
    metrics = result.get('metrics') or {}
    zones = []
    velocity = np.asarray(spec.flow.velocity(), dtype=float)
    for zone in spec.rotating_zones():
        entry = {'name': zone.name, 'kind': zone.kind, 'rpm': zone.rpm}
        if zone.name == 'rotor':
            entry.update({k: metrics.get(k) for k in ('thrust_n', 'torque_nm', 'fluid_torque_nm', 'shaft_power_w', 'advance_ratio', 'ct', 'cq', 'efficiency')})
        elif case is not None:
            loads = _forces(case, zone_names(zone)[3], window)
            if loads is not None:
                axis = np.asarray(zone.axis)
                moment = loads['moment'] - np.cross(np.asarray(zone.origin) - np.asarray(spec.references.origin), loads['total'])
                fluid = float(moment@axis)
                driving = -fluid*np.sign(zone.rpm)
                n = abs(zone.rpm)/60
                diameter = 2*zone.blade_radius
                thrust = float(loads['total']@(-axis))
                power = driving*2*math.pi*n
                advance = float(-velocity@(-axis))
                entry.update(thrust_n=thrust, fluid_torque_nm=fluid, torque_nm=driving, shaft_power_w=power,
                             ct=thrust/(spec.fluid.density*n*n*diameter**4) if n else None,
                             cq=driving/(spec.fluid.density*n*n*diameter**5) if n else None,
                             advance_ratio=advance/(n*diameter) if n else None,
                             efficiency=efficiency(thrust, advance, power))
        zones.append(entry)
    wheels = []
    for wheel in spec.elements.wheels:
        entry = {'name': wheel.name, 'rotating': wheel.rotating, 'radius_m': wheel.radius}
        if wheel.rotating:
            axis, rpm = wheel_rotation(spec, wheel)
            entry.update(rpm=rpm, omega_rad_s=rpm*math.pi/30, axis=axis.tolist())
        loads = _forces(case, f'wheel_{wheel.name}', window) if case is not None else None
        if loads is not None:
            drag = np.asarray(spec.references.drag_axis); lift = np.asarray(spec.references.lift_axis)
            entry.update(drag_n=float(loads['total']@drag), lift_n=float(loads['total']@lift))
        wheels.append(entry)
    porous = []
    for zone in spec.elements.porous:
        thickness, layers, basis = porous_effective_thickness(spec, zone, porous_level(spec, zone, spec.mesh.surface_level))
        fit = porous_fit(zone.curve, spec.fluid.dynamic_viscosity, spec.fluid.density, thickness)
        entry = {'name': zone.name, 'coefficients': {'d': fit['d'], 'f': fit['f'], 'curve': [list(p) for p in zone.curve]},
                 'effective_thickness_m': thickness, 'cell_layers': layers, 'thickness_basis': basis,
                 'resolution': None if layers is None or layers >= POROUS_CELLS_ACROSS else
                 f'Only {layers} cell layers across the radiator; the pressure drop is over-predicted by roughly 10-25 % at this resolution'}
        if case is not None:
            u = _mean(case, f'zone_{zone.name}', slice(1, 4), window)
            up = _mean(case, f'zone_{zone.name}_up', slice(4, 5), window)
            down = _mean(case, f'zone_{zone.name}_down', slice(4, 5), window)
            if u is not None:
                axial = float(np.asarray(u)@np.asarray(zone.axis))
                area = zone.width_m*zone.height_m
                entry.update(mean_velocity_m_s=axial, volume_flow_m3_s=axial*area, mass_flow_kg_s=axial*area*spec.fluid.density,
                             curve_pressure_drop_pa=fit['a']*abs(axial) + fit['b']*axial*axial)
            if up is not None and down is not None:
                entry['pressure_drop_pa'] = float((up - down)*spec.fluid.density)
        porous.append(entry)
    openings = []
    fluxes = result.get('boundary_flux_m3s') or {}
    pressures = result.get('boundary_pressure_pa') or {}
    from .validation import DOMAIN_PATCHES
    # Wind-tunnel faces are not openings of the model; kinds are reported as compiled.
    for boundary in effective_boundaries(spec):
        if boundary.patch in DOMAIN_PATCHES and spec.mode != 'internal':
            continue
        if boundary.kind in ('wall', 'moving_wall', 'rotating_wall', 'slip', 'symmetry'):
            continue
        if boundary.patch in fluxes or boundary.patch in pressures:
            flux = fluxes.get(boundary.patch)
            openings.append({'patch': boundary.patch, 'kind': boundary.kind, 'volume_flow_m3_s': flux,
                             'mass_flow_kg_s': flux*spec.fluid.density if flux is not None else None,
                             'mean_pressure_pa': pressures.get(boundary.patch)})
    return {'schema': 'gustsim.elements/1', 'rotating_zones': zones, 'wheels': wheels, 'porous': porous, 'openings': openings,
            'notes': {'flux_sign': 'Positive volume flow leaves the domain through the boundary',
                      'porous': 'Measured pressure drop is the difference of zone-averaged pressures just upstream and downstream; the curve value at the measured mean velocity is shown for comparison'}}
