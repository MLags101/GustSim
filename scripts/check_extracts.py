"""Exercise supported ParaView extracts against an existing solved smoke case."""
import json
from pathlib import Path
from gustsim import db,config
from gustsim.worker import LocalExecutor
from gustsim.exports import export_run
db.initialize()
run=next(r for r in db.runs() if r['kind']=='solve')
case=config.DATA/'runs'/run['id']
executor=LocalExecutor()
for kind in ('slice','clip','streamlines','glyphs','line','probe','surface'):
    target=case/'views'/('test_'+kind);target.mkdir(parents=True,exist_ok=True)
    request=target/'request.json'
    request.write_text(json.dumps({'kind':kind,'field':'U' if kind!='surface' else 'vorticity','origin':[-.2,0,0],'normal':[0,0,1],'end':[.5,0,0],'seed_radius':.15,'resolution':30}))
    executor.run(run['id'],['pvbatch','--force-offscreen-rendering','/app/backend/gustsim/postprocess.py',str(case),str(target),str(request)],case,'extract_'+kind,600)
    print(kind,'OK',flush=True)
for kind in ('hdf5','csv','vtk','png','report','bundle'):
    target=export_run(run,kind)
    print(kind,target.stat().st_size,'bytes',flush=True)
