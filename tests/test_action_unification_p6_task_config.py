"""Legacy configuration and synchronous return contracts over one canonical host."""
import asyncio
import copy
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
import httpx

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService
from packages.scheduler import api, canonical_tasks
from packages.scheduler.json_handoff import JsonTaskHandoff, project, resolve, control, freeze_dispatch
from tests.test_action_unification_p6_json_scheduler import json_task, USER
from tests.test_job_final_clients import tcp
from tests.test_action_unification_p6_json_receipts import shell_task, finish


@pytest.fixture
def h(json_task):
    service, source, mapping, root = json_task
    # Exercise real durable queue admission without depending on a globally
    # installed CLI or starting an unowned worker on the test machine.
    from packages.core import session as sessions
    target = sessions.get(source['targetSessionId'])
    target.queue_paused = True
    sessions.save(target)
    port = JsonTaskHandoff(service)
    port.apply(port.preview([mapping], actor=USER), 'config-original-adopt', actor=USER)
    app = FastAPI()
    app.state.job_final_service = service
    app.state.automation = SimpleNamespace(auth=service.auth)
    if os.environ.get('P6_CONFIG_BASELINE') == '1':
        import subprocess, types
        code = subprocess.check_output(['git', 'show', '1118134c:packages/scheduler/api.py'])
        original = types.ModuleType('packages.scheduler.config_original')
        original.__package__ = 'packages.scheduler'
        exec(compile(code, '<1118134c scheduler routes>', 'exec'), original.__dict__)
        app.include_router(original.router)
    else:
        app.include_router(api.router)
    return SimpleNamespace(app=app, service=service, source=source, root=root,
        mapping=mapping, token=service.auth.login(service.auth.key()))


def client(h, tcp):
    return httpx.Client(base_url=tcp, trust_env=False, headers={'Authorization': 'Bearer ' + h.token})


def test_P6_CONFIG_01_actual_http_create_patch_cold_cursor_count_no_legacy_write(h, tcp):
    original = (h.root / h.mapping['sourceFile']).read_bytes()
    payload = {'targetSessionId': h.source['targetSessionId'], 'text': 'created task body',
        'schedule': {'kind': 'interval', 'intervalSec': 60, 'anchor': '2030-01-01T00:00:00', 'timezone': 'Asia/Shanghai'},
        'name': 'Created through old HTTP', 'requestKey': 'old-create-once'}
    with client(h, tcp) as c:
        first = c.post('/api/scheduler/tasks', json=payload)
        assert first.status_code == 200, first.text
        value = first.json()
        assert value['ok'], value
        tid = value['task']['id']
        assert tid != h.source['taskId'] and value['task']['text'] == payload['text']
        assert c.post('/api/scheduler/tasks', json=payload).json()['task']['id'] == tid
        assert c.get('/api/scheduler/tasks/' + tid).json()['task']['id'] == tid
        assert {r['id'] for r in c.get('/api/scheduler/tasks').json()['tasks']} == {tid, h.source['taskId']}
        cursor = value['task']['nextFireAt']
        changed = c.patch('/api/scheduler/tasks/' + tid, json={'text': 'edited before dispatch', 'description': 'full description',
            'max_runs': 6, 'paused': True, 'misfire_policy': 'skip', 'expectedRevision': 1, 'requestKey': 'edit-once'})
        assert changed.status_code == 200, changed.text
        task = changed.json()['task']
        assert task['text'] == 'edited before dispatch' and task['description'] == 'full description'
        assert task['paused'] and task['maxRuns'] == 6 and task['misfirePolicy'] == 'skip'
        assert task['nextFireAt'] == cursor
        stale = c.patch('/api/scheduler/tasks/' + tid, json={'name': 'stale', 'expectedRevision': 1, 'requestKey': 'stale-edit'})
        assert stale.status_code == 409
        ran = c.post('/api/scheduler/tasks/' + tid + '/run-now', json={'requestKey': 'new-task-sync'}).json()
        assert ran['run']['status'] == 'dispatched', ran
        assert ran['run']['run']['result']['status'] == 'queued', ran
        from packages.core import session as sessions
        queued = sessions.get(h.source['targetSessionId']).queue_pending
        assert len(queued) == 1 and queued[0]['text'] == 'edited before dispatch'
        rid = ran['operation']['runId']
        again = c.post('/api/scheduler/tasks/' + tid + '/run-now', json={'requestKey': 'new-task-sync'}).json()
        assert again['operation']['runId'] == rid
        assert c.get('/api/scheduler/tasks/' + tid + '/runs').json()['runs'][0]['runId'] == rid
        assert c.get('/api/scheduler/tasks/' + tid).json()['task']['runCount'] == 1
    cold = FinalService(h.service.root, h.service.auth)
    try:
        assert project(cold, tid, actor=USER)['task']['text'] == 'edited before dispatch'
        assert cold.get_run(rid, actor=USER)['state'] == 'completed'
    finally:
        asyncio.run(cold.close())
    assert (h.root / h.mapping['sourceFile']).read_bytes() == original
    assert len(list((h.root / 'background_jobs' / 'jobs').glob('*.json'))) == 1


def test_P6_CONFIG_02_adopted_edit_atomic_replay_disable_schedule_and_pin(h):
    service, tid = h.service, h.source['taskId']
    original = (h.root / h.mapping['sourceFile']).read_bytes()
    admitted = control(service, tid, 'run-now', actor=USER, request_key='frozen-before-edit')
    before = service.final.store.get(h.source['jobId'])['finalRuntime']['plans'][admitted['runId']]['frozen']
    body = {'text': 'future body', 'schedule': {'kind': 'interval', 'intervalSec': 120,
        'anchor': '2030-01-01T00:00:00', 'timezone': 'Asia/Shanghai'}, 'enabled': False, 'max_runs': None}
    first = canonical_tasks.update(service, tid, body, actor=USER, request_key='atomic-config', expected_revision=2)
    assert canonical_tasks.update(service, tid, body, actor=USER, request_key='atomic-config', expected_revision=2)['replayed']
    assert project(service, tid, actor=USER)['task']['enabled'] is False
    assert project(service, tid, actor=USER)['task']['nextFireAt'] is None
    after = service.final.store.get(h.source['jobId'])['finalRuntime']['plans'][admitted['runId']]['frozen']
    assert after == before
    enabled = canonical_tasks.update(service, tid, {'enabled': True}, actor=USER, request_key='enable-clock')
    current = project(service, tid, actor=USER)['task']
    assert current['enabled'] and current['schedule']['intervalSec'] == 120 and current['nextFireAt']
    assert (h.root / h.mapping['sourceFile']).read_bytes() == original


