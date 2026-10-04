'''Verify whether cbc persists a native-rewind fork on graceful PTY exit.'''

from __future__ import annotations

import argparse
import hashlib
import json
import queue
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'evidence'
EVIDENCE.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))

from packages.core.rewind.driver import _PtySession, fork_session, navigate_to_anchor


def snapshot(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        'path': str(path),
        'sha1': hashlib.sha1(data).hexdigest(),
        'lines': data.count(b'\n'),
        'size': len(data),
    }


def project_files(project_dir: Path) -> dict[str, dict[str, Any]]:
    if not project_dir.exists():
        return {}
    return {path.name: snapshot(path) for path in sorted(project_dir.glob('*.jsonl'))}


def find_transcript(session_id: str) -> Path | None:
    root = Path.home() / '.codebuddy' / 'projects'
    hits = list(root.rglob(f'{session_id}.jsonl')) if root.is_dir() else []
    return hits[0] if hits else None


def transcript_summary(path: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    users: list[str] = []
    for record in records:
        if record.get('type') != 'message' or record.get('role') != 'user':
            continue
        texts: list[str] = []
        for item in record.get('content') or []:
            if isinstance(item, dict) and isinstance(item.get('text'), str):
                texts.append(item['text'])
        if texts:
            users.append(' '.join(texts))
    tail = []
    for record in records[-5:]:
        tail.append({
            'type': record.get('type'),
            'role': record.get('role'),
            'name': record.get('name'),
            'id': record.get('id'),
            'sessionId': record.get('sessionId') or record.get('session_id'),
        })
    return {
        'records': len(records),
        'user_messages': len(users),
        'last_user_text': users[-1] if users else None,
        'tail': tail,
    }


def write_evidence(method: str, evidence: dict[str, Any]) -> None:
    (EVIDENCE / f'rewind_persist_{method}.json').write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8',
    )


def append_screen(path: Path, title: str, screen: str) -> None:
    with path.open('a', encoding='utf-8', errors='replace') as handle:
        handle.write(chr(10) + '=' * 20 + ' ' + title + ' ' + '=' * 20 + chr(10))
        handle.write(screen)
        handle.write(chr(10))


def wait_exit(session: _PtySession, timeout: float = 60.0) -> tuple[bool, float]:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        while True:
            try:
                chunk = session.queue.get_nowait()
            except queue.Empty:
                break
            if chunk:
                session.stream.feed(chunk)
        if not session.proc.isalive():
            return True, round(time.monotonic() - started, 3)
        time.sleep(0.1)
    return False, round(time.monotonic() - started, 3)


def send_slash_command(session: _PtySession, command: str) -> None:
    for char in command:
        session.send(char)
        time.sleep(0.03)
    session.send(chr(13))


def attempt_exit(session: _PtySession, method: str) -> str:
    session.send(chr(127) * 600)
    time.sleep(0.5)
    if method == 'exit':
        send_slash_command(session, '/exit')
        return 'Backspace x600, typed /exit, then Enter'
    if method == 'quit':
        send_slash_command(session, '/quit')
        return 'Backspace x600, typed /quit, then Enter'
    if method == 'ctrl-d':
        session.proc.sendcontrol('d')
        return 'Backspace x600, then Ctrl+D'
    if method == 'eof':
        session.proc.sendeof()
        return 'Backspace x600, then PTY EOF'
    raise ValueError(f'unknown method: {method}')


