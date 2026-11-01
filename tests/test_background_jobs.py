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
from tests.test_job_automatic_recovery import env  # noqa: F401
from tests.test_job_process_logs import live as canonical_live  # noqa: F401

from packages.core import background_jobs as jobs
from packages.core import session as sess
from packages.core import worker


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    # Other scheduler fixtures bind a strict broadcaster globally. Keep both
    # callback paths owned by this fixture, including terminal retry tests.
    monkeypatch.setattr(jobs, "_scheduled_task_hooks", {})
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


def test_accepted_cancel_wins_runner_exit_race(tmp_path):
    _cancel_original_before_effect(tmp_path, claimed=True, cold=False)


def test_runner_finishes_accepted_cancel_if_canceller_disappears(tmp_path):
    _cancel_original_before_effect(tmp_path, claimed=True, cold=True)


def test_cancel_survives_runner_disappearing_during_tree_lookup(canonical_live, monkeypatch):
    from tests.test_job_process_logs import test_failed_and_cancelled_real_output_survives_and_no_rerun
    # The retained native owner replaces the old runner/PID tree walk. The
    # canonical cancel must work without that retired, racy lookup boundary.
    monkeypatch.setattr(jobs, '_owns_process', lambda *a, **k: pytest.fail('legacy PID lookup'))
    monkeypatch.setattr(jobs, '_kill_tree', lambda *a, **k: pytest.fail('legacy tree walk'))
    test_failed_and_cancelled_real_output_survives_and_no_rerun(canonical_live, 0)


def test_tree_cleanup_continues_after_one_descendant_disappears():
    import psutil

    parent, survivor, disappeared = Mock(), Mock(), Mock()
    parent.children.return_value = [survivor, disappeared]
    disappeared.kill.side_effect = psutil.NoSuchProcess(123)
    jobs._kill_tree(parent)
    survivor.kill.assert_called_once_with()
    parent.kill.assert_called_once_with()


