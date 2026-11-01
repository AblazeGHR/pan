"""WP06c effective production routing and owned TCP compatibility acceptance."""
from collections import Counter
import json
from pathlib import Path
import re

import httpx
import pytest

from packages.automation.authorization import canonical_path, object_path
from packages.jobs.final_client import CanonicalClient
from packages.scheduler.providers import time_definition
from tests.test_job_final_clients import tcp
from tests.test_job_final_creators import config, interval
from tests.test_job_final_http_service import h, job_def


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "tests/fixtures/job_path_flip_routes.json"


def effective_routes(app):
    for route in app.routes:
        if hasattr(route, "effective_route_contexts"):
            yield from route.effective_route_contexts()
        else:
            yield route


def route_snapshot(app):
    routes = [route for route in effective_routes(app) if hasattr(route, "methods") and
              re.match(r"/api/(?:final/|legacy/jobs(?:/|$)|jobs(?:/|$)|triggers(?:/|$)|"
                       r"event-creators(?:/|$)|job-templates(?:/|$)|job-runs(?:/|$)|operations(?:/|$))", route.path)]
    rows = [{"path": route.path, "methods": sorted(route.methods),
             "handler": route.endpoint.__module__ + "." + route.endpoint.__name__,
             "deprecated": bool(route.deprecated)} for route in routes]
    pairs = Counter((method, re.sub(r"\{[^}]+\}", "{id}", row["path"]))
                    for row in rows for method in row["methods"])
    conflicts = [list(pair) for pair, count in pairs.items() if count > 1]
    assert conflicts == []
    # Exercise the dispatch order, including static legacy names that resemble IDs.
    for route in routes:
        sample = re.sub(r"\{[^}]+\}", "wp06c_sample", route.path)
        for method in route.methods:
            first = next(candidate for candidate in routes if method in candidate.methods
                         and candidate.path_regex.fullmatch(sample))
            assert first.endpoint is route.endpoint, (method, route.path, first.path)
    return {"methodPathConflicts": conflicts, "routes": rows}


def test_production_route_snapshot_and_same_handler_aliases():
    # Import only: no application lifespan or service is started.
    from packages.web.server import app
    snapshot = route_snapshot(app)
    assert snapshot == json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    routes = list(effective_routes(app))
    for route in routes:
        if not hasattr(route, "path") or not route.path.startswith("/api/final/"):
            continue
        current = route.path.replace("/api/final/actions", "/api/jobs/actions").replace("/api/final/", "/api/")
        twin = next(r for r in routes if getattr(r, "path", None) == current and r.methods == route.methods)
        assert twin.endpoint is route.endpoint
        assert route.deprecated and not twin.deprecated
    # R5: the facade keeps every read/cancel handler; every other old mutation
    # is replaced by an explicit retirement, at both old addresses.
    from packages.jobs.api import router as facade
    from packages.automation.authorization import object_path as trusted_bridge
    for old in facade.routes:
        relocated = old.path.replace("/api/jobs", "/api/legacy/jobs", 1)
        reads = old.methods <= {"GET", "HEAD", "OPTIONS"} or old.path.endswith("/cancel")
        match = [r for r in routes if getattr(r, "path", None) == relocated and
                 r.methods == old.methods]
        assert match, (old.methods, old.path)
        retired = [r for r in match if r.endpoint.__name__ == "retired_legacy_write"]
        kept = [r for r in match if r.endpoint is old.endpoint]
        if reads:
            assert kept == match, (old.methods, old.path)
        else:
            assert len(retired) == len(match), (old.methods, old.path)
        # A retired write never re-enters the trusted MCP object bridge.  The
        # bare root path keeps its canonical meaning, so only concrete legacy
        # sub-paths are asserted here.
        if not reads and old.path not in {"/api/jobs", "/api/jobs/"}:
            assert not trusted_bridge(old.path)
            assert not trusted_bridge(relocated)


