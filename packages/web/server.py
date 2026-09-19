"""Pan Web Channel — FastAPI routes + WebSocket + Dashboard."""

from __future__ import annotations

import asyncio
from collections import deque
import ctypes
import errno
import hashlib
import inspect
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Callable
from urllib.parse import parse_qs, quote, unquote, urlsplit

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Body
from fastapi.responses import HTMLResponse, Response, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import httpx

from packages.core import worker
from packages.core import session as sess
from packages.core import workspace as workspaces
from packages.core.adapters import get_adapter, list_adapters, get_sessions_provider
from packages.core.adapters.validation import (
    AdapterCapabilityError,
    is_valid_model,
    resolve_adapter,
    validate_effort,
    validate_model,
    validate_permission_mode,
    validate_session_settings,
    sanitize_adapter_config,
    supported_settings as _adapter_supported_settings,
    VALID_MCP_TRANSPORTS,
)
from packages.core.adapters.cbc import sessions as cbc_sessions
from packages.core.adapters.cbc.sessions import sanitize_project_dir_name
from packages.core.adapters.kimi import sessions as kimi_sessions
from packages.core.adapters.opencode import sessions as opencode_sessions
from packages.core.config import (
    DEFAULT_PLUGIN_MANIFESTS,
    load_config,
    read_config_file,
    resolve_pan_python_info,
    save_config,
)
from packages.core.cli_diagnostics import get_cli_diagnostics
from packages.core.character import CharacterManager
from packages.core.manifest_loader import SessionTemplate
from packages.core import background_jobs
from packages.core.codex_quota import (
    format_codex_quota_record,
    validate_quota_window,
)
from packages.core.codex_quota_provider import CodexWhamProvider
from packages.core.codex_quota_store import (
    CodexProfile,
    CodexQuotaStore,
    is_stale,
    resolve_profile_identity,
)
from packages.core import main_lifecycle
from packages.core import notifications, reminders

# ── logging ──

def _log(msg: str):
    """Print with HH:MM:SS prefix without failing on narrow consoles."""
    stream = sys.stdout
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    encoding = getattr(stream, "encoding", None)
    if encoding:
        line = line.encode(encoding, errors="backslashreplace").decode(encoding)
    print(line, file=stream)


# Comma-separated path prefixes to skip in request logging.
# e.g. PAN_LOG_SKIP=/api/sessions,/ws
_LOG_SKIP = [
    p.strip()
    for p in os.environ.get("PAN_LOG_SKIP", "").split(",")
    if p.strip()
]


# ── lifespan ──


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: load all saved sessions (don't auto-spawn Workers).
    Shutdown: kill all child processes."""
    sessions = sess.list_all()
    if sessions:
        _log(f"[Pan] Loaded {len(sessions)} sessions from disk")

    # 服务级 watchdog（立项 4.4）：生命周期=Pan 服务，周期扫描落盘队列
    # queue_pending 非空但没有活 worker 的 session，自动 spawn 恢复。
    worker.start_global_watchdog()
    background_jobs.start_recovery_loop()
    reminder_task = asyncio.create_task(_reminder_loop())
    
    # Init CharacterManager with manifest
    global _character_manager
    config = load_config()
    plugin_paths = config.get("plugin_manifests", DEFAULT_PLUGIN_MANIFESTS)
    _character_manager = CharacterManager(str(DATA_DIR))
    try:
        _character_manager.load_manifest(plugin_paths)
        templates = _character_manager.list_session_templates()
        _log(f"[Pan] Loaded {len(templates)} session templates from manifest")
    except Exception as e:
        _log(f"[Pan] Character manifest not loaded: {e}")
    
    yield
    reminder_task.cancel()
    try:
        await reminder_task
    except asyncio.CancelledError:
        pass
    worker.stop_global_watchdog()
    await background_jobs.stop_recovery_loop()
    # 方案 C（关闭收尾加固）：先有界 drain fire-and-forget 的 recovery 任务
    # （关闭开始即禁止新调度），再关 worker——避免取消打在真实 subprocess
    # spawn 中途导致 Windows Proactor 循环无法退出。
    await worker.drain_recoveries()
    await worker.shutdown_all()
    # Release cached MemoryManagers + loaded embedding models (#20).
    try:
        from packages.core.memory_context import clear_memory_manager_cache
        clear_memory_manager_cache()
    except Exception:
        pass
    _log("[Pan] All workers shut down")


_character_manager: CharacterManager | None = None


app = FastAPI(title="Pan", lifespan=lifespan)


async def _reminder_loop():
    """Recover persisted reminders after restart; claim-before-send is once-only."""
    while True:
        await _deliver_due_reminders()
        await asyncio.sleep(1)


async def _deliver_due_reminders() -> int:
    """Claim due records before sending, returning the number of broadcasts."""
    delivered = 0
    for item in reminders.claim_due():
        s = sess.get(item.get("sessionId", ""))
        if not s:
            continue
        payload = notifications.dispatch_reminder(item.get("title", "Reminder"), item.get("body", ""))
        await broadcast({"type": "notification.reminder", "sessionId": s.id,
                         "reminderId": item["id"], "notification": payload})
        delivered += 1
    return delivered

ws_clients: set[WebSocket] = set()
agent_clients: set[WebSocket] = set()

# agent 视角的默认订阅：只推结果摘要，不推原始 stream（防 context 爆炸）
_AGENT_DEFAULT_SUBSCRIPTION = frozenset({"worker.result"})
_AGENT_TERMINAL_RESULT_STATUSES = frozenset({"done", "error", "cancelled"})


@dataclass
class AgentSubscription:
    """单个 /ws/agent 连接的订阅状态。

    - event_types：订阅的事件类型（默认只 worker.result）
    - session_ids：关心的 session 集合；非空时 worker.result 只推给匹配
      workerId→sessionId 的连接的 session；空集 = 订阅所有 session
    - consumed_seq：每 session 已消费的 result 序号（taskSeq），重连补发用
    """
    event_types: set[str] = field(default_factory=lambda: set(_AGENT_DEFAULT_SUBSCRIPTION))
    session_ids: set[str] = field(default_factory=set)
    consumed_seq: dict[str, int] = field(default_factory=dict)


# 每个 /ws/agent 连接的订阅状态；未订阅默认只推 worker.result
agent_subscriptions: dict[WebSocket, AgentSubscription] = {}

# ── file paths (relative to packages/web/) ──
_WEB_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _WEB_DIR.parent.parent  # packages/web/ → packages/ → project root
DATA_DIR = _PROJECT_DIR / "data"
WORKDIRS_DIR = DATA_DIR / "workdirs"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
REACT_DIST_DIR = _WEB_DIR / "dist"
REACT_DIST_EXISTS = REACT_DIST_DIR.is_dir()

# Main-service restart compatibility aliases.  Durable Registry records are
# authoritative; these names remain for older in-process callers/tests only.
_main_restart_lock = threading.Lock()
_main_restart_pending = False
_main_restart_request_id: str | None = None

# Main-service exit has a separate state machine.  It never shares the
# restart supervisor: once the workers are stopped the detached supervisor
# only stops this checkout's main service and never starts it again.
_main_exit_lock = threading.Lock()
_main_exit_pending = False
_main_exit_request_id: str | None = None
_main_exit_stage = "idle"
_main_exit_error: str | None = None

# Phase-one main lifecycle options are intentionally empty.  Keep the
# validation at the HTTP boundary so a future option cannot be accepted by
# one operation and silently ignored by the other.
_MAIN_LIFECYCLE_REQUEST_FIELDS = frozenset({"options"})
_MAIN_LIFECYCLE_SUPPORTED_OPTIONS = frozenset()


def _parse_main_lifecycle_options(payload: dict | None, operation: str) -> dict:
    """Validate and freeze the phase-one lifecycle request options once.

    The returned mapping is JSON-shaped and is persisted in the lifecycle Job
    before any worker gate or detached supervisor is touched.  ``None`` and
    ``{}`` preserve the historical no-body behavior; ``options`` is the only
    request envelope accepted for forward compatibility.
    """
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_lifecycle_request",
                "operation": operation,
                "message": "request body must be an object",
            },
        )

    unknown_request_fields = sorted(set(payload) - _MAIN_LIFECYCLE_REQUEST_FIELDS)
    if unknown_request_fields:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "unsupported_lifecycle_request_fields",
                "operation": operation,
                "fields": unknown_request_fields,
                "message": "unsupported lifecycle request field(s)",
            },
        )

    options = payload.get("options", {})
    if not isinstance(options, dict):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_lifecycle_options",
                "operation": operation,
                "message": "options must be an object",
            },
        )
    unknown_options = sorted(set(options) - _MAIN_LIFECYCLE_SUPPORTED_OPTIONS)
    if unknown_options:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "unsupported_lifecycle_options",
                "operation": operation,
                "fields": unknown_options,
                "message": "unsupported lifecycle option(s)",
            },
        )

    # JSON request data is already detached from the caller.  Rebuild the
    # mapping so later phases receive a canonical snapshot, not a live input
    # object; the Job write is the cross-process source of truth.
    return json.loads(json.dumps(options, ensure_ascii=False, sort_keys=True))


def _main_restart_paths() -> dict[str, Path]:
    scripts = _PROJECT_DIR / "scripts"
    return {
        "supervisor": scripts / "restart_pan.ps1",
        "stop": scripts / "stop_pan.bat",
        "start": scripts / "start_pan.bat",
    }


def _main_restart_registry_root() -> Path:
    return _PROJECT_DIR / "data" / "background_jobs"


def _main_restart_port() -> int:
    value = os.environ.get("PAN_PORT")
    if value:
        try:
            return int(value)
        except ValueError:
            pass
    try:
        config = json.loads((_PROJECT_DIR / "config.json").read_text(encoding="utf-8"))
        return int(config.get("port") or 8768)
    except (OSError, ValueError, TypeError):
        return 8768


def _main_restart_job_view(job: dict | None) -> dict:
    if not job:
        return {}
    return {
        "jobId": job.get("jobId"), "requestId": job.get("requestId"),
        "operation": job.get("operation"), "options": dict(job.get("options") or {}),
        "phase": job.get("phase"), "jobStatus": job.get("status"),
        "root": job.get("root"), "port": job.get("port"),
        "oldPid": job.get("oldPid"), "oldPidCreatedAt": job.get("oldPidCreatedAt"),
        "newPid": job.get("newPid"), "newPidCreatedAt": job.get("newPidCreatedAt"),
        "error": job.get("error"), "errors": list(job.get("errors") or []),
        "createdAt": job.get("createdAt"),
        "updatedAt": job.get("updatedAt"),
    }


def _main_restart_status() -> dict:
    paths = _main_restart_paths()
    available = os.name == "nt" and all(path.is_file() for path in paths.values())
    missing = [str(path) for path in paths.values() if not path.is_file()]
    registry_root = _main_restart_registry_root()
    port = _main_restart_port()
    active_job = background_jobs.get_active_service_job(
        str(_PROJECT_DIR), port, registry_root
    )
    latest_job = next(
        (job for job in background_jobs.list_jobs(registry_root)
         if job.get("kind") == background_jobs.SERVICE_LIFECYCLE_KIND
         and str(Path(str(job.get("root", ""))).expanduser().resolve())
         == str(_PROJECT_DIR.expanduser().resolve())
         and int(job.get("port", -1)) == port),
        None,
    )
    with _main_restart_lock:
        # The in-memory values remain only as a compatibility fallback for
        # older callers/tests.  A persisted terminal Job always wins, so a
        # failed supervisor cannot leave a stale process-local guard behind.
        pending = bool(active_job) or (_main_restart_pending and latest_job is None)
        request_id = _main_restart_request_id
    result = {
        "available": available,
        "pending": pending,
        "platform": os.name,
        "port": port,
    }
    job = active_job or latest_job
    if job:
        result.update(_main_restart_job_view(job))
    elif request_id:
        result["requestId"] = request_id
    if not available:
        result["reason"] = (
            "main service restart is available only on Windows"
            if os.name != "nt"
            else "restart scripts are missing: " + ", ".join(missing)
        )
    return result


def _clear_main_restart_state(request_id: str) -> None:
    global _main_restart_pending, _main_restart_request_id
    with _main_restart_lock:
        if _main_restart_request_id == request_id:
            _main_restart_pending = False
            _main_restart_request_id = None


def _watch_main_restart(process: subprocess.Popen, request_id: str) -> None:
    """Legacy hook retained for callers that used to patch the old watcher.

    New restart requests do not start this thread or use a timed in-memory
    guard; the detached lifecycle Job owns all progress and failure state.
    """
    _clear_main_restart_state(request_id)


def _launch_main_restart_supervisor(request_id: str) -> subprocess.Popen:
    """Launch a hidden, detached PowerShell supervisor.

    The supervisor is deliberately not awaited here.  It starts a second
    PowerShell process before running stop_pan.bat, so taskkill /T against the
    current Pan process cannot take the restart orchestration with it.
    """
    script = _main_restart_paths()["supervisor"]
    registry_root = _main_restart_registry_root()
    job = background_jobs.find_service_job(request_id, registry_root)
    if not job:
        raise ValueError("durable restart Job not found")
    powershell_args = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Root",
        str(_PROJECT_DIR),
        "-RequestId",
        request_id,
        "-JobId",
        job["jobId"],
        "-RegistryRoot",
        str(registry_root),
        "-Port",
        str(job["port"]),
        # The shell launcher enters the real supervisor directly instead of
        # relying on restart_pan.ps1's second Start-Process hop.
        "-Supervisor",
    ]
    if job.get("oldPid"):
        powershell_args += ["-OldPid", str(job["oldPid"])]
    if job.get("oldPidCreatedAt") is not None:
        powershell_args += ["-OldPidCreatedAt", str(job["oldPidCreatedAt"])]
    # A new process group does not change the parent PID relationship.  The
    # old Pan service's stop_pan.bat uses taskkill /T, so launch through the
    # short-lived `start` shell and let it exit after CreateProcess succeeds;
    # the PowerShell supervisor is then no longer below the old Pan tree.
    command = ["cmd.exe", "/d", "/c", "start", "", "/b"] + powershell_args
    launcher_log = _PROJECT_DIR / "data" / "logs" / "pan-restart-launcher.log"
    launcher_log.parent.mkdir(parents=True, exist_ok=True)
    # Windows DETACHED_PROCESS can report a successful Popen while the
    # powershell.exe child never executes its -File script.  CREATE_NO_WINDOW
    # provides the required non-console launch while CREATE_NEW_PROCESS_GROUP
    # keeps this supervisor outside the old Pan process-group cleanup.
    flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    )
    # Keep the handle open only across Popen.  subprocess duplicates the
    # redirected standard handle for the detached child before this closes it.
    # This captures PowerShell parameter/parser/startup failures that happen
    # before restart_pan.ps1 can create its own pan-restart.log.
    with launcher_log.open("ab") as log:
        return subprocess.Popen(
            command,
            cwd=str(_PROJECT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=flags,
        )


def _main_exit_paths() -> dict[str, Path]:
    scripts = _PROJECT_DIR / "scripts"
    return {"supervisor": scripts / "exit_pan.ps1", "stop": scripts / "stop_pan.bat"}


def _main_exit_status() -> dict:
    paths = _main_exit_paths()
    available = os.name == "nt" and all(path.is_file() for path in paths.values())
    missing = [str(path) for path in paths.values() if not path.is_file()]
    registry_root = _main_restart_registry_root()
    port = _main_restart_port()
    active_job = next(
        (job for job in background_jobs.list_jobs(registry_root)
         if job.get("kind") == background_jobs.SERVICE_LIFECYCLE_KIND
         and job.get("operation") == "exit"
         and str(Path(str(job.get("root", ""))).expanduser().resolve())
         == str(_PROJECT_DIR.expanduser().resolve())
         and int(job.get("port", -1)) == port
         and (job.get("phase") in background_jobs.SERVICE_ACTIVE_PHASES
              or job.get("status") in {"pending", "running"})),
        None,
    )
    latest_job = next(
        (job for job in background_jobs.list_jobs(registry_root)
         if job.get("kind") == background_jobs.SERVICE_LIFECYCLE_KIND
         and job.get("operation") == "exit"
         and str(Path(str(job.get("root", ""))).expanduser().resolve())
         == str(_PROJECT_DIR.expanduser().resolve())
         and int(job.get("port", -1)) == port),
        None,
    )
    with _main_exit_lock:
        pending = bool(active_job) or (_main_exit_pending and latest_job is None)
        request_id = _main_exit_request_id
        stage = _main_exit_stage
        error = _main_exit_error
    result = {
        "available": available,
        "pending": pending,
        "stage": stage,
        "platform": os.name,
        "port": port,
    }
    job = active_job or latest_job
    if job:
        result.update(_main_restart_job_view(job))
        result["stage"] = job.get("phase")
    elif request_id:
        result["requestId"] = request_id
    if error:
        result["error"] = error
    if not available:
        result["reason"] = (
            "main service exit is available only on Windows"
            if os.name != "nt"
            else "exit scripts are missing: " + ", ".join(missing)
        )
    return result


def _launch_main_exit_supervisor(request_id: str) -> subprocess.Popen:
    """Launch the stop-only exit supervisor outside the old Pan process tree."""
    script = _main_exit_paths()["supervisor"]
    registry_root = _main_restart_registry_root()
    job = background_jobs.find_service_job(request_id, registry_root)
    if not job or job.get("operation") != "exit":
        raise ValueError("durable exit Job not found")
    powershell_args = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(script), "-Root", str(_PROJECT_DIR),
        "-RequestId", request_id, "-JobId", job["jobId"],
        "-RegistryRoot", str(registry_root), "-Port", str(job["port"]),
        "-Supervisor",
    ]
    if job.get("oldPid"):
        powershell_args += ["-OldPid", str(job["oldPid"])]
    if job.get("oldPidCreatedAt") is not None:
        powershell_args += ["-OldPidCreatedAt", str(job["oldPidCreatedAt"])]
    # CREATE_NEW_PROCESS_GROUP alone does not break the old Pan parent tree;
    # stop_pan.bat uses taskkill /T.  The short-lived start shell creates the
    # PowerShell child and exits before the stop-only supervisor runs.
    command = ["cmd.exe", "/d", "/c", "start", "", "/b"] + powershell_args
    launcher_log = _PROJECT_DIR / "data" / "logs" / "pan-exit-launcher.log"
    launcher_log.parent.mkdir(parents=True, exist_ok=True)
    flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    )
    with launcher_log.open("ab") as log:
        return subprocess.Popen(
            command,
            cwd=str(_PROJECT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=flags,
        )

_WS_OUTBOUND_QUEUE_MAX = 64
_WS_SEND_TIMEOUT_SEC = 2.0
_WS_RESYNC_CLOSE_CODE = 1013

# A Worker stdout producer must never wait for a browser/agent socket.  The
# queue is deliberately process-local: queue_pending/history remain the
# durable truth for business/report delivery, while this is only a bounded
# live-view transport buffer.
_WS_DIAGNOSTICS = {
    "enqueued": 0,
    "coalescedDeltas": 0,
    "droppedDeltas": 0,
    "droppedControlEvents": 0,
    "slowClients": 0,
    "resyncRequired": 0,
    "resyncFrames": 0,
    "sendFailures": 0,
    "maxQueueDepth": 0,
}


@dataclass
class _OutboundMessage:
    data: dict
    control: bool
    coalesce_key: tuple | None = None
    on_delivered: Callable[[], None] | None = None


# This map is intentionally bounded by live connections.  It is separate from
# ws_clients/agent_clients because tests and legacy embedders may populate
# those sets directly before the first broadcast.
_ws_outbound: dict[WebSocket, "_OutboundClient"] = {}


_NON_COALESCIBLE_STREAM_TYPES = frozenset({
    # Native requests/responses are control traffic.  Losing one without an
    # explicit resync/close would strand a provider waiting for a decision.
    "approval.request",
    "codex.user_input",
    "codex.elicitation",
    "codex.terminal_interaction",
    "claude.permission_resolved",
    "codex.request_resolved",
})


def _stream_delta_key(data: dict) -> tuple | None:
    """Return the identity of a safe, coalescible intermediate stream delta.

    The key includes the durable Session address, the temporary Worker
    identity, its generation, the current task, and the native item/turn.  A
    delta without an explicit native item falls back to its event kind; the
    Worker serializes tasks, and the stream broadcast now carries taskSeq so
    this remains isolated across tasks as well.
    """
    if not isinstance(data, dict) or data.get("type") != "worker.stream":
        return None
    event = data.get("event")
    if not isinstance(event, dict) or event.get("delta") is not True:
        return None
    if event.get("type") in _NON_COALESCIBLE_STREAM_TYPES:
        return None
    session_id = data.get("sessionId")
    worker_id = data.get("workerId")
    if not session_id or not worker_id:
        return None
    task_key = data.get("taskId")
    if task_key is None:
        task_key = data.get("taskSeq", "implicit-task")
    item_key = None
    for candidate in (
        event.get("item_id"),
        event.get("itemId"),
        event.get("turn_id"),
        event.get("turnId"),
    ):
        if candidate is not None:
            item_key = candidate
            break
    if item_key is None:
        part = event.get("part")
        part_type = part.get("type") if isinstance(part, dict) else None
        item_key = (event.get("type"), event.get("role"), part_type)
    return (
        str(session_id),
        str(worker_id),
        data.get("generation"),
        str(task_key),
        str(item_key),
    )


def _append_delta_content(previous, current):
    """Append compatible delta content without retaining the prior payload."""
    if isinstance(previous, str) and isinstance(current, str):
        return previous + current
    if not isinstance(previous, list) or not isinstance(current, list):
        return None
    result = [dict(item) if isinstance(item, dict) else item for item in previous]
    for item in current:
        if not isinstance(item, dict):
            result.append(item)
            continue
        if result and isinstance(result[-1], dict):
            prior = result[-1]
            if prior.get("type") == item.get("type"):
                text_key = "text" if isinstance(item.get("text"), str) else None
                if text_key is None and isinstance(item.get("thinking"), str):
                    text_key = "thinking"
                if text_key is None and isinstance(item.get("think"), str):
                    text_key = "think"
                if text_key is not None and isinstance(prior.get(text_key), str):
                    result[-1] = {**prior, text_key: prior[text_key] + item[text_key]}
                    continue
        result.append(dict(item))
    return result


def _merge_stream_deltas(previous: dict, current: dict) -> dict | None:
    """Merge two adjacent deltas while preserving the UI's final text.

    Codex content.part carries a cumulative ``stream_text`` but a delta-sized
    ``part.text``; in that case the merged event is marked ``replace`` and
    carries the cumulative text so a client that receives only this event
    still renders the complete prefix.  Claude/Kimi-style delta blocks are
    appended instead.  Replace snapshots such as plan/diff simply keep the
    newest snapshot.
    """
    previous_event = previous.get("event")
    current_event = current.get("event")
    if not isinstance(previous_event, dict) or not isinstance(current_event, dict):
        return None
    merged = dict(current)
    event = dict(current_event)

    cumulative = current_event.get("stream_text")
    previous_cumulative = previous_event.get("stream_text")
    if isinstance(cumulative, str):
        # The bridge contract defines stream_text as the current cumulative
        # text.  A non-prefix reset can only be safe when the provider marked
        # the item as a replacement; otherwise keep the events separate.
        if (isinstance(previous_cumulative, str)
                and not (cumulative.startswith(previous_cumulative)
                         or previous_cumulative.startswith(cumulative))
                and not current_event.get("replace")):
            return None
        part = event.get("part")
        if isinstance(part, dict):
            part = dict(part)
            part_type = part.get("type")
            part_key = part_type if isinstance(part_type, str) else None
            if part_key and isinstance(part.get(part_key), str):
                part[part_key] = cumulative
                event["part"] = part
                event["replace"] = True
        content = event.get("content")
        if isinstance(content, str):
            event["content"] = cumulative
            event["replace"] = True
        elif isinstance(content, list):
            replaced = False
            blocks = []
            for block in content:
                if not isinstance(block, dict):
                    blocks.append(block)
                    continue
                block = dict(block)
                for content_key in ("text", "thinking", "think"):
                    if isinstance(block.get(content_key), str):
                        block[content_key] = cumulative
                        replaced = True
                        break
                blocks.append(block)
            if replaced:
                event["content"] = blocks
                event["replace"] = True
        message = event.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            blocks = []
            replaced = False
            for block in message["content"]:
                if not isinstance(block, dict):
                    blocks.append(block)
                    continue
                block = dict(block)
                for content_key in ("text", "thinking", "think"):
                    if isinstance(block.get(content_key), str):
                        block[content_key] = cumulative
                        replaced = True
                        break
                blocks.append(block)
            if replaced:
                event["message"] = {**message, "content": blocks}
                event["replace"] = True
        event["stream_text"] = cumulative
    elif current_event.get("replace"):
        # The latest event is already an authoritative item snapshot.
        merged["event"] = event
        return merged
    else:
        for key in ("content", "text", "thinking", "think", "diff"):
            old_value = previous_event.get(key)
            new_value = current_event.get(key)
            if isinstance(old_value, str) and isinstance(new_value, str):
                event[key] = old_value + new_value

        for container_key in ("message",):
            old_container = previous_event.get(container_key)
            new_container = current_event.get(container_key)
            if not isinstance(old_container, dict) or not isinstance(new_container, dict):
                continue
            old_content = old_container.get("content")
            new_content = new_container.get("content")
            appended = _append_delta_content(old_content, new_content)
            if appended is not None:
                event[container_key] = {**new_container, "content": appended}

        old_content = previous_event.get("content")
        new_content = current_event.get("content")
        appended = _append_delta_content(old_content, new_content)
        if appended is not None:
            event["content"] = appended

        old_part = previous_event.get("part")
        new_part = current_event.get("part")
        if isinstance(old_part, dict) and isinstance(new_part, dict):
            part = dict(new_part)
            part_type = part.get("type")
            part_key = part_type if isinstance(part_type, str) else None
            if part_key and isinstance(old_part.get(part_key), str) and isinstance(part.get(part_key), str):
                part[part_key] = old_part[part_key] + part[part_key]
                event["part"] = part

    merged["event"] = event
    return merged


def _is_control_event(data: dict) -> bool:
    """Classify an outbound event for overflow policy, without inspecting body."""
    return _stream_delta_key(data) is None


class _OutboundClient:
    """One bounded FIFO and sender task for one WebSocket connection."""

    def __init__(self, ws: WebSocket, kind: str):
        self.ws = ws
        self.kind = kind
        self.loop = asyncio.get_running_loop()
        self._queue: deque[_OutboundMessage] = deque()
        self._wake = asyncio.Event()
        self._sender_task: asyncio.Task | None = None
        self._accepting = True
        self._closing = False
        self.closed = False
        self.resync_required = False
        self.resync_frame_enqueued = False
        self.coalesced_deltas = 0
        self.dropped_deltas = 0
        self.dropped_control_events = 0
        self.send_failures = 0

    @property
    def queue_depth(self) -> int:
        return len(self._queue)

    def _ensure_sender(self) -> None:
        if self.closed or self._sender_task is not None and not self._sender_task.done():
            return
        self._sender_task = asyncio.create_task(
            self._sender_loop(),
            name=f"pan-ws-sender:{self.kind}",
        )

    def enqueue(
        self,
        data: dict,
        *,
        on_delivered: Callable[[], None] | None = None,
    ) -> bool:
        """Enqueue without awaiting socket I/O; return False only on eviction."""
        if self.closed or not self._accepting:
            self._record_drop(data)
            return False
        coalesce_key = _stream_delta_key(data)
        if coalesce_key is not None and self._queue:
            tail = self._queue[-1]
            if tail.coalesce_key == coalesce_key:
                merged = _merge_stream_deltas(tail.data, data)
                if merged is not None:
                    tail.data = merged
                    tail.on_delivered = on_delivered
                    self.coalesced_deltas += 1
                    _WS_DIAGNOSTICS["coalescedDeltas"] += 1
                    self._ensure_sender()
                    self._wake.set()
                    return True

        if len(self._queue) >= _WS_OUTBOUND_QUEUE_MAX:
            if coalesce_key is not None:
                self._record_drop(data)
                pending_control = None
            else:
                # A control event arriving behind deltas gets priority.  The
                # overflow handler removes queued deltas first and retains
                # this event whenever a bounded slot is available.
                pending_control = data
            self._require_resync("outbound queue full", pending_control=pending_control)
            return False

        self._queue.append(_OutboundMessage(
            data=data,
            control=_is_control_event(data),
            coalesce_key=coalesce_key,
            on_delivered=on_delivered,
        ))
        _WS_DIAGNOSTICS["enqueued"] += 1
        _WS_DIAGNOSTICS["maxQueueDepth"] = max(
            _WS_DIAGNOSTICS["maxQueueDepth"], len(self._queue),
        )
        self._ensure_sender()
        self._wake.set()
        return True

    def _record_drop(self, data_or_item: dict | _OutboundMessage) -> None:
        data = data_or_item.data if isinstance(data_or_item, _OutboundMessage) else data_or_item
        if _stream_delta_key(data) is not None:
            self.dropped_deltas += 1
            _WS_DIAGNOSTICS["droppedDeltas"] += 1
        else:
            self.dropped_control_events += 1
            _WS_DIAGNOSTICS["droppedControlEvents"] += 1

    def _require_resync(
        self, reason: str, *, pending_control: dict | None = None,
    ) -> None:
        if self.resync_required:
            self._wake.set()
            return
        self.resync_required = True
        self._accepting = False
        self._closing = True
        _WS_DIAGNOSTICS["slowClients"] += 1
        _WS_DIAGNOSTICS["resyncRequired"] += 1
        # Preserve queued control events.  Intermediate deltas are explicitly
        # discarded because the close/resync marker tells the consumer that a
        # fresh authoritative snapshot is required.
        retained: deque[_OutboundMessage] = deque()
        for item in self._queue:
            if item.coalesce_key is not None:
                self._record_drop(item)
            else:
                retained.append(item)
        self._queue = retained
        if pending_control is not None:
            if len(self._queue) < _WS_OUTBOUND_QUEUE_MAX:
                self._queue.append(_OutboundMessage(
                    data=pending_control, control=True,
                ))
            else:
                self._record_drop(pending_control)
        marker = {
            "type": "resync_required",
            "scope": self.kind,
            "reason": "slow_client_queue_full",
            "queueDepth": len(self._queue),
            "droppedDeltas": self.dropped_deltas,
            "coalescedDeltas": self.coalesced_deltas,
        }
        if len(self._queue) < _WS_OUTBOUND_QUEUE_MAX:
            self._queue.append(_OutboundMessage(data=marker, control=True))
            self.resync_frame_enqueued = True
            _WS_DIAGNOSTICS["resyncFrames"] += 1
        _detach_client(self)
        _log(
            f"[ws] slow {self.kind} client evicted: depth={len(self._queue)} "
            f"droppedDeltas={self.dropped_deltas} "
            f"droppedControlEvents={self.dropped_control_events} reason={reason}"
        )
        self._ensure_sender()
        self._wake.set()

    async def _sender_loop(self) -> None:
        while True:
            while not self._queue:
                if self._closing:
                    await self._finish_close()
                    return
                self._wake.clear()
                if self._queue:
                    break
                await self._wake.wait()
            item = self._queue.popleft()
            try:
                await asyncio.wait_for(
                    self.ws.send_json(item.data), timeout=_WS_SEND_TIMEOUT_SEC,
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                self.send_failures += 1
                self.resync_required = True
                self._accepting = False
                self._closing = True
                _WS_DIAGNOSTICS["sendFailures"] += 1
                _WS_DIAGNOSTICS["slowClients"] += 1
                _WS_DIAGNOSTICS["resyncRequired"] += 1
                self._record_drop(item)
                for queued in self._queue:
                    self._record_drop(queued)
                self._queue.clear()
                _detach_client(self)
                _log(
                    f"[ws] slow {self.kind} client send timeout: "
                    f"droppedDeltas={self.dropped_deltas}"
                )
                await self._finish_close(
                    code=_WS_RESYNC_CLOSE_CODE, reason="resync_required",
                )
                return
            except Exception:
                self.send_failures += 1
                _WS_DIAGNOSTICS["sendFailures"] += 1
                self._accepting = False
                self._closing = True
                self._record_drop(item)
                for queued in self._queue:
                    self._record_drop(queued)
                self._queue.clear()
                _detach_client(self)
                await self._finish_close(code=1011, reason="websocket send failed")
                return
            if item.on_delivered is not None:
                try:
                    item.on_delivered()
                except Exception:
                    _log("[ws] delivery callback failed")

    async def _finish_close(
        self, *, code: int = _WS_RESYNC_CLOSE_CODE,
        reason: str = "resync_required",
    ) -> None:
        if self.closed:
            return
        self.closed = True
        _detach_client(self)
        if _ws_outbound.get(self.ws) is self:
            _ws_outbound.pop(self.ws, None)
        close = getattr(self.ws, "close", None)
        if close is not None:
            try:
                await close(code=code, reason=reason)
            except Exception:
                pass

    async def close_now(self) -> None:
        """Stop a connection from its receive loop without draining it."""
        self._accepting = False
        self._closing = True
        self._queue.clear()
        _detach_client(self)
        task = self._sender_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._finish_close(code=1000, reason="connection closed")


def _detach_client(client: _OutboundClient) -> None:
    ws = client.ws
    ws_clients.discard(ws)
    agent_clients.discard(ws)
    agent_subscriptions.pop(ws, None)


def _client_channel(ws: WebSocket, kind: str) -> _OutboundClient:
    loop = asyncio.get_running_loop()
    client = _ws_outbound.get(ws)
    if client is None or client.loop is not loop or client.closed:
        if client is not None and client._sender_task is not None:
            client._sender_task.cancel()
        client = _OutboundClient(ws, kind)
        _ws_outbound[ws] = client
    return client


async def _send_ws(
    ws: WebSocket, data: dict, *,
    on_delivered: Callable[[], None] | None = None,
    kind: str | None = None,
) -> bool:
    """Queue one outbound frame; never await socket I/O in the caller."""
    if kind is None:
        kind = "agent" if ws in agent_clients else "dashboard"
    accepted = _client_channel(ws, kind).enqueue(
        data, on_delivered=on_delivered,
    )
    # Give the sender one scheduling turn.  This keeps fast in-process test
    # doubles deterministic while a real slow ``send_json`` remains entirely
    # inside its own sender task.
    await asyncio.sleep(0)
    return accepted


async def _close_slow_dashboard(ws: WebSocket) -> None:
    """Compatibility helper: evict a dashboard with an explicit resync mark."""
    client = _client_channel(ws, "dashboard")
    client._require_resync("send timeout")
    await asyncio.sleep(0)


_SUMMARY_WORKER_EVENT_TYPES = frozenset({
    "worker.spawned", "worker.restarted", "worker.reconfigured",
    "worker.status", "worker.result", "worker.destroyed", "worker.crashed",
})
_SUMMARY_SESSION_EVENT_TYPES = frozenset({
    "session.created", "session.updated", "session.renamed",
    "session.workspaceUpdated",
})


def _summary_session_get(session_id: str):
    """Read Session metadata shallowly, tolerating legacy test embedders."""
    try:
        return sess.get(session_id, load_history=False)
    except TypeError:
        # Compatibility with callers that replace sess.get with the old
        # one-argument function; production Session.get supports the keyword.
        return sess.get(session_id)


def _attach_session_summary_patch(data: dict) -> dict:
    """Attach one revisioned Session summary to low-frequency WS patches."""
    if not isinstance(data, dict):
        return data
    session_id = data.get("sessionId")
    event_type = data.get("type")
    if not isinstance(session_id, str) or not session_id:
        return data
    if event_type in _SUMMARY_WORKER_EVENT_TYPES:
        status = data.get("status")
        worker_id = data.get("workerId")
        generation = data.get("generation")
        task_id = data.get("taskId")
        task_seq = data.get("taskSeq")
        if event_type in {"worker.destroyed", "worker.crashed"}:
            status, worker_id, task_id, task_seq = None, None, None, None
        current = _summary_session_get(session_id)
        if isinstance(current, sess.Session):
            sess.update_worker_summary(
                current,
                status=status,
                worker_id=worker_id,
                generation=generation,
                task_id=task_id,
                task_seq=task_seq,
            )
    if event_type not in (_SUMMARY_WORKER_EVENT_TYPES | _SUMMARY_SESSION_EVENT_TYPES):
        return data
    current = _summary_session_get(session_id)
    if not isinstance(current, sess.Session):
        return data
    # A server-side event is emitted from the current Session object, so its
    # nested patch is newer than any caller-provided legacy summary payload.
    return {**data, "session": _session_summary(current)}


def _project_worker_event(data: dict) -> dict:
    """Project local Markdown links before exposing a Worker event to UI.

    Persisted Session history is projected by ``_api_history`` when it is
    fetched.  Live stream/result events and result replay have a separate
    outbound path, however, so leaving them raw makes a link clickable but not
    draggable until the next history refresh.  Keep the stored provider text
    unchanged and project only the copies sent to browser/agent clients.
    """
    if not isinstance(data, dict):
        return data
    session_id = data.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        return data

    event_type = data.get("type")
    if event_type == "worker.result" and isinstance(data.get("result"), str):
        projected = _project_editor_links(session_id, data["result"])
        return {**data, "result": projected} if projected != data["result"] else data

    if event_type != "worker.stream" or not isinstance(data.get("event"), dict):
        return data

    event = dict(data["event"])
    changed = False
    for key in ("content", "stream_text"):
        value = event.get(key)
        if isinstance(value, str):
            projected = _project_editor_links(session_id, value)
            if projected != value:
                event[key] = projected
                changed = True
    message = event.get("message")
    if isinstance(message, dict):
        projected_message = dict(message)
        message_content = message.get("content")
        if isinstance(message_content, str):
            projected = _project_editor_links(session_id, message_content)
            if projected != message_content:
                projected_message["content"] = projected
                changed = True
        elif isinstance(message_content, list):
            projected_blocks = list(message_content)
            blocks_changed = False
            for index, block in enumerate(projected_blocks):
                if not isinstance(block, dict) or not isinstance(block.get("text"), str):
                    continue
                projected = _project_editor_links(session_id, block["text"])
                if projected != block["text"]:
                    projected_blocks[index] = {**block, "text": projected}
                    blocks_changed = True
            if blocks_changed:
                projected_message["content"] = projected_blocks
                changed = True
        if changed:
            event["message"] = projected_message
    return {**data, "event": event} if changed else data


async def broadcast(data: dict):
    """Enqueue one event for each eligible client and return immediately.

    Socket writes happen in one sender task per connection.  A producer may
    yield once to let a fast in-process socket make progress, but it never
    awaits that socket's send or a slow client's timeout.
    """
    # Keep the persisted provider/history representation untouched.  This is
    # the common outbound boundary for live browser events and is intentionally
    # before both dashboard and agent-client fan-out.
    data = _attach_session_summary_patch(data)
    data = _project_worker_event(data)
    for ws in list(ws_clients):
        _client_channel(ws, "dashboard").enqueue(data)

    etype = data.get("type", "")
    data_session_id = data.get("sessionId")
    targets: list[WebSocket] = []
    for ws in list(agent_clients):
        sub = agent_subscriptions.setdefault(ws, AgentSubscription())
        # 事件类型过滤
        if etype not in sub.event_types and "*" not in sub.event_types:
            continue
        # worker.result 按 sessionId 过滤（若订阅了特定 session 列表）
        if etype == "worker.result" and sub.session_ids and data_session_id not in sub.session_ids:
            continue
        targets.append(ws)
    for ws in targets:
        callback = None
        # 记录已消费的 result 序号（实际 sender 成功后才推进）。
        if etype == "worker.result" and data_session_id:
            seq = data.get("taskSeq")
            if isinstance(seq, int):
                callback = lambda ws=ws, sid=data_session_id, seq=seq: _mark_agent_result_delivered(
                    ws, sid, seq,
                )
        _client_channel(ws, "agent").enqueue(data, on_delivered=callback)

    # Do not wait for any send_json.  This turn only makes fast test doubles
    # deterministic and starts sender tasks; a real socket is still isolated.
    await asyncio.sleep(0)


def _mark_agent_result_delivered(ws: WebSocket, session_id: str, seq: int) -> None:
    sub = agent_subscriptions.get(ws)
    if sub is not None:
        sub.consumed_seq[session_id] = max(
            sub.consumed_seq.get(session_id, 0), seq,
        )


def websocket_diagnostics() -> dict:
    """Return bounded live transport counters without retaining message bodies."""
    return {
        "totals": dict(_WS_DIAGNOSTICS),
        "clients": [
            {
                "kind": client.kind,
                "queueDepth": client.queue_depth,
                "coalescedDeltas": client.coalesced_deltas,
                "droppedDeltas": client.dropped_deltas,
                "droppedControlEvents": client.dropped_control_events,
                "resyncRequired": client.resync_required,
                "sendFailures": client.send_failures,
            }
            for client in tuple(_ws_outbound.values())
            if not client.closed
        ],
    }


worker.set_broadcaster(broadcast)
worker.load_worker_config()
worker.load_memory_config()


async def _replay_agent_results(ws: WebSocket, session_ids: list[str]) -> None:
    """补发 agent 尚未消费的终态结果（成功、失败、取消均不能静默丢失）。"""
    sub = agent_subscriptions.get(ws)
    if sub is None:
        sub = AgentSubscription()
        agent_subscriptions[ws] = sub
    for sid in session_ids:
        s = sess.get(sid)
        if not s or not s.last_result:
            continue
        status = s.last_result.get("status")
        if status not in _AGENT_TERMINAL_RESULT_STATUSES:
            continue
        # 补发条件：consumed_seq < latest_seq（中途断线、部分消费也补发）
        latest_seq = s.last_result.get("taskSeq")
        if latest_seq is None:
            # 旧数据未存 taskSeq：仅当完全未消费时补发（保持原有行为）
            if sub.consumed_seq.get(sid, 0) > 0:
                continue
            latest_seq = 0
        elif sub.consumed_seq.get(sid, 0) >= latest_seq:
            continue
        accepted = await _send_ws(
            ws,
            {
                "type": "worker.result",
                "workerId": "",
                "sessionId": sid,
                "status": status,
                "result": _project_editor_links(
                    sid, str(s.last_result.get("result") or "")),
                "taskSeq": latest_seq,
                "replayed": True,
            },
            on_delivered=lambda ws=ws, sid=sid, seq=latest_seq: _mark_agent_result_delivered(
                ws, sid, seq,
            ),
            kind="agent",
        )
        if not accepted:
            return


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every API request with method, path, and status code."""
    path = request.url.path
    response = await call_next(request)

    # Prevent browsers/CDNs from serving stale static assets
    if path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"

    if not any(path.startswith(p) for p in _LOG_SKIP):
        status = response.status_code
        _log(f"{request.method}  {path}  → {status}")
    return response


@app.middleware("http")
async def no_cache_api(request: Request, call_next):
    """Prevent browser/CDN from caching API responses."""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


# ── helpers ──

def _session_to_api(
    s: sess.Session,
    *,
    include_history: bool = True,
    include_raw_usage: bool = True,
    include_last_result: bool = True,
):
    """Convert Session to API response dict."""
    w = worker.find_alive_worker_by_session(s.id)
    a = get_adapter(s.adapter)
    config = load_config().get(s.adapter, {})
    ac = s.adapter_config
    last_result = s.last_result if include_last_result else None
    if include_last_result and isinstance(last_result, dict) and isinstance(last_result.get("result"), str):
        last_result = {
            **last_result,
            "result": _project_editor_links(s.id, last_result["result"]),
        }
    mcp_lock_reason = _get_mcp_locked_state(s)
    projection = sess.summary_projection(s)
    return {
        "id": s.id,
        "name": s.name,
        "adapter": s.adapter,
        "cliSessionId": s.cli_session_id,
        "model": s.model or a.default_model,
        "permissionMode": s.permission_mode or config.get("permission_mode") or None,
        # Canonical nested capability object; the flat keys below are kept as
        # deprecated aliases so old HTTP consumers keep working.
        "panAccess": {
            "restrictToManaged": s.restrict_to_managed,
            "canClaimUnmanaged": s.can_claim_unmanaged,
            "autoClaimCreated": s.auto_claim_created,
        },
        "restrictToManaged": s.restrict_to_managed,
        "canClaimUnmanaged": s.can_claim_unmanaged,
        "autoClaimCreated": s.auto_claim_created,
        "sessionTemplate": s.session_template,
        "originalPrompt": s.original_prompt,
        "handoffPrompt": s.handoff_prompt,
        "systemPrompt": s.system_prompt,  # derived, read-only compatibility view
        "alwaysThinkingEnabled": ac.get("always_thinking_enabled", False),
        "effort": ac.get("effort") or config.get("effort", ""),
        "maxThinkingTokens": ac.get("max_thinking_tokens"),
        "modelContextWindow": ac.get("model_context_window"),
        "modelAutoCompactTokenLimit": ac.get("model_auto_compact_token_limit"),
        "workdir": s.workdir,
        **({"history": _api_history(s.id, s.history)} if include_history else {}),
        **({"lastResult": last_result} if include_last_result else {}),
        "activeTaskId": s.active_task_id,
        "lastLegalWorkerState": s.last_legal_worker_state,
        **({"rawUsage": s.raw_usage} if include_raw_usage else {}),
        "totalUsage": s.total_usage,
        "usageEnrichmentPending": s.usage_enrichment_pending,
        "createdAt": s.created_at,
        "updatedAt": s.updated_at,
        "order": s.order,
        "workspaceIds": list(s.workspace_ids),
        "managed": s.managed,
        "managedBy": s.managed_by,
        "readonlySession": s.readonly_session,
        "agentLevel": sess.agent_level(s.id),
        "reportSubscriptions": sorted(s.report_subscriptions),
        "qqSubscriptions": sorted(s.qq_subscriptions),
        "notificationSettings": notifications.normalize_notification_settings(s.notification_settings),
        "workerStatus": w.status if w else None,
        "workerId": w.worker_id if w else None,
        "lastLegalWorkerState": s.last_legal_worker_state,
        "mcpEnabled": bool(ac.get("mcp_servers")),
        "mcpLocked": mcp_lock_reason is not None,
        "mcpLockReason": mcp_lock_reason,
        # Currently-enabled MCP server names (extracted from the adapter_config
        # mcp_servers list of config dicts, each carrying a "name" key).
        "mcpServers": [
            c.get("name")
            for c in (ac.get("mcp_servers") or [])
            if isinstance(c, dict) and c.get("name")
        ],
        "outputMode": ac.get("output_mode"),
        "executionModes": list(a.execution_modes),
        "gameId": s.game_id,
        # Bounded, raw summary projection.  The full view keeps its existing
        # history/attachment behavior; these fields let metadata/detail
        # consumers share the same revisioned summary contract.
        "summaryRevision": projection["revision"],
        "lastUserPreview": projection["last_user_preview"],
        "lastAssistantPreview": projection["last_assistant_preview"],
        "lastDisplayPreview": projection["last_display_preview"],
        "historyTotal": projection["history_total"],
    }


def _session_summary(s: sess.Session) -> dict:
    """Lean session dict for list summaries (A1: no history / usage).

    Fields: id/name/adapter/cliSessionId/workerStatus/updatedAt/managedBy/
    agentLevel —
    used by GET /api/sessions?summary=1 for agent context budgeting.
    cliSessionId lets MCP session_import locate the session that a reimport
    would overwrite (§8.2).

    The preview fields are a bounded in-memory/persisted projection.  This
    function must not load config.json, parse attachment links, touch the
    attachment registry, stat a local path, register an editor reference, or
    traverse the full history. ``lastMessage`` remains the old API alias for
    the raw ``lastDisplayPreview`` value.

    Since 2026-09-01: also exposes managed / mcpServers / mcpLockReason so the
    sidebar can run the "has subagent" and "is MetaAgent" special filters
    without per-session detail calls (mirrors _session_to_api).
    """
    w = worker.find_alive_worker_by_session(s.id)
    projection = sess.summary_projection(s)
    worker_state = getattr(s, "_summary_worker_state", None) or {}
    worker_status = w.status if w else worker_state.get("status")
    worker_id = w.worker_id if w else worker_state.get("worker_id")
    worker_generation = (
        getattr(w, "generation", None) if w else worker_state.get("generation")
    )
    worker_task_id = (
        worker_state.get("task_id") if worker_state else
        (getattr(w, "_current_task_id", None) if w else None)
    )
    worker_task_seq = (
        worker_state.get("task_seq") if worker_state else
        (getattr(w, "_current_seq", None) if w else None)
    )
    updated_at = projection["updated_at"] or s.updated_at
    ac = s.adapter_config
    return {
        "id": s.id,
        "name": s.name,
        "adapter": s.adapter,
        "cliSessionId": s.cli_session_id,
        "workerStatus": worker_status,
        "workerId": worker_id,
        "workerGeneration": worker_generation,
        "workerTaskId": worker_task_id,
        "workerTaskSeq": worker_task_seq,
        "lastLegalWorkerState": s.last_legal_worker_state,
        "updatedAt": updated_at,
        "summaryRevision": projection["revision"],
        "order": s.order,
        "workspaceIds": list(s.workspace_ids),
        "managedBy": s.managed_by,
        "readonlySession": s.readonly_session,
        "agentLevel": sess.agent_level(s.id, load_history=False),
        "lastUserPreview": projection["last_user_preview"],
        "lastAssistantPreview": projection["last_assistant_preview"],
        "lastDisplayPreview": projection["last_display_preview"],
        "lastMessage": projection["last_display_preview"],
        "historyTotal": projection["history_total"],
        "totalUsage": s.total_usage,
        "managed": s.managed,
        "mcpServers": [
            c.get("name")
            for c in (ac.get("mcp_servers") or [])
            if isinstance(c, dict) and c.get("name")
        ],
        "mcpLockReason": _get_mcp_locked_state(s),
        # 设置字段（供前端列表/InputRow 显示真实值，避免未打开设置弹窗时回退默认）
        # Do not resolve defaults here.  A list summary is a projection of
        # persisted Session state; configuration/default resolution belongs to
        # the detail/settings path.
        "model": s.model,
        "permissionMode": s.permission_mode,
        "alwaysThinkingEnabled": ac.get("always_thinking_enabled", False),
        "effort": ac.get("effort", ""),
        "modelContextWindow": ac.get("model_context_window"),
        "modelAutoCompactTokenLimit": ac.get("model_auto_compact_token_limit"),
        "workdir": s.workdir,
    }


def _get_mcp_locked_state(s) -> str | None:
    """Return the MCP lock reason for this session's session_template.

    Returns "always" / "never" when the template locks the MCP toggle,
    None when unlocked (no template, optional mode, or lookup failure).
    """
    if _character_manager is None or not s.session_template:
        return None
    try:
        template = _character_manager.get_session_template(s.session_template)
        if template and template.mcp_mode in ("always", "never"):
            return template.mcp_mode
    except Exception:
        pass
    return None


_NAME_RE = re.compile(r"^\S+$")  # session name: any non-whitespace chars
_MAX_NAME_LEN = 64
_MAX_TEXT_LEN = 10000


def _check_session_name(name: str) -> str | None:
    """Return error if name is empty, has invalid chars, too long, or taken."""
    if not name or not name.strip():
        return "Session name is required"
    if len(name) > _MAX_NAME_LEN:
        return f"Session name too long (max {_MAX_NAME_LEN})"
    if not _NAME_RE.match(name):
        return "Session name cannot contain spaces"
    for s in sess.list_all():
        if s.name == name:
            return f"Session name '{name}' already exists"
    return None


_WORKDIR_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

# ── file-system api constants ──
_HIDDEN_ENTRIES = {".git", ".venv", "node_modules", "__pycache__", ".codebuddy"}
_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB

# Reserved for future path restriction
_ALLOWED_WORKDIR_ROOTS: list[Path] | None = None


def _resolve_workdir(workdir_name: str, *, strict_workdir: bool = False) -> Path:
    """Resolve a workdir name to a Path.

    Relative session names retain their historical private workdir behavior.
    Absolute paths must already exist; a caller that wants a new absolute
    directory must use the explicit directory-creation endpoint after user
    confirmation. This prevents session creation from silently creating an
    arbitrary path supplied by a client.
    """
    p = Path(workdir_name)
    if p.is_absolute():
        if _ALLOWED_WORKDIR_ROOTS is not None:
            for root in _ALLOWED_WORKDIR_ROOTS:
                try:
                    p.resolve().relative_to(root.resolve())
                    break
                except ValueError:
                    pass
            else:
                raise ValueError(
                    f"Workdir {workdir_name!r} is outside allowed roots: "
                    f"{[str(r) for r in _ALLOWED_WORKDIR_ROOTS]}"
                )
        try:
            resolved = p.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError(
                f"Workdir {workdir_name!r} does not exist; confirm directory creation first"
            ) from exc
        if not resolved.is_dir():
            raise ValueError(f"Workdir {workdir_name!r} is not a directory")
        return resolved

    if strict_workdir:
        if workdir_name in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", workdir_name):
            raise ValueError(f"Invalid relative workdir path: {workdir_name!r}")
        workdir = WORKDIRS_DIR / workdir_name
        workdir.mkdir(parents=True, exist_ok=True)
        return workdir.resolve()

    # Slug name — resolve under WORKDIRS_DIR
    # 非法字符不抛错：清理成安全 slug（替换为 -），避免合法 session 名
    # （如 "fanout.config"、"v1.2"）触发 500。
    if not _WORKDIR_NAME_RE.match(workdir_name):
        cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "-", workdir_name).strip("-")
        if not cleaned:
            cleaned = "session"
        workdir_name = cleaned
    workdir = WORKDIRS_DIR / workdir_name
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _path_is_allowed(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve(strict=False)
    return any(
        resolved == root.resolve(strict=False)
        or root.resolve(strict=False) in resolved.parents
        for root in roots
    )


def _create_directory(path: str) -> Path:
    """Create one explicitly confirmed directory without expanding trust.

    Existing external workspaces remain valid workdirs. New absolute trees are
    only creatable below the configured allowlist; when no allowlist is
    configured, only Pan's own workdir root is creatable. The parent must
    already exist, so a typo cannot cause an arbitrary directory tree to be
    manufactured.
    """
    raw = path.strip()
    if not raw or "\x00" in raw or any(ord(char) < 32 for char in raw):
        raise ValueError("Invalid directory path")
    if sys.platform == "win32":
        invalid = set('<>"|?*')
        if any(char in invalid for char in raw):
            raise ValueError("Invalid directory path")
        for index, char in enumerate(raw):
            if char == ":" and not (index == 1 and raw[0].isalpha()):
                raise ValueError("Invalid directory path")
    target = Path(raw)
    if not target.is_absolute():
        return _resolve_workdir(raw, strict_workdir=True)

    if _ALLOWED_WORKDIR_ROOTS is not None:
        roots = _ALLOWED_WORKDIR_ROOTS
    else:
        roots = [WORKDIRS_DIR]
    if not _path_is_allowed(target, roots):
        raise ValueError(f"Workdir {raw!r} is outside allowed roots: {[str(root) for root in roots]}")

    try:
        if target.exists():
            if not target.is_dir():
                raise ValueError(f"Workdir {raw!r} is not a directory")
            return target.resolve(strict=True)
        parent = target.parent.resolve(strict=True)
        if not parent.is_dir() or not _path_is_allowed(parent, roots):
            raise ValueError("Parent directory is outside allowed roots or does not exist")
        target.mkdir(exist_ok=False)
        return target.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("Parent directory is outside allowed roots or does not exist") from exc
    except PermissionError as exc:
        raise PermissionError("Directory creation permission denied") from exc


def _resolve_fs_path(session_id: str, rel_path: str) -> Path:
    """Resolve a session-relative or absolute path on the Pan server.

    The web editor intentionally permits opening files anywhere on the server
    for now. A future security policy can add containment checks here without
    changing the client-side link or editor flow.
    """
    s = sess.get(session_id)
    if not s or not s.workdir:
        raise ValueError("session has no workdir")
    target = Path(rel_path)
    if not target.is_absolute():
        target = Path(s.workdir) / target
    return target.resolve()


def _resolve_attachment_source_path(session_id: str, raw_path: str) -> Path:
    """Resolve an attachment source and reject relative workdir escape."""
    target = _resolve_fs_path(session_id, raw_path)
    if not Path(raw_path).is_absolute():
        session = sess.get(session_id)
        if not session or not session.workdir:
            raise ValueError("session has no workdir")
        try:
            target.relative_to(Path(session.workdir).resolve())
        except ValueError as exc:
            raise ValueError("Attachment path escapes the Session workdir") from exc
    return target


def _rename_no_overwrite(src: Path, dst: Path) -> None:
    """Rename without replacing a target which appears concurrently.

    ``os.replace`` is intentionally not used here: it unconditionally
    replaces on POSIX and could destroy a draft created after the UI's
    advisory target-state check. Windows MoveFileEx without
    MOVEFILE_REPLACE_EXISTING and Linux renameat2(RENAME_NOREPLACE) provide
    atomic no-overwrite behavior. Platforms without a proven atomic
    no-overwrite primitive fail closed.
    """
    if src == dst:
        if not src.exists():
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(src))
        return

    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        move_file_ex = kernel32.MoveFileExW
        move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move_file_ex.restype = ctypes.c_int
        # MOVEFILE_WRITE_THROUGH; omitting MOVEFILE_REPLACE_EXISTING is the
        # no-overwrite guarantee and works for local and UNC paths.
        if not move_file_ex(str(src), str(dst), 0x8):
            error = ctypes.get_last_error()
            raise OSError(error, os.strerror(error), str(dst))
        return

    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is not None:
            renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameat2.restype = ctypes.c_int
            result = renameat2(
                -100,
                os.fsencode(src),
                -100,
                os.fsencode(dst),
                1,  # AT_FDCWD, RENAME_NOREPLACE
            )
            if result == 0:
                return
            error = ctypes.get_errno()
            # Some Linux architectures expose renameat2 but return ENOSYS at
            # runtime (for example under an older kernel/container). A
            # link+unlink fallback is not safe: another actor can remove the
            # source inode between those operations, and it cannot provide
            # the same no-overwrite guarantee for directories. Fail closed.
            if error != errno.ENOSYS:
                raise OSError(error, os.strerror(error), str(dst))
            raise OSError(
                errno.ENOTSUP,
                "atomic no-overwrite rename is unavailable on this Linux system",
                str(dst),
            )

    raise OSError(
        errno.ENOTSUP,
        "atomic no-overwrite rename is unavailable on this platform",
        str(dst),
    )


