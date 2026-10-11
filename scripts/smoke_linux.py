"""Real end-to-end OpenFOAM/ParaView smoke test; not experimental validation.

Run inside the worker container with a temporary GUSTSIM_DATA. v2 flags exercise the steady
core features (parallel meshing, potential initialisation, robust numerics, early stopping and
the output function objects) on the pinned OpenCFD image. Element flags (--onground, --wheels,
--multi-mrf, --porous, --openings) compile each experimental element, run it and check the
measured element results; passing says the case runs, not that the physics is validated.
"""
import json
import argparse
import numpy as np
from gustsim import db,geometry,validation,config,presets,wheels
from gustsim.models import (Primitive, Boundary, Rotation, UseCase, Convergence, PresetRequest, SimulationSpec,
                            Wheel, RotatingZone, PorousZone, Opening, DesignBox)
from gustsim.worker import process_job

db.initialize()
parser=argparse.ArgumentParser()
parser.add_argument('--mode',choices=['external','internal','rotor'],default='external')
parser.add_argument('--turbulent',action='store_true')
parser.add_argument('--processes',type=int,default=1)
parser.add_argument('--static',action='store_true',help='Rotor in quiescent fluid with open ambient boundaries and automatic views')
parser.add_argument('--v2',action='store_true',help='Version 2 configuration (SIMPLEC numerics, monitor, output function objects)')
parser.add_argument('--parallel-mesh',action='store_true',help='v2: decompose and mesh with snappyHexMesh -parallel')
parser.add_argument('--potential',action='store_true',help='v2: initialise with potentialFoam')
parser.add_argument('--robust',action='store_true',help='v2: first-order startup stage then second order')
parser.add_argument('--early-stop',action='store_true',help='v2: force-convergence monitor with the abort stop file')
parser.add_argument('--fo-probe',action='store_true',help='v2: assert the output function-object fields were written')
parser.add_argument('--onground',choices=['static','moving','slip'],help='v2: sphere above a ground plane (external mode)')
parser.add_argument('--wheels',action='store_true',help='v2: a detected cylinder wheel rolling on a moving ground')
parser.add_argument('--multi-mrf',action='store_true',help='v2: two spheres, each in its own MRF zone (rotor mode)')
parser.add_argument('--porous',action='store_true',help='v2: a radiator box upstream of the sphere (external mode)')
parser.add_argument('--openings',action='store_true',help='v2: flow-rate inlet opening on the duct (internal mode)')
parser.add_argument('--porous-curve',default='.5,1,1,3',help='Radiator curve v1,dp1,v2,dp2 (m/s, Pa)')
parser.add_argument('--porous-thickness',type=float,default=.04,help='Radiator thickness (m)')
parser.add_argument('--iterations',type=int,help='Override the 40-iteration smoke budget')
parser.add_argument('--transient',action='store_true',help='v2: steady start, then pimpleFoam with time averaging and animation frames (external)')
parser.add_argument('--adjoint',action='store_true',help='v2: adjoint design-advice sensitivity after the steady solve (external, no rotating parts)')
parser.add_argument('--morph',action='store_true',help='v2: one design box and a short shape morph; implies --adjoint')
parser.add_argument('--compile-only',action='store_true',help='Validate and write the case without OpenFOAM (any OS)')
args=parser.parse_args()
if args.wheels or args.onground:
    if args.mode!='external':raise SystemExit('--onground and --wheels need --mode external')
if args.multi_mrf:args.mode='rotor'
if args.openings:args.mode='internal'
elements=bool(args.onground or args.wheels or args.multi_mrf or args.porous or args.openings)
if args.transient and args.mode!='external':raise SystemExit('--transient needs --mode external')
if args.morph:args.adjoint=True
if args.adjoint and args.mode!='external':raise SystemExit('--adjoint needs --mode external')
v2=args.v2 or args.parallel_mesh or args.potential or args.robust or args.early_stop or args.fo_probe or elements or args.transient or args.adjoint


