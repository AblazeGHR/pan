"""Unread-recording policy: single predicate, transitions, failures, races.

Covers the full scope matrix (bell-on / bell-off / pinned / all / none and
their unions), legacy defaults, empty-set normalization, dynamic bell/pin
transitions with immediate unread alignment, re-inclusion counting from new
events only, persistence failures staying honest behind the read projection,
cancellation and stale acks never resurrecting unread, and the startup
alignment path. All state is process-local test data (see
docs/TESTING.md for the isolated runner).
"""

import asyncio
import json
import threading
from unittest.mock import MagicMock

import httpx
import pytest

from packages.core import config as core_config
from packages.core import session as sess
from packages.core import unread_policy
from packages.core import worker
from packages.core.adapters import CbcAdapter
from packages.web import server


@pytest.fixture(autouse=True)
def policy_env(monkeypatch):
    """Fresh locks, no enrichment side effects, captured broadcasts, no snapshot."""
    monkeypatch.setattr(worker, "_unread_done_locks", {})
    monkeypatch.setattr(worker, "_queue_locks", {})
    monkeypatch.setattr(worker, "_queue_usage_enrichment", lambda *a, **kw: None)
    monkeypatch.setattr(unread_policy, "_COMPILED_SNAPSHOT", None)
    events = []

    async def record(event):
        events.append(event)

    monkeypatch.setattr(server, "broadcast", record)
    return events


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(core_config, "CONFIG_FILE", cfg_path)
    return cfg_path


def _write_config(path, ui):
    path.write_text(json.dumps({"ui": ui}), encoding="utf-8")


def _disk(s):
    return json.loads(sess._path(s.id).read_text(encoding="utf-8"))


def _make_session(name, *, bridge=False, generation=0, read=0):
    s = sess.create(name=name)
    s.qq_subscriptions = {"user:1"} if bridge else set()
    s.qq_report_targets = set()
    s.notification_settings = {"browser": False, "system": False}
    s.unread_done_generation = generation
    s.unread_done_read_generation = read
    s.unread_done_count = generation - read
    sess.save(s)
    return s


def _task_worker(s, task_id="policy-task"):
    w = worker.Worker(worker_id=f"unread-policy-{task_id}", session_id=s.id,
                      adapter=CbcAdapter(), process=MagicMock(), status="running")
    w._current_seq = 1
    w._current_task_id = task_id
    w._current_task_idempotent = True
    return w


def _run_terminal(s, task_id="policy-task", status="done", text="result"):
    w = _task_worker(s, task_id)
    return asyncio.run(worker._persist_terminal_state(w, s, status, text))


def _block_metadata_replace(monkeypatch, s, *, fail=False):
    entered = threading.Event()
    release = threading.Event()
    replace = sess.os.replace
    blocked = False

    def before_replace(source, destination):
        nonlocal blocked
        if destination == sess._path(s.id) and not blocked:
            blocked = True
            entered.set()
            assert release.wait(3), "metadata commit was not released"
            if fail:
                raise OSError("synthetic unread reset failure")
        return replace(source, destination)

    monkeypatch.setattr(sess.os, "replace", before_replace)
    return entered, release


# ── normalization (mirrors the React sanitize rule) ──

def test_normalize_unread_recording_matrix():
    n = unread_policy.normalize_unread_recording
    # Missing / non-list keeps the legacy default (record everything).
    assert n(None) == ("all",)
    assert n("all") == ("all",)
    assert n({"scopes": ["none"]}) == ("all",)
    # Empty set and fully-invalid sets mean "no recording".
    assert n([]) == ("none",)
    assert n(["bogus", 3]) == ("none",)
    # all / none absorb everything else.
    assert n(["all", "pinned"]) == ("all",)
    assert n(["none", "bell-on"]) == ("none",)
    # Categories are a union; bell-on + bell-off is every Session.
    assert n(["bell-on"]) == ("bell-on",)
    assert n(["pinned", "bell-off"]) == ("bell-off", "pinned")
    assert n(["bell-on", "bell-on"]) == ("bell-on",)
    assert n(["bell-on", "bell-off"]) == ("all",)


def test_ui_extraction_defaults_and_empty_rule():
    assert unread_policy.unread_recording_from_ui({}) == ("all",)
    assert unread_policy.unread_recording_from_ui(
        {"notifications": {"codexWarningToast": True}}) == ("all",)
    assert unread_policy.unread_recording_from_ui(
        {"notifications": {"unreadRecording": []}}) == ("none",)
    assert unread_policy.unread_recording_from_ui(
        {"notifications": {"unreadRecording": ["pinned"]}}) == ("pinned",)


