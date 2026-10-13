"""Demo models: watertight geometry and a ready setup that validates, built as the setup page does."""
import pytest
from gustsim import presets, rotors, samples, validation, wheels
from gustsim.models import Elements, PresetRequest, SimulationSpec, TimeControl


def spec_for(key):
    g = samples.create(key)
    setup = g['sample_setup']
    intent = setup.get('intent', {})
    names = [p['name'] for p in g['patches']]
    kind = intent.get('kind', 'aerodynamics')
    request = {'geometry_id': g['id'], 'kind': kind, 'speed': intent.get('speed', 20), 'direction': (-1, 0, 0), 'lift_axis': (0, 0, 1),
               'patches': intent.get('patches', names), 'ground': intent.get('ground', 'none'), 'ride_height_m': intent.get('ride_height_m', 0),
               'tier': 'basic', 'driving': intent.get('driving', 'speed'), 'inlet': intent.get('inlet', ''), 'outlet': intent.get('outlet', '')}
    if kind == 'propeller':
        found = rotors.detect(g['id'], request['patches'])
        # As the setup's rotor card does: the axis follows the air the blades push.
        axis, rpm = rotors.orient(found['axis'], intent['rpm'], found['handedness'])
        request.update(origin=found['origin'], axis=axis, rpm=rpm)
    data = presets.generate(PresetRequest(**request))['spec']
    data['use_case']['confirmed'] = True; data['use_case']['references_confirmed'] = True
    spec = SimulationSpec.model_validate(data)
    overrides = setup.get('overrides', {})
    if overrides.get('reference_area'):
        spec.references.area = overrides['reference_area']
    if overrides.get('reference_length'):
        spec.references.length = overrides['reference_length']
    if 'elements' in overrides:
        spec.elements = Elements.model_validate(overrides['elements'])
    if 'time' in overrides:
        spec.time = TimeControl.model_validate(overrides['time'])
    return g, spec


@pytest.mark.parametrize('key', samples.ORDER)
def test_every_demo_is_watertight_and_its_setup_validates(key):
    g, spec = spec_for(key)
    d = g['diagnostics']
    assert d['watertight'] and d['winding_consistent'], key
    report = validation.validate(spec)
    assert report['valid'], (key, report['errors'])


def test_demo_specifics():
    g, car = spec_for('car')
    assert car.environment.ground == 'moving' and len(car.elements.wheels) == 4
    found = wheels.detect(g['id'])
    assert len(found['candidates']) == 4, 'the car demo wheels are detectable as wheels'
    g, prop = spec_for('propeller')
    # The demo rotor pushes air down (-Z): that is the axis and the climb inflow, so thrust (+Z) is positive.
    assert prop.rotation.enabled and list(prop.rotation.axis) == [0.0, 0.0, -1.0] and prop.flow.speed == 8
    assert list(prop.references.thrust_axis) == [0.0, 0.0, 1.0] and prop.rotation.rpm == -6000
    g, wing = spec_for('wing')
    assert wing.references.area == 0.25 and wing.references.length == 0.25, 'wing coefficients use the planform'
    g, tower = spec_for('tower')
    assert tower.time.mode == 'transient' and tower.environment.ground == 'static'
    g, duct = spec_for('radiator')
    assert duct.mode == 'internal' and duct.elements.porous[0].name == 'radiator'
    assert [s['id'] for s in samples.listing()[:7]] == list(samples.ORDER)


@pytest.mark.parametrize('key', samples.ORDER)
def test_point_inside_the_fluid_is_off_the_background_grid(key):
    """snappyHexMesh rejects a locationInMesh on a cell face, edge or vertex. The original sphere demo's
    preset point fell exactly on a background-grid vertex (a round number of model lengths)."""
    import numpy as np
    from gustsim import mesh_tiers
    g, spec = spec_for(key)
    spec = mesh_tiers.apply(spec)
    low, high = np.asarray(spec.domain.minimum), np.asarray(spec.domain.maximum)
    counts = np.maximum(4, np.ceil(spec.mesh.base_cells*(high - low)/max(high - low))).astype(int)
    cells = (np.asarray(spec.domain.fluid_point) - low)/((high - low)/counts)
    for level in range(9):
        scaled = cells*2**level
        assert np.all(np.abs(scaled - np.round(scaled)) > 1e-6), (key, level, spec.domain.fluid_point)
    assert validation.validate(spec)['valid']
