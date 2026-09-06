"""Real end-to-end OpenFOAM/ParaView smoke test; not experimental validation."""
import json
import argparse
import numpy as np
from gustsim import db,geometry,validation,config
from gustsim.models import Primitive, Boundary, Rotation
from gustsim.worker import process_job

db.initialize()
parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['external','internal','rotor'],default='external');parser.add_argument('--turbulent',action='store_true');parser.add_argument('--processes',type=int,default=1);args=parser.parse_args()
g=geometry.add_primitive(Primitive(shape='cylinder' if args.mode=='internal' else 'sphere',name='smoke_'+args.mode,radius=.1,length=.4,role='enclosure' if args.mode=='internal' else 'solid'))
s=validation.defaults(g['id']);s.name='Sphere · runtime smoke test'
s.flow.speed=.01;s.flow.turbulence='laminar';s.mesh.base_cells=20;s.mesh.surface_level=2;s.mesh.feature_level=2;s.mesh.layers=0
s.solver.processes=1;s.solver.iterations=40;s.solver.write_interval=20
s.solver.processes=args.processes
if args.turbulent:s.flow.turbulence='kOmegaSST';s.flow.speed=2;s.mesh.layers=2
if args.mode=='internal':
    s.mode='internal';s.domain.minimum=(-.3,-.2,-.2);s.domain.maximum=(.3,.2,.2);s.domain.fluid_point=(0,0,0)
    mesh,groups=geometry.load(g['id']);s.boundaries=[]
    for patch in g['patches']:
        center=mesh.triangles_center[groups==patch['group']].mean(axis=0)
        kind='velocity_inlet' if center[0]<-.19 else 'pressure_outlet' if center[0]>.19 else 'wall'
        s.boundaries.append(Boundary(patch=patch['name'],kind=kind))
if args.mode=='rotor':
    s.mode='rotor';s.rotation=Rotation(enabled=True,patches=[p['name'] for p in g['patches']],radius=.15,length=.3,blade_radius=.101,rpm=10)
job=db.enqueue(s.model_dump());db.claim();process_job(job)
r=db.run(job['id'])
print(json.dumps({'id':r['id'],'status':r['status'],'stage':r['stage'],'error':r['error'],'metrics':r['result'].get('metrics'),'fields_available':r['result'].get('fields_available')},indent=2))
if r['status']!='completed':raise SystemExit(1)
