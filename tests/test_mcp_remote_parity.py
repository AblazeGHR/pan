"""Remote gateway must serve the same FastMCP registry as local stdio.

``packages.remote.mcp_gateway`` is a transport and Cloudflare Access guard
only. It imports ``packages.mcp.server.mcp`` from the checkout named by
``pan_project_root``; tools, input schemas and docstrings are therefore the
stdio registry, not a remote copy.

These tests exercise a real streamable-HTTP gateway on an ephemeral loopback
port (never 8767/8768/9742) and fail if:

- a second tool registry or remote-only tool definition appears;
- remote ``tools/list`` drifts from stdio (description and schema);
- an unbound remote call gets a local caller identity or the ``////by agent``
  prefix;
- an unknown delivery result is retried.
"""

import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from packages.mcp.server import mcp as stdio_mcp  # noqa: E402
from packages.remote import mcp_gateway  # noqa: E402

ISSUER = "https://team.cloudflareaccess.com"
AUDIENCE = "test-aud"
TOKEN_HEADER = "cf-access-jwt-assertion"


class _FakeKey:
    def __init__(self, key):
        self.key = key


class _FakeJwkClient:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _token):
        return _FakeKey(self.public_key)


def _access_token(private_key):
    now = int(time.time())
    return pyjwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "test-user", "iat": now, "exp": now + 300},
        private_key,
        algorithm="RS256",
    )


@pytest.fixture(scope="module")
def access_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def remote_gateway(access_key):
    """Run the real gateway app once with a locally generated Access key."""
    import uvicorn

    mp = pytest.MonkeyPatch()
    key = access_key
    mp.setattr(
        mcp_gateway.jwt, "PyJWKClient",
        lambda *args, **kwargs: _FakeJwkClient(key.public_key()),
    )
    saved_env = {name: os.environ.get(name) for name in (
        "PAN_API_URL", "PAN_AGENT_SESSION_ID", "PAN_AGENT_SESSION_TITLE")}
    saved_path = list(sys.path)
    saved_bytecode = sys.dont_write_bytecode
    saved_settings = (
        stdio_mcp.settings.stateless_http,
        stdio_mcp.settings.json_response,
        stdio_mcp.settings.transport_security,
    )
    mp.setenv("PAN_AGENT_SESSION_ID", "ses_inherited_local")
    mp.setenv("PAN_AGENT_SESSION_TITLE", "inherited local caller")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    assert port not in (8767, 8768, 9742)
    server = None
    thread = None
    try:
        app = mcp_gateway.create_app({
            "access_issuer": ISSUER,
            "access_audience": AUDIENCE,
            "public_hostname": "mcp.example.com",
            "pan_project_root": str(ROOT),
            "pan_api_url": "http://127.0.0.1:9",
        })
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        if not server.started:
            raise AssertionError("gateway did not start")
        assert server.servers[0].sockets[0].getsockname()[1] == port
        evidence = ROOT / "artifacts" / "remote-http-owned.json"
        evidence.parent.mkdir(exist_ok=True)
        evidence.write_text(json.dumps({"host": "127.0.0.1", "port": port, "pid": os.getpid(), "synthetic_access_key": True}), encoding="utf-8")
        yield f"http://127.0.0.1:{port}/mcp", _access_token(key)
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(5)
            assert not thread.is_alive(), "owned gateway failed to stop"
        listener.close()
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        stdio_mcp.settings.stateless_http = saved_settings[0]
        stdio_mcp.settings.json_response = saved_settings[1]
        stdio_mcp.settings.transport_security = saved_settings[2]
        sys.path[:] = saved_path
        sys.dont_write_bytecode = saved_bytecode
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        mp.undo()


def _mcp_call(url, token, name, arguments):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def run():
        async with streamablehttp_client(
                url, headers={TOKEN_HEADER: token}) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name, arguments)

    return asyncio.run(run())


def _mcp_list_tools(url, token):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def run():
        async with streamablehttp_client(
                url, headers={TOKEN_HEADER: token}) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return (await session.list_tools()).tools

    return asyncio.run(run())


