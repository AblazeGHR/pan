"""Reproduce the frozen targeted suite with explicit command/result metadata."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
out = Path(__file__).resolve().parent
python = sys.executable
selections = [
    'tests/test_event_trigger_reliability.py',
    'tests/test_automation_core.py',
    'tests/test_automation_concurrency.py',
    'tests/test_automation_retry_parallel.py',
    'tests/test_event_store.py',
    'tests/test_plugin_events.py',
    'tests/test_plugin_event_lifecycle.py',
    'tests/test_locker_trusted_binding.py',
    'tests/test_locker_independent_review.py',
    'tests/test_job_action_templates.py',
    'tests/test_eta_report_coexistence.py',
    'tests/test_terminal_report_diagnostic.py',
    'tests/test_report_action_coexistence.py',
    'tests/test_job_inbox_migration_drill.py',
    'tests/test_session_query.py::test_freezes_once_before_effect_and_reports_partial_failure',
    'tests/test_session_query.py::test_recovery_preserves_frozen_targets_and_never_requeries',
    'tests/test_session_query.py::test_real_session_mutations_after_checkpoint_do_not_reselect',
    'tests/test_event_store_http.py',
    'tests/test_terminal_report_http.py',
]
command = [python, str(root/'scripts/run_tests_isolated.py'), '--', python, '-m', 'pytest',
           *selections, '-o', 'addopts=', '-q', '--junitxml='+str(out/'final.xml')]
metadata = {'sourceSha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,encoding='utf-8').strip(),
            'cwd':str(root),'command':command,'realProvider':False}
with (out/'final.log').open('w',encoding='utf-8') as stream:
    result = subprocess.run(command,cwd=root,stdout=stream,stderr=subprocess.STDOUT)
metadata['exitCode'] = result.returncode
# Preserve new generated evidence, then restore only these known tracked artifacts.
changed = subprocess.check_output(['git','diff','--name-only'],cwd=root,encoding='utf-8').splitlines()
tracked = [name for name in changed if name.startswith(('artifacts/event-plugin-adapter/', 'artifacts/event-store/'))]
for name in tracked + ['audit/terminal-report/http/evidence.json','audit/terminal-report/http/delivered.txt']:
    source = root/name
    if source.is_file():
        target = out/'observed-final'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
if tracked:
    subprocess.run(['git','restore','--',*tracked],cwd=root,check=True)
(out/'validation.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
print(json.dumps(metadata,indent=2))
print((out/'final.log').read_text(encoding='utf-8')[-5000:])
sys.exit(result.returncode)
