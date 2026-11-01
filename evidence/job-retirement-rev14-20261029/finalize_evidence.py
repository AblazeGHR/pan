"""Derive the final versioned matrix from actual immutable-run artifacts."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

out = Path(__file__).resolve().parent
commands = json.loads((out / 'commands.json').read_text())
checks = commands['checks']
assert {c['name'] for c in checks} == {'product', 'runtime', 'react', 'build'}
prefix = 'tests/test_retirement_rev14_templates.py::'
def tests(*names):
    return [prefix + name for name in names]
matrix = {
    'schema': 'pan.retirement.passive-template/revision14',
    'baseline': 'aad8dd82eaa137a5e456a3c49696dd7387545249',
    'testedSourceSha': commands['sourceSha'],
    'historicalArtifactsUnchanged': 'r6-r13; original successors.json and revision6 acceptance-matrix.json',
    'capabilities': [
        {'id': 'M01', 'status': 'bounded-passive-template-preserved',
         'strictSuccessors': tests('test_zero_creator_complete_template_default_disabled_same_key_cold_reopen',
                                  'test_real_grants_passive_activation_revoke_and_source_authority',
                                  'test_passive_instantiate_failure_rolls_back_and_recovers_same_key'),
         'limit': 'No overall M01 or historical suite acceptance implied'},
        {'id': 'M05', 'status': 'simple-default-assign-production-entry-verified',
         'originalNode': 'tests/test_jobs_api.py::test_action_template_default_assign',
         'strictSuccessors': tests('test_production_form_assign_queue_source_frozen_identity_cold_no_duplicate',
                                  'test_fresh_production_browser_real_template_http_queue_and_shell'),
         'limit': 'Precomposed fixed assign entry, not obsolete scheduled API or unknown generic dispatch'},
        {'id': 'M07', 'status': 'partial-quoted-Windows-command-product-gap',
         'originalNode': 'tests/test_jobs_api.py::test_post_creates_scheduled_shell_action_without_target_or_runner',
         'strictSuccessors': tests('test_fresh_production_browser_real_template_http_queue_and_shell'),
         'retainedRed': tests('test_production_shell_form_real_process_exit_and_original_lookup'),
         'limit': 'Simple shell real exit/file effect preserved; quoted executable fails; scheduled regression is a separate claim'}
    ],
    'productGap': {
        'symbol': 'packages/core/platform/process_creation_win.py::OwnedPopen._execute_child',
        'scope': 'CreateProcess command-line serialization around subprocess.list2cmdline(values[args])',
        'observed': 'Exact raw command reaches process Action; actual cmd exit1, marker absent, Run failed, owned cleanup confirmed',
        'decision': 'No repair in this scope; MA coordinate narrow platform serialization contract/tests before edits'
    },
    'oldNegativeCorrection': {
        'baselineEvidence': 'baseline-zero-creator-red.xml',
        'baselineExit': 1,
        'actual': '422 capability_unsupported, expected201',
        'reason': 'Documented complete passive Template capability, not a new Scheduler requirement',
        'preservedEvidence': 'All r13 raw negative artifacts retained; current frontend assertion now positive'
    },
    'checks': checks,
    'remaining': ['quoted Windows command serialization', 'monitoring/stable-reset', 'unknown fallback/clear-target',
                  'unrelated UI flows', 'real migration', 'deployment'],
    'unverifiedLayers': ['POSIX shell execution', 'remote CI in this revision', 'real accounts/data', 'deployed application and full app browser shell'],
    'notClaimed': 'No aggregate original43/remaining-nine closure, no generic fallback, no production delivery acceptance'
}
(out / 'parity-matrix.json').write_text(json.dumps(matrix, indent=2) + '\n', encoding='utf-8')
report = out.parents[1] / 'docs/reports/JOB_RETIRE_REV14_PASSIVE_TEMPLATE_20261029.md'
rows = ['| Check | Actual result | Exit | Seconds |', '|---|---|---|---|']
for c in checks:
    if 'counts' in c:
        k = c['counts']
        detail = f"{k['passed']} passed / {k['failedOrError']} failed or error / {k['skipped']} skipped ({k['tests']} total)"
    else:
        detail = 'TypeScript + production build; see build.log'
    rows.append(f"| {c['name']} | {detail} | {c['exit']} | {c['seconds']} |")
dedicated = [r for r in ET.parse(out / 'product.xml').getroot().iter('testcase')
             if r.attrib.get('classname') == 'tests.test_retirement_rev14_templates']
rows.append(f"\nDedicated backend subset: {len(dedicated)} tests, included in product counts; browser scenario is one test, not an extra added count. Overall runner exit is {max(c['exit'] for c in checks)}. No skip/xfail added. Python {commands['python']}; Node {commands['node']}; pnpm {commands['pnpm']}; Chromium 153.0.8010.12. No dependency installation.")
report.write_text(report.read_text(encoding='utf-8').replace('<!-- FINAL_COUNTS -->', '\n'.join(rows)), encoding='utf-8')
