"""Lane worker processes with durable attempts, cancellation and resource limits.

Each lane (compute: OpenFOAM, post: ParaView extraction and reports) runs in its own process
with its own lock and heartbeat, so a view extraction never waits behind a solve.
"""
import contextlib
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import psutil
from . import config, db, foam, quality, workflow
from .models import SimulationSpec

class Cancelled(Exception):pass

# Result fields recorded when an attempt is queued that later result writes must keep.
CARRIED_RESULT=('changes',)

# Lane of this process; heartbeats from executor and keepalive threads report against it.
CURRENT_LANE='compute'

# Every program a job may start. Arguments are typed values or paths inside GUSTSIM_DATA;
# nothing is executed through a shell.
ALLOWED_PROGRAMS={'blockMesh','surfaceFeatureExtract','snappyHexMesh','checkMesh','decomposePar','reconstructPar',
    'reconstructParMesh','renumberMesh','potentialFoam','simpleFoam','pimpleFoam','interFoam','adjointOptimisationFoam',
    'topoSet','setFields','mapFields','foamToVTK','createBaffles','mergeOrSplitBaffles','pvbatch','ffmpeg','mpirun'}

def program_allowed(command):
    if not command:return False
    first=command[0]
    # The worker's own interpreter runs GustSim helper modules.
    if first==sys.executable:return True
    name=Path(first).name
    if name not in ALLOWED_PROGRAMS:return False
    if name=='mpirun':
        rest=list(command[1:])
        while rest and rest[0].startswith('-'):
            rest=rest[2:] if rest[0] in ('-np','-n') else rest[1:]
        return bool(rest) and Path(rest[0]).name in ALLOWED_PROGRAMS-{'mpirun'}
    return True

def lock_name(lane):
    # The compute lane keeps the historical name so an old single worker cannot run beside it.
    return 'worker.lock' if lane=='compute' else f'worker-{lane}.lock'

@contextlib.contextmanager
def worker_lock(lane='compute'):
    config.initialize()
    with (config.DATA/lock_name(lane)).open('a+b') as handle:
        handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as e:raise RuntimeError(f'Another GustSim {lane} worker already owns this data directory') from e
        try:yield
        finally:
            if os.name=='nt':
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)

class LocalExecutor:
    def __init__(self,memory_gb=None):
        self.memory_gb=memory_gb
        self.peak_rss=0
    def run(self,identifier,command,cwd,stage,timeout,monitor=None):
        """Run one program to completion. `monitor(elapsed)` is polled with the heartbeat;
        returning 'stop' creates <case>/gustsim.stop, which the case's abort function object
        turns into a clean write-and-exit, so the process is never killed mid-write."""
        if not program_allowed(command):raise RuntimeError(f'Program not permitted for worker jobs: {command[0] if command else ""}')
        if db.run(identifier)['cancel']:raise Cancelled()
        db.update(identifier,stage=stage)
        db.event(identifier,{'stage':stage,'message':' '.join(command)})
        (cwd/'logs').mkdir(exist_ok=True)
        env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'}
        process=subprocess.Popen(command,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
            text=True,encoding='utf-8',errors='replace',env=env,start_new_session=os.name!='nt')
        lines=queue.Queue()
        def reader():
            for line in process.stdout:lines.put(line)
        thread=threading.Thread(target=reader,daemon=True);thread.start()
        start=time.monotonic();last_heartbeat=0;buffer=[];last_flush=0;stop_requested=False
        ceiling=self.memory_gb or config.MEMORY_GB
        try:
            with (cwd/'logs'/f'{stage}.log').open('w',encoding='utf-8') as log:
                while process.poll() is None or thread.is_alive() or not lines.empty():
                    now=time.monotonic()
                    try:
                        line=lines.get(timeout=0.1);log.write(line);log.flush();buffer.append(line.rstrip())
                    except queue.Empty:pass
                    if now-last_flush>1 and buffer:
                        db.event(identifier,{'stage':stage,'message':'\n'.join(buffer[-60:])});buffer=[];last_flush=now
                    if now-last_heartbeat>2:
                        db.heartbeat(identifier,lane=CURRENT_LANE);last_heartbeat=now
                        if db.run(identifier)['cancel']:raise Cancelled()
                        if now-start>timeout:raise RuntimeError(f'{stage} exceeded the configured time limit')
                        try:
                            parent=psutil.Process(process.pid)
                            rss=sum(p.memory_info().rss for p in [parent,*parent.children(recursive=True)] if p.is_running())
                            self.peak_rss=max(self.peak_rss,rss)
                            if rss>ceiling*1024**3:raise RuntimeError(f'{stage} exceeded the {ceiling:g} GB process-tree memory ceiling')
                        except psutil.NoSuchProcess:pass
                        if monitor and not stop_requested and process.poll() is None:
                            if monitor(now-start)=='stop':
                                (cwd/'gustsim.stop').write_text('stop requested by GustSim convergence monitor\n',encoding='utf-8')
                                stop_requested=True
                                db.event(identifier,{'stage':stage,'message':'Convergence criterion met; asking the solver to write and stop'})
                if buffer:db.event(identifier,{'stage':stage,'message':'\n'.join(buffer[-60:])})
            if process.wait()!=0:raise RuntimeError(f'{stage} failed with exit code {process.returncode}; inspect logs/{stage}.log')
        finally:
            if process.poll() is None:
                if os.name!='nt':
                    with contextlib.suppress(ProcessLookupError):os.killpg(process.pid,signal.SIGTERM)
                else:
                    with contextlib.suppress(psutil.NoSuchProcess):
                        for child in psutil.Process(process.pid).children(recursive=True):child.kill()
                    process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name!='nt':
                        with contextlib.suppress(ProcessLookupError):os.killpg(process.pid,signal.SIGKILL)
                    else:process.kill()
                    process.wait()
            if process.stdout:process.stdout.close()

