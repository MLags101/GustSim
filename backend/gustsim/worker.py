"""Single-worker process with durable attempts, cancellation and resource limits."""
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

@contextlib.contextmanager
def worker_lock():
    config.initialize()
    with (config.DATA/'worker.lock').open('a+b') as handle:
        handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as e:raise RuntimeError('Another GustSim worker already owns this data directory') from e
        try:yield
        finally:
            if os.name=='nt':
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)

class LocalExecutor:
    def run(self,identifier,command,cwd,stage,timeout):
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
        start=time.monotonic();last_heartbeat=0;buffer=[];last_flush=0
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
                        db.heartbeat(identifier);last_heartbeat=now
                        if db.run(identifier)['cancel']:raise Cancelled()
                        if now-start>timeout:raise RuntimeError(f'{stage} exceeded the configured time limit')
                        try:
                            parent=psutil.Process(process.pid)
                            rss=sum(p.memory_info().rss for p in [parent,*parent.children(recursive=True)] if p.is_running())
                            if rss>config.MEMORY_GB*1024**3:raise RuntimeError(f'{stage} exceeded the {config.MEMORY_GB:g} GB process-tree memory ceiling')
                        except psutil.NoSuchProcess:pass
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

def runtime_check():
    required=['blockMesh','surfaceFeatureExtract','snappyHexMesh','checkMesh','simpleFoam','decomposePar','reconstructPar','pvbatch']
    missing=[name for name in required if not shutil.which(name)]
    if missing:raise RuntimeError('CFD runtime unavailable: '+', '.join(missing)+'. Run the Linux worker container.')
    if os.environ.get('WM_PROJECT_VERSION','').lstrip('v')!=config.FOAM_VERSION:
        raise RuntimeError('Worker requires the OpenCFD v2606 environment; source its etc/bashrc')

