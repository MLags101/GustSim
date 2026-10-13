"""Regression tests for the backend review findings: sliding mesh, porous loads, free surface,
adaptive indicator, experimental opt-in, wheels, monitor signals, averaging windows, retries.
Compilation and bookkeeping only; the OpenFOAM behaviour is covered by the Linux smokes."""
import json
import math
import re
import numpy as np
import pytest
from gustsim import config, db, foam, foam_elements, forces, geometry, manifest, presets, quality, validation, worker
from gustsim.models import Elements, FreeSurface, PorousZone, PresetRequest, Primitive, SimulationSpec, Wheel


def confirmed(data):
    data['use_case']['confirmed'] = True
    data['use_case']['references_confirmed'] = True
    return SimulationSpec.model_validate(data)


def aero(speed=10, **extra):
    g = geometry.add_primitive(Primitive(shape='box', name='body', dimensions=(1, 1, 1)))
    names = [p['name'] for p in g['patches']]
    return g, confirmed(presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=names, speed=speed, **extra))['spec'])


def sliding():
    g = geometry.add_primitive(Primitive(shape='sphere', radius=.1))
    names = [p['name'] for p in g['patches']]
    spec = confirmed(presets.generate(PresetRequest(geometry_id=g['id'], kind='propeller', patches=names, speed=5, rpm=-200))['spec'])
    spec.time.mode = 'transient'
    spec.time.sliding_mesh = True
    spec.time.flow_throughs = 0.5
    spec.solver.processes = 1  # serial: the command list has no decomposition stages
    return g, spec


def lines(path, prefix):
    return [line for line in path.read_text(encoding='utf-8').splitlines() if line.startswith(prefix)]


# ---- 1, 2, 9: sliding mesh ---------------------------------------------------------------------
def test_sliding_mesh_moves_the_rotor_walls_and_snaps_a_cylindrical_interface(tmp_path):
    g, spec = sliding()
    assert validation.validate(spec)['valid']
    case = foam.compile_case(spec, tmp_path/'case')
    for patch in spec.rotation.patches:
        assert lines(case/'0/U', patch + ' {') == [f'{patch} {{ type movingWallVelocity; value uniform (0 0 0); }}']
    snappy = (case/'system/snappyHexMeshDict').read_text()
    assert 'rotorRegion { level' in snappy and 'faceZone rotorInterface; cellZone rotor; cellZoneInside inside;' in snappy
    # snappyHexMesh makes the zones; a topoSet cell selection would overwrite them with a staircase.
    assert not (case/'system/topoSetDict').exists()
    assert 'zoneName rotorInterface;' in (case/'system/createBafflesDict').read_text()
    assert '"pcorr.*"' in (case/'system/fvSolution').read_text()
    control = (case/'system/controlDict').read_text()
    assert 'application pimpleFoam;' in control and 'writeControl adjustableRunTime;' in control
    assert 'writeControl timeStep; writeInterval 100;' not in control and 'adjustTimeStep yes;' in control
    plan = json.loads((case/'results/transient_plan.json').read_text())
    assert f'endTime {plan["end"]:.12g};' in control


class Recorder:
    def __init__(self, on_run=None):
        self.commands, self.on_run = [], on_run

    def run(self, identifier, command, cwd, stage, timeout, monitor=None):
        self.commands.append((stage, command))
        if self.on_run:
            self.on_run(stage, monitor)


def test_sliding_solve_splits_the_baffles_before_pimplefoam(tmp_path):
    g, spec = sliding()
    root = tmp_path/'case'
    foam.compile_case(spec, root)
    executor = Recorder()
    worker.solve('x', executor, root, spec, lambda: 60)
    stages = [stage for stage, _ in executor.commands]
    assert stages[:3] == ['baffles', 'split_baffles', 'sliding']
    assert executor.commands[1][1] == ['mergeOrSplitBaffles', '-split', '-overwrite']


