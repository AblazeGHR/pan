"""Owned cooperative JSON task -> canonical clock/admission, never old writer."""
import asyncio
import copy
import json
import os
from datetime import datetime, timezone

import pytest

from packages.jobs import final_contracts as fc
from packages.jobs.final_api import CanonicalUserAuth
from packages.jobs.final_service import FinalService, object_ref
from packages.jobs.runtime_owner import OwnerFence, enroll_fresh_profile
from packages.scheduler.json_handoff import JsonTaskHandoff
from tests.test_action_unification_p6_active_source import USER


@pytest.fixture
def json_task(tmp_path, monkeypatch):
    root = tmp_path / 'cooperative-json'
    enroll_fresh_profile(root)
    monkeypatch.setenv('PAN_BACKGROUND_JOBS_DIR', str(root / 'background_jobs'))
    monkeypatch.setenv('PAN_SCHEDULER_DIR', str(root / 'background_jobs'))
    from packages.scheduler import store
    from packages.core import session
    target = session.create('Owned JSON target')
    old = store.create_task({'id': 'sch_original_json', 'name': 'JSON original',
        'target_session_id': target.id, 'text': 'original text', 'enabled': True,
        'action': {'api': 'send_session'}, 'max_runs': 3,
        'schedule': {'kind': 'interval', 'intervalSec': 60, 'anchor': '2030-01-01T00:00:00+00:00'}})
    from packages.jobs.scheduled_records import find_task
    source = find_task(old['id'], root / 'background_jobs')
    source_file = 'background_jobs/jobs/' + source['jobId'] + '.json'
    source = json.loads((root / source_file).read_text(encoding='utf8'))
    service = FinalService(root / 'canonical', CanonicalUserAuth(root / 'automation'))
    action = service.get('action', 'product_session_message_runtime_v1', actor=USER)
    original_zone = datetime.now().astimezone().isoformat()[-6:]
    mapping = {'sourceFile': source_file, 'actionRef': object_ref('action', action), 'timezone': original_zone,
        'policies': {'overlap': {'mode': 'forbid', 'maxActive': 1, 'maxPending': 0},
                     'recovery': {'automatic': False, 'maxAttempts': 1, 'backoffSeconds': ['1'], 'stableResetSeconds': '0'},
                     'notification': {'on': [], 'recipients': []}, 'retention': {'policyRef': 'owned-test'}}}
    yield service, source, mapping, root
    asyncio.run(service.close())


def test_P6_JSON_01_active_original_id_cursor_limit_and_single_writer(json_task):
    service, source, mapping, root = json_task
    port = JsonTaskHandoff(service)
    raw = (root / mapping['sourceFile']).read_bytes()
    plan = port.preview([mapping], actor=USER)
    assert plan['blocked'] == [], plan['blocked']
    if os.environ.get('P6_JSON_BASELINE') == '1':
        import subprocess
        import types
        code = subprocess.check_output(['git', 'show', '9807ec8febcaedb4b1a36640805b57eaaf95fbff:packages/scheduler/retained.py'])
        baseline = types.ModuleType('packages.scheduler.json_baseline_retained')
        baseline.__package__ = 'packages.scheduler'
        exec(compile(code, '<9807 scheduler retained>', 'exec'), baseline.__dict__)
        from packages.scheduler.history import History
        baseline.query(service, History(service.root).get(source['taskId']), actor=USER)
    result = port.apply(plan, 'json-original-adopt', actor=USER)
    assert port.apply(plan, 'json-original-adopt', actor=USER)['operationId'] == result['operationId']
    target = result['result']['tasks'][0]
    assert target['jobId'] == source['jobId'] and target['taskId'] == source['taskId']
    creator = service.creators.get(target['creatorId'])
    rule_id = source['schedule'][0]['id']
    assert creator['state'] == 'enabled'
    assert 'driveLimit' not in creator['config']  # Events are not returned dispatches.
    original_instant = datetime.fromisoformat(source['nextFireAt'] + mapping['timezone']).astimezone(timezone.utc)
    assert creator['cursor']['rules'][rule_id]['nextFireAt'] == original_instant.isoformat().replace('+00:00', 'Z')
    from packages.scheduler import store
    with pytest.raises(fc.ContractError):
        store.update_task(source['taskId'], {'text': 'second writer'})
    assert (root / mapping['sourceFile']).read_bytes() == raw
    assert service.final.get(source['jobId'])['runs'] == {}
    cold = FinalService(service.root, service.auth)
    try:
        assert cold.get('job', source['jobId'], actor=USER)['id'] == source['jobId']
        assert cold.operation_for_key('create', 'json-original-adopt', actor=USER)['operationId'] == result['operationId']
    finally:
        asyncio.run(cold.close())


