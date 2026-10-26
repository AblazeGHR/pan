"""WP06a real TestClient + durable service + WP02/WP04/WP05 integration."""
import asyncio
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.automation import authorization
from packages.automation.service import Service as Automation
from packages.jobs import final_api as api, final_contracts as fc
from packages.jobs.final_service import FinalService, bind, definition, object_ref
from packages.jobs.final_runtime.executor import TypedExecutor
from packages.scheduler.providers import time_definition
from tests.test_job_final_creators import config, interval

BASE = "/api"
USER = {"type": "user", "id": "local"}


class SessionProvider:
    def __init__(self):
        self.calls = []
        self.fail_once = None
        self.unknown = False
        self.after_send = None

    async def send_session(self, session_id, text, *, effect_key, source):
        self.calls.append((text, copy.deepcopy(effect_key)))
        if self.after_send:
            self.after_send()
        if self.unknown:
            return {"status": "unknown"}
        if self.fail_once == text:
            self.fail_once = None
            return {"status": "rejected"}
        return {"status": "queued", "messageId": "message_" + str(len(self.calls))}


@pytest.fixture
def h(tmp_path):
    app = FastAPI()
    automation = Automation(tmp_path / "automation", SimpleNamespace(workers={}), None)
    automation.auth = api.CanonicalUserAuth(tmp_path / "automation")
    service = bind(app, automation, tmp_path / "canonical")
    app.state.automation = automation
    from packages.jobs.action_api import router as action_router
    from packages.jobs.api import router as facade_router
    api.install(app, action_router, facade_router)
    provider = SessionProvider()
    service.executor = TypedExecutor(session_provider=provider)
    product = next(item for item in service.actions(actor=USER)["items"] if item["id"] == "product_session_message")
    value = definition("action", product)
    value.update(system=False, retry={"idempotent": True, "sameKeySupported": True, "requiresProof": False})
    action = service.publish_action("test_message", value)
    client = TestClient(app)
    token = automation.auth.login(automation.auth.key())
    client.headers["Authorization"] = "Bearer " + token
    result = SimpleNamespace(app=app, automation=automation, service=service, provider=provider,
        client=client, root=tmp_path / "canonical", action=object_ref("action", action), token=token)
    yield result
    client.close()
    asyncio.run(service.close())


def job_def(h, *, steps=1, **kwargs):
    return {"name": "job", "steps": [{"stepId": "s" + str(index), "actionRef": h.action,
            "arguments": {"sessionId": {"from": "literal", "value": "owned-test-session"},
                          "text": {"from": "literal", "value": str(index)}},
            "completion": {"contract": "durable-queue-accepted"}} for index in range(steps)],
            "retention": {"policyRef": "test"}, **kwargs}


def create(h, kind="jobs", value=None, key="create"):
    response = h.client.post(BASE + "/" + kind,
        json={"definition": value or job_def(h), "expectedRevision": 0, "requestKey": key})
    assert response.status_code == 201, response.text
    return response.json()["result"]["object"]


def control(h, kind, item, operation, key=None):
    return h.client.post(BASE + "/" + kind + "/" + item["id"] + "/operations", json={
        "operation": operation, "expectedRevision": item.get("configRevision", item.get("revision")),
        "expectedStateRevision": item["stateRevision"], "requestKey": key or operation, "parameters": {}})


def enabled(h, value=None):
    item = create(h, value=value)
    response = control(h, "jobs", item, "enable")
    assert response.status_code == 200, response.text
    return response.json()["result"]["object"]


def launch(h, item, key="run"):
    response = h.client.post(BASE + "/jobs/" + item["id"] + "/runs", json={
        "expectedRevision": item["configRevision"], "requestKey": key, "input": {}, "cause": {"type": "manual"}})
    assert response.status_code == 202, response.text
    return response.json()


def run_control(h, run_id, operation, key, **kwargs):
    run = h.client.get(BASE + "/job-runs/" + run_id).json()
    return h.client.post(BASE + "/job-runs/" + run_id + "/operations", json={
        "operation": operation, "expectedStateRevision": run["stateRevision"], "requestKey": key, **kwargs})


