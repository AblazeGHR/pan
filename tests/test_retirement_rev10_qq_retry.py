"""Retained manual QQ retry on the original owned HTTP/channel fixture.

Success proofs use real plugin, OneBotChannel, NoneBot Bot and HTTP client.
Corruption/spoof probes below are explicitly adversarial, not delivery proofs.
"""
import asyncio
import copy

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from packages.actions.qq_channel_provider import QQChannelProvider
from packages.jobs.final_contracts import ContractError
from packages.jobs.final_service import FinalService, object_ref
from tests.job_retirement_native_fixture import h  # noqa: F401
from tests.test_job_final_http_service import USER, create, control, launch
from tests.test_retirement_rev6_parity import tcp, qq_job
from tests.test_retirement_rev9_qq_http import qq_http, admitted  # noqa: F401


def disconnected(q):
    bot = q.default._bot
    q.default._bot = None
    _, item, run = admitted(q)
    asyncio.run(q.h.service.dispatch_once())
    q.default._bot = bot
    state = q.h.service.final.get(item['id'])
    assert state['runs'][run['runId']]['state'] == 'failed'
    assert len(state['attempts']) == 1 and not q.rows
    assert 'qqNoSend' in state['effects'][0]['receipt']
    assert 'cleanupProof' not in state['effects'][0]['receipt']
    return item, run, state


def retry_body(state, rid, key='retry'):
    return {'operation': 'retry', 'requestKey': key,
            'expectedStateRevision': state['runs'][rid]['stateRevision']}


@pytest.mark.parametrize('bot', [None, '222222', '333333'])
def test_original_http_cold_retry_duplicate_control_cas_same_effect(qq_http, bot, monkeypatch):
    q = qq_http
    channel = q.default if bot is None else q.selected
    connected = channel._bot
    if bot != '333333':
        channel._bot = None
    _, item, run = admitted(q, bot)
    asyncio.run(q.h.service.dispatch_once())
    before = q.h.service.final.get(item['id'])
    rid = run['runId']
    assert before['runs'][rid]['state'] == 'failed' and not q.rows
    proof = before['effects'][0]['receipt']['qqNoSend']
    assert proof['phase']['reason'] == ('unknown-selected-bot' if bot == '333333' else 'resolve-not-connected')
    # Repeated pulses do not retry a channel, even after connectivity returns.
    if bot == '333333':
        from nonebot.adapters.onebot.v11 import Bot
        from packages.qq import channels
        from packages.qq.channels.base import ChannelConfig
        from packages.qq.channels.onebot import OneBotChannel
        channel = OneBotChannel(ChannelConfig(name='owned-third', bot_uin=bot))
        adapter = connected.adapter
        adapter.onebot_config.onebot_api_roots[bot] = str(adapter.onebot_config.onebot_api_roots['222222']).replace('/222222/', '/333333/')
        channel._bot = Bot(adapter, bot)
        monkeypatch.setitem(channels._ACTIVE_CHANNELS, 'owned-third', channel)
    else:
        channel._bot = connected
    for _ in range(2):
        asyncio.run(q.h.service.dispatch_once())
    assert q.h.service.final.get(item['id']) == before and not q.rows
    cold = FinalService(q.h.root, q.h.automation.auth)
    from packages.web import server
    server._bind_qq_channel_provider(cold)
    try:
        assert cold.final.get(item['id']) == before
        body = retry_body(before, rid)
        response = q.h.client.post('/api/job-runs/' + rid + '/operations', json=body)
        assert response.status_code == 202, response.text
        operation_id = response.json()['operationId']
        duplicate = q.h.client.post('/api/job-runs/' + rid + '/operations', json=body)
        assert duplicate.json()['operationId'] == operation_id
        assert len(cold.final.get(item['id'])['attempts']) == 1 and not q.rows
        stale = q.h.client.post('/api/job-runs/' + rid + '/operations', json={**body, 'requestKey': 'stale'})
        assert stale.status_code == 409
        queued = cold.final.get(item['id'])
        again = q.h.client.post('/api/job-runs/' + rid + '/operations', json=retry_body(queued, rid, 'queued-again'))
        assert again.status_code == 409
        asyncio.run(cold.dispatch_once())
        after = cold.final.get(item['id'])
        assert after['runs'][rid]['state'] == 'completed'
        assert len(after['runs']) == 1 and len(after['attempts']) == len(after['effects']) == 2
        assert len(q.rows) == 1
        assert q.rows[0]['bot'] == (bot or '111111')
        assert q.rows[0]['body'] == {'user_id': 123456, 'message': '  原文 [CQ:face,id=1]  '}
        assert after['effects'][0]['effectKey'] == after['effects'][1]['effectKey']
        assert after['effects'][0]['attemptId'] != after['effects'][1]['attemptId']
        assert after['effects'][1]['receipt']['receipt'] == {'ok': True, 'message_id': 'owned-1'}
        replay = q.h.client.post('/api/job-runs/' + rid + '/operations', json=body)
        assert replay.json()['operationId'] == operation_id
        completed_retry = q.h.client.post('/api/job-runs/' + rid + '/operations', json=retry_body(after, rid, 'success-again'))
        assert completed_retry.status_code == 409
    finally:
        asyncio.run(cold.close())
    reopened = FinalService(q.h.root, q.h.automation.auth)
    server._bind_qq_channel_provider(reopened)
    try:
        assert reopened.final.get(item['id']) == after
        asyncio.run(reopened.dispatch_once())
        assert len(q.rows) == 1
    finally:
        asyncio.run(reopened.close())


