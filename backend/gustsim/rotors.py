"""Rotor detection: the spin axis and centre of a propeller, fan or rotor from its surfaces.

A suggestion the user confirms, like wheel detection. The axis is found among the principal
axes of the selected surfaces (area-weighted): the true spin axis is the one about which the
part is rotationally symmetric (n blades map onto each other under a 360/n degree turn). For a
rotor with three or more blades the axis is also the thinnest principal direction; a two-bladed
propeller needs the symmetry test, because its chord and axial thickness can spread similarly.
"""
import numpy as np
from . import db, geometry

SNAP_DEGREES = 5.0
SAMPLES = 700


def unit(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v/n if n > 1e-12 else v


def rotate(points, axis, angle):
    """Rodrigues rotation of points (about the origin) by `angle` around unit `axis`."""
    k = unit(axis)
    c, s = np.cos(angle), np.sin(angle)
    return points*c + np.cross(k, points)*s + np.outer(points@k, k)*(1 - c)


def symmetry_error(points, axis, folds):
    """Mean nearest-neighbour distance between the point set and itself turned by 360/folds."""
    turned = rotate(points, axis, 2*np.pi/folds)
    d2 = ((turned[:, None, :] - points[None, :, :])**2).sum(axis=2)
    return float(np.sqrt(d2.min(axis=1)).mean())


def snap(axis):
    """Snap to the nearest coordinate axis within SNAP_DEGREES (keeps dictionaries clean)."""
    a = unit(axis)
    k = int(np.argmax(np.abs(a)))
    if abs(a[k]) >= np.cos(np.radians(SNAP_DEGREES)):
        out = np.zeros(3); out[k] = 1.0
        return out, True
    return a, False


def handedness(centres, normals, areas, axis, origin):
    """+1 or -1: the helix sense of the blades, 0 when it cannot be told (a flat disk, a hub).

    With t = axis x r_hat the direction a point moves under positive rotation, a blade whose
    chord rises along t (leading edge ahead and raised) has surface normals with
    (n.t)(n.axis) < 0. That product does not change when the axis is reversed, so the sense is
    a property of the blades alone. Spun positively about `axis`, a +1 rotor pushes the air
    towards -axis, so its thrust points along +axis."""
    axis = unit(axis)
    radial = centres - origin
    radial = radial - np.outer(radial@axis, axis)
    length = np.linalg.norm(radial, axis=1)
    keep = length > 1e-9
    r_hat = radial[keep]/length[keep][:, None]
    t = np.cross(axis, r_hat)
    n = normals[keep]
    product = (n*t).sum(axis=1)*(n@axis)
    weight = areas[keep]*length[keep]  # outer blade sections dominate, as they do the thrust
    total = float((weight*np.abs(product)).sum())
    signed = float((weight*product).sum())
    if total <= 0 or abs(signed) < 0.2*total:
        return 0
    return 1 if signed < 0 else -1


def orient(axis, rpm, sense):
    """Axis and RPM with the axis along the air the rotor pushes (the solver's inflow
    direction, so thrust along -axis comes out positive). The physical spin is unchanged:
    reversing the axis reverses the sign of the RPM. With an unknown sense, nothing changes."""
    axis = unit(axis)
    if sense and np.sign(rpm)*sense > 0:
        return (-axis).tolist(), -float(rpm)
    return axis.tolist(), float(rpm)


def detect(identifier, patches):
    meta = db.geometry(identifier)
    names = {p['name']: p['group'] for p in meta['patches']}
    if not patches or not set(patches) <= names.keys():
        raise ValueError('Select the rotating part first')
    mesh, groups = geometry.load(identifier)
    selected = np.isin(groups, [names[p] for p in patches])
    faces = mesh.faces[selected]
    if len(faces) < 8:
        raise ValueError('The selected surfaces are too small to find a rotation axis')
    triangles = mesh.vertices[faces]
    areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1)/2
    centres = triangles.mean(axis=1)
    total = float(areas.sum()) or 1.0
    centroid = (centres*areas[:, None]).sum(axis=0)/total
    offset = centres - centroid
    covariance = (offset*areas[:, None]).T @ offset/total
    values, vectors = np.linalg.eigh(covariance)
    # Area-weighted subsample of surface points for the symmetry test.
    rng = np.random.default_rng(0)
    pick = rng.choice(len(centres), size=min(SAMPLES, len(centres)), replace=False, p=areas/total)
    points = offset[pick]
    size = float(np.sqrt(values.max())) or 1.0
    candidates = []
    for i in range(3):
        axis = vectors[:, i]
        errors = {n: symmetry_error(points, axis, n) for n in (2, 3, 4, 5, 6)}
        folds = min(errors, key=errors.get)
        candidates.append({'axis': axis, 'folds': folds, 'error': errors[folds]/size, 'variance': float(values[i])})
    best = min(candidates, key=lambda c: (round(c['error'], 2), c['variance']))
    axis, snapped = snap(best['axis'])
    reasons = []
    symmetric = best['error'] < 0.12
    if symmetric:
        reasons.append(f"{best['folds']}-fold rotational symmetry about this axis")
    thinnest = best['variance'] <= values.min()*1.0001
    if thinnest:
        reasons.append('thinnest direction of the part (a disk-like rotor)')
    if snapped:
        reasons.append(f'within {SNAP_DEGREES:g}° of a coordinate axis, snapped to it')
    # Prefer a positive axis along the dominant component so the sign convention is stable.
    if axis[int(np.argmax(np.abs(axis)))] < 0:
        axis = -axis
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals = normals/np.maximum(np.linalg.norm(normals, axis=1), 1e-300)[:, None]
    sense = handedness(centres, normals, areas, axis, centroid)
    vertices = mesh.vertices[np.unique(faces)]
    delta = vertices - centroid
    axial = delta@axis
    radial = np.linalg.norm(delta - np.outer(axial, axis), axis=1)
    blade = max(float(radial.max()), 1e-5)
    confidence = 0.9 if symmetric and thinnest else 0.7 if symmetric else 0.5 if thinnest else 0.3
    return {'axis': axis.tolist(), 'origin': centroid.tolist(), 'blade_radius': blade, 'radius': blade*1.15,
            'length': max(float(np.abs(axial).max())*2*1.2, blade*.2), 'blades': best['folds'] if symmetric else None,
            'confidence': confidence, 'reasons': reasons or ['best guess from the principal axes; check the axis'],
            # +1: spun positively about `axis`, the blades push air towards -axis (thrust +axis).
            'handedness': sense,
            'patches': list(patches), 'stationary_patches': sorted(set(names) - set(patches))}
