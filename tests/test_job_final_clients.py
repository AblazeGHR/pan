"""Real stdio -> trusted IPC -> isolated durable canonical service, and CLI TCP."""
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import uvicorn

from packages.automation.authorization import bind_objects, object_path
from packages.core.manifest_loader import ManifestConfig, McpServer
from packages.core.session import Session
from packages.jobs import final_contracts as fc
from packages.jobs.final_client import CanonicalClient, RESOURCES, TOOL_PREFIXES
from packages.jobs.window import command_window_options
from packages.mcp.server import (CANONICAL_TOOLS, RETAINED_LEGACY_MCP_TOOLS,
                                 RETIRED_MCP_TOOLS)
from packages.plugins.ipc import Bridge
from packages.plugins.runtime import Runtime
from packages.scheduler.providers import time_definition
from tests.test_job_final_creators import config, interval
from tests.test_job_final_http_service import h, job_def
from tests.test_job_final_transports import run, DEFINITIONS


ROOT = Path(__file__).resolve().parents[1]


def unpack(result):
    assert not result.isError, result
    return result.structuredContent or json.loads(result.content[0].text)


def grants(h, enabled):
    value = h.automation.auth._default_permissions()
    for row in value.values():
        for key in row:
            row[key] = enabled
    h.automation.auth.change_permissions(value, h.automation.auth.permissions_status()["revision"])


