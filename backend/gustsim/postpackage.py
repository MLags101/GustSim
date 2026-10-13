"""Results package: browser-ready layers and report renders from a solved case.

Uses VTK only (vtkmodules), so it runs under pvbatch in the worker (which provides the virtual
display for offscreen renders) and in-process in tests. Each item is independent: a failure is
recorded in layers.json `missing` with its reason and never removes the other layers.

Conventions written into every layer: SI units, gauge pressure in Pa (rho * kinematic p),
coefficients against the freestream (or inlet) dynamic pressure, `wall_shear_pa` = stress the
fluid exerts on the wall (aligned with the near-wall flow, i.e. -rho * OpenFOAM wallShearStress),
and surface normals pointing out of the body into the fluid.
"""
import json
import sys
import time
import traceback
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))  # pvbatch runs this file without gustsim installed
from foamread import cleanup as foam_cleanup, foam_file  # noqa: E402

SURFACE_BUDGET_TRIANGLES = 300_000
CLOUD_TRIANGLES = 250_000
SECTION_LONGEST = 160
# Near-wall velocity: |U| averaged over samples at these fractions of the reference length off
# the surface. Closer in, the sample sits inside the steep boundary-layer gradient and follows
# the cell layering (visible banding) instead of the flow just outside the wall.
NEAR_WALL_OFFSETS = (0.015, 0.02, 0.025)
# Seeds per streamline rake; the viewer thins them with its line-density slider.
RAKE_SEEDS = 81
ANIMATION_BUDGET_BYTES = 80*1024*1024


def vtk():
    import vtkmodules.vtkCommonCore as core
    import vtkmodules.vtkCommonDataModel as data
    import vtkmodules.vtkFiltersCore as filters
    import vtkmodules.vtkFiltersGeneral as general
    import vtkmodules.vtkFiltersGeometry as geometry
    import vtkmodules.vtkFiltersFlowPaths as flow
    import vtkmodules.vtkFiltersSources as sources
    import vtkmodules.vtkIOGeometry as iogeometry
    import vtkmodules.vtkIOXML as xml
    from vtkmodules.util import numpy_support
    return core, data, filters, general, geometry, flow, sources, iogeometry, xml, numpy_support


