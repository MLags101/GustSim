import math
import numpy as np
from . import db
from .geometry import load, point_inside, projected_area
from .models import SimulationSpec, Boundary

DOMAIN_PATCHES = ["inlet", "outlet", "front", "back", "ground", "top"]

TARGET_YPLUS = {"wall_function": 50.0, "resolved": 1.0}


def skin_friction(turbulence, reynolds):
    """Local flat-plate skin friction at the reference length.

    Laminar flow uses the Blasius value 0.664 Re^-0.5. Turbulent flow uses the
    1/7-power local value 0.058 Re^-0.2. These estimate wall shear for a y+ target.
    They do not predict transition, and a laminar run keeps the laminar value even
    when the Reynolds number is high.
    """
    if turbulence == "laminar":
        return 0.664*reynolds**-0.5
    return 0.058*reynolds**-0.2


def first_layer_thickness(spec, reference_speed=None):
    """First prism-layer thickness for the configured wall treatment, in metres.

    Estimates wall shear from the local flat-plate skin friction, then inverts
    y+ = rho u_tau y / mu. The thickness changes with speed, fluid, turbulence
    treatment and the requested wall treatment.

    This sizes the first cell; it does not guarantee the achieved y+, which is only
    known after solving.
    """
    speed = reference_speed if reference_speed is not None else spec.flow.speed
    if spec.rotation.enabled:
        # A static rotor has no freestream, so the blade tip sets the near-wall flow.
        tip = 2*math.pi*abs(spec.rotation.rpm)/60*max(spec.rotation.blade_radius, 1e-9)
        speed = max(speed, tip)
    length = max(spec.references.length, 1e-9)
    density, viscosity = spec.fluid.density, spec.fluid.dynamic_viscosity
    target = TARGET_YPLUS.get(spec.mesh.wall_treatment, 50.0)
    reynolds = density*speed*length/viscosity
    if not np.isfinite(reynolds) or reynolds <= 1:
        return length*0.001
    friction = skin_friction(spec.flow.turbulence, reynolds)
    wall_shear = 0.5*density*speed*speed*friction
    if wall_shear <= 0:
        return length*0.001
    u_tau = math.sqrt(wall_shear/density)
    centroid = target*viscosity/(density*u_tau)
    # y+ is defined at the cell centre, so the layer is twice that height.
    thickness = 2*centroid
    # Keep it physically sensible relative to the model.
    return float(min(max(thickness, length*1e-6), length*0.05))


def background_cell_size(spec):
    """Edge length of the blockMesh cells, matching the case compiler.

    Counts follow snappy's background: at least four cells on each axis, otherwise
    `base_cells` along the longest side. The returned edge is the smallest of the
    three, which is the tightest cell a prism stack could be asked to fit.
    """
    extent = np.asarray(spec.domain.maximum, dtype=float) - np.asarray(spec.domain.minimum, dtype=float)
    longest = float(np.max(extent))
    if longest <= 0:
        return 0.0
    counts = np.maximum(4, np.ceil(spec.mesh.base_cells * extent / longest))
    return float(np.min(extent / counts))


def layer_stack_thickness(first, ratio, count):
    if count <= 0:
        return 0.0
    if abs(ratio - 1) < 1e-9:
        return first * count
    return first * (ratio**count - 1) / (ratio - 1)


