from pathlib import Path
import hashlib,json,os,subprocess,sys
r=Path(__file__).resolve().parents[2];out=r/'.validation/readiness/inbox-startup-415464/formal';out.mkdir(exist_ok=False)
paths=['packages/web/server.py','packages/interactions/user_cards_api.py','tests/test_inbox_startup.py','tests/support/inbox_startup_probe.py'];before={p:hashlib.sha256((r/p).read_bytes()).hexdigest() for p in paths};(out/'source-before.json').write_text(json.dumps(before),encoding='utf8')
env={k:v for k,v in os.environ.items() if not k.upper().startswith('PAN_') and k.upper()!='PYTHONPATH'};env['PYTHONIOENCODING']='utf-8'
nodes=['tests/test_inbox_startup.py','tests/test_delayed_restart_locked_queue_http.py','tests/test_real_queue_http_e2e.py','tests/test_user_cards.py','tests/test_interaction_card_api.py','tests/test_job_maintenance_startup.py'];results=[]
for version,venv in [('314','ci-venv'),('312','ci312-venv')]:
 py=r/'.validation'/venv/'Scripts/python.exe'
 with (out/(version+'.log')).open('w',encoding='utf8') as f:rc=subprocess.run([str(py),str(r/'scripts/run_tests_isolated.py'),'--',str(py),'-m','pytest',*nodes,'-vv','-ra','--timeout=300','--junitxml='+str(out/(version+'.xml'))],cwd=r,env=env,stdout=f,stderr=subprocess.STDOUT).returncode
 results.append({'python':version,'exitCode':rc});(out/'results.json').write_text(json.dumps(results),encoding='utf8');print(json.dumps(results[-1]),flush=True)
after={p:hashlib.sha256((r/p).read_bytes()).hexdigest() for p in paths};drift=[p for p in paths if before[p]!=after[p]];(out/'source-after.json').write_text(json.dumps(after),encoding='utf8');(out/'source-drift.json').write_text(json.dumps(drift),encoding='utf8');print(json.dumps({'results':results,'sourceDrift':drift}),flush=True);sys.exit(1 if drift or any(x['exitCode'] for x in results) else 0)
