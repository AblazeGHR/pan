"""Targeted latest-main delete/snapshot/permission combination, no real profile."""
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'audit/job-delete-main-integration'
sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,encoding='utf-8').strip()
tests=['tests/test_job_delete_main_combination.py','tests/test_job_delete_safety.py',
 'tests/test_job_delete_http_owned.py','tests/test_background_jobs.py','tests/test_jobs_api.py',
 'tests/test_job_system_actions.py','tests/test_job_action_templates.py',
 'tests/test_authorization_permissions.py','tests/test_authorization_runtime.py','tests/test_authorization_mcp.py',
 'tests/test_terminal_guard.py','tests/test_job_runner_venv.py']
cmd=[sys.executable,'scripts/run_tests_isolated.py','--',sys.executable,'-m','pytest',*tests,'-o','addopts=','-q',
 '--junitxml=audit/job-delete-main-integration/final-junit.xml']
r=subprocess.run(cmd,cwd=ROOT,capture_output=True,encoding='utf-8',errors='replace')
(OUT/'final.log').write_text(r.stdout+r.stderr,encoding='utf-8')
(OUT/'command.json').write_text(json.dumps({'sourceSha':sha,'command':cmd,'exitCode':r.returncode},indent=2),encoding='utf-8')
print(r.stdout+r.stderr)
raise SystemExit(r.returncode)
