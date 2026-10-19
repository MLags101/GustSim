"""Tools a coding or design agent uses to configure and run GustSim through its HTTP API.

The public API stays authoritative: these tools do not write case files themselves,
do not overwrite an earlier run, and do not promote a finished job to an experimentally
validated result. Start the stack with ``docker compose up`` and point ``GUSTSIM_URL``
at it (the default is http://127.0.0.1:8080).

This module imports only the standard library, so ``gustsim mcp`` runs from a plain
checkout on the host (``PYTHONPATH=backend python -m gustsim mcp``) without the CAD, mesh
or solver dependencies; those live in the containers.
"""
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

PHYSICS = {
    "solver": "OpenCFD OpenFOAM v2606",
    "core": "Steady, incompressible, single-phase air or water, laminar or k-omega SST, with at most one MRF rotating region, "
            "and force-based early stopping.",
    "experimental": ["multiple MRF zones", "ground plane with rotating wheels", "porous radiators", "openings",
                     "transient pimpleFoam", "sliding-mesh AMI for one transient rotor", "adjoint design advice and shape morphing",
                     "calm-water interFoam", "adaptive mesh refinement"],
    "experimental_rule": "An experimental capability raises the experimental_physics review finding. It needs acknowledge_warnings, "
                         "and every artifact of that run is labelled physics_maturity experimental.",
    "not_supported": ["compressible", "cavitation", "transition", "6-DoF motion"],
    "qualitative": "Noise clouds are a Proudman estimate. Insights are rule-based heuristics and never change any status.",
    "experimental_validation": "pending",
    "evidence_states": ["geometry readiness", "execution", "numerical quality", "experimental validation"],
}

INSTRUCTIONS = (
    "GustSim configures and runs local OpenFOAM CFD for external bodies (vehicles, drones, robot bodies, hulls), "
    "internal passages, and rotors (propellers, thrusters, fans). All quantities are SI. "
    "Core physics is steady, incompressible, single-phase air or water, laminar or k-omega SST, with one MRF rotor; "
    "other capabilities are experimental and need acknowledgement. "
    "Design loop: import or prepare geometry (each change is a new immutable revision), gustsim_preset, confirm the setup, "
    "gustsim_estimate for cost, gustsim_simulate, gustsim_simulation with wait_s, then gustsim_results for body-frame loads. "
    "Use gustsim_batch for speed, angle, RPM or design-variant sweeps and gustsim_compare to rank designs. "
    "A finished run keeps execution, numerical quality, and experimental validation separate. "
    "Experimental validation is pending: a completed job is not a measurement, so quote loads with their status. "
    "Compressible, cavitation, transition and 6-DoF predictions are not supported. "
    "Retries create a new run. The service is local; start it with docker compose up."
)

VEC = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
SPEC = {"type": "object", "description": "A typed SimulationSpec, as returned by gustsim_preset."}
WAIT = {"type": "number", "minimum": 0, "maximum": 3600, "description": "Seconds to keep polling while the work is still active."}
IMPORT_SUFFIXES = {".stl", ".step", ".stp", ".iges", ".igs", ".obj", ".gltf", ".glb"}
RUN_ACTIVE = {"queued", "running", "generating"}
SIMULATION_ACTIVE = {"queued", "meshing", "solving", "postprocessing"}
STUDY_PATHS = ["flow.speed", "flow.alpha_deg", "flow.beta_deg", "environment.yaw_deg", "environment.ride_height_m",
               "rotation.rpm", "mesh.tier", "geometry_id"]


def _schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def _tool(name, description, properties, required=()):
    return {"name": name, "description": description, "inputSchema": _schema(properties, required)}


