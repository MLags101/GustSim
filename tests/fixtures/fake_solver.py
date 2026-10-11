"""Stand-in for simpleFoam in executor tests: writes force and residual rows over time and
stops cleanly when the GustSim stop file appears, like the case's abort function object."""
import math
import sys
import time
from pathlib import Path

case = Path.cwd()
iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 400
delay = float(sys.argv[2]) if len(sys.argv) > 2 else 0.005
force = case / 'postProcessing/loads/0'
residuals = case / 'postProcessing/residuals/0'
force.mkdir(parents=True, exist_ok=True)
residuals.mkdir(parents=True, exist_ok=True)
with (force / 'force.dat').open('w') as f, (residuals / 'solverInfo.dat').open('w') as r:
    f.write('# Time total_x total_y total_z pressure_x pressure_y pressure_z viscous_x viscous_y viscous_z\n')
    r.write('# Time U_solver Ux_initial Ux_final Ux_iters p_solver p_initial p_final p_iters\n')
    for i in range(1, iterations + 1):
        drag = 10 + 5 * math.exp(-i / 40)
        f.write(f'{i} ({drag} 0 {0.1*drag}) ({0.8*drag} 0 0) ({0.2*drag} 0 0)\n'); f.flush()
        r.write(f'{i} smoothSolver {1e-1*math.exp(-i/50)} 1e-6 2 GAMG {2e-1*math.exp(-i/60)} 1e-6 4\n'); r.flush()
        print(f'Time = {i}', flush=True)
        if (case / 'gustsim.stop').exists():
            print('Abort: stop file found; writing and exiting', flush=True)
            break
        time.sleep(delay)
print('End', flush=True)
