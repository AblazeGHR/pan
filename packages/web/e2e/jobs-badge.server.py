"""Isolated Pan server for the Jobs running-badge browser E2E.

Real FastAPI app, real Worker/scheduler/job registry and the real WS
broadcast path. The CLI provider boundary is the deterministic fixture in
tests/support/frontend_stream_cli.py — no network/model calls are made.
Session/data isolation, disposable runtime root and the `/__e2e/*` injection
seams come from packages/web/e2e/server.py (the established browser fixture).
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('browser_fixture', Path(__file__).with_name('server.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

from packages.core.adapters.cbc.adapter import CbcAdapter
from packages.core.adapters.cbc import adapter as cbc_module

CLI = str(ROOT / 'tests/support/frontend_stream_cli.py')
CbcAdapter._resolve_cbc_argv = lambda self: [sys.executable, '-u', CLI]
cbc_module.MCP_CONFIG_DIR = fixture.RUNTIME / 'mcp-configs'


def seed():
    fixture.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    # Cold restart must reopen the identical files, never reseed/replace them.
    if list(fixture.SESSION_DIR.glob('*.json')):
        return
    from packages.core import session as sessions
    workdir = fixture.RUNTIME / 'workdirs' / 'badge'
    workdir.mkdir(parents=True, exist_ok=True)
    session = sessions.create(
        'Badge Fixture',
        adapter='cbc',
        workdir=str(workdir),
        notification_settings={'browser': False, 'system': False},
    )
    session.model = 'deterministic-fixture'
    session.mcp_servers = []
    sessions.save(session)


fixture._seed_sessions = seed
fixture.main()
