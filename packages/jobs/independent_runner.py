"""One-shot process owned by independent_service; no Pan runtime imports."""
from __future__ import annotations
import argparse
import ctypes
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from ctypes import wintypes


def _connect(root):
    db = sqlite3.connect(Path(root) / "jobs.sqlite3", timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    return db


def _identity(pid):
    if os.name != "nt":
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            rest = stat[stat.rfind(")") + 2:].split()
            return rest[19] if len(rest) > 19 else None
        except OSError: return None
    try:
        from packages.core.terminal.identity import open_process_for_probe, read_creation_filetime, close_handle_checked
        h = open_process_for_probe(pid)
        if not h: return None
        try: return str(read_creation_filetime(h))
        finally: close_handle_checked(h)
    except Exception: return None


def _process_group_gone(pgid):
    if os.name == "nt": return False
    try:
        os.killpg(pgid, 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:
        return False


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


def _run_windows_task(root, job_id, claim, payload, env, log_path):
    """Create suspended, assign and prove a kill-on-close Job, then resume once."""
    import msvcrt
    from packages.core.terminal.guard import JobObjectGuard
    from packages.core.terminal.identity import read_creation_filetime, wait_state, informational_exit_code, ProcessStatus, close_handle_checked

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION)]
    kernel.CreateProcessW.restype = wintypes.BOOL
    kernel.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel.SetHandleInformation.restype = wintypes.BOOL
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL

    process_info = _PROCESS_INFORMATION()
    process_handle = thread_handle = 0
    guard = None
    assigned = resumed_ok = False
    final_status, exit_code = "failed", -1
    with log_path.open("ab", buffering=0) as log, open("NUL", "rb") as null_in:
        std_handles = [int(msvcrt.get_osfhandle(null_in.fileno())),
                       int(msvcrt.get_osfhandle(log.fileno()))]
        for handle in std_handles:
            if not kernel.SetHandleInformation(wintypes.HANDLE(handle), 1, 1):
                raise OSError("could not make task output handles inheritable")
        startup = _STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)
        startup.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
        startup.hStdInput = std_handles[0]
        startup.hStdOutput = std_handles[1]
        startup.hStdError = std_handles[1]
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(payload["argv"]))
        env_block = "\0".join(f"{key}={value}" for key, value in sorted(env.items(), key=lambda pair: pair[0].upper())) + "\0\0"
        env_buffer = ctypes.create_unicode_buffer(env_block)
        created = kernel.CreateProcessW(None, command, None, None, True,
            0x00000004 | 0x00000200 | 0x08000000 | 0x00000400,
            ctypes.cast(env_buffer, ctypes.c_void_p), payload["cwd"],
            ctypes.byref(startup), ctypes.byref(process_info))
        if not created:
            return "failed", -int(ctypes.get_last_error()), None, None
        process_handle, thread_handle = int(process_info.hProcess), int(process_info.hThread)
        pid = int(process_info.dwProcessId)
        try:
            creation = read_creation_filetime(process_handle)
            if creation is None: raise RuntimeError("task creation FILETIME unavailable")
            guard = JobObjectGuard(kill_on_close=True)
            guard.assign(process_handle)
            assigned = True
            if not guard.is_member(process_handle) or (guard.active_processes() or 0) < 1:
                raise RuntimeError("task membership in owned Job Object was not proven")
            with _connect(root) as db:
                changed = db.execute("UPDATE jobs SET status='running',task_pid=?,task_filetime=?,updated=? WHERE id=? AND status='starting' AND runner_claim=?",
                    (pid, str(creation), time.time(), job_id, claim)).rowcount
            if changed != 1: raise RuntimeError("runner claim lost before task resume")
            resumed = int(kernel.ResumeThread(wintypes.HANDLE(thread_handle)))
            if resumed != 1: raise RuntimeError("task resume count was not exactly one")
            resumed_ok = True
            while True:
                state = wait_state(process_handle)
                if state is ProcessStatus.DEAD: break
                if state is ProcessStatus.UNKNOWN:
                    return "UNKNOWN", None, pid, str(creation)
                with _connect(root) as db:
                    cancel = db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
                if cancel and cancel[0]:
                    try:
                        _, remaining = guard.terminate_tree(pid, timeout=5.0)
                    except Exception:
                        return "UNKNOWN", None, pid, str(creation)
                    if remaining:
                        return "UNKNOWN", None, pid, str(creation)
                    exit_code = informational_exit_code(process_handle)
                    return "cancelled", exit_code, pid, str(creation)
                time.sleep(.05)
            exit_code = informational_exit_code(process_handle)
            active = guard.active_processes()
            members = guard.member_pids()
            if active != 0 or members:
                # Direct command ended but owned descendants remain; close-on-close will contain them.
                return "UNKNOWN", exit_code, pid, str(creation)
            return ("completed" if exit_code == 0 else "failed"), exit_code, pid, str(creation)
        except Exception:
            if assigned and guard is not None and guard.handle_open:
                try: guard.terminate_tree(pid, timeout=5.0)
                except Exception: pass
            elif process_handle and not resumed_ok:
                # Not resumed: no user command could have run. Terminate through the
                # exact retained CreateProcess handle so an assign failure cannot orphan it.
                try:
                    kernel.TerminateProcess(wintypes.HANDLE(process_handle), 1)
                except Exception: pass
            return "UNKNOWN", None, pid, _identity(pid)
        finally:
            if guard is not None and guard.handle_open:
                guard.close()
            if thread_handle: close_handle_checked(thread_handle)
            if process_handle: close_handle_checked(process_handle)