def test_start_persists_metadata_and_detaches(monkeypatch, tmp_path):
    _session(tmp_path)
    _session(tmp_path, "ses_creator")
    proc = Mock(pid=4321)
    monkeypatch.setattr(jobs.subprocess, "Popen", Mock(return_value=proc))
    monkeypatch.setattr(jobs, "_process_create_time", lambda pid: 12.5)
    result = jobs.start(
        "ses_target", ["python", "train.py", "--epochs", "2"], str(tmp_path),
        creator_session_id="ses_creator",
        name="Validate background jobs",
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
    from packages.actions.typed_executor import TypedExecutor
    from packages.jobs.final_contracts import ContractError
    calls = []
    class ForbiddenOwner:
        def spawn(self, *args, **kwargs):
            calls.append((args, kwargs))
            pytest.fail('invalid argv/cwd must never reach the native owner')
    receipts = {}
    executor = TypedExecutor(process_owner=ForbiddenOwner(), receipt_store=receipts)
    key = {'runId': 'original', 'stepId': 'process', 'targetId': 'process', 'operationOrdinal': 0}
    with pytest.raises(ContractError) as caught:
        asyncio.run(executor._process(key, {'operation': 'process',
            'arguments': {'argv': argv, 'cwd': cwd}}))
    assert caught.value.code == 'invalid_schema' and caught.value.path in {'argv', 'cwd'}
    assert calls == [] and receipts == {}


def test_external_cwd_runs_and_projects_one_completion_notice(tmp_path, monkeypatch):
    from packages.core import background_runner

    project = tmp_path / "project"
    external = tmp_path / "external 中文 worktree"
    project.mkdir()
    external.mkdir()
    monkeypatch.setattr(jobs, "PROJECT_ROOT", project)
    _session(tmp_path)
    # Run the production Runner entrypoint locally against an isolated registry;
    # its command subprocess is real, while no deployed service is touched.
    monkeypatch.setattr(jobs, "_spawn_background_runner", lambda job, **kwargs: job)
    job = jobs.start("ses_target", [sys.executable, "-c",
        "import os,json; print('JOB_EXTERNAL_CWD_OK'); print(json.dumps(os.getcwd()))"], str(external), name="Validate background jobs")
    monkeypatch.setattr(sys, "argv", ["background_runner", "--job-id", job["jobId"]])
    assert background_runner.main() == 0
    result = jobs.get(job["jobId"])
    assert result["cwd"] == str(external.resolve())
    assert result["status"] == "completed" and result["exitCode"] == 0
    log = Path(result["logPath"]).read_bytes().decode(errors="replace")
    assert "JOB_EXTERNAL_CWD_OK" in log
    assert json.dumps(str(external.resolve())) in log
    notices = []

    async def notify(target, text, **kwargs):
        notices.append((target, json.loads(text), kwargs))
        return {"ok": True}

    monkeypatch.setattr(worker, "enqueue_notice", notify)
    assert asyncio.run(jobs.recover_notifications()) == 1
    assert asyncio.run(jobs.recover_notifications()) == 0
    assert len(notices) == 1 and notices[0][0] == "ses_target"
    assert notices[0][1]["exitCode"] == 0
    assert notices[0][2]["event_id"] == f"{job['jobId']}:terminal"


def test_external_cwd_shared_by_shell_validation(tmp_path, monkeypatch):
    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()
    monkeypatch.setattr(jobs, "PROJECT_ROOT", project)
    command, cwd = jobs.validate_shell_action("echo JOB_CWD", str(external))
    assert command == "echo JOB_CWD" and cwd == external.resolve()
    for invalid in (str(external / "missing"), str(project / "file"), "", None):
        (project / "file").touch()
        with pytest.raises(ValueError):
            jobs.validate_shell_action("echo JOB_CWD", invalid)


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


def test_list_handles_mixed_legacy_timestamps_without_rewriting(tmp_path):
    values = {"job_missing": None, "job_number": 10, "job_string": "20",
              "job_iso": "2026-10-04T12:00:00Z", "job_bad": "invalid",
              "job_bool": True, "job_list": [], "job_inf": float('inf')}
    for job_id, value in values.items():
        record = {"jobId": job_id, "status": "completed"}
        if job_id != "job_missing":
            record["createdAt"] = value
        jobs._save(record)
    paths = list((tmp_path / "background_jobs/jobs").glob('job_*.json'))
    before = {path: path.read_bytes() for path in paths}
    result = jobs.list_jobs()
    assert [job['jobId'] for job in result[:3]] == ['job_iso', 'job_string', 'job_number']
    assert {job['jobId'] for job in result} == set(values)
    assert {path: path.read_bytes() for path in paths} == before


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


def test_delete_active_job_requires_confirmed_process_cleanup(monkeypatch, tmp_path):
    _session(tmp_path)
    jobs._save({"jobId": "job_delete_active", "targetSessionId": "ses_target",
                "status": "running", "pid": 10, "processCreatedAt": 1,
                "runnerPid": 11, "runnerProcessCreatedAt": 2, "createdAt": 1})
    calls = []
    monkeypatch.setattr(jobs, "_terminate_job_processes",
                        lambda records: calls.append(records) or {"confirmed": True})
    assert jobs.delete_job("job_delete_active") is True
    assert len(calls) == 1
    assert jobs.get("job_delete_active") is None


def test_delete_active_job_unknown_receipt_is_fail_closed_and_idempotent(monkeypatch, tmp_path):
    _session(tmp_path)
    jobs._save({"jobId": "job_delete_unknown", "targetSessionId": "ses_target",
                "status": "starting", "pid": 10, "processCreatedAt": 1,
                "createdAt": 1})
    monkeypatch.setattr(jobs, "_terminate_job_processes",
                        lambda records: {"confirmed": False, "reason": "live owned PID"})
    with pytest.raises(ValueError, match="live owned PID"):
        jobs.delete_job("job_delete_unknown")
    retained = jobs.get("job_delete_unknown")
    assert retained["status"] == "cleanup_pending"
    assert "live owned PID" in retained["cleanupError"]
    with pytest.raises(ValueError, match="live owned PID"):
        jobs.delete_job("job_delete_unknown")


def test_delete_terminal_job_keeps_legacy_delete_semantics(tmp_path):
    _session(tmp_path)
    jobs._save({"jobId": "job_delete_done", "status": "completed", "createdAt": 1})
    assert jobs.delete_job("job_delete_done") is True
    assert jobs.get("job_delete_done") is None


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


def test_retry_rejects_running_job(env):
    import copy
    from tests.test_job_automatic_recovery import job_definition, create, drive_until
    h = env
    gate = h.tmp / 'release-retry-negative'
    cfg = job_definition(h, {'automatic': False}, code=(
        "import time;from pathlib import Path\n"
        f"while not Path({str(gate)!r}).exists(): time.sleep(.02)"))
    jid, rid = create(h, cfg)
    try:
        asyncio.run(h.service.dispatch_once())
        before = copy.deepcopy(h.service.final.store.get(jid))
        state = before['finalRuntime']
        assert state['runs'][rid]['state'] == 'running' and len(state['attempts']) == 1
        response = h.client.post('/api/job-runs/' + rid + '/operations', json={'operation': 'retry',
            'expectedStateRevision': state['runs'][rid]['stateRevision'], 'requestKey': 'running-retry'})
        assert response.status_code == 409 and response.json()['error']['code'] == 'not_retryable'
        assert h.service.final.store.get(jid) == before
    finally:
        gate.write_text('release')
        asyncio.run(drive_until(h, jid, rid, 'completed'))


def test_retry_inherits_creator_and_target(env):
    import copy
    from tests.test_job_automatic_recovery import job_definition, create, drive_until
    h = env
    target = _session(h.tmp)
    sess.save(target)
    grants = h.auth._default_permissions()
    grants['job'].update({k: True for k in grants['job']})
    grants['action'].update({k: True for k in grants['action']})
    h.auth.change_permissions(grants, 0)
    creator = {'type': 'agent', 'id': 'ses_creator', 'revision': 1}
    counter = h.tmp / 'retry-counter'
    code = ("from pathlib import Path; p=Path(" + repr(str(counter)) + "); "
        "n=int(p.read_text()) if p.exists() else 0;p.write_text(str(n+1));raise SystemExit(7 if n==0 else 0)")
    cfg = job_definition(h, {'automatic': False}, code=code)
    cfg['notification'] = {'on': ['failed', 'completed'], 'recipients': [
        {'recipientId': 'target', 'channelId': 'session', 'targetRef': target.id}]}
    jid, rid = create(h, cfg, actor=creator)
    asyncio.run(drive_until(h, jid, rid, 'failed'))
    before = h.service.final.get(jid)
    frozen = copy.deepcopy(h.service.final.store.get(jid)['finalRuntime']['plans'][rid]['frozen'])
    response = h.client.post('/api/job-runs/' + rid + '/operations', json={'operation': 'retry',
        'expectedStateRevision': before['runs'][rid]['stateRevision'], 'requestKey': 'original-retry'})
    assert response.status_code == 202, response.text
    asyncio.run(drive_until(h, jid, rid, 'completed'))
    after = h.service.final.get(jid)
    assert set(after['runs']) == {rid} and len(after['attempts']) == len(after['effects']) == 2
    assert after['effects'][0]['effectKey'] == after['effects'][1]['effectKey']
    assert after['effects'][0]['attemptId'] != after['effects'][1]['attemptId']
    assert counter.read_text() == '2'
    assert h.service.final.store.get(jid)['finalRuntime']['plans'][rid]['frozen'] == frozen
    assert frozen['authority'] == creator
    rows = [r for r in h.service.final.history(jid)['outbox'] if r['channel'] == 'session']
    assert len(rows) == 2 and all(r['targetRef'] == target.id and r['summary']['creatorSessionId'] == 'ses_creator' for r in rows)


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


def _fake_kernel32(monkeypatch, *, create_result=0x1234, wait_result=0,
                   release_result=1, close_result=1, last_error=0):
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
    api.CreateMutexW = FakeWin32Function(create_result)
    api.WaitForSingleObject = FakeWin32Function(wait_result)
    api.ReleaseMutex = FakeWin32Function(release_result)
    api.CloseHandle = FakeWin32Function(close_result)
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_args, **_kwargs: api, raising=False)
    monkeypatch.setattr(
        ctypes, "get_last_error", lambda: last_error, raising=False)
    return api