def test_P6_CONFIG_03_publication_and_owner_fence_rollback(h):
    service, tid = h.service, h.source['taskId']
    with service.transaction() as db:
        _, binding = resolve(service, db, tid)
        creator = service.creators.get(binding['creatorId'])
        creator['pendingPublication'] = {'original': 'unknown'}
        service.creators._write(db, creator)
    before = service.final.store.get(h.source['jobId'])
    with pytest.raises(fc.ContractError, match='publication'):
        canonical_tasks.update(service, tid, {'text': 'must not commit'}, actor=USER, request_key='blocked-config')
    assert service.final.store.get(h.source['jobId']) == before
    old = service.owner.token
    service.owner.token = (old[0], 'stale-nonce')
    try:
        with pytest.raises(Exception, match='fence'):
            canonical_tasks.update(service, tid, {'text': 'stale'}, actor=USER, request_key='stale-owner')
    finally:
        service.owner.token = old


def test_P6_CONFIG_04_old_relative_cwd_normalizes_omitted_is_validation_error(tmp_path, monkeypatch):
    from packages.core.background_jobs import validate_shell_action
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'relative').mkdir()
    assert validate_shell_action('echo unchanged', 'relative') == ('echo unchanged', tmp_path / 'relative')
    with pytest.raises(ValueError, match='cwd is required'):
        validate_shell_action('echo unchanged', None)


@pytest.mark.parametrize('projection', ['core', 'native', 'gateway'])
def test_P6_CONFIG_05_real_standalone_stdio_create_update_default_sync(h, tmp_path, projection):
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from packages.automation.authorization import bind_objects
    from packages.core.manifest_loader import ManifestConfig, McpServer
    from packages.core.session import Session
    from packages.plugins.ipc import Bridge
    from packages.plugins.runtime import Runtime
    from tests.test_job_final_clients import unpack, ROOT
    permissions = h.service.auth._default_permissions()
    for row in permissions.values():
        for key in row: row[key] = True
    h.service.auth.change_permissions(permissions, h.service.auth.permissions_status()['revision'])
    session = Session(id=h.source['targetSessionId'], name='Owned config caller', adapter_config={
        'plugin_bindings': [{'plugin_id': 'pan', 'mode': 'standalone', 'enabled': True}]})
    manifest = ManifestConfig(mcp_servers=[McpServer('pan', command=sys.executable, args=['-m', 'packages.mcp.server'])])
    runtime = Runtime(lambda: manifest, lambda sid: session if sid == session.id else None, data_root=tmp_path / 'plugins')
    bind_objects(runtime, h.app)
    async def scenario():
        bridge = Bridge(runtime, tmp_path)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PAN_')}
        env.update(PAN_PLUGIN_IPC=bridge.address, PAN_AGENT_SESSION_ID=session.id, PAN_API_URL='http://127.0.0.1:1', NO_PROXY='127.0.0.1,localhost')
        try:
            params = StdioServerParameters(command=sys.executable, args=(['-m', 'packages.mcp.server'] if projection == 'core' else
                ['-m', 'packages.plugins.mcp', '--view', projection, '--core']), cwd=str(ROOT), env=env)
            async with stdio_client(params) as streams, ClientSession(*streams) as c:
                await c.initialize()
                async def call_tool(name, arguments):
                    if projection == 'gateway':
                        return await c.call_tool('api_call', {'name': 'pan.' + name, 'arguments': arguments})
                    return await c.call_tool(name, arguments)
                if projection == 'gateway':
                    schema = unpack(await c.call_tool('api_discover', {'plugin': 'pan',
                        'name': 'pan.scheduler_update', 'include_schema': True}))
                    assert schema['tools'][0]['name'] == 'pan.scheduler_update', schema
                args = {'target_session_id': session.id, 'text': 'stdio original', 'kind': 'interval',
                    'interval_sec': 60, 'request_key': 'stdio-create-original'}
                created = unpack(await call_tool('scheduler_create', args))
                assert created['ok'], created
                tid = created['task']['id']
                assert unpack(await call_tool('scheduler_create', args))['task']['id'] == tid
                updated = unpack(await call_tool('scheduler_update', {'task_id': tid, 'text': 'stdio edited',
                    'description': 'stdio description', 'request_key': 'stdio-edit-original'}))
                assert updated['task']['text'] == 'stdio edited', updated
                ran = unpack(await call_tool('scheduler_run_now', {'task_id': tid, 'request_key': 'stdio-sync-original'}))
                assert ran['run']['status'] == 'dispatched', ran
                from packages.core import session as sessions
                queued = sessions.get(session.id).queue_pending
                assert len(queued) == 1 and queued[0]['text'] == 'stdio edited'
                assert unpack(await call_tool('scheduler_runs', {'task_id': tid}))['total'] == 1
                multi = unpack(await call_tool('scheduler_create', {
                    'target_session_id': session.id, 'text': 'stdio multi', 'kind': 'interval',
                    'schedule_entries': [
                        {'kind': 'interval', 'intervalSec': 60, 'anchor': '2030-01-01T00:00:00',
                         'misfirePolicy': 'fire_now', 'graceSec': 1},
                        {'kind': 'interval', 'intervalSec': 120, 'anchor': '2030-01-01T00:00:00',
                         'misfirePolicy': 'skip', 'graceSec': 7},
                    ], 'request_key': 'stdio-multi-create'}))
                assert multi['ok'] and len(multi['task']['schedule']) == 2, multi
                assert [(e['misfirePolicy'], e['graceSec']) for e in multi['task']['schedule']] == [('fire_now', 1), ('skip', 7)]
                entry = multi['task']['schedule'][1]
                edited = unpack(await call_tool('scheduler_update', {'task_id': multi['task']['id'],
                    'entry_id': entry['id'], 'expected_entry_revision': 1,
                    'entry_paused': True, 'request_key': 'stdio-multi-pause'}))
                assert edited['ok'] and next(item for item in edited['task']['schedule']
                    if item['id'] == entry['id'])['nextFireAt'] is None, edited
                mtid = multi['task']['id']
                bulk = unpack(await call_tool('scheduler_update', {'task_id': mtid,
                    'schedule': [{'kind': 'interval', 'intervalSec': 180}, {'kind': 'interval', 'intervalSec': 240}],
                    'expected_revision': 2, 'request_key': 'stdio-bulk'}))
                assert bulk['ok'], bulk
                inserted = unpack(await call_tool('scheduler_update', {'task_id': mtid,
                    'append_schedule': [{'kind': 'interval', 'intervalSec': 300}], 'insert_index': 1,
                    'expected_revision': 3, 'request_key': 'stdio-insert'}))
                assert inserted['ok'] and len(inserted['task']['schedule']) == 3, inserted
                rows = inserted['task']['schedule']
                mixed = unpack(await call_tool('scheduler_update', {'task_id': mtid,
                    'entry_id': rows[1]['id'], 'expected_entry_revision': 1, 'entry_paused': True,
                    'text': 'stdio mixed', 'expected_revision': 4, 'request_key': 'stdio-mixed'}))
                assert mixed['task']['text'] == 'stdio mixed' and mixed['task']['schedule'][1]['paused'], mixed
                assert unpack(await call_tool('scheduler_update', {'task_id': mtid,
                    'entry_id': rows[1]['id'], 'expected_entry_revision': 1, 'entry_paused': True,
                    'text': 'stdio mixed', 'expected_revision': 4, 'request_key': 'stdio-mixed'}))['operation']['replayed']
                for rev, row in enumerate(rows, start=5):
                    removed = unpack(await call_tool('scheduler_update', {'task_id': mtid,
                        'entry_id': row['id'], 'expected_entry_revision': 2 if row['id'] == rows[1]['id'] else 1,
                        'delete_entry': True, 'expected_revision': rev, 'request_key': 'stdio-del-' + row['id']}))
                    assert removed['ok'], removed
                assert removed['operation']['result']['deleted']
                permissions['job']['updateConfig'] = False
                h.service.auth.change_permissions(permissions, h.service.auth.permissions_status()['revision'])
                denied = unpack(await call_tool('scheduler_update', {'task_id': tid,
                    'text': 'denied', 'request_key': 'stdio-revoked'}))
                assert denied['error']['code'] == 403, denied
                assert denied['error']['message']['code'] == 'authorization_denied', denied
                if projection == 'gateway':
                    changed = await c.call_tool('api_call', {'name': 'pan.scheduler_list',
                        'arguments': {}, 'expected_contract_digest': 'stale'})
                    assert changed.isError
                    assert json.loads(changed.content[0].text)['error']['code'] == 'contract_changed'
                session.adapter_config['plugin_bindings'][0]['enabled'] = False
                unavailable = await call_tool('scheduler_update', {'task_id': tid,
                    'text': 'disabled', 'request_key': 'stdio-disabled'})
                if projection == 'core':
                    assert unpack(unavailable)['error'] == {'code': 403, 'message': 'Pan core disabled'}
                else:
                    assert unavailable.isError
                    assert json.loads(unavailable.content[0].text)['error']['code'] == 'tool_unavailable'
        finally:
            await bridge.close()
    asyncio.run(scenario())


