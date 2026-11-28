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


def _access_token(private_key):
    now = int(time.time())
    return pyjwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "test-user", "iat": now, "exp": now + 300},
        private_key,
        algorithm="RS256",
        headers={'kid': 'owned-test-key'},
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
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    jwk = json.loads(pyjwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid='owned-test-key', use='sig', alg='RS256')

    class Keys(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == '/cdn-cgi/access/certs'
            raw = json.dumps({'keys': [jwk]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    jwks = ThreadingHTTPServer(('127.0.0.1', 0), Keys)
    assert jwks.server_port not in {8767, 8768, 9742}
    jwks_thread = threading.Thread(target=jwks.serve_forever, daemon=True)
    jwks_thread.start()
    key_client = pyjwt.PyJWKClient
    mp.setattr(
        mcp_gateway.jwt, "PyJWKClient",
        lambda *args, **kwargs: key_client(
            f'http://127.0.0.1:{jwks.server_port}/cdn-cgi/access/certs', timeout=5),
    )
    saved_env = {name: os.environ.get(name) for name in (
        "PAN_API_URL", "PAN_AGENT_SESSION_ID", "PAN_AGENT_SESSION_TITLE", "PAN_PLUGIN_IPC")}
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
        # Expose only the fixture JWKS address, never a production override.
        mp.setenv('PAN_TEST_REMOTE_JWKS_URL', f'http://127.0.0.1:{jwks.server_port}/cdn-cgi/access/certs')
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
        jwks.shutdown()
        jwks.server_close()
        jwks_thread.join(5)
        assert not jwks_thread.is_alive()


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


def test_request_identity_is_independent_and_reset(access_key):
    from packages.mcp.server import _remote_access_token
    observed = []
    barrier = asyncio.Event()

    async def endpoint(scope, receive, send):
        before = _remote_access_token.get()
        observed.append(before)
        if len(observed) == 2:
            barrier.set()
        await barrier.wait()
        assert _remote_access_token.get() == before
        if scope['path'] == '/raise':
            raise RuntimeError('owned endpoint failure')

    tokens = [_access_token(access_key), pyjwt.encode({
        'iss': ISSUER, 'aud': AUDIENCE, 'sub': 'second-user',
        'iat': int(time.time()), 'exp': int(time.time()) + 300},
        access_key, algorithm='RS256', headers={'kid': 'owned-test-key'})]

    class LocalKeys:
        def get_signing_key_from_jwt(self, token):
            from types import SimpleNamespace
            return SimpleNamespace(key=access_key.public_key())

    guard = mcp_gateway.AccessGuard(endpoint, ISSUER, AUDIENCE, key_client=LocalKeys())
    async def run():
        async def call(token, path):
            try:
                await guard({'type': 'http', 'path': path,
                             'headers': [(TOKEN_HEADER.encode(), token.encode())]}, None, None)
            except RuntimeError:
                assert path == '/raise'
            assert _remote_access_token.get() is None
        await asyncio.gather(call(tokens[0], '/ok'), call(tokens[1], '/raise'))
    asyncio.run(run())
    assert set(observed) == set(tokens)
    assert _remote_access_token.get() is None


def test_remote_transport_unknown_is_not_replayed(monkeypatch):
    from packages.mcp import server
    calls = []
    def fail(message):
        calls.append(message)
        raise TimeoutError('unconfirmed')
    monkeypatch.setattr('packages.plugins.ipc.request', fail)
    monkeypatch.setenv('PAN_AGENT_SESSION_ID', 'inherited-local')
    monkeypatch.setenv('PAN_AGENT_SESSION_TITLE', 'inherited title')
    binding = server._remote_access_token.set('request-token')
    try:
        result = server.agent_send('target', 'original', 'stable-txn')
        assert result['status'] == 'unknown' and result['retryable'] is False
        assert len(calls) == 1
        assert calls[0] == {'operation': 'remote_http', 'session_id': '',
                            'access_token': 'request-token', 'method': 'POST', 'path': '/api/send',
                            'body': {'sessionId': 'target', 'text': 'original', 'source': 'agent',
                                     'force': True, 'clientMessageId': 'stable-txn'}}
        assert server._terminal_api('list', {})['error']['code'] == 'local-stdio-required'
        assert server._self_queue_api('read')['error'] == 'local-stdio-required'
    finally:
        server._remote_access_token.reset(binding)


def test_gateway_discards_inherited_ipc_and_uses_checkout_locator(tmp_path):
    import subprocess
    code = """
import json, os, sys
from pathlib import Path
from packages.remote.mcp_gateway import create_app
root = Path(sys.argv[1])
config = {'access_issuer': 'https://team.cloudflareaccess.com',
          'access_audience': 'test-aud', 'public_hostname': 'mcp.example.com',
          'pan_project_root': str(root), 'pan_api_url': 'http://127.0.0.1:9'}
create_app(config)
assert 'PAN_PLUGIN_IPC' not in os.environ
assert 'PAN_AGENT_SESSION_ID' not in os.environ
(root / 'data').mkdir()
(root / 'data/core-ipc.json').write_text(json.dumps({'address': 'owned-test-locator'}))
create_app(config)
assert os.environ['PAN_PLUGIN_IPC'] == 'owned-test-locator'
"""
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], cwd=ROOT,
                            env={**os.environ, 'PAN_PLUGIN_IPC': 'foreign-test-locator',
                                 'PAN_AGENT_SESSION_ID': 'foreign-test-session'},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_remote_tools_list_equals_stdio_registry(remote_gateway):
    url, token = remote_gateway
    remote = {tool.name: tool for tool in _mcp_list_tools(url, token)}
    stdio = {tool.name: tool for tool in asyncio.run(stdio_mcp.list_tools())}
    assert set(remote) == set(stdio)
    assert {name for name in remote if name.startswith("agent_send")
            and name != "agent_send_many"} == {"agent_send", "agent_send_to_queue"}

    for name in sorted(stdio):
        assert remote[name].description == stdio[name].description, name
        assert remote[name].inputSchema == stdio[name].inputSchema, name

    remote_force = remote["agent_send"]
    stdio_force = stdio["agent_send"]
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


@pytest.fixture
def owned_remote_core(remote_gateway, tmp_path, monkeypatch):
    """Production Core with synthetic credentials and private profile/ports."""
    from packages.automation.auth import UserAuth
    import test_real_queue_http_e2e as real

    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    assert port not in {8767, 8768, 9742}
    monkeypatch.setattr(real, 'PORT', port)
    monkeypatch.setattr(real, 'SERVER_LAUNCHER', ROOT / 'tests/support/automation_http_server.py')
    root = tmp_path / 'owned-remote-profile'
    root.mkdir()
    (root / 'config.json').write_text(json.dumps({
        'plugin_manifests': [str(ROOT / 'manifest.json'), str(ROOT / 'packages/mcp/manifest.json')],
        'qq': {'enabled': False}, 'wechat': {'enabled': False}, 'remote': {'enabled': False},
        'tailscale': {'startupEnabled': False}, 'lan': {'startupEnabled': False},
        'mcp_remote': {'access_issuer': ISSUER, 'access_audience': AUDIENCE,
                       'public_hostname': 'mcp.example.com'},
    }), encoding='utf-8')
    password = 'Owned-remote-test-only-123!'
    UserAuth(root / 'automation').initialize(password)
    runtime = real._PanRuntime(root)
    runtime.start()
    try:
        with httpx.Client(base_url=runtime.base_url, trust_env=False) as client:
            login = client.post('/api/automation/login', json={'key': password},
                                headers={'Origin': runtime.base_url})
            assert login.status_code == 200
            bearer = {'Authorization': 'Bearer ' + login.json()['token']}
            created = client.post('/api/sessions', headers=bearer, json={
                'name': 'Owned-remote-read-target', 'adapter': 'cbc',
                'model': 'deepseek-v4-flash', 'mcpServers': []})
            assert created.status_code == 200, created.text
            assert 'id' in created.json(), created.text
            sid = created.json()['id']
            assert client.get('/api/sessions/' + sid, headers=bearer).status_code == 200
            client.cookies.clear()
            assert client.get('/api/sessions', headers={
                'X-Pan-Queue-Caller': sid, 'X-Pan-Terminal-Caller': sid,
                'cf-access-jwt-assertion': remote_gateway[1]}).status_code == 401
        monkeypatch.setattr('packages.mcp.server._pan_api_url', runtime.base_url)
        address = json.loads((root / 'core-ipc.json').read_text())['address']
        monkeypatch.setenv('PAN_PLUGIN_IPC', address)
        yield runtime, sid, bearer, address
    finally:
        runtime.stop()


def test_remote_authorized_reads_reach_owned_core(remote_gateway, owned_remote_core, access_key):
    """Do not replace _api: metadata parity alone cannot prove dispatch works."""
    runtime, sid, bearer, address = owned_remote_core
    url, token = remote_gateway
    assert 'session_list' in {tool.name for tool in _mcp_list_tools(url, token)}
    results = {name: _payload(_mcp_call(url, token, name, args)) for name, args in (
        ('session_list', {}), ('session_get', {'session_id': sid}))}
    summaries = {}
    for name, value in results.items():
        if isinstance(value, dict) and set(value) == {'result'}:
            value = value['result']
        summaries[name] = ({'ok': False, 'error': value['error']}
                           if isinstance(value, dict) and value.get('error') else
                           {'ok': True, 'synthetic_target_present': sid in json.dumps(value)})
    evidence = {'baseline': 'd5cd05e48da3b43727f6a4e8108adb4dda1bf49b',
                'gateway': url, 'core': runtime.base_url, 'pids': runtime.pan_pids,
                'synthetic_access': True, 'results': summaries}
    (ROOT / 'artifacts/remote-core-read.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print('REMOTE_CORE_READ ' + json.dumps(evidence))
    listed = results['session_list']
    if isinstance(listed, dict) and set(listed) == {'result'}:
        listed = listed['result']
    assert not (isinstance(listed, dict) and listed.get('error')), results
    assert not any(isinstance(value, dict) and value.get('error') for value in results.values()), results
    assert sid in json.dumps(listed)
    assert results['session_get']['id'] == sid
    # Allowed writes retain the original business receipt and queue gates.
    update = _payload(_mcp_call(url, token, 'session_update', {
        'session_id': sid, 'game_id': 'remote-owned-game'}))
    assert not update.get('error'), update
    with httpx.Client(base_url=runtime.base_url, trust_env=False) as client:
        assert client.get('/api/sessions/' + sid, headers=bearer).json()['gameId'] == 'remote-owned-game'
        paused = client.patch('/api/sessions/' + sid + '/queue/paused',
                              headers=bearer, json={'paused': True})
        assert paused.status_code == 200, paused.text
    sent = _payload(_mcp_call(url, token, 'agent_send', {
        'session_id': sid, 'text': 'owned remote queued input', 'client_message_id': 'remote-owned-txn'}))
    assert not sent.get('error'), sent
    assert sent.get('status') == 'queued', sent
    assert sent.get('queuePaused') is True and sent.get('pendingSpawn') is False, sent
    with httpx.Client(base_url=runtime.base_url, trust_env=False) as client:
        queue = client.get('/api/sessions/' + sid + '/queue', headers=bearer).json()
    assert 'owned remote queued input' in json.dumps(queue), queue
    assert '////by agent' not in json.dumps(queue), queue
    assert not runtime.fake_records(), 'paused write must not spawn a CLI'
    # Exercise Core's own authorization, rather than only the Gateway gate.
    from packages.plugins.ipc import request
    def core(method, path, body=None, credential=token):
        return request({'operation': 'remote_http', 'session_id': '',
                        'access_token': credential, 'method': method, 'path': path, 'body': body},
                       address=address)
    for method, path, body in [
        ('GET', '/api/settings/ui', None),
        ('POST', '/api/self-queue/read', {}),
        ('POST', '/api/terminal-tools/list', {}),
        ('POST', '/api/claim', {'sessionId': sid, 'managerId': sid}),
        ('POST', '/api/send', {'sessionId': sid, 'text': 'forged', 'sourceSessionId': sid}),
        ('GET', '/api/jobs/action-jobs', None),
    ]:
        assert core(method, path, body)['error']['code'] == 'permission_denied', path
    assert core('GET', '/api/sessions', credential='invalid')['error']['code'] == 'permission_denied'
    for claim, value in [('aud', 'other-application'), ('iss', 'https://other.cloudflareaccess.com'), ('exp', 1)]:
        claims = {'iss': ISSUER, 'aud': AUDIENCE, 'sub': 'owned-synthetic',
                  'iat': int(time.time()), 'exp': int(time.time()) + 300}
        claims[claim] = value
        wrong = pyjwt.encode(claims, access_key, algorithm='RS256', headers={'kid': 'owned-test-key'})
        assert core('GET', '/api/sessions', credential=wrong)['error']['code'] == 'permission_denied'
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert core('GET', '/api/sessions', credential=_access_token(other_key))['error']['code'] == 'permission_denied'
    policy_path = runtime.data_root / 'config.json'
    original_policy = policy_path.read_text(encoding='utf-8')
    changed = json.loads(original_policy)
    changed['mcp_remote']['access_audience'] = 'different-core-scope'
    try:
        policy_path.write_text(json.dumps(changed), encoding='utf-8')
        refused = _payload(_mcp_call(url, token, 'session_get', {'session_id': sid}))
        assert refused['error']['code'] == 'permission_denied'
    finally:
        policy_path.write_text(original_policy, encoding='utf-8')
    assert core('GET', '/api/sessions')['sessions'][0]['id'] == sid
    with httpx.Client(base_url=runtime.base_url, trust_env=False) as client:
        after_denials = client.get('/api/sessions/' + sid + '/queue', headers=bearer).json()
    assert after_denials['items'] == queue['items'], 'denials must not partially enqueue input'
    assert not runtime.fake_records(), 'denials must not spawn a provider'
    from packages.mcp.server import _remote_access_token as access_token
    assert access_token.get() is None
    (ROOT / 'artifacts/remote-core-write.json').write_text(json.dumps({
        'update': {'gameId': update.get('gameId')}, 'receipt': sent,
        'queueItems': len(queue.get('items', [])), 'no_provider_spawn': True,
        'cross_scope_denied': True}), encoding='utf-8')


def test_real_stdio_retains_bound_scope(remote_gateway, owned_remote_core):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    runtime, outsider, bearer, address = owned_remote_core
    with httpx.Client(base_url=runtime.base_url, trust_env=False) as client:
        owner = client.post('/api/sessions', headers=bearer, json={
            'name': 'Owned-bound-stdio', 'adapter': 'cbc', 'model': 'deepseek-v4-flash',
            'mcpServers': ['pan'],
            'panAccess': {'restrictToManaged': True, 'canClaimUnmanaged': False}}).json()
        assert owner.get('id'), owner
    params = StdioServerParameters(command=sys.executable, args=['-m', 'packages.mcp.server'],
        cwd=str(ROOT), env={**{k: v for k, v in os.environ.items() if not k.startswith('PAN_')},
            'PAN_PLUGIN_IPC': address, 'PAN_AGENT_SESSION_ID': owner['id'],
            'PAN_API_URL': runtime.base_url, 'PYTHONPATH': str(ROOT)})
    async def run():
        async with stdio_client(params) as streams, ClientSession(*streams) as client:
            await client.initialize()
            listing = _payload(await client.call_tool('session_list', {}))
            assert owner['id'] in json.dumps(listing)
            assert outsider not in json.dumps(listing)
            assert _payload(await client.call_tool('session_get', {'session_id': owner['id']}))['id'] == owner['id']
            denied = _payload(await client.call_tool('session_get', {'session_id': outsider}))
            assert denied['error']['code'] == 'permission_denied'
            url, token = remote_gateway
            remote = await asyncio.to_thread(_mcp_call, url, token, 'session_get', {'session_id': outsider})
            assert _payload(remote)['id'] == outsider
            denied_after_remote = _payload(await client.call_tool('session_get', {'session_id': outsider}))
            assert denied_after_remote['error']['code'] == 'permission_denied'
    asyncio.run(run())


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
    result = _mcp_call(url, token, "agent_send", {
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
    result = _mcp_call(url, token, "agent_send", {
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
    result = _mcp_call(url, token, "agent_send", {
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
    ("agent_restart_delayed", {"session_id"}),
    ("agent_restart_delayed_status", {"session_id"}),
    ("report_consume", {"item_id"}),
    ("action_drafts", {"operation", "kind", "item", "expected_revision"}),
    ("agent_send", {"session_id", "text", "client_message_id"}),
    ("agent_send_to_queue", {"session_id", "text"}),
])
def test_current_catalog_schema(remote_gateway, name, fields):
    url, token = remote_gateway
    tools = {tool.name: tool for tool in _mcp_list_tools(url, token)}
    assert fields <= tools[name].inputSchema["properties"].keys()
    if name in {"agent_send", "agent_send_to_queue"}:
        assert "session_id" in tools[name].inputSchema["required"]


def test_remote_catalog_excludes_retired_job_writes(remote_gateway):
    from packages.mcp.server import RETIRED_MCP_TOOLS
    url, token = remote_gateway
    names = {tool.name for tool in _mcp_list_tools(url, token)}
    assert not names & set(RETIRED_MCP_TOOLS)


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
    token = pyjwt.encode(claims, key, algorithm="RS256", headers={'kid': 'owned-test-key'})
    assert httpx.post(url, json={}, headers={TOKEN_HEADER: token}).status_code == 401


def test_access_rejects_untrusted_signature(remote_gateway):
    url, _ = remote_gateway
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert httpx.post(url, json={}, headers={TOKEN_HEADER: _access_token(other)}).status_code == 401


@pytest.mark.parametrize('name,arguments', [
    ('reminding_card', {'operation': 'list_pending'}),
    ('locker_message', {'operation': 'get', 'message_id': 'owned-test-id'}),
    ('interaction_card', {'operation': 'list_pending'}),
])
def test_remote_local_producers_never_reach_core(monkeypatch, remote_gateway, name, arguments):
    monkeypatch.setattr('packages.mcp.server._api', lambda *a, **k: pytest.fail('local producer reached Core'))
    url, token = remote_gateway
    result = _payload(_mcp_call(url, token, name, arguments))
    assert result == {'ok': False, 'error': 'local_stdio_caller_required'}


@pytest.mark.parametrize("name,arguments", [
    ("agent_restart_delayed", {"session_id": "ses_unmanaged"}),
    ("agent_restart_delayed_status", {"session_id": "ses_unmanaged"}),
    ("session_get", {"session_id": "ses_unmanaged"}),
    ("session_message_get", {"session_id": "ses_unmanaged", "message_id": "pan:synthetic"}),
    ("session_history_search", {"session_id": "ses_unmanaged", "query": "private"}),
    ("agent_send", {"session_id": "ses_unmanaged", "text": "private", "client_message_id": "original-id"}),
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


def test_bound_stdio_delayed_restart_real_http(monkeypatch, tmp_path):
    import test_real_queue_http_e2e as real
    from test_delayed_restart_locked_queue_http import _stop_owned
    from types import SimpleNamespace
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    monkeypatch.setattr(real, 'PORT', port)
    runtime = real._PanRuntime(tmp_path / 'remote-delayed-runtime', authenticated=True)
    runtime.start()
    try:
        monkeypatch.setattr('packages.mcp.server._pan_api_url', runtime.base_url)
        session = real._create(runtime, 'remote-delayed-offline')
        sid = session['id']
        runtime.request('PATCH',f'/api/sessions/{sid}',json={'pluginBindings':[
            {'plugin_id':'pan','mode':'standalone','enabled':True}]})
        async def bound_stdio():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            locator=json.loads((runtime.data_root/'core-ipc.json').read_text(encoding='utf-8'))
            params=StdioServerParameters(command=sys.executable,args=['-m','packages.mcp.server'],cwd=str(ROOT),
                env={**os.environ,'PAN_AGENT_SESSION_ID':sid,'PAN_PLUGIN_IPC':locator['address']})
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    rows=[]
                    for name in ('agent_restart_delayed_status','agent_restart_delayed'):
                        result=await client.call_tool(name,{'session_id':sid})
                        assert not result.isError
                        rows.append(_payload(result))
                    return rows
        status,rejected=asyncio.run(bound_stdio())
        assert status == {'ok': True, 'delayedRestart': None}
        assert rejected['ok'] is False and 'No live Worker' in rejected['error']
        assert runtime.request('GET', f'/api/sessions/{sid}/worker/delayed-restart')['delayedRestart'] is None
        assert not [row for row in runtime.fake_records() if row.get('cwd') == session['workdir']]
    finally:
        _stop_owned(runtime, SimpleNamespace(stop=lambda: None), locals().get('sid'))


def test_remote_delayed_restart_real_http(remote_gateway, owned_remote_core):
    runtime, sid, bearer, _ = owned_remote_core
    url, token = remote_gateway
    status = _payload(_mcp_call(url, token, 'agent_restart_delayed_status', {'session_id': sid}))
    assert status == {'ok': True, 'delayedRestart': None}
    rejected = _payload(_mcp_call(url, token, 'agent_restart_delayed', {'session_id': sid}))
    assert rejected['ok'] is False and 'No live Worker' in rejected['error']
    with httpx.Client(base_url=runtime.base_url, trust_env=False) as client:
        assert client.get(f'/api/sessions/{sid}/worker/delayed-restart', headers=bearer).json()['delayedRestart'] is None
    assert not runtime.fake_records()
