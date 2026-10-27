"""Exact canonical successors: real HTTP, real independent intent/owned tree.

The two DELETE successors deliberately stay red until MA's shared control is
integrated. Backend cleanup evidence is collected before reporting that red.
"""
import asyncio
import json
import os
from pathlib import Path
import socket
import threading
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from packages.automation.service import Service as Automation
from packages.jobs import final_api
from packages.jobs.final_service import bind, object_ref
from packages.jobs.independent_executor import IndependentExecutor, effect_id
from tests.test_job_retained_safety_owned import owned, submit, exercise_cleanup, wait_for

USER = {"type": "user", "id": "local"}


@pytest.fixture
def canonical(owned):
    app = FastAPI()
    automation = Automation(owned.tmp_path / "automation", SimpleNamespace(workers={}), None)
    automation.auth = final_api.CanonicalUserAuth(owned.tmp_path / "automation")
    service = bind(app, automation, owned.tmp_path / "canonical")
    app.state.automation = automation
    from packages.jobs.action_api import router as action_router
    from packages.jobs.api import router as facade_router
    final_api.install(app, action_router, facade_router)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    assert port not in (8767, 8768)
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    wait_for(lambda: server.started, bool)
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10,
        headers={"Authorization": "Bearer " + automation.auth.login(automation.auth.key())})
    state = SimpleNamespace(service=service, client=client, release_receipt=threading.Event(), dispatch=None,
                            dispatch_errors=[], owned=owned)
    try:
        yield state
    finally:
        owned.release.touch()
        state.release_receipt.set()
        if state.dispatch:
            state.dispatch.join(15)
            assert not state.dispatch.is_alive()
        client.close()
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()
        asyncio.run(service.close())
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        owned.evidence.update(canonicalPort=port, canonicalPortReleased=True)


def launch(c):
    o = c.owned
    action = next(item for item in c.service.actions(actor=USER)["items"] if item["id"] == "product_process")
    definition = {"name": "retained safety canonical parent", "steps": [{"stepId": "owned",
        "actionRef": object_ref("action", action),
        "arguments": {key: {"from": "literal", "value": value} for key, value in o.plan["arguments"].items()},
        "completion": {"contract": "process-exit"}}], "retention": {"policyRef": "test"}}
    response = c.client.post("/api/jobs", json={"definition": definition, "expectedRevision": 0, "requestKey": "create"})
    assert response.status_code == 201, response.text
    job = response.json()["result"]["object"]
    response = c.client.post("/api/jobs/" + job["id"] + "/operations", json={"operation": "enable",
        "expectedRevision": job["configRevision"], "expectedStateRevision": job["stateRevision"],
        "parameters": {}, "requestKey": "enable"})
    assert response.status_code == 200, response.text
    job = response.json()["result"]["object"]
    c.job = job
    o.job_id = job["id"]
    adapter = IndependentExecutor(job_id=job["id"], client=o.client)

    class AwaitOriginalReceipt:
        """Executor port waits for the actual host result, never synthesizes it.

        Holding terminal delivery models an in-flight network callback. This
        does not claim default execution_pending recovery is already wired.
        """
        async def execute(self, *, attempt, effect_key, frozen_plan, retry=False):
            o.attempt, o.key, o.plan = attempt, effect_key, frozen_plan
            pending = await adapter.execute(attempt=attempt, effect_key=effect_key, frozen_plan=frozen_plan, retry=retry)
            o.identity = effect_id(effect_key)
            if pending["receipt"].get("code") != "execution_pending":
                return pending
            while True:
                doc = await asyncio.to_thread(o.client.get_execution, o.identity)
                if doc["result"] is not None:
                    assert await asyncio.to_thread(c.release_receipt.wait, 20), "terminal delivery gate timed out"
                    return doc["result"]
                await asyncio.sleep(.02)

    c.service.executor = AwaitOriginalReceipt()
    response = c.client.post("/api/jobs/" + job["id"] + "/runs", json={"expectedRevision": job["configRevision"],
        "requestKey": "run", "input": {}, "cause": {"type": "manual"}})
    assert response.status_code == 202, response.text
    c.run_id = response.json()["runId"]

    def dispatch():
        try:
            asyncio.run(c.service.dispatch_once())
        except BaseException as exc:
            c.dispatch_errors.append(repr(exc))
    c.dispatch = threading.Thread(target=dispatch, daemon=True)
    c.dispatch.start()
    wait_for(lambda: getattr(o, "identity", None), bool)
    # Idempotent duplicate admission must query the same executor identity.
    doc, request = submit(o)
    assert doc["jobId"] == job["id"] and doc["attempt"]["runId"] == c.run_id
    assert len(o.children) == 1
    return job