def test_P6_CONFIG_05B_real_http_flat_multi_entry_policy_cursor_cas(h, tcp):
    payload = {'targetSessionId': h.source['targetSessionId'], 'text': 'http multi',
        'schedule': [
            {'kind': 'interval', 'intervalSec': 60, 'anchor': '2030-01-01T00:00:00',
             'misfirePolicy': 'fire_now', 'graceSec': 1},
            {'kind': 'interval', 'intervalSec': 120, 'anchor': '2030-01-01T00:00:00',
             'misfirePolicy': 'skip', 'graceSec': 7},
        ], 'requestKey': 'http-multi-create'}
    with client(h, tcp) as c:
        created = c.post('/api/scheduler/tasks', json=payload)
        assert created.status_code == 200, created.text
        task = created.json()['task']
        assert len(task['schedule']) == 2
        assert [(e['misfirePolicy'], e['graceSec']) for e in task['schedule']] == [('fire_now', 1), ('skip', 7)]
        entry = task['schedule'][1]
        updated = c.patch('/api/scheduler/tasks/' + task['id'], json={
            'entryId': entry['id'], 'expectedEntryRevision': 1, 'entryPaused': True,
            'expectedRevision': 1, 'requestKey': 'http-multi-pause'})
        assert updated.status_code == 200, updated.text
        assert next(item for item in updated.json()['task']['schedule']
                    if item['id'] == entry['id'])['nextFireAt'] is None
        stale = c.patch('/api/scheduler/tasks/' + task['id'], json={
            'entryId': entry['id'], 'expectedEntryRevision': 1, 'entryPaused': False,
            'expectedRevision': 2, 'requestKey': 'http-multi-stale'})
        assert stale.status_code == 409
        body = {'entryId': entry['id'], 'expectedEntryRevision': 2, 'entryPaused': False,
                'entryMisfirePolicy': 'fire_now', 'entryGraceSec': 3,
                'expectedRevision': 2, 'requestKey': 'http-multi-resume'}
        resumed = c.patch('/api/scheduler/tasks/' + task['id'], json=body)
        assert resumed.status_code == 200, resumed.text
        assert c.patch('/api/scheduler/tasks/' + task['id'], json=body).json()['operation']['replayed']
        current = next(e for e in resumed.json()['task']['schedule'] if e['id'] == entry['id'])
        assert current['revision'] == 3 and not current['paused']
        assert current['nextFireAt'] == entry['nextFireAt']
        assert (current['misfirePolicy'], current['graceSec']) == ('fire_now', 3)
        denied = httpx.get(tcp + '/api/scheduler/tasks/' + task['id'], trust_env=False)
        assert denied.status_code in (401, 403)
        removed = c.patch('/api/scheduler/tasks/' + task['id'], json={
            'entryId': entry['id'], 'expectedEntryRevision': 3, 'deleteEntry': True,
            'expectedRevision': 3, 'requestKey': 'http-multi-delete'})
        assert removed.status_code == 200, removed.text
        remaining = removed.json()['task']['schedule'][0]
        # Entry CAS remains routed after a multi-entry task has only one rule left.
        changed = c.patch('/api/scheduler/tasks/' + task['id'], json={
            'entryId': remaining['id'], 'expectedEntryRevision': 1, 'entryPaused': True,
            'expectedRevision': 4, 'requestKey': 'http-last-entry-pause'})
        assert changed.status_code == 200, changed.text
        assert changed.json()['task']['schedule'][0]['revision'] == 2
    cold = FinalService(h.service.root, h.service.auth)
    try:
        value = project(cold, task['id'], actor=USER)['task']
        assert value['schedule'][0]['paused'] and value['nextFireAt'] is None
        assert value['runCount'] == 0
    finally:
        asyncio.run(cold.close())


