"""Owned current-product parity, with real plugin rejection and process effects.

Channel configuration is test-local. No fake provider, QQ account, gateway or
supplier CLI participates. Rejection is plugin behavior, not a transport mock.
"""
import asyncio
import copy
import os
import socket
import threading
import time
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from packages.jobs.final_service import object_ref
from tests.job_retirement_native_fixture import h  # noqa: F401
from tests.test_job_final_http_service import USER, create, control, launch
from tests.test_job_automatic_recovery import env, policy, job_definition, drive_until, state  # noqa: F401
from tests.test_job_automatic_recovery import create as create_process


@contextmanager
def tcp(app):
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    sock.listen(32)
    server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 8
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(.01)
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(8)
        sock.close()
        assert not thread.is_alive()
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', port)) != 0


@pytest.fixture
def offline_plugin(h, tmp_path, monkeypatch):
    import nonebot
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init(driver='~fastapi')
    from packages.qq import channels, plugin
    from packages.qq.channels.base import ChannelConfig
    from packages.qq.channels.onebot import OneBotChannel
    from packages.web import server
    # Actual registered OneBot channels without a connected Bot. They never
    # contact an account; error identity proves selected versus default route.
    default = OneBotChannel(ChannelConfig(name='owned-default', bot_uin='111111'))
    selected = OneBotChannel(ChannelConfig(name='owned-selected', bot_uin='222222'))
    monkeypatch.setattr(plugin, '_channel', default)
    monkeypatch.setattr(channels, '_ACTIVE', default)
    monkeypatch.setattr(channels, '_ACTIVE_CHANNELS', {'owned-default': default, 'owned-selected': selected})
    monkeypatch.setattr(plugin, '_HISTORY_DIR', tmp_path / 'qq-history')
    monkeypatch.setattr(server, '_qq_plugin_client', None)
    app = FastAPI()
    plugin._register_qq_api(app)
    with tcp(app) as url:
        monkeypatch.setenv('PAN_QQ_API_URL', url)
        server._bind_qq_channel_provider(h.service)
        h.service._install_product_actions()
        try:
            yield h, url
        finally:
            # The existing sender owns per-loop transport; no live service is
            # started and no global production listener is used.
            server._qq_plugin_client = None


def qq_job(h, bot=None):
    actions = h.service.actions(actor=USER)
    product = next(row for row in actions['items'] if row['id'] == 'product_channel')
    args = {'targetType': 'user', 'targetId': '123456', 'text': 'hello QQ'}
    if bot is not None:
        args['botUin'] = bot
    return {'name': 'owned QQ', 'steps': [{'stepId': 'send', 'actionRef': object_ref('action', product),
        'arguments': {k: {'from': 'literal', 'value': v} for k, v in args.items()},
        'completion': {'contract': 'provider-receipt'}}], 'retention': {'policyRef': 'owned'}}


@pytest.mark.parametrize('bot,code', [(None, 'bot_not_connected'), ('222222', 'bot_not_connected'),
                                    ('333333', 'unknown_bot_uin')])
def test_real_plugin_default_selected_bot_failure_manual_receipt_and_no_replay(offline_plugin, bot, code):
    h, url = offline_plugin
    value = qq_job(h, bot)
    item = create(h, value=value)
    item = control(h, 'jobs', item, 'enable').json()['result']['object']
    events = copy.deepcopy(h.service.events.stats())
    original = launch(h, item)
    asyncio.run(h.service.dispatch_once())
    aggregate = h.service.final.get(item['id'])
    assert aggregate['runs'][original['runId']]['state'] == 'failed'
    assert len(aggregate['attempts']) == len(aggregate['effects']) == 1
    receipt = aggregate['effects'][0]['receipt']
    with httpx.Client(base_url=url, trust_env=False) as client:
        body = {'target_type': 'private', 'target_id': '123456', 'text': 'hello QQ'}
        if bot is not None:
            body['bot_uin'] = bot
        expected = client.post('/api/qq/send', json=body).json()
    assert expected['error']['code'] == code
    assert receipt['receipt'] == expected and receipt['outcome'] == 'rejected'
    assert h.service.events.stats() == events
    asyncio.run(h.service.dispatch_once())
    assert h.service.final.get(item['id']) == aggregate
    # Explicit failed QQ retry currently requires proof not supplied by this
    # original channel. Preserve fence; this is evidence of a retained gap.
    run = aggregate['runs'][original['runId']]
    denied = h.client.post('/api/job-runs/' + original['runId'] + '/operations', json={
        'operation': 'retry', 'requestKey': 'qq-retry', 'expectedStateRevision': run['stateRevision']})
    assert denied.status_code == 409, denied.text
    assert h.service.final.get(item['id']) == aggregate


