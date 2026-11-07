"""P4 original-identity, frozen-Run and Trigger delivery contracts."""
import asyncio
import copy
import json
import sys
from concurrent.futures import ThreadPoolExecutor
import pytest
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService, definition, object_ref
from packages.jobs.final_runtime.ledger import aggregate
from packages.actions.job_creation import product_definition
from packages.jobs.action_trigger import deliver
from tests.test_job_final_http_service import h, USER, create, enabled, job_def, control, launch


def install(h, *, start=False, value=None):
    action = product_definition()
    action["system"] = False
    if value is not None:
        action["inputSchema"] = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
        action["execution"]["config"] = {"jobDefinition": value, "start": start}
    else:
        action["execution"]["config"]["start"] = start
    result = object_ref("action", h.service.publish_action("test_job_create", action))
    if h.service.owner.token is None:
        h.service.owner.claim(0)
    return result


def invoke(h, ref, args, key="invoke"):
    return asyncio.run(h.service.invoke_action(ref["id"], {"actionRef": ref, "arguments": args, "requestKey": key}, actor=USER))


def trigger(h, target, *, arguments=None, limits=None):
    value = {"name": "direct", "eventTypes": ["test.event"], "creatorSelector": {"mode": "registeredType", "providerType": "fixture"},
             "target": target, "arguments": arguments or {}}
    if limits:
        value["concurrency"] = limits
    item = create(h, "triggers", value, "trigger")
    response = control(h, "triggers", item, "enable", "enable-trigger")
    assert response.status_code == 200, response.text
    return response.json()["result"]["object"]


def event(key="event1"):
    return {"eventId": key, "type": "test.event", "creatorType": "fixture", "payload": {"title": "From event"}}


def plans(h, jid):
    with h.service.transaction(readonly=True) as db:
        return copy.deepcopy(aggregate(h.service._get(db, "job", jid), h.service.root)["plans"])


def test_parameterized_creation_response_loss_cold_query_and_key_conflict(h):
    value = job_def(h)
    value["name"] = {"$argument": "title"}
    ref = install(h, value=value)
    first = invoke(h, ref, {"title": "Created"})
    jid = first["result"]["jobId"]
    assert h.service.get("job", jid, actor=USER)["name"] == "Created"
    assert first["result"]["start"]["status"] == "not_requested"
    assert h.service.final.runs(jid) == []
    provenance = h.client.get("/api/jobs/" + jid + "/provenance").json()
    assert provenance["operationId"] == first["operationId"] and provenance["actionRef"] == ref
    assert "Created" not in json.dumps(provenance)
    restored = FinalService(h.root, h.automation.auth, automation_ledger=h.automation.ledger)
    replay = asyncio.run(restored.invoke_action(ref["id"], {"actionRef": ref, "arguments": {"title": "Created"}, "requestKey": "invoke"}, actor=USER))
    assert replay["operationId"] == first["operationId"]
    assert replay["result"]["jobId"] == jid
    with pytest.raises(fc.ContractError) as err:
        invoke(h, ref, {"title": "Changed"})
    assert err.value.code == "idempotency_conflict"
    assert len(h.service.list("job", actor=USER)["items"]) == 1


def test_concurrent_job_creation_and_full_config(h):
    ref = install(h)
    value = job_def(h, steps=2, description="preserve", overlap={"mode": "queue", "maxActive": 1, "maxPending": 9})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: invoke(h, ref, {"jobDefinition": value}), range(4)))
    assert len({r["operationId"] for r in results}) == 1
    item = h.service.get("job", results[0]["result"]["jobId"], actor=USER)
    assert definition("job", item) == fc.validate_definition("job", value)


