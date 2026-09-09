import {useEffect,useState} from 'react';
import {api} from './api';

export function JobProgress({job,busy}:any){
  const [now,setNow]=useState(Date.now());
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
  if(!job&&!busy)return null;
  const active=['queued','running'].includes(job?.status),progress=job?.progress||(job?.status==='completed'?{percent:100,label:'Completed'}:undefined);
  const label=busy||job?.stage?.replaceAll('_',' ');
  return <div className="job-progress" role="status"><div><strong>{label}</strong><span>{progress?`${Math.round(progress.percent)}%`:''}</span></div><progress aria-label={busy||'Job progress'} max={100} value={busy?undefined:progress?.percent}/><p>{busy?'Working — duration depends on model size.':`${active?Math.round(now/1000-job.created)+' seconds · ':''}${progress?.label||job.status}`}</p>{active&&<small>You can inspect other stages. This task continues if you close the page.</small>}</div>;
}

export function HeaderJobProgress({mesh,run,busy,onNavigate}:any){
  const [now,setNow]=useState(Date.now());
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
  const activeMesh=['queued','running'].includes(mesh?.status);
  const activeRun=['queued','running'].includes(run?.status);
  const activeJob=activeMesh?mesh:activeRun?run:null;
  const isBusy=!!busy;
  if(!isBusy&&!activeJob) return null;
  const targetStep=activeMesh?2:activeRun?3:undefined;
  const progress=activeJob?.progress;
  const pct=progress?Math.round(progress.percent):undefined;
  const elapsed=activeJob?.created?Math.max(0,Math.round(now/1000-activeJob.created)):0;
  const label=isBusy?busy:(activeJob?.stage?.replaceAll('_',' ')||activeJob?.status);
  const subLabel=isBusy?'Working...':progress?.label||`${elapsed}s elapsed`;
  return <div className="header-job-progress" role="status" onClick={()=>targetStep!==undefined&&onNavigate?.(targetStep)} title={targetStep!==undefined?'Click to view job details':undefined} style={{cursor:targetStep!==undefined?'pointer':'default'}}><span className="pulse-indicator"/><div className="header-job-info"><div className="header-job-title"><strong>{label}</strong>{pct!==undefined&&<span>{pct}%</span>}</div><progress max={100} value={pct} aria-label={label}/><div className="header-job-meta"><small>{subLabel}</small>{targetStep!==undefined&&<span className="header-job-link">View stage →</span>}</div></div></div>;
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
  useEffect(()=>{setPending(undefined);if(!run)return;let alive=true;const poll=()=>api<any[]>(`/runs/${run.id}/views`).then(v=>{if(alive)setPending(v.find(x=>['running','queued'].includes(x.status)));}).catch(()=>{});poll();const timer=setInterval(poll,2500);return()=>{alive=false;clearInterval(timer);};},[run?.id]);
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
