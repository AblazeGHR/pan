"""Isolated Pan server for the notification -> workspace jump browser E2E.

Real FastAPI app, real Worker/queue/history/WS broadcast and the real
completion-notification dispatch (browser channel only: every seeded session
keeps ``system: False``, so no OS notification is ever attempted). The CLI
provider boundary is the deterministic fixture in
tests/support/frontend_stream_cli.py - no network/model calls are made.

Session/data/workspace isolation, the disposable runtime root and the
``/__e2e/*`` injection seams come from packages/web/e2e/server.py (the
established browser fixture). This seed adds Workspaces, real membership and
one managed child (inheriting its root's workspace) on top of it.
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
from packages.core import workspace as workspaces

CLI = str(ROOT / 'tests/support/frontend_stream_cli.py')
CbcAdapter._resolve_cbc_argv = lambda self: [sys.executable, '-u', CLI]
cbc_module.MCP_CONFIG_DIR = fixture.RUNTIME / 'mcp-configs'


def seed():
    fixture.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    # Cold restart must reopen the identical files, never reseed/replace them.
    if list(fixture.SESSION_DIR.glob('*.json')):
        return
    workdir = fixture.RUNTIME / 'workdirs' / 'notify-ws'
    workdir.mkdir(parents=True, exist_ok=True)

    alpha = workspaces.create('Alpha Lab')
    beta = workspaces.create('Beta Lab')

    def make(name, **kwargs):
        session = sessions.create(name, adapter='cbc', workdir=str(workdir), **kwargs)
        session.model = 'deterministic-fixture'
        session.mcp_servers = []
        sessions.save(session)
        return session

    notify = {'browser': True, 'system': False}
    # Alpha Lab: two real members (Alpha One is the hot-click target).
    make('Alpha One', notification_settings=notify, workspace_ids=[alpha.id])
    make('Alpha Two', workspace_ids=[alpha.id])
    # Beta Lab: a root plus a managed child that carries NO own workspace id and
    # must inherit Beta Lab through the manager chain.
    beta_root = make('Beta Root', workspace_ids=[beta.id])
    beta_child = make('Beta Child', notification_settings=notify)
    claim_error = sessions.claim(beta_root.id, beta_child.id)
    if claim_error:
        raise RuntimeError(f'failed to claim Beta Child: {claim_error}')
    # Ungrouped: no workspace membership at all.
    make('Loose One', notification_settings=notify)


fixture._seed_sessions = seed
fixture.main()