@pytest.mark.parametrize("projection", ["core", "native"])
@run
async def test_real_stdio_crud_operations_permissions_and_legacy(h, tmp_path, projection):
    from packages.automation.api import install
    install(SimpleNamespace(app=h.app))
    session = Session(id="ses_final_client", name="owned test", adapter_config={
        "plugin_bindings": [{"plugin_id": "pan", "mode": "standalone", "enabled": True}]})
    manifest = ManifestConfig(mcp_servers=[McpServer("pan", command=sys.executable,
                                                    args=["-m", "packages.mcp.server"])])
    runtime = Runtime(lambda: manifest, lambda sid: session if sid == session.id else None,
                      data_root=tmp_path / "plugins")
    bind_objects(runtime, h.app)
    bridge = Bridge(runtime, tmp_path)
    args = ["-m", "packages.mcp.server"] if projection == "core" else [
        "-m", "packages.plugins.mcp", "--view", "native", "--core"]
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PAN_")}
    env.update(PAN_PLUGIN_IPC=bridge.address, PAN_AGENT_SESSION_ID=session.id,
               PAN_API_URL="http://127.0.0.1:1")
    params = StdioServerParameters(command=sys.executable, args=args, cwd=str(ROOT), env=env)
    try:
        async with stdio_client(params) as streams, ClientSession(*streams) as client:
            await client.initialize()
            tools = (await client.list_tools()).tools
            names = [tool.name for tool in tools]
            assert max(Counter(names).values()) == 1
            assert set(CANONICAL_TOOLS) <= set(names)
            # All baseline tools minus the R5 retired Job writes, plus canonical.
            baseline = json.loads((ROOT / "tests/fixtures/job_final_legacy_tools.json").read_text())
            assert set(names) == (set(baseline) - set(RETIRED_MCP_TOOLS)) | set(CANONICAL_TOOLS)
            # The retirement is exact: old writes are gone, reads/cancel remain.
            assert not set(RETIRED_MCP_TOOLS) & set(names)
            assert set(RETAINED_LEGACY_MCP_TOOLS) <= set(names)
            # tools/call agrees with tools/list: an unknown name is refused.
            missing = await client.call_tool("agent_background_start", {
                "argv": ["echo", "x"], "cwd": str(ROOT), "name": "retired"})
            assert missing.isError is True

            async def call(name, **arguments):
                value = unpack(await client.call_tool(name, arguments))
                return value

            grants(h, False)
            definitions = {"jobs": job_def(h), "event-creators": time_definition("clock", config([interval()])),
                "triggers": DEFINITIONS["trigger"](), "job-templates": {
                    "name": "template", "jobDefinition": job_def(h), "composition": {"creators": [], "triggers": []}}}
            # Every resource's exact read/create/update/lifecycle/delete grant is denied.
            for resource, prefix in TOOL_PREFIXES.items():
                d = definitions[resource]
                for tool, arguments in [
                    ("list", {}), ("get", {"object_id": "missing"}),
                    ("create", {"definition": d, "request_key": "deny-create"}),
                    ("update", {"object_id": "missing", "definition": d, "expected_revision": 1, "request_key": "deny-update"}),
                    ("control", {"object_id": "missing", "operation": "retire", "expected_revision": 1,
                                 "expected_state_revision": 0, "request_key": "deny-control"}),
                    ("retire", {"object_id": "missing", "expected_revision": 1,
                                "expected_state_revision": 0, "request_key": "deny-retire"})]:
                    result = await call(prefix + "_" + tool, **arguments)
                    assert result["error"]["code"] == "authorization_denied", result
            for name in ["job_runs", "job_run_get", "job_run", "job_run_retry", "job_run_again",
                         "job_run_clone_and_run", "job_run_cancel", "job_run_pause", "job_run_resume"]:
                arguments = {"job_id": "missing"} if name in {"job_runs", "job_run"} else {"run_id": "missing"}
                if name not in {"job_runs", "job_run_get"}:
                    arguments["request_key"] = "deny-run"
                    if name != "job_run":
                        arguments["expected_state_revision"] = 0
                    if name in {"job_run", "job_run_again", "job_run_clone_and_run"}:
                        arguments["expected_revision"] = 1
                assert (await call(name, **arguments))["error"]["code"] == "authorization_denied"
            # Legacy write still reaches its original authorization path and fails closed.
            old = await call("authorization_objects", kind="trigger", operation="read")
            assert old["error"]["code"] == 403
            grants(h, True)
            assert "triggers" in (await call("authorization_objects", kind="trigger", operation="read"))

            objects = {}
            for resource in ["jobs", "event-creators", "triggers", "job-templates"]:
                prefix = TOOL_PREFIXES[resource]
                if resource == "triggers":
                    definitions[resource] = {"name": "clock trigger", "targetJobId": objects["jobs"]["id"],
                        "eventTypes": definitions["event-creators"]["eventTypes"],
                        "creatorSelector": {"mode": "ids", "ids": [objects["event-creators"]["id"]]}}
                created = await call(prefix + "_create", definition=definitions[resource], request_key=prefix + "-create")
                assert "result" in created, (resource, created)
                item = created["result"]["object"]
                assert (await call(prefix + "_get", object_id=item["id"])) == item
                assert (await call(prefix + "_list", limit=1))["items"][0]["id"] == item["id"]
                update = await call(prefix + "_update", object_id=item["id"], definition={**definitions[resource], "name": "updated"},
                    expected_revision=1, request_key=prefix + "-update")
                item = update["result"]["object"]
                assert item[fc.VERSION_FIELD[RESOURCES[resource]]] == 2
                stale = await call(prefix + "_update", object_id=item["id"], definition=definitions[resource],
                    expected_revision=1, request_key=prefix + "-stale")
                assert stale["error"]["code"] == "revision_conflict"
                if resource == "jobs":
                    item = (await call("job_control", object_id=item["id"], operation="enable", expected_revision=2,
                        expected_state_revision=item["stateRevision"], request_key="enable"))["result"]["object"]
                objects[resource] = item

            item = objects["jobs"]
            for resource in ["triggers", "event-creators", "job-templates"]:
                obj = objects[resource]
                result = await call(TOOL_PREFIXES[resource] + "_control", object_id=obj["id"],
                    operation="publish" if resource == "job-templates" else "enable", expected_revision=2,
                    expected_state_revision=obj["stateRevision"], request_key=resource + "-enable")
                assert "result" in result, result
            actions = await call("job_action_list")
            assert any(action["id"] == h.action["id"] for action in actions["items"])
            h.provider.fail_once = "0"
            first = await call("job_run", job_id=item["id"], expected_revision=2, request_key="run")
            rid = first["runId"]
            await h.service.dispatch_once()
            state = await call("job_run_get", run_id=rid)
            assert state["state"] == "failed"
            retried = await call("job_run_retry", run_id=rid, expected_state_revision=state["stateRevision"],
                                 request_key="retry", step_id="s0")
            assert retried["status"] == "accepted"
            await h.service.dispatch_once()
            state = await call("job_run_get", run_id=rid)
            assert state["state"] == "completed"
            again = await call("job_run_again", run_id=rid, expected_revision=2,
                expected_state_revision=state["stateRevision"], request_key="again")
            assert again["runId"] != rid
            for operation in ["pause", "resume", "cancel"]:
                current = await call("job_run_get", run_id=again["runId"])
                result = await call("job_run_" + operation, run_id=again["runId"],
                    expected_state_revision=current["stateRevision"], request_key=operation)
                assert result["status"] == "accepted", result
            await h.service.dispatch_once()
            assert (await call("job_run_get", run_id=again["runId"]))["state"] == "cancelled"
            cloned = await call("job_run_clone_and_run", run_id=rid, expected_revision=2,
                expected_state_revision=state["stateRevision"], request_key="clone", parameters={"overrides": {"name": "clone"}})
            assert cloned["result"]["jobId"] != item["id"]
            await h.service.dispatch_once()
            assert (await call("job_run_get", run_id=cloned["runId"]))["state"] == "completed"
            assert len((await call("job_runs", job_id=item["id"]))["items"]) == 2
            by_id = await call("job_operation_get", operation_id=first["operationId"])
            by_key = await call("job_operation_find", operation="create", request_key="run")
            assert by_id == by_key
            assert by_id["status"] == "rejected"  # original failure remains historical truth
            for resource in ["triggers", "event-creators", "job-templates", "jobs"]:
                obj = await call(TOOL_PREFIXES[resource] + "_get", object_id=objects[resource]["id"])
                result = await call(TOOL_PREFIXES[resource] + "_retire", object_id=obj["id"],
                    expected_revision=obj[fc.VERSION_FIELD[RESOURCES[resource]]],
                    expected_state_revision=obj["stateRevision"], request_key=resource + "-retire")
                assert result["result"]["object"]["state"] == "retired", result
    finally:
        await bridge.close()


