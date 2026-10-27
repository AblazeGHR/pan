"""S13 actual native HTTP/config pin, metadata CAS, GC and durable outbox.

Recording provider/sender ports expose effect calls; storage, owner, HTTP,
operations, Run settlement and notification recovery are product implementations.
No production files, historical pin store or WS wire format are replaced.
"""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService, definition
from packages.jobs.final_runtime.coordinator import PinMetadataUnavailable
from tests.test_job_final_http_service import h, create, job_def, control, enabled, launch, USER  # noqa: F401


@pytest.fixture
def native(h):
    h.service.owner.claim(0)
    h.service.owner.set_state("active")
    yield h
    asyncio.run(h.service.close())


def reopen(h):
    prior = h.service
    epoch = prior.owner.status()["epoch"]
    executor = prior.executor
    asyncio.run(prior.close())
    service = FinalService(h.root, h.automation.auth,
        automation_ledger=h.automation.ledger, executor=executor)
    service.owner.claim(epoch)
    service.owner.set_state("active")
    service.recover()
    h.service = service
    h.app.state.job_final_service = service
    return service


def update_pin(h, item, pinned, key="pin-update", revision=None):
    return h.client.put("/api/jobs/" + item["id"], json={
        "definition": {**definition("job", item), "retention": {**item["retention"], "pinned": pinned}},
        "expectedRevision": item["configRevision"] if revision is None else revision,
        "requestKey": key})


def notice_rows(h, jid):
    return copy.deepcopy(h.service.final.get(jid)["outbox"])


def disable(h, item):
    if item["state"] == "draft":
        response = control(h, "jobs", item, "enable")
        assert response.status_code == 200, response.text
        item = response.json()["result"]["object"]
    response = control(h, "jobs", item, "disable")
    assert response.status_code == 200, response.text
    return response.json()["result"]["object"]


def assert_delete_blocked(h, jid, reason):
    before = h.service.final.store.get(jid)
    with pytest.raises(fc.ContractError, match=reason):
        h.service.final.delete(jid, expected_revision=before["configRevision"])
    assert h.service.final.store.get(jid) == before
    scan = h.service.final.retention(retention_days=1, at=4102444800)
    assert scan["deleted"] == [] and reason in scan["retained"][jid]
    assert h.service.final.store.get(jid) == before


@pytest.mark.parametrize("fault", ["json", "encoding", "read"])
def test_old_pin_corruption_never_enters_native_crud_run_or_retention(native, tmp_path, monkeypatch, fault):
    h = native
    old_pins = [tmp_path / "background_jobs/pins.json", h.root / "pins.json"]
    raw = {"json": b'{broken', "encoding": b'\xff\xfe', "read": b'{"old-job":true}'}[fault]
    for path in old_pins:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    reads = []
    original_read = Path.read_text
    def read(path, *args, **kwargs):
        if path in old_pins:
            reads.append(str(path))
            if fault == "read":
                raise PermissionError("owned old pin read denied")
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    item = create(h)
    assert item["retention"]["pinned"] is False
    assert h.service.final.metadata(item["id"])["pinned"] is False
    response = update_pin(h, item, False)
    assert response.status_code == 200, response.text
    item = response.json()["result"]["object"]
    assert h.client.get("/api/jobs/" + item["id"]).json() == item
    assert h.client.get("/api/jobs").json()["items"] == [item]
    item = control(h, "jobs", item, "enable").json()["result"]["object"]
    run = launch(h, item)
    asyncio.run(h.service.dispatch_once())
    assert h.client.get("/api/job-runs/" + run["runId"]).json()["state"] == "completed"
    assert len(h.provider.calls) == 1
    assert h.client.get("/api/operations/" + run["operationId"]).json()["status"] == "completed"
    async def sender(row):
        return {"outcome": "succeeded", "originalKey": row["legacyId"], "outboxId": row["outboxId"]}
    h.service.outbox_sender = sender
    asyncio.run(h.service.deliver_outbox())
    disable(h, h.client.get("/api/jobs/" + item["id"]).json())
    scan = h.service.final.retention(retention_days=1, at=4102444800)
    assert scan["deleted"] == [item["id"]]
    assert reads == []
    assert all(path.read_bytes() == raw for path in old_pins)
    assert h.service.final.history(item["id"])["runs"][0]["runId"] == run["runId"]


