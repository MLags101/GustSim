"""Adaptive refinement, free surface and sliding-mesh compilation. No OpenFOAM run."""
from gustsim import amr, config, db, foam, foam_multiphase, geometry, pipeline, presets, validation
from gustsim.models import FreeSurface, PresetRequest, Primitive, SimulationSpec


def aero():
    g = geometry.add_primitive(Primitive(shape='sphere', radius=.1))
    names = [p['name'] for p in g['patches']]
    data = presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=names, speed=5, tier='basic'))['spec']
    data['use_case']['confirmed'] = True
    data['use_case']['references_confirmed'] = True
    return g, SimulationSpec.model_validate(data)


def test_indicator_mask_and_coefficient_change():
    assert amr.mask([10, 0.1], [0.1, 0.1], 1.0) == [True, False]
    assert abs(amr.coefficient_change(0.40, 0.42) - 0.05) < 1e-12
    assert amr.coefficient_change(0, 0.4) is None


def test_advanced_tier_requests_one_adaptive_cycle():
    _, spec = aero()
    resolved = __import__('gustsim.mesh_tiers', fromlist=['resolve']).resolve(spec, 'advanced')
    assert resolved.mesh.adaptive_cycles == 1
    assert validation.validate(resolved)['valid']


def test_completed_solve_queues_an_indicator_then_a_refined_mesh():
    _, spec = aero()
    spec.mesh.adaptive_cycles = 1
    sim = pipeline.create(spec, queue_mesh=False)
    mesh = db.enqueue(spec.model_dump(), 'mesh', simulation_id=sim['id'])
    db.update(mesh['id'], status='completed', stage='completed', result={'reviewed_configuration_id': 'x', 'reviewed_mesh_identity': 'y'})
    solve = db.enqueue(spec.model_dump(), 'solve', parent_id=mesh['id'], simulation_id=sim['id'])
    db.update(solve['id'], status='completed', stage='completed', result={'metrics': {'cd': 0.4}})
    pipeline.advance(sim['id'])
    runs = db.runs(simulation_id=sim['id'], limit=20)
    indicators = [run for run in runs if run['kind'] == 'amr_indicator']
    assert len(indicators) == 1 and indicators[0]['parent_id'] == solve['id']
    stl = config.DATA / 'runs' / solve['id'] / 'results' / 'amr' / 'indicator.stl'
    amr.write_stl(stl, [((0, 0, 0), (0.1, 0, 0), (0, 0.1, 0))])
    distance, level = amr.distance_and_level(spec)
    db.update(indicators[0]['id'], status='completed', stage='completed',
              result={'sha256': amr.file_sha256(stl), 'distance_m': distance, 'level': level, 'triangles': 1})
    pipeline.advance(sim['id'])
    refined = [run for run in db.runs(simulation_id=sim['id'], limit=20) if run['kind'] == 'mesh' and (run['spec'].get('mesh') or {}).get('adaptive_source')]
    assert len(refined) == 1
    case = config.DATA / 'amr-case'
    foam.compile_case(SimulationSpec.model_validate(refined[0]['spec']), case)
    text = (case / 'system' / 'snappyHexMeshDict').read_text(encoding='utf-8')
    assert 'amrIndicator' in text and 'mode distance' in text
    pipeline.advance(sim['id'])
    assert len([run for run in db.runs(simulation_id=sim['id'], limit=20) if run['kind'] == 'amr_indicator']) == 1


def test_free_surface_compiles_interfoam_and_rejects_a_rotor():
    _, spec = aero()
    spec.free_surface = FreeSurface(waterline_z=0)
    report = validation.validate(spec)
    assert report['valid'], report['errors']
    assert 'free_surface' in report['experimental_features']
    case = config.DATA / 'free-case'
    foam.compile_case(spec, case)
    assert 'application interFoam;' in (case / 'system' / 'controlDict').read_text(encoding='utf-8')
    assert 'localEuler' in (case / 'system' / 'fvSchemes').read_text(encoding='utf-8')
    assert (case / '0' / 'alpha.water').is_file() and (case / 'system' / 'setFieldsDict').is_file()
    assert foam_multiphase.wetted_area([0.2, 0.8, float('nan')], [1, 2, 3]) == 2
    assert foam_multiphase.resistance_coefficient(10, 2, 1, 1000) == 10 / (0.5 * 1000 * 4)
    spec.rotation.enabled = True
    spec.mode = 'rotor'
    spec.rotation.patches = [spec.boundaries[0].patch]
    spec.rotation.radius, spec.rotation.length, spec.rotation.blade_radius, spec.rotation.rpm = 0.2, 0.2, 0.11, 10
    assert any('rotating region' in error for error in validation.validate(spec)['errors'])


def test_sliding_mesh_compiles_an_ami_pair_without_mrf():
    g = geometry.add_primitive(Primitive(shape='sphere', radius=.1))
    names = [p['name'] for p in g['patches']]
    data = presets.generate(PresetRequest(geometry_id=g['id'], kind='propeller', patches=names, speed=5, rpm=-200))['spec']
    data['use_case']['confirmed'] = True
    data['use_case']['references_confirmed'] = True
    spec = SimulationSpec.model_validate(data)
    spec.time.mode = 'transient'
    spec.time.sliding_mesh = True
    spec.time.flow_throughs = 0.5
    report = validation.validate(spec)
    assert report['valid'], report['errors']
    assert 'ami' in report['experimental_features']
    case = config.DATA / 'ami-case'
    foam.compile_case(spec, case)
    baffles = (case / 'system' / 'createBafflesDict').read_text(encoding='utf-8')
    assert 'cyclicAMI' in baffles and 'amiInner' in baffles
    assert 'pimpleFoam' in (case / 'system' / 'controlDict').read_text(encoding='utf-8')
    assert 'rotatingMotion' in (case / 'constant' / 'dynamicMeshDict').read_text(encoding='utf-8')
    assert not (case / 'constant' / 'MRFProperties').exists()