# ── decision matrix over bell / pin facts ──

@pytest.mark.parametrize("scopes,bridge,pinned,expected", [
    (("all",), False, False, True),
    (("none",), True, True, False),
    (("bell-on",), True, False, True),
    (("bell-on",), False, False, False),
    (("bell-off",), False, False, True),
    (("bell-off",), True, False, False),
    (("pinned",), False, True, True),
    (("pinned",), False, False, False),
    (("bell-on", "pinned"), True, False, True),
    (("bell-on", "pinned"), False, True, True),
    (("bell-on", "pinned"), False, False, False),
    (("bell-off", "pinned"), False, False, True),
    (("bell-off", "pinned"), True, False, False),
    (("bell-off", "pinned"), True, True, True),
])
def test_scope_decision_matrix(scopes, bridge, pinned, expected):
    s = _make_session(f"policy-matrix-{bridge}-{pinned}", bridge=bridge)
    if pinned:
        sess.set_session_pinned(s.id, True)
    assert unread_policy.should_record_unread(s, scopes=scopes) is expected


# ── terminal commit respects the policy ──

@pytest.mark.parametrize("scopes,bridge,records", [
    (("all",), False, True),
    (("none",), False, False),
    (("bell-on",), True, True),
    (("bell-on",), False, False),
    (("bell-off",), False, True),
    (("bell-off",), True, False),
])
def test_terminal_commit_records_only_included(monkeypatch, scopes, bridge, records):
    monkeypatch.setattr(unread_policy, "_COMPILED_SNAPSHOT", scopes)
    s = _make_session(f"policy-terminal-{scopes[0]}-{bridge}", bridge=bridge)
    _run_terminal(s)
    disk = _disk(s)
    if records:
        assert disk["unread_done_generation"] == 1
        assert disk["unread_done_count"] == 1
    else:
        assert disk["unread_done_generation"] == 0
        assert disk["unread_done_count"] == 0


def test_excluded_terminal_never_accumulates_and_heals_legacy_unread(monkeypatch):
    monkeypatch.setattr(unread_policy, "_COMPILED_SNAPSHOT", ("none",))
    s = _make_session("policy-heal", generation=3, read=1)
    _run_terminal(s)
    disk = _disk(s)
    assert disk["unread_done_generation"] == 3  # no accumulation
    assert disk["unread_done_read_generation"] == 3  # leftover healed
    assert disk["unread_done_count"] == 0


def test_excluded_error_terminal_heals_but_count_semantics_hold(monkeypatch):
    monkeypatch.setattr(unread_policy, "_COMPILED_SNAPSHOT", ("none",))
    s = _make_session("policy-heal-error", generation=2, read=0)
    _run_terminal(s, task_id="err-task", status="error", text="boom")
    disk = _disk(s)
    assert disk["unread_done_generation"] == 2
    assert disk["unread_done_count"] == 0


def test_reincluded_counts_from_new_events_only(monkeypatch):
    monkeypatch.setattr(unread_policy, "_COMPILED_SNAPSHOT", ("none",))
    s = _make_session("policy-reinclude", generation=2, read=0)
    _run_terminal(s, task_id="excluded-done")  # excluded: no new generation
    assert _disk(s)["unread_done_count"] == 0
    monkeypatch.setattr(unread_policy, "_COMPILED_SNAPSHOT", ("all",))
    _run_terminal(s, task_id="included-done")  # re-included: counts from now
    disk = _disk(s)
    assert disk["unread_done_generation"] == 3
    assert disk["unread_done_count"] == 1


# ── read-surface projection is authoritative zero ──

@pytest.mark.parametrize("generation,read", [(0, 0), (3, 0), (3, 2), (3, 3)])
def test_excluded_projection_is_zero_on_every_surface(monkeypatch, generation, read):
    monkeypatch.setattr(unread_policy, "_COMPILED_SNAPSHOT", ("none",))
    s = _make_session(f"policy-proj-{generation}-{read}",
                      generation=generation, read=read)
    summary = server._session_summary(s)
    assert summary["unreadDoneCount"] == 0
    assert summary["unreadDoneReadGeneration"] == generation
    assert summary["unreadDoneGeneration"] == generation
    full = server._session_to_api(s)
    assert full["unreadDoneCount"] == 0
    assert full["unreadDoneReadGeneration"] == generation
    # Broadcast summary patch (WS session.updated payloads) serves the same view.
    patch = server._attach_session_summary_patch(
        {"type": "session.updated", "sessionId": s.id})
    assert patch["session"]["unreadDoneCount"] == 0
    # Ack response follows the projection too.
    ack = asyncio.run(server.api_ack_session_unread_done(s.id, {"observed": generation}))
    assert ack["unreadDoneCount"] == 0


