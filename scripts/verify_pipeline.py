"""Exercise the v2 simulations pipeline of a running Compose stack through HTTP.

Sample sphere -> guided preset -> validate -> launch -> mesh -> review -> solve (force-convergence
monitor) -> results package -> PDF report -> results documents. Creates one small labelled
simulation in the normal workspace and keeps it. Uses only the Python standard library.

Usage: python scripts/verify_pipeline.py [http://localhost:8080] [existing-simulation-id] [--transient]
--transient adds a short time-accurate stage (pimpleFoam) with time averaging and animation frames.
Checks execution and contracts only; it does not certify CFD accuracy.
"""
import json
import sys
import time
import urllib.error
import urllib.request

TRANSIENT = '--transient' in sys.argv
argv = [a for a in sys.argv if a != '--transient']
base = (argv[1] if len(argv) > 1 else 'http://127.0.0.1:8080').rstrip('/')


def request(path, payload=None, raw=False, method=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=data, headers={'Content-Type': 'application/json'}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            content = response.read()
    except urllib.error.HTTPError as error:
        raise SystemExit(f'{path}: HTTP {error.code} {error.read()[:400]!r}')
    return content if raw else json.loads(content)


health = request('/api/health')
assert health['worker']['online'], health
existing = argv[2] if len(argv) > 2 else None


def launch():
    geometry = request('/api/samples/sphere', {})
    patches = [p['name'] for p in geometry['patches']]
    preview = request('/api/presets/preview', {'geometry_id': geometry['id'], 'kind': 'aerodynamics', 'speed': 20, 'patches': patches})
    spec = preview['spec']
    spec['name'] = 'Pipeline verification · sphere'
    spec['use_case']['confirmed'] = True
    spec['use_case']['references_confirmed'] = True
    # A small custom mesh keeps the check to minutes; the convergence monitor may stop it early.
    spec['mesh'].update({'tier': 'custom', 'base_cells': 24, 'surface_level': 3, 'feature_level': 3, 'body_level': 2, 'layers': 2})
    spec['solver'].update({'iterations': 600, 'processes': 2})
    spec['convergence'] = {**spec.get('convergence', {}), 'enabled': True, 'min_iterations': 150, 'cap': 600}
    if TRANSIENT:
        spec['name'] = 'Pipeline verification · sphere (transient)'
        spec['time'] = {'mode': 'transient', 'flow_throughs': 2, 'frames': 30, 'average_after_fraction': 0.4, 'max_courant': 2}
    report = request('/api/validate', spec)
    assert report['valid'], report['errors']
    print('Physics maturity:', report['physics_maturity'], '· review findings:',
          [f['code'] for f in report['findings'] if f['status'] == 'review'])
    simulation = request('/api/simulations', {'spec': spec, 'name': spec['name'], 'tags': ['verification'],
                                              'acknowledged': report['configuration_id']})
    return simulation['id']


identifier = existing or launch()
print('Simulation', identifier)

deadline = time.monotonic() + 3600
previous = None
reviewed = False
while time.monotonic() < deadline:
    record = request(f'/api/simulations/{identifier}')
    state = (record['status'], record['stage'])
    if state != previous:
        print(*state, flush=True)
        previous = state
    if record['status'] == 'awaiting_review' and not reviewed:
        # The mesh check had findings (or none were auto-accepted): review it as a user would.
        record = request(f'/api/simulations/{identifier}/continue?acknowledge_warnings=true', {})
        reviewed = True
        continue
    if record['status'] in ('completed', 'failed', 'cancelled'):
        break
    time.sleep(3)
else:
    raise TimeoutError('The pipeline took more than an hour; inspect it in the workbench')
assert record['status'] == 'completed', record.get('error') or record
run = record['results_run_id']
assert record['thumbnail_url'], 'the results package produced no thumbnail'
manifest = request(f'/api/runs/{run}/results/manifest')
layers = [layer['id'] for layer in manifest['layers']]
assert 'surface' in layers, manifest['missing']
forces = request(f'/api/runs/{run}/results/forces')
insights = request(f'/api/runs/{run}/results/insights')
pdf = None
for _ in range(180):  # the report runs on the post lane after the package
    try:
        pdf = request(f'/api/runs/{run}/export/pdf', raw=True)
        break
    except SystemExit:
        time.sleep(5)
assert pdf and pdf.startswith(b'%PDF'), 'the PDF report was not produced'
transient = None
if TRANSIENT:
    transient = request(f'/api/runs/{run}/results/transient')
    assert transient['state'] != 'unavailable', transient['message']
    assert manifest.get('basis') == 'time_averaged' and any(layer['kind'] == 'animation' for layer in manifest['layers']), manifest['missing']
print(json.dumps({
    'passed': True, 'simulation': identifier, 'solve_run': run, 'reviewed_manually': reviewed,
    'cells': record['cells'], 'convergence': record['convergence'], 'status': record['status_block'],
    'cd': forces['coefficients']['cd'], 'cl': forces['coefficients']['cl'],
    'layers': layers, 'missing': [m['id'] for m in manifest['missing']],
    'insights': [i['rule'] for i in insights.get('insights', [])], 'pdf_bytes': len(pdf),
    'transient': None if transient is None else {k: transient.get(k) for k in ('state', 'message', 'strouhal', 'periods_in_window')},
}, indent=2))
