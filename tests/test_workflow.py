import json
import numpy as np
import pytest
from fastapi.testclient import TestClient
from gustsim import geometry, validation, db, workflow, presets, quality, foam, config
from gustsim.models import Primitive, Prepare, PresetRequest, RotorSuggestion, SimulationSpec
from gustsim.api import app


def pipe():
    g=geometry.add_primitive(Primitive(shape='cylinder',radius=.1,length=.4,role='enclosure'))
    mesh,groups=geometry.load(g['id'])
    caps=sorted([(mesh.triangles_center[groups==p['group']].mean(axis=0)[0],p['name']) for p in g['patches']])
    return g,caps[0][1],caps[-1][1]


@pytest.mark.parametrize('driving',['flow','speed','pressure'])
def test_pipe_presets(driving,tmp_path):
    g,inlet,outlet=pipe()
    preview=presets.generate(PresetRequest(geometry_id=g['id'],kind='pipe',inlet=inlet,outlet=outlet,driving=driving,speed=.1,turbulence='laminar'))
    assert not preview['report']['valid']
    spec=SimulationSpec.model_validate(preview['spec']);spec.use_case.confirmed=True
    assert validation.validate(spec)['valid']
    foam.compile_case(spec,tmp_path/'case')
    text=(tmp_path/'case/system/controlDict').read_text()
    assert 'weightField phi' in text and 'operation sumMag' in text
    b=next(b for b in spec.boundaries if b.patch==inlet)
    assert b.kind=={'flow':'flow_inlet','speed':'velocity_inlet','pressure':'pressure_inlet'}[driving]
    if driving=='pressure': assert 'totalPressure' in foam.boundary_field(spec,b,'p')


def test_external_direction_and_static_rotor():
    g=geometry.add_primitive(Primitive(shape='sphere',radius=.1))
    selected=[p['name'] for p in g['patches']]
    req=PresetRequest(geometry_id=g['id'],kind='aerodynamics',patches=selected,direction=(1,1,0),lift_axis=(0,0,1))
    s=SimulationSpec.model_validate(presets.generate(req)['spec']);s.use_case.confirmed=True
    np.testing.assert_allclose(s.flow.velocity(),-np.array([1,1,0])/np.sqrt(2)*10,atol=1e-10)
    assert validation.validate(s)['valid']
    r=SimulationSpec.model_validate(presets.generate(PresetRequest(geometry_id=g['id'],kind='propeller',patches=selected,speed=0,rpm=-10))['spec'])
    r.use_case.confirmed=True
    assert r.rotation.rpm==-10 and r.flow.speed==0 and validation.validate(r)['valid']
    assert r.rotation.radius>r.rotation.blade_radius
    far=next(b for b in r.boundaries if b.kind=='freestream')
    assert 'pressureInletOutletVelocity' in foam.boundary_field(r,far,'U')
    assert 'totalPressure' in foam.boundary_field(r,far,'p')


def test_readiness_identity_and_mesh_review():
    g=geometry.add_primitive(Primitive(shape='sphere'))
    spec=validation.defaults(g['id']);original=workflow.identity(spec)
    spec.name='renamed';spec.solver.iterations=20
    assert workflow.identity(spec)==original
    legacy=spec.model_dump();legacy.pop('use_case')
    assert workflow.identity(legacy)==original
    run=db.enqueue(spec.model_dump(),'mesh');db.update(run['id'],status='completed')
    with pytest.raises(ValueError,match='evidence'): workflow.require_mesh(db.run(run['id']),spec)
    root=config.DATA/'runs'/run['id'];root.mkdir(parents=True)
    findings=[{'code':code,'status':'review' if code=='mesh_extended' else 'pass'} for code in ('mesh_quality','mesh_boundaries','mesh_preview','mesh_rotation','mesh_extended')]
    db.update(run['id'],result={'mesh_check':'passed','mesh_available':True,'mesh_summary':{'findings':findings}})
    with TestClient(app) as c:
        url=f"/api/runs/{run['id']}/review?configuration_id={original}"
        assert c.post(url+'&acknowledge_warnings=true').status_code==422
        (root/'mesh-preview.vtp').write_text('fixture preview')
        assert c.post(url).status_code==422
        assert c.post(url+'&acknowledge_warnings=true').status_code==200
        db.heartbeat()
        assert c.post(f"/api/runs?guided=true&mesh_run_id={run['id']}",json=spec.model_dump()).status_code==202
        spec.flow.speed*=2
        assert c.post(f"/api/runs?guided=true&mesh_run_id={run['id']}",json=spec.model_dump()).status_code==422


