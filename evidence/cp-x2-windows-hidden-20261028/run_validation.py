"""Source-bound validation runner, records actual exit even for intentional red."""
import json
from pathlib import Path
import subprocess
import sys
import time

GROUPS = {
 'final-platform': ['tests/test_platform_process_windows_hidden.py', 'tests/test_platform_process_contract.py', 'tests/test_hidden_job_token.py', 'tests/test_job_cleanup_failures.py'],
 'final-p23': ['tests/test_job_retained_safety_protocol.py', 'tests/test_job_retained_safety_owned.py', 'tests/test_job_retained_safety_canonical.py', 'tests/test_job_retained_safety_public.py', 'tests/test_job_final_executors.py'],
 'final-logs': ['tests/test_job_process_logs.py'],
 'final-legacy': ['tests/test_job_lifecycle_prevention.py'],
}
name = sys.argv[1]
root = Path(__file__).resolve().parent
sha = subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
command = [sys.executable, 'scripts/run_tests_isolated.py', '--', sys.executable, '-m', 'pytest',
           *GROUPS[name], '-q', '-o', 'addopts=', '--tb=short', '--junitxml='+str(root/(name+'.xml'))]
start = time.monotonic()
with (root/(name+'.log')).open('w',encoding='utf-8') as output:
    result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT)
record = {'sourceSha':sha, 'interpreter':sys.executable, 'version':sys.version,
          'argv':command, 'exit':result.returncode, 'seconds':round(time.monotonic()-start,3)}
(root/(name+'.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
print(json.dumps(record))
raise SystemExit(result.returncode)
