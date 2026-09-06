import json
import math
import numpy as np
import pytest
from pydantic import ValidationError
from gustsim import geometry, validation, foam
from gustsim.models import Primitive, Boundary, Rotation, Flow, SimulationSpec

def sample():
    g=geometry.add_primitive(Primitive(shape='sphere',name='sphere'))
    return g,validation.defaults(g['id'])

def test_pressure_conversion_and_case(tmp_path):
    g,s=sample();s.fluid.density=1000
    b=Boundary(patch='outlet',kind='pressure_outlet',pressure_pa=2000)
    assert 'uniform 2.0;' in foam.boundary_field(s,b,'p')
    case=foam.compile_case(s,tmp_path/'case')
    assert 'simpleFoam' in (case/'system/controlDict').read_text()
    assert 'simulationType RAS' in (case/'constant/turbulenceProperties').read_text()
    assert len(list((case/'constant/triSurface').glob('*.stl')))==len(g['patches'])
    assert json.loads((case/'provenance.json').read_text())['geometry_sha256']==g['sha256']

def test_orientation_and_invalid_axes():
    assert np.allclose(Flow(speed=10,alpha_deg=90).velocity(),[0,0,10],atol=1e-10)
    assert np.allclose(Flow(speed=10,beta_deg=90).velocity(),[0,10,0],atol=1e-10)
    with pytest.raises(ValidationError):Rotation(axis=(0,0,0))
    with pytest.raises(ValidationError):Boundary(patch='body; #codeStream',kind='wall')

def test_missing_boundary_and_solid_fluid_point():
    _,s=sample();s.boundaries.pop()
    assert not validation.validate(s)['valid']
    _,s=sample();s.domain.fluid_point=(0,0,0)
    assert any('inside the solid' in e for e in validation.validate(s)['errors'])

def test_laminar_case_has_no_turbulence_fields(tmp_path):
    _,s=sample();s.flow.turbulence='laminar';case=foam.compile_case(s,tmp_path/'case')
    assert not (case/'0/k').exists()
    assert 'simulationType laminar' in (case/'constant/turbulenceProperties').read_text()

def test_mrf_geometry_and_signed_rpm(tmp_path):
    g,s=sample();s.rotation=Rotation(enabled=True,patches=[p['name'] for p in g['patches']],radius=.7,length=1.3,blade_radius=.51,rpm=-100)
    s.mode='rotor';case=foam.compile_case(s,tmp_path/'case')
    assert 'omega -10.4719' in (case/'constant/MRFProperties').read_text()
    assert 'cellZone rotor' in (case/'constant/MRFProperties').read_text()
    assert not (case/'constant/triSurface/rotorRegion.stl').exists()
    s.rotation.radius=.2
    assert not validation.validate(s)['valid']

def test_mach_checks_rotor_tip():
    g,s=sample();s.rotation=Rotation(enabled=True,patches=[p['name'] for p in g['patches']],radius=.7,length=1.3,blade_radius=.51,rpm=20000)
    assert any('Mach' in e for e in validation.validate(s)['errors'])
