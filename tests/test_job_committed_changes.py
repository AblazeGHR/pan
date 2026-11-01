"""Post-commit canonical metadata/runtime observations, never execution authority."""
import copy
import pytest
from tests.job_retirement_native_fixture import h as native_h  # noqa: F401
from tests.test_retirement_rev16_behavior import h  # noqa: F401
from tests.test_retirement_session_runtime import admitted, edit
from packages.jobs.final_service import definition
from tests.test_job_final_http_service import control


@pytest.mark.parametrize('op', ['session-message', 'session-broadcast'])
def test_committed_lifecycle_snapshots_and_rollback_never_publish(h, monkeypatch, op):
    observations = []
    h.service.on_committed_change = observations.append
    job, rid, _ = admitted(h, op)
    assert observations, 'canonical creation/admission must publish after commit'
    original = copy.deepcopy(observations)
    target = ['ses_b'] if op == 'session-broadcast' else 'ses_b'
    job = edit(h, job, target, 'changed body', 'new-target')
    assert observations[:len(original)] == original
    assert any(row['object']['arguments']['delivery']['target'] == target for row in observations)
    before = copy.deepcopy(observations)
    with pytest.raises(RuntimeError, match='rollback'):
        with h.service.transaction() as db:
            raw = h.service._get(db, 'job', job['id'])
            raw['canonical']['name'] = 'must not publish'
            h.service._save(db, 'job', job['id'], raw)
            raise RuntimeError('rollback')
    assert observations == before
    assert h.client.get('/api/jobs/' + job['id']).json()['name'] != 'must not publish'
    import asyncio
    provider = h.service.executor.session_provider
    original_send = provider.send_session
    async def checked_send(*args, **kwargs):
        assert any(run['runId'] == rid and run['state'] == 'running'
            for change in observations for run in change.get('runs', [])), 'publish running before real enqueue'
        return await original_send(*args, **kwargs)
    monkeypatch.setattr(provider, 'send_session', checked_send)
    asyncio.run(h.service.dispatch_once())
    states = [run['state'] for row in observations for run in row.get('runs', []) if run['runId'] == rid]
    assert 'running' in states and 'completed' in states
    completed = copy.deepcopy(observations)
    value = {'expectedRevision': job['configRevision'], 'requestKey': 'second-run',
        'input': job['arguments'], 'cause': {'type': 'manual'}}
    response = h.client.post('/api/jobs/' + job['id'] + '/runs', json=value)
    assert response.status_code == 202, response.text
    second = response.json()['runId']
    run = h.client.get('/api/job-runs/' + second).json()
    response = h.client.post('/api/job-runs/' + second + '/operations', json={'operation':'cancel',
        'expectedStateRevision':run['stateRevision'], 'requestKey':'cancel-unstarted'})
    assert response.status_code in {200, 202}, response.text
    asyncio.run(h.service.dispatch_once())
    assert h.service.final.get(job['id'])['runs'][second]['state'] == 'cancelled'
    assert any(r['runId'] == second and r['state'] == 'cancelled' for change in observations for r in change.get('runs', []))
    result = control(h, 'jobs', job, 'retire', 'retire')
    assert result.status_code == 200, result.text
    assert observations[-1]['object']['state'] == 'retired'
    assert observations[:len(completed)] == completed


