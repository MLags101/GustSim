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

## Usability update and supplied CAD (2026-09-06)

- Propeller: prepared one surface while retaining the component identity; local body refinement level 7 preserves 9,823 rotor boundary faces. Required mesh checks pass at 211,565 cells. Mesh `b3a0dc35513c4f9c98b32bb00447e41e`; SST solve `74a1d6ed9311432c8295ca17e6c96acb` (40 iterations, signed −10 RPM about Y). Three model-centered sections and full boundary preview contain actual volume-mesh cells. Surface, velocity slice, 360 streamline branches with IntegrationTime, and ZIP/HDF5 export verified.
- Rectangular prism: default prepared SST mesh passes required checks at 8,555 cells. Mesh `28e7f65495ab40a69f3664799366890c`; solve `a5caedf5b1f2455082e6c6a15da70d49` (40 iterations, 0.01 m/s). Automatic views and streamline extraction complete.
- Extended mesh diagnostics remain review items: propeller has determinant, concavity and interpolation-weight findings; prism has concave-cell findings. Requested layers are not a claim of measured coverage. Both short solutions retain numerical review requirements. No experimental acceptance gate is changed.
- Vehicle STEP native import: 43 component records, 262,436 triangles, 13,869 CAD face patches. Conservative repair and component grouping reduce this to 42 nonempty surfaces and remove duplicate/degenerate triangles and open edges. 1,064 nonmanifold edges remain; the geometry stays blocked. No vehicle solve or accuracy claim is made.
- Browser testing caught and fixed a mesh identity bug: JavaScript serializes negative zero as zero. Configuration identity now canonicalizes signed zero so a browser roundtrip does not invalidate a compatible mesh. Old acknowledgments may need a one-time review after this change.

- Current automated checks: 49 pytest tests pass, including air/water in all presets, local refinement, component preparation, independent saved projects, progress calculations and signed-zero compatibility. TypeScript and the production Vite build pass.
- Browser checks: empty new workspace, saved-project switching and reload recovery; reviewed mesh enables solving; water setup from +Y generates a reviewable preview; streamline/object visibility, side camera, tracer playback and Hide/Show controls work. Cube extracts contain 288 streamline branches, and all four mesh previews plus ZIP/HDF5 export were checked against actual artifacts.

## Geometry wrapping and the graded geometry gate (2026-09-15)

`backend/gustsim/wrap.py` adds a shrink-wrap fallback for CAD that conservative repair
cannot close, and the geometry gate is now graded rather than binary.

**What changed in the gate.** A surface that is *closed but non-manifold* — the normal
state of an assembly whose parts touch — is now a review finding instead of a hard
error, because snappyHexMesh builds a valid mesh from it. Not-closed, degenerate
triangles and inconsistent normals still block. `geometry_ready` now means "nothing
blocks meshing", not "every check is a pass".

**Measured wrap accuracy** against analytic volumes, at 128–160 cells across the longest
side (`tests/test_wrap.py`):

| Case | Exact volume | Wrapped | Error |
|---|---|---|---|
| Sphere r = 0.5 m | 0.5236 m³ | 0.5312 m³ | 1.4 % |
| Unit cube | 1.0 m³ | 1.037 m³ | 3.7 % |
| Two interpenetrating unit cubes | 1.5 m³ | 1.519 m³ | 1.3 % |
| Two touching unit cubes | 2.0 m³ | 2.075 m³ | 3.7 % |

The wrap consistently **overestimates** volume; the error is a voxel-scale surface
offset and falls with finer resolution. These are geometric checks only. **No wrapped
geometry has been solved or compared against any measurement, and wrapping does not
change the experimental validation status, which remains pending.**

**Supplied vehicle CAD.** `Example CAD/VehicleAssem.STEP` (34 MB, 43 components) imports
as 262,436 triangles across 13,869 CAD-face patches, and is blocked by `closed_surface`
and `triangles`. With component grouping plus wrapping at 256 cells it becomes a
watertight, zero-non-manifold, single-component surface of 240,044 triangles across 39
component patches on a 2.12 mm grid, and passes `validate` with no errors
(`geometry_ready: true`). Import took 54 s and wrapping 23 s on the Windows development
host.

**This is a geometry result only.** The vehicle has not been meshed or solved here: that
needs the Linux worker with OpenFOAM, which was not run for this change. Treat "passes
validation" as "no longer blocked before meshing", not as a completed CFD case.

**Honesty plumbing.** A wrapped revision carries `geometry_fidelity: "wrapped"` plus the
grid size. That label reaches the validation warnings, the stage findings, the model
summary in the UI, `manifest()` (so HDF5 attributes and the bundle), and a banner in the
exported HTML report. Loads computed on a wrap are loads on an approximation, and every
artifact says so.

## Agent tools and near-wall layer fit (2026-09-22)

The coding-agent tools are covered by `tests/test_agent.py` against the public API with the worker offline. That test imports STL, builds a preset, refuses to queue a mesh while the worker is offline, refuses a layer stack that cannot fit until the finding is acknowledged, compiles a case, and exports it. It does not run OpenFOAM and does not change experimental validation.

Laminar first-cell thickness now uses the Blasius skin friction. A requested prism stack thicker than the castellated surface cell is a preflight review finding (`validation.layer_plan`). That finding is a geometric estimate. Achieved layer coverage is still whatever snappyHexMesh reports after meshing. No acceptance gate above was executed.
