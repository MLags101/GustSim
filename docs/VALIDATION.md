# Verification status

Status recorded during implementation on Windows using Docker Desktop's Linux engine. This is a development build, not an experimentally validated engineering release.

## Passed checks

- 37 pytest tests: STL units/transforms and immutable originals; STEP import scale/face groups; inner-wall isolation and planar caps; conservative repair; boundary mapping and dictionary safety; pressure conversion; incident-flow axes; MRF region geometry and signed RPM; Mach checks; atomic queue claim/recovery; cancellation of a silent process; CSV-force parsing; HDF5 round trip; API case generation; local-origin protection; saved setups; analytic reference formulas; backup/restore and archive traversal rejection; reviewed-mesh reuse across JSON serialization and rejection after physics changes.
- Added guided regression coverage: nested/repeated/transformed STEP instances with duplicate names; component-bounded preparation and provenance; all presets and both pipe-driving families; static negative RPM; mesh identity/review and missing preview evidence; incomplete draft readiness; pressure/loss conversion and reverse flow; force-axis projection and torque-origin shifts; idempotent views, artifact availability, planar outlet selection, and mesh-first guided retries/studies.
- TypeScript compilation and Vite production build.
- Linux **internal cylinder**: closed-fluid-envelope meshing, explicit checkMesh quality gate, laminar solve, pressure-drop extraction, surface/volume fields, PNG and ParaView state.
- Linux **MRF rotating sphere** with two MPI ranks: rotating cell-zone selection, signed rotation, decomposition, solve/reconstruction, force/torque extraction and postprocessing.
- Linux **external sphere, k–ω SST** with two MPI ranks and two requested prism layers: meshing, turbulence fields, parallel solve/reconstruction, y+ output and postprocessing.
- Linux external laminar sphere solved; its initial ParaView integration failures were fixed and the existing solution reprocessed successfully.
- Seven ParaView extraction modes on actual solved fields: surface/vorticity, slice, clip, streamlines, glyphs, line and probe.
- Actual solved-case exports: HDF5, CSV, VTU, PNG, HTML report and ZIP bundle.
- Default production Compose stack built and started against a new named volume, with healthy API/CAD and worker services on loopback port 8080. Through HTTP: create geometry → validate → mesh preview → reuse reviewed mesh → two-rank solve → line extraction → sampled CSV → full bundle with HDF5 and packaged Python/MATLAB readers. Verified SSE completion and mesh-reuse events. Evidence run: `29f56c58282140d08c3add00e39cad87`, mesh: `4927ea55ef304572a9a3d358869ecd9e` (2026-09-05). Numerical status remains `review_required` as expected for a coarse 40-iteration smoke test.

The smoke runs intentionally use coarse meshes and 40 SIMPLE iterations to test the execution pipeline. They retain `review_required` when convergence or extended diagnostics require attention. They are **not measurements of the plan's accuracy targets**, and the MRF sphere is not a propeller-performance validation case.

## Guided workflow runtime evidence (2026-09-06)

All four public-API workflows completed meshing, explicit checks, mesh review, two-rank solving, two automatic ParaView views and ZIP/HDF5 export on the pinned OpenCFD v2606 image:

| Case | Mesh | Solve |
|---|---|---|
| Flow-driven pipe | `a8771b842f1940c09ce991ee3f3b8e3c` | `c8965ff869464057a4f5394b345bf1aa` |
| Pressure-driven pipe | `4d557339e0644f7abd35428d58145d85` | `409738fae7ef451db7f2688a8b514b0c` |
| Still-air rotor, negative RPM | `d854de44f3e44479b7656b8287767509` | `754c2a29fa334f8595257535a98db1a3` |
| External aerodynamics | `e4e138c37c5c4064af66c8d29ccaafc2` | `95112a6414a0441d9e07729bbdd65ba9` |

The initial still-air rotor attempt failed inside freestreamPressure. Its record and logs remain preserved (`ac77498105d748d7954cdf2fc77ce665`). Open ambient total-pressure boundaries fixed the zero-velocity division; both isolated Linux and public-API reruns passed. Two-rank SST and nonzero-inflow MRF smoke tests also completed after the case/worker changes.

Interactive browser QA completed conservative preparation of a sphere, guided external setup, current-mesh preview and review, two-rank solve (`9263c03c…`), automatic pressure/velocity views and report download. Keyboard panel bounds (280–600 px), hidden navigation, and restored setup/mesh after opening a fresh tab were checked. Coarse-run residual and stability failures remain visible as numerical review findings. Nested STEP import through the public API, duplicate-name component selection/isolation, preparation identity, rotor cylinder overlays, collapsed boundary controls, and warning acknowledgment persistence/invalidation were also checked in the browser. The in-app browser automation file chooser timed out, so automated file-picker interaction remains unverified; the same STEP file imported successfully through the public multipart API. This is interaction evidence, not an automated cross-browser acceptance suite.

## Pending release gates

| Gate | Requirement | Status |
|---|---|---|
| Laminar pipe | Velocity profile and developed-section pressure drop ≤2% from analytic reference | Not executed |
| Rotating cylinders | Tangential velocity profile ≤2% from analytic reference | Not executed |
| NACA 0012 | Matched-condition pressure/lift/drag reference comparison | Not executed |
| Potsdam VP1304 | Thrust and torque ≤10% from measured data at three nonzero operating points | Not executed |
| Three-grid study | Primary load change <5% between finest two meshes | Not executed |
| Domain sensitivity | Documented sensitivity of primary outputs to larger domain | Not executed |
| Browser regression automation | Import → prepare → configure → mesh → solve → inspect → export, interruption and reload paths | Interactive guided journey checked; automated regression suite pending |
| ARM64 deployment | Complete CAD + CFD + ParaView Compose stack | Not tested |

See `benchmarks/README.md` for evidence requirements. Keep numerical-quality findings separate from these release gates. The evaluator's passing unit tests only verify formulas and acceptance logic.

## Reproduce runtime checks

For all three guided presets (including both pipe driving modes), run `python scripts/verify_guided.py`. This preserves four labelled mesh/solve pairs, automatic view jobs and exports in the normal local volume.

To check a normally running Compose installation through its public API, run `python scripts/verify_compose.py` on a host with Python, or use the development override and run `docker compose -f compose.yaml -f compose.dev.yaml exec api python /app/scripts/verify_compose.py http://127.0.0.1:8000`. This creates labelled, small smoke runs in the normal workspace and preserves their results. The script needs only Python's standard library.

Use the development Compose override so scripts and Python sources are mounted. Stop the ordinary worker while invoking the smoke runner directly; use a separate temporary data directory to avoid claiming user jobs:

```sh
docker compose stop worker
docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps -e GUSTSIM_DATA=/tmp/gustsim-smoke worker python /app/scripts/smoke_linux.py --mode internal
docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps -e GUSTSIM_DATA=/tmp/gustsim-smoke worker python /app/scripts/smoke_linux.py --mode rotor --processes 2
docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps -e GUSTSIM_DATA=/tmp/gustsim-smoke worker python /app/scripts/smoke_linux.py --mode rotor --static --processes 2
docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps -e GUSTSIM_DATA=/tmp/gustsim-smoke worker python /app/scripts/smoke_linux.py --turbulent --processes 2
docker compose up -d worker
```

`/tmp` smoke data is removed with the disposable container. To retain diagnostic artifacts, provide a separate named test volume and point `GUSTSIM_DATA` into it. Never use the ordinary production data directory for a direct smoke runner while the queue worker is active.
