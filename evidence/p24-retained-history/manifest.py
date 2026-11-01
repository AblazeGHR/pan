"""Rebuild the P24 evidence inventory after validation and source commit."""
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = 'bdd4c778d55daff58529635b2adc464c03a1393c'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


source = git('rev-parse', 'HEAD').decode().strip()
paths = ['tests/test_jobs_api.py', 'tests/test_scheduler_legacy_bridge.py']
with zipfile.ZipFile(OUT / 'original-test-sources.zip', 'w', zipfile.ZIP_DEFLATED) as bundle:
    for path in paths:
        bundle.writestr(path, git('show', BASE + ':' + path))
protected = ['packages/scheduler/store.py', 'packages/scheduler/time_events.py',
             'packages/scheduler/legacy_bridge.py', 'tests/test_scheduler_legacy_bridge.py',
             'tests/test_job_retained_pin_successor.py', 'docs/reports/R6_I_REPAIR_20261027.md']
protected += git('ls-tree', '-r', '--name-only', BASE, 'evidence/r6i-repair').decode().splitlines()
preserved = []
for path in protected:
    before = git('show', BASE + ':' + path)
    current = (ROOT / path).read_bytes()
    # Git line-ending conversion is allowed; repository blobs must be exact.
    unchanged = git('hash-object', '--path=' + path, path).strip() == git('rev-parse', BASE + ':' + path).strip()
    assert unchanged, path
    preserved.append({'path': path, 'baselineBlob': git('rev-parse', BASE + ':' + path).decode().strip(),
                      'currentSha256': hashlib.sha256(current).hexdigest(), 'unchanged': unchanged})
runs = []
for path in sorted(OUT.glob('*.xml')):
    suite = ET.parse(path).getroot().find('testsuite')
    meta = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    counts = {key: int(suite.get(key, 0)) for key in ('tests', 'failures', 'errors', 'skipped')}
    counts['passed'] = counts['tests'] - counts['failures'] - counts['errors'] - counts['skipped']
    runs.append({'label': path.stem, **meta, **counts,
        'nodes': [{'node': case.get('classname').replace('.', '/') + '.py::' + case.get('name'),
                   'result': 'failed' if case.find('failure') is not None else 'error' if case.find('error') is not None
                   else 'skipped' if case.find('skipped') is not None else 'passed'} for case in suite.findall('testcase')]})
artifacts = [{'path': p.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(OUT.iterdir()) if p.is_file() and p.name != 'validation.json']
value = {'baselineSha': BASE, 'sourceSha': source,
         'integrationBaseline': 'bac726cf84c0510dff8618afb82e227e660f590c',
         'branch': git('branch', '--show-current').decode().strip(),
         'sourcePaths': git('diff', '--name-only', BASE, source).decode().splitlines(),
         'runs': runs, 'preserved': preserved, 'artifacts': artifacts}
(OUT / 'validation.json').write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps([{k: row[k] for k in ('label', 'tests', 'passed', 'failures', 'errors', 'skipped', 'exit')} for row in runs]))