@pytest.mark.parametrize('schedule', [[], [[]], [{'kind': 'interval', 'intervalSec': 60, 'graceSec': -1}],
    [{'kind': 'interval', 'intervalSec': 60, 'enabled': 'false'}],
    [{'kind': 'interval', 'intervalSec': 60, 'misfirePolicy': 'retry'}]])
def test_P6_CONFIG_05C_invalid_entry_fields_do_not_create(h, tcp, schedule):
    with client(h, tcp) as c:
        before = c.get('/api/scheduler/tasks').json()['tasks']
        result = c.post('/api/scheduler/tasks', json={'targetSessionId': h.source['targetSessionId'],
            'text': 'invalid', 'schedule': schedule, 'requestKey': 'invalid-entry'})
        assert result.json()['ok'] is False, result.text
        assert c.get('/api/scheduler/tasks').json()['tasks'] == before


@pytest.mark.parametrize('active', [False, True])
def test_P6_CONFIG_06_actual_original_process_relative_receipt_and_live_handoff(shell_task, monkeypatch, active):
    from pathlib import Path
    from packages.core.background_jobs import validate_shell_action
    from packages.jobs.final_runtime.ledger import aggregate
    from packages.jobs.independent_executor import effect_id
    from packages.jobs.scheduled_records import append_run
    service, source, mapped_by, root, counter, gate = shell_task
    # Synthetic predecessor resolves relative cwd with the actual old validator
    # before admission. Later migration never uses the current process cwd.
    actual = Path(source['action']['args']['cwd'])
    monkeypatch.chdir(actual.parent)
    source['action']['args']['cwd'] = actual.name
    _, resolved = validate_shell_action(source['action']['args']['command'], source['action']['args']['cwd'])
    normalized = copy.deepcopy(source)
    normalized['action']['args']['cwd'] = str(resolved)
    with service.transaction() as db:
        cfg = JsonTaskHandoff(service)._map(db, normalized, mapped_by, definition_only=True)['job']
        obj = service._envelope('job', cfg, USER, object_id=source['jobId'], state='enabled')
        job = {'jobId': source['jobId'], 'canonical': obj, 'config': cfg, 'configRevision': 1}
        aggregate(job, service.root)['profileId'] = service.profile_id
        service._save(db, 'job', source['jobId'], job)
        admitted = service._admit(db, job, {'expectedRevision': 1, 'input': {}}, USER, 'op_original_relative')
        job = service._get(db, 'job', source['jobId'])
        job['finalRuntime']['plans'][admitted['runId']]['dispatchKey'] = 'original-relative-dispatch'
        service._save(db, 'job', source['jobId'], job)
    if not active:
        gate.write_text('release')
    asyncio.run(service.dispatch_once())
    if not active:
        asyncio.run(finish(service, source['jobId'], admitted['runId']))
    state = service.final.store.get(source['jobId'])['finalRuntime']
    effect = next(iter(state['effects'] or state['effectAdmissions'].values()))
    process_id = effect_id(effect['effectKey'])
    if active:
        import time
        from packages.jobs.independent_executor import ExecutionStore
        deadline = time.monotonic() + 10
        while ExecutionStore(service.root / 'executions').get(process_id)['phase'] != 'running':
            assert time.monotonic() < deadline
            time.sleep(.03)
    append_run({'run_id': 'old-relative-run', 'task_id': source['taskId'], 'dispatch_key': 'original-relative-dispatch',
        'status': 'running' if active else 'completed', 'result': {'processJobId': process_id}}, root=root / 'background_jobs')
    source.update(runCount=1, status='running' if active else 'scheduled')
    path = root / mapped_by['sourceFile']
    path.write_text(json.dumps(source), encoding='utf8')
    raw = path.read_bytes()
    mapped_by['reconcileDispatches'] = True
    monkeypatch.chdir(root)  # Wrong current base must have no influence.
    port = JsonTaskHandoff(service)
    # A terminal process must also have a proven supervisor exit.
    if not active:
        import time
        from packages.jobs.independent_executor import ExecutionStore
        from packages.core.platform.execution_host import supervisor_exit_evidence
        doc = ExecutionStore(service.root / 'executions').get(process_id)
        deadline = time.monotonic() + 10
        while not supervisor_exit_evidence(doc['supervisorIdentity'])['confirmed']:
            assert time.monotonic() < deadline
            time.sleep(.03)
    plan = port.preview([mapped_by], actor=USER)
    assert plan['blocked'] == [], plan['blocked']
    before = service.final.store.get(source['jobId'])
    port.apply(plan, 'original-relative-handoff', actor=USER)
    assert service.final.store.get(source['jobId']) == before
    assert project(service, source['taskId'], actor=USER)['task']['runCount'] == 1
    epoch = service.owner.token[0]
    asyncio.run(service.close())
    cold = FinalService(service.root, service.auth)
    cold.owner.claim(epoch)
    cold.owner.set_state('active')
    try:
        asyncio.run(cold.recover_external_receipts())
        assert len(cold.final.store.get(source['jobId'])['finalRuntime']['runs']) == 1
        gate.write_text('release')
        asyncio.run(finish(cold, source['jobId'], admitted['runId']))
        current = cold.final.store.get(source['jobId'])['finalRuntime']
        assert set(current['attempts']) == set(state['attempts']) and len(current['effects']) == 1
        assert project(cold, source['taskId'], actor=USER)['task']['runCount'] == 1
    finally:
        gate.write_text('release')
        asyncio.run(cold.close())
    assert counter.read_text().splitlines() == ['literal & {business}'] and path.read_bytes() == raw


