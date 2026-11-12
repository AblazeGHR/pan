import os
import sys
from unittest.mock import MagicMock, Mock
import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows desktop launch')


@pytest.mark.parametrize('stage', ['desktop', 'guard'])
def test_canonical_execution_fallback_keeps_original_effect_and_receipt(monkeypatch, tmp_path, stage):
    import asyncio
    from packages.core.platform.desktop_win import HiddenDesktop
    from packages.jobs.window_guard import OwnedWindowGuard
    from packages.jobs.window import HiddenWindowUnavailable
    from packages.jobs.independent_executor import ExecutionStore
    from packages.jobs.independent_execution_runner import execute_claimed
    from tests.test_job_final_independent_execution import claim, admit, attempt
    marker = tmp_path / 'canonical-marker'
    plan = {'operation': 'process', 'argv': [sys.executable, '-c',
        "from pathlib import Path; p=Path(" + repr(str(marker)) + "); p.open('a').write('once\\n'); print('canonical stdout')"],
        'cwd': str(tmp_path)}
    if stage == 'desktop':
        monkeypatch.setattr(HiddenDesktop, '__init__', Mock(side_effect=HiddenWindowUnavailable('desktop unavailable')))
    else:
        monkeypatch.setattr(OwnedWindowGuard, 'start', lambda self: False)
    store = ExecutionStore(tmp_path / 'canonical')
    doc = claim(store, plan)
    result = asyncio.run(execute_claimed(store, doc))
    receipt = result['result']
    assert result['effectId'] == doc['effectId']
    assert result['attempt'] == doc['attempt']
    assert receipt['outcome'] == 'succeeded'
    assert receipt['cleanupProof']['confirmed'] is True
    assert receipt['receipt']['effectiveHideWindow'] is False
    assert receipt['receipt']['hiddenWindowFailure']
    assert receipt['receipt']['processIdentity']['birth']
    assert marker.read_text().splitlines() == ['once']
    assert admit(store, plan, attempt(), retry=True)[0]['result'] == receipt
    assert marker.read_text().splitlines() == ['once']
    logs = list(store.root.rglob('*.log'))
    assert any('launching visible' in p.read_text() and 'canonical stdout' in p.read_text() for p in logs)


def test_canonical_cleanup_failure_blocks_fallback(monkeypatch, tmp_path):
    from packages.core.platform.process import WindowsProcessOwner
    from packages.core.platform.desktop_win import HiddenDesktop
    from packages.jobs.window_guard import OwnedWindowGuard
    from packages.core.platform import process_creation_win
    original_close = HiddenDesktop.close
    def failed_close(self):
        original_close(self)
        raise RuntimeError('cleanup unconfirmed')
    monkeypatch.setattr(OwnedWindowGuard, 'start', lambda self: False)
    monkeypatch.setattr(HiddenDesktop, 'close', failed_close)
    create = Mock()
    monkeypatch.setattr(process_creation_win, 'create_owned_process', create)
    with pytest.raises(RuntimeError, match='cleanup unconfirmed'):
        WindowsProcessOwner().spawn([sys.executable, '-c', 'pass'], cwd=tmp_path)
    create.assert_not_called()

def test_guard_failure_falls_back_once_and_records_mode(monkeypatch, tmp_path):
    from packages.core import background_runner as runner
    from packages.jobs.window import HiddenWindowUnavailable
    job = {'jobId':'owned', 'argv':[sys.executable], 'cwd':str(tmp_path), 'logPath':str(tmp_path/'log')}
    process = MagicMock(pid=123, wait=Mock(return_value=0))
    hidden = Mock(side_effect=HiddenWindowUnavailable('guard unavailable'))
    visible = Mock(return_value=process)
    updates = Mock()
    monkeypatch.setattr(runner, 'DesktopCommand', hidden)
    monkeypatch.setattr(runner.subprocess, 'Popen', visible)
    monkeypatch.setattr(runner.jobs, 'get', lambda _: job)
    monkeypatch.setattr(runner.jobs, 'claim_hidden_runner', lambda _, **kwargs: True)
    monkeypatch.setattr(runner.jobs, 'runner_spawn', lambda _, factory, **kwargs: factory())
    monkeypatch.setattr(runner.jobs, 'runner_update', updates)
    monkeypatch.setattr(sys, 'argv', ['runner', '--job-id', 'owned'])
    assert runner.main() == 0
    hidden.assert_called_once(); visible.assert_called_once()
    assert visible.call_args.kwargs['creationflags'] == 0x10
    assert any(c.kwargs.get('effectiveHideWindow') is False and 'guard unavailable' in c.kwargs.get('hiddenWindowFailure', '') for c in updates.call_args_list)