def _payload(result):
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    return json.loads(result.content[0].text)


def test_gateway_has_no_second_tool_registry():
    """The remote entry may serve tools but must not define any of its own."""
    source = (ROOT / "packages/remote/mcp_gateway.py").read_text(encoding="utf-8")
    assert "from packages.mcp.server import mcp" in source
    assert "@mcp.tool" not in source
    assert "FastMCP(" not in source
    assert "types.Tool(" not in source


def test_remote_tools_list_equals_stdio_registry(remote_gateway):
    url, token = remote_gateway
    remote = {tool.name: tool for tool in _mcp_list_tools(url, token)}
    stdio = {tool.name: tool for tool in asyncio.run(stdio_mcp.list_tools())}
    assert set(remote) == set(stdio)

    for name in sorted(stdio):
        assert remote[name].description == stdio[name].description, name
        assert remote[name].inputSchema == stdio[name].inputSchema, name

    remote_force = remote["agent_send_force"]
    stdio_force = stdio["agent_send_force"]
    assert remote_force.description == stdio_force.description
    assert remote_force.inputSchema == stdio_force.inputSchema

    description = remote_force.description or ""
    assert "Default for sending updates to the agent's current task." in description
    assert "never replay unknown input" in description
    assert "client_message_id" in description
    assert "unknown" in description
    assert "一律" not in description

    schema = remote_force.inputSchema
    assert {"session_id", "text", "client_message_id"} <= set(schema["properties"])
    assert "session_id" in schema.get("required", [])


def test_remote_access_requires_valid_jwt(remote_gateway):
    url, _token = remote_gateway
    assert httpx.post(url, json={}).status_code == 401
    assert httpx.post(
        url, json={}, headers={TOKEN_HEADER: "not-a-jwt"}).status_code == 401


def test_remote_send_force_keeps_capability_semantics(monkeypatch, remote_gateway):
    url, token = remote_gateway
    calls = []

    def fake_api(method, path, body=None, timeout=30.0):
        calls.append((method, path, body))
        return {"ok": True, "status": "accepted"}

    monkeypatch.setattr("packages.mcp.server._api", fake_api)
    assert "PAN_AGENT_SESSION_ID" not in os.environ
    result = _mcp_call(url, token, "agent_send_force", {
        "session_id": "ses_remote", "text": "urgent",
        "client_message_id": "cmid-1"})

    assert calls == [("POST", "/api/send", {
        "sessionId": "ses_remote",
        "text": "urgent",
        "source": "agent",
        "force": True,
        "clientMessageId": "cmid-1",
    })]
    assert "sourceSessionId" not in calls[0][2]
    assert _payload(result)["status"] == "accepted"


def test_remote_unknown_result_is_not_retried(monkeypatch, remote_gateway):
    url, token = remote_gateway
    calls = []

    def fake_api(method, path, body=None, timeout=30.0):
        calls.append((method, path, body))
        return {"ok": False, "status": "unknown",
                "error": {"code": "unconfirmed", "message": "no receipt"}}

    monkeypatch.setattr("packages.mcp.server._api", fake_api)
    result = _mcp_call(url, token, "agent_send_force", {
        "session_id": "ses_remote", "text": "again",
        "client_message_id": "cmid-2"})

    assert len(calls) == 1
    assert _payload(result)["status"] == "unknown"


def test_remote_call_still_runs_managed_access_gate(monkeypatch, remote_gateway):
    url, token = remote_gateway
    calls = []

    def fake_api(*args, **kwargs):
        calls.append(args)
        return {"ok": True}

    monkeypatch.setattr(
        "packages.mcp.server._check_access",
        lambda *args, **kwargs: {"ok": False, "error": "denied"})
    monkeypatch.setattr("packages.mcp.server._api", fake_api)
    result = _mcp_call(url, token, "agent_send_force", {
        "session_id": "ses_remote", "text": "denied"})

    assert calls == []
    assert _payload(result)["error"] == "denied"


