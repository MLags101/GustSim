"""Orthogonal sections and the exterior of actual volume cells."""
def create(case,origin):
    import vtk
    reader=vtk.vtkOpenFOAMReader();reader.SetFileName(str(case/'gustsim.foam'));reader.SetSkipZeroTime(False)
    reader.UpdateInformation();reader.DisableAllCellArrays();reader.DisableAllPatchArrays();reader.SetPatchArrayStatus('internalMesh',1);reader.Update()
    def save(poly,name):
        if poly.GetNumberOfCells()==0:raise ValueError('Mesh section is empty; inspect the retained fluid volume')
        writer=vtk.vtkXMLPolyDataWriter();writer.SetFileName(str(case/name));writer.SetInputData(poly);writer.SetDataModeToBinary()
        if not writer.Write():raise ValueError('Mesh preview could not be written')
    geometry=vtk.vtkCompositeDataGeometryFilter();geometry.SetInputConnection(reader.GetOutputPort());geometry.Update()
    save(geometry.GetOutput(),'mesh-exterior.vtp')
    for axis,normal in [('x',(1,0,0)),('y',(0,1,0)),('z',(0,0,1))]:
        plane=vtk.vtkPlane();plane.SetOrigin(*origin);plane.SetNormal(*normal)
        cut=vtk.vtkCutter();cut.SetInputConnection(reader.GetOutputPort());cut.SetCutFunction(plane);cut.Update()
        flatten=vtk.vtkCompositeDataGeometryFilter();flatten.SetInputConnection(cut.GetOutputPort());flatten.Update()
        if axis=='y':save(flatten.GetOutput(),'mesh-preview.vtp')
        if flatten.GetOutput().GetNumberOfCells():save(flatten.GetOutput(),f'mesh-section-{axis}.vtp')
