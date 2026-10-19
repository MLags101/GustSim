# Driving GustSim from an agent (MCP)

New to this? Start with the step-by-step [MCP quick start](MCP_QUICKSTART.md).

GustSim exposes its HTTP API as MCP tools. A coding agent or an AI design agent can then import geometry, set up and queue CFD, and read the loads back. The tools never write solver dictionaries themselves, never overwrite an earlier attempt, and never treat a finished job as an experimentally validated result.

**Core physics** is steady, incompressible, single-phase air or water, laminar or k–ω SST, one MRF rotor, and force-based early stopping.

**Experimental capabilities** need `acknowledge_warnings: true` and are labelled `physics_maturity: experimental` in every artifact:
- several rotors
- a ground plane with wheels
- porous radiators
- openings
- transient flow and sliding mesh
- adjoint design advice and morphing
- calm-water free surface
- adaptive refinement

`gustsim_status` lists each capability's maturity and benchmark gates.

**Not supported:** compressible, cavitating, transition and 6-DoF predictions.

## 1. Start the service

```sh
docker compose up
```

The API listens on `http://127.0.0.1:8080`. Set `GUSTSIM_URL` when it runs somewhere else.

## 2. Connect an MCP client

The MCP server is a small stdio process on the host. It imports only the Python standard library (3.11 or newer), so a plain checkout is enough. The CAD, mesh and solver stack stays in the containers. Replace `C:\path\to\GustSim` with your checkout. On Ubuntu use `python3` and a POSIX path.

**Claude Code** (any project, including the robot-design repository):

```sh
claude mcp add --transport stdio --scope user --env PYTHONPATH=C:\path\to\GustSim\backend --env GUSTSIM_URL=http://127.0.0.1:8080 gustsim -- python -m gustsim mcp
```

**Claude Desktop, Cursor, or any client with a JSON config:**

```json
{
  "mcpServers": {
    "gustsim": {
      "command": "python",
      "args": ["-m", "gustsim", "mcp"],
      "env": {"PYTHONPATH": "C:\\path\\to\\GustSim\\backend", "GUSTSIM_URL": "http://127.0.0.1:8080"}
    }
  }
}
```

If GustSim is installed into an environment with `pip install -e .`, the command is simply `gustsim mcp`. To call the same tools from a shell script or a non-MCP agent:

```sh
gustsim agent tools
gustsim agent call gustsim_status
gustsim agent call gustsim_validate -
```

`gustsim agent call gustsim_validate -` reads the JSON arguments from stdin.

File paths in `gustsim_import_geometry` and `gustsim_export` are paths on the machine that runs the MCP process.

### Long waits

`wait_s` on `gustsim_simulation`, `gustsim_batch_status` and `gustsim_run` polls for up to an hour. When the client sends a `progressToken`, the server emits `notifications/progress` while it polls. A client with a short request timeout should use a small `wait_s` (30–60 s) and call again. A wait that ends early never cancels anything.

## 3. Tools

| Step | Tools |
|---|---|
| Service | `gustsim_status` |
| Geometry | `gustsim_import_geometry` (STEP, IGES, STL, OBJ, glTF/GLB), `gustsim_create_primitive`, `gustsim_prepare_geometry`, `gustsim_describe_geometry`, `gustsim_list_geometry`, `gustsim_detect_rotor` |
| Setup | `gustsim_preset`, `gustsim_validate`, `gustsim_estimate` |
| Simulations (pipeline) | `gustsim_simulate`, `gustsim_simulation`, `gustsim_continue_simulation`, `gustsim_cancel_simulation`, `gustsim_list_simulations` |
| Results | `gustsim_results`, `gustsim_compare`, `gustsim_export` |
| Studies | `gustsim_batch`, `gustsim_batch_status` |
| Low-level attempts | `gustsim_queue_mesh`, `gustsim_review_mesh`, `gustsim_queue_solve`, `gustsim_compile_case`, `gustsim_run`, `gustsim_cancel`, `gustsim_list_runs` |

**Replies:**
- A tool reply is JSON with `ok`. When `ok` is false the MCP result has `isError: true`.
- `needs_acknowledgement: true` means the setup or mesh has review findings. Read them, decide, then call again with `acknowledge_warnings: true`.

## 4. The design loop

1. **Check the service.** Call `gustsim_status`. Meshing and solving need the compute lane online. A simulation queued while the worker is offline waits for it.
2. **Bring in the design.** Use `gustsim_import_geometry` with the file your CAD step wrote. Mesh formats need `units`. Every design change is a new immutable revision, so import each iteration rather than editing one in place.
3. **Prepare it.**
   - `component` welds each body into one surface, for external flow and rotors.
   - `cleanup` keeps faces, for a duct's inlet and outlet.
   - `wrap` is a labelled approximation for an assembly that will not close. Quote loads on a wrap as loads on the approximation.
4. **Build the setup.** Call `gustsim_preset` with `aerodynamics`, `propeller` or `pipe`. Give the travel `direction`, `speed`, `fluid` (water for marine robots) and a `tier` (`basic` to explore, `regular` or `advanced` to confirm).
5. **Read the preset's evidence.** Read `review`, `report.findings` and `layer_plan`.
   - If `layer_plan.fits` is false, lower `spec.mesh.layers` or refine the surface.
   - Set `spec.use_case.confirmed` to true only once the dimensions, fluid point and directions are right.
   - Set the robot's body axes, `spec.references.object_forward` and `spec.references.object_up`, so loads come back in its frame. They default to the travel direction and +z.
