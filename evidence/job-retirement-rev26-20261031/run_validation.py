"""Owned r26 gates: exact assertions, immutable source, same-process order."""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SOURCE = '9ccf4f2184851a7a67f60823b0c733d47d1386e6'
BASE = 'f905ff36726aa395f5edb176781f3db467a03a0d'
PYTHON = 'D:/project/Pan-main/.venv/Scripts/python.exe'
MANUAL = 'tests/test_retirement_rev10_qq_retry.py'
RUNTIME = 'tests/test_retirement_rev25_qq_runtime.py'

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)

def frozen():
    assert git('rev-parse', 'HEAD').decode().strip() == SOURCE
    assert git('diff', SOURCE, '--', 'packages', 'tests') == b''

frozen()
old = ast.parse(git('show', BASE + ':' + MANUAL).decode('utf-8'))
new = ast.parse((ROOT / MANUAL).read_text(encoding='utf-8'))
count = 0
for function in old.body:
    if not isinstance(function, ast.FunctionDef):
        continue
    current = next(n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == function.name)
    before = Counter(ast.dump(n, include_attributes=False) for n in ast.walk(function) if isinstance(n, ast.Assert))
    after = Counter(ast.dump(n, include_attributes=False) for n in ast.walk(current) if isinstance(n, ast.Assert))
    assert before == after, function.name
    count += sum(before.values())
assert git('diff', BASE, SOURCE, '--', 'packages') == b''
(HERE / 'assertion-preservation.json').write_text(json.dumps({
    'sourceSha': SOURCE, 'baseline': BASE, 'originalAssertionsPreserved': count,
    'comparison': 'UTF-8 AST multiset per original function, including duplicates',
    'productDiffEmpty': True,
    'additionalAssertions': 'fixture client closes before loop; loop closes before outer fixture teardown',
}, indent=2) + '\n', encoding='utf-8')
failures = []
for label, tests in [('manual', [MANUAL]), ('mixed-runtime-then-manual', [RUNTIME, MANUAL])]:
    frozen()
    record_path = HERE / (label + '.command.json')
    assert not record_path.exists(), 'Never overwrite raw gate results'
    argv = [PYTHON, str(ROOT / 'scripts/run_tests_isolated.py'), '--', PYTHON,
            '-m', 'pytest', *tests, '-q', '--tb=short', '-o', 'junit_logging=all',
            '--junitxml=' + str(HERE / (label + '.xml'))]
    started = time.monotonic()
    with (HERE / (label + '.log')).open('wb') as log:
        result = subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    cases = list(ET.parse(HERE / (label + '.xml')).iter('testcase'))
    counts = {k: 0 for k in ('passed', 'failed', 'error', 'skipped')}
    order = []
    for case in cases:
        status = ('failed' if case.find('failure') is not None else 'error' if case.find('error') is not None
                  else 'skipped' if case.find('skipped') is not None else 'passed')
        counts[status] += 1
        order.append({'node': case.attrib['classname'].replace('.', '/') + '.py::' + case.attrib['name'], 'status': status})
    if label.startswith('mixed'):
        assert len(cases) == 31
        assert all(n['node'].startswith(RUNTIME + '::') for n in order[:23])
        assert all(n['node'].startswith(MANUAL + '::') for n in order[23:])
    record = {'sourceSha': SOURCE, 'argv': argv, 'cwd': str(ROOT), 'exit': result.returncode,
              'seconds': time.monotonic() - started, 'counts': counts, 'executionOrder': order,
              'junit': label + '.xml', 'log': label + '.log'}
    for kind in ('junit', 'log'):
        record[kind + 'Sha256'] = hashlib.sha256((HERE / record[kind]).read_bytes()).hexdigest()
    frozen()
    record_path.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(record), flush=True)
    if result.returncode:
        failures.append(label)
raise SystemExit(bool(failures))
