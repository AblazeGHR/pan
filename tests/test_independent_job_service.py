from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from packages.jobs.independent_service import Handler, JobStore
from packages.jobs.independent_client import IndependentJobClient


def test_owned_job_exit_accounting_settles_without_assuming_empty(monkeypatch):
    from packages.jobs import independent_runner as runner

    class Guard:
        calls = 0
        def active_processes(self):
            self.calls += 1
            return 1 if self.calls < 3 else 0
        def member_pids(self):
            return []

    guard = Guard()
    assert runner._owned_job_empty(guard, timeout=.1) is True
    assert guard.calls == 3


@pytest.mark.parametrize("active,members", [(1, [123]), (0, [123]), (None, []), (0, None)])
def test_owned_job_empty_requires_both_proofs(active, members):
    from packages.jobs import independent_runner as runner
    class Guard:
        def active_processes(self): return active
        def member_pids(self): return members
    assert runner._owned_job_empty(Guard(), timeout=.02) is False


def test_owned_job_query_failure_does_not_confirm_empty():
    from packages.jobs import independent_runner as runner
    class Guard:
        def active_processes(self): raise OSError("query failed")
    assert runner._owned_job_empty(Guard()) is False


def _service(root):
    store = JobStore(str(root))
    store.recover()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    httpd.store = store
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    client = IndependentJobClient(f"http://127.0.0.1:{httpd.server_port}")
    return httpd, thread, client, store


def _stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def _request(argv, **more):
    return {"name": "Validate independent lifecycle", "argv": argv, "cwd": os.getcwd(), "targetSessionId": "session_test", **more}


def _wait(client, job_id, terminal=("completed", "failed", "cancelled", "UNKNOWN"), timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = client.get(job_id)
        if job["status"] in terminal:
            return job
        time.sleep(.03)
    raise AssertionError(f"job {job_id} did not become terminal")


def test_concurrent_create_is_idempotent_and_conflict_is_rejected(tmp_path):
    server, thread, client, _ = _service(tmp_path)
    try:
        payload = _request([sys.executable, "-c", "import time; time.sleep(.2)"], idempotencyKey="same")
        results = []
        workers = [threading.Thread(target=lambda: results.append(client.start(payload))) for _ in range(5)]
        for w in workers: w.start()
        for w in workers: w.join()
        assert len({j["jobId"] for j in results}) == 1
        changed = {**payload, "argv": [sys.executable, "-c", "print('different')"]}
        with pytest.raises(RuntimeError, match="409"):
            client.start(changed)
    finally:
        _stop(server, thread)


def test_immediate_completion_and_outbox_ack_are_durable_and_idempotent(tmp_path):
    server, thread, client, _ = _service(tmp_path)
    try:
        job = client.start(_request([sys.executable, "-c", "pass"]))
        terminal = _wait(client, job["jobId"])
        assert terminal["status"] == "completed"
        events = client.events()["events"]
        assert len(events) == 1
        event = events[0]
        assert event["eventId"] == job["jobId"] + ":terminal"
        assert event["targetSessionId"] == "session_test"
        assert client.ack(event["eventId"]) == {"ok": True, "eventId": event["eventId"]}
        assert client.ack(event["eventId"]) == {"ok": True, "eventId": event["eventId"]}
        assert client.events()["events"] == []
    finally:
        _stop(server, thread)


def test_malformed_body_is_rejected_with_static_error(tmp_path):
    server, thread, client, _ = _service(tmp_path)
    try:
        from urllib.request import Request, urlopen
        req = Request(client.endpoint + "/jobs", data=b"{", method="POST", headers={"Content-Type": "application/json"})
        with pytest.raises(Exception) as exc:
            urlopen(req, timeout=2)
        assert getattr(exc.value, "code", None) == 400
        assert json.loads(exc.value.read()) == {"error": "malformed JSON"}
    finally:
        _stop(server, thread)


def test_cancel_with_wrong_runner_identity_does_not_touch_job(tmp_path):
    server, thread, client, store = _service(tmp_path)
    try:
        job = client.start(_request([sys.executable, "-c", "import time; time.sleep(1.5)"]))
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            job = client.get(job["jobId"])
            if job["runnerPid"] and job["runnerFiletime"]: break
            time.sleep(.03)
        assert job["runnerPid"] and job["runnerFiletime"]
        with store.connect() as db:
            db.execute("UPDATE jobs SET runner_filetime='wrong-identity' WHERE id=?", (job["jobId"],))
        with pytest.raises(RuntimeError, match="409"):
            client.cancel(job["jobId"])
        with store.connect() as db:
            assert db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job["jobId"],)).fetchone()[0] == 0
        with store.connect() as db:
            db.execute("UPDATE jobs SET runner_filetime=? WHERE id=?", (job["runnerFiletime"], job["jobId"]))
        assert _wait(client, job["jobId"])["status"] == "completed"
    finally:
        _stop(server, thread)


