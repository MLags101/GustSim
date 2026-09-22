import {JobProgress} from './Experience';
import {fmt} from './Controls';

function canonical(value:any):any {return Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])])):value;}

// Specs are replaced immutably, so identity is a sound cache key. Without this the
// recursive sort + stringify runs about a dozen times per render (readiness, checklist,
// mesh compatibility, toolbar), on a spec that can hold hundreds of boundaries.
const keyCache=new WeakMap<object,string>();
export function configurationKey(spec:any){
  if(!spec)return '';
  const cached=keyCache.get(spec);
  if(cached!==undefined)return cached;
  const {name,solver,project_id,...rest}=spec;
  const value=JSON.stringify(canonical({...rest,mesh:{...rest.mesh,body_level:rest.mesh?.body_level||0},use_case:rest.use_case||null}));
  keyCache.set(spec,value);
  return value;
}
export function meshCompatible(mesh:any,spec:any){return !!mesh&&configurationKey(mesh.spec)===configurationKey(spec);}

export function geometrySummary(geometry:any){
  if(!geometry?.diagnostics)return {tone:'',label:'Awaiting import'};
  const d=geometry.diagnostics;
  if(!d.watertight)return {tone:'warn',label:'Open surface'};
  if(d.degenerate_faces||!d.winding_consistent)return {tone:'warn',label:'Needs repair'};
  if(d.nonmanifold_edges)return {tone:'warn',label:`Closed · ${d.nonmanifold_edges} non-manifold edges`};
  return {tone:'good',label:'Closed surface'};
}

export function preparationNote(current:any,candidate:any){
  const d=candidate.diagnostics;
  const counts=`Preparation preview · triangles ${current.triangles.toLocaleString()} → ${candidate.triangles.toLocaleString()} · open edges ${current.diagnostics.open_edges} → ${d.open_edges} · nonmanifold ${current.diagnostics.nonmanifold_edges} → ${d.nonmanifold_edges} · degenerate ${current.diagnostics.degenerate_faces} → ${d.degenerate_faces}.`;
  let verdict='Openings remain; inspect and cap only intended planar ports.';
  if(!d.watertight&&d.nonmanifold_edges)verdict='The surface is still open, and non-manifold connections remain.';
  else if(d.watertight&&d.nonmanifold_edges)verdict=`Closed, with ${d.nonmanifold_edges} non-manifold edges where parts meet. Meshing can proceed. Wrapping builds one clean surface.`;
  else if(d.watertight)verdict='Closed surface.';
  if(candidate.geometry_fidelity==='wrapped')verdict=`Wrapped approximation. ${candidate.wrap?.detail||'Loads on this revision are loads on the wrap.'}`;
  return `${counts} ${verdict}`;
}

export function stageStates(geometry:any,spec:any,report:any,mesh:any,run:any,ack:string=''){
  const g=geometry?.diagnostics;
  // Mirrors workflow.geometry_findings: non-manifold junctions and a wrapped
  // approximation are review items, not blockers. Only a surface that is not closed,
  // has degenerate triangles, or has inconsistent normals stops meshing.
  const geometryReady=!!g&&g.watertight&&g.winding_consistent&&!g.degenerate_faces;
  const same=meshCompatible(mesh,spec);
  const active=(r:any)=>['queued','running'].includes(r?.status);
  return [!geometry?'Not started':geometryReady?'Ready':'Needs input',!spec?'Not started':!report?'Needs input':!report.valid?'Needs input':report.findings.some((f:any)=>f.status==='review')&&ack!==report.configuration_id?'Review required':'Ready',!mesh?'Not started':!same?'Outdated':active(mesh)?'Working':mesh.status==='failed'||mesh.status==='cancelled'?'Failed':mesh.result.reviewed_configuration_id===report?.configuration_id?'Ready':'Review required',!run?'Not started':active(run)?'Working':run.status==='failed'||run.status==='cancelled'?'Failed':run.status==='completed'?'Ready':'Needs input',!run?.result.fields_available?'Not started':run.result.numerical_status==='checks_passed'?'Ready':'Review required',run?.result.case_available?'Ready':'Not started'];
}

