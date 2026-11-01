"""FS01-FS05: canonical Event Creator / Scheduler contract tests (WP04)."""
from __future__ import annotations

import asyncio
import copy
import functools
from datetime import datetime, timezone

import pytest

from packages.jobs import final_contracts as contracts
from packages.scheduler.composition import CompositionCoordinator, NullCompositionPort
from packages.scheduler.creators import CanonicalEventStore, CreatorRuntime, CreatorStore, EventBus
from packages.scheduler.providers import (
    ProviderRegistry, TimeProvider, settlement_definition, time_definition,
)
from tests.test_job_final_contracts import template_definition

T0 = "2026-01-01T00:00:00Z"


def async_test(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def at(second):
    from decimal import Decimal
    from packages.scheduler.providers import instant, timestamp
    return timestamp(instant(T0) + Decimal(str(second)))


def interval(rule_id="daily", *, seconds="60", mode="grid"):
    return {"ruleId": rule_id, "type": "interval", "seconds": seconds, "anchor": T0, "advanceMode": mode}


def config(rules, *, policy="fire_now", grace="1"):
    return {"rules": rules, "misfirePolicy": policy, "graceSeconds": grace, "pausePolicy": "advance"}


def stack(tmp_path, *, event_clock=lambda: T0):
    creators = CreatorStore(tmp_path / "creators")
    events = CanonicalEventStore(tmp_path / "events", clock=event_clock)
    bus = EventBus(events, creators.get)
    runtime = CreatorRuntime(creators, bus)
    return creators, events, bus, runtime


def create_time(creators, definition, *, key="create", state="enabled", at=T0):
    return creators.create(definition, request_key=key, state=state, at=at)


# FS01 ──────────────────────────────────────────────────────────────────────


def test_fs01_decimal_grid_weekly_seconds_once_and_cron():
    provider = TimeProvider()
    rule = interval(seconds="0.125")
    assert provider.next_after(rule, T0)["at"] == at("0.125")
    assert provider.next_after(rule, at("0.125"))["at"] == at("0.25")
    assert provider.next_after(rule, at("10.5"))["at"] == at("10.625")
    from decimal import Decimal, localcontext
    from packages.scheduler.providers import instant, timestamp
    with localcontext() as context:
        context.prec = 100
        almost = timestamp(instant(at("0.125")) - Decimal("1e-60"))
    assert provider.next_after(rule, almost)["at"] == at("0.125")
    once = {"ruleId": "once", "type": "once", "at": at("0.125")}
    assert provider.next_after(once, T0)["at"] == at("0.125")
    assert provider.next_after(once, at("0.125")) is None
    weekly = {
        "ruleId": "weekly", "type": "weekly", "weekday": 3,
        "time": "00:00:07", "timezone": "UTC", "dstPolicy": "gap-skip-fold-first",
    }
    assert provider.next_after(weekly, T0)["at"] == "2026-01-01T00:00:07Z"
    cron = {
        "ruleId": "cron", "type": "cron", "expression": "30 9 * * *",
        "timezone": "America/New_York", "dstPolicy": "gap-skip-fold-first",
    }
    assert provider.next_after(cron, "2026-01-01T00:00:00Z")["at"] == "2026-01-01T14:30:00Z"


@async_test
async def test_fs01_multiple_rules_and_after_publication_have_independent_cursors(tmp_path):
    creators, events, bus, runtime = stack(tmp_path, event_clock=lambda: at("0.125"))
    definition = time_definition("multi", config([
        interval("a", seconds="0.125"), interval("b", seconds="0.125"),
    ]))
    item = create_time(creators, definition)
    assert await runtime.tick(at=at("0.125")) == 2
    assert events.stats()["events"] == 2
    current = creators.get(item["id"])
    assert current["producedCount"] == 2
    assert current["cursor"]["rules"]["a"]["nextFireAt"] == at("0.25")
    assert current["cursor"]["rules"]["b"]["nextFireAt"] == at("0.25")
    assert {event["payload"]["ruleId"] for event in events.page()} == {"a", "b"}

    creators2, events2, _, runtime2 = stack(tmp_path / "after", event_clock=lambda: at(60))
    after = create_time(creators2, time_definition("after", config([interval(seconds="0.5", mode="afterPublication")], grace="300")))
    await runtime2.tick(at=at(60))
    assert creators2.get(after["id"])["cursor"]["rules"]["daily"]["nextFireAt"] == at("60.5")
    assert events2.stats()["events"] == 1


def test_fs01_iana_zone_matrix_keeps_offsets_and_fold_ordinals():
    provider = TimeProvider()
    london = {
        "ruleId": "london", "type": "weekly", "weekday": 6, "time": "01:30:00",
        "timezone": "Europe/London", "dstPolicy": "gap-skip-fold-second",
    }
    occurrence = provider.next_after(london, "2026-10-25T00:00:00Z")
    assert (occurrence["at"], occurrence["fold"], occurrence["ordinal"]) == (
        "2026-10-25T01:30:00Z", 1, 2,
    )
    auckland_gap = {
        "ruleId": "auckland", "type": "weekly", "weekday": 6, "time": "02:30:00",
        "timezone": "Pacific/Auckland", "dstPolicy": "gap-skip-fold-first",
    }
    assert provider.next_after(auckland_gap, "2026-09-20T00:00:00Z")["at"] == "2026-10-03T13:30:00Z"


def test_fs01_cron_dst_gap_and_impossible_search_have_explicit_results():
    provider = TimeProvider()
    cron = {
        "ruleId": "cron", "type": "cron", "expression": "30 2 * * *",
        "timezone": "America/New_York", "dstPolicy": "gap-skip-fold-first",
    }
    assert provider.next_after(cron, "2026-03-08T05:00:00Z")["at"] == "2026-03-09T06:30:00Z"
    impossible = {
        "ruleId": "impossible", "type": "cron", "expression": "0 0 31 2 *",
        "timezone": "UTC", "dstPolicy": "strict",
    }
    assert provider.next_after(impossible, "2026-01-01T00:00:00Z") is None


def test_fs01_utc_and_fixed_offset_do_not_require_zoneinfo():
    def unavailable(_name):
        raise RuntimeError("tzdata intentionally unavailable")
    provider = TimeProvider(zone_factory=unavailable)
    utc = {
        "ruleId": "utc", "type": "weekly", "weekday": 3, "time": "00:00:07",
        "timezone": "UTC", "dstPolicy": "gap-skip-fold-first",
    }
    assert provider.next_after(utc, T0)["at"] == "2026-01-01T00:00:07Z"
    fixed = {
        "ruleId": "fixed", "type": "weekly", "weekday": 3, "time": "06:00:00",
        "timezone": "+05:30", "dstPolicy": "gap-skip-fold-first",
    }
    occurrence = provider.next_after(fixed, T0)
    assert occurrence["at"] == "2026-01-01T00:30:00Z"
    assert occurrence["timezone"] == "+05:30"


def test_fs01_named_zone_without_tzdata_is_explicitly_unavailable():
    def unavailable(_name):
        raise RuntimeError("tzdata intentionally unavailable")
    provider = TimeProvider(zone_factory=unavailable)
    rule = {
        "ruleId": "named", "type": "weekly", "weekday": 3, "time": "00:00:07",
        "timezone": "Europe/London", "dstPolicy": "gap-skip-fold-first",
    }
    with pytest.raises(contracts.ContractError) as error:
        provider.next_after(rule, T0)
    assert error.value.code == "provider_unavailable"
    assert error.value.details["reason"] == "timezone_unavailable"


def test_fs01_explicit_dst_gap_fold_and_unavailable_zone():
    provider = TimeProvider()
    base = {"ruleId": "dst", "type": "weekly", "weekday": 6, "time": "02:30:00", "timezone": "America/New_York"}
    shifted = provider.next_after({**base, "dstPolicy": "gap-shift-fold-first"}, "2026-03-01T08:00:00Z")
    skipped = provider.next_after({**base, "dstPolicy": "gap-skip-fold-first"}, "2026-03-01T08:00:00Z")
    assert shifted["at"] == "2026-03-08T07:30:00Z" and shifted["gap"] == "shifted"
    assert skipped["at"] == "2026-03-15T06:30:00Z"
    fold = {**base, "time": "01:30:00"}
    first = provider.next_after({**fold, "dstPolicy": "gap-skip-fold-first"}, "2026-10-25T07:00:00Z")
    second = provider.next_after({**fold, "dstPolicy": "gap-skip-fold-second"}, "2026-10-25T07:00:00Z")
    assert (first["at"], first["fold"], first["ordinal"]) == ("2026-11-01T05:30:00Z", 0, 1)
    assert (second["at"], second["fold"], second["ordinal"]) == ("2026-11-01T06:30:00Z", 1, 2)
    with pytest.raises(ValueError):
        provider.next_after({**fold, "dstPolicy": "strict"}, "2026-10-25T07:00:00Z")
    with pytest.raises(contracts.ContractError) as error:
        provider.validate_config(config([{**fold, "dstPolicy": "gap-skip-fold-first", "timezone": "Missing/Zone"}]))
    assert error.value.code == "provider_unavailable"


# FS02 ──────────────────────────────────────────────────────────────────────


@async_test
async def test_fs02_skip_and_backfill_are_distinct_instances_with_actual_drive_time(tmp_path):
    creators, events, _, runtime = stack(tmp_path)
    skipped = create_time(creators, time_definition("skip", config([interval()], policy="skip")), key="skip")
    fired = create_time(creators, time_definition("fire", config([interval()], policy="fire_now")), key="fire")
    await runtime.tick(at=at(600))
    assert events.stats()["events"] == 1
    event = events.page()[0]
    assert event["creatorId"] == fired["id"] != skipped["id"]
    assert event["occurredAt"] == at(600)
    assert event["payload"]["scheduledAt"] == at(60)
    assert event["payload"]["driveReason"] == "backfill"
    skip_state = creators.get(skipped["id"])
    fire_state = creators.get(fired["id"])
    assert skip_state["producedCount"] == 0 and skip_state["health"]["lastOutcome"] == "skipped"
    assert fire_state["producedCount"] == 1
    assert skip_state["cursor"] != fire_state["cursor"]
    assert skip_state["drives"][0]["outcome"] == "skipped"


@async_test
async def test_fs02_pause_advances_without_business_event_or_resume_backfill(tmp_path):
    creators, events, _, runtime = stack(tmp_path)
    item = create_time(creators, time_definition("paused", config([interval()])))
    paused = creators.set_state(item["id"], "paused", expected_state_revision=item["stateRevision"])
    await runtime.tick(at=at(600))
    assert events.stats()["events"] == 0
    paused = creators.get(item["id"])
    assert paused["drives"][0]["outcome"] == "paused"
    assert paused["cursor"]["rules"]["daily"]["nextFireAt"] == at(660)
    creators.set_state(item["id"], "enabled", expected_state_revision=paused["stateRevision"])
    await runtime.tick(at=at(600))
    assert events.stats()["events"] == 0


# FS03 ──────────────────────────────────────────────────────────────────────


@async_test
async def test_fs03_event_commit_cursor_crash_and_restart_replay_same_envelope_once(tmp_path, monkeypatch):
    creators, events, _, runtime = stack(tmp_path)
    item = create_time(creators, time_definition("once", config([{"ruleId": "once", "type": "once", "at": at(60)}], grace="300")))
    original = creators.advance
    def crash(*args):
        raise OSError("after Event commit, before cursor commit")
    monkeypatch.setattr(creators, "advance", crash)
    await runtime.tick(at=at(60))
    pending = creators.get(item["id"])["pendingPublication"]
    assert pending and events.stats() == {"events": 1, "pins": 1}
    assert creators.get(item["id"])["producedCount"] == 0

    fresh = CreatorStore(tmp_path / "creators")
    fresh_bus = EventBus(CanonicalEventStore(tmp_path / "events", clock=lambda: at(61)), fresh.get)
    fresh_runtime = CreatorRuntime(fresh, fresh_bus)
    await fresh_runtime.tick(at=at(61))
    assert fresh_bus.events.stats() == {"events": 1, "pins": 0}
    settled = fresh.get(item["id"])
    assert settled["producedCount"] == 1 and settled["pendingPublication"] is None
    assert settled["drives"][0]["eventId"] == pending["envelope"]["eventId"]
    retained = fresh_bus.events.page()[0]
    assert retained["emittedAt"] == pending["envelope"]["emittedAt"] == at(60)
    assert retained["occurredAt"] == at(60)
    assert original is not None


@async_test
async def test_fs03_publish_failure_ack_failure_and_concurrent_contenders_are_idempotent(tmp_path, monkeypatch):
    creators, events, bus, runtime = stack(tmp_path)
    item = create_time(creators, time_definition("once", config([{"ruleId": "once", "type": "once", "at": at(60)}], grace="300")))
    key = "time:once:" + at(60)
    with pytest.raises(contracts.ContractError):
        creators.reserve(item["id"], item["revision"], item["ownerEpoch"] + 1, key, at(60))
    async def unavailable(*args):
        raise OSError("publish unavailable")
    monkeypatch.setattr(bus, "publish", unavailable)
    await runtime.tick(at=at(60))
    assert events.stats()["events"] == 0 and creators.get(item["id"])["producedCount"] == 0

    fresh_events = CanonicalEventStore(tmp_path / "events")
    fresh_bus = EventBus(fresh_events, creators.get)
    fresh_runtime = CreatorRuntime(creators, fresh_bus)
    async def broken_ack(event_id):
        raise OSError("ack failed after cursor commit")
    await asyncio.gather(*(CreatorRuntime(CreatorStore(tmp_path / "creators"), fresh_bus).tick(at=at(60)) for _ in range(2)))
    assert fresh_events.stats()["events"] == 1 and creators.get(item["id"])["producedCount"] == 1
    monkeypatch.setattr(fresh_bus, "release_pin", broken_ack)
    # A later once is used to force the ack boundary after a real publication.
    second = create_time(creators, time_definition("second", config([{"ruleId": "once", "type": "once", "at": at(61)}], grace="300")), key="second")
    await fresh_runtime.tick(at=at(61))
    assert creators.get(second["id"])["producedCount"] == 1
    assert fresh_events.is_pinned(creators.get(second["id"])["lastPublication"]["eventId"])
    recovered_bus = EventBus(fresh_events, creators.get)
    await CreatorRuntime(creators, recovered_bus).tick(at=at(62))
    assert fresh_events.stats() == {"events": 2, "pins": 0}
    retained = fresh_events.page()[0]
    changed = {k: copy.deepcopy(v) for k, v in retained.items() if k not in {"receivedAt", "factDigest", "profileId"}}
    changed["payload"] = {"different": True}
    with pytest.raises(contracts.ContractError) as error:
        fresh_events.append(changed, profile_id=retained["profileId"])
    assert error.value.code == "idempotency_conflict"


@async_test
async def test_fs03_old_provider_generation_can_settle_but_not_reserve(tmp_path):
    creators, events, _, runtime = stack(tmp_path, event_clock=lambda: at("0.5"))
    item = create_time(creators, time_definition("generation", config([interval(seconds="0.5", mode="afterPublication")], grace="300")))
    pending = creators.reserve(item["id"], 1, 1, "time:daily:" + at("0.5"), at("0.5"))
    replacement = TimeProvider()
    replacement.generation = 2
    replacement.release_digest = "f" * 64
    creators.registry.providers[("time", "scheduler")] = replacement
    await runtime.tick(at=at("0.5"))
    assert events.stats()["events"] == 1
    assert creators.get(item["id"])["producedCount"] == 1
    with pytest.raises(contracts.ContractError):
        creators.reserve(item["id"], 1, 1, "time:daily:" + at(1), at(1))


# FS04 ──────────────────────────────────────────────────────────────────────


class FaultPort(NullCompositionPort):
    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.calls = []

    def call(self, phase):
        self.calls.append(phase)
        if self.fail_at == phase:
            raise OSError("injected " + phase)

    def create_disabled(self, operation): self.call("create")
    def verify_links(self, operation): self.call("links")
    def commit_activation(self, operation, token): self.call("activation")
    def activate(self, operation, token, enabled): self.call("active")
    def complete(self, operation): self.call("complete")


def test_fs04_shared_reference_cas_phase_recovery_and_template_composition(tmp_path):
    creators = CreatorStore(tmp_path / "creators")
    shared = create_time(creators, time_definition("shared", config([interval()])))
    shared_ref = creators.ref(shared["id"])
    new_definition = time_definition("owned", config([interval("owned")]))
    graph = {
        "job": {"name": "job"},
        "creators": [
            {"mode": "reference", "key": "shared", "ref": shared_ref},
            {"mode": "create", "key": "owned", "definition": new_definition},
        ],
        "triggers": [{
            "key": "trigger", "definition": {"name": "trigger", "creatorSelector": {"mode": "ids", "ids": []}},
            "targetJobKey": "job", "creatorKeys": ["shared", "owned"],
        }],
    }
    request = dict(request_key="composition-1", desired_graph=graph, expected_refs=[shared_ref], activate=True)
    port = FaultPort("activation")
    coordinator = CompositionCoordinator(creators, port)
    with pytest.raises(OSError):
        coordinator.compose(**request)
    failed = creators.composition_for_key("composition-1")
    owned_id = failed["objectIds"]["creators"]["owned"]
    assert failed["phase"] == "links_verified" and failed["status"] == "failed"
    assert creators.get(owned_id)["state"] == "disabled"
    assert not creators.get(owned_id)["activation"]["committed"]
    assert creators.get(shared["id"])["state"] == "enabled"
    with pytest.raises(contracts.ContractError):
        creators.set_state(owned_id, "enabled", expected_state_revision=0)

    port.fail_at = None
    resumed = coordinator.compose(**request)
    assert resumed["operationId"] == failed["operationId"] and resumed["phase"] == "complete"
    assert len(creators.list()) == 2
    assert creators.get(owned_id)["activation"]["committed"]
    assert creators.get(shared["id"])["revision"] == shared["revision"]
    updated_definition = time_definition("shared-renamed", config([interval()]))
    updated = creators.update(shared["id"], updated_definition, expected_revision=1, request_key="shared-edit")
    assert updated["affectedReferences"][0]["jobId"] == resumed["objectIds"]["job"]
    disabled = creators.set_state(shared["id"], "disabled", expected_state_revision=updated["stateRevision"])
    with pytest.raises(contracts.ContractError):
        creators.delete(shared["id"], expected_revision=disabled["revision"])
    with pytest.raises(contracts.ContractError):
        coordinator.compose(**{**request, "activate": False})

    template = template_definition()
    template["composition"]["creators"] = [{"mode": "create", "key": "creator", "definition": new_definition}]
    template_result = CompositionCoordinator(CreatorStore(tmp_path / "template")).instantiate_template(
        template, request_key="template-1", activate=True,
    )
    trigger = template_result["desiredGraph"]["triggers"][0]
    assert template_result["operation"] == "instantiate-template"
    assert trigger["definition"]["targetJobId"] == template_result["objectIds"]["job"]
    assert trigger["definition"]["creatorSelector"]["ids"] == [template_result["objectIds"]["creators"]["creator"]]


def test_fs04_partial_activation_is_fenced_and_cancel_is_terminal(tmp_path):
    creators = CreatorStore(tmp_path / "creators")
    graph = {
        "creators": [{"mode": "create", "key": "owned", "definition": time_definition("owned", config([interval()]))}],
        "triggers": [],
    }
    request = dict(request_key="cancel-composition", desired_graph=graph, activate=True)
    port = FaultPort("active")
    coordinator = CompositionCoordinator(creators, port)
    with pytest.raises(OSError):
        coordinator.compose(**request)
    operation = creators.composition_for_key("cancel-composition")
    creator_id = operation["objectIds"]["creators"]["owned"]
    assert creators.get(creator_id)["state"] == "disabled"
    assert not creators.get(creator_id)["activation"]["committed"]
    coordinator.cancel("cancel-composition")
    with pytest.raises(contracts.ContractError):
        coordinator.compose(**request)
    assert creators.get(creator_id)["state"] == "disabled"


# FS05 ──────────────────────────────────────────────────────────────────────


def settlement_event(event_id, cause_event_id, *, outcome="completed", transition=1):
    return {
        "schemaVersion": 2, "eventId": event_id, "eventType": "job.settled.v1",
        "eventSchemaRevision": 1, "creatorId": "creator_job_runtime", "creatorRevision": 1,
        "occurrenceKey": event_id, "occurredAt": at(10), "emittedAt": at(10),
        "trustedSystem": {"type": "core", "id": "job-runtime"},
        "payload": {
            "runId": "run_1", "transitionSeq": transition, "outcome": outcome,
            "finishedAt": at(10), "cause": {"type": "event", "eventId": cause_event_id},
            "compositionId": "composition-message",
        },
    }


@async_test
async def test_fs05_trusted_job_settlement_rearms_next_timer_without_action(tmp_path):
    creators, events, _, runtime = stack(tmp_path)
    definition = settlement_definition("after-business-completion", {
        "compositionId": "composition-message", "targetJobId": "job_message",
        "intervalSeconds": "5", "continueOn": ["completed"], "initialFireAt": T0,
    })
    item = create_time(creators, definition)
    item = creators.update(item["id"], settlement_definition("renamed-settlement", definition["config"]),
                           expected_revision=1, request_key="settlement-rename")
    await runtime.tick(at=T0)
    first = events.page()[0]
    assert creators.get(item["id"])["state"] == "disabled"
    settlement = settlement_event("evt_settlement_1", first["eventId"])
    assert creators.accept_settlement(item["id"], settlement)
    armed = creators.get(item["id"])
    assert armed["state"] == "enabled"
    assert armed["cursor"]["rules"]["settlement"]["nextFireAt"] == at(15)
    assert not creators.accept_settlement(item["id"], settlement)
    assert creators.get(item["id"])["cursor"] == armed["cursor"]
    assert not creators.accept_settlement(item["id"], settlement_event("evt_unknown", first["eventId"], outcome="uncertain", transition=2))
    assert not creators.accept_settlement(item["id"], settlement_event("evt_wrong_cause", "evt_not_owned", transition=3))
    await runtime.tick(at=at(14))
    assert events.stats()["events"] == 1
    await runtime.tick(at=at(15))
    assert events.stats()["events"] == 2
    assert creators.get(item["id"])["producedCount"] == 2
    assert events.page()[1]["payload"]["driveReason"] == "settlement"


@async_test
async def test_fs05_settlement_between_event_commit_and_cursor_advance_is_not_lost(tmp_path, monkeypatch):
    creators, events, _, runtime = stack(tmp_path)
    definition = settlement_definition("settlement-race", {
        "compositionId": "composition-message", "targetJobId": "job_message",
        "intervalSeconds": "5", "continueOn": ["completed"], "initialFireAt": T0,
    })
    item = create_time(creators, definition)
    original = creators.advance
    def settle_before_advance(creator_id, event_id, epoch, receipt):
        assert creators.accept_settlement(creator_id, settlement_event("evt_early_settlement", event_id))
        return original(creator_id, event_id, epoch, receipt)
    monkeypatch.setattr(creators, "advance", settle_before_advance)
    await runtime.tick(at=T0)
    current = creators.get(item["id"])
    assert current["producedCount"] == 1 and current["state"] == "enabled"
    assert current["cursor"]["rules"]["settlement"]["nextFireAt"] == at(15)
    assert events.stats()["events"] == 1
