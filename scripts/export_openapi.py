"""Write the API's OpenAPI schema to src/api/openapi.json (sorted, LF) for type generation.

Run after changing Pydantic models, then `pnpm run gen:api`. tests/test_openapi_snapshot.py
fails while the committed schema is stale.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from gustsim.api import app  # noqa: E402

TARGET = ROOT/'src'/'api'/'openapi.json'


def schema_text():
    return json.dumps(app.openapi(), indent=1, sort_keys=True, ensure_ascii=False) + '\n'


if __name__ == '__main__':
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with TARGET.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(schema_text())
    print(f'Wrote {TARGET.relative_to(ROOT)}')
