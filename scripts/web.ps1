# Frontend dev server against a Compose API on localhost:8080.
# From the repo root: powershell -File scripts/web.ps1
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
  Write-Error "pnpm is not on PATH. Use: docker compose -f compose.yaml -f compose.dev.yaml --profile web up --build"
  exit 1
}
if (-not $env:GUSTSIM_API) { $env:GUSTSIM_API = "http://127.0.0.1:8080" }
pnpm dev
