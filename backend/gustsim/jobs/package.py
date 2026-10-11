"""Post-lane job: results package, force breakdown, elements and insights for a solved run."""
import hashlib
import json
import re
import shutil
from pathlib import Path
from .. import config, db, elements, forces, geometry, insights, manifest
from ..models import SimulationSpec
from ..worker import keepalive, register


def job_id(run_id, attempt=1):
    return hashlib.sha256(f'{run_id}:package:{attempt}'.encode()).hexdigest()[:32]


def enqueue(run_id):
    """Queue the package once per solved run (deterministic id; safe on recovery)."""
    identifier = job_id(run_id)
    try:
        return db.run(identifier)
    except KeyError:
        return db.enqueue({'run_id': run_id}, 'package', parent_id=run_id, identifier=identifier)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=float), encoding='utf-8')


def documents_for(case):
    names = {'forces': 'results/forces.json', 'convergence': 'results/convergence.json', 'insights': 'results/insights.json',
             'elements': 'results/elements.json', 'design': 'results/design.json',
             'freesurface': 'results/freesurface.json', 'report_pdf': 'results/report.pdf'}
    return {k: (v if (case/v).is_file() else None) for k, v in names.items()}


def signal_exit(error):
    """True when a stage died from a signal (exit code above 128 or negative), e.g. a segfault."""
    match = re.search(r'exit code (-?\d+)', str(error))
    return bool(match) and (int(match.group(1)) < 0 or int(match.group(1)) > 128)


def run_pvbatch(executor, identifier, case, out):
    """Run postpackage.py under pvbatch. ParaView occasionally segfaults while tearing down its
    offscreen context (about one run in twenty on the v2606 worker image). layers.json is written
    last, so a crash after it keeps the complete package; a crash before it is retried once."""
    marker = out/'layers.json'
    marker.unlink(missing_ok=True)
    command = ['pvbatch', '--force-offscreen-rendering', str(Path(__file__).resolve().parents[1]/'postpackage.py'),
               str(case), str(out), str(case/'results/package_plan.json')]
    for attempt in (1, 2):
        try:
            executor.run(identifier, command, case, 'packaging', 4*3600)
            return
        except RuntimeError as error:
            if marker.is_file():
                db.event(identifier, {'stage': 'packaging', 'level': 'warning',
                                      'message': f'pvbatch exited abnormally after writing the complete package ({error}); outputs kept'})
                return
            if attempt == 2 or not signal_exit(error):
                raise
            log = case/'logs/packaging.log'
            if log.exists():
                log.replace(case/'logs/packaging_attempt1.log')
            db.event(identifier, {'stage': 'packaging', 'level': 'warning', 'message': f'pvbatch crashed ({error}); retrying once'})


