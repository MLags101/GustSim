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
| Early-stop consistency | Early-stopped coefficients within 1% of the full-budget window average | **Passed 2026-10-13** (`benchmarks/results/early_stop_consistency.json`). Two full-budget cases, replayed with `scripts/early_stop_gate.py`: drone propeller in an 8 m/s climb (thrust 0.027 %, torque 0.011 %, would stop at 540 of 2000) and Ahmed body 25° (drag 0.063 %, lift 0.085 %, would stop at 760 of 2000). Two non-converging runs (hover propeller, sphere) never trigger a stop. Early stopping is now core and on by default in the setup page. |
| Flat-plate Cf | Laminar Cf within 5% of Blasius | Not executed |
| Multi-zone Couette | Each MRF zone's profile within 2% | Not executed |
| Porous duct | Δp within 2% of the two-point curve | Not executed |
| Fackrell wheel / DrivAer ground | Wheel and moving-ground loads against cited data | Not executed |
| Adjoint FD / morph verification | Sensitivity sign and magnitude; morphed-shape check run | Not executed (capability compiled; smoke not run) |
| Cylinder Re 100 / square cylinder | Strouhal and Cd | Not executed (transient implemented; `scripts/benchmark_cylinder_re100.py` defines the Re 100 case, first attempt interrupted) |
| Hydrostatic box / DTC resistance | Free-surface pressure and resistance | Not executed (interFoam path compiled; smoke not run) |
| Browser regression automation | Import → prepare → configure → mesh → solve → inspect → export, interruption and reload paths | Playwright journey against a local API with the worker offline (see below); real-stack run pending |
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

## AirShaper-style upgrade, milestones M0–M4 (2026-10-09)

This change adds:
- spec v2 with its element compilers
- lane workers and migrations
- the results package
- forces, insights, compare and the PDF report
- the new React UI

It was first checked on Windows without Docker, then on the same host with Docker Desktop (Linux engine, pinned OpenCFD v2606 worker image).

### Automated checks

- **pytest:** 179 tests. Coverage includes:
  - v1 compiled-case goldens, byte-identical to the pre-upgrade compiler, plus v2 goldens
  - migration backup, and refusal of a newer schema
  - lane isolation and job-handler registration when the worker runs as `__main__`
  - the convergence detector and retry rules
  - force frames
  - insights on synthetic VTK cases
  - compare
  - PDF text
  - element compilation: ground, wheel ω = V/r with the contact point moving at belt speed, multiple zones, Darcy–Forchheimer fit and effective thickness, openings
  - the reader-safe case view
  - pre-pipeline run adoption
  - the OpenAPI snapshot
  - benchmark evaluators and the gate-status contract
- **Frontend:** `tsc -b`, `eslint src`, 15 Vitest tests and `vite build`. `docker build --target web-check .` passes, covering API type drift, lint, types and unit tests.
- **Playwright:** 5 tests (Chromium, swiftshader WebGL) against a local API with the worker offline:
  - dashboard
  - results tabs on fixture VTK data
  - sample → setup → launch
  - setup elements (moving ground, ride height, radiator fit)
  - theme

### Runtime checks on the pinned v2606 image (Docker Desktop, 2026-10-09)

- **`scripts/probe_runtime.py`:** every program the capabilities need is present. That includes `adjointOptimisationFoam`, `pimpleFoam`, `interFoam`, `mapFields` and `ffmpeg`.
- **`scripts/smoke_linux.py` matrix,** all passing on a coarse mesh:
  - v1 regression: internal, rotor (2 ranks), static rotor with 4 automatic views, SST (2 ranks)
  - v2 steady core: `--v2`, `--parallel-mesh --processes 2`, `--potential`, `--robust`, `--early-stop`, `--fo-probe --turbulent`
  - elements: `--onground moving`, `--wheels`, `--multi-mrf`, `--porous` (free-standing and `--mode internal` duct), `--openings`
  - Every v2 case also built the pvbatch results package and the PDF report. A typical package had 21–22 layers: surface, friction lines, Cpt = 0 cloud, Q, 14 rakes and section stacks.
  - Missing layers carry their reason, e.g. "laminar runs have no turbulence to estimate noise from".
  - The early-stop case stopped on force convergence after 240 iterations of a 3,000 cap, averaged over 240–305.
  - Wheel detection found the smoke wheel from all four cues. The compiled ground moves at −V and the wheel turns at ω = V/r.
  - The flow-rate opening's measured inlet flux matched the commanded flow rate.
  - Each MRF zone in the two-zone case reported its own loads.
