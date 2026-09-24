import asyncio
import concurrent.futures
import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from packages.core import background_jobs as jobs
from packages.core import session as sess
from packages.core import worker


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "DEFAULT_ROOT", tmp_path / "background_jobs")
    monkeypatch.setattr(jobs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sess, "_all_loaded", False)
    sess._cache.clear()
    yield
    sess._cache.clear()


def _session(tmp_path, sid="ses_target"):
    s = sess.Session(id=sid, name=sid, workdir=str(tmp_path))
    sess._cache[sid] = s
    return s


def test_start_persists_metadata_and_detaches(monkeypatch, tmp_path):
    _session(tmp_path)
    _session(tmp_path, "ses_creator")
    proc = Mock(pid=4321)
    monkeypatch.setattr(jobs.subprocess, "Popen", Mock(return_value=proc))
    monkeypatch.setattr(jobs, "_process_create_time", lambda pid: 12.5)
    result = jobs.start(
        "ses_target", ["python", "train.py", "--epochs", "2"], str(tmp_path),
        creator_session_id="ses_creator",
    )
    assert result["status"] == "running"
    assert result["pid"] is None
    assert result["runnerPid"] == 4321
    assert result["runnerProcessCreatedAt"] == 12.5
    assert result["creatorSessionId"] == "ses_creator"
    assert jobs.get(result["jobId"])["logPath"].endswith(".log")
    assert jobs.subprocess.Popen.call_args.kwargs["stdin"] is jobs.subprocess.DEVNULL


@pytest.mark.parametrize("argv,cwd", [([], "x"), (["python"], ""), (["python"], "missing")])
def test_rejects_invalid_command_and_cwd(tmp_path, argv, cwd):
    _session(tmp_path)
    with pytest.raises(ValueError):
        jobs.start("ses_target", argv, cwd)


def test_terminal_notification_is_idempotent(monkeypatch, tmp_path):
    target = _session(tmp_path)
    job = {
        "jobId": "job_terminal", "targetSessionId": target.id, "status": "completed",
        "exitCode": 0, "notificationState": "pending", "terminalEventId": "job_terminal:terminal",
        "logPath": "x", "createdAt": 1, "creatorSessionId": "ses_creator",
    }
    jobs._save(job)
    calls = []

    async def notify(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True}

    monkeypatch.setattr(worker, "enqueue_notice", notify)
    assert asyncio.run(jobs.recover_notifications()) == 1
    assert asyncio.run(jobs.recover_notifications()) == 0
    assert len(calls) == 1
    assert calls[0][1]["event_id"] == "job_terminal:terminal"
    assert calls[0][1]["notice_kind"] == "background_job_terminal"
    assert calls[0][1]["job_id"] == "job_terminal"
    assert calls[0][1]["notice_status"] == "completed"
    assert calls[0][1]["creator_session_id"] == "ses_creator"
    assert calls[0][1]["target_session_ids"] == [target.id]
    assert jobs.get("job_terminal")["notificationState"] == "delivered"


def test_enqueue_notice_same_event_does_not_duplicate(tmp_path, monkeypatch):
    target = _session(tmp_path)
    monkeypatch.setattr(sess, "save_async", lambda s: None)
    async def save(s):
        return None
    monkeypatch.setattr(sess, "save_async", save)
    monkeypatch.setattr(worker, "_wake_worker", lambda *a, **k: asyncio.sleep(0))
    first = asyncio.run(worker.enqueue_notice(target.id, "done", source="automation", event_id="job_x:terminal"))
    second = asyncio.run(worker.enqueue_notice(target.id, "done", source="automation", event_id="job_x:terminal"))
    assert first["ok"] and second["duplicate"]
    assert len(target.queue_pending) == 1


def test_automatic_job_notice_keeps_structured_envelope_and_automation_label(tmp_path, monkeypatch):
    target = _session(tmp_path)

    async def save(_session):
        return None

    monkeypatch.setattr(sess, "save_async", save)
    monkeypatch.setattr(worker, "_wake_worker", lambda *a, **k: asyncio.sleep(0))
    result = asyncio.run(worker.enqueue_notice(
        target.id, '{"jobId":"job_x"}', source="automation",
        event_id="job_x:terminal", envelope={
            "jobId": "job_x", "status": "completed",
            "targetSessionId": target.id, "creatorSessionId": None}))
    assert result["ok"]
    item = target.queue_pending[0]
    assert item["envelope"]["jobId"] == "job_x"
    rendered = worker._format_report_batch([item])
    assert rendered.startswith("////by pan system")
    assert "jobId: job_x" in rendered
    assert f"targetSessionId: {target.id}" in rendered


