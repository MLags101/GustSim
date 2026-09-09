# CAD Preparation Guide for CFD

This guide explains how to prepare 3D CAD models before importing them into **GustSim** for Computational Fluid Dynamics (CFD). Following these guidelines ensures smooth surface meshing with OpenFOAM `snappyHexMesh` and prevents nonmanifold errors, meshing failures, and unphysical flow predictions.

---

## 1. Quick Pre-Import Checklist

Before exporting your model from your CAD system, verify:

- [ ] **Solid Bodies, Not Surfaces**: Every part must be an enclosed 3D solid volume (no zero-thickness sheet bodies).
- [ ] **Single Assembly STEP**: Export the entire simulation setup (including moving rotors and stationary ducts) in **one STEP file** (`.step` or `.stp`, AP214 or AP242).
- [ ] **Watertight & Manifold**: No open edges, no T-junctions, and no edges shared by more than two faces.
- [ ] **Boolean Combine Touching Solids**: Stationary components that touch or weld together must be Boolean-unioned (`Combine` / `Join`) into single unified bodies.
- [ ] **Clearance Around Rotors**: Maintain a clean gap (at least 2–5 mm or 1% diameter) between spinning rotor blades and stationary casings so the virtual MRF cylinder does not intersect stationary walls.
- [ ] **Defeatured**: Cosmetic fillets (< 1–2 mm), screw threads, bolt heads, logos, and tiny clearance gaps (< 0.5 mm) should be removed or suppressed.
- [ ] **Aligned with Coordinate System**: Position the model with standard Cartesian directions (+X downstream / travel direction, +Z vertical/lift, rotation axis along X, Y, or Z).

---

## 2. Supported Formats: STEP vs. STL

| Feature | STEP (`.step`, `.stp`) | STL (`.stl`) |
|---|---|---|
| **Recommendation** | **Preferred (Recommended)** | Fallback only |
| **Assembly Structure** | Preserves named component hierarchy and instance transforms | Flattened into a single mesh or requires manual patch grouping |
| **Surface Precision** | True analytic B-Rep curves and NURBS surfaces | Faceted planar triangles; faceted curves |
| **Patch Separation** | Automatically splits CAD faces into selectable patches | Requires angle-based edge splitting |
| **Units** | Embedded in file header (automatically converted to metres) | Unitless (must declare mm, cm, in, or m on import) |

> [!TIP]
> When exporting STEP files from your CAD system, select **STEP AP214** or **STEP AP242**. These versions preserve assembly part names, colors, and hierarchical instances.

---

## 3. Water-Tightness and 2-Manifold Geometry

OpenFOAM's snappyHexMesh generates a 3D Cartesian background grid and "snaps" cell vertices to the CAD surface. For this algorithm to distinguish fluid cells from solid cells:

### A. What is 2-Manifold Geometry?
- Every edge in the surface mesh must be shared by **exactly two faces**.
- **Nonmanifold Edges (3+ faces per edge)** occur when two solid bodies touch at an edge or face without being Boolean-fused, or when an internal partition wall exists inside a solid.
- **Open Edges (1 face per edge)** occur when a face is missing, leaving a hole into the interior.

### B. How to Fix in CAD:
1. **Combine Stationary Parts**: In SolidWorks, Fusion 360, or Onshape, use **Boolean Combine (Join)** to merge touching stationary parts (e.g. wing + fuselage, pipe segments, brackets).
2. **Delete Internal Faces**: Never leave internal dividing walls between two adjacent fluid or solid cavities.
3. **Check for Zero-Thickness Geometry**: Avoid tangential contacts where a cylinder touches a flat plate along an infinitesimal line; create a small fillet or overlap and Boolean-combine them.

---

## 4. Spinning Components & MRF Setup

GustSim uses the steady **Multiple Reference Frame (MRF)** formulation to model rotating components (propellers, turbines, fans, impellers) without moving the computational mesh.

### A. Assembly Structure
- Keep the rotor (blades + hub) as a **separate named solid body** within the same assembly STEP file.
- Do **not** merge the rotor body with stationary nacelles, struts, or ducts.

