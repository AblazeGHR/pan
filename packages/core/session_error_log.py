"""Append-only session error samples for later classification.

One global JSONL file (``data/logs/session-errors.jsonl`` by default).  Every
record keeps the complete error text, the CLI's raw error payload when Pan
received one, and the full history message that preceded the error.  The file
is never rotated or evicted by Pan; operators own its retention.

Design rules enforced here:

* Never infer ``retryable``/``code``/``status``/``type`` from message text.
  Only values actually present in a provider payload are copied.
* Keep CLI facts (``cli``) separate from Pan-derived facts (``pan`` and the
  top-level outcome fields).  Unparsed stream text is attached as
  ``rawDiagnostics`` and is never called an error.
* The previous message is snapshotted by the caller at the moment the error is
  detected, before later concurrent messages can be appended.
* Async callers use ``record_session_error_async``: file locking and writes run
  on a single dedicated executor thread, so the event loop is never blocked.
  Records are prepared (timestamp/sequence/previous message) on the caller's
  thread and written FIFO.
* A lock failure or write failure drops the sample, increments observable
  counters and logs a warning.  It never writes without the cross-process lock
  and never raises into worker/task/queue code.
* The file tail is re-checked under the lock before every append; an in-process
  "already newline terminated" cache is intentionally not used.
* Credentials are best-effort redacted; process argv/environment are never
  collected by this module.
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# Stable namespace: the same logical event always receives the same eventId in
# one process, so duplicate terminal/recovery paths cannot append twice.
_EVENT_NAMESPACE = uuid.UUID("5f2c7e18-4d9a-4e11-9f37-2b1f0c8a6d21")

_LOG = logging.getLogger(__name__)

# Pan-derived classification.  The value is selected by the call site (the
# actual code path that observed the event), never by matching message text.
KINDS = (
    "terminal_error",          # provider result with is_error / Pan preflight failure
    "worker_crash",            # provider process EOF / unexpected exit
    "worker_start_failure",    # stream process could not be spawned
    "native_operation_error",  # pan.native_error from a native Codex/CBC wrapper
    "transient_provider_error",  # reconnect / in-turn error not (yet) terminal
    "tool_failure",            # one tool/item failed; the task may still succeed
    "cancelled",               # provider-reported cancellation, not an error
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_LOG_FILE = "data/logs/pan.log"

_THREAD_LOCK = threading.RLock()
_RECENT_EVENT_IDS: deque[str] = deque()
_RECENT_EVENT_ID_SET: set[str] = set()
_INFLIGHT_EVENT_IDS: set[str] = set()
_MAX_RECENT_EVENT_IDS = 8192
_OCCURRENCE: dict[str, int] = {}
_SEQUENCE = 0

# One writer thread preserves submission order and keeps lock/disk waits off
# the event loop.  Each async caller awaits its own write (bounded by the lock
# timeout); there is no unbounded background queue and no silent drop.
_WRITE_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="pan-session-error-log")


def _shutdown_executor() -> None:
    try:
        _WRITE_EXECUTOR.shutdown(wait=True)
    except Exception:
        pass


atexit.register(_shutdown_executor)

_path_override: Path | None = None

_STATS = {
    "written": 0,
    "duplicates": 0,
    "failures": 0,
    "lockFailures": 0,
    "pending": 0,
    "lastErrorType": None,
    "lastErrorAt": None,
    "lastEventAt": None,
    "lastEventId": None,
}

# Keys whose values are credentials rather than diagnostic data.  Deliberately
# excludes bare "token"/"tokens": token *usage* counters are not secrets.
_SECRET_KEY_RE = re.compile(
    r"(?i)^(authorization|cookie|set-cookie|api[-_]?key|access[-_]?token|"
    r"refresh[-_]?token|id[-_]?token|bearer|password|passwd|secret|"
    r"client[-_]?secret|credential)s?$"
)
_SECRET_TEXT_PATTERNS = (
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;\"']+"),
     r"\1***redacted***"),
    (re.compile(r"(?i)(x-api-key\s*:\s*)[^\s,;\"']+"),
     r"\1***redacted***"),
    (re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token)"
                r"\s*[=:]\s*)[^\s,;\"'}]+"),
     r"\1***redacted***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "sk-***redacted***"),
    (re.compile(r"(?i)(cookie\s*:\s*)[^\r\n]+"), r"\1***redacted***"),
)


def configure(path: str | os.PathLike[str] | None) -> None:
    """Set the process-wide output path (called by logging setup / tests)."""
    global _path_override
    _path_override = Path(path) if path is not None else None


def reset_state() -> None:
    """Clear process-local caches and counters.  Tests only."""
    global _path_override, _SEQUENCE
    with _THREAD_LOCK:
        _path_override = None
        _RECENT_EVENT_IDS.clear()
        _RECENT_EVENT_ID_SET.clear()
        _INFLIGHT_EVENT_IDS.clear()
        _OCCURRENCE.clear()
        _SEQUENCE = 0
        _STATS.update({
            "written": 0, "duplicates": 0, "failures": 0, "lockFailures": 0,
            "pending": 0, "lastErrorType": None, "lastErrorAt": None,
            "lastEventAt": None, "lastEventId": None,
        })


def path() -> Path:
    """Resolve the single global sample file."""
    if _path_override is not None:
        return _path_override
    env = os.environ.get("PAN_SESSION_ERROR_LOG_PATH")
    if env:
        return Path(env)
    try:
        from .config import load_config
        cfg = load_config().get("logging", {}) or {}
        raw = str(cfg.get("file", _DEFAULT_LOG_FILE))
    except Exception:
        raw = _DEFAULT_LOG_FILE
    log_file = Path(raw)
    if not log_file.is_absolute():
        log_file = _REPO_ROOT / log_file
    return log_file.parent / "session-errors.jsonl"


def stats() -> dict:
    with _THREAD_LOCK:
        result = dict(_STATS)
        result["pending"] = len(_INFLIGHT_EVENT_IDS)
        result["path"] = str(path())
    return result


def flush(timeout: float = 5.0) -> bool:
    """Wait until all submitted writes have run.  Safe to call at shutdown."""
    try:
        _WRITE_EXECUTOR.submit(lambda: None).result(timeout=timeout)
        return True
    except Exception as exc:
        _LOG.warning("session-error-log: flush failed: %s", exc)
        return False


def sanitize(value: Any, *, key: str | None = None) -> Any:
    """Best-effort credential redaction for copied raw payloads.

    Only credential-shaped keys and well-known token formats are changed.  Full
    diagnostic text is otherwise preserved verbatim.
    """
    if key is not None and isinstance(key, str) and _SECRET_KEY_RE.match(key):
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            return "***redacted***"
    if isinstance(value, str):
        text = value
        for pattern, replacement in _SECRET_TEXT_PATTERNS:
            text = pattern.sub(replacement, text)
        return text
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        return {
            str(item_key): sanitize(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    return value


def cli_error_fields(event: Any) -> tuple[Any, str | None, str | None]:
    """Extract the provider's own error object/text from one raw CLI event.

    Returns ``(raw_error, error_text, error_source)``.  Nothing is synthesized
    from the terminal result or from the message wording.
    """
    if not isinstance(event, dict):
        return None, None, None
    error = event.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("data") or error.get("error_text")
        if isinstance(message, (dict, list)):
            message = json.dumps(message, ensure_ascii=False, default=str)
        if message is not None and str(message).strip():
            return error, str(message), "cli.error"
        return error, json.dumps(error, ensure_ascii=False, default=str), "cli.error"
    if isinstance(error, str) and error.strip():
        return error, error, "cli.error"
    text = event.get("error_text")
    if isinstance(text, str) and text.strip():
        return error, text, "cli.error_text"
    return error, None, None


def previous_message_snapshot(session: Any) -> dict | None:
    """Full copy of the last history row at this exact moment, or None.

    Callers must invoke this synchronously at error detection, before any
    later concurrent append.  It never filters by role: a tool/thinking row is
    a legitimate "last message" and must not be replaced by an older user row.
    """
    if session is None:
        return None
    try:
        from . import session as session_store
        return session_store.history_tail_snapshot(session)
    except Exception:
        _LOG.warning("session-error-log: cannot snapshot previous message",
                     exc_info=True)
        return None


def record_session_error(
    *,
    kind: str,
    error_text: str,
    error_text_source: str,
    terminal: bool,
    session: Any = None,
    session_id: str | None = None,
    session_name: str | None = None,
    adapter: str | None = None,
    model: str | None = None,
    worker_id: str | None = None,
    generation: int | None = None,
    task_seq: int | None = None,
    task_id: str | None = None,
    execution_seq: int | None = None,
    queue_item_id: str | None = None,
    source_session_id: str | None = None,
    pan_status: str | None = None,
    cancelled: bool = False,
    recovered: bool | None = None,
    cli_raw: Any = None,
    cli_error_events: list | None = None,
    cli_raw_output: str | None = None,
    cli_origin: str | None = None,
    cli_returncode: Any = None,
    cli_timed_out: bool | None = None,
    pan_extra: dict | None = None,
    previous_message: dict | None = None,
    previous_captured: bool = False,
    dedupe_token: str | None = None,
    raw_diagnostics: str | None = None,
    raw_diagnostics_source: Any = None,
    runtime_issue: dict | None = None,
) -> str | None:
    """Synchronously append one sample.  Never raises; returns eventId/None."""
    try:
        prepared = _prepare_record(
            kind=kind,
            error_text=error_text,
            error_text_source=error_text_source,
            terminal=terminal,
            session=session,
            session_id=session_id,
            session_name=session_name,
            adapter=adapter,
            model=model,
            worker_id=worker_id,
            generation=generation,
            task_seq=task_seq,
            task_id=task_id,
            execution_seq=execution_seq,
            queue_item_id=queue_item_id,
            source_session_id=source_session_id,
            pan_status=pan_status,
            cancelled=cancelled,
            recovered=recovered,
            cli_raw=cli_raw,
            cli_error_events=cli_error_events,
            cli_raw_output=cli_raw_output,
            cli_origin=cli_origin,
            cli_returncode=cli_returncode,
            cli_timed_out=cli_timed_out,
            pan_extra=pan_extra,
            previous_message=previous_message,
            previous_captured=previous_captured,
            dedupe_token=dedupe_token,
            raw_diagnostics=raw_diagnostics,
            raw_diagnostics_source=raw_diagnostics_source,
            runtime_issue=runtime_issue,
        )
    except Exception as exc:
        _record_failure(exc)
        return None
    if prepared is None:
        return None
    return _write_prepared(prepared)


async def record_session_error_async(**kwargs) -> str | None:
    """Prepare on the caller thread, then write on the dedicated executor.

    The previous-message snapshot and processSequence are taken before the
    first await, preserving detection-time facts and submission order.  The
    event loop is not blocked by lock waits or disk I/O.
    """
    try:
        prepared = _prepare_record(**kwargs)
    except Exception as exc:
        _record_failure(exc)
        return None
    if prepared is None:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return _write_prepared(prepared)
    try:
        return await loop.run_in_executor(_WRITE_EXECUTOR, _write_prepared, prepared)
    except Exception as exc:  # executor shutdown / cancellation of the submit
        _record_failure(exc)
        return None


def _prepare_record(
    *,
    kind: str,
    error_text: str,
    error_text_source: str,
    terminal: bool,
    session: Any = None,
    session_id: str | None = None,
    session_name: str | None = None,
    adapter: str | None = None,
    model: str | None = None,
    worker_id: str | None = None,
    generation: int | None = None,
    task_seq: int | None = None,
    task_id: str | None = None,
    execution_seq: int | None = None,
    queue_item_id: str | None = None,
    source_session_id: str | None = None,
    pan_status: str | None = None,
    cancelled: bool = False,
    recovered: bool | None = None,
    cli_raw: Any = None,
    cli_error_events: list | None = None,
    cli_raw_output: str | None = None,
    cli_origin: str | None = None,
    cli_returncode: Any = None,
    cli_timed_out: bool | None = None,
    pan_extra: dict | None = None,
    previous_message: dict | None = None,
    previous_captured: bool = False,
    dedupe_token: str | None = None,
    raw_diagnostics: str | None = None,
    raw_diagnostics_source: Any = None,
    runtime_issue: dict | None = None,
) -> tuple[dict, str, Any] | None:
    if kind not in KINDS:
        kind = "terminal_error"
    if session is not None:
        session_id = session_id or getattr(session, "id", None)
        session_name = session_name if session_name is not None else getattr(session, "name", None)
        adapter = adapter or getattr(session, "adapter", None)
        model = model if model is not None else getattr(session, "model", None)
    if not previous_captured:
        previous_message = previous_message_snapshot(session)

    timestamp = datetime.now(timezone.utc).isoformat()
    cli = _build_cli_block(
        cli_raw=cli_raw,
        cli_error_events=cli_error_events,
        cli_raw_output=cli_raw_output,
        cli_origin=cli_origin,
        cli_returncode=cli_returncode,
        cli_timed_out=cli_timed_out,
    )
    cli_error, cli_error_text, cli_error_source = cli_error_fields(cli_raw)
    if cli_error is None and cli_error_events:
        for candidate in reversed(cli_error_events):
            candidate_error, candidate_text, candidate_source = cli_error_fields(candidate)
            if candidate_text is not None or candidate_error is not None:
                cli_error, cli_error_text, cli_error_source = (
                    candidate_error, candidate_text, candidate_source)
                break
    if cli_error is not None:
        cli["error"] = sanitize(cli_error)
    if cli_error_text is not None:
        cli["errorText"] = sanitize(cli_error_text)
    if cli_error_source is not None:
        cli["errorSource"] = cli_error_source
    if cli_raw is not None:
        cli["raw"] = sanitize(cli_raw)
        if isinstance(cli_raw, dict) and cli_raw.get("error_source"):
            cli["errorSource"] = str(cli_raw["error_source"])
    if cli_error_events:
        cli["errorEvents"] = sanitize(cli_error_events)

    # Prefer the CLI's own error text.  Only when the provider gave none do we
    # fall back to the terminal result, explicitly labelled as such.
    if cli_error_text is not None and str(cli_error_text).strip():
        final_error_text = str(cli_error_text)
        final_source = str(cli.get("errorSource") or cli_error_source or "cli.error")
    else:
        final_error_text = str(error_text or "")
        final_source = str(error_text_source or "pan.runtime")

    base_key = "|".join([
        str(session_id or "-"),
        str(worker_id or "-"),
        str(generation if generation is not None else "-"),
        str(task_seq if task_seq is not None else "-"),
        str(kind),
        str(cli_origin or "-"),
        str(pan_status or "-"),
    ])
    if dedupe_token:
        event_key = base_key + "|" + str(dedupe_token)
    elif terminal:
        event_key = base_key
    else:
        event_key = base_key + "|occ:" + str(_next_occurrence(base_key))
    event_id = "se_" + uuid.uuid5(_EVENT_NAMESPACE, event_key).hex

    record = {
        "schemaVersion": SCHEMA_VERSION,
        "eventId": event_id,
        "processSequence": _next_sequence(),
        "timestamp": timestamp,
        "kind": kind,
        "terminal": bool(terminal),
        "cancelled": bool(cancelled),
        "sessionId": session_id,
        "sessionName": session_name,
        "name": session_name,
        "adapter": adapter,
        "model": model,
        "workerId": worker_id,
        "generation": generation,
        "taskSeq": task_seq,
        "taskId": task_id,
        "executionSeq": execution_seq,
        "queueItemId": queue_item_id,
        "sourceSessionId": source_session_id,
        "panStatus": pan_status,
        "errorText": sanitize(final_error_text),
        "errorTextSource": final_source,
        "cli": cli if cli else None,
        "pan": _build_pan_block(
            pan_status=pan_status,
            kind=kind,
            terminal=terminal,
            pan_extra=pan_extra,
        ),
        "previousMessage": previous_message,
        "previousMessageSelection": "last_history_row_at_error_detection",
    }
    if recovered is not None:
        record["recovered"] = bool(recovered)
    if previous_message is None:
        record["previousMessageMissing"] = True
    if raw_diagnostics is not None:
        record["rawDiagnostics"] = sanitize(raw_diagnostics)
        record["rawDiagnosticSource"] = "unparsed_stream_text"
        record["rawDiagnosticComplete"] = True
    if runtime_issue is not None:
        record["runtimeIssue"] = sanitize(runtime_issue)
    return record, event_id, raw_diagnostics_source


def _write_prepared(prepared: tuple[dict, str, Any]) -> str | None:
    record, event_id, raw_source = prepared
    if raw_source is not None:
        text, count = _resolve_raw_diagnostics(raw_source)
        if text:
            record = {
                **record,
                "rawDiagnostics": sanitize(text),
                "rawDiagnosticLineCount": count,
                "rawDiagnosticSource": "unparsed_stream_text",
                "rawDiagnosticComplete": True,
            }
    line = (json.dumps(record, ensure_ascii=False, default=str,
                       separators=(",", ":")) + "\n").encode("utf-8")
    with _THREAD_LOCK:
        if event_id in _RECENT_EVENT_ID_SET:
            _STATS["duplicates"] += 1
            return event_id
        if event_id in _INFLIGHT_EVENT_IDS:
            # Another writer already owns this logical event; it will either
            # persist it or report the failure.
            return event_id
        _INFLIGHT_EVENT_IDS.add(event_id)
    try:
        _append_line(path(), line)
    except Exception as exc:  # lock/write failure: drop the sample, keep the task
        _record_failure(exc)
        return None
    else:
        with _THREAD_LOCK:
            _remember_event_id(event_id)
            _STATS["written"] += 1
            _STATS["lastEventAt"] = record["timestamp"]
            _STATS["lastEventId"] = event_id
        return event_id
    finally:
        # Covers success, OSError, and BaseException (cancellation of the
        # executor future / interpreter shutdown).  A cancelled awaiter can
        # never leave a permanently "inflight" event that blocks flush.
        with _THREAD_LOCK:
            _INFLIGHT_EVENT_IDS.discard(event_id)


def _resolve_raw_diagnostics(source: Any) -> tuple[str | None, int]:
    """Read a per-run scratch file without touching the event loop."""
    try:
        handle, count = source
    except (TypeError, ValueError):
        return None, 0
    try:
        handle.seek(0)
        data = handle.read()
    except Exception:
        return None, 0
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="replace")
    else:
        text = str(data)
    try:
        line_count = int(count)
    except (TypeError, ValueError):
        line_count = 0
    return text, line_count


def _build_cli_block(
    *,
    cli_raw: Any,
    cli_error_events: list | None,
    cli_raw_output: str | None,
    cli_origin: str | None,
    cli_returncode: Any,
    cli_timed_out: bool | None,
) -> dict:
    block: dict = {}
    if cli_origin:
        block["origin"] = cli_origin
    if isinstance(cli_raw, dict):
        if cli_raw.get("type") is not None:
            block["eventType"] = cli_raw.get("type")
        if cli_raw.get("turn_status") is not None:
            block["turnStatus"] = cli_raw.get("turn_status")
        if cli_raw.get("is_error") is not None:
            block["isError"] = bool(cli_raw.get("is_error"))
        if cli_raw.get("cancelled") is not None:
            block["cancelled"] = bool(cli_raw.get("cancelled"))
    if cli_error_events:
        block["errorEventCount"] = len(cli_error_events)
    if cli_raw_output:
        block["rawOutputText"] = sanitize(cli_raw_output)
    if cli_returncode is not None:
        block["returncode"] = cli_returncode
    if cli_timed_out is not None:
        block["timedOut"] = bool(cli_timed_out)
    return block


def _build_pan_block(*, pan_status, kind, terminal, pan_extra) -> dict:
    block: dict = {
        "kind": kind,
        "terminal": bool(terminal),
    }
    if pan_status is not None:
        block["status"] = pan_status
    if isinstance(pan_extra, dict):
        for key, value in pan_extra.items():
            block[str(key)] = sanitize(value)
    return block


def _next_occurrence(base_key: str) -> int:
    with _THREAD_LOCK:
        value = _OCCURRENCE.get(base_key, 0)
        _OCCURRENCE[base_key] = value + 1
        return value


def _next_sequence() -> int:
    global _SEQUENCE
    with _THREAD_LOCK:
        value = _SEQUENCE
        _SEQUENCE += 1
        return value


def _remember_event_id(event_id: str) -> None:
    with _THREAD_LOCK:
        if event_id in _RECENT_EVENT_ID_SET:
            return
        _RECENT_EVENT_IDS.append(event_id)
        _RECENT_EVENT_ID_SET.add(event_id)
        while len(_RECENT_EVENT_IDS) > _MAX_RECENT_EVENT_IDS:
            old = _RECENT_EVENT_IDS.popleft()
            _RECENT_EVENT_ID_SET.discard(old)


def _record_failure(exc: BaseException) -> None:
    with _THREAD_LOCK:
        _STATS["failures"] += 1
        _STATS["lastErrorType"] = type(exc).__name__
        _STATS["lastErrorAt"] = datetime.now(timezone.utc).isoformat()
    _LOG.warning(
        "session-error-log: append failed (%s); sample lost, task continues",
        type(exc).__name__, exc_info=True,
    )


def _append_line(destination: Path, line: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(str(destination) + ".lock")
    with _THREAD_LOCK:
        with _cross_process_lock(lock_path) as lock:
            # Never append without the cross-process lock: an unlocked
            # os.write loop can interleave with another writer and corrupt
            # the JSONL line.  A dropped sample is observable and safe.
            if not lock.acquired:
                with _THREAD_LOCK:
                    _STATS["lockFailures"] += 1
                raise OSError("session-error-log cross-process lock unavailable")
            # Always re-check the tail under the lock.  Another process may
            # have crashed after a partial line even though this process
            # previously wrote a newline-terminated record.
            _ensure_newline(destination)
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            flags |= getattr(os, "O_BINARY", 0)
            fd = os.open(str(destination), flags, 0o644)
            try:
                view = memoryview(line)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("short write to session error log")
                    view = view[written:]
            finally:
                os.close(fd)


def _ensure_newline(destination: Path) -> None:
    try:
        with open(destination, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size <= 0:
                return
            handle.seek(size - 1)
            if handle.read(1) == b"\n":
                return
    except FileNotFoundError:
        return
    with open(destination, "ab") as handle:
        handle.write(b"\n")
        handle.flush()


class _WindowsNamedMutex:
    """Bounded Windows kernel mutex for one log file.

    ``msvcrt`` byte-range locks on a separately opened lock file intermittently
    return ERROR_ACCESS_DENIED when several fresh processes create/open it at
    the same time.  A named mutex is process-wide, automatically released when
    a process dies, and avoids that file-sharing race.
    """

    def __init__(self, lock_path: Path, timeout: float = 1.0):
        self._name = "Local\\PanSessionErrorLog_" + hashlib.sha256(
            os.path.normcase(str(lock_path.absolute())).encode("utf-8")
        ).hexdigest()
        self._timeout_ms = max(1, int(timeout * 1000))
        self._handle = None
        self.acquired = False

    def acquire(self) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateMutexW(None, False, self._name)
        if not handle:
            _LOG.warning("session-error-log: CreateMutexW failed: %s",
                         ctypes.WinError(ctypes.get_last_error()))
            return False
        wait = kernel32.WaitForSingleObject(handle, self._timeout_ms)
        if wait not in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        self.acquired = True
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        try:
            if not kernel32.ReleaseMutex(self._handle):
                _LOG.warning("session-error-log: ReleaseMutex failed: %s",
                             ctypes.WinError(ctypes.get_last_error()))
        finally:
            kernel32.CloseHandle(self._handle)
            self._handle = None
            self.acquired = False


class _cross_process_lock:
    """Bounded cross-process lock for this one file.

    Windows: named kernel mutex (no lock-file sharing race).  POSIX: advisory
    flock on a sibling ``.lock`` file.  Acquisition is bounded and never
    raises; callers must check ``acquired`` and refuse to write when false.
    """

    def __init__(self, lock_path: Path):
        self._path = lock_path
        self._handle = None
        self._mutex = None
        self.acquired = False

    def __enter__(self):
        if os.name == "nt":
            self._mutex = _WindowsNamedMutex(self._path)
            if not self._mutex.acquire():
                _LOG.warning(
                    "session-error-log: lock acquisition failed; sample will be dropped")
            else:
                self.acquired = True
            return self

        self._path.parent.mkdir(parents=True, exist_ok=True)
        open_deadline = time.monotonic() + 2.0
        while True:
            try:
                self._handle = self._path.open("a+b")
                break
            except OSError as exc:
                if time.monotonic() >= open_deadline:
                    _LOG.warning("session-error-log: lock open failed: %s", exc)
                    return self
                time.sleep(0.01)
        try:
            self._handle.seek(0, os.SEEK_END)
            if self._handle.tell() == 0:
                self._handle.write(b"\0")
                self._handle.flush()
            self._handle.seek(0)
            lock_deadline = time.monotonic() + 1.0
            while True:
                try:
                    import fcntl
                    fcntl.flock(self._handle.fileno(),
                                fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.acquired = True
                    break
                except OSError:
                    if time.monotonic() >= lock_deadline:
                        break
                    time.sleep(0.01)
        except OSError as exc:
            _LOG.warning("session-error-log: lock open failed: %s", exc)
        if not self.acquired:
            _LOG.warning(
                "session-error-log: lock acquisition failed; sample will be dropped")
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._mutex is not None:
            self._mutex.release()
            self._mutex = None
            self.acquired = False
            return False
        if self._handle is None:
            return False
        try:
            if self.acquired:
                self._handle.seek(0)
                try:
                    import fcntl
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            self._handle.close()
            self._handle = None
            self.acquired = False
        return False
