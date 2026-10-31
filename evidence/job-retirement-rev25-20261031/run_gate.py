"""Run one owned bounded gate and bind its exact command/results to source."""
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PYTHON = 'D:/project/Pan-main/.venv/Scripts/python.exe'
SOURCE = 'f5f05ca5be51e3dbcd1bc4b4cd5b29ebe0ab1597'
QQ = 'tests/test_retirement_rev25_qq_runtime.py'
groups = {
    'qq-native': [QQ, '-k', 'not current_gate and not cold_corrupt and not invalid_runtime and not hard_crash'],
    'qq-fences': [QQ, '-k', 'current_gate or cold_corrupt or invalid_runtime'],
    'qq-crash': [QQ, '-k', 'hard_crash'],
    'affected': ['tests/test_job_final_contracts.py', 'tests/test_job_final_http_service.py',
        'tests/test_session_product_closure.py', 'tests/test_retirement_session_runtime.py',
        'tests/test_retirement_rev25_form.py', 'tests/test_retirement_rev25_monitoring.py'],
    'recovery': ['tests/test_job_automatic_recovery.py', 'tests/test_job_monitoring_completion.py',
        'tests/test_job_recovery_stable_reset.py'],
    'manual-qq': ['tests/test_retirement_rev10_qq_retry.py'],
    'browser': ['tests/test_retirement_rev25_browser.py'],
}
name = sys.argv[1]
assert subprocess.check_output(['git', 'diff', SOURCE, '--', 'packages', 'tests'], cwd=ROOT) == b''
cwd = ROOT
if name in groups:
    argv = [PYTHON, str(ROOT / 'scripts/run_tests_isolated.py'), '--', PYTHON, '-m', 'pytest',
        *groups[name], '-q', '--tb=short', '-o', 'junit_logging=all', '--junitxml=' + str(HERE / (name + '.xml'))]
elif name == 'react':
    cwd = ROOT / 'packages/web'
    argv = ['cmd.exe', '/d', '/c', 'pnpm', 'exec', 'vitest', 'run',
        'src/views/CanonicalJobsView.retirement-r14.test.tsx', 'src/hooks/useWebSocket.test.tsx',
        'src/hooks/useWebSocket.partial-r24.test.ts', 'src/components/automation/CanonicalForms.test.tsx']
elif name == 'build':
    cwd = ROOT / 'packages/web'
    argv = ['cmd.exe', '/d', '/c', 'pnpm', 'build']
else:
    raise SystemExit('Unknown bounded gate')
assert not (HERE / (name + '.command.json')).exists(), 'Preserve prior immutable gate output'
started = time.time()
with (HERE / (name + '.log')).open('wb') as log:
    result = subprocess.run(argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
record = {'sourceSha': SOURCE, 'gate': name, 'argv': argv, 'cwd': str(cwd),
    'exit': result.returncode, 'elapsedSeconds': time.time() - started,
    'log': name + '.log', 'logSha256': hashlib.sha256((HERE / (name + '.log')).read_bytes()).hexdigest()}
if name in groups:
    suites = list(ET.parse(HERE / (name + '.xml')).getroot())
    record['counts'] = {k: sum(int(s.attrib[k]) for s in suites) for k in ('tests', 'errors', 'failures', 'skipped')}
    record['counts']['passed'] = record['counts']['tests'] - sum(record['counts'][k] for k in ('errors', 'failures', 'skipped'))
    record['junit'] = name + '.xml'
    record['junitSha256'] = hashlib.sha256((HERE / record['junit']).read_bytes()).hexdigest()
elif name == 'react':
    plain = re.sub(r'\x1b\[[0-9;]*m', '', (HERE / (name + '.log')).read_text(encoding='utf-8', errors='replace'))
    summary = re.search(r'^\s*Tests\s+(\d+) passed \((\d+)\)', plain, re.MULTILINE)
    if result.returncode == 0:
        assert summary and summary[1] == summary[2], 'Require actual complete Vitest summary'
        record['counts'] = {'tests': int(summary[2]), 'passed': int(summary[1]),
                            'failures': 0, 'errors': 0, 'skipped': 0}
assert subprocess.check_output(['git', 'diff', SOURCE, '--', 'packages', 'tests'], cwd=ROOT) == b''
(HERE / (name + '.command.json')).write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
print(json.dumps(record))
raise SystemExit(result.returncode)