@pytest.mark.parametrize('op', ['session-message', 'session-broadcast'])
def test_actual_ws_committed_change_refresh_has_no_private_payload(h, monkeypatch, op):
    import asyncio
    import json
    import httpx
    from fastapi import WebSocket, Request, WebSocketDisconnect, HTTPException
    from websockets.sync.client import connect
    from packages.web import server
    from tests.test_retirement_rev6_parity import tcp
    monkeypatch.setattr(server, 'ws_clients', set())
    monkeypatch.setattr(server, 'agent_clients', set())
    monkeypatch.setattr(server, '_ws_outbound', {})
    @h.app.websocket('/owned-change-ws')
    async def socket(ws: WebSocket):
        await ws.accept()
        server.ws_clients.add(ws)
        try:
            while True: await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            server.ws_clients.discard(ws)
            channel = server._ws_outbound.pop(ws, None)
            if channel: await channel.close_now()
    @h.app.post('/owned-bind-changes')
    async def bind(request: Request):
        if request.headers.get('authorization') != 'Bearer ' + h.token: raise HTTPException(401)
        server._bind_final_change_notifications(h.service)
        return {'ok': True}
    @h.app.post('/owned-dispatch')
    async def dispatch(request: Request):
        if request.headers.get('authorization') != 'Bearer ' + h.token: raise HTTPException(401)
        await h.service.dispatch_once()
        return {'ok': True}
    with tcp(h.app) as url, httpx.Client(base_url=url, trust_env=False,
            headers={'Authorization': 'Bearer ' + h.token}) as client:
        assert client.post('/owned-bind-changes').status_code == 200
        with connect(url.replace('http://', 'ws://') + '/owned-change-ws') as ws:
            job, rid, _ = admitted(h, op)
            def refresh_until(predicate):
                for _ in range(12):
                    event = json.loads(ws.recv(timeout=3))
                    assert event['type'] == 'canonical.changed'
                    assert not ({'jobId', 'object', 'runs', 'arguments', 'name'} & set(event))
                    rows = client.get('/api/jobs').json()['items']
                    if predicate(rows): return rows
                pytest.fail('committed update not reflected in authoritative HTTP read')
            rows = refresh_until(lambda rows: any(r['id'] == job['id'] for r in rows))
            original = copy.deepcopy(rows)
            target = ['ses_b'] if op == 'session-broadcast' else 'ses_b'
            changed = edit(h, job, target, 'private body never in WS', 'ws-edit')
            refresh_until(lambda rows: rows[0]['configRevision'] == changed['configRevision'])
            assert original[0]['arguments']['delivery']['target'] != target
            assert client.get('/api/jobs', headers={'Authorization':'Bearer invalid'}).status_code == 401
            assert client.post('/owned-dispatch').status_code == 200
            refresh_until(lambda _: h.service.final.get(job['id'])['runs'][rid]['state'] == 'completed')
            assert h.service.final.get(job['id'])['runs'][rid]['state'] == 'completed'
            response = control(h, 'jobs', changed, 'retire', 'ws-retire')
            assert response.status_code == 200
            refresh_until(lambda rows: rows[0]['state'] == 'retired')


def test_borrowed_creator_transaction_publishes_only_after_actual_outer_commit(h):
    observations = []
    h.service.on_committed_change = observations.append
    job, _, _ = admitted(h)
    observations.clear()
    with pytest.raises(RuntimeError):
        with h.service.creators.transaction():
            _direct_edit(h, job, 'rolled back', 'outer-fail')
            assert observations == []
            raise RuntimeError('outer rollback')
    assert observations == []
    assert h.client.get('/api/jobs/' + job['id']).json()['arguments']['delivery']['text'] == 'original'
    with h.service.creators.transaction():
        _direct_edit(h, job, 'committed', 'outer-commit')
        assert observations == []
    assert observations[-1]['object']['arguments']['delivery']['text'] == 'committed'


def _direct_edit(h, job, text, key):
    cfg = definition('job', job)
    cfg['arguments']['delivery'] = {'target': 'ses_b', 'text': text}
    return h.service.write('job', job['id'], {'definition': cfg,
        'expectedRevision': job['configRevision'], 'requestKey': key}, actor={'type':'user','id':'local'})


def test_publication_failure_is_diagnostic_and_never_rolls_back_committed_write(h):
    def broken(_snapshot):
        raise OSError('owned observer unavailable')
    h.service.on_committed_change = broken
    job, rid, _ = admitted(h)
    assert h.client.get('/api/jobs/' + job['id']).status_code == 200
    assert h.service.final.get(job['id'])['runs'][rid]['state'] == 'queued'
    assert any(d['code']=='change_publication_failed' and 'owned observer unavailable' in d['message']
        for d in h.service.diagnostics)
    assert not h.service.final.get(job['id'])['effects']
