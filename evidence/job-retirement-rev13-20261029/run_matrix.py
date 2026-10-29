"""Owned, short serial frontend checks; no Pan service or deployed configuration."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
PYTHON = Path('D:/project/Pan-main/.venv/Scripts/python.exe')
PNPM = shutil.which('pnpm.cmd') or shutil.which('pnpm')
if not PNPM:
    raise SystemExit('pnpm is required; no dependency installation is performed')

def capture(argv):
    return subprocess.check_output(argv, cwd=ROOT, text=True).strip()

sha = capture(['git', 'rev-parse', 'HEAD'])
subprocess.run(['git', 'diff', '--exit-code', 'HEAD'], cwd=ROOT, check=True)
tests = [
    'src/views/CanonicalJobsView.retirement-r13.test.tsx',
    'src/views/CanonicalJobsView.test.tsx',
    'src/components/automation/CanonicalForms.test.tsx',
    'src/components/jobs/JobTemplateEditor.test.tsx',
    'src/components/jobs/UnifiedJobForm.test.tsx',
    'src/components/jobs/NewJobForm.window.test.tsx',
    'src/components/session/SelectedSessionActions.test.tsx',
]
commands = {
    'react': [PNPM, '--dir', 'packages/web', 'exec', 'vitest', 'run', *tests,
              '--reporter=junit', '--outputFile=' + str(OUT / 'react.xml')],
    'build': [PNPM, '--dir', 'packages/web', 'build'],
    'browser': ['node', 'packages/web/e2e/retirement-r13-callers.mjs'],
}
result = {'sourceSha': sha, 'python': capture([str(PYTHON), '--version']),
          'node': capture(['node', '--version']), 'pnpm': capture([PNPM, '--version']), 'checks': []}
for name, command in commands.items():
    argv = [str(PYTHON), str(ROOT / 'scripts/run_tests_isolated.py'), '--', *command]
    started = time.monotonic()
    with (OUT / (name + '.log')).open('w', encoding='utf-8') as stream:
        completed = subprocess.run(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    entry = {'name': name, 'argv': argv, 'exit': completed.returncode,
             'seconds': round(time.monotonic() - started, 3)}
    if name == 'react' and (OUT / 'react.xml').exists():
        suites = ET.parse(OUT / 'react.xml').getroot()
        entry['counts'] = {key: int(suites.attrib[key]) for key in ('tests', 'failures', 'errors')}
        entry['skipped'] = sum(int(s.attrib.get('skipped', 0)) for s in suites.findall('testsuite'))
    result['checks'].append(entry)
    (OUT / 'commands.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(entry, ensure_ascii=False), flush=True)
    if completed.returncode:
        raise SystemExit(completed.returncode)
if capture(['git', 'rev-parse', 'HEAD']) != sha:
    raise SystemExit('HEAD changed during checks')
subprocess.run(['git', 'diff', '--exit-code', 'HEAD'], cwd=ROOT, check=True)
