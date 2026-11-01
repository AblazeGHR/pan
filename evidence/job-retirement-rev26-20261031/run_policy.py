"""Bind the policy-only successor to its own immutable test/source commit."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SOURCE = '38b60e270c060a086705cbd5eb0b149f3d75441f'
PYTHON = 'D:/project/Pan-main/.venv/Scripts/python.exe'
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip() == SOURCE
assert subprocess.check_output(['git', 'diff', SOURCE, '--', 'packages', 'tests'], cwd=ROOT) == b''
assert subprocess.check_output(['git', 'diff', '9ccf4f21', SOURCE, '--', 'packages',
    'tests/test_retirement_rev10_qq_retry.py', 'tests/test_retirement_rev25_qq_runtime.py'], cwd=ROOT) == b''
argv = [PYTHON, str(ROOT / 'scripts/run_tests_isolated.py'), '--', PYTHON, '-m', 'pytest',
    'tests/test_retirement_rev26_unknown_api.py', '-q', '--tb=short', '-o', 'junit_logging=all',
    '--junitxml=' + str(HERE / 'unknown-policy.xml')]
assert not (HERE / 'unknown-policy.command.json').exists()
started = time.monotonic()
with (HERE / 'unknown-policy.log').open('wb') as log:
    result = subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
counts = {'passed': 0, 'failed': 0, 'error': 0, 'skipped': 0}
nodes = []
for case in ET.parse(HERE / 'unknown-policy.xml').iter('testcase'):
    status = ('failed' if case.find('failure') is not None else 'error' if case.find('error') is not None
              else 'skipped' if case.find('skipped') is not None else 'passed')
    counts[status] += 1
    nodes.append({'node': case.attrib['classname'].replace('.', '/') + '.py::' + case.attrib['name'], 'status': status})
record = {'sourceSha': SOURCE, 'argv': argv, 'cwd': str(ROOT), 'exit': result.returncode,
    'seconds': time.monotonic() - started, 'counts': counts, 'executionOrder': nodes,
    'qqProductAndTestSourceIdenticalTo': '9ccf4f2184851a7a67f60823b0c733d47d1386e6',
    'log': 'unknown-policy.log', 'junit': 'unknown-policy.xml'}
for kind in ('log', 'junit'):
    record[kind + 'Sha256'] = hashlib.sha256((HERE / record[kind]).read_bytes()).hexdigest()
(HERE / 'unknown-policy.command.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
print(json.dumps(record))
raise SystemExit(result.returncode)