def test_P6_CONFIG_07_multi_entry_identity_cursor_and_dispatch_key(json_task):
    service, source, mapping, root = json_task
    second = copy.deepcopy(source['schedule'][0])
    second['id'] = 'entry-two'
    # Both original entries intentionally share the old task-level next-fire
    # fact; ruleId remains the disambiguating identity in the canonical event.
    source['schedule'].append(second)
    source['nextFireAt'] = source['schedule'][0]['nextFireAt']
    path = root / mapping['sourceFile']
    path.write_text(json.dumps(source), encoding='utf8')
    port = JsonTaskHandoff(service)
    plan = port.preview([mapping], actor=USER)
    assert plan['blocked'] == [], plan['blocked']
    mapped = plan['entries'][0]['mapped']
    first_id = source['schedule'][0]['id']
    assert set(mapped['entryCursors']) == {first_id, 'entry-two'}
    port.apply(plan, 'multi-entry-adopt', actor=USER)
    binding = None
    with service.transaction(readonly=True) as db:
        _, binding = resolve(service, db, source['taskId'])
    creator = service.creators.get(binding['creatorId'])
    assert {rule['ruleId'] for rule in creator['config']['rules']} == {first_id, 'entry-two'}
    projected = project(service, source['taskId'], actor=USER)['task']
    assert isinstance(projected['schedule'], list)
    assert {entry['id'] for entry in projected['schedule']} == {first_id, 'entry-two'}
    paused = canonical_tasks.update(service, source['taskId'], {'entry_id': 'entry-two',
        'expected_entry_revision': 1, 'entry_paused': True}, actor=USER,
        request_key='multi-entry-pause', expected_revision=1)
    assert paused['result']['entryRevision'] == 2
    assert next(item for item in project(service, source['taskId'], actor=USER)['task']['schedule']
                if item['id'] == 'entry-two')['nextFireAt'] is None
    replay = canonical_tasks.update(service, source['taskId'], {'entry_id': 'entry-two',
        'expected_entry_revision': 1, 'entry_paused': True}, actor=USER,
        request_key='multi-entry-pause', expected_revision=1)
    assert replay['replayed']
    with pytest.raises(fc.ContractError, match='Per-entry schedule revision'):
        canonical_tasks.update(service, source['taskId'], {'entry_id': 'entry-two',
            'expected_entry_revision': 1, 'entry_paused': False}, actor=USER,
            request_key='multi-entry-stale', expected_revision=2)
    resumed = canonical_tasks.update(service, source['taskId'], {'entry_id': 'entry-two',
        'expected_entry_revision': 2, 'entry_paused': False}, actor=USER,
        request_key='multi-entry-resume', expected_revision=2)
    assert resumed['result']['entryRevision'] == 3
    removed = canonical_tasks.update(service, source['taskId'], {'entry_id': 'entry-two',
        'expected_entry_revision': 3, 'delete_entry': True}, actor=USER,
        request_key='multi-entry-delete', expected_revision=3)
    assert removed['result']['entryId'] == 'entry-two'
    assert [item['id'] for item in project(service, source['taskId'], actor=USER)['task']['schedule']] == [first_id]
    run = control(service, source['taskId'], 'run-now', actor=USER, request_key='multi-entry-manual')
    with service.transaction() as db:
        job = service._get(db, 'job', source['jobId'])
        freeze_dispatch(service, db, job, run['runId'], {'creatorId': binding['creatorId'],
            'payload': {'ruleId': first_id, 'scheduledAt': creator['cursor']['rules'][first_id]['nextFireAt']}})
        service._save(db, 'job', source['jobId'], job)
    state = service.final.store.get(source['jobId'])['finalRuntime']
    assert state['plans'][run['runId']]['dispatchKey'].startswith(source['taskId'] + ':' + first_id + ':')


def test_P6_CONFIG_08_multi_entry_heterogeneous_policy_rule_override_and_cas(json_task):
    service, source, mapping, root = json_task
    second = copy.deepcopy(source['schedule'][0])
    second.update(id='entry-policy', misfirePolicy='skip', graceSec=7)
    source['schedule'].append(second)
    path = root / mapping['sourceFile']
    path.write_text(json.dumps(source), encoding='utf8')
    port = JsonTaskHandoff(service)
    plan = port.preview([mapping], actor=USER)
    assert plan['blocked'] == [], plan['blocked']
    port.apply(plan, 'heterogeneous-policy-adopt', actor=USER)
    with service.transaction(readonly=True) as db:
        _, binding = resolve(service, db, source['taskId'])
    creator = service.creators.get(binding['creatorId'])
    rules = {rule['ruleId']: rule for rule in creator['config']['rules']}
    assert rules['entry-policy']['misfirePolicy'] == 'skip'
    assert rules['entry-policy']['graceSeconds'] == '7'
    from packages.scheduler.providers import TimeProvider, instant, timestamp
    due = creator['cursor']['rules']['entry-policy']['nextFireAt']
    provider = TimeProvider()
    within = provider.prepare(creator, 'time:entry-policy:' + due,
        timestamp(instant(due) + 2), 'drive-within-entry-grace', 1)
    assert within['payload']['driveReason'] == 'scheduled'
    assert 'disposition' not in within
    missed = provider.prepare(creator, 'time:entry-policy:' + due,
        timestamp(instant(due) + 8), 'drive-outside-entry-grace', 2)
    assert missed['disposition'] == 'skipped'
    updated = canonical_tasks.update(service, source['taskId'], {'entry_id': 'entry-policy',
        'expected_entry_revision': 1, 'entry_misfire_policy': 'fire_now',
        'entry_grace_seconds': 2}, actor=USER, request_key='heterogeneous-policy-edit', expected_revision=1)
    assert updated['result']['entryRevision'] == 2
    creator = service.creators.get(binding['creatorId'])
    rules = {rule['ruleId']: rule for rule in creator['config']['rules']}
    assert rules['entry-policy']['misfirePolicy'] == 'fire_now'
    assert rules['entry-policy']['graceSeconds'] == '2'
    with pytest.raises(fc.ContractError, match='Per-entry schedule revision'):
        canonical_tasks.update(service, source['taskId'], {'entry_id': 'entry-policy',
            'expected_entry_revision': 1, 'entry_misfire_policy': 'skip'}, actor=USER,
            request_key='heterogeneous-policy-stale', expected_revision=2)


