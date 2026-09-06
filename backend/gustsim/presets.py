"""Typed, reviewable common-use setup generators."""
import math
import numpy as np
from . import db, geometry, validation
from .models import Boundary, Fluid, UseCase, Rotation, References, SimulationSpec


def unit(v):
    a = np.asarray(v, dtype=float)
    if np.linalg.norm(a) < 1e-12: raise ValueError('Direction must be nonzero')
    return a / np.linalg.norm(a)


def rotor_suggestion(identifier, request):
    meta = db.geometry(identifier)
    names = {p['name']:p['group'] for p in meta['patches']}
    if not request.patches or not set(request.patches) <= names.keys(): raise ValueError('Select known rotor surfaces')
    mesh, groups = geometry.load(identifier)
    selected = np.isin(groups, [names[p] for p in request.patches])
    points = mesh.vertices[np.unique(mesh.faces[selected])]
    axis = unit(request.axis)
    delta = points - np.asarray(request.origin)
    axial = delta @ axis
    radial = np.linalg.norm(delta - np.outer(axial, axis), axis=1)
    blade = max(float(radial.max()), 1e-5)
    return {'axis':axis.tolist(), 'origin':list(request.origin), 'blade_radius':blade,
            'radius':blade*1.15, 'length':max(float(np.abs(axial).max())*2*1.2, blade*.2),
            'patches':request.patches, 'stationary_patches':sorted(set(names)-set(request.patches))}


def generate(request):
    meta = db.geometry(request.geometry_id)
    spec = validation.defaults(request.geometry_id)
    spec.name = meta['filename'].rsplit('.',1)[0] + ' · ' + request.kind
    spec.use_case = UseCase(kind=request.kind, force_patches=request.patches)
    spec.flow.turbulence = request.turbulence
    spec.flow.speed = request.speed
    if request.fluid == 'water': spec.fluid = Fluid(name='water', density=998.2, dynamic_viscosity=.001002, speed_of_sound=1482)
    lo, hi = np.asarray(meta['bounds'])
    center, length = (lo+hi)/2, max(float(max(hi-lo)), .01)
    names = {p['name'] for p in meta['patches']}
    if not set(request.patches) <= names: raise ValueError('Unknown selected component surfaces')
    if request.kind == 'pipe':
        if request.inlet == request.outlet or not {request.inlet,request.outlet} <= names:
            raise ValueError('Choose different inlet and outlet surfaces on the prepared fluid envelope')
        if request.driving in ('speed','pressure') and request.speed <= 0:
            raise ValueError('A positive inlet speed or turbulence reference speed is required')
        mesh,groups = geometry.load(request.geometry_id)
        patch = next(p for p in meta['patches'] if p['name']==request.inlet)
        mask = groups==patch['group']
        area = float(mesh.area_faces[mask].sum())
        normal = -np.sum(mesh.face_normals[mask]*mesh.area_faces[mask,None],axis=0)
        direction = unit(normal)
        if np.linalg.norm(normal)/area < .99: raise ValueError('Guided pipe inlet must be planar; choose a planar cap')
        outpatch=next(p for p in meta['patches'] if p['name']==request.outlet)
        outmask=groups==outpatch['group']
        outarea=float(mesh.area_faces[outmask].sum())
        outnormal=np.sum(mesh.face_normals[outmask]*mesh.area_faces[outmask,None],axis=0)
        if outarea<=0 or np.linalg.norm(outnormal)/outarea<.99:
            raise ValueError('Guided pipe outlet must be planar; choose a planar cap')
        speed = request.flow_rate/area if request.driving=='flow' else request.speed
        spec.flow.speed = speed
        spec.mode='internal';spec.domain.fluid_point=tuple(center)
        inlet = Boundary(patch=request.inlet, kind={'flow':'flow_inlet','speed':'velocity_inlet','pressure':'pressure_inlet'}[request.driving],
                         velocity=tuple(direction*speed), flow_rate=request.flow_rate, pressure_pa=request.inlet_pressure_pa)
        spec.boundaries=[Boundary(patch=p,kind='wall') for p in sorted(names-{request.inlet,request.outlet})]+[inlet,Boundary(patch=request.outlet,kind='pressure_outlet',pressure_pa=request.outlet_pressure_pa)]
        spec.use_case.force_patches = sorted(names-{request.inlet,request.outlet})
    elif request.kind=='aerodynamics':
        if request.speed <= 0: raise ValueError('Travel speed must be positive')
        if request.fluid != 'air': raise ValueError('The aerodynamics preset uses air')
        if not request.patches: raise ValueError('Select the force-bearing components')
        direction = -unit(request.direction)
        lift = unit(request.lift_axis)
        if abs(float(direction@lift)) > 1e-6: raise ValueError('Lift direction must be perpendicular to travel direction')
        spec.references = References(area=spec.references.area,length=length,origin=tuple(center),drag_axis=tuple(direction),lift_axis=tuple(lift))
        # Open far-field faces support arbitrary travel direction.
        spec.boundaries=[Boundary(patch=p,kind='freestream') for p in validation.DOMAIN_PATCHES]
        spec.boundaries += [Boundary(patch=p,kind='wall') for p in sorted(names)]
    else:
        if request.rpm == 0: raise ValueError('Rotor RPM must be nonzero')
        suggestion = rotor_suggestion(request.geometry_id, request)
        spec.rotation=Rotation(enabled=True,rpm=request.rpm,**suggestion)
        spec.mode='rotor'
        direction=unit(request.axis)
        spec.references.thrust_axis=tuple(-direction)
        spec.references.origin=tuple(request.origin)
        spec.boundaries=[Boundary(patch=p,kind='freestream') for p in validation.DOMAIN_PATCHES]
        spec.boundaries += [Boundary(patch=p,kind='wall') for p in sorted(names)]
    spec.flow.alpha_deg=math.degrees(math.atan2(direction[2],direction[0]))
    spec.flow.beta_deg=math.degrees(math.asin(float(np.clip(direction[1],-1,1))))
    if request.kind!='pipe':
        spec.domain.minimum=tuple(lo-length*3-np.maximum(-direction,0)*length*5)
        spec.domain.maximum=tuple(hi+length*3+np.maximum(direction,0)*length*5)
        spec.domain.fluid_point=tuple(np.asarray(spec.domain.minimum)+length*.5)
    spec = SimulationSpec.model_validate(spec.model_dump())
    return {'spec':spec.model_dump(mode='json'), 'report':validation.validate(spec),
            'review':['Confirm model dimensions in metres', 'Confirm the point lies in the intended fluid volume', 'Confirm reference directions and selected surfaces', 'Mesh presets do not guarantee accuracy']}