TOOLS = [
    _tool("gustsim_status", "Read service health, which worker lanes are online, the supported physics, and each capability's maturity and benchmark gates. Does not start a run.", {}),
    _tool("gustsim_list_geometry", "List imported and prepared geometry revisions.", {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
    _tool("gustsim_describe_geometry", "Read one geometry revision: surfaces, dimensions, closure, and whether it is a shrink-wrapped approximation.", {"geometry_id": {"type": "string"}}, ("geometry_id",)),
    _tool("gustsim_import_geometry", "Import a STEP, IGES, STL, OBJ or glTF/GLB file from a path on this machine (100 MB at most). STEP and IGES use the units embedded in the file. The units argument applies to mesh formats and defaults to millimetres. Creates a new geometry revision; import each design iteration as its own revision.", {
        "path": {"type": "string"},
        "units": {"type": "string", "enum": ["m", "mm", "cm", "in"]},
    }, ("path",)),
    _tool("gustsim_create_primitive", "Create a box, cylinder, tube, or sphere in metres. Useful for a pipe or a check case. role solid is a body; enclosure is a fluid envelope; refinement and rotation are not solids.", {
        "shape": {"type": "string", "enum": ["box", "cylinder", "tube", "sphere"]},
        "role": {"type": "string", "enum": ["solid", "enclosure", "refinement", "rotation"]},
        "name": {"type": "string"},
        "center": VEC, "dimensions": VEC, "axis": VEC,
        "radius": {"type": "number"}, "inner_radius": {"type": "number"}, "length": {"type": "number"},
        "parent_id": {"type": "string"},
    }, ("shape",)),
    _tool("gustsim_prepare_geometry", "Create a new prepared revision. cleanup keeps individual faces, which a pipe needs for its inlet and outlet. component welds each component into one boundary for external flow and rotors. wrap builds a labelled shrink-wrap approximation when the assembly will not close; loads on a wrap are loads on that approximation. The original revision is kept.", {
        "geometry_id": {"type": "string"},
        "mode": {"type": "string", "enum": ["cleanup", "component", "wrap"]},
        "wrap_resolution": {"type": "integer"},
        "wrap_close_gaps": {"type": "integer"},
    }, ("geometry_id", "mode")),
    _tool("gustsim_detect_rotor", "Suggest a rotor's spin axis, centre, blade radius, blade count and rotating-region size from the selected surfaces, using the blade symmetry and helix sense. A suggestion, not a measurement. Pass rpm (the physical spin about the detected axis) to get preset_axis and preset_rpm oriented so the reported thrust comes out positive.", {
        "geometry_id": {"type": "string"},
        "patches": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "rpm": {"type": "number"},
    }, ("geometry_id", "patches")),
    _tool("gustsim_preset", "Build a typed pipe, external-object, or rotor setup. Does not mesh or solve. The spec comes back with use_case.confirmed false: read the review notes, the report, and layer_plan, then set use_case.confirmed to true only when the dimensions, fluid point, and directions are acceptable. direction is the travel direction of the object through still fluid. tier basic, regular or advanced picks a resolved mesh and solver budget. A ground other than none is experimental.", {
        "geometry_id": {"type": "string"},
        "kind": {"type": "string", "enum": ["pipe", "aerodynamics", "propeller"]},
        "fluid": {"type": "string", "enum": ["air", "water"]},
        "turbulence": {"type": "string", "enum": ["kOmegaSST", "laminar"]},
        "speed": {"type": "number"}, "direction": VEC, "lift_axis": VEC,
        "inlet": {"type": "string"}, "outlet": {"type": "string"},
        "driving": {"type": "string", "enum": ["flow", "speed", "pressure"]},
        "flow_rate": {"type": "number"}, "inlet_pressure_pa": {"type": "number"}, "outlet_pressure_pa": {"type": "number"},
        "patches": {"type": "array", "items": {"type": "string"}},
        "origin": VEC, "axis": VEC, "rpm": {"type": "number"},
        "tier": {"type": "string", "enum": ["basic", "regular", "advanced", "custom"]},
        "ground": {"type": "string", "enum": ["none", "static", "moving", "slip"]},
        "ride_height_m": {"type": "number"},
    }, ("geometry_id", "kind")),
    _tool("gustsim_validate", "Run preflight on a typed spec. Reports geometry readiness, setup errors, review findings, physics maturity, the near-wall layer fit, Reynolds number, and estimated Mach. Does not queue a job. valid false means the spec will be rejected.", {"spec": SPEC}, ("spec",)),
    _tool("gustsim_estimate", "Estimate cells, memory and runtime for a spec at its own resolution and at each accuracy tier, calibrated from this computer's past runs. Use it to budget a design loop before queuing. feasible false means the worker memory limit would be exceeded.", {"spec": SPEC}, ("spec",)),
    _tool("gustsim_simulate", "Start a full simulation: mesh, automatic mesh review (only when the mesh has no findings), solve, and results package. Validates first; review findings, including experimental physics, must be read and passed back with acknowledge_warnings true. Queues even while the compute worker is offline. Returns the simulation; follow it with gustsim_simulation.", {
        "spec": SPEC,
        "acknowledge_warnings": {"type": "boolean"},
        "name": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "auto_review": {"type": "boolean", "description": "False stops after meshing for an explicit review even when the mesh has no findings."},
    }, ("spec",)),
    _tool("gustsim_simulation", "Read one simulation: status, stage, each attempt, headline loads, and the status block (geometry, execution, numerical, validation, physics maturity). wait_s polls until it is completed, failed, cancelled, or awaiting a mesh review.", {
        "simulation_id": {"type": "string"}, "wait_s": WAIT,
    }, ("simulation_id",)),
    _tool("gustsim_continue_simulation", "Continue a simulation that is awaiting a mesh review: record the review and queue the solve. Mesh findings must be read and passed back with acknowledge_warnings true. This does not change the mesh.", {
        "simulation_id": {"type": "string"}, "acknowledge_warnings": {"type": "boolean"},
    }, ("simulation_id",)),
    _tool("gustsim_cancel_simulation", "Cancel every active attempt of a simulation. Artifacts already written are kept.", {"simulation_id": {"type": "string"}}, ("simulation_id",)),
    _tool("gustsim_list_simulations", "List simulations, newest first, with headline loads and status blocks.", {
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "tag": {"type": "string"}, "status": {"type": "string"}, "query": {"type": "string"}, "archived": {"type": "boolean"},
    }),
    _tool("gustsim_results", "Read the loads of a completed solve run (a simulation's results_run_id): forces and moments in the wind frame and the object (body) frame with pressure and viscous parts, coefficients and references, per-part loads, rotor thrust, torque, power and efficiency, the convergence verdict, and the evidence states. include_insights adds the rule-based insights (heuristics, never a status).", {
        "run_id": {"type": "string"}, "include_insights": {"type": "boolean"},
    }, ("run_id",)),
    _tool("gustsim_batch", "Queue up to thirty simulations from one confirmed base spec. kind sweep varies one parameter, grid takes the product of several, and variants lists labelled overrides. Only these paths can vary: " + ", ".join(STUDY_PATHS) + ". A geometry_id variant must keep the base geometry's surface names and fit its domain. Members whose volume mesh would be identical (a speed or RPM sweep) share one mesh. Review findings on the base must be read and passed back with acknowledge_warnings true, which then acknowledges them for every member.", {
        "name": {"type": "string"},
        "base": SPEC,
        "kind": {"type": "string", "enum": ["sweep", "grid", "variants"]},
        "parameters": {"type": "array", "items": {"type": "object", "properties": {
            "path": {"type": "string", "enum": STUDY_PATHS},
            "values": {"type": "array", "items": {"type": ["number", "string"]}, "minItems": 1, "maxItems": 30}},
            "required": ["path", "values"], "additionalProperties": False}},
        "variants": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "overrides": {"type": "object"}},
            "required": ["label", "overrides"], "additionalProperties": False}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "acknowledge_warnings": {"type": "boolean"},
        "auto_review": {"type": "boolean"},
    }, ("name", "base", "kind")),
    _tool("gustsim_batch_status", "Read a batch: each member's label, varied values, status, headline loads and status block, plus the coefficient series over the first varied parameter. wait_s polls while any member is still queued or running.", {
        "batch_id": {"type": "string"}, "wait_s": WAIT,
    }, ("batch_id",)),
    _tool("gustsim_compare", "Compare two to twelve solved runs: aligned forces, moments, coefficients and rotor loads, deltas against a baseline, configuration differences, and comparability warnings. Check comparable and warnings before ranking designs.", {
        "run_ids": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 12},
        "baseline": {"type": "string"},
        "columns": {"type": "array", "items": {"type": "string"}},
    }, ("run_ids",)),
    _tool("gustsim_queue_mesh", "Low-level: queue mesh generation and the explicit mesh checks outside a simulation. Prefer gustsim_simulate. Requires a valid spec. Review findings must be passed back with acknowledge_warnings true after they have been read. The worker must be online. Returns a new run id.", {
        "spec": SPEC, "acknowledge_warnings": {"type": "boolean"},
    }, ("spec",)),
    _tool("gustsim_review_mesh", "Low-level: record that a completed mesh was reviewed. Required before gustsim_queue_solve. If extended mesh findings need attention, read them and call again with acknowledge_warnings true. This does not change the mesh.", {
        "run_id": {"type": "string"}, "acknowledge_warnings": {"type": "boolean"},
    }, ("run_id",)),
    _tool("gustsim_queue_solve", "Low-level: queue a solve on a mesh that has already been reviewed. mesh_run_id is that mesh attempt. A geometry or physics change makes the mesh unusable and this call fails. Creates a new solve run.", {
        "spec": SPEC, "mesh_run_id": {"type": "string"}, "acknowledge_warnings": {"type": "boolean"},
    }, ("spec", "mesh_run_id")),
    _tool("gustsim_compile_case", "Write the OpenFOAM case for a valid spec without meshing or solving. Works while the worker is offline. The case is a new run and can be exported with kind case.", {
        "spec": SPEC, "acknowledge_warnings": {"type": "boolean"},
    }, ("spec",)),
    _tool("gustsim_run", "Read one run (attempt). ok means the record was read, not that the CFD succeeded: use status and evidence for that. Histories are omitted. evidence keeps execution, numerical quality, and experimental validation separate. wait_s polls until the attempt leaves the queue or the time runs out.", {
        "run_id": {"type": "string"}, "wait_s": WAIT,
    }, ("run_id",)),
    _tool("gustsim_cancel", "Cancel a queued or running attempt. Artifacts already written are kept. A later retry is a new run.", {"run_id": {"type": "string"}}, ("run_id",)),
    _tool("gustsim_list_runs", "List recent runs without their histories.", {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
    _tool("gustsim_export", "Download one artifact of a run to output_path on this machine. kind is case, bundle, hdf5, vtk, csv, png, paraview, or report. Availability follows what that run actually produced.", {
        "run_id": {"type": "string"},
        "kind": {"type": "string", "enum": ["case", "bundle", "hdf5", "vtk", "csv", "png", "paraview", "report"]},
        "output_path": {"type": "string"},
    }, ("run_id", "kind", "output_path")),
]
TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}