### B. Clearance Requirements
- In GustSim, a virtual cylindrical cell zone (the MRF region) is placed around the rotor.
- **Rule**: The MRF cylinder must completely encompass the rotating blades, but **must not intersect or touch any stationary walls**.
- Maintain radial clearance: ensure the stationary casing or duct is larger than the rotating blade tips by at least 1–3 mm (or ~2% blade radius).
- If the rotor hub blends into a stationary shaft, split the surface at the physical rotation interface.

### C. Rotation Axis Alignment
- Align the rotor axis with a principal Cartesian axis (+X, +Y, or +Z) and center the origin on the hub face or shaft center.

---

## 5. External vs. Internal Flow Preparation

### External Aerodynamics (Vehicles, Drones, Airfoils, Buildings)
- Model the **solid vehicle geometry**. GustSim will automatically construct the outer fluid bounding box.
- Seal all cabin openings, wheel wells, and radiator grilles unless internal flow through the engine bay is intentionally being modeled. Open passenger cabins or hollow chassis walls will trap fluid and create meshing anomalies.
- For wheels resting on ground planes, create a slight flat contact patch rather than an infinitesimal line contact.

### Internal Flows (Pipes, Valves, Manifolds, Ducts)
There are two valid approaches for internal flow:
1. **Negative Fluid Volume Extraction (Best Practice)**:
   - In your CAD system, use the "Cavity" or "Combine / Subtract" tool to extract the fluid volume as a solid body.
   - The outer boundaries of this solid body will directly represent the pipe walls, inlet, and outlet.
2. **Solid Pipe with Planar Caps**:
   - If importing a hollow pipe, ensure the inlet and outlet openings are **flat planar cuts** perpendicular to the flow.
   - In GustSim's **Geometry** tab, use the **Cap planar openings** tool to create inlet and outlet surface patches automatically.

---

## 6. Defeaturing Guidelines

CFD meshes do not need every manufacturing detail. Features smaller than the local fluid mesh resolution cause high cell counts, inverted cells, and divergence.

| Feature to Suppress | Why Suppress It | Recommended Action |
|---|---|---|
| **Screw threads & bolt heads** | Creates millions of tiny distorted cells | Replace with flush cylinders or suppress entirely |
| **Small cosmetic fillets (< 1.5 mm)** | Causes high cell skewness | Remove or replace with sharp corners |
| **Embossed text & logos** | Creates nonmanifold sliver triangles | Suppress all text features |
| **Small clearance gaps (< 0.5 mm)** | snappyHexMesh cannot resolve gap without extreme refinement | Close gaps or widen them to > 2 mm |
| **Internal wiring & electronics** | Not exposed to external fluid flow | Delete from assembly before export |

---

## 7. Export Instructions by CAD Package

### Autodesk Fusion 360
1. Open the model and navigate to the **Design** workspace.
2. Suppress fasteners, small fillets, and cosmetic features in the timeline.
3. In the browser tree, select the top-level assembly component.
4. Click **File → Export**.
5. Set **Type** to `STEP Files (*.step *.stp)`.
6. Ensure each component has a descriptive name (e.g., `Chassis`, `Propeller_Blades`, `Duct`).

### SolidWorks
1. Open your assembly (`.sldasm`).
2. Go to **Tools → Evaluate → Check** to verify there are no invalid faces or open edges.
3. Use **Defeature** (Tools → Defeature) to remove internal parts and cosmetic details if desired.
4. Click **File → Save As**.
5. Select **STEP AP214 (*.step)**.
6. Click **Options...**:
   - Set **Output as**: `Solid/Surface geometry`.
   - Check `Export face/edge properties`.
   - Check `Split periodic faces`.
   - Set Output unit to `Millimeters` or `Meters`.

### PTC Onshape
1. Open your Part Studio or Assembly.
2. Right-click the Assembly tab or Part Studio tab at the bottom.
3. Click **Export**.
4. Set **Format** to `STEP`.
5. Set **Version** to `AP214` or `AP242`.
6. Check `Export unique parts only` as `False` (export entire assembly in position).

### Rhino 3D
1. Select all solid geometry.
2. Run `ShowEdges` and select **Naked Edges** to confirm there are no open edges.
3. Run `SelBadObjects` to ensure all polysurfaces are valid.
4. Run `Export` or `Save As`.
5. Choose **STEP (*.stp, *.step)** with the `Default` or `AP214AutomotiveDesign` scheme.