def test_actual_failed_process_revocation_preserves_attempt_budget_without_second_effect(env):
    grants = env.auth._default_permissions()
    grants['job']['execute'] = grants['action']['manageSystemActions'] = True
    env.auth.change_permissions(grants, 0)
    count = env.tmp / 'revocation-count.txt'
    code = f"from pathlib import Path; Path({str(count)!r}).open('a').write('run\\n'); raise SystemExit(7)"
    jid, rid = create_process(env, job_definition(env, policy(backoffSeconds=['0.3']), code),
        actor={'type': 'agent', 'id': 'owned', 'revision': 1})
    asyncio.run(drive_until(env, jid, rid, 'retry_wait'))
    before = copy.deepcopy(env.service.final.store.get(jid)['finalRuntime'])
    marker = before['plans'][rid]['autoRecovery']
    assert len(before['attempts']) == 1 and count.read_text().splitlines() == ['run']
    env.auth.change_permissions(env.auth._default_permissions(), 1)
    time.sleep(.35)
    asyncio.run(env.service.dispatch_once())
    after = env.service.final.store.get(jid)['finalRuntime']
    assert len(after['attempts']) == 1 and count.read_text().splitlines() == ['run']
    assert after['plans'][rid]['autoRecovery'] == marker
    # Current canonical terminalization on revocation is explicit failed;
    # original decisive assertions are no extra effect/budget/deadline change.
    assert after['runs'][rid]['state'] == 'failed'
    assert after['effects'] == before['effects']


def test_real_product_qq_edit_preserves_frozen_target_bot_text_and_cas(offline_plugin):
    h, _ = offline_plugin
    value = qq_job(h, '333333')
    item = create(h, value=value)
    item = control(h, 'jobs', item, 'enable').json()['result']['object']
    first = launch(h, item, 'before-edit')
    frozen = copy.deepcopy(h.service.final.store.get(item['id'])['finalRuntime']['plans'])
    edited = copy.deepcopy(value)
    args = edited['steps'][0]['arguments']
    for key, v in {'targetType': 'group', 'targetId': '654321', 'botUin': '222222', 'text': 'edited QQ'}.items():
        args[key]['value'] = v
    url = '/api/jobs/' + item['id']
    body = {'definition': edited, 'expectedRevision': 1, 'requestKey': 'qq-edit'}
    response = h.client.put(url, json=body)
    assert response.status_code == 200, response.text
    current = response.json()['result']['object']
    assert current['steps'] == edited['steps'] and current['configRevision'] == 2
    assert h.service.final.store.get(item['id'])['finalRuntime']['plans'] == frozen
    assert h.client.put(url, json={**body, 'requestKey': 'qq-stale'}).status_code == 409
    assert h.client.get(url).json() == current
    asyncio.run(h.service.dispatch_once())
    second = launch(h, current, 'after-edit')
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(item['id'])
    assert state['runs'][first['runId']]['configRevision'] == 1
    assert state['runs'][second['runId']]['configRevision'] == 2
    assert [e['receipt']['receipt']['error']['code'] for e in state['effects']] == [
        'unknown_bot_uin', 'bot_not_connected']
    assert [e['receipt']['targetSelection'] for e in state['effects']] == [
        {'targetType': 'user', 'targetId': '123456'}, {'targetType': 'group', 'targetId': '654321'}]


