"""Contract tests for the live Codex quota API/MCP projection."""

import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as session_module
from packages.core import worker
from packages.core.adapters.codex.adapter import CodexAdapter
from packages.core.codex_quota import format_codex_quota


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
    assert result["source"]["event"] == "account/rateLimits/updated"


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
    assert w.native_rate_limits_updated_at

    worker.clear_native_runtime_state(w)
    assert w.native_rate_limits is None
    assert w.native_rate_limits_updated_at is None


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