def test_P6_CONFIG_09_entry_lifecycle_permission_fence(h):
    before = project(h.service, h.source['taskId'], actor=USER)['task']
    permissions = h.service.auth._default_permissions()
    permissions['job']['updateConfig'] = True
    permissions['eventCreator']['updateConfig'] = True
    permissions['eventCreator']['lifecycle'] = False
    h.service.auth.change_permissions(permissions, h.service.auth.permissions_status()['revision'])
    actor = {'type': 'agent', 'id': h.source['targetSessionId'],
             'revision': h.service.auth.permissions_status()['revision']}
    with pytest.raises(fc.ContractError, match='Authorization revoked or capability denied'):
        canonical_tasks.update(h.service, h.source['taskId'], {
            'entry_id': h.source['schedule'][0]['id'], 'expected_entry_revision': 1,
            'entry_paused': True}, actor=actor, request_key='entry-lifecycle-denied')
    assert project(h.service, h.source['taskId'], actor=USER)['task'] == before


def test_P6_CONFIG_10_mixed_entry_patch_is_atomic(h):
    canonical_tasks.update(h.service, h.source['taskId'], {
        'entry_id': h.source['schedule'][0]['id'], 'expected_entry_revision': 1,
        'entry_paused': True, 'text': 'mixed body'}, actor=USER, request_key='mixed-edit')
    value = project(h.service, h.source['taskId'], actor=USER)['task']
    assert value['text'] == 'mixed body' and value['nextFireAt'] is None
    before = copy.deepcopy(value)
    with pytest.raises(fc.ContractError, match='Per-entry schedule revision'):
        canonical_tasks.update(h.service, h.source['taskId'], {
            'entry_id': h.source['schedule'][0]['id'], 'expected_entry_revision': 1,
            'entry_paused': False, 'text': 'must rollback'}, actor=USER, request_key='mixed-stale')
    assert project(h.service, h.source['taskId'], actor=USER)['task'] == before


def test_P6_CONFIG_11_bulk_mixed_insert_delete_cold_receipts(h):
    tid = h.source['taskId']
    spec = {'kind': 'interval', 'intervalSec': 120, 'anchor': '2030-01-01T00:00:00',
            'misfirePolicy': 'skip', 'graceSec': 7}
    body = {'schedule': [spec, dict(spec, intervalSec=240)], 'text': 'bulk edited'}
    value = canonical_tasks.update(h.service, tid, body, actor=USER,
        request_key='bulk-two', expected_revision=1)
    assert canonical_tasks.update(h.service, tid, body, actor=USER,
        request_key='bulk-two', expected_revision=1)['replayed']
    rows = project(h.service, tid, actor=USER)['task']['schedule']
    assert len(rows) == 2 and rows[0]['id'] != h.source['schedule'][0]['id']
    first, second = rows
    canonical_tasks.update(h.service, tid, {'entry_id': first['id'], 'expected_entry_revision': 1,
        'entry_paused': True, 'text': 'mixed atomic'}, actor=USER, request_key='mixed-two', expected_revision=2)
    canonical_tasks.update(h.service, tid, {'append_schedule': [spec], 'insert_index': 1}, actor=USER,
        request_key='insert-middle', expected_revision=3)
    task = project(h.service, tid, actor=USER)['task']
    assert task['text'] == 'mixed atomic'
    assert task['schedule'][0]['paused'] and task['schedule'][2]['id'] == second['id']
    inserted = task['schedule'][1]
    assert inserted['id'] not in {first['id'], second['id']}
    for rev, entry in enumerate([inserted, first, second], start=4):
        result = canonical_tasks.update(h.service, tid, {'entry_id': entry['id'],
            'expected_entry_revision': 2 if entry['id'] == first['id'] else 1, 'delete_entry': True},
            actor=USER, request_key='remove-' + entry['id'], expected_revision=rev)
    assert result['result']['deleted']
    cold = FinalService(h.service.root, h.service.auth)
    try:
        task = project(cold, tid, actor=USER)
        assert task['deleted'] and task['task']['runCount'] == 0
    finally:
        asyncio.run(cold.close())
    assert json.loads((h.root / h.mapping['sourceFile']).read_text(encoding='utf8')) == h.source


def test_P6_CONFIG_12_real_http_concurrent_revision_owner_lease(h, tcp):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from packages.jobs.runtime_owner import OwnerFence, OwnershipConflict
    tid = h.source['taskId']
    spec = {'kind': 'interval', 'intervalSec': 60}
    canonical_tasks.update(h.service, tid, {'schedule': [spec, spec]}, actor=USER,
        request_key='concurrent-setup', expected_revision=1)
    entry = project(h.service, tid, actor=USER)['task']['schedule'][0]
    barrier = Barrier(2)
    def edit(number):
        barrier.wait(timeout=10)
        body = {'entryId': entry['id'], 'expectedEntryRevision': 1, 'entryPaused': True,
                'text': 'winner-' + str(number), 'expectedRevision': 2, 'requestKey': 'race-' + str(number)}
        with client(h, tcp) as c:
            result = c.patch('/api/scheduler/tasks/' + tid, json=body)
            return result.status_code, body
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, [1, 2]))
    assert sorted(code for code, _ in results) == [200, 409]
    winner = next(body for code, body in results if code == 200)
    with client(h, tcp) as c:
        assert c.patch('/api/scheduler/tasks/' + tid, json=winner).json()['operation']['replayed']
    competing = OwnerFence(h.service.owner.root)
    with pytest.raises(OwnershipConflict) as exc:
        competing.claim(h.service.owner.token[0])
    assert exc.value.code == 'revision_conflict' and exc.value.reason == 'owner_busy'
    with pytest.raises(fc.ContractError):
        with competing.legacy_pass():
            pytest.fail('legacy writer must never enter after handoff')
    original = h.service.owner.token
    before = project(h.service, tid, actor=USER)['task']
    h.service.owner.token = (original[0], 'expired-owner')
    try:
        with pytest.raises(fc.ContractError, match='fence'):
            canonical_tasks.update(h.service, tid, {'append_schedule': [spec]}, actor=USER,
                request_key='stale-owner-insert', expected_revision=3)
    finally:
        h.service.owner.token = original
    assert project(h.service, tid, actor=USER)['task'] == before
    assert before['runCount'] == 0