def test_start_acceptance_separate_and_nested_job_creation(h):
    child_ref = install(h)
    original = h.action
    value = job_def(h)
    value["steps"] = [{"stepId": "nested", "actionRef": child_ref,
        "arguments": {"jobDefinition": {"from": "literal", "value": job_def(h)}}, "completion": {"contract": "job-created"}}]
    # Creating a Job whose first step creates another Job is explicitly legal.
    creator = product_definition()
    creator["system"] = False
    creator["execution"]["config"]["start"] = True
    outer_ref = object_ref("action", h.service.publish_action("outer_creator", creator))
    result = invoke(h, outer_ref, {"jobDefinition": value})
    assert result["result"]["creation"]["status"] == "completed"
    assert result["result"]["start"]["status"] == "accepted"
    jid = result["result"]["jobId"]
    asyncio.run(h.service.dispatch_once())
    assert len(h.service.list("job", actor=USER)["items"]) == 2
    rid = result["result"]["start"]["runId"]
    assert h.service.get_run(rid, actor=USER)["state"] == "completed"
    assert len(h.service.final.get(jid)["effects"]) == 1
    assert invoke(h, outer_ref, {"jobDefinition": value})["operationId"] == result["operationId"]


def grant(h, **rows):
    permissions = h.automation.auth._default_permissions()
    for name, fields in rows.items():
        permissions[name].update(fields)
    old = h.automation.auth.permissions_status()["revision"]
    h.automation.auth.change_permissions(permissions, old)
    return {"type": "agent", "id": "fixture", "revision": old + 1}


def test_creation_permission_and_start_rejection_are_distinct(h):
    ref = install(h, start=True)
    actor = grant(h, action={"read": True, "invoke": True}, job={"create": True, "read": True})
    body = {"actionRef": ref, "arguments": {"jobDefinition": job_def(h)}, "requestKey": "agent-create"}
    result = asyncio.run(h.service.invoke_action(ref["id"], body, actor=actor))
    assert result["result"]["creation"]["status"] == "completed"
    assert result["result"]["start"] == {"status": "rejected", "code": "authorization_denied"}
    jid = result["result"]["jobId"]
    assert h.service.get("job", jid, actor=USER)["state"] == "disabled"
    assert h.service.final.runs(jid) == []
    denied = grant(h, action={"read": True, "invoke": True})
    with pytest.raises(fc.ContractError) as exc:
        asyncio.run(h.service.invoke_action(ref["id"], {**body, "requestKey": "no-create"}, actor=denied))
    assert exc.value.code == "authorization_denied"
    assert len(h.service.list("job", actor=USER)["items"]) == 1


def upgrade(h):
    first = h.action
    with h.service.transaction(readonly=True) as db:
        value = copy.deepcopy(h.service._action_version_record(db, first["id"], first["revision"])["definition"])
    value["description"] = "v2"
    edited = h.service.write("action", first["id"], {"definition": value, "expectedRevision": 1, "requestKey": "edit-v2"}, actor=USER)["result"]["object"]
    published = h.service.control("action", first["id"], {"operation": "publish", "expectedRevision": edited["revision"],
        "expectedStateRevision": edited["stateRevision"], "parameters": {}, "requestKey": "publish-v2"}, actor=USER)
    version = published["result"]["version"]
    second = {"id": first["id"], "revision": version["revision"], "digest": version["digest"]}
    return first, second


def scope(job, ref):
    return {"jobs": [{"jobId": job["id"], "expectedRevision": job["configRevision"],
                      "steps": [{"stepId": "s0", "actionRef": ref}]}], "triggers": []}


def preview(h, value):
    response = h.client.post("/api/jobs/action-references/preview", json=value)
    assert response.status_code == 200, response.text
    return response.json()


def apply(h, value, key="upgrade"):
    return h.client.post("/api/jobs/action-references/apply", json={"scope": value["scope"], "previewDigest": value["previewDigest"], "requestKey": key})


def test_reference_update_frozen_queued_and_future_run_cas_idempotency(h):
    job = enabled(h, job_def(h, overlap={"mode": "queue", "maxActive": 1, "maxPending": 8}))
    old, new = upgrade(h)
    before = launch(h, job)
    frozen = copy.deepcopy(plans(h, job["id"])[before["runId"]])
    reviewed = preview(h, scope(job, new))
    assert not reviewed["blockers"]
    result = apply(h, reviewed)
    assert result.status_code == 200, result.text
    assert apply(h, reviewed).json()["operationId"] == result.json()["operationId"]
    assert apply(h, reviewed, "stale").status_code == 409
    assert plans(h, job["id"])[before["runId"]] == frozen
    current = h.service.get("job", job["id"], actor=USER)
    after = launch(h, current, "future")
    state = {"plans": plans(h, job["id"])}
    assert state["plans"][before["runId"]]["frozen"]["steps"][0]["actionRef"] == old
    assert state["plans"][after["runId"]]["frozen"]["steps"][0]["actionRef"] == new


