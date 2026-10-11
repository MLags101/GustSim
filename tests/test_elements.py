"""M4 experimental elements: ground and wheels, several rotating zones, porous zones and
openings. Compilation and validation only; physics is gated by the benchmark suite."""
import json
import math
import numpy as np
import pytest
from fastapi.testclient import TestClient
from gustsim import db, foam, foam_elements, geometry, presets, validation, wheels, workflow
from gustsim.api import app
from gustsim.models import Elements, Opening, PorousZone, Primitive, PresetRequest, RotatingZone, SimulationSpec, Wheel


def car():
    """A box body on four wheels (cylinders along Y) touching z = 0."""
    g = geometry.add_primitive(Primitive(shape='box', name='body', center=(0, 0, 0.6), dimensions=(4, 1.6, 0.8)))
    for i, (x, y) in enumerate([(1.3, 0.9), (1.3, -0.9), (-1.3, 0.9), (-1.3, -0.9)]):
        g = geometry.add_primitive(Primitive(shape='cylinder', name=f'wheel{i}', center=(x, y, 0.33), radius=0.33, length=0.25, axis=(0, 1, 0)), g['id'])
    return g


def ground_spec(g, ground='moving'):
    names = [p['name'] for p in g['patches']]
    data = presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=names, speed=30, ground=ground))['spec']
    data['use_case']['confirmed'] = True; data['use_case']['references_confirmed'] = True
    return SimulationSpec.model_validate(data)


def test_ground_preset_places_the_floor_and_moves_it():
    g = car()
    spec = ground_spec(g)
    height = g['bounds'][1][2] - g['bounds'][0][2]
    assert spec.domain.minimum[2] == pytest.approx(g['bounds'][0][2] + 0.005*height)
    assert spec.environment.ground == 'moving'
    ground = next(b for b in foam_elements.effective_boundaries(spec) if b.patch == 'ground')
    assert ground.kind == 'moving_wall' and ground.velocity is None
    report = validation.validate(spec)
    assert report['valid'], report['errors']
    assert 'ground_wheels' in report['experimental_features']
    with pytest.raises(ValueError, match='horizontal'):
        presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=[g['patches'][0]['name']], speed=30, ground='moving', direction=(1, 0, 1), lift_axis=(-1, 0, 1)))


def test_wheel_detection_finds_four_wheels_and_two_axles():
    g = car()
    found = wheels.detect(g['id'], forward=(-1, 0, 0))
    names = sorted(c['name'] for c in found['candidates'])
    assert names == ['wheel0', 'wheel1', 'wheel2', 'wheel3'], found['candidates']
    assert all(abs(c['radius'] - 0.33) < 0.03 and abs(abs(c['axis'][1]) - 1) < 1e-3 for c in found['candidates'])
    assert len(found['axles']) == 2 and found['wheelbase_m'] == pytest.approx(2.6, abs=0.05)
    assert found['front_axle'][0] < found['rear_axle'][0], 'travel along -X: the front axle has the smaller X'
    assert wheels.detect(geometry.add_primitive(Primitive(shape='sphere'))['id'])['candidates'] == []


def test_wheel_contact_point_moves_with_the_belt(tmp_path):
    g = car()
    spec = ground_spec(g)
    parts = {p['name']: p['patches'] for p in g['parts']}
    spec.elements = Elements(wheels=[Wheel(name=f'w{i}', patches=parts[f'wheel{i}'], center=c, axis=(0, 1, 0), radius=0.33)
                                     for i, c in enumerate([(1.3, 0.9, 0.33), (1.3, -0.9, 0.33), (-1.3, 0.9, 0.33), (-1.3, -0.9, 0.33)])])
    axis, rpm = foam_elements.wheel_rotation(spec, spec.elements.wheels[0])
    omega = rpm*math.pi/30
    contact = np.cross(omega*axis, np.array([0, 0, -0.33]))
    np.testing.assert_allclose(contact, spec.flow.velocity(), atol=1e-9)
    assert validation.validate(spec)['valid']
    case = foam.compile_case(spec, tmp_path/'case')
    field = (case/'0/U').read_text()
    assert field.count('rotatingWallVelocity') == sum(len(w.patches) for w in spec.elements.wheels)
    assert 'wheel_w0' in (case/'system/controlDict').read_text()
    no_ground = spec.model_copy(deep=True); no_ground.environment.ground = 'none'
    assert any('ground plane' in e for e in validation.validate(no_ground)['errors'])