SIMULATION_NEXT = {
    "queued": "Waiting for a worker lane. Call gustsim_simulation again with wait_s.",
    "meshing": "Meshing. Call gustsim_simulation again with wait_s.",
    "solving": "Solving. Call gustsim_simulation again with wait_s.",
    "postprocessing": "The solve finished; the results package is being built. Call gustsim_simulation again with wait_s.",
    "awaiting_review": "The mesh is waiting for a review. Read its findings with gustsim_run on current_run_id, then call gustsim_continue_simulation (acknowledge_warnings true only after reading them).",
    "completed": "Read the loads with gustsim_results on results_run_id and quote them with status_block.",
    "failed": "Read error, and gustsim_run on current_run_id. The failed attempt is kept; a retry is a new simulation.",
    "cancelled": "Cancelled. Artifacts already written are kept.",
}

CONVENTIONS = {
    "units": "SI: N, N*m, Pa, m, m/s, kg/s, W, rad/s; rpm for rotation.",
    "wind_frame": "drag along the flow, lift along references.lift_axis, side completing the right-handed set.",
    "object_frame": "Body axes: x is references.object_forward (the travel direction when unset), z is references.object_up, "
                    "y completes the right-handed set. Moments roll, pitch and yaw are about x, y and z through references.origin.",
    "pressure": "The raw case stores kinematic pressure; pascal values are gauge, density times p.",
    "rotor": "Thrust is measured along the stored thrust axis; driving torque is positive in the commanded RPM direction.",
    "averaging": "Loads are means over the averaging window reported in forces.averaging, not the last iteration.",
}


class AgentError(Exception):
    pass


class Reply:
    def __init__(self, status, body, raw=b""):
        self.status = status
        self.body = body
        self.raw = raw

    @property
    def ok(self):
        return self.status < 400


def _detail(body):
    if isinstance(body, dict):
        item = body.get("detail", body.get("error", body))
        if isinstance(item, list):
            parts = []
            for entry in item:
                parts.append(entry.get("msg", str(entry)) if isinstance(entry, dict) else str(entry))
            return "; ".join(parts)
        return str(item)
    return str(body)[:800]


def _encode_multipart(form, files):
    boundary = "----gustsim" + uuid.uuid4().hex
    chunks = []
    for key, value in (form or {}).items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    for key, (filename, content, content_type) in (files or {}).items():
        safe = Path(str(filename)).name.replace('"', "").replace("\r", "").replace("\n", "")
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{safe}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n".encode())
        chunks.append(content)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class UrllibTransport:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")

    def request(self, method, path, *, json_body=None, form=None, files=None, timeout=60, raw=False):
        headers = {}
        payload = None
        if files:
            payload, content_type = _encode_multipart(form, files)
            headers["Content-Type"] = content_type
        elif json_body is not None:
            payload = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            content = error.read()
            status = error.code
        except urllib.error.URLError as error:
            raise AgentError(f"Cannot reach GustSim at {self.base_url}. Start it with docker compose up. ({error.reason})") from error
        if raw:
            return Reply(status, None, content)
        if not content:
            return Reply(status, None)
        try:
            return Reply(status, json.loads(content.decode()))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return Reply(status, {"detail": content[:400].decode("utf-8", "replace")})


