"""WP06a HTTP boundary tests; service probes do not prove backend CRUD/effects."""
import asyncio
from collections import Counter
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from packages.automation import authorization
from packages.automation.auth import UserAuth
from packages.jobs import final_api as api, final_contracts as fc
from tests.test_job_final_contracts import (
    job_definition, trigger_definition, creator_definition, template_definition,
)


DEFINITIONS = {"job": job_definition, "trigger": trigger_definition,
               "event_creator": creator_definition, "template": template_definition}


class ServiceProbe:
    """Records the transport-to-service contract; never executes an effect."""
    def __init__(self):
        self.calls = []
        self.failure = None

    def __getattr__(self, method):
        def call(*args, **kwargs):
            self.calls.append((method, args, kwargs))
            if self.failure:
                raise self.failure
            if method in {"list", "runs"}:
                return {"items": [], "nextCursor": None}
            if method == "get":
                kind, object_id = args
                result = fc.validate_definition(kind, DEFINITIONS[kind]())
                result.update(schemaVersion=2, id=object_id, profileId="profile_test",
                              createdBy={"type": "user", "id": "local"},
                              owner={"type": "user", "id": "local"},
                              createdAt="2026-01-01T00:00:00Z", updatedAt="2026-01-01T00:00:00Z",
                              state="draft", stateRevision=0)
                result[fc.VERSION_FIELD[kind]] = 1
                if kind == "event_creator":
                    result.update(ownerEpoch=1, providerGeneration=1)
                return result
            return {"operationId": "op_test", "requestKey": "request", "status": "completed",
                    "objectRefs": [], "links": {"self": "/api/final/operations/op_test"}}
        return call


def harness(tmp_path, *, trusted=False):
    app = FastAPI()
    auth = api.CanonicalUserAuth(tmp_path)
    app.state.automation = SimpleNamespace(auth=auth)
    service = ServiceProbe()
    app.state.job_final_service = service
    app.include_router(api.router)
    token = auth.login(auth.key())
    async def transport(scope, receive, send):
        if trusted:
            scope[authorization._SCOPE_KEY] = authorization._MARKER
        await app(scope, receive, send)
    return app, auth, service, httpx.ASGITransport(app=transport), token