def _guarded_model(a, value) -> str | None:
    """模板 / config.json 回退 model 的宽容守卫。

    与显式请求的硬校验（validate_model → 拒绝）不同：回退值不在 adapter
    可选列表内时丢弃（交还 adapter 默认），记日志但不阻止 session 创建。
    这是既有 stale-model guard 语义（原 kimi-code/kimi-for-coding 场景）。
    """
    if not value:
        return None
    if is_valid_model(a, value):
        return value
    _log(f"[session-config] dropping invalid model {value!r} "
         f"for adapter '{a.name}' (not in supported_models)")
    return None


def _guarded_permission_mode(a, value) -> str | None:
    """模板 / config.json 回退 permissionMode 的宽容守卫（同 _guarded_model）。"""
    if not value:
        return None
    try:
        validate_permission_mode(a, value)
        return value
    except AdapterCapabilityError as exc:
        _log(f"[session-config] dropping invalid permission_mode for adapter "
             f"'{a.name}': {exc}")
        return None


def _build_session_params(
    data: dict,
    *,
    resolve_workdir: bool = True,
    strict_mcp: bool = True,
) -> dict:
    """Extract session creation parameters from request data, with defaults.

    Session config (system_prompt / adapter / model / permission_mode /
    mcp_mode / mcp_servers / pan_access capability flags) comes from a
    session_template —
    either the explicit ``sessionTemplate`` name or the built-in ``default``
    template (config.json session config). ``characterId`` only binds memory/assets.

    ``resolve_workdir=False`` skips creating a workdir under data/workdirs/ —
    used by the import endpoints whose sessions keep the external project /
    workspace path as workdir instead.

    ``strict_mcp=False`` is reserved for importing an existing external CLI
    session. A stale import template may omit MCP rather than preventing the
    external session from being recorded; normal session creation remains
    strict so a configured MCP server can never disappear silently.
    """
    _normalize_context_setting_keys(data)
    for key in ("originalPrompt", "handoffPrompt", "systemPrompt"):
        if key in data and data[key] is not None and not isinstance(data[key], str):
            raise ValueError(f"{key} must be a string or null")
    name = data.get("name", "default")
    explicit_workdir = data.get("workdir")
    workdir_name = explicit_workdir or name

    # Resolve session_template first: the template's adapter participates in
    # the final adapter resolution (explicit request > template > "cbc"), so
    # capability validation must run against the adapter the session will
    # actually use (previously a no-adapter-request + kimi-template request
    # validated against cbc but created a kimi session).
    template_name = data.get("sessionTemplate") or data.get("session_template") or None
    template = None
    if template_name:
        if _character_manager is None:
            raise AdapterCapabilityError(
                f"Unknown session template {template_name!r}: "
                "manifest catalog not loaded"
            )
        template = _character_manager.get_session_template(template_name)
        if template is None:
            available: list[str] = []
            if _character_manager._manifest_config is not None:
                available = [
                    t.name for t in _character_manager._manifest_config.session_templates
                ]
            raise AdapterCapabilityError(
                f"Unknown session template {template_name!r}. "
                f"Available templates: {', '.join(available) or '(none)'}"
            )

    adapter_name = data.get("adapter") or (template.adapter if template else "") or "cbc"
    a = resolve_adapter(adapter_name)
    config = load_config().get(adapter_name, {})

    if template is None:
        # Built-in default session_template = config.json session config.
        # 默认 stream + MCP：注入 `pan` MCP server（输出模式自动走 stream+MCP）。
        template = SessionTemplate(
            name="default",
            adapter=adapter_name,
            # Same stale-model guard as the params below: only adopt the config
            # model when it's in the adapter's selectable list.
            model=(config.get("model")
                   if config.get("model") in a.supported_models else None)
            or a.default_model,
            permission_mode=config.get("permission_mode"),
            system_prompt="",
            mcp_mode="always",
            mcp_servers=["pan"],
        )
        template_name = None  # don't record an explicit name for the default

    # Capability flags: template values are the baseline; an explicit request
    # ``panAccess`` (or legacy flat body fields) overrides them.
    # Priority: explicit field > sessionTemplate template value > default.
    pan_access = {
        "restrict_to_managed": template.restrict_to_managed,
        "can_claim_unmanaged": template.can_claim_unmanaged,
        "auto_claim_created": template.auto_claim_created,
    }
    req_pa = data.get("panAccess")
    if isinstance(req_pa, dict):
        for req_key, pa_key in [
            ("restrictToManaged", "restrict_to_managed"),
            ("canClaimUnmanaged", "can_claim_unmanaged"),
            ("autoClaimCreated", "auto_claim_created"),
        ]:
            if req_key in req_pa:
                pan_access[pa_key] = bool(req_pa[req_key])
    for req_key, pa_key in [
        ("restrictToManaged", "restrict_to_managed"),
        ("canClaimUnmanaged", "can_claim_unmanaged"),
        ("autoClaimCreated", "auto_claim_created"),
    ]:
        if req_key in data:  # legacy flat body fields (backward compat)
            pan_access[pa_key] = bool(data[req_key])

    # 显式请求的能力校验：非法值直接拒绝（结构化错误，不静默回退）。
    # 校验在所有回退值解析完成后进行，per-model effort 收窄以最终 model 为准。
    # A named manifest template is an explicit, contractual configuration: if
    # it carries a model, an adapter override that cannot run that model must
    # fail rather than silently replacing the template's requested model.
    # The synthesized default template / config.json fallback keeps the legacy
    # stale-model guard semantics (宽容守卫，不误杀 —— 例如 claude 的 config
    # model 可能不在其 best-effort builtin 列表内)。
    if data.get("model"):
        _final_model = data["model"]
    elif template_name and template is not None and template.model:
        validate_model(a, str(template.model))
        _final_model = template.model
    elif template is not None and template.model:
        # 内置 default 模板：model 已在构造时按 supported_models 守卫过
        # （config guard + adapter 自带 default 守卫），此处信任旧语义。
        _final_model = template.model
    else:
        _final_model = _guarded_model(a, config.get("model")) or a.default_model
    explicit_settings: dict = {}
    if data.get("model"):
        explicit_settings["model"] = data["model"]
    if data.get("permissionMode"):
        explicit_settings["permissionMode"] = data["permissionMode"]
    if data.get("effort"):
        explicit_settings["effort"] = data["effort"]
    if data.get("maxThinkingTokens"):
        explicit_settings["maxThinkingTokens"] = data["maxThinkingTokens"]
    if data.get("alwaysThinkingEnabled"):
        explicit_settings["alwaysThinkingEnabled"] = data["alwaysThinkingEnabled"]
    for api_key, _native_key in _CODEX_CONTEXT_SETTING_KEYS:
        if api_key in data and data[api_key] is not None:
            explicit_settings[api_key] = data[api_key]
    if explicit_settings:
        validate_session_settings(adapter_name, explicit_settings, current_model=_final_model)

    # thinking / effort 的 config 回退值做宽容守卫：adapter 不支持或值非法时
    # 丢弃回退默认（与 stale-model guard 同语义），只有显式请求才硬拒绝。
    _declared = _adapter_supported_settings(a)
    _explicit_thinking = data.get("alwaysThinkingEnabled")
    if _explicit_thinking is not None:
        _thinking = bool(_explicit_thinking)
    else:
        _config_thinking = bool(config.get("always_thinking_enabled", False))
        _thinking = _config_thinking if (_declared is None or "thinking" in _declared) else False

    _effort = data.get("effort") or ""
    if not _effort:
        _effort = config.get("effort", "") or ""
        if _effort:
            try:
                validate_effort(a, _effort, model=_final_model)
            except AdapterCapabilityError as _exc:
                _log(f"[session-config] dropping config effort for adapter "
                     f"'{a.name}': {_exc}")
                _effort = ""

    params = {
        "name": name,
        # User's explicit adapter wins, then the template's (a no-adapter
        # template parses to ""), else default "cbc". Fix: previously the user
        # selection was ignored — `template.adapter or "cbc"` overwrote a
        # chosen adapter (e.g. kimi) with "cbc" when the template had none.
        "adapter": adapter_name,
        # Priority: explicit request > template > config.json > adapter
        # default. Explicit values are hard-validated above; template/config
        # fallbacks are guard-dropped (stale/invalid → adapter default).
        "model": _final_model,
        "permission_mode": data.get("permissionMode")
        or _guarded_permission_mode(a, template.permission_mode)
        or _guarded_permission_mode(a, config.get("permission_mode"))
        or None,
        "workdir": str(_resolve_workdir(workdir_name, strict_workdir=bool(explicit_workdir))) if resolve_workdir else "",
        "adapter_config": {
            "always_thinking_enabled": _thinking,
            "effort": _effort,
            # maxThinkingTokens 不再从请求写入：没有任何 adapter 消费该字段，
            # 显式传入会在上面的能力校验中被拒绝；字段保留为 None 以兼容
            # 既有落盘结构读取。
            "max_thinking_tokens": None,
        },
        "session_template": template_name,
        "pan_access": pan_access,
        "original_prompt": data.get("originalPrompt", data.get("systemPrompt", template.system_prompt)),
        "handoff_prompt": data.get("handoffPrompt"),
        "game_id": data.get("gameId") or None,
    }
    requested_workspace_ids = data.get("workspaceIds", data.get("workspace_ids"))
    if requested_workspace_ids is not None:
        if (not isinstance(requested_workspace_ids, list)
                or not all(isinstance(wid, str) and wid for wid in requested_workspace_ids)
                or len(set(requested_workspace_ids)) != len(requested_workspace_ids)
                or not all(isinstance(wid, str) and workspaces.get(wid)
                           for wid in requested_workspace_ids)):
            raise ValueError("workspaceIds must contain existing unique workspace ids")
        params["workspace_ids"] = list(requested_workspace_ids)
    # These are deliberately added only when explicitly supplied.  In
    # particular, do not synthesize a model/default value into Session JSON.
    for api_key, native_key in _CODEX_CONTEXT_SETTING_KEYS:
        if api_key in data and data[api_key] is not None:
            params["adapter_config"][native_key] = data[api_key]
    # Optional worker execution mode ("stream" | "oneshot"); validated against
    # the adapter's execution_modes. Unset/"auto" = automatic (existing behaviour).
    raw_mode = data.get("outputMode")
    if raw_mode not in (None, "", "auto"):
        allowed = list(a.execution_modes)
        if raw_mode not in allowed:
            raise ValueError(
                f"outputMode must be one of {allowed} for adapter '{a.name}', got {raw_mode!r}"
            )
        params["adapter_config"]["output_mode"] = raw_mode

    # MCP servers come from the template (names → full configs).
    # mcp_mode decides injection: "always" injects; "optional"/"never" start
    # without servers (optional templates toggle via PATCH mcpServers).
    if template.mcp_mode == "always" and template.mcp_servers:
        try:
            params["adapter_config"]["mcp_servers"] = _resolve_mcp_server_configs(template.mcp_servers)
        except ValueError as exc:
            if not strict_mcp:
                _log(
                    f"Imported MCP server(s) {template.mcp_servers!r} unavailable; "
                    f"continuing without MCP: {exc}"
                )
                params["adapter_config"]["mcp_servers"] = []
                return params
            # Do not create a session that claims to have the default Pan MCP
            # while silently dropping its descriptor.  The API caller needs a
            # concrete configuration error so it can fix the catalog first.
            raise ValueError(
                f"Unable to configure default MCP server(s) "
                f"{template.mcp_servers!r}: {exc}"
            ) from exc

    # Character binding: memory/assets only (no session config from character).
    character_id = data.get("characterId")
    if character_id and _character_manager is not None:
        char = _character_manager.get_character(character_id)
        if char is None:
            # characterId may be a character_template name (e.g. "coc-keeper")
            # rather than a persisted character id — instantiate on first use.
            try:
                char = _character_manager.create_character(character_id)
            except ValueError:
                char = None
        if char:
            params["character_id"] = char.id

    return params


