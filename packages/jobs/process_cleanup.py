"""Explicit background-Job deletion; unknown ownership never means an empty tree.

Legacy unguarded trees are observable but cannot prove absence of escaped
children. They remain cleanup_pending; no best-effort PID kill is called success.
"""
from __future__ import annotations

import math
import os
import time
from contextlib import ExitStack

import psutil


def _capture_process(pid, expected=None, filetime=None):
    from packages.core.terminal.win_pipe import ProcessIdentityHandle
    process = psutil.Process(int(pid))
    # Preserve access/identity failures, including NoSuchProcess: without a
    # persisted completed-tree proof an absent root cannot prove no orphans.
    birth = process.create_time()
    if filetime is None and expected is not None and birth != float(expected):
        raise ValueError("process birth identity mismatch (zero tolerance)")
    retained = ProcessIdentityHandle.open(int(pid))
    if retained is None:
        raise ValueError("process retained identity unavailable")
    try:
        if filetime is not None and str(retained.filetime) != str(filetime):
            raise ValueError("raw FILETIME identity mismatch")
        from packages.core.terminal.contracts import ProcessStatus
        if retained.probe().status is not ProcessStatus.ALIVE:
            raise ValueError("process not observably alive during tree capture")
        return process, retained
    except BaseException:
        retained.close()
        raise


def _open_task_guard(job, runner, task):
    """Duplicate the runner's already-owned kernel Job; never recreate by name."""
    import ctypes
    from ctypes import wintypes
    from packages.core.terminal import identity
    from packages.core.terminal.guard import JobObjectGuard
    raw = job.get("processJobHandle")
    if not raw:
        raise ValueError("legacy tree has no provable kernel Job ownership")
    k = identity.kernel32()
    source = k.OpenProcess(0x40 | identity.PROCESS_QUERY_LIMITED_INFORMATION |
                           identity.SYNCHRONIZE, False, runner.pid)
    if not source:
        raise ValueError("runner duplicate-handle access unavailable")
    duplicate = wintypes.HANDLE()
    try:
        if identity.read_creation_filetime(source) != runner.filetime:
            raise ValueError("runner identity changed before Job acquisition")
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                     ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                     wintypes.BOOL, wintypes.DWORD]
        k.DuplicateHandle.restype = wintypes.BOOL
        if not k.DuplicateHandle(source, int(raw), k.GetCurrentProcess(),
                                 ctypes.byref(duplicate), 0, False, 2):
            raise ValueError("owned Job handle duplication failed")
        guard = JobObjectGuard.from_retained_handle(int(duplicate.value))
        duplicate = wintypes.HANDLE()
        try:
            if not guard.is_member(task.handle):
                raise ValueError("task not a member of duplicated owned Job")
            return guard
        except BaseException:
            guard.close()
            raise
    finally:
        identity.close_handle_checked(source)
        if duplicate.value:
            identity.close_handle_checked(duplicate.value)


def _verify_completed_proof(job, timeout):
    """Explicit recheck of durable whole-tree proof; never repeat child effects.

    A runner still alive after a prior bounded termination may be terminated
    again only by this explicit user delete and its saved raw identity. Unknown
    liveness/access is an error, not proof of exit.
    """
    if job.get("cleanupTreeEnded") is not True or not job.get("cleanupMembers"):
        return False
    from packages.core.terminal import identity
    from packages.core.terminal.contracts import ProcessStatus
    for member in job["cleanupMembers"]:
        try:
            process = psutil.Process(member["pid"])
            if process.create_time() != member["birth"]:
                raise ValueError("cleanup member PID reused; explicit reconciliation required")
            if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                if member['pid'] != job.get('runnerPid'):
                    raise ValueError("tree proof conflicts with a live task member")
                process, held = _capture_process(member['pid'], member['birth'], member['filetime'])
                try:
                    receipt = identity.kill_verified(held.pid, held.filetime, wait_timeout=timeout)
                    if not receipt.killed or not receipt.signaled_after_terminate or held.probe().status is not ProcessStatus.DEAD:
                        raise ValueError("runner recheck termination unconfirmed")
                finally:
                    held.close()
        except psutil.NoSuchProcess:
            continue
    return True


def terminate_owned_jobs(jobs, *, timeout):
    if not math.isfinite(float(timeout)) or timeout < 0:
        raise ValueError("invalid cleanup timeout")
    if os.name != "nt":
        raise ValueError("unguarded legacy process tree cleanup cannot be proven on this platform")
    from packages.core.terminal import identity
    from packages.core.terminal.contracts import ProcessStatus
    deadline = time.monotonic() + timeout
    for job in jobs:
        if _verify_completed_proof(job, max(0, deadline-time.monotonic())):
            continue
        if not job.get("runnerPid") or not job.get("pid"):
            raise ValueError("active Job runner/task identity missing; no termination attempted")
        with ExitStack() as stack:
            captured = {}
            roots = []
            for pid, birth, ft in ((job["runnerPid"], job.get("runnerProcessCreatedAt"), job.get("runnerFiletime")),
                                   (job["pid"], job.get("processCreatedAt"), job.get("processFiletime"))):
                if birth is None:
                    raise ValueError("process birth identity missing")
                process, held = _capture_process(pid, birth, ft)
                stack.callback(held.close)
                captured[pid] = (process, held)
                roots.append(process)
            # Capture EVERY root's full descendants before any termination.
            # AccessDenied / disappearing child / unreadable creation time is
            # unknown, not [] and never an eligible success receipt.
            for root in roots:
                for child in root.children(recursive=True):
                    if child.pid not in captured:
                        process, held = _capture_process(child.pid, child.create_time())
                        stack.callback(held.close)
                        captured[child.pid] = (process, held)
            runner = captured[job["runnerPid"]][1]
            task = captured[job["pid"]][1]
            guard = _open_task_guard(job, runner, task)
            stack.callback(guard.close)
            # Task descendants are covered even if born after capture. OS
            # console helpers may sit outside that Job: retain their identities
            # and require observed exit after the runner exits, never kill them
            # or classify ownership by executable name.
            persist = job.get("_cleanupPersist", lambda proof: None)
            members = [{"pid": pid, "birth": proc.create_time(), "filetime": str(held.filetime)}
                       for pid, (proc, held) in captured.items()]
            persist({"cleanupMembers": members})
            _, remaining = guard.terminate_tree(task.pid, timeout=max(0, deadline-time.monotonic()))
            if remaining or guard.last_terminate_error is not None:
                raise ValueError("owned Job termination refused or timed out")
            persist({"cleanupTreeEnded": True})
            # Runner stays alive until its child Job is proven empty. Retained
            # handles prevent PID reuse; native kill also validates raw FILETIME.
            if runner.probe().status is ProcessStatus.ALIVE:
                receipt = identity.kill_verified(runner.pid, runner.filetime,
                    wait_timeout=max(0, deadline-time.monotonic()))
                if not receipt.killed or not receipt.signaled_after_terminate:
                    raise ValueError("runner termination receipt unknown/refused")
            while True:
                states = [held.probe().status for _, held in captured.values()]
                if all(state is ProcessStatus.DEAD for state in states):
                    break
                if ProcessStatus.UNKNOWN in states or time.monotonic() >= deadline:
                    raise ValueError("captured member exit unconfirmed")
                time.sleep(0.02)
    return {"confirmed": True}
