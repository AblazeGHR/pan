"""Thin published time Template admission over the existing shared database."""
import copy
import json

import pytest

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService, definition
from packages.scheduler.providers import time_definition
from tests.test_job_final_http_service import h, job_def, USER
from tests.test_job_final_creators import config


def template(h, *, publish=True, specs=None):
    source = time_definition("host day", config([{"ruleId": "day", "type": "daily", "time": "09:00:07"}]))
    value = {"name": "Time template", "jobDefinition": job_def(h), "composition": {
        "creators": specs or [{"mode": "create", "key": "clock", "definition": source}],
        "triggers": [{"key": "tick", "targetJobKey": "job", "creatorKeys": ["clock"], "definition": {
            "name": "tick", "targetJobId": "job", "creatorSelector": {"mode": "ids", "ids": ["clock"]},
            "eventTypes": ["scheduler.time.v2"]}}]}}
    result = h.client.post("/api/job-templates", json={"definition": value, "expectedRevision": 0, "requestKey": "template"})
    assert result.status_code == 201, result.text
    item = result.json()["result"]["object"]
    if publish:
        result = h.client.post(f"/api/job-templates/{item['id']}/operations", json={"operation": "publish",
            "expectedRevision": 1, "expectedStateRevision": 0, "parameters": {}, "requestKey": "publish"})
        assert result.status_code == 200, result.text
        item = result.json()["result"]["object"]
    return item


def request(item, **kwargs):
    return {"expectedRevision": item["revision"], "requestKey": "instantiate", **kwargs}


def test_default_disabled_graph_lost_receipt_query_and_cold_reopen(h):
    item = template(h)
    url = f"/api/job-templates/{item['id']}/instantiate"
    response = h.client.post(url, json=request(item))
    assert response.status_code == 201, response.text
    receipt = response.json()
    ids = receipt["result"]["objectIds"]
    for kind, oid in [("jobs", ids["job"]), ("triggers", ids["triggers"]["tick"]),
                      ("event-creators", ids["creators"]["clock"])]:
        assert h.client.get(f"/api/{kind}/{oid}").json()["state"] == "disabled"
    assert h.client.get(f"/api/jobs/{ids['job']}/runs").json()["items"] == []
    assert not h.provider.calls
    lookup = h.client.get("/api/operations", params={"operation": "instantiate-template", "requestKey": "instantiate"})
    assert lookup.json() == receipt
    fresh = FinalService(h.root, h.automation.auth, executor=h.service.executor)
    assert fresh.operation_for_key("instantiate-template", "instantiate", actor=USER) == receipt
    replay = fresh.instantiate_time_template(item["id"], request(item), actor=USER)
    assert replay["replayed"] and replay["operationId"] == receipt["operationId"]
    assert replay["result"]["objectIds"] == ids
    assert len(fresh.creators.list()) == 1
    changed = h.client.post(url, json=request(item, activate=True))
    assert changed.status_code == 409


@pytest.mark.parametrize("failure", ["draft", "stale", "bad-activate"])
def test_invalid_request_does_not_create_graph(h, failure):
    item = template(h, publish=failure != "draft")
    body = request(item)
    if failure == "stale": body["expectedRevision"] -= 1
    if failure == "bad-activate": body["activate"] = "true"
    result = h.client.post(f"/api/job-templates/{item['id']}/instantiate", json=body)
    assert result.status_code in (400, 409, 422), result.text
    assert h.service.creators.list() == []


def test_explicit_activation_authorities_and_no_immediate_run(h):
    item = template(h)
    result = h.client.post(f"/api/job-templates/{item['id']}/instantiate", json=request(item, activate=True))
    assert result.status_code == 201, result.text
    ids = result.json()["result"]["objectIds"]
    assert h.client.get(f"/api/jobs/{ids['job']}").json()["state"] == "enabled"
    source = h.service.creators.get(ids["creators"]["clock"])
    h.service._guard_creator(source)
    assert source["state"] == "enabled"
    assert not h.provider.calls
    assert h.client.get(f"/api/jobs/{ids['job']}/runs").json()["items"] == []


@pytest.mark.parametrize("missing,activate", [(('template', 'use'), False), (('job', 'create'), False),
    (('event_creator', 'create'), False), (('trigger', 'create'), False),
    (('event_creator', 'lifecycle'), True), (('trigger', 'lifecycle'), True),
    (('job', 'lifecycle'), True), (('job', 'execute'), True)])
def test_actual_graph_grants_not_inferred_from_template_use(h, monkeypatch, missing, activate):
    item = template(h)
    calls = []
    def permit(actor, kind, capability, **kwargs):
        calls.append((kind, capability))
        if (kind, capability) == missing:
            raise fc.ContractError("authorization_denied", "test denial")
        return 1
    monkeypatch.setattr(h.service, "_permit", permit)
    with pytest.raises(fc.ContractError, match="test denial"):
        h.service.instantiate_time_template(item["id"], request(item, activate=activate), actor={"type": "agent", "id": "test", "revision": 1})
    assert missing in calls
    assert h.service.creators.list() == []


