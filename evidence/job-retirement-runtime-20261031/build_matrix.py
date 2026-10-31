"""Versioned parity audit. Historical assertions and red artifacts stay intact."""
import copy
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OLD = ROOT / 'evidence/job-retirement-rev16-20261029/current43-matrix.json'
prior = json.loads(OLD.read_text(encoding='utf-8'))
source = json.loads((HERE / 'commands.json').read_text(encoding='utf-8'))
executed = {}
for command in source['checks']:
    if not command.get('junit'):
        continue
    for node in ET.parse(HERE / command['junit']).iter('testcase'):
        name = node.attrib['classname'].replace('.', '/') + '.py::' + node.attrib['name']
        executed[name] = {'status': 'failed' if node.find('failure') is not None or node.find('error') is not None
                         else 'skipped' if node.find('skipped') is not None else 'passed',
                         'command': command['name'], 'sourceSha': source['sourceSha']}
rows = copy.deepcopy(prior['originalNodes'])
for row in rows:
    row['previousDisposition'] = row['status']
    row['previousStrictSuccessors'] = copy.deepcopy(row.get('strictSuccessors', []))
    row['status'] = 'historical-not-reaccepted-in-this-batch'
    row['priorEvidence'] = copy.deepcopy(row.get('successorAudit', []))
    row['currentExecution'] = []
    for node in row.get('strictSuccessors', []) + row.get('currentRedEvidence', []):
        if node in executed:
            row['currentExecution'].append({'node': node, **executed[node]})

def update(index, status, nodes, meaning, remaining):
    row = rows[index - 1]
    row.update(status=status, strictSuccessors=nodes, matchedRetainedSemantics=meaning, remaining=remaining,
               currentExecution=[{'node': n, **executed.get(n, {'status': 'not-run'})} for n in nodes])

behavior = 'tests/test_retirement_rev16_behavior.py::'
backlog = 'tests/test_retirement_rev17_backlog.py::'
delivery = 'tests/test_retirement_session_runtime.py::'
notify = 'tests/test_retirement_notification_runtime.py::'
update(16, 'notification-detail-fixed-ws-consumer-unverified', [behavior + 'test_partial_fanout_real_results_notice_receipt_once_and_cold_no_replay',
    behavior + 'test_partial_failure_terminal_notice_identifies_actual_failed_target'],
    'Original per-target actual queue/rejection and parent terminal notice carry failed target/results; one original outbox receipt and no cold replay.',
    'Production WS envelope is job.updated with failedTargets/targetResults; original job.partial_failed toast/consumer parity is still unverified, not closed by outbox/queue tests.')
update(28, 'verified-core-lifecycle-product-caller-migration-open',
    [backlog + 'test_pending_event_cold_retarget_preserves_original_identity_exactly_once',
     backlog + 'test_pending_event_pause_does_not_consume_original_backlog',
     backlog + 'test_pending_backlog_authority_revoked_no_admission_or_send',
     delivery + 'test_mixed_accepted_and_proven_unsent_cold_retarget_same_live_attempt',
     'tests/test_retirement_trigger_backlog.py::test_published_event_multitick_cold_resume_same_target_id_once[pause-resume]',
     'tests/test_retirement_trigger_backlog.py::test_published_event_multitick_cold_resume_same_target_id_once[disable-enable]'],
    'Existing published Event backlog plus same live canonical Attempt: current target/text before acceptance; accepted/unknown children frozen, known-unsent child only resumes; original red retained.',
    'Migration mapper and simple production creation callers must opt into new immutable runtime releases. This batch does not activate/migrate real records; old flattened undeliveredFires representation is not restored.')
update(29, 'verified-runtime-opt-in-current',
    [delivery + 'test_real_http_concurrent_cas_during_first_acceptance_selects_only_winning_unsent_values'],
    'Actual HTTP CAS winner supplies text only to still-unsent target; accepted target retains original bytes, same frozen Run and same Attempt/effect.',
    'Only explicit new runtime releases plus Job declaration. Old releases intentionally remain frozen; production migration/caller opt-in not claimed.')
for index in (6, 10, 11, 14, 19, 20, 25, 26, 27, 43):
    row = rows[index - 1]
    required = row.get('strictSuccessors', [])
    if required and all(n in executed and executed[n]['status'] == 'passed' for n in required):
        row['status'] = 'verified-bounded-current'