# ---- 3, 11: porous only in the total; parts follow the load selection ---------------------------
def test_porous_force_is_only_in_the_total_and_parts_follow_the_measured_surfaces(tmp_path):
    g = geometry.add_primitive(Primitive(shape='box', name='body', dimensions=(1, 1, 1)))
    g = geometry.add_primitive(Primitive(shape='box', name='mast', center=(0, 0, 1), dimensions=(0.2, 0.2, 1)), g['id'])
    body = [p for part in g['parts'] if part['name'].startswith('body') for p in part['patches']]
    names = [p['name'] for p in g['patches']]
    spec = confirmed(presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=body, speed=10))['spec'])
    assert set(spec.use_case.force_patches) == set(body) and set(body) < set(names)
    spec.elements = Elements(porous=[PorousZone(name='rad', center=(-1.2, 0, 0), axis=(1, 0, 0), width_m=0.6, height_m=0.4,
                                                thickness_m=0.05, curve=((5, 330), (7, 556)))])
    case = foam.compile_case(spec, tmp_path/'case')
    control = (case/'system/controlDict').read_text()
    assert control.count('porosity yes') == 1
    assert re.search(r'loads \{[^}]*porosity yes', control)
    groups = json.loads((case/'force_groups.json').read_text())
    assert groups and all(set(group['patches']) <= set(body) for group in groups)
    assert not any(set(group['patches']) & (set(names) - set(body)) for group in groups)


def write_forces(case, name, total, porous=(0, 0, 0), rows=50):
    folder = case/'postProcessing'/name/'0'
    folder.mkdir(parents=True)
    vec = lambda v: ' '.join(f'{x:.9g}' for x in v)
    with (folder/'force.dat').open('w') as f, (folder/'moment.dat').open('w') as m:
        for i in range(1, rows + 1):
            wall = np.asarray(total, float) - np.asarray(porous, float)
            f.write(f'{i} ({vec(total)}) ({vec(wall)}) (0 0 0) ({vec(porous)})\n')
            m.write(f'{i} (0 0 0) (0 0 0) (0 0 0) (0 0 0)\n')


def test_part_sum_check_excludes_the_porous_body_force(tmp_path):
    g, spec = aero()
    drag = np.asarray(spec.references.drag_axis, float)
    write_forces(tmp_path, 'loads', 13*drag, porous=3*drag)
    write_forces(tmp_path, 'group_part_0', 6*drag)
    write_forces(tmp_path, 'group_part_1', 4*drag)
    (tmp_path/'force_groups.json').write_text(json.dumps([{'name': 'part_0', 'patches': ['a']}, {'name': 'part_1', 'patches': ['b']}]))
    result = forces.breakdown(tmp_path, spec)
    assert result['parts_sum_check']['ok'] and result['parts_sum_check']['wall_drag_n'] == pytest.approx(10)
    assert sum(p['share_of_drag'] for p in result['parts']) == pytest.approx(1.0)


# ---- 4: free surface ------------------------------------------------------------------------------
def free_surface_case(tmp_path):
    g, spec = aero(speed=1, fluid='water')
    spec.free_surface = FreeSurface(waterline_z=0)
    assert validation.validate(spec)['valid'], validation.validate(spec)['errors']
    return spec, foam.compile_case(spec, tmp_path/'free')


