"""Shared configuration identity and evidence-based readiness (no mutable study state)."""
import hashlib
import json
from .models import SimulationSpec


def identity(spec):
    data = SimulationSpec.model_validate(spec).model_dump(mode='json') if isinstance(spec, dict) else spec.model_dump(mode='json')
    for key in ('name', 'solver'): data.pop(key, None)
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


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
    checks = [
        ('units', meta['units_confirmed'], 'Model dimensions and units are available in metres'),
        ('closed_surface', d['watertight'], 'Surface must be closed; isolate inner walls and confirm planar caps for a pipe'),
        ('manifold', not d['nonmanifold_edges'], 'No nonmanifold edges'),
        ('triangles', not d['degenerate_faces'], 'No degenerate triangles; Auto prepare can repair them'),
        ('normals', d['winding_consistent'], 'Consistent surface normals'),
        ('cad_validity', meta.get('cad_valid') is not False, 'Imported CAD validity check must not fail'),
    ]
    return [{'code': code, 'stage': 'geometry', 'control': 'geometry-diagnostics', 'status': 'pass' if ok else 'fail', 'detail': detail} for code,ok,detail in checks]


def enrich(report, spec, meta):
    findings = geometry_findings(meta)
    for i, message in enumerate(report['errors']):
        control = 'rotating-region' if any(s in message for s in ('MRF', 'Rotor', 'rotor', 'Rotation', 'Blade')) else 'boundary-conditions' if 'oundar' in message or 'inlet' in message else 'domain-fluid-region'
        findings.append({'code': f'setup_{i}', 'stage': 'setup', 'control': control, 'status': 'fail', 'detail': message})
    for i, message in enumerate(report['warnings']):
        informational = any(s in message for s in ('self-intersections', 'Steady MRF', 'Fully turbulent', 'undefined at zero'))
        findings.append({'code': f'warning_{i}', 'stage': 'setup', 'control': 'reference-quantities' if 'coefficients' in message else 'mesh-resolution', 'status': 'info' if informational else 'review', 'detail': message})
    report.update(findings=findings, configuration_id=identity(spec), geometry_ready=all(f['status']=='pass' for f in findings if f['stage']=='geometry'))
    return report


def mesh_evidence(case, spec, boundary_ok, preview_ok, rotation_ok):
    import re
    primary = (case/'logs/check.log').read_text(errors='replace') if (case/'logs/check.log').exists() else ''
    extended = (case/'logs/mesh_diagnostics.log').read_text(errors='replace') if (case/'logs/mesh_diagnostics.log').exists() else ''
    cells = re.search(r'\bcells:\s+(\d+)', primary)
    findings = [
        {'code':'mesh_quality', 'status':'pass' if 'Mesh OK.' in primary else 'fail', 'detail':'Explicit OpenFOAM mesh-quality limits', 'log':'logs/check.log'},
        {'code':'mesh_boundaries', 'status':'pass' if boundary_ok else 'fail', 'detail':'Every configured boundary is present and has faces'},
        {'code':'mesh_preview', 'status':'pass' if preview_ok else 'unavailable', 'detail':'Section of actual volume cells; preview failure does not imply mesh-quality failure'},
        {'code':'mesh_rotation', 'status':('pass' if rotation_ok else 'fail') if spec.rotation.enabled else 'info', 'detail':'Nonempty rotor cell zone' if spec.rotation.enabled else 'No rotating region requested'},
        {'code':'mesh_extended', 'status':'review' if 'Failed ' in extended else 'pass' if 'Mesh OK.' in extended else 'unavailable', 'detail':'Extended topology and geometry diagnostics', 'log':'logs/mesh_diagnostics.log'},
        {'code':'mesh_layers', 'status':'info', 'detail':f'{spec.mesh.layers} layers requested. Quantitative achieved coverage unavailable.'},
    ]
    return {'configuration_id':identity(spec), 'cell_count':int(cells.group(1)) if cells else None, 'findings':findings, 'layer_coverage':None}