def _findings(report):
    items = report.get("findings") if isinstance(report, dict) else None
    return [{key: item.get(key) for key in ("code", "status", "stage", "detail") if item.get(key) is not None}
            for item in (items or []) if isinstance(item, dict)]


def _geometry_view(meta):
    parts = meta.get("parts") or []
    return {
        "id": meta.get("id"),
        "filename": meta.get("filename"),
        "triangles": meta.get("triangles"),
        "vertices": meta.get("vertices"),
        "bounds": meta.get("bounds"),
        "dimensions": meta.get("dimensions"),
        "units": meta.get("units"),
        "units_confirmed": meta.get("units_confirmed"),
        "geometry_fidelity": meta.get("geometry_fidelity") or "imported",
        "diagnostics": meta.get("diagnostics"),
        "patches": [{"name": patch.get("name"), "triangles": patch.get("triangles")} for patch in meta.get("patches") or []],
        "parts": [{"id": part.get("id"), "name": part.get("name"), "patches": part.get("patches")} for part in parts],
        "wrap": meta.get("wrap"),
        "parent_id": meta.get("parent_id"),
    }


def _summarize_run(run, include_spec=False):
    result = run.get("result") or {}
    metrics = {key: value for key, value in (result.get("metrics") or {}).items() if value is not None}
    mesh_findings = ((result.get("mesh_summary") or {}).get("findings")) or []
    summary = {
        "ok": True,
        "id": run.get("id"),
        "kind": run.get("kind"),
        "status": run.get("status"),
        "stage": run.get("stage"),
        "error": run.get("error"),
        "name": (run.get("spec") or {}).get("name"),
        "mode": (run.get("spec") or {}).get("mode"),
        "geometry_id": (run.get("spec") or {}).get("geometry_id"),
        "parent_id": run.get("parent_id"),
        "simulation_id": run.get("simulation_id"),
        "evidence": {
            "execution": run.get("status"),
            "termination": result.get("termination"),
            "numerical_quality": result.get("numerical_status"),
            "experimental_validation": result.get("validation_status") or "experimental_validation_pending",
            "mesh_check": result.get("mesh_check"),
            "mesh_reviewed": bool(result.get("reviewed_configuration_id")),
            "fields_available": bool(result.get("fields_available")),
            "case_available": bool(result.get("case_available")),
        },
        "metrics": metrics,
        "findings": _findings({"findings": result.get("findings") or mesh_findings}),
        "cell_count": result.get("cell_count"),
        "mass_imbalance": result.get("mass_imbalance"),
        "analysis_unavailable": (result.get("analysis") or {}).get("unavailable"),
        "progress": run.get("progress"),
    }
    if include_spec:
        summary["spec"] = run.get("spec")
    return summary


def _simulation_view(body):
    status = body.get("status")
    return {
        "ok": True,
        "id": body.get("id"),
        "name": body.get("name"),
        "status": status,
        "stage": body.get("stage"),
        "tags": body.get("tags") or [],
        "geometry": body.get("geometry"),
        "setup_summary": body.get("setup_summary"),
        "experimental": body.get("experimental") or [],
        "status_block": body.get("status_block"),
        "headline": {key: value for key, value in (body.get("headline") or {}).items() if value is not None},
        "cells": body.get("cells"),
        "convergence": body.get("convergence"),
        "results_run_id": body.get("results_run_id"),
        "current_run_id": body.get("current_run_id"),
        "error": body.get("error"),
        "stages": [{key: stage.get(key) for key in ("id", "kind", "status", "stage", "attempt", "retry_of", "error")}
                   for stage in body.get("stages") or []],
        "next": SIMULATION_NEXT.get(status),
    }


def _batch_member(member):
    batch = member.get("batch") or {}
    return {"id": member.get("id"), "label": batch.get("label"), "values": batch.get("values"), "mesh_from": batch.get("mesh_from"),
            "status": member.get("status"), "stage": member.get("stage"),
            "headline": {key: value for key, value in (member.get("headline") or {}).items() if value is not None},
            "status_block": member.get("status_block"), "results_run_id": member.get("results_run_id"),
            "current_run_id": member.get("current_run_id"), "error": member.get("error")}


def _unit(vector):
    length = math.sqrt(sum(float(x)*float(x) for x in vector))
    return [float(x)/length for x in vector] if length > 0 else [float(x) for x in vector]


def _orient(axis, rpm, sense):
    """The axis along the air the rotor pushes, keeping the physical spin (rotors.orient)."""
    axis = _unit(axis)
    if sense and rpm and (1 if rpm > 0 else -1)*sense > 0:
        return [-x for x in axis], -float(rpm)
    return axis, float(rpm)


def _failure(reply):
    return {"ok": False, "status": reply.status, "error": _detail(reply.body)}


