import io
import tarfile
import numpy as np
import pytest
from gustsim import backup,config
from gustsim.benchmarks import pipe_profile,pipe_pressure_drop,couette_profile,evaluate

def test_pipe_known_values_and_failure():
    assert np.allclose(pipe_profile([0,.5,1],1,2),[4,3,0])
    assert pipe_pressure_drop(1,2,1,3)==48
    data={'benchmark':'pipe','radius_m':[0,1],'pipe_radius_m':1,'mean_velocity_m_s':2,'axial_velocity_m_s':[4,0],'dynamic_viscosity_pa_s':1,'measurement_length_m':2,'pressure_drop_pa':32}
    assert evaluate(data)['passed']
    data['pressure_drop_pa']=40
    assert not evaluate(data)['passed']

def test_couette_no_slip_endpoints():
    assert np.allclose(couette_profile([1,2],1,2,10,3),[10,6])

def test_backup_roundtrip_and_nonempty_restore(tmp_path,monkeypatch):
    (config.DATA/'geometry/test.txt').write_text('immutable model')
    archive=backup.create(tmp_path/'project.tar.gz')
    destination=tmp_path/'restored';monkeypatch.setattr(config,'DATA',destination)
    backup.restore(archive)
    assert (destination/'geometry/test.txt').read_text()=='immutable model'
    with pytest.raises(ValueError):backup.restore(archive)

def test_backup_rejects_path_traversal(tmp_path,monkeypatch):
    archive=tmp_path/'bad.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        item=tarfile.TarInfo('../outside');item.size=3;tar.addfile(item,io.BytesIO(b'bad'))
    monkeypatch.setattr(config,'DATA',tmp_path/'empty')
    with pytest.raises(ValueError):backup.restore(archive)
    assert not (tmp_path/'outside').exists()
