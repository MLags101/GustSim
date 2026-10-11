"""Rule-based flow insights: deterministic observations anchored on the model.

Every rule is a pure function of this run's solved data (wall-face values, averaged loads,
convergence record, iso-surface statistics) with thresholds listed in the output. Insights are
observations to guide where to look; they never change numerical or validation status, and no
network or language model is involved.
"""
import hashlib
import json
from collections import deque
import numpy as np

ENGINE_VERSION = '1.0.0'
THRESHOLDS = {
    'separation_v1': {'min_area_fraction': 0.005, 'min_tangent': 0.3},
    'stagnation_v1': {'min_cp': 0.8, 'min_area_fraction_of_frontal': 0.01},
    'suction_peak_v1': {'max_cp': -1.5},
    'base_pressure_v1': {'rear_facing': 0.5, 'max_cp': -0.2, 'min_drag_share': 0.1},
    'part_ranking_v1': {'top': 3},
    'wake_size_v1': {'min_length_ratio': 0.5},
    'lift_balance_v1': {'min_difference': 0.1},
    'yplus_v1': {'wall_function': [30, 300], 'resolved_max': 1, 'max_fraction_outside': 0.2},
    'unsteadiness_v1': {'max_normalised_std': 0.05},
    'design_advice_v1': {'min_area_fraction': 0.005},
}
DISCLAIMER = ('Rule-based observations from this solution to guide where to look. They are not design '
              'validation, and steady RANS separation and wake predictions are mesh- and model-sensitive.')


def clusters(points, spacing):
    """Group points whose grid cells touch (26-neighbourhood). Returns one label per point."""
    if not len(points):
        return np.zeros(0, int)
    keys = np.floor(points/spacing).astype(np.int64)
    cells = {}
    for index, key in enumerate(map(tuple, keys)):
        cells.setdefault(key, []).append(index)
    label_of_cell, label = {}, 0
    offsets = [(i, j, k) for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)]
    for start in cells:
        if start in label_of_cell:
            continue
        queue = deque([start]); label_of_cell[start] = label
        while queue:
            cx, cy, cz = queue.popleft()
            for dx, dy, dz in offsets:
                neighbour = (cx+dx, cy+dy, cz+dz)
                if neighbour in cells and neighbour not in label_of_cell:
                    label_of_cell[neighbour] = label
                    queue.append(neighbour)
        label += 1
    labels = np.empty(len(points), int)
    for key, members in cells.items():
        labels[members] = label_of_cell[key]
    return labels


