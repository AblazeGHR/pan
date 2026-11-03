"""One bounded durable PTY CI waiter, including initial new-workflow registration."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

p=argparse.ArgumentParser()
p.add_argument('--sha',required=True)
p.add_argument('--output',required=True,type=Path)
a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=True)
deadline=time.monotonic()+180
while time.monotonic()<deadline:
    runs=json.loads(subprocess.check_output(['gh','run','list','--workflow','x5-pty.yml',
        '--branch','validation/x5-pty-20261104','--limit','10','--json','databaseId,headSha'],text=True))
    exact=[r for r in runs if r['headSha']==a.sha]
    if exact:
        if len(exact)!=1:
            raise RuntimeError('multiple exact PTY runs; refusing ambiguous watcher')
        raise SystemExit(subprocess.run([sys.executable,str(Path(__file__).with_name('x5_wait_ci.py')),
            '--run',str(exact[0]['databaseId']),'--sha',a.sha,'--output',str(a.output)]).returncode)
    time.sleep(15)
(a.output/'discovery-timeout.json').write_text(json.dumps({'sha':a.sha,'outcome':'unknown',
    'reason':'no exact push workflow discovered within deadline'}),encoding='utf-8')
raise SystemExit(2)
