"""Analytical and reference-data acceptance checks, independent of UI claims.

`evaluate` compares supplied measurements with an analytical solution or with cited reference
data and returns a gate result. Writing that result to benchmarks/results/<gate>.json is what
capabilities.gate_status reads; a passed gate is necessary, never sufficient, to promote a
capability (promotion is a reviewed code change in capabilities.py).
"""
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

def blasius_cf(x,velocity,kinematic_viscosity):
    """Laminar flat-plate local skin friction, Cf = 0.664/sqrt(Re_x)."""
    x=np.asarray(x,dtype=float)
    if np.any(x<=0) or velocity<=0 or kinematic_viscosity<=0:raise ValueError('Positive stations, speed and viscosity are required')
    return .664/np.sqrt(velocity*x/kinematic_viscosity)

def two_point_curve(curve):
    """Coefficients (a, b) of dp = a*v + b*v^2 through two measured points."""
    (v1,p1),(v2,p2)=curve
    det=v1*v2*v2-v2*v1*v1
    if abs(det)<1e-12:raise ValueError('Two distinct nonzero curve velocities are required')
    return (p1*v2*v2-p2*v1*v1)/det,(v1*p2-v2*p1)/det

def normalized_error(measured,reference):
    m,r=np.asarray(measured,dtype=float),np.asarray(reference,dtype=float)
    if m.shape!=r.shape or not m.size or not np.isfinite(m).all() or not np.isfinite(r).all():raise ValueError('Finite matching samples required')
    scale=float(np.max(np.abs(r)))
    if scale<=1e-12:raise ValueError('A nonzero reference scale is required')
    return float(np.max(np.abs(m-r))/scale)

# Gates compared with published data must say where the data came from.
REFERENCE_DATA=('pptc','reference')

def evaluate(data):
    kind=data['benchmark']; checks={}; tolerances={}
    def check(name,error,tolerance):checks[name]=error;tolerances[name]=tolerance
    if kind=='pipe':
        reference=pipe_profile(data['radius_m'],data['pipe_radius_m'],data['mean_velocity_m_s'])
        check('velocity_profile',normalized_error(data['axial_velocity_m_s'],reference),.02)
        expected=pipe_pressure_drop(data['dynamic_viscosity_pa_s'],data['measurement_length_m'],data['pipe_radius_m'],data['mean_velocity_m_s'])
        check('pressure_drop',normalized_error([data['pressure_drop_pa']],[expected]),.02)
    elif kind=='rotating_cylinders':
        expected=couette_profile(data['radius_m'],data['inner_radius_m'],data['outer_radius_m'],data['inner_omega_rad_s'],data.get('outer_omega_rad_s',0))
        check('tangential_velocity',normalized_error(data['tangential_velocity_m_s'],expected),.02)
    elif kind=='multi_zone_couette':
        # Concentric annuli, each driven by its own MRF zone: every zone must match its own profile.
        if len(data['zones'])<2:raise ValueError('At least two rotating zones are required')
        for zone in data['zones']:
            expected=couette_profile(zone['radius_m'],zone['inner_radius_m'],zone['outer_radius_m'],zone['inner_omega_rad_s'],zone.get('outer_omega_rad_s',0))
            check(f"{zone['name']} tangential_velocity",normalized_error(zone['tangential_velocity_m_s'],expected),.02)
    elif kind=='porous_duct':
        # Fully developed duct through a porous block: the measured drop must follow the fitted curve.
        a,b=two_point_curve(data['curve'])
        if len(data['points'])<2:raise ValueError('At least two duct speeds are required')
        for point in data['points']:
            v=point['mean_velocity_m_s']
            check(f"v={v:g} pressure_drop",normalized_error([point['pressure_drop_pa']],[a*v+b*v*v]),.02)
    elif kind=='early_stop_consistency':
        # Coefficients at the early stop vs the window average of the same case run to the full budget.
        for metric,full in data['full_budget'].items():
            check(metric,normalized_error([data['early_stop'][metric]],[full]),.01)
    elif kind=='flat_plate_cf':
        expected=blasius_cf(data['x_m'],data['velocity_m_s'],data['kinematic_viscosity_m2_s'])
        check('skin_friction',normalized_error(data['cf'],expected),.05)
    elif kind=='pptc':
        if len(data['points'])<3:raise ValueError('At least three open-water operating points are required')
        for point in data['points']:
            for metric in ('kt','kq'):
                check(f"J={point['advance_ratio']} {metric}",normalized_error([point[metric]],[point['reference_'+metric]]),.1)
    elif kind=='mesh_study':
        if len(data['resolutions'])!=3:raise ValueError('Provide three meshes in increasing resolution order')
        for metric in data['metrics']:
            check(metric,normalized_error([data['resolutions'][-2][metric]],[data['resolutions'][-1][metric]]),.05)
    elif kind=='reference':
        # Cited experimental or high-fidelity values with a per-metric tolerance fixed in the gate
        # definition (benchmarks/README.md), e.g. cylinder_re100 St 3 %, Cd 5 %.
        if not data.get('metrics'):raise ValueError('Provide at least one metric')
        for metric,item in data['metrics'].items():
            check(metric,normalized_error([item['measured']],[item['reference']]),float(item['tolerance']))
    else:raise ValueError('Unknown benchmark type')
    provenance=data.get('provenance',{})
    if kind in REFERENCE_DATA and not provenance.get('source'):raise ValueError('Reference-data gates need provenance.source (citation or dataset URL)')
    passed=all(checks[k]<=tolerances[k] for k in checks)
    single=set(tolerances.values())
    return {'gate':data.get('gate',kind),'benchmark':kind,'status':'passed' if passed else 'failed','passed':passed,
            'tolerance':single.pop() if len(single)==1 else None,'tolerances':tolerances,'normalized_errors':checks,
            'provenance':provenance,'scope':'Agreement with supplied measurements and conditions; does not certify other cases'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('measurements',type=Path);parser.add_argument('--output',type=Path);args=parser.parse_args()
    result=evaluate(json.loads(args.measurements.read_text(encoding='utf-8')));text=json.dumps(result,indent=2)
    if args.output:args.output.write_text(text+'\n',encoding='utf-8',newline='\n')
    print(text);raise SystemExit(0 if result['passed'] else 1)

if __name__=='__main__':main()
