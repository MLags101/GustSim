import vtkPolyData from '@kitware/vtk.js/Common/DataModel/PolyData';
const cross=(a:number[],b:number[])=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const unit=(v:number[])=>{const n=Math.hypot(...v);return n>1e-12?v.map(x=>x/n):[1,0,0];};

export function overlayGeometry(rotation:any,domain:any,flow?:any,bounds?:number[][]){
  const points:number[]=[],lines:number[]=[];
  const segment=(a:number[],b:number[])=>{const i=points.length/3;points.push(...a,...b);lines.push(2,i,i+1);};
  if(flow&&bounds&&flow.speed>0){
    const a=(flow.alpha_deg||0)*Math.PI/180,b=(flow.beta_deg||0)*Math.PI/180;
    const direction=unit(Array.isArray(flow.velocity)?flow.velocity:[Math.cos(a)*Math.cos(b),Math.sin(b),Math.sin(a)*Math.cos(b)]);
    const center=bounds[0].map((n,i)=>(n+bounds[1][i])/2),length=Math.max(...bounds[0].map((n,i)=>bounds[1][i]-n));
    const basis=Math.abs(direction[2])<.9?[0,0,1]:[0,1,0],side=unit(cross(direction,basis));
    for(const offset of [-.35,0,.35]){const start=center.map((n,i)=>n-direction[i]*length+side[i]*offset*length);const end=start.map((n,i)=>n+direction[i]*length*.65);segment(start,end);for(const sign of [-1,1])segment(end,end.map((n,i)=>n-direction[i]*length*.14+side[i]*length*.07*sign));}
  }
  if(domain){const lo=domain.minimum,hi=domain.maximum,corners=Array.from({length:8},(_,i)=>[i&1?hi[0]:lo[0],i&2?hi[1]:lo[1],i&4?hi[2]:lo[2]]);for(let i=0;i<8;i++)for(const bit of [1,2,4])if(!(i&bit))segment(corners[i],corners[i|bit]);const p=domain.fluid_point,d=Math.max(...hi.map((n:number,i:number)=>n-lo[i]))*.015;for(let i=0;i<3;i++)segment(p.map((n:number,j:number)=>n-(i===j?d:0)),p.map((n:number,j:number)=>n+(i===j?d:0)));}
  if(rotation?.enabled){
    const r=rotation;
    const origin=r.origin||[0,0,0];
    const axis=unit(r.axis||[1,0,0]);
    const rpm=r.rpm||0;
    const spinSign=rpm>=0?1:-1;
    const modelSpan=bounds?Math.max(...bounds[0].map((n,i)=>bounds[1][i]-n)):1.0;
    const radius=r.radius&&r.radius>0?r.radius:(r.blade_radius?r.blade_radius*1.15:modelSpan*0.4);
    const length=r.length&&r.length>0?r.length:radius*0.8;
    const bladeRadius=r.blade_radius&&r.blade_radius>0?r.blade_radius:radius*0.85;

    const basis=[0,0,0];
    basis[axis.map(Math.abs).indexOf(Math.min(...axis.map(Math.abs)))]=1;
    const u=unit(cross(axis,basis)),v=unit(cross(axis,u));
    const at=(t:number,z:number,rad:number)=>origin.map((n:number,i:number)=>n+axis[i]*z+rad*(u[i]*Math.cos(t)+v[i]*Math.sin(t)));

    // A. Origin anchor gizmo
    const gizmoSize=radius*0.16;
    for(const vec of [u,v,axis]){
      segment(origin.map((n:number,i:number)=>n-vec[i]*gizmoSize),origin.map((n:number,i:number)=>n+vec[i]*gizmoSize));
    }
    for(let k=0;k<4;k++){
      const t1=k*Math.PI/2,t2=(k+1)*Math.PI/2;
      segment(at(t1,0,gizmoSize*0.55),at(t2,0,gizmoSize*0.55));
    }

    // B. Rotation axis line with positive direction arrowhead
    const axisStart=origin.map((n:number,i:number)=>n-axis[i]*length*0.65);
    const axisEnd=origin.map((n:number,i:number)=>n+axis[i]*length*0.75);
    segment(axisStart,axisEnd);
    const arrowLen=Math.min(length*0.16,radius*0.22);
    const arrowW=arrowLen*0.38;
    for(const sign of [-1,1]){
      segment(axisEnd,axisEnd.map((n:number,i:number)=>n-axis[i]*arrowLen+u[i]*arrowW*sign));
      segment(axisEnd,axisEnd.map((n:number,i:number)=>n-axis[i]*arrowLen+v[i]*arrowW*sign));
    }
    // Negative stop bar
    segment(axisStart.map((n:number,i:number)=>n-u[i]*arrowW),axisStart.map((n:number,i:number)=>n+u[i]*arrowW));

    // C. Curved Spin Direction Arc Arrow (Right-Hand Rule based on signed RPM)
    const arcRad=radius*0.52;
    const arcAngle=Math.PI*1.3;
    const arcSteps=24;
    for(let k=0;k<arcSteps;k++){
      const t1=spinSign*(k*arcAngle/arcSteps);
      const t2=spinSign*((k+1)*arcAngle/arcSteps);
      segment(at(t1,0,arcRad),at(t2,0,arcRad));
    }
    const tipAngle=spinSign*arcAngle;
    const tipPoint=at(tipAngle,0,arcRad);
    const tangent=[-Math.sin(tipAngle)*u[0]+Math.cos(tipAngle)*v[0],-Math.sin(tipAngle)*u[1]+Math.cos(tipAngle)*v[1],-Math.sin(tipAngle)*u[2]+Math.cos(tipAngle)*v[2]].map(x=>x*spinSign);
    const radial=[Math.cos(tipAngle)*u[0]+Math.sin(tipAngle)*v[0],Math.cos(tipAngle)*u[1]+Math.sin(tipAngle)*v[1],Math.cos(tipAngle)*u[2]+Math.sin(tipAngle)*v[2]];
    const barbLen=arcRad*0.22;
    const barbW=barbLen*0.45;
    segment(tipPoint,tipPoint.map((n:number,i:number)=>n-tangent[i]*barbLen+radial[i]*barbW));
    segment(tipPoint,tipPoint.map((n:number,i:number)=>n-tangent[i]*barbLen-radial[i]*barbW));

    // D. Blade sweep disk (at z=0)
    if(bladeRadius>0&&bladeRadius<=radius){
      for(let k=0;k<36;k++){
        const t1=k*Math.PI/18,t2=(k+1)*Math.PI/18;
        segment(at(t1,0,bladeRadius),at(t2,0,bladeRadius));
      }
      for(let k=0;k<4;k++){
        const t=k*Math.PI/2;
        segment(origin,at(t,0,bladeRadius));
      }
    }

    // E. MRF Bounding Cylinder (Top, Bottom, Mid-plane circles & Cage Struts)
    const zLo=-length/2,zHi=length/2;
    for(let k=0;k<48;k++){
      const t1=k*Math.PI/24,t2=(k+1)*Math.PI/24;
      segment(at(t1,zLo,radius),at(t2,zLo,radius));
      segment(at(t1,zHi,radius),at(t2,zHi,radius));
      if(k%2===0) segment(at(t1,0,radius),at(t2,0,radius));
      if(k%6===0){
        segment(at(t1,zLo,radius),at(t1,zHi,radius));
        segment(at(t1,zLo,radius),origin.map((n:number,i:number)=>n+axis[i]*zLo));
        segment(at(t1,zHi,radius),origin.map((n:number,i:number)=>n+axis[i]*zHi));
      }
    }
  }
  const poly=vtkPolyData.newInstance();poly.getPoints().setData(new Float32Array(points),3);poly.getLines().setData(new Uint32Array(lines));return poly;
}

