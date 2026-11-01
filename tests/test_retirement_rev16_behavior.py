"""Real owned canonical HTTP/Event/Session queue and notification receipts."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn

from packages.core import session, worker
from packages.jobs.final_service import FinalService, definition
from packages.scheduler.providers import time_definition
from tests.job_retirement_native_fixture import h as native_h
from tests.test_job_final_http_service import enabled, launch, control
from tests.test_job_final_creators import config as time_config
from tests.test_session_product_closure import config


@pytest.fixture
def h(native_h, tmp_path):
    owned = {}
    for sid in ('ses_a', 'ses_b', 'ses_notice'):
        session.save(session.Session(id=sid, name=sid, workdir=str(tmp_path), queue_paused=True))
        owned[sid] = session.get(sid)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    assert port not in {8767, 8768}
    server = uvicorn.Server(uvicorn.Config(native_h.app, lifespan='off', log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started: break
        time.sleep(.01)
    assert server.started
    client = httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False,
                          headers={'Authorization': 'Bearer ' + native_h.token})
    value = SimpleNamespace(**{**vars(native_h), 'client': client, 'owned': owned, 'tmp': tmp_path})
    try:
        yield value
    finally:
        client.close()
        server.should_exit = True
        thread.join(6)
        listener.close()
        if thread.is_alive():
            # Preserve the real unresolved request identity instead of extending
            # shutdown timeouts or silently dropping the assertion.
            print('OWNED_HTTP_TEARDOWN_PENDING=' + repr([
                {'task': repr(task), 'stack': [(f.f_code.co_filename, f.f_lineno)
                    for f in task.get_stack()]} for task in server.server_state.tasks]))
        assert not thread.is_alive()


def schedule(h, rules, *, operation="assign"):
    job = enabled(h, config(h, operation, sessionId='ses_a', text='scheduled', source='automation'))
    source = time_definition('owned rules', time_config(rules))
    response = h.client.post('/api/event-creators', json={'definition': source, 'expectedRevision': 0, 'requestKey': 'creator'})
    assert response.status_code == 201, response.text
    creator = control(h, 'event-creators', response.json()['result']['object'], 'enable', 'enable-creator').json()['result']['object']
    trigger_def = {'name': 'owned subscription', 'targetJobId': job['id'],
        'creatorSelector': {'mode': 'ids', 'ids': [creator['id']]}, 'eventTypes': ['scheduler.time.v2']}
    response = h.client.post('/api/triggers', json={'definition': trigger_def, 'expectedRevision': 0, 'requestKey': 'trigger'})
    assert response.status_code == 201, response.text
    trigger = control(h, 'triggers', response.json()['result']['object'], 'enable', 'enable-trigger').json()['result']['object']
    h.service.refresh_subscriptions()
    return job, creator, trigger


def fire(h, instant):
    h.service.refresh_subscriptions()
    asyncio.run(h.service.creator_runtime.tick(at=instant))
    asyncio.run(h.service.tick())


def instant(offset):
    from packages.scheduler.providers import timestamp
    return timestamp(datetime.now(timezone.utc) + timedelta(seconds=offset))


def test_schedule_replacement_removes_old_rule_and_fires_only_new_real_event(h):
    old_at, new_at = instant(30), instant(60)
    job, creator, _ = schedule(h, [{'ruleId': 'old', 'type': 'once', 'at': old_at}])
    value = definition('event_creator', creator)
    value['config']['rules'] = [
        {'ruleId': 'cron', 'type': 'cron', 'expression': '30 8 * * *', 'timezone': 'UTC', 'dstPolicy': 'gap-skip-fold-first'},
        {'ruleId': 'new', 'type': 'interval', 'seconds': '7200', 'anchor': new_at, 'advanceMode': 'grid'}]
    response = h.client.put('/api/event-creators/' + creator['id'], json={
        'definition': value, 'expectedRevision': creator['revision'], 'requestKey': 'replace'})
    assert response.status_code == 200, response.text
    current = response.json()['result']['object']
    assert [r['type'] for r in current['config']['rules']] == ['cron', 'interval']
    cursors = h.service.creators.get(creator['id'])['cursor']['rules']
    assert set(cursors) == {'cron', 'new'}
    assert min(c['nextFireAt'] for c in cursors.values()) == new_at
    fire(h, old_at)
    assert not session.get('ses_a').queue_pending
    assert h.service.final.history(job['id'])['runs'] == []
    fire(h, new_at)
    history = h.service.final.history(job['id'])
    assert len(history['runs']) == len(history['effects']) == 1
    cause = history['runs'][0]['cause']
    event = h.service.events.get(cause['eventId'])['event']
    assert event['payload']['ruleId'] == 'new' and event['payload']['scheduledAt'] == new_at
    assert event['creatorRevision'] == current['revision']
    assert len(session.get('ses_a').queue_pending) == 1
    fire(h, new_at)
    assert h.service.final.history(job['id']) == history


@pytest.mark.parametrize('rules', [[], [{'ruleId': 'bad', 'type': 'cron', 'expression': 'not a cron',
    'timezone': 'UTC', 'dstPolicy': 'gap-skip-fold-first'}]])
def test_literal_empty_or_bad_schedule_update_rejects_without_any_write(h, rules):
    _, creator, _ = schedule(h, [{'ruleId': 'old', 'type': 'once', 'at': instant(30)}])
    original = copy.deepcopy(h.service.creators.get(creator['id']))
    with h.service.transaction(readonly=True) as db:
        before = db.execute('SELECT count(*) FROM final_operations').fetchone()[0]
    value = definition('event_creator', creator)
    value['config']['rules'] = rules
    response = h.client.put('/api/event-creators/' + creator['id'], json={
        'definition': value, 'expectedRevision': creator['revision'], 'requestKey': 'bad'})
    assert response.status_code in {400, 422}, response.text
    assert h.service.creators.get(creator['id']) == original
    with h.service.transaction(readonly=True) as db:
        assert db.execute('SELECT count(*) FROM final_operations').fetchone()[0] == before
    assert not session.get('ses_a').queue_pending


@pytest.mark.parametrize('operation', ['disable', 'retire'])
def test_job_lifecycle_preserves_admitted_run_but_explicit_cancel_prevents_effect(h, operation):
    value = config(h, 'assign', sessionId='ses_a', text='admitted')
    value['overlap'] = {'mode': 'queue', 'maxActive': 1, 'maxPending': 4}
    job = enabled(h, value)
    first = launch(h, job, 'first')
    second = launch(h, job, 'second')
    changed = control(h, 'jobs', job, operation, 'lifecycle')
    assert changed.status_code == 200, changed.text
    latest = changed.json()['result']['object']
    run = h.client.get('/api/job-runs/' + second['runId']).json()
    cancelled = h.client.post('/api/job-runs/' + second['runId'] + '/operations', json={
        'operation': 'cancel', 'expectedStateRevision': run['stateRevision'], 'requestKey': 'cancel'})
    assert cancelled.status_code in {200, 202}, cancelled.text
    rejected = h.client.post('/api/jobs/' + job['id'] + '/runs', json={
        'expectedRevision': latest['configRevision'], 'requestKey': 'after', 'input': {}, 'cause': {'type': 'manual'}})
    assert rejected.status_code in {409, 422}, rejected.text
    asyncio.run(h.service.dispatch_once())
    assert h.client.get('/api/job-runs/' + first['runId']).json()['state'] == 'completed'
    assert h.client.get('/api/job-runs/' + second['runId']).json()['state'] == 'cancelled'
    assert len(session.get('ses_a').queue_pending) == 1
    history = h.service.final.history(job['id'])
    assert len(history['effects']) == 1
    cold = FinalService(h.root, h.automation.auth)
    try:
        asyncio.run(cold.dispatch_once())
        assert cold.final.history(job['id']) == history
        assert len(session.get('ses_a').queue_pending) == 1
    finally:
        asyncio.run(cold.close())


def test_creator_disable_stops_future_events_and_effects(h):
    at = instant(30)
    job, creator, _ = schedule(h, [{'ruleId': 'old', 'type': 'once', 'at': at}])
    response = control(h, 'event-creators', creator, 'disable', 'stop-source')
    assert response.status_code == 200, response.text
    fire(h, at)
    assert h.service.final.history(job['id'])['runs'] == []
    assert not session.get('ses_a').queue_pending
    assert h.service.creators.get(creator['id'])['state'] == 'disabled'


def test_literal_empty_schedule_create_rejected_without_operation_creator_or_effect(h):
    source = time_definition('empty literal rules', time_config([]))
    with h.service.transaction(readonly=True) as db:
        before = db.execute('SELECT count(*) FROM final_operations').fetchone()[0]
    result = h.client.post('/api/event-creators', json={'definition': source, 'expectedRevision': 0, 'requestKey': 'empty'})
    assert result.status_code in {400, 422}, result.text
    assert h.service.creators.list() == []
    with h.service.transaction(readonly=True) as db:
        assert db.execute('SELECT count(*) FROM final_operations').fetchone()[0] == before
    assert not session.get('ses_a').queue_pending


def test_actual_missing_notification_target_unknown_receipt_cold_no_resend(h):
    value = config(h, 'assign', sessionId='ses_a', text='body')
    value['notification'] = {'on': ['completed'], 'recipients': [
        {'recipientId': 'notice', 'channelId': 'session', 'targetRef': 'ses_notice'}]}
    job = enabled(h, value)
    launch(h, job)
    session.delete('ses_notice')
    calls = []
    async def send(row):
        calls.append(row['outboxId'])
        if row['channel'] == 'ws': return {'outcome': 'succeeded'}
        actual = await worker.enqueue_notice(row['targetRef'], json.dumps(row['summary']),
            source='automation', event_id=row['outboxId'], notice_kind='job_terminal', job_id=row['jobId'])
        assert actual['error']['code'] == 'session_not_found'
        return {'outcome': 'unknown'}
    h.service.outbox_sender = send
    asyncio.run(h.service.dispatch_once())
    asyncio.run(h.service.deliver_outbox())
    history = h.service.final.history(job['id'])
    assert sorted(r['status'] for r in history['outbox']) == ['acked', 'uncertain']
    cold = FinalService(h.root, h.automation.auth)
    cold.outbox_sender = send
    try:
        asyncio.run(cold.dispatch_once())
        asyncio.run(cold.deliver_outbox())
        assert cold.final.history(job['id']) == history
        assert len(calls) == 2
        assert len(session.get('ses_a').queue_pending) == 1 and session.get('ses_notice') is None
    finally:
        asyncio.run(cold.close())


def partial(h):
    value = config(h, 'session-broadcast', sessionIds=['ses_a', 'ses_b'], text='fanout')
    value['notification'] = {'on': ['uncertain'], 'recipients': [
        {'recipientId': 'owned-notice', 'channelId': 'session', 'targetRef': 'ses_notice'}]}
    job = enabled(h, value)
    admitted = launch(h, job)
    session.delete('ses_b')  # existed at admission; real provider rejects missing target
    emitted = []
    async def send(row):
        emitted.append(copy.deepcopy(row))
        if row['channel'] == 'ws': return {'outcome': 'succeeded'}
        result = await worker.enqueue_notice(row['targetRef'], json.dumps(row['summary']),
            source='automation', event_id=row['outboxId'], notice_kind='job_terminal',
            job_id=row['jobId'], notice_status=row['summary']['status'])
        return {'outcome': 'succeeded' if result.get('ok') else 'unknown'}
    h.service.outbox_sender = send  # owned sink composes the same real enqueue as web lifespan
    asyncio.run(h.service.dispatch_once())
    asyncio.run(h.service.deliver_outbox())
    history = h.service.final.history(job['id'])
    targets = history['effects'][0]['receipt']['receipt']['targets']
    assert [r['effectKey']['targetId'] for r in targets] == ['ses_a', 'ses_b']
    assert [r['outcome'] for r in targets] == ['succeeded', 'rejected']
    assert history['effects'][0]['receipt']['outcome'] == 'partial'
    assert history['runs'][0]['state'] == 'uncertain'
    assert len(session.get('ses_a').queue_pending) == 1
    notices = session.get('ses_notice').queue_pending
    assert len(notices) == 1 and notices[0]['noticeKind'] == 'job_terminal'
    assert len(emitted) == len(history['outbox']) == 2
    assert all(row['status'] == 'acked' for row in history['outbox'])
    summary = json.loads(notices[0]['result'])
    assert summary['runId'] == admitted['runId'] and summary['status'] == 'uncertain'
    assert summary['failedTargets'] == ['ses_b']
    assert summary['targetResults'][1]['outcome'] == 'rejected'
    cold = FinalService(h.root, h.automation.auth)
    cold.outbox_sender = send
    try:
        asyncio.run(cold.dispatch_once())
        asyncio.run(cold.deliver_outbox())
        assert cold.final.history(job['id']) == history
        assert len(session.get('ses_a').queue_pending) == len(session.get('ses_notice').queue_pending) == 1
        assert len(emitted) == 2
    finally:
        asyncio.run(cold.close())
    return targets, emitted


def test_partial_fanout_real_results_notice_receipt_once_and_cold_no_replay(h):
    partial(h)


def test_partial_failure_terminal_notice_identifies_actual_failed_target(h):
    targets, emitted = partial(h)
    failed_id = targets[1]['effectKey']['targetId']
    # Old node requires the visible parent notification to identify the failed
    # target, not merely a queryable effect receipt elsewhere.
    assert failed_id in json.dumps(emitted[0]['summary']), emitted[0]
