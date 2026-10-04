"""Deterministic ownership gates supplement, not replace, the real PTY tests."""
from types import SimpleNamespace

import pytest

from packages.core.rewind import automation, driver, pty_session
from packages.core.terminal.spawn_win import SpawnDenied


def test_admission_precedes_any_spawn(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pty_session, "MAX_REWIND_OWNERS", 0)
    monkeypatch.setattr(pty_session.ConPtyBackend, "spawn", lambda *_a, **_k: calls.append(1))
    with pytest.raises(RuntimeError, match="capacity exhausted"):
        pty_session.PtySession(["never-spawn"], tmp_path)
    assert calls == []


def test_atomic_spawn_failure_keeps_its_original_cleanup_owner(tmp_path, monkeypatch):
    class Attempt:
        def __init__(self):
            self.calls = 0
        def cleanup(self):
            self.calls += 1
            return {"release": {"closed": self.calls >= 2},
                    "guard_terminate": {"remaining": []}}
    attempt = Attempt()
    denied = SpawnDenied("assign", {}, cleanup={"release": {"closed": False}}, attempt=attempt)
    monkeypatch.setattr(pty_session, "PyteScreenObserver", lambda **_k: object())
    def spawn(*_a, **_k):
        raise denied
    monkeypatch.setattr(pty_session.ConPtyBackend, "spawn", spawn)
    try:
        with pytest.raises(pty_session.RewindStartupError) as error:
            pty_session.PtySession(["test-only"], tmp_path)
        owner_id = error.value.owner_id
        assert error.value.cleanup["ok"] is False
        assert owner_id in pty_session.retained_owner_ids()
        assert pty_session.retry_cleanup(owner_id)["ok"] is True
        assert attempt.calls == 2
        assert owner_id not in pty_session.retained_owner_ids()
    finally:
        for owner_id in pty_session.retained_owner_ids():
            pty_session.retry_cleanup(owner_id)


def test_menu_send_does_not_hide_partial_input():
    session = automation.AutomationSession(SimpleNamespace(send_text=lambda value: 1))
    with pytest.raises(RuntimeError, match="partially accepted"):
        session.send("\x1b[A")


@pytest.mark.parametrize("confirmed", [False, True])
def test_completed_is_emitted_only_after_cleanup_is_confirmed(tmp_path, monkeypatch, confirmed):
    events = []
    owner = SimpleNamespace(context=object(), close=lambda: {
        "ok": confirmed, "owner_retained": not confirmed, "owner_id": "test-owner"})
    monkeypatch.setattr(driver, "_PtySession", lambda *_a, **_k: owner)
    monkeypatch.setattr(driver, "_build_resume_argv", lambda *_a: ["test-only"])
    monkeypatch.setattr(driver.RewindAutomation, "rewind", lambda *_a, **_k: driver.RewindResult(
        success=True, stage="completed", restore_verification="expected-files"))
    result = driver.RewindDriver(on_stage=lambda stage, info: events.append(stage)).rewind(
        "test-fixture", tmp_path, "restore fixture checkpoint")
    assert result.success is confirmed
    assert ("completed" in events) is confirmed
    assert result.stage == ("completed" if confirmed else "failed")
