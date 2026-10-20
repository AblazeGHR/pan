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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fastapi import FastAPI
import httpx
import uvicorn
from packages.web import server as product
from packages.web.native_file_picker import native_file_picker
from packages.core.session import Session

OUTPUT = Path(__file__).parent
native_file_picker.timeout = 30
runtime = Path(tempfile.mkdtemp(prefix='pan-native-picker-'))
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
            responsive = httpx.get(base + '/identity', timeout=2).status_code
            automation = subprocess.run([
                str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
                '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', str(OUTPUT / 'real-dialog-select.ps1'),
                str(process.pid), str(fixture) if action == 'select' else 'CANCEL', str(OUTPUT / 'real-dialog.png'),
            ], capture_output=True, timeout=25)
            result = request.result().json()
            case = {'action': action, 'helperPid': process.pid, 'responsiveHTTP': responsive, 'automationExit': automation.returncode,
                    'automation': automation.stdout.decode('utf-8', errors='replace'), 'error': automation.stderr.decode('utf-8', errors='replace'), 'response': result}
            report['cases'].append(case)
            (OUTPUT / 'real-native-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            assert automation.returncode == 0, case
            assert result['status'] == ('selected' if action == 'select' else 'cancelled'), case
            if action == 'select':
                assert result['paths'] == [str(fixture)]
                assert not product.ATTACHMENTS_DIR.exists()
                confirmed = httpx.post(base + f'/api/sessions/{session.id}/attachments/from-server-file', json={'path': result['paths'][0]}).json()
                record = product._attachment_record(session.id, confirmed['attachmentId'])
                assert record['source'] == 'server_file' and record['path'] == str(fixture)
                assert fixture.read_text(encoding='utf-8') == 'owned fixture, unchanged'
                assert not any(p.name == fixture.name for p in product.ATTACHMENTS_DIR.rglob('*'))
                case['confirmation'] = confirmed
    (OUTPUT / 'real-native-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))
finally:
    native_file_picker.close()
    host.should_exit = True
    thread.join(10)
    assert not thread.is_alive()
