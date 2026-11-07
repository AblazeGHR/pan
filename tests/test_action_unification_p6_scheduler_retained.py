"""Owned original Scheduler bytes -> reviewed successor queries/Run controls.

register_original is explicitly an alias, not a writer handoff. Synthetic old
records are settled; unknown/active old records must remain blocked.
"""
import asyncio
import hashlib
import json
import os
import subprocess
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService
from packages.scheduler import api, retained
from tests.test_job_final_http_service import h, enabled, job_def, launch, USER
from tests.test_job_final_clients import tcp, grants, unpack, ROOT


@pytest.fixture
def mapped(h, tmp_path, monkeypatch):
    root = tmp_path / 'owned-old'
    monkeypatch.setenv('PAN_BACKGROUND_JOBS_DIR', str(root))
    monkeypatch.setenv('PAN_SCHEDULER_DIR', str(root))
    path = root / 'jobs' / 'old.json'
    path.parent.mkdir(parents=True)
    source = {'jobId': 'job_old', 'taskId': 'sch_original', 'kind': 'scheduled-task',
        'status': 'completed', 'revision': 7, 'generation': 3, 'targetSessionId': 'owned-test-session',
        'enabled': False, 'runCount': 1, 'schedule': [{'kind': 'interval', 'intervalSec': 60}],
        'notificationState': 'delivered', 'terminalEventId': 'old-terminal'}
    path.write_text(json.dumps(source), encoding='utf8')
    (root / 'runs.jsonl').write_text(json.dumps({'run_id': 'old_run', 'task_id': 'sch_original',
        'status': 'unknown', 'dispatch_key': 'old_dispatch'}), encoding='utf8')
    raw = path.read_bytes()
    job = enabled(h, job_def(h, notification={'on': ['completed', 'cancelled'], 'recipients': [{'recipientId':'owned-recipient', 'channelId':'session', 'targetRef':'owned-test-session'}]}))
    h.service.final.register_original(job['id'], 'sch_original',
        source_digest=hashlib.sha256(raw).hexdigest(), source_path=str(path),
        expected_revision=job['configRevision'])
    router = api.router
    if os.environ.get('P6_RETAINED_BASELINE') == '1':
        # Execute the actual baseline API, not a fixture that imitates its 404.
        code = subprocess.check_output(['git', 'show', '911935d95dc212af79398f1af684717f6a65e1e4:packages/scheduler/api.py'], encoding='utf8')
        module = types.ModuleType('packages.scheduler.p6_baseline_api')
        module.__package__ = 'packages.scheduler'
        exec(compile(code, '<baseline scheduler api>', 'exec'), module.__dict__)
        router = module.router
    h.app.include_router(router)
    h.old_path, h.old_bytes, h.job, h.old_root = path, raw, job, root
    yield h
    assert path.read_bytes() == raw


def task(h):
    return api._history(type('R', (), {'app': h.app})()).get('sch_original')


def envelope(h, run, key='old-cancel'):
    current = h.service.get_run(run['runId'], actor=USER)
    return {'jobId': h.job['id'], 'runId': run['runId'], 'expectedRevision': h.job['configRevision'],
            'expectedStateRevision': current['stateRevision'], 'requestKey': key}


def test_P6_SCHED_01_real_tcp_original_history_cold_successor_and_no_replay(mapped, tcp):
    import httpx
    h = mapped
    original = launch(h, h.job)
    with httpx.Client(base_url=tcp, headers={'Authorization': 'Bearer ' + h.token}, trust_env=False) as client:
        assert client.post('/api/scheduler/tasks/sch_original/cancel', json={},
            headers={'Authorization':'Bearer invalid'}).status_code == 401
        response = client.get('/api/scheduler/tasks/sch_original/runs')
        assert response.status_code == 200
        result = response.json()
        assert result['runs'] == [{'runId': 'old_run', 'taskId': 'sch_original', 'status': 'unknown', 'dispatchKey': 'old_dispatch'}]
        assert result['canonicalSuccessor']['runs'][0]['runId'] == original['runId']
        assert result['canonicalSuccessor']['historicalExecutionAdopted'] is False
        detail = client.get('/api/scheduler/tasks/job_old').json()
        assert detail['task']['revision'] == 7 and detail['task']['generation'] == 3
        assert detail['task']['notificationState'] == 'delivered'
        assert detail['canonicalSuccessor']['jobId'] == h.job['id']
        body = envelope(h, original)
        cancelled = client.post('/api/scheduler/tasks/sch_original/cancel', json=body)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()['cancelled'] is False
        assert cancelled.json()['status'] == 'accepted'
        repeated = client.post('/api/scheduler/tasks/sch_original/cancel', json=body).json()
        assert repeated['operation']['operationId'] == cancelled.json()['operation']['operationId']
    cold = FinalService(h.root, h.automation.auth)
    try:
        assert retained.query(cold, task(h), actor=USER)['runs'][0]['runId'] == original['runId']
        assert cold.operation_for_key('cancel', 'old-cancel', actor=USER)['operationId'] == cancelled.json()['operation']['operationId']
        assert len(cold.final.history(h.job['id'])['runs']) == 1
        assert not h.provider.calls
    finally:
        asyncio.run(cold.close())


