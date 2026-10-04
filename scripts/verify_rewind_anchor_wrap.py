'''Bug 1 regression E2E: rewind with a long, multi-line, row-wrapping anchor.

Before the fix this failed with
  LookupError: checkpoint anchor not found after 64 ArrowUp steps
because the checkpoint preview shows only the first line of a message while
_anchor_terms matched whole-message text, and list-top detection never fired
(ticking relative timestamps kept the whole-screen diff alive).

Also covers the negative case: an anchor that is not in the checkpoint list
must fail fast with AnchorOutOfRangeError (business limit), not LookupError
after 64 wasted steps.

Evidence: evidence/rewind_bugfix_anchor_wrap.json (UTF-8); one-line stdout.
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
from scripts.verify_rewind_hybrid import find_transcript, snapshot


def main() -> int:
    started = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix='pan-rewind-wrap-', dir=str(ROOT)))
    target = work / 'rewind_wrap_probe.txt'
    sid = f'pan_rewind_wrap_{int(time.time())}'
    marker = 'PAN_WRAP_FILE_MARK'
    filler = ('lorem ipsum dolor sit amet consectetur adipiscing elit '
              'sed do eiusmod tempor incididunt ut labore et dolore magna aliqua ')
    # Multi-line AND row-wrapping: the first line is the only thing the cbc
    # checkpoint preview can ever show. The instruction comes before the
    # filler so a distracted model still acts on it.
    prompt = (
        f'PAN_WRAP_ANCHOR task: IMMEDIATELY use your native Write file tool (never Bash or shell) '
        f'to create exactly one file at {target} with exactly this content: {marker}\n'
        f'Context: {filler}{filler}\n'
        'After writing the file, reply with exactly PAN_WRAP_DONE.'
    )
    evidence: dict[str, Any] = {
        'workdir': str(work), 'session_a': sid, 'target': str(target),
        'prompt': prompt, 'prompt_len': len(prompt), 'prompt_lines': prompt.count('\n') + 1,
    }

    def flush() -> None:
        evidence['elapsed_seconds'] = round(time.monotonic() - started, 3)
        (EVIDENCE / 'rewind_bugfix_anchor_wrap.json').write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8',
        )

    try:
        cbc = shutil.which('cbc')
        if not cbc:
            evidence['status'] = 'blocked'
            return 2
        # The headless model occasionally ignores the instruction inside a
        # long multi-line prompt; allow one retry with a fresh session id.
        create = None
        for attempt in (1, 2):
            create = subprocess.run(
                [cbc, '-p', '--session-id', sid, '--permission-mode', 'bypassPermissions',
                 '--output-format', 'json', prompt],
                cwd=str(work), capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=180, check=False,
            )
            evidence.setdefault('create_attempts', []).append({
                'attempt': attempt,
                'session_id': sid,
                'exit_code': create.returncode,
                'stdout_tail': (create.stdout or '')[-800:],
            })
            flush()
            if create.returncode == 0 and target.exists() and find_transcript(sid):
                break
            sid = f'pan_rewind_wrap_{int(time.time() * 1000) % 10**10}'
            evidence['session_a'] = sid
        transcript_a = find_transcript(sid)
        evidence['file_after_create'] = (
            target.read_text(encoding='utf-8', errors='replace') if target.exists() else None)
        flush()
        if create.returncode != 0 or not target.exists() or not transcript_a:
            evidence['status'] = 'prepare_failed'
            return 1
        evidence['transcript_a_before'] = snapshot(transcript_a)

        # Positive: long multi-line anchor must rewind successfully.
        ok_run = run_hybrid_rewind(
            sid, work, AnchorSpec(message_text=prompt), scope=1,
            expected_files={target: None}, timeout=35.0,
        )
        evidence['positive'] = {
            'success': ok_run.success,
            'error': ok_run.error,
            'navigation_steps': ok_run.file_rewind.navigation_steps if ok_run.file_rewind else None,
            'truncation': ok_run.truncation.__dict__ if ok_run.truncation else None,
            'elapsed_seconds': ok_run.elapsed_seconds,
        }
        flush()

        # Negative: a bogus anchor must fail fast with AnchorOutOfRangeError.
        bad_run = run_hybrid_rewind(
            sid, work,
            AnchorSpec(message_text='PAN_DEFINITELY_MISSING_ANCHOR zzz qqq'),
            scope=1, expected_files=None, timeout=35.0,
        )
        evidence['negative'] = {
            'success': bad_run.success,
            'error': bad_run.error,
            'elapsed_seconds': bad_run.elapsed_seconds,
        }
        evidence['transcript_a_after'] = snapshot(transcript_a)
        evidence['file_after_rewind'] = target.exists()
        flush()

        evidence['assertions'] = {
            'positive_success': ok_run.success,
            'file_rolled_back': not target.exists(),
            'conversation_truncated': bool(ok_run.truncation and ok_run.truncation.removed_lines > 0),
            'negative_failed': not bad_run.success,
            'negative_is_out_of_range': bool(
                bad_run.error and 'AnchorOutOfRangeError' in bad_run.error
                and '不在可回滚的检查点范围内' in bad_run.error
            ),
            'no_stacked_runtimeerror': not (bad_run.error and 'RuntimeError: RuntimeError' in bad_run.error),
            'parent_unchanged': (
                evidence['transcript_a_after']['sha1'] == evidence['transcript_a_before']['sha1']),
        }
        evidence['status'] = 'success' if all(evidence['assertions'].values()) else 'failed'
        return 0 if evidence['status'] == 'success' else 1
    except subprocess.TimeoutExpired:
        evidence['status'] = 'timeout'
        return 1
    except Exception as exc:
        evidence['status'] = 'exception'
        evidence['error'] = f'{type(exc).__name__}: {exc}'
        return 1
    finally:
        flush()
        shutil.rmtree(work, ignore_errors=True)
        print('anchor wrap e2e status={} elapsed={}'.format(
            evidence.get('status'), evidence.get('elapsed_seconds')))


if __name__ == '__main__':
    raise SystemExit(main())
