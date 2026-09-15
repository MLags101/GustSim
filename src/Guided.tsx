import {useEffect, useState} from 'react';
import {api, post} from './api';
import {usePolling} from './usePolling';
import {NumberField as N, VectorField as V, SelectField as S, Section} from './Controls';

export function ComponentTree({geometry,selected,setSelected,hidden,setHidden}:any){
  const [query,setQuery]=useState(''),[limit,setLimit]=useState(200);
  const parts=geometry.parts?.length?geometry.parts:geometry.patches.map((p:any)=>({id:p.name,name:p.name,patches:[p.name]}));
  return <Section title="Components"><label className="field">Find component<input value={query} onChange={e=>setQuery(e.target.value)}/></label><p className="hint">Select a component to select all its surfaces. The rotor belongs in the same assembly STEP.</p>
    <button className="secondary full" onClick={()=>setHidden([])}>Show all components</button>
    {parts.filter((p:any)=>p.name.toLowerCase().includes(query.toLowerCase())).slice(0,limit).map((p:any)=>{let depth=0,parent=p.parent_id;while(parent&&depth<64){depth++;parent=parts.find((q:any)=>q.id===parent)?.parent_id;}const checked=p.patches.length>0&&p.patches.every((n:string)=>selected.includes(n));return <div className="component-row" key={p.id||p.name} style={{paddingLeft:depth*12}}><label><input type="checkbox" checked={checked} onChange={()=>setSelected(checked?selected.filter((n:string)=>!p.patches.includes(n)):[...new Set([...selected,...p.patches])])}/><span title={p.id}>{p.name}</span></label><div><button onClick={()=>setHidden(p.patches.every((n:string)=>hidden.includes(n))?hidden.filter((n:string)=>!p.patches.includes(n)):[...new Set([...hidden,...p.patches])])}>{p.patches.every((n:string)=>hidden.includes(n))?'Show':'Hide'}</button><button onClick={()=>setHidden(geometry.patches.map((q:any)=>q.name).filter((n:string)=>!p.patches.includes(n)))}>Isolate</button></div></div>;})}
    {parts.filter((p:any)=>p.name.toLowerCase().includes(query.toLowerCase())).length>limit&&<button className="secondary full" onClick={()=>setLimit(v=>v+200)}>Show more components</button>}
  </Section>;
}

const sig=(n:number)=>Number.isFinite(n)?Number(n).toPrecision(4).replace(/\.?0+$/,''):'—';
const axisLabel=(v:number[]|undefined)=>Array.isArray(v)?`(${v.map(n=>sig(n)).join(', ')})`:'—';

/** Plain-language view of a generated setup, so the attestation checkboxes below it
 *  refer to values the user can actually see rather than a JSON blob. */
