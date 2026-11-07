"""Canonical family delivery with original frozen Attempts/Events and real IPC."""
import asyncio
import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from packages.actions.group_migration import command_definition
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService, object_ref
from tests.test_action_unification_p1 import action_http
from tests.test_action_unification_p3 import USER
from tests.test_action_unification_p6 import own
from tests.test_job_qq_native_provider import qq_http


def job_run(service, ref, arguments, *, contract='provider-receipt', key='family', notification=None):
    value = {'name': 'Frozen family Job', 'steps': [{'stepId': 'original', 'actionRef': ref,
        'arguments': {k: {'from': 'literal', 'value': v} for k, v in arguments.items()}, 'completion': {'contract': contract}}],
        'retention': {'policyRef': 'synthetic'}, 'notification': {'on': ['completed', 'uncertain'], 'recipients': []}}
    if notification is not None:
        value['notification'] = notification
    job = service.write('job', None, {'definition': value, 'expectedRevision': 0, 'requestKey': key + '-create'}, actor=USER)['result']['object']
    job = service.control('job', job['id'], {'operation': 'enable', 'expectedRevision': job['configRevision'],
        'expectedStateRevision': job['stateRevision'], 'requestKey': key + '-enable', 'parameters': {}}, actor=USER)['result']['object']
    run = service.create_run(job['id'], {'expectedRevision': job['configRevision'], 'input': {},
        'cause': {'type': 'manual'}, 'requestKey': key + '-run'}, actor=USER)
    return job, run


@pytest.mark.parametrize('lost', [False, True])
def test_P6_DELIVERY_API_01_real_http_frozen_attempt_cold_unknown_no_replay(action_http, tmp_path, monkeypatch, lost):
    _, service = action_http
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            if lost:
                self.connection.close()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"original":true}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('PAN_API_URL', f'http://127.0.0.1:{server.server_port}')
    raw = {'id': 'get', 'name': 'GET', 'enabled': True, 'effect': 'read',
           'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
           'execution': {'kind': 'api', 'method': 'GET', 'path': '/api/models'}}
    value = command_definition(raw)
    ref = object_ref('action', service.publish_action('frozen_get', value))
    own(service)
    job, admitted = job_run(service, ref, {})
    # A new head is not a reason to replace a frozen Step's original ActionRef.
    changed = copy.deepcopy(value)
    changed['execution']['config']['path'] = '/api/workspaces'
    service.write('action', ref['id'], {'definition': changed, 'expectedRevision': ref['revision'], 'requestKey': 'future-only'}, actor=USER)
    try:
        asyncio.run(service.dispatch_once())
        run = service.get_run(admitted['runId'], actor=USER)
        assert run['state'] == ('uncertain' if lost else 'completed')
        state = service.final.get(job['id'])
        assert len(state['attempts']) == 1 and len(state['effects']) == 1
        effect = state['effects'][0]
        assert effect['receipt']['effectKey'] == effect['effectKey']
        if not lost:
            assert effect['receipt']['receipt']['output']['structuredContent'] == {'original': True}
        asyncio.run(service.dispatch_once())
        cold = FinalService(service.root, service.auth)
        assert cold.get_run(admitted['runId'], actor=USER)['state'] == run['state']
        assert calls == ['/api/models']
        assert len(service.list('job', actor=USER)['items']) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_P6_DELIVERY_SESSION_01_real_worker_queue_provider_original_id_no_cli_no_replay(action_http, tmp_path):
    from packages.actions.store import system_definition
    from packages.actions.typed_executor import WorkerSessionProvider
    from packages.core import session, worker
    _, service = action_http
    target = session.Session(id='ses_p6_owned_queue', name='Owned P6 queue', queue_paused=True)
    session.save(target)
    service.executor.session_provider = WorkerSessionProvider()
    ref = object_ref('action', service.publish_action('legacy_message_queue', command_definition(system_definition('session-message'))))
    own(service)
    job, admitted = job_run(service, ref, {'sessionId': target.id, 'text': 'original queue once'})
    asyncio.run(service.dispatch_once())
    run = service.get_run(admitted['runId'], actor=USER)
    assert run['state'] == 'completed', service.final.get(job['id'])
    receipt = service.final.get(job['id'])['effects'][0]['receipt']
    queue_id = receipt['receipt']['output']['queueItemId']
    original = session.get(target.id, load_history=False)
    assert len(original.queue_pending) == 1
    assert worker._queue_item_id(original.queue_pending[0]) == queue_id
    assert original.queue_pending[0]['text'] == 'original queue once'
    assert receipt['effectKey']['runId'] == admitted['runId']
    asyncio.run(service.dispatch_once())
    assert len(session.get(target.id, load_history=False).queue_pending) == 1
    assert worker.find_alive_worker_by_session(target.id) is None


