"""CAD lane: geometry import and heavy preparation as durable jobs.

OCP (STEP/IGES) lives in the API image, not the OpenFOAM worker, so this lane runs from the
API image as its own Compose service. Uploads are kept until the import succeeds, so a retry
can reuse them.
"""
import shutil
import time
from . import config, db, geometry
from .models import Prepare
from .worker import Cancelled, keepalive, worker_lock

UPLOADS = 'uploads'


def upload_dir(job_id):
    return config.DATA / UPLOADS / job_id


def import_job(job):
    identifier = job['id']
    spec = job['spec']
    folder = upload_dir(spec.get('upload_job') or identifier)
    source = folder / spec['stored_name']
    if not source.is_file():
        raise RuntimeError('The uploaded file is no longer available; upload it again')
    db.update(identifier, stage='importing')
    db.event(identifier, {'stage': 'importing', 'message': f"Importing {spec['filename']}"})
    with keepalive(identifier):
        meta = geometry.import_file(spec['filename'], source.read_bytes(), spec.get('units', 'mm'), spec.get('deflection', 'auto'))
    db.update(identifier, status='completed', stage='completed',
              result={'geometry_id': meta['id'], 'triangles': meta['triangles'], 'patches': len(meta['patches'])})
    shutil.rmtree(folder, ignore_errors=True)


def prepare_job(job):
    identifier = job['id']
    request = Prepare.model_validate(job['spec']['request'])
    db.update(identifier, stage='wrapping' if request.wrap else 'preparing')
    db.event(identifier, {'stage': 'preparing', 'message': 'Preparing a new geometry revision'})
    with keepalive(identifier):
        meta = geometry.prepare(job['spec']['geometry_id'], request)
    db.update(identifier, status='completed', stage='completed',
              result={'geometry_id': meta['id'], 'parent_id': job['spec']['geometry_id']})


HANDLERS = {'import': import_job, 'prepare': prepare_job}


def process(job):
    identifier = job['id']
    try:
        if db.run(identifier)['cancel']:
            raise Cancelled()
        HANDLERS[job['kind']](job)
    except Cancelled:
        db.update(identifier, status='cancelled', stage='cancelled', error='Cancelled by user')
    except Exception as e:
        db.update(identifier, status='failed', error=str(e))
        db.event(identifier, {'message': str(e), 'level': 'error'})
    finally:
        db.event(identifier, {'stage': db.run(identifier)['status'], 'message': 'Job ' + db.run(identifier)['status']})


def main(poll=1.0, once=False):
    from . import worker
    worker.CURRENT_LANE = 'cad'
    db.require_schema()
    with worker_lock('cad'):
        db.recover(lanes=('cad',), statuses=('running',))
        print('GustSim CAD worker ready.', flush=True)
        while True:
            db.heartbeat(lane='cad')
            job = db.claim(('cad',))
            if job:
                process(job)
            elif once:
                return
            else:
                time.sleep(poll)


if __name__ == '__main__':
    main()
