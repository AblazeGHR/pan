"""Retained legacy registry reads and fenced resource/history adapters.

R6-A removed unmounted creation, message/scheduler dispatch and recovery
leaves. Historical JSON, logs, Run records and original identities remain
readable; shared locks, ownership, cancellation and manual-delete policy stay
available to their existing hosts. Canonical runtimes own new admissions.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import math
import os
import re
import secrets
import stat
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from packages.core import session as _sessions
from packages.core import worker as _worker
from packages.core import config as _config
from packages.core import background_retry as _retry
from packages.jobs import cron as _job_cron
from packages.jobs.final_runtime.coordinator import REGISTRY_LOCK as _lock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = PROJECT_ROOT / "data" / "background_jobs"
COMPLETED_RETENTION_SCAN_INTERVAL_SEC = 24 * 60 * 60
JOB_DELETE_PROCESS_TIMEOUT_SEC = 2.0
_completed_retention_hooks: dict[str, Any] = {"on_deleted": None}

# The registry is shared by the generic background-process Jobs and the small
# number of service lifecycle Jobs.  Keep the latter deliberately data-only:
# they do not have a target Session and never participate in queue_pending.
BACKGROUND_PROCESS_KIND = "background-process"
SESSION_MESSAGE_KIND = "session-message"
SESSION_BROADCAST_KIND = "session-broadcast"
SERVICE_LIFECYCLE_KIND = "main-lifecycle"
SCHEDULED_TASK_KIND = "scheduled-task"
RESUME_LEGAL_RUNNING_ACTION = "resume_legal_running"
RESUME_LEGAL_RUNNING_TEXT = "继续"
SERVICE_ACTIVE_PHASES = frozenset({
    "requested", "stopping", "stopping_workers", "stopping_service",
    "stopped", "starting",
})
SERVICE_TERMINAL_PHASES = frozenset({"ready", "offline", "failed", "timed_out"})


class _RunnerSpawnFailed(OSError):
    """Process creation failed and its terminal fact was committed."""


def _legacy_write(fn):
    from packages.jobs.runtime_owner import legacy_writer
    signature = inspect.signature(fn)
    def profile(*args, **kwargs):
        values = signature.bind_partial(*args, **kwargs).arguments
        root = values.get("registry_root") or os.environ.get("PAN_BACKGROUND_JOBS_DIR") or DEFAULT_ROOT
        if fn.__name__ == "_write_job":
            root = values["path"].parent.parent
        return Path(root).resolve().parent
    # Reserve before acquiring a store/job lock. Only the final file commit
    # takes the authority transaction, so lock order cannot invert with RMW.
    return legacy_writer(profile, reservation=fn.__name__ != "_write_job")(fn)


def _root(registry_root: str | Path | None = None) -> Path:
    value = registry_root or os.environ.get("PAN_BACKGROUND_JOBS_DIR")
    root = Path(value).expanduser() if value else DEFAULT_ROOT
    (root / "jobs").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    return root


def _job_path(job_id: str, registry_root: str | Path | None = None) -> Path:
    if not job_id or Path(job_id).name != job_id or not job_id.startswith("job_"):
        raise ValueError("invalid job id")
    return _root(registry_root) / "jobs" / f"{job_id}.json"


def _atomic_write(path: Path, value: dict) -> None:
    """Compatibility import; canonical runtime owns atomic storage."""
    from packages.jobs.final_runtime.coordinator import atomic_write
    atomic_write(path, value)


@_legacy_write
def _write_job(path: Path, job: dict, *, created: bool = False) -> None:
    """Publish a full public snapshot only after the registry commit succeeds.

    Message/scheduler state machines and agent-facing APIs share this boundary.
    Detached runners have no event callback; their terminal recovery remains
    responsible for broadcasting after Pan observes the durable result.
    """
    from packages.jobs.system_actions import sync
    _retry.terminal(job, time.time())
    sync(job, created)
    _atomic_write(path, job)
    if (not job.get("scheduledParentJobId")
            and not (job.get("kind") == BACKGROUND_PROCESS_KIND
                     and job.get("status") in {"completed", "failed", "cancelled"})):
        # Process terminal delivery retains the strict retry/ack recovery path.
        # Freeze nested schedule/target fields before an async broadcaster runs.
        snapshot = json.loads(json.dumps(job_public_view(job)))
        _scheduled_task_emit({"type": "job.created" if created else "job.updated",
                              "jobId": job["jobId"], "job": snapshot})


@contextmanager
def _windows_named_mutex(lock_path: Path, namespace: str):
    """Acquire a named Windows mutex with correctly typed Win32 handles."""
    import ctypes
    from ctypes import wintypes

    digest = hashlib.sha256(str(lock_path.resolve()).encode("utf-8")).hexdigest()
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

    mutex = kernel32.CreateMutexW(
        None, False, f"{namespace}{digest}")
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    wait = kernel32.WaitForSingleObject(mutex, 0xFFFFFFFF)
    if wait not in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
        error = ctypes.get_last_error()
        kernel32.CloseHandle(mutex)
        if wait == 0xFFFFFFFF:  # WAIT_FAILED
            raise ctypes.WinError(error)
        raise OSError(f"WaitForSingleObject failed: {wait}")
    try:
        yield
    finally:
        try:
            if not kernel32.ReleaseMutex(mutex):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            if not kernel32.CloseHandle(mutex):
                raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def _job_lock(job_id: str, registry_root: str | Path | None = None):
    from packages.jobs.final_runtime.coordinator import registry_lock
    with registry_lock(_root(registry_root), "job:" + job_id):
        yield


@contextmanager
def _registry_lock(name: str, registry_root: str | Path | None = None):
    from packages.jobs.final_runtime.coordinator import registry_lock
    with registry_lock(_root(registry_root), name):
        yield


def _load_path(path: Path) -> dict | None:
    # A concurrent ``_atomic_write`` can transiently deny the read on Windows
    # (the same sharing violation its rename retry exists for). Reporting a
    # live record as missing would fail the caller, so retry the read in the
    # same bounded way before giving up.
    for attempt in range(20):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 19:
                return None
            time.sleep(0.01 * (attempt + 1))
        except (OSError, json.JSONDecodeError):
            return None


def _normalize_job(job: dict | None) -> dict | None:
    """Add read-time defaults without rewriting legacy JSON files."""
    if not job:
        return job
    result = dict(job)
    result.setdefault("kind", BACKGROUND_PROCESS_KIND)
    result.setdefault("operation", "run")
    _retry.defaults(result)
    # name/description 必填规范（所有 kind）：旧记录读路径兜底，不重写文件。
    # 兜底名只用 jobId 派生（无注册表扫描），避免读路径产生 I/O 放大。
    if not str(result.get("name") or "").strip():
        digest = re.sub(r"[^0-9a-f]", "", str(result.get("jobId") or ""))[:6]
        result["name"] = f"job-{int(digest, 16) % 100000 + 1}" if digest else "job"
    result.setdefault("description", "")
    result.setdefault("paused", False)
    # T-046: creator and target are independent identities.  Old records did
    # not persist creatorSessionId, so keep them readable with a null creator.
    if result.get("kind") in {SESSION_MESSAGE_KIND, SESSION_BROADCAST_KIND}:
        result.setdefault("creatorSessionId", result.get("sourceSessionId"))
    else:
        result.setdefault("creatorSessionId", None)
    if result.get("kind") == SERVICE_LIFECYCLE_KIND:
        result.setdefault("options", {})
        # ``errors`` was added after the first lifecycle Job schema.  Keep
        # old JSON readable and expose a legacy scalar error as one item,
        # without rewriting the persisted record just by reading it.
        errors = result.get("errors")
        if not isinstance(errors, list):
            errors = []
        if result.get("error") and result["error"] not in errors:
            errors.append(result["error"])
        result["errors"] = errors
    return result


@_legacy_write
def _save(job: dict, registry_root: str | Path | None = None) -> dict:
    from packages.jobs.final_runtime.coordinator import JsonRegistryStore
    return JsonRegistryStore(_root(registry_root)).publish(job, publisher=_write_job)


@_legacy_write
def _create(job: dict, registry_root: str | Path | None = None) -> dict:
    from packages.jobs.final_runtime.coordinator import JsonRegistryStore
    return JsonRegistryStore(_root(registry_root)).publish(job, create=True, publisher=_write_job)


@_legacy_write
def _update(job_id: str, changes: dict[str, Any], *, replace: dict | None = None,
            registry_root: str | Path | None = None) -> dict:
    from packages.jobs.final_runtime.coordinator import JsonRegistryStore
    return JsonRegistryStore(_root(registry_root)).update(job_id, changes, replace=replace, publisher=_write_job)


def get(job_id: str, registry_root: str | Path | None = None) -> dict | None:
    try:
        local = _normalize_job(_load_path(_job_path(job_id, registry_root)))
    except ValueError:
        return None
    if local is not None or registry_root is not None:
        return local
    from packages.jobs import pan_bridge
    remote = pan_bridge.request("get", job_id)
    return pan_bridge.public_job(remote) if remote is not None else None


# ── name / description 规范（所有 kind 统一；2026-09-25 用户拍板）──
#
# - OS process start requires explicit name (1-200 trimmed characters).
# - Web /api/jobs and schedule/message creation retain optional automatic `job-N` names;
# - 默认名 = **存量最小空缺**序号（删了 job-3 → 新建可复用 job-3）；
#   允许极端条件（并发创建）下重名——name 纯展示，唯一标识是 jobId；
# - name 是普通可编辑字段，无 nameIsAuto 标记位；
# - description 可空字符串，缺省 ""。


_DEFAULT_NAME_RE = re.compile(r"^job-(\d+)$")


def normalize_name(value: Any) -> str | None:
    """显式名字：strip 后非空才收；否则 None（调用方走默认名）。"""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def normalize_description(value: Any) -> str:
    """description：非字符串一律收编为空串。"""
    return value.strip() if isinstance(value, str) else ""


def default_job_name(registry_root: str | Path | None = None) -> str:
    """存量最小空缺序号的默认名：job-1, job-2, ...（跳过被占用的号）。

    无锁扫描一次存活 job；并发创建可能撞号，属已接受的产品语义。
    """
    taken: set[int] = set()
    try:
        for job in list_jobs(registry_root):
            match = _DEFAULT_NAME_RE.match(str(job.get("name") or "").strip())
            if match:
                taken.add(int(match.group(1)))
    except Exception:
        pass
    candidate = 1
    while candidate in taken:
        candidate += 1
    return f"job-{candidate}"


def list_jobs(registry_root: str | Path | None = None) -> list[dict]:
    root = _root(registry_root) / "jobs"
    jobs = [_normalize_job(_load_path(p)) for p in root.glob("job_*.json")]
    jobs = [j for j in jobs if j]
    if registry_root is None:
        from packages.jobs import pan_bridge
        remote = pan_bridge.request("list")
        if remote is not None:
            if not isinstance(remote, dict) or not isinstance(remote.get("jobs"), list):
                raise pan_bridge.JobServiceUnavailable("invalid independent Job service response")
            local_ids = {job["jobId"] for job in jobs}
            for item in remote["jobs"]:
                job = pan_bridge.public_job(item)
                if job["jobId"] not in local_ids:
                    jobs.append(job)
    return sorted(jobs, key=_job_creation_sort_key, reverse=True)


def _job_creation_sort_key(job: dict) -> tuple[float, str]:
    """Legacy/malformed timestamps must not take down the whole Jobs GUI.

    Use a numeric read-time key; preserve the original record on disk and in
    its public view. Missing, bool, nonfinite and invalid values sort oldest.
    """
    value = job.get("createdAt")
    timestamp = 0.0
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            timestamp = float(value)
        elif isinstance(value, str):
            try:
                timestamp = float(value)
            except ValueError:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                timestamp = parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        timestamp = 0.0
    if not math.isfinite(timestamp):
        timestamp = 0.0
    return timestamp, str(job.get("jobId") or "")


# ── 结构化 source / target（PLAN §1/§3；2026-09-25 定形）──
#
# 落盘形状（新记录）：sourceStruct: {type, sessionId?, pluginName?}、
# targetStruct: {sessionId}。旧记录的扁平字段（source 字符串 + sourceSessionId +
# targetSessionId [+ targetSessionIds]）在**读路径出口**折算成结构化形状展示，
# 不重写旧文件；写入时双写（struct + 扁平），旧读者不破。

SOURCE_TYPES_STRUCT = ("agent", "user", "system", "plugin")


def normalize_source(value: Any) -> dict:
    """输入 → 结构化 source。接受结构化 dict 或旧扁平字符串。"""
    if isinstance(value, dict):
        stype = str(value.get("type") or "").strip().lower()
        if stype not in SOURCE_TYPES_STRUCT:
            stype = "system"
        out: dict[str, Any] = {"type": stype}
        sid = value.get("sessionId")
        if isinstance(sid, str) and sid.strip():
            out["sessionId"] = sid.strip()
        pname = value.get("pluginName")
        if stype == "plugin" and isinstance(pname, str) and pname.strip():
            out["pluginName"] = pname.strip()
        return out
    # 旧扁平形态：source 字符串（agent/user/automation/...）
    text = str(value or "").strip().lower() if isinstance(value, str) else ""
    if text == "automation":
        text = "system"
    if text not in SOURCE_TYPES_STRUCT:
        text = "system"
    return {"type": text}


def normalize_target(session_id: Any, extra_ids: Any = None) -> dict:
    """target：单目标或扇出列表 → 结构化形状。"""
    ids: list[str] = []
    if isinstance(session_id, str) and session_id.strip():
        ids.append(session_id.strip())
    if isinstance(extra_ids, list):
        for sid in extra_ids:
            if isinstance(sid, str) and sid.strip() and sid.strip() not in ids:
                ids.append(sid.strip())
    return {"sessionId": ids[0] if ids else None,
            **({"sessionIds": ids} if len(ids) > 1 else {})}


def _structured_identity(job: dict) -> dict:
    """读路径：任何年代的 job 记录 → 统一的 {source, target} 结构（不重写文件）。

    规则（PLAN §3 source 四类 × target 正交）：
    - 结构化字段已存在 → 原样返回；
    - 旧扁平字段 → 折算：source 字符串映射四类（automation→system），
      sourceSessionId → source.sessionId，targetSessionId(s) → target。
    - main-lifecycle 无 session 概念 → target.sessionId = None。
    """
    raw_source = job.get("sourceStruct")
    if isinstance(raw_source, dict):
        source = normalize_source(raw_source)
    else:
        source = normalize_source(job.get("source"))
        if source.get("type") == "agent" and job.get("sourceSessionId"):
            source["sessionId"] = job.get("sourceSessionId")
    raw_target = job.get("targetStruct")
    if isinstance(raw_target, dict):
        target = normalize_target(raw_target.get("sessionId"),
                                  raw_target.get("sessionIds"))
    else:
        extra = job.get("targetSessionIds") if isinstance(
            job.get("targetSessionIds"), list) else None
        target = normalize_target(job.get("targetSessionId"), extra)
    return {"source": source, "target": target}


def job_public_view(job: dict | None) -> dict | None:
    """job 记录 → GUI/API 出口视图：扁平字段折算成结构化身份。

    出口契约（camelCase，/api/jobs 与 GUI 共用）：source: {type, sessionId?,
    pluginName?}、target: {sessionId, sessionIds?}；其余键原样透传。
    """
    if not isinstance(job, dict):
        return job
    from packages.jobs.chains import public as chain_public
    view = chain_public(job)
    _retry.defaults(view)
    view.pop('attemptSnapshot', None)
    identity = _structured_identity(job)
    view["source"] = identity["source"]
    view["target"] = identity["target"]
    view.pop("sourceStruct", None)
    view.pop("targetStruct", None)
    if isinstance(view.get('config'), dict) and view.get('action', {}).get('api') == 'action_reference':
        from packages.jobs.unified import project
        view.pop('unified', None)
        view['unified'] = project(view)
    return view


def _job_target_ids(job: dict) -> tuple[list[str], bool]:
    """Return every persisted target reference in display/dispatch order.

    The current Jobs kinds persist targets as a scalar and/or an ordered list,
    with ``targetStruct`` as the structured equivalent.  Combining the
    representations here also makes a partially migrated record safe: a
    target found only in a legacy field is not lost when the structured shape
    is brought back into sync.
    """
    target_struct = job.get("targetStruct")
    flat_ids = job.get("targetSessionIds")
    structured_ids = (target_struct.get("sessionIds")
                      if isinstance(target_struct, dict) else None)
    is_multi = isinstance(flat_ids, list) or isinstance(structured_ids, list)
    values: list[Any] = []
    if isinstance(target_struct, dict):
        values.append(target_struct.get("sessionId"))
        if isinstance(structured_ids, list):
            values.extend(structured_ids)
    values.append(job.get("targetSessionId"))
    if isinstance(flat_ids, list):
        values.extend(flat_ids)

    ids: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            ids.append(value)
    return ids, is_multi


def _retarget_job_record(job: dict, old_session_id: str,
                         new_session_id: str) -> bool:
    """Replace target references and keep structured/legacy views in sync."""
    ids, was_multi = _job_target_ids(job)
    if old_session_id not in ids:
        return False

    updated_ids: list[str] = []
    seen: set[str] = set()
    for session_id in ids:
        replacement = new_session_id if session_id == old_session_id else session_id
        if replacement not in seen:
            seen.add(replacement)
            updated_ids.append(replacement)
    if not updated_ids:
        return False

    # A list-shaped record remains list-shaped even when de-duplication leaves
    # one destination.  Both runtime readers and job_public_view then observe
    # exactly the same ordered set.
    is_multi = was_multi or len(updated_ids) > 1
    job["targetSessionId"] = updated_ids[0]
    if is_multi:
        job["targetSessionIds"] = updated_ids
    elif "targetSessionIds" in job:
        job["targetSessionIds"] = None

    raw_struct = job.get("targetStruct")
    target_struct = dict(raw_struct) if isinstance(raw_struct, dict) else {}
    target_struct["sessionId"] = updated_ids[0]
    if is_multi:
        target_struct["sessionIds"] = updated_ids
    elif "sessionIds" in target_struct:
        target_struct.pop("sessionIds", None)
    job["targetStruct"] = target_struct
    return True


@_legacy_write
def retarget_session_jobs(old_session_id: str, new_session_id: str,
                          registry_root: str | Path | None = None) -> dict:
    """Compatibility adapter; the canonical runtime owns handoff policy."""
    from packages.core import background_jobs as registry
    from packages.jobs.final_runtime.coordinator import retarget_legacy_jobs
    return retarget_legacy_jobs(registry, old_session_id, new_session_id, registry_root)


@_legacy_write
def update_job_field(job_id: str, changes: dict[str, Any],
                     registry_root: str | Path | None = None) -> dict:
    """跨 kind 的通用字段更新（name/description/enabled/paused/target 切换等）。

    - target 切换 = 写 targetStruct + 扁平字段双写（旧读者兼容）；
      scheduled-task 的积压便条由统一循环按新 target 自动重投（PLAN §10）。
    - enabled=False → completed + scheduled-task 的 entry 全停；True → 状态
      交给循环/状态机按 nextFireAt 自行归类。
    全程持跨进程 job 锁。
    """
    with _lock, _job_lock(job_id, registry_root):
        path = _job_path(job_id, registry_root)
        current = _load_path(path)
        if not current:
            raise ValueError("job not found")
        patch = dict(changes)
        if "target" in patch:
            raw = patch["target"]
            target = normalize_target(
                raw.get("sessionId") if isinstance(raw, dict) else raw,
                raw.get("sessionIds") if isinstance(raw, dict) else None)
            patch["targetStruct"] = target
            patch["targetSessionId"] = target.get("sessionId")
            if target.get("sessionIds"):
                patch["targetSessionIds"] = target["sessionIds"]
            elif current.get("kind") != SESSION_BROADCAST_KIND:
                patch["targetSessionIds"] = None
            patch.pop("target", None)
        if patch.get("enabled") is False:
            patch["status"] = "completed"
            if current.get("kind") == SCHEDULED_TASK_KIND:
                patch["schedule"] = [
                    {**entry, "enabled": False, "nextFireAt": None}
                    for entry in (current.get("schedule") or [])
                ]
                patch["nextFireAt"] = None
        elif patch.get("enabled") is True:
            patch.pop("status", None)  # 循环/状态机按 nextFireAt 自行归类
        patch["updatedAt"] = time.time()
        current.update(patch)
        _write_job(path, current)
        return current


def _validate_command(argv: Any, cwd: Any) -> tuple[list[str], Path]:
    if (not isinstance(argv, list) or not argv
            or not all(isinstance(x, str) and x for x in argv)
            or not argv[0].strip()):
        raise ValueError("argv must be a non-empty string array")
    return list(argv), _validate_cwd(cwd)


def _validate_cwd(cwd: Any) -> Path:
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("cwd is required")
    path = Path(cwd).expanduser().resolve()
    # Jobs execute agent work in arbitrary existing directories, including
    # sibling Git worktrees. The command and notification permissions are
    # separate from cwd validation; cwd is not a project-containment boundary.
    if not path.is_dir():
        raise ValueError("cwd does not exist or is not a directory")
    return path


def validate_shell_action(command: Any, cwd: Any) -> tuple[str, Path]:
    """Validate the explicit shell action used by scheduled Jobs."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("shell command must be a non-empty string")
    if len(command) > 16000:
        raise ValueError("shell command must be 16000 characters or fewer")
    return command, _validate_cwd(cwd)