@pytest.mark.parametrize('error', [OSError('command creation failed'), RuntimeError('unknown process'), RuntimeError('cleanup failed')])
def test_unproven_failure_never_replays(monkeypatch, tmp_path, error):
    from packages.core import background_runner as runner
    job = {'jobId':'owned', 'argv':[sys.executable], 'cwd':str(tmp_path), 'logPath':str(tmp_path/'log')}
    visible = Mock()
    monkeypatch.setattr(runner, 'DesktopCommand', Mock(side_effect=error))
    monkeypatch.setattr(runner.subprocess, 'Popen', visible)
    monkeypatch.setattr(runner.jobs, 'get', lambda _: job)
    monkeypatch.setattr(runner.jobs, 'claim_hidden_runner', lambda _, **kwargs: True)
    monkeypatch.setattr(runner.jobs, 'runner_spawn', lambda _, factory, **kwargs: factory())
    update = Mock(); monkeypatch.setattr(runner.jobs, 'runner_update', update)
    monkeypatch.setattr(sys, 'argv', ['runner', '--job-id', 'owned'])
    assert runner.main() == 1
    visible.assert_not_called()
    assert str(error) in update.call_args.kwargs['error']


def test_real_guard_failure_executes_visible_process_once(monkeypatch, tmp_path):
    from packages.core import background_runner as runner
    from packages.jobs.window_guard import OwnedWindowGuard
    marker = tmp_path / 'marker'
    code = "import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.open('a').write('once\\n'); print('stdout'); print('stderr', file=sys.stderr)"
    job = {'jobId':'owned', 'argv':[sys.executable, '-c', code, str(marker)], 'cwd':str(tmp_path), 'logPath':str(tmp_path/'log')}
    monkeypatch.setattr(OwnedWindowGuard, 'start', lambda self: False)
    monkeypatch.setattr(runner.jobs, 'get', lambda _: job)
    monkeypatch.setattr(runner.jobs, 'claim_hidden_runner', lambda _, **kwargs: True)
    monkeypatch.setattr(runner.jobs, 'runner_spawn', lambda _, factory, **kwargs: factory())
    update = Mock(); monkeypatch.setattr(runner.jobs, 'runner_update', update)
    monkeypatch.setattr(sys, 'argv', ['runner', '--job-id', 'owned'])
    assert runner.main() == 0
    assert marker.read_text().splitlines() == ['once']
    log = (tmp_path/'log').read_text()
    assert 'owned window guard failed' in log and 'stdout' in log and 'stderr' in log
    assert any(c.kwargs.get('effectiveHideWindow') is False for c in update.call_args_list)


def test_fallback_failure_retains_both_errors(monkeypatch, tmp_path):
    from packages.core import background_runner as runner
    from packages.jobs.window import HiddenWindowUnavailable
    job = {'jobId':'owned', 'argv':['missing'], 'cwd':str(tmp_path), 'logPath':str(tmp_path/'log')}
    monkeypatch.setattr(runner, 'DesktopCommand', Mock(side_effect=HiddenWindowUnavailable('hidden failure')))
    monkeypatch.setattr(runner.subprocess, 'Popen', Mock(side_effect=OSError('visible failure')))
    monkeypatch.setattr(runner.jobs, 'get', lambda _: job)
    monkeypatch.setattr(runner.jobs, 'claim_hidden_runner', lambda _, **kwargs: True)
    monkeypatch.setattr(runner.jobs, 'runner_spawn', lambda _, factory, **kwargs: factory())
    update = Mock(); monkeypatch.setattr(runner.jobs, 'runner_update', update)
    monkeypatch.setattr(sys, 'argv', ['runner', '--job-id', 'owned'])
    assert runner.main() == 1
    assert 'hidden failure' in update.call_args.kwargs['error']
    assert 'visible failure' in update.call_args.kwargs['error']


