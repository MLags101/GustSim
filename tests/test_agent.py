"""Coding-agent tools over the public API. These do not run OpenFOAM."""
import io
import json
from copy import deepcopy

import trimesh
from fastapi.testclient import TestClient

from gustsim.agent import INSTRUCTIONS, Session, dispatch, _read_message
from gustsim.api import app


class ClientTransport:
    def __init__(self, client):
        self.client = client

    def request(self, method, path, *, json_body=None, form=None, files=None, timeout=None, raw=False):
        kwargs = {}
        if files:
            kwargs["files"] = files
            kwargs["data"] = form or {}
        elif json_body is not None:
            kwargs["json"] = json_body
        response = self.client.request(method, path, **kwargs)
        if raw:
            return type("Reply", (), {"status": response.status_code, "body": None, "raw": response.content, "ok": response.status_code < 400})()
        try:
            body = response.json()
        except Exception:
            body = {"detail": response.text}
        return type("Reply", (), {"status": response.status_code, "body": body, "raw": b"", "ok": response.status_code < 400})()


def session():
    client = TestClient(app)
    client.__enter__()
    return Session("http://gustsim.test", ClientTransport(client)), client


def confirmed_external(tool):
    created = tool.call("gustsim_create_primitive", {"shape": "sphere", "name": "body", "radius": 0.2})
    assert created["ok"], created
    patches = [patch["name"] for patch in created["patches"]]
    preview = tool.call("gustsim_preset", {"geometry_id": created["id"], "kind": "aerodynamics", "patches": patches, "speed": 5, "turbulence": "laminar"})
    assert preview["ok"] and preview["confirmed"] is False
    assert preview["report"]["validation_status"] == "experimental_validation_pending"
    spec = preview["spec"]
    spec["use_case"]["confirmed"] = True
    return created, spec


def test_mcp_dispatch_advertises_the_physics_contract():
    tool, client = session()
    try:
        init = dispatch(tool, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert init["result"]["serverInfo"]["name"] == "gustsim"
        assert "pending" in init["result"]["instructions"]
        assert init["result"]["instructions"] == INSTRUCTIONS
        listed = dispatch(tool, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {item["name"] for item in listed["result"]["tools"]}
        assert {"gustsim_preset", "gustsim_queue_mesh", "gustsim_queue_solve", "gustsim_review_mesh", "gustsim_export"} <= names
        assert dispatch(tool, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
        called = dispatch(tool, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "gustsim_status", "arguments": {}}})
        body = json.loads(called["result"]["content"][0]["text"])
        assert body["ok"] and body["physics"]["experimental_validation"] == "pending"
        assert "transient" in body["physics"]["excluded"]
    finally:
        client.__exit__(None, None, None)


def test_message_framing_accepts_lines_and_content_length():
    assert _read_message(io.BytesIO(b'{"method":"ping","id":1}\n'))["method"] == "ping"
    body = b'{"method":"ping","id":2}'
    framed = f"Content-Length: {len(body)}\r\n\r\n".encode() + body
    assert _read_message(io.BytesIO(framed))["id"] == 2
    assert _read_message(io.BytesIO(b"")) is None


def test_agent_builds_a_case_and_refuses_to_mesh_without_a_worker_or_review():
    tool, client = session()
    try:
        assert tool.call("gustsim_status", {"nope": True})["unexpected"] == ["nope"]
        created, spec = confirmed_external(tool)
        described = tool.call("gustsim_describe_geometry", {"geometry_id": created["id"]})
        assert described["geometry_ready"] is True
        prepared = tool.call("gustsim_prepare_geometry", {"geometry_id": created["id"], "mode": "cleanup"})
        assert prepared["ok"] and prepared["id"] != created["id"]
        missing = tool.call("gustsim_preset", {"geometry_id": created["id"], "kind": "pipe"})
        assert missing["ok"] is False
        thick = deepcopy(spec)
        thick["mesh"].update(layers=15, first_layer_m=0.2, surface_level=1, body_level=0)
        blocked = tool.call("gustsim_queue_mesh", {"spec": thick, "acknowledge_warnings": False})
        assert blocked["ok"] is False and blocked["needs_acknowledgement"] is True
        assert any("do not fit" in item["detail"] for item in blocked["findings"])
        assert tool.call("gustsim_list_runs", {})["runs"] == []
        offline = tool.call("gustsim_queue_mesh", {"spec": spec, "acknowledge_warnings": True})
        assert offline["ok"] is False and "worker" in offline["error"].lower()
        compiled = tool.call("gustsim_compile_case", {"spec": spec})
        assert compiled["ok"] and compiled["status"] == "completed"
        assert compiled["evidence"]["case_available"] is True
        assert compiled["evidence"]["fields_available"] is False
        assert compiled["evidence"]["experimental_validation"] == "experimental_validation_pending"
        record = tool.call("gustsim_run", {"run_id": compiled["id"], "wait_s": 0})
        assert record["still_running"] is False and record["spec"]["geometry_id"] == created["id"]
        refused = tool.call("gustsim_queue_solve", {"spec": spec, "mesh_run_id": compiled["id"]})
        assert refused["ok"] is False
    finally:
        client.__exit__(None, None, None)


def test_agent_imports_stl_and_exports_the_compiled_case(tmp_path):
    tool, client = session()
    try:
        stl = tmp_path / "box.stl"
        trimesh.creation.box(extents=(0.4, 0.2, 0.2)).export(stl)
        imported = tool.call("gustsim_import_geometry", {"path": str(stl), "units": "m"})
        assert imported["ok"] and imported["triangles"] > 0
        patches = [patch["name"] for patch in imported["patches"]]
        preview = tool.call("gustsim_preset", {"geometry_id": imported["id"], "kind": "aerodynamics", "patches": patches, "speed": 3})
        spec = preview["spec"]
        spec["use_case"]["confirmed"] = True
        compiled = tool.call("gustsim_compile_case", {"spec": spec, "acknowledge_warnings": True})
        assert compiled["ok"], compiled
        target = tmp_path / "case.zip"
        exported = tool.call("gustsim_export", {"run_id": compiled["id"], "kind": "case", "output_path": str(target)})
        assert exported["ok"] and target.stat().st_size > 100
        assert exported["experimental_validation"] == "pending"
        assert tool.call("gustsim_cancel", {"run_id": compiled["id"]})["ok"] is False
    finally:
        client.__exit__(None, None, None)
