"""Demo models that show what GustSim can do, each with a ready setup.

Generated parametrically (no files shipped) and stored as ordinary immutable revisions. Each
demo carries a `sample_setup` the setup page starts from (use case, speed, ground, rotating
parts, elements, transient settings); everything stays editable. Demos are illustrations of the
workflow, not validation cases: the Ahmed body and the NACA 0012 wing have well-known reference values
in the literature, but a demo run is not a benchmark gate.

An optional GUSTSIM_SAMPLES_DIR adds the user's own STEP/STL files to the list; they are read,
never modified.
"""
import math
import os
from pathlib import Path
import numpy as np
import trimesh
from . import geometry

SAMPLES = {
    'teardrop': {'name': 'First run: streamlined body', 'use_case': 'aerodynamics', 'speed': 20.0, 'minutes': 10,
                 'shows': ['Pressure and friction', 'Streamlines', 'Forces and Cd'],
                 'description': 'A 0.6 m streamlined body of revolution at 20 m/s. The flow stays attached, so the steady solve settles cleanly: the quickest complete run with flow pictures, forces and the report.'},
    'car': {'name': 'Road car with spinning wheels', 'use_case': 'aerodynamics', 'speed': 30.0, 'minutes': 40,
            'shows': ['Moving ground', 'Rotating wheels', 'Front/rear lift', 'Drag per part'],
            'description': 'A simple 4.3 m car at 108 km/h on a moving road, wheels spinning with the ground — like a rolling-road wind tunnel.'},
    'ahmed': {'name': 'Ahmed body (25° slant)', 'use_case': 'aerodynamics', 'speed': 40.0, 'minutes': 30,
              'shows': ['Separation on the slant', 'Wake and loss clouds', 'Insights'],
              'description': 'The classic automotive reference shape (1.044 m long, on four stilts). Shows separation over the rear slant and a trailing vortex pair.'},
    'wing': {'name': 'Wing at 6° angle of attack', 'use_case': 'aerodynamics', 'speed': 30.0, 'minutes': 30,
             'shows': ['Lift and drag', 'Tip vortices', 'Suction peak'],
             'description': 'A 1 m NACA 0012 wing with 0.25 m chord, pitched 6° nose-up. Lift, induced drag and the tip vortices; coefficients use the 0.25 m² planform.'},
    'propeller': {'name': 'Drone propeller in climb', 'use_case': 'propeller', 'speed': 8.0, 'minutes': 30,
                  'shows': ['Rotating region (MRF)', 'Automatic axis detection', 'Thrust, torque and efficiency'],
                  'description': 'A three-blade 250 mm propeller at 6000 RPM climbing at 8 m/s (advance ratio 0.32). The spin axis and the direction the blades push air are read from its shape.'},
    'tower': {'name': 'Tower in wind (transient)', 'use_case': 'aerodynamics', 'speed': 10.0, 'minutes': 60,
              'shows': ['Vortex shedding', 'Animations', 'Strouhal number'],
              'description': 'A 0.8 m square tower on the ground in a 10 m/s wind, solved in time: shedding vortices, load fluctuations and an animation.'},
    'radiator': {'name': 'Radiator in a duct', 'use_case': 'pipe', 'speed': 4.0, 'minutes': 15,
                 'shows': ['Internal flow', 'Porous radiator', 'Pressure drop'],
                 'description': 'A 1.2 m square duct with a radiator across it (fitted from two measured points). Shows the pressure drop through the core.'},
}
ORDER = ('teardrop', 'car', 'ahmed', 'wing', 'propeller', 'tower', 'radiator')


def external_dir():
    folder = os.environ.get('GUSTSIM_SAMPLES_DIR')
    path = Path(folder) if folder else None
    return path if path and path.is_dir() else None


def listing():
    items = [{'id': key, **SAMPLES[key], 'source': 'generated'} for key in ORDER]
    folder = external_dir()
    if folder:
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in geometry.IMPORT_SUFFIXES and path.stat().st_size <= 100*1024*1024:
                items.append({'id': 'file:' + path.name, 'name': path.stem, 'use_case': 'aerodynamics', 'speed': 20.0,
                              'description': f'{path.suffix[1:].upper()} file from the samples folder', 'source': 'file'})
    return items


