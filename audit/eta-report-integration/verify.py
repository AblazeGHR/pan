"""Cross-line acceptance, owned profiles/ports/fake CLI; no shared service use."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
PY=sys.executable
PNPM='D:/node_npm/node_global/pnpm.cmd'
names=['eta_report_coexistence','terminal_report_diagnostic','runtime_issues',
    'session_error_log_worker','report_error_context','report_reads','force_steer',
    'locker_independent_review','locker_trusted_binding','job_inbox_migration_drill',
    'job_inbox_boundaries','job_action_templates','job_system_actions','job_completion_recovery',
    'agent_inbox','automation_retry_parallel']
commands=[
    ('python',ROOT,[PY,'scripts/run_tests_isolated.py','--',PY,'-m','pytest',
        *['tests/test_'+n+'.py' for n in names],'-o','addopts=','-q','-rs',
        '--junitxml='+str(OUT/'python.xml')]),
    ('ui',ROOT/'packages/web',[PNPM,'exec','vitest','run','--config','e2e/integration.vite.config.mts','--no-cache',
        'src/views/LockerView.test.tsx','src/views/lockerModel.auth.test.ts',
        'src/components/jobs/UnifiedJobForm.test.tsx','src/components/jobs/JobTemplateEditor.test.tsx',
        '--reporter=default','--reporter=junit','--outputFile='+str(OUT/'ui.xml')]),
    ('typecheck',ROOT/'packages/web',[PNPM,'exec','tsc','-b']),
    ('build',ROOT/'packages/web',[PNPM,'exec','vite','build','--config','e2e/integration.vite.config.mts']),
    ('assets',ROOT/'packages/web',['node','e2e/verify-precompressed-assets.mjs']),
    ('http',ROOT,[PY,'scripts/run_tests_isolated.py','--',PY,'-m','pytest',
        'tests/test_eta_report_integration_http.py','-o','addopts=','-q','--junitxml='+str(OUT/'http.xml')]),
    ('compile',ROOT,[PY,'-m','compileall','-q','packages/core/worker.py','packages/core/session_error_log.py',
        'tests/test_eta_report_coexistence.py','tests/test_eta_report_integration_http.py']),
    ('diff',ROOT,['git','diff','ca464c8d55e9c857aa1fa85652e3dcb3e1f7025e','HEAD','--check','--','packages','docs','tests','audit/eta-report-integration/verify.py']),
]
result={'sourceSha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,encoding='utf-8').strip(),'commands':[]}
for name,cwd,args in commands:
    print('Starting '+name,flush=True)
    r=subprocess.run(args,cwd=cwd,capture_output=True,encoding='utf-8',errors='replace',timeout=150)
    (OUT/(name+'.log')).write_text(r.stdout+'\n'+r.stderr,encoding='utf-8')
    result['commands'].append({'name':name,'cwd':str(cwd),'argv':args,'exitCode':r.returncode})
    (OUT/'commands.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(name+' exit='+str(r.returncode),flush=True)
    if r.returncode:
        print(r.stdout[-4500:]+r.stderr[-2000:],flush=True)
        break
for source in json.loads((OUT/'preserved-inputs.json').read_text(encoding='utf-8')):
    root=Path(source['root'])
    assert subprocess.check_output(['git','status','--porcelain'],cwd=root,encoding='utf-8')==source['status']
    for entry in source['files']:
        assert hashlib.sha256((root/entry['path']).read_bytes()).hexdigest()==entry['sha256']
result['originalSourceAuditsUnchanged']=True
(OUT/'commands.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
raise SystemExit(int(any(row['exitCode'] for row in result['commands'])))