class Session:
    def __init__(self, base_url=None, transport=None):
        self.base_url = (base_url or os.environ.get("GUSTSIM_URL") or "http://127.0.0.1:8080").rstrip("/")
        self.transport = transport or UrllibTransport(self.base_url)
        # Called as progress(message) while a wait_s poll is still active (MCP progress notifications).
        self.progress = None

    def request(self, method, path, **kwargs):
        return self.transport.request(method, path, **kwargs)

    def call(self, name, arguments=None):
        spec = TOOLS_BY_NAME.get(name)
        if spec is None:
            return {"ok": False, "error": f"Unknown tool {name}."}
        arguments = arguments or {}
        if not isinstance(arguments, dict):
            return {"ok": False, "error": "Tool arguments must be an object."}
        properties = spec["inputSchema"]["properties"]
        missing = [key for key in spec["inputSchema"]["required"] if key not in arguments]
        unexpected = [key for key in arguments if key not in properties]
        if missing or unexpected:
            return {"ok": False, "error": "Tool arguments do not match the schema.", "missing": missing, "unexpected": unexpected}
        try:
            return getattr(self, name)(**arguments)
        except AgentError as error:
            return {"ok": False, "error": str(error)}
        except TypeError as error:
            return {"ok": False, "error": str(error)}

    def _wait(self, fetch, active, wait_s, describe=None):
        """Poll fetch() until active(view) is false or wait_s runs out. The attempt is never touched."""
        describe = describe or (lambda view: f"{view.get('status')} ({view.get('stage')})")
        timeout = max(0.0, min(float(wait_s or 0), 3600.0))
        deadline = time.monotonic() + timeout
        while True:
            view = fetch()
            if not view.get("ok"):
                return view
            running = active(view)
            if not running or time.monotonic() >= deadline:
                view["still_running"] = running
                if running:
                    view["note"] = "The wait ended while this work was still active. Call again with wait_s to keep watching. Nothing was cancelled."
                return view
            if self.progress:
                self.progress(describe(view))
            time.sleep(min(2.0, max(0.05, deadline - time.monotonic())))

    def gustsim_status(self):
        reply = self.request("GET", "/api/health", timeout=15)
        if not reply.ok:
            return _failure(reply)
        out = {"ok": True, "url": self.base_url, "service": reply.body, "physics": PHYSICS}
        capabilities = self.request("GET", "/api/capabilities", timeout=15)
        if capabilities.ok and isinstance(capabilities.body, dict):
            out["lanes"] = capabilities.body.get("lanes")
            out["capabilities"] = {key: {name: item.get(name) for name in ("label", "maturity", "available", "gates")}
                                   for key, item in (capabilities.body.get("physics") or {}).items()}
        return out

    def gustsim_list_geometry(self, limit=50):
        reply = self.request("GET", "/api/geometry", timeout=30)
        if not reply.ok:
            return _failure(reply)
        rows = reply.body or []
        return {"ok": True, "geometries": [_geometry_view(item) for item in rows[: int(limit)]]}

    def gustsim_describe_geometry(self, geometry_id):
        reply = self.request("GET", f"/api/geometry/{geometry_id}", timeout=30)
        if not reply.ok:
            return _failure(reply)
        view = _geometry_view(reply.body)
        view["ok"] = True
        diagnostics = view.get("diagnostics") or {}
        view["geometry_ready"] = bool(diagnostics.get("watertight") and diagnostics.get("winding_consistent") and not diagnostics.get("degenerate_faces"))
        return view

    def gustsim_import_geometry(self, path, units="mm"):
        source = Path(path)
        if not source.is_file():
            return {"ok": False, "error": f"File not found: {source}"}
        if source.suffix.lower() not in IMPORT_SUFFIXES:
            return {"ok": False, "error": "Supported formats: STEP, IGES, STL, OBJ and glTF/GLB."}
        content = source.read_bytes()
        if len(content) > 100 * 1024 * 1024:
            return {"ok": False, "error": "File exceeds 100 MB."}
        reply = self.request("POST", "/api/geometry/import", form={"units": units},
                             files={"file": (source.name, content, "application/octet-stream")}, timeout=600)
        if not reply.ok:
            return _failure(reply)
        view = _geometry_view(reply.body)
        view["ok"] = True
        return view

    def gustsim_create_primitive(self, shape, **fields):
        body = {"shape": shape}
        parent = fields.pop("parent_id", None)
        body.update({key: value for key, value in fields.items() if value is not None})
        path = "/api/geometry/primitive" + (f"?parent_id={parent}" if parent else "")
        reply = self.request("POST", path, json_body=body, timeout=120)
        if not reply.ok:
            return _failure(reply)
        view = _geometry_view(reply.body)
        view["ok"] = True
        return view

    def gustsim_prepare_geometry(self, geometry_id, mode, wrap_resolution=None, wrap_close_gaps=None):
        body = {"repair": True, "component_surfaces": mode == "component", "wrap": mode == "wrap"}
        if wrap_resolution is not None:
            body["wrap_resolution"] = wrap_resolution
        if wrap_close_gaps is not None:
            body["wrap_close_gaps"] = wrap_close_gaps
        reply = self.request("POST", f"/api/geometry/{geometry_id}/prepare", json_body=body, timeout=600)
        if not reply.ok:
            return _failure(reply)
        view = _geometry_view(reply.body)
        view["ok"] = True
        view["mode"] = mode
        return view

    def gustsim_detect_rotor(self, geometry_id, patches, rpm=None):
        reply = self.request("POST", f"/api/geometry/{geometry_id}/rotor-detect", json_body={"patches": patches}, timeout=120)
        if not reply.ok:
            return _failure(reply)
        out = {"ok": True, **reply.body, "suggestion": True,
               "convention": "handedness +1: spun positively about axis, the blades push air towards -axis. "
                             "The preset axis points along the pushed air so thrust comes out positive."}
        if rpm is not None:
            axis, oriented = _orient(reply.body.get("axis") or [0, 0, 1], float(rpm), reply.body.get("handedness") or 0)
            out.update(preset_axis=axis, preset_rpm=oriented, preset_origin=reply.body.get("origin"))
            if not reply.body.get("handedness"):
                out["note"] = "The blade sense could not be told; check the sign of the thrust after the first run."
        return out

    def gustsim_preset(self, **request):
        reply = self.request("POST", "/api/presets/preview", json_body=request, timeout=120)
        if not reply.ok:
            return _failure(reply)
        report = reply.body.get("report") or {}
        return {"ok": True, "confirmed": False, "spec": reply.body.get("spec"), "review": reply.body.get("review"),
                "layer_plan": report.get("layer_plan"), "report": _report(report),
                "next": "Read review, report.findings, and layer_plan. Set spec.use_case.confirmed to true only after the dimensions, fluid point, and directions are acceptable, then call gustsim_validate."}

    def gustsim_validate(self, spec):
        reply = self.request("POST", "/api/validate", json_body=spec, timeout=120)
        if not reply.ok:
            return _failure(reply)
        report = _report(reply.body)
        report["ok"] = True
        return report

    def gustsim_estimate(self, spec):
        reply = self.request("POST", "/api/mesh/estimate", json_body=spec, timeout=120)
        if not reply.ok:
            return _failure(reply)
        body = reply.body or {}
        keep = ("tier", "cells", "memory_gb", "runtime_min", "iterations", "time_steps", "feasible", "reason")
        return {"ok": True, "basis": body.get("basis"), "calibration_samples": body.get("samples"),
                "current": {key: (body.get("current") or {}).get(key) for key in keep},
                "tiers": [{key: tier.get(key) for key in keep} for tier in body.get("tiers") or []],
                "background_cell_m": body.get("background_cell_m"), "surface_cell_m": body.get("surface_cell_m"),
                "first_layer_m": body.get("first_layer_m"), "note": body.get("note")}

    def _ready(self, spec, acknowledge_warnings):
        checked = self.gustsim_validate(spec)
        if not checked.get("ok"):
            return None, checked
        if not checked.get("valid"):
            checked["ok"] = False
            return None, checked
        reviews = [item for item in checked.get("findings") or [] if item.get("status") == "review"]
        if reviews and not acknowledge_warnings:
            return None, {"ok": False, "needs_acknowledgement": True, "configuration_id": checked.get("configuration_id"),
                          "findings": reviews, "layer_plan": checked.get("layer_plan"), "physics_maturity": checked.get("physics_maturity"),
                          "error": "Setup has review findings. Read them, then call again with acknowledge_warnings true."}
        return checked, None

    def _queue(self, spec, kind, acknowledge_warnings=False, mesh_run_id=None):
        checked, blocked = self._ready(spec, acknowledge_warnings)
        if blocked:
            return blocked
        query = f"kind={kind}&guided=true"
        if acknowledge_warnings and checked.get("configuration_id"):
            query += f"&acknowledged={checked['configuration_id']}"
        if mesh_run_id:
            query += f"&mesh_run_id={mesh_run_id}"
        reply = self.request("POST", f"/api/runs?{query}", json_body=spec, timeout=180)
        if not reply.ok:
            failure = _failure(reply)
            failure["configuration_id"] = checked.get("configuration_id")
            return failure
        summary = _summarize_run(reply.body, include_spec=True)
        summary["queued"] = summary.get("status") in {"queued", "running", "generating", "completed"}
        summary["experimental_validation"] = "pending"
        return summary

    def gustsim_queue_mesh(self, spec, acknowledge_warnings=False):
        return self._queue(spec, "mesh", acknowledge_warnings)

    def gustsim_queue_solve(self, spec, mesh_run_id, acknowledge_warnings=False):
        if not mesh_run_id:
            return {"ok": False, "error": "A reviewed mesh run id is required before solving."}
        return self._queue(spec, "solve", acknowledge_warnings, mesh_run_id)

    def gustsim_compile_case(self, spec, acknowledge_warnings=False):
        return self._queue(spec, "case", acknowledge_warnings)

    def gustsim_review_mesh(self, run_id, acknowledge_warnings=False):
        current = self.gustsim_run(run_id)
        if not current.get("ok"):
            return current
        if not current.get("spec"):
            return {"ok": False, "error": "This run has no stored specification."}
        checked = self.gustsim_validate(current["spec"])
        if not checked.get("ok"):
            return checked
        findings = current.get("findings") or []
        reviews = [item for item in findings if item.get("status") == "review"]
        if reviews and not acknowledge_warnings:
            return {"ok": False, "needs_acknowledgement": True, "configuration_id": checked.get("configuration_id"),
                    "findings": findings, "error": "Mesh has review findings. Read them, then call again with acknowledge_warnings true."}
        query = f"configuration_id={checked.get('configuration_id')}&acknowledge_warnings={'true' if acknowledge_warnings else 'false'}"
        reply = self.request("POST", f"/api/runs/{run_id}/review?{query}", json_body={}, timeout=60)
        if not reply.ok:
            return _failure(reply)
        summary = _summarize_run(reply.body, include_spec=True)
        summary["mesh_reviewed"] = True
        return summary

    def _read_run(self, run_id):
        reply = self.request("GET", f"/api/runs/{run_id}", timeout=60)
        if not reply.ok:
            return _failure(reply)
        return _summarize_run(reply.body, include_spec=True)

    def gustsim_run(self, run_id, wait_s=0):
        return self._wait(lambda: self._read_run(run_id), lambda view: view.get("status") in RUN_ACTIVE, wait_s)

    def gustsim_cancel(self, run_id):
        reply = self.request("POST", f"/api/runs/{run_id}/cancel", json_body={}, timeout=30)
        if not reply.ok:
            return _failure(reply)
        return _summarize_run(reply.body, include_spec=False)

    def gustsim_list_runs(self, limit=20):
        reply = self.request("GET", "/api/runs", timeout=30)
        if not reply.ok:
            return _failure(reply)
        rows = [_summarize_run(item) for item in (reply.body or [])[: int(limit)]]
        return {"ok": True, "runs": rows}

    def gustsim_simulate(self, spec, acknowledge_warnings=False, name=None, tags=None, auto_review=True):
        checked, blocked = self._ready(spec, acknowledge_warnings)
        if blocked:
            return blocked
        body = {"spec": spec, "plan": {"auto_review": bool(auto_review)}, "tags": list(tags or [])}
        if name:
            body["name"] = name
        if acknowledge_warnings and checked.get("configuration_id"):
            body["acknowledged"] = checked["configuration_id"]
        reply = self.request("POST", "/api/simulations", json_body=body, timeout=180)
        if not reply.ok:
            failure = _failure(reply)
            failure["configuration_id"] = checked.get("configuration_id")
            return failure
        view = _simulation_view(reply.body)
        view["configuration_id"] = checked.get("configuration_id")
        view["physics_maturity"] = checked.get("physics_maturity")
        health = self.request("GET", "/api/health", timeout=15)
        online = bool(health.ok and ((health.body or {}).get("worker") or {}).get("online"))
        view["worker_online"] = online
        if not online:
            view["note"] = "The compute worker is offline. The simulation stays queued until the worker starts (docker compose up)."
        return view

    def _read_simulation(self, simulation_id):
        reply = self.request("GET", f"/api/simulations/{simulation_id}", timeout=60)
        if not reply.ok:
            return _failure(reply)
        return _simulation_view(reply.body)

    def gustsim_simulation(self, simulation_id, wait_s=0):
        return self._wait(lambda: self._read_simulation(simulation_id), lambda view: view.get("status") in SIMULATION_ACTIVE, wait_s)

    def gustsim_continue_simulation(self, simulation_id, acknowledge_warnings=False):
        current = self._read_simulation(simulation_id)
        if not current.get("ok"):
            return current
        if current.get("status") != "awaiting_review":
            return {"ok": False, "status": current.get("status"), "error": "Only a simulation awaiting a mesh review can continue."}
        mesh = next((stage for stage in reversed(current["stages"]) if stage.get("kind") == "mesh"), None)
        if mesh is not None:
            record = self._read_run(mesh["id"])
            if not record.get("ok"):
                return record
            reviews = [item for item in record.get("findings") or [] if item.get("status") == "review"]
            if reviews and not acknowledge_warnings:
                return {"ok": False, "needs_acknowledgement": True, "mesh_run_id": mesh["id"], "findings": record.get("findings"),
                        "error": "Mesh has review findings. Read them, then call again with acknowledge_warnings true."}
        query = f"acknowledge_warnings={'true' if acknowledge_warnings else 'false'}"
        reply = self.request("POST", f"/api/simulations/{simulation_id}/continue?{query}", json_body={}, timeout=60)
        if not reply.ok:
            return _failure(reply)
        return _simulation_view(reply.body)

    def gustsim_cancel_simulation(self, simulation_id):
        reply = self.request("POST", f"/api/simulations/{simulation_id}/cancel", json_body={}, timeout=30)
        if not reply.ok:
            return _failure(reply)
        return _simulation_view(reply.body)

    def gustsim_list_simulations(self, limit=20, tag=None, status=None, query=None, archived=False):
        params = {key: value for key, value in (("tag", tag), ("status", status), ("q", query)) if value}
        if archived:
            params["archived"] = "true"
        path = "/api/simulations" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        reply = self.request("GET", path, timeout=60)
        if not reply.ok:
            return _failure(reply)
        rows = [_simulation_view(item) for item in (reply.body or [])[: int(limit)]]
        for row in rows:
            row.pop("ok", None)
            row.pop("stages", None)
        return {"ok": True, "simulations": rows}

    def _document(self, run_id, name):
        reply = self.request("GET", f"/api/runs/{run_id}/results/{name}", timeout=60)
        return reply.body if reply.ok and isinstance(reply.body, dict) else None

    def gustsim_results(self, run_id, include_insights=False):
        reply = self.request("GET", f"/api/runs/{run_id}", timeout=60)
        if not reply.ok:
            return _failure(reply)
        if reply.body.get("kind") != "solve":
            return {"ok": False, "error": "Results belong to a solve run. Use results_run_id from gustsim_simulation."}
        summary = _summarize_run(reply.body)
        out = {"ok": True, "run_id": run_id, "simulation_id": summary.get("simulation_id"), "name": summary.get("name"),
               "status": summary.get("status"), "evidence": summary["evidence"], "metrics": summary["metrics"],
               "findings": summary["findings"], "cell_count": summary.get("cell_count"), "conventions": CONVENTIONS}
        unavailable = []
        manifest = self._document(run_id, "manifest")
        if manifest and isinstance(manifest.get("status"), dict):
            out["status_block"] = manifest["status"]
        else:
            unavailable.append("manifest")
        forces = self._document(run_id, "forces")
        if forces:
            out["forces"] = {key: forces.get(key) for key in ("averaging", "frames", "coefficients", "reference", "frontal_area_m2",
                                                             "lift_split", "blockage", "parts", "parts_sum_check", "speed_curve",
                                                             "unavailable") if forces.get(key) is not None}
        else:
            unavailable.append("forces")
        convergence = self._document(run_id, "convergence")
        if convergence:
            out["convergence"] = {key: convergence.get(key) for key in ("state", "message", "detected_at", "averaging", "stop", "signals")}
        else:
            unavailable.append("convergence")
        elements = self._document(run_id, "elements")
        if elements:
            out["elements"] = {key: value for key, value in elements.items() if key != "schema" and value}
        if include_insights:
            insights = self._document(run_id, "insights")
            if insights:
                out["insights"] = {"disclaimer": insights.get("disclaimer"),
                                   "items": [{key: item.get(key) for key in ("id", "severity", "confidence", "category", "title", "message", "limitations")}
                                             for item in insights.get("insights") or []]}
            else:
                unavailable.append("insights")
        if unavailable:
            out["unavailable"] = unavailable
            if summary.get("status") == "completed":
                out["note"] = "Some results documents are not built yet. The results package runs after the solve; wait for the simulation to complete."
        return out

    def gustsim_batch(self, name, base, kind, parameters=None, variants=None, tags=None, acknowledge_warnings=False, auto_review=True):
        checked, blocked = self._ready(base, acknowledge_warnings)
        if blocked:
            return blocked
        body = {"name": name, "base": base, "kind": kind, "parameters": list(parameters or []), "variants": list(variants or []),
                "tags": list(tags or []), "auto_review": bool(auto_review), "acknowledge_review": bool(acknowledge_warnings)}
        reply = self.request("POST", "/api/batches", json_body=body, timeout=300)
        if not reply.ok:
            failure = _failure(reply)
            if "findings to review" in failure["error"]:
                failure["needs_acknowledgement"] = True
            return failure
        return self._batch_view(reply.body)

    def _batch_view(self, body):
        members = [_batch_member(member) for member in body.get("members") or []]
        counts = {}
        for member in members:
            counts[member["status"]] = counts.get(member["status"], 0) + 1
        return {"ok": True, "id": body.get("id"), "name": body.get("name"), "kind": body.get("kind"), "counts": counts,
                "members": members, "series": body.get("series") or [], "shared_meshes": body.get("shared_meshes") or [],
                "next": "Members awaiting_review need gustsim_continue_simulation on the member that owns the shared mesh. "
                        "Compare completed members with gustsim_compare on their results_run_id."}

    def _read_batch(self, batch_id):
        reply = self.request("GET", f"/api/batches/{batch_id}", timeout=120)
        if not reply.ok:
            return _failure(reply)
        return self._batch_view(reply.body)

    def gustsim_batch_status(self, batch_id, wait_s=0):
        return self._wait(lambda: self._read_batch(batch_id),
                          lambda view: any(member["status"] in SIMULATION_ACTIVE for member in view["members"]), wait_s,
                          lambda view: ", ".join(f"{count} {status}" for status, count in sorted(view["counts"].items())))

    def gustsim_compare(self, run_ids, baseline=None, columns=None):
        params = {"runs": ",".join(run_ids)}
        if baseline:
            params["baseline"] = baseline
        if columns:
            params["columns"] = ",".join(columns)
        reply = self.request("GET", f"/api/compare?{urllib.parse.urlencode(params)}", timeout=120)
        if not reply.ok:
            return _failure(reply)
        return {"ok": True, **reply.body, "experimental_validation": "pending"}

    def gustsim_export(self, run_id, kind, output_path):
        target = Path(output_path)
        if target.exists() and target.is_dir():
            return {"ok": False, "error": "output_path is a directory."}
        if not target.parent.is_dir():
            return {"ok": False, "error": f"Output directory does not exist: {target.parent}"}
        reply = self.request("GET", f"/api/runs/{run_id}/export/{kind}", timeout=300, raw=True)
        if not reply.ok:
            try:
                body = json.loads(reply.raw.decode())
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = {"detail": "Export failed"}
            return _failure(Reply(reply.status, body))
        target.write_bytes(reply.raw)
        return {"ok": True, "path": str(target), "bytes": len(reply.raw), "sha256": hashlib.sha256(reply.raw).hexdigest(),
                "kind": kind, "run_id": run_id, "experimental_validation": "pending"}


