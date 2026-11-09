"""Actual stdio initialize/list/call; retained tools never regain write handlers."""
import asyncio
import json
import os
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from packages.mcp.server import RETIRED_MCP_TOOLS, RETAINED_LEGACY_MCP_TOOLS
from tests.test_authorization_mcp import harness, close, grants, job_definition


def test_retired_names_actual_stdio_boundary_and_authorized_canonical_path(tmp_path):
    asyncio.run(exercise(tmp_path))


async def exercise(tmp_path):
    h = harness(tmp_path)
    from packages.core import worker
    before_workers = set(worker.workers)
    messages = []
    original = h.bridge.runtime.handle
    async def record(message):
        messages.append(message)
        return await original(message)
    h.bridge.runtime.handle = record
    http_calls = []
    class Sentinel(BaseHTTPRequestHandler):
        def do_GET(self):
            http_calls.append(self.path)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{}')
        def log_message(self, *args): pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), Sentinel)
    assert http.server_port not in {8767, 8768}
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    env = {**os.environ, 'PAN_PLUGIN_IPC': h.bridge.address,
           'PAN_AGENT_SESSION_ID': h.session.id, 'PAN_API_URL': f'http://127.0.0.1:{http.server_port}'}
    proc = await asyncio.create_subprocess_exec(sys.executable, '-m', 'packages.mcp.server',
        cwd=str(Path.cwd()), env=env, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=2 * 1024 * 1024)
    serial = 0
    async def rpc(method, params, notification=False):
        nonlocal serial
        serial += 1
        message = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if not notification: message['id'] = serial
        proc.stdin.write((json.dumps(message) + '\n').encode())
        await proc.stdin.drain()
        if notification: return
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
            assert line, 'MCP exited before response'
            response = json.loads(line)
            if response.get('id') == serial: return response
    async def call(name, arguments=None):
        return await rpc('tools/call', {'name': name, 'arguments': arguments or {}})
    def data(response):
        result = response['result']
        return result.get('structuredContent') or json.loads(result['content'][0]['text'])
    try:
        initialized = await rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
            'clientInfo': {'name': 'owned-retirement-test', 'version': '1'}})
        assert initialized['result']['serverInfo']['name'] == 'Pan'
        await rpc('notifications/initialized', {}, notification=True)
        listed = await rpc('tools/list', {})
        names = {t['name'] for t in listed['result']['tools']}
        assert not set(RETIRED_MCP_TOOLS) & names
        assert set(RETAINED_LEGACY_MCP_TOOLS) <= names
        assert {'job_create', 'job_run', 'job_operation_find'} <= names
        unknown_before = await call('owned-not-a-tool')
        assert unknown_before['result']['isError'] is True
        assert 'Unknown tool' in unknown_before['result']['content'][0]['text']
        before_ipc = len(messages)
        with h.service.transaction(readonly=True) as db:
            before_writes = db.execute('SELECT count(*) FROM final_operations').fetchone()[0]
        # Retired names are removed from the public registry entirely. A
        # direct stdio call therefore fails as unknown before any legacy
        # writer, executor, or cancellation path can run.
        for name in RETIRED_MCP_TOOLS:
            response = await call(name, {'session_id': 'spoof', 'request_key': 'never-write', 'command': 'must not execute'})
            assert response['result']['isError'] is True
            if response['result'].get('structuredContent'):
                value = data(response)
                assert value['error']['code'] == 'capability_unsupported'
                assert value['error']['tool'] == name
                assert 'request_key' in value['error']['guidance'] or 'requestKey' in value['error']['guidance']
            else:
                assert response['result']['content'][0]['text'] == f'Unknown tool: {name}'
        assert len(messages) == before_ipc, 'retired calls must not reach HTTP/IPC/effect handlers'
        assert http_calls == [], 'retired calls must not touch the actual owned HTTP endpoint'
        with h.service.transaction(readonly=True) as db:
            assert db.execute('SELECT count(*) FROM final_operations').fetchone()[0] == before_writes
        assert h.host.provider.calls == []
        malformed = await rpc('tools/call', {'name': 'scheduler_create', 'arguments': ['invalid-shape']})
        assert 'error' in malformed or malformed.get('result', {}).get('isError') is True
        assert len(messages) == before_ipc
        assert http_calls == []
        unknown_after = await call('owned-not-a-tool')
        assert unknown_after['result'] == unknown_before['result']
        denied = data(await call('job_list'))
        assert denied['error']['code'] == 'authorization_denied'
        h.svc.auth.change_permissions(grants(), 0)
        assert data(await call('job_list'))['items'] == []
        bad = await call('job_create', {'definition': {}, 'request_key': 12})
        assert bad['result']['isError'] is True
        assert data(await call('job_list'))['items'] == []
        created = data(await call('job_create', {'definition': job_definition(h.action), 'request_key': 'owned-create'}))
        assert created['status'] == 'completed', created
        job = created['result']['object']
        found = data(await call('job_operation_find', {'operation': 'create', 'request_key': 'owned-create'}))
        assert found['operationId'] == created['operationId']
        assert data(await call('job_get', {'object_id': job['id']}))['id'] == job['id']
        # Retained reads/cancel still traverse the existing authorized IPC bridge.
        for name, args in [('agent_background_list', {}), ('agent_background_get', {'job_id': 'owned-absent'})]:
            count = len(messages)
            response = await call(name, args)
            assert response['result'].get('isError') is not True, response
            assert len(messages) > count, response
        assert set(worker.workers) == before_workers
        assert h.service.final.history(job['id'])['runs'] == []
        print('R16_STDIO=' + json.dumps({'retiredNames': len(RETIRED_MCP_TOOLS), 'retainedDiscoverable': len(RETAINED_LEGACY_MCP_TOOLS),
            'canonicalOperationIdentity': True, 'retiredIPC': 0, 'noWorkerSpawn': True, 'malformedRejected': True}))
    finally:
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=8)
        except asyncio.TimeoutError:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=8)
        await close(h)
        http.shutdown()
        http.server_close()
        thread.join(5)
        assert not thread.is_alive()
