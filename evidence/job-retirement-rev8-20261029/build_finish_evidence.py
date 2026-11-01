"""Audit the authorized fixture timing change and complete prior test coverage."""
import ast
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = '877717b0014ed386c6bafbfa3c3443f53aa5f309'
SOURCE = '0dc3cbad7f86c6553dfcfc94032dd5e864749e44'
PATH = 'tests/test_session_product_closure.py'
NAMES = ['test_product_missing_target_rejects_without_spawn',
         'test_product_fanout_partial_unknown_cold_reopen_never_resends']


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def functions(source):
    return {n.name: n for n in ast.parse(source).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}


old = subprocess.check_output(['git','show',BASE+':'+PATH],cwd=ROOT).decode('utf-8-sig')
new = subprocess.check_output(['git','show',SOURCE+':'+PATH],cwd=ROOT).decode('utf-8-sig')
a,b = functions(old), functions(new)
assert [name for name in a if ast.get_source_segment(old,a[name]) != ast.get_source_segment(new,b[name])] == NAMES
audit = []
for name in NAMES:
    original = [ast.get_source_segment(old,n) for n in ast.walk(a[name]) if isinstance(n,ast.Assert)]
    current = [ast.get_source_segment(new,n) for n in ast.walk(b[name]) if isinstance(n,ast.Assert)]
    assert all(assertion in current for assertion in original)
    body = b[name].body
    admission = next(i for i,n in enumerate(body) if isinstance(n,ast.Assign) and any(
        isinstance(t,ast.Name) and t.id == 'admitted' for t in n.targets))
    deletion = next(i for i,n in enumerate(body) if isinstance(n,ast.Expr) and ast.get_source_segment(new,n).startswith('sessions.delete('))
    dispatch = next(i for i,n in enumerate(body) if isinstance(n,ast.Expr) and 'dispatch_once()' in ast.get_source_segment(new,n))
    assert admission < deletion < dispatch
    audit.append({'node':PATH+'::'+name,'originalAssertions':original,'verbatimPreserved':True,
                  'admissionBeforeRealDeletionBeforeDispatch':True})
assert not subprocess.check_output(['git','diff','--name-only',BASE,SOURCE,'--','packages','scripts','tests/conftest.py','tests/support'],cwd=ROOT).strip()
command = load(OUT/'combined-immutable-command.json')
assert command['testedSourceSha'] == SOURCE and command['exit'] == 0
assert command['counts']['failure'] == command['counts']['error'] == command['counts']['skipped'] == 0
passed = {r['node'] for r in command['nodes'] if r['status']=='passed'}
assert len(passed) == len(command['nodes'])
prior = ROOT/'evidence/job-retirement-rev7-20261029'
old131 = load(prior/'immutable-final-command.json')
old64 = load(prior/'affected-http-command.json')
assert len(old131['nodes']) == 131
assert {r['node'] for r in old131['nodes']} <= passed
assert {r['node'] for r in old64['nodes']} <= passed
assert len(list(ET.parse(OUT/'combined-immutable.xml').iter('testcase'))) == len(passed)
red = [r['node'] for r in old64['nodes'] if r['status']=='failure']
assert red == [PATH+'::'+name for name in NAMES]
diff = subprocess.check_output(['git','diff','-U0',BASE,SOURCE,'--',PATH],cwd=ROOT)
(OUT/'fixture-timing.diff').write_bytes(diff)
product = subprocess.check_output(['git','show',SOURCE+':packages/jobs/final_service.py'],cwd=ROOT)
result = {'schema':'pan.retirement.validation-finish/revision8','baseSha':BASE,'testedSourceSha':SOURCE,
    'changedSourceFile':PATH,'changedFunctions':NAMES,'assertionAudit':audit,'productUnchanged':True,
    'productGitBlobSha256':digest(product),'original131Covered':True,'entirePriorAffectedMatrixCovered':True,
    'twoRedToGreen':[{'node':node,'originalSourceSha':old64['testedSourceSha'],
        'originalCommand':'evidence/job-retirement-rev7-20261029/affected-http-command.json',
        'currentStatus':'passed','currentSourceSha':SOURCE} for node in red],
    'counts':command['counts'],'actualExit':command['exit'],'command':command['command'],
    'entireSessionClosureNodeCount':sum(n.startswith(PATH+'::') for n in passed),
    'originalRedArtifacts':{p.name:digest(p.read_bytes()) for p in
        (prior/'affected-http.xml',prior/'affected-http.log',prior/'affected-http-command.json')},
    'environment':{'python':platform.python_version(),'executable':sys.executable,'prefix':sys.prefix,
        'tzlocal':importlib.metadata.version('tzlocal'),'tzdata':importlib.metadata.version('tzdata'),
        'packagesInstalledThisRevision':[]},
    'remainingDesignItems':['QQ retry','default assign/shell form wrappers','MCP compatibility',
        'monitoring/stable-reset','unknown fallback','clear-target decisions'],
    'wholeMigrationAccepted':False,'remoteCIInvoked':False}
(OUT/'finish-matrix.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'counts':command['counts'],'exit':command['exit'],'original131Covered':True,
    'priorAffectedCovered':True,'sessionClosureNodes':result['entireSessionClosureNodeCount']}))