@pytest.mark.parametrize('change', ['unknown', 'uncertain', 'dispatch', 'count', 'mismatch-cursor', 'complex'])
def test_P6_JSON_02_missing_identity_unknown_complex_preserve(json_task, change):
    service, source, mapping, root = json_task
    file = root / mapping['sourceFile']
    value = json.loads(file.read_text(encoding='utf8'))
    if change == 'unknown': value['status'] = 'unknown'
    elif change == 'uncertain': value['status'] = 'uncertain'
    elif change == 'dispatch': value.update(dispatchKey='original-dispatch', runId='original-run')
    elif change == 'count': value['runCount'] = 2
    elif change == 'mismatch-cursor': value['nextFireAt'] = '2031-01-01T00:00:00Z'
    else: value['unknownLeaseOwner'] = 'preserve'
    file.write_text(json.dumps(value), encoding='utf8')
    before = file.read_bytes()
    plan = JsonTaskHandoff(service).preview([mapping], actor=USER)
    assert plan['entries'] == [] and len(plan['blocked']) == 1
    with pytest.raises(fc.ContractError): JsonTaskHandoff(service).apply(plan, 'blocked', actor=USER)
    assert file.read_bytes() == before and service.list('job', actor=USER)['items'] == []


def test_P6_JSON_03_real_lease_and_crash_resume(json_task, monkeypatch):
    service, source, mapping, root = json_task
    port = JsonTaskHandoff(service)
    plan = port.preview([mapping], actor=USER)
    with OwnerFence(root).legacy_pass():
        with pytest.raises(fc.ContractError, match='Legacy pass'):
            port.apply(plan, 'lease-adopt', actor=USER)
    original = service._operation
    def crash(*a, **kw): raise OSError('owned crash after source fence')
    monkeypatch.setattr(service, '_operation', crash)
    with pytest.raises(OSError): port.apply(plan, 'lease-adopt', actor=USER)
    from packages.scheduler import store
    with pytest.raises(fc.ContractError): store.update_task(source['taskId'], {'text': 'old writer'})
    monkeypatch.setattr(service, '_operation', original)
    assert port.apply(plan, 'lease-adopt', actor=USER)['status'] == 'completed'


def test_P6_JSON_04_manual_original_dispatch_count_cursor_pause_unknown_no_replay(json_task):
    from packages.scheduler.json_handoff import control, project
    from packages.jobs.final_runtime.executor import TypedExecutor
    from tests.test_automation_canonical import SessionProvider
    service, source, mapping, root = json_task
    port = JsonTaskHandoff(service)
    result = port.apply(port.preview([mapping], actor=USER), 'json-dispatch-adopt', actor=USER)
    provider = SessionProvider()
    service.executor = TypedExecutor(session_provider=provider)
    tid = source['taskId']
    cid = result['result']['tasks'][0]['creatorId']
    original_cursor = copy.deepcopy(service.creators.get(cid)['cursor'])
    pause = control(service, tid, 'pause', actor=USER, request_key='pause-old', expected_revision=1)
    assert pause['status'] == 'completed'
    assert service.creators.get(cid)['state'] == 'paused'
    # Pause belongs to the clock; old run-now still dispatches manually and
    # preserves next-fire. It is accepted, not falsely reported completed.
    request = control(service, tid, 'run-now', actor=USER, request_key='original-manual-dispatch', expected_revision=2)
    assert request['status'] == 'accepted'
    replay = control(service, tid, 'run-now', actor=USER, request_key='original-manual-dispatch', expected_revision=2)
    assert replay['operationId'] == request['operationId']
    assert service.creators.get(cid)['cursor'] == original_cursor
    asyncio.run(service.dispatch_once())
    assert len(provider.calls) == 1
    current = project(service, tid, actor=USER)
    assert current['task']['runCount'] == 1
    assert current['runs'][0]['runId'] == request['runId']
    assert service.creators.get(cid)['cursor'] == original_cursor
    provider.unknown = True
    unknown = control(service, tid, 'run-now', actor=USER, request_key='original-unknown-dispatch')
    asyncio.run(service.dispatch_once())
    assert len(provider.calls) == 2
    assert project(service, tid, actor=USER)['task']['runCount'] == 2
    assert service.get_run(unknown['runId'], actor=USER)['state'] == 'uncertain'
    asyncio.run(service.dispatch_once())
    assert len(provider.calls) == 2
    with pytest.raises(fc.ContractError): control(service, tid, 'delete', actor=USER, request_key='cannot-delete-unknown')
    assert (root / mapping['sourceFile']).read_bytes() == base64_bytes(project_source(service, tid))