def test_ft01_real_crud_cas_idempotency_persisted_after_reload(h):
    payload = {"definition": job_def(h), "expectedRevision": 0, "requestKey": "create"}
    item = create(h)
    replay = h.client.post(BASE + "/jobs", json=payload)
    assert replay.status_code == 200
    assert replay.json()["result"]["object"]["id"] == item["id"]
    changed = {**payload, "definition": {**payload["definition"], "name": "different"}}
    assert h.client.post(BASE + "/jobs", json=changed).status_code == 409
    url = BASE + "/jobs/" + item["id"]
    assert h.client.get(url).json() == item
    update = {"definition": {**job_def(h), "name": "updated"}, "expectedRevision": 1, "requestKey": "update"}
    response = h.client.put(url, json=update)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["object"]["configRevision"] == 2
    assert h.client.put(url, json=update).json()["operationId"] == response.json()["operationId"]
    assert h.client.put(url, json={**update, "requestKey": "stale"}).status_code == 409
    restored = FinalService(h.root, h.automation.auth, automation_ledger=h.automation.ledger)
    assert restored.get("job", item["id"], actor=USER)["name"] == "updated"
    assert restored.write("job", None, payload, actor=USER)["operationId"] == replay.json()["operationId"]
    assert h.client.get(BASE + "/jobs").json()["items"][0]["name"] == "updated"
    op = h.client.get(BASE + "/operations", params={"operation": "update", "requestKey": "update"})
    assert op.status_code == 200
    assert op.json()["operationId"] == response.json()["operationId"]


def test_ft01_run_and_six_operations_use_same_wp02_identities(h):
    item = enabled(h, job_def(h, steps=2))
    h.provider.fail_once = "1"
    first = launch(h, item)
    rid = first["runId"]
    asyncio.run(h.service.dispatch_once())
    assert h.client.get(BASE + "/job-runs/" + rid).json()["state"] == "failed"
    failed_operation = h.client.get(BASE + "/operations/" + first["operationId"]).json()
    assert failed_operation["status"] == "rejected"
    retry = run_control(h, rid, "retry", "retry", stepId="s1")
    assert retry.status_code == 202, retry.text
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(item["id"])
    assert state["runs"][rid]["state"] == "completed"
    assert state["steps"][rid]["s0"]["attemptCount"] == 1
    assert state["steps"][rid]["s1"]["attemptCount"] == 2
    assert [text for text, _ in h.provider.calls] == ["0", "1", "1"]
    assert h.provider.calls[1][1] == h.provider.calls[2][1]
    assert h.client.get(BASE + "/operations/" + first["operationId"]).json() == failed_operation
    assert h.client.get(BASE + "/operations/" + retry.json()["operationId"]).json()["status"] == "completed"
    again = run_control(h, rid, "run-again", "again", expectedRevision=1)
    assert again.status_code == 202, again.text
    again_id = again.json()["runId"]
    assert again_id != rid
    assert run_control(h, again_id, "pause", "pause").status_code == 202
    asyncio.run(h.service.dispatch_once())
    assert len(h.provider.calls) == 3
    assert run_control(h, again_id, "resume", "resume").status_code == 202
    assert run_control(h, again_id, "cancel", "cancel").status_code == 202
    asyncio.run(h.service.dispatch_once())
    assert h.service.get_run(again_id, actor=USER)["state"] == "cancelled"
    clone = run_control(h, rid, "clone-and-run", "clone", expectedRevision=1, parameters={"overrides": {"name": "cloned"}})
    assert clone.status_code == 202, clone.text
    clone_id = clone.json()["result"]["jobId"]
    assert clone_id != item["id"]
    asyncio.run(h.service.dispatch_once())
    assert h.service.get_run(clone.json()["runId"], actor=USER)["state"] == "completed"
    assert len(h.client.get(BASE + "/jobs/" + item["id"] + "/runs").json()["items"]) == 2
    assert len(h.service.final.runs(clone_id)) == 1
    assert run_control(h, rid, "retry", "retry-completed").status_code == 409