export function Checklist({step,geometry,report,mesh,run,spec,ack,setAck,onCheck,onNavigate}:any){
  let findings:any[]=[];
  if(step===0&&geometry){const d=geometry.diagnostics;findings=[
    {code:'closed',status:d.watertight?'pass':'fail',detail:d.watertight?'Closed fluid envelope or solid surfaces':'Surface is not closed. Auto prepare fixes simple defects; for an assembly whose parts touch or overlap, use Wrap.'},
    {code:'normals',status:d.winding_consistent?'pass':'fail',detail:'Consistent surface normals'},
    {code:'triangles',status:!d.degenerate_faces?'pass':'fail',detail:'No degenerate triangles'},
    {code:'manifold',status:!d.nonmanifold_edges?'pass':'review',detail:d.nonmanifold_edges?`${d.nonmanifold_edges} non-manifold edges where parts meet. Meshing can proceed on a closed surface; wrap for a single clean surface.`:'No non-manifold edges'}];
    if(geometry.geometry_fidelity==='wrapped')findings.push({code:'fidelity',status:'review',detail:geometry.wrap?.detail||'Geometry is a shrink-wrapped approximation of the imported CAD.'});}
  if(step===1) findings=report?.findings||[];
  if(step===2) findings=mesh?.result.mesh_summary?.findings||[];
  if(step===3) findings=[{code:'mesh',status:meshCompatible(mesh,spec)&&mesh?.result.reviewed_configuration_id?'pass':'fail',detail:'A compatible mesh has been checked and reviewed',control:'mesh-resolution'}, {code:'worker',status:run?.status==='failed'?'fail':'info',detail:run?`Execution: ${run.status}`:'Ready to queue after mesh review'}];
  if(step===4) findings=run?.result.findings||[];
  if(step===5) findings=[{code:'case',status:run?.result.case_available?'pass':'unavailable',detail:'Raw case and logs available'}, {code:'fields',status:run?.result.fields_available?'pass':'unavailable',detail:'Solved field exports available'}];
  const review=report?.findings?.some((f:any)=>f.status==='review');
  return <section className="readiness"><details><summary>{(()=>{const blockers=findings.filter(f=>f.status==='fail').length,reviews=findings.filter(f=>f.status==='review').length;if(!blockers&&!reviews)return findings.length?'Checks · all clear':'Checks';return `Checks · ${blockers?`${blockers} ${blockers===1?'blocker':'blockers'}`:''}${blockers&&reviews?', ':''}${reviews?`${reviews} to review`:''}`;})()}</summary>{!findings.length&&<p className="hint">{step===2?'Generate a mesh to obtain quality evidence.':'Complete the inputs, then check readiness.'}</p>}{findings.map((f:any)=><div key={f.code} className={`finding ${f.status}`}><strong>{f.status.replaceAll('_',' ')} · </strong>{f.detail}{f.control&&f.status==='fail'&&<button className="text-button" onClick={()=>{onNavigate(f.stage==='geometry'?0:step===3?2:1);setTimeout(()=>{const el=document.getElementById(f.control);if(el){const details=el.querySelector('details');if(details)details.open=true;el.scrollIntoView({block:'center',behavior:'smooth'});}},50);}}>Go to setting</button>}</div>)}</details>{findings.some(f=>f.status==='fail')&&<p className="finding fail">Needs attention: {findings.find(f=>f.status==='fail').detail}</p>}{step===1&&<><button className="secondary full" onClick={onCheck}>Check setup</button>{review&&<label className="check-field"><input type="checkbox" checked={ack===report.configuration_id} onChange={e=>setAck(e.target.checked?report.configuration_id:'')}/> I reviewed the actionable setup warnings</label>}</>}</section>;
}



