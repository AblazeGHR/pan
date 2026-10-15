"""Real production launchers with isolated registries; no deployed services."""
import asyncio
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import AsyncMock

import pytest
from packages.jobs.independent_service import JobStore
from packages.core import background_jobs as legacy
from packages.core.terminal.identity import open_process_for_probe, read_creation_filetime, wait_state, ProcessStatus, close_handle_checked

pytestmark = pytest.mark.skipif(os.name != "nt", reason="real Windows desktop lifecycle")
PROBE = Path(__file__).parent / "helpers/desktop_tree_probe.py"


def receipt(case, facts):
    """Optional compact audit output; never writes runtime databases to Git."""
    target = os.environ.get("PAN_TEST_JOB_TRIAL_EVIDENCE")
    if target:
        with Path(target).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"case": case, **facts}) + "\n")


def brief(rows):
    return [{key: r[key] for key in ("pid", "creationFiletime", "desktop", "inputDesktop", "gui", "console") if key in r} for r in rows]


def until(check, timeout=12):
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "owned test deadline"
        time.sleep(.02)


def released(name):
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.OpenDesktopW.argtypes = [w.LPCWSTR, w.DWORD, w.BOOL, w.DWORD]; user.OpenDesktopW.restype = w.HANDLE
    user.CloseDesktop.argtypes = [w.HANDLE]
    def gone():
        handle = user.OpenDesktopW(name, 0, False, 1)
        if handle: user.CloseDesktop(handle); return False
        assert ctypes.get_last_error() == 2
        return True
    until(gone, 5)


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    monkeypatch.setenv("PAN_BACKGROUND_JOBS_DIR", str(root))
    monkeypatch.setattr(legacy._sessions, "get", lambda sid: {"id": sid})
    # No external notices, scheduler or independent bridge during recovery tests.
    from packages.jobs import pan_bridge
    monkeypatch.setattr(pan_bridge, "recover_events", AsyncMock(return_value=0))
    monkeypatch.setattr(legacy, "run_due_scheduled_tasks", AsyncMock(return_value=0))
    monkeypatch.setattr(legacy, "_scheduled_task_root", lambda: None)
    monkeypatch.setattr(legacy._worker, "enqueue_notice", AsyncMock(return_value={"ok": True}))
    return root


def launch(kind, root, argv, cwd):
    if kind == "independent":
        store = JobStore(str(root))
        job = store.create({"argv": argv, "cwd": str(cwd), "targetSessionId": "owned-trial"})
        return job, lambda: store.get(job["jobId"]), lambda: store.request_cancel(job["jobId"]), store
    job = legacy.start("owned-trial", argv, str(cwd), registry_root=root)
    return job, lambda: legacy.get(job["jobId"], root), lambda: legacy.cancel(job["jobId"]), None


def retain(row):
    handle = open_process_for_probe(row["pid"])
    assert handle and read_creation_filetime(handle) == row["creationFiletime"]
    return handle


def test_legacy_scheduled_shell_uses_real_runner(isolated_registry, tmp_path):
    command = subprocess.list2cmdline([sys.executable, str(PROBE.resolve()), "2", "new", str(tmp_path)])
    parent = {"jobId": "job_owned_shell_parent", "kind": legacy.SCHEDULED_TASK_KIND,
              "name": "owned shell", "targetSessionId": None,
              "action": {"api": "shell", "args": {"command": command, "cwd": str(tmp_path)}}}
    legacy._create(parent, registry_root=isolated_registry)
    job = legacy.start_scheduled_process(parent, "owned-dispatch", registry_root=isolated_registry)
    try:
        until(lambda: legacy.get(job["jobId"], isolated_registry)["status"] not in ("starting", "running"))
        current = legacy.get(job["jobId"], isolated_registry)
        assert current["status"] == "completed" and current["exitCode"] == 0, Path(current["logPath"]).read_text(errors="replace")
        rows = [json.loads((tmp_path / f"{depth}.json").read_text()) for depth in range(3)]
        assert all(row["desktop"] != row["inputDesktop"] and row["inputOwnedWindows"] == [] for row in rows)
        released(rows[0]["desktop"])
        receipt("legacy-scheduled-shell", {"status": current["status"], "rows": brief(rows), "desktopReleased": True})
    finally:
        if legacy.get(job["jobId"], isolated_registry)["status"] in ("starting", "running"): legacy.cancel(job["jobId"])


