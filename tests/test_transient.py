"""M6 transient: plan and dictionaries, statistics on a known signal, the analysis window and
checks, and the results package on time-averaged fields with animation frames. The force
history and frames are prescribed signals, not a flow solution."""
import json
import math
import re
import numpy as np
import pytest
import foamcase
from gustsim import config, db, foam, quality, transient, validation
from gustsim.jobs import package as package_job
from gustsim.models import TimeControl
from test_results_package import BODY, box_case


def transient_case(duration=4.0, frames=20, fraction=0.5):
    g, spec, faces = box_case()
    spec.time = TimeControl(mode='transient', duration_s=duration, frames=frames, average_after_fraction=fraction)
    return g, spec, faces


def test_plan_and_transient_dictionaries(tmp_path):
    g, spec, _ = transient_case(duration=None)
    spec.time.flow_throughs = 4
    times = transient.plan(spec, 400)
    length = transient.domain_length(spec)
    assert times['duration_s'] == pytest.approx(4*length/spec.flow.speed)
    assert times['average_start'] == pytest.approx(400 + 0.5*times['duration_s'])
    assert times['frame_interval_s'] == pytest.approx(0.5*times['duration_s']/20)
    assert 0 < times['first_step_s'] <= times['duration_s']/200
    report = validation.validate(spec)
    assert report['valid'], report['errors']
    assert 'transient' in report['experimental_features'] and any('Transient simulation' in w for w in report['warnings'])
    case = foam.compile_case(spec, tmp_path/'case')
    transient.prepare(spec, case, 400, [2, 1, 1])
    control = (case/'system/controlDict').read_text()
    for token in ('application pimpleFoam', 'startFrom latestTime', 'adjustTimeStep yes', f'endTime {400 + times["duration_s"]:.12g}',
                  'type fieldAverage', 'type surfaces', 'cuttingPlane', 'type forces'):
        assert token in control, token
    assert 'default backward' in (case/'system/fvSchemes').read_text()
    solution = (case/'system/fvSolution').read_text()
    assert 'PIMPLE' in solution and 'pFinal' in solution
    assert json.loads((case/transient.PLAN).read_text())['start'] == 400


def test_zero_speed_is_rejected_for_transient():
    g, spec, _ = transient_case()
    still = spec.model_copy(deep=True); still.flow.speed = 0
    assert any('nonzero flow speed' in e for e in validation.validate(still)['errors'])


def write_history(case, spec, start, duration, frequency, rows_fraction=1.0, dt=1e-3):
    drag, lift = np.asarray(spec.references.drag_axis), np.asarray(spec.references.lift_axis)
    folder = case/'postProcessing/loads/0'; folder.mkdir(parents=True, exist_ok=True)
    vec = lambda v: ' '.join(f'{x:.12g}' for x in v)
    with (folder/'force.dat').open('w') as f, (folder/'moment.dat').open('w') as m:
        f.write('# Time total pressure viscous\n')
        for i in range(1, int(start) + 1):
            f.write(f'{i} ({vec(12*drag)}) ({vec(12*drag)}) (0 0 0)\n'); m.write(f'{i} (0 0 0) (0 0 0) (0 0 0)\n')
        steps = int(duration*rows_fraction/dt)
        for k in range(1, steps + 1):
            t = start + k*dt
            total = 12*drag + 3*math.sin(2*math.pi*frequency*(t - start))*lift
            f.write(f'{t:.9f} ({vec(total)}) ({vec(total)}) (0 0 0)\n'); m.write(f'{t:.9f} (0 0 0) (0 0 0) (0 0 0)\n')


