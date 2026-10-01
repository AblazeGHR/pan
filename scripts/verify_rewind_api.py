'''End-to-end HTTP and WebSocket verification for the cbc rewind API.'''

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import psutil
import websockets

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'evidence'
EVIDENCE.mkdir(exist_ok=True)


def snapshot(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        'path': str(path),
        'sha1': hashlib.sha1(data).hexdigest(),
        'lines': data.count(b'\n'),
        'size': len(data),
    }


def find_transcript(session_id: str) -> Path | None:
    root = Path.home() / '.codebuddy' / 'projects'
    hits = list(root.rglob(f'{session_id}.jsonl')) if root.is_dir() else []
    return hits[0] if hits else None


def free_port() -> int:
    for port in (8767, 8765):
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1', port)) != 0:
                return port
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def wait_health(client: httpx.Client, base_url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = client.get(base_url + '/api/health', timeout=1.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError('isolated Pan server did not become healthy')


def stop_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        root = psutil.Process(proc.pid)
        children = root.children(recursive=True)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            for child in children:
                child.kill()
            root.kill()
            proc.wait(timeout=5)
    except Exception:
        pass


def pan_file_snapshots(data_root: Path, session_id: str) -> dict[str, Any]:
    main = data_root / 'sessions' / f'{session_id}.json'
    history = data_root / 'sessions' / f'{session_id}.history.jsonl'
    return {
        'main': snapshot(main),
        'history': snapshot(history),
    }


async def drive_two_rewinds(base_url: str, session_id: str, message_id: str) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    async with websockets.connect(base_url.replace('http://', 'ws://') + '/ws', ping_interval=None) as ws:
        for attempt in (1, 2):
            async with httpx.AsyncClient(base_url=base_url, timeout=30.0, trust_env=False) as client:
                response = await client.post(
                    f'/api/sessions/{session_id}/history/{message_id}/rewind'
                )
            response.raise_for_status()
            accepted = response.json()
            job_id = accepted['jobId']
            events: list[dict[str, Any]] = []
            deadline = time.monotonic() + 120.0
            terminal = None
            while time.monotonic() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
                payload = json.loads(raw)
                if payload.get('type') != 'session.rewind.progress' or payload.get('jobId') != job_id:
                    continue
                events.append(payload)
                if payload.get('stage') in {'completed', 'failed'}:
                    terminal = payload
                    break
            if terminal is None:
                raise TimeoutError('rewind job did not reach a terminal stage')
            outcomes.append({'accepted': accepted, 'events': events, 'terminal': terminal})
    return {'attempts': outcomes}


def main() -> int:
    started = time.monotonic()
    temp_root = Path(tempfile.mkdtemp(prefix='pan-rewind-api-', dir=str(ROOT)))
    work = temp_root / 'work'
    data_root = temp_root / 'data'
    work.mkdir()
    data_root.mkdir()
    target = work / 'rewind_api_probe.txt'
    sid_a = f'pan_rewind_api_{int(time.time())}'
    marker = 'PAN_REWIND_API_MARKER'
    prompt = (
        f'Use your native Write or Edit file tool, never Bash or shell, to create exactly one file '
        f'at {target} with exactly this content: {marker}. Then confirm the file was created.'
    )
    evidence: dict[str, Any] = {
        'workdir': str(work),
        'data_root': str(data_root),
        'session_a': sid_a,
        'target': str(target),
        'marker': marker,
    }
    server_proc: subprocess.Popen | None = None
    log_handle = None
    try:
        cbc = shutil.which('cbc')
        if not cbc:
            evidence['status'] = 'blocked'
            evidence['error'] = 'cbc unavailable'
            return 2
        create = subprocess.run(
            [cbc, '-p', '--session-id', sid_a, '--permission-mode', 'bypassPermissions',
             '--output-format', 'json', prompt],
            cwd=str(work), capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=180, check=False,
        )
        transcript_a = find_transcript(sid_a)
        evidence['create'] = {'exit_code': create.returncode}
        evidence['file_after_create'] = (
            target.read_text(encoding='utf-8', errors='replace') if target.exists() else None
        )
        evidence['transcript_a'] = str(transcript_a) if transcript_a else None
        if create.returncode != 0 or not target.exists() or not transcript_a:
            evidence['status'] = 'prepare_failed'
            return 1
        evidence['transcript_a_before'] = snapshot(transcript_a)
        project_dir = transcript_a.parent

        port = free_port()
        base_url = f'http://127.0.0.1:{port}'
        log_path = EVIDENCE / 'rewind_api_server.log'
        log_handle = log_path.open('wb')
        env = dict(os.environ)
        env['PAN_BACKGROUND_JOBS_DIR'] = str(data_root / 'background_jobs')
        env['PAN_SCHEDULER_DIR'] = str(data_root / 'background_jobs')
        env['PAN_WECHAT_DATA_DIR'] = str(data_root)
        env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', '')
        server_proc = subprocess.Popen(
            [sys.executable, 'tests/support/isolated_http_server.py',
             '--port', str(port), '--data-root', str(data_root)],
            cwd=str(ROOT), stdout=log_handle, stderr=subprocess.STDOUT,
            env=env,
        )
        with httpx.Client(base_url=base_url, timeout=30.0, trust_env=False) as client:
            wait_health(client, base_url)
            imported = client.post('/api/adapters/cbc/sessions/import', json={
                'session_id': sid_a,
                'cwd': str(work),
                'name': 'rewind-api-parent',
            }).json()
            evidence['import'] = imported
            pan_parent_id = imported.get('id')
            if not pan_parent_id:
                evidence['status'] = 'import_failed'
                return 1
            history_response = client.get(f'/api/sessions/{pan_parent_id}/history?limit=100').json()
            anchor_item = next(
                item for item in history_response.get('history', [])
                if item.get('role') == 'user' and marker in str(item.get('content'))
            )
            message_id = anchor_item['messageId']
            evidence['pan_parent_id'] = pan_parent_id
            evidence['message_id'] = message_id
            evidence['parent_history_before'] = history_response
            evidence['parent_files_before'] = pan_file_snapshots(data_root, pan_parent_id)
            evidence['parent_usage_before'] = client.get(f'/api/sessions/{pan_parent_id}/usage').json()

        drive = asyncio.run(drive_two_rewinds(base_url, pan_parent_id, message_id))
        evidence['drive'] = drive
        records_root = data_root / 'rewind' / pan_parent_id
        evidence['rewind_records'] = [
            json.loads(path.read_text(encoding='utf-8'))
            for path in sorted(records_root.glob('*.json'))
        ] if records_root.exists() else []

        with httpx.Client(base_url=base_url, timeout=30.0, trust_env=False) as client:
            first_id = drive['attempts'][0]['terminal'].get('newSessionId')
            second_id = drive['attempts'][1]['terminal'].get('newSessionId')
            first = client.get(f'/api/sessions/{first_id}').json()
            second = client.get(f'/api/sessions/{second_id}').json()
            first_history = client.get(f'/api/sessions/{first_id}/history?limit=100').json()
            second_history = client.get(f'/api/sessions/{second_id}/history?limit=100').json()
            parent_after = client.get(f'/api/sessions/{pan_parent_id}/history?limit=100').json()
            parent_usage_after = client.get(f'/api/sessions/{pan_parent_id}/usage').json()
            first_usage = client.get(f'/api/sessions/{first_id}/usage').json()

        evidence['first_session'] = {'id': first.get('id'), 'name': first.get('name'), 'cliSessionId': first.get('cliSessionId')}
        evidence['second_session'] = {'id': second.get('id'), 'name': second.get('name'), 'cliSessionId': second.get('cliSessionId')}
        evidence['first_history'] = first_history
        evidence['second_history'] = second_history
        evidence['parent_history_after'] = parent_after
        evidence['parent_usage_after'] = parent_usage_after
        evidence['first_usage'] = first_usage
        evidence['parent_files_after'] = pan_file_snapshots(data_root, pan_parent_id)
        evidence['transcript_a_after'] = snapshot(transcript_a)
        evidence['file_after_rewinds'] = target.exists()

        first_rows = first_history.get('history', [])
        second_rows = second_history.get('history', [])
        evidence['assertions'] = {
            'file_rolled_back': not target.exists(),
            'new_sessions_created': bool(first_id and second_id and first_id != second_id),
            'cli_ids_are_new': first.get('cliSessionId') not in {None, sid_a} and second.get('cliSessionId') not in {None, sid_a},
            # Exclusive-anchor semantics: the anchor is the only user message,
            # so both new sessions start with an empty history.
            'histories_exclude_anchor': bool(
                not first_rows and not second_rows
                and marker not in json.dumps(first_history, ensure_ascii=False)
                and marker not in json.dumps(second_history, ensure_ascii=False)
            ),
            'usage_recalculated': first_usage != evidence['parent_usage_before'],
            'parent_history_unchanged': parent_after == evidence['parent_history_before'],
            'parent_files_unchanged': evidence['parent_files_after'] == evidence['parent_files_before'],
            'names_deduplicated': first.get('name') != second.get('name'),
            'original_cbc_transcript_unchanged': evidence['transcript_a_after'] == evidence['transcript_a_before'],
        }
        evidence['status'] = 'success' if all(evidence['assertions'].values()) else 'failed'
        return 0 if evidence['status'] == 'success' else 1
    except Exception as exc:
        evidence['status'] = 'exception'
        evidence['error'] = f'{type(exc).__name__}: {exc}'
        return 1
    finally:
        if server_proc is not None:
            stop_process_tree(server_proc)
        if log_handle is not None:
            log_handle.close()
        evidence['elapsed_seconds'] = round(time.monotonic() - started, 3)
        (EVIDENCE / 'rewind_api_e2e.json').write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8',
        )
        transcript = find_transcript(sid_a)
        if transcript and 'pan-rewind-api-' in transcript.parent.name:
            shutil.rmtree(transcript.parent, ignore_errors=True)
        shutil.rmtree(temp_root, ignore_errors=True)
        print('rewind api e2e status={} elapsed={}'.format(
            evidence.get('status'), evidence.get('elapsed_seconds'),
        ))


if __name__ == '__main__':
    raise SystemExit(main())
