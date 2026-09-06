"""HTTP integration of guided presets, reviewed meshes, measured outputs and views.

Creates labelled runtime smoke attempts and preserves them. Not accuracy validation.
Usage: python scripts/verify_guided.py [http://127.0.0.1:8080]
"""
import io
import json
import sys
import time
import urllib.request
import urllib.error
import zipfile

base=(sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8080').rstrip('/')
def request(path,payload=None,raw=False):
    req=urllib.request.Request(base+'/api'+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=120) as response:
        value=response.read();return value if raw else json.loads(value)

def wait(job):
    end=time.monotonic()+600;previous=None
    while time.monotonic()<end:
        run=request('/runs/'+job['id']);state=(run['status'],run['stage'])
        if state!=previous:print(run['id'],*state,flush=True);previous=state
        if state[0] not in ('queued','running'):
            assert state[0]=='completed',run['error'];return run
        time.sleep(2)
    raise TimeoutError('Inspect the labelled smoke attempt; it exceeded ten minutes')

# Compose starts the queue worker after the API becomes healthy.
startup=time.monotonic()+60
while True:
    try:
        if request('/health')['worker']['online']:break
    except (urllib.error.URLError,KeyError):pass
    if time.monotonic()>startup:raise RuntimeError('Start the local Compose worker before this check')
    time.sleep(1)

evidence=[]
for mode in ('pipe-flow','pipe-pressure','propeller','aerodynamics'):
    internal=mode.startswith('pipe')
    g=request('/geometry/primitive',{'shape':'cylinder' if internal else 'sphere','name':'guided_'+mode.replace('-','_'),'radius':.1,'length':.4,'role':'enclosure' if internal else 'solid'})
    payload={'geometry_id':g['id'],'kind':'pipe' if internal else mode,'turbulence':'laminar','speed':.01,'patches':[p['name'] for p in g['patches']]}
    if internal:
        scene=request('/geometry/'+g['id']+'/scene');centers={p['group']:[] for p in g['patches']}
        for i,group in enumerate(scene['groups']):
            ids=scene['polys'][i*4+1:i*4+4];centers[group].extend(scene['points'][v*3] for v in ids)
        ordered=sorted(g['patches'],key=lambda p:sum(centers[p['group']])/len(centers[p['group']]))
        payload.update(inlet=ordered[0]['name'],outlet=ordered[-1]['name'],driving='pressure' if mode=='pipe-pressure' else 'flow',flow_rate=.0003,inlet_pressure_pa=.0002,outlet_pressure_pa=0)
    if mode=='propeller':payload.update(speed=0,rpm=-10)
    s=request('/presets/preview',payload)['spec'];s['use_case']['confirmed']=True
    s['name']='Guided runtime smoke · '+mode;s['mesh'].update(base_cells=20,surface_level=2,feature_level=2,layers=0)
    s['solver'].update(processes=2,iterations=40,write_interval=20)
    check=request('/validate',s);assert check['valid'],check
    query='&guided=true&acknowledged='+check['configuration_id']
    mesh=wait(request('/runs?kind=mesh'+query,s))
    assert mesh['result']['mesh_check']=='passed' and mesh['result']['mesh_available']
    request('/runs/'+mesh['id']+'/review?configuration_id='+check['configuration_id']+'&acknowledge_warnings=true',{})
    run=wait(request('/runs?kind=solve&mesh_run_id='+mesh['id']+query,s))
    assert run['result']['fields_available']
    metrics=run['result']['metrics']
    if internal:assert all(metrics.get(k) is not None for k in ('outlet_mass_flow_kgs','pressure_drop_pa','total_pressure_loss_pa'))
    if mode=='propeller':assert metrics['fluid_torque_nm'] is not None and metrics['efficiency'] is None and run['result']['rotor_load_history']
    if mode=='aerodynamics':assert metrics['drag_n'] is not None and metrics['cd'] is None and run['result']['projected_history']
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        views=request('/runs/'+run['id']+'/views')
        if len(views)==2 and all(v['status']=='completed' for v in views):break
        assert not any(v['status']=='failed' for v in views),views
        time.sleep(2)
    assert len(views)==2 and all(v['status']=='completed' for v in views),views
    for view in views:assert len(request('/runs/'+run['id']+'/files/views/'+view['id']+'/surface.vtp',raw=True))>100
    with zipfile.ZipFile(io.BytesIO(request('/runs/'+run['id']+'/export/bundle',raw=True))) as archive:
        manifest=json.loads(archive.read('manifest.json'));assert manifest['quality']['analysis']['kind']==s['use_case']['kind']
        assert 'fields.h5' in archive.namelist()
    evidence.append({'mode':mode,'mesh_id':mesh['id'],'run_id':run['id'],'views':len(views),'metrics':metrics})
print(json.dumps({'runtime_evidence':evidence,'experimental_validation':'pending'},indent=2),flush=True)
