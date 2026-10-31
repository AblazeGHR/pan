"""Canonical QQ pending fires over the real owned plugin/OneBot HTTP path."""
import asyncio
import copy
import json
import httpx
from pathlib import Path
import subprocess
import sys

import pytest

from packages.jobs.final_service import FinalService, object_ref, definition
from tests.job_retirement_native_fixture import h
from tests.test_retirement_rev9_qq_http import qq_http
from tests.test_retirement_rev6_parity import qq_job
from tests.test_job_final_http_service import USER, create, control, launch


@pytest.fixture
def run():
    # The real sender owns a pooled AsyncClient. Production dispatch has one
    # persistent loop; closing asyncio.run's loop between HTTP calls creates
    # an artificial lost-connection result unrelated to bot selection.
    with asyncio.Runner() as runner:
        from packages.web import server
        original = server._qq_plugin_client
        # Owned plugin starts the original NoneBot HTTP client lazily. On
        # this Windows runner building its TLS context alone takes seconds,
        # even for loopback HTTP. Use an explicit fixture-only HTTP timeout;
        # production sender/transport and its failure classification stay as-is.
        client = httpx.AsyncClient(timeout=30, trust_env=False)
        server._qq_plugin_client = client
        try:
            yield runner.run
        finally:
            runner.run(client.aclose())
            server._qq_plugin_client = original


def admit(q, bot=None, *, launch_now=True, actor=None):
    value = qq_job(q.h, bot)
    action = next(a for a in q.h.service.actions(actor=USER)['items']
                  if a['id'] == 'product_channel_runtime_v1')
    value['steps'][0]['actionRef'] = object_ref('action', action)
    args = {'targetType': 'user', 'targetId': '123456', 'text': '  original\nQQ  '}
    if bot:
        args['botUin'] = bot
    value['inputSchema'] = copy.deepcopy(action['inputSchema'])
    value['arguments'] = args
    value['overlap'] = {'mode': 'parallel', 'maxActive': 4, 'maxPending': 4}
    value['steps'][0]['arguments'] = {k: {'from': 'input', 'path': k} for k in args}
    value['deliveryRuntime'] = {'owner': 'delivery-manager', 'targetMode': 'runtime',
        'inputFields': list(args), 'allowedStates': ['pending', 'reserved'],
        'concurrency': 'job-config-cas+event-receipt', 'phase': 'before-reservation',
        'audit': 'final_operations', 'unknownPolicy': 'query-original'}
    item = create(q.h, value=value)
    item = control(q.h, 'jobs', item, 'enable').json()['result']['object']
    if not launch_now:
        return item, args
    if actor:
        run = q.h.service.create_run(item['id'], {'expectedRevision': item['configRevision'],
            'requestKey': 'owned-agent-fire', 'input': args, 'cause': {'type': 'manual'}}, actor=actor)
        return item, run['runId']
    response = q.h.client.post('/api/jobs/' + item['id'] + '/runs', json={
        'expectedRevision': item['configRevision'], 'requestKey': 'owned-runtime-fire',
        'input': args, 'cause': {'type': 'manual'}})
    assert response.status_code == 202, response.text
    run = response.json()
    return item, run['runId']