def run(root, job_id):
    root = str(Path(root).resolve())
    with _connect(root) as db:
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job: return 2
    payload = json.loads(job["payload"])
    runner_pid = os.getpid()
    runner_filetime = _identity(runner_pid)
    claim = secrets.token_hex(16)
    with _connect(root) as db:
        db.execute("BEGIN IMMEDIATE")
        changed = db.execute("UPDATE jobs SET runner_pid=?,runner_filetime=?,runner_claim=?,updated=? WHERE id=? AND status='starting' AND runner_claim IS NULL",
            (runner_pid, runner_filetime, claim, time.time(), job_id)).rowcount
        db.execute("COMMIT")
    if changed != 1: return 3

    # Task environment does not inherit Pan settings or Python import injection.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAN_") and k.upper() != "PYTHONPATH"}
    log_path = Path(job["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    exit_code = None
    final_status = "failed"
    task_pid = task_filetime = None
    try:
        if os.name == "nt":
            final_status, exit_code, task_pid, task_filetime = _run_windows_task(root, job_id, claim, payload, env, log_path)
        else:
            with log_path.open("ab", buffering=0) as log:
                kwargs = {"cwd": payload["cwd"], "env": env, "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
                          "start_new_session": True}
                task = subprocess.Popen(payload["argv"], **kwargs)
                task_pid = task.pid
                task_filetime = _identity(task.pid)
                with _connect(root) as db:
                    changed = db.execute("UPDATE jobs SET status='running',task_pid=?,task_filetime=?,updated=? WHERE id=? AND status='starting' AND runner_claim=?",
                        (task.pid, task_filetime, time.time(), job_id, claim)).rowcount
                if changed != 1: raise RuntimeError("runner claim lost before task tracking")
                while task.poll() is None:
                    with _connect(root) as db:
                        cancel = db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
                    if cancel and cancel[0]:
                        try: os.killpg(task.pid, __import__("signal").SIGTERM)
                        except OSError: pass
                        try: exit_code = task.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            final_status = "UNKNOWN"; break
                        final_status = "cancelled" if _process_group_gone(task.pid) else "UNKNOWN"
                        break
                    time.sleep(.05)
                if exit_code is None and final_status != "UNKNOWN": exit_code = task.wait()
                if final_status not in ("UNKNOWN", "cancelled"):
                    final_status = "completed" if exit_code == 0 else "failed"
    except Exception:
        final_status = "UNKNOWN"
        exit_code = exit_code if exit_code is not None else -1

    event_id = f"{job_id}:terminal"
    event = {"eventId": event_id, "jobId": job_id, "executionOwner": "independent-job-service",
             "status": final_status, "exitCode": exit_code,
             "targetSessionId": payload["targetSessionId"], "creatorSessionId": payload["creatorSessionId"],
             "logPath": str(log_path)}
    with _connect(root) as db:
        db.execute("BEGIN IMMEDIATE")
        # Terminal fact and durable outbox entry share one transaction.
        db.execute("UPDATE jobs SET status=?,exit_code=?,updated=? WHERE id=? AND status IN ('starting','running')",
                   (final_status, exit_code, time.time(), job_id))
        actual = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
        if actual == final_status and final_status != "UNKNOWN":
            db.execute("INSERT OR IGNORE INTO outbox(event_id,job_id,payload,created) VALUES(?,?,?,?)",
                       (event_id, job_id, json.dumps(event, ensure_ascii=False), time.time()))
        db.execute("COMMIT")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(); p.add_argument("--root", required=True); p.add_argument("--job-id", required=True)
    a = p.parse_args(argv); return run(a.root, a.job_id)

if __name__ == "__main__": raise SystemExit(main())
