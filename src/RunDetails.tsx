import { useState } from 'react';
import { fmt } from './Controls';

// Dash patterns so series stay distinguishable without relying on colour alone.
const DASHES=['','5 3','1.5 3','8 3 1.5 3','3 2','10 4'];
const PLOT={left:52,right:600,top:25,bottom:155};

export function TraceChart({series,logarithmic=false,label,xLabel='SIMPLE iteration',band}:{series:{name:string;color:string;values:[number,number][]}[];logarithmic?:boolean;label:string;xLabel?:string;band?:[number,number]}){
  const [hover,setHover]=useState<number|null>(null);
  const clean=(values:[number,number][])=>values.filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1])&&(!logarithmic||p[1]>0));
  const all=series.flatMap(s=>clean(s.values));
  if(!all.length)return <div className="chart-empty">{label} appears after a solve.</div>;
  const transform=(v:number)=>logarithmic?Math.log10(Math.max(v,1e-16)):v;
  // Reduce rather than spread: Math.min(...arr) overflows the argument limit past ~125k points.
  let minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity;
  for(const [px,py] of all){
    if(px<minX)minX=px; if(px>maxX)maxX=px;
    const t=transform(py);
    if(t<minY)minY=t; if(t>maxY)maxY=t;
  }
  if(minY===maxY){minY-=.5;maxY+=.5;}
  const width=PLOT.right-PLOT.left,height=PLOT.bottom-PLOT.top;
  const x=(v:number)=>PLOT.left+(v-minX)/(maxX-minX||1)*width;
  const y=(v:number)=>PLOT.bottom-(transform(v)-minY)/(maxY-minY||1)*height;
  const ticks=[0,.25,.5,.75,1];
  const readout=hover===null?null:series.map(s=>{
    const values=clean(s.values);
    if(!values.length)return null;
    let best=values[0];
    for(const point of values) if(Math.abs(point[0]-hover)<Math.abs(best[0]-hover)) best=point;
    return {name:s.name,color:s.color,point:best};
  }).filter(Boolean) as {name:string;color:string;point:[number,number]}[];
  function onMove(event:React.MouseEvent<SVGSVGElement>){
    const box=event.currentTarget.getBoundingClientRect();
    const local=(event.clientX-box.left)/box.width*620;
    if(local<PLOT.left||local>PLOT.right){setHover(null);return;}
    setHover(minX+(local-PLOT.left)/width*(maxX-minX||1));
  }
  return <div className="trace-chart">
    <svg viewBox="0 0 620 195" role="img" aria-label={label} onMouseMove={onMove} onMouseLeave={()=>setHover(null)}>
      {band&&<rect x={x(band[0])} width={Math.max(0,x(band[1])-x(band[0]))} y={PLOT.top} height={height} fill="#71e1c314"/>}
      {ticks.map(f=><g key={f}>
        <line x1={PLOT.left} x2={PLOT.right} y1={PLOT.top+f*height} y2={PLOT.top+f*height} stroke="#2d3c49"/>
        <text x="4" y={PLOT.top+5+f*height} fill="#94a6b8" fontSize="11">{logarithmic?`10^${(maxY-f*(maxY-minY)).toFixed(1)}`:fmt(maxY-f*(maxY-minY),3)}</text>
      </g>)}
      {ticks.map(f=><text key={'x'+f} x={x(minX+f*(maxX-minX))} y="178" fill="#94a6b8" fontSize="11" textAnchor="middle">{fmt(minX+f*(maxX-minX),3)}</text>)}
      {series.map((s,i)=><polyline key={s.name} points={clean(s.values).map(p=>`${x(p[0])},${y(p[1])}`).join(' ')} fill="none" stroke={s.color} strokeWidth="1.8" strokeDasharray={DASHES[i%DASHES.length]||undefined}/>)}
      {hover!==null&&<line x1={x(hover)} x2={x(hover)} y1={PLOT.top} y2={PLOT.bottom} stroke="#71e1c3" strokeWidth="1" strokeDasharray="3 3"/>}
      {readout?.map(r=><circle key={r.name} cx={x(r.point[0])} cy={y(r.point[1])} r="3" fill={r.color}/>)}
    </svg>
    <div className="chart-key">{series.map((s,i)=><span key={s.name}><i style={{background:s.color,borderBottom:DASHES[i%DASHES.length]?'2px dotted #0c1219':'none'}}/>{s.name}</span>)}<small>{xLabel}</small></div>
    {readout&&!!readout.length&&<div className="chart-readout">{xLabel.split('·')[0].trim()} {fmt(readout[0].point[0],5)}{readout.map(r=><span key={r.name}><i style={{background:r.color}}/>{r.name} {fmt(r.point[1],4)}</span>)}</div>}
  </div>;
}
export default function RunDetails({run,logs}:{run:any;logs:string[]}){
  const result=run?.result; const m=result?.metrics||{};
  // Cd/Cl per iteration: the forces are already recorded per iteration, and a coefficient
  // that is still drifting is the clearest sign a run stopped too early. Only defined when
  // the reference quantities were confirmed, matching quality.analyze's own gate.
  const speed=run?.spec?.flow?.speed??0, density=run?.spec?.fluid?.density??0, area=run?.spec?.references?.area??0;
  const dynamic=0.5*density*speed*speed*area;
  const coefficientsAvailable=dynamic>0&&!!run?.spec?.use_case?.references_confirmed&&!!result?.projected_history?.length;
  const coefficients=coefficientsAvailable
    ?['drag_n','lift_n'].map((key,i)=>({name:i?'Cl':'Cd',color:i?'#89b9ff':'#77e5c3',
        values:result.projected_history.map((r:any)=>[r.iteration,r[key]/dynamic] as [number,number])}))
    :[];
  // Highlight the window quality.analyze uses to judge load stability.
  const iterations:number[]=(result?.projected_history||result?.history||[]).map((r:any)=>r.iteration);
  const band:[number,number]|undefined=iterations.length>20?[iterations[iterations.length-20],iterations[iterations.length-1]]:undefined;
  const common=[['Mass imbalance','mass_imbalance','fraction']];
  const byUse:Record<string,string[][]>={pipe:[['Outlet volume flow','outlet_volume_flow_m3s','m³/s'],['Outlet mass flow','outlet_mass_flow_kgs','kg/s'],['Inlet static pressure','inlet_pressure_pa','Pa gauge'],['Outlet static pressure','outlet_pressure_pa','Pa gauge'],['Static pressure drop','pressure_drop_pa','Pa'],['Total-pressure loss','total_pressure_loss_pa','Pa'],['Equivalent head loss','head_loss_m','m']],aerodynamics:[['Drag','drag_n','N'],['Lift','lift_n','N'],['Drag coefficient','cd',''],['Lift coefficient','cl','']],propeller:[['Rotor thrust','thrust_n','N'],['Fluid torque · positive axis','fluid_torque_nm','N·m'],['Required driving torque','torque_nm','N·m'],['Required shaft power','shaft_power_w','W'],['Propulsive efficiency','efficiency',''],['Thrust coefficient','ct',''],['Torque coefficient','cq','']]};
  const quantities=byUse[run?.spec?.use_case?.kind];

  return <div className="run-details"><div className="details-heading"><div><span className="eyebrow">SIMULATION RECORD</span><h2>{run?.spec?.name||'No run selected'}</h2></div>{run&&<span className={`run-badge ${run.status}`}>{run.status} · {run.stage}</span>}</div>
    {run&&<div className="evidence-row"><div><span>EXECUTION</span><strong>{run.status}{result?.termination?` · ${String(result.termination).replaceAll('_',' ')}`:''}</strong></div><div><span>NUMERICAL QUALITY</span><strong className={result?.numerical_status==='checks_passed'?'good':'warn'}>{result?.numerical_status==='checks_passed'?'Checks passed':result?.numerical_status?'Review required':'Not available yet'}</strong></div><div><span>EXPERIMENTAL VALIDATION</span><strong className="warn">Pending</strong></div></div>}
    {run?.error&&<p className="finding fail">{run.error}</p>}
    {result?.metrics&&<><div className="metrics-grid">{[...(quantities||[...byUse.aerodynamics,...byUse.propeller,...byUse.pipe]),...common].map(([name,key,unit])=><div key={key} title={result.analysis?.definitions?.[key]}><span>{name}</span><strong>{fmt(key==='mass_imbalance'?result.mass_imbalance:m[key])}<small>{unit}</small></strong>{m[key]===null&&<p className="hint">{result.analysis?.unavailable?.[key]||'Unavailable for this run'}</p>}</div>)}</div><p className="hint">“—” indicates an unavailable or undefined quantity. A completed execution is not an experimentally validated result.</p>
      <div className="chart-grid"><section><h3>Initial residuals</h3><TraceChart label="Residual history" logarithmic series={['p','Ux','Uy','Uz','k','omega'].map((field,i)=>({name:field,color:['#77e5c3','#89b9ff','#d5a8ff','#edba76','#ff9595','#bdd66d'][i],values:(result.residuals||[]).filter((r:any)=>r.field===field).map((r:any)=>[r.iteration,r.initial])}))}/></section><section><h3>Force stability</h3><TraceChart label="Force history in newtons" band={band} series={run.spec?.use_case?.kind==='aerodynamics'&&result.projected_history?.length?['drag_n','lift_n'].map((key,i)=>({name:i?'Lift':'Drag',color:i?'#89b9ff':'#77e5c3',values:result.projected_history.map((r:any)=>[r.iteration,r[key]])})):['Fx','Fy','Fz'].map((name,i)=>({name,color:['#77e5c3','#89b9ff','#edba76'][i],values:(result.history||[]).map((r:any)=>[r.iteration,r.force?.[i]]).filter((v:any[])=>v[1]!=null)}))}/></section>{coefficientsAvailable&&<section><h3>Force coefficients</h3><TraceChart label="Drag and lift coefficient against iteration" band={band} series={coefficients}/><p className="hint">Cd and Cl from the same forces as the table above, divided by ½·ρ·V²·A ({fmt(dynamic)} Pa·m²). The shaded band is the last 20 iterations used for the stability check.</p></section>}</div>
      {run.spec?.rotation?.enabled&&<><p className="hint">Thrust follows the saved thrust axis. Fluid torque is about the rotor origin, projected on the positive rotation axis. Driving torque is positive in the commanded rotation direction. Efficiency is undefined at zero advance speed.</p><TraceChart label="Rotor thrust history" series={[{name:'Thrust · N',color:'#71e1c3',values:(result.rotor_history||[]).map((r:any)=>[r.iteration,(r.force||[]).reduce((sum:number,n:number,i:number)=>sum+n*(run.spec?.references?.thrust_axis?.[i]??0),0)])}]}/>{result.rotor_load_history?.length>0&&<TraceChart label="Rotor torque history" series={['fluid_torque_nm','torque_nm'].map((key,i)=>({name:i?'Driving torque · N·m':'Fluid torque · N·m',color:i?'#edba76':'#89b9ff',values:result.rotor_load_history.map((r:any)=>[r.iteration,r[key]])}))}/>}</>}
      {run.spec?.mode==='internal'&&<p className="hint">Pressure is gauge Pa. Static pressure drop and total-pressure loss are separate measurements. Head loss uses 9.80665 m/s² with no elevation correction. Fluxes are positive out of the domain.</p>}
      {!!Object.keys(result.components||{}).length&&<details><summary>Component loads</summary><div className="table-scroll"><table><thead><tr><th>Surface</th><th>Fx · N</th><th>Fy · N</th><th>Fz · N</th></tr></thead><tbody>{Object.entries(result.components).map(([name,v]:any)=><tr key={name}><td>{name}</td>{(v.force||[null,null,null]).map((n:number,i:number)=><td key={i}>{fmt(n)}</td>)}</tr>)}</tbody></table></div></details>}
    </>}
    {run&&<details open={run.status==='running'||run.status==='failed'}><summary>Execution log <small>{run.id.slice(0,8)}</small></summary><pre className="log-output">{logs.length?logs.join('\n'):'No log events recorded.'}</pre></details>}
  </div>;
}