def _resolve_mcp_server_configs(server_names) -> list[dict]:
    """Resolve MCP server names to full configs from the manifest table.

    Session 的 MCP 配置只接受 manifest 中声明的 server 名称（单一事实源）：
    名称必须是字符串、不允许重复，transport/type 必须在白名单内
    （stdio/http/sse），不接受任意 command/url/env 内联描述符。
    """
    _ensure_manifest_fresh()
    if _character_manager is None or _character_manager._manifest_config is None:
        raise ValueError("MCP manifest not loaded")
    if not isinstance(server_names, list):
        raise ValueError("MCP server names must be a list")
    seen: set[str] = set()
    configs: list[dict] = []
    for name in server_names:
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid MCP server name: {name!r}")
        if name in seen:
            raise ValueError(f"Duplicate MCP server: {name!r}")
        seen.add(name)
        for srv in _character_manager._manifest_config.mcp_servers:
            if srv.name == name:
                for _key in ("transport", "type"):
                    _val = getattr(srv, _key, None)
                    if _val and str(_val) not in VALID_MCP_TRANSPORTS:
                        raise ValueError(
                            f"MCP server {name!r} has invalid {_key}: "
                            f"{_val!r}. Allowed: {', '.join(VALID_MCP_TRANSPORTS)}"
                        )
                cfg: dict = {"name": srv.name}
                if srv.command:
                    command = str(srv.command)
                    if not _mcp_command_available(command):
                        raise ValueError(
                            f"MCP server {srv.name!r} command is unavailable: {command}"
                        )
                    cfg["command"] = srv.command
                if srv.args:
                    cfg["args"] = srv.args
                if srv.env:
                    cfg["env"] = srv.env
                if srv.cwd:
                    if not Path(srv.cwd).is_dir():
                        raise ValueError(
                            f"MCP server {srv.name!r} cwd is unavailable: {srv.cwd}"
                        )
                    cfg["cwd"] = srv.cwd
                if srv.url:
                    cfg["url"] = srv.url
                if srv.transport:
                    cfg["transport"] = srv.transport
                if srv.headers:
                    cfg["headers"] = srv.headers
                if srv.type:
                    cfg["type"] = srv.type
                if not srv.command and not srv.url:
                    raise ValueError(
                        f"MCP server {srv.name!r} has no command or URL configured"
                    )
                configs.append(cfg)
                break
        else:
            raise ValueError(f"Unknown MCP server: {name!r}")
    return configs


def _mcp_command_available(command: str) -> bool:
    """Return whether a manifest stdio command can be launched.

    Absolute and path-like commands are checked directly. Bare executable
    names are resolved through PATH, allowing portable declarations such as
    ``python`` while still reporting a useful error before a Claude worker is
    spawned. This is a preflight check, not a probe that starts the MCP server.
    """
    candidate = Path(command)
    if candidate.is_absolute() or candidate.parent != Path("."):
        return candidate.is_file()
    return shutil.which(command) is not None


def _safe_adapter(adapter_name: str):
    """Return adapter by name, falling back to cbc on unknown names."""
    try:
        return get_adapter(adapter_name)
    except KeyError:
        return get_adapter("cbc")


# 进程相关字段：修改后影响 worker 进程（cbc 启动参数/执行模式），需要 respawn 才生效。
# 不含 name 等纯元数据字段。
_PROCESS_AFFECTING_FIELDS = {
    "model", "permissionMode", "alwaysThinkingEnabled", "effort",
    "maxThinkingTokens", "mcpServers", "outputMode",
    "modelContextWindow", "modelAutoCompactTokenLimit",
}

_CODEX_CONTEXT_SETTING_ALIASES = {
    # HTTP/MCP use camelCase like the rest of the session settings; accepting
    # the native names as a compatibility bridge keeps hand-authored API
    # requests and persisted migration tooling unambiguous.
    "model_context_window": "modelContextWindow",
    "model_auto_compact_token_limit": "modelAutoCompactTokenLimit",
}
_CODEX_CONTEXT_SETTING_KEYS = (
    ("modelContextWindow", "model_context_window"),
    ("modelAutoCompactTokenLimit", "model_auto_compact_token_limit"),
)


def _normalize_context_setting_keys(data: dict) -> None:
    """Normalize native snake_case aliases into the public API spelling."""
    for native_key, api_key in _CODEX_CONTEXT_SETTING_ALIASES.items():
        if native_key in data:
            data.setdefault(api_key, data[native_key])
            data.pop(native_key, None)


def _apply_session_updates(s: sess.Session, data: dict):
    """Apply model/mode/thinking/effort fields from data to a Session (in-place).

    Validate-first：所有显式设置先整体通过 adapter 能力校验，任一非法即抛
    AdapterCapabilityError 且 **不修改** session（避免半套写入的脏配置）。
    """
    _normalize_context_setting_keys(data)
    # systemPrompt was not a supported settings field. Reject effective-prompt
    # writes explicitly so a detail response cannot become a recursive baseline.
    if "systemPrompt" in data:
        raise ValueError("systemPrompt is read-only; update originalPrompt or handoffPrompt")
    for key in ("originalPrompt", "handoffPrompt"):
        if key in data and data[key] is not None and not isinstance(data[key], str):
            raise ValueError(f"{key} must be a string or null")
    _explicit = {
        key: data[key]
        for key in ("model", "permissionMode", "alwaysThinkingEnabled",
                    "effort", "maxThinkingTokens", "outputMode",
                    "modelContextWindow", "modelAutoCompactTokenLimit")
        if key in data
    }
    if _explicit:
        validate_session_settings(s.adapter, _explicit, current_model=s.model)
    if "model" in data:
        s.model = data["model"]
        # Some adapters (currently Codex) expose model-specific effort levels.
        # Switching models must not leave an incompatible effort on the session;
        # an empty value delegates to the native model default.
        if "effort" not in data:
            model_efforts = getattr(get_adapter(s.adapter), "model_efforts", {}).get(str(s.model))
            current_effort = s.adapter_config.get("effort", "")
            if model_efforts and current_effort and current_effort not in model_efforts:
                s.set_adapter_field("effort", "")
    if "permissionMode" in data:
        s.permission_mode = data["permissionMode"] or None
    if "alwaysThinkingEnabled" in data:
        s.set_adapter_field("always_thinking_enabled", data["alwaysThinkingEnabled"])
    if "effort" in data:
        s.set_adapter_field("effort", data["effort"])
    if "maxThinkingTokens" in data:
        s.set_adapter_field("max_thinking_tokens", data["maxThinkingTokens"])
    for api_key, native_key in _CODEX_CONTEXT_SETTING_KEYS:
        if api_key in data:
            s.set_adapter_field(native_key, data[api_key])
    if "mcpServers" in data:
        # forceMcp:true（UI 强制解除模板锁确认后携带）跳过 always/never 校验。
        _apply_mcp_servers(s, data["mcpServers"], force=bool(data.get("forceMcp")))
    if "panAccess" in data:
        _apply_pan_access(s, data["panAccess"])
    if "outputMode" in data:
        _apply_output_mode(s, data["outputMode"])
    if "gameId" in data:
        # Allow None / empty to clear; store string otherwise. Used by QQ
        # plugin to bind a RuleWhisper game_id to a group-scoped session so
        # LLM-driven MCP tool calls can pass it through.
        s.game_id = data["gameId"] or None
    if "notificationSettings" in data:
        if not isinstance(data["notificationSettings"], dict):
            raise ValueError("notificationSettings must be an object")
        current = notifications.normalize_notification_settings(s.notification_settings)
        current.update({k: bool(data["notificationSettings"][k])
                        for k in ("browser", "system") if k in data["notificationSettings"]})
        s.notification_settings = current
    # Apply prompts after the other settings have accepted the request. These
    # affect future fresh workers/handoffs; resumed CLI context already contains
    # its prompt. Do not imply that respawning rewrites that context.
    if "originalPrompt" in data:
        s.original_prompt = data["originalPrompt"]
    if "handoffPrompt" in data:
        s.handoff_prompt = data["handoffPrompt"]


_PAN_ACCESS_FIELDS = (
    ("restrictToManaged", "restrict_to_managed"),
    ("canClaimUnmanaged", "can_claim_unmanaged"),
    ("autoClaimCreated", "auto_claim_created"),
)


def _apply_pan_access(s: sess.Session, body) -> None:
    """Patch the session's capability flags from a camelCase ``panAccess`` dict.

    Only keys present in ``body`` are touched — a partial dict leaves the other
    flags alone (no whole-object overwrite, no back-filling of missing keys).
    """
    if not isinstance(body, dict):
        raise ValueError("panAccess must be an object of capability flags")
    for req_key, pa_key in _PAN_ACCESS_FIELDS:
        if req_key in body:
            s.pan_access[pa_key] = bool(body[req_key])


def _apply_mcp_servers(s: sess.Session, server_names, force: bool = False) -> None:
    """Set session mcp_servers by manifest server names (e.g. ["pan"]).

    Resolves names to full configs via the character manager's manifest table.
    Accepts a list of names, or None/[] to clear. mcp_servers 非空即启用
    （单一事实源），mcp_mode 的 always/never 锁在此处强制执行。
    force=True 跳过该锁（PATCH body 的 forceMcp:true 传入，UI 在用户
    明确确认后用于强制解除 never 锁）。
    """
    enabling = server_names not in (None, [], "")
    if not force and s.session_template and _character_manager is not None:
        template = _character_manager.get_session_template(s.session_template)
        if template:
            if template.mcp_mode == "always" and not enabling:
                raise ValueError(f"MCP is locked to 'always' for session template '{template.name}'. Cannot disable.")
            if template.mcp_mode == "never" and enabling:
                raise ValueError(f"MCP is locked to 'never' for session template '{template.name}'. Cannot enable.")

    if not enabling:
        s.set_adapter_field("mcp_servers", [])
        return
    if not isinstance(server_names, list):
        raise ValueError("mcpServers must be a list of server names")
    s.set_adapter_field("mcp_servers", _resolve_mcp_server_configs(server_names))


_VALID_OUTPUT_MODES = ("stream", "oneshot")


def _apply_output_mode(s: sess.Session, mode):
    """Apply outputMode: worker execution channel ("stream" | "oneshot").

    - "stream": long-running stream-json process; if MCP is also enabled,
      the process is spawned with --mcp-config (stream + MCP, cbc >= 2.137.0).
    - "oneshot": per-task one-shot cbc process (legacy MCP path).
    - None/""/"auto" clears the field -> automatic (按 adapter 默认解析，
      见 packages/core/adapters/resolution.py:resolve_execution_mode)。

    校验：mode 必须 ∈ 该 adapter 的 execution_modes，否则拒绝（避免"不可能"的
    配置，如给 one-shot-only adapter 设 stream）。adapter-architecture P1 建议 4。
    """
    if mode in (None, "", "auto"):
        s.adapter_config.pop("output_mode", None)
        return
    a = get_adapter(s.adapter)
    allowed = list(getattr(a, "execution_modes", _VALID_OUTPUT_MODES))
    if mode not in allowed:
        raise ValueError(
            f"outputMode must be one of {allowed} for adapter '{a.name}', got {mode!r}"
        )
    s.set_adapter_field("output_mode", mode)


def _open_terminal(cmd: str, cwd: str | Path) -> int:
    """Open a new terminal window running `cmd` in `cwd` (cross-platform)."""
    cwd = str(cwd) if cwd else str(Path.cwd())
    if sys.platform == "win32":
        # Strip terminal-capability vars inherited from a POSIX parent (e.g.
        # Git Bash sets TERM=xterm-256color). Left in place, cbc's ink TUI
        # mis-detects the console as a Unix terminal and renders black & white.
        env = dict(os.environ)
        env.pop("TERM", None)
        env.pop("NO_COLOR", None)
        env.pop("COLORTERM", None)
        proc = subprocess.Popen(
            ["powershell.exe", "-NoExit", "-Command", cmd],
            cwd=cwd,
            env=env,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        return proc.pid
    elif sys.platform == "darwin":
        script = f'tell app "Terminal" to do script "{cmd}"'
        proc = subprocess.Popen(["osascript", "-e", script], cwd=cwd)
        return proc.pid
    else:
        try:
            proc = subprocess.Popen(
                ["gnome-terminal", "--", "bash", "-c", cmd],
                cwd=cwd,
            )
            return proc.pid
        except FileNotFoundError:
            proc = subprocess.Popen(["xterm", "-e", cmd], cwd=cwd)
            return proc.pid


# ── Dashboard & favicon ──

from packages.core import __version__

@app.get("/api/health")
async def health():
    """Health check: process liveness + Pan version."""
    return {"status": "ok", "version": __version__}


@app.get("/api/main/restart/status")
async def api_main_restart_status():
    """Report whether the safe Windows main-service restart is available."""
    return _main_restart_status()


@app.post("/api/main/restart")
async def api_main_restart(payload: Annotated[dict | None, Body()] = None):
    """Accept a durable detached restart of this Pan instance.

    Returning before the supervisor stops this process is essential: waiting
    for stop/start inside this request would turn the expected disconnect into
    an HTTP failure in the browser.  ``restart_pan.ps1`` owns the subsequent
    stop/start chain and uses this checkout's scripts only.
    """
    global _main_restart_pending, _main_restart_request_id

    options = _parse_main_lifecycle_options(payload, "restart")

    status = _main_restart_status()
    if not status["available"]:
        return {
            "ok": False,
            "status": "disabled",
            "error": status.get("reason", "main restart is unavailable"),
        }
    if status.get("pending"):
        return {
            "ok": False,
            "status": "busy",
            "pending": True,
            "error": "Pan main-service restart is already scheduled",
            "requestId": status.get("requestId"),
            "jobId": status.get("jobId"),
            "phase": status.get("phase"),
        }

    registry_root = _main_restart_registry_root()
    port = status["port"]
    old_pid = main_lifecycle.listener_owner(port)
    old_pid_created_at = main_lifecycle.process_create_time(old_pid)
    request_id = uuid.uuid4().hex
    try:
        job = background_jobs.create_service_job(
            request_id=request_id, operation="restart", root=str(_PROJECT_DIR), port=port,
            old_pid=old_pid, old_pid_created_at=old_pid_created_at,
            registry_root=registry_root, options=options,
        )
    except background_jobs.ServiceJobBusy as exc:
        existing = exc.job
        return {
            "ok": False, "status": "busy", "pending": True,
            "error": "Pan main-service restart is already scheduled",
            "requestId": existing.get("requestId"), "jobId": existing.get("jobId"),
            "phase": existing.get("phase"),
        }
    except (OSError, ValueError) as exc:
        return {"ok": False, "status": "error", "error": f"failed to record Pan restart: {exc}"}

    # Keep these names for older in-process integrations; durable registry
    # state is authoritative for status and duplicate detection.
    with _main_restart_lock:
        _main_restart_pending = True
        _main_restart_request_id = request_id

    try:
        _launch_main_restart_supervisor(request_id)
    except (OSError, ValueError) as exc:
        background_jobs.transition_service_job(
            job["jobId"], "failed", registry_root=registry_root,
            error=f"failed to spawn restart supervisor: {exc}",
        )
        _clear_main_restart_state(request_id)
        return {
            "ok": False,
            "status": "error",
            "error": f"failed to schedule Pan restart: {exc}",
            "jobId": job["jobId"],
        }
    return {
        "ok": True,
        "status": "scheduled",
        "accepted": True,
        "phase": "requested",
        "jobId": job["jobId"],
        "message": "Pan main-service restart scheduled",
        "requestId": request_id,
    }


async def _perform_main_exit(request_id: str) -> None:
    """Stop Workers, persist confirmed legal offline states, then stop Pan."""
    global _main_exit_pending, _main_exit_stage, _main_exit_error
    registry_root = _main_restart_registry_root()
    job = background_jobs.find_service_job(request_id, registry_root)
    if not job or job.get("operation") != "exit":
        return
    with _main_exit_lock:
        if _main_exit_request_id != request_id:
            return
        _main_exit_stage = "stopping_workers"
    try:
        background_jobs.transition_service_job(
            job["jobId"], "stopping_workers", registry_root=registry_root,
        )
    except (OSError, ValueError) as exc:
        _log(f"[main-exit] failed to record worker-stop phase: {exc}")
        return
    try:
        await worker.shutdown_all(mark_legal_offline=True)
    except Exception as exc:  # still hand off to the stop-only supervisor
        _log(f"[main-exit] worker shutdown failed: {exc}")
        with _main_exit_lock:
            _main_exit_error = str(exc)

    with _main_exit_lock:
        if _main_exit_request_id != request_id:
            return
        _main_exit_stage = "stopping_service"
    try:
        background_jobs.transition_service_job(
            job["jobId"], "stopping_service", registry_root=registry_root,
            **({"error": _main_exit_error} if _main_exit_error else {}),
        )
    except (OSError, ValueError) as exc:
        _log(f"[main-exit] failed to record service-stop phase: {exc}")
        return
    try:
        _launch_main_exit_supervisor(request_id)
    except (OSError, ValueError) as exc:
        background_jobs.transition_service_job(
            job["jobId"], "failed", registry_root=registry_root, error=str(exc),
        )
        with _main_exit_lock:
            _main_exit_pending = False
            _main_exit_stage = "error"
            _main_exit_error = f"failed to schedule Pan exit: {exc}"
        _log(f"[main-exit] supervisor launch failed: {exc}")
        return
    with _main_exit_lock:
        _main_exit_stage = "scheduled"


@app.get("/api/main/exit/status")
async def api_main_exit_status():
    """Report stop-only Pan exit availability and pending stage."""
    return _main_exit_status()


@app.post("/api/main/exit")
async def api_main_exit(payload: Annotated[dict | None, Body()] = None):
    """Schedule a legal, stop-only shutdown of this Pan instance.

    The HTTP response is returned before Worker draining and service stop.  No
    health-recovery wait follows: this operation intentionally makes the
    service unavailable.
    """
    global _main_exit_pending, _main_exit_request_id, _main_exit_stage, _main_exit_error

    options = _parse_main_lifecycle_options(payload, "exit")

    status = _main_exit_status()
    if not status["available"]:
        return {
            "ok": False,
            "status": "disabled",
            "error": status.get("reason", "main service exit is unavailable"),
        }
    restart_status = _main_restart_status()
    if restart_status.get("pending"):
        return {
            "ok": False,
            "status": "busy",
            "pending": True,
            "error": "Pan main-service restart is already scheduled",
            "requestId": restart_status.get("requestId"),
            "jobId": restart_status.get("jobId"),
        }
    with _main_exit_lock:
        if _main_exit_pending:
            return {
                "ok": False,
                "status": "busy",
                "pending": True,
                "error": "Pan main-service exit is already scheduled",
                "requestId": _main_exit_request_id,
            }
    registry_root = _main_restart_registry_root()
    port = status.get("port", _main_restart_port())
    old_pid = main_lifecycle.listener_owner(port)
    old_pid_created_at = main_lifecycle.process_create_time(old_pid)
    request_id = uuid.uuid4().hex
    try:
        job = background_jobs.create_service_job(
            request_id=request_id, operation="exit", root=str(_PROJECT_DIR), port=port,
            old_pid=old_pid, old_pid_created_at=old_pid_created_at,
            registry_root=registry_root, options=options,
        )
    except background_jobs.ServiceJobBusy as exc:
        existing = exc.job
        return {
            "ok": False, "status": "busy", "pending": True,
            "error": "Pan main-service exit is already scheduled",
            "requestId": existing.get("requestId"), "jobId": existing.get("jobId"),
            "phase": existing.get("phase"),
        }
    except (OSError, ValueError) as exc:
        return {"ok": False, "status": "error", "error": f"failed to record Pan exit: {exc}"}

    with _main_exit_lock:
        _main_exit_pending = True
        _main_exit_request_id = request_id
        _main_exit_stage = "scheduled"
        _main_exit_error = None

    # Close spawn/recovery/queue gates synchronously, before the background
    # task gets its first scheduling opportunity.
    worker.begin_shutdown()
    asyncio.create_task(
        _perform_main_exit(request_id),
        name="pan-main-exit",
    )
    return {
        "ok": True,
        "status": "scheduled",
        "accepted": True,
        "phase": "requested",
        "jobId": job["jobId"],
        "message": "Pan main-service exit scheduled; this service will stop",
        "requestId": request_id,
    }


def _directory_roots() -> list[Path]:
    """Return navigable filesystem roots on the machine running Pan."""
    if sys.platform == "win32":
        # GetLogicalDrives avoids presenting disconnected or nonexistent drive
        # letters.  Keep this behind a helper so the API remains testable on
        # non-Windows hosts.
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return [Path(f"{chr(65 + i)}:\\") for i in range(26) if mask & (1 << i)]
    return [Path("/")]


def _resolve_directory(path: str | None) -> Path | None:
    if not path or not path.strip():
        return None
    # strict=True ensures the response never claims a path exists when it does
    # not. Path.resolve also normalizes separators and removes dot segments.
    return Path(path.strip()).resolve(strict=True)


def _attachment_session_dir(session_id: str) -> Path:
    """Return a filesystem-safe, session-isolated attachment directory."""
    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id).strip("._") or "session"
    return ATTACHMENTS_DIR / f"{safe_id[:80]}-{session_hash}"


def _attachment_filename(raw_name: str | None) -> str:
    """Return the original basename for display, never as a storage path.

    The browser may send a URL-encoded name and, on some browsers, a legacy
    fakepath.  Keep the user's basename (including spaces, Unicode and
    Markdown punctuation) for the link label, while removing only path/control
    characters that cannot safely be displayed as one filename.
    """
    name = unquote(raw_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if ord(c) >= 32 and c not in "\x7f")
    return name if name.strip(" .") else "attachment"


def _attachment_storage_filename(display_name: str) -> str:
    """Create an opaque storage filename, retaining only a safe extension."""
    suffix = Path(display_name).suffix
    suffix = re.sub(r"[^A-Za-z0-9._-]", "", suffix)[:32]
    return f"upload_{uuid.uuid4().hex}{suffix}"


_ATTACHMENT_ID_RE = re.compile(
    r"(?:upload_[A-Za-z0-9]{32}(?:\.[A-Za-z0-9._-]{1,32})?|att_[A-Za-z0-9]{32})"
)


def _attachment_registry_path(session_id: str) -> Path:
    """Return the durable, session-scoped attachment metadata sidecar."""
    return _attachment_session_dir(session_id) / ".attachments.json"


def _read_attachment_registry(session_id: str) -> dict[str, dict]:
    try:
        raw = json.loads(_attachment_registry_path(session_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): value for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, dict)
    }


def _write_attachment_registry(session_id: str, registry: dict[str, dict]) -> None:
    target = _attachment_registry_path(session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def _attachment_record(session_id: str, attachment_id: str) -> dict | None:
    """Resolve an id without accepting client supplied href/path metadata.

    Old uploads used their opaque storage filename as ``attachmentId`` and did
    not have a sidecar.  They remain readable through the conservative
    filename/path fallback; all new server-file references are sidecar-backed.
    """
    if not isinstance(attachment_id, str) or not _ATTACHMENT_ID_RE.fullmatch(attachment_id):
        return None
    record = _read_attachment_registry(session_id).get(attachment_id)
    if record is not None:
        return dict(record)
    if attachment_id.startswith("upload_"):
        root = _attachment_session_dir(session_id).resolve()
        target = (root / attachment_id).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        if target.is_file():
            return {
                "source": "upload",
                "displayName": attachment_id,
                "storageFilename": attachment_id,
                "path": str(target),
                "size": target.stat().st_size,
                "mimeType": mimetypes.guess_type(attachment_id)[0] or "application/octet-stream",
                "completed": True,
            }
    # A message may be dragged from another Session.  The opaque id is the
    # bearer reference; the registry owner, never a browser-supplied path, is
    # the authority for the source file.  The queue boundary imports this
    # record into the target Session before persisting the message reference.
    try:
        candidates = list(ATTACHMENTS_DIR.iterdir())
    except OSError:
        candidates = []
    for directory in candidates:
        if not directory.is_dir():
            continue
        try:
            registry = json.loads(
                (directory / ".attachments.json").read_text(encoding="utf-8")
            )
        except (FileNotFoundError, OSError, ValueError, TypeError):
            registry = {}
        if isinstance(registry, dict):
            record = registry.get(attachment_id)
            if isinstance(record, dict):
                return dict(record)
    return None


def _attachment_owner_dir(attachment_id: str) -> Path | None:
    """Find an id in another session directory for a useful mismatch error."""
    if not _ATTACHMENT_ID_RE.fullmatch(attachment_id or ""):
        return None
    try:
        candidates = list(ATTACHMENTS_DIR.iterdir())
    except OSError:
        return None
    for directory in candidates:
        if not directory.is_dir():
            continue
        registry_path = directory / ".attachments.json"
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            registry = {}
        if isinstance(registry, dict) and attachment_id in registry:
            return directory
        if attachment_id.startswith("upload_") and (directory / attachment_id).is_file():
            return directory
    return None


def _register_attachment(session_id: str, attachment_id: str, record: dict) -> None:
    registry = _read_attachment_registry(session_id)
    registry[attachment_id] = {
        **record,
        "completed": record.get("completed", True),
        "sessionId": session_id,
    }
    _write_attachment_registry(session_id, registry)


def _attachment_href(session_id: str, storage_filename: str) -> str:
    """Build the only public href accepted for an uploaded attachment."""
    return (
        f"/api/attachments/{quote(storage_filename, safe='')}"
        f"?session_id={quote(session_id, safe='')}"
    )


def _attachment_ref_href(session_id: str, attachment_id: str) -> str:
    """Build an opaque download href that never embeds a filesystem path."""
    return (
        f"/api/attachments/ref/{quote(attachment_id, safe='')}"
        f"?session_id={quote(session_id, safe='')}"
    )


def _editor_attachment_href(
    session_id: str, attachment_id: str, line: int | None = None,
    end_line: int | None = None,
) -> str:
    href = _attachment_ref_href(session_id, attachment_id).replace(
        "/api/attachments/ref/", "/api/attachments/editor/", 1)
    if line is not None:
        suffix = f"#L{line}" if end_line is None else f"#L{line}-L{end_line}"
        href += suffix
    return href


def _fs_download_href(session_id: str, path: str) -> str:
    """Build a safe download href for an existing server-side file."""
    return (
        "/api/fs/read?"
        f"session_id={quote(session_id, safe='')}&"
        f"path={quote(path, safe='')}&download=1"
    )


def _markdown_label(display_name: str) -> str:
    """Escape Markdown label punctuation without changing rendered text."""
    return re.sub(r"([\\\[\]\(\)])", r"\\\1", display_name)


def _attachment_markdown(display_name: str, href: str) -> str:
    """Return a standard Markdown link for a trusted, server-built href."""
    return f"[{_markdown_label(display_name)}]({href})"


_LEGACY_ATTACHMENT_RE = re.compile(r'@"([^"\r\n]+)"')
_MESSAGE_ATTACHMENT_HREF_RE = re.compile(
    r"\[(?:\\.|[^\]\\\r\n])*\]\((?P<href>/api/(?:attachments/(?:ref/|editor/|[^)\s]+)|fs/read\?[^)\s]+))\)"
)


def _legacy_attachment_href(session_id: str, raw_path: str) -> str:
    """Map an old absolute attachment path to a validated download route."""
    try:
        target = Path(raw_path).resolve()
        attachment_root = _attachment_session_dir(session_id).resolve()
        relative = target.relative_to(attachment_root)
        if len(relative.parts) == 1 and relative.name:
            return _attachment_href(session_id, relative.name)
    except (OSError, RuntimeError, ValueError):
        pass
    # This preserves the existing fs/read permission and path validation for
    # older server-file attachments whose original name was not persisted.
    return _fs_download_href(session_id, raw_path)


def _normalize_legacy_attachment_links(session_id: str, content: str) -> str:
    """Make pre-Metadata ``@\"path\"`` history entries renderable Markdown."""
    def replace(match: re.Match[str]) -> str:
        raw_path = match.group(1)
        display_name = _attachment_filename(raw_path)
        return _attachment_markdown(
            display_name,
            _legacy_attachment_href(session_id, raw_path),
        )

    return _LEGACY_ATTACHMENT_RE.sub(replace, content)


_MARKDOWN_LINK_RE = re.compile(
    r"(?P<label>\[(?:\\.|[^\]\\\r\n])*\])\((?P<href>[^)\s]+)\)"
)


def _parse_editor_destination(raw_href: str) -> tuple[str, int | None, int | None] | None:
    """Parse a local Markdown destination without accepting web URLs."""
    hash_index = raw_href.find("#")
    raw_path = unquote(raw_href if hash_index < 0 else raw_href[:hash_index])
    fragment = "" if hash_index < 0 else unquote(raw_href[hash_index + 1:])
    if not raw_path or raw_href.startswith("#"):
        return None
    lower = raw_path.lower()
    if lower.startswith("file://"):
        uri = urlsplit(raw_path)
        if uri.netloc and uri.netloc.lower() != "localhost":
            path = f"//{uri.netloc}{uri.path}"
        else:
            path = uri.path[1:] if re.match(r"^/[A-Za-z]:[\\/]", uri.path) else uri.path
    else:
        if re.match(r"^[A-Za-z][A-Za-z\d+.-]*:", raw_path) and not re.match(
            r"^[A-Za-z]:[\\/]", raw_path
        ):
            return None
        path = raw_path
    path = path.replace("\\", "/")
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]

    line = end_line = None
    colon_match = re.match(r"^(.*):(\d+)(?:-(\d+))?$", path)
    if colon_match and not re.match(r"^[A-Za-z]$", colon_match.group(1)):
        line = int(colon_match.group(2))
        end_line = int(colon_match.group(3)) if colon_match.group(3) else None
        if end_line is not None and end_line < line:
            line = end_line = None
        else:
            path = colon_match.group(1)
    fragment_match = re.fullmatch(r"L(\d+)(?:-L(\d+))?", fragment, re.IGNORECASE)
    if fragment_match:
        fragment_line = int(fragment_match.group(1))
        fragment_end = int(fragment_match.group(2)) if fragment_match.group(2) else None
        if fragment_line > 0 and (fragment_end is None or fragment_end >= fragment_line):
            line, end_line = fragment_line, fragment_end
    if line is not None and line < 1:
        line = end_line = None
    return path, line, end_line


def _editor_reference_id(session_id: str, path: str, line: int | None, end_line: int | None) -> str:
    canonical_path = str(Path(path).resolve())
    registry = _read_attachment_registry(session_id)
    for attachment_id, record in registry.items():
        if (
            isinstance(record, dict)
            and record.get("source") == "server_file"
            and str(Path(str(record.get("path", ""))).resolve()) == canonical_path
            and record.get("line") == line
            and record.get("endLine") == end_line
            and record.get("completed", True) is True
        ):
            return attachment_id
    attachment_id = "att_" + uuid.uuid4().hex
    _register_attachment(session_id, attachment_id, {
        "source": "server_file",
        "displayName": _attachment_filename(Path(canonical_path).name),
        "path": canonical_path,
        "size": Path(canonical_path).stat().st_size,
        "mimeType": mimetypes.guess_type(canonical_path)[0],
        "line": line,
        "endLine": end_line,
    })
    return attachment_id


def _project_editor_links(session_id: str, content: str) -> str:
    """Give existing local Markdown links opaque editor/download identities.

    This is intentionally best-effort for old history: an existing file can
    be upgraded to a draggable opaque reference; missing or ambiguous legacy
    links remain ordinary editor links and retain click-only compatibility.
    """
    def replace(match: re.Match[str]) -> str:
        parsed = _parse_editor_destination(match.group("href"))
        if parsed is None:
            return match.group(0)
        raw_path, line, end_line = parsed
        if raw_path.startswith("/api/"):
            return match.group(0)
        try:
            target = _resolve_attachment_source_path(session_id, raw_path)
            if not target.is_file():
                return match.group(0)
            attachment_id = _editor_reference_id(session_id, str(target), line, end_line)
            return f"{match.group('label')}({_editor_attachment_href(session_id, attachment_id, line, end_line)})"
        except (OSError, RuntimeError, ValueError):
            return match.group(0)

    return _MARKDOWN_LINK_RE.sub(replace, content)


