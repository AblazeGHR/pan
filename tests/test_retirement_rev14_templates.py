"""Real owned HTTP + existing composition/queue/process; no legacy dispatch."""
import asyncio
import json
import sys
import time

import pytest

from packages.jobs.final_service import FinalService, definition
import copy
from packages.jobs import final_contracts as fc
from packages.jobs.final_runtime.ledger import aggregate
from packages.core import session, worker
from tests.test_job_automatic_recovery import env  # owned default process host
from tests.test_job_final_http_service import USER, control, launch


@pytest.fixture
def h(env):
    # Real WorkerAssignProvider/worker.assign/storage. Paused queues make the
    # original _wake_worker return without spawning; no provider/Worker mocks.
    env.owned = {}
    for sid in ('ses_a', 'ses_source'):
        target = session.Session(id=sid, name=sid, workdir=str(env.tmp), queue_paused=True)
        session.save(target)
        env.owned[sid] = target
    env.automation = env.app.state.automation
    return env


def form_body(client, kind="assign", fields=None, **extra):
    catalog = client.get('/api/jobs/actions').json()
    aid = 'product_assign_v2' if kind == 'assign' else 'product_process_logs_v1'
    action = next(a for a in catalog['items'] if a['id'] == aid)
    job = {'name': 'Complete ' + kind, 'steps': [{'stepId': 'effect', 'actionRef': catalog['references'][aid],
        'arguments': {}, 'completion': {'contract': action['defaultCompletion']}}], 'retention': {'policyRef': 'default'}}
    return {'definition': {'name': 'Passive ' + kind, 'jobDefinition': job,
            'composition': {'creators': [], 'triggers': []}},
        'form': {'kind': kind, 'fields': fields or {'sessionId': 'ses_a', 'text': '  中文\nraw  '}},
        'expectedRevision': 0, 'requestKey': 'create-form', **extra}


def publish(client, item):
    response = client.post('/api/job-templates/' + item['id'] + '/operations', json={
        'operation': 'publish', 'expectedRevision': item['revision'], 'expectedStateRevision': item['stateRevision'],
        'parameters': {}, 'requestKey': 'publish-' + item['id']})
    assert response.status_code == 200, response.text
    return response.json()['result']['object']


def create_template(client, body):
    response = client.post('/api/job-templates', json=body)
    assert response.status_code == 201, response.text
    return publish(client, response.json()['result']['object'])


def instantiate(client, item, **extra):
    return client.post('/api/job-templates/' + item['id'] + '/instantiate', json={
        'expectedRevision': item['revision'], 'requestKey': 'instantiate', **extra})


def counts(service):
    with service.transaction(readonly=True) as db:
        return [db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                for table in ('final_documents', 'final_operations', 'compositions')]


def test_zero_creator_complete_template_default_disabled_same_key_cold_reopen(h):
    body = form_body(h.client)
    # A complete canonical definition works without the convenience envelope.
    del body['form']
    body['definition']['jobDefinition']['steps'][0]['arguments'] = {
        'sessionId': {'from': 'literal', 'value': 'ses_a'}, 'text': {'from': 'literal', 'value': 'raw'}}
    item = create_template(h.client, body)
    before = set(worker.workers)
    first = instantiate(h.client, item)
    assert first.status_code == 201, first.text
    receipt = first.json()
    ids = receipt['result']['objectIds']
    assert ids['creators'] == ids['triggers'] == {}
    job = h.client.get('/api/jobs/' + ids['job']).json()
    assert job['state'] == 'disabled'
    assert h.client.get('/api/jobs/' + ids['job'] + '/runs').json()['items'] == []
    assert h.service.creators.list() == [] and not h.owned['ses_a'].queue_pending
    assert set(worker.workers) == before
    assert instantiate(h.client, item).json()['operationId'] == receipt['operationId']
    assert instantiate(h.client, item, activate=True).status_code == 409
    assert instantiate(h.client, item, expectedRevision=item['revision'] + 1, requestKey='stale').status_code == 409
    cold = FinalService(h.root, h.automation.auth)
    try:
        again = cold.instantiate_template(item['id'], {'expectedRevision': item['revision'], 'requestKey': 'instantiate'}, actor=USER)
        assert again['operationId'] == receipt['operationId'] and again['result']['objectIds'] == ids
        assert cold.operation_for_key('instantiate-template', 'instantiate', actor=USER) == receipt
        assert cold.creators.list() == []
    finally:
        asyncio.run(cold.close())


