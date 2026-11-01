"""Canonical system fields: registry, normalization, Event and Action mapping."""
from __future__ import annotations

import asyncio
import copy
import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from packages.actions.executor import execute_dynamic
from packages.actions.store import validate_action
from packages.automation.api import install
from packages.automation.engine import arguments
from packages.automation.event_store import EventStore
from packages.automation.events import Bus
from packages.automation.store import Ledger
from packages.automation.service import Service
from packages.core.runtime_issues import map_runtime_issue
from packages.core.system_fields import (
    ACTION_TARGET_FIELDS,
    SYSTEM_FIELD_NAMES,
    SYSTEM_FIELDS,
    SystemFieldError,
    canonical_event,
    canonical_system,
    public_schema,
    system_binding_name,
    validate_argument_bindings,
)


REQUIRED = {
    "sessionId", "sourceSessionId", "workspaceId", "workerId", "taskId",
    "executionId", "attemptId", "queueItemId", "threadId", "turnId",
}


def run(coro):
    return asyncio.run(coro)


def fact(event_id="e", payload=None, system=None):
    event = dict(id=event_id, type="workers.all_idle", schemaVersion=1,
                 source={"type": "core", "id": "workers"},
                 occurredAt="2026-10-23T00:00:00Z",
                 payload=payload or {"runningCount": 0})
    if system is not None:
        event["system"] = system
    return event


def action(selector):
    return dict(
        id="pause", name="Pause selected", enabled=True, effect="write_local",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        execution={"kind": "session_operation", "operation": "pause_everything",
                   "selector": selector},
    )


def test_registry_is_canonical_complete_and_discoverable():
    assert set(SYSTEM_FIELD_NAMES) == REQUIRED
    assert set(ACTION_TARGET_FIELDS) == {"sessionId", "sourceSessionId"}
    for name in SYSTEM_FIELD_NAMES:
        row = SYSTEM_FIELDS[name]
        assert row["name"] == name
        assert row["type"] == "string"
        assert row["optional"] is True and row["nullable"] is False
        assert row["source"] in {"session", "workspace", "runtime", "task", "execution", "queue", "adapter"}
        assert row["trustedFrom"] in {"host", "adapter"}
        assert type(row["sensitive"]) is bool and type(row["actionTarget"]) is bool
        assert row["aliases"] and all(alias == name[0].lower() + "".join(
            "_" + ch.lower() if ch.isupper() else ch for ch in name[1:]) for alias in row["aliases"])
    assert SYSTEM_FIELDS["threadId"]["sensitive"] and SYSTEM_FIELDS["turnId"]["sensitive"]
    assert not SYSTEM_FIELDS["sessionId"]["sensitive"]
    schema = public_schema()
    json.dumps(schema)
    assert schema["canonicalOnly"] is True
    assert schema["aliases"]["session_id"] == "sessionId"
    assert schema["aliases"]["source_session_id"] == "sourceSessionId"
    assert len(schema["fields"]) == 10


def test_input_aliases_normalize_and_conflicts_fail_closed():
    assert canonical_system({
        "session_id": "ses_1",
        "source_session_id": "ses_0",
        "worker_id": "w1",
        "queue_item_id": "q1",
    }) == {"sessionId": "ses_1", "sourceSessionId": "ses_0",
           "workerId": "w1", "queueItemId": "q1"}
    # Equal canonical+alias is idempotent; conflicting duplicates are rejected.
    assert canonical_system({"sessionId": "ses_1", "session_id": "ses_1"}) == {"sessionId": "ses_1"}
    with pytest.raises(SystemFieldError, match="Conflicting"):
        canonical_system({"sessionId": "ses_1", "session_id": "ses_2"})
    with pytest.raises(SystemFieldError, match="Unknown system field"):
        canonical_system({"targetSession": "ses_1"})
    assert canonical_system({"targetSession": "ses_1", "session_id": None}, allow_unknown=True) == {}
    for bad in (1, True, ["ses_1"], "", " ses_1", "ses_1 ", "ses\x00_1", "x" * 257):
        with pytest.raises(SystemFieldError):
            canonical_system({"sessionId": bad})
    with pytest.raises(SystemFieldError, match="exceed"):
        canonical_system({name: "界" * 250 for name in SYSTEM_FIELD_NAMES})


def test_payload_cannot_create_or_override_system_and_text_is_never_parsed():
    event = canonical_event({
        "id": "e", "type": "workers.all_idle", "schemaVersion": 1,
        "source": {"type": "core", "id": "workers"}, "occurredAt": "now",
        "payload": {"sessionId": "ses_payload", "session_id": "ses_payload"},
        "system": {"session_id": "ses_trusted", "workerId": "w1"},
    })
    assert event["system"] == {"sessionId": "ses_trusted", "workerId": "w1"}
    assert event["payload"]["sessionId"] == "ses_payload"
    assert "system" not in canonical_event({
        "id": "e", "type": "x", "payload": {"sessionId": "ses_payload"},
    })
    # Message-like keys are unknown metadata and are dropped, not parsed.
    assert canonical_system({"message": "session_id: ses_guess"}, allow_unknown=True) == {}


