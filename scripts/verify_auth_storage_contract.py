"""Standard-library native storage contract; requires an explicit owned root."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from packages.automation.auth import UserAuth


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owned-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--native-temp', action='store_true',
                        help='Use a new native POSIX temporary directory, e.g. when checkout is on DrvFS')
    args = parser.parse_args()
    root = args.owned_root.resolve()
    # Cleanup is confined to a newly-created temporary child under this tree.
    if not root.is_relative_to(REPO) or root == REPO:
        parser.error('owned root must be a child of this isolated checkout')
    root.mkdir(parents=True, exist_ok=True)
    report = {'platform': sys.platform, 'crossProcessSessions': False,
              'profileIsolation': False, 'coldRestart': False, 'revocation': False,
              'passwordHash': False, 'privateModes': None}
    if args.native_temp and os.name == 'nt':
        parser.error('native-temp is only for the POSIX contract')
    with tempfile.TemporaryDirectory(prefix='pan-auth-owned-', dir=None if args.native_temp else root) as directory:
        auth_root = Path(directory)
        auth = UserAuth(auth_root)
        password = 'Aa1!' + secrets.token_urlsafe(24)
        auth.initialize(password)
        auth.change_session_days(7)
        token = auth.login(password)
        report['coldRestart'] = UserAuth(auth_root).session(token)['authenticated']
        code = '''import sys
from packages.automation.auth import UserAuth
auth=UserAuth(sys.argv[1])
password=sys.stdin.readline().strip()
for _ in range(3): auth.login(password)
'''
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PAN_')}
        env['PYTHONPATH'] = str(REPO)
        def child(_):
            result = subprocess.run([sys.executable, '-c', code, str(auth_root)], input=password + '\n',
                env=env, capture_output=True, text=True, timeout=20,
                **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
            assert result.returncode == 0, 'Owned child storage contract failed'
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(child, range(2)))
        report['crossProcessSessions'] = len(json.loads(auth.sessions_path.read_text())['sessions']) == 7
        other = UserAuth(auth_root / 'other')
        other.initialize(password)
        report['profileIsolation'] = not other.session(token)['authenticated']
        record = auth.path.read_text()
        report['passwordHash'] = password not in record and hashlib.sha256(password.encode()).hexdigest() not in record
        auth.revoke()
        report['revocation'] = not UserAuth(auth_root).session(token)['authenticated']
        if os.name != 'nt':
            report['privateModes'] = all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in
                (auth.path, auth.sessions_path, auth.path.with_name('auth.lock')))
            from packages.core.platform.plugin_ipc import UnixEndpoint
            endpoint = UnixEndpoint()
            try:
                assert stat.S_IMODE(endpoint.directory.stat().st_mode) == 0o700
                assert len(os.fsencode(endpoint.address)) < 104
                report['privateIpcEndpoint'] = True
            finally:
                endpoint.close()
        assert all(value is True for key, value in report.items() if key not in {'platform', 'privateModes'})
        assert report['privateModes'] is not False
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
