"""Analytical and reference-data acceptance checks, independent of UI claims."""
import argparse
import json
from pathlib import Path
import numpy as np

def pipe_profile(radius,pipe_radius,mean_velocity):
    r=np.asarray(radius,dtype=float)
    if pipe_radius<=0 or np.any(r<0) or np.any(r>pipe_radius):raise ValueError('Sample radii must lie within a positive pipe radius')
    return 2*mean_velocity*(1-(r/pipe_radius)**2)

def pipe_pressure_drop(viscosity,length,radius,mean_velocity):
    if viscosity<=0 or length<=0 or radius<=0:raise ValueError('Viscosity, length and radius must be positive')
    return 8*viscosity*length*mean_velocity/radius**2

def couette_profile(radius,inner_radius,outer_radius,inner_omega,outer_omega=0):
    r=np.asarray(radius,dtype=float)
    if not 0<inner_radius<outer_radius or np.any(r<inner_radius) or np.any(r>outer_radius):raise ValueError('Invalid annulus or sample radius')
    a=(outer_omega*outer_radius**2-inner_omega*inner_radius**2)/(outer_radius**2-inner_radius**2)
    b=inner_radius**2*outer_radius**2*(inner_omega-outer_omega)/(outer_radius**2-inner_radius**2)
    return a*r+b/r

def normalized_error(measured,reference):
    m,r=np.asarray(measured,dtype=float),np.asarray(reference,dtype=float)
    if m.shape!=r.shape or not m.size or not np.isfinite(m).all() or not np.isfinite(r).all():raise ValueError('Finite matching samples required')
    scale=float(np.max(np.abs(r)))
    if scale<=1e-12:raise ValueError('A nonzero reference scale is required')
    return float(np.max(np.abs(m-r))/scale)

def evaluate(data):
    kind=data['benchmark']; checks={}
    if kind=='pipe':
        reference=pipe_profile(data['radius_m'],data['pipe_radius_m'],data['mean_velocity_m_s'])
        checks['velocity_profile']=normalized_error(data['axial_velocity_m_s'],reference)
        expected=pipe_pressure_drop(data['dynamic_viscosity_pa_s'],data['measurement_length_m'],data['pipe_radius_m'],data['mean_velocity_m_s'])
        checks['pressure_drop']=normalized_error([data['pressure_drop_pa']],[expected]);tolerance=.02
    elif kind=='rotating_cylinders':
        expected=couette_profile(data['radius_m'],data['inner_radius_m'],data['outer_radius_m'],data['inner_omega_rad_s'],data.get('outer_omega_rad_s',0))
        checks['tangential_velocity']=normalized_error(data['tangential_velocity_m_s'],expected);tolerance=.02
    elif kind=='pptc':
        if len(data['points'])<3:raise ValueError('At least three open-water operating points are required')
        for point in data['points']:
            for metric in ('kt','kq'):
                checks[f"J={point['advance_ratio']} {metric}"]=normalized_error([point[metric]],[point['reference_'+metric]])
        tolerance=.1
    elif kind=='mesh_study':
        if len(data['resolutions'])!=3:raise ValueError('Provide three meshes in increasing resolution order')
        for metric in data['metrics']:
            checks[metric]=normalized_error([data['resolutions'][-2][metric]],[data['resolutions'][-1][metric]])
        tolerance=.05
    else:raise ValueError('Unknown benchmark type')
    return {'benchmark':kind,'tolerance':tolerance,'passed':all(e<=tolerance for e in checks.values()),'normalized_errors':checks,
            'provenance':data.get('provenance',{}),'scope':'Agreement with supplied measurements and conditions; does not certify other cases'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('measurements',type=Path);parser.add_argument('--output',type=Path);args=parser.parse_args()
    result=evaluate(json.loads(args.measurements.read_text()));text=json.dumps(result,indent=2)
    if args.output:args.output.write_text(text,encoding='utf-8')
    print(text);raise SystemExit(0 if result['passed'] else 1)

if __name__=='__main__':main()
