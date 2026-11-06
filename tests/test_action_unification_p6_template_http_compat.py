"""P6 old Template HTTP/MCP shape -> one canonical FinalService ledger."""
import asyncio
import pytest

from packages.jobs import final_contracts as fc

from tests.test_job_final_http_service import h


def _legacy_item(action_id):
    return {
        "id": "jtpl_legacy_http",
        "name": "Legacy HTTP Template",
        "description": "preserve me",
        "config": {
            "name": "Legacy HTTP Template",
            "actionId": action_id,
            "arguments": {"sessionId": "owned-test-session", "text": "legacy"},
            "enabled": False,
            "schedule": [],
            "trigger": {"kind": "manual"},
        },
        "referenceGeneration": {action_id: 1},
    }


def test_old_template_http_crud_display_alias_and_cold_query(h):
    item = _legacy_item(h.action["id"])
    created = h.client.post("/api/jobs/job-templates", json={"item": item, "expectedRevision": 0})
    assert created.status_code == 200, created.text
    assert created.json()["item"]["id"] == item["id"]
    listed = h.client.get("/api/jobs/job-templates")
    assert listed.status_code == 200
    assert listed.json()["templates"][0]["id"] == item["id"]
    canonical = h.client.get("/api/job-templates/" + item["id"])
    assert canonical.status_code == 200
    published = h.client.post("/api/job-templates/" + item["id"] + "/operations", json={
        "operation": "publish", "expectedRevision": 1, "expectedStateRevision": 0,
        "requestKey": "publish-legacy-http", "parameters": {}})
    assert published.status_code == 200, published.text
    updated = h.client.put("/api/jobs/job-templates/" + item["id"], json={"item": {
        **item, "name": "Legacy HTTP Template v2", "config": {**item["config"], "name": "Legacy HTTP Template v2"}},
        "expectedRevision": 1})
    assert updated.status_code == 200, updated.text
    assert updated.json()["item"]["id"] == item["id"] and updated.json()["item"]["revision"] == 2
    instantiated = h.client.post("/api/jobs/job-templates/" + item["id"] + "/instantiate", json={
        "expectedRevision": 2, "idempotencyKey": "legacy-instance", "overrides": {"name": "Display override"}})
    assert instantiated.status_code in (200, 201), instantiated.text
    job_id = instantiated.json()["job"]["jobId"]
    assert job_id
    replay = h.client.post("/api/jobs/job-templates/" + item["id"] + "/instantiate", json={
        "expectedRevision": 2, "idempotencyKey": "legacy-instance", "overrides": {"name": "Display override"}})
    assert replay.status_code == 200
    assert replay.json()["job"]["jobId"] == job_id
    cold = type(h.service)(h.root, h.automation.auth)
    try:
        assert cold.operation_for_key("instantiate-template", "legacy-instance", actor={"type": "user", "id": "local"})["operationId"]
    finally:
        asyncio.run(cold.close())


def test_old_event_template_creator_selector_and_query_are_composed(h):
    from packages.scheduler.providers import time_definition
    creator = h.service.creators.create(time_definition("legacy source", {"rules": [{"ruleId": "r", "type": "interval", "seconds": "60", "anchor": "2026-01-01T00:00:00Z", "advanceMode": "grid"}],
        "misfirePolicy": "fire_now", "graceSeconds": "1", "pausePolicy": "advance"}),
        request_key="legacy-event-source", state="disabled", created_by={"type": "user", "id": "local"},
        owner={"type": "user", "id": "local"})
    item = _legacy_item(h.action["id"])
    item["id"] = "jtpl_legacy_event"
    item["config"]["trigger"] = {"kind": "event", "eventTypes": ["legacy.event"],
        "creatorSelector": {"mode": "ids", "ids": [creator["id"]]},
        "query": {"tenant": "owned"}}
    response = h.client.post("/api/jobs/job-templates", json={"item": item, "expectedRevision": 0})
    assert response.status_code == 200, response.text
    canonical = h.client.get("/api/job-templates/" + item["id"]).json()
    trigger = canonical["composition"]["triggers"][0]["definition"]
    assert trigger["eventTypes"] == ["legacy.event"]
    assert trigger["eventFilter"] == {"tenant": "owned"}
    assert trigger["creatorSelector"]["mode"] == "ids"
    assert canonical["composition"]["creators"][0]["mode"] == "reference"


def test_old_unknown_template_shape_is_blocked_without_canonical_row(h):
    item = _legacy_item(h.action["id"])
    item["id"] = "jtpl_unknown_shape"
    item["config"]["trigger"] = {"kind": "event", "eventTypes": ["legacy.event"],
        "creatorSelector": {"mode": "unknown-writer", "writer": "x"}}
    with pytest.raises(fc.ContractError, match="creator selector"):
        h.service.legacy_template_write(item, 0, "unknown-template", actor={"type": "user", "id": "local"})
    assert h.client.get("/api/job-templates/" + item["id"]).status_code == 404
