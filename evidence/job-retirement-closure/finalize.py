"""Build review artifacts from real command receipts and preserved Git bytes."""
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = 'add4bef7773216dd79793733a50a2ec19a6869c3'
PYTHON = 'D:/project/Pan-main/.venv/Scripts/python.exe'
command = [PYTHON, str(ROOT / 'scripts/run_tests_isolated.py'), '--', PYTHON,
    str(ROOT / 'scripts/job_retirement_closure.py'), '--output', str(OUT / 'inventory.json')]
result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
(OUT / 'inventory-command.json').write_text(json.dumps(dict(command=command, cwd=str(ROOT),
    exit=result.returncode, stdout=result.stdout, stderr=result.stderr), indent=2) + '\n', encoding='utf-8')
assert result.returncode == 2, result

preserved = []
names = subprocess.check_output(['git', 'ls-files', '-z', 'evidence/r6i-repair',
    'evidence/p23-retained-safety', 'evidence/p24-retained-history',
    'evidence/p24-canonical-pins', 'evidence/p24-canonical-pin-repair',
    'docs/reports/R6_I_REPAIR_20261027.md',
    'tests/test_job_delete_safety.py', 'tests/test_job_delete_http_owned.py',
    'tests/test_scheduler_legacy_bridge.py', 'packages/scheduler/store.py',
    'packages/scheduler/time_events.py', 'packages/scheduler/legacy_bridge.py'], cwd=ROOT).decode().split('\0')
for name in filter(None, names):
    original = subprocess.check_output(['git', 'show', BASE + ':' + name], cwd=ROOT)
    current = (ROOT / name).read_bytes()
    # Git autocrlf checkout may differ in line endings; compare exact baseline checkout bytes
    # separately from immutable Git blob, while requiring no tracked diff.
    assert original.replace(b'\r\n', b'\n') == current.replace(b'\r\n', b'\n'), name
    preserved.append(dict(path=name, gitBlob=subprocess.check_output(
        ['git', 'rev-parse', BASE + ':' + name], cwd=ROOT, text=True).strip(),
        currentSha256=hashlib.sha256(current).hexdigest()))
assert not subprocess.check_output(['git', 'diff', BASE, '--', *filter(None, names)], cwd=ROOT)

mapping = json.loads((OUT / 'successors.json').read_text(encoding='utf-8'))
inventory = json.loads((OUT / 'inventory.json').read_text(encoding='utf-8'))['inventory']
commands = {label: json.loads((OUT / (label + '-command.json')).read_text(encoding='utf-8'))
    for label in ('focused', 'p23', 'new-final')}
actual = {row['node']: row['status'] for run in commands.values() for row in run['nodes']}
for row in mapping['originalNodes']:
    row['currentExecution'] = {node: actual.get(node, 'see parameterized result or missing exact execution')
                             for node in row['strictSuccessors']}

def escape(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')

lines = ['# JOB-RETIRE revision2 逐节点审阅', '',
    '原 43 是历史失败集合；P23 原三红是其中子集。下列结果不改写旧证据，也不等同整宿主退役。', '',
    '| 原节点 | 后继/状态 | 保留语义与缺口 |', '|---|---|---|']
for row in mapping['originalNodes']:
    lines.append('| `' + row['node'] + '` | ' + row['status'] + ': ' +
        '<br>'.join('`' + node + '`' for node in row['strictSuccessors']) +
        ' | ' + escape(row['boundary']) + ' |')
lines += ['', '## E/S/U 全量 45 项', '', '| ID | 当前观察 | 证据入口 | 删除前缺口 |', '|---|---|---|---|']
for row in mapping['retirementItems']:
    lines.append('| ' + ' | '.join(escape(row[key]) for key in
        ('id', 'currentObservation', 'currentEvidence', 'remaining')) + ' |')
(OUT / 'node-review.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

dynamic_review = []
for row in inventory['dynamicCandidates']:
    review = 'unresolved; requires manual review'
    if '/e2e/' in row['path']:
        review = 'Browser test fixture loader, not production startup; retained in shipping source inventory'
    elif row['code'] in ("__import__('time')", "__import__('signal')", "__import__('re')", "__import__('uuid')"):
        review = 'Literal Python standard library; does not load legacy host'
    elif row['path'] == 'scripts/check_terminal_deployment.py':
        review = 'Read-only prerequisite checker: finite tuple pyte/psutil/fastapi/uvicorn/websockets/httpx/mcp/jwt'
    dynamic_review.append({**row, 'manualReview': review})
assert not any(row['manualReview'].startswith('unresolved') for row in dynamic_review)
(OUT / 'dynamic-review.json').write_text(json.dumps(dynamic_review, indent=2) + '\n', encoding='utf-8')

validation = dict(baseline=BASE, commands=commands, preserved=preserved,
    counts=dict(originalNodes=len(mapping['originalNodes']), retirementItems=len(mapping['retirementItems']),
        strictSuccessor=sum(r['status'] == 'strict-successor' for r in mapping['originalNodes']),
        productGap=sum(r['status'] == 'product-gap' for r in mapping['originalNodes']),
        otherGap=sum(r['status'] == 'gap' for r in mapping['originalNodes'])),
    interruptedRuns='focused-interrupted.log and p23-interrupted.log have no terminal JUnit; never counted as completed',
    p23Rerun='Only p23.xml/command declares completed three canonical tests; backend artifacts from interrupted run not counted',
    originalEvidenceUnchanged=True, productFilesChanged=[],
    limits='Windows isolated tests; recording providers except P23 real owned process/HTTP. No real data/cohort or deployment acceptance.')
(OUT / 'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'inventoryExit': result.returncode, 'preserved': len(preserved), 'counts': validation['counts']}))
