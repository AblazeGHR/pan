"""Instance-level persistence for account-scoped Codex quota.

The quota belongs to the Codex authentication profile, not to a Pan Session or
Worker.  This module deliberately stores only provider quota snapshots and
observation metadata.  Credentials are read by the optional WHAM provider but
are never returned or persisted here.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .codex_quota import merge_normalized_quota, normalize_codex_rate_limits


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUOTA_ROOT = PROJECT_ROOT / "data" / "codex" / "quota"
QUOTA_SCHEMA_VERSION = 1
_PROCESS_LOCK = threading.RLock()


@dataclass(frozen=True)
class CodexProfile:
    """Non-secret identity used to select one Codex account cache."""

    home: Path
    account_id: str | None
    chatgpt_account_id: str | None
    profile_key: str


@dataclass(frozen=True)
class CodexCredentials:
    """Short-lived in-memory credentials; never serialize this object."""

    access_token: str
    account_id: str | None = None
    chatgpt_account_id: str | None = None


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return (Path(configured).expanduser() if configured else Path.home() / ".codex").resolve()


def _auth_path(home: Path) -> Path:
    return home / "auth.json"


def _read_auth_object(home: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(_auth_path(home).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _auth_fields(value: dict[str, Any] | None) -> tuple[str | None, str | None, str | None, str | None]:
    if not value:
        return None, None, None, "auth file missing or invalid"
    tokens = value.get("tokens") if isinstance(value.get("tokens"), dict) else {}
    auth_mode = value.get("auth_mode") or value.get("authMode") or tokens.get("auth_mode")
    access_token = tokens.get("access_token") or value.get("access_token")
    account_id = tokens.get("account_id") or value.get("account_id")
    chatgpt_account_id = (
        tokens.get("chatgpt_account_id")
        or value.get("chatgpt_account_id")
        or account_id
    )
    return (
        access_token if isinstance(access_token, str) and access_token else None,
        account_id if isinstance(account_id, str) and account_id else None,
        chatgpt_account_id if isinstance(chatgpt_account_id, str) and chatgpt_account_id else None,
        auth_mode if isinstance(auth_mode, str) else None,
    )


def resolve_profile_identity(home: Path | None = None) -> CodexProfile:
    """Resolve a stable opaque profile key without retaining raw auth data."""
    resolved_home = (home or codex_home()).expanduser().resolve()
    _, account_id, chatgpt_account_id, _ = _auth_fields(_read_auth_object(resolved_home))
    material = "|".join((str(resolved_home), account_id or "", chatgpt_account_id or ""))
    profile_key = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return CodexProfile(
        home=resolved_home,
        account_id=account_id,
        chatgpt_account_id=chatgpt_account_id,
        profile_key=profile_key,
    )


def read_codex_credentials(
    profile: CodexProfile | None = None,
) -> tuple[CodexCredentials | None, str]:
    """Read only the access token needed for an optional WHAM request.

    The current implementation intentionally does not refresh tokens.  A
    missing/expired token is a provider-unavailable result and never causes a
    Pan-owned credential write.
    """
    current = profile or resolve_profile_identity()
    value = _read_auth_object(current.home)
    access_token, account_id, chatgpt_account_id, auth_mode = _auth_fields(value)
    if auth_mode and auth_mode.lower() != "chatgpt":
        return None, "unsupported_auth_mode"
    if not access_token:
        return None, "missing_access_token"
    return CodexCredentials(
        access_token=access_token,
        account_id=account_id,
        chatgpt_account_id=chatgpt_account_id,
    ), "valid"


def quota_root() -> Path:
    configured = os.environ.get("PAN_CODEX_QUOTA_DIR")
    return Path(configured).expanduser() if configured else DEFAULT_QUOTA_ROOT


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_stale(record: dict[str, Any] | None, ttl_seconds: float, now: datetime | None = None) -> bool:
    if not isinstance(record, dict):
        return True
    observed = _parse_timestamp(record.get("observedAt") or record.get("updatedAt"))
    if observed is None:
        return True
    current = now or datetime.now(timezone.utc)
    return (current - observed).total_seconds() > max(0.0, float(ttl_seconds))


def _lock_digest(lock_path: Path, prefix: str) -> str:
    digest = hashlib.sha256(str(lock_path.resolve()).encode("utf-8")).hexdigest()
    return f"Local\\Pan{prefix}_{digest}"


@contextmanager
def _cross_process_lock(lock_path: Path):
    """Use the same cross-process semantics on Windows and POSIX."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel32.CreateMutexW(
            None, False, _lock_digest(lock_path, "CodexQuota")
        )
        if not mutex:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        wait = kernel32.WaitForSingleObject(mutex, 0xFFFFFFFF)
        if wait not in (0, 0x80):
            kernel32.CloseHandle(mutex)
            raise OSError(f"WaitForSingleObject failed: {wait}")
        try:
            yield
        finally:
            kernel32.ReleaseMutex(mutex)
            kernel32.CloseHandle(mutex)
        return

    lock_path.touch(exist_ok=True)
    handle = open(lock_path, "r+b")
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(20):
            try:
                os.replace(temp_path, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return value


def _normalize_legacy_record(value: dict[str, Any] | None, profile_key: str) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("windows"), dict) and isinstance(value.get("rawSnapshots"), dict):
        result = dict(value)
        result.setdefault("schemaVersion", QUOTA_SCHEMA_VERSION)
        result.setdefault("profileKey", profile_key)
        result.setdefault("source", "cache")
        result.setdefault("observedAt", result.get("updatedAt"))
        result.setdefault("receivedAt", result.get("observedAt"))
        return result

    raw = value.get("rateLimits") or value.get("rate_limits") or value.get("raw")
    if not isinstance(raw, dict):
        return None
    normalized = normalize_codex_rate_limits(raw)
    if not normalized.get("valid"):
        return None
    observed = value.get("observedAt") or value.get("updatedAt") or value.get("receivedAt")
    return {
        "schemaVersion": QUOTA_SCHEMA_VERSION,
        "profileKey": profile_key,
        "windows": normalized["windows"],
        "rawSnapshots": {"legacy": normalized["raw"]},
        "raw": normalized["raw"],
        "observedAt": observed,
        "receivedAt": value.get("receivedAt") or observed,
        "updatedAt": value.get("updatedAt") or observed,
        "source": value.get("source") or "legacy",
    }


