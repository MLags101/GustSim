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

def analyze(case:Path,spec:SimulationSpec):
    text=(case/"logs"/"solve.log").read_text(errors="replace") if (case/"logs"/"solve.log").exists() else ""
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
    rotor_history=force_history(case,"rotorLoads") if spec.rotation.enabled else []
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
    check("residuals",residual_pass,"All solved fields meet the configured initial-residual threshold")
    check("mass_balance",imbalance<0.001 if imbalance is not None else None,"Net boundary flux below 0.1% of throughflow")
    check("load_stability",steady<0.01 if steady is not None else None,"Force variation below 1% over the last 20 iterations")
    if spec.rotation.enabled:check("torque_stability",torque_steady<0.01 if torque_steady is not None else None,"Torque variation below 1% over the last 20 iterations")
    converged="solution converged" in text.lower()
    check("solver_termination",converged,"SIMPLE residual-control convergence reported; iteration-limit completion alone is insufficient")
    check("experimental_validation",None,"Template has not yet passed the experimental benchmark release gates")
    metrics={"force_n":None,"moment_nm":None,"drag_n":None,"lift_n":None,"side_force_n":None,"cd":None,"cl":None,"thrust_n":None,"torque_nm":None,"shaft_power_w":None,"efficiency":None,"ct":None,"cq":None,"advance_ratio":None}
    q=0.5*spec.fluid.density*spec.flow.speed**2
    if history:
        force=np.asarray(history[-1]["force"])
        side=np.cross(spec.references.lift_axis,spec.references.drag_axis)
        metrics.update(force_n=force.tolist(),moment_nm=history[-1]["moment"],drag_n=float(force@spec.references.drag_axis),lift_n=float(force@spec.references.lift_axis),side_force_n=float(force@side))
        if q>0:
            metrics.update(cd=metrics["drag_n"]/(q*spec.references.area),cl=metrics["lift_n"]/(q*spec.references.area))
    if rotor_history:
        h=rotor_history[-1];r=spec.rotation
        if all(v is not None for v in h["moment"]):
            # Forces/moments are fluid loads on the rotor. Shift to rotor origin.
            force=np.asarray(h["force"])
            moment=np.asarray(h["moment"])-np.cross(np.asarray(r.origin)-np.asarray(spec.references.origin),force)
            thrust=float(force@spec.references.thrust_axis)
            torque=-float(moment@r.axis)*np.sign(r.rpm)
            n=abs(r.rpm)/60;diameter=2*r.blade_radius
            power=torque*2*math.pi*n
            advance=-float(np.asarray(spec.flow.velocity())@spec.references.thrust_axis)
            metrics.update(thrust_n=thrust,torque_nm=torque,shaft_power_w=power,ct=thrust/(spec.fluid.density*n*n*diameter**4),cq=torque/(spec.fluid.density*n*n*diameter**5),advance_ratio=advance/(n*diameter),efficiency=thrust*advance/power if power>0 and advance>0 and thrust>0 else None)
    inlet=[pressures[b.patch] for b in spec.boundaries if b.kind in {"velocity_inlet","flow_inlet","pressure_inlet"} and b.patch in pressures]
    outlet=[pressures[b.patch] for b in spec.boundaries if b.kind=="pressure_outlet" and b.patch in pressures]
    metrics["pressure_drop_pa"]=inlet[0]-outlet[0] if spec.mode=='internal' and len(inlet)==len(outlet)==1 else None
    numerical=all(f["status"]=="pass" for f in findings if f["code"]!="experimental_validation")
    components={b.patch:force_history(case,"load_"+b.patch)[-1] for b in spec.boundaries if force_history(case,"load_"+b.patch)}
    return {"numerical_status":"checks_passed" if numerical else "review_required","validation_status":"experimental_validation_pending",
            "termination":"converged" if converged else "iteration_limit_or_unconfirmed", "findings":findings,"metrics":metrics,
            "residuals":residuals,"history":history,"rotor_history":rotor_history,"components":components,
            "mass_imbalance":imbalance,"force_variation":steady,"boundary_flux_m3s":fluxes,"boundary_pressure_pa":pressures,
            "cell_count":int(cell_match.group(1)) if cell_match else None}
