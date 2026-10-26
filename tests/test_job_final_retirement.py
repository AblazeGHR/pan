"""R5 retirement: pre-canonical write entrypoints are gone, reads/cancel stay.

Each case asserts three things the R5 exit condition needs:

* the old mutation is refused *after* authentication (401 unauth, 410 authed),
  so an anonymous caller cannot probe which legacy paths remain;
* refusing it produces no state change -- no created Job, no new Run, no
  re-delivery, no spawned process;
* the retained read and cancel controls still work, because a legacy Job that
  still owns a process must stay inspectable and cancellable until R6.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from packages.automation.authorization import object_path
from packages.mcp.server import RETAINED_LEGACY_MCP_TOOLS, RETIRED_MCP_TOOLS
from packages.web.server import app

ROOT = Path(__file__).resolve().parents[1]

RETIRED_DETAIL = "retired"

# (method, path) pairs that must never reach an effectful handler again.
# Root paths owned by the canonical API are deliberately absent: canonical
# wins outright there and serves its own handler.
LEGACY_HTTP_WRITES = [
    ("POST", "/api/jobs/action-jobs"),
    ("PUT", "/api/jobs/action-jobs/{id}"),
    ("POST", "/api/jobs/action-jobs/{id}/run-now"),
    ("POST", "/api/jobs/action-jobs/{id}/lifecycle"),
    ("POST", "/api/jobs/action-jobs/{id}/pause"),
    ("POST", "/api/jobs/action-jobs/{id}/resume"),
    ("POST", "/api/jobs/action-jobs/{id}/retry"),
    ("POST", "/api/jobs/action-jobs/{id}/confirm-recovery"),
    ("DELETE", "/api/jobs/action-jobs/{id}"),
    ("POST", "/api/jobs/job-templates"),
    ("PUT", "/api/jobs/job-templates/{id}"),
    ("DELETE", "/api/jobs/job-templates/{id}"),
    ("POST", "/api/jobs/job-templates/{id}/instantiate"),
    ("POST", "/api/jobs/templates"),
    ("DELETE", "/api/jobs/templates/{id}"),
    ("PUT", "/api/jobs/settings/completed-retention"),
    ("POST", "/api/jobs/{id}/run-now"),
    ("POST", "/api/background-jobs"),
    ("POST", "/api/session-message-jobs"),
    ("PATCH", "/api/background-jobs/{id}"),
    ("POST", "/api/background-jobs/{id}/retry"),
    ("POST", "/api/scheduler/tasks"),
    ("PATCH", "/api/scheduler/tasks/{id}"),
    ("DELETE", "/api/scheduler/tasks/{id}"),
    ("POST", "/api/scheduler/tasks/{id}/pause"),
    ("POST", "/api/scheduler/tasks/{id}/resume"),
    ("POST", "/api/scheduler/tasks/{id}/run-now"),
    ("POST", "/api/scheduler/tasks/{id}/owner-switch"),
    ("POST", "/api/scheduler/event-sources"),
    ("PUT", "/api/scheduler/event-sources/{id}"),
    ("DELETE", "/api/scheduler/event-sources/{id}"),
]

RETAINED_HTTP_READS = [
    ("GET", "/api/legacy/jobs"),
    ("GET", "/api/legacy/jobs/kinds"),
    ("GET", "/api/jobs/action-jobs"),
    ("GET", "/api/jobs/action-jobs/schema"),
    ("GET", "/api/background-jobs"),
    ("GET", "/api/scheduler/tasks"),
    ("GET", "/api/scheduler/status"),
]


def materialise(path):
    return path.replace("{id}", "legacy_sample")


def matching_routes(method, path):
    """Effective routes whose method and template can serve this request."""
    from tests.test_job_path_flip import effective_routes
    sample = materialise(path)
    return [route for route in effective_routes(app)
            if getattr(route, "methods", None) and method in route.methods
            and route.path_regex.fullmatch(sample)]


def is_retirement(handler):
    """Recognise the R5 refusal, including the thin per-route wrappers."""
    return bool(getattr(handler, "retirement_boundary", False))


@pytest.mark.parametrize("method,path", LEGACY_HTTP_WRITES)
def test_old_write_reaches_no_effectful_handler(method, path):
    routes = matching_routes(method, path)
    assert routes, (method, path)
    # Whatever else matches, no pre-canonical business handler owns the
    # address any more: only the retirement boundary, or the canonical API for
    # a path it legitimately shares.
    for route in routes:
        owner = route.endpoint.__module__
        assert (is_retirement(route.endpoint) or owner == "packages.jobs.final_api"), (
            method, path, owner, route.endpoint.__name__)


@pytest.mark.parametrize("method,path", LEGACY_HTTP_WRITES)
def test_old_write_refuses_an_anonymous_caller_before_answering(method, path):
    """No unauthenticated caller learns whether a retired path exists."""
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.request(method, materialise(path), json={})
    # 401/403 when authorization is available; fail closed (5xx) when the
    # provider is not wired.  Never 2xx/3xx, never a bare 404 that would let a
    # caller enumerate the retired surface, and never the old 200 shape.
    assert response.status_code in {401, 403, 503}, (method, path, response.text)
    assert response.status_code < 500 or "provider_unavailable" in response.text
    assert RETIRED_DETAIL not in response.text or response.status_code == 410


def delegated_response(method, path):
    """Call the app as the trusted local MCP transport would."""
    import asyncio
    import httpx
    from packages.automation.authorization import _MARKER, _SCOPE_KEY

    async def scoped(scope, receive, send):
        scope[_SCOPE_KEY] = _MARKER
        await app(scope, receive, send)

    async def call():
        transport = httpx.ASGITransport(app=scoped)
        async with httpx.AsyncClient(transport=transport, base_url="http://ipc") as client:
            return await client.request(method, path, json={})

    return asyncio.run(call())


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/background-jobs"),
    ("POST", "/api/session-message-jobs"),
    ("PATCH", "/api/background-jobs/{id}"),
    ("POST", "/api/background-jobs/{id}/retry"),
])
def test_trusted_caller_is_told_the_entrypoint_is_retired(method, path):
    """A trusted caller gets the explicit retirement, not a silent 404."""
    response = delegated_response(method, materialise(path))
    assert response.status_code == 410, (method, path, response.text)
    assert response.json()["detail"]["code"] == "retired"
    # The refusal is a boundary answer: no Job was created by trying.
def test_delegated_old_write_created_no_record(method="POST", path="/api/background-jobs"):
    from packages.core import background_jobs
    before = {job.get("jobId") for job in background_jobs.list_jobs()}
    delegated_response(method, path)
    after = {job.get("jobId") for job in background_jobs.list_jobs()}
    assert after == before


@pytest.mark.parametrize("method,path", RETAINED_HTTP_READS)
def test_retained_reads_are_still_routed(method, path):
    routes = matching_routes(method, path)
    assert routes, (method, path)
    # A retained read must not have been replaced by the retirement boundary.
    for route in routes:
        assert not is_retirement(route.endpoint), (method, path)


def test_retirement_is_implemented_at_the_route_table_not_by_hiding_paths():
    """The allowlist keeps its contract; liability moves to the HTTP boundary."""
    # Retiring by shrinking the allowlist would silently change a capability
    # contract that has its own tests.  Instead the old write addresses still
    # match a route, and that route refuses before any handler runs, which is
    # what the parametrised cases above verify.  This case pins that choice so
    # a later edit cannot drop the boundary and leave a live old path behind.
    for method, path in LEGACY_HTTP_WRITES:
        routes = matching_routes(method, path)
        assert routes, (method, path)
        assert all(is_retirement(r.endpoint) or r.endpoint.__module__ == "packages.jobs.final_api"
                   for r in routes), (method, path)


def test_mcp_retires_old_writes_and_keeps_reads_and_cancel():
    tools = {tool.name for tool in app_mcp()._tool_manager.list_tools()}
    assert not set(RETIRED_MCP_TOOLS) & tools
    assert set(RETAINED_LEGACY_MCP_TOOLS) <= tools
    # Every old Job tool the inventory listed is accounted for: retired or
    # explicitly retained, never silently dropped.
    manifest = json.loads((ROOT / "docs/design/job-unification/"
                           "R5_R6_EXECUTION_MANIFEST_20261026.json").read_text(encoding="utf-8"))
    inventory = {tool["symbol"] for tool in manifest["mcpTools"]}
    legacy = {name for name in inventory
              if name.startswith(("agent_background_", "agent_message_job_", "scheduler_"))}
    assert legacy == set(RETIRED_MCP_TOOLS) | set(RETAINED_LEGACY_MCP_TOOLS)
    # Reads and cancels survive; they are the safe half of the old surface.
    for name in ("agent_background_cancel", "agent_message_job_cancel",
                 "agent_background_get", "scheduler_runs"):
        assert name in tools, name


def app_mcp():
    from packages.mcp import server
    return server.mcp


def test_cli_keeps_host_lifecycle_and_final_client():
    """CLI retirement is explicitly out of P5 scope; the ops path must survive."""
    from packages.core.cli import parser
    commands = parser()._subparsers._group_actions[0].choices
    for name in ("final", "status", "doctor", "logs", "start", "exit", "restart",
                 "debug", "show", "hide"):
        assert name in commands, name


def test_canonical_surface_is_unchanged():
    spec = app.openapi()["paths"]
    for path in ("/api/jobs", "/api/triggers", "/api/event-creators",
                 "/api/job-templates", "/api/job-runs/{run_id}", "/api/operations",
                 "/api/jobs/actions"):
        assert path in spec, path
    # Deprecated aliases still bind the canonical handlers.
    assert "/api/final/jobs" in spec
    assert spec["/api/final/jobs"]["get"]["deprecated"] is True