- **Production stack (`docker compose up`) on the user's existing `gustsim-data` volume:**
  - First, a full volume backup was taken with the pre-upgrade image and checked with gzip and tar listing. It is kept outside git under `release/volume-backups/`.
  - The API then migrated the database from schema v0 to v6. A verified SQLite backup and sidecar are in `backups/schema/`, `integrity_check` passed, and all 93 earlier runs are intact (87 completed, 6 failed).
  - `scripts/verify_compose.py` passed: mesh `801a7b97…`, two-rank solve `e0995cbb…`, extraction and bundle.
  - `scripts/verify_guided.py` passed all four guided workflows with automatic views.
  - New `scripts/verify_pipeline.py` passed: simulation `7335f132…`, solve `b89095b8…`. It ran:
    1. sample sphere
    2. preset, validate and launch
    3. mesh (16.6k cells) and review
    4. SST solve, stopped by the force-convergence monitor after 390 of 600 iterations
    5. package with 22 layers, 5 rule-based insights, and a 329 KB PDF
  - The new results UI rendered that run's real fields: the Cp surface, insight pins, and evidence badges with numerical quality "review required" and validation "pending". The browser console had no errors.
  - One pre-upgrade run (`95112a64…`) was given a modern results package on request. It has 19 layers; layers whose fields the old solve did not write are listed as missing.

The coarse runs are execution evidence only. For example, the 16.6k-cell sphere gives Cd ≈ 0.36 and Cl ≈ 0.15. A sphere at Re ≈ 2.7×10⁵ has Cd ≈ 0.47 and no lift, so these numbers are a mesh-resolution artefact, not a prediction.

### Defects found by the runtime checks and fixed

1. **Post-lane jobs never ran in production.**
   - Cause: `python -m gustsim.worker` loads the worker as `__main__`, while `gustsim.jobs` registered its handlers on the importable module.
   - Symptom: the post lane failed every results package with "Unknown job kind package". The smoke runner hid this because it imports the handlers in-process.
   - Fix: `__main__` now delegates to `gustsim.worker`, and a regression test covers it.
   - A failed package now shows as a failed simulation (it previously showed "completed"). Retrying it is parented to the solved run.
2. **pvbatch aborted while reading some solved cases.**
   - Cause: the pinned image's VTK 9.5 has assertions enabled. VTK infers a `uniform ( … )` list's type from its first token, so OpenFOAM's `uniform (-20 0 -2.449293598e-15)` (−X freestream at an integer speed, with floating-point noise) fails `GetLabelValue: result >= -1`. Release VTK 9.6 reads the same file correctly.
   - Fix: `foamread.py` gives the reader a shadow case. Only the affected field files are copied, with `-20` written as `-20.0`. The solved case is never modified, and the shadow is removed after reading.
   - This also protects the ParaView view extractions used by v1 runs.
3. **pvbatch occasionally segfaults on teardown** (one package in about 20).
   - Fix: a crash after `layers.json` is written keeps the complete package. An earlier crash is retried once and the first log is kept.
