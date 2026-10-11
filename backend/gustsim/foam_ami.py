"""Sliding-mesh rotor: one cylindrical cyclicAMI interface and solid-body motion.

MRF is not written. The rotor cell zone still exists so the mesh check can see it, and its
boundary faces become the AMI pair. Experimental; the transient pptc gate has not been run.
"""
import math
import re

from .foam import vector
from .foam_elements import zone_cylinder, zone_names


def face_actions(spec):
    """topoSet actions that leave the boundary faces of each rotating cell set."""
    actions = []
    for zone in spec.rotating_zones():
        cells, cell_zone = zone_names(zone)[1], zone_names(zone)[2]
        a, b = zone_cylinder(zone)
        actions.append(
            f"{{name {cell_zone}Boundary; type faceSet; action new; source cellToFace; set {cells}; option all;}}")
        actions.append(
            f"{{name {cell_zone}Boundary; type faceSet; action delete; source cellToFace; set {cells}; option both;}}")
        actions.append(
            f"{{name {cell_zone}Interface; type faceZoneSet; action new; source setToFaceZone; faceSet {cell_zone}Boundary;}}")
        # point1/point2 are recorded so a reader can see which cylinder the faces came from.
        actions[-1] += f" // {vector(a)} {vector(b)} r {zone.radius:.6g}"
    return actions


def prepare(spec, folder):
    """Baffle dictionary, solid-body motion, and a transient pimpleFoam control file."""
    from . import transient
    zone = spec.rotating_zones()[0]
    _, _, cell_zone, _ = zone_names(zone)
    omega = zone.rpm * 2 * math.pi / 60
    (folder / 'system' / 'createBafflesDict').write_text(
        'FoamFile { version 2.0; format ascii; class dictionary; object createBafflesDict; }\n'
        'internalFacesOnly true;\nbaffles\n{\n'
        f'    {cell_zone}Interface\n    {{\n        type faceZone;\n        zoneName {cell_zone}Interface;\n'
        '        patches\n        {\n'
        f'            master {{ name amiInner; type cyclicAMI; neighbourPatch amiOuter; transform noOrdering; }}\n'
        f'            slave {{ name amiOuter; type cyclicAMI; neighbourPatch amiInner; transform noOrdering; }}\n'
        '        }\n    }\n}\n', encoding='utf-8', newline='\n')
    (folder / 'constant' / 'dynamicMeshDict').write_text(
        'FoamFile { version 2.0; format ascii; class dictionary; object dynamicMeshDict; }\n'
        'dynamicFvMesh dynamicMotionSolverFvMesh;\nmotionSolverLibs (fvMotionSolvers);\n'
        f'motionSolver solidBody;\ncellZone {cell_zone};\nsolidBodyMotionFunction rotatingMotion;\n'
        f'origin {vector(zone.origin)};\naxis {vector(zone.axis)};\nomega constant {omega:.8g};\n',
        encoding='utf-8', newline='\n')
    times = transient.plan(spec, 0)
    (folder / 'results').mkdir(parents=True, exist_ok=True)
    (folder / 'results' / 'transient_plan.json').write_text(__import__('json').dumps(times, indent=2), encoding='utf-8')
    transient.write_schemes(spec, folder)
    transient.write_solution(spec, folder)
    control = (folder / 'system' / 'controlDict').read_text(encoding='utf-8')
    control = re.sub(r'application\s+\w+;', 'application pimpleFoam;', control, count=1)
    control = re.sub(r'startFrom\s+\w+;', 'startFrom startTime;', control, count=1)
    control = re.sub(r'endTime\s+[^;]+;', f'endTime {times["end"]:.8g};', control, count=1)
    control = re.sub(r'deltaT\s+[^;]+;', f'deltaT {times["first_step_s"]:.8g};', control, count=1)
    if 'adjustTimeStep' not in control:
        control = control.replace('deltaT', f'adjustTimeStep yes; maxCo {times["max_courant"]:g}; maxDeltaT {times["max_step_s"]:.8g};\ndeltaT', 1)
    (folder / 'system' / 'controlDict').write_text(control, encoding='utf-8', newline='\n')
    return {'zone': cell_zone, 'omega_rad_s': omega, 'duration_s': times['duration_s']}