def test_free_surface_dictionaries_follow_interfoam(tmp_path):
    spec, case = free_surface_case(tmp_path)
    assert 'dimensions [1 -1 -2 0 0 0 0];' in (case/'0/p_rgh').read_text()
    assert 'dimensions [1 -1 -2 0 0 0 0];' in (case/'0/p').read_text()
    assert lines(case/'0/p_rgh', 'top {') == ['top { type totalPressure; p0 uniform 0; value uniform 0; }']
    assert lines(case/'0/U', 'top {') == ['top { type pressureInletOutletVelocity; value uniform (0 0 0); }']
    solution = (case/'system/fvSolution').read_text()
    for entry in ('PIMPLE {', 'p_rghFinal', '"(U|k|omega).*"', '"alpha.water.*"', '"pcorr.*"', 'pRefPoint'):
        assert entry in solution, entry
    assert 'SIMPLE' not in solution
    schemes = (case/'system/fvSchemes').read_text()
    for entry in ('localEuler', 'div(rhoPhi,U)', 'div(phi,alpha)', 'div(phirb,alpha)', 'div(((rho*nuEff)*dev2(T(grad(U)))))'):
        assert entry in schemes, entry
    control = (case/'system/controlDict').read_text()
    assert 'application interFoam;' in control and 'rho rho;' in control and 'rhoInf' not in control.split('loads {')[1].split('}')[0]
    assert re.search(r'residuals \{[^}]*fields \(p_rgh alpha.water', control)
    plan = manifest.plan(spec, db.geometry(spec.geometry_id))
    assert plan['pressure_is_kinematic'] is False and plan['rho'] == spec.free_surface.water.density
    assert manifest.plan(aero()[1], db.geometry(spec.geometry_id))['pressure_is_kinematic'] is True


def test_free_surface_boundary_pressure_is_not_multiplied_by_density(tmp_path):
    spec, case = free_surface_case(tmp_path)
    folder = case/'postProcessing/pressure_inlet/0'; folder.mkdir(parents=True)
    folder.joinpath('surfaceFieldValue.dat').write_text(''.join(f'{i} 1000\n' for i in range(1, 30)))
    assert quality.analyze(case, spec)['boundary_pressure_pa']['inlet'] == pytest.approx(1000)
    plain = spec.model_copy(deep=True); plain.free_surface = None
    assert quality.analyze(case, plain)['boundary_pressure_pa']['inlet'] == pytest.approx(1000*spec.fluid.density)


def test_half_model_symmetry_is_rejected(tmp_path):
    g, spec = aero(speed=1)
    spec.free_surface = FreeSurface(waterline_z=0, symmetry_y0=True)
    assert any('symmetry plane' in e for e in validation.validate(spec)['errors'])


# ---- 5: adaptive indicator --------------------------------------------------------------------------
def test_adaptive_indicator_uses_each_cells_own_size():
    from vtkmodules.util import numpy_support
    from vtkmodules.vtkCommonDataModel import vtkRectilinearGrid
    from vtkmodules.vtkFiltersCore import vtkAppendFilter
    from gustsim.amr_indicator import indicator_surface
    x = np.array([0, 1, 2, 2.5, 3.0, 3.25, 3.5])  # cell widths 1, 1, .5, .5, .25, .25
    grid = vtkRectilinearGrid()
    grid.SetDimensions(len(x), 2, 2)
    grid.SetXCoordinates(numpy_support.numpy_to_vtk(x))
    grid.SetYCoordinates(numpy_support.numpy_to_vtk(np.array([0.0, 0.25])))
    grid.SetZCoordinates(numpy_support.numpy_to_vtk(np.array([0.0, 0.25])))
    append = vtkAppendFilter(); append.SetInputData(grid); append.Update()
    volume = append.GetOutput()
    cells = volume.GetNumberOfCells()
    vorticity = numpy_support.numpy_to_vtk(np.tile([0.0, 0.0, 2.0], (cells, 1)), deep=1)
    vorticity.SetName('vorticity')
    volume.GetCellData().AddArray(vorticity)
    # Indicator 2·h runs from about 0.79 (coarse cells) to 0.50 (fine cells): 0.6 crosses it.
    surface, indicator = indicator_surface(volume, 1.0, 0.6)
    widths = np.diff(x)
    expected = 2.0*np.cbrt(widths*0.25*0.25)
    np.testing.assert_allclose(indicator, expected, rtol=1e-9)
    assert len(indicator) == cells and volume.GetCellData().GetArray('indicator') is not None
    assert surface.GetNumberOfCells() > 0


