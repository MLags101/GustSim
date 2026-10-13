# GustSim

A local workbench for STEP assemblies, internal passages, and one steady MRF rotor, with external flow as the setup those cases share. It prepares CAD, runs steady incompressible air or water on OpenFOAM v2606, and exports a provenance-backed bundle. The API and worker run in Linux containers; Windows needs Docker Desktop with WSL2. Ubuntu needs Docker Engine and the Compose plugin. For the boundary with single-STL external aerodynamics, see [the Slipstream comparison](docs/SLIPSTREAM_COMPARISON.md).

## Supported workflows

| Workflow | Setup | Automatic analysis |
|---|---|---|
| Pipe flow | One prepared passage; flow/speed or inlet-total/outlet-static pressures | Volume/mass flow, static pressure drop, total-pressure and head loss, mass balance |
| Object moving through fluid | Stationary body with opposite air/water flow; arbitrary travel/lift directions | Drag/lift, confirmed-reference coefficients, force histories |
| Stationary assembly with rotor | Selected assembly components; one MRF region; signed RPM | Thrust, fluid/driving torque, shaft power, load histories |

All three allow air/water selection and provide pressure surfaces and velocity slices from solved fields. Core physics is **steady, incompressible, single-phase air/water**, with laminar or k–ω SST turbulence and one MRF region. Ground planes, wheels, extra rotors, radiators and openings are **experimental** until their benchmark gates pass. This is development software; benchmark accuracy and experimental acceptance remain pending.

## Run on a new machine

Copy or clone this entire source directory, then run from its root:

```sh
docker compose up
```

The first launch builds both images and downloads their dependencies. Open **http://localhost:8080** after the API is healthy. No host Python, Node, OpenFOAM, ParaView, absolute filesystem paths, or cloud account are required. Use `docker compose up -d` for background operation. After source changes, run `docker compose up --build`.

The `api`, `cad` and `worker` containers share the `gustsim_gustsim-data` Docker volume. Active CFD cases stay on the Linux filesystem. `docker compose down` preserves this volume; **`docker compose down -v` deletes it**. Jobs run in lanes so result processing never waits behind a solve. The worker handles the `compute` (OpenFOAM) and `post` (ParaView, report) lanes, and `cad` handles CAD import. Each lane runs one job at a time as an unprivileged user; do not scale these services. No Docker socket is mounted into the application.

The default resource budget is intended for a 32 GB workstation: API up to 6 GB, worker up to 22 GB, process-tree soft ceiling 20 GB, and no CPU cap: the worker reports its physical cores, and new simulations run one MPI rank per core less one, which is kept for post-processing. Set `GUSTSIM_CPUS` to cap the worker. Ensure Docker/WSL has sufficient memory allocated. On a smaller machine, copy `.env.example` to `.env` and lower the limits and mesh sizes. The soft worker ceiling must stay below the container memory limit. CPU and memory availability vary across machines; these are ceilings, not reservations or performance guarantees.

The application listens on loopback only. Do not expose it to a shared network until authentication and access controls are implemented. Intel/AMD Linux is the verified container target; the base solver image also publishes ARM64, but the complete CAD/ParaView stack on ARM64 is not yet tested.

## Use the workbench

The interface follows AirShaper's flow: a simulations dashboard, a one-page setup and tabbed results. Light, dark or system theme and display units are in the top bar. Every value is stored in SI.

1. **Simulations:** the dashboard lists every simulation. Each row shows its headline results and separate evidence columns for geometry, run, numerical quality and experimental validation. Experimental physics is flagged. Filter, tag, archive or select runs to compare; nothing is deleted. **New simulation** imports STEP, IGES, STL, OBJ or glTF (CAD import runs as a background job) or clones a sample model.
2. **Setup:** one page with six numbered sections:
   - **Model:** health checks, preparation, orientation and parts.
   - **Simulation type:** object in a flow, rotor or internal flow; air or water; ground; steady.
   - **Speed and direction.**
   - **Elements.**
   - **Accuracy:** Basic, Regular or Advanced, with cell, memory and time estimates for this computer.
   - **Review and launch.**

   The 3D view shows flow arrows, dimensions, the wind tunnel, the ground plane, radiator boxes and highlighted rotating parts. Launch stays disabled until you confirm the inferred setup and acknowledge review findings.
