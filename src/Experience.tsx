import {useEffect,useRef,useState} from 'react';
import {Wind, X} from 'lucide-react';
import {api} from './api';
import {usePolling} from './usePolling';

/** Tick once a second only while something is actually running. A permanently mounted
 *  interval re-renders the whole App subtree every second even on an empty workspace. */
function useElapsedClock(active:boolean){
  const [now,setNow]=useState(Date.now());
  useEffect(()=>{
    if(!active) return;
    const timer=setInterval(()=>setNow(Date.now()),1000);
    return()=>clearInterval(timer);
  },[active]);
  return active?now:Date.now();
}

export function JobProgress({job,busy}:any){
  const now=useElapsedClock(!!busy||['queued','running'].includes(job?.status));
  if(!job&&!busy)return null;
  const active=['queued','running'].includes(job?.status),progress=job?.progress||(job?.status==='completed'?{percent:100,label:'Completed'}:undefined);
  const label=busy||job?.stage?.replaceAll('_',' ');
  return <div className="job-progress" role="status"><div><strong>{label}</strong><span>{progress?`${Math.round(progress.percent)}%`:''}</span></div><progress aria-label={busy||'Job progress'} max={100} value={busy?undefined:progress?.percent}/><p>{busy?'Working — duration depends on model size.':`${active?Math.round(now/1000-job.created)+' seconds · ':''}${progress?.label||job.status}`}</p>{active&&<small>You can inspect other stages. This task continues if you close the page.</small>}</div>;
}

export function HeaderJobProgress({mesh,run,busy,onNavigate}:any){
  const activeMesh=['queued','running'].includes(mesh?.status);
  const activeRun=['queued','running'].includes(run?.status);
  const activeJob=activeMesh?mesh:activeRun?run:null;
  const isBusy=!!busy;
  const now=useElapsedClock(isBusy||!!activeJob);
  if(!isBusy&&!activeJob) return null;
  const targetStep=activeMesh?2:activeRun?3:undefined;
  const progress=activeJob?.progress;
  const pct=progress?Math.round(progress.percent):undefined;
  const elapsed=activeJob?.created?Math.max(0,Math.round(now/1000-activeJob.created)):0;
  const label=isBusy?busy:(activeJob?.stage?.replaceAll('_',' ')||activeJob?.status);
  const subLabel=isBusy?'Working...':progress?.label||`${elapsed}s elapsed`;
  const body=<><span className="pulse-indicator"/><div className="header-job-info"><div className="header-job-title"><strong>{label}</strong>{pct!==undefined&&<span>{pct}%</span>}</div><progress max={100} value={pct} aria-label={label}/><div className="header-job-meta"><small>{subLabel}</small>{targetStep!==undefined&&<span className="header-job-link">View stage →</span>}</div></div></>;
  // A real button when it navigates, so it is reachable and activatable by keyboard.
  return targetStep!==undefined
    ? <button type="button" className="header-job-progress" onClick={()=>onNavigate?.(targetStep)} title="View job details">{body}</button>
    : <div className="header-job-progress" role="status">{body}</div>;
}

export function StageGuide({step,geometry}:any){
  const tips=[['Start with a model','Import a STEP assembly, check its size, then prepare a mesh-friendly revision. Your original CAD is kept.'],['Tell us what is moving','Choose a use case and fluid. Set speed and direction using the arrows in the 3D view. Then select the measured or rotating components.'],['Inspect the cells around the model','The mesh divides the fluid into cells. A section cuts through those cells; the hole is the solid object. Check that thin features are still present.'],['Run the steady simulation','Progress reports solver iterations, not physical time. A finished run still needs convergence and mesh-sensitivity review.'],['Explore the flow','Choose pressure, a velocity slice or streamlines. Show the object for context, and animate tracers along the solved streamlines.'],['Keep your results','Export the case and numerical findings together so the setup and limitations stay with your measurements.']];
  return <div className="stage-guide"><strong>{tips[step][0]}</strong><p>{tips[step][1]}</p>{step===0&&!geometry&&<p>Use New simulation above to keep separate designs and attempts organized.</p>}</div>;
}

export function ViewControls({pose,setPose,showObject,setShowObject,animate,setAnimate,kind,result,meshPlane,setMeshPlane,mesh}:any){
  return <div className="view-controls"><div className="button-group" aria-label="Camera views">{[['iso','3D'],['x','Front'],['y','Side'],['z','Top']].map(([id,label])=><button className={pose===id?'active':''} key={id} onClick={()=>setPose(id)}>{label}</button>)}</div>{mesh&&<label>Mesh view <select value={meshPlane} onChange={e=>setMeshPlane(e.target.value)}><option value="y">XZ section · through model</option><option value="x">YZ section · through model</option><option value="z">XY section · through model</option><option value="exterior">Full mesh boundary</option></select></label>}{(result||mesh)&&<label><input type="checkbox" checked={showObject} onChange={e=>setShowObject(e.target.checked)}/> Show object</label>}{result&&kind==='streamlines'&&<><button onClick={()=>setAnimate(!animate)}>{animate?'Pause tracers':'Animate tracers'}</button><small>Playback of steady streamlines; not a transient simulation.</small></>}</div>;
}