class Engine:
    def __init__(self, faces, spec, forces=None, convergence=None, layers=None, mesh_summary=None, part_names=None, model_bounds=None):
        self.f = faces
        self.spec = spec
        self.forces = forces or {}
        self.convergence = convergence or {}
        self.layers = layers or {}
        self.mesh_summary = mesh_summary or {}
        self.part_names = part_names or {}
        self.items = []
        drag = np.asarray(spec.references.drag_axis, float)
        self.drag = drag/np.linalg.norm(drag)
        lift = np.asarray(spec.references.lift_axis, float)
        lift = lift - (lift@self.drag)*self.drag
        self.lift = lift/max(np.linalg.norm(lift), 1e-12)
        self.side = np.cross(self.lift, self.drag)
        self.area = faces['area']
        self.wetted = float(self.area.sum())
        edge = float(np.sqrt(2*np.median(self.area))) if len(self.area) else 1.0
        self.spacing = 2.5*edge
        bounds = np.asarray(model_bounds, float) if model_bounds is not None else np.array([faces['centroid'].min(axis=0), faces['centroid'].max(axis=0)])
        self.center = bounds.mean(axis=0)
        self.size = float(np.linalg.norm(bounds[1]-bounds[0]))
        self.q = 0.5*spec.fluid.density*spec.flow.speed**2

    # ---- helpers -----------------------------------------------------------------------
    def part_of(self, mask):
        parts = self.f['part_index'][mask]
        if not len(parts):
            return None
        values, counts = np.unique(parts, return_counts=True)
        return self.part_names.get(int(values[np.argmax(counts)]))

    def anchor(self, mask):
        weights = self.area[mask]
        point = (self.f['centroid'][mask]*weights[:, None]).sum(axis=0)/max(weights.sum(), 1e-300)
        normal = (self.f['normal'][mask]*weights[:, None]).sum(axis=0)
        normal = normal/max(np.linalg.norm(normal), 1e-12)
        radius = float(np.sqrt(weights.sum()/np.pi))
        return {'point': point.tolist(), 'normal': normal.tolist(), 'radius_m': radius, 'part': self.part_of(mask)}

    def camera(self, point, normal):
        point, normal = np.asarray(point), np.asarray(normal)
        direction = normal if np.linalg.norm(normal) > 0.5 else -self.drag
        direction = direction + 0.35*self.lift + 0.2*self.side
        direction = direction/np.linalg.norm(direction)
        distance = max(self.size*0.9, 1e-3)
        return {'position': (point + direction*distance).tolist(), 'focal_point': point.tolist(), 'view_up': self.lift.tolist()}

    def add(self, rule, index, category, severity, confidence, title, message, evidence, anchor, layers, array, value_range, limitations):
        self.items.append({'id': f'{rule}:{index}', 'rule': rule, 'category': category, 'severity': severity, 'confidence': confidence,
                           'title': title, 'message': message, 'evidence': evidence, 'anchor': anchor,
                           'view_preset': {'camera': self.camera(anchor['point'], anchor.get('normal', [0, 0, 0])), 'layers': layers,
                                           'array': array, 'range': value_range},
                           'limitations': limitations})

    def pressure_drag(self, mask):
        """Pressure drag of the selected faces, N (force on body = -p n_out A)."""
        p = self.f['pressure_pa'][mask]
        return float(np.nansum(-p*(self.f['normal'][mask]@self.drag)*self.area[mask]))

    def total_pressure_drag(self):
        try:
            return float(self.forces['frames']['wind']['total']['pressure_n']['drag'])
        except (KeyError, TypeError):
            return self.pressure_drag(np.ones(len(self.area), bool))

    # ---- rules -------------------------------------------------------------------------
    def separation(self):
        if self.q <= 0 or 'wall_shear_pa' not in self.f:
            return
        t = THRESHOLDS['separation_v1']
        n = self.f['normal']
        tangent = self.drag[None, :] - (n@self.drag)[:, None]*n
        magnitude = np.linalg.norm(tangent, axis=1)
        valid = magnitude >= t['min_tangent']
        direction = tangent/np.maximum(magnitude, 1e-12)[:, None]
        along = np.einsum('ij,ij->i', self.f['wall_shear_pa'], direction)
        # A surface facing straight downstream has no tangential freestream to compare with;
        # such a base lies in separated flow by construction, so it joins the reversed region.
        # It only counts when it joins measured reversed flow on its flanks.
        base = (n@self.drag) > np.sqrt(1 - t['min_tangent']**2)
        measured = valid & np.isfinite(along) & (along < 0)
        reversed_ = measured | base
        if not reversed_.any():
            return
        index = np.flatnonzero(reversed_)
        labels = clusters(self.f['centroid'][index], self.spacing)
        total_drag = self.total_pressure_drag()
        found = []
        for label in np.unique(labels):
            members = index[labels == label]
            area = float(self.area[members].sum())
            if area < t['min_area_fraction']*self.wetted or not measured[members].any():
                continue
            mask = np.zeros(len(self.area), bool); mask[members] = True
            share = self.pressure_drag(mask)/total_drag if abs(total_drag) > 1e-12 else None
            found.append((area, mask, share))
        for i, (area, mask, share) in enumerate(sorted(found, key=lambda x: -x[0])[:5]):
            anchor = self.anchor(mask)
            where = f" on '{anchor['part']}'" if anchor['part'] else ''
            evidence = {'area_m2': {'value': area, 'unit': 'm²'}, 'wetted_fraction': {'value': area/self.wetted, 'unit': '1'}}
            if share is not None:
                evidence['pressure_drag_share'] = {'value': share, 'unit': '1'}
            share_text = f' Faces in this region carry {share*100:.0f}% of the pressure drag.' if share is not None else ''
            self.add('separation_v1', i, 'separation', 'warning' if (share or 0) > 0.15 else 'suggestion', 'measured',
                     f'Reversed near-wall flow{where}',
                     f'The wall shear stress opposes the oncoming flow over {area:.3g} m²{where}, which indicates separated, '
                     f'recirculating flow.{share_text} Smoother curvature or a later, sharper release edge can reduce it.',
                     evidence, anchor, ['surface', 'friction_lines'], 'Cf', [0, 0.005],
                     'Separation onset in steady RANS depends on the mesh, the turbulence model and the wall treatment.')

    def stagnation(self):
        if self.q <= 0:
            return
        t = THRESHOLDS['stagnation_v1']
        mask = np.isfinite(self.f['Cp']) & (self.f['Cp'] > t['min_cp'])
        frontal = self.forces.get('frontal_area_m2') or self.spec.references.area
        area = float(self.area[mask].sum())
        if not mask.any() or area < t['min_area_fraction_of_frontal']*frontal:
            return
        anchor = self.anchor(mask)
        self.add('stagnation_v1', 0, 'drag', 'info', 'measured', 'High-pressure stagnation region',
                 f'Pressure close to the full dynamic pressure (Cp > {t["min_cp"]}) acts on {area:.3g} m², '
                 f'{area/frontal*100:.0f}% of the frontal area. Rounding or angling this face lowers the pressure that pushes back.',
                 {'area_m2': {'value': area, 'unit': 'm²'}, 'max_cp': {'value': float(np.nanmax(self.f['Cp'][mask])), 'unit': '1'}},
                 anchor, ['surface'], 'Cp', [-1, 1], 'Pressure magnitudes are reliable in steady RANS; this is not a design recommendation.')

    def suction_peak(self):
        if self.q <= 0:
            return
        t = THRESHOLDS['suction_peak_v1']
        mask = np.isfinite(self.f['Cp']) & (self.f['Cp'] < t['max_cp'])
        if not mask.any():
            return
        index = np.flatnonzero(mask)
        labels = clusters(self.f['centroid'][index], self.spacing)
        for i, label in enumerate(sorted(np.unique(labels), key=lambda l: self.f['Cp'][index[labels == l]].min())[:3]):
            sub = np.zeros(len(self.area), bool); sub[index[labels == label]] = True
            anchor = self.anchor(sub)
            peak = float(self.f['Cp'][sub].min())
            self.add('suction_peak_v1', i, 'separation', 'suggestion', 'heuristic', 'Strong suction peak',
                     f'Pressure drops to Cp {peak:.2f} here. Flow must slow down again after a peak like this, which is where it tends to separate.',
                     {'min_cp': {'value': peak, 'unit': '1'}, 'area_m2': {'value': float(self.area[sub].sum()), 'unit': 'm²'}},
                     anchor, ['surface'], 'Cp', [-2, 1], 'A suction peak is a risk indicator, not proof of separation.')

    def base_pressure(self):
        if self.q <= 0:
            return
        t = THRESHOLDS['base_pressure_v1']
        rear = (self.f['normal']@self.drag) > t['rear_facing']
        mask = rear & np.isfinite(self.f['Cp']) & (self.f['Cp'] < t['max_cp'])
        if not mask.any():
            return
        total = self.total_pressure_drag()
        share = self.pressure_drag(mask)/total if abs(total) > 1e-12 else None
        if share is None or share < t['min_drag_share']:
            return
        anchor = self.anchor(mask)
        mean_cp = float(np.average(self.f['Cp'][mask], weights=self.area[mask]))
        self.add('base_pressure_v1', 0, 'drag', 'warning' if share > 0.3 else 'suggestion', 'measured', 'Low pressure on rear-facing surfaces',
                 f'Rear-facing surfaces sit at an average Cp of {mean_cp:.2f} and contribute {share*100:.0f}% of the pressure drag. '
                 'A tapered tail (boat-tailing) or a sharp trailing edge usually raises base pressure.',
                 {'pressure_drag_share': {'value': share, 'unit': '1'}, 'mean_cp': {'value': mean_cp, 'unit': '1'},
                  'area_m2': {'value': float(self.area[mask].sum()), 'unit': 'm²'}},
                 anchor, ['surface', 'pressure_cloud'], 'Cp', [-1, 1], 'Base pressure in steady RANS is sensitive to wake modelling.')

    def part_ranking(self):
        parts = [p for p in self.forces.get('parts', []) if p.get('share_of_drag') is not None]
        if len(parts) < 2:
            return
        ranked = sorted(parts, key=lambda p: -p['share_of_drag'])[:THRESHOLDS['part_ranking_v1']['top']]
        names = {v: k for k, v in self.part_names.items()}
        for i, part in enumerate(ranked):
            index = names.get(part['name'])
            mask = self.f['part_index'] == index if index is not None else np.zeros(len(self.area), bool)
            if not mask.any():
                continue
            anchor = self.anchor(mask)
            self.add('part_ranking_v1', i, 'drag', 'info', 'measured', f"'{part['name']}' carries {part['share_of_drag']*100:.0f}% of the drag",
                     f"This part produces {part['wind']['drag_n']:.3g} N of drag "
                     f"({part['wind']['pressure_drag_n']:.3g} N pressure, {part['wind']['viscous_drag_n']:.3g} N friction).",
                     {'drag_n': {'value': part['wind']['drag_n'], 'unit': 'N'}, 'share_of_drag': {'value': part['share_of_drag'], 'unit': '1'}},
                     anchor, ['surface'], 'Cp', [-1, 1], 'Part loads include interference from neighbouring parts.')
        for i, part in enumerate(p for p in parts if p['wind']['drag_n'] < 0):
            index = names.get(part['name'])
            mask = self.f['part_index'] == index if index is not None else np.zeros(len(self.area), bool)
            if mask.any():
                self.add('part_ranking_v1', 10+i, 'drag', 'info', 'measured', f"'{part['name']}' produces thrust",
                         f"The flow pushes this part forward ({part['wind']['drag_n']:.3g} N), typically from low pressure on a forward-facing surface.",
                         {'drag_n': {'value': part['wind']['drag_n'], 'unit': 'N'}}, self.anchor(mask), ['surface'], 'Cp', [-1, 1],
                         'Part loads include interference from neighbouring parts.')

    def wake_size(self):
        cloud = next((l for l in self.layers.get('layers', []) if l.get('id') == 'pressure_cloud'), None)
        if not cloud or not cloud.get('blobs'):
            return
        blob = cloud['blobs'][0]
        low, high = np.asarray(blob['min']), np.asarray(blob['max'])
        corners = np.array([[x, y, z] for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])])
        length = float(np.ptp(corners@self.drag))
        model = self.size/np.sqrt(3)
        if length < THRESHOLDS['wake_size_v1']['min_length_ratio']*model:
            return
        anchor = {'point': blob['centroid'], 'normal': (-self.side).tolist(), 'radius_m': length/2, 'part': None}
        self.add('wake_size_v1', 0, 'wake', 'info', 'measured', 'Total-pressure loss behind the model',
                 f'The largest region where the flow has lost its total pressure extends {length:.3g} m downstream '
                 f'({length/model:.1f} model lengths). A smaller loss region usually means lower pressure drag.',
                 {'length_m': {'value': length, 'unit': 'm'}, 'surface_area_m2': {'value': blob['area_m2'], 'unit': 'm²'}},
                 anchor, ['surface', 'pressure_cloud'], 'Cp', [-1, 1], 'The extent of the loss region depends on wake mesh resolution.')

    def lift_balance(self):
        coefficients = self.forces.get('coefficients') or {}
        split = self.forces.get('lift_split') or {}
        front, rear = coefficients.get('cl_front'), coefficients.get('cl_rear')
        if front is None or rear is None or not self.spec.use_case or self.spec.use_case.kind != 'aerodynamics':
            return
        if abs(front - rear) < THRESHOLDS['lift_balance_v1']['min_difference']:
            return
        anchor = {'point': self.center.tolist(), 'normal': self.lift.tolist(), 'radius_m': self.size/4, 'part': None}
        heavier = 'front' if front > rear else 'rear'
        self.add('lift_balance_v1', 0, 'lift', 'suggestion', 'measured', f'Lift is concentrated at the {heavier}',
                 f'Front lift coefficient {front:.3f} against rear {rear:.3f} (split basis: {split.get("basis")}). '
                 'An imbalance shifts load between the axles at speed.',
                 {'cl_front': {'value': front, 'unit': '1'}, 'cl_rear': {'value': rear, 'unit': '1'}},
                 anchor, ['surface'], 'Cp', [-1, 1], 'Without axle positions the split uses the model ends.')

    def yplus(self):
        if 'yPlus' not in self.f or not np.isfinite(self.f['yPlus']).any() or self.spec.flow.turbulence == 'laminar':
            return
        t = THRESHOLDS['yplus_v1']
        y = self.f['yPlus']
        if self.spec.mesh.wall_treatment == 'resolved':
            outside = np.isfinite(y) & (y > t['resolved_max']); band = f"y+ ≤ {t['resolved_max']}"
        else:
            low, high = t['wall_function']
            outside = np.isfinite(y) & ((y < low) | (y > high)); band = f'{low} ≤ y+ ≤ {high}'
        fraction = float(self.area[outside].sum()/self.wetted)
        if fraction <= t['max_fraction_outside']:
            return
        index = np.flatnonzero(outside)
        labels = clusters(self.f['centroid'][index], self.spacing)
        largest = max(np.unique(labels), key=lambda l: self.area[index[labels == l]].sum())
        mask = np.zeros(len(self.area), bool); mask[index[labels == largest]] = True
        self.add('yplus_v1', 0, 'mesh', 'warning', 'measured', 'Near-wall resolution outside the wall-treatment range',
                 f'{fraction*100:.0f}% of the wetted area has y+ outside {band}. Friction drag is least reliable there; '
                 'adjust the first layer thickness or the accuracy tier.',
                 {'area_fraction': {'value': fraction, 'unit': '1'}}, self.anchor(mask), ['surface'], 'yPlus',
                 [0, 300] if self.spec.mesh.wall_treatment != 'resolved' else [0, 5],
                 'This is a numerical-quality observation; it does not change the numerical status by itself.')

    def unsteadiness(self):
        signals = (self.convergence or {}).get('signals') or {}
        worst = max(((name, s.get('normalised_std', 0)) for name, s in signals.items()), key=lambda x: x[1], default=None)
        if not worst or worst[1] <= THRESHOLDS['unsteadiness_v1']['max_normalised_std']:
            return
        anchor = {'point': self.center.tolist(), 'normal': (-self.drag).tolist(), 'radius_m': self.size/4, 'part': None}
        self.add('unsteadiness_v1', 0, 'convergence', 'suggestion', 'measured', 'Loads keep fluctuating',
                 f'{worst[0]} fluctuates by {worst[1]*100:.1f}% of its mean over the final iterations. The flow may be inherently '
                 'unsteady (vortex shedding); a transient simulation would resolve it, and the averaged steady value is an approximation.',
                 {'normalised_std': {'value': worst[1], 'unit': '1'}}, anchor, ['surface', 'q_criterion'], 'Cp', [-1, 1],
                 'Steady solvers cannot represent periodic shedding; fluctuations are not a time history.')

    def design_advice(self):
        if not getattr(self.spec, 'design', None) or not self.spec.design.enabled or 'sensitivity' not in self.f:
            return
        sens = np.asarray(self.f['sensitivity'], float)
        finite = np.isfinite(sens)
        if not finite.any():
            return
        t = THRESHOLDS['design_advice_v1']
        # The strongest inward advice: faces at or below the 10th percentile of the negative values.
        negative = sens[finite & (sens < 0)]
        if not len(negative):
            return
        cutoff = float(np.quantile(negative, 0.1))
        mask = finite & (sens <= cutoff)
        if float(self.area[mask].sum()) / self.wetted < t['min_area_fraction']:
            mask = finite & (sens < 0)
            if float(self.area[mask].sum()) / self.wetted < t['min_area_fraction']:
                return
        labels = clusters(self.f['centroid'][mask], self.spacing)
        chosen = mask.copy()
        if len(labels):
            largest = max(np.unique(labels), key=lambda label: self.area[np.flatnonzero(mask)[labels == label]].sum())
            chosen = np.zeros(len(self.area), bool)
            chosen[np.flatnonzero(mask)[labels == largest]] = True
        weights = self.area[chosen]
        mean = float(np.sum(sens[chosen] * weights) / max(weights.sum(), 1e-300))
        per_mm = mean * 0.001
        objective = self.spec.design.objective
        span = float(max(abs(np.nanmin(sens[finite])), abs(np.nanmax(sens[finite]))))
        self.add('design_advice_v1', 0, 'design', 'suggestion', 'heuristic',
                 f'Moving inward here is predicted to reduce {objective}',
                 f'A 1 mm inward move of this region is predicted to change {objective} by {per_mm:.3g} N. '
                 f'Blue on the sensitivity map is inward; red is outward. The estimate is the face sensitivity '
                 f'({mean:.3g} N/m) times 0.001.',
                 {'delta_n_per_mm': {'value': per_mm, 'unit': 'N'}, 'sensitivity_n_per_m': {'value': mean, 'unit': 'N/m'}},
                 self.anchor(chosen), ['surface'], 'sensitivity', [-span, span],
                 f'{("Laminar" if self.spec.flow.turbulence == "laminar" else "Frozen k–ω SST")} adjoint. '
                 'This is a linear prediction, not a finite-difference check and not an experimental result. '
                 'It does not change numerical quality or validation status.')

    def run(self):
        for rule in (self.separation, self.stagnation, self.suction_peak, self.base_pressure, self.part_ranking,
                     self.wake_size, self.lift_balance, self.yplus, self.unsteadiness, self.design_advice):
            rule()
        order = {'warning': 0, 'suggestion': 1, 'info': 2}
        self.items.sort(key=lambda i: (order[i['severity']], i['id']))
        return self.items


def generate(faces, spec, **inputs):
    items = Engine(faces, spec, **inputs).run()
    digest = hashlib.sha256()
    for key in sorted(faces):
        digest.update(key.encode()); digest.update(np.ascontiguousarray(faces[key]).tobytes())
    digest.update(json.dumps(inputs.get('forces') or {}, sort_keys=True, default=str).encode())
    return {'schema': 'gustsim.insights/1', 'engine_version': ENGINE_VERSION, 'inputs_sha256': digest.hexdigest(),
            'disclaimer': DISCLAIMER, 'insights': items, 'thresholds': THRESHOLDS}


def load_faces(path):
    with np.load(path, allow_pickle=False) as data:
        return {k: data[k] for k in data.files if k != 'patch_names'}
