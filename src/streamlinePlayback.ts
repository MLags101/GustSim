// Playback interpolates existing streamline points; it never invents a flow field.
export function tracksFromPolyData(poly:any){
  const lines=poly.getLines().getData(),points=poly.getPoints().getData();
  const time=poly.getPointData().getArrayByName('IntegrationTime')?.getData();
  if(!time)return [];
  const tracks:any[]=[];
  for(let i=0;i<lines.length&&tracks.length<200;){
    const count=lines[i++],ids=Array.from(lines.slice(i,i+count)) as number[];i+=count;
    const samples=ids.map(id=>({t:Number(time[id]),p:[points[id*3],points[id*3+1],points[id*3+2]]})).filter(p=>Number.isFinite(p.t)).sort((a,b)=>a.t-b.t);
    if(samples.length>1&&samples.at(-1)!.t>samples[0].t)tracks.push(samples);
  }
  return tracks;
}
export function sampleTrack(track:any[],phase:number){
  const t=track[0].t+phase*(track.at(-1).t-track[0].t);
  let low=0,high=track.length-1;
  while(high-low>1){const mid=(low+high)>>1;if(track[mid].t<t)low=mid;else high=mid;}
  const a=track[low],b=track[high],f=(t-a.t)/(b.t-a.t||1);
  return a.p.map((n:number,i:number)=>n+f*(b.p[i]-n));
}
