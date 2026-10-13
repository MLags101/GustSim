"""Case dictionaries for spec v2 elements: rotating zones, wheels with a moving ground,
porous (Darcy–Forchheimer) zones and flow openings.

The legacy single rotating region compiles exactly as before (zone 'rotor', cellZone 'rotor',
refinement 'rotorRegion', forces 'rotorLoads'); goldens enforce it.
"""
import math
import numpy as np


def vector(v):
    return "(" + " ".join(f"{x:.12g}" for x in v) + ")"


def unit(v):
    a = np.asarray(v, dtype=float)
    n = np.linalg.norm(a)
    if n < 1e-12:
        raise ValueError('Direction must be nonzero')
    return a/n


# ---- ground and wheels -----------------------------------------------------------------
GROUND_KIND = {'moving': 'moving_wall', 'static': 'wall', 'slip': 'slip'}


def wheel_rotation(spec, wheel):
    """Axis and signed RPM of a wheel rolling on the moving ground (belt).

    The wheel spins about its own axle `a` (made horizontal). Wall velocity is ω a × (x - origin);
    at the contact point x - origin = -r·up, so the contact moves at -ω r (a × up). Choosing
    ω = -V·(a × up)/r makes it move with the component of the belt velocity V along the rolling
    direction. With the flow along the rolling direction (no yaw) that is exactly V; with yaw the
    remaining lateral belt component slips under the tyre, as on a yawed rolling-road model."""
    velocity = np.asarray(spec.flow.velocity(), dtype=float)
    up = unit(spec.references.lift_axis)
    axle = np.asarray(wheel.axis, dtype=float)
    axle = axle - (axle@up)*up
    horizontal = velocity - (velocity@up)*up
    if np.linalg.norm(axle) < 1e-6 or float(np.linalg.norm(horizontal)) < 1e-12:
        # A vertical axle cannot roll (validation rejects it); still air turns nothing.
        return unit(wheel.axis), 0.0
    axis = unit(axle)
    omega = -float(horizontal@np.cross(axis, up))/wheel.radius
    return axis, omega*30/math.pi


def opening_normal_velocity(spec, meta, mesh, groups, patch, speed):
    """Velocity vector for an inflow opening: into the fluid along the patch normal."""
    group = next(p['group'] for p in meta['patches'] if p['name'] == patch)
    mask = groups == group
    normal = np.sum(mesh.face_normals[mask]*mesh.area_faces[mask, None], axis=0)
    normal = unit(normal)
    # Body normals point out of the solid. External flow: the fluid is outside the body, so
    # inflow into the domain is along +n. Internal flow: the fluid is inside, so it is -n.
    direction = -normal if spec.mode == 'internal' else normal
    return tuple(direction*speed)


def effective_boundaries(spec, meta=None, mesh=None, groups=None):
    """Boundaries as compiled after applying the environment and elements."""
    wheels = {p: w for w in spec.elements.wheels if w.rotating for p in w.patches}
    openings = {o.patch: o for o in spec.elements.openings}
    out = []
    for b in spec.boundaries:
        if b.patch == 'ground' and spec.environment.ground != 'none':
            b = b.model_copy(update={'kind': GROUND_KIND[spec.environment.ground], 'velocity': None})
        elif b.patch in wheels:
            axis, rpm = wheel_rotation(spec, wheels[b.patch])
            b = b.model_copy(update={'kind': 'rotating_wall', 'origin': wheels[b.patch].center, 'axis': tuple(axis), 'rpm': rpm})
        elif b.patch in openings:
            o = openings[b.patch]
            if o.kind == 'inflow_velocity':
                velocity = opening_normal_velocity(spec, meta, mesh, groups, b.patch, o.value) if meta is not None else None
                b = b.model_copy(update={'kind': 'velocity_inlet', 'velocity': velocity})
            elif o.kind == 'inflow_flow_rate':
                b = b.model_copy(update={'kind': 'flow_inlet', 'flow_rate': o.value})
            elif o.kind == 'total_pressure':
                b = b.model_copy(update={'kind': 'pressure_inlet', 'pressure_pa': o.value})
            elif o.kind == 'outflow_pressure':
                b = b.model_copy(update={'kind': 'pressure_outlet', 'pressure_pa': o.value})
            else:
                b = b.model_copy(update={'kind': 'flow_outlet', 'flow_rate': o.value})
        out.append(b)
    return out


# ---- rotating zones ----------------------------------------------------------------------
def zone_names(zone):
    if zone.name == 'rotor':
        return 'rotorRegion', 'rotorCells', 'rotor', 'rotorLoads'
    return f'{zone.name}Region', f'{zone.name}Cells', zone.name, f'rotorLoads_{zone.name}'