def _attachment_reference_error(session_id: str, href: str) -> dict | None:
    """Validate one server-generated attachment href for a target session.

    The browser still submits the established Markdown text protocol.  This
    check makes that protocol authoritative at the queue boundary: a client
    cannot submit another session's upload or a file that disappeared after
    the UI's pre-send check.  Ordinary external Markdown links and legacy
    ``@"path"`` markers are intentionally outside this validator.
    """
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return {"code": "invalid_attachment_reference", "message": "Attachment link is invalid"}
    if parsed.fragment and not parsed.path.startswith("/api/attachments/editor/"):
        return {"code": "invalid_attachment_reference", "message": "Attachment link is invalid"}
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query.get("session_id", [None]) != [session_id]:
        return {
            "code": "attachment_session_mismatch",
            "message": "Attachment belongs to another session",
        }

    if parsed.path.startswith("/api/attachments/"):
        if parsed.path.startswith("/api/attachments/ref/"):
            attachment_id = unquote(parsed.path.rsplit("/", 1)[-1])
            return _attachment_id_error(
                session_id, attachment_id, allow_cross_session=True,
            )
        if parsed.path.startswith("/api/attachments/editor/"):
            attachment_id = unquote(parsed.path.rsplit("/", 1)[-1])
            return _attachment_id_error(
                session_id, attachment_id, allow_cross_session=True,
            )
        storage_filename = unquote(parsed.path.rsplit("/", 1)[-1])
        if not re.fullmatch(
            r"upload_[A-Za-z0-9]{32}(?:\.[A-Za-z0-9._-]{1,32})?",
            storage_filename,
        ):
            return {"code": "invalid_attachment_reference", "message": "Attachment link is invalid"}
        root = _attachment_session_dir(session_id).resolve()
        target = (root / storage_filename).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return {"code": "invalid_attachment_reference", "message": "Attachment link is invalid"}
        if not target.is_file():
            return {
                "code": "attachment_not_found",
                "message": "Attachment is no longer available",
            }
        return None

    if parsed.path == "/api/fs/read":
        if query.get("download", [None]) != ["1"] or query.get("path", [None])[0] is None:
            return {"code": "invalid_attachment_reference", "message": "Attachment link is invalid"}
        try:
            target = _resolve_attachment_source_path(session_id, query["path"][0])
        except (ValueError, OSError):
            return {"code": "invalid_attachment_reference", "message": "Attachment link is invalid"}
        if not target.is_file():
            return {
                "code": "attachment_not_found",
                "message": "Attachment is no longer available",
            }
        return None

    return {"code": "invalid_attachment_reference", "message": "Attachment link is invalid"}


def _validate_message_attachment_references(session_id: str, text: str) -> dict | None:
    """Return the first invalid internal attachment reference in message text."""
    # Preserve the queue endpoint's established session-not-found response;
    # worker.enqueue_user_message remains authoritative for that case.
    if sess.get(session_id) is None:
        return None
    for match in _MESSAGE_ATTACHMENT_HREF_RE.finditer(text):
        error = _attachment_reference_error(session_id, match.group("href"))
        if error is not None:
            return error
    return None


# DEC-002 separates two cross-Session rules.  A pending chip or inline
# composer node is Session-owned, so a queued structured part must not adopt an
# opaque id that another Session registered.  A server-file reference (the
# editor/正文 link projection of a real server file) may still be dragged into
# another Session: the target imports a registry receipt and no bytes are
# copied.  Client uploads own their bytes inside the registering Session, so
# only ``server_file`` keeps the reuse path.
_CROSS_SESSION_STRUCTURED_SOURCES = frozenset({"server_file"})


def _attachment_id_error(
    session_id: str,
    attachment_id: str,
    *,
    allow_cross_session: bool = False,
    cross_session_sources: frozenset[str] | None = None,
) -> dict | None:
    """Validate one opaque AttachmentRef at the session boundary.

    ``cross_session_sources`` narrows a cross-Session allowance to the registry
    ``source`` values that may be reused.  ``None`` keeps the historical
    "any source" allowance used by the read-only download/editor endpoints.
    """
    if not isinstance(attachment_id, str) or not _ATTACHMENT_ID_RE.fullmatch(attachment_id):
        return {"code": "invalid_attachment_id", "message": "Attachment id is invalid"}
    record = _attachment_record(session_id, attachment_id)
    if record is None:
        owner = _attachment_owner_dir(attachment_id)
        if owner is not None and owner != _attachment_session_dir(session_id).resolve():
            return {
                "code": "attachment_session_mismatch",
                "message": "Attachment belongs to another session",
            }
        return {"code": "attachment_not_found", "message": "Attachment is no longer available"}
    owner_session_id = record.get("sessionId")
    if owner_session_id not in (None, session_id):
        reusable_source = (
            cross_session_sources is None
            or record.get("source") in cross_session_sources
        )
        if not allow_cross_session or not reusable_source:
            return {
                "code": "attachment_session_mismatch",
                "message": "Attachment belongs to another session",
            }
    if record.get("completed") is not True:
        return {"code": "attachment_incomplete", "message": "Attachment upload is not complete"}
    try:
        target = Path(str(record.get("path", ""))).resolve()
        if not target.is_file():
            return {"code": "attachment_not_found", "message": "Attachment is no longer available"}
    except (OSError, RuntimeError, ValueError):
        return {"code": "attachment_not_found", "message": "Attachment is no longer available"}
    return None


def _import_attachment_reference(session_id: str, attachment_id: str, record: dict) -> None:
    """Persist a source-owned opaque reference in the target registry.

    No bytes are copied.  Keeping a target-side receipt makes the target
    message independently recoverable while retaining the source owner for
    href generation and permission/stale checks.
    """
    owner = record.get("sessionId")
    if owner in (None, session_id):
        return
    registry = _read_attachment_registry(session_id)
    if attachment_id in registry:
        return
    registry[attachment_id] = {
        **record,
        "sessionId": session_id,
        "sourceSessionId": owner,
        "sourceAttachmentId": attachment_id,
    }
    _write_attachment_registry(session_id, registry)


def _canonical_attachment_ref(session_id: str, attachment_id: str) -> dict | None:
    """Return server-owned metadata used by structured parts and Markdown fallback."""
    if _attachment_id_error(session_id, attachment_id) is not None:
        return None
    record = _attachment_record(session_id, attachment_id)
    if record is None:
        return None
    source = record.get("source") if record.get("source") in {"upload", "server_file"} else "upload"
    path = str(record.get("path", ""))
    owner_session_id = str(record.get("sourceSessionId") or record.get("sessionId") or session_id)
    href = (
        _attachment_href(owner_session_id, str(record.get("storageFilename")))
        if source == "upload" and record.get("storageFilename")
        else _attachment_ref_href(owner_session_id, attachment_id)
    )
    display_name = _attachment_filename(record.get("displayName"))
    return {
        "type": "attachment",
        "attachmentId": attachment_id,
        "displayName": display_name,
        "mimeType": record.get("mimeType") or mimetypes.guess_type(display_name)[0] or "application/octet-stream",
        "size": int(record.get("size", 0) or 0),
        "source": source,
        "href": href,
        **({"line": record["line"]} if isinstance(record.get("line"), int) else {}),
        **({"endLine": record["endLine"]} if isinstance(record.get("endLine"), int) else {}),
        # Internal-only field consumed by packages.core.worker.  API/history
        # serializers remove it before returning data to the browser.
        "__serverPath": str(Path(path).resolve()),
    }


def _normalize_message_parts(session_id: str, raw_parts) -> tuple[list[dict] | None, str | None, dict | None]:
    """Validate parts and generate the adapter-compatible Markdown fallback.

    Client supplied labels, hrefs and paths are deliberately ignored.  Only the
    opaque id is authoritative; the registry supplies all attachment metadata.
    A structured part never adopts another Session's upload: only a
    server-file reference keeps the cross-Session reuse path.
    """
    if not isinstance(raw_parts, list) or not raw_parts or len(raw_parts) > 512:
        return None, None, {"code": "invalid_parts", "message": "parts must be a non-empty array"}
    normalized: list[dict] = []
    fallback: list[str] = []
    for part in raw_parts:
        if not isinstance(part, dict) or not isinstance(part.get("type"), str):
            return None, None, {"code": "invalid_parts", "message": "message part is invalid"}
        kind = part["type"]
        if kind == "text":
            value = part.get("text", part.get("value"))
            if not isinstance(value, str):
                return None, None, {"code": "invalid_parts", "message": "text part must contain text"}
            normalized.append({"type": "text", "text": value})
            fallback.append(value)
            continue
        if kind != "attachment":
            return None, None, {"code": "invalid_parts", "message": "unknown message part type"}
        attachment_id = part.get("attachmentId")
        error = _attachment_id_error(
            session_id, attachment_id,
            allow_cross_session=True,
            cross_session_sources=_CROSS_SESSION_STRUCTURED_SOURCES,
        )
        if error is not None:
            return None, None, error
        record = _attachment_record(session_id, attachment_id)
        if record is None:
            return None, None, {"code": "attachment_not_found", "message": "Attachment is no longer available"}
        _import_attachment_reference(session_id, attachment_id, record)
        canonical = _canonical_attachment_ref(session_id, attachment_id)
        if canonical is None:
            return None, None, {"code": "attachment_not_found", "message": "Attachment is no longer available"}
        normalized.append({key: value for key, value in canonical.items() if key != "href"})
        fallback.append(_attachment_markdown(canonical["displayName"], canonical["href"]))
    return normalized, "".join(fallback), None


def _normalize_text_attachment_parts(
    session_id: str, content: str,
) -> tuple[list[dict] | None, str | None, dict | None]:
    """Upgrade legacy internal Markdown links to durable parts.

    This keeps old text/Markdown callers compatible while ensuring a Worker
    never receives an API href as the file target.  External links and all
    ordinary prose remain text parts; each validated Pan attachment becomes a
    registry-backed part with the same internal path projection as a new
    structured request.
    """
    content = _normalize_legacy_attachment_links(session_id, content)
    matches = list(_MESSAGE_ATTACHMENT_HREF_RE.finditer(content))
    if not matches:
        return None, content, None
    normalized: list[dict] = []
    fallback: list[str] = []
    cursor = 0
    for match in matches:
        prefix = content[cursor:match.start()]
        if prefix:
            normalized.append({"type": "text", "text": prefix})
            fallback.append(prefix)
        href = match.group("href")
        error = _attachment_reference_error(session_id, href)
        if error is not None:
            return None, None, error
        parsed = urlsplit(href)
        if parsed.path == "/api/fs/read":
            raw_path = parse_qs(parsed.query, keep_blank_values=True).get("path", [""])[0]
            try:
                target = _resolve_attachment_source_path(session_id, raw_path)
            except (OSError, RuntimeError, ValueError):
                return None, None, {
                    "code": "attachment_not_found",
                    "message": "Attachment is no longer available",
                }
            attachment_id = _editor_reference_id(session_id, str(target), None, None)
        else:
            attachment_id = unquote(parsed.path.rsplit("/", 1)[-1])
        canonical = _canonical_attachment_ref(session_id, attachment_id)
        if canonical is None:
            return None, None, {
                "code": "attachment_not_found",
                "message": "Attachment is no longer available",
            }
        normalized.append({key: value for key, value in canonical.items() if key != "href"})
        fallback.append(_attachment_markdown(canonical["displayName"], canonical["href"]))
        cursor = match.end()
    suffix = content[cursor:]
    if suffix:
        normalized.append({"type": "text", "text": suffix})
        fallback.append(suffix)
    return normalized, "".join(fallback), None


def _api_history(session_id: str, history: list[dict]) -> list[dict]:
    """Serialize history with a compatibility view for old attachment text."""
    normalized: list[dict] = []
    for message in history:
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            if isinstance(message, dict) and isinstance(message.get("parts"), list):
                normalized.append({
                    **message,
                    "parts": [
                        {key: value for key, value in part.items() if key != "__serverPath"}
                        for part in message["parts"] if isinstance(part, dict)
                    ],
                })
            else:
                normalized.append(message)
            continue
        content = _normalize_legacy_attachment_links(session_id, message["content"])
        content = _project_editor_links(session_id, content)
        safe_message = {**message, "content": content}
        if isinstance(message.get("parts"), list):
            safe_message["parts"] = [
                {key: value for key, value in part.items() if key != "__serverPath"}
                for part in message["parts"] if isinstance(part, dict)
            ]
        normalized.append(safe_message)
    return normalized


@app.post("/api/sessions/{session_id}/attachments")
async def upload_session_attachment(session_id: str, request: Request, filename: str | None = None):
    """Persist one browser-uploaded file under the target session directory.

    The request body is the raw file bytes.  ``X-Filename`` is preferred so
    names containing non-ASCII characters survive URL handling; the query
    parameter remains a simple fallback for clients that cannot set headers.
    """
    if sess.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    raw_name = request.headers.get("x-filename") or filename
    display_name = _attachment_filename(raw_name)
    target_dir = _attachment_session_dir(session_id)
    temp_path = target_dir / f".upload-{uuid.uuid4().hex}.tmp"
    storage_filename = _attachment_storage_filename(display_name)
    target_path = target_dir / storage_filename
    total = 0
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        with temp_path.open("wb") as output:
            async for chunk in request.stream():
                if not chunk:
                    continue
                output.write(chunk)
                total += len(chunk)
        os.replace(temp_path, target_path)
        _register_attachment(session_id, storage_filename, {
            "source": "upload",
            "displayName": display_name,
            "storageFilename": storage_filename,
            "path": str(target_path.resolve()),
            "size": total,
            "mimeType": request.headers.get("content-type") or mimetypes.guess_type(display_name)[0],
        })
        return {
            "ok": True,
            # filename is retained as a compatibility alias for old clients;
            # new clients must use displayName for the label and href for the
            # download target.
            "filename": display_name,
            # The opaque storage filename is the stable server-side identity;
            # keep it separate from the client-local composer node id.
            "attachmentId": storage_filename,
            "displayName": display_name,
            "storageFilename": storage_filename,
            "href": _attachment_href(session_id, storage_filename),
            "path": str(target_path.resolve()),
            "size": total,
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Attachment storage permission denied") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Attachment upload failed") from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


@app.post("/api/sessions/{session_id}/attachments/from-server-file")
async def register_server_file_attachment(session_id: str, data: dict):
    """Register one existing server file as an opaque AttachmentRef.

    The path is used only for this server-side lookup and is never accepted as
    the message reference.  Directories are intentionally rejected in phase 1.
    """
    if sess.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    raw_path = data.get("path") if isinstance(data, dict) else None
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise HTTPException(status_code=400, detail="path is required")
    try:
        target = _resolve_attachment_source_path(session_id, raw_path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Invalid server file path") from exc
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Directories cannot be attached yet")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Server file not found")
    attachment_id = "att_" + uuid.uuid4().hex
    display_name = _attachment_filename(target.name)
    _register_attachment(session_id, attachment_id, {
        "source": "server_file",
        "displayName": display_name,
        "path": str(target.resolve()),
        "size": target.stat().st_size,
        "mimeType": mimetypes.guess_type(target.name)[0],
    })
    canonical = _canonical_attachment_ref(session_id, attachment_id)
    return {
        "ok": True,
        "attachmentId": attachment_id,
        "displayName": display_name,
        "href": canonical["href"] if canonical else _fs_download_href(session_id, str(target)),
        "path": str(target),
        "size": target.stat().st_size,
    }