def test_P6_DELIVERY_SESSION_02_trigger_event_selector_original_pause_backend_and_identity(action_http):
    from packages.core import session, worker
    from packages.web.server import api_session_queue_paused
    from packages.jobs.action_trigger import deliver
    _, service = action_http
    target = session.Session(id='ses_p6_pause_backend', name='Owned pause backend')
    session.save(target)
    service.session_pause = api_session_queue_paused
    raw = {'id': 'pause', 'name': 'Pause selected from event', 'enabled': True, 'effect': 'write_local',
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'execution': {'kind': 'session_operation', 'operation': 'pause_everything', 'selector': {'mode': 'event', 'path': 'payload.target'}}}
    ref = object_ref('action', service.publish_action('pause_event', command_definition(raw)))
    own(service)
    value = {'name': 'Original event', 'eventTypes': ['p6.synthetic'],
             'creatorSelector': {'mode': 'registeredType', 'providerType': 'fixture'},
             'target': {'type': 'action', 'actionRef': ref}, 'arguments': {}}
    trigger = service.write('trigger', None, {'definition': value, 'expectedRevision': 0, 'requestKey': 'pause-trigger'}, actor=USER)['result']['object']
    trigger = service.control('trigger', trigger['id'], {'operation': 'enable', 'expectedRevision': trigger['revision'],
        'expectedStateRevision': trigger['stateRevision'], 'parameters': {}, 'requestKey': 'pause-trigger-enable'}, actor=USER)['result']['object']
    event = {'eventId': 'p6-original-event', 'type': 'p6.synthetic', 'creatorType': 'fixture', 'payload': {'target': target.id}}
    admitted = service.admit_event(event, trigger)
    asyncio.run(deliver(service))
    receipt = service.operation(admitted['operationId'], actor=USER)
    assert receipt['result']['status'] == 'completed', receipt
    assert receipt['result']['eventId'] == event['eventId'] and receipt['result']['triggerId'] == trigger['id']
    assert receipt['result']['output']['targets'][0]['sessionId'] == target.id
    assert receipt['result']['checkpoint']['targetIds'] == [target.id]
    assert session.get(target.id, load_history=False).queue_paused is True
    revision = session.get(target.id, load_history=False).queue_revision
    cold = FinalService(service.root, service.auth)
    repeated = cold.admit_event(event, trigger)
    assert repeated['replayed'] and repeated['operationId'] == admitted['operationId']
    asyncio.run(deliver(service))
    assert session.get(target.id, load_history=False).queue_revision == revision
    assert len(service.list('job', actor=USER)['items']) == 0
    assert worker.find_alive_worker_by_session(target.id) is None