def test_shared_source_reference_checked_and_never_enabled_by_instantiation(h):
    from packages.jobs.final_service import object_ref
    source = h.service.creators.create(time_definition("shared", config([{"ruleId": "d", "type": "daily", "time": "09:00:00", "timezone": "UTC"}])), request_key="shared", state="disabled", created_by=USER, owner=USER)
    item = template(h, specs=[{"mode": "reference", "key": "clock", "ref": h.service.creators.ref(source["id"])}])
    result = h.client.post(f"/api/job-templates/{item['id']}/instantiate", json=request(item))
    assert result.status_code == 201, result.text
    assert result.json()["result"]["objectIds"]["creators"]["clock"] == source["id"]
    assert h.service.creators.get(source["id"])["state"] == "disabled"
    assert len(h.service.creators.list()) == 1


def test_joined_transaction_failure_rolls_back_and_same_key_recovers(h, monkeypatch):
    item = template(h)
    coordinator = h.service.compositions
    original = coordinator._checkpoint
    def fail(operation, phase):
        result = original(operation, phase)
        if phase == "links_verified": raise OSError("injected before activation")
        return result
    monkeypatch.setattr(coordinator, "_checkpoint", fail)
    result = h.client.post(f"/api/job-templates/{item['id']}/instantiate", json=request(item))
    assert result.status_code == 503
    assert not h.service.creators.list()
    with h.service.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM compositions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0
    monkeypatch.setattr(coordinator, "_checkpoint", original)
    recovered = h.client.post(f"/api/job-templates/{item['id']}/instantiate", json=request(item))
    assert recovered.status_code == 201, recovered.text
    assert len(h.service.creators.list()) == 1


@pytest.mark.parametrize("phase", ["reserved", "disabled_objects_created", "links_verified"])
def test_preexisting_composition_checkpoint_resumes_same_ids(h, monkeypatch, phase):
    item = template(h)
    key = "template:" + fc.digest({"scope": h.service._scope(USER, "instantiate-template"), "requestKey": "instantiate"})
    value = definition("template", item)
    operation = h.service.compositions.reserve(request_key=key, desired_graph={"job": value["jobDefinition"],
        **value["composition"]}, expected_refs=[], authorization_evidence={"principal": USER},
        activate=False, operation="instantiate-template")
    before = operation["objectIds"]
    if phase != "reserved":
        original = h.service.compositions._checkpoint
        def crash(value, next_phase):
            result = original(value, next_phase)
            if next_phase == phase: raise OSError("crash after committed checkpoint")
            return result
        monkeypatch.setattr(h.service.compositions, "_checkpoint", crash)
        with pytest.raises(OSError):
            h.service.compositions.compose(request_key=key, desired_graph={"job": value["jobDefinition"],
                **value["composition"]}, expected_refs=[], authorization_evidence={"principal": USER},
                activate=False, operation="instantiate-template")
        assert h.service.creators.composition_for_key(key)["phase"] == phase
    fresh = FinalService(h.root, h.automation.auth, executor=h.service.executor)
    receipt = fresh.instantiate_time_template(item["id"], request(item), actor=USER)
    assert receipt["result"]["objectIds"] == before
    assert fresh.creators.composition_for_key(key)["phase"] == "complete"
    assert len(fresh.creators.list()) == 1


def test_real_delegated_profile_grants_and_revoke(h):
    import asyncio
    import httpx
    from packages.automation import authorization
    item = template(h)
    async def trusted(scope, receive, send):
        scope[authorization._SCOPE_KEY] = authorization._MARKER
        await h.app(scope, receive, send)
    async def check():
        grants = h.automation.auth._default_permissions()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=trusted), base_url="http://testserver") as client:
            url = f"/api/job-templates/{item['id']}/instantiate"
            assert (await client.post(url, json=request(item))).status_code == 403
            for kind, capability in [("jobTemplate", "use"), ("job", "create"), ("eventCreator", "create")]:
                grants[kind][capability] = True
            h.automation.auth.change_permissions(grants, h.automation.auth.permissions_status()["revision"])
            assert (await client.post(url, json=request(item))).status_code == 403
            grants["trigger"]["create"] = True
            h.automation.auth.change_permissions(grants, h.automation.auth.permissions_status()["revision"])
            result = await client.post(url, json=request(item))
            assert result.status_code == 201, result.text
            assert (await client.post(url, json=request(item, requestKey="active", activate=True))).status_code == 403
            grants["jobTemplate"]["use"] = False
            h.automation.auth.change_permissions(grants, h.automation.auth.permissions_status()["revision"])
            assert (await client.post(url, json=request(item, requestKey="revoked"))).status_code == 403
    asyncio.run(check())
    assert len(h.service.creators.list()) == 1
