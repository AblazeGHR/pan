"""Rewind PTY ownership, independent of the menu automation.

Only retained handles and the assigned Job establish death. Failed cleanup
keeps this owner in a bounded registry; retry_cleanup consumes the same owner.
"""
from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any, Sequence

from packages.core.terminal.attachments import AttachmentRegistry
from packages.core.terminal.backend import ConPtyBackend
from packages.core.terminal.contracts import OwnershipMode, OwnershipPolicy
from packages.core.terminal.driver import AutomationContext
from packages.core.terminal.observer import PyteScreenObserver
from packages.core.terminal.ownership import build_runtime
from packages.core.terminal.spawn_win import SpawnDenied

MAX_REWIND_OWNERS = 32
_owners: dict[str, PtySession] = {}
_owners_lock = threading.Lock()


class RewindStartupError(RuntimeError):
    def __init__(self, owner_id: str, cause: Exception, cleanup: dict[str, Any]):
        super().__init__(f"rewind terminal startup failed ({type(cause).__name__})")
        self.owner_id = owner_id
        self.cleanup = cleanup


def retry_cleanup(owner_id: str) -> dict[str, Any]:
    """Retry a retained owner in this process, not a PID-only persisted claim."""
    with _owners_lock:
        owner = _owners.get(owner_id)
    if owner is None:
        return {"ok": False, "owner_id": owner_id, "error": "owner-not-found"}
    return owner.close()


def retained_owner_ids() -> list[str]:
    with _owners_lock:
        return list(_owners)


class PtySession:
    def __init__(self, argv: Sequence[str], cwd: str | Path, *, rows: int = 36, cols: int = 120):
        self.owner_id = "rewind_" + uuid.uuid4().hex
        self.backend = None
        self.runtime = None
        self._startup_denied = None
        self._close_lock = threading.Lock()
        self._confirmed: dict[str, Any] | None = None
        # Admission reserves even before spawn: no untracked process on cap.
        with _owners_lock:
            if len(_owners) >= MAX_REWIND_OWNERS:
                raise RuntimeError("rewind cleanup owner capacity exhausted")
            _owners[self.owner_id] = self
        try:
            observer = PyteScreenObserver(rows=rows, cols=cols)
            self.backend = ConPtyBackend.spawn(
                argv, cwd=str(cwd), rows=rows, cols=cols,
                env={**os.environ, "TERM": "xterm-256color", "CI": "0"},
            )
            self.runtime = build_runtime(
                self.owner_id, self.backend,
                ownership=OwnershipPolicy(
                    mode=OwnershipMode.SERVICE, lifecycle_owner="rewind-host",
                    tree_guard_kind="windows-job", tree_guard=self.backend.guard,
                ),
                identity=self.backend.identity, identity_probe=self.backend.probe,
                output_cap=256 * 1024,
                output_consumer=lambda seq, data: observer.feed(data),
            )
            attachments = AttachmentRegistry(
                lambda tid: self.runtime if tid == self.owner_id else None)
            control = attachments.attach(self.owner_id, "rewind-automation", role="control",
                                         rows=rows, cols=cols)
            self.context = AutomationContext(
                self.owner_id, self.runtime, observer, control, attachments)
            self.runtime.start(rows=rows, cols=cols, gate=self.backend.gate)
        except Exception as exc:
            if isinstance(exc, SpawnDenied) and exc.attempt is not None:
                self._startup_denied = exc
                cleanup = self.close()
                raise RewindStartupError(self.owner_id, exc, cleanup) from exc
            if self.backend is None:
                with _owners_lock:
                    _owners.pop(self.owner_id, None)
                # A spawn failure retains its own atomic-spawn cleanup owner.
                raise
            cleanup = self.close()
            raise RewindStartupError(self.owner_id, exc, cleanup) from exc

    def close(self) -> dict[str, Any]:
        if not self._close_lock.acquire(timeout=0.2):
            return {"ok": False, "owner_id": self.owner_id, "owner_retained": True,
                    "error": "close-in-progress", "retryable": True}
        try:
            if self._confirmed is not None:
                return dict(self._confirmed)
            if self._startup_denied is not None:
                report = self._startup_denied.retry_cleanup()
                release = report.get("release", report)
                confirmed = (release.get("closed") is True
                             and "guard_terminate_error" not in report
                             and not report.get("guard_terminate", {}).get("remaining"))
                info = {**report, "ok": confirmed,
                        "owner_id": self.owner_id,
                        "owner_retained": not confirmed}
            elif self.runtime is None:
                # Runtime assembly failed after spawn. Never bare-close a live
                # process: the backend's retained handle and Job remain owner.
                self.backend.terminate(True)
                _, remaining = self.backend.guard.terminate_tree(self.backend.pid, timeout=2.0)
                if remaining:
                    return {"ok": False, "owner_id": self.owner_id,
                            "owner_retained": True, "retryable": True,
                            "error": "tree-not-empty", "tree_remaining_pids": remaining}
                closed = self.backend.close()
                info = {"ok": closed.get("closed") is True, "owner_id": self.owner_id,
                        "owner_retained": closed.get("closed") is not True,
                        "error": closed.get("error")}
            else:
                report = self.runtime.close(reason="rewind-complete", interrupt=False)
                info = {**report.as_dict(), "ok": report.ok,
                        "owner_id": self.owner_id, "retryable": not report.ok}
                if report.ok:
                    self.context.attachments.forget(self.owner_id)
            if info["ok"] is True:
                self._confirmed = dict(info)
                with _owners_lock:
                    _owners.pop(self.owner_id, None)
            return info
        except Exception as exc:
            return {"ok": False, "owner_id": self.owner_id, "owner_retained": True,
                    "retryable": True, "error": type(exc).__name__}
        finally:
            self._close_lock.release()
