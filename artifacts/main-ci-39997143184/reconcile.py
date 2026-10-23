"""Read completed collection once and retain exact matrix identity evidence."""
from collections import Counter
import hashlib
import json
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
REPO=next(p for p in HERE.parents if (p/'artifacts/main-ci-39913304619/junit-summary.json').is_file())
baseline=json.loads((REPO/'artifacts/main-ci-39913304619/junit-summary.json').read_text(encoding='utf-8'))
old_failures=sorted({(f['classname'],f['name']) for leg in baseline for f in leg['failures']})
run=json.loads((HERE/'run.json').read_text(encoding='utf-8'))
jobs=json.loads((HERE/'jobs.json').read_text(encoding='utf-8'))['jobs']
assert run['head_sha']=='9a06b5f67cff5725c1a9ebe15b8b3b1fa6db68ce'
assert run['status']=='completed'
legs=[]
identities=[]
for version in ('3.12','3.14'):
    job=next(j for j in jobs if j['name']=='pytest ('+version+')')
    assert job['status']=='completed'
    path=HERE/'artifacts'/('pytest-results-'+version)/'pytest-results.xml'
    if path.exists():
        junit_bytes=path.read_bytes()
        tree=ET.ElementTree(ET.fromstring(junit_bytes))
    else:
        with zipfile.ZipFile(HERE/('pytest-results-'+version+'.zip')) as archive:
            name=next(n for n in archive.namelist() if n.endswith('pytest-results.xml'))
            junit_bytes=archive.read(name)
            tree=ET.ElementTree(ET.fromstring(junit_bytes))
    rows=[]
    for case in tree.iter('testcase'):
        outcomes=[c for c in case if c.tag in {'failure','error','skipped'}]
        rows.append({'classname':case.get('classname'),'name':case.get('name'),
            'outcome':outcomes[0].tag if outcomes else 'passed',
            'message':outcomes[0].get('message') if outcomes else None})
    index={(r['classname'],r['name']):r for r in rows}
    identity=Counter((r['classname'],r['name']) for r in rows)
    identities.append(identity)
    old_leg=next(l for l in baseline if 'pytest-results-'+version in l['file'])
    old_skips=Counter((r['classname'],r['name']) for r in old_leg['skips'])
    skips=Counter((r['classname'],r['name']) for r in rows if r['outcome']=='skipped')
    legs.append({'python':version,'jobId':job['id'],'conclusion':job['conclusion'],
        'junitSha256':hashlib.sha256(junit_bytes).hexdigest(),
        'counts':dict(Counter(r['outcome'] for r in rows)),'total':len(rows),
        'failures':[r for r in rows if r['outcome'] in {'failure','error'}],
        'skips':[r for r in rows if r['outcome']=='skipped'],
        'skipMultisetMatchesOriginal':skips==old_skips,'addedSkipIdentities':sorted((skips-old_skips).elements()),'removedSkipIdentities':sorted((old_skips-skips).elements()),
        'original14':[{'classname':k[0],'name':k[1],**({'outcome':index[k]['outcome']} if k in index else {'outcome':'missing'})} for k in old_failures],
        'identities':rows})
gate=run['conclusion']=='success' and all(l['conclusion']=='success' and not l['failures']
    and all(r['outcome']=='passed' for r in l['original14']) for l in legs)
result={'runId':run['id'],'headSha':run['head_sha'],'runConclusion':run['conclusion'],
    'matrixIdentityMultisetsEqual':identities[0]==identities[1],
    'gatePassed':gate,'legs':legs}
(HERE/'gate.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'runId':run['id'],'headSha':run['head_sha'],'gatePassed':gate,
    'legs':[{k:l[k] for k in ('python','jobId','conclusion','counts','total','addedSkipIdentities','removedSkipIdentities')} for l in legs]},ensure_ascii=False),flush=True)