def zone_cylinder(zone):
    axis = np.asarray(zone.axis, dtype=float)
    a = np.asarray(zone.origin) - axis*zone.length/2
    b = np.asarray(zone.origin) + axis*zone.length/2
    return a, b


def zones(spec, boundaries, refinement_level):
    """(geometry entries, refinement entries, topoSet actions, MRFProperties entries)."""
    geo, refine, actions, mrf = [], [], [], []
    for zone in spec.rotating_zones():
        region, cells, cell_zone, _ = zone_names(zone)
        a, b = zone_cylinder(zone)
        geo.append(f"{region} {{ type searchableCylinder; point1 {vector(a)}; point2 {vector(b)}; radius {zone.radius}; }}")
        refine.append(f"{region} {{ mode inside; levels ((1e15 {refinement_level})); }}")
        actions.append(f"{{name {cells};type cellSet;action new;source cylinderToCell;point1 {vector(a)};point2 {vector(b)};radius {zone.radius};}}")
        actions.append(f"{{name {cell_zone};type cellZoneSet;action new;source setToCellZone;set {cells};}}")
        nonrotating = [b.patch for b in boundaries if b.patch not in zone.patches]
        mrf.append(f"{cell_zone} {{ active yes; cellZone {cell_zone}; nonRotatingPatches ({' '.join(nonrotating)}); origin {vector(zone.origin)}; axis {vector(zone.axis)}; omega {zone.rpm*math.pi/30}; }}")
    return geo, refine, actions, mrf


# ---- porous zones ------------------------------------------------------------------------
def porous_fit(curve, dynamic_viscosity, density, thickness):
    """Darcy–Forchheimer coefficients from two (velocity, pressure drop) points.

    Δp = a·v + b·v² through a layer of thickness L; the source term per unit volume is
    (μ d + ½ ρ f |U|) U, so d = a / (μ L) and f = 2 b / (ρ L)."""
    (v1, p1), (v2, p2) = curve
    matrix = np.array([[v1, v1*v1], [v2, v2*v2]], dtype=float)
    a, b = np.linalg.solve(matrix, np.array([p1, p2], dtype=float))
    if a < 0 or b < 0:
        raise ValueError('These two points imply a negative linear or quadratic loss; check the curve '
                         '(a single point through the origin can be fitted as purely quadratic)')
    return {'a': float(a), 'b': float(b), 'd': float(a/(dynamic_viscosity*thickness)), 'f': float(2*b/(density*thickness))}


def porous_frame(zone):
    axis = unit(zone.axis)
    up = np.asarray(zone.up, dtype=float)
    up = unit(up - (up@axis)*axis)
    side = np.cross(up, axis)
    return axis, up, side


def porous_box(zone, offset=0.0, depth=None):
    """rotatedBoxToCell origin and edge vectors; offset shifts along the flow axis."""
    axis, up, side = porous_frame(zone)
    depth = zone.thickness_m if depth is None else depth
    center = np.asarray(zone.center, dtype=float) + axis*offset
    i, j, k = axis*depth, side*zone.width_m, up*zone.height_m
    origin = center - (i + j + k)/2
    return origin, i, j, k


# A sharp porous zone on OpenFOAM's collocated grid over-predicts its pressure jump by a
# discretisation error that falls roughly with the square of the cell layers across it. Measured
# in a uniform (slip-wall) duct on the pinned v2606 image with a linear curve: 6 layers +21 %,
# 11 layers +8 %, 21 layers +2.4 % (scripts/smoke_linux.py --porous --mode internal). Eight layers
# keep the error near 10 % at moderate cost; the porous_duct gate needs a mesh study.
POROUS_CELLS_ACROSS = 8
POROUS_MAX_EXTRA_LEVELS = 4


def porous_level(spec, zone, surface_level):
    """Refinement level giving at least POROUS_CELLS_ACROSS cells through the radiator."""
    from .validation import background_cell_size
    h0 = background_cell_size(spec)
    if not h0:
        return surface_level
    # h <= L/(N+1) guarantees at least N cell centres inside the box whatever its alignment.
    wanted = math.ceil(math.log2(max(h0*(POROUS_CELLS_ACROSS + 1)/zone.thickness_m, 1)))
    return int(min(max(wanted, surface_level), surface_level + POROUS_MAX_EXTRA_LEVELS, 10))


