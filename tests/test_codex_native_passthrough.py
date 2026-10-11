"""Native policy is opt-in; admission/unknown-state marking stays fenced."""
import asyncio
import io
import json
import sys
import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from packages.core import worker, session
from packages.core.adapters.codex.adapter import CodexAdapter
from packages.core.adapters.codex.app_server_wrapper import AppServer, _read_pan_stdin
from packages.core.adapters.codex.resident_tui import ResidentTui


def proxy(monkeypatch, messages, *, enabled=False, native=True, failing=False):
    app = AppServer('node', 'codex.js', '.', [], native_control=True, native_passthrough=enabled)
    app.thread_id = 'current'
    app.process = SimpleNamespace(poll=lambda: None)
    sent, replies, events = [], [], []
    def send(message):
        sent.append(message)
        if failing:
            raise OSError('SECRET-NOT-A-DIAGNOSTIC')
    peer = SimpleNamespace(send=send)
    app._native_transport = SimpleNamespace(peer=lambda poll: peer)
    if native:
        app.control_owner.switch('native', 0)
    tui = ResidentTui(app, events.append)
    monkeypatch.setattr(tui, '_from_server', lambda: None)
    monkeypatch.setattr(tui, '_down_send', replies.append)
    tui._handle([json.dumps(m) for m in messages])
    return app, sent, replies, events


@pytest.mark.parametrize('method', ['thread/settings/update', 'config/batchWrite', 'future/newOperation', 'config/mcpServer/reload'])
def test_passthrough_explicitly_forwards_without_rewriting_and_marks_unknown(monkeypatch, method):
    message = {'id': 1, 'method': method, 'params': {'threadId': 'current', 'value': 'SECRET'}}
    _, sent, replies, events = proxy(monkeypatch, [message])
    assert sent == [] and replies[0]['error']['code'] == -32000 and not events
    _, sent, replies, events = proxy(monkeypatch, [message], enabled=True)
    assert sent == [message] and not replies
    assert events == [{'type': 'pan.native_settings_uncertain'}]


@pytest.mark.parametrize('native,thread', [(False, 'current'), (True, 'other')])
def test_passthrough_does_not_bypass_current_owner_or_thread(monkeypatch, native, thread):
    _, sent, replies, events = proxy(monkeypatch, [{'id': 1, 'method': 'config/batchWrite',
        'params': {'threadId': thread}}], enabled=True, native=native)
    assert sent == [] and replies[0]['error']['code'] == -32000 and not events


def test_known_reads_and_plain_turn_do_not_mark_settings_but_override_does(monkeypatch):
    read = {'id': 1, 'method': 'config/read', 'params': {}}
    turn = {'id': 2, 'method': 'turn/start', 'params': {'threadId': 'current', 'input': []}}
    _, sent, _, events = proxy(monkeypatch, [read, turn])
    assert sent == [read, turn] and events == [{'type': 'pan.native_busy', 'busy': True}]
    _, sent, _, events = proxy(monkeypatch, [{**turn, 'params': {**turn['params'], 'model': 'changed'}}])
    assert len(sent) == 1 and {'type': 'pan.native_settings_uncertain'} in events


def test_ambiguous_send_marks_before_delivery_and_never_leaks_params(monkeypatch):
    _, _, _, events = proxy(monkeypatch, [{'id': 1, 'method': 'config/batchWrite',
        'params': {'secret': 'SECRET'}}], enabled=True, failing=True)
    assert events[0] == {'type': 'pan.native_settings_uncertain'}
    assert 'SECRET' not in json.dumps(events)


def test_policy_toggle_is_a_control_message_not_a_new_turn(monkeypatch):
    task, controls, received = Queue(), Queue(), []
    monkeypatch.setattr('sys.stdin', io.StringIO('{"type":"native_passthrough","enabled":true}\n'))
    _read_pan_stdin(task, controls, native_handler=received.append)
    assert received == [{'type': 'native_passthrough', 'enabled': True}]
    assert task.get_nowait() is None and task.empty() and controls.empty()


def test_spawn_policy_default_off_and_only_true_on_native_transport(monkeypatch):
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, 'base_args', lambda: ['wrapper'])
    monkeypatch.setattr(adapter, 'mcp_args', lambda s: [])
    s = session.Session(id='ses-policy', name='policy', adapter='codex')
    for value in (None, False, 'true', 1):
        s.adapter_config = {'native_control': True, 'native_passthrough': value}
        assert '--native-passthrough' not in adapter.build_spawn_args(s)
    s.adapter_config = {'native_control': True, 'native_passthrough': True}
    assert '--native-passthrough' in adapter.build_spawn_args(s)
    s.adapter_config.pop('native_control')
    assert '--native-passthrough' not in adapter.build_spawn_args(s)


def test_uncertainty_is_durable_and_not_cleared_by_runtime_reset(monkeypatch):
    async def run():
        s = session.Session(id='ses-policy-state', name='policy', adapter='codex')
        s.adapter_config['native_passthrough'] = True
        w = worker.Worker('policy-worker', s.id, CodexAdapter())
        monkeypatch.setattr(worker, '_session', lambda _: s)
        saved, broadcast = AsyncMock(), AsyncMock()
        monkeypatch.setattr(session, 'save_async', saved)
        monkeypatch.setattr(worker, '_bcast', broadcast)
        await worker._record_native_settings_state(w, uncertain=True)
        await worker._record_native_settings_state(w, uncertain=True)
        assert saved.await_count == 1
        worker.clear_native_runtime_state(w)  # Restart failure stops here.
        assert s.adapter_config['native_settings_uncertain'] is True
        await worker._record_native_settings_state(w, uncertain=False)  # New init only.
        assert 'native_settings_uncertain' not in s.adapter_config
        assert w.native_passthrough_enabled is True and saved.await_count == 2
    asyncio.run(run())