@app.get("/api/attachments/ref/{attachment_id}")
async def download_attachment_reference(attachment_id: str, session_id: str):
    """Download a server file through an opaque, session-checked reference."""
    if sess.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    error = _attachment_id_error(session_id, attachment_id, allow_cross_session=True)
    if error is not None:
        status = 404 if error["code"] in {"attachment_not_found", "attachment_incomplete"} else 400
        raise HTTPException(status_code=status, detail=error["message"])
    record = _attachment_record(session_id, attachment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Attachment is no longer available")
    target = Path(str(record.get("path", ""))).resolve()
    safe_name = "".join(c for c in target.name if ord(c) >= 32 and c not in '"\\').strip() or "download"
    return FileResponse(
        target,
        filename=safe_name,
        media_type=record.get("mimeType") or mimetypes.guess_type(target.name)[0] or "application/octet-stream",
    )


@app.get("/api/attachments/editor/{attachment_id}")
async def resolve_editor_attachment(attachment_id: str, session_id: str):
    """Resolve an opaque editor reference for a click, never for drag data."""
    if sess.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    error = _attachment_id_error(session_id, attachment_id, allow_cross_session=True)
    if error is not None:
        status = 404 if error["code"] in {"attachment_not_found", "attachment_incomplete"} else 400
        raise HTTPException(status_code=status, detail=error["message"])
    record = _attachment_record(session_id, attachment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Attachment is no longer available")
    return {
        "ok": True,
        "attachmentId": attachment_id,
        "displayName": _attachment_filename(record.get("displayName") or Path(str(record.get("path", ""))).name),
        "path": str(Path(str(record.get("path", ""))).resolve()),
        "line": record.get("line"),
        "endLine": record.get("endLine"),
    }


@app.get("/api/attachments/{storage_filename}")
async def download_session_attachment(storage_filename: str, session_id: str):
    """Download an uploaded attachment without exposing arbitrary filesystem paths."""
    if sess.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if (
        storage_filename != Path(storage_filename).name
        or "/" in storage_filename
        or "\\" in storage_filename
        or not re.fullmatch(r"upload_[A-Za-z0-9]{32}(?:\.[A-Za-z0-9._-]{1,32})?", storage_filename)
    ):
        raise HTTPException(status_code=400, detail="Invalid attachment name")
    attachment_root = _attachment_session_dir(session_id).resolve()
    target = (attachment_root / storage_filename).resolve()
    try:
        target.relative_to(attachment_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid attachment path") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, filename=target.name, media_type=media_type)


@app.post("/api/directories")
async def create_directory(data: dict):
    """Create a directory only after the UI has obtained explicit consent."""
    try:
        path = data.get("path") if isinstance(data, dict) else None
        if not isinstance(path, str):
            raise ValueError("path is required")
        created = _create_directory(path)
        return {"ok": True, "path": str(created)}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/directories")
async def list_directories(path: str | None = None, include_files: bool = False):
    """List one directory level on the Pan server (never recursive).

    ``path`` omitted/empty returns the server's filesystem roots.  The API is
    intentionally ready for an allowed-roots check to be inserted after path
    resolution when sandboxing is introduced.
    """
    try:
        current = _resolve_directory(path)
        if current is None:
            roots = _directory_roots()
            entries = [
                {"name": root.name or str(root), "path": str(root), "isDirectory": True}
                for root in roots
            ]
            return {"current": "", "parent": None, "entries": entries}

        if not current.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")
        entries = []
        with os.scandir(current) as scan:
            for item in scan:
                try:
                    if item.is_dir(follow_symlinks=False):
                        entries.append({
                            "name": item.name,
                            "path": str(Path(item.path)),
                            "isDirectory": True,
                        })
                    elif include_files and item.is_file(follow_symlinks=False):
                        entries.append({
                            "name": item.name,
                            "path": str(Path(item.path)),
                            "isDirectory": False,
                        })
                except OSError:
                    # A directory can disappear or become inaccessible during
                    # enumeration; omit only that entry and keep the layer.
                    continue
        entries.sort(key=lambda entry: entry["name"].casefold())
        parent = current.parent if current.parent != current else None
        return {
            "current": str(current),
            "parent": str(parent) if parent else None,
            "entries": entries,
        }
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Directory does not exist") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid directory path") from exc


@app.get("/favicon.ico")
async def favicon():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#58a6ff"/><text x="16" y="22" font-size="18" text-anchor="middle" fill="#fff" font-family="monospace" font-weight="bold">P</text></svg>'
    return Response(content=svg, media_type="image/svg+xml")


def _react_unavailable_response() -> HTMLResponse:
    return HTMLResponse(
        content=(
            "React frontend is unavailable: packages/web/dist is missing. "
            "Build it with `pnpm --dir packages/web build`."
        ),
        status_code=503,
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Redirect to the React SPA, or explain that its build is unavailable."""
    if not REACT_DIST_EXISTS:
        return _react_unavailable_response()
    return RedirectResponse("/react/", status_code=307)


# ── WebSocket: Dashboard ──

async def _replay_pending_interactions(
    ws: WebSocket, session_ids: list[str] | None = None,
) -> None:
    """Restore live native prompts to a dashboard that just reconnected.

    Only live workers are considered: the snapshot is a UI replay cache, while
    the open request still belongs to the native process/future.  A
    dead/restarted worker cannot safely receive the old response.
    """
    selected = {str(sid) for sid in session_ids or []}
    for w in worker.list_workers():
        if selected and w.session_id not in selected:
            continue
        if not worker._process_alive(w):
            continue
        status_event = worker.native_status_event(w)
        if status_event is not None:
            await _send_ws(ws, {
                "type": "worker.stream",
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "generation": getattr(w, "generation", 0),
                "event": status_event,
                "replayed": True,
            })
        usage_event = worker.native_usage_event(w)
        if usage_event is not None:
            await _send_ws(ws, {
                "type": "worker.stream",
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "generation": getattr(w, "generation", 0),
                "event": usage_event,
                "replayed": True,
            })
        rate_limits_event = worker.native_rate_limits_event(w)
        if rate_limits_event is not None:
            await _send_ws(ws, {
                "type": "worker.stream",
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "generation": getattr(w, "generation", 0),
                "event": rate_limits_event,
                "replayed": True,
            })
        for native_event in (
            worker.native_plan_event(w),
            worker.native_diff_event(w),
        ):
            if native_event is not None:
                await _send_ws(ws, {
                    "type": "worker.stream",
                    "workerId": w.worker_id,
                    "sessionId": w.session_id,
                    "generation": getattr(w, "generation", 0),
                    "event": native_event,
                    "replayed": True,
                })
        for event in worker.pending_interaction_events(w):
            await _send_ws(ws, {
                "type": "worker.stream",
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "generation": getattr(w, "generation", 0),
                "event": event,
                "replayed": True,
            })

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")
            if msg_type == "ping":
                # Browser heartbeats are application-level JSON frames. A
                # pong updates the client's inbound activity timestamp and
                # prevents an OPEN-but-silent connection from lingering.
                await _send_ws(ws, {"type": "pong"})
            elif msg_type == "user_inject":
                session_id = msg.get("sessionId")
                text = msg.get("text")
                parts = msg.get("parts")
                normalized_parts = None
                if session_id and (text or parts is not None):
                    if parts is not None:
                        normalized_parts, generated_text, parts_error = _normalize_message_parts(
                            session_id, parts)
                        if parts_error is not None:
                            await _send_ws(ws, {
                                "type": "user_inject.rejected",
                                "sessionId": session_id,
                                "message": parts_error["message"],
                                "error": parts_error,
                            })
                            continue
                        text = generated_text
                    elif isinstance(text, str):
                        attachment_error = _validate_message_attachment_references(session_id, text)
                        if attachment_error is not None:
                            await _send_ws(ws, {
                                "type": "user_inject.rejected",
                                "sessionId": session_id,
                                "message": attachment_error["message"],
                                "error": attachment_error,
                            })
                            continue
                    if not isinstance(text, str) or not text.strip():
                        await _send_ws(ws, {
                            "type": "user_inject.rejected",
                            "sessionId": session_id,
                            "message": "text is required",
                            "error": {"code": "text_required", "message": "text is required"},
                        })
                        continue
                    client_message_id = msg.get("clientMessageId")
                    if client_message_id is not None and not isinstance(client_message_id, str):
                        await _send_ws(ws, {"type": "user_inject.rejected",
                                            "sessionId": session_id,
                                            "message": "clientMessageId must be a string"})
                        continue
                    if isinstance(client_message_id, str) and len(client_message_id) > 512:
                        await _send_ws(ws, {"type": "user_inject.rejected",
                                            "sessionId": session_id,
                                            "clientMessageId": client_message_id,
                                            "message": "clientMessageId is too long"})
                        continue
                    # Session is the durable address.  All browser messages
                    # enter the canonical server queue, independent of worker
                    # liveness; the ack means durable enqueue, not Provider
                    # completion.
                    if normalized_parts is None:
                        result = await worker.enqueue_user_message(
                            session_id, text, client_message_id)
                    else:
                        result = await worker.enqueue_user_message(
                            session_id, text, client_message_id, parts=normalized_parts)
                    if result.get("status") == "error":
                        await _send_ws(ws, {"type": "user_inject.rejected",
                                            "sessionId": session_id,
                                            "clientMessageId": client_message_id,
                                            "queueItemId": result.get("queueItemId"),
                                            "message": result.get("result", "send failed")})
                    else:
                        await _send_ws(ws, {"type": "user_inject.accepted",
                                            "sessionId": session_id,
                                            "workerId": result.get("workerId"),
                                            "clientMessageId": client_message_id,
                                            "queueItemId": result.get("queueItemId"),
                                            "queueRevision": getattr(sess.get(session_id), "queue_revision", 0)})
            elif msg_type == "worker_control":
                session_id = msg.get("sessionId")
                worker_id = msg.get("workerId")
                control = msg.get("control")
                if session_id and isinstance(control, dict):
                    result = await worker.send_session_control(session_id, control)
                    if isinstance(result, str):
                        await _send_ws(ws, {"type": "error", "message": result})
                    elif result is None:
                        await _send_ws(ws, {"type": "error", "message": "Worker not found"})
                elif worker_id and isinstance(control, dict):
                    err = await worker.send_control_message(worker_id, control)
                    if err:
                        await _send_ws(ws, {"type": "error", "message": err})
            elif msg_type == "sync_interactive":
                # Optional sessionIds narrows the replay; omitted means all
                # live workers visible to this dashboard, matching /ws's
                # existing broadcast scope.
                raw_session_ids = msg.get("sessionIds")
                if raw_session_ids is not None and not isinstance(raw_session_ids, list):
                    await _send_ws(ws, {
                        "type": "error",
                        "message": "sessionIds must be a list",
                    })
                    continue
                await _replay_pending_interactions(ws, raw_session_ids)
    except WebSocketDisconnect:
        pass
    finally:
        client = _ws_outbound.get(ws)
        if client is not None:
            await client.close_now()
        else:
            ws_clients.discard(ws)


# ── WebSocket: Main Agent ──

@app.websocket("/ws/agent")
async def ws_agent_endpoint(ws: WebSocket):
    await ws.accept()
    agent_clients.add(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "subscribe":
                # 订阅：{"type":"subscribe","eventTypes":[...],"sessionIds":[...]}
                # - eventTypes: "*" 订阅全部；[] 或省略 → 默认（仅 worker.result）
                # - sessionIds: 关心的 session 列表；省略 → 订阅所有 session 的 result
                sub = agent_subscriptions.get(ws)
                if sub is None:
                    sub = AgentSubscription()
                    agent_subscriptions[ws] = sub
                raw_types = msg.get("eventTypes")
                if raw_types is not None:
                    if not isinstance(raw_types, list):
                        await _send_ws(ws, {"type": "error", "message": "eventTypes must be a list"})
                        continue
                    types = set(str(t) for t in raw_types)
                    sub.event_types = types if types else set(_AGENT_DEFAULT_SUBSCRIPTION)
                raw_sids = msg.get("sessionIds")
                if raw_sids is not None:
                    if not isinstance(raw_sids, list):
                        await _send_ws(ws, {"type": "error", "message": "sessionIds must be a list"})
                        continue
                    sub.session_ids = set(str(s) for s in raw_sids)
                await _send_ws(ws, {
                    "type": "subscribed",
                    "eventTypes": sorted(sub.event_types),
                    "sessionIds": sorted(sub.session_ids),
                })

            elif msg_type == "reconnect":
                # 断线重连补发：{"type":"reconnect","sessionIds":[...]}
                # 补发各 session 未消费的终态 worker.result（成功/失败/取消）
                await _replay_agent_results(ws, msg.get("sessionIds") or [])

            elif msg_type == "task":
                session_id = msg.get("sessionId")
                text = msg.get("text")
                if session_id and text:
                    w = worker.find_alive_worker_by_session(session_id)
                    if not w:
                        result = await worker.create_worker(session_id)
                        if isinstance(result, str):
                            await _send_ws(ws, {"type": "error", "message": result})
                            continue
                        else:
                            w = result
                    err = await worker.send_task(w.worker_id, text, source="agent")
                    if err:
                        await _send_ws(ws, {"type": "error", "message": err})

            elif msg_type == "spawn":
                try:
                    params = _build_session_params(msg)
                except ValueError as exc:
                    await _send_ws(ws, {"type": "error", "message": str(exc)})
                    continue
                # 名称校验与 HTTP spawn 对齐（缺名/重名此前会静默建出重复名 session）
                err_name = _check_session_name(params.get("name", "default"))
                if err_name:
                    await _send_ws(ws, {"type": "error", "message": err_name})
                    continue
                s = sess.create(**params)
                result = await worker.create_worker(s.id)
                if isinstance(result, str):
                    await _send_ws(ws, {"type": "error", "message": result})
                else:
                    await _send_ws(ws, {
                        "type": "worker.spawned",
                        "sessionId": s.id,
                        "workerId": result.worker_id,
                        "generation": result.generation,
                        "name": s.name,
                        "status": result.status,
                        "model": s.model,
                    })

            elif msg_type == "assign":
                session_id = msg.get("sessionId")
                text = msg.get("text")
                if not session_id or not text:
                    await _send_ws(ws, {"type": "error", "message": "sessionId and text required"})
                    continue
                result = await worker.assign(session_id, text, source="agent")
                await _send_ws(ws, {"type": "assign.result", **result})

            elif msg_type == "send":
                worker_id = msg.get("workerId")
                text = msg.get("text")
                if not worker_id or not text:
                    await _send_ws(ws, {"type": "error", "message": "workerId and text required"})
                    continue
                result = await worker.send(worker_id, text, source="agent")
                await _send_ws(ws, {"type": "send.result", **result})

            elif msg_type == "kill":
                session_id = msg.get("sessionId") or msg.get("workerId")
                result = await worker.kill_session_worker(session_id)
                if isinstance(result, str):
                    await _send_ws(ws, {"type": "error", "message": result})

            elif msg_type == "list":
                sessions = sess.list_all()
                await _send_ws(ws, {
                    "type": "session.list",
                    "sessions": [_session_to_api(s) for s in sessions],
                })

    except WebSocketDisconnect:
        pass
    finally:
        client = _ws_outbound.get(ws)
        if client is not None:
            await client.close_now()
        else:
            agent_clients.discard(ws)
            agent_subscriptions.pop(ws, None)


# ── Session API ──

@app.post("/api/notifications/send")
async def api_notification_send(data: dict):
    """Send a best-effort Pan system notification for an accessible Session."""
    session_id = data.get("sessionId")
    s = sess.get(session_id) if isinstance(session_id, str) else None
    if not s:
        return {"ok": False, "error": {"code": "session_not_found", "message": "Session not found"}}
    payload = notifications.dispatch_reminder(data.get("title", "Notification"), data.get("body", ""))
    return {"ok": True, "notification": payload}


@app.post("/api/sessions/{session_id}/reminders")
async def api_register_reminder(session_id: str, data: dict):
    if not sess.get(session_id):
        return {"ok": False, "error": {"code": "session_not_found", "message": "Session not found"}}
    try:
        item = reminders.register(session_id, data.get("dueAt"), data.get("title", "Reminder"), data.get("body", ""))
    except ValueError as exc:
        return {"ok": False, "error": {"code": "invalid_due_at", "message": str(exc)}}
    return {"ok": True, "reminder": item}


@app.get("/api/sessions/{session_id}/reminders")
async def api_list_reminders(session_id: str):
    if not sess.get(session_id):
        return {"ok": False, "error": {"code": "session_not_found", "message": "Session not found"}}
    return {"ok": True, "reminders": reminders.list_for_session(session_id)}


@app.delete("/api/sessions/{session_id}/reminders/{reminder_id}")
async def api_cancel_reminder(session_id: str, reminder_id: str):
    if not sess.get(session_id):
        return {"ok": False, "error": {"code": "session_not_found", "message": "Session not found"}}
    item = reminders.cancel(session_id, reminder_id)
    if not item:
        return {"ok": False, "error": {"code": "reminder_not_found", "message": "Reminder not found or already delivered"}}
    return {"ok": True, "reminder": item}

@app.get("/api/sessions")
async def api_list_sessions(summary: int = 0, workspaceId: str | None = None):
    """List all sessions (includes worker status if active).

    summary=1 → lean payload [{id, name, adapter, workerStatus, updatedAt,
    managedBy}] without history/usage (供 MCP session_list(summary=true) 等
    轻量巡检，避免全量传输再过滤). Default stays the full payload for
    backward compatibility.
    """
    # Summary reads use the metadata/projection loader.  Full history is only
    # loaded by the legacy full response below.
    sessions = sess.list_all(load_history=not bool(summary))
    if workspaceId is not None:
        # ``ungrouped`` is a stable query alias; an empty workspaceId is kept
        # equivalent to the historical all-sessions response.
        if workspaceId == "ungrouped":
            sessions = [s for s in sessions if not s.workspace_ids]
        elif workspaceId:
            if workspaces.get(workspaceId) is None:
                return {"sessions": [], "workspaceId": workspaceId}
            sessions = [s for s in sessions if workspaceId in s.workspace_ids]
    if summary:
        return {"sessions": [_session_summary(s) for s in sessions]}
    result = []
    for s in sessions:
        api = _session_to_api(s)
        full_history = api.get("history") or []
        api["history"] = full_history[-50:] if len(full_history) > 50 else full_history
        api["historyTruncated"] = len(full_history) > 50
        api["historyTotal"] = len(full_history)
        result.append(api)
    return {"sessions": result}


def _workspace_view(workspace: workspaces.Workspace) -> dict:
    members = [s.id for s in sess.list_all() if workspace.id in s.workspace_ids]
    return {
        "id": workspace.id,
        "name": workspace.name,
        "order": workspace.order,
        "createdAt": workspace.created_at,
        "updatedAt": workspace.updated_at,
        "sessionCount": len(members),
        "sessionIds": members,
    }


def _workspace_name_error(name) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return "Workspace name is required"
    if len(name.strip()) > 128:
        return "Workspace name too long (max 128)"
    return None


def _workspace_write_allowed(actor_id: str | None, session: sess.Session) -> bool:
    """Apply Session's existing managed-scope permission when an actor is given.

    Browser/legacy callers omit actorSessionId and retain the historical
    trusted local-web behavior.  MCP/future callers can provide it to get an
    explicit permission check without coupling workspaces to managedBy.
    """
    if not actor_id or not session.restrict_to_managed:
        return True
    actor = sess.get(actor_id)
    return bool(actor and (session.managed_by == actor_id or session.id == actor_id
                           or session.id in actor.managed))


@app.get("/api/workspaces")
async def api_list_workspaces():
    """List durable workspaces in their independent display order."""
    return {"workspaces": [_workspace_view(w) for w in workspaces.list_all()]}


@app.post("/api/workspaces")
async def api_create_workspace(data: dict):
    error = _workspace_name_error(data.get("name"))
    if error:
        return {"ok": False, "error": {"code": "invalid_name", "message": error}}
    name = data["name"].strip()
    if any(w.name == name for w in workspaces.list_all()):
        return {"ok": False, "error": {"code": "name_taken", "message": "Workspace name already exists"}}
    workspace = workspaces.create(name)
    await broadcast({"type": "workspace.created", "workspaceId": workspace.id})
    return {"ok": True, "workspace": _workspace_view(workspace)}


@app.get("/api/workspaces/{workspace_id}")
async def api_get_workspace(workspace_id: str):
    workspace = workspaces.get(workspace_id)
    if workspace is None:
        return {"ok": False, "error": {"code": "workspace_not_found", "message": "Workspace not found"}}
    return {"ok": True, "workspace": _workspace_view(workspace)}


@app.patch("/api/workspaces/{workspace_id}")
async def api_update_workspace(workspace_id: str, data: dict):
    workspace = workspaces.get(workspace_id)
    if workspace is None:
        return {"ok": False, "error": {"code": "workspace_not_found", "message": "Workspace not found"}}
    if "name" in data:
        error = _workspace_name_error(data["name"])
        if error:
            return {"ok": False, "error": {"code": "invalid_name", "message": error}}
        name = data["name"].strip()
        if any(w.id != workspace_id and w.name == name for w in workspaces.list_all()):
            return {"ok": False, "error": {"code": "name_taken", "message": "Workspace name already exists"}}
        workspaces.update(workspace, name=name)
        await broadcast({"type": "workspace.updated", "workspaceId": workspace_id})
    return {"ok": True, "workspace": _workspace_view(workspace)}


@app.post("/api/workspaces/order")
async def api_workspaces_order(data: dict):
    ids = data.get("workspaceIds")
    if not isinstance(ids, list) or not all(isinstance(i, str) and i.strip() for i in ids):
        return {"ok": False, "error": {"code": "invalid_params", "message": "workspaceIds is required"}}
    error = workspaces.apply_order([i.strip() for i in ids])
    if error:
        return {"ok": False, "error": {"code": "workspace_not_found", "message": error}}
    order = [w.id for w in workspaces.list_all()]
    await broadcast({"type": "workspace.orderUpdated", "order": order})
    return {"ok": True, "order": order}


@app.delete("/api/workspaces/{workspace_id}")
async def api_delete_workspace(workspace_id: str):
    if workspaces.get(workspace_id) is None:
        return {"ok": False, "error": {"code": "workspace_not_found", "message": "Workspace not found"}}
    for session in sess.list_all():
        if workspace_id in session.workspace_ids:
            session.workspace_ids.remove(workspace_id)
            sess.save(session)
    workspaces.delete(workspace_id)
    await broadcast({"type": "workspace.deleted", "workspaceId": workspace_id})
    return {"ok": True, "workspaceId": workspace_id}


async def _set_workspace_membership(workspace_id: str, session_ids, actor_id=None):
    workspace = workspaces.get(workspace_id)
    if workspace is None:
        return {"ok": False, "error": {"code": "workspace_not_found", "message": "Workspace not found"}}
    if (not isinstance(session_ids, list)
            or not all(isinstance(sid, str) and sid for sid in session_ids)
            or len(set(session_ids)) != len(session_ids)):
        return {"ok": False, "error": {"code": "invalid_session_ids", "message": "sessionIds must be a unique array"}}
    for session_id in session_ids:
        session = sess.get(session_id) if isinstance(session_id, str) else None
        if session is None:
            return {"ok": False, "error": {"code": "session_not_found", "message": f"Session {session_id} not found"}}
        if not _workspace_write_allowed(actor_id, session):
            return {"ok": False, "error": {"code": "forbidden", "message": "Workspace membership is restricted"}}
    wanted = set(session_ids)
    for session in sess.list_all():
        if not _workspace_write_allowed(actor_id, session):
            continue
        has = workspace_id in session.workspace_ids
        should = session.id in wanted
        if has != should:
            if should:
                session.workspace_ids.append(workspace_id)
            else:
                session.workspace_ids.remove(workspace_id)
            sess.save(session)
    await broadcast({"type": "workspace.membershipUpdated", "workspaceId": workspace_id,
                     "sessionIds": [s.id for s in sess.list_all() if workspace_id in s.workspace_ids]})
    return {"ok": True, "workspace": _workspace_view(workspace)}


@app.put("/api/workspaces/{workspace_id}/sessions")
async def api_set_workspace_sessions(workspace_id: str, data: dict):
    return await _set_workspace_membership(workspace_id, data.get("sessionIds"), data.get("actorSessionId"))


@app.get("/api/workspaces/{workspace_id}/sessions")
async def api_get_workspace_sessions(workspace_id: str, summary: int = 0):
    if workspaces.get(workspace_id) is None:
        return {"ok": False, "error": {"code": "workspace_not_found", "message": "Workspace not found"}}
    sessions = [s for s in sess.list_all(load_history=not bool(summary))
                if workspace_id in s.workspace_ids]
    return {"ok": True, "workspaceId": workspace_id,
            "sessions": [_session_summary(s) if summary else _session_to_api(s) for s in sessions]}


@app.put("/api/sessions/{session_id}/workspaces")
async def api_set_session_workspaces(session_id: str, data: dict):
    session = sess.get(session_id)
    if session is None:
        return {"ok": False, "error": {"code": "session_not_found", "message": "Session not found"}}
    workspace_ids = data.get("workspaceIds")
    if (not isinstance(workspace_ids, list)
            or not all(isinstance(wid, str) and wid for wid in workspace_ids)
            or len(set(workspace_ids)) != len(workspace_ids)):
        return {"ok": False, "error": {"code": "invalid_workspace_ids", "message": "workspaceIds must be a unique array"}}
    if not all(isinstance(wid, str) and workspaces.get(wid) for wid in workspace_ids):
        return {"ok": False, "error": {"code": "workspace_not_found", "message": "Workspace not found"}}
    if not _workspace_write_allowed(data.get("actorSessionId"), session):
        return {"ok": False, "error": {"code": "forbidden", "message": "Workspace membership is restricted"}}
    session.workspace_ids = list(workspace_ids)
    sess.save(session)
    await broadcast({"type": "session.workspaceUpdated", "sessionId": session_id,
                     "workspaceIds": list(session.workspace_ids)})
    return {"ok": True, "session": _session_to_api(session)}


@app.post("/api/sessions")
async def api_create_session(data: dict):
    """Create a new Session (no worker spawned)."""
    try:
        params = _build_session_params(data)
    except ValueError as e:
        return {"error": str(e)}
    name = params["name"]
    err = _check_session_name(name)
    if err:
        return {"error": err}

    s = sess.create(**params)
    await broadcast({
        "type": "session.created",
        "sessionId": s.id,
        "name": s.name,
    })
    return _session_to_api(s)


@app.post("/api/sessions/order")
async def api_sessions_order(data: dict):
    """Persist a custom display order for the session list (drag & drop).

    Body: {"sessionIds": ["ses_a", "ses_b", ...]} — desired display order.
    - sessionIds must be an array of unique, existing session id strings;
    - sessions not listed keep their current relative order after the
      listed ones (partial reorder is allowed);
    - afterwards every session carries an explicit integer order value;
      sessions created later (order=None) sort to the end until reordered.

    Response: {"ok": true, "order": [full session id order]} or
    {"ok": false, "error": {code, message}}. Broadcasts
    {"type": "session.orderUpdated", "order": [...]}.

    快捷管理（拖到另一张卡片正中间 = 由对方 manage）不需要新端点：直接复用
    POST /api/claim（managerId=被拖入的卡片 B，sessionId=A）。
    """
    ids = data.get("sessionIds")
    if not isinstance(ids, list) or \
            not all(isinstance(i, str) and i.strip() for i in ids):
        return {"ok": False, "error": {
            "code": "invalid_params",
            "message": "sessionIds (array of session id strings) is required"}}
    ids = [i.strip() for i in ids]
    if len(set(ids)) != len(ids):
        return {"ok": False, "error": {
            "code": "duplicate_session_ids",
            "message": "sessionIds contains duplicates"}}
    err = sess.apply_order(ids)
    if err:
        return {"ok": False, "error": {
            "code": "session_not_found",
            "message": err}}
    order = [s.id for s in sess.list_all()]
    await broadcast({"type": "session.orderUpdated", "order": order})
    return {"ok": True, "order": order}


@app.get("/api/sessions/{session_id}")
async def api_get_session(session_id: str, view: str = "full"):
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    if view in {"metadata", "detail"}:
        # Keep relationship/settings/system-prompt fields while avoiding the
        # expensive and potentially large history/rawUsage/lastResult payloads.
        # The default full view is unchanged for API/MCP compatibility.
        return _session_to_api(
            s,
            include_history=False,
            include_raw_usage=False,
            include_last_result=False,
        )
    return _session_to_api(s)


@app.get("/api/sessions/{session_id}/usage")
async def api_get_session_usage(session_id: str):
    """Return the stable persisted input/output/cache usage projection.

    This is a read-only view over Session.raw_usage / Session.total_usage. It
    does not wait for an active provider refresh and, unlike the full session
    response, does not expose the historical raw payload. Codex receives the
    last persisted quota snapshot only; callers that need a best-effort live
    refresh use ``/api/codex/quota`` separately.
    """
    s = sess.get(session_id)
    if not s:
        return {"ok": False, "error": {
            "code": "session_not_found",
            "message": f"Session {session_id} not found",
        }}
    result = sess.session_usage_view(s)
    if s.adapter == "codex":
        quota = await api_codex_quota(session_id=session_id, refresh=False)
        result["codexQuota"] = quota if quota.get("ok") else None
    return result


@app.get("/api/sessions/{session_id}/managers")
async def api_session_managers(session_id: str):
    """Manager chain of a session, topmost first (level 1 = top).

    Walks the managedBy chain upward from the session. Each entry carries
    {level, id, name, workerStatus, lastResultStatus}:
    - workerStatus: live worker status (None = no worker spawned)
    - lastResultStatus: status of the session's last completed task
      ("done"/"error"/...; None = never ran a task)

    Edge cases:
    - Dangling managedBy (manager deleted): the chain stops there — the
      dangling id is not included (no info available about it).
    - Cycles: a visited-set stops the walk on a repeated id.
    - Unknown session_id → {"error": "Session not found"}.
    - Session with no manager → {"managers": []}.
    """
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    chain: list[sess.Session] = []
    seen = {session_id}
    cur = s
    while True:
        mb = cur.managed_by
        if not mb or mb in seen:
            break
        manager = sess.get(mb)
        if manager is None:
            break  # dangling reference → chain ends here
        seen.add(mb)
        chain.append(manager)
        cur = manager
    managers = []
    for i, m in enumerate(reversed(chain)):  # topmost first
        w = worker.find_alive_worker_by_session(m.id)
        last_status = (m.last_result or {}).get("status") \
            if isinstance(m.last_result, dict) else None
        managers.append({
            "level": i + 1,  # level 1 = topmost manager
            "id": m.id,
            "name": m.name,
            "workerStatus": w.status if w else None,
            "lastResultStatus": last_status,
        })
    return {"ok": True, "sessionId": session_id, "managers": managers}


@app.get("/api/sessions/{session_id}/history")
async def api_session_history(session_id: str, before: int = 0, limit: int = 50):
    """Paginated session history for lazy-loading older messages."""
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    total = len(s.history)
    if before <= 0:
        before = total
    start = max(0, before - limit)
    page = _api_history(session_id, s.history[start:before])
    return {
        "history": page,
        "total": total,
        "hasMore": start > 0,
        "start": start,
    }


# ── Agent queue (session.queue_pending, normalized view) ──
#
# queue_pending 是异构落盘队列：task、report/notice、QQ 都使用统一 envelope；
# 这里序列化为统一的 AgentQueueItem 供前端 SendQueuePanel 渲染。旧版本没有
# queueItemId 的行只在迁移期间回退内容 hash，新写入项始终使用服务端 q_ ID。

_REPORT_TEXT_MAX = 200


def _queue_item_id(item: dict) -> str:
    """Return the server-generated queue identity; hash only legacy rows."""
    if item.get("queueItemId"):
        return str(item["queueItemId"])
    if item.get("id") and (item.get("kind") or item.get("schemaVersion")):
        return str(item["id"])
    if item.get("type") == "task" and item.get("id"):
        return str(item["id"])
    try:
        # deliveryState changes during hand-off; it is bookkeeping, not item
        # identity.  Keep report/QQ ids stable so a stale panel action still
        # addresses the same durable item.
        identity = {k: v for k, v in item.items() if k not in {
            "deliveryState", "reservedBy", "reservedGeneration", "reservedAt",
            "deliveryAttempts", "nextAttemptAt", "lastDeliveryError",
        }}
        canonical = json.dumps(identity, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        canonical = repr(item)
    return "sha1:" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def _queue_dispatch_state(s, item: dict) -> str:
    """Expose the durable state, never infer it from Provider status."""
    state = worker._delivery_state(item)
    return "unknown_after_crash" if state == worker._DELIVERY_IN_FLIGHT else state


def _serialize_queue_item(item, session=None) -> dict | None:
    """queue_pending item → AgentQueueItem；无法识别的形状返回 None（跳过）。"""
    if not isinstance(item, dict):
        return None
    t = item.get("type")
    # Before durable task envelopes were introduced, user/agent messages were
    # persisted as {text, source} without a type.  They are tasks, never
    # reports: treating a no-type text item as a report is what made a message
    # show up under the Agent queue (and eventually acquire an @@@@by agent
    # rendering after a worker restart).  Mirror worker._migrate_legacy_task_items
    # here so the queue API is safe even before the next worker recovery tick.
    if t == "task" or item.get("kind") == "task" or (t is None and isinstance(item.get("text"), str)):
        source = worker._task_source(item) or "user"
        meta = {
            "seq": item.get("seq"),
            "taskId": item.get("taskId"),
            "revision": item.get("revision", 1),
            "dispatchState": _queue_dispatch_state(session, item) if session else worker._delivery_state(item),
        }
        if item.get("sourceSessionId") is not None:
            meta["sourceSessionId"] = item.get("sourceSessionId")
        return {
            "id": _queue_item_id(item),
            "queueItemId": _queue_item_id(item),
            "kind": "task",
            "text": item.get("text") if isinstance(item.get("text"), str) else "",
            **({"parts": [
                    {key: value for key, value in part.items() if key != "__serverPath"}
                    for part in item["parts"] if isinstance(part, dict)
                ]}
               if isinstance(item.get("parts"), list) else {}),
            "createdAt": item.get("createdAt", 0),
            "source": source,
            "meta": meta,
        }
    if t == "qq" or item.get("kind") == "qq":
        return {
            "id": _queue_item_id(item),
            "queueItemId": _queue_item_id(item),
            "kind": "qq",
            "text": item.get("text") if isinstance(item.get("text"), str) else "",
            "createdAt": item.get("createdAt", 0),
            "source": "qq",
            "meta": {
                "qqTarget": item.get("qqTarget"),
                "time": item.get("time"),
                "revision": item.get("revision", 1),
                "dispatchState": _queue_dispatch_state(session, item) if session else worker._delivery_state(item),
            },
        }
    # 非 task/qq 一律按 report 处理（普通报告无 type 字段、zombie 为 type=zombie），
    # 与 worker 消费端分类（type != "task" 即报告）保持一致；完全无 result 的
    # 畸形项跳过。
    if "result" not in item and t is not None:
        return None
    result = item.get("result")
    if isinstance(result, str):
        text = result
    elif result is None:
        text = ""
    else:
        try:
            text = json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError):
            text = repr(result)
    if len(text) > _REPORT_TEXT_MAX:
        text = text[:_REPORT_TEXT_MAX] + "…"
    meta = {
        "status": item.get("status"),
        "taskId": item.get("taskId"),
        "workerId": item.get("workerId"),
        "revision": item.get("revision", 1),
        "dispatchState": _queue_dispatch_state(session, item) if session else worker._delivery_state(item),
    }
    # Preserve structured system-Job metadata in the queue API.  The text
    # remains available for the Agent/CLI, but clients must not parse jobId or
    # routing identity back out of result text.
    for key in (
        "noticeKind", "jobId", "targetSessionId", "targetSessionIds",
        "creatorSessionId", "eventId",
    ):
        if key in item:
            meta[key] = item[key]
    if item.get("sourceSessionId") is not None:
        meta["sourceSessionId"] = item.get("sourceSessionId")
    return {
        "id": _queue_item_id(item),
        "queueItemId": _queue_item_id(item),
        "kind": "report",
        "text": text,
        "createdAt": item.get("createdAt", 0),
        "source": item.get("source") or "report",
        "meta": meta,
    }


def _session_queue_items(s) -> list[dict]:
    items = []
    seen: set[str] = set()
    # The pending API is intentionally narrower than the delivery ledger:
    # queue_pending is the only source for items still awaiting delivery.
    # Reserved/sent/failed/unknown records remain in the ledger for idempotency
    # and diagnostics, but must disappear from this snapshot immediately.
    source_items = [
        it for it in (s.queue_pending or [])
        if isinstance(it, dict) and worker._delivery_state(it) == worker._DELIVERY_QUEUED
    ]
    source_items.sort(key=lambda it: (it.get("position", 10**9), str(it.get("createdAt", ""))))
    for it in source_items:
        si = _serialize_queue_item(it, s)
        if si is not None and si["id"] not in seen:
            items.append(si)
            seen.add(si["id"])
    return items


@app.get("/api/sessions/{session_id}/queue")
async def api_session_queue(session_id: str):
    """Normalized agent queue (session.queue_pending) for the frontend panel."""
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    migrated = worker._migrate_queue_delivery_state(s)
    migrated = worker._sync_queued_ledger(s) or migrated
    if migrated:
        await sess.save_async(s)
    return {"items": _session_queue_items(s),
            "queueRevision": getattr(s, "queue_revision", 0)}


@app.post("/api/sessions/{session_id}/queue")
async def api_session_queue_enqueue(session_id: str, data: dict):
    """Canonical user enqueue endpoint.

    Only text and the browser idempotency key are accepted from the client;
    source=user/kind=task are fixed by the server-side entry point.
    """
    text = data.get("text")
    original_text = text
    parts = data.get("parts")
    normalized_parts = None
    if parts is not None:
        normalized_parts, generated_text, parts_error = _normalize_message_parts(session_id, parts)
        if parts_error is not None:
            return {"ok": False, "error": parts_error}
        text = generated_text
    elif isinstance(text, str):
        # Upgrade old text/Markdown attachment links before the queue item is
        # persisted, so retry/restart delivery has the same canonical path
        # projection as a new structured request.
        normalized_parts, generated_text, parts_error = _normalize_text_attachment_parts(
            session_id, text)
        if parts_error is not None:
            return {"ok": False, "error": parts_error}
        if normalized_parts is not None:
            text = generated_text
    if not isinstance(text, str) or not text.strip():
        return {"ok": False, "error": {"code": "text_required",
                                         "message": "text is required"}}
    if normalized_parts is None:
        attachment_error = _validate_message_attachment_references(session_id, text)
        if attachment_error is not None:
            return {"ok": False, "error": attachment_error}
    client_id = data.get("clientMessageId")
    if client_id is not None and (not isinstance(client_id, str) or len(client_id) > 512):
        return {"ok": False, "error": {"code": "invalid_client_message_id",
                                         "message": "clientMessageId must be a string of at most 512 characters"}}
    if normalized_parts is None:
        # Keep the exact legacy call shape for embedders and test doubles that
        # still implement the text-only queue contract.
        result = await worker.enqueue_user_message(session_id, text, client_id)
    else:
        # Older embedders may still expose the three-argument queue function.
        # Production Worker accepts parts; a legacy replacement gets the
        # canonical fallback text and keeps its established call shape.
        parameters = inspect.signature(worker.enqueue_user_message).parameters
        supports_parts = (
            "parts" in parameters
            or any(parameter.kind == inspect.Parameter.VAR_KEYWORD
                   for parameter in parameters.values())
        )
        if supports_parts:
            result = await worker.enqueue_user_message(
                session_id, text, client_id, parts=normalized_parts)
        else:
            # A legacy replacement cannot consume the private path projection;
            # preserve its established text-only input after server-side
            # validation rather than changing its call semantics.
            legacy_text = original_text if parts is None else text
            result = await worker.enqueue_user_message(session_id, legacy_text, client_id)
    if result.get("status") == "error":
        return {"ok": False, "error": {"code": "enqueue_failed",
                                         "message": result.get("result", "enqueue failed")}}
    s = sess.get(session_id)
    return {"ok": True,
            "item": _serialize_queue_item(result.get("item") or {}, s),
            "queueRevision": getattr(s, "queue_revision", 0),
            "duplicate": bool(result.get("duplicate"))}


def _queue_error(code: str, message: str, s=None) -> dict:
    response = {"ok": False, "error": {"code": code, "message": message}}
    if s is not None:
        response["items"] = _session_queue_items(s)
        response["queueRevision"] = getattr(s, "queue_revision", 0)
    return response


@app.patch("/api/sessions/{session_id}/queue/order")
async def api_session_queue_order_route(session_id: str, data: dict):
    """Route the static order path before the dynamic item-id path below."""
    return await api_session_queue_order(session_id, data)


@app.patch("/api/sessions/{session_id}/queue/{item_id}")
async def api_session_queue_update(session_id: str, item_id: str, data: dict):
    """Edit only a queued user task, retaining its durable identity."""
    s = sess.get(session_id)
    if not s:
        return {"ok": False, "error": "Session not found"}
    text = data.get("text")
    expected = data.get("expectedRevision")
    async with worker.queue_lock(session_id):
        target = next((it for it in s.queue_pending or []
                       if isinstance(it, dict) and _queue_item_id(it) == item_id), None)
        if target is None:
            if item_id in (getattr(s, "queue_delivery_ledger", {}) or {}):
                return _queue_error("queue_item_not_editable", "Only queued user task messages can be edited", s)
            return _queue_error("not_found", "Queue item not found", s)
        if (worker._queue_item_kind(target) != "task"
                or worker._task_source(target) != "user"):
            return _queue_error("queue_item_readonly", "Only user task queue items can be edited", s)
        if worker._delivery_state(target) != worker._DELIVERY_QUEUED:
            return _queue_error("queue_item_not_editable", "Queue item is no longer queued", s)
        if not isinstance(text, str) or not text.strip():
            return _queue_error("text_required", "text is required", s)
        if isinstance(target.get("parts"), list):
            normalized_parts, canonical_text, parts_error = _normalize_message_parts(
                session_id, target["parts"])
            if parts_error is not None:
                return _queue_error(parts_error["code"], parts_error["message"], s)
            if text != canonical_text:
                return _queue_error(
                    "parts_text_conflict",
                    "Queued message text must match its structured attachment parts",
                    s,
                )
            target["parts"] = normalized_parts
        current_revision = int(target.get("revision", 1))
        if expected is not None and expected != current_revision:
            return _queue_error("queue_revision_conflict", "Queue item revision conflict", s)
        old_target = dict(target)
        old_ledger = dict(s.queue_delivery_ledger.get(item_id, {}))
        old_queue_revision = s.queue_revision
        target["text"] = text
        target["revision"] = current_revision + 1
        target["updatedAt"] = datetime.now().isoformat()
        _ledger = s.queue_delivery_ledger.get(item_id)
        if not isinstance(_ledger, dict):
            _ledger = dict(old_target)
            s.queue_delivery_ledger[item_id] = _ledger
        _ledger.update(target)
        s.queue_revision += 1
        try:
            await sess.save_async(s)
        except Exception:
            target.clear()
            target.update(old_target)
            if old_ledger:
                _ledger.clear()
                _ledger.update(old_ledger)
            else:
                s.queue_delivery_ledger.pop(item_id, None)
            s.queue_revision = old_queue_revision
            raise
    await worker._bcast({
        "type": "queue.item_updated",
        "sessionId": session_id,
        "queueItemId": item_id,
        "queueRevision": s.queue_revision,
        "item": _serialize_queue_item(target, s),
    })
    return {"ok": True, "item": _serialize_queue_item(target, s),
            "queueRevision": s.queue_revision}


@app.delete("/api/sessions/{session_id}/queue/{item_id}")
async def api_session_queue_delete(session_id: str, item_id: str):
    """Remove one still-queued item; delivery receipts remain auditable."""
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    async with worker.queue_lock(session_id):
        pending = s.queue_pending or []
        target = next((it for it in pending if isinstance(it, dict)
                       and _queue_item_id(it) == item_id), None)
        if target is None:
            if item_id in (getattr(s, "queue_delivery_ledger", {}) or {}):
                return _queue_error("queue_item_not_deletable", "Queue item is no longer queued", s)
            return {"ok": False, "error": "not_found"}
        if worker._delivery_state(target) != worker._DELIVERY_QUEUED:
            return _queue_error("queue_item_not_deletable", "Queue item is no longer queued", s)
        old_pending = list(pending)
        old_record = dict(s.queue_delivery_ledger.get(item_id, {}))
        old_queue_revision = s.queue_revision
        s.queue_pending = [it for it in pending if it is not target]
        record = s.queue_delivery_ledger.get(item_id)
        if isinstance(record, dict):
            record["deliveryState"] = "deleted"
            record["dispatchState"] = "deleted"
        s.queue_revision += 1
        try:
            await sess.save_async(s)
        except Exception:
            s.queue_pending = old_pending
            if old_record:
                s.queue_delivery_ledger[item_id] = old_record
            else:
                s.queue_delivery_ledger.pop(item_id, None)
            s.queue_revision = old_queue_revision
            raise
    await worker._bcast({"type": "queue.item_removed", "sessionId": session_id,
                         "queueItemId": item_id, "queueRevision": s.queue_revision})
    return {"ok": True, "queueItemId": item_id,
            "queueRevision": s.queue_revision}


@app.post("/api/sessions/{session_id}/queue/{item_id}/retry")
async def api_session_queue_retry(session_id: str, item_id: str):
    """Requeue a failed or interrupted item using its existing queue identity."""
    result = await worker.retry_pending_item(session_id, item_id)
    if isinstance(result, str):
        return {"ok": False, "error": result}
    item = result.get("item", result) if isinstance(result, dict) else result
    return {"ok": True, "item": _serialize_queue_item(item, sess.get(session_id)),
            "status": result.get("status") if isinstance(result, dict) else None}


async def api_session_queue_order(session_id: str, data: dict):
    """Reorder all still-queued sources within the persisted queue.

    Body: {"orderedIds": ["id1", ...]} — the relative order of every queued
    source (user, agent, report, QQ, or system) in `orderedIds` becomes the new
    pending sequence; omitted queued items keep their relative order at the end.
    Returns the reordered normalized items.
    """
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    order = data.get("orderedIds", data.get("order"))
    if not isinstance(order, list):
        return {"error": "order (array of item ids) required"}
    expected = data.get("expectedQueueRevision")
    async with worker.queue_lock(session_id):
        if expected is not None and expected != getattr(s, "queue_revision", 0):
            return _queue_error("queue_revision_conflict", "Queue revision conflict", s)
        pending = [it for it in (s.queue_pending or [])
                   if isinstance(it, dict) and worker._delivery_state(it) == worker._DELIVERY_QUEUED]
        by_id = {_queue_item_id(it): it for it in pending}
        if len({str(i) for i in order}) != len(order):
            return _queue_error("invalid_queue_order", "orderedIds contains duplicates", s)
        unknown = [str(i) for i in order if str(i) not in by_id]
        if unknown:
            return _queue_error("invalid_queue_order", "orderedIds contains unknown or claimed items", s)
        ordered = [by_id[str(i)] for i in order]
        ordered.extend(it for it in pending if it not in ordered)
        old_pending = list(s.queue_pending or [])
        old_positions = {id(it): it.get("position") for it in old_pending}
        old_queue_revision = s.queue_revision
        # Keep any reserved/writing rows in their existing slots while
        # applying the requested order to every still-queued source.
        queued_slots = [index for index, it in enumerate(old_pending)
                        if isinstance(it, dict)
                        and worker._delivery_state(it) == worker._DELIVERY_QUEUED]
        s.queue_pending = list(old_pending)
        for slot, it in zip(queued_slots, ordered):
            s.queue_pending[slot] = it
        for position, it in enumerate(s.queue_pending):
            it["position"] = position
        s.queue_revision += 1
        try:
            await sess.save_async(s)
        except Exception:
            s.queue_pending = old_pending
            for it in old_pending:
                if old_positions[id(it)] is None:
                    it.pop("position", None)
                else:
                    it["position"] = old_positions[id(it)]
            s.queue_revision = old_queue_revision
            raise
    await worker._bcast({"type": "queue.snapshot", "sessionId": session_id,
                         "queueRevision": s.queue_revision})
    return {"items": _session_queue_items(s), "queueRevision": s.queue_revision}


@app.patch("/api/sessions/{session_id}")
async def api_update_session(session_id: str, data: dict):
    """Update session-level settings without spawning a worker."""
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    require_restart = False
    old_mcp_servers = s.adapter_config.get("mcp_servers")
    old_output_mode = s.adapter_config.get("output_mode")
    try:
        _apply_session_updates(s, data)
    except ValueError as e:
        return {"error": str(e)}
    new_mcp_servers = s.adapter_config.get("mcp_servers")
    new_output_mode = s.adapter_config.get("output_mode")
    # MCP servers 增删或执行模式切换都需要重启 worker 才生效
    require_restart = (old_mcp_servers != new_mcp_servers
                       or old_output_mode != new_output_mode)
    sess.save(s)
    await broadcast({
        "type": "session.updated",
        "sessionId": s.id,
        # Safe summary fields let connected routes update immediately; the
        # debounced list refresh below remains the final reconciliation.
        "session": _session_summary(s),
    })
    result = _session_to_api(s)
    # 进程相关字段变更（model/effort/thinking/MCP 等）：idle worker 立即
    # respawn 让新配置生效；running worker 标记 pending_restart，回 idle 时
    # 自动 respawn（worker._maybe_restart_pending）。
    if any(k in data for k in _PROCESS_AFFECTING_FIELDS):
        w = worker.find_alive_worker_by_session(session_id)
        if w:
            if w.status == "idle" and w.process is not None:
                asyncio.create_task(worker._respawn_worker(w))
            else:
                w.pending_restart = True
            require_restart = True
    if require_restart:
        result["requireRestart"] = True
    return result


@app.post("/api/sessions/{session_id}/rename")
async def api_rename_session(session_id: str, data: dict):
    """Rename a session by its internal ID (no worker required)."""
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return {"error": "name is required"}

    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}

    if s.name == new_name:
        return {"sessionId": s.id, "name": new_name, "status": "unchanged"}

    err = _check_session_name(new_name)
    if err:
        return {"error": err}

    old_name = s.name
    s.name = new_name
    sess.save(s)

    # G7: 把重命名持久化进 adapter 原生存储（按 provider 统一调用，P0-2）。
    # kimi/opencode 显式回写 state.json / SQLite；cbc 追加 custom-title 事件
    # （与 fork 时的写标题路径一致），三者均幂等安全。
    if s.cli_session_id:
        provider = _sessions_provider(s.adapter)
        if provider is not None:
            try:
                provider.write_custom_title(
                    s.cli_session_id, new_name, s.workdir or None,
                    kimi_home=s.adapter_config.get("kimi_home_dir"),
                )
            except Exception as e:
                print(f"[rename] {s.adapter} write_custom_title failed: {e}")

    await broadcast({
        "type": "session.renamed",
        "sessionId": s.id,
        "oldName": old_name,
        "newName": new_name,
        "name": new_name,
        "session": _session_summary(s),
    })
    return {"sessionId": s.id, "name": new_name, "status": "renamed"}


@app.post("/api/sessions/{session_id}/branch")
async def api_branch_session(session_id: str, data: dict):
    """Branch from a session — copy adapter-specific transcript, import new session, preserve settings.

    Dispatches by adapter because each backend stores sessions differently
    (cbc: JSONL under a project dir; kimi: wire.jsonl under ~/.kimi-code/sessions).
    """
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    if not s.cli_session_id:
        return {"error": "Session has no CLI session ID — cannot branch"}

    name = (data.get("name") or "").strip()
    if not name:
        name = f"{s.name}-branch"

    err = _check_session_name(name)
    if err:
        return {"error": err}

    # Fork via pure file operations — no CLI process spawned.
    # 按 provider 统一调用（P0-2）：cbc=复制 JSONL、kimi=目录复制、
    # opencode=SQLite 行复制，各自 fork_session 内部实现。
    cwd = s.workdir or ""
    provider = _sessions_provider(s.adapter)
    if provider is None:
        return {"error": f"Unknown adapter: {s.adapter}"}
    try:
        new_cli_id = provider.fork_session(s.cli_session_id, name, cwd or None)
    except FileNotFoundError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Fork failed: {e}"}

    try:
        history = provider.parse_history(new_cli_id, cwd or None)
        raw_usage_entries = provider.get_raw_usage(new_cli_id, cwd or None)
    except Exception as e:
        return {"error": f"Failed to parse forked session: {e}"}

    raw_usage = sess.accumulate_raw_usage(None, raw_usage_entries)
    total_usage = sess.compute_total_usage(raw_usage)

    # Preserve MCP binding from parent so branched session inherits
    # character identity + MCP servers (needed for --resume to work with tools).
    new_adapter_config = {
        "always_thinking_enabled": s.adapter_config.get("always_thinking_enabled", False),
        "effort": s.adapter_config.get("effort", ""),
        "max_thinking_tokens": s.adapter_config.get("max_thinking_tokens"),
    }
    for _api_key, _native_key in _CODEX_CONTEXT_SETTING_KEYS:
        if _native_key in s.adapter_config:
            new_adapter_config[_native_key] = s.adapter_config[_native_key]
    if s.adapter_config.get("mcp_servers"):
        new_adapter_config["mcp_servers"] = s.adapter_config["mcp_servers"]

    new_s = sess.create(
        name=name,
        adapter=s.adapter,
        cli_session_id=new_cli_id,
        model=s.model,
        permission_mode=s.permission_mode,
        always_thinking_enabled=s.adapter_config.get("always_thinking_enabled", False),
        effort=s.adapter_config.get("effort", ""),
        max_thinking_tokens=s.adapter_config.get("max_thinking_tokens"),
        raw_usage=raw_usage,
        total_usage=total_usage,
        workdir=s.workdir,
        history=history,
        character_id=s.character_id,
        original_prompt=s.original_prompt,
        handoff_prompt=s.handoff_prompt,
        adapter_config=new_adapter_config,
        pan_access=dict(s.pan_access),
        notification_settings=dict(s.notification_settings),
    )

    await broadcast({
        "type": "session.created",
        "sessionId": new_s.id,
        "name": new_s.name,
    })

    return _session_to_api(new_s)


@app.post("/api/sessions/{session_id}/handoff")
async def api_session_handoff(session_id: str, data: dict):
    """替身交接（session_handoff v1）：创建孪生 session B 接替 A。

    Body: {"handoffPrompt": <必填，A 的 agent 编写的交接简报>,
           "copySettings": bool (默认 true), "adapter"?, "model"?,
           "permissionMode"?}

    行为见 ``sess.handoff_session``：关系网接替 + B 自动 manage A + 可选设置
    复制（不含旧交接简报）+ B.system_prompt = 本次 handoffPrompt 与 A.original_prompt
    拼接 + 重命名（A → "(archive) <原名>"，B → "<原名>"）。
    """
    handoff_prompt = (data.get("handoffPrompt") or "").strip()
    if not handoff_prompt:
        return {"error": "handoffPrompt is required — 由 session A 的 agent 编写交接简报"}
    copy_settings = data.get("copySettings", True)
    if not isinstance(copy_settings, bool):
        copy_settings = True
    adapter = data.get("adapter")
    model = data.get("model")
    permission_mode = data.get("permissionMode")
    if not copy_settings and not adapter:
        return {"error": "adapter is required when copySettings is false"}

    # ── 能力预校验：新 adapter 与 model / settings 组合必须成立 ──
    # 切 adapter 时连继承自 A 的设置也一并核验，避免交接出一个必然启动失败
    # 的 B（不静默换模型/清设置）。
    session_a = sess.get(session_id)
    if session_a is None:
        return {"error": f"Session {session_id} not found"}
    new_adapter_name = adapter or (session_a.adapter if copy_settings else "cbc")
    switched = new_adapter_name != session_a.adapter
    try:
        new_adapter = resolve_adapter(new_adapter_name)
        if model:
            validate_model(new_adapter, model)
        elif copy_settings and session_a.model and switched:
            try:
                validate_model(new_adapter, session_a.model)
            except AdapterCapabilityError:
                return {"error": (
                    f"handoff to adapter '{new_adapter_name}' would inherit "
                    f"model '{session_a.model}' from session A (adapter "
                    f"'{session_a.adapter}'), which does not support it. Pass "
                    f"an explicit supported 'model' or use copySettings=false."
                )}
        if permission_mode:
            validate_permission_mode(new_adapter, permission_mode)
        elif copy_settings and session_a.permission_mode and switched:
            try:
                validate_permission_mode(new_adapter, session_a.permission_mode)
            except AdapterCapabilityError:
                return {"error": (
                    f"handoff to adapter '{new_adapter_name}' would inherit "
                    f"permissionMode '{session_a.permission_mode}' from session "
                    f"A (adapter '{session_a.adapter}'), which does not support "
                    f"it. Pass an explicit 'permissionMode' or use "
                    f"copySettings=false."
                )}
    except AdapterCapabilityError as exc:
        return {"error": str(exc)}

    result = sess.handoff_session(
        session_id, handoff_prompt,
        copy_settings=copy_settings, adapter=adapter, model=model,
        permission_mode=permission_mode,
    )
    if isinstance(result, str):
        return {"error": result}
    a, b = result
    # 跨 adapter 复制设置时，清洗 adapter_config：源 adapter 的 effort /
    # thinking / output_mode / maxThinkingTokens 对新 adapter 不成立的降级为
    # 默认（复制残值的既有降级语义；显式传参已在上面硬校验）。
    if switched and copy_settings:
        b.adapter_config = sanitize_adapter_config(
            new_adapter_name, b.adapter_config, model=b.model)
        sess.save(b)
    await broadcast({
        "type": "session.renamed",
        "sessionId": a.id,
        "oldName": b.name,  # A 原名 == B 现名
        "newName": a.name,
    })
    await broadcast({
        "type": "session.created",
        "sessionId": b.id,
        "name": b.name,
    })
    return {
        "ok": True,
        "archivedSession": _session_to_api(a),
        "session": _session_to_api(b),
    }


def _cleanup_mcp_config(session_id: str) -> None:
    """S3：session 删除后清理 data/mcp-configs/<session_id>.mcp.json。

    cbc 运行期间持续读取该文件（mcp-configs 生命周期立项），但 session
    删除后不再需要，避免残留。文件不存在时静默跳过。
    """
    p = DATA_DIR / "mcp-configs" / f"{session_id}.mcp.json"
    try:
        p.unlink(missing_ok=True)
    except OSError as e:
        _log(f"[mcp-config] 清理失败 {p}: {e}")


def _cleanup_kimi_home(session_id: str) -> None:
    """S3：session 删除后清理 data/kimi-homes/<session_id>/ 隔离 HOME（方案 C）。

    kimi MCP 会话的整个用户目录（config.toml + mcp.json + kimi 自身写入的
    sessions/、session_index.jsonl 等）都在该隔离目录内，删除 session 时一并
    清理，避免 data/ 残留。目录不存在时静默跳过。
    """
    p = DATA_DIR / "kimi-homes" / session_id
    try:
        if p.exists():
            shutil.rmtree(p)
    except OSError as e:
        _log(f"[kimi-home] 清理失败 {p}: {e}")


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    """Delete a session and its worker if running."""
    sess.release(session_id)  # 清理 managed 关系 + 各 manager 的 report 订阅残留（B1）
    w = worker.find_worker_by_session(session_id)
    if w:
        asyncio.create_task(
            worker.cleanup_worker_background(w.worker_id, w.session_id)
        )
    sess.delete(session_id)
    _cleanup_mcp_config(session_id)
    _cleanup_kimi_home(session_id)
    await broadcast({
        "type": "session.deleted",
        "sessionId": session_id,
    })
    return {"sessionId": session_id, "status": "deleted"}


@app.post("/api/sessions/batch-delete")
async def api_batch_delete_sessions(data: dict):
    """Delete sessions and workers, optionally expanding managed descendants.

    ``sessionIds`` retains the historical exact-delete behavior.  When
    ``cascadeSessionIds`` is supplied, the server expands those roots from
    their persisted ``managed`` lists, deduplicates shared descendants, and
    deletes child sessions before their parents.  This keeps the browser from
    having to guess a nested relationship graph.
    """
    session_ids = data.get("sessionIds", [])
    if not session_ids:
        return {"error": "sessionIds is required"}

    cascade_ids = [sid for sid in data.get("cascadeSessionIds", [])
                   if sid in session_ids]
    ordered_ids = list(dict.fromkeys(
        sess.expand_managed_descendants(cascade_ids) + list(session_ids)
    ))
    deleted = 0
    for sid in ordered_ids:
        sess.release(sid)  # 清理 managed 关系 + 各 manager 的 report 订阅残留（B1）
        w = worker.find_worker_by_session(sid)
        if w:
            asyncio.create_task(
                worker.cleanup_worker_background(w.worker_id, w.session_id)
            )
        sess.delete(sid)
        _cleanup_mcp_config(sid)
        _cleanup_kimi_home(sid)
        deleted += 1

    await broadcast({
        "type": "sessions.deleted",
        "sessionIds": ordered_ids,
    })

    return {"deleted": deleted, "sessionIds": ordered_ids}


@app.get("/api/models")
async def api_models(adapter: str = "cbc"):
    """Return model list and default for a given adapter."""
    a = _safe_adapter(adapter)
    return {"models": a.supported_models, "default": a.default_model}


@app.post("/api/sessions/broadcast")
async def api_sessions_broadcast(data: dict):
    """Send one message to selected Sessions through the normal send path.

    This is an immediate fan-out primitive.  Scheduled fan-out uses the same
    per-target send semantics through the durable time-Job endpoint below.
    """
    session_ids = data.get("sessionIds")
    text = data.get("text")
    if not isinstance(session_ids, list) or not session_ids:
        return {"ok": False, "error": {"code": "missing_session_ids",
                                         "message": "sessionIds must be a non-empty array"}}
    if not isinstance(text, str) or not text:
        return {"ok": False, "error": {"code": "missing_text",
                                         "message": "text is required"}}
    source_type, source_session_id, source_error = _request_source_metadata(data)
    if source_error:
        return source_error
    unique_ids = list(dict.fromkeys(str(value) for value in session_ids))
    results = []
    for session_id in unique_ids:
        target = sess.get(session_id)
        if not target:
            results.append({"sessionId": session_id, "status": "error",
                            "result": f"Session {session_id} not found"})
            continue
        denied = _source_access_error(target, source_session_id)
        if denied:
            results.append({"sessionId": session_id, "status": "error",
                            "error": denied["error"]})
            continue
        result = await worker.send_session(
            session_id, text, source=source_type, force=bool(data.get("force")),
            source_session_id=source_session_id)
        results.append({"sessionId": session_id, **result})
    failed = [item for item in results if item.get("status") == "error"]
    return {"ok": not failed, "status": "partial" if failed and len(failed) < len(results)
            else ("error" if failed else "queued"), "results": results}


# ── Durable background jobs ──

@app.post("/api/background-jobs")
async def api_background_job_start(data: dict):
    """Create a durable process Job with independent creator/target fields."""
    target = data.get("targetSessionId")
    argv = data.get("argv")
    cwd = data.get("cwd")
    try:
        return background_jobs.start(
            target, argv, cwd, label=data.get("label"),
            creator_session_id=data.get("creatorSessionId"),
        )
    except ValueError as exc:
        return {"ok": False, "error": {"code": "invalid_job", "message": str(exc)}}
    except OSError as exc:
        return {"ok": False, "error": {"code": "runner_spawn_failed", "message": str(exc)}}


@app.get("/api/background-jobs")
async def api_background_job_list(targetSessionId: str | None = None):
    # Service lifecycle Jobs share the durable Registry but are not ordinary
    # Session-targeted background commands and must not leak into this API.
    jobs = [job for job in background_jobs.list_jobs()
            if job.get("kind") in {background_jobs.BACKGROUND_PROCESS_KIND,
                                    background_jobs.SESSION_MESSAGE_KIND,
                                    background_jobs.SESSION_BROADCAST_KIND}]
    if targetSessionId:
        jobs = [j for j in jobs if (j.get("targetSessionId") == targetSessionId
                                   or targetSessionId in (j.get("targetSessionIds") or []))]
    return {"jobs": jobs}


@app.get("/api/background-jobs/{job_id}")
async def api_background_job_get(job_id: str):
    job = background_jobs.get(job_id)
    return job or {"ok": False, "error": {"code": "job_not_found", "message": "job not found"}}


@app.post("/api/background-jobs/{job_id}/cancel")
async def api_background_job_cancel(job_id: str):
    try:
        return background_jobs.cancel(job_id)
    except ValueError as exc:
        code = "cancel_unsafe" if "safely cancel" in str(exc) else "job_not_found"
        return {"ok": False, "error": {"code": code, "message": str(exc)}}


@app.post("/api/session-message-jobs")
async def api_session_message_job_start(data: dict):
    """Create a durable Session message or scheduled broadcast.

    ``text`` is delivered as message text; it is never an OS command.
    """
    target = data.get("targetSessionId")
    target_ids = data.get("targetSessionIds")
    text = data.get("text")
    source_type, source_session_id, source_error = _request_source_metadata(data)
    if source_error:
        return source_error
    if target_ids is not None and target is not None:
        return {"ok": False, "error": {"code": "invalid_job",
                                         "message": "provide targetSessionId or targetSessionIds, not both"}}
    if target_ids is None:
        target_ids = [target] if isinstance(target, str) else None
    if not isinstance(target_ids, list) or not target_ids:
        return {"ok": False, "error": {"code": "invalid_job",
                                         "message": "targetSessionId or targetSessionIds is required"}}
    if any(not isinstance(target_id, str) or not target_id.strip()
           for target_id in target_ids):
        return {"ok": False, "error": {"code": "invalid_job",
                                         "message": "target session IDs must be non-empty strings"}}
    target_ids = list(dict.fromkeys(target_ids))
    for target_id in target_ids:
        target_session = sess.get(target_id) if isinstance(target_id, str) else None
        if not target_session:
            return {"ok": False, "error": {"code": "invalid_job",
                                             "message": "target session does not exist"}}
        denied = _source_access_error(target_session, source_session_id)
        if denied:
            return denied
    try:
        common = {
            "description": data.get("description", data.get("label")),
            "source": source_type, "source_session_id": source_session_id,
            "creator_session_id": data.get("creatorSessionId") or source_session_id,
        }
        if len(target_ids) == 1 and data.get("targetSessionIds") is None:
            return background_jobs.start_message(
                target_ids[0], text, data.get("schedule"), **common)
        return background_jobs.start_broadcast(
            target_ids, text, data.get("schedule"), **common)
    except ValueError as exc:
        return {"ok": False, "error": {"code": "invalid_job", "message": str(exc)}}


@app.patch("/api/background-jobs/{job_id}")
async def api_background_job_update(job_id: str, data: dict):
    try:
        job = background_jobs.get(job_id)
        if not job or job.get("kind") not in {
            background_jobs.SESSION_MESSAGE_KIND,
            background_jobs.SESSION_BROADCAST_KIND,
        }:
            raise ValueError("message Job not found")
        return background_jobs.update_message(
            job_id, text=data.get("text"), schedule=data.get("schedule"),
            description=data.get("description"),
            target_session_ids=data.get("targetSessionIds"))
    except ValueError as exc:
        code = "job_not_found" if "not found" in str(exc) else "invalid_job"
        return {"ok": False, "error": {"code": code, "message": str(exc)}}


@app.post("/api/background-jobs/{job_id}/retry")
async def api_background_job_retry(job_id: str):
    try:
        return background_jobs.retry(job_id)
    except ValueError as exc:
        code = "job_not_retryable" if "cannot be retried" in str(exc) else "invalid_job"
        return {"ok": False, "error": {"code": code, "message": str(exc)}}


@app.get("/api/adapter/config")
async def api_adapter_config(adapter: str = "cbc"):
    """Return adapter configuration for frontend selects (per-adapter dynamic)."""
    a = _safe_adapter(adapter)
    return {
        "adapter": a.name,
        "models": a.supported_models,
        "defaultModel": a.default_model,
        "effortValues": list(a.effort_values),
        "modelEfforts": getattr(a, "model_efforts", {}),
        "permissionModes": a.permission_modes,
        "defaultPermissionMode": a.default_permission_mode,
        "supportedSettings": getattr(a, "supported_settings", ["model", "permissionMode", "thinking", "effort"]),
        "executionModes": list(a.execution_modes),
    }


@app.get("/api/adapters")
async def api_list_adapters():
    """Return all registered adapter names and basic info."""
    adapters = list_adapters()
    return {
        "adapters": [
            {
                "name": a.name,
                "defaultModel": a.default_model,
                "supportsResume": a.supports_resume,
                "supportsFork": a.supports_fork,
            }
            for a in adapters
        ],
        "default": "cbc",
    }


# ── App settings (config.json ui) ──

@app.get("/api/settings/ui")
async def api_get_settings_ui():
    """Return the app-settings ``ui`` object (merged with defaults).

    Backs the React ``appSettingsStore``. config.json's ``ui`` object is the
    single source of truth for these display-level settings, shared across
    browsers/sessions (previously they lived in browser localStorage).
    """
    return load_config().get("ui") or {}


@app.put("/api/settings/ui")
async def api_put_settings_ui(data: dict):
    """Merge partial/full ui settings into config.json's ``ui`` object.

    Body may carry any subset of the ui fields; provided keys are merged over
    the current values (defaults fill the rest) and persisted atomically.
    Unknown keys are stored as-is for forward compatibility. Returns the full
    merged ui object.
    """
    ui = dict(load_config().get("ui") or {})
    ui.update(data)
    raw = read_config_file()
    raw["ui"] = ui
    save_config(raw)
    return ui


# ── Worker settings (config.json worker, hot-applied) ──

_WORKER_SETTING_KEYS = ("timeout_sec", "task_timeout_sec", "idle_sec")


@app.put("/api/settings/worker")
async def api_put_settings_worker(data: dict):
    """Merge worker lifecycle timeouts into config.json's ``worker`` object.

    Body may carry any subset of {timeout_sec, task_timeout_sec, idle_sec};
    each provided value must be a positive number (seconds). Provided keys
    are merged over the current worker section (unprovided keys and other
    fields in the section are kept) and persisted atomically, then
    worker.reload_worker_config() re-reads the file so the running server
    applies the new values without a restart. Returns the {before, after}
    diff in the same shape as the ``worker`` entry of /api/config/reload.
    """
    patch: dict = {}
    for k in _WORKER_SETTING_KEYS:
        if k not in data:
            continue
        try:
            v = float(data[k])
        except (TypeError, ValueError):
            return {"error": f"worker.{k} must be a number (seconds)"}
        if not (math.isfinite(v) and v > 0):  # rejects 0 / negative / inf / nan
            return {"error": f"worker.{k} must be a positive number (seconds)"}
        patch[k] = v
    if not patch:
        return {
            "error": "no worker keys provided (expected any of: "
            + ", ".join(_WORKER_SETTING_KEYS)
            + ")"
        }
    raw = read_config_file()
    section = dict(raw.get("worker") or {})
    section.update(patch)
    raw["worker"] = section
    save_config(raw)
    return worker.reload_worker_config()


# ── Remote tunnel (cloudflared, scripts/start_cf.ps1) ──
#
# Pan's own tunnel cloudflared is started by scripts/start_cf.ps1 with a
# generated temp yml (%TEMP%/pan_cf_config_<port>.yml). PidFiles written at
# start are deleted by start_pan.bat, so running processes are identified by
# command line instead — the temp-yml marker is unique to Pan's tunnel and
# never matches service installs (e.g. cloudflared-ssh).

_PAN_TUNNEL_MARKER = "pan_cf_config_"


def _matches_pan_tunnel(name: str, cmdline: str) -> bool:
    """Pure matcher for Pan's own tunnel cloudflared process.

    True only when the process is a cloudflared binary AND its command line
    references the temp tunnel config marker (pan_cf_config_<port>.yml).
    Service processes (cloudflared-ssh etc.) never reference that file and
    are never matched — mirrors scripts/stop_pan.bat's precise 5c fallback.
    """
    if not cmdline:
        return False
    base = name.lower()
    if not base.startswith("cloudflared"):
        return False
    return _PAN_TUNNEL_MARKER in cmdline


def _find_pan_tunnel_processes() -> list[dict]:
    """Return [{pid, name, cmdline}] for Pan's cloudflared tunnel processes."""
    procs: list[dict] = []
    try:
        import psutil

        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = p.info
                cmdline = " ".join(info.get("cmdline") or [])
                if _matches_pan_tunnel(info.get("name") or "", cmdline):
                    procs.append({
                        "pid": info["pid"],
                        "name": info.get("name") or "",
                        "cmdline": cmdline,
                    })
            except Exception:
                continue
    except ImportError:
        # PowerShell fallback (same matcher as scripts/stop_pan.bat 5c).
        try:
            out = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\""
                    " | Select-Object ProcessId,CommandLine | ConvertTo-Json",
                ],
                capture_output=True, text=True, timeout=15,
            )
            data = json.loads(out.stdout or "null")
            items = data if isinstance(data, list) else ([data] if data else [])
            for it in items:
                cmdline = it.get("CommandLine") or ""
                if _matches_pan_tunnel("cloudflared.exe", cmdline):
                    procs.append({
                        "pid": int(it["ProcessId"]),
                        "name": "cloudflared.exe",
                        "cmdline": cmdline,
                    })
        except Exception as e:
            _log(f"[remote] cloudflared process scan failed: {e}")
    return procs


def _kill_pan_tunnel_processes(procs: list[dict]) -> list[int]:
    """Kill the given processes (tree-kill); returns pids actually killed."""
    killed: list[int] = []
    for pr in procs:
        try:
            r = subprocess.run(
                ["taskkill", "/PID", str(pr["pid"]), "/T", "/F"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                killed.append(pr["pid"])
            else:
                _log(f"[remote] kill pid {pr['pid']} failed: {r.stderr.strip()}")
        except Exception as e:
            _log(f"[remote] kill pid {pr['pid']} error: {e}")
    return killed


@app.get("/api/remote/status")
async def api_remote_status():
    """Remote tunnel status for the App Settings modal.

    ``available`` reflects the raw on-disk config (remote section present);
    ``enabled`` comes from the merged config. ``running`` = a Pan tunnel
    cloudflared process was found by command-line match.
    """
    config = load_config()
    raw = read_config_file()
    remote = config.get("remote") or {}
    return {
        "available": "remote" in raw,
        "enabled": bool(remote.get("enabled")),
        "provider": remote.get("provider"),
        "quickTunnel": bool(remote.get("quick_tunnel")),
        "protocol": remote.get("protocol") or "",
        "port": config.get("port"),
        "running": bool(_find_pan_tunnel_processes()),
    }


@app.post("/api/remote/restart")
async def api_remote_restart():
    """Restart Pan's cloudflared tunnel via scripts/start_cf.ps1.

    Only processes whose command line carries the temp-yml marker are killed
    (never the cloudflared-ssh service). Restarting re-runs start_cf.ps1 —
    the same entry point start_pan.bat uses — so the freshly generated temp
    yml picks up current config.json values (port + remote.protocol).
    """
    config = load_config()
    remote = config.get("remote") or {}
    if not remote.get("enabled"):
        return {"ok": False, "error": "remote is not enabled in config.json"}

    killed = _kill_pan_tunnel_processes(_find_pan_tunnel_processes())

    script = _PROJECT_DIR / "scripts" / "start_cf.ps1"
    if not script.exists():
        return {"ok": False, "error": f"start script not found: {script}",
                "killed": killed}
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script)],
            capture_output=True, text=True, timeout=30,
            cwd=str(_PROJECT_DIR),
        )
    except Exception as e:
        return {"ok": False, "error": str(e), "killed": killed}
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()[-500:]
        return {"ok": False, "error": f"start_cf.ps1 failed: {err}",
                "killed": killed}

    # Give cloudflared a moment to appear, then confirm via process scan.
    await asyncio.sleep(2)
    restarted = bool(_find_pan_tunnel_processes())
    return {"ok": True, "killed": killed, "restarted": restarted}