4. **Radiators were far too weak, then too strong.**
   - Cause of the weakness: with a sideways resistance of 1000× the through-flow resistance, `explicitPorositySource` (trace on the diagonal) converged at about 1/2001 per iteration, so after 600 iterations the flow through the radiator was still 0.79 of the freestream.
   - Fix: the default factor is now 10 (minimum 2).
   - On the mesh, the zone takes whole cells, so its thickness differs from the box. Coefficients are now sized for the effective thickness, which is exact for axis-aligned radiators.
   - The jump across a sharp porous zone on the collocated grid converges with resolution. In a slip-wall duct with a linear curve the measured drop was 21% high with 6 cell layers, 8% with 11 and 2.4% with 21. Radiators are now refined to at least 8 layers (measured 7.9% high with 10 layers, for both a linear and a mixed curve). A radiator left with fewer layers by the refinement cap gets a setup warning.
   - The ≤2% `porous_duct` gate needs about 20 layers and a mesh study.
5. **Multi-zone and duct smoke geometries were too small for the coarse smoke mesh.** The guards correctly blocked them, with an empty zone and a missing boundary respectively. The smoke geometry was fixed; the guards were not weakened.
6. **Pre-upgrade runs were invisible in the new dashboard.**
   - Fix: solve runs made outside the pipeline are listed as adopted simulations that only reference them; the run records are unchanged. They open in legacy mode (pressure surface) and can be given a results package on request.
7. **Element results reported unphysical values.** They listed wind-tunnel faces as openings with pre-element boundary kinds, and an extra rotor showed an efficiency of 2.6 at near-zero shaft power. Openings are now model surfaces with their compiled kinds, and efficiency is reported only when 0 < η ≤ 1.

### Rerun the runtime checks

Stop the ordinary worker first, as for the earlier smoke runs:

```sh
docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps -e GUSTSIM_DATA=/tmp/gustsim-smoke worker python /app/scripts/smoke_linux.py --early-stop
docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps -e GUSTSIM_DATA=/tmp/gustsim-smoke worker python /app/scripts/smoke_linux.py --wheels
docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps -e GUSTSIM_DATA=/tmp/gustsim-smoke worker python /app/scripts/smoke_linux.py --porous --mode internal --iterations 400
python scripts/verify_pipeline.py http://localhost:8080
```

Other flags are listed by `smoke_linux.py --help`. `--compile-only` validates and writes a case on any OS without OpenFOAM.

### Still not done

- **Benchmarks:** every gate in the table above is "Not executed"; all new physics stays experimental.
- **Adaptive mesh refinement:** compiled. The Advanced tier requests one cycle. An indicator surface is queued after a solve, the next mesh refines near it, and `mapFields` initialises the new solve. The mesh-study gate is not executed.
- **Later milestones:** free surface and sliding-mesh AMI (M7) are compiled and labelled experimental. Their gates and Linux smokes have not been run. Design advice (M5) and transient flow (M6) are implemented; their gates are not executed either.

Insights are rule-based heuristics and never change any status.

## Milestone M6: transient flow (2026-10-09)

A transient simulation first solves steadily, with the usual convergence monitor. The same case then switches to `pimpleFoam` with these settings (`backend/gustsim/transient.py`):
- backward time differencing
- PIMPLE in PISO mode
- an adaptive time step bounded by the Courant number
- `fieldAverage` over the averaging window
- a `surfaces` function object that writes animation frames: the object surface (pressure) and a section through the model (velocity)

The statistics in `results/transient.json` are:
- window means and standard deviations of the loads
- a Welch spectrum of the cross-flow force, the dominant frequency and the Strouhal number
- statistical convergence: the two halves of the window agree within 1%, with at least 5 shedding periods when the signal is periodic

The results package is built on the time-averaged fields, plus packed animation frames. The UI has:
- a transient option in setup
- an animation player in the Flow tab
- a "Transient loads" card in the Forces tab (history with the averaging window, spectrum, Strouhal number)

The PDF has a transient page. Transient is **experimental**. A transient rotor uses the sliding-mesh option; without it, rotating parts are rejected. Scale-resolving DES is rejected at setup.