def test_P6_DELIVERY_TEMPLATE_01_frozen_attempt_creates_one_child_same_ledger(action_http, tmp_path, monkeypatch):
    from tests.test_action_unification_p6_templates import registry
    service, old, _, maps, _, raw, ref, _ = registry(action_http, tmp_path, monkeypatch)
    from packages.actions.group_migration import LegacyGroupMigration
    migration = LegacyGroupMigration(service, old.root)
    migration.apply(migration.preview(maps, actor=USER), 'delivery-template-adopt', actor=USER)
    job, admitted = job_run(service, ref, {}, contract='job-created')
    asyncio.run(service.dispatch_once())
    assert service.get_run(admitted['runId'], actor=USER)['state'] == 'completed'
    effect = service.final.get(job['id'])['effects'][0]
    child = effect['receipt']['receipt']['output']['jobId']
    assert effect['receipt']['effectKey'] == effect['effectKey']
    assert effect['receipt']['receipt']['source']['effectKey'] == effect['effectKey']
    assert service.get('job', child, actor=USER)['name'] == raw['config']['name']
    asyncio.run(service.dispatch_once())
    assert len(service.list('job', actor=USER)['items']) == 2
    cold = FinalService(service.root, service.auth)
    assert cold.get_run(admitted['runId'], actor=USER)['state'] == 'completed'


def test_P6_DELIVERY_TEMPLATE_02_direct_trigger_original_event_operation_creates_once(action_http, tmp_path, monkeypatch):
    from tests.test_action_unification_p6_templates import registry
    from packages.actions.group_migration import LegacyGroupMigration
    from packages.jobs.action_trigger import deliver
    service, old, _, maps, _, raw, ref, _ = registry(action_http, tmp_path, monkeypatch)
    migration = LegacyGroupMigration(service, old.root)
    migration.apply(migration.preview(maps, actor=USER), 'trigger-template-adopt', actor=USER)
    value = {'name': 'Original template event', 'eventTypes': ['p6.template.event'],
        'creatorSelector': {'mode': 'registeredType', 'providerType': 'fixture'},
        'target': {'type': 'action', 'actionRef': ref}, 'arguments': {}}
    trigger = service.write('trigger', None, {'definition': value, 'expectedRevision': 0, 'requestKey': 'template-event-create'}, actor=USER)['result']['object']
    trigger = service.control('trigger', trigger['id'], {'operation': 'enable', 'expectedRevision': trigger['revision'],
        'expectedStateRevision': trigger['stateRevision'], 'requestKey': 'template-event-enable', 'parameters': {}}, actor=USER)['result']['object']
    event = {'eventId': 'original-template-event', 'type': 'p6.template.event', 'creatorType': 'fixture', 'payload': {}}
    admitted = service.admit_event(event, trigger)
    asyncio.run(deliver(service))
    receipt = service.operation(admitted['operationId'], actor=USER)
    assert receipt['result']['output']['templateId'] == raw['id']
    assert receipt['result']['source'] == {'eventId': event['eventId'], 'triggerId': trigger['id'], 'triggerRevision': trigger['revision']}
    cold = FinalService(service.root, service.auth)
    assert cold.admit_event(event, trigger)['operationId'] == admitted['operationId']
    asyncio.run(deliver(service))
    assert len(service.list('job', actor=USER)['items']) == 1


