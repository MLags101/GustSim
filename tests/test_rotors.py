"""Rotor axis detection from the selected rotating surfaces."""
import numpy as np
import pytest
import trimesh
from gustsim import geometry, rotors


def propeller(blades, axis=(0, 1, 0), centre=(0.3, -0.1, 0.2), radius=0.25, pitch_deg=25):
    """Twisted flat blades around +Y, then rotated to `axis` and moved to `centre`."""
    parts = []
    for b in range(blades):
        blade = trimesh.creation.box(extents=(radius*0.85, 0.012, 0.05))
        blade.apply_translation((radius*0.5, 0, 0))
        blade.apply_transform(trimesh.transformations.rotation_matrix(np.radians(pitch_deg), (1, 0, 0)))
        blade.apply_transform(trimesh.transformations.rotation_matrix(2*np.pi*b/blades, (0, 1, 0)))
        parts.append(blade)
    hub = trimesh.creation.cylinder(radius=0.03, height=0.06, sections=24)
    hub.apply_transform(trimesh.transformations.rotation_matrix(np.pi/2, (1, 0, 0)))
    parts.append(hub)
    mesh = trimesh.util.concatenate(parts)
    target = np.asarray(axis, float)/np.linalg.norm(axis)
    mesh.apply_transform(trimesh.geometry.align_vectors((0, 1, 0), target))
    mesh.apply_translation(centre)
    g = geometry.save(mesh, np.zeros(len(mesh.faces), dtype=int), {0: 'rotor'}, 'rotor.stl', confirmed=True)
    return g


@pytest.mark.parametrize('blades,axis', [(2, (0, 1, 0)), (3, (0, 1, 0)), (4, (1, 0, 0)), (3, (0.6, 0.8, 0))])
def test_detects_the_spin_axis_and_centre(blades, axis):
    g = propeller(blades, axis)
    found = rotors.detect(g['id'], ['rotor'])
    expected = np.asarray(axis, float)/np.linalg.norm(axis)
    assert abs(np.dot(found['axis'], expected)) > 0.995, (found['axis'], found['reasons'])
    assert np.allclose(found['origin'], (0.3, -0.1, 0.2), atol=0.02)
    assert found['blades'] == blades and found['confidence'] >= 0.7
    assert 0.2 < found['blade_radius'] < 0.3 and found['radius'] > found['blade_radius']
    if axis in ((0, 1, 0), (1, 0, 0)):
        assert found['axis'] == pytest.approx(list(expected)), 'coordinate axes are snapped exactly'


def test_rejects_unknown_surfaces():
    g = propeller(3)
    with pytest.raises(ValueError):
        rotors.detect(g['id'], ['nope'])


@pytest.mark.parametrize('axis', [(0, 1, 0), (1, 0, 0), (0.6, 0.8, 0)])
def test_blade_handedness_is_found_and_mirrored_blades_reverse_it(axis):
    """The helix sense comes from the blade twist alone: mirror-image blades (pitch -25 deg)
    must report the opposite sense, whatever the axis."""
    right = rotors.detect(propeller(3, axis, pitch_deg=25)['id'], ['rotor'])
    left = rotors.detect(propeller(3, axis, pitch_deg=-25)['id'], ['rotor'])
    assert right['handedness'] in (1, -1) and left['handedness'] == -right['handedness']
    flat = rotors.detect(propeller(3, axis, pitch_deg=0)['id'], ['rotor'])
    assert flat['handedness'] == 0, 'untwisted blades push no air either way'


def test_orient_points_the_axis_along_the_pushed_air_and_keeps_the_spin():
    axis, rpm = rotors.orient((0, 0, 1), 6000, 1)
    assert axis == [0, 0, -1] and rpm == -6000, 'same physical spin, axis now downstream'
    assert rotors.orient((0, 0, 1), -6000, 1) == ([0, 0, 1], -6000)
    assert rotors.orient((0, 0, 1), 6000, 0) == ([0, 0, 1], 6000), 'unknown sense: unchanged'