### Checks

**pytest:** coverage includes:
- the plan and dictionaries
- statistics on a known signal (mean, standard deviation, a 10 Hz peak, St, and an incomplete run)
- the analysis window and checks
- the package on time-averaged fields, with packed frames
- the PDF transient page
- the restart time state
- monitor history across restart segments

**Pinned v2606 image, coarse smoke cases.** These are execution evidence only:
- `smoke_linux.py --transient` (serial) ran `pimpleFoam` from the steady solution to the full 18 s. It wrote `UMean`/`pMean`, 8 frames (`object.vtp`, `section.vtp`) and both animation layers, with the package on the time-averaged basis. The first time step was 0.107 s against a planned 0.09 s. Statistics were reported as "review": only 2.6 shedding periods fell in the window.
- `smoke_linux.py --transient --early-stop --processes 2`: the steady stage stopped on force convergence at iteration 140, then 102 transient steps ran to the full 18 s.
- The production stack ran `verify_pipeline.py --transient`: simulation `43a74ab2…`, solve `62407732…`. It produced the package with both animation layers, 5 insights and the PDF. The run is labelled experimental, with statistics "review" (2.5 periods). The Flow tab played the section and surface animations from real frames, and the Forces tab showed the transient card. The browser console had no errors.

### Defects found by the real runs and fixed

1. **The steady time state was restored on restart.** `pimpleFoam` restored `uniform/time` from the last steady iteration, i.e. Δt = 1 and the iteration index. With a nonzero index it skipped its Courant-based initial step. The first step was then 1 s long (Courant number about 3,600), or the run ended at once when the duration was under 0.5 s. The steady state is now set aside as `uniform/time.steady` (kept, not deleted) in the case and every processor directory.
2. **Monitor history read only the newest restart segment.** The convergence chart of a transient run therefore showed the transient samples. `monitor.history` now reads every segment.
3. **Display fixes.** The steady chart and the force caption labelled the transient window as iterations; they now show the steady initialisation and the time window in seconds.

### `cylinder_re100` gate

`scripts/benchmark_cylinder_re100.py` defines the gate case:
- laminar, quasi-2D cylinder at Re = 100
- one-diameter span between symmetry planes
- 15D upstream, 25D downstream and ±15D laterally (3.3% blockage)
- D/h = 32 at the wall
- 600 s simulated, about 15 periods, averaged after 35%

It compares St with 0.1643 (Williamson 1996; 3% tolerance) and mean Cd with 1.33 (Park, Kwon & Choi 1998; 5% tolerance) through the benchmark evaluator, and writes `benchmarks/results/cylinder_re100.json` whatever the outcome. To allow the quasi-2D section, validation now accepts a body that passes through symmetry faces, with a warning that loads are per modelled span.

The first attempt was cut off when the session ended, before any result was written. **The gate is still "Not executed".**

## Milestone M5: design advice and morphing (2026-10-11)

Adjoint design advice and shape morphing are compiled and wired into the steady pipeline. They are experimental. A finished steady external solve can run `adjointOptimisationFoam` for a face sensitivity map (laminar, or frozen k–ω SST). Design boxes queue a separate morphing attempt that does not overwrite that solution; the cycle before the objective rises is stored as a `morphed` geometry revision and a check simulation is queued.

`tests/test_adjoint.py` covers the dictionaries, the restrictions (no rotating region, radiators, openings, wheels, transient or free surface), parking the sensitivity onto the primal time, the one-millimetre insight, and idempotent morph enqueue. It does not run OpenFOAM.

`scripts/smoke_linux.py --adjoint` and `--morph` are the execution checks on the pinned v2606 image. **They have not been run for this change.** The `adjoint_fd` and `morph_verification` gates remain "Not executed". A sensitivity map is a linear adjoint prediction, not a finite-difference check and not an experimental result.
