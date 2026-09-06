# GustSim engineering instructions

Read `docs/HANDOFF.md` and `docs/VALIDATION.md` before continuing implementation. The user wants a local, portable engineering tool that starts with **docker compose up** on Windows/WSL2 or Ubuntu. Preserve that deployment contract and the Docker named data volume.

- OpenFOAM means **OpenCFD v2606**. Do not replace it with the Foundation distribution or silently upgrade the solver. Keep the pinned container digest and regenerate/verify cases after changes.
- The supported physics is steady, incompressible, single-phase air/water with laminar or k–ω SST and one MRF region. Do not present transient, free-surface, cavitating, compressible, or transition predictions as supported.
- Never use synthetic field data or successful process exits to claim numerical or experimental validation. Keep geometry, execution, numerical quality, and experimental validation as distinct states.
- Preserve SI units, explicit pressure conversion, reference frames, cell/point associations, immutable geometry revisions and run snapshots, and raw case/log exports.
- Do not remove data volumes, user CAD, or completed cases. Back up data before any schema migration. New retry attempts must not overwrite prior results.
- All authoritative filesystem paths are runtime-relative or configured by `GUSTSIM_DATA`; do not introduce host-specific paths. Shell scripts must retain LF line endings.
- Validate changes with the relevant pytest tests, TypeScript/build checks, and real Linux smoke tests when touching case generation, worker execution, or ParaView pipelines. Smoke tests do not replace the benchmark acceptance suite.
- The frontend uses React/TypeScript/Vite/vtk.js; the API uses FastAPI/Pydantic/SQLite; the worker runs controlled subprocesses without a shell. Avoid arbitrary executable dictionary inputs.

No credentials or external cloud accounts are required. This repository has no hosted Sites project and must remain local unless the user explicitly requests deployment.
