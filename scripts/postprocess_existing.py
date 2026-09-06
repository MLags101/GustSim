"""Re-extract the latest smoke case without repeating its successful solve."""
from gustsim import db,config
from gustsim.worker import LocalExecutor
from pathlib import Path
db.initialize()
run=next(r for r in db.runs() if r['kind']=='solve')
case=config.DATA/'runs'/run['id']
LocalExecutor().run(run['id'],['pvbatch','--force-offscreen-rendering','/app/backend/gustsim/postprocess.py',str(case),str(case/'results')],case,'postprocessing',600)
print('ParaView exports generated for',run['id'])