rows[8]['status'] = 'model-conflict-not-resolved'
rows[21]['status'] = 'historical-bounded-exact-node-not-reaccepted'
rows[22]['status'] = 'required-automatic-qq-backlog-capability-not-addressed'
rows[41]['status'] = 'separate-accepted-recovery-combination-unverified'
rows[41]['remaining'] = ('Recovery/monitoring source 0bab904 was accepted separately by MA; not merged in this tree. '
    'Generic automatic-recovery tests passed here but do not by themselves cover this original business node. '
    'MA combination plus exact owner/revocation/budget original successor remains required; not a new model decision.')

remaining = [copy.deepcopy(rows[r['ordinal'] - 1]) for r in prior['remainingNine']]
capabilities = copy.deepcopy(prior.get('capabilities', []))
for capability in capabilities:
    capability['previousBoundary'] = capability.get('currentBoundary')
    capability['currentBatchEvidence'] = []
    capability['completeCapabilityAccepted'] = False
groups = {
    'M01': ('test_retirement_rev17_process_notice.py::',),
    'M03': ('test_retirement_session_runtime.py::', 'test_retirement_notification_runtime.py::'),
    'M04': ('test_retirement_session_runtime.py::test_mixed_',
            'test_retirement_rev16_behavior.py::test_partial_'),
    'M05': ('test_retirement_session_runtime.py::',),
    'M06': ('test_retirement_rev17_backlog.py::test_pending_', 'test_retirement_trigger_backlog.py::'),
    'M08': ('test_retirement_rev7_write_validation.py::test_product_qq_',
            'test_retirement_rev6_parity.py::test_real_product_qq_invalid_'),
    'M09': ('test_retirement_rev6_resume_native.py::',),
    'M12': ('test_retirement_trigger_backlog.py::', 'test_retirement_rev17_backlog.py::test_pending_'),
    'M13': ('test_retirement_rev16_behavior.py::test_job_lifecycle_',
            'test_retirement_session_runtime.py::test_current_gate_',
            'test_retirement_notification_runtime.py::test_runtime_notice_authority_'),
    'M14': ('test_retirement_notification_runtime.py::test_fresh_',),
    'M15': ('test_retirement_trigger_backlog.py::', 'test_quota_native_admission.py::'),
}
boundaries = {
    'M01': 'Actual owned detached completion and original notice ID retain exit/log/creator/target payload. Other background lifecycle observations are historical, not all rerun.',
    'M03': 'New explicitly opted-in Session runtime releases preserve same live Attempt; pending no-attempt/unknown and notification receipt lifecycle proved. Migration/caller opt-in remains open.',
    'M04': 'Actual original fanout loop preserves accepted child identity and only continues explicit unsent children, including mixed unknown and crash recovery. Partial detail is fixed; old WS toast/consumer remains unverified.',
    'M05': 'Original assign queue with new immutable runtime declaration and current unsent target/text, source/owner/CAS fences, no extra Attempt. Scheduled migration/caller opt-in is not claimed.',
    'M06': 'Existing Event backlog and new same-Attempt delivery runtime are separately tested. Temporary Trigger inactivity no longer consumes Event; accepted effects stay original. Production migration/caller remains open.',
    'M08': 'Only fixed-field POST/PUT validation is reaccepted here. Prior QQ manual retry/plugin evidence is retained; automatic backlog remains a real capability gap, selected-bot inconsistency is not silently resolved.',
    'M09': 'Native candidate rescan, original Wake identity and empty no-op reverified. No live CLI/Worker acceptance claim.',
    'M12': 'Publish-pause/disable-multitick-cold-resume uses original Event/Trigger and unique effect; content revision refresh, true nonmatch/retire and accepted replay gates verified. Historical skipped rows not repaired.',
    'M13': 'Current Job pause/cancel/revoke gates preserve accepted identity and block fresh child reservation. Prior pin/GC/retention not rerun. Runtime retarget is approved implementation, not a pending model decision.',
    'M14': 'Fresh production browser edits explicit Notification Manager policy through real canonical HTTP and reads original receipt. Existing simple assign/shell production callers and migration mapper are not converted to new releases in this batch.',
    'M15': 'Current same-Trigger revision/selector/filter/auth validation and native quota original-identity query are bounded proofs. Monitoring/stable-reset 0bab904 is separate, not merged or reaccepted here.',
}
for capability in capabilities:
    cid = capability['id']
    if cid in groups:
        capability['currentBatchEvidence'] = [n for n in executed if any(p in n for p in groups[cid])]
        capability['currentBoundary'] = boundaries[cid]
