"""Run only deletion/runner/guard regressions in the isolated test environment."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'audit/job-delete-lifecycle/review'
TESTS = ['tests/test_job_delete_safety.py', 'tests/test_background_jobs.py',
         'tests/test_jobs_api.py', 'tests/test_job_delete_http_owned.py',
         'tests/test_terminal_guard.py', 'tests/test_job_runner_venv.py']
cmd = [sys.executable, 'scripts/run_tests_isolated.py', '--', sys.executable,
       '-m', 'pytest', *TESTS, '-o', 'addopts=', '-q',
       '--junitxml=audit/job-delete-lifecycle/review/final-junit.xml']
sha = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,encoding='utf-8').strip()
result = subprocess.run(cmd,cwd=ROOT,capture_output=True,encoding='utf-8',errors='replace')
log = result.stdout + result.stderr
(OUT/'final.log').write_text(log,encoding='utf-8')
(OUT/'command.json').write_text(json.dumps({'sourceSha':sha,'command':cmd,'exitCode':result.returncode},indent=2),encoding='utf-8')
print(log)
raise SystemExit(result.returncode)
