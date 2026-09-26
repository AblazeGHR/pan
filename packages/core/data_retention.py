"""Conservative, registered-root-only retention for Pan-owned user data.

The module deliberately does not discover roots. Callers pass only the fixed
Pan-owned roots registered by the application. Provider homes, config/auth,
external workdirs, QQ inbox, Jobs data and arbitrary ``data/**`` paths are not
retention targets.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import re
import stat
import threading
import time
from datetime import datetime
from typing import Callable
import uuid


POLICY_IDS = ("sessions", "attachments", "qq_history", "qq_media", "pan_logs")
DEFAULT_POLICIES = {
    "sessions": {"enabled": False, "days": None},
    "attachments": {"enabled": False, "days": None},
    "qq_history": {"enabled": False, "days": None},
    "qq_media": {"enabled": False, "days": None},
    "pan_logs": {"enabled": False, "days": None},
}
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 36500
MAX_FUTURE_SKEW_SEC = 0
_UPLOAD_NAME = re.compile(r"upload_[A-Za-z0-9]{32}(?:\.[A-Za-z0-9._-]{1,32})?\Z")
_QQ_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_QQ_FILE_LOCKS: dict[str, threading.RLock] = {}
_QQ_FILE_LOCKS_GUARD = threading.Lock()


def validate_policy_update(raw: object, current: dict | None = None) -> dict:
    """Validate and merge the Data-owned policy fields; reject extra fields."""
    if not isinstance(raw, dict) or set(raw) - {"policies"}:
        raise ValueError("body must contain only policies")
    policies = raw.get("policies")
    if not isinstance(policies, dict) or not policies or set(policies) - set(POLICY_IDS):
        raise ValueError("policies must contain supported Data categories only")
    merged = {key: dict((current or {}).get(key) or DEFAULT_POLICIES[key])
              for key in POLICY_IDS}
    for policy_id, value in policies.items():
        if not isinstance(value, dict) or set(value) != {"enabled", "days"}:
            raise ValueError(f"{policy_id} must contain enabled and days only")
        enabled = value.get("enabled")
        days = value.get("days")
        if not isinstance(enabled, bool):
            raise ValueError(f"{policy_id}.enabled must be a boolean")
        if (days is not None and (not isinstance(days, int) or isinstance(days, bool)
                or not MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS)):
            raise ValueError(
                f"{policy_id}.days must be null or an integer from {MIN_RETENTION_DAYS} "
                f"to {MAX_RETENTION_DAYS}"
            )
        merged[policy_id] = {"enabled": enabled, "days": days}
    return merged


def normalize_policies(raw: object) -> dict:
    """Read the supported policy subset; malformed values disable cleanup."""
    if not isinstance(raw, dict):
        return {key: dict(value) for key, value in DEFAULT_POLICIES.items()}
    result = {key: dict(value) for key, value in DEFAULT_POLICIES.items()}
    for key in POLICY_IDS:
        value = raw.get(key)
        days = value.get("days") if isinstance(value, dict) else object()
        if (isinstance(value, dict) and isinstance(value.get("enabled"), bool)
                and (days is None or (isinstance(days, int) and not isinstance(days, bool)
                     and MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS))):
            result[key] = {"enabled": value["enabled"], "days": days}
    return result


def _timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _plain_lstat(path: Path, *, directory: bool = False) -> os.stat_result | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE:
        return None
    if directory and not stat.S_ISDIR(info.st_mode):
        return None
    if not directory and not stat.S_ISREG(info.st_mode):
        return None
    if not directory and getattr(info, "st_nlink", 1) != 1:
        return None
    return info


def _safe_root(path: Path) -> Path | None:
    info = _plain_lstat(path, directory=True)
    if info is None:
        return None
    try:
        resolved = path.resolve(strict=True)
        lexical = Path(os.path.abspath(path))
    except OSError:
        return None
    return resolved if os.path.normcase(str(resolved)) == os.path.normcase(str(lexical)) else None


def _contained_plain_file(root: Path, candidate: Path) -> tuple[Path, os.stat_result] | None:
    info = _plain_lstat(candidate)
    if info is None:
        return None
    try:
        resolved = candidate.resolve(strict=True)
        resolved_root = root.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    if resolved == resolved_root:
        return None
    if os.path.normcase(str(resolved)) != os.path.normcase(str(Path(os.path.abspath(candidate)))):
        return None
    return resolved, info


def _iso_now(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def _cutoff(now_epoch: float, days: int) -> float:
    return now_epoch - days * 86400


def _summary(*, scanned: int = 0, deleted: int = 0, skipped: int = 0,
             reasons: dict[str, int] | None = None, error: str | None = None,
             completed_at: str | None = None) -> dict:
    return {
        "scanned": scanned,
        "deleted": deleted,
        "skipped": skipped,
        "skipReasons": dict(reasons or {}),
        "error": error,
        "completedAt": completed_at,
    }


def _skip(result: dict, reason: str, count: int = 1) -> None:
    result["skipped"] += count
    result["skipReasons"][reason] = result["skipReasons"].get(reason, 0) + count


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _history_activity(path: Path, main_record: dict) -> tuple[float | None, str | None]:
    """Return the newest reliable Session history timestamp or a skip reason."""
    rows: list[dict] = []
    try:
        path_info = path.lstat()
    except FileNotFoundError:
        path_info = None
    except OSError:
        return None, "history_unreadable"
    if path_info is not None:
        safe_path = _contained_plain_file(path.parent, path)
        if safe_path is None:
            return None, "history_path_unsafe"
        try:
            raw = safe_path[0].read_text(encoding="utf-8")
        except OSError:
            return None, "history_unreadable"
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                return None, "history_corrupt"
            if not isinstance(row, dict):
                return None, "history_corrupt"
            rows.append(row)
    tail = main_record.get("history", [])
    if not isinstance(tail, list) or any(not isinstance(row, dict) for row in tail):
        return None, "history_corrupt"
    rows.extend(tail)
    values: list[float] = []
    for row in rows:
        value = row.get("ts") or row.get("timestamp")
        timestamp = _timestamp(value)
        if timestamp is None:
            return None, "history_timestamp_unknown"
        values.append(timestamp)
    return (max(values) if values else None), None


def scan_sessions(
    root: Path,
    days: int,
    now_epoch: float,
    delete_session: Callable[[str, dict], dict],
) -> dict:
    result = _summary()
    root_resolved = _safe_root(root)
    if root_resolved is None:
        _skip(result, "session_root_missing_or_unsafe")
        return result
    cutoff = _cutoff(now_epoch, days)
    try:
        candidates = sorted(root.iterdir())
    except OSError:
        result["error"] = "session_root_unreadable"
        return result
    for path in candidates:
        if path.name.endswith(".history.jsonl") or path.suffix != ".json":
            continue
        result["scanned"] += 1
        safe = _contained_plain_file(root_resolved, path)
        if safe is None:
            _skip(result, "session_metadata_not_plain_or_contained")
            continue
        try:
            record = json.loads(safe[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _skip(result, "session_metadata_unreadable")
            continue
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            _skip(result, "session_metadata_invalid")
            continue
        session_id = record["id"]
        if path.stem != session_id or not session_id or any(c in session_id for c in "\\/:\0"):
            _skip(result, "session_id_path_mismatch")
            continue
        updated = _timestamp(record.get("updated_at") or record.get("updatedAt"))
        activity, history_error = _history_activity(root_resolved / f"{session_id}.history.jsonl", record)
        if history_error:
            _skip(result, history_error)
            continue
        if updated is None:
            _skip(result, "session_updated_at_unknown")
            continue
        reliable = [updated]
        if activity is not None:
            reliable.append(activity)
        if any(value > now_epoch + MAX_FUTURE_SKEW_SEC for value in reliable):
            _skip(result, "future_timestamp")
            continue
        if max(reliable) > cutoff:
            continue
        deletion_record = dict(record)
        deletion_record["__retentionHistoryActivity"] = activity
        outcome = delete_session(session_id, deletion_record)
        if isinstance(outcome, dict) and outcome.get("deleted") is True:
            result["deleted"] += 1
            if outcome.get("workdirSkipped"):
                reason = outcome.get("workdirSkipReason")
                _skip(result, reason if isinstance(reason, str) else "workdir_preserved_ownership_unclear")
            lifecycle_reasons = outcome.get("lifecycleSkipReasons")
            if isinstance(lifecycle_reasons, list):
                for reason in lifecycle_reasons:
                    if isinstance(reason, str) and re.fullmatch(r"[a-z0-9_:-]{1,80}", reason):
                        _skip(result, reason)
        else:
            reason = (outcome.get("reason") if isinstance(outcome, dict) else None) or "session_reference_unclear"
            _skip(result, str(reason))
    return result


@contextmanager
def cross_process_file_lock(path: Path):
    """Small advisory lock shared by QQ's writer process and Pan retention."""
    lock_key = os.path.normcase(str(Path(path).absolute()))
    with _QQ_FILE_LOCKS_GUARD:
        thread_lock = _QQ_FILE_LOCKS.setdefault(lock_key, threading.RLock())
    with thread_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            if _plain_lstat(path) is None:
                raise OSError("retention lock path is not a plain file")
        with path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _delete_owned_attachment(root: Path, root_resolved: Path, owner_dir: Path,
                             sidecar: Path, attachment_id: str,
                             expected_record: dict, cutoff: float,
                             now_epoch: float) -> tuple[bool, str | None]:
    current_root = _safe_root(root)
    if current_root is None or current_root != root_resolved:
        return False, "attachment_root_changed"
    sidecar_safe = _contained_plain_file(current_root, sidecar)
    if sidecar_safe is None:
        return False, "attachment_sidecar_changed"
    try:
        current = json.loads(sidecar_safe[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "attachment_sidecar_unreadable"
    if not isinstance(current, dict) or current.get(attachment_id) != expected_record:
        return False, "attachment_registry_changed"
    fresh = _contained_plain_file(current_root, owner_dir / str(expected_record.get("storageFilename") or ""))
    if fresh is None:
        return False, "attachment_file_missing_or_unsafe"
    created = _timestamp(expected_record.get("createdAt"))
    timestamp = created if created is not None and created <= now_epoch else fresh[1].st_mtime
    if timestamp > now_epoch:
        return False, "future_timestamp"
    if timestamp > cutoff:
        return False, "attachment_changed_during_scan"
    try:
        fresh[0].unlink()
    except OSError:
        return False, "attachment_delete_failed"
    current.pop(attachment_id, None)
    try:
        _atomic_json(sidecar_safe[0], current)
    except (OSError, TypeError):
        return True, "attachment_owner_sidecar_update_failed"
    return True, None


def scan_attachments(root: Path, days: int, now_epoch: float,
                     registry_lock: threading.RLock | None = None) -> dict:
    result = _summary()
    root_resolved = _safe_root(root)
    if root_resolved is None:
        _skip(result, "attachment_root_missing_or_unsafe")
        return result
    cutoff = _cutoff(now_epoch, days)
    try:
        directories = sorted(root_resolved.iterdir())
    except OSError:
        result["error"] = "attachment_root_unreadable"
        return result
    for directory in directories:
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        dir_info = _plain_lstat(directory, directory=True)
        if dir_info is None:
            _skip(result, "attachment_owner_directory_unsafe")
            continue
        try:
            owner_dir = directory.resolve(strict=True)
            owner_dir.relative_to(root_resolved)
        except (OSError, ValueError):
            _skip(result, "attachment_owner_directory_escape")
            continue
        sidecar = owner_dir / ".attachments.json"
        sidecar_safe = _contained_plain_file(root_resolved, sidecar)
        if sidecar_safe is None:
            continue
        try:
            registry = json.loads(sidecar_safe[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            result["scanned"] += 1
            _skip(result, "attachment_sidecar_unreadable")
            continue
        if not isinstance(registry, dict):
            result["scanned"] += 1
            _skip(result, "attachment_sidecar_invalid")
            continue
        for attachment_id, record in list(registry.items()):
            if not isinstance(record, dict) or record.get("source") != "upload":
                continue
            if record.get("sourceSessionId"):
                # Imported cross-Session receipts do not own bytes. The source
                # owner entry remains the only deletion authority.
                continue
            filename = record.get("storageFilename")
            if not isinstance(filename, str) or not _UPLOAD_NAME.fullmatch(filename):
                continue
            result["scanned"] += 1
            owner_id = record.get("sessionId")
            if not isinstance(owner_id, str) or attachment_id != filename:
                _skip(result, "attachment_owner_unclear")
                continue
            owner_hash = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:12]
            safe_owner = re.sub(r"[^A-Za-z0-9_.-]+", "_", owner_id).strip("._") or "session"
            if owner_dir.name != f"{safe_owner[:80]}-{owner_hash}":
                _skip(result, "attachment_owner_unclear")
                continue
            if record.get("completed") is not True or filename.startswith(".upload-") or filename.endswith((".tmp", ".part")):
                _skip(result, "attachment_incomplete")
                continue
            file_path = owner_dir / filename
            safe_file = _contained_plain_file(root_resolved, file_path)
            if safe_file is None:
                _skip(result, "attachment_file_missing_or_unsafe")
                continue
            created = _timestamp(record.get("createdAt"))
            timestamp = created if created is not None and created <= now_epoch else safe_file[1].st_mtime
            if timestamp > now_epoch:
                _skip(result, "future_timestamp")
                continue
            if timestamp > cutoff:
                continue
            if registry_lock:
                with registry_lock:
                    removed, error = _delete_owned_attachment(
                        root, root_resolved, owner_dir, sidecar, attachment_id,
                        record, cutoff, now_epoch,
                    )
            else:
                removed, error = _delete_owned_attachment(
                    root, root_resolved, owner_dir, sidecar, attachment_id,
                    record, cutoff, now_epoch,
                )
            if removed:
                result["deleted"] += 1
            if error:
                _skip(result, error)
    return result


def _walk_plain_files(root: Path, result: dict, *, max_depth: int):
    """Walk only the registered root; refuse links/reparse points at every level."""
    stack = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            entries = list(directory.iterdir())
        except OSError:
            _skip(result, "qq_root_unreadable")
            continue
        for entry in entries:
            if entry.name.startswith(".") and entry.name != ".retention.lock":
                _skip(result, "qq_hidden_path")
                continue
            info = _plain_lstat(entry, directory=True)
            if info is not None:
                try:
                    resolved = entry.resolve(strict=True)
                    resolved.relative_to(root)
                except (OSError, ValueError):
                    _skip(result, "qq_path_escape")
                    continue
                if depth >= max_depth:
                    _skip(result, "qq_unregistered_subdirectory")
                    continue
                stack.append((resolved, depth + 1))
                continue
            info = _plain_lstat(entry)
            if info is None:
                _skip(result, "qq_non_regular_or_reparse")
                continue
            candidate = _contained_plain_file(root, entry)
            if candidate is None:
                _skip(result, "qq_path_escape")
                continue
            yield candidate


def scan_qq_history(root: Path, days: int, now_epoch: float) -> dict:
    result = _summary()
    root_resolved = _safe_root(root)
    if root_resolved is None:
        _skip(result, "qq_history_root_missing_or_unsafe")
        return result
    cutoff = _cutoff(now_epoch, days)
    files = list(_walk_plain_files(root_resolved, result, max_depth=1))
    for path, info in files:
        if path.name == ".retention.lock":
            continue
        if path.suffix.lower() != ".json":
            continue
        if path.name.startswith("."):
            _skip(result, "qq_hidden_path")
            continue
        result["scanned"] += 1
        try:
            with cross_process_file_lock(root_resolved / ".retention.lock"):
                current_root = _safe_root(root)
                if current_root is None or current_root != root_resolved:
                    _skip(result, "qq_history_root_changed")
                    continue
                fresh = _contained_plain_file(current_root, path)
                if fresh is None:
                    _skip(result, "qq_history_file_changed_or_unsafe")
                    continue
                path = fresh[0]
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                    raise ValueError("history format")
                keep = []
                for row in data:
                    stamp = _timestamp_for_qq(row.get("time"))
                    if stamp is None or stamp > now_epoch:
                        raise ValueError("history timestamp")
                    if stamp > cutoff:
                        keep.append(row)
                if len(keep) == len(data):
                    continue
                temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
                try:
                    temporary.write_text(json.dumps(keep, ensure_ascii=False, indent=1) + "\n",
                                         encoding="utf-8")
                    os.replace(temporary, path)
                finally:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
                result["deleted"] += len(data) - len(keep)
        except ValueError:
            _skip(result, "qq_history_format_or_timestamp_unclear")
        except (OSError, json.JSONDecodeError):
            _skip(result, "qq_history_read_or_write_failed")
    return result


def _timestamp_for_qq(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, _QQ_TIME_FORMAT).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def scan_qq_media(root: Path, days: int, now_epoch: float) -> dict:
    result = _summary()
    root_resolved = _safe_root(root)
    if root_resolved is None:
        _skip(result, "qq_media_root_missing_or_unsafe")
        return result
    cutoff = _cutoff(now_epoch, days)
    for path, info in _walk_plain_files(root_resolved, result, max_depth=2):
        if path.name == ".retention.lock":
            continue
        result["scanned"] += 1
        if path.name.endswith((".part", ".tmp")) or path.name.startswith("."):
            _skip(result, "qq_media_incomplete_or_hidden")
            continue
        current_root = _safe_root(root)
        fresh = (_contained_plain_file(current_root, path)
                 if current_root is not None and current_root == root_resolved else None)
        if fresh is None:
            _skip(result, "qq_media_file_changed_or_unsafe")
            continue
        path, info = fresh
        if info.st_mtime > now_epoch:
            _skip(result, "future_timestamp")
            continue
        if info.st_mtime > cutoff:
            continue
        try:
            path.unlink()
            result["deleted"] += 1
        except OSError:
            _skip(result, "qq_media_delete_failed")
    return result


def scan_pan_logs(logs_root: Path, active_log: Path, days: int,
                  now_epoch: float) -> dict:
    """Remove expired rotated Pan logs from the fixed data/logs root.

    The process-owned active file can still receive writes, so it is reported
    as skipped. Only exact basename siblings (the logger's numbered and date
    rotation forms) are considered; configured paths outside this root are
    never followed or cleaned.
    """
    result = _summary()
    safe_root = _safe_root(logs_root)
    if safe_root is None:
        _skip(result, "pan_logs_root_missing_or_unsafe")
        return result
    active = Path(active_log)
    try:
        active_absolute = Path(os.path.abspath(active))
        active_resolved = active.resolve(strict=False)
        active_resolved.relative_to(safe_root)
    except (OSError, ValueError):
        _skip(result, "pan_log_external_or_unregistered")
        return result
    if (active_absolute.parent != safe_root or active_resolved.parent != safe_root
            or active_resolved.name != active_absolute.name):
        _skip(result, "pan_log_external_or_unregistered")
        return result
    cutoff = _cutoff(now_epoch, days)
    try:
        candidates = sorted(safe_root.iterdir())
    except OSError:
        result["error"] = "pan_logs_root_unreadable"
        return result
    active_name = active_absolute.name
    rotated_name = re.compile(re.escape(active_name) + r"\.(?:\d+|\d{8})\Z")
    active_path = safe_root / active_name
    try:
        active_path.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        result["scanned"] += 1
        _skip(result, "active_log_file_unavailable")
    else:
        result["scanned"] += 1
        if _contained_plain_file(safe_root, active_path) is None:
            _skip(result, "active_log_file_not_plain")
        else:
            _skip(result, "active_log_file")
    for path in candidates:
        if path.name == active_name:
            continue
        if not rotated_name.fullmatch(path.name):
            continue
        result["scanned"] += 1
        safe = _contained_plain_file(safe_root, path)
        if safe is None:
            _skip(result, "pan_rotated_log_not_plain_or_contained")
            continue
        modified = safe[1].st_mtime
        if modified > now_epoch:
            _skip(result, "future_timestamp")
            continue
        if modified > cutoff:
            continue
        # Recheck root and file identity immediately before unlinking.
        if _safe_root(logs_root) != safe_root or _contained_plain_file(safe_root, path) is None:
            _skip(result, "pan_rotated_log_changed_or_unsafe")
            continue
        try:
            safe[0].unlink()
            result["deleted"] += 1
        except OSError:
            _skip(result, "pan_rotated_log_delete_failed")
    return result


class DataRetentionService:
    """Daily bounded runner with persisted per-policy result counters."""

    def __init__(self, *, sessions_root: Path, attachments_root: Path,
                 qq_history_root: Path, qq_media_root: Path, status_path: Path,
                 policy_loader: Callable[[], object],
                 session_delete: Callable[[str, dict], dict],
                 attachment_registry_lock: threading.RLock | None = None,
                 pan_logs_root: Path | None = None,
                 active_log_path: Callable[[], Path] | None = None):
        self.sessions_root = Path(sessions_root)
        self.attachments_root = Path(attachments_root)
        self.qq_history_root = Path(qq_history_root)
        self.qq_media_root = Path(qq_media_root)
        self.status_path = Path(status_path)
        self.pan_logs_root = Path(pan_logs_root) if pan_logs_root is not None else None
        self.active_log_path = active_log_path or (lambda: self.pan_logs_root / "pan.log")
        self.policy_loader = policy_loader
        self.session_delete = session_delete
        self.attachment_registry_lock = attachment_registry_lock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status_lock = threading.RLock()
        self._run_lock = threading.Lock()

    def _load_status(self) -> dict:
        status_root = self._status_root(create=False)
        if status_root is None:
            return {}
        safe_status = _contained_plain_file(status_root, self.status_path)
        if safe_status is None:
            return {}
        try:
            raw = json.loads(safe_status[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        clean = {}
        for policy_id in POLICY_IDS:
            item = raw.get(policy_id)
            if not isinstance(item, dict):
                continue
            counts = {}
            for name in ("scanned", "deleted", "skipped"):
                value = item.get(name)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    break
                counts[name] = min(value, 2**53 - 1)
            if len(counts) != 3:
                continue
            raw_reasons = item.get("skipReasons")
            reasons = {}
            if isinstance(raw_reasons, dict):
                for reason, value in raw_reasons.items():
                    if (isinstance(reason, str) and re.fullmatch(r"[a-z0-9_:-]{1,80}", reason)
                            and isinstance(value, int) and not isinstance(value, bool) and value >= 0):
                        reasons[reason] = min(value, 2**53 - 1)
            clean[policy_id] = {
                **counts,
                "skipReasons": reasons,
                "error": item.get("error") if isinstance(item.get("error"), str)
                and re.fullmatch(r"[a-zA-Z0-9_:-]{1,100}", item["error"]) else None,
                "completedAt": self._safe_status_time(item.get("completedAt")),
                "lastScanAt": self._safe_status_time(item.get("lastScanAt")),
            }
        return clean

    @staticmethod
    def _safe_status_time(value: object) -> str | None:
        timestamp = _timestamp(value)
        if not isinstance(value, str) or timestamp is None or timestamp > time.time():
            return None
        return value[:64]

    def _status_root(self, *, create: bool) -> Path | None:
        """Confine status IO to data/retention without following aliases."""
        if self.status_path.name != "status.json" or self.status_path.parent.name != "retention":
            return None
        data_root = self.status_path.parent.parent
        project_root = data_root.parent
        safe_project = _safe_root(project_root)
        if safe_project is None:
            return None
        expected_data = safe_project / "data"
        if os.path.normcase(str(data_root.absolute())) != os.path.normcase(str(expected_data)):
            return None
        if not data_root.exists() and create:
            try:
                data_root.mkdir()
            except OSError:
                return None
        safe_data = _safe_root(data_root)
        if safe_data is None:
            return None
        expected_retention = safe_data / "retention"
        if not expected_retention.exists() and create:
            try:
                expected_retention.mkdir()
            except OSError:
                return None
        safe_retention = _safe_root(expected_retention)
        if safe_retention is None:
            return None
        return safe_retention

    def _save_status(self, status: dict) -> None:
        try:
            status_root = self._status_root(create=True)
            if status_root is None:
                return
            if self.status_path.exists() or self.status_path.is_symlink():
                if _contained_plain_file(status_root, self.status_path) is None:
                    return
            _atomic_json(self.status_path, status)
        except OSError:
            # A failed diagnostics write must never stop Worker/Scheduler.
            pass

    def get_status(self) -> dict:
        with self._status_lock:
            status = self._load_status()
        return {
            key: status.get(key, {"lastScanAt": None, **_summary()})
            for key in POLICY_IDS
        }

    def run_once(self, *, now_epoch: float | None = None, force: bool = False) -> dict:
        current = time.time() if now_epoch is None else float(now_epoch)
        policies = normalize_policies(self.policy_loader())
        now_iso = _iso_now(current)
        with self._run_lock:
            with self._status_lock:
                status = self._load_status()
            for policy_id in POLICY_IDS:
                policy = policies[policy_id]
                if not policy["enabled"] or policy["days"] is None:
                    continue
                prior = status.get(policy_id) if isinstance(status.get(policy_id), dict) else {}
                prior_epoch = _timestamp(prior.get("lastScanAt"))
                if (not force and prior_epoch is not None and prior_epoch <= current
                        and current - prior_epoch < 86400):
                    continue
                try:
                    scanner = {
                        "sessions": lambda: scan_sessions(
                            self.sessions_root, policy["days"], current, self.session_delete),
                        "attachments": lambda: scan_attachments(
                            self.attachments_root, policy["days"], current,
                            self.attachment_registry_lock),
                        "qq_history": lambda: scan_qq_history(
                            self.qq_history_root, policy["days"], current),
                        "qq_media": lambda: scan_qq_media(
                            self.qq_media_root, policy["days"], current),
                        "pan_logs": lambda: scan_pan_logs(
                            self.pan_logs_root, self.active_log_path(), policy["days"], current),
                    }[policy_id]
                    result = scanner()
                except Exception as exc:  # Sweep failures are isolated per category.
                    result = _summary(error=f"scan_failed:{type(exc).__name__}")
                result["lastScanAt"] = now_iso
                with self._status_lock:
                    status[policy_id] = result
                    self._save_status(status)
            with self._status_lock:
                latest = self._load_status()
            return {key: latest.get(key, {"lastScanAt": None, **_summary()})
                    for key in POLICY_IDS}

    def start(self, *, interval_sec: int = 60) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.run_once()
                except Exception:
                    # Last-resort isolation for startup/lifespan resilience.
                    pass
                self._stop.wait(max(1, interval_sec))

        self._thread = threading.Thread(target=loop, name="pan-data-retention", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 20.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._thread = None
