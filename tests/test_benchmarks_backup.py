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

def test_new_gate_evaluators_pass_on_exact_data_and_fail_on_bias():
    from gustsim.benchmarks import blasius_cf,two_point_curve
    a,b=two_point_curve(((.5,1.),(1.,3.)))
    assert np.allclose((a,b),(1,2))
    duct={'benchmark':'porous_duct','curve':[[.5,1],[1,3]],'points':[{'mean_velocity_m_s':v,'pressure_drop_pa':a*v+b*v*v} for v in (.6,.9)]}
    assert evaluate(duct)['status']=='passed'
    duct['points'][0]['pressure_drop_pa']*=1.05
    assert evaluate(duct)['status']=='failed'
    zones=[{'name':n,'radius_m':[1,1.5,2],'inner_radius_m':1,'outer_radius_m':2,'inner_omega_rad_s':w,
            'tangential_velocity_m_s':couette_profile([1,1.5,2],1,2,w).tolist()} for n,w in (('inner',10),('outer',-4))]
    assert evaluate({'benchmark':'multi_zone_couette','zones':zones})['passed']
    stop={'benchmark':'early_stop_consistency','early_stop':{'cd':.402},'full_budget':{'cd':.400}}
    assert evaluate(stop)['passed'] and evaluate({**stop,'early_stop':{'cd':.41}})['status']=='failed'
    x=[.1,.2,.4];plate={'benchmark':'flat_plate_cf','x_m':x,'velocity_m_s':1,'kinematic_viscosity_m2_s':1.5e-5,'cf':blasius_cf(x,1,1.5e-5).tolist()}
    assert evaluate(plate)['passed']

def test_reference_gates_require_provenance_and_write_status_capabilities_reads(tmp_path,monkeypatch):
    from gustsim import capabilities
    data={'benchmark':'reference','gate':'cylinder_re100','metrics':{'St':{'measured':.166,'reference':.164,'tolerance':.03},'Cd':{'measured':1.36,'reference':1.33,'tolerance':.05}}}
    with pytest.raises(ValueError):evaluate(data)
    data['provenance']={'source':'cited dataset'}
    result=evaluate(data)
    assert result['gate']=='cylinder_re100' and result['status']=='passed' and result['tolerances']=={'St':.03,'Cd':.05}
    monkeypatch.setattr(capabilities,'RESULTS',tmp_path)
    assert capabilities.gate_status('cylinder_re100')=='not_executed'
    (tmp_path/'cylinder_re100.json').write_text(__import__('json').dumps(result))
    assert capabilities.gate_status('cylinder_re100')=='passed'