def test_production_form_assign_queue_source_frozen_identity_cold_no_duplicate(h):
    fields = {'target': {'sessionId': 'ses_a'}, 'message': '  中文\nraw  ',
              'source': 'agent', 'sourceSessionId': 'ses_source', 'taskId': 'owned-r14-task'}
    body = form_body(h.client, fields=fields)
    item = create_template(h.client, body)
    assert 'form' not in item
    step = item['jobDefinition']['steps'][0]
    assert step['arguments']['text']['value'] == fields['message']
    assert step['arguments']['sessionId']['value'] == 'ses_a'
    assert h.client.post('/api/job-templates', json=body).status_code == 200
    immutable = h.client.put('/api/job-templates/' + item['id'], json={
        'definition': body['definition'], 'expectedRevision': item['revision'], 'requestKey': 'edit-published'})
    assert immutable.status_code == 409
    result = instantiate(h.client, item, activate=True)
    assert result.status_code == 201, result.text
    jid = result.json()['result']['objectIds']['job']
    job = h.client.get('/api/jobs/' + jid).json()
    admitted = launch(h, job)
    before_plan = copy.deepcopy(aggregate(h.service.final.store.get(jid), h.root)['plans'])
    edited = definition('job', job)
    edited['steps'][0]['arguments']['sessionId']['value'] = 'ses_source'
    edited['steps'][0]['arguments']['text']['value'] = 'edited after admission'
    changed = h.client.put('/api/jobs/' + jid, json={'definition': edited,
        'expectedRevision': job['configRevision'], 'requestKey': 'edit-after-admission'})
    assert changed.status_code == 200, changed.text
    assert aggregate(h.service.final.store.get(jid), h.root)['plans'] == before_plan
    workers_before = set(worker.workers)
    asyncio.run(h.service.dispatch_once())
    run = h.client.get('/api/job-runs/' + admitted['runId']).json()
    assert run['state'] == 'completed'
    row = session.get('ses_a').queue_pending[0]
    assert (row['text'], row['source'], row['sourceSessionId'], row['taskId']) == (
        fields['message'], 'agent', 'ses_source', 'owned-r14-task')
    assert set(worker.workers) == workers_before
    assert session.get('ses_source').queue_pending == []
    history = h.service.final.history(jid)
    assert len(history['effects']) == 1
    cold = FinalService(h.root, h.automation.auth)
    try:
        assert cold.final.query_original(row['taskId'])[0]['runs'][0]['runId'] == admitted['runId']
        asyncio.run(cold.dispatch_once())
        assert len(session.get('ses_a').queue_pending) == 1
        assert cold.final.history(jid)['effects'] == history['effects']
    finally:
        asyncio.run(cold.close())


@pytest.mark.parametrize('case', ['missing-session', 'stale-ref', 'conflict', 'unknown', 'dynamic', 'custom', 'missing-key'])
def test_form_rejection_no_definition_operation_or_composition(h, case):
    body = form_body(h.client)
    step = body['definition']['jobDefinition']['steps'][0]
    if case == 'missing-session': body['form']['fields']['sessionId'] = 'ses_absent'
    if case == 'stale-ref': step['actionRef']['digest'] = '0' * 64
    if case == 'conflict': step['arguments']['text'] = {'from': 'literal', 'value': 'different'}
    if case == 'unknown': body['form']['fields']['extra'] = True
    if case == 'dynamic': step['arguments']['text'] = {'from': 'input', 'path': 'text'}
    if case == 'custom': step['actionRef']['id'] = 'custom'
    if case == 'missing-key': del body['requestKey']
    before = counts(h.service)
    result = h.client.post('/api/job-templates', json=body)
    assert result.status_code in (400, 404, 409, 422), result.text
    assert counts(h.service) == before
    assert not h.owned['ses_a'].queue_pending


def test_session_deleted_after_publish_blocks_instantiate_without_writes(h):
    body = form_body(h.client)
    item = create_template(h.client, body)
    session.delete('ses_a')
    before = counts(h.service)
    assert h.client.post('/api/job-templates', json=body).status_code == 200
    assert counts(h.service) == before
    response = instantiate(h.client, item)
    assert response.status_code == 400 and response.json()['error']['code'] == 'invalid_schema', response.text
    assert counts(h.service) == before


