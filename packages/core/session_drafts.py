"""Durable composer drafts, one bounded sidecar file per Session.

The normal chat composer's unsent text is a *draft*: it belongs to the person
typing, not to the conversation, and losing it on reload is pure data loss.  It
is persisted here rather than on ``Session`` for three measured reasons:

* ``Session._save_body`` rewrites the metadata mirror and bumps ``updated_at``,
  which is the retention clock key (``data_retention`` deletes by
  ``updated_at``).  Persisting keystrokes there would keep every edited Session
  permanently "recently active" and defeat retention entirely.
* The metadata rewrite is gated on ``_meta_signature``, i.e. it serializes the
  whole metadata projection per write.  A draft is a small bounded blob; a
  Session save is not.
* ``Session._from_data`` ends in ``cls(**data)``, so a new key on disk without a
  matching constructor field breaks loading of every existing Session.

Layout mirrors the pin-state precedent (``data/session-pins.json``): its own
directory, its own lock, temp-file + ``os.replace`` atomic commit, a
``version``/``revision`` envelope, and an ``(mtime_ns, size, ino)``-stamped read
cache so a hot read is one ``stat`` and no parse.

One file per Session (not one shared map) is deliberate: a save is O(1) in the
number of Sessions, so no draft write ever rewrites unrelated drafts, and
deleting a Session is one unlink with no shared-file rewrite.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from packages.core.session import SESSION_DIR

# Beside, but separate from, Session metadata.  Never inside SESSION_DIR: the
# Session loader globs nothing, but retention and directory listings treat that
# folder as Session records.
DRAFT_DIR = SESSION_DIR.parent / "session-drafts"

_DRAFT_VERSION = 1
_DRAFT_LOCK = threading.RLock()

# Bounds.  A draft is one person's unsent message, not a document store.  These
# cap the write amplification of a paste-happy client and the read cost of a
# cold load; they are enforced on write so an oversized payload can never reach
# the disk.
MAX_DRAFT_TEXT = 100_000          # ~100 KB of UTF-8 text
MAX_DRAFT_PARTS = 2_000           # ComposerValue.parts entries
MAX_DRAFT_ATTACHMENTS = 64        # PendingAttachment entries
MAX_DRAFT_FIELD = 4_096           # any single string field (displayName, path…)
MAX_DRAFT_SERIALIZED = 1_000_000  # whole-payload ceiling, checked after encode

# Tombstones are how "the composer was cleared" stays distinguishable from
# "never had a draft".  Without them a cleared draft would be deleted, the
# revision counter would reset, and a slow in-flight save carrying the old
# (higher) revision would be accepted as a fresh write -- resurrecting text the
# user already sent.  Bounded by session count, since one tombstone replaces
# that Session's only draft file.
_EMPTY_DRAFT = {"text": "", "parts": [], "attachments": []}

# (path, (mtime_ns, size, ino)) -> parsed payload.  A write sets the stamp to
# None so the next read re-stats the file: an external Pan process may replace
# it after this write and a stamp we invented would hide that.
_CACHE: dict[str, tuple[Path, tuple[int, int, int], dict]] = {}


class DraftConflict(Exception):
    """The caller's base revision is stale; the current draft is attached."""

    def __init__(self, current: dict):
        super().__init__("Draft revision conflict")
        self.current = current


class DraftTooLarge(Exception):
    """The payload exceeds a documented bound and was not written."""


def _path(session_id: str) -> Path:
    return DRAFT_DIR / f"{session_id}.json"


def _safe_session_id(session_id: str) -> bool:
    """Reject ids that could escape DRAFT_DIR or address a non-Session file.

    Session ids are ``ses_`` + 16 hex chars, so this only ever rejects a
    hand-crafted request; the check exists so a bad id can never become a path
    traversal or a write outside the draft directory.
    """
    return bool(session_id) and not (
        session_id in {".", ".."}
        or any(c in session_id for c in "\\/:\0")
        or session_id.startswith(".")
    )


def _stamp(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino)


def _validate_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("draft text must be a string")
    if len(value) > MAX_DRAFT_TEXT:
        raise DraftTooLarge(f"draft text exceeds {MAX_DRAFT_TEXT} characters")
    return value