def test_two_real_scheduler_fires_cold_drain_current_bot_each_original_event_once(qq_http, run):
    from tests.test_retirement_rev16_behavior import instant
    from tests.test_job_final_creators import config as time_config
    from packages.scheduler.providers import time_definition
    from packages.web import server
    q = qq_http
    connected = q.default._bot
    q.default._bot = None
    item, _ = admit(q, launch_now=False)
    at1, at2 = instant(30), instant(60)
    source = q.h.client.post('/api/event-creators', json={'definition': time_definition('owned QQ fires',
        time_config([{'ruleId': 'one', 'type': 'once', 'at': at1}, {'ruleId': 'two', 'type': 'once', 'at': at2}])),
        'expectedRevision': 0, 'requestKey': 'qq-time'}).json()['result']['object']
    source = control(q.h, 'event-creators', source, 'enable', 'time-enable').json()['result']['object']
    trigger = q.h.client.post('/api/triggers', json={'definition': {'name': 'QQ fires',
        'targetJobId': item['id'], 'creatorSelector': {'mode': 'ids', 'ids': [source['id']]},
        'eventTypes': ['scheduler.time.v2']}, 'expectedRevision': 0, 'requestKey': 'qq-trigger'}).json()['result']['object']
    control(q.h, 'triggers', trigger, 'enable', 'qq-trigger-enable')
    q.h.service.refresh_subscriptions()
    for at in (at1, at2):
        run(q.h.service.creator_runtime.tick(at=at))
        run(q.h.service.tick())
    before = q.h.service.final.get(item['id'])
    assert len(before['runs']) == len(before['attempts']) == 2 and not before['effects'] and not q.rows
    original = {r: copy.deepcopy(q.h.service.final.store.get(item['id'])['finalRuntime']['plans'][r]) for r in before['runs']}
    assert {p['runtimeDelivery']['send']['children'][0]['backlog']['fireAt'] for p in original.values()} == {at1, at2}
    assert len({p['runtimeDelivery']['send']['children'][0]['backlog']['dispatchKey'] for p in original.values()}) == 2
    cold = FinalService(q.h.root, q.h.automation.auth)
    server._bind_qq_channel_provider(cold)
    try:
        cold.recover()
        cfg = definition('job', item)
        # Optional botUin was not bound in the original Job. Deliberate edit
        # of the declared current target/text remains the approved field set.
        cfg['arguments'].update(targetId='654321', text='current pending body')
        response = q.h.client.put('/api/jobs/' + item['id'], json={'definition': cfg,
            'expectedRevision': item['configRevision'], 'requestKey': 'pending-target'})
        assert response.status_code == 200, response.text
        q.default._bot = connected
        async def pulses():
            await asyncio.gather(cold.dispatch_once(), q.h.service.dispatch_once())
        run(pulses())
        after = cold.final.get(item['id'])
        assert set(after['runs']) == set(before['runs']) and set(after['attempts']) == set(before['attempts'])
        assert len(after['effects']) == len(q.rows) == 2, after['effects']
        assert all(row['body'] == {'user_id': 654321, 'message': 'current pending body'} for row in q.rows)
        assert all(r['state'] == 'completed' for r in after['runs'].values())
        run(cold.tick())
        assert len(q.rows) == 2
        for rid, prior in original.items():
            assert cold.final.store.get(item['id'])['finalRuntime']['plans'][rid]['frozen'] == prior['frozen']
    finally:
        q.default._bot = connected
        run(cold.close())


@pytest.mark.parametrize('gate', ['pause', 'disable', 'revoke'])
def test_proven_unsent_qq_current_gate_blocks_same_attempt_without_budget(gate, qq_http, run):
    q = qq_http
    grants = q.h.automation.auth._default_permissions()
    grants['job']['execute'] = grants['action']['manageSystemActions'] = True
    q.h.automation.auth.change_permissions(grants, 0)
    connected = q.default._bot
    q.default._bot = None
    item, rid = admit(q, actor={'type': 'agent', 'id': 'owned-QQ-author', 'revision': 1})
    run(q.h.service.dispatch_once())
    before = q.h.service.final.get(item['id'])
    assert before['runs'][rid]['state'] == 'paused_at_boundary'
    if gate == 'revoke':
        q.h.automation.auth.change_permissions(q.h.automation.auth._default_permissions(), 1)
    else:
        response = control(q.h, 'jobs', item, gate, 'pending-' + gate)
        assert response.status_code == 200, response.text
    q.default._bot = connected
    run(q.h.service.dispatch_once())
    after = q.h.service.final.get(item['id'])
    assert after['attempts'] == before['attempts'] and not after['effects'] and not q.rows