@pytest.mark.parametrize("wait_result", [0, 0x80], ids=["acquired", "abandoned"])
def test_windows_named_mutex_declares_pointer_sized_win32_signatures(
        monkeypatch, tmp_path, wait_result):
    from ctypes import wintypes

    api = _fake_kernel32(monkeypatch, wait_result=wait_result)

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


def test_windows_named_mutex_create_failure_raises_winerror_without_closing(
        monkeypatch, tmp_path):
    api = _fake_kernel32(monkeypatch, create_result=0, last_error=5)

    with pytest.raises(OSError) as error:
        with jobs._windows_named_mutex(tmp_path / "mutex.lock", "Local\\PanTest_"):
            pytest.fail("the lock body must not run")

    assert error.value.winerror == 5
    assert api.WaitForSingleObject.calls == []
    assert api.CloseHandle.calls == []


@pytest.mark.parametrize(
    ("wait_result", "last_error", "expected_winerror"),
    [(0xFFFFFFFF, 6, 6), (0x1234, 0, None)],
    ids=["wait-failed", "unknown-result"],
)
def test_windows_named_mutex_wait_failure_closes_handle(
        monkeypatch, tmp_path, wait_result, last_error, expected_winerror):
    api = _fake_kernel32(
        monkeypatch, wait_result=wait_result, last_error=last_error)

    with pytest.raises(OSError) as error:
        with jobs._windows_named_mutex(tmp_path / "mutex.lock", "Local\\PanTest_"):
            pytest.fail("the lock body must not run")

    if expected_winerror is not None:
        assert error.value.winerror == expected_winerror
    else:
        assert str(wait_result) in str(error.value)
    assert api.CloseHandle.calls == [(0x1234,)]
    assert api.ReleaseMutex.calls == []


