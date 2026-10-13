"""Shared configuration identity and evidence-based readiness (no mutable study state)."""
import hashlib
import json
from .models import SimulationSpec


def canonical(value):
    # JavaScript JSON drops the sign of zero. It does not change the physics.
    if isinstance(value,dict):return {k:canonical(v) for k,v in value.items()}
    if isinstance(value,list):return [canonical(v) for v in value]
    return 0.0 if isinstance(value,float) and value==0 else value


def digest(data):
    return hashlib.sha256(json.dumps(canonical(data), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def dumped(spec):
    return SimulationSpec.model_validate(spec).model_dump(mode='json') if isinstance(spec, dict) else spec.model_dump(mode='json')


_DEFAULTS = None
# Fields added in spec v2. They enter the identity only when they differ from the default,
# so every configuration identity (and every recorded mesh review) from before v2 is unchanged.
V2_PATHS = (('environment',), ('elements',), ('time',), ('free_surface',), ('design',), ('references','object_forward'),
            ('references','object_up'), ('references','front_axle'), ('references','rear_axle'), ('references','force_groups'),
            ('mesh','quality_profile'), ('mesh','n_cells_between_levels'), ('mesh','nested_wake_boxes'),
            ('mesh','wake_length_factor'), ('mesh','wake_level_offset'), ('mesh','adaptive_cycles'), ('mesh','adaptive_source'))
# Run control and requested outputs do not change the configuration being simulated;
# `tier` is resolved into the numeric mesh fields, and parallel meshing is an execution choice.
RUN_CONTROL = (('convergence',), ('outputs',), ('mesh','tier'), ('mesh','parallel'), ('time','frames'))


def _defaults():
    global _DEFAULTS
    if _DEFAULTS is None:
        _DEFAULTS = SimulationSpec(geometry_id='0'*32).model_dump(mode='json')
        for path in RUN_CONTROL: _drop(_DEFAULTS, path)
    return _DEFAULTS


def _lookup(data, path):
    for key in path:
        if not isinstance(data, dict) or key not in data: return None, False
        data = data[key]
    return data, True


def _drop(data, path):
    for key in path[:-1]:
        data = data.get(key) if isinstance(data, dict) else None
    if isinstance(data, dict): data.pop(path[-1], None)


def identity(spec):
    data = dumped(spec)
    for key in ('name', 'solver', 'project_id'): data.pop(key, None)
    for path in RUN_CONTROL: _drop(data, path)
    defaults = _defaults()
    for path in V2_PATHS:
        value, present = _lookup(data, path)
        default, _ = _lookup(defaults, path)
        if present and canonical(value) == canonical(default): _drop(data, path)
    return digest(data)


def mesh_identity(spec):
    """What the volume mesh depends on: geometry, domain, mesh controls, refinement shapes and
    wall/patch typing. Speed, RPM, fluid and turbulence model are excluded, so a reviewed mesh
    can be reused across those variants. The wake box follows the flow direction and the
    reference length, so those are included for external flow."""
    s = SimulationSpec.model_validate(spec) if isinstance(spec, dict) else spec
    from .foam_elements import effective_boundaries
    def kind_class(b):
        return 'symmetry' if b.kind == 'symmetry' else 'wall' if b.kind in ('wall','moving_wall','rotating_wall') else 'patch'
    mesh = s.mesh.model_dump(mode='json'); mesh.pop('tier', None); mesh.pop('parallel', None)
    import math
    velocity = s.flow.velocity(); norm = math.sqrt(sum(v*v for v in velocity))
    zones = [{'name': z.name, 'origin': list(z.origin), 'axis': list(z.axis), 'radius': z.radius, 'length': z.length}
             for z in s.rotating_zones()]
    data = {'geometry_id': s.geometry_id, 'mode': s.mode, 'domain': s.domain.model_dump(mode='json'), 'mesh': mesh,
            'regions': [r.model_dump(mode='json') for r in s.regions],
            'patches': sorted((b.patch, kind_class(b)) for b in effective_boundaries(s)),
            'zones': zones,
            'porous': [p.model_dump(mode='json', include={'name','center','axis','up','width_m','height_m','thickness_m'}) for p in s.elements.porous],
            'ground': s.environment.model_dump(mode='json', include={'ride_height_m','yaw_deg'}),
            'free_surface': s.free_surface.waterline_z if s.free_surface else None}
    if s.mode != 'internal':
        data['wake'] = {'direction': [round(v/norm, 9) for v in velocity] if norm > 0 else [0, 0, 0],
                        'length': s.references.length}
    return digest(data)


def compatible(source, spec):
    return identity(source) == identity(spec)


def mesh_compatible(source, spec):
    """v2 configurations may reuse a mesh across physics-only changes; v1 keeps the strict rule."""
    target = SimulationSpec.model_validate(spec) if isinstance(spec, dict) else spec
    if target.schema_version >= 2:
        return mesh_identity(source) == mesh_identity(target)
    return compatible(source, spec)


def require_mesh(source, spec):
    if source['kind'] != 'mesh' or source['status'] != 'completed':
        raise ValueError('Select a completed mesh attempt')
    if not mesh_compatible(source['spec'], spec):
        raise ValueError('Geometry or physics changed after meshing; generate and review a new mesh')
    if source['result'].get('mesh_check') != 'passed':
        raise ValueError('This mesh has no passing quality evidence; generate and check a new mesh')


def geometry_findings(meta):
    d = meta['diagnostics']
    wrapped = meta.get('geometry_fidelity') == 'wrapped'
    checks = [
        ('units', meta['units_confirmed'], 'Model dimensions and units are available in metres', 'fail'),
        ('closed_surface', d['watertight'], 'Surface must be closed; wrap the assembly or cap planar openings', 'fail'),
        # Non-manifold junctions where parts meet do not stop snappyHexMesh from building a
        # valid mesh, so they are a review item rather than a blocker.
        ('manifold', not d['nonmanifold_edges'], 'No nonmanifold edges where parts meet', 'review'),
        ('triangles', not d['degenerate_faces'], 'No degenerate triangles; Auto prepare can repair them', 'fail'),
        ('normals', d['winding_consistent'], 'Consistent surface normals', 'fail'),
        ('cad_validity', meta.get('cad_valid') is not False, 'Imported CAD validity check must not fail', 'review'),
    ]
    findings = [{'code': code, 'stage': 'geometry', 'control': 'geometry-diagnostics',
                 'status': 'pass' if ok else severity, 'detail': detail}
                for code, ok, detail, severity in checks]
    if wrapped:
        info = meta.get('wrap') or {}
        findings.append({'code': 'geometry_fidelity', 'stage': 'geometry', 'control': 'geometry-diagnostics',
                         'status': 'review',
                         'detail': info.get('detail', 'Geometry is a shrink-wrapped approximation of the imported CAD.')})
    return findings


def enrich(report, spec, meta):
    findings = geometry_findings(meta)
    for i, message in enumerate(report['errors']):
        control = 'rotating-region' if any(s in message for s in ('MRF', 'Rotor', 'rotor', 'Rotation', 'Blade')) else 'boundary-conditions' if 'oundar' in message or 'inlet' in message else 'domain-fluid-region'
        findings.append({'code': f'setup_{i}', 'stage': 'setup', 'control': control, 'status': 'fail', 'detail': message})
    for i, message in enumerate(report['warnings']):
        informational = any(s in message for s in ('self-intersections', 'Steady MRF', 'Fully turbulent', 'undefined at zero'))
        findings.append({'code': f'warning_{i}', 'stage': 'setup', 'control': 'reference-quantities' if 'coefficients' in message else 'mesh-resolution', 'status': 'info' if informational else 'review', 'detail': message})
    # Ready means nothing blocks meshing. Review items (non-manifold junctions, a wrapped
    # approximation) stay visible as findings without stopping the workflow.
    report.update(findings=findings, configuration_id=identity(spec),
                  geometry_ready=not any(f['status']=='fail' for f in findings if f['stage']=='geometry'))
    return report


NUMBER = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'


def mesh_metrics(text):
    """Pull checkMesh's numbers out of its log.

    The log already reports non-orthogonality, skewness, aspect ratio and negative
    volumes; reducing all of that to `"Mesh OK." in text` discards the evidence a user
    needs to judge whether a mesh is good enough for the loads it produced.
    """
    import re
    patterns = {
        'cells': r'\bcells:\s+(\d+)',
        'points': r'\bpoints:\s+(\d+)',
        'faces': r'\bfaces:\s+(\d+)',
        'max_non_orthogonality': r'Mesh non-orthogonality Max:\s*(' + NUMBER + ')',
        'average_non_orthogonality': r'Mesh non-orthogonality Max:\s*' + NUMBER + r'\s+average:\s*(' + NUMBER + ')',
        'max_skewness': r'Max skewness\s*=\s*(' + NUMBER + ')',
        'max_aspect_ratio': r'Max aspect ratio\s*=\s*(' + NUMBER + ')',
        'min_face_area': r'Minimum face area\s*=\s*(' + NUMBER + ')',
        'min_cell_volume': r'Min volume\s*=\s*(' + NUMBER + ')',
    }
    metrics = {}
    for name, pattern in patterns.items():
        found = re.search(pattern, text)
        if found:
            value = float(found.group(1))
            metrics[name] = int(value) if name in ('cells', 'points', 'faces') else value
    # OpenFOAM marks this as a warning (one asterisk) or an error (three) depending on
    # whether the mesh still passes, so accept either.
    severe = re.search(r'\*{1,3}Number of severely non-orthogonal[^:]*:\s*(\d+)', text)
    if severe: metrics['severely_non_orthogonal_faces'] = int(severe.group(1))
    negative = re.search(r'\*{1,3}Error in face pyramids: (\d+) faces are incorrectly oriented', text)
    if negative: metrics['incorrectly_oriented_faces'] = int(negative.group(1))
    zero = re.search(r'\*{1,3}Zero or negative cell volume detected', text)
    metrics['negative_volume_cells'] = bool(zero)
    return metrics


def layer_coverage(text):
    """Parse snappyHexMesh's achieved-layer table.

    Requested layers are not achieved layers, and layer coverage drives wall shear and
    therefore drag. Without this the app can only repeat the request back at the user.
    """
    import re
    # snappyHexMesh prints a planning table before layer addition and the achieved table after;
    # v2606's final table has a second header line ("target  mesh  [m]  [%]"). Read the last one.
    headers = list(re.finditer(r'^patch\s+faces\s+layers.*$', text, re.M))
    if not headers:
        return None
    patches, achieved_columns = [], False
    for line in text[headers[-1].end():].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(('near-wall', '-----', '[')):
            continue
        if stripped.startswith('target'):
            achieved_columns = True  # columns: patch faces target mesh thickness[m] thickness[%]
            continue
        parts = stripped.split()
        if len(parts) < 3:
            break
        try:
            faces = int(parts[1])
            layers = float(parts[3]) if achieved_columns and len(parts) >= 4 else float(parts[2])
        except ValueError:
            break
        entry = {'patch': parts[0], 'faces': faces, 'layers': layers}
        try:
            if achieved_columns and len(parts) >= 6:
                entry['requested_layers'] = float(parts[2])
                entry['overall_thickness_m'], entry['thickness_fraction'] = float(parts[4]), float(parts[5])/100
            elif len(parts) >= 5:
                entry['near_wall_thickness_m'], entry['overall_thickness_m'] = float(parts[3]), float(parts[4])
        except ValueError:
            pass
        patches.append(entry)
    if not patches:
        return None
    total = sum(p['faces'] for p in patches) or 1
    achieved = sum(p['faces'] * p['layers'] for p in patches) / total
    return {'patches': patches, 'mean_layers': achieved,
            'min_layers': min(p['layers'] for p in patches),
            'faces': total}


# checkMesh -allGeometry flags concave cells on practically every snappyHexMesh mesh (cut cells
# at the surface); with the explicit quality limits passed they are information, not a defect.
ADVISORY_DIAGNOSTICS = ('concave cells',)
# Findings that are information when they touch only a few isolated cells or faces.
ISOLATED_DIAGNOSTICS = ('small determinant', 'small interpolation weight', 'highly skew faces')
ISOLATED_FRACTION = 1e-4


def extended_diagnostics(text):
    """(status, detail) for `checkMesh -allTopology -allGeometry` output: 'unavailable' without a
    log, 'pass' when clean, 'info' when only concave cells or a few isolated cells/faces were
    reported, and 'review' for anything else (orientation, many skewed faces, ...)."""
    import re
    if not text:
        return 'unavailable', 'Extended topology and geometry diagnostics were not run'
    if 'Failed ' not in text:
        return ('pass' if 'Mesh OK.' in text else 'unavailable'), 'Extended topology and geometry diagnostics'
    problems = [line.strip(' *<') for line in text.splitlines() if line.lstrip().startswith('***')]
    total = re.search(r'^\s*cells:\s+(\d+)', text, re.M)
    total = int(total.group(1)) if total else None

    def isolated(problem):
        # A handful of cut cells with a small determinant, low interpolation weight or a skewed
        # boundary face is normal snappyHexMesh output at sharp edges; the explicit limits are
        # checked separately. Only a count at most ISOLATED_FRACTION of the cells is advisory.
        low = problem.lower()
        if not any(k in low for k in ISOLATED_DIAGNOSTICS) or not total:
            return False
        count = re.search(r'number of (?:cells|faces):\s*(\d+)', problem) or re.search(r'(\d+) highly skew faces', problem)
        return bool(count) and int(count.group(1)) <= ISOLATED_FRACTION*total

    advisory = [p for p in problems if any(a in p.lower() for a in ADVISORY_DIAGNOSTICS) or isolated(p)]
    others = [p for p in problems if p not in advisory]
    if problems and not others:
        counts = ', '.join(re.sub(r'\s+', ' ', p) for p in advisory)
        return 'info', (f'Extended diagnostics: {counts}. Concave cells, and a few isolated cells or faces (at most '
                        f'{ISOLATED_FRACTION:.2%} of the cells), are normal where snappyHexMesh cuts cells at the surface; '
                        'the explicit solver mesh-quality limits are checked separately.')
    listed = '; '.join(re.sub(r'\s+', ' ', p) for p in (others or problems)[:4])
    return 'review', f'Extended diagnostics need review: {listed or "see the log"}. Inspect logs/mesh_diagnostics.log.'


def quality_limits(text):
    """(passed, detail) for `checkMesh -meshQuality` against the explicit limits.

    checkMesh's generic geometry test flags any face with skewness above 4, boundary faces
    included, while the explicit limits (meshQualityDict, the same ones snappyHexMesh enforces)
    allow 4 on internal and 20 on boundary faces. When the generic test fails only on skewness and
    the explicit section reports no face in error, the extra faces are boundary faces inside the
    explicit limit, so the limits pass; the detail says so instead of hiding it."""
    import re
    if 'Mesh OK.' in text:
        return True, 'Explicit OpenFOAM mesh-quality limits'
    section = text.split('Checking faces in error', 1)
    counts = [int(n) for n in re.findall(r':\s*(\d+)\s*$', section[1], re.M)] if len(section) == 2 else []
    flagged = [line.strip(' *') for line in text.splitlines() if line.lstrip().startswith('***')]
    if counts and not any(counts) and flagged and all('skew' in line.lower() for line in flagged):
        worst = re.search(r'Max skewness = ([\d.eE+-]+), (\d+) highly skew', text)
        what = f'{worst.group(2)} boundary faces with skewness up to {float(worst.group(1)):.3g}' if worst else 'Some boundary faces'
        return True, (f'Explicit OpenFOAM mesh-quality limits pass. {what} exceed checkMesh\'s generic limit of 4 '
                      'but are within the boundary limit of 20 that snappyHexMesh and the solver template use.')
    return False, 'Explicit OpenFOAM mesh-quality limits'


def mesh_evidence(case, spec, boundary_ok, preview_ok, rotation_ok):
    import re
    primary = (case/'logs/check.log').read_text(errors='replace') if (case/'logs/check.log').exists() else ''
    extended = (case/'logs/mesh_diagnostics.log').read_text(errors='replace') if (case/'logs/mesh_diagnostics.log').exists() else ''
    meshing = (case/'logs/meshing.log').read_text(errors='replace') if (case/'logs/meshing.log').exists() else ''
    cells = re.search(r'\bcells:\s+(\d+)', primary)
    from .foam_elements import needs_toposet
    zoned = needs_toposet(spec)
    findings = [
        {'code':'mesh_quality', 'status':'pass' if quality_limits(primary)[0] else 'fail', 'detail':quality_limits(primary)[1], 'log':'logs/check.log'},
        {'code':'mesh_boundaries', 'status':'pass' if boundary_ok else 'fail', 'detail':'Every configured boundary is present and has faces'},
        {'code':'mesh_preview', 'status':'pass' if preview_ok else 'unavailable', 'detail':'Section of actual volume cells; preview failure does not imply mesh-quality failure'},
        {'code':'mesh_rotation', 'status':('pass' if rotation_ok else 'fail') if zoned else 'info',
         'detail':('Nonempty rotor cell zone' if spec.rotation.enabled and not (spec.elements.rotating_zones or spec.elements.porous) else 'Every rotating and porous zone has cells') if zoned else 'No rotating region requested'},
        {'code':'mesh_extended', 'status':extended_diagnostics(extended)[0], 'detail':extended_diagnostics(extended)[1], 'log':'logs/mesh_diagnostics.log'},
    ]
    coverage = layer_coverage(meshing)
    requested = spec.mesh.layers
    if not requested:
        findings.append({'code':'mesh_layers','status':'info','detail':'No near-wall layers were requested.'})
    elif coverage is None:
        findings.append({'code':'mesh_layers','status':'unavailable',
                         'detail':f'{requested} layers requested; snappyHexMesh reported no layer table, so achieved coverage is unknown.',
                         'log':'logs/meshing.log'})
    else:
        fraction = coverage['mean_layers']/requested if requested else 0
        detail = (f"{requested} layers requested; {coverage['mean_layers']:.2f} achieved on average "
                  f"across {coverage['faces']} wall faces (worst patch {coverage['min_layers']:.2f}).")
        # Layers set the near-wall resolution that wall shear, and therefore drag, depends
        # on, so a shortfall is a review item rather than a note.
        findings.append({'code':'mesh_layers',
                         'status':'pass' if fraction>=0.8 else 'review',
                         'detail':detail if fraction>=0.8 else detail+' Coverage below 80% of the request; wall shear and drag are under-resolved where layers are missing.',
                         'log':'logs/meshing.log'})
    metrics = mesh_metrics(primary)
    budget = spec.mesh.max_cells
    if metrics.get('cells') and budget and metrics['cells'] >= budget*0.98:
        # snappy stops refining at the budget and still reports a valid mesh, so a mesh
        # that ran out of cells looks identical to one that did not need more.
        findings.append({'code':'mesh_budget','status':'review',
                         'detail':f"Reached the {budget:,} cell budget ({metrics['cells']:,} cells); refinement may have stopped before resolving the requested detail."})
    return {'configuration_id':identity(spec), 'cell_count':int(cells.group(1)) if cells else None,
            'findings':findings, 'layer_coverage':coverage, 'mesh_metrics':metrics}