def two_rotor_spec():
    g = geometry.add_primitive(Primitive(shape='sphere', name='left', center=(0, -1, 0), radius=0.1))
    g = geometry.add_primitive(Primitive(shape='sphere', name='right', center=(0, 1, 0), radius=0.1), g['id'])
    data = presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=[p['name'] for p in g['patches']], speed=5))['spec']
    data['use_case']['confirmed'] = True; data['use_case']['references_confirmed'] = True
    spec = SimulationSpec.model_validate(data)
    left = [p['name'] for p in g['patches'] if p['name'].startswith('left')]
    right = [p['name'] for p in g['patches'] if p['name'].startswith('right')]
    spec.elements = Elements(rotating_zones=[RotatingZone(name='fanA', origin=(0, -1, 0), axis=(1, 0, 0), rpm=500, radius=0.15, length=0.3, blade_radius=0.11, patches=left),
                                             RotatingZone(name='fanB', origin=(0, 1, 0), axis=(1, 0, 0), rpm=-500, radius=0.15, length=0.3, blade_radius=0.11, patches=right)])
    return spec


def test_two_rotating_zones_compile_and_overlap_is_rejected(tmp_path):
    spec = two_rotor_spec()
    report = validation.validate(spec)
    assert report['valid'], report['errors']
    assert 'mrf_multi' in report['experimental_features']
    case = foam.compile_case(spec, tmp_path/'case')
    mrf = (case/'constant/MRFProperties').read_text()
    assert 'fanA { active yes; cellZone fanA;' in mrf and 'fanB { active yes; cellZone fanB;' in mrf
    toposet = (case/'system/topoSetDict').read_text()
    assert toposet.count('cylinderToCell') == 2
    control = (case/'system/controlDict').read_text()
    assert 'rotorLoads_fanA' in control and 'rotorLoads_fanB' in control
    assert foam_elements.zone_list(spec) == ['fanA', 'fanB']
    close = spec.model_copy(deep=True)
    close.elements.rotating_zones[1].origin = (0, -0.9, 0)
    assert any('overlap' in e for e in validation.validate(close)['errors'])


def test_porous_fit_recovers_coefficients_and_rejects_bad_curves():
    mu, rho, thickness = 1.825e-5, 1.204, 0.05
    d, f = 2e7, 50.0
    dp = lambda v: thickness*(mu*d*v + 0.5*rho*f*v*v)
    fit = foam_elements.porous_fit(((5, dp(5)), (7, dp(7))), mu, rho, thickness)
    assert fit['d'] == pytest.approx(d, rel=1e-9) and fit['f'] == pytest.approx(f, rel=1e-9)
    with pytest.raises(ValueError):
        foam_elements.porous_fit(((5, 400), (7, 410)), mu, rho, thickness)
    with pytest.raises(Exception):
        PorousZone(name='r', center=(0, 0, 0), width_m=1, height_m=1, thickness_m=0.1, curve=((7, 556), (5, 330)))
    with TestClient(app) as client:
        body = client.post('/api/porous/fit', json={'curve': [[5, dp(5)], [7, dp(7)]], 'thickness_m': thickness}).json()
        assert body['d'] == pytest.approx(d, rel=1e-9)