def table(root,name,value):
    folder=root/'postProcessing'/name/'0';folder.mkdir(parents=True,exist_ok=True)
    (folder/'surfaceFieldValue.dat').write_text(f'1 {value}\n')


def test_incomplete_rotor_draft_has_actionable_checks():
    g=geometry.add_primitive(Primitive(shape='sphere'))
    s=validation.defaults(g['id']).model_dump();s['rotation']['enabled']=True
    with TestClient(app) as c:
        response=c.post('/api/validate',json=s)
        assert response.status_code==200
        report=response.json()
        assert not report['valid'] and report['findings'][0]['control']=='rotating-region'
        assert c.post('/api/runs',json=s).status_code==422


def test_pipe_loss_units_reverse_and_missing(tmp_path):
    from gustsim.analysis import pipe_metrics
    g,a,b=pipe();s=SimulationSpec.model_validate(presets.generate(PresetRequest(geometry_id=g['id'],kind='pipe',inlet=a,outlet=b))['spec'])
    for patch,total in ((a,200),(b,50)):
        table(tmp_path,'totalpressure_'+patch,total);table(tmp_path,'absoluteFlux_'+patch,.01)
    metrics,findings,reason=pipe_metrics(tmp_path,s,{a:-.01,b:.01},{a:120,b:40})
    assert metrics['total_pressure_loss_pa']==150 and metrics['outlet_mass_flow_kgs']==pytest.approx(.01*s.fluid.density)
    assert metrics['head_loss_m']==pytest.approx(150/s.fluid.density/9.80665)
    table(tmp_path,'absoluteFlux_'+b,.02)
    metrics,findings,reason=pipe_metrics(tmp_path,s,{a:-.01,b:.01},{})
    assert metrics['total_pressure_loss_pa'] is None and findings[0]['status']=='review'


def test_default_views_are_idempotent_and_keep_rotor_selection():
    from gustsim.analysis import queue_default_views,default_views
    g=geometry.add_primitive(Primitive(shape='sphere',radius=.1));patches=[p['name'] for p in g['patches']]
    s=SimulationSpec.model_validate(presets.generate(PresetRequest(geometry_id=g['id'],kind='propeller',patches=patches,speed=0,rpm=10))['spec'])
    job=db.enqueue(s.model_dump());queue_default_views(job['id'],s);queue_default_views(job['id'],s)
    assert len([r for r in db.runs() if r['kind']=='view'])==2
    assert default_views(s)[0].patches==patches
    with TestClient(app) as c:
        assert all(v['automatic'] for v in c.get(f"/api/runs/{job['id']}/views").json())


def test_nested_step_instances_and_component_repair(tmp_path):
    pytest.importorskip('OCP')
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Trsf,gp_Vec,gp_Ax1,gp_Pnt,gp_Dir
    from OCP.TopLoc import TopLoc_Location
    from OCP.TDataStd import TDataStd_Name
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    doc=TDocStd_Document(TCollection_ExtendedString('nested'))
    tool=XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    part=tool.AddShape(BRepPrimAPI_MakeBox(100,100,100).Shape(),False)
    child=tool.NewShape();root=tool.NewShape()
    def loc(x,y,z):
        tr=gp_Trsf();tr.SetTranslation(gp_Vec(x,y,z));return TopLoc_Location(tr)
    for position in (0,200):
        instance=tool.AddComponent(child,part,loc(position,0,0));TDataStd_Name.Set_s(instance,TCollection_ExtendedString('Blade'))
    placement=gp_Trsf();placement.SetRotation(gp_Ax1(gp_Pnt(0,0,0),gp_Dir(0,0,1)),np.pi/2);placement.SetTranslationPart(gp_Vec(0,300,0))
    tool.AddComponent(root,child,TopLoc_Location(placement));tool.UpdateAssemblies()
    writer=STEPCAFControl_Writer();writer.Transfer(doc,STEPControl_AsIs)
    path=tmp_path/'nested.step';writer.Write(str(path))
    g=geometry.import_step('nested.step',path.read_bytes())
    children={p['parent_id'] for p in g['parts']};leaves=[p for p in g['parts'] if p['id'] not in children]
    assert len(leaves)==2 and len({p['id'] for p in leaves})==2
    assert leaves[0]['name']==leaves[1]['name']=='Blade'
    np.testing.assert_allclose(g['bounds'],[[-.1,.3,0],[0,.6,.1]],atol=1e-8)
    repaired=geometry.prepare(g['id'],Prepare(repair=True))
    assert repaired['diagnostics']['components']==2
    assert repaired['parts']==g['parts']
    assert all(v==[k] for k,v in repaired['patch_mapping'].items())
    with pytest.raises(ValueError,match='one component'):
        geometry.prepare(g['id'],Prepare(merge_patches=[leaves[0]['patches'][0],leaves[1]['patches'][0]]))
    moved=geometry.prepare(g['id'],Prepare(scale=2,translation=(1,0,0)))
    assert moved['parts'][0]['placement'][0][3]==1
    s=validation.defaults(g['id']);s.rotation.enabled=True;s.rotation.patches=[leaves[0]['patches'][0]]
    assert any('only part of component' in e for e in validation.validate(s)['errors'])


