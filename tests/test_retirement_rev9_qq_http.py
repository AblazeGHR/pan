"""Owned real plugin -> OneBotChannel -> NoneBot adapter -> HTTP evidence.

The HTTP endpoint is an owned OneBot fixture, not a real gateway/account.
No provider, sender, channel.send or Bot.call_api is replaced.
"""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request, Response

from tests.job_retirement_native_fixture import h  # noqa: F401
from tests.test_retirement_rev6_parity import tcp, qq_job
from tests.test_job_final_http_service import USER, create, control, launch
from packages.jobs.final_service import FinalService


@pytest.fixture
def qq_http(h, tmp_path, monkeypatch):
    import nonebot
    from nonebot.config import Env, Config
    from nonebot.drivers import combine_driver
    from nonebot.drivers.fastapi import Driver
    from nonebot.drivers.httpx import Mixin
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init(driver='~fastapi', _env_file=tmp_path / 'absent.env')
    from nonebot.adapters.onebot.v11 import Adapter, Bot
    from packages.qq import channels, plugin
    from packages.qq.channels.base import ChannelConfig
    from packages.qq.channels.onebot import OneBotChannel
    from packages.web import server

    rows = []
    fixture = FastAPI()
    mode = {'value': 'success'}

    @fixture.post('/{bot}/{operation}')
    async def onebot(bot: str, operation: str, request: Request):
        rows.append({'bot': bot, 'operation': operation, 'body': await request.json()})
        if mode['value'] == 'lost-response':
            # The external effect has been accepted/recorded above, but the
            # acknowledgement is lost. Exercise the actual adapter parse error.
            return Response(content=b'', status_code=200)
        if mode['value'] == 'missing-id':
            return {'status': 'ok', 'retcode': 0, 'data': {}}
        return {'status': 'ok', 'retcode': 0, 'data': {'message_id': 'owned-' + str(len(rows))}}

    with tcp(fixture) as root:
        # Real HTTP-capable driver independent of a suite's global NoneBot
        # driver. This does not change any production configuration.
        driver = combine_driver(Driver, Mixin)(Env(), Config(_env_file=tmp_path / 'absent.env'))
        adapter = Adapter(driver)
        adapter.onebot_config.onebot_api_roots = {
            '111111': root + '/111111/', '222222': root + '/222222/'}
        default = OneBotChannel(ChannelConfig(name='owned-default', bot_uin='111111'))
        selected = OneBotChannel(ChannelConfig(name='owned-selected', bot_uin='222222'))
        default._bot = Bot(adapter, '111111')
        selected._bot = Bot(adapter, '222222')
        monkeypatch.setattr(plugin, '_channel', default)
        monkeypatch.setattr(channels, '_ACTIVE', default)
        monkeypatch.setattr(channels, '_ACTIVE_CHANNELS', {
            'owned-default': default, 'owned-selected': selected})
        monkeypatch.setattr(plugin, '_HISTORY_DIR', tmp_path / 'qq-history')
        monkeypatch.setattr(server, '_qq_plugin_client', None)
        app = FastAPI()
        plugin._register_qq_api(app)
        with tcp(app) as url:
            monkeypatch.setenv('PAN_QQ_API_URL', url)
            server._bind_qq_channel_provider(h.service)
            h.service._install_product_actions()
            try:
                yield SimpleNamespace(h=h, rows=rows, mode=mode, default=default,
                                      selected=selected, url=url)
            finally:
                async def close():
                    if server._qq_plugin_client is not None:
                        await server._qq_plugin_client.aclose()
                asyncio.run(close())
                server._qq_plugin_client = None


def admitted(q, bot=None, text='  原文 [CQ:face,id=1]  ', target_type='user'):
    value = qq_job(q.h, bot)
    args = value['steps'][0]['arguments']
    args['text']['value'] = text
    args['targetType']['value'] = target_type
    item = create(q.h, value=value)
    item = control(q.h, 'jobs', item, 'enable').json()['result']['object']
    return value, item, launch(q.h, item)


@pytest.mark.parametrize('bot,target_type,expected_bot,operation', [
    (None, 'user', '111111', 'send_private_msg'),
    ('222222', 'group', '222222', 'send_group_msg')])
