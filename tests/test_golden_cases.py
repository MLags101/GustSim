"""Compiled-case goldens.

The v1 case compiler output is a contract: refactors (spec v2, function-object builders,
element support) must reproduce it byte for byte. A deliberate numerical change regenerates
the goldens with GUSTSIM_UPDATE_GOLDENS=1 and the diff is reviewed like any other change.
"""
import os
import re
from pathlib import Path
import pytest
from gustsim import geometry, presets, foam
from gustsim.models import Primitive, PresetRequest, SimulationSpec

GOLDEN = Path(__file__).with_name('golden')
COMPARED = ('system', 'constant', '0')
SKIPPED = ('constant/triSurface', 'constant/polyMesh')


def pipe_caps(g):
    mesh, groups = geometry.load(g['id'])
    caps = sorted([(mesh.triangles_center[groups == p['group']].mean(axis=0)[0], p['name']) for p in g['patches']])
    return caps[0][1], caps[-1][1]


def external_sst():
    g = geometry.add_primitive(Primitive(shape='sphere', radius=.1))
    names = [p['name'] for p in g['patches']]
    return presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=names, speed=20))['spec']


def internal_laminar():
    g = geometry.add_primitive(Primitive(shape='cylinder', radius=.1, length=.4, role='enclosure'))
    inlet, outlet = pipe_caps(g)
    return presets.generate(PresetRequest(geometry_id=g['id'], kind='pipe', inlet=inlet, outlet=outlet,
                                          driving='flow', turbulence='laminar'))['spec']


def rotor_mrf():
    g = geometry.add_primitive(Primitive(shape='sphere', radius=.1))
    names = [p['name'] for p in g['patches']]
    return presets.generate(PresetRequest(geometry_id=g['id'], kind='propeller', patches=names, speed=5, rpm=-300))['spec']


def static_rotor():
    g = geometry.add_primitive(Primitive(shape='sphere', radius=.1))
    names = [p['name'] for p in g['patches']]
    return presets.generate(PresetRequest(geometry_id=g['id'], kind='propeller', patches=names, speed=0, rpm=60,
                                          axis=(0, 0, 1)))['spec']


def external_v2_regular():
    g = geometry.add_primitive(Primitive(shape='sphere', radius=.1))
    names = [p['name'] for p in g['patches']]
    return presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=names, speed=20, tier='regular'))['spec']


def as_v1(build):
    def v1():
        data = build()
        data['schema_version'] = 1
        return data
    return v1


# v1 cases pin the original compiler output; v2 cases pin the guided-setup defaults.
CASES = {'external_sst': as_v1(external_sst), 'internal_laminar': as_v1(internal_laminar),
         'rotor_mrf': as_v1(rotor_mrf), 'static_rotor': as_v1(static_rotor),
         'external_v2_regular': external_v2_regular, 'rotor_v2': rotor_mrf, 'internal_v2': internal_laminar}


def compiled_files(root):
    files = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if not path.is_file() or not relative.startswith(COMPARED) or relative.startswith(SKIPPED):
            continue
        files[relative] = path.read_text(encoding='utf-8')
    return files


def normalized(text):
    # Run directories, not configuration, differ between machines.
    return re.sub(r'"[^"]*gustsim\.stop"', '"<case>/gustsim.stop"', text)


@pytest.mark.parametrize('name', sorted(CASES))
def test_compiled_case_matches_golden(name, tmp_path):
    data = CASES[name]()
    data['use_case']['confirmed'] = True
    data['use_case']['references_confirmed'] = True
    spec = SimulationSpec.model_validate(data)
    files = {k: normalized(v) for k, v in compiled_files(foam.compile_case(spec, tmp_path / 'case')).items()}
    target = GOLDEN / name
    if os.environ.get('GUSTSIM_UPDATE_GOLDENS') == '1':
        for old in target.rglob('*') if target.exists() else []:
            if old.is_file(): old.unlink()
        for relative, text in files.items():
            out = target / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding='utf-8', newline='\n')
    expected = {p.relative_to(target).as_posix(): p.read_text(encoding='utf-8') for p in sorted(target.rglob('*')) if p.is_file()}
    assert sorted(files) == sorted(expected)
    for relative in files:
        assert files[relative] == expected[relative], f'{name}/{relative} differs from the reviewed golden'
