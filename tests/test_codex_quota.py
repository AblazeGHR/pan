"""Contract tests for the live Codex quota API/MCP projection."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as session_module
from packages.core import worker
from packages.core.adapters.codex.adapter import CodexAdapter
from packages.core.codex_quota import format_codex_quota, normalize_codex_rate_limits


def _rate_limits():
    return {
        "primary": {
            "usedPercent": 16,
            "windowDurationMins": 300,
            "resetsAt": 1788760000,
        },
        "secondary": {
            "usedPercent": 70,
            "windowDurationMins": 10080,
            "resetsAt": 1788766414,
            "credits": {"balance": "500"},
        },
    }


def test_codex_quota_maps_first_to_five_hours_and_secondary_to_week():
    result = format_codex_quota(
        _rate_limits(),
        session_id="ses-codex",
        worker_id="worker-codex",
        updated_at="2026-09-07T01:02:03+00:00",
        received_at="2026-09-07T01:02:03+00:00",
    )

    first = result["windows"]["first"]
    secondary = result["windows"]["secondary"]
    assert first["name"] == "5h"
    assert first["label"] == "five_hour"
    assert first["providerKey"] == "primary"
    assert first["usage"] == {
        "usedPercent": 16,
        "remainingPercent": 84,
        "used": None,
        "remaining": None,
        "limit": None,
    }
    assert first["windowDurationMins"] == 300
    assert secondary["name"] == "week"
    assert secondary["label"] == "weekly"
    assert secondary["providerKey"] == "secondary"
    assert secondary["usage"]["usedPercent"] == 70
    assert secondary["usage"]["remainingPercent"] == 30
    assert secondary["windowDurationMins"] == 10080
    assert secondary["raw"]["credits"]["balance"] == "500"
    assert result["updatedAt"] == "2026-09-07T01:02:03+00:00"
    assert result["receivedAt"] == "2026-09-07T01:02:03+00:00"
    assert result["source"]["event"] == "account/rateLimits/updated"
    assert result["source"]["providerUpdatedAt"] is None
    assert "local Pan Worker receive time" in result["source"]["timestampMeaning"]


def test_codex_quota_missing_provider_window_is_explicitly_unknown():
    result = format_codex_quota(
        {"secondary": {"usedPercent": 2}},
        session_id="ses-codex",
        worker_id="worker-codex",
        updated_at=None,
    )

    first = result["windows"]["first"]
    assert first["status"] == "missing"
    assert first["usage"] == {
        "usedPercent": None,
        "remainingPercent": None,
        "used": None,
        "remaining": None,
        "limit": None,
    }
    assert result["updatedAt"] is None
    assert result["receivedAt"] is None


def test_codex_quota_uses_actual_duration_for_wham_and_preserves_unknown_window():
    normalized = normalize_codex_rate_limits({
        "rate_limit": {
            "primary_window": {"limit_window_seconds": 18000, "used_percent": 1},
            "secondary_window": {"limit_window_seconds": 2592000, "used_percent": 2},
            "burst_window": {"limit_window_seconds": 43200, "used_percent": 3},
        },
    })

    assert normalized["windows"]["first"]["kind"] == "five_hour"
    assert normalized["windows"]["secondary"]["kind"] == "monthly"
    assert normalized["windows"]["provider:burst_window"]["kind"] == "unknown"
    assert normalized["windows"]["provider:burst_window"]["raw"]["used_percent"] == 3


def test_codex_rate_limit_update_records_source_timestamp_and_clears_on_respawn():
    w = worker.Worker(
        worker_id="worker-rate-limit-contract",
        session_id="ses-rate-limit-contract",
        adapter=CodexAdapter(),
    )
    worker._update_pending_interactions(w, {
        "type": "codex.rate_limits",
        "rate_limits": {"primary": {"usedPercent": 1}},
    })
    assert w.native_rate_limits == {"primary": {"usedPercent": 1}}
    assert w.native_rate_limits_received_at
    assert w.native_rate_limits_updated_at
    assert w.native_rate_limits_received_at == w.native_rate_limits_updated_at

    worker.clear_native_runtime_state(w)
    assert w.native_rate_limits is None
    assert w.native_rate_limits_received_at is None
    assert w.native_rate_limits_updated_at is None


def test_worker_rate_limit_push_is_forwarded_to_global_store(monkeypatch):
    class Adapter:
        name = "codex"

        def parse_event(self, _line):
            return {"type": "codex.rate_limits", "rate_limits": _rate_limits()}

        def is_init_event(self, _event):
            return False

        def is_assistant_event(self, _event):
            return False

        def is_result_event(self, _event):
            return False

    async def lines(_worker):
        yield b"{}\n"

    persisted = []
    monkeypatch.setattr(worker, "_iter_stdout_lines", lines)
    monkeypatch.setattr(
        worker._codex_quota_store,
        "update_current_profile",
        lambda *args, **kwargs: persisted.append((args, kwargs)) or ({}, True),
    )
    async def noop(*_args, **_kwargs):
        return None
    monkeypatch.setattr(worker, "_bcast", noop)
    monkeypatch.setattr(worker, "_enqueue_zombie_report", noop)

    instance = worker.Worker(
        worker_id="worker-push-contract",
        session_id="ses-push-contract",
        adapter=Adapter(),
        process=SimpleNamespace(returncode=0),
    )
    worker.workers[instance.worker_id] = instance
    try:
        asyncio.run(worker._read_stdout(instance))
    finally:
        worker.workers.pop(instance.worker_id, None)

    assert persisted
    assert persisted[0][0][0] == _rate_limits()
    assert persisted[0][1]["source"] == "app-server-push"


def test_total_usage_bridges_cache_aliases_without_double_counting():
    result = session_module.compute_total_usage({
        "codex": {
            "rawUsage": {
                "prompt_tokens": 10,
                "cache_read_tokens": 1722368,
                "cache_write_tokens": 12,
                "completion_tokens": 5,
            },
        },
        "canonical": {
            "rawUsage": {
                "prompt_cache_hit_tokens": 3,
                "cache_read_tokens": 999,
                "prompt_cache_miss_tokens": 4,
                "cache_write_tokens": 999,
            },
        },
    })

    assert result["cache_hit_tokens"] == 1722368 + 3
    assert result["cache_miss_tokens"] == 12 + 4
    assert result["prompt_tokens"] == 10
    assert result["completion_tokens"] == 5
