"""Run targeted terminal/report verification in owned fixtures, never a provider."""
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'audit/terminal-report'
PY=sys.executable
names=['terminal_report_diagnostic','report_error_context','report_subscription',
    'report_reads','manual_task_report','session_error_log','session_error_log_worker',
    'force_steer','self_queue','auto_force_sources','usage_enrichment_commit']
commands=[('python',[PY,'scripts/run_tests_isolated.py','--',PY,'-m','pytest',
    *['tests/test_'+n+'.py' for n in names],'-o','addopts=','-q','-rs',
    '--junitxml=audit/terminal-report/python.xml']),
    ('http',[PY,'scripts/run_tests_isolated.py','--',PY,'-m','pytest',
        'tests/test_terminal_report_http.py','-o','addopts=','-q',
        '--junitxml=audit/terminal-report/http.xml']),
    ('compile',[PY,'-m','compileall','-q','packages/core/worker.py',
        'packages/core/session_error_log.py','tests/test_terminal_report_diagnostic.py',
        'tests/test_terminal_report_http.py','tests/support/session_error_fake_cli.py']),
    ('diff',['git','diff','140455976b7c60097fafdd4dc3f8e3b18a699ccc','HEAD','--check'])]
results={'sourceSha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,encoding='utf-8').strip(),
    'cwd':str(ROOT),'commands':[]}
for name,args in commands:
    print('Starting '+name,flush=True)
    p=subprocess.run(args,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
        encoding='utf-8',errors='replace',timeout=180)
    (OUT/(name+'.log')).write_text(p.stdout,encoding='utf-8')
    results['commands'].append({'name':name,'argv':args,'exitCode':p.returncode})
    (OUT/'commands.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(name+' exit='+str(p.returncode),flush=True)
    if p.returncode:
        print(p.stdout[-6000:],flush=True)
        break
raise SystemExit(int(any(c['exitCode'] for c in results['commands'])))