def test_event_store_uses_canonical_system_in_identity(tmp_path):
    store = EventStore(tmp_path)
    store.initialize()
    first = store.append(fact("e", system={"session_id": "ses_1"}), [])
    assert first["duplicate"] is False
    assert store.lookup("e")["event"]["system"] == {"sessionId": "ses_1"}
    # The same fact with canonical input is the same durable Event.
    assert store.append(fact("e", system={"sessionId": "ses_1"}), [])["duplicate"] is True
    with pytest.raises(ValueError, match="different fact"):
        store.append(fact("e", system={"sessionId": "ses_2"}), [])
    with pytest.raises(ValueError, match="different fact"):
        store.append(fact("e"), [])
    # Legacy retained Events without a system block keep their old fingerprint.
    store.append(fact("legacy"), [])
    assert store.append(fact("legacy"), [])["duplicate"] is True
    from packages.automation.event_store import fact_identity
    assert "system" not in fact_identity(fact("legacy"))


def test_trigger_arguments_bind_system_without_payload_fallback():
    event = fact(system={"sessionId": "ses_trusted"}, payload={"sessionId": "ses_payload"})
    assert arguments({"target": {"from": "system", "path": "sessionId"}}, event) == {
        "target": "ses_trusted"}
    assert arguments({"target": {"from": "system", "path": "system.sessionId"}}, event) == {
        "target": "ses_trusted"}
    assert arguments({"target": {"from": "event", "path": "payload.sessionId"}}, event) == {
        "target": "ses_payload"}
    with pytest.raises(SystemFieldError, match="unavailable"):
        arguments({"target": {"from": "system", "path": "sourceSessionId"}}, event)
    with pytest.raises(SystemFieldError, match="Unknown system field"):
        arguments({"target": {"from": "system", "path": "payload.sessionId"}}, event)
    validate_argument_bindings({
        "a": {"from": "literal", "value": 1},
        "b": {"from": "event", "path": "payload.value"},
        "c": {"from": "system", "path": "sessionId"},
    })
    for bad in (
        {"a": {"from": "system"}},
        {"a": {"from": "system", "path": "workerId", "extra": 1}},
        {"a": {"from": "system", "path": "unknown"}},
        {"a": {"from": "other", "path": "x"}},
    ):
        with pytest.raises(SystemFieldError):
            validate_argument_bindings(bad)
    assert system_binding_name("system.sourceSessionId") == "sourceSessionId"


def test_ledger_rejects_invalid_system_binding_before_storage(tmp_path):
    ledger = Ledger(tmp_path)
    base = dict(name="system binding", eventTypes=["workers.all_idle"], actionId="pause",
                enabled=True)
    accepted = ledger.put({**base, "id": "t1",
                           "arguments": {"target": {"from": "system", "path": "sessionId"}}}, 0, True)
    assert accepted["arguments"]["target"]["path"] == "sessionId"
    with pytest.raises(SystemFieldError):
        ledger.put({**base, "id": "t2",
                    "arguments": {"target": {"from": "system", "path": "payload.sessionId"}}}, 0, True)
    assert "t2" not in ledger.read()["triggers"]


def test_action_selector_uses_only_trusted_system_targets():
    assert validate_action(action({"mode": "event", "path": "system.sessionId"}))
    with pytest.raises(SystemFieldError, match="not an Action target"):
        validate_action(action({"mode": "event", "path": "system.workerId"}))
    with pytest.raises(SystemFieldError, match="Unknown system Action target"):
        validate_action(action({"mode": "event", "path": "system"}))

    calls = []
    async def pause(session_id, args):
        calls.append((session_id, args))
        return {"ok": True}
    async def progress(**fields):
        return None
    event = fact(system={"sessionId": "ses_trusted"}, payload={"sessionId": "ses_payload"})
    result = run(execute_dynamic(action({"mode": "event", "path": "system.sessionId"}),
                                 {}, event, progress, pause))
    assert result["ok"] is True and calls == [("ses_trusted", {"paused": True})]
    calls.clear()
    with pytest.raises(SystemFieldError, match="unavailable"):
        run(execute_dynamic(action({"mode": "event", "path": "system.sourceSessionId"}),
                            {}, event, progress, pause))
    assert calls == []
    forged = fact(payload={"sessionId": "ses_payload"})
    with pytest.raises(SystemFieldError, match="unavailable"):
        run(execute_dynamic(action({"mode": "event", "path": "system.sessionId"}),
                            {}, forged, progress, pause))
    assert calls == []


