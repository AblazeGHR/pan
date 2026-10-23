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
from packages.core import config, workspace, session
config.CONFIG_FILE = data / 'config.json'
session.SESSION_DIR = data / 'sessions'
workspace.WORKSPACE_DIR = data / 'workspaces'
workspace._cache.clear()
workspace._loaded = False
from packages.core.adapters.cbc.adapter import CbcAdapter
CbcAdapter.supported_models = property(lambda self: ['deepseek-v4-flash'])
from tests.support.isolated_http_server import main
main()
