"""Reproduce owned fixture acceptance; no deployed profile or provider access."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'audit/locker-runtime'
PY=sys.executable
PNPM=shutil.which('pnpm.cmd') or shutil.which('pnpm')
TESTS=[
    'test_interaction_runtime','test_interaction_cards','test_interaction_card_api',
    'test_user_locker_lifecycle','test_agent_inbox','test_job_inbox_migration_drill',
    'test_job_inbox_boundaries','test_job_action_templates','test_job_completion_recovery',
    'test_job_system_actions','test_job_overlap','test_automation_retry_parallel',
]
commands=[
    ('python',ROOT,[PY,'scripts/run_tests_isolated.py','--',PY,'-m','pytest',
        *['tests/'+name+'.py' for name in TESTS],'-o','addopts=','-q',
        '--junitxml=audit/locker-runtime/python.xml']),
    ('ui',ROOT/'packages/web',[PNPM,'exec','vitest','run','views/LockerView.test.tsx',
        'views/lockerModel.auth.test.ts','components/jobs/UnifiedJobForm.test.tsx',
        'components/jobs/JobTemplateEditor.test.tsx','shortcuts/registry.test.ts',
        '--reporter=default','--reporter=junit','--outputFile='+str(OUT/'ui.xml')]),
    ('build',ROOT/'packages/web',[PNPM,'build']),
    ('http',ROOT,[PY,'scripts/run_tests_isolated.py','--',PY,'-m','pytest',
        'tests/test_locker_runtime_http.py','-o','addopts=','-q',
        '--junitxml=audit/locker-runtime/http.xml']),
    ('diff',ROOT,['git','diff','--check']),
]
result={'sourceSha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,encoding='utf-8').strip(),
        'python':PY,'commands':[]}
for name,cwd,args in commands:
    print('Starting '+name,flush=True)
    completed=subprocess.run(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
        encoding='utf-8',errors='replace',timeout=180)
    (OUT/(name+'.log')).write_text(completed.stdout,encoding='utf-8')
    result['commands'].append({'name':name,'cwd':str(cwd),'args':args,'exitCode':completed.returncode})
    (OUT/'commands.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(name+' exit='+str(completed.returncode),flush=True)
    if completed.returncode:
        print(completed.stdout[-4500:],flush=True)
        break
raise SystemExit(int(any(c['exitCode'] for c in result['commands'])))
