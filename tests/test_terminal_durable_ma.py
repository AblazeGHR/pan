"""Real host exit and detached terminal reattachment; no durability injection."""
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from packages.core.terminal import identity
from packages.core.terminal.contracts import ProcessStatus, RuntimeState
from packages.core.terminal.contracts import StaleLeaseError
from packages.core.terminal.service import CleanupUnconfirmed, TerminalService, _TerminalState

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job ownership")
REPO = Path(__file__).resolve().parents[1]
CHILD = r'''
import json, os, sys, time
from pathlib import Path
from packages.core.terminal.service import TerminalService
root = Path(sys.argv[1])
service = TerminalService(root / 'terminals', log_stderr=False)
def publish(report):
    temporary = root / 'report.tmp'
    temporary.write_text(json.dumps(report), encoding='utf-8')
    os.replace(temporary, root / 'report.json')
view = service.create(cwd=str(root))
tid = view['terminal_id']
# Let the parent retain this exact live identity before any refusal cleanup.
publish({'view': view})
deadline = time.monotonic() + 8
while not (root / 'identity-retained').exists() and time.monotonic() < deadline:
    time.sleep(.05)
assert (root / 'identity-retained').exists()
token = service.attach(tid, 'durable-original', role='control')
service.input(tid, token, b'set PAN_DURABLE_VALUE=STILL_SAME_SHELL\r')
service.input(tid, token, b'echo ORIGINAL_READY>original-ready\r')
deadline = time.monotonic() + 8
while not (root / 'original-ready').exists() and time.monotonic() < deadline:
    time.sleep(.05)
assert (root / 'original-ready').exists()
detached = service.detach(tid)
publish({'view': view, 'detach': detached})
if not detached['detached']:
    closed = service.close(tid)
    publish({'view': view, 'detach': detached, 'closed': closed})
    sys.exit(23)
# Exit the actual owning service process, without service.shutdown().
sys.exit(0)
'''


def test_same_service_detach_reconnect_revokes_old_connection_tokens(tmp_path):
    if not (REPO / 'packages/core/terminal/emulator_sidecar/node_modules/@xterm/headless').exists():
        pytest.skip('sidecar dependencies absent')
    service = TerminalService(tmp_path / 'terminals', log_stderr=False)
    view = service.create(cwd=str(tmp_path))
    tid = view['terminal_id']
    try:
        old = service.attach(tid, 'old-controller', role='control')
        result = service.detach(tid)
        if not result['detached']:
            pytest.skip('actual ancestor Job restricts detach')
        # A new observer reconnects the IPC bridge without claiming control.
        observer = service.attach(tid, 'new-observer')
        assert service.control_holder(tid) is None
        with pytest.raises(StaleLeaseError):
            service.input(tid, old, b'OLD_MUST_NOT_REVIVE\r')
        control = service.attach(tid, 'new-controller', role='control')
        service.input(tid, control, b'echo SAME_SERVICE_RECONNECTED>reconnect-witness\r')
        deadline = time.monotonic() + 8
        witness = tmp_path / 'reconnect-witness'
        while not witness.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert witness.read_text().strip() == 'SAME_SERVICE_RECONNECTED'
        assert service.get(tid)['pid'] == view['pid']
        service.release_attachment(observer)
    finally:
        deadline = time.monotonic() + 25
        while True:
            try:
                service.close(tid)
                break
            except CleanupUnconfirmed:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.1)


def _cleanup_cross_host_terminal(service, tid, retained):
    """Recover a live owner, or verify already-completed identity-bound cleanup."""
    deadline = time.monotonic() + 25
    while True:
        record = service._registry.get(tid)
        assert retained, "missing retained runner identity"
        probe = identity.probe_handle(retained, record.pid)
        assert probe.identity is not None
        assert probe.identity.pid == record.pid
        assert probe.identity.created_at_filetime == int(record.process_created_at_filetime)
        assert probe.status in (ProcessStatus.ALIVE, ProcessStatus.DEAD), "runner death unknown"
        service.reconcile()
        try:
            if tid in service._states:
                service.close(tid)
            elif probe.status is ProcessStatus.ALIVE:
                # A managed host can die before detach. Recover its verified IPC
                # owner using the existing bounded persisted-stop path.
                service._retry_persisted_stop(record)
            record = service._registry.get(tid)
            evidence = _TerminalState(terminal_id=tid, record=record,
                                      runner_pid=record.pid,
                                      runner_filetime=record.process_created_at_filetime)
            complete = (identity.wait_state(retained) is ProcessStatus.DEAD
                        and record.status is RuntimeState.EXITED
                        and record.cleanup_pending is False
                        and service._runner_cleanup_record_confirmed(evidence)
                        and service._launcher_status_evidence(evidence).get('engine_converged') is True
                        and not service._store().exists(tid))
            if complete:
                return
        except CleanupUnconfirmed:
            if time.monotonic() >= deadline:
                raise
        assert time.monotonic() < deadline, "cross-host cleanup lacks proof"
        time.sleep(.1)


