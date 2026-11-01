import asyncio

import pytest

from packages.core import config
from packages.jobs import pan_bridge as bridge
from tests.test_job_final_http_service import h as canonical_h  # noqa: F401


def test_disabled_does_not_import_or_start_service(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: {})
    assert bridge.configured_client() is None
    assert bridge.request("start", {}) is None


@pytest.mark.parametrize("endpoint", ["http://example.com:9001", "http://localhost:9001",
    "http://127.0.0.1", "http://127.0.0.1:9001/jobs", "http://x@127.0.0.1:9001",
    "http://127.0.0.1:bad", "http://[broken", None])
def test_endpoint_fail_closed(monkeypatch, endpoint):
    monkeypatch.setattr(config, "load_config", lambda: {
        "independent_jobs": {"enabled": True, "endpoint": endpoint}})
    with pytest.raises(bridge.JobServiceUnavailable):
        bridge.configured_client()


def test_outage_is_static_and_no_execution_fallback(monkeypatch):
    class Client:
        def start(self, payload):
            raise RuntimeError("SECRET_SENTINEL")
    monkeypatch.setattr(bridge, "configured_client", Client)
    with pytest.raises(bridge.JobServiceUnavailable) as error:
        bridge.request("start", {})
    assert "SECRET" not in str(error.value)


def test_outbox_enqueues_before_ack_and_replays_same_identity(monkeypatch):
    calls = []
    event = {"eventId": "job_1:terminal", "jobId": "job_1", "targetSessionId": "ses_1",
             "status": "completed", "exitCode": 0, "error": "SECRET_SENTINEL"}
    class Client:
        def events(self):
            return {"events": [event]}
        def ack(self, event_id):
            calls.append(("ack", event_id))
    monkeypatch.setattr(bridge, "configured_client", Client)
    async def enqueue(target, text, **kwargs):
        assert "SECRET" not in text
        calls.append(("enqueue", kwargs["event_id"]))
        return {"ok": True}
    for _ in range(2):
        assert asyncio.run(bridge.recover_events(enqueue)) == 1
    assert calls == [("enqueue", "job_1:terminal"), ("ack", "job_1:terminal")] * 2


def test_missing_session_does_not_ack(monkeypatch):
    class Client:
        def events(self):
            return {"events": [{"eventId": "job_1:terminal", "jobId": "job_1",
                                "targetSessionId": "deleted", "status": "failed"}]}
        def ack(self, event_id):
            pytest.fail("must retain undelivered event")
    monkeypatch.setattr(bridge, "configured_client", Client)
    async def enqueue(*args, **kwargs):
        return {"ok": False}
    assert asyncio.run(bridge.recover_events(enqueue)) == 0


