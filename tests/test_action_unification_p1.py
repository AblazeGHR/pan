"""P1 canonical Action directory and immutable version contracts."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.automation import service as automation_service
from packages.jobs import final_api
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import bind
from tests.test_job_final_contracts import action_definition


@pytest.fixture
def action_http(tmp_path):
    app = FastAPI()
    automation = automation_service.Service(tmp_path / "automation", SimpleNamespace(workers={}), None)
    automation.auth = final_api.CanonicalUserAuth(tmp_path / "automation")
    service = bind(app, automation, tmp_path / "canonical")
    app.state.automation = automation
    final_api.install(app)
    token = automation.auth.login(automation.auth.key())
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer " + token
    try:
        yield client, service
    finally:
        client.close()
        asyncio.run(service.close())


def shell_definition(name="Shell descriptor", *, system=False, command="echo hello"):
    value = action_definition(
        name=name,
        system=system,
        effectClass="write_local",
        requiredCapabilities=[],
        execution={"operation": "process.shell", "config": {
            "command": command,
            "cwd": "C:/owned/workspace",
            "interpreter": "system-default",
            "externalScript": {"path": "C:/owned/scripts/task.ps1", "contentDigest": "a" * 64},
        }},
    )
    return value


def create_draft(client, definition, key="create"):
    response = client.post("/api/jobs/actions", json={
        "definition": definition, "expectedRevision": 0, "requestKey": key,
    })
    assert response.status_code == 201, response.text
    return response.json()


def control(client, action_id, operation, revision, state_revision, key, parameters=None):
    response = client.post(f"/api/jobs/actions/{action_id}/operations", json={
        "operation": operation,
        "expectedRevision": revision,
        "expectedStateRevision": state_revision,
        "requestKey": key,
        "parameters": parameters or {},
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_p1_draft_publish_versions_fixed_refs_and_shell_descriptor(action_http):
    client, _ = action_http
    created = create_draft(client, shell_definition())
    action = created["result"]["object"]
    action_id = action["id"]
    assert action["state"] == "draft"
    assert action["revision"] == 1

    published = control(client, action_id, "publish", 1, 0, "publish-1")
    first = published["result"]["version"]
    assert first["revision"] == 1
    assert len(first["digest"]) == 64

    edited = client.put(f"/api/jobs/actions/{action_id}", json={
        "definition": shell_definition(command="echo second"),
        "expectedRevision": 1,
        "requestKey": "edit-2",
    })
    assert edited.status_code == 200, edited.text
    assert edited.json()["result"]["object"]["state"] == "draft"
    assert edited.json()["result"]["object"]["revision"] == 2
    second_published = control(client, action_id, "publish", 2, 2, "publish-2")
    second = second_published["result"]["version"]
    assert second["revision"] == 2 and second["digest"] != first["digest"]

    versions = client.get(f"/api/jobs/actions/{action_id}/versions").json()["items"]
    assert [item["revision"] for item in versions] == [1, 2]
    old = client.get(f"/api/jobs/actions/{action_id}/versions/1", params={"digest": first["digest"]})
    assert old.status_code == 200, old.text
    assert old.json()["definition"]["execution"]["operation"] == "process.shell"
    assert old.json()["definition"]["execution"]["config"]["externalScript"]["contentDigest"] == "a" * 64
    assert client.get(f"/api/jobs/actions/{action_id}/versions/99").status_code == 404


def test_p1_cas_idempotency_and_version_lifecycle(action_http):
    client, _ = action_http
    created = create_draft(client, shell_definition(), "create-cas")
    action_id = created["result"]["object"]["id"]
    control(client, action_id, "publish", 1, 0, "publish-cas")

    stale = client.put(f"/api/jobs/actions/{action_id}", json={
        "definition": shell_definition(command="stale"), "expectedRevision": 1, "requestKey": "edit-cas",
    })
    assert stale.status_code == 200
    replay = client.put(f"/api/jobs/actions/{action_id}", json={
        "definition": shell_definition(command="stale"), "expectedRevision": 1, "requestKey": "edit-cas",
    })
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    conflict = client.put(f"/api/jobs/actions/{action_id}", json={
        "definition": shell_definition(command="different"), "expectedRevision": 1, "requestKey": "edit-cas",
    })
    assert conflict.status_code == 409
    assert client.put(f"/api/jobs/actions/{action_id}", json={
        "definition": shell_definition(command="stale-2"), "expectedRevision": 1, "requestKey": "stale-cas",
    }).status_code == 409

    control(client, action_id, "publish", 2, 2, "publish-cas-2")
    versions = client.get(f"/api/jobs/actions/{action_id}/versions").json()["items"]
    first = versions[0]
    disabled = control(client, action_id, "disable", 2, 3, "disable-old", {"revision": 1})
    assert disabled["result"]["version"]["revision"] == 1
    assert client.get(f"/api/jobs/actions/{action_id}/versions/1").json()["enabled"] is False
    retired = control(client, action_id, "retire", 2, 3, "retire-old", {"revision": 1})
    assert retired["result"]["version"]["state"] == "retired"
    fixed = client.get(f"/api/jobs/actions/{action_id}/versions/1", params={"digest": first["digest"]})
    assert fixed.status_code == 200 and fixed.json()["state"] == "retired"


def test_p1_system_action_protection_and_legacy_alias_mapping(action_http):
    client, _ = action_http
    forbidden = client.post("/api/jobs/actions", json={
        "definition": shell_definition(system=True), "expectedRevision": 0, "requestKey": "system-user",
    })
    assert forbidden.status_code == 403

    created = create_draft(client, shell_definition(), "alias-create")
    action_id = created["result"]["object"]["id"]
    published = control(client, action_id, "publish", 1, 0, "alias-publish")
    version = published["result"]["version"]
    mapped = client.post(f"/api/jobs/actions/{action_id}/aliases", json={
        "legacyId": "legacy_shell_001", "revision": 1, "digest": version["digest"],
        "source": "legacy-file", "requestKey": "alias-1",
    })
    assert mapped.status_code == 200, mapped.text
    alias = client.get("/api/jobs/actions/aliases/legacy_shell_001")
    assert alias.status_code == 200
    assert alias.json()["actionRef"] == {"id": action_id, "revision": 1, "digest": version["digest"]}
    conflict = client.post(f"/api/jobs/actions/{action_id}/aliases", json={
        "legacyId": "legacy_shell_001", "revision": 1, "digest": "b" * 64,
        "source": "legacy-file", "requestKey": "alias-conflict",
    })
    assert conflict.status_code == 409


def test_p1_concurrent_publish_uses_owner_fenced_cas(action_http):
    _, service = action_http
    created = service.write("action", None, {
        "definition": shell_definition(), "expectedRevision": 0, "requestKey": "concurrent-create",
    }, actor={"type": "user", "id": "local"})
    action_id = created["result"]["object"]["id"]

    def publish(key):
        try:
            return service.control("action", action_id, {
                "operation": "publish", "expectedRevision": 1, "expectedStateRevision": 0,
                "requestKey": key, "parameters": {},
            }, actor={"type": "user", "id": "local"})
        except fc.ContractError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, ("concurrent-a", "concurrent-b")))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(result == "revision_conflict" for result in results) == 1
