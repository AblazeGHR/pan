"""Production P6 launch readiness includes successful historical assembly."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest


@pytest.mark.parametrize('fail_seed', [False, True])
def test_p6_cold_identity_never_hides_failed_seed(tmp_path, fail_seed):
    root = Path(__file__).resolve().parents[1]
    (root / '.tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='p6-cold-', dir=root / '.tmp') as directory:
        runtime = Path(directory).resolve()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        assert port not in {8767, 8768}
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PAN_')}
        env.update(PAN_E2E_RUNTIME=str(runtime), PAN_PORT=str(port), PYTHONPATH=str(root))
        launcher = root / 'packages/web/e2e/action-p6-server.py'
        command = [sys.executable, str(launcher)]
        if fail_seed:
            command = [sys.executable, '-c',
                'import runpy; from tests.support.legacy_action_source import LegacySource; '
                'LegacySource.put=lambda *a,**k: (_ for _ in ()).throw(RuntimeError("owned seed failure")); '
                f'runpy.run_path({str(launcher)!r},run_name="__main__")']
        log_path = tmp_path / 'server.log'
        with log_path.open('w', encoding='utf-8') as log:
            child = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=log)
            try:
                endpoint = f'http://127.0.0.1:{port}/__e2e/identity'
                deadline = time.monotonic() + 30
                while True:
                    assert child.poll() is None, log_path.read_text(encoding='utf-8')
                    try:
                        response = urlopen(endpoint, timeout=2)
                        break
                    except HTTPError as error:
                        response = error
                        break
                    except URLError:
                        assert time.monotonic() < deadline, log_path.read_text(encoding='utf-8')
                        time.sleep(.1)
                if fail_seed:
                    assert response.code == 500
                    response.close()
                    # A second request must not report ready after the first
                    # seed failed; the old eager flag concealed this failure.
                    with pytest.raises(HTTPError) as second:
                        urlopen(endpoint, timeout=2)
                    assert second.value.code == 500
                    second.value.close()
                    assert not (runtime / 'p6-seed.json').exists()
                    assert 'owned seed failure' in log_path.read_text(encoding='utf-8')
                else:
                    with response:
                        identity = json.load(response)
                    assert identity['pid'] == child.pid
                    assert Path(identity['checkout']).resolve() == root
                    assert Path(identity['runtime']).resolve() == runtime
                    seed = json.loads((runtime / 'p6-seed.json').read_text(encoding='utf-8'))
                    assert seed['ref']['revision'] == 1
                    assert seed['actionRef']['revision'] == 2 and seed['templateId']
                    registry = runtime / 'cooperative-actions/registry.json'
                    before = registry.read_bytes()
                    with urlopen(endpoint, timeout=2) as again:
                        assert json.load(again)['pid'] == child.pid
                    assert registry.read_bytes() == before
                assert not (runtime / 'effects').exists()
            finally:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
