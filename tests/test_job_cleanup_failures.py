"""Failure injection in owned cleanup, including real suspended creation."""
import os
import sys
import traceback
from types import SimpleNamespace
from contextlib import contextmanager
import pytest
from packages.jobs.cleanup import close_resources, stop_window_guard


def failure(calls, name):
    def call():
        calls.append(name)
        raise RuntimeError(name + ' failure')
    return call


def test_all_cleanup_failures_and_original_remain_diagnostic():
    calls = []
    with pytest.raises(RuntimeError) as caught:
        try:
            raise ValueError('original spawn failure')
        finally:
            close_resources(*(failure(calls, name) for name in ['job', 'guard', 'thread', 'process', 'desktop']))
    assert calls == ['job', 'guard', 'thread', 'process', 'desktop']
    text = ''.join(traceback.format_exception(caught.value))
    for name in calls:
        assert name + ' failure' in text
    assert 'original spawn failure' in text


def test_guard_stop_false_is_unconfirmed_failure():
    with pytest.raises(RuntimeError, match='stop unconfirmed'):
        stop_window_guard(SimpleNamespace(stop=lambda: False))


@pytest.mark.skipif(os.name != 'nt', reason='Windows production native APIs')
@pytest.mark.parametrize('failed_stage', ['job', 'guard', 'desktop', 'all', 'unconfirmed'])
def test_independent_failure_still_attempts_other_native_resources(tmp_path, monkeypatch, failed_stage):
    from packages.jobs import independent_runner as runner
    from packages.jobs.desktop import HiddenDesktop
    from packages.jobs.window_guard import OwnedWindowGuard
    from packages.core.terminal.guard import JobObjectGuard
    from packages.core.terminal import identity
    calls = []
    for cls, attr, name in [(JobObjectGuard, 'close', 'job'), (OwnedWindowGuard, 'stop', 'guard'), (HiddenDesktop, 'close', 'desktop')]:
        original = getattr(cls, attr)
        def injected(self, *args, original=original, name=name, **kwargs):
            result = original(self, *args, **kwargs)
            calls.append(name)
            if name == 'guard' and failed_stage == 'unconfirmed':
                return False
            if failed_stage in (name, 'all'):
                raise RuntimeError(name + ' failure')
            return result
        monkeypatch.setattr(cls, attr, injected)
    original_close = identity.close_handle_checked
    def close(handle):
        original_close(handle)
        calls.append('handle')
    monkeypatch.setattr(identity, 'close_handle_checked', close)
    @contextmanager
    def connect(root):
        yield SimpleNamespace(execute=lambda *args: SimpleNamespace(rowcount=1, fetchone=lambda: (0,)))
    monkeypatch.setattr(runner, '_connect', connect)
    with pytest.raises(RuntimeError) as caught:
        runner._run_windows_task(tmp_path, 'owned', 'claim',
            {'argv': [sys.executable, '-c', 'pass'], 'cwd': str(tmp_path)}, dict(os.environ), tmp_path / 'log')
    job_index = calls.index('job')
    assert calls[job_index:job_index+4] == ['job', 'guard', 'handle', 'handle']
    assert calls[-1] == 'desktop'
    text = ''.join(traceback.format_exception(caught.value))
    for name in (['job', 'guard', 'desktop'] if failed_stage == 'all' else ([] if failed_stage == 'unconfirmed' else [failed_stage])):
        assert name + ' failure' in text


@pytest.mark.skipif(os.name != 'nt', reason='Windows production native APIs')
def test_legacy_constructor_retains_spawn_and_cleanup_errors(tmp_path, monkeypatch):
    from packages.jobs.desktop import DesktopCommand, HiddenDesktop
    from packages.jobs.window_guard import OwnedWindowGuard
    from packages.jobs.restricted_token import HiddenJobToken
    calls = []
    def spawn(*args):
        raise ValueError('original spawn failure')
    monkeypatch.setattr(HiddenJobToken, 'create_process', spawn)
    for cls, attr, name in [(OwnedWindowGuard, 'stop', 'guard'), (HiddenDesktop, 'close', 'desktop')]:
        original = getattr(cls, attr)
        def injected(self, *args, original=original, name=name, **kwargs):
            result = original(self, *args, **kwargs)
            calls.append(name)
            raise RuntimeError(name + ' failure')
        monkeypatch.setattr(cls, attr, injected)
    with (tmp_path / 'log').open('ab') as log, pytest.raises(RuntimeError) as caught:
        DesktopCommand([sys.executable, '-c', 'pass'], str(tmp_path), log)
    assert calls == ['guard', 'desktop']
    text = ''.join(traceback.format_exception(caught.value))
    assert all(message in text for message in ['original spawn failure', 'guard failure', 'desktop failure'])


def test_legacy_close_process_thread_guard_desktop_attempted():
    from packages.jobs.desktop import DesktopCommand
    calls = []
    command = DesktopCommand.__new__(DesktopCommand)
    command.job_guard = None
    command.handle, command.thread_handle = 10, 11
    command.kernel = SimpleNamespace(CloseHandle=lambda h: calls.append(h) or True)
    command.window_guard = SimpleNamespace(stop=failure(calls, 'guard'))
    command.desktop = SimpleNamespace(close=failure(calls, 'desktop'))
    with pytest.raises(RuntimeError) as caught:
        command.close()
    assert calls == [10, 11, 'guard', 'desktop']
    assert command.handle == command.thread_handle == 0
    text = ''.join(traceback.format_exception(caught.value))
    assert 'guard failure' in text and 'desktop failure' in text