def test_porous_zone_compiles_source_and_measurement_zones(tmp_path):
    g = geometry.add_primitive(Primitive(shape='box', name='body', dimensions=(1, 1, 1)))
    data = presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=[p['name'] for p in g['patches']], speed=10))['spec']
    data['use_case']['confirmed'] = True; data['use_case']['references_confirmed'] = True
    spec = SimulationSpec.model_validate(data)
    spec.elements = Elements(porous=[PorousZone(name='rad', center=(-1.2, 0, 0), axis=(1, 0, 0), width_m=0.6, height_m=0.4, thickness_m=0.05, curve=((5, 330), (7, 556)))])
    report = validation.validate(spec)
    assert report['valid'], report['errors']
    assert 'porous' in report['experimental_features']
    case = foam.compile_case(spec, tmp_path/'case')
    options = (case/'constant/fvOptions').read_text()
    # Coefficients are sized for the thickness the cell zone will have: whole cell layers at the
    # radiator's refinement level, at least eight across.
    zone = spec.elements.porous[0]
    level = foam_elements.porous_level(spec, zone, spec.mesh.surface_level)
    thickness, layers, _ = foam_elements.porous_effective_thickness(spec, zone, level)
    capped = level == spec.mesh.surface_level + foam_elements.POROUS_MAX_EXTRA_LEVELS
    assert (layers >= foam_elements.POROUS_CELLS_ACROSS or capped) and abs(thickness - 0.05) <= thickness/layers
    # A radiator the refinement cap leaves under-resolved is reported, not silently trusted.
    assert capped == any('cell layers across' in w for w in report['warnings'])
    assert f'levels ((1e15 {level}))' in (case/'system/snappyHexMeshDict').read_text()
    fit = foam_elements.porous_fit(((5, 330), (7, 556)), spec.fluid.dynamic_viscosity, spec.fluid.density, thickness)
    assert 'explicitPorositySource' in options and f"d ({fit['d']:.12g} -10 -10)" in options
    assert f'{layers} cell layers' in options
    toposet = (case/'system/topoSetDict').read_text()
    assert toposet.count('rotatedBoxToCell') == 3
    control = (case/'system/controlDict').read_text()
    assert 'porosity yes' in control and 'zone_rad_up' in control
    assert foam_elements.zone_list(spec) == ['porous_rad', 'porous_rad_up', 'porous_rad_down']
    origin, i, j, k = foam_elements.porous_box(spec.elements.porous[0])
    np.testing.assert_allclose(origin + (i + j + k)/2, (-1.2, 0, 0), atol=1e-12)
    assert abs(i@j) < 1e-12 and abs(j@k) < 1e-12 and abs(i@k) < 1e-12
    outside = spec.model_copy(deep=True); outside.elements.porous[0].center = (500, 0, 0)
    assert any('inside the wind tunnel' in e for e in validation.validate(outside)['errors'])


def test_openings_become_patches_with_flow_conditions(tmp_path):
    g = geometry.add_primitive(Primitive(shape='box', name='body', dimensions=(1, 1, 1)))
    names = [p['name'] for p in g['patches']]
    data = presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=names, speed=10))['spec']
    data['use_case']['confirmed'] = True; data['use_case']['references_confirmed'] = True
    spec = SimulationSpec.model_validate(data)
    grille, exhaust = names[0], names[1]
    spec.use_case.force_patches = [n for n in names if n not in (grille, exhaust)]
    spec.elements = Elements(openings=[Opening(patch=grille, kind='outflow_flow_rate', value=0.05), Opening(patch=exhaust, kind='inflow_velocity', value=2.0)])
    report = validation.validate(spec)
    assert report['valid'], report['errors']
    case = foam.compile_case(spec, tmp_path/'case')
    snappy = (case/'system/snappyHexMeshDict').read_text()
    assert f'{grille} {{ level' in snappy and f'{grille} {{ level ' in snappy
    assert f'{grille} {{ nSurfaceLayers' not in snappy, 'openings are not walls'
    u = (case/'0/U').read_text()
    assert 'flowRateOutletVelocity' in u
    exhaust_line = next(line for line in u.splitlines() if line.startswith(exhaust))
    velocity = np.array([float(v) for v in exhaust_line.split('uniform (')[1].split(')')[0].split()])
    assert np.linalg.norm(velocity) == pytest.approx(2.0)
    mesh, groups = geometry.load(g['id'])
    group = next(p['group'] for p in g['patches'] if p['name'] == exhaust)
    outward = mesh.face_normals[groups == group].mean(axis=0)
    assert velocity @ outward > 0, 'external inflow leaves the body surface into the fluid'
    clash = spec.model_copy(deep=True); clash.use_case.force_patches = names
    assert any('force-measured' in e for e in validation.validate(clash)['errors'])
    # Results list the model's openings with their compiled kinds, not the wind-tunnel faces.
    from gustsim.elements import build
    fluxes = {grille: 0.05, exhaust: -0.01, 'inlet': -1.0, 'outlet': 1.0, names[2]: 0.0}
    listed = {o['patch']: o['kind'] for o in build(spec, {'boundary_flux_m3s': fluxes})['openings']}
    assert listed == {grille: 'flow_outlet', exhaust: 'velocity_inlet'}