def test_cancel_proves_and_terminates_the_owned_process_tree(tmp_path):
    server, thread, client, _ = _service(tmp_path)
    try:
        child = "import time; time.sleep(30)"
        parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); print('ready',flush=True); time.sleep(30)"
        job = client.start(_request([sys.executable, "-c", parent]))
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            current = client.get(job["jobId"])
            if current["status"] == "running" and os.path.isfile(current["logPath"]):
                if os.path.getsize(current["logPath"]) > 0: break
            time.sleep(.03)
        else: raise AssertionError("owned parent and child did not start")
        client.cancel(job["jobId"])
        cancelled = _wait(client, job["jobId"], timeout=8)
        assert cancelled["status"] == "cancelled"
        assert any(e["eventId"] == job["jobId"] + ":terminal" and e["status"] == "cancelled"
                   for e in client.events()["events"])
    finally:
        _stop(server, thread)


def test_runner_environment_removes_pan_variables_and_pythonpath(tmp_path, monkeypatch):
    monkeypatch.setenv("PAN_PRIVATE_TEST_MARKER", "should-not-leak")
    monkeypatch.setenv("PYTHONPATH", "contaminated-path")
    server, thread, client, _ = _service(tmp_path)
    try:
        marker = tmp_path / "environment.json"
        code = "import json,os,sys; json.dump({'pan':any(k.upper().startswith('PAN_') for k in os.environ),'pythonpath':os.environ.get('PYTHONPATH'),'path':sys.path},open(r'" + str(marker) + "','w'))"
        job = client.start(_request([sys.executable, "-c", code]))
        assert _wait(client, job["jobId"])["status"] == "completed"
        value = json.loads(marker.read_text())
        assert value["pan"] is False
        assert value["pythonpath"] is None
    finally:
        _stop(server, thread)


def test_service_restart_keeps_live_runner_and_does_not_repeat_execution(tmp_path):
    server, thread, client, store = _service(tmp_path)
    marker = tmp_path / "ran-once"
    try:
        code = "import pathlib,time; pathlib.Path(r'" + str(marker) + "').write_text('once'); time.sleep(1.2)"
        job = client.start(_request([sys.executable, "-c", code]))
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and not job["runnerPid"]:
            time.sleep(.03); job = client.get(job["jobId"])
        pid = job["runnerPid"]
        assert pid
        _stop(server, thread)
        server2, thread2, client2, _ = _service(tmp_path)
        try:
            recovered = client2.get(job["jobId"])
            assert recovered["runnerPid"] == pid
            assert recovered["status"] in ("running", "completed")
            assert _wait(client2, job["jobId"])["status"] == "completed"
            assert marker.read_text() == "once"
        finally:
            _stop(server2, thread2)
    except BaseException:
        # The first server may already be stopped; close is safe in normal completion.
        raise


