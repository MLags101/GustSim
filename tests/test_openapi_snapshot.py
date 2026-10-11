"""The committed OpenAPI schema (source of the frontend's generated types) must match the API."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_committed_openapi_matches_api():
    spec = importlib.util.spec_from_file_location('export_openapi', ROOT/'scripts'/'export_openapi.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    committed = (ROOT/'src'/'api'/'openapi.json').read_text(encoding='utf-8')
    assert committed == module.schema_text(), 'Run python scripts/export_openapi.py and pnpm run gen:api'
