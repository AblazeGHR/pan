"""Preserve initial successful tests and restore their overwritten old proof bytes."""
import hashlib
import json
from pathlib import Path
import subprocess

out = Path(__file__).resolve().parent
root = out.parents[1]
initial = out / 'first-run'
initial.mkdir(exist_ok=True)
for name in ['commands.json', 'shell.log', 'shell.xml', 'ownership.log', 'ownership.xml']:
    (initial / name).write_bytes((out / name).read_bytes())
archive = initial / 'native-proof'
archive.mkdir(exist_ok=True)
rows = []
for name in subprocess.check_output(['git', 'diff', '--name-only', 'HEAD'], cwd=root, text=True).splitlines():
    assert name.startswith('evidence/cp-x2-windows-hidden-20261028/proof/') and name.endswith('.json'), name
    path = root / name
    current = path.read_bytes()
    old = subprocess.check_output(['git', 'show', 'HEAD:' + name], cwd=root)
    (archive / path.name).write_bytes(current)
    path.write_bytes(old)
    rows.append({'originalPath': name, 'archivedNewSha256': hashlib.sha256(current).hexdigest(),
                 'restoredHistoricalSha256': hashlib.sha256(old).hexdigest()})
(initial / 'orchestration.json').write_text(json.dumps({'testExits': [0, 0], 'orchestratorExit': 1,
    'reason': 'Final all-tree invariant saw fixed-path diagnostics, not source changes',
    'preservedAndRestored': rows}, indent=2) + '\n', encoding='utf-8')
subprocess.run(['git', 'diff', '--exit-code', 'HEAD'], cwd=root, check=True)