def test_P6_SCHED_02_cancel_race_revision_foreign_run_and_unknown_preserve(mapped):
    h = mapped
    run = launch(h, h.job)
    body = envelope(h, run)
    bad = {**body, 'expectedRevision': body['expectedRevision'] + 1}
    with pytest.raises(fc.ContractError) as exc:
        asyncio.run(retained.cancel(h.service, task(h), bad, actor=USER))
    assert exc.value.code == 'revision_conflict'
    def submit(index):
        try:
            return asyncio.run(retained.cancel(h.service, task(h), {**body, 'requestKey': 'race-' + str(index)}, actor=USER))['status']
        except fc.ContractError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(submit, range(2))) == ['accepted', 'revision_conflict']
    changed = dict(json.loads(h.old_bytes), status='unknown')
    # An unknown old record is never upgraded by its separate alias metadata.
    old_task = task(h)
    old_task['history']['source'] = changed
    with pytest.raises(fc.ContractError) as exc:
        retained.query(h.service, old_task, actor=USER)
    assert exc.value.code == 'reconciliation_required'
    assert not h.provider.calls


def test_P6_SCHED_03_actual_notification_outbox_once_history_and_cold(mapped):
    h = mapped
    run = launch(h, h.job)
    from packages.core import session
    from packages.web.server import _send_final_notice
    target = session.get('owned-test-session', load_history=False)
    target.queue_paused = True
    session.save(target)
    h.service.outbox_sender = _send_final_notice
    async def finish():
        await h.service.dispatch_once()
        await h.service.deliver_outbox()
        await h.service.deliver_outbox()
    asyncio.run(finish())
    result = retained.query(h.service, task(h), actor=USER)
    assert result['runs'][0]['state'] == 'completed'
    assert len(h.provider.calls) == 1
    notices = [row for row in result['notifications'] if row['channel'] == 'session']
    assert len(notices) == 1 and notices[0]['status'] == 'acked'
    queued = session.get('owned-test-session', load_history=False).queue_pending
    assert len(queued) == 1 and queued[0]['eventId'] == notices[0]['outboxId']
    assert queued[0]['jobId'] == h.job['id']
    assert json.loads(queued[0]['result'])['runId'] == run['runId']
    cold = FinalService(h.root, h.automation.auth)
    try:
        assert retained.query(cold, task(h), actor=USER)['notifications'] == result['notifications']
    finally:
        asyncio.run(cold.close())


def test_P6_SCHED_04_real_stdio_original_alias_and_cancel(mapped, tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from packages.automation.authorization import bind_objects
    from packages.core.manifest_loader import ManifestConfig, McpServer
    from packages.core.session import Session
    from packages.plugins.ipc import Bridge
    from packages.plugins.runtime import Runtime
    h = mapped
    run = launch(h, h.job)
    body = envelope(h, run, 'stdio-cancel')
    grants(h, True)
    session = Session(id='owned-test-session', name='Owned caller', adapter_config={
        'plugin_bindings': [{'plugin_id': 'pan', 'mode': 'standalone', 'enabled': True}]})
    manifest = ManifestConfig(mcp_servers=[McpServer('pan', command=os.sys.executable, args=['-m', 'packages.mcp.server'])])
    runtime = Runtime(lambda: manifest, lambda sid: session if sid == session.id else None, data_root=tmp_path/'plugins')
    bind_objects(runtime, h.app)
    async def scenario():
        bridge = Bridge(runtime, tmp_path)
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith('PAN_')}
        env.update(PAN_PLUGIN_IPC=bridge.address, PAN_AGENT_SESSION_ID=session.id, PAN_API_URL='http://127.0.0.1:1')
        try:
            params = StdioServerParameters(command=os.sys.executable, args=['-m', 'packages.mcp.server'], cwd=str(ROOT), env=env)
            async with stdio_client(params) as streams, ClientSession(*streams) as client:
                await client.initialize()
                rows = unpack(await client.call_tool('scheduler_runs', {'task_id':'sch_original', 'target_session_id':session.id}))
                assert rows['runs'][0]['runId'] == 'old_run'
                assert rows['canonicalSuccessor']['runs'][0]['runId'] == run['runId']
                reply = unpack(await client.call_tool('scheduler_cancel', {'task_id':'sch_original',
                    'target_session_id':session.id, 'job_id':body['jobId'], 'run_id':body['runId'],
                    'expected_revision':body['expectedRevision'], 'expected_state_revision':body['expectedStateRevision'],
                    'request_key':body['requestKey']}))
                assert reply['ok'] and reply['status'] == 'accepted', reply
        finally:
            await bridge.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['owner_epoch', 'owner_nonce', 'source_digest', 'foreign_run', 'grant_revoked'])
