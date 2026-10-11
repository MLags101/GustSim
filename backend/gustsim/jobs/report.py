"""Post-lane job: the PDF report of a solved run (after its results package)."""
import hashlib
from .. import config, db, report_pdf
from ..models import SimulationSpec
from ..worker import keepalive, register


def enqueue(run_id, attempt=1):
    identifier = hashlib.sha256(f'{run_id}:report:{attempt}'.encode()).hexdigest()[:32]
    try:
        return db.run(identifier)
    except KeyError:
        return db.enqueue({'run_id': run_id}, 'report', parent_id=run_id, identifier=identifier, attempt=attempt)


def build(run_id):
    run = db.run(run_id)
    spec = SimulationSpec.model_validate(run['spec'])
    path = report_pdf.build(run, spec, db.geometry(spec.geometry_id), config.DATA/'runs'/run_id)
    db.merge_result(run_id, {'report_pdf': 'results/report.pdf'})
    return path


@register('report')
def report_job(job, executor, root):
    with keepalive(job['id']):
        path = build(job['spec']['run_id'])
    db.update(job['id'], status='completed', stage='completed', result={'run_id': job['spec']['run_id'], 'bytes': path.stat().st_size})
