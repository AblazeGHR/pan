"""Persistence and profile-isolation tests for account-scoped Codex quota."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from packages.core.codex_quota_store import (
    CodexProfile,
    CodexQuotaStore,
    read_codex_credentials,
    resolve_profile_identity,
)


def _limits(*, primary=16, secondary=70):
    return {
        "primary": {"usedPercent": primary, "windowDurationMins": 300},
        "secondary": {"usedPercent": secondary, "windowDurationMins": 10080},
    }


def _profile(name: str) -> CodexProfile:
    return CodexProfile(Path(f"C:/codex/{name}"), None, None, name)


def test_profile_key_includes_codex_home_and_available_chatgpt_identity(tmp_path):
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({
        "tokens": {
            "access_token": "token-only-in-memory",
            "account_id": "account-a",
            "chatgpt_account_id": "workspace-a",
        },
    }), encoding="utf-8")

    profile = resolve_profile_identity(home)
    credentials, status = read_codex_credentials(profile)
    assert profile.home == home.resolve()
    assert profile.account_id == "account-a"
    assert profile.chatgpt_account_id == "workspace-a"
    assert len(profile.profile_key) == 32
    assert status == "valid"
    assert credentials.access_token == "token-only-in-memory"

    (home / "auth.json").write_text(json.dumps({
        "auth_mode": "api_key",
        "access_token": "not-used",
    }), encoding="utf-8")
    _, status = read_codex_credentials(profile)
    assert status == "unsupported_auth_mode"


def test_store_atomic_schema_reload_and_secret_exclusion(tmp_path):
    profile = _profile("profile-a")
    store = CodexQuotaStore(profile, tmp_path)
    limits = _limits()
    limits["access_token"] = "should-never-be-written"
    limits["refresh_token"] = "should-never-be-written"
    record, updated = store.update(
        limits,
        observed_at="2026-09-09T01:02:03+00:00",
        received_at="2026-09-09T01:02:04+00:00",
    )

    assert updated is True
    assert record["schemaVersion"] == 1
    assert record["profileKey"] == "profile-a"
    assert record["observedAt"] == "2026-09-09T01:02:03+00:00"
    assert record["receivedAt"] == "2026-09-09T01:02:04+00:00"
    persisted = json.loads((tmp_path / "profile-a.json").read_text(encoding="utf-8"))
    assert "access_token" not in json.dumps(persisted)
    assert "refresh_token" not in json.dumps(persisted)
    assert "session_id" not in json.dumps(persisted)
    assert "worker_id" not in json.dumps(persisted)
    assert not list(tmp_path.glob(".*.tmp"))

    reloaded = CodexQuotaStore(profile, tmp_path).load()
    assert reloaded["windows"]["first"]["usage"]["usedPercent"] == 16
    assert reloaded["windows"]["secondary"]["usage"]["usedPercent"] == 70


def test_store_profile_isolation_and_empty_or_partial_updates_keep_last_good(tmp_path):
    first = CodexQuotaStore(_profile("profile-a"), tmp_path)
    second = CodexQuotaStore(_profile("profile-b"), tmp_path)
    first.update(_limits(), observed_at="2026-09-09T01:00:00+00:00")
    second.update({"primary": {"usedPercent": 91, "windowDurationMins": 300}})

    unchanged, did_update = first.update({}, observed_at="2026-09-09T02:00:00+00:00")
    assert did_update is False
    assert unchanged["windows"]["secondary"]["usage"]["usedPercent"] == 70

    partial, did_update = first.update(
        {"primary": {"usedPercent": 22, "windowDurationMins": 300}},
        observed_at="2026-09-09T03:00:00+00:00",
    )
    assert did_update is True
    assert partial["windows"]["first"]["usage"]["usedPercent"] == 22
    assert partial["windows"]["secondary"]["usage"]["usedPercent"] == 70
    assert second.load()["windows"]["first"]["usage"]["usedPercent"] == 91


def test_store_rejects_delayed_older_snapshot(tmp_path):
    store = CodexQuotaStore(_profile("ordered-profile"), tmp_path)
    store.update(
        {"secondary": {"usedPercent": 70, "windowDurationMins": 10080}},
        observed_at="2026-09-09T03:00:00+00:00",
    )
    record, did_update = store.update(
        {"secondary": {"usedPercent": 2, "windowDurationMins": 10080}},
        observed_at="2026-09-09T02:00:00+00:00",
    )
    assert did_update is False
    assert record["windows"]["secondary"]["usage"]["usedPercent"] == 70


def test_store_concurrent_incremental_updates_do_not_drop_windows(tmp_path):
    store = CodexQuotaStore(_profile("profile-concurrent"), tmp_path)

    def write(index: int):
        if index % 2:
            return store.update({"secondary": {"usedPercent": index, "windowDurationMins": 10080}})
        return store.update({"primary": {"usedPercent": index, "windowDurationMins": 300}})

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(16)))

    final = store.load()
    assert final["windows"]["first"]["status"] == "available"
    assert final["windows"]["secondary"]["status"] == "available"


def test_store_reads_legacy_rate_limits_shape_without_session_json(tmp_path):
    profile = _profile("legacy-profile")
    path = tmp_path / "legacy-profile.json"
    path.write_text(json.dumps({
        "rateLimits": _limits(),
        "updatedAt": "2026-09-08T00:00:00+00:00",
    }), encoding="utf-8")

    loaded = CodexQuotaStore(profile, tmp_path).load()
    assert loaded["schemaVersion"] == 1
    assert loaded["source"] == "legacy"
    assert loaded["windows"]["secondary"]["kind"] == "weekly"