# ── Config hot-reload ──

def _reload_adapter_models() -> tuple[list[dict], list[str]]:
    """Invalidate adapter model caches and return count diffs and errors."""
    adapters_out = []
    errors: list[str] = []
    for a in list_adapters():
        entry: dict = {"name": a.name}
        try:
            entry["modelsBefore"] = len(a.supported_models)
        except Exception as e:
            entry["modelsBefore"] = None
            errors.append(f"{a.name}: read before: {e}")
        invalidate = getattr(a, "invalidate_models_cache", None)
        if callable(invalidate):
            try:
                invalidate()
            except Exception as e:
                errors.append(f"{a.name}: invalidate: {e}")
        try:
            entry["modelsAfter"] = len(a.supported_models)
        except Exception as e:
            entry["modelsAfter"] = None
            errors.append(f"{a.name}: read after: {e}")
        adapters_out.append(entry)
    return adapters_out, errors


@app.post("/api/codex/refresh-official-models")
async def api_codex_refresh_official_models():
    """Replace the Codex whitelist with the visible official model catalog."""
    try:
        codex_argv = [str(part) for part in get_adapter("codex").resolved_cli_argv()]
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"failed to resolve codex executable: {e}",
        ) from e
    if not codex_argv:
        raise HTTPException(status_code=502, detail="failed to resolve codex executable: empty argv")

    command = [*codex_argv, "debug", "models"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(_PROJECT_DIR),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="codex debug models timed out")
    except OSError as e:
        raise HTTPException(status_code=502, detail=f"failed to run codex debug models: {e}") from e

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "command failed").strip()
        raise HTTPException(status_code=502, detail=f"codex debug models failed: {message[-500:]}")

    try:
        catalog = json.loads(completed.stdout)
        if isinstance(catalog, dict):
            if "models" not in catalog:
                raise ValueError("expected a JSON object with models[] or a JSON array")
            catalog = catalog["models"]
        if not isinstance(catalog, list):
            raise ValueError("expected a JSON object with models[] or a JSON array")
        models = []
        for item in catalog:
            if not isinstance(item, dict):
                raise ValueError("catalog entries must be objects")
            if item.get("visibility") in (None, "list"):
                slug = item.get("slug")
                if not isinstance(slug, str) or not slug.strip():
                    raise ValueError("visible catalog entry has no valid slug")
                models.append(slug.strip())
        if not models:
            raise ValueError("catalog contains no visible models")
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        raise HTTPException(status_code=502, detail=f"invalid codex model catalog: {e}")

    before = list(get_adapter("codex").supported_models)
    raw = read_config_file()
    codex = dict(raw.get("codex") or {})
    codex["models"] = models
    raw["codex"] = codex
    save_config(raw)

    _, reload_errors = _reload_adapter_models()
    if reload_errors:
        raise HTTPException(status_code=500, detail="; ".join(reload_errors))
    after = list(get_adapter("codex").supported_models)
    return {"ok": True, "before": before, "after": after}

@app.post("/api/config/reload")
async def api_config_reload(data: dict | None = None):
    """Force a config.json hot-reload (adapters / worker / plugin / memory / python).

    config.json is re-read from disk on every load_config() call, but a few
    things are read once and then cached: the adapters' class-level model-list
    caches (codex/opencode/cbc with TTL, kimi/claude permanent), worker.py's
    module-level lifecycle timeouts, worker.py's memory-injection switch
    (``memory.enabled``), and the registered plugin manifest list
    (``plugin_manifests``, fixed at lifespan). This endpoint invalidates all
    of them so edits to config.json take effect without a server restart —
    same style as POST /api/manifest/reload.

    Body (optional): ``{"scope": "adapters" | "worker" | "plugin" | "memory"
    | "python" | "all"}`` — default "all". Idempotent: repeated calls just re-read the
    same config. Per-item failures are collected into ``errors`` and reported
    with ``reloaded: false`` instead of a 500. The response always carries
    ``requiresRestart`` — fields that are startup-frozen by nature and can
    never hot-apply (the bound port, logging handlers, the Windows startup
    console window, and the external remote tunnel process).
    """
    scope = (data or {}).get("scope") or "all"
    if scope not in ("adapters", "worker", "plugin", "memory", "python", "all"):
        return {"reloaded": False, "error": f"Unknown scope: {scope}"}

    result: dict = {"reloaded": True}
    errors: list[str] = []

    if scope in ("python", "all"):
        # The resolver is intentionally uncached.  This metadata proves the
        # fresh choice without returning a configured path or environment
        # value, either of which could contain sensitive information.
        try:
            result["python"] = resolve_pan_python_info()
        except Exception as e:
            errors.append(f"python: {e}")

    if scope in ("adapters", "all"):
        adapters_out, adapter_errors = _reload_adapter_models()
        errors.extend(adapter_errors)
        result["adapters"] = adapters_out

    if scope in ("worker", "all"):
        try:
            result["worker"] = worker.reload_worker_config()
        except Exception as e:
            errors.append(f"worker: {e}")

    if scope in ("memory", "all"):
        try:
            result["memory"] = worker.reload_memory_config()
        except Exception as e:
            errors.append(f"memory: {e}")

    if scope in ("plugin", "all"):
        if _character_manager is None:
            errors.append("plugin: character manager not initialized")
        else:
            before_paths = list(_character_manager._plugin_paths)
            # Same default as lifespan: a missing key loads the project and
            # first-party MCP manifests; an explicit [] clears all plugins.
            new_paths = list(
                load_config().get("plugin_manifests", DEFAULT_PLUGIN_MANIFESTS)
            )
            cfg, plug_errors = _character_manager.reload_plugin_paths(new_paths)
            plugin_entry: dict = {"before": before_paths, "after": new_paths}
            if cfg is None:
                # Aborted: previous paths + config kept by the manager.
                plugin_entry["applied"] = False
                plugin_entry["errors"] = plug_errors
                errors.extend(f"plugin: {e}" for e in plug_errors)
            else:
                plugin_entry["applied"] = True
                plugin_entry["sessionTemplates"] = len(cfg.session_templates)
                plugin_entry["mcpServers"] = len(cfg.mcp_servers)
                plugin_entry["characters"] = len(cfg.character_templates)
                plugin_entry["commandRoutes"] = len(cfg.command_routes)
            result["plugin"] = plugin_entry

    # Startup-frozen fields a config.json edit can never hot-apply.
    result["requiresRestart"] = ["port", "logging", "remote", "startup"]

    if errors:
        result["reloaded"] = False
        result["errors"] = errors
    return result


# ── Spawn ──

@app.post("/api/spawn")
async def api_spawn(data: dict):
    """Spawn a Worker for a Session."""
    session_id = data.get("sessionId")
    if session_id:
        s = sess.get(session_id)
        if not s:
            return {"error": f"Session {session_id} not found"}
        # 已有 Session 的 spawn 不接受切换 adapter（此前被静默忽略）。
        # 换 adapter 是结构性切换（进程 argv/协议都不同），必须走 handoff。
        req_adapter = data.get("adapter")
        if req_adapter and req_adapter != s.adapter:
            return {"error": (
                f"Session '{s.name}' uses adapter '{s.adapter}' and cannot "
                f"be spawned with adapter '{req_adapter}'. Adapter switching "
                f"requires POST /api/sessions/{session_id}/handoff."
            )}
        # kill existing worker (avoid multiple workers on same session)
        existing = worker.find_worker_by_session(session_id)
        if existing:
            await worker.kill_worker(existing.worker_id)
        try:
            _apply_session_updates(s, data)
        except ValueError as e:
            return {"error": str(e)}
        sess.save(s)
    else:
        try:
            params = _build_session_params(data)
        except ValueError as e:
            return {"error": str(e)}
        name = params["name"]
        err_name = _check_session_name(name)
        if err_name:
            return {"error": err_name}
        s = sess.create(**params)
        session_id = s.id
        await broadcast({
            "type": "session.created",
            "sessionId": s.id,
            "name": s.name,
        })

    result = await worker.create_worker(session_id)
    if isinstance(result, str):
        return {"error": result}

    w = result
    return {
        "workerId": w.worker_id,
        "sessionId": session_id,
        "name": s.name,
        "status": w.status,
        "model": s.model or get_adapter(s.adapter).default_model,
    }


def _request_source_metadata(data: dict, default: str = "agent"):
    """Parse the two independent source fields at an HTTP boundary.

    只读校验（不覆盖/改写 data）：source 缺失时按 default（agent），显式传入
    的 source 必须在合法集合内（worker.SOURCE_TYPES，含未来 meta-agent /
    automation），非法类型或未知字符串直接拒绝——HTTP handler 不得在调用本函数
    前把客户端 source 无条件覆盖为 agent。sourceSessionId 校验其确为存在的
    Session。返回 (source_type, source_session_id, None) 或
    (None, None, {"error": ...})。
    """
    source_type, error = worker._normalize_source_type(data.get("source"), default)
    if error:
        return None, None, {"error": error}
    source_session_id, error = worker._normalize_source_session_id(
        data.get("sourceSessionId"))
    if error:
        return None, None, {"error": error}
    return source_type, source_session_id, None


def _source_access_error(target, source_session_id):
    if (target is not None and source_session_id
            and source_session_id != target.id and target.readonly_session
            and target.managed_by == source_session_id):
        return {"ok": False, "error": {"code": "readonly_session",
                "message": worker.READONLY_SESSION_ERROR}}
    return None


@app.post("/api/task")
async def api_task(data: dict):
    """Send a task to a Worker by worker_id or session_id."""
    worker_id = data.get("workerId")
    session_id = data.get("sessionId")
    # source 是开放但受校验的来源元数据：先校验再走流程（缺省 agent，未知/
    # 非法 source 直接拒绝），绝不把客户端传入的 source 无条件覆盖为 agent。
    source_type, source_session_id, source_error = _request_source_metadata(data)
    if source_error:
        return source_error
    if session_id:
        target = sess.get(session_id)
        if not target:
            return {"error": f"Session {session_id} not found"}
        denied = _source_access_error(target, source_session_id)
        if denied:
            return denied

    if not worker_id and session_id:
        w = worker.find_alive_worker_by_session(session_id)
        if w:
            worker_id = w.worker_id

    if not worker_id and session_id:
        s = sess.get(session_id)
        if not s:
            return {"error": f"Session {session_id} not found"}
        result = await worker.create_worker(session_id)
        if isinstance(result, str):
            return {"error": f"Worker auto-spawn failed: {result}"}
        worker_id = result.worker_id
        await broadcast({
            "type": "worker.spawned",
            "workerId": worker_id,
            "sessionId": session_id,
            "generation": result.generation,
            "name": s.name,
            "status": "idle",
            "model": s.model or get_adapter(s.adapter).default_model,
            "reason": "auto-spawned by /api/task",
        })

    if not worker_id:
        return {"error": "workerId or sessionId required"}

    text = data.get("text")
    if not text:
        return {"error": "text is required"}

    if not session_id:
        resolved = worker.get_worker(worker_id)
        session_id = resolved.session_id if resolved else None
    if session_id:
        target = sess.get(session_id)
        if target:
            denied = _source_access_error(target, source_session_id)
            if denied:
                return denied
    send_kwargs = {"source": source_type}
    if data.get("taskId") is not None:
        send_kwargs["task_id"] = data.get("taskId")
    elif data.get("inheritTaskId") and source_type == "agent" and session_id:
        # agent_send_force uses this legacy task route after restarting.  Keep
        # the same enqueue-time snapshot as /api/send without turning the
        # inherited id into a second assign idempotency key.
        send_kwargs["task_id"] = target.active_task_id if target else None
        send_kwargs["idempotent_task_id"] = False
    if data.get("clientMessageId") is not None:
        send_kwargs["client_message_id"] = data.get("clientMessageId")
    if source_session_id is not None:
        send_kwargs["source_session_id"] = source_session_id
    err = await worker.send_task(worker_id, text, **send_kwargs)
    if err:
        if session_id and err in ("Worker not found", "Worker process dead"):
            old = worker.find_worker_by_session(session_id)
            if old:
                await worker.kill_worker(old.worker_id)
            s = sess.get(session_id)
            if s:
                result = await worker.create_worker(session_id)
                if not isinstance(result, str):
                    worker_id = result.worker_id
                    err = await worker.send_task(worker_id, text, **send_kwargs)
        if err:
            return {"error": err}

    w = worker.get_worker(worker_id)
    return {
        "workerId": worker_id,
        "sessionId": w.session_id if w else session_id,
        "status": "queued",
        "queueItemId": (worker._find_queue_item_by_idempotency(
            sess.get(session_id), client_message_id=data.get("clientMessageId"),
            task_id=data.get("taskId")) or {}).get("queueItemId"),
    }