def test_ft01_run_request_key_replays_after_restart_without_new_run(h):
    item = enabled(h)
    original = launch(h, item)
    request = {"expectedRevision": 1, "requestKey": "run", "input": {}, "cause": {"type": "manual"}}
    restored = FinalService(h.root, h.automation.auth)
    replay = restored.create_run(item["id"], request, actor=USER)
    assert replay["runId"] == original["runId"]
    assert len(restored.final.runs(item["id"])) == 1
    request["input"] = {"changed": True}
    with pytest.raises(fc.ContractError, match="different|changed|conflict"):
        restored.create_run(item["id"], request, actor=USER)


def test_ft01_creator_trigger_template_and_retire_real_storage(h):
    item = enabled(h)
    creator_def = time_definition("clock", config([interval()]))
    creator = create(h, "event-creators", creator_def, "creator")
    creator_url = BASE + "/event-creators/" + creator["id"]
    assert h.client.get(creator_url).json() == creator
    changed = {**creator_def, "name": "updated clock"}
    update = h.client.put(creator_url, json={"definition": changed, "expectedRevision": 1, "requestKey": "creator-update"})
    assert update.status_code == 200, update.text
    assert h.service.creators.get(creator["id"])["name"] == "updated clock"
    trigger_def = {"name": "on clock", "eventTypes": creator_def["eventTypes"],
        "creatorSelector": {"mode": "ids", "ids": [creator["id"]]}, "targetJobId": item["id"]}
    trigger = create(h, "triggers", trigger_def, "trigger")
    response = control(h, "triggers", trigger, "enable", "trigger-enable")
    assert response.status_code == 200, response.text
    template = create(h, "job-templates", {"name": "template", "jobDefinition": job_def(h),
        "composition": {"creators": [], "triggers": []}}, "template")
    published = control(h, "job-templates", template, "publish", "publish")
    assert published.status_code == 200, published.text
    assert published.json()["result"]["object"]["revision"] == 2
    retire = h.client.request("DELETE", BASE + "/triggers/" + trigger["id"], json={
        "expectedRevision": 1, "expectedStateRevision": 1, "requestKey": "retire"})
    assert retire.status_code == 200, retire.text
    assert h.client.get(BASE + "/triggers/" + trigger["id"]).json()["state"] == "retired"
    assert h.service.creators.get(creator["id"])["state"] == "draft"


def test_ft01_wp04_driver_admits_once_and_wp03_projection_is_durable(h):
    item = enabled(h)
    creator_def = time_definition("clock", config([interval()]))
    creator = create(h, "event-creators", creator_def, "creator")
    trigger = create(h, "triggers", {"name": "tick", "eventTypes": creator_def["eventTypes"],
        "creatorSelector": {"mode": "ids", "ids": [creator["id"]]}, "targetJobId": item["id"]}, "trigger")
    assert control(h, "triggers", trigger, "enable", "trigger-enable").status_code == 200
    assert control(h, "event-creators", creator, "enable", "creator-enable").status_code == 200
    # The real provider runs at a deterministic time after its stored cursor.
    stored = h.service.creators.get(creator["id"])
    point = stored["cursor"]["rules"]["daily"]["nextFireAt"]
    assert asyncio.run(h.service.creator_runtime.tick(at=point)) == 1
    event = h.service.events.page()[0]
    target = h.service.targets({"type": event["eventType"]})[0]
    receipt = h.service.admit_event(event, target)
    assert h.service.admit_event(event, target)["runId"] == receipt["runId"]
    assert len(h.service.final.runs(item["id"])) == 1
    assert len(h.automation.ledger.read()["finalAdmissions"]) == 1
    asyncio.run(h.service.dispatch_once())
    assert len(h.provider.calls) == 1