def _validate_parts(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("draft parts must be an array")
    if len(value) > MAX_DRAFT_PARTS:
        raise DraftTooLarge(f"draft exceeds {MAX_DRAFT_PARTS} parts")
    parts: list[dict] = []
    for part in value:
        if not isinstance(part, dict):
            raise ValueError("draft part must be an object")
        kind = part.get("type")
        # ComposerPart is a closed union: a text run, or an attachment
        # occurrence.  Rebuilding it field by field (instead of trusting the
        # payload) keeps unexpected keys out of the persisted draft.
        if kind == "text":
            parts.append({"type": "text", "value": _validate_text(part.get("value", ""))})
        elif kind == "attachment":
            attachment_id = part.get("attachmentId")
            if not isinstance(attachment_id, str) or not attachment_id:
                raise ValueError("attachment part requires attachmentId")
            if len(attachment_id) > MAX_DRAFT_FIELD:
                raise DraftTooLarge("attachmentId exceeds the field bound")
            entry: dict = {"type": "attachment", "attachmentId": attachment_id}
            # occurrenceId is the local occurrence identity; several
            # occurrences may share one attachmentId.  Both must round-trip
            # exactly or the restored inline structure reorders.
            occurrence_id = part.get("occurrenceId")
            if occurrence_id is not None:
                if not isinstance(occurrence_id, str) or not occurrence_id:
                    raise ValueError("occurrenceId must be a non-empty string")
                if len(occurrence_id) > MAX_DRAFT_FIELD:
                    raise DraftTooLarge("occurrenceId exceeds the field bound")
                entry["occurrenceId"] = occurrence_id
            parts.append(entry)
        else:
            raise ValueError(f"unsupported draft part type: {kind!r}")
    return parts


def _validate_field(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if len(value) > MAX_DRAFT_FIELD:
        raise DraftTooLarge(f"{name} exceeds the field bound")
    return value or None


def _validate_attachments(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("draft attachments must be an array")
    if len(value) > MAX_DRAFT_ATTACHMENTS:
        raise DraftTooLarge(f"draft exceeds {MAX_DRAFT_ATTACHMENTS} attachments")
    restored: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("draft attachment must be an object")
        occurrence_id = item.get("occurrenceId")
        if not isinstance(occurrence_id, str) or not occurrence_id:
            raise ValueError("draft attachment requires occurrenceId")
        if len(occurrence_id) > MAX_DRAFT_FIELD:
            raise DraftTooLarge("occurrenceId exceeds the field bound")
        entry = {
            "occurrenceId": occurrence_id,
            "displayName": _validate_field(item.get("displayName"), "displayName") or "",
        }
        # A File/Blob never reaches this function: the client refuses to send
        # bytes it cannot re-acquire, and the server has no field to put them
        # in.  ``attachmentId`` (already uploaded, server-owned) and ``path``
        # (a server file) are the only recoverable identities, so an attachment
        # carrying neither is dropped by the client rather than persisted as a
        # chip that would fail to resolve on reload.
        for key in ("attachmentId", "path", "href", "mimeType", "fileKey"):
            field = _validate_field(item.get(key), key)
            if field is not None:
                entry[key] = field
        location = item.get("location")
        if isinstance(location, dict):
            line = location.get("line")
            if isinstance(line, int) and not isinstance(line, bool) and line >= 1:
                entry["location"] = {"line": line}
                end_line = location.get("endLine")
                if isinstance(end_line, int) and not isinstance(end_line, bool) and end_line >= line:
                    entry["location"]["endLine"] = end_line
        if item.get("source") in {"upload", "server_file"}:
            entry["source"] = item["source"]
        restored.append(entry)
    return restored


def normalize_draft(draft: object) -> dict:
    """Validate one client draft into the exact shape that reaches disk.

    Rejecting (rather than truncating or coercing) is deliberate: a partially
    persisted draft would restore as a silently different message, which is
    worse than an honest failure the client can retry or report.
    """
    if not isinstance(draft, dict):
        raise ValueError("draft must be an object")
    normalized = {
        "text": _validate_text(draft.get("text", "")),
        "parts": _validate_parts(draft.get("parts", [])),
        "attachments": _validate_attachments(draft.get("attachments", [])),
    }
    encoded = json.dumps(normalized, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_DRAFT_SERIALIZED:
        raise DraftTooLarge("draft exceeds the serialized size bound")
    return normalized


def _read_locked(session_id: str) -> dict:
    """Return the authoritative envelope, or the absent-draft default."""
    path = _path(session_id)
    stamp = _stamp(path)
    cached = _CACHE.get(session_id)
    if cached is not None and cached[0] == path and cached[1] == stamp and stamp is not None:
        return cached[2]
    if stamp is None:
        return {"revision": 0, "draft": None, "updatedAt": None}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A missing or unreadable draft is a missing draft.  The composer
        # starts empty rather than blocking the UI on a damaged sidecar.
        return {"revision": 0, "draft": None, "updatedAt": None}
    if not isinstance(raw, dict) or raw.get("version") != _DRAFT_VERSION:
        return {"revision": 0, "draft": None, "updatedAt": None}
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return {"revision": 0, "draft": None, "updatedAt": None}
    draft = raw.get("draft")
    if draft is not None and not isinstance(draft, dict):
        draft = None
    payload = {
        "revision": revision,
        "draft": draft,
        "updatedAt": raw.get("updatedAt") if isinstance(raw.get("updatedAt"), str) else None,
    }
    _CACHE[session_id] = (path, stamp, payload)
    return payload


def _write_locked(session_id: str, revision: int, draft: dict | None) -> None:
    path = _path(session_id)
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = {
        "version": _DRAFT_VERSION,
        "revision": revision,
        "updatedAt": datetime.now().isoformat(),
        "draft": draft,
    }
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    # Drop the read cache instead of inventing a stamp for the new file: the
    # next reader re-stats and re-parses, so a replacement written by another
    # Pan process immediately after this commit is still observed.
    _CACHE.pop(session_id, None)


def read_draft(session_id: str) -> dict:
    """Return one Session's draft envelope for a cold composer load."""
    if not _safe_session_id(session_id):
        return {"revision": 0, "draft": None, "updatedAt": None}
    with _DRAFT_LOCK:
        return _read_locked(session_id)


def write_draft(session_id: str, base_revision: int | None, draft: dict | None) -> dict:
    """Compare-and-set one Session's draft and return the new envelope.

    ``base_revision`` is the revision the caller last observed.  ``None`` means
    "create only if absent" and is how a first-ever save asserts that no other
    client has written a draft it would overwrite.  A mismatch raises
    :class:`DraftConflict` carrying the current envelope, so the caller can
    merge or surface the conflict instead of silently winning on a stale read.
    """
    if not _safe_session_id(session_id):
        raise ValueError("invalid session id")
    normalized = None if draft is None else normalize_draft(draft)
    with _DRAFT_LOCK:
        current = _read_locked(session_id)
        current_revision = current["revision"]
        if base_revision is None:
            if current_revision != 0:
                raise DraftConflict(current)
        elif base_revision != current_revision:
            raise DraftConflict(current)
        revision = current_revision + 1
        _write_locked(session_id, revision, normalized)
        return {"revision": revision, "draft": normalized,
                "updatedAt": datetime.now().isoformat()}


def clear_draft(session_id: str, base_revision: int | None) -> dict:
    """Persist a tombstone so a cleared composer cannot be resurrected.

    The composer clearing (a successful send, or the user emptying the box) is
    itself versioned state.  Deleting the file instead would reset the revision
    to 0 and let any in-flight save that still carries the pre-clear revision be
    accepted as a create, restoring text the user already sent.
    """
    return write_draft(session_id, base_revision, None)


def delete_draft(session_id: str) -> None:
    """Remove one Session's draft file.  Missing is success."""
    if not _safe_session_id(session_id):
        return
    with _DRAFT_LOCK:
        _CACHE.pop(session_id, None)
        try:
            _path(session_id).unlink(missing_ok=True)
        except OSError:
            # Session deletion must not fail because a sidecar is undeletable;
            # an orphan draft is inert (its Session can never be selected).
            pass


def draft_diagnostics() -> dict:
    """Bounded counters for observability.  Never returns draft content."""
    now = time.time()
    with _DRAFT_LOCK:
        try:
            files = sum(1 for _ in DRAFT_DIR.iterdir() if _safe_session_id(p.stem))
        except OSError:
            files = 0
        return {"files": files, "cached": len(_CACHE), "observedAt": datetime.fromtimestamp(now).isoformat()}
