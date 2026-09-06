import pytest
import numpy as np
from fastapi.testclient import TestClient
from gustsim import geometry, validation, presets, workflow, foam, projects, progress
from gustsim.models import Primitive, Prepare, PresetRequest, SimulationSpec
from gustsim.api import app

@pytest.mark.parametrize('kind',['aerodynamics','propeller'])
@pytest.mark.parametrize('fluid',['air','water'])
def test_external_presets_support_both_fluids(kind,fluid):
    g=geometry.add_primitive(Primitive(shape='sphere',radius=.1))
    s=SimulationSpec.model_validate(presets.generate(PresetRequest(geometry_id=g['id'],kind=kind,fluid=fluid,patches=[p['name'] for p in g['patches']],speed=.1))['spec'])
    s.use_case.confirmed=True
    assert s.fluid.name==fluid and validation.validate(s)['valid']
    assert s.fluid.density>990 if fluid=='water' else s.fluid.density<2

def test_component_surfaces_keep_identity_and_original(tmp_path):
    pytest.importorskip('OCP')
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.STEPControl import STEPControl_Writer,STEPControl_AsIs
    path=tmp_path/'box.step';writer=STEPControl_Writer()
    writer.Transfer(BRepPrimAPI_MakeBox(100,200,300).Shape(),STEPControl_AsIs);writer.Write(str(path))
    g=geometry.import_step('box.step',path.read_bytes())
    prepared=geometry.prepare(g['id'],Prepare(repair=True,component_surfaces=True))
    assert len(prepared['patches'])==1
    assert [p['id'] for p in prepared['parts']]==[p['id'] for p in g['parts']]
    assert set(prepared['patch_mapping'])=={p['name'] for p in g['patches']}
    assert all(v==[prepared['patches'][0]['name']] for v in prepared['patch_mapping'].values())
    assert prepared['diagnostics']['watertight']
    assert len(geometry.load(g['id'])[0].faces)==g['triangles']

def test_thin_body_refinement_is_local_and_invalidates_mesh(tmp_path):
    g=geometry.add_primitive(Primitive(shape='box',dimensions=(.5,.025,.04)))
    s=validation.defaults(g['id']);original=workflow.identity(s)
    assert s.mesh.body_level>s.mesh.surface_level
    with TestClient(app) as client:
        response=client.post('/api/mesh/recommend',json=s.model_dump())
        assert response.status_code==200 and response.json()['body_level']==s.mesh.body_level
    s.project_id='a'*32
    assert workflow.identity(s)==original
    foam.compile_case(s,tmp_path/'case')
    text=(tmp_path/'case/system/snappyHexMeshDict').read_text()
    assert 'bodyResolution' in text and f'levels ((1e15 {s.mesh.body_level}))' in text
    s.mesh.body_level+=1
    assert workflow.identity(s)!=original

def test_progress_uses_iteration_budget_and_never_calls_failure_complete(tmp_path):
    run={'kind':'solve','status':'running','stage':'solve','spec':{'solver':{'iterations':100}}}
    (tmp_path/'logs').mkdir();(tmp_path/'logs/solve.log').write_text('x'*70000+'\nTime = 50\n')
    p=progress.describe(run,tmp_path)
    assert p['basis']=='iteration budget' and '50 / 100' in p['label'] and p['percent']<100
    run['status']='failed'
    assert progress.describe(run,tmp_path)['percent']<100
    run['status']='completed'
    assert progress.describe(run,tmp_path)['percent']==100

def test_projects_preserve_independent_drafts_and_validate_paths():
    with TestClient(app) as client:
        a=client.post('/api/projects',json={'name':'A','workspace':{'spec':{'draft':'incomplete'}}}).json()
        b=client.post('/api/projects',json={'name':'B','workspace':{}}).json()
        assert a['id']!=b['id']
        assert client.get('/api/projects/'+a['id']).json()['workspace']['spec']['draft']=='incomplete'
        assert client.get('/api/projects/'+b['id']).json()['workspace']=={}
        assert client.put('/api/projects/'+a['id'],json={'name':'A','workspace':[]}).status_code==422
        assert client.get('/api/projects/'+'0'*32).status_code==404
        assert len(client.get('/api/projects').json())==2
    with pytest.raises(ValueError):projects.read('../bad')


def test_browser_roundtrip_signed_zero_does_not_stale_mesh():
    g=geometry.add_primitive(Primitive(shape='sphere'))
    s=validation.defaults(g['id'])
    s.references.thrust_axis=(-0.0,-1.0,-0.0)
    before=workflow.identity(s)
    s.references.thrust_axis=(0.0,-1.0,0.0)
    assert workflow.identity(s)==before