def _runner_command(job_id: str) -> list[str]:
    from packages.core.config import resolve_pan_python_argv

    return [*resolve_pan_python_argv(), "-m", "packages.core.background_runner", "--job-id", job_id]


@_legacy_write
def _spawn_background_runner(job: dict, registry_root: str | Path | None = None) -> dict:
    with _registry_lock("spawn-" + job["jobId"], registry_root):
        current = get(job["jobId"], registry_root)
        if not current or current.get("status") not in {"starting", "running"}:
            raise ValueError("Job no longer admitted for runner spawn")
        if current.get("runnerSpawnStartedAt"):
            return current
        return _spawn_background_runner_unlocked(current, registry_root)


def _spawn_background_runner_unlocked(job: dict, registry_root: str | Path | None = None) -> dict:
    job_id = job["jobId"]
    _update(job_id, {'runnerSpawnStartedAt': time.time()}, registry_root=registry_root)
    log_path = Path(job["logPath"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab")
    kwargs: dict[str, Any] = {
        "cwd": job["cwd"], "stdout": log, "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL, "close_fds": True,
        "env": {
            **os.environ,
            "PYTHONPATH": str(PROJECT_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
            "PAN_BACKGROUND_JOBS_DIR": str(_root(registry_root)),
        },
    }
    if os.name == "nt":
        # A venv redirector starts the real Python with flags=0. DETACHED_PROCESS
        # is not inherited and would let that child allocate a Terminal window.
        # NO_WINDOW creates a windowless console that the redirector child inherits.
        # This runner is always headless; the task's hideWindow policy is separate.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        command = _runner_command(job_id)
        if job.get("attemptId"):
            command += ["--attempt-id", job["attemptId"]]
        proc = subprocess.Popen(command, **kwargs)
    except Exception as exc:
        _update(job_id, {"status": "failed", "error": f"runner spawn failed: {exc}",
                         "notificationState": "pending", "updatedAt": time.time()},
                registry_root=registry_root)
        raise _RunnerSpawnFailed(str(exc)) from exc
    finally:
        log.close()
    with _lock, _job_lock(job_id, registry_root):
        path = _job_path(job_id, registry_root)
        current = _load_path(path)
        if not current:
            raise ValueError("job not found after runner spawn")
        if current.get("attemptId") != job.get("attemptId"):
            return _normalize_job(current)
        current.setdefault("runnerPid", proc.pid)
        current.setdefault("runnerProcessCreatedAt", _process_create_time(proc.pid))
        if current.get("status") == "starting":
            current["status"] = "running"
        current["updatedAt"] = time.time()
        _write_job(path, current)
        return _normalize_job(current)


@_legacy_write
def start_action_process(job_id: str, execution_id: str, *, registry_root=None) -> dict:
    """Attach the native Runner to an already admitted legacy Action Job.

    Admission is persisted before spawn. An interrupted spawn is uncertain and
    is never retried by this function: the same execution cannot spawn twice.
    """
    with _lock, _job_lock(job_id, registry_root):
        current = get(job_id, registry_root)
        if (not current or current.get('legacyCompat') != BACKGROUND_PROCESS_KIND
                or current.get('execution', {}).get('id') != execution_id
                or current.get('status') != 'running'):
            raise ValueError('System Action process admission is no longer current')
        if current.get('processAdmissionId'):
            raise ValueError('Process spawn already admitted; do not replay an unconfirmed spawn')
        admitted_at = time.time()
        current.update(processAdmissionId=execution_id, processAdmittedAt=admitted_at,
                       runnerSpawnStartedAt=admitted_at,
                       logPath=str(_root(registry_root) / 'logs' / (job_id + '.log')),
                       notificationState='pending', pid=None, processCreatedAt=None)
        _write_job(_job_path(job_id, registry_root), current)
    return _spawn_background_runner(current, registry_root)


# ---------------------------------------------------------------------------
# Durable Session-message Jobs
# ---------------------------------------------------------------------------

_CLOCK_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$")
MESSAGE_JOB_TERMINAL = frozenset({"completed", "failed", "cancelled"})
MESSAGE_JOB_ACTIVE = frozenset({"pending", "scheduled", "running"})
MESSAGE_JOB_REQUEUE_AFTER_SEC = 5.0


def _iso_utc(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_at(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("schedule.at must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        result = parsed.timestamp()
    else:
        raise ValueError("schedule.at must be an ISO-8601 timestamp")
    if not math.isfinite(result):
        raise ValueError("schedule.at must be finite")
    return result


def _positive_seconds(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be a positive number")
    return result


def _weekly_timezone(name: Any):
    if name in (None, "", "local"):
        return datetime.now().astimezone().tzinfo
    if name in ("UTC", "Z", "+00:00"):
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(str(name))
    except Exception as exc:
        raise ValueError("schedule.timezone must be local, UTC, or a valid IANA zone") from exc


def _next_weekly(schedule: dict, after: float) -> float:
    weekday = schedule["weekday"]
    clock = schedule["time"]
    parts = [int(part) for part in clock.split(":")]
    hour, minute = parts[:2]
    second = parts[2] if len(parts) == 3 else 0
    tz = _weekly_timezone(schedule.get("timezone"))
    current = datetime.fromtimestamp(after, tz)
    delta = (weekday - current.weekday()) % 7
    candidate = current.replace(hour=hour, minute=minute, second=second, microsecond=0)
    candidate += timedelta(days=delta)
    if candidate.timestamp() <= after:
        candidate += timedelta(days=7)
    return candidate.timestamp()


def _normalize_message_schedule(schedule: Any, now: float) -> tuple[dict, float]:
    if not isinstance(schedule, dict):
        raise ValueError("schedule must be an object")
    kind = schedule.get("type")
    if kind == "once":
        has_at = schedule.get("at") is not None
        has_delay = schedule.get("delaySeconds") is not None
        if has_at == has_delay:
            raise ValueError("once schedule requires exactly one of at or delaySeconds")
        if has_at:
            at = _parse_at(schedule.get("at"))
            if at <= now:
                raise ValueError("schedule.at must be in the future")
            normalized = {"type": "once", "at": _iso_utc(at)}
            return normalized, at
        delay = _positive_seconds(schedule.get("delaySeconds"), "schedule.delaySeconds")
        return {"type": "once", "delaySeconds": delay}, now + delay
    if kind == "interval":
        interval = _positive_seconds(schedule.get("intervalSeconds"), "schedule.intervalSeconds")
        return {"type": "interval", "intervalSeconds": interval}, now + interval
    if kind == "weekly":
        try:
            weekday = int(schedule.get("weekday"))
        except (TypeError, ValueError) as exc:
            raise ValueError("schedule.weekday must be an integer from 0 (Monday) to 6 (Sunday)") from exc
        if weekday not in range(7):
            raise ValueError("schedule.weekday must be an integer from 0 (Monday) to 6 (Sunday)")
        clock = schedule.get("time")
        if not isinstance(clock, str) or not _CLOCK_RE.match(clock):
            raise ValueError("schedule.time must be HH:MM or HH:MM:SS")
        # Validate the timezone now, so a typo cannot create a permanently
        # un-runnable persisted Job.
        _weekly_timezone(schedule.get("timezone"))
        normalized = {"type": "weekly", "weekday": weekday, "time": clock}
        if schedule.get("timezone") not in (None, ""):
            normalized["timezone"] = schedule.get("timezone")
        return normalized, _next_weekly(normalized, now)
    raise ValueError("schedule.type must be once, interval, or weekly")


@_legacy_write
def cancel_message(job_id: str, registry_root: str | Path | None = None) -> dict:
    current = get(job_id, registry_root)
    if not current or current.get("kind") not in {
        SESSION_MESSAGE_KIND, SESSION_BROADCAST_KIND}:
        raise ValueError("message Job not found")
    if current.get("status") in MESSAGE_JOB_TERMINAL:
        return current
    return _update(job_id, {"status": "cancelled", "nextRunAt": None,
                            "cancelRequestedAt": time.time(),
                            "updatedAt": time.time()}, registry_root=registry_root)


# ---------------------------------------------------------------------------
# Retained scheduler read/config/event adapters. The old dispatch host is gone.
# ---------------------------------------------------------------------------

SCHEDULED_TASK_REQUEUE_AFTER_SEC = 5.0
SCHEDULED_TASK_UNDELIVERED_MAX = 20

_scheduled_task_hooks: dict[str, Any] = {
    "root_resolver": None,
    "config_resolver": None,
    "on_event": None,
    "on_event_strict": None,
    "qq_send": None,
}
_scheduled_task_stats: dict[str, Any] = {"dueScanned": 0, "lastTickAt": None}


def register_scheduled_tasks(*, root_resolver=None, config_resolver=None,
                             on_event=None, on_event_strict=None, qq_send=None) -> None:
    """Register the scheduler plugin's registry root / config / event callback.

    幂等：仅覆盖显式给出的可调用项，重复注册安全。on_event_strict 是带成功
    契约的发射（scheduler emit_strict）：返回是否已成功递交广播，供终态补发
    的重试语义使用；未注册时 _scheduled_task_emit_strict 退回 on_event 的
    尽力而为语义。
    """
    if callable(root_resolver):
        _scheduled_task_hooks["root_resolver"] = root_resolver
    if callable(config_resolver):
        _scheduled_task_hooks["config_resolver"] = config_resolver
    if on_event is not None:
        _scheduled_task_hooks["on_event"] = on_event
    if on_event_strict is not None:
        _scheduled_task_hooks["on_event_strict"] = on_event_strict
    if callable(qq_send):
        _scheduled_task_hooks["qq_send"] = qq_send


def scheduled_task_stats() -> dict:
    """Unified-loop health snapshot for the compat status API."""
    return dict(_scheduled_task_stats)


def is_recovery_running() -> bool:
    return False  # R6-A: the legacy execution loop is retired.


@_legacy_write
def delete_job(job_id: str, registry_root: str | Path | None = None) -> bool:
    """Fenced compatibility adapter for canonical deletion/cleanup policy."""
    from packages.core import background_jobs as registry
    from packages.jobs.final_runtime.coordinator import delete_legacy_job
    return delete_legacy_job(registry, job_id, registry_root)


def _terminate_job_processes(jobs: list[dict], *, timeout: float | None = None) -> dict:
    from packages.jobs.process_cleanup import terminate_owned_jobs
    return terminate_owned_jobs(jobs, timeout=(JOB_DELETE_PROCESS_TIMEOUT_SEC
                                               if timeout is None else timeout))


def register_completed_job_retention(*, on_deleted=None) -> None:
    """Register the Jobs API event callback used by automatic retention."""
    if callable(on_deleted):
        _completed_retention_hooks["on_deleted"] = on_deleted


def _emit_retention_deleted(job_ids: list[str]) -> None:
    """Emit automatic-deletion events on the caller's event loop/thread."""
    on_deleted = _completed_retention_hooks.get("on_deleted")
    if not callable(on_deleted):
        return
    for job_id in job_ids:
        try:
            on_deleted(job_id)
        except Exception:
            pass


def _retention_marker_path(registry_root: str | Path) -> Path:
    return Path(registry_root) / "jobs" / ".completed-retention.json"


def _valid_updated_at(value: Any) -> float | None:
    # Job records persist updatedAt as Unix seconds. Reject bools, strings,
    # NaN, infinities, and malformed/missing values so they are retained.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _completed_job_is_expired(job: dict, cutoff: float) -> bool:
    if not isinstance(job, dict) or job.get("status") != "completed":
        return False
    updated_at = _valid_updated_at(job.get("updatedAt"))
    return updated_at is not None and updated_at <= cutoff


_RETENTION_JOB_STATUSES = frozenset({"completed", "failed", "timed_out", "cancelled"})
_RETENTION_LOG_SAFE_STATUSES = _RETENTION_JOB_STATUSES | {"pending", "scheduled"}
_RETENTION_RULES = ("completed", "failed", "timed_out", "cancelled", "logs")


def _validate_retention_days(retention_days: int) -> None:
    if (type(retention_days) is not int
            or not (_config.COMPLETED_JOB_RETENTION_MIN_DAYS
                    <= retention_days <= _config.COMPLETED_JOB_RETENTION_MAX_DAYS)):
        raise ValueError("retention_days is outside the supported range")


def _retention_result(now: float, scanned: int = 0) -> dict:
    return {
        "scannedAt": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "scanned": scanned,
        "deleted": 0,
        "skipped": 0,
        "errorCount": 0,
        "errors": [],
    }


def _add_retention_error(result: dict, message: str) -> None:
    result["errorCount"] = int(result.get("errorCount") or 0) + 1
    errors = result.setdefault("errors", [])
    if len(errors) < 20:
        errors.append(str(message)[:500])


def _job_is_expired_for_status(job: dict, status: str, cutoff: float) -> bool:
    if not isinstance(job, dict) or job.get("status") != status:
        return False
    updated_at = _valid_updated_at(job.get("updatedAt"))
    return updated_at is not None and updated_at <= cutoff


@_legacy_write
def cleanup_jobs_by_statuses(*, registry_root: str | Path,
                             retention_days_by_status: dict[str, int],
                             now: float | None = None, emit_events: bool = True) -> dict[str, dict]:
    """Fenced compatibility adapter for the canonical retention policy."""
    from packages.core import background_jobs as registry
    from packages.jobs.final_runtime.coordinator import cleanup_legacy_jobs
    return cleanup_legacy_jobs(registry, registry_root=registry_root,
        retention_days_by_status=retention_days_by_status, now=now, emit_events=emit_events)


def cleanup_completed_jobs(*, registry_root: str | Path,
                           retention_days: int,
                           now: float | None = None,
                           emit_events: bool = True) -> dict:
    """Compatibility wrapper for the first-phase completed-only cleaner."""
    _validate_retention_days(retention_days)
    return cleanup_jobs_by_statuses(
        registry_root=registry_root,
        retention_days_by_status={"completed": retention_days},
        now=now,
        emit_events=emit_events,
    )["completed"]


def _is_reparse_stat(value: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return (stat.S_ISLNK(value.st_mode)
            or bool(getattr(value, "st_file_attributes", 0) & reparse_flag))


def _path_has_reparse_component(path: Path) -> bool:
    """Fail closed if any existing component is a symlink or reparse point."""
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            value = current.lstat()
        except FileNotFoundError:
            break
        except OSError:
            return True
        if _is_reparse_stat(value):
            return True
    return False


def _controlled_logs_dir(registry_root: str | Path) -> tuple[Path | None, str | None]:
    root = Path(os.path.abspath(registry_root))
    if _path_has_reparse_component(root):
        return None, "registry root contains a symlink or reparse point"
    logs_dir = root / "logs"
    try:
        value = logs_dir.lstat()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"cannot inspect logs directory: {exc}"
    if _is_reparse_stat(value) or not stat.S_ISDIR(value.st_mode):
        return None, "logs path is not a plain directory"
    return logs_dir, None


def _canonical_log_path(value: Any, job: dict, logs_dir: Path) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = Path(value)
    job_id = job.get("jobId")
    # All Pan Jobs runners use this deterministic direct-child name. Custom,
    # relative, nested, and external paths are not eligible for cleanup.
    if (not candidate.is_absolute()
            or not isinstance(job_id, str)
            or candidate.name != f"{job_id}.log"
            or os.path.normcase(os.path.abspath(candidate.parent))
            != os.path.normcase(os.path.abspath(logs_dir))):
        return None
    return os.path.normcase(os.path.abspath(candidate))


def _runner_active_or_unknown(job: dict) -> bool:
    """Keep the log unless every recorded Runner identity is safely inactive."""
    try:
        import psutil
    except Exception:
        return any(job.get(key) is not None for key in ("pid", "runnerPid"))

    identities = (
        (job.get("pid"), job.get("processCreatedAt")),
        (job.get("runnerPid"), job.get("runnerProcessCreatedAt")),
    )
    for pid, expected in identities:
        if pid is None:
            continue
        expected_time = _valid_updated_at(expected)
        if expected_time is None:
            return True
        try:
            process = psutil.Process(int(pid))
            created_at = process.create_time()
            if abs(created_at - expected_time) > 1.0:
                continue  # PID was reused; this is not the recorded Runner.
            if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                return True
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except Exception:
            return True
    return False


@_legacy_write
def cleanup_job_log_files(*, registry_root: str | Path,
                          retention_days: int, now: float | None = None) -> dict:
    """Compatibility adapter for canonical log-retention policy."""
    from packages.core import background_jobs as registry
    from packages.jobs.final_runtime.coordinator import cleanup_legacy_logs
    return cleanup_legacy_logs(registry, registry_root=registry_root, retention_days=retention_days, now=now)


def _read_retention_marker(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def completed_job_retention_status() -> dict:
    """Return the newest persisted result for each retention rule."""
    roots = {_root()}
    scheduled_root = _scheduled_task_root()
    if scheduled_root is not None:
        roots.add(scheduled_root)
    last_runs: dict[str, dict | None] = {rule: None for rule in _RETENTION_RULES}
    for root in roots:
        marker = _read_retention_marker(_retention_marker_path(root))
        stored_rules = marker.get("rules")
        if not isinstance(stored_rules, dict):
            stored_rules = {}
        for rule in _RETENTION_RULES:
            state = stored_rules.get(rule)
            last_run = state.get("lastRun") if isinstance(state, dict) else None
            if rule == "completed" and last_run is None:
                last_run = marker.get("lastRun")  # first-phase marker compatibility
            if not isinstance(last_run, dict) or not isinstance(last_run.get("scannedAt"), str):
                continue
            last_run = dict(last_run)
            last_run.setdefault("errorCount", 0)
            last_run.setdefault("errors", [])
            current = last_runs[rule]
            if current is None or last_run["scannedAt"] > current["scannedAt"]:
                last_runs[rule] = last_run
    return {"lastRun": last_runs["completed"], "lastRuns": last_runs}


@_legacy_write
def run_completed_job_retention(*, now: float | None = None,
                                emit_events: bool = False) -> dict:
    """Hot-read settings and serialize cleanup against setting changes."""
    now = time.time() if now is None else float(now)
    if not math.isfinite(now):
        raise ValueError("now must be finite")
    # The PUT handler takes the same cross-process config lock. A disable
    # request therefore cannot finish while a cleanup pass still uses the
    # previous enabled value.
    with _registry_lock("completed_retention_config"):
        return _run_completed_job_retention_locked(now, emit_events=emit_events)


def _run_completed_job_retention_locked(now: float, *, emit_events: bool = False) -> dict:
    """Run enabled retention rules at most once per 24 hours per registry."""
    try:
        config = _config.load_config()
        settings, validity = _config.job_retention_settings(config)
    except Exception:
        settings = {rule: dict(_config.COMPLETED_JOB_RETENTION_DEFAULT)
                    for rule in _RETENTION_RULES}
        validity = {rule: False for rule in _RETENTION_RULES}

    roots = {_root()}
    scheduled_root = _scheduled_task_root()
    if scheduled_root is not None:
        roots.add(scheduled_root)

    totals = {"scannedAt": None, "scanned": 0, "deleted": 0, "skipped": 0,
              "errorCount": 0, "errors": [], "rules": {}, "deletedJobIds": []}
    for root in roots:
        try:
            marker_path = _retention_marker_path(root)
            with _registry_lock("completed_retention_daily", root):
                marker = _read_retention_marker(marker_path)
                stored_rules = marker.get("rules")
                if not isinstance(stored_rules, dict):
                    stored_rules = {}
                else:
                    stored_rules = dict(stored_rules)
                marker_changed = not isinstance(marker.get("rules"), dict)
                # Migrate the completed-only marker written in phase one.
                if ("completed" not in stored_rules
                        and isinstance(marker.get("lastRunEpoch"), (int, float))):
                    stored_rules["completed"] = {
                        "enabled": marker.get("enabled") is True,
                        "lastRunEpoch": marker.get("lastRunEpoch"),
                        "lastRun": marker.get("lastRun"),
                    }
                    marker_changed = True

                due: dict[str, int] = {}
                for rule in _RETENTION_RULES:
                    state = stored_rules.get(rule)
                    if not isinstance(state, dict):
                        state = {}
                    else:
                        state = dict(state)
                    configured = settings[rule]
                    enabled = (validity[rule] and configured["enabled"] is True
                               and configured["days"] is not None)
                    if not enabled:
                        if state.get("enabled") is not False:
                            state["enabled"] = False
                            marker_changed = True
                        stored_rules[rule] = state
                        continue

                    last_epoch = state.get("lastRunEpoch")
                    if (not isinstance(last_epoch, bool)
                            and isinstance(last_epoch, (int, float))
                            and math.isfinite(float(last_epoch))
                            and now - float(last_epoch) < COMPLETED_RETENTION_SCAN_INTERVAL_SEC):
                        if state.get("enabled") is not True:
                            state["enabled"] = True
                            marker_changed = True
                        stored_rules[rule] = state
                        continue
                    due[rule] = configured["days"]
                    # Persist the gate before scanning. A failed final marker
                    # write (or interrupted cleanup) must not cause a full
                    # registry scan on every recovery tick.
                    state.update({"enabled": True, "lastRunEpoch": now})
                    stored_rules[rule] = state
                    marker_changed = True

                if marker_changed:
                    marker["rules"] = stored_rules
                    _atomic_write(marker_path, marker)
                    marker_changed = False

                due_statuses = {rule: days for rule, days in due.items()
                                if rule in _RETENTION_JOB_STATUSES}
                results: dict[str, dict] = {}
                if due_statuses:
                    try:
                        if set(due_statuses) == {"completed"}:
                            results["completed"] = cleanup_completed_jobs(
                                registry_root=root,
                                retention_days=due_statuses["completed"], now=now,
                                emit_events=emit_events)
                        else:
                            results.update(cleanup_jobs_by_statuses(
                                registry_root=root,
                                retention_days_by_status=due_statuses, now=now,
                                emit_events=emit_events))
                    except Exception as exc:
                        for rule in due_statuses:
                            result = _retention_result(now)
                            _add_retention_error(result, f"Job scan failed: {exc}")
                            results[rule] = result

                if "logs" in due:
                    try:
                        results["logs"] = cleanup_job_log_files(
                            registry_root=root, retention_days=due["logs"], now=now)
                    except Exception as exc:
                        result = _retention_result(now)
                        _add_retention_error(result, f"Log scan failed: {exc}")
                        results["logs"] = result

                for rule, result in results.items():
                    deleted_job_ids = result.pop("_deletedJobIds", [])
                    state = stored_rules.get(rule)
                    if not isinstance(state, dict):
                        state = {}
                    state.update({"enabled": True, "lastRunEpoch": now,
                                  "lastRun": result})
                    stored_rules[rule] = state
                    totals["scannedAt"] = result["scannedAt"]
                    totals["scanned"] += result["scanned"]
                    totals["deleted"] += result["deleted"]
                    totals["skipped"] += result["skipped"]
                    totals["errorCount"] += result["errorCount"]
                    totals["errors"].extend(result["errors"][:max(0, 20 - len(totals["errors"]))])
                    totals["rules"][rule] = result
                    totals["deletedJobIds"].extend(deleted_job_ids)
                    marker_changed = True

                if marker_changed:
                    marker["rules"] = stored_rules
                    _atomic_write(marker_path, marker)
        except Exception as exc:
            # A broken root or marker must not prevent other roots/rules from
            # being serviced on this recovery cycle.
            _add_retention_error(totals, f"Registry {root}: {exc}")
            continue
    return totals


# ── runs.jsonl（泛化执行历史，scheduled-task 首个消费者）──


@_legacy_write
def append_run_record(record: dict, registry_root: str | Path | None = None,
                      max_entries: int = 500) -> None:
    """Append one run row to ``runs.jsonl``; roll to the newest ``max_entries``."""
    if not isinstance(record, dict):
        return
    path = _root(registry_root) / "runs.jsonl"
    line = json.dumps(record, ensure_ascii=False)
    with _lock:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if len(lines) <= max_entries:
            return
        keep = lines[-max_entries:]
        tmp = path.with_suffix(path.suffix + f".{secrets.token_hex(4)}.tmp")
        tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                    raise
                time.sleep(0.01 * (attempt + 1))


def list_run_records(task_id: str | None = None, limit: int = 100,
                     registry_root: str | Path | None = None) -> list[dict]:
    from packages.jobs.final_runtime.coordinator import read_legacy_history
    return read_legacy_history(_root(registry_root), task_id=task_id, limit=limit)


# ── scheduled-task pass ──


def _scheduled_task_root() -> Path | None:
    resolver = _scheduled_task_hooks.get("root_resolver")
    if not callable(resolver):
        return None
    try:
        root = resolver()
    except Exception:
        return None
    if not root:
        return None
    return Path(root)


def _scheduled_task_config() -> dict:
    resolver = _scheduled_task_hooks.get("config_resolver")
    if not callable(resolver):
        return {}
    try:
        cfg = resolver()
    except Exception:
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _scheduled_task_grace(cfg: dict, entry: dict) -> float:
    raw = entry.get("graceSec", cfg.get("misfire_grace_sec", 300))
    try:
        return max(0.0, float(raw or 0))
    except (TypeError, ValueError):
        return 300.0


def _iso_local(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat()


def _scheduled_task_emit(event: dict) -> None:
    from packages.jobs.system_actions import notify
    notify(event)
    callback = _scheduled_task_hooks.get("on_event")
    if callback is None:
        return
    try:
        result = callback(event)
    except Exception:
        return
    if inspect.isawaitable(result):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(_consume_emitted(result))
        else:
            close = getattr(result, "close", None)
            if callable(close):
                close()


async def _consume_emitted(awaitable) -> None:
    try:
        await awaitable
    except Exception:
        pass


def _entry_next_fire(entry: dict, base_dt: datetime) -> datetime | None:
    """Entry 自身就是合法 cron spec；在 base 之后找下一个网格点。"""
    try:
        return _job_cron.next_fire_after(entry, base_dt,
                                         tz_name=entry.get("timezone"))
    except ValueError:
        return None


def _entry_is_due(entry: dict, now_dt: datetime) -> bool:
    fire = _job_cron.parse_datetime(entry.get("nextFireAt"))
    return fire is not None and fire <= now_dt


def _job_next_fire(job: dict) -> str | None:
    """全部 enabled entries 的最早下一跳（ISO 或 None）。"""
    points: list[datetime] = []
    for entry in (job.get("schedule") or []):
        if not entry.get("enabled", True):
            continue
        point = _job_cron.parse_datetime(entry.get("nextFireAt"))
        if point is not None:
            points.append(point)
    if not points:
        return None
    return _iso_local(min(points))


def _apply_entry_change(job_id: str, entry_id: str, entry_patch: dict,
                        job_patch: dict, registry_root: str | Path | None) -> dict | None:
    """单 entry 的读-改-写，全程持跨进程 job 锁（P0 锁纪律）。"""
    with _lock, _job_lock(job_id, registry_root):
        path = _job_path(job_id, registry_root)
        current = _load_path(path)
        if not current or current.get("kind") != SCHEDULED_TASK_KIND:
            return None
        schedule = list(current.get("schedule") or [])
        replaced = False
        for index, entry in enumerate(schedule):
            if (entry.get("id") or "entry0") == entry_id:
                merged = dict(entry)
                merged.update(entry_patch)
                schedule[index] = merged
                replaced = True
                break
        if not replaced:
            return None
        current["schedule"] = schedule
        current.update(job_patch)
        current["nextFireAt"] = _job_next_fire(current)
        current["updatedAt"] = time.time()
        _write_job(path, current)
        return current


def _append_run_record_once(record: dict,
                            registry_root: str | Path | None = None) -> None:
    """Append a deterministic run record at most once across process restarts."""
    run_id = record.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        append_run_record(record, registry_root=registry_root)
        return
    with _registry_lock("runs", registry_root):
        existing = list_run_records(limit=0, registry_root=registry_root)
        if any(item.get("run_id") == run_id for item in existing):
            return
        append_run_record(record, registry_root=registry_root)


def _process_create_time(pid: int | None) -> float | None:
    if not pid:
        return None
    try:
        import psutil
        return psutil.Process(pid).create_time()
    except Exception:
        return None


def _owns_process(job: dict) -> Any:
    try:
        import psutil
        p = psutil.Process(int(job["pid"]))
        expected = job.get("processCreatedAt")
        if expected is None or abs(p.create_time() - float(expected)) > 1.0:
            return None
        if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
            return None
        return p
    except Exception:
        return None


@_legacy_write
def cancel(job_id: str, registry_root: str | Path | None = None) -> dict:
    job = get(job_id, registry_root)
    if not job:
        raise ValueError("job not found")
    if job.get("executionOwner") == "independent-job-service":
        from packages.jobs import pan_bridge
        return pan_bridge.public_job(pan_bridge.request("cancel", job_id))
    if job.get("kind") in {SESSION_MESSAGE_KIND, SESSION_BROADCAST_KIND}:
        return cancel_message(job_id, registry_root)
    if job.get("status") in {"cleanup_pending", "deleting"}:
        raise ValueError("cannot safely cancel: deletion cleanup pending; use explicit DELETE to recheck")
    if job.get("nextRetryAt") is not None:
        with _registry_lock("spawn-" + job_id, registry_root), _lock, _job_lock(job_id, registry_root):
            path = _job_path(job_id, registry_root)
            current = _load_path(path)
            if current and current.get("nextRetryAt") is not None:
                current.update(status="cancelled", reason="cancelled", nextRetryAt=None,
                               cancelRequestedAt=time.time(), updatedAt=time.time(),
                               terminalEventId=f"{job_id}:cancelled:terminal")
                _write_job(path, current)
                return current
        # A concurrent retry was admitted; use its identities for cancellation.
        return cancel(job_id, registry_root)
    if job.get("status") in {"completed", "failed", "cancelled"}:
        return job
    proc = _owns_process(job)
    runner = _owns_process({"pid": job.get("runnerPid"),
                            "processCreatedAt": job.get("runnerProcessCreatedAt")})
    if job.get("pid") and proc is None:
        raise ValueError("cannot safely cancel: task PID identity is unavailable or reused")
    if not proc and not runner:
        raise ValueError("cannot safely cancel: Runner PID identity is unavailable")
    # Publish accepted cancellation before killing. The runner can otherwise
    # observe the termination exit code and commit failed before cancel commits.
    with _lock, _job_lock(job_id, registry_root):
        current = _load_path(_job_path(job_id, registry_root))
        if not current: raise ValueError("job not found")
        if current.get("status") in {"cleanup_pending", "deleting"}:
            raise ValueError("cannot safely cancel: deletion cleanup pending")
        if current.get("status") in {"completed", "failed", "cancelled"}: return current
        current["cancelRequestedAt"] = time.time()
        _write_job(_job_path(job_id, registry_root), current)
    if proc:
        _kill_tree(proc)
    if runner and getattr(runner, "pid", None) != getattr(proc, "pid", None):
        _kill_tree(runner)
    with _lock, _job_lock(job_id, registry_root):
        current = _load_path(_job_path(job_id, registry_root))
        if not current:
            raise ValueError("job not found")
        if current.get("status") in {"cleanup_pending", "deleting"}:
            raise ValueError("cannot safely cancel: deletion cleanup pending")
        if current.get("status") in {"completed", "failed", "cancelled"}:
            return current
        current.update(status="cancelled", exitCode=None, updatedAt=time.time(),
                       terminalEventId=f"{job_id}:terminal", notificationState="pending")
        _write_job(_job_path(job_id, registry_root), current)
        return current


def _kill_tree(proc: Any) -> None:
    """Best-effort descendant-first termination for an owned process."""
    import psutil

    try:
        children = proc.children(recursive=True)
    except (OSError, AttributeError, psutil.NoSuchProcess):
        children = []
    for owned in [*reversed(children), proc]:
        try:
            owned.kill()
        except (OSError, AttributeError, psutil.NoSuchProcess):
            # Exit races are expected; still attempt every other owned member.
            pass


class ServiceJobBusy(ValueError):
    """Raised when a durable lifecycle Job already owns root+port."""

    def __init__(self, job: dict):
        super().__init__("a service lifecycle Job is already active")
        self.job = job


def _service_key(root: str, port: int) -> str:
    digest = hashlib.sha256(f"{Path(root).resolve()}:{int(port)}".encode("utf-8")).hexdigest()
    return f"service-{digest}"


def get_active_service_job(root: str, port: int,
                           registry_root: str | Path | None = None) -> dict | None:
    """Return the persisted in-flight lifecycle Job for one checkout/port."""
    root_value = str(Path(root).expanduser().resolve())
    for job in list_jobs(registry_root):
        if job.get("executionOwner") == "independent-job-service":
            continue
        if (job.get("kind") == SERVICE_LIFECYCLE_KIND
                and str(Path(str(job.get("root", ""))).expanduser().resolve()) == root_value
                and int(job.get("port", -1)) == int(port)
                and (job.get("phase") in SERVICE_ACTIVE_PHASES
                     or job.get("status") in {"pending", "running"})):
            return job
    return None


@_legacy_write
def create_service_job(*, request_id: str, operation: str, root: str, port: int,
                       old_pid: int | None = None,
                       old_pid_created_at: float | None = None,
                       log_path: str | None = None,
                       options: dict | None = None,
                       registry_root: str | Path | None = None) -> dict:
    """Atomically reserve a service lifecycle operation before spawning it."""
    if not request_id or not isinstance(request_id, str):
        raise ValueError("request_id is required")
    if not operation or not isinstance(operation, str):
        raise ValueError("operation is required")
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError("lifecycle options must be an object")
    # Persist a detached JSON snapshot.  The supervisor must be able to read
    # the same value in a different process after the request has returned.
    frozen_options = json.loads(json.dumps(options, ensure_ascii=False, sort_keys=True))
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise ValueError("service root does not exist")
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("service port is invalid")
    job_id = "job_" + secrets.token_hex(12)
    now = time.time()
    if log_path is None:
        log_path = str(_root(registry_root) / "logs" / f"{job_id}.log")
    registry_path = str(_root(registry_root))
    job = {
        "jobId": job_id, "kind": SERVICE_LIFECYCLE_KIND, "operation": operation,
        "name": f"{operation} Pan service on port {port}",
        "description": "",
        "options": frozen_options,
        "requestId": request_id, "phase": "requested", "status": "pending",
        "root": str(root_path), "port": port, "registryRoot": registry_path,
        "oldPid": old_pid, "oldPidCreatedAt": old_pid_created_at,
        "newPid": None, "newPidCreatedAt": None, "error": None,
        "errors": [],
        "createdAt": now, "updatedAt": now, "logPath": str(log_path),
    }
    with _lock, _registry_lock(_service_key(str(root_path), port), registry_path):
        active = get_active_service_job(str(root_path), port, registry_path)
        if active:
            raise ServiceJobBusy(active)
        _create(job, registry_path)
    return job


@_legacy_write
def transition_service_job(job_id: str, phase: str, *, registry_root: str | Path | None = None,
                           **changes: Any) -> dict:
    """Persist one legal lifecycle transition with a single atomic write."""
    if phase not in SERVICE_ACTIVE_PHASES | SERVICE_TERMINAL_PHASES:
        raise ValueError(f"invalid service lifecycle phase: {phase}")
    with _lock, _job_lock(job_id, registry_root):
        path = _job_path(job_id, registry_root)
        current = _load_path(path)
        if not current or current.get("kind", BACKGROUND_PROCESS_KIND) != SERVICE_LIFECYCLE_KIND:
            raise ValueError("service lifecycle Job not found")
        previous = current.get("phase")
        if current.get("operation") == "exit":
            allowed = {
                "requested": {"stopping_workers", "failed", "timed_out"},
                "stopping_workers": {"stopping_service", "failed", "timed_out"},
                "stopping_service": {"offline", "failed", "timed_out"},
                "offline": set(), "failed": set(), "timed_out": set(),
            }
        else:
            allowed = {
                "requested": {"stopping_workers", "stopping", "failed", "timed_out"},
                "stopping_workers": {"stopping", "failed", "timed_out"},
                "stopping": {"stopped", "failed", "timed_out"},
                "stopped": {"starting", "failed", "timed_out"},
                "starting": {"ready", "failed", "timed_out"},
                "ready": set(), "failed": set(), "timed_out": set(),
            }
        if phase != previous and phase not in allowed.get(previous, set()):
            raise ValueError(f"invalid service lifecycle transition: {previous} -> {phase}")
        # A successful service stop and a successful *Exit Job* are separate
        # facts.  In particular, worker shutdown may have failed before the
        # detached supervisor confirmed that the service itself is offline.
        # Do not let the offline confirmation erase that failure.
        requested_error = changes.get("error", ...)
        if phase == "offline" and requested_error is None:
            changes.pop("error", None)
        errors = current.get("errors")
        if not isinstance(errors, list):
            errors = []
        legacy_error = current.get("error")
        if legacy_error and legacy_error not in errors:
            errors.append(legacy_error)
        new_error = changes.get("error")
        if new_error and new_error not in errors:
            errors.append(new_error)
        if errors:
            changes["errors"] = errors

        current.update(changes)
        has_error = bool(current.get("error")) or bool(current.get("errors"))
        status = ("failed" if phase == "offline" and has_error else "completed") if phase in {
            "ready", "offline"
        } else (
            phase if phase in {"failed", "timed_out"} else "running")
        current.update(phase=phase, status=status, updatedAt=time.time())
        _write_job(path, current)
        return _normalize_job(current)


def find_service_job(request_id: str, registry_root: str | Path | None = None) -> dict | None:
    """Find a lifecycle Job by the API-facing request id."""
    for job in list_jobs(registry_root):
        if job.get("kind") == SERVICE_LIFECYCLE_KIND and job.get("requestId") == request_id:
            return job
    return None


@_legacy_write
def claim_hidden_runner(job_id: str, *, expected_attempt: str | None = None) -> bool:
    """Publish the real Python owner before hidden task creation, exactly once.

    The spawning controller can die before persisting Popen's redirector PID.
    The runner therefore owns this claim, fenced by the persisted attempt ID.
    """
    from packages.core.terminal.identity import probe_process, ProcessStatus
    pid = os.getpid()
    probe = probe_process(pid)
    if probe.status is not ProcessStatus.ALIVE or probe.identity is None:
        raise OSError("hidden runner creation identity unavailable")
    filetime = probe.identity.created_at_filetime
    with _lock, _job_lock(job_id):
        path = _job_path(job_id)
        current = _load_path(path)
        if (not current or current.get("status") not in {"starting", "running"}
                or current.get("runnerClaim")
                or (expected_attempt is not None and current.get("attemptId", "legacy") != expected_attempt)):
            return False
        if current.get("cancelRequestedAt"):
            current.update(status="cancelled", exitCode=None,
                           terminalEventId=f"{job_id}:terminal", notificationState="pending")
            current["updatedAt"] = time.time()
            _write_job(path, current)
            return False
        current.update(runnerPid=pid, runnerFiletime=str(filetime),
                       runnerProcessCreatedAt=filetime / 10_000_000 - 11644473600,
                       runnerClaim=secrets.token_hex(16), updatedAt=time.time())
        _write_job(path, current)
        return True


@_legacy_write
def runner_spawn(job_id: str, factory, *, expected_attempt: str | None = None):
    """Serialize task spawn/publication without holding the global registry gate."""
    with _registry_lock("task-spawn-" + job_id):
        with _lock, _job_lock(job_id):
            path = _job_path(job_id)
            current = _load_path(path)
            if (not current or current.get("status") not in {"starting", "running"}
                    or current.get("cancelRequestedAt")
                    or (expected_attempt is not None and current.get("attemptId", "legacy") != expected_attempt)):
                raise ValueError("Job no longer admitted for task spawn")
        proc = factory()
        try:
            facts = dict(status="running", pid=proc.pid,
                         processCreatedAt=_process_create_time(proc.pid), updatedAt=time.time())
            if os.name == "nt":
                from packages.core.terminal.identity import probe_process
                probe = probe_process(proc.pid)
                if probe.identity is None:
                    raise ValueError("spawned task identity unavailable")
                facts["processFiletime"] = str(probe.identity.created_at_filetime)
                guard = getattr(proc, "job_guard", None)
                if guard is not None:
                    facts["processJobHandle"] = str(guard.raw_handle)
            with _lock, _job_lock(job_id):
                current = _load_path(path)
                if (not current or current.get("status") not in {"starting", "running"}
                        or current.get("cancelRequestedAt")
                        or (expected_attempt is not None and current.get("attemptId", "legacy") != expected_attempt)):
                    raise ValueError("Job no longer admitted after task spawn")
                current.update(facts)
                _write_job(path, current)
            return proc
        except BaseException:
            if getattr(proc, "job_guard", None) is not None:
                proc.close()
            else:
                proc.kill()
                proc.wait(timeout=2)
            raise


@_legacy_write
def runner_update(job_id: str, *, expected_attempt: str | None = None, **changes: Any) -> dict:
    job = get(job_id)
    if not job:
        raise ValueError("job not found")
    changes = dict(changes)
    if "reason" in changes and changes["reason"] not in _retry.REASONS:
        raise ValueError("invalid terminal reason")
    if changes.get("status") in {"completed", "failed", "cancelled"}:
        changes.setdefault("terminalEventId", f"{job_id}:terminal")
        changes["notificationState"] = "pending"
    changes["updatedAt"] = time.time()
    with _lock, _job_lock(job_id):
        current = _load_path(_job_path(job_id))
        if not current: raise ValueError("job not found")
        if expected_attempt is not None and (current.get("attemptId", "legacy") != expected_attempt
                                            or current.get("status") in _retry.TERMINAL):
            return current
        if current.get("cancelRequestedAt") and changes.get("status") in {"completed", "failed"}:
            # The runner may finish before the cancelling caller commits (or
            # that caller may disappear). Do not leave an active pending record.
            changes.update(status="cancelled", exitCode=None, reason="cancelled")
        if current.get("status") in {"cleanup_pending", "deleting"}:
            if changes.get("status") in {"completed", "failed", "cancelled"}:
                # This is an observation, not an admitted terminal transition.
                # Keep the executable Attempt snapshot until deletion has its
                # own complete exit proof; do not publish a terminal outbox ID.
                changes["cleanupRunnerOutcome"] = {
                    key: changes.pop(key) for key in
                    ("status", "exitCode", "error", "processTreeEnded") if key in changes}
                changes.pop("terminalEventId", None)
                changes.pop("notificationState", None)
            changes.pop("status", None)
        if current.get("status") == "cancelled":
            return current
        from packages.jobs.action_runtime import is_action_job
        if (is_action_job(current) and not current.get('execution')
                and current.get('lastExecution', {}).get('status') in {'unknown', 'uncertain'}
                and changes.get('status') in {'completed', 'failed'}):
            current['processOutcome'] = {key: changes[key] for key in
                ('status', 'exitCode', 'error', 'processTreeEnded') if key in changes}
            changes.pop('status', None)
            changes.pop('terminalEventId', None)
            changes.pop('notificationState', None)
        current.update(changes)
        _write_job(_job_path(job_id), current)
        return current


@_legacy_write
def reconcile_running(registry_root: str | Path | None = None) -> int:
    """Resolve running records whose independent Runner disappeared.

    A live Runner is the safe re-attach case. Missing identity, unavailable
    liveness inspection, PID reuse, or a dead Runner is persisted as failed
    with reason=orphaned. Only explicit policies admit a replacement attempt.
    """
    from packages.jobs.action_runtime import is_action_job
    changed = 0
    for job in list_jobs(registry_root):
        if job.get("executionOwner") == "independent-job-service":
            continue
        if is_action_job(job) and not job.get('processAdmissionId'):
            continue  # The Action runtime owns admission and non-process effects.
        if job.get("kind") in {
            SERVICE_LIFECYCLE_KIND, SESSION_MESSAGE_KIND, SESSION_BROADCAST_KIND,
            SCHEDULED_TASK_KIND}:
            continue
        if job.get("status") not in {"starting", "running"}:
            continue
        if (not job.get('runnerClaim') and job.get('runnerSpawnStartedAt')
                and time.time() - job['runnerSpawnStartedAt'] < 5):
            continue  # Windows venv redirector can exit before real Python claims.
        runner_pid = job.get("runnerPid")
        runner_created = job.get("runnerProcessCreatedAt")
        runner = _owns_process({"pid": runner_pid, "processCreatedAt": runner_created})
        if runner is not None:
            continue
        with (_registry_lock("spawn-" + job["jobId"], registry_root),
              _lock, _job_lock(job["jobId"], registry_root)):
            path = _job_path(job["jobId"], registry_root)
            current = _load_path(path)
            if (not current or current.get("status") not in {"starting", "running"}
                    or current.get("attemptId") != job.get("attemptId")
                    or current.get("runnerPid") != runner_pid
                    or current.get("runnerProcessCreatedAt") != runner_created):
                # A runner may commit its terminal fact during the liveness probe.
                # Do not replace that fact (or a different owner generation).
                continue
            reason = "runner identity missing, unavailable, dead, or PID reused"
            if job.get("scheduledParentJobId"):
                task_process = _owns_process({
                    "pid": job.get("pid"),
                    "processCreatedAt": job.get("processCreatedAt"),
                })
                if task_process is not None:
                    _kill_tree(task_process)
                    try:
                        task_process.wait(timeout=2.0)
                    except Exception:
                        pass
                    reason = "Runner disappeared; terminated the verified scheduled shell process"
            current.update(status="failed", reason="orphaned", error="orphaned: " + reason,
                           terminalEventId=f"{job['jobId']}:terminal",
                           notificationState="pending", updatedAt=time.time())
            _write_job(path, current)
            changed += 1
    changed += retry_due(registry_root)
    return changed


@_legacy_write
def retry_due(registry_root: str | Path | None = None) -> int:
    """Claim due attempts durably before spawn; concurrent recovery is idempotent.

    Unknown task ownership blocks replay: losing a runner is not proof that its
    side-effecting child stopped. Do not kill or duplicate an unverified child.
    """
    count = 0
    for job in list_jobs(registry_root):
        if not _retry.eligible(job) or job.get("nextRetryAt") is None:
            continue
        job_id = job["jobId"]
        with (_registry_lock("spawn-" + job_id, registry_root),
              _registry_lock("task-spawn-" + job_id, registry_root)):
            with _lock, _job_lock(job_id, registry_root):
                path = _job_path(job_id, registry_root)
                current = _load_path(path)
                if (not current or current.get("status") not in {"failed", "cancelled"}
                        or current.get("nextRetryAt") is None
                        or current["nextRetryAt"] > time.time()):
                    continue
                _retry.defaults(current)
                if (current.get("cancelRequestedAt") or current["status"] == "cancelled"
                        or current.get("reason") == "cancelled" or not current["remaining"]
                        or current.get("reason") not in current["retryIf"]
                        or not (current.get("idempotencyKey") or current.get("deduplication"))):
                    current["nextRetryAt"] = None
                    _write_job(path, current)
                    continue
                if current.get("pid") and not current.get("processTreeEnded"):
                    import psutil
                    try:
                        alive = psutil.pid_exists(current["pid"])
                    except (OSError, ValueError):
                        alive = True
                    if alive:
                        current["retryBlockedReason"] = "task exit is unverified"
                        _write_job(path, current)
                        continue
                _retry.new_attempt(current, time.time())
                from packages.jobs.system_actions import prepare
                prepare(current, job_id + ":retry:" + current["attemptId"])
                _write_job(path, current)
            try:
                _spawn_background_runner_unlocked(current, registry_root)
            except _RunnerSpawnFailed:
                # Spawn failure is already durable; future recovery applies the policy.
                pass
            count += 1
    return count


async def _scheduled_task_emit_strict(event: dict) -> bool:
    """可靠发射：返回事件是否已成功入队广播通道（失败由调用方保持可重试）。

    on_event_strict（scheduler emit_strict）优先；未注册时退回 on_event 的
    尽力而为语义（回调异常 = 失败）。入队成功即算数：每连接 socket 发送
    按 broadcast 的隔离契约不 await，断线客户端由重连 REST 水合收敛。
    job.updated 是状态 upsert，客户端对重复事件幂等：成功后、标记持久化
    前崩溃导致的重发是允许的收敛路径，因此这里绝不以牺牲送达换取严格一次。
    """
    from packages.jobs.system_actions import notify
    notify(event)
    strict = _scheduled_task_hooks.get("on_event_strict")
    callback = strict if callable(strict) else _scheduled_task_hooks.get("on_event")
    if not callable(callback):
        return False
    try:
        result = callback(event)
        if inspect.isawaitable(result):
            result = await result
        return result is not False
    except Exception:
        return False


def _terminal_broadcast_ledger_path() -> Path:
    return _root() / "jobs" / ".terminal-broadcast-ledger.json"


def _load_terminal_broadcast_ledger() -> dict:
    try:
        data = json.loads(_terminal_broadcast_ledger_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("jobs"), dict):
        return data["jobs"]
    return data if isinstance(data, dict) else {}


@_legacy_write
def _save_terminal_broadcast_ledger(ledger: dict) -> None:
    path = _terminal_broadcast_ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, {"jobs": ledger})


async def stop_recovery_loop() -> None:
    """Retained shutdown adapter; no legacy recovery task can be started."""
    return


# Removed R6-A leaves are absent; stale Python callers receive an explicit refusal.
_RETIRED_ENTRYPOINTS = frozenset({
    'start',
    'retry',
    'start_scheduled_process',
    '_message_next_run',
    '_normalize_target_session_ids',
    '_message_job_common',
    'start_message',
    'start_broadcast',
    'update_message',
    'run_due_message_jobs',
    '_tail_log_output',
    '_run_job_action',
    '_execute_job_action',
    '_scheduled_task_requeue_after',
    '_append_task_run',
    '_reconcile_scheduled_processes',
    '_redeliver_undelivered',
    '_advance_paused_entries',
    '_repair_missing_next_fire',
    '_backlog_undelivered_fire',
    '_fire_scheduled_entry',
    'run_due_scheduled_tasks',
    '_terminal_broadcast_candidate',
    '_mark_terminal_broadcast',
    '_broadcast_independent_terminals',
    'recover_notifications',
    '_recovery_loop',
    'start_recovery_loop',
})


def __getattr__(name):
    from packages.jobs.retired import missing
    return missing(name, _RETIRED_ENTRYPOINTS)
