"""Verify trusted new-Worker launch binding; only fake effects and owned HTTP.

No encoding overrides, dependency changes, provider calls or existing services.
Logs/JSON are UTF-8; preserve undecodable raw output separately if necessary.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/provider-launch'
OUT.mkdir(parents=True, exist_ok=True)
ENV = dict(os.environ)
# Exercise the ordinary Windows encoding environment without changing the parent.
for key in ('PYTHONUTF8', 'PYTHONIOENCODING'):
    ENV.pop(key, None)


def output(command):
    return subprocess.check_output(command, cwd=ROOT, env=ENV).decode('utf-8').strip()


def main():
    owned_home = tempfile.TemporaryDirectory(prefix='pan-owned-launch-native-home-')
    ENV['CODEX_HOME'] = owned_home.name
    sha = output(['git', 'rev-parse', 'HEAD'])
    tracked = output(['git', 'diff', '--name-only'])
    if any(not path.startswith('artifacts/') for path in tracked.splitlines()):
        raise RuntimeError('Freeze source before verification')
    probe = output([sys.executable, '-c',
        'import json,sys,locale,os;print(json.dumps(dict(utf8Mode=sys.flags.utf8_mode,stdoutEncoding=sys.stdout.encoding,localeEncoding=locale.getencoding(),PYTHONUTF8=os.environ.get("PYTHONUTF8"),PYTHONIOENCODING=os.environ.get("PYTHONIOENCODING"))))'])
    manifest = {'sourceSha': sha, 'branch': output(['git', 'branch', '--show-current']),
                'cwd': str(ROOT), 'python': sys.executable, 'encoding': json.loads(probe),
                'ownedCodexHome':owned_home.name, 'providerCalls': 0, 'accountSwitches': 0, 'existingServiceOperations': 0, 'checks': []}
    groups = {
        'final-launch': ['tests/test_provider_launch.py'],
        'final-host': ['tests/test_quota_host.py', 'tests/test_quota_provider_source.py'],
        'final-lifecycle-compat': ['tests/test_worker_system_prompt_spawn.py', 'tests/test_legal_offline.py', 'tests/test_codex_adapter.py'],
        'final-http': ['tests/test_provider_launch_http.py'],
    }
    checks = [('diff-check', ['git', 'diff', '--check', 'ff7c5d78', 'HEAD'], None)]
    checks += [(name, [sys.executable, 'scripts/run_tests_isolated.py', '--', sys.executable,
                       '-m', 'pytest', *tests, '-q', '--junitxml=artifacts/provider-launch/'+name+'.xml'],
                OUT/(name+'.xml')) for name, tests in groups.items()]
    checks += [('compile', [sys.executable, '-m', 'compileall', '-q',
                 'packages/core/worker.py', 'packages/core/adapters/codex/provider_launch.py', 'packages/core/adapters/codex/app_server_wrapper.py', 'packages/automation/quota_policy.py', 'packages/automation/quota_host.py', 'packages/automation/quota_provider.py', 'packages/cc_switch_bridge/provider.py', 'packages/automation/quota_identity.py', 'packages/automation/api.py',
                 'packages/core/runtime_issues.py', 'packages/core/session_error_log.py',
                 'packages/jobs/action_runtime.py', 'tests/test_quota_policy.py',
                 'tests/test_provider_launch.py', 'tests/test_provider_launch_http.py', 'tests/fixtures/fixed_codex_server.py', 'tests/support/provider_launch_http_server.py', 'tests/test_quota_host.py', 'tests/test_quota_provider_source.py', 'tests/test_quota_host_http.py', 'tests/support/quota_host_http_server.py', 'tests/fixtures/quota_host_cli.py', 'tests/test_quota_policy_safety.py', 'tests/test_quota_policy_idle.py'], None)]
    for name, command, junit in checks:
        started = time.monotonic()
        historical = ROOT/'artifacts/quota-policy-safety/http-evidence.json'
        previous = None
        try:
            completed = subprocess.run(command, cwd=ROOT, env=ENV, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, timeout=170)
        finally:
            if previous is not None:
                # The existing owned HTTP test writes its earlier evidence path.
                # Keep this run's evidence separately and preserve prior bytes.
                (OUT/'compat-http-evidence.json').write_bytes(historical.read_bytes())
                historical.write_bytes(previous)
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