def test_reference_admission_race_is_wholly_old_or_new(h):
    job = enabled(h, job_def(h, overlap={"mode": "queue", "maxActive": 1, "maxPending": 8}))
    old, new = upgrade(h)
    reviewed = preview(h, scope(job, new))
    def admission():
        try:
            return h.service.create_run(job["id"], {"expectedRevision": job["configRevision"], "requestKey": "racing", "input": {}, "cause": {"type": "manual"}}, actor=USER)
        except fc.ContractError as exc:
            assert exc.code == "revision_conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        run, result = list(pool.map(lambda f: f(), [admission, lambda: apply(h, reviewed)]))
    assert result.status_code == 200
    if run:
        frozen = plans(h, job["id"])[run["runId"]]["frozen"]
        assert frozen["configRevision"] == 1 and frozen["steps"][0]["actionRef"] == old
    current = h.service.get("job", job["id"], actor=USER)
    future = launch(h, current, "after-race")
    frozen = plans(h, job["id"])[future["runId"]]["frozen"]
    assert frozen["configRevision"] == 2 and frozen["steps"][0]["actionRef"] == new


def test_pinned_subscriber_explicit_sync_and_original_event_identity(h):
    job = enabled(h)
    old, new = upgrade(h)
    t = trigger(h, {"type": "job", "jobId": job["id"], "configRevision": 1})
    first = h.service.admit_event(event(), t)
    value = scope(job, new)
    blocked = preview(h, value)
    assert blocked["blockers"][0]["reason"] == "subscriber_not_selected"
    assert apply(h, blocked).status_code == 409
    value["triggers"] = [{"triggerId": t["id"], "expectedRevision": t["revision"]}]
    reviewed = preview(h, value)
    assert apply(h, reviewed).status_code == 200
    updated = h.service.get("trigger", t["id"], actor=USER)
    assert updated["target"]["configRevision"] == 2
    assert h.service.admit_event(event(), updated)["operationId"] == first["operationId"]
    future = h.service.admit_event(event("next"), updated)
    assert future["result"]["configRevision"] == 2


def test_reference_batch_rollback_retired_incompatible_and_permissions(h):
    one = enabled(h)
    two = create(h, value=job_def(h), key="two")
    old, new = upgrade(h)
    value = scope(one, new)
    value["jobs"].append({**scope(two, new)["jobs"][0], "expectedRevision": 999})
    response = h.client.post("/api/jobs/action-references/preview", json=value)
    assert response.status_code == 409
    assert h.service.get("job", one["id"], actor=USER)["configRevision"] == 1
    actor = grant(h, job={"read": True}, action={"read": True}, trigger={"read": True})
    with pytest.raises(fc.ContractError) as err:
        h.service.preview_action_references(scope(one, new), actor=actor)
    assert err.value.code == "authorization_denied"


def test_trigger_action_dedup_revision_freeze_and_cold_delivery(h):
    value = job_def(h)
    value["name"] = {"$argument": "title"}
    ref = install(h, value=value)
    t = trigger(h, {"type": "action", "actionRef": ref}, arguments={"title": {"from": "event", "path": "payload.title"}})
    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = list(pool.map(lambda _: h.service.admit_event(event(), t), range(4)))
    assert len({r["operationId"] for r in accepted}) == 1
    assert h.service.list("job", actor=USER)["items"] == []
    changed = definition("trigger", t)
    changed["name"] = "new revision"
    updated = h.service.write("trigger", t["id"], {"definition": changed, "expectedRevision": t["revision"], "requestKey": "edit"}, actor=USER)["result"]["object"]
    assert h.service.admit_event(event(), updated)["operationId"] == accepted[0]["operationId"]
    asyncio.run(deliver(h.service))
    result = h.service.operation(accepted[0]["operationId"], actor=USER)
    jid = result["result"]["jobId"]
    assert result["result"]["provenance"]["source"]["triggerRevision"] == t["revision"]
    assert h.service.get("job", jid, actor=USER)["name"] == "From event"
    asyncio.run(deliver(h.service))
    assert len(h.service.list("job", actor=USER)["items"]) == 1
    restored = FinalService(h.root, h.automation.auth, automation_ledger=h.automation.ledger)
    assert restored.admit_event(event(), updated)["operationId"] == result["operationId"]
    assert restored.operation(result["operationId"], actor=USER) == result
    with pytest.raises(fc.ContractError) as err:
        h.service.admit_event({**event(), "payload": {"title": "changed"}}, updated)
    assert err.value.code == "idempotency_conflict"


