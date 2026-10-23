"""Failed HTTP fixture admission must retire its owned process tree."""
import socket

import psutil
import pytest

from tests import test_real_queue_http_e2e as real


def test_failed_readiness_cleans_owned_server_and_child(tmp_path, monkeypatch):
    launcher = tmp_path / 'owned_startup.py'
    launcher.write_text('''import json,os,subprocess,sys,time
from pathlib import Path
root=Path(sys.argv[sys.argv.index('--data-root')+1])
if '--owned-child' in sys.argv:
    time.sleep(60)
else:
    child=subprocess.Popen([sys.executable,__file__,*sys.argv[1:],'--owned-child'],
        creationflags=0x08000000 if os.name=='nt' else 0)
    (root/'owned-pids.json').write_text(json.dumps({'parent':os.getpid(),'child':child.pid}))
    time.sleep(60)
''', encoding='utf-8')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    assert port not in {8767, 8768}
    monkeypatch.setattr(real, 'PORT', port)
    monkeypatch.setattr(real, 'SERVER_LAUNCHER', launcher)
    runtime = real._PanRuntime(tmp_path / 'profile')
    marker = runtime.data_root / 'owned-pids.json'
    original = real._wait_for
    captured = []

    def fail_readiness(predicate, *, timeout, label):
        if label != 'isolated FastAPI readiness':
            return original(predicate, timeout=timeout, label=label)
        original(marker.exists, timeout=5, label='owned startup child ready')
        parent = psutil.Process(runtime.process.pid)
        for process in [parent, *parent.children(recursive=True)]:
            captured.append((process, process.create_time()))
        raise AssertionError('injected readiness failure')

    monkeypatch.setattr(real, '_wait_for', fail_readiness)
    try:
        with pytest.raises(AssertionError, match='isolated server failed to start'):
            runtime.start()
        assert runtime.process is None, 'Failed admission must not leave a live server handle'
        _, remaining = psutil.wait_procs([p for p, _ in captured], timeout=5)
        assert not remaining, [(p.pid, p.status()) for p in remaining]
        assert real._port_is_free(port)
    finally:
        for process, created in reversed(captured):
            try:
                if process.create_time() == created and str(launcher) in ' '.join(process.cmdline()):
                    process.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs([p for p, _ in captured], timeout=5)
