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
import {overlayGeometry} from './overlays';

export default function Viewer({geometryId, resultUrl, field='pressure_pa', colorRange, wireframe=false, onPick, reset=0, selectedGroups=[],hiddenGroups=[],rotation,domain}: {
  geometryId?: string; resultUrl?: string; field?: string; colorRange?: [number,number]; wireframe?:boolean; onPick?:(group:number)=>void; reset?:number;selectedGroups?:number[];hiddenGroups?:number[];rotation?:any;domain?:any;
}) {
  const container = useRef<HTMLDivElement>(null);
  const cameraState=useRef<any>(undefined);
  const pickRef = useRef(onPick); pickRef.current = onPick;
  const [error,setError]=useState('');
  const [range,setRange]=useState<number[]>([]);
  useEffect(()=>{
    if (!container.current) return;
    let disposed=false;
    const render = vtkGenericRenderWindow.newInstance({background:[0.055,0.073,0.095]});
    render.setContainer(container.current);
    const renderer=render.getRenderer(), window=render.getRenderWindow();
    const mapper=vtkMapper.newInstance(), actor=vtkActor.newInstance();
    actor.setMapper(mapper); actor.getProperty().setColor(0.61,0.73,0.78);
    actor.getProperty().setSpecular(0.3); actor.getProperty().setSpecularPower(25);
    actor.getProperty().setEdgeVisibility(wireframe);
    actor.getProperty().setEdgeColor(0.14,0.24,0.28);
    if(resultUrl){actor.getProperty().setAmbient(.7);actor.getProperty().setDiffuse(.3);}
    if(resultUrl?.endsWith('mesh-preview.vtp'))actor.getProperty().setEdgeColor(.36,.56,.65);
    renderer.addActor(actor);
    let groups:number[]=[];
    const owned:any[]=[];
    const picker=vtkCellPicker.newInstance();
    picker.setPickFromList(true); picker.addPickList(actor);
    const subscription=render.getInteractor().onLeftButtonPress((event:any)=>{
      if(!event.position || !groups.length) return;
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
          } else { mapper.setScalarVisibility(false); if(!resultUrl.endsWith('mesh-preview.vtp'))setError(`Field ${field} is not available in this extract.`); }
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
        mapper.setInputData(poly); renderer.resetCamera();
        const camera=renderer.getActiveCamera();const saved=cameraState.current;const sceneKey=resultUrl||geometryId;
        if(saved?.key===sceneKey&&saved.reset===reset){camera.setPosition(...saved.position as [number,number,number]);camera.setFocalPoint(...saved.focal as [number,number,number]);camera.setViewUp(...saved.up as [number,number,number]);camera.setParallelScale(saved.scale);}
        else{camera.azimuth(25);camera.elevation(20);camera.setViewUp(0,0,1);}
        if(!resultUrl&&(rotation?.enabled||domain)){const overlay=overlayGeometry(rotation,domain);owned.push(overlay);const overlayMapper=vtkMapper.newInstance();owned.push(overlayMapper);overlayMapper.setInputData(overlay);const overlayActor=vtkActor.newInstance();owned.push(overlayActor);overlayActor.setMapper(overlayMapper);overlayActor.getProperty().setColor(.97,.79,.5);overlayActor.getProperty().setLineWidth(2);overlayActor.setPickable(false);renderer.addActor(overlayActor);}
        renderer.resetCameraClippingRange();render.resize();window.render();
      }catch(e){if(!disposed)setError(String(e));}
    }
    load();
    return ()=>{const camera=renderer.getActiveCamera();cameraState.current={key:resultUrl||geometryId,reset,position:[...camera.getPosition()],focal:[...camera.getFocalPoint()],up:[...camera.getViewUp()],scale:camera.getParallelScale()};disposed=true; observer.disconnect();subscription.unsubscribe();picker.delete();renderer.removeActor(actor);actor.delete();mapper.delete();owned.forEach(v=>v.delete());render.delete();};
  },[geometryId,resultUrl,field,colorRange?.[0],colorRange?.[1],wireframe,reset,JSON.stringify(selectedGroups),JSON.stringify(hiddenGroups),JSON.stringify(rotation),JSON.stringify(domain)]);
  return <div className="viewport"><div className="vtk-container" ref={container}/>{error&&<div className="viewport-notice">{error}</div>}
    {!geometryId&&!resultUrl&&<div className="viewport-empty"><div className="empty-cross">＋</div><h2>Your next simulation starts here</h2><p>Import a STEP or STL model, or create a primitive.<br/>Geometry stays on this machine.</p></div>}
    <div className="axis-key"><span>X</span><span>Y</span><span>Z</span><small>SI · metres</small></div>
    {!!range.length&&<div className="color-legend"><span>{range[0].toPrecision(4)}</span><div/><span>{range[1].toPrecision(4)}</span><small>{field==='pressure_pa'?'Pressure · Pa':field==='U'?'Velocity · m/s':field}</small></div>}
  </div>;
}
