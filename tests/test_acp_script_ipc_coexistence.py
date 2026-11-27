"""Owned ACP and script transports retain separate authority and lifetimes."""
import json
import os
import secrets
import socket

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='ACP and script credentials require Windows owner-only DPAPI')
def test_acp_and_script_full_api_coexist_without_sharing_authority(tmp_path, monkeypatch):
    from packages.acp.local import initialize, request
    from packages.automation.auth import UserAuth
    from packages.web import script_auth, script_full_api
    from tests import test_real_queue_http_e2e as h

    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    assert port not in (8767, 8768)
    monkeypatch.setattr(h, 'PORT', port)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', h.ROOT / 'tests/support/automation_http_server.py')
    data, workspace = tmp_path / 'owned-data', tmp_path / 'owned-workspace'
    data.mkdir()
    workspace.mkdir()
    (data / 'config.json').write_text(json.dumps({'qq': {'enabled': False}, 'wechat': {'enabled': False},
        'remote': {'enabled': False}, 'tailscale': {'startupEnabled': False},
        'lan': {'startupEnabled': False}}), encoding='utf-8')
    password = 'Aa1!' + secrets.token_urlsafe(24)
    UserAuth(data / 'automation').initialize(password)
    initialize(data, 'editor', 'default', workspace)
    script_full_api.provision(data, 'full', password)
    script_auth.initialize(data, 'limited', {'enabled': True, 'sessions': [], 'rules': []})
    runtime = h._PanRuntime(data)
    try:
        runtime.start()
        # The harness's graceful stop is protected by the same real admin
        # session; otherwise its hard fallback cannot run lifespan cleanup.
        runtime.auth_headers = {'Authorization': 'Bearer ' + UserAuth(data / 'automation').login(password)}
        handle = request(data, 'editor', 'prepare', {'cwd': str(workspace)})['handle']
        assert script_full_api.request(data, 'full', 'GET', '/api/automation/authorization')['status'] == 200
        # A scoped script is never promoted to the explicit full-API grant.
        with pytest.raises(RuntimeError):
            script_full_api.request(data, 'limited', 'GET', '/api/automation/authorization')
        request(data, 'editor', 'release', {'handle': handle})
        script_auth.revoke(data, 'full')
        with pytest.raises(RuntimeError):
            script_full_api.request(data, 'full', 'GET', '/api/list')
        # Revoking UserAuth-backed script access does not revoke the distinct
        # ACP profile or create a replacement script identity.
        handle = request(data, 'editor', 'prepare', {'cwd': str(workspace)})['handle']
        request(data, 'editor', 'release', {'handle': handle})
        assert script_auth._read_json(data / 'script-api/full/profile.json')['policy']['enabled'] is False
    finally:
        runtime.stop()
    assert h._port_is_free(port)
    assert not (data / 'script-ipc.json').exists()
