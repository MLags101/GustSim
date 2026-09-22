# Slipstream comparison and resulting GustSim priorities

Recorded 2026-09-19. Slipstream (`OwenTWebb/slipstream`, MIT) is an independent local
OpenFOAM front end cloned alongside this project. It solves part of the same problem and has
shipped further along one axis. This document records what it does, what it does not do, and
how that changes GustSim's priorities. It supplements `HANDOFF.md`; it does not replace any
policy in `AGENTS.md`.

Nothing here relaxes the validation policy. Slipstream's published numbers are Slipstream's
evidence, not GustSim's.

## What Slipstream is

A shipped consumer product: 65 commits over roughly ten weeks, public repository with CI, MIT
license, tagged releases, Homebrew packaging and a macOS desktop app. ~5,100 lines of backend
Python, ~9,600 lines of frontend. Single stated purpose: drop in an STL, get a wind tunnel
analysis.

GustSim at the same date: 6 commits, ~3,200 lines of backend Python, ~1,300 lines of
frontend, no LICENSE, no CI. The line-count ratio inverts between the two projects, and that
is the comparison in one number. Slipstream spent its effort on the user's experience of a
narrow problem; GustSim spent its effort on the correctness substrate of a wide one.

## Differences that matter

| | GustSim | Slipstream |
|---|---|---|
| Geometry in | STEP via OCCT/XDE: nested assemblies, named components, repeated instances, preserved placements; STL too | STL only, plus import of pre-built Gmsh/Fluent/OpenFOAM volume meshes |
| Flow regimes | Pipe/internal, external object, stationary assembly with one MRF rotor | External aerodynamics only |
| Physics | Steady incompressible, air or water, laminar or k-omega SST | Incompressible, transonic (`rhoSimpleFoam`), supersonic (`rhoCentralFoam`); air |
| Rotors | MRF rotating cell zone: fluid and required shaft torque, thrust, power | Actuator disks (momentum source), automatic motor detection, trim solver for forward-flight attitude and per-motor thrust |
| Post-processing | ParaView `pvbatch`, separate Python 3.14 environment, seven extract modes | Own legacy-VTK parsers; no ParaView dependency |
| Exports | HDF5, VTU, CSV, PNG, ParaView state, HTML report, ZIP bundle with Python/MATLAB readers and provenance manifest | Viz payloads and logs; no scientific bundle |
| Deployment | Docker only, two containers, pinned v2606 digest, named volume, backup/restore CLI | Native OpenFOAM plus macOS `.app`; Docker added later |
| Validation | Five acceptance gates defined, **none executed** | Executed and published: Ahmed body +9% (mesh independent), sphere Re=1e5 -32%, sphere Re=4e6 +45%, each with a refinement sweep |

The methodological divide: GustSim holds geometry readiness, execution success, numerical
quality and experimental validation as four separate states and refuses to conflate them.
Slipstream ships the result as a feature and then measures how wrong it is. Both are honest.
Only one currently has numbers.

## Strategic conclusion

Slipstream has taken "drop in an STL, get a drag coefficient", and took it with published
comparisons against Ahmed-body and sphere measurements. GustSim will not catch that on the
external-aero axis and should not spend effort trying. That is the commodity half of GustSim's
scope, and it is now free, validated and MIT-licensed.

Slipstream structurally cannot do STEP assemblies, internal flow, MRF rotors, water, or
portable scientific output. That is GustSim's remaining reason to exist, and it is the
engineering half rather than the hobbyist half.

**GustSim's scope narrows to: real CAD assemblies, internal flow, rotors, and exportable
evidence.** External flow stays as the shared substrate that rotor and validation cases run
on. It stops being the pitch.

## Priorities

### 1. Execute one acceptance gate - laminar pipe

The single blocking item. GustSim has a unit-tested acceptance evaluator
(`backend/gustsim/benchmarks.py`) that has never evaluated a real run, and five gates in
`benchmarks/README.md` that are all unexecuted. Until one is green, every other improvement is
decoration on an unvalidated stack.

Take the laminar pipe gate first: it uses an analytic reference, needs no sourced measured
dataset, and validates the differentiator rather than the part Slipstream already covers.

Build a harness modelled on Slipstream's `examples/validation/`, which is three scripts -
`make_models.py`, `run_validation.py`, `report.py` - driving the real app through its own
public API and emitting the markdown table its `docs/VALIDATION.md` publishes. Their whole
campaign is about 45 minutes of solver time. Put the GustSim equivalent under `benchmarks/`
with versioned fixtures and committed raw output, per `benchmarks/README.md`. Do not relax a
tolerance to claim a pass.

### 2. Throughput trio

All three are already open items in `HANDOFF.md`, and all three have a working reference
implementation in the sibling checkout.

