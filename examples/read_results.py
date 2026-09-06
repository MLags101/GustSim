"""python read_results.py fields.h5 -- no OpenFOAM installation required."""
import json
import sys
import h5py
import numpy as np

with h5py.File(sys.argv[1]) as results:
    manifest = json.loads(results.attrs['manifest_json'])
    xyz = results['mesh/points'][:]
    cells = results['mesh/connectivity'][:]
    offsets = results['mesh/offsets'][:]
    pressure = results['fields/cell/pressure_pa'][:]
    velocity = results['fields/cell/U'][:]
    print('Run:', manifest['run_id'])
    print('Points:', xyz.shape, 'Pressure range [Pa gauge]:', pressure.min(), pressure.max())
    print('First cell vertex indices:', cells[offsets[0]:offsets[1]])
    print('Speed range [m/s]:', np.linalg.norm(velocity, axis=1).min(), np.linalg.norm(velocity, axis=1).max())