def _report(report):
    return {"valid": report.get("valid"), "errors": report.get("errors") or [], "warnings": report.get("warnings") or [],
            "findings": _findings(report), "configuration_id": report.get("configuration_id"),
            "geometry_ready": report.get("geometry_ready"), "layer_plan": report.get("layer_plan"),
            "reynolds": report.get("reynolds"), "estimated_mach": report.get("estimated_mach"),
            "physics_maturity": report.get("physics_maturity"), "experimental_features": report.get("experimental_features") or [],
            "validation_status": report.get("validation_status") or "experimental_validation_pending"}


def _result(identifier, payload):
    return {"jsonrpc": "2.0", "id": identifier, "result": payload}


def _rpc_error(identifier, code, message):
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


def dispatch(session, message, notify=None):
    """Handle one MCP JSON-RPC message. Notifications return None. notify(payload) sends a
    server notification (progress while a wait_s poll runs) when the client asked for one."""
    method = message.get("method")
    identifier = message.get("id")
    if identifier is None and isinstance(method, str) and method.startswith("notifications/"):
        return None
    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion") or "2024-11-05"
        return _result(identifier, {"protocolVersion": version, "capabilities": {"tools": {}},
                                    "serverInfo": {"name": "gustsim", "version": "0.2.0"}, "instructions": INSTRUCTIONS})
    if method == "tools/list":
        return _result(identifier, {"tools": TOOLS})
    if method == "ping":
        return _result(identifier, {})
    if method == "tools/call":
        params = message.get("params") or {}
        token = (params.get("_meta") or {}).get("progressToken")
        if notify is not None and token is not None:
            count = [0]

            def progress(text):
                count[0] += 1
                notify({"jsonrpc": "2.0", "method": "notifications/progress",
                        "params": {"progressToken": token, "progress": count[0], "message": text}})
            session.progress = progress
        try:
            payload = session.call(params.get("name"), params.get("arguments") or {})
        finally:
            session.progress = None
        return _result(identifier, {"content": [{"type": "text", "text": json.dumps(payload)}],
                                    "isError": not payload.get("ok", False)})
    if identifier is None:
        return None
    return _rpc_error(identifier, -32601, f"Unknown method {method}")


