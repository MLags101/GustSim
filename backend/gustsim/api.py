import asyncio
import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from . import config, db, geometry, migrations, validation
from .models import Batch, Prepare, Primitive, SimulationSpec, Sweep, ViewSpec, PresetRequest, RotorSuggestion
from . import presets, workflow
from . import projects


VERSION="0.2.0"

@asynccontextmanager
async def lifespan(app):
    # The API owns schema migration (with a verified backup) and its own inline case
    # compilations; worker lanes recover their own jobs when they start.
    db.initialize()
    db.recover(statuses=('generating',))
    yield

app = FastAPI(title="GustSim",version=VERSION,lifespan=lifespan)

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
    worker=db.worker_status()
    return {"version":VERSION,"openfoam_version":config.FOAM_VERSION,"worker":worker,
            "cad_available":bool(importlib.util.find_spec("OCP")),"async_import":worker['lanes']['cad']['online'],
            "schema_version":migrations.schema_version(),
            "validation_status":"experimental_validation_pending"}

@app.get("/api/capabilities")
def capabilities():
    from . import capabilities as registry
    lanes=db.lanes_status()
    return {'physics':registry.report(lanes['compute']['capabilities']),
            'lanes':{k:{'online':v['online']} for k,v in lanes.items()},
            'features':{'async_import':lanes['cad']['online'],'formats':sorted({s.lstrip('.') for s in geometry.IMPORT_SUFFIXES}),
                        'pdf_report':False,'share_bundle':False,'estimate':False}}

@app.get("/api/geometry")
def list_geometry():
    return db.geometries()

def read_upload(file,units):
    if units not in geometry.UNITS:
        raise ValueError("Unsupported source units")
    name=Path(file.filename or "model.stl").name
    if Path(name).suffix.lower() not in geometry.IMPORT_SUFFIXES:
        raise ValueError("Supported formats: STEP, IGES, STL, OBJ and glTF/GLB")
    content=file.file.read(config.MAX_UPLOAD+1)
    if len(content)>config.MAX_UPLOAD:
        raise HTTPException(413,f"File exceeds {config.MAX_UPLOAD//1024//1024} MB")
    return name,content

@app.post("/api/geometry/import",status_code=201)
def import_geometry(file:UploadFile=File(...),units:str=Form("mm")):
    """Synchronous import (agents, scripts, small files). The browser uses import-jobs."""
    name,content=read_upload(file,units)
    return geometry.import_file(name,content,units)

@app.post("/api/geometry/import-jobs",status_code=202)
def import_geometry_job(file:UploadFile=File(...),units:str=Form("mm"),deflection:str=Form("auto")):
    """Durable import on the CAD lane; progress arrives on /api/runs/{id}/events."""
    if deflection!='auto':
        try:
            if not 0.01<=float(deflection)<=10:raise ValueError
        except ValueError:raise ValueError('Tessellation deflection must be auto or 0.01-10 mm')
    if not db.lane_online('cad'):raise HTTPException(503,'The CAD worker is offline; use the synchronous import or start the cad service')
    name,content=read_upload(file,units)
    job_id=db.uid()
    from .cadworker import upload_dir
    folder=upload_dir(job_id);folder.mkdir(parents=True,exist_ok=True)
    stored='source'+Path(name).suffix.lower()
    (folder/stored).write_bytes(content)
    return db.enqueue({'filename':name,'units':units,'deflection':deflection,'stored_name':stored,'upload_job':job_id},'import',identifier=job_id)

@app.post("/api/geometry/{identifier}/prepare-jobs",status_code=202)
def prepare_job(identifier:str,request:Prepare):
    db.geometry(identifier)
    if not db.lane_online('cad'):raise HTTPException(503,'The CAD worker is offline; use the synchronous preparation')
    return db.enqueue({'geometry_id':identifier,'request':request.model_dump(mode='json')},'prepare')

@app.post('/api/geometry/{identifier}/wheels/detect')
def detect_wheels(identifier:str,body:dict|None=None):
    """Suggest wheels: round, flat parts with a sideways axis touching the ground."""
    from .wheels import detect
    body=body or {}
    return detect(identifier,body.get('forward',(1,0,0)),body.get('up',(0,0,1)))

