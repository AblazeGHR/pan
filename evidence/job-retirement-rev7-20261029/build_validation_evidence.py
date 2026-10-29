"""Bounded revision7 overlay. Historical parity records remain unchanged."""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = '14380fe47b01d1adbaf69bfc43983b5df7947a80'
SOURCE = '4ef9764c9089a714b9355c5932f0060a68719326'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


final = read(OUT/'immutable-final-command.json')
affected = read(OUT/'affected-http-command.json')
assert final['testedSourceSha'] == affected['testedSourceSha'] == SOURCE
assert final['exit'] == 0 and final['counts'] == {'passed':131,'failure':0,'error':0,'skipped':0}
for label in ('immutable-final', 'affected-http'):
    command = read(OUT/(label+'-command.json'))
    counts = Counter()
    for node in ET.parse(OUT/(label+'.xml')).iter('testcase'):
        counts[next((k for k in ('failure','error','skipped') if node.find(k) is not None),'passed')] += 1
    assert all(counts[k] == v for k,v in command['counts'].items())
passes = {r['node'] for r in final['nodes'] if r['status'] == 'passed'}
red = []
for label in ('qq-original-field-matrix', 'source-aware-validation'):
    old = read(ROOT/'evidence/job-retirement-rev6-20261029'/(label+'-command.json'))
    for row in old['nodes']:
        if row['status'] == 'failure':
            assert row['node'] in passes
            red.append({'node':row['node'],'redSourceSha':'cde7549690d3611b45b0cad3b51c982323e4f213',
                        'redCommand':'evidence/job-retirement-rev6-20261029/'+label+'-command.json',
                        'greenSourceSha':SOURCE,'greenCommand':'immutable-final-command.json','greenStatus':'passed'})
assert len(red) == 8
for relative in ('tests/test_retirement_rev6_parity.py','tests/test_retirement_rev6_session_validation.py'):
    assert not subprocess.check_output(['git','diff',BASE,'HEAD','--',relative],cwd=ROOT).strip()
old = subprocess.check_output(['git','show',BASE+':packages/jobs/final_service.py'],cwd=ROOT).decode('utf-8-sig')
now = subprocess.check_output(['git','show',SOURCE+':packages/jobs/final_service.py'],cwd=ROOT).decode('utf-8-sig')


def methods(source):
    cls = next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name == 'FinalService')
    return {n.name:ast.get_source_segment(source,n) for n in cls.body
            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}


a,b = methods(old),methods(now)
changed = sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))
assert changed == ['_check_literal_product_qq','_check_literal_product_sessions','_references']
diff = subprocess.check_output(['git','diff','-U0',BASE,SOURCE,'--','packages/jobs/final_service.py'],cwd=ROOT)
(OUT/'product.diff').write_bytes(diff)
old_api_sha = subprocess.check_output(['git','rev-parse','68be5bd4^'],cwd=ROOT,text=True).strip()
old_api = subprocess.check_output(['git','show',old_api_sha+':packages/jobs/api.py'],cwd=ROOT).decode('utf-8-sig')
lines = old_api.splitlines()
contract = [{'line':i+1,'context':'\n'.join(lines[max(0,i-8):i+4])} for i,l in enumerate(lines) if 'session_not_found' in l]
(OUT/'original-assign-contract.json').write_text(json.dumps({'sourceSha':old_api_sha,'path':'packages/jobs/api.py',
    'sourceSha256':sha(old_api.encode()),'writeTimeTargetChecks':contract,
    'reason':'Scheduled assign/send_session require an existing fixed target on POST and PATCH; native v2 uses same contract.'},
    ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
matrix = read(ROOT/'evidence/job-retirement-rev6-20261029/acceptance-matrix.json')
updates = []
for record in matrix['originalNodes']:
    if record['ordinal'] in (19,20,25):
        updates.append({'originalNode':record['originalNode'],'previousStatus':record['status'],
            'newEvidenceStatus':'write-validation-proven', 'testedSourceSha':SOURCE,
            'proven':'Trusted product QQ fixed identity/text validation and original v2 Session existence/no-write semantics.',
            'nodes':[r['node'] for r in red if
                (record['ordinal'] == 25 and 'qq_invalid' not in r['node']) or
                (record['ordinal'] == 19 and 'qq_invalid' in r['node'] and r['node'].endswith('-create]')) or
                (record['ordinal'] == 20 and 'qq_invalid' in r['node'] and r['node'].endswith('-update]'))],
            'wholeCapabilityAccepted':False})
dependencies = read(OUT/'interpreter-dependency.json')
ma_xml = Path('D:/project/Pan/.workflow/job-unification-review-20261028/parity-r6-ma-red.xml')
result = {'schema':'pan.retirement.write-validation/revision7','baseSha':BASE,'testedSourceSha':SOURCE,
    'changedProductMethods':changed,'productFileGitSha256':sha(now.encode()),
    'sourceDiff':'product.diff','dependencies':dependencies,'redToGreen':red,'originalNodeUpdates':updates,
    'historicalMatrix':'evidence/job-retirement-rev6-20261029/acceptance-matrix.json',
    'historicalMatrixSha256':sha((ROOT/'evidence/job-retirement-rev6-20261029/acceptance-matrix.json').read_bytes()),
    'unchangedItems':'All other revision6 statuses remain historical evidence, not revalidated full acceptance.',
    'maRedEvidence':{'path':str(ma_xml),'sha256':sha(ma_xml.read_bytes())},
    'commands':{p.name:read(p) for p in OUT.glob('*-command.json')},
    'affectedSharedFixtureFailures':[r for r in affected['nodes'] if r['status']=='failure'],
    'publicationChanged':False,'frozenPlanChanged':False,'wholeMigrationAccepted':False,
    'remaining':['QQ known-failure retry','default assign/shell wrappers','MCP compatibility',
        'monitoring/stable-reset recovery','unknown fallback/clear target decisions','real migration/CI/deployment'],
    'newTestsSha256':sha((ROOT/'tests/test_retirement_rev7_write_validation.py').read_bytes())}
(OUT/'validation-matrix.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'eightUnchangedRedToGreen':len(red),'immutableMatrix':final['counts'],
                  'affectedHTTP':affected['counts'],'changedMethods':changed}))
