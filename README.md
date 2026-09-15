# GustSim

A local browser workbench for STEP/STL preparation, single-phase air/water CFD setup, OpenFOAM v2606 execution, and ParaView results. The API and worker run in Linux containers; Windows needs Docker Desktop with WSL2. Ubuntu needs Docker Engine and the Compose plugin.

## Supported workflows

| Workflow | Setup | Automatic analysis |
|---|---|---|
| Pipe flow | One prepared passage; flow/speed or inlet-total/outlet-static pressures | Volume/mass flow, static pressure drop, total-pressure and head loss, mass balance |
| Object moving through fluid | Stationary body with opposite air/water flow; arbitrary travel/lift directions | Drag/lift, confirmed-reference coefficients, force histories |
| Stationary assembly with rotor | Selected assembly components; one MRF region; signed RPM | Thrust, fluid/driving torque, shaft power, load histories |

All three allow air/water selection and provide pressure surfaces and velocity slices from solved fields. Supported physics is **steady, incompressible, single-phase air/water**, with laminar or k–ω SST turbulence. This is development software; benchmark accuracy and experimental acceptance remain pending.

## Run on a new machine

Copy or clone this entire source directory, then run from its root:

```sh
docker compose up
```

The first launch builds both images and downloads their dependencies. Open **http://localhost:8080** after the API is healthy. No host Python, Node, OpenFOAM, ParaView, absolute filesystem paths, or cloud account are required. Use `docker compose up -d` for background operation. After source changes, run `docker compose up --build`.

Both containers share the `gustsim_gustsim-data` Docker volume. Active CFD cases stay on the Linux filesystem. `docker compose down` preserves this volume; **`docker compose down -v` deletes it**. The worker runs as an unprivileged user with one job at a time; do not scale the worker service. No Docker socket is mounted into the application.

The default resource budget is intended for a 32 GB workstation: API up to 6 GB, worker up to 22 GB, process-tree soft ceiling 20 GB, and six available CPU cores with four solver ranks by default. Ensure Docker/WSL has sufficient memory allocated. On a smaller machine, copy `.env.example` to `.env` and lower the limits and mesh sizes. The soft worker ceiling must stay below the container memory limit. CPU and memory availability vary across machines; these are ceilings, not reservations or performance guarantees.

The application listens on loopback only. Do not expose it to a shared network until authentication and access controls are implemented. Intel/AMD Linux is the verified container target; the base solver image also publishes ARM64, but the complete CAD/ParaView stack on ARM64 is not yet tested.

## Use the workbench

Start with **New simulation** in the top bar. Name each workspace and switch between saved simulations without replacing their CAD or completed attempts. Drafts, selected mesh and solution are saved in the Docker data volume; the browser remembers the active workspace.

1. **Geometry:** export one assembly STEP with separate, named component bodies and assembled positions preserved, including the rotor. GustSim retains nested components, repeated instances and placements. STL also works with explicit source units. Search, select, hide or isolate components; face selection remains available.
2. **Setup:** choose pipe flow, an object moving through air or water, or a stationary assembly with a rotor. Preview the generated settings and confirm dimensions, fluid point, directions and selected surfaces. All settings remain editable. Older configurations load as Custom.
3. **Mesh:** generate and check the mesh here. Follow named stages, an estimated progress bar, elapsed time and logs. Switch between three sections through the model or the full mesh boundary. Thin models receive bounded local refinement before surface fitting. Inspect the actual volume-cell section, required quality checks, boundary preservation and MRF zone findings. Acknowledge actionable findings with **Review mesh & continue**. Failed or unavailable previews are explicit; requested layers are not reported as achieved layers.
4. **Run:** solve using the reviewed mesh. Geometry or physics edits invalidate it; study names and solver controls may reuse it. Every attempt preserves its specification, logs and results. Cancellation preserves available artifacts. Guided retries and parameter studies start with new mesh attempts for review before solving. Stage navigation always remains open, while blocked actions explain what needs attention.
5. **Results:** use the pressure, velocity section and streamline buttons. Toggle **Show object** for context and **Animate tracers** for playback along solved streamlines. This is visualization of steady flow, not transient CFD. Inspect measured quantities, pressure surfaces and longitudinal velocity slices generated once per guided run. Pipe results include volume/mass flow, static pressures and separate total-pressure/head loss; object results include drag/lift and their projected histories; rotor results include thrust, fluid torque, required driving torque/power and load histories. Missing evidence stays unavailable. Field-extraction failure is separate from solve success.
6. **Export:** download the raw case, histories, VTK, HDF5, ParaView state, PNG, report or complete bundle. Buttons reflect actual artifact availability. Bundles retain definitions, selected surfaces, axes, units, numerical findings and Python/MATLAB readers.