/** Wireframe oriented boxes (radiators, design boxes). */
export function orientedBoxes(boxes:{center:number[];axis:number[];up:number[];size:number[]}[]){
  const points:number[]=[],lines:number[]=[];
  const segment=(a:number[],b:number[])=>{const i=points.length/3;points.push(...a,...b);lines.push(2,i,i+1);};
  for(const box of boxes){
    const a=unit(box.axis),upRaw=box.up.map((v,i)=>v-(box.up[0]*a[0]+box.up[1]*a[1]+box.up[2]*a[2])*a[i]),u=unit(upRaw),s=cross(u,a);
    const half=[box.size[0]/2,box.size[1]/2,box.size[2]/2];
    const corner=(i:number)=>box.center.map((c,k)=>c+a[k]*half[0]*(i&1?1:-1)+s[k]*half[1]*(i&2?1:-1)+u[k]*half[2]*(i&4?1:-1));
    for(let i=0;i<8;i++)for(const bit of [1,2,4])if(!(i&bit))segment(corner(i),corner(i|bit));
  }
  const poly=vtkPolyData.newInstance();poly.getPoints().setData(new Float32Array(points),3);poly.getLines().setData(new Uint32Array(lines));return poly;
}

/** Grey ground plane under the model (setup preview; the case uses the domain's bottom face). */
export function groundPlane(bounds:number[][],up:number[]=[0,0,1],offset=0){
  const axis=unit(up),k=axis.map(Math.abs).indexOf(Math.max(...axis.map(Math.abs)));
  const lo=bounds[0],hi=bounds[1],span=Math.max(...lo.map((n,i)=>hi[i]-n));
  const level=(axis[k]>0?lo[k]:hi[k])-offset*Math.sign(axis[k]);
  const others=[0,1,2].filter(i=>i!==k);
  const corner=(a:number,b:number)=>{const p=[0,0,0];p[k]=level;p[others[0]]=a;p[others[1]]=b;return p;};
  const c0=(lo[others[0]]+hi[others[0]])/2,c1=(lo[others[1]]+hi[others[1]])/2,h=span*1.6;
  const pts=[corner(c0-h,c1-h),corner(c0+h,c1-h),corner(c0+h,c1+h),corner(c0-h,c1+h)];
  const poly=vtkPolyData.newInstance();poly.getPoints().setData(new Float32Array(pts.flat()),3);poly.getPolys().setData(new Uint32Array([4,0,1,2,3]));return poly;
}