# ---- shape builders --------------------------------------------------------------------------
def loft(rings, cap=True):
    """Closed surface through matching cross-section rings (each an (n, 3) array, same n), with
    fan caps at both ends. Normals are made consistent and outward."""
    rings = [np.asarray(r, dtype=float) for r in rings]
    n = len(rings[0])
    vertices = np.concatenate(rings)
    faces = []
    for k in range(len(rings) - 1):
        a, b = k*n, (k + 1)*n
        for i in range(n):
            j = (i + 1) % n
            faces += [[a + i, a + j, b + j], [a + i, b + j, b + i]]
    if cap:
        for end, ring_index in ((0, 0), (1, len(rings) - 1)):
            centre = len(vertices)
            vertices = np.vstack([vertices, rings[ring_index].mean(axis=0)])
            base = ring_index*n
            for i in range(n):
                j = (i + 1) % n
                faces.append([centre, base + j, base + i] if end == 0 else [centre, base + i, base + j])
    mesh = trimesh.Trimesh(vertices, np.asarray(faces), process=True)
    trimesh.repair.fix_normals(mesh)
    if mesh.volume < 0:
        mesh.invert()
    return mesh


def superellipse(width, height, centre_z, n=48, power=4.0):
    """(n, 2) points (y, z) of a rounded-rectangle-like section."""
    t = np.linspace(0, 2*np.pi, n, endpoint=False)
    c, s = np.cos(t), np.sin(t)
    y = width/2*np.sign(c)*np.abs(c)**(2/power)
    z = centre_z + height/2*np.sign(s)*np.abs(s)**(2/power)
    return np.column_stack([y, z])


def naca0012(chord, n=40):
    """Closed NACA 0012 section, (2n, 2) points (x along chord from the leading edge, thickness)."""
    beta = np.linspace(0, np.pi, n)
    x = (1 - np.cos(beta))/2
    t = 0.12/0.2*(0.2969*np.sqrt(x) - 0.1260*x - 0.3516*x**2 + 0.2843*x**3 - 0.1036*x**4)
    upper = np.column_stack([x, t])[::-1]
    lower = np.column_stack([x, -t])[1:-1]
    return np.vstack([upper, lower])*chord


def car_body():
    """A smooth car-like body from superelliptic sections lofted along X (front at -X)."""
    length = 4.3
    xs = np.linspace(0, length, 44)
    rings = []
    for x in xs:
        u = x/length  # 0 front, 1 rear
        nose = math.sin(math.pi/2*min(1, u/0.12))**0.7
        tail = math.sin(math.pi/2*min(1, (1 - u)/0.06))**0.5
        bottom = 0.18 + 0.12*(1 - nose) + 0.08*(1 - tail)
        hood = 0.55 + 0.37*nose
        if u < 0.30: top = hood
        elif u < 0.45: top = hood + (1.42 - hood)*((u - 0.30)/0.15)
        elif u < 0.75: top = 1.42
        else: top = 1.42 - (1.42 - 0.95)*((u - 0.75)/0.25)**1.2
        top = bottom + (top - bottom)*tail
        width = 1.7*(0.35 + 0.65*nose)*(0.55 + 0.45*tail)
        section = superellipse(max(width, 0.05), max(top - bottom, 0.03), (top + bottom)/2, power=5.0)
        rings.append(np.column_stack([np.full(len(section), x - length/2), section]))
    return loft(rings)


def wheel(centre, radius=0.32, width=0.22):
    w = trimesh.creation.cylinder(radius=radius, height=width, sections=48)
    w.apply_transform(trimesh.transformations.rotation_matrix(np.pi/2, (1, 0, 0)))
    w.apply_translation(centre)
    return w


