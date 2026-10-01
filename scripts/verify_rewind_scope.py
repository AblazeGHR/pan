'''End-to-end verification for rewind scope 1/2/3 (cbc confirmation menu).

The anchor is the file-creation message itself, so rewinding to it changes
both code and conversation and cbc shows the full three-option menu:
  1. Restore code and conversation
  2. Restore conversation
  3. Restore code
(Verified empirically on 2026-09-29: at a checkpoint where code is
unchanged, cbc only offers "Restore conversation" / "Never Mind".)

Scope semantics under test:
  1 = files rolled back, transcript truncated
  2 = files keep modified state, transcript truncated
  3 = files rolled back, transcript kept whole

Key sequence under test (menu defaults to option 1):
  scope 1: Enter
  scope 2: ArrowDown, Enter
  scope 3: ArrowDown, ArrowDown, Enter

Usage: py scripts/verify_rewind_scope.py <scope>
Evidence: evidence/rewind_scope_<scope>.json (UTF-8); console prints one line.
'''

from __future__ import annotations

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

from packages.core.rewind import AnchorSpec, run_hybrid_rewind
from scripts.verify_rewind_hybrid import find_transcript, snapshot, transcript_tail

KEY_SEQUENCES = {
    1: ['Enter'],
    2: ['ArrowDown', 'Enter'],
    3: ['ArrowDown', 'ArrowDown', 'Enter'],
}


def run_cbc(cbc: str, argv: list[str], work: Path, timeout: float = 180.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [cbc, *argv], cwd=str(work), capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=timeout, check=False,
    )


def main() -> int:
    scope = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    assert scope in (1, 2, 3)
    started = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix=f'pan-rewind-scope{scope}-', dir=str(ROOT)))
    target = work / f'rewind_scope{scope}_probe.txt'
    sid_a = f'pan_rewind_scope{scope}_{int(time.time())}'
    file_marker = f'PAN_SCOPE{scope}_FILE_MARK'
    file_prompt = (
        f'Use your native Write or Edit file tool, never Bash or shell, to create exactly one file '
        f'at {target} with exactly this content: {file_marker}. Then confirm the file was created.'
    )
    evidence: dict[str, Any] = {
        'scope': scope,
        'key_sequence': KEY_SEQUENCES[scope],
        'scope_label': {
            1: 'Restore code and conversation',
            2: 'Restore conversation',
            3: 'Restore code',
        }[scope],
        'workdir': str(work),
        'session_a': sid_a,
        'target': str(target),
        'file_marker': file_marker,
    }

    def flush() -> None:
        evidence['elapsed_seconds'] = round(time.monotonic() - started, 3)
        (EVIDENCE / f'rewind_scope_{scope}.json').write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8',
        )

    try:
        cbc = shutil.which('cbc')
        if not cbc:
            evidence['status'] = 'blocked'
            evidence['error'] = 'cbc unavailable'
            return 2
        second = run_cbc(cbc, ['-p', '--session-id', sid_a, '--permission-mode',
                               'bypassPermissions', '--output-format', 'json', file_prompt], work)
        evidence['create_file'] = {'exit_code': second.returncode}
        transcript_a = find_transcript(sid_a)
        evidence['transcript_a'] = str(transcript_a) if transcript_a else None
        evidence['file_after_create'] = (
            target.read_text(encoding='utf-8', errors='replace') if target.exists() else None
        )
        flush()
        if second.returncode != 0 or not target.exists() or not transcript_a:
            evidence['status'] = 'prepare_failed'
            return 1
        evidence['transcript_a_before'] = snapshot(transcript_a)

        result = run_hybrid_rewind(
            sid_a,
            work,
            # Anchor on the full prompt: the PTY menu truncates long lines,
            # so the 64-char visible prefix term must identify the checkpoint
            # (this mirrors production, where anchor_text is the full user
            # message). A bare trailing marker would be cut off the screen.
            AnchorSpec(message_text=file_prompt),
            scope=scope,
            expected_files={target: None} if scope in (1, 3) else None,
            timeout=35.0,
        )
        evidence['result'] = {
            'stage': result.stage,
            'success': result.success,
            'scope': result.scope,
            'error': result.error,
            'forked_cli_session_id': result.forked_cli_session_id,
            'fork': result.fork.__dict__ if result.fork else None,
            'file_rewind': result.file_rewind.__dict__ if result.file_rewind else None,
            'truncation': result.truncation.__dict__ if result.truncation else None,
            'history': result.history,
            'stage_events': result.stage_events,
            'elapsed_seconds': result.elapsed_seconds,
        }
        flush()
        transcript_b = find_transcript(result.forked_cli_session_id or '')
        evidence['transcript_a_after'] = snapshot(transcript_a)
        evidence['file_after_rewind_exists'] = target.exists()
        evidence['file_after_rewind'] = (
            target.read_text(encoding='utf-8', errors='replace') if target.exists() else None
        )
        evidence['transcript_b_tail'] = transcript_tail(transcript_b) if transcript_b else None
        history_text = json.dumps(result.history, ensure_ascii=False)
        parent_unchanged = (
            evidence['transcript_a_after']['sha1'] == evidence['transcript_a_before']['sha1']
            and evidence['transcript_a_after']['lines'] == evidence['transcript_a_before']['lines']
        )
        file_rolled_back = not target.exists()
        conversation_truncated = bool(result.truncation and result.truncation.removed_lines > 0)
        scope_selected = bool(
            result.file_rewind
            and result.file_rewind.success
            and result.file_rewind.scope == scope
            and result.file_rewind.scope_label == evidence['scope_label']
        )
        # Exclusive-anchor semantics: the anchor is the only user message, so
        # a conversation-truncating scope leaves an EMPTY forked transcript.
        excludes_anchor = bool(
            evidence['transcript_b_tail']
            and evidence['transcript_b_tail']['lines'] == 0
        )
        if scope == 1:
            scope_assertions = {
                'file_rolled_back': file_rolled_back,
                'conversation_truncated': conversation_truncated,
                'child_excludes_anchor': excludes_anchor,
            }
        elif scope == 2:
            scope_assertions = {
                'file_kept_modified': target.exists()
                and evidence['file_after_rewind'] == file_marker,
                'conversation_truncated': conversation_truncated,
                'child_excludes_anchor': excludes_anchor,
                'history_is_truncated': len(result.history) == 0
                and file_marker not in history_text,
            }
        else:
            scope_assertions = {
                'file_rolled_back': file_rolled_back,
                'transcript_not_truncated': result.truncation is None,
                'history_kept_whole': bool(result.history)
                and file_marker in history_text,
            }
        evidence['assertions'] = {
            'success': result.success,
            'scope_selected': scope_selected,
            'parent_unchanged': parent_unchanged,
            **scope_assertions,
        }
        evidence['status'] = 'success' if all(evidence['assertions'].values()) else 'failed'
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
        flush()
        shutil.rmtree(work, ignore_errors=True)
        print('rewind scope={} status={} elapsed={}'.format(
            scope, evidence.get('status'), evidence.get('elapsed_seconds'),
        ))


if __name__ == '__main__':
    raise SystemExit(main())