@pytest.mark.parametrize("prefix", ["/api", "/api/final"])
def test_tcp_new_and_deprecated_crud_operations_and_legacy(h, tcp, prefix):
    other = "/api/final" if prefix == "/api" else "/api"
    with httpx.Client(base_url=tcp, trust_env=False, headers={"Authorization": "Bearer " + h.token}) as client:
        definitions = {"jobs": job_def(h), "event-creators": time_definition("clock", config([interval()])),
                       "job-templates": {"name": "template", "jobDefinition": job_def(h),
                                         "composition": {"creators": [], "triggers": []}}}
        objects = {}
        for kind in ["jobs", "event-creators", "triggers", "job-templates"]:
            if kind == "triggers":
                definitions[kind] = {"name": "trigger", "targetJobId": objects["jobs"]["id"],
                    "eventTypes": definitions["event-creators"]["eventTypes"],
                    "creatorSelector": {"mode": "ids", "ids": [objects["event-creators"]["id"]]}}
            payload = {"definition": definitions[kind], "expectedRevision": 0, "requestKey": kind + "-create"}
            response = client.post(prefix + "/" + kind, json=payload)
            assert response.status_code == 201, response.text
            receipt = response.json()
            item = receipt["result"]["object"]
            assert receipt["links"]["self"] == "/api/operations/" + receipt["operationId"]
            for base in [prefix, other]:
                assert client.get(base + "/" + kind + "/" + item["id"]).json() == item
                assert client.get(base + "/" + kind).json()["items"][0] == item
                assert client.get(base + "/operations/" + receipt["operationId"]).json() == receipt
                assert client.get(base + "/operations", params={"operation": "create", "requestKey": payload["requestKey"]}).json() == receipt
            replay = client.post(other + "/" + kind, json=payload)
            assert replay.status_code == 200 and replay.json()["operationId"] == receipt["operationId"]
            changed = client.put(other + "/" + kind + "/" + item["id"], json={
                "definition": {**definitions[kind], "name": "changed"}, "expectedRevision": 1,
                "requestKey": kind + "-update"})
            assert changed.status_code == 200, changed.text
            objects[kind] = changed.json()["result"]["object"]
            if kind == "jobs":
                enabled = client.post(prefix + "/jobs/" + item["id"] + "/operations", json={
                    "operation": "enable", "expectedRevision": 2,
                    "expectedStateRevision": objects[kind]["stateRevision"],
                    "requestKey": "enable", "parameters": {}})
                assert enabled.status_code == 200, enabled.text
                objects[kind] = enabled.json()["result"]["object"]
        for kind, item in objects.items():
            retired = client.request("DELETE", prefix + "/" + kind + "/" + item["id"], json={
                "expectedRevision": 2, "expectedStateRevision": item["stateRevision"], "requestKey": kind + "-retire"})
            assert retired.status_code == 200, retired.text
            assert client.get(other + "/" + kind + "/" + item["id"]).json()["state"] == "retired"
        assert client.get("/api/jobs/actions").json() == client.get("/api/final/actions").json()
        # A legacy runtime-unavailable response proves this reached its old handler.
        response = client.get("/api/jobs/action-jobs")
        assert response.status_code == 503 and response.json() == {"detail": "Job Action runtime unavailable"}
        assert "jobs" in client.get("/api/legacy/jobs").json()
        assert "kinds" in client.get("/api/legacy/jobs/kinds").json()
        # R5: the facade no longer validates and applies old writes.  It answers
        # an explicit retirement for every mutation, valid body or not, so a
        # stale client cannot tell a good payload from a bad one.
        retired = client.post("/api/legacy/jobs", json={"kind": "not-supported"})
        assert retired.status_code == 410
        assert retired.json()["detail"]["code"] == "retired"
        for base in [prefix, other]:
            assert client.get(base + "/jobs", headers={"Authorization": "Bearer invalid"}).status_code == 401


@pytest.mark.parametrize("prefix", ["/api", "/api/final"])
def test_tcp_cookie_origin_and_explicit_bearer_precedence(h, tcp, prefix):
    payload = {"definition": job_def(h), "expectedRevision": 0, "requestKey": "cookie-create"}
    with httpx.Client(base_url=tcp, trust_env=False, cookies={"pan_user": h.token}) as client:
        assert client.get(prefix + "/jobs").status_code == 200
        assert client.post(prefix + "/jobs", json=payload).status_code == 403
        assert client.post(prefix + "/jobs", json=payload, headers={"Origin": "http://foreign"}).status_code == 403
        assert client.get(prefix + "/jobs", headers={"Authorization": "Bearer invalid"}).status_code == 401
        allowed = client.post(prefix + "/jobs", json=payload, headers={"Origin": tcp})
        assert allowed.status_code == 201, allowed.text


def test_clients_emit_only_current_routes():
    calls = []
    client = CanonicalClient(lambda *args: calls.append(args) or {})
    client.call("jobs", "list")
    client.call("triggers", "get", object_id="t")
    client.call("event-creators", "get", object_id="c")
    client.call("job-templates", "get", object_id="t")
    client.call("job-runs", "get", object_id="r")
    client.call("operations", "get", object_id="o")
    client.call("actions", "list")
    assert [args[1].split("?")[0] for args in calls] == ["/api/jobs", "/api/triggers/t", "/api/event-creators/c",
        "/api/job-templates/t", "/api/job-runs/r", "/api/operations/o", "/api/jobs/actions"]


@pytest.mark.parametrize("path", ["/api/legacy/jobs", "/api/jobs/private/x", "/api/operations/o/private",
    "/api/triggers/t/lifecycle", "/api/event-creators/c/private", "/api/job-templates/t/instantiate",
    "/api/jobs/settings", "/api/jobs/kinds", "/api/final/jobs/../private", "/api/main/exit"])
def test_new_allowlist_does_not_expand_other_paths(path):
    assert not canonical_path(path)
    assert not object_path(path)