def test_bus_accepts_system_only_from_trusted_core_runtime():
    bus = Bus()
    core = bus.activate("core", "workers")
    bus.register(core, "workers.all_idle", {"type": "object"})
    event = bus.prepare(core, "workers.all_idle", {"sessionId": "payload"},
                        system={"session_id": "ses_1"})
    assert event["system"] == {"sessionId": "ses_1"}
    assert event["payload"] == {"sessionId": "payload"}
    plugin = bus.activate("plugin", "producer")
    bus.register(plugin, "producer.ping", {"type": "object"})
    with pytest.raises(ValueError, match="trusted core runtime"):
        bus.prepare(plugin, "producer.ping", {"value": 1}, system={"sessionId": "forged"})


def test_runtime_issue_uses_structured_host_identity_only():
    raw = {
        "type": "codex.turn_error", "thread_id": "thr-1", "turn_id": "turn-1",
        "session_id": "provider-session-should-not-win",
        "error": {"code": "usage_limit_reached", "message": "Session ses_from_text failed"},
    }
    mapped = map_runtime_issue(raw, adapter="codex", metadata={
        "session_id": "ses_host", "worker_id": "w1", "task_id": "T-1",
        "generation": 3, "runtimeEpoch": "owner",
    })
    assert mapped["event"]["source"] == {"type": "core", "id": "runtime"}
    assert mapped["event"]["system"] == {
        "sessionId": "ses_host", "workerId": "w1", "taskId": "T-1",
        "threadId": "thr-1", "turnId": "turn-1",
    }
    assert mapped["issue"]["correlation"]["sessionId"] == "ses_host"
    assert "ses_from_text" not in mapped["event"]["system"].values()
    # Without host metadata, a provider envelope session_id is not promoted.
    untrusted = map_runtime_issue({"session_id": "provider-only", "error": {"code": "quota_exhausted"}},
                                  adapter="codex")
    assert "sessionId" not in untrusted["event"].get("system", {})
    assert "sessionId" not in untrusted["issue"]["correlation"]
    text_only = map_runtime_issue({"message": "session ses_guess failed"}, adapter="codex")
    assert "system" not in text_only["event"]
    only_task = map_runtime_issue({"message": "failed"}, adapter="codex", metadata={"task_id": "T-1"})
    assert only_task["event"]["system"] == {"taskId": "T-1"}
    assert "sessionId" not in only_task["event"]["system"]
    with pytest.raises(SystemFieldError, match="Conflicting supplied aliases"):
        map_runtime_issue({"message": "failed"}, adapter="codex",
                          metadata={"sessionId": "ses_a", "session_id": "ses_b"})
    with pytest.raises(SystemFieldError, match="Conflicting adapter aliases"):
        map_runtime_issue({"threadId": "thr_a", "thread_id": "thr_b",
                           "error": {"code": "usage_limit_reached"}}, adapter="codex")
    from packages.core.runtime_issues import runtime_issue_schema
    assert set(runtime_issue_schema()["systemFields"]) == REQUIRED


def test_service_persists_runtime_issue_and_keeps_quota_observer(tmp_path):
    async def scenario():
        from tests.test_event_store import setup as service_setup
        from tests.test_automation_core import until
        from packages.core.runtime_issues import observe as notify_observer
        svc, _ = await service_setup(tmp_path)
        seen = []
        original = svc.quota_policy.observe

        def quota_observe(mapping):
            seen.append(mapping)
            original(mapping)

        svc.quota_policy.observe = quota_observe
        try:
            mapping = map_runtime_issue(
                {"type": "error", "status": 429,
                 "error": {"code": "model_capacity_exceeded",
                           "message": "Selected model is at capacity."}},
                adapter="cbc", metadata={"session_id": "ses_observed", "worker_id": "w1"})
            assert mapping["event"]["system"] == {"sessionId": "ses_observed", "workerId": "w1"}
            notify_observer(mapping)

            async def stored():
                page = await svc.engine.io(svc.events.page, 0, 100)
                return next((row for row in page["events"]
                             if row["event"]["type"] == "runtime.issue.v1"), None)

            await until(stored)
            row = await stored()
            event = row["event"]
            assert event["system"] == {"sessionId": "ses_observed", "workerId": "w1"}
            assert event["source"] == {"type": "core", "id": "runtime"}
            assert event["occurredAt"]
            assert event["payload"]["correlation"]["sessionId"] == "ses_observed"
            assert seen and seen[0]["event"]["id"] == event["id"]
            assert svc.bus.available("runtime.issue.v1")

            await svc.close()
            before = len((await svc.engine.io(svc.events.page, 0, 100))["events"])
            closed_mapping = map_runtime_issue({"type": "error", "status": 429,
                               "error": {"code": "model_capacity_exceeded"}},
                              adapter="cbc", metadata={"session_id": "ses_after_close"})
            notify_observer(closed_mapping)
            svc.observe_runtime_issue(closed_mapping)
            await asyncio.sleep(.05)
            after = len((await svc.engine.io(svc.events.page, 0, 100))["events"])
            assert after == before
            assert any(d.get("phase") == "runtime-issue-ingress"
                       and "closed" in d.get("error", "").lower()
                       for d in svc.bus.diagnostics)
        finally:
            if not svc.stopping:
                await svc.close()

    run(scenario())


