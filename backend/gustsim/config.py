import os
from pathlib import Path

DATA = Path(os.environ.get("GUSTSIM_DATA", "data")).resolve()
FOAM_VERSION = "2606"
MAX_UPLOAD = 100 * 1024 * 1024
MEMORY_GB = float(os.environ.get("GUSTSIM_MEMORY_GB", "20"))

def initialize():
    for name in ("geometry", "runs", "exports"):
        (DATA / name).mkdir(parents=True, exist_ok=True)
