"""Explain job progress using phases and actual SIMPLE iterations."""
import json
import re

MESH=[('queued',0),('preparing',3),('background',8),('features',15),('decompose_mesh',18),('meshing',30),('reconstruct_mesh',70),('renumber',73),('check',76),('mesh_diagnostics',82),('rotation',88),('mesh_export',94)]

TERMINAL={'failed':'Stopped: attempt failed','cancelled':'Stopped: cancelled by user','interrupted':'Stopped: worker restarted before this attempt finished'}

def describe(run, root):
    if run['status']=='completed':return {'percent':100,'label':'Completed','basis':'completed'}
    # A stopped attempt never reports progress toward completion; it reports where it stopped.
    if run['status'] in TERMINAL:
        return {'percent':0,'label':f"{TERMINAL[run['status']]} during {str(run['stage']).replace('_',' ')}",'basis':'stopped','stopped_stage':run['stage']}
    phase=dict(MESH).get(run['stage'],5)
    detail='Phase estimate; individual phases can take very different amounts of time'
    if run['kind']=='solve':
        phase={'decomposing':5,'initialise':8,'solve_startup':10,'solve':10,'transient':35,'reconstructing':88,'postprocessing':93}.get(run['stage'],phase//2)
        if run['stage']=='transient':
            path=root/'logs'/'transient.log';plan=root/'results'/'transient_plan.json'
            if path.is_file() and plan.is_file():
                with path.open('rb') as f:
                    f.seek(max(0,path.stat().st_size-65536));text=f.read().decode(errors='replace')
                times=re.findall(r'^Time = ([\d.eE+-]+)',text,re.M)
                try:info=json.loads(plan.read_text())
                except ValueError:info=None
                if times and info:
                    done=max(0.0,min(1.0,(float(times[-1])-info['start'])/info['duration_s']))
                    return {'percent':35+52*done,'label':f"Transient flow: {float(times[-1])-info['start']:.3g} s of {info['duration_s']:.3g} s simulated",
                            'basis':'simulated time'}
            return {'percent':35,'label':'Starting the transient stage from the steady solution','basis':'phase estimate'}
        if run['stage'] in ('solve','solve_startup'):
            path=root/'logs'/(run['stage']+'.log')
            if path.is_file():
                with path.open('rb') as f:
                    f.seek(max(0,path.stat().st_size-65536));text=f.read().decode(errors='replace')
                iterations=re.findall(r'^Time = ([\d.eE+-]+)',text,re.M)
                # Early stopping is experimental and opt-in; without it the full budget runs.
                stops=bool(((run['spec'].get('convergence') or {}).get('enabled')))
                live=root/'results/convergence_live.json'
                if iterations and live.is_file():
                    try:state=json.loads(live.read_text())
                    except ValueError:state=None
                    if state and state.get('cap'):
                        current=int(float(iterations[-1]));cap=int(state['cap'])
                        if state['state']=='averaging' and state.get('averaging_start') is not None:
                            label=f"Convergence detected at iteration {int(state['averaging_start'])}; averaging loads (iteration {current}, cap {cap})"
                        elif stops:label=f'Searching for load convergence (iteration {current}, cap {cap}); the solve stops early once loads settle'
                        else:label=f'Iteration {current} / {cap}; monitoring load convergence (early stopping is off)'
                        return {'percent':min(87,10+77*current/cap),'label':label,'basis':'convergence monitor','convergence':state}
                if iterations:
                    current=int(float(iterations[-1]));total=run['spec']['solver']['iterations']
                    return {'percent':min(87,10+77*current/total),'label':f'Iteration {current} / {total}'+('; convergence can finish earlier' if stops else ''),'basis':'iteration budget'}
    if run['kind']=='view':phase=50;detail='Extracting a view from solved fields'
    return {'percent':phase,'label':detail,'basis':'phase estimate'}
