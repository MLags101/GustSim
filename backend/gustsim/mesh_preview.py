"""Small cross-sectional volume-mesh preview, without invented flow fields."""
def create(case,origin):
    import vtk
    reader=vtk.vtkOpenFOAMReader();reader.SetFileName(str(case/'gustsim.foam'));reader.SetSkipZeroTime(False)
    reader.UpdateInformation();reader.DisableAllCellArrays();reader.DisableAllPatchArrays();reader.SetPatchArrayStatus('internalMesh',1);reader.Update()
    geometry=vtk.vtkCompositeDataGeometryFilter();geometry.SetInputConnection(reader.GetOutputPort());geometry.Update()
    # Slice the actual internal volume cells to expose mesh resolution and surface fit.
    plane=vtk.vtkPlane();plane.SetOrigin(*origin);plane.SetNormal(0,1,0)
    cut=vtk.vtkCutter();cut.SetInputConnection(reader.GetOutputPort());cut.SetCutFunction(plane);cut.Update()
    flatten=vtk.vtkCompositeDataGeometryFilter();flatten.SetInputConnection(cut.GetOutputPort());flatten.Update()
    if flatten.GetOutput().GetNumberOfCells()==0:
        raise ValueError('Mesh section is empty; check the fluid point and retained volume')
    writer=vtk.vtkXMLPolyDataWriter();writer.SetFileName(str(case/'mesh-preview.vtp'));writer.SetInputData(flatten.GetOutput());writer.SetDataModeToBinary()
    if not writer.Write(): raise ValueError('Mesh preview could not be written')