@app.post('/api/porous/fit')
def fit_porous(body:dict):
    """Darcy–Forchheimer coefficients for a two-point radiator curve (for display)."""
    from .foam_elements import porous_fit
    try:
        curve=tuple(tuple(float(x) for x in point) for point in body['curve'])
        return porous_fit(curve,float(body.get('dynamic_viscosity',1.825e-5)),float(body.get('density',1.204)),float(body['thickness_m']))
    except (KeyError,TypeError) as e:raise ValueError('Give curve [[v1, dp1], [v2, dp2]] and thickness_m') from e

@app.get('/api/samples')
def list_samples():
    from . import samples
    return samples.listing()

@app.post('/api/samples/{identifier}',status_code=201)
def create_sample(identifier:str):
    from . import samples
    return samples.create(identifier)

@app.get("/api/jobs/{identifier}")
def get_job(identifier:str):
    record=db.run(identifier)
    from .progress import describe
    record['progress']=describe(record,config.DATA/'runs'/identifier)
    return record

@app.get("/api/geometry/{identifier}/export/{fmt}")
def export_geometry(identifier:str,fmt:str):
    path=geometry.export_revision(identifier,fmt)
    return FileResponse(path,filename=f"{Path(db.geometry(identifier)['filename']).stem}-{identifier[:8]}.{fmt}")

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
def validate(spec:SimulationSpec):
    # An accuracy tier is resolved into concrete mesh and solver numbers before checking,
    # so the report (and its configuration id) describe exactly what would run.
    from . import mesh_tiers
    resolved=mesh_tiers.apply(spec)
    report=validation.validate(resolved)
    report['resolved_spec']=resolved.model_dump(mode='json')
    return report

@app.post('/api/presets/preview')
def preset_preview(request:PresetRequest): return presets.generate(request)

@app.post('/api/geometry/{identifier}/rotor-suggestion')
def rotor_suggestion(identifier:str, request:RotorSuggestion): return presets.rotor_suggestion(identifier,request)

@app.get("/api/runs")
def runs(kinds:str|None=None):
    """Simulation attempts by default; `kinds=all` or a comma list includes job kinds such
    as view extraction and CAD import."""
    selected=None if kinds=='all' else tuple(k for k in (kinds or ','.join(db.SIMULATION_KINDS+('view',))).split(',') if k)
    return db.runs(compact=True,kinds=selected)

@app.post("/api/runs",status_code=202)
def launch(spec:SimulationSpec,kind:str="solve",mesh_run_id:str|None=None,guided:bool=False,acknowledged:str|None=None):
    if kind not in ("mesh","solve","case"):raise ValueError("Unknown job type")
    from . import mesh_tiers
    spec=mesh_tiers.apply(spec)
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
        # v2 configurations reuse a reviewed mesh across physics-only changes (speed, RPM,
        # fluid), so the review is matched on the mesh identity rather than the full one.
        reviewed=(source['result'].get('reviewed_mesh_identity')==workflow.mesh_identity(spec) if spec.schema_version>=2
                  else source['result'].get('reviewed_configuration_id')==report['configuration_id'])
        if guided and not reviewed:
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
    db.update(identifier,result={**source['result'],'reviewed_configuration_id':configuration_id,
                                 'reviewed_mesh_identity':workflow.mesh_identity(source['spec'])})
    return db.run(identifier)

