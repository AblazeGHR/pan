"""Authenticated product HTTP -> canonical admission -> real worker queue.

Owned temporary Sessions are paused to prove durable enqueue without launching
supplier CLIs. Unknown transport responses are explicitly injected and never
treated as consumption or permission to resend.
"""
import asyncio
import copy
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from packages.automation.service import Service as Automation
from packages.core import session as sessions, worker
from packages.jobs import final_api as api, final_contracts as fc
from packages.jobs.final_service import FinalService, bind, definition
from packages.actions.typed_executor import WorkerAssignProvider
from tests.test_job_final_http_service import create, enabled, launch, control


@pytest.fixture
def h(tmp_path, monkeypatch):
    app = FastAPI()
    automation = Automation(tmp_path / "automation", SimpleNamespace(workers={}), None)
    automation.auth = api.CanonicalUserAuth(tmp_path / "automation")
    service = bind(app, automation, tmp_path / "canonical")
    app.state.automation = automation
    from packages.jobs.action_api import router as action_router
    from packages.jobs.api import router as facade_router
    api.install(app, action_router, facade_router)
    assert isinstance(service.executor.assign_provider, WorkerAssignProvider)
    monkeypatch.setattr(worker, "_broadcast", None)
    monkeypatch.setattr(worker, "find_alive_worker_by_session", lambda _: None)
    async def no_wake(*args, **kwargs):
        return None
    monkeypatch.setattr(worker, "_wake_worker", no_wake)
    async def no_spawn(*args, **kwargs):
        raise AssertionError("owned paused/missing targets must not spawn")
    monkeypatch.setattr(worker, "create_worker", no_spawn)
    owned = {}
    for sid in ("ses_a", "ses_b", "ses_source", "ses_new"):
        s = sessions.Session(id=sid, name=sid, workdir=str(tmp_path), queue_paused=True)
        sessions._cache[sid] = s
        sessions.save(s)
        owned[sid] = s
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    assert port not in {8767, 8768}
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(.01)
    assert server.started
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False)
    token = automation.auth.login(automation.auth.key())
    client.headers["Authorization"] = "Bearer " + token
    yield SimpleNamespace(client=client, service=service, automation=automation,
                          root=tmp_path / "canonical", owned=owned, app=app)
    client.close()
    server.should_exit = True
    thread.join(5)
    assert not thread.is_alive()
    listener.close()
    asyncio.run(service.close())


def config(h, operation, **arguments):
    response = h.client.get("/api/jobs/actions")
    assert response.status_code == 200
    published = next(a for a in response.json()["items"] if a["id"] == "product_" + operation.replace("-", "_") + "_v2")
    assert published["inputSchema"]["properties"]["source"]["enum"] == sorted(worker.SOURCE_TYPES)
    ref = response.json()["references"]["product_" + operation.replace("-", "_") + "_v2"]
    return {"name": operation, "steps": [{"stepId": "send", "actionRef": ref,
        "arguments": {k: {"from": "literal", "value": v} for k, v in arguments.items()},
        "completion": {"contract": "durable-queue-accepted"}}], "retention": {"policyRef": "test"}}


@pytest.mark.parametrize("op", ["session-message", "assign", "session-broadcast"])
def test_product_http_real_queue_source_and_cold_original_query(h, op):
    targets = {"sessionIds": ["ses_b", "ses_a", "ses_b"]} if op == "session-broadcast" else {"sessionId": "ses_a"}
    args = {**targets, "text": "owned-body", "source": "agent", "sourceSessionId": "ses_source"}
    if op == "assign":
        args["taskId"] = "owned-task"
    job = enabled(h, config(h, op, **args))
    admitted = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    run = h.client.get("/api/job-runs/" + admitted["runId"]).json()
    assert run["state"] == "completed"
    row = h.owned["ses_a"].queue_pending[0]
    assert row["source"] == "agent" and row["sourceSessionId"] == "ses_source"
    assert row["text"] == "owned-body"
    if op == "assign":
        assert row["taskId"] == "owned-task" and row["taskIdSource"] == "assign"
    # Reopen Session file: real persistence, no supplier consumption asserted.
    sessions._cache.pop("ses_a")
    assert sessions.get("ses_a").queue_pending[0]["id"] == row["id"]
    history = h.service.final.history(job["id"])
    assert len(history["effects"]) == 1
    receipt = history["effects"][0]["receipt"]
    if op == "session-broadcast":
        children = receipt["receipt"]["targets"]
        assert [r["effectKey"]["targetId"] for r in children] == ["ses_b", "ses_a"]
        identity = fc.digest(children[1]["effectKey"])
    else:
        identity = fc.digest(receipt["effectKey"])
    cold = FinalService(h.root, h.automation.auth)
    try:
        matches = cold.final.query_original(identity)
        assert matches[0]["runs"][0]["runId"] == admitted["runId"]
        assert matches[0]["effects"] == history["effects"]
        worker_id = "owned-task" if op == "assign" else row["id"]
        assert cold.final.query_original(worker_id)[0]["runs"][0]["runId"] == admitted["runId"]
        asyncio.run(cold.dispatch_once())
        assert len(sessions.get("ses_a").queue_pending) == 1
    finally:
        asyncio.run(cold.close())


