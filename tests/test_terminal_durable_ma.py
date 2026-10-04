"""Real host exit and detached terminal reattachment; no durability injection."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from packages.core.terminal import identity
from packages.core.terminal.contracts import ProcessStatus
from packages.core.terminal.contracts import StaleLeaseError
from packages.core.terminal.service import CleanupUnconfirmed, TerminalService

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job ownership")
REPO = Path(__file__).resolve().parents[1]
CHILD = r'''
import json, sys, time
from pathlib import Path
from packages.core.terminal.service import TerminalService
root = Path(sys.argv[1])
service = TerminalService(root / 'terminals', log_stderr=False)
view = service.create(cwd=str(root))
tid = view['terminal_id']
token = service.attach(tid, 'durable-original', role='control')
service.input(tid, token, b'set PAN_DURABLE_VALUE=STILL_SAME_SHELL\r')
service.input(tid, token, b'echo ORIGINAL_READY>original-ready\r')
deadline = time.monotonic() + 8
while not (root / 'original-ready').exists() and time.monotonic() < deadline:
    time.sleep(.05)
assert (root / 'original-ready').exists()
detached = service.detach(tid)
(root / 'report.json').write_text(json.dumps({'view': view, 'detach': detached}))
if not detached['detached']:
    service.close(tid)
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
    try:
        stdout, stderr = host.communicate(timeout=35)
        report = json.loads((tmp_path / 'report.json').read_text())
        tid = report['view']['terminal_id']
        if host.returncode == 23:
            pytest.skip('actual ancestor Job disallows breakaway; detach safely refused')
        assert host.returncode == 0, (stdout, stderr)
        assert report['detach']['detached'] is True
        pid = report['view']['pid']
        filetime = int(report['view']['process_created_at_filetime'])
        retained = identity.open_process_for_probe(pid)
        assert retained
        observed = identity.probe_handle(retained, pid)
        assert observed.status is ProcessStatus.ALIVE
        assert observed.identity.created_at_filetime == filetime
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
    finally:
        # An assertion/pipe timeout must not orphan the already-created terminal.
        report_path = tmp_path / 'report.json'
        if tid is None and report_path.exists():
            tid = json.loads(report_path.read_text())['view']['terminal_id']
            service.reconcile()
        if tid:
            deadline = time.monotonic() + 25
            while True:
                try:
                    service.close(tid)
                    break
                except CleanupUnconfirmed:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.1)
            assert not service._store().exists(tid)
        if retained:
            assert identity.wait_state(retained) is ProcessStatus.DEAD
            assert identity.close_handle_checked(retained)
        if host.poll() is None:
            # Popen owns the original process handle, not a fresh bare PID target.
            host.terminate()
            host.wait(timeout=5)
        if host.stdout:
            host.stdout.close()
        if host.stderr:
            host.stderr.close()