class CodexQuotaStore:
    """Atomic, profile-scoped last-good quota store."""

    def __init__(self, profile: CodexProfile | None = None, root: Path | None = None):
        self.profile = profile or resolve_profile_identity()
        self.root = Path(root) if root is not None else quota_root()
        self.path = self.root / f"{self.profile.profile_key}.json"
        self.lock_path = self.root / f".{self.profile.profile_key}.lock"

    @classmethod
    def available_profile_keys(cls, root: Path | None = None) -> list[str]:
        """Return cached profile keys without opening or exposing snapshots."""
        directory = Path(root) if root is not None else quota_root()
        if not directory.is_dir():
            return []
        return sorted(
            path.stem for path in directory.glob("*.json")
            if path.is_file() and path.stem
        )

    def load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        with _PROCESS_LOCK, _cross_process_lock(self.lock_path):
            return _normalize_legacy_record(_read_record(self.path), self.profile.profile_key)

    def update(
        self,
        rate_limits: dict[str, Any],
        *,
        observed_at: str | None = None,
        received_at: str | None = None,
        source: str = "app-server-push",
    ) -> tuple[dict[str, Any] | None, bool]:
        normalized = normalize_codex_rate_limits(rate_limits)
        if not normalized.get("valid"):
            return self.load(), False

        with _PROCESS_LOCK, _cross_process_lock(self.lock_path):
            current = _normalize_legacy_record(_read_record(self.path), self.profile.profile_key)
            observed = observed_at or _now_iso()
            received = received_at or observed
            current_observed = _parse_timestamp(
                current.get("observedAt") if isinstance(current, dict) else None
            )
            incoming_observed = _parse_timestamp(observed)
            if (
                current_observed is not None
                and incoming_observed is not None
                and incoming_observed < current_observed
            ):
                # A delayed worker/HTTP response must not roll a profile back
                # to an older valid snapshot. The caller can retry with a
                # newer observation; the last-good record remains untouched.
                return current, False
            merged = merge_normalized_quota(current, normalized)
            if merged is None:
                return current, False
            raw_snapshots = merged["rawSnapshots"]
            raw_snapshots[source] = normalized["raw"]
            record = {
                "schemaVersion": QUOTA_SCHEMA_VERSION,
                "profileKey": self.profile.profile_key,
                "windows": merged["windows"],
                "rawSnapshots": raw_snapshots,
                "raw": normalized["raw"],
                "observedAt": observed,
                "receivedAt": received,
                "updatedAt": observed,
                "source": source,
            }
            _atomic_write(self.path, record)
            return record, True


def current_store(root: Path | None = None) -> CodexQuotaStore:
    return CodexQuotaStore(resolve_profile_identity(), root=root)


def update_current_profile(
    rate_limits: dict[str, Any],
    *,
    observed_at: str | None = None,
    received_at: str | None = None,
    source: str = "app-server-push",
    root: Path | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    return current_store(root).update(
        rate_limits,
        observed_at=observed_at,
        received_at=received_at,
        source=source,
    )
