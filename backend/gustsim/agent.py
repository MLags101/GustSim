"""Tools a coding agent uses to configure and run GustSim through its HTTP API.

The public API stays authoritative: these tools do not write case files themselves,
do not overwrite an earlier run, and do not promote a finished job to an experimentally
validated result. Start the stack with ``docker compose up`` and point ``GUSTSIM_URL``
at it (the default is http://127.0.0.1:8080).
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

PHYSICS = {
    "solver": "OpenCFD OpenFOAM v2606, simpleFoam",
    "supported": "Steady, incompressible, single-phase air or water, laminar or k-omega SST, with at most one MRF region.",
    "excluded": ["transient", "compressible", "multiphase", "free surface", "cavitation", "transition", "sliding meshes"],
    "experimental_validation": "pending",
    "evidence_states": ["geometry readiness", "execution", "numerical quality", "experimental validation"],
}

INSTRUCTIONS = (
    "GustSim configures and runs steady incompressible CFD for STEP assemblies, internal passages, "
    "and one MRF rotor. External flow is the shared setup those cases use. "
    "A finished run keeps execution, numerical quality, and experimental validation separate. "
    "Experimental validation is pending: a completed job is not a measurement. "
    "Do not request transient, compressible, multiphase, free-surface, cavitation, or transition physics. "
    "Retries create a new run. The service is local; start it with docker compose up."
)

VEC = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}


def _schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def _tool(name, description, properties, required=()):
    return {"name": name, "description": description, "inputSchema": _schema(properties, required)}


TOOLS = [
    _tool("gustsim_status", "Read service health, whether the compute worker is online, and the supported physics. Does not start a run.", {}),
    _tool("gustsim_list_geometry", "List imported and prepared geometry revisions.", {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
    _tool("gustsim_describe_geometry", "Read one geometry revision: surfaces, dimensions, closure, and whether it is a shrink-wrapped approximation.", {"geometry_id": {"type": "string"}}, ("geometry_id",)),
    _tool("gustsim_import_geometry", "Import a STEP or STL file from a path on this machine. STEP uses the units embedded in the file. The units argument applies to STL and defaults to millimetres. Creates a new geometry revision.", {
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
    _tool("gustsim_preset", "Build a typed pipe, external-object, or rotor setup. Does not mesh or solve. The spec comes back with use_case.confirmed false: read the review notes, the report, and layer_plan, then set use_case.confirmed to true only when the dimensions, fluid point, and directions are acceptable.", {
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
    }, ("geometry_id", "kind")),
    _tool("gustsim_validate", "Run preflight on a typed spec. Reports geometry readiness, setup errors, review findings, the near-wall layer fit, Reynolds number, and estimated Mach. Does not queue a job. valid false means the spec will be rejected.", {"spec": {"type": "object"}}, ("spec",)),
    _tool("gustsim_queue_mesh", "Queue mesh generation and the explicit mesh checks. Requires a valid spec. Review findings must be passed back with acknowledge_warnings true after they have been read. The worker must be online. Returns a new run id.", {
        "spec": {"type": "object"}, "acknowledge_warnings": {"type": "boolean"},
    }, ("spec",)),
    _tool("gustsim_review_mesh", "Record that a completed mesh was reviewed. Required before a guided solve. If extended mesh findings need attention, read them and call again with acknowledge_warnings true. This does not change the mesh.", {
        "run_id": {"type": "string"}, "acknowledge_warnings": {"type": "boolean"},
    }, ("run_id",)),
    _tool("gustsim_queue_solve", "Queue a solve on a mesh that has already been reviewed. mesh_run_id is that mesh attempt. A geometry or physics change makes the mesh unusable and this call fails. Creates a new solve run.", {
        "spec": {"type": "object"}, "mesh_run_id": {"type": "string"}, "acknowledge_warnings": {"type": "boolean"},
    }, ("spec", "mesh_run_id")),
    _tool("gustsim_compile_case", "Write the OpenFOAM case for a valid spec without meshing or solving. Works while the worker is offline. The case is a new run and can be exported with kind case.", {
        "spec": {"type": "object"}, "acknowledge_warnings": {"type": "boolean"},
    }, ("spec",)),
    _tool("gustsim_run", "Read one run. ok means the record was read, not that the CFD succeeded: use status and evidence for that. Histories are omitted. evidence keeps execution, numerical quality, and experimental validation separate. wait_s polls until the attempt leaves the queue or the time runs out (maximum 3600).", {
        "run_id": {"type": "string"}, "wait_s": {"type": "number"},
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


def _failure(reply):
    return {"ok": False, "status": reply.status, "error": _detail(reply.body)}


class Session:
    def __init__(self, base_url=None, transport=None):
        self.base_url = (base_url or os.environ.get("GUSTSIM_URL") or "http://127.0.0.1:8080").rstrip("/")
        self.transport = transport or UrllibTransport(self.base_url)

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

    def gustsim_status(self):
        reply = self.request("GET", "/api/health", timeout=15)
        if not reply.ok:
            return _failure(reply)
        return {"ok": True, "url": self.base_url, "service": reply.body, "physics": PHYSICS}

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
        if source.suffix.lower() not in {".stl", ".step", ".stp"}:
            return {"ok": False, "error": "Only STEP and STL files are supported."}
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
                          "findings": reviews, "layer_plan": checked.get("layer_plan"),
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

    def gustsim_run(self, run_id, wait_s=0):
        timeout = max(0.0, min(float(wait_s or 0), 3600.0))
        deadline = time.monotonic() + timeout
        while True:
            reply = self.request("GET", f"/api/runs/{run_id}", timeout=60)
            if not reply.ok:
                return _failure(reply)
            summary = _summarize_run(reply.body, include_spec=True)
            running = summary.get("status") in {"queued", "running", "generating"}
            summary["still_running"] = running and time.monotonic() < deadline
            if not running or time.monotonic() >= deadline:
                if running:
                    summary["still_running"] = True
                    summary["note"] = "The wait ended while this attempt was still queued or running. Call gustsim_run again with wait_s to keep watching. The attempt is preserved."
                return summary
            time.sleep(min(2.0, max(0.05, deadline - time.monotonic())))

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
            "validation_status": report.get("validation_status") or "experimental_validation_pending"}


def _result(identifier, payload):
    return {"jsonrpc": "2.0", "id": identifier, "result": payload}


def _rpc_error(identifier, code, message):
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


def dispatch(session, message):
    """Handle one MCP JSON-RPC message. Notifications return None."""
    method = message.get("method")
    identifier = message.get("id")
    if identifier is None and isinstance(method, str) and method.startswith("notifications/"):
        return None
    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion") or "2024-11-05"
        return _result(identifier, {"protocolVersion": version, "capabilities": {"tools": {}},
                                    "serverInfo": {"name": "gustsim", "version": "0.1.0"}, "instructions": INSTRUCTIONS})
    if method == "tools/list":
        return _result(identifier, {"tools": TOOLS})
    if method == "ping":
        return _result(identifier, {})
    if method == "tools/call":
        params = message.get("params") or {}
        payload = session.call(params.get("name"), params.get("arguments") or {})
        return _result(identifier, {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False})
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
            response = dispatch(session, message)
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
