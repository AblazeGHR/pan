"""Real ACP, Worker, Codex wrapper and synthetic native RPC subprocess."""
import json
import os
import queue
import socket
import subprocess
import sys
import threading

import pytest

from packages.acp.local import initialize
from tests import test_real_queue_http_e2e as h


@pytest.mark.skipif(os.name != 'nt', reason='ACP dedicated transport is Windows-only')
def test_running_cancel_through_codex_native_bridge(tmp_path, monkeypatch):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    assert port not in (8767, 8768)
    monkeypatch.setattr(h, 'PORT', port)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', h.ROOT / 'tests/support/automation_http_server.py')
    monkeypatch.setenv('PAN_E2E_SYNTH_CODEX', '1')
    root, workspace, plugin = tmp_path / 'owned', tmp_path / 'workspace', tmp_path / 'plugin'
    data = root / 'data'
    data.mkdir(parents=True)
    workspace.mkdir()
    plugin.mkdir()
    (plugin / 'manifest.json').write_text(json.dumps({'session_templates':[{'name':'synthetic-codex',
        'adapter':'codex', 'model':'synthetic-model', 'mcp_mode':'never',
        'permission_mode':'read-only'}]}))
    (data / 'config.json').write_text(json.dumps({'qq':{'enabled':False}, 'wechat':{'enabled':False},
        'remote':{'enabled':False}, 'tailscale':{'startupEnabled':False}, 'lan':{'startupEnabled':False},
        'codex':{'models':['synthetic-model'], 'model':'synthetic-model', 'permission_mode':'read-only'},
        'plugin_manifests':[str(plugin)]}))
    initialize(data, 'editor', 'synthetic-codex', workspace)
    runtime, child = h._PanRuntime(data), None
    try:
        runtime.start()
        child = subprocess.Popen([sys.executable, '-m', 'packages.core.cli', '--root', str(root),
                                  'acp', '--profile', 'editor'], cwd=str(h.ROOT),
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = queue.Queue()
        def read():
            for line in child.stdout:
                output.put(json.loads(line))
        threading.Thread(target=read, daemon=True).start()
        def send(message):
            child.stdin.write((json.dumps(message) + '\n').encode()); child.stdin.flush()
        send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':1}})
        assert output.get(timeout=15)['result']['protocolVersion'] == 1
        send({'jsonrpc':'2.0','id':2,'method':'session/new','params':{'cwd':str(workspace),'mcpServers':[]}})
        sid = output.get(timeout=15)['result']['sessionId']
        send({'jsonrpc':'2.0','id':3,'method':'session/prompt',
              'params':{'sessionId':sid,'prompt':[{'type':'text','text':'hold'}]}})
        log = workspace / 'synthetic-native-rpc.jsonl'
        def started():
            return log.exists() and any(json.loads(x).get('method') == 'turn/start'
                                        for x in log.read_text().splitlines())
        h._wait_for(started, timeout=25, label='native turn start')
        send({'jsonrpc':'2.0','method':'session/cancel','params':{'sessionId':sid}})
        while True:
            message = output.get(timeout=25)
            if message.get('id') == 3:
                assert message['result']['stopReason'] == 'cancelled'
                break
        calls = [json.loads(x) for x in log.read_text().splitlines()]
        interrupts = [x for x in calls if x.get('method') == 'turn/interrupt']
        assert len(interrupts) == 1 and interrupts[0]['params']['turnId'] == 'native-1'
        send({'jsonrpc':'2.0','id':4,'method':'session/prompt',
              'params':{'sessionId':sid,'prompt':[{'type':'text','text':'permission-probe'}]}})
        while True:
            message = output.get(timeout=25)
            if message.get('method') == 'session/request_permission':
                break
        send({'jsonrpc':'2.0', 'id':message['id'], 'result':{'outcome':{
            'outcome':'selected', 'optionId':'reject_once'}}})
        while True:
            message = output.get(timeout=25)
            if message.get('id') == 4:
                assert message['result']['stopReason'] == 'end_turn'
                break
        assert json.loads((workspace / 'synthetic-approval.json').read_text()) == {'decision':'decline'}
        if os.environ.get('ACP_TCK_TEST') == '1':
            env = dict(os.environ, UV_CACHE_DIR='D:/uv/cache', PYTHONPATH=str(h.ROOT),
                       TMP=str(workspace), TEMP=str(workspace))
            cmd = ['uvx', '--from',
                   'git+https://github.com/agentclientprotocol/acp-tck@7c71c4d1af0994e73bdc100c9f7f6b98f2319cda',
                   'acp-tck', '--agent-cwd', str(workspace), '--timeout', '12', '--test-timeout', '20',
                   '--cancel-prompt', 'hold', '-k', 'test_cancel_resolves or test_no_session_update_follows or test_cancel_request_for_in_flight_prompt',
                   '--', sys.executable, '-m', 'packages.core.cli', '--root', str(root), 'acp', '--profile', 'editor']
            tck = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True, timeout=100)
            print('ACP TCK cancellation subset exit:', tck.returncode)
            print(tck.stdout[-12000:])
            print(tck.stderr[-2000:])
            assert tck.returncode == 1
            assert '3 passed' in tck.stdout
    finally:
        if child:
            child.stdin.close()
            child.wait(timeout=10)
        runtime.stop()
    assert h._port_is_free(port)
