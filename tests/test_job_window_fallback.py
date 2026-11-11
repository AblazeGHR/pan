import os
import sys
from unittest.mock import MagicMock, Mock
import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows desktop launch')

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
    monkeypatch.setattr(runner.jobs, 'claim_hidden_runner', lambda _: True)
    monkeypatch.setattr(runner.jobs, 'runner_spawn', lambda _, factory: factory())
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
    monkeypatch.setattr(runner.jobs, 'claim_hidden_runner', lambda _: True)
    monkeypatch.setattr(runner.jobs, 'runner_spawn', lambda _, factory: factory())
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
    monkeypatch.setattr(runner.jobs, 'claim_hidden_runner', lambda _: True)
    monkeypatch.setattr(runner.jobs, 'runner_spawn', lambda _, factory: factory())
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
    monkeypatch.setattr(runner.jobs, 'claim_hidden_runner', lambda _: True)
    monkeypatch.setattr(runner.jobs, 'runner_spawn', lambda _, factory: factory())
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
    monkeypatch.setattr(runner.jobs, 'claim_hidden_runner', lambda _: True)
    monkeypatch.setattr(runner.jobs, 'runner_spawn', lambda _, factory: factory())
    update = Mock(); monkeypatch.setattr(runner.jobs, 'runner_update', update)
    monkeypatch.setattr(sys, 'argv', ['runner', '--job-id', 'owned'])
    assert runner.main() == 1
    visible.assert_not_called()
    assert 'cleanup unconfirmed' in update.call_args.kwargs['error']


def test_independent_runner_desktop_failure_real_visible_process(monkeypatch, tmp_path):
    from packages.jobs import independent_runner as runner
    from packages.jobs.independent_service import JobStore
    from packages.jobs import desktop
    from packages.jobs.window import HiddenWindowUnavailable
    marker = tmp_path / 'marker'
    code = "import pathlib,sys; pathlib.Path(sys.argv[1]).open('a').write('once\\n'); print('stdout'); print('stderr', file=sys.stderr)"
    store = JobStore(str(tmp_path / 'root'))
    with monkeypatch.context() as patch:
        patch.setattr('packages.jobs.independent_service.subprocess.Popen', Mock())
        job = store.create({'name':'owned test', 'argv':[sys.executable,'-c',code,str(marker)], 'cwd':str(tmp_path), 'targetSessionId':'ses_test'})
    monkeypatch.setattr(desktop, 'HiddenDesktop', Mock(side_effect=HiddenWindowUnavailable('desktop unavailable')))
    assert runner.run(store.root, job['jobId']) == 0
    result = store.get(job['jobId'])
    assert result['status'] == 'completed'
    assert result['effectiveHideWindow'] is False and result['hiddenWindowFailure'] == 'desktop unavailable'
    assert marker.read_text().splitlines() == ['once']
    log = open(result['logPath']).read()
    assert 'desktop unavailable' in log and 'stdout' in log and 'stderr' in log


def test_independent_fallback_cancels_owned_tree(monkeypatch, tmp_path):
    import threading
    import time
    import psutil
    from packages.jobs import independent_runner as runner, desktop
    from packages.jobs.independent_service import JobStore
    from packages.jobs.window import HiddenWindowUnavailable
    marker = tmp_path / 'child-pid'
    code = ("import subprocess,sys,time,pathlib; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)")
    store = JobStore(str(tmp_path / 'root'))
    with monkeypatch.context() as patch:
        patch.setattr('packages.jobs.independent_service.subprocess.Popen', Mock())
        job = store.create({'name':'owned cancel', 'argv':[sys.executable,'-c',code,str(marker)], 'cwd':str(tmp_path), 'targetSessionId':'ses_test'})
    monkeypatch.setattr(desktop, 'HiddenDesktop', Mock(side_effect=HiddenWindowUnavailable('desktop unavailable')))
    thread = threading.Thread(target=runner.run, args=(store.root, job['jobId']))
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        assert marker.exists()
        child = psutil.Process(int(marker.read_text()))
        birth = child.create_time()
        with store.connect() as db:
            db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?', (job['jobId'],))
        thread.join(15)
        assert not thread.is_alive()
        assert store.get(job['jobId'])['status'] == 'cancelled'
        try:
            child.wait(timeout=5)
        except psutil.NoSuchProcess:
            pass
        assert not psutil.pid_exists(child.pid) or psutil.Process(child.pid).create_time() != birth
    finally:
        if thread.is_alive():
            with store.connect() as db:
                db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?', (job['jobId'],))
            thread.join(15)
