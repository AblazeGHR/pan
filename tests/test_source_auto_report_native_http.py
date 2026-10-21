"""Real socket/WS + deterministic native control fixture for integration."""
import json
import time

import pytest

from tests.test_auto_force_http_e2e import (
    make_runtime, _create, _claim, _spawn, _send, _enable, _events,
    _wait_received, _wait_for, _steer_texts,
)


@pytest.mark.timeout(90)
def test_upstream_report_consume_and_auto_force_use_original_native_transaction(make_runtime, record_property):
    runtime = make_runtime(steer=True)
    recorder = _events(runtime)
    record_property('owned_port', runtime.port)
    record_property('owned_pid_identities', json.dumps(runtime._owned))
    def request(method, path, body=None, **extra):
        return runtime.request(method, path, **({'json': body} if body is not None else {}), **extra)
    def stored(sid):
        return json.loads((runtime.data_root / 'sessions' / f'{sid}.json').read_text(encoding='utf-8'))
    def consume(sid, item_id=None):
        return request('POST', '/api/self-queue/report-consume',
                       {'itemId': item_id} if item_id else {}, headers={'X-Pan-Queue-Caller': sid})
    try:
        root = _create(runtime, 'root')
        manager = _create(runtime, 'native-source-manager')
        child = _create(runtime, 'child')
        _claim(runtime, root['id'], manager['id']); _claim(runtime, manager['id'], child['id'])
        request('PATCH', f"/api/sessions/{root['id']}/queue/paused", {'paused': True})
        _spawn(runtime, manager['id'])
        _send(runtime, manager['id'], 'hold-report')
        _wait_received(runtime, 'hold-report')
        time.sleep(.4)  # fixture's running frame must reach the actual native run gate
        first = _send(runtime, manager['id'], 'EXPLICIT NATIVE BODY', source='agent',
                      sourceSessionId=child['id'], clientMessageId='explicit-consume')
        assert first['status'] == 'queued'
        item = _wait_for(lambda: next((r for r in stored(manager['id'])['queue_pending']
            if r.get('clientMessageId') == 'explicit-consume'), None), label='durable upstream task')
        assert item['source'] == 'report' and item['type'] == 'task'
        projection = request('POST', '/api/self-queue/read', {}, headers={'X-Pan-Queue-Caller': manager['id']})
        assert 'EXPLICIT NATIVE BODY' not in json.dumps(projection)
        assert next(r for r in projection['items'] if r['id'] == item['id']) == {
            'id': item['id'], 'queueItemId': item['id'], 'type': 'report', 'source': 'report'}
        consumed = consume(manager['id'])
        assert consumed['status'] == 'accepted' and consumed['queueItemIds'] == [item['id']]
        assert 'EXPLICIT NATIVE BODY' not in json.dumps(consumed)
        assert _steer_texts(runtime) == ['EXPLICIT NATIVE BODY']
        assert consume(manager['id'], item['id']) == consumed
        assert _steer_texts(runtime) == ['EXPLICIT NATIVE BODY']
        runtime.gates['hold-report'].touch()
        _wait_for(lambda: stored(manager['id']).get('terminal_results'), label='first durable result')
        assert stored(manager['id'])['terminal_results'][-1]['autoReportAdmission']['enabled'] is False
        assert stored(root['id'])['queue_pending'] == []

        # Auto Force's Report switch must classify explicit upstream task envelopes
        # independently of MA. Formal assign still cannot be injected by it.
        _send(runtime, manager['id'], 'hold-auto')
        _wait_received(runtime, 'hold-auto')
        time.sleep(.4)
        _enable(runtime, report=True, ma=False)
        _send(runtime, manager['id'], 'AUTO FORCE UPSTREAM', source='agent',
              sourceSessionId=child['id'], clientMessageId='explicit-auto-force')
        auto_item = _wait_for(lambda: recorder.find_added(manager['id'], 'AUTO FORCE UPSTREAM'), label='original auto Force ID')
        _wait_for(lambda: 'AUTO FORCE UPSTREAM' in _steer_texts(runtime), label='Report auto Force native control')
        _wait_for(lambda: auto_item['queueItemId'] in recorder.delivered_ids(manager['id']), label='same original delivered ID')
        formal = request('POST', '/api/assign', {'sessionId': manager['id'], 'sourceSessionId': child['id'],
            'text': 'FORMAL WAIT', 'taskId': 'FORMAL-UPSTREAM'})
        assert formal['status'] == 'queued'
        request('PATCH', f"/api/sessions/{manager['id']}/queue/paused", {'paused': True})
        assert (request('POST', '/api/assign', {'sessionId': manager['id'], 'sourceSessionId': child['id'],
            'text': 'FORMAL WAIT', 'taskId': 'FORMAL-UPSTREAM'}))['duplicate']
        assert _steer_texts(runtime) == ['EXPLICIT NATIVE BODY', 'AUTO FORCE UPSTREAM']
        assert len([r for r in stored(manager['id'])['queue_pending'] if r.get('taskId') == 'FORMAL-UPSTREAM']) == 1
        runtime.gates['hold-auto'].touch()
        _wait_for(lambda: len(stored(manager['id'])['terminal_results']) == 2, label='second durable result')
        assert all(not t['autoReportAdmission']['enabled'] for t in stored(manager['id'])['terminal_results'])
        assert stored(root['id'])['queue_pending'] == []
        assert recorder.count('worker.restarted', manager['id']) == 0
        assert not [r for r in runtime.records('received') if r.get('text') in {'EXPLICIT NATIVE BODY', 'AUTO FORCE UPSTREAM', 'FORMAL WAIT'}]
        record_property('original_ids', json.dumps([item['id'], auto_item['queueItemId']]))
        record_property('native_wire_messages', json.dumps(_steer_texts(runtime)))
    finally:
        recorder.stop()
