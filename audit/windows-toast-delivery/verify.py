"""Opt-in real OS regression: baseline errors, fixed dispatch, dedup, private Job.

Run through scripts/run_tests_isolated.py. Posts three marked owned toasts.
Queries only its exact owned tags, directly in read-only mode; no DB copies.
No browser activation is simulated or claimed as a native toast click.
"""
import asyncio
import http.server
import json
import logging
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.core import notifications, windows_toast
from packages.jobs.desktop import DesktopCommand

HERE = Path(__file__).resolve().parent
SID = 'ses_owned_toast_20261017'
TAG = 'completion-' + SID
APP = windows_toast.TOAST_APP_ID
BODY = 'OWNED DIAGNOSTIC 20261017 <&> 中文😀 ' + ('<&>中文😀' * 4000)


def owned_row(app_id, tag):
    path = Path(os.environ['LOCALAPPDATA']) / 'Microsoft/Windows/Notifications/wpndatabase.db'
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as con:
        return con.execute('SELECT n.Tag,n."Group",CAST(n.Payload AS TEXT) FROM Notification n '
                           'JOIN NotificationHandler h ON h.RecordId=n.HandlerId '
                           'WHERE h.PrimaryId=? AND n.Tag=?', (app_id, tag)).fetchall()


def child():
    print(json.dumps(windows_toast.show_toast(
        'Pan: OWNED DIAGNOSTIC fixed private Job', BODY, app_id='Pan.Owned.Toast20261017',
        tag='owned-combined', group='Pan-owned-diag')), flush=True)


def main():
    baseline_source = subprocess.run(['git', 'show', 'e51b4ba5:packages/core/windows_toast.py'],
                                     cwd=ROOT, capture_output=True, text=True, check=True).stdout
    baseline = {'__name__': 'baseline_windows_toast'}
    exec(compile(baseline_source, '<baseline>', 'exec'), baseline)
    summary = {'baseline': 'e51b4ba50e22db464b8d45c5f3f1f2d4d99b5d4c'}
    summary['baselineLaunch'] = baseline['show_toast'](
        'Pan: OWNED oversized regression', 'X' * 20000,
        app_id='Pan.Owned.Toast20261017', tag='owned-large-file', group='Pan-owned-diag')
    raw_xml = baseline['build_toast_xml']('Pan: OWNED oversized regression', 'X' * 20000)
    with tempfile.TemporaryDirectory(prefix='pan-toast-owned-') as temporary:
        script = Path(temporary) / 'oversized.ps1'
        # Bypass only the command-line length to independently observe WinRT.
        script.write_text(windows_toast.build_show_script('Pan.Owned.Toast20261017', raw_xml,
                                                          'owned-large-file', 'Pan-owned-diag'),
                          encoding='utf-8')
        result = subprocess.run([windows_toast._powershell_executable(), '-NoLogo', '-NoProfile',
                                 '-NonInteractive', '-STA', '-File', str(script)],
                                capture_output=True, text=True, encoding='utf-8', timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        summary['baselineWinRT'] = {'exitcode': result.returncode,
                                    'result': windows_toast._parse_result(result.stdout)}
    assert summary['baselineLaunch']['code'] == 'windows_system_notification_launch_failed'
    assert summary['baselineWinRT']['result']['ok'] is False

    accesses = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            accesses.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'owned toast diagnostic')
        def log_message(self, *_):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_port
    assert port not in (8767, 8768)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    os.environ['PAN_API_URL'] = f'http://127.0.0.1:{port}'
    session = SimpleNamespace(id=SID, name='OWNED DIAGNOSTIC fixed large completion',
                              notification_settings={'browser': False, 'system': True})
    try:
        logging.basicConfig(filename=HERE / 'fixed-background.log', level=logging.INFO,
                            encoding='utf-8', force=True)
        summary['fixedDispatch'] = notifications.dispatch_completion(session, 'done', BODY)['system']
        async def background():
            payload = notifications.dispatch_completion_nonblocking(session, 'done', BODY)
            await asyncio.gather(*notifications._notification_tasks)
            return payload['system']
        summary['fixedBackgroundReceipt'] = asyncio.run(background())
        time.sleep(1)
        rows = owned_row(APP, TAG)
        summary['fixedRows'] = rows
        expected = notifications.completion_deep_link(SID)
        assert len(rows) == 1 and expected in rows[0][2] and '…' in rows[0][2]
        assert summary['fixedDispatch']['ok']
        summary['runtime'] = {'pid': os.getpid(), 'port': port, 'expectedLink': expected,
                              'nativeClickAccesses': accesses}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    with (HERE / 'fixed-combined.log').open('w', encoding='utf-8') as log:
        with DesktopCommand([sys.executable, str(Path(__file__).resolve()), '--child'], str(ROOT), log) as proc:
            summary['combinedExitcode'] = proc.wait(timeout=30)
            summary['combinedPid'] = proc.pid
            summary['combinedDesktop'] = proc.desktop.path
    summary['combinedResult'] = json.loads((HERE / 'fixed-combined.log').read_text(encoding='utf-8'))
    time.sleep(1)
    summary['combinedRows'] = owned_row('Pan.Owned.Toast20261017', 'owned-combined')
    assert summary['combinedResult']['ok'] and len(summary['combinedRows']) == 1
    with socket.socket() as check:
        summary['runtime']['portClosed'] = check.connect_ex(('127.0.0.1', port)) != 0
    assert summary['runtime']['portClosed']
    summary['unverified'] = ['banner pixels', 'notification center panel', 'native click',
                             'browser cold start', 'exact original failing notification']
    (HERE / 'verification.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                            encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k not in ('fixedRows', 'combinedRows')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    child() if '--child' in sys.argv else main()