@pytest.mark.parametrize('operation', ['create', 'update'])
@pytest.mark.parametrize('field,value', [('targetId', 'bad'), ('targetId', '123'), ('botUin', 'untrusted'),
    ('targetType', 'private'), ('text', ''), ('text', '  '), ('endpoint', 'http://untrusted'), ('sessionId', 'unknown')])
def test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin(offline_plugin, field, value, operation):
    h, _ = offline_plugin
    value_job = qq_job(h)
    item = create(h, value=value_job) if operation == 'update' else None
    value_job['steps'][0]['arguments'][field] = {'from': 'literal', 'value': value}
    with h.service.transaction(readonly=True) as db:
        before = {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table)]
                  for table in ('final_documents', 'final_operations')}
    response = h.client.request('PUT' if item else 'POST', '/api/jobs' + ('/' + item['id'] if item else ''), json={
        'definition': value_job, 'expectedRevision': 1 if item else 0, 'requestKey': 'qq-invalid'})
    assert response.status_code == 400, response.text
    with h.service.transaction(readonly=True) as db:
        assert {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table)]
                for table in before} == before


def test_scheduled_explicit_shell_argv_preserves_command_cwd_exit_and_no_early_creation(env):
    from datetime import datetime, timedelta, timezone
    from packages.scheduler.providers import time_definition
    from tests.test_job_final_creators import config as time_config
    from packages.jobs.independent_executor import ExecutionStore, effect_id
    assert os.name == 'nt'
    value = job_definition(env, policy(automatic=False, retryIf=[]))
    value['steps'][0]['arguments']['argv']['value'] = [os.environ['COMSPEC'], '/d', '/s', '/c',
        'echo retained-shell> retained-shell.txt & echo owned stdout & exit /b 7']
    fire = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat().replace('+00:00', 'Z')
    source = time_definition('owned shell clock', time_config([{'ruleId': 'once', 'type': 'once', 'at': fire}]))
    template = {'name': 'explicit shell time Template', 'jobDefinition': value, 'composition': {
        'creators': [{'mode': 'create', 'key': 'clock', 'definition': source}],
        'triggers': [{'key': 'tick', 'targetJobKey': 'job', 'creatorKeys': ['clock'], 'definition': {
            'name': 'shell tick', 'targetJobId': 'job', 'creatorSelector': {'mode': 'ids', 'ids': ['clock']},
            'eventTypes': ['scheduler.time.v2']}}]}}
    response = env.client.post('/api/job-templates', json={
        'definition': template, 'expectedRevision': 0, 'requestKey': 'shell-template'})
    assert response.status_code == 201, response.text
    item = response.json()['result']['object']
    response = env.client.post('/api/job-templates/' + item['id'] + '/operations', json={
        'operation': 'publish', 'expectedRevision': 1, 'expectedStateRevision': 0,
        'parameters': {}, 'requestKey': 'shell-publish'})
    assert response.status_code == 200, response.text
    item = response.json()['result']['object']
    response = env.client.post('/api/job-templates/' + item['id'] + '/instantiate', json={
        'expectedRevision': item['revision'], 'requestKey': 'shell-instantiate', 'activate': True})
    assert response.status_code == 201, response.text
    ids = response.json()['result']['objectIds']
    assert state(env, ids['job'])['runs'] == {} and not (env.tmp / 'retained-shell.txt').exists()
    env.service.refresh_subscriptions()
    asyncio.run(env.service.creator_runtime.tick(at=fire))
    asyncio.run(env.service.tick())
    initial = state(env, ids['job'])
    rid, = initial['runs']
    asyncio.run(drive_until(env, ids['job'], rid, 'failed'))
    result = state(env, ids['job'])
    assert (env.tmp / 'retained-shell.txt').read_text().strip() == 'retained-shell'
    assert len(result['runs']) == len(result['attempts']) == len(result['effects']) == 1
    assert result['runs'][rid]['cause']['type'] == 'event'
    doc = ExecutionStore(env.root / 'executions').get(effect_id(result['effects'][0]['effectKey']))
    assert doc['result']['receipt']['exitCode'] == 7
    assert doc['result']['cleanupProof']['ownedTree']['confirmed']
    assert doc['frozenPlan']['arguments']['cwd'] == str(env.tmp)
