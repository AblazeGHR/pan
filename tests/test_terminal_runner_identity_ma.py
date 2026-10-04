"""Death evidence must refer to the bootstrap runner, never a Python shim."""
from types import SimpleNamespace as NS
import subprocess
import sys

import pytest

from packages.core.terminal.contracts import ProcessIdentity, ProcessProbe, ProcessStatus
from packages.core.terminal.service import TerminalService


def state(*, spawn_pid=41, runner_pid=42, handle=None):
    return NS(process=NS(poll=lambda: 0), spawn_pid=spawn_pid, runner_pid=runner_pid,
              runner_filetime=9, runner_identity_handle=handle)


@pytest.mark.parametrize('status', [ProcessStatus.ALIVE, ProcessStatus.UNKNOWN])
def test_exited_shim_never_proves_live_or_unknown_runner_dead(tmp_path, status):
    service = TerminalService(tmp_path, identity_probe=lambda pid: ProcessProbe(
        status=status, identity=ProcessIdentity(pid=pid, created_at_filetime=9)))
    proof = service._spawn_handle_exit_evidence(state())
    assert proof['exited'] is False
    assert proof['source'] != 'self-spawn-handle'


def test_same_pid_popen_keeps_its_original_bound_exit_proof(tmp_path):
    service = TerminalService(tmp_path)
    proof = service._spawn_handle_exit_evidence(state(spawn_pid=42))
    assert proof['exited'] is True
    assert proof['source'] == 'self-spawn-handle'


def test_missing_bootstrap_identity_is_not_a_popen_death_proof(tmp_path):
    service = TerminalService(tmp_path)
    owner = state(spawn_pid=None, runner_pid=None)
    owner.runner_filetime = None
    assert service._spawn_handle_exit_evidence(owner)['exited'] is False


@pytest.mark.parametrize('status,identity,expected', [
    (ProcessStatus.DEAD, ProcessIdentity(pid=42, created_at_filetime=9), True),
    (ProcessStatus.ALIVE, ProcessIdentity(pid=42, created_at_filetime=9), False),
    (ProcessStatus.UNKNOWN, ProcessIdentity(pid=42, created_at_filetime=9), False),
    (ProcessStatus.DEAD, ProcessIdentity(pid=43, created_at_filetime=9), False),
    (ProcessStatus.DEAD, ProcessIdentity(pid=42, created_at_filetime=10), False),
])
def test_retained_runner_handle_requires_same_identity_and_signaled(tmp_path, status, identity, expected):
    service = TerminalService(tmp_path)
    handle = NS(probe=lambda: ProcessProbe(status=status, identity=identity))
    proof = service._spawn_handle_exit_evidence(state(handle=handle))
    assert proof['exited'] is expected
    assert proof['source'] == 'runner-retained-handle'


@pytest.mark.parametrize('pid,filetime', [(True, '9'), (42.1, '9'), (42, True),
                                       (42, 9.1), (42, '9'*5000)])
def test_cleanup_identity_fields_do_not_coerce_invalid_values(tmp_path, pid, filetime):
    service = TerminalService(tmp_path)
    assert service._identity_matches({'pid': pid, 'process_created_at_filetime': filetime}, 42, 9) is False


def test_retained_handle_close_failure_keeps_owner_for_retry(tmp_path):
    service = TerminalService(tmp_path)
    outcomes = iter([False, True])
    handle = NS(close=lambda: next(outcomes))
    owner = state(handle=handle)
    assert service._release_runner_identity_handle(owner) is False
    assert owner.runner_identity_handle is handle
    assert service._release_runner_identity_handle(owner) is True
    assert owner.runner_identity_handle is None


def test_handle_probe_and_release_do_not_overlap_or_wait_on_busy_owner(tmp_path):
    service = TerminalService(tmp_path)
    calls = []
    handle = NS(close=lambda: calls.append('close') or True, probe=lambda: calls.append('probe'))
    owner = state(handle=handle)
    lock = service._runner_identity_lock(owner)
    lock.acquire()
    try:
        assert service._spawn_handle_exit_evidence(owner)['detail'] == 'handle-busy'
        assert service._release_runner_identity_handle(owner) is False
        assert calls == []
        assert owner.runner_identity_handle is handle
    finally:
        lock.release()
    assert service._release_runner_identity_handle(owner) is True
    assert calls == ['close']