def test_statistics_recover_mean_and_strouhal(tmp_path):
    g, spec, _ = transient_case(duration=4.0)
    case = tmp_path/'case'; (case/'results').mkdir(parents=True)
    times = transient.plan(spec, 100)
    (case/transient.PLAN).write_text(json.dumps(times))
    write_history(case, spec, 100, 4.0, frequency=10.0)
    stats = transient.analyse(case, spec)
    assert stats['state'] == 'converged', stats['message']
    assert stats['statistics']['drag_n']['mean'] == pytest.approx(12, rel=1e-6)
    assert stats['statistics']['lift_n']['std'] == pytest.approx(3/math.sqrt(2), rel=0.02)
    assert stats['dominant_frequency_hz'] == pytest.approx(10.0, rel=0.05)
    assert stats['strouhal'] == pytest.approx(10.0*spec.references.length/spec.flow.speed, rel=0.05)
    assert stats['periods_in_window'] == pytest.approx(20, rel=0.05)
    # A run that stopped early is reported as incomplete, not converged.
    short = tmp_path/'short'; (short/'results').mkdir(parents=True)
    (short/transient.PLAN).write_text(json.dumps(times))
    write_history(short, spec, 100, 4.0, frequency=10.0, rows_fraction=0.7)
    assert transient.analyse(short, spec)['state'] == 'incomplete'


def test_analysis_uses_the_transient_window_and_checks(tmp_path):
    g, spec, faces = transient_case(duration=4.0)
    case = tmp_path/'case'
    foamcase.build(case, cells=(16, 8, 8), body=BODY, body_faces=faces, speed=10.0)
    (case/'results').mkdir(exist_ok=True)
    times = transient.plan(spec, 100)
    (case/transient.PLAN).write_text(json.dumps(times))
    write_history(case, spec, 100, 4.0, frequency=10.0)
    (case/'logs').mkdir(exist_ok=True)
    (case/'logs/check.log').write_text('Mesh OK.\ncells: 2048\n')
    (case/'logs/transient.log').write_text('Courant Number mean: 0.1 max: 1.8\nTime = 104\n')
    result = quality.analyze(case, spec)
    codes = {f['code']: f['status'] for f in result['findings']}
    assert codes['statistical_convergence'] == 'pass' and codes['courant'] == 'pass' and 'residuals' not in codes
    assert result['termination'] == 'transient_complete'
    assert result['metrics']['drag_n'] == pytest.approx(12, rel=1e-6), 'loads are the transient window mean'
    assert result['transient']['strouhal'] == pytest.approx(10.0*spec.references.length/spec.flow.speed, rel=0.05)
    assert result['averaging']['start'] == pytest.approx(times['average_start'], abs=2e-3)


def write_frames(case, start, count=3):
    import vtk
    for k in range(count):
        folder = case/f'postProcessing/frames/{start + 0.1*(k + 1):.6g}'; folder.mkdir(parents=True)
        for name in ('object', 'section'):
            plane = vtk.vtkPlaneSource(); plane.SetResolution(2, 2)
            tri = vtk.vtkTriangleFilter(); tri.SetInputConnection(plane.GetOutputPort()); tri.Update()
            poly = tri.GetOutput()
            n = poly.GetNumberOfCells()
            p = vtk.vtkFloatArray(); p.SetName('p')
            u = vtk.vtkFloatArray(); u.SetName('U'); u.SetNumberOfComponents(3)
            for i in range(n):
                p.InsertNextValue(50.0*(k + 1)); u.InsertNextTuple3(3.0*(k + 1), 4.0*(k + 1), 0)
            poly.GetCellData().AddArray(p); poly.GetCellData().AddArray(u)
            writer = vtk.vtkXMLPolyDataWriter(); writer.SetFileName(str(folder/f'{name}.vtp')); writer.SetInputData(poly); writer.Write()
    return n


