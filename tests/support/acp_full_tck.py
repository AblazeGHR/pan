"""Run the unfiltered pinned ACP v1 TCK against a disposable Pan fixture.

Output and synthetic data are retained under the candidate's ignored .workflow
directory. This script never connects to an existing Pan service or profile.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.acp.local import initialize
from tests import test_real_queue_http_e2e as h

run = ROOT / '.workflow' / 'acp-acceptance' / ('tck-' + uuid.uuid4().hex)
data, workspace = run / 'data', run / 'workspace'
data.mkdir(parents=True)
workspace.mkdir()
(data / 'config.json').write_text(json.dumps({'qq':{'enabled':False}, 'wechat':{'enabled':False},
    'remote':{'enabled':False}, 'tailscale':{'startupEnabled':False}, 'lan':{'startupEnabled':False}}))
initialize(data, 'editor', 'default', workspace)
with socket.socket() as probe:
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]
if port in (8767, 8768):
    raise RuntimeError('protected port selected')
h.PORT = port
h.SERVER_LAUNCHER = ROOT / 'tests/support/automation_http_server.py'
runtime = h._PanRuntime(data)
env = dict(os.environ, UV_CACHE_DIR='D:/uv/cache', PYTHONPATH=str(ROOT), TMP=str(workspace), TEMP=str(workspace))
cmd = ['uvx', '--from',
       'git+https://github.com/agentclientprotocol/acp-tck@7c71c4d1af0994e73bdc100c9f7f6b98f2319cda',
       'acp-tck', '--protocol-version', '1', '--agent-cwd', str(workspace), '--timeout', '8',
       '--test-timeout', '20', '--report-json', str(run / 'report.json'), '--',
       sys.executable, '-m', 'packages.core.cli', '--root', str(run), 'acp', '--profile', 'editor']
print('Evidence directory:', run, flush=True)
try:
    runtime.start()
    with (run / 'tck.log').open('w', encoding='utf-8') as out:
        result = subprocess.run(cmd, cwd=workspace, env=env, stdout=out, stderr=subprocess.STDOUT,
                                text=True, timeout=1800)
    print('TCK original exit code:', result.returncode, flush=True)
finally:
    runtime.stop()
if not h._port_is_free(port):
    raise RuntimeError('owned fixture port remained bound')
raise SystemExit(result.returncode)
