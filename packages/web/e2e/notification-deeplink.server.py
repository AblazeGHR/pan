"""Isolated Pan server for the notification deep-link browser E2E.

Real FastAPI app, real Worker/queue/history/WS broadcast path and the real
completion-notification dispatch (browser channel only: every seeded session
keeps `system: false`, so no OS notification is ever attempted). The CLI
provider boundary is the deterministic fixture in
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
from packages.core import session as sessions

CLI = str(ROOT / 'tests/support/frontend_stream_cli.py')
CbcAdapter._resolve_cbc_argv = lambda self: [sys.executable, '-u', CLI]
cbc_module.MCP_CONFIG_DIR = fixture.RUNTIME / 'mcp-configs'


def seed():
    fixture.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    # Cold restart must reopen the identical files, never reseed/replace them.
    if list(fixture.SESSION_DIR.glob('*.json')):
        return
    workdir = fixture.RUNTIME / 'workdirs' / 'notify'
    workdir.mkdir(parents=True, exist_ok=True)
    for name, browser in [
        ('Notify Target', True),
        ('Other Target', False),
        ('Delete Target', True),
    ]:
        session = sessions.create(
            name,
            adapter='cbc',
            workdir=str(workdir),
            notification_settings={'browser': browser, 'system': False},
        )
        session.model = 'deterministic-fixture'
        session.mcp_servers = []
        sessions.save(session)


fixture._seed_sessions = seed
fixture.main()
