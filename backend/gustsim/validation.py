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
        # The tessellated surface is what meshes; an invalid B-Rep is a review item here,
        # exactly as in the geometry readiness checklist.
        warnings.append("Open CASCADE reported an invalid CAD solid; inspect the tessellated surface and the generated mesh")
    warnings.append("Triangle self-intersections are not certified; inspect the geometry and generated mesh")
    kinds={b.kind for b in spec.boundaries}
    if not (kinds & {"velocity_inlet","flow_inlet","pressure_inlet","freestream"}) or not ("pressure_outlet" in kinds or spec.mode != 'internal' and 'freestream' in kinds):
        errors.append("Assign an inlet and at least one pressure outlet")
    low,high=np.asarray(spec.domain.minimum),np.asarray(spec.domain.maximum)
    bounds=np.asarray(meta["bounds"])
    # On a ground plane the model may touch or sink slightly into the bottom face (wheel
    # contact patches); every other face needs a positive margin.
    below=bounds[0]<=low
    above=bounds[1]>=high
    if spec.environment.ground!="none":below[2]=False
    # A body may pass through symmetry faces: a quasi-2D section (e.g. a cylinder spanning the
    # tunnel between two symmetry planes). Loads are then per modelled span.
    symmetry={b.patch for b in spec.boundaries if b.kind=="symmetry"}
    crossed=[]
    for axis,(low_face,high_face) in enumerate((("inlet","outlet"),("front","back"),("ground","top"))):
        if below[axis] and low_face in symmetry:below[axis]=False;crossed.append(low_face)
        if above[axis] and high_face in symmetry:above[axis]=False;crossed.append(high_face)
    if crossed:
        warnings.append(f"The model passes through the symmetry plane(s) {', '.join(crossed)}: a quasi-2D section; forces are for the modelled span only")
    if np.any(below) or np.any(above):
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
    validate_elements(spec,meta,mesh,groups,low,high,errors,warnings)
    validate_design(spec, errors)
    validate_free_surface(spec, low, high, errors)
    validate_sliding_mesh(spec, errors)
    tip=max([2*math.pi*abs(z.rpm)/60*z.blade_radius for z in spec.rotating_zones()]+[0])
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
    for feature in unsupported_features(spec):
        errors.append(f"{feature} is not available in this build of the case compiler")
    from .workflow import enrich
    report = enrich({"valid":not errors,"errors":errors,"warnings":warnings,
            "reynolds":spec.fluid.density*max_speed*spec.references.length/spec.fluid.dynamic_viscosity,
            "estimated_mach":max_speed/spec.fluid.speed_of_sound,"validation_status":"experimental_validation_pending"}, spec, meta)
    report["layer_plan"] = plan
    from .capabilities import capabilities_used, experimental_used, CAPABILITIES
    report["capabilities"] = capabilities_used(spec)
    experimental = experimental_used(spec)
    report["experimental_features"] = experimental
    report["physics_maturity"] = "experimental" if experimental else "core"
    if experimental:
        # Guided launch requires acknowledging every review finding for this exact
        # configuration, so this doubles as the experimental-physics consent.
        report["findings"].append({"code":"experimental_physics","stage":"setup","control":"simulation-template","status":"review",
            "detail":"Experimental capability in use: "+", ".join(CAPABILITIES[c]["label"] for c in experimental)
                     +". Results are labelled experimental until the benchmark gates for these capabilities pass."})
    return report


def objective_patches(spec):
    """Model walls the adjoint objective is allowed to see. The wind-tunnel faces, including
    the ground, stay out of the objective so the map is about the model."""
    if spec.use_case and spec.use_case.force_patches:
        names = list(spec.use_case.force_patches)
    else:
        names = [b.patch for b in spec.boundaries if b.kind in ("wall", "moving_wall", "rotating_wall")]
    return [name for name in names if name not in DOMAIN_PATCHES and name != "ground"]


