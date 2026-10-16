"""Durable message bookmarks, one bounded sidecar file per Session.

A bookmark marks one conversation block (one history row) for later return.
It is persisted here rather than on ``Session`` for the same measured reasons
as composer drafts (``session_drafts.py``):

* ``Session._save_body`` rewrites the metadata mirror and bumps ``updated_at``,
  the retention clock key; bookmarking must not keep a Session "recently
  active".
* A Session save serializes the whole metadata projection; a bookmark is a
  small bounded blob.
* ``Session._from_data`` ends in ``cls(**data)``, so a new on-disk key would
  break loading of every existing Session.

Identity contract (the whole point of this module):

* A bookmark key is one durable Pan message identity, ``pan:<uuid>``, exactly
  as assigned by ``session.append_history`` / ``assign_pan_message_ids`` /
  ``history_identity_backfill``.  One history row is one body block.
* Positional wire identities (``legacy:<session>:<epoch>:<index>``), legacy
  ``msg_`` ids, provider ``nativeItemId`` values, message text and array
  offsets are never accepted: they are either epoch-bound, provider-owned or
  forgeable.  The HTTP layer resolves the key against the target Session's
  own history before any write, so a bookmark can never address another
  Session's block or an arbitrary invented id.
* Bookmarks are per-Session by construction (one sidecar file per Session)
  and never shared, merged or inferred across Sessions.
* Bookmarked content is user messages and assistant text blocks.  Tool and
  thinking rows resolve (their identities are durable) but are rejected as
  targets: they are conversation machinery, not bookmarked content.

Durability and concurrency:

* One file per Session, temp-file + ``os.replace`` atomic commit, a
  ``version``/``revision`` envelope and an ``(mtime_ns, size, ino)``-stamped
  read cache, mirroring the pin-state/draft precedents.
* Cross-process exclusion is the advisory file lock ``data_retention``
  already uses; the read-compare-write runs inside it.  ``os.replace`` makes
  each commit atomic but cannot make the sequence atomic, and a
  ``threading.Lock`` cannot see another process at all.
* An optional ``baseRevision`` gives compare-and-set semantics for dual
  clients; a stale base is rejected with the current envelope attached.
  Add/remove are idempotent set/clear operations: repeating the desired
  state is a successful no-op that neither writes nor bumps the revision.
* A file that exists but cannot be parsed is ``BookmarkUnreadable``, never
  "empty": reporting damage as absence is what would let the next write
  destroy bookmarks the file still holds.  Only the projection path degrades
  to "no flags" (history must keep serving); every mutation and the read
  endpoint surface the error instead.
* Bookmarks whose target row no longer exists (an ambiguous reimport re-id)
  are kept on disk, excluded from projection and reported as ``dangling``.
  They are never silently dropped and never re-matched by content.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from packages.core.data_retention import cross_process_file_lock
from packages.core import session as sessions

log = logging.getLogger(__name__)

# Beside, but separate from, Session metadata: the Session loader, retention
# and directory listings treat ``data/sessions`` as Session records only.
# Derived per call (not frozen at import) so the store follows the Session
# store's actual location, including test/relocated roots.
def _bookmark_dir() -> Path:
    return sessions.SESSION_DIR.parent / "session-bookmarks"

_BOOKMARK_VERSION = 1
# Serializes threads within this process.  Cross-process exclusion is the
# advisory file lock below; both are needed because the file lock is held
# across a read and a write, and the thread lock keeps same-process callers
# from queueing on the OS lock one at a time.  Order is always thread lock
# then file lock, never the reverse.
_BOOKMARK_LOCK = threading.RLock()
# One lock file for the whole store.  A single file keeps the on-disk story
# simple; transactions touch only one bounded sidecar.
_LOCK_NAME = ".bookmarks.lock"

# A bookmark is a marker, not a document store.  The bound caps write
# amplification and the response size of a paste-happy client; it is
# enforced on write so an oversized set can never reach the disk.
MAX_BOOKMARKS_PER_SESSION = 5000

# (path, (mtime_ns, size, ino)) -> parsed envelope.  A write drops the entry
# instead of inventing a stamp: the next reader re-stats, so a replacement
# written by another Pan process immediately after a commit is observed.
_CACHE: dict[str, tuple[Path, tuple[int, int, int], dict]] = {}

_ABSENT: dict = {"revision": 0, "bookmarks": {}, "updatedAt": None, "state": "absent"}


class BookmarkSessionMissing(Exception):
    """The Session no longer exists at the bookmark transaction boundary."""


class BookmarkUnreadable(Exception):
    """A bookmark file exists but could not be read as a valid envelope.

    Distinct from "absent".  Reporting damaged data as an empty bookmark set
    is indistinguishable from a Session that never had bookmarks, which would
    let the next write destroy the entries the file still holds.
    """


class BookmarkConflict(Exception):
    """The caller's base revision is stale; the current envelope is attached."""

    def __init__(self, current: dict):
        super().__init__("Bookmark revision conflict")
        self.current = current