def test_product_missing_target_rejects_without_spawn(h):
    job = enabled(h, config(h, "assign", sessionId="ses_missing", text="body"))
    admitted = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    assert h.client.get("/api/job-runs/" + admitted["runId"]).json()["state"] == "failed"
    assert sessions.get("ses_missing") is None


@pytest.mark.parametrize("op", ["session-message", "assign", "session-broadcast"])
def test_lost_worker_ack_reconciles_original_queue_after_cold_reopen(h, monkeypatch, op):
    name = "assign" if op == "assign" else "send_session"
    original = getattr(worker, name)
    calls = []
    async def lost_ack(sid, text, **kwargs):
        calls.append(sid)
        await original(sid, text, **kwargs)
        return {"status": "unknown"}  # durable write succeeded; response lost
    monkeypatch.setattr(worker, name, lost_ack)
    targets = {"sessionIds": ["ses_b", "ses_a"]} if op == "session-broadcast" else {"sessionId": "ses_a"}
    job = enabled(h, config(h, op, **targets, text="lost acknowledgement"))
    admitted = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    assert h.service.get_run(admitted["runId"], actor={"type": "user", "id": "local"})["state"] == "uncertain"
    before = copy.deepcopy(h.service.final.history(job["id"])["effects"][0]["effectKey"])
    sessions._cache.clear()
    cold = FinalService(h.root, h.automation.auth)
    try:
        asyncio.run(cold.dispatch_once())
        assert cold.get_run(admitted["runId"], actor={"type": "user", "id": "local"})["state"] == "completed"
        effect = cold.final.history(job["id"])["effects"][0]
        assert effect["effectKey"] == before
        assert effect["receipt"]["outcome"] == "succeeded"
        assert len(calls) == (2 if op == "session-broadcast" else 1)
        assert len(sessions.get("ses_a").queue_pending) == 1
    finally:
        asyncio.run(cold.close())


def test_assign_connection_failure_cold_query_stays_unknown(h, monkeypatch):
    calls = []
    async def connection_lost(*args, **kwargs):
        calls.append(kwargs["task_id"])
        raise ConnectionError("owned transport lost")
    monkeypatch.setattr(worker, "assign", connection_lost)
    job = enabled(h, config(h, "assign", sessionId="ses_a", text="body", taskId="connection-task"))
    admitted = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    cold = FinalService(h.root, h.automation.auth)
    try:
        asyncio.run(cold.dispatch_once())
        matches = cold.final.query_original("connection-task")
        assert matches[0]["runs"][0]["state"] == "uncertain"
        assert cold.get_run(admitted["runId"], actor={"type": "user", "id": "local"})["state"] == "uncertain"
        assert calls == ["connection-task"] and not h.owned["ses_a"].queue_pending
    finally:
        asyncio.run(cold.close())


def test_product_fanout_partial_unknown_cold_reopen_never_resends(h, monkeypatch):
    original = worker.send_session
    calls = []
    async def transport(sid, text, **kwargs):
        calls.append(sid)
        if sid == "ses_b":
            return {"status": "unknown", "queueItemId": "original-unknown"}
        return await original(sid, text, **kwargs)
    monkeypatch.setattr(worker, "send_session", transport)
    job = enabled(h, config(h, "session-broadcast", sessionIds=["ses_a", "ses_b", "ses_missing", "ses_a"], text="body"))
    admitted = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    history = h.service.final.history(job["id"])
    receipt = history["effects"][0]["receipt"]
    assert [r["outcome"] for r in receipt["receipt"]["targets"]] == ["succeeded", "unknown", "rejected"]
    assert receipt["outcome"] == "partial"
    cold = FinalService(h.root, h.automation.auth)
    try:
        assert cold.final.query_original("original-unknown")[0]["runs"][0]["state"] == "uncertain"
        asyncio.run(cold.dispatch_once())
        assert cold.get_run(admitted["runId"], actor={"type": "user", "id": "local"})["state"] == "uncertain"
        assert calls == ["ses_a", "ses_b", "ses_missing"]
    finally:
        asyncio.run(cold.close())