def test_windows_named_mutex_release_failure_still_closes_handle(
        monkeypatch, tmp_path):
    api = _fake_kernel32(monkeypatch, release_result=0, last_error=5)

    with pytest.raises(OSError) as error:
        with jobs._windows_named_mutex(tmp_path / "mutex.lock", "Local\\PanTest_"):
            pass

    assert error.value.winerror == 5
    assert api.ReleaseMutex.calls == [(0x1234,)]
    assert api.CloseHandle.calls == [(0x1234,)]


def test_windows_named_mutex_close_failure_raises_winerror(monkeypatch, tmp_path):
    api = _fake_kernel32(monkeypatch, close_result=0, last_error=6)

    with pytest.raises(OSError) as error:
        with jobs._windows_named_mutex(tmp_path / "mutex.lock", "Local\\PanTest_"):
            pass

    assert error.value.winerror == 6
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


def test_runner_terminal_broadcasts_job_updated_once(monkeypatch, tmp_path):
    """独立 runner 落盘终态后，恢复循环首次观测即补发一次 job.updated。"""
    _session(tmp_path)
    events = []
    monkeypatch.setitem(jobs._scheduled_task_hooks, "on_event", lambda event: events.append(event))
    monkeypatch.setattr(jobs, "_spawn_background_runner", lambda job, **kwargs: job)
    job = jobs.start("ses_target", [sys.executable, "-c", "print('ok')"], str(tmp_path), name="Validate background jobs")
    monkeypatch.setattr(sys, "argv", ["background_runner", "--job-id", job["jobId"]])
    from packages.core import background_runner
    assert background_runner.main() == 0
    assert jobs.get(job["jobId"])["status"] == "completed"
    assert events[0]["type"] == "job.created"
    assert any(event["job"]["status"] == "running" for event in events)
    events.clear()  # Observe only terminal recovery below, as in a detached runner.

    async def notify(target, text, **kwargs):
        return {"ok": True}

    monkeypatch.setattr(worker, "enqueue_notice", notify)
    assert asyncio.run(jobs.recover_notifications()) == 1
    assert len(events) == 1
    assert events[0]["type"] == "job.updated"
    assert events[0]["jobId"] == job["jobId"]
    assert events[0]["job"]["status"] == "completed"
    assert events[0]["job"]["exitCode"] == 0
    assert (jobs.get(job["jobId"])["terminalEventEmittedFor"]
            == f"{job['jobId']}:terminal")
    # 幂等：第二次恢复循环不得重复广播同一终态。
    assert asyncio.run(jobs.recover_notifications()) == 0
    assert len(events) == 1


