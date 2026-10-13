"""pvbatch: write an STL of the cells where |ω|·h/U∞ exceeds the threshold.

Arguments: case directory, output directory, freestream speed, threshold.
Writes indicator.stl and indicator.json. The solved case is not modified.

The indicator is a cell quantity: |ω| is the cell-centred vorticity and h = V^(1/3) is the size
of that same cell. It is interpolated to the points explicitly (cell → point) only to contour it.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def cell_sizes(volume):
    """h = |V|^(1/3) for every cell of an unstructured grid."""
    import numpy as np
    from vtkmodules.util import numpy_support
    from vtkmodules.vtkFiltersVerdict import vtkCellSizeFilter
    sizes = vtkCellSizeFilter()
    sizes.SetInputData(volume)
    sizes.SetComputeVertexCount(False)
    sizes.SetComputeLength(False)
    sizes.SetComputeArea(False)
    sizes.SetComputeVolume(True)
    sizes.SetComputeSum(False)
    sizes.Update()
    array = sizes.GetOutput().GetCellData().GetArray('Volume')
    if array is None:
        raise RuntimeError('Cell volumes could not be computed')
    return np.cbrt(np.abs(numpy_support.vtk_to_numpy(array).astype(float)))


def indicator_surface(volume, speed, threshold):
    """(contour polydata, cell indicator array) for a grid with cell-data 'vorticity'."""
    import numpy as np
    from vtkmodules.util import numpy_support
    from vtkmodules.vtkCommonDataModel import vtkDataObject
    from vtkmodules.vtkFiltersCore import vtkCellDataToPointData, vtkContourFilter
    vorticity = volume.GetCellData().GetArray('vorticity')
    if vorticity is None:
        raise RuntimeError('vorticity was not written as a cell field; the output function objects must include it')
    values = numpy_support.vtk_to_numpy(vorticity).astype(float)
    magnitude = np.linalg.norm(values, axis=1) if values.ndim > 1 else np.abs(values)
    h = cell_sizes(volume)
    if len(h) != len(magnitude):
        raise RuntimeError('Cell sizes and vorticity have different lengths')
    indicator = magnitude * h / max(abs(speed), 1e-12)
    array = numpy_support.numpy_to_vtk(np.ascontiguousarray(indicator, dtype=np.float64), deep=1)
    array.SetName('indicator')
    volume.GetCellData().AddArray(array)
    to_points = vtkCellDataToPointData()
    to_points.SetInputData(volume)
    to_points.PassCellDataOff()
    to_points.Update()
    contour = vtkContourFilter()
    contour.SetInputConnection(to_points.GetOutputPort())
    contour.SetInputArrayToProcess(0, 0, 0, vtkDataObject.FIELD_ASSOCIATION_POINTS, 'indicator')
    contour.SetValue(0, threshold)
    contour.Update()
    return contour.GetOutput(), indicator


def main(argv):
    from foamread import cleanup as foam_cleanup, foam_file
    from vtkmodules.vtkIOGeometry import vtkOpenFOAMReader, vtkSTLWriter
    case, out, speed, threshold = Path(argv[1]), Path(argv[2]), float(argv[3]), float(argv[4])
    out.mkdir(parents=True, exist_ok=True)
    reader = vtkOpenFOAMReader()
    reader.SetFileName(str(foam_file(case)))
    if hasattr(reader, 'SetDecomposePolyhedra'):
        reader.SetDecomposePolyhedra(1)
    reader.UpdateInformation()
    reader.EnableAllCellArrays()
    # Cell data only: the indicator pairs each cell's vorticity with that cell's size.
    reader.SetCreateCellToPoint(0)
    values = reader.GetTimeValues()
    times = [values.GetValue(i) for i in range(values.GetNumberOfTuples())] if values else []
    times = [t for t in times if t > 0]
    if not times:
        raise RuntimeError('No solved time directories were written')
    reader.UpdateTimeStep(max(times))
    reader.Update()
    output = reader.GetOutput()

    def find(block, name):
        if block is None or not hasattr(block, 'GetNumberOfBlocks'):
            return None
        for i in range(block.GetNumberOfBlocks()):
            meta = block.GetMetaData(i)
            if meta is not None and meta.Get(output.NAME()) == name:
                return block.GetBlock(i)
            found = find(block.GetBlock(i), name)
            if found is not None:
                return found
        return None

    volume = find(output, 'internalMesh')
    foam_cleanup(case)
    if volume is None:
        raise RuntimeError('The solved volume is not readable')
    surface, indicator = indicator_surface(volume, speed, threshold)
    if surface.GetNumberOfCells() < 1:
        raise RuntimeError(f'No cells exceed the indicator threshold {threshold:g}')
    target = out / 'indicator.stl'
    writer = vtkSTLWriter()
    writer.SetFileName(str(target))
    writer.SetInputData(surface)
    writer.Write()
    (out / 'indicator.json').write_text(json.dumps({
        'triangles': int(surface.GetNumberOfCells()), 'threshold': threshold,
        'cells_above': int((indicator > threshold).sum()), 'cells': int(len(indicator)),
        'definition': '|vorticity| * cell volume^(1/3) / U_inf, cell data; contoured after cell-to-point interpolation'}),
        encoding='utf-8')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv))
    except Exception as error:
        print(f'amr indicator failed: {error}', file=sys.stderr)
        raise SystemExit(1)
