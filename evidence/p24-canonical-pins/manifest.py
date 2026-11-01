"""Inventory native S13 runs and prove previous P24 evidence is preserved."""
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = '075d857a1f6f39cfe6998edff7e621417770b864'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode().strip()


source = git('rev-parse', 'HEAD')
protected = git('ls-tree', '-r', '--name-only', BASE, 'evidence/p24-retained-history',
    'evidence/r6i-repair', 'docs/reports/P24_RETAINED_HISTORY_20261027.md',
    'docs/reports/R6_I_REPAIR_20261027.md', 'tests/test_jobs_api.py',
    'tests/test_job_retained_pin_successor.py', 'tests/test_scheduler_legacy_bridge.py',
    'tests/test_job_retained_history_bridge.py', 'tests/test_job_retained_history_fixtures.py').splitlines()
assert git('diff', '--name-only', BASE, source, '--', 'packages', 'tests/conftest.py', 'tests/support') == ''
preserved = []
for path in protected:
    blob = git('rev-parse', BASE + ':' + path)
    assert git('hash-object', '--path=' + path, path) == blob, path
    preserved.append({'path': path, 'gitBlob': blob, 'unchanged': True})
runs = []
for path in sorted(OUT.glob('*.xml')):
    suite = ET.parse(path).getroot().find('testsuite')
    counts = {key: int(suite.get(key, 0)) for key in ('tests', 'failures', 'errors', 'skipped')}
    counts['passed'] = counts['tests'] - counts['failures'] - counts['errors'] - counts['skipped']
    runs.append({'label': path.stem, **json.loads(path.with_suffix('.json').read_text(encoding='utf-8')), **counts,
        'nodes': [{'node': row.get('classname').replace('.', '/') + '.py::' + row.get('name'),
                   'result': 'failed' if row.find('failure') is not None else 'error' if row.find('error') is not None
                   else 'skipped' if row.find('skipped') is not None else 'passed'} for row in suite.findall('testcase')]})
artifacts = [{'path': path.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
             for path in sorted(OUT.iterdir()) if path.is_file() and path.name != 'validation.json']
value = {'baselineSha': BASE, 'sourceSha': source, 'interfaceBaseline': 'bdd4c778d55daff58529635b2adc464c03a1393c',
         'integrationBaseline': 'bac726cf84c0510dff8618afb82e227e660f590c',
         'branch': git('branch', '--show-current'), 'sourcePaths': git('diff', '--name-only', BASE, source).splitlines(),
         'runs': runs, 'preserved': preserved, 'artifacts': artifacts}
(OUT / 'validation.json').write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
print(json.dumps([{key: run[key] for key in ('label', 'tests', 'passed', 'failures', 'errors', 'skipped', 'exit')} for run in runs]))