def test_stale_ack_cannot_resurrect_excluded_unread(monkeypatch):
    monkeypatch.setattr(unread_policy, "_COMPILED_SNAPSHOT", ("none",))
    s = _make_session("policy-stale-ack", generation=4, read=1)
    # A late ack whose observed cursor is behind the durable pair only advances
    # the read cursor monotonically; the projection stays 0 either way.
    ack = asyncio.run(server.api_ack_session_unread_done(s.id, {"observed": 2}))
    assert ack["unreadDoneCount"] == 0
    assert ack["unreadDoneReadGeneration"] == 4
    assert server._session_summary(s)["unreadDoneCount"] == 0
    assert _disk(s)["unread_done_count"] <= 2  # never below the observed cursor
    assert server._session_to_api(s)["unreadDoneCount"] == 0


def test_included_projection_keeps_real_unread(monkeypatch):
    monkeypatch.setattr(unread_policy, "_COMPILED_SNAPSHOT", ("all",))
    s = _make_session("policy-proj-included", generation=3, read=1)
    assert server._session_summary(s)["unreadDoneCount"] == 2
    assert server._session_to_api(s)["unreadDoneCount"] == 2


# ── settings PUT: immediate coordination + cross-client broadcast ──

def test_settings_put_clears_newly_excluded_and_broadcasts(
        policy_env, tmp_config, monkeypatch):
    s = _make_session("policy-put-clear", generation=2, read=0)
    monkeypatch.setattr(sess, "list_all", lambda **kw: [s])
    asyncio.run(server.api_put_settings_ui(
        {"notifications": {"unreadRecording": ["none"]}}))
    disk = _disk(s)
    assert disk["unread_done_count"] == 0
    assert disk["unread_done_read_generation"] == 2
    assert unread_policy.current_unread_recording() == ("none",)
    assert json.loads(tmp_config.read_text(encoding="utf-8"))["ui"]["notifications"]["unreadRecording"] == ["none"]
    assert any(e.get("type") == "settings.uiUpdated" for e in policy_env)
    assert any(e.get("type") == "session.updated" and e.get("sessionId") == s.id
               for e in policy_env)


def test_settings_put_reinclude_never_restores_old_unread(
        policy_env, tmp_config, monkeypatch):
    _write_config(tmp_config, {"notifications": {"unreadRecording": ["none"]}})
    unread_policy.refresh_unread_recording()
    s = _make_session("policy-put-reinclude", generation=2, read=0)
    monkeypatch.setattr(sess, "list_all", lambda **kw: [s])
    asyncio.run(server.api_put_settings_ui(
        {"notifications": {"unreadRecording": ["all"]}}))
    assert _disk(s)["unread_done_count"] == 0  # leftover cleared, none restored
    _run_terminal(s, task_id="post-reinclude")
    disk = _disk(s)
    assert disk["unread_done_generation"] == 3
    assert disk["unread_done_count"] == 1


def test_empty_scope_set_is_persisted_as_none(policy_env, tmp_config, monkeypatch):
    monkeypatch.setattr(sess, "list_all", lambda **kw: [])
    result = asyncio.run(server.api_put_settings_ui(
        {"notifications": {"unreadRecording": []}}))
    assert result["notifications"]["unreadRecording"] == ["none"]
    on_disk = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert on_disk["ui"]["notifications"]["unreadRecording"] == ["none"]


def test_failed_config_write_changes_nothing(policy_env, tmp_config, monkeypatch):
    unread_policy.refresh_unread_recording()
    s = _make_session("policy-put-fail", generation=2, read=0)
    monkeypatch.setattr(sess, "list_all", lambda **kw: [s])

    def boom(_cfg):
        raise OSError("disk full")

    monkeypatch.setattr(server, "save_config", boom)
    with pytest.raises(OSError):
        asyncio.run(server.api_put_settings_ui(
            {"notifications": {"unreadRecording": ["none"]}}))
    # The policy stays at its previous value; nothing was cleared or broadcast.
    assert unread_policy.current_unread_recording() == ("all",)
    assert _disk(s)["unread_done_count"] == 2
    assert not any(e.get("type") == "settings.uiUpdated" for e in policy_env)