@pytest.mark.parametrize('bot', [None, '222222'])
def test_real_qq_no_send_cold_pulse_same_attempt_effect_current_target_text(qq_http, bot, run):
    q = qq_http
    channel = q.default if bot is None else q.selected
    connected = channel._bot
    channel._bot = None
    item, rid = admit(q, bot)
    frozen = copy.deepcopy(q.h.service.final.store.get(item['id'])['finalRuntime']['plans'][rid]['frozen'])
    run(q.h.service.dispatch_once())
    first = q.h.service.final.get(item['id'])
    assert first['runs'][rid]['state'] == 'paused_at_boundary'
    assert len(first['attempts']) == 1 and not first['effects'] and not q.rows
    plan = q.h.service.final.store.get(item['id'])['finalRuntime']['plans'][rid]
    child = plan['runtimeDelivery']['send']['children'][0]
    assert child['status'] == 'known-no-attempt' and child['attempted'] is False
    assert child['backlog']['dispatchKey'] == rid
    assert child['backlog']['text'] == '  original\nQQ  '
    assert child['backlog']['error']['code'] == 'bot_not_connected'
    public = q.h.service.get_run(rid, actor=USER)
    assert public['deliveryRuntime']['steps']['send']['pendingFires'] == [{**child['backlog'],
        'status': 'known-no-attempt', 'automaticDelivery': True}]
    before = copy.deepcopy(first['attempts'])
    cold = FinalService(q.h.root, q.h.automation.auth)
    from packages.web import server
    server._bind_qq_channel_provider(cold)
    try:
        cold.recover()
        cfg = definition('job', item)
        cfg['arguments'].update(targetType='group', targetId='654321', text='  changed\nraw  ')
        response = q.h.client.put('/api/jobs/' + item['id'], json={'definition': cfg,
            'expectedRevision': item['configRevision'], 'requestKey': 'runtime-edit'})
        assert response.status_code == 200, response.text
        channel._bot = connected
        run(cold.dispatch_once())  # existing pulse; no manual retry operation
        after = cold.final.get(item['id'])
        assert after['runs'][rid]['state'] == 'completed', json.dumps(after['effects'])
        assert list(after['attempts']) == list(before) and len(after['effects']) == 1
        assert q.rows == [{'bot': bot or '111111', 'operation': 'send_group_msg',
            'body': {'group_id': 654321, 'message': '  changed\nraw  '}}]
        assert after['effects'][0]['receipt']['receipt'] == {'ok': True, 'message_id': 'owned-1'}
        assert cold.get_run(rid, actor=USER)['deliveryRuntime']['steps']['send']['pendingFires'] == []
        assert cold.final.store.get(item['id'])['finalRuntime']['plans'][rid]['frozen'] == frozen
        run(cold.dispatch_once())
        assert len(q.rows) == 1
    finally:
        channel._bot = connected
        run(cold.close())


@pytest.mark.parametrize('mode', ['lost-response', 'missing-id'])
def test_runtime_actual_accepted_unknown_never_resends_or_creates_attempt(qq_http, mode, run):
    q = qq_http
    q.mode['value'] = mode
    item, rid = admit(q)
    run(q.h.service.dispatch_once())
    state = q.h.service.final.get(item['id'])
    assert state['runs'][rid]['state'] == 'uncertain'
    assert len(state['attempts']) == len(state['effects']) == len(q.rows) == 1, state['effects']
    cold = FinalService(q.h.root, q.h.automation.auth)
    from packages.web import server
    server._bind_qq_channel_provider(cold)
    try:
        cold.recover()
        for _ in range(2):
            run(cold.dispatch_once())
        assert cold.final.get(item['id']) == state
        assert len(q.rows) == 1
    finally:
        run(cold.close())