def test_http_config_pin_cas_operation_and_disk_reopen(native):
    h = native
    item = create(h)
    response = update_pin(h, item, True)
    assert response.status_code == 200, response.text
    receipt = response.json()
    pinned = receipt["result"]["object"]
    assert pinned["retention"]["pinned"] is True and pinned["configRevision"] == 2
    before = h.service.creators.path.read_bytes()
    stale = update_pin(h, item, False, "stale-pin")
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict"
    assert h.service.creators.path.read_bytes() == before
    reopened = reopen(h)
    assert h.client.get("/api/jobs/" + item["id"]).json() == pinned
    assert reopened.final.metadata(item["id"])["pinned"] is True
    assert h.client.get("/api/operations/" + receipt["operationId"]).json() == receipt
    disabled = disable(h, pinned)
    assert_delete_blocked(h, item["id"], "pinned")
    unpin = update_pin(h, disabled, False, "unpin").json()["result"]["object"]
    assert unpin["configRevision"] == 3
    assert reopened.final.delete(item["id"], expected_revision=3)["historyRetained"]


def test_runtime_metadata_pin_cas_persists_without_mutating_config_or_runs(native):
    h = native
    item = create(h)
    before = h.service.final.store.get(item["id"])
    assert h.service.final.set_pin(item["id"], True, expected_revision=0) == {"pinned": True, "revision": 1}
    saved = h.service.creators.path.read_bytes()
    with pytest.raises(fc.ContractError) as conflict:
        h.service.final.set_pin(item["id"], False, expected_revision=0)
    assert conflict.value.code == "revision_conflict"
    assert h.service.creators.path.read_bytes() == saved
    reopened = reopen(h)
    assert reopened.final.metadata(item["id"]) == {"pinned": True, "revision": 1}
    after = reopened.final.store.get(item["id"])
    assert after["config"] == before["config"] and after["canonical"] == before["canonical"]
    assert after["finalRuntime"] == before["finalRuntime"]
    disable(h, item)
    assert_delete_blocked(h, item["id"], "pinned")


@pytest.mark.parametrize("path", ["http-config", "runtime-metadata"])
def test_pin_write_failure_rolls_back_disk_and_has_no_success_or_outbox(native, monkeypatch, path):
    h = native
    item = create(h)
    jid = item["id"]
    disk = h.service.creators.path.read_bytes()
    original = h.service._save
    def fail(db, kind, identity, value):
        original(db, kind, identity, value)
        if kind == "job" and identity == jid:
            raise OSError("owned pin transaction disk failure after SQL write")
    with monkeypatch.context() as patch:
        patch.setattr(h.service, "_save", fail)
        if path == "http-config":
            response = update_pin(h, item, True, "failed-pin")
            assert response.status_code == 503 and response.json()["error"]["code"] == "storage_unavailable"
        else:
            with pytest.raises(OSError, match="disk failure"):
                h.service.final.set_pin(jid, True, expected_revision=0)
    assert h.service.creators.path.read_bytes() == disk
    assert h.client.get("/api/jobs/" + jid).json() == item
    assert h.service.final.metadata(jid) == {"revision": 0, "pinned": False}
    assert notice_rows(h, jid) == {} and h.provider.calls == []
    assert h.client.get("/api/operations", params={"operation": "update", "requestKey": "failed-pin"}).status_code == 404


def test_lost_pin_response_queries_original_operation_without_reposting(native, monkeypatch):
    h = native
    item = create(h)
    original = h.service.write
    committed = []
    def lose_response(*args, **kwargs):
        result = original(*args, **kwargs)
        committed.append(result)
        raise OSError("owned response lost after durable commit")
    with monkeypatch.context() as patch:
        patch.setattr(h.service, "write", lose_response)
        response = update_pin(h, item, True, "ambiguous-pin")
        assert response.status_code == 503
    reopen(h)
    lookup = h.client.get("/api/operations", params={"operation": "update", "requestKey": "ambiguous-pin"})
    assert lookup.status_code == 200 and lookup.json() == committed[0]
    assert h.client.get("/api/operations/" + lookup.json()["operationId"]).json() == committed[0]
    current = h.client.get("/api/jobs/" + item["id"]).json()
    assert current["configRevision"] == 2 and current["retention"]["pinned"] is True
    assert len(committed) == 1 and h.provider.calls == [] and notice_rows(h, item["id"]) == {}


@pytest.mark.parametrize("state,reason", [
    ({"runtimeMetadata": {"pinned": None}}, "pin_metadata_unknown"),
    ({"runtimeMetadata": {"pinned": True}}, "pinned"),
    ({"cleanupPending": True}, "cleanup_or_ownership_unknown"),
    ({"status": "cleanup_pending"}, "active_or_unknown"),
])
def test_canonical_unknown_pin_and_cleanup_pending_block_delete_and_gc(native, state, reason):
    h = native
    item = disable(h, create(h))
    # Explicit synthetic persisted corruption/resource state; no process fixture.
    h.service.final.store.mutate(item["id"], lambda job: job.update(copy.deepcopy(state)))
    if state.get("runtimeMetadata", {}).get("pinned", False) is None:
        with pytest.raises(PinMetadataUnavailable):
            h.service.final.metadata(item["id"])
    assert_delete_blocked(h, item["id"], reason)


