# Automated CAD Modification & Cleaning Architecture Plan

This document details the engineering architecture and technical implementation plan for automatically cleaning, healing, and modifying dirty or nonmanifold CAD geometry inside **GustSim**.

---

## 1. Problem Definition & Goals

Mechanical CAD models imported into GustSim frequently exhibit defects that prevent OpenFOAM `snappyHexMesh` from generating a valid volume mesh:
1. **Nonmanifold Edges**: Multiple solid bodies in an assembly touch without a shared topological interface, resulting in edges shared by 3 or 4 triangles.
2. **Open Boundaries**: Missing faces or micro-gaps from imprecise tessellation.
3. **Internal Partitions**: Internal structural bulkheads or fasteners trapped inside outer skins.
4. **Self-Intersections & Overlaps**: Interference fits where components penetrate each other.

### Engineering Goals
- **Automated Watertight Envelope**: Convert any assembly (even with hundreds of touching, overlapping parts) into a single 100% watertight 2-manifold surface.
- **Component Identity Preservation**: Keep selectable boundary patches (e.g. rotor vs. chassis) after automated healing.
- **Deterministic & Local**: Run entirely in the local container without external cloud services.
- **Fail-Safe Preview**: Revisions are immutable; users can preview diagnostics before applying changes.

---

## 2. Multi-Tiered Architecture

We propose a three-tiered automated cleaning pipeline:

```
[Imported STEP / STL]
         │
         ▼
┌────────────────────────────────────────────────────────┐
│ Tier 1: Exact B-Rep Healing (OpenCASCADE / OCP)        │
│ - Sew open faces (`ShapeFix_Shape`)                    │
│ - Boolean union touching stationary bodies             │
│ - Unify coplanar faces & suppress micro-features       │
└────────────────────────────────────────────────────────┘
         │ (If B-Rep clean & manifold)
         ├───────────────────────────────────────────────► [Mesh Ready]
         │ (If self-intersections or complex defects)
         ▼
┌────────────────────────────────────────────────────────┐
│ Tier 2: Surface Voxel Shrink-Wrapping (Watertight Skin)│
│ - Signed Distance Field (SDF) or Voxelization          │
│ - Dual Contouring / Marching Cubes (OpenVDB or VTK)    │
│ - Feature-preserving Laplacian smoothing               │
│ - Automatic patch projection from source components   │
└────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────┐
│ Tier 3: In-App Interactive Diagnostic & Repair UI      │
│ - 3D visual error flags (red nonmanifold edges)        │
│ - One-click actions: "Auto-fuse", "Shrink-wrap"        │
└────────────────────────────────────────────────────────┘
```

---

## 3. Tier 1: Exact B-Rep Healing (OpenCASCADE / OCP)

When geometry is imported via STEP, GustSim already has access to OpenCASCADE Python (`OCP`). Tier 1 operates directly on the B-Rep topological shapes prior to surface tessellation.

### 1. Topological Sewing (`BRepBuilderAPI_Sewing`)
```python
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing

sewing = BRepBuilderAPI_Sewing(tolerance=1e-4)
sewing.Add(shape)
sewing.Perform()
healed_shape = sewing.SewedShape()
```
- Stitches adjacent faces sharing coincident edges within a configurable tolerance (e.g., $10^{-4}\text{ m}$).
- Eliminates microscopic cracks caused by export tolerances.

### 2. Assembly Boolean Union (`BRepAlgoAPI_Fuse`)
- Identify all stationary leaf parts in the STEP assembly.
- Sequentially execute `BRepAlgoAPI_Fuse` to union touching and overlapping stationary solids into a single unified compound solid.
- Preserves sharp mechanical edges and exact analytic curvature while eliminating all internal touching faces and nonmanifold T-junctions.

### 3. Shape Healing (`ShapeFix_Shape`)
- Correct face orientation (consistently outward-pointing normals).
- Re-triangulate degenerate wire boundaries with `ShapeFix_Wire`.

---

## 4. Tier 2: Surface Voxel Shrink-Wrapping (Guaranteed Watertight Skin)

For highly complex, dirty geometries (such as `VehicleAssem.STEP` with thousands of internal components, overlapping sheet metals, and complex interference fits), B-Rep Booleans can fail or produce singular geometry.

A **Surface Shrink-Wrap / Voxel Remesher** guarantees a 100% closed, 2-manifold surface with zero nonmanifold edges:

### Technical Pipeline
1. **Surface Voxelization**:
   - Discretize the triangulated bounds into a narrow-band Signed Distance Field (SDF) using `VTK` or `OpenVDB`.
   - Grid spacing is chosen based on the desired surface resolution (e.g., $0.002\text{ m}$ for a vehicle exterior).
2. **Dual Contouring / Discrete Flying Edges**:
   - Extract the zero-isocontour using `vtkDiscreteFlyingEdges3D` or `vtkFlyingEdges3D`.
   - The resulting isosurface is topologically guaranteed to be 2-manifold (zero open edges, zero nonmanifold edges).
3. **Surface Smoothing & Decimation**:
   - Apply constrained Laplacian smoothing to eliminate voxel stair-stepping.
   - Decimate flat regions with `vtkQuadricDecimation` while preserving sharp feature angles (> 30°).
4. **Patch Attribute Transfer**:
   - Project the original component identities and patch names from the source model onto the new shrink-wrapped mesh via nearest-surface ray casting (`vtkCellLocator`).
   - Rotor boundaries and force measurement patches retain their assignment.

---

## 5. Tier 3: In-App Interactive Diagnostic & Repair UI

In the GustSim **Geometry** stage (Step 0):
1. **Error Visualization**:
   - Highlight open edges in yellow and nonmanifold edges in red directly in the 3D viewport.
2. **Automated Healing Presets**:
   - **"Clean & Fuse Assembly"**: Runs Tier 1 (Sewing + B-Rep Boolean Fuse).
   - **"Exterior Shrink-Wrap (Aerodynamics)"**: Runs Tier 2 (Extracts external watertight shell, ideal for vehicles and complex structures).
   - **"Cap Planar Ports (Internal Flow)"**: Automatically identifies all planar circular/elliptical loops and triangulates inlet/outlet boundary patches.
3. **Diagnostic Comparison Banner**:
   - Compares before/after statistics:
     `Triangles: 262k → 180k | Open Edges: 0 | Nonmanifold: 0 | Closed Surface: Yes`.

---

## 6. Implementation Roadmap

- **Phase A (Current)**: Architecture plan & user pre-export guide ([CAD_PREPARATION_GUIDE.md](./CAD_PREPARATION_GUIDE.md)).
- **Phase B**: OpenCASCADE `BRepBuilderAPI_Sewing` and `BRepAlgoAPI_Fuse` for stationary assembly components in `geometry.py`.
- **Phase C**: Voxel shrink-wrap filter module using VTK (`vtkVoxelModeller` / `vtkFlyingEdges3D` / `vtkQuadricDecimation`) for dirty assemblies.
- **Phase D**: UI integration with real-time error wireframe overlays in the 3D viewer.
