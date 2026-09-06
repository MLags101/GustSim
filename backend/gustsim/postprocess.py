"""Executed by ParaView pvbatch, whose Python environment owns paraview/vtk."""
import json
import sys
from pathlib import Path

def main():
    import numpy as np
    from paraview.simple import (OpenFOAMReader, Calculator, ExtractSurface, GenerateSurfaceNormals, SaveData,
        SaveState, Slice, Clip, StreamTracer, Glyph, PlotOverLine, ProbeLocation, Gradient, CreateView, Show,
        ColorBy, GetColorTransferFunction, GetScalarBar, ResetCamera, SaveScreenshot, GetAnimationScene)
    from paraview import servermanager
    from vtkmodules.vtkFiltersCore import vtkAppendFilter
    from vtkmodules.util.numpy_support import vtk_to_numpy
    case=Path(sys.argv[1]); output=Path(sys.argv[2]); output.mkdir(parents=True,exist_ok=True)
    spec=json.loads((case/"simulation.json").read_text())
    request=json.loads(Path(sys.argv[3]).read_text()) if len(sys.argv)>3 else {"kind":"surface","field":"pressure_pa"}
    reader=OpenFOAMReader(FileName=str(case/"gustsim.foam"))
    reader.UpdatePipelineInformation()
    regions=list(reader.MeshRegions.Available)
    geometry=json.loads((case/'geometry.json').read_text())
    surface_names={p['name'] for p in geometry['patches']}
    if request.get('patches'):
        if not set(request['patches']) <= surface_names: raise ValueError('Extraction references unknown surfaces')
        surface_names=set(request['patches'])
    surface_regions=[r for r in regions if r.split('/')[-1] in surface_names]
    reader.MeshRegions=surface_regions if request['kind']=='surface' and request.get('field')!='vorticity' else ['internalMesh']
    selected_fields=[f for f in ("p","U","yPlus","k","omega") if f in reader.CellArrays.Available]
    reader.CellArrays=selected_fields
    times=list(reader.TimestepValues)
    if not times or max(times)<=0:raise RuntimeError("No solved time directories were written")
    time=max(times);reader.UpdatePipeline(time)
    calc=Calculator(Input=reader);calc.AttributeType="Cell Data";calc.ResultArrayName="pressure_pa"
    calc.Function=f"p * {spec['fluid']['density']}";calc.UpdatePipeline(time)
    source=calc
    if request.get('field')=='vorticity':
        grad=Gradient(Input=calc);grad.ScalarArray=['CELLS','U'];grad.ComputeVorticity=1;grad.VorticityArrayName='vorticity';source=grad
    kind=request['kind']; origin=request.get('origin',[0,0,0]);normal=request.get('normal',[0,0,1])
    if kind=='slice':
        source=Slice(Input=source);source.SliceType='Plane';source.SliceType.Origin=origin;source.SliceType.Normal=normal
    elif kind=='clip':
        source=Clip(Input=source);source.ClipType='Plane';source.ClipType.Origin=origin;source.ClipType.Normal=normal
    elif kind=='streamlines':
        source=StreamTracer(Input=source,SeedType='Point Cloud');source.Vectors=['POINTS','U'];source.SeedType.Center=origin;source.SeedType.Radius=request.get('seed_radius',0.2);source.SeedType.NumberOfPoints=min(request.get('resolution',100),300)
        source.IntegrationDirection='BOTH'
        source.MaximumStreamlineLength=float(np.linalg.norm(np.asarray(spec['domain']['maximum'])-np.asarray(spec['domain']['minimum'])))*2
    elif kind=='glyphs':
        source=Glyph(Input=source,GlyphType='Arrow');source.OrientationArray=['POINTS','U'];source.ScaleArray=['POINTS','U'];source.MaximumNumberOfSamplePoints=min(request.get('resolution',100),2000)
    elif kind=='line':
        source=PlotOverLine(Input=source);source.Point1=origin;source.Point2=request.get('end',[1,0,0]);source.Resolution=request.get('resolution',100)
    elif kind=='probe':
        source=ProbeLocation(Input=source);source.ProbeType.Center=origin
    source.UpdatePipeline(time)
    if source.GetDataInformation().GetNumberOfPoints()==0:
        raise RuntimeError('This view is empty. Move the streamline seeds into the fluid, enlarge the seed radius, or choose a section that intersects the domain.')
    surf=ExtractSurface(Input=source);surf.UpdatePipeline(time)
    # Convert composite output to one dataset before writing browser PolyData.
    from paraview.simple import MergeBlocks, CellDatatoPointData, Triangulate
    merged=MergeBlocks(Input=surf);merged.UpdatePipeline(time)
    surface=ExtractSurface(Input=merged)
    display=CellDatatoPointData(Input=surface);display.PassCellData=1;display.UpdatePipeline(time)
    SaveData(str(output/'samples.csv'),proxy=display,FieldAssociation='Point Data',Precision=12)
    SaveData(str(output/'surface.vtp'),proxy=display)
    if (output/'surface.vtp').stat().st_size>40*1024*1024:
        from paraview.simple import Decimate
        reduced=Decimate(Input=Triangulate(Input=display));reduced.TargetReduction=0.85;reduced.UpdatePipeline(time)
        SaveData(str(output/'surface.vtp'),proxy=reduced)
        if (output/'surface.vtp').stat().st_size>40*1024*1024:
            raise RuntimeError('Extract exceeds the 40 MB browser budget; use a smaller slice or clip')
    if len(sys.argv)<=3:
        # Full volume export uses internalMesh only; surface patches are exported separately.
        volume_reader=OpenFOAMReader(FileName=str(case/'gustsim.foam'));volume_reader.UpdatePipelineInformation();volume_reader.MeshRegions=['internalMesh'];volume_reader.CellArrays=selected_fields
        volume=Calculator(Input=volume_reader);volume.AttributeType='Cell Data';volume.ResultArrayName='pressure_pa';volume.Function=calc.Function;volume.UpdatePipeline(time)
        flat=MergeBlocks(Input=volume);flat.UpdatePipeline(time)
        SaveData(str(output/'volume.vtu'),proxy=flat)
        data=servermanager.Fetch(flat)
        arrays={"points":vtk_to_numpy(data.GetPoints().GetData()),"connectivity":vtk_to_numpy(data.GetCells().GetConnectivityArray()),
                "offsets":vtk_to_numpy(data.GetCells().GetOffsetsArray()),"cell_types":vtk_to_numpy(data.GetCellTypesArray())}
        for association in ('cell','point'):
            attrs=data.GetCellData() if association=='cell' else data.GetPointData()
            for i in range(attrs.GetNumberOfArrays()):
                a=attrs.GetArray(i)
                if a is not None:arrays[association+'__'+a.GetName()]=vtk_to_numpy(a)
        for name,method in [('polyhedron_faces','GetFaces'),('polyhedron_face_locations','GetFaceLocations')]:
            if hasattr(data,method):
                a=getattr(data,method)()
                if a is not None:arrays[name]=vtk_to_numpy(a)
        np.savez_compressed(output/'fields.npz',**arrays)
        summary={'iteration':time,'min_pressure_pa':float(np.min(arrays['cell__pressure_pa']))}
        surface_data=servermanager.Fetch(display)
        wall_yplus=surface_data.GetPointData().GetArray('yPlus') if hasattr(surface_data,'GetPointData') else None
        if wall_yplus is not None:
            yp=vtk_to_numpy(wall_yplus)
            summary['yplus_range']=[float(np.min(yp)),float(np.max(yp))]
        (output/'field_summary.json').write_text(json.dumps(summary))
    view=CreateView('RenderView');view.ViewSize=[1500,1000];view.UseColorPaletteForBackground=0;view.Background=[0.055,0.073,0.095]
    rep=Show(display,view);field=request.get('field','pressure_pa');ColorBy(rep,('POINTS',field))
    rep.RescaleTransferFunctionToDataRange(True,False);rep.SetScalarBarVisibility(view,True)
    GetColorTransferFunction(field).ApplyPreset('Cool to Warm',True)
    if request.get('color_range'):GetColorTransferFunction(field).RescaleTransferFunction(*request['color_range'])
    GetScalarBar(GetColorTransferFunction(field),view).Title='Pressure [Pa gauge]' if field=='pressure_pa' else field
    ResetCamera(view);SaveScreenshot(str(output/'view.png'),view)
    SaveState(str(output/'view.pvsm'))

if __name__=='__main__':main()