# ---- 6: experimental numerics ------------------------------------------------------------------------
def test_tiers_leave_experimental_numerics_off_and_opt_in_needs_acknowledgement():
    g, spec = aero(tier='basic')
    report = validation.validate(spec)
    assert not spec.convergence.enabled and spec.mesh.adaptive_cycles == 0
    assert report['physics_maturity'] == 'core' and not report['experimental_features']
    spec.convergence.enabled = True
    report = validation.validate(spec)
    assert 'early_stop' in report['experimental_features'] and report['physics_maturity'] == 'experimental'
    assert any(f['code'] == 'experimental_physics' and f['status'] == 'review' for f in report['findings'])


# ---- 7, 8: wheels -----------------------------------------------------------------------------------
def car(ground='moving', beta=0.0):
    g = geometry.add_primitive(Primitive(shape='box', name='body', center=(0, 0, 0.6), dimensions=(4, 1.6, 0.8)))
    g = geometry.add_primitive(Primitive(shape='cylinder', name='wheel', center=(1.3, 0.9, 0.33), radius=0.33, length=0.25, axis=(0, 1, 0)), g['id'])
    names = [p['name'] for p in g['patches']]
    direction = (-math.cos(math.radians(beta)), math.sin(math.radians(beta)), 0)
    spec = confirmed(presets.generate(PresetRequest(geometry_id=g['id'], kind='aerodynamics', patches=names, speed=30,
                                                    ground=ground, direction=direction))['spec'])
    wheel = [p for part in g['parts'] if part['name'] == 'wheel' for p in part['patches']]
    spec.elements = Elements(wheels=[Wheel(name='w', patches=wheel, center=(1.3, 0.9, 0.33), axis=(0, 1, 0), radius=0.33)])
    return spec


def test_wheel_spins_about_its_own_axle_and_rolls_with_the_belt_component():
    for beta in (0.0, 10.0):
        spec = car(beta=beta)
        axis, rpm = foam_elements.wheel_rotation(spec, spec.elements.wheels[0])
        np.testing.assert_allclose(np.abs(axis), (0, 1, 0), atol=1e-12)
        contact = np.cross(rpm*math.pi/30*axis, np.array([0, 0, -0.33]))
        velocity = np.asarray(spec.flow.velocity())
        rolling = np.array([1.0, 0, 0])
        np.testing.assert_allclose(contact, (velocity@rolling)*rolling, atol=1e-9)
        if beta == 0:
            np.testing.assert_allclose(contact, velocity, atol=1e-9)


def test_rotating_wheels_on_a_static_floor_are_rejected():
    spec = car(ground='static')
    assert any('moving ground' in e for e in validation.validate(spec)['errors'])
    spec.elements.wheels[0].rotating = False
    assert not any('moving ground' in e for e in validation.validate(spec)['errors'])


# ---- 10, 18: monitor ----------------------------------------------------------------------------------
def propeller():
    g = geometry.add_primitive(Primitive(shape='sphere', radius=.1))
    names = [p['name'] for p in g['patches']]
    spec = confirmed(presets.generate(PresetRequest(geometry_id=g['id'], kind='propeller', patches=names, speed=5, rpm=600))['spec'])
    spec.convergence.enabled = True
    return spec


def write_rotor_tables(case, spec, rows):
    axis = np.asarray(spec.rotation.axis, float)
    thrust = np.asarray(spec.references.thrust_axis, float)
    for name in ('loads', 'rotorLoads'):
        folder = case/'postProcessing'/name/'0'; folder.mkdir(parents=True, exist_ok=True)
        with (folder/'force.dat').open('a') as f, (folder/'moment.dat').open('a') as m:
            for i in rows:
                force = (2 + math.exp(-i/50))*thrust
                moment = -(0.5 + 0.1*math.exp(-i/50))*axis
                f.write(f'{i} ({force[0]} {force[1]} {force[2]}) (0 0 0) (0 0 0)\n')
                m.write(f'{i} ({moment[0]} {moment[1]} {moment[2]}) (0 0 0) (0 0 0)\n')


