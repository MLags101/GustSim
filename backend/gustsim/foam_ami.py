"""Sliding-mesh rotor: one cylindrical cyclicAMI interface and solid-body motion.

MRF is not written. snappyHexMesh snaps the rotor cylinder as a faceZone (the interface) and a
cellZone (the rotating cells), so the interface is a true surface of revolution. The worker then
runs createBaffles to split the faceZone into a cyclicAMI pair and `mergeOrSplitBaffles -split`
so the rotating and stationary sides no longer share points. The rotor walls move with the mesh
(movingWallVelocity). Experimental; the transient pptc gate has not been run.
"""
import json
import math

from .foam import set_patch_field, vector
from .foam_elements import zone_names


def interface_name(zone):
    return zone_names(zone)[2] + 'Interface'


def zone_surfaces(spec):
    """snappyHexMesh refinementSurfaces entries that snap each rotor cylinder as a zone pair."""
    level = spec.mesh.surface_level
    out = []
    for zone in spec.rotating_zones():
        region, _, cell_zone, _ = zone_names(zone)
        out.append(f'{region} {{ level ({level} {level}); faceZone {interface_name(zone)}; '
                   f'cellZone {cell_zone}; cellZoneInside inside; }}')
    return out


def prepare(spec, folder, boundaries=None):
    """Baffle dictionary, solid-body motion, moving rotor walls and the transient control file."""
    from . import transient
    zone = spec.rotating_zones()[0]
    _, _, cell_zone, _ = zone_names(zone)
    omega = zone.rpm * 2 * math.pi / 60
    interface = interface_name(zone)
    (folder / 'system' / 'createBafflesDict').write_text(
        'FoamFile { version 2.0; format ascii; class dictionary; object createBafflesDict; }\n'
        'internalFacesOnly true;\nbaffles\n{\n'
        f'    {interface}\n    {{\n        type faceZone;\n        zoneName {interface};\n'
        '        patches\n        {\n'
        '            master { name amiInner; type cyclicAMI; neighbourPatch amiOuter; transform noOrdering; lowWeightCorrection 0.2; }\n'
        '            slave { name amiOuter; type cyclicAMI; neighbourPatch amiInner; transform noOrdering; lowWeightCorrection 0.2; }\n'
        '        }\n    }\n}\n', encoding='utf-8', newline='\n')
    (folder / 'constant' / 'dynamicMeshDict').write_text(
        'FoamFile { version 2.0; format ascii; class dictionary; object dynamicMeshDict; }\n'
        'dynamicFvMesh dynamicMotionSolverFvMesh;\nmotionSolverLibs (fvMotionSolvers);\n'
        f'motionSolver solidBody;\ncellZone {cell_zone};\nsolidBodyMotionFunction rotatingMotion;\n'
        f'origin {vector(zone.origin)};\naxis {vector(zone.axis)};\nomega constant {omega:.8g};\n',
        encoding='utf-8', newline='\n')
    # MRF made the rotor walls rotate implicitly. With a moving mesh they are no-slip walls that
    # move with the mesh; a stationary fixedValue (0 0 0) would hold the fluid still on the blades.
    kinds = {b.patch: b.kind for b in (boundaries or spec.boundaries)}
    for patch in zone.patches:
        if kinds.get(patch) in ('wall', 'moving_wall', 'rotating_wall'):
            set_patch_field(folder, 'U', patch, 'type movingWallVelocity; value uniform (0 0 0);')
    times = transient.plan(spec, 0)
    center = None
    meta_path = folder / 'geometry.json'
    if meta_path.is_file():
        bounds = json.loads(meta_path.read_text(encoding='utf-8')).get('bounds')
        if bounds:
            center = [(a + b) / 2 for a, b in zip(*bounds)]
    transient.write_schemes(spec, folder)
    transient.write_solution(spec, folder)
    # The same transient control as a steady-start transient run: adaptive time step, writes at
    # physical-time intervals (not every 100 steps), averaging window and animation frames.
    transient.write_control(spec, folder, times, center)
    return {'zone': cell_zone, 'omega_rad_s': omega, 'duration_s': times['duration_s']}