6. **Budget the run.** Call `gustsim_estimate` for cells, memory and minutes at each tier. `feasible: false` means the worker memory limit would be exceeded.
7. **Start it.** Call `gustsim_simulate`. The pipeline meshes, then decides:
   - A mesh with no findings is reviewed automatically and the simulation goes on to solve and build the results package.
   - A mesh with findings stops at `awaiting_review`. Read the findings with `gustsim_run` on `current_run_id`, then call `gustsim_continue_simulation`.
8. **Wait.** Call `gustsim_simulation` with `wait_s` until `status` is `completed`, `failed`, `cancelled` or `awaiting_review`.
9. **Read the loads.** Call `gustsim_results` on `results_run_id`. Quote the loads together with `evidence` and `status_block`.
10. **Iterate.** Change the geometry or setup, start a new simulation, and rank designs with `gustsim_compare`. Check `comparable` and `warnings` before ranking: different references, tiers, wrapped geometry or numerical findings make deltas partly non-design.

## 5. Robot-design recipes

**Body drag and moments (ground robot, drone airframe, ROV or AUV hull)**
- Use the `aerodynamics` preset, with `fluid: water` for a marine vehicle.
- In `gustsim_results`, `forces.frames.object.total` gives force and moment in body axes, each with pressure and viscous parts. `forces.coefficients` gives Cd, Cl, Cs, CdA and L/D. `forces.parts` gives per-part loads when `references.force_groups` is set.
- `forces.speed_curve` is an extrapolation at a constant drag area, not a simulation. Run a speed sweep to measure Reynolds-number effects.

**Attitude and crosswind maps**
- Use `gustsim_batch` with `kind: sweep` or `grid` over `flow.alpha_deg`, `flow.beta_deg` or `environment.yaw_deg`.
- The wake refinement follows the flow direction, so each angle meshes separately.
- A speed sweep shares one mesh and is cheap.

**Propeller or thruster maps**
1. Call `gustsim_detect_rotor` with the blade surfaces and the physical `rpm`. It returns `preset_axis`, `preset_rpm` and `preset_origin`, oriented so thrust comes out positive.
2. Pass those values to the `propeller` preset.
3. Run `gustsim_batch` with `kind: grid` over `rotation.rpm` and `flow.speed`. `results.elements.rotating_zones` reports thrust, torque, shaft power, Ct, Cq, advance ratio and efficiency.
- Steady MRF at zero inflow (hover) is poorly posed and needs many iterations. A small axial speed settles faster.
- A second rotor is experimental.

**Comparing candidate designs**
- Candidates that keep the same surface names and fit the base domain can be one `gustsim_batch` with `kind: variants` and `{"geometry_id": ...}` overrides.
- Otherwise give each candidate its own preset and simulation, then use `gustsim_compare` on their `results_run_id`s.

**Near the ground**
- `ground` in the preset (`static`, `moving` or `slip`) and rotating wheels are experimental. They need acknowledgement and are labelled in every artifact.

**What GustSim does not do:** it does not couple loads to motion. Feed the steady loads and coefficients into your own dynamics model. There is no 6-DoF, no free-flight trim, and no compressible flow (keep air below about Mach 0.3; `estimated_mach` is reported).

## 6. Evidence and conventions

Quote a result together with the state that produced it:

- **Status states are separate.** `execution`, `numerical_quality` and `experimental_validation` are reported separately. `status_block` adds `geometry` (`imported`, `wrapped` or `morphed`) and `physics_maturity`.
- **Unconverged runs.** `numerical_quality: review_required` means the residual, balance or load-stability checks did not pass. A bluff body whose wake sheds vortices may never settle in a steady solve. Treat its loads as indicative, not as a single number.
- **Validation is pending.** Experimental validation stays `experimental_validation_pending` until the benchmark gates in `benchmarks/README.md` pass. A completed run is not a measurement.
- **Insights are heuristics.** `gustsim_results` with `include_insights: true` returns rule-based heuristics. They never change any status.

Conventions:
- **Units.** All values are SI.
- **Pressure.** Pressure in the raw case is kinematic. Derived pascal values are gauge, `density × p`.
- **Object frame.** x is `object_forward`, z is `object_up`, and y completes the right-handed set. Roll, pitch and yaw moments are about those axes through `references.origin`.
- **Rotors.** Rotor thrust follows the stored thrust axis. Driving torque is positive in the commanded RPM direction.
- **Averaging.** Loads are means over the averaging window reported in `forces.averaging`.
- **Pipes.** Pipe total-pressure loss and static pressure drop are different numbers.

## 7. Low-level attempts

`gustsim_queue_mesh` → `gustsim_run` → `gustsim_review_mesh` → `gustsim_queue_solve` drives single attempts outside the simulations pipeline. The same acknowledgement rules apply. A geometry or physics change invalidates a mesh, but a v2 mesh can be reused across speed, RPM, fluid and turbulence-model changes.

`gustsim_compile_case` writes the OpenFOAM case without the worker.

`gustsim_export` writes `case`, `bundle`, `hdf5`, `vtk`, `csv`, `png`, `paraview` or `report`. It only offers artifacts the run actually produced.

`gustsim_cancel` and `gustsim_cancel_simulation` keep what was already written. A retry is a new run.
