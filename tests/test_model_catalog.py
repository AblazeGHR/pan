"""Credential-free model directory and truthful declared capability projection."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.core.model_catalog import adapter_catalog
from packages.core.adapters.cbc import adapter as cbc_module
from packages.core.adapters.codex import adapter as codex_module
from packages.core import config as config_module
from packages.mcp import server as mcp
from packages.web import server


class Cached:
    name = 'cached'
    _cached_models = ['m1','m2','m1']
    _models_cached_at = 1.0
    _MODELS_TTL_SEC = 300
    effort_values = ['low','high']
    model_efforts = {'m1':['low'], 'm2':[]}
    supported_settings = ['model','effort']
    execution_modes = ['stream','oneshot']
    supports_resume = True
    supports_fork = False
    model_credit_multipliers = {'m1':0, 'm2':.25, 'other':99}
    @property
    def supported_models(self):
        pytest.fail('catalog must not trigger discovery')
    @property
    def default_model(self):
        pytest.fail('catalog must not read dynamic config')


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.add_api_route('/api/model-catalog', server.api_model_catalog, methods=['GET'])
    client = TestClient(app)
    monkeypatch.setattr(server, 'list_adapters', lambda: [Cached()])
    monkeypatch.delenv('PAN_AGENT_SESSION_ID', raising=False)
    monkeypatch.setattr(mcp, '_api', lambda method, path: client.request(method, path).json())
    return client


def test_declared_fields_unknown_scope_and_freshness(monkeypatch):
    monkeypatch.setattr(time, 'monotonic', lambda: 20)
    result = adapter_catalog(Cached())
    assert [m['id'] for m in result['models']] == ['m1','m2']
    assert result['modelList']['freshness'] == {'status':'fresh', 'ageSeconds':19.0, 'ttlSeconds':300, 'sourceUpdatedAt':None}
    assert result['modelList']['origin'] == 'unknown'
    m1,m2 = result['models']
    assert m1['effort']['value'] == ['low'] and m2['effort']['value'] == []
    assert m1['creditMultiplier']['value'] == 0
    assert m2['creditMultiplier']['value'] == .25
    assert result['defaultModel']['status'] == 'unknown'
    assert result['adapterEffortValues']['value'] == ['low','high']
    assert result['capabilities']['fork']['value'] is False
    assert result['capabilities']['nativeSteer']['value'] is None
    assert result['executionModes']['value'] == ['stream']
    result['models'][0]['effort']['value'].append('bad')
    assert Cached.model_efforts['m1'] == ['low']


def test_missing_model_declaration_does_not_inherit_generic_effort():
    a = SimpleNamespace(name='plain', supported_models=['m'], effort_values=['high'])
    result = adapter_catalog(a)
    assert result['modelList']['status'] == 'declared'
    assert result['models'][0]['effort']['status'] == 'unknown'
    assert result['models'][0]['creditMultiplier']['value'] is None
    assert result['supportedSettings']['value'] is None
    assert result['capabilities']['resume']['status'] == 'unknown'


def test_cold_dynamic_sources_unknown_not_refreshed():
    class Cold(Cached):
        _cached_models = None
        model_credit_multipliers = property(lambda _: pytest.fail('no credentials'))
    result = adapter_catalog(Cold())
    assert result['modelList']['status'] == 'unknown' and result['models'] == []
    class Empty(Cold):
        _cached_models = []
    assert adapter_catalog(Empty())['modelList']['status'] == 'cached'


@pytest.mark.parametrize('stamp,ttl,status', [(1,3,'stale'),(0,3,'unknown'),(200,3,'unknown'),(float('nan'),3,'unknown'),(1,None,'unknown')])
def test_stale_and_unknown_timing(monkeypatch, stamp, ttl, status):
    monkeypatch.setattr(time, 'monotonic', lambda: 20)
    a = Cached()
    a._models_cached_at = stamp
    a._MODELS_TTL_SEC = ttl
    assert adapter_catalog(a)['modelList']['freshness']['status'] == status


def test_cbc_warm_account_cache_not_read_or_refreshed(monkeypatch):
    a = cbc_module.CbcAdapter()
    monkeypatch.setattr(cbc_module.CbcAdapter, '_cached_models', ['m'])
    monkeypatch.setattr(cbc_module.CbcAdapter, '_cached_models_ts', time.monotonic())
    monkeypatch.setattr(cbc_module.CbcAdapter, '_cached_multipliers', {'m':.01})
    monkeypatch.setattr(cbc_module, '_current_cbc_user_id', lambda: pytest.fail('no credential read'))
    monkeypatch.setattr(cbc_module, '_fetch_credit_multipliers', lambda: pytest.fail('no multiplier refresh'))
    monkeypatch.setattr(config_module, 'load_config', lambda: pytest.fail('no config discovery'))
    result = adapter_catalog(a)
    assert result['models'][0]['creditMultiplier']['status'] == 'unknown'
    assert result['modelList']['freshness']['status'] == 'fresh'


def test_codex_reuses_non_secret_model_efforts_only(monkeypatch, tmp_path):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    (tmp_path/'models_cache.json').write_text(json.dumps({'models':[
        {'slug':'m', 'supported_reasoning_levels':[{'effort':'high'}], 'secret':'NEVER_EXPOSE'},
        {'slug':'hidden', 'visibility':'hide', 'supported_reasoning_levels':['max']},
    ]}),encoding='utf-8')
    secret = tmp_path/'auth.json'
    secret.write_text('credential sentinel',encoding='utf-8')
    monkeypatch.setattr(codex_module.CodexAdapter, '_cached_models', ['m','other'])
    monkeypatch.setattr(config_module, 'load_config', lambda: pytest.fail('no config discovery'))
    original = secret.read_bytes()
    result = adapter_catalog(codex_module.CodexAdapter())
    assert result['models'][0]['effort']['value'] == ['high']
    assert result['models'][1]['effort']['status'] == 'unknown'
    assert 'NEVER_EXPOSE' not in json.dumps(result) and 'hidden' not in json.dumps(result)
    assert secret.read_bytes() == original


def test_http_mcp_selection_errors_and_permission_checks(client, monkeypatch):
    assert mcp.model_catalog()['adapters'][0]['adapter'] == 'cached'
    assert mcp.model_catalog(adapter=' cached ')['ok'] is True
    assert mcp.model_catalog(adapter='missing')['error']['code'] == 'unknown_adapter'
    assert mcp.model_catalog(adapter=' ')['error']['code'] == 'invalid_params'
    monkeypatch.setattr(mcp, '_caller_identity', lambda: {'id':'self', 'panAccess':{'restrictToManaged':True}, 'managed':['allowed']})
    assert mcp.model_catalog(session_id='blocked')['error']['code'] == 'permission_denied'
    assert mcp.model_catalog(session_id='allowed')['ok'] is True
    monkeypatch.setenv('PAN_AGENT_SESSION_ID','self')
    assert mcp.model_catalog()['ok'] is True


def test_hook_failure_safe_downgrade_and_no_secret_text(client, monkeypatch):
    class Fails(Cached):
        def model_catalog_metadata(self):
            raise ValueError('SECRET_CREDENTIAL_OR_PATH')
        model_efforts = property(lambda _: pytest.fail('unsafe property'))
    monkeypatch.setattr(server, 'list_adapters', lambda: [Fails()])
    result = mcp.model_catalog()
    assert result['adapters'][0]['models'][0]['effort']['status'] == 'unknown'
    assert 'SECRET' not in json.dumps(result)


def test_real_http_mcp_protocol_is_credential_free(monkeypatch, record_property):
    import os
    import socket
    import threading
    import uvicorn
    from mcp.shared.memory import create_connected_server_and_client_session

    app = FastAPI()
    app.add_api_route('/api/model-catalog', server.api_model_catalog, methods=['GET'])
    monkeypatch.setattr(server, 'list_adapters', lambda: [Cached()])
    monkeypatch.delenv('PAN_AGENT_SESSION_ID', raising=False)
    sock = socket.socket()
    sock.bind(('127.0.0.1',0))
    port = sock.getsockname()[1]
    assert port not in (8767,8768)
    record_property('owned_http_port',port)
    record_property('owned_http_pid',os.getpid())
    monkeypatch.setattr(mcp,'_pan_api_url',f'http://127.0.0.1:{port}')
    http = uvicorn.Server(uvicorn.Config(app,lifespan='off',log_level='error',ws='none'))
    thread = threading.Thread(target=http.run,kwargs={'sockets':[sock]},daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+5
        while not http.started and thread.is_alive() and time.monotonic()<deadline:
            thread.join(.01)
        assert http.started
        async def exercise():
            async with create_connected_server_and_client_session(mcp.mcp) as protocol:
                tools = {t.name:t for t in (await protocol.list_tools()).tools}
                assert {'adapter','session_id'} <= set(tools['model_catalog'].inputSchema['properties'])
                result = await protocol.call_tool('model_catalog',{'adapter':'cached'})
                assert not result.isError
                value = json.loads(next(c.text for c in result.content if c.type=='text'))
                assert value['adapters'][0]['models'][0]['effort']['value'] == ['low']
                assert value['adapters'][0]['capabilities']['nativeSteer']['value'] is None
                assert value['availability'] == 'metadata_only_not_provider_verified'
        asyncio.run(exercise())
    finally:
        http.should_exit=True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1',port)) != 0


def test_hook_descriptor_is_not_invoked():
    class Unsafe(Cached):
        model_catalog_metadata = property(lambda _: pytest.fail('do not invoke descriptors'))
    assert adapter_catalog(Unsafe())['models'][0]['effort']['value'] == ['low']