def test_P6_DELIVERY_SESSION_03_real_stdio_ipc_job_original_queue_receipt(tmp_path):
    import os
    import sys
    from pathlib import Path
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from packages.actions.store import system_definition
    from packages.actions.typed_executor import WorkerSessionProvider
    from packages.core import session, worker
    from tests.test_authorization_mcp import harness, close, grants, result
    async def exercise():
        h = harness(tmp_path)
        try:
            target = session.Session(id='ses_p6_ipc_queue', name='Owned IPC target', queue_paused=True)
            session.save(target)
            h.service.executor.session_provider = WorkerSessionProvider()
            ref = object_ref('action', h.service.publish_action('ipc_legacy_message', command_definition(system_definition('session-message'))))
            h.svc.auth.change_permissions(grants(), 0)
            params = StdioServerParameters(command=sys.executable, args=['-m', 'packages.mcp.server'], cwd=str(Path.cwd()),
                env={**os.environ, 'PAN_PLUGIN_IPC': h.bridge.address, 'PAN_AGENT_SESSION_ID': h.session.id,
                     'PAN_API_URL': 'http://127.0.0.1:1'})
            definition = {'name': 'IPC family Job', 'steps': [{'stepId': 'original', 'actionRef': ref,
                'arguments': {'sessionId': {'from': 'literal', 'value': target.id}, 'text': {'from': 'literal', 'value': 'IPC once'}},
                'completion': {'contract': 'provider-receipt'}}], 'retention': {'policyRef': 'synthetic'}}
            async with stdio_client(params) as streams, ClientSession(*streams) as client:
                await client.initialize()
                async def call(name, **arguments):
                    return result(await client.call_tool(name, arguments))
                created = await call('job_create', definition=definition, request_key='ipc-create')
                job = created['result']['object']
                enabled = await call('job_control', object_id=job['id'], operation='enable', expected_revision=job['configRevision'],
                    expected_state_revision=job['stateRevision'], request_key='ipc-enable')
                job = enabled['result']['object']
                admitted = await call('job_run', job_id=job['id'], expected_revision=job['configRevision'], request_key='ipc-run')
                await h.service.dispatch_once()
                assert (await call('job_run_get', run_id=admitted['runId']))['state'] == 'completed'
                repeat = await call('job_run', job_id=job['id'], expected_revision=job['configRevision'], request_key='ipc-run')
                assert repeat['operationId'] == admitted['operationId']
                effect = h.service.final.get(job['id'])['effects'][0]
                persisted = session.get(target.id, load_history=False)
                assert len(persisted.queue_pending) == 1
                assert worker._queue_item_id(persisted.queue_pending[0]) == effect['receipt']['receipt']['output']['queueItemId']
                assert worker.find_alive_worker_by_session(target.id) is None
        finally:
            await close(h)
    asyncio.run(exercise())


@pytest.mark.parametrize('disconnected', [False, True])
def test_P6_DELIVERY_CHANNEL_01_original_qq_http_plugin_synthetic_onebot_ack_and_no_send(action_http, qq_http, monkeypatch, disconnected):
    from packages.actions.store import system_definition
    _, service = action_http
    provider, calls, transport, channel = qq_http
    if disconnected:
        monkeypatch.setattr(channel, '_bot_fallback', None)
    service.executor.channel_provider = provider
    ref = object_ref('action', service.publish_action('legacy_qq_delivery', command_definition(system_definition('scheduled-task'))))
    own(service)
    job, admitted = job_run(service, ref, {'sessionId': None, 'text': 'original QQ once',
        'action': {'api': 'send_qq', 'args': {'targetType': 'user', 'targetId': '123456'}}})
    async def exercise():
        await service.dispatch_once()
        run = service.get_run(admitted['runId'], actor=USER)
        assert run['state'] == ('failed' if disconnected else 'completed')
        effect = service.final.get(job['id'])['effects'][0]
        original = effect['receipt']['receipt']['providerReceipt']
        assert effect['effectKey'] == original['effectKey']
        if disconnected:
            attempt = next(iter(service.final.get(job['id'])['attempts'].values()))
            assert original['qqNoSend']['attempt']['attemptId'] == attempt['attemptId']
            assert original['receipt']['error']['code'] == 'bot_not_connected'
        else:
            assert original['receipt']['message_id'] == 654321
        await service.dispatch_once()
        cold = FinalService(service.root, service.auth)
        assert cold.get_run(admitted['runId'], actor=USER)['state'] == run['state']
        assert len(transport['requests']) == 1
        assert len(calls) == (0 if disconnected else 1)
    asyncio.run(exercise())