def test_ft01_revoke_between_steps_prevents_next_effect(h):
    grants = h.automation.auth._default_permissions()
    grants["job"]["execute"] = True
    h.automation.auth.change_permissions(grants, 0)
    item = enabled(h, job_def(h, steps=2))
    agent = {"type": "agent", "id": "agent", "revision": 1}
    receipt = h.service.create_run(item["id"], {"expectedRevision": 1, "requestKey": "agent-run", "input": {}, "cause": {"type": "manual"}}, actor=agent)
    def revoke():
        revoked = h.automation.auth._default_permissions()
        revoked["job"]["read"] = True
        h.automation.auth.change_permissions(revoked, 1)
        h.provider.after_send = None
    h.provider.after_send = revoke
    asyncio.run(h.service.dispatch_once())
    assert len(h.provider.calls) == 1
    assert h.service.get_run(receipt["runId"], actor=USER)["state"] == "failed"
    assert len(h.service.final.get(item["id"])["attempts"]) == 1
    async def trusted(scope, receive, send):
        scope[authorization._SCOPE_KEY] = authorization._MARKER
        await h.app(scope, receive, send)
    with TestClient(trusted) as agent_http:
        response = agent_http.get(BASE + "/operations/" + receipt["operationId"])
        assert response.status_code == 200, response.text
        operation = response.json()
    assert operation["status"] == "rejected"
    assert operation["error"]["code"] == "authorization_denied"
    assert operation["result"]["runState"] == "failed"


def test_ft01_system_grant_and_high_risk_remain_separate(h):
    grants = h.automation.auth._default_permissions()
    grants["job"]["execute"] = True
    h.automation.auth.change_permissions(grants, 0)
    action = next(a for a in h.service.actions(actor=USER)["items"] if a["id"] == "product_session_message")
    h.action = object_ref("action", action)
    item = enabled(h)
    result = h.service.create_run(item["id"], {"expectedRevision": 1, "requestKey": "agent-run", "input": {}, "cause": {"type": "manual"}},
                                  actor={"type": "agent", "id": "agent", "revision": 1})
    asyncio.run(h.service.dispatch_once())
    assert h.service.get_run(result["runId"], actor=USER)["state"] == "failed"
    assert h.provider.calls == []
    # Even a user cannot turn an arbitrary high-risk provider into a trusted system executor.
    value = definition("action", action)
    value.update(effectClass="high_risk", system=False)
    action = h.service.publish_action("high_risk_test", value)
    h.action = object_ref("action", action)
    second = create(h, value=job_def(h), key="high-risk-job")
    assert control(h, "jobs", second, "enable", "high-risk-enable").status_code == 200
    result = launch(h, second, "high-risk-run")
    asyncio.run(h.service.dispatch_once())
    assert h.service.get_run(result["runId"], actor=USER)["state"] == "failed"
    assert h.provider.calls == []


def test_ft01_unknown_is_not_retried_or_replayed_on_restart(h):
    item = enabled(h)
    h.provider.unknown = True
    result = launch(h, item)
    asyncio.run(h.service.dispatch_once())
    assert h.service.get_run(result["runId"], actor=USER)["state"] == "uncertain"
    assert run_control(h, result["runId"], "retry", "retry").status_code == 409
    restored = FinalService(h.root, h.automation.auth, executor=h.service.executor)
    restored.recover()
    asyncio.run(restored.dispatch_once())
    assert len(h.provider.calls) == 1


@pytest.mark.parametrize("field,value", [("kind", "background-process"), ("source", "user"),
    ("enabled", True), ("authorization", {"type": "user"}), ("configRevision", 999)])
def test_ft02_real_service_invalid_definitions_do_not_write(h, field, value):
    response = h.client.post(BASE + "/jobs", json={"definition": {**job_def(h), field: value},
        "expectedRevision": 0, "requestKey": "invalid"})
    assert response.status_code == 400
    assert h.client.get(BASE + "/jobs").json()["items"] == []