@app.post("/api/send")
async def api_send(data: dict):
    """向 session（agent）发消息（阶段 6 寻址兼容层）。

    workerId / sessionId 皆可寻址（编排对象是 agent，进程是顺带的）。
    sessionId 无活 worker 时**不报错**：消息入 Session.queue_pending
    （type=task），由全局 watchdog spawn 后经 _recover_pending_signals
    分发。force=true 时对活 worker 先 restart 再投递（worker_send_force 语义）。
    source=agent 的消息在入队时继承目标 Session.active_task_id 作为 report
    配对上下文；该值不参加 assign 幂等，source=user 不继承。
    隔离由 MCP 层实施（与 /api/claim 同约定），本端点不检查 pan_access。
    """
    worker_id = data.get("workerId")
    session_id = data.get("sessionId")
    text = data.get("text")
    if not text:
        return {"error": "text is required"}
    if not worker_id and not session_id:
        return {"error": "workerId or sessionId required"}
    # source 契约先于寻址/投递校验：缺省 agent，非法类型/未知字符串拒绝，
    # 合法 source 保留透传（与 /api/task、/api/notify 同约定）。
    source_type, source_session_id, source_error = _request_source_metadata(data)
    if source_error:
        return source_error
    if worker_id and not session_id:
        w = worker.get_worker(worker_id)
        if not w:
            return {"error": "Worker not found"}
        session_id = w.session_id
    target = sess.get(session_id)
    if source_session_id and not target:
        return {"error": f"Session {session_id} not found"}
    denied = _source_access_error(target, source_session_id)
    if denied:
        return denied
    send_kwargs = {"source": source_type, "force": bool(data.get("force"))}
    if data.get("clientMessageId") is not None:
        send_kwargs["client_message_id"] = data.get("clientMessageId")
    if source_session_id is not None:
        send_kwargs["source_session_id"] = source_session_id
    result = await worker.send_session(session_id, text, **send_kwargs)
    if isinstance(result, dict) and result.get("status") == "error":
        return {"error": result.get("result") or "send failed"}
    return result


@app.post("/api/notify")
async def api_notify(data: dict):
    """[internal/MCP-only] 向 session 投递一条提醒（MCP agent_notify 专用）。

    不作为通用对外 API（README/API 文档不宣传）；隔离由 MCP 层
    _check_access 实施（仅允许调用方自己或其 managed 的 session），本端点
    与 /api/send 同约定不检查 pan_access。

    Body: {"targetSessionId": <目标 session id>, "text": <提醒正文>,
           "source"?: <来源类型，如 agent>,
           "sourceSessionId"?: <来源 session id>}

    内部走 worker.enqueue_notice：与 report 相同的持久化投递链路
    （queue_pending 落盘 + 唤醒 + 无活 worker 立即 auto-spawn，不等
    watchdog tick）。区别于 /api/send：投递的是「通知」而非任务消息。
    """
    target = (data.get("targetSessionId") or data.get("sessionId") or "").strip()
    text = data.get("text")
    if not target or not text:
        return {"error": "targetSessionId and text are required"}
    source_type, source_session_id, source_error = _request_source_metadata(data)
    if source_error:
        return source_error
    target_session = sess.get(target)
    if not target_session:
        return {"error": f"Session {target} not found"}
    denied = _source_access_error(target_session, source_session_id)
    if denied:
        return denied
    notice_kwargs = {"source": source_type}
    if source_session_id is not None:
        notice_kwargs["source_session_id"] = source_session_id
    result = await worker.enqueue_notice(target, str(text),
                                         **notice_kwargs)
    if isinstance(result, dict) and not result.get("ok", True):
        return {"error": (result.get("error") or {}).get("message", "notify failed")}
    return result


@app.post("/api/assign")
async def api_assign(data: dict):
    """异步分派：发任务后立即返回 queued，完成时通过 worker.result 事件回调。

    taskId 可选：带 taskId 时走幂等语义（同 taskId 重发不双跑），见 worker.assign；
    成功入队后该值成为 Session 的持久 active_task_id。
    """
    session_id = data.get("sessionId")
    text = data.get("text")
    if not session_id or not text:
        return {"ok": False, "error": {"code": "missing_params",
                                       "message": "sessionId and text are required"}}
    task_id = data.get("taskId")
    # 同其它 HTTP handler：source 契约先校验（缺省 agent，非法拒绝，合法保留）。
    source_type, source_session_id, source_error = _request_source_metadata(data)
    if source_error:
        return {"status": "error", "result": source_error["error"]}
    target = sess.get(session_id)
    if not target:
        return {"status": "error", "result": f"Session {session_id} not found"}
    denied = _source_access_error(target, source_session_id)
    if denied:
        return {"status": "error", "result": denied["error"]["message"]}
    return await worker.assign(session_id, text,
                               source=source_type,
                               source_session_id=source_session_id,
                               task_id=task_id)


@app.post("/api/report-subscribe")
async def api_report_subscribe(data: dict):
    """meta-agent 订阅某个 managed session 的完成报告（立项 4.3 订阅制）。

    Body: {"managerId": <meta-agent session id>, "sessionId": <managed session id>}

    订阅后，该 managed session 每次完成（done/error）都会把报告 append 到
    meta-agent 的落盘队列 queue_pending（报告形状 {status,result,sessionId,taskId,workerId}）。
    未订阅则只保留现有 worker.result 广播。
    """
    manager_id = (data.get("managerId") or "").strip()
    session_id = (data.get("sessionId") or "").strip()
    if not manager_id or not session_id:
        return {"error": "managerId and sessionId are required"}
    # 禁止自订阅：meta-agent 不能订阅自己（自管理/自订阅无意义且会自我唤醒）
    if manager_id == session_id:
        return {"error": f"Cannot subscribe to itself ({session_id})"}
    manager = sess.get(manager_id)
    if not manager:
        return {"error": f"Manager session {manager_id} not found"}
    target = sess.get(session_id)
    if not target:
        return {"error": f"Session {session_id} not found"}
    # 软约束：已有归属且不属于该 manager → 拒绝（防止越权订阅）
    # A deleted manager may remain in historical data as a dangling reference;
    # treat that target as unmanaged so it can be recovered.
    if (target.managed_by and target.managed_by != manager_id
            and sess.get(target.managed_by) is not None):
        return {"error": f"Session {session_id} is managed by {target.managed_by}, not {manager_id}"}
    # 订阅即接管：建立 managed 关系（双向落盘，立项 4.2）。
    # claim 内部已自动 report_subscribe；此处 add 幂等，保留作防御性兜底。
    sess.claim(manager_id, session_id)
    manager.report_subscriptions.add(session_id)
    sess.save(manager)
    return {
        "managerId": manager_id,
        "sessionId": session_id,
        "subscribed": True,
        "reportSubscriptions": sorted(manager.report_subscriptions),
    }


@app.post("/api/report-unsubscribe")
async def api_report_unsubscribe(data: dict):
    """取消订阅 managed session 的完成报告（立项 4.3）。"""
    manager_id = (data.get("managerId") or "").strip()
    session_id = (data.get("sessionId") or "").strip()
    if not manager_id or not session_id:
        return {"error": "managerId and sessionId are required"}
    manager = sess.get(manager_id)
    if not manager:
        return {"error": f"Manager session {manager_id} not found"}
    manager.report_subscriptions.discard(session_id)
    sess.save(manager)
    return {
        "managerId": manager_id,
        "sessionId": session_id,
        "subscribed": session_id in manager.report_subscriptions,
        "reportSubscriptions": sorted(manager.report_subscriptions),
    }


# ── QQ session 绑定（订阅 inbox 更新提醒，镜像 report-subscribe 链路）──


@app.post("/api/qq/subscribe")
async def api_qq_subscribe(data: dict):
    """Pan session 订阅某 QQ 会话的 inbox 更新提醒（镜像 report-subscribe）。

    Body: {"sessionId": <pan session id>, "target_type": "user"|"group",
           "target_id": <QQ 号 / 群号>, "bot_uin"?: <bot QQ 号>}

    订阅后，该 QQ 会话每次收到新消息（selective 模式入 inbox）都会推送一条
    `@@@@by qq` 提醒到本 session 的落盘队列 queue_pending 并唤醒其 worker。

    bot_uin 可选（多账号）：订阅粒度为「某 bot 的某用户/群」，订阅键
    `<type>:<id>@<bot_uin>`；缺省订阅键 `<type>:<id>`（不区分 bot，任何 bot
    收到都提醒，兼容旧订阅）。
    """
    session_id = (data.get("sessionId") or "").strip()
    target_type = (data.get("target_type") or "").strip().lower()
    target_id = (data.get("target_id") or "").strip()
    bot_uin = (data.get("bot_uin") or "").strip()
    if not session_id or not target_id or target_type not in ("user", "group"):
        return {"error": "sessionId, target_type(user|group) and target_id are required"}
    s = sess.get(session_id)
    if not s:
        return {"error": f"Session {session_id} not found"}
    target_key = f"{target_type}:{target_id}@{bot_uin}" if bot_uin else f"{target_type}:{target_id}"
    s.qq_subscriptions.add(target_key)
    sess.save(s)
    return {
        "sessionId": session_id,
        "qqTarget": target_key,
        "subscribed": True,
        "qqSubscriptions": sorted(s.qq_subscriptions),
    }


@app.post("/api/qq/unsubscribe")
async def api_qq_unsubscribe(data: dict):
    """取消订阅某 QQ 会话的 inbox 更新提醒。

    bot_uin 可选（多账号）：解绑 `<type>:<id>@<bot>` 订阅键；缺省解绑不区分
    bot 的旧键 `<type>:<id>`。
    """
    session_id = (data.get("sessionId") or "").strip()
    target_type = (data.get("target_type") or "").strip().lower()
    target_id = (data.get("target_id") or "").strip()
    bot_uin = (data.get("bot_uin") or "").strip()
    if not session_id or not target_id or target_type not in ("user", "group"):
        return {"error": "sessionId, target_type(user|group) and target_id are required"}
    s = sess.get(session_id)
    if not s:
        return {"error": f"Session {session_id} not found"}
    target_key = f"{target_type}:{target_id}@{bot_uin}" if bot_uin else f"{target_type}:{target_id}"
    s.qq_subscriptions.discard(target_key)
    sess.save(s)
    return {
        "sessionId": session_id,
        "qqTarget": target_key,
        "subscribed": target_key in s.qq_subscriptions,
        "qqSubscriptions": sorted(s.qq_subscriptions),
    }


@app.post("/api/qq/notify")
async def api_qq_notify(data: dict):
    """QQ 插件上报 inbox 更新（由 packages/qq/plugin.py 调用）。

    Body: {"target_type": "user"|"group", "target_id": ..., "nickname": ...,
           "text": ..., "time": ..., "bot_uin"?: <bot QQ 号>}

    找到所有订阅该 QQ 会话的 session，各推送一条 `@@@@by qq` 提醒到其
    queue_pending 并唤醒 worker。bot_uin 可选（多账号来源标注）：同时命中
    不区分 bot 的旧订阅键与 `<type>:<id>@<bot>` 精确订阅键。返回投递数量。
    """
    target_type = (data.get("target_type") or "").strip().lower()
    target_id = (data.get("target_id") or "").strip()
    if not target_id or target_type not in ("user", "group"):
        return {"error": "target_type(user|group) and target_id are required"}
    delivered = await worker.enqueue_qq_reminder(
        target_type,
        target_id,
        nickname=(data.get("nickname") or ""),
        text=(data.get("text") or ""),
        time_str=(data.get("time") or ""),
        bot_uin=(data.get("bot_uin") or ""),
    )
    return {"ok": True, "delivered": delivered}


# 复用 AsyncClient：避免每个代理请求新建连接（Windows 下 async httpx 首次
# 初始化/连接池创建可达数秒，复用后仅首次有开销）。
_qq_plugin_client: httpx.AsyncClient | None = None


def _qq_plugin_client_get() -> httpx.AsyncClient:
    global _qq_plugin_client
    if _qq_plugin_client is None:
        _qq_plugin_client = httpx.AsyncClient(timeout=5)
    return _qq_plugin_client


async def _qq_plugin_get(path: str, params: dict | None = None) -> dict:
    """GET 代理到 QQ 插件（packages/qq/plugin.py，PAN_QQ_API_URL，默认 8080）。

    统一错误形态 {ok:false, error:{code,message}}；连接失败归为 connection_error，
    HTTP 非 2xx 透传插件返回体（插件已按同一形态返回）。
    """
    plugin_url = os.environ.get("PAN_QQ_API_URL", "http://127.0.0.1:8080").rstrip("/")
    try:
        r = await _qq_plugin_client_get().get(f"{plugin_url}{path}", params=params)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            if isinstance(body, dict) and body.get("error"):
                return body
        except ValueError:
            pass
        return {"ok": False, "error": {
            "code": e.response.status_code,
            "message": e.response.text[:300]}}
    except httpx.HTTPError as e:
        return {"ok": False, "error": {
            "code": "connection_error",
            "message": f"{type(e).__name__}: {e}"}}


@app.get("/api/qq/contacts")
async def api_qq_contacts(bot_uin: str = ""):
    """列出最近的 QQ 联系人/群（代理到 QQ 插件 recent_contacts）。

    postbox 弹窗需要可选 QQ 会话列表。QQ 插件（packages/qq/plugin.py）在独立
    端口（默认 8080，PAN_QQ_API_URL 可覆盖）暴露 GET /api/qq/recent_contacts，
    此处代理转发，让前端统一走 Pan Core 同源 /api，避免跨域。bot_uin 可选
    （多账号）：透传给插件，只拉指定 bot 通道的联系人；缺省插件走默认通道。
    """
    params = {"bot_uin": bot_uin} if bot_uin else None
    return await _qq_plugin_get("/api/qq/recent_contacts", params)


@app.get("/api/qq/channels")
async def api_qq_channels():
    """列出已注册的 QQ bot 通道（代理到插件 /api/qq/channels）。

    Postbox 合并模式据此对每个 bot 分别拉联系人；每项 {name, bot_uin, connected}。
    """
    return await _qq_plugin_get("/api/qq/channels")


@app.post("/api/claim")
async def api_claim(data: dict):
    """建立 managed 关系（双向落盘，立项 4.2）。

    Body: {"managerId": <manager session id>, "sessionId": <managed session id>}

    效果：manager.managed += [sessionId]，session.managed_by = managerId。
    约束：session 若已有其他 manager 则拒绝。can_claim_unmanaged 能力限制仅由
    MCP 层（packages/mcp/server.py _check_access）实施；本端点（web/前端途径）
    不检查 pan_access——前端 manage 调整拥有最高权限。
    """
    manager_id = (data.get("managerId") or "").strip()
    session_id = (data.get("sessionId") or "").strip()
    if not manager_id or not session_id:
        return {"ok": False, "error": {
            "code": "missing_params",
            "message": "managerId and sessionId are required"}}
    manager = sess.get(manager_id)
    if not manager:
        return {"ok": False, "error": {
            "code": "manager_not_found",
            "message": f"Manager session {manager_id} not found"}}
    # 不检查 manager.can_claim_unmanaged：pan_access 限制只由 MCP 层实施，
    # web/前端途径拥有最高权限（前端 manage 调整不受限）。
    # claim 已自动 report_subscribe（manager.report_subscriptions 加入 session_id）
    err = sess.claim(manager_id, session_id)
    if err:
        return {"ok": False, "error": {
            "code": "claim_failed",
            "message": err}}
    return {
        "ok": True,
        "managerId": manager_id,
        "sessionId": session_id,
        "managed": list(manager.managed),
        "reportSubscriptions": sorted(manager.report_subscriptions),
    }


@app.post("/api/unclaim")
async def api_unclaim(data: dict):
    """解除 managed 关系（管理方主动解绑，不删除 session）。

    Body: {"managerId": <manager session id>, "sessionId": <managed session id>}

    效果：manager.managed 移除 sessionId，session.managed_by 置空，同时清理
    manager.report_subscriptions 对该 session 的订阅（解除管理即退订报告）。
    """
    manager_id = (data.get("managerId") or "").strip()
    session_id = (data.get("sessionId") or "").strip()
    if not manager_id or not session_id:
        return {"ok": False, "error": {
            "code": "missing_params",
            "message": "managerId and sessionId are required"}}
    err = sess.unclaim(manager_id, session_id)
    if err:
        return {"ok": False, "error": {
            "code": "unclaim_failed",
            "message": err}}
    manager = sess.get(manager_id)
    return {
        "ok": True,
        "managerId": manager_id,
        "sessionId": session_id,
        "managed": list(manager.managed) if manager else [],
    }


@app.post("/api/readonly")
async def api_readonly(data: dict):
    """Set or clear a managed session's persistent readonly state.

    Only the target's current manager may change it; unlike claim this endpoint
    never establishes a managed relationship or accepts a claim capability.
    """
    manager_id = (data.get("managerId") or "").strip()
    session_id = (data.get("sessionId") or "").strip()
    enabled = data.get("readonlySession")
    if not manager_id or not session_id or not isinstance(enabled, bool):
        return {"ok": False, "error": {"code": "missing_params",
                "message": "managerId, sessionId and readonlySession(boolean) are required"}}
    if manager_id == session_id:
        return {"ok": False, "error": {"code": "permission_denied",
                "message": "A session cannot set readonly on itself"}}
    manager = sess.get(manager_id)
    target = sess.get(session_id)
    if not manager:
        return {"ok": False, "error": {"code": "manager_not_found",
                "message": f"Manager session {manager_id} not found"}}
    if not target:
        return {"ok": False, "error": {"code": "session_not_found",
                "message": f"Session {session_id} not found"}}
    if target.managed_by != manager_id or session_id not in manager.managed:
        return {"ok": False, "error": {"code": "permission_denied",
                "message": f"Session {manager_id} does not manage {session_id}"}}
    target.readonly_session = enabled
    sess.save(target)
    await broadcast({"type": "session.updated", "sessionId": session_id})
    return {"ok": True, "managerId": manager_id, "sessionId": session_id,
            "readonlySession": target.readonly_session}


@app.post("/api/kill/{worker_id}")
async def api_kill(worker_id: str):
    """Kill a Worker process. Does NOT delete the Session."""
    err = await worker.kill_worker(worker_id, report_abnormal=True)
    if err:
        return {"error": err}
    return {"workerId": worker_id, "status": "killed"}


@app.get("/api/list")
async def api_list():
    """List running workers."""
    return {
        "workers": [
            {
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "status": w.status,
            "generation": getattr(w, "generation", 0),
            }
            for w in worker.list_live_workers()
        ]
    }


@app.get("/api/cli/status")
async def api_cli_status():
    """Return user-facing availability diagnostics for supported Agent CLIs."""
    adapters = get_cli_diagnostics()
    return {
        "adapters": adapters,
        "available": [entry["name"] for entry in adapters if entry["available"]],
        "hasAvailable": any(entry["available"] for entry in adapters),
    }


def _is_codex_worker(w) -> bool:
    """Match only live workers backed by the Codex adapter."""
    return getattr(getattr(w, "adapter", None), "name", "") == "codex"


_codex_wham_provider = CodexWhamProvider()


def _codex_worker_profile_key(w, default_key: str) -> str:
    """Return a live Worker's profile identity without persisting Worker IDs."""
    value = getattr(w, "codex_profile_key", None)
    if isinstance(value, str) and value:
        return value
    # Hand-built test/compat workers have no profile metadata. Treat each as
    # unknown rather than silently claiming that two account snapshots share a
    # profile.
    worker_id = getattr(w, "worker_id", None)
    return f"unknown-worker:{worker_id or id(w)}"


async def _persist_live_codex_quota(w, store: CodexQuotaStore) -> bool:
    rate_limits = getattr(w, "native_rate_limits", None)
    if not isinstance(rate_limits, dict) or not rate_limits:
        return False
    observed_at = (
        getattr(w, "native_rate_limits_updated_at", None)
        or getattr(w, "native_rate_limits_received_at", None)
    )
    _, updated = await asyncio.to_thread(
        store.update,
        rate_limits,
        observed_at=observed_at,
        received_at=getattr(w, "native_rate_limits_received_at", None) or observed_at,
        source="app-server-push",
    )
    return updated


async def _codex_quota_for_request(
    *,
    session_id: str,
    target_worker=None,
    candidates: list | None = None,
    requested_window: str = "all",
    refresh: bool = True,
) -> dict:
    base_profile = resolve_profile_identity()
    live_worker = target_worker
    live_workers = candidates or []
    selected_profile_key = base_profile.profile_key
    live_snapshot_updated = False
    if live_worker is not None:
        worker_profile_key = getattr(live_worker, "codex_profile_key", None)
        if isinstance(worker_profile_key, str) and worker_profile_key:
            selected_profile_key = worker_profile_key
    elif live_workers:
        groups: dict[str, list] = {}
        for candidate in live_workers:
            groups.setdefault(_codex_worker_profile_key(candidate, base_profile.profile_key), []).append(candidate)
        if len(groups) > 1:
            return {
                "ok": False,
                "error": {
                    "code": "quota_ambiguous",
                    "message": "More than one Codex profile is available; pass session_id",
                    "candidates": [
                        {"profileKey": key, "sessionIds": [w.session_id for w in values]}
                        for key, values in groups.items()
                    ],
                },
            }
        same_profile = next(iter(groups.values()))
        live_worker = max(
            same_profile,
            key=lambda value: (
                getattr(value, "native_rate_limits_updated_at", None) or "",
                getattr(value, "worker_id", None) or "",
            ),
        )
        worker_profile_key = getattr(live_worker, "codex_profile_key", None)
        if isinstance(worker_profile_key, str) and worker_profile_key:
            selected_profile_key = worker_profile_key
    else:
        cached_profiles = CodexQuotaStore.available_profile_keys()
        if len(cached_profiles) > 1:
            return {
                "ok": False,
                "error": {
                    "code": "quota_ambiguous",
                    "message": "More than one cached Codex profile is available; pass session_id",
                    "candidates": [{"profileKey": key} for key in cached_profiles],
                },
            }

    profile = base_profile if selected_profile_key == base_profile.profile_key else CodexProfile(
        home=base_profile.home,
        account_id=base_profile.account_id,
        chatgpt_account_id=base_profile.chatgpt_account_id,
        profile_key=selected_profile_key,
    )
    store = CodexQuotaStore(profile)
    if live_worker is not None:
        live_snapshot_updated = await _persist_live_codex_quota(live_worker, store)

    record = store.load()
    refresh_result = (
        await _codex_wham_provider.maybe_refresh(store)
        if refresh else None
    )
    if refresh_result is not None and refresh_result.record is not None:
        record = refresh_result.record
    if record is None:
        error = {
            "code": "quota_unavailable",
            "message": "No Codex quota snapshot is available",
            "provider": "codex",
            "sessionId": session_id,
        }
        if refresh_result is not None and refresh_result.error_code:
            error["refresh"] = refresh_result.error_code
        if refresh_result is not None and refresh_result.credential_status:
            error["credentialStatus"] = refresh_result.credential_status
        return {"ok": False, "error": error}

    stale = is_stale(record, _codex_wham_provider.ttl_seconds)
    result = format_codex_quota_record(
        record,
        session_id=session_id or None,
        worker_id=getattr(live_worker, "worker_id", None) if live_worker else None,
        requested_window=requested_window,
        stale=stale,
    )
    result["cacheMode"] = "live" if live_snapshot_updated else "persisted"
    result["profileKey"] = profile.profile_key
    if refresh_result is not None and refresh_result.error_code and refresh_result.error_code != "feature_disabled":
        result["refreshError"] = refresh_result.error_code
        if refresh_result.credential_status:
            result["credentialStatus"] = refresh_result.credential_status
    return result


@app.get("/api/codex/quota")
async def api_codex_quota(session_id: str = "", window: str = "all", refresh: bool = True):
    """Query global Codex account windows, preferring live push data.

    ``first`` is the five-hour window and ``secondary`` is the weekly window;
    neither name selects an adapter fallback or a model. Without a session id
    one Codex profile must be selected when multiple profiles are live. The
    quota belongs to that provider profile rather than to a Pan Session.

    This is a loopback HTTP trusted-admin interface.  It has no manager
    identity authentication and does not apply managed-session isolation;
    callers must not invent or pass a manager parameter.  Managed isolation
    is enforced by the MCP caller layer before it calls this endpoint.

    The response may be a live app-server push, a persisted last-good value, or
    an optional WHAM refresh. ``refresh=false`` is the cache-only form used by
    the Session usage endpoint and never waits for WHAM. ``observedAt``/
    ``receivedAt`` are local Pan times; the provider's original update time is
    not fabricated or exposed.
    """
    if not validate_quota_window(window):
        return {"ok": False, "error": {
            "code": "invalid_window",
            "message": "window must be one of 'all', 'first', or 'secondary'",
        }}

    if session_id:
        target = sess.get(session_id)
        if target is None:
            return {"ok": False, "error": {
                "code": "session_not_found",
                "message": f"Session {session_id} not found",
            }}
        if target.adapter != "codex":
            return {"ok": False, "error": {
                "code": "unsupported_provider",
                "message": f"Session {session_id} uses adapter {target.adapter!r}; Codex quota requires adapter 'codex'",
            }}
        selected = worker.find_alive_worker_by_session(session_id)
        return await _codex_quota_for_request(
            session_id=session_id,
            target_worker=selected,
            requested_window=window,
            refresh=refresh,
        )
    else:
        candidates = [w for w in worker.list_live_workers() if _is_codex_worker(w)]
    return await _codex_quota_for_request(
        session_id="",
        candidates=candidates,
        requested_window=window,
        refresh=refresh,
    )


# ── cbc Session Import ──

@app.get("/api/cbc/projects")
async def api_cbc_projects():
    config = load_config()
    ci = config.get("cbc_import", {})
    recent_days = ci.get("import_recent_days", 30)
    min_resume_bytes = ci.get("min_resume_bytes", 200)
    projects = cbc_sessions.list_cbc_projects(
        recent_days=recent_days, min_resume_bytes=min_resume_bytes
    )
    return {"projects": projects}




@app.get("/api/cbc/sessions")
async def api_cbc_sessions(project_dir: str = "", cwd: str = "", all: int = 0):
    """List external cbc sessions available for import."""
    config = load_config()
    filter_cfg = config.get("cbc_import", {})

    if project_dir:
        all_sessions = cbc_sessions.list_cbc_sessions(project_dir=project_dir)
    else:
        cwd = cwd or str(Path.cwd())
        all_sessions = cbc_sessions.list_cbc_sessions(cwd)

    if all:
        return {"sessions": all_sessions, "total": len(all_sessions)}

    exclude_patterns = filter_cfg.get("exclude_workdir_patterns", [])
    target_dir = None
    if filter_cfg.get("project_dir_exact_match", False):
        target_dir = sanitize_project_dir_name(cwd)

    filtered: list[dict] = []
    for s in all_sessions:
        if target_dir and s["project_dir"].lower() != target_dir:
            continue
        if not target_dir and any(p in s["project_dir"] for p in exclude_patterns):
            continue
        if s["message_count"] < filter_cfg.get("min_message_count", 5):
            continue
        filtered.append(s)

    filtered.sort(key=lambda x: x.get("last_timestamp", ""), reverse=True)

    max_shown = filter_cfg.get("max_sessions_shown", 30)
    total = len(filtered)
    filtered = filtered[:max_shown]

    return {
        "sessions": filtered,
        "total": total,
        "shown": len(filtered),
    }


@app.get("/api/cbc/browse")
async def api_cbc_browse(path: str = "", limit: int = 30, offset: int = 0, q: str = ""):
    """Browse cbc sessions as a file-tree."""
    result = cbc_sessions.browse_cbc_tree(
        path=path, limit=limit, offset=offset, query=q,
    )
    return result


@app.post("/api/cbc/sessions/import")
async def api_cbc_sessions_import(data: dict):
    """Import a cbc session into Pan (Session only, no worker spawned)."""
    return await _import_session(cbc_sessions, "cbc", data)


# ── Kimi import ──

@app.get("/api/kimi/workspaces")
async def api_kimi_workspaces():
    """List Kimi workspaces that have sessions."""
    from packages.core.adapters.kimi import sessions as kimi_sessions
    return {"workspaces": kimi_sessions.list_kimi_workspaces()}


@app.get("/api/kimi/sessions")
async def api_kimi_sessions(cwd: str = ""):
    """List Kimi sessions for a workspace."""
    from packages.core.adapters.kimi import sessions as kimi_sessions
    return {"sessions": kimi_sessions.list_kimi_sessions_for_cwd(cwd)}


@app.post("/api/kimi/sessions/import")
async def api_kimi_sessions_import(data: dict):
    """Import a Kimi session into Pan (Session only, no worker spawned)."""
    return await _import_session(kimi_sessions, "kimi", data)


# ── OpenCode import ──

@app.get("/api/opencode/sessions")
async def api_opencode_sessions(cwd: str = ""):
    """List OpenCode sessions available for import (read from SQLite DB)."""
    sessions = opencode_sessions.list_opencode_sessions(cwd or None)
    return {"sessions": sessions, "total": len(sessions)}


@app.post("/api/opencode/sessions/import")
async def api_opencode_sessions_import(data: dict):
    """Import an OpenCode session into Pan (Session only, no worker spawned)."""
    return await _import_session(opencode_sessions, "opencode", data)


# ── Generic adapter sessions (P0-2: SessionsProvider 统一分派) ──
# 每新增一个 adapter，只要其在 adapters/__init__.py 注册了 sessions provider
# （模块，提供 SessionsProvider 协议函数），以下通用路由即自动覆盖，server.py
# 无需再写 import/branch/rename 分派。旧 /api/cbc|kimi|opencode/* 端点保留为
# 薄包装（前端仍在用，见 app.ts / api.ts）。


def _sessions_provider(adapter: str):
    """按 adapter 名取 sessions provider；未注册返回 None。"""
    try:
        return get_sessions_provider(adapter)
    except KeyError:
        return None


async def _import_session(provider, adapter: str, data: dict) -> dict:
    """Import an adapter-native session into Pan（Session only，不 spawn worker）。

    三个旧 import 端点（cbc/kimi/opencode）与通用 /api/adapters/{adapter}/sessions/import
    共用此实现。行为差异由 provider 能力位承载：
    - ``session_exists(session_id, cwd)``：可选；存在则启用 import-guard
      （cbc/opencode 提供，kimi 不提供 → 保持旧的无 guard 行为）。
    - ``project_dir_to_path(project_dir)``：可选；cbc 独有，把 cbc 项目目录名
      解析回真实路径（旧 /api/cbc/sessions/import 契约）。
    """
    session_id = data.get("session_id")
    if not session_id:
        return {"error": "session_id is required"}

    cwd = data.get("cwd") or data.get("workdir") or str(Path.cwd())

    # cbc 兼容：project_dir 直接给出且未显式传 cwd 时，解析为真实路径。
    project_dir = data.get("project_dir")
    if project_dir and not data.get("cwd") and not data.get("workdir"):
        resolver = getattr(provider, "project_dir_to_path", None)
        if resolver:
            resolved = resolver(project_dir)
            if resolved:
                cwd = resolved

    try:
        history = provider.parse_history(session_id, cwd)
        raw_usage_entries = provider.get_raw_usage(session_id, cwd)
    except Exception as e:
        return {"error": f"Failed to parse session history: {e}"}

    # 防御（#import-guard）：验证 adapter 侧 session 真实存在，防止孤儿/坏 id
    # 污染导入（例如某 Pan session 的 cli_session_id 被错误指向不存在的 session，
    # 直接 import 会匹配到 existing 并清空其 history）。
    exists = getattr(provider, "session_exists", None)
    if exists and not exists(session_id, cwd):
        return {"error": f"{adapter} session {session_id} not found on disk; refusing to import"}

    raw_usage = sess.accumulate_raw_usage(None, raw_usage_entries)
    total_usage = sess.compute_total_usage(raw_usage)

    # 信用验证：比对 raw_usage_entries 总和与 total_usage（调试用途，不阻断导入）
    credit_sum = sum(
        e.get("rawUsage", {}).get("credit", 0) for e in raw_usage_entries
    )
    cli_credit = total_usage.get("credit", 0) if total_usage else 0
    if abs(credit_sum - cli_credit) > 0.01:
        _log(f"[WARN] import credit mismatch ({adapter}): sum={credit_sum:.2f} cli={cli_credit:.2f}")

    # Dedup by cli_session_id（限定同 adapter）
    existing = None
    for s in sess.list_all():
        if s.cli_session_id == session_id and s.adapter == adapter:
            existing = s
            break

    # 防御（#import-guard）：匹配到已有 session 但解析不出任何历史时，拒绝用空
    # 历史覆盖，避免把已有会话数据清空。
    if existing and not history:
        _log(f"[WARN] import {session_id}: history empty for existing session {existing.id}; refusing to overwrite")
        return {"error": f"{adapter} session {session_id} has no parseable history; refusing to overwrite existing session {existing.id}"}

    if existing:
        w = worker.find_alive_worker_by_session(existing.id)
        if w:
            # Worker process is alive — overwrite history atomically.
            # _replaying=True prevents _read_stdout from appending during
            # the race window (CLI writes to external store before stdout),
            # which would otherwise duplicate agent-side messages.
            w._replaying = True
            try:
                sess.replace_history(existing, history)
                existing.raw_usage = raw_usage
                existing.total_usage = total_usage
                # history 整体替换 → 全量重写 jsonl（增量 append 会把新历史
                # 头部误判为已落盘而跳过）
                sess.save_full(existing)
                await broadcast({
                    "type": "session.updated",
                    "sessionId": existing.id,
                })
            finally:
                w._replaying = False
            return {**_session_to_api(existing), "reimported": True}
        w = worker.find_worker_by_session(existing.id)
        if w:
            await worker.kill_worker(w.worker_id)
        sess.replace_history(existing, history)
        existing.raw_usage = raw_usage
        existing.total_usage = total_usage
        existing.last_result = None
        sess.save_full(existing)
        await broadcast({
            "type": "session.updated",
            "sessionId": existing.id,
        })
        return {**_session_to_api(existing), "reimported": True}

    name = (
        data.get("name", "")
        or provider.get_session_title(session_id, cwd)
        or f"{adapter}-{session_id[:8]}"
    )

    # 模板/能力字段：复用 _build_session_params 模板解析（显式 > 模板 > 默认），
    # 使导入的会话能带 model / permission_mode / MCP / pan_access。
    # workdir 刻意保留外部项目路径（cwd），不落 data/workdirs/<name>。
    try:
        params = _build_session_params(
            {
                "adapter": adapter,
                "name": name,
                "sessionTemplate": data.get("sessionTemplate"),
                **({"panAccess": data["panAccess"]} if "panAccess" in data else {}),
                **({key: data[key] for key in (
                    "modelContextWindow", "modelAutoCompactTokenLimit",
                    "model_context_window", "model_auto_compact_token_limit",
                ) if key in data}),
                **{key: data[key] for key in ("originalPrompt", "handoffPrompt", "systemPrompt")
                   if key in data},
            },
            resolve_workdir=False,
            strict_mcp=False,
        )
    except ValueError as e:
        return {"error": f"Failed to apply session template: {e}"}

    # model 兜底：模板未显式指定时回填原生存储里实际用过的模型
    # （opencode 旧端点行为；cbc/kimi 顺带受益，无害）。
    model = params.get("model") or (
        raw_usage_entries[0].get("model") if raw_usage_entries else None
    )

    s = sess.create(
        name=name,
        adapter=adapter,
        cli_session_id=session_id,
        history=history,
        raw_usage=raw_usage,
        total_usage=total_usage,
        workdir=cwd,
        model=model,
        permission_mode=params.get("permission_mode"),
        session_template=params.get("session_template"),
        original_prompt=params.get("original_prompt"),
        handoff_prompt=params.get("handoff_prompt"),
        pan_access=params.get("pan_access"),
        adapter_config=params.get("adapter_config"),
    )

    await broadcast({
        "type": "session.created",
        "sessionId": s.id,
        "name": s.name,
    })

    return _session_to_api(s)


