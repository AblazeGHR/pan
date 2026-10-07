"""Checks run as one durable Job with exact exit evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
ROOT = Path(__file__).resolve().parents[3]
CHECKOUT = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT
WEB = CHECKOUT / 'packages/web'
mode = sys.argv[1]
EVIDENCE = WEB / 'test-results' / f'navigation-check-{mode}-{int(time.time()*1000)}'
EVIDENCE.mkdir(parents=True, exist_ok=True)
sha = subprocess.check_output(['git','rev-parse','HEAD'], cwd=CHECKOUT, text=True).strip()
commands = [['pnpm.cmd', 'build'], ['node', str(ROOT / 'packages/web/e2e/navigation-open-nearest.e2e.mjs'), mode, str(CHECKOUT)]]
if mode == 'unit':
    commands = [['pnpm.cmd', 'exec', 'vitest', 'run', 'src/components/chat/MessageNavigationRail.test.tsx', 'src/components/chat/navigationIndex.test.ts', 'src/components/chat/ChatMessages.test.tsx', 'src/components/chat/messageFilter.test.ts', 'src/views/ChatView.navigationRail.test.tsx', '--reporter=default', '--reporter=junit', f'--outputFile={EVIDENCE / 'junit.xml'}']]
results = []
env = {k:v for k,v in os.environ.items() if not k.upper().startswith('PAN_')}
for argv in commands:
    run = subprocess.run(argv, cwd=WEB, env=env, check=False)
    results.append({'argv': argv, 'exitCode': run.returncode})
    summary = {'sha': sha, 'checkout': str(CHECKOUT), 'mode': mode, 'commands': results}
    if mode == 'unit' and run.returncode == 0:
        xml = ET.parse(EVIDENCE / 'junit.xml').getroot()
        summary['testcases'] = len(list(xml.iter('testcase')))
        summary['failures'] = len(list(xml.iter('failure')))
        summary['errors'] = len(list(xml.iter('error')))
        assert summary['testcases'] > 0 and not summary['failures'] and not summary['errors']
    (EVIDENCE / 'result.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps({'evidence': str(EVIDENCE), **summary}), flush=True)
    if run.returncode: sys.exit(run.returncode)
