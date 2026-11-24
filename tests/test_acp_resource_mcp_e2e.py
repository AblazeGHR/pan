"""Isolated real Pan IPC and synthetic provider/tool lifecycle."""
import json
import os
import socket
import sys
import subprocess

import pytest

from packages.acp.local import initialize, request
from tests import test_real_queue_http_e2e as h


@pytest.mark.skipif(os.name != 'nt', reason='ACP transport is Windows-only')
def test_private_environment_provisioned_via_stdin(tmp_path):
    from packages.acp.local import _read_profile
    root, workspace, plugin = tmp_path / 'owned', tmp_path / 'workspace', tmp_path / 'plugin'
    data = root / 'data'; data.mkdir(parents=True); workspace.mkdir(); plugin.mkdir()
    (plugin / 'manifest.json').write_text(json.dumps({'mcp_servers':[{
        'name':'tool', 'command':sys.executable, 'args':[]}]}))
    (data / 'config.json').write_text(json.dumps({'plugin_manifests':[str(plugin)]}))
    secret = 'synthetic-private-init'
    args = [sys.executable, '-m', 'packages.core.cli', '--root', str(root), 'acp-init',
            '--profile','editor', '--template','fixture', '--workspace',str(workspace),
            '--allow-mcp','tool', '--mcp-env-stdin']
    result = subprocess.run(args, input=json.dumps({'tool':{'SYNTH_SECRET':secret}}),
                            cwd=str(h.ROOT), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert secret not in repr(args) + result.stdout + result.stderr
    profile, _ = _read_profile(data, 'editor')
    assert profile.mcp_env == ((('SYNTH_SECRET', secret),),)
    assert secret.encode() not in (data / 'acp/editor.profile').read_bytes()
    assert secret not in (data / 'config.json').read_text()


@pytest.mark.skipif(os.name != 'nt', reason='ACP transport is Windows-only')
@pytest.mark.parametrize('native', [False, True])
def test_private_mcp_environment_is_scoped_and_revoked(tmp_path, monkeypatch, native):
    import psutil
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    assert port not in (8767, 8768)
    monkeypatch.setattr(h, 'PORT', port)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', h.ROOT / 'tests/support/automation_http_server.py')
    monkeypatch.setattr(h, 'FAKE_CLI', h.ROOT / 'tests/support/fake_acp_mcp_provider.py')
    if native:
        monkeypatch.setenv('PAN_E2E_SYNTH_CODEX', '1')
    root, workspace, plugin = tmp_path / 'owned', tmp_path / 'workspace', tmp_path / 'plugin'
    data = root / 'data'; data.mkdir(parents=True); workspace.mkdir(); plugin.mkdir()
    marker, sibling = workspace / 'private-marker', workspace / 'sibling-marker'
    tool = str(h.ROOT / 'tests/support/fake_acp_mcp_tool.py')
    args = [tool, str(marker), '--private-check']
    sibling_args = [tool, str(sibling), '--no-private-check']
    manifest = {'session_templates':[{'name':'fixture', 'adapter':'codex' if native else 'cbc',
                'model':'synthetic-model' if native else 'deepseek-v4-flash', 'mcp_mode':'optional'}], 'mcp_servers':[
                {'name':'private_tool','command':sys.executable,'args':args},
                {'name':'sibling_tool','command':sys.executable,'args':sibling_args}]}
    (plugin / 'manifest.json').write_text(json.dumps(manifest))
    (data / 'config.json').write_text(json.dumps({'qq':{'enabled':False}, 'wechat':{'enabled':False},
        'remote':{'enabled':False}, 'tailscale':{'startupEnabled':False}, 'lan':{'startupEnabled':False},
        'plugin_manifests':[str(plugin)], 'codex':{'models':['synthetic-model'], 'model':'synthetic-model'}}))
    secret = 'synthetic-only-value'
    initialize(data, 'editor', 'fixture', workspace, ['private_tool', 'sibling_tool'],
               {'private_tool':{'ACP_SYNTHETIC_SECRET':secret}})
    descriptor = {'name':'private_tool','command':sys.executable,'args':args,
                  'env':[{'name':'ACP_SYNTHETIC_SECRET','value':secret}]}
    other = {'name':'sibling_tool','command':sys.executable,'args':sibling_args,'env':[]}
    assert secret.encode() not in (data / 'acp/editor.profile').read_bytes()
    runtime = h._PanRuntime(data)
    client = None
    try:
        runtime.start()
        for bad in ({**descriptor, 'env':[]}, {**descriptor, 'env':[{'name':'ACP_SYNTHETIC_SECRET','value':'wrong'}]},
                    {**descriptor, 'args':args + ['extra']}):
            with pytest.raises(RuntimeError):
                request(data, 'editor', 'prepare', {'cwd':str(workspace), 'servers':[bad]})
        if native:
            client = subprocess.Popen([sys.executable, '-m', 'packages.core.cli', '--root', str(root),
                'acp', '--profile', 'editor'], cwd=str(h.ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)
            def call(row):
                client.stdin.write((json.dumps(row) + '\n').encode()); client.stdin.flush()
                return json.loads(client.stdout.readline())
            assert call({'jsonrpc':'2.0','id':1,'method':'initialize',
                         'params':{'protocolVersion':1}})['result']['protocolVersion'] == 1
            sid = call({'jsonrpc':'2.0','id':2,'method':'session/new','params':{
                'cwd':str(workspace),'mcpServers':[descriptor,other]}})['result']['sessionId']
            result = call({'jsonrpc':'2.0','id':3,'method':'session/prompt','params':{
                'sessionId':sid, 'prompt':[{'type':'text','text':'synthetic request'}]}})
            assert result['result']['stopReason'] == 'end_turn'
        else:
            handle = request(data, 'editor', 'prepare', {'cwd':str(workspace), 'servers':[descriptor, other]})['handle']
            request(data, 'editor', 'submit', {'handle':handle, 'turnId':'private', 'blocks':[
                {'type':'text','text':'synthetic request'}]})
        h._wait_for(lambda: marker.exists() and sibling.exists(), timeout=25, label='scoped tools start')
        pid = int(marker.with_suffix('.pid').read_text())
        process = psutil.Process(pid)
        descendant = psutil.Process(int(marker.with_suffix('.childpid').read_text()))
        assert secret not in repr(process.cmdline())
        broker = process.parent()
        assert broker is not None and secret not in repr(broker.cmdline())
        provider = broker.parent()
        assert provider is not None and secret not in repr(provider.cmdline())
        for path in (data / 'sessions').glob('*.json'):
            assert secret.encode() not in path.read_bytes()
        for path in data.rglob('*'):
            if path.is_file() and path.stat().st_size < 1024 * 1024:
                try:
                    assert secret.encode() not in path.read_bytes(), path.name
                except PermissionError:
                    pass  # Live service lock files are not evidence surfaces.
        (data / 'acp/editor.profile').unlink()
        process.wait(timeout=10)
        descendant.wait(timeout=10)
        with pytest.raises(RuntimeError):
            request(data, 'editor', 'prepare', {'cwd':str(workspace), 'servers':[descriptor]})
    finally:
        if client:
            client.stdin.close()
            client.wait(timeout=10)
        runtime.stop()
    assert h._port_is_free(port)


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