class Package:
    def __init__(self, case, out, plan):
        self.case, self.out, self.plan = Path(case), Path(out), plan
        self.out.mkdir(parents=True, exist_ok=True)
        self.layers, self.missing, self.renders, self.timings = [], [], [], {}
        (self.core, self.data, self.filters, self.general, self.geometry, self.flow, self.sources,
         self.iogeometry, self.xml, self.np) = vtk()
        self.rho = float(plan['rho'])
        # Single-phase solvers write kinematic p (m²/s²); interFoam writes p in Pa already.
        self.pressure_is_kinematic = bool(plan.get('pressure_is_kinematic', True))
        self.u_inf = np.asarray(plan['u_inf'], float)
        self.speed = float(np.linalg.norm(self.u_inf))
        self.q_kin = 0.5*self.speed**2
        self.length = float(plan['reference_length'])
        self.basis = 'steady'

    # ---- reading -----------------------------------------------------------------------
    def read(self):
        reader = self.iogeometry.vtkOpenFOAMReader()
        reader.SetFileName(str(foam_file(self.case)))
        reader.UpdateInformation()
        reader.SetCreateCellToPoint(1)
        if hasattr(reader, 'SetDecomposePolyhedra'):  # removed in VTK 9.3+, polyhedra are native
            reader.SetDecomposePolyhedra(1)
        wanted = set(self.plan['object_patches'])
        for i in range(reader.GetNumberOfPatchArrays()):
            name = reader.GetPatchArrayName(i)
            reader.SetPatchArrayStatus(name, int(name == 'internalMesh' or name.split('/')[-1] in wanted))
        reader.EnableAllCellArrays()
        values = reader.GetTimeValues()
        times = [values.GetValue(i) for i in range(values.GetNumberOfTuples())] if values else []
        times = [t for t in times if t > 0]
        if not times:
            raise RuntimeError('No solved time directories were written')
        self.time = max(times)
        reader.UpdateTimeStep(self.time)
        output = reader.GetOutput()
        self.volume = self.find(output, 'internalMesh')
        boundary = self.find(output, 'boundary')
        self.patches = {}
        if boundary is not None:
            for i in range(boundary.GetNumberOfBlocks()):
                name = boundary.GetMetaData(i).Get(self.data.vtkCompositeDataSet.NAME())
                if name in wanted and boundary.GetBlock(i) is not None:
                    self.patches[name] = boundary.GetBlock(i)
        if self.volume is None:
            raise RuntimeError('The internal mesh could not be read')
        if self.plan.get('basis') == 'time_averaged':
            self.use_mean()

    def use_mean(self):
        """Transient runs: the time-averaged fields (fieldAverage) replace the final instant for
        every layer; coefficients are then derived from the mean pressure and velocity."""
        swapped = False
        for dataset in [self.volume, *self.patches.values()]:
            for attributes in (dataset.GetPointData(), dataset.GetCellData()):
                for name in ('p', 'U'):
                    mean = attributes.GetArray(name + 'Mean')
                    if mean is not None:
                        values = self.np.vtk_to_numpy(mean).astype(float)
                        attributes.RemoveArray(name)
                        self.put(attributes, name, values, dtype=float)
                        swapped = True
                for derived in ('Cp', 'Cpt'):
                    attributes.RemoveArray(derived)
        self.basis = 'time_averaged' if swapped else 'final_instant'
        if not swapped:
            self.missing.append({'id': 'time_average', 'reason': 'No time-averaged fields (pMean, UMean) were written; layers show the final instant'})

    def find(self, block, name):
        for i in range(block.GetNumberOfBlocks()):
            if block.GetMetaData(i).Get(self.data.vtkCompositeDataSet.NAME()) == name:
                return block.GetBlock(i)
            child = block.GetBlock(i)
            if child is not None and child.IsA('vtkMultiBlockDataSet'):
                found = self.find(child, name)
                if found is not None:
                    return found
        return None

    # ---- array helpers -----------------------------------------------------------------
    def get(self, attributes, name):
        array = attributes.GetArray(name)
        return None if array is None else self.np.vtk_to_numpy(array).astype(float)

    def put(self, attributes, name, values, dtype=np.float32):
        array = self.np.numpy_to_vtk(np.ascontiguousarray(values, dtype=dtype), deep=1)
        array.SetName(name)
        attributes.AddArray(array)

    def keep(self, attributes, names):
        for i in reversed(range(attributes.GetNumberOfArrays())):
            name = attributes.GetArrayName(i)
            if name not in names:
                attributes.RemoveArray(name)

    def kinematic(self, p):
        """Pressure as kinematic p (m²/s²) whatever the solver wrote, or None."""
        if p is None:
            return None
        return p if self.pressure_is_kinematic else p/self.rho

    def derive_surface(self, attributes):
        p = self.kinematic(self.get(attributes, 'p'))
        if p is not None:
            self.put(attributes, 'pressure_pa', p*self.rho)
            cp = self.get(attributes, 'Cp')
            if cp is None and self.q_kin > 0:
                self.put(attributes, 'Cp', p/self.q_kin)
        tau = self.get(attributes, 'wallShearStress')
        if tau is not None:
            shear = -self.rho*tau
            magnitude = np.linalg.norm(shear, axis=1)
            self.put(attributes, 'wall_shear_pa', shear)
            self.put(attributes, 'wall_shear_mag', magnitude)
            if self.q_kin > 0:
                self.put(attributes, 'Cf', magnitude/(self.rho*self.q_kin))
        for index in range(attributes.GetNumberOfArrays()):
            name = attributes.GetArrayName(index) or ''
            if 'Sens' in name or name == 'sensitivity':
                values = self.get(attributes, name)
                if values is not None and values.ndim == 1:
                    # OpenFOAM's face sensitivity, kept under one name for the viewer.
                    self.put(attributes, 'sensitivity', values)
            elif name == 'alpha.water':
                values = self.get(attributes, name)
                if values is not None and values.ndim == 1:
                    self.put(attributes, 'alpha_water', values)

    def derive_volume(self, attributes):
        u = self.get(attributes, 'U')
        p = self.kinematic(self.get(attributes, 'p'))
        if u is not None:
            self.put(attributes, 'U_mag', np.linalg.norm(u, axis=1))
        if p is not None:
            self.put(attributes, 'pressure_pa', p*self.rho)
            if self.q_kin > 0:
                if attributes.GetArray('Cp') is None:
                    self.put(attributes, 'Cp', p/self.q_kin)
                if attributes.GetArray('Cpt') is None and u is not None:
                    # Total-pressure coefficient with freestream = 1 (pInf = 0, kinematic).
                    self.put(attributes, 'Cpt', (p + 0.5*np.sum(u*u, axis=1))/self.q_kin)

    # ---- writing -----------------------------------------------------------------------
    def write(self, polydata, relative):
        target = self.out/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        writer = self.xml.vtkXMLPolyDataWriter()
        writer.SetFileName(str(target))
        writer.SetInputData(polydata)
        writer.SetDataModeToBinary()
        writer.SetHeaderTypeToUInt32()
        writer.SetCompressorTypeToZLib()
        writer.Write()
        return target

    def describe(self, polydata):
        arrays = []
        for association, attributes in (('point', polydata.GetPointData()), ('cell', polydata.GetCellData())):
            for i in range(attributes.GetNumberOfArrays()):
                array = attributes.GetArray(i)
                if array is None:
                    continue
                values = self.np.vtk_to_numpy(array)
                finite = values[np.isfinite(values).all(axis=1)] if values.ndim > 1 else values[np.isfinite(values)]
                magnitude = np.linalg.norm(finite, axis=1) if finite.ndim > 1 else finite
                arrays.append({'name': array.GetName(), 'association': association, 'components': array.GetNumberOfComponents(),
                               'range': [float(magnitude.min()), float(magnitude.max())] if len(magnitude) else None})
        return arrays

    def layer(self, identifier, kind, polydata, relative, **extra):
        path = self.write(polydata, relative)
        record = {'id': identifier, 'kind': kind, 'path': relative, 'bytes': path.stat().st_size,
                  'points': polydata.GetNumberOfPoints(), 'cells': polydata.GetNumberOfCells(),
                  'arrays': self.describe(polydata), **extra}
        self.layers.append(record)
        return record

    def attempt(self, identifier, function):
        start = time.monotonic()
        try:
            function()
        except Exception as e:
            self.missing.append({'id': identifier, 'reason': str(e) or e.__class__.__name__,
                                 'trace': traceback.format_exc(limit=3)})
        self.timings[identifier] = round(time.monotonic() - start, 3)

    # ---- surface -----------------------------------------------------------------------
    def surface(self):
        if not self.patches:
            raise RuntimeError('No object surfaces were found in the solved case')
        append = self.filters.vtkAppendPolyData()
        part_of = self.plan.get('patch_parts', {})
        names = sorted(self.patches)
        faces = {k: [] for k in ('centroid', 'normal', 'area', 'part_index', 'patch_index', 'Cp', 'pressure_pa', 'wall_shear_pa', 'yPlus', 'sensitivity', 'alpha_water')}
        for index, name in enumerate(names):
            triangles = self.filters.vtkTriangleFilter()
            triangles.SetInputData(self.patches[name])
            triangles.Update()
            poly = self.data.vtkPolyData()
            poly.DeepCopy(triangles.GetOutput())
            if not poly.GetNumberOfCells():
                continue
            for attributes in (poly.GetPointData(), poly.GetCellData()):
                self.derive_surface(attributes)
            cells = poly.GetNumberOfCells()
            self.put(poly.GetCellData(), 'part_index', np.full(cells, part_of.get(name, -1)), np.int32)
            self.put(poly.GetCellData(), 'patch_index', np.full(cells, index), np.int32)
            points = self.np.vtk_to_numpy(poly.GetPoints().GetData()).astype(float)
            connectivity = self.np.vtk_to_numpy(poly.GetPolys().GetConnectivityArray()).reshape(-1, 3)
            tri = points[connectivity]
            cross = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
            area = 0.5*np.linalg.norm(cross, axis=1)
            # OpenFOAM orders boundary faces with the area vector leaving the fluid, i.e. into
            # the body; the package stores the outward normal of the body surface.
            normal = -cross/np.maximum(np.linalg.norm(cross, axis=1), 1e-300)[:, None]
            cell = poly.GetCellData()
            # Face sensitivity is sometimes only on the points. The insight uses one value per triangle.
            if self.get(cell, 'sensitivity') is None:
                point_sens = self.get(poly.GetPointData(), 'sensitivity')
                if point_sens is not None:
                    self.put(cell, 'sensitivity', point_sens[connectivity].mean(axis=1))
            faces['centroid'].append(tri.mean(axis=1)); faces['normal'].append(normal); faces['area'].append(area)
            faces['part_index'].append(np.full(cells, part_of.get(name, -1))); faces['patch_index'].append(np.full(cells, index))
            for key, fallback in (('Cp', np.nan), ('pressure_pa', np.nan), ('yPlus', np.nan), ('sensitivity', np.nan), ('alpha_water', np.nan)):
                values = self.get(cell, key)
                faces[key].append(values if values is not None else np.full(cells, fallback))
            shear = self.get(cell, 'wall_shear_pa')
            faces['wall_shear_pa'].append(shear if shear is not None else np.full((cells, 3), np.nan))
            append.AddInputData(poly)
        append.Update()
        surface = append.GetOutput()
        if surface.GetNumberOfCells() > SURFACE_BUDGET_TRIANGLES:
            decimate = self.filters.vtkQuadricDecimation()
            decimate.SetInputData(surface)
            decimate.SetTargetReduction(1 - SURFACE_BUDGET_TRIANGLES/surface.GetNumberOfCells())
            decimate.SetVolumePreservation(1)
            decimate.AttributeErrorMetricOn()
            decimate.Update()
            surface = decimate.GetOutput()
        try:
            self.near_wall_velocity(surface)
        except Exception as e:
            self.missing.append({'id': 'near_wall_velocity', 'reason': str(e) or e.__class__.__name__})
        self.keep(surface.GetPointData(), {'Cp', 'pressure_pa', 'wall_shear_pa', 'wall_shear_mag', 'Cf', 'yPlus', 'sensitivity', 'alpha_water', 'U_near_mag'})
        self.keep(surface.GetCellData(), {'part_index', 'patch_index', 'Cp', 'pressure_pa', 'wall_shear_mag', 'Cf', 'yPlus', 'sensitivity', 'alpha_water'})
        self.object_surface = surface
        self.layer('surface', 'surface', surface, 'surface/object.vtp', patch_names=names,
                   near_wall_offset_m=getattr(self, 'near_wall_offset_m', None))
        np.savez_compressed(self.out/'surface/object_faces.npz', patch_names=np.asarray(names),
                            **{k: np.concatenate(v) for k, v in faces.items() if v})

    def near_wall_velocity(self, surface):
        """|U| a short distance off the wall (the wall velocity is zero): each point is moved along
        its outward normal by each of NEAR_WALL_OFFSETS of the reference length, probed in the
        volume, and the valid samples are averaged."""
        if self.volume.GetPointData().GetArray('U') is None:
            raise RuntimeError('U was not written by this solve')
        points = self.np.vtk_to_numpy(surface.GetPoints().GetData()).astype(float)
        connectivity = self.np.vtk_to_numpy(surface.GetPolys().GetConnectivityArray()).reshape(-1, 3)
        tri = points[connectivity]
        # Boundary faces point into the body (out of the fluid); negate for the fluid side.
        face = -np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
        normal = np.zeros_like(points)
        for k in range(3):
            np.add.at(normal, connectivity[:, k], face)
        normal /= np.maximum(np.linalg.norm(normal, axis=1), 1e-300)[:, None]
        offsets = [f*self.length for f in NEAR_WALL_OFFSETS]
        probe_points = self.core.vtkPoints()
        stacked = np.concatenate([points + d*normal for d in offsets])
        probe_points.SetData(self.np.numpy_to_vtk(np.ascontiguousarray(stacked), deep=1))
        source = self.data.vtkPolyData()
        source.SetPoints(probe_points)
        locator = self.data.vtkStaticCellLocator()
        probe = self.filters.vtkProbeFilter()
        probe.SetInputData(source)
        probe.SetSourceData(self.volume)
        probe.SetCellLocatorPrototype(locator)
        probe.Update()
        output = probe.GetOutput()
        u = self.get(output.GetPointData(), 'U')
        valid = self.get(output.GetPointData(), 'vtkValidPointMask')
        if u is None or valid is None:
            raise RuntimeError('The volume could not be probed near the wall')
        samples = np.where(valid > 0, np.linalg.norm(u, axis=1), np.nan).reshape(len(offsets), len(points))
        counted = np.isfinite(samples).sum(axis=0)
        # A sample inside another solid (a narrow gap) is invalid; average whatever lies in fluid.
        magnitude = np.where(counted > 0, np.nansum(samples, axis=0)/np.maximum(counted, 1), np.nan)
        if not np.isfinite(magnitude).any():
            raise RuntimeError('No surface point had fluid within the near-wall offset')
        offset = offsets[len(offsets)//2]
        self.put(surface.GetPointData(), 'U_near_mag', magnitude)
        self.near_wall_offset_m = offset

    def friction_lines(self):
        surface = getattr(self, 'object_surface', None)
        if surface is None or surface.GetPointData().GetArray('wall_shear_pa') is None:
            raise RuntimeError('Wall shear stress was not written by this solve')
        mask = self.filters.vtkMaskPoints()
        mask.SetInputData(surface)
        mask.SetMaximumNumberOfPoints(2500)
        mask.SetOnRatio(max(1, surface.GetNumberOfPoints()//2500))
        mask.RandomModeOn()
        mask.SetRandomModeType(1)
        mask.GenerateVerticesOn()
        mask.Update()
        tracer = self.flow.vtkStreamTracer()
        tracer.SetInputData(surface)
        tracer.SetSourceData(mask.GetOutput())
        tracer.SetInputArrayToProcess(0, 0, 0, 0, 'wall_shear_pa')
        tracer.SetSurfaceStreamlines(True)
        tracer.SetIntegrationDirectionToBoth()
        tracer.SetIntegratorTypeToRungeKutta2()
        tracer.SetIntegrationStepUnit(2)
        tracer.SetInitialIntegrationStep(0.3)
        tracer.SetMaximumNumberOfSteps(600)
        tracer.SetMaximumPropagation(float(np.linalg.norm(np.ptp(np.asarray(self.plan['model_bounds']), axis=0))))
        tracer.SetTerminalSpeed(1e-12)
        tracer.Update()
        lines = tracer.GetOutput()
        if not lines.GetNumberOfCells():
            raise RuntimeError('No surface friction lines could be traced')
        self.keep(lines.GetPointData(), {'Cf', 'wall_shear_mag', 'IntegrationTime'})
        self.keep(lines.GetCellData(), {'SeedIds'})
        self.layer('friction_lines', 'lines', lines, 'surface/friction_lines.vtp')

    # ---- volume ------------------------------------------------------------------------
    def free_surface_layer(self):
        """alpha.water = 0.5 contour, coloured by elevation. Missing when the solve is single-phase."""
        if self.volume is None or self.volume.GetPointData().GetArray('alpha.water') is None:
            raise RuntimeError('alpha.water was not written by this solve')
        surface = self.contour('alpha.water', 0.5)
        points = self.np.vtk_to_numpy(surface.GetPoints().GetData())
        self.put(surface.GetPointData(), 'elevation_m', points[:, 2])
        waterline = (self.plan.get('free_surface') or {}).get('waterline_z')
        self.layer('free_surface', 'isosurface', surface, 'volume/free_surface.vtp',
                   iso={'array': 'alpha.water', 'value': 0.5, 'unit': '1'},
                   color_by='elevation_m')
        if waterline is not None:
            self.layers[-1]['waterline_z'] = waterline

    def prepare_volume(self):
        self.derive_volume(self.volume.GetPointData())
        self.derive_volume(self.volume.GetCellData())

    def contour(self, array, value):
        if self.volume.GetPointData().GetArray(array) is None:
            raise RuntimeError(f'{array} was not written by this solve')
        contour = self.filters.vtkContourFilter()
        contour.SetInputData(self.volume)
        contour.SetInputArrayToProcess(0, 0, 0, 0, array)
        contour.SetValue(0, value)
        contour.SetComputeScalars(0)
        contour.Update()
        surface = contour.GetOutput()
        if surface.GetNumberOfCells() > CLOUD_TRIANGLES:
            triangles = self.filters.vtkTriangleFilter(); triangles.SetInputData(surface); triangles.Update()
            decimate = self.filters.vtkQuadricDecimation()
            decimate.SetInputData(triangles.GetOutput())
            decimate.SetTargetReduction(1 - CLOUD_TRIANGLES/surface.GetNumberOfCells())
            decimate.Update()
            surface = decimate.GetOutput()
        normals = self.filters.vtkPolyDataNormals()
        normals.SetInputData(surface)
        normals.SplittingOff()
        normals.Update()
        surface = normals.GetOutput()
        self.keep(surface.GetPointData(), {'U_mag', 'Normals', 'elevation_m'})
        self.keep(surface.GetCellData(), set())
        return surface

    def blobs(self, surface, limit=10):
        """Connected regions of an iso-surface: area, centroid and extent (for insights)."""
        if not surface.GetNumberOfCells():
            return []
        connectivity = self.filters.vtkPolyDataConnectivityFilter()
        connectivity.SetInputData(surface)
        connectivity.SetExtractionModeToAllRegions()
        connectivity.ColorRegionsOn()
        connectivity.Update()
        labelled = connectivity.GetOutput()
        triangles = self.filters.vtkTriangleFilter(); triangles.SetInputData(labelled); triangles.Update()
        poly = triangles.GetOutput()
        points = self.np.vtk_to_numpy(poly.GetPoints().GetData()).astype(float)
        region = self.np.vtk_to_numpy(poly.GetPointData().GetArray('RegionId'))
        cells = self.np.vtk_to_numpy(poly.GetPolys().GetConnectivityArray()).reshape(-1, 3)
        tri = points[cells]
        area = 0.5*np.linalg.norm(np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0]), axis=1)
        labels = region[cells[:, 0]]
        out = []
        for label in np.unique(labels):
            mask = labels == label
            pts = tri[mask].reshape(-1, 3)
            total = float(area[mask].sum())
            centroid = (tri[mask].mean(axis=1)*area[mask, None]).sum(axis=0)/max(total, 1e-300)
            out.append({'area_m2': total, 'centroid': centroid.tolist(), 'min': pts.min(axis=0).tolist(), 'max': pts.max(axis=0).tolist()})
        return sorted(out, key=lambda b: -b['area_m2'])[:limit]

    def pressure_cloud(self):
        if self.q_kin <= 0:
            raise RuntimeError('No freestream: total-pressure loss is undefined at zero speed')
        cloud = self.contour('Cpt', 0.0)
        if not cloud.GetNumberOfCells():
            raise RuntimeError('No total-pressure loss region (Cpt = 0) in the solved field')
        self.cloud = cloud
        self.layer('pressure_cloud', 'isosurface', cloud, 'volume/pressure_cloud.vtp', iso={'array': 'Cpt', 'value': 0.0},
                   color_by='U_mag', blobs=self.blobs(cloud))

    def q_criterion(self):
        if self.q_kin <= 0:
            raise RuntimeError('Q-criterion levels are normalised by the freestream; undefined at zero speed')
        levels = []
        for level in self.plan['outputs']['q_levels']:
            value = float(level)*(self.speed/self.length)**2
            surface = self.contour('Q', value)
            if not surface.GetNumberOfCells():
                continue
            relative = f'volume/q_{str(level).replace(".", "p")}.vtp'
            record = self.layer(f'q_{level}', 'isosurface', surface, relative, iso={'array': 'Q', 'value': value, 'normalized': level}, color_by='U_mag')
            levels.append(record['id'])
        if not levels:
            raise RuntimeError('No vortex regions above the requested Q-criterion levels')

    def noise(self):
        if not self.plan['outputs'].get('noise'):
            raise RuntimeError('Noise estimate not requested')
        if self.volume.GetPointData().GetArray('L_P') is None:
            raise RuntimeError('No acoustic power level field (laminar runs have no turbulence to estimate it from)')
        made = 0
        for level in self.plan.get('noise_levels_db', [60, 70, 80]):
            surface = self.contour('L_P', float(level))
            if surface.GetNumberOfCells():
                self.layer(f'noise_{level}', 'isosurface', surface, f'volume/noise_{level}.vtp',
                           iso={'array': 'L_P', 'value': float(level), 'unit': 'dB'}, color_by='U_mag', maturity='qualitative')
                made += 1
        if not made:
            raise RuntimeError('No region exceeds the lowest noise level')

    def frames(self):
        d = self.u_inf/self.speed if self.speed > 0 else np.asarray(self.plan['drag_axis'], float)
        lift = np.asarray(self.plan['lift_axis'], float)
        lift = lift - (lift@d)*d
        lift = lift/max(np.linalg.norm(lift), 1e-12)
        side = np.cross(lift, d)
        return d, lift, side

    def rakes(self):
        if self.speed <= 0:
            raise RuntimeError('Streamline rakes are seeded upstream; there is no freestream')
        if self.volume.GetPointData().GetArray('U') is None:
            raise RuntimeError('No velocity field')
        d, lift, side = self.frames()
        low, high = (np.asarray(b, float) for b in self.plan['model_bounds'])
        center = (low+high)/2
        corners = np.array([[x, y, z] for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])])
        extent = lambda axis: float(np.ptp(corners@axis))
        start = center - d*(extent(d)/2 + 0.3*self.length)
        height, width = max(extent(lift), 1e-6), max(extent(side), 1e-6)
        horizontal, vertical = self.plan['outputs']['rakes']
        made = []
        for kind, count in (('h', horizontal), ('v', vertical)):
            for i, t in enumerate(np.linspace(-0.45, 0.45, count) if count > 1 else [0.0][:count]):
                if kind == 'h':
                    origin = start + lift*t*height
                    a, b = origin - side*0.75*width, origin + side*0.75*width
                else:
                    origin = start + side*t*width
                    a, b = origin - lift*0.6*height, origin + lift*0.6*height
                line = self.sources.vtkLineSource()
                line.SetPoint1(*a); line.SetPoint2(*b); line.SetResolution(RAKE_SEEDS - 1)
                line.Update()
                tracer = self.flow.vtkStreamTracer()
                tracer.SetInputData(self.volume)
                tracer.SetSourceData(line.GetOutput())
                tracer.SetInputArrayToProcess(0, 0, 0, 0, 'U')
                tracer.SetIntegrationDirectionToForward()
                tracer.SetIntegratorTypeToRungeKutta45()
                tracer.SetIntegrationStepUnit(2)
                tracer.SetInitialIntegrationStep(0.5)
                tracer.SetMaximumNumberOfSteps(3000)
                domain = np.asarray(self.plan['domain_bounds'], float)
                tracer.SetMaximumPropagation(float(np.linalg.norm(domain[1]-domain[0])))
                tracer.Update()
                lines = tracer.GetOutput()
                if not lines.GetNumberOfCells():
                    continue
                self.keep(lines.GetPointData(), {'U_mag', 'Cpt', 'pressure_pa', 'IntegrationTime'})
                self.keep(lines.GetCellData(), {'SeedIds'})
                orientation = 'horizontal' if kind == 'h' else 'vertical'
                record = self.layer(f'rake_{kind}_{i}', 'streamlines', lines, f'rakes/{kind}_{i:02d}.vtp',
                                    orientation=orientation, offset=float(t), seeds=RAKE_SEEDS)
                made.append(record['id'])
        if not made:
            raise RuntimeError('No streamlines could be traced from the rakes')

    def section_region(self):
        low, high = (np.asarray(b, float) for b in self.plan['model_bounds'])
        d, _, _ = self.frames()
        margin = 0.5*self.length
        region_low = np.minimum(low, low + d*1.5*self.length) - margin
        region_high = np.maximum(high, high + d*1.5*self.length) + margin
        domain = np.asarray(self.plan['domain_bounds'], float)
        return np.maximum(region_low, domain[0]), np.minimum(region_high, domain[1])

    def sections(self):
        counts = self.plan['outputs']['sections_per_axis']
        low, high = self.section_region()
        geometry_filter = self.geometry
        made = 0
        for axis, name in enumerate('xyz'):
            n = int(counts[axis])
            positions = [float(low[axis] + (k+0.5)*(high[axis]-low[axis])/n) for k in range(n)]
            others = [a for a in range(3) if a != axis]
            span = high - low
            scale = SECTION_LONGEST/max(span[others[0]], span[others[1]], 1e-12)
            items = []
            for k, position in enumerate(positions):
                dims = [1, 1, 1]
                for a in others:
                    dims[a] = max(8, int(round(span[a]*scale)))
                bounds = []
                for a in range(3):
                    bounds += [position, position] if a == axis else [float(low[a]), float(high[a])]
                resample = self.filters.vtkResampleToImage()
                resample.SetInputDataObject(self.volume)
                resample.SetUseInputBounds(False)
                resample.SetSamplingBounds(bounds)
                resample.SetSamplingDimensions(*dims)
                resample.Update()
                image = resample.GetOutput()
                to_poly = geometry_filter.vtkImageDataGeometryFilter()
                to_poly.SetInputData(image)
                to_poly.Update()
                threshold = self.filters.vtkThreshold()
                threshold.SetInputData(to_poly.GetOutput())
                threshold.SetInputArrayToProcess(0, 0, 0, 0, 'vtkValidPointMask')
                threshold.SetLowerThreshold(1)
                threshold.SetUpperThreshold(255)
                threshold.SetThresholdFunction(self.filters.vtkThreshold.THRESHOLD_BETWEEN)
                threshold.AllScalarsOn()
                threshold.Update()
                surface = geometry_filter.vtkGeometryFilter()
                surface.SetInputData(threshold.GetOutput())
                surface.Update()
                poly = surface.GetOutput()
                if not poly.GetNumberOfCells():
                    continue
                self.keep(poly.GetPointData(), {'U_mag', 'pressure_pa', 'Cp', 'Cpt'})
                self.keep(poly.GetCellData(), set())
                relative = f'sections/{name}/{k:02d}.vtp'
                self.write(poly, relative)
                items.append({'index': k, 'position_m': position, 'path': relative, 'bytes': (self.out/relative).stat().st_size,
                              'cells': poly.GetNumberOfCells()})
                arrays = self.describe(poly)
            if items:
                made += len(items)
                self.layers.append({'id': f'sections_{name}', 'kind': 'section_stack', 'axis': name, 'items': items,
                                    'arrays': arrays, 'bytes': sum(i['bytes'] for i in items)})
        if not made:
            raise RuntimeError('No section plane intersects the fluid')

    # ---- renders -----------------------------------------------------------------------
    def render(self):
        import vtkmodules.vtkRenderingCore as rendering
        import vtkmodules.vtkRenderingOpenGL2  # noqa: F401  (registers the OpenGL backend)
        import vtkmodules.vtkRenderingFreeType  # noqa: F401
        import vtkmodules.vtkIOImage as image
        surface = getattr(self, 'object_surface', None)
        if surface is None:
            raise RuntimeError('No object surface to render')
        d, lift, side = self.frames()
        low, high = (np.asarray(b, float) for b in self.plan['model_bounds'])
        center = (low+high)/2
        radius = float(np.linalg.norm(high-low))/2
        lut = rendering.vtkColorTransferFunction()
        for t, rgb in ((-1.0, (0.231, 0.298, 0.753)), (0.0, (0.865, 0.865, 0.865)), (1.0, (0.706, 0.016, 0.150))):
            lut.AddRGBPoint(t, *rgb)
        views = {'front': (-d, lift), 'rear': (d, lift), 'side': (side, lift), 'top': (lift, d), 'iso': (-d + side + lift*0.8, lift)}
        layers = {'cp': None}
        if getattr(self, 'cloud', None) is not None:
            layers['cloud'] = self.cloud
        for layer_name, extra in layers.items():
            for view, (direction, up) in views.items():
                if layer_name == 'cloud' and view in ('front', 'rear'):
                    continue
                window = rendering.vtkRenderWindow()
                window.SetOffScreenRendering(1)
                window.SetSize(1600, 1000)
                renderer = rendering.vtkRenderer()
                renderer.SetBackground(1, 1, 1)
                window.AddRenderer(renderer)
                mapper = rendering.vtkPolyDataMapper()
                mapper.SetInputData(surface)
                actor = rendering.vtkActor()
                actor.SetMapper(mapper)
                if layer_name == 'cp' and surface.GetPointData().GetArray('Cp') is not None:
                    mapper.SetScalarModeToUsePointFieldData()
                    mapper.SelectColorArray('Cp')
                    mapper.SetLookupTable(lut)
                    mapper.SetScalarRange(-1, 1)
                    mapper.ScalarVisibilityOn()
                else:
                    mapper.ScalarVisibilityOff()
                    actor.GetProperty().SetColor(0.78, 0.82, 0.86)
                renderer.AddActor(actor)
                if extra is not None:
                    cloud_mapper = rendering.vtkPolyDataMapper()
                    cloud_mapper.SetInputData(extra)
                    cloud_mapper.ScalarVisibilityOff()
                    cloud = rendering.vtkActor()
                    cloud.SetMapper(cloud_mapper)
                    cloud.GetProperty().SetColor(0.86, 0.18, 0.2)
                    cloud.GetProperty().SetOpacity(0.55)
                    renderer.AddActor(cloud)
                direction = np.asarray(direction, float)/np.linalg.norm(direction)
                camera = renderer.GetActiveCamera()
                camera.SetFocalPoint(*center)
                camera.SetPosition(*(center + direction*radius*6))
                camera.SetViewUp(*up)
                renderer.ResetCamera()
                camera.Zoom(1.15)
                window.Render()
                grab = rendering.vtkWindowToImageFilter()
                grab.SetInput(window)
                grab.Update()
                relative = f'renders/{view}_{layer_name}.png'
                (self.out/'renders').mkdir(parents=True, exist_ok=True)
                writer = image.vtkPNGWriter()
                writer.SetFileName(str(self.out/relative))
                writer.SetInputConnection(grab.GetOutputPort())
                writer.Write()
                self.renders.append({'id': f'{view}_{layer_name}', 'view': view, 'layer': layer_name, 'path': relative,
                                     'array': 'Cp' if layer_name == 'cp' else None, 'width': 1600, 'height': 1000})
                if view == 'iso' and layer_name == 'cp':
                    window.SetSize(480, 300)
                    window.Render()
                    grab = rendering.vtkWindowToImageFilter(); grab.SetInput(window); grab.Update()
                    writer = image.vtkPNGWriter(); writer.SetFileName(str(self.out/'renders/thumb.png'))
                    writer.SetInputConnection(grab.GetOutputPort()); writer.Write()
                window.Finalize()

    # ---- transient animation -----------------------------------------------------------
    def animation(self):
        """Frames written by the surfaces function object: each surface becomes a base VTP (the
        first frame) plus one little-endian float32 file holding the per-cell values of every
        frame (frames x cells), so the viewer can step through them without re-reading geometry."""
        setup = self.plan['animation']
        root = self.case/setup['dir']
        def is_time(name):
            try:
                float(name)
                return True
            except ValueError:
                return False
        folders = sorted((p for p in root.iterdir() if p.is_dir() and is_time(p.name)), key=lambda p: float(p.name)) if root.is_dir() else []
        if not folders:
            raise RuntimeError('No animation frames were written')
        reader = self.xml.vtkXMLPolyDataReader()
        produced = 0
        for surface, field, name, unit in (('object', 'p', 'Cp', None), ('section', 'U', 'U_mag', 'm/s')):
            frames, base, cells = [], None, None
            for folder in folders:
                files = sorted(folder.glob(f'{surface}*.vtp'))
                if not files:
                    continue
                reader.SetFileName(str(files[0]))
                reader.Update()
                poly = reader.GetOutput()
                values = self.get(poly.GetCellData(), field)
                if values is None:
                    continue
                if values.ndim > 1:
                    values = np.linalg.norm(values, axis=1)
                elif name == 'Cp':
                    if self.q_kin <= 0:
                        continue
                    values = self.kinematic(values)/self.q_kin
                if cells is None:
                    cells = len(values)
                    base = self.data.vtkPolyData()
                    base.DeepCopy(poly)
                if len(values) != cells:
                    continue  # topology changed (should not happen on a static mesh); skip the frame
                frames.append((float(folder.name) - float(setup['start']), values.astype('<f4')))
            if not frames:
                continue
            step = max(1, int(np.ceil(len(frames)*cells*4/(ANIMATION_BUDGET_BYTES/2))))
            frames = frames[::step]
            data = np.stack([v for _, v in frames])
            relative = f'animation/{surface}_{name}.f32'
            target = self.out/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            data.tofile(target)
            self.keep(base.GetPointData(), set())
            self.keep(base.GetCellData(), set())
            self.put(base.GetCellData(), name, data[0])
            low, high = np.percentile(data, [1, 99]) if data.size else (0.0, 1.0)
            self.layer(f'animation_{surface}', 'animation', base, f'animation/{surface}.vtp',
                       frames={'times_s': [t for t, _ in frames], 'data': relative, 'array': name, 'association': 'cell',
                               'count': len(frames), 'values_per_frame': int(cells), 'unit': unit, 'range': [float(low), float(high)],
                               'bytes': int(target.stat().st_size), 'subsampled_every': step})
            produced += 1
        if not produced:
            raise RuntimeError('Animation frames contained no pressure or velocity data')

    def run(self):
        start = time.monotonic()
        try:
            self.read()
        finally:
            foam_cleanup(self.case)
        self.attempt('surface', self.surface)
        if self.plan['outputs'].get('friction_lines', True):
            self.attempt('friction_lines', self.friction_lines)
        self.attempt('volume_fields', self.prepare_volume)
        self.attempt('pressure_cloud', self.pressure_cloud)
        self.attempt('q_criterion', self.q_criterion)
        self.attempt('noise_cloud', self.noise)
        self.attempt('rakes', self.rakes)
        self.attempt('sections', self.sections)
        if self.plan['outputs'].get('renders', True):
            self.attempt('renders', self.render)
        if self.plan.get('animation'):
            self.attempt('animation', self.animation)
        if self.plan.get('free_surface'):
            self.attempt('free_surface', self.free_surface_layer)
        record = {'schema': 'gustsim.layers/1', 'time': self.time, 'basis': self.basis, 'layers': self.layers, 'renders': self.renders,
                  'missing': self.missing, 'timings': self.timings, 'seconds': round(time.monotonic()-start, 3),
                  'vtk_version': self.core.vtkVersion.GetVTKVersion()}
        (self.out/'layers.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
        return record


def main(argv=None):
    argv = argv or sys.argv[1:]
    case, out, plan = Path(argv[0]), Path(argv[1]), json.loads(Path(argv[2]).read_text(encoding='utf-8'))
    return Package(case, out, plan).run()


if __name__ == '__main__':
    main()