def test_P6_DELIVERY_CANCEL_01_scheduled_shell_original_cancel_receipt_cleanup_no_replay(action_http, tmp_path):
    import sys
    from packages.actions.store import system_definition
    _, service = action_http
    script = tmp_path / 'cancel-owned.py'
    marker = tmp_path / 'cancel-started'
    script.write_text('from pathlib import Path\nimport time\nPath(' + repr(str(marker)) + ').write_text("once")\ntime.sleep(60)\n', 'utf-8')
    ref = object_ref('action', service.publish_action('cancel_union', command_definition(system_definition('scheduled-task'))))
    own(service)
    job, admitted = job_run(service, ref, {'sessionId': None, 'text': '', 'action': {'api': 'shell',
        'args': {'command': '"' + sys.executable + '" "' + str(script) + '"', 'cwd': str(tmp_path)}}})
    async def scenario():
        task = asyncio.create_task(service.dispatch_once())
        try:
            async with asyncio.timeout(10):
                while not marker.exists():
                    await asyncio.sleep(.02)
            run = service.get_run(admitted['runId'], actor=USER)
            cancelled = await service.control_run_postcommit(admitted['runId'], {'operation': 'cancel',
                'requestKey': 'original-shell-cancel', 'expectedStateRevision': run['stateRevision']}, actor=USER)
            await task
            return cancelled
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    receipt = asyncio.run(scenario())
    assert service.get_run(admitted['runId'], actor=USER)['state'] == 'cancelled'
    assert service.operation(receipt['operationId'], actor=USER)['status'] == 'completed'
    effect = service.final.get(job['id'])['effects'][0]
    assert effect['receipt']['receipt']['commands'][0]['cleanupProof']['confirmed'] is True
    cold = FinalService(service.root, service.auth)
    assert cold.operation(receipt['operationId'], actor=USER)['requestKey'] == 'original-shell-cancel'
    asyncio.run(service.dispatch_once())
    assert marker.read_text() == 'once' and len(service.final.get(job['id'])['attempts']) == 1


def test_P6_DELIVERY_HISTORY_01_real_notice_queue_same_outbox_and_cold_history(action_http):
    from packages.actions.store import system_definition
    from packages.actions.typed_executor import WorkerSessionProvider
    from packages.core import session, worker
    from packages.web.server import _send_final_notice
    _, service = action_http
    target = session.Session(id='ses_p6_real_notice', name='Owned message and notice', queue_paused=True)
    session.save(target)
    service.executor.session_provider = WorkerSessionProvider()
    service.outbox_sender = _send_final_notice
    ref = object_ref('action', service.publish_action('family_notice', command_definition(system_definition('session-message'))))
    own(service)
    job, admitted = job_run(service, ref, {'sessionId': target.id, 'text': 'original work'},
        notification={'on': ['completed'], 'recipients': [{'recipientId': 'original-recipient', 'channelId': 'session', 'targetRef': target.id}]})
    async def scenario():
        await service.dispatch_once()
        await service.deliver_outbox()
        await service.deliver_outbox()
    asyncio.run(scenario())
    history = service.final.history(job['id'])
    row = next(r for r in history['outbox'] if r['channel'] == 'session')
    assert row['status'] == 'acked' and row['runId'] == admitted['runId']
    assert row['recipientId'] == 'original-recipient' and row['targetRef'] == target.id
    persisted = session.get(target.id, load_history=False)
    assert len(persisted.queue_pending) == 2
    notice = next(r for r in persisted.queue_pending if r.get('type') == 'notice')
    assert notice['eventId'] == row['outboxId'] and notice['jobId'] == job['id']
    assert json.loads(notice['result'])['runId'] == admitted['runId']
    cold = FinalService(service.root, service.auth)
    projected = cold.get_run(admitted['runId'], actor=USER)
    assert projected['state'] == 'completed'
    assert projected['notifications'][0]['outboxId'] == row['outboxId']
    assert projected['notifications'][0]['receipt']['binding']['outboxId'] == row['outboxId']
    assert projected['notifications'][0]['receipt']['queueReceipt']['ok'] is True
    assert projected['notifications'][0]['receipt']['queueReceipt']['sessionId'] == target.id
    assert worker.find_alive_worker_by_session(target.id) is None
