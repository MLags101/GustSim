import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from . import config, db, geometry, validation
from .models import Prepare, Primitive, SimulationSpec, Sweep, ViewSpec, PresetRequest, RotorSuggestion
from . import presets, workflow
from . import projects


@asynccontextmanager
async def lifespan(app):
    db.initialize()
    yield

app = FastAPI(title="GustSim",version="0.1.0",lifespan=lifespan)

@app.get('/api/projects')
def list_projects():return projects.listing()

@app.post('/api/projects',status_code=201)
def new_project(body:dict):return projects.save(db.uid(),body)

@app.get('/api/projects/{identifier}')
def read_project(identifier:str):return projects.read(identifier)

@app.put('/api/projects/{identifier}')
def save_project(identifier:str,body:dict):return projects.save(identifier,body)

@app.middleware("http")
async def local_origin(request: Request, call_next):
    origin=request.headers.get("origin")
    if request.method not in ("GET","HEAD","OPTIONS") and origin:
        from urllib.parse import urlparse
        if urlparse(origin).hostname not in {"localhost","127.0.0.1"}:
            return JSONResponse({"detail":"Only local browser origins are accepted"},status_code=403)
    return await call_next(request)

@app.exception_handler(KeyError)
async def missing(request,exc):
    return JSONResponse({"detail":str(exc)},status_code=404)

@app.exception_handler(ValueError)
async def invalid(request,exc):
    return JSONResponse({"detail":str(exc)},status_code=422)

@app.exception_handler(RequestValidationError)
async def invalid_draft(request,exc):
    if request.url.path != '/api/validate':
        return await request_validation_exception_handler(request,exc)
    controls={'rotation':'rotating-region','domain':'domain-fluid-region','boundaries':'boundary-conditions','mesh':'mesh-resolution','flow':'incident-flow','references':'reference-quantities','use_case':'guided-setup'}
    findings=[]
    for i,error in enumerate(exc.errors()):
        location=[str(v) for v in error['loc'] if v!='body']
        findings.append({'code':f'input_{i}','stage':'setup','control':controls.get(location[0] if location else '', 'simulation-template'),
                         'status':'fail','detail':('.'.join(location)+': ' if location else '')+error['msg']})
    return JSONResponse({'valid':False,'errors':[f['detail'] for f in findings],'warnings':[], 'findings':findings,
                         'configuration_id':None,'geometry_ready':False,'validation_status':'experimental_validation_pending'})

@app.get("/api/health")
def health():
    import importlib.util
    return {"version":"0.1.0","openfoam_version":config.FOAM_VERSION,"worker":db.worker_status(),
            "cad_available":bool(importlib.util.find_spec("OCP")),"validation_status":"experimental_validation_pending"}

@app.get("/api/geometry")
def list_geometry():
    return db.geometries()

@app.post("/api/geometry/import",status_code=201)
def import_geometry(file:UploadFile=File(...),units:str=Form("mm")):
    if units not in geometry.UNITS:
        raise ValueError("Unsupported source units")
    name=Path(file.filename or "model.stl").name
    content=file.file.read(config.MAX_UPLOAD+1)
    if len(content)>config.MAX_UPLOAD:
        raise HTTPException(413,"File exceeds 100 MB")
    suffix=Path(name).suffix.lower()
    if suffix==".stl":return geometry.import_stl(name,content,units)
    if suffix in (".stp",".step"):return geometry.import_step(name,content)
    raise ValueError("Only STEP and STL files are supported")

@app.post("/api/geometry/primitive",status_code=201)
def primitive(p:Primitive, parent_id:str|None=None):
    return geometry.add_primitive(p,parent_id)

@app.get("/api/geometry/{identifier}")
def get_geometry(identifier:str):return db.geometry(identifier)

@app.get("/api/geometry/{identifier}/scene")
def get_scene(identifier:str):
    db.geometry(identifier)
    return geometry.scene(identifier)

@app.get("/api/geometry/{identifier}/defaults")
def get_defaults(identifier:str):return validation.defaults(identifier)

@app.post("/api/geometry/{identifier}/prepare",status_code=201)
def prepare(identifier:str,request:Prepare):return geometry.prepare(identifier,request)

@app.post("/api/geometry/{identifier}/regroup",status_code=201)
def regroup(identifier:str,angle:float=35):
    if not 0<angle<180:raise ValueError("Angle must be between 0 and 180 degrees")
    return geometry.regroup(identifier,angle)

@app.post("/api/validate")
def validate(spec:SimulationSpec):return validation.validate(spec)

@app.post('/api/presets/preview')
def preset_preview(request:PresetRequest): return presets.generate(request)

@app.post('/api/geometry/{identifier}/rotor-suggestion')
def rotor_suggestion(identifier:str, request:RotorSuggestion): return presets.rotor_suggestion(identifier,request)

@app.get("/api/runs")
def runs():return db.runs(compact=True)

