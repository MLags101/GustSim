"""Automatic retry plans: evidence-driven setting changes applied to a *new* attempt.

A failed attempt is never modified. Each rule names the evidence it reacts to and the exact
setting changes, which the new attempt records (retry_reason, result.changes).
"""
import copy
import re


def _log(root, name):
    path = root / 'logs' / name
    try:
        return path.read_text(errors='replace') if path.is_file() else ''
    except OSError:
        return ''


def _set(spec, path, value):
    target = spec
    for key in path[:-1]:
        target = target[key]
    old = target.get(path[-1])
    target[path[-1]] = value
    return {'path': '.'.join(path), 'from': old, 'to': value}


def plan(kind, spec, error, result, root):
    """Return (reason, changes, new_spec, new_kind) or None when no rule applies.

    `spec` is the failed attempt's spec dict; it is copied, never changed in place."""
    text = str(error or '')
    if any(s in text for s in ('Cancelled', 'time limit', 'interrupted')):
        return None
    new = copy.deepcopy(spec)
    changes = []
    summary = (result or {}).get('mesh_summary') or {}
    codes = {f['code']: f['status'] for f in summary.get('findings', [])}
    solve_log = _log(root, 'solve.log')
    if 'memory ceiling' in text or 'Killed' in _log(root, 'meshing.log')[-2000:]:
        changes.append(_set(new, ('mesh', 'parallel'), True))
        changes.append(_set(new, ('mesh', 'max_cells'), max(10000, int(spec['mesh']['max_cells']*0.8))))
        return 'The process ran out of memory; mesh in parallel with a smaller cell budget', changes, new, 'mesh' if kind != 'case' else kind
    if codes.get('mesh_boundaries') == 'fail':
        level = min(10, int(spec['mesh'].get('body_level') or 0) + 1)
        changes.append(_set(new, ('mesh', 'body_level'), level))
        return 'Surfaces disappeared from the mesh; refine around the body', changes, new, 'mesh'
    if codes.get('mesh_quality') == 'fail':
        changes.append(_set(new, ('mesh', 'quality_profile'), 'relaxed'))
        changes.append(_set(new, ('mesh', 'layers'), max(0, int(spec['mesh']['layers']) - 1)))
        return 'Mesh quality limits failed; relax snapping and layer controls (the acceptance gate is unchanged)', changes, new, 'mesh'
    if kind == 'solve' and (re.search(r'Floating point exception|sigFpe|\bnan\b', solve_log, re.I) or 'solve failed' in text):
        if spec['solver'].get('numerics') == 'robust' and spec['solver'].get('initialise') == 'potential':
            return None
        changes.append(_set(new, ('solver', 'numerics'), 'robust'))
        changes.append(_set(new, ('solver', 'initialise'), 'potential'))
        return 'The solver diverged; restart with first-order startup numerics and potential-flow initialisation', changes, new, 'solve'
    return None