def test_retain_real_runner_requires_exact_alive_identity(tmp_path, monkeypatch):
    from packages.core.terminal.service import _TerminalState
    from packages.core.terminal.win_pipe import ProcessIdentityHandle
    service = TerminalService(tmp_path)
    calls = []
    handle = NS(close=lambda: calls.append('close') or True, probe=lambda: ProcessProbe(
        status=ProcessStatus.ALIVE, identity=ProcessIdentity(pid=42, created_at_filetime=9)))
    monkeypatch.setattr(ProcessIdentityHandle, 'open', lambda pid: handle)
    owner = _TerminalState(terminal_id='term_retained_ma', record=None, runner_pid=42, runner_filetime=9)
    service._retain_runner_identity_handle(owner)
    assert owner.runner_identity_handle is handle
    assert owner.runner_handle_finalizer.alive is True
    assert service._release_runner_identity_handle(owner) is True
    assert owner.runner_handle_finalizer is None
    assert calls == ['close']


@pytest.mark.skipif(sys.platform != 'win32', reason='real Windows retained handle')
def test_real_retained_runner_survives_original_popen_handle_release(tmp_path):
    from packages.core.terminal.service import _TerminalState
    from packages.core.terminal.win_pipe import ProcessIdentityHandle
    # The explicit base interpreter avoids a venv redirector for this one kernel
    # primitive control; production uv service tests cover the actual shim path.
    import sysconfig
    from pathlib import Path
    python = Path(sys.base_prefix) / 'python.exe'
    assert python.exists(), sysconfig.get_platform()
    process = subprocess.Popen([str(python), '-c', 'import sys;sys.stdin.readline()'],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, text=True)
    service = TerminalService(tmp_path)
    owner = None
    try:
        with ProcessIdentityHandle.open(process.pid) as observer:
            owner = _TerminalState(terminal_id='term_real_retained_ma', record=None,
                                   process=process, spawn_pid=process.pid + 1,
                                   runner_pid=process.pid, runner_filetime=observer.filetime)
            service._retain_runner_identity_handle(owner)
        assert service._spawn_handle_exit_evidence(owner)['exited'] is False
        process.communicate('exit\n', timeout=10)
        assert process.returncode == 0
        process._handle.Close()  # release only this owned original Popen handle
        assert service._spawn_handle_exit_evidence(owner) == {
            'exited': True, 'source': 'runner-retained-handle', 'detail': 'same-handle-signaled'}
        assert service._release_runner_identity_handle(owner) is True
    finally:
        if process.returncode is None:
            # Popen.kill uses this original CreateProcess handle, never a PID scan.
            process.kill()
            process.communicate(timeout=10)
        if owner is not None:
            assert service._release_runner_identity_handle(owner) is True


def test_close_retains_credentials_until_runner_handle_itself_is_released(tmp_path):
    from packages.core.terminal.service import CleanupUnconfirmed
    from tests.test_terminal_service import _FakeProcess, _make_service, _converged_launcher_status
    process = _FakeProcess()
    service = _make_service(tmp_path, process=process, heartbeat_interval=5)
    tid = service.create()['terminal_id']
    owner = service._states[tid]
    _converged_launcher_status(service.root, tid, owner.runner_pid, owner.runner_filetime)
    process.exit()
    outcomes = iter([False, True])
    owner.runner_identity_handle = NS(close=lambda: next(outcomes), probe=lambda: ProcessProbe(
        status=ProcessStatus.DEAD, identity=ProcessIdentity(
            pid=owner.runner_pid, created_at_filetime=owner.runner_filetime)))
    with pytest.raises(CleanupUnconfirmed):
        service.close(tid)
    assert service._store().deleted == []
    assert owner.runner_identity_handle is not None
    assert service.close(tid)['status'] == 'exited'
    assert owner.runner_identity_handle is None
    assert service._store().deleted