def test_failed_legacy_handle_closes_retain_unconfirmed_handles_and_attempt_rest():
    from packages.jobs.desktop import DesktopCommand
    from packages.jobs.cleanup import CleanupError
    calls = []
    command = DesktopCommand.__new__(DesktopCommand)
    command.job_guard = None
    command.handle, command.thread_handle = 10, 11
    command.kernel = SimpleNamespace(CloseHandle=lambda h: calls.append(h) or False)
    command.window_guard = SimpleNamespace(stop=lambda: calls.append('guard') or False)
    command.desktop = SimpleNamespace(close=failure(calls, 'desktop'))
    with pytest.raises(CleanupError) as caught:
        command.close()
    assert calls == [10, 11, 'guard', 'desktop']
    assert command.handle == 10 and command.thread_handle == 11
    assert len(caught.value.failures) == 4
    assert 'stop unconfirmed' in str(caught.value)


@pytest.mark.skipif(os.name != 'nt', reason='Windows production native APIs')
def test_independent_original_spawn_error_survives_outer_desktop_failure(tmp_path, monkeypatch):
    from packages.jobs import independent_runner as runner
    from packages.jobs.desktop import HiddenDesktop
    def spawn(*args):
        raise ValueError('original spawn failure')
    monkeypatch.setattr(runner, '_run_windows_task_on_desktop', spawn)
    original = HiddenDesktop.close
    def close(self):
        original(self)
        raise RuntimeError('desktop failure')
    monkeypatch.setattr(HiddenDesktop, 'close', close)
    with pytest.raises(RuntimeError) as caught:
        runner._run_windows_task(tmp_path, 'owned', 'claim', {}, {}, tmp_path / 'log')
    text = ''.join(traceback.format_exception(caught.value))
    assert 'original spawn failure' in text and 'desktop failure' in text


@pytest.mark.skipif(os.name != 'nt', reason='Windows production native APIs')
@pytest.mark.parametrize('kind', ['independent', 'legacy'])
def test_token_close_failure_after_creation_terminates_unresumed_owned_process(tmp_path, monkeypatch, kind):
    from packages.jobs.restricted_token import HiddenJobToken
    from packages.jobs.desktop import DesktopCommand
    from packages.jobs import independent_runner as runner
    from packages.core.terminal.identity import wait_state, ProcessStatus, read_creation_filetime, close_handle_checked
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    held = []
    original_create = HiddenJobToken.create_process
    def create(self, *args):
        result = original_create(self, *args)
        handle = wintypes.HANDLE()
        current = kernel.GetCurrentProcess()
        assert kernel.DuplicateHandle(current, result[0], current, ctypes.byref(handle), 0, False, 2)
        assert read_creation_filetime(handle.value)
        held.append(handle.value)
        return result
    monkeypatch.setattr(HiddenJobToken, 'create_process', create)
    original_close = HiddenJobToken.close
    def close(self):
        original_close(self)
        raise RuntimeError('token close failure')
    monkeypatch.setattr(HiddenJobToken, 'close', close)
    marker = tmp_path / 'must-not-run'
    argv = [sys.executable, '-c', 'from pathlib import Path;Path(' + repr(str(marker)) + ').touch()']
    try:
        with pytest.raises(RuntimeError, match='token close failure'):
            if kind == 'independent':
                runner._run_windows_task(tmp_path, 'owned', 'claim', {'argv': argv, 'cwd': str(tmp_path)}, dict(os.environ), tmp_path / 'log')
            else:
                with (tmp_path / 'log').open('ab') as log:
                    DesktopCommand(argv, str(tmp_path), log)
        assert len(held) == 1
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        assert kernel.WaitForSingleObject(held[0], 3000) == 0
        assert wait_state(held[0]) is ProcessStatus.DEAD
        assert not marker.exists()
    finally:
        for handle in held:
            close_handle_checked(handle)

@pytest.mark.skipif(os.name != 'nt', reason='Windows production native APIs')
def test_runner_persists_unknown_and_diagnostics_on_cleanup_failure(tmp_path, monkeypatch):
    from packages.jobs import independent_runner as runner
    from packages.jobs.independent_service import JobStore
    from packages.jobs.window_guard import OwnedWindowGuard
    store = JobStore(tmp_path / 'store')
    # Run the real one-shot entry in this test process, with no detached harness runner.
    from tests.helpers.legacy_process_fixture import historical_process
    job = historical_process(store.root, [sys.executable, '-c', 'pass'], tmp_path, launch=False)
    original = OwnedWindowGuard.stop
    def stop(self):
        original(self)
        return False
    monkeypatch.setattr(OwnedWindowGuard, 'stop', stop)
    assert runner.run(store.root, job['jobId']) == 0  # handled terminal update, not task success
    fact = store.get(job['jobId'])
    assert fact['status'] == 'UNKNOWN'
    assert fact['exitCode'] == -1
    from pathlib import Path
    assert 'stop unconfirmed' in Path(fact['logPath']).read_text(encoding='utf-8')
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0