@app.get("/api/runs/{identifier}")
def get_run(identifier:str):
    record=db.run(identifier)
    root=config.DATA/'runs'/identifier
    from .progress import describe
    record['progress']=describe(record,root)
    record['result']['export_available']={kind:(root/path).is_file() for kind,path in {
        'case':'simulation.json','bundle':'simulation.json','vtk':'results/volume.vtu',
        'hdf5':'results/fields.npz','png':'results/view.png','paraview':'results/view.pvsm','pdf':'results/report.pdf',
        'package':'results/package/manifest.json'}.items()}
    # `report` needs actual run evidence; `export_available` itself was just inserted above, so
    # testing the whole result dict would always be truthy even for a queued run.
    from .share import available as share_built
    record['result']['export_available'].update(csv=bool(record['result'].get('history')),
        report=any(record['result'].get(key) for key in ('findings','metrics','mesh_summary')),
        share=share_built() and record['status']=='completed' and ((root/'results/package/manifest.json').is_file() or (root/'results/surface.vtp').is_file()))
    for name in ('history','rotor_history','residuals','projected_history','rotor_load_history'):
        values=record['result'].get(name,[])
        if len(values)>6000:
            stride=max(1,len(values)//5000)
            record['result'][name]=values[::stride]+([values[-1]] if values[-1]!=values[::stride][-1] else [])
    return record

@app.post("/api/runs/{identifier}/cancel")
def cancel(identifier:str):
    run=db.run(identifier)
    if run["status"] not in ("queued","running","generating"):raise ValueError("Only queued or running jobs can be cancelled")
    # Nothing polls the cancel flag during inline case compilation, so a stranded
    # "generating" run is marked cancelled directly, like a queued one.
    db.update(identifier,cancel=1,**({"status":"cancelled","stage":"cancelled"} if run["status"] in ("queued","generating") else {}))
    return db.run(identifier)

@app.post("/api/runs/{identifier}/retry",status_code=202)
def retry(identifier:str,guided:bool=False):
    r=db.run(identifier)
    if r["status"] in ("queued","running"):raise ValueError("Wait for the current attempt to stop")
    if r["kind"]=="view":
        # A view job's parent is the solved run it extracts from; pointing it at the previous
        # job id would hide the retry from GET /runs/{id}/views forever.
        if not db.lane_online('post'):raise HTTPException(503,"Start the worker to extract this view")
        return db.enqueue(r["spec"],"view",r["study_id"],r["spec"]["run_id"])
    if r["kind"] in ("package","report"):
        # Post-lane documents belong to the solved run, not to the failed job.
        return db.enqueue(r["spec"],r["kind"],parent_id=r["parent_id"],retry_of=identifier,attempt=(r.get('attempt') or 1)+1,
                          simulation_id=r.get('simulation_id'))
    if r["kind"] in ("import","prepare"):
        if not db.lane_online('cad'):raise HTTPException(503,"Start the CAD worker to retry this job")
        return db.enqueue({**r["spec"],'upload_job':r["spec"].get('upload_job',identifier)} if r["kind"]=="import" else r["spec"],
                          r["kind"],parent_id=identifier,retry_of=identifier,attempt=(r.get('attempt') or 1)+1)
    if r["kind"]!="case" and not db.worker_status()["online"]:
        raise HTTPException(503,"Compute worker is offline. Start the Linux worker before queuing a mesh or solve.")
    return db.enqueue(r["spec"],'mesh' if guided and r['kind']=='solve' else r["kind"],r["study_id"],identifier,retry_of=identifier,attempt=(r.get('attempt') or 1)+1,simulation_id=r.get('simulation_id'))

@app.get("/api/runs/{identifier}/events")
async def events(identifier:str,request:Request,after:int=0):
    db.run(identifier)
    try: cursor=max(after,int(request.headers.get("last-event-id","0")))
    except ValueError:cursor=after
    from starlette.concurrency import run_in_threadpool
    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            # SQLite calls are blocking and each opens its own connection. Run them off
            # the event loop: one lock stall here would otherwise freeze the whole API
            # for every client, and each open stream polls twice a second.
            events=await run_in_threadpool(db.events,identifier,cursor)
            for event in events:
                cursor=event["id"]
                yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
            yield ': heartbeat\n\n'
            status=(await run_in_threadpool(db.run,identifier))["status"]
            if status not in ("queued","running"):
                # Drain anything written between the event read and the status read,
                # otherwise the final error or completion message is lost to the client.
                for event in await run_in_threadpool(db.events,identifier,cursor):
                    cursor=event["id"]
                    yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
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

@app.head("/api/runs/{identifier}/files/{relative:path}",include_in_schema=False)
@app.get("/api/runs/{identifier}/files/{relative:path}")
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
    if not db.lane_online("post"):raise HTTPException(503,"Start the worker to extract this view")
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
        # Read fieldnames inside the block: for an empty file the property re-reads the handle.
        columns=reader.fieldnames or []
    return {'columns':columns,'rows':rows,'association':'interpolated points'}

@app.get("/api/runs/{identifier}/export/share")
def export_share(identifier:str):
    """A single self-contained HTML file with this run's simulation results: opens offline, read-only."""
    from . import share
    from fastapi.responses import Response
    if not share.available():raise HTTPException(503,'The share viewer was not built into this installation (vite build -c vite.share.config.ts)')
    record=db.run(identifier)
    with db.connection() as c:
        row=c.execute("SELECT id FROM simulations WHERE json_extract(summary,'$.results_run_id')=? OR json_extract(plan,'$.legacy_run')=? ORDER BY updated DESC LIMIT 1",(identifier,identifier)).fetchone()
    simulation_id=record.get('simulation_id') or (row[0] if row else None)
    if simulation_id is None:raise ValueError('Only simulation results can be shared; open this run from the simulations dashboard')
    simulation=get_simulation(simulation_id)
    if simulation.get('results_run_id')!=identifier:raise ValueError('Share the simulation’s current results run')
    documents={f'/simulations/{simulation_id}':simulation,f'/runs/{identifier}':get_run(identifier)}
    for name,relative in RESULT_DOCUMENTS.items():
        path=config.DATA/'runs'/identifier/relative
        if path.is_file():documents[f'/runs/{identifier}/results/{name}']=json.loads(path.read_text(encoding='utf-8'))
    try:documents[f'/runs/{identifier}/monitor']=run_monitor(identifier)
    except Exception:pass
    html=share.build(simulation_id,identifier,config.DATA/'runs'/identifier,documents,simulation['name'])
    filename=re.sub(r'[^A-Za-z0-9._-]+','-',simulation['name']).strip('-')[:60] or 'gustsim'
    return Response(html,media_type='text/html; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{filename}-shared-report.html"'})


