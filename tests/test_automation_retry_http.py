"""Canonical retry HTTP; no legacy Trigger confirmation or retry host."""
import asyncio
from tests.test_automation_canonical import canonical, subscription, fact, admit, run, operation


def test_http_uncertain_retry_is_rejected_by_original_run_identity(canonical):
    h = canonical
    _, target = subscription(h)
    result = admit(h, fact(h), target)
    h.provider.unknown = True
    asyncio.run(h.service.dispatch_once())
    assert run(h, result['runId'])['state'] == 'uncertain'
    response = operation(h, result['runId'], 'retry')
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'reconciliation_required'
    assert h.client.get('/api/operations/' + result['operationId']).json()['status'] == 'unknown'
    assert len(h.provider.calls) == 1


def test_http_retry_replays_operation_without_a_second_admission(canonical):
    h = canonical
    _, target = subscription(h)
    result = admit(h, fact(h), target)
    h.provider.fail_once = '0'
    asyncio.run(h.service.dispatch_once())
    response = operation(h, result['runId'], 'retry', stepId='s0')
    assert response.status_code == 202, response.text
    original = response.json()
    asyncio.run(h.service.dispatch_once())
    assert run(h, result['runId'])['state'] == 'completed'
    receipt = h.client.get('/api/operations', params={'operation':'retry', 'requestKey':'retry'})
    assert receipt.status_code == 200 and receipt.json()['operationId'] == original['operationId']
    assert len(h.provider.calls) == 2
    assert h.provider.calls[0][1] == h.provider.calls[1][1]