def test_cleanup_failure_blocks_visible_fallback(monkeypatch, tmp_path):
    from packages.core import background_runner as runner
    from packages.jobs.window_guard import OwnedWindowGuard
    from packages.jobs.desktop import HiddenDesktop
    visible = Mock()
    job = {'jobId':'owned', 'argv':[sys.executable], 'cwd':str(tmp_path), 'logPath':str(tmp_path/'log')}
    monkeypatch.setattr(OwnedWindowGuard, 'start', lambda self: False)
    original_close = HiddenDesktop.close
    def failed_close(self):
        original_close(self)
        raise RuntimeError('cleanup unconfirmed')
    monkeypatch.setattr(HiddenDesktop, 'close', failed_close)
    monkeypatch.setattr(runner.subprocess, 'Popen', visible)
    monkeypatch.setattr(runner.jobs, 'get', lambda _: job)
    monkeypatch.setattr(runner.jobs, 'claim_hidden_runner', lambda _, **kwargs: True)
    monkeypatch.setattr(runner.jobs, 'runner_spawn', lambda _, factory, **kwargs: factory())
    update = Mock(); monkeypatch.setattr(runner.jobs, 'runner_update', update)
    monkeypatch.setattr(sys, 'argv', ['runner', '--job-id', 'owned'])
    assert runner.main() == 1
    visible.assert_not_called()
    assert 'cleanup unconfirmed' in update.call_args.kwargs['error']


def test_independent_runner_desktop_failure_real_visible_process(monkeypatch, tmp_path):
    import asyncio
    from packages.core.platform.desktop_win import HiddenDesktop
    from packages.jobs.independent_executor import ExecutionStore
    from packages.jobs.independent_execution_runner import execute_claimed
    from packages.jobs.window import HiddenWindowUnavailable
    from tests.test_job_final_independent_execution import claim
    marker = tmp_path / 'marker'
    code = "import pathlib,sys; pathlib.Path(sys.argv[1]).open('a').write('once\\n'); print('stdout'); print('stderr', file=sys.stderr)"
    store = ExecutionStore(tmp_path / 'canonical')
    doc = claim(store, {'operation': 'process', 'argv': [sys.executable, '-c', code, str(marker)], 'cwd': str(tmp_path)})
    monkeypatch.setattr(HiddenDesktop, '__init__', Mock(side_effect=HiddenWindowUnavailable('desktop unavailable')))
    result = asyncio.run(execute_claimed(store, doc))['result']
    assert result['outcome'] == 'succeeded'
    assert result['receipt']['effectiveHideWindow'] is False
    assert result['receipt']['hiddenWindowFailure'] == 'desktop unavailable'
    assert result['cleanupProof']['confirmed'] is True
    assert marker.read_text().splitlines() == ['once']
    log = '\n'.join(p.read_text() for p in store.root.rglob('*.log'))
    assert 'desktop unavailable' in log and 'stdout' in log and 'stderr' in log


def test_independent_fallback_cancels_owned_tree(monkeypatch, tmp_path):
    import asyncio
    import threading
    import time
    import psutil
    from packages.core.platform.desktop_win import HiddenDesktop
    from packages.jobs.independent_executor import ExecutionStore
    from packages.jobs.independent_execution_runner import execute_claimed
    from packages.jobs.window import HiddenWindowUnavailable
    from tests.test_job_final_independent_execution import claim, attempt
    marker = tmp_path / 'child-pid'
    code = ("import subprocess,sys,time,pathlib; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)")
    store = ExecutionStore(tmp_path / 'canonical')
    doc = claim(store, {'operation': 'process', 'argv': [sys.executable, '-c', code, str(marker)], 'cwd': str(tmp_path)})
    monkeypatch.setattr(HiddenDesktop, '__init__', Mock(side_effect=HiddenWindowUnavailable('desktop unavailable')))
    thread = threading.Thread(target=lambda: asyncio.run(execute_claimed(store, doc)))
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        assert marker.exists()
        child = psutil.Process(int(marker.read_text()))
        birth = child.create_time()
        store.request_cleanup(doc['effectId'], attempt())
        thread.join(15)
        assert not thread.is_alive()
        final = store.get(doc['effectId'])
        assert final['cleanupRequest']['status'] == 'confirmed'
        assert final['result']['cleanupProof']['confirmed'] is True
        assert final['result']['receipt']['effectiveHideWindow'] is False
        try:
            child.wait(timeout=5)
        except psutil.NoSuchProcess:
            pass
        assert not psutil.pid_exists(child.pid) or psutil.Process(child.pid).create_time() != birth
    finally:
        if thread.is_alive():
            store.request_cleanup(doc['effectId'], attempt())
            thread.join(15)