@app.get("/api/runs/{identifier}/export/{kind}")
def export(identifier:str,kind:str):
    from .exports import export_run
    path=export_run(db.run(identifier),kind)
    return FileResponse(path,filename=path.name)


@app.post('/api/simulations',status_code=202)
def create_simulation(body:dict):
    """Validate a setup and start its pipeline: mesh, review, solve and results package.

    Body: {spec, plan?, name?, tags?, acknowledged?}. Review findings (including experimental
    physics) must be acknowledged for this exact configuration id, as for guided launches."""
    from . import mesh_tiers, pipeline
    spec=mesh_tiers.apply(SimulationSpec.model_validate(body.get('spec') or {}))
    report=validation.validate(spec)
    if not report['valid']:raise ValueError('; '.join(report['errors']))
    if any(f['status']=='review' for f in report['findings']) and body.get('acknowledged')!=report['configuration_id']:
        raise ValueError('Review and acknowledge the setup findings for this configuration')
    tags=body.get('tags') or []
    if not isinstance(tags,list) or not all(isinstance(t,str) for t in tags):raise ValueError('Tags must be a list of strings')
    created=pipeline.create(spec,plan={k:v for k,v in (body.get('plan') or {}).items() if k in ('auto_review',)},
                            project_id=body.get('project_id') or spec.project_id,name=body.get('name'),tags=tags)
    return pipeline.describe(created)

@app.get('/api/simulations')
def list_simulations(project_id:str|None=None,tag:str|None=None,status:str|None=None,q:str|None=None,archived:bool=False):
    from . import pipeline
    return pipeline.listing(project_id,tag,status,q,archived)

@app.get('/api/simulations/{identifier}')
def get_simulation(identifier:str):
    from . import pipeline
    return pipeline.describe(pipeline.refresh(identifier))

@app.patch('/api/simulations/{identifier}')
def patch_simulation(identifier:str,body:dict):
    from . import pipeline
    unknown=set(body)-{'name','tags','archived'}
    if unknown:raise ValueError('Only name, tags and archived can be changed; a configuration change is a new simulation')
    return pipeline.describe(pipeline.update(identifier,body.get('name'),body.get('tags'),body.get('archived')))

@app.post('/api/simulations/{identifier}/continue')
def continue_simulation(identifier:str,acknowledge_warnings:bool=False):
    from . import pipeline
    return pipeline.describe(pipeline.continue_after_review(identifier,acknowledge_warnings))

@app.post('/api/simulations/{identifier}/cancel')
def cancel_simulation(identifier:str):
    from . import pipeline
    return pipeline.describe(pipeline.cancel(identifier))

RESULT_DOCUMENTS={'manifest':'results/package/manifest.json','forces':'results/forces.json','insights':'results/insights.json',
                  'elements':'results/elements.json','convergence':'results/convergence.json',
                  'transient':'results/transient.json','design':'results/design.json',
                  'freesurface':'results/freesurface.json'}

