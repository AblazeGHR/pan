"""Real owned FastAPI + Node peers + fake CLI; isolated ports and data only."""
from dataclasses import asdict
import json
from pathlib import Path

import psutil

from tests import test_real_queue_http_e2e as harness
from tests.test_automation_core import action, trigger
from tests.test_plugin_events import manifest, records, publish


def test_real_http_plugin_lifecycle_and_restart(tmp_path, monkeypatch):
    assert harness.PORT not in {8767, 8768}
    monkeypatch.setattr(harness, 'SERVER_LAUNCHER', Path(__file__).parent / 'support/automation_http_server.py')
    runtime = harness._PanRuntime(tmp_path / 'owned-data')
    runtime.data_root.mkdir()
    sentinel = tmp_path / 'simulated-practical/sentinel'
    sentinel.parent.mkdir()
    sentinel.write_bytes(b'untouched')
    monkeypatch.setenv('PAN_BACKGROUND_JOBS_DIR', str(sentinel.parent))
    m = manifest()
    file = tmp_path / 'plugins.json'
    file.write_text(json.dumps({'mcp_servers': [{k: v for k, v in asdict(s).items() if v is not None} for s in m.mcp_servers], 'plugins': m.plugins}))
    (runtime.data_root / 'config.json').write_text(json.dumps({'plugin_manifests': [str(file)], 'scheduler': {'enabled': False}}))
    evidence = {'port': harness.PORT, 'dataRoot': str(runtime.data_root), 'processes': []}
    def capture():
        if runtime.process and runtime.process.poll() is None:
            for p in [psutil.Process(runtime.process.pid), *psutil.Process(runtime.process.pid).children(recursive=True)]:
                evidence['processes'].append({'pid': p.pid, 'createTime': p.create_time(), 'command': p.cmdline()})
    def stopped():
        return all(not psutil.pid_exists(p['pid']) or psutil.Process(p['pid']).create_time() != p['createTime'] for p in evidence['processes'])
    def view():
        return runtime.request('GET', '/api/automation')
    def bindings(pid, enabled=True):
        return [{'plugin_id': pid, 'enabled': enabled, 'mode': 'gateway'}]
    try:
        runtime.start()
        def create(name):
            return runtime.request('POST', '/api/sessions', json={'name': name, 'adapter': 'cbc', 'model': 'deepseek-v4-flash', 'mcpServers': []})
        target = create('target')
        control = create('unselected')
        owner = create('event-owner')
        observer = create('event-observer')
        runtime.request('POST', '/api/actions/actions', json={'item': action([target['id']])})
        key = (runtime.data_root / 'automation/user.key').read_text()
        token = runtime.request('POST', '/api/automation/login', json={'key': key})['token']
        runtime.request('POST', '/api/automation/triggers', json={'item': {**trigger(), 'eventTypes': ['producer.ping']}, 'expectedRevision': 0}, headers={'Authorization': 'Bearer ' + token})
        # Independent subscribers are live before producer admission.
        response = runtime.request('PATCH', f"/api/sessions/{observer['id']}", json={'pluginBindings': [*bindings('observer'), *bindings('other')]})
        assert 'error' not in response, response
        response = runtime.request('PATCH', f"/api/sessions/{owner['id']}", json={'pluginBindings': bindings('producer')})
        assert 'error' not in response, response
        capture()
        harness._wait_for(lambda: any(d['type'] == 'producer.ping' and d['available'] for d in view()['definitions']), label='asynchronous plugin ready')
        publish(runtime.data_root, type='producer.ping', payload={'value': 1}, event_id='http-event')
        def succeeded():
            state = view()
            return state if any(r['status'] == 'succeeded' for r in state['runs'].values()) else None
        state = harness._wait_for(succeeded, label='external event Action')
        row = next(iter(state['runs'].values()))
        assert row['event']['source'] == {'type': 'plugin', 'id': 'producer'}
        assert row['targetIds'] == [target['id']]
        assert runtime.request('GET', f"/api/sessions/{target['id']}/queue")['queuePaused']
        assert not runtime.request('GET', f"/api/sessions/{control['id']}/queue")['queuePaused']
        harness._wait_for(lambda: all(any(r['kind'] == 'delivery' and r['event']['id'] == 'http-event' for r in records(runtime.data_root, pid)) for pid in ('observer', 'other')), label='external independent fanout')
        generation = [r for r in records(runtime.data_root, 'producer') if r['kind'] == 'ready'][-1]['generation']
        runtime.request('PATCH', f"/api/sessions/{owner['id']}", json={'pluginBindings': bindings('producer', False)})
        assert not next(d for d in view()['definitions'] if d['type'] == 'producer.ping')['available']
        assert view()['dependencies']['t']['unavailable'] == ['producer.ping']
        assert view()['triggers']['t']['enabled']
        runtime.request('PATCH', f"/api/sessions/{owner['id']}", json={'pluginBindings': bindings('producer')})
        harness._wait_for(lambda: [r for r in records(runtime.data_root, 'producer') if r['kind'] == 'ready'][-1]['generation'] != generation, label='new peer ready')
        # Persisted Session bindings restore peers at web startup, without tool use.
        capture()
        runtime.stop()
        harness._wait_for(stopped, label='owned server and Node subtree retirement')
        runtime.start()
        capture()
        harness._wait_for(lambda: any(d['type'] == 'producer.ping' and d['available'] for d in view()['definitions']), label='restored peer ready')
        assert len(view()['runs']) == 1
        publish(runtime.data_root, type='producer.ping', payload={'value': 2}, event_id='after-server-restart')
        harness._wait_for(lambda: len([r for r in view()['runs'].values() if r['status'] == 'succeeded']) == 2, label='restart event Action')
        evidence.update(state=view(), records={pid: records(runtime.data_root, pid) for pid in ('producer', 'observer', 'other')})
        assert sorted(p.name for p in sentinel.parent.iterdir()) == ['sentinel'] and sentinel.read_bytes() == b'untouched'
        evidence['sentinelUnchanged'] = True
    finally:
        capture()
        runtime.stop()
        harness._wait_for(stopped, label='final owned process cleanup')
        evidence['ownedProcessesRemaining'] = []
        evidence['portReleased'] = harness._port_is_free(harness.PORT)
        out = Path('artifacts/event-plugin-adapter')
        out.mkdir(parents=True, exist_ok=True)
        (out / 'http-evidence.json').write_text(json.dumps(evidence, indent=2))
        (out / 'http-server.log').write_text((runtime.data_root / 'pan.log').read_text(encoding='utf-8', errors='replace'))