@pytest.mark.skipif(os.name != "nt", reason="taskkill /T is a Windows-only lifecycle check")
def test_killing_pan_like_sibling_host_tree_does_not_kill_independent_service_task(tmp_path):
    """A prestarted service and its task are siblings of the simulated Pan host."""
    from urllib.request import urlopen
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = tmp_path / "independent-root"
    root.mkdir()
    repo = os.path.dirname(os.path.dirname(__file__))
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAN_") and k.upper() != "PYTHONPATH"}
    env["PYTHONPATH"] = repo
    service = subprocess.Popen(
        [sys.executable, "-m", "packages.jobs.independent_service", "--root", str(root), "--port", str(port)],
        cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    client = IndependentJobClient(f"http://127.0.0.1:{port}", timeout=1)
    host = None
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                if client.health()["ok"]: break
            except Exception: time.sleep(.05)
        else: raise AssertionError("independent service did not become ready")
        marker = tmp_path / "survived-host-tree-kill"
        code = "import pathlib,time; time.sleep(1.5); pathlib.Path(r'" + str(marker) + "').write_text('done')"
        job = client.start(_request([sys.executable, "-c", code]))
        # This is a separate simulated Pan process. It has no service/task children.
        host = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        assert host.poll() is None
        killed = subprocess.run(["taskkill", "/PID", str(host.pid), "/T", "/F"],
                                capture_output=True, timeout=8, check=False)
        host.wait(timeout=3)
        assert host.returncode is not None
        host = None
        assert client.health()["ok"] is True
        assert _wait(client, job["jobId"], timeout=8)["status"] == "completed"
        assert marker.read_text() == "done"
    finally:
        if host is not None and host.poll() is None:
            subprocess.run(["taskkill", "/PID", str(host.pid), "/T", "/F"], capture_output=True, timeout=8)
        if service.poll() is None:
            service.terminate()
            try: service.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Terminate only the explicitly spawned service PID.
                service.kill(); service.wait(timeout=5)


def test_unknown_jobs_are_not_retryable(tmp_path):
    server, thread, client, store = _service(tmp_path)
    try:
        job = client.start(_request([sys.executable, "-c", "import time; time.sleep(2)"]))
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            job = client.get(job["jobId"])
            if job["status"] == "running": break
            time.sleep(.02)
        assert job["status"] == "running"
        with store.connect() as db:
            db.execute("UPDATE jobs SET status='UNKNOWN' WHERE id=?", (job["jobId"],))
        with pytest.raises(RuntimeError, match="409"):
            client.retry(job["jobId"])
        assert len(client.list()["jobs"]) == 1
        with store.connect() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job["jobId"],))
        assert _wait(client, job["jobId"])["status"] == "completed"
    finally:
        _stop(server, thread)


@pytest.mark.parametrize("name", [None, "", "   ", "x" * 201])
def test_new_process_requires_name_without_spawning(tmp_path, monkeypatch, name):
    from packages.jobs.independent_service import ServiceError
    store = JobStore(str(tmp_path / "owned-name"))
    monkeypatch.setattr("packages.jobs.independent_service.subprocess.Popen", lambda *a, **k: pytest.fail("must not spawn"))
    with pytest.raises(ServiceError, match="name"):
        store.create(_request([sys.executable], name=name))


def test_name_is_trimmed_label_optional_and_legacy_read_unchanged(tmp_path, monkeypatch):
    store = JobStore(str(tmp_path / "owned-name"))
    monkeypatch.setattr("packages.jobs.independent_service.subprocess.Popen", lambda *a, **k: None)
    job = store.create(_request([sys.executable], name="  Validate logs  "))
    assert job["name"] == "Validate logs" and job["label"] is None
    with store.connect() as db:
        row = db.execute("SELECT payload FROM jobs WHERE id=?", (job["jobId"],)).fetchone()
        payload = json.loads(row["payload"])
        payload["name"] = "Historical name " + "x" * 250
        db.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload), job["jobId"]))
    assert store.get(job["jobId"])["name"] == payload["name"]