@app.get('/api/runs/{identifier}/results/{document}')
def result_document(identifier:str,document:str):
    db.run(identifier)
    if document not in RESULT_DOCUMENTS:raise HTTPException(404,'Unknown results document')
    path=config.DATA/'runs'/identifier/RESULT_DOCUMENTS[document]
    if not path.is_file():raise HTTPException(404,f'The {document} document is not available for this run yet')
    return JSONResponse(json.loads(path.read_text(encoding='utf-8')))

@app.post('/api/runs/{identifier}/package',status_code=202)
def queue_package(identifier:str):
    run=db.run(identifier)
    if run['kind']!='solve' or run['status']!='completed':raise ValueError('Only a completed solve has a results package')
    from .jobs.package import enqueue,job_id
    existing=next((r for r in db.runs(kinds=('package',),limit=500) if r['parent_id']==identifier and r['status'] in ('queued','running')),None)
    if existing:return existing
    attempt=1+sum(1 for r in db.runs(kinds=('package',),limit=500) if r['parent_id']==identifier)
    if attempt==1:return enqueue(identifier)
    return db.enqueue({'run_id':identifier},'package',parent_id=identifier,identifier=job_id(identifier,attempt),attempt=attempt)

@app.post('/api/runs/{identifier}/report',status_code=202)
def queue_report(identifier:str):
    run=db.run(identifier)
    if run['kind']!='solve' or run['status']!='completed':raise ValueError('Only a completed solve has a report')
    from .jobs.report import enqueue
    previous=[r for r in db.runs(kinds=('report',),limit=500) if r['parent_id']==identifier]
    active=next((r for r in previous if r['status'] in ('queued','running')),None)
    return active or enqueue(identifier,attempt=len(previous)+1)

@app.get('/api/compare')
def compare_runs(runs:str,baseline:str|None=None,columns:str|None=None):
    from .compare import compare
    return compare([r for r in runs.split(',') if r],baseline,[c for c in columns.split(',') if c] if columns else None)

@app.post('/api/compare/surface')
def compare_surface(body:dict):
    from .compare import surface_delta
    field=body.get('field','Cp')
    if field not in ('Cp','pressure_pa','Cf','wall_shear_mag','yPlus'):raise ValueError('Unsupported comparison field')
    return surface_delta(str(body.get('a','')),str(body.get('b','')),field)

@app.get('/api/compare/files/{key}/delta.vtp')
def compare_file(key:str):
    if len(key)!=24 or any(c not in '0123456789abcdef' for c in key):raise HTTPException(404,'Not found')
    path=config.DATA/'compare'/key/'delta.vtp'
    if not path.is_file():raise HTTPException(404,'Not found')
    return FileResponse(path)

@app.post('/api/batches',status_code=202)
def create_batch(batch:Batch,project_id:str|None=None):
    from . import batches
    return batches.create(batch,project_id)

@app.get('/api/batches')
def list_batches():
    from . import batches
    return batches.listing()

@app.get('/api/batches/{identifier}')
def get_batch(identifier:str):
    from . import batches
    return batches.detail(identifier)

@app.get('/api/mesh/tiers')
def mesh_tier_list():
    from . import mesh_tiers
    return mesh_tiers.describe()

@app.post('/api/mesh/estimate')
def mesh_estimate(spec:SimulationSpec):
    from .estimate import estimate
    return estimate(spec)

@app.get('/api/runs/{identifier}/monitor')
def run_monitor(identifier:str,after_iteration:float=-1):
    """Residual and load histories read from the solver's own tables (authoritative backfill
    for live charts; the SSE stream carries the same row shapes incrementally)."""
    record=db.run(identifier)
    root=config.DATA/'runs'/identifier
    if record['kind'] not in ('solve','mesh','case'):raise ValueError('Only simulation attempts have solver histories')
    from .monitor import history
    forces,residuals=history(root,SimulationSpec.model_validate(record['spec']),after_iteration)
    path=root/'results/convergence.json'
    convergence=json.loads(path.read_text()) if path.is_file() else record['result'].get('convergence')
    return {'forces':forces,'residuals':residuals,'convergence':convergence}

@app.post('/api/mesh/recommend')
def recommend_mesh(spec:SimulationSpec):
    from .mesh_guidance import recommend
    return recommend(spec,db.geometry(spec.geometry_id))

if Path("dist").is_dir():
    app.mount("/",StaticFiles(directory="dist",html=True),name="web")