def test_runner_registry_survives_reload(tmp_path):
    _session(tmp_path)
    jobs._save({"jobId": "job_reload", "targetSessionId": "ses_target", "status": "running", "createdAt": 1})
    assert jobs.list_jobs()[0]["jobId"] == "job_reload"
    assert json.loads((tmp_path / "background_jobs" / "jobs" / "job_reload.json").read_text())["status"] == "running"


def test_legacy_job_defaults_creator_without_rewriting(tmp_path):
    _session(tmp_path)
    jobs._save({"jobId": "job_legacy", "targetSessionId": "ses_target",
                "status": "completed", "createdAt": 1})
    raw = json.loads((tmp_path / "background_jobs" / "jobs" / "job_legacy.json").read_text())
    result = jobs.get("job_legacy")
    assert result["creatorSessionId"] is None
    assert "creatorSessionId" not in raw


def test_cancel_kills_descendants_and_marks_terminal(monkeypatch, tmp_path):
    _session(tmp_path)
    job = {"jobId": "job_cancel", "targetSessionId": "ses_target", "status": "running",
           "pid": 10, "processCreatedAt": 1, "createdAt": 1}
    jobs._save(job)

    class FakeProcess:
        def __init__(self):
            self.killed = []
        def children(self, recursive=False):
            assert recursive is True
            return [self.child]
        def kill(self):
            self.killed.append(self)
    process = FakeProcess()
    process.child = Mock()
    monkeypatch.setattr(jobs, "_owns_process", lambda value: process)
    result = jobs.cancel("job_cancel")
    process.child.kill.assert_called_once_with()
    assert process.killed == [process]
    assert result["status"] == "cancelled"
    assert result["terminalEventId"] == "job_cancel:terminal"


def test_cancel_does_not_kill_pid_reuse(monkeypatch, tmp_path):
    _session(tmp_path)
    jobs._save({"jobId": "job_reuse", "targetSessionId": "ses_target", "status": "running",
                "pid": 10, "processCreatedAt": 1, "createdAt": 1})
    monkeypatch.setattr(jobs, "_owns_process", lambda value: None)
    with pytest.raises(ValueError, match="cannot safely cancel"):
        jobs.cancel("job_reuse")


def test_reconcile_running_orphan_becomes_failed_without_kill(monkeypatch, tmp_path):
    _session(tmp_path)
    jobs._save({"jobId": "job_orphan", "targetSessionId": "ses_target", "status": "running",
                "pid": 10, "processCreatedAt": 1, "runnerPid": 11,
                "runnerProcessCreatedAt": 2, "createdAt": 1})
    monkeypatch.setattr(jobs, "_owns_process", lambda value: None)
    assert jobs.reconcile_running() == 1
    result = jobs.get("job_orphan")
    assert result["status"] == "failed"
    assert result["notificationState"] == "pending"
    assert "orphaned" in result["error"]


def test_retry_rejects_running_job(tmp_path):
    _session(tmp_path)
    jobs._save({"jobId": "job_running", "targetSessionId": "ses_target", "status": "running",
                "argv": ["python"], "cwd": str(tmp_path), "createdAt": 1})
    with pytest.raises(ValueError, match="cannot be retried"):
        jobs.retry("job_running")


def test_retry_inherits_creator_and_target(monkeypatch, tmp_path):
    _session(tmp_path)
    jobs._save({
        "jobId": "job_finished", "targetSessionId": "ses_target",
        "creatorSessionId": "ses_creator", "status": "failed",
        "argv": ["python", "job.py"], "cwd": str(tmp_path), "createdAt": 1,
    })
    calls = []

    def fake_start(target, argv, cwd, *, label=None, creator_session_id=None):
        calls.append((target, argv, cwd, label, creator_session_id))
        retry = {"jobId": "job_retry", "targetSessionId": target,
                 "creatorSessionId": creator_session_id, "status": "running"}
        jobs._save(retry)
        return retry

    monkeypatch.setattr(jobs, "start", fake_start)
    result = jobs.retry("job_finished")
    assert result["retryOf"] == "job_finished"
    assert calls == [("ses_target", ["python", "job.py"], str(tmp_path),
                      None, "ses_creator")]


