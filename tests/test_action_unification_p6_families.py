"""Named legacy family successors using isolated providers and original receipts."""
import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from packages.actions import store, group_catalog
from packages.actions.group_migration import LegacyGroupMigration, LegacyGroupCompatibility, enroll_fresh_source, command_definition
from packages.actions.group_catalog import legacy_result
from packages.actions.groups import Groups
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService, object_ref
from tests.test_action_unification_p1 import action_http
from tests.test_action_unification_p3 import USER
from tests.test_action_unification_p6 import own
from tests.test_action_unification_p6_compat import adopted


@pytest.mark.parametrize('path', ['/api/sessions', '/api/models', '/api/workspaces'])
def test_P6_API_01_get_adopt_edit_cold_no_replay(action_http, tmp_path, monkeypatch, path):
    _, service = action_http
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'path': self.path, 'value': '雪'}).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('PAN_API_URL', f'http://127.0.0.1:{server.server_port}')
    root = tmp_path / 'api-source'
    enroll_fresh_source(root)
    monkeypatch.setattr(store, 'root', lambda: root)
    monkeypatch.setattr(group_catalog, 'canonical_path', lambda: service.creators.path)
    old = store.Store(root)
    raw = {'id': 'query', 'name': 'Original GET', 'enabled': True, 'effect': 'read',
           'input_schema': {'type': 'object', 'properties': {'requestKey': {'type': 'string', 'default': 'business'}},
                            'additionalProperties': False},
           'execution': {'kind': 'api', 'method': 'GET', 'path': path}}
    old.put('actions', raw)
    old.put('groups', {'id': 'queries', 'name': 'Queries', 'action_ids': ['query']})
    built = old.build('queries')
    raw = old.read()['actions']['query']
    ref = object_ref('action', service.publish_action('old_query', command_definition(raw)))
    maps = [{'groupId': 'queries', 'version': v, 'actionId': 'query',
             'definitionDigest': fc.digest(raw), 'actionRef': ref} for v in ('draft', built['version'])]
    migration = LegacyGroupMigration(service, root)
    before = old.path.read_bytes()
    plan = migration.preview(maps, actor=USER)
    assert plan['blocked'] == []
    own(service)
    migration.apply(plan, 'api-adopt', actor=USER)
    try:
        receipt = asyncio.run(Groups(service).invoke(built['version'], 'query', {}, 'transport-key', actor=USER))
        reply = legacy_result(Groups(service).release(built['version'], actor=USER), 'query', receipt)
        assert reply['structuredContent'] == {'path': path + '?requestKey=business', 'value': '雪'}
        assert reply['isError'] is False
        assert len(calls) == 1
        cold = FinalService(service.root, service.auth)
        repeated = asyncio.run(Groups(cold).invoke(built['version'], 'query', {}, 'transport-key', actor=USER))
        assert repeated['operationId'] == receipt['operationId'] and repeated['replayed']
        assert repeated['result']['output'] == receipt['result']['output']
        compat = LegacyGroupCompatibility(service, root)
        changed = {**raw, 'name': 'New GET name'}
        edited = compat.put_action(changed, None, 'edit-query', actor=USER)
        assert edited['result']['actionRef']['id'] == ref['id']
        assert compat.projection(old.read())['actions']['query']['execution'] == raw['execution']
        assert Groups(service).release(built['version'], actor=USER)['tools'][0]['actionRef'] == ref
        assert old.path.read_bytes() == before and len(calls) == 1
        assert service._pending_outbox_snapshot() == []
        with service.transaction(readonly=True) as db:
            assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_P6_API_02_descriptor_and_argument_override_rejected(action_http, tmp_path):
    _, service = action_http
    raw = {'id': 'read', 'name': 'GET', 'enabled': True, 'effect': 'read',
           'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
           'execution': {'kind': 'api', 'method': 'GET', 'path': '/api/models'}}
    for execution in ({'kind': 'api', 'method': 'POST', 'path': '/api/models'},
                      {'kind': 'api', 'method': 'GET', 'path': '/api/secrets'}):
        with pytest.raises(ValueError):
            command_definition({**raw, 'execution': execution})
    ref = object_ref('action', service.publish_action('get', command_definition(raw)))
    own(service)
    with pytest.raises(fc.ContractError, match='input schema'):
        asyncio.run(service.invoke_action(ref['id'], {'actionRef': ref, 'arguments': {'path': '/api/secrets'},
                                                     'requestKey': 'cannot-override'}, actor=USER))