def two_spheres():
    """One STL with two spheres; each becomes its own connected surface (and MRF zone)."""
    import trimesh
    solids=[]
    for name,x in (('rotor_a',-.25),('rotor_b',.25)):
        m=trimesh.creation.icosphere(subdivisions=3,radius=.06);m.apply_translation((x,0,0))
        lines=[f'solid {name}']
        for tri,n in zip(m.triangles,m.face_normals):
            lines+=[f' facet normal {n[0]:.7e} {n[1]:.7e} {n[2]:.7e}','  outer loop',*[f'   vertex {v[0]:.7e} {v[1]:.7e} {v[2]:.7e}' for v in tri],'  endloop',' endfacet']
        solids.append('\n'.join(lines+[f'endsolid {name}']))
    return geometry.import_file('smoke_rotors.stl',('\n'.join(solids)+'\n').encode(),units='m')


def coarse(s):
    s.flow.speed=.01;s.flow.turbulence='laminar';s.mesh.tier='custom';s.mesh.base_cells=20;s.mesh.surface_level=2;s.mesh.feature_level=2
    s.mesh.body_level=0;s.mesh.layers=0;s.solver.iterations=40;s.solver.write_interval=20;s.solver.processes=args.processes
    if args.turbulent:s.flow.turbulence='kOmegaSST';s.flow.speed=2;s.mesh.layers=2


def drain_post():
    """Run the queued post-lane jobs (results package, report) in this process."""
    for _ in range(10):
        job=db.claim(('post',))
        if not job:return
        process_job(job)
        assert db.run(job['id'])['status']=='completed',(job['kind'],db.run(job['id'])['error'])


if args.wheels:
    g=geometry.add_primitive(Primitive(shape='cylinder',name='smoke_wheel',radius=.1,length=.06,axis=(0,1,0)))
elif args.multi_mrf:
    g=two_spheres()
else:
    g=geometry.add_primitive(Primitive(shape='cylinder' if args.mode=='internal' else 'sphere',name='smoke_'+args.mode,radius=.1,length=.4,role='enclosure' if args.mode=='internal' else 'solid'))
patches=[p['name'] for p in g['patches']]
if args.multi_mrf:
    # STL import names surfaces by connected body; pick each sphere by its centre.
    mesh,groups=geometry.load(g['id'])
    side={p['name']:float(mesh.triangles_center[groups==p['group']][:,0].mean()) for p in g['patches']}
    assert len(side)==2,side
    left,right=min(side,key=side.get),max(side,key=side.get)
if args.onground or args.wheels:
    # The preset places the ground plane (bottom face) under the model, as the setup page does.
    request=PresetRequest(geometry_id=g['id'],kind='aerodynamics',speed=1,ground=args.onground or 'moving',
                          ride_height_m=0 if args.wheels else .02,patches=patches)
    s=SimulationSpec.model_validate(presets.generate(request)['spec']);s.use_case.confirmed=True;s.use_case.references_confirmed=True
else:
    s=validation.defaults(g['id'])
s.name='Runtime smoke test';coarse(s)
if args.mode=='internal':
    s.mode='internal';s.domain.minimum=(-.3,-.2,-.2);s.domain.maximum=(.3,.2,.2);s.domain.fluid_point=(0,0,0)
    mesh,groups=geometry.load(g['id']);s.boundaries=[]
    for patch in g['patches']:
        center=mesh.triangles_center[groups==patch['group']].mean(axis=0)
        kind='velocity_inlet' if center[0]<-.19 else 'pressure_outlet' if center[0]>.19 else 'wall'
        s.boundaries.append(Boundary(patch=patch['name'],kind=kind))
if args.mode=='rotor':
    rotating=[left] if args.multi_mrf else patches
    s.mode='rotor';s.rotation=Rotation(enabled=True,patches=rotating,radius=.15,length=.3,blade_radius=.101,rpm=10)
    if args.multi_mrf:s.rotation=Rotation(enabled=True,origin=(-.25,0,0),patches=rotating,radius=.14,length=.3,blade_radius=.061,rpm=10)
    if args.static:
        s.flow.speed=0;s.rotation.rpm=-10
        for b in s.boundaries:
            if b.patch in validation.DOMAIN_PATCHES:b.kind='freestream'
        s.use_case=UseCase(kind='propeller',confirmed=True,force_patches=s.rotation.patches)
