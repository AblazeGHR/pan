"""Production app with owned roots and inert CLI inventory for browser QA."""
from dataclasses import replace
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
data = Path(os.environ['PAN_E2E_DATA_ROOT']).resolve()
from packages.core import config, session, workspace, platform
config.CONFIG_FILE = data / 'config.json'
session.SESSION_DIR = data / 'sessions'
workspace.WORKSPACE_DIR = data / 'workspaces'
import packages.web.server as server
server.get_cli_diagnostics = lambda: [{'name': 'codex', 'label': 'Codex', 'available': True,
    'command': [], 'missing': [], 'hint': 'Owned inventory; provider never launched'}]
native = platform.CAPABILITIES['terminal.native']


@server.app.post('/__test/native-policy/{mode}')
async def policy(mode: str):
    assert mode in {'actual', 'denied'}
    platform.CAPABILITIES['terminal.native'] = replace(native, supported_on=frozenset()) if mode == 'denied' else native
    return {'ok': True}


from tests.support.automation_http_server import main
main()
