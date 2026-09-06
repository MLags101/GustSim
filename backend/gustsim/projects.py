"""Small project workspaces persisted in the existing named data volume."""
import json
import re
import time
from . import config, db

def path(identifier):
    if not re.fullmatch('[a-f0-9]{32}',identifier):raise ValueError('Invalid simulation identifier')
    return config.DATA/'projects'/(identifier+'.json')

def save(identifier,body):
    name=body.get('name','New simulation')
    if not isinstance(name,str) or not 1<=len(name)<=120:raise ValueError('Simulation name must contain 1–120 characters')
    if not isinstance(body.get('workspace',{}),dict):raise ValueError('Workspace must be an object')
    record={'id':identifier,'name':name,'updated':time.time(),'workspace':body.get('workspace',{})}
    data=json.dumps(record)
    if len(data)>2_000_000:raise ValueError('Workspace is too large')
    target=path(identifier);target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_suffix('.'+db.uid()+'.tmp');tmp.write_text(data,encoding='utf-8');tmp.replace(target)
    return record

def read(identifier):
    target=path(identifier)
    if not target.is_file():raise KeyError('Simulation not found')
    return json.loads(target.read_text(encoding='utf-8'))
def listing():
    return sorted([{k:v for k,v in json.loads(p.read_text(encoding='utf-8')).items() if k!='workspace'} for p in (config.DATA/'projects').glob('*.json')],key=lambda r:r['updated'],reverse=True)