if v2:
    s.schema_version=2
    s.mesh.parallel=args.parallel_mesh
    if args.parallel_mesh and s.solver.processes<2:raise SystemExit('--parallel-mesh needs --processes 2 or more')
    if args.potential:s.solver.initialise='potential'
    if args.robust:s.solver.numerics='robust';s.solver.iterations=max(s.solver.iterations,260)
    if args.early_stop:
        s.flow.speed=max(s.flow.speed,1.0);s.solver.iterations=3000
        s.convergence=Convergence(enabled=True,min_iterations=100,averaging_min=50,check_every=10,cap=3000)
if args.wheels:
    found=wheels.detect(g['id'])
    assert found['candidates'],'wheel detection missed the smoke wheel'
    c=found['candidates'][0];print('Detected wheel:',c['reasons'])
    s.elements.wheels=[Wheel(name='wheel',patches=c['patches'],center=c['center'],axis=c['axis'],radius=c['radius'])]
if args.multi_mrf:
    # Two small spheres in a long domain: one more surface level keeps both in the coarse mesh.
    s.mesh.surface_level=3;s.mesh.feature_level=3
    s.elements.rotating_zones=[RotatingZone(name='fan',origin=(.25,0,0),axis=(1,0,0),rpm=-10,radius=.14,length=.3,blade_radius=.061,patches=[right])]
if args.porous:
    # External: a free-standing radiator upstream of the sphere (air can bypass it).
    # Internal: a radiator spanning the duct, so all the flow passes through it.
    duct=args.mode=='internal'
    v1,p1,v2,p2=(float(x) for x in args.porous_curve.split(','));curve=((v1,p1),(v2,p2))
    s.flow.speed=.5 if duct else 1
    if duct:
        # Slip walls keep the duct flow uniform, so the measured drop isolates the porous model
        # (with no-slip walls the local |U|U term averages above the mean-velocity curve).
        for boundary in s.boundaries:
            if boundary.kind=='wall':boundary.kind='slip'
    flow=np.asarray((1,0,0) if duct else s.flow.velocity(),float);flow/=np.linalg.norm(flow)
    s.elements.porous=[PorousZone(name='rad',center=(0,0,0) if duct else tuple(-flow*.25),axis=tuple(flow),up=(0,0,1),
                                  width_m=.25 if duct else .15,height_m=.25 if duct else .15,thickness_m=args.porous_thickness,curve=curve)]
if args.transient:
    from gustsim.models import TimeControl
    s.flow.speed=.2
    if not args.early_stop:s.solver.iterations=60
    s.time=TimeControl(mode='transient',flow_throughs=1.5,frames=8,average_after_fraction=.4)
if args.adjoint:
    s.schema_version=2
    s.design.enabled=True
    s.design.objective='drag'
    s.design.iterations=30
    s.design.cycles=2
    s.flow.turbulence='laminar'
    if args.morph:
        s.elements.design_boxes=[DesignBox(name='nose',center=(.05,0,0),dimensions=(.12,.12,.12),control_points=(4,4,4),max_displacement_m=.002)]
if args.iterations:s.solver.iterations=args.iterations
if args.openings:
    inlet=next(b.patch for b in s.boundaries if b.kind=='velocity_inlet')
    flow_rate=s.flow.speed*np.pi*.1**2
    s.elements.openings=[Opening(patch=inlet,kind='inflow_flow_rate',value=flow_rate)]
report=validation.validate(s)
assert report['valid'],report['errors']
if elements:print('Experimental features:',report['experimental_features'])
if args.compile_only:
    from gustsim import foam
    case=config.DATA/'compile_only';foam.compile_case(s,case)
    print('Compiled',sorted(p.relative_to(case).as_posix() for p in case.rglob('*') if p.is_file() and 'triSurface' not in p.parts))
    raise SystemExit(0)
