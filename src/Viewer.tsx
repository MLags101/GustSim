import { useEffect, useRef, useState } from 'react';
import '@kitware/vtk.js/Rendering/Profiles/Geometry';
import vtkGenericRenderWindow from '@kitware/vtk.js/Rendering/Misc/GenericRenderWindow';
import vtkPolyData from '@kitware/vtk.js/Common/DataModel/PolyData';
import vtkMapper from '@kitware/vtk.js/Rendering/Core/Mapper';
import vtkActor from '@kitware/vtk.js/Rendering/Core/Actor';
import vtkCellPicker from '@kitware/vtk.js/Rendering/Core/CellPicker';
import vtkXMLPolyDataReader from '@kitware/vtk.js/IO/XML/XMLPolyDataReader';
import vtkColorTransferFunction from '@kitware/vtk.js/Rendering/Core/ColorTransferFunction';
import vtkColorMaps from '@kitware/vtk.js/Rendering/Core/ColorTransferFunction/ColorMaps';
import { api } from './api';
import vtkDataArray from '@kitware/vtk.js/Common/Core/DataArray';
import vtkInteractorStyleManipulator from '@kitware/vtk.js/Interaction/Style/InteractorStyleManipulator';
import vtkMouseCameraTrackballRotateManipulator from '@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballRotateManipulator';
import vtkMouseCameraTrackballPanManipulator from '@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballPanManipulator';
import vtkMouseCameraTrackballZoomManipulator from '@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballZoomManipulator';
import vtkOrientationMarkerWidget from '@kitware/vtk.js/Interaction/Widgets/OrientationMarkerWidget';
import vtkAxesActor from '@kitware/vtk.js/Rendering/Core/AxesActor';
import {tracksFromPolyData,sampleTrack} from './streamlinePlayback';
import {overlayGeometry} from './overlays';