def test_enqueue_survives_reload_and_ack_failure_does_not_duplicate(monkeypatch, tmp_path):
    from packages.core import session as sessions, worker
    monkeypatch.setattr(sessions, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(worker, "_shutdown_started", False)
    monkeypatch.setattr(worker, "_wake_worker", lambda *a, **k: asyncio.sleep(0))
    target = sessions.create("independent-outbox-test", workdir=str(tmp_path))
    event = {"eventId": "job_owned:terminal", "jobId": "job_owned",
             "targetSessionId": target.id, "status": "completed", "exitCode": 0}
    class Client:
        failures = 1
        acknowledgements = 0
        def events(self):
            return {"events": [event]}
        def ack(self, event_id):
            if self.failures:
                self.failures -= 1
                raise OSError("simulated acknowledgement loss")
            self.acknowledgements += 1
    client = Client()
    monkeypatch.setattr(bridge, "configured_client", lambda: client)
    with pytest.raises(bridge.JobServiceUnavailable):
        asyncio.run(bridge.recover_events(worker.enqueue_notice))
    # Reload durable Session facts, not the old in-memory queue object.
    sessions._cache.pop(target.id, None)
    recovered = sessions.get(target.id)
    assert len([row for row in recovered.queue_pending if row.get("eventId") == event["eventId"]]) == 1
    assert asyncio.run(bridge.recover_events(worker.enqueue_notice)) == 1
    assert client.acknowledgements == 1
    assert len([row for row in recovered.queue_pending if row.get("eventId") == event["eventId"]]) == 1


def test_core_routes_only_new_generic_jobs_and_never_reconciles_remote(monkeypatch, tmp_path):
    # E15: the remote port owns executions, while the canonical Job/Run owner
    # retains admission and retry policy. It must never create a second Job.
    import copy
    from packages.jobs.independent_executor import ExecutionHost, ExecutionStore, ExecutionConflict
    from packages.jobs.independent_service import JobStore
    from tests.test_job_final_independent_execution import attempt, key, process_plan
    def forbidden(*args, **kwargs):
        raise AssertionError("canonical execution port called retired Job lifecycle")
    monkeypatch.setattr(JobStore, 'create', forbidden)
    monkeypatch.setattr(JobStore, 'retry', forbidden)
    monkeypatch.setattr(bridge, 'request', forbidden)
    store = ExecutionStore(tmp_path / 'executions')
    launches = []
    host = ExecutionHost(store, launch=lambda *args: launches.append(args))
    request = {'jobId':'job_canonical', 'attempt':attempt(), 'effectKey':key(),
               'frozenPlan':process_plan(tmp_path)}
    original = host.submit_execution(request)
    assert host.submit_execution(request) == original and len(launches) == 1
    cold = ExecutionHost(ExecutionStore(store.root), launch=forbidden)
    assert cold.get_execution(original['effectId']) == original
    # Read-only cold lookup cannot infer a result or launch a replacement.
    before = copy.deepcopy(original)
    assert before['result'] is None and before['jobId'] == 'job_canonical'
    with pytest.raises(ExecutionConflict):
        cold.cancel_execution(original['effectId'], attempt(leaseId='foreign'))
    assert cold.get_execution(original['effectId']) == before
    cancelled = host.cancel_execution(original['effectId'], attempt())
    assert cancelled['result']['receipt']['code'] == 'cancelled_before_effect'
    assert cancelled['result']['cleanupProof'] == {'confirmed':True, 'effectStarted':False}
    assert len(launches) == 1
    # An explicitly authorized new Attempt retains the original Run/effect;
    # the execution port does not invent policy, a Run, or a Job identity.
    with pytest.raises(ExecutionConflict):
        host.submit_execution({**request, 'retry':True,
            'attempt':attempt(attemptId='cancelled_retry', generation=2)})
    assert host.get_execution(original['effectId']) == cancelled
    from packages.actions.typed_executor import EffectReceipt
    failed_key = {**key(), 'targetId':'known-failure'}
    failed_request = {**request, 'effectKey':failed_key}
    failed = host.submit_execution(failed_request)
    claimed = store.claim(failed['effectId'], attempt(), {'pid':7,'birth':'trusted-port-birth'})
    failure = EffectReceipt(failed_key, 'rejected', {'exitCode':7,
        'processIdentity':{'pid':8,'birth':'trusted-process-birth'}}, {'confirmed':True}).as_dict()
    store.finish(failed['effectId'], attempt(), claimed['claim'], failure)
    retry = {**failed_request, 'retry':True, 'attempt':attempt(attemptId='attempt_retry', generation=2)}
    retried = host.submit_execution(retry)
    assert len(launches) == 3
    assert retried['effectId'] == failed['effectId']
    assert retried['jobId'] == original['jobId'] and retried['effectKey'] == failed_key
    assert retried['attempt']['runId'] == original['attempt']['runId']
    assert retried['history'][0]['result'] == failure
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM executions').fetchone()[0] == 2
        assert db.execute("SELECT name FROM sqlite_master WHERE name='jobs'").fetchone() is None
    assert not list((tmp_path / 'local' / 'jobs').glob('*.json'))


def test_malformed_remote_list_has_static_error(monkeypatch, tmp_path):
    from packages.core import background_jobs as jobs
    monkeypatch.setenv("PAN_BACKGROUND_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(bridge, "request", lambda *a: {"jobs": [None]})
    with pytest.raises(bridge.JobServiceUnavailable, match="invalid independent Job service response"):
        jobs.list_jobs()


def test_unified_gui_reads_remote_and_refuses_local_metadata_mutation(canonical_h):
    # E15 removes the second remote Job facade. The GUI queries the canonical
    # Job/Run; metadata CAS may rename that Job without mutating its receipt.
    import copy
    from packages.jobs.final_service import definition
    from tests.test_job_final_http_service import enabled, launch
    h = canonical_h
    job = enabled(h)
    rid = launch(h, job)['runId']
    asyncio.run(h.service.dispatch_once())
    original = copy.deepcopy(h.service.final.get(job['id']))
    original_plans = copy.deepcopy(h.service.final.store.get(job['id'])['finalRuntime']['plans'])
    assert original['runs'][rid]['state'] == 'completed'
    assert len(original['attempts']) == len(original['effects']) == 1
    assert h.client.get('/api/job-runs/' + rid).json()['jobId'] == job['id']
    assert h.client.get('/api/jobs/' + job['id']).json()['id'] == job['id']
    cfg = definition('job', job)
    cfg['name'] = 'Canonical metadata only'
    result = h.client.put('/api/jobs/' + job['id'], json={'definition':cfg,
        'expectedRevision':job['configRevision'], 'requestKey':'rename-canonical'})
    assert result.status_code == 200, result.text
    changed = h.service.final.get(job['id'])
    for field in ('runs', 'attempts', 'effects'):
        assert changed[field] == original[field]
    assert h.service.final.store.get(job['id'])['finalRuntime']['plans'] == original_plans
    # A stale GUI mutation is rejected with no additional effect or Run.
    before = copy.deepcopy(changed)
    stale = h.client.put('/api/jobs/' + job['id'], json={'definition':cfg,
        'expectedRevision':job['configRevision'], 'requestKey':'stale-rename'})
    assert stale.status_code == 409
    assert h.service.final.get(job['id']) == before
    assert len(h.service.list('job', actor={'type':'user','id':'local'})['items']) == 1


def test_real_service_outbox_reaches_durable_pan_queue(monkeypatch, tmp_path):
    import json
    import sys
    import threading
    import time
    from http.server import ThreadingHTTPServer
    from packages.jobs.independent_service import Handler, JobStore
    from packages.jobs.independent_client import IndependentJobClient
    from packages.jobs import api
    from packages.core import session as sessions, worker
    monkeypatch.setattr(sessions, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(worker, "_shutdown_started", False)
    monkeypatch.setattr(worker, "_wake_worker", lambda *a, **k: asyncio.sleep(0))
    target = sessions.create("independent-real-outbox", workdir=str(tmp_path))
    # R5 only drains a pre-existing terminal event through this compatibility
    # bridge. Canonical execution events use their independent consumer port.
    from tests.test_independent_job_service import _historical
    identity = _historical(tmp_path / "host", status="completed")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.store = JobStore(str(tmp_path / "host"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = IndependentJobClient(f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(bridge, "configured_client", lambda: client)
    try:
        with pytest.raises(RuntimeError, match="410"):
            client.start({"argv": [sys.executable], "cwd": str(tmp_path)})
        job = client.get(identity)
        with server.store.connect() as db:
            row = db.execute("SELECT payload FROM outbox").fetchone()
            event = {**json.loads(row["payload"]), "targetSessionId": target.id,
                     "creatorSessionId": None, "exitCode": 0, "logPath": job["logPath"]}
            db.execute("UPDATE outbox SET payload=?", (json.dumps(event),))
        assert job["executionOwner"] == "independent-job-service"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not client.events()["events"]:
            time.sleep(.03)
        assert client.events()["events"][0]["jobId"] == job["jobId"]
        assert asyncio.run(bridge.recover_events(worker.enqueue_notice)) == 1
        assert client.events() == {"events": []}
        sessions._cache.pop(target.id, None)
        rows = sessions.get(target.id).queue_pending
        assert len([row for row in rows if row.get("eventId") == job["jobId"] + ":terminal"]) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("headers,status", [
    ({"Origin": "http://untrusted.example", "Content-Type": "application/json"}, 403),
    ({"Host": "untrusted.example", "Content-Type": "application/json"}, 403),
    ({"Content-Type": "text/plain"}, 415),
])
def test_internal_http_rejects_browser_or_rebinding_before_create(tmp_path, headers, status):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from packages.jobs.independent_service import Handler, JobStore
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.store = JobStore(str(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}/jobs", data=b"{}", headers=headers)
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=2)
        assert error.value.code == status
        assert server.store.list() == {"jobs": []}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows owned-task runner claim test")
def test_duplicate_runner_claim_launches_command_only_once(monkeypatch, tmp_path):
    import sys
    import threading
    from packages.jobs import independent_service as service, independent_runner as runner
    store = service.JobStore(str(tmp_path / "host"))
    marker = tmp_path / "once.txt"
    from tests.helpers.legacy_process_fixture import historical_process
    job = historical_process(store.root, [sys.executable, "-c",
            "from pathlib import Path; p=Path(" + repr(str(marker)) + "); p.write_text('once')"],
            tmp_path, launch=False)
    results = []
    start = threading.Barrier(2)
    def invoke():
        start.wait(timeout=3)
        results.append(runner.run(str(store.root), job["jobId"]))
    callers = [threading.Thread(target=invoke) for _ in range(2)]
    for thread in callers:
        thread.start()
    for thread in callers:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert sorted(results) == [0, 3]
    assert marker.read_text() == "once"
    assert store.get(job["jobId"])["status"] == "completed"
    assert len(store.events()["events"]) == 1


@pytest.mark.parametrize("headers,status", [
    ({"Origin": "http://untrusted.example", "Content-Type": "application/json"}, 403),
    ({"Host": "untrusted.example", "Content-Type": "application/json"}, 403),
    ({"Content-Type": "text/plain"}, 415),
])
def test_rejected_post_consumes_framed_body_without_parsing_or_creating(headers, status):
    import io
    from packages.jobs.independent_service import Handler

    class Connection:
        timeout = None
        changes = []
        def gettimeout(self):
            return self.timeout
        def settimeout(self, value):
            self.timeout = value
            self.changes.append(value)

    class Server:
        server_port = 12345
        @property
        def store(self):
            raise AssertionError("rejected request must never access JobStore")

    handler = Handler.__new__(Handler)
    handler.server = Server()
    handler.headers = {"Host": "127.0.0.1:12345", "Content-Length": "2", **headers}
    handler.connection = Connection()
    handler.rfile = io.BytesIO(b"{}")
    handler.path = "/jobs"
    responses = []
    handler._json = lambda code, value: responses.append((code, value, handler.rfile.tell()))
    handler._body = lambda: pytest.fail("reject before JSON parsing")
    handler._run("POST")
    assert len(responses) == 1
    assert responses[0][0] == status
    assert responses[0][2] == 2
    assert handler.connection.timeout is None


@pytest.mark.parametrize("length", [None, "invalid", "-1", "0", "65537"])
def test_rejected_body_drain_refuses_unbounded_or_invalid_framing(length):
    from packages.jobs.independent_service import Handler
    handler = Handler.__new__(Handler)
    handler.headers = {} if length is None else {"Content-Length": length}
    # No socket or reader: these lengths must be rejected without any I/O.
    handler._discard_rejected_body()


def test_rejected_body_drain_total_budget_and_timeout_propagation(monkeypatch):
    from types import SimpleNamespace
    from packages.jobs import independent_service as service

    ticks = iter([0, .2, .6, 1.1])
    monkeypatch.setattr(service, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    changes = []
    reads = []
    handler = service.Handler.__new__(service.Handler)
    handler.headers = {"Content-Length": "10"}
    handler.connection = SimpleNamespace(gettimeout=lambda: 7,
                                         settimeout=changes.append)
    handler.rfile = SimpleNamespace(read1=lambda count: reads.append(count) or b"x")
    with pytest.raises(TimeoutError, match="drain timed out"):
        handler._discard_rejected_body()
    assert reads == [10, 9]
    assert changes == [.8, .4, 7]
