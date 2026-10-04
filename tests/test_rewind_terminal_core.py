"""Real PTY, fake local CBC menu/fork: no provider, account or user data."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from packages.core.rewind import driver, hybrid, pty_session
from packages.core.terminal.contracts import ProcessStatus

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="real ConPTY")

MENU_CHILD = r'''
import ctypes, msvcrt, sys, time
from pathlib import Path
ctypes.windll.kernel32.SetConsoleMode(ctypes.windll.kernel32.GetStdHandle(-11), 7)
def show(value):
    print("\x1b[2J\x1b[H" + value, flush=True)
show("CodeBuddy Code\n> ")
esc = 0
stage = "ready"
while True:
    key = msvcrt.getwch()
    if key in ("\x00", "\xe0"):
        msvcrt.getwch()
        continue
    if stage == "ready" and key == "\x1b":
        esc += 1
        if esc >= 2:
            stage = "menu"
            show("Restore and fork the conversation\n❯ restore the tracked fixture checkpoint")
    elif stage == "menu" and key == "\r":
        stage = "confirm"
        show("Confirm checkpoint\n❯ 1. Restore code and conversation\n  2. Restore conversation\n  3. Restore code\n  4. Never Mind")
    elif stage == "confirm" and key == "\r":
        Path(sys.argv[1]).write_text("restored", encoding="utf-8")
        show("> restored conversation\nbypass permissions for agents")
        time.sleep(300)
'''


def prepare(tmp_path, monkeypatch):
    pytest.importorskip("pyte")
    script = tmp_path / "menu_child.py"
    script.write_text(MENU_CHILD, encoding="utf-8")
    watched = tmp_path / "tracked.txt"
    watched.write_text("modified", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    fork_path = tmp_path / ".codebuddy" / "projects" / "fixture" / "fork-fixture.jsonl"
    fork_path.parent.mkdir(parents=True)
    rows = [
        {"type": "system", "id": "sys"},
        {"type": "message", "role": "user", "id": "u1",
         "content": [{"text": "restore the tracked fixture checkpoint"}]},
        {"type": "message", "role": "assistant", "id": "a1", "content": [{"text": "done"}]},
    ]
    fork_path.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
    monkeypatch.setattr(driver, "_build_resume_argv", lambda *_: [
        getattr(sys, "_base_executable", sys.executable), "-X", "utf8", str(script), str(watched)])
    monkeypatch.setattr(hybrid, "fork_session", lambda *_args, **_kwargs: driver.ForkResult(
        parent_session_id="parent-fixture", new_session_id="fork-fixture",
        transcript_path=str(fork_path), original_unchanged=True, exit_code=0))
    monkeypatch.setattr(hybrid.cbc_sessions, "parse_history", lambda *_: [])
    monkeypatch.setattr(hybrid.cbc_sessions, "get_raw_usage", lambda *_: [])
    owners = []
    original_session = driver._PtySession
    def create(*args, **kwargs):
        owner = original_session(*args, **kwargs)
        owners.append(owner)
        return owner
    monkeypatch.setattr(driver, "_PtySession", create)
    return watched, fork_path, owners


def run_rewind(tmp_path, watched):
    return hybrid.run_hybrid_rewind(
        "parent-fixture", tmp_path,
        driver.AnchorSpec("restore the tracked fixture checkpoint", message_id="u1"),
        expected_files={watched: "restored"}, timeout=5.0)


def cleanup(owners):
    for owner in owners:
        for _ in range(4):
            if owner.close().get("ok") is True:
                break
            time.sleep(0.1)
        assert owner.backend.closed
        assert owner.owner_id not in pty_session.retained_owner_ids()


def test_real_pty_fake_fork_restores_then_cleans_before_truncation(tmp_path, monkeypatch):
    watched, fork_path, owners = prepare(tmp_path, monkeypatch)
    try:
        result = run_rewind(tmp_path, watched)
        assert result.success, result.error
        assert result.file_rewind.cleanup["ok"] is True
        assert result.file_rewind.cleanup["owner_retained"] is False
        assert result.file_rewind.restore_verification == "expected-files"
        assert watched.read_text(encoding="utf-8") == "restored"
        assert len(fork_path.read_text(encoding="utf-8").splitlines()) == 1
        assert owners[0].backend.probe(owners[0].backend.pid).status is ProcessStatus.DEAD
        assert owners[0].runtime.consumer_failed is False
        assert owners[0].runtime.log.total_bytes > 0
    finally:
        cleanup(owners)


def test_failed_cleanup_retains_same_owner_and_does_not_truncate(tmp_path, monkeypatch):
    watched, fork_path, owners = prepare(tmp_path, monkeypatch)
    before = fork_path.read_bytes()
    original_session = driver._PtySession
    restore_methods = []
    def fail_tree(*args, **kwargs):
        owner = original_session(*args, **kwargs)
        guard = owner.backend.guard
        real = guard.terminate_tree
        restore_methods.append((guard, real))
        def refuse(*_args, **_kwargs):
            raise OSError("test-only guard failure")
        monkeypatch.setattr(guard, "terminate_tree", refuse)
        return owner
    monkeypatch.setattr(driver, "_PtySession", fail_tree)
    try:
        result = run_rewind(tmp_path, watched)
        assert not result.success
        assert result.file_rewind.restore_verification == "expected-files"
        report = result.file_rewind.cleanup
        assert report["ok"] is False and report["owner_retained"] is True
        assert report["owner_id"] == owners[0].owner_id
        assert not owners[0].backend.closed
        assert report["owner_id"] in pty_session.retained_owner_ids()
        assert result.truncation is None
        assert fork_path.read_bytes() == before
        assert not any(x["stage"] == "completed" for x in result.stage_events)
        for guard, real in restore_methods:
            monkeypatch.setattr(guard, "terminate_tree", real)
        retry = pty_session.retry_cleanup(report["owner_id"])
        assert retry["ok"] is True, retry
        assert retry["owner_id"] == report["owner_id"]
        assert report["owner_id"] not in pty_session.retained_owner_ids()
    finally:
        for guard, real in restore_methods:
            monkeypatch.setattr(guard, "terminate_tree", real)
        cleanup(owners)
