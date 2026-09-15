"""Explain job progress using phases and actual SIMPLE iterations."""
import re

MESH=[('queued',0),('preparing',3),('background',8),('features',15),('meshing',30),('check',76),('mesh_diagnostics',82),('rotation',88),('mesh_export',94)]

TERMINAL={'failed':'Stopped: attempt failed','cancelled':'Stopped: cancelled by user','interrupted':'Stopped: worker restarted before this attempt finished'}

def describe(run, root):
    if run['status']=='completed':return {'percent':100,'label':'Completed','basis':'completed'}
    # A stopped attempt never reports progress toward completion; it reports where it stopped.
    if run['status'] in TERMINAL:
        return {'percent':0,'label':f"{TERMINAL[run['status']]} during {str(run['stage']).replace('_',' ')}",'basis':'stopped','stopped_stage':run['stage']}
    phase=dict(MESH).get(run['stage'],5)
    detail='Phase estimate; individual phases can take very different amounts of time'
    if run['kind']=='solve':
        phase={'decomposing':5,'solve':10,'reconstructing':88,'postprocessing':93}.get(run['stage'],phase//2)
        if run['stage']=='solve':
            path=root/'logs/solve.log'
            if path.is_file():
                with path.open('rb') as f:
                    f.seek(max(0,path.stat().st_size-65536));text=f.read().decode(errors='replace')
                iterations=re.findall(r'^Time = ([\d.eE+-]+)',text,re.M)
                if iterations:
                    current=int(float(iterations[-1]));total=run['spec']['solver']['iterations']
                    return {'percent':min(87,10+77*current/total),'label':f'Iteration {current} / {total}; convergence can finish earlier','basis':'iteration budget'}
    if run['kind']=='view':phase=50;detail='Extracting a view from solved fields'
    return {'percent':phase,'label':detail,'basis':'phase estimate'}
