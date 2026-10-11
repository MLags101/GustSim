"""Compare solved runs: aligned metrics, deltas against a baseline, configuration differences,
comparability warnings and surface-field deltas mapped between two result surfaces."""
import hashlib
import json
import numpy as np
from . import config, db
from .models import SimulationSpec

COLUMNS = [
    ('cd', 'Cd', '1', 'coefficients'), ('cl', 'Cl', '1', 'coefficients'), ('cs', 'Cs', '1', 'coefficients'),
    ('cda_m2', 'CdA', 'm²', 'coefficients'), ('l_over_d', 'L/D', '1', 'coefficients'),
    ('cl_front', 'Cl front', '1', 'coefficients'), ('cl_rear', 'Cl rear', '1', 'coefficients'),
    ('drag_n', 'Drag', 'N', 'forces'), ('lift_n', 'Lift', 'N', 'forces'), ('side_n', 'Side force', 'N', 'forces'),
    ('pressure_drag_n', 'Pressure drag', 'N', 'forces'), ('viscous_drag_n', 'Friction drag', 'N', 'forces'),
    ('roll_nm', 'Roll moment', 'N·m', 'moments'), ('pitch_nm', 'Pitch moment', 'N·m', 'moments'), ('yaw_nm', 'Yaw moment', 'N·m', 'moments'),
    ('frontal_area_m2', 'Frontal area', 'm²', 'geometry'), ('speed_m_s', 'Speed', 'm/s', 'setup'), ('cells', 'Cells', '1', 'mesh'),
    ('thrust_n', 'Thrust', 'N', 'rotor'), ('torque_nm', 'Torque', 'N·m', 'rotor'), ('shaft_power_w', 'Shaft power', 'W', 'rotor'),
    ('efficiency', 'Efficiency', '1', 'rotor'), ('pressure_drop_pa', 'Pressure drop', 'Pa', 'internal'),
    ('outlet_mass_flow_kgs', 'Mass flow', 'kg/s', 'internal'), ('total_pressure_loss_pa', 'Total-pressure loss', 'Pa', 'internal'),
]


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
    except ValueError:
        return None


def values(run):
    case = config.DATA/'runs'/run['id']
    metrics = run['result'].get('metrics') or {}
    out = {k: metrics.get(k) for k in ('thrust_n', 'torque_nm', 'shaft_power_w', 'efficiency', 'pressure_drop_pa',
                                       'outlet_mass_flow_kgs', 'total_pressure_loss_pa', 'cd', 'cl', 'drag_n', 'lift_n')}
    out['side_n'] = metrics.get('side_force_n')
    out['cells'] = run['result'].get('cell_count')
    out['speed_m_s'] = run['spec']['flow']['speed']
    forces = read_json(case/'results/forces.json')
    if forces:
        wind = forces['frames']['wind']['total']
        coefficients = forces['coefficients']
        out.update({k: coefficients.get(k) for k in ('cd', 'cl', 'cs', 'cda_m2', 'l_over_d', 'cl_front', 'cl_rear')})
        out.update(drag_n=wind['force_n']['drag'], lift_n=wind['force_n']['lift'], side_n=wind['force_n']['side'],
                   pressure_drag_n=wind['pressure_n']['drag'], viscous_drag_n=wind['viscous_n']['drag'],
                   roll_nm=wind['moment_nm']['roll'], pitch_nm=wind['moment_nm']['pitch'], yaw_nm=wind['moment_nm']['yaw'],
                   frontal_area_m2=forces.get('frontal_area_m2'))
    return out


def flatten(data, prefix=''):
    items = {}
    if isinstance(data, dict):
        for key, value in data.items():
            items.update(flatten(value, f'{prefix}.{key}' if prefix else key))
    elif isinstance(data, list) and data and all(isinstance(v, dict) for v in data):
        for i, value in enumerate(data):
            items.update(flatten(value, f'{prefix}[{i}]'))
    else:
        items[prefix] = data
    return items


