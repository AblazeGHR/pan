"""Canonical system fields: registry, normalization, Event and Action mapping."""
from __future__ import annotations

import asyncio
import copy
import json
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
    from packages.core.runtime_issues import runtime_issue_schema
    assert set(runtime_issue_schema()["systemFields"]) == REQUIRED


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