def ahmed_body():
    """Ahmed body (25 degree slant): 1.044 x 0.389 x 0.288 m, front edges rounded with R = 0.1 m,
    50 mm above the floor on four 30 mm stilts. Front at -X."""
    L, W, H, R, clearance = 1.044, 0.389, 0.288, 0.1, 0.05
    slant_length, slant = 0.222, math.radians(25)
    slant_x = slant_length*math.cos(slant)
    xs = np.r_[np.linspace(0, R, 16), np.linspace(R + 0.02, L - slant_x, 8), np.linspace(L - slant_x + 0.01, L, 8)]
    rings = []
    for x in xs:
        inset = R - math.sqrt(max(R*R - (R - x)**2, 0.0)) if x < R else 0.0
        top = clearance + H - inset
        if x > L - slant_x:
            top -= (x - (L - slant_x))*math.tan(slant)
        bottom, half = clearance + inset, W/2 - inset
        corners = [(-half, bottom), (half, bottom), (half, top), (-half, top)]
        ring = []
        for (y0, z0), (y1, z1) in zip(corners, corners[1:] + corners[:1]):
            for s in np.linspace(0, 1, 8, endpoint=False):
                ring.append((x - L/2, y0 + (y1 - y0)*s, z0 + (z1 - z0)*s))
        rings.append(np.asarray(ring))
    body = loft(rings)
    stilts = []
    for x in (-L/2 + 0.163, L/2 - 0.47):
        for y in (-0.1175, 0.1175):
            s = trimesh.creation.cylinder(radius=0.015, height=clearance + 0.01, sections=20)
            s.apply_translation((x, y, (clearance + 0.01)/2))
            stilts.append(s)
    return body, stilts


def teardrop(length=0.6, diameter=0.15, nose=0.35, n=48, stations=60):
    """Streamlined body of revolution along x, nose at -x: an elliptical nose over `nose` of the
    length, then a long tail tapering to a small blunt end. A bluff body (a sphere) sheds an
    unsteady wake that a steady solve cannot settle; this shape keeps the flow attached."""
    radius = diameter/2
    s = (1 - np.cos(np.linspace(0, np.pi, stations)))/2  # clustered at both ends
    r = np.where(s < nose, radius*np.sqrt(np.clip(1 - ((nose - s)/nose)**2, 0, 1)),
                 radius*np.cos(np.pi/2*(s - nose)/(1 - nose))**1.3)
    r = np.maximum(r, 0.03*radius)
    t = np.linspace(0, 2*np.pi, n, endpoint=False)
    rings = [np.column_stack([np.full(n, x*length - length/2), rr*np.cos(t), rr*np.sin(t)]) for x, rr in zip(s, r)]
    return loft(rings)


def wing(span=1.0, chord=0.25, pitch_deg=6.0):
    section = naca0012(chord)
    rings = [np.column_stack([section[:, 0] - chord/2, np.full(len(section), y), section[:, 1]]) for y in np.linspace(-span/2, span/2, 12)]
    mesh = loft(rings)
    mesh.apply_transform(trimesh.transformations.rotation_matrix(math.radians(pitch_deg), (0, 1, 0)))
    return mesh


def propeller(blades=3, diameter=0.25, hub=0.018):
    """Twisted, tapered blades around +Z (a drone rotor). Positive RPM about +Z (anticlockwise
    seen from above) pushes air down: the leading edges point along the motion and are raised."""
    radius = diameter/2
    parts = []
    for b in range(blades):
        phi0 = 2*math.pi*b/blades
        rings = []
        for r in np.linspace(hub*0.8, radius, 14):
            u = (r - hub)/(radius - hub)
            chord = 0.030*(1.15 - 0.55*max(u, 0))
            pitch = math.atan(0.11/(2*math.pi*max(r, 1e-3)))  # ~110 mm geometric pitch
            section = naca0012(chord, n=16)
            s = section[:, 0] - chord/2          # along the chord, LE at -chord/2
            t = section[:, 1]
            tangential = -s*math.cos(pitch)      # LE (s<0) ahead in the direction of motion
            axial = -s*math.sin(pitch) + t*math.cos(pitch)  # LE raised
            radial_dir = np.array([math.cos(phi0), math.sin(phi0), 0.0])
            motion_dir = np.array([-math.sin(phi0), math.cos(phi0), 0.0])
            ring = r*radial_dir + np.outer(tangential, motion_dir) + np.outer(axial, (0, 0, 1))
            rings.append(ring)
        parts.append(loft(rings))
    centre = trimesh.creation.cylinder(radius=hub, height=0.016, sections=32)
    parts.append(centre)
    return trimesh.util.concatenate(parts)


