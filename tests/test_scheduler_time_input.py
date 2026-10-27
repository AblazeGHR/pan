"""Read-only Pan-host local input resolution; canonical rules stay instants."""
import copy
import pytest

from packages.scheduler.providers import TimeProvider
from tests.test_job_final_http_service import h


def test_plain_local_time_uses_current_pan_host_not_browser_and_keeps_fraction():
    current = ["Asia/Singapore"]
    provider = TimeProvider(host_zone=lambda: current[0])
    value = "2027-01-02 09:30:12.12345678901234567890123456789"
    result = provider.resolve_input(value)
    assert result == {"status": "resolved", "at": "2027-01-02T01:30:12.12345678901234567890123456789Z",
        "localTime": "2027-01-02T09:30:12.12345678901234567890123456789+08:00",
        "timezone": "Asia/Singapore", "zoneSelection": "host", "input": value}
    current[0] = "America/New_York"
    assert provider.resolve_input(value)["at"] == "2027-01-02T14:30:12.12345678901234567890123456789Z"
    assert provider.resolve_input(value, "Asia/Singapore")["at"] == result["at"]
    assert provider.resolve_input("2027-01-02T09:30")["at"] == "2027-01-02T14:30:00Z"


def test_gap_reports_nonexistent_and_fold_requires_two_actual_instants():
    provider = TimeProvider(host_zone=lambda: "America/New_York")
    gap = provider.resolve_input("2026-03-08 02:30:00.123456789")
    assert gap["status"] == "nonexistent" and gap["reason"] == "dst_gap"
    assert "at" not in gap and "options" not in gap
    fold = provider.resolve_input("2026-11-01 01:30:00.123456789")
    assert fold["status"] == "ambiguous" and "at" not in fold
    assert fold["options"] == [
        {"at": "2026-11-01T05:30:00.123456789Z", "localTime": "2026-11-01T01:30:00.123456789-04:00"},
        {"at": "2026-11-01T06:30:00.123456789Z", "localTime": "2026-11-01T01:30:00.123456789-05:00"}]
    for choice in fold["options"]:
        assert provider.resolve_input(choice["localTime"])["at"] == choice["at"]


def test_explicit_offset_does_not_require_host_and_bad_zones_never_fallback():
    def unavailable(): raise RuntimeError("host unavailable")
    provider = TimeProvider(host_zone=unavailable)
    assert provider.resolve_input("2026-11-01T01:30:00.123456789-05:00")["at"] == "2026-11-01T06:30:00.123456789Z"
    assert provider.resolve_input("2027-01-01 09:00:00")["status"] == "unavailable"
    assert provider.resolve_input("2027-01-01 09:00:00", "Unknown/Zone")["status"] == "unavailable"
    assert provider.resolve_input("2027-01-01 09:00:00", "+05:30")["at"] == "2027-01-01T03:30:00Z"


@pytest.mark.parametrize("value", ["", "tomorrow", "2027-02-30 09:00", "2027-01-01 25:00", "2027-01-01T00:00:00+99:00"])
def test_invalid_calendar_or_offset_is_structured(value):
    assert TimeProvider().resolve_input(value)["status"] == "invalid"


def test_authenticated_preview_is_read_only_and_saved_once_is_a_fixed_instant(h):
    provider = h.service.creators.registry.providers[("time", "scheduler")]
    zone = ["Asia/Singapore"]
    provider.host_zone = lambda: zone[0]
    def snapshot():
        with h.service.transaction(readonly=True) as db:
            return list(db.iterdump())
    before = snapshot()
    response = h.client.post("/api/event-creators/time-input/resolve", json={"value": "2027-01-01 09:00:12.123456789"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["at"] == "2027-01-01T01:00:12.123456789Z"
    assert snapshot() == before
    from packages.scheduler.providers import time_definition
    from tests.test_job_final_creators import config
    definition = time_definition("once", config([{"ruleId": "once", "type": "once", "at": result["at"]}]))
    saved = h.client.post("/api/event-creators", json={"definition": definition, "expectedRevision": 0, "requestKey": "resolved-once"})
    assert saved.status_code == 201, saved.text
    item = saved.json()["result"]["object"]
    original = h.service.creators.get(item["id"])
    zone[0] = "America/New_York"
    assert h.service.creators.refresh_time_zone(item["id"], "2026-01-01T00:00:00Z")["cursor"] == original["cursor"]
    h.client.headers.pop("Authorization")
    assert h.client.post("/api/event-creators/time-input/resolve", json={"value": "2027-01-01 09:00"}).status_code == 401
