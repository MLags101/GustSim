"""Results package plan (input to postpackage.py) and the results manifest the viewer reads.

The manifest is built in plain Python from layers.json, so its contract is tested without
ParaView. URLs are relative to /api/runs/{run_id}/files/.
"""
import json
import os
import time
import numpy as np
from . import capabilities, config

PACKAGE = 'results/package'
# Display metadata for every array the package writes. Units are SI; coefficients are 1.
ARRAYS = {
    'Cp': {'unit': '1', 'label': 'Pressure coefficient', 'colormap': 'cool_to_warm', 'default_range': [-1, 1], 'diverging': True},
    'Cpt': {'unit': '1', 'label': 'Total pressure coefficient', 'colormap': 'rainbow', 'default_range': [0, 1]},
    'pressure_pa': {'unit': 'Pa', 'label': 'Static pressure (gauge)', 'colormap': 'cool_to_warm', 'diverging': True},
    'wall_shear_pa': {'unit': 'Pa', 'label': 'Wall shear stress (fluid on wall)', 'colormap': 'rainbow'},
    'wall_shear_mag': {'unit': 'Pa', 'label': 'Wall shear stress', 'colormap': 'rainbow'},
    'Cf': {'unit': '1', 'label': 'Skin-friction coefficient', 'colormap': 'rainbow', 'default_range': [0, 0.01]},
    'yPlus': {'unit': '1', 'label': 'y+', 'colormap': 'rainbow'},
    'sensitivity': {'unit': 'N/m', 'label': 'Design advice (inward − / outward +)', 'colormap': 'advice', 'diverging': True},
    'elevation_m': {'unit': 'm', 'label': 'Free-surface elevation', 'colormap': 'cool_to_warm', 'diverging': True},
    'alpha_water': {'unit': '1', 'label': 'Water fraction', 'colormap': 'cool_to_warm', 'default_range': [0, 1]},
    'U_mag': {'unit': 'm/s', 'label': 'Velocity magnitude', 'colormap': 'rainbow'},
    'IntegrationTime': {'unit': 's', 'label': 'Integration time', 'colormap': 'rainbow'},
    'part_index': {'unit': '1', 'label': 'Part', 'categorical': True},
    'patch_index': {'unit': '1', 'label': 'Surface', 'categorical': True},
    'SeedIds': {'unit': '1', 'label': 'Seed', 'categorical': True},
    'Normals': {'unit': '1', 'label': 'Normals', 'hidden': True},
}
DEFINITIONS = {
    'Cp': '(p - p_ref) / q_inf; p_ref is the outlet gauge reference (0), q_inf = ½ ρ U_inf²',
    'Cpt': '(p + ½ρ|U|² - p_ref) / q_inf; 1 in the undisturbed freestream, 0 where all dynamic pressure is lost',
    'Cf': '|τ_w| / q_inf',
    'wall_shear_pa': 'Stress of the fluid on the wall, aligned with the near-wall flow (−ρ × OpenFOAM wallShearStress)',
    'pressure_pa': 'ρ × OpenFOAM kinematic pressure, gauge',
    'normals': 'Surface normals point out of the body into the fluid',
    'sensitivity': 'Adjoint dJ/dn, N/m of inward normal displacement. Negative (blue): move inward to reduce the objective. Positive (red): move outward. A 1 mm estimate is the value times 0.001. Linear adjoint prediction, not a measurement.',
}


def leaf_parts(meta):
    parts = meta.get('parts') or []
    parents = {p.get('parent_id') for p in parts}
    return [p for p in parts if p['id'] not in parents]


def object_patches(spec, meta):
    names = {p['name'] for p in meta['patches']}
    walls = [b.patch for b in spec.boundaries if b.patch in names and b.kind in ('wall', 'moving_wall', 'rotating_wall')]
    if spec.use_case and spec.use_case.force_patches:
        chosen = [p for p in spec.use_case.force_patches if p in names]
        return sorted(set(chosen) | set(walls)) if spec.use_case.kind == 'pipe' else sorted(chosen or walls)
    return sorted(walls)


def part_table(meta, patches):
    parts = leaf_parts(meta)
    table, patch_parts = [], {}
    if parts:
        for index, part in enumerate(parts):
            members = [p for p in part['patches'] if p in patches]
            if members:
                table.append({'index': index, 'id': part['id'], 'name': part['name'], 'patches': members})
                for p in members:
                    patch_parts[p] = index
    for patch in patches:
        if patch not in patch_parts:
            patch_parts[patch] = len(table)
            table.append({'index': len(table), 'id': patch, 'name': patch, 'patches': [patch]})
    return table, patch_parts


def flow_frame(spec):
    velocity = np.asarray(spec.flow.velocity(), float)
    speed = float(np.linalg.norm(velocity))
    drag = velocity/speed if speed > 0 else np.asarray(spec.references.drag_axis, float)
    lift = np.asarray(spec.references.lift_axis, float)
    lift = lift - (lift@drag)*drag
    lift = lift/max(np.linalg.norm(lift), 1e-12)
    return drag, lift, np.cross(lift, drag), velocity, speed