def test_terminal_broadcast_retries_after_channel_failure(monkeypatch, tmp_path):
    """成功发布后才持久化标记：广播通道故障保持可重试，下一 tick 恢复即收敛。

    job.updated 是状态 upsert，客户端对重复事件幂等，故绝不以牺牲送达换取
    严格一次：首发失败不标记（否则一次失败会永久卡住旧徽标），通道恢复后
    补发一次并标记，随后不再重复。
    """
    _session(tmp_path)

    def raising_hook(event):
        raise RuntimeError("broadcast down")

    monkeypatch.setitem(jobs._scheduled_task_hooks, "on_event", raising_hook)
    monkeypatch.setattr(jobs, "_spawn_background_runner", lambda job, **kwargs: job)
    job = jobs.start("ses_target", [sys.executable, "-c", "print('ok')"], str(tmp_path), name="Validate background jobs")
    monkeypatch.setattr(sys, "argv", ["background_runner", "--job-id", job["jobId"]])
    from packages.core import background_runner
    assert background_runner.main() == 0

    async def notify(target, text, **kwargs):
        return {"ok": True}

    monkeypatch.setattr(worker, "enqueue_notice", notify)
    # 首发失败：恢复循环不得崩溃，且不得持久化标记（保持可重试）。
    asyncio.run(jobs.recover_notifications())
    assert jobs.get(job["jobId"]).get("terminalEventEmittedFor") is None

    events = []
    monkeypatch.setitem(jobs._scheduled_task_hooks, "on_event",
                        lambda event: events.append(event))
    # 通道恢复后的下一 tick：补发一次并标记。
    asyncio.run(jobs.recover_notifications())
    assert [event["type"] for event in events] == ["job.updated"]
    assert events[0]["jobId"] == job["jobId"]
    assert (jobs.get(job["jobId"])["terminalEventEmittedFor"]
            == f"{job['jobId']}:terminal")
    # 之后不再重复（成功路径无每 tick 重复）。
    asyncio.run(jobs.recover_notifications())
    assert len(events) == 1


def test_terminal_broadcast_skips_scheduled_children(monkeypatch, tmp_path):
    """scheduled 子进程作业不在 /api/jobs 列表内：不得经 upsert 进入 GUI store。"""
    _session(tmp_path)
    events = []
    monkeypatch.setitem(jobs._scheduled_task_hooks, "on_event", lambda event: events.append(event))

    async def notify(target, text, **kwargs):
        return {"ok": True}

    monkeypatch.setattr(worker, "enqueue_notice", notify)
    job = jobs.start("ses_target", [sys.executable, "-c", "print('ok')"], str(tmp_path), name="Validate background jobs")
    jobs.runner_update(job["jobId"], status="completed", exitCode=0,
                       scheduledParentJobId="sch_parent")
    assert events[0]["type"] == "job.created"
    events.clear()  # Child identity is established before the recovery observation.
    asyncio.run(jobs.recover_notifications())
    assert events == []
    assert jobs.get(job["jobId"]).get("terminalEventEmittedFor") is None


def test_independent_terminal_broadcast_uses_local_ledger_and_retries(monkeypatch, tmp_path):
    """独立 Job 服务路径：远程终态补发 + 本地 ledger 幂等 + 失败可重试。

    远程 runner 只写服务自己的注册表（进程所有权不变）；标记只落在本地
    ledger（绝不写服务注册表）。scheduled 子任务被 /api/jobs 过滤且由父
    任务 finalize 广播，非终态同理跳过。
    """
    from packages.jobs import pan_bridge

    events = []

    def failing_hook(event):
        raise RuntimeError("broadcast down")

    monkeypatch.setitem(jobs._scheduled_task_hooks, "on_event", failing_hook)
    remote_payload = {"jobs": [
        {"jobId": "job_remote_done", "kind": "background-process",
         "status": "completed", "name": "Remote done", "updatedAt": 1},
        {"jobId": "job_remote_child", "kind": "background-process",
         "status": "completed", "scheduledParentJobId": "sch_parent",
         "name": "Remote child", "updatedAt": 1},
        {"jobId": "job_remote_live", "kind": "background-process",
         "status": "running", "name": "Remote live", "updatedAt": 1},
    ]}

    class Client:
        def list(self):
            return remote_payload
        def events(self):
            return {"events": []}
        def ack(self, event_id):
            return {}

    monkeypatch.setattr(pan_bridge, "configured_client", Client)

    async def notify(target, text, **kwargs):
        return {"ok": True}

    monkeypatch.setattr(worker, "enqueue_notice", notify)
    # 首发失败：无广播、无 ledger（保持可重试）。
    asyncio.run(jobs.recover_notifications())
    assert events == []
    assert jobs._load_terminal_broadcast_ledger() == {}

    events.clear()
    monkeypatch.setitem(jobs._scheduled_task_hooks, "on_event",
                        lambda event: events.append(event))
    asyncio.run(jobs.recover_notifications())
    # 仅补发远程终态的非子任务；scheduled 子任务与非终态跳过。
    assert [event["jobId"] for event in events] == ["job_remote_done"]
    assert events[0]["job"]["status"] == "completed"
    ledger = jobs._load_terminal_broadcast_ledger()
    assert ledger["job_remote_done"] == "completed:job_remote_done:terminal"
    assert "job_remote_child" not in ledger
    assert "job_remote_live" not in ledger
    # 之后不再重复（成功路径无每 tick 重复）。
    events.clear()
    asyncio.run(jobs.recover_notifications())
    assert events == []
    # 服务 retention 删除的作业不再出现在远程列表：ledger 项当轮清掉，
    # 文件不随终态作业数无限增长；也不得因此重新广播。
    remote_payload["jobs"] = [item for item in remote_payload["jobs"]
                              if item["jobId"] != "job_remote_done"]
    asyncio.run(jobs.recover_notifications())
    assert events == []
    assert jobs._load_terminal_broadcast_ledger() == {}