@pytest.fixture
def tcp(h):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    assert port not in {8767, 8768}
    server = uvicorn.Server(uvicorn.Config(h.app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started and thread.is_alive()
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        assert not thread.is_alive()


@pytest.mark.parametrize("resource", RESOURCES)
def test_cli_real_tcp_401_and_user_crud(h, tcp, tmp_path, resource):
    from packages.jobs.final_client import HTTPTransport
    client = CanonicalClient(HTTPTransport(tcp))
    assert client.call(resource, "list")["error"]["code"] == "unauthenticated"
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PAN_")}
    env.update(PAN_API_TOKEN=h.token, PYTHONIOENCODING="utf-8")

    def cli(operation, object_id=None, body=None, token=True):
        args = [sys.executable, "-m", "packages.core.cli", "final", "--api-url", tcp, resource, operation]
        if object_id is not None:
            args.append(object_id)
        if body is not None:
            path = tmp_path / "body.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            args += ["--file", str(path)]
        current_env = dict(env)
        if not token:
            current_env.pop("PAN_API_TOKEN")
        result = subprocess.run(args, cwd=ROOT, env=current_env, text=True, encoding="utf-8",
            capture_output=True, timeout=30, **command_window_options())
        return result.returncode, json.loads(result.stdout)

    code, denied = cli("list", token=False)
    assert code == 1 and denied["error"]["code"] == "unauthenticated"
    value = job_def(h)
    if resource == "event-creators":
        value = time_definition("clock", config([interval()]))
    elif resource == "job-templates":
        value = {"name": "template", "jobDefinition": value, "composition": {"creators": [], "triggers": []}}
    elif resource == "triggers":
        from tests.test_job_final_http_service import enabled, create
        job = enabled(h)
        creator = create(h, "event-creators", time_definition("clock", config([interval()])), "clock")
        value = {"name": "trigger", "targetJobId": job["id"], "eventTypes": creator["eventTypes"],
                 "creatorSelector": {"mode": "ids", "ids": [creator["id"]]}}
    code, result = cli("create", body={"definition": value, "expectedRevision": 0, "requestKey": "cli-create"})
    assert code == 0, result
    item = result["result"]["object"]
    assert cli("get", item["id"]) == (0, item)
    assert cli("list")[1]["items"][0]["id"] == item["id"]
    code, result = cli("update", item["id"], {"definition": {**value, "name": "CLI updated"},
        "expectedRevision": 1, "requestKey": "cli-update"})
    assert code == 0, result
    obj = result["result"]["object"]
    code, result = cli("retire", obj["id"], {"expectedRevision": 2,
        "expectedStateRevision": obj["stateRevision"], "requestKey": "cli-retire"})
    assert code == 0 and result["result"]["object"]["state"] == "retired", result


@pytest.mark.parametrize("resource", RESOURCES)
@pytest.mark.parametrize("mutation", [{"expectedRevision": True}, {"requestKey": ""}, {"source": "user"}])
def test_wp01_validation_before_transport(resource, mutation):
    calls = []
    client = CanonicalClient(lambda *args: calls.append(args))
    body = {"definition": DEFINITIONS[RESOURCES[resource]](), "expectedRevision": 0, "requestKey": "request", **mutation}
    result = client.call(resource, "create", body=body)
    assert result["error"]["code"] == "invalid_schema"
    assert calls == []


@pytest.mark.parametrize("path", ["/api/final/jobs", "/api/final/jobs/j/runs?limit=1",
    "/api/final/event-creators/c/operations", "/api/final/triggers/t", "/api/final/job-templates/t",
    "/api/final/job-runs/r/operations", "/api/final/operations?operation=retry&requestKey=k", "/api/final/actions"])
def test_bridge_canonical_allowlist(path):
    assert object_path(path)
    assert object_path(path.replace("/api/final/actions", "/api/jobs/actions").replace("/api/final/", "/api/"))


@pytest.mark.parametrize("path", ["https://bad/api/final/jobs", "//bad/api/final/jobs", "/api/final/jobs/x/private",
    "/api/final/private", "/api/main/exit", "/api/final/jobs#fragment", "/api/final/jobs/%2e%2e",
    "/api/final/jobs/..", "/api/final/jobs/%2fapi%2fmain%2fexit"])
def test_bridge_stays_bounded(path):
    assert not object_path(path)
    assert not object_path(path.replace("/api/final/", "/api/"))


def test_cli_real_tcp_run_controls_and_receipt_recovery(h, tcp):
    from tests.test_job_final_http_service import enabled
    item = enabled(h)
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PAN_")}
    env.update(PAN_API_TOKEN=h.token, PYTHONIOENCODING="utf-8")

    def cli(resource, operation, object_id=None, body=None):
        args = [sys.executable, "-m", "packages.core.cli", "final", "--api-url", tcp, resource, operation]
        if object_id is not None:
            args.append(object_id)
        if body is not None:
            args += ["--file", "-"]
        result = subprocess.run(args, cwd=ROOT, env=env, text=True, encoding="utf-8", input=json.dumps(body),
            capture_output=True, timeout=30, **command_window_options())
        return result.returncode, json.loads(result.stdout)

    h.provider.fail_once = "0"
    code, first = cli("jobs", "run", item["id"], {"expectedRevision": 1, "requestKey": "cli-run",
        "input": {}, "cause": {"type": "manual"}})
    assert code == 0 and first["status"] == "accepted", first
    rid = first["runId"]
    asyncio.run(h.service.dispatch_once())
    state = cli("job-runs", "get", rid)[1]
    assert state["state"] == "failed"
    code, result = cli("job-runs", "retry", rid, {"expectedStateRevision": state["stateRevision"],
        "requestKey": "cli-retry", "stepId": "s0"})
    assert code == 0 and result["status"] == "accepted", result
    asyncio.run(h.service.dispatch_once())
    state = cli("job-runs", "get", rid)[1]
    assert state["state"] == "completed"
    code, again = cli("job-runs", "run-again", rid, {"expectedRevision": 1,
        "expectedStateRevision": state["stateRevision"], "requestKey": "cli-again"})
    assert code == 0 and again["runId"] != rid, again
    for operation in ["pause", "resume", "cancel"]:
        current = cli("job-runs", "get", again["runId"])[1]
        code, result = cli("job-runs", operation, again["runId"], {
            "expectedStateRevision": current["stateRevision"], "requestKey": "cli-" + operation})
        assert code == 0 and result["status"] == "accepted", result
    asyncio.run(h.service.dispatch_once())
    assert cli("job-runs", "get", again["runId"])[1]["state"] == "cancelled"
    code, clone = cli("job-runs", "clone-and-run", rid, {"expectedRevision": 1,
        "expectedStateRevision": state["stateRevision"], "requestKey": "cli-clone"})
    assert code == 0 and clone["result"]["jobId"] != item["id"], clone
    asyncio.run(h.service.dispatch_once())
    assert cli("job-runs", "get", clone["runId"])[1]["state"] == "completed"
    assert len(cli("jobs", "runs", item["id"])[1]["items"]) == 2
    code, by_id = cli("operations", "get", first["operationId"])
    assert code == 1 and by_id["status"] == "rejected"
    assert cli("operations", "find", body={"operation": "create", "requestKey": "cli-run"}) == (1, by_id)
    assert cli("actions", "list")[0] == 0


@pytest.mark.parametrize("operation,body", [
    ("pause", {"expectedStateRevision": True, "requestKey": "k"}),
    ("cancel", {"expectedStateRevision": 0, "requestKey": "k", "expectedRevision": 1}),
    ("retry", {"expectedStateRevision": 0, "requestKey": "k", "stepId": "../escape"}),
    ("run-again", {"expectedStateRevision": 0, "requestKey": "k"}),
    ("clone-and-run", {"expectedStateRevision": 0, "expectedRevision": 1, "requestKey": "k", "parameters": []}),
    ("clone-and-run", {"expectedStateRevision": 0, "expectedRevision": 1, "requestKey": "k", "parameters": {"x": float("nan")}}),
])
def test_invalid_run_controls_never_reach_transport(operation, body):
    calls = []
    result = CanonicalClient(lambda *args: calls.append(args)).call("job-runs", operation, object_id="run", body=body)
    assert result["error"]["code"] == "invalid_schema"
    assert not calls


def test_http_unconfirmed_write_is_never_replayed(monkeypatch):
    from packages.jobs import final_client
    calls = []
    class Timeout:
        def open(self, request, **kwargs):
            calls.append(request)
            raise TimeoutError()
    monkeypatch.setattr(final_client, "build_opener", lambda *args: Timeout())
    client = CanonicalClient(final_client.HTTPTransport("http://127.0.0.1:1"))
    result = client.call("jobs", "create", body={"definition": DEFINITIONS["job"](),
        "expectedRevision": 0, "requestKey": "original-key"})
    assert result["error"]["code"] == "transport_unconfirmed"
    assert len(calls) == 1
    assert json.loads(calls[0].data)["requestKey"] == "original-key"


@pytest.mark.parametrize("prefix", ["/api", "/api/final"])
def test_remote_mcp_cannot_borrow_local_authority(monkeypatch, prefix):
    from packages.mcp import server
    monkeypatch.setattr(server, "_terminal_transport", "streamable-http")
    assert server._api("GET", prefix + "/jobs")["error"]["code"] == "authorization_denied"


@pytest.mark.parametrize("prefix", ["/api", "/api/final"])
def test_imported_gateway_request_cannot_borrow_default_stdio_authority(monkeypatch, prefix):
    from packages.mcp import server
    monkeypatch.setattr(server, "_terminal_transport", "stdio")
    monkeypatch.setattr(server.mcp, "get_context", lambda: SimpleNamespace(
        request_context=SimpleNamespace(request=object())))
    monkeypatch.setattr("packages.plugins.ipc.request", lambda *args: pytest.fail("HTTP request must not use IPC"))
    assert server._api("GET", prefix + "/jobs")["error"]["code"] == "authorization_denied"


@run
async def test_real_http_mcp_request_cannot_borrow_local_ipc(monkeypatch):
    import httpx
    from packages.mcp import server
    monkeypatch.setattr(server, "_terminal_transport", "stdio")
    monkeypatch.setattr(server.mcp.settings, "stateless_http", True)
    monkeypatch.setattr(server.mcp.settings, "json_response", True)
    monkeypatch.setattr(server.mcp, "_session_manager", None)
    monkeypatch.setattr("packages.plugins.ipc.request", lambda *args: pytest.fail("HTTP request must not use IPC"))
    app = server.mcp.streamable_http_app()
    async with server.mcp.session_manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:12345") as client:
            response = await client.post("/mcp", headers={"Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18"}, json={"jsonrpc": "2.0", "id": 1,
                "method": "tools/call", "params": {"name": "job_list", "arguments": {}}})
            assert response.status_code == 200, response.text
            payload = response.json()["result"]
            assert not payload.get("isError")
            value = payload.get("structuredContent") or json.loads(payload["content"][0]["text"])
            assert value["error"]["code"] == "authorization_denied"


def test_cli_unknown_receipt_exits_nonzero(monkeypatch, tmp_path, capsys):
    from packages.jobs import final_cli
    from packages.core.cli import parser
    class UnknownClient:
        def __init__(self, transport):
            pass
        def call(self, *args, **kwargs):
            return {"status": "unknown", "operationId": "original-op"}
    monkeypatch.setattr(final_cli, "CanonicalClient", UnknownClient)
    args = parser().parse_args(["final", "--api-url", "http://127.0.0.1:1", "operations", "get", "original-op"])
    assert final_cli.run(args, tmp_path) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "unknown", "operationId": "original-op"}
