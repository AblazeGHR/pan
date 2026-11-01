"""Owned authenticated real WebSocket: preparation versus accepted-send boundary."""
import json
import httpx
import pytest
from fastapi import WebSocket, Request, HTTPException, WebSocketDisconnect
from websockets.sync.client import connect
from tests.job_retirement_native_fixture import h as native_h  # noqa: F401
from tests.test_retirement_rev16_behavior import h  # noqa: F401
from tests.test_retirement_notification_runtime import completed
from tests.test_retirement_rev6_parity import tcp


@pytest.mark.parametrize('after_enqueue', [False, True])
def test_native_ws_preparation_retries_only_before_enqueue(h, monkeypatch, after_enqueue):
    from packages.web import server
    from packages.core import worker
    monkeypatch.setattr(worker, '_broadcast', None)
    monkeypatch.setattr(server, 'ws_clients', set())
    monkeypatch.setattr(server, 'agent_clients', set())
    monkeypatch.setattr(server, '_ws_outbound', {})
    job = completed(h, 'ses_b')
    h.service.outbox_sender = server._send_final_notice
    fail = {'value': True}
    calls = []
    prepare = server._attach_session_summary_patch
    enqueue = server._OutboundClient.enqueue
    def faulty_prepare(data):
        if fail['value'] and not after_enqueue:
            raise OSError('owned pre-enqueue preparation fault')
        return prepare(data)
    def faulty_enqueue(channel, data, **kwargs):
        result = enqueue(channel, data, **kwargs)
        calls.append(data['outboxId'])
        if fail['value'] and after_enqueue:
            raise OSError('owned acknowledgement lost after native enqueue')
        return result
    monkeypatch.setattr(server, '_attach_session_summary_patch', faulty_prepare)
    monkeypatch.setattr(server._OutboundClient, 'enqueue', faulty_enqueue)

    @h.app.websocket('/owned-notice-ws')
    async def socket(ws: WebSocket):
        if ws.headers.get('authorization') != 'Bearer ' + h.token:
            await ws.close(code=1008)
            return
        await ws.accept()
        server.ws_clients.add(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            server.ws_clients.discard(ws)
            channel = server._ws_outbound.pop(ws, None)
            if channel is not None:
                await channel.close_now()

    @h.app.post('/owned-notice-pulse')
    async def pulse(request: Request):
        if request.headers.get('authorization') != 'Bearer ' + h.token:
            raise HTTPException(401)
        await h.service.deliver_outbox()
        return {'ok': True}

    def row():
        return next(r for r in h.service.final.history(job['id'])['outbox'] if r['channel'] == 'ws')
    with tcp(h.app) as url, httpx.Client(base_url=url, trust_env=False,
            headers={'Authorization': 'Bearer ' + h.token}) as client:
        with connect(url.replace('http://', 'ws://') + '/owned-notice-ws',
                additional_headers={'Authorization': 'Bearer ' + h.token}) as ws:
            assert client.post('/owned-notice-pulse').status_code == 200
            original = row()
            assert original['status'] == ('uncertain' if after_enqueue else 'pending')
            if after_enqueue:
                frame = json.loads(ws.recv(timeout=3))
                assert frame['outboxId'] == original['outboxId'] and calls == [original['outboxId']]
            else:
                assert original['receipt']['phase'] == 'before-enqueue'
                assert original['receipt']['attempted'] is False and calls == []
                with pytest.raises(TimeoutError): ws.recv(timeout=.1)
            fail['value'] = False
            assert client.post('/owned-notice-pulse').status_code == 200
            if after_enqueue:
                assert row() == original
            else:
                frame = json.loads(ws.recv(timeout=3))
                assert frame['type'] == 'job.updated' and frame['jobId'] == job['id']
                assert frame['outboxId'] == original['outboxId']
                assert row()['status'] == 'acked' and calls == [original['outboxId']]
            assert client.post('/owned-notice-pulse').status_code == 200
            assert calls == [original['outboxId']]
            with pytest.raises(TimeoutError): ws.recv(timeout=.1)
