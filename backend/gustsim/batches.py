"""Batch runs: variants, sweeps and grids over whitelisted setup paths.

Each member is an ordinary simulation (own snapshots, own evidence). Members whose volume mesh
would be identical share one mesh: the first meshes, the others wait for its review and solve
on it, which is what makes a speed or RPM sweep cheap.
"""
import itertools
import json
import time
from . import db, mesh_tiers, pipeline, validation, workflow
from .models import Batch, SimulationSpec


def assign(data, path, value):
    if path == 'geometry_id':
        data['geometry_id'] = value
        return
    section, key = path.split('.')
    if path == 'mesh.tier':
        value = str(value)
    elif not isinstance(value, (int, float)):
        raise ValueError(f'{path} needs a number')
    data[section][key] = value


def expand(batch: Batch):
    base = batch.base.model_dump(mode='json')
    members = []
    if batch.kind == 'variants':
        for variant in batch.variants:
            data = json.loads(json.dumps(base))
            for path, value in variant.overrides.items():
                assign(data, path, value)
            members.append((variant.label, dict(variant.overrides), data))
    else:
        for combination in itertools.product(*[p.values for p in batch.parameters]):
            data = json.loads(json.dumps(base))
            values = {}
            for parameter, value in zip(batch.parameters, combination):
                assign(data, parameter.path, value)
                values[parameter.path] = value
            label = ', '.join(f'{k.split(".")[-1]}={v}' for k, v in values.items())
            members.append((label, values, data))
    out = []
    for label, values, data in members:
        data['name'] = f'{batch.name} · {label}'
        spec = SimulationSpec.model_validate(data)
        if 'environment.ride_height_m' in values:
            # The ride height is geometry, not a label: move the ground (bottom face) for this member.
            if spec.environment.ground == 'none':
                raise ValueError('Ride height applies only on a ground plane; choose On ground or Moving ground first')
            from .presets import place_ground
            place_ground(spec, db.geometry(spec.geometry_id)['bounds'], float(values['environment.ride_height_m']))
        spec = mesh_tiers.apply(spec)
        out.append((label, values, spec))
    return out


def create(batch: Batch, project_id=None):
    members = expand(batch)
    reports = []
    for label, _, spec in members:
        report = validation.validate(spec)
        if not report['valid']:
            raise ValueError(f'{label}: ' + '; '.join(report['errors']))
        if any(f['status'] == 'review' for f in report['findings']) and not batch.acknowledge_review:
            raise ValueError(f'{label} has setup findings to review; acknowledge them for the batch or configure it individually')
        reports.append(report)
    study_id = db.create_study(batch.name, batch.model_dump(mode='json'), kind='batch', definition_version=2)
    created, mesh_owner = [], {}
    for (label, values, spec), report in zip(members, reports):
        identity = workflow.mesh_identity(spec) if spec.schema_version >= 2 else report['configuration_id']
        plan = {'auto_review': batch.auto_review, 'study_id': study_id, 'label': label, 'values': values,
                'acknowledged_configuration_id': report['configuration_id'] if batch.acknowledge_review else None}
        owner = mesh_owner.get(identity)
        if owner:
            plan['mesh_from'] = owner
        simulation = pipeline.create(spec, plan=plan, project_id=project_id or spec.project_id, name=spec.name,
                                     tags=[*batch.tags, f'batch:{study_id[:8]}'], queue_mesh=owner is None)
        mesh_owner.setdefault(identity, simulation['id'])
        created.append(simulation)
    return detail(study_id)


def detail(study_id):
    with db.connection() as c:
        row = c.execute('SELECT * FROM studies WHERE id=?', (study_id,)).fetchone()
        if row is None:
            raise KeyError('Batch not found')
        ids = [r[0] for r in c.execute("SELECT id FROM simulations WHERE json_extract(plan,'$.study_id')=? ORDER BY created", (study_id,))]
    definition = json.loads(row['spec'])
    members = [pipeline.describe(pipeline.refresh(i)) for i in ids]
    for member, simulation_id in zip(members, ids):
        member['batch'] = {k: pipeline.get(simulation_id)['plan'].get(k) for k in ('label', 'values', 'mesh_from')}
    series = []
    parameters = [p['path'] for p in definition.get('parameters', [])]
    for path in parameters[:1]:
        for metric in ('cd', 'drag_n', 'cl', 'lift_n'):
            points = [{'x': m['batch']['values'].get(path), 'y': m['headline'].get(metric), 'simulation_id': m['id']}
                      for m in members if m['headline'].get(metric) is not None]
            if points:
                series.append({'x': path, 'y': metric, 'points': sorted(points, key=lambda p: (str(type(p['x'])), p['x']))})
    return {'id': study_id, 'name': row['name'], 'kind': row['kind'], 'created': row['created'], 'definition': definition,
            'members': members, 'series': series,
            'shared_meshes': sorted({m['batch']['mesh_from'] for m in members if m['batch'].get('mesh_from')})}


def listing():
    with db.connection() as c:
        return [{'id': r['id'], 'name': r['name'], 'created': r['created'], 'kind': r['kind']}
                for r in c.execute("SELECT id,name,created,kind FROM studies WHERE kind='batch' ORDER BY created DESC")]