def test_trigger_command_crash_after_effect_no_replay(h, tmp_path, monkeypatch):
    from packages.actions.command_execution import product_definition as command_definition
    action = command_definition("process.command")
    action["system"] = False
    marker = tmp_path / "count"
    action["execution"]["config"] = {"argv": [sys.executable, "-c", "from pathlib import Path;p=Path(" + repr(str(marker)) + ");p.write_text(p.read_text()+'x' if p.exists() else 'x')"], "cwd": str(tmp_path)}
    ref = object_ref("action", h.service.publish_action("trigger_command", action))
    h.service.owner.claim(0)
    t = trigger(h, {"type": "action", "actionRef": ref})
    accepted = h.service.admit_event(event(), t)
    original = h.service.executor.execute_commands
    async def lost_receipt(**kwargs):
        await original(**kwargs)
        raise RuntimeError("receipt lost after effect")
    monkeypatch.setattr(h.service.executor, "execute_commands", lost_receipt)
    with pytest.raises(RuntimeError):
        asyncio.run(deliver(h.service))
    assert marker.read_text() == "x"
    asyncio.run(deliver(h.service))
    assert marker.read_text() == "x"
    result = h.service.operation(accepted["operationId"], actor=USER)
    assert result["status"] == "unknown" and result["result"]["phase"] == "calling"
    assert h.service.admit_event(event(), t)["operationId"] == result["operationId"]


def test_trigger_capacity_backpressure_disable_and_revocation(h):
    ref = install(h, value=job_def(h))
    t = trigger(h, {"type": "action", "actionRef": ref}, arguments={"title": {"from": "literal", "value": "ok"}}, limits={"maxActive": 1, "maxPending": 0})
    # Same fact never allocates another capacity slot.
    first = h.service.admit_event(event(), t)
    assert h.service.admit_event(event(), t)["operationId"] == first["operationId"]
    assert h.service.admit_event(event("second"), t)["status"] == "deferred"
    control(h, "triggers", t, "disable", "disable-trigger")
    asyncio.run(deliver(h.service))
    assert h.service.list("job", actor=USER)["items"] == []
    # Re-enable, then disable the fixed Action before delivery.
    current = h.service.get("trigger", t["id"], actor=USER)
    assert control(h, "triggers", current, "enable", "reenable-trigger").status_code == 200
    with h.service.transaction() as db:
        db.execute("UPDATE final_action_lifecycle SET enabled=0 WHERE actionId=?", (ref["id"],))
    asyncio.run(deliver(h.service))
    result = h.service.operation(first["operationId"], actor=USER)
    assert result["status"] == "rejected"
    assert h.service.list("job", actor=USER)["items"] == []


def test_trigger_mutually_exclusive_targets(h):
    ref = install(h)
    value = {"name": "invalid", "eventTypes": ["test.event"], "creatorSelector": {"mode": "registeredType", "providerType": "fixture"},
        "target": {"type": "action", "actionRef": ref}, "targetJobId": "other"}
    with pytest.raises(fc.ContractError) as err:
        fc.validate_definition("trigger", value)
    assert err.value.code == "invalid_schema"


