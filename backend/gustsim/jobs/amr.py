"""Post-lane job: vorticity indicator surface for the next mesh attempt."""
import json
from pathlib import Path

from .. import amr, config, db
from ..worker import register


def enqueue(solve_id):
    identifier = amr.indicator_id(solve_id)
    try:
        return db.run(identifier)
    except KeyError:
        parent = db.run(solve_id)
        return db.enqueue({'run_id': solve_id}, 'amr_indicator', parent_id=solve_id,
                          simulation_id=parent.get('simulation_id'), identifier=identifier)


@register('amr_indicator')
def indicator_job(job, executor, root):
    """Extract the indicator with pvbatch. The solved case is not modified except for results/amr/."""
    solve_id = job['spec']['run_id']
    case = config.DATA / 'runs' / solve_id
    out = case / 'results' / 'amr'
    out.mkdir(parents=True, exist_ok=True)
    from ..models import SimulationSpec
    spec = db.run(solve_id)['spec']
    parsed = SimulationSpec.model_validate(spec)
    speed = abs(parsed.flow.speed) or 1e-6
    script = Path(__file__).resolve().parents[1] / 'amr_indicator.py'
    executor.run(job['id'], ['pvbatch', '--force-offscreen-rendering', str(script), str(case), str(out), f'{speed:.8g}',
                             f'{amr.THRESHOLD:.8g}'], case, 'amr_indicator', 3600)
    stl = out / 'indicator.stl'
    if not stl.is_file() or stl.stat().st_size < 84:
        raise RuntimeError('The indicator surface was not written')
    distance, level = amr.distance_and_level(parsed)
    meta = json.loads((out / 'indicator.json').read_text(encoding='utf-8')) if (out / 'indicator.json').is_file() else {}
    result = {'sha256': amr.file_sha256(stl), 'distance_m': meta.get('distance_m', distance), 'level': meta.get('level', level),
              'triangles': amr.read_stl_triangles(stl), 'threshold': amr.THRESHOLD, 'stl': 'results/amr/indicator.stl'}
    db.update(job['id'], status='completed', stage='completed', result=result)
    try:
        from ..pipeline import advance
        if job.get('simulation_id'):
            advance(job['simulation_id'])
    except Exception as error:
        db.event(job['id'], {'stage': 'pipeline', 'level': 'warning', 'message': 'Indicator kept; the next mesh was not queued: ' + str(error)})