def project_source(service, tid):
    with service.transaction(readonly=True) as db:
        return service._get(db, 'json_scheduler_handoff', tid)['rawBytesBase64']


def base64_bytes(value):
    import base64
    return base64.b64decode(value)


@pytest.mark.parametrize('predecessor_state', ['unknown', 'queued'])
def test_P6_JSON_05_original_run_attempt_effect_checkpoint_survives_cold_unknown(json_task, predecessor_state):
    """Controlled cooperating old writer persists REAL existing runtime output.

    No fixture invents unknown writer ownership or a successful migration.
    The effect is produced once before handoff through the shared executor.
    """
    from packages.jobs.final_runtime.ledger import aggregate
    from packages.jobs.final_runtime.executor import TypedExecutor
    from tests.test_automation_canonical import SessionProvider
    service, source, mapping, root = json_task
    port = JsonTaskHandoff(service)
    initial = port.preview([mapping], actor=USER)
    cfg = initial['entries'][0]['mapped']['job']
    jid = source['jobId']
    # The cooperative predecessor uses WP02's existing ledger/engine, then
    # persists that same authoritative aggregate in its JSON storage backend.
    with service.transaction() as db:
        obj = service._envelope('job', cfg, USER, object_id=jid, state='enabled')
        stored = {'jobId': jid, 'canonical': obj, 'config': cfg, 'configRevision': 1}
        aggregate(stored, service.root)['profileId'] = service.profile_id
        service._save(db, 'job', jid, stored)
        original = service._admit(db, stored, {'expectedRevision': 1, 'input': cfg['arguments']}, USER,
                                  'op_owned_predecessor', cause={'type': 'manual'})
    provider = SessionProvider()
    provider.unknown = True
    service.executor = TypedExecutor(session_provider=provider)
    if predecessor_state == 'unknown':
        asyncio.run(service.dispatch_once())
        assert len(provider.calls) == 1
    with service.transaction() as db:
        stored = service._get(db, 'job', jid)
        runtime = stored['finalRuntime']
        runtime['plans'][original['runId']]['dispatchKey'] = 'original-json-dispatch'
        checkpoint = copy.deepcopy(runtime)
        db.execute("DELETE FROM final_documents WHERE kind='job' AND id=?", (jid,))
    with OwnerFence(root).legacy_pass():
        source.update(finalRuntime=checkpoint, runId=original['runId'], dispatchKey='original-json-dispatch',
                      runCount=1 if predecessor_state == 'unknown' else 0, configRevision=1, status='scheduled')
        (root / mapping['sourceFile']).write_text(json.dumps(source), encoding='utf8')
    raw = (root / mapping['sourceFile']).read_bytes()
    reviewed = port.preview([mapping], actor=USER)
    assert reviewed['blocked'] == [], reviewed['blocked']
    port.apply(reviewed, 'original-checkpoint-handoff', actor=USER)
    with service.transaction(readonly=True) as db:
        assert service._get(db, 'job', jid)['finalRuntime'] == checkpoint
    asyncio.run(service.dispatch_once())
    assert len(provider.calls) == 1
    from packages.scheduler.json_handoff import project
    assert project(service, source['taskId'], actor=USER)['task']['runCount'] == 1
    cold = FinalService(service.root, service.auth)
    try:
        assert cold.get_run(original['runId'], actor=USER)['state'] == 'uncertain'
        current = cold.final.get(jid)
        if predecessor_state == 'unknown':
            assert set(current['attempts']) == set(checkpoint['attempts'])
            assert current['effects'] == checkpoint['effects']
        else:
            assert len(current['attempts']) == 1 and len(current['runs']) == 1
    finally:
        asyncio.run(cold.close())
    assert (root / mapping['sourceFile']).read_bytes() == raw


