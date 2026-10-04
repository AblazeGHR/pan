"""Natural shell exit is not runner exit or pipe EOF; all three stay distinct."""
import threading
import time
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.core.terminal.runner import TerminalRunner
from packages.core.terminal.service import TerminalService, _Heartbeat


@pytest.mark.parametrize('seen,reader_done,code,should_close', [
    (True, True, 7, True), (True, False, 7, False),
    (False, True, 7, False), (True, True, None, False),
    (True, True, True, False), ('true', True, 7, False),
])
def test_watchdog_natural_exit_requires_real_code_and_finished_reader(tmp_path, seen, reader_done, code, should_close):
    runner = TerminalRunner('term_natural', tmp_path / 'secrets/term_natural.secret')
    info = SimpleNamespace(process_exit_seen=seen, reader_done=reader_done, code=code)
    runner._runtime = SimpleNamespace(poll_exit=lambda: info)
    runner._runner_state = 'running'
    calls = []
    runner.close = lambda **kwargs: calls.append(kwargs) or {'status': 'exited'}
    assert runner._natural_exit_tick(now=0) is False
    assert calls == []
    runner._natural_exit_tick(now=5.1)
    assert bool(calls) is should_close
    if should_close:
        assert calls[0]['reason'] == 'natural-exit'


@pytest.mark.parametrize('data,seen,reader_done,code', [
    (b'TAIL', True, True, 7), (b'', True, False, 7),
    (b'', False, True, 7), (b'', True, True, None),
    (b'', 'true', True, 7), (b'', True, True, True),
])
def test_service_does_not_close_before_tail_or_unknown_exit(data, seen, reader_done, code):
    service = TerminalService.__new__(TerminalService)
    result = {'data': data, 'size': len(data), 'seq': 0, 'next_cursor': len(data),
              'total_bytes': len(data), 'status': 'running',
              'describe': {'exit': {'seen': seen, 'reader_done': reader_done, 'code': code}}}
    state = SimpleNamespace(natural_exit=None, lock=threading.RLock(),
                            client=SimpleNamespace(read=lambda *args, **kwargs: result))
    service._require_state = lambda _id: state
    service._require_client = lambda state: state.client
    calls = []
    service._close_state = lambda *args, **kwargs: calls.append(kwargs) or {'status': 'exited'}
    assert service.read('term_natural')['data'] == data
    assert calls == []


def test_service_consumes_natural_cleanup_with_same_owner_and_no_fake_eof():
    service = TerminalService.__new__(TerminalService)
    result = {'data': b'', 'size': 0, 'seq': 5, 'next_cursor': 5,
              'total_bytes': 5, 'status': 'running',
              'describe': {'exit': {'seen': True, 'reader_done': True, 'code': 7,
                                    'output_complete': False}}}
    state = SimpleNamespace(terminal_id='term_natural', natural_exit=None, lock=threading.RLock(),
                            client=SimpleNamespace(read=lambda *args, **kwargs: result))
    service._require_state = lambda _id: state
    service._require_client = lambda state: state.client
    calls = []
    def close(owner, **kwargs):
        calls.append(owner)
        if len(calls) == 1:
            return {'status': 'closing'}
        owner.client = None
        return {'status': 'exited'}
    service._close_state = close
    first = service.read('term_natural', 5)
    assert first['status'] == 'closing'
    second = service.read('term_natural', 5)
    assert second['status'] == 'exited'
    assert second['exit_code'] == 7 and second['output_complete'] is False
    assert second['process_exit_seen'] is True and second['reader_done'] is True
    assert calls == [state, state]


def test_heartbeat_join_return_value_is_not_mistaken_for_join_result(tmp_path):
    heartbeat = _Heartbeat(terminal_id='term_natural', client_factory=lambda *args, **kwargs: None,
                           data_root=tmp_path, client_id='owner')
    heartbeat._thread = threading.Thread(target=lambda: None)
    heartbeat._thread.start()
    heartbeat._thread.join()
    released = []
    heartbeat._client = SimpleNamespace(release_connection=lambda: released.append(True))
    assert heartbeat.stop(.1) is True
    assert released == [True]


def test_heartbeat_owner_is_retained_when_thread_is_still_in_flight():
    service = TerminalService.__new__(TerminalService)
    heartbeat = SimpleNamespace(stop=lambda **kwargs: False)
    state = SimpleNamespace(heartbeat=heartbeat)
    service._stop_heartbeat(state)
    assert state.heartbeat is heartbeat


@pytest.mark.parametrize('runner,engine,dead,closed', [
    (True, True, True, True), (False, True, True, False),
    (True, False, True, False), (True, True, False, False),
])
def test_browserless_refresh_requires_all_three_proofs(runner, engine, dead, closed):
    from packages.core.terminal.contracts import RuntimeState
    service = TerminalService.__new__(TerminalService)
    state = SimpleNamespace(record=SimpleNamespace(status=RuntimeState.RUNNING),
                            lock=threading.RLock())
    service._global_lock = threading.Lock()
    service._states = {'term_natural': state}
    service._runner_cleanup_record_confirmed = lambda state: runner
    service._launcher_status_evidence = lambda state: {'engine_converged': engine}
    service._spawn_handle_exit_evidence = lambda state: {'exited': dead}
    service._finished_runtime_exit = lambda state: {'seen': True, 'reader_done': True, 'code': 7}
    service._release_runner_identity_handle = lambda state: True
    calls = []
    service._finalize_exited = lambda *args, **kwargs: calls.append(kwargs)
    service._sync_state_record = lambda state: None
    service._refresh_finished_owner('term_natural')
    assert bool(calls) is closed
    if closed:
        assert calls[0]['evidence']['runner_exit']['code'] == 7


@pytest.mark.skipif(sys.platform != 'win32' or not (
    Path(__file__).resolve().parents[1] / 'packages/core/terminal/emulator_sidecar/node_modules/@xterm/headless/package.json'
).is_file(), reason='owned Windows ConPTY/sidecar fixture required')
def test_real_browserless_natural_exit_consumes_launcher_cleanup(tmp_path):
    import json
    from packages.core.terminal import win_pipe
    service = TerminalService(tmp_path / 'terminals', log_stderr=False)
    terminal_id = None
    try:
        created = service.create()
        terminal_id = created['terminal_id']
        token = service.attach(terminal_id, 'owned-natural-fixture', role='control')
        service.input(terminal_id, token, b'echo NATURAL_NO_BROWSER&exit 7\r')
        # No browser, no raw read, no explicit stop: the runner's fallback owns
        # whole-Job cleanup. GET may consume only already-completed proof.
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            view = service.get(terminal_id)
            if view['status'] == 'exited':
                break
            time.sleep(.1)
        assert view['status'] == 'exited', view
        assert view['exit']['code'] == 7, view
        state = service._states[terminal_id]
        assert state.heartbeat is None
        assert state.client is None and state.runner_identity_handle is None
        assert state.process.poll() == 0
        assert not (service.root / 'secrets' / f'{terminal_id}.secret').exists()
        status = json.loads((service.root / 'runner-status' / f'{terminal_id}.json').read_text(encoding='utf-8'))
        assert status['runtime_exit']['code'] == 7
        assert status['runtime_exit']['output_complete'] is False
        assert status['cleanup']['converged'] is True
    finally:
        report = service.shutdown(budget=10)
        if terminal_id and report.get('unconfirmed'):
            state = service._states[terminal_id]
            if state.process.poll() is None:
                win_pipe.terminate_verified_process(state.runner_pid, state.runner_filetime)