job=db.enqueue(s.model_dump());db.claim();process_job(job)
r=db.run(job['id'])
print(json.dumps({'id':r['id'],'status':r['status'],'stage':r['stage'],'error':r['error'],'metrics':r['result'].get('metrics'),
                  'fields_available':r['result'].get('fields_available'),'termination':r['result'].get('termination'),
                  'mesh_method':(r['result'].get('mesh_summary') or {}).get('method')},indent=2))
if r['status']!='completed':
    for finding in r['result'].get('findings') or []:
        if finding.get('status') in ('fail','review','unavailable'):print('FINDING',json.dumps(finding))
    summary=r['result'].get('mesh_summary') or {}
    print('MESH',json.dumps({k:v for k,v in summary.items() if k not in ('history','sections')},default=str)[:3000])
    raise SystemExit(1)
root=config.DATA/'runs'/r['id']
if args.parallel_mesh:
    assert r['result']['mesh_summary']['method']=='parallel',r['result']['mesh_summary']['method']
    # The mesh was reconstructed into constant/; a parallel solve decomposes it again afterwards.
    assert (root/'constant/polyMesh/owner').is_file() and (root/'logs/reconstruct_mesh.log').is_file()
if args.potential:
    assert any(f['code']=='initialisation' for f in r['result']['findings'])
    print([f['detail'] for f in r['result']['findings'] if f['code']=='initialisation'])
if args.robust:
    assert (root/'logs/solve_startup.log').exists()
if args.early_stop:
    summary=json.loads((root/'results/convergence.json').read_text())
    print(summary['message'])
    # The abort function object deletes gustsim.stop when the run ends; the monitor records the request.
    assert (summary.get('stop') or {}).get('requested_at'),'the monitor never requested a stop'
    assert summary['state']=='converged',summary['state']
    if not args.transient:  # a transient run continues past the steady stop
        assert r['result']['termination']=='force_convergence',r['result']['termination']
        final=max(float(p.name) for p in root.iterdir() if p.is_dir() and p.name.replace('.','',1).isdigit())
        assert final<3000 and final>=summary['stop']['requested_at'],'the solver wrote the iteration it stopped at'
if args.fo_probe:
    final=max((p for p in root.iterdir() if p.is_dir() and p.name.replace('.','',1).isdigit() and p.name!='0'),key=lambda p:float(p.name))
    written={p.name for p in final.iterdir()}
    expected={'wallShearStress','Q','vorticity'} | ({'Cp','Cpt'} if s.flow.speed>0 else set()) | ({'L_P'} if s.flow.turbulence!='laminar' else set())
    missing=expected-written
    print('Output fields at',final.name,sorted(written))
    assert not missing,f'function objects did not write: {sorted(missing)} (check keywords on this OpenFOAM release)'
if args.adjoint:
    advice=[f for f in r['result']['findings'] if f['code']=='design_advice']
    assert advice and advice[-1]['status']=='info',advice
    assert (root/'results'/'adjoint_run.json').is_file()
    parked=json.loads((root/'results'/'adjoint_run.json').read_text())
    assert parked['fields'],parked
if args.morph:
    morph=db.claim(('compute',))
    assert morph and morph['kind']=='optimise',morph
    process_job(morph)
    done=db.run(morph['id'])
    assert done['status']=='completed',done['error']
    assert (done['result'].get('morph') or {}).get('geometry_id'),done['result']
if v2 and not args.static:
    # The solve queued the post-lane results package (pvbatch) and the PDF report; run them here.
    from gustsim import manifest as package_manifest
    drain_post()
    built=json.loads((root/package_manifest.PACKAGE/'manifest.json').read_text())
    print('Package layers:',[layer['id'] for layer in built['layers']],'missing:',built['missing'])
    if any(b.kind in ('wall','moving_wall','rotating_wall') for b in s.boundaries):
        assert any(layer['id']=='surface' for layer in built['layers']),built['missing']
    assert (root/'results/report.pdf').is_file(),'the PDF report was not built'