def _release_cross_host_resources(host, retained, cleanup):
    """Attempt every release; retain the primary failure alongside cleanup errors."""
    primary = sys.exception()
    errors = []
    def attempt(action):
        try:
            action()
        except Exception as exc:
            errors.append(exc)
    def stop_host():
        if host.poll() is None:
            host.terminate()  # Popen's owned handle, never a fresh PID.
            host.wait(timeout=5)
    attempt(stop_host)
    attempt(cleanup)
    if retained:
        def release_handle():
            assert identity.close_handle_checked(retained)
        attempt(release_handle)
    for stream in (host.stdout, host.stderr):
        if stream:
            attempt(stream.close)
    if errors:
        raise BaseExceptionGroup('cross-host test/cleanup failures',
                                 ([primary] if primary is not None else []) + errors)


def test_detached_shell_survives_real_service_host_exit_and_reconnect(tmp_path):
    if not (REPO / 'packages/core/terminal/emulator_sidecar/node_modules/@xterm/headless').exists():
        pytest.skip('sidecar dependencies absent')
    script = tmp_path / 'host.py'
    script.write_text(CHILD, encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(REPO), PYTHONIOENCODING='utf-8')
    host = subprocess.Popen([getattr(sys, '_base_executable', sys.executable), str(script), str(tmp_path)],
                            cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    service = TerminalService(tmp_path / 'terminals', log_stderr=False)
    tid = None
    retained = None
    refused = False
    try:
        deadline = time.monotonic() + 35
        report_path = tmp_path / 'report.json'
        while not report_path.exists():
            assert host.poll() is None, "host exited before publishing runner identity"
            assert time.monotonic() < deadline, "host identity publication timed out"
            time.sleep(.05)
        report = json.loads(report_path.read_text())
        tid = report['view']['terminal_id']
        pid = report['view']['pid']
        filetime = int(report['view']['process_created_at_filetime'])
        retained = identity.open_process_for_probe(pid)
        assert retained
        observed = identity.probe_handle(retained, pid)
        assert observed.status is ProcessStatus.ALIVE
        assert observed.identity.pid == pid
        assert observed.identity.created_at_filetime == filetime
        (tmp_path / 'identity-retained').touch()
        stdout, stderr = host.communicate(timeout=max(0.0, deadline - time.monotonic()))
        report = json.loads((tmp_path / 'report.json').read_text())
        tid = report['view']['terminal_id']
        if host.returncode == 23:
            assert report['detach']['detached'] is False
            assert report['closed']['status'] == 'exited'
            assert report['closed']['cleanup_pending'] is False
            assert identity.wait_state(retained) is ProcessStatus.DEAD
            assert not service._store().exists(tid)
            refused = True
        else:
            assert host.returncode == 0, stderr.decode('utf-8', errors='replace')
            assert report['detach']['detached'] is True
            _verify_detached_reconnect(service, tmp_path, report, retained)
    finally:
        def cleanup():
            if tid is not None:
                _cleanup_cross_host_terminal(service, tid, retained)
            else:
                # Startup can fail before create publishes a view; still inspect
                # every record left in the exclusively owned test root.
                for record in service._registry.list():
                    handle = identity.open_process_for_probe(record.pid)
                    try:
                        _cleanup_cross_host_terminal(service, record.terminal_id, handle)
                    finally:
                        if handle:
                            assert identity.close_handle_checked(handle)
        _release_cross_host_resources(host, retained, cleanup)
    if refused:
        pytest.skip('actual ancestor Job disallows breakaway; detach safely refused')


def _verify_detached_reconnect(service, tmp_path, report, retained):
    tid = report['view']['terminal_id']
    pid = report['view']['pid']
    filetime = int(report['view']['process_created_at_filetime'])
    # Beyond managed lease grace; neither surviving heartbeats nor parent ownership.
    time.sleep(2.5)
    assert identity.wait_state(retained) is ProcessStatus.ALIVE
    reconciled = service.reconcile()
    assert {'terminal_id': tid} in reconciled['buckets']['alive']
    view = service.get(tid)
    assert view['pid'] == pid and int(view['process_created_at_filetime']) == filetime
    token = service.attach(tid, 'durable-new-controller', role='control')
    service.input(tid, token, b'echo %PAN_DURABLE_VALUE%>reconnected-value\r')
    destination = tmp_path / 'reconnected-value'
    deadline = time.monotonic() + 8
    while not destination.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    assert destination.read_text().strip() == 'STILL_SAME_SHELL'
    assert service._store().exists(tid)


def _fake_cleanup_owner(monkeypatch, *, status=ProcessStatus.DEAD, proven=True):
    record = SimpleNamespace(terminal_id='term_cleanup', pid=42,
                             process_created_at_filetime=123,
                             status=RuntimeState.EXITED if status is ProcessStatus.DEAD else RuntimeState.RUNNING,
                             cleanup_pending=False)
    facts = {'status': status, 'proven': proven, 'secret': status is not ProcessStatus.DEAD,
             'reconciles': 0, 'stops': 0, 'released': False}
    class Owner:
        _states = {}
        _registry = SimpleNamespace(get=lambda tid: record, list=lambda: [record])
        def reconcile(self):
            facts['reconciles'] += 1
        def close(self, tid):
            from packages.core.terminal.service import TerminalNotAttached
            raise TerminalNotAttached('not-attached')
        def _retry_persisted_stop(self, item):
            assert item is record and facts['status'] is ProcessStatus.ALIVE
            facts['stops'] += 1
            facts['status'] = ProcessStatus.DEAD
            facts['secret'] = False
            record.status = RuntimeState.EXITED
        def _store(self):
            return SimpleNamespace(exists=lambda tid: facts['secret'])
        def _runner_cleanup_record_confirmed(self, state):
            return facts['proven']
        def _launcher_status_evidence(self, state):
            return {'engine_converged': facts['proven']}
    monkeypatch.setattr(identity, 'probe_handle', lambda handle, pid: SimpleNamespace(
        status=facts['status'], identity=SimpleNamespace(pid=42, created_at_filetime=123)))
    monkeypatch.setattr(identity, 'wait_state', lambda handle: facts['status'])
    def release(handle):
        facts['released'] = True
        return True
    monkeypatch.setattr(identity, 'close_handle_checked', release)
    return Owner(), facts


def test_refused_host_skip_requires_verified_cleanup_and_releases_resources(tmp_path, monkeypatch):
    service, facts = _fake_cleanup_owner(monkeypatch, status=ProcessStatus.ALIVE)
    view = {'terminal_id': 'term_cleanup', 'pid': 42, 'process_created_at_filetime': 123}
    class Host:
        returncode = None
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        def __init__(self, *args, **kwargs):
            (tmp_path / 'report.json').write_text(json.dumps({'view': view}))
        def communicate(self, timeout):
            facts['identity_retained_before_close'] = (tmp_path / 'identity-retained').exists()
            facts['status'] = ProcessStatus.DEAD
            facts['secret'] = False
            service._registry.get('term_cleanup').status = RuntimeState.EXITED
            (tmp_path / 'report.json').write_text(json.dumps({
                'view': view, 'detach': {'detached': False},
                'closed': {'status': 'exited', 'cleanup_pending': False}}))
            self.returncode = 23
            return b'', b''
        def poll(self):
            return self.returncode
    monkeypatch.setattr(subprocess, 'Popen', Host)
    monkeypatch.setattr(identity, 'open_process_for_probe', lambda pid: 99)
    monkeypatch.setattr(sys.modules[__name__], 'TerminalService', lambda *a, **kw: service)
    with pytest.raises(pytest.skip.Exception, match='actual ancestor Job'):
        test_detached_shell_survives_real_service_host_exit_and_reconnect(tmp_path)
    assert facts['reconciles'] == 1 and facts['released']
    assert facts['identity_retained_before_close']
    assert Host.stdout.closed and Host.stderr.closed


def test_cross_host_cleanup_recovers_live_managed_owner(tmp_path, monkeypatch):
    service, facts = _fake_cleanup_owner(monkeypatch, status=ProcessStatus.ALIVE)
    _cleanup_cross_host_terminal(service, 'term_cleanup', 99)
    assert facts['reconciles'] == 1 and facts['stops'] == 1
    assert facts['status'] is ProcessStatus.DEAD and not facts['secret']


@pytest.mark.parametrize('status,proven', [(ProcessStatus.UNKNOWN, True), (ProcessStatus.DEAD, False)])
def test_cross_host_cleanup_rejects_unknown_death_or_missing_engine_proof(monkeypatch, status, proven):
    service, facts = _fake_cleanup_owner(monkeypatch, status=status, proven=proven)
    clock = iter([0, 26])
    monkeypatch.setattr(time, 'monotonic', lambda: next(clock))
    with pytest.raises(AssertionError):
        _cleanup_cross_host_terminal(service, 'term_cleanup', 99)
    assert facts['stops'] == 0


def test_cross_host_release_preserves_primary_and_attempts_every_resource(monkeypatch):
    service, facts = _fake_cleanup_owner(monkeypatch)
    host = SimpleNamespace(stdout=io.BytesIO(), stderr=io.BytesIO(),
                           poll=lambda: None, terminate=lambda: facts.update(terminated=True),
                           wait=lambda timeout: 0)
    def failed_cleanup():
        raise RuntimeError('cleanup missing proof')
    with pytest.raises(BaseExceptionGroup) as caught:
        try:
            raise subprocess.TimeoutExpired('host', 35)
        finally:
            _release_cross_host_resources(host, 99, failed_cleanup)
    assert isinstance(caught.value.exceptions[0], subprocess.TimeoutExpired)
    assert isinstance(caught.value.exceptions[1], RuntimeError)
    assert facts['terminated'] and facts['released']
    assert host.stdout.closed and host.stderr.closed
