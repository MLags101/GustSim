"""Use-specific measurements and durable default views from real run evidence."""
import hashlib
import json
import time
import numpy as np
from . import db
from .models import ViewSpec


def pipe_metrics(case, spec, fluxes, pressures):
    from .quality import latest_table
    inlet=[b.patch for b in spec.boundaries if b.kind in {'velocity_inlet','flow_inlet','pressure_inlet'}]
    outlet=[b.patch for b in spec.boundaries if b.kind=='pressure_outlet']
    metrics={k:None for k in ('outlet_volume_flow_m3s','outlet_mass_flow_kgs','inlet_pressure_pa','outlet_pressure_pa','total_pressure_loss_pa','head_loss_m')}
    findings=[]
    if len(inlet)!=1 or len(outlet)!=1: return metrics,findings,'Requires one inlet and one outlet'
    a,b=inlet[0],outlet[0]
    metrics.update(inlet_pressure_pa=pressures.get(a),outlet_pressure_pa=pressures.get(b),outlet_volume_flow_m3s=fluxes.get(b),outlet_mass_flow_kgs=fluxes[b]*spec.fluid.density if b in fluxes else None)
    totals={}; reverse=False; missing=False
    for patch in (a,b):
        rows=latest_table(case,f'postProcessing/totalpressure_{patch}/*/surfaceFieldValue.dat')
        absolute=latest_table(case,f'postProcessing/absoluteFlux_{patch}/*/surfaceFieldValue.dat')
        if rows and len(rows[-1])>=2 and np.isfinite(rows[-1][1]): totals[patch]=rows[-1][1]
        if absolute and len(absolute[-1])>=2 and patch in fluxes:
            reverse |= absolute[-1][1]-abs(fluxes[patch]) > max(1e-12,absolute[-1][1]*1e-6)
        else: missing=True
    directional=fluxes.get(a,0)<-1e-12 and fluxes.get(b,0)>1e-12
    reason='Full-resolution total-pressure or directional flux evidence unavailable'
    if reverse or not directional:
        reason='Reverse or insufficient directional flow; loss quantities are unavailable'
        findings.append({'code':'pipe_direction','status':'review','detail':reason})
    elif not missing and a in totals and b in totals:
        loss=totals[a]-totals[b]
        metrics.update(total_pressure_loss_pa=loss,head_loss_m=loss/(spec.fluid.density*9.80665))
        reason=''
        if loss<0: findings.append({'code':'negative_loss','status':'review','detail':'Measured total-pressure loss is negative; inspect convergence and boundary conditions'})
    return metrics,findings,reason


def definitions(spec, metrics, reason=''):
    unavailable={key:reason if key in ('total_pressure_loss_pa','head_loss_m') and reason else 'No matching measured evidence or undefined reference quantity' for key,value in metrics.items() if value is None}
    if spec.use_case and not spec.use_case.references_confirmed:
        for key in ('cd','cl'):
            if metrics.get(key) is None: unavailable[key]='Confirm reference area, length, speed and axes to calculate coefficients'
    if spec.rotation.enabled and spec.flow.speed==0:
        unavailable['efficiency']='Static propeller efficiency is undefined at zero advance speed'
    return {'kind':spec.use_case.kind if spec.use_case else 'custom',
            'definitions':{'pressure_pa':'Area-averaged static gauge pressure, rho × OpenFOAM p',
              'total_pressure_loss_pa':'Inlet minus outlet phi-weighted mean of rho*p + 0.5*rho*|U|², Pa; no elevation term',
              'head_loss_m':'Total-pressure loss / (rho*9.80665), metres',
              'outlet_mass_flow_kgs':'Density × signed outward outlet volume flux, kg/s',
              'fluid_torque_nm':'Fluid moment on rotor about rotation origin, projected on positive rotation axis, N·m',
              'torque_nm':'Required driving torque, positive in commanded RPM direction, N·m',
              'shaft_power_w':'Required driving torque × absolute angular speed, W'},
            'unavailable':unavailable,
            'force_patches':spec.use_case.force_patches if spec.use_case else [],
            'references':spec.references.model_dump(), 'rotation_origin':spec.rotation.origin}