def test_ft02_real_http_401_403_404_409_and_cookie_same_origin(h):
    item = create(h)
    url = BASE + "/jobs/" + item["id"]
    assert h.client.get(BASE + "/jobs/missing").status_code == 404
    assert h.client.get(BASE + "/operations/missing").status_code == 404
    h.client.headers.pop("Authorization")
    assert h.client.get(url).status_code == 401
    h.client.cookies.set(api.COOKIE_NAME, h.token)
    assert h.client.get(url).status_code == 200
    update = {"definition": job_def(h), "expectedRevision": 1, "requestKey": "update"}
    assert h.client.put(url, json=update).status_code == 403
    assert h.client.put(url, json=update, headers={"Origin": "http://evil"}).status_code == 403
    assert h.client.put(url, json=update, headers={"Origin": "http://testserver"}).status_code == 200
    assert h.client.put(url, json={**update, "requestKey": "stale"}, headers={"Origin": "http://testserver"}).status_code == 409


def test_ft01_concurrent_cas_allows_exactly_one_writer(h):
    item = create(h)
    def change(index):
        try:
            h.service.write("job", item["id"], {"definition": {**job_def(h), "name": str(index)},
                "expectedRevision": 1, "requestKey": "update-" + str(index)}, actor=USER)
            return "saved"
        except fc.ContractError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(change, [1, 2])) == ["revision_conflict", "saved"]