def test_original_plugin_real_onebot_http_success_exact_receipt_single_send(
        qq_http, bot, target_type, expected_bot, operation):
    q = qq_http
    _, item, run = admitted(q, bot, target_type=target_type)
    asyncio.run(q.h.service.dispatch_once())
    state = q.h.service.final.get(item['id'])
    assert state['runs'][run['runId']]['state'] == 'completed'
    assert len(state['attempts']) == len(state['effects']) == len(q.rows) == 1
    assert q.rows == [{'bot': expected_bot, 'operation': operation, 'body': {
        'user_id' if target_type == 'user' else 'group_id': 123456,
        'message': '  原文 [CQ:face,id=1]  '}}]
    receipt = state['effects'][0]['receipt']
    assert receipt['outcome'] == 'succeeded'
    assert receipt['receipt'] == {'ok': True, 'message_id': 'owned-1'}
    asyncio.run(q.h.service.dispatch_once())
    cold = FinalService(q.h.root, q.h.automation.auth)
    try:
        assert cold.final.get(item['id']) == state
        asyncio.run(cold.dispatch_once())
        assert len(q.rows) == 1
    finally:
        asyncio.run(cold.close())


@pytest.mark.parametrize('bot', [None, '222222'])
def test_local_disconnected_no_send_manual_retry_retained_gap(qq_http, bot):
    """Strict target for the missing trusted producer proof; keep red raw.

    Fixture connectivity is observed directly, never used as runtime proof.
    The product must eventually carry trusted original no-send evidence.
    """
    q = qq_http
    channel = q.default if bot is None else q.selected
    original_bot = channel._bot
    channel._bot = None
    _, item, run = admitted(q, bot)
    asyncio.run(q.h.service.dispatch_once())
    before = q.h.service.final.get(item['id'])
    assert not q.rows
    assert len(before['attempts']) == len(before['effects']) == 1
    assert before['effects'][0]['receipt']['receipt']['error']['code'] == 'bot_not_connected'
    channel._bot = original_bot
    response = q.h.client.post('/api/job-runs/' + run['runId'] + '/operations', json={
        'operation': 'retry', 'requestKey': 'known-no-send-retry',
        'expectedStateRevision': before['runs'][run['runId']]['stateRevision']})
    assert response.status_code == 202, response.text
    asyncio.run(q.h.service.dispatch_once())
    after = q.h.service.final.get(item['id'])
    assert after['runs'][run['runId']]['state'] == 'completed'
    assert len(after['attempts']) == len(after['effects']) == 2
    assert len(q.rows) == 1
    assert after['effects'][0]['effectKey'] == after['effects'][1]['effectKey']
    assert after['effects'][0]['attemptId'] != after['effects'][1]['attemptId']


def test_edit_after_admission_keeps_actual_original_bot_target_text(qq_http):
    q = qq_http
    value, item, run = admitted(q, '222222')
    frozen = copy.deepcopy(q.h.service.final.store.get(item['id'])['finalRuntime']['plans'])
    args = value['steps'][0]['arguments']
    for field, literal in {'botUin': '111111', 'targetId': '654321',
                           'targetType': 'group', 'text': 'new text'}.items():
        args[field]['value'] = literal
    response = q.h.client.put('/api/jobs/' + item['id'], json={
        'definition': value, 'expectedRevision': item['configRevision'], 'requestKey': 'edit'})
    assert response.status_code == 200, response.text
    assert q.h.service.final.store.get(item['id'])['finalRuntime']['plans'] == frozen
    asyncio.run(q.h.service.dispatch_once())
    assert q.rows == [{'bot': '222222', 'operation': 'send_private_msg',
                      'body': {'user_id': 123456, 'message': '  原文 [CQ:face,id=1]  '}}]
    assert q.h.service.get_run(run['runId'], actor=USER)['state'] == 'completed'


@pytest.mark.parametrize('mode', ['lost-response', 'missing-id'])
def test_accepted_without_ack_is_unknown_cold_retry_no_new_attempt_or_send(qq_http, mode):
    q = qq_http
    q.mode['value'] = mode
    _, item, run = admitted(q)
    asyncio.run(q.h.service.dispatch_once())
    state = q.h.service.final.get(item['id'])
    assert len(q.rows) == len(state['attempts']) == len(state['effects']) == 1
    assert state['effects'][0]['receipt']['outcome'] == 'unknown'
    cold = FinalService(q.h.root, q.h.automation.auth)
    try:
        original = cold.get_run(run['runId'], actor=USER)
        from packages.jobs.final_contracts import ContractError
        with pytest.raises(ContractError) as denied:
            cold.control_run(run['runId'], {'operation': 'retry', 'requestKey': 'retry',
                'expectedStateRevision': original['stateRevision']}, actor=USER)
        assert denied.value.code == 'reconciliation_required'
        asyncio.run(cold.dispatch_once())
        assert cold.final.get(item['id']) == state
        assert len(q.rows) == 1
    finally:
        asyncio.run(cold.close())