**Preparation:** Auto prepare previews conservative triangle cleanup, component-bounded welding and normal correction. Review before applying an immutable revision. **Mesh-friendly preparation** groups faces within each component to reduce tiny boundary patches for external/rotor cases. **Cleanup only** preserves individual faces for pipe inlet/outlet assignment. It does not merge touching parts, certify self-intersections or automatically close unidentified openings. For internal flow, isolate the inner walls and explicitly cap planar ports to form one connected fluid envelope. Ambiguous surface mappings require reassignment in Setup.

**Rotors:** one steady MRF cylinder encloses the selected complete rotor components. Confirm axis and origin before suggesting cylinder dimensions. Signed RPM follows the right-hand rule. The cylinder is rotating fluid, never a solid wall. Other components stay stationary. MRF predicts mean loads at a fixed rotor position, not moving blades through time.

**Layout:** drag the panel divider or focus it and use arrow keys (Home/End set 280/600 px). Sections collapse independently; boundary lists filter by component and assignment. Hide controls reclaims the navigation and setup space. Layout preferences and the active setup/mesh/solution restore locally after reload.

**Progress:** mesh percentages estimate phases, rather than predicting remaining time. Solve progress uses the actual iteration budget and can finish early on convergence. A failed job never displays 100% completion.

**Difficult assemblies:** if Auto prepare cannot close a model — parts that touch, overlap or leave gaps, which is the usual reason a real STEP assembly will not mesh — use **Close a difficult assembly** in Geometry. Wrapping builds a new closed surface around the model on a uniform grid. It is an approximation: detail finer than one cell is lost and openings narrower than the seal distance are closed. The revision is labelled as wrapped, and that label follows every run, report and export made from it. Geometry that is closed but non-manifold where parts meet no longer blocks meshing; it is reported as a review item.

**Example CAD:** `Example CAD/propeller.STEP` and `RectPrism.STEP` completed default SST meshing, short solves and result extraction after component preparation. The propeller retained its rotor surface in a 211,565-cell mesh. `VehicleAssem.STEP` imports its 43 component records as 262,436 triangles across 13,869 CAD-face patches; component grouping plus wrapping turns it into a watertight single-surface revision of 240,044 triangles across 39 patches that passes preflight validation. It has not been meshed or solved. These are runtime and geometry checks, not aerodynamic or propeller benchmarks.

**Current release status:** development software. Passing mesh/solver checks does not establish agreement with experiments. The app explicitly marks experimental template validation as pending. See [validation status](docs/VALIDATION.md) and [implementation handoff](docs/HANDOFF.md) for tested paths and remaining engineering work.

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

API documentation: http://localhost:8080/docs. CLI entry points: `gustsim import`, `gustsim defaults`, `gustsim validate`, `gustsim compile`, `gustsim run`, and `gustsim worker`. Complete cases always target **OpenCFD v2606**, not the Foundation release series.

## Numerical conventions

- SI storage; right-handed global coordinates. Incident velocity is `(V cos(alpha) cos(beta), V sin(beta), V sin(alpha) cos(beta))`.
- Constant density and viscosity; no energy equation, transition, free surfaces, cavitation dynamics, compressibility, or moving sliding meshes.
- Raw `p` is kinematic pressure. Derived `pressure_pa = density * p` is gauge pressure. Absolute pressure adds the configured reference pressure.
- Inlet pressure control means **total** pressure; outlet pressure is **static** pressure. Pressure-driven pipe presets require a positive turbulence initialization speed estimate, which does not prescribe the resulting flow.
- Pipe static pressure drop uses area means. Total-pressure loss uses volume-flux-weighted means of `rho*p + 0.5*rho*|U|²`; head loss divides that loss by `rho*9.80665`, without elevation correction. Reverse or insufficient throughflow makes derived losses unavailable; unexpected signed measurements are retained.
- Force/moment outputs are fluid loads on selected walls. Rotor torque is converted to required shaft torque about the configured rotor origin. Thrust uses `references.thrust_axis` (default −X); advance speed is minus incident velocity projected onto that axis. Reference force coefficients require nonzero freestream speed; undefined values remain null.
- The stored time coordinate of a steady run is a SIMPLE iteration, not physical seconds.

Dependencies retain their own licenses. OpenFOAM is GPL; redistribution must include its applicable notices and source-access obligations. This repository does not change upstream licenses.