@pytest.mark.parametrize("kind", ["independent", "legacy"])
@pytest.mark.parametrize("mode", ["new", "alloc"])
def test_production_three_generation_console_and_gui(isolated_registry, tmp_path, kind, mode):
    job, get, cancel, _ = launch(kind, isolated_registry, [sys.executable, str(PROBE.resolve()), "2", mode, str(tmp_path)], tmp_path)
    try:
        until(lambda: get()["status"] not in ("starting", "running"))
        result = get()
        assert result["status"] == "completed" and result["exitCode"] == 0, Path(result["logPath"]).read_text(errors="replace")
        rows = [json.loads((tmp_path / f"{depth}.json").read_text()) for depth in range(3)]
        assert len({r["desktop"] for r in rows}) == 1
        for row in rows:
            assert row["desktop"] != row["inputDesktop"] and row["inputOwnedWindows"] == []
            assert row["gui"]["creationFiletime"] == row["creationFiletime"]
            if row["console"]: assert row["console"]["desktop"] == row["desktop"]
        assert Path(result["logPath"]).read_text(errors="replace").count("probe") == 3
        released(rows[0]["desktop"])
        receipt(kind + "-" + mode, {"jobId": result["jobId"], "status": result["status"], "rows": brief(rows), "desktopReleased": True})
    finally:
        if get()["status"] in ("starting", "running"): cancel()


@pytest.mark.parametrize("kind", ["independent", "legacy"])
def test_production_stdout_exit_and_bidirectional_file_ipc(isolated_registry, tmp_path, kind):
    cwd = tmp_path / "owned 中文 cwd with spaces"; cwd.mkdir()
    program = """import os,sys,json,time
from pathlib import Path
print('owned stdout',flush=True)
print('owned stderr',file=sys.stderr,flush=True)
Path('request.tmp').write_text('child-request')
os.replace('request.tmp', 'request.txt')
end=time.monotonic()+8
while not Path('reply.txt').exists():
 assert time.monotonic()<end
 time.sleep(.01)
assert Path('reply.txt').read_text()=='parent-reply'
Path('cwd.json').write_text(json.dumps(os.getcwd()))
sys.exit(7)
"""
    job, get, cancel, _ = launch(kind, isolated_registry, [sys.executable, "-c", program], cwd)
    try:
        until(lambda: (cwd / "request.txt").exists())
        assert (cwd / "request.txt").read_text() == "child-request"
        (cwd / "reply.tmp").write_text("parent-reply")
        os.replace(cwd / "reply.tmp", cwd / "reply.txt")
        until(lambda: get()["status"] not in ("starting", "running"))
        assert get()["status"] == "failed" and get()["exitCode"] == 7
        assert json.loads((cwd / "cwd.json").read_text()) == str(cwd)
        text = Path(get()["logPath"]).read_text(errors="replace")
        assert "owned stdout" in text and "owned stderr" in text
        receipt(kind + "-stdio-ipc-exit", {"jobId": job["jobId"], "status": get()["status"], "exitCode": get()["exitCode"], "cwd": str(cwd), "fileIPC": True, "stdout": True, "stderr": True})
    finally:
        if get()["status"] in ("starting", "running"): cancel()


@pytest.mark.parametrize("kind", ["independent", "legacy"])
@pytest.mark.parametrize("policy", ["shutdown", "deadline"])
def test_production_shutdown_recovery_then_cancel(isolated_registry, tmp_path, kind, policy):
    job, get, cancel, store = launch(kind, isolated_registry, [sys.executable, str(PROBE.resolve()), "1", "hold", str(tmp_path)], tmp_path)
    handles = []
    try:
        until(lambda: (tmp_path / "child.json").exists())
        rows = [json.loads((tmp_path / f"{name}.json").read_text()) for name in ("hold", "child")]
        handles = [retain(row) for row in rows]
        before = get()
        if policy == "deadline":
            time.sleep(.1)  # These argv APIs have no native task timeout option.
        elif kind == "independent":
            restarted = JobStore(str(isolated_registry)); restarted.recover()
            assert restarted.get(job["jobId"])["runnerFiletime"] == before["runnerFiletime"]
        else:
            async def stop_controller():
                legacy.start_recovery_loop()
                await asyncio.sleep(.05)
                await legacy.stop_recovery_loop()
            asyncio.run(stop_controller())
            assert legacy.reconcile_running(isolated_registry) == 0
        # Controller shutdown/recovery leaves durable Jobs alive by design.
        assert get()["status"] == "running" and all(wait_state(h) is ProcessStatus.ALIVE for h in handles)
        cancel()
        until(lambda: get()["status"] == "cancelled")
        until(lambda: all(wait_state(h) is ProcessStatus.DEAD for h in handles), 8)
        released(rows[0]["desktop"])
        receipt(kind + "-" + policy + "-cancel", {"jobId": job["jobId"], "status": get()["status"], "rows": brief(rows), "retainedDead": True, "desktopReleased": True})
    finally:
        if get()["status"] in ("starting", "running"): cancel()
        for handle in handles: close_handle_checked(handle)


