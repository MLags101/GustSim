"""Interpret solver evidence without conflating process exit with convergence."""
import math
import re
from pathlib import Path
import numpy as np
from .models import SimulationSpec

NUMBER=r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"

def numeric_rows(path):
    if not path.exists():return []
    rows=[]
    for line in path.read_text(errors="replace").splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            try: rows.append([float(v) for v in re.findall(NUMBER,line)])
            except ValueError: pass
    return rows

def latest_table(root,pattern):
    files=list(root.glob(pattern))
    if not files:return []
    rows=[]
    for file in sorted(files,key=lambda p:float(p.parent.name) if p.parent.name.replace('.','',1).isdigit() else 0):
        rows.extend(numeric_rows(file))
    return rows

def force_history(case,name="loads"):
    # OpenCFD forces writes force.dat and moment.dat separately. Each row contains
    # time, total(3), pressure(3), viscous(3), optional porous(3).
    force=latest_table(case,f"postProcessing/{name}/*/force.dat")
    moment=latest_table(case,f"postProcessing/{name}/*/moment.dat")
    moments={row[0]:row[1:4] for row in moment if len(row)>=4}
    return [{"iteration":r[0],"force":r[1:4],"moment":moments.get(r[0],[None,None,None])} for r in force if len(r)>=4]

def parse_log(text):
    rows=[]; iteration=0
    for line in text.splitlines():
        t=re.match(r"Time = ("+NUMBER+r")",line)
        if t:iteration=float(t.group(1))
        match=re.search(r"Solving for (\w+), Initial residual = ("+NUMBER+r"), Final residual = ("+NUMBER+r")",line)
        if match:rows.append({"iteration":iteration,"field":match.group(1),"initial":float(match.group(2)),"final":float(match.group(3))})
    return rows

def stability(history,key):
    if len(history)<20:return None
    values=np.asarray([h[key] for h in history[-20:]],dtype=float)
    if not np.isfinite(values).all():return None
    mean=np.mean(values,axis=0)
    denominator=max(float(np.linalg.norm(mean)),1e-12)
    return float(np.max(np.linalg.norm(values-mean,axis=1))/denominator)


def rotor_load(row, spec):
    r=spec.rotation
    if any(v is None for v in row['moment']):return None
    force=np.asarray(row['force'])
    moment=np.asarray(row['moment'])-np.cross(np.asarray(r.origin)-np.asarray(spec.references.origin),force)
    fluid_torque=float(moment@r.axis)
    driving=-fluid_torque*np.sign(r.rpm)
    return {'iteration':row['iteration'],'thrust_n':float(force@spec.references.thrust_axis),
            'fluid_torque_nm':fluid_torque,'torque_nm':driving,'shaft_power_w':driving*abs(r.rpm)*math.pi/30}

def solve_log(case):
    # The robust numerics profile solves in two stages with separate logs.
    return "\n".join((case/"logs"/name).read_text(errors="replace") for name in ("solve_startup.log","solve.log") if (case/"logs"/name).exists())


def convergence_assessment(case,spec,history):
    """The convergence verdict for a v2 run: the live monitor's record when it ran with early
    stopping, otherwise a post-hoc assessment of the same force history."""
    import json
    path=case/"results"/"convergence.json"
    if path.exists() and spec.convergence.enabled:
        try:return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:pass
    from .monitor import assess
    summary=assess(case,spec,history).summary()
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(summary,indent=2),encoding="utf-8")
    return summary


def window_mean(rows,key,window):
    from .convergence import averaged
    values=[r[key] for r in rows if r.get(key) is not None and all(v is not None for v in np.atleast_1d(r[key]))]
    iterations=[r["iteration"] for r in rows if r.get(key) is not None and all(v is not None for v in np.atleast_1d(r[key]))]
    mean,std,basis=averaged(iterations,values,window)
    return mean,std,basis