def test_P6_SESSION_OPERATION_01_crud_selection_checkpoint_cold_no_replay(action_http, tmp_path, monkeypatch):
    _, service, old, _, _, compat = adopted(action_http, tmp_path, monkeypatch)
    calls = []
    async def pause(sid, body):
        calls.append((sid, body))
        return {'ok': sid != 'missing', 'queueRevision': 7, 'paused': True}
    service.session_pause = pause
    raw = {'id': 'pause', 'name': 'Pause selected', 'enabled': False, 'effect': 'write_local',
           'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
           'execution': {'kind': 'session_operation', 'operation': 'pause_everything',
                         'selector': {'mode': 'explicit', 'ids': ['first', 'missing', 'first']}}}
    before = old.path.read_bytes()
    compat.put_action(raw, None, 'pause-create', actor=USER)
    enabled = compat.lifecycle_action('pause', True, compat.projection(old.read())['revision'], 'pause-enable', actor=USER)
    ref = enabled['result']['actionRef']
    body = {'actionRef': ref, 'arguments': {}, 'requestKey': 'pause-once'}
    result = asyncio.run(service.invoke_action(ref['id'], body, actor=USER))
    assert result['result']['status'] == 'failed'
    assert result['result']['output'] == {'ok': False, 'matched': 2, 'succeeded': 1, 'failed': 1,
        'targets': [{'sessionId': 'first', 'ok': True, 'queueRevision': 7, 'paused': True},
                    {'sessionId': 'missing', 'ok': False, 'queueRevision': 7, 'paused': True}]}
    assert result['result']['checkpoint']['targetIds'] == ['first', 'missing']
    assert len(calls) == 2
    cold = FinalService(service.root, service.auth)
    replay = asyncio.run(cold.invoke_action(ref['id'], body, actor=USER))
    assert replay['operationId'] == result['operationId'] and replay['replayed']
    assert old.path.read_bytes() == before and len(calls) == 2


def test_P6_SESSION_OPERATION_02_owner_loss_stops_next_target_no_replay(action_http, tmp_path, monkeypatch):
    _, service, old, _, _, compat = adopted(action_http, tmp_path, monkeypatch)
    calls = []
    original = service.owner.token
    async def pause(sid, body):
        calls.append(sid)
        service.owner.token = None
        return {'ok': True}
    service.session_pause = pause
    raw = {'id': 'pause', 'name': 'Pause selected', 'enabled': True, 'effect': 'write_local',
           'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
           'execution': {'kind': 'session_operation', 'operation': 'pause_everything',
                         'selector': {'mode': 'explicit', 'ids': ['first', 'second']}}}
    ref = compat.put_action(raw, None, 'pause-create', actor=USER)['result']['actionRef']
    body = {'actionRef': ref, 'arguments': {}, 'requestKey': 'pause-owner-lost'}
    try:
        with pytest.raises(fc.ContractError):
            asyncio.run(service.invoke_action(ref['id'], body, actor=USER))
        cold = FinalService(service.root, service.auth)
        receipt = asyncio.run(cold.invoke_action(ref['id'], body, actor=USER))
        assert receipt['replayed'] and receipt['result']['status'] == 'uncertain'
        assert receipt['result']['checkpoint']['targetIds'] == ['first', 'second']
        assert calls == ['first']
    finally:
        service.owner.token = original