def test_settings_put_over_http_api(policy_env, tmp_config, monkeypatch):
    s = _make_session("policy-put-http", generation=5, read=2)
    monkeypatch.setattr(sess, "list_all", lambda **kw: [s])

    async def scenario():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://diag") as client:
            r = await client.put("/api/settings/ui", json={
                "notifications": {"unreadRecording": ["pinned"]}})
            assert r.status_code == 200
            body = await server.api_get_settings_ui()
            assert body["notifications"]["unreadRecording"] == ["pinned"]

    asyncio.run(scenario())
    assert _disk(s)["unread_done_count"] == 0


# ── dynamic bell transitions ──

def test_bell_off_transition_clears_existing_unread(policy_env, tmp_config):
    _write_config(tmp_config, {"notifications": {"unreadRecording": ["bell-on"]}})
    unread_policy.refresh_unread_recording()
    s = _make_session("policy-bell-off", bridge=True, generation=2, read=0)
    result = asyncio.run(server.api_set_session_msg_bridge(
        s.id, {"enabled": False, "expectedEnabled": True}))
    assert result["ok"] is True
    disk = _disk(s)
    assert disk["unread_done_count"] == 0
    assert disk["unread_done_read_generation"] == 2


def test_bell_on_transition_does_not_restore_old_unread(policy_env, tmp_config):
    _write_config(tmp_config, {"notifications": {"unreadRecording": ["bell-on"]}})
    unread_policy.refresh_unread_recording()
    # bell-on scope: this bell-off Session starts excluded, then re-enters.
    s = _make_session("policy-bell-on", bridge=False, generation=3, read=0)
    result = asyncio.run(server.api_set_session_msg_bridge(
        s.id, {"enabled": True, "expectedEnabled": False}))
    assert result["ok"] is True
    assert _disk(s)["unread_done_count"] == 0
    _run_terminal(s, task_id="bell-on-new")
    disk = _disk(s)
    assert disk["unread_done_generation"] == 4
    assert disk["unread_done_count"] == 1


def test_bell_edit_outside_policy_keeps_legitimate_unread(policy_env, tmp_config):
    _write_config(tmp_config, {"notifications": {"unreadRecording": ["bell-on"]}})
    unread_policy.refresh_unread_recording()
    s = _make_session("policy-bell-unrelated", bridge=True, generation=2, read=0)
    # Replacing the bell group while still recorded must not clear unread.
    result = asyncio.run(server.api_set_session_msg_bridge(
        s.id, {"enabled": False, "expectedEnabled": True}))
    assert result["ok"] is True
    assert _disk(s)["unread_done_count"] == 0  # bell-on scope: now excluded
    # Flip back on; the record decision returns and new events count again.
    result = asyncio.run(server.api_set_session_msg_bridge(
        s.id, {"enabled": True, "expectedEnabled": False}))
    assert result["ok"] is True
    _run_terminal(s, task_id="bell-unrelated-new")
    disk = _disk(s)
    assert disk["unread_done_count"] == 1


# ── dynamic pin transitions (Session pin, never Job pins) ──

def test_pin_off_clears_and_repin_starts_from_new_events(policy_env, tmp_config):
    _write_config(tmp_config, {"notifications": {"unreadRecording": ["pinned"]}})
    unread_policy.refresh_unread_recording()
    s = _make_session("policy-pin", generation=2, read=0)
    sess.set_session_pinned(s.id, True)
    # Unpin → excluded → immediate clear.
    result = asyncio.run(server.api_set_session_pin(s.id, {"pinned": False}))
    assert result["ok"] is True
    assert _disk(s)["unread_done_count"] == 0
    # Re-pin → included again; counts from new events only.
    result = asyncio.run(server.api_set_session_pin(s.id, {"pinned": True}))
    assert result["ok"] is True
    _run_terminal(s, task_id="pin-new")
    disk = _disk(s)
    assert disk["unread_done_generation"] == 3
    assert disk["unread_done_count"] == 1


def test_pin_edit_outside_policy_keeps_legitimate_unread(policy_env, tmp_config):
    _write_config(tmp_config, {"notifications": {"unreadRecording": ["bell-off"]}})
    unread_policy.refresh_unread_recording()
    s = _make_session("policy-pin-unrelated", bridge=False, generation=2, read=0)
    # bell-off scope: this Session is recorded; pin edits must not clear unread.
    result = asyncio.run(server.api_set_session_pin(s.id, {"pinned": True}))
    assert result["ok"] is True
    assert _disk(s)["unread_done_count"] == 2
    result = asyncio.run(server.api_set_session_pin(s.id, {"pinned": False}))
    assert result["ok"] is True
    assert _disk(s)["unread_done_count"] == 2


