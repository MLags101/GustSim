"""Morphing attempt. Copies the finished mesh into its own run so the steady solution is never overwritten.

adjointOptimisationFoam then moves the design boxes. The cycle before the objective rises is
exported as a new geometry revision (`fidelity: morphed`) and a verification simulation is
queued from that shape. The verification is an ordinary steady run; it does not prove the
adjoint prediction.
"""
import io
import json
import math
import shutil
from pathlib import Path

from .. import adjoint, config, db, geometry, presets
from ..models import PresetRequest, SimulationSpec
from ..worker import register, remove_processor_dirs


COPY = ('constant', 'system', '0', 'simulation.json', 'geometry.json', 'gustsim.foam', 'preflight.json')


def enqueue(solve_id):
    """Queue morphing once per solved run. A second call returns the existing attempt."""
    identifier = adjoint.enqueue_id(solve_id)
    try:
        return db.run(identifier)
    except KeyError:
        parent = db.run(solve_id)
        return db.enqueue(parent['spec'], 'optimise', parent_id=solve_id, simulation_id=parent.get('simulation_id'),
                          identifier=identifier)


def copy_case(source, target):
    target.mkdir(parents=True, exist_ok=True)
    for name in COPY:
        src = Path(source) / name
        if not src.exists():
            continue
        dest = target / name
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dest)


def merged_stl(paths):
    import trimesh
    meshes = []
    for path in paths:
        mesh = trimesh.load(path, force='mesh', process=True)
        if len(mesh.faces):
            meshes.append(mesh)
    if not meshes:
        raise ValueError('The morphing surface export has no triangles')
    combined = meshes[0] if len(meshes) == 1 else trimesh.util.concatenate(meshes)
    buffer = io.BytesIO()
    combined.export(buffer, file_type='stl')
    return buffer.getvalue()


def verification_spec(spec, revision):
    """A steady external case on the morphed revision. Patch names come from that revision."""
    velocity = spec.flow.velocity()
    norm = math.sqrt(sum(v * v for v in velocity)) or 1.0
    names = [patch['name'] for patch in revision['patches']]
    data = presets.generate(PresetRequest(
        geometry_id=revision['id'], kind='aerodynamics', patches=names, fluid=spec.fluid.name,
        turbulence=spec.flow.turbulence, speed=spec.flow.speed, direction=tuple(v / norm for v in velocity),
        lift_axis=spec.references.lift_axis, tier=spec.mesh.tier if spec.mesh.tier != 'custom' else 'regular'))['spec']
    check = SimulationSpec.model_validate(data)
    check.schema_version = 2
    check.design.enabled = False
    check.elements.design_boxes = []
    check.solver.iterations = spec.solver.iterations
    check.solver.processes = spec.solver.processes
    check.name = (spec.name + ' · morphed check')[:120]
    if check.use_case:
        check.use_case.confirmed = True
        check.use_case.references_confirmed = True
    return check


def write_record(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding='utf-8')


def attach_to_parent(parent_id, record):
    db.merge_result(parent_id, {'design_morph': {
        'run_id': record['run_id'], 'status': record['status'], 'kept_cycle': record.get('kept_cycle'),
        'geometry_id': record.get('geometry_id'), 'verification_id': record.get('verification_id'),
        'message': record.get('message')}})
    design = config.DATA / 'runs' / parent_id / 'results' / 'design.json'
    if design.is_file():
        data = json.loads(design.read_text(encoding='utf-8'))
        data['morph'] = record
        design.write_text(json.dumps(data, indent=2), encoding='utf-8')


@register('optimise')
def optimise_job(job, executor, root):
    parent_id = job['parent_id']
    parent = db.run(parent_id)
    spec = SimulationSpec.model_validate(parent['spec'])
    source = config.DATA / 'runs' / parent_id
    copy_case(source, root)
    adjoint.write_morph(spec, root)
    parallel = spec.solver.processes > 1
    try:
        if parallel:
            executor.run(job['id'], ['decomposePar', '-force'], root, 'morph_decompose', spec.solver.timeout_minutes * 60)
            command = ['mpirun', '-np', str(spec.solver.processes), 'adjointOptimisationFoam', '-parallel']
        else:
            command = ['adjointOptimisationFoam']
        executor.run(job['id'], command, root, 'morph', spec.solver.timeout_minutes * 60)
        if parallel:
            executor.run(job['id'], ['reconstructPar', '-latestTime'], root, 'morph_reconstruct', spec.solver.timeout_minutes * 60)
    finally:
        remove_processor_dirs(root)
    log_path = root / 'logs' / 'morph.log'
    history = adjoint.objective_history(log_path.read_text(encoding='utf-8', errors='replace'), spec.design.objective) if log_path.is_file() else []
    kept = adjoint.best_cycle(history)
    surfaces = adjoint.morph_surfaces(root)
    record = {
        'schema': 'gustsim.morph/1', 'run_id': job['id'], 'status': 'completed', 'objective': spec.design.objective,
        'turbulence': adjoint.turbulence_treatment(spec), 'history': history, 'kept_cycle': kept,
        'cycles_run': len(history), 'geometry_id': None, 'verification_id': None,
        'stopped_because': 'objective rose' if kept is not None and kept < len(history) - 1 else 'cycle budget',
        'limitations': ('The kept cycle is the lowest objective before the first rise. The morphed surface is a new '
                        'geometry revision. The verification simulation is a separate steady run and is not an experimental acceptance.'),
    }
    message = 'Morphing finished'
    if kept is None or kept >= len(surfaces):
        message = 'Morphing finished but no surface was exported for the kept cycle, so no verification run was queued'
        record['status'] = 'unavailable'
    else:
        try:
            content = merged_stl(surfaces[kept][1])
            revision = geometry.adopt_surface(spec.geometry_id, 'morphed-surface.stl', content, 'morphed', {
                'operation': 'adjoint_morph', 'parent_run': parent_id, 'kept_cycle': kept,
                'objective': spec.design.objective, 'max_displacement_m': min(box.max_displacement_m for box in spec.elements.design_boxes)})
            record['geometry_id'] = revision['id']
            from .. import pipeline
            check = verification_spec(spec, revision)
            created = pipeline.create(check, plan={'auto_review': True, 'morph_of': parent.get('simulation_id'), 'morph_run': job['id']},
                                      project_id=parent['spec'].get('project_id'), name=check.name, tags=['morph-verification'])
            record['verification_id'] = created['id']
            message = f"Morphed revision {revision['id']} queued as simulation {created['id']}"
        except Exception as error:
            record['status'] = 'unavailable'
            message = 'Morphing finished but the morphed surface could not be stored: ' + str(error)
    record['message'] = message
    write_record(root / 'results' / 'morph.json', record)
    attach_to_parent(parent_id, record)
    db.update(job['id'], status='completed' if record['geometry_id'] else 'completed', stage='completed',
              result={'morph': record, 'case_available': True})
    if record['geometry_id'] is None:
        db.event(job['id'], {'stage': 'morph', 'level': 'warning', 'message': message})
    try:
        from .. import pipeline
        if parent.get('simulation_id'):
            pipeline.advance(parent['simulation_id'])
    except Exception as error:
        db.event(job['id'], {'stage': 'pipeline', 'level': 'warning', 'message': 'Morphing kept; pipeline update failed: ' + str(error)})
