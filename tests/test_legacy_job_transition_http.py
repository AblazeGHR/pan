"""Real HTTP + production Chromium + owned descendant cancellation, ephemeral port."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import psutil
import httpx

from tests import test_real_queue_http_e2e as h
from tests.test_automation_core import action
from packages.jobs.window import command_window_options


def test_dual_job_tabs_and_old_lifecycle_over_real_http(tmp_path, monkeypatch):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    assert port not in (8767, 8768)
    monkeypatch.setattr(h, 'PORT', port)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', Path(__file__).parent / 'support/legacy_job_http_server.py')
    rt = h._PanRuntime(tmp_path / 'data')
    rt.data_root.mkdir()
    (rt.data_root / 'config.json').write_text(json.dumps({'plugin_manifests': [], 'scheduler': {'enabled': True}}))
    out = Path('audit/legacy-job-latest').resolve()
    out.mkdir(parents=True, exist_ok=True)
    owned = {}
    try:
        rt.start()
        session = rt.request('POST', '/api/sessions', json={'name': 'Legacy-target', 'adapter': 'codex', 'mcpServers': []})
        assert 'id' in session, session
        sid = session['id']
        marker = tmp_path / 'child.json'
        child_code = 'import time; time.sleep(120)'
        parent_code = (f'import subprocess,sys,time,json,pathlib; p=subprocess.Popen([sys.executable,"-c",{child_code!r}]); '
                       f'pathlib.Path({str(marker)!r}).write_text(json.dumps({{"pid":p.pid}})); time.sleep(120)')
        old = rt.request('POST', '/api/background-jobs', json={'targetSessionId': sid, 'name': 'Legacy tree boundary',
                         'argv': [sys.executable, '-c', parent_code], 'cwd': str(tmp_path)})
        jid = old['jobId']
        def running():
            job = rt.request('GET', '/api/background-jobs/' + jid)
            return job if job.get('status') == 'running' and marker.exists() else None
        live = h._wait_for(running, timeout=20, label='owned background tree ready')
        for pid in [live['pid'], live['runnerPid'], json.loads(marker.read_text())['pid']]:
            owned[pid] = psutil.Process(pid).create_time()
        timer = rt.request('POST', '/api/session-message-jobs', json={'targetSessionId': sid, 'text': 'future message',
                           'schedule': {'type': 'once', 'delaySeconds': 3600}, 'description': 'Original timed message delivery'})
        # Raw message API has no name input; use the existing old metadata API.
        rt.request('PATCH', '/api/jobs/' + timer['jobId'], json={'name': 'Legacy timer boundary'})
        unknown_path = rt.data_root / 'background_jobs/jobs/job_unknown_fixture.json'
        unknown_path.write_text(json.dumps({'jobId': 'job_unknown_fixture', 'name': 'Legacy unknown boundary',
            'kind': 'background-process', 'status': 'unknown', 'targetSessionId': sid, 'argv': ['unknown'],
            'cwd': str(tmp_path), 'createdAt': time.time(), 'updatedAt': time.time()}))
        assert rt.request('DELETE', '/api/jobs/job_unknown_fixture')['error']['code'] == 'job_busy'
        assert rt.request('GET', '/api/background-jobs/job_unknown_fixture')['status'] == 'cleanup_pending'
        assert rt.request('POST', '/api/background-jobs/job_unknown_fixture/cancel')['error']['code'] == 'cancel_unsafe'
        assert unknown_path.exists()
        key = (rt.data_root / 'automation/user.key').read_text().strip()
        auth = {'Authorization': 'Bearer ' + rt.request('POST', '/api/automation/login', json={'key': key})['token']}
        rt.request('POST', '/api/actions/actions', json={'item': action([sid])}, headers=auth)
        fresh = rt.request('POST', '/api/jobs/action-jobs', json={'config': {'name': 'New Action boundary', 'actionId': 'pause',
                           'arguments': {}, 'schedule': [], 'trigger': {'kind': 'immediate'}, 'enabled': False}}, headers=auth)['job']
        assert fresh.get('jobId'), fresh
        assert not any(j['jobId'] == fresh['jobId'] for j in rt.request('GET', '/api/jobs')['jobs'])
        assert any(j['jobId'] == fresh['jobId'] for j in rt.request('GET', '/api/jobs', headers=auth)['jobs'])
        with httpx.Client(base_url=rt.base_url, trust_env=False) as client:
            assert client.get('/api/jobs/' + fresh['jobId']).status_code == 401
        # Another live tree must be deleted through the latest product path in GUI.
        delete_marker = tmp_path / 'delete-child.json'
        delete_code = parent_code.replace(repr(str(marker)), repr(str(delete_marker)))
        deleting = rt.request('POST', '/api/background-jobs', json={'targetSessionId': sid, 'name': 'Legacy safe delete boundary',
            'argv': [sys.executable, '-c', delete_code], 'cwd': str(tmp_path)})
        delete_id = deleting['jobId']
        delete_live = h._wait_for(lambda: (j if j.get('status') == 'running' and j.get('processJobHandle') and delete_marker.exists() else None)
            if (j := rt.request('GET', '/api/background-jobs/' + delete_id)) else None, timeout=20, label='owned Job Object ready')
        delete_child_pid = json.loads(delete_marker.read_text())['pid']
        for pid in [delete_live['pid'], delete_live['runnerPid'], delete_child_pid]:
            proc = psutil.Process(pid)
            for member in [proc, *proc.children(recursive=True)]: owned[member.pid] = member.create_time()
        input_file = out / 'input.json'
        input_file.write_text(json.dumps({'processId': jid, 'newId': fresh['jobId'], 'deleteId': delete_id, 'sessionId': sid}))
        result = subprocess.run(['node', 'e2e/legacy-job-transition.mjs', rt.base_url, str(input_file), str(out), str(tmp_path / 'chromium-profile')],
                                cwd=h.ROOT / 'packages/web', capture_output=True, text=True, encoding='utf-8', timeout=90,
                                env={**os.environ, 'PAN_TEST_JOB_KEY': key}, **command_window_options())
        assert result.returncode == 0, result.stdout + result.stderr
        assert rt.request('GET', '/api/background-jobs/' + delete_id)['error']['code'] == 'job_not_found'
        def gone():
            for pid, birth in owned.items():
                try:
                    if psutil.Process(pid).create_time() == birth:
                        return False
                except psutil.NoSuchProcess:
                    pass
            return True
        h._wait_for(gone, timeout=10, label='original runner, process and descendant retired')
        assert rt.request('DELETE', '/api/jobs/' + jid)['deleted']
        assert rt.request('GET', '/api/background-jobs/' + jid)['error']['code'] == 'job_not_found'
        # Retry creates a different old Job and preserves retryOf; no Attempt conversion.
        retry = h._wait_for(lambda: next((j for j in rt.request('GET', '/api/background-jobs')['jobs'] if j.get('retryOf') == jid), None), label='legacy retry record')
        retry_live = h._wait_for(lambda: (j if j.get('status') == 'running' else None) if (j := rt.request('GET', '/api/background-jobs/' + retry['jobId'])) else None, timeout=20, label='retry running')
        for pid in [retry_live['pid'], retry_live['runnerPid']]:
            process = psutil.Process(pid)
            for member in [process, *process.children(recursive=True)]:
                owned[member.pid] = member.create_time()
        assert rt.request('POST', '/api/background-jobs/' + retry['jobId'] + '/cancel')['status'] == 'cancelled'
        rt.request('POST', '/api/background-jobs/' + timer['jobId'] + '/cancel')
        assert rt.request('DELETE', '/api/jobs/' + timer['jobId'])['deleted']
        created = next(j for j in rt.request('GET', '/api/jobs')['jobs'] if j['name'] == 'GUI legacy created')
        assert created['kind'] == 'session-message' and created['unified']['compatibility']
        rt.request('POST', '/api/background-jobs/' + created['jobId'] + '/cancel')
        assert rt.request('DELETE', '/api/jobs/' + created['jobId'])['deleted']
        assert rt.request('GET', '/api/jobs/' + fresh['jobId'], headers=auth)['job']['configRevision'] == 1
        (out / 'http.json').write_text(json.dumps({'ok': True, 'port': port, 'profile': str(tmp_path), 'oldJob': jid,
            'newJob': fresh['jobId'], 'deletedLiveJob': delete_id, 'deletedLiveChildPid': delete_child_pid, 'guiCreatedJob': created['jobId'], 'retryJob': retry['jobId'], 'ownedProcessIdentities': owned,
            'sourceSha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}, indent=2))
    finally:
        # Only identity-matched processes created by this test may be retired.
        for pid, birth in owned.items():
            try:
                proc = psutil.Process(pid)
                if proc.create_time() == birth:
                    proc.kill()
            except psutil.NoSuchProcess:
                pass
        rt.stop()
