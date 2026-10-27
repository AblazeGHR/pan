"""Exact native successors for retained editing semantics; no legacy writer."""
import asyncio
import copy

import pytest

from packages.jobs.final_service import FinalService
from tests.test_job_final_http_service import (  # noqa: F401
    h, USER, create, enabled, job_def, launch,
)


def test_rename_description_roundtrip_cas_and_original_operation(h):
    item = create(h)
    url = '/api/jobs/' + item['id']
    value = job_def(h, name='改名', description='完整描述')
    body = dict(definition=value, expectedRevision=1, requestKey='rename-description')
    response = h.client.put(url, json=body)
    assert response.status_code == 200, response.text
    receipt = response.json()
    result = receipt['result']['object']
    assert (result['name'], result['description'], result['configRevision']) == ('改名', '完整描述', 2)
    replay = h.client.put(url, json=body).json()
    assert replay.pop('replayed') is True
    assert replay == receipt
    assert h.client.put(url, json={**body, 'requestKey': 'stale'}).status_code == 409
    restored = FinalService(h.root, h.automation.auth)
    try:
        assert restored.get('job', item['id'], actor=USER) == result
        assert h.client.get('/api/operations/' + receipt['operationId']).json()['status'] == 'completed'
        assert h.client.get(url).json() == result
        assert not h.provider.calls
    finally:
        asyncio.run(restored.close())


@pytest.mark.parametrize('name', ['', ' ', '\t\n'])
def test_blank_name_rejection_preserves_definition_operation_and_disk(h, name):
    item = create(h)
    url = '/api/jobs/' + item['id']
    before = h.service.final.get(item['id'])
    response = h.client.put(url, json=dict(definition=job_def(h, name=name),
        expectedRevision=1, requestKey='invalid-name'))
    assert response.status_code == 400, response.text
    assert h.client.get(url).json() == item
    assert h.service.final.get(item['id']) == before
    restored = FinalService(h.root, h.automation.auth)
    try:
        assert restored.get('job', item['id'], actor=USER) == item
        assert h.client.get('/api/operations', params={
            'operation': 'update', 'requestKey': 'invalid-name'}).status_code == 404
        assert not h.provider.calls
    finally:
        asyncio.run(restored.close())


def test_target_and_text_edit_only_changes_future_frozen_runs(h):
    item = enabled(h)
    first = launch(h, item, 'before-edit')
    frozen = copy.deepcopy(h.service.final.get(item['id'])['runs'][first['runId']])
    calls = []
    send = h.provider.send_session

    async def record(session_id, text, **kwargs):
        calls.append((session_id, text, copy.deepcopy(kwargs['effect_key'])))
        return await send(session_id, text, **kwargs)

    h.provider.send_session = record
    value = job_def(h)
    value['steps'][0]['arguments'] = {
        'sessionId': {'from': 'literal', 'value': 'second-owned-session'},
        'text': {'from': 'literal', 'value': 'edited text'}}
    response = h.client.put('/api/jobs/' + item['id'], json=dict(
        definition=value, expectedRevision=1, requestKey='edit-target-text'))
    assert response.status_code == 200, response.text
    edited = response.json()['result']['object']
    assert h.service.final.get(item['id'])['runs'][first['runId']] == frozen
    asyncio.run(h.service.dispatch_once())
    second = launch(h, edited, 'after-edit')
    asyncio.run(h.service.dispatch_once())
    assert [(sid, text) for sid, text, _ in calls] == [
        ('owned-test-session', '0'), ('second-owned-session', 'edited text')]
    assert calls[0][2] != calls[1][2]
    for receipt in (first, second):
        assert h.client.get('/api/job-runs/' + receipt['runId']).json()['state'] == 'completed'
        assert h.client.get('/api/operations/' + receipt['operationId']).json()['status'] == 'completed'
    asyncio.run(h.service.dispatch_once())
    assert len(calls) == 2
