"""Offline share bundle: one self-contained HTML file with a simulation's results.

The file embeds the single-file app build (`vite build -c vite.share.config.ts` → dist-share),
the API documents the results pages read (simulation, run, manifest, forces, insights, ...) and
the result files as base64. It opens from disk in a browser with no network and no GustSim
installation, read-only. Result files are added in priority order (surface first, section stacks
last) until the size budget is reached; anything left out is listed in the file and shown as
unavailable. The embedded status block keeps execution, numerical quality, experimental
validation and physics maturity visible exactly as in the app.
"""
import base64
import json
import os
import re
import time
from pathlib import Path

DIST = Path(os.environ.get('GUSTSIM_SHARE_DIST', 'dist-share'))
BUDGET_BYTES = int(float(os.environ.get('GUSTSIM_SHARE_BUDGET_MB', '60'))*1024*1024)

# Smaller number = included first.
PRIORITY = {'surface': 0, 'friction_lines': 1, 'pressure_cloud': 2, 'isosurface': 3, 'streamlines': 4, 'animation': 5,
            'section_stack': 6}


def available():
    return (DIST/'index.html').is_file()


def application(dist=None):
    """The share build's index.html with its JS and CSS inlined."""
    dist = Path(dist or DIST)
    html = (dist/'index.html').read_text(encoding='utf-8')
    script = re.search(r'<script type="module"[^>]*src="([^"]+)"[^>]*></script>', html)
    style = re.search(r'<link rel="stylesheet"[^>]*href="([^"]+)"[^>]*>', html)
    if not script:
        raise RuntimeError('The share build has no application script; rebuild with vite.share.config.ts')
    js = (dist/script.group(1).lstrip('./')).read_text(encoding='utf-8').replace('</script', '<\\/script')
    html = html.replace(script.group(0), '<!--GUSTSIM_SHARE_DATA--><script type="module">' + js + '</script>')
    if style:
        css = (dist/style.group(1).lstrip('./')).read_text(encoding='utf-8').replace('</style', '<\\/style')
        html = html.replace(style.group(0), '<style>' + css + '</style>')
    return html


def safe_json(value):
    """JSON that cannot close the surrounding <script> element."""
    text = json.dumps(value, separators=(',', ':'), default=float)
    return text.replace('</', '<\\/').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def result_files(run_id, case, manifest):
    """(api url, path on disk, priority, label) for every result file the pages can load."""
    base = f'/api/runs/{run_id}/files/'
    out = []
    if manifest:
        for layer in manifest.get('layers', []):
            rank = PRIORITY.get(layer['id'], PRIORITY.get(layer['kind'], 7))
            if layer.get('url'):
                out.append((base + layer['url'], case/layer['url'], rank, layer['id']))
            for item in layer.get('items') or []:
                out.append((base + item['url'], case/item['url'], rank + item['index']*0.001, f"{layer['id']}[{item['index']}]"))
            if layer.get('frames'):
                out.append((base + layer['frames']['url'], case/layer['frames']['url'], rank + 0.5, layer['id'] + ' frames'))
    else:
        legacy = case/'results/surface.vtp'
        out.append((base + 'results/surface.vtp', legacy, 0, 'surface'))
    return sorted(out, key=lambda item: item[2])


def build(simulation_id, run_id, case, documents, title, budget=BUDGET_BYTES, dist=None):
    """The complete HTML document."""
    files, omitted, used = {}, [], 0
    for url, path, _, label in result_files(run_id, Path(case), documents.get(f'/runs/{run_id}/results/manifest')):
        relative = url.split('/files/', 1)[1]
        if not path.is_file():
            omitted.append({'path': relative, 'reason': f'{label} was not produced for this run'})
            continue
        size = path.stat().st_size*4//3
        if used + size > budget:
            omitted.append({'path': relative, 'reason': f'{label} was left out to keep the shared report under {budget//(1024*1024)} MB'})
            continue
        files[url] = base64.b64encode(path.read_bytes()).decode('ascii')
        used += size
    bundle = {'schema': 'gustsim.share/1', 'simulation_id': simulation_id, 'run_id': run_id, 'title': title,
              'created': time.time(), 'documents': documents, 'files': files, 'omitted': omitted}
    html = application(dist)
    html = re.sub(r'<title>[^<]*</title>', '<title>' + title.replace('<', '&lt;').replace('&', '&amp;') + ' · GustSim shared report</title>', html, count=1)
    return html.replace('<!--GUSTSIM_SHARE_DATA-->', '<script>window.__GUSTSIM_SHARE__=' + safe_json(bundle) + ';</script>', 1)
