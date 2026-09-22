import { useEffect, useRef, useState } from 'react';
import { Wind, Upload, Box, SlidersHorizontal, Layers3, Play, ChartNoAxesCombined, Download, CircleCheck, CircleAlert, RotateCcw, Plus, ChevronRight } from 'lucide-react';
import Viewer from './Viewer';
import {JobProgress,HeaderJobProgress,StageGuide,ViewControls,QuickViews,AboutModal} from './Experience';
import { api, post } from './api';
import {usePolling} from './usePolling';
import { GeometryTools, PhysicsPanel, MeshPanel, RunPanel, ResultsPanel, ExportPanel } from './Panels';
import RunDetails, { TraceChart, ComparisonTable } from './RunDetails';
import {ComponentTree, GuidedSetup, AutomaticViews} from './Guided';
import {browserTools} from './agentTools';
import {Checklist, MeshProgress, stageStates, meshCompatible, configurationKey, geometrySummary, preparationNote} from './Workflow';

const steps = [['Geometry',Box],['Setup',SlidersHorizontal],['Mesh',Layers3],['Run',Play],['Results',ChartNoAxesCombined],['Export',Download]] as const;
function preference(key:string,fallback:any){try{return JSON.parse(localStorage.getItem('gustsim.'+key)||JSON.stringify(fallback));}catch{return fallback;}}
export default function App(){
  const [step,setStep]=useState(0), [geometries,setGeometries]=useState<any[]>([]), [geometry,setGeometry]=useState<any>(),[spec,setSpec]=useState<any>(),[health,setHealth]=useState<any>();
  const [error,setError]=useState(''),[busy,setBusy]=useState(''),[units,setUnits]=useState('mm'),[reset,setReset]=useState(0),[wireframe,setWireframe]=useState(false),[selected,setSelected]=useState<string[]>([]);
  const [runs,setRuns]=useState<any[]>([]),[run,setRun]=useState<any>(),[logs,setLogs]=useState<string[]>([]),[report,setReport]=useState<any>(),[candidate,setCandidate]=useState<any>(),[field,setField]=useState('pressure_pa'),[colorRange,setColorRange]=useState<[number,number]>(),[viewPath,setViewPath]=useState<string>(),[comparison,setComparison]=useState('');
  const [samples,setSamples]=useState<any>();
  const [mesh,setMesh]=useState<any>(),[meshLogs,setMeshLogs]=useState<string[]>([]),[ack,setAck]=useState(''),[hidden,setHidden]=useState<string[]>([]),[panelWidth,setPanelWidth]=useState(()=>preference('panelWidth',350)),[hideControls,setHideControls]=useState(()=>preference('hideControls',false));
  const [projectId,setProjectId]=useState(()=>preference('projectId','')),[projectName,setProjectName]=useState(()=>preference('projectName','New simulation')),[projects,setProjects]=useState<any[]>([]);
  const [advancedSteps,setAdvancedSteps]=useState<Record<number,boolean>>({}),[showLibrary,setShowLibrary]=useState(false),[pose,setPose]=useState('iso'),[showObject,setShowObject]=useState(true),[animate,setAnimate]=useState(false),[viewKind,setViewKind]=useState('surface'),[meshPlane,setMeshPlane]=useState('y'),[flowPreview,setFlowPreview]=useState<any>(null);
  const [showAbout,setShowAbout]=useState(false);
  const advanced=!!advancedSteps[step];
  const toggleAdvanced=()=>setAdvancedSteps(v=>({...v,[step]:!v[step]}));
  const switching=useRef(false);
  const projectRef=useRef(projectId);
  const meshRef=useRef(mesh);
  const specRef=useRef(spec);
  const restored=useRef(false), restoredAck=useRef('');
  const [offline,setOffline]=useState(false);
  const streams=useRef<EventSource[]>([]);
  // Mutating refs during render is unsafe once React may discard or replay a render.
  useEffect(()=>{projectRef.current=projectId;},[projectId]);
  useEffect(()=>{meshRef.current=mesh;},[mesh]);
  useEffect(()=>{specRef.current=spec;},[spec]);
  useEffect(()=>()=>{streams.current.forEach(s=>s.close());streams.current=[];},[]);
  useEffect(()=>{let alive=true;(async()=>{let saved=preference('workspace',null);if(projectRef.current){try{const p=await api(`/projects/${projectRef.current}`);saved=p.workspace;if(alive)setProjectName(p.name);}catch{}}if(!saved)return;const [g,m,r]=await Promise.all([saved.spec?api(`/geometry/${saved.spec.geometry_id}`):Promise.resolve(undefined),saved.mesh?api(`/runs/${saved.mesh}`).catch(()=>undefined):Promise.resolve(undefined),saved.run?api(`/runs/${saved.run}`).catch(()=>undefined):Promise.resolve(undefined)]);if(alive){if(g){setGeometry(g);restoredAck.current=saved.ack||'';setSpec(saved.spec);}setMesh(m);setRun(r);setStep(Math.min(5,Math.max(0,saved.step||0)));}})().catch(()=>{}).finally(()=>{restored.current=true;});return()=>{alive=false;};},[]);
  useEffect(()=>{if(!restored.current)return;try{localStorage.setItem('gustsim.workspace',JSON.stringify({spec,mesh:mesh?.id,run:run?.id,step,ack}));}catch{}},[spec,mesh?.id,run?.id,step,ack]);
  useEffect(()=>{try{localStorage.setItem('gustsim.panelWidth',JSON.stringify(panelWidth));localStorage.setItem('gustsim.hideControls',JSON.stringify(hideControls));}catch{}},[panelWidth,hideControls]);
  useEffect(()=>{if(!mesh)return;setMeshLogs([]);const events=new EventSource(`/api/runs/${mesh.id}/events`);events.onmessage=e=>{const event=JSON.parse(e.data);if(event.message)setMeshLogs(v=>[...v,event.message].slice(-250));};events.addEventListener('done',()=>{events.close();api(`/runs/${mesh.id}`).then(detail=>setMesh((current:any)=>current?.id===detail.id?detail:current)).catch(()=>{});});return()=>events.close();},[mesh?.id]);
  useEffect(()=>{api<any[]>('/projects').then(setProjects).catch(()=>{});},[]);
  useEffect(()=>{if(!projectId||!restored.current)return;const target=projectId;const timer=setTimeout(()=>{if(switching.current||projectRef.current!==target)return;const workspace={spec,mesh:mesh?.id,run:run?.id,step,ack};api(`/projects/${projectId}`,{method:'PUT',body:JSON.stringify({name:projectName,workspace})}).then(()=>api<any[]>('/projects').then(setProjects)).catch(e=>setError(String(e)));localStorage.setItem('gustsim.projectId',JSON.stringify(projectId));localStorage.setItem('gustsim.projectName',JSON.stringify(projectName));},800);return()=>clearTimeout(timer);},[projectId,projectName,spec,mesh?.id,run?.id,step,ack]);
  async function switchProject(id:string){switching.current=true;try{await act('Opening simulation',async()=>{
    if(projectId)await api(`/projects/${projectId}`,{method:'PUT',body:JSON.stringify({name:projectName,workspace:{spec,mesh:mesh?.id,run:run?.id,step,ack}})});
    const p=id?await api(`/projects/${id}`):await post('/projects',{name:'New simulation',workspace:{}}),w=p.workspace;
    const [g,m,r]=await Promise.all([w.spec?api(`/geometry/${w.spec.geometry_id}`):undefined,w.mesh?api(`/runs/${w.mesh}`):undefined,w.run?api(`/runs/${w.run}`):undefined]);
    setProjectId(p.id);projectRef.current=p.id;setProjectName(p.name);setGeometry(g);setSpec(w.spec);setMesh(m);setRun(r);restoredAck.current=w.ack||'';setAck(w.ack||'');setStep(w.step||0);setSelected([]);setHidden([]);setCandidate(undefined);setComparison('');setViewPath(undefined);setAnimate(false);setAdvancedSteps({});setShowLibrary(false);setReset(v=>v+1);setProjects(await api('/projects'));
  });}finally{switching.current=false;}}
  const runRef=useRef(run);
  useEffect(()=>{runRef.current=run;},[run]);
  async function refresh(){try{setGeometries(await api('/geometry'));setHealth(await api('/health'));}catch(e){setError(String(e));}}
  useEffect(()=>{refresh();},[]);
  usePolling(async()=>{
    try{
      setHealth(await api('/health'));
      const list=await api<any[]>('/runs');
      setOffline(false);
      setRuns(list);
      for(const [r,setter] of [[runRef.current,setRun],[meshRef.current,setMesh]] as any[]){
        const latest=r&&list.find(x=>x.id===r.id);
        if(latest&&(latest.updated!==r.updated||['queued','running'].includes(r.status)))
          api(`/runs/${r.id}`).then(detail=>setter((current:any)=>current?.id===detail.id?detail:current)).catch(()=>{});
      }
    }catch{ setOffline(true); }
  },3000);
  useEffect(()=>{setReport(undefined);setAck(restoredAck.current);restoredAck.current='';if(!spec)return;let alive=true;const timer=setTimeout(()=>post('/validate',spec).then(r=>{if(alive)setReport(r);}).catch(()=>{}),400);return()=>{alive=false;clearTimeout(timer);};},[configurationKey(spec)]);
  useEffect(()=>{setLogs([]);setViewPath(undefined);setSamples(undefined);setAnimate(false);setViewKind('surface');if(!run)return;const events=new EventSource(`/api/runs/${run.id}/events`);events.onmessage=e=>{const event=JSON.parse(e.data);if(event.message)setLogs(v=>[...v,event.message].slice(-250));};events.addEventListener('done',()=>{events.close();api(`/runs/${run.id}`).then(detail=>setRun((current:any)=>current?.id===detail.id?detail:current)).catch(()=>{});});return()=>events.close();},[run?.id]);
  async function choose(g:any){const previous=specRef.current;setGeometry(g);setSelected([]);setHidden([]);setCandidate(undefined);let next=await api(`/geometry/${g.id}/defaults`);if(g.parent_id===previous?.geometry_id&&g.patch_mapping){const map=g.patch_mapping;const reverse:Record<string,string[]>={};for(const [from,to] of Object.entries(map) as any)for(const n of to)(reverse[n]??=[]).push(from);const safe=(n:string)=>map[n]?.length===1&&reverse[map[n][0]]?.length===1;const remap=(list:string[])=>list.filter(safe).map(n=>map[n][0]);const retained=previous.boundaries.filter((b:any)=>!(b.patch in map)||safe(b.patch)).map((b:any)=>({...b,patch:map[b.patch]?.[0]||b.patch}));const patchNames=new Set(g.patches.map((p:any)=>p.name));next={...previous,geometry_id:g.id,boundaries:retained.filter((b:any)=>patchNames.has(b.patch)||!geometry?.patches.some((p:any)=>p.name===b.patch)),rotation:{...previous.rotation,patches:remap(previous.rotation.patches),stationary_patches:remap(previous.rotation.stationary_patches)},use_case:previous.use_case?{...previous.use_case,confirmed:false,force_patches:remap(previous.use_case.force_patches)}:null};}if(!projectRef.current){const p=await post('/projects',{name:g.filename,workspace:{}});projectRef.current=p.id;setProjectId(p.id);setProjectName(p.name);}next.project_id=projectRef.current;setSpec(next);}
  function applySpec(s:any){setSpec({...s,project_id:projectRef.current||null});if(s.geometry_id!==geometry?.id)api(`/geometry/${s.geometry_id}`).then(g=>{setGeometry(g);setSelected([]);}).catch(e=>setError(String(e)));}
  async function act(label:string,action:()=>Promise<void>){setBusy(label);setError('');try{await action();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy('');}}
  async function upload(file:File){await act('Importing geometry',async()=>{const form=new FormData();form.append('file',file);form.append('units',units);const g=await api('/geometry/import',{method:'POST',body:form});await choose(g);await refresh();});}
  async function validate(){await act('Checking setup',async()=>setReport(await post('/validate',spec)));}
  async function launch(kind:string, acknowledged?:string){let created:any;let failure='';await act(kind==='case'?'Generating case':'Queuing simulation',async()=>{try{const check=await post('/validate',spec);setReport(check);if(!check.valid)throw new Error(check.errors.join('; '));const token=acknowledged??ack;const source=kind==='solve'&&meshCompatible(mesh,spec)?`&mesh_run_id=${mesh.id}`:'';const r=await post(`/runs?kind=${kind}&guided=true&acknowledged=${encodeURIComponent(token)}${source}`,spec);created=r;if(kind==='mesh'){setMesh(r);setStep(2);}else{setRun(r);setStep(kind==='case'?5:3);}setRuns(await api('/runs'));}catch(e){failure=e instanceof Error?e.message:String(e);throw e;}});return failure?{error:failure}:created;}
  async function selectRun(r:any){const detail=await api(`/runs/${typeof r==='string'?r:r.id}`);if(detail.kind==='mesh'){setMesh(detail);setStep(2);}else setRun(detail);}
  async function reviewMesh(){let reviewed:any;let failure='';await act('Recording mesh review',async()=>{try{const check=await post('/validate',spec);if(!meshCompatible(mesh,spec))throw new Error('Mesh is outdated');reviewed=await post(`/runs/${mesh.id}/review?configuration_id=${check.configuration_id}&acknowledge_warnings=true`,{});setMesh(reviewed);setStep(3);}catch(e){failure=e instanceof Error?e.message:String(e);throw e;}});return failure?{error:failure}:reviewed;}
  async function view(request:any,savedId?:string){setField(request.field);setViewKind(request.kind);setAnimate(false);setSamples(undefined);async function show(id:string){if(runRef.current?.id!==run.id)return;setViewPath(`/api/runs/${run.id}/files/views/${id}/surface.vtp`);if(['line','probe'].includes(request.kind))setSamples({...await api(`/runs/${run.id}/views/${id}/samples`),kind:request.kind,id,run_id:run.id});}if(savedId){const path=`/api/runs/${run.id}/files/views/${savedId}/surface.vtp`;const r=await fetch(path,{method:'HEAD'});if(!r.ok)throw new Error('This saved extraction is still pending or failed; inspect run history');await show(savedId);return;}const saved=await post(`/runs/${run.id}/views`,request);
    const events=new EventSource(`/api/runs/${saved.job.id}/events`);
    streams.current.push(events);
    const release=()=>{events.close();streams.current=streams.current.filter(s=>s!==events);};
    events.onerror=()=>{if(events.readyState===EventSource.CLOSED){release();setError('Lost the connection while extracting this view; reopen it from run history.');}};
    events.addEventListener('done',async()=>{release();try{const job=await api(`/runs/${saved.job.id}`);if(job.status==='completed')await show(saved.id);else setError(job.error||'Extraction failed');}catch(e){setError(String(e));}});}
  const props={geometry,spec,setSpec:applySpec,act,selected,setSelected,choose,refresh,busy,onFlowPreview:setFlowPreview};
  const [comparisonRun,setComparisonRun]=useState<any>();
  useEffect(()=>{
    if(!comparison){setComparisonRun(undefined);return;}
    let alive=true;
    api(`/runs/${comparison}`).then(r=>{if(alive)setComparisonRun(r);}).catch(()=>{if(alive)setComparisonRun(undefined);});
    return()=>{alive=false;};
  },[comparison]);
  const awaitingSurface=step===4&&!!run?.spec?.use_case&&!!run?.result?.fields_available&&!viewPath;
  usePolling(async()=>{
    const views=await api<any[]>(`/runs/${run.id}/views`);
    const surface=views.find(v=>v.spec.kind==='surface'&&v.status==='completed');
    if(surface){setField(surface.spec.field);setViewPath(`/api/runs/${run.id}/files/views/${surface.id}/surface.vtp`);}
  },3000,awaitingSurface);
  const resultUrl=step>=4&&run?.result.fields_available?(viewPath||`/api/runs/${run.id}/files/results/surface.vtp`):step===2&&mesh?.result.mesh_available?`/api/runs/${mesh.id}/files/${meshPlane==='y'?'mesh-preview.vtp':meshPlane==='exterior'?'mesh-exterior.vtp':`mesh-section-${meshPlane}.vtp`}`:undefined;
  const displayedGeometry=step>=4?geometries.find(g=>g.id===run?.spec.geometry_id):step===2&&mesh?.result.mesh_available?geometries.find(g=>g.id===mesh.spec.geometry_id):geometry;
  const geometryLabel=geometrySummary(displayedGeometry);
  const states=stageStates(geometry,spec,report,mesh,run,ack);
  const setupReady=report?.valid&&(!report.findings.some((f:any)=>f.status==='review')||ack===report.configuration_id);
  const solveReady=setupReady&&meshCompatible(mesh,spec)&&mesh?.status==='completed'&&mesh.result.reviewed_configuration_id===report?.configuration_id;
  const nextReady=[states[0]==='Ready',setupReady&&!flowPreview?.preview,solveReady,!!run?.result.fields_available,!!run?.result.case_available,false][step];
  useEffect(()=>{const context=(document as any).modelContext;if(!context?.registerTool)return;const life=new AbortController();for(const tool of browserTools({geometry,spec,report,mesh,run,post,applySpec,setReport,setAck,setStep,launch,reviewMesh})){try{Promise.resolve(context.registerTool(tool,{signal:life.signal})).catch(()=>{});}catch{}}return()=>life.abort();},[geometry,spec,report,mesh,run,ack]);
  return <div className="app-shell">
    <div className="project-bar">
      <div className="project-bar-left">
        <label>Simulation <select aria-label="Open simulation" value={projectId} onChange={e=>e.target.value&&switchProject(e.target.value)}><option value="">Select a saved simulation</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <input aria-label="Simulation name" value={projectName} onChange={e=>setProjectName(e.target.value)}/>
        <button className="secondary" disabled={!!busy} onClick={()=>switchProject('')}>＋ New simulation</button>
        <small>{projectId?'Saved automatically on this machine':'Import a model to begin'}</small>
      </div>
      <div className="project-bar-right">
        <HeaderJobProgress mesh={mesh} run={run} busy={busy} onNavigate={setStep}/>
      </div>
    </div>
    <div className={`workbench ${hideControls?'controls-hidden':''}`} style={{'--setup-width':`${Math.min(600,Math.max(280,panelWidth))}px`} as any}>
      <nav className="rail" aria-label="Workflow">
        {steps.map(([name,Icon],i)=><button key={name} className={step===i?'rail-item active':'rail-item'} onClick={()=>setStep(i)}><Icon size={22}/><span>{name}</span><em className={`stage-status ${states[i].toLowerCase().replaceAll(' ','-')}`}>{states[i]}</em><small>{String(i+1).padStart(2,'0')}</small></button>)}
        <div className="rail-bottom">
          <button type="button" className="rail-logo-button" onClick={()=>setShowAbout(true)} title="About GustSim & System Status" aria-label="About GustSim and System Status">
            <Wind size={24}/>
            <span className="logo-name">gust<span>sim</span></span>
            <span className="logo-badge">v0.1 · info</span>
          </button>
        </div>
      </nav>
    <aside className="setup-panel"><div className="panel-heading"><span className="eyebrow">SETUP / {String(step+1).padStart(2,'0')}</span><h1>{steps[step][0]}</h1><p>{step===0?'Prepare the model and identify its surfaces.':'Configure and inspect your simulation.'}</p></div>
      {offline&&<div className="notice error" role="alert"><CircleAlert size={17}/><span>Cannot reach the GustSim API. Your work is kept; reconnecting automatically.</span></div>}
      {error&&<div className="notice error" role="alert"><CircleAlert size={17}/><span>{error}</span><button className="text-button" onClick={()=>setError('')} aria-label="Dismiss message">Dismiss</button></div>}{busy&&<JobProgress busy={busy}/>}
      <StageGuide step={step} geometry={geometry}/><Checklist step={step} geometry={geometry} report={report} mesh={mesh} run={run} spec={spec} ack={ack} setAck={setAck} onCheck={validate} onNavigate={(n:number)=>{setStep(n);setAdvancedSteps(v=>({...v,[n]:true}));}}/>
      {step===0?<><section><h3>Import a model</h3><label className="dropzone"><Upload size={25}/><strong>Choose CAD geometry</strong><span>STEP or STL · up to 100 MB</span><input type="file" accept=".stl,.step,.stp" disabled={!!busy} onChange={e=>{if(e.target.files?.[0])upload(e.target.files[0]);e.target.value='';}}/></label><label className="field">STL source units<select value={units} onChange={e=>setUnits(e.target.value)}><option value="mm">Millimetres</option><option value="m">Metres</option><option value="cm">Centimetres</option><option value="in">Inches</option></select></label><p className="hint">GustSim is for assemblies, internal passages, and one steady rotor. External flow is the setup those cases share. Export one assembly STEP with separate named bodies in their assembled positions, including the rotor. Select the spinning component in GustSim; a second import creates a separate model. STEP uses embedded units. Every revision is stored in metres.</p></section>
      <section><button className="secondary full" onClick={()=>setShowLibrary(!showLibrary)}>{showLibrary?'Hide model library':'Choose an existing model'}</button>{showLibrary&&<><div className="section-title"><h3>Geometry library</h3><span>{geometries.length}</span></div>{geometries.length?geometries.map(g=><button className={`geometry-item ${geometry?.id===g.id?'selected':''}`} key={g.id} onClick={()=>act('Loading model',()=>choose(g))}><Box size={18}/><span><strong>{g.filename}</strong><small>{g.triangles.toLocaleString()} triangles · {g.patches.length} surfaces</small></span><ChevronRight size={16}/></button>):<p className="hint">Imported models and prepared revisions appear here.</p>}</>}
      </section>
      {geometry&&<><section className="prepare-card"><h3>Prepare for meshing</h3><p className="hint">For external flow and rotors, combine tiny CAD-face patches into one boundary per component. For pipes, use cleanup only to preserve separate inlet and outlet surfaces.</p><button className="primary full spaced" disabled={!!busy} onClick={()=>act('Preparing model',async()=>{setCandidate(await post(`/geometry/${geometry.id}/prepare`,{repair:true,component_surfaces:true}));await refresh();})}>Preview mesh-friendly preparation</button><button className="secondary full spaced" disabled={!!busy} onClick={()=>act('Cleaning geometry',async()=>{setCandidate(await post(`/geometry/${geometry.id}/prepare`,{repair:true}));await refresh();})}>Preview cleanup only · preserve faces</button></section><ComponentTree geometry={geometry} selected={selected} setSelected={setSelected} hidden={hidden} setHidden={setHidden}/><section><details><summary>Advanced surface selection</summary><p className="hint">Click a surface in the viewport or select it below.</p><div className="patch-list">{geometry.patches.map((p:any)=><label key={p.name}><input type="checkbox" checked={selected.includes(p.name)} onChange={()=>setSelected(v=>v.includes(p.name)?v.filter(x=>x!==p.name):[...v,p.name])}/><span>{p.name}</span><small>{p.triangles}</small></label>)}</div></details></section></>}<button className="secondary spaced" onClick={toggleAdvanced}>{advanced?'Hide advanced preparation':'Advanced preparation'}</button>{advanced&&<GeometryTools {...props} onCandidate={setCandidate}/>}</>:spec?<>
      {step===1&&<><GuidedSetup key={geometry.id+projectId} {...props}/><ComponentTree geometry={geometry} selected={selected} setSelected={setSelected} hidden={hidden} setHidden={setHidden}/><button className="secondary spaced" onClick={toggleAdvanced}>{advanced?'Hide advanced settings':'Advanced settings'}</button>{advanced&&<PhysicsPanel {...props}/>}</>}{step===2&&<><MeshProgress onRecommend={()=>act('Recommending local refinement',async()=>{const r=await post('/mesh/recommend',spec);applySpec({...spec,mesh:{...spec.mesh,body_level:r.body_level}});})} onLoadSpec={applySpec} mesh={mesh} spec={spec} logs={meshLogs} onReview={reviewMesh} onCancel={()=>act('Cancelling mesh',async()=>setMesh(await post(`/runs/${mesh.id}/cancel`,{})))} onSelect={(id:string)=>id&&act('Loading mesh',()=>selectRun(id))} runs={runs}/><button className="secondary spaced" onClick={toggleAdvanced}>{advanced?'Hide mesh settings':'Adjust mesh settings'}</button>{advanced&&<MeshPanel {...props}/>}<section>{report?.layer_plan?.requested_layers>0&&<p className={report.layer_plan.fits?'hint':'finding review'}>{report.layer_plan.detail}</p>}<button className="primary full" disabled={!health?.worker?.online||!!busy||!setupReady||['queued','running'].includes(mesh?.status)} onClick={()=>launch('mesh')}>Generate & check mesh</button>{!setupReady&&<p className="hint">Complete Setup and acknowledge actionable warnings first.</p>}</section></>}
      {step===3&&<><JobProgress job={run}/><RunPanel {...props} runs={runs.filter(r=>r.kind!=='view'&&(!projectId||r.spec.project_id===projectId))} run={run} setRun={(r:any)=>act('Loading attempt',()=>selectRun(r))} health={health} validation={report} onValidate={validate} onLaunch={launch} solveReady={solveReady} setupReady={setupReady} advanced={advanced}/><button className="secondary spaced" onClick={toggleAdvanced}>{advanced?'Hide solver settings':'Solver settings & parameter study'}</button></>}
      {step===4&&<><QuickViews run={run} act={act} onView={view}/><AutomaticViews run={run} act={act} onView={view}/><button className="secondary spaced" onClick={toggleAdvanced}>{advanced?'Hide advanced analysis':'Advanced analysis & saved views'}</button>{advanced&&<ResultsPanel run={run} act={act} onView={view} field={field} setField={setField} colorRange={colorRange} setColorRange={setColorRange}/>}</>}
      {step===5&&<ExportPanel run={run} act={act} onLaunch={launch} busy={busy}/>}
      </>:<div className="section-placeholder"><SlidersHorizontal size={28}/><p>Import or create geometry to configure a simulation.</p></div>}
      {step!==5&&<div className="panel-footer"><button className="primary full" disabled={!nextReady} onClick={()=>setStep(Math.min(step+1,5))}>Continue to {steps[Math.min(step+1,5)][0].toLowerCase()}<ChevronRight size={17}/></button>{!nextReady&&<p className="hint">Complete this stage’s required checks. You can still browse the other stages.</p>}</div>}
    </aside>
    <div className="panel-resizer" role="separator" tabIndex={0} aria-label="Resize setup panel" aria-orientation="vertical" aria-valuemin={280} aria-valuemax={600} aria-valuenow={panelWidth} onKeyDown={e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();setPanelWidth((v:number)=>e.key==='Home'?280:e.key==='End'?600:Math.max(280,Math.min(600,v+(e.key==='ArrowRight'?20:-20))));}}} onPointerDown={e=>{e.currentTarget.setPointerCapture(e.pointerId);}} onPointerMove={e=>{if(e.currentTarget.hasPointerCapture(e.pointerId))setPanelWidth(Math.max(280,Math.min(600,window.innerWidth-450,e.clientX-92)));}} onPointerUp={e=>e.currentTarget.releasePointerCapture(e.pointerId)}/>
    <main className="main-surface"><div className="viewport-toolbar"><div><span className="eyebrow">{step===2?(mesh?.result.mesh_available?'VOLUME MESH'+(meshCompatible(mesh,spec)?'':' · OUTDATED ATTEMPT'):'CAD PREVIEW · NO VOLUME MESH YET'):step>=4?'SOLVED RESULTS':'MODEL VIEW'}</span><strong>{step>=4&&run?run.spec.name:step===2&&mesh?.result.mesh_available?mesh.spec.name:geometry?.filename||'No geometry selected'}</strong></div><div className="toolbar-actions"><button onClick={()=>setHideControls((v:boolean)=>!v)}>{hideControls?'Show controls':'Hide controls'}</button><button className={wireframe?'active':''} onClick={()=>setWireframe(v=>!v)} title="Toggle mesh edges"><Layers3 size={17}/> Edges</button><button onClick={()=>setReset(v=>v+1)} title="Reset camera"><RotateCcw size={17}/></button></div></div>
      {hideControls&&error&&<div className="notice error" role="alert">{error}</div>}
      {candidate&&<div className="candidate-banner"><span>{preparationNote(geometry,candidate)}</span><button onClick={()=>act('Using prepared revision',async()=>{await choose(candidate);setCandidate(undefined);})}>Use revision</button><button onClick={()=>setCandidate(undefined)}>Return to original</button></div>}
      {step>=4&&<div className="comparison-toolbar"><label>Compare with <select value={comparison} onChange={e=>setComparison(e.target.value)}><option value="">Single view</option>{runs.filter(r=>r.result.fields_available&&r.id!==run?.id).map(r=><option key={r.id} value={r.id}>{r.spec.name}</option>)}</select></label><span>Field: {field} · {colorRange?'shared fixed range':'automatic ranges'}</span></div>}
      <ViewControls pose={pose} setPose={setPose} showObject={showObject} setShowObject={setShowObject} animate={animate} setAnimate={setAnimate} kind={viewKind} result={step>=4} mesh={step===2} meshPlane={meshPlane} setMeshPlane={setMeshPlane}/><div className={`viewer-row ${comparison&&step>=4?'comparison':''}`}><Viewer cameraPose={pose} objectGeometryId={showObject&&((step>=4&&viewKind!=='surface')||step===2)?displayedGeometry?.id:undefined} animate={animate&&step>=4&&viewKind==='streamlines'} flow={step===1?(flowPreview||{...spec?.flow}):undefined} modelBounds={geometry?.bounds} geometryId={step>=4?undefined:candidate?.id||geometry?.id} resultUrl={resultUrl} field={field} colorRange={colorRange} wireframe={step===2||wireframe} reset={reset} selectedGroups={geometry?.patches.filter((p:any)=>selected.includes(p.name)).map((p:any)=>p.group)} hiddenGroups={geometry?.patches.filter((p:any)=>hidden.includes(p.name)).map((p:any)=>p.group)} rotation={step<3?(flowPreview?.rotation||spec?.rotation):undefined} domain={step===1?(flowPreview?.domain||spec?.domain):undefined} onPick={id=>{const p=geometry?.patches.find((p:any)=>p.group===id);if(p)setSelected(v=>v.includes(p.name)?v.filter(x=>x!==p.name):[...v,p.name]);}}/>{comparison&&step>=4&&<Viewer resultUrl={`/api/runs/${comparison}/files/results/surface.vtp`} field={field} colorRange={colorRange} wireframe={wireframe}/>}</div>
      <div className="model-summary"><div><span>MODEL SIZE</span><strong>{displayedGeometry?displayedGeometry.dimensions.map((v:number)=>v.toPrecision(3)).join(' × ')+' m':'—'}</strong></div><div><span>SURFACE MESH</span><strong>{displayedGeometry?displayedGeometry.triangles.toLocaleString()+' triangles':'—'}</strong></div><div><span>GEOMETRY CHECK</span><strong className={geometryLabel.tone}>{geometryLabel.tone==='good'&&<CircleCheck size={15}/>}{geometryLabel.tone==='warn'&&<CircleAlert size={15}/>}{geometryLabel.label}</strong></div>{displayedGeometry?.geometry_fidelity==='wrapped'&&<div><span>FIDELITY</span><strong className="warn" title={displayedGeometry.wrap?.detail}><CircleAlert size={15}/> Wrapped approximation</strong></div>}</div>
      <div className="run-strip"><div><span className={`status-dot ${health?.worker?.online?'online':''}`}/><strong>{health?.worker?.online?'Compute worker ready':'Compute worker offline'}</strong><span>OpenFOAM {health?.openfoam_version||'2606'} · steady incompressible</span></div><small>{spec?'Configuration editable':'Geometry preparation available'}</small></div>
      {step===4&&samples&&<div className="run-details"><h3>{samples.kind==='line'?'Line sample':'Point probe'} · {field}</h3>{samples.kind==='line'?<TraceChart xLabel="Arc length · m" label={`Sampled ${field} versus arc length in metres`} series={[{name:field,color:'#71e1c3',values:samples.rows.filter((r:any)=>r.vtkValidPointMask!==0).map((r:any)=>[r.arc_length,field==='U'?Math.hypot(r['U:0'],r['U:1'],r['U:2']):r[field]])}]}/>:<div className="table-scroll"><table><tbody>{samples.columns.map((c:string)=><tr key={c}><th>{c}</th><td>{String(samples.rows[0]?.[c]??'Unavailable')}</td></tr>)}</tbody></table></div>}<a className="secondary spaced" href={`/api/runs/${samples.run_id}/files/views/${samples.id}/samples.csv`} download>Download samples · CSV</a></div>}
      {step>=3&&<RunDetails run={run} logs={logs}/>}
      {step>=4&&comparisonRun&&<ComparisonTable run={run} other={comparisonRun}/>}
    </main></div>
    {showAbout && <AboutModal onClose={()=>setShowAbout(false)} health={health}/>}
  </div>;
}