class BookmarkTooMany(Exception):
    """The Session already holds the documented maximum number of bookmarks."""


class BookmarkTargetUnresolved(Exception):
    """The requested message identity is not a verifiable bookmark target.

    ``code`` is ``invalid_message_id`` (not a durable Pan identity),
    ``message_not_found`` (well-formed but absent or ambiguous in this
    Session's history) or ``message_not_bookmarkable`` (resolves, but the row
    is not bookmarked content — tool/thinking/system machinery).
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require_session(session_id: str) -> None:
    # Disk is authoritative here; Session's process-local cache may outlive a
    # deletion in another process. Called only under the bookmark lock.
    if not _safe_session_id(session_id) or not sessions._path(session_id).is_file():
        raise BookmarkSessionMissing("Session does not exist")


def _path(session_id: str) -> Path:
    return _bookmark_dir() / f"{session_id}.json"


def _safe_session_id(session_id: str) -> bool:
    """Reject ids that could escape BOOKMARK_DIR or address a non-Session file.

    Session ids are ``ses_`` + 16 hex chars, so this only ever rejects a
    hand-crafted request; the check exists so a bad id can never become a
    path traversal or a write outside the bookmark directory.
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


def _validate_bookmark_entry(key: str, value: object) -> dict:
    """Validate one stored entry into the exact shape that reaches disk.

    Rejecting (rather than truncating or coercing) is deliberate: a partially
    trusted bookmark set would project onto the wrong blocks.
    """
    if not sessions.is_pan_message_id(key):
        raise ValueError(f"bookmark key is not a Pan message id: {key!r}")
    if not isinstance(value, dict):
        raise ValueError("bookmark entry must be an object")
    if set(value) != {"messageId", "createdAt"}:
        raise ValueError("bookmark entry has unexpected fields")
    if value.get("messageId") != key:
        raise ValueError("bookmark entry messageId does not match its key")
    created_at = value.get("createdAt")
    if not isinstance(created_at, str) or not created_at:
        raise ValueError("bookmark entry requires a createdAt timestamp")
    return {"messageId": key, "createdAt": created_at}


def _validate_bookmarks(value: object) -> dict[str, dict]:
    if not isinstance(value, dict):
        raise ValueError("bookmarks must be an object")
    if len(value) > MAX_BOOKMARKS_PER_SESSION:
        raise ValueError(f"bookmarks exceed the {MAX_BOOKMARKS_PER_SESSION} bound")
    return {key: _validate_bookmark_entry(key, entry) for key, entry in value.items()}


