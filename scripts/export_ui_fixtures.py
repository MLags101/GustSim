"""Cross-language fixtures for frontend tests (run after changing the backend conventions).

euler_sxyz.json: rotation matrices from trimesh (the backend's geometry.prepare convention),
so the setup page's orientation preview is tested against the exact matrices the backend uses.
"""
import json
from pathlib import Path
import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT/'src'/'test'/'fixtures'
ANGLES = [(0, 0, 0), (90, 0, 0), (0, 90, 0), (0, 0, 90), (30, 45, 60), (-20, 10, 135), (180, -90, 45), (12.5, -33.3, 271)]

if __name__ == '__main__':
    TARGET.mkdir(parents=True, exist_ok=True)
    cases = [{'degrees': list(a), 'matrix': trimesh.transformations.euler_matrix(*np.radians(a), axes='sxyz')[:3, :3].round(12).tolist()} for a in ANGLES]
    with (TARGET/'euler_sxyz.json').open('w', encoding='utf-8', newline='\n') as handle:
        json.dump(cases, handle, indent=1)
        handle.write('\n')
    print('Wrote', TARGET/'euler_sxyz.json')
