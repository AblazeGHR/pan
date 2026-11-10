"""Canonical Action/Group identity and retired legacy boundary tests."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from packages.actions.command_execution import product_definition
from packages.actions.groups import Groups
from packages.web import server as srv
from tests.test_action_groups import setup
from tests.test_job_final_http_service import h, USER

def definition(name="identity-action"):
    value = product_definition("process.shell")
    value.update(name=name, description="canonical identity test", system=False)
    value["execution"]["config"] = {"command": "echo identity"}
    return value

def create(service, name, key):
    return service.write("action", None, {"definition": definition(name), "expectedRevision": 0, "requestKey": key}, actor=USER)["result"]["object"]

def test_generated_canonical_action_ids_are_unique_and_stable(setup):
    service, _ = setup
    first = create(service, "First", "identity-first")
    second = create(service, "Second", "identity-second")
    assert first["id"].startswith("action_") and second["id"].startswith("action_")
    assert first["id"] != second["id"]
    edited = service.write("action", first["id"], {"definition": definition("Renamed"), "expectedRevision": first["revision"], "requestKey": "identity-edit"}, actor=USER)["result"]["object"]
    assert edited["id"] == first["id"] and edited["revision"] == first["revision"] + 1

def test_concurrent_canonical_action_creates_do_not_overwrite(setup):
    service, _ = setup
    with ThreadPoolExecutor(max_workers=6) as pool:
        items = list(pool.map(lambda i: create(service, "same-name", f"identity-{i}"), range(24)))
    assert len({item["id"] for item in items}) == 24

def test_explicit_canonical_group_id_preserves_release_identity(setup):
    service, _ = setup
    action = create(service, "group-action", "group-action")
    service.control("action", action["id"], {"operation": "publish", "expectedRevision": action["revision"], "expectedStateRevision": action["stateRevision"], "requestKey": "group-publish-action", "parameters": {}}, actor=USER)
    ref = service.action_ref(action["id"], actor=USER)
    groups = Groups(service)
    value = {"id": "identity_group", "name": "Identity Group", "tools": [{"alias": "run", "actionRef": ref}]}
    created = groups.put(value, 0, "identity-group", actor=USER)["result"]["group"]
    released = groups.publish("identity_group", created["revision"], "identity-group-publish", actor=USER)["result"]["release"]
    assert released["groupId"] == "identity_group" and released["tools"][0]["actionRef"] == ref
    with pytest.raises(Exception):
        groups.put(value, 0, "identity-group-reuse", actor=USER)

def test_invalid_canonical_action_definition_writes_nothing(setup):
    service, _ = setup
    before = service.actions(actor=USER)
    with pytest.raises(Exception):
        service.write("action", None, {"definition": {}, "expectedRevision": 0, "requestKey": "bad-action"}, actor=USER)
    assert service.actions(actor=USER) == before

def test_legacy_action_writer_is_retired_and_canonical_http_create_works(setup):
    async def run():
        raw = definition("HTTP canonical")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://isolated") as client:
            retired = await client.post("/api/actions/actions", json={"item": raw})
            assert retired.status_code == 410
            created = await client.post("/api/jobs/actions", json={"definition": raw, "expectedRevision": 0, "requestKey": "http-canonical-action"})
            assert created.status_code == 201
            item = created.json()["result"]["object"]
            assert item["id"].startswith("action_")
            detail = await client.get("/api/jobs/actions/" + item["id"])
            assert detail.status_code == 200 and detail.json()["id"] == item["id"]
    asyncio.run(run())
