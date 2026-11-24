"""Isolated real Pan IPC and synthetic provider/tool lifecycle."""
import json
import os
import socket
import sys

import pytest

from packages.acp.local import initialize, request
from tests import test_real_queue_http_e2e as h


@pytest.mark.skipif(os.name != 'nt', reason='ACP transport is Windows-only')
def test_authorized_resource_and_manifest_mcp(tmp_path, monkeypatch):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    assert port not in (8767, 8768)
    monkeypatch.setattr(h, 'PORT', port)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', h.ROOT / 'tests/support/automation_http_server.py')
    monkeypatch.setattr(h, 'FAKE_CLI', h.ROOT / 'tests/support/fake_acp_mcp_provider.py')
    root, workspace, plugin = tmp_path / 'owned', tmp_path / 'workspace', tmp_path / 'plugin'
    data = root / 'data'; data.mkdir(parents=True); workspace.mkdir(); plugin.mkdir()
    marker = workspace / 'tool-marker'
    tool = str(h.ROOT / 'tests/support/fake_acp_mcp_tool.py')
    args = [tool, str(marker)]
    (plugin / 'manifest.json').write_text(json.dumps({'session_templates':[{'name':'fixture',
        'adapter':'cbc', 'model':'deepseek-v4-flash', 'mcp_mode':'optional'}],
        'mcp_servers':[{'name':'fixture_tool', 'command':sys.executable, 'args':args}]}))
    (data / 'config.json').write_text(json.dumps({'qq':{'enabled':False}, 'wechat':{'enabled':False},
        'remote':{'enabled':False}, 'tailscale':{'startupEnabled':False}, 'lan':{'startupEnabled':False},
        'plugin_manifests':[str(plugin)]}))
    initialize(data, 'editor', 'fixture', workspace, ['fixture_tool'])
    paper = workspace / 'paper.txt'; paper.write_text('SYNTHETIC PRIVATE CONTENT')
    descriptor = {'name':'fixture_tool','command':sys.executable,'args':args,'env':[]}
    runtime = h._PanRuntime(data)
    try:
        runtime.start()
        for bad in ({**descriptor,'args':args + ['extra']}, {**descriptor,'command':sys.executable + '.bad'},
                    {**descriptor,'env':[{'name':'PASSWORD','value':'secret'}]}):
            with pytest.raises(RuntimeError):
                request(data, 'editor', 'prepare', {'cwd':str(workspace), 'servers':[bad]})
        handle = request(data, 'editor', 'prepare', {'cwd':str(workspace), 'servers':[descriptor]})['handle']
        request(data, 'editor', 'submit', {'handle':handle, 'turnId':'resource-1', 'blocks':[
            {'type':'text','text':'Read this'}, {'type':'resource_link','uri':paper.as_uri(),'name':'paper.txt'}]})
        h._wait_for(lambda: marker.exists() and marker.read_text() == 'started', timeout=25, label='owned MCP start')
        def done():
            response = request(data, 'editor', 'poll', {'handle':handle, 'turnId':'resource-1', 'cursor':0})
            return response if response['status'] else None
        assert h._wait_for(done, timeout=25, label='resource turn')['status'] == 'done'
        records = h._received(runtime, cwd=str(workspace), text=lambda t:'paper.txt' in t)
        assert len(records) == 1 and str(paper.resolve()) in records[0]['text']
        assert 'SYNTHETIC PRIVATE CONTENT' not in records[0]['text']
        outside = tmp_path / 'outside-secret.txt'; outside.write_text('DO NOT READ')
        other = request(data, 'editor', 'prepare', {'cwd':str(workspace)})['handle']
        request(data, 'editor', 'submit', {'handle':other, 'turnId':'outside', 'blocks':[
            {'type':'text','text':'question'}, {'type':'resource_link','uri':outside.as_uri(),'name':'sensitive-name'}]})
        def outside_done():
            response = request(data, 'editor', 'poll', {'handle':other,'turnId':'outside','cursor':0})
            return response if response['status'] else None
        assert h._wait_for(outside_done, timeout=25, label='unavailable resource')['status'] == 'done'
        rows = h._received(runtime, cwd=str(workspace), text=lambda t:'[Resource unavailable]' in t)
        assert len(rows) == 1 and str(outside) not in rows[0]['text'] and 'DO NOT READ' not in rows[0]['text']
    finally:
        runtime.stop()
    h._wait_for(lambda: marker.exists() and marker.read_text() == 'closed', timeout=10, label='owned MCP cleanup')
    assert h._port_is_free(port)
