"""Full frontend acceptance server, disposable data and fake CLI only."""
import importlib.util
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('browser_fixture', Path(__file__).with_name('server.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
assert fixture.PORT in (8765, 8767), 'use only isolated test ports'

from packages.core.adapters.cbc.adapter import CbcAdapter
from packages.core.adapters.claude.adapter import ClaudeAdapter
from packages.core.adapters.codex.adapter import CodexAdapter
from packages.core.adapters.cbc import adapter as cbc_module
from packages.core import session as sessions
from packages.core import worker as worker_module

CLI = str(ROOT / 'tests/support/frontend_stream_cli.py')
CbcAdapter._resolve_cbc_argv = lambda self: [sys.executable, '-u', CLI]
ClaudeAdapter._resolve_claude_argv = lambda self: [sys.executable, '-u', CLI, '--claude']
CodexAdapter.base_args = lambda self: [sys.executable, '-u', CLI, '--codex']
cbc_module.MCP_CONFIG_DIR = fixture.RUNTIME / 'mcp-configs'

_reserve_queue_unit = worker_module._reserve_queue_unit
_CRASH_RECOVERY_LABEL = 'crash-recovery-history-idempotency'


async def _reserve_with_crash_gate(worker, session, items, text):
    history_added = await _reserve_queue_unit(worker, session, items, text)
    is_target = any(
        _CRASH_RECOVERY_LABEL in str(item.get('text') or '')
        for item in items
    )
    marker = fixture.RUNTIME / 'handoff-reservation-paused-once'
    if is_target and not marker.exists():
        # _reserve_queue_unit has durably written the user's history row and
        # reserved queue receipt, but the provider callback has not started.
        # The browser harness kills this Pan process at that exact boundary.
        marker.write_text('reserved', encoding='utf-8')
        release = fixture.RUNTIME / 'release-handoff-reservation'
        while not release.exists():
            await asyncio.sleep(.01)
    return history_added


worker_module._reserve_queue_unit = _reserve_with_crash_gate

_queue_item_backoff = worker_module._queue_item_backoff


def _fast_crash_fixture_retry(item, reason, *, immediate=False):
    if _CRASH_RECOVERY_LABEL in str(item.get('text') or ''):
        return _queue_item_backoff(item, reason, immediate=True)
    return _queue_item_backoff(item, reason, immediate=immediate)


worker_module._queue_item_backoff = _fast_crash_fixture_retry
# Keep the same service watchdog path while making its restart probe fast in
# this disposable fixture. Production defaults remain untouched.
worker_module._GLOBAL_WATCHDOG_TICK_SEC = .5

def seed():
    fixture.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    # Cold restart must reopen the identical files, never reseed/replace them.
    if list(fixture.SESSION_DIR.glob('*.json')):
        return
    for name, adapter, count in [
        ('E2E-A', 'codex', 420), ('E2E-B', 'cbc', 120),
        ('E2E-C', 'claude', 80), ('E2E-LONG', 'codex', 5000),
    ]:
        history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'{name}-history-{i:05d}'} for i in range(count)]
        workdir = fixture.RUNTIME / 'workdirs' / name
        workdir.mkdir(parents=True, exist_ok=True)
        s = sessions.create(name, adapter=adapter, workdir=str(workdir), history=history)
        s.mcp_servers = []
        s.model = 'deterministic-fixture'
        sessions.save(s)

fixture._seed_sessions = seed
fixture.main()