def config_diff(specs):
    flat = [flatten({k: v for k, v in s.items() if k not in ('name', 'project_id')}) for s in specs]
    keys = sorted(set().union(*flat))
    out = []
    for key in keys:
        column = [f.get(key) for f in flat]
        if any(json.dumps(c, sort_keys=True) != json.dumps(column[0], sort_keys=True) for c in column[1:]):
            out.append({'path': key, 'values': column})
    return out


def compare(run_ids, baseline=None, columns=None):
    if not 2 <= len(run_ids) <= 12:
        raise ValueError('Compare two to twelve runs')
    runs = [db.run(i) for i in run_ids]
    for run in runs:
        if run['kind'] != 'solve':
            raise ValueError('Only solved runs can be compared')
    baseline = baseline or run_ids[0]
    if baseline not in run_ids:
        raise ValueError('The baseline must be one of the compared runs')
    data = {r['id']: values(r) for r in runs}
    chosen = [c for c in COLUMNS if (columns is None or c[0] in columns) and any(data[r][c[0]] is not None for r in data)]
    base = data[baseline]
    rows = []
    geometry = {}
    for run in runs:
        spec = run['spec']
        try:
            meta = db.geometry(spec['geometry_id'])
        except KeyError:
            meta = {}
        geometry[run['id']] = meta.get('geometry_fidelity') or 'imported'
        deltas = {}
        for key, *_ in chosen:
            a, b = base.get(key), data[run['id']].get(key)
            if a is None or b is None:
                deltas[key] = None
            else:
                deltas[key] = {'abs': b - a, 'rel': (b - a)/abs(a) if abs(a) > 1e-12 else None}
        rows.append({'run_id': run['id'], 'simulation_id': run.get('simulation_id'), 'name': spec.get('name'),
                     'geometry_id': spec['geometry_id'], 'fidelity': geometry[run['id']], 'tier': spec['mesh'].get('tier', 'custom'),
                     'status': {'execution': run['status'], 'numerical': run['result'].get('numerical_status', 'unavailable'),
                                'validation': run['result'].get('validation_status', 'experimental_validation_pending')},
                     'values': {key: data[run['id']].get(key) for key, *_ in chosen}, 'deltas': deltas})
    warnings = []
    references = {json.dumps([r['spec']['references'].get(k) for k in ('area', 'length', 'drag_axis', 'lift_axis')]) for r in runs}
    if len(references) > 1:
        warnings.append({'code': 'references', 'detail': 'Reference area, length or axes differ; coefficients are normalised differently.'})
    if len(set(geometry.values())) > 1:
        warnings.append({'code': 'fidelity', 'detail': 'Some runs use a wrapped geometry approximation and others the imported CAD.'})
    if len({r['spec']['mesh'].get('tier', 'custom') for r in runs}) > 1:
        warnings.append({'code': 'tier', 'detail': 'Accuracy tiers differ; part of each difference is mesh resolution.'})
    if any(r['result'].get('numerical_status') != 'checks_passed' for r in runs):
        warnings.append({'code': 'numerical', 'detail': 'At least one run has numerical findings to review; differences may reflect convergence rather than design.'})
    try:
        from .capabilities import experimental_used
        maturity = {bool(experimental_used(SimulationSpec.model_validate(r['spec']))) for r in runs}
        if True in maturity:
            warnings.append({'code': 'experimental', 'detail': 'Experimental physics is involved in at least one run.'})
    except Exception:
        pass
    return {'baseline': baseline, 'columns': [{'id': c[0], 'label': c[1], 'unit': c[2], 'group': c[3]} for c in chosen],
            'rows': rows, 'config_diff': config_diff([r['spec'] for r in runs]), 'warnings': warnings,
            'comparable': all(r['status'] == 'completed' for r in runs) and not any(w['code'] in ('references',) for w in warnings),
            'note': 'A difference is meaningful only when both runs pass their own numerical checks; neither is an experimentally validated result.'}