def _write_message(payload):
    data = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _read_message(stream):
    while True:
        line = stream.readline()
        if not line:
            return None
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
            while True:
                header = stream.readline()
                if header in (b"\n", b"\r\n", b""):
                    break
            return json.loads(stream.read(length).decode())
        stripped = line.strip()
        if stripped:
            return json.loads(stripped.decode())


def serve_stdio(base_url=None):
    """Speak MCP over stdin/stdout. Diagnostics go to stderr; stdout is protocol only."""
    session = Session(base_url)
    while True:
        try:
            message = _read_message(sys.stdin.buffer)
        except (json.JSONDecodeError, ValueError) as error:
            _write_message(_rpc_error(None, -32700, f"Invalid message: {error}"))
            continue
        if message is None:
            return
        try:
            response = dispatch(session, message, notify=_write_message)
        except Exception as error:
            print(f"gustsim mcp: {error}", file=sys.stderr)
            if message.get("id") is not None:
                _write_message(_rpc_error(message.get("id"), -32603, "Tool dispatch failed"))
            continue
        if response is not None:
            _write_message(response)


def run_cli(args):
    session = Session(args.url)
    if args.agent_command == "tools":
        json.dump(TOOLS, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return
    raw = sys.stdin.read() if args.arguments == "-" else args.arguments
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as error:
        json.dump({"ok": False, "error": f"Arguments are not JSON: {error}"}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        raise SystemExit(2)
    result = session.call(args.name, payload)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    raise SystemExit(0 if result.get("ok") else 1)
