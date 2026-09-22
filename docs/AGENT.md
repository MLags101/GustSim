# Driving GustSim from a coding agent

The workbench can be configured and run by an agent through the same HTTP API the browser uses. The agent tools do not write solver dictionaries themselves, do not overwrite an earlier attempt, and do not treat a finished job as an experimentally validated result.

Supported physics is steady, incompressible, single-phase air or water, laminar or k–ω SST, with at most one MRF region. Transient, compressible, multiphase, free-surface, cavitation, transition, and sliding-mesh requests are outside this solver.

## Start the service

From the project root:

```sh
docker compose up
```

The API listens on `http://127.0.0.1:8080`. Override it with `GUSTSIM_URL` when the tools run somewhere else.

## Tools

Two entry points call the same operations:

```sh
gustsim agent tools
gustsim agent call gustsim_status
gustsim agent call gustsim_validate -
gustsim mcp
```

`gustsim mcp` speaks MCP on stdin and stdout for an agent that connects to local tools. A browser agent attached to the open page gets a smaller set (`configure_gustsim_preset`, `queue_gustsim_mesh`, `review_gustsim_mesh`, `queue_gustsim_solve`, and the read/validate tools) that drives the workspace on screen. Both stop for review findings until `acknowledge_warnings` is true.

An MCP client entry looks like this, using the installed `gustsim` command:

```json
{
  "mcpServers": {
    "gustsim": {
      "command": "gustsim",
      "args": ["mcp"],
      "env": {"GUSTSIM_URL": "http://127.0.0.1:8080"}
    }
  }
}
```

From a checkout without the command on `PATH`: `PYTHONPATH=backend python -m gustsim mcp`.

## Order of operations

1. `gustsim_status`. Mesh and solve need `service.worker.online`. Compiling a case does not.
2. Import a STEP or STL (`gustsim_import_geometry`) or create a primitive. STEP carries its own units. STL needs `units`.
3. Prepare a new revision. `cleanup` keeps faces for a pipe inlet and outlet. `component` welds each component for external flow and rotors. `wrap` is a labelled approximation for an assembly that will not close; say so wherever its loads are quoted. The original revision stays.
4. `gustsim_preset` with `pipe`, `aerodynamics`, or `propeller`. The spec comes back unconfirmed. Read `review`, `report.findings`, and `layer_plan`.
5. Set `spec.use_case.confirmed` to true only after the dimensions, fluid point, and directions are acceptable. `gustsim_validate` again.
6. If `layer_plan.fits` is false, lower `spec.mesh.layers` or refine the surface. The feasible count is how many layers fit in the castellated cell. Achieved coverage is still measured after meshing.
7. `gustsim_queue_mesh`. When the reply has `needs_acknowledgement`, read the findings and call again with `acknowledge_warnings: true`.
8. `gustsim_run` with `wait_s` until `still_running` is false.
9. `gustsim_review_mesh`. Extended findings use the same acknowledgement rule. Review records a decision; it does not improve the mesh.
10. `gustsim_queue_solve` with that mesh id and the same spec. A geometry or physics edit invalidates the mesh.
11. Read `evidence` on the solve. `execution`, `numerical_quality`, and `experimental_validation` are separate. Experimental validation stays `experimental_validation_pending`.
12. `gustsim_export` writes `case`, `bundle`, `hdf5`, `vtk`, `csv`, `png`, `paraview`, or `report` to a path on this machine. Buttons and this tool only offer artifacts the run actually produced.

`gustsim_cancel` stops a queued or running attempt and keeps what was already written. A retry is a new run.

## Evidence

Quote a result with the state that produced it. A queued job has no loads. A completed job with `numerical_quality` of `review_required` has not passed the residual, balance, and load-stability checks. Neither state is a comparison against a measurement. The laminar-pipe, rotating-cylinder, NACA 0012, Potsdam propeller, and mesh-sensitivity gates in `benchmarks/README.md` are still unexecuted.

Pressure in the raw case is kinematic. Derived pascal values are gauge, `density × p`. Rotor thrust follows the stored thrust axis. Driving torque is positive in the commanded RPM direction. Pipe total-pressure loss and static pressure drop are different numbers.