# ---- demos -----------------------------------------------------------------------------------
def build(key):
    """(mesh, groups, names, setup) for one demo; groups index the names."""
    if key == 'teardrop':
        mesh = teardrop()
        return mesh, np.zeros(len(mesh.faces), int), {0: 'body'}, {'intent': {'speed': 20.0}}
    if key == 'car':
        body = car_body()
        wheels = {'wheel_fl': (-1.35, -0.78), 'wheel_fr': (-1.35, 0.78), 'wheel_rl': (1.35, -0.78), 'wheel_rr': (1.35, 0.78)}
        parts = [body] + [wheel((x, y, 0.32)) for x, y in wheels.values()]
        names = {0: 'body', **{i + 1: name for i, name in enumerate(wheels)}}
        mesh = trimesh.util.concatenate(parts)
        groups = np.concatenate([np.full(len(p.faces), i) for i, p in enumerate(parts)])
        elements = {'wheels': [{'name': name, 'patches': [name], 'center': [x, y, 0.32], 'axis': [0, 1, 0], 'radius': 0.32, 'rotating': True}
                               for name, (x, y) in wheels.items()]}
        return mesh, groups, names, {'intent': {'speed': 30.0, 'ground': 'moving', 'ride_height_m': 0.0},
                                     'overrides': {'elements': elements, 'front_axle': [-1.35, 0, 0.32], 'rear_axle': [1.35, 0, 0.32]}}
    if key == 'ahmed':
        body, stilts = ahmed_body()
        parts = [body] + stilts
        mesh = trimesh.util.concatenate(parts)
        groups = np.concatenate([np.full(len(body.faces), 0)] + [np.full(len(s.faces), 1) for s in stilts])
        return mesh, groups, {0: 'ahmed_body', 1: 'stilts'}, {'intent': {'speed': 40.0, 'ground': 'static', 'ride_height_m': 0.0}}
    if key == 'wing':
        mesh = wing()
        # Wing coefficients refer to the planform (span x chord) and the chord, not the frontal
        # area the generic preset uses; with the frontal area Cl would read about 4x too high.
        return mesh, np.zeros(len(mesh.faces), int), {0: 'wing'},             {'intent': {'speed': 30.0}, 'overrides': {'reference_area': 0.25, 'reference_length': 0.25}}
    if key == 'propeller':
        mesh = propeller()
        return mesh, np.zeros(len(mesh.faces), int), {0: 'propeller'}, \
            {'intent': {'kind': 'propeller', 'speed': 8.0, 'rpm': 6000.0, 'patches': ['propeller'], 'axis': [0, 0, 1], 'origin': [0, 0, 0], 'axis_mode': 'auto'},
             # One automatic retry with robust numerics if the rotating start diverges.
             'overrides': {'auto_retry': 1}}
    if key == 'tower':
        mesh = trimesh.creation.box(extents=(0.2, 0.2, 0.8))
        mesh.apply_translation((0, 0, 0.4))
        return mesh, np.zeros(len(mesh.faces), int), {0: 'tower'}, \
            {'intent': {'speed': 10.0, 'ground': 'static', 'ride_height_m': 0.0},
             'overrides': {'time': {'mode': 'transient', 'flow_throughs': 3, 'frames': 40, 'average_after_fraction': 0.4}}}
    if key == 'radiator':
        mesh = trimesh.creation.box(extents=(1.2, 0.3, 0.3))
        normals = mesh.face_normals
        groups = np.where(normals[:, 0] < -0.9, 0, np.where(normals[:, 0] > 0.9, 1, 2))
        porous = {'name': 'radiator', 'center': [0, 0, 0], 'axis': [1, 0, 0], 'up': [0, 0, 1], 'width_m': 0.3, 'height_m': 0.3,
                  'thickness_m': 0.05, 'curve': [[2.0, 45.0], [5.0, 210.0]], 'transverse_factor': 10}
        return mesh, groups, {0: 'duct_inlet', 1: 'duct_outlet', 2: 'duct_walls'}, \
            {'intent': {'kind': 'pipe', 'speed': 4.0, 'inlet': 'duct_inlet', 'outlet': 'duct_outlet', 'driving': 'speed', 'turbulence': 'kOmegaSST'},
             'overrides': {'elements': {'porous': [porous]}}}
    raise KeyError('Sample not found')


def create(key):
    if key.startswith('file:'):
        folder = external_dir()
        name = Path(key[5:]).name
        if folder is None or not (folder/name).is_file():
            raise KeyError('Sample not found')
        return geometry.import_file(name, (folder/name).read_bytes(), units='mm')
    if key not in SAMPLES:
        raise KeyError('Sample not found')
    mesh, groups, names, setup = build(key)
    return geometry.save(mesh, groups, names, f'{SAMPLES[key]["name"]}.stl', confirmed=True,
                         operations=[{'sample': key}], extra={'sample': key, 'sample_setup': setup})