def build(run_id, executor=None, identifier=None, in_process=None):
    run = db.run(run_id)
    case = config.DATA/'runs'/run_id
    spec = SimulationSpec.model_validate(run['spec'])
    meta = db.geometry(spec.geometry_id)
    out = case/manifest.PACKAGE
    plan = manifest.plan(spec, meta)
    transient_plan = case/'results/transient_plan.json'
    if spec.time.mode == 'transient' and transient_plan.is_file():
        times = json.loads(transient_plan.read_text(encoding='utf-8'))
        plan['basis'] = 'time_averaged'
        if times.get('frames'):
            plan['animation'] = {'dir': 'postProcessing/frames', 'start': times['start']}
    write_json(case/'results/package_plan.json', plan)
    use_pvbatch = shutil.which('pvbatch') is not None if in_process is None else not in_process
    if use_pvbatch:
        run_pvbatch(executor, identifier, case, out)
    else:
        from ..postpackage import Package
        Package(case, out, plan).run()
    frontal = None
    try:
        mesh, _ = geometry.load(spec.geometry_id)
        if spec.flow.speed > 0:
            frontal = geometry.projected_area(mesh, spec.flow.velocity())
    except Exception:
        frontal = None
    breakdown = forces.breakdown(case, spec, meta, frontal)
    if breakdown is not None:
        write_json(case/'results/forces.json', breakdown)
    write_json(case/'results/elements.json', elements.build(spec, run['result'], case))
    faces_path = out/'surface/object_faces.npz'
    layers = json.loads((out/'layers.json').read_text(encoding='utf-8')) if (out/'layers.json').is_file() else {}
    if faces_path.is_file():
        convergence = json.loads((case/'results/convergence.json').read_text()) if (case/'results/convergence.json').is_file() else run['result'].get('convergence')
        parts = {p['index']: p['name'] for p in plan['parts']}
        loaded = insights.load_faces(faces_path)
        found = insights.generate(loaded, spec, forces=breakdown, convergence=convergence, layers=layers,
                                  mesh_summary=run['result'].get('mesh_summary'), part_names=parts, model_bounds=meta['bounds'])
        write_json(case/'results/insights.json', found)
        if spec.free_surface is not None and 'alpha_water' in loaded:
            from ..foam_multiphase import resistance_coefficient, wetted_area
            area = wetted_area(loaded['alpha_water'], loaded['area'])
            drag = (breakdown or {}).get('frames', {}).get('wind', {}).get('total', {}).get('force_n', {}).get('drag') if breakdown else None
            write_json(case/'results/freesurface.json', {
                'schema': 'gustsim.freesurface/1', 'waterline_z': spec.free_surface.waterline_z,
                'wetted_area_m2': area, 'ct': resistance_coefficient(drag, spec.flow.speed, spec.references.area, spec.free_surface.water.density),
                'limitations': 'Fixed trim and sinkage. C_T uses the water density and the reference area. Not a resistance-test comparison.'})
        if spec.design.enabled:
            from .. import adjoint
            adjoint_run = json.loads((case/'results/adjoint_run.json').read_text(encoding='utf-8')) if (case/'results/adjoint_run.json').is_file() else {}
            adjoint_run['morph'] = run['result'].get('design_morph')
            write_json(case/'results/design.json', adjoint.summarise(loaded, spec, adjoint_run))
    elif spec.design.enabled:
        from .. import adjoint
        adjoint_run = json.loads((case/'results/adjoint_run.json').read_text(encoding='utf-8')) if (case/'results/adjoint_run.json').is_file() else {}
        adjoint_run['morph'] = run['result'].get('design_morph')
        write_json(case/'results/design.json', adjoint.summarise({}, spec, adjoint_run))
    result = manifest.build(run, spec, meta, out, documents_for(case))
    write_json(out/'manifest.json', result)
    surface = any(l['id'] == 'surface' for l in result['layers'])
    headline = {}
    if breakdown:
        c = breakdown['coefficients']
        headline = {'cd': c['cd'], 'cl': c['cl'], 'cda_m2': c['cda_m2'], 'drag_n': breakdown['frames']['wind']['total']['force_n']['drag'],
                    'lift_n': breakdown['frames']['wind']['total']['force_n']['lift']}
    db.merge_result(run_id, {'package': {'status': 'completed', 'manifest': f'{manifest.PACKAGE}/manifest.json',
                                         'missing': result['missing'], 'thumbnail': result['thumbnail']},
                             'headline': headline, 'fields_available': run['result'].get('fields_available') or surface})
    return result


@register('package')
def package_job(job, executor, root):
    run_id = job['spec']['run_id']
    with keepalive(job['id']):
        result = build(run_id, executor, job['id'])
    db.update(job['id'], status='completed', stage='completed',
              result={'run_id': run_id, 'layers': len(result['layers']), 'missing': result['missing']})
    try:
        from .report import enqueue as enqueue_report
        enqueue_report(run_id)
    except Exception as e:
        db.event(job['id'], {'stage': 'report', 'level': 'warning', 'message': 'Package built; report scheduling failed: ' + str(e)})
    try:
        from .. import pipeline
        pipeline.advance_run(run_id)
    except Exception as e:
        db.event(job['id'], {'stage': 'pipeline', 'level': 'warning', 'message': 'Package built; pipeline update failed: ' + str(e)})
