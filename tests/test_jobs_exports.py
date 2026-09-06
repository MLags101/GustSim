import json
from pathlib import Path
import sys
import threading
import time
import zipfile
import numpy as np
import h5py
import pytest
from fastapi.testclient import TestClient
from gustsim import config,db,geometry,validation,quality
from gustsim.api import app
from gustsim.models import Primitive
from gustsim.worker import LocalExecutor, Cancelled, process_job, worker_lock
from gustsim.exports import export_run,write_hdf5

def make_job():
    g=geometry.add_primitive(Primitive(shape='sphere'))
    return db.enqueue(validation.defaults(g['id']).model_dump(),'case')

def test_atomic_claim_and_restart_preserve_snapshot():
    job=make_job();claims=[]
    threads=[threading.Thread(target=lambda:claims.append(db.claim())) for _ in range(4)]
    for t in threads:t.start()
    for t in threads:t.join()
    assert sum(v is not None for v in claims)==1
    db.recover();assert db.run(job['id'])['status']=='interrupted'
    assert db.run(job['id'])['spec']==job['spec']

def test_worker_case_only_and_zip_manifest():
    job=make_job();db.claim();process_job(job)
    run=db.run(job['id']);assert run['status']=='completed'
    with zipfile.ZipFile(export_run(run,'case')) as z:
        assert 'case/system/controlDict' in z.namelist()
        assert 'examples/read_results.py' in z.namelist()
        assert json.loads(z.read('manifest.json'))['run_id']==run['id']
    with pytest.raises(ValueError):export_run(run,'hdf5')

def test_hdf5_roundtrip(tmp_path):
    source=tmp_path/'fields.npz';target=tmp_path/'fields.h5'
    arrays={'points':np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1.]]),'connectivity':np.arange(4),'offsets':np.array([0,4]),'cell_types':np.array([10]),'cell__pressure_pa':np.array([123.4]),'cell__U':np.array([[1.,2.,3.]])}
    np.savez(source,**arrays);job=make_job();write_hdf5(source,target,job)
    with h5py.File(target) as h:
        np.testing.assert_array_equal(h['mesh/connectivity'][:],arrays['connectivity'])
        np.testing.assert_array_equal(h['fields/cell/U'][:],arrays['cell__U'])
        assert h['fields/cell/pressure_pa'].attrs['units']=='Pa gauge'

def test_success_exit_is_not_convergence(tmp_path):
    job=make_job();from gustsim.models import SimulationSpec
    case=tmp_path/'case';(case/'logs').mkdir(parents=True)
    (case/'logs/solve.log').write_text('Time = 1000\nEnd\n')
    (case/'logs/check.log').write_text('Mesh OK.\ncells: 100')
    result=quality.analyze(case,SimulationSpec.model_validate(job['spec']))
    assert result['numerical_status']=='review_required'
    assert result['metrics']['cd'] is None

def test_force_parser_total_is_not_sum_of_contributions(tmp_path):
    p=tmp_path/'postProcessing/loads/0';p.mkdir(parents=True)
    (p/'force.dat').write_text('# Time total pressure viscous\n1 (10 20 30) (9 18 27) (1 2 3)\n')
    (p/'moment.dat').write_text('1 (4 5 6) (3 4 5) (1 1 1)\n')
    row=quality.force_history(tmp_path)[0]
    assert row['force']==[10,20,30] and row['moment']==[4,5,6]

def test_cancellation_interrupts_silent_process(tmp_path):
    job=make_job();db.claim();executor=LocalExecutor()
    timer=threading.Timer(.4,lambda:db.update(job['id'],cancel=1));timer.start()
    start=time.monotonic()
    try:
        with pytest.raises(Cancelled):executor.run(job['id'],[sys.executable,'-c','import time;time.sleep(30)'],tmp_path,'silent',40)
    finally:timer.join()
    assert time.monotonic()-start<10

def test_api_import_compile_and_invalid_origin():
    with TestClient(app) as client:
        g=client.post('/api/geometry/primitive',json={'shape':'sphere'}).json()
        spec=client.get('/api/geometry/'+g['id']+'/defaults').json()
        assert client.post('/api/validate',json=spec).json()['valid']
        r=client.post('/api/runs?kind=case',json=spec)
        assert r.status_code==202,r.text
        run=r.json();assert run['status']=='completed'
        response=client.get('/api/runs/'+run['id']+'/export/case')
        assert response.status_code==200 and response.content[:2]==b'PK'
        assert client.post('/api/setups',json=spec).status_code==201
        assert len(client.get('/api/setups').json())==1
        assert client.post('/api/validate',json=spec,headers={'origin':'https://attacker.invalid'}).status_code==403
        assert client.post('/api/runs',json=spec).status_code==503

def test_mesh_reuse_compares_json_roundtrip_and_rejects_changed_physics():
    with TestClient(app) as client:
        job=make_job();spec=job['spec']
        mesh=db.enqueue(spec,'mesh');db.update(mesh['id'],status='completed',result={'mesh_check':'passed'})
        db.heartbeat()
        spec['solver']['iterations']=50
        response=client.post('/api/runs?mesh_run_id='+mesh['id'],json=spec)
        assert response.status_code==202,response.text
        assert response.json()['parent_id']==mesh['id']
        spec['flow']['speed']*=2
        assert client.post('/api/runs?mesh_run_id='+mesh['id'],json=spec).status_code==422
