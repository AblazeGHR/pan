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


# R5: canonical effects replace the old Job writer in all live fixtures.
from packages.jobs.independent_service import ServiceError, ServiceLock
from packages.jobs.independent_executor import ExecutionHost, ExecutionStore, ExecutionConflict, effect_id
from packages.actions.typed_executor import EffectReceipt
from tests.test_job_final_independent_execution import attempt, key, process_plan


def _service(root, launch=None):
    store = JobStore(str(root))
    if launch is not None:
        store._execution_host = ExecutionHost(ExecutionStore(root), launch=launch)
    store.recover()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.store = store
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, IndependentJobClient(f"http://127.0.0.1:{server.server_port}"), store


def _stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    assert not thread.is_alive()


def _request(root, argv=None):
    plan = process_plan(root)
    if argv is not None: plan["argv"] = argv
    return {"jobId": "job_original", "attempt": attempt(), "effectKey": key(), "frozenPlan": plan}


def _wait(client, identity, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        doc = client.get_execution(identity)
        if doc["result"] is not None: return doc
        time.sleep(.03)
    raise AssertionError("canonical execution did not settle")


def _historical(root, *, status="running", birth="unverified"):
    # Explicit historical bytes, never call a runtime legacy creator.
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root / "jobs.sqlite3") as db:
        db.executescript("""
            CREATE TABLE jobs(id TEXT PRIMARY KEY,payload TEXT,status TEXT,created REAL,updated REAL,
                runner_pid INTEGER,runner_filetime TEXT,exit_code INTEGER,log_path TEXT,
                notification_state TEXT,cancel_requested INTEGER DEFAULT 0,execution_audit TEXT);
            CREATE TABLE outbox(event_id TEXT PRIMARY KEY,job_id TEXT,payload TEXT,acked INTEGER DEFAULT 0,created REAL);
        """)
        payload = {"argv": ["historical"], "cwd": str(root), "label": None,
                   "name": "Historical name " + "x" * 250, "targetSessionId": "s", "creatorSessionId": None}
        identity = "job_" + "a" * 24
        db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (identity, json.dumps(payload), status, 1, 1, 123, birth, None, "historical.log", "pending", 0, "{}"))
        event = {"eventId": identity + ":terminal", "jobId": identity, "status": status}
        db.execute("INSERT INTO outbox VALUES(?,?,?,?,?)", (event["eventId"], identity, json.dumps(event), 0, 1))
    return identity


def test_system_action_admission_sql_failure_and_unknown_cleanup(tmp_path, monkeypatch):
    launches = []
    server, thread, client, container = _service(tmp_path, lambda *args: launches.append(args))
    try:
        store = container.execution_host.store
        with store.connect() as db:
            db.execute("CREATE TRIGGER reject_admission BEFORE INSERT ON executions BEGIN SELECT RAISE(ABORT,'failure'); END")
        with pytest.raises(RuntimeError, match="500"): client.submit_execution(_request(tmp_path))
        assert launches == [] and store.events()["events"] == []
        with store.connect() as db: db.execute("DROP TRIGGER reject_admission")
        doc = client.submit_execution(_request(tmp_path))
        assert len(launches) == 1
        store.recover(lambda _: False)
        unknown = client.get_execution(doc["effectId"])
        assert unknown["result"]["outcome"] == "unknown"
        assert unknown["result"]["cleanupProof"]["confirmed"] is False
        assert client.submit_execution(_request(tmp_path))["result"] == unknown["result"]
        assert len(launches) == 1 and not (tmp_path / "jobs.sqlite3").exists()
    finally: _stop(server, thread)


def test_concurrent_create_is_idempotent_and_conflict_is_rejected(tmp_path):
    launches = []
    server, thread, client, _ = _service(tmp_path, lambda *args: launches.append(args))
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=5) as pool:
            docs = list(pool.map(lambda _: client.submit_execution(_request(tmp_path)), range(5)))
        assert len({doc["effectId"] for doc in docs}) == 1 and len(launches) == 1
        changed = _request(tmp_path, [sys.executable, "-c", "print('changed')"])
        with pytest.raises(RuntimeError, match="409"): client.submit_execution(changed)
        assert len(launches) == 1
    finally: _stop(server, thread)