def _read_locked(session_id: str) -> dict:
    """Return the authoritative envelope, the absent default, or raise.

    The returned envelope always carries ``state``: ``"absent"``,
    ``"present"``.  Only a genuinely missing file is absent; anything else
    raises :class:`BookmarkUnreadable` so no caller can mistake damaged data
    for no data and write over it.
    """
    path = _path(session_id)
    try:
        stamp = _stamp(path)
    except OSError as exc:
        raise BookmarkUnreadable(f"Bookmarks for {session_id} could not be inspected") from exc
    cached = _CACHE.get(session_id)
    if cached is not None and cached[0] == path and cached[1] == stamp and stamp is not None:
        return cached[2]
    if stamp is None:
        # Genuinely absent.  Distinguishing this from "exists but unreadable"
        # is the whole point: only absence may be treated as an empty set.
        return dict(_ABSENT)
    # The compact write ceiling plus pretty-print/envelope overhead is below
    # this bound. Refuse enormous damaged files before allocating/parsing them.
    if stamp[1] > MAX_BOOKMARKS_PER_SESSION * 512:
        _CACHE.pop(session_id, None)
        raise BookmarkUnreadable(f"Bookmarks for {session_id} exceed the file size bound")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(_ABSENT)          # deleted between stat and read
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        _CACHE.pop(session_id, None)
        raise BookmarkUnreadable(
            f"Bookmarks for {session_id} exist but could not be read: "
            f"{type(exc).__name__ if isinstance(exc, OSError) else 'invalid JSON or encoding'}"
        ) from exc
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw.get("version") != _BOOKMARK_VERSION:
        _CACHE.pop(session_id, None)
        raise BookmarkUnreadable(f"Bookmarks for {session_id} have an unsupported format")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        _CACHE.pop(session_id, None)
        raise BookmarkUnreadable(f"Bookmarks for {session_id} have an invalid revision")
    try:
        bookmarks = _validate_bookmarks(raw.get("bookmarks"))
    except ValueError as exc:
        _CACHE.pop(session_id, None)
        raise BookmarkUnreadable(f"Bookmarks for {session_id} have an invalid payload: {exc}") from exc
    payload = {
        "revision": revision,
        "bookmarks": bookmarks,
        "updatedAt": raw.get("updatedAt") if isinstance(raw.get("updatedAt"), str) else None,
        "state": "present",
    }
    _CACHE[session_id] = (path, stamp, payload)
    return payload


@contextmanager
def _exclusive():
    """Hold both the in-process lock and the cross-process advisory lock.

    Order is always thread lock then file lock.  ``cross_process_file_lock``
    blocks, so a caller that held only the file lock and then asked for the
    thread lock could deadlock against a caller that holds them in this
    order; never take them the other way round.  The lock context manager
    creates the lock file's parent directory itself.
    """
    with _BOOKMARK_LOCK:
        with cross_process_file_lock(_bookmark_dir() / _LOCK_NAME):
            yield