export default function Viewer({geometryId, resultUrl, field='pressure_pa', colorRange, wireframe=false, onPick, reset=0, selectedGroups=[],hiddenGroups=[],rotation,domain,flow,modelBounds,cameraPose='iso',objectGeometryId,animate=false}: {
  flow?:any;modelBounds?:number[][];cameraPose?:string;objectGeometryId?:string;animate?:boolean;geometryId?: string; resultUrl?: string; field?: string; colorRange?: [number,number]; wireframe?:boolean; onPick?:(group:number)=>void; reset?:number;selectedGroups?:number[];hiddenGroups?:number[];rotation?:any;domain?:any;
}) {
  const container = useRef<HTMLDivElement>(null);
  const cameraState=useRef<any>(undefined);
  const pickRef = useRef(onPick); pickRef.current = onPick;
  const [error,setError]=useState('');
  const [range,setRange]=useState<number[]>([]);
  useEffect(()=>{
    if (!container.current) return;
    let disposed=false,animationFrame=0;
    const render = vtkGenericRenderWindow.newInstance({background:[0.055,0.073,0.095]});
    render.setContainer(container.current);
    const renderer=render.getRenderer(), window=render.getRenderWindow();
    const mapper=vtkMapper.newInstance(), actor=vtkActor.newInstance();
    actor.setMapper(mapper); actor.getProperty().setColor(0.61,0.73,0.78);
    actor.getProperty().setSpecular(0.3); actor.getProperty().setSpecularPower(25);
    actor.getProperty().setEdgeVisibility(wireframe);
    actor.getProperty().setEdgeColor(0.14,0.24,0.28);
    if(resultUrl){actor.getProperty().setAmbient(.7);actor.getProperty().setDiffuse(.3);}
    if(resultUrl?.includes('/mesh-'))actor.getProperty().setEdgeColor(.36,.56,.65);
    renderer.addActor(actor);
    let groups:number[]=[];
    const owned:any[]=[];
    const picker=vtkCellPicker.newInstance();
    picker.setPickFromList(true); picker.addPickList(actor);

    const interactor=render.getInteractor();
    const canvas=container.current.querySelector('canvas');
    const onContextMenu=(e:MouseEvent)=>e.preventDefault();
    canvas?.addEventListener('contextmenu',onContextMenu);

    const iStyle=vtkInteractorStyleManipulator.newInstance();
    iStyle.addMouseManipulator(vtkMouseCameraTrackballRotateManipulator.newInstance({button:1}));
    iStyle.addMouseManipulator(vtkMouseCameraTrackballPanManipulator.newInstance({button:3}));
    iStyle.addMouseManipulator(vtkMouseCameraTrackballPanManipulator.newInstance({button:2}));
    iStyle.addMouseManipulator(vtkMouseCameraTrackballPanManipulator.newInstance({button:1,shift:true}));
    iStyle.addMouseManipulator(vtkMouseCameraTrackballZoomManipulator.newInstance({scrollEnabled:true}));
    iStyle.addMouseManipulator(vtkMouseCameraTrackballZoomManipulator.newInstance({button:3,control:true}));
    interactor.setInteractorStyle(iStyle);

    const axes=vtkAxesActor.newInstance();
    const orientationWidget=vtkOrientationMarkerWidget.newInstance({
      actor:axes,
      interactor,
    });
    orientationWidget.setParentRenderer(renderer);
    orientationWidget.setEnabled(true);
    orientationWidget.setViewportCorner(vtkOrientationMarkerWidget.Corners.BOTTOM_LEFT);
    orientationWidget.setViewportSize(0.16);
    orientationWidget.setMinPixelSize(70);
    orientationWidget.setMaxPixelSize(120);

    let downPos:{x:number;y:number}|null=null;
    const subDown=interactor.onLeftButtonPress((event:any)=>{
      if(event.position) downPos={x:event.position.x,y:event.position.y};
    });
    const subUp=interactor.onLeftButtonRelease((event:any)=>{
      if(!downPos||!event.position||!groups.length) return;
      const dx=event.position.x-downPos.x, dy=event.position.y-downPos.y;
      downPos=null;
      if(Math.hypot(dx,dy)>5||event.shiftKey||event.controlKey||event.altKey) return;
      picker.pick([event.position.x,event.position.y,0],renderer);
      const id=picker.getCellId();
      if(id>=0) pickRef.current?.(groups[id]);
    });
    const observer=new ResizeObserver(()=>render.resize()); observer.observe(container.current);
    setError(''); setRange([]);
    async function load(){
      try{
        let poly:any;
        if(resultUrl){
          const reader=vtkXMLPolyDataReader.newInstance(); owned.push(reader);
          const response=await fetch(resultUrl); if(!response.ok) throw new Error('Result surface is unavailable');
          const bytes=await response.arrayBuffer(); if(disposed)return;
          reader.parseAsArrayBuffer(bytes); poly=reader.getOutputData(0);
          const array=poly.getPointData().getArrayByName(field) || poly.getCellData().getArrayByName(field);
          if(array){
            const isPoint=!!poly.getPointData().getArrayByName(field);
            mapper.setScalarMode(isPoint?3:4); mapper.setColorByArrayName(field); mapper.setScalarVisibility(true);
            const extent=colorRange || array.getRange(array.getNumberOfComponents()>1?-1:0);
            const lut=vtkColorTransferFunction.newInstance(); owned.push(lut);
            const preset=vtkColorMaps.getPresetByName('Cool to Warm'); if(preset)lut.applyColorMap(preset);
            lut.setMappingRange(extent[0],extent[1] || extent[0]+1); lut.updateRange();
            mapper.setLookupTable(lut); mapper.setUseLookupTableScalarRange(true); setRange([...extent]);
          } else { mapper.setScalarVisibility(false); if(!resultUrl.includes('/mesh-'))setError(`Field ${field} is not available in this extract.`); }
        } else if(geometryId){
          const data=await api(`/geometry/${geometryId}/scene`); if(disposed)return;
          poly=vtkPolyData.newInstance(); owned.push(poly);
          poly.getPoints().setData(new Float32Array(data.points),3);
          const hidden=new Set(hiddenGroups),selected=new Set(selectedGroups),rotor=new Set((data.patches||[]).filter((p:any)=>rotation?.enabled&&rotation.patches.includes(p.name)).map((p:any)=>p.group));
          const polys:number[]=[],colors:number[]=[];let cell=0;
          for(let i=0;i<data.polys.length;){const n=data.polys[i],g=data.groups[cell++];if(!hidden.has(g)){polys.push(...data.polys.slice(i,i+n+1));groups.push(g);colors.push(...(selected.has(g)?[113,225,195]:rotor.has(g)?[237,186,118]:[156,186,199]));}i+=n+1;}
          poly.getPolys().setData(new Uint32Array(polys));
          const colorsArray=vtkDataArray.newInstance({name:'componentColors',numberOfComponents:3,values:new Uint8Array(colors)});owned.push(colorsArray);poly.getCellData().setScalars(colorsArray);mapper.setScalarMode(2);mapper.setColorModeToDirectScalars();mapper.setScalarVisibility(true);
        } else return;
        if(disposed)return;
        mapper.setInputData(poly);
        if(objectGeometryId&&resultUrl){
          const data=await api(`/geometry/${objectGeometryId}/scene`);if(disposed)return;
          const object=vtkPolyData.newInstance();owned.push(object);object.getPoints().setData(new Float32Array(data.points),3);object.getPolys().setData(new Uint32Array(data.polys));
          const objectMapper=vtkMapper.newInstance();owned.push(objectMapper);objectMapper.setInputData(object);objectMapper.setScalarVisibility(false);
          const objectActor=vtkActor.newInstance();owned.push(objectActor);objectActor.setMapper(objectMapper);objectActor.getProperty().setColor(.64,.7,.73);objectActor.getProperty().setOpacity(.8);objectActor.setPickable(false);renderer.addActor(objectActor);
        }
        if(animate){
          const tracks=tracksFromPolyData(poly);
          if(!tracks.length)setError('No integration-time streamline data is available for animation. Extract Streamlines first.');
          else{
            const particles=vtkPolyData.newInstance();owned.push(particles);particles.getPoints().setData(new Float32Array(tracks.length*3),3);particles.getVerts().setData(new Uint32Array(tracks.flatMap((_,i)=>[1,i])));
            const pm=vtkMapper.newInstance();owned.push(pm);pm.setInputData(particles);pm.setScalarVisibility(false);
            const pa=vtkActor.newInstance();owned.push(pa);pa.setMapper(pm);pa.getProperty().setColor(1,.85,.25);pa.getProperty().setPointSize(6);pa.getProperty().setAmbient(1);pa.setPickable(false);renderer.addActor(pa);
            const started=performance.now();
            const frame=(now:number)=>{if(disposed)return;const values=particles.getPoints().getData() as Float32Array;tracks.forEach((track,i)=>values.set(sampleTrack(track,((now-started)/8000+i/tracks.length)%1),i*3));particles.getPoints().modified();particles.modified();window.render();animationFrame=requestAnimationFrame(frame);};animationFrame=requestAnimationFrame(frame);
          }
        }
        renderer.resetCamera();
        const camera=renderer.getActiveCamera();const saved=cameraState.current;const sceneKey=resultUrl||geometryId;
        if(saved?.key===sceneKey&&saved.reset===reset&&saved.pose===cameraPose){camera.setPosition(...saved.position as [number,number,number]);camera.setFocalPoint(...saved.focal as [number,number,number]);camera.setViewUp(...saved.up as [number,number,number]);camera.setParallelScale(saved.scale);}
        else{if(cameraPose==='iso'){camera.azimuth(25);camera.elevation(20);camera.setViewUp(0,0,1);}else{const f=camera.getFocalPoint(),distance=Math.hypot(...camera.getPosition().map((n:number,i:number)=>n-f[i]));const axis=['x','y','z'].indexOf(cameraPose);camera.setPosition(...f.map((n:number,i:number)=>n+(i===axis?distance:0)) as [number,number,number]);camera.setViewUp(...(axis===2?[0,1,0]:[0,0,1]) as [number,number,number]);}}
        if(!resultUrl&&(rotation?.enabled||domain||flow)){const overlay=overlayGeometry(rotation,domain,flow,modelBounds);owned.push(overlay);const overlayMapper=vtkMapper.newInstance();owned.push(overlayMapper);overlayMapper.setInputData(overlay);const overlayActor=vtkActor.newInstance();owned.push(overlayActor);overlayActor.setMapper(overlayMapper);overlayActor.getProperty().setColor(.97,.79,.5);overlayActor.getProperty().setLineWidth(2);overlayActor.setPickable(false);renderer.addActor(overlayActor);}
        renderer.resetCameraClippingRange();render.resize();window.render();
      }catch(e){if(!disposed)setError(String(e));}
    }
    load();
    return ()=>{const camera=renderer.getActiveCamera();cameraState.current={key:resultUrl||geometryId,reset,pose:cameraPose,position:[...camera.getPosition()],focal:[...camera.getFocalPoint()],up:[...camera.getViewUp()],scale:camera.getParallelScale()};disposed=true;cancelAnimationFrame(animationFrame);observer.disconnect();canvas?.removeEventListener('contextmenu',onContextMenu);subDown.unsubscribe();subUp.unsubscribe();orientationWidget.setEnabled(false);orientationWidget.delete();axes.delete();iStyle.delete();picker.delete();renderer.removeActor(actor);actor.delete();mapper.delete();owned.forEach(v=>v.delete());render.delete();};
  },[geometryId,resultUrl,field,colorRange?.[0],colorRange?.[1],wireframe,reset,JSON.stringify(selectedGroups),JSON.stringify(hiddenGroups),JSON.stringify(rotation),JSON.stringify(domain),JSON.stringify(flow),JSON.stringify(modelBounds),cameraPose,objectGeometryId,animate]);
  return <div className="viewport"><div className="vtk-container" ref={container}/>{error&&<div className="viewport-notice">{error}</div>}
    {!geometryId&&!resultUrl&&<div className="viewport-empty"><div className="empty-cross">＋</div><h2>Your next simulation starts here</h2><p>Import a STEP or STL model, or create a primitive.<br/>Geometry stays on this machine.</p></div>}
    <div className="flow-legend">{flow&&<span>{flow.preview?'Preview · ':''}{flow.label||(flow.speed===0?'Still fluid':`${flow.fluid==='water'?'Water':'Fluid'} speed ${flow.speed} m/s · arrows show incoming flow`)}</span>}</div><div className="axis-key"><small>SI · metres</small></div>
    {!!range.length&&<div className="color-legend"><span>{range[0].toPrecision(4)}</span><div/><span>{range[1].toPrecision(4)}</span><small>{field==='pressure_pa'?'Pressure · Pa':field==='U'?'Velocity · m/s':field}</small></div>}
  </div>;
}
