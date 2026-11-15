"""Same durable storage contract on Windows Named Mutex and POSIX flock."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import secrets

from packages.automation.auth import UserAuth
from packages.jobs.window import command_window_options


def test_independent_processes_do_not_lose_sessions(tmp_path):
    password = 'Aa1!' + secrets.token_urlsafe(24)
    auth = UserAuth(tmp_path)
    auth.initialize(password)
    code = '''
import sys
import secrets
from packages.automation.auth import UserAuth
auth = UserAuth(sys.argv[1])
for _ in range(3):
    auth.login(sys.stdin.readline().strip())
'''
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PAN_')}
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
    def child(_):
        result = subprocess.run([sys.executable, '-c', code, str(tmp_path)],
            input=(password + '\n') * 3, capture_output=True, text=True, encoding='utf-8',
            env=env, timeout=20, **command_window_options())
        assert result.returncode == 0, result.stderr
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(child, range(2)))
    assert len(json.loads(auth.sessions_path.read_text())['sessions']) == 6
    assert password not in auth.path.read_text()
    restarted = UserAuth(tmp_path)
    restarted.revoke()
    assert json.loads(auth.sessions_path.read_text())['sessions'] == {}


def test_profile_boundaries_and_private_file_modes(tmp_path):
    import stat
    password = 'Aa1!' + secrets.token_urlsafe(24)
    one, two = UserAuth(tmp_path / 'one'), UserAuth(tmp_path / 'two')
    one.initialize(password)
    two.initialize(password)
    token = one.login(password)
    assert not two.session(token)['authenticated']
    assert one.session(token)['authenticated']
    if os.name != 'nt':
        for path in (one.path, one.sessions_path, one.path.with_name('auth.lock')):
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
