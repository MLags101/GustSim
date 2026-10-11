"""Wheel detection: disk-like parts touching the ground with a lateral axle.

A suggestion only; the user confirms which parts rotate. Each candidate lists its reasons.
"""
import numpy as np
from . import db, geometry


def leaf_parts(meta, mesh, groups):
    parts = meta.get('parts') or []
    parents = {p.get('parent_id') for p in parts}
    leaves = [p for p in parts if p['id'] not in parents and p['patches']]
    if leaves:
        return [(p['id'], p['name'], p['patches']) for p in leaves]
    # No CAD components: connected bodies of the surface stand in for parts.
    names = {p['group']: p['name'] for p in meta['patches']}
    bodies = geometry.connected_groups(mesh, 179.999)
    out = []
    for body in np.unique(bodies):
        members = sorted({names[int(g)] for g in np.unique(groups[bodies == body])})
        out.append((f'body_{int(body)}', members[0] if len(members) == 1 else f'Body {int(body)+1}', members))
    return out


def detect(identifier, forward=(1, 0, 0), up=(0, 0, 1)):
    meta = db.geometry(identifier)
    mesh, groups = geometry.load(identifier)
    forward = np.asarray(forward, float); forward /= np.linalg.norm(forward)
    up = np.asarray(up, float); up /= np.linalg.norm(up)
    lateral = np.cross(up, forward)
    bottom = float((mesh.vertices@up).min())
    size = float(np.linalg.norm(mesh.extents))
    group_of = {p['name']: p['group'] for p in meta['patches']}
    candidates = []
    for part_id, name, patches in leaf_parts(meta, mesh, groups):
        mask = np.isin(groups, [group_of[p] for p in patches if p in group_of])
        if not mask.any():
            continue
        points = mesh.vertices[np.unique(mesh.faces[mask])]
        if len(points) < 12:
            continue
        center = points.mean(axis=0)
        centered = points - center
        values, vectors = np.linalg.eigh(np.cov(centered.T))
        axis = vectors[:, 0]
        large = values[1:]
        reasons, score = [], 0.0
        disk = abs(large[0] - large[1]) / max(large.max(), 1e-12) < 0.15
        thin = values[0] < 0.5 * large.min()
        if not (disk and thin):
            continue  # a wheel is first of all a flat disk; everything else only adds confidence
        score += 0.35; reasons.append('round and flat like a wheel')
        lateral_axis = abs(axis@lateral) > np.cos(np.radians(20))
        if lateral_axis:
            score += 0.25; reasons.append('axis points sideways')
        radius = float(np.percentile(np.linalg.norm(centered - np.outer(centered@axis, axis), axis=1), 98))
        touches = float((points@up).min()) - bottom < 0.02 * size
        if touches:
            score += 0.25; reasons.append('touches the lowest point of the model')
        silhouette = geometry.projected_area(geometry.trimesh.Trimesh(mesh.vertices, mesh.faces[mask], process=False), axis)
        if radius > 0 and abs(silhouette/(np.pi*radius**2) - 1) < 0.2:
            score += 0.15; reasons.append('side view is a circle')
        if score >= 0.6:
            axis = axis if axis@lateral >= 0 else -axis
            candidates.append({'part_id': part_id, 'name': name, 'patches': patches, 'center': center.tolist(), 'axis': axis.tolist(),
                               'radius': radius, 'width': float(np.ptp(centered@axis)), 'confidence': round(min(score, 1.0), 2), 'reasons': reasons})
    candidates.sort(key=lambda c: float(np.asarray(c['center'])@forward))
    axles = []
    along = [float(np.asarray(c['center'])@forward) for c in candidates]
    used = set()
    for i, c in enumerate(candidates):
        if i in used:
            continue
        group = [j for j in range(len(candidates)) if j not in used and abs(along[j] - along[i]) < 0.5 * c['radius']]
        used.update(group)
        centers = np.array([candidates[j]['center'] for j in group])
        axles.append({'center': centers.mean(axis=0).tolist(), 'wheels': [candidates[j]['name'] for j in group]})
    result = {'candidates': candidates, 'axles': axles, 'wheelbase_m': None, 'front_axle': None, 'rear_axle': None}
    if len(axles) >= 2:
        positions = [np.asarray(a['center'])@forward for a in axles]
        front, rear = axles[int(np.argmax(positions))], axles[int(np.argmin(positions))]
        result.update(front_axle=front['center'], rear_axle=rear['center'], wheelbase_m=float(max(positions) - min(positions)))
    return result