matrix = {'schema': 'retirement-runtime-parity-v1', 'sourceSha': source['sourceSha'],
    'historicalMatrix': str(OLD.relative_to(ROOT)), 'historicalMatrixSha256': hashlib.sha256(OLD.read_bytes()).hexdigest(),
    'originalNodeCount': len(rows), 'originalNodes': rows, 'remainingNine': remaining,
    'capabilities': capabilities, 'executed': executed,
    'counts': dict(Counter(n['status'] for n in executed.values())),
    'wholeMigrationAccepted': False, 'realMigrationPerformed': False,
    'unverified': ['remote platforms/CI', 'real profiles/accounts/migration/deployment',
        'migration mapper opt-in and end-to-end migrated caller', 'QQ automatic backlog parity',
        'legacy partial-event wire alias compatibility', 'history skipped Event repair'],
    'migrationContract': {'bindingType': 'existing input binding; no new Binding variant',
        'suggestedInputPaths': ['delivery.sessionId', 'delivery.sessionIds', 'delivery.text'],
        'jobDeclaration': {'owner': 'delivery-manager', 'phase': 'before-reservation',
            'allowedStates': ['pending', 'reserved'], 'targetMode': 'runtime',
            'concurrency': 'job-config-cas+event-receipt', 'audit': 'final_operations', 'unknownPolicy': 'query-original'},
        'actionIds': ['product_assign_runtime_v1', 'product_session_message_runtime_v1', 'product_session_broadcast_runtime_v1'],
        'storage': 'existing Run plan runtimeDelivery children + original effect receipt; no second ledger',
        'api': 'existing Job definition deliveryRuntime; existing Run GET/list deliveryRuntime and notifications',
        'notificationOwner': 'notification-manager', 'notificationPhase': 'pending before-enqueue',
        'unknown': 'query original target/key only; never assume no send from absence/code'}}
(HERE / 'current43-matrix.json').write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
assert len(rows) == 43
assert [r['originalNode'] for r in rows] == [r['originalNode'] for r in prior['originalNodes']]
assert all(row['originalAssertions'] == old['originalAssertions'] for row, old in zip(rows, prior['originalNodes']))
assert all(row.get('preservedHistoricalRedEvidence') == old.get('preservedHistoricalRedEvidence')
           for row, old in zip(rows, prior['originalNodes']))
print(json.dumps(matrix['counts']))
review = ['# Current original-node review', '', 'Source: `' + source['sourceSha'] + '`.', '',
    'This is a bounded parity matrix, not 43 new green tests or whole migration acceptance. '
    'Historical original assertions, earlier successor lists and raw red provenance are retained in current43-matrix.json. '
    'Current execution is exact-node evidence only; unexecuted or incomplete successors stay historical/open.', '',
    '## M01–M15', '']
for c in capabilities:
    review += ['- **' + c['id'] + ' ' + c['capability'] + '**: ' + c['currentBoundary'],
               '  Current exact checks: ' + str(len(c['currentBatchEvidence'])) + '; full capability accepted: false.']
review += ['', '## Original 43 nodes', '']
for r in rows:
    review += ['### ' + str(r['ordinal']) + '. `' + r['originalNode'] + '`', '',
               'Disposition: **' + r['status'] + '**. ' + r.get('matchedRetainedSemantics', ''), '',
               'Successor IDs (historical unless an exact current execution follows):', '']
    review += ['- `' + n + '`' for n in r.get('strictSuccessors', [])] or ['- No exact successor currently registered.']
    review += ['', 'Current execution: ' + (', '.join('`' + e['node'] + '` = ' + e['status']
               for e in r['currentExecution']) or 'not reaccepted in this batch') + '.', '',
               'Remaining/unverified: ' + r.get('remaining', 'not specified') + '.', '']
(HERE / 'node-review.md').write_text('\n'.join(review).rstrip() + '\n', encoding='utf-8')