def test_remote_unbound_call_cannot_use_local_session_tools(remote_gateway):
    url, token = remote_gateway
    queue = _payload(_mcp_call(url, token, "self_queue_read", {}))
    terminal = _payload(_mcp_call(url, token, "terminal_list", {}))

    assert queue["ok"] is False
    assert queue["error"] in {"caller_unavailable", "caller-required", "local-stdio-required"}
    assert terminal["ok"] is False
    assert terminal["error"]["code"] in {
        "caller-required", "caller-unverified", "local-stdio-required"}


@pytest.mark.parametrize("name,fields", [
    ("session_get", {"limit", "roles", "tool_output", "before"}),
    ("session_message_get", {"message_id", "roles", "tool_output"}),
    ("session_history_search", {"query", "cursor", "roles", "tool_output"}),
    ("model_catalog", {"adapter", "session_id"}),
    ("report_consume", {"item_id"}),
    ("action_drafts", {"operation", "kind", "item", "expected_revision"}),
    ("agent_background_start", {"name", "target_session_id", "hide_window"}),
])
def test_current_catalog_schema(remote_gateway, name, fields):
    url, token = remote_gateway
    tools = {tool.name: tool for tool in _mcp_list_tools(url, token)}
    assert fields <= tools[name].inputSchema["properties"].keys()
    if name == "agent_background_start":
        assert "name" in tools[name].inputSchema["required"]


@pytest.mark.parametrize("name,arguments", [
    ("report_consume", {}),
    ("report_consume", {"item_id": "original-report-id"}),
    ("self_queue_consume", {"item_id": "original-queue-id"}),
])
def test_unbound_report_and_queue_never_read_or_write_api(monkeypatch, remote_gateway, name, arguments):
    url, token = remote_gateway
    monkeypatch.setattr("packages.mcp.server._api", lambda *a, **k: pytest.fail("unbound local tool reached API"))
    result = _payload(_mcp_call(url, token, name, arguments))
    assert result["ok"] is False
    assert result["error"] in {"caller_unavailable", "caller-required", "local-stdio-required"}
    assert "original-report-id" not in json.dumps(result)


@pytest.mark.parametrize("claim", ["aud", "iss", "exp"])
def test_access_rejects_wrong_claims(remote_gateway, access_key, claim):
    url, _ = remote_gateway
    # Keep the trusted signature valid to isolate issuer/audience/expiry checks.
    key = access_key
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "synthetic", "iat": int(time.time()), "exp": int(time.time()) + 300}
    claims[claim] = 1 if claim == "exp" else "wrong"
    token = pyjwt.encode(claims, key, algorithm="RS256")
    assert httpx.post(url, json={}, headers={TOKEN_HEADER: token}).status_code == 401


@pytest.mark.parametrize("name,arguments", [
    ("session_get", {"session_id": "ses_unmanaged"}),
    ("session_message_get", {"session_id": "ses_unmanaged", "message_id": "pan:synthetic"}),
    ("session_history_search", {"session_id": "ses_unmanaged", "query": "private"}),
    ("agent_send_force", {"session_id": "ses_unmanaged", "text": "private", "client_message_id": "original-id"}),
])
def test_real_managed_gate_before_backend(monkeypatch, remote_gateway, name, arguments):
    url, token = remote_gateway
    # Inject a restricted caller snapshot only at the identity lookup; keep the
    # production permission gate intact to test defense at each tool entry.
    monkeypatch.setattr("packages.mcp.server._caller_identity", lambda: {
        "id": "ses_restricted", "managed": ["ses_allowed"],
        "panAccess": {"restrictToManaged": True, "canClaimUnmanaged": False}})
    monkeypatch.setattr("packages.mcp.server._api", lambda *a, **k: pytest.fail("denied request reached backend"))
    result = _payload(_mcp_call(url, token, name, arguments))
    assert result["ok"] is False
    assert result["error"]["code"] == "permission_denied"