export function SpecSummary({spec,kind}:{spec:any;kind:string}){
  if(!spec) return null;
  const lo=spec.domain?.minimum||[],hi=spec.domain?.maximum||[];
  const size=lo.length===3&&hi.length===3?hi.map((h:number,i:number)=>h-lo[i]):[];
  const walls=(spec.boundaries||[]).filter((b:any)=>b.kind==='wall').length;
  const rows:[string,string][]=[
    ['Fluid',`${spec.fluid?.name==='water'?'Water':'Air'} · ${sig(spec.fluid?.density)} kg/m³ · ${sig(spec.fluid?.dynamic_viscosity)} Pa·s`],
    ['Turbulence',spec.flow?.turbulence==='laminar'?'Laminar':'Fully turbulent k–ω SST'],
    ['Flow speed',`${sig(spec.flow?.speed)} m/s`],
    ['Wind tunnel size',size.length?`${size.map((n:number)=>sig(n)).join(' × ')} m`:'—'],
    ['Wall surfaces',`${walls} of ${(spec.boundaries||[]).length} boundaries`],
  ];
  if(kind==='aerodynamics'){
    rows.push(['Frontal area',`${sig(spec.references?.area)} m² · projected along the travel direction`]);
    rows.push(['Reference length',`${sig(spec.references?.length)} m`]);
    rows.push(['Drag direction',axisLabel(spec.references?.drag_axis)]);
    rows.push(['Lift direction',axisLabel(spec.references?.lift_axis)]);
  }
  if(kind==='propeller'&&spec.rotation?.enabled){
    rows.push(['Rotor speed',`${sig(spec.rotation.rpm)} RPM about ${axisLabel(spec.rotation.axis)}`]);
    rows.push(['Blade radius',`${sig(spec.rotation.blade_radius)} m`]);
    rows.push(['Rotating region',`radius ${sig(spec.rotation.radius)} m · length ${sig(spec.rotation.length)} m`]);
  }
  rows.push(['Mesh budget',`${sig(spec.mesh?.max_cells)} cells max · ${spec.mesh?.layers??0} near-wall layers requested`]);
  return <dl className="spec-summary">{rows.map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>;
}

export function GuidedSetup({geometry,spec,setSpec,selected,setSelected,act,onFlowPreview}:any){
  const [page,setPage]=useState(0);
  const [kind,setKind]=useState(spec?.use_case?.kind||'aerodynamics'),[fluid,setFluid]=useState(spec?.fluid?.name==='water'?'water':'air'),[speed,setSpeed]=useState(spec?.flow?.speed??10),[direction,setDirection]=useState<number[]>((spec?.references?.drag_axis||[1,0,0]).map((n:number)=>-n)),[lift,setLift]=useState<number[]>(spec?.references?.lift_axis||[0,0,1]);
  const [inlet,setInlet]=useState(''),[outlet,setOutlet]=useState(''),[driving,setDriving]=useState('flow'),[flow,setFlow]=useState(.01),[pin,setPin]=useState(100),[pout,setPout]=useState(0),[turbulence,setTurbulence]=useState(spec?.flow?.turbulence||'kOmegaSST');
  const [origin,setOrigin]=useState<number[]>(spec?.rotation?.enabled?spec.rotation.origin:(geometry?.bounds?.[0]||[0,0,0]).map((n:number,i:number)=>(n+(geometry?.bounds?.[1]?.[i]??0))/2)),[axis,setAxis]=useState<number[]>(spec?.rotation?.axis||[1,0,0]),[rpm,setRpm]=useState(spec?.rotation?.enabled?spec.rotation.rpm:2000),[preview,setPreview]=useState<any>(),[confirmed,setConfirmed]=useState(false),[refs,setRefs]=useState(false);
  const request={geometry_id:geometry.id,kind,fluid,turbulence,speed,direction,lift_axis:lift,inlet,outlet,driving,flow_rate:flow,inlet_pressure_pa:pin,outlet_pressure_pa:pout,patches:selected,origin,axis,rpm};
  const requestKey=JSON.stringify(request);
  // Reference area follows the travel direction, so a changed request invalidates the
  // reference attestation too, not just the dimension attestation.
  useEffect(()=>{setPreview(undefined);setConfirmed(false);setRefs(false);},[requestKey]);
  useEffect(()=>{
    const v=kind==='aerodynamics'?direction.map(n=>-n):axis;
    const propSpan=geometry?.dimensions?Math.max(...geometry.dimensions):1;
    const estRadius=propSpan*0.45, estLength=propSpan*0.35;
    const rotPreview=kind==='propeller'?{
      enabled:true,
      origin,
      axis,
      rpm,
      radius:preview?.spec?.rotation?.radius||estRadius,
      length:preview?.spec?.rotation?.length||estLength,
      blade_radius:preview?.spec?.rotation?.blade_radius||(estRadius*0.85),
      patches:selected
    }:undefined;
    onFlowPreview?.(page===2&&preview?{...preview.spec.flow,fluid,domain:preview.spec.domain,rotation:preview.spec.rotation,preview:true}:page===1?{
      velocity:v,
      speed:kind==='pipe'?0:speed,
      fluid,
      rotation:rotPreview,
      preview:true,
      label:kind==='pipe'?'Preview the pipe setup to inspect its inlet direction':kind==='propeller'?`Rotor: ${rpm} RPM · positive axis and spin direction shown in 3D`:undefined
    }:null);
    return()=>onFlowPreview?.(null);
  },[page,kind,speed,fluid,JSON.stringify(direction),JSON.stringify(axis),JSON.stringify(origin),rpm,JSON.stringify(selected),JSON.stringify(preview?.spec)]);

  return <Section title="Guided setup"><div className="step-tabs">{['1 · Use case','2 · Flow & model','3 · Review'].map((name,i)=><button className={page===i?'active':''} key={name} onClick={()=>setPage(i)}>{name}</button>)}</div><p className="hint">Current setup: {spec?.use_case?.kind||'Custom'}. Preview a common-use setup before replacing the current settings.</p>
    {page===0&&<><S label="Common use" value={kind} onChange={v=>{setKind(v);setSpeed(v==='propeller'?0:10);}} options={[["pipe","Basic pipe flow"],["aerodynamics","Object moving through fluid"],["propeller","Stationary assembly with rotor"]]}/>
    <S label="Fluid at 20 °C" value={fluid} onChange={setFluid} options={['air','water']}/>
    <S label="Turbulence" value={turbulence} onChange={setTurbulence} options={[["kOmegaSST","Fully turbulent k–ω SST"],["laminar","Laminar"]]}/><button className="primary full spaced" onClick={()=>setPage(1)}>Next · set the flow</button></>}
    {page===1&&<>{kind==='pipe'?<><p className="hint">First isolate the inner walls and cap planar openings in Geometry. Select one cap for each end.</p><S label="Inlet surface" value={inlet} onChange={setInlet} options={[["","Choose inlet"],...geometry.patches.map((p:any)=>[p.name,p.name])]}/><S label="Outlet surface" value={outlet} onChange={setOutlet} options={[["","Choose outlet"],...geometry.patches.map((p:any)=>[p.name,p.name])]}/><S label="Driving condition" value={driving} onChange={setDriving} options={[["flow","Known volume flow"],["speed","Known mean inlet speed"],["pressure","Known pressures → predict flow"]]}/>{driving==='flow'?<N label="Volume flow · m³/s" value={flow} onChange={setFlow}/>:<N label={driving==='pressure'?'Turbulence reference speed estimate · m/s':'Mean inlet speed · m/s'} value={speed} onChange={setSpeed}/>}{driving==='pressure'&&<><N label="Inlet total pressure · Pa gauge" value={pin} onChange={setPin}/><p className="hint">The reference speed initializes turbulence; it does not prescribe the resulting flow.</p></>}<N label="Outlet static pressure · Pa gauge" value={pout} onChange={setPout}/></>:<><N label={kind==='propeller'?'Axial inflow · m/s (0 = still fluid)':'Travel speed · m/s'} value={speed} onChange={setSpeed}/>{kind==='aerodynamics'?<><div className="direction-picker"><p className="hint">Fluid comes from the selected side. Arrows show its direction in the viewport.</p>{['+X','-X','+Y','-Y','+Z','-Z'].map((name,i)=><button key={name} onClick={()=>{setDirection([0,0,0].map((_,j)=>j===Math.floor(i/2)?(i%2?-1:1):0));setLift(Math.floor(i/2)===2?[0,1,0]:[0,0,1]);}}>From {name}</button>)}</div><V label="Object travel direction" value={direction} onChange={setDirection}/><V label="Positive lift direction" value={lift} onChange={setLift}/><p className="hint">{fluid==='air'?'Air':'Water'} flows opposite the travel direction. {selected.length} surfaces selected for force measurement.</p></>:<><V label="Rotor origin · m" value={origin} onChange={setOrigin}/><V label="Positive rotation axis" value={axis} onChange={setAxis}/><N label="Signed RPM · right-hand rule" value={rpm} onChange={setRpm}/><p className="hint">{selected.length} rotor surfaces selected. Confirm axis and origin here; the preview fits the region around these surfaces.</p></>}</>}
    {kind!=='pipe'&&<><button className="secondary full spaced" onClick={()=>setSelected(geometry.patches.map((p:any)=>p.name))}>Select all model surfaces</button><p className="hint">For an assembly, expand Components below to select only the body or rotor you want to measure.</p></>}
    <button className="secondary full spaced" onClick={()=>act('Generating setup preview',async()=>{setPreview(await post('/presets/preview',request));setPage(2);})}>Next · preview setup</button></>}
    {page===2&&(preview?<div className="preset-preview"><h3>Review suggested settings</h3><p className="hint">Model dimensions: {geometry.dimensions.map((n:number)=>n.toPrecision(4)).join(' × ')} m</p><V label="Point inside intended fluid · m" value={preview.spec.domain.fluid_point} onChange={v=>{setConfirmed(false);setPreview({...preview,spec:{...preview.spec,domain:{...preview.spec.domain,fluid_point:v}}});}}/><SpecSummary spec={preview.spec} kind={kind}/><details><summary>Full generated configuration</summary><pre className="log-output">{JSON.stringify(preview.spec,null,2)}</pre></details><label className="check-field"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/> I checked dimensions, fluid point, directions and selected surfaces</label><label className="check-field"><input type="checkbox" checked={refs} onChange={e=>setRefs(e.target.checked)}/> Reference area and length are suitable for coefficients</label><button className="primary full" disabled={!confirmed} onClick={()=>act('Applying guided setup',async()=>{const s={...preview.spec,use_case:{...preview.spec.use_case,confirmed:true,references_confirmed:refs}};setSpec(s);setPreview(undefined);onFlowPreview?.(null);})}>Apply reviewed setup</button><p className="hint">Standard mesh and SST settings are starting points. Experimental validation remains pending.</p></div>:<p className="hint">{spec?.use_case?.confirmed?'Setup applied. Continue to Mesh when the checks below are ready.':'Complete Flow & model and preview your setup first.'}</p>)}
  </Section>;
}


/** Name an automatic view by what it shows, not by its ParaView filter kind. */
export function viewLabel(spec:any){
  if(!spec) return 'Result view';
  if(spec.kind==='surface') return spec.field==='yPlus'?'Wall y⁺ on the model':'Surface pressure';
  if(spec.kind==='streamlines') return 'Streamlines';
  if(spec.kind==='slice') return spec.field==='pressure_pa'?'Pressure section':'Velocity section';
  if(spec.kind==='clip') return 'Clipped volume';
  if(spec.kind==='glyphs') return 'Velocity arrows';
  if(spec.kind==='line') return 'Line sample';
  if(spec.kind==='probe') return 'Point probe';
  return 'Result view';
}

export function AutomaticViews({run,onView,act}:any){
  const [views,setViews]=useState<any[]>([]);
  useEffect(()=>{setViews([]);},[run?.id]);
  usePolling(async()=>{
    const views=await api<any[]>(`/runs/${run.id}/views`);
    setViews(views.filter(item=>item.automatic));
  },3000,!!run?.result?.fields_available);
  if(!run?.spec.use_case)return null;
  return <Section title="Suggested result views">{views.length?views.map(v=><button className="secondary full" key={v.id} disabled={v.status!=='completed'} onClick={()=>act('Opening saved view',()=>onView(v.spec,v.id))}>{viewLabel(v.spec)} · {v.status||'queued'}{v.error&&<small>{v.error}</small>}</button>):<p className="hint">Views are queued automatically after solved fields are available.</p>}</Section>;
}
