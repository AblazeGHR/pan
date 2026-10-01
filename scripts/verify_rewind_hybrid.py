'''End-to-end verification for the hybrid cbc rewind flow.'''

from __future__ import annotations

import hashlib
import json
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

from packages.core.adapters.cbc import sessions as cbc_sessions
from packages.core.rewind import AnchorSpec, run_hybrid_rewind


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


def transcript_tail(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    last = records[-1] if records else {}
    texts = []
    for item in last.get('content') or []:
        if isinstance(item, dict) and isinstance(item.get('text'), str):
            texts.append(item['text'])
    return {
        'lines': len(lines),
        'last_type': last.get('type'),
        'last_role': last.get('role'),
        'last_text': ' '.join(texts),
    }


def main() -> int:
    started = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix='pan-rewind-hybrid-', dir=str(ROOT)))
    target = work / 'rewind_hybrid_probe.txt'
    sid_a = f'pan_rewind_hybrid_{int(time.time())}'
    marker = 'PAN_REWIND_HYBRID_MARKER'
    prompt = (
        f'Use your native Write or Edit file tool, never Bash or shell, to create exactly one file '
        f'at {target} with exactly this content: {marker}. Then confirm the file was created.'
    )
    evidence: dict[str, Any] = {
        'workdir': str(work),
        'session_a': sid_a,
        'target': str(target),
        'marker': marker,
    }
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
        evidence['parent_raw_usage_count'] = len(cbc_sessions.get_raw_usage(sid_a, str(work)))

        result = run_hybrid_rewind(
            sid_a,
            work,
            # Anchor on the full user prompt, like the server does: cbc
            # 2.160.0 truncates checkpoint previews to one short row, so the
            # trailing marker alone no longer matches any preview.
            AnchorSpec(message_text=prompt),
            expected_files={target: None},
            timeout=35.0,
        )
        evidence['result'] = {
            'stage': result.stage,
            'success': result.success,
            'error': result.error,
            'forked_cli_session_id': result.forked_cli_session_id,
            'fork': result.fork.__dict__ if result.fork else None,
            'file_rewind': result.file_rewind.__dict__ if result.file_rewind else None,
            'truncation': result.truncation.__dict__ if result.truncation else None,
            'history': result.history,
            'raw_usage': result.raw_usage,
            'stage_events': result.stage_events,
            'elapsed_seconds': result.elapsed_seconds,
        }
        transcript_b = find_transcript(result.forked_cli_session_id or '')
        evidence['transcript_b'] = str(transcript_b) if transcript_b else None
        evidence['transcript_a_after'] = snapshot(transcript_a)
        evidence['file_after_rewind'] = (
            target.read_text(encoding='utf-8', errors='replace') if target.exists() else None
        )
        evidence['transcript_b_tail'] = transcript_tail(transcript_b) if transcript_b else None
        history_text = json.dumps(result.history, ensure_ascii=False)
        pty_pid = (result.file_rewind.cleanup or {}).get('pid') if result.file_rewind else None
        pty_alive = False
        if pty_pid:
            import psutil
            pty_alive = psutil.pid_exists(int(pty_pid))
        evidence['assertions'] = {
            'file_rolled_back': not target.exists(),
            'parent_unchanged': (
                evidence['transcript_a_after']['sha1'] == evidence['transcript_a_before']['sha1']
                and evidence['transcript_a_after']['lines'] == evidence['transcript_a_before']['lines']
            ),
            'child_truncated': bool(result.truncation and result.truncation.removed_lines > 0),
            # Exclusive-anchor semantics: the anchor is the only user message,
            # so the forked transcript is empty and the new history has no
            # rows; the anchor text itself must be gone from both.
            'child_excludes_anchor': bool(
                evidence['transcript_b_tail']
                and evidence['transcript_b_tail']['lines'] == 0
            ),
            'history_is_truncated': len(result.history) == 0 and marker not in history_text,
            'usage_recalculated': len(result.raw_usage) == 0 and evidence['parent_raw_usage_count'] > 0,
            'pty_reclaimed': bool(result.file_rewind and result.file_rewind.cleanup and not pty_alive),
        }
        evidence['status'] = 'success' if result.success and all(evidence['assertions'].values()) else 'failed'
        return 0 if evidence['status'] == 'success' else 1
    except subprocess.TimeoutExpired:
        evidence['status'] = 'timeout'
        evidence['error'] = 'bounded subprocess timeout'
        return 1
    except Exception as exc:
        evidence['status'] = 'exception'
        evidence['error'] = f'{type(exc).__name__}: {exc}'
        return 1
    finally:
        evidence['elapsed_seconds'] = round(time.monotonic() - started, 3)
        (EVIDENCE / 'rewind_hybrid_e2e.json').write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8',
        )
        shutil.rmtree(work, ignore_errors=True)
        print('rewind hybrid e2e status={} elapsed={}'.format(
            evidence.get('status'), evidence.get('elapsed_seconds'),
        ))


if __name__ == '__main__':
    raise SystemExit(main())
