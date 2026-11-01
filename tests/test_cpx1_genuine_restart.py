"""Real main restart in a committed, disposable checkout snapshot on a random port."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time

import httpx
import psutil
import pytest

from packages.core import launcher, platform
from packages.jobs.window import command_window_options


@pytest.mark.skipif(not platform.supports('service.lifecycle'), reason='real lifecycle requires host capability')
def test_genuine_main_restart_preserves_original_effect_and_confirms_new_owner(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    root = (tmp_path / 'owned-checkout').resolve()
    root.mkdir()
    archive = tmp_path / 'owned-source.tar'
    startup_log = tmp_path / 'owned-main-startup.log'
    with archive.open('wb') as output:
        subprocess.run(['git', 'archive', source, 'main.py', 'packages', 'scripts', 'manifest.json'],
                       cwd=repo, stdout=output, check=True)
    with tarfile.open(archive) as stream:
        stream.extractall(root, filter='data')
    assert (root / 'main.py').read_bytes() == (repo / 'main.py').read_bytes()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    config = {'port': port, 'python': sys.executable, 'startup': {'console_hidden': True},
              'qq': {'enabled': False}, 'wechat': {'enabled': False}, 'remote': {'enabled': False},
              'mcp_remote': {'enabled': False}, 'memory': {'enabled': False},
              'session_lifecycle': {'exitStrategy': 'ask', 'startupPreference': 'ask'}}
    (root / 'config.json').write_text(json.dumps(config), encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PAN_') and k.upper() != 'PYTHONPATH'}
    env.update({'PYTHONPATH': str(root), 'PAN_PORT': str(port)})
    # The owned child reproduces the CI console's restrictive encoding; the
    # host implementation must handle it without changing its global streams.
    env.update({'PYTHONIOENCODING': 'cp1252:strict', 'PYTHONUTF8': '0'})
    base = f'http://127.0.0.1:{port}'
    process = None
    effect = None
    doc = None
    old_identity = None
    observed = {'sourceSha': source, 'portAllowed': True, 'childOutputEncoding': 'cp1252:strict'}

    def request(method, route, **kwargs):
        with httpx.Client(base_url=base, timeout=2, trust_env=False) as client:
            response = client.request(method, route, **kwargs)
            response.raise_for_status()
            return response.json()

    def wait(check, timeout, label):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = check()
                if result:
                    return result
            except (httpx.HTTPError, OSError, sqlite3.Error, ValueError):
                pass
            time.sleep(.05)
        if label == 'original main readiness':
            output = startup_log.read_bytes()[-8192:].decode('utf-8', errors='replace') if startup_log.exists() else ''
            output = output.replace(str(root), '<owned-root>').replace(str(tmp_path), '<owned-tmp>')
            pid = process.pid if process else None
            birth = launcher.process_create_time(pid) if pid and process.poll() is None else None
            raise AssertionError('owned restart timed out: ' + label +
                                 f'; childPid={pid}; childBirth={birth}; '
                                 f'childReturncode={process.poll() if process else None}; '
                                 f'listenerOwner={launcher.listener_owner(port)}; '
                                 f'ownedPort={port}; root=<owned-root>; configPort={config["port"]}; '
                                 f'source={source}; startupLogTail={output!r}')
        raise AssertionError('owned restart timed out: ' + label)

    def receipt():
        db_path = root / 'data/lifecycle-executions/executions.sqlite3'
        if not db_path.is_file() or effect is None:
            return None
        with sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT document FROM executions WHERE effect_id=?', (effect,)).fetchone()
        return json.loads(row[0]) if row else None

    def terminate_verified(pid, birth):
        if not pid or not birth:
            return False
        identity = launcher.service_process_identity(pid, root, birth)
        if not identity.get('ok'):
            return False
        target = psutil.Process(pid)
        if abs(target.create_time() - birth) >= .01:
            return False
        target.terminate()
        try:
            target.wait(10)
        except psutil.TimeoutExpired:
            target.kill()
            target.wait(5)
        return True

    try:
        with startup_log.open('wb') as startup_output:
            process = subprocess.Popen([sys.executable, str(root / 'main.py'),
                f'--pan-root-marker={root}', f'--pan-port-marker={port}'], cwd=root, env=env,
                stdin=subprocess.DEVNULL, stdout=startup_output, stderr=subprocess.STDOUT,
                **command_window_options())
        identity = wait(lambda: request('GET', '/api/main/identity'), 25, 'original main readiness')
        old_pid, old_birth = identity['pid'], identity['createdAt']
        old_identity = {'pid': old_pid, 'birth': old_birth}
        assert Path(identity['root']).resolve() == root
        assert launcher.listener_owner(port) == old_pid
        assert launcher.service_process_identity(old_pid, root, old_birth)['ok'] is True
        response = request('POST', '/api/system-actions/main-lifecycle',
                           json={'operation': 'restart', 'options': {'markRunningSessionsOffline': True}})
        assert response['accepted'] is True and response['status'] == 'scheduled'
        effect = response['effectId']
        doc = wait(lambda: (d if (d := receipt()) and d.get('result') else None), 110, 'canonical receipt')
        assert doc['effectId'] == effect and doc['frozenPlan']['arguments']['requestId'] == response['requestId']
        assert doc['result']['outcome'] == 'succeeded', doc['result']
        # The original main has exited and flushed its redirected output: this
        # proves its real console prompt ran under the restricted encoding.
        assert b'Pan: \\u8f93\\u5165' in startup_log.read_bytes()
        observed['restrictedConsolePromptObserved'] = True
        fact = doc['result']['receipt']
        assert fact['phase'] == 'ready' and fact['offlineProof']['ok'] is True
        ready = fact['readyProof']
        assert ready['ok'] is True and ready['newPid'] != old_pid and ready['newPidCreatedAt'] > old_birth
        assert ready['listenerOwner'] == ready['newPid'] and ready['port'] == port
        assert ready['identity']['ok'] is True and Path(ready['identity']['root']).resolve() == root
        assert launcher.listener_owner(port) == ready['newPid']
        assert launcher.service_process_identity(ready['newPid'], root, ready['newPidCreatedAt'])['ok'] is True
        observed.update({'effectId': effect, 'requestId': response['requestId'], 'oldPid': old_pid,
                         'newPid': ready['newPid'], 'oldBirth': old_birth, 'newBirth': ready['newPidCreatedAt'],
                         'outcome': doc['result']['outcome'], 'offline': fact['offlineProof']['ok'], 'ready': ready['ok']})
    finally:
        # Stop only the retained original supervisor identity, if still alive;
        # it must not be able to start a new process after listener cleanup.
        latest = receipt() if effect else None
        supervisor = (latest or doc or {}).get('supervisorIdentity') or {}
        if supervisor:
            from packages.core.platform.process import default_process_owner
            owner = default_process_owner()
            if owner.verify(supervisor).verified_alive:
                psutil.Process(supervisor['pid']).terminate()
                psutil.Process(supervisor['pid']).wait(10)
        listener = launcher.listener_owner(port)
        if listener:
            birth = launcher.process_create_time(listener)
            assert terminate_verified(listener, birth), 'refusing to terminate unverified listener'
        if process is not None and process.poll() is None:
            # Retained Popen is the exact child created by this test.
            process.terminate()
            process.wait(timeout=10)
        wait(lambda: launcher.listener_owner(port) is None, 10, 'owned port release')
        observed['portReleased'] = True
        target = Path(os.environ.get('CPX1_LIFECYCLE_EVIDENCE_ROOT', str(tmp_path)))
        target.mkdir(parents=True, exist_ok=True)
        (target / 'genuine-restart.json').write_text(json.dumps(observed, indent=2), encoding='utf-8')