def test_P6_RECONCILE_01_original_identity_after_revision_and_lifecycle_change(action_http, tmp_path, monkeypatch):
    _, service, old, built, original, compat = adopted(action_http, tmp_path, monkeypatch)
    before = old.path.read_bytes()
    with service.transaction() as db:
        db.execute("DELETE FROM final_documents WHERE kind='legacy_action_binding'")
        Groups.save(db, 'action_group_registry', compat.root, {'revision': 3})
    value = command_definition(old.read()['actions']['echo'])
    value['name'] = 'Canonical updated'
    head = service.write('action', original['id'], {'definition': value, 'expectedRevision': original['revision'],
                                                  'requestKey': 'original-edit'}, actor=USER)['result']['object']
    edited = service.control('action', original['id'], {'operation': 'publish', 'expectedRevision': head['revision'],
        'expectedStateRevision': head['stateRevision'], 'parameters': {}, 'requestKey': 'original-publish'}, actor=USER)
    head = edited['result']['object']
    disabled = service.control('action', original['id'], {'operation': 'disable', 'expectedRevision': head['revision'],
        'expectedStateRevision': head['stateRevision'], 'parameters': {}, 'requestKey': 'original-disable'}, actor=USER)
    with pytest.raises(fc.ContractError, match='reconcile'):
        compat.put_action(old.read()['actions']['echo'], None, 'cannot-guess', actor=USER)
    plan = compat.reconciliation_preview(actor=USER)
    assert plan['items'][0]['originalRef'] == original
    assert plan['items'][0]['receiptIds'] == [disabled['operationId']]
    assert old.path.read_bytes() == before
    receipt = compat.reconcile(plan, 'reconcile-original', actor=USER)
    cold = FinalService(service.root, service.auth)
    replay = LegacyGroupCompatibility(cold, old.root).reconcile(plan, 'reconcile-original', actor=USER)
    assert replay['replayed'] and replay['operationId'] == receipt['operationId']
    view = compat.projection(old.read())
    assert view['actions']['echo']['enabled'] is False
    assert view['actions']['echo']['name'] == 'Canonical updated'
    assert Groups(service).release(built['version'], actor=USER)['tools'][0]['actionRef'] == original
    assert old.path.read_bytes() == before and not (tmp_path / 'effects').exists()


def test_P6_RECONCILE_02_stale_plan_and_missing_receipt_preserve(action_http, tmp_path, monkeypatch):
    _, service, old, _, ref, compat = adopted(action_http, tmp_path, monkeypatch)
    with service.transaction() as db:
        db.execute("DELETE FROM final_documents WHERE kind='legacy_action_binding'")
        Groups.save(db, 'action_group_registry', compat.root, {'revision': 3})
    plan = compat.reconciliation_preview(actor=USER)
    head = service.get('action', ref['id'], actor=USER)
    changed = service.control('action', ref['id'], {'operation': 'disable', 'expectedRevision': head['revision'],
        'expectedStateRevision': head['stateRevision'], 'parameters': {}, 'requestKey': 'disable-after-plan'}, actor=USER)
    with pytest.raises(fc.ContractError, match='plan changed'):
        compat.reconcile(plan, 'stale-reconcile', actor=USER)
    with service.transaction() as db:
        db.execute('DELETE FROM final_operations WHERE id=?', (changed['operationId'],))
    with pytest.raises(fc.ContractError, match='no matching'):
        compat.reconciliation_preview(actor=USER)
    with service.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM final_documents WHERE kind='legacy_action_binding'").fetchone()[0] == 0


@pytest.mark.parametrize('kind', ['session-message', 'session-broadcast', 'scheduled-task'])
def test_P6_SYSTEM_01_original_union_provider_receipt_and_cold_no_replay(action_http, tmp_path, kind):
    from packages.actions.store import system_definition
    _, service = action_http
    calls = []
    class Provider:
        async def send_session(self, sid, text, **kw):
            calls.append((sid, text, kw))
            return {'status': 'queued', 'queueItemId': 'original-' + sid, 'consumption': 'unconfirmed'}
        async def assign(self, sid, text, **kw):
            calls.append((sid, text, kw))
            return {'status': 'queued', 'taskId': kw['task_id'], 'queueItemId': 'original-' + sid}
    service.executor.session_provider = Provider()
    service.executor.assign_provider = Provider()
    ref = object_ref('action', service.publish_action('legacy_' + kind, command_definition(system_definition(kind))))
    own(service)
    args = ({'sessionIds': ['first', 'second', 'first'], 'text': '雪'} if kind == 'session-broadcast' else
            {'sessionId': 'first', 'text': '雪', **({'action': {'api': 'assign'}} if kind == 'scheduled-task' else {})})
    body = {'actionRef': ref, 'arguments': args, 'requestKey': 'system-original'}
    result = asyncio.run(service.invoke_action(ref['id'], body, actor=USER))
    assert result['result']['status'] == 'completed'
    assert all(row[2]['effect_key']['operationId'] == result['operationId'] for row in calls)
    assert len(calls) == (2 if kind == 'session-broadcast' else 1)
    if kind == 'session-broadcast':
        assert len(result['result']['providerReceipts']) == 2
        assert result['result']['output']['targets'][0]['receipt']['queueItemId'] == 'original-first'
    else:
        assert result['result']['output']['queueItemId'] == 'original-first'
    cold = FinalService(service.root, service.auth)
    replay = asyncio.run(cold.invoke_action(ref['id'], body, actor=USER))
    assert replay['replayed'] and replay['operationId'] == result['operationId']
    with service.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0


