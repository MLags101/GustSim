"""What the compute worker can use: CPU cores (physical, logical, container quota) and the MPI
process count GustSim recommends for a solve. Reported once at worker start-up with the runtime
capabilities; the setup page sizes new simulations from it.

CFD scales with physical cores (and memory bandwidth), not hyper-threads, so the recommendation
is the physical cores the container may use, minus the cores reserved for the post lane.
"""
import os
import re
from pathlib import Path

MAX_PROCESSES = 32


def logical_cpus():
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def physical_cores(cpuinfo=None):
    """Distinct (physical id, core id) pairs in /proc/cpuinfo; the logical count when unknown."""
    if cpuinfo is None:
        try:
            cpuinfo = Path('/proc/cpuinfo').read_text(errors='replace')
        except OSError:
            cpuinfo = ''
    pairs = set()
    for block in cpuinfo.split('\n\n'):
        physical = re.search(r'^physical id\s*:\s*(\d+)', block, re.M)
        core = re.search(r'^core id\s*:\s*(\d+)', block, re.M)
        if physical and core:
            pairs.add((physical.group(1), core.group(1)))
    return len(pairs) or logical_cpus()


def cpu_quota(text=None):
    """CPUs allowed by the cgroup v2 quota (`cpu.max`, e.g. compose `cpus: 6`), None if unlimited."""
    if text is None:
        try:
            text = Path('/sys/fs/cgroup/cpu.max').read_text()
        except OSError:
            return None
    parts = text.split()
    if len(parts) != 2 or parts[0] == 'max':
        return None
    try:
        return float(parts[0])/float(parts[1])
    except (ValueError, ZeroDivisionError):
        return None


def recommended_processes(physical, logical, quota, post_cpus):
    usable = min(physical, logical, quota if quota else physical)
    return int(max(1, min(MAX_PROCESSES, int(usable) - post_cpus)))


def report():
    physical, logical, quota = physical_cores(), logical_cpus(), cpu_quota()
    post = int(float(os.environ.get('GUSTSIM_POST_CPUS', '1')))
    return {'physical_cores': physical, 'logical_cpus': logical, 'cpu_quota': quota, 'post_cpus': post,
            'recommended_processes': recommended_processes(physical, logical, quota, post),
            'gpu_devices': sorted(p.name for p in Path('/dev').glob('nvidia[0-9]*')) if Path('/dev').is_dir() else []}