def test_immediate_completion_and_outbox_ack_are_durable_and_idempotent(tmp_path):
    server, thread, client, _ = _service(tmp_path)
    try:
        doc = client.submit_execution(_request(tmp_path))
        final = _wait(client, doc["effectId"])
        assert final["result"]["outcome"] == "succeeded"
        assert final["result"]["cleanupProof"]["ownedTree"]["confirmed"] is True
        event, = client.execution_events()["events"]
        assert event["attempt"] == attempt() and event["result"] == final["result"]
        for _ in range(2): assert client.ack_execution(event["eventId"])["ok"] is True
        assert client.execution_events()["events"] == []
        assert ExecutionStore(tmp_path).get(doc["effectId"])["result"] == final["result"]
    finally: _stop(server, thread)


def test_malformed_body_is_rejected_with_static_error(tmp_path):
    server, thread, client, _ = _service(tmp_path)
    try:
        from urllib.request import Request, urlopen
        for route in ("/jobs", "/executions"):
            req = Request(client.endpoint + route, data=b"{", method="POST", headers={"Content-Type": "application/json"})
            with pytest.raises(Exception) as caught: urlopen(req, timeout=2)
            assert caught.value.code == 400
            assert json.loads(caught.value.read()) == {"error": "malformed JSON"}
    finally: _stop(server, thread)


def test_cancel_with_wrong_runner_identity_does_not_touch_job(tmp_path):
    identity = _historical(tmp_path)
    server, thread, client, store = _service(tmp_path, lambda *args: None)
    try:
        with store.connect() as db: db.execute("UPDATE jobs SET status='running'")
        with pytest.raises(RuntimeError, match="409"): client.cancel(identity)
        with store.connect() as db:
            assert db.execute("SELECT cancel_requested FROM jobs").fetchone()[0] == 0
    finally: _stop(server, thread)


def test_cancel_proves_and_terminates_the_owned_process_tree(tmp_path):
    # Canonical cancellation is a fenced pre-effect window; active cleanup is
    # separately exercised by ProcessOwner and delete-safety contract tests.
    server, thread, client, store = _service(tmp_path, lambda *args: None)
    try:
        doc = client.submit_execution(_request(tmp_path))
        before = client.get_execution(doc["effectId"])
        with pytest.raises(RuntimeError, match="409"):
            client.cancel_execution(doc["effectId"], attempt(leaseId="stale"))
        assert client.get_execution(doc["effectId"]) == before
        cancelled = client.cancel_execution(doc["effectId"], attempt())
        assert cancelled["result"]["cleanupProof"] == {"confirmed": True, "effectStarted": False}
        assert cancelled["phase"] == "cancelled"
        with pytest.raises(ExecutionConflict):
            store.execution_host.store.claim(doc["effectId"], attempt(), {"pid": 7, "birth": "birth"})
    finally: _stop(server, thread)


def test_runner_environment_removes_pan_variables_and_pythonpath(tmp_path, monkeypatch):
    monkeypatch.setenv("PAN_PRIVATE_TEST_MARKER", "should-not-leak")
    monkeypatch.setenv("PYTHONPATH", "contaminated-path")
    server, thread, client, _ = _service(tmp_path)
    try:
        marker = tmp_path / "environment.json"
        code = "import json,os; json.dump({'pan':any(k.upper().startswith('PAN_') for k in os.environ),'pythonpath':os.environ.get('PYTHONPATH')},open(" + repr(str(marker)) + ",'w'))"
        doc = client.submit_execution(_request(tmp_path, [sys.executable, "-c", code]))
        assert _wait(client, doc["effectId"])["result"]["outcome"] == "succeeded"
        assert json.loads(marker.read_text()) == {"pan": False, "pythonpath": None}
    finally: _stop(server, thread)


def test_service_restart_keeps_live_runner_and_does_not_repeat_execution(tmp_path):
    server, thread, client, store = _service(tmp_path)
    marker = tmp_path / "once"
    code = "import pathlib,time; p=pathlib.Path(" + repr(str(marker)) + "); p.write_text(p.read_text()+'x' if p.exists() else 'x'); time.sleep(1.5)"
    try:
        doc = client.submit_execution(_request(tmp_path, [sys.executable, "-c", code]))
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline: time.sleep(.02)
        assert marker.exists()
        original = client.get_execution(doc["effectId"])
    finally: _stop(server, thread)
    server, thread, client, _ = _service(tmp_path)
    try:
        recovered = client.get_execution(doc["effectId"])
        assert recovered["supervisorIdentity"] == original["supervisorIdentity"]
        assert _wait(client, doc["effectId"])["result"]["outcome"] == "succeeded"
        assert marker.read_text() == "x"
    finally: _stop(server, thread)


