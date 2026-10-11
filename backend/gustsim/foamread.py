"""Reader-safe view of an OpenFOAM case for VTK's vtkOpenFOAMReader.

Standard library only: imported by the in-process package builder and, by path, by the pvbatch
scripts (postpackage.py, postprocess.py), whose Python does not have gustsim installed.

VTK infers the type of a `uniform ( ... )` list from its first token. The assert-enabled VTK 9.5
of the pinned worker image treats a leading integer as a mesh label and aborts on a value below
-1 (`GetLabelValue: Assertion result >= -1`). OpenFOAM writes -20.0 as `-20`, so a freestream of
20 m/s along -X with floating-point noise, `uniform (-20 0 -2.449293598e-15)`, kills the reader
(found on the v2606 worker; release VTK 9.6 builds read the same file correctly).

The case itself is never modified. When a field in the time being read contains such a vector,
the reader opens a shadow case under results/.vtk_reader_case: every entry is linked to the
original, and only the affected field files are copied with the leading integer written as a
float literal (`-20` -> `-20.0`, the same value). Callers remove the shadow with cleanup()
once the data is in memory.
"""
import os
import re
import shutil
from pathlib import Path

RISKY = re.compile(rb'(uniform\s*\(\s*)(-(?:[2-9]|1\d)\d*)(?=\s)')
SHADOW = Path('results')/f'.vtk_reader_case_{os.getpid()}'
FOAM = 'gustsim.foam'


def is_time(name):
    try:
        float(name)
        return True
    except ValueError:
        return False


def latest_time(case):
    times = [p.name for p in Path(case).iterdir() if p.is_dir() and is_time(p.name)]
    return max(times, key=float) if times else None


def safe_bytes(data):
    """The file content with each risky leading integer written as a float literal."""
    return RISKY.sub(lambda m: m.group(1) + m.group(2) + b'.0', data)


def _link(source, target):
    try:
        os.symlink(source, target, target_is_directory=source.is_dir())
    except (OSError, NotImplementedError):  # e.g. Windows without symlink rights (tests)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)


def cleanup(case):
    """Remove this process's shadow case (it links back into the case; never export it)."""
    shutil.rmtree(Path(case)/SHADOW, ignore_errors=True)


def foam_file(case, times=None):
    """The .foam file VTK should open for `times` (default: the latest time directory)."""
    case = Path(case)
    times = [str(t) for t in times] if times else [latest_time(case)]
    risky = {}
    for name in times:
        folder = case/name if name else None
        if folder is None or not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.is_file():
                data = path.read_bytes()
                if RISKY.search(data):
                    risky[path] = data
    if not risky:
        return case/FOAM
    shadow = case/SHADOW
    shutil.rmtree(shadow, ignore_errors=True)
    shadow.mkdir(parents=True)
    for entry in case.iterdir():
        if entry.name == 'results' or entry.name.startswith('processor'):
            continue
        target = shadow/entry.name
        if entry.is_dir() and any(path.parent == entry for path in risky):
            target.mkdir()
            for path in entry.iterdir():
                if path in risky:
                    (target/path.name).write_bytes(safe_bytes(risky[path]))
                else:
                    _link(path, target/path.name)
        else:
            _link(entry, target)
    if not (shadow/FOAM).exists():
        (shadow/FOAM).touch()
    return shadow/FOAM