def test_P6_SCHED_05_owner_revision_and_identity_refusals_have_no_effect(mapped, failure):
    h = mapped
    run = launch(h, h.job)
    body = envelope(h, run)
    who = USER
    original_token = None
    if failure.startswith('owner_'):
        h.service.owner.claim(0)
        original_token = h.service.owner.token
        epoch, nonce = original_token
        h.service.owner.token = (epoch + 1, nonce) if failure == 'owner_epoch' else (epoch, 'foreign-lease')
    elif failure == 'source_digest':
        h.service.final.store.mutate(h.job['id'], lambda row: row['runtimeMetadata']['originals']['sch_original'].update(sourceDigest='0'*64))
    elif failure == 'foreign_run':
        # The public helper uses a fixed create key, so use a unique definition
        # and request identity for the unrelated Job.
        other = h.service.write('job', None, {'definition': job_def(h), 'expectedRevision':0, 'requestKey':'other-job'}, actor=USER)['result']['object']
        other = h.service.control('job', other['id'], {'operation':'enable', 'expectedRevision':1, 'expectedStateRevision':0,
            'parameters':{}, 'requestKey':'other-enable'}, actor=USER)['result']['object']
        other_run = h.service.create_run(other['id'], {'expectedRevision':1, 'requestKey':'other-run', 'input':{}, 'cause':{'type':'manual'}}, actor=USER)
        body['runId'] = other_run['runId']
    else:
        grants(h, False)
        who = {'type':'agent', 'id':'agent', 'revision':h.automation.auth.permissions_status()['revision']}
    try:
        with pytest.raises(fc.ContractError) as exc:
            asyncio.run(retained.cancel(h.service, task(h), body, actor=who))
        assert exc.value.code in {'revision_conflict', 'reference_conflict', 'authorization_denied'}
        assert h.service.get_run(run['runId'], actor=USER)['state'] == 'queued'
        assert not h.provider.calls
    finally:
        if original_token is not None:
            h.service.owner.token = original_token


def test_P6_SCHED_06_unconfirmed_notification_is_not_resent_after_cold_query(mapped):
    h = mapped
    launch(h, h.job)
    sent = []
    async def unknown(row):
        sent.append(row['outboxId'])
        return {'outcome':'unknown'}
    h.service.outbox_sender = unknown
    async def finish():
        await h.service.dispatch_once()
        await h.service.deliver_outbox()
        await h.service.deliver_outbox()
    asyncio.run(finish())
    rows = retained.query(h.service, task(h), actor=USER)['notifications']
    assert sent and len(sent) == len(set(sent))
    assert all(row['status'] == 'uncertain' for row in rows)
    cold = FinalService(h.root, h.automation.auth)
    try:
        cold.outbox_sender = unknown
        asyncio.run(cold.deliver_outbox())
        assert len(sent) == len(rows)
        assert retained.query(cold, task(h), actor=USER)['notifications'] == rows
    finally:
        asyncio.run(cold.close())


def test_P6_SCHED_07_trigger_event_dedup_and_fixed_job_cold_alias(mapped):
    h = mapped
    trigger = h.service.write('trigger', None, {'definition': {
        'name':'Original controlled event', 'eventTypes':['p6.synthetic'],
        'creatorSelector':{'mode':'registeredType', 'providerType':'fixture'},
        'targetJobId':h.job['id']}, 'expectedRevision':0, 'requestKey':'event-trigger'}, actor=USER)['result']['object']
    trigger = h.service.control('trigger', trigger['id'], {'operation':'enable',
        'expectedRevision':trigger['revision'], 'expectedStateRevision':trigger['stateRevision'],
        'parameters':{}, 'requestKey':'event-enable'}, actor=USER)['result']['object']
    event = {'eventId':'p6-retained-original-event', 'type':'p6.synthetic', 'creatorType':'fixture', 'payload':{}}
    admitted = h.service.admit_event(event, trigger)
    cold = FinalService(h.root, h.automation.auth)
    try:
        repeated = cold.admit_event(event, trigger)
        assert repeated['runId'] == admitted['runId'] and repeated['operationId'] == admitted['operationId']
        queried = retained.query(cold, task(h), actor=USER)
        assert len(queried['runs']) == 1 and queried['runs'][0]['runId'] == admitted['runId']
        history = cold.final.history(h.job['id'])
        assert history['events'][0]['eventId'] == event['eventId']
        assert history['events'][0]['triggerId'] == trigger['id']
        assert not h.provider.calls
    finally:
        asyncio.run(cold.close())
