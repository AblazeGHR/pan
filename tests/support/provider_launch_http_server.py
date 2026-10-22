"""Trusted fixture exporter + local provider. Never queries cc-switch/accounts."""
import atexit
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import runpy
import sys
import threading

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
data=Path(os.environ['PAN_E2E_DATA_ROOT']).resolve()
os.environ['CODEX_HOME']=str(data/'unused-global-codex')
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        with (data/'provider-hits.jsonl').open('a',encoding='utf-8') as out:out.write(json.dumps(self.path)+'\n')
        self.send_response(200);self.end_headers();self.wfile.write(b'owned-provider')
    def log_message(self,*args):pass
provider=ThreadingHTTPServer(('127.0.0.1',0),Handler)
assert provider.server_port not in (8767,8768)
threading.Thread(target=provider.serve_forever,daemon=True).start()
atexit.register(provider.server_close)
(data/'provider-port.json').write_text(json.dumps(provider.server_port),encoding='utf-8')
from packages.core.adapters.codex.adapter import CodexAdapter
from packages.core.adapters.codex import provider_launch
provider_launch.enabled=lambda:True  # owned fixture only; launcher clears PAN_* inputs
from tests.test_provider_launch import export
def source(session):
    path=data/'selected-provider.json'
    selected=json.loads(path.read_text(encoding='utf-8')) if path.exists() else 'provider-a'
    assert selected in ('provider-a','provider-b')
    return export('http://127.0.0.1:'+str(provider.server_port)+'/'+selected,selected)
CodexAdapter.provider_launch_source=staticmethod(source)
CodexAdapter.base_args=lambda self:[sys.executable,'-u',str(ROOT/'packages/core/adapters/codex/wrapper.py'),
    '--app-server','--node-path',sys.executable,'--codex-path',str(ROOT/'tests/fixtures/fixed_codex_server.py')]
CodexAdapter.mcp_args=lambda self,s:[]
CodexAdapter.model_args=lambda self,s:[]
CodexAdapter.permission_mode_args=lambda self,s:[]
CodexAdapter.default_model=property(lambda self:'owned-model')
CodexAdapter.supported_models=property(lambda self:['owned-model'])
runpy.run_path(str(ROOT/'tests/support/automation_http_server.py'),run_name='__main__')
