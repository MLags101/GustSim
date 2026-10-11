"""Accuracy tiers: deterministic mesh and solver settings from the model size.

Basic, Regular and Advanced target a surface cell of about L/100, L/200 and L/400 (L = the
reference length). The resolved numbers, not the tier name, are what the run snapshot and the
configuration identity record. Tiers do not guarantee accuracy; a mesh study does.
"""
import math
import numpy as np
from . import config, db

TIERS = {
    'basic': {'label': 'Basic', 'divisions': 100, 'feature_extra': 1, 'layers': 3, 'max_cells': 2_000_000,
              'wake_boxes': 1, 'wake_offset': 2, 'adaptive_cycles': 0, 'iterations': 2000,
              'description': 'Quick concept comparison. Coarse surface resolution; trends rather than absolute values.'},
    'regular': {'label': 'Regular', 'divisions': 200, 'feature_extra': 1, 'layers': 4, 'max_cells': 8_000_000,
                'wake_boxes': 2, 'wake_offset': 2, 'adaptive_cycles': 0, 'iterations': 4000,
                'description': 'Design work. Finer surface and a nested wake refinement.'},
    'advanced': {'label': 'Advanced', 'divisions': 400, 'feature_extra': 2, 'layers': 5, 'max_cells': 25_000_000,
                 'wake_boxes': 3, 'wake_offset': 2, 'adaptive_cycles': 1, 'iterations': 6000,
                 'description': 'Detailed study. Fine surface, three wake boxes and one adaptive refinement cycle '
                                'where available. Bounded by this computer\'s memory.'},
}
SNAPPY_GB_PER_MILLION = 2.0
MAX_SURFACE_LEVEL = 6


def memory_cells():
    """Largest cell count serial snappyHexMesh can build within the worker memory ceiling."""
    return int(config.MEMORY_GB*0.8/SNAPPY_GB_PER_MILLION*1e6)


def reference_length(spec, meta):
    lo, hi = np.asarray(meta['bounds'])
    return max(float(spec.references.length), float(max(hi-lo))*0.1, 1e-6)


def resolve(spec, tier, meta=None):
    """Return a copy of spec with tier settings applied (tier name kept for display)."""
    if tier == 'custom':
        return spec
    from . import validation
    from .mesh_guidance import recommend
    meta = meta or db.geometry(spec.geometry_id)
    settings = TIERS[tier]
    out = spec.model_copy(deep=True)
    length = reference_length(spec, meta)
    extent = np.asarray(spec.domain.maximum, float) - np.asarray(spec.domain.minimum, float)
    longest = float(extent.max())
    target = length/settings['divisions']
    base_cells = out.mesh.base_cells
    level = math.ceil(math.log2(max(longest/base_cells/target, 1)))
    if level > MAX_SURFACE_LEVEL:
        # Refining the background is cheaper than more than six octree levels.
        base_cells = min(200, math.ceil(longest/(target*2**MAX_SURFACE_LEVEL)))
        level = math.ceil(math.log2(max(longest/base_cells/target, 1)))
    level = int(min(max(level, 1), MAX_SURFACE_LEVEL))
    adaptive = settings['adaptive_cycles'] if 'adaptive_mesh' in validation.COMPILED_FEATURES else 0
    out.mesh = out.mesh.model_copy(update={
        'tier': tier, 'base_cells': int(base_cells), 'surface_level': level,
        'feature_level': int(min(level + settings['feature_extra'], 7)),
        'max_cells': int(min(settings['max_cells'], max(memory_cells(), 100_000))),
        'nested_wake_boxes': settings['wake_boxes'] if spec.mode != 'internal' else 1,
        'wake_level_offset': settings['wake_offset'],
        'adaptive_cycles': adaptive})
    out.mesh.first_layer_m = validation.first_layer_thickness(out)
    out.mesh.layers = settings['layers']
    plan = validation.layer_plan(out)
    if not plan['fits']:
        out.mesh.layers = max(plan['feasible_layers'], 1 if plan['feasible_layers'] else 0)
    # The body box sits one level coarser than the surface unless thin features need more:
    # recommend() never returns less than the surface level, so equality means "not thin".
    thin = recommend(out, meta)['body_level']
    out.mesh.body_level = thin if thin > level else max(level - 1, 1)
    out.solver = out.solver.model_copy(update={'iterations': settings['iterations']})
    out.convergence = out.convergence.model_copy(update={'enabled': True, 'cap': settings['iterations']})
    return out


def apply(spec, meta=None):
    tier = spec.mesh.tier
    return spec if tier == 'custom' else resolve(spec, tier, meta)


def describe():
    return [{'tier': key, 'label': value['label'], 'description': value['description'],
             'surface_cell_fraction': 1/value['divisions'], 'layers': value['layers'], 'iterations': value['iterations'],
             'max_cells': min(value['max_cells'], memory_cells())} for key, value in TIERS.items()]
