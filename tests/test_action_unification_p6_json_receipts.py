"""Original receipt reconciliation and shell transfer with owned processes."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import time

import pytest

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService, definition, object_ref
from packages.jobs.final_runtime.ledger import aggregate
from packages.jobs.independent_executor import ExecutionStore, ExecutionHost, effect_id
from packages.scheduler.json_handoff import JsonTaskHandoff, control, project
from tests.test_action_unification_p6_json_scheduler import json_task, USER


@pytest.fixture
def shell_task(json_task, tmp_path):
    service, source, mapping, root = json_task
    script = tmp_path / 'owned shell command.py'
    counter = tmp_path / 'shell-once.txt'
    gate = tmp_path / 'shell-release'
    script.write_text("from pathlib import Path\nimport sys,time\n" +
        f"Path({str(counter)!r}).open('a').write(sys.argv[1]+'\\n')\n" +
        f"gate=Path({str(gate)!r})\nwhile not gate.exists(): time.sleep(.03)\n" +
        "print('shell-completed')\n", encoding='utf8')
    # The original shell interpreter sees the opaque full string, including a
    # quoted business value containing shell operators and braces.
    command = f'"{sys.executable}" "{script}" "literal & {{business}}"'
    source['action'] = {'api': 'shell', 'args': {'command': command, 'cwd': str(tmp_path), 'hideWindow': True}}
    source['text'] = 'must not interpolate into shell'
    (root / mapping['sourceFile']).write_text(json.dumps(source), encoding='utf8')
    mapping['actionRef'] = object_ref('action', service.get('action', 'product_process_logs_v1', actor=USER))
    yield service, source, mapping, root, counter, gate
    gate.write_text('release', encoding='utf8')
    path = service.root / 'executions' / 'executions.sqlite3'
    if path.exists():
        store = ExecutionStore(service.root / 'executions')
        with store.connect() as db:
            docs = [fc.loads(row[0]) for row in db.execute('SELECT document FROM executions')]
        from packages.core.platform.execution_host import supervisor_exit_evidence
        for doc in docs:
            if doc.get('supervisorIdentity'):
                if doc.get('result') is None:
                    ExecutionHost(store).cleanup_execution(doc['effectId'], doc['attempt'])
                deadline = time.monotonic() + 12
                while not supervisor_exit_evidence(doc['supervisorIdentity'])['confirmed']:
                    assert time.monotonic() < deadline, 'owned shell supervisor did not exit'
                    time.sleep(.03)


async def finish(service, jid, rid):
    deadline = time.monotonic() + 15
    while service.get_run(rid, actor=USER)['state'] not in {'completed', 'failed', 'uncertain', 'cancelled'}:
        assert time.monotonic() < deadline
        await service.dispatch_once()
        await asyncio.sleep(.03)
    assert service.get_run(rid, actor=USER)['state'] == 'completed'


def test_P6_JSON_SHELL_01_actual_shell_args_running_pin_logs_cold_once(shell_task):
    service, source, mapping, root, counter, gate = shell_task
    port = JsonTaskHandoff(service)
    plan = port.preview([mapping], actor=USER)
    assert plan['blocked'] == [], plan['blocked']
    if os.environ.get('P6_SHELL_BASELINE') == '1':
        import subprocess, types
        code = subprocess.check_output(['git', 'show', 'd9660dd6c3aa77258417969314e2f05de04fc2ca:packages/scheduler/json_handoff.py'])
        baseline = types.ModuleType('packages.scheduler.old_json_handoff')
        baseline.__package__ = 'packages.scheduler'
        exec(compile(code, '<d966 JSON mapper>', 'exec'), baseline.__dict__)
        assert baseline.JsonTaskHandoff(service).preview([mapping], actor=USER)['blocked'] == []
    step = plan['entries'][0]['mapped']['job']['steps'][0]
    assert step['arguments']['argv']['value'][-1] == source['action']['args']['command']
    assert 'timeout' not in step['arguments']
    raw = (root / mapping['sourceFile']).read_bytes()
    port.apply(plan, 'adopt-opaque-shell', actor=USER)
    run = control(service, source['taskId'], 'run-now', actor=USER, request_key='opaque-shell-once')
    try:
        asyncio.run(service.dispatch_once())
        assert service.get_run(run['runId'], actor=USER)['state'] == 'running'
        assert project(service, source['taskId'], actor=USER)['task']['runCount'] == 1
        cold = FinalService(service.root, service.auth, owner=service.owner)
        before = service.final.get(source['jobId'])
        for _ in range(2): asyncio.run(cold.recover_external_receipts())
        assert cold.final.get(source['jobId'])['runs'] == before['runs']
        gate.write_text('release')
        asyncio.run(finish(cold, source['jobId'], run['runId']))
        assert counter.read_text().splitlines() == ['literal & {business}']
        state = cold.final.get(source['jobId'])
        assert len(state['runs']) == len(state['attempts']) == len(state['effects']) == 1
        attempt = next(iter(state['attempts'].values()))
        page = asyncio.run(cold.read_process_log(run['runId'], attempt['attemptId'], actor=USER, offset=0, limit=1024))
        assert 'shell-completed' in page['text']
        assert control(service, source['taskId'], 'run-now', actor=USER, request_key='opaque-shell-once')['runId'] == run['runId']
        assert (root / mapping['sourceFile']).read_bytes() == raw
        asyncio.run(cold.close())
    finally:
        gate.write_text('release')


def predecessor(shell_task):
    """Actual shared execution with an old journal, not constructed identities."""
    service, source, mapping, root, counter, gate = shell_task
    port = JsonTaskHandoff(service)
    cfg = port.preview([mapping], actor=USER)['entries'][0]['mapped']['job']
    jid = source['jobId']
    with service.transaction() as db:
        obj = service._envelope('job', cfg, USER, object_id=jid, state='enabled')
        job = {'jobId': jid, 'canonical': obj, 'config': cfg, 'configRevision': 1}
        aggregate(job, service.root)['profileId'] = service.profile_id
        service._save(db, 'job', jid, job)
        receipt = service._admit(db, job, {'expectedRevision': 1, 'input': {}}, USER, 'op_original_dispatch')
        job = service._get(db, 'job', jid)
        job['finalRuntime']['plans'][receipt['runId']]['dispatchKey'] = 'sch_original_json:manual:original'
        service._save(db, 'job', jid, job)
    gate.write_text('release')
    asyncio.run(finish(service, jid, receipt['runId']))
    runtime = service.final.store.get(jid)['finalRuntime']
    effect = runtime['effects'][0]
    from packages.core.platform.execution_host import supervisor_exit_evidence
    doc = ExecutionStore(service.root / 'executions').get(effect_id(effect['effectKey']))
    deadline = time.monotonic() + 10
    while not supervisor_exit_evidence(doc['supervisorIdentity'])['confirmed']:
        assert time.monotonic() < deadline
        time.sleep(.03)
    row = {'run_id': 'old-journal-id', 'task_id': source['taskId'],
           'dispatch_key': 'sch_original_json:manual:original', 'status': 'completed',
           'result': {'processJobId': effect_id(effect['effectKey']), 'exitCode': 0}}
    from packages.jobs.scheduled_records import append_run
    append_run(row, root=root / 'background_jobs')
    source.update(runCount=1, lastFireAt='2026-01-01T00:00:00', lastDelivery=row['result'])
    (root / mapping['sourceFile']).write_text(json.dumps(source), encoding='utf8')
    return service, source, mapping, runtime, row


def test_P6_JSON_RECEIPT_01_existing_original_dispatch_actual_receipt_adopt_no_new_run(shell_task):
    service, source, mapping, runtime, row = predecessor(shell_task)
    port = JsonTaskHandoff(service)
    original = port.preview([mapping], actor=USER)
    assert original['blocked']  # Original no-checkpoint gap retained.
    mapping['reconcileDispatches'] = True
    plan = port.preview([mapping], actor=USER)
    assert plan['blocked'] == [], plan['blocked']
    proof = plan['entries'][0]['mapped']['receiptReview']
    assert proof['matches'][0]['originalLogRunId'] == 'old-journal-id'
    assert proof['matches'][0]['runId'] == next(iter(runtime['runs']))
    before = service.final.store.get(source['jobId'])
    raw = (service.owner.root / mapping['sourceFile']).read_bytes()
    result = port.apply(plan, 'reconcile-original-shell', actor=USER)
    assert service.final.store.get(source['jobId']) == before
    from packages.scheduler.json_handoff import legacy_runs
    legacy = legacy_runs(service, source['taskId'], actor=USER)
    assert len(legacy) == 1 and legacy[0]['runId'] == 'old-journal-id'
    assert legacy[0]['canonicalRunId'] == next(iter(runtime['runs']))
    cold = FinalService(service.root, service.auth)
    try:
        state = cold.final.get(source['jobId'])
        assert state['runs'] == runtime['runs'] and state['attempts'] == runtime['attempts'] and state['effects'] == runtime['effects']
        assert cold.operation_for_key('create', 'reconcile-original-shell', actor=USER)['operationId'] == result['operationId']
    finally:
        asyncio.run(cold.close())
    assert shell_task[4].read_text().splitlines() == ['literal & {business}']
    assert (service.owner.root / mapping['sourceFile']).read_bytes() == raw


@pytest.mark.parametrize('failure', ['locator', 'log-unknown', 'receipt-unknown', 'count', 'pending', 'notice', 'lease', 'checkpoint'])
def test_P6_JSON_RECEIPT_02_unproven_outcomes_preserve_no_new_identity(shell_task, failure):
    service, source, mapping, runtime, row = predecessor(shell_task)
    mapping['reconcileDispatches'] = True
    port = JsonTaskHandoff(service)
    journal = service.owner.root / 'background_jobs' / 'runs.jsonl'
    if failure in {'locator', 'log-unknown'}:
        if failure == 'locator': row['result']['processJobId'] = 'unknown-original-process'
        else: row['status'] = 'unknown'
        journal.write_text(json.dumps(row) + '\n', encoding='utf8')
    elif failure == 'receipt-unknown':
        store = ExecutionStore(service.root / 'executions')
        doc = store.get(row['result']['processJobId'])
        doc['result']['outcome'] = 'unknown'
        with store.connect() as db:
            db.execute('UPDATE executions SET document=? WHERE effect_id=?', (fc.canonical_json(doc), doc['effectId']))
    elif failure == 'count': source['runCount'] = 2
    elif failure == 'pending': source['undeliveredFires'] = [{'dispatchKey': row['dispatch_key'], 'error': 'unknown'}]
    elif failure == 'notice': source['notificationState'] = 'pending'
    elif failure == 'checkpoint': source['finalRuntime'] = {'profileId': 'mismatched-original'}
    (service.owner.root / mapping['sourceFile']).write_text(json.dumps(source), encoding='utf8')
    before = service.final.store.get(source['jobId'])
    raw = (service.owner.root / mapping['sourceFile']).read_bytes()
    logs = journal.read_bytes()
    plan = port.preview([mapping], actor=USER)
    if failure == 'lease':
        assert plan['blocked'] == []
        with service.owner.transaction() as db: db.execute("INSERT INTO legacy_leases VALUES('unknown-live-writer')")
        with pytest.raises(fc.ContractError, match='Legacy pass'): port.apply(plan, 'must-not-adopt', actor=USER)
    else:
        assert plan['blocked']
        with pytest.raises(fc.ContractError): port.apply(plan, 'must-not-adopt', actor=USER)
    assert service.final.store.get(source['jobId']) == before
    assert (service.owner.root / mapping['sourceFile']).read_bytes() == raw and journal.read_bytes() == logs
    assert shell_task[4].read_text().splitlines() == ['literal & {business}']


def test_P6_JSON_SHELL_02_actual_running_resource_is_query_only_not_readopted(shell_task):
    service, source, mapping, root, counter, gate = shell_task
    # First create a real held process under the ordinary canonical owner.
    port = JsonTaskHandoff(service)
    port.apply(port.preview([mapping], actor=USER), 'live-shell-adopt', actor=USER)
    run = control(service, source['taskId'], 'run-now', actor=USER, request_key='live-shell-once')
    try:
        asyncio.run(service.dispatch_once())
        state = service.final.store.get(source['jobId'])['finalRuntime']
        assert state['runs'][run['runId']]['state'] == 'running'
        old_attempt = copy.deepcopy(state['attempts'])
        mapping['reconcileDispatches'] = True
        review = port.preview([mapping], actor=USER)
        assert review['blocked']  # No original completed journal/receipt exists.
        with pytest.raises(fc.ContractError): port.apply(review, 'cannot-readopt-resource', actor=USER)
        asyncio.run(service.recover_external_receipts())
        assert service.final.store.get(source['jobId'])['finalRuntime']['attempts'] == old_attempt
        gate.write_text('release')
        asyncio.run(finish(service, source['jobId'], run['runId']))
        assert counter.read_text().splitlines() == ['literal & {business}']
    finally:
        gate.write_text('release')


@pytest.mark.parametrize('intent', ['terminalOutbox', 'pendingRuns', 'execution', 'chainRecovery'])
def test_P6_JSON_RECEIPT_04_disabled_sibling_pending_intent_blocks_profile_cutover(shell_task, intent):
    service, source, mapping, root, counter, gate = shell_task
    sibling = copy.deepcopy(source)
    sibling.update(jobId='job_sibling', taskId='task_sibling', enabled=False, status='disabled')
    sibling[intent] = [{'identity': 'unsettled-original'}]
    path = (root / mapping['sourceFile']).parent / 'sibling.json'
    path.write_text(json.dumps(sibling), encoding='utf8')
    raw = path.read_bytes()
    port = JsonTaskHandoff(service)
    plan = port.preview([mapping], actor=USER)
    assert plan['blocked']
    with pytest.raises(fc.ContractError):
        port.apply(plan, 'must-not-freeze-sibling', actor=USER)
    assert path.read_bytes() == raw and not counter.exists()


def test_P6_JSON_OPERATOR_01_real_cli_dry_run_apply_status_cold_no_effect(shell_task, tmp_path):
    import subprocess
    service, source, mapping, root, counter, gate = shell_task
    service.auth.change_permissions(service.auth._default_permissions(), service.auth.permissions_status()['revision'])
    service.owner.claim(0)
    service.owner.set_state('active')
    service.owner.release()
    mappings = tmp_path / 'operator-mappings.json'
    plan_file = tmp_path / 'operator-plan.json'
    mappings.write_text(json.dumps([mapping]), encoding='utf8')
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/migrate_json_scheduler.py'),
        '--profile-root', str(root), '--canonical-root', str(service.root),
        '--authorization-root', str(root / 'automation')]
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    dry = subprocess.run([*command, '--mappings', str(mappings), '--output', str(plan_file)], capture_output=True, text=True)
    assert dry.returncode == 0, dry.stderr
    summary = json.loads(dry.stdout)
    assert summary['eligible'] == 1 and summary['blocked'] == []
    after = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert after == before
    bad = subprocess.run([*command, '--apply', '--plan', str(plan_file), '--expected-plan-digest', 'wrong',
        '--expected-owner-epoch', '1', '--request-key', 'operator-shell-adopt'], capture_output=True, text=True)
    assert bad.returncode == 2
    applied = subprocess.run([*command, '--apply', '--plan', str(plan_file), '--expected-plan-digest', summary['planDigest'],
        '--expected-owner-epoch', '1', '--request-key', 'operator-shell-adopt'], capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    receipt = json.loads(applied.stdout)
    status = subprocess.run([*command, '--status-key', 'operator-shell-adopt'], capture_output=True, text=True)
    assert status.returncode == 0 and json.loads(status.stdout)['operationId'] == receipt['operationId']
    missing = subprocess.run([*command, '--status-key', 'unknown-request'], capture_output=True, text=True)
    assert missing.returncode == 3 and json.loads(missing.stdout)['status'] == 'unknown'
    assert not counter.exists()  # Migration starts no provider or process.
    cold = FinalService(service.root, service.auth)
    try:
        assert cold.operation_for_key('create', 'operator-shell-adopt', actor=USER)['operationId'] == receipt['operationId']
        assert cold.final.get(source['jobId'])['runs'] == {}
    finally:
        asyncio.run(cold.close())


@pytest.fixture
def h(shell_task):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from packages.scheduler import api
    service, source, mapping, root, counter, gate = shell_task
    port = JsonTaskHandoff(service)
    port.apply(port.preview([mapping], actor=USER), 'transport-shell-adopt', actor=USER)
    app = FastAPI()
    app.state.job_final_service = service
    app.state.automation = SimpleNamespace(auth=service.auth)
    app.include_router(api.router)
    token = service.auth.login(service.auth.key())
    return SimpleNamespace(app=app, service=service, source=source, root=root, token=token,
                           counter=counter, gate=gate)


from tests.test_job_final_clients import tcp


def test_P6_JSON_SHELL_03_real_http_original_running_return_no_replay(h, tcp):
    import httpx
    path = '/api/scheduler/tasks/' + h.source['taskId']
    body = {'requestKey': 'http-actual-shell', 'waitForDispatch': True}
    try:
        with httpx.Client(base_url=tcp, trust_env=False, headers={'Authorization': 'Bearer ' + h.token}) as client:
            response = client.post(path + '/run-now', json=body)
            assert response.status_code == 200, response.text
            result = response.json()
            assert result['run']['status'] == 'running'
            process_id = result['run']['run']['result']['processJobId']
            assert process_id.startswith('effect_')
            again = client.post(path + '/run-now', json=body).json()
            assert again['operation']['operationId'] == result['operation']['operationId']
            assert again['run']['run']['result']['processJobId'] == process_id
            h.gate.write_text('release')
            asyncio.run(finish(h.service, h.source['jobId'], result['operation']['runId']))
            completed = client.post(path + '/run-now', json=body).json()
            assert completed['run']['status'] == 'completed'
            assert completed['run']['run']['result']['exitCode'] == 0
            assert client.get(path + '/runs').json()['runs'][0]['runId'] == result['operation']['runId']
        assert h.counter.read_text().splitlines() == ['literal & {business}']
    finally:
        h.gate.write_text('release')


def test_P6_JSON_SHELL_04_real_standalone_stdio_original_running_return(h, tmp_path):
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
    session = Session(id=h.source['targetSessionId'], name='Owned shell caller', adapter_config={
        'plugin_bindings': [{'plugin_id': 'pan', 'mode': 'standalone', 'enabled': True}]})
    manifest = ManifestConfig(mcp_servers=[McpServer('pan', command=sys.executable, args=['-m', 'packages.mcp.server'])])
    runtime = Runtime(lambda: manifest, lambda sid: session if sid == session.id else None, data_root=tmp_path / 'plugins')
    bind_objects(runtime, h.app)
    async def call():
        bridge = Bridge(runtime, tmp_path)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PAN_')}
        env.update(PAN_PLUGIN_IPC=bridge.address, PAN_AGENT_SESSION_ID=session.id, PAN_API_URL='http://127.0.0.1:1')
        try:
            params = StdioServerParameters(command=sys.executable, args=['-m', 'packages.mcp.server'], cwd=str(ROOT), env=env)
            async with stdio_client(params) as streams, ClientSession(*streams) as client:
                await client.initialize()
                args = {'task_id': h.source['taskId'], 'request_key': 'stdio-actual-shell', 'wait_for_dispatch': True}
                first = unpack(await client.call_tool('scheduler_run_now', args))
                again = unpack(await client.call_tool('scheduler_run_now', args))
                assert first['run']['status'] == again['run']['status'] == 'running'
                assert first['operation']['runId'] == again['operation']['runId']
                h.gate.write_text('release')
                await finish(h.service, h.source['jobId'], first['operation']['runId'])
        finally:
            await bridge.close()
    try:
        asyncio.run(call())
        assert h.counter.read_text().splitlines() == ['literal & {business}']
    finally:
        h.gate.write_text('release')


def test_P6_JSON_RECEIPT_03_actual_local_queue_original_locator_no_resend(json_task, monkeypatch):
    from packages.core import session, worker
    from packages.jobs.scheduled_records import append_run
    service, source, mapping, root = json_task
    monkeypatch.setattr(worker, '_shutdown_started', False)
    workers_before = set(worker.workers)
    port = JsonTaskHandoff(service)
    cfg = port.preview([mapping], actor=USER)['entries'][0]['mapped']['job']
    jid = source['jobId']
    with service.transaction() as db:
        obj = service._envelope('job', cfg, USER, object_id=jid, state='enabled')
        job = {'jobId': jid, 'canonical': obj, 'config': cfg, 'configRevision': 1}
        aggregate(job, service.root)['profileId'] = service.profile_id
        service._save(db, 'job', jid, job)
        admitted = service._admit(db, job, {'expectedRevision': 1, 'input': cfg['arguments']}, USER, 'op_original_queue')
        job = service._get(db, 'job', jid)
        job['finalRuntime']['plans'][admitted['runId']]['dispatchKey'] = 'original-queue-dispatch'
        service._save(db, 'job', jid, job)
    asyncio.run(service.dispatch_once())
    target = session.get(source['targetSessionId'], load_history=False)
    assert len(target.queue_pending) == 1
    queue_id = worker._queue_item_id(target.queue_pending[0])
    append_run({'run_id': 'old-queue-journal', 'task_id': source['taskId'], 'dispatch_key': 'original-queue-dispatch',
        'status': 'dispatched', 'result': {'status': 'queued', 'queueItemId': queue_id}}, root=root / 'background_jobs')
    source['runCount'] = 1
    (root / mapping['sourceFile']).write_text(json.dumps(source), encoding='utf8')
    mapping['reconcileDispatches'] = True
    plan = port.preview([mapping], actor=USER)
    assert plan['blocked'] == [], plan['blocked']
    assert plan['entries'][0]['mapped']['receiptReview']['matches'][0]['queueItemId'] == queue_id
    before = service.final.store.get(jid)
    port.apply(plan, 'reconcile-original-queue', actor=USER)
    assert service.final.store.get(jid) == before
    cold = FinalService(service.root, service.auth)
    try:
        assert cold.get_run(admitted['runId'], actor=USER)['state'] == 'completed'
    finally:
        asyncio.run(cold.close())
    assert len(session.get(source['targetSessionId'], load_history=False).queue_pending) == 1
    assert set(worker.workers) == workers_before