3. **Progress:** the simulation meshes, then checks the mesh. It continues automatically only when the mesh check has no findings; otherwise you review it. It then solves until the forces converge or the iteration budget ends, and builds the results package and a PDF report. Live residual and force charts show the convergence window. Failed stages can be retried as new attempts; earlier attempts are kept.
4. **Results:**
   - **Flow:** surface pressure and friction with friction lines, pressure (Cpt = 0) and Q clouds, a qualitative noise cloud, streamline rakes, section stacks, hover probe, camera presets and screenshots.
   - **Forces:** wind and object frames, pressure and friction split, front/rear lift, per-part breakdown, force and power vs speed under a constant-coefficient assumption.
   - **Insights:** rule-based, not AI. Separation, stagnation, suction peaks, base pressure, wake size, lift balance, y+ and unsteadiness are shown as 3D pins. They are heuristics with listed thresholds and never change a status.
   - **Compare:** synced 3D panes and a surface delta.
   - **Downloads:** raw case, logs, VTK, HDF5, bundle and PDF.

**Elements** are experimental. Each is marked in the setup, needs acknowledgement at launch and is labelled on its results:
- **Ground:** on or above a static or moving ground at a given ride height.
- **Wheels:** detected rotating wheels, rolling at ω = V/r.
- **Rotors:** additional MRF zones.
- **Radiators:** a porous zone fitted from two measured speed and pressure-drop points.
- **Openings:** inlets and outlets with a set speed, flow rate or pressure.

**Transient flow** (experimental) adds a time-accurate stage after the steady solution. Results are averaged over a window and reported with their fluctuations, the shedding frequency (Strouhal number), a load history and spectrum, and animations of surface pressure and a velocity section. Rotating parts cannot run transient yet: that needs sliding meshes.

**Design advice** (experimental) is an adjoint sensitivity map on a finished steady external solution: blue is a predicted decrease for a 1 mm inward move, red for an outward move. A design box then morphs the shape in a separate attempt and queues a check simulation of the moved shape. Laminar flow differentiates a laminar adjoint; k–ω SST keeps the frozen-viscosity assumption. Rotating regions, radiators, openings, wheels, transient flow and a free surface cannot be combined with it. The map is not a finite-difference check and not an experimental result.

**Water + air** (experimental) is a calm-water `interFoam` run with a fixed waterline. Trim and sinkage are not solved. A transient rotor can use a sliding mesh (cyclicAMI) instead of MRF. Neither has been compared with a benchmark.

**Preparation:** Auto prepare previews conservative triangle cleanup, component-bounded welding and normal correction. Review before applying an immutable revision. **Mesh-friendly preparation** groups faces within each component to reduce tiny boundary patches for external/rotor cases. **Cleanup only** preserves individual faces for pipe inlet/outlet assignment. It does not merge touching parts, certify self-intersections or automatically close unidentified openings. For internal flow, isolate the inner walls and explicitly cap planar ports to form one connected fluid envelope. Ambiguous surface mappings require reassignment in Setup.

**Rotors:** one steady MRF cylinder encloses the selected complete rotor components. Confirm axis and origin before suggesting cylinder dimensions. Signed RPM follows the right-hand rule. The cylinder is rotating fluid, never a solid wall. Other components stay stationary. MRF predicts mean loads at a fixed rotor position, not moving blades through time.

**Progress:** mesh percentages estimate phases, rather than predicting remaining time. Solve progress uses the actual iteration budget. It finishes early on force convergence only when you switch on the experimental "Stop when the forces converge" option in the Accuracy step. A failed job never displays 100% completion.

**Difficult assemblies:** if Auto prepare cannot close a model — parts that touch, overlap or leave gaps, which is the usual reason a real STEP assembly will not mesh — use **Close a difficult assembly** in Geometry. Wrapping builds a new closed surface around the model on a uniform grid. It is an approximation: detail finer than one cell is lost and openings narrower than the seal distance are closed. The revision is labelled as wrapped, and that label follows every run, report and export made from it. Geometry that is closed but non-manifold where parts meet no longer blocks meshing; it is reported as a review item.

