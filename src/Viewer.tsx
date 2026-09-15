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
import vtkAnnotatedCubeActor from '@kitware/vtk.js/Rendering/Core/AnnotatedCubeActor';
import {tracksFromPolyData,sampleTrack} from './streamlinePlayback';
import {overlayGeometry} from './overlays';

/** Units mirror backend/gustsim/exports.py FIELD_UNITS so the legend never shows a raw key. */
export const FIELD_LABELS:Record<string,string>={
  pressure_pa:'Pressure · Pa gauge',
  p:'Kinematic pressure · m²/s²',
  U:'Velocity · m/s',
  k:'Turbulent kinetic energy · m²/s²',
  omega:'Specific dissipation · 1/s',
  nut:'Turbulent viscosity · m²/s',
  yPlus:'Wall y⁺ · dimensionless',
  vorticity:'Vorticity · 1/s',
};

type ScenePatch={name:string;group:number};
type SceneData={points:number[];polys:number[];groups:number[];patches?:ScenePatch[]};

const SELECTED=[113,225,195],ROTOR=[237,186,118],NEUTRAL=[156,186,199];

/** Point the camera at an absolute orientation. Callers reset to a canonical pose first so
 *  the relative azimuth/elevation used for the isometric view stay idempotent. */
function applyPose(renderer:any,pose:string){
  const camera=renderer.getActiveCamera();
  if(pose==='iso'){camera.azimuth(25);camera.elevation(20);camera.setViewUp(0,0,1);return;}
  const axis=['x','y','z'].indexOf(pose);
  if(axis<0)return;
  const focal=camera.getFocalPoint();
  const distance=Math.hypot(...camera.getPosition().map((n:number,i:number)=>n-focal[i]));
  camera.setPosition(...focal.map((n:number,i:number)=>n+(i===axis?distance:0)) as [number,number,number]);
  camera.setViewUp(...(axis===2?[0,1,0]:[0,0,1]) as [number,number,number]);
}