def test_package_uses_mean_fields_and_packs_animation_frames():
    g, spec, faces = transient_case(duration=4.0, frames=3)
    run = db.enqueue(spec.model_dump(), 'solve')
    case = config.DATA/'runs'/run['id']
    foamcase.build(case, cells=(16, 8, 8), body=BODY, body_faces=faces, speed=10.0)
    patches = re.findall(r'^\s*(\w+)\s*\n\s*\{', (case/'constant/polyMesh/boundary').read_text(), re.M)
    boundary = ''.join(f'    {p}\n    {{\n        type calculated;\n        value uniform 7;\n    }}\n' for p in patches if p != 'FoamFile')
    (case/'100/pMean').write_text(foamcase.header('volScalarField', 'pMean', '100') +
                                  f'dimensions [0 2 -2 0 0 0 0];\ninternalField uniform 7;\nboundaryField\n{{\n{boundary}}}\n')
    (case/'100/UMean').write_text((case/'100/U').read_text().replace('object U;', 'object UMean;'))
    (case/'results').mkdir(exist_ok=True)
    times = transient.plan(spec, 99)
    (case/transient.PLAN).write_text(json.dumps(times))
    cells = write_frames(case, 99)
    write_history(case, spec, 99, 4.0, frequency=10.0)
    transient.analyse(case, spec)
    db.update(run['id'], status='completed', stage='completed', result={'metrics': {}, 'fields_available': True})
    out = package_job.build(run['id'], in_process=True)
    assert out['basis'] == 'time_averaged'
    surface = next(l for l in out['layers'] if l['id'] == 'surface')
    pressure = next(a for a in surface['arrays'] if a['name'] == 'pressure_pa')
    assert pressure['range'] == pytest.approx([7*spec.fluid.density]*2, rel=1e-5), 'surface pressure is the time average'
    animation = next(l for l in out['layers'] if l['id'] == 'animation_object')
    frames = animation['frames']
    assert frames['count'] == 3 and frames['array'] == 'Cp' and frames['times_s'] == pytest.approx([0.1, 0.2, 0.3])
    data = np.fromfile(case/frames['url'], dtype='<f4').reshape(3, cells)
    q = 0.5*spec.flow.speed**2
    assert data[:, 0] == pytest.approx([50/q, 100/q, 150/q], rel=1e-6)
    section = next(l for l in out['layers'] if l['id'] == 'animation_section')
    values = np.fromfile(case/section['frames']['url'], dtype='<f4').reshape(3, cells)
    assert values[:, 0] == pytest.approx([5, 10, 15], rel=1e-6) and section['frames']['unit'] == 'm/s'
    # The report adds a transient page and labels the run as transient.
    from gustsim.jobs import report as report_job
    data = report_job.build(run['id']).read_bytes()
    assert data[:4] == b'%PDF' and b'Transient CFD results' in data
    assert len(re.findall(rb'/Type\s*/Page\b', data)) >= 10


def test_steady_time_state_is_set_aside_for_the_transient_restart(tmp_path):
    for root in (tmp_path, tmp_path/'processor0', tmp_path/'processor1'):
        (root/'425/uniform').mkdir(parents=True)
        (root/'425/uniform/time').write_text('value 425; index 425; deltaT 1; deltaT0 1;')
        (root/'400/uniform').mkdir(parents=True)
        (root/'400/uniform/time').write_text('index 400;')
    transient.release_steady_time_state(tmp_path, 425.0)
    for root in (tmp_path, tmp_path/'processor0', tmp_path/'processor1'):
        assert not (root/'425/uniform/time').exists() and (root/'425/uniform/time.steady').read_text().startswith('value 425')
        assert (root/'400/uniform/time').exists(), 'only the restart time is touched'


def test_monitor_history_includes_every_restart_segment(tmp_path):
    from gustsim import monitor
    g, spec, _ = transient_case()
    for start, rows in (('0', [(1, 10.0), (2, 11.0)]), ('2', [(2.01, 12.0), (2.02, 13.0)])):
        folder = tmp_path/f'postProcessing/loads/{start}'; folder.mkdir(parents=True)
        (folder/'force.dat').write_text('# Time total\n' + ''.join(f'{t} ({v} 0 0) ({v} 0 0) (0 0 0)\n' for t, v in rows))
    forces, _ = monitor.history(tmp_path, spec)
    assert [f['iteration'] for f in forces] == [1, 2, 2.01, 2.02]