def analyze(case:Path,spec:SimulationSpec):
    text=solve_log(case)
    residuals=parse_log(text)
    history=force_history(case)
    last={};last_iteration=None
    for r in residuals:
        if r['iteration']!=last_iteration:last={};last_iteration=r['iteration']
        last.setdefault(r['field'],r['initial'])
    required={"p","Ux","Uy","Uz"} | ({"k","omega"} if spec.flow.turbulence!="laminar" else set())
    residual_pass=required<=last.keys() and all(last[f]<=spec.solver.residual_target for f in required)
    imbalance=None; fluxes={}; pressures={}
    for boundary in spec.boundaries:
        rows=latest_table(case,f"postProcessing/flux_{boundary.patch}/*/surfaceFieldValue.dat")
        if rows and len(rows[-1])>=2:fluxes[boundary.patch]=rows[-1][1]
        rows=latest_table(case,f"postProcessing/pressure_{boundary.patch}/*/surfaceFieldValue.dat")
        if rows and len(rows[-1])>=2:pressures[boundary.patch]=rows[-1][1]*spec.fluid.density
    if fluxes:
        throughput=sum(abs(v) for v in fluxes.values())/2
        if throughput>1e-12:imbalance=abs(sum(fluxes.values()))/throughput
    steady=stability(history,"force")
    v2=spec.schema_version>=2
    transient_stats=None
    if v2 and spec.time.mode=="transient" and (case/"results"/"transient_plan.json").exists():
        # Loads are averaged over the transient window; the steady stage only initialised the flow.
        from .transient import analyse
        transient_stats=analyse(case,spec)
        start=transient_stats["plan"]["start"]
        convergence=convergence_assessment(case,spec,[h for h in history if h["iteration"]<=start]) if history else None
        window=(transient_stats["window"]["start"],transient_stats["window"]["end"]) if transient_stats.get("window") else (start,None)
    else:
        convergence=convergence_assessment(case,spec,history) if v2 and history else None
        window=tuple((convergence.get("averaging") or {}).get(k) for k in ("start","end")) if convergence and convergence.get("averaging") else None
    rotor_history=force_history(case,"rotorLoads") if spec.rotation.enabled else []
    projected_history=[{'iteration':row['iteration'],'drag_n':float(np.asarray(row['force'])@spec.references.drag_axis),'lift_n':float(np.asarray(row['force'])@spec.references.lift_axis)} for row in history]
    rotor_load_history=[value for row in rotor_history if (value:=rotor_load(row,spec)) is not None]
    torque_steady=stability(rotor_history,"moment") if spec.rotation.enabled else None
    mesh_log=(case/"logs"/"check.log").read_text(errors="replace") if (case/"logs"/"check.log").exists() else ""
    cell_match=re.search(r"\bcells:\s+(\d+)",mesh_log)
    findings=[]
    def check(code,passed,detail):
        findings.append({"code":code,"status":"pass" if passed is True else "fail" if passed is False else "unavailable","detail":detail})
    check("mesh","Mesh OK." in mesh_log,"OpenFOAM checkMesh")
    advanced=(case/'logs/mesh_diagnostics.log').read_text(errors='replace') if (case/'logs/mesh_diagnostics.log').exists() else ''
    if 'Failed ' in advanced:
        findings.append({'code':'extended_mesh_diagnostics','status':'review','detail':'Extended geometry diagnostics reported findings (including possible concave cells). Inspect logs/mesh_diagnostics.log; explicit solver mesh-quality limits passed separately.'})
    if transient_stats is None:
        res_msg=f"All solved fields meet residual target ({spec.solver.residual_target})" if residual_pass else f"Field residuals remain above target ({spec.solver.residual_target}); increase iterations in solver controls"
        check("residuals",residual_pass,res_msg)
    check("mass_balance",imbalance<0.001 if imbalance is not None else None,"Net boundary flux below 0.1% of throughflow")
    if transient_stats is not None:
        from .transient import max_courant
        state=transient_stats.get("state")
        check("statistical_convergence",state=="converged" if state in ("converged","review","incomplete") else None,transient_stats.get("message",""))
        log=(case/"logs"/"transient.log").read_text(errors="replace") if (case/"logs"/"transient.log").exists() else ""
        courant=max_courant(log)
        limit=transient_stats["plan"]["max_courant"]
        check("courant",courant<=1.5*limit if courant is not None else None,
              f"Largest Courant number {courant:.3g} (limit {limit:g}; adaptive time step)" if courant is not None else "No Courant number was logged")
    elif v2:
        state=convergence["state"] if convergence else None
        check("load_convergence",state=="converged" if state else None,
              convergence["message"] if convergence else "No force history was recorded")
    else:check("load_stability",steady<0.01 if steady is not None else None,"Force variation below 1% over the last 20 iterations")
    if spec.rotation.enabled:check("torque_stability",torque_steady<0.01 if torque_steady is not None else None,"Torque variation below 1% over the last 20 iterations")
    converged="solution converged" in text.lower()
    stopped_by=((convergence or {}).get("stop") or {}).get("by")
    termination="residual_control" if converged else stopped_by or "iteration_limit"
    if transient_stats is not None:
        termination="transient_complete" if transient_stats.get("state") in ("converged","review") else "transient_incomplete"
    elif v2:
        messages={"residual_control":"SIMPLE residual-control convergence reported",
                  "force_convergence":"Stopped when the monitored loads converged and their average settled; residual convergence is reported separately",
                  "runaway_stopped":"Stopped because the monitored loads ran away; inspect the setup and mesh",
                  "iteration_limit":"Stopped at the iteration budget before the convergence criteria were met"}
        check("solver_termination",termination in ("residual_control","force_convergence"),messages[termination])
    else:
        term_msg="SIMPLE residual-control convergence reported" if converged else f"Stopped at iteration limit ({spec.solver.iterations} iter) before residual convergence; increase iterations in solver controls"
        check("solver_termination",converged,term_msg)
    check("experimental_validation",None,"Template has not yet passed the experimental benchmark release gates")
    metrics={"force_n":None,"moment_nm":None,"drag_n":None,"lift_n":None,"side_force_n":None,"cd":None,"cl":None,"thrust_n":None,"torque_nm":None,"shaft_power_w":None,"efficiency":None,"ct":None,"cq":None,"advance_ratio":None}
    q=0.5*spec.fluid.density*spec.flow.speed**2
    averaging=None
    if history:
        force=np.asarray(history[-1]["force"]);moment=history[-1]["moment"]
        if v2:
            # Reported loads are the mean over the convergence averaging window (else the
            # last 20% of iterations); the last iteration is kept alongside for reference.
            mean,std,basis=window_mean(history,"force",window)
            moment_mean,_,_=window_mean(history,"moment",window)
            force=np.asarray(mean);moment=moment_mean.tolist() if moment_mean is not None else moment
            averaging={"basis":basis,"start":window[0] if window else None,"end":window[1] if window else None,
                       "force_std_n":np.asarray(std).tolist()}
        side=np.cross(spec.references.lift_axis,spec.references.drag_axis)
        metrics.update(force_n=force.tolist(),moment_nm=moment,drag_n=float(force@spec.references.drag_axis),lift_n=float(force@spec.references.lift_axis),side_force_n=float(force@side))
        if q>0 and (not spec.use_case or spec.use_case.references_confirmed):
            metrics.update(cd=metrics["drag_n"]/(q*spec.references.area),cl=metrics["lift_n"]/(q*spec.references.area))
    if rotor_load_history and rotor_load_history[-1]['iteration']==rotor_history[-1]['iteration']:
        loads=rotor_load_history[-1];r=spec.rotation
        if v2:
            loads=dict(loads)
            for key in ('thrust_n','torque_nm','fluid_torque_nm'):
                mean,_,_=window_mean(rotor_load_history,key,window)
                if mean is not None:loads[key]=float(mean)
        thrust=loads['thrust_n'];torque=loads['torque_nm']
        metrics['fluid_torque_nm']=loads['fluid_torque_nm']
        n=abs(r.rpm)/60;diameter=2*r.blade_radius
        power=torque*2*math.pi*n
        advance=-float(np.asarray(spec.flow.velocity())@spec.references.thrust_axis)
        metrics.update(thrust_n=thrust,torque_nm=torque,shaft_power_w=power,ct=thrust/(spec.fluid.density*n*n*diameter**4),cq=torque/(spec.fluid.density*n*n*diameter**5),advance_ratio=advance/(n*diameter),efficiency=thrust*advance/power if power>0 and advance>0 and thrust>0 else None)
    inlet=[pressures[b.patch] for b in spec.boundaries if b.kind in {"velocity_inlet","flow_inlet","pressure_inlet"} and b.patch in pressures]
    outlet=[pressures[b.patch] for b in spec.boundaries if b.kind=="pressure_outlet" and b.patch in pressures]
    metrics["pressure_drop_pa"]=inlet[0]-outlet[0] if spec.mode=='internal' and len(inlet)==len(outlet)==1 else None
    from .analysis import pipe_metrics, definitions
    reason=''
    if spec.mode=='internal':
        pipe,extra,reason=pipe_metrics(case,spec,fluxes,pressures)
        metrics.update(pipe);findings.extend(extra)
    numerical=all(f["status"]=="pass" for f in findings if f["code"]!="experimental_validation")
    # One pass per patch: force_history globs and reparses force.dat/moment.dat on every call.
    components={}
    for b in spec.boundaries:
        rows=force_history(case,"load_"+b.patch)
        if rows:components[b.patch]=rows[-1]
    last_iteration=None
    if v2 and history:
        f=np.asarray(history[-1]["force"])
        last_iteration={"iteration":history[-1]["iteration"],"force_n":f.tolist(),"drag_n":float(f@spec.references.drag_axis),"lift_n":float(f@spec.references.lift_axis)}
    v2_fields={"convergence":convergence,"averaging":averaging,"metrics_last_iteration":last_iteration} if v2 else {}
    if transient_stats is not None:
        v2_fields["transient"]={k:transient_stats.get(k) for k in ("state","message","strouhal","dominant_frequency_hz","periods_in_window","window","duration_reached_s")}
        v2_fields["transient"]["coefficients"]={k:(transient_stats.get("statistics") or {}).get(k) for k in ("cd","cl","cs")}
    return {**v2_fields,"numerical_status":"checks_passed" if numerical else "review_required","validation_status":"experimental_validation_pending",
            "termination":(termination if v2 else "converged" if converged else "iteration_limit_or_unconfirmed"), "findings":findings,"metrics":metrics,
            "residuals":residuals,"history":history,"rotor_history":rotor_history,"components":components,
            'projected_history':projected_history,'rotor_load_history':rotor_load_history,
            "mass_imbalance":imbalance,"force_variation":steady,"boundary_flux_m3s":fluxes,"boundary_pressure_pa":pressures,
            "cell_count":int(cell_match.group(1)) if cell_match else None, 'analysis':definitions(spec,metrics,reason)}
