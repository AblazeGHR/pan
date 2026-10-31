"""Exact current execution delta; preserve every original assertion and red."""
import copy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = 'f5f05ca5be51e3dbcd1bc4b4cd5b29ebe0ab1597'
original = ROOT / 'evidence/job-retirement-runtime-20261031/current43-matrix.json'
prior = json.loads(original.read_text(encoding='utf-8'))
checks = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(HERE.glob('*.command.json'))]
assert {c['gate'] for c in checks} == {'qq-native', 'qq-fences', 'qq-crash', 'affected',
    'recovery', 'manual-qq', 'browser', 'react', 'build'}, 'Complete bounded gates required'
executed = {}
for check in checks:
    assert check['sourceSha'] == SOURCE
    assert hashlib.sha256((HERE / check['log']).read_bytes()).hexdigest() == check['logSha256']
    if check.get('junit'):
        assert hashlib.sha256((HERE / check['junit']).read_bytes()).hexdigest() == check['junitSha256']
    if not check.get('junit'):
        continue
    for t in ET.parse(HERE / check['junit']).iter('testcase'):
        node = t.attrib['classname'].replace('.', '/') + '.py::' + t.attrib['name']
        executed[node] = {'status': 'failed' if t.find('failure') is not None or t.find('error') is not None
            else 'skipped' if t.find('skipped') is not None else 'passed', 'gate': check['gate'], 'sourceSha': SOURCE}
rows = copy.deepcopy(prior['originalNodes'])
for row in rows:
    row['previousDisposition'] = row['status']
    row['previousStrictSuccessors'] = copy.deepcopy(row.get('strictSuccessors', []))
    row['status'] = 'historical-bounded-not-reaccepted-in-rev25'
    row['currentExecution'] = [{'node': n, **executed[n]} for n in row.get('strictSuccessors', []) if n in executed]

qq = 'tests/test_retirement_rev25_qq_runtime.py::'
def update(index, status, nodes, semantics, remaining):
    row = rows[index - 1]
    row.update(status=status, strictSuccessors=nodes, matchedRetainedSemantics=semantics, remaining=remaining,
        currentExecution=[{'node': n, **executed.get(n, {'status': 'not-run'})} for n in nodes])

update(22, 'verified-targetless-original-plugin-errors-bounded-current',
    [qq + 'test_original_targetless_manual_qq_error_matches_actual_plugin_and_has_no_session_effect[' + p + ']'
     for p in ('None-bot_not_connected', '222222-bot_not_connected', '333333-unknown_bot_uin')],
    'Actual original plugin error equals canonical raw receipt for default/selected/offline/unknown bot. '
    'No Session target/effect, one canonical Run/Attempt/effect, ordinary failed old release never automatically resends.',
    'The three old rev6 red nodes also asserted manual retry must be denied; accepted r10 deliberately permits trusted '
    'no-send manual retry. Their exact 409 assertion remains red at cc3fc708 and is not edited or relabelled green. '
    'Old retired HTTP envelope compatibility and real QQ delivery are not claimed.')
update(23, 'verified-canonical-runtime-opt-in-automatic-backlog',
    [n for n in executed if n.startswith(qq) and 'original_targetless' not in n and 'invalid_runtime' not in n],
    'Real Scheduler fires retain separate original Event identities and committed pending child facts '
    '(dispatchKey/fireAt/text/error) in the original Run plan. Same unfinished Attempt/effect continues automatically '
    'through existing pulse only for original identity-bound no-send; per-fire current target/text/bot, pause/disable/revoke, '
    'CAS/concurrent pulse, hard crash/owner handoff, successful exact removal from pending projection, unknown no-replay.',
    'Opt-in new immutable release only. No real records migrated, no old backlog registry activated or rewritten. '
    'Migration owner must consume the release contract; sending/unknown has no remote query protocol and remains held.')
update(16, 'verified-partial-visible-consumer-owned-production-browser',
    ['tests/test_retirement_rev25_browser.py::test_fresh_browser_original_partial_outbox_ws_duplicate_and_runtime_form_http'],
    'Real original fanout queue produces rejected target plus original WS outbox; fresh production React useWebSocket '
    'and ToastContainer consume server.broadcast actual frames. Original outbox repeated frame yields one visible failed-target warning, '
    'receipt/effect identity and queue stay original through cold reopen.',
    'Current job.updated envelope is used; literal job.partial_failed alias is not restored. Live deployment or real client acceptance is unverified.')
update(42, 'verified-real-delegated-monitoring-revocation-budget',
    ['tests/test_retirement_rev25_monitoring.py::test_real_delegated_monitoring_revocation_preserves_attempt_budget_deadline_and_original_effect'],
    'Actual detached process monitoring rejection schedules original autoRecovery; revoke delegated authority before due pulse, '
    'then cold reopen: original Attempt/effect, recovery budget/window/deadline and one invocation unchanged.',
    'Owned local process only. No real supplier/CLI monitoring or deployment claim; accepted monitoring/recovery algorithm is reused unchanged.')