def validate_design(spec, errors):
    """Design advice is a steady external adjoint. Rotating regions, radiators, openings,
    wheels, transient flow and a free surface are rejected rather than silently dropped."""
    boxes = spec.elements.design_boxes
    if not spec.design.enabled and not boxes:
        return
    if boxes and not spec.design.enabled:
        errors.append("Design boxes are the morphing region; turn on design advice to use them")
        return
    if spec.schema_version < 2:
        return
    if spec.mode != "external" or spec.rotation.enabled or spec.elements.rotating_zones:
        errors.append("Design advice is for a steady external flow with no rotating region")
    if spec.elements.porous or spec.elements.openings or spec.elements.wheels:
        errors.append("Design advice does not run with radiators, openings or rotating wheels")
    if spec.time.mode == "transient" or spec.free_surface is not None:
        errors.append("Design advice does not run with transient flow or a free surface")
    if spec.flow.speed <= 0 or spec.references.area <= 0:
        errors.append("Design advice needs a positive speed and a positive reference area")
    if not objective_patches(spec):
        errors.append("Design advice needs at least one model wall to measure the objective on")


def validate_free_surface(spec, low, high, errors):
    """Calm-water interFoam. Fixed trim: the waterline is a number, not a solved sinkage."""
    if spec.free_surface is None or spec.schema_version < 2:
        return
    z = spec.free_surface.waterline_z
    if not low[2] < z < high[2]:
        errors.append("The waterline must lie between the bottom and the top of the domain")
    if spec.rotation.enabled or spec.elements.rotating_zones:
        errors.append("Free-surface runs have no rotating region; sliding meshes are a separate capability")
    if spec.time.mode == "transient" or spec.time.sliding_mesh:
        errors.append("Free-surface runs use local-time interFoam, not a transient or sliding-mesh solve")
    if spec.elements.porous or spec.elements.wheels or spec.design.enabled:
        errors.append("Free-surface runs do not combine with radiators, wheels or design advice")
    gravity = spec.free_surface.gravity
    if abs(gravity[2]) < 1e-6:
        errors.append("Gravity must have a vertical component")
    if spec.free_surface.symmetry_y0:
        # Not compiled: the half-model would need a y = 0 domain face with symmetry conditions.
        errors.append("A half-model symmetry plane at y = 0 is not supported for free-surface runs yet; model the full hull")


def validate_sliding_mesh(spec, errors):
    """cyclicAMI replaces MRF for one transient rotor. Steady MRF is unchanged."""
    if not spec.time.sliding_mesh or spec.schema_version < 2:
        return
    if spec.time.mode != "transient":
        errors.append("A sliding mesh is the transient form of a rotor; choose transient time")
    if spec.mode != "rotor" or not spec.rotation.enabled or spec.elements.rotating_zones:
        errors.append("Sliding mesh needs exactly one rotating region")
    if spec.free_surface is not None or spec.elements.porous or spec.elements.wheels or spec.design.enabled:
        errors.append("Sliding mesh does not combine with a free surface, radiators, wheels or design advice")
    if spec.flow.speed <= 0:
        errors.append("A sliding-mesh run needs a nonzero flow speed")


