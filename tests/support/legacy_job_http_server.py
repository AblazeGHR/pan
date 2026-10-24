"""Owned transition fixture: shared production-style Job root, no provider calls."""
import os
from pathlib import Path

data = Path(os.environ['PAN_E2E_DATA_ROOT']).resolve()
for key in tuple(os.environ):
    if key.upper().startswith('PAN_') and not key.upper().startswith(('PAN_E2E_', 'PAN_TEST_')):
        os.environ.pop(key)
os.environ['PAN_BACKGROUND_JOBS_DIR'] = str(data / 'background_jobs')
os.environ['PAN_SCHEDULER_DIR'] = str(data / 'background_jobs')
os.environ['PAN_TERMINALS_DIR'] = str(data / 'terminals')
from packages.core import config, workspace, session
config.CONFIG_FILE = data / 'config.json'
workspace.WORKSPACE_DIR = data / 'workspaces'
session.SESSION_DIR = data / 'sessions'
from tests.support.isolated_http_server import main
main()
