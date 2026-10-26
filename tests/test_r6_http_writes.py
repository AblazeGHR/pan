"""R6-B production route negative controls; no host lifecycle effects."""
import asyncio
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from packages.web import server
from tests.test_job_path_flip import effective_routes
from tests.test_job_final_clients import tcp

ROOT = Path(__file__).resolve().parents[1]
REMOVED = ("api_background_job_start", "api_session_message_job_start",
           "api_background_job_update", "api_background_job_retry",
           "api_main_exit", "api_main_restart")
WRITES = [("POST", "/api/background-jobs", 410),
          ("POST", "/api/session-message-jobs", 410),
          ("PATCH", "/api/background-jobs/original", 410),
          ("POST", "/api/background-jobs/original/retry", 410),
          ("POST", "/api/main/exit", 404),
          ("POST", "/api/main/restart", 404)]


@pytest.fixture
def h(tmp_path, monkeypatch):
    from packages.automation.authorization import _MARKER, _SCOPE_KEY

    async def delegated(scope, receive, send):
        scope[_SCOPE_KEY] = _MARKER
        await server.app(scope, receive, send)

    def forbidden(*args, **kwargs):
        pytest.fail("removed address reached lifecycle handoff")

    monkeypatch.setattr(server, "_PROJECT_DIR", tmp_path)
    monkeypatch.setattr(server, "_canonical_lifecycle_handoff", forbidden)
    return SimpleNamespace(app=delegated)


def test_tcp_removed_addresses_refuse_without_writing(tcp, tmp_path):
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with httpx.Client(base_url=tcp) as client:
        for method, path, status in WRITES:
            response = client.request(method, path, json={"operation": "restart"},
                                      headers={"x-pan-expected-pid": "stale"})
            assert response.status_code == status, response.text
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_stdin_console_submits_fixed_lifecycle_operation(monkeypatch):
    import io
    from packages.core import console_exit
    requests = []

    def respond(request, **kwargs):
        requests.append(request)
        return io.BytesIO(b'{"ok": true, "accepted": true}')

    monkeypatch.setattr(console_exit, "urlopen", respond)
    monkeypatch.setattr(console_exit.threading.Thread, "start", lambda self: None)
    console = console_exit.start_console_exit(43210, lambda: "offline",
                                              stream=SimpleNamespace(isatty=lambda: True))
    assert console.submit({"options": {"preserveRunningIllegal": True}})["accepted"]
    assert len(requests) == 1
    assert requests[0].full_url == "http://127.0.0.1:43210/api/system-actions/main-lifecycle"
    assert json.loads(requests[0].data) == {"operation": "exit", "options": {"preserveRunningIllegal": True}}


def test_managed_console_submits_fixed_lifecycle_operation(monkeypatch):
    import os
    if os.name != "nt":
        pytest.skip("Windows managed host console")
    from packages.core import cli, console_exit, host_console
    consoles, requests = [], []
    original = console_exit.ConsoleExit

    def capture(*args, **kwargs):
        console = original(*args, **kwargs)
        consoles.append(console)
        return console

    monkeypatch.setattr(console_exit, "ConsoleExit", capture)
    monkeypatch.setattr(host_console.threading.Thread, "start", lambda self: None)
    monkeypatch.setattr(cli, "request", lambda *args: requests.append(args) or {"accepted": True})
    host = host_console.HostConsole()
    host.managed = True
    host.start_input(43210, lambda: "offline")
    assert consoles[0].submit({"options": {"markRunningSessionsOffline": False}})["accepted"]
    assert requests == [(43210, "/api/system-actions/main-lifecycle",
                         {"operation": "exit", "options": {"markRunningSessionsOffline": False}})]


