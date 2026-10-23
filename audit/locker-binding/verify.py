"""Owned final verification; no installed service or real provider requests."""
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
PY=sys.executable
PNPM='D:/node_npm/node_global/pnpm.cmd'
tests=['test_locker_independent_review','test_locker_trusted_binding','test_interaction_runtime',
       'test_interaction_cards','test_interaction_card_api','test_user_locker_lifecycle','test_agent_inbox',
       'test_job_action_templates','test_job_inbox_boundaries','test_automation_retry_parallel']
commands=[
    ('python',ROOT,[PY,'scripts/run_tests_isolated.py','--',PY,'-m','pytest',
        *['tests/'+t+'.py' for t in tests],
        'tests/test_automation_core.py::test_serial_cross_trigger_dynamic_and_disable',
        'tests/test_automation_core.py::test_dynamic_action_disabled_deleted_and_contract_failure',
        '-o','addopts=','-q','--junitxml=audit/locker-binding/python.xml']),
    ('ui',ROOT/'packages/web',[PNPM,'exec','vitest','run','src/views/LockerView.test.tsx',
        'src/views/lockerModel.auth.test.ts','--reporter=default','--reporter=junit','--outputFile='+str(OUT/'ui-final.xml')]),
    ('build',ROOT/'packages/web',[PNPM,'build']),
    ('http',ROOT,[PY,'scripts/run_tests_isolated.py','--',PY,'-m','pytest','tests/test_locker_independent_http.py',
        '-o','addopts=','-q','--junitxml=audit/locker-binding/http-final.xml']),
    ('diff',ROOT,['git','diff','--check']),
]
result={'sourceSha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,encoding='utf-8').strip(),'commands':[]}
for name,cwd,args in commands:
    print('Starting '+name,flush=True)
    finished=subprocess.run(args,cwd=cwd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=150)
    (OUT/('final-'+name+'.log')).write_text(finished.stdout+'\n'+finished.stderr,encoding='utf-8')
    result['commands'].append({'name':name,'cwd':str(cwd),'argv':args,'exitCode':finished.returncode})
    (OUT/'commands.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(name+' exit='+str(finished.returncode),flush=True)
    if finished.returncode:
        print(finished.stdout[-3000:]+finished.stderr[-3000:],flush=True)
        break
raise SystemExit(int(any(r['exitCode'] for r in result['commands'])))
