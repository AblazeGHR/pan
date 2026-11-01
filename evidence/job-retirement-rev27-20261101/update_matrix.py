import copy, hashlib, json
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
prior_path=ROOT/'evidence/job-retirement-rev26-20261031/current43-matrix.json'
prior=json.loads(prior_path.read_text(encoding='utf-8'))
m=copy.deepcopy(prior)
m['revision']=27
m['sourceSha']='fee7ffa0c3439668597ab3c58f3e218751bb9b8f'
m['historicalMatrix']=str(prior_path.relative_to(ROOT))
m['historicalMatrixSha256']=hashlib.sha256(prior_path.read_bytes()).hexdigest()
m['rev27Evidence']={'focused':json.loads((HERE/'rev27-focused.command.json').read_text()),'legacyRed':json.loads((HERE/'legacy-surface-red.command.json').read_text()),'actualMcp':'tests/test_retirement_rev16_mcp_stdio.py::test_retired_names_actual_stdio_boundary_and_authorized_canonical_path','retainedBehavior':'tests/test_retirement_rev16_behavior.py exact schedule replacement/disable/cancel/partial notification successors','combinedAcceptance':False}
for r in m['originalNodes']:
    if r['ordinal'] in (2,6,16,17,18,20,21,24,25,26):
        r.setdefault('rev27Evidence',[]).append('rev27-focused:passed')
        r['remaining']=r.get('remaining','')+' Rev27 focused successor passed; production deployment/profile migration remains unverified.'
for r in m['originalNodes']:
    assert r['originalAssertions']==prior['originalNodes'][r['ordinal']-1]['originalAssertions']
(HERE/'current43-matrix.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'revision':27,'originalNodes':len(m['originalNodes']),'focusedPassed':14,'legacyRed':13,'assertionsPreserved':True}))