def test_trigger_system_binding_fails_closed_when_only_payload_has_id(tmp_path):
    async def scenario():
        from tests.test_automation_core import setup as engine_setup, settled
        engine, calls, _ = await engine_setup(tmp_path)
        owner = engine.bus.activate("core", "runtime")
        engine.bus.register(owner, "runtime.issue.v1", {"type": "object"})
        engine.ledger.put(dict(id="t", name="t", eventTypes=["runtime.issue.v1"],
                               actionId="pause", enabled=True, arguments={
                                   "targetSessionId": {"from": "system", "path": "sessionId"}}), 0, True)
        try:
            await engine.match({"id": "forged", "type": "runtime.issue.v1",
                                "payload": {"sessionId": "ses_forged"}})
            rows = await settled(engine, 1)
            assert rows[0]["status"] == "failed"
            assert "System field unavailable" in rows[0]["error"]
            assert calls == []
        finally:
            await engine.close()

    run(scenario())


def _service_stub(tmp_path, limit):
    svc = Service(tmp_path / "service", SimpleNamespace(
        workers={}, set_running_observer=lambda cb: None,
        _sess=SimpleNamespace(SESSION_DIR=tmp_path / "sessions")), None)
    svc.started = True
    svc.ingress = asyncio.Queue(limit)
    return svc


class BlockedLoop:
    """Accept cross-thread callbacks but never run them until the test does."""
    def __init__(self):
        self.callbacks = []

    def call_soon_threadsafe(self, fn, *args):
        self.callbacks.append((fn, args))

    def is_closed(self):
        return False


def test_cross_thread_runtime_issue_callbacks_are_bounded_and_released(tmp_path):
    limit = 4
    svc = _service_stub(tmp_path, limit)
    svc.loop = BlockedLoop()
    mapping = map_runtime_issue(
        {"type": "error", "status": 429, "error": {"code": "model_capacity_exceeded"}},
        adapter="cbc", metadata={"session_id": "ses_thread"})

    def submit():
        svc.observe_runtime_issue(mapping)

    threads = [threading.Thread(target=submit) for _ in range(limit + 3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(svc.loop.callbacks) == limit
    assert svc._runtime_issue_reserved == limit
    assert sum(d.get("error") == "Runtime issue cross-thread backlog full"
               for d in svc.bus.diagnostics) == 3

    for fn, args in svc.loop.callbacks:
        fn(*args)
    assert svc._runtime_issue_reserved == 0
    assert svc.ingress.qsize() == limit

    broken = _service_stub(tmp_path / "broken", 1)
    broken.loop = BlockedLoop()
    broken.bus.prepare = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("schema failure"))
    thread = threading.Thread(target=lambda: broken.observe_runtime_issue(mapping))
    thread.start()
    thread.join()
    assert len(broken.loop.callbacks) == 1
    broken.loop.callbacks[0][0](*broken.loop.callbacks[0][1])
    assert broken._runtime_issue_reserved == 0
    assert any("schema failure" in d.get("error", "") for d in broken.bus.diagnostics)

    closing = _service_stub(tmp_path / "closing", limit)
    closing.loop = BlockedLoop()
    threads = [threading.Thread(target=lambda: closing.observe_runtime_issue(mapping))
               for _ in range(limit)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert closing._runtime_issue_reserved == limit
    run(closing.close())
    assert closing._runtime_issue_reserved == 0


def test_discovery_http_is_read_only_and_available_without_runtime():
    server = SimpleNamespace(app=FastAPI())
    install(server)

    async def request():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://owned") as client:
            listing = await client.get("/api/automation/system-fields")
            schema = await client.get("/api/automation/schema")
            return listing, schema

    listing, schema = run(request())
    assert listing.status_code == 200
    body = listing.json()
    assert body["canonicalOnly"] is True
    assert {item["name"] for item in body["fields"]} == REQUIRED
    assert all(item["type"] == "string" and "sensitive" in item and "actionTarget" in item
               and item["trusted"] is True for item in body["fields"])
    assert schema.status_code == 200
    assert schema.json()["systemFields"] == {
        "version": 1, "path": "/api/automation/system-fields"}