def test_propeller_monitor_tracks_thrust_and_torque(tmp_path):
    from gustsim.monitor import SolveMonitor, assess, history
    spec = propeller()
    job = db.enqueue(spec.model_dump(), 'solve')
    write_rotor_tables(tmp_path, spec, range(1, 41))
    monitor = SolveMonitor(job['id'], tmp_path, spec, publish_every=1e9)
    monitor()
    assert set(monitor.detector.values) == {'thrust_n', 'torque_nm'}
    assert len(monitor.detector.iterations) == 40
    assert monitor.detector.values['thrust_n'][-1] == pytest.approx(2 + math.exp(-40/50))
    forces_rows, _ = history(tmp_path, spec)
    assert 'thrust_n' in forces_rows[0] and 'torque_nm' in forces_rows[0]
    detector = assess(tmp_path, spec, quality.force_history(tmp_path))
    assert set(detector.values) == {'thrust_n', 'torque_nm'}


def test_robust_startup_stop_is_held_and_detection_restarts(tmp_path):
    from gustsim.monitor import SolveMonitor
    spec = propeller()
    job = db.enqueue(spec.model_dump(), 'solve')
    monitor = SolveMonitor(job['id'], tmp_path, spec, publish_every=1e9)
    monitor.hold = True
    monitor.detector.state = 'converged'
    assert monitor() is None and not monitor.stop_requested
    monitor.hold = False
    assert monitor() == 'stop'
    monitor.restart_detection()
    assert monitor.detector.state == 'searching' and not monitor.detector.iterations


def test_robust_profile_always_runs_the_second_order_stage(tmp_path):
    g, spec = aero()
    spec.solver.numerics = 'robust'
    spec.convergence.enabled = True
    spec.solver.processes = 1
    job = db.enqueue(spec.model_dump(), 'solve')
    root = config.DATA/'runs'/job['id']
    foam.compile_case(spec, root)

    def startup_converges(stage, monitor):
        if stage == 'solve_startup':
            monitor.detector.state = 'converged'
            assert monitor() is None, 'no stop request during the first-order startup'
    executor = Recorder(startup_converges)
    worker.solve(job['id'], executor, root, spec, lambda: 60)
    assert [stage for stage, _ in executor.commands] == ['solve_startup', 'solve']


# ---- 12, 14: averaged boundary tables and compiled boundary kinds ----------------------------------
def test_boundary_tables_are_window_means_in_v2_and_last_rows_in_v1(tmp_path):
    g, spec = aero()
    for case, current in ((tmp_path/'v2', spec), (tmp_path/'v1', spec.model_copy(update={'schema_version': 1}))):
        folder = case/'postProcessing/pressure_inlet/0'; folder.mkdir(parents=True)
        folder.joinpath('surfaceFieldValue.dat').write_text(''.join(f'{i} {i % 2}\n' for i in range(1, 101)))
        flux = case/'postProcessing/flux_inlet/0'; flux.mkdir(parents=True)
        flux.joinpath('surfaceFieldValue.dat').write_text(''.join(f'{i} {-1 - (i % 2)}\n' for i in range(1, 101)))
    v2 = quality.analyze(tmp_path/'v2', spec)
    assert v2['boundary_pressure_pa']['inlet'] == pytest.approx(0.5*spec.fluid.density, rel=0.1)
    assert v2['boundary_flux_m3s']['inlet'] == pytest.approx(-1.5, rel=0.1)
    v1 = quality.analyze(tmp_path/'v1', spec.model_copy(update={'schema_version': 1}))
    assert v1['boundary_pressure_pa']['inlet'] == 0 and v1['boundary_flux_m3s']['inlet'] == -1


def test_openings_count_as_the_inlets_they_compile_to():
    from gustsim.models import Opening
    g = geometry.add_primitive(Primitive(shape='cylinder', radius=.1, length=.4, role='enclosure'))
    mesh, groups = geometry.load(g['id'])
    caps = sorted([(mesh.triangles_center[groups == p['group']].mean(axis=0)[0], p['name']) for p in g['patches']])
    inlet, outlet = caps[0][1], caps[-1][1]
    spec = confirmed(presets.generate(PresetRequest(geometry_id=g['id'], kind='pipe', inlet=inlet, outlet=outlet, driving='flow'))['spec'])
    wall = next(b.patch for b in spec.boundaries if b.kind == 'wall')
    spec.elements = Elements(openings=[Opening(patch=wall, kind='inflow_flow_rate', value=1e-4)])
    kinds = {b.patch: b.kind for b in quality.compiled_boundaries(spec)}
    assert kinds[wall] == 'flow_inlet'
    assert wall not in manifest.object_patches(spec, db.geometry(spec.geometry_id))


