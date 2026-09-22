/** In-page tools for a browser agent attached to the open workspace.
 *  A coding agent on the machine uses `gustsim mcp` instead; both follow the same gates.
 */

const empty = {type:'object', properties:{}, additionalProperties:false};
const acknowledge = {type:'object', properties:{acknowledge_warnings:{type:'boolean'}}, additionalProperties:false};

function evidence(run:any){
  const result=run?.result||{};
  return {
    id:run?.id??null,
    status:run?.status??null,
    stage:run?.stage??null,
    error:run?.error??null,
    execution:run?.status??null,
    termination:result.termination??null,
    numerical_quality:result.numerical_status??null,
    experimental_validation:result.validation_status||'experimental_validation_pending',
    mesh_check:result.mesh_check??null,
    mesh_reviewed:!!result.reviewed_configuration_id,
    fields_available:!!result.fields_available,
  };
}

export function browserTools(ctx:{
  geometry:any; spec:any; report:any; mesh:any; run:any;
  post:(path:string, body:unknown)=>Promise<any>;
  applySpec:(spec:any)=>void;
  setReport:(report:any)=>void;
  setAck:(ack:string)=>void;
  setStep:(step:number)=>void;
  launch:(kind:string, acknowledged?:string)=>Promise<any>;
  reviewMesh:()=>Promise<any>;
}){
  return [
    {name:'read_gustsim_workspace', title:'Read CFD workspace', description:'Read the open model, typed setup, layer fit, mesh, and run. experimental_validation stays pending. This does not start a job.', inputSchema:empty, annotations:{readOnlyHint:true}, execute:async()=>({
      geometry:ctx.geometry?{id:ctx.geometry.id, filename:ctx.geometry.filename, triangles:ctx.geometry.triangles, diagnostics:ctx.geometry.diagnostics, geometry_fidelity:ctx.geometry.geometry_fidelity||'imported', patches:(ctx.geometry.patches||[]).map((p:any)=>({name:p.name, triangles:p.triangles}))}:null,
      spec:ctx.spec??null,
      layer_plan:ctx.report?.layer_plan??null,
      valid:ctx.report?.valid??null,
      findings:ctx.report?.findings??[],
      mesh:evidence(ctx.mesh),
      run:evidence(ctx.run),
    })},
    {name:'validate_gustsim_setup', title:'Validate simulation setup', description:'Run preflight on the open setup. Does not queue a mesh or solve. Review findings are not failures; they must be read before a guided run.', inputSchema:empty, annotations:{readOnlyHint:true}, execute:async()=>{
      if(!ctx.spec)throw new Error('Import and configure a model first');
      const report=await ctx.post('/validate', ctx.spec);
      ctx.setReport(report);
      return {valid:report.valid, errors:report.errors, findings:report.findings, layer_plan:report.layer_plan, configuration_id:report.configuration_id, geometry_ready:report.geometry_ready, validation_status:report.validation_status};
    }},
    {name:'configure_gustsim_preset', title:'Configure a guided setup', description:'Build a pipe, external-object, or rotor setup for the open geometry and show it in the workspace. Does not mesh or solve, and leaves the setup unconfirmed. kind is pipe, aerodynamics, or propeller. For a pipe, pass inlet, outlet, and driving (flow, speed, or pressure). For a rotor, pass patches, origin, axis, and rpm.', inputSchema:{type:'object', properties:{kind:{type:'string', enum:['pipe','aerodynamics','propeller']}, fluid:{type:'string', enum:['air','water']}, turbulence:{type:'string', enum:['kOmegaSST','laminar']}, speed:{type:'number'}, direction:{type:'array', items:{type:'number'}}, lift_axis:{type:'array', items:{type:'number'}}, inlet:{type:'string'}, outlet:{type:'string'}, driving:{type:'string', enum:['flow','speed','pressure']}, flow_rate:{type:'number'}, inlet_pressure_pa:{type:'number'}, outlet_pressure_pa:{type:'number'}, patches:{type:'array', items:{type:'string'}}, origin:{type:'array', items:{type:'number'}}, axis:{type:'array', items:{type:'number'}}, rpm:{type:'number'}}, required:['kind'], additionalProperties:false}, execute:async(input:any={})=>{
      if(!ctx.geometry)throw new Error('Import geometry first');
      const preview=await ctx.post('/presets/preview', {...input, geometry_id:ctx.geometry.id});
      ctx.applySpec(preview.spec);
      ctx.setReport(preview.report);
      ctx.setAck('');
      ctx.setStep(1);
      return {confirmed:false, review:preview.review, valid:preview.report?.valid, findings:preview.report?.findings, layer_plan:preview.report?.layer_plan, spec:preview.spec};
    }},
    {name:'confirm_gustsim_setup', title:'Confirm the guided setup', description:'Set the open guided setup to confirmed after its dimensions, fluid point, and directions have been read. Does not start the solver.', inputSchema:empty, execute:async()=>{
      if(!ctx.spec?.use_case)throw new Error('Apply a pipe, object, or rotor preset first');
      const next={...ctx.spec, use_case:{...ctx.spec.use_case, confirmed:true}};
      ctx.applySpec(next);
      const report=await ctx.post('/validate', next);
      ctx.setReport(report);
      return {valid:report.valid, errors:report.errors, findings:report.findings, layer_plan:report.layer_plan, configuration_id:report.configuration_id};
    }},
    {name:'queue_gustsim_mesh', title:'Queue mesh generation', description:'Queue a new mesh attempt for the open setup. Review findings must be read first; pass acknowledge_warnings true to record that. The worker must be online. This does not solve.', inputSchema:acknowledge, execute:async(input:any={})=>{
      if(!ctx.spec)throw new Error('Configure a setup first');
      const report=await ctx.post('/validate', ctx.spec);
      ctx.setReport(report);
      if(!report.valid)return {queued:false, valid:false, errors:report.errors, findings:report.findings};
      const reviews=(report.findings||[]).filter((item:any)=>item.status==='review');
      if(reviews.length&&!input.acknowledge_warnings)return {queued:false, needs_acknowledgement:true, findings:reviews, layer_plan:report.layer_plan};
      if(input.acknowledge_warnings)ctx.setAck(report.configuration_id);
      const run=await ctx.launch('mesh', input.acknowledge_warnings?report.configuration_id:undefined);
      if(run?.error)return {queued:false, error:run.error};
      return {queued:!!run?.id, run:evidence(run)};
    }},
    {name:'review_gustsim_mesh', title:'Review the current mesh', description:'Record review of the open mesh so a solve can use it. If findings need attention, read them and pass acknowledge_warnings true. This does not modify the mesh or start the solver.', inputSchema:acknowledge, execute:async(input:any={})=>{
      const findings=ctx.mesh?.result?.mesh_summary?.findings||[];
      const reviews=findings.filter((item:any)=>item.status==='review');
      if(reviews.length&&!input.acknowledge_warnings)return {reviewed:false, needs_acknowledgement:true, findings};
      const result=await ctx.reviewMesh();
      if(result?.error)return {reviewed:false, error:result.error};
      return {reviewed:!!result?.id, run:evidence(result)};
    }},
    {name:'queue_gustsim_solve', title:'Queue the solve', description:'Queue a new solve on the reviewed mesh. A geometry or physics change blocks it. Pass acknowledge_warnings true only after reading setup findings. Completion is not experimental validation.', inputSchema:acknowledge, execute:async(input:any={})=>{
      if(!ctx.spec)throw new Error('Configure a setup first');
      const report=await ctx.post('/validate', ctx.spec);
      ctx.setReport(report);
      if(!report.valid)return {queued:false, valid:false, errors:report.errors, findings:report.findings};
      const reviews=(report.findings||[]).filter((item:any)=>item.status==='review');
      if(reviews.length&&!input.acknowledge_warnings)return {queued:false, needs_acknowledgement:true, findings:reviews, layer_plan:report.layer_plan};
      if(input.acknowledge_warnings)ctx.setAck(report.configuration_id);
      const run=await ctx.launch('solve', input.acknowledge_warnings?report.configuration_id:undefined);
      if(run?.error)return {queued:false, error:run.error};
      return {queued:!!run?.id, run:evidence(run), experimental_validation:'pending'};
    }},
    {name:'read_gustsim_run', title:'Read the open run', description:'Read execution, numerical quality, and experimental validation for the open mesh or solve. ok is not a field here: status completed still has experimental validation pending. Histories are omitted.', inputSchema:{type:'object', properties:{which:{type:'string', enum:['solve','mesh']}}, additionalProperties:false}, annotations:{readOnlyHint:true}, execute:async(input:any={})=>{
      const run=input.which==='mesh'?ctx.mesh:ctx.run;
      if(!run)return {available:false};
      return {available:true, ...evidence(run), metrics:run.result?.metrics??null, findings:run.result?.findings||run.result?.mesh_summary?.findings||[]};
    }},
  ];
}
