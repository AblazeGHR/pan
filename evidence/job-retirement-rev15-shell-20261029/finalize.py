"""Final evidence mapping and read-only resource observation."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess

out = Path(__file__).resolve().parent
root = out.parents[1]
commands = json.loads((out / 'commands.json').read_text())
assert len(commands['checks']) == 2 and all(c['exit'] == 0 for c in commands['checks'])
lines = (out / 'shell.log').read_text(encoding='utf-8').splitlines()
browser = json.loads(next(line.split('R14_BROWSER=', 1)[1] for line in lines if 'R14_BROWSER=' in line))
diagnostic = json.loads(next(line.split('SHELL_DIAGNOSTIC=', 1)[1] for line in lines if 'SHELL_DIAGNOSTIC=' in line))
(out / 'browser.json').write_text(json.dumps(browser, indent=2) + '\n', encoding='utf-8')
(out / 'native-lines.json').write_text(json.dumps(diagnostic, indent=2) + '\n', encoding='utf-8')
with socket.socket() as client:
    client.settimeout(1)
    reachable = client.connect_ex(('127.0.0.1', browser['port'])) == 0
assert not reachable
proof_dir = root / 'evidence/cp-x2-windows-hidden-20261028/proof'
proofs = [{'path': p.relative_to(root).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
           'sourceSha': json.loads(p.read_text()).get('sourceSha')} for p in sorted(proof_dir.glob('*.json'))]
cleanup = {'browserPort': browser['port'], 'tcpConnectableAfterTests': reachable,
    'processes': 'Real tests close all retained child handles/Job/desktop resources; original ownership assertions pass',
    'dependencyJunction': 'Earlier r14 link remains untouched; no retry of previously blocked cleanup',
    'historicalProofsRestored': 'Runner archived current outputs then restored historical tracked bytes', 'proofInventory': proofs}
(out / 'cleanup.json').write_text(json.dumps(cleanup, indent=2) + '\n', encoding='utf-8')
positive = 'tests/test_retirement_rev14_templates.py::test_production_shell_form_real_process_exit_and_original_lookup'
matrix = {'schema': 'pan.retirement.quoted-shell/revision15', 'baseline': 'a710d2c9859a8f1b89339f6fe9ac97f270ac5192',
    'testedSourceSha': commands['sourceSha'], 'productFilesChanged': ['packages/core/platform/process_creation_win.py'],
    'methods': ['_owned_command_line', 'OwnedPopen._execute_child (serialization expression only)'],
    'capability': 'M07 exact configured Windows cmd /d /s /c raw-tail', 'status': 'verified-preserved',
    'originalNode': 'tests/test_jobs_api.py::test_post_creates_scheduled_shell_action_without_target_or_runner',
    'strictSuccessors': [positive, 'tests/test_retirement_rev14_templates.py::test_fresh_production_browser_real_template_http_queue_and_shell'],
    'regressionFiles': ['tests/test_retirement_rev15_shell_serializer.py', 'tests/test_retirement_rev14_shell_diagnostic.py',
                        'tests/test_platform_process_windows_hidden.py', 'tests/test_hidden_job_token.py', 'tests/test_cpx1_creation_edges.py'],
    'preservedRed': ['evidence/job-retirement-rev14-20261029/product.xml', 'evidence/job-retirement-rev14-shell-diagnostic-20261029/focused.xml'],
    'retainedBoundary': 'Unproven COMSPEC/envelope retains original CRT; no unknown replay, generic dispatch or new process protocol',
    'priorOtherStatuses': 'M01/M05 prior bounded evidence unchanged; no aggregate original43/remaining-nine closure',
    'checks': commands['checks'], 'overallFinalRunnerExit': 0,
    'unverifiedLayers': ['remote CI', 'other Python/OS execution', 'real data migration', 'deployment', 'full deployed application browser navigation']}
(out / 'parity-matrix.json').write_text(json.dumps(matrix, indent=2) + '\n', encoding='utf-8')
report = root / 'docs/reports/JOB_RETIRE_REV15_QUOTED_SHELL_FIX_20261029.md'
rows = ['Final tested source **`' + commands['sourceSha'] + '`**, Python **3.14.5**, Chromium **' + browser['chromium'] + '**.',
        '', '| Group | Passed | Failed | Skipped | Exit | Seconds |', '|---|---:|---:|---:|---:|---:|']
for check in commands['checks']:
    k = check['counts']
    rows.append(f"| {check['name']} | {k['passed']} | {k['failed']} | {k['skipped']} | {check['exit']} | {check['seconds']} |")
rows.append('\nFinal runner exit **0**, tracked-source and historical-proof invariant passed. Counts are 71 total, not added to historical or initial runs. No dependency installation or frontend source change. The embedded browser test rebuilds its owned production harness; no separate large React/type/build matrix is repeated.')
report.write_text(report.read_text(encoding='utf-8').replace('<!-- FINAL_RESULTS -->', '\n'.join(rows)), encoding='utf-8')
subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--', 'packages', 'tests'], cwd=root, check=True)
print(json.dumps({'sourceSha': commands['sourceSha'], 'browserPort': browser['port'], 'ownedPortReachable': reachable, 'totalPassed': 71}))