@pytest.fixture
def http_json(json_task):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from packages.scheduler import api
    from tests.test_automation_canonical import SessionProvider
    from packages.jobs.final_runtime.executor import TypedExecutor
    service, source, mapping, root = json_task
    port = JsonTaskHandoff(service)
    port.apply(port.preview([mapping], actor=USER), 'json-http-adopt', actor=USER)
    provider = SessionProvider()
    service.executor = TypedExecutor(session_provider=provider)
    app = FastAPI()
    app.state.job_final_service = service
    app.state.automation = SimpleNamespace(auth=service.auth)
    app.include_router(api.router)
    token = service.auth.login(service.auth.key())
    return SimpleNamespace(app=app, service=service, source=source, mapping=mapping, root=root,
                           token=token, provider=provider)


@pytest.fixture
def h(http_json):
    return http_json


from tests.test_job_final_clients import tcp


def test_P6_JSON_06_real_http_taskwide_pause_resume_manual_and_delete(h, tcp):
    import httpx
    path = '/api/scheduler/tasks/' + h.source['taskId']
    raw = (h.root / h.mapping['sourceFile']).read_bytes()
    with httpx.Client(base_url=tcp, trust_env=False) as client:
        for method, suffix in [('POST', '/pause'), ('POST', '/resume'), ('POST', '/run-now'), ('DELETE', '')]:
            assert client.request(method, path + suffix).status_code == 401
        client.headers['Authorization'] = 'Bearer ' + h.token
        assert client.get(path).json()['task']['canonicalJobId'] == h.source['jobId']
        pause = client.post(path + '/pause', json={'requestKey': 'http-pause', 'expectedRevision': 1})
        assert pause.status_code == 200 and pause.json()['task']['paused'], pause.text
        resume = client.post(path + '/resume', json={'requestKey': 'http-resume', 'expectedRevision': 2})
        assert resume.status_code == 200 and not resume.json()['task']['paused'], resume.text
        run = client.post(path + '/run-now', json={'requestKey': 'http-manual', 'expectedRevision': 3})
        assert run.status_code == 200 and run.json()['operation']['status'] == 'accepted', run.text
        assert run.json()['run']['dispatchKey'] == 'http-manual'
        asyncio.run(h.service.dispatch_once())
        assert len(h.provider.calls) == 1
        history = client.get(path + '/runs').json()
        assert history['runs'][0]['runId'] == run.json()['operation']['runId']
        assert history['runs'][0]['dispatchKey'] == 'http-manual'
        assert history['runs'][0]['status'] == 'completed'
        assert client.get('/api/scheduler/tasks').json()['tasks'][0]['runCount'] == 1
        deleted = client.request('DELETE', path, json={'requestKey': 'http-delete', 'expectedRevision': 4})
        assert deleted.status_code == 200 and deleted.json()['deleted'], deleted.text
        assert client.get(path).json()['error']['code'] == 'not_found'
    assert (h.root / h.mapping['sourceFile']).read_bytes() == raw


def test_P6_JSON_07_real_standalone_stdio_lifecycle_aliases(http_json, tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from packages.automation.authorization import bind_objects
    from packages.core.manifest_loader import ManifestConfig, McpServer
    from packages.core.session import Session
    from packages.plugins.ipc import Bridge
    from packages.plugins.runtime import Runtime
    from tests.test_job_final_clients import unpack, ROOT
    h = http_json
    permissions = h.service.auth._default_permissions()
    for row in permissions.values():
        for key in row: row[key] = True
    h.service.auth.change_permissions(permissions, h.service.auth.permissions_status()['revision'])
    session = Session(id=h.source['targetSessionId'], name='Owned JSON caller', adapter_config={
        'plugin_bindings': [{'plugin_id': 'pan', 'mode': 'standalone', 'enabled': True}]})
    manifest = ManifestConfig(mcp_servers=[McpServer('pan', command=os.sys.executable, args=['-m', 'packages.mcp.server'])])
    runtime = Runtime(lambda: manifest, lambda sid: session if sid == session.id else None, data_root=tmp_path / 'plugins')
    bind_objects(runtime, h.app)
    async def scenario():
        bridge = Bridge(runtime, tmp_path)
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith('PAN_')}
        env.update(PAN_PLUGIN_IPC=bridge.address, PAN_AGENT_SESSION_ID=session.id, PAN_API_URL='http://127.0.0.1:1')
        try:
            params = StdioServerParameters(command=os.sys.executable, args=['-m', 'packages.mcp.server'], cwd=str(ROOT), env=env)
            async with stdio_client(params) as streams, ClientSession(*streams) as client:
                await client.initialize()
                tid = h.source['taskId']
                pause = unpack(await client.call_tool('scheduler_pause', {'task_id': tid, 'request_key': 'stdio-pause'}))
                assert pause['ok'] and pause['task']['paused'], pause
                resume = unpack(await client.call_tool('scheduler_resume', {'task_id': tid, 'request_key': 'stdio-resume'}))
                assert resume['ok'] and not resume['task']['paused'], resume
                manual = unpack(await client.call_tool('scheduler_run_now', {'task_id': tid, 'request_key': 'stdio-manual'}))
                assert manual['ok'] and manual['operation']['status'] == 'accepted', manual
                again = unpack(await client.call_tool('scheduler_run_now', {'task_id': tid, 'request_key': 'stdio-manual'}))
                assert again['operation']['operationId'] == manual['operation']['operationId']
        finally:
            await bridge.close()
    asyncio.run(scenario())
    assert len(h.service.final.get(h.source['jobId'])['runs']) == 1
    assert not h.provider.calls