def test_http_creation_response_lost_after_commit_queries_original(h, monkeypatch):
    from fastapi.testclient import TestClient
    ref = install(h)
    original = h.service.invoke_action
    async def lose(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("simulated response loss")
    body = {"actionRef": ref, "arguments": {"jobDefinition": job_def(h)}, "requestKey": "http-loss"}
    monkeypatch.setattr(h.service, "invoke_action", lose)
    with TestClient(h.app, raise_server_exceptions=False) as client:
        client.headers.update(h.client.headers)
        assert client.post("/api/jobs/actions/" + ref["id"] + "/invocations", json=body).status_code == 500
    receipt = h.service.operation_for_key("invoke", "http-loss", actor=USER)
    assert receipt["result"]["creation"]["status"] == "completed"
    monkeypatch.setattr(h.service, "invoke_action", original)
    response = h.client.post("/api/jobs/actions/" + ref["id"] + "/invocations", json=body)
    assert response.status_code == 200
    assert response.json()["operationId"] == receipt["operationId"]
    assert len(h.service.list("job", actor=USER)["items"]) == 1


def test_reference_batch_apply_failure_rolls_back_all_objects_and_operation(h, monkeypatch):
    one = enabled(h)
    two = create(h, value=job_def(h), key="second-job")
    old, new = upgrade(h)
    value = scope(one, new)
    value["jobs"].extend(scope(two, new)["jobs"])
    reviewed = h.service.preview_action_references(value, actor=USER)
    original = h.service._save
    def fail_second(db, kind, object_id, item):
        if kind == "job" and object_id == two["id"]:
            raise RuntimeError("second write rejected")
        return original(db, kind, object_id, item)
    monkeypatch.setattr(h.service, "_save", fail_second)
    body = {"scope": value, "previewDigest": reviewed["previewDigest"], "requestKey": "atomic"}
    with pytest.raises(RuntimeError):
        h.service.apply_action_references(body, actor=USER)
    assert h.service.get("job", one["id"], actor=USER)["configRevision"] == 1
    assert h.service.get("job", two["id"], actor=USER)["configRevision"] == 1
    with pytest.raises(KeyError):
        h.service.operation_for_key("update", "atomic", actor=USER)
    monkeypatch.setattr(h.service, "_save", original)
    result = h.service.apply_action_references(body, actor=USER)
    assert len(result["result"]["jobs"]) == 2
    assert h.service.get("job", two["id"], actor=USER)["configRevision"] == 2
    with pytest.raises(fc.ContractError) as exc:
        h.service.apply_action_references({**body, "previewDigest": "a" * 64}, actor=USER)
    assert exc.value.code == "idempotency_conflict"


@pytest.mark.parametrize("run_state", ["queued", "paused_at_boundary", "retry_wait", "uncertain"])
def test_reference_update_preserves_every_existing_run_state(h, run_state):
    job = enabled(h)
    old, new = upgrade(h)
    admitted = launch(h, job)
    rid = admitted["runId"]
    if run_state == "paused_at_boundary":
        from tests.test_job_final_http_service import run_control
        assert run_control(h, rid, "pause", "pause-run").status_code == 202
        asyncio.run(h.service.dispatch_once())
    elif run_state in {"retry_wait", "uncertain"}:
        with h.service.transaction() as db:
            stored = h.service._get(db, "job", job["id"])
            h.service.final.ledger.start(stored, h.root, rid)
            h.service.final.ledger.transition_run(stored, h.root, rid, "uncertain" if run_state == "uncertain" else "failed")
            if run_state == "retry_wait":
                h.service.final.ledger.transition_run(stored, h.root, rid, "retry_wait", operation="retry")
            h.service._save(db, "job", job["id"], stored)
    before = plans(h, job["id"])
    assert apply(h, preview(h, scope(job, new))).status_code == 200
    assert plans(h, job["id"]) == before
    assert h.service.get_run(rid, actor=USER)["state"] == run_state


def test_selected_step_versions_freeze_independently(h):
    job = enabled(h, job_def(h, steps=2))
    old, new = upgrade(h)
    assert apply(h, preview(h, scope(job, new))).status_code == 200
    item = h.service.get("job", job["id"], actor=USER)
    admitted = launch(h, item)
    frozen = plans(h, job["id"])[admitted["runId"]]["frozen"]
    assert frozen["stepActions"]["s0"]["revision"] == new["revision"]
    assert frozen["stepActions"]["s1"]["revision"] == old["revision"]
    asyncio.run(h.service.dispatch_once())
    assert h.service.get_run(admitted["runId"], actor=USER)["state"] == "completed"
    assert len(h.provider.calls) == 2


def test_reference_target_retire_and_subscriber_edit_invalidate_preview(h):
    job = enabled(h)
    old, new = upgrade(h)
    t = trigger(h, {"type": "job", "jobId": job["id"], "configRevision": 1})
    value = scope(job, new)
    value["triggers"] = [{"triggerId": t["id"], "expectedRevision": t["revision"]}]
    reviewed = preview(h, value)
    edited = definition("trigger", t)
    edited["name"] = "concurrent edit"
    h.service.write("trigger", t["id"], {"definition": edited, "expectedRevision": t["revision"], "requestKey": "edit-subscriber"}, actor=USER)
    assert apply(h, reviewed).status_code == 409
    assert h.service.get("job", job["id"], actor=USER)["configRevision"] == 1
    current = h.service.get("action", new["id"], actor=USER)
    result = h.service.control("action", new["id"], {"operation": "retire", "expectedRevision": current["revision"],
        "expectedStateRevision": current["stateRevision"], "parameters": {}, "requestKey": "retire-version"}, actor=USER)
    with pytest.raises(fc.ContractError) as exc:
        h.service.preview_action_references(scope(job, new), actor=USER)
    assert exc.value.code == "reference_conflict"


def test_trigger_admitted_before_effect_survives_owner_handoff(h):
    ref = install(h, value=job_def(h))
    t = trigger(h, {"type": "action", "actionRef": ref}, arguments={"title": {"from": "literal", "value": "unused"}})
    accepted = h.service.admit_event(event(), t)
    h.service.owner.release()
    h.service.owner.token = None
    restored = FinalService(h.root, h.automation.auth, automation_ledger=h.automation.ledger)
    restored.owner.claim(1)
    try:
        asyncio.run(deliver(restored))
        result = restored.operation(accepted["operationId"], actor=USER)
        assert result["result"]["creation"]["status"] == "completed"
        asyncio.run(deliver(restored))
        assert len(restored.list("job", actor=USER)["items"]) == 1
    finally:
        asyncio.run(restored.close())


def test_trigger_revoked_authority_never_borrows_user_job_grant(h):
    ref = install(h, value=job_def(h))
    t = trigger(h, {"type": "action", "actionRef": ref}, arguments={"title": {"from": "literal", "value": "ok"}})
    actor = grant(h, action={"read": True, "invoke": True}, trigger={"read": True, "lifecycle": True}, job={"create": True})
    with h.service.transaction() as db:
        db.execute("UPDATE final_authorities SET document=? WHERE kind='trigger' AND id=?", (fc.canonical_json(actor), t["id"]))
    accepted = h.service.admit_event(event(), t)
    grant(h, action={"read": True}, trigger={"read": True})
    asyncio.run(deliver(h.service))
    result = h.service.operation(accepted["operationId"], actor=actor)
    assert result["status"] == "rejected"
    assert result["result"]["code"] == "authorization_denied"
    assert h.service.list("job", actor=USER)["items"] == []


def test_trusted_group_provenance_does_not_grant_job_creation(h):
    ref = install(h)
    source = {"groupRelease": {"id": "fixture-group", "revision": 1, "digest": "a" * 64}}
    body = {"actionRef": ref, "arguments": {"jobDefinition": job_def(h)}, "requestKey": "group-create"}
    result = asyncio.run(h.service.invoke_action(ref["id"], body, actor=USER, source=source))
    assert h.service.job_provenance(result["result"]["jobId"], actor=USER)["source"] == source
    actor = grant(h, action={"read": True, "invoke": True})
    with pytest.raises(fc.ContractError) as err:
        asyncio.run(h.service.invoke_action(ref["id"], body, actor=actor, source=source))
    assert err.value.code == "authorization_denied"


@pytest.mark.parametrize("max_active", [1, 2])
def test_trigger_explicit_parallel_limit_and_concurrent_claims(h, tmp_path, monkeypatch, max_active):
    from packages.actions.command_execution import product_definition as command_definition
    action = command_definition("process.command")
    action["system"] = False
    action["execution"]["config"] = {"argv": [sys.executable, "-c", "print('claim fixture')"], "cwd": str(tmp_path)}
    ref = object_ref("action", h.service.publish_action("parallel_command", action))
    h.service.owner.claim(0)
    t = trigger(h, {"type": "action", "actionRef": ref}, limits={"maxActive": max_active, "maxPending": 3})
    accepted = [h.service.admit_event(event(str(i)), t) for i in range(3)]
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        identities = []
        active, maximum = 0, 0
        async def gated(**kwargs):
            nonlocal active, maximum
            identities.append(kwargs["context"].identity["operationId"])
            active += 1
            maximum = max(maximum, active)
            if active == max_active:
                entered.set()
            await release.wait()
            active -= 1
            return {"outcome": "succeeded", "receipt": {"status": "completed", "commands": []}}
        monkeypatch.setattr(h.service.executor, "execute_commands", gated)
        task = asyncio.create_task(deliver(h.service))
        await asyncio.wait_for(entered.wait(), 5)
        await deliver(h.service)  # Concurrent tick cannot claim the same invocation.
        assert active == max_active
        release.set()
        await task
        for _ in range(3):
            await deliver(h.service)
        assert maximum == max_active
        assert len(identities) == len(set(identities)) == 3
        assert set(identities) == {r["operationId"] for r in accepted}
    asyncio.run(scenario())


def test_real_creator_event_store_tick_delivers_action_once(h):
    from packages.scheduler.providers import time_definition
    from tests.test_job_final_creators import config, interval
    ref = install(h)
    creator_value = time_definition("p4 clock", config([interval()]))
    creator = create(h, "event-creators", creator_value, "p4-creator")
    t = create(h, "triggers", {"name": "time to Action", "eventTypes": creator_value["eventTypes"],
        "creatorSelector": {"mode": "ids", "ids": [creator["id"]]},
        "target": {"type": "action", "actionRef": ref},
        "arguments": {"jobDefinition": {"from": "literal", "value": job_def(h)}}}, "p4-time-trigger")
    assert control(h, "triggers", t, "enable", "p4-time-enable").status_code == 200
    assert control(h, "event-creators", creator, "enable", "p4-creator-enable").status_code == 200
    h.service.refresh_subscriptions()
    point = h.service.creators.get(creator["id"])["cursor"]["rules"]["daily"]["nextFireAt"]
    assert asyncio.run(h.service.creator_runtime.tick(at=point)) == 1
    asyncio.run(h.service.tick())
    jobs = h.service.list("job", actor=USER)["items"]
    assert len(jobs) == 1 and jobs[0]["state"] == "disabled"
    source = h.service.job_provenance(jobs[0]["id"], actor=USER)["source"]
    stored_event = h.service.events.page()[0]
    assert source["eventId"] == stored_event["eventId"] and source["triggerId"] == t["id"]
    asyncio.run(h.service.tick())
    assert len(h.service.list("job", actor=USER)["items"]) == 1
    references = h.service.creator_references(creator["id"], actor=USER)["references"]
    assert references[0]["jobId"] is None


def test_action_job_reference_discovery_is_paged_and_permission_checked(h):
    first = create(h)
    second = create(h, value=job_def(h), key="reference-second")
    url = "/api/jobs/actions/" + h.action["id"] + "/job-references"
    response = h.client.get(url, params={"limit": 1})
    assert response.status_code == 200, response.text
    page = response.json()
    assert len(page["items"]) == 1 and page["nextCursor"]
    following = h.client.get(url, params={"limit": 1, "cursor": page["nextCursor"]}).json()
    assert {page["items"][0]["jobId"], following["items"][0]["jobId"]} == {first["id"], second["id"]}
    actor = grant(h, action={"read": True})
    with pytest.raises(fc.ContractError) as err:
        h.service.action_job_references(h.action["id"], actor=actor)
    assert err.value.code == "authorization_denied"