def test_P6_CHANNEL_01_scheduled_union_original_provider_unknown_no_replay(action_http):
    from packages.actions.store import system_definition
    _, service = action_http
    calls = []
    class Channel:
        async def send(self, target_type, target_id, text, **kw):
            calls.append((target_type, target_id, text, kw))
            return {'status': 'unknown', 'message_id': '', 'originalError': 'lost-response'}
    service.executor.channel_provider = Channel()
    ref = object_ref('action', service.publish_action('legacy_schedule', command_definition(system_definition('scheduled-task'))))
    own(service)
    body = {'actionRef': ref, 'arguments': {'sessionId': None, 'text': 'once',
            'action': {'api': 'send_qq', 'args': {'targetType': 'user', 'targetId': '12345'}}}, 'requestKey': 'channel-once'}
    result = asyncio.run(service.invoke_action(ref['id'], body, actor=USER))
    assert result['result']['status'] == 'uncertain'
    assert result['result']['output']['originalError'] == 'lost-response'
    cold = FinalService(service.root, service.auth)
    replay = asyncio.run(cold.invoke_action(ref['id'], body, actor=USER))
    assert replay['replayed'] and replay['operationId'] == result['operationId'] and len(calls) == 1


def test_P6_SYSTEM_02_broadcast_revocation_fences_each_target(action_http):
    from packages.actions.store import system_definition
    from tests.test_authorization_mcp import grants
    _, service = action_http
    calls = []
    class Provider:
        async def send_session(self, sid, text, **kw):
            calls.append(sid)
            service.auth.change_permissions(grants(action={'invoke': False}), 1)
            return {'status': 'queued', 'queueItemId': 'first-accepted'}
    service.executor.session_provider = Provider()
    ref = object_ref('action', service.publish_action('legacy_broadcast', command_definition(system_definition('session-broadcast'))))
    own(service)
    service.auth.change_permissions(grants(), 0)
    actor = {'type': 'agent', 'id': 'agent', 'revision': 1}
    body = {'actionRef': ref, 'arguments': {'sessionIds': ['first', 'second'], 'text': 'once'}, 'requestKey': 'fenced-broadcast'}
    result = asyncio.run(service.invoke_action(ref['id'], body, actor=actor))
    assert calls == ['first']
    assert result['result']['outcome'] == 'partial'
    assert result['result']['providerReceipts'][0]['receipt']['queueItemId'] == 'first-accepted'
    replay = asyncio.run(service.invoke_action(ref['id'], body, actor=actor))
    assert replay['operationId'] == result['operationId'] and replay['replayed']


