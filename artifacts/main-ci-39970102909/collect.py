import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parent
GH=shutil.which('gh')
REPO='AblazeGHR/pan'
RUN='39970102909'
SHA='838ada46bca4fc20d77c60e5e7542c1a0bed7c43'

def command(args,name,timeout=180,attempts=3):
    for attempt in range(attempts):
        path=ROOT/(name if attempt==0 else name+'.attempt'+str(attempt+1))
        with path.open('wb') as out,path.with_name(path.name+'.stderr').open('wb') as err:
            try:code=subprocess.run([GH,*args],stdout=out,stderr=err,timeout=timeout).returncode
            except subprocess.TimeoutExpired:code=124
        print(json.dumps({'name':name,'attempt':attempt+1,'exitCode':code}),flush=True)
        if code==0:
            if attempt:shutil.copyfile(path,ROOT/name)
            return path
        if attempt+1<attempts:time.sleep(10)
    raise RuntimeError('Request failed: '+name)

# Read-only collector: never push, dispatch, or rerun.
before=command(['api',f'repos/{REPO}/actions/runs/{RUN}'],'accepted.json')
accepted=json.loads(before.read_text(encoding='utf-8'))
assert accepted['head_sha']==SHA and (accepted['run_attempt']==1 or accepted['status'] in {'queued','in_progress'})
command(['run','watch',RUN,'--repo',REPO,'--interval','60'],'watch.log',timeout=7200,attempts=1)
run=command(['api',f'repos/{REPO}/actions/runs/{RUN}'],'run.json')
metadata=json.loads(run.read_text(encoding='utf-8'))
assert metadata['head_sha']==SHA and metadata['run_attempt']==1 and metadata['status']=='completed'
jobfile=command(['api',f'repos/{REPO}/actions/runs/{RUN}/attempts/1/jobs?per_page=100'],'jobs.json')
jobs=json.loads(jobfile.read_text(encoding='utf-8'))['jobs']
errors=[]
for job in jobs:
    for name,url in [(f'job-{job["id"]}.log',f'repos/{REPO}/actions/jobs/{job["id"]}/logs'),
                     (f'annotations-{job["id"]}.json',f'repos/{REPO}/check-runs/{job["id"]}/annotations')]:
        try:command(['api',url],name)
        except Exception as exc:errors.append(str(exc))
try:command(['api',f'repos/{REPO}/actions/runs/{RUN}/attempts/1/logs'],'run-logs.zip')
except Exception as exc:errors.append(str(exc))
artifactfile=command(['api',f'repos/{REPO}/actions/runs/{RUN}/artifacts?per_page=100'],'artifacts.json')
artifacts=json.loads(artifactfile.read_text(encoding='utf-8'))['artifacts']
for version in ('3.12','3.14'):
    name='pytest-results-'+version
    candidates=[a for a in artifacts if a['name']==name and a['created_at']>=metadata['run_started_at']]
    if not candidates:
        errors.append('Attempt 1 artifact missing: '+name)
        continue
    artifact=max(candidates,key=lambda a:a['created_at'])
    try:
        path=command(['api',f'repos/{REPO}/actions/artifacts/{artifact["id"]}/zip'],name+'.zip',timeout=300)
        destination=ROOT/'artifacts'/name
        destination.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(path) as z:
            assert all((destination/n).resolve().is_relative_to(destination.resolve()) for n in z.namelist())
            z.extractall(destination)
    except Exception as exc:errors.append(str(exc))
(ROOT/'collection-errors.json').write_text(json.dumps(errors,indent=2),encoding='utf-8')
print(json.dumps({'collectionErrors':errors}),flush=True)
if errors:raise SystemExit(1)
raise SystemExit(subprocess.run([sys.executable,str(ROOT/'reconcile.py')],cwd=ROOT.parents[1]).returncode)