def validate_elements(spec, meta, mesh, groups, low, high, errors, warnings):
    """Checks for spec v2 elements: ground, wheels, extra rotating zones, porous zones, openings."""
    patches = {p["name"] for p in meta["patches"]}
    kinds = {b.patch: b.kind for b in spec.boundaries}
    e, env = spec.elements, spec.environment
    bounds = np.asarray(meta["bounds"])
    if spec.time.mode == "transient":
        if spec.flow.speed <= 0:
            errors.append("A transient simulation needs a nonzero flow speed")
        if spec.rotating_zones() and not spec.time.sliding_mesh:
            errors.append("Transient rotating parts need a sliding mesh (AMI); turn that on, or use the steady MRF setup")
        if spec.time.scale_resolving:
            errors.append("Scale-resolving (DES) transient simulations are not supported yet; use URANS (k–ω SST) or laminar")
        warnings.append("Transient simulation (experimental): unsteady RANS resolves large-scale shedding only; loads are averaged "
                        "over the averaging window and reported with their fluctuations")
    if env.ground != "none":
        if spec.mode != "external":
            errors.append("A ground plane needs external flow")
        if np.linalg.norm(np.asarray(spec.references.lift_axis) - np.array([0, 0, 1])) > 1e-6:
            errors.append("With a ground plane, lift must point along +Z; the ground is the bottom face of the wind tunnel")
        velocity = np.asarray(spec.flow.velocity())
        if abs(velocity[2]) > 1e-9 * max(spec.flow.speed, 1):
            errors.append("With a ground plane the flow must run parallel to the ground (angle of attack 0)")
        height = float(bounds[1][2] - bounds[0][2])
        sink = low[2] - bounds[0][2]
        if sink > 0.03 * height:
            errors.append("The model sinks too deep into the ground plane; reduce the contact sink or raise the ride height")
        elif sink > 0:
            warnings.append(f"The model sinks {sink*1000:.3g} mm into the ground plane so contact patches mesh cleanly; loads exclude the part below the ground")
    if e.wheels and env.ground == "none":
        errors.append("Rotating wheels need a ground plane: choose On ground or Moving ground")
    if env.ground == "static" and any(w.rotating for w in e.wheels):
        # A spinning tyre on a stationary no-slip floor slips at the contact patch.
        errors.append("Rotating wheels need a moving ground: on a static floor the tyre contact would slip. "
                      "Choose Moving ground, or set the wheels to not rotate")
    for w in e.wheels:
        if w.rotating and abs(float(np.asarray(w.axis) @ np.asarray(spec.references.lift_axis))) > 0.5:
            errors.append(f"Wheel {w.name} axle must be roughly horizontal to roll on the ground")
        if not set(w.patches) <= patches:
            errors.append(f"Wheel {w.name} references unknown surfaces")
        elif any(kinds.get(p) not in ("wall", "rotating_wall", "moving_wall") for p in w.patches):
            errors.append(f"Wheel {w.name} surfaces must be walls")
    zones = spec.rotating_zones()
    for z in e.rotating_zones:
        if not set(z.patches) <= patches:
            errors.append(f"Rotating zone {z.name} references unknown surfaces")
            continue
        selected = [p["group"] for p in meta["patches"] if p["name"] in z.patches]
        vertices = mesh.vertices[np.unique(mesh.faces[np.isin(groups, selected)])]
        if len(vertices):
            delta = vertices - np.asarray(z.origin)
            axial = delta @ np.asarray(z.axis)
            radial = np.linalg.norm(delta - np.outer(axial, z.axis), axis=1)
            if np.max(radial) >= z.radius or np.max(np.abs(axial)) >= z.length/2:
                errors.append(f"Rotating zone {z.name} must fully enclose its surfaces")
        extent = np.abs(z.axis)*z.length/2 + z.radius*np.sqrt(np.maximum(0, 1 - np.square(z.axis)))
        if np.any(np.asarray(z.origin) - extent <= low) or np.any(np.asarray(z.origin) + extent >= high):
            errors.append(f"Rotating zone {z.name} must stay inside the wind tunnel")
    for i, a in enumerate(zones):
        for b in zones[i+1:]:
            # Bounding spheres of the two cylinders: conservative but never misses an overlap.
            reach = math.hypot(a.radius, a.length/2) + math.hypot(b.radius, b.length/2)
            if np.linalg.norm(np.asarray(a.origin) - np.asarray(b.origin)) < reach:
                errors.append(f"Rotating zones {a.name} and {b.name} may overlap; move them apart or shrink them")
    claimed = [p for z in zones for p in z.patches]
    if len(zones) > 1 and len(set(claimed)) != len(claimed):
        errors.append("A surface can belong to only one rotating zone")
    for z in zones:
        if any(kinds.get(p) != "wall" for p in z.patches if p in kinds):
            errors.append(f"Rotating zone {z.name} surfaces must use wall boundaries")
    from .foam_elements import POROUS_CELLS_ACROSS, porous_box, porous_effective_thickness, porous_fit, porous_level
    for p in e.porous:
        try:
            porous_fit(p.curve, spec.fluid.dynamic_viscosity, spec.fluid.density, p.thickness_m)
        except (ValueError, np.linalg.LinAlgError) as error:
            errors.append(f"Radiator {p.name}: {error}")
        _, layers, _ = porous_effective_thickness(spec, p, porous_level(spec, p, spec.mesh.surface_level))
        if layers is not None and layers < POROUS_CELLS_ACROSS:
            warnings.append(f"Radiator {p.name} gets only {layers} cell layers across its thickness at this mesh resolution; "
                            "a sharp porous zone over-predicts its pressure drop by roughly 10-25 % below eight layers. "
                            "Use a finer accuracy tier or a smaller wind tunnel")
        origin, i, j, k = porous_box(p, 0, p.thickness_m*3)
        corners = np.array([origin + i*a + j*b + k*c for a in (0, 1) for b in (0, 1) for c in (0, 1)])
        if np.any(corners.min(axis=0) <= low) or np.any(corners.max(axis=0) >= high):
            errors.append(f"Radiator {p.name} must lie inside the wind tunnel")
    force = set(spec.use_case.force_patches) if spec.use_case else set()
    for o in e.openings:
        if o.patch not in patches:
            errors.append(f"Opening {o.patch} is not a model surface")
        elif o.patch in force:
            errors.append(f"Opening {o.patch} cannot also be a force-measured wall; remove it from the measured parts")
        elif any(o.patch in w.patches for w in e.wheels):
            errors.append(f"Opening {o.patch} is a wheel surface")
    if e.openings and not any(o.kind in ("total_pressure", "outflow_pressure") for o in e.openings) and spec.mode == "internal":
        warnings.append("No opening fixes a pressure; the flow split between openings is then set by the flow rates alone")