def surface_delta(run_a, run_b, field='Cp'):
    """Map run B's surface field onto run A's surface points (nearest point within twice the
    local spacing) and write a VTP with arrays a, b, delta and valid."""
    from vtkmodules.vtkIOXML import vtkXMLPolyDataReader, vtkXMLPolyDataWriter
    from vtkmodules.vtkCommonDataModel import vtkPointLocator
    from vtkmodules.util.numpy_support import vtk_to_numpy, numpy_to_vtk
    key = hashlib.sha256(f'{run_a}:{run_b}:{field}'.encode()).hexdigest()[:24]
    folder = config.DATA/'compare'/key
    target = folder/'delta.vtp'
    meta_path = folder/'delta.json'
    if target.is_file() and meta_path.is_file():
        return json.loads(meta_path.read_text())

    def read(run_id):
        path = config.DATA/'runs'/run_id/'results/package/surface/object.vtp'
        if not path.is_file():
            raise ValueError(f'Run {run_id} has no results package surface')
        reader = vtkXMLPolyDataReader(); reader.SetFileName(str(path)); reader.Update()
        return reader.GetOutput()
    a, b = read(run_a), read(run_b)
    for poly, name in ((a, run_a), (b, run_b)):
        if poly.GetPointData().GetArray(field) is None:
            raise ValueError(f'Run {name} has no surface field {field}')
    locator = vtkPointLocator(); locator.SetDataSet(b); locator.BuildLocator()
    points_a = vtk_to_numpy(a.GetPoints().GetData()).astype(float)
    points_b = vtk_to_numpy(b.GetPoints().GetData()).astype(float)
    values_a = vtk_to_numpy(a.GetPointData().GetArray(field)).astype(float)
    values_b = vtk_to_numpy(b.GetPointData().GetArray(field)).astype(float)
    cells = vtk_to_numpy(a.GetPolys().GetConnectivityArray()).reshape(-1, 3) if a.GetPolys().GetNumberOfCells() else None
    spacing = float(np.median(np.linalg.norm(points_a[cells[:, 0]] - points_a[cells[:, 1]], axis=1))) if cells is not None and len(cells) else 1e-3
    nearest = np.array([locator.FindClosestPoint(p) for p in points_a])
    distance = np.linalg.norm(points_b[nearest] - points_a, axis=1)
    valid = distance <= 2*max(spacing, 1e-9)
    mapped = np.where(valid, values_b[nearest], np.nan)
    out = type(a)(); out.DeepCopy(a)
    for i in reversed(range(out.GetPointData().GetNumberOfArrays())):
        out.GetPointData().RemoveArray(out.GetPointData().GetArrayName(i))
    for name, array, dtype in (('a', values_a, np.float32), ('b', mapped, np.float32), ('delta', mapped - values_a, np.float32), ('valid', valid, np.uint8)):
        vtk_array = numpy_to_vtk(np.ascontiguousarray(array, dtype=dtype), deep=1); vtk_array.SetName(name)
        out.GetPointData().AddArray(vtk_array)
    folder.mkdir(parents=True, exist_ok=True)
    writer = vtkXMLPolyDataWriter(); writer.SetFileName(str(target)); writer.SetInputData(out)
    writer.SetDataModeToBinary(); writer.SetHeaderTypeToUInt32(); writer.SetCompressorTypeToZLib(); writer.Write()
    finite = (mapped - values_a)[valid]
    result = {'key': key, 'url': f'/api/compare/files/{key}/delta.vtp', 'field': field, 'arrays': ['a', 'b', 'delta', 'valid'],
              'coverage': float(valid.mean()) if len(valid) else 0.0,
              'delta_range': [float(finite.min()), float(finite.max())] if len(finite) else None,
              'same_geometry': db.run(run_a)['spec']['geometry_id'] == db.run(run_b)['spec']['geometry_id'],
              'note': 'Run B mapped onto run A by nearest surface point; unmatched points are marked invalid.'}
    meta_path.write_text(json.dumps(result), encoding='utf-8')
    return result
