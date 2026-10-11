"""Populate a *scratch* GUSTSIM_DATA with UI fixture records (development and e2e only).

The completed simulation uses prescribed synthetic fields on a block (tests/foamcase.py), not
a flow solution; its name and tags say so. Never point this at a real data volume.
Usage: GUSTSIM_DATA=<empty scratch dir> python scripts/ui_fixture_data.py
"""
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
sys.path.insert(0, str(ROOT/'tests'))
from gustsim import config, db, geometry, pipeline, presets, quality  # noqa: E402
from gustsim.jobs import package as package_job  # noqa: E402
from gustsim.jobs import report as report_job  # noqa: E402
from gustsim.models import Primitive, PresetRequest, SimulationSpec  # noqa: E402
import foamcase  # noqa: E402

if (config.DATA/'gustsim.sqlite3').exists() and not os.environ.get('GUSTSIM_FIXTURE_APPEND'):
    raise SystemExit(f'{config.DATA} already has a database; use an empty scratch directory')
db.initialize()


def block_spec(speed):
    g = geometry.add_primitive(Primitive(shape='box', name='block', center=(2, 1, 1), dimensions=(1, 1, 1)))
    mesh, groups = geometry.load(g['id'])
    faces = {}
    for patch in g['patches']:
        normal = mesh.face_normals[groups == patch['group']].mean(axis=0)
        axis = int(max(range(3), key=lambda i: abs(normal[i])))
        faces[(axis, 1 if normal[axis] > 0 else -1)] = patch['name']
    data = presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=[p['name'] for p in g['patches']], speed=speed, direction=(-1, 0, 0)))['spec']
    data['use_case']['confirmed'] = True; data['use_case']['references_confirmed'] = True
    data['domain'] = {'minimum': (0, 0, 0), 'maximum': (4, 2, 2), 'fluid_point': (0.5, 0.25, 0.25)}
    data['outputs'] = {'sections_per_axis': (8, 4, 4), 'rakes': (5, 5), 'q_levels': (0.5,), 'noise': True, 'friction_lines': True, 'renders': True}
    data['name'] = f'UI fixture block · {speed:g} m/s (synthetic fields)'
    return g, SimulationSpec.model_validate(data), faces


def completed(speed):
    g, spec, faces = block_spec(speed)
    simulation = pipeline.create(spec, name=spec.name, tags=['ui-fixture', 'synthetic'], queue_mesh=False)
    mesh = db.enqueue(spec.model_dump(), 'mesh', simulation_id=simulation['id'])
    findings = [{'code': c, 'status': 'pass', 'detail': 'fixture'} for c in pipeline.REQUIRED_MESH_EVIDENCE]
    mesh_root = config.DATA/'runs'/mesh['id']; mesh_root.mkdir(parents=True, exist_ok=True)
    db.update(mesh['id'], status='completed', stage='completed', result={'mesh_check': 'passed', 'mesh_available': True, 'cell_count': 960,
                                                                           'mesh_summary': {'findings': findings, 'method': 'serial'}})
    solve = db.enqueue(spec.model_dump(), 'solve', parent_id=mesh['id'], simulation_id=simulation['id'])
    case = config.DATA/'runs'/solve['id']
    foamcase.build(case, cells=(32, 16, 16), body=((1.5, 2.5), (0.5, 1.5), (0.5, 1.5)), body_faces=faces, speed=speed)
    (case/'force_groups.json').write_text(json.dumps([{'name': 'part_0', 'patches': [p['name'] for p in g['patches']]}]))
    drag = spec.references.drag_axis
    for name in ('loads', 'group_part_0'):
        folder = case/f'postProcessing/{name}/0'; folder.mkdir(parents=True)
        with (folder/'force.dat').open('w') as f, (folder/'moment.dat').open('w') as m:
            for i in range(1, 801):
                total = (0.5*1.2*speed**2)*(1.05 + 0.4*math.exp(-i/80))
                v = lambda s: ' '.join(f'{x*s:.9g}' for x in drag)
                f.write(f'{i} ({v(total)}) ({v(0.85*total)}) ({v(0.15*total)})\n'); m.write(f'{i} (0 0.2 0) (0 0.2 0) (0 0 0)\n')
    residuals = case/'postProcessing/residuals/0'; residuals.mkdir(parents=True)
    with (residuals/'solverInfo.dat').open('w') as r:
        r.write('# Time U_solver Ux_initial Ux_final Ux_iters p_solver p_initial p_final p_iters\n')
        for i in range(1, 801):
            r.write(f'{i} smoothSolver {0.2*math.exp(-i/120):.6g} 1e-07 2 GAMG {0.5*math.exp(-i/150):.6g} 1e-07 4\n')
    (case/'logs').mkdir(exist_ok=True)
    (case/'logs/solve.log').write_text('Time = 800\nEnd\n'); (case/'logs/check.log').write_text('Mesh OK.\ncells: 960\n')
    result = quality.analyze(case, spec)
    result.update(fields_available=True, case_available=True, cell_count=8192, mesh_check='passed')
    db.update(solve['id'], status='completed', stage='completed', result=result)
    package_job.build(solve['id'], in_process=True)
    report_job.build(solve['id'])
    package_id = package_job.job_id(solve['id'])
    db.enqueue({'run_id': solve['id']}, 'package', parent_id=solve['id'], identifier=package_id)
    db.update(package_id, status='completed', stage='completed')
    return pipeline.refresh(simulation['id'])


if __name__ == '__main__':
    first = completed(20.0)
    second = completed(25.0)
    g, spec, _ = block_spec(30.0)
    spec.name = 'UI fixture block · queued (no worker)'
    queued = pipeline.create(spec, name=spec.name, tags=['ui-fixture'])
    print(json.dumps({'data': str(config.DATA), 'completed': [first['id'], second['id']], 'queued': queued['id']}, indent=2))