export function QuickViews({run,onView,act}:any){
  const [pending,setPending]=useState<any>();
  useEffect(()=>{setPending(undefined);},[run?.id]);
  usePolling(async()=>{
    if(!run) return;
    const views=await api<any[]>(`/runs/${run.id}/views`);
    setPending(views.find(x=>['running','queued'].includes(x.status)));
  },2500,!!run);
  const choose=(kind:string)=>act('Preparing '+kind,async()=>{
    const spec=run.spec,geometry=await api(`/geometry/${spec.geometry_id}`),center=geometry.bounds[0].map((n:number,i:number)=>(n+geometry.bounds[1][i])/2);
    const a=spec.flow.alpha_deg*Math.PI/180,b=spec.flow.beta_deg*Math.PI/180;
    const direction=spec.rotation.enabled&&spec.flow.speed===0?spec.rotation.axis:[Math.cos(a)*Math.cos(b),Math.sin(b),Math.sin(a)*Math.cos(b)];
    const length=Math.max(...geometry.dimensions);
    const normal=Math.abs(direction[2])<.9?[-direction[1],direction[0],0]:[0,-direction[2],direction[1]];
    const request={kind,field:kind==='surface'?'pressure_pa':'U',origin:kind==='streamlines'?center.map((n:number,i:number)=>n-direction[i]*length*.7):center,normal,seed_radius:length*.65,resolution:180};
    const saved=await api<any[]>(`/runs/${run.id}/views`);
    const match=saved.find(v=>v.status==='completed'&&v.spec.kind===kind&&v.spec.field===request.field);
    if(!match)setPending({spec:request,status:'queued'});await onView(match?.spec||request,match?.id);
  });
  return <section className="quick-views"><h3>Choose a result view</h3><div className="view-cards">{[['surface','Surface pressure'],['slice','Velocity section'],['streamlines','Streamlines']].map(([kind,label])=><button className="secondary" key={kind} disabled={!run?.result.fields_available||!!pending} onClick={()=>choose(kind)}>{label}</button>)}</div>{pending&&<div className="job-progress"><progress aria-label="Extracting view"/><p>{pending.spec.kind} · {pending.status}. The solved run is preserved.</p></div>}<p className="hint">Streamlines follow the calculated velocity field. Use Show object and Animate tracers above the viewport.</p></section>;
}

export function AboutModal({onClose,health}:any){
  const dialog=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    const opener=document.activeElement as HTMLElement|null;
    const focusable=()=>Array.from(dialog.current?.querySelectorAll<HTMLElement>(
      'button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')||[]);
    focusable()[0]?.focus();
    const onKey=(e:KeyboardEvent)=>{
      if(e.key==='Escape'){onClose();return;}
      if(e.key!=='Tab')return;
      // Keep Tab inside the dialog; otherwise focus walks the page behind it.
      const items=focusable();
      if(!items.length)return;
      const first=items[0],last=items[items.length-1];
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
      else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
    };
    window.addEventListener('keydown',onKey);
    return()=>{window.removeEventListener('keydown',onKey);opener?.focus?.();};
  },[onClose]);
  return <div className="modal-backdrop" onClick={onClose}><div ref={dialog} className="about-dialog" onClick={e=>e.stopPropagation()} role="dialog" aria-modal="true" aria-label="About GustSim and system status">
    <div className="about-header"><div className="brand"><Wind size={26}/><span>gust<span>sim</span></span></div><button className="about-close" onClick={onClose} aria-label="Close dialog"><X size={18}/></button></div>
    <p className="about-subtitle">Local Web-Based Engineering CFD Workbench</p>
    <div className="about-grid">
      <div><span>VERSION</span><strong>v0.1</strong></div>
      <div><span>DEPLOYMENT</span><strong>Local Docker / Desktop</strong></div>
      <div><span>SOLVER</span><strong>OpenCFD OpenFOAM {health?.openfoam_version||'2606'}</strong></div>
      <div><span>PHYSICS</span><strong>Steady Incompressible (Air/Water)</strong></div>
      <div><span>TURBULENCE</span><strong>Laminar & k–ω SST · 1 MRF Region</strong></div>
      <div><span>COMPUTE WORKER</span><strong className={health?.worker?.online?'good':'bad'}>{health?.worker?.online?'Online':'Offline'}</strong></div>
      <div><span>POST-PROCESSING</span><strong>ParaView / vtk.js</strong></div>
      <div><span>DATA PRIVACY</span><strong>Local Named Volume (No Cloud)</strong></div>
    </div>
    <div className="about-notes">
      <p><strong>Workflow:</strong> Geometry → Setup → Mesh → Run → Results → Export</p>
      <p>All geometry and results remain private on this local machine. Numerical convergence and benchmark validation remain distinct engineering verification states.</p>
    </div>
    <div className="about-footer"><button className="primary" onClick={onClose}>Close</button></div>
  </div></div>;
}
