"""Serial immutable-source verification, retaining actual nonzero test exits."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
PY = Path('D:/project/Pan-main/.venv/Scripts/python.exe')
PNPM = shutil.which('pnpm.cmd') or shutil.which('pnpm')
if not PNPM:
    raise SystemExit('Existing pnpm/dependencies required; no install is performed')

def read(argv):
    return subprocess.check_output(argv, cwd=ROOT, text=True).strip()

subprocess.run(['git', 'diff', '--exit-code', 'HEAD'], cwd=ROOT, check=True)
sha = read(['git', 'rev-parse', 'HEAD'])
groups = {
    'product': ['tests/test_retirement_rev14_templates.py', 'tests/test_scheduler_time_templates.py',
                'tests/test_retirement_rev11_compat.py', 'tests/test_job_final_http_service.py',
                'tests/test_session_product_closure.py'],
    'runtime': ['tests/test_job_automatic_recovery.py', 'tests/test_job_process_logs.py',
                'tests/test_job_final_contracts.py', 'tests/test_job_final_creators.py'],
}
react = ['src/views/CanonicalJobsView.retirement-r14.test.tsx', 'src/views/CanonicalJobsView.retirement-r13.test.tsx',
         'src/views/CanonicalJobsView.test.tsx', 'src/components/automation/CanonicalForms.test.tsx',
         'src/components/jobs/JobTemplateEditor.test.tsx', 'src/components/jobs/UnifiedJobForm.test.tsx',
         'src/components/jobs/NewJobForm.window.test.tsx', 'src/components/session/SelectedSessionActions.test.tsx']
commands = {name: [str(PY), '-m', 'pytest', *files, '-q', '-s', '--junitxml=' + str(OUT / (name + '.xml'))]
            for name, files in groups.items()}
commands['react'] = [PNPM, '--dir', 'packages/web', 'exec', 'vitest', 'run', *react,
                     '--reporter=junit', '--outputFile=' + str(OUT / 'react.xml')]
commands['build'] = [PNPM, '--dir', 'packages/web', 'build']
result = {'sourceSha': sha, 'python': read([str(PY), '--version']), 'node': read(['node', '--version']),
          'pnpm': read([PNPM, '--version']), 'checks': []}
code = 0
for name, command in commands.items():
    argv = [str(PY), str(ROOT / 'scripts/run_tests_isolated.py'), '--', *command]
    started = time.monotonic()
    with (OUT / (name + '.log')).open('w', encoding='utf-8') as log:
        child = subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    entry = {'name': name, 'argv': argv, 'exit': child.returncode, 'seconds': round(time.monotonic() - started, 3)}
    xml = OUT / (name + '.xml')
    if xml.exists():
        suites = ET.parse(xml).getroot()
        rows = list(suites.iter('testcase'))
        failed = [r.attrib.get('classname', '') + '::' + r.attrib['name'] for r in rows if r.find('failure') is not None or r.find('error') is not None]
        skipped = sum(r.find('skipped') is not None for r in rows)
        entry['counts'] = {'tests': len(rows), 'passed': len(rows) - len(failed) - skipped,
                           'failedOrError': len(failed), 'skipped': skipped}
        entry['failedNodes'] = failed
    result['checks'].append(entry)
    (OUT / 'commands.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(entry), flush=True)
    code = max(code, child.returncode)
subprocess.run(['git', 'diff', '--exit-code', 'HEAD'], cwd=ROOT, check=True)
assert read(['git', 'rev-parse', 'HEAD']) == sha
sys.exit(code)