def test_real_grants_passive_activation_revoke_and_source_authority(h):
    import httpx
    from packages.automation import authorization
    item = create_template(h.client, form_body(h.client))
    async def trusted(scope, receive, send):
        scope[authorization._SCOPE_KEY] = authorization._MARKER
        await h.app(scope, receive, send)
    async def check():
        grants = h.auth._default_permissions()
        url = '/api/job-templates/' + item['id'] + '/instantiate'
        body = {'expectedRevision': item['revision'], 'requestKey': 'delegated'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=trusted), base_url='http://owned') as client:
            before = counts(h.service)
            assert (await client.post(url, json=body)).status_code == 403
            assert counts(h.service) == before
            for row, cap in [('jobTemplate', 'use'), ('job', 'create')]: grants[row][cap] = True
            h.auth.change_permissions(grants, h.auth.permissions_status()['revision'])
            passive = await client.post(url, json=body)
            assert passive.status_code == 201, passive.text
            before = counts(h.service)
            assert (await client.post(url, json={**body, 'requestKey': 'activate', 'activate': True})).status_code == 403
            assert counts(h.service) == before
            for cap in ('lifecycle', 'execute'): grants['job'][cap] = True
            h.auth.change_permissions(grants, h.auth.permissions_status()['revision'])
            active = await client.post(url, json={**body, 'requestKey': 'activate', 'activate': True})
            assert active.status_code == 201, active.text
            jid = active.json()['result']['objectIds']['job']
            assert h.client.get('/api/jobs/' + jid).json()['state'] == 'enabled'
            assert h.client.get('/api/jobs/' + jid + '/runs').json()['items'] == []
            grants['jobTemplate']['create'] = True
            h.auth.change_permissions(grants, h.auth.permissions_status()['revision'])
            forged = form_body(h.client, fields={'sessionId': 'ses_a', 'text': 'x', 'source': 'agent', 'sourceSessionId': 'ses_source'})
            before = counts(h.service)
            assert (await client.post('/api/job-templates', json=forged)).status_code == 403
            assert counts(h.service) == before
            grants['jobTemplate']['use'] = False
            h.auth.change_permissions(grants, h.auth.permissions_status()['revision'])
            assert (await client.post(url, json={**body, 'requestKey': 'revoked'})).status_code == 403
    asyncio.run(check())


def test_passive_instantiate_failure_rolls_back_and_recovers_same_key(h, monkeypatch):
    item = create_template(h.client, form_body(h.client))
    original = h.service.compositions._checkpoint
    def fail(operation, phase):
        result = original(operation, phase)
        if phase == 'links_verified': raise OSError('owned failure')
        return result
    before = counts(h.service)
    monkeypatch.setattr(h.service.compositions, '_checkpoint', fail)
    assert instantiate(h.client, item).status_code == 503
    assert counts(h.service) == before
    monkeypatch.setattr(h.service.compositions, '_checkpoint', original)
    assert instantiate(h.client, item).status_code == 201


def test_production_shell_form_real_process_exit_and_original_lookup(env):
    marker = env.tmp / 'shell-result.txt'
    command = f'"{sys.executable}" -c "from pathlib import Path; Path(\'shell-result.txt\').write_text(\'owned\')"'
    body = form_body(env.client, kind='shell', fields={'command': command, 'cwd': str(env.tmp), 'hideWindow': True})
    item = create_template(env.client, body)
    argv = item['jobDefinition']['steps'][0]['arguments']['argv']['value']
    assert argv[-1] == command
    response = instantiate(env.client, item)
    assert response.status_code == 201, response.text
    jid = response.json()['result']['objectIds']['job']
    job = env.client.get('/api/jobs/' + jid).json()
    assert job['state'] == 'disabled' and not marker.exists()
    assert env.client.get('/api/jobs/' + jid + '/runs').json()['items'] == []
    response = control(env, 'jobs', job, 'enable')
    assert response.status_code == 200, response.text
    admitted = launch(env, response.json()['result']['object'])
    for _ in range(400):
        asyncio.run(env.service.dispatch_once())
        if env.client.get('/api/job-runs/' + admitted['runId']).json()['state'] in {'completed', 'failed'}: break
        time.sleep(.025)
    history = env.service.final.history(jid)
    logs = asyncio.run(env.service.read_process_log(admitted['runId'], history['effects'][0]['attemptId'], actor=USER, limit=65536))
    assert env.client.get('/api/job-runs/' + admitted['runId']).json()['state'] == 'completed', json.dumps({'history': history, 'log': logs}, ensure_ascii=True)
    assert marker.read_text() == 'owned'
    history = env.service.final.history(jid)
    assert len(history['effects']) == 1
    cold = FinalService(env.root, env.auth)
    try:
        key = history['effects'][0]['effectKey']
        assert cold.final.query_original(fc.digest(key))[0]['runs'][0]['runId'] == admitted['runId']
        asyncio.run(cold.dispatch_once())
        assert cold.final.history(jid)['effects'] == history['effects']
    finally:
        asyncio.run(cold.close())


