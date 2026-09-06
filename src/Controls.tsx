import type { ReactNode } from 'react';
export function NumberField({label,value,onChange,step='any',min,max}:{label:string;value:number;onChange:(n:number)=>void;step?:string|number;min?:number;max?:number}){
  return <label className="field">{label}<input type="number" step={step} min={min} max={max} value={Number.isFinite(value)?value:''} onChange={e=>{if(e.target.value!==''&&Number.isFinite(e.target.valueAsNumber))onChange(e.target.valueAsNumber);}}/></label>;
}
export function VectorField({label,value,onChange}:{label:string;value:number[];onChange:(v:number[])=>void}){return <div className="field">{label}<div className="vector-fields">{value.map((v,i)=><label key={i}><small>{'XYZ'[i]}</small><input aria-label={`${label} ${'XYZ'[i]}`} type="number" step="any" value={v} onChange={e=>{if(e.target.value!==''&&Number.isFinite(e.target.valueAsNumber))onChange(value.map((n,j)=>i===j?e.target.valueAsNumber:n));}}/></label>)}</div></div>;}
export function SelectField({label,value,onChange,options}:{label:string;value:string;onChange:(s:string)=>void;options:(string|[string,string])[]}){return <label className="field">{label}<select value={value} onChange={e=>onChange(e.target.value)}>{options.map(o=><option key={typeof o==='string'?o:o[0]} value={typeof o==='string'?o:o[0]}>{typeof o==='string'?o:o[1]}</option>)}</select></label>;}
export function Section({title,children}:{title:string;children:ReactNode}){return <section><h3>{title}</h3>{children}</section>;}
export const fmt=(v:any,digits=4)=>typeof v==='number'&&Number.isFinite(v)?v.toPrecision(digits):'—';
