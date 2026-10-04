"""Real Pan startup/shutdown/restart with owned terminal processes and temp data."""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest
from websockets.sync.client import connect

from packages.core.terminal import identity
from packages.core.terminal.contracts import ProcessStatus
from packages.core.terminal.service import CleanupUnconfirmed, TerminalService

REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows full layout')


def until(fn, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except (httpx.HTTPError, OSError):
            pass
        time.sleep(.05)
    raise AssertionError('owned lifecycle condition did not converge')


def wait_witness_text(path, expected, seconds=15):
    # cmd redirection creates/truncates the file before echo writes its bytes.
    # Existence alone is not evidence that the shell command has completed.
    return until(lambda: path.exists() and path.read_text().strip() == expected, seconds)


@pytest.mark.parametrize('intermediate', ['', 'UNCHANGED_'])
def test_witness_wait_requires_complete_content(intermediate):
    from unittest.mock import Mock
    path = Mock()
    path.exists.return_value = True
    path.read_text.side_effect = [intermediate, 'UNCHANGED_SHELL\n']
    assert wait_witness_text(path, 'UNCHANGED_SHELL') is True
    assert path.read_text.call_count == 2


def test_witness_wait_wrong_content_does_not_pass():
    from unittest.mock import Mock
    path = Mock()
    path.exists.return_value = True
    path.read_text.return_value = 'WRONG_SHELL'
    with pytest.raises(AssertionError, match='did not converge'):
        wait_witness_text(path, 'UNCHANGED_SHELL', seconds=.001)


class Pan:
    def __init__(self, root):
        self.root = root
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            self.port = reservation.getsockname()[1]
        self.base = f'http://127.0.0.1:{self.port}'
        self.env = dict(os.environ, PYTHONPATH=str(REPO), PAN_PORT=str(self.port),
                        PAN_HOST='127.0.0.1', PAN_TERMINALS_DIR=str(root / 'terminals'),
                        PAN_BACKGROUND_JOBS_DIR=str(root / 'jobs'), PAN_SCHEDULER_DIR=str(root / 'scheduler'),
                        PAN_AGENT_SESSION_ID='', PYTHONIOENCODING='utf-8')
        self.client = httpx.Client(base_url=self.base, headers={'Origin': self.base}, trust_env=False,
                                   timeout=30)
        self.process = None
        self.log = None

    def start(self):
        self.log = (self.root / 'pan.log').open('a', encoding='utf-8')
        self.process = subprocess.Popen(
            [getattr(sys, '_base_executable', sys.executable),
             str(REPO / 'tests/support/terminal_pan_server.py'), '--root', str(self.root), '--port', str(self.port)],
            env=self.env, cwd=REPO, stdout=self.log, stderr=subprocess.STDOUT)
        until(lambda: self.client.get('/api/terminals').status_code == 200, 30)

    def stop(self, crash=False):
        if self.process is None:
            return
        if self.process.poll() is None:
            if crash:
                self.process.terminate()  # original owned Popen handle
            else:
                response = self.client.post('/api/internal/main/shutdown')
                assert response.json()['ok'] is True
            self.process.wait(timeout=40)
        if not crash:
            assert self.process.returncode == 0
        self.log.close()

    def command(self, tid, text):
        with connect(f'ws://127.0.0.1:{self.port}/ws/terminal/{tid}?cursor=0', origin=self.base) as ws:
            ws.send(json.dumps({'v': 1, 'type': 'command', 'terminal_id': tid, 'op': 'claim'}))
            generation = None
            for _ in range(60):
                frame = json.loads(ws.recv(timeout=5))
                if frame.get('type') == 'claim-result' and frame.get('role') == 'control':
                    generation = frame['generation']
                    break
            assert generation is not None
            ws.send(json.dumps({'v': 1, 'type': 'command', 'terminal_id': tid, 'op': 'input',
                                'generation': generation, 'data_b64': base64.b64encode(text.encode()).decode()}))
            for _ in range(60):
                frame = json.loads(ws.recv(timeout=5))
                if frame.get('type') == 'input-result':
                    assert frame['accepted'] is True
                    return
            raise AssertionError('actual WS input was not accepted')


@pytest.mark.parametrize('crash', [False, True])
def test_real_pan_managed_cleanup_detached_restart_same_shell(tmp_path, crash):
    if not (REPO / 'packages/core/terminal/emulator_sidecar/node_modules/@xterm/headless').exists():
        pytest.skip('sidecar dependencies absent')
    (tmp_path / 'config.json').write_text(json.dumps({'plugin_manifests': [], 'qq': {}}))
    pan = Pan(tmp_path)
    records, handles = [], []
    try:
        pan.start()
        # Negative Origin and real readiness: rejection is not an all-deny fake pass.
        assert pan.client.get('/api/terminals', headers={'Origin': 'http://wrong.invalid'}).status_code == 403
        for _ in range(2):
            response = pan.client.post('/api/terminals', json={'cwd': str(tmp_path)})
            assert response.status_code == 200, response.text
            view = response.json()['result']
            records.append(view)
            handle = identity.open_process_for_probe(view['pid'])
            assert handle
            handles.append(handle)
            assert identity.read_creation_filetime(handle) == int(view['process_created_at_filetime'])
        managed, detached = records
        tid = detached['terminal_id']
        pan.command(tid, 'set PAN_RESTART_VALUE=UNCHANGED_SHELL\r')
        response = pan.client.post(f'/api/terminals/{tid}/detach', json={})
        assert response.status_code == 200, response.text
        pan.stop(crash=crash)
        until(lambda: identity.wait_state(handles[0]) is ProcessStatus.DEAD, 15)
        assert identity.wait_state(handles[1]) is ProcessStatus.ALIVE
        if not crash:
            assert not (tmp_path / 'terminals/secrets' / f"{managed['terminal_id']}.secret").exists()
        pan.start()
        managed_after = pan.client.get(f"/api/terminals/{managed['terminal_id']}").json()['result']
        assert managed_after['status'] == 'exited'
        assert not (tmp_path / 'terminals/secrets' / f"{managed['terminal_id']}.secret").exists()
        restored = pan.client.get(f'/api/terminals/{tid}').json()['result']
        assert restored['pid'] == detached['pid']
        assert restored['process_created_at_filetime'] == detached['process_created_at_filetime']
        witness = tmp_path / 'restart-witness'
        pan.command(tid, f'echo %PAN_RESTART_VALUE%>"{witness}"\r')
        wait_witness_text(witness, 'UNCHANGED_SHELL')
        assert witness.read_text().strip() == 'UNCHANGED_SHELL'
        def close():
            response = pan.client.post(f'/api/terminals/{tid}/close', json={})
            return response.status_code == 200
        until(close, 25)
        until(lambda: identity.wait_state(handles[1]) is ProcessStatus.DEAD)
        pan.stop()
    finally:
        if pan.process is not None and pan.process.poll() is None:
            pan.stop(crash=True)
        # On assertion failure, use retained identity observations and the original
        # persisted terminal credential, never process-name or command-line kills.
        cleanup = TerminalService(tmp_path / 'terminals', log_stderr=False,
            identity_probe=lambda pid: identity.probe_handle(handles[[r['pid'] for r in records].index(pid)], pid))
        cleanup.reconcile()
        for record in records:
            handle = handles[records.index(record)]
            if identity.wait_state(handle) is ProcessStatus.ALIVE:
                until(lambda: identity.wait_state(handle) is ProcessStatus.DEAD
                      or _close_owned(cleanup, record['terminal_id']), 25)
        for handle in handles:
            assert identity.wait_state(handle) is ProcessStatus.DEAD
            assert identity.close_handle_checked(handle)
        pan.client.close()


def _close_owned(service, tid):
    try:
        service.close(tid)
        return True
    except CleanupUnconfirmed:
        return False