def test_P6_JSON_08_paused_grid_event_original_dispatch_limit_and_cold_query(json_task):
    from datetime import timedelta
    from packages.scheduler.json_handoff import control, project
    from packages.jobs.final_runtime.executor import TypedExecutor
    from tests.test_automation_canonical import SessionProvider
    service, source, mapping, root = json_task
    port = JsonTaskHandoff(service)
    port.apply(port.preview([mapping], actor=USER), 'clock-json-adopt', actor=USER)
    provider = SessionProvider()
    service.executor = TypedExecutor(session_provider=provider)
    tid = source['taskId']
    original_instant = datetime.fromisoformat(source['nextFireAt'] + mapping['timezone']).astimezone(timezone.utc)
    at = lambda seconds: (original_instant + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')
    control(service, tid, 'pause', actor=USER, request_key='pause-grid')
    assert asyncio.run(service.creator_runtime.tick(at=at(0))) == 0
    assert service.events.page() == []
    assert project(service, tid, actor=USER)['task']['runCount'] == 0
    control(service, tid, 'resume', actor=USER, request_key='resume-grid')
    for seconds in (60, 120, 180):
        assert asyncio.run(service.creator_runtime.tick(at=at(seconds))) == 1
        envelope = service.events.page()[-1]
        event = {**envelope, 'type': envelope['eventType']}
        targets = service.targets(event)
        assert len(targets) == 1
        first = asyncio.run(service.admit_event_async(event, targets[0]))
        again = asyncio.run(service.admit_event_async(event, targets[0]))
        assert first['runId'] == again['runId']
        # Publishing/admitting does not falsely increment returned dispatches.
        assert project(service, tid, actor=USER)['task']['runCount'] == seconds // 60 - 1
        asyncio.run(service.dispatch_once())
        with service.transaction(readonly=True) as db:
            stored = service._get(db, 'job', source['jobId'])
            dispatch = stored['finalRuntime']['plans'][first['runId']]['dispatchKey']
        assert dispatch == f"{tid}:{source['schedule'][0]['id']}:{int((original_instant + timedelta(seconds=seconds)).timestamp())}"
    assert len(provider.calls) == 3
    assert project(service, tid, actor=USER)['task']['runCount'] == 3
    assert asyncio.run(service.creator_runtime.tick(at=at(240))) == 0
    with pytest.raises(fc.ContractError, match='maxRuns'):
        control(service, tid, 'run-now', actor=USER, request_key='exhausted')
    assert len(provider.calls) == 3 and len(service.events.page()) == 3


def test_P6_JSON_09_disabled_manual_intent_does_not_enable_clock(json_task):
    from packages.scheduler.json_handoff import control, project
    from packages.jobs.final_runtime.executor import TypedExecutor
    from tests.test_automation_canonical import SessionProvider
    service, source, mapping, root = json_task
    file = root / mapping['sourceFile']
    source['enabled'] = False
    file.write_text(json.dumps(source), encoding='utf8')
    port = JsonTaskHandoff(service)
    result = port.apply(port.preview([mapping], actor=USER), 'disabled-manual-adopt', actor=USER)
    cid = result['result']['tasks'][0]['creatorId']
    provider = SessionProvider()
    service.executor = TypedExecutor(session_provider=provider)
    control(service, source['taskId'], 'pause', actor=USER, request_key='disabled-pause')
    assert project(service, source['taskId'], actor=USER)['task']['paused'] is True
    assert service.creators.get(cid)['state'] == 'disabled'
    control(service, source['taskId'], 'resume', actor=USER, request_key='disabled-resume')
    manual = control(service, source['taskId'], 'run-now', actor=USER, request_key='disabled-manual-once')
    asyncio.run(service.dispatch_once())
    assert len(provider.calls) == 1
    assert service.get_run(manual['runId'], actor=USER)['state'] == 'completed'
    assert service.creators.get(cid)['state'] == 'disabled'
    assert project(service, source['taskId'], actor=USER)['task']['enabled'] is False
    assert project(service, source['taskId'], actor=USER)['task']['runCount'] == 1


@pytest.mark.parametrize('failure', ['source-bytes', 'owner-nonce', 'revision', 'grant', 'unknown-writer'])
def test_P6_JSON_10_fence_refusals_do_not_dispatch(json_task, failure):
    from packages.scheduler.json_handoff import control
    service, source, mapping, root = json_task
    port = JsonTaskHandoff(service)
    plan = port.preview([mapping], actor=USER)
    if failure == 'unknown-writer':
        with service.owner.transaction() as db:
            db.execute("DELETE FROM metadata WHERE key='cooperative-profile-v1'")
        with pytest.raises(fc.ContractError, match='Unknown original writer'):
            port.apply(plan, 'unknown-writer', actor=USER)
        return
    port.apply(plan, 'negative-adopt', actor=USER)
    who, token, expected = USER, service.owner.token, None
    if failure == 'source-bytes':
        with (root / mapping['sourceFile']).open('ab') as f: f.write(b'\n')
    elif failure == 'owner-nonce': service.owner.token = (token[0], 'foreign-nonce')
    elif failure == 'revision': expected = 99
    else:
        permissions = service.auth._default_permissions()
        for row in permissions.values():
            for key in row: row[key] = False
        service.auth.change_permissions(permissions, service.auth.permissions_status()['revision'])
        who = {'type': 'agent', 'id': 'revoked', 'revision': service.auth.permissions_status()['revision']}
    try:
        with pytest.raises(fc.ContractError):
            control(service, source['taskId'], 'run-now', actor=who, request_key='cannot-dispatch', expected_revision=expected)
    finally:
        service.owner.token = token
    assert service.final.get(source['jobId'])['runs'] == {}


def test_P6_JSON_11_unknown_lease_and_unselected_live_task_block_profile_cutover(json_task):
    service, source, mapping, root = json_task
    port = JsonTaskHandoff(service)
    file = root / mapping['sourceFile']
    other = file.with_name('other-live-task.json')
    second = {**source, 'jobId': 'job_other', 'taskId': 'sch_other'}
    other.write_text(json.dumps(second), encoding='utf8')
    plan = port.preview([mapping], actor=USER)
    assert any('Unselected active' in row['reason'] for row in plan['blocked'])
    with pytest.raises(fc.ContractError): port.apply(plan, 'must-not-freeze-sibling', actor=USER)
    assert service.owner.status() is None
    other.unlink()  # Owned synthetic fixture only, before handoff.
    plan = port.preview([mapping], actor=USER)
    with service.owner.transaction() as db:
        db.execute("INSERT INTO legacy_leases VALUES('unknown-json-writer')")
    raw = file.read_bytes()
    with pytest.raises(fc.ContractError, match='Legacy pass'):
        port.apply(plan, 'unknown-lease', actor=USER)
    with service.owner.transaction() as db:
        assert db.execute("SELECT id FROM legacy_leases WHERE id='unknown-json-writer'").fetchone()
    assert file.read_bytes() == raw and service.owner.status() is None


def test_P6_JSON_12_concurrent_lifecycle_cas_and_read_queries(json_task):
    from concurrent.futures import ThreadPoolExecutor
    from packages.scheduler.json_handoff import control, project
    service, source, mapping, root = json_task
    port = JsonTaskHandoff(service)
    port.apply(port.preview([mapping], actor=USER), 'race-adopt', actor=USER)
    def write(index):
        try:
            return control(service, source['taskId'], 'pause', actor=USER,
                           request_key='race-' + str(index), expected_revision=1)['status']
        except fc.ContractError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(write, range(2))) == ['completed', 'revision_conflict']
    def read(index):
        for _ in range(10):
            assert project(service, source['taskId'], actor=USER)['task']['revision'] >= 2
        return True
    def lifecycle(index):
        for i in range(10):
            control(service, source['taskId'], 'resume' if i % 2 == 0 else 'pause', actor=USER,
                    request_key='interleave-' + str(i))
        return True
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(read, 0), pool.submit(lifecycle, 1)]
        assert all(f.result(timeout=15) for f in futures)
    assert service.final.get(source['jobId'])['runs'] == {}