def run_probe(method: str) -> dict[str, Any]:
    started = time.monotonic()
    method_token = method.replace('-', '_')
    work = Path(tempfile.mkdtemp(prefix=f'pan-rewind-persist-{method}-', dir=str(ROOT)))
    target = work / 'rewind_persist_probe.txt'
    sid_a = f'pan_rewind_persist_{method_token}_{int(time.time())}'
    marker = f'PAN_REWIND_PERSIST_{method_token.upper()}'
    prompt = (
        f'Use your native Write or Edit file tool, never Bash or shell, to create exactly one file '
        f'at {target} with exactly this content: {marker}. Then confirm the file was created.'
    )
    evidence: dict[str, Any] = {
        'method': method,
        'workdir': str(work),
        'session_a': sid_a,
        'target': str(target),
        'marker': marker,
    }
    screen_path = EVIDENCE / f'rewind_persist_{method}_screen.txt'
    screen_path.write_text('', encoding='utf-8')
    session: _PtySession | None = None
    try:
        cbc = shutil.which('cbc')
        if not cbc:
            evidence['status'] = 'blocked'
            evidence['error'] = 'cbc unavailable'
            return evidence
        create_argv = [
            cbc, '-p', '--session-id', sid_a, '--permission-mode', 'bypassPermissions',
            '--output-format', 'json', prompt,
        ]
        create = subprocess.run(
            create_argv, cwd=str(work), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=180, check=False,
        )
        evidence['create'] = {
            'exit_code': create.returncode,
            'stdout_tail': create.stdout[-2000:],
            'stderr_tail': create.stderr[-1000:],
        }
        transcript_a = find_transcript(sid_a)
        evidence['transcript_a'] = str(transcript_a) if transcript_a else None
        evidence['file_after_create'] = (
            target.read_text(encoding='utf-8', errors='replace') if target.exists() else None
        )
        if create.returncode != 0 or not target.exists() or not transcript_a:
            evidence['status'] = 'prepare_failed'
            return evidence
        project_dir = transcript_a.parent
        evidence['project_dir'] = str(project_dir)
        evidence['files_before'] = project_files(project_dir)
        evidence['transcript_a_before'] = snapshot(transcript_a)

        fork = fork_session(sid_a, work, timeout=180.0)
        evidence['fork'] = fork.__dict__
        if fork.error or not fork.new_session_id:
            evidence['status'] = 'fork_failed'
            return evidence
        transcript_b = Path(fork.transcript_path) if fork.transcript_path else find_transcript(fork.new_session_id)
        if not transcript_b:
            evidence['status'] = 'fork_transcript_missing'
            return evidence
        evidence['session_b'] = fork.new_session_id
        evidence['transcript_b'] = str(transcript_b)
        evidence['transcript_b_before'] = snapshot(transcript_b)
        evidence['files_after_fork'] = project_files(project_dir)

        session = _PtySession(
            [cbc, '-r', fork.new_session_id, '--permission-mode', 'bypassPermissions'],
            work,
        )
        ready, screen = session.wait_for(
            lambda value: 'CodeBuddy Code' in value and (chr(10) + '>' in value or value.rstrip().endswith('>')),
            time.monotonic() + 35.0,
        )
        evidence['tui_ready'] = ready
        append_screen(screen_path, 'ready', screen)
        if not ready:
            evidence['status'] = 'tui_ready_timeout'
            return evidence
        session.send(chr(27))
        time.sleep(0.2)
        session.send(chr(27))
        menu_ok, screen = session.wait_for(
            lambda value: 'Restore and fork the conversation' in value,
            time.monotonic() + 20.0,
        )
        evidence['rewind_menu'] = menu_ok
        append_screen(screen_path, 'rewind-menu', screen)
        if not menu_ok:
            evidence['status'] = 'rewind_menu_timeout'
            return evidence
        steps, screen = navigate_to_anchor(session, marker, timeout=10.0)
        evidence['navigation_steps'] = steps
        append_screen(screen_path, 'anchor-selected', screen)
        session.send(chr(13))
        confirm_ok, screen = session.wait_for(
            lambda value: 'Restore code and conversation' in value and 'Never Mind' in value,
            time.monotonic() + 20.0,
        )
        evidence['confirm_page'] = confirm_ok
        append_screen(screen_path, 'confirm', screen)
        if not confirm_ok:
            evidence['status'] = 'confirm_timeout'
            return evidence
        session.send(chr(13))
        restore_deadline = time.monotonic() + 20.0
        restored = False
        screen = session.text()
        while time.monotonic() < restore_deadline:
            if not target.exists() and 'Restore and fork the conversation' not in session.text():
                restored = True
                screen = session.text()
                break
            session.wait_for(lambda value: True, min(restore_deadline, time.monotonic() + 0.05))
        evidence['restored_in_memory'] = restored
        evidence['file_after_rewind'] = (
            target.read_text(encoding='utf-8', errors='replace') if target.exists() else None
        )
        append_screen(screen_path, 'after-restore', screen)
        if not restored:
            evidence['status'] = 'restore_failed'
            return evidence

        evidence['exit_action'] = attempt_exit(session, method)
        natural, wait_seconds = wait_exit(session, timeout=60.0)
        evidence['natural_exit'] = natural
        evidence['exit_wait_seconds'] = wait_seconds
        evidence['exit_status'] = getattr(session.proc, 'exitstatus', None)
        evidence['screen_after_exit'] = session.text()
        append_screen(screen_path, 'after-exit-attempt', session.text())

        files_after = project_files(project_dir)
        evidence['files_after_exit'] = files_after
        evidence['transcript_a_after'] = snapshot(transcript_a) if transcript_a.exists() else None
        evidence['transcript_b_after'] = snapshot(transcript_b) if transcript_b.exists() else None
        evidence['new_session_files'] = [
            value for name, value in files_after.items() if name not in evidence['files_after_fork']
        ]
        evidence['transcript_b_summary_after'] = (
            transcript_summary(transcript_b) if transcript_b.exists() else None
        )
        evidence['new_session_summaries'] = [
            transcript_summary(Path(item['path'])) for item in evidence['new_session_files']
        ]
        evidence['status'] = 'success' if natural else 'exit_timeout'
        return evidence
    except Exception as exc:
        evidence['status'] = 'exception'
        evidence['error'] = f'{type(exc).__name__}: {exc}'
        return evidence
    finally:
        if session is not None and session.proc.isalive():
            evidence['cleanup'] = session.close()
        elif session is not None:
            try:
                session.proc.close()
                evidence['cleanup'] = {'pid': getattr(session.proc, 'pid', None), 'terminate': 'already exited'}
            except Exception as exc:
                evidence['cleanup'] = {'error': repr(exc)}
        evidence['elapsed_seconds'] = round(time.monotonic() - started, 3)
        write_evidence(method, evidence)
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--method', choices=('exit', 'ctrl-d', 'quit', 'eof'), required=True)
    args = parser.parse_args()
    result = run_probe(args.method)
    status = result.get('status')
    natural = result.get('natural_exit')
    new_count = len(result.get('new_session_files', []))
    print('rewind persist method={} status={} natural={} new_files={}'.format(
        args.method, status, natural, new_count,
    ))
    return 0 if status == 'success' else 1


if __name__ == '__main__':
    raise SystemExit(main())
