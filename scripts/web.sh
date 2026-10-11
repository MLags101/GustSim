#!/bin/sh
# Frontend dev server against a Compose API on localhost:8080.
# From the repo root: sh scripts/web.sh
cd "$(dirname "$0")/.." || exit 1
if ! command -v pnpm >/dev/null 2>&1; then
  echo "pnpm is not on PATH. Use: docker compose -f compose.yaml -f compose.dev.yaml --profile web up --build" >&2
  exit 1
fi
GUSTSIM_API="${GUSTSIM_API:-http://127.0.0.1:8080}"
export GUSTSIM_API
exec pnpm dev
