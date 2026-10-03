"""Local MCP dispatch core; transport must establish a local process boundary.

This module does not authenticate arbitrary HTTP headers. The host supplies a
caller resolver and access checker, and executes dispatch in TerminalRuntime's
tracked worker. Scope is metadata, not a substitute for caller verification.
"""
from __future__ import annotations

import base64
import binascii
import threading
from collections.abc import Callable, Mapping

from packages.core.terminal.service import ServiceContext
from packages.web.terminal_api import (
    project_view, project_read, project_snapshot, project_detach,
    validate_terminal_id, _parse_uint64,
)


class ToolRejected(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_FIELDS = {
    "create": {"rows", "cols", "cwd", "workspace_id", "session_id"},
    "list": {"workspace_id", "session_id"},
    "get": {"terminal_id"},
    "read": {"terminal_id", "cursor", "max_bytes"},
    "snapshot": {"terminal_id", "timeout_ms"},
    "close": {"terminal_id"},
    "detach": {"terminal_id"},
    "input": {"terminal_id", "data_b64", "take_control", "seq"},
}


def _integer(data: Mapping, name: str, default: int, maximum: int) -> int:
    value = data.get(name, default)
    if type(value) is not int or not 1 <= value <= maximum:
        raise ToolRejected("invalid-" + name.replace("_", "-"))
    return value


def _string(data: Mapping, name: str) -> str | None:
    value = data.get(name)
    if value is not None and (not isinstance(value, str) or not value or len(value) > 4096):
        raise ToolRejected("invalid-" + name.replace("_", "-"))
    return value


class TerminalTools:
    """One host-owned dispatcher using the existing service, never a new one.

    Input explicitly takes control for one command, revoking a prior controller.
    Tokens stay in the host. Failed release retains the real token for retry.
    The host must drain these retained tokens before service shutdown.
    """
    def __init__(self, service, *, resolve_caller: Callable, check_access: Callable):
        self.service = service
        self.resolve_caller = resolve_caller
        self.check_access = check_access
        self._retained = {}
        self._dispatch_lock = threading.RLock()

    def _caller(self, caller_id: str, operation: str, data: Mapping) -> Mapping:
        if not isinstance(caller_id, str) or not caller_id or len(caller_id) > 128:
            raise ToolRejected("caller-required")
        caller = self.resolve_caller(caller_id)
        if not isinstance(caller, Mapping) or caller.get("id") != caller_id:
            raise ToolRejected("caller-unverified")
        if operation not in {"list", "get", "read", "snapshot"} and caller.get("readonly") is True:
            raise ToolRejected("caller-readonly")
        target = _string(data, "session_id")
        if target is not None and self.check_access(caller, target) is not True:
            raise ToolRejected("permission-denied")
        return caller

    def retry_releases(self) -> int:
        if not self._dispatch_lock.acquire(blocking=False):
            return len(self._retained)
        try:
            return self._retry_releases()
        finally:
            self._dispatch_lock.release()

    def _retry_releases(self) -> int:
        for key, token in tuple(self._retained.items()):
            try:
                self.service.release_attachment(token)
            except Exception:
                continue
            self._retained.pop(key, None)
        return len(self._retained)

    def dispatch(self, caller_id: str, operation: str, data: Mapping):
        # Nonblocking admission: no unbounded queue behind a blocked input.
        if not self._dispatch_lock.acquire(blocking=False):
            raise ToolRejected("busy")
        try:
            return self._dispatch(caller_id, operation, data)
        finally:
            self._dispatch_lock.release()

    def _dispatch(self, caller_id: str, operation: str, data: Mapping):
        if operation not in _FIELDS:
            raise ToolRejected("unknown-operation")
        if not isinstance(data, Mapping) or set(data) - _FIELDS[operation]:
            raise ToolRejected("unknown-field")
        self._caller(caller_id, operation, data)
        if operation == "create":
            rows = _integer(data, "rows", 24, 500)
            cols = _integer(data, "cols", 80, 1000)
            strings = {key: _string(data, key) for key in ("cwd", "workspace_id", "session_id")}
            return project_view(self.service.create(rows=rows, cols=cols, **strings,
                context=ServiceContext(created_by="mcp:" + caller_id[:28], trusted_local=True)))
        if operation == "list":
            workspace, session = _string(data, "workspace_id"), _string(data, "session_id")
            items = self.service.list()
            return [project_view(item) for item in items
                    if (workspace is None or item.get("workspace_id") == workspace)
                    and (session is None or item.get("session_id") == session)]
        try:
            terminal_id = validate_terminal_id(data.get("terminal_id"))
        except (ValueError, TypeError):
            raise ToolRejected("invalid-terminal-id") from None
        if operation == "get":
            return project_view(self.service.get(terminal_id))
        if operation == "read":
            cursor = _parse_uint64(data.get("cursor", "0"))
            if cursor is None:
                raise ToolRejected("invalid-cursor")
            maximum = _integer(data, "max_bytes", 32768, 131072)
            return project_read(self.service.read(terminal_id, cursor=cursor, max_bytes=maximum))
        if operation == "snapshot":
            timeout = _integer(data, "timeout_ms", 1000, 5000)
            return project_snapshot(self.service.snapshot(terminal_id, timeout_ms=timeout))
        if operation == "close":
            return project_view(self.service.close(terminal_id, reason="explicit-close"))
        if operation == "detach":
            return project_detach(self.service.detach(terminal_id))
        if data.get("take_control") is not True:
            raise ToolRejected("explicit-control-required")
        encoded = data.get("data_b64")
        if not isinstance(encoded, str) or len(encoded) > 174764:
            raise ToolRejected("invalid-input")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ToolRejected("invalid-input") from None
        if len(payload) > 131072:
            raise ToolRejected("input-too-large")
        seq = None
        if "seq" in data:
            seq = _parse_uint64(data["seq"])
            if seq is None:
                raise ToolRejected("invalid-seq")
        self.retry_releases()
        if len(self._retained) >= 128:
            raise ToolRejected("cleanup-busy")
        token = self.service.attach(terminal_id, client_id="mcp:" + caller_id, role="control")
        self._retained[id(token)] = token
        try:
            raw = self.service.input(terminal_id, token, payload, seq=seq)
            return {"terminal_id": terminal_id, "accepted": raw.get("accepted") is True,
                    "size": raw.get("size") if type(raw.get("size")) is int and
                    0 <= raw["size"] <= len(payload) else None,
                    "seq": str(seq) if seq is not None else None}
        finally:
            try:
                self.service.release_attachment(token)
            except Exception:
                pass  # the actual token remains in the bounded owner registry
            else:
                self._retained.pop(id(token), None)
