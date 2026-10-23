"""Reproducible bounded candidate gate; no shared ports/services/profiles."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
pnpm = shutil.which('pnpm.cmd' if os.name == 'nt' else 'pnpm')
assert pnpm, 'pnpm must be installed'
env = {key: value for key, value in os.environ.items() if not key.upper().startswith('PAN_')}
env['PYTHONIOENCODING'] = 'utf-8'
commands = [
    ('typescript', [pnpm, '--dir', 'packages/web', 'exec', 'tsc', '-b']),
    ('build', [pnpm, '--dir', 'packages/web', 'run', 'build']),
    ('vitest', [pnpm, '--dir', 'packages/web', 'exec', 'vitest', 'run',
        'src/views/LockerView.test.tsx', 'src/views/lockerModel.auth.test.ts',
        'src/components/chat/MarkdownRenderer.test.tsx', 'src/components/layout/Sidebar.test.tsx',
        'src/components/session/SessionItem.test.tsx', 'src/components/session/SessionList.drag.test.tsx',
        '--reporter=default', '--reporter=junit', '--outputFile='+str(OUT/'vitest.xml')]),
    ('python', [sys.executable, '-m', 'pytest', '-q',
        'tests/test_locker_messages.py', 'tests/test_user_locker_lifecycle.py',
        'tests/test_interaction_cards.py', 'tests/test_interaction_card_api.py',
        'tests/test_locker_trusted_binding.py', 'tests/test_system_fields.py',
        'tests/test_worker_runtime_issue_identity.py', '--junitxml='+str(OUT/'python.xml')]),
    ('live', [sys.executable, '-m', 'pytest', '-q', 'tests/test_locker_product_live.py',
              '--junitxml='+str(OUT/'live.xml')]),
]
report = {'sourceSha': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,encoding='utf-8').strip(),
          'main': subprocess.check_output(['git','rev-parse','main'],cwd=ROOT,encoding='utf-8').strip(),
          'commands': []}
for name, argv in commands:
    before = time.monotonic()
    with (OUT/f'{name}.log').open('w',encoding='utf-8') as log:
        try:
            result = subprocess.run(argv,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    report['commands'].append({'name':name,'argv':argv,'exit':code,'seconds':round(time.monotonic()-before,2)})
    (OUT/'commands.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(f'{name}: exit={code}',flush=True)
    if code:
        raise SystemExit(code)
print('Candidate checks complete; real fixture cleanup is recorded in live/evidence.json',flush=True)
