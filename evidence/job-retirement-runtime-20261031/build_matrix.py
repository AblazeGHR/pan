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
     delivery + 'test_mixed_accepted_and_proven_unsent_cold_retarget_same_live_attempt'],
    'Existing published Event backlog plus same live canonical Attempt: current target/text before acceptance; accepted/unknown children frozen, known-unsent child only resumes; original red retained.',
    'Migration mapper and simple production creation callers must opt into new immutable runtime releases. This batch does not activate/migrate real records; old flattened undeliveredFires representation is not restored.')
update(29, 'verified-runtime-opt-in-current',
    [delivery + 'test_real_http_concurrent_cas_during_first_acceptance_selects_only_winning_unsent_values'],
    'Actual HTTP CAS winner supplies text only to still-unsent target; accepted target retains original bytes, same frozen Run and same Attempt/effect.',
    'Only explicit new runtime releases plus Job declaration. Old releases intentionally remain frozen; production migration/caller opt-in not claimed.')
for index in (6, 26, 27):
    row = rows[index - 1]
    if row['currentExecution'] and all(e['status'] == 'passed' for e in row['currentExecution']):
        row['status'] = 'verified-bounded-current'

remaining = [copy.deepcopy(rows[r['ordinal'] - 1]) for r in prior['remainingNine']]
capabilities = copy.deepcopy(prior.get('capabilities', []))
for capability in capabilities:
    capability['previousBoundary'] = capability.get('currentBoundary')
    capability['currentBatchEvidence'] = []
    capability['completeCapabilityAccepted'] = False
for capability in capabilities:
    if capability['id'] in {'M01', 'M03', 'M04', 'M05', 'M06', 'M12', 'M13', 'M14', 'M15'}:
        capability['currentBatchEvidence'] = [n for n in executed if
            ('test_retirement_session_runtime' in n or 'test_retirement_notification_runtime' in n
             or 'test_retirement_trigger_backlog' in n or 'test_retirement_rev17_process_notice' in n)]
        capability['currentBoundary'] = ('Current runtime and notification lifecycle is bounded by explicit new releases/Job declaration. '
            'Same live Attempt and per-child proof, notification pending/unknown split, original terminal payload and Trigger temporary gates verified by exact owned checks. '
            'Historical complete capability acceptance, migration caller and deployment are not inferred.')
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
print(json.dumps(matrix['counts']))
