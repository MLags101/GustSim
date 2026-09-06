import math
import numpy as np
from . import db
from .geometry import load, point_inside
from .models import SimulationSpec, Boundary

DOMAIN_PATCHES = ["inlet", "outlet", "front", "back", "ground", "top"]

def defaults(identifier):
    meta = db.geometry(identifier)
    lo, hi = np.asarray(meta["bounds"])
    length = max(float(max(hi-lo)), 0.01)
    low = lo - length*np.array([3,3,3])
    high = hi + length*np.array([8,3,3])
    spec = SimulationSpec(geometry_id=identifier, name=meta["filename"].rsplit('.',1)[0]+" · baseline")
    spec.domain.minimum=tuple(low)
    spec.domain.maximum=tuple(high)
    spec.domain.fluid_point=tuple(low+length*0.5)
    spec.flow.length_scale=length*0.05
    spec.references.area=float(max((hi[1]-lo[1])*(hi[2]-lo[2]),1e-6))
    spec.references.length=length
    spec.mesh.first_layer_m=length*0.001
    spec.boundaries=[Boundary(patch="inlet",kind="velocity_inlet"),Boundary(patch="outlet",kind="pressure_outlet")]
    spec.boundaries += [Boundary(patch=p,kind="freestream") for p in DOMAIN_PATCHES[2:]]
    spec.boundaries += [Boundary(patch=p["name"],kind="wall") for p in meta["patches"]]
    return spec

def validate(spec: SimulationSpec):
    meta = db.geometry(spec.geometry_id)
    errors, warnings = [], []
    patches = {p["name"] for p in meta["patches"]}
    expected = patches | (set(DOMAIN_PATCHES) if spec.mode != "internal" else set())
    assigned = {b.patch for b in spec.boundaries}
    if assigned != expected:
        errors.append("Boundary assignments must match the geometry and domain. Missing: " + ", ".join(sorted(expected-assigned)) + "; unknown: " + ", ".join(sorted(assigned-expected)))
    if patches & set(DOMAIN_PATCHES):
        errors.append("Geometry surface names conflict with reserved domain names; rename these surfaces")
    if not meta["units_confirmed"]:
        errors.append("Confirm model units before meshing")
    diag = meta["diagnostics"]
    if not diag["watertight"] or diag["nonmanifold_edges"] or diag["degenerate_faces"]:
        errors.append("Prepare a closed, manifold surface without degenerate triangles before meshing")
    if not diag["winding_consistent"]:
        errors.append("Surface normals are inconsistent; use conservative repair")
    if meta.get("cad_valid") is False:
        errors.append("CAD solid validity check failed")
    warnings.append("Triangle self-intersections are not certified; inspect the geometry and generated mesh")
    kinds={b.kind for b in spec.boundaries}
    if not (kinds & {"velocity_inlet","flow_inlet","pressure_inlet","freestream"}) or "pressure_outlet" not in kinds:
        errors.append("Assign an inlet and at least one pressure outlet")
    low,high=np.asarray(spec.domain.minimum),np.asarray(spec.domain.maximum)
    bounds=np.asarray(meta["bounds"])
    if np.any(bounds[0]<=low) or np.any(bounds[1]>=high):
        errors.append("The background domain must enclose all geometry with a positive margin")
    mesh,groups=load(spec.geometry_id)
    if diag["watertight"]:
        inside=point_inside(mesh,spec.domain.fluid_point)
        if spec.mode=="internal" and not inside:
            errors.append("Internal fluid point must lie inside the prepared closed fluid envelope")
        if spec.mode!="internal" and inside:
            errors.append("External fluid point lies inside the solid; move it into the surrounding fluid")
    if spec.mode=="internal" and diag["components"] != 1:
        errors.append("Internal mode requires one connected closed fluid envelope; isolate inner walls and cap the openings")
    r=spec.rotation
    if r.enabled:
        if not set(r.patches+r.stationary_patches)<=patches:
            errors.append("Rotation references unknown surfaces")
        for b in spec.boundaries:
            if b.patch in r.patches and b.kind not in {"wall"}:
                errors.append("MRF rotor surfaces must use wall boundaries")
        selected=[p["group"] for p in meta["patches"] if p["name"] in r.patches]
        vertices=mesh.vertices[np.unique(mesh.faces[np.isin(groups,selected)])]
        if len(vertices):
            delta=vertices-np.asarray(r.origin)
            axial=delta@np.asarray(r.axis)
            radial=np.linalg.norm(delta-np.outer(axial,r.axis),axis=1)
            if np.max(radial)>=r.radius or np.max(np.abs(axial))>=r.length/2:
                errors.append("The MRF cylinder must fully enclose every selected rotor surface")
            if np.max(radial)>r.blade_radius*1.01:
                errors.append("Blade radius is smaller than the selected rotor geometry")
        cylinder_extent=np.abs(r.axis)*r.length/2+r.radius*np.sqrt(np.maximum(0,1-np.square(r.axis)))
        if np.any(np.asarray(r.origin)-cylinder_extent<=low) or np.any(np.asarray(r.origin)+cylinder_extent>=high):
            errors.append("MRF cylinder must remain inside the background domain")
        warnings.append("Steady MRF freezes rotor position; blade-passing loads and transient interactions are unresolved")
    tip=2*math.pi*abs(r.rpm)/60*r.blade_radius if r.enabled else 0
    max_speed=spec.flow.speed+tip
    if spec.fluid.name=="air" and max_speed/spec.fluid.speed_of_sound>=0.3:
        errors.append("Estimated inlet plus blade-tip Mach number exceeds 0.3; compressible flow is outside this solver template")
    if spec.flow.turbulence=="kOmegaSST":
        warnings.append("Fully turbulent k–ω SST; transition is not modeled. Check y+ after the run")
    if spec.mesh.wall_treatment=="resolved" and spec.mesh.layers<5:
        warnings.append("Wall-resolved treatment usually requires more layers; verify first-cell y+ and layer coverage")
    if spec.flow.speed==0:
        warnings.append("Freestream force coefficients are undefined at zero reference speed")
    return {"valid":not errors,"errors":errors,"warnings":warnings,
            "reynolds":spec.fluid.density*max_speed*spec.references.length/spec.fluid.dynamic_viscosity,
            "estimated_mach":max_speed/spec.fluid.speed_of_sound,"validation_status":"experimental_validation_pending"}