@pytest.mark.parametrize("operation", ["delete", "gc"])
def test_http_pin_true_must_protect_gc_after_runtime_unpin(native, operation):
    """Strict S13 red if native HTTP pin success can still allow physical GC."""
    h = native
    item = create(h)
    h.service.final.set_pin(item["id"], False, expected_revision=0)
    response = update_pin(h, item, True)
    assert response.status_code == 200 and response.json()["result"]["object"]["retention"]["pinned"] is True
    disable(h, response.json()["result"]["object"])
    if operation == "delete":
        with pytest.raises(fc.ContractError, match="pinned"):
            h.service.final.delete(item["id"], expected_revision=2)
    else:
        result = h.service.final.retention(retention_days=1, at=4102444800)
        assert result["deleted"] == [], result
        assert "pinned" in result["retained"][item["id"]]


def test_pending_terminal_outbox_reopens_and_acks_same_key_without_reexecution(native):
    h = native
    item = enabled(h)
    run = launch(h, item)
    asyncio.run(h.service.dispatch_once())
    before = notice_rows(h, item["id"])
    assert len(before) == 1
    row = next(iter(before.values()))
    assert row["channel"] == "ws" and row["legacyId"] == "canonical:" + run["runId"]
    assert row["status"] == "pending"
    assert h.client.get("/api/operations/" + run["operationId"]).json()["status"] == "completed"
    reopen(h)
    assert notice_rows(h, item["id"]) == before
    sent = []
    async def sender(value):
        # Observe the product sender port, without claiming real WS delivery.
        assert notice_rows(h, item["id"])[value["outboxId"]]["status"] == "sending"
        sent.append(copy.deepcopy(value))
        return {"outcome": "succeeded", "originalKey": value["legacyId"], "outboxId": value["outboxId"]}
    h.service.outbox_sender = sender
    asyncio.run(h.service.deliver_outbox())
    asyncio.run(h.service.deliver_outbox())
    asyncio.run(h.service.dispatch_once())
    assert len(sent) == 1 and len(h.provider.calls) == 1
    assert sent[0]["outboxId"] == row["outboxId"]
    assert notice_rows(h, item["id"])[row["outboxId"]]["status"] == "acked"
    assert h.service.final.query_original(row["legacyId"])[0]["runs"][0]["runId"] == run["runId"]


def test_notification_ack_commit_failure_recovers_uncertain_without_resending(native, monkeypatch):
    h = native
    item = enabled(h)
    run = launch(h, item)
    asyncio.run(h.service.dispatch_once())
    row = next(iter(notice_rows(h, item["id"]).values()))
    sent = []
    receipt = {"outcome": "succeeded", "originalKey": row["legacyId"], "outboxId": row["outboxId"]}
    async def sender(value):
        sent.append(copy.deepcopy(value))
        return receipt
    h.service.outbox_sender = sender
    original = h.service._save
    def fail_ack(db, kind, identity, value):
        original(db, kind, identity, value)
        if kind == "job" and value["finalRuntime"]["outbox"][row["outboxId"]]["status"] == "acked":
            raise OSError("owned notification ack commit failed")
    with monkeypatch.context() as patch:
        patch.setattr(h.service, "_save", fail_ack)
        with pytest.raises(OSError, match="ack commit failed"):
            asyncio.run(h.service.deliver_outbox())
    assert notice_rows(h, item["id"])[row["outboxId"]]["status"] == "sending"
    reopen(h)
    assert notice_rows(h, item["id"])[row["outboxId"]]["status"] == "uncertain"
    h.service.outbox_sender = sender
    asyncio.run(h.service.deliver_outbox())
    asyncio.run(h.service.dispatch_once())
    assert len(sent) == 1 and len(h.provider.calls) == 1
    assert h.client.get("/api/operations/" + run["operationId"]).json()["status"] == "completed"
    settled = h.service.final.settle_notification(item["id"], row["legacyId"], outbox_id=row["outboxId"], receipt=receipt)
    assert settled["status"] == "acked"
    assert h.service.final.settle_notification(item["id"], row["legacyId"], outbox_id=row["outboxId"], receipt=receipt) == settled
    asyncio.run(h.service.deliver_outbox())
    assert len(sent) == 1 and len(h.provider.calls) == 1


