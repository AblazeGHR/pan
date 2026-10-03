"""Loopback-only local MCP bridge, not a remote authentication mechanism.

The caller header is an assertion by a same-user local process. Resolve it from
the Session store; browsers (Origin/Fetch) and non-loopback peers are refused.
Remote/SSE MCP must not proxy its process identity through this endpoint.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import threading
from contextlib import asynccontextmanager

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from packages.web import terminal_api
from packages.web.terminal_tools import TerminalTools, ToolRejected

router = APIRouter()
MAX_BODY = 256 * 1024


def resolve_caller(caller_id):
    from packages.core import session
    caller = session.get(caller_id, load_history=False)
    if caller is None or caller.id != caller_id:
        return None
    return {"id": caller.id, "restrictToManaged": caller.restrict_to_managed,
            "managed": tuple(caller.managed)}


def check_access(caller, target):
    from packages.core import session
    if session.get(target, load_history=False) is None:
        return False
    return caller.get("restrictToManaged") is not True or target == caller["id"] or target in caller.get("managed", ())


def _error(status, code):
    return JSONResponse({"ok": False, "error": {"code": code}}, status_code=status)


def _local(request):
    if request.client is None:
        return False
    try:
        address = ipaddress.ip_address(request.client.host)
        local = address.is_loopback or (getattr(address, "ipv4_mapped", None) is not None and address.ipv4_mapped.is_loopback)
    except ValueError:
        return False
    return local and "origin" not in request.headers and not any(key.startswith("sec-fetch-") for key in request.headers)


class ToolsHost:
    def __init__(self, runtime, *, resolver=resolve_caller, access=check_access):
        self.runtime = runtime
        self.resolver, self.access = resolver, access
        self.tools = None
        self.closing = False
        self.cleanup_task = None
        self.active = 0
        self._lock = threading.Lock()

    def dispatch(self, caller, operation, body):
        with self._lock:
            if self.closing:
                raise ToolRejected("closing")
            if self.tools is None:
                self.tools = TerminalTools(self.runtime.service, resolve_caller=self.resolver, check_access=self.access)
            self.active += 1
        try:
            return self.tools.dispatch(caller, operation, body)
        finally:
            with self._lock:
                self.active -= 1

    async def close(self, budget=2.0):
        deadline = asyncio.get_running_loop().time() + max(0.0, budget)
        with self._lock:
            self.closing = True
        while self.active and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(min(0.01, max(0.0, deadline - asyncio.get_running_loop().time())))
        if self.active:
            return {"active_workers": self.active, "cleanup_unconfirmed": True}
        if self.tools is None:
            return {"retained_tokens": 0}
        if self.cleanup_task is None or self.cleanup_task.done():
            self.cleanup_task = asyncio.create_task(self.runtime.call(self.tools.retry_releases))
        try:
            remaining = await asyncio.wait_for(asyncio.shield(self.cleanup_task), max(0.0, deadline - asyncio.get_running_loop().time()))
            return {"retained_tokens": remaining}
        except (Exception, asyncio.CancelledError):
            return {"retained_tokens": len(self.tools._retained), "cleanup_unconfirmed": True}


@router.post("/api/terminal-tools/{operation}")
async def terminal_tool(request: Request, operation: str):
    if not _local(request):
        return _error(403, "local-process-required")
    caller = request.headers.get("x-pan-terminal-caller")
    if not caller or len(caller) > 128:
        return _error(403, "caller-required")
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        return _error(415, "json-required")
    host = getattr(request.app.state, "terminal_tools_host", None)
    if not isinstance(host, ToolsHost) or host.closing:
        return _error(503, "closing" if isinstance(host, ToolsHost) else "not-ready")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BODY:
            return _error(413, "body-too-large")
        data.extend(chunk)
    try:
        body = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        return _error(422, "invalid-json")
    try:
        value = await host.runtime.call(lambda: host.dispatch(caller, operation, body))
        return JSONResponse({"ok": True, "result": value})
    except ToolRejected as error:
        return _error(403 if error.code.startswith(("caller-", "permission-", "session-scope")) else 422, error.code)
    except terminal_api.GateRejected as error:
        return _error(error.status_code, error.code)
    except terminal_api.Busy:
        return _error(429, "busy")
    except Exception:
        return _error(502, "terminal-tool-failed")


@asynccontextmanager
async def terminal_tools_lifespan(app):
    host = ToolsHost(app.state.terminal_runtime)
    app.state.terminal_tools_host = host
    try:
        yield host
    finally:
        app.state.terminal_tools_shutdown = await host.close()
