"""Run unchanged legacy suites against the exact baseline production modules.

Only product Python differences are scheduler/api.py and mcp/server.py. Load
those two git blobs in memory; all other production files and selected tests
must match the baseline. No worktree file or application data is replaced.
"""
import importlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import types

import pytest

BASELINE = '1fc385ff34b2e3fdddaf0cd066648a1a7ee1cd03'
ROOT = Path.cwd()
overrides = ('packages.scheduler.api', 'packages.mcp.server')
expected = {name.replace('.', '/') + '.py' for name in overrides}
changed = subprocess.check_output(['git', 'diff', '--name-only', BASELINE, '--', 'packages/'], text=True).splitlines()
assert {p for p in changed if p.endswith('.py')} == expected, changed
suites = ['tests/test_jobs_api.py', 'tests/test_scheduler_time_events.py', 'tests/test_scheduler_integration.py']
assert not subprocess.check_output(['git', 'diff', BASELINE, '--', *suites, 'tests/conftest.py'])
for name in overrides:
    path = name.replace('.', '/') + '.py'
    parent = importlib.import_module(name.rpartition('.')[0])
    source = subprocess.check_output(['git', 'show', BASELINE + ':' + path])
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / path)
    module.__package__ = name.rpartition('.')[0]
    module.__spec__ = importlib.util.spec_from_loader(name, loader=None)
    sys.modules[name] = module
    setattr(parent, name.rpartition('.')[2], module)
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
print('Baseline product blobs loaded in memory; other product Python and selected suites unchanged', flush=True)
raise SystemExit(pytest.main([*suites, '-q', '-o', 'addopts=', '--tb=short',
                             '--junitxml=evidence/r6c-scheduler-surface/baseline-legacy.xml']))
