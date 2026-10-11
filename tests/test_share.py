"""Offline share bundle: one HTML file embedding the app, the documents and the result files."""
import base64
import json
import re
from fastapi.testclient import TestClient
from gustsim import pipeline, share
from gustsim.api import app
from gustsim.jobs import package as package_job
from test_results_package import solved  # noqa: F401  (fixture)


def fake_dist(tmp_path):
    dist = tmp_path/'dist-share'
    (dist/'assets').mkdir(parents=True)
    (dist/'assets/index.js').write_text('console.log("app </script> inside a string");')
    (dist/'assets/style.css').write_text('body{color:red}')
    (dist/'index.html').write_text('<!doctype html><html><head><title>GustSim</title>'
                                   '<script type="module" crossorigin src="./assets/index.js"></script>'
                                   '<link rel="stylesheet" crossorigin href="./assets/style.css"></head><body><div id="root"></div></body></html>')
    return dist


def embedded(html):
    match = re.search(r'<script>window\.__GUSTSIM_SHARE__=(.*?);</script>', html, re.S)
    assert match, 'the share data script is missing'
    return json.loads(match.group(1).replace('<\\/', '</'))


def test_share_bundle_embeds_documents_and_files_and_respects_the_budget(solved, tmp_path, monkeypatch):
    g, spec, run, case = solved
    package_job.build(run['id'], in_process=True)
    monkeypatch.setattr(share, 'DIST', fake_dist(tmp_path))
    pipeline.adopt_legacy_runs()
    simulation_id = pipeline.legacy_id(run['id'])
    with TestClient(app) as client:
        response = client.get(f"/api/runs/{run['id']}/export/share")
    assert response.status_code == 200, response.text
    assert 'attachment' in response.headers['content-disposition'] and response.headers['content-type'].startswith('text/html')
    html = response.text
    assert '<script type="module">' in html and 'src="./assets' not in html and '<style>body{color:red}</style>' in html
    assert html.count('</script') == html.count('<script'), 'embedded code or data closed a script element early'
    data = embedded(html)
    assert data['schema'] == 'gustsim.share/1' and data['simulation_id'] == simulation_id and data['run_id'] == run['id']
    for key in (f'/simulations/{simulation_id}', f"/runs/{run['id']}", f"/runs/{run['id']}/results/manifest", f"/runs/{run['id']}/results/forces"):
        assert key in data['documents'], key
    manifest = data['documents'][f"/runs/{run['id']}/results/manifest"]
    surface = next(layer for layer in manifest['layers'] if layer['id'] == 'surface')
    url = f"/api/runs/{run['id']}/files/{surface['url']}"
    assert base64.b64decode(data['files'][url]) == (case/surface['url']).read_bytes()
    assert data['documents'][f'/simulations/{simulation_id}']['status_block']['validation'] == 'experimental_validation_pending'
    # A tiny budget keeps the documents but leaves files out, and says so.
    small = share.build(simulation_id, run['id'], case, data['documents'], 'Block', budget=1, dist=share.DIST)
    lean = embedded(small)
    assert not lean['files'] and any('left out' in o['reason'] for o in lean['omitted'])


def test_share_requires_the_share_build(solved, tmp_path, monkeypatch):
    g, spec, run, case = solved
    monkeypatch.setattr(share, 'DIST', tmp_path/'missing')
    with TestClient(app) as client:
        assert client.get(f"/api/runs/{run['id']}/export/share").status_code == 503
