"""Bounded local refinement advice for thin geometry; never an accuracy claim."""
import math
import numpy as np
from . import geometry


def recommend(spec, meta):
    mesh,_=geometry.load(meta['id'])
    size=np.asarray(meta['dimensions'])
    thickness=2*abs(mesh.volume)/mesh.area if meta['diagnostics']['watertight'] and mesh.area else min(size)
    target=max(min(min(size)/6,thickness/2),max(size)*1e-5)
    base=max(np.asarray(spec.domain.maximum)-np.asarray(spec.domain.minimum))/spec.mesh.base_cells
    level=max(spec.mesh.surface_level,min(10,math.ceil(math.log2(base/target))))
    # Bound the estimated local box workload, leaving room for transition cells.
    while level>spec.mesh.surface_level:
        h=base/2**level
        estimate=float(np.prod(size+4*h)/h**3)
        if estimate < spec.mesh.max_cells*.35:break
        level-=1
    return {'body_level':level,'target_cell_m':base/2**level,
            'detail':'Local refinement resolves the object before surface fitting. Small tips, gaps and layers still need inspection.'}
