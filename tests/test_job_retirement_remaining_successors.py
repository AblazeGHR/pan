"""Exact successors for two remaining legacy scheduler semantics."""
import asyncio

import pytest

from packages.jobs import final_contracts as contracts
from packages.scheduler.creators import CreatorStore
from packages.scheduler.providers import TimeProvider, time_definition
from tests.test_job_final_creators import config, interval, stack, at
from tests.test_job_final_http_service import USER, control, create, enabled, h, job_def
from packages.actions.typed_executor import TypedExecutor
from packages.jobs.final_service import definition, object_ref


def test_scheduled_fire_without_matching_candidates_is_noop_and_not_backlogged(h):
    """A canonical trigger with no matching event type creates no Run or backlog."""
    job = enabled(h)
    trigger = create(h, 'triggers', {
        'name': 'only-workers', 'eventTypes': ['workers.all_idle'],
        'creatorSelector': {'mode': 'registeredType', 'providerType': 'core'},
        'targetJobId': job['id']}, 'no-candidate-trigger')
    active = control(h, 'triggers', trigger, 'enable', 'enable-no-candidate').json()['result']['object']
    result = h.service.admit_event({'id': 'scheduler-event', 'type': 'scheduler.tick',
        'source': {'type': 'scheduler'}, 'payload': {}}, active)
    assert result['status'] == 'skipped'
    assert h.service.final.runs(job['id']) == []
    assert not h.provider.calls


def test_invalid_schedule_spec_is_rejected_before_creator_persistence(tmp_path):
    provider = TimeProvider()
    invalid = config([interval(seconds='not-a-duration')])
    with pytest.raises(contracts.ContractError):
        provider.validate_config(invalid)
    creators = CreatorStore(tmp_path / 'creators')
    before = list((tmp_path / 'creators').rglob('*'))
    with pytest.raises(contracts.ContractError):
        creators.create(time_definition('invalid', invalid), request_key='invalid', state='enabled', at=at(0))
    assert list((tmp_path / 'creators').rglob('*')) == before


def _channel_job(h, provider):
    class Channel:
        async def send(self, target_type, target_id, text, **kwargs):
            provider.append((target_type, target_id, text, kwargs))
            return {'status': 'queued', 'messageId': 'qq-' + str(len(provider))}

    h.service.executor = TypedExecutor(channel_provider=Channel())
    product = next(item for item in h.service.actions(actor=USER)['items'] if item['id'] == 'product_process')
    value = definition('action', product)
    value.update(name='channel', inputSchema={'type': 'object', 'properties': {
        'targetType': {'enum': ['user', 'group']},
        'targetId': {'type': 'string', 'pattern': '^[0-9]+$'},
        'botUin': {'type': 'string', 'pattern': '^[0-9]+$'},
        'text': {'type': 'string', 'minLength': 1}},
        'required': ['targetType', 'targetId', 'text'], 'additionalProperties': False},
        execution={'operation': 'channel', 'config': {}}, effectClass='write_external',
        completionContracts=[{'id': 'provider-receipt', 'schema': {'type': 'object'}, 'terminalProofSchema': {'type': 'object'}}],
        defaultCompletion='provider-receipt', system=False)
    action = h.service.publish_action('test_channel', value)
    return object_ref('action', action)


def _channel_definition(action, *, target='123456', bot='987654', text='hello QQ'):
    return {'name': 'QQ job', 'steps': [{'stepId': 'send', 'actionRef': action,
        'arguments': {k: {'from': 'literal', 'value': v} for k, v in {
            'targetType': 'user', 'targetId': target, 'botUin': bot, 'text': text}.items()},
        'completion': {'contract': 'provider-receipt'}}], 'retention': {'policyRef': 'test'}}


def test_canonical_qq_manual_run_is_targetless_to_session_but_preserves_channel_target(h):
    calls = []
    action = _channel_job(h, calls)
    item = create(h, 'jobs', _channel_definition(action), 'qq-manual')
    item = control(h, 'jobs', item, 'enable', 'qq-manual-enable').json()['result']['object']
    run = h.client.post('/api/jobs/' + item['id'] + '/runs', json={
        'expectedRevision': item['configRevision'], 'requestKey': 'qq-manual-run', 'input': {}, 'cause': {'type': 'manual'}})
    assert run.status_code == 202
    asyncio.run(h.service.dispatch_once())
    record = h.client.get('/api/job-runs/' + run.json()['runId']).json()
    assert record['state'] == 'completed' and record.get('session_id') is None
    assert len(calls) == 1 and calls[0][:3] == ('user', '123456', 'hello QQ')
    assert calls[0][3]['bot_uin'] == '987654'