# ---- 13: pipe inlet length scale ---------------------------------------------------------------------------
def test_pipe_inlet_length_scale_follows_the_hydraulic_diameter():
    g = geometry.add_primitive(Primitive(shape='cylinder', radius=.1, length=.4, role='enclosure'))
    mesh, groups = geometry.load(g['id'])
    caps = sorted([(mesh.triangles_center[groups == p['group']].mean(axis=0)[0], p['name']) for p in g['patches']])
    spec = presets.generate(PresetRequest(geometry_id=g['id'], kind='pipe', inlet=caps[0][1], outlet=caps[-1][1], driving='flow'))['spec']
    assert spec['flow']['length_scale'] == pytest.approx(0.07*0.2, rel=0.03)


# ---- 15: retry changes survive the attempt's result writes -------------------------------------------------
def test_retry_changes_are_kept_in_the_new_attempts_result():
    g, spec = aero()
    job = db.enqueue(spec.model_dump(), 'case')
    changes = [{'path': 'mesh.body_level', 'from': 1, 'to': 2}]
    db.merge_result(job['id'], {'changes': changes})
    claimed = db.claim()
    worker.process_job(claimed)
    run = db.run(job['id'])
    assert run['status'] == 'completed' and run['result']['changes'] == changes and run['result']['case_available']


# ---- 17: package lookup by parent ---------------------------------------------------------------------------
def test_runs_filter_by_parent():
    g, spec = aero()
    solve_a, solve_b = db.enqueue(spec.model_dump(), 'solve'), db.enqueue(spec.model_dump(), 'solve')
    first = db.enqueue({'run_id': solve_a['id']}, 'package', parent_id=solve_a['id'])
    for _ in range(3):
        db.enqueue({'run_id': solve_b['id']}, 'package', parent_id=solve_b['id'])
    found = db.runs(kinds=('package',), parent_id=solve_a['id'], limit=1)
    assert [r['id'] for r in found] == [first['id']]


# ---- 20: adjoint scheme names and initial fields ---------------------------------------------------------------
def test_adjoint_numerics_use_negative_flux_names_and_initial_fields(tmp_path):
    from gustsim import adjoint
    g, spec = aero()
    spec.design.enabled = True
    case = foam.compile_case(spec, tmp_path/'case')
    (case/'100').mkdir()
    adjoint.write_sensitivity(spec, case)
    schemes = (case/'system/fvSchemes').read_text()
    assert 'div(-phi,Ua)' in schemes and 'div(-phi,ka)' in schemes
    assert 'adjointFarFieldVelocity' in (case/'100/Ua').read_text()
    assert 'adjointWallVelocity' in (case/'100/Ua').read_text()
    assert (case/'100/pa').is_file() and (case/'system/finite-area/faSchemes').is_file()
    assert '|ma|da' in (case/'system/fvSolution').read_text()


# ---- drafts can be hidden without deleting them ------------------------------------------------------------------
def test_hidden_draft_is_kept_and_unhidden_when_saved_again():
    from gustsim import projects
    identifier = db.uid()
    projects.save(identifier, {'name': 'Old demo', 'workspace': {'version': 2}})
    record = projects.read(identifier)
    projects.save(identifier, {**record, 'archived': True})
    listed = {p['id']: p for p in projects.listing()}
    assert listed[identifier]['archived'] and 'workspace' not in listed[identifier]
    assert projects.read(identifier)['workspace'] == {'version': 2}
    projects.save(identifier, {'name': 'Old demo', 'workspace': {'version': 2}})
    assert not projects.read(identifier)['archived']