def test_appended_component_provenance():
    original=geometry.add_primitive(Primitive(shape='sphere',name='body',radius=.1))
    added=geometry.add_primitive(Primitive(shape='sphere',name='rotor',radius=.05,center=(.5,0,0)),original['id'])
    assert len(added['parts'])==2
    assert {n for part in added['parts'] for n in part['patches']}=={p['name'] for p in added['patches']}
    repaired=geometry.prepare(added['id'],Prepare(repair=True))
    assert repaired['parts']==added['parts']
    assert repaired['diagnostics']['components']==2


def test_force_projection_torque_shift_and_zero_advance(tmp_path):
    g=geometry.add_primitive(Primitive(shape='sphere',radius=.1))
    s=SimulationSpec.model_validate(presets.generate(PresetRequest(geometry_id=g['id'],kind='propeller',patches=[p['name'] for p in g['patches']],speed=0,rpm=-60,axis=(0,0,1)))['spec'])
    s.references.origin=(0,0,0);s.rotation.origin=(1,0,0)
    s.references.drag_axis=(0,1,0);s.references.lift_axis=(1,0,0);s.references.thrust_axis=(0,0,1)
    for name in ('loads','rotorLoads'):
        folder=tmp_path/'postProcessing'/name/'0';folder.mkdir(parents=True)
        (folder/'force.dat').write_text('1 (2 3 4) (0 0 0) (2 3 4)\n')
        (folder/'moment.dat').write_text('1 (0 0 10) (0 0 0) (0 0 10)\n')
    result=quality.analyze(tmp_path,s);m=result['metrics']
    assert m['drag_n']==3 and m['lift_n']==2 and m['thrust_n']==4
    assert m['fluid_torque_nm']==7 and m['torque_nm']==7
    assert result['projected_history'][-1]['drag_n']==3
    assert result['rotor_load_history'][-1]['fluid_torque_nm']==7
    assert m['shaft_power_w']==pytest.approx(14*np.pi)
    assert m['efficiency'] is None and 'zero advance' in result['analysis']['unavailable']['efficiency']


def test_export_buttons_reflect_actual_artifacts():
    g=geometry.add_primitive(Primitive(shape='sphere'))
    run=db.enqueue(validation.defaults(g['id']).model_dump())
    root=config.DATA/'runs'/run['id'];root.mkdir(parents=True)
    (root/'simulation.json').write_text('{}')
    with TestClient(app) as c:
        available=c.get('/api/runs/'+run['id']).json()['result']['export_available']
        assert available['case'] and not available['hdf5'] and not available['csv']


def test_guided_retry_and_study_start_with_reviewable_meshes():
    g=geometry.add_primitive(Primitive(shape='sphere'))
    spec=validation.defaults(g['id']);db.heartbeat()
    failed=db.enqueue(spec.model_dump(),'solve');db.update(failed['id'],status='failed')
    with TestClient(app) as c:
        retry=c.post(f"/api/runs/{failed['id']}/retry?guided=true").json()
        assert retry['kind']=='mesh' and retry['parent_id']==failed['id']
        sweep=c.post('/api/studies?guided=true',json={'spec':spec.model_dump(),'parameter':'speed','values':[1,2]})
        assert sweep.status_code==201 and all(r['kind']=='mesh' for r in sweep.json()['runs'])
        spec.mesh.wall_treatment='resolved';spec.mesh.layers=1
        assert c.post('/api/studies?guided=true',json={'spec':spec.model_dump(),'parameter':'speed','values':[1,2]}).status_code==422


def test_pipe_requires_planar_outlet():
    g,a,b=pipe()
    side=next(p['name'] for p in g['patches'] if p['name'] not in (a,b))
    with pytest.raises(ValueError,match='outlet must be planar'):
        presets.generate(PresetRequest(geometry_id=g['id'],kind='pipe',inlet=a,outlet=side))
