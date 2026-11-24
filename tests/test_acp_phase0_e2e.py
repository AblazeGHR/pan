"""ACP phase-zero observation against an owned Pan and synthetic provider."""
import json
import secrets
import socket
import threading
import time

import httpx
from websockets.sync.client import connect

from packages.automation.auth import UserAuth
from packages.acp.projection import TurnProjection
from tests import test_real_queue_http_e2e as h


def test_real_queue_stream_and_terminal_identity(tmp_path, monkeypatch):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    assert port not in (8767, 8768)
    monkeypatch.setattr(h, 'PORT', port)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', h.ROOT / 'tests/support/automation_http_server.py')
    root = tmp_path / 'owned'
    root.mkdir()
    (root / 'config.json').write_text(json.dumps({'qq': {'enabled': False}, 'wechat': {'enabled': False},
        'remote': {'enabled': False}, 'tailscale': {'startupEnabled': False}, 'lan': {'startupEnabled': False}}))
    auth = UserAuth(root / 'automation')
    password = 'Aa1!' + secrets.token_urlsafe(24)
    auth.initialize(password)
    token = auth.login(password)
    runtime = h._PanRuntime(root)
    events, ready, stop = [], threading.Event(), threading.Event()
    requested_ids = []
    errors = []
    def record():
        try:
            with connect(runtime.base_url.replace('http', 'ws', 1) + '/ws/agent',
                         additional_headers={'Authorization': 'Bearer ' + token}, open_timeout=10) as ws:
                ws.send(json.dumps({'type': 'subscribe', 'eventTypes': ['queue.item_delivered', 'worker.stream', 'worker.result'],
                                    'sessionIds': requested_ids}))
                while not stop.is_set():
                    try: event = json.loads(ws.recv(timeout=.2))
                    except TimeoutError: continue
                    if event.get('type') == 'subscribed': ready.set()
                    elif event.get('type') in ('queue.item_delivered', 'worker.stream', 'worker.result'): events.append(event)
        except Exception as exc:
            errors.append(type(exc).__name__)
            ready.set()
    thread = None
    try:
        runtime.start()
        with httpx.Client(base_url=runtime.base_url, headers={'Authorization': 'Bearer ' + token}, timeout=15, trust_env=False) as client:
            def request(method, path, **kwargs):
                response = client.request(method, path, **kwargs)
                assert response.status_code == 200, (response.status_code, response.text)
                return response.json()
            session = request('POST', '/api/sessions', json={'name': 'acp-phase0', 'adapter':'cbc',
                'model':'deepseek-v4-flash', 'permissionMode':'bypassPermissions', 'outputMode':'stream'})
            sid = session['id']
            other = request('POST', '/api/sessions', json={'name': 'acp-other', 'adapter':'cbc',
                'model':'deepseek-v4-flash', 'permissionMode':'bypassPermissions', 'outputMode':'stream'})
            requested_ids.append(sid)
            thread = threading.Thread(target=record, daemon=True)
            thread.start()
            assert ready.wait(10) and not errors
            request('POST', '/api/spawn', json={'sessionId': sid})
            observed = []
            for n in (1, 2):
                cmid = 'acp-phase0-' + str(n)
                accepted = request('POST', f'/api/sessions/{sid}/queue', json={'text': 'phase0-' + str(n), 'clientMessageId': cmid})
                qid = accepted['item']['queueItemId']
                delivered = h._wait_for(lambda: next((e for e in events if e.get('sessionId') == sid
                    and e.get('type') == 'queue.item_delivered' and qid in (e.get('queueItemIds') or [])), None), label='delivery')
                result = h._wait_for(lambda: next((e for e in events if e.get('sessionId') == sid
                    and e.get('type') == 'worker.result' and e.get('executionSeq') == delivered.get('executionSeq')), None), label='terminal')
                assert delivered.get('workerId') and type(delivered.get('generation')) is int and delivered.get('executionSeq')
                assert result.get('terminalKey') and result.get('executionSeq') == delivered['executionSeq']
                saved = h._wait_for(lambda: json.loads((root / 'sessions' / (sid + '.json')).read_text(encoding='utf-8')),
                                    label='persisted session')
                assert saved['queue_delivery_ledger'][qid]['deliveryState'] == 'sent_to_cli'
                receipt = saved['queue_delivery_ledger'][qid]
                assert receipt['executionSeq'] == result['executionSeq']
                assert receipt['workerId'] == result['workerId']
                assert receipt['generation'] == result['generation']
                duplicate = request('POST', f'/api/sessions/{sid}/queue', json={'text': 'phase0-' + str(n), 'clientMessageId': cmid})
                assert duplicate['item']['queueItemId'] == qid
                assert len(h._received(runtime, cwd=session['workdir'], text='phase0-' + str(n))) == 1
                streams = [e for e in events if e.get('sessionId') == sid and e.get('type') == 'worker.stream'
                           and e.get('executionSeq') == delivered['executionSeq']]
                projection = TurnProjection(sid, qid)
                chunks = []
                selected = [e for e in events if e.get('sessionId') == sid and
                            (e is delivered or e is result or e in streams)]
                for event in selected:
                    chunks.extend(projection.offer(event))
                assert ''.join(chunks) == 'ack:phase0-' + str(n)
                assert projection.terminal and projection.terminal['terminalKey'] == result['terminalKey']
                observed.append({'queue': qid, 'executionSeq': delivered['executionSeq'], 'streamCount': len(streams),
                    'streamBeforeReceipt': bool(streams and events.index(streams[0]) < events.index(delivered)),
                    'terminalKey': bool(result.get('terminalKey')), 'resultCursor': result.get('resultCursor'),
                    'streamTypes': [e.get('event', {}).get('type') for e in streams],
                    'streamShapes': [{'keys': sorted(e.get('event', {})), 'delta': e.get('event', {}).get('delta'),
                                      'messageKeys': sorted(e.get('event', {}).get('message', {}))
                                      if isinstance(e.get('event', {}).get('message'), dict) else []} for e in streams],
                    'receiptFields': sorted(saved['queue_delivery_ledger'][qid].keys()),
                    'terminalFields': sorted(result.keys())})
            assert observed[0]['executionSeq'] != observed[1]['executionSeq']
            assert all(row['streamCount'] for row in observed)
            saved = json.loads((root / 'sessions' / (sid + '.json')).read_text(encoding='utf-8'))
            for row in observed:
                receipt = saved['queue_delivery_ledger'][row['queue']]
                assert any(t.get('workerId') == receipt['workerId'] and t.get('generation') == receipt['generation']
                           and t.get('executionSeq') == receipt['executionSeq'] and t.get('terminalKey')
                           for t in saved['terminal_results'])
            request('POST', '/api/spawn', json={'sessionId': other['id']})
            request('POST', f"/api/sessions/{other['id']}/queue", json={'text':'other-synthetic', 'clientMessageId':'other-1'})
            h._wait_for(lambda: any(e.get('sessionId') == other['id'] and e.get('type') == 'worker.stream'
                                    for e in events), label='cross-session generic websocket stream')
            h._wait_for(lambda: h._received(runtime, cwd=other['workdir'], text='other-synthetic'), label='other provider delivery')
            assert not any(e.get('sessionId') == other['id'] and e.get('type') == 'worker.result' for e in events)
            print('ACP phase0 synthetic identity:', json.dumps(observed))
        stop.set()
        thread.join(timeout=5)
        assert not thread.is_alive() and not errors
        thread = None
        runtime.stop()
        runtime.start()
        with httpx.Client(base_url=runtime.base_url, headers={'Authorization': 'Bearer ' + token}, timeout=15, trust_env=False) as client:
            retry = client.post(f'/api/sessions/{sid}/queue', json={'text':'phase0-2', 'clientMessageId':'acp-phase0-2'})
            assert retry.status_code == 200 and retry.json()['item']['queueItemId'] == observed[1]['queue']
            saved = json.loads((root / 'sessions' / (sid + '.json')).read_text(encoding='utf-8'))
            receipt = saved['queue_delivery_ledger'][observed[1]['queue']]
            assert receipt['executionSeq'] == observed[1]['executionSeq']
            assert any(t.get('executionSeq') == receipt['executionSeq'] and t.get('terminalKey')
                       for t in saved['terminal_results'])
            assert len(h._received(runtime, cwd=session['workdir'], text='phase0-2')) == 1
    finally:
        stop.set()
        if thread: thread.join(timeout=5)
        runtime.stop()
    assert not errors and h._port_is_free(port)