update(28, 'verified-production-runtime-form-clear-target-pending',
    ['tests/test_retirement_rev25_form.py::test_real_form_instance_clear_target_pending_restore_preserves_business_task_and_frozen_plan'],
    'Real server form selects new assign release, creates passive Template/Job, clears declared runtime target after admission, '
    'no Attempt/effect while absent; restore different target/text queues once with original business task and frozen Run.',
    'Actual API lifecycle plus fresh browser opt-in verified; no real cohort migration. New model uses Event/Run plan pending facts, not legacy file mutation.')
rows[8]['status'] = 'precise-model-conflict-unknown-action-identity-still-open'
rows[8]['remaining'] = ('Original assertion rejects PATCH action.api=create_session, then directly writes that unknown api into legacy record '
    'and expects assign on scheduler tick. Canonical steps require an explicit immutable actionRef and D6 forbids executing unknown foreign actions. '
    'Runtime amendment permits target/payload changes, not Action identity substitution. Safe explicit assign/default forms already exist; '
    'automatic reinterpretation of a corrupt unknown Action requires MA/user plan decision. No generic fallback added.')
for row in rows:
    assert row['originalAssertions'] == prior['originalNodes'][row['ordinal'] - 1]['originalAssertions']
capabilities = copy.deepcopy(prior['capabilities'])
for c in capabilities:
    c['previousBoundary'] = c.get('currentBoundary')
    c['completeCapabilityAccepted'] = False
    c['rev25Evidence'] = []
    if c['id'] == 'M08':
        c['rev25Evidence'] = [n for n in executed if n.startswith(qq)]
        c['currentBoundary'] = 'New immutable QQ runtime release, actual owned HTTP plugin and same-Attempt no-send automatic backlog verified. Original three manual-denial red assertions retained; real QQ migration/deployment not accepted.'
    elif c['id'] in {'M03', 'M04', 'M13', 'M14'}:
        c['rev25Evidence'] = [n for n in executed if 'rev25_form' in n or 'rev25_browser' in n]
        c['currentBoundary'] = 'Production form runtime opt-in and real owned WS/browser failed-target consumption verified; existing Session and CAS regression evidence separate. Historical full capability/migration acceptance not inferred.'
    elif c['id'] == 'M15':
        c['rev25Evidence'] = [n for n in executed if 'rev25_monitoring' in n]
        c['currentBoundary'] = 'Exact delegated monitoring revoke preserves original attempts/effects/budget/deadline via accepted monitoring/recovery implementation. Broader triggers remain prior bounded evidence.'
matrix = {'revision': 25, 'sourceSha': SOURCE, 'historicalMatrix': str(original.relative_to(ROOT)),
    'historicalMatrixSha256': hashlib.sha256(original.read_bytes()).hexdigest(), 'originalNodeCount': 43,
    'originalNodes': rows, 'remainingNine': [copy.deepcopy(rows[r['ordinal'] - 1]) for r in prior['remainingNine']],
    'capabilities': capabilities, 'executed': executed,
    'wholeMigrationAccepted': False, 'realMigrationPerformed': False,
    'separateMigrationInput': '09c1dff49f467de9fff2e522c15750363eb6d5c8 not merged during tests',
    'unverified': ['real profiles/accounts/QQ/gateway/CLI', 'remote CI/other platforms', 'actual migration activation/deployment',
        'legacy unknown Action fallback decision', 'migration mapper consumption of new QQ release',
        'production five-second QQ HTTP timeout latency on this Windows fixture (native tests use real fixture client timeout30)']}
(HERE / 'current43-matrix.json').write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
review = ['# Revision25 original-node disposition', '', 'Immutable source: `' + SOURCE + '`.', '',
    'All original assertions and historical red provenance remain in current43-matrix.json. '
    'Counts below are exact current execution evidence, not all original nodes green.', '']
for r in rows:
    review += ['## ' + str(r['ordinal']) + '. `' + r['originalNode'] + '`', '',
        '**' + r['status'] + '**. ' + r.get('matchedRetainedSemantics', ''), '',
        'Remaining: ' + r.get('remaining', '') + '.', '', 'Registered successor IDs:', '']
    review += ['- `' + n + '`' for n in r.get('strictSuccessors', [])] or ['- No current strict successor.']
    review += ['', 'Execution: ' + (', '.join('`' + n['node'] + '` ' + n['status'] for n in r['currentExecution']) or 'historical only in this batch') + '.', '']
(HERE / 'node-review.md').write_text('\n'.join(review).rstrip() + '\n', encoding='utf-8')
(HERE / 'commands.json').write_text(json.dumps({'sourceSha': SOURCE, 'checks': checks}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'executed': len(executed), 'failures': [n for n,v in executed.items() if v['status'] == 'failed'],
    'originalNodes': len(rows)}))
