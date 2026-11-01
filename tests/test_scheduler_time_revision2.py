"""Same-instant precision/calendar and frozen-publication regression."""
import asyncio
import copy
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

import pytest

from packages.jobs import cron, final_contracts as fc
from packages.scheduler.creators import CreatorStore, CreatorRuntime, CanonicalEventStore, EventBus
from packages.scheduler.providers import TimeProvider, ProviderRegistry, instant, timestamp, time_definition
from tests.test_job_final_creators import config, interval, T0


def test_same_actual_instant_legacy_cron_next_hundred():
    provider = TimeProvider()
    spec = {"kind": "cron", "cron": "30 1 * * *", "timezone": "UTC"}
    rule = {"ruleId": "cron", "type": "cron", "expression": spec["cron"], "timezone": "UTC"}
    boundary = T0
    for _ in range(100):
        # Old API consumes and emits host-local naive values; use the same instant.
        local = datetime.fromisoformat(boundary.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        legacy = cron.next_fire_after(spec, local)
        canonical = provider.next_after(rule, boundary)
        assert timestamp(legacy.astimezone(timezone.utc)) == canonical["at"]
        boundary = canonical["at"]


def test_decimal_grid_one_hundred_and_once_offsets():
    provider = TimeProvider()
    rule = interval(seconds="0.000000000000000000123")
    boundary = T0
    for n in range(1, 101):
        boundary = provider.next_after(rule, boundary)["at"]
        with localcontext() as ctx:
            ctx.prec = 80
            assert instant(boundary) == instant(T0) + Decimal(n) * Decimal(rule["seconds"])
    once = {"ruleId": "once", "type": "once", "at": "2026-01-01T08:00:00.123456789+08:00"}
    assert provider.next_after(once, T0)["at"] == "2026-01-01T00:00:00.123456789Z"
    assert provider.next_after(once, once["at"]) is None


def test_fixed_interval_is_elapsed_even_through_dst_and_host_unavailability():
    def unavailable(): raise RuntimeError("host resolution must not be needed for elapsed intervals")
    provider = TimeProvider(host_zone=unavailable)
    rule = {**interval(seconds="3600.125"), "anchor": "2026-03-08T01:30:00-05:00"}
    assert provider.next_after(rule, rule["anchor"])["at"] == "2026-03-08T07:30:00.125Z"
    after = {**rule, "advanceMode": "afterPublication"}
    assert provider.next_after(after, "2026-03-08T07:45:00Z")["at"] == "2026-03-08T08:45:00.125Z"


@pytest.mark.parametrize("wall", ["2026-03-08T02:30:00", "2026-11-01T01:30:00", "2026-01-01T09:00:00"])
def test_once_requires_explicit_offset_resolution(wall):
    with pytest.raises(fc.ContractError):
        TimeProvider().validate_config(config([{"ruleId": "once", "type": "once", "at": wall}]))


@pytest.mark.parametrize("kind", ["daily", "weekly", "cron"])
def test_host_default_gap_skip_fold_first_and_evidence(kind):
    provider = TimeProvider(host_zone=lambda: "America/New_York")
    rule = {"ruleId": "calendar", "type": kind}
    rule.update({"expression": "30 2 * * *"} if kind == "cron" else {"time": "02:30:00"})
    if kind == "weekly": rule["weekday"] = 6
    provider.validate_config(config([rule]))
    gap = provider.next_after(rule, "2026-03-08T05:00:00Z")
    assert gap["at"] == ("2026-03-15T06:30:00Z" if kind == "weekly" else "2026-03-09T06:30:00Z")
    assert gap["zoneSelection"] == "host"
    assert gap["skippedOccurrences"] == [{"wallTime": "2026-03-08T02:30:00", "timezone": "America/New_York", "reason": "dst_gap"}]
    fold_rule = {**rule, **({"expression": "30 1 * * *"} if kind == "cron" else {"time": "01:30:00"})}
    first = provider.next_after(fold_rule, "2026-11-01T04:00:00Z")
    assert first["at"] == "2026-11-01T05:30:00Z"
    assert provider.next_after(fold_rule, first["at"])["at"] != "2026-11-01T06:30:00Z"


def test_host_zone_unavailable_and_explicit_override_independent():
    def unavailable(): raise RuntimeError("no host zone")
    provider = TimeProvider(host_zone=unavailable)
    rule = {"ruleId": "day", "type": "daily", "time": "09:00:00"}
    with pytest.raises(fc.ContractError, match="host timezone"):
        provider.validate_config(config([rule]))
    assert provider.next_after({**rule, "timezone": "UTC"}, T0)["at"] == "2026-01-01T09:00:00Z"
    with pytest.raises(fc.ContractError): provider.next_after({**rule, "timezone": "Invalid/Zone"}, T0)


def test_current_host_zone_changes_future_cursor_across_cold_reopen(tmp_path):
    zone = ["UTC"]
    registry = ProviderRegistry((TimeProvider(host_zone=lambda: zone[0]),))
    store = CreatorStore(tmp_path, registry=registry)
    rule = {"ruleId": "day", "type": "daily", "time": "09:00:00"}
    host = store.create(time_definition("host", config([rule])), request_key="host", state="enabled", at=T0)
    fixed = store.create(time_definition("fixed", config([{**rule, "timezone": "UTC"}])), request_key="fixed", state="enabled", at=T0)
    zone[0] = "Asia/Singapore"
    reopened = CreatorStore(tmp_path, registry=registry)
    current = reopened.refresh_time_zone(host["id"], T0)
    assert current["cursor"]["rules"]["day"]["nextFireAt"] == "2026-01-01T01:00:00Z"
    assert current["health"]["lastZoneChange"]["from"] == "UTC"
    assert reopened.refresh_time_zone(fixed["id"], T0)["cursor"] == fixed["cursor"]
    assert current["config"] == host["config"]
    assert current["revision"] == host["revision"]


def test_zone_change_between_scan_and_reserve_cannot_claim_stale_occurrence(tmp_path):
    zone = ["UTC"]
    store = CreatorStore(tmp_path, registry=ProviderRegistry((TimeProvider(host_zone=lambda: zone[0]),)))
    rule = {"ruleId": "day", "type": "daily", "time": "09:00:00"}
    item = store.create(time_definition("host", config([rule])), request_key="clock", state="enabled", at=T0)
    zone[0] = "America/New_York"
    assert store.reserve(item["id"], 1, 1, "time:day:2026-01-01T09:00:00Z", "2026-01-01T09:00:00Z") is None
    current = store.get(item["id"])
    assert current["cursor"]["rules"]["day"]["nextFireAt"] == "2026-01-01T14:00:00Z"
    assert not current["drives"] and current["pendingPublication"] is None


def test_pending_event_and_ack_keep_identity_through_zone_change(tmp_path):
    zone = ["UTC"]
    registry = ProviderRegistry((TimeProvider(host_zone=lambda: zone[0]),))
    store = CreatorStore(tmp_path / "creators", registry=registry)
    rule = {"ruleId": "day", "type": "daily", "time": "09:00:00"}
    item = store.create(time_definition("host", config([rule], grace="300")), request_key="host", state="enabled", at=T0)
    point = "2026-01-01T09:00:00Z"
    pending = store.reserve(item["id"], 1, 1, "time:day:" + point, point)
    zone[0] = "America/New_York"
    assert store.refresh_time_zone(item["id"], point)["pendingPublication"] == pending
    events = CanonicalEventStore(tmp_path / "events", clock=lambda: point)
    bus = EventBus(events, store.get)
    runtime = CreatorRuntime(store, bus)
    asyncio.run(runtime.tick(at=point))
    assert events.page()[0]["eventId"] == pending["envelope"]["eventId"]
    assert events.page()[0]["payload"]["timezone"] == "UTC"
    asyncio.run(runtime.tick(at=point))
    assert events.stats() == {"events": 1, "pins": 0}
    current = store.get(item["id"])
    assert current["producedCount"] == 1
    assert current["cursor"]["rules"]["day"]["nextFireAt"] == "2026-01-01T14:00:00Z"
    assert current["lastPublication"]["eventId"] == pending["envelope"]["eventId"]


def test_platform_host_zone_returns_real_zoneinfo_identity():
    from packages.core.platform.timezones import host_zone_name
    assert ZoneInfo(host_zone_name()).key


def test_platform_zone_discovery_failure_never_defaults_to_utc(monkeypatch):
    import tzlocal
    from packages.core.platform.timezones import host_zone_name
    def no_config():
        import warnings
        warnings.warn("Can not find any timezone configuration, defaulting to UTC.")
        return ZoneInfo("UTC")
    monkeypatch.setattr(tzlocal, "reload_localzone", no_config)
    with pytest.raises(UserWarning): host_zone_name()


def test_legacy_fractional_grid_and_once_same_instant():
    provider = TimeProvider()
    anchor = datetime(2026, 1, 1)
    spec = {"kind": "interval", "interval_sec": .125, "anchor": anchor.isoformat()}
    rule = interval(seconds="0.125")
    rule["anchor"] = timestamp(anchor.astimezone(timezone.utc))
    old_boundary = anchor
    boundary = rule["anchor"]
    for _ in range(100):
        old_boundary = cron.next_fire_after(spec, old_boundary)
        boundary = provider.next_after(rule, boundary)["at"]
        assert timestamp(old_boundary.astimezone(timezone.utc)) == boundary
    point = cron.next_fire_after({"kind": "once", "at": "2026-01-02T09:00:00"}, anchor)
    once = {"ruleId": "once", "type": "once", "at": timestamp(point.astimezone(timezone.utc))}
    assert provider.next_after(once, rule["anchor"])["at"] == once["at"]


@pytest.mark.parametrize("state,policy,outcome,count", [
    ("enabled", "skip", "skipped", 0), ("enabled", "fire_now", "published", 1),
    ("paused", "fire_now", "paused", 0),
])
def test_calendar_pause_misfire_backfill_and_limits(tmp_path, state, policy, outcome, count):
    provider = TimeProvider(host_zone=lambda: "UTC")
    store = CreatorStore(tmp_path / "creators", registry=ProviderRegistry((provider,)))
    cfg = config([{"ruleId": "day", "type": "daily", "time": "09:00:00"}], policy=policy)
    cfg["driveLimit"] = {"maxEvents": 1, "initialProducedCount": 0}
    item = store.create(time_definition("clock", cfg), request_key="clock", state="enabled", at=T0)
    if state == "paused": store.set_state(item["id"], "paused", expected_state_revision=0)
    actual = "2026-01-01T10:00:00Z"
    events = CanonicalEventStore(tmp_path / "events", clock=lambda: actual)
    runtime = CreatorRuntime(store, EventBus(events, store.get))
    asyncio.run(runtime.tick(at=actual))
    current = store.get(item["id"])
    assert current["drives"][0]["outcome"] == outcome
    assert current["producedCount"] == count
    assert events.stats() == {"events": count, "pins": 0}
    assert current["cursor"]["rules"]["day"]["nextFireAt"] == "2026-01-02T09:00:00Z"
    if count:
        assert current["state"] == "disabled"
        assert events.page()[0]["payload"]["driveReason"] == "backfill"
        assert events.page()[0]["occurredAt"] == actual
    if state == "paused":
        store.set_state(item["id"], "enabled", expected_state_revision=current["stateRevision"])
        asyncio.run(runtime.tick(at=actual))
        assert events.stats()["events"] == 0


def test_gap_reason_persists_in_cursor_after_cold_reopen(tmp_path):
    registry = ProviderRegistry((TimeProvider(host_zone=lambda: "America/New_York"),))
    store = CreatorStore(tmp_path, registry=registry)
    item = store.create(time_definition("gap", config([{"ruleId": "day", "type": "daily", "time": "02:30:00"}])),
                        request_key="gap", at="2026-03-08T05:00:00Z")
    reopened = CreatorStore(tmp_path, registry=registry).get(item["id"])
    occurrence = reopened["cursor"]["rules"]["day"]["nextOccurrence"]
    assert occurrence["at"] == "2026-03-09T06:30:00Z"
    assert occurrence["skippedOccurrences"][0]["reason"] == "dst_gap"