- **Parallel snappyHexMesh.** `decomposePar` -> `mpirun -np N snappyHexMesh -parallel` ->
  `reconstructParMesh`, with a serial fallback when the parallel attempt fails. Slipstream:
  `backend/app/runner.py` lines ~369-401. GustSim meshes serially in
  `backend/gustsim/worker.py:155`. The reconstructed mesh must present the same layout the
  serial path produces, so `checkMesh`, `topoSet` and the solve's own `decomposePar` are
  unaffected - this matters for MRF cell-zone selection.
- **Auto-stop on load convergence.** Slipstream judges Cd stability over a trailing window and
  stops the solve early, saving a claimed 30-40% (`backend/app/runner.py` lines ~287-326,
  `_converged`). GustSim sets `residualControl` but nothing watches the force histories, so a
  converged run burns its full iteration budget. Cheapest real win available on a
  workstation-bound tool. A run that stops early must record that it did, and must not be
  presented as having completed its requested budget.
- **`potentialFoam` initialisation**, already listed in `HANDOFF.md`.

### 3. Mesh-independence sweep as a feature, not only a gate

Release gate 5 (three-grid study, <5% change between the two finest meshes) and a usable
product feature are the same code. Slipstream implements it as
`backend/app/mesh.py:MeshSweepController` - run coarse, then medium, then fine, stop as soon as
one refinement changes Cd by less than the tolerance, and report which mesh it settled on.
Building it as a feature produces the gate evidence as a by-product.

### 4. Layer feasibility, not only layer sizing

`validation.first_layer_thickness` already sizes the first layer from a y+ target. Slipstream
additionally computes whether the requested layer count can physically fit between that first
layer and the background cell (`backend/app/foamcase.py` lines ~215-314: `layer_settings`,
`first_layer_thickness`, `bridging_layer_count`, `feasible_layer_count`,
`layer_thickness_controls`). Without that, GustSim requests counts snappy will silently miss,
and the existing achieved-layer parsing turns the shortfall into a review finding after the
fact rather than preventing it. Keep the achieved-layer reporting either way - requested layers
are still not achieved layers.

### 5. Reconsider ParaView on the common read path

Slipstream produces surface pressure, three-axis slices with on-demand plane generation, and
velocity-coloured streamlines with no ParaView at all: OpenFOAM's own `postProcess` plus
roughly 300 lines of legacy-VTK parsing in `backend/app/post.py`, served as flat arrays.

GustSim carries a full ParaView stack, a second Python 3.14 environment, an Xvfb wrapper and a
class of extraction failures already documented in `HANDOFF.md`. Keep `pvbatch` for deep
exports - HDF5, ParaView state files, glyphs, clips, probes - where it earns its weight.
Evaluate serving the three everyday views natively instead. This is an investigation, not a
committed rewrite; it would reduce image size, remove a whole runtime environment, and remove
the failure mode that already forced the "extraction failure is separate from solve success"
handling.

### 6. Frontend weight

9,600 lines against 1,300. The README describes a richer workbench than `src/` can plausibly
contain. Slipstream is evidence of what the interaction layer costs when it is the product.
Budget accordingly, but only after item 1 - UI polish must not obscure unvalidated physics.

### 7. Project hygiene

Add a LICENSE and CI. Rewrite the README around assemblies, internal flow, rotors and
exportable results, and state plainly that for single-STL external aerodynamics, Slipstream is
the better tool. A project that names its own boundary is more credible than one claiming three
workflows with zero executed gates.

## Deferred

External-object flow as a headline feature. Generic-body UI depth. Anything compressible -
Slipstream covers transonic and supersonic, and GustSim's physics contract in `AGENTS.md`
excludes them. The existing prohibitions on SaaS, billing, remote rendering, free surfaces and
transient AMI stand unchanged.

## What Slipstream would want from GustSim

Recorded for completeness; acting on it is optional and separate from the priorities above.

- STEP assembly import via OCCT/XDE - Slipstream's hard ceiling, and the most valuable
  transferable asset in this repository.
- Internal/pipe flow with total-pressure and head-loss extraction.
- MRF rotors. Actuator disks give downwash; they cannot give shaft torque or required power.
- Water as a fluid.
- The scientific export bundle with generated Python/MATLAB readers and a provenance manifest.
- Immutable geometry revisions, run snapshots, a typed spec that accepts no raw dictionary
  text, and backup/restore.
- The four-state evidence model, particularly "requested layers are not achieved layers" and
  "process exit is not convergence".

Contributing the STEP importer upstream would put the strongest part of this codebase in front
of real users quickly. It is not exclusive with continuing GustSim, and it is the piece most
worth preserving if GustSim stalls.
