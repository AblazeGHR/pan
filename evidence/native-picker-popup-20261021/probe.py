"""Owned isolated HTTP route host + actual OS dialog, no shared services."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

for key in list(os.environ):
    if key.upper().startswith('PAN_'):
        del os.environ[key]

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
runtime = Path(tempfile.mkdtemp(prefix='pan-native-popup-'))
from packages.core import config
config.CONFIG_FILE = runtime / 'config.json'
assert config.CONFIG_FILE.is_relative_to(runtime)
from fastapi import FastAPI
import httpx
import uvicorn
from packages.web import server as product
from packages.web.native_file_picker import native_file_picker
from packages.core.session import Session
product.sess.SESSION_DIR = runtime / 'sessions'

OUTPUT = Path(__file__).parent / sys.argv[1]
OUTPUT.mkdir(exist_ok=True)
native_file_picker.timeout = 30
# Observe only our product helper's stderr, without changing argv or behavior.
original_popen = subprocess.Popen
def observe_popen(argv, **kwargs):
    process = original_popen(argv, **kwargs)
    if argv[-1].endswith('native_file_dialog.ps1'):
        original_communicate = process.communicate
        def communicate(*args, **options):
            result = original_communicate(*args, **options)
            if result[1]:
                process.focus_diagnostic = result[1].decode('utf-8', errors='replace')
            return result
        process.communicate = communicate
    return process
subprocess.Popen = observe_popen
# Own temporary profile allocated before product imports.
session = Session(id='ses_native_probe', name='native probe', workdir=str(runtime))
product.sess.get = lambda sid: session if sid == session.id else None
product.ATTACHMENTS_DIR = runtime / 'attachments'
fixture = runtime / 'native-附件.txt'
fixture.write_text('owned fixture, unchanged', encoding='utf-8')
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
assert port not in (8767, 8768)
app = FastAPI()
app.post('/api/sessions/{session_id}/attachments/native-picker')(product.pick_native_server_files)
app.post('/api/sessions/{session_id}/attachments/from-server-file')(product.register_server_file_attachment)
@app.get('/identity')
async def identity():
    return {'pid': os.getpid(), 'checkout': str(ROOT), 'port': port, 'profile': str(runtime)}
host = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
thread = threading.Thread(target=host.run)
thread.start()
base = f'http://127.0.0.1:{port}'
try:
    for _ in range(100):
        if host.started:
            break
        time.sleep(.05)
    report = {'identity': httpx.get(base + '/identity').json(), 'cases': []}
    for action in ('select', 'cancel'):
        with ThreadPoolExecutor() as executor:
            request = executor.submit(httpx.post, base + f'/api/sessions/{session.id}/attachments/native-picker', json={'directory': str(runtime)}, timeout=100)
            for _ in range(100):
                process = native_file_picker._process
                if process is not None or request.done():
                    break
                time.sleep(.05)
            assert process is not None, request.result().text if request.done() else 'helper not created'
            assert httpx.post(base + f'/api/sessions/{session.id}/attachments/native-picker', json={}, timeout=2).json()['status'] == 'busy'
            responsive = httpx.get(base + '/identity', timeout=2).status_code
            automation = subprocess.run([
                str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
                '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', str(Path(__file__).with_name('real-dialog-select.ps1')),
                str(process.pid), str(fixture) if action == 'select' else 'CANCEL', str(OUTPUT / 'real-dialog.png'),
            ], capture_output=True, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
            result = request.result().json()
            case = {'action': action, 'helperPid': process.pid, 'responsiveHTTP': responsive, 'concurrentStatus': 'busy', 'automationExit': automation.returncode,
                    'automation': automation.stdout.decode('utf-8', errors='replace'), 'error': automation.stderr.decode('utf-8', errors='replace'), 'response': result}
            report['cases'].append(case)
            (OUTPUT / 'real-native-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            assert automation.returncode == 0, case
            assert result['status'] == ('selected' if action == 'select' else 'cancelled'), case
            window = json.loads(case['automation'])
            if sys.argv[1] != 'baseline':
                assert not window['topmost'], window
                assert all(not item['topmost'] for item in window['windows'] if item['visible'] and (item['class'] == '#32770' or item['hwnd'] == window['owner'])), window
                case['uncoveredAtCenter'] = window['centerTopWindow'] == window['hwnd']
                case['focused'] = window['foreground'] == window['hwnd'] and window['keyboardFocusRoot'] == window['hwnd']
                assert any(item['hwnd'] == window['owner'] and item['visible'] for item in window['windows']), window
                assert not any(item['class'] == 'ConsoleWindowClass' and item['visible'] for item in window['windows']), window
            case['focusDiagnostic'] = getattr(process, 'focus_diagnostic', '')
            case['helperExited'] = process.poll() is not None
            assert case['helperExited']
            if action == 'select':
                assert result['paths'] == [str(fixture)]
                assert not product.ATTACHMENTS_DIR.exists()
                confirmed = httpx.post(base + f'/api/sessions/{session.id}/attachments/from-server-file', json={'path': result['paths'][0]}).json()
                record = product._attachment_record(session.id, confirmed['attachmentId'])
                assert record['source'] == 'server_file' and record['path'] == str(fixture)
                assert fixture.read_text(encoding='utf-8') == 'owned fixture, unchanged'
                assert not any(p.name == fixture.name for p in product.ATTACHMENTS_DIR.rglob('*'))
                case['confirmation'] = confirmed
    invalid = httpx.post(base + f'/api/sessions/{session.id}/attachments/native-picker', json={'directory': str(runtime / 'missing')}, timeout=15).json()
    assert invalid['status'] == 'failed', invalid
    native_file_picker.timeout = 6
    with ThreadPoolExecutor() as executor:
        waiting = executor.submit(httpx.post, base + f'/api/sessions/{session.id}/attachments/native-picker', json={}, timeout=15)
        for _ in range(100):
            timed_process = native_file_picker._process
            if timed_process is not None or waiting.done():
                break
            time.sleep(.05)
        assert timed_process is not None, waiting.result().text if waiting.done() else 'helper start timed out'
        assert httpx.get(base + '/identity', timeout=2).status_code == 200
        timed = waiting.result().json()
        assert timed['status'] == 'timeout' and timed_process.poll() is not None
    report['failureCases'] = {'invalidDirectory': invalid, 'timeout': timed, 'timeoutHelperExited': True, 'responsiveHTTP': 200}
    (OUTPUT / 'real-native-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))
finally:
    native_file_picker.close()
    host.should_exit = True
    thread.join(10)
    assert not thread.is_alive()
    with socket.socket() as check:
        assert check.connect_ex(('127.0.0.1', port)) != 0
    report['cleanup'] = {'hostThreadStopped': True, 'portClosed': True, 'helperOwned': native_file_picker._process is not None}
    assert not report['cleanup']['helperOwned']
    (OUTPUT / 'real-native-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
