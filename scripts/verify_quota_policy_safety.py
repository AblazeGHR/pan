"""Verify quota disabled-account and Worker/task safety in the owned isolation.

No encoding overrides, dependency changes, provider calls or existing services.
Logs/JSON are UTF-8; preserve undecodable raw output separately if necessary.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/quota-policy-safety'
OUT.mkdir(parents=True, exist_ok=True)
ENV = dict(os.environ)
# Exercise the ordinary Windows encoding environment without changing the parent.
for key in ('PYTHONUTF8', 'PYTHONIOENCODING'):
    ENV.pop(key, None)


def output(command):
    return subprocess.check_output(command, cwd=ROOT, env=ENV).decode('utf-8').strip()


def main():
    sha = output(['git', 'rev-parse', 'HEAD'])
    tracked = output(['git', 'diff', '--name-only'])
    if any(not path.startswith('artifacts/') for path in tracked.splitlines()):
        raise RuntimeError('Freeze source before verification')
    probe = output([sys.executable, '-c',
        'import json,sys,locale,os;print(json.dumps(dict(utf8Mode=sys.flags.utf8_mode,stdoutEncoding=sys.stdout.encoding,localeEncoding=locale.getencoding(),PYTHONUTF8=os.environ.get("PYTHONUTF8"),PYTHONIOENCODING=os.environ.get("PYTHONIOENCODING"))))'])
    manifest = {'sourceSha': sha, 'branch': output(['git', 'branch', '--show-current']),
                'cwd': str(ROOT), 'python': sys.executable, 'encoding': json.loads(probe),
                'providerCalls': 0, 'accountSwitches': 0, 'existingServiceOperations': 0, 'checks': []}
    groups = {
        'final-policy': ['tests/test_quota_policy_safety.py','tests/test_quota_policy.py',
            'tests/test_runtime_issues.py',
            'tests/test_session_error_log_worker.py::test_codex_result_error_preserves_raw_payload_and_never_impersonates_assistant',
            'tests/test_session_error_log_worker.py::test_tool_item_failure_is_non_terminal'],
        'final-compat': ['tests/test_automation_concurrency.py',
            'tests/test_job_action_templates.py::test_api_permissions_unknown_fields_legacy_aliases_and_repair',
            'tests/test_job_action_templates.py::test_shared_scheduler_entry_and_misfire_do_not_replay_effects',
            'tests/test_job_action_templates.py::test_disabled_and_high_risk_action_gates_block_job_effects',
            'tests/test_job_action_templates.py::test_cancel_exception_shutdown_and_recovery_remove_snapshots',
            'tests/test_session_query.py::test_freezes_once_before_effect_and_reports_partial_failure',
            'tests/test_session_query.py::test_recovery_preserves_frozen_targets_and_never_requeries',
            'tests/test_automation_retry_parallel.py::test_retry_pause_gate_plugin_loss_and_new_generation',
            'tests/test_plugin_events.py::test_service_restart_restores_queued_only_after_actual_plugin_ready'],
        'final-http': ['tests/test_quota_policy_http.py'],
    }
    checks = [('diff-check', ['git', 'diff', '--check', '115e7db2', 'HEAD'], None)]
    checks += [(name, [sys.executable, 'scripts/run_tests_isolated.py', '--', sys.executable,
                       '-m', 'pytest', *tests, '-q', '--junitxml=artifacts/quota-policy-safety/'+name+'.xml'],
                OUT/(name+'.xml')) for name, tests in groups.items()]
    checks += [('compile', [sys.executable, '-m', 'compileall', '-q',
                 'packages/automation/quota_policy.py', 'packages/automation/quota_identity.py', 'packages/automation/api.py',
                 'packages/core/runtime_issues.py', 'packages/core/session_error_log.py',
                 'packages/jobs/action_runtime.py', 'tests/test_quota_policy.py',
                 'tests/test_quota_policy_http.py', 'tests/test_quota_policy_safety.py'], None)]
    for name, command, junit in checks:
        started = time.monotonic()
        completed = subprocess.run(command, cwd=ROOT, env=ENV, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=150)
        raw = completed.stdout
        try:
            log = raw.decode('utf-8')
        except UnicodeDecodeError:
            (OUT/(name+'.raw')).write_bytes(raw)
            log = raw.decode('utf-8', errors='replace')
        (OUT/(name+'.log')).write_text(log.replace('\r\n','\n'), encoding='utf-8', newline='\n')
        check = {'name':name,'argv':command,'exitCode':completed.returncode,
                 'seconds':round(time.monotonic()-started,3),'log':name+'.log'}
        if junit and junit.exists():
            suites = ET.parse(junit).getroot()
            check['junit'] = junit.name
            check['counts'] = {k:sum(int(s.get(k,0)) for s in suites.iter('testsuite'))
                               for k in ('tests','failures','errors','skipped')}
        manifest['checks'].append(check)
        (OUT/'verification.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        print(name+': exit '+str(completed.returncode),flush=True)
        if completed.returncode:
            print(log,flush=True)
            return completed.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