def _write_locked(session_id: str, revision: int, bookmarks: dict[str, dict]) -> None:
    path = _path(session_id)
    _bookmark_dir().mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = {
        "version": _BOOKMARK_VERSION,
        "revision": revision,
        "updatedAt": datetime.now().isoformat(),
        "bookmarks": bookmarks,
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


def read_bookmarks(session_id: str) -> dict:
    """Return one Session's bookmark envelope for a cold client load.

    Raises :class:`BookmarkUnreadable` when a file exists but cannot be read,
    so the HTTP layer can report "damaged" instead of "empty".
    """
    if not _safe_session_id(session_id):
        raise ValueError("invalid session id")
    with _exclusive():
        _require_session(session_id)
        return _read_locked(session_id)


def _public_envelope(payload: dict) -> dict:
    return {
        "revision": payload["revision"],
        "bookmarks": payload["bookmarks"],
        "updatedAt": payload["updatedAt"],
        "state": payload["state"],
    }


def _cas_locked(session_id: str, base_revision) -> dict:
    """Read under the lock and enforce the optional compare-and-set base."""
    current = _read_locked(session_id)   # raises BookmarkUnreadable, never overwrites
    if base_revision is not None:
        if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 0:
            raise ValueError("base revision must be null or a non-negative integer")
        if base_revision != current["revision"]:
            raise BookmarkConflict(_public_envelope(current))
    return current


def add_bookmark(session_id: str, message_id: str, base_revision=None) -> dict:
    """Bookmark one verifiable message and return the new envelope.

    Idempotent: bookmarking an already-bookmarked message writes nothing and
    keeps the revision.  ``base_revision`` (optional) must equal the revision
    the caller last read; a mismatch raises :class:`BookmarkConflict` with
    the current envelope attached so the caller can re-read instead of
    overwriting on a stale base.
    """
    target = resolve_bookmark_target(session_id, message_id)
    with _exclusive():
        _require_session(session_id)
        current = _cas_locked(session_id, base_revision)
        bookmarks = dict(current["bookmarks"])
        changed = message_id not in bookmarks
        if changed:
            if len(bookmarks) >= MAX_BOOKMARKS_PER_SESSION:
                raise BookmarkTooMany(
                    f"Session already holds {MAX_BOOKMARKS_PER_SESSION} bookmarks")
            bookmarks[message_id] = {
                "messageId": message_id,
                "createdAt": datetime.now().isoformat(),
            }
            _write_locked(session_id, current["revision"] + 1, bookmarks)
            current = _read_locked(session_id)
    return {**_public_envelope(current), "changed": changed, "target": target}


def remove_bookmark(session_id: str, message_id: str, base_revision=None) -> dict:
    """Remove one bookmark and return the new envelope.

    Idempotent: removing an absent bookmark writes nothing and keeps the
    revision.  The optional ``baseRevision`` compare-and-set matches
    :func:`add_bookmark`.

    Unlike :func:`add_bookmark` this does not re-resolve the target: a
    bookmark whose row no longer exists (an ambiguous reimport re-id) must
    stay removable so the user can clean it up, and removing a key from this
    Session's own store can never address another Session's data.
    """
    if not isinstance(message_id, str) or not sessions.is_pan_message_id(message_id):
        raise BookmarkTargetUnresolved(
            "invalid_message_id",
            "Bookmark targets must be durable Pan message ids (pan:...)",
        )
    with _exclusive():
        _require_session(session_id)
        current = _cas_locked(session_id, base_revision)
        bookmarks = dict(current["bookmarks"])
        changed = message_id in bookmarks
        if changed:
            bookmarks.pop(message_id, None)
            _write_locked(session_id, current["revision"] + 1, bookmarks)
            current = _read_locked(session_id)
    return {**_public_envelope(current), "changed": changed, "target": None}


def bookmarked_message_ids(session_id: str) -> frozenset[str]:
    """Return the Session's bookmarked message ids for history projection.

    Never raises into the history serving path: a missing Session projects
    no flags, and an unreadable sidecar degrades to no flags with a log line
    while the read/mutation endpoints keep surfacing the damage.  Projecting
    a stale flag set is bounded by the stamped read cache and the next
    request; silently dropping the file is not an option anywhere.
    """
    if not _safe_session_id(session_id):
        return frozenset()
    try:
        # Fast path: no sidecar file means no bookmarks, ever.  Creation
        # goes through the locked write that drops the read cache, so a
        # racing creation can at worst miss one projection render; the
        # write's own response and broadcast carry the truth.
        if _stamp(_path(session_id)) is None:
            return frozenset()
    except OSError:
        return frozenset()
    try:
        with _exclusive():
            payload = _read_locked(session_id)
    except BookmarkUnreadable:
        log.warning("[bookmarks] unreadable sidecar for %s; projecting no flags", session_id)
        return frozenset()
    except (BookmarkSessionMissing, OSError):
        return frozenset()
    except Exception as exc:  # noqa: BLE001 - projection must not break history
        log.warning("[bookmarks] projection read failed for %s: %s", session_id, type(exc).__name__)
        return frozenset()
    return frozenset(payload["bookmarks"])


def resolve_bookmark_target(session_id: str, message_id: str) -> dict:
    """Prove that ``message_id`` is one bookmarkable row of this Session.

    Accepts only durable Pan identities.  Resolution covers blocks that
    pagination has not loaded: a hydrated Session is scanned in memory (the
    authoritative view, including unsaved worker appends); otherwise the
    companion JSONL is streamed row by row with bounded memory; a legacy
    Session without a JSONL falls back to its main-file history.  The match
    must be unique and a searchable body row (user/assistant/tool/thinking
    with non-empty content) — the same rows that receive Pan identities.
    Anything else is an honest failure, never a content-based guess.
    """
    if not isinstance(message_id, str) or not sessions.is_pan_message_id(message_id):
        raise BookmarkTargetUnresolved(
            "invalid_message_id",
            "Bookmark targets must be durable Pan message ids (pan:...)",
        )
    if not _safe_session_id(session_id):
        raise BookmarkSessionMissing("Session does not exist")

    cached = sessions.get_cached(session_id)
    if cached is not None and getattr(cached, "_history_loaded", True):
        matches = [
            row for row in cached.history
            if isinstance(row, dict) and row.get("messageId") == message_id
        ]
        source = "memory"
    else:
        history_path = sessions._history_path(session_id)
        if history_path.exists():
            matches = []
            try:
                with history_path.open("rb") as handle:
                    for raw_line in handle:
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            value = json.loads(line.decode("utf-8"))
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            # A crash-tail half-line is not a history row.
                            continue
                        if isinstance(value, dict) and value.get("messageId") == message_id:
                            matches.append(value)
            except OSError as exc:
                raise BookmarkTargetUnresolved(
                    "message_not_found",
                    "Session history could not be read",
                ) from exc
            source = "jsonl"
        else:
            path = sessions._path(session_id)
            if not path.is_file():
                raise BookmarkSessionMissing("Session does not exist")
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise BookmarkTargetUnresolved(
                    "message_not_found", "Session history could not be read") from exc
            rows = data.get("history")
            matches = [
                row for row in (rows if isinstance(rows, list) else [])
                if isinstance(row, dict) and row.get("messageId") == message_id
            ]
            source = "main"
    if len(matches) != 1:
        # Zero matches: the block is not in this Session (never loaded,
        # replaced, or belongs to another Session).  More than one: the
        # history is damaged; refusing is the only non-guessing answer.
        raise BookmarkTargetUnresolved(
            "message_not_found",
            "Message not found in this session's history"
            if not matches else "Message identity is ambiguous in this session's history",
        )
    row = matches[0]
    # Bookmarked content is user messages and assistant text blocks.  Thinking
    # and tool rows are conversation machinery, not bookmarked content: their
    # identities stay durable and resolvable, but they are not targets, so the
    # API rejects them instead of letting the UI imply otherwise.
    if (not sessions._is_searchable_history_body(row)
            or row.get("role") not in ("user", "assistant")):
        raise BookmarkTargetUnresolved(
            "message_not_bookmarkable",
            "Only user messages and assistant text blocks can be bookmarked",
        )
    return {
        "messageId": message_id,
        "role": row.get("role"),
        "source": source,
    }


def dangling_bookmarks(session_id: str) -> list[str]:
    """Return bookmarked ids that no longer resolve in the Session's history.

    Kept on disk, excluded from projection and reported here: an ambiguous
    reimport re-id must not be silently dropped (the user bookmarked that
    block) and must not be re-matched by content (that would be a guess).
    """
    if not _safe_session_id(session_id):
        return []
    try:
        with _exclusive():
            payload = _read_locked(session_id)
    except (BookmarkUnreadable, BookmarkSessionMissing, OSError):
        return []
    if payload["state"] != "present" or not payload["bookmarks"]:
        return []
    cached = sessions.get_cached(session_id)
    present: set[str] = set()
    if cached is not None and getattr(cached, "_history_loaded", True):
        present = {
            row.get("messageId") for row in cached.history
            if isinstance(row, dict) and sessions.is_pan_message_id(row.get("messageId"))
        }
    else:
        history_path = sessions._history_path(session_id)
        if history_path.exists():
            try:
                with history_path.open("rb") as handle:
                    for raw_line in handle:
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            value = json.loads(line.decode("utf-8"))
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        if isinstance(value, dict) and sessions.is_pan_message_id(value.get("messageId")):
                            present.add(value["messageId"])
            except OSError:
                return []
        else:
            path = sessions._path(session_id)
            if not path.is_file():
                return []
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return []
            rows = data.get("history")
            present = {
                row.get("messageId") for row in (rows if isinstance(rows, list) else [])
                if isinstance(row, dict) and sessions.is_pan_message_id(row.get("messageId"))
            }
    return sorted(key for key in payload["bookmarks"] if key not in present)


@contextmanager
def session_deletion(session_id: str):
    """Serialize Session metadata removal and bookmark cleanup against writes.

    A write either commits before deletion (then gets removed), or enters
    after metadata removal and fails _require_session. Mutual exclusion of
    the sidecar unlink alone cannot provide this guarantee.
    """
    with _exclusive():
        yield
        _delete_locked(session_id)


def _delete_locked(session_id: str) -> None:
    _CACHE.pop(session_id, None)
    try:
        _path(session_id).unlink(missing_ok=True)
    except OSError:
        # If cleanup fails, reads/writes still reject the missing Session.
        pass


def delete_bookmarks(session_id: str) -> None:
    """Remove one Session's bookmark file.  Missing is success.

    Takes the same cross-process lock as the write path. This alone is not a
    Session lifecycle boundary: Session.delete uses ``session_deletion`` to
    hold the same lock while removing its metadata too.
    """
    if not _safe_session_id(session_id):
        return
    with _exclusive():
        _delete_locked(session_id)


def bookmark_diagnostics() -> dict:
    """Bounded counters for observability.  Never returns bookmark content."""
    with _BOOKMARK_LOCK:
        try:
            files = sum(1 for _ in _bookmark_dir().iterdir() if _safe_session_id(p.stem))
        except OSError:
            files = 0
        return {"files": files, "cached": len(_CACHE)}