/** checkMesh numbers and achieved near-wall layers, which the logs already report.
 *  Requested layers are a request; these are the measurement. */
export function MeshQuality({summary}:any){
  const m=summary?.mesh_metrics,coverage=summary?.layer_coverage;
  if(!m&&!coverage) return null;
  const rows:[string,string][]=[];
  if(m?.max_non_orthogonality!=null) rows.push(['Max non-orthogonality',`${fmt(m.max_non_orthogonality,4)}°  (average ${fmt(m.average_non_orthogonality,3)}°)`]);
  if(m?.max_skewness!=null) rows.push(['Max skewness',fmt(m.max_skewness,4)]);
  if(m?.max_aspect_ratio!=null) rows.push(['Max aspect ratio',fmt(m.max_aspect_ratio,4)]);
  if(m?.severely_non_orthogonal_faces!=null) rows.push(['Severely non-orthogonal faces',String(m.severely_non_orthogonal_faces)]);
  if(m?.negative_volume_cells) rows.push(['Negative cell volumes','present']);
  if(coverage) rows.push(['Near-wall layers achieved',`${fmt(coverage.mean_layers,3)} average · worst patch ${fmt(coverage.min_layers,3)} · ${coverage.faces} wall faces`]);
  if(!rows.length) return null;
  return <dl className="diagnostic-list">{rows.map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>;
}

export function MeshProgress({mesh,spec,logs,onReview,onCancel,onSelect,onLoadSpec,onRecommend,runs}:any){
  const matching=meshCompatible(mesh,spec);
  const findings=mesh?.result.mesh_summary?.findings||[];
  const canReview=matching&&mesh?.status==='completed'&&mesh.result.mesh_check==='passed'&&findings.length>0&&!findings.some((f:any)=>['fail','unavailable'].includes(f.status));
  return <section><h3>Mesh attempt</h3><JobProgress job={mesh}/><label className="field">Previous mesh attempts<select value={mesh?.id||''} onChange={e=>onSelect(e.target.value)}><option value="">Select attempt</option>{runs.filter((r:any)=>r.kind==='mesh'&&r.spec.geometry_id===spec.geometry_id).map((r:any)=><option key={r.id} value={r.id}>{r.spec.name} · {r.status} · {r.id.slice(0,6)}</option>)}</select></label>{mesh?<><p className="hint">{matching?'Current configuration':'Outdated: geometry or physics changed'} · {mesh.id.slice(0,8)}</p><p>Volume cells: {fmt(mesh.result.cell_count,6)} · Preview: {mesh.result.mesh_available?'available':'unavailable'}</p><MeshQuality summary={mesh.result.mesh_summary}/>{!matching&&<button className="secondary full" onClick={()=>onLoadSpec(mesh.spec)}>Load this mesh’s setup</button>}{mesh.error&&<><p className="finding fail">{mesh.error}</p><button className="secondary full" onClick={onRecommend}>Apply recommended local refinement</button><p className="hint">Then generate a new mesh. Missing tiny face patches may also need component-surface preparation in Geometry.</p></>}{['queued','running'].includes(mesh.status)&&<button className="secondary full" onClick={onCancel}>Cancel mesh attempt</button>}<details><summary>Mesh logs and evidence</summary><pre className="log-output">{logs.join('\n')||'No log events yet.'}</pre>{['check','mesh_diagnostics','meshing','rotation'].map(log=><a className="secondary" key={log} href={`/api/runs/${mesh.id}/files/logs/${log}.log`} target="_blank" rel="noreferrer">{log} log</a>)}</details><button className="primary full spaced" disabled={!canReview} onClick={onReview}>{findings.some((f:any)=>f.status==='review')?'Acknowledge findings, review mesh & continue':'Review mesh & continue'}</button>{!canReview&&<p className="hint">A compatible completed mesh, passing checks, and a working preview are required.</p>}</>:<p className="hint">No mesh generated for this setup yet.</p>}</section>;
}
