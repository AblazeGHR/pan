"""Isolated repeatable profile runner; never uses a live Pan registry."""
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[3]
checkout=Path(sys.argv[1])
env={k:v for k,v in os.environ.items() if not k.upper().startswith('PAN_') and not any(w in k.upper() for w in ('TOKEN','SECRET','PASSWORD','API_KEY','AUTH_KEY'))}
env.update(PAN_NAV_BENCH='1')
if len(sys.argv)>4: env['PAN_NAV_PROFILE']='1'
if len(sys.argv)>4: env['PAN_NAV_ONLY']=sys.argv[4]
subprocess.run(['pnpm.cmd','build'],cwd=checkout/'packages/web',env=env,check=True)
for repeat in range(int(sys.argv[3])):
    print(f'profile round {repeat+1}',flush=True)
    subprocess.run(['node',str(ROOT/'packages/web/e2e/navigation-continuous.e2e.mjs'),'continuous',str(checkout),sys.argv[2]],cwd=ROOT,env=env,check=True)