def porous_effective_thickness(spec, zone, level):
    """(thickness, cell layers or None, basis): the thickness the cellZone will actually have.

    The octree keeps blockMesh's grid, so at refinement `level` cell centres along an axis sit at
    low + (i + 1/2)h. For a radiator whose flow axis is a coordinate axis, the centres inside the
    box are counted exactly; otherwise the nominal thickness is used.
    """
    axis = np.asarray(porous_frame(zone)[0], dtype=float)
    k = int(np.argmax(np.abs(axis)))
    if abs(axis[k]) < 1 - 1e-9:
        return zone.thickness_m, None, 'nominal (radiator not aligned with a coordinate axis)'
    low = np.asarray(spec.domain.minimum, dtype=float)
    extent = np.asarray(spec.domain.maximum, dtype=float) - low
    counts = np.maximum(4, np.ceil(spec.mesh.base_cells*extent/max(extent))).astype(int)
    h = float(extent[k]/counts[k])/2**level
    start = (zone.center[k] - zone.thickness_m/2 - low[k])/h - 0.5
    stop = (zone.center[k] + zone.thickness_m/2 - low[k])/h - 0.5
    layers = math.floor(stop + 1e-9) - math.ceil(start - 1e-9) + 1
    if layers < 1:
        return zone.thickness_m, 0, 'nominal (thinner than one cell)'
    return layers*h, layers, f'{layers} cell layers of {h:.6g} m'


def porous(spec, refinement_level):
    """(geometry, refinement, topoSet actions, fvOptions entries, function objects)."""
    geo, refine, actions, options, functions = [], [], [], [], ''
    for zone in spec.elements.porous:
        level = porous_level(spec, zone, refinement_level)
        # Resistance per unit volume is sized for the thickness the zone really has, so the
        # pressure drop across it follows the measured curve.
        thickness, _, basis = porous_effective_thickness(spec, zone, level)
        fit = porous_fit(zone.curve, spec.fluid.dynamic_viscosity, spec.fluid.density, thickness)
        axis, up, _ = porous_frame(zone)
        name = f'porous_{zone.name}'
        origin, i, j, k = porous_box(zone)
        corners = [origin + i*a + j*b + k*c for a in (0, 1) for b in (0, 1) for c in (0, 1)]
        margin = zone.thickness_m
        low = np.min(corners, axis=0) - margin
        high = np.max(corners, axis=0) + margin
        geo.append(f"{name}Box {{ type searchableBox; min {vector(low)}; max {vector(high)}; }}")
        refine.append(f"{name}Box {{ mode inside; levels ((1e15 {level})); }}")
        for suffix, offset, depth in (('', 0.0, None), ('_up', -zone.thickness_m, zone.thickness_m), ('_down', zone.thickness_m, zone.thickness_m)):
            o, a, b, c = porous_box(zone, offset, depth)
            actions.append(f"{{name {name}{suffix}Cells;type cellSet;action new;source rotatedBoxToCell;origin {vector(o)};i {vector(a)};j {vector(b)};k {vector(c)};}}")
            actions.append(f"{{name {name}{suffix};type cellZoneSet;action new;source setToCellZone;set {name}{suffix}Cells;}}")
        t = zone.transverse_factor
        options.append(
            f"// {zone.name}: coefficients for an effective thickness of {thickness:.6g} m ({basis}); nominal {zone.thickness_m:.6g} m\n"
            f"porosity_{zone.name} {{ type explicitPorositySource; explicitPorositySourceCoeffs {{ selectionMode cellZone; cellZone {name}; "
            f"type DarcyForchheimer; DarcyForchheimerCoeffs {{ d ({fit['d']:.12g} {-t:.12g} {-t:.12g}); f ({fit['f']:.12g} {-t:.12g} {-t:.12g}); "
            f"coordinateSystem {{ type cartesian; origin (0 0 0); rotation {{ type axes; e1 {vector(axis)}; e2 {vector(up)}; }} }} }} }} }}")
        for suffix in ('', '_up', '_down'):
            functions += (f'\nzone_{zone.name}{suffix} {{ type volFieldValue; libs (fieldFunctionObjects); regionType cellZone; name {name}{suffix}; '
                          'operation volAverage; fields (U p); writeFields false; writeControl timeStep; writeInterval 1; errors warn; }')
    return geo, refine, actions, options, functions


def needs_toposet(spec):
    return bool(spec.rotating_zones() or spec.elements.porous)


def zone_list(spec):
    """Every cellZone the worker must find non-empty after topoSet."""
    names = [zone_names(z)[2] for z in spec.rotating_zones()]
    for zone in spec.elements.porous:
        names += [f'porous_{zone.name}', f'porous_{zone.name}_up', f'porous_{zone.name}_down']
    return names
