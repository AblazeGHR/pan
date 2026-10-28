"""Run exact frozen-source groups with actual isolated test exit evidence."""
import json
from pathlib import Path
import subprocess
import sys
import time
root=Path(__file__).resolve().parent
name=sys.argv[1]
groups={
 'combination':['tests/test_lifecycle_followthrough.py','tests/test_lifecycle_controller_publication.py','tests/test_platform_process_windows_hidden.py','-p','tests.support.lifecycle_strict_options','--cpx1-require-hidden-desktop'],
 'safety':['tests/test_platform_process_contract.py','tests/test_platform_process_posix_unit.py','tests/test_hidden_job_token.py','tests/test_job_cleanup_failures.py','tests/test_job_retained_safety_protocol.py'],
 'p23':['tests/test_job_retained_safety_owned.py','tests/test_job_retained_safety_canonical.py','tests/test_job_retained_safety_public.py','tests/test_job_final_executors.py'],
}
command=[sys.executable,'scripts/run_tests_isolated.py','--',sys.executable,'-m','pytest',*groups[name],'-q','-o','addopts=','--tb=short','--junitxml='+str(root/(name+'.xml'))]
sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();start=time.monotonic()
with (root/(name+'.log')).open('w',encoding='utf-8') as output:
 result=subprocess.run(command,stdout=output,stderr=subprocess.STDOUT)
record={'sourceSha':sha,'argv':command,'interpreter':sys.executable,'version':sys.version,'exit':result.returncode,'seconds':round(time.monotonic()-start,3)}
(root/(name+'.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
print(json.dumps(record))
raise SystemExit(result.returncode)