def test_rotor_efficiency_is_reported_only_where_physical():
    from gustsim.elements import efficiency
    assert efficiency(10, 5, 100) == pytest.approx(0.5)
    assert efficiency(1.5e-5, 0.05, 1e-7) is None, 'T·V/P above 1 is not a propulsive efficiency'
    assert efficiency(-1, 5, 10) is None and efficiency(1, 5, 0) is None


def test_mesh_identity_follows_compiled_patch_types():
    spec = two_rotor_spec()
    base = workflow.mesh_identity(spec)
    faster = spec.model_copy(deep=True); faster.elements.rotating_zones[0].rpm = 2000
    assert workflow.mesh_identity(faster) == base, 'RPM changes physics, not the mesh'
    g = car()
    spec = ground_spec(g, 'static')
    moving = spec.model_copy(deep=True); moving.environment.ground = 'moving'
    assert workflow.mesh_identity(moving) == workflow.mesh_identity(spec), 'static and moving ground are both walls'
    slip = spec.model_copy(deep=True); slip.environment.ground = 'slip'
    assert workflow.mesh_identity(slip) != workflow.mesh_identity(spec), 'a slip floor has no layers'


def test_element_results_from_zone_tables(tmp_path):
    g = geometry.add_primitive(Primitive(shape='box', name='body', dimensions=(1, 1, 1)))
    data = presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=[p['name'] for p in g['patches']], speed=10))['spec']
    spec = SimulationSpec.model_validate(data)
    spec.elements = Elements(porous=[PorousZone(name='rad', center=(-1.2, 0, 0), width_m=0.5, height_m=0.4, thickness_m=0.05, curve=((5, 330), (7, 556)))])
    for suffix, p in (('', 0.0), ('_up', 300.0/spec.fluid.density), ('_down', 0.0)):
        folder = tmp_path/f'postProcessing/zone_rad{suffix}/0'; folder.mkdir(parents=True)
        (folder/'volFieldValue.dat').write_text('# Time volAverage(U) volAverage(p)\n' + ''.join(f'{i} (4 0 0) {p}\n' for i in range(1, 101)))
    from gustsim.elements import build
    result = build(spec, {}, tmp_path)
    radiator = result['porous'][0]
    assert radiator['mean_velocity_m_s'] == pytest.approx(4) and radiator['pressure_drop_pa'] == pytest.approx(300)
    assert radiator['volume_flow_m3_s'] == pytest.approx(4*0.5*0.4)
    fit = foam_elements.porous_fit(((5, 330), (7, 556)), spec.fluid.dynamic_viscosity, spec.fluid.density, 0.05)
    assert radiator['curve_pressure_drop_pa'] == pytest.approx(fit['a']*4 + fit['b']*16)