@pytest.mark.parametrize('next_bot', ['222222', None])
def test_unknown_selected_bot_pending_then_current_bot_switch_with_cas_and_no_manual_retry(qq_http, run, next_bot):
    q = qq_http
    item, rid = admit(q, '333333')
    run(q.h.service.dispatch_once())
    before = q.h.service.final.get(item['id'])
    assert before['runs'][rid]['state'] == 'paused_at_boundary' and not q.rows
    child = q.h.service.final.store.get(item['id'])['finalRuntime']['plans'][rid]['runtimeDelivery']['send']['children'][0]
    assert child['backlog']['error']['code'] == 'unknown_bot_uin'
    cfg = definition('job', item)
    cfg['arguments']['botUin'] = next_bot
    body = {'definition': cfg, 'expectedRevision': item['configRevision'], 'requestKey': 'explicit-bot-switch'}
    response = q.h.client.put('/api/jobs/' + item['id'], json=body)
    assert response.status_code == 200, response.text
    duplicate = q.h.client.put('/api/jobs/' + item['id'], json=body)
    assert duplicate.json()['operationId'] == response.json()['operationId']
    stale = q.h.client.put('/api/jobs/' + item['id'], json={**body, 'requestKey': 'stale-bot-switch'})
    assert stale.status_code == 409
    manual = q.h.client.post('/api/job-runs/' + rid + '/operations', json={'operation': 'retry',
        'expectedStateRevision': before['runs'][rid]['stateRevision'], 'requestKey': 'not-a-failed-retry'})
    assert manual.status_code == 409
    run(q.h.service.dispatch_once())
    after = q.h.service.final.get(item['id'])
    assert after['runs'][rid]['state'] == 'completed', json.dumps(after['effects'])
    assert list(after['attempts']) == list(before['attempts'])
    assert len(q.rows) == 1 and q.rows[0]['bot'] == (next_bot or '111111')


@pytest.mark.parametrize('corruption', ['missing-proof', 'wrong-attempt', 'wrong-arguments'])
def test_cold_corrupt_qq_no_send_proof_never_authorizes_automatic_replay(qq_http, run, corruption):
    q = qq_http
    bot = q.default._bot
    q.default._bot = None
    item, rid = admit(q)
    run(q.h.service.dispatch_once())
    with q.h.service.transaction() as db:
        job = q.h.service._get(db, 'job', item['id'])
        child = job['finalRuntime']['plans'][rid]['runtimeDelivery']['send']['children'][0]
        if corruption == 'missing-proof': child['receipt'].pop('qqNoSend')
        elif corruption == 'wrong-attempt': child['receipt']['qqNoSend']['attempt']['attemptId'] = 'attempt_unrelated'
        else: child['receipt']['qqNoSend']['arguments']['text'] = 'unrelated body'
        q.h.service._save(db, 'job', item['id'], job)
    q.default._bot = bot
    cold = FinalService(q.h.root, q.h.automation.auth)
    from packages.web import server
    server._bind_qq_channel_provider(cold)
    try:
        cold.recover()
        run(cold.dispatch_once())
        after = cold.final.get(item['id'])
        assert after['runs'][rid]['state'] == 'paused_at_boundary'
        assert cold.final.store.get(item['id'])['finalRuntime']['plans'][rid]['runtimeHold'] == 'runtime_reservation_unproven'
        assert len(after['attempts']) == 1 and not q.rows
    finally:
        run(cold.close())


@pytest.mark.parametrize('bot,code', [(None, 'bot_not_connected'), ('222222', 'bot_not_connected'),
                                    ('333333', 'unknown_bot_uin')])
def test_original_targetless_manual_qq_error_matches_actual_plugin_and_has_no_session_effect(qq_http, run, bot, code):
    q = qq_http
    channel = q.default if bot is None else q.selected
    connected = channel._bot
    channel._bot = None
    value = qq_job(q.h, bot)  # original immutable product_channel, no runtime opt-in
    item = create(q.h, value=value)
    item = control(q.h, 'jobs', item, 'enable').json()['result']['object']
    rid = launch(q.h, item)['runId']
    run(q.h.service.dispatch_once())
    result = q.h.service.final.get(item['id'])
    original = copy.deepcopy(result)
    wire = {'target_type': 'private', 'target_id': '123456', 'text': 'hello QQ'}
    if bot:
        wire['bot_uin'] = bot
    with httpx.Client(base_url=q.url, trust_env=False, timeout=30) as client:
        expected = client.post('/api/qq/send', json=wire).json()
    assert result['effects'][0]['receipt']['receipt'] == expected
    assert expected['error']['code'] == code
    assert result['effects'][0]['receipt']['outcome'] == 'rejected'
    assert len(result['runs']) == len(result['attempts']) == len(result['effects']) == 1 and not q.rows
    frozen = q.h.service.final.store.get(item['id'])['finalRuntime']['plans'][rid]['frozen']
    assert not ({'sessionId', 'sessionIds', 'sourceSessionId'} & set(frozen['steps'][0]['arguments']))
    # Ordinary rejected product_channel stays failed. Only the explicit new
    # runtime release holds a live Attempt for automatic pending drain.
    channel._bot = connected
    run(q.h.service.dispatch_once())
    assert q.h.service.final.get(item['id']) == original and not q.rows