def test_ft01_failed_creator_or_cross_reference_rolls_back_every_table(h, monkeypatch):
    creator_def = time_definition("clock", config([interval()]))
    original = h.service.creators.create
    def broken(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("synthetic storage fault")
    monkeypatch.setattr(h.service.creators, "create", broken)
    response = h.client.post(BASE + "/event-creators", json={
        "definition": creator_def, "expectedRevision": 0, "requestKey": "fault"})
    assert response.status_code == 503
    assert h.service.creators.list() == []
    with h.service.transaction() as db:
        assert db.execute("SELECT count(*) FROM final_operations").fetchone()[0] == 0


def test_ft03_old_facade_remains_at_legacy_urls_with_canonical_registered(h):
    from packages.jobs.api import router as old
    from packages.jobs.action_api import router as action_old
    before, after = FastAPI(), FastAPI()
    before.include_router(action_old)
    before.include_router(old)
    api.install(after, action_old, old)
    with TestClient(before) as a, TestClient(after) as b:
        for method, path, payload in [("GET", "/api/jobs", None), ("GET", "/api/jobs/kinds", None),
                                      ("POST", "/api/jobs", {"kind": "unrecognized"}),
                                      ("PATCH", "/api/jobs/missing", {"name": "x"}),
                                      ("DELETE", "/api/jobs/missing", None)]:
            old_response = a.request(method, path, json=payload)
            new_response = b.request(method, path.replace("/api/jobs", "/api/legacy/jobs"), json=payload)
            assert new_response.status_code == old_response.status_code
            assert new_response.json() == old_response.json()
    assert "/api/legacy/jobs" in after.openapi()["paths"]


def test_ft01_core_event_enters_canonical_trigger_port_and_automation_receipt(h):
    item = enabled(h)
    trigger = create(h, "triggers", {"name": "idle", "eventTypes": ["workers.all_idle"],
        "creatorSelector": {"mode": "registeredType", "providerType": "core"}, "targetJobId": item["id"],
        "condition": {"field": "workers.runningCount", "op": "eq", "value": 0}}, "trigger")
    assert control(h, "triggers", trigger, "enable", "trigger-enable").status_code == 200
    async def deliver():
        svc = h.automation
        svc.events.initialize()
        svc.started = True
        try:
            event = svc.bus.prepare(svc.core, "workers.all_idle", {"runningCount": 0}, "owned-event")
            receipt = await svc.accept(event, svc.core)
            assert receipt["persisted"]
            row = svc.events.pending()[0]
            assert row["targets"][0]["_final"] is True
            await svc.deliver(row)
            await svc.deliver(row)
        finally:
            svc.started = False
    asyncio.run(deliver())
    assert len(h.service.final.runs(item["id"])) == 1
    assert len(h.automation.ledger.read()["finalAdmissions"]) == 1
    assert h.provider.calls == []  # Admission is not an effect host.
    asyncio.run(h.service.dispatch_once())
    assert len(h.provider.calls) == 1


def test_ft01_new_trigger_never_consumes_old_wp04_event(h):
    item = enabled(h)
    creator_def = time_definition("clock", config([interval()]))
    creator = create(h, "event-creators", creator_def, "creator")
    assert control(h, "event-creators", creator, "enable", "creator-enable").status_code == 200
    point = h.service.creators.get(creator["id"])["cursor"]["rules"]["daily"]["nextFireAt"]
    assert asyncio.run(h.service.creator_runtime.tick(at=point)) == 1
    trigger = create(h, "triggers", {"name": "late", "eventTypes": creator_def["eventTypes"],
        "creatorSelector": {"mode": "ids", "ids": [creator["id"]]}, "targetJobId": item["id"]}, "late-trigger")
    assert control(h, "triggers", trigger, "enable", "late-enable").status_code == 200
    asyncio.run(h.service.tick())
    assert h.service.final.runs(item["id"]) == []


def test_ft01_action_disable_after_admission_prevents_effect(h):
    item = enabled(h)
    receipt = launch(h, item)
    with h.service.transaction() as db:
        action = h.service._get(db, "action", h.action["id"])
        action["enabled"] = False
        h.service._save(db, "action", action["id"], action)
    asyncio.run(h.service.dispatch_once())
    assert h.provider.calls == []
    assert h.service.get_run(receipt["runId"], actor=USER)["state"] == "failed"


def test_ft01_definition_retire_preserves_already_admitted_run(h):
    item = enabled(h)
    receipt = launch(h, item)
    assert control(h, "jobs", item, "retire", "retire").status_code == 200
    asyncio.run(h.service.dispatch_once())
    assert len(h.provider.calls) == 1
    assert h.service.get_run(receipt["runId"], actor=USER)["state"] == "completed"
    response = h.client.post(BASE + "/jobs/" + item["id"] + "/runs", json={
        "expectedRevision": 1, "requestKey": "another", "input": {}, "cause": {"type": "manual"}})
    assert response.status_code == 409


def test_ft01_forbid_is_durable_skipped_admission_not_a_second_run(h):
    item = enabled(h)
    first = launch(h, item)
    response = h.client.post(BASE + "/jobs/" + item["id"] + "/runs", json={
        "expectedRevision": 1, "requestKey": "overlap", "input": {}, "cause": {"type": "manual"}})
    assert response.status_code == 200, response.text
    assert response.json()["result"]["skipped"] is True
    assert "runId" not in response.json()
    assert len(h.service.final.runs(item["id"])) == 1
    assert len(h.service.final.get(item["id"])["admissions"]) == 2
    assert first["runId"] == h.service.final.runs(item["id"])[0]["runId"]


def test_ft01_creator_runtime_rechecks_lifecycle_grant_before_new_publication(h):
    creator = create(h, "event-creators", time_definition("clock", config([interval()])), "creator")
    grants = h.automation.auth._default_permissions()
    grants["eventCreator"]["lifecycle"] = True
    h.automation.auth.change_permissions(grants, 0)
    request = {"operation": "enable", "expectedRevision": 1, "expectedStateRevision": 0,
               "requestKey": "agent-enable", "parameters": {}}
    h.service.control("event_creator", creator["id"], request, actor={"type": "agent", "id": "agent", "revision": 1})
    h.automation.auth.change_permissions(h.automation.auth._default_permissions(), 1)
    point = h.service.creators.get(creator["id"])["cursor"]["rules"]["daily"]["nextFireAt"]
    assert asyncio.run(h.service.creator_runtime.tick(at=point)) == 0
    assert h.service.events.page() == []
    assert h.service.creators.get(creator["id"])["producedCount"] == 0


def test_ft01_real_agent_http_uses_existing_matrix_and_scoped_operations(h):
    async def trusted(scope, receive, send):
        scope[authorization._SCOPE_KEY] = authorization._MARKER
        await h.app(scope, receive, send)
    with TestClient(trusted) as agent:
        assert agent.get(BASE + "/jobs").status_code == 403
        grants = h.automation.auth._default_permissions()
        grants["job"].update(read=True, create=True, updateConfig=True, lifecycle=True, execute=True, delete=True)
        h.automation.auth.change_permissions(grants, 0)
        response = agent.post(BASE + "/jobs", json={"definition": job_def(h), "expectedRevision": 0, "requestKey": "agent-create"})
        assert response.status_code == 201, response.text
        item = response.json()["result"]["object"]
        assert item["createdBy"]["type"] == "agent"
        assert agent.get(BASE + "/jobs/" + item["id"]).status_code == 200
        operation = response.json()["operationId"]
        assert agent.get(BASE + "/operations/" + operation).status_code == 200
        assert h.client.get(BASE + "/operations/" + operation).status_code == 404
        grants["job"]["read"] = False
        h.automation.auth.change_permissions(grants, 1)
        assert agent.get(BASE + "/operations/" + operation).status_code == 403


def test_ft01_bound_background_driver_survives_reload_without_resend(h):
    item = enabled(h)
    receipt = launch(h, item)
    async def drive():
        await h.service.start()
        try:
            for _ in range(100):
                if h.service.get_run(receipt["runId"], actor=USER)["state"] == "completed":
                    return
                await asyncio.sleep(0.01)
            pytest.fail("Canonical background driver did not settle its queued Run")
        finally:
            await h.service.close()
    asyncio.run(drive())
    assert len(h.provider.calls) == 1
    restored = FinalService(h.root, h.automation.auth, executor=h.service.executor)
    async def restart():
        await restored.start()
        try:
            await restored.dispatch_once()
        finally:
            await restored.close()
    asyncio.run(restart())
    assert len(h.provider.calls) == 1


def test_ft02_unbound_automatic_recovery_is_explicit_unsupported(h):
    response = h.client.post(BASE + "/jobs", json={"definition": job_def(h, recovery={"automatic": True}),
        "expectedRevision": 0, "requestKey": "automatic"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "capability_unsupported"
    assert h.client.get(BASE + "/jobs").json()["items"] == []


def test_ft01_recovery_never_reports_lost_cancelling_effect_as_cancelled(h):
    item = enabled(h)
    receipt = launch(h, item)
    rid = receipt["runId"]
    # Model a host loss after intent commit and cancel acceptance, before a
    # provider receipt. No external effect is actually sent by this test.
    with h.service.transaction() as db:
        job = h.service._get(db, "job", item["id"])
        ledger = h.service.final.ledger
        ledger.start(job, h.root, rid)
        attempt = ledger.begin_attempt(job, h.root, rid, "s0")
        ledger.admit_effect(job, h.root, attempt["attemptId"], h.action["id"])
        h.service._save(db, "job", item["id"], job)
    cancellation = run_control(h, rid, "cancel", "cancel")
    assert cancellation.status_code == 202
    restored = FinalService(h.root, h.automation.auth, executor=h.service.executor)
    restored.recover()
    asyncio.run(restored.dispatch_once())
    assert restored.get_run(rid, actor=USER)["state"] == "uncertain"
    assert restored.operation(cancellation.json()["operationId"], actor=USER)["status"] == "unknown"
    assert h.provider.calls == []


def test_ft01_queue_keeps_admission_order_after_sorted_json_reload(h):
    item = enabled(h, job_def(h, overlap={"mode": "queue", "maxActive": 1, "maxPending": 4}))
    receipts = [launch(h, item, "run-" + str(index)) for index in range(3)]
    # Make random ID ordering deterministically disagree with admission order.
    with h.service.transaction() as db:
        job = h.service._get(db, "job", item["id"])
        state = job["finalRuntime"]
        ids = sorted(receipt["runId"] for receipt in receipts)
        for index, rid in enumerate(reversed(ids)):
            state["runs"][rid]["createdAt"] = "2026-10-26T00:00:0" + str(index) + "Z"
        h.service._save(db, "job", item["id"], job)
    restored = FinalService(h.root, h.automation.auth, executor=h.service.executor)
    asyncio.run(restored.dispatch_once())
    assert restored.get_run(ids[-1], actor=USER)["state"] == "completed"
    assert restored.get_run(ids[0], actor=USER)["state"] == "queued"
    assert len(h.provider.calls) == 1


def test_ft01_unknown_effect_holds_queue_capacity_but_queued_cancel_can_settle(h):
    item = enabled(h, job_def(h, overlap={"mode": "queue", "maxActive": 1, "maxPending": 2}))
    first = launch(h, item, "first")
    second = launch(h, item, "second")
    h.provider.unknown = True
    asyncio.run(h.service.dispatch_once())
    assert h.service.get_run(first["runId"], actor=USER)["state"] == "uncertain"
    asyncio.run(h.service.dispatch_once())
    assert h.service.get_run(second["runId"], actor=USER)["state"] == "queued"
    assert len(h.provider.calls) == 1
    assert run_control(h, second["runId"], "cancel", "cancel-tail").status_code == 202
    asyncio.run(h.service.dispatch_once())
    assert h.service.get_run(second["runId"], actor=USER)["state"] == "cancelled"
    assert len(h.provider.calls) == 1


def test_ft01_event_projection_replay_after_trigger_retire_uses_original_identity(h, monkeypatch):
    item = enabled(h)
    trigger = create(h, "triggers", {"name": "idle", "eventTypes": ["workers.all_idle"],
        "creatorSelector": {"mode": "registeredType", "providerType": "core"}, "targetJobId": item["id"]}, "trigger")
    active = control(h, "triggers", trigger, "enable", "trigger-enable").json()["result"]["object"]
    event = {"id": "fact", "type": "workers.all_idle", "source": {"type": "core"}, "payload": {}}
    project = h.automation.ledger.record_final_admission
    def lost(*args):
        raise OSError("projection unavailable")
    monkeypatch.setattr(h.automation.ledger, "record_final_admission", lost)
    with pytest.raises(OSError):
        h.service.admit_event(event, active)
    assert control(h, "triggers", active, "retire", "retire-trigger").status_code == 200
    monkeypatch.setattr(h.automation.ledger, "record_final_admission", project)
    result = h.service.admit_event(event, active)
    assert result["replayed"]
    assert len(h.service.final.runs(item["id"])) == 1
    projection = h.automation.ledger.read()["finalAdmissions"]["fact:" + trigger["id"]]
    assert projection["runId"] == result["runId"]
    with pytest.raises(fc.ContractError, match="Event fact changed"):
        h.service.admit_event({**event, "payload": {"changed": True}}, active)


@pytest.mark.parametrize("kind", ["triggers", "event-creators", "job-templates"])
def test_ft01_all_definition_kinds_update_retire_and_reload_over_http(h, kind):
    job = enabled(h)
    values = {
        "triggers": {"name": "trigger", "eventTypes": ["workers.all_idle"],
            "creatorSelector": {"mode": "registeredType", "providerType": "core"}, "targetJobId": job["id"]},
        "event-creators": time_definition("clock", config([interval()])),
        "job-templates": {"name": "template", "jobDefinition": job_def(h), "composition": {"creators": [], "triggers": []}},
    }
    item = create(h, kind, values[kind], "definition-create")
    url = BASE + "/" + kind + "/" + item["id"]
    assert h.client.get(url).json() == item
    assert any(row["id"] == item["id"] for row in h.client.get(BASE + "/" + kind).json()["items"])
    payload = {"definition": {**values[kind], "name": "edited"}, "expectedRevision": 1, "requestKey": "definition-update"}
    update = h.client.put(url, json=payload)
    assert update.status_code == 200, update.text
    assert update.json()["result"]["object"]["revision"] == 2
    assert h.client.put(url, json=payload).json()["operationId"] == update.json()["operationId"]
    assert h.client.put(url, json={**payload, "requestKey": "stale-definition"}).status_code == 409
    retirement = h.client.request("DELETE", url, json={"expectedRevision": 2,
        "expectedStateRevision": 0, "requestKey": "definition-retire"})
    assert retirement.status_code == 200, retirement.text
    assert h.client.get(url).json()["state"] == "retired"
    restored = FinalService(h.root, h.automation.auth)
    assert restored.get(api.KINDS[kind], item["id"], actor=USER)["state"] == "retired"
