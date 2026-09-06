import numpy as np
import pytest
import trimesh
from gustsim import geometry
from gustsim.models import Prepare, Primitive

def test_stl_units_and_immutable_transform():
    source=trimesh.creation.box(extents=[100,200,300]).export(file_type='stl')
    g=geometry.import_stl('box.stl',source,'mm')
    assert np.allclose(g['dimensions'],[.1,.2,.3])
    moved=geometry.prepare(g['id'],Prepare(scale=2,translation=(1,0,0)))
    assert np.allclose(moved['dimensions'],[.2,.4,.6])
    original,_=geometry.load(g['id'])
    assert np.allclose(original.centroid,0)
    assert moved['parent_id']==g['id']
    assert moved['sha256']!=g['sha256']

def test_internal_wall_isolation_and_caps():
    g=geometry.add_primitive(Primitive(shape='cylinder',radius=.5,length=2))
    # Cylinder wall is the largest smooth face group; removing ends exposes two loops.
    wall=max(g['patches'],key=lambda p:p['triangles'])
    open_g=geometry.prepare(g['id'],Prepare(keep_patches=[wall['name']]))
    assert len(open_g['diagnostics']['open_loops'])==2
    closed=geometry.prepare(open_g['id'],Prepare(cap_loops=[0,1]))
    assert closed['diagnostics']['watertight']
    assert closed['diagnostics']['winding_consistent']
    assert {'opening_0','opening_1'}<=set(p['name'] for p in closed['patches'])

def test_repair_preserves_group_mapping():
    mesh=trimesh.creation.box(); mesh.faces=np.vstack([mesh.faces,mesh.faces[0]])
    groups=np.zeros(len(mesh.faces),dtype=int)
    g=geometry.save(mesh,groups,{0:'body'},'bad.stl',confirmed=True)
    repaired=geometry.prepare(g['id'],Prepare(repair=True))
    assert repaired['triangles']==12
    assert repaired['patches'][0]['name']=='body'

def test_step_scale_and_faces(tmp_path):
    pytest.importorskip('OCP')
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.STEPControl import STEPControl_Writer,STEPControl_AsIs
    path=tmp_path/'box.step';w=STEPControl_Writer();w.Transfer(BRepPrimAPI_MakeBox(100,200,300).Shape(),STEPControl_AsIs);w.Write(str(path))
    g=geometry.import_step('box.step',path.read_bytes())
    assert np.allclose(g['dimensions'],[.1,.2,.3])
    assert len(g['patches'])==6 and g['cad_valid'] and g['parts']

def test_malformed_stl_is_rejected():
    with pytest.raises(ValueError):geometry.import_stl('bad.stl',b'not an STL','m')
