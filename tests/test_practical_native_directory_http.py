"""Production React / real HTTP picker wiring in a disposable owned server."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid


def test_combined_native_directory_production_wiring():
    root = Path(__file__).resolve().parents[1]
    runtime = root / 'packages/web/test-results' / ('native-directory-' + uuid.uuid4().hex)
    runtime.mkdir(parents=True)
    out = root / '.tmp/practical-main/production'
    out.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PAN_')}
    env['PAN_E2E_BASE_URL'] = f'http://127.0.0.1:{port}'
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    with (out / 'native-directory-server.log').open('w', encoding='utf-8') as log:
        server = subprocess.Popen([sys.executable, str(root / 'packages/web/e2e/new-session-native-directory.server.py'),
            '--runtime', str(runtime), '--port', str(port)], cwd=root, env=env,
            stdout=log, stderr=subprocess.STDOUT, **kwargs)
        try:
            deadline = time.monotonic() + 45
            identity = None
            while time.monotonic() < deadline:
                assert server.poll() is None, 'owned fixture exited during startup'
                try:
                    with urllib.request.urlopen(env['PAN_E2E_BASE_URL'] + '/__e2e/identity', timeout=1) as response:
                        identity = json.load(response)
                    break
                except (OSError, ValueError):
                    time.sleep(.1)
            assert identity and Path(identity['checkout']).resolve() == root
            assert identity['port'] == port
            result = subprocess.run([shutil.which('node'), 'e2e/new-session-native-directory.e2e.mjs'],
                cwd=root / 'packages/web', env=env, capture_output=True, text=True, encoding='utf-8', timeout=90, **kwargs)
            (out / 'native-directory-browser.json').write_text(result.stdout, encoding='utf-8')
            (out / 'native-directory-browser.stderr').write_text(result.stderr, encoding='utf-8')
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            server.terminate()
            server.wait(timeout=20)
