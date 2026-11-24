"""Run the unfiltered pinned ACP v1 TCK against a disposable Pan fixture.

Output and synthetic data are retained under the candidate's ignored .workflow
directory. This script never connects to an existing Pan service or profile.
"""
import json
import os
from pathlib import Path
import socket
import shlex
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
native = '--native' in sys.argv[1:]
authorized_mcp = '--authorized-mcp' in sys.argv[1:]
if authorized_mcp and not native:
    raise ValueError('--authorized-mcp requires --native')
tck_python = None
if authorized_mcp:
    try:
        tck_python = Path(sys.argv[sys.argv.index('--authorized-mcp') + 1]).resolve(strict=True)
    except (ValueError, IndexError):
        raise ValueError('--authorized-mcp requires the pinned TCK Python executable') from None
    tck_info = json.loads(subprocess.check_output(
        [str(tck_python), '-c', 'import json,sys,tck; print(json.dumps([sys.executable,tck.__file__]))'], text=True))
    tck_python, tck_dir = Path(tck_info[0]), Path(tck_info[1]).parent
    conformance = tck_dir / 'v1' / 'conformance'
    basetemp = workspace / 'pytest-owned'
    marker = basetemp / 'test_session_new_accepts_a_std0' / 'mcp-marker'
config = {'qq':{'enabled':False}, 'wechat':{'enabled':False},
    'remote':{'enabled':False}, 'tailscale':{'startupEnabled':False}, 'lan':{'startupEnabled':False}}
template = 'default'
if native:
    plugin = run / 'plugin'; plugin.mkdir()
    manifest = {'session_templates':[{'name':'synthetic-codex', 'adapter':'codex',
        'model':'synthetic-model', 'permission_mode':'read-only',
        'mcp_mode':'optional' if authorized_mcp else 'never'}]}
    if authorized_mcp:
        manifest['mcp_servers'] = [{'name':'acp-tck-stub', 'command':str(tck_python),
            'args':[str(conformance / '_mcp_stub.py'), str(marker)]}]
    (plugin / 'manifest.json').write_text(json.dumps(manifest))
    config.update({'plugin_manifests':[str(plugin)], 'codex':{'models':['synthetic-model'],
                   'model':'synthetic-model', 'permission_mode':'read-only'}})
    os.environ['PAN_E2E_SYNTH_CODEX'] = '1'
    template = 'synthetic-codex'
(data / 'config.json').write_text(json.dumps(config))
initialize(data, 'editor', template, workspace, ['acp-tck-stub'] if authorized_mcp else ())
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
       '--test-timeout', '20', *(['--cancel-prompt', 'hold'] if native else []), '--report-json', str(run / 'report.json'), '--',
       sys.executable, '-m', 'packages.core.cli', '--root', str(run), 'acp', '--profile', 'editor']
if authorized_mcp:
    # Same unfiltered v1 suite and plugin as the TCK CLI, with an owned,
    # deterministic basetemp so the operator can authorize the exact stub
    # descriptor through the normal manifest/profile setup before launch.
    agent = [sys.executable, '-m', 'packages.core.cli', '--root', str(run), 'acp', '--profile', 'editor']
    cmd = [str(tck_python), '-m', 'pytest', str(conformance), '-p', 'tck.v1.plugin',
           '-p', 'no:cacheprovider', '-p', 'no:xdist', '-c', str(conformance / 'pytest.ini'),
           '--rootdir', str(conformance), '--tck-agent-cmd', shlex.join(agent),
           '--tck-timeout', '8', '--tck-startup-timeout', '10', '--tck-test-timeout', '20',
           '-rs', '--tck-agent-cwd', str(workspace), '--tck-cancel-prompt', 'hold',
           '--tck-report-json', str(run / 'report.json'), '--basetemp', str(basetemp)]
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