def test_P6_SYSTEM_03_standalone_adoption_old_http_lifecycle_original_grants(action_http, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from packages.actions.api import install
    from packages.actions.store import system_definition
    from tests.test_action_unification_p6 import source
    from tests.test_authorization_mcp import grants
    client, service = action_http
    old, built, maps, _ = source(tmp_path, service, monkeypatch)
    raw = system_definition('session-message')
    old.mutate(lambda state: state['actions'].update({raw['id']: raw}))
    ref = object_ref('action', service.publish_action('mapped_system_message', command_definition(raw)))
    maps.append({'groupId': None, 'version': 'draft', 'actionId': raw['id'],
                 'definitionDigest': fc.digest(raw), 'actionRef': ref})
    own(service)
    migration = LegacyGroupMigration(service, old.root)
    plan = migration.preview(maps, actor=USER)
    assert plan['blocked'] == []
    migration.apply(plan, 'system-source-adopt', actor=USER)
    before = old.path.read_bytes()
    install(SimpleNamespace(app=client.app, sess=SimpleNamespace(list_all=lambda **kw: [])))
    compat = LegacyGroupCompatibility(service, old.root)
    revision = compat.projection(old.read())['revision']
    response = client.post('/api/actions/actions/system_session_message/lifecycle', json={
        'enabled': False, 'expectedRevision': revision}, headers={'x-pan-request-key': 'system-disable-once'})
    assert response.status_code == 200, response.text
    assert next(a for a in response.json()['actions'] if a['id'] == raw['id'])['enabled'] is False
    service.auth.change_permissions(grants(action={'manageSystemActions': False}), 0)
    actor = {'type': 'agent', 'id': 'agent', 'revision': 1}
    with pytest.raises(fc.ContractError, match='denied'):
        compat.lifecycle_action(raw['id'], True, compat.projection(old.read())['revision'], 'cannot-enable-system', actor=actor)
    assert old.path.read_bytes() == before


@pytest.mark.parametrize('api', ['send_session', 'resume_legal_running', 'shell'])
def test_P6_SYSTEM_04_remaining_scheduled_union_freeze_no_replay(action_http, tmp_path, monkeypatch, api):
    import sys
    from packages.actions import session_resume
    from packages.actions.store import system_definition
    _, service = action_http
    calls = []
    class Provider:
        async def send_session(self, sid, text, **kw):
            calls.append(sid)
            return {'status': 'queued', 'queueItemId': sid}
        async def wake_session(self, sid, **kw):
            calls.append(sid)
            return {'status': 'queued', 'queueItemId': sid}
    service.executor.session_provider = Provider()
    monkeypatch.setattr(session_resume, 'select', lambda: ['frozen-first', 'frozen-second'])
    ref = object_ref('action', service.publish_action('remaining_union', command_definition(system_definition('scheduled-task'))))
    own(service)
    action = {'api': api}
    if api == 'shell':
        script = tmp_path / 'shell.py'
        script.write_text('from pathlib import Path\np=Path(' + repr(str(tmp_path / 'shell-count')) + ')\np.write_text(p.read_text()+"x" if p.exists() else "x")\nprint("done")\n', 'utf-8')
        action['args'] = {'command': '"' + sys.executable + '" "' + str(script) + '"', 'cwd': str(tmp_path), 'hideWindow': True}
    body = {'actionRef': ref, 'arguments': {'sessionId': 'direct-target', 'text': 'once', 'action': action}, 'requestKey': 'remaining-once'}
    result = asyncio.run(service.invoke_action(ref['id'], body, actor=USER))
    assert result['result']['status'] == 'completed'
    monkeypatch.setattr(session_resume, 'select', lambda: ['changed-after-call'])
    cold = FinalService(service.root, service.auth)
    replay = asyncio.run(cold.invoke_action(ref['id'], body, actor=USER))
    assert replay['replayed'] and replay['operationId'] == result['operationId']
    if api == 'shell':
        assert (tmp_path / 'shell-count').read_text() == 'x'
        assert result['result']['commands'][0]['cleanupProof']['confirmed'] is True
    else:
        assert calls == (['frozen-first', 'frozen-second'] if api == 'resume_legal_running' else ['direct-target'])


@pytest.mark.parametrize('output_format', ['text', 'json'])
def test_P6_MULTI_01_explicit_serial_stdout_merge_and_original_pin(action_http, tmp_path, monkeypatch, output_format):
    import sys
    from tests.test_action_unification_p3 import apply_body
    _, service, old, built, ref, compat = adopted(action_http, tmp_path, monkeypatch)
    value = command_definition(old.read()['actions']['echo'])
    chunks = ['first\n', 'second\n'] if output_format == 'text' else ['{"value":', '"雪"}']
    scripts = []
    for index, chunk in enumerate(chunks):
        script = tmp_path / ('multi-' + str(index) + '.py')
        script.write_text('import sys\nsys.stdout.buffer.write(' + repr(chunk.encode('utf-8')) + ')\n', 'utf-8')
        scripts.append(script)
    value['execution']['config'] = {'cwd': str(tmp_path), 'legacyOutputMerge': 'concat/v1',
        'commands': [{'argv': [sys.executable, str(script)]} for script in scripts]}
    head = service.write('action', ref['id'], {'definition': value, 'expectedRevision': ref['revision'],
                                             'requestKey': 'multi-edit'}, actor=USER)['result']['object']
    service.control('action', ref['id'], {'operation': 'publish', 'expectedRevision': head['revision'],
        'expectedStateRevision': head['stateRevision'], 'parameters': {}, 'requestKey': 'multi-publish'}, actor=USER)
    groups = Groups(service)
    # Explicit old output format mapping remains owned by the Group config.
    with service.transaction() as db:
        group = groups.get(db, 'tools')
        group['compatibility']['outputFormats']['echo'] = output_format
        Groups.save(db, 'action_group', 'tools', group)
    preview = groups.preview({'scope': 'action', 'actionRef': service.action_ref(ref['id'], actor=USER)}, actor=USER)
    assert not preview['items'][0]['blockers']
    groups.apply(apply_body(preview, 'multi-upgrade'), actor=USER)
    group = groups.list(actor=USER)['items'][0]
    released = groups.publish('tools', group['revision'], 'multi-build', actor=USER)['result']['release']
    result = asyncio.run(groups.invoke(released['version'], 'echo', {'value': 'input'}, 'multi-call', actor=USER))
    reply = legacy_result(released, 'echo', result)
    assert reply['isError'] is False and len(result['result']['commands']) == 2
    assert reply['content'][0]['text'] == (''.join(chunks) if output_format == 'text' else json.dumps({'value': '雪'}, ensure_ascii=False))
    cold = FinalService(service.root, service.auth)
    assert asyncio.run(Groups(cold).invoke(released['version'], 'echo', {'value': 'input'}, 'multi-call', actor=USER))['operationId'] == result['operationId']
    assert groups.release(built['version'], actor=USER)['tools'][0]['actionRef'] == ref
    if output_format == 'json':
        rebuilt = compat.mutate('tools', 'build', compat.projection(old.read())['revision'], 'old-entry-multi-build', actor=USER)
        assert rebuilt['result']['release']['tools'][0]['outputMerge'] == 'concat/v1'
    client, _ = action_http
    response = client.get('/api/actions')
    assert response.status_code == 200
    mapped = response.json()['canonicalActions']['echo']
    assert mapped['object']['id'] == ref['id']
    assert mapped['definition'] == value
    assert len(mapped['definition']['execution']['config']['commands']) == 2
    client.headers.clear()
    assert 'canonicalActions' not in client.get('/api/actions').json()


def test_P6_TEMPLATE_01_explicit_successor_create_original_fields_cold_no_replay(action_http, tmp_path, monkeypatch):
    from tests.test_job_final_contracts import job_definition, template_definition
    _, service, old, built, command_ref, compat = adopted(action_http, tmp_path, monkeypatch)
    job = job_definition(inputSchema={'type': 'object', 'properties': {}, 'additionalProperties': False}, arguments={},
        steps=[{'stepId': 'main', 'actionRef': command_ref, 'arguments': {'value': {'from': 'literal', 'value': 'hello'}},
                'completion': {'contract': 'process-exit'}}])
    template = service.write('template', None, {'definition': template_definition(jobDefinition=job,
        composition={'creators': [], 'triggers': []}), 'expectedRevision': 0, 'requestKey': 'template-create'}, actor=USER)['result']['object']
    template = service.control('template', template['id'], {'operation': 'publish', 'expectedRevision': template['revision'],
        'expectedStateRevision': template['stateRevision'], 'parameters': {}, 'requestKey': 'template-publish'}, actor=USER)['result']['object']
    template_ref = object_ref('template', template)
    raw = {'id': 'create_job', 'name': 'Fixed template', 'enabled': True, 'effect': 'write_local',
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'execution': {'kind': 'job_template', 'templateId': 'jtpl_original', 'templateRevision': 4}}
    raw = store.validate_action(raw)
    ref = object_ref('action', service.publish_action('fixed_template', command_definition(raw, template_ref=template_ref)))
    before = old.path.read_bytes()
    body = {'actionRef': ref, 'arguments': {}, 'requestKey': 'template-once'}
    result = asyncio.run(service.invoke_action(ref['id'], body, actor=USER))
    output = result['result']['output']
    assert output == {'ok': True, 'jobId': output['jobId'], 'created': True, 'jobCompleted': False,
                      'templateId': 'jtpl_original', 'templateRevision': 4}
    cold = FinalService(service.root, service.auth)
    assert asyncio.run(cold.invoke_action(ref['id'], body, actor=USER))['operationId'] == result['operationId']
    from packages.actions.legacy_families import template_view
    assert template_view(command_definition(raw, template_ref=template_ref), raw, True) == raw
    from packages.jobs.action_templates import ReferenceError as TemplateReferenceError
    with pytest.raises(TemplateReferenceError, match='Missing or changed fixed Job Template'):
        compat.put_action(raw, None, 'unmapped-template', actor=USER)
    with service.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 1
    assert old.path.read_bytes() == before and not (tmp_path / 'effects').exists()


@pytest.mark.parametrize('mode,view', [('standalone', 'standalone'), ('pan_native', 'native'), ('gateway', 'gateway')])
def test_P6_API_03_adopted_flat_real_stdio_original_shape_and_key(tmp_path, monkeypatch, mode, view):
    import os
    import sys
    from pathlib import Path
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from tests.test_authorization_mcp import harness, close, grants, result
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'path': self.path}).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('PAN_API_URL', f'http://127.0.0.1:{server.server_port}')
    async def exercise():
        h = harness(tmp_path)
        try:
            root = tmp_path / 'old-api'
            enroll_fresh_source(root)
            monkeypatch.setattr(store, 'root', lambda: root)
            monkeypatch.setattr(group_catalog, 'canonical_path', lambda: h.service.creators.path)
            old = store.Store(root)
            old.put('actions', {'id': 'query', 'name': 'GET', 'enabled': True, 'effect': 'read',
                'input_schema': {'type': 'object', 'properties': {'requestKey': {'type': 'string'}}, 'additionalProperties': False},
                'execution': {'kind': 'api', 'method': 'GET', 'path': '/api/models'}})
            old.put('groups', {'id': 'api', 'name': 'API', 'action_ids': ['query']})
            built = old.build('api')
            raw = old.read()['actions']['query']
            ref = object_ref('action', h.service.publish_action('old_api', command_definition(raw)))
            maps = [{'groupId': 'api', 'version': v, 'actionId': 'query', 'definitionDigest': fc.digest(raw), 'actionRef': ref}
                    for v in ('draft', built['version'])]
            migration = LegacyGroupMigration(h.service, root)
            migration.apply(migration.preview(maps, actor=USER), 'api-stdio-adopt', actor=USER)
            before = old.path.read_bytes()
            h.svc.auth.change_permissions(grants(), 0)
            h.session.adapter_config['plugin_bindings'].append({'plugin_id': built['plugin_id'], 'enabled': True, 'mode': mode})
            params = StdioServerParameters(command=sys.executable, args=['-m', 'packages.plugins.mcp', '--view', view,
                *(['--plugin', built['plugin_id']] if view == 'standalone' else [])], cwd=str(Path.cwd()),
                env={**os.environ, 'PAN_PLUGIN_IPC': h.bridge.address, 'PAN_AGENT_SESSION_ID': h.session.id})
            async with stdio_client(params) as streams, ClientSession(*streams) as client:
                await client.initialize()
                name = (result(await client.call_tool('api_discover', {'include_schema': True}))['tools'][0]['name']
                        if view == 'gateway' else (await client.list_tools()).tools[0].name)
                async def call():
                    return await client.call_tool('api_call' if view == 'gateway' else name,
                        {'name': name, 'arguments': {'requestKey': 'business'}} if view == 'gateway' else {'requestKey': 'business'},
                        meta={'panRequestKey': 'api-outer-once'})
                first, repeat = await call(), await call()
                assert not first.isError and first.structuredContent == {'path': '/api/models?requestKey=business'}
                assert first.meta['panOperation']['operationId'] == repeat.meta['panOperation']['operationId']
                assert first.meta['panOperation']['groupRelease'] == built['version']
                assert calls == ['/api/models?requestKey=business'] and old.path.read_bytes() == before
        finally:
            await close(h)
    try:
        asyncio.run(exercise())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
