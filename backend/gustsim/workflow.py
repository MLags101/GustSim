"""Shared configuration identity and evidence-based readiness (no mutable study state)."""
import hashlib
import json
from .models import SimulationSpec


def identity(spec):
    data = SimulationSpec.model_validate(spec).model_dump(mode='json') if isinstance(spec, dict) else spec.model_dump(mode='json')
    for key in ('name', 'solver', 'project_id'): data.pop(key, None)
    # JavaScript JSON drops the sign of zero. It does not change the physics.
    def canonical(value):
        if isinstance(value,dict):return {k:canonical(v) for k,v in value.items()}
        if isinstance(value,list):return [canonical(v) for v in value]
        return 0.0 if isinstance(value,float) and value==0 else value
    return hashlib.sha256(json.dumps(canonical(data), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def compatible(source, spec):
    return identity(source) == identity(spec)


def require_mesh(source, spec):
    if source['kind'] != 'mesh' or source['status'] != 'completed':
        raise ValueError('Select a completed mesh attempt')
    if not compatible(source['spec'], spec):
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
    header = re.search(r'^patch\s+faces\s+layers.*$', text, re.M)
    if not header:
        return None
    patches = []
    for line in text[header.end():].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(('near-wall', '-----')):
            continue
        parts = stripped.split()
        if len(parts) < 3:
            break
        try:
            faces, layers = int(parts[1]), float(parts[2])
        except ValueError:
            break
        entry = {'patch': parts[0], 'faces': faces, 'layers': layers}
        if len(parts) >= 5:
            try: entry['near_wall_thickness_m'], entry['overall_thickness_m'] = float(parts[3]), float(parts[4])
            except ValueError: pass
        patches.append(entry)
    if not patches:
        return None
    total = sum(p['faces'] for p in patches) or 1
    achieved = sum(p['faces'] * p['layers'] for p in patches) / total
    return {'patches': patches, 'mean_layers': achieved,
            'min_layers': min(p['layers'] for p in patches),
            'faces': total}


def mesh_evidence(case, spec, boundary_ok, preview_ok, rotation_ok):
    import re
    primary = (case/'logs/check.log').read_text(errors='replace') if (case/'logs/check.log').exists() else ''
    extended = (case/'logs/mesh_diagnostics.log').read_text(errors='replace') if (case/'logs/mesh_diagnostics.log').exists() else ''
    meshing = (case/'logs/meshing.log').read_text(errors='replace') if (case/'logs/meshing.log').exists() else ''
    cells = re.search(r'\bcells:\s+(\d+)', primary)
    findings = [
        {'code':'mesh_quality', 'status':'pass' if 'Mesh OK.' in primary else 'fail', 'detail':'Explicit OpenFOAM mesh-quality limits', 'log':'logs/check.log'},
        {'code':'mesh_boundaries', 'status':'pass' if boundary_ok else 'fail', 'detail':'Every configured boundary is present and has faces'},
        {'code':'mesh_preview', 'status':'pass' if preview_ok else 'unavailable', 'detail':'Section of actual volume cells; preview failure does not imply mesh-quality failure'},
        {'code':'mesh_rotation', 'status':('pass' if rotation_ok else 'fail') if spec.rotation.enabled else 'info', 'detail':'Nonempty rotor cell zone' if spec.rotation.enabled else 'No rotating region requested'},
        {'code':'mesh_extended', 'status':'review' if 'Failed ' in extended else 'pass' if 'Mesh OK.' in extended else 'unavailable', 'detail':'Extended topology and geometry diagnostics', 'log':'logs/mesh_diagnostics.log'},
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
