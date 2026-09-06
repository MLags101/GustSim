import vtkPolyData from '@kitware/vtk.js/Common/DataModel/PolyData';
const cross=(a:number[],b:number[])=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const unit=(v:number[])=>{const n=Math.hypot(...v);return n>1e-12?v.map(x=>x/n):[1,0,0];};
export function overlayGeometry(rotation:any,domain:any){
  const points:number[]=[],lines:number[]=[];
  const segment=(a:number[],b:number[])=>{const i=points.length/3;points.push(...a,...b);lines.push(2,i,i+1);};
  if(domain){const lo=domain.minimum,hi=domain.maximum,corners=Array.from({length:8},(_,i)=>[i&1?hi[0]:lo[0],i&2?hi[1]:lo[1],i&4?hi[2]:lo[2]]);for(let i=0;i<8;i++)for(const bit of [1,2,4])if(!(i&bit))segment(corners[i],corners[i|bit]);const p=domain.fluid_point,d=Math.max(...hi.map((n:number,i:number)=>n-lo[i]))*.015;for(let i=0;i<3;i++)segment(p.map((n:number,j:number)=>n-(i===j?d:0)),p.map((n:number,j:number)=>n+(i===j?d:0)));}
  if(rotation?.enabled){const r=rotation,axis=unit(r.axis),basis=[0,0,0];basis[axis.map(Math.abs).indexOf(Math.min(...axis.map(Math.abs)))]=1;const u=unit(cross(axis,basis)),v=cross(axis,u);const at=(t:number,z:number)=>r.origin.map((n:number,i:number)=>n+axis[i]*z+r.radius*(u[i]*Math.cos(t)+v[i]*Math.sin(t)));for(let k=0;k<48;k++){const t=k*Math.PI/24,nt=(k+1)*Math.PI/24;for(const z of [-r.length/2,r.length/2])segment(at(t,z),at(nt,z));if(k%12===0)segment(at(t,-r.length/2),at(t,r.length/2));}const end=r.origin.map((n:number,i:number)=>n+axis[i]*r.length*.8);segment(r.origin,end);for(const sign of [-1,1])segment(end,end.map((n:number,i:number)=>n-axis[i]*r.length*.1+sign*u[i]*r.length*.04));const t=Math.sign(r.rpm)*Math.PI/2;const tip=at(t,0),tail=at(t-Math.sign(r.rpm)*.25,0);segment(tail,tip);segment(tip,at(t-Math.sign(r.rpm)*.15,r.radius*.08));}
  const poly=vtkPolyData.newInstance();poly.getPoints().setData(new Float32Array(points),3);poly.getLines().setData(new Uint32Array(lines));return poly;
}