if elements:
    result=json.loads((root/'results/elements.json').read_text())
    print(json.dumps(result,indent=2))
    velocity=(root/'0/U').read_text()
    if args.onground or args.wheels:
        assert 'ground' in velocity,'the ground patch is missing from 0/U'
    if args.wheels:
        assert 'rotatingWallVelocity' in velocity,'the wheel patch does not rotate'
        assert result['wheels'] and result['wheels'][0]['drag_n'] is not None,'no wheel loads were measured'
    if args.multi_mrf:
        mrf=(root/'constant/MRFProperties').read_text()
        assert 'fan' in mrf and 'rotor' in mrf,'both MRF zones must be compiled'
        fan=next(z for z in result['rotating_zones'] if z['name']=='fan')
        assert fan.get('fluid_torque_nm') is not None,'the second zone has no measured torque'
    if args.porous:
        assert (root/'constant/fvOptions').exists()
        rad=result['porous'][0]
        assert rad.get('mean_velocity_m_s') is not None and rad.get('pressure_drop_pa') is not None,'radiator flow was not measured'
        print(f"Radiator: v={rad['mean_velocity_m_s']:.3g} m/s, measured dp={rad['pressure_drop_pa']:.3g} Pa, curve dp={rad['curve_pressure_drop_pa']:.3g} Pa")
        if args.mode=='internal':
            error=abs(rad['pressure_drop_pa']/rad['curve_pressure_drop_pa']-1)
            print(f'Duct radiator: measured drop is {100*error:.2g} % from the curve (smoke tolerance 10 %, gate 2 %)')
            assert error<=.1,'the porous zone does not reproduce its pressure-drop curve in a uniform duct'
    if args.openings:
        assert 'flowRateInletVelocity' in velocity
        opening=next(o for o in result['openings'] if o['patch']==inlet)
        assert opening['volume_flow_m3_s'] is not None and abs(abs(opening['volume_flow_m3_s'])-flow_rate)<=.02*flow_rate,opening
if args.transient:
    stats=json.loads((root/'results/transient.json').read_text())
    plan=stats['plan']
    print('Transient:',stats['state'],stats['message'],'reached',stats.get('duration_reached_s'),'of',plan['duration_s'],'s; St',stats.get('strouhal'))
    assert stats['state']!='unavailable' and stats.get('duration_reached_s',0)>=plan['duration_s']*(1-1e-6),'the transient stage did not run to its end time'
    import re
    steps=[float(v) for v in re.findall(r'^deltaT = ([-+0-9.eE]+)',(root/'logs/transient.log').read_text(),re.M)]
    print('Transient steps:',len(steps),'first',steps[:1],'planned first',plan['first_step_s'])
    assert steps and steps[0]<=10*plan['first_step_s'],'the first transient step ignored the Courant-based start (steady time state restored?)'
    latest=max((p for p in root.iterdir() if p.is_dir() and p.name.replace('.','',1).isdigit()),key=lambda p:float(p.name))
    assert (latest/'UMean').exists() and (latest/'pMean').exists(),'fieldAverage did not write the mean fields'
    frames=sorted((root/'postProcessing/frames').iterdir())
    assert len(frames)>=plan['frames']-1,f'only {len(frames)} animation frames were written'
    print('Frame files:',sorted(p.name for p in frames[0].iterdir()))
    built=json.loads((root/package_manifest.PACKAGE/'manifest.json').read_text())
    assert built['basis']=='time_averaged',built['basis']
    assert any(layer['kind']=='animation' for layer in built['layers']),built['missing']
    assert r['result']['termination']=='transient_complete',r['result']['termination']
if args.static:
    assert r['result']['metrics']['fluid_torque_nm'] is not None
    assert r['result']['metrics']['efficiency'] is None
    expected=db.run(r['id'])['result']['expected_views']
    for _ in range(expected):
        view=db.claim(('post',));assert view and view['kind']=='view';process_job(view)
        assert db.run(view['id'])['status']=='completed',db.run(view['id'])['error']
    print(f'All {expected} automatic views completed on the static rotor solution')