def test_unknown_run_queries_original_operation_and_never_resends_effect(native):
    h = native
    h.provider.unknown = True
    item = enabled(h)
    run = launch(h, item)
    asyncio.run(h.service.dispatch_once())
    assert h.client.get("/api/operations/" + run["operationId"]).json()["status"] == "unknown"
    reopen(h)
    for _ in range(2):
        assert h.client.get("/api/operations", params={"operation": "create", "requestKey": "run"}).json()["operationId"] == run["operationId"]
        assert h.client.get("/api/job-runs/" + run["runId"]).json()["state"] == "uncertain"
        asyncio.run(h.service.dispatch_once())
    assert len(h.provider.calls) == 1 and len(h.service.final.runs(item["id"])) == 1


def test_session_notice_is_durable_before_ack_and_settles_original_row_after_restart(native, tmp_path, monkeypatch):
    """Real Session writer/queue; wake/auto-force excluded to avoid any Worker."""
    from packages.core import session as sessions, worker
    h = native
    target = sessions.Session(id="ses_p24_notice", name="owned notice", workdir=str(tmp_path))
    sessions._cache[target.id] = target
    sessions.save(target)
    wakes = []
    async def no_worker(identity, **kwargs):
        wakes.append(identity)
    monkeypatch.setattr(worker, "_wake_worker", no_worker)
    monkeypatch.setattr(worker, "_schedule_auto_force", lambda *args: None)
    item = enabled(h, job_def(h, notification={"on": ["completed"], "recipients": [
        {"recipientId": "owned-recipient", "channelId": "session", "targetRef": target.id}]}))
    run = launch(h, item)
    asyncio.run(h.service.dispatch_once())
    rows = notice_rows(h, item["id"])
    session_row = next(row for row in rows.values() if row["channel"] == "session")
    assert len(rows) == 2
    sent = []
    async def sender(row):
        sent.append(row["outboxId"])
        if row["channel"] == "ws":
            return {"outcome": "succeeded"}
        result = await worker.enqueue_notice(row["targetRef"], json.dumps(row["summary"]),
            source="automation", event_id=row["outboxId"], notice_kind="job_terminal",
            job_id=row["jobId"], notice_status=row["summary"]["status"])
        assert result["ok"] is True
        # Observe the actual durable Session file before returning a receipt.
        saved = json.loads((sessions.SESSION_DIR / (target.id + ".json")).read_text(encoding="utf-8"))
        assert len(saved["queue_pending"]) == 1
        assert saved["queue_pending"][0]["eventId"] == row["outboxId"]
        return {"outcome": "succeeded"}
    h.service.outbox_sender = sender
    original = h.service._save
    def fail_ack(db, kind, identity, value):
        original(db, kind, identity, value)
        if kind == "job" and value["finalRuntime"]["outbox"][session_row["outboxId"]]["status"] == "acked":
            raise OSError("owned crash after Session commit before outbox ack")
    with monkeypatch.context() as patch:
        patch.setattr(h.service, "_save", fail_ack)
        with pytest.raises(OSError, match="Session commit"):
            asyncio.run(h.service.deliver_outbox())
    assert wakes == [target.id]
    sessions._cache.pop(target.id)
    restored = sessions.get(target.id)
    assert len(restored.queue_pending) == 1
    notice = restored.queue_pending[0]
    assert notice["eventId"] == session_row["outboxId"] and notice["jobId"] == item["id"]
    assert notice["noticeKind"] == "job_terminal" and notice["source"] == "automation"
    assert notice["deliveryState"] == "queued"
    reopen(h)
    h.service.outbox_sender = sender
    asyncio.run(h.service.deliver_outbox())
    assert len(sent) == 2 and len(h.provider.calls) == 1 and len(restored.queue_pending) == 1
    assert notice_rows(h, item["id"])[session_row["outboxId"]]["status"] == "uncertain"
    # Query durable enqueue by the original event key, then explicitly settle;
    # never call enqueue_notice or resubmit the Job again to resolve unknown.
    receipt = {"outcome": "succeeded", "originalKey": session_row["legacyId"],
               "outboxId": notice["eventId"], "queueItemId": notice["queueItemId"]}
    h.service.final.settle_notification(item["id"], session_row["legacyId"],
        outbox_id=session_row["outboxId"], receipt=receipt)
    assert all(row["status"] == "acked" for row in notice_rows(h, item["id"]).values())
    assert h.client.get("/api/operations/" + run["operationId"]).json()["status"] == "completed"