/** Dimension lines along the model's bounding box and where to place their labels. */
export function dimensionLines(bounds:number[][]){
  const points:number[]=[],lines:number[]=[],labels:{axis:number;point:number[];length:number}[]=[];
  const lo=bounds[0],hi=bounds[1],span=Math.max(...lo.map((n,i)=>hi[i]-n)),gap=span*.08,tick=span*.03;
  const segment=(a:number[],b:number[])=>{const i=points.length/3;points.push(...a,...b);lines.push(2,i,i+1);};
  for(let axis=0;axis<3;axis++){
    // Offset each dimension line outside the box along the next axis.
    const side=(axis+1)%3,start=lo.slice(),end=lo.slice();start[side]-=gap;end[side]-=gap;end[axis]=hi[axis];
    if(axis===2){start[0]-=gap;end[0]-=gap;start[side]+=gap;end[side]+=gap;}
    segment(start,end);
    for(const p of [start,end]){const a=p.slice(),b=p.slice();a[side]-=tick;b[side]+=tick;segment(a,b);}
    labels.push({axis,point:start.map((n,i)=>(n+end[i])/2),length:hi[axis]-lo[axis]});
  }
  const poly=vtkPolyData.newInstance();poly.getPoints().setData(new Float32Array(points),3);poly.getLines().setData(new Uint32Array(lines));
  return {poly,labels};
}