def test_original_receipt_binding_corruption_and_unknown_partial_never_retry(qq_http):
    q = qq_http
    item, run, before = disconnected(q)
    rid = run['runId']
    original = q.h.service.final.store.get(item['id'])
    mutations = [
        ('missing-proof', lambda e: e['receipt'].pop('qqNoSend')),
        ('wrong-effect', lambda e: e['receipt']['qqNoSend']['effectKey'].update(targetId='other')),
        ('wrong-ordinal', lambda e: e['receipt']['qqNoSend']['effectKey'].update(operationOrdinal=1)),
        ('wrong-run', lambda e: e['receipt']['qqNoSend']['attempt'].update(runId='run_other')),
        ('wrong-step', lambda e: e['receipt']['qqNoSend']['attempt'].update(stepId='other')),
        ('wrong-attempt', lambda e: e['receipt']['qqNoSend']['attempt'].update(attemptId='attempt_other')),
        ('stale-generation', lambda e: e['receipt']['qqNoSend']['attempt'].update(generation=99)),
        ('wrong-owner', lambda e: e['receipt']['qqNoSend']['attempt'].update(ownerEpoch=99)),
        ('wrong-lease', lambda e: e['receipt']['qqNoSend']['attempt'].update(leaseId='lease_other')),
        ('wrong-text', lambda e: e['receipt']['qqNoSend']['arguments'].update(text='changed')),
        ('wrong-bot', lambda e: e['receipt']['qqNoSend']['arguments'].update(botUin='222222')),
        ('wrong-target', lambda e: e['receipt']['qqNoSend']['arguments'].update(targetId='654321')),
        ('wrong-phase', lambda e: e['receipt']['qqNoSend']['phase'].update(stage='after-call-api')),
        ('wrong-producer', lambda e: e['receipt']['qqNoSend']['phase'].update(producer='custom')),
        ('missing-phase', lambda e: e['receipt']['receipt'].pop('qqSendPhase')),
        ('unknown', lambda e: e.update(outcome='unknown')),
        ('partial', lambda e: e.update(outcome='partial')),
    ]
    for label, mutate in mutations:
        corrupt = copy.deepcopy(original)
        mutate(corrupt['finalRuntime']['effects'][0])
        q.h.service.final.store.mutate(item['id'], lambda job: job.update(corrupt))
        with pytest.raises(ContractError) as rejected:
            q.h.service.control_run(rid, retry_body(before, rid, label), actor=USER)
        assert rejected.value.code == ('reconciliation_required' if label in {'unknown', 'partial'} else 'not_retryable'), label
        assert q.h.service.final.store.get(item['id']) == corrupt, label
        assert not q.rows, label
    q.h.service.final.store.mutate(item['id'], lambda job: job.update(original))
    # Automatic callers never receive the narrow manual exception.
    with pytest.raises(ContractError) as denied:
        q.h.service._retry_boundary(original, rid)
    assert denied.value.code == 'not_retryable'
    assert q.h.service.final.get(item['id']) == before


def test_current_authorization_revoked_before_manual_retry_no_write_or_send(qq_http):
    q = qq_http
    item, run, before = disconnected(q)
    grants = q.h.automation.auth._default_permissions()
    grants['job']['execute'] = False
    q.h.automation.auth.change_permissions(grants, 0)
    with pytest.raises(ContractError) as denied:
        q.h.service.control_run(run['runId'], retry_body(before, run['runId']),
                                actor={'type': 'agent', 'id': 'owned', 'revision': 0})
    assert denied.value.code == 'authorization_denied'
    asyncio.run(q.h.service.dispatch_once())
    assert q.h.service.final.get(item['id']) == before and not q.rows