def plan(spec, meta):
    patches = object_patches(spec, meta)
    table, patch_parts = part_table(meta, patches)
    drag, lift, side, velocity, speed = flow_frame(spec)
    if spec.mode == 'internal':
        inlets = [b for b in spec.boundaries if b.kind in ('velocity_inlet', 'flow_inlet', 'pressure_inlet')]
        if inlets and spec.flow.speed > 0:
            velocity = drag*spec.flow.speed
    return {'rho': spec.fluid.density, 'u_inf': velocity.tolist(), 'reference_length': spec.references.length,
            'reference_area': spec.references.area, 'drag_axis': drag.tolist(), 'lift_axis': lift.tolist(), 'side_axis': side.tolist(),
            'object_patches': patches, 'patch_parts': patch_parts, 'parts': table,
            'model_bounds': meta['bounds'], 'domain_bounds': [list(spec.domain.minimum), list(spec.domain.maximum)],
            'outputs': spec.outputs.model_dump(mode='json'), 'noise_levels_db': [60, 70, 80],
            'budget_bytes': int(float(os.environ.get('GUSTSIM_PACKAGE_BUDGET_MB', '400'))*1024*1024),
            'free_surface': spec.free_surface.model_dump(mode='json') if spec.free_surface else None}


def camera_presets(spec, meta):
    drag, lift, side, _, _ = flow_frame(spec)
    low, high = (np.asarray(b, float) for b in meta['bounds'])
    center = (low+high)/2
    distance = float(np.linalg.norm(high-low))*2.2
    def preset(direction, up):
        direction = np.asarray(direction, float)/np.linalg.norm(direction)
        return {'position': (center + direction*distance).tolist(), 'focal_point': center.tolist(), 'view_up': np.asarray(up, float).tolist()}
    return {'front': preset(-drag, lift), 'rear': preset(drag, lift), 'left': preset(side, lift), 'right': preset(-side, lift),
            'top': preset(lift, -drag), 'bottom': preset(-lift, -drag), 'iso': preset(-drag + side + lift*0.8, lift)}


def status_block(run, spec, meta):
    result = run.get('result') or {}
    return {'geometry': 'wrapped' if meta.get('geometry_fidelity') == 'wrapped' else 'imported',
            'execution': run.get('status'), 'numerical': result.get('numerical_status', 'unavailable'),
            'validation': result.get('validation_status', 'experimental_validation_pending'),
            'physics_maturity': 'experimental' if capabilities.experimental_used(spec) else 'core',
            'capabilities': capabilities.capabilities_used(spec)}


def array_entries(arrays):
    out = []
    for array in arrays:
        info = ARRAYS.get(array['name'], {'unit': None, 'label': array['name']})
        if info.get('hidden'):
            continue
        out.append({**array, **{k: v for k, v in info.items() if k != 'hidden'}})
    return out


def build(run, spec, meta, package_dir, documents=None):
    layers_path = package_dir/'layers.json'
    record = json.loads(layers_path.read_text(encoding='utf-8')) if layers_path.is_file() else {'layers': [], 'renders': [], 'missing': [{'id': 'package', 'reason': 'The package was not produced'}]}
    base = f'{PACKAGE}/'
    drag, lift, side, velocity, speed = flow_frame(spec)
    layers = []
    for layer in record['layers']:
        entry = {k: v for k, v in layer.items() if k not in ('path', 'items', 'arrays')}
        entry['arrays'] = array_entries(layer.get('arrays', []))
        if 'path' in layer:
            entry['url'] = base + layer['path']
            entry['format'] = 'vtp'; entry['compression'] = 'zlib'
        if layer.get('frames'):
            entry['frames'] = {**{k: v for k, v in layer['frames'].items() if k != 'data'}, 'url': base + layer['frames']['data'], 'format': 'float32le'}
        if layer['kind'] == 'section_stack':
            entry['items'] = [{**{k: v for k, v in i.items() if k != 'path'}, 'url': base + i['path']} for i in layer['items']]
        layers.append(entry)
    for layer in layers:
        if layer['id'] == 'surface':
            layer['default'] = True
    renders = [{**{k: v for k, v in r.items() if k != 'path'}, 'url': base + r['path']} for r in record.get('renders', [])]
    budget = sum(l.get('bytes', 0) for l in layers if l['kind'] in ('surface', 'lines', 'isosurface', 'streamlines'))
    q = 0.5*spec.fluid.density*spec.flow.speed**2
    parts, _ = part_table(meta, object_patches(spec, meta))
    return {'schema': 'gustsim.results/1', 'run_id': run['id'], 'simulation_id': run.get('simulation_id'), 'generated': time.time(),
            'status': status_block(run, spec, meta),
            'engine': {'openfoam': config.FOAM_VERSION, 'vtk': record.get('vtk_version')},
            'reference': {'rho': spec.fluid.density, 'u_inf': velocity.tolist(), 'speed_m_s': speed, 'q_inf_pa': q,
                          'area_m2': spec.references.area, 'length_m': spec.references.length, 'p_ref_pa': 0.0,
                          'frames': {'wind': {'drag': drag.tolist(), 'side': side.tolist(), 'lift': lift.tolist()}},
                          'definitions': DEFINITIONS},
            'bounds': {'model': meta['bounds'], 'domain': [list(spec.domain.minimum), list(spec.domain.maximum)]},
            'parts': parts, 'camera_presets': camera_presets(spec, meta), 'layers': layers, 'renders': renders,
            'thumbnail': base + 'renders/thumb.png' if (package_dir/'renders/thumb.png').is_file() else None,
            'documents': documents or {}, 'missing': [{'id': m['id'], 'reason': m['reason']} for m in record.get('missing', [])],
            'budget': {'initial_bytes': budget, 'limit_bytes': 40*1024*1024, 'within': budget <= 40*1024*1024},
            'time': record.get('time'),
            # 'time_averaged' for transient runs (fieldAverage means), else the steady solution.
            'basis': record.get('basis', 'steady')}