@pytest.mark.skipif(os.name != "nt", reason="taskkill /T is a Windows-only lifecycle check")
def test_killing_pan_like_sibling_host_tree_does_not_kill_independent_service_task(tmp_path, monkeypatch):
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
    from packages.jobs import independent_client
    original_urlopen = independent_client.urlopen
    requests = []
    def observed_urlopen(request, **kwargs):
        started = time.monotonic()
        entry = {"method": request.method, "url": request.full_url, "timeout": kwargs.get('timeout')}
        try:
            return original_urlopen(request, **kwargs)
        except Exception as exc:
            entry['error'] = repr(exc)
            raise
        finally:
            entry['elapsed'] = time.monotonic() - started
            requests.append(entry)
            (tmp_path / 'http-requests.json').write_text(json.dumps(requests, indent=2), encoding='utf-8')
    monkeypatch.setattr(independent_client, 'urlopen', observed_urlopen)
    service_log = (tmp_path / 'independent-service.log').open('w', encoding='utf-8')
    service = subprocess.Popen(
        [sys.executable, "-m", "packages.jobs.independent_service", "--root", str(root), "--port", str(port)],
        cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=service_log, stderr=service_log,
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
        try:
            job = client.submit_execution(_request(tmp_path, [sys.executable, "-c", code]))
        except Exception as exc:
            # Preserve one POST and its original timeout. A lost response may
            # already have admitted a Job; diagnostics must never retry it.
            state = {"serviceReturncode": service.poll(), "requests": requests}
            database = root / 'executions.sqlite3'
            if database.exists():
                with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
                    state['durableExecutions'] = db.execute('SELECT effect_id,document FROM executions').fetchall()
            service_log.flush()
            exc.add_note(json.dumps(state) + '\n' + (tmp_path / 'independent-service.log').read_text(encoding='utf-8', errors='replace')[-8000:])
            raise
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
        assert _wait(client, job["effectId"], timeout=8)["result"]["outcome"] == "succeeded"
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
        service_log.close()



@pytest.mark.parametrize("status", ["UNKNOWN", "failed", "cancelled", "completed", "running"])
def test_unknown_jobs_are_not_retryable(tmp_path, status):
    identity = _historical(tmp_path, status=status)
    store = JobStore(str(tmp_path))
    before = (tmp_path / "jobs.sqlite3").read_bytes()
    with pytest.raises(ServiceError) as caught: store.retry(identity)
    assert caught.value.status == 410
    assert (tmp_path / "jobs.sqlite3").read_bytes() == before
    assert store.get(identity)["status"] == status


@pytest.mark.parametrize("name", [None, "", "   ", "x" * 201, "valid historical name"])
def test_new_process_requires_name_without_spawning(tmp_path, monkeypatch, name):
    store = JobStore(str(tmp_path))
    with pytest.raises(ServiceError) as caught: store.create({"name": name})
    assert caught.value.status == 410
    assert not (tmp_path / "jobs.sqlite3").exists()


def test_name_is_trimmed_label_optional_and_legacy_read_unchanged(tmp_path):
    identity = _historical(tmp_path, status="completed")
    store = JobStore(str(tmp_path))
    before = (tmp_path / "jobs.sqlite3").read_bytes()
    doc = store.get(identity)
    assert doc["name"] == "Historical name " + "x" * 250 and doc["label"] is None
    assert doc["readOnly"] is True and doc["legacy"] is True
    assert (tmp_path / "jobs.sqlite3").read_bytes() == before
    event, = store.events()["events"]
    assert store.ack(event["eventId"])["ok"] is True
    assert store.ack(event["eventId"])["ok"] is True
    assert store.events()["events"] == []


def test_retired_http_writes_and_client_helpers_never_create_jobs(tmp_path):
    from urllib.request import Request, urlopen
    server, thread, client, _ = _service(tmp_path)
    try:
        for route in ("/jobs", "/jobs/job_" + "a" * 24 + "/retry"):
            req = Request(client.endpoint + route, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
            with pytest.raises(Exception) as caught: urlopen(req, timeout=2)
            assert caught.value.code == 410
        for call in (lambda: client.start({}), lambda: client.retry("original")):
            with pytest.raises(RuntimeError, match="410"): call()
        assert client.list() == {"jobs": []} and client.events() == {"events": []}
        assert not (tmp_path / "jobs.sqlite3").exists()
    finally: _stop(server, thread)


def test_service_lock_is_exclusive_and_releasable(tmp_path):
    first, second = ServiceLock(tmp_path), ServiceLock(tmp_path)
    first.acquire()
    try:
        with pytest.raises((OSError, RuntimeError)): second.acquire()
        assert first.file is not None
    finally: first.release()
    second.acquire()
    second.release()
    assert second.file is None
