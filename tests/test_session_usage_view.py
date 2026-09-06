"""Contract tests for the persisted Session input/output/cache projection."""

from packages.core import session


def _session(**kwargs):
    return session.Session(
        id="ses-usage",
        name="usage",
        updated_at="2026-09-07T01:02:03+00:00",
        **kwargs,
    )


def test_usage_view_aggregates_provider_aliases_once_and_keeps_cache_separate():
    value = session.session_usage_view(_session(raw_usage={
        "codex": {"model": "gpt", "rawUsage": {
            "input_tokens": 100,
            "cached_input_tokens": 999,
            "cache_read_tokens": 40,  # precedence alias must not be added again
            "cache_write_input_tokens": 7,
            "output_tokens": 20,
        }},
        "canonical": {"model": "m", "rawUsage": {
            "prompt_tokens": 10,
            "prompt_cache_hit_tokens": 3,
            "prompt_cache_miss_tokens": 4,
            "completion_tokens": 5,
            "cost": 0.25,
        }},
    }))

    assert value["input"] == 110
    assert value["output"] == 25
    assert value["cache"] == {"read": 43, "write": 11, "total": 54}
    assert value["total"] == {"tokens": 135, "credit": 0.25}
    assert value["source"]["kind"] == "Session.rawUsage"
    assert value["updatedAt"] == "2026-09-07T01:02:03+00:00"


def test_usage_view_distinguishes_missing_from_explicit_zero():
    value = session.session_usage_view(_session(raw_usage={
        "model": {"rawUsage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0,
            "credit": 0.0,
        }},
    }))
    assert value["input"] == 0
    assert value["output"] == 0
    assert value["cache"] == {"read": 0, "write": 0, "total": 0}
    assert value["total"] == {"tokens": 0, "credit": 0.0}

    missing = session.session_usage_view(_session())
    assert missing["input"] is None
    assert missing["output"] is None
    assert missing["cache"] == {"read": None, "write": None, "total": None}
    assert missing["total"] == {"tokens": None, "credit": None}
    assert missing["source"]["kind"] is None


def test_usage_view_falls_back_to_legacy_total_usage_without_raw_payload():
    value = session.session_usage_view(_session(total_usage={
        "prompt_tokens": 12,
        "cache_hit_tokens": 4,
        "cache_miss_tokens": 2,
        "completion_tokens": 8,
        "credit": 0.5,
    }))
    assert value["input"] == 12
    assert value["output"] == 8
    assert value["cache"] == {"read": 4, "write": 2, "total": 6}
    assert value["total"] == {"tokens": 20, "credit": 0.5}
    assert value["source"]["kind"] == "Session.totalUsage"
    assert "rawUsage" not in value
    assert "totalUsage" not in value