def test_payload_source_cannot_authenticate_http_or_delegate_identity(h):
    value = config(h, "session-message", sessionId="ses_a", text="body", source="agent", sourceSessionId="ses_source")
    auth = h.client.headers.pop("Authorization")
    response = h.client.post("/api/jobs", json={"definition": value, "expectedRevision": 0, "requestKey": "spoof"})
    assert response.status_code == 401
    h.client.headers["Authorization"] = auth
    # Existing trusted IPC authenticates generic Agent capability, not a
    # Session identity. Even granted callers cannot claim user/Session source.
    from packages.automation.authorization import bind_objects
    runtime = SimpleNamespace()
    bind_objects(runtime, h.app)
    permissions = h.automation.auth._default_permissions()
    for kind in permissions.values():
        if isinstance(kind, dict):
            for capability in kind:
                kind[capability] = True
    h.automation.auth.change_permissions(permissions, h.automation.auth.permissions_status()["revision"])
    job = enabled(h, value)
    response = asyncio.run(runtime.authorization_objects("POST", "/api/jobs/" + job["id"] + "/runs", {
        "expectedRevision": job["configRevision"], "requestKey": "delegated-spoof", "input": {}, "cause": {"type": "manual"}}))
    assert response["error"]["code"] == "authorization_denied"
    assert not h.owned["ses_a"].queue_pending
    # The very same authenticated/granted transport can submit an ordinary
    # automation message; source metadata grants no additional authority.
    ordinary = create(h, value=config(h, "session-message", sessionId="ses_a", text="ordinary"), key="ordinary-create")
    response = control(h, "jobs", ordinary, "enable", "ordinary-enable")
    assert response.status_code == 200
    ordinary = response.json()["result"]["object"]
    result = asyncio.run(runtime.authorization_objects("POST", "/api/jobs/" + ordinary["id"] + "/runs", {
        "expectedRevision": ordinary["configRevision"], "requestKey": "delegated-normal", "input": {}, "cause": {"type": "manual"}}))
    assert "error" not in result
    asyncio.run(h.service.dispatch_once())
    assert h.owned["ses_a"].queue_pending[0]["source"] == "automation"


def test_handoff_only_declared_targets_preserves_nested_payload_and_frozen_run(h):
    base = next(a for a in h.service.actions(actor={"type": "user", "id": "local"})["items"] if a["id"] == "product_session_message_v2")
    old_definition = copy.deepcopy(base)
    value = definition("action", base)
    value["inputSchema"]["properties"]["business"] = {"type": "object"}
    h.service.publish_action("test_session_payload", value)
    cfg = config(h, "session-message", sessionId="ses_a", text="body", source="agent", sourceSessionId="ses_source")
    action = h.service.actions(actor={"type": "user", "id": "local"})["references"]["test_session_payload"]
    cfg["steps"][0]["actionRef"] = action
    nested = {"sessionId": "ses_a", "nested": {"sessionIds": ["ses_a"]}}
    cfg["steps"][0]["arguments"]["business"] = {"from": "literal", "value": nested}
    job = enabled(h, cfg)
    launch(h, job)
    before = copy.deepcopy(h.service.final.store.get(job["id"])["finalRuntime"]["plans"])
    h.service.final.handoff(job["id"], "ses_a", "ses_new", expected_revision=job["configRevision"], request_key="handoff")
    stored = h.service.final.store.get(job["id"])
    arguments = stored["config"]["steps"][0]["arguments"]
    assert arguments["sessionId"]["value"] == "ses_new"
    assert arguments["business"]["value"] == nested
    assert arguments["sourceSessionId"]["value"] == "ses_source"
    assert stored["finalRuntime"]["plans"] == before
    h.service._install_product_actions()
    assert next(a for a in h.service.actions(actor={"type": "user", "id": "local"})["items"] if a["id"] == base["id"]) == old_definition


def test_process_log_publication_additive_immutable_idempotent(h):
    actor = {"type": "user", "id": "local"}
    before = {a["id"]: fc.canonical_json(a) for a in h.service.actions(actor=actor)["items"]}
    h.service._install_product_actions()
    after = {a["id"]: fc.canonical_json(a) for a in h.service.actions(actor=actor)["items"]}
    assert before == after
    import json
    cleanup = definition("action", json.loads(before["product_process_cleanup_v1"]))
    logged = definition("action", json.loads(before["product_process_logs_v1"]))
    expected = copy.deepcopy(cleanup)
    expected["providerRef"] = {"id": "pan.typed", "releaseDigest": fc.digest("pan.typed.process-logs.v1"), "generation": 3}
    expected["outputSchema"].setdefault("properties", {})["logRef"] = {"type": "object"}
    assert logged == expected
    assert logged["completionContracts"][0]["schema"] == {"type": "object"}
    assert logged["completionContracts"][0]["terminalProofSchema"] == {"type": "object"}
    # A queued Run freezes the selected immutable release, not the catalog's
    # latest optional log publication. No process is executed by this test.
    ref = h.service.actions(actor=actor)["references"]["product_process_cleanup_v1"]
    cfg = {"name": "frozen process", "steps": [{"stepId": "process", "actionRef": ref,
        "arguments": {k: {"from": "literal", "value": v} for k, v in {"argv": ["owned-never-run"], "cwd": str(h.root)}.items()},
        "completion": {"contract": "process-exit"}}], "retention": {"policyRef": "test"}}
    job = enabled(h, cfg)
    launch(h, job)
    frozen = copy.deepcopy(h.service.final.store.get(job["id"])["finalRuntime"]["plans"])
    h.service._install_product_actions()
    assert h.service.final.store.get(job["id"])["finalRuntime"]["plans"] == frozen