@pytest.mark.parametrize('field,value', [('targetId', '123'), ('botUin', 'bad'), ('text', '  ')])
def test_invalid_runtime_qq_current_defaults_reject_before_operation_revision_or_effect(qq_http, field, value):
    q = qq_http
    item, _ = admit(q, '222222', launch_now=False)
    before = q.h.service.final.store.get(item['id'])
    with q.h.service.transaction(readonly=True) as db:
        operations = db.execute('SELECT count(*) FROM final_operations').fetchone()[0]
    cfg = definition('job', item)
    cfg['arguments'][field] = value
    response = q.h.client.put('/api/jobs/' + item['id'], json={'definition': cfg,
        'expectedRevision': item['configRevision'], 'requestKey': 'invalid-default'})
    assert response.status_code in {400, 422}, response.text
    assert q.h.service.final.store.get(item['id']) == before and not q.rows
    with q.h.service.transaction(readonly=True) as db:
        assert db.execute('SELECT count(*) FROM final_operations').fetchone()[0] == operations


@pytest.mark.parametrize('phase,expected,sends', [
    ('before-reservation', 'completed', 1), ('before-invoke', 'uncertain', 0),
    ('after-qq', 'uncertain', 1), ('after-receipt', 'completed', 1)])
def test_actual_qq_hard_crash_owner_handoff_original_identity_only(qq_http, run, phase, expected, sends):
    q = qq_http
    item, rid = admit(q)
    from packages.core import session
    from packages.web import server
    root = Path(__file__).resolve().parents[1]
    path = q.h.root.parent / 'owned-qq-crash.json'
    barrier = q.h.root.parent / 'owned-qq-crash-barrier'
    path.write_text(json.dumps({'root': str(q.h.root), 'auth': str(q.h.automation.auth.path.parent),
        'sessions': str(session.SESSION_DIR), 'job': item['id'], 'run': rid,
        'barrier': str(barrier), 'phase': phase, 'qqUrl': q.url}), encoding='utf-8')
    result = subprocess.run([sys.executable, str(root / 'scripts/run_tests_isolated.py'), '--',
        sys.executable, str(root / 'tests/retirement_runtime_crash_child.py'), str(path)],
        cwd=root, capture_output=True, text=True, timeout=65)
    assert result.returncode == 71, result.stdout + result.stderr
    assert barrier.read_text() == phase
    original = q.h.service.final.store.get(item['id'])['finalRuntime']['plans'][rid]['frozen']
    cold = FinalService(q.h.root, q.h.automation.auth)
    server._bind_qq_channel_provider(cold)
    cold.owner.claim(cold.owner.status()['epoch'])
    try:
        assert cold.owner.token[0] == 2
        cold.recover()
        run(cold.dispatch_once())
        after = cold.final.get(item['id'])
        assert after['runs'][rid]['state'] == expected, after
        assert len(after['attempts']) == len(after['effects']) == 1
        assert len(q.rows) == sends
        assert next(iter(after['attempts'].values()))['generation'] == 1
        assert cold.final.store.get(item['id'])['finalRuntime']['plans'][rid]['frozen'] == original
        run(cold.dispatch_once())
        assert len(q.rows) == sends
    finally:
        run(cold.close())
