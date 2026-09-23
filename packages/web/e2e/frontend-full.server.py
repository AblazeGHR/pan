"""Full frontend acceptance server, disposable data and fake CLI only."""
import importlib.util
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

CLI = str(ROOT / 'tests/support/frontend_stream_cli.py')
CbcAdapter._resolve_cbc_argv = lambda self: [sys.executable, '-u', CLI]
ClaudeAdapter._resolve_claude_argv = lambda self: [sys.executable, '-u', CLI, '--claude']
CodexAdapter.base_args = lambda self: [sys.executable, '-u', CLI, '--codex']
cbc_module.MCP_CONFIG_DIR = fixture.RUNTIME / 'mcp-configs'

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
