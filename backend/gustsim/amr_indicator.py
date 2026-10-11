"""pvbatch: write an STL of the cells where |ω|·h/U∞ exceeds the threshold.

Arguments: case directory, output directory, freestream speed, threshold.
Writes indicator.stl and indicator.json. The solved case is not modified.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foamread import cleanup as foam_cleanup, foam_file  # noqa: E402


def main(argv):
    import numpy as np
    from vtkmodules.vtkFiltersCore import vtkContourFilter
    from vtkmodules.vtkIOGeometry import vtkSTLWriter
    from vtkmodules.vtkIOGeometry import vtkOpenFOAMReader
    case, out, speed, threshold = Path(argv[1]), Path(argv[2]), float(argv[3]), float(argv[4])
    out.mkdir(parents=True, exist_ok=True)
    reader = vtkOpenFOAMReader()
    reader.SetFileName(str(foam_file(case)))
    if hasattr(reader, 'SetDecomposePolyhedra'):
        reader.SetDecomposePolyhedra(1)
    reader.UpdateInformation()
    reader.EnableAllCellArrays()
    reader.SetCreateCellToPoint(1)
    values = reader.GetTimeValues()
    times = [values.GetValue(i) for i in range(values.GetNumberOfTuples())] if values else []
    times = [t for t in times if t > 0]
    if not times:
        raise RuntimeError('No solved time directories were written')
    reader.UpdateTimeStep(max(times))
    reader.Update()
    volume = None
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
    vorticity = volume.GetCellData().GetArray('vorticity') or volume.GetPointData().GetArray('vorticity')
    if vorticity is None:
        raise RuntimeError('vorticity was not written; the output function objects must include it')
    from vtkmodules.util import numpy_support
    values = numpy_support.vtk_to_numpy(vorticity).astype(float)
    magnitude = np.linalg.norm(values, axis=1) if values.ndim > 1 else values
    if volume.GetCellData().GetArray('vorticity') is not None:
        size = np.full(len(magnitude), volume.GetCell(0).GetLength2() ** 0.5 if volume.GetNumberOfCells() else 1.0)
    else:
        size = np.full(len(magnitude), 1.0)
    indicator = magnitude * size / max(speed, 1e-12)
    array = numpy_support.numpy_to_vtk(np.ascontiguousarray(indicator, dtype=np.float32), deep=1)
    array.SetName('indicator')
    volume.GetPointData().AddArray(array) if volume.GetPointData().GetArray('vorticity') is not None else volume.GetCellData().AddArray(array)
    contour = vtkContourFilter()
    contour.SetInputData(volume)
    contour.SetInputArrayToProcess(0, 0, 0, 0 if volume.GetPointData().GetArray('indicator') is not None else 1, 'indicator')
    contour.SetValue(0, threshold)
    contour.Update()
    surface = contour.GetOutput()
    if surface.GetNumberOfCells() < 1:
        raise RuntimeError(f'No cells exceed the indicator threshold {threshold:g}')
    target = out / 'indicator.stl'
    writer = vtkSTLWriter()
    writer.SetFileName(str(target))
    writer.SetInputData(surface)
    writer.Write()
    (out / 'indicator.json').write_text(json.dumps({'triangles': int(surface.GetNumberOfCells()), 'threshold': threshold}), encoding='utf-8')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv))
    except Exception as error:
        print(f'amr indicator failed: {error}', file=sys.stderr)
        raise SystemExit(1)
