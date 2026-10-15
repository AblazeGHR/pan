"""Zero-model verification of retained private production evidence.

Usage: python research/cbc_steer/verify_production_result.py <result.json>
Does not expose model reasoning or private raw logs in the sanitized summary.
"""
import json
from pathlib import Path
import sys

source = Path(sys.argv[1])
facts = json.loads(source.read_text(encoding='utf-8'))
base = Path(facts['base'])
run = facts['originalRunId']
assert facts['identityBefore'] == facts['identityAfter']
assert facts['cleanupError'] is None and facts['processExitCode'] is not None
assert len(facts['wire']) == len(facts['http']) == 4
for response in facts['http']:
    receipt = response['response'].get('steer', response['response'])
    assert receipt.get('outcome', receipt.get('status')) == 'accepted'
    assert receipt['consumption'] == 'unconfirmed'
for frame in facts['wire']:
    assert frame['request']['expected_request_id'] == run
    assert frame['request']['session_id'] == facts['nativeSessionId']
native = next((base / 'profile/projects').rglob(facts['nativeSessionId'] + '.jsonl'))
rows = [json.loads(line) for line in native.read_text(encoding='utf-8').splitlines()]
injected = [row for row in rows if row.get('role') == 'user'
            and row.get('providerData', {}).get('startsNewUserRequest') is False]
assert len(injected) == 4
assert all(row['providerData']['conversationRequestId'] == run for row in injected)
assert sum(row.get('providerData', {}).get('startsNewUserRequest') is True for row in rows) == 1
final = rows[-1]
assert final['role'] == 'assistant' and final['providerData']['conversationRequestId'] == run
terminals = [event for event in facts['events'] if event.get('type') == 'worker.result']
assert len(terminals) == 1 and terminals[0]['taskSeq'] == 1 and terminals[0]['executionSeq'] == 1
final_text = ''.join(part.get('text', '') for part in final['content'])
assert final_text == terminals[0]['result']
session = json.loads((base / 'sessions' / (facts['sessionId'] + '.json')).read_text(encoding='utf-8'))
assert not session['queue_pending']
for key in ('queuedItemId', 'selfItemId', 'forceItemId'):
    origin = session['queue_delivery_ledger'][facts[key]]['steer']
    assert origin['runId'] == run and origin['taskSeq'] == 1
direct = session['adapter_config']['_steer_requests']
assert len(direct) == 1 and next(iter(direct.values()))['status'] == 'accepted'
summary = {
    'sourceJob': 'job_7d3ed597f11e2ee1b40502ba', 'sourceResult': str(source),
    'cliVersion': facts['cliVersion'], 'headlessSHA256': facts['headlessSHA256'],
    'model': facts['model'], 'identityBefore': facts['identityBefore'],
    'identityAfter': facts['identityAfter'], 'sessionId': facts['sessionId'],
    'nativeSessionId': facts['nativeSessionId'], 'runId': run,
    'pathsAccepted': [row['kind'] for row in facts['http']],
    'nativeSameRunInjectedMessages': [row['id'] for row in injected],
    'nativeNewUserRequests': 1, 'finalResult': final_text,
    'panTerminalCount': 1, 'queueEmpty': True, 'originalItemLedgerBinding': True,
    'nativeHistoryAndFinalSameRun': True,
    'modelFollowedMarkerInstruction': all(marker in final_text for marker in
        ('DIRECT_CBC_73', 'QUEUE_CBC_73', 'SELF_CBC_73', 'FORCE_CBC_73')),
    'cleanupError': None, 'processExitCode': facts['processExitCode'],
    'sourceProbeExitWasFailure': True,
    'failureReason': 'Marker compliance assertion failed; native same-run consumption independently verified',
    'verification': 'passed', 'verificationModelsCalled': 0,
}
target = Path(__file__).with_name('production-evidence.json')
target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=True, indent=2))
