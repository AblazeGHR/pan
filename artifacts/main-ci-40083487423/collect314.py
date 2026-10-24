import json,shutil,subprocess,sys,time,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent; REPO='AblazeGHR/pan'; RUN='40083487423'; SHA='9bdd05e96cfcc7682a03fb3bc13b2bf0fd909519'; GH=shutil.which('gh')
def cmd(args,name,timeout=180):
 out=ROOT/name;err=ROOT/(name+'.stderr')
 with out.open('wb') as o,err.open('wb') as e:
  code=subprocess.run([GH,*args],stdout=o,stderr=e,timeout=timeout).returncode
 if code: raise SystemExit(f'{name} exit {code}')
 return out
accepted=json.loads(cmd(['api',f'repos/{REPO}/actions/runs/{RUN}'],'accepted.json').read_text(encoding='utf-8'));assert accepted['head_sha']==SHA
for _ in range(120):
 jobs=json.loads(cmd(['api',f'repos/{REPO}/actions/runs/{RUN}/attempts/1/jobs?per_page=100'],'jobs.json').read_text(encoding='utf-8'))['jobs'];job=next(j for j in jobs if j['name']=='pytest (3.14)')
 print(json.dumps({'job':job['id'],'status':job['status'],'conclusion':job['conclusion']}),flush=True)
 if job['status']=='completed':break
 time.sleep(60)
else:raise SystemExit('3.14 wait exhausted')
cmd(['api',f'repos/{REPO}/actions/jobs/{job["id"]}/logs'],f'job-{job["id"]}.log')
cmd(['api',f'repos/{REPO}/check-runs/{job["id"]}/annotations'],f'annotations-{job["id"]}.json')
arts=json.loads(cmd(['api',f'repos/{REPO}/actions/runs/{RUN}/artifacts?per_page=100'],'artifacts.json').read_text(encoding='utf-8'))['artifacts'];a=max((x for x in arts if x['name']=='pytest-results-3.14'),key=lambda x:x['created_at']);z=cmd(['api',f'repos/{REPO}/actions/artifacts/{a["id"]}/zip'],'pytest-results-3.14.zip',300);dest=ROOT/'artifacts/pytest-results-3.14';dest.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(z) as f:f.extractall(dest)
(ROOT/'collection-errors.json').write_text('[]');print(json.dumps({'run':RUN,'sha':SHA,'python':'3.14','job':job['id'],'conclusion':job['conclusion']}),flush=True)