# Spec v2 elements become available as the compiler learns them. Until then a configuration
# that uses one is rejected rather than silently simulated without it.
COMPILED_FEATURES = {"early_stop", "nested_wake", "quality_profile", "cells_between_levels", "numerics",
                     "multi_zone", "wheels", "ground", "porous", "openings", "flow_outlet", "transient",
                     "design_advice", "design_boxes", "adaptive_mesh", "free_surface", "ami"}


def unsupported_features(spec):
    used = []
    e = spec.elements
    if e.rotating_zones: used.append(("multi_zone", "Additional rotating zones"))
    if e.wheels: used.append(("wheels", "Rotating wheels"))
    if spec.environment.ground != "none": used.append(("ground", "A ground plane"))
    if spec.environment.yaw_deg: used.append(("yaw", "Model yaw"))
    if e.porous: used.append(("porous", "Porous zones"))
    if e.openings: used.append(("openings", "Flow openings"))
    if e.design_boxes: used.append(("design_boxes", "Design boxes"))
    if spec.design.enabled: used.append(("design_advice", "Design advice"))
    if spec.convergence.enabled: used.append(("early_stop", "Convergence-based early stopping"))
    if spec.mesh.adaptive_cycles or spec.mesh.adaptive_source: used.append(("adaptive_mesh", "Adaptive mesh refinement"))
    if spec.mesh.nested_wake_boxes != 1 or spec.mesh.wake_length_factor != 5 or spec.mesh.wake_level_offset != 1: used.append(("nested_wake", "Configurable wake refinement"))
    if spec.mesh.quality_profile != "standard": used.append(("quality_profile", "Relaxed meshing profile"))
    if spec.mesh.n_cells_between_levels != 3: used.append(("cells_between_levels", "Custom refinement transition"))
    if spec.solver.numerics != "standard" or spec.solver.initialise != "uniform": used.append(("numerics", "Alternative solver initialisation"))
    if spec.time.mode == "transient": used.append(("transient", "Transient simulation"))
    if spec.time.sliding_mesh: used.append(("ami", "Sliding mesh (AMI)"))
    if spec.free_surface is not None: used.append(("free_surface", "Free-surface simulation"))
    if any(b.kind == "flow_outlet" for b in spec.boundaries): used.append(("flow_outlet", "Flow-rate outlets"))
    if spec.schema_version < 2:
        return [label + " (requires a version 2 configuration)" for _, label in used]
    return [label for key, label in used if key not in COMPILED_FEATURES]