export default function Viewer({geometryId, resultUrl, field='pressure_pa', colorRange, wireframe=false, onPick, reset=0, selectedGroups=[],hiddenGroups=[],rotation,domain,flow,modelBounds,cameraPose='iso',objectGeometryId,animate=false}: {
  flow?:any;modelBounds?:number[][];cameraPose?:string;objectGeometryId?:string;animate?:boolean;geometryId?: string; resultUrl?: string; field?: string; colorRange?: [number,number]; wireframe?:boolean; onPick?:(group:number)=>void; reset?:number;selectedGroups?:number[];hiddenGroups?:number[];rotation?:any;domain?:any;
}) {
  const container = useRef<HTMLDivElement>(null);
  const scene = useRef<any>(null);
  const pickRef = useRef(onPick);
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(false);
  const [range,setRange]=useState<number[]>([]);
  const [sceneData,setSceneData]=useState<SceneData|null>(null);
  const [resultPoly,setResultPoly]=useState<any>(null);
  const [built,setBuilt]=useState(0);

  // Stable primitive dependency keys: these props are arrays/objects rebuilt on every
  // parent render, so comparing them by identity would re-run every effect constantly.
  const selectedKey=selectedGroups.join(','),hiddenKey=hiddenGroups.join(',');
  const rotorKey=(rotation?.enabled?rotation.patches||[]:[]).join(',');
  const overlayKey=JSON.stringify([rotation,domain,flow,modelBounds]);

  useEffect(()=>{pickRef.current=onPick;});

  // --- Mount once. The render window, actors and interactor outlive every prop change;
  // everything below mutates them instead of rebuilding the WebGL context.
  useEffect(()=>{
    if(!container.current) return;
    const render=vtkGenericRenderWindow.newInstance({background:[0.055,0.073,0.095]});
    render.setContainer(container.current);
    const renderer=render.getRenderer(), renderWindow=render.getRenderWindow();
    const mapper=vtkMapper.newInstance(), actor=vtkActor.newInstance();
    actor.setMapper(mapper);
    const property=actor.getProperty();
    property.setColor(0.61,0.73,0.78); property.setSpecular(0.3); property.setSpecularPower(25);
    property.setEdgeColor(0.14,0.24,0.28);
    renderer.addActor(actor);
    const picker=vtkCellPicker.newInstance();
    picker.setPickFromList(true); picker.addPickList(actor);

    const interactor=render.getInteractor();
    const canvas=container.current.querySelector('canvas');
    const onContextMenu=(e:MouseEvent)=>e.preventDefault();
    canvas?.addEventListener('contextmenu',onContextMenu);

    const manipulators=[
      vtkMouseCameraTrackballRotateManipulator.newInstance({button:1}),
      vtkMouseCameraTrackballPanManipulator.newInstance({button:3}),
      vtkMouseCameraTrackballPanManipulator.newInstance({button:2}),
      vtkMouseCameraTrackballPanManipulator.newInstance({button:1,shift:true}),
      vtkMouseCameraTrackballZoomManipulator.newInstance({scrollEnabled:true}),
      vtkMouseCameraTrackballZoomManipulator.newInstance({button:3,control:true}),
    ];
    const iStyle=vtkInteractorStyleManipulator.newInstance();
    manipulators.forEach(m=>iStyle.addMouseManipulator(m));
    interactor.setInteractorStyle(iStyle);

    const cube=vtkAnnotatedCubeActor.newInstance();
    cube.setDefaultStyle({
      text:'',
      fontFamily:'Segoe UI, Arial, sans-serif',
      fontColor:'#ffffff',
      fontStyle:'bold',
      fontSizeScale:(res:number)=>res/2.2,
      edgeThickness:0.08,
      edgeColor:'#101720',
      resolution:256,
    });
    cube.setXPlusFaceProperty({text:'+X',faceColor:'#e0484c'});
    cube.setXMinusFaceProperty({text:'-X',faceColor:'#7a2022'});
    cube.setYPlusFaceProperty({text:'+Y',faceColor:'#2ea36f'});
    cube.setYMinusFaceProperty({text:'-Y',faceColor:'#144d33'});
    cube.setZPlusFaceProperty({text:'+Z',faceColor:'#3d7cd1'});
    cube.setZMinusFaceProperty({text:'-Z',faceColor:'#183c6b'});

    const orientationWidget=vtkOrientationMarkerWidget.newInstance({actor:cube,interactor});
    orientationWidget.setParentRenderer(renderer);
    orientationWidget.setEnabled(true);
    orientationWidget.setViewportCorner(vtkOrientationMarkerWidget.Corners.BOTTOM_LEFT);
    orientationWidget.setViewportSize(0.17);
    orientationWidget.setMinPixelSize(80);
    orientationWidget.setMaxPixelSize(130);

    let downPos:{x:number;y:number}|null=null;
    const subDown=interactor.onLeftButtonPress((event:any)=>{
      if(event.position) downPos={x:event.position.x,y:event.position.y};
    });
    const subUp=interactor.onLeftButtonRelease((event:any)=>{
      const groups:number[]=scene.current?.groups||[];
      if(!downPos||!event.position||!groups.length) return;
      const dx=event.position.x-downPos.x, dy=event.position.y-downPos.y;
      downPos=null;
      if(Math.hypot(dx,dy)>5||event.shiftKey||event.controlKey||event.altKey) return;
      picker.pick([event.position.x,event.position.y,0],renderer);
      const id=picker.getCellId();
      if(id>=0) pickRef.current?.(groups[id]);
    });
    const observer=new ResizeObserver(()=>render.resize());
    observer.observe(container.current);

    const handle={render,renderer,renderWindow,mapper,actor,picker,groups:[] as number[],
      colors:null as any,input:null as any,disposables:[] as any[]};
    scene.current=handle;

    return ()=>{
      scene.current=null;
      observer.disconnect();
      canvas?.removeEventListener('contextmenu',onContextMenu);
      subDown.unsubscribe(); subUp.unsubscribe();
      orientationWidget.setEnabled(false); orientationWidget.delete(); cube.delete();
      manipulators.forEach(m=>m.delete());
      iStyle.delete(); picker.delete();
      handle.disposables.forEach(v=>v.delete?.());
      handle.input?.delete?.();
      renderer.removeActor(actor); actor.delete(); mapper.delete(); render.delete();
    };
  },[]);

  /** Swap the displayed dataset, disposing whatever it replaces. */
  function showData(handle:any,poly:any){
    const previous=handle.input;
    handle.mapper.setInputData(poly);
    handle.input=poly;
    if(previous&&previous!==poly) previous.delete?.();
  }

  // --- Fetch the component scene once per geometry revision.
  useEffect(()=>{
    if(!geometryId){setSceneData(null);return;}
    let alive=true; setLoading(true); setError('');
    api(`/geometry/${geometryId}/scene`)
      .then(data=>{if(alive){setSceneData(data);setLoading(false);}})
      .catch(e=>{if(alive){setError(String(e));setLoading(false);}});
    return()=>{alive=false;};
  },[geometryId]);

  // --- Fetch the result extract once per URL. Changing `field` reuses this data.
  useEffect(()=>{
    if(!resultUrl){setResultPoly(null);return;}
    let alive=true; setLoading(true); setError('');
    fetch(resultUrl)
      .then(response=>{if(!response.ok)throw new Error('Result surface is unavailable');return response.arrayBuffer();})
      .then(bytes=>{
        const handle=scene.current;
        if(!alive||!handle)return;
        const reader=vtkXMLPolyDataReader.newInstance();
        handle.disposables.push(reader);
        reader.parseAsArrayBuffer(bytes);
        setResultPoly(reader.getOutputData(0));
        setLoading(false);
      })
      .catch(e=>{if(alive){setError(String(e));setLoading(false);}});
    return()=>{alive=false;};
  },[resultUrl]);

  // --- Build the component polydata. Only a changed revision or a changed hidden set
  // rebuilds; selection alone recolours in place below.
  useEffect(()=>{
    const handle=scene.current;
    if(!handle||!sceneData) return;
    const hidden=new Set(hiddenGroups);
    const polys:number[]=[],groups:number[]=[];
    for(let i=0,cell=0;i<sceneData.polys.length;){
      const count=sceneData.polys[i],group=sceneData.groups[cell++];
      if(!hidden.has(group)){
        // Copy explicitly: spreading a slice per cell allocates and blows the call
        // stack argument limit on large assemblies.
        for(let k=0;k<=count;k++) polys.push(sceneData.polys[i+k]);
        groups.push(group);
      }
      i+=count+1;
    }
    const poly=vtkPolyData.newInstance();
    poly.getPoints().setData(new Float32Array(sceneData.points),3);
    poly.getPolys().setData(new Uint32Array(polys));
    const colors=vtkDataArray.newInstance({name:'componentColors',numberOfComponents:3,
      values:new Uint8Array(groups.length*3)});
    poly.getCellData().setScalars(colors);
    showData(handle,poly);
    handle.mapper.setScalarMode(2);
    handle.mapper.setColorModeToDirectScalars();
    handle.mapper.setScalarVisibility(true);
    handle.groups=groups; handle.colors=colors;
    const property=handle.actor.getProperty();
    property.setAmbient(0); property.setDiffuse(1); property.setEdgeColor(0.14,0.24,0.28);
    setRange([]);
    setBuilt(v=>v+1);
  },[sceneData,hiddenKey]);

  // --- Recolour selection without touching geometry buffers.
  useEffect(()=>{
    const handle=scene.current;
    if(!handle||!handle.colors||!sceneData) return;
    const selected=new Set(selectedGroups);
    const rotorGroups=new Set((sceneData.patches||[])
      .filter(p=>rotation?.enabled&&(rotation.patches||[]).includes(p.name))
      .map(p=>p.group));
    const values=handle.colors.getData() as Uint8Array;
    handle.groups.forEach((group:number,i:number)=>{
      const rgb=selected.has(group)?SELECTED:rotorGroups.has(group)?ROTOR:NEUTRAL;
      values[i*3]=rgb[0]; values[i*3+1]=rgb[1]; values[i*3+2]=rgb[2];
    });
    handle.colors.modified(); handle.input?.modified?.(); handle.renderWindow.render();
  },[built,selectedKey,rotorKey,sceneData]);

  // --- Show a result extract.
  useEffect(()=>{
    const handle=scene.current;
    if(!handle||!resultPoly) return;
    showData(handle,resultPoly);
    handle.groups=[]; handle.colors=null;
    const property=handle.actor.getProperty();
    property.setAmbient(.7); property.setDiffuse(.3);
    if(resultUrl?.includes('/mesh-')) property.setEdgeColor(.36,.56,.65);
    else property.setEdgeColor(0.14,0.24,0.28);
    handle.renderWindow.render();
  },[resultPoly]);

  // --- Colour the extract by field. No refetch: the extract carries every field.
  useEffect(()=>{
    const handle=scene.current;
    if(!handle||!resultPoly) return;
    const array=resultPoly.getPointData().getArrayByName(field)||resultPoly.getCellData().getArrayByName(field);
    if(!array){
      handle.mapper.setScalarVisibility(false); setRange([]);
      if(!resultUrl?.includes('/mesh-')) setError(`Field ${field} is not available in this extract.`);
      handle.renderWindow.render();
      return;
    }
    setError('');
    const isPoint=!!resultPoly.getPointData().getArrayByName(field);
    handle.mapper.setScalarMode(isPoint?3:4);
    handle.mapper.setColorByArrayName(field);
    handle.mapper.setScalarVisibility(true);
    const extent=colorRange||array.getRange(array.getNumberOfComponents()>1?-1:0);
    // Only widen a genuinely degenerate range. `extent[1] || ...` also fires when the
    // maximum is exactly 0, which is common for suction-dominated gauge pressure and
    // would collapse e.g. [-5,0] to [-5,-4], saturating the whole surface.
    const span:[number,number]=extent[1]===extent[0]?[extent[0],extent[0]+1]:[extent[0],extent[1]];
    const lut=vtkColorTransferFunction.newInstance();
    const preset=vtkColorMaps.getPresetByName('Cool to Warm'); if(preset)lut.applyColorMap(preset);
    lut.setMappingRange(span[0],span[1]); lut.updateRange();
    handle.mapper.setLookupTable(lut); handle.mapper.setUseLookupTableScalarRange(true);
    setRange(span); handle.renderWindow.render();
    return()=>{lut.delete();};
  },[resultPoly,field,colorRange?.[0],colorRange?.[1]]);

  // --- Cheap property toggles.
  useEffect(()=>{
    const handle=scene.current; if(!handle) return;
    handle.actor.getProperty().setEdgeVisibility(wireframe);
    handle.renderWindow.render();
  },[wireframe,built,resultPoly]);

  // --- One camera authority. Reset to a canonical orientation first so the isometric
  // pose's relative azimuth/elevation cannot accumulate across runs.
  useEffect(()=>{
    const handle=scene.current;
    if(!handle||(!sceneData&&!resultPoly)) return;
    const camera=handle.renderer.getActiveCamera();
    camera.setPosition(0,0,1); camera.setFocalPoint(0,0,0); camera.setViewUp(0,1,0);
    handle.renderer.resetCamera();
    applyPose(handle.renderer,cameraPose);
    handle.renderer.resetCameraClippingRange();
    handle.render.resize();
    handle.renderWindow.render();
  },[sceneData,resultPoly,reset,cameraPose]);

  // --- Domain / rotor / flow overlays.
  useEffect(()=>{
    const handle=scene.current;
    if(!handle||resultUrl||!(rotation?.enabled||domain||flow)) return;
    const overlay=overlayGeometry(rotation,domain,flow,modelBounds);
    const mapper=vtkMapper.newInstance(); mapper.setInputData(overlay);
    const actor=vtkActor.newInstance(); actor.setMapper(mapper);
    actor.getProperty().setColor(.97,.79,.5);
    actor.getProperty().setLineWidth(2);
    actor.setPickable(false);
    handle.renderer.addActor(actor);
    handle.renderWindow.render();
    return()=>{
      if(scene.current===handle){handle.renderer.removeActor(actor);handle.renderWindow.render();}
      actor.delete(); mapper.delete(); overlay.delete?.();
    };
  },[overlayKey,resultUrl,built]);

  // --- Translucent context model alongside a result.
  useEffect(()=>{
    const handle=scene.current;
    if(!handle||!objectGeometryId||!resultUrl) return;
    let alive=true; let actor:any=null,mapper:any=null,poly:any=null;
    api(`/geometry/${objectGeometryId}/scene`).then(data=>{
      if(!alive||scene.current!==handle) return;
      poly=vtkPolyData.newInstance();
      poly.getPoints().setData(new Float32Array(data.points),3);
      poly.getPolys().setData(new Uint32Array(data.polys));
      mapper=vtkMapper.newInstance(); mapper.setInputData(poly); mapper.setScalarVisibility(false);
      actor=vtkActor.newInstance(); actor.setMapper(mapper);
      actor.getProperty().setColor(.64,.7,.73);
      actor.getProperty().setOpacity(.8);
      actor.setPickable(false);
      handle.renderer.addActor(actor);
      handle.renderWindow.render();
    }).catch(()=>{});
    return()=>{
      alive=false;
      if(actor&&scene.current===handle){handle.renderer.removeActor(actor);handle.renderWindow.render();}
      actor?.delete(); mapper?.delete(); poly?.delete();
    };
  },[objectGeometryId,resultUrl]);

  // --- Steady-streamline tracer playback.
  useEffect(()=>{
    const handle=scene.current;
    if(!handle||!animate||!resultPoly) return;
    const tracks=tracksFromPolyData(resultPoly);
    if(!tracks.length){
      setError('No integration-time streamline data is available for animation. Extract Streamlines first.');
      return;
    }
    const particles=vtkPolyData.newInstance();
    particles.getPoints().setData(new Float32Array(tracks.length*3),3);
    particles.getVerts().setData(new Uint32Array(tracks.flatMap((_,i)=>[1,i])));
    const mapper=vtkMapper.newInstance(); mapper.setInputData(particles); mapper.setScalarVisibility(false);
    const actor=vtkActor.newInstance(); actor.setMapper(mapper);
    actor.getProperty().setColor(1,.85,.25);
    actor.getProperty().setPointSize(6);
    actor.getProperty().setAmbient(1);
    actor.setPickable(false);
    handle.renderer.addActor(actor);
    let frameId=0; const started=performance.now();
    const frame=(now:number)=>{
      if(scene.current!==handle) return;
      const values=particles.getPoints().getData() as Float32Array;
      tracks.forEach((track,i)=>values.set(sampleTrack(track,((now-started)/8000+i/tracks.length)%1),i*3));
      particles.getPoints().modified(); particles.modified();
      handle.renderWindow.render();
      frameId=requestAnimationFrame(frame);
    };
    frameId=requestAnimationFrame(frame);
    return()=>{
      cancelAnimationFrame(frameId);
      if(scene.current===handle){handle.renderer.removeActor(actor);handle.renderWindow.render();}
      actor.delete(); mapper.delete(); particles.delete();
    };
  },[animate,resultPoly]);

  function onKeyDown(event:React.KeyboardEvent){
    const handle=scene.current; if(!handle) return;
    const camera=handle.renderer.getActiveCamera();
    const step=event.shiftKey?15:5;
    if(event.key==='ArrowLeft') camera.azimuth(step);
    else if(event.key==='ArrowRight') camera.azimuth(-step);
    else if(event.key==='ArrowUp') camera.elevation(step);
    else if(event.key==='ArrowDown') camera.elevation(-step);
    else if(event.key==='+'||event.key==='=') camera.dolly(1.1);
    else if(event.key==='-'||event.key==='_') camera.dolly(0.9);
    else return;
    event.preventDefault();
    camera.orthogonalizeViewUp();
    handle.renderer.resetCameraClippingRange();
    handle.renderWindow.render();
  }

  const busy=loading&&!error;
  return <div className="viewport">
    <div className="vtk-container" ref={container} tabIndex={0} role="application"
      aria-label="3D model view. Arrow keys orbit the camera, plus and minus zoom."
      onKeyDown={onKeyDown}/>
    {error&&<div className="viewport-notice" role="alert">{error}</div>}
    {busy&&<div className="viewport-loading" role="status"><span/>Loading geometry…</div>}
    {!geometryId&&!resultUrl&&<div className="viewport-empty"><div className="empty-cross">＋</div><h2>Your next simulation starts here</h2><p>Import a STEP or STL model, or create a primitive.<br/>Geometry stays on this machine.</p></div>}
    <div className="flow-legend">{flow&&<span>{flow.preview?'Preview · ':''}{flow.label||(flow.speed===0?'Still fluid':`${flow.fluid==='water'?'Water':'Fluid'} speed ${flow.speed} m/s · arrows show incoming flow`)}</span>}</div>
    <div className="axis-key" title="Coordinate orientation: Red=X, Green=Y, Blue=Z"><span className="axis-tag x">X</span><span className="axis-tag y">Y</span><span className="axis-tag z">Z</span><small>SI · metres</small></div>
    {!!range.length&&<div className="color-legend"><span>{range[0].toPrecision(4)}</span><div/><span>{range[1].toPrecision(4)}</span><small>{FIELD_LABELS[field]||field}</small></div>}
  </div>;
}