def default_views(spec):
    """The result set generated once per guided run.

    Two views (a pressure surface and one velocity slice) are not enough to judge an
    external aerodynamics result: the wake, the near-wall resolution and the flow
    topology all need their own view. Each use case gets the views that its reported
    quantities actually depend on. Extraction is per-view, so one failure does not
    remove the others.
    """
    meta=db.geometry(spec.geometry_id)
    bounds=np.asarray(meta['bounds'])
    center=np.mean(bounds,axis=0)
    span=float(max(bounds[1]-bounds[0])) or 1.0
    kind=spec.use_case.kind
    if kind=='propeller':
        direction=np.asarray(spec.rotation.axis,dtype=float)
        direction=direction/max(np.linalg.norm(direction),1e-12)
        center=np.asarray(spec.rotation.origin,dtype=float)
        patches=spec.rotation.patches
    else:
        direction=np.asarray(spec.flow.velocity(),dtype=float)
        direction=direction/max(np.linalg.norm(direction),1e-12)
        patches=spec.use_case.force_patches
    up=np.asarray(spec.references.lift_axis,dtype=float) if kind=='aerodynamics' else np.eye(3)[np.argmin(np.abs(direction))]
    normal=np.cross(direction,up)
    if np.linalg.norm(normal)<1e-12: normal=np.array([0.0,1.0,0.0])
    normal=normal/np.linalg.norm(normal)
    second=np.cross(direction,normal)
    if np.linalg.norm(second)<1e-12: second=np.array([0.0,0.0,1.0])
    second=second/np.linalg.norm(second)

    views=[ViewSpec(kind='surface',field='pressure_pa',patches=patches),
           ViewSpec(kind='slice',field='U',origin=tuple(center),normal=tuple(normal))]
    if kind=='pipe':
        views.append(ViewSpec(kind='slice',field='pressure_pa',origin=tuple(center),normal=tuple(normal)))
        return views
    # Streamlines seeded upstream show separation and recirculation, which a single
    # section plane hides.
    views.append(ViewSpec(kind='streamlines',field='U',
                          origin=tuple(center-direction*span*0.7),
                          seed_radius=max(span*0.65,1e-6),resolution=180))
    views.append(ViewSpec(kind='slice',field='U',origin=tuple(center),normal=tuple(second)))
    if kind=='aerodynamics':
        # A plane one body length downstream: wake size is the clearest visual
        # explanation of a drag number.
        views.append(ViewSpec(kind='slice',field='U',
                              origin=tuple(center+direction*span),normal=tuple(direction)))
    if spec.flow.turbulence!='laminar':
        # y+ decides whether the wall treatment was valid at all, so it belongs in the
        # default set rather than behind a manual extraction.
        views.append(ViewSpec(kind='surface',field='yPlus',patches=patches))
    return views


def queue_default_views(identifier,spec):
    # Deterministic identifiers + a single transaction prevent duplicate jobs on recovery.
    for index,view in enumerate(default_views(spec)):
        # v2: the default set changed, so the per-index identity must change with it or a
        # re-queued run would collide with a previous run's view at the same index.
        view_id=hashlib.sha256(f'{identifier}:default:{index}:v2'.encode()).hexdigest()[:32]
        job_id=hashlib.sha256(f'{view_id}:job'.encode()).hexdigest()[:32]
        payload={'run_id':identifier,'view_id':view_id,'view':view.model_dump(mode='json'),'automatic':True}
        now=time.time()
        with db.connection() as c:
            if c.execute('SELECT 1 FROM views WHERE id=?',(view_id,)).fetchone(): continue
            c.execute('INSERT INTO views VALUES(?,?,?,?)',(view_id,identifier,now,view.model_dump_json()))
            c.execute('INSERT INTO runs(id,created,updated,status,stage,kind,spec,parent_id) VALUES(?,?,?,?,?,?,?,?)',(job_id,now,now,'queued','queued','view',json.dumps(payload),identifier))
        db.event(job_id,{'stage':'queued','message':'Automatic '+view.kind+' view queued'})
