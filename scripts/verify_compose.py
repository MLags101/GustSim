"""Exercise a running Compose stack through HTTP. Creates small labelled smoke runs.

Usage: python scripts/verify_compose.py [http://localhost:8080]
Uses only the Python standard library; does not certify CFD accuracy.
"""
import csv
import io
import json
import sys
import time
import urllib.request
import zipfile

base = (sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8080').rstrip('/')

def request(path, payload=None, raw=False):
    data=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=180) as response:
        content=response.read()
        return content if raw else json.loads(content)

def wait(job):
    deadline=time.monotonic()+900
    previous=None
    while time.monotonic()<deadline:
        record=request('/api/runs/'+job['id'])
        state=(record['status'],record['stage'])
        if state!=previous:
            print(job['id'],*state,flush=True);previous=state
        if state[0] not in ('queued','running'):
            assert state[0]=='completed',record.get('error')
            return record
        time.sleep(2)
    raise TimeoutError('Smoke run exceeded 15 minutes; inspect/cancel it in the workbench')

health=request('/api/health')
assert health['worker']['online'] and health['cad_available'],health
assert b'<div id="root">' in request('/',raw=True)
geometry=request('/api/geometry/primitive',{'shape':'sphere','name':'compose_smoke','radius':.1,'role':'solid'})
spec=request('/api/geometry/'+geometry['id']+'/defaults')
spec['name']='Compose installation smoke test'
spec['flow'].update(speed=.01,turbulence='laminar')
spec['mesh'].update(base_cells=20,surface_level=2,feature_level=2,layers=0)
spec['solver'].update(processes=2,iterations=40,write_interval=20)
assert request('/api/validate',spec)['valid']
mesh=wait(request('/api/runs?kind=mesh',spec))
assert len(request('/api/runs/'+mesh['id']+'/files/mesh-preview.vtp',raw=True))>100
run=wait(request('/api/runs?mesh_run_id='+mesh['id'],spec))
assert run['result']['fields_available']
events=request('/api/runs/'+run['id']+'/events',raw=True)
assert b'mesh_reuse' in events and b'event: done' in events
rows=list(csv.DictReader(io.StringIO(request('/api/runs/'+run['id']+'/files/results/samples.csv',raw=True).decode())))
assert rows and 'pressure_pa' in rows[0], 'Surface CSV lacks pressure fields'
view=request('/api/runs/'+run['id']+'/views',{'kind':'line','field':'pressure_pa','origin':[-.2,.15,0],'end':[.4,.15,0],'resolution':30})
wait(view['job'])
samples=request('/api/runs/'+run['id']+'/views/'+view['id']+'/samples')
assert len(samples['rows'])==31 and 'pressure_pa' in samples['columns']
bundle=request('/api/runs/'+run['id']+'/export/bundle',raw=True)
with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
    names=set(archive.namelist())
    assert {'manifest.json','fields.h5','examples/read_results.py','examples/read_results.m','postprocess.py'} <= names
    assert archive.read('fields.h5').startswith(b'\x89HDF\r\n\x1a\n')
    manifest=json.loads(archive.read('manifest.json'))
print(json.dumps({'passed':True,'mesh_run':mesh['id'],'solve_run':run['id'],'numerical_status':run['result'].get('numerical_status'),'accuracy_validation':'not performed','bundle_bytes':len(bundle)},indent=2))