def test_legacy_runner_crash_recovery_and_cleanup(isolated_registry, tmp_path):
    job, get, cancel, _ = launch("legacy", isolated_registry, [sys.executable, str(PROBE.resolve()), "1", "hold", str(tmp_path)], tmp_path)
    handles = []; runner_handle = None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]; kernel.OpenProcess.restype = w.HANDLE
    kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
    try:
        until(lambda: (tmp_path / "child.json").exists())
        rows = [json.loads((tmp_path / f"{name}.json").read_text()) for name in ("hold", "child")]
        for row in rows:
            handle = kernel.OpenProcess(0x101001, False, row["pid"])
            assert handle and read_creation_filetime(handle) == row["creationFiletime"]
            handles.append(handle)
        current = get()
        runner_handle = kernel.OpenProcess(0x101001, False, current["runnerPid"])
        assert runner_handle
        assert abs(read_creation_filetime(runner_handle) / 10_000_000 - 11644473600 - current["runnerProcessCreatedAt"]) < .01
        assert kernel.TerminateProcess(runner_handle, 1)
        until(lambda: wait_state(runner_handle) is ProcessStatus.DEAD)
        assert legacy.reconcile_running(isolated_registry) == 1 and get()["status"] == "failed"
        # Hidden legacy tasks now enter the runner-owned kernel Job at creation.
        until(lambda: all(wait_state(handle) is ProcessStatus.DEAD for handle in handles), 8)
        receipt("legacy-runner-crash-contained", {"jobId": job["jobId"], "status": get()["status"], "rows": brief(rows), "retainedDead": True, "cleanupOwner": "runner-owned kernel Job"})
    finally:
        for handle in handles:
            if wait_state(handle) is not ProcessStatus.DEAD: assert kernel.TerminateProcess(handle, 1)
        if handles: until(lambda: all(wait_state(h) is ProcessStatus.DEAD for h in handles), 5)
        if runner_handle: close_handle_checked(runner_handle)
        for handle in handles: close_handle_checked(handle)
        if handles: released(rows[0]["desktop"])


def test_independent_runner_crash_recovery_and_cleanup(isolated_registry, tmp_path):
    job, get, cancel, store = launch("independent", isolated_registry, [sys.executable, str(PROBE.resolve()), "1", "hold", str(tmp_path)], tmp_path)
    handles = []; runner_handle = None
    try:
        until(lambda: (tmp_path / "child.json").exists())
        rows = [json.loads((tmp_path / f"{name}.json").read_text()) for name in ("hold", "child")]
        handles = [retain(row) for row in rows]
        current = get()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]; kernel.OpenProcess.restype = w.HANDLE
        kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        runner_handle = kernel.OpenProcess(0x101001, False, current["runnerPid"])
        assert runner_handle and str(read_creation_filetime(runner_handle)) == current["runnerFiletime"]
        assert kernel.TerminateProcess(runner_handle, 1)
        until(lambda: all(wait_state(h) is ProcessStatus.DEAD for h in handles), 8)
        store.recover(); assert get()["status"] == "UNKNOWN"
        from packages.jobs.independent_runner import run
        assert run(store.root, job["jobId"]) == 3
        released(rows[0]["desktop"])
        receipt("independent-runner-crash", {"jobId": job["jobId"], "status": get()["status"], "rows": brief(rows), "retainedDead": True, "desktopReleased": True, "restartClaimRefused": True})
    finally:
        if get()["status"] in ("starting", "running"): cancel()
        if runner_handle: close_handle_checked(runner_handle)
        for handle in handles: close_handle_checked(handle)