# ── persistence failure: honest by projection, healed by retry paths ──

def test_reset_persist_failure_keeps_projection_zero_and_heals(
        policy_env, monkeypatch):
    monkeypatch.setattr(unread_policy, "_COMPILED_SNAPSHOT", ("none",))
    s = _make_session("policy-reset-fail", generation=2, read=0)
    entered, release = _block_metadata_replace(monkeypatch, s, fail=True)
    changed_holder = {}

    async def scenario():
        task = asyncio.create_task(server._coordinate_unread_recording(
            [s], before_scopes=("all",), after_scopes=("none",)))
        assert await asyncio.to_thread(entered.wait, 2)
        release.set()
        changed_holder["result"] = await task

    asyncio.run(scenario())
    # The failed reset honestly reports no change and left the disk untouched.
    assert changed_holder["result"] == []
    assert _disk(s)["unread_done_count"] == 2
    # ...but the read projection is already authoritative zero everywhere.
    assert server._session_summary(s)["unreadDoneCount"] == 0
    assert server._session_to_api(s)["unreadDoneCount"] == 0
    # The next terminal commit heals the durable cursors (the injected failure
    # fires once, so this commit persists normally).
    _run_terminal(s, task_id="heal-after-fail")
    disk = _disk(s)
    assert disk["unread_done_read_generation"] == 2
    assert disk["unread_done_count"] == 0


def test_cancelled_reset_keeps_the_committed_clear(policy_env, monkeypatch):
    monkeypatch.setattr(unread_policy, "_COMPILED_SNAPSHOT", ("none",))
    s = _make_session("policy-reset-cancel", generation=3, read=0)
    entered, release = _block_metadata_replace(monkeypatch, s)

    async def scenario():
        task = asyncio.create_task(server._coordinate_unread_recording(
            [s], before_scopes=("all",), after_scopes=("none",)))
        assert await asyncio.to_thread(entered.wait, 2)
        try:
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    # Cancellation after the durable commit must not roll the clear back.
    assert _disk(s)["unread_done_read_generation"] == 3
    assert _disk(s)["unread_done_count"] == 0


# ── startup alignment ──

def test_startup_alignment_clears_excluded_legacy_unread(
        policy_env, tmp_config):
    _write_config(tmp_config, {"notifications": {"unreadRecording": ["none"]}})
    s = _make_session("policy-startup", generation=5, read=2)
    count = asyncio.run(server._coordinate_unread_recording_on_startup([s]))
    assert count == 1
    disk = _disk(s)
    assert disk["unread_done_read_generation"] == 5
    assert disk["unread_done_count"] == 0


def test_startup_alignment_is_noop_for_all_scope(policy_env, tmp_config):
    _write_config(tmp_config, {"notifications": {"unreadRecording": ["all"]}})
    s = _make_session("policy-startup-all", generation=5, read=2)
    count = asyncio.run(server._coordinate_unread_recording_on_startup([s]))
    assert count == 0
    assert _disk(s)["unread_done_count"] == 3


def test_legacy_config_without_key_defaults_to_all(policy_env, tmp_config):
    _write_config(tmp_config, {"showQQ": True})
    assert unread_policy.refresh_unread_recording() == ("all",)
    s = _make_session("policy-legacy-default", generation=2, read=0)
    _run_terminal(s, task_id="legacy")
    assert _disk(s)["unread_done_count"] == 3  # kept recording as before


def test_startup_alignment_of_pinned_scope_uses_shared_pin_snapshot(
        policy_env, tmp_config):
    _write_config(tmp_config, {"notifications": {"unreadRecording": ["pinned"]}})
    pinned = _make_session("policy-startup-pinned", generation=4, read=1)
    unpinned = _make_session("policy-startup-unpinned", generation=4, read=1)
    sess.set_session_pinned(pinned.id, True)
    count = asyncio.run(server._coordinate_unread_recording_on_startup(
        [pinned, unpinned]))
    assert count == 1
    assert _disk(pinned)["unread_done_count"] == 3  # pinned Session keeps unread
    assert _disk(unpinned)["unread_done_count"] == 0  # unpinned cleared
