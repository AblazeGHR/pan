"""Append-only session error samples for later classification.

One global JSONL file (``data/logs/session-errors.jsonl`` by default).  Every
record keeps the complete error text, the CLI's raw error payload when Pan
received one, and the full history message that preceded the error.  The file
is never rotated or evicted by Pan; operators own its retention.

Design rules enforced here:

* Never infer ``retryable``/``code``/``status``/``type`` from message text.
  Only values actually present in a provider payload are copied.
* Keep CLI facts (``cli``) separate from Pan-derived facts (``pan`` and the
  top-level outcome fields).
* The previous message is snapshotted by the caller at the moment the error is
  detected, before later concurrent messages can be appended.
* A write failure is observable (warning + ``stats()``) and never raises into
  worker/task/queue code.  It cannot cause a retry, hand-off or deadlock.
* Credentials are best-effort redacted; process argv/environment are never
  collected by this module.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
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
_NEWLINE_TERMINATED: set[str] = set()
_RECENT_EVENT_IDS: deque[str] = deque()
_RECENT_EVENT_ID_SET: set[str] = set()
_MAX_RECENT_EVENT_IDS = 8192
_OCCURRENCE: dict[str, int] = {}

_path_override: Path | None = None

_STATS = {
    "written": 0,
    "duplicates": 0,
    "failures": 0,
    "lockFailures": 0,
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
    global _path_override
    with _THREAD_LOCK:
        _path_override = None
        _NEWLINE_TERMINATED.clear()
        _RECENT_EVENT_IDS.clear()
        _RECENT_EVENT_ID_SET.clear()
        _OCCURRENCE.clear()
        _STATS.update({
            "written": 0, "duplicates": 0, "failures": 0, "lockFailures": 0,
            "lastErrorType": None, "lastErrorAt": None, "lastEventAt": None,
            "lastEventId": None,
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
        result["path"] = str(path())
    return result


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
        # A dict without a message is still a raw fact; keep it and use a
        # complete JSON rendering as text rather than dropping the payload.
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
) -> str | None:
    """Append one sample.  Never raises; returns the eventId or None.

    ``previous_captured`` lets a caller pass an already-taken snapshot (or an
    explicit None snapshot) so later appends cannot change the answer.
    """
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

    line = (json.dumps(record, ensure_ascii=False, default=str,
                       separators=(",", ":")) + "\n").encode("utf-8")
    # Check + append + remember under one lock so two threads observing the
    # same logical event cannot both append it.
    with _THREAD_LOCK:
        if event_id in _RECENT_EVENT_ID_SET:
            _STATS["duplicates"] += 1
            return event_id
        try:
            _append_line(path(), line)
        except Exception as exc:  # never propagate into task/queue code
            _record_failure(exc)
            return None
        _remember_event_id(event_id)
        _STATS["written"] += 1
        _STATS["lastEventAt"] = timestamp
        _STATS["lastEventId"] = event_id
    return event_id


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
        with _cross_process_lock(lock_path):
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
        _NEWLINE_TERMINATED.add(str(destination))


def _ensure_newline(destination: Path) -> None:
    key = str(destination)
    if key in _NEWLINE_TERMINATED:
        return
    try:
        with open(destination, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size <= 0:
                return
            handle.seek(size - 1)
            if handle.read(1) == b"\n":
                _NEWLINE_TERMINATED.add(key)
                return
    except FileNotFoundError:
        return
    with open(destination, "ab") as handle:
        handle.write(b"\n")
        handle.flush()
    _NEWLINE_TERMINATED.add(key)


class _cross_process_lock:
    """Advisory lock shared by Pan's threads/processes for this one file.

    The lock is a sibling ``.lock`` file so the append handle can stay in
    pure O_APPEND mode.  Failure to acquire is reported but still attempts the
    append: losing one sample is preferable to blocking or failing a task.
    """

    def __init__(self, lock_path: Path):
        self._path = lock_path
        self._handle = None
        self._acquired = False

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._handle = self._path.open("a+b")
            self._handle.seek(0, os.SEEK_END)
            if self._handle.tell() == 0:
                self._handle.write(b"\0")
                self._handle.flush()
            self._handle.seek(0)
            # Bounded acquisition: an abandoned lock must not stall a worker
            # terminal path for msvcrt's default ~10s retry loop.
            deadline = time.monotonic() + 2.0
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self._handle.fileno(),
                                    fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
        except OSError as exc:
            with _THREAD_LOCK:
                _STATS["lockFailures"] += 1
            _LOG.warning("session-error-log: lock acquisition failed: %s", exc)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._handle is None:
            return False
        try:
            if self._acquired:
                self._handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    try:
                        msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
        finally:
            self._handle.close()
            self._handle = None
            self._acquired = False
        return False