def run(test):
    def wrapped(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    import functools
    return functools.wraps(test)(wrapped)


@pytest.mark.parametrize("path,kind", api.KINDS.items())
@run
async def test_ft02_wp01_create_update_and_read_boundary(tmp_path, path, kind):
    _, _, service, transport, token = harness(tmp_path)
    async with httpx.AsyncClient(transport=transport, base_url="http://test",
                               headers={"Authorization": "Bearer " + token}) as client:
        payload = {"definition": DEFINITIONS[kind](), "expectedRevision": 0, "requestKey": "request"}
        response = await client.post("/api/final/" + path, json=payload)
        assert response.status_code == 201, response.text
        method, args, kwargs = service.calls[-1]
        assert method == "write" and args[:2] == (kind, None)
        assert args[2] == fc.validate_write(kind, payload, create=True)
        assert kwargs["actor"] == {"type": "user", "id": "local"}
        payload["expectedRevision"] = 1
        response = await client.put("/api/final/" + path + "/object", json=payload)
        assert response.status_code == 200
        assert service.calls[-1][1][1] == "object"
        response = await client.get("/api/final/" + path + "/object")
        assert response.status_code == 200
        fc.validate_object(kind, response.json())


@pytest.mark.parametrize("mutation", [
    {"source": "user"}, {"authorization": {"type": "user"}}, {"expectedRevision": True},
    {"requestKey": ""}, {"definition": {"kind": "background-process"}},
    {"definition": {"schemaVersion": 2}}, {"expectedRevision": 1},
])
@run
async def test_ft02_invalid_create_has_no_service_call(tmp_path, mutation):
    _, _, service, transport, token = harness(tmp_path)
    payload = {"definition": job_definition(), "expectedRevision": 0, "requestKey": "request"}
    payload.update(mutation)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/final/jobs", json=payload, headers={"Authorization": "Bearer " + token})
    assert response.status_code in {400, 422}
    assert response.json()["error"]["code"] in {"invalid_schema", "unsupported_schema_version"}
    assert service.calls == []


@pytest.mark.parametrize("raw", ['[]', '{', '{"requestKey":"one","requestKey":"two"}',
                                  '{"value":NaN}', 'null'])
@run
async def test_ft02_strict_json_rejects_before_service(tmp_path, raw):
    _, _, service, transport, token = harness(tmp_path)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/final/jobs", content=raw,
                                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    assert response.status_code == 400
    assert service.calls == []


@pytest.mark.parametrize("headers", [{}, {"X-Pan-Principal": "user"},
                                     {"X-Pan-Source": "system"}, {"Authorization": "Bearer invalid"}])
@run
async def test_ft01_http_headers_never_manufacture_authority(tmp_path, headers):
    _, _, service, transport, _ = harness(tmp_path)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/final/jobs", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert service.calls == []


@pytest.mark.parametrize("path,kind", api.KINDS.items())
@run
async def test_ft01_agent_read_create_update_lifecycle_delete_exact_grants(tmp_path, path, kind):
    _, auth, service, transport, _ = harness(tmp_path, trusted=True)
    definition = DEFINITIONS[kind]()
    control = {"operation": "retire", "expectedRevision": 1, "expectedStateRevision": 0,
               "requestKey": "request", "parameters": {}}
    delete = {key: value for key, value in control.items() if key not in {"operation", "parameters"}}
    cases = [("GET", "", None, "read"),
             ("POST", "", {"definition": definition, "expectedRevision": 0, "requestKey": "request"}, "create"),
             ("PUT", "/object", {"definition": definition, "expectedRevision": 1, "requestKey": "request"}, "updateConfig"),
             ("POST", "/object/operations", control, "updateConfig" if kind == "template" else "lifecycle"),
             ("DELETE", "/object", delete, "delete")]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for method, suffix, value, grant in cases:
            permissions = auth._default_permissions()
            auth.change_permissions(permissions, auth.permissions_status()["revision"])
            before = len(service.calls)
            denied = await client.request(method, "/api/final/" + path + suffix, json=value)
            assert denied.status_code == 403, denied.text
            assert len(service.calls) == before
            permissions[api.GRANT_KINDS[kind]][grant] = True
            auth.change_permissions(permissions, auth.permissions_status()["revision"])
            allowed = await client.request(method, "/api/final/" + path + suffix, json=value)
            assert allowed.status_code in {200, 201}, allowed.text
            assert service.calls[-1][2]["actor"]["type"] == "agent"


@pytest.mark.parametrize("operation", ["retry", "cancel", "pause", "resume", "run-again", "clone-and-run"])
@run
async def test_ft01_run_controls_require_execute_and_clone_create(tmp_path, operation):
    _, auth, service, transport, _ = harness(tmp_path, trusted=True)
    payload = {"operation": operation, "expectedStateRevision": 0, "requestKey": "request"}
    if operation in {"run-again", "clone-and-run"}:
        payload["expectedRevision"] = 1
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/api/final/job-runs/run/operations", json=payload)).status_code == 403
        grants = auth._default_permissions()
        grants["job"]["execute"] = True
        auth.change_permissions(grants, 0)
        result = await client.post("/api/final/job-runs/run/operations", json=payload)
        if operation == "clone-and-run":
            assert result.status_code == 403 and service.calls == []
            grants["job"]["create"] = True
            auth.change_permissions(grants, 1)
            result = await client.post("/api/final/job-runs/run/operations", json=payload)
        assert result.status_code == 200, result.text
        auth.change_permissions(auth._default_permissions(), auth.permissions_status()["revision"])
        assert (await client.post("/api/final/job-runs/run/operations", json=payload)).status_code == 403


@pytest.mark.parametrize("origin,site,expected", [(None, None, 403), ("http://foreign", None, 403),
    ("http://test.evil", None, 403), ("http://test", "cross-site", 403),
    ("http://test:81", None, 403), ("null", None, 403), ("http://test", None, 201),
    ("http://test:80", "same-origin", 201)])
@run
async def test_ft02_cookie_writes_enforce_same_origin(tmp_path, origin, site, expected):
    _, _, service, transport, token = harness(tmp_path)
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    if site is not None:
        headers["Sec-Fetch-Site"] = site
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies={api.COOKIE_NAME: token}) as client:
        assert (await client.get("/api/final/jobs")).status_code == 200
        service.calls.clear()
        response = await client.post("/api/final/jobs", headers=headers,
            json={"definition": job_definition(), "expectedRevision": 0, "requestKey": "request"})
    assert response.status_code == expected, response.text
    assert bool(service.calls) == (expected == 201)