def test_concurrent_read_modify_write_preserves_updates(tmp_path):
    _session(tmp_path)
    jobs._save({"jobId": "job_race", "targetSessionId": "ses_target", "status": "running", "createdAt": 1})
    def update(i):
        return jobs.runner_update("job_race", **{f"marker{i}": i})
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(update, range(20)))
    result = jobs.get("job_race")
    assert {result[f"marker{i}"] for i in range(20)} == set(range(20))


def test_cross_process_read_modify_write_preserves_updates(tmp_path, monkeypatch):
    _session(tmp_path)
    registry_root = tmp_path / "background_jobs"
    # _root intentionally lets PAN_BACKGROUND_JOBS_DIR override DEFAULT_ROOT.
    # Pin the parent to the same isolated registry the child processes inherit,
    # regardless of the shell or CI runner environment.
    monkeypatch.setenv("PAN_BACKGROUND_JOBS_DIR", str(registry_root))
    jobs._save({"jobId": "job_process_race", "targetSessionId": "ses_target",
                "status": "running", "createdAt": 1})
    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo),
           "PAN_BACKGROUND_JOBS_DIR": str(registry_root)}
    code = (
        "import sys; from packages.core import background_jobs as j; "
        "j.runner_update(sys.argv[1], **{sys.argv[2]: int(sys.argv[2][6:])})"
    )
    def update(i):
        return subprocess.run([sys.executable, "-c", code, "job_process_race", f"marker{i}"],
                              cwd=str(repo), env=env, check=False, capture_output=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(update, range(12)))
    process_results = [
        {
            "marker": f"marker{index}",
            "returncode": result.returncode,
            "stdout": result.stdout.decode(errors="replace"),
            "stderr": result.stderr.decode(errors="replace"),
        }
        for index, result in enumerate(results)
    ]
    assert all(not result["returncode"] for result in process_results), process_results
    result = jobs.get("job_process_race")
    assert {result[f"marker{i}"] for i in range(12)} == set(range(12))


def test_windows_named_mutex_declares_pointer_sized_win32_signatures(monkeypatch, tmp_path):
    from ctypes import wintypes

    class FakeWin32Function:
        def __init__(self, result):
            self.result = result
            self.argtypes = None
            self.restype = None
            self.calls = []

        def __call__(self, *args):
            self.calls.append(args)
            return self.result

    api = type("Kernel32", (), {})()
    api.CreateMutexW = FakeWin32Function(0x1234)
    api.WaitForSingleObject = FakeWin32Function(0)
    api.ReleaseMutex = FakeWin32Function(1)
    api.CloseHandle = FakeWin32Function(1)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: api, raising=False)

    with jobs._windows_named_mutex(tmp_path / "mutex.lock", "Local\\PanTest_"):
        pass

    assert api.CreateMutexW.argtypes == (
        wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    assert api.CreateMutexW.restype is wintypes.HANDLE
    assert api.WaitForSingleObject.argtypes == (
        wintypes.HANDLE, wintypes.DWORD)
    assert api.ReleaseMutex.argtypes == (wintypes.HANDLE,)
    assert api.CloseHandle.argtypes == (wintypes.HANDLE,)
    assert api.ReleaseMutex.calls == [(0x1234,)]
    assert api.CloseHandle.calls == [(0x1234,)]


def test_missing_creation_time_never_owns_pid(monkeypatch):
    class FakeProcess:
        pid = 123
        def create_time(self):
            return 100.0
    fake_psutil = type("Psutil", (), {"Process": lambda self, pid: FakeProcess()})()
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    assert jobs._owns_process({"pid": 123}) is None


def test_reconcile_keeps_job_when_runner_identity_is_alive(monkeypatch, tmp_path):
    _session(tmp_path)
    jobs._save({"jobId": "job_alive", "targetSessionId": "ses_target", "status": "running",
                "runnerPid": 11, "runnerProcessCreatedAt": 2, "createdAt": 1})
    monkeypatch.setattr(jobs, "_owns_process", lambda value: object())
    assert jobs.reconcile_running() == 0
    assert jobs.get("job_alive")["status"] == "running"