const COMPARE:[string,string,string][]=[
  ['Drag','drag_n','N'],['Lift','lift_n','N'],['Drag coefficient','cd',''],['Lift coefficient','cl',''],
  ['Rotor thrust','thrust_n','N'],['Required driving torque','torque_nm','N·m'],['Required shaft power','shaft_power_w','W'],
  ['Outlet volume flow','outlet_volume_flow_m3s','m³/s'],['Static pressure drop','pressure_drop_pa','Pa'],
  ['Total-pressure loss','total_pressure_loss_pa','Pa'],
];

/** Side-by-side numbers for two runs. Two pictures do not answer "did it get better".  */
export function ComparisonTable({run,other}:{run:any;other:any}){
  if(!run||!other) return null;
  const a=run.result?.metrics||{},b=other.result?.metrics||{};
  const rows=COMPARE.filter(([,key])=>Number.isFinite(a[key])||Number.isFinite(b[key]));
  if(!rows.length) return null;
  return <div className="run-details"><h3>Comparison</h3>
    <div className="table-scroll"><table className="compare-table">
      <thead><tr><th>Quantity</th><th>{run.spec?.name||'This run'}</th><th>{other.spec?.name||'Other run'}</th><th>Change</th></tr></thead>
      <tbody>{rows.map(([name,key,unit])=>{
        const first=a[key],second=b[key];
        const comparable=Number.isFinite(first)&&Number.isFinite(second)&&Math.abs(first)>1e-12;
        const delta=comparable?(second-first)/Math.abs(first)*100:null;
        return <tr key={key}>
          <td>{name}{unit&&<small> · {unit}</small>}</td>
          <td>{fmt(first)}</td>
          <td>{fmt(second)}</td>
          <td>{delta===null?'—':`${delta>0?'+':''}${delta.toFixed(1)}%`}</td>
        </tr>;
      })}</tbody>
    </table></div>
    <p className="hint">Change is the other run relative to this one. A difference is only meaningful once both runs pass their own numerical checks; neither is an experimentally validated result.</p>
  </div>;
}
