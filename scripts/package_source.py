"""Create a portable source ZIP, excluding caches, private settings, and results."""
from pathlib import Path
import os
import zipfile

root=Path(__file__).resolve().parents[1]
excluded={'.git','.venv','node_modules','.pnpm-store','data','dist','release','__pycache__','.pytest_cache','.codex','.agents'}
target=root/'release/gustsim-source.zip';target.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
    for directory, folders, files in os.walk(root):
        folders[:] = [name for name in folders if name not in excluded and not name.endswith('.egg-info') and not (Path(directory)/name).is_symlink()]
        for name in files:
            path=Path(directory)/name
            if name=='.env' or name.endswith(('.log','.pyc','.tar.gz','.tsbuildinfo')):continue
            if path.is_file() and not path.is_symlink():archive.write(path,'GustSim/'+path.relative_to(root).as_posix())
print(target)