@app.post("/api/runs",status_code=202)
def launch(spec:SimulationSpec,kind:str="solve",mesh_run_id:str|None=None,guided:bool=False,acknowledged:str|None=None):
    if kind not in ("mesh","solve","case"):raise ValueError("Unknown job type")
    report=validation.validate(spec)
    if not report["valid"]:raise ValueError("; ".join(report["errors"]))
    if guided and any(f['status']=='review' for f in report['findings']) and acknowledged != report['configuration_id']:
        raise ValueError('Review and acknowledge the setup warnings for this configuration')
    if guided and kind=='solve' and not mesh_run_id:
        raise ValueError('Generate and review a compatible mesh before solving')
    if kind!="case" and not db.worker_status()["online"]:
        raise HTTPException(503,"Compute worker is offline. Start the Linux worker before queuing a mesh or solve.")
    if mesh_run_id:
        source=db.run(mesh_run_id)
        if kind!='solve': raise ValueError('Mesh reuse is only available for a solve')
        workflow.require_mesh(source,spec)
        if guided and source['result'].get('reviewed_configuration_id') != report['configuration_id']:
            raise ValueError('Review this mesh before solving')
    job=db.enqueue(spec.model_dump(),kind,parent_id=mesh_run_id,initial_status='generating' if kind=='case' else 'queued')
    if kind=="case":
        from . import foam
        try:
            foam.compile_case(spec,config.DATA/'runs'/job['id'])
            db.update(job['id'],status='completed',stage='completed',result={'case_available':True,'fields_available':False})
        except Exception as e:
            db.update(job['id'],status='failed',error=str(e))
            raise
        return db.run(job['id'])
    return job

@app.post('/api/runs/{identifier}/review')
def review_mesh(identifier:str, configuration_id:str, acknowledge_warnings:bool=False):
    source=db.run(identifier)
    workflow.require_mesh(source,source['spec'])
    if configuration_id != workflow.identity(source['spec']): raise ValueError('Mesh review is outdated')
    findings=source['result'].get('mesh_summary',{}).get('findings',[])
    required={'mesh_quality','mesh_boundaries','mesh_preview','mesh_rotation','mesh_extended'}
    if not required <= {f['code'] for f in findings} or any(f['status'] in ('fail','unavailable') for f in findings) or not source['result'].get('mesh_available') or not (config.DATA/'runs'/identifier/'mesh-preview.vtp').is_file():
        raise ValueError('Required mesh evidence or preview is unavailable')
    if any(f['status']=='review' for f in findings) and not acknowledge_warnings:
        raise ValueError('Acknowledge the extended mesh findings before proceeding')
    db.update(identifier,result={**source['result'],'reviewed_configuration_id':configuration_id})
    return db.run(identifier)