@contextlib.contextmanager
def keepalive(identifier, interval=3):
    """Keep the worker heartbeat fresh during long pure-Python phases.

    `LocalExecutor.run` beats while a subprocess is running, but case compilation, mesh
    preview, quality analysis and copying a polyMesh do not go through it. On a large
    assembly those take minutes, during which the worker is declared offline after 15 s
    and the API starts refusing new jobs with a 503 while it is in fact busy.
    """
    stop = threading.Event()
    def beat():
        while not stop.wait(interval):
            with contextlib.suppress(Exception):
                db.heartbeat(identifier,lane=CURRENT_LANE)
    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=interval)


def runtime_check(required=('blockMesh','surfaceFeatureExtract','snappyHexMesh','checkMesh','simpleFoam','decomposePar','reconstructPar','pvbatch')):
    missing=[name for name in required if not shutil.which(name)]
    if missing:raise RuntimeError('CFD runtime unavailable: '+', '.join(missing)+'. Run the Linux worker container.')
    if required==('pvbatch',):return
    if os.environ.get('WM_PROJECT_VERSION','').lstrip('v')!=config.FOAM_VERSION:
        raise RuntimeError('Worker requires the OpenCFD v2606 environment; source its etc/bashrc')

def extract_view(job,executor,root):
    runtime_check(('pvbatch',))
    identifier=job['id']
    parent=config.DATA/'runs'/job['spec']['run_id']
    target=parent/'views'/job['spec']['view_id'];target.mkdir(parents=True,exist_ok=True)
    request=target/'request.json';request.write_text(json.dumps(job['spec']['view']))
    executor.run(identifier,['pvbatch','--force-offscreen-rendering',str(Path(__file__).with_name('postprocess.py')),str(parent),str(target),str(request)],root,'extracting',600)
    db.update(identifier,status='completed',stage='completed',result={'view_id':job['spec']['view_id'],'run_id':job['spec']['run_id']})

