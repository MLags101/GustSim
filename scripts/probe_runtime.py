"""Report which capabilities the worker image can run.

Run inside the worker container. Program presence comes from capabilities.probe_runtime();
function-object keywords are confirmed by `smoke_linux.py --v2 --fo-probe --early-stop`,
which this script runs when --functions is given.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from gustsim.capabilities import probe_runtime, report

parser = argparse.ArgumentParser()
parser.add_argument('--functions', action='store_true', help='Also run the function-object smoke case (needs a writable GUSTSIM_DATA)')
args = parser.parse_args()
probe = probe_runtime()
print(json.dumps(report(probe), indent=2))
missing = sorted(p for p, ok in probe['programs'].items() if not ok)
if missing:
    print('Missing programs:', ', '.join(missing))
if args.functions:
    script = Path(__file__).with_name('smoke_linux.py')
    code = subprocess.call([sys.executable, str(script), '--v2', '--turbulent', '--fo-probe', '--early-stop'])
    raise SystemExit(code)