def test_fresh_production_browser_real_template_http_queue_and_shell(h):
    from pathlib import Path
    import socket
    import subprocess
    import threading
    import uvicorn
    from starlette.staticfiles import StaticFiles
    root = Path(__file__).resolve().parents[1]
    runner = root / 'packages/web/e2e/retirement-r14-production.mjs'
    built = subprocess.run(['node', str(runner), '--build'], cwd=root, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=100)
    assert built.returncode == 0, built.stdout + built.stderr
    h.app.mount('/react', StaticFiles(directory=root / 'packages/web/test-results/retirement-r14-production', html=True))
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    assert port not in {8767, 8768}
    server = uvicorn.Server(uvicorn.Config(h.app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started: break
            time.sleep(.025)
        assert server.started
        browser_config = h.tmp / 'browser-config.json'
        raw = '  Browser 中文\nraw  '
        command = 'echo owned>>browser-shell-result.txt'
        browser_config.write_text(json.dumps({'url': f'http://127.0.0.1:{port}', 'token': h.client.headers['Authorization'][7:],
            'sessionId': 'ses_a', 'text': raw, 'command': command, 'cwd': str(h.tmp)}), encoding='utf-8')
        before_workers = set(worker.workers)
        result = subprocess.run(['node', str(runner), str(browser_config)], cwd=root, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=100)
        assert result.returncode == 0, result.stdout + result.stderr
        evidence = json.loads(result.stdout.strip().splitlines()[-1])
        assert len(evidence['instances']) == 2 and evidence['pageErrors'] == []
        assert not session.get('ses_a').queue_pending and not (h.tmp / 'browser-shell-result.txt').exists()
        histories = []
        for instance in evidence['instances']:
            assert instance['creators'] == instance['triggers'] == {}
            jid = instance['job']
            job = h.client.get('/api/jobs/' + jid).json()
            assert job['state'] == 'disabled'
            assert h.client.get('/api/jobs/' + jid + '/runs').json()['items'] == []
            assert job['steps'][0]['arguments']
            enabled = control(h, 'jobs', job, 'enable', key='enable-' + instance['kind'])
            assert enabled.status_code == 200, enabled.text
            run = launch(h, enabled.json()['result']['object'], key='browser-' + instance['kind'])
            for _ in range(400):
                asyncio.run(h.service.dispatch_once())
                current = h.client.get('/api/job-runs/' + run['runId']).json()
                if current['state'] in {'completed', 'failed'}: break
                time.sleep(.025)
            assert current['state'] == 'completed', current
            history = h.service.final.history(jid)
            assert len(history['effects']) == 1
            histories.append((jid, run['runId'], history))
        assert session.get('ses_a').queue_pending[0]['text'] == raw
        assert len(session.get('ses_a').queue_pending) == 1
        assert (h.tmp / 'browser-shell-result.txt').read_text().splitlines() == ['owned']
        assert set(worker.workers) == before_workers
        cold = FinalService(h.root, h.auth)
        try:
            for jid, rid, history in histories:
                key = fc.digest(history['effects'][0]['effectKey'])
                assert cold.final.query_original(key)[0]['runs'][0]['runId'] == rid
                assert cold.final.history(jid)['effects'] == history['effects']
            asyncio.run(cold.dispatch_once())
            assert len(session.get('ses_a').queue_pending) == 1
            assert (h.tmp / 'browser-shell-result.txt').read_text().splitlines() == ['owned']
        finally:
            asyncio.run(cold.close())
        evidence.update(port=port, realEffects=2, queueRows=1, shellLines=1, coldLookup=True, noDuplicate=True)
        print('R14_BROWSER=' + json.dumps(evidence))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