def delete(c, key):
    job = c.client.get("/api/jobs/" + c.job["id"]).json()
    return c.client.request("DELETE", "/api/jobs/" + job["id"], json={"expectedRevision": job["configRevision"],
        "expectedStateRevision": job["stateRevision"], "requestKey": key})


@pytest.mark.skipif(os.name != "nt", reason="P23 Windows FILETIME/Job Object real-tree verification")
@pytest.mark.parametrize("first_unknown", [False, True])
def test_real_owned_tree_delete_http_successor(canonical, first_unknown):
    c, o = canonical, canonical.owned
    o.evidence["label"] = "canonical-delete-unknown-" + str(first_unknown).lower()
    launch(c)
    wired = []

    def canonical_cleanup():
        # DELETE is logical retirement. Explicit Run cancellation must arrange
        # owned cleanup before anyone can claim physical deletion is safe.
        run = c.client.get("/api/job-runs/" + c.run_id).json()
        response = c.client.post("/api/job-runs/" + c.run_id + "/operations", json={"operation": "cancel",
            "expectedStateRevision": run["stateRevision"], "requestKey": "cancel-original"})
        assert response.status_code == 202, response.text
        operation_id = response.json()["operationId"]
        try:
            doc = wait_for(lambda: o.client.get_execution(o.identity), lambda row: "cleanupRequest" in row, timeout=1)
            wired.append(True)
        except AssertionError:
            wired.append(False)
            doc = o.client.cleanup_execution(o.identity, o.attempt)
        operation = c.client.get("/api/operations/" + operation_id).json()
        assert operation["status"] in {"accepted", "pending", "unknown"}
        o.evidence.update(canonicalCleanupRequested=wired[0], canonicalCancelStatus=response.status_code,
                         canonicalOperationBeforeProof=operation["status"])
        return doc

    final = exercise_cleanup(o, first_unknown, request_cleanup=canonical_cleanup)
    assert final["jobId"] == c.job["id"]
    c.release_receipt.set()
    c.dispatch.join(10)
    assert not c.dispatch.is_alive() and c.dispatch_errors == []
    assert c.client.get("/api/job-runs/" + c.run_id).json()["state"] == "failed"
    after = delete(c, "delete-confirmed")
    assert after.status_code == 200, after.text
    assert c.client.get("/api/jobs/" + c.job["id"]).json()["state"] == "retired"
    o.evidence.update(retireStatus=after.status_code, physicallyCleanBeforeRetire=True)
    assert wired == [True], (
        "P23 shared-entry gap: canonical Run cancel never requested cleanup of the original owned executor; "
        "the test had to explicitly call the independent cleanup endpoint. DELETE only retires the definition.")


@pytest.mark.skipif(os.name != "nt", reason="P23 Windows FILETIME/Job Object real-tree verification")
def test_terminal_child_writeback_does_not_recreate_retired_parent_successor(canonical):
    c, o = canonical, canonical.owned
    o.evidence["label"] = "canonical-late-terminal"
    launch(c)
    # Let the real parent exit successfully; its child is still alive until
    # the original runner's Job handle proves and cleans the entire tree.
    o.release.touch()
    final = wait_for(lambda: o.client.get_execution(o.identity), lambda row: row["result"] is not None)
    assert final["result"]["outcome"] == "succeeded"
    assert final["result"]["cleanupProof"]["ownedTree"]["confirmed"] is True
    o.children[0].wait(timeout=5)
    from tests.test_job_retained_safety_owned import remaining
    assert not remaining(o.identities)
    response = delete(c, "retire-before-late-receipt")
    assert response.status_code == 200, response.text
    retired = c.client.get("/api/jobs/" + c.job["id"]).json()
    assert retired["state"] == "retired"
    c.release_receipt.set()
    c.dispatch.join(10)
    assert not c.dispatch.is_alive() and c.dispatch_errors == []
    assert c.client.get("/api/job-runs/" + c.run_id).json()["state"] == "completed"
    assert c.client.get("/api/jobs/" + c.job["id"]).json() == retired
    assert [item["id"] for item in c.client.get("/api/jobs").json()["items"]] == [c.job["id"]]
    asyncio.run(c.service.dispatch_once())
    asyncio.run(c.service.recover_external_receipts())
    assert c.client.get("/api/jobs/" + c.job["id"]).json() == retired
    denied = c.client.post("/api/jobs/" + c.job["id"] + "/runs", json={"expectedRevision": retired["configRevision"],
        "requestKey": "must-not-resurrect", "input": {}, "cause": {"type": "manual"}})
    assert denied.status_code == 409, denied.text
    assert o.effects.read_text().splitlines() == ["effect"] and len(o.children) == 1
    assert len((o.root / "kills.jsonl").read_text().splitlines()) == 1
    o.evidence.update(parentState="retired", lateTerminalRunState="completed", parentUnchanged=True,
        noReplacementParent=True, effects=1, cleanupCalls=1, newAdmissionRejected=True)
