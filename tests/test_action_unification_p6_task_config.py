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


def test_P6_CONFIG_05_real_standalone_stdio_create_update_default_sync(h, tmp_path):
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
        env.update(PAN_PLUGIN_IPC=bridge.address, PAN_AGENT_SESSION_ID=session.id, PAN_API_URL='http://127.0.0.1:1')
        try:
            params = StdioServerParameters(command=sys.executable, args=['-m', 'packages.mcp.server'], cwd=str(ROOT), env=env)
            async with stdio_client(params) as streams, ClientSession(*streams) as c:
                await c.initialize()
                args = {'target_session_id': session.id, 'text': 'stdio original', 'kind': 'interval',
                    'interval_sec': 60, 'request_key': 'stdio-create-original'}
                created = unpack(await c.call_tool('scheduler_create', args))
                assert created['ok'], created
                tid = created['task']['id']
                assert unpack(await c.call_tool('scheduler_create', args))['task']['id'] == tid
                updated = unpack(await c.call_tool('scheduler_update', {'task_id': tid, 'text': 'stdio edited',
                    'description': 'stdio description', 'request_key': 'stdio-edit-original'}))
                assert updated['task']['text'] == 'stdio edited', updated
                ran = unpack(await c.call_tool('scheduler_run_now', {'task_id': tid, 'request_key': 'stdio-sync-original'}))
                assert ran['run']['status'] == 'dispatched', ran
                assert unpack(await c.call_tool('scheduler_runs', {'task_id': tid}))['total'] == 1
        finally:
            await bridge.close()
    asyncio.run(scenario())


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
