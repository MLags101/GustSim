"""Reader-safe case view for the pinned image's assert-enabled vtkOpenFOAMReader."""
import re
import numpy as np
import foamcase
from gustsim import foamread


def test_only_leading_negative_integers_below_minus_one_are_rewritten():
    assert foamread.safe_bytes(b'value uniform (-20 0 -2.449293598e-15);') == b'value uniform (-20.0 0 -2.449293598e-15);'
    assert foamread.safe_bytes(b'uniform ( -12 0 1e-3 )') == b'uniform ( -12.0 0 1e-3 )'
    for unchanged in (b'uniform (-1 0 -2e-5)', b'uniform (-20.5 0 0)', b'uniform (20 0 1e-15)', b'uniform -20;',
                      b'uniform (-2e-5 0 0)', b'nonuniform List<vector> 3((-20 0 0))'):
        assert foamread.safe_bytes(unchanged) == unchanged


def test_shadow_case_patches_only_risky_fields_and_reads_back(tmp_path):
    case = foamcase.build(tmp_path/'case', cells=(16, 8, 8), speed=20.0)
    u = case/'100/U'
    original = u.read_text()
    patch = re.search(r'\n    (\w+)\n    \{\n        type calculated;', original).group(1)
    risky = re.sub(r'(\n    ' + patch + r'\n    \{\n).*?(\n    \})',
                   r'\1        type freestream;\n        freestreamValue uniform (-20 0 -2.449293598e-15);\n'
                   r'        value uniform (-20 0 -2.449293598e-15);\2', original, count=1, flags=re.S)
    u.write_text(risky)
    assert foamread.foam_file(case, times=['0']) == case/'gustsim.foam', 'nothing risky in that time'
    foam = foamread.foam_file(case)
    assert foam != case/'gustsim.foam' and foam.exists()
    assert u.read_text() == risky, 'the solved case is never modified'
    shadow = foam.parent
    assert b'uniform (-20.0 0 -2.449293598e-15)' in (shadow/'100/U').read_bytes()
    assert (shadow/'100/p').read_bytes() == (case/'100/p').read_bytes() and (shadow/'constant/polyMesh/owner').exists()
    import vtk
    from vtk.util.numpy_support import vtk_to_numpy
    reader = vtk.vtkOpenFOAMReader(); reader.SetFileName(str(foam)); reader.UpdateInformation()
    for i in range(reader.GetNumberOfPatchArrays()):
        reader.SetPatchArrayStatus(reader.GetPatchArrayName(i), 1)
    reader.UpdateTimeStep(100)
    it = reader.GetOutput().NewIterator(); it.InitTraversal()
    found = None
    while not it.IsDoneWithTraversal():
        if it.GetCurrentMetaData().Get(vtk.vtkCompositeDataSet.NAME()) == patch:
            found = vtk_to_numpy(it.GetCurrentDataObject().GetCellData().GetArray('U'))
        it.GoToNextItem()
    assert found is not None and np.allclose(found, [-20, 0, 0], atol=1e-9)
    foamread.cleanup(case)
    assert not shadow.exists() and u.exists() and (case/'constant/polyMesh/owner').exists()