@pytest.mark.parametrize('lost_receipt', [False, True])
def test_P6_CONFIG_13_multi_entry_real_notice_cold_no_replay(h, lost_receipt):
    from packages.core import session
    from packages.web.server import _send_final_notice
    from packages.jobs.final_service import definition
    tid = h.source['taskId']
    target = session.get(h.source['targetSessionId'], load_history=False)
    target.queue_paused = True
    session.save(target)
    job = h.service.get('job', h.source['jobId'], actor=USER)
    config = definition('job', job)
    config['notification'] = {'on': ['completed'], 'recipients': [
        {'recipientId': 'owned-entry-notice', 'channelId': 'session', 'targetRef': target.id}]}
    h.service.write('job', job['id'], {'definition': config, 'expectedRevision': job['configRevision'],
        'requestKey': 'configure-owned-notice'}, actor=USER)
    run = control(h.service, tid, 'run-now', actor=USER, request_key='notice-original-run')
    frozen = copy.deepcopy(h.service.final.store.get(job['id'])['finalRuntime']['plans'][run['runId']]['frozen'])
    canonical_tasks.update(h.service, tid, {'schedule': [{'kind': 'interval', 'intervalSec': 60},
        {'kind': 'interval', 'intervalSec': 120}], 'text': 'future notice work'}, actor=USER,
        request_key='notice-bulk', expected_revision=2)
    assert h.service.final.store.get(job['id'])['finalRuntime']['plans'][run['runId']]['frozen'] == frozen
    sends = []
    async def sender(row):
        sends.append((row['channel'], row['outboxId']))
        receipt = await _send_final_notice(row)
        return {'outcome': 'unknown'} if lost_receipt and row['channel'] == 'session' else receipt
    h.service.outbox_sender = sender
    async def deliver():
        await h.service.dispatch_once()
        await h.service.deliver_outbox()
        await h.service.deliver_outbox()
    asyncio.run(deliver())
    projected = h.service.get_run(run['runId'], actor=USER)
    assert projected['state'] == 'completed'
    notice = projected['notifications'][0]
    assert notice['status'] == ('uncertain' if lost_receipt else 'acked')
    assert len(sends) == 2 and len(set(sends)) == 2
    assert ('session', notice['outboxId']) in sends
    assert len([item for item in sends if item[0] == 'ws']) == 1
    queued = session.get(target.id, load_history=False).queue_pending
    assert len([row for row in queued if row.get('type') == 'notice']) == 1
    assert next(row for row in queued if row.get('type') == 'notice')['eventId'] == notice['outboxId']
    asyncio.run(h.service.close())
    cold = FinalService(h.service.root, h.service.auth)
    cold.owner.claim(cold.owner.status()['epoch'])
    cold.owner.set_state('active')
    cold.outbox_sender = sender
    try:
        asyncio.run(cold.deliver_outbox())
        assert len(sends) == 2 and len(set(sends)) == 2
        assert ('session', notice['outboxId']) in sends
        assert cold.get_run(run['runId'], actor=USER)['notifications'][0]['outboxId'] == notice['outboxId']
        assert project(cold, tid, actor=USER)['task']['text'] == 'future notice work'
    finally:
        asyncio.run(cold.close())


def test_P6_CONFIG_14_real_http_bulk_insert_mixed_final_delete(h, tcp):
    tid = h.source['taskId']
    path = '/api/scheduler/tasks/' + tid
    spec = {'kind': 'interval', 'intervalSec': 120, 'misfirePolicy': 'skip', 'graceSec': 3}
    with client(h, tcp) as c:
        bulk = {'schedule': [spec, dict(spec, intervalSec=240)], 'text': 'HTTP bulk',
                'expectedRevision': 1, 'requestKey': 'tcp-bulk'}
        created = c.patch(path, json=bulk)
        assert created.status_code == 200, created.text
        assert c.patch(path, json=bulk).json()['operation']['replayed']
        task = created.json()['task']
        assert task['text'] == 'HTTP bulk' and len(task['schedule']) == 2
        inserted = c.patch(path, json={'appendSchedule': [spec], 'insertIndex': 1,
            'expectedRevision': 2, 'requestKey': 'tcp-insert'})
        assert inserted.status_code == 200, inserted.text
        entries = inserted.json()['task']['schedule']
        body = {'entryId': entries[1]['id'], 'expectedEntryRevision': 1, 'entryPaused': True,
                'name': 'HTTP mixed', 'expectedRevision': 3, 'requestKey': 'tcp-mixed'}
        mixed = c.patch(path, json=body)
        assert mixed.status_code == 200, mixed.text
        assert mixed.json()['task']['name'] == 'HTTP mixed' and mixed.json()['task']['schedule'][1]['paused']
        bad = c.patch(path, json={**body, 'name': 'must rollback', 'requestKey': 'tcp-stale'})
        assert bad.status_code == 409
        assert c.get(path).json()['task']['name'] == 'HTTP mixed'
        for rev, entry in enumerate(entries, start=4):
            removed = c.patch(path, json={'entryId': entry['id'], 'expectedEntryRevision': 2 if entry == entries[1] else 1,
                'deleteEntry': True, 'description': 'retained tombstone',
                'expectedRevision': rev, 'requestKey': 'tcp-delete-' + entry['id']})
            assert removed.status_code == 200, removed.text
        assert removed.json()['operation']['result']['deleted']
        assert removed.json()['task']['description'] == 'retained tombstone'
        assert not removed.json()['task']['executable']


