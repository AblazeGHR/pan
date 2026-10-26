"""Authenticated native source config/reference CAS, no legacy source writer."""
import asyncio
import httpx

from packages.automation import authorization

from packages.scheduler.providers import time_definition
from tests.test_job_final_http_service import h, enabled
from tests.test_job_final_creators import config, interval


def source(h):
    definition = time_definition("shared", config([interval(seconds="0.125")]))
    definition["config"]["driveLimit"] = {"maxEvents":7, "initialProducedCount":0}
    response = h.client.post("/api/event-creators", json={"definition":definition,"expectedRevision":0,"requestKey":"source"})
    assert response.status_code == 201, response.text
    return response.json()["result"]["object"], definition


def link(h, creator, job, key, *, selector=None):
    response = h.client.post("/api/triggers", json={"expectedRevision":0,"requestKey":key,
        "definition":{"name":key,"targetJobId":job["id"],"eventTypes":["scheduler.time.v2"],
            "creatorSelector":selector or {"mode":"ids","ids":[creator["id"]]}},
        "expectedCreatorRevisions":{creator["id"]:creator["revision"]} if selector is None else {}})
    assert response.status_code == 201, response.text
    return response.json()["result"]["object"]


def test_provider_metadata_and_shared_reference_cas_preserve_full_config_and_authorization(h):
    providers = h.client.get("/api/event-creators/providers")
    assert providers.status_code == 200 and providers.json()["items"][0]["providerRef"]["releaseDigest"]
    creator, definition = source(h)
    path = "/api/event-creators/" + creator["id"]
    empty = h.client.get(path + "/references").json()
    job = enabled(h)
    first = link(h, creator, job, "first")
    second = link(h, creator, job, "second")
    link(h, creator, job, "all-time", selector={"mode":"registeredType","providerType":"time"})
    refs = h.client.get(path + "/references").json()
    assert len(refs["references"]) == 3 and refs["referenceDigest"] != empty["referenceDigest"]
    payload={"definition":{**definition,"name":"changed"},"expectedRevision":1,"requestKey":"stale-ref", "expectedReferences":empty["referenceDigest"]}
    rejected=h.client.put(path,json=payload)
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "reference_conflict"
    assert h.client.get(path).json()["revision"] == 1
    payload.update(requestKey="reviewed-ref",expectedReferences=refs["referenceDigest"])
    changed=h.client.put(path,json=payload)
    assert changed.status_code == 200, changed.text
    item=changed.json()["result"]["object"]
    assert item["config"] == definition["config"] and item["revision"] == 2
    deletion=h.client.request("DELETE",path,json={"expectedRevision":2,"expectedStateRevision":item["stateRevision"],"requestKey":"delete-shared","expectedReferences":refs["referenceDigest"]})
    assert deletion.status_code == 409
    # Removing all authentication cannot disclose subscriber metadata.
    h.client.headers.pop("Authorization")
    assert h.client.get(path + "/references").status_code == 401


def test_reference_metadata_requires_all_object_read_grants_and_cookie_writes_same_origin(h):
    creator, definition = source(h)
    path = "/api/event-creators/" + creator["id"]
    async def trusted(scope, receive, send):
        scope[authorization._SCOPE_KEY] = authorization._MARKER
        await h.app(scope, receive, send)
    async def check_grants():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=trusted), base_url="http://testserver") as client:
            assert (await client.get(path + "/references")).status_code == 403
            grants = h.automation.auth._default_permissions()
            for kind in ("eventCreator", "trigger", "job"):
                grants[kind]["read"] = True
                h.automation.auth.change_permissions(grants, h.automation.auth.permissions_status()["revision"])
                reply = await client.get(path + "/references")
                assert reply.status_code == (200 if kind == "job" else 403), reply.text
                assert (await client.get("/api/event-creators/providers")).status_code == 200
            grants["trigger"]["read"] = False
            h.automation.auth.change_permissions(grants, h.automation.auth.permissions_status()["revision"])
            assert (await client.get(path + "/references")).status_code == 403
    asyncio.run(check_grants())
    h.client.headers.pop("Authorization")
    h.client.cookies.set("pan_user", h.token)
    assert h.client.get(path + "/references").status_code == 200
    payload = {"definition": {**definition, "name": "cookie edit"}, "expectedRevision": 1,
               "requestKey": "cookie-edit"}
    assert h.client.put(path, json=payload).status_code == 403
    assert h.client.put(path, json=payload, headers={"Origin": "http://foreign"}).status_code == 403
    assert h.client.get(path).json()["revision"] == 1
    assert h.client.put(path, json=payload, headers={"Origin": "http://testserver"}).status_code == 200


def test_selected_creator_revision_is_checked_atomically_before_creating_link(h):
    creator, definition = source(h)
    job=enabled(h)
    h.client.put("/api/event-creators/"+creator["id"],json={"definition":{**definition,"name":"new"},"expectedRevision":1,"requestKey":"edit"})
    result=h.client.post("/api/triggers",json={"definition":{"name":"stale-source","targetJobId":job["id"],"eventTypes":["scheduler.time.v2"],"creatorSelector":{"mode":"ids","ids":[creator["id"]]}},"expectedRevision":0,"requestKey":"stale-link","expectedCreatorRevisions":{creator["id"]:1}})
    assert result.status_code == 409 and result.json()["error"]["code"] == "reference_conflict"
    assert h.client.get("/api/triggers").json()["items"] == []
