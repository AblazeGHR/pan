"""Automation's owned HTTP fixture, reusing the queue test launcher."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
data = Path(os.environ['PAN_E2E_DATA_ROOT']).resolve()
for key in tuple(os.environ):
    if key.upper().startswith('PAN_') and not key.upper().startswith(('PAN_E2E_', 'PAN_TEST_')):
        os.environ.pop(key)
os.environ['PAN_BACKGROUND_JOBS_DIR'] = str(data / 'background_jobs')
os.environ['PAN_TERMINALS_DIR'] = str(data / 'terminals')
os.environ['PAN_SCHEDULER_DIR'] = str(data / 'scheduler')
if os.environ.get('PAN_E2E_ROTATION_CODEX') == '1':
    os.environ['PAN_CODEX_NODE'] = sys.executable
    os.environ['PAN_CODEX_PATH'] = str(ROOT / 'tests/support/rotation_fake_codex_rpc.py')
    os.environ['CODEX_HOME'] = str(data / 'synthetic-codex-home')
elif os.environ.get('PAN_E2E_SYNTH_CODEX') == '1':
    os.environ['PAN_CODEX_NODE'] = sys.executable
    os.environ['PAN_CODEX_PATH'] = str(ROOT / 'tests/support/fake_acp_codex_rpc.py')
    os.environ['CODEX_HOME'] = str(data / 'synthetic-codex-home')
from packages.core import config, workspace, session
config.CONFIG_FILE = data / 'config.json'
session.SESSION_DIR = data / 'sessions'
workspace.WORKSPACE_DIR = data / 'workspaces'
workspace._cache.clear()
workspace._loaded = False
from packages.core.adapters.cbc.adapter import CbcAdapter
CbcAdapter.supported_models = property(lambda self: ['deepseek-v4-flash'])
if os.environ.get('PAN_TEST_AUTH_CHANNEL_FIXTURE') == '1':
    import json
    from packages.web.channel_auth import child_environment
    data.mkdir(parents=True, exist_ok=True)
    (data / 'owned-channel-credentials.json').write_text(json.dumps({
        name: child_environment(name)['PAN_CHANNEL_CREDENTIAL'] for name in ('qq', 'wechat')
    }), encoding='utf-8')
from tests.support.isolated_http_server import main
main()
