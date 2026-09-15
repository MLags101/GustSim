"""Watertight shrink-wrap for CAD that conservative repair cannot close.

Conservative preparation (`geometry.prepare`) deliberately never welds across STEP
instance boundaries, so a real assembly whose parts touch or interpenetrate keeps
non-manifold edges forever and can never satisfy the meshing gate. Wrapping trades
exact geometry for a guaranteed-closed surface: it rasterizes the triangle soup onto a
uniform grid, floods the exterior, and contours the boundary between outside and
everything else.

That makes it robust to exactly what blocks assemblies today -- open shells,
non-manifold junctions, interpenetrating bodies, duplicate and self-intersecting
triangles -- because none of those change which voxels the surface occupies.

The cost is honest and must stay visible to the user: **features thinner than the wrap
resolution are lost, and openings narrower than it are sealed.** Callers stamp the
resulting revision with `geometry_fidelity="wrapped"` and the resolution used, and that
label has to survive into the run manifest and every export.
"""
import numpy as np
import trimesh

# Wrapping is a geometry approximation, never an accuracy claim.
FIDELITY = "wrapped"


def _surface_samples(mesh, spacing):
    """Points covering every triangle densely enough that no voxel is skipped."""
    triangles = np.asarray(mesh.triangles, dtype=float)
    if not len(triangles):
        raise ValueError("Cannot wrap a surface with no triangles")
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    areas = np.linalg.norm(np.cross(b - a, c - a), axis=1) / 2
    # Roughly four samples per voxel cross-section, so a triangle cannot straddle a
    # voxel without marking it.
    counts = np.maximum(np.ceil(4 * areas / (spacing * spacing)).astype(np.int64), 1)
    index = np.repeat(np.arange(len(counts)), counts)
    rank = np.arange(int(counts.sum()), dtype=np.int64) - np.repeat(np.cumsum(counts) - counts, counts)
    span = np.repeat(counts, counts)
    r1 = (rank + 0.5) / span
    r2 = _radical_inverse(rank, 3)
    root = np.sqrt(r1)
    w0, w1, w2 = (1 - root), root * (1 - r2), root * r2
    points = a[index] * w0[:, None] + b[index] * w1[:, None] + c[index] * w2[:, None]
    # Triangle corners guarantee the extremes are covered too.
    corners = triangles.reshape(-1, 3)
    corner_index = np.repeat(np.arange(len(triangles)), 3)
    return np.vstack([points, corners]), np.concatenate([index, corner_index])


def _radical_inverse(n, base):
    result = np.zeros(len(n), dtype=float)
    scale = 1.0
    remaining = n.astype(np.int64).copy()
    while remaining.any():
        scale /= base
        result += (remaining % base) * scale
        remaining //= base
    return result


def plan(mesh, resolution=256, budget=320, pad=6):
    """Choose a voxel size: `resolution` cells across the longest side, capped by `budget`."""
    extent = np.asarray(mesh.extents, dtype=float)
    longest = float(extent.max())
    if not np.isfinite(longest) or longest <= 0:
        raise ValueError("Cannot wrap degenerate geometry")
    spacing = longest / max(int(resolution), 8)
    # Keep the grid inside a sane memory envelope on a 20 GB worker.
    while np.any(np.ceil(extent / spacing) + 2 * pad + 1 > budget):
        spacing *= 1.25
    return spacing