@app.get("/api/runs/{identifier}")
def get_run(identifier:str):
    record=db.run(identifier)
    root=config.DATA/'runs'/identifier
    from .progress import describe
    record['progress']=describe(record,root)
    record['result']['export_available']={kind:(root/path).is_file() for kind,path in {
        'case':'simulation.json','bundle':'simulation.json','vtk':'results/volume.vtu',
        'hdf5':'results/fields.npz','png':'results/view.png','paraview':'results/view.pvsm'}.items()}
    record['result']['export_available'].update(csv=bool(record['result'].get('history')),report=bool(record['result']))
    for name in ('history','rotor_history','residuals','projected_history','rotor_load_history'):
        values=record['result'].get(name,[])
        if len(values)>6000:
            stride=max(1,len(values)//5000)
            record['result'][name]=values[::stride]+([values[-1]] if values[-1]!=values[::stride][-1] else [])
    return record

@app.post("/api/runs/{identifier}/cancel")
def cancel(identifier:str):
    run=db.run(identifier)
    if run["status"] not in ("queued","running"):raise ValueError("Only queued or running jobs can be cancelled")
    db.update(identifier,cancel=1,**({"status":"cancelled","stage":"cancelled"} if run["status"]=="queued" else {}))
    return db.run(identifier)

@app.post("/api/runs/{identifier}/retry",status_code=202)
def retry(identifier:str,guided:bool=False):
    r=db.run(identifier)
    if r["status"] in ("queued","running"):raise ValueError("Wait for the current attempt to stop")
    return db.enqueue(r["spec"],'mesh' if guided and r['kind']=='solve' else r["kind"],r["study_id"],identifier)

@app.get("/api/runs/{identifier}/events")
async def events(identifier:str,request:Request,after:int=0):
    db.run(identifier)
    try: cursor=max(after,int(request.headers.get("last-event-id","0")))
    except ValueError:cursor=after
    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            for event in db.events(identifier,cursor):
                cursor=event["id"]
                yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
            yield ': heartbeat\n\n'
            if db.run(identifier)["status"] not in ("queued","running"):
                yield 'event: done\ndata: {}\n\n'
                return
            await asyncio.sleep(1)
    return StreamingResponse(stream(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.post("/api/studies",status_code=201)
def study(sweep:Sweep,guided:bool=False):
    specs=[]
    for value in sweep.values:
        data=sweep.spec.model_dump()
        target={"speed":"flow","alpha_deg":"flow","rpm":"rotation","surface_level":"mesh"}[sweep.parameter]
        data[target][sweep.parameter]=value
        data["name"]=f"{sweep.spec.name} · {sweep.parameter}={value}"
        item=SimulationSpec.model_validate(data)
        report=validation.validate(item)
        if not report["valid"]:raise ValueError("; ".join(report["errors"]))
        if guided and any(f['status']=='review' for f in report['findings']):
            raise ValueError('This sweep includes actionable warnings; configure and acknowledge each variant individually')
        specs.append(item.model_dump())
    if not db.worker_status()["online"]:raise HTTPException(503,"Start the compute worker before creating a sweep")
    identifier=db.create_study(sweep.spec.name,sweep.model_dump())
    return {"id":identifier,"runs":[db.enqueue(s,'mesh' if guided else "solve",identifier) for s in specs]}

@app.get("/api/studies")
def studies():
    with db.connection() as c:
        return [dict(r) for r in c.execute("SELECT id,created,name FROM studies ORDER BY created DESC")]

@app.post('/api/setups',status_code=201)
def save_setup(spec:SimulationSpec):
    identifier=db.uid()
    with db.connection() as c:
        c.execute('INSERT INTO setups VALUES(?,?,?,?)',(identifier,time.time(),spec.name,spec.model_dump_json()))
    return {'id':identifier}

@app.get('/api/setups')
def setups():
    with db.connection() as c:
        return [{'id':r['id'],'name':r['name'],'spec':json.loads(r['spec'])} for r in c.execute('SELECT * FROM setups ORDER BY created DESC')]

@app.api_route("/api/runs/{identifier}/files/{relative:path}",methods=['GET','HEAD'])
def artifact(identifier:str,relative:str):
    db.run(identifier)
    root=(config.DATA/"runs"/identifier).resolve()
    path=(root/relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise HTTPException(404,"Artifact not found")
    return FileResponse(path)

@app.post("/api/runs/{identifier}/views",status_code=202)
def create_view(identifier:str,view:ViewSpec):
    r=db.run(identifier)
    if not r["result"].get("fields_available"):raise ValueError("This run has no solved fields")
    if not db.worker_status()["online"]:raise HTTPException(503,"Start the worker to extract this view")
    view_id=db.uid()
    with db.connection() as c:
        c.execute("INSERT INTO views VALUES(?,?,?,?)",(view_id,identifier,time.time(),json.dumps(view.model_dump())))
    job=db.enqueue({"run_id":identifier,"view_id":view_id,"view":view.model_dump()},"view",parent_id=identifier)
    return {"id":view_id,"job":job}

@app.get("/api/runs/{identifier}/views")
def views(identifier:str):
    db.run(identifier)
    with db.connection() as c:
        items=[{"id":r[0],"spec":json.loads(r[1])} for r in c.execute("SELECT id,spec FROM views WHERE run_id=? ORDER BY created DESC",(identifier,))]
        jobs=[db.decode(r) for r in c.execute("SELECT * FROM runs WHERE kind='view' AND parent_id=? ORDER BY created DESC",(identifier,))]
        for item in items:
            job=next((j for j in jobs if j['spec'].get('view_id')==item['id']),None)
            item.update(status=job['status'] if job else 'unavailable',error=job['error'] if job else None,automatic=bool(job and job['spec'].get('automatic')))
        return items

@app.get('/api/runs/{identifier}/views/{view_id}/samples')
def samples(identifier:str,view_id:str):
    import csv
    import math
    db.run(identifier)
    if len(view_id)!=32 or any(c not in '0123456789abcdef' for c in view_id):raise ValueError('Invalid view identifier')
    path=config.DATA/'runs'/identifier/'views'/view_id/'samples.csv'
    if not path.is_file():raise HTTPException(404,'Samples are not available yet')
    with path.open() as file:
        reader=csv.DictReader(file);rows=[]
        for i,row in enumerate(reader):
            if i>=2000:break
            cleaned={}
            for key,value in row.items():
                try:
                    number=float(value);cleaned[key]=number if math.isfinite(number) else None
                except (TypeError,ValueError):cleaned[key]=value
            rows.append(cleaned)
    return {'columns':reader.fieldnames,'rows':rows,'association':'interpolated points'}

@app.get("/api/runs/{identifier}/export/{kind}")
def export(identifier:str,kind:str):
    from .exports import export_run
    path=export_run(db.run(identifier),kind)
    return FileResponse(path,filename=path.name)


@app.post('/api/mesh/recommend')
def recommend_mesh(spec:SimulationSpec):
    from .mesh_guidance import recommend
    return recommend(spec,db.geometry(spec.geometry_id))

if Path("dist").is_dir():
    app.mount("/",StaticFiles(directory="dist",html=True),name="web")