@pytest.mark.parametrize('terminal', ['completed', 'failed', 'cancelled'])
def test_reconcile_preserves_concurrent_runner_terminal(monkeypatch, tmp_path, terminal):
    _session(tmp_path)
    jobs._save({'jobId': 'job_reconcile_race', 'targetSessionId': 'ses_target',
                'status': 'running', 'runnerPid': 11, 'runnerProcessCreatedAt': 2, 'createdAt': 1})
    def disappears(_):
        jobs._update('job_reconcile_race', {'status': terminal, 'exitCode': 0})
        return None
    monkeypatch.setattr(jobs, '_owns_process', disappears)
    assert jobs.reconcile_running() == 0
    assert jobs.get('job_reconcile_race')['status'] == terminal

@pytest.mark.skipif(os.name != 'nt', reason='Windows hidden runner identity')
def test_hidden_runner_claim_is_once_and_refuses_unknown(tmp_path):
    from packages.jobs.independent_executor import ExecutionStore, ExecutionConflict
    from packages.actions.typed_executor import EffectReceipt
    from tests.test_job_final_independent_execution import admit, attempt, key, process_plan
    store = ExecutionStore(tmp_path / 'canonical-executions')
    doc, _ = admit(store, process_plan(tmp_path))
    supervisor = {'pid': 7, 'birth': 'original-owned-fixture-birth'}
    claimed = store.claim(doc['effectId'], attempt(), supervisor)
    assert claimed['supervisorIdentity'] == supervisor and claimed['claim']
    with pytest.raises(ExecutionConflict):
        store.claim(doc['effectId'], attempt(), supervisor)
    assert store.get(doc['effectId']) == claimed
    unknown = EffectReceipt(key(), 'unknown', {'code': 'original_result_unavailable'}).as_dict()
    settled = store.finish(doc['effectId'], attempt(), claimed['claim'], unknown)
    with pytest.raises(ExecutionConflict):
        store.claim(doc['effectId'], attempt(), supervisor)
    with pytest.raises(ExecutionConflict):
        admit(store, process_plan(tmp_path), attempt(attemptId='new-attempt', generation=2), retry=True)
    assert store.get(doc['effectId']) == settled


@pytest.mark.skipif(os.name != 'nt', reason='Windows hidden runner identity')
def test_hidden_runner_claim_finishes_already_accepted_cancel(tmp_path):
    _cancel_original_before_effect(tmp_path, claimed=False, cold=True)


@pytest.mark.parametrize("kind", ["single", "broadcast"])
def test_agent_message_job_registry_commits_publish_full_lifecycle(monkeypatch, tmp_path, kind):
    _session(tmp_path)
    _session(tmp_path, "ses_other")
    events = []
    monkeypatch.setitem(jobs._scheduled_task_hooks, "on_event", events.append)
    schedule = {"type": "interval", "intervalSeconds": 3600}
    if kind == "single":
        job = jobs.start_message("ses_target", "context", schedule)
    else:
        job = jobs.start_broadcast(["ses_target", "ses_other"], "context", schedule)
    assert events[-1]["type"] == "job.created"
    assert events[-1]["job"]["target"]["sessionId"] == "ses_target"
    original = events[-1]["job"]
    jobs.update_job_field(job["jobId"], {"target": {"sessionId": "ses_other"}})
    assert events[-1]["job"]["target"]["sessionId"] == "ses_other"
    assert original["target"]["sessionId"] == "ses_target", "freeze prior snapshots"
    async def send(*args, **kwargs):
        assert events[-1]["job"]["status"] == "running", "publish claim before delivery"
        return {"status": "queued"}
    monkeypatch.setattr(worker, "send_session", send)
    asyncio.run(jobs.run_due_message_jobs(now=jobs.time.time() + 7200))
    assert any(e["job"]["status"] == "running" for e in events)
    assert events[-1]["job"]["status"] == "scheduled"
    jobs.cancel(job["jobId"])
    assert events[-1]["job"]["status"] == "cancelled"
    assert all(isinstance(e["job"]["source"], dict) and "target" in e["job"] for e in events)