@run
async def test_ft02_invalid_bearer_never_falls_back_to_cookie(tmp_path):
    _, _, _, transport, token = harness(tmp_path)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies={api.COOKIE_NAME: token}) as client:
        assert (await client.get("/api/final/jobs", headers={"Authorization": "Bearer bad"})).status_code == 401


@pytest.mark.parametrize("code", list(fc.ERROR_HTTP_STATUS))
@run
async def test_ft02_contract_errors_keep_wp01_status_and_redact_values(tmp_path, code):
    _, _, service, transport, token = harness(tmp_path)
    service.failure = fc.ContractError(code, "synthetic-secret-body", details={"currentRevision": 7})
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/final/jobs", headers={"Authorization": "Bearer " + token})
    assert response.status_code == fc.ERROR_HTTP_STATUS[code]
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["currentRevision"] == 7
    assert "synthetic-secret-body" not in response.text


@run
async def test_ft02_missing_host_binding_is_503_before_admission(tmp_path):
    app, _, service, transport, token = harness(tmp_path)
    del app.state.job_final_service
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/final/jobs", headers={"Authorization": "Bearer " + token},
            json={"definition": job_definition(), "expectedRevision": 0, "requestKey": "request"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"
    assert service.calls == []


def test_ft04_legacy_permissions_preserved_without_read_write_or_revision_bump(tmp_path):
    original = UserAuth(tmp_path)
    grants = original._default_permissions()
    grants["job"]["execute"] = True
    grants["action"]["manageSystemActions"] = True
    original.change_permissions(grants, 0)
    before = original.permissions_path.read_bytes()
    upgraded = api.CanonicalUserAuth(tmp_path)
    current = upgraded.permissions_status()
    assert current["revision"] == 1
    assert {key: value for key, value in current["permissions"].items() if key != "eventCreator"} == grants
    assert not any(current["permissions"]["eventCreator"].values())
    assert original.permissions_path.read_bytes() == before
    current["permissions"]["eventCreator"]["create"] = True
    upgraded.change_permissions(current["permissions"], 1)
    assert upgraded.permission_allowed("eventCreator", "create") == (True, 2)
    assert upgraded.permission_allowed("job", "execute") == (True, 2)


def test_ft04_unknown_creator_permissions_and_broken_old_grants_fail_closed(tmp_path):
    auth = api.CanonicalUserAuth(tmp_path)
    for key in ("source", "manageSystemActions", "execute"):
        grants = auth._default_permissions()
        grants["eventCreator"][key] = True
        with pytest.raises(ValueError):
            auth.change_permissions(grants, 0)
    grants = auth._default_permissions()
    del grants["job"]["execute"]
    with pytest.raises(ValueError):
        auth.change_permissions(grants, 0)


def test_ft03_one_registration_owner_no_duplicate_or_shadowed_canonical_routes():
    from packages.jobs.action_api import router as action_router
    from packages.jobs.api import router as legacy_router
    app = FastAPI()
    api.install(app, action_router, legacy_router)
    def leaves(routes):
        for route in routes:
            child = getattr(route, "original_router", None)
            if child is not None:
                yield from leaves(child.routes)
            else:
                yield route
    routes = list(leaves(app.routes))
    pairs = Counter((method, route.path) for route in routes if hasattr(route, "methods") for method in route.methods)
    assert max(pairs.values()) == 1
    canonical = [route for route in routes if "canonical-jobs" in getattr(route, "tags", [])]
    assert len(canonical) == 31
    assert pairs[("POST", "/api/final/jobs")] == 1
    assert pairs[("POST", "/api/jobs")] == 1
    assert pairs[("POST", "/api/jobs/action-jobs")] == 1
    assert app.openapi()["paths"]["/api/jobs"]["get"]["deprecated"] is True
    # R3 hasn't removed the old bridge allowlist.
    assert authorization.object_path("/api/background-jobs")
    assert authorization.object_path("/api/jobs/action-jobs")


@run
async def test_ft03_legacy_facade_handler_behavior_preserved(tmp_path):
    from packages.jobs.api import router as old_router
    direct, installed = FastAPI(), FastAPI()
    direct.include_router(old_router)
    api.install(installed, old_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(direct), base_url="http://test") as before:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(installed), base_url="http://test") as after:
            for method, path, value in [("GET", "/api/jobs/kinds", None), ("GET", "/api/jobs", None),
                                        ("POST", "/api/jobs", {"kind": "not-supported"})]:
                old = await before.request(method, path, json=value)
                new = await after.request(method, path, json=value)
                assert new.status_code == old.status_code
                assert new.json() == old.json()
