"""Real TCP HTTP + production queue + an owned fake CLI, no real profile."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen


def test_live_existing_running_cli_receives_brief_once(tmp_path):
    runtime = tmp_path / 'owned-handoff-http'
    runtime.mkdir()
    launcher = Path(__file__).parent / 'support' / 'handoff_existing_server.py'
    env = {k: v for k, v in os.environ.items() if not k.startswith('PAN_')}
    log = (runtime / 'server.log').open('w', encoding='utf-8')
    proc = subprocess.Popen([sys.executable, str(launcher), str(runtime)], env=env,
        stdout=log, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    identity = None
    def wait_for(check):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert proc.poll() is None, (runtime / 'server.log').read_text(encoding='utf-8')
            try:
                result = check()
                if result: return result
            except (ConnectionError, OSError, json.JSONDecodeError):
                pass
            time.sleep(.05)
        raise AssertionError((runtime / 'server.log').read_text(encoding='utf-8'))
    def call(method, path, data=None):
        request = Request(f"http://127.0.0.1:{identity['port']}" + path,
            method=method, data=json.dumps(data).encode() if data is not None else None,
            headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=5) as response:
            return json.load(response)
    try:
        wait_for(lambda: (runtime / 'identity.json').exists())
        identity = json.loads((runtime / 'identity.json').read_text(encoding='utf-8'))
        assert identity['pid'] == proc.pid and identity['port'] not in (8767, 8768)
        assert Path(identity['runtime']) == runtime
        wait_for(lambda: call('GET', '/__handoff/identity') == identity)
        a, b = identity['source'], identity['target']
        spawned = call('POST', '/api/spawn', {'sessionId': b})
        assert 'error' not in spawned, spawned
        initial = call('POST', '/api/send', {'sessionId': b, 'text': 'hold-turn', 'source': 'user'})
        assert 'error' not in initial, initial
        wait_for(lambda: (runtime / 'running').exists())
        call('PATCH', f'/api/sessions/{b}/queue/paused', {'paused': True})
        before = call('GET', f'/api/sessions/{b}')
        body = {'targetSessionId': b, 'handoffPrompt': 'unique-handoff-brief'}
        result = call('POST', f'/api/sessions/{a}/handoff', body)
        assert result['ok'], result
        after = call('GET', f'/api/sessions/{b}')
        assert after['systemPrompt'] == before['systemPrompt'] == identity['effectivePrompt']
        assert after['originalPrompt'] == 'A metadata only'
        assert after['cliSessionId'] == before['cliSessionId']
        assert after['workerId'] == before['workerId']
        item_id = result['briefDelivery']['queueItemId']
        repeat = call('POST', f'/api/sessions/{a}/handoff', body)
        assert repeat['briefDelivery']['queueItemId'] == item_id
        audit = (runtime / 'cli-audit.jsonl').read_text(encoding='utf-8')
        assert 'unique-handoff-brief' not in audit
        (runtime / 'release').write_text('release', encoding='utf-8')
        call('PATCH', f'/api/sessions/{b}/queue/paused', {'paused': False})
        wait_for(lambda: 'unique-handoff-brief' in (runtime / 'cli-audit.jsonl').read_text(encoding='utf-8'))
        final = wait_for(lambda: (value if (value := call('GET', f'/api/sessions/{b}')).get('workerStatus') == 'idle' else None))
        rows = [json.loads(row) for row in (runtime / 'cli-audit.jsonl').read_text(encoding='utf-8').splitlines()]
        assert sum('unique-handoff-brief' in row.get('text', '') for row in rows) == 1
        assert final['systemPrompt'] == identity['effectivePrompt']
        assert any(row.get('content') == 'existing B context' for row in final['history'])
        # Reconciliation after actual provider handoff still does not resend.
        assert call('POST', f'/api/sessions/{a}/handoff', body)['briefDelivery']['queueItemId'] == item_id
        rows = [json.loads(row) for row in (runtime / 'cli-audit.jsonl').read_text(encoding='utf-8').splitlines()]
        assert sum('unique-handoff-brief' in row.get('text', '') for row in rows) == 1
        resumed = call('POST', '/api/spawn', {'sessionId': b})
        assert 'error' not in resumed, resumed
        coldspawn = call('POST', '/__handoff/coldspawn', {})
        assert coldspawn['systemPrompt'] == identity['effectivePrompt']
        def got_coldspawn():
            entries = [json.loads(row) for row in (runtime / 'cli-audit.jsonl').read_text(encoding='utf-8').splitlines()]
            return next((row for row in entries if '--system-prompt' in row.get('argv', [])), None)
        argv = wait_for(got_coldspawn)['argv']
        assert argv[argv.index('--system-prompt') + 1] == identity['effectivePrompt']
        assert 'A metadata only' not in str(argv) and 'unique-handoff-brief' not in str(argv)
        (runtime / 'verified.json').write_text(json.dumps({'identity': identity, 'queueItemId': item_id,
            'workerUnchanged': True, 'briefWrites': 1, 'systemPromptUnchanged': True}), encoding='utf-8')
    finally:
        if proc.poll() is None and identity is not None:
            try: call('POST', '/__handoff/shutdown', {})
            except OSError: pass
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=5)
        log.close()
        if identity is not None:
            with socket.socket() as check:
                assert check.connect_ex(('127.0.0.1', identity['port'])) != 0
