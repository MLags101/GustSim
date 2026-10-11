"""Sample models for a first simulation without a CAD file.

Generated parametrically (no files shipped) and stored as ordinary immutable revisions. An
optional GUSTSIM_SAMPLES_DIR adds the user's own STEP/STL files to the list; they are read,
never modified.
"""
import os
from pathlib import Path
import numpy as np
import trimesh
from . import geometry

SAMPLES = {
    'capsule': {'name': 'Streamlined capsule', 'use_case': 'aerodynamics', 'speed': 20.0,
                'description': 'A 1 m rounded body travelling along +X. Low drag; a quick first external-flow run.'},
    'block': {'name': 'Bluff block', 'use_case': 'aerodynamics', 'speed': 20.0,
              'description': 'A 1.0 × 0.4 × 0.3 m box. Sharp edges make separation and a large wake easy to see.'},
    'sphere': {'name': 'Sphere', 'use_case': 'aerodynamics', 'speed': 10.0,
               'description': 'A 0.2 m sphere: the classic bluff-body case for comparing Cd with textbook values.'},
    'pipe': {'name': 'Straight pipe', 'use_case': 'pipe', 'speed': 1.0,
             'description': 'A closed 50 mm × 0.5 m fluid volume with planar end caps for internal flow.'},
}


def external_dir():
    folder = os.environ.get('GUSTSIM_SAMPLES_DIR')
    path = Path(folder) if folder else None
    return path if path and path.is_dir() else None


def listing():
    items = [{'id': key, **value, 'source': 'generated'} for key, value in SAMPLES.items()]
    folder = external_dir()
    if folder:
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in geometry.IMPORT_SUFFIXES and path.stat().st_size <= 100*1024*1024:
                items.append({'id': 'file:' + path.name, 'name': path.stem, 'use_case': 'aerodynamics', 'speed': 20.0,
                              'description': f'{path.suffix[1:].upper()} file from the samples folder', 'source': 'file'})
    return items


def mesh_for(key):
    if key == 'capsule':
        mesh = trimesh.creation.capsule(height=0.7, radius=0.15, count=[48, 24])
        mesh.apply_transform(trimesh.geometry.align_vectors([0, 0, 1], [1, 0, 0]))
        mesh.apply_scale([1.0, 1.0, 0.8])
    elif key == 'block':
        mesh = trimesh.creation.box(extents=(1.0, 0.4, 0.3))
    elif key == 'sphere':
        mesh = trimesh.creation.icosphere(subdivisions=4, radius=0.1)
    elif key == 'pipe':
        mesh = trimesh.creation.cylinder(radius=0.025, height=0.5, sections=64)
        mesh.apply_transform(trimesh.geometry.align_vectors([0, 0, 1], [1, 0, 0]))
    else:
        raise KeyError('Sample not found')
    return mesh


def create(key):
    if key.startswith('file:'):
        folder = external_dir()
        name = Path(key[5:]).name
        if folder is None or not (folder/name).is_file():
            raise KeyError('Sample not found')
        return geometry.import_file(name, (folder/name).read_bytes(), units='mm')
    mesh = mesh_for(key)
    groups = geometry.connected_groups(mesh, 35 if key in ('block', 'pipe') else 60)
    prefix = {'capsule': 'capsule', 'block': 'block', 'sphere': 'sphere', 'pipe': 'pipe'}[key]
    names = {int(g): f'{prefix}_{int(g)}' for g in np.unique(groups)}
    return geometry.save(mesh, groups, names, f'{SAMPLES[key]["name"]}.stl', confirmed=True,
                         operations=[{'sample': key}], extra={'sample': key})
