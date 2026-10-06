'''Hybrid rewind flow: cbc restores files, Pan truncates the forked transcript.'''

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from packages.core.adapters.cbc import sessions as cbc_sessions
from packages.core.rewind.driver import (
    AnchorSpec,
    ForkResult,
    RewindResult,
    _format_exc,
    coerce_rewind_scope,
    fork_session,
    rewind_in_pty,
)
from packages.core.rewind.storage import RewindRecordStore
from packages.core.rewind.transcript import TranscriptAnchor, TruncationResult, truncate_transcript
from packages.core.terminal.observer import require_pyte


StatusCallback = Callable[[str, Mapping[str, Any]], None]
SUPPORTED_REWIND_ADAPTERS = frozenset({'cbc'})


def ensure_rewind_supported(adapter: str) -> None:
    if adapter not in SUPPORTED_REWIND_ADAPTERS:
        raise NotImplementedError(
            f'rewind is not implemented for adapter {adapter!r}; only cbc is supported'
        )


@dataclass
class HybridRewindResult:
    stage: str = 'starting'
    success: bool = False
    scope: int = 1
    parent_cli_session_id: str = ''
    forked_cli_session_id: str | None = None
    workdir: str = ''
    fork: ForkResult | None = None
    file_rewind: RewindResult | None = None
    truncation: TruncationResult | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    raw_usage: list[dict[str, Any]] = field(default_factory=list)
    stage_events: list[dict[str, Any]] = field(default_factory=list)
    record_path: str | None = None
    record_error: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0


def _anchor_to_transcript(anchor: AnchorSpec | str) -> TranscriptAnchor:
    if isinstance(anchor, AnchorSpec):
        return TranscriptAnchor(
            message_id=anchor.message_id,
            message_text=anchor.message_text,
            absolute_index=anchor.absolute_index,
            match_ordinal=anchor.match_ordinal,
        )
    return TranscriptAnchor(message_text=str(anchor))


def run_hybrid_rewind(parent_cli_session_id: str, workdir: str | Path,
                      anchor: AnchorSpec | str,
                      *, adapter: str = 'cbc',
                      scope: int | str = 1,
                      expected_files: Mapping[str | Path, Any] | None = None,
                      watched_files: Sequence[str | Path] | None = None,
                      timeout: float = 35.0,
                      on_stage: StatusCallback | None = None,
                      record_store: RewindRecordStore | None = None,
                      pan_session_id: str | None = None,
                      job_id: str | None = None) -> HybridRewindResult:
    started = time.monotonic()
    scope = coerce_rewind_scope(scope)
    result = HybridRewindResult(
        parent_cli_session_id=parent_cli_session_id,
        workdir=str(workdir),
        scope=scope,
    )
    record_store = record_store if pan_session_id else None
    job_id = job_id or f'rewind_{secrets.token_hex(8)}'

    def emit(stage: str, **details: Any) -> None:
        result.stage = stage
        event = {
            'stage': stage,
            'details': dict(details),
            'elapsed_seconds': round(time.monotonic() - started, 3),
        }
        result.stage_events.append(event)
        if on_stage:
            on_stage(stage, details)

    def save_record() -> None:
        if not record_store or not pan_session_id:
            return
        record = {
            'job_id': job_id,
            'session_id': pan_session_id,
            'adapter': 'cbc',
            'scope': scope,
            'stage': result.stage,
            'success': result.success,
            'parent_cli_session_id': result.parent_cli_session_id,
            'forked_cli_session_id': result.forked_cli_session_id,
            'anchor': _anchor_to_transcript(anchor).__dict__,
            'limitation': 'cbc rewind does not track files edited manually or via bash.',
            'stage_events': result.stage_events,
            'fork': result.fork.__dict__ if result.fork else None,
            'file_rewind': result.file_rewind.__dict__ if result.file_rewind else None,
            'truncation': result.truncation.__dict__ if result.truncation else None,
            'error': result.error,
            'elapsed_seconds': result.elapsed_seconds,
        }
        try:
            result.record_path = str(record_store.save(record))
        except Exception as exc:
            result.record_error = f'{type(exc).__name__}: {exc}'

    try:
        ensure_rewind_supported(adapter)
        require_pyte()
        emit('starting', parent_cli_session_id=parent_cli_session_id, scope=scope)
        fork = fork_session(parent_cli_session_id, workdir, timeout=max(timeout, 180.0))
        result.fork = fork
        result.forked_cli_session_id = fork.new_session_id
        if fork.error or not fork.new_session_id or not fork.transcript_path:
            raise RuntimeError(fork.error or 'forked transcript path is missing')
        if fork.original_unchanged is False:
            raise RuntimeError('parent transcript changed during fork')

        def forward_file_stage(stage: str, details: Mapping[str, Any]) -> None:
            if stage == 'resuming':
                emit('resuming', **dict(details))
            elif stage in ('rewind-menu', 'restoring'):
                emit('rewinding-files', **dict(details))

        file_rewind = rewind_in_pty(
            fork.new_session_id,
            workdir,
            anchor,
            expected_files=expected_files,
            watched_files=watched_files,
            timeout=timeout,
            on_stage=forward_file_stage,
            scope=scope,
        )
        result.file_rewind = file_rewind
        if not file_rewind.success:
            raise RuntimeError(file_rewind.error or 'cbc file rewind failed')

        if scope == 3:
            # Code-only rewind: the forked transcript keeps the full
            # conversation, so Pan must not truncate it.
            emit('truncating', transcript_path=fork.transcript_path, skipped=True, scope=scope)
        else:
            emit('truncating', transcript_path=fork.transcript_path)
            result.truncation = truncate_transcript(
                fork.transcript_path,
                _anchor_to_transcript(anchor),
                expected_session_id=fork.new_session_id,
            )
        result.history = cbc_sessions.parse_history(fork.new_session_id, str(workdir))
        result.raw_usage = cbc_sessions.get_raw_usage(fork.new_session_id, str(workdir))
        result.success = True
        emit('completed', history_messages=len(result.history), raw_usage_records=len(result.raw_usage))
    except Exception as exc:
        result.success = False
        result.error = _format_exc(exc)
        emit('failed', error=result.error)
    finally:
        result.elapsed_seconds = round(time.monotonic() - started, 3)
        save_record()
    return result