def layer_plan(spec):
    """Whether the requested prism stack can fit in the castellated surface cell.

    snappyHexMesh grows layers into that cell and drops them when the stack, or the
    outer layer, is thicker than the cell. Requested layers are still not achieved
    layers: this only says the request is geometrically possible.
    """
    background = background_cell_size(spec)
    level = max(int(spec.mesh.surface_level), int(spec.mesh.body_level or 0))
    surface = background / 2**level if background else 0.0
    first = float(spec.mesh.first_layer_m)
    ratio = float(spec.mesh.expansion_ratio)
    requested = int(spec.mesh.layers)
    # The outer layer has to transition into the hex it displaces. Half the cell
    # matches the case's maxFaceThicknessRatio of 0.5; the whole stack also has to
    # stay inside that cell.
    outer_limit = 0.5 * surface
    feasible = 0
    total = 0.0
    for index in range(requested):
        thick = first * (ratio ** index)
        if surface <= 0 or thick > outer_limit or total + thick > surface:
            break
        total += thick
        feasible += 1
    requested_stack = layer_stack_thickness(first, ratio, requested)
    fits = feasible == requested
    if requested == 0:
        detail = "No near-wall layers were requested."
    elif fits:
        detail = (f"{requested} requested layers fit inside the {surface*1000:.3g} mm surface cell "
                  f"(stack {requested_stack*1000:.3g} mm). This is a geometric check; achieved coverage is measured after meshing.")
    else:
        detail = (f"Requested {requested} near-wall layers do not fit in the {surface*1000:.3g} mm surface cell "
                  f"(stack {requested_stack*1000:.3g} mm). About {feasible} layers fit before the stack exceeds the cell "
                  "snappyHexMesh grows into. Reduce the layer count or refine the surface. Achieved coverage is still measured after meshing.")
    return {"background_cell_m": background, "surface_cell_m": surface, "refinement_level": level,
            "requested_layers": requested, "feasible_layers": feasible, "fits": fits,
            "requested_stack_m": requested_stack, "feasible_stack_m": total, "detail": detail}


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
    # True silhouette along the reference travel direction, not the YZ bounding-box face:
    # a box face is only correct when the flow runs along +X and ignores the actual shape.
    mesh,_=load(identifier)
    spec.references.area=float(max(projected_area(mesh,spec.references.drag_axis),1e-6))
    spec.references.length=length
    spec.mesh.first_layer_m=first_layer_thickness(spec)
    spec.boundaries=[Boundary(patch="inlet",kind="velocity_inlet"),Boundary(patch="outlet",kind="pressure_outlet")]
    spec.boundaries += [Boundary(patch=p,kind="freestream") for p in DOMAIN_PATCHES[2:]]
    spec.boundaries += [Boundary(patch=p["name"],kind="wall") for p in meta["patches"]]
    from .mesh_guidance import recommend
    spec.mesh.body_level=recommend(spec,meta)['body_level']
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
    # Graded geometry gate. snappyHexMesh needs a surface that separates inside from
    # outside; it does not need a perfectly manifold one. A wrapped revision is closed by
    # construction, so it meshes -- carrying an explicit approximation label instead of a
    # hard block, which is what left real assemblies permanently unmeshable.
    wrapped = meta.get("geometry_fidelity") == "wrapped"
    if not diag["watertight"]:
        errors.append("Prepare a closed surface before meshing. Auto prepare fixes simple defects; "
                      "for an assembly whose parts touch or overlap, use Wrap to close it.")
    elif diag["degenerate_faces"]:
        errors.append("Remove degenerate triangles before meshing; Auto prepare repairs them")
    elif diag["nonmanifold_edges"] and not wrapped:
        # Closed but non-manifold: meshable, and worth flagging rather than blocking.
        warnings.append(f"{diag['nonmanifold_edges']} non-manifold edges remain where parts meet. "
                        "The surface is closed so meshing can proceed, but check that the mesh "
                        "kept those junctions, or use Wrap for a single clean surface.")
    if not diag["winding_consistent"]:
        errors.append("Surface normals are inconsistent; use conservative repair")
    if wrapped:
        info = meta.get("wrap") or {}
        resolution = info.get("wrap_resolution_m")
        warnings.append(
            "Geometry is a shrink-wrapped approximation"
            + (f" on a {resolution*1000:.3g} mm grid" if resolution else "")
            + ". Features thinner than that are not represented and narrow openings are sealed. "
              "Loads are computed on the wrap, not on the original CAD.")
    if meta.get("cad_valid") is False:
        errors.append("CAD solid validity check failed")
    warnings.append("Triangle self-intersections are not certified; inspect the geometry and generated mesh")
    kinds={b.kind for b in spec.boundaries}
    if not (kinds & {"velocity_inlet","flow_inlet","pressure_inlet","freestream"}) or not ("pressure_outlet" in kinds or spec.mode != 'internal' and 'freestream' in kinds):
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
    if spec.use_case:
        if not spec.use_case.confirmed:
            errors.append('Confirm inferred dimensions, fluid point, and reference directions in the guided setup preview')
        if not set(spec.use_case.force_patches) <= patches:
            errors.append('Force selection references unknown surfaces; reselect the components')
        if any(b.patch in spec.use_case.force_patches and b.kind not in {'wall','moving_wall','rotating_wall'} for b in spec.boundaries):
            errors.append('Force-bearing surfaces must have wall boundary conditions')
        if spec.use_case.kind == 'pipe' and (spec.mode != 'internal' or sum(b.kind in {'velocity_inlet','flow_inlet','pressure_inlet'} for b in spec.boundaries)!=1 or sum(b.kind=='pressure_outlet' for b in spec.boundaries)!=1):
            errors.append('Guided pipe analysis requires one inlet and one outlet in internal mode')
    r=spec.rotation
    if r.enabled:
        if not set(r.patches+r.stationary_patches)<=patches:
            errors.append("Rotation references unknown surfaces")
        parents = {p.get('parent_id') for p in meta.get('parts', [])}
        for part in meta.get('parts', []):
            chosen = set(part['patches']) & set(r.patches)
            if part.get('id') not in parents and chosen and chosen != set(part['patches']):
                errors.append('Rotor selection includes only part of component '+part['name']+'; select all its surfaces')
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
    if spec.use_case and spec.use_case.kind=='aerodynamics':
        # Catch a travel direction edited after the area was confirmed: the stored area
        # would still describe the old direction and silently bias Cd.
        silhouette=projected_area(mesh,spec.references.drag_axis)
        if silhouette>0 and abs(spec.references.area-silhouette)/silhouette>0.05:
            warnings.append(f"Reference area {spec.references.area:.4g} m² differs from the frontal area "
                            f"projected along the current travel direction ({silhouette:.4g} m²); "
                            "confirm which area the drag coefficient should use")
    plan = layer_plan(spec)
    if spec.mesh.layers and not plan["fits"]:
        warnings.append(plan["detail"])
    from .workflow import enrich
    report = enrich({"valid":not errors,"errors":errors,"warnings":warnings,
            "reynolds":spec.fluid.density*max_speed*spec.references.length/spec.fluid.dynamic_viscosity,
            "estimated_mach":max_speed/spec.fluid.speed_of_sound,"validation_status":"experimental_validation_pending"}, spec, meta)
    report["layer_plan"] = plan
    return report