@pytest.mark.parametrize('restart_ready', [False, True])
def test_worker_stream_only_new_init_clears_warning_not_duplicate_init(monkeypatch, restart_ready):
    async def run():
        s = session.Session(id='ses-policy-stream', name='policy', adapter='codex')
        s.adapter_config = {'native_passthrough': True, 'native_settings_uncertain': True}
        events = [{'type': 'thread.started', 'thread_id': 'live'}] if restart_ready else [
            {'type': 'thread.started', 'thread_id': 'live'},
            {'type': 'pan.native_settings_uncertain'},
            {'type': 'thread.started', 'thread_id': 'live'}]
        class Process:
            returncode = 0
            async def read(self, size):
                return (json.dumps(events.pop(0)) + '\n').encode() if events else b''
        process = Process()
        process.stdout = process
        w = worker.Worker('stream-policy', s.id, CodexAdapter(), process=process)
        monkeypatch.setattr(worker, '_session', lambda _: s)
        monkeypatch.setattr(session, 'save_async', AsyncMock())
        monkeypatch.setattr(worker, '_bcast', AsyncMock())
        await worker._read_stdout(w)
        assert w.native_kernel_ready and w.native_passthrough_enabled is True
        assert (s.adapter_config.get('native_settings_uncertain') is True) is not restart_ready
    asyncio.run(run())


def test_hot_policy_in_pan_mode_uses_receipt_and_runtime_fence(monkeypatch):
    async def run():
        w = worker.Worker('policy-worker', 'ses-policy', CodexAdapter())
        w.native_control_enabled = w.native_kernel_ready = True
        monkeypatch.setattr(worker, 'workers', {w.worker_id: w})
        sent = []
        class Stdin:
            def write(self, raw):
                message = json.loads(raw)
                sent.append(message)
                w._native_mode_receipts[message['requestId']].set_result({'ok': True})
            async def drain(self):
                pass
        w.process = SimpleNamespace(stdin=Stdin(), returncode=None)
        assert (await worker.write_native_control(w.worker_id, {'type': 'native_passthrough', 'enabled': True}))['ok']
        assert w.native_control_mode == 'pan' and w.native_passthrough_enabled is True
        assert sent[0]['type'] == 'native_passthrough' and 'generation' not in sent[0]
        assert not w._native_mode_receipts
        assert not (await worker.write_native_control(w.worker_id, {'type': 'native_passthrough', 'enabled': 1}))['ok']
        assert len(sent) == 1
    asyncio.run(run())


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows owned Codex kernel')
def test_real_codex_config_write_through_private_proxy_uses_only_temporary_home(tmp_path, monkeypatch):
    """Real installed Codex RPC/disk proof, not a browser/model-provider claim."""
    import tomllib
    from packages.core.adapters.codex.adapter import _resolve_codex_js, _resolve_codex_node
    home, cwd = tmp_path / 'codex-home', tmp_path / 'workspace'
    home.mkdir(); cwd.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    # No turn/inference/account: local dummy provider merely seeds a thread.
    options = ['-c', 'model="probe-model"', '-c', 'model_provider="probe_local"', '-c',
        'model_providers.probe_local={name="probe",base_url="http://127.0.0.1:9/v1",wire_api="responses"}']
    app = AppServer(_resolve_codex_node(), _resolve_codex_js(), str(cwd), options, native_control=True)
    incoming, replies, events = Queue(), Queue(), []
    class Down:
        def __iter__(self):
            while (message := incoming.get()) is not None:
                yield json.dumps(message)
        def send(self, raw):
            replies.put(json.loads(raw))
    tui = ResidentTui(app, events.append)
    connection = None
    def call(message):
        incoming.put(message)
        while True:
            reply = replies.get(timeout=10)
            if reply.get('id') == message['id']:
                return reply
    try:
        app.start()
        app.control_owner.switch('native', 0)
        connection = threading.Thread(target=tui._handle, args=(Down(),), daemon=True)
        connection.start()
        assert 'result' in call({'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'pan-probe', 'version': '1'}, 'capabilities': {}}})
        incoming.put({'method': 'initialized', 'params': {}})
        write = {'id': 2, 'method': 'config/batchWrite', 'params': {'edits': [
            {'keyPath': 'model_reasoning_effort', 'value': 'high', 'mergeStrategy': 'replace'}]}}
        config = home / 'config.toml'
        assert call(write)['error']['code'] == -32000 and not config.exists()
        with app._native_policy_lock:
            app.native_passthrough = True
        assert 'result' in call({**write, 'id': 3})
        assert tomllib.loads(config.read_text(encoding='utf-8'))['model_reasoning_effort'] == 'high'
        assert events == [{'type': 'pan.native_settings_uncertain'}]
        with app._native_policy_lock:
            app.native_passthrough = False
        before = config.read_bytes()
        assert call({**write, 'id': 4})['error']['code'] == -32000
        assert config.read_bytes() == before and app.process.poll() is None
    finally:
        tui.stopped.set()
        incoming.put(None)
        if tui.peer:
            tui.peer.close()
        if connection:
            connection.join(3)
            assert not connection.is_alive()
        app.close()