def test_failed_job_commit_does_not_publish_live_state(monkeypatch, tmp_path):
    events = []
    monkeypatch.setitem(jobs._scheduled_task_hooks, "on_event", events.append)
    def fail(*args, **kwargs):
        raise OSError("owned injected persistence failure")
    monkeypatch.setattr(jobs, "_atomic_write", fail)
    with pytest.raises(OSError):
        jobs._write_job(tmp_path / "job_owned.json", {"jobId": "job_owned"})
    assert events == []


@pytest.mark.parametrize("name", [None, "", "   ", "x" * 201])
def test_process_creation_requires_explicit_bounded_name(env, monkeypatch, name):
    from tests.test_job_automatic_recovery import job_definition
    h = env
    value = job_definition(h, {'automatic': False})
    value['name'] = name  # explicit null/blank/overlong is distinct from omission
    before = h.client.get('/api/jobs').json()
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('invalid creation must not spawn'))
    response = h.client.post('/api/jobs', json={'definition': value,
        'expectedRevision': 0, 'requestKey': 'invalid-explicit-name'})
    assert response.status_code == 400 and response.json()['error']['code'] == 'invalid_schema'
    assert response.json()['error']['path'] in {'name', 'definition.name'}
    assert h.client.get('/api/jobs').json() == before


def _cancel_original_before_effect(tmp_path, *, claimed, cold):
    from packages.jobs.independent_executor import ExecutionStore, ExecutionConflict
    from packages.jobs.independent_execution_runner import execute_claimed
    from packages.actions.typed_executor import EffectReceipt
    from tests.test_job_final_independent_execution import admit, attempt, key, process_plan
    root = tmp_path / 'canonical-executions'
    store = ExecutionStore(root)
    original, _ = admit(store, process_plan(tmp_path))
    if claimed:
        original = store.claim(original['effectId'], attempt(), {'pid': 7, 'birth': 'owned-fixture-birth'})
    cancelled = store.cancel(original['effectId'], attempt())
    assert cancelled['phase'] == 'cancelled'
    assert cancelled['result']['receipt']['code'] == 'cancelled_before_effect'
    assert cancelled['result']['cleanupProof'] == {'confirmed': True, 'effectStarted': False}
    if cold:
        store = ExecutionStore(root)
    if claimed:
        class Forbidden:
            async def execute(self, **kwargs):
                pytest.fail('accepted cancellation must prevent execution after cold reopen')
        assert asyncio.run(execute_claimed(store, original, executor=Forbidden())) == cancelled
        # A competing late runner result cannot overwrite the original receipt.
        with pytest.raises(ExecutionConflict):
            store.finish(original['effectId'], attempt(), original['claim'],
                EffectReceipt(key(), 'succeeded', {'exitCode': 0}).as_dict())
    else:
        with pytest.raises(ExecutionConflict):
            store.claim(original['effectId'], attempt(), {'pid': 7, 'birth': 'owned-fixture-birth'})
    assert store.get(original['effectId']) == cancelled
    events = store.events()['events']
    assert len(events) == 1 and events[0]['result'] == cancelled['result']
    assert events[0]['attempt'] == attempt() and events[0]['effectId'] == original['effectId']
    # Once execution has crossed its fence, cancellation alone proves nothing.
    other, _ = store.admit(job_id='other', attempt=attempt(runId='other'),
        effect_key={**key(), 'runId': 'other'}, frozen_plan=process_plan(tmp_path))
    other = store.claim(other['effectId'], other['attempt'], {'pid': 8, 'birth': 'other-fixture-birth'})
    other = store.phase(other['effectId'], other['attempt'], other['claim'], 'executing')
    with pytest.raises(ExecutionConflict):
        store.cancel(other['effectId'], other['attempt'])
    assert store.get(other['effectId']) == other and other['result'] is None
