"""Record exact isolated commands and actual exit codes; no environment install."""
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
PYTHON = 'D:/project/Pan-main/.venv/Scripts/python.exe'
label, *nodes = sys.argv[1:]
extra = ['-p', 'tests.job_retirement_closure_capture'] if label == 'p23' else []
command = [PYTHON, str(ROOT / 'scripts/run_tests_isolated.py'), '--', PYTHON, '-m', 'pytest',
           *nodes, *extra, '-q', '--tb=short', '--junitxml=' + str(OUT / (label + '.xml'))]
started = time.monotonic()
source = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
bound_files = ['packages/actions/qq_channel_provider.py', 'packages/actions/typed_executor.py',
               'packages/jobs/final_service.py', 'tests/test_retirement_rev9_qq_http.py']
hashes = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in bound_files}
with (OUT / (label + '.log')).open('w', encoding='utf-8') as log:
    completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
rows = []
for case in ET.parse(OUT / (label + '.xml')).iter('testcase'):
    state = next((name for name in ('failure', 'error', 'skipped') if case.find(name) is not None), 'passed')
    rows.append(dict(node=case.get('classname').replace('.', '/') + '.py::' + case.get('name'), status=state))
result = dict(command=command, cwd=str(ROOT), exit=completed.returncode,
    testedSourceSha=source,
    boundFileSha256=hashes,
    sourceStable=(source == subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        and hashes == {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in bound_files}),
    productFileSha256=hashlib.sha256((ROOT/'packages/jobs/final_service.py').read_bytes()).hexdigest(),
    elapsedSeconds=round(time.monotonic() - started, 3), nodes=rows,
    counts={status: sum(row['status'] == status for row in rows) for status in ('passed', 'failure', 'error', 'skipped')})
(OUT / (label + '-command.json')).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps({key: result[key] for key in ('exit', 'elapsedSeconds', 'counts')}))
raise SystemExit(completed.returncode)