**Example CAD:** `Example CAD/propeller.STEP` and `RectPrism.STEP` completed default SST meshing, short solves and result extraction after component preparation. The propeller retained its rotor surface in a 211,565-cell mesh. `VehicleAssem.STEP` imports its 43 component records as 262,436 triangles across 13,869 CAD-face patches; component grouping plus wrapping turns it into a watertight single-surface revision of 240,044 triangles across 39 patches that passes preflight validation. It has not been meshed or solved. These are runtime and geometry checks, not aerodynamic or propeller benchmarks.

**Current release status:** development software. Passing mesh/solver checks does not establish agreement with experiments. The app explicitly marks experimental template validation as pending. See [validation status](docs/VALIDATION.md) and [implementation handoff](docs/HANDOFF.md) for tested paths and remaining engineering work.

## Upgrade an existing installation

Stop the stack and back up the data volume as described below, then run `docker compose up --build`. The API backs up the database on its own before migrating it: a verified copy goes in `backups/schema/` inside the volume. Existing runs and their results are not rewritten. They appear on the new dashboard as earlier simulations; open one and use **Report & downloads** to build its results package (clouds, streamlines, sections) on request. Front-end development without Node on the host: `docker build --target web-check .` runs the type, lint and unit checks.

## Transfer projects and results

Copying source code does not copy a Docker data volume. To transfer existing studies, first stop both services so the database and case files are consistent:

```sh
docker compose stop
docker compose run --rm --no-deps -v .:/backup api python -m gustsim.backup create /backup/gustsim-data.tar.gz
docker compose up -d
```

Copy the source directory and `gustsim-data.tar.gz` to the destination machine. Before its first normal startup, restore into a new empty volume:

```sh
docker compose build
docker compose run --rm --no-deps -v .:/backup api python -m gustsim.backup restore /backup/gustsim-data.tar.gz
docker compose up -d
```

Restore refuses to overwrite nonempty data and rejects links and paths outside the volume. Keep the original backup until the destination is verified. Data created by native development under `data/` is separate from Compose's named volume; the same backup module can archive it using the native Python environment.

## Development

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -c constraints.txt -e '.[test,cad]'
python -m pytest -q
corepack enable
corepack prepare pnpm@11.19.0 --activate
pnpm install --frozen-lockfile
pnpm run build
```

For source changes in Docker, use `docker compose -f compose.yaml -f compose.dev.yaml up --build`. Restart the worker after changing its Python code. For web HMR, `pnpm dev` proxies `/api` to port 8000 by default; set `GUSTSIM_API=http://localhost:8080` to target the Compose API instead.

API documentation: http://localhost:8080/docs. CLI entry points: `gustsim import`, `gustsim defaults`, `gustsim validate`, `gustsim compile`, `gustsim run`, and `gustsim worker`. Coding agents use `gustsim agent` and `gustsim mcp` against this API; the workflow and the evidence rules are in [docs/AGENT.md](docs/AGENT.md). Complete cases always target **OpenCFD v2606**, not the Foundation release series.

## Numerical conventions

- SI storage; right-handed global coordinates. Incident velocity is `(V cos(alpha) cos(beta), V sin(beta), V sin(alpha) cos(beta))`.
- Constant density and viscosity in the core single-phase solver. No energy equation, transition, cavitation dynamics, or compressibility. Calm-water free surface and one sliding-mesh rotor are experimental options and are labelled as such.
- Raw `p` is kinematic pressure. Derived `pressure_pa = density * p` is gauge pressure. Absolute pressure adds the configured reference pressure.
- Inlet pressure control means **total** pressure; outlet pressure is **static** pressure. Pressure-driven pipe presets require a positive turbulence initialization speed estimate, which does not prescribe the resulting flow.
- Pipe static pressure drop uses area means. Total-pressure loss uses volume-flux-weighted means of `rho*p + 0.5*rho*|U|²`; head loss divides that loss by `rho*9.80665`, without elevation correction. Reverse or insufficient throughflow makes derived losses unavailable; unexpected signed measurements are retained.
- Force/moment outputs are fluid loads on selected walls. Rotor torque is converted to required shaft torque about the configured rotor origin. Thrust uses `references.thrust_axis` (default −X); advance speed is minus incident velocity projected onto that axis. Reference force coefficients require nonzero freestream speed; undefined values remain null.
- The stored time coordinate of a steady run is a SIMPLE iteration, not physical seconds.

Dependencies retain their own licenses. OpenFOAM is GPL; redistribution must include its applicable notices and source-access obligations. This repository does not change upstream licenses.
