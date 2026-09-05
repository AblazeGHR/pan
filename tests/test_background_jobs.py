import asyncio
import json
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
    proc = Mock(pid=4321)
    monkeypatch.setattr(jobs.subprocess, "Popen", Mock(return_value=proc))
    monkeypatch.setattr(jobs, "_process_create_time", lambda pid: 12.5)
    result = jobs.start("ses_target", ["python", "train.py", "--epochs", "2"], str(tmp_path))
    assert result["status"] == "running"
    assert result["pid"] == 4321
    assert result["processCreatedAt"] == 12.5
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
        "logPath": "x", "createdAt": 1,
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


def test_runner_registry_survives_reload(tmp_path):
    _session(tmp_path)
    jobs._save({"jobId": "job_reload", "targetSessionId": "ses_target", "status": "running", "createdAt": 1})
    assert jobs.list_jobs()[0]["jobId"] == "job_reload"
    assert json.loads((tmp_path / "background_jobs" / "jobs" / "job_reload.json").read_text())["status"] == "running"


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
    result = jobs.cancel("job_reuse")
    assert result["status"] == "cancelled"
