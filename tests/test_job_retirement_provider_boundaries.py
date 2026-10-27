"""Real canonical writes and built-in provider rejection; no provider doubles.

Red cases retain the legacy write-time safety requirement. Green runtime
rejection is supporting evidence, not permission to close the write-time gap.
"""
import asyncio
import copy

import pytest

from packages.actions.typed_executor import TypedExecutor, WorkerSessionProvider
from packages.core import session, worker
from packages.jobs.final_service import definition, object_ref
from tests.job_retirement_native_fixture import h  # noqa: F401
from tests.test_job_final_http_service import USER, create, control, job_def, launch


def channel_definition(h):
    # Trusted explicit publication using the actual canonical typed channel
    # schema. Do not install a pretend QQ provider or change product binding.
    value = definition('action', next(row for row in h.service.actions(actor=USER)['items']
        if row['id'] == 'product_session_message'))
    value.update(name='owned typed channel', system=False,
        inputSchema={'type': 'object', 'properties': {
            'targetType': {'enum': ['user', 'group']},
            'targetId': {'type': 'string', 'pattern': '^[0-9]+$'},
            'botUin': {'type': 'string', 'pattern': '^[0-9]+$'},
            'text': {'type': 'string', 'minLength': 1}},
            'required': ['targetType', 'targetId', 'text'], 'additionalProperties': False},
        execution={'operation': 'channel', 'config': {}}, effectClass='write_external',
        completionContracts=[{'id': 'provider-receipt', 'schema': {'type': 'object'},
                              'terminalProofSchema': {'type': 'object'}}],
        defaultCompletion='provider-receipt')
    action = h.service.publish_action('owned_channel_boundary', value)
    return {'name': 'owned QQ job', 'steps': [{'stepId': 'send',
        'actionRef': object_ref('action', action),
        'arguments': {key: {'from': 'literal', 'value': value} for key, value in {
            'targetType': 'user', 'targetId': '123456', 'botUin': '987654', 'text': 'hello QQ'}.items()},
        'completion': {'contract': 'provider-receipt'}}], 'retention': {'policyRef': 'test'}}


@pytest.mark.parametrize('operation', ['create', 'update'])
def test_qq_untrusted_literal_must_be_rejected_before_committing(h, operation):
    h.service.executor = TypedExecutor()
    value = channel_definition(h)
    if operation == 'update':
        item = create(h, value=value)
        before = h.client.get('/api/jobs/' + item['id']).json()
    value['steps'][0]['arguments']['targetId']['value'] = 'not-a-qq-id'
    url = '/api/jobs' + ('/' + item['id'] if operation == 'update' else '')
    response = h.client.request('PUT' if operation == 'update' else 'POST', url,
        json={'definition': value, 'expectedRevision': 1 if operation == 'update' else 0,
              'requestKey': 'invalid-qq-' + operation})
    assert response.status_code == 400, response.text
    if operation == 'update':
        assert h.client.get(url).json() == before
    else:
        assert h.client.get('/api/jobs').json()['items'] == []


def test_missing_session_must_be_rejected_before_job_definition_commit(h):
    h.service.executor = TypedExecutor(session_provider=WorkerSessionProvider())
    value = job_def(h)
    value['steps'][0]['arguments']['sessionId']['value'] = 'ses_owned_missing'
    assert session.get('ses_owned_missing') is None
    response = h.client.post('/api/jobs', json={
        'definition': value, 'expectedRevision': 0, 'requestKey': 'missing-session'})
    assert response.status_code == 400, response.text
    assert h.client.get('/api/jobs').json()['items'] == []


def test_actual_worker_provider_rejects_missing_session_without_creating_or_spawning(h):
    h.service.executor = TypedExecutor(session_provider=WorkerSessionProvider())
    value = job_def(h)
    value['steps'][0]['arguments']['sessionId']['value'] = 'ses_owned_missing'
    item = create(h, value=value)
    item = control(h, 'jobs', item, 'enable').json()['result']['object']
    sessions_before = {p: p.read_bytes() for p in session.SESSION_DIR.rglob('*') if p.is_file()}
    workers_before = set(worker.workers)
    receipt = launch(h, item)
    asyncio.run(h.service.dispatch_once())
    assert h.client.get('/api/job-runs/' + receipt['runId']).json()['state'] == 'failed'
    assert session.get('ses_owned_missing') is None
    assert set(worker.workers) == workers_before
    assert {p: p.read_bytes() for p in session.SESSION_DIR.rglob('*') if p.is_file()} == sessions_before


def test_channel_without_bound_plugin_records_failure_and_never_invents_time_event(h):
    h.service.executor = TypedExecutor()
    value = channel_definition(h)
    item = create(h, value=value)
    item = control(h, 'jobs', item, 'enable').json()['result']['object']
    before = copy.deepcopy(h.service.events.stats())
    receipt = launch(h, item, 'manual-channel-unavailable')
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(item['id'])
    assert state['runs'][receipt['runId']]['state'] == 'failed'
    assert 'provider_unavailable' in str(state['effects'])
    assert len(state['attempts']) == 1
    assert h.service.events.stats() == before
    original = copy.deepcopy(state)
    asyncio.run(h.service.dispatch_once())
    assert h.service.final.get(item['id']) == original