def process_job(job,executor=None):
    executor=executor or LocalExecutor();identifier=job['id']
    root=config.DATA/'runs'/identifier;root.mkdir(parents=True,exist_ok=True)
    try:
        if job['kind']=='view':
            runtime_check()
            parent=config.DATA/'runs'/job['spec']['run_id']
            target=parent/'views'/job['spec']['view_id'];target.mkdir(parents=True,exist_ok=True)
            request=target/'request.json';request.write_text(json.dumps(job['spec']['view']))
            executor.run(identifier,['pvbatch','--force-offscreen-rendering',str(Path(__file__).with_name('postprocess.py')),str(parent),str(target),str(request)],root,'extracting',600)
            db.update(identifier,status='completed',stage='completed',result={'view_id':job['spec']['view_id'],'run_id':job['spec']['run_id']})
            return
        spec=SimulationSpec.model_validate(job['spec'])
        db.update(identifier,stage='preparing');foam.compile_case(spec,root)
        if job['kind']=='case':
            db.update(identifier,status='completed',stage='completed',result={'case_available':True,'fields_available':False})
            return
        runtime_check();timeout=spec.solver.timeout_minutes*60
        reused=False
        if job.get('parent_id') and job['kind']=='solve':
            parent=db.run(job['parent_id'])
            if parent['kind']=='mesh' and parent['status']=='completed':
                workflow.require_mesh(parent,spec)
                source=config.DATA/'runs'/parent['id']
                shutil.copytree(source/'constant/polyMesh',root/'constant/polyMesh')
                reused=True
                db.event(identifier,{'stage':'mesh_reuse','message':'Using the reviewed mesh from '+parent['id']})
        commands=[] if reused else [(['blockMesh'],'background'),(['surfaceFeatureExtract'],'features'),(['snappyHexMesh','-overwrite'],'meshing')]
        commands += [(['checkMesh','-meshQuality'],'check'),(['checkMesh','-allTopology','-allGeometry'],'mesh_diagnostics')]
        for command,stage in commands:
            try: executor.run(identifier,command,root,stage,timeout)
            except RuntimeError:
                if stage!='mesh_diagnostics' or 'Failed ' not in (root/'logs/mesh_diagnostics.log').read_text(errors='replace'): raise
                db.event(identifier,{'stage':stage,'message':'Extended diagnostics require review; explicit quality gate is evaluated separately'})
        mesh_log=(root/'logs/check.log').read_text(errors='replace')
        # Verify the mesh retained the same patch contract as the source geometry.
        import re
        boundary=(root/'constant/polyMesh/boundary').read_text(errors='replace')
        actual=set(re.findall(r'^\s*([A-Za-z][A-Za-z0-9_]*)\s*\n\s*\{',boundary,re.M))-{'FoamFile'}
        expected={b.patch for b in spec.boundaries}
        boundary_ok=actual==expected and not re.search(r'\bnFaces\s+0\s*;',boundary)
        from .mesh_preview import create as mesh_preview
        preview_ok=False
        try:
            mesh_preview(root,list(spec.domain.fluid_point) if spec.mode=='internal' else [(a+b)/2 for a,b in zip(spec.domain.minimum,spec.domain.maximum)])
            preview_ok=True
        except Exception as e:
            db.event(identifier,{'stage':'mesh_preview','message':str(e),'level':'warning'})
        cell_match=re.search(r'\bcells:\s+(\d+)',mesh_log)
        rotation_ok=not spec.rotation.enabled
        if spec.rotation.enabled:
            executor.run(identifier,['topoSet'],root,'rotation',timeout)
            zone_text=(root/'constant/polyMesh/cellZones').read_text(errors='replace')
            rotation_ok=bool(re.search(r'\brotor\s*\{[^}]*cellLabels\s+(?:List<label>\s+)?[1-9]\d*\s*\(',zone_text,re.S))
        summary=workflow.mesh_evidence(root,spec,boundary_ok,preview_ok,rotation_ok)
        mesh_pass='Mesh OK.' in mesh_log and boundary_ok and rotation_ok
        db.update(identifier,result={'case_available':True,'mesh_available':preview_ok,'fields_available':False,'mesh_check':'passed' if mesh_pass else 'failed','cell_count':int(cell_match.group(1)) if cell_match else None,'mesh_summary':summary})
        if not mesh_pass:raise RuntimeError('Mesh quality, boundary, or rotor-zone check failed; solving was blocked. Inspect the mesh summary and logs.')
        if job['kind']=='mesh':
            executor.run(identifier,['foamToVTK','-constant','-ascii'],root,'mesh_export',timeout)
            db.update(identifier,status='completed',stage='completed')
            return
        if spec.solver.processes>1:
            executor.run(identifier,['decomposePar','-force'],root,'decomposing',timeout)
            executor.run(identifier,['mpirun','-np',str(spec.solver.processes),'simpleFoam','-parallel'],root,'solve',timeout)
            executor.run(identifier,['reconstructPar','-latestTime'],root,'reconstructing',timeout)
        else:executor.run(identifier,['simpleFoam'],root,'solve',timeout)
        result=quality.analyze(root,spec);result.update(case_available=True,mesh_available=preview_ok,fields_available=False,mesh_summary=summary,mesh_check='passed')
        db.update(identifier,result=result)
        executor.run(identifier,['pvbatch','--force-offscreen-rendering',str(Path(__file__).with_name('postprocess.py')),str(root),str(root/'results')],root,'postprocessing',timeout)
        summary=json.loads((root/'results/field_summary.json').read_text())
        result.update(fields_available=True,field_summary=summary)
        if spec.fluid.name=='water' and summary['min_pressure_pa']+spec.fluid.reference_pressure_pa<spec.fluid.vapor_pressure_pa:
            result['findings'].append({'code':'cavitation_risk','status':'fail','detail':'Estimated absolute pressure drops below vapor pressure; single-phase solution is outside its valid envelope'})
            result['numerical_status']='review_required'
        (root/'results/quality.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        db.update(identifier,status='completed',stage='completed',result=result)
        if spec.use_case:
            try:
                from .analysis import queue_default_views
                queue_default_views(identifier,spec)
            except Exception as e:
                db.event(identifier,{'stage':'automatic_views','level':'warning','message':'Solve preserved; automatic view scheduling failed: '+str(e)})
    except Cancelled:
        db.update(identifier,status='cancelled',stage='cancelled',error='Cancelled by user; completed artifacts preserved')
    except Exception as e:
        if job['kind'] in ('mesh','solve') and not db.run(identifier)['result'].get('mesh_summary'):
            with contextlib.suppress(Exception):
                evidence=workflow.mesh_evidence(root,spec,False,False,False)
                db.update(identifier,result={**db.run(identifier)['result'],'mesh_summary':evidence,'mesh_check':'failed','mesh_available':False})
        db.update(identifier,status='failed',error=str(e))
        db.event(identifier,{'message':str(e),'level':'error'})
    finally:
        db.event(identifier,{'stage':db.run(identifier)['status'],'message':'Attempt '+db.run(identifier)['status']})

def main():
    db.initialize()
    with worker_lock():
        db.recover()
        print('GustSim worker ready. One job at a time.',flush=True)
        while True:
            db.heartbeat()
            job=db.claim()
            if job:process_job(job)
            else:time.sleep(1)

if __name__=='__main__':main()