@app.get("/api/adapters/{adapter}/sessions")
async def api_adapter_sessions(adapter: str, cwd: str = ""):
    """List adapter-native sessions available for import (generic)."""
    provider = _sessions_provider(adapter)
    if provider is None:
        return {"error": f"Unknown adapter: {adapter}"}
    # An empty cwd means all native sessions.  Providers can still apply
    # their own safe default/filtering when a caller supplies a directory.
    sessions = provider.list_sessions(cwd or None)
    return {"sessions": sessions, "total": len(sessions)}


@app.post("/api/adapters/{adapter}/sessions/import")
async def api_adapter_sessions_import(adapter: str, data: dict):
    """Import an adapter-native session into Pan (generic)."""
    provider = _sessions_provider(adapter)
    if provider is None:
        return {"error": f"Unknown adapter: {adapter}"}
    return await _import_session(provider, adapter, data)


# ── Worker actions ──

@app.post("/api/worker/{worker_id}/restart")
async def api_restart(worker_id: str):
    err = await worker.restart_worker(worker_id)
    if err:
        return {"error": err}
    return {"workerId": worker_id, "status": "restarted"}


@app.post("/api/sessions/{session_id}/worker/restart")
async def api_restart_or_start(session_id: str):
    """Restart the session's live worker, or start it when it is gone."""
    s = sess.get(session_id)
    if not s:
        return {"error": f"Session {session_id} not found"}
    result = await worker.restart_or_start_worker(session_id)
    if isinstance(result, str):
        return {"error": result}
    return {
        "workerId": result.worker_id,
        "sessionId": session_id,
        "status": result.status,
    }


@app.post("/api/sessions/{session_id}/worker/kill")
async def api_session_kill(session_id: str):
    """Kill the session's live worker; workerId is response detail only."""
    result = await worker.kill_session_worker(session_id, report_abnormal=True)
    if isinstance(result, str):
        return {"error": result}
    if result is None:
        return {"workerId": None, "sessionId": session_id, "status": "offline"}
    return {"workerId": result.worker_id, "sessionId": session_id, "status": "offline"}


@app.post("/api/worker/{worker_id}/settings")
async def api_worker_settings(worker_id: str, data: dict):
    """Apply model/mode/thinking settings to a session and respawn the worker."""
    w = worker.get_worker(worker_id)
    if not w:
        return {"error": "Worker not found"}
    s = sess.get(w.session_id)
    if not s:
        return {"error": "Session not found"}

    try:
        _apply_session_updates(s, data)
    except ValueError as e:
        return {"error": str(e)}
    sess.save(s)

    adapter = get_adapter(s.adapter)
    extra_args: list[str] = []
    # Most native CLIs consume these compatibility overrides directly. Codex
    # uses a persistent wrapper and receives all settings from its Session via
    # --codex-extra-args; passing --model/--permission-mode to the wrapper would
    # make argparse reject the process before it can handle the next message.
    if not getattr(adapter, "settings_via_session", False):
        if "model" in data:
            extra_args.extend(["--model", data["model"]])
        if "permissionMode" in data:
            extra_args.extend(["--permission-mode", data["permissionMode"] or ""])
        extra_args.extend(adapter.effort_args(s))

    err = await worker.respawn_worker(worker_id, extra_args if extra_args else None)
    if err:
        return {"error": err}

    return {
        "workerId": worker_id,
        "sessionId": s.id,
        "model": s.model,
        "permissionMode": s.permission_mode,
        "alwaysThinkingEnabled": s.adapter_config.get("always_thinking_enabled", False),
        "effort": s.adapter_config.get("effort", ""),
        "status": "settings applied",
    }


@app.post("/api/worker/{worker_id}/rename")
async def api_rename(worker_id: str, data: dict):
    new_name = data.get("name")
    if not new_name:
        return {"error": "name is required"}

    err = _check_session_name(new_name)
    if err:
        return {"error": err}

    session_id = None
    w = worker.get_worker(worker_id)
    if w:
        session_id = w.session_id
    else:
        session_id = data.get("sessionId") or worker_id

    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}

    old_name = s.name
    s.name = new_name
    sess.save(s)
    await broadcast({
        "type": "session.renamed",
        "sessionId": s.id,
        "oldName": old_name,
        "newName": new_name,
    })
    return {"sessionId": s.id, "name": new_name, "status": "renamed"}


@app.post("/api/worker/{worker_id}/branch")
async def api_branch(worker_id: str, data: dict):
    w = worker.get_worker(worker_id)
    if not w:
        return {"error": "Worker not found"}

    orig = sess.get(w.session_id)
    if not orig or not orig.cli_session_id:
        return {"error": "Session not ready for branching"}

    name = data.get("name") or f"{orig.name}-branch"
    new_session = sess.create(name, adapter=orig.adapter, model=orig.model,
                              permission_mode=orig.permission_mode,
                              always_thinking_enabled=orig.adapter_config.get("always_thinking_enabled", False),
                              effort=orig.adapter_config.get("effort", ""),
                              max_thinking_tokens=orig.adapter_config.get("max_thinking_tokens"),
                              workdir=orig.workdir)

    result = await worker.branch_worker(worker_id, new_session.id)
    if isinstance(result, str):
        sess.delete(new_session.id)
        return {"error": result}

    return {
        "workerId": result.worker_id,
        "sessionId": new_session.id,
        "name": new_session.name,
        "status": "idle",
        "parentSessionId": w.session_id,
    }


@app.post("/api/worker/{worker_id}/interrupt")
async def api_interrupt(worker_id: str):
    err = await worker.interrupt_worker(worker_id)
    if err:
        return {"error": err}
    return {"workerId": worker_id, "status": "interrupted"}


@app.post("/api/sessions/{session_id}/worker/interrupt")
async def api_session_interrupt(session_id: str):
    """Interrupt the session's live worker; workerId is response detail only."""
    result = await worker.interrupt_session_worker(session_id)
    if isinstance(result, str):
        return {"error": result}
    if result is None:
        return {"workerId": None, "sessionId": session_id, "status": "offline"}
    return {"workerId": result.worker_id, "sessionId": session_id, "status": "interrupted"}


@app.post("/api/worker/{worker_id}/control")
async def api_worker_control(worker_id: str, data: dict):
    """Send an adapter-native out-of-band control to a live worker."""
    control = data.get("control") if isinstance(data.get("control"), dict) else data
    err = await worker.send_control_message(worker_id, control)
    if err:
        return {"error": err}
    return {"workerId": worker_id, "status": "control sent"}


@app.post("/api/sessions/{session_id}/worker/control")
async def api_session_worker_control(session_id: str, data: dict):
    """Send a native control using the session as the stable address."""
    control = data.get("control") if isinstance(data, dict) and isinstance(data.get("control"), dict) else data
    result = await worker.send_session_control(session_id, control)
    if isinstance(result, str):
        return {"error": result}
    if result is None:
        return {"error": "Worker not found"}
    return {"workerId": result.worker_id, "sessionId": session_id, "status": "control sent"}


@app.post("/api/worker/{worker_id}/steer")
async def api_worker_steer(worker_id: str, data: dict):
    """Inject text into a running native turn (Claude stream / Codex)."""
    err = await worker.steer_worker(
        worker_id, data.get("text") if isinstance(data, dict) else ""
    )
    if err:
        return {"error": err}
    return {"workerId": worker_id, "status": "steer sent"}


@app.post("/api/sessions/{session_id}/worker/steer")
async def api_session_worker_steer(session_id: str, data: dict):
    result = await worker.steer_session_worker(
        session_id, data.get("text") if isinstance(data, dict) else "",
    )
    if isinstance(result, str):
        return {"error": result}
    if result is None:
        return {"error": "Worker not found"}
    return {"workerId": result.worker_id, "sessionId": session_id, "status": "steer sent"}


@app.post("/api/worker/{worker_id}/claude-permission")
async def api_claude_permission(worker_id: str, data: dict):
    """Bridge Claude Code's MCP permission-prompt callback to the dashboard."""
    if not isinstance(data, dict):
        return {"behavior": "deny", "message": "Invalid permission request"}
    tool_name = data.get("toolName") or data.get("tool_name") or "unknown"
    tool_input = data.get("input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    return await worker.request_claude_permission(
        worker_id, str(tool_name), tool_input,
    )


@app.post("/api/worker/{worker_id}/takeover")
async def api_takeover(worker_id: str):
    w = worker.get_worker(worker_id)
    if not w:
        return {"error": "Worker not found"}
    s = sess.get(w.session_id)
    if not s:
        return {"error": "Session not found"}
    if not s.cli_session_id:
        return {"error": "Worker has no CLI session yet"}
    if w.status == "held":
        return {"error": "Worker already in takeover mode"}

    adapter_cmd = w.adapter.takeover_command(s)
    if not adapter_cmd:
        return {"error": f"Adapter '{w.adapter.name}' does not support takeover"}

    # Takeover must leave no Pan app-server alive.  Restarting here would spawn
    # a replacement writer immediately before the native TUI resumes the same
    # Codex thread, which fails with "already has an active writer".
    err = await worker.takeover_worker(worker_id)
    if err:
        return {"error": err}

    await broadcast({
        "type": "worker.status",
        "sessionId": w.session_id,
        "workerId": w.worker_id,
        "generation": getattr(w, "generation", 0),
        "status": "held",
    })

    try:
        w.takeover_pid = _open_terminal(
            # 逐参数引号转义：takeover 命令含 --resume <cli_session_id>，裸 join
            # 会把其特殊字符拆成额外参数，导致 takeover 终端 cbc 启动失败。
            subprocess.list2cmdline(adapter_cmd),
            s.workdir or Path.cwd(),
        )
    except FileNotFoundError:
        return {"error": "terminal opener not found"}
    except OSError as e:
        return {"error": str(e)}

    return {
        "workerId": worker_id,
        "sessionId": w.session_id,
        "cliSessionId": s.cli_session_id,
        # list2cmdline 逐参数引号转义（同 /takeover）：system_prompt 含空格时
        # 裸 join 的命令无法直接粘贴执行（takeover 修复）。
        # list2cmdline 逐参数引号转义（同 /takeover）：takeover 命令含 --resume
        # <cli_session_id>，裸 join 的命令无法直接粘贴执行。
        "takeoverCommand": subprocess.list2cmdline(adapter_cmd),
        "takeoverPid": w.takeover_pid,
        "status": "takeover started",
    }


@app.post("/api/sessions/{session_id}/worker/takeover")
async def api_session_takeover(session_id: str):
    """Take over the session's live worker using sessionId as the control key."""
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    w = worker.find_alive_worker_by_session(session_id)
    if not w:
        return {"workerId": None, "sessionId": session_id, "status": "offline"}
    if not s.cli_session_id:
        return {"error": "Worker has no CLI session yet"}
    adapter_cmd = w.adapter.takeover_command(s)
    if not adapter_cmd:
        return {"error": f"Adapter '{w.adapter.name}' does not support takeover"}

    result = await worker.takeover_session_worker(session_id)
    if isinstance(result, str):
        return {"error": result}
    if result is None:
        return {"workerId": None, "sessionId": session_id, "status": "offline"}
    w = result
    await broadcast({
        "type": "worker.status",
        "sessionId": session_id,
        "workerId": w.worker_id,
        "generation": getattr(w, "generation", 0),
        "status": "held",
    })
    try:
        w.takeover_pid = _open_terminal(
            subprocess.list2cmdline(adapter_cmd), s.workdir or Path.cwd(),
        )
    except FileNotFoundError:
        return {"error": "terminal opener not found"}
    except OSError as e:
        return {"error": str(e)}
    return {
        "workerId": w.worker_id,
        "sessionId": session_id,
        "cliSessionId": s.cli_session_id,
        "takeoverCommand": subprocess.list2cmdline(adapter_cmd),
        "takeoverPid": w.takeover_pid,
        "status": "takeover started",
    }


@app.get("/api/worker/{worker_id}/takeover-command")
async def api_takeover_command(worker_id: str):
    """Return the takeover command without executing it (mobile use).

    Unlike POST /takeover, this does NOT restart the worker or open a
    terminal.  The caller (mobile dashboard) copies the command to
    clipboard so the user can paste it manually.
    """
    w = worker.get_worker(worker_id)
    if not w:
        return {"error": "Worker not found"}
    s = sess.get(w.session_id)
    if not s:
        return {"error": "Session not found"}
    if not s.cli_session_id:
        return {"error": "Worker has no CLI session yet"}

    adapter_cmd = w.adapter.takeover_command(s)
    if not adapter_cmd:
        return {"error": f"Adapter '{w.adapter.name}' does not support takeover"}

    return {
        "workerId": worker_id,
        "sessionId": w.session_id,
        "cliSessionId": s.cli_session_id,
        # list2cmdline 逐参数引号转义（同 /takeover）：takeover 命令含 --resume
        # <cli_session_id>，裸 join 的命令无法直接粘贴执行。
        "takeoverCommand": subprocess.list2cmdline(adapter_cmd),
    }


@app.get("/api/sessions/{session_id}/worker/takeover-command")
async def api_session_takeover_command(session_id: str):
    """Return a takeover command by session, without changing runtime state."""
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    w = worker.find_alive_worker_by_session(session_id)
    if not w:
        return {"workerId": None, "sessionId": session_id, "status": "offline"}
    if not s.cli_session_id:
        return {"error": "Worker has no CLI session yet"}
    adapter_cmd = w.adapter.takeover_command(s)
    if not adapter_cmd:
        return {"error": f"Adapter '{w.adapter.name}' does not support takeover"}
    return {
        "workerId": w.worker_id,
        "sessionId": session_id,
        "cliSessionId": s.cli_session_id,
        "takeoverCommand": subprocess.list2cmdline(adapter_cmd),
    }


# ── Memory API ──

_memory_managers: dict[str, object] = {}  # character_id → MemoryManager

# character_id must be exactly "char_" + 16 lowercase hex (or "default")
_CHARACTER_ID_RE = re.compile(r"^(?:char_[0-9a-f]{16}|default)$")


def _validate_character_id(character_id: str) -> bool:
    """Return True if character_id is safe to embed in a filesystem path."""
    return bool(_CHARACTER_ID_RE.match(character_id))


def _get_memory_manager(character_id: str):
    """Get or create a MemoryManager for a character.

    Rejects unsafe character_ids (path traversal). Memory is indexed with the
    same sentence-transformers provider used by the worker injection path
    (packages/core/memory_context.py) to avoid embedding dims mismatch.
    """
    from packages.core.memory import MemoryManager, PROVIDER_SENTENCE_TRANSFORMERS

    if not _validate_character_id(character_id):
        raise ValueError(
            f"Invalid character_id: {character_id!r}. "
            "Expected 'char_<16 hex>' or 'default'."
        )

    if character_id not in _memory_managers:
        api_key = os.environ.get("OPENAI_API_KEY")
        db_path = str(DATA_DIR / "memory" / f"{character_id}.sqlite")
        db_path_parent = Path(db_path).parent
        db_path_parent.mkdir(parents=True, exist_ok=True)
        _memory_managers[character_id] = MemoryManager(
            db_path=db_path,
            api_key=api_key,
            provider=PROVIDER_SENTENCE_TRANSFORMERS,
        )
    return _memory_managers[character_id]


# ── Character API ──


def _ensure_manifest_fresh() -> None:
    """Hot-reload the manifest if any manifest file changed on disk.

    Cheap on the hot path: only ``stat``s files (no read / parse). A full
    re-read + re-parse + atomic config swap happens *only* when the newest
    mtime advanced or the set of manifest files changed. Any failure is
    swallowed (and already logged inside the manager) so a request never 500s
    on a broken manifest.
    """
    if _character_manager is None:
        return
    try:
        if _character_manager.manifest_changed():
            _character_manager.reload_manifest()
    except Exception:
        _log("[Pan] Manifest hot-reload check failed (non-fatal)")


@app.get("/api/session-templates")
async def api_session_templates():
    """List available session templates from manifest.

    Semantically this endpoint lists session_templates (the former "profiles"),
    which now describe session config rather than characters.
    """
    if _character_manager is None:
        return {"error": "Character manager not initialized"}
    _ensure_manifest_fresh()
    templates = _character_manager.list_session_templates()
    return {
        "sessionTemplates": [
            {
                "name": t.name,
                "adapter": t.adapter,
                "model": t.model,
                "mcpServers": list(t.mcp_servers or []),
                "sourceManifest": t.source_manifest,
                "sourceManifestLabel": t.source_manifest_label,
                "system_prompt_preview": t.system_prompt[:100] + "..." if len(t.system_prompt) > 100 else t.system_prompt,
                "panAccess": {
                    "restrictToManaged": t.restrict_to_managed,
                    "canClaimUnmanaged": t.can_claim_unmanaged,
                    "autoClaimCreated": t.auto_claim_created,
                },
            }
            for t in templates
        ],
        "total": len(templates),
    }


@app.get("/api/mcp/servers")
async def api_mcp_servers():
    """List all available MCP servers from the manifest.

    Returns the full catalog of MCP servers declared across plugin manifests
    (deduplicated/merged). Used by the Manage modal's "MCP Server" section to
    let the user multi-select which servers a session enables.

    Does NOT expose `env` (may contain secrets). If the manifest is not loaded
    yet, returns an empty list with `loaded: false` instead of a 500.
    """
    if _character_manager is None or _character_manager._manifest_config is None:
        return {"servers": [], "loaded": False}
    _ensure_manifest_fresh()
    servers = [
        {
            "name": srv.name,
            "command": srv.command,
            "cwd": srv.cwd,
            "url": srv.url,
            "transport": srv.transport,
            "type": srv.type,
        }
        for srv in _character_manager._manifest_config.mcp_servers
    ]
    return {"servers": servers, "loaded": True}


@app.get("/api/characters/profiles")
async def api_characters_profiles():
    """Deprecated alias for GET /api/session-templates (backward compat).

    Old callers hitting the historical ``/api/characters/profiles`` path get
    the same response; new code should use ``/api/session-templates``.
    """
    return await api_session_templates()


@app.post("/api/manifest/reload")
async def api_manifest_reload():
    """Force a manifest hot-reload.

    Idempotent: calling it repeatedly just reloads the same files and returns
    the same counts. Returns the number of loaded templates/servers/routes so
    the caller can confirm the new state. On failure it returns the last good
    counts and ``reloaded: false`` (the previous config is kept).
    """
    if _character_manager is None:
        return {"error": "Character manager not initialized", "reloaded": False}
    config = _character_manager.reload_manifest()
    if config is None:
        return {
            "reloaded": False,
            "sessionTemplates": 0,
            "mcpServers": 0,
            "characters": 0,
            "commandRoutes": 0,
        }
    return {
        "reloaded": True,
        "sessionTemplates": len(config.session_templates),
        "mcpServers": len(config.mcp_servers),
        "characters": len(config.character_templates),
        "commandRoutes": len(config.command_routes),
    }


@app.get("/api/manifest/command-routes")
async def api_manifest_command_routes():
    """List QQ Bot command routes from loaded manifests.

    Used by the QQ Bot plugin (separate NoneBot2 process) to do prefix
    matching: a message starting with one of ``prefixes`` is forwarded
    directly to ``target`` (HTTP POST ``{"text": "..."}``) without going
    through the LLM path. The plugin should sort by prefix length descending
    so ``.rca`` wins over ``.rc``.
    """
    if _character_manager is None:
        return {"error": "Character manager not initialized"}
    _ensure_manifest_fresh()
    routes = _character_manager.list_command_routes()
    return {
        "routes": [
            {"prefixes": list(r.prefixes), "target": r.target}
            for r in routes
        ],
        "total": len(routes),
    }


@app.post("/api/characters")
async def api_characters_create(data: dict):
    """Create a new character from a manifest character_template."""
    if _character_manager is None:
        return {"error": "Character manager not initialized"}
    _ensure_manifest_fresh()
    template_name = data.get("template_name", "")
    if not template_name:
        return {"error": "template_name is required"}
    try:
        char = _character_manager.create_character(
            template_name=template_name,
            name=data.get("name"),
            auto_index=True,
        )
    except ValueError as e:
        return {"error": str(e)}
    return {
        "id": char.id,
        "name": char.name,
        "memory_db_path": char.memory_db_path,
        "memory_dir": char.memory_dir,
        "created_at": char.created_at,
    }


@app.get("/api/characters")
async def api_characters_list():
    """List all created characters."""
    if _character_manager is None:
        return {"error": "Character manager not initialized"}
    chars = _character_manager.list_characters()
    return {
        "characters": [
            {"id": c.id, "name": c.name}
            for c in chars
        ],
        "total": len(chars),
    }


@app.get("/api/characters/{character_id}")
async def api_characters_get(character_id: str):
    """Get character details including memory stats."""
    if _character_manager is None:
        return {"error": "Character manager not initialized"}
    if not _validate_character_id(character_id):
        return {"error": f"Invalid character_id: {character_id!r}"}
    char = _character_manager.get_character(character_id)
    if char is None:
        return {"error": f"Character {character_id} not found"}
    mem_stats = None
    try:
        mgr = _character_manager.get_memory_manager(character_id)
        if mgr:
            mem_stats = mgr.stats()
    except Exception:
        pass
    return {
        "id": char.id,
        "name": char.name,
        "memory_db_path": char.memory_db_path,
        "memory_dir": char.memory_dir,
        "memory_stats": {"files": mem_stats.files, "chunks": mem_stats.chunks} if mem_stats else None,
        "created_at": char.created_at,
    }


@app.delete("/api/characters/{character_id}")
async def api_characters_delete(character_id: str):
    """Delete a character and its memory store."""
    if _character_manager is None:
        return {"error": "Character manager not initialized"}
    if not _validate_character_id(character_id):
        return {"error": f"Invalid character_id: {character_id!r}"}
    if _character_manager.delete_character(character_id):
        # Drop any cached MemoryManager in this module too — the underlying
        # .sqlite has been unlinked and the fd is stale (#33).
        mgr = _memory_managers.pop(character_id, None)
        if mgr is not None:
            try:
                mgr.close()
            except Exception:
                pass
        return {"status": "deleted", "character_id": character_id}
    return {"error": f"Character {character_id} not found"}


@app.post("/api/memory/index")
async def api_memory_index(data: dict):
    """Index a directory of .md files into the memory store.
    
    Request body:
        character_id: str — which character's memory store
        dir_path: str     — directory containing .md files to index
    """
    character_id = data.get("character_id", "default")
    dir_path = data.get("dir_path")
    if not dir_path:
        return {"error": "dir_path is required"}

    try:
        mgr = _get_memory_manager(character_id)
    except ValueError as e:
        return {"error": str(e)}

    # Restrict dir_path to the project root to prevent arbitrary-directory
    # indexing (security). Mirrors _resolve_fs_path's containment check.
    try:
        target = Path(dir_path).resolve()
        target.relative_to(_PROJECT_DIR)
    except ValueError:
        return {
            "error": f"dir_path {dir_path!r} is outside allowed roots "
            f"({_PROJECT_DIR})"
        }

    report = mgr.index_directory(str(target))
    return {
        "character_id": character_id,
        "files_scanned": report.files_scanned,
        "files_modified": report.files_modified,
        "chunks_upserted": report.chunks_upserted,
        "details": [
            {"path": d.path, "status": d.status, "chunks": d.chunks}
            for d in report.details
        ],
    }


@app.get("/api/memory/search")
async def api_memory_search(
    q: str = "",
    character_id: str = "default",
    max_results: int = 6,
    min_score: float = 0.35,
):
    """Search the memory store for a character.
    
    Query params:
        q            — search query text
        character_id — which character's memory store
        max_results  — max number of results (default 6)
        min_score    — minimum score threshold (default 0.35)
    """
    if not q.strip():
        return {"results": [], "total": 0}

    try:
        mgr = _get_memory_manager(character_id)
    except ValueError as e:
        return {"error": str(e)}
    results = mgr.search(q, max_results=max_results, min_score=min_score)
    return {
        "results": [
            {
                "chunk_id": r.chunk_id,
                "path": r.path,
                "text": r.text,
                "score": round(r.score, 4),
                "start_line": r.start_line,
                "end_line": r.end_line,
                "source": r.source,
            }
            for r in results
        ],
        "total": len(results),
    }


@app.get("/api/memory/stats")
async def api_memory_stats(character_id: str = "default"):
    """Get memory store statistics for a character."""
    try:
        mgr = _get_memory_manager(character_id)
    except ValueError as e:
        return {"error": str(e)}
    stats = mgr.stats()
    return {
        "character_id": character_id,
        "files": stats.files,
        "chunks": stats.chunks,
    }


@app.post("/api/memory/inject")
async def api_memory_inject(data: dict):
    """Search memory and return formatted context for Worker injection.

    Request body:
        text         — user's query to search memory AND the task text
        character_id — which character's memory to search (default: "default")
        max_results  — max memory snippets (default: 3)
        min_score    — minimum score threshold (default: 0.35)

    Returns:
        injected_text — the task text with memory context prepended
        context       — raw memory context (Markdown)
        snippet_count — number of memory snippets found
    """
    from packages.core.memory_context import search_and_format, inject_context

    text = data.get("text", "")
    if not text.strip():
        return {"error": "text is required"}

    character_id = data.get("character_id", "default")
    max_results = data.get("max_results", 3)
    min_score = float(data.get("min_score", 0.35))

    if not _validate_character_id(character_id):
        return {"error": f"Invalid character_id: {character_id!r}"}

    # Search memory using the task text as query
    context = search_and_format(
        query=text,
        character_id=character_id,
        max_results=max_results,
        min_score=min_score,
        db_dir=str(DATA_DIR / "memory"),
    )

    if context.snippet_count > 0:
        injected = inject_context(text, context)
    else:
        injected = text

    return {
        "injected_text": injected,
        "context": context.results_md,
        "snippet_count": context.snippet_count,
        "character_id": character_id,
    }


# ── File-system operations ──

@app.get("/api/fs/list")
async def api_fs_list(session_id: str, path: str = "", include_hidden: bool = False):
    """List files/dirs under a path within the session's workdir."""
    try:
        target = _resolve_fs_path(session_id, path)
    except ValueError as e:
        return {"error": str(e)}
    if not target.is_dir():
        return {"error": f"Not a directory: {path!r}"}
    try:
        entries = []
        for entry in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if entry.name.startswith("."):
                if not include_hidden:
                    continue
                if entry.name in _HIDDEN_ENTRIES:
                    continue
            stat = entry.stat()
            entries.append({
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": stat.st_size if entry.is_file() else 0,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        return {"entries": entries}
    except PermissionError:
        return {"error": f"Permission denied: {path!r}"}


@app.get("/api/fs/read")
async def api_fs_read(session_id: str, path: str = "", download: bool = False):
    """Read file contents within the session's workdir.

    download=False (default): JSON {content, size}, UTF-8 text, 5 MiB cap
    (editor file-open path). download=True: binary attachment download via
    FileResponse (streamed, no size cap, works for binary files too).
    """
    try:
        target = _resolve_fs_path(session_id, path)
    except ValueError as e:
        return {"error": str(e)}
    if not target.is_file():
        return {"error": f"Not a file: {path!r}"}
    if download:
        # Sanitize: strip header-injection chars (CR/LF/quotes/control) —
        # starlette itself quotes non-ASCII via RFC 5987.
        safe_name = "".join(
            c for c in target.name if ord(c) >= 32 and c not in '"\\'
        ).strip() or "download"
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, filename=safe_name, media_type=media_type)
    if target.stat().st_size > _MAX_FILE_BYTES:
        return {"error": f"File too large (max {_MAX_FILE_BYTES // (1024*1024)} MiB)"}
    try:
        content = target.read_text(encoding="utf-8")
        return {"content": content, "size": len(content)}
    except UnicodeDecodeError:
        return {"error": "Cannot read binary file"}
    except PermissionError:
        return {"error": f"Permission denied: {path!r}"}


@app.post("/api/fs/write")
async def api_fs_write(data: dict):
    """Write content to a file within the session's workdir (atomic write)."""
    session_id = data.get("session_id")
    path = data.get("path", "")
    content = data.get("content", "")
    if not session_id:
        return {"error": "session_id required"}
    if len(content) > _MAX_FILE_BYTES:
        return {"error": f"File too large (max {_MAX_FILE_BYTES // (1024*1024)} MiB)"}
    try:
        target = _resolve_fs_path(session_id, path)
    except ValueError as e:
        return {"error": str(e)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # atomic: write to temp then replace
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(str(tmp), str(target))
        return {"path": path, "size": len(content)}
    except PermissionError:
        return {"error": f"Permission denied: {path!r}"}


@app.post("/api/fs/rename")
async def api_fs_rename(data: dict):
    """Rename a file or directory within the session's workdir."""
    session_id = data.get("session_id")
    frm = data.get("from", "")
    to = data.get("to", "")
    if not session_id:
        return {"error": "session_id required"}
    if not frm:
        return {"error": "from path required"}
    if not to:
        return {"error": "to path required"}
    try:
        src = _resolve_fs_path(session_id, frm)
        dst = _resolve_fs_path(session_id, to)
    except ValueError as e:
        return {"error": str(e)}
    try:
        _rename_no_overwrite(src, dst)
        return {"from": frm, "to": to}
    except PermissionError:
        return {"error": f"Permission denied"}
    except OSError as e:
        return {"error": str(e)}


@app.post("/api/fs/delete")
async def api_fs_delete(data: dict):
    """Delete a file or empty directory within the session's workdir."""
    session_id = data.get("session_id")
    path = data.get("path", "")
    if not session_id:
        return {"error": "session_id required"}
    try:
        target = _resolve_fs_path(session_id, path)
    except ValueError as e:
        return {"error": str(e)}
    if not target.exists():
        return {"error": f"Not found: {path!r}"}
    try:
        if target.is_dir():
            target.rmdir()  # only deletes empty dirs — safety
        else:
            target.unlink()
        return {"path": path, "deleted": True}
    except OSError as e:
        return {"error": str(e)}


# ── React SPA ──
if REACT_DIST_EXISTS:
    react_name = (
        "react"
        if not app.routes or not any(
            getattr(r, "name", "") == "react" for r in app.routes
        )
        else "react_v2"
    )

    @app.get(f"/{react_name}/", response_class=HTMLResponse)
    async def react_index_html():
        """Serve React index.html with no-cache so new builds are picked up on refresh."""
        # The route table is built at import time, but the dist availability
        # flag can change while the process is alive (and is intentionally
        # patchable for startup/error-path checks). Do not let the static
        # mount turn a missing build into a misleading 200 response.
        if not REACT_DIST_EXISTS:
            return _react_unavailable_response()
        return HTMLResponse(
            content=(REACT_DIST_DIR / "index.html").read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache"},
        )

    app.mount(
        f"/{react_name}",
        StaticFiles(directory=str(REACT_DIST_DIR), html=True),
        name=react_name,
    )

    @app.get(f"/{react_name}/{{full_path:path}}")
    async def react_spa_fallback(full_path: str):
        """SPA fallback: return index.html for any /react/* path not matching a file."""
        file_path = REACT_DIST_DIR / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(
            REACT_DIST_DIR / "index.html",
            headers={"Cache-Control": "no-cache"},
        )
else:

    @app.get("/react/", response_class=HTMLResponse)
    async def react_unavailable():
        """Return an actionable error when the React build is missing."""
        return _react_unavailable_response()


# ── Static files ──
STATIC_DIR = _WEB_DIR / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
