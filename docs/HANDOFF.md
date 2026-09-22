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
