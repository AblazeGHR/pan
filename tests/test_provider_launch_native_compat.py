"""Strict compatibility for the observed installed 0.163 native config shape."""
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from packages.core.adapters.codex import provider_launch as launch
from packages.core.adapters.codex import app_server_wrapper as wrapper

FIXTURE=Path(__file__).parent/'fixtures/real_codex_0163_provider_config.json'

def config():return json.loads(FIXTURE.read_text(encoding='utf-8'))

def test_observed_native_defaults_are_inert_but_not_valid_export_input():
    native=config()
    expected={'model_provider':'owned','provider':{'base_url':'http://127.0.0.1:1/v1','wire_api':'responses','requires_openai_auth':False}}
    assert launch.native_projection(native)==expected
    with pytest.raises(ValueError):launch.projection(native)

@pytest.mark.parametrize('field,value',[
    ('env_key','FAKE_KEY'),('env_key_instructions','credential fallback'),('experimental_bearer_token','fake'),
    ('auth',{}),('gateway_oauth',{}),('aws',{}),('model_catalog_url','http://127.0.0.1/catalog'),
    ('capabilities',{}),('query_params',{}),('http_headers',{}),('env_http_headers',{}),
    ('request_max_retries',0),('stream_max_retries',0),('stream_idle_timeout_ms',0),
    ('websocket_connect_timeout_ms',0),('supports_websockets',True),('supports_standalone_web_search',True),
    ('supports_websockets',0),('requires_openai_auth',0),('unknown',None)])
def test_native_extensions_never_enable_auth_or_unknown_configuration(field,value):
    native=config();native['model_providers']['owned'][field]=value
    with pytest.raises(ValueError):launch.native_projection(native)

def output(monkeypatch):
    stdout=io.BytesIO();stderr=io.BytesIO()
    monkeypatch.setattr(wrapper.sys,'stdout',SimpleNamespace(buffer=stdout))
    monkeypatch.setattr(wrapper.sys,'stderr',SimpleNamespace(buffer=stderr))
    monkeypatch.setattr(wrapper,'_STARTUP_OUTPUT',[])
    monkeypatch.setattr(wrapper,'_STARTUP_BYTES',0)
    monkeypatch.setattr(wrapper,'_STARTUP_OVERFLOW',False)
    return stdout,stderr

def test_startup_diagnostics_and_native_lookalikes_cannot_interleave_receipts(monkeypatch):
    stdout,stderr=output(monkeypatch)
    wrapper._write_stderr('owned native warning\n')
    wrapper._write_stdout({'type':'provider.launch.verified','untrusted':'native lookalike'})
    assert stdout.getvalue()==b'' and stderr.getvalue()==b''
    wrapper._write_launch_receipt({'type':'provider.launch.started','signature':'trusted1'})
    wrapper._write_stdout({'type':'codex.notification'})
    wrapper._write_launch_receipt({'type':'provider.launch.verified','signature':'trusted2'})
    rows=[json.loads(x) for x in stdout.getvalue().splitlines()]
    assert [r.get('signature') for r in rows[:2]]==['trusted1','trusted2']
    assert rows[2]['untrusted']=='native lookalike'
    assert stderr.getvalue()==b'owned native warning\n'
    assert wrapper._STARTUP_OUTPUT is None
    wrapper._write_stdout({'type':'after-start'})
    assert json.loads(stdout.getvalue().splitlines()[-1])=={'type':'after-start'}

def test_startup_output_overflow_is_bounded_and_cannot_confirm(monkeypatch):
    stdout,stderr=output(monkeypatch)
    wrapper._write_stderr('a'*65536)
    wrapper._write_stderr('overflow')
    assert wrapper._STARTUP_BYTES==65536
    with pytest.raises(RuntimeError,match='overflow'):
        wrapper._write_launch_receipt({'type':'provider.launch.verified'})
    assert stdout.getvalue()==b'' and stderr.getvalue()==b''