def test_production_http_leaf_snapshot():
    routes = [r for r in effective_routes(server.app) if hasattr(r, "methods") and
              (r.path.startswith(("/api/background-jobs", "/api/session-message-jobs")) or
               r.path in {"/api/main/exit", "/api/main/restart", "/api/main/exit/status",
                          "/api/main/restart/status", "/api/system-actions/main-lifecycle"})]
    rows = sorted([[method, r.path, r.endpoint.__name__] for r in routes for method in r.methods])
    assert rows == json.loads((ROOT / "tests/fixtures/r6_http_write_routes.json").read_text())
    assert all(name not in vars(server) for name in REMOVED)


def test_no_production_python_reference_to_removed_http_handlers():
    for directory in ("packages", "scripts"):
        for path in (ROOT / directory).rglob("*.py"):
            if {"node_modules", "__pycache__"} & set(path.parts):
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                name = (node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute)
                        else node.name if isinstance(node, ast.alias) else None)
                assert name not in REMOVED, (path.relative_to(ROOT), node.lineno, name)
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and
                        node.func.id == "getattr" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)):
                    assert node.args[1].value not in REMOVED, (path.relative_to(ROOT), node.lineno)


@pytest.mark.parametrize("method,path,status", WRITES)
@pytest.mark.parametrize("payload", [{}, {"operation": "restart", "argv": ["must-not-run"],
                                        "options": {"drain": True}}])
def test_old_http_writes_have_no_store_run_lease_or_process_effect(tmp_path, monkeypatch, method, path, status, payload):
    from packages.automation.authorization import _MARKER, _SCOPE_KEY
    from packages.jobs.independent_executor import ExecutionStore
    from packages.scheduler.creators import CreatorStore
    from packages.jobs.runtime_owner import OwnerFence

    def forbidden(*args, **kwargs):
        pytest.fail("retired HTTP write reached a store, lease or lifecycle effect")

    monkeypatch.setattr(server, "_PROJECT_DIR", tmp_path)
    monkeypatch.setenv("PAN_BACKGROUND_JOBS_DIR", str(tmp_path / "registry"))
    monkeypatch.setattr(server, "_canonical_lifecycle_handoff", forbidden)
    monkeypatch.setattr(server.background_jobs, "create_service_job", forbidden)
    for cls in (ExecutionStore, CreatorStore, OwnerFence):
        monkeypatch.setattr(cls, "__init__", forbidden)

    async def scoped(scope, receive, send):
        scope[_SCOPE_KEY] = _MARKER
        await server.app(scope, receive, send)

    async def call():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=scoped), base_url="http://ipc") as client:
            return await client.request(method, path, json=payload)

    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    response = asyncio.run(call())
    assert response.status_code == status, response.text
    if status == 410:
        assert response.json()["detail"]["code"] == "retired"
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("operation", ["exit", "restart"])
def test_original_lifecycle_receipt_remains_queryable_after_host_memory_loss(tmp_path, monkeypatch, operation):
    from packages.core import lifecycle_action, platform
    monkeypatch.setattr(platform, "supports", lambda capability: True)
    launches = []
    admitted = asyncio.run(lifecycle_action.submit_protected(
        root=tmp_path, operation=operation, port=43210, old_pid=41,
        old_pid_created_at=12.5, request_id="request_original_" + operation,
        launch=lambda *args: launches.append(args)))
    monkeypatch.setattr(server, "_canonical_lifecycle_pending", {})
    from packages.jobs.independent_executor import ExecutionStore
    store = ExecutionStore(lifecycle_action.receipt_root(tmp_path))
    doc = store.get(admitted["effectId"])
    claim = store.claim(doc["effectId"], doc["attempt"], {"pid": 42, "birth": 13.5})["claim"]
    terminal = {"effectKey": doc["effectKey"], "outcome": "succeeded", "receipt": {"phase": "offline" if operation == "exit" else "ready"}}
    store.finish(doc["effectId"], doc["attempt"], claim, terminal)
    receipt = lifecycle_action.wait_for_receipt(root=tmp_path, effect_id=admitted["effectId"],
                                               operation=operation, timeout=1)
    assert receipt == terminal
    assert len(launches) == 1