def wrap(mesh, resolution=256, smoothing=20, target_triangles=250_000, close_gaps=2):
    """Return `(watertight_mesh, info)` approximating `mesh` at the chosen resolution.

    Raises ValueError when the result is not closed, so a failed wrap is reported as a
    blocked geometry rather than silently meshing something unusable.
    """
    from vtkmodules.vtkCommonDataModel import vtkImageData
    from vtkmodules.vtkCommonCore import vtkFloatArray
    from vtkmodules.vtkFiltersCore import (vtkFlyingEdges3D, vtkWindowedSincPolyDataFilter,
                                           vtkQuadricDecimation, vtkPolyDataConnectivityFilter)
    from vtkmodules.vtkImagingMorphological import vtkImageConnectivityFilter
    from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy

    # The padding must clear the gap-closing dilation, or the sealed surface reaches the
    # grid border, the exterior flood cannot get around the body, and the contour is cut
    # off at the image edge -- producing an open surface.
    # Closing a gap much wider than a fraction of the model distorts it badly, so bound
    # the dilation and report what was actually used.
    requested_gaps = int(close_gaps)
    close_gaps = max(0, min(requested_gaps, max(1, int(resolution) // 8)))
    pad = int(close_gaps) + 4
    spacing = plan(mesh, resolution, pad=pad)
    low = np.asarray(mesh.bounds[0], dtype=float) - spacing * pad
    high = np.asarray(mesh.bounds[1], dtype=float) + spacing * pad
    shape = (np.ceil((high - low) / spacing).astype(np.int64) + 1)

    occupied = np.zeros(tuple(shape), dtype=bool)
    samples, _ = _surface_samples(mesh, spacing)
    ijk = np.floor((samples - low) / spacing).astype(np.int64)
    for axis in range(3):
        np.clip(ijk[:, axis], 0, shape[axis] - 1, out=ijk[:, axis])
    occupied[ijk[:, 0], ijk[:, 1], ijk[:, 2]] = True

    # Thicken the surface before flooding so gaps up to `close_gaps` voxels wide are
    # bridged; the same amount is eroded back afterwards so the wrap does not inflate.
    sealed = _dilate(occupied, close_gaps) if close_gaps else occupied

    # Flood the empty space from the padded border. Whatever the flood cannot reach is
    # enclosed by the surface, however messy that surface is.
    free = (~sealed).astype(np.uint8)
    image = vtkImageData()
    image.SetDimensions(int(shape[0]), int(shape[1]), int(shape[2]))
    image.SetOrigin(float(low[0]), float(low[1]), float(low[2]))
    image.SetSpacing(spacing, spacing, spacing)
    # VTK image arrays are x-fastest; numpy is x-slowest.
    flat = numpy_to_vtk(np.ascontiguousarray(free.transpose(2, 1, 0).ravel()), deep=True)
    flat.SetName("free")
    image.GetPointData().SetScalars(flat)

    connectivity = vtkImageConnectivityFilter()
    connectivity.SetInputData(image)
    connectivity.SetScalarRange(1, 1)
    connectivity.SetExtractionModeToSeededRegions()
    connectivity.SetLabelModeToConstantValue()
    connectivity.SetLabelConstantValue(1)
    seeds = _corner_seeds(low, high)
    connectivity.SetSeedData(seeds)
    connectivity.Update()
    outside = vtk_to_numpy(connectivity.GetOutput().GetPointData().GetScalars())
    outside = outside.reshape(int(shape[2]), int(shape[1]), int(shape[0])).transpose(2, 1, 0)

    solid = outside == 0
    if close_gaps:
        solid = _erode(solid, close_gaps)
    if not solid.any():
        raise ValueError("Wrapping found no enclosed volume; the surface may be a single open sheet")
    # A wrap that leaked through a hole returns the surface shell instead of the body.
    # Compare against the occupied shell itself: a real solid encloses far more voxels.
    shell_voxels = int(occupied.sum())
    solid_voxels = int(solid.sum())
    if solid_voxels <= shell_voxels * 1.05:
        raise ValueError(
            "Wrapping leaked through an opening wider than the wrap resolution, so no "
            "enclosed volume was formed. Use a finer wrap resolution or a larger gap-closing "
            "distance, or cap the opening in Geometry.")
    solid = solid.astype(np.float32)

    volume = vtkImageData()
    volume.SetDimensions(int(shape[0]), int(shape[1]), int(shape[2]))
    volume.SetOrigin(float(low[0]), float(low[1]), float(low[2]))
    volume.SetSpacing(spacing, spacing, spacing)
    values = numpy_to_vtk(np.ascontiguousarray(solid.transpose(2, 1, 0).ravel()), deep=True,
                          array_type=vtkFloatArray().GetDataType())
    values.SetName("solid")
    volume.GetPointData().SetScalars(values)

    contour = vtkFlyingEdges3D()
    contour.SetInputData(volume)
    contour.SetValue(0, 0.5)
    contour.ComputeNormalsOff()
    contour.Update()
    surface = contour.GetOutput()
    if surface.GetNumberOfCells() == 0:
        raise ValueError("Wrapping produced an empty surface")

    if smoothing:
        # Removes the voxel staircase without pulling the surface off the geometry.
        smooth = vtkWindowedSincPolyDataFilter()
        smooth.SetInputData(surface)
        smooth.SetNumberOfIterations(int(smoothing))
        smooth.SetPassBand(0.1)
        smooth.NonManifoldSmoothingOff()
        smooth.NormalizeCoordinatesOn()
        smooth.BoundarySmoothingOff()
        smooth.FeatureEdgeSmoothingOff()
        smooth.Update()
        surface = smooth.GetOutput()

    if target_triangles and surface.GetNumberOfCells() > target_triangles:
        decimate = vtkQuadricDecimation()
        decimate.SetInputData(surface)
        decimate.SetTargetReduction(1 - target_triangles / surface.GetNumberOfCells())
        decimate.Update()
        if decimate.GetOutput().GetNumberOfCells():
            surface = decimate.GetOutput()

    # Keep only the largest shell: isolated specks from stray triangles are not geometry.
    regions = vtkPolyDataConnectivityFilter()
    regions.SetInputData(surface)
    regions.SetExtractionModeToAllRegions()
    regions.Update()
    shells = regions.GetNumberOfExtractedRegions()
    if shells > 1:
        largest = vtkPolyDataConnectivityFilter()
        largest.SetInputData(surface)
        largest.SetExtractionModeToLargestRegion()
        largest.Update()
        surface = largest.GetOutput()

    points = vtk_to_numpy(surface.GetPoints().GetData()).astype(float)
    faces = vtk_to_numpy(surface.GetPolys().GetConnectivityArray()).reshape(-1, 3)
    wrapped = trimesh.Trimesh(points, faces, process=True)
    wrapped.remove_unreferenced_vertices()
    trimesh.repair.fix_normals(wrapped, multibody=True)
    if not wrapped.is_watertight:
        raise ValueError("Wrapping did not produce a closed surface; try a finer wrap resolution")

    info = {
        "fidelity": FIDELITY,
        "wrap_resolution_m": float(spacing),
        "grid": [int(n) for n in shape],
        "shells_found": int(shells),
        "triangles": int(len(wrapped.faces)),
        "source_triangles": int(len(mesh.faces)),
        "closed_gap_m": float(spacing * close_gaps),
        "close_gaps_voxels": int(close_gaps),
        "close_gaps_requested": requested_gaps,
        "volume_m3": float(abs(wrapped.volume)),
        "detail": ("Closed surface approximated on a "
                   f"{spacing*1000:.3g} mm grid. Features thinner than this are not "
                   "represented and openings narrower than it are sealed."),
    }
    return wrapped, info


def _corner_seeds(low, high):
    """Seed the flood at the eight padded corners, which are always outside the body.

    These are world coordinates: vtkImageConnectivityFilter locates seed points through
    the image geometry, so index coordinates would seed arbitrary interior voxels.
    """
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkPolyData
    points = vtkPoints()
    for x in (low[0], high[0]):
        for y in (low[1], high[1]):
            for z in (low[2], high[2]):
                points.InsertNextPoint(float(x), float(y), float(z))
    seeds = vtkPolyData()
    seeds.SetPoints(points)
    return seeds


def _dilate(mask, steps):
    """Grow a boolean mask by `steps` voxels along the 6 face neighbours."""
    grown = mask
    for _ in range(int(steps)):
        out = grown.copy()
        out[1:, :, :] |= grown[:-1, :, :]; out[:-1, :, :] |= grown[1:, :, :]
        out[:, 1:, :] |= grown[:, :-1, :]; out[:, :-1, :] |= grown[:, 1:, :]
        out[:, :, 1:] |= grown[:, :, :-1]; out[:, :, :-1] |= grown[:, :, 1:]
        grown = out
    return grown


def _erode(mask, steps):
    return ~_dilate(~mask, steps)


def _fill_labels(labels, steps=8):
    """Spread labels into unlabelled neighbours so points just off the shell resolve."""
    for _ in range(int(steps)):
        empty = labels < 0
        if not empty.any():
            break
        for axis in range(3):
            for shift in (1, -1):
                donor = np.roll(labels, shift, axis=axis)
                # np.roll wraps; blank the wrapped face so labels cannot jump across the grid.
                face = [slice(None)] * 3
                face[axis] = 0 if shift > 0 else -1
                donor[tuple(face)] = -1
                take = empty & (donor >= 0)
                labels[take] = donor[take]
                empty = labels < 0
    return labels


def assign_groups(wrapped, source, groups, spacing=None):
    """Carry patch identity onto the wrap.

    Component selection, per-component forces and rotor patches all key off group ids, so
    a wrap that dropped them would only be good for a single-body drag number.

    Uses the same voxel grid as the wrap rather than a nearest-triangle search: a real
    assembly has hundreds of thousands of triangles on both sides, and the brute-force
    product of the two is not tractable (no scipy or rtree in this stack).
    """
    groups = np.asarray(groups)
    if not len(groups) or not len(wrapped.faces):
        return np.zeros(len(wrapped.faces), dtype=np.int32)
    if spacing is None:
        spacing = plan(source, 256)
    low = np.asarray(source.bounds[0], dtype=float) - spacing * 2
    high = np.asarray(source.bounds[1], dtype=float) + spacing * 2
    shape = np.ceil((high - low) / spacing).astype(np.int64) + 1
    labels = np.full(tuple(shape), -1, dtype=np.int32)
    points, triangle = _surface_samples(source, spacing)
    ijk = np.floor((points - low) / spacing).astype(np.int64)
    for axis in range(3):
        np.clip(ijk[:, axis], 0, shape[axis] - 1, out=ijk[:, axis])
    labels[ijk[:, 0], ijk[:, 1], ijk[:, 2]] = groups[triangle]
    _fill_labels(labels)

    centers = np.asarray(wrapped.triangles_center, dtype=float)
    cijk = np.floor((centers - low) / spacing).astype(np.int64)
    for axis in range(3):
        np.clip(cijk[:, axis], 0, shape[axis] - 1, out=cijk[:, axis])
    assigned = labels[cijk[:, 0], cijk[:, 1], cijk[:, 2]]
    if (assigned < 0).any():
        # Anything still unresolved sits far from every source triangle; give it the
        # largest patch rather than inventing a group.
        values, counts = np.unique(groups, return_counts=True)
        assigned = np.where(assigned < 0, values[int(np.argmax(counts))], assigned)
    return assigned.astype(np.int32)