@pytest.mark.parametrize('prefix', ['/api/jobs', '/api/legacy/jobs'])
def test_P6_CONFIG_15_original_jobs_bulk_entry_transfers_to_same_graph(h, tcp, prefix):
    from packages.jobs import api as jobs_api
    from packages.jobs.final_api import install
    install(h.app, jobs_api.router)
    with client(h, tcp) as c:
        result = c.patch(prefix + '/' + h.source['jobId'], json={
            'schedule': [{'kind': 'interval', 'intervalSec': 60}, {'kind': 'interval', 'intervalSec': 120}],
            'description': 'original jobs edit', 'expectedRevision': 1, 'requestKey': 'original-jobs-bulk'})
        assert result.status_code == 200, result.text
        value = result.json()
        assert value['ok'] and value['job']['jobId'] == h.source['jobId']
        assert len(value['job']['schedule']) == 2 and value['job']['description'] == 'original jobs edit'
        assert c.patch(prefix + '/' + h.source['jobId'], json={
            'schedule': [{'kind': 'interval', 'intervalSec': 60}, {'kind': 'interval', 'intervalSec': 120}],
            'description': 'original jobs edit', 'expectedRevision': 1, 'requestKey': 'original-jobs-bulk'}).json()['operation']['replayed']


def test_P6_CONFIG_16_last_entry_delete_unsettled_rolls_back_all_fields(h):
    tid = h.source['taskId']
    admitted = control(h.service, tid, 'run-now', actor=USER, request_key='unsettled-original')
    before = project(h.service, tid, actor=USER)
    with pytest.raises(fc.ContractError, match='unsettled'):
        canonical_tasks.update(h.service, tid, {'entry_id': h.source['schedule'][0]['id'],
            'expected_entry_revision': 1, 'delete_entry': True, 'text': 'must rollback'},
            actor=USER, request_key='last-delete-unsettled', expected_revision=2)
    assert project(h.service, tid, actor=USER) == before
    assert h.service.get_run(admitted['runId'], actor=USER)['state'] == 'queued'


def test_P6_CONFIG_17_inserted_entry_real_tick_original_receipt_no_replay(h):
    tid = h.source['taskId']
    original = (h.root / h.mapping['sourceFile']).read_bytes()
    canonical_tasks.update(h.service, tid, {'append_schedule': [
        {'kind': 'once', 'at': '2027-01-01T00:00:00Z'}]}, actor=USER,
        request_key='new-entry-clock', expected_revision=1)
    task = project(h.service, tid, actor=USER)['task']
    added = task['schedule'][1]
    assert added['id'] != h.source['schedule'][0]['id']
    assert asyncio.run(h.service.creator_runtime.tick(at=added['nextFireAt'])) == 1
    envelope = h.service.events.page()[-1]
    event = {**envelope, 'type': envelope['eventType']}
    assert event['payload']['ruleId'] == added['id']
    target = h.service.targets(event)[0]
    admitted = asyncio.run(h.service.admit_event_async(event, target))
    assert asyncio.run(h.service.admit_event_async(event, target))['runId'] == admitted['runId']
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.store.get(h.source['jobId'])['finalRuntime']
    assert state['plans'][admitted['runId']]['dispatchKey'].startswith(tid + ':' + added['id'] + ':')
    assert len(state['effects']) == 1
    assert asyncio.run(h.service.creator_runtime.tick(at=added['nextFireAt'])) == 0
    assert (h.root / h.mapping['sourceFile']).read_bytes() == original


@pytest.mark.parametrize('case', ['mixed', 'insert', 'last-delete'])
def test_P6_CONFIG_18_named_original_entry_gaps(h, case):
    editor = canonical_tasks
    if os.environ.get('P6_ENTRY_EDITOR_BASELINE') == '1':
        import subprocess, types
        editor = types.ModuleType('packages.scheduler.original_entry_editor')
        editor.__package__ = 'packages.scheduler'
        code = subprocess.check_output(['git', 'show', 'f2045961:packages/scheduler/canonical_tasks.py'])
        exec(compile(code, '<f2045961 original entry editor>', 'exec'), editor.__dict__)
    tid = h.source['taskId']
    if case == 'mixed':
        patch = {'entry_id': h.source['schedule'][0]['id'], 'expected_entry_revision': 1,
                 'entry_paused': True, 'text': 'named mixed edit'}
    elif case == 'insert':
        patch = {'append_schedule': [{'kind': 'interval', 'intervalSec': 120}]}
    else:
        patch = {'entry_id': h.source['schedule'][0]['id'], 'expected_entry_revision': 1,
                 'delete_entry': True}
    result = editor.update(h.service, tid, patch, actor=USER, request_key='named-' + case, expected_revision=1)
    current = project(h.service, tid, actor=USER)
    if case == 'mixed':
        assert current['task']['text'] == 'named mixed edit' and current['task']['nextFireAt'] is None
    elif case == 'insert':
        assert isinstance(current['task']['schedule'], list) and len(current['task']['schedule']) == 2
    else:
        assert result['result']['deleted'] and current['deleted']


@pytest.mark.parametrize('patch', [{'entry_paused': True}, {'insert_index': 0},
    {'delete_entry': True, 'entry_grace_seconds': 1}])
def test_P6_CONFIG_19_no_silent_entry_controls(h, patch):
    if patch.get('delete_entry'):
        patch = {**patch, 'entry_id': h.source['schedule'][0]['id'], 'expected_entry_revision': 1}
    before = project(h.service, h.source['taskId'], actor=USER)
    with pytest.raises(fc.ContractError):
        canonical_tasks.update(h.service, h.source['taskId'], patch, actor=USER,
            request_key='incomplete-entry-control', expected_revision=1)
    assert project(h.service, h.source['taskId'], actor=USER) == before


def test_P6_CONFIG_20_mixed_edit_preserves_live_task_lifecycle(h):
    tid = h.source['taskId']
    canonical_tasks.update(h.service, tid, {'schedule': [{'kind': 'interval', 'intervalSec': 60},
        {'kind': 'interval', 'intervalSec': 120}]}, actor=USER, request_key='paused-bulk', expected_revision=1)
    control(h.service, tid, 'pause', actor=USER, request_key='external-pause', expected_revision=2)
    entry = project(h.service, tid, actor=USER)['task']['schedule'][0]
    canonical_tasks.update(h.service, tid, {'entry_id': entry['id'], 'expected_entry_revision': 1,
        'entry_misfire_policy': 'skip', 'text': 'edit while paused'}, actor=USER,
        request_key='edit-while-paused', expected_revision=3)
    assert project(h.service, tid, actor=USER)['task']['paused']
