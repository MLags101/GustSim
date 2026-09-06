import csv
import html
import io
import json
import os
from pathlib import Path
import zipfile
import h5py
import numpy as np
from . import config

FIELD_UNITS={'pressure_pa':'Pa gauge','p':'m^2/s^2','U':'m/s','k':'m^2/s^2','omega':'1/s','nut':'m^2/s','yPlus':'1','vorticity':'1/s'}

def manifest(run):
    return {'run_id':run['id'],'created':run['created'],'status':run['status'],'configuration':run['spec'],
            'openfoam_version':config.FOAM_VERSION,'quality':run['result'],
            'conventions':{'units':'SI','raw_pressure':'kinematic','derived_pressure':'gauge Pa','absolute_pressure':'pressure_pa + fluid.reference_pressure_pa',
              'frame':'global right-handed Cartesian','iteration':'SIMPLE iteration, not seconds','rotor_torque':'required shaft torque, positive in commanded rotation direction',
              'point_fields':'interpolated when derived from cell fields','hdf5_connectivity':'zero-based indices; offsets have length n_cells+1','field_units':FIELD_UNITS}}

def write_hdf5(source,target,run):
    with np.load(source,allow_pickle=False) as fields, h5py.File(target,'w') as h:
        h.attrs['manifest_json']=json.dumps(manifest(run))
        h.attrs['schema_version']=1
        for name in fields.files:
            if '__' in name:
                association,field=name.split('__',1)
                ds=h.create_dataset(f'fields/{association}/{field}',data=fields[name],compression='gzip')
                ds.attrs['association']=association
                ds.attrs['units']=FIELD_UNITS.get(field,'see OpenFOAM field dimensions')
            else:
                ds=h.create_dataset('mesh/'+name,data=fields[name],compression='gzip')
                if name=='points':ds.attrs['units']='m'

def report_html(run):
    result=run['result'];esc=html.escape
    metrics=''.join(f'<tr><td>{esc(k)}</td><td>{esc(str(v)) if v is not None else "Unavailable"}</td></tr>' for k,v in result.get('metrics',{}).items())
    findings=''.join(f'<li><strong>{esc(f["status"])} · {esc(f["code"])}</strong>: {esc(f["detail"])}</li>' for f in result.get('findings',[]))
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>GustSim report</title>
<style>body{{font:16px/1.6 system-ui;max-width:950px;margin:50px auto;padding:20px;color:#182c38}}h1{{font-weight:500}}table{{border-collapse:collapse;width:100%}}td{{padding:8px;border-bottom:1px solid #ccd6df}}pre{{white-space:pre-wrap;background:#f1f5f8;padding:20px}}strong{{color:#205b61}}</style>
<h1>{esc(run['spec'].get('name','GustSim'))}</h1><p>Run {esc(run['id'])} · OpenFOAM {config.FOAM_VERSION}</p>
<p>Execution: {esc(run['status'])}. Numerical quality: {esc(result.get('numerical_status','unavailable'))}.
Experimental template validation: pending.</p><h2>Quality findings</h2><ul>{findings}</ul><h2>Engineering quantities</h2><table>{metrics}</table>
<h2>Reproducible configuration and conventions</h2><pre>{esc(json.dumps(manifest(run),indent=2))}</pre></html>'''

def export_run(run,kind):
    root=config.DATA/'runs'/run['id'];out=config.DATA/'exports';out.mkdir(parents=True,exist_ok=True)
    result=root/'results'
    if kind=='hdf5':
        if not (result/'fields.npz').exists():raise ValueError('Solved volume fields are unavailable')
        target=out/(run['id']+'.h5');tmp=target.with_suffix('.'+os.urandom(4).hex()+'.tmp')
        write_hdf5(result/'fields.npz',tmp,run);tmp.replace(target);return target
    if kind in ('png','vtk','paraview'):
        path=result/{'png':'view.png','vtk':'volume.vtu','paraview':'view.pvsm'}[kind]
        if not path.exists():raise ValueError('This result artifact has not been generated')
        return path
    if kind=='report':
        target=out/(run['id']+'-report.html');target.write_text(report_html(run),encoding='utf-8');return target
    if kind=='csv':
        if not run['result'].get('history'):raise ValueError('No force history was recorded')
        target=out/(run['id']+'-loads.csv')
        with target.open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f);w.writerow(['iteration','Fx_N','Fy_N','Fz_N','Mx_Nm','My_Nm','Mz_Nm'])
            for row in run['result']['history']:w.writerow([row['iteration'],*row['force'],*row['moment']])
        return target
    if kind not in ('case','bundle'):raise ValueError('Unknown export format')
    if not (root/'simulation.json').exists():raise ValueError('The case has not been generated yet')
    target=out/(run['id']+'-'+kind+'.zip');tmp=target.with_suffix('.'+os.urandom(4).hex()+'.tmp')
    with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as z:
        for path in root.rglob('*'):
            if path.is_file() and not path.is_symlink():z.write(path,'case/'+path.relative_to(root).as_posix())
        z.writestr('manifest.json',json.dumps(manifest(run),indent=2))
        z.writestr('report.html',report_html(run))
        example_root=Path(__file__).with_name('resources')
        for name in ('read_results.py','read_results.m'):
            if (example_root/name).exists():z.write(example_root/name,'examples/'+name)
        if kind=='bundle' and (result/'fields.npz').exists():
            hdf=export_run(run,'hdf5');z.write(hdf,'fields.h5')
        script=Path(__file__).with_name('postprocess.py');z.write(script,'postprocess.py')
    tmp.replace(target);return target
