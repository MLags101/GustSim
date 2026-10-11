"""Write a small, valid OpenFOAM ASCII case for pipeline tests.

A structured box mesh with a block of cells removed forms a body with a wall patch. Field
values are prescribed functions, not a flow solution: they exercise the readers, filters and
file contracts of the results package, never numerical or physical correctness.
"""
from pathlib import Path
import numpy as np


def header(cls, name, location=None):
    loc = f'    location "{location}";\n' if location else ''
    return f'FoamFile\n{{\n    version 2.0;\n    format ascii;\n    class {cls};\n{loc}    object {name};\n}}\n'


def lst(items, fmt):
    return f'{len(items)}\n(\n' + '\n'.join(fmt(x) for x in items) + '\n)\n'


def build(root, lengths=(4.0, 2.0, 2.0), cells=(16, 8, 8), body=((1.5, 2.5), (0.5, 1.5), (0.5, 1.5)), time='100',
          speed=10.0, body_name='body', fields=True, body_faces=None):
    """body_faces: optional {(axis, sign): patch} naming body faces by their outward normal
    (out of the body), e.g. to match the six surfaces of a box primitive."""
    root = Path(root)
    nx, ny, nz = cells
    dx, dy, dz = (lengths[0]/nx, lengths[1]/ny, lengths[2]/nz)
    xs, ys, zs = np.linspace(0, lengths[0], nx+1), np.linspace(0, lengths[1], ny+1), np.linspace(0, lengths[2], nz+1)
    centers = lambda i, j, k: np.array([(i+.5)*dx, (j+.5)*dy, (k+.5)*dz])
    solid = np.zeros(cells, bool)
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                c = centers(i, j, k)
                solid[i, j, k] = all(lo < c[a] < hi for a, (lo, hi) in enumerate(body))
    cell_id = -np.ones(cells, int)
    active = [(i, j, k) for k in range(nz) for j in range(ny) for i in range(nx) if not solid[i, j, k]]
    for n, (i, j, k) in enumerate(active):
        cell_id[i, j, k] = n
    pid = lambda i, j, k: i + (nx+1)*(j + (ny+1)*k)
    points = [(x, y, z) for z in zs for y in ys for x in xs]

    def face(axis, i, j, k, positive):
        # Points of the face on the low side of node (i,j,k) normal to `axis`, ordered so the
        # right-hand normal is +axis; reversed for faces whose owner lies on the + side.
        if axis == 0:
            quad = [pid(i, j, k), pid(i, j+1, k), pid(i, j+1, k+1), pid(i, j, k+1)]
        elif axis == 1:
            quad = [pid(i, j, k), pid(i, j, k+1), pid(i+1, j, k+1), pid(i+1, j, k)]
        else:
            quad = [pid(i, j, k), pid(i+1, j, k), pid(i+1, j+1, k), pid(i, j+1, k)]
        return quad if positive else quad[::-1]

    body_patches = sorted(set(body_faces.values())) if body_faces else [body_name]
    internal, patches = [], {n: [] for n in ('inlet', 'outlet', 'front', 'back', 'ground', 'top', *body_patches)}
    names = {(0, False): 'inlet', (0, True): 'outlet', (1, False): 'front', (1, True): 'back', (2, False): 'ground', (2, True): 'top'}
    for (i, j, k) in active:
        owner = cell_id[i, j, k]
        for axis in range(3):
            idx = [i, j, k]
            for side in (False, True):
                other = idx.copy(); other[axis] += 1 if side else -1
                node = idx.copy(); node[axis] += 1 if side else 0
                f = face(axis, *node, positive=side)
                if not 0 <= other[axis] < cells[axis]:
                    patches[names[(axis, side)]].append((f, owner))
                elif solid[tuple(other)]:
                    name = body_faces[(axis, -1 if side else 1)] if body_faces else body_name
                    patches[name].append((f, owner))
                elif side:
                    internal.append((f, owner, cell_id[tuple(other)]))
    internal.sort(key=lambda r: (r[1], r[2]))
    faces = [f for f, _, _ in internal]
    owner = [o for _, o, _ in internal]
    neighbour = [n for _, _, n in internal]
    boundary = []
    for name, entries in patches.items():
        boundary.append((name, len(faces), len(entries)))
        for f, o in entries:
            faces.append(f); owner.append(o)
    mesh = root / 'constant/polyMesh'; mesh.mkdir(parents=True, exist_ok=True)
    (mesh/'points').write_text(header('vectorField', 'points') + lst(points, lambda p: f'({p[0]:.9g} {p[1]:.9g} {p[2]:.9g})'))
    (mesh/'faces').write_text(header('faceList', 'faces') + lst(faces, lambda f: '4(' + ' '.join(map(str, f)) + ')'))
    note = f'note "nPoints:{len(points)} nCells:{len(active)} nFaces:{len(faces)} nInternalFaces:{len(internal)}";\n'
    (mesh/'owner').write_text(header('labelList', 'owner').replace('}\n', note + '}\n', 1) + lst(owner, str))
    (mesh/'neighbour').write_text(header('labelList', 'neighbour').replace('}\n', note + '}\n', 1) + lst(neighbour, str))
    kinds = {name: 'wall' for name in body_patches}
    text = header('polyBoundaryMesh', 'boundary') + f'{len(boundary)}\n(\n'
    for name, start, n in boundary:
        text += f'    {name}\n    {{\n        type {kinds.get(name, "patch")};\n        nFaces {n};\n        startFace {start};\n    }}\n'
    (mesh/'boundary').write_text(text + ')\n')
    system = root/'system'; system.mkdir(parents=True, exist_ok=True)
    (system/'controlDict').write_text(header('dictionary', 'controlDict') + 'application simpleFoam;\nstartFrom latestTime;\nendTime 100;\ndeltaT 1;\nwriteControl timeStep;\nwriteInterval 100;\n')
    (system/'fvSchemes').write_text(header('dictionary', 'fvSchemes'))
    (system/'fvSolution').write_text(header('dictionary', 'fvSolution'))
    (root/'gustsim.foam').touch()
    if not fields:
        return root
    cell_centers = np.array([centers(*c) for c in active])
    face_centers = {name: np.array([np.mean([points[p] for p in f], axis=0) for f, _ in entries]) for name, entries in patches.items()}
    mid = np.array([np.mean(r) for r in body])
    half = np.array([(r[1]-r[0])/2 for r in body])

    def velocity(x):
        # Uniform stream slowed in a wake behind the block, reversed just behind it.
        r = np.sqrt(((x[:, 1]-mid[1])/half[1])**2 + ((x[:, 2]-mid[2])/half[2])**2)
        behind = (x[:, 0] > body[0][1]) & (r < 1.2)
        u = np.full(len(x), speed)
        u[behind] = speed*(1 - 1.3*np.exp(-(x[behind, 0]-body[0][1])))
        return np.column_stack([u, 0.05*speed*np.sin(x[:, 2]), np.zeros(len(x))])

    def pressure(x):
        # Bernoulli outside the wake; a constant base pressure inside it, so the wake has a
        # total-pressure loss (Cpt < 0 next to the body) like a separated flow.
        u = velocity(x)
        p = 0.5*speed**2 - 0.5*np.sum(u*u, axis=1)
        r = np.sqrt(((x[:, 1]-mid[1])/half[1])**2 + ((x[:, 2]-mid[2])/half[2])**2)
        wake = (x[:, 0] > body[0][1]) & (r < 1.2)
        p[wake] = -0.3*0.5*speed**2
        return p

    q = 0.5*speed**2
    folder = root/time; folder.mkdir(exist_ok=True)

    def write_field(name, internal_values, boundary_values, vector):
        cls = 'volVectorField' if vector else 'volScalarField'
        dims = '[0 1 -1 0 0 0 0]' if name == 'U' else '[0 2 -2 0 0 0 0]' if name in ('p', 'wallShearStress', 'k') else '[0 0 0 0 0 0 0]'
        fmt = (lambda v: f'({v[0]:.9g} {v[1]:.9g} {v[2]:.9g})') if vector else (lambda v: f'{v:.9g}')
        kind = 'List<vector>' if vector else 'List<scalar>'
        text = header(cls, name, time) + f'dimensions {dims};\ninternalField nonuniform {kind} ' + lst(list(internal_values), fmt) + ';\nboundaryField\n{\n'
        for patch, values in boundary_values.items():
            text += f'    {patch}\n    {{\n        type calculated;\n        value nonuniform {kind} ' + lst(list(values), fmt) + ';\n    }\n'
        (folder/name).write_text(text + '}\n')

    U = velocity(cell_centers)
    p = pressure(cell_centers)

    def body_pressure(fc):
        # A bluff-body-like wall distribution: stagnation on the upstream face, suction on the
        # sides recovering downstream, a low base pressure behind.
        cp = np.full(len(fc), -0.3)
        front = fc[:, 0] < body[0][0] + 1e-9
        rear = fc[:, 0] > body[0][1] - 1e-9
        r2 = ((fc[:, 1]-mid[1])/half[1])**2 + ((fc[:, 2]-mid[2])/half[2])**2
        cp[front] = 1 - 0.6*r2[front]
        side = ~front & ~rear
        cp[side] = -1.1 + 0.8*(fc[side, 0]-body[0][0])/(body[0][1]-body[0][0])
        return cp*q
    wall_u = {name: (np.zeros((len(fc), 3)) if name in body_patches else velocity(fc)) for name, fc in face_centers.items()}
    write_field('U', U, wall_u, True)
    wall_p = {name: (body_pressure(fc) if name in body_patches else pressure(fc)) for name, fc in face_centers.items()}
    write_field('p', p, wall_p, False)
    write_field('Cp', p/q, {name: v/q for name, v in wall_p.items()}, False)
    # OpenFOAM pressure function object, mode totalCoeff with pInf 0: freestream = 1.
    total = lambda x: (pressure(x) + 0.5*np.sum(velocity(x)**2, axis=1))/q
    write_field('Cpt', total(cell_centers), {name: total(fc) for name, fc in face_centers.items()}, False)
    write_field('Q', np.exp(-np.sum((cell_centers-mid-[1, 0, 0])**2, axis=1))*speed**2, {n: np.zeros(len(fc)) for n, fc in face_centers.items()}, False)
    # Kinematic wall shear on the body: opposite to the near-wall flow by OpenFOAM's
    # convention, with a reversed (separated) band on the rear half.
    tau = {}
    for name, fc in face_centers.items():
        if name in body_patches:
            sign = np.where(fc[:, 0] > mid[0] + 0.2*half[0], -1.0, 1.0)
            tau[name] = np.column_stack([-0.01*speed**2*sign, np.zeros(len(fc)), np.zeros(len(fc))])
        else:
            tau[name] = np.zeros((len(fc), 3))
    write_field('wallShearStress', np.zeros((len(active), 3)), tau, True)
    write_field('yPlus', np.zeros(len(active)), {n: (np.full(len(fc), 40.0) if n in body_patches else np.zeros(len(fc))) for n, fc in face_centers.items()}, False)
    return root
