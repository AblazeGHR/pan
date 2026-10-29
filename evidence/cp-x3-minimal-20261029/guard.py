"""Negative Job retirement guard and exact source manifest; no product writes."""
import hashlib
import json
from pathlib import Path
import subprocess

BASE = '9b4506c94ac67a39093961b218b2f01d387d1049'
OUT = Path(__file__).resolve().parent
ALLOWED = {
    'packages/core/platform/worker_runtime.py', 'packages/core/worker.py',
    'packages/web/native_control_api.py', 'packages/web/server.py',
    'packages/web/src/components/session/NewSessionModal.tsx',
    'packages/plugins/ipc.py', 'packages/core/platform/plugin_ipc.py',
}
def git(*args):
    return subprocess.check_output(['git', *args]).decode().strip()
tracked = git('diff', '--name-only', BASE).splitlines()
untracked = git('ls-files', '--others', '--exclude-standard').splitlines()
paths = sorted(set(tracked + untracked))
product = [p for p in paths if p.startswith('packages/') and '/e2e/' not in p and not p.endswith(('.test.ts', '.test.tsx'))]
assert set(product) == ALLOWED, product
negative = git('diff', BASE, '--', 'packages/jobs', 'packages/scheduler', 'packages/core/background_jobs.py',
    'packages/core/main_lifecycle.py', 'packages/core/launcher.py', 'packages/core/cli.py',
    'packages/mcp', 'packages/core/platform/__init__.py', 'main.py', 'minimal-requirements.txt')
assert not negative, negative
server = Path('packages/web/server.py').read_text(encoding='utf-8')
assert 'OwnerFence' not in server and 'bind_final_jobs' not in server and 'scheduler_engine.start_loop' in server
manifest = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sorted(ALLOWED)}
data = {'base': BASE, 'head': git('rev-parse', 'HEAD'), 'productSHA256': manifest,
    'negativeJobSchedulerOwnerDiff': 'empty', 'protectedRefs': {r: git('rev-parse', r) for r in ('main','practical')},
    'approvedPatchSources': ['c8c8c4eb340a9db1285596e161f6a7f54bb032bc','cf7510f5152655992e376092d946962812950137']}
(OUT / 'source-guard.json').write_text(json.dumps(data, indent=2))
print('PASS exact seven product paths; no Job/Scheduler/MCP/owner/capability registry delta')