def process_job(job,executor=None):
    executor=executor or LocalExecutor();identifier=job['id']
    root=config.DATA/'runs'/identifier;root.mkdir(parents=True,exist_ok=True)
    spec=None
    try:
        handler=JOB_HANDLERS.get(job['kind'])
        if handler is not None:
            handler(job,executor,root)
            return
        if job['kind'] not in ('case','mesh','solve'):raise RuntimeError(f"Unknown job kind {job['kind']}")
        spec=SimulationSpec.model_validate(job['spec'])
        # A retry attempt is queued with result.changes (the settings it altered). Every later
        # result write replaces the whole result, so carry those fields forward.
        carry={k:v for k,v in (job.get('result') or {}).items() if k in CARRIED_RESULT}
        def save(**fields):
            if 'result' in fields:fields['result']={**carry,**fields['result']}
            db.update(identifier,**fields)
        db.update(identifier,stage='preparing')
        with keepalive(identifier): foam.compile_case(spec,root)
        if job['kind']=='case':
            save(status='completed',stage='completed',result={'case_available':True,'fields_available':False})
            return
        runtime_check()
        # One budget for the attempt. Passing the same limit to every stage let a
        # "120 minute" run occupy many hours.
        budget=spec.solver.timeout_minutes*60
        started=time.monotonic()
        def timeout():
            left=budget-(time.monotonic()-started)
            if left<=0: raise RuntimeError('Attempt exceeded the configured time limit of '
                                           f'{spec.solver.timeout_minutes} minutes')
            return left
        reused=False
        if job.get('parent_id') and job['kind']=='solve':
            parent=db.run(job['parent_id'])
            if parent['kind']=='mesh' and parent['status']=='completed':
                workflow.require_mesh(parent,spec)
                source=config.DATA/'runs'/parent['id']
                with keepalive(identifier): shutil.copytree(source/'constant/polyMesh',root/'constant/polyMesh')
                reused=True
                db.event(identifier,{'stage':'mesh_reuse','message':'Using the reviewed mesh from '+parent['id']})
        v2=spec.schema_version>=2
        mesh_started=time.monotonic()
        mesh_method='reused' if reused else build_mesh(identifier,executor,root,spec,timeout)
        commands=[(['checkMesh','-meshQuality'],'check'),(['checkMesh','-allTopology','-allGeometry'],'mesh_diagnostics')]
        for command,stage in commands:
            try: executor.run(identifier,command,root,stage,timeout())
            except RuntimeError:
                # Extended diagnostics are advisory, but only tolerate the failure when the
                # log actually shows diagnostic findings. Reading it must never raise from
                # inside the handler and mask the original error.
                tolerated=False
                if stage=='mesh_diagnostics':
                    log=root/'logs/mesh_diagnostics.log'
                    try: tolerated='Failed ' in log.read_text(errors='replace')
                    except OSError: tolerated=False
                if not tolerated: raise
                db.event(identifier,{'stage':stage,'message':'Extended diagnostics require review; explicit quality gate is evaluated separately'})
        mesh_log=(root/'logs/check.log').read_text(errors='replace')
        # Verify the mesh retained the same patch contract as the source geometry.
        import re
        boundary=(root/'constant/polyMesh/boundary').read_text(errors='replace')
        actual=set(re.findall(r'^\s*([A-Za-z][A-Za-z0-9_]*)\s*\n\s*\{',boundary,re.M))-{'FoamFile'}
        expected={b.patch for b in spec.boundaries}
        empty=re.findall(r'([A-Za-z][A-Za-z0-9_]*)\s*\{[^}]*\bnFaces\s+0\s*;',boundary,re.S)
        boundary_ok=actual==expected and not empty
        from .mesh_preview import create as mesh_preview
        preview_ok=False
        try:
            meta=json.loads((root/'geometry.json').read_text())
            with keepalive(identifier):
                mesh_preview(root,[(a+b)/2 for a,b in zip(*meta['bounds'])])
            preview_ok=True
        except Exception as e:
            db.event(identifier,{'stage':'mesh_preview','message':str(e),'level':'warning'})
        cell_match=re.search(r'\bcells:\s+(\d+)',mesh_log)
        # Every rotating and porous zone must exist with cells, or the physics silently vanishes.
        from .foam_elements import zone_list
        zones=zone_list(spec)
        rotation_ok=True
        empty_zones=[]
        if (root/'system'/'topoSetDict').is_file():
            executor.run(identifier,['topoSet'],root,'rotation',timeout())
        if zones:
            zone_text=(root/'constant/polyMesh/cellZones').read_text(errors='replace')
            empty_zones=[z for z in zones if not re.search(r'\b'+re.escape(z)+r'\s*\{[^}]*cellLabels\s+(?:List<label>\s+)?[1-9]\d*\s*\(',zone_text,re.S)]
            rotation_ok=not empty_zones
        summary=workflow.mesh_evidence(root,spec,boundary_ok,preview_ok,rotation_ok)
        if empty_zones:summary['empty_zones']=empty_zones
        summary['method']=mesh_method
        if not boundary_ok:
            missing=sorted(expected-actual)
            summary['boundary_details']={'missing':missing,'empty':empty,'unexpected':sorted(actual-expected)}
            for finding in summary['findings']:
                if finding['code']=='mesh_boundaries':
                    finding['detail']=f'{len(missing)} surfaces disappeared and {len(empty)} have no faces. Increase local body refinement; thin geometry may be smaller than the cells. For tiny CAD face patches, prepare one surface per component and reassign conditions.'
        mesh_pass=workflow.quality_limits(mesh_log)[0] and boundary_ok and rotation_ok
        save(result={'case_available':True,'mesh_available':preview_ok,'fields_available':False,'mesh_check':'passed' if mesh_pass else 'failed','cell_count':int(cell_match.group(1)) if cell_match else None,'mesh_summary':summary})
        if not mesh_pass:raise RuntimeError('Mesh quality, boundary, or rotor-zone check failed; solving was blocked. Inspect the mesh summary and logs.')
        if not reused and cell_match:
            from .estimate import record_mesh
            record_mesh(spec,int(cell_match.group(1)),time.monotonic()-mesh_started)
        if job['kind']=='mesh':
            executor.run(identifier,['foamToVTK','-constant','-ascii'],root,'mesh_export',timeout())
            db.update(identifier,status='completed',stage='completed')
            return
        solve_started=time.monotonic()
        notes=solve(identifier,executor,root,spec,timeout)
        with keepalive(identifier): result=quality.analyze(root,spec)
        result['findings'].extend(notes)
        if v2 and cell_match:
            from .estimate import record_solve
            iterations=max([h['iteration'] for h in result.get('history',[])] or [0])
            record_solve(spec,int(cell_match.group(1)),iterations,time.monotonic()-solve_started)
        if spec.mesh.adaptive_source is not None:
            from .amr import coefficient_change
            previous=db.run(spec.mesh.adaptive_source.run_id)['result'].get('metrics') or {}
            change=coefficient_change(previous.get('cd'), (result.get('metrics') or {}).get('cd'))
            if change is not None:
                result['amr_delta_cd']=change
                result['findings'].append({'code':'adaptive_mesh','status':'info','detail':f'Adaptive cycle changed Cd by {change*100:.2f}% relative to the previous mesh. This is not a mesh-study acceptance.'})
        result.update(case_available=True,mesh_available=preview_ok,fields_available=False,mesh_summary=summary,mesh_check='passed')
        save(result=result)
        # Execution and field extraction are distinct states: the solve has already
        # converged and its measurements are recorded, so a ParaView failure must not
        # retract them. It becomes an extraction finding on a completed run.
        db.heartbeat(identifier)
        try:
            executor.run(identifier,['pvbatch','--force-offscreen-rendering',str(Path(__file__).with_name('postprocess.py')),str(root),str(root/'results')],root,'postprocessing',timeout())
            field_summary=json.loads((root/'results/field_summary.json').read_text())
            result.update(fields_available=True,field_summary=field_summary)
            minimum=field_summary.get('min_pressure_pa')
            if spec.fluid.name=='water' and minimum is not None and minimum+spec.fluid.reference_pressure_pa<spec.fluid.vapor_pressure_pa:
                result['findings'].append({'code':'cavitation_risk','status':'fail','detail':'Estimated absolute pressure drops below vapor pressure; single-phase solution is outside its valid envelope'})
                result['numerical_status']='review_required'
        except Cancelled:
            raise
        except Exception as e:
            result.update(fields_available=False,extraction_error=str(e))
            result['findings'].append({'code':'field_extraction','status':'unavailable',
                'detail':'Solved fields were written but could not be extracted for viewing: '+str(e)+
                         '. The solve, its measurements and the raw case are preserved.'})
            db.event(identifier,{'stage':'postprocessing','level':'warning',
                'message':'Field extraction failed; the solve is preserved: '+str(e)})
        (root/'results').mkdir(parents=True,exist_ok=True)
        (root/'results/quality.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        save(status='completed',stage='completed',result=result)
        if v2:
            # The results package (layers, renders, forces, insights) runs on the post lane so
            # the compute lane is free for the next solve.
            try:
                from .jobs.package import enqueue as enqueue_package
                enqueue_package(identifier)
            except Exception as e:
                db.event(identifier,{'stage':'package','level':'warning','message':'Solve preserved; results package scheduling failed: '+str(e)})
        if spec.use_case:
            try:
                from .analysis import queue_default_views
                queue_default_views(identifier,spec)
            except Exception as e:
                db.event(identifier,{'stage':'automatic_views','level':'warning','message':'Solve preserved; automatic view scheduling failed: '+str(e)})
    except Cancelled:
        db.update(identifier,status='cancelled',stage='cancelled',error='Cancelled by user; completed artifacts preserved')
    except Exception as e:
        if spec is not None and job['kind'] in ('mesh','solve') and not db.run(identifier)['result'].get('mesh_summary'):
            with contextlib.suppress(Exception):
                evidence=workflow.mesh_evidence(root,spec,False,False,False)
                db.update(identifier,result={**db.run(identifier)['result'],'mesh_summary':evidence,'mesh_check':'failed','mesh_available':False})
        db.update(identifier,status='failed',error=str(e))
        db.event(identifier,{'message':str(e),'level':'error'})
        if spec is not None:
            with contextlib.suppress(Exception):schedule_retry(job,spec,e,root)
    finally:
        db.event(identifier,{'stage':db.run(identifier)['status'],'message':'Attempt '+db.run(identifier)['status']})
        if job.get('simulation_id') and job['kind'] in ('mesh','solve'):
            try:
                from .pipeline import advance
                advance(job['simulation_id'])
            except Exception as e:
                db.event(identifier,{'stage':'pipeline','level':'warning','message':'Simulation stage update failed: '+str(e)})

def remove_processor_dirs(root):
    for folder in root.glob('processor*'):
        if folder.is_dir():shutil.rmtree(folder,ignore_errors=True)

def build_mesh(identifier,executor,root,spec,timeout):
    """Background mesh, features and snappyHexMesh. v2 meshes in parallel when the attempt has
    more than one process, falling back to serial within the same attempt; the parallel log is
    kept as logs/meshing_parallel.log. Returns the method used."""
    executor.run(identifier,['blockMesh'],root,'background',timeout())
    executor.run(identifier,['surfaceFeatureExtract'],root,'features',timeout())
    method='serial'
    if spec.schema_version>=2 and spec.mesh.parallel and spec.solver.processes>1:
        try:
            executor.run(identifier,['decomposePar','-force'],root,'decompose_mesh',timeout())
            executor.run(identifier,['mpirun','-np',str(spec.solver.processes),'snappyHexMesh','-parallel','-overwrite'],root,'meshing',timeout())
            executor.run(identifier,['reconstructParMesh','-constant','-mergeTol','1e-6'],root,'reconstruct_mesh',timeout())
            method='parallel'
        except Cancelled:
            raise
        except RuntimeError as e:
            log=root/'logs/meshing.log'
            if log.exists():log.replace(root/'logs/meshing_parallel.log')
            db.event(identifier,{'stage':'meshing','level':'warning','message':'Parallel meshing failed ('+str(e)+'); meshing serially in this attempt'})
            remove_processor_dirs(root)
            executor.run(identifier,['blockMesh'],root,'background',timeout())
            method='serial_fallback'
        finally:
            remove_processor_dirs(root)
    if method!='parallel':
        executor.run(identifier,['snappyHexMesh','-overwrite'],root,'meshing',timeout())
    if spec.schema_version>=2:
        # Bandwidth reduction speeds every later solver iteration; cell zones are kept.
        executor.run(identifier,['renumberMesh','-overwrite'],root,'renumber',timeout())
    return method

def solve(identifier,executor,root,spec,timeout):
    """Decompose, initialise, solve (optionally in two numerics stages) and reconstruct.
    Returns informational findings about what was actually done."""
    notes=[]
    v2=spec.schema_version>=2
    parallel=spec.solver.processes>1
    sliding=v2 and spec.time.sliding_mesh
    freesurface=v2 and spec.free_surface is not None
    from .monitor import SolveMonitor
    monitor=SolveMonitor(identifier,root,spec) if v2 and not freesurface and not sliding else None
    def solver(stage):
        command=['mpirun','-np',str(spec.solver.processes),'simpleFoam','-parallel'] if parallel else ['simpleFoam']
        executor.run(identifier,command,root,stage,timeout(),monitor=monitor)
    if sliding:
        executor.run(identifier,['createBaffles','-overwrite'],root,'baffles',timeout())
        # The baffle sides still share their edge points; split them so the rotor can slide.
        executor.run(identifier,['mergeOrSplitBaffles','-split','-overwrite'],root,'split_baffles',timeout())
        notes.append({'code':'sliding_mesh','status':'info','detail':'Rotor interface split into a cyclicAMI pair. This is experimental; the transient propeller gate has not been run.'})
    if spec.mesh.adaptive_source is not None:
        origin=config.DATA/'runs'/spec.mesh.adaptive_source.run_id
        try:
            # Same domain and patch names on both meshes: consistent mapping needs no mapFieldsDict.
            executor.run(identifier,['mapFields','-consistent','-sourceTime','latestTime',str(origin)],root,'map_fields',timeout())
            notes.append({'code':'map_fields','status':'info','detail':'Velocity and pressure mapped from the previous adaptive cycle'})
        except Cancelled:
            raise
        except RuntimeError as e:
            notes.append({'code':'map_fields','status':'info','detail':'mapFields failed; the refined mesh starts from the uniform field: '+str(e)})
    if freesurface:
        executor.run(identifier,['setFields'],root,'set_fields',timeout())
        notes.append({'code':'free_surface','status':'info','detail':f"Water set below z = {spec.free_surface.waterline_z:.4g} m. Trim and sinkage are fixed. Experimental."})
    if parallel:executor.run(identifier,['decomposePar','-force'],root,'decomposing',timeout())
    if freesurface:
        command=['mpirun','-np',str(spec.solver.processes),'interFoam','-parallel'] if parallel else ['interFoam']
        executor.run(identifier,command,root,'free_surface',timeout())
    elif sliding:
        command=['mpirun','-np',str(spec.solver.processes),'pimpleFoam','-parallel'] if parallel else ['pimpleFoam']
        executor.run(identifier,command,root,'sliding',timeout())
    elif v2 and spec.solver.initialise=='potential':
        original=root/'0.orig'
        if not parallel and not original.exists():shutil.copytree(root/'0',original)
        try:
            command=['mpirun','-np',str(spec.solver.processes),'potentialFoam','-parallel','-initialiseUBCs'] if parallel else ['potentialFoam','-initialiseUBCs']
            executor.run(identifier,command,root,'initialise',timeout())
            notes.append({'code':'initialisation','status':'info','detail':'Velocity initialised from a potential-flow solution'})
        except Cancelled:
            raise
        except RuntimeError as e:
            db.event(identifier,{'stage':'initialise','level':'warning','message':'potentialFoam failed; continuing from the uniform field: '+str(e)})
            if parallel:executor.run(identifier,['decomposePar','-force'],root,'decomposing',timeout())
            elif original.exists():
                shutil.rmtree(root/'0',ignore_errors=True);shutil.copytree(original,root/'0')
            notes.append({'code':'initialisation','status':'info','detail':'Uniform initial field (potentialFoam failed; see logs/initialise.log)'})
    if not freesurface and not sliding:
        end=foam.end_time(spec)
        if v2 and spec.solver.numerics=='robust':
            first=min(200,end)
            foam.write_schemes(spec,root,robust_start=True);foam.restart_control(spec,root,first)
            # The first-order stage only starts the flow: a convergence (or runaway) verdict on
            # it must not skip the second-order solve, so stop requests are held until it ends
            # and detection restarts on the second-order iterations.
            if monitor:monitor.hold=True
            try:solver('solve_startup')
            finally:
                if monitor:monitor.hold=False
            if monitor:monitor.restart_detection()
            foam.write_schemes(spec,root);foam.restart_control(spec,root,end)
            notes.append({'code':'numerics_profile','status':'info','detail':f'Robust startup: first-order momentum for {first} iterations, then second order'})
            solver('solve')
        else:solver('solve')
    def latest_time():
        folder=root/'processor0' if parallel else root
        times=[float(p.name) for p in folder.iterdir() if p.is_dir() and p.name.replace('.','',1).isdigit()]
        return max(times) if times else None
    if monitor:monitor.finish(latest_time())
    if v2 and spec.time.mode=='transient' and not sliding and not freesurface:
        # Time-accurate continuation from the steady solution on the same mesh and case.
        from . import transient
        meta=json.loads((root/'geometry.json').read_text())
        center=[(a+b)/2 for a,b in zip(*meta['bounds'])]
        times=transient.prepare(spec,root,latest_time() or 0,center)
        command=['mpirun','-np',str(spec.solver.processes),'pimpleFoam','-parallel'] if parallel else ['pimpleFoam']
        executor.run(identifier,command,root,'transient',timeout())
        notes.append({'code':'transient','status':'info','detail':
            f"Transient pimpleFoam from the steady solution: {times['duration_s']:.4g} s "
            f"({times['flow_throughs']:.3g} domain flow-throughs), averaged from {times['average_start']-times['start']:.4g} s, "
            f"maximum Courant number {times['max_courant']:g}"})
    if parallel:executor.run(identifier,['reconstructPar','-latestTime'],root,'reconstructing',timeout())
    if v2 and spec.design.enabled and spec.time.mode!='transient':
        _design_advice(identifier,executor,root,spec,timeout,notes)
    return notes

def _design_advice(identifier,executor,root,spec,timeout,notes):
    """Run the adjoint on the finished steady case. A failure is a finding; the steady solution stays."""
    from . import adjoint
    primal=adjoint.latest_time_name(root)
    if primal is None:
        notes.append({'code':'design_advice','status':'unavailable','detail':'Design advice found no solved time directory. The steady solution is unchanged.'})
        return
    parallel=spec.solver.processes>1
    try:
        adjoint.write_sensitivity(spec,root)
        if parallel:
            executor.run(identifier,['decomposePar','-force'],root,'adjoint_decompose',timeout())
            command=['mpirun','-np',str(spec.solver.processes),'adjointOptimisationFoam','-parallel']
        else:
            command=['adjointOptimisationFoam']
        executor.run(identifier,command,root,'adjoint',timeout())
        if parallel:
            executor.run(identifier,['reconstructPar','-latestTime'],root,'adjoint_reconstruct',timeout())
        fields=adjoint.park_sensitivity(root,primal)
        (root/'results').mkdir(parents=True,exist_ok=True)
        (root/'results'/'adjoint_run.json').write_text(json.dumps({
            'fields':fields,'objective':spec.design.objective,'turbulence':adjoint.turbulence_treatment(spec)}),encoding='utf-8')
        if not fields:
            notes.append({'code':'design_advice','status':'unavailable','detail':'Adjoint finished but wrote no sensitivity field. The steady solution is unchanged.'})
        else:
            notes.append({'code':'design_advice','status':'info','detail':
                f"Adjoint design advice ({adjoint.turbulence_treatment(spec)}), objective {spec.design.objective}. "
                f"Sensitivity on time {primal}: {', '.join(fields)}. Blue is a predicted decrease for an inward move. "
                'This is not an experimental result.'})
    except Cancelled:
        raise
    except Exception as e:
        adjoint.discard_later_times(root,primal)
        notes.append({'code':'design_advice','status':'unavailable','detail':'The steady solution is kept. Design advice did not finish: '+str(e)})
    finally:
        remove_processor_dirs(root)
    if spec.elements.design_boxes:
        try:
            from .jobs.optimise import enqueue
            queued=enqueue(identifier)
            notes.append({'code':'morph','status':'info','detail':f'Shape morphing is a separate attempt ({queued["id"]}), so this solution is not overwritten.'})
        except Exception as e:
            notes.append({'code':'morph','status':'unavailable','detail':'The steady solution is kept. Morphing was not queued: '+str(e)})

def schedule_retry(job,spec,error,root):
    """Queue an automatic new attempt when the spec allows it and a rule matches."""
    attempt=job.get('attempt') or 1
    if spec.solver.auto_retry<attempt:return None
    from .retry import plan
    planned=plan(job['kind'],job['spec'],error,db.run(job['id'])['result'],root)
    if not planned:return None
    reason,changes,new_spec,kind=planned
    parent=job.get('parent_id') if kind=='solve' else None
    new=db.enqueue(new_spec,kind,job.get('study_id'),parent or job['id'],simulation_id=job.get('simulation_id'),
                   attempt=attempt+1,retry_of=job['id'],retry_reason=reason)
    db.merge_result(new['id'],{'changes':changes})
    db.event(job['id'],{'stage':'retry','message':f'Automatic retry queued as attempt {attempt+1}: {reason}'})
    return new

# Job kinds handled outside the case/mesh/solve pipeline. Later phases register their
# handlers here (package, report, insights, ...); each declares its lane in db.KIND_LANES.
JOB_HANDLERS={'view':extract_view}

def register(kind):
    def decorator(handler):
        JOB_HANDLERS[kind]=handler
        return handler
    return decorator

def lane_loop(lane,poll=1.0,once=False):
    """Serve one lane until stopped. Recovery is per lane, so restarting the post lane
    never interrupts a running solve."""
    global CURRENT_LANE
    CURRENT_LANE=lane
    db.require_schema()
    from . import jobs  # registers the post-lane handlers
    with worker_lock(lane):
        db.recover(lanes=(lane,),statuses=('running',))
        capabilities=None
        if lane=='compute':
            from .capabilities import probe_runtime
            capabilities=probe_runtime()
        db.heartbeat(lane=lane,capabilities=capabilities)
        print(f'GustSim {lane} worker ready. One job at a time on this lane.',flush=True)
        memory={'post':float(os.environ.get('GUSTSIM_POST_MEMORY_GB','4'))}.get(lane)
        while True:
            db.heartbeat(lane=lane)
            job=db.claim((lane,))
            if job:process_job(job,LocalExecutor(memory_gb=memory))
            elif once:return
            else:time.sleep(poll)

def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description='GustSim lane worker')
    parser.add_argument('--lanes',default=os.environ.get('GUSTSIM_WORKER_LANES','compute,post'),
                        help='Comma-separated lanes served by this container (compute, post)')
    args=parser.parse_args(argv)
    lanes=[l.strip() for l in args.lanes.split(',') if l.strip()]
    unknown=set(lanes)-{'compute','post'}
    if unknown or not lanes:raise SystemExit('Unknown worker lanes: '+', '.join(sorted(unknown)))
    if len(lanes)==1:
        return lane_loop(lanes[0])
    import multiprocessing
    processes=[multiprocessing.Process(target=lane_loop,args=(lane,),name=f'gustsim-{lane}') for lane in lanes]
    for process in processes:process.start()
    try:
        while all(p.is_alive() for p in processes):time.sleep(2)
    finally:
        # One lane dying takes the container down so the orchestrator restarts it cleanly.
        for process in processes:
            if process.is_alive():process.terminate()
        for process in processes:process.join(timeout=10)
    raise SystemExit('A GustSim worker lane stopped; see the log above')

if __name__=='__main__':
    # `python -m gustsim.worker` loads this file as __main__, while gustsim.jobs registers its
    # handlers on the importable gustsim.worker module. Run through that module so both share
    # one JOB_HANDLERS registry (otherwise the post lane reports "Unknown job kind package").
    from gustsim.worker import main as run_worker
    run_worker()
