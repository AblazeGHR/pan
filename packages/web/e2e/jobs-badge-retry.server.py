"""Isolated Pan server for the Jobs badge broadcast-retry browser E2E.

Identical real chain to jobs-badge.server.py (real FastAPI, real Worker/
scheduler/job registry, real WS fan-out) with ONE test-only fault: the
scheduler broadcast channel is wrapped so the FIRST terminal job.updated
event raised by the recovery loop fails once, exactly like a broken WS
channel. The wrapper is installed after packages.web.server has bound the
real broadcaster and before uvicorn starts; every later terminal event
passes through untouched.

The injection is precise: only ``job.updated`` events whose job reached a
terminal status are counted, and only the first one raises. job.created /
job.deleted and all non-job events take the real path, so the rest of the
badge lifecycle under test stays real. GET /__e2e/broadcast-fault exposes
the attempt counters so the browser script can assert the causal chain
(failure observed -> badge still stale -> retry -> badge converges).
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('browser_fixture', Path(__file__).with_name('server.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

# Importing the real server binds scheduler_api's broadcaster at module
# level; wrap that channel before the fixture starts uvicorn.
import packages.scheduler.api as scheduler_api
import packages.web.server as server_module

_real_broadcast = scheduler_api._state["broadcast"]
fault = {"failed_terminal_events": 0, "passed_terminal_events": 0}

_TERMINAL = {"completed", "failed", "cancelled"}


async def faulty_broadcast(data: dict):
    if (isinstance(data, dict)
            and data.get("type") == "job.updated"
            and isinstance(data.get("job"), dict)
            and data["job"].get("status") in _TERMINAL):
        if fault["failed_terminal_events"] == 0:
            fault["failed_terminal_events"] += 1
            # Same shape as a broken channel: emit_strict must observe a
            # failure and leave the idempotency marker unpersisted.
            raise RuntimeError("injected terminal broadcast failure")
        fault["passed_terminal_events"] += 1
    return await _real_broadcast(data)


scheduler_api._state["broadcast"] = faulty_broadcast


@server_module.app.get("/__e2e/broadcast-fault")
async def e2e_broadcast_fault():
    """Expose the injected-channel attempt counters to the browser script."""
    return {"ok": True, **fault}


from packages.core.adapters.cbc.adapter import CbcAdapter
from packages.core.adapters.cbc import adapter as cbc_module

CLI = str(ROOT / 'tests/support/frontend_stream_cli.py')
CbcAdapter._resolve_cbc_argv = lambda self: [sys.executable, '-u', CLI]
cbc_module.MCP_CONFIG_DIR = fixture.RUNTIME / 'mcp-configs'


def seed():
    fixture.SESSION_DIR.mkdir(parents=True, exist_ok=True)
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
