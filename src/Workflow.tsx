import {JobProgress} from './Experience';
import {fmt} from './Controls';

function canonical(value:any):any {return Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])])):value;}
export function configurationKey(spec:any){if(!spec)return '';const {name,solver,project_id,...rest}=spec;return JSON.stringify(canonical({...rest,mesh:{...rest.mesh,body_level:rest.mesh?.body_level||0},use_case:rest.use_case||null}));}
export function meshCompatible(mesh:any,spec:any){return !!mesh&&configurationKey(mesh.spec)===configurationKey(spec);}

export function stageStates(geometry:any,spec:any,report:any,mesh:any,run:any,ack:string=''){
  const g=geometry?.diagnostics;
  const geometryReady=!!g&&g.watertight&&g.winding_consistent&&!g.nonmanifold_edges&&!g.degenerate_faces&&geometry.cad_valid!==false;
  const same=meshCompatible(mesh,spec);
  const active=(r:any)=>['queued','running'].includes(r?.status);
  return [!geometry?'Not started':geometryReady?'Ready':'Needs input',!spec?'Not started':!report?'Needs input':!report.valid?'Needs input':report.findings.some((f:any)=>f.status==='review')&&ack!==report.configuration_id?'Review required':'Ready',!mesh?'Not started':!same?'Outdated':active(mesh)?'Working':mesh.status==='failed'||mesh.status==='cancelled'?'Failed':mesh.result.reviewed_configuration_id===report?.configuration_id?'Ready':'Review required',!run?'Not started':active(run)?'Working':run.status==='failed'||run.status==='cancelled'?'Failed':run.status==='completed'?'Ready':'Needs input',!run?.result.fields_available?'Not started':run.result.numerical_status==='checks_passed'?'Ready':'Review required',run?.result.case_available?'Ready':'Not started'];
}

export function Checklist({step,geometry,report,mesh,run,spec,ack,setAck,onCheck,onNavigate}:any){
  let findings:any[]=[];
  if(step===0&&geometry){const d=geometry.diagnostics;findings=[{code:'closed',status:d.watertight?'pass':'fail',detail:'Closed fluid envelope or solid surfaces'},{code:'normals',status:d.winding_consistent?'pass':'fail',detail:'Consistent surface normals'},{code:'manifold',status:!d.nonmanifold_edges&&!d.degenerate_faces?'pass':'fail',detail:'Manifold surface without degenerate triangles'}];}
  if(step===1) findings=report?.findings||[];
  if(step===2) findings=mesh?.result.mesh_summary?.findings||[];
  if(step===3) findings=[{code:'mesh',status:meshCompatible(mesh,spec)&&mesh?.result.reviewed_configuration_id?'pass':'fail',detail:'A compatible mesh has been checked and reviewed',control:'mesh-resolution'}, {code:'worker',status:run?.status==='failed'?'fail':'info',detail:run?`Execution: ${run.status}`:'Ready to queue after mesh review'}];
  if(step===4) findings=run?.result.findings||[];
  if(step===5) findings=[{code:'case',status:run?.result.case_available?'pass':'unavailable',detail:'Raw case and logs available'}, {code:'fields',status:run?.result.fields_available?'pass':'unavailable',detail:'Solved field exports available'}];
  const review=report?.findings?.some((f:any)=>f.status==='review');
  return <section className="readiness"><details><summary>Checks · {findings.filter(f=>f.status==='fail').length} blockers, {findings.filter(f=>f.status==='review').length} review items</summary>{!findings.length&&<p className="hint">{step===2?'Generate a mesh to obtain quality evidence.':'Complete the inputs, then check readiness.'}</p>}{findings.map((f:any)=><div key={f.code} className={`finding ${f.status}`}><strong>{f.status.replaceAll('_',' ')} · </strong>{f.detail}{f.control&&f.status==='fail'&&<button className="text-button" onClick={()=>{onNavigate(f.stage==='geometry'?0:step===3?2:1);setTimeout(()=>{const el=document.getElementById(f.control);if(el){const details=el.querySelector('details');if(details)details.open=true;el.scrollIntoView({block:'center',behavior:'smooth'});}},50);}}>Go to setting</button>}</div>)}</details>{findings.some(f=>f.status==='fail')&&<p className="finding fail">Needs attention: {findings.find(f=>f.status==='fail').detail}</p>}{step===1&&<><button className="secondary full" onClick={onCheck}>Check setup</button>{review&&<label className="check-field"><input type="checkbox" checked={ack===report.configuration_id} onChange={e=>setAck(e.target.checked?report.configuration_id:'')}/> I reviewed the actionable setup warnings</label>}</>}</section>;
}


export function MeshProgress({mesh,spec,logs,onReview,onCancel,onSelect,onLoadSpec,onRecommend,runs}:any){
  const matching=meshCompatible(mesh,spec);
  const findings=mesh?.result.mesh_summary?.findings||[];
  const canReview=matching&&mesh?.status==='completed'&&mesh.result.mesh_check==='passed'&&findings.length>0&&!findings.some((f:any)=>['fail','unavailable'].includes(f.status));
  return <section><h3>Mesh attempt</h3><JobProgress job={mesh}/><label className="field">Previous mesh attempts<select value={mesh?.id||''} onChange={e=>onSelect(e.target.value)}><option value="">Select attempt</option>{runs.filter((r:any)=>r.kind==='mesh'&&r.spec.geometry_id===spec.geometry_id).map((r:any)=><option key={r.id} value={r.id}>{r.spec.name} · {r.status} · {r.id.slice(0,6)}</option>)}</select></label>{mesh?<><p className="hint">{matching?'Current configuration':'Outdated: geometry or physics changed'} · {mesh.id.slice(0,8)}</p><p>Volume cells: {fmt(mesh.result.cell_count,6)} · Preview: {mesh.result.mesh_available?'available':'unavailable'}</p>{!matching&&<button className="secondary full" onClick={()=>onLoadSpec(mesh.spec)}>Load this mesh’s setup</button>}{mesh.error&&<><p className="finding fail">{mesh.error}</p><button className="secondary full" onClick={onRecommend}>Apply recommended local refinement</button><p className="hint">Then generate a new mesh. Missing tiny face patches may also need component-surface preparation in Geometry.</p></>}{['queued','running'].includes(mesh.status)&&<button className="secondary full" onClick={onCancel}>Cancel mesh attempt</button>}<details><summary>Mesh logs and evidence</summary><pre className="log-output">{logs.join('\n')||'No log events yet.'}</pre>{['check','mesh_diagnostics','meshing','rotation'].map(log=><a className="secondary" key={log} href={`/api/runs/${mesh.id}/files/logs/${log}.log`} target="_blank" rel="noreferrer">{log} log</a>)}</details><button className="primary full spaced" disabled={!canReview} onClick={onReview}>{findings.some((f:any)=>f.status==='review')?'Acknowledge findings, review mesh & continue':'Review mesh & continue'}</button>{!canReview&&<p className="hint">A compatible completed mesh, passing checks, and a working preview are required.</p>}</>:<p className="hint">No mesh generated for this setup yet.</p>}</section>;
}
