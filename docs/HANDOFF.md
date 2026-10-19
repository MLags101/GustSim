# Implementation handoff

## Goal and current architecture

Build a local web CFD workbench inspired by AirShaper: neutral CAD import, guided/advanced setup, actual OpenFOAM solves, interpretable ParaView output, and portable scientific exports. The agreed initial scope is stationary external flow, simple internal passages, and one steady MRF rotor in single-phase air/water. The project started from an empty workspace.

The implementation now has a React/TypeScript/vtk.js workbench, FastAPI API, SQLite run queue, immutable geometry storage, typed case compiler, controlled Linux worker, ParaView extraction, and data export. The production contract is `docker compose up`, opening `http://localhost:8080`. See the root README for backup/restore. Keep active data in the named Linux volume, not a Windows bind mount.

## Continue on Ubuntu

1. Copy the source ZIP or repository to the Ubuntu desktop. Do not copy `.venv` or `node_modules` across platforms.
2. Install Docker Engine with its Compose plugin if not present, using [Docker's Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/). Confirm `docker compose version` and that your user can run Docker.
3. From the project root, run `docker compose up`. No native OpenFOAM, ParaView, Python, or Node install is needed to use the app.
4. Restore an existing data-volume backup before first startup if transferring studies. The source ZIP alone does not contain user data or images.
5. Open this project in Codex. Use this prompt:

> Read AGENTS.md, docs/HANDOFF.md, docs/VALIDATION.md, and benchmarks/README.md. Continue GustSim implementation from its current state. Preserve docker compose up portability, the OpenCFD v2606 solver pin, real CFD results, and explicit validation status. First verify the current Compose stack and tests, then work through the remaining engineering priorities in the handoff. Do not restart the project or replace the working architecture.

For development, `compose.dev.yaml` mounts Python source and scripts. Launch with `docker compose -f compose.yaml -f compose.dev.yaml up --build`. The API reloads Python changes; restart the worker after edits. A native frontend dev server can target Compose via `GUSTSIM_API=http://localhost:8080 pnpm dev`. Keep both development and production ports bound to loopback.

## Code map

- `backend/gustsim/models.py`: typed SI configuration, validation of identifiers/numerics/axes.
- `geometry.py`: STEP XDE import, STL grouping, immutable NPZ mesh revisions, primitive generation, planar caps, conservative repair, point containment.
- `workflow.py`, `presets.py`, `analysis.py`: shared configuration identity, structured readiness and mesh evidence, guided setup previews, signed measurements and durable automatic views.
- `validation.py` and `foam.py`: geometric/physics preflight and OpenFOAM dictionary generation. MRF and refinement cylinders are virtual regions, never solid STL walls.
- `db.py`, `api.py`, `worker.py`: durable run records, API/SSE, single worker lock, cancellation/time/memory limits, retry/recovery. `LocalExecutor` is the future remote-worker adapter boundary.
- `postprocess.py`, `mesh_preview.py`, `quality.py`, `exports.py`: actual ParaView/VTK processing, quality evidence, scientific formats, and reproducible bundles.
- `benchmarks.py`: measurement-based analytic/reference acceptance evaluator. It does not manufacture reference data or automatically certify templates.
- `src/App.tsx`, `Panels.tsx`, `Viewer.tsx`, `RunDetails.tsx`: workflow, controls, vtk.js scene, plots, logs, comparison and export UI.
- `scripts/smoke_linux.py`: real small external/internal/MRF/SST runtime checks. `scripts/check_extracts.py`: seven extracts and export checks against a solved case.
- `scripts/verify_compose.py`: complete HTTP workflow against the default stack, including reviewed-mesh reuse, parallel solve, sampled data, and scientific bundle export. It passed on the Windows Docker Desktop Linux engine. Ubuntu transfer is available for convenience; it is not required to resolve a current execution blocker.
- `Dockerfile`, `Dockerfile.worker`, `compose.yaml`: portable application and worker. `constraints.txt` pins the tested Python application stack; `pnpm-lock.yaml` pins web dependencies. Runtime provenance records Python/library versions.

## Important discoveries from real runtime testing

- The available OpenFOAM image is `opencfd/openfoam-run:2606`; the `openfoam-default:2606` tag was absent. The verified multiarchitecture digest is pinned in `Dockerfile.worker`.
- That image uses Ubuntu **26.04** and Python **3.14** for its system ParaView. The API uses Python **3.12** with CAD wheels. Keep these environments separate.
- OpenFOAM exposes `WM_PROJECT_VERSION=v2606`; normalize the leading `v` when validating it. Source its bashrc without inherited positional arguments and without `set -e`, then enable strict error handling.
- ParaView uses `FieldAssociation='Point Data'`, not `'Points'`, and reader selections must be plain lists rather than property proxy objects.
- The packaged ParaView rendering path works under Xvfb; a wrapper supplies it automatically.
- `checkMesh -allGeometry` reports concave cells even on otherwise acceptable snappy meshes. The hard gate uses explicit mesh-quality limits; extended diagnostics are preserved and cause a review finding rather than being hidden.
- Default result surfaces must select object boundary patches; extracting all regions shows the outer computational box and hides the object.

## Guided workflow implementation (2026-09-06)

The six stages are Geometry → Setup → Mesh → Run → Results → Export. Geometry preparation partitions STEP components before welding and records patch mappings on immutable revisions. Guided presets preview typed settings; prior configurations remain Custom. Mesh and selected solution references are separate, and API/worker mesh reuse shares one conservative configuration identity. Warnings are acknowledged against that identity; numerical quality and experimental validation remain separate.

The default views use the durable extraction queue and deterministic per-run IDs. Measurements use full solver reductions before display interpolation. Pipe total pressure is emitted by OpenCFD's pressure function object in pascals, then reduced with phi weights. Static rotor far-field boundaries use totalPressure / pressureInletOutletVelocity: the freestreamPressure condition divides by zero at zero freestream in the pinned v2606 runtime. Do not restore it for still-air cases.

Run `python scripts/verify_guided.py` against the local Compose app to reproduce four small reviewed-mesh workflows and check automatic views and full exports. These smoke cases are preserved in the data volume and do not meet the benchmark acceptance gates.

## Remaining engineering priorities

1. **Complete scientific validation before an engineering release.** Real runtime smoke tests pass, but the requested ≤2% pipe/Couette, NACA 0012 reference comparison, ≤10% PPTC, three-grid and domain-sensitivity acceptance campaigns are not yet executed. The app intentionally retains `experimental_validation_pending`. Add versioned authentic fixtures and measured reports under `benchmarks/`; do not relax tolerances to claim a pass.
2. **Geometry robustness.** Triangle self-intersections are reported as untested. STEP now preserves nested assembly names, repeated instance identifiers and accumulated SI placements. CAD repair beyond tessellated conservative operations and general internal-volume extraction still need expansion. Internal import is a guided isolate-inner-walls/cap-planar-openings workflow. Intersecting physical primitives are not Boolean-unioned automatically.
3. **Quality and usability depth.** Add quantitative achieved-layer coverage and y+ acceptance thresholds for each validated template, profile component selection/highlighting for very large assemblies. Domain/rotor overlays, component selection and mesh review now exist. Wall-resolved presets require dedicated validation. Automatically reconcile rotor geometry selection with disconnected bodies and overlapping stationary geometry more thoroughly.
4. **Large-data behavior.** Browser extracts have a 40 MB budget, run lists omit dense histories, and run-detail plots are bounded. Large CAD import, topology inspection, and full-volume HDF5/ZIP generation still need workload-specific memory/time profiling. Consider moving heavy CAD preparation/export jobs into the durable queue.
5. **Complete browser interaction QA.** TypeScript and production builds pass. Interactive browser QA covered preparation, a guided external-flow solve, mesh review, automatic views, export, resizing, hidden panels and reload recovery; see VALIDATION.md. Maintain a repeatable browser regression suite, including interruption/recovery paths. The optional WebMCP registry is feature-detected; it has not been validated with a supported WebMCP browser context.

Do not extend into public SaaS, billing, remote rendering, free surfaces, or transient AMI until the basic numerical acceptance work is complete. UI polish must not obscure unvalidated physics.

## Data and test safety

Native development uses `data/`; production Compose uses `gustsim_gustsim-data`. The native smoke artifacts under `data/linux-*` are development evidence and are deliberately excluded from the source ZIP. Unit tests use isolated temporary data. Backups must be created with services stopped; restore refuses nonempty targets and unsafe archive entries.

Source transfer packaging: `python scripts/package_source.py` writes `release/gustsim-source.zip`. The ZIP excludes dependencies, environments, private settings, caches and user results. `docs/VALIDATION.md` records verified checks and the difference between runtime success and benchmark acceptance.

## Latest usability work

Added JSON simulation workspaces under `GUSTSIM_DATA/projects`, use-case wizard pages, air/water in every preset, incoming-flow arrows, collapsed advanced settings, phase/iteration progress, camera buttons, three model-centered mesh sections plus boundary view, one-click field views, optional CAD overlays and steady-streamline tracer playback. No database migration or named-volume change.

`mesh_guidance.py` supplies bounded refinement around thin geometry. The supplied propeller previously vanished from a coarse surface mesh; component surface grouping plus local refinement now preserves it, with strict boundary checks intact. The supplied vehicle remains nonmanifold after conservative cleanup and requires CAD repair. See VALIDATION.md for authentic example attempts and limitations. `progress.py`, `projects.py`, `Experience.tsx` and `streamlinePlayback.ts` contain the new workflow support.

## Usability and robustness work (2026-09-15)

Focus: make the app usable on real CAD without weakening any honesty guarantee. New
module `wrap.py`; new frontend module `usePolling.ts`; new tests `test_wrap.py`,
`test_mesh_evidence.py`, `test_phase0_fixes.py` (105 backend tests pass).

**Geometry is no longer a dead end.** The geometry gate is graded instead of binary: a
closed-but-non-manifold surface — the normal state of an assembly whose parts touch —
is a review finding, because snappyHexMesh meshes it fine. `geometry_ready` now means
"nothing blocks meshing". `wrap.py` adds a voxel shrink-wrap for CAD conservative repair
cannot close: rasterize the triangle soup, flood the exterior, contour the boundary. It
tolerates open shells, non-manifold junctions and interpenetrating bodies, and refuses
rather than returning a hollow shell when it leaks. Wrapped revisions carry
`geometry_fidelity: "wrapped"` into validation, findings, the UI, `manifest()` and the
HTML report. `VehicleAssem.STEP` now reaches `geometry_ready` (see VALIDATION.md); it has
**not** been meshed or solved.

**Mesh evidence is measured, not asserted.** `workflow.mesh_metrics` parses checkMesh's
non-orthogonality, skewness, aspect ratio and negative volumes; `workflow.layer_coverage`
parses snappyHexMesh's achieved-layer table. A layer shortfall below 80% of the request is
a review finding, and hitting the cell budget is flagged — snappy stops refining there and
still reports a valid mesh. `validation.first_layer_thickness` sizes the first layer from a
y+ target via the flat-plate Cf correlation, so `wall_treatment: resolved` finally changes
the mesh instead of only a boundary condition.

**Corrected physics.** `references.area` is the true projected silhouette along the travel
direction (`geometry.projected_area`, validated against analytic boxes/spheres/cylinders to
<0.4%), not the YZ bounding-box face — Cd was wrong for any non-+X direction. Validation
warns when a confirmed area no longer matches the current direction.

**Worker/API fixes.** A converged solve is no longer marked `failed` when ParaView
extraction fails (it becomes an `unavailable` finding on a completed run). The solver time
limit is one budget for the attempt, not per stage. `keepalive()` holds the heartbeat during
long pure-Python phases, so the worker stops being declared offline while busy. SSE reads run
in a threadpool and drain events after the terminal status, so the final error is not lost.
`recover()` reclaims stranded `generating` runs. The internal-mode wake refinement box is
gone, and time directories are written binary.

## Agent tools and layer fit (2026-09-22)

`backend/gustsim/agent.py` exposes the public API as tools for a coding agent: `gustsim agent call` and `gustsim mcp` (stdio MCP). The open page registers the same gates for a browser agent. Tools queue new runs, require an explicit acknowledgement of review findings, and return execution, numerical quality, and experimental validation as separate fields. `docs/AGENT.md` is the workflow.

`validation.layer_plan` checks whether the requested prism stack fits the castellated surface cell before meshing. A stack that does not fit is a setup review finding. `validation.skin_friction` uses the Blasius local value for laminar flow and the 1/7-power local value for k–ω SST. Achieved layer coverage is unchanged: it is still parsed from the mesher log after the run.

The Slipstream comparison's laminar-pipe gate, parallel snappyHexMesh, load-based early stop, and `potentialFoam` initialisation are still open. Those last three change the pinned v2606 run and need a Linux worker before they land. The five acceptance gates remain unexecuted.

### Remaining priorities

1. **Benchmark acceptance campaign — not started.** All five gates in `benchmarks/README.md`
   remain unexecuted. This needs authentic reference data and hours of real solves; nothing
   here changes the `experimental_validation_pending` label.
2. **Meshing throughput.** No parallel snappy (`decomposePar` → `-parallel` →
   `reconstructParMesh`), no `potentialFoam` initialization, no `renumberMesh`, and no bounded
   automatic retry on a mesh-quality failure.
3. **Frontend typing.** `src/types.ts` was not written; the app is still `any` throughout, so
   `strict: true` does little. A `useReducer` refactor of `App.tsx` is still worthwhile —
   the ref-mirror and autosave races were fixed in place rather than designed out.
4. **CAD import cost.** STEP import and wrapping still run synchronously in the request
   (54 s and 23 s respectively for the supplied vehicle). Move them into the durable queue.
   Tessellation deflection is still a fixed 0.25 mm absolute regardless of part size.
5. **Results depth.** Slice planes are still a backend round-trip per adjustment; client-side
   slicing of the already-exported `volume.vtu`, and a 3D hover probe, are still open.

## AirShaper-style upgrade (2026-10-09): milestones M0–M4

The plan was to move toward AirShaper's style and capabilities while staying local. It has seven parts:
- one-page setup
- dashboard
- tabbed results
- rule-based insights
- elements
- tiers
- convergence-based stopping

Milestones M0–M6 are implemented. M5 (design advice), M7 (free surface and sliding-mesh AMI) and adaptive refinement are compiled and labelled experimental; their benchmark gates and the new Linux smokes have not been run. M8 polish covers contrast, the tour, chart data tables, reduced motion and viewer keyboard shortcuts. There are still no 3D element gizmos. See VALIDATION.md for exactly what was run: the smoke matrix on the pinned v2606 image, the production stack on an existing data volume (migration included), and the HTTP verification scripts. It also lists the seven defects those runs found and how each was fixed.

### Upgrading an existing installation

1. Stop the stack with `docker compose stop`.
2. Back up the volume with `docker compose run --rm --no-deps -v .:/backup api python -m gustsim.backup create /backup/gustsim-data.tar.gz` (see README).
3. Run `docker compose up --build`.

On startup the API migrates SQLite additively (`migrations.py`). Before migrating it takes an online backup into `DATA/backups/schema/`, checks it with `integrity_check` and writes a sha256 sidecar. It refuses to run against a newer schema. Workers wait for the schema (`require_schema`).

Old v1 runs are never rewritten. Their compiled cases stay byte-identical (`tests/golden`). Their configuration identity is unchanged unless a v2 field differs from its default.

### Backend code map (new)

**Infrastructure**

| Module | Purpose |
|---|---|
| `migrations.py` | Schema versions and verified backup |
| `db.py` | Lanes (`compute`, `post`, `cad`), claim by lane, per-lane heartbeats and recovery, `merge_result` |
| `capabilities.py` | Maturity, gates and runtime probe for each capability |
| `cadworker.py` | Async CAD import and preparation jobs |
| `jobs/` | Post-lane jobs: results `package`, then PDF `report` |
| `pipeline.py` | Simulations: mesh → automatic review only with zero findings → solve → package → report; continue and cancel |

**Mesh and solve**

| Module | Purpose |
|---|---|
| `mesh_tiers.py` | Basic, Regular and Advanced tiers resolved into numeric mesh fields |
| `estimate.py` | Cell, memory and runtime estimates, calibrated from completed runs. `wake_boxes` is shared with the compiler. |
| `convergence.py` | Force-convergence detector |
| `monitor.py` | Live monitor; writes the `gustsim.stop` file read by the `abort` function object |
| `retry.py` | Auto-retry rules; each retry is a new attempt |
| `foam_functions.py` | Output function objects |

**Results**

| Module | Purpose |
|---|---|
| `postpackage.py` | Runs under pvbatch: surface Cp/Cf, friction lines, Cpt = 0 cloud, Q, noise proxy, rakes, section stacks and renders |
| `manifest.py` | Describes the results package |
| `forces.py` | Wind and object frames, pressure/viscous split, axle split, blockage, parts, speed curve |
| `insights.py` | Versioned rules |
| `compare.py`, `batches.py` | Comparison and batch runs |
| `report_pdf.py` | matplotlib PDF report |
| `samples.py` | Sample models |

**Elements**

| Module | Purpose |
|---|---|
| `wheels.py` | Wheel detection |
| `foam_elements.py` | Compiles ground, wheels, zones, porous media and openings |
| `elements.py` | `results/elements.json` |
| `transient.py` | Transient stage: plan (duration from flow-throughs, Courant-bounded step, averaging window, frames), `pimpleFoam` dictionaries and function objects, restart time state, statistics (`results/transient.json`) |
| `adjoint.py`, `jobs/optimise.py` | Design advice: `adjointOptimisationFoam` sensitivity on the finished steady time, then a separate morphing attempt (volumetric B-splines, BFGS) that stores a `morphed` revision and queues a check simulation |
| `foamread.py` | Reader-safe shadow case for the assert-enabled VTK 9.5 in the worker image (see VALIDATION.md, defect 2); used by `postpackage.py` and `postprocess.py` |

**Spec v2** (`models.py`) adds:
- `environment`, `elements`, `convergence`, `time` and `outputs`
- `mesh.tier` and `mesh.wake_level_offset`
- `solver.numerics` and `solver.initialise`

`workflow.mesh_identity` lets physics-only changes reuse a reviewed mesh.

### Frontend code map (replaces `src/App.tsx` and friends)

- **Routing:** `src/app/` (shell, top bar, hash routes).
- **API layer:** `src/api/` (client, generated `schema.gen.ts`, TanStack Query hooks).
- **State:** `src/state/` (preferences: theme, units, colormap).
- **Shared code:** `src/ui/` (primitives), `src/lib/` (units, colormaps, evidence).
- **Viewer:** `src/viewer/` holds the vtk.js `Viewport` with layers, legend from the LUT, probe/pick, camera presets and overlays (ground, boxes, dimensions).
- **Features:** under `src/features/`:
  - `dashboard`
  - `setup`: one page, elements editors in `ElementsEditor.tsx`
  - `simulation`: progress, results tabs Flow/Forces/Insights/Compare/Other
  - `compare`
  - `settings`
  - `onboarding`
- **Browser agent:** `src/agent/BrowserTools.tsx` keeps the eight browser-agent tool names.

### Checks

- **Without Node:** `docker build --target web-check .` runs gen:api check, ESLint, `tsc` and Vitest.
- **With Node:** `pnpm check` and `pnpm e2e`. Playwright needs a local API serving `dist/`; see `playwright.config.ts`.
- **After any model or API change:** regenerate the API types with `python scripts/export_openapi.py && pnpm gen:api`. `tests/test_openapi_snapshot.py` catches drift.
- **On a Linux worker:**
  - run `python scripts/probe_runtime.py`
  - run the `smoke_linux.py` flags listed in VALIDATION.md
  - `--compile-only` checks case generation on any OS without OpenFOAM
- **Against a running stack:** `python scripts/verify_pipeline.py http://localhost:8080` drives the v2 simulations pipeline end to end: sample, preset, launch, mesh review, solve with early stop, package, insights and PDF. Run it alongside `verify_compose.py` and `verify_guided.py`.

**Pre-upgrade runs.** Solve runs made outside the pipeline (all runs from before v2, and runs queued through the run API) appear on the dashboard as adopted simulations. These are `simulations` rows whose plan has `legacy_run` and only references the run; the run record is not modified (`pipeline.adopt_legacy_runs`). They open in legacy mode, and `POST /api/runs/{id}/package` builds a modern results package for them.

### Open items in M4

- Adaptive mesh refinement is compiled. The Advanced tier requests one cycle. The mesh-study gate is not executed.
- Free surface and sliding-mesh AMI are compiled and labelled experimental. Their gates and Linux smokes have not been run.
- The element smoke runs pass on the pinned image (see VALIDATION.md); none of the element gates has been executed.
- Elements are positioned with numeric fields; there are no 3D gizmos for rotors or radiators yet.
- The offline share bundle is `GET /api/runs/{id}/export/share`. The batch UI is the setup dialog and `/batches/:id`.
- Frontend dev server: `scripts/web.ps1`, `scripts/web.sh`, or `docker compose -f compose.yaml -f compose.dev.yaml --profile web up`.
- Design advice (M5) compiles an `adjointOptimisationFoam` sensitivity map after the steady solve and, when design boxes are set, queues a separate morphing attempt. The `adjoint_fd` and `morph_verification` gates are not executed. `scripts/smoke_linux.py --adjoint` and `--morph` are the execution checks; they have not been run on the pinned image for this change.

## Usability and accuracy pass (2026-10-12)

This pass follows the user's test notes. `docs/DESIGN.md` records the AirShaper reference used for the look and workflow.

**Results UI**
- `SimulationPage` shows a slim header, a left icon rail (AirShaper section order) and a full-height content area.
- Flow and Insights are full-bleed 3D views with floating cards; the other sections scroll.
- Flow (`FlowTab.tsx`):
  - Surface and volume field pills sit at the bottom, with compact legends above them.
  - One volume field is shown at a time: streamlines, loss clouds, vortices, noise, slices or water surface.
  - View settings persist per run across sections, in a zustand store.
  - Streamlines show one seed plane (the middle one first) or all planes. Line density runs from every seed to 1 in 20.
  - Particles: several per streamline, evenly spaced in integration time on every visible rake (`viewer/playback.ts`).
- Elements appears only when a run has rotors, wheels, radiators, openings or internal-flow metrics. It now has wheel and radiator cards.
- A pre-package (legacy) run offers "Build results package" directly in the Flow view.
- The setup page puts the model first, with the step cards in a right-hand column and a step navigator.
- The dashboard header is light. Light-theme tokens follow DESIGN.md, and muted text was darkened for contrast.

**Bugs fixed**

| Symptom | Cause | Fix |
|---|---|---|
| The 3D object was missing from results | `postpackage.surface` raised `KeyError: 'alpha_water'` on every run, so no surface layer was written. `tests/test_results_package.py` would have caught it. | Added the missing key; the test now also checks `U_near_mag` |
| Results did not load the first time | The completed solve run was never refetched after the package job merged `result.package` into it | `SimulationPage` invalidates the run when the simulation's status or stage changes |
| The probe did nothing | Legacy layer ID mismatch; the first move after a pause arrives as `StartMouseMove`; only one layer was probe-able | Fixed all three |
| Results or the viewer went blank after many section switches | WebGL contexts leaked | The context is released on unmount |
| Layers loaded late were clipped | The camera clipping range was not reset | Reset after every layer load |
| Error banner stayed up | It was never cleared | Cleared at the start of each layer update |
| Animation stuck on the stored field | Frame values did not reach a late-loading animation layer, and the cached VTP kept the last frame | Values are re-applied after it loads and the stored field is restored afterwards |
| Batch members solved at the owner's speed | `pipeline.queue_solve` took the spec from a mesh borrowed from another member | A borrowed mesh no longer supplies the spec |
| The onboarding tour blocked the next page | Its overlay outlived navigation | The tour ends on route change |

**Results package**
- `U_near_mag`: the mean |U| of samples 1.5, 2 and 2.5 % of the reference length off the wall along the outward normal. Closer samples sat inside the boundary-layer gradient and showed the cell layering as bands. It appears as the Velocity surface pill.
- Streamline rakes now have 81 seeds. This applies to new packages only; rebuild a package to get it.

**Backend review fixes** (`tests/test_review_fixes.py`)
- Sliding mesh: rotor walls use `movingWallVelocity`; interface zones come from snappyHexMesh; `mergeOrSplitBaffles -split`; `pcorr`; adjustable-time writes.
- Forces: porosity is counted only in the total `loads`; part groups follow the load selection.
- interFoam: dictionaries fixed; p is not converted again (`pressure_is_kinematic` in the package plan).
- AMR: the indicator is per cell, with h = V^(1/3).
- Wheels: they spin about their own axle; rotating wheels on a static floor are rejected.
- Monitoring and numerics:
  - Propeller monitors thrust and torque.
  - Internal-flow tables use window means.
  - The pipe inlet length scale is 0.07·D_h.
  - Openings use their compiled boundary kinds.
  - Retry `changes` survive later result writes.
  - The package lookup is by parent run.
  - The robust profile always runs its second-order stage.
  - `mapFields -consistent`.
  - The free-surface half model is rejected.
  - Adjoint scheme names checked.
- **Early stopping passed its gate and is core (2026-10-13).**
  - `scripts/early_stop_gate.py` replays the live detector on full-budget force histories. The measurements are in `benchmarks/measurements/`, and `benchmarks/results/early_stop_consistency.json` passed: two cases, worst error 0.085 % against the 1 % tolerance.
  - The setup page reads the maturity from `/api/capabilities` and switches early stopping on by default; users can switch it off.
  - Tiers stay neutral, so runs created through the API without the setup page keep the model default (off).
- **AMR is still experimental** and opt-in. Tiers never switch it on.
  - Tiers no longer enable early stopping server-side either, which is why the v2 golden `external_v2_regular/system/controlDict` changed. The v1 goldens are unchanged.

**Demos** (`samples.py`, `tests/test_samples.py`)
- Seven built-in demos: streamlined body (first run), road car with wheels on a moving ground, Ahmed body, wing (planform reference area), drone propeller in an 8 m/s climb, tower in wind (transient) and radiator in a duct.
- Each ships its setup in `sample_setup` (intent and overrides), so it opens ready to launch.
- `rotors.py` with `POST /api/geometry/{id}/rotor-detect` finds a rotor's axis, centre and blade count from its symmetry. `RotorCard` uses it to set the axis automatically, so a propeller no longer needs its axis typed in.
  - The earlier propeller meshing failure came from a wrong axis, which gave a misaligned MRF cylinder and skewed faces.

**Found while running the demos on the real stack**

| Symptom | Cause | Fix |
|---|---|---|
| The sphere demo failed meshing: "Point (-0.6 -0.6 -0.6) is not inside the mesh or on a face or edge" | The preset puts the point inside the fluid a round number of model lengths from the domain corner, so it can land exactly on a background-grid vertex | `mesh_tiers.off_grid` shifts it 0.137 background cells off any (dyadically refined) grid plane. It applies to v2 specs only, so v1 identities and goldens are unchanged. Covered by `test_samples.py` |
| The propeller failed the mesh check with 5 faces at skewness 5.3 | checkMesh's generic test applies 4 to every face; the explicit limits allow 20 on boundary faces and reported none in error | `workflow.quality_limits` passes in that case only, and the finding says how many boundary faces exceeded the generic limit |
| The hover propeller diverged at iteration 453 (sigFpe in GAMG) | SIMPLEC with U 0.9 and unrelaxed p is too aggressive for MRF with no freestream | v2 MRF cases use `consistent no`, p 0.3, U 0.7 (the OpenFOAM MRF tutorial factors). The v2 rotor golden was regenerated; v1 is unchanged. The propeller demo also gets one automatic retry |
| The sphere demo ended "numerical quality: review required" | Real: vortex shedding behind a sphere at Re ≈ 1.3×10⁵. Drag swung ±30 % and lift was as large as drag, so steady RANS cannot settle | The first demo is now a streamlined body of revolution (`teardrop`), which keeps the flow attached. The sphere run's honest "review" status stands |
| The Velocity pill showed concentric bands | Sampling 0.5 % L off the wall lands inside the steep boundary-layer gradient, so the value follows the cell layering | Mean of samples at 1.5, 2 and 2.5 % L |
| The progress label promised "convergence can finish earlier" when early stopping was off | — | The label depends on `spec.convergence.enabled` |
| The propeller reported thrust −1.6 N | Rotor convention: the axis is the inflow direction and thrust is measured along −axis. Detection always chose +Z, but this rotor pushes air toward −Z | `rotors.detect` reads the blade helix sense (`handedness`, the sign of Σ (n·t)(n·a) over the blade faces). `rotors.orient` and the rotor card point the axis along the pushed air while keeping the physical spin. The card now gives the spin against the detected axis and says where thrust is reported. Covered by `test_rotors.py` and `rotor.test.ts` |
| The hover propeller's loads were still drifting after 2,000 iterations | Steady MRF at zero inflow is poorly posed: the rotor's own jet recirculates in the closed tunnel | The demo is now an 8 m/s climb (advance ratio 0.32). Hover still runs, but expect it to need more iterations |
| The wing reported Cl 2.45 | The generic preset references the frontal area (≈0.06 m²) | The wing demo uses its 0.25 m² planform and 0.25 m chord, giving Cl ≈ 0.36 |
| The hover probe never showed a value | vtk.js freezes its objects. Caching the cell index on the dataset (`poly.__gustsimCells = true`) threw, the `catch` returned no point IDs, and every probe value was `null`. GPU picking itself always worked | The cell-offset cache is a `WeakMap` beside the dataset. `e2e/journey.spec.ts` hovers the model and expects the readout |
| Solves used 4 MPI ranks on a 10-core machine | Worker capped at `cpus: 6` and a fixed default of 4 processes | `hardware.py` reports physical cores, cgroup quota and a recommendation (physical cores within the quota, minus the post lane) with the compute lane's capabilities. The setup page uses it unless Advanced sets processes. `compose.yaml` defaults to no CPU cap (`GUSTSIM_CPUS=0`) |
| `/api/capabilities` reported every gate as `not_executed` inside Docker | The images install the package into site-packages, so `capabilities.RESULTS` pointed at `/usr/local/lib/python3.12/benchmarks/results`, and `benchmarks/results` was not copied into the images | Both Dockerfiles copy `benchmarks/results` and set `GUSTSIM_BENCHMARK_RESULTS` |
| The streamlined body met every residual target at iteration 150, yet was reported as "loads did not settle" | The force detector needs 200 iterations of history before it judges anything | `Detector.accept_residual_stop`: when the solver stops on its residual targets, the loads are judged on the tail (same drift test) and that tail becomes the averaging window. Covered by `test_steady_core.py` |
| Every real mesh waited for a manual review: the wing had 4 small-determinant cells and 8 low-weight faces out of 720,143 cells | Any extended-diagnostics finding other than concave cells meant "review" | Small determinant, low interpolation weight and skewed faces are information when they affect at most 0.01 % of the cells; anything more, or any other failure, still needs review. Covered by `test_mesh_evidence.py` |

**Data:** nothing was deleted.
- The 30 old smoke-test and demo simulations in the user's volume were archived (`PATCH archived: true`); "Show archived" on the dashboard lists them again.
- Five old demo drafts were hidden with the new draft `archived` flag (the dashboard's "Hide draft" button). Opening and saving a draft unhides it.

**Dev note:** the Vite dev server sometimes reloads the page mid-request after re-optimising dependencies, which can leave a query pending. Check loading behaviour against the built `dist/`.

## MCP for design agents (2026-10-19)

The MCP server (`agent.py`, `gustsim mcp`) now drives the v2 simulations pipeline, so an external design agent can run a design loop: `gustsim_estimate`, `gustsim_simulate`, `gustsim_simulation`, `gustsim_continue_simulation`, `gustsim_results` (wind- and body-frame loads, rotor maps, convergence, evidence), `gustsim_batch`, `gustsim_batch_status`, `gustsim_compare` and `gustsim_detect_rotor` (with the `rotors.orient` axis convention).
- The physics text follows `capabilities.py`: experimental, not excluded.
- Failed tool replies set `isError`.
- `wait_s` polls send `notifications/progress` when the client passes a `progressToken`.
- `cli.py` imports the CAD and solver modules only after the `mcp` and `agent` commands, so the server runs on the host with the standard library alone. `tests/test_agent.py` checks this.
- No API, schema or case-generation change. `docs/AGENT.md` has client configuration and robot-design recipes.
- Verified with pytest and a stdio drive against a local API without a worker. No OpenFOAM solve has been run through the new tools yet. Run one against the Compose stack before relying on it.
