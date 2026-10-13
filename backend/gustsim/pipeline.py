"""Simulations: one user-facing record over immutable attempts (mesh, solve, package, ...).

`advance` derives the simulation state from its attempts and queues the next stage under
deterministic identifiers, so calling it twice (or after a restart) never duplicates work.
A mesh continues to the solve automatically only when its evidence has no review, failed or
unavailable finding and the plan allows it; otherwise the simulation waits for a review.
"""
import hashlib
import json
import time
from . import capabilities, config, db, workflow
from .models import SimulationSpec

ACTIVE = ('queued', 'running', 'generating')
REQUIRED_MESH_EVIDENCE = {'mesh_quality', 'mesh_boundaries', 'mesh_preview', 'mesh_rotation', 'mesh_extended'}


def stage_id(simulation_id, stage, attempt=1):
    return hashlib.sha256(f'{simulation_id}:{stage}:{attempt}'.encode()).hexdigest()[:32]


def decode(row):
    record = dict(row)
    for key in ('plan', 'spec', 'summary'):
        record[key] = json.loads(record[key])
    return record


def get(identifier):
    with db.connection() as c:
        row = c.execute('SELECT * FROM simulations WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise KeyError('Simulation not found')
        tags = [r[0] for r in c.execute('SELECT tag FROM simulation_tags WHERE simulation_id=? ORDER BY tag', (identifier,))]
    return {**decode(row), 'tags': tags}


def attempts(identifier, simulation=None):
    runs = db.runs(simulation_id=identifier, limit=500)
    legacy = (simulation or get(identifier))['plan'].get('legacy_run')
    if legacy:
        # An adopted pre-v2 run: the simulation references the solve (and its mesh) without
        # modifying either record.
        try:
            solve = db.run(legacy)
            runs = [solve] + runs
            if solve.get('parent_id'):
                parent = db.run(solve['parent_id'])
                if parent['kind'] == 'mesh':
                    runs = [parent] + runs
        except KeyError:
            pass
    return sorted(runs, key=lambda r: r['created'])


def legacy_id(run_id):
    return hashlib.sha256(f'legacy:{run_id}'.encode()).hexdigest()[:32]


def adopt_legacy_runs():
    """List solve runs made outside the simulations pipeline (before v2, or through the run API)
    as simulations that reference them. Idempotent; the runs themselves are never modified."""
    with db.connection() as c:
        rows = c.execute("SELECT id,created,updated,spec FROM runs WHERE kind='solve' AND simulation_id IS NULL "
                         "AND status IN ('completed','failed','cancelled','interrupted')").fetchall()
        for run_id, created, updated, spec in rows:
            data = json.loads(spec)
            plan = {'legacy_run': run_id, 'stages': ['solve'], 'auto_review': False}
            c.execute('INSERT OR IGNORE INTO simulations(id,project_id,created,updated,name,status,stage,plan,spec,summary,archived) '
                      'VALUES(?,?,?,?,?,?,?,?,?,?,0)',
                      (legacy_id(run_id), data.get('project_id'), created, updated, (data.get('name') or 'Earlier run')[:120],
                       'queued', 'solve', json.dumps(plan), spec, json.dumps({})))
        fresh = [r[0] for r in c.execute("SELECT id FROM simulations WHERE summary='{}' AND json_extract(plan,'$.legacy_run') IS NOT NULL")]
    for identifier in fresh:
        refresh(identifier)


def latest(runs, kind):
    matches = [r for r in runs if r['kind'] == kind]
    return matches[-1] if matches else None


def create(spec, plan=None, project_id=None, name=None, tags=(), queue_mesh=True):
    identifier = db.uid()
    plan = {'stages': ['mesh', 'review', 'solve', 'package'], 'auto_review': True, **(plan or {})}
    now = time.time()
    with db.connection() as c:
        c.execute('INSERT INTO simulations(id,project_id,created,updated,name,status,stage,plan,spec,summary,archived) VALUES(?,?,?,?,?,?,?,?,?,?,0)',
                  (identifier, project_id or spec.project_id, now, now, name or spec.name, 'queued', 'mesh', json.dumps(plan),
                   spec.model_dump_json(), json.dumps({})))
        for tag in sorted({t.strip() for t in tags if t and t.strip()}):
            c.execute('INSERT OR IGNORE INTO simulation_tags VALUES(?,?)', (identifier, tag[:40]))
    if queue_mesh:
        db.enqueue(spec.model_dump(), 'mesh', simulation_id=identifier, identifier=stage_id(identifier, 'mesh'))
    return refresh(identifier)


def mesh_for(simulation, runs):
    """The simulation's own latest mesh, or the shared mesh of the batch member it borrows from."""
    own = latest(runs, 'mesh')
    if own is not None or not simulation['plan'].get('mesh_from'):
        return own
    return latest(attempts(simulation['plan']['mesh_from']), 'mesh')


def dependents(identifier):
    with db.connection() as c:
        return [r[0] for r in c.execute("SELECT id FROM simulations WHERE json_extract(plan,'$.mesh_from')=?", (identifier,))]


def mesh_ready_for_auto_review(mesh):
    findings = (mesh['result'].get('mesh_summary') or {}).get('findings', [])
    codes = {f['code'] for f in findings}
    preview = (config.DATA/'runs'/mesh['id']/'mesh-preview.vtp').is_file()
    return (REQUIRED_MESH_EVIDENCE <= codes and preview and mesh['result'].get('mesh_available')
            and not any(f['status'] in ('review', 'fail', 'unavailable') for f in findings))


def record_review(mesh, acknowledged=False):
    """Mark a mesh reviewed (same rule as POST /api/runs/{id}/review)."""
    findings = (mesh['result'].get('mesh_summary') or {}).get('findings', [])
    if not REQUIRED_MESH_EVIDENCE <= {f['code'] for f in findings} or any(f['status'] in ('fail', 'unavailable') for f in findings):
        raise ValueError('Required mesh evidence or preview is unavailable')
    if any(f['status'] == 'review' for f in findings) and not acknowledged:
        raise ValueError('Acknowledge the mesh findings before continuing')
    db.merge_result(mesh['id'], {'reviewed_configuration_id': workflow.identity(mesh['spec']),
                                 'reviewed_mesh_identity': workflow.mesh_identity(mesh['spec']),
                                 'reviewed_by': 'automatic' if not acknowledged else 'user'})


def queue_solve(simulation, mesh):
    # A later mesh of this simulation (adaptive refinement) carries its own spec. A mesh borrowed
    # from another batch member has that member's physics (speed, rpm), so the solve keeps ours.
    own = mesh.get('simulation_id') == simulation['id']
    spec = mesh['spec'] if own and isinstance(mesh.get('spec'), dict) else simulation['spec']
    attempt = 1 + sum(1 for r in attempts(simulation['id']) if r['kind'] == 'solve')
    identifier = stage_id(simulation['id'], 'solve', attempt)
    try:
        return db.run(identifier)
    except KeyError:
        return db.enqueue(spec, 'solve', parent_id=mesh['id'], simulation_id=simulation['id'], attempt=attempt, identifier=identifier)


def _unsolved_mesh(identifier, runs):
    solved = {run.get('parent_id') for run in runs if run['kind'] == 'solve'}
    own = [run for run in runs if run['kind'] == 'mesh' and run.get('simulation_id') == identifier
           and run['status'] == 'completed' and run['id'] not in solved]
    return own[-1] if own else None


def advance(identifier):
    simulation = get(identifier)
    runs = attempts(identifier)
    mesh, solve = mesh_for(simulation, runs), latest(runs, 'solve')
    borrowed = mesh is not None and mesh.get('simulation_id') != identifier
    pending = _unsolved_mesh(identifier, runs)
    if pending is None and solve is None:
        pending = mesh
    if pending is not None and pending['status'] == 'completed' and 'solve' in simulation['plan']['stages']:
        reviewed = pending['result'].get('reviewed_mesh_identity') or pending['result'].get('reviewed_configuration_id')
        if not reviewed and not borrowed and simulation['plan'].get('auto_review') and mesh_ready_for_auto_review(pending):
            record_review(pending)
            reviewed = True
        if reviewed:
            queue_solve(simulation, db.run(pending['id']))
    from . import amr
    from .jobs.amr import enqueue as enqueue_indicator
    step = amr.action(simulation, attempts(identifier))
    if step and step[0] == 'indicator':
        enqueue_indicator(step[1]['id'])
    elif step and step[0] == 'mesh':
        _, solved_run, indicator = step
        spec = amr.refined_spec(simulation['spec'], solved_run['id'], indicator['result'])
        attempt = 1 + sum(1 for run in runs if run['kind'] == 'mesh' and run.get('simulation_id') == identifier)
        db.enqueue(spec.model_dump(mode='json'), 'mesh', simulation_id=identifier,
                   identifier=stage_id(identifier, 'mesh', attempt), attempt=attempt)
    state = refresh(identifier)
    if not borrowed:
        for other in dependents(identifier):
            advance(other)
    return state


def advance_run(run_id):
    run = db.run(run_id)
    simulation_id = run.get('simulation_id')
    if simulation_id is None and run.get('parent_id'):
        simulation_id = db.run(run['parent_id']).get('simulation_id')
    if simulation_id is None:
        solve_id = run['parent_id'] if run['kind'] in ('package', 'report') else run_id
        with db.connection() as c:
            row = c.execute("SELECT id FROM simulations WHERE json_extract(plan,'$.legacy_run')=?", (solve_id,)).fetchone()
        simulation_id = row[0] if row else None
    if simulation_id:
        return advance(simulation_id)
    return None


def derive(simulation, runs):
    mesh, solve = mesh_for(simulation, runs), latest(runs, 'solve')
    if mesh is not None and solve is not None and mesh['created'] > solve['created'] and not any(
            run['kind'] == 'solve' and run.get('parent_id') == mesh['id'] for run in runs):
        if mesh['status'] == 'cancelled':
            return 'cancelled', 'mesh', mesh
        if mesh['status'] in ('failed', 'interrupted'):
            return 'failed', 'mesh', mesh
        if mesh['status'] in ACTIVE:
            return ('queued' if mesh['status'] == 'queued' else 'meshing'), 'mesh', mesh
        return 'awaiting_review', 'review', mesh
    package = None
    if solve is not None:
        # Query by parent: a window of recent package runs misses older solves on a busy install.
        package = next(iter(db.runs(kinds=('package',), parent_id=solve['id'], limit=1)), None)
    current = solve or mesh
    if current is None:
        return 'queued', 'mesh', None
    if solve is None and mesh is not None and mesh.get('simulation_id') != simulation['id'] and mesh['status'] == 'completed':
        # Waiting for the batch member that owns the shared mesh to have it reviewed.
        return 'awaiting_review', 'review', mesh
    if current['status'] in ('cancelled',):
        return 'cancelled', current['kind'], current
    if current['status'] in ('failed', 'interrupted'):
        retried = any(r.get('retry_of') == current['id'] for r in runs)
        return ('queued' if retried else 'failed'), current['kind'], current
    if current['kind'] == 'mesh':
        if current['status'] in ACTIVE:
            return ('queued' if current['status'] == 'queued' else 'meshing'), 'mesh', current
        return 'awaiting_review', 'review', current
    if current['status'] in ACTIVE:
        return ('queued' if current['status'] == 'queued' else 'solving'), 'solve', current
    if package is None and simulation['plan'].get('legacy_run'):
        # Results from before the results package: shown in legacy mode, packaged on request.
        return 'completed', 'completed', current
    if package is None or package['status'] in ACTIVE:
        return 'postprocessing', 'package', current
    if package['status'] in ('failed', 'interrupted', 'cancelled'):
        # The solve is complete but its results package is not: say so, so it can be retried.
        return 'failed', 'package', package
    return 'completed', 'completed', current


def refresh(identifier):
    simulation = get(identifier)
    runs = attempts(identifier)
    status, stage, current = derive(simulation, runs)
    solve = latest(runs, 'solve')
    result = solve['result'] if solve else {}
    metrics = result.get('metrics') or {}
    headline = {k: metrics.get(k) for k in ('cd', 'cl', 'drag_n', 'lift_n', 'thrust_n', 'torque_nm', 'efficiency', 'pressure_drop_pa', 'outlet_mass_flow_kgs')}
    headline.update({k: v for k, v in (result.get('headline') or {}).items() if v is not None})
    summary = {'headline': headline,
               'cells': (latest(runs, 'mesh') or {}).get('result', {}).get('cell_count') if latest(runs, 'mesh') else None,
               'convergence': (result.get('convergence') or {}).get('message'),
               'thumbnail': (result.get('package') or {}).get('thumbnail'),
               'results_run_id': solve['id'] if solve and solve['status'] == 'completed' else None,
               'numerical_status': result.get('numerical_status'), 'validation_status': result.get('validation_status', 'experimental_validation_pending'),
               'current_run_id': current['id'] if current else None, 'error': current['error'] if current and current['status'] == 'failed' else None}
    with db.connection() as c:
        c.execute('UPDATE simulations SET status=?,stage=?,summary=?,updated=? WHERE id=?', (status, stage, json.dumps(summary), time.time(), identifier))
    return get(identifier)


def describe(simulation, runs=None):
    """The dashboard and detail representation."""
    runs = runs if runs is not None else attempts(simulation['id'])
    spec = SimulationSpec.model_validate(simulation['spec'])
    try:
        meta = db.geometry(spec.geometry_id)
    except KeyError:
        meta = {'id': spec.geometry_id, 'filename': 'missing geometry'}
    experimental = capabilities.experimental_used(spec)
    summary = simulation['summary']
    thumbnail = summary.get('thumbnail')
    results_run = summary.get('results_run_id')
    return {'id': simulation['id'], 'project_id': simulation['project_id'], 'name': simulation['name'], 'tags': simulation['tags'],
            'archived': bool(simulation['archived']), 'created': simulation['created'], 'updated': simulation['updated'],
            'status': simulation['status'], 'stage': simulation['stage'], 'plan': simulation['plan'],
            'geometry': {'id': meta['id'], 'filename': meta.get('filename'), 'fidelity': meta.get('geometry_fidelity') or 'imported'},
            'setup_summary': {'use_case': spec.use_case.kind if spec.use_case else 'custom', 'mode': spec.mode, 'fluid': spec.fluid.name,
                              'speed_m_s': spec.flow.speed, 'turbulence': spec.flow.turbulence, 'tier': spec.mesh.tier,
                              'ground': spec.environment.ground, 'transient': spec.time.mode == 'transient',
                              'design': spec.design.enabled,
                              'elements': {'rotating_zones': len(spec.rotating_zones()), 'wheels': len(spec.elements.wheels),
                                           'porous': len(spec.elements.porous), 'openings': len(spec.elements.openings),
                                           'design_boxes': len(spec.elements.design_boxes)}},
            'experimental': experimental,
            'stages': [{'id': r['id'], 'kind': r['kind'], 'status': r['status'], 'stage': r['stage'], 'attempt': r.get('attempt') or 1,
                        'retry_of': r.get('retry_of'), 'retry_reason': r.get('retry_reason'), 'created': r['created'],
                        'finished': r.get('finished'), 'error': r['error']} for r in runs],
            'headline': summary.get('headline') or {}, 'cells': summary.get('cells'), 'convergence': summary.get('convergence'),
            'thumbnail_url': f'/api/runs/{results_run}/files/{thumbnail}' if thumbnail and results_run else None,
            'results_run_id': results_run, 'current_run_id': summary.get('current_run_id'), 'error': summary.get('error'),
            'status_block': {'geometry': meta.get('geometry_fidelity') if meta.get('geometry_fidelity') in ('wrapped', 'morphed') else 'imported',
                             'execution': simulation['status'], 'numerical': summary.get('numerical_status') or 'unavailable',
                             'validation': summary.get('validation_status') or 'experimental_validation_pending',
                             'physics_maturity': 'experimental' if experimental else 'core'}}


def listing(project_id=None, tag=None, status=None, query=None, archived=False, limit=500):
    adopt_legacy_runs()
    where, params = ['archived=?'], [1 if archived else 0]
    if project_id:
        where.append('project_id=?'); params.append(project_id)
    if status:
        where.append('status=?'); params.append(status)
    if query:
        where.append('name LIKE ?'); params.append(f'%{query}%')
    if tag:
        where.append('id IN (SELECT simulation_id FROM simulation_tags WHERE tag=?)'); params.append(tag)
    with db.connection() as c:
        rows = [r[0] for r in c.execute('SELECT id FROM simulations WHERE ' + ' AND '.join(where) + ' ORDER BY updated DESC LIMIT ?', (*params, limit))]
    return [describe(get(i)) for i in rows]


def update(identifier, name=None, tags=None, archived=None):
    get(identifier)
    with db.connection() as c:
        if name is not None:
            if not 1 <= len(name) <= 120:
                raise ValueError('Simulation name must contain 1–120 characters')
            c.execute('UPDATE simulations SET name=?,updated=? WHERE id=?', (name, time.time(), identifier))
        if archived is not None:
            c.execute('UPDATE simulations SET archived=?,updated=? WHERE id=?', (1 if archived else 0, time.time(), identifier))
        if tags is not None:
            c.execute('DELETE FROM simulation_tags WHERE simulation_id=?', (identifier,))
            for tag in sorted({t.strip()[:40] for t in tags if t and t.strip()}):
                c.execute('INSERT INTO simulation_tags VALUES(?,?)', (identifier, tag))
    return get(identifier)


def continue_after_review(identifier, acknowledge=False):
    simulation = get(identifier)
    mesh = latest(attempts(identifier), 'mesh')
    if mesh is None or mesh['status'] != 'completed':
        raise ValueError('There is no completed mesh to review')
    if not (mesh['result'].get('reviewed_mesh_identity') or mesh['result'].get('reviewed_configuration_id')):
        record_review(mesh, acknowledged=acknowledge)
    queue_solve(simulation, db.run(mesh['id']))
    for other in dependents(identifier):
        advance(other)
    return refresh(identifier)


def cancel(identifier):
    for run in attempts(identifier):
        if run['status'] in ACTIVE:
            db.update(run['id'], cancel=1, **({'status': 'cancelled', 'stage': 'cancelled'} if run['status'] != 'running' else {}))
    return refresh(identifier)
