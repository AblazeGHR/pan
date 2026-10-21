"""Real loopback HTTP + deterministic fake stream CLI; never a paid provider."""
import json
import os
from pathlib import Path
import time

import psutil

from tests import test_real_queue_http_e2e as harness
from tests.test_automation_core import action, trigger


def test_real_running_event_pause_blocks_queue(tmp_path, monkeypatch):
    assert harness.PORT not in {8767, 8768}
    launcher = Path(__file__).parent / 'support' / 'automation_http_server.py'
    monkeypatch.setattr(harness, 'SERVER_LAUNCHER', launcher)
    runtime = harness._PanRuntime(tmp_path / 'owned-profile')
    runtime.data_root.mkdir(parents=True)
    # Poisoned parent directories are represented by an owned sentinel only.
    sentinel = tmp_path / 'simulated-practical' / 'sentinel'
    sentinel.parent.mkdir()
    sentinel.write_bytes(b'unchanged')
    monkeypatch.setenv('PAN_BACKGROUND_JOBS_DIR', str(sentinel.parent))
    # Launcher must remove this poisoned parent setting before application import.
    runtime.data_root.joinpath('config.json').write_text(json.dumps({'plugin_manifests': [], 'scheduler': {'enabled': False}}))
    runtime.start()
    evidence = dict(port=harness.PORT, dataRoot=str(runtime.data_root), pid=runtime.process.pid, createTime=psutil.Process(runtime.process.pid).create_time(), command=psutil.Process(runtime.process.pid).cmdline())
    try:
        def create(name):
            return runtime.request('POST', '/api/sessions', json={'name': name, 'adapter': 'cbc', 'model': 'deepseek-v4-flash', 'permissionMode': 'bypassPermissions', 'outputMode': 'stream', 'mcpServers': []})
        target = create('auto-target')
        control = create('auto-control')
        source = create('auto-source')
        for session in (target, control, source):
            assert 'id' in session, session
            result = harness._spawn(runtime, session['id'])
            assert 'error' not in result, result
        a = action([target['id']])
        runtime.request('POST', '/api/actions/actions', json={'item': a})
        draft = {**trigger(), 'enabled': False}
        runtime.request('POST', '/api/automation/triggers', json={'item': draft, 'expectedRevision': 0})
        harness._enqueue(runtime, source['id'], 'default-off-probe', 'off')
        harness._wait_for(lambda: harness._received(runtime, text='default-off-probe'), label='default off source handoff')
        harness._wait_for(lambda: all(s.get('workerStatus') != 'running' for s in runtime.request('GET', '/api/sessions')['sessions']), label='source idle')
        assert runtime.request('GET', '/api/automation')['runs'] == {}
        assert not runtime.request('GET', f"/api/sessions/{target['id']}/queue")['queuePaused']
        item = runtime.request('POST', f"/api/sessions/{target['id']}/queue", json={'text': 'must-stay-queued', 'clientMessageId': 'blocked', 'locked': True})['item']
        key = (runtime.data_root / 'automation' / 'user.key').read_text()
        token = runtime.request('POST', '/api/automation/login', json={'key': key})['token']
        headers = {'Authorization': 'Bearer ' + token}
        runtime.request('POST', '/api/automation/triggers', json={'item': trigger(), 'expectedRevision': 1}, headers=headers)
        runtime.request('POST', '/api/automation/logout', headers=headers)
        harness._enqueue(runtime, source['id'], 'enabled-source-probe', 'on')
        def finished():
            data = runtime.request('GET', '/api/automation')
            return data if any(r['status'] == 'succeeded' for r in data['runs'].values()) else None
        automation = harness._wait_for(finished, label='durable automatic Pause')
        run = next(iter(automation['runs'].values()))
        assert run['event']['type'] == 'workers.all_idle' and run['event']['payload'] == {'runningCount': 0}
        assert run['targetIds'] == [target['id']] and run['targetResults'][0]['ok']
        queue = runtime.request('GET', f"/api/sessions/{target['id']}/queue")
        assert queue['queuePaused']
        runtime.request('PATCH', f"/api/sessions/{target['id']}/queue/{item['queueItemId']}/lock", json={'locked': False})
        harness._enqueue(runtime, control['id'], 'unselected-control-delivered', 'control')
        harness._wait_for(lambda: harness._received(runtime, text='unselected-control-delivered'), label='unselected handoff')
        # More than one consumer/watchdog wake opportunity; no target handoff.
        time.sleep(.4)
        queue = runtime.request('GET', f"/api/sessions/{target['id']}/queue")
        assert queue['queuePaused'] and any(i['queueItemId'] == item['queueItemId'] for i in queue['items'])
        assert not harness._received(runtime, text='must-stay-queued')
        persisted = json.loads((runtime.data_root / 'automation' / 'registry.json').read_text())
        assert persisted['runs'][run['id']]['status'] == 'succeeded'
        assert json.loads((runtime.data_root / 'sessions' / (target['id'] + '.json')).read_text())['queue_paused'] is True
        evidence.update(run=run, queue=queue, targetId=target['id'], unselectedId=control['id'], sentinelUnchanged=sentinel.read_bytes() == b'unchanged' and sorted(p.name for p in sentinel.parent.iterdir()) == ['sentinel'], fakeRecords=runtime.fake_records())
    finally:
        # Capture only this fixture's descendants, including CLIs spawned after readiness.
        if runtime.process and runtime.process.poll() is None:
            for p in psutil.Process(runtime.process.pid).children(recursive=True):
                runtime._owned_processes.append((p.pid, p.create_time(), ' '.join(p.cmdline())))
        owned = list(runtime._owned_processes)
        runtime.stop()
        remaining = []
        for pid, created, command in owned:
            try:
                p = psutil.Process(pid)
                if abs(p.create_time() - created) <= .01 and p.is_running():
                    remaining.append(pid)
            except psutil.Error:
                pass
        evidence['ownedProcessesRemaining'] = remaining
        assert not remaining, remaining
        evidence['portReleased'] = harness._port_is_free(harness.PORT)
        output = Path('artifacts/event-trigger-core')
        output.mkdir(parents=True, exist_ok=True)
        (output / 'http-evidence.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        (output / 'http-server.log').write_text((runtime.data_root / 'pan.log').read_text(encoding='utf-8', errors='replace'), encoding='utf-8')