def test_custom_product_like_action_cannot_use_trusted_retry_exception(qq_http):
    q = qq_http
    from packages.jobs.final_service import definition
    product = next(row for row in q.h.service.actions(actor=USER)['items'] if row['id'] == 'product_channel')
    custom = q.h.service.publish_action('owned_custom_channel', {**definition('action', product), 'system': False})
    value = qq_job(q.h)
    value['steps'][0]['actionRef'] = object_ref('action', custom)
    q.default._bot = None
    item = create(q.h, value=value)
    item = control(q.h, 'jobs', item, 'enable').json()['result']['object']
    run = launch(q.h, item)
    asyncio.run(q.h.service.dispatch_once())
    state = q.h.service.final.get(item['id'])
    assert 'qqNoSend' in state['effects'][0]['receipt']  # Same real no-send producer, different Action authority.
    response = q.h.client.post('/api/job-runs/' + run['runId'] + '/operations', json=retry_body(state, run['runId']))
    assert response.status_code == 409
    assert q.h.service.final.get(item['id']) == state and not q.rows


def test_call_api_entered_channel_not_connected_is_ambiguous_no_proof(qq_http):
    q = qq_http
    from packages.qq.channels.base import ChannelNotConnected
    from nonebot.adapters.onebot.v11 import Bot
    class LostAfterAcceptedBot(Bot):
        async def call_api(self, api, **data):
            # Fault injection after the real NoneBot HTTP call has succeeded.
            await super().call_api(api, **data)
            raise ChannelNotConnected('same text after entering call_api')
    q.default._bot = LostAfterAcceptedBot(q.default._bot.adapter, '111111')
    _, item, run = admitted(q)
    asyncio.run(q.h.service.dispatch_once())
    state = q.h.service.final.get(item['id'])
    receipt = state['effects'][0]['receipt']
    assert len(q.rows) == 1
    assert receipt['receipt']['error']['code'] == 'bot_not_connected'
    assert receipt['outcome'] == 'unknown' and 'qqNoSend' not in receipt
    response = q.h.client.post('/api/job-runs/' + run['runId'] + '/operations', json=retry_body(state, run['runId']))
    assert response.status_code == 409 and response.json()['error']['code'] == 'reconciliation_required'
    assert q.h.service.final.get(item['id']) == state and len(q.rows) == 1


def test_http_spoof_missing_wrong_phase_non2xx_and_sender_provenance(qq_http, monkeypatch):
    q = qq_http
    from packages.web import server
    wire = {'target_type': 'private', 'target_id': '123456', 'text': 'owned'}
    phase = {'producer': 'onebot.send', 'stage': 'before-call-api',
             'reason': 'resolve-not-connected', 'arguments': wire}
    raw = {'ok': False, 'error': {'code': 'bot_not_connected', 'message': 'copied'}}
    scenarios = [raw, {**raw, 'qqSendPhase': {**phase, 'stage': 'after-call-api'}},
        {**raw, 'qqSendPhase': {**phase, 'arguments': {**wire, 'target_id': '654321'}}},
        {**raw, 'qqSendPhase': {**phase, 'attemptId': 'stale'}},
        {**raw, 'qqSendPhase': {**phase, 'reason': ['malformed']}},
        {**raw, 'qqSendPhase': phase}]
    app = FastAPI()
    current = {'payload': raw, 'status': 200}

    @app.post('/api/qq/send')
    async def forged():
        return JSONResponse(current['payload'], status_code=current['status'])

    with tcp(app) as url:
        monkeypatch.setenv('PAN_QQ_API_URL', url)
        provider = QQChannelProvider(server._send_scheduled_qq)
        for index, payload in enumerate(scenarios):
            current.update(payload=payload, status=503 if index == len(scenarios) - 1 else 200)
            result = asyncio.run(provider.send('user', '123456', 'owned', effect_key={
                'runId': 'owned', 'stepId': 'send', 'targetId': 'product_channel', 'operationOrdinal': 0}))
            assert result == payload and result.no_send is None, index
    # An arbitrary sender cannot mint host provenance even with matching data.
    async def arbitrary(**kwargs):
        return {**raw, 'qqSendPhase': phase}
    result = asyncio.run(QQChannelProvider(arbitrary).send('user', '123456', 'owned', effect_key={}))
    assert result.no_send is None
